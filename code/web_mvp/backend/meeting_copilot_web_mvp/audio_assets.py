from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import time
import wave
from typing import Any, Callable

from meeting_copilot_web_mvp.repository import SESSION_ID_PATTERN


SAMPLE_RATE_HZ = 16_000
CHANNEL_COUNT = 1
PCM_SAMPLE_WIDTH_BYTES = 2
DEFAULT_CHUNK_DURATION_SECONDS = 5.0
_MANIFEST_VERSION = 1
_HASH_BUFFER_BYTES = 1024 * 1024
_CHUNK_NAME_PATTERN = re.compile(r"^chunk-(\d{8})\.pcm$")


def audio_chunk_journal_sha256(chunks: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "chunk_seq": int(chunk.get("chunk_seq", index)),
            "name": str(chunk.get("name") or Path(str(chunk.get("relative_path") or "")).name),
            "sample_count": int(chunk["sample_count"]),
            "file_size_bytes": int(chunk["file_size_bytes"]),
            "sha256": str(chunk["sha256"]),
        }
        for index, chunk in enumerate(chunks)
    ]
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RealtimeWavAssetWriter:
    def __init__(
        self,
        *,
        data_dir: str | Path,
        session_id: str,
        source_type: str,
        sample_rate_hz: int = SAMPLE_RATE_HZ,
        chunk_duration_seconds: float = DEFAULT_CHUNK_DURATION_SECONDS,
        on_chunk_committed: Callable[[dict[str, Any]], None] | None = None,
        authorize_chunk_commit: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"unsafe session_id: {session_id}")
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if chunk_duration_seconds <= 0:
            raise ValueError("chunk_duration_seconds must be positive")

        self._data_dir = Path(data_dir)
        self._session_id = session_id
        self._source_type = source_type
        self._sample_rate_hz = sample_rate_hz
        self._chunk_duration_seconds = chunk_duration_seconds
        self._chunk_sample_count = max(1, round(sample_rate_hz * chunk_duration_seconds))
        self._chunk_size_bytes = self._chunk_sample_count * PCM_SAMPLE_WIDTH_BYTES
        self._on_chunk_committed = on_chunk_committed
        self._authorize_chunk_commit = authorize_chunk_commit
        self._closed = False
        self._assembled = False
        self._buffer = bytearray()
        self._relative_path = Path("audio_assets") / session_id / "audio.wav"
        self._path = self._data_dir / self._relative_path
        self._session_dir = self._path.parent
        self._chunks_dir = self._session_dir / "chunks"
        self._manifest_path = self._session_dir / "audio.manifest.json"
        self._assembly_temp_path = self._session_dir / "audio.wav.tmp"
        self._legacy_temp_path = self._session_dir / "audio.wav.inprogress"
        self._manifest_temp_path = self._session_dir / "audio.manifest.json.tmp"
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._chunks_dir.mkdir(parents=True, exist_ok=True)
        self._remove_incomplete_files()

        manifest = self._load_manifest()
        self._base_sample_count = self._load_base_sample_count(manifest)
        self._chunks = self._recover_chunks(manifest)
        self._next_chunk_index = len(self._chunks)
        self._sample_count = self._base_sample_count + sum(
            int(chunk["sample_count"]) for chunk in self._chunks
        )
        self._write_manifest()
        for chunk_index, chunk in enumerate(self._chunks):
            self._notify_chunk_committed(chunk, chunk_index=chunk_index)

    def write_float32_pcm(self, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("audio asset writer is already closed")
        usable = payload[: len(payload) - (len(payload) % 4)]
        if not usable:
            return
        pcm16 = bytearray()
        for (sample,) in struct.iter_unpack("<f", usable):
            if sample > 1.0:
                sample = 1.0
            elif sample < -1.0:
                sample = -1.0
            pcm16.extend(struct.pack("<h", int(sample * 32767)))
        self._buffer.extend(pcm16)
        while len(self._buffer) >= self._chunk_size_bytes:
            chunk = bytes(self._buffer[: self._chunk_size_bytes])
            del self._buffer[: self._chunk_size_bytes]
            self._commit_chunk(chunk)

    def seal(self) -> dict[str, Any]:
        """Flush the recoverable journal without assembling the final WAV."""

        if not self._closed:
            if self._buffer:
                self._commit_chunk(bytes(self._buffer))
                self._buffer.clear()
            self._write_manifest()
            self._closed = True
        return {
            "saved": True,
            "assembled": False,
            "audio_asset_id": f"audio_{self._session_id}",
            "relative_path": str(self._relative_path),
            "format": "pcm_s16le_chunk_journal",
            "sample_rate_hz": self._sample_rate_hz,
            "channel_count": CHANNEL_COUNT,
            "sample_count": self._sample_count,
            "duration_ms": round(self._sample_count / self._sample_rate_hz * 1_000),
            "file_size_bytes": self._sample_count * PCM_SAMPLE_WIDTH_BYTES,
            "sha256": None,
            "source_type": self._source_type,
            "chunk_count": len(self._chunks),
            "journal_sha256": audio_chunk_journal_sha256(self._chunks),
            "created_at_ms": int(time.time() * 1000),
        }

    def close(self) -> dict[str, Any]:
        """Compatibility path that seals and synchronously assembles the WAV."""

        self.seal()
        if not self._assembled:
            self._assemble_wav()
            self._assembled = True
        metadata = audio_metadata_for_file(
            data_dir=self._data_dir,
            session_id=self._session_id,
            relative_path=self._relative_path,
            source_type=self._source_type,
            sample_rate_hz=self._sample_rate_hz,
            sample_count=self._sample_count,
        )
        return {**metadata, "assembled": True, "chunk_count": len(self._chunks)}

    def discard(self) -> None:
        if self._closed:
            return
        self._buffer.clear()
        self._closed = True
        self._remove_incomplete_files()

    def _remove_incomplete_files(self) -> None:
        self._assembly_temp_path.unlink(missing_ok=True)
        self._legacy_temp_path.unlink(missing_ok=True)
        self._manifest_temp_path.unlink(missing_ok=True)
        for path in self._chunks_dir.glob("*.tmp"):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)

    def _load_manifest(self) -> dict[str, Any] | None:
        if not self._manifest_path.exists():
            return None
        try:
            with self._manifest_path.open("r", encoding="utf-8") as manifest_file:
                manifest = json.load(manifest_file)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("audio chunk manifest is unreadable") from exc
        if not isinstance(manifest, dict) or manifest.get("version") != _MANIFEST_VERSION:
            raise ValueError("unsupported audio chunk manifest")
        if (
            manifest.get("sample_rate_hz") != self._sample_rate_hz
            or manifest.get("channel_count") != CHANNEL_COUNT
            or manifest.get("sample_width_bytes") != PCM_SAMPLE_WIDTH_BYTES
        ):
            raise ValueError("existing realtime audio format does not match writer")
        if not isinstance(manifest.get("chunks", []), list):
            raise ValueError("audio chunk manifest has invalid chunks")
        return manifest

    def _load_base_sample_count(self, manifest: dict[str, Any] | None) -> int:
        if manifest is None:
            if not self._path.exists():
                return 0
            return self._validated_wav_frame_count(self._path)

        value = manifest.get("base_sample_count", 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("audio chunk manifest has invalid base_sample_count")
        if value:
            if not self._path.exists():
                raise ValueError("base audio file is missing")
            if self._validated_wav_frame_count(self._path) < value:
                raise ValueError("base audio file is shorter than its manifest")
        return value

    def _validated_wav_frame_count(self, path: Path) -> int:
        try:
            with wave.open(str(path), "rb") as existing:
                if (
                    existing.getnchannels() != CHANNEL_COUNT
                    or existing.getsampwidth() != PCM_SAMPLE_WIDTH_BYTES
                    or existing.getframerate() != self._sample_rate_hz
                    or existing.getcomptype() != "NONE"
                ):
                    raise ValueError("existing realtime audio format does not match writer")
                return existing.getnframes()
        except (EOFError, wave.Error) as exc:
            raise ValueError("existing realtime audio is not a valid PCM WAV file") from exc

    def _recover_chunks(self, manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
        chunk_paths: list[tuple[int, Path]] = []
        for path in self._chunks_dir.iterdir():
            match = _CHUNK_NAME_PATTERN.fullmatch(path.name)
            if match and path.is_file() and not path.is_symlink():
                chunk_paths.append((int(match.group(1)), path))
        chunk_paths.sort(key=lambda item: item[0])
        indexes = [index for index, _path in chunk_paths]
        if indexes != list(range(len(indexes))):
            raise ValueError("audio chunk journal is not contiguous")

        recovered: list[dict[str, Any]] = []
        for _index, path in chunk_paths:
            file_size_bytes, sha256 = _file_size_and_sha256(path)
            if file_size_bytes <= 0 or file_size_bytes % PCM_SAMPLE_WIDTH_BYTES:
                raise ValueError(f"invalid PCM16 audio chunk: {path.name}")
            recovered.append({
                "name": path.name,
                "sample_count": file_size_bytes // PCM_SAMPLE_WIDTH_BYTES,
                "file_size_bytes": file_size_bytes,
                "sha256": sha256,
            })

        declared = list((manifest or {}).get("chunks") or [])
        if len(declared) > len(recovered):
            raise ValueError("audio chunk declared by manifest is missing")
        for index, expected in enumerate(declared):
            actual = recovered[index]
            if not isinstance(expected, dict) or any(
                expected.get(key) != actual[key]
                for key in ("name", "sample_count", "file_size_bytes", "sha256")
            ):
                raise ValueError("audio chunk does not match its manifest")
        return recovered

    def _commit_chunk(self, pcm16: bytes) -> None:
        if not pcm16 or len(pcm16) % PCM_SAMPLE_WIDTH_BYTES:
            raise ValueError("PCM16 chunk must contain complete samples")
        name = f"chunk-{self._next_chunk_index:08d}.pcm"
        sample_count = len(pcm16) // PCM_SAMPLE_WIDTH_BYTES
        proposed_chunk = {
            "name": name,
            "session_id": self._session_id,
            "source_type": self._source_type,
            "sample_rate_hz": self._sample_rate_hz,
            "chunk_index": self._next_chunk_index,
            "sample_count": sample_count,
            "duration_ms": round(sample_count / self._sample_rate_hz * 1_000),
            "file_size_bytes": len(pcm16),
            "sha256": hashlib.sha256(pcm16).hexdigest(),
            "relative_path": str(
                Path("audio_assets")
                / self._session_id
                / "chunks"
                / name
            ),
        }
        if (
            self._authorize_chunk_commit is not None
            and not self._authorize_chunk_commit(dict(proposed_chunk))
        ):
            raise RuntimeError("capture lease fence rejected audio chunk commit")
        path = self._chunks_dir / name
        temp_path = self._chunks_dir / f"{name}.tmp"
        with temp_path.open("wb") as chunk_file:
            chunk_file.write(pcm16)
            chunk_file.flush()
            os.fsync(chunk_file.fileno())
        os.replace(temp_path, path)
        _fsync_directory(self._chunks_dir)

        committed_chunk = {
            "name": name,
            "sample_count": sample_count,
            "file_size_bytes": len(pcm16),
            "sha256": proposed_chunk["sha256"],
        }
        self._chunks.append(committed_chunk)
        self._next_chunk_index += 1
        self._sample_count += sample_count
        self._write_manifest()
        self._notify_chunk_committed(
            committed_chunk,
            chunk_index=self._next_chunk_index - 1,
        )

    def _notify_chunk_committed(
        self,
        chunk: dict[str, Any],
        *,
        chunk_index: int,
    ) -> None:
        if self._on_chunk_committed is None:
            return
        self._on_chunk_committed({
            **chunk,
            "session_id": self._session_id,
            "source_type": self._source_type,
            "sample_rate_hz": self._sample_rate_hz,
            "chunk_index": chunk_index,
            "duration_ms": round(
                int(chunk["sample_count"]) / self._sample_rate_hz * 1_000
            ),
            "relative_path": str(
                Path("audio_assets")
                / self._session_id
                / "chunks"
                / str(chunk["name"])
            ),
        })

    def _write_manifest(self) -> None:
        manifest = {
            "version": _MANIFEST_VERSION,
            "format": "pcm_s16le_chunk_journal",
            "sample_rate_hz": self._sample_rate_hz,
            "channel_count": CHANNEL_COUNT,
            "sample_width_bytes": PCM_SAMPLE_WIDTH_BYTES,
            "chunk_duration_ms": round(self._chunk_duration_seconds * 1000),
            "base_sample_count": self._base_sample_count,
            "chunks": self._chunks,
        }
        encoded = json.dumps(
            manifest,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        with self._manifest_temp_path.open("wb") as manifest_file:
            manifest_file.write(encoded)
            manifest_file.flush()
            os.fsync(manifest_file.fileno())
        os.replace(self._manifest_temp_path, self._manifest_path)
        _fsync_directory(self._session_dir)

    def _assemble_wav(self) -> None:
        try:
            with wave.open(str(self._assembly_temp_path), "wb") as assembled:
                assembled.setnchannels(CHANNEL_COUNT)
                assembled.setsampwidth(PCM_SAMPLE_WIDTH_BYTES)
                assembled.setframerate(self._sample_rate_hz)
                if self._base_sample_count:
                    self._copy_base_audio(assembled)
                for chunk in self._chunks:
                    path = self._chunks_dir / str(chunk["name"])
                    with path.open("rb") as chunk_file:
                        while data := chunk_file.read(_HASH_BUFFER_BYTES):
                            assembled.writeframesraw(data)
            with self._assembly_temp_path.open("rb") as assembled_file:
                os.fsync(assembled_file.fileno())
            os.replace(self._assembly_temp_path, self._path)
            _fsync_directory(self._session_dir)
        except Exception:
            self._assembly_temp_path.unlink(missing_ok=True)
            raise

    def _copy_base_audio(self, assembled: wave.Wave_write) -> None:
        remaining = self._base_sample_count
        with wave.open(str(self._path), "rb") as existing:
            while remaining:
                frames = existing.readframes(min(remaining, self._sample_rate_hz))
                if not frames:
                    raise ValueError("base audio ended before its declared sample count")
                frame_count = len(frames) // PCM_SAMPLE_WIDTH_BYTES
                assembled.writeframesraw(frames)
                remaining -= frame_count


def assemble_realtime_wav_asset(
    *,
    data_dir: str | Path,
    session_id: str,
    source_type: str,
    sample_rate_hz: int = SAMPLE_RATE_HZ,
    expected_chunk_count: int | None = None,
    expected_sample_count: int | None = None,
    expected_journal_sha256: str | None = None,
) -> dict[str, Any]:
    """Assemble a sealed/recovered journal in a background-worker friendly call."""

    writer = RealtimeWavAssetWriter(
        data_dir=data_dir,
        session_id=session_id,
        source_type=source_type,
        sample_rate_hz=sample_rate_hz,
    )
    sealed = writer.seal()
    expected = {
        "chunk_count": expected_chunk_count,
        "sample_count": expected_sample_count,
        "journal_sha256": expected_journal_sha256,
    }
    mismatches = {
        field: (expected_value, sealed[field])
        for field, expected_value in expected.items()
        if expected_value is not None and expected_value != sealed[field]
    }
    if mismatches:
        raise ValueError(f"recording journal changed before export: {mismatches}")
    return writer.close()


def inspect_realtime_audio_journal(
    *,
    data_dir: str | Path,
    session_id: str,
    sample_rate_hz: int,
) -> dict[str, Any]:
    """Validate and describe a durable PCM journal without changing it."""

    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"unsafe session_id: {session_id}")
    sample_rate_hz = int(sample_rate_hz)
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")

    session_dir = safe_audio_path(data_dir, Path("audio_assets") / session_id)
    if session_dir.is_symlink():
        raise ValueError("audio journal session directory must not be a symlink")
    chunks_dir = session_dir / "chunks"
    manifest_path = session_dir / "audio.manifest.json"
    if not chunks_dir.is_dir() or chunks_dir.is_symlink():
        raise ValueError("audio chunk journal directory is missing or unsafe")

    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("audio chunk manifest path is unsafe")
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("audio chunk manifest is unreadable") from exc
        if not isinstance(loaded, dict) or loaded.get("version") != _MANIFEST_VERSION:
            raise ValueError("unsupported audio chunk manifest")
        if (
            int(loaded.get("sample_rate_hz") or 0) != sample_rate_hz
            or loaded.get("channel_count") != CHANNEL_COUNT
            or loaded.get("sample_width_bytes") != PCM_SAMPLE_WIDTH_BYTES
        ):
            raise ValueError("audio chunk manifest format does not match recording")
        if not isinstance(loaded.get("chunks", []), list):
            raise ValueError("audio chunk manifest has invalid chunks")
        manifest = loaded

    indexed_paths: list[tuple[int, Path]] = []
    for path in chunks_dir.iterdir():
        match = _CHUNK_NAME_PATTERN.fullmatch(path.name)
        if match is None:
            if path.name.endswith(".tmp"):
                continue
            raise ValueError("audio chunk journal contains an unexpected file")
        if not path.is_file() or path.is_symlink():
            raise ValueError("audio chunk journal contains an unsafe chunk")
        indexed_paths.append((int(match.group(1)), path))
    indexed_paths.sort(key=lambda item: item[0])
    if [index for index, _path in indexed_paths] != list(range(len(indexed_paths))):
        raise ValueError("audio chunk journal is not contiguous")

    chunks: list[dict[str, Any]] = []
    for chunk_seq, path in indexed_paths:
        file_size_bytes, sha256 = _file_size_and_sha256(path)
        if file_size_bytes <= 0 or file_size_bytes % PCM_SAMPLE_WIDTH_BYTES:
            raise ValueError(f"invalid PCM16 audio chunk: {path.name}")
        sample_count = file_size_bytes // PCM_SAMPLE_WIDTH_BYTES
        chunks.append({
            "chunk_seq": chunk_seq,
            "name": path.name,
            "relative_path": str(
                Path("audio_assets") / session_id / "chunks" / path.name
            ),
            "sample_rate_hz": sample_rate_hz,
            "sample_count": sample_count,
            "duration_ms": round(sample_count / sample_rate_hz * 1_000),
            "file_size_bytes": file_size_bytes,
            "sha256": sha256,
        })

    declared = list((manifest or {}).get("chunks") or [])
    if len(declared) > len(chunks):
        raise ValueError("audio chunk declared by manifest is missing")
    for index, expected in enumerate(declared):
        actual = chunks[index]
        if not isinstance(expected, dict) or any(
            expected.get(key) != actual[key]
            for key in ("name", "sample_count", "file_size_bytes", "sha256")
        ):
            raise ValueError("audio chunk does not match its manifest")

    return {
        "session_id": session_id,
        "sample_rate_hz": sample_rate_hz,
        "chunk_count": len(chunks),
        "sample_count": sum(int(chunk["sample_count"]) for chunk in chunks),
        "duration_ms": sum(int(chunk["duration_ms"]) for chunk in chunks),
        "file_size_bytes": sum(int(chunk["file_size_bytes"]) for chunk in chunks),
        "journal_sha256": audio_chunk_journal_sha256(chunks),
        "chunks": chunks,
    }


def persist_uploaded_audio_asset(
    *,
    data_dir: str | Path,
    session_id: str,
    source_type: str,
    filename: str,
    payload: bytes,
) -> dict[str, Any]:
    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"unsafe session_id: {session_id}")
    suffix = Path(filename or "").suffix.lower() or ".audio"
    if len(suffix) > 12 or "/" in suffix or "\\" in suffix:
        suffix = ".audio"
    relative_path = Path("audio_assets") / session_id / f"source{suffix}"
    path = Path(data_dir) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return audio_metadata_for_file(
        data_dir=data_dir,
        session_id=session_id,
        relative_path=relative_path,
        source_type=source_type,
        sample_rate_hz=None,
        sample_count=None,
        original_filename=Path(filename or "recording").name,
    )


def persist_uploaded_audio_asset_from_path(
    *,
    data_dir: str | Path,
    session_id: str,
    source_type: str,
    filename: str,
    source_path: str | Path,
) -> dict[str, Any]:
    """Atomically copy an uploaded recording without loading it into memory."""

    if not SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"unsafe session_id: {session_id}")
    suffix = Path(filename or "").suffix.lower() or ".audio"
    if len(suffix) > 12 or "/" in suffix or "\\" in suffix:
        suffix = ".audio"
    relative_path = Path("audio_assets") / session_id / f"source{suffix}"
    destination = Path(data_dir) / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(f"{destination.suffix}.tmp")
    try:
        with Path(source_path).open("rb") as source, temp_path.open("wb") as target:
            shutil.copyfileobj(source, target, length=_HASH_BUFFER_BYTES)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temp_path, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return audio_metadata_for_file(
        data_dir=data_dir,
        session_id=session_id,
        relative_path=relative_path,
        source_type=source_type,
        sample_rate_hz=None,
        sample_count=None,
        original_filename=Path(filename or "recording").name,
    )


def audio_metadata_for_file(
    *,
    data_dir: str | Path,
    session_id: str,
    relative_path: str | Path,
    source_type: str,
    sample_rate_hz: int | None,
    sample_count: int | None,
    original_filename: str | None = None,
) -> dict[str, Any]:
    path = safe_audio_path(data_dir, relative_path)
    file_size_bytes, sha256 = _file_size_and_sha256(path)
    duration_ms = (
        int((sample_count / sample_rate_hz) * 1000)
        if sample_rate_hz and sample_count is not None
        else 0
    )
    return {
        "saved": True,
        "audio_asset_id": f"audio_{session_id}",
        "relative_path": str(Path(relative_path)),
        "format": path.suffix.lower().lstrip(".") or "audio",
        "sample_rate_hz": sample_rate_hz,
        "channel_count": CHANNEL_COUNT if sample_rate_hz else None,
        "duration_ms": duration_ms,
        "file_size_bytes": file_size_bytes,
        "sha256": sha256,
        "source_type": source_type,
        "created_at_ms": int(time.time() * 1000),
        "retention_policy": "local_until_session_deleted",
        **({"original_filename": original_filename} if original_filename else {}),
    }


def safe_audio_path(data_dir: str | Path, relative_path: str | Path) -> Path:
    root = Path(data_dir).resolve()
    raw = Path(relative_path)
    if raw.is_absolute():
        raise ValueError("audio relative_path must not be absolute")
    path = (root / raw).resolve()
    if root != path and root not in path.parents:
        raise ValueError("audio path escapes data_dir")
    return path


def delete_audio_asset(data_dir: str | Path | None, audio: dict[str, Any] | None) -> str:
    if not audio or not audio.get("relative_path"):
        return "not_present"
    if data_dir is None:
        return "not_tracked_by_live_session_repo"
    session_dir = _controlled_session_audio_dir(data_dir, str(audio["relative_path"]))
    if not session_dir.exists():
        return "already_missing"
    if session_dir.is_symlink() or not session_dir.is_dir():
        raise ValueError("controlled session audio path is not a directory")
    shutil.rmtree(session_dir)
    return "deleted"


def _controlled_session_audio_dir(data_dir: str | Path, relative_path: str) -> Path:
    if "\\" in relative_path:
        raise ValueError("audio cleanup path must use POSIX separators")
    raw = PurePosixPath(relative_path)
    parts = raw.parts
    if (
        raw.is_absolute()
        or ".." in parts
        or len(parts) < 3
        or parts[0] != "audio_assets"
        or not SESSION_ID_PATTERN.fullmatch(parts[1])
    ):
        raise ValueError("audio cleanup path is not owned by a controlled session")

    root = Path(data_dir).resolve()
    unresolved_session_dir = root / "audio_assets" / parts[1]
    if unresolved_session_dir.is_symlink():
        raise ValueError("controlled session audio directory must not be a symlink")
    session_dir = unresolved_session_dir.resolve()
    target = safe_audio_path(root, Path(*parts))
    if root not in session_dir.parents or (
        target != session_dir and session_dir not in target.parents
    ):
        raise ValueError("audio cleanup path escapes its controlled session")
    return session_dir


def _file_size_and_sha256(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    file_size_bytes = 0
    with path.open("rb") as source:
        while data := source.read(_HASH_BUFFER_BYTES):
            file_size_bytes += len(data)
            digest.update(data)
    return file_size_bytes, digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

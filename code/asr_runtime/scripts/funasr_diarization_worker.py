"""Offline-first JSONL diarization sidecar protocol.

The worker owns transport validation, sample continuity, bounded online speaker
clustering, and fail-closed model resolution. Provider inference is injected
through ``is_speech`` and ``embedding`` methods so protocol tests do not imply
that a real CAM++ runtime or model artifact is present.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from contextlib import contextmanager, redirect_stdout
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, TextIO


DIARIZATION_PROTOCOL = "funasr-diarization-jsonl.v1"
PROTOCOL_VERSION = 1
SAMPLE_RATE = 16_000
CHANNELS = 1
AUDIO_ENCODING = "pcm_f32le"
MAX_COMMAND_LINE_BYTES = 6 * 1024 * 1024
MAX_AUDIO_PAYLOAD_BYTES = 4 * 1024 * 1024
MAX_SESSION_SAMPLES = SAMPLE_RATE * 60 * 60
MAX_SESSION_WINDOWS = 12_000
MAX_CLUSTERS = 32
MAX_EMBEDDING_DIMENSION = 2_048
MIN_SPEECH_RMS = 0.002
DEFAULT_SIMILARITY_THRESHOLD = 0.35
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_AUDIO_FIELDS = ("pcm_base64", "audio_base64", "samples_base64")
_VAD_REQUIRED_FILES = ("config.yaml", "model.pt")
_CAMPLUS_REQUIRED_FILES = ("campplus_cn_common.bin", "config.yaml")


def enforce_offline_environment() -> None:
    """Force both supported model hubs into offline mode before provider imports."""

    os.environ["MODELSCOPE_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"


enforce_offline_environment()


class DiarizationProtocolError(ValueError):
    def __init__(self, code: str, *, session_id: str | None = None):
        super().__init__(code)
        self.code = code
        self.session_id = session_id


class DiarizationUnavailable(RuntimeError):
    def __init__(self, reason: str, *, detail: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@contextmanager
def _protocol_output_channel(stdout: TextIO, stderr: TextIO) -> Iterator[TextIO]:
    """Reserve the original stdout for JSONL and route all other output to stderr.

    ``redirect_stdout`` covers Python writers. Duplicating stdout before ``dup2``
    also isolates writes made directly to file descriptor 1 by native provider
    dependencies. String-backed streams used by unit tests take the Python-only
    fallback.
    """

    try:
        stdout_fd = stdout.fileno()
        stderr_fd = stderr.fileno()
    except (AttributeError, OSError, ValueError):
        with redirect_stdout(stderr):
            yield stdout
        return
    if stdout_fd == stderr_fd:
        with redirect_stdout(stderr):
            yield stdout
        return

    stdout.flush()
    stderr.flush()
    protocol_fd = os.dup(stdout_fd)
    redirected_fds = tuple(
        fd for fd in dict.fromkeys((1, stdout_fd)) if fd != stderr_fd
    )
    try:
        saved_fds = {fd: os.dup(fd) for fd in redirected_fds}
    except OSError:
        os.close(protocol_fd)
        with redirect_stdout(stderr):
            yield stdout
        return
    protocol_output = os.fdopen(
        protocol_fd,
        "w",
        buffering=1,
        encoding=getattr(stdout, "encoding", None) or "utf-8",
        errors=getattr(stdout, "errors", None) or "strict",
    )
    try:
        for fd in redirected_fds:
            os.dup2(stderr_fd, fd)
        with redirect_stdout(stderr):
            yield protocol_output
    finally:
        try:
            protocol_output.flush()
            stdout.flush()
        finally:
            for fd in reversed(redirected_fds):
                os.dup2(saved_fds[fd], fd)
                os.close(saved_fds[fd])
            protocol_output.close()


def _import_funasr_auto_model() -> Any:
    from funasr import AutoModel

    return AutoModel


def _import_numpy() -> Any:
    import numpy

    return numpy


class FunASRLocalDiarizationBackend:
    """CPU-only local VAD and CAM++ adapter for the JSONL worker."""

    backend_name = "funasr_local_vad_camplus"
    model_resolution = "absolute_local_verified_files"

    def __init__(
        self,
        *,
        vad_dir: Path,
        camplus_dir: Path,
        auto_model_factory: Any | None = None,
        numpy_module: Any | None = None,
        ncpu: int = 2,
    ) -> None:
        enforce_offline_environment()
        self.vad_dir = _validate_local_model_dir(
            vad_dir,
            name="vad_dir",
            required_files=_VAD_REQUIRED_FILES,
        )
        self.camplus_dir = _validate_local_model_dir(
            camplus_dir,
            name="camplus_dir",
            required_files=_CAMPLUS_REQUIRED_FILES,
        )
        try:
            # Imports may initialize logging and print dependency banners.
            with redirect_stdout(sys.stderr):
                if auto_model_factory is None:
                    auto_model_factory = _import_funasr_auto_model()
                if numpy_module is None:
                    numpy_module = _import_numpy()
        except Exception as exc:
            raise DiarizationUnavailable(
                "backend_dependency_missing",
                detail=type(exc).__name__,
            ) from exc
        self._np = numpy_module
        options = {
            "device": "cpu",
            "disable_update": True,
            "ncpu": max(1, int(ncpu)),
        }
        try:
            # FunASR writes version/progress text during initialization. Keep stdout
            # reserved for the worker's machine-readable JSONL protocol.
            with redirect_stdout(sys.stderr):
                self._vad_model = auto_model_factory(model=str(self.vad_dir), **options)
                self._embedding_model = auto_model_factory(
                    model=str(self.camplus_dir),
                    **options,
                )
        except Exception as exc:
            raise DiarizationUnavailable(
                "backend_initialization_failed",
                detail=type(exc).__name__,
            ) from exc

    def _array(self, samples: tuple[float, ...]) -> Any:
        return self._np.asarray(samples, dtype=self._np.float32)

    def is_speech(self, samples: tuple[float, ...], **_kwargs: Any) -> bool:
        if not samples:
            return False
        rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
        if not math.isfinite(rms) or rms < MIN_SPEECH_RMS:
            return False
        try:
            with redirect_stdout(sys.stderr):
                result = self._vad_model.generate(
                    input=self._array(samples),
                    disable_pbar=True,
                )
        except Exception as exc:
            raise DiarizationProtocolError("vad_inference_failed") from exc
        if (
            not isinstance(result, list)
            or not result
            or not isinstance(result[0], dict)
        ):
            raise DiarizationProtocolError("invalid_vad_result")
        intervals = result[0].get("value")
        if not isinstance(intervals, list):
            raise DiarizationProtocolError("invalid_vad_result")
        return any(
            isinstance(interval, (list, tuple))
            and len(interval) >= 2
            and isinstance(interval[0], (int, float))
            and isinstance(interval[1], (int, float))
            and float(interval[1]) > float(interval[0])
            for interval in intervals
        )

    def embedding(
        self, samples: tuple[float, ...], **_kwargs: Any
    ) -> tuple[float, ...]:
        try:
            with redirect_stdout(sys.stderr):
                result = self._embedding_model.generate(
                    input=self._array(samples),
                    disable_pbar=True,
                )
        except Exception as exc:
            raise DiarizationProtocolError("embedding_inference_failed") from exc
        if (
            not isinstance(result, list)
            or not result
            or not isinstance(result[0], dict)
        ):
            raise DiarizationProtocolError("invalid_embedding")
        embedding = result[0].get("spk_embedding")
        if embedding is None:
            raise DiarizationProtocolError("invalid_embedding")
        detach = getattr(embedding, "detach", None)
        if callable(detach):
            embedding = detach()
        cpu = getattr(embedding, "cpu", None)
        if callable(cpu):
            embedding = cpu()
        numpy = getattr(embedding, "numpy", None)
        if callable(numpy):
            embedding = numpy()
        return _normalize_vector(self._np.asarray(embedding).reshape(-1).tolist())


@dataclass(frozen=True)
class DiarizationCommand:
    command: str
    session_id: str | None = None
    sample_start: int | None = None
    samples: tuple[float, ...] = ()
    reason: str = ""


@dataclass
class _Cluster:
    speaker_id: str
    vector_sum: list[float]
    count: int = 1

    @property
    def centroid(self) -> tuple[float, ...]:
        return _normalize_vector(self.vector_sum)

    def add(self, embedding: tuple[float, ...]) -> None:
        if len(embedding) != len(self.vector_sum):
            raise DiarizationProtocolError("embedding_dimension_mismatch")
        for index, value in enumerate(embedding):
            self.vector_sum[index] += value
        self.count += 1


@dataclass
class _ActiveTurn:
    speaker_id: str
    sample_start: int
    sample_end: int
    confidence: float


@dataclass
class SessionState:
    session_id: str
    next_sample_start: int = 0
    sample_count: int = 0
    window_count: int = 0
    clusters: list[_Cluster] = field(default_factory=list)
    active_turn: _ActiveTurn | None = None


def _json_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise DiarizationProtocolError("duplicate_json_field")
        payload[key] = value
    return payload


def _line_text(line: bytes | str) -> str:
    if isinstance(line, bytes):
        if len(line) > MAX_COMMAND_LINE_BYTES:
            raise DiarizationProtocolError("command_line_too_large")
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DiarizationProtocolError("invalid_utf8") from exc
    if isinstance(line, str):
        if len(line.encode("utf-8")) > MAX_COMMAND_LINE_BYTES:
            raise DiarizationProtocolError("command_line_too_large")
        return line
    raise DiarizationProtocolError("invalid_json")


def _valid_session_id(value: object) -> bool:
    return isinstance(value, str) and SESSION_ID_PATTERN.fullmatch(value) is not None


def _validate_audio_metadata(payload: dict[str, object]) -> None:
    if "sample_rate" in payload and payload["sample_rate"] != SAMPLE_RATE:
        raise DiarizationProtocolError("invalid_sample_rate")
    if "channels" in payload and payload["channels"] != CHANNELS:
        raise DiarizationProtocolError("invalid_channels")
    if "audio_encoding" in payload and payload["audio_encoding"] != AUDIO_ENCODING:
        raise DiarizationProtocolError("invalid_audio_encoding")


def _decode_pcm_base64(value: object, *, session_id: str | None) -> tuple[float, ...]:
    if not isinstance(value, str):
        raise DiarizationProtocolError("invalid_audio_base64", session_id=session_id)
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise DiarizationProtocolError(
            "invalid_audio_base64", session_id=session_id
        ) from exc
    if not raw:
        raise DiarizationProtocolError("empty_audio_payload", session_id=session_id)
    if len(raw) > MAX_AUDIO_PAYLOAD_BYTES:
        raise DiarizationProtocolError("audio_payload_too_large", session_id=session_id)
    if len(raw) % 4:
        raise DiarizationProtocolError("audio_payload_unaligned", session_id=session_id)
    samples = tuple(item[0] for item in struct.iter_unpack("<f", raw))
    if not all(math.isfinite(sample) for sample in samples):
        raise DiarizationProtocolError("non_finite_audio", session_id=session_id)
    return samples


def decode_command(line: bytes | str) -> DiarizationCommand:
    """Parse and validate one JSONL v1 command without touching session state."""

    try:
        payload = json.loads(
            _line_text(line), object_pairs_hook=_json_without_duplicate_keys
        )
    except DiarizationProtocolError:
        raise
    except json.JSONDecodeError as exc:
        raise DiarizationProtocolError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise DiarizationProtocolError("invalid_command_shape")

    type_value = payload.get("type")
    command_value = payload.get("command")
    if (
        type_value is not None
        and command_value is not None
        and type_value != command_value
    ):
        raise DiarizationProtocolError("invalid_command")
    command = type_value if type_value is not None else command_value
    if not isinstance(command, str) or command not in {
        "session",
        "audio",
        "end",
        "abort",
    }:
        raise DiarizationProtocolError("unknown_command")

    session_id = payload.get("session_id")
    error_session_id = session_id if _valid_session_id(session_id) else None
    if command != "session" and not _valid_session_id(session_id):
        raise DiarizationProtocolError("invalid_session_id")
    if command == "session":
        if not _valid_session_id(session_id):
            raise DiarizationProtocolError("invalid_session_id")
        _validate_audio_metadata(payload)
        return DiarizationCommand(command=command, session_id=session_id)

    if command == "audio":
        _validate_audio_metadata(payload)
        sample_start = payload.get("sample_start")
        if (
            not isinstance(sample_start, int)
            or isinstance(sample_start, bool)
            or sample_start < 0
        ):
            raise DiarizationProtocolError(
                "invalid_sample_start", session_id=error_session_id
            )
        audio_fields = [field for field in _AUDIO_FIELDS if field in payload]
        if len(audio_fields) != 1:
            raise DiarizationProtocolError(
                "invalid_audio_field", session_id=error_session_id
            )
        samples = _decode_pcm_base64(
            payload[audio_fields[0]], session_id=error_session_id
        )
        sample_count = payload.get("sample_count")
        if sample_count is not None and (
            not isinstance(sample_count, int)
            or isinstance(sample_count, bool)
            or sample_count != len(samples)
        ):
            raise DiarizationProtocolError(
                "sample_count_mismatch", session_id=error_session_id
            )
        return DiarizationCommand(
            command=command,
            session_id=session_id,
            sample_start=sample_start,
            samples=samples,
        )

    reason = payload.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 256:
        raise DiarizationProtocolError(
            "invalid_abort_reason", session_id=error_session_id
        )
    return DiarizationCommand(command=command, session_id=session_id, reason=reason)


def _normalize_vector(values: Any) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise DiarizationProtocolError("invalid_embedding")
    try:
        vector = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise DiarizationProtocolError("invalid_embedding") from exc
    if not vector or len(vector) > MAX_EMBEDDING_DIMENSION:
        raise DiarizationProtocolError("invalid_embedding")
    if not all(math.isfinite(value) for value in vector):
        raise DiarizationProtocolError("invalid_embedding")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0 or not math.isfinite(norm):
        raise DiarizationProtocolError("invalid_embedding")
    return tuple(value / norm for value in vector)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        raise DiarizationProtocolError("embedding_dimension_mismatch")
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _call_provider(
    provider: Any, names: tuple[str, ...], samples: tuple[float, ...], start: int
) -> Any:
    target = provider
    if not callable(target):
        for name in names:
            candidate = getattr(provider, name, None)
            if candidate is not None:
                target = candidate
                break
    if not callable(target):
        raise DiarizationProtocolError("backend_interface_missing")
    try:
        with redirect_stdout(sys.stderr):
            return target(samples, sample_start=start, sample_rate=SAMPLE_RATE)
    except TypeError:
        with redirect_stdout(sys.stderr):
            return target(samples)


def _speech_result(value: Any) -> bool:
    if isinstance(value, dict):
        value = value.get("is_speech", value.get("speech", False))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise DiarizationProtocolError("invalid_vad_result")
        return float(value) > 0.5
    if isinstance(value, (list, tuple)):
        return bool(value)
    raise DiarizationProtocolError("invalid_vad_result")


class DiarizationWorker:
    """Process validated commands with one bounded active session."""

    def __init__(
        self,
        *,
        backend: Any | None = None,
        vad_backend: Any | None = None,
        embedding_backend: Any | None = None,
        max_session_samples: int = MAX_SESSION_SAMPLES,
        max_session_windows: int = MAX_SESSION_WINDOWS,
        max_clusters: int = MAX_CLUSTERS,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self.vad_backend = vad_backend or backend
        self.embedding_backend = embedding_backend or backend
        self.max_session_samples = max_session_samples
        self.max_session_windows = max_session_windows
        self.max_clusters = max_clusters
        self.similarity_threshold = similarity_threshold
        self.state: SessionState | None = None

    def handle_line(self, line: bytes | str) -> list[dict[str, Any]]:
        return self.handle_command(decode_command(line))

    def handle_command(self, command: DiarizationCommand) -> list[dict[str, Any]]:
        if command.command == "session":
            if self.state is not None:
                raise DiarizationProtocolError(
                    "session_already_active", session_id=command.session_id
                )
            self.state = SessionState(session_id=command.session_id or "")
            return [
                {
                    "event_type": "session_started",
                    "session_id": command.session_id,
                    "sample_rate": SAMPLE_RATE,
                    "channels": CHANNELS,
                    "audio_encoding": AUDIO_ENCODING,
                }
            ]

        state = self.state
        if state is None:
            raise DiarizationProtocolError(
                "no_active_session", session_id=command.session_id
            )
        if command.session_id != state.session_id:
            raise DiarizationProtocolError(
                "session_mismatch", session_id=command.session_id
            )
        if command.command == "audio":
            return self._handle_audio(state, command)
        if command.command == "end":
            events = self._finish_turn(state)
            events.append(
                {
                    "event_type": "speaker.done",
                    "session_id": state.session_id,
                    "status": "completed",
                    "sample_count": state.sample_count,
                    "speaker_count": len(state.clusters),
                }
            )
            self.state = None
            return events
        self.state = None
        return [
            {
                "event_type": "session_aborted",
                "session_id": state.session_id,
                "status": "aborted",
                "reason": command.reason or "abort",
            }
        ]

    def _handle_audio(
        self, state: SessionState, command: DiarizationCommand
    ) -> list[dict[str, Any]]:
        assert command.sample_start is not None
        if command.sample_start != state.next_sample_start:
            raise DiarizationProtocolError(
                "sample_start_gap", session_id=state.session_id
            )
        if state.sample_count + len(command.samples) > self.max_session_samples:
            raise DiarizationProtocolError(
                "session_limit_exceeded", session_id=state.session_id
            )
        if state.window_count >= self.max_session_windows:
            raise DiarizationProtocolError(
                "session_limit_exceeded", session_id=state.session_id
            )
        if self.vad_backend is None or self.embedding_backend is None:
            raise DiarizationProtocolError(
                "backend_interface_missing", session_id=state.session_id
            )

        is_speech = _speech_result(
            _call_provider(
                self.vad_backend,
                ("is_speech", "vad", "detect"),
                command.samples,
                command.sample_start,
            )
        )
        events: list[dict[str, Any]] = []
        if is_speech:
            raw_embedding = _call_provider(
                self.embedding_backend,
                ("embedding", "embed", "extract"),
                command.samples,
                command.sample_start,
            )
            if isinstance(raw_embedding, dict):
                raw_embedding = raw_embedding.get("embedding")
            embedding = _normalize_vector(raw_embedding)
            speaker_id, confidence = self._assign_cluster(state, embedding)
            if (
                state.active_turn is not None
                and state.active_turn.speaker_id != speaker_id
            ):
                events.extend(self._finish_turn(state))
                state.active_turn = None
            if state.active_turn is None:
                state.active_turn = _ActiveTurn(
                    speaker_id=speaker_id,
                    sample_start=command.sample_start,
                    sample_end=command.sample_start + len(command.samples),
                    confidence=confidence,
                )
            else:
                state.active_turn.sample_end = command.sample_start + len(
                    command.samples
                )
                state.active_turn.confidence = min(
                    state.active_turn.confidence, confidence
                )
        elif state.active_turn is not None:
            events.extend(self._finish_turn(state))
            state.active_turn = None

        state.next_sample_start += len(command.samples)
        state.sample_count += len(command.samples)
        state.window_count += 1
        return events

    def _assign_cluster(
        self, state: SessionState, embedding: tuple[float, ...]
    ) -> tuple[str, float]:
        if not state.clusters:
            state.clusters.append(_Cluster("speaker_1", list(embedding)))
            return "speaker_1", 1.0
        scores = [_cosine(embedding, cluster.centroid) for cluster in state.clusters]
        best_index = max(range(len(scores)), key=scores.__getitem__)
        best_score = scores[best_index]
        if best_score >= self.similarity_threshold:
            cluster = state.clusters[best_index]
            cluster.add(embedding)
            return cluster.speaker_id, max(0.0, best_score)
        if len(state.clusters) >= self.max_clusters:
            raise DiarizationProtocolError(
                "session_limit_exceeded", session_id=state.session_id
            )
        speaker_id = f"speaker_{len(state.clusters) + 1}"
        state.clusters.append(_Cluster(speaker_id, list(embedding)))
        return speaker_id, 1.0

    def _finish_turn(self, state: SessionState) -> list[dict[str, Any]]:
        turn = state.active_turn
        if turn is None:
            return []
        return [
            {
                "event_type": "speaker.turn",
                "session_id": state.session_id,
                "speaker_id": turn.speaker_id,
                "speaker": turn.speaker_id,
                "sample_start": turn.sample_start,
                "sample_end": turn.sample_end,
                "confidence": round(turn.confidence, 6),
            }
        ]


def _ready_event(backend: Any) -> dict[str, Any]:
    return {
        "event_type": "ready",
        "session_id": None,
        "protocol": DIARIZATION_PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "audio_encoding": AUDIO_ENCODING,
        "backend": str(getattr(backend, "backend_name", "injected")),
        "model_resolution": str(
            getattr(backend, "model_resolution", "injected_backend")
        ),
        "model_download_status": "not_performed",
        "safe_to_download_models": False,
    }


def _error_event(error: DiarizationProtocolError) -> dict[str, Any]:
    return {
        "event_type": "error",
        "session_id": error.session_id,
        "error_code": error.code,
        "fatal": False,
    }


def run_jsonl(
    lines: Iterable[bytes | str],
    *,
    backend: Any,
    output: TextIO | None = None,
    max_session_samples: int = MAX_SESSION_SAMPLES,
    max_session_windows: int = MAX_SESSION_WINDOWS,
    max_clusters: int = MAX_CLUSTERS,
) -> list[dict[str, Any]]:
    """Run commands and return emitted events; malformed lines remain isolated."""

    worker = DiarizationWorker(
        backend=backend,
        max_session_samples=max_session_samples,
        max_session_windows=max_session_windows,
        max_clusters=max_clusters,
    )
    events: list[dict[str, Any]] = []

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if output is not None:
            output.write(
                json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n"
            )
            output.flush()

    emit(_ready_event(backend))
    for line in lines:
        try:
            command_events = worker.handle_line(line)
        except DiarizationProtocolError as error:
            emit(_error_event(error))
            continue
        except Exception:
            session_id = worker.state.session_id if worker.state is not None else None
            emit(
                _error_event(
                    DiarizationProtocolError("backend_failed", session_id=session_id)
                )
            )
            worker.state = None
            continue
        for event in command_events:
            emit(event)
    if worker.state is not None:
        session_id = worker.state.session_id
        emit(
            _error_event(
                DiarizationProtocolError("unexpected_eof", session_id=session_id)
            )
        )
        emit(
            {
                "event_type": "session_aborted",
                "session_id": session_id,
                "status": "aborted",
                "reason": "unexpected_eof",
            }
        )
        worker.state = None
    return events


def _emit_jsonl(output: TextIO, event: dict[str, Any]) -> None:
    output.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
    output.flush()


def _validate_local_model_dir(
    path: Path | None,
    *,
    name: str,
    required_files: tuple[str, ...] = (),
) -> Path:
    if path is None or not path.is_absolute():
        raise DiarizationUnavailable(
            "invalid_local_model_path",
            detail=f"{name} must be an absolute local directory",
        )
    resolved = path.resolve(strict=False)
    if not resolved.is_dir() or not any(resolved.iterdir()):
        raise DiarizationUnavailable(
            "missing_local_model", detail=f"{name} is missing or empty"
        )
    missing = [
        filename for filename in required_files if not (resolved / filename).is_file()
    ]
    if missing:
        raise DiarizationUnavailable(
            "incomplete_local_model",
            detail=f"{name} missing required file(s): {', '.join(missing)}",
        )
    return resolved


def load_default_backend(vad_dir: Path | None, camplus_dir: Path | None) -> Any:
    """Load verified local files without model-hub resolution or downloads."""

    enforce_offline_environment()
    return FunASRLocalDiarizationBackend(
        vad_dir=vad_dir,
        camplus_dir=camplus_dir,
    )


def _unavailable_event(error: DiarizationUnavailable) -> dict[str, Any]:
    return {
        "event_type": "diarization_unavailable",
        "session_id": None,
        "status": "unavailable",
        "reason": error.reason,
        "detail": error.detail,
        "model_resolution": "absolute_local_only",
        "model_download_status": "not_performed",
        "safe_to_download_models": False,
        "protocol": DIARIZATION_PROTOCOL,
    }


def build_description() -> dict[str, Any]:
    return {
        "protocol": DIARIZATION_PROTOCOL,
        "protocol_version": PROTOCOL_VERSION,
        "commands": ["session", "audio", "end", "abort"],
        "events": [
            "ready",
            "session_started",
            "speaker.turn",
            "speaker.done",
            "session_aborted",
            "error",
            "diarization_unavailable",
        ],
        "audio": {
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "encoding": AUDIO_ENCODING,
            "payload_field": "pcm_base64",
            "sample_start_continuity": True,
        },
        "offline_policy": {
            "local_model_directories": "absolute_paths_only",
            "modelscope_offline": "1",
            "hf_hub_offline": "1",
            "remote_download": False,
        },
        "limits": {
            "max_command_line_bytes": MAX_COMMAND_LINE_BYTES,
            "max_audio_payload_bytes": MAX_AUDIO_PAYLOAD_BYTES,
            "max_session_samples": MAX_SESSION_SAMPLES,
            "max_session_windows": MAX_SESSION_WINDOWS,
            "max_clusters": MAX_CLUSTERS,
        },
        "clustering": {
            "cosine_similarity_threshold": DEFAULT_SIMILARITY_THRESHOLD,
            "threshold_basis": "campplus_v2.0.2_local_same_and_different_speaker_sanity",
        },
        "implementation_status": "local_funasr_vad_camplus_supported",
    }


def _stdin_lines(stdin: Any) -> Iterable[bytes | str]:
    stream = getattr(stdin, "buffer", stdin)
    while True:
        line = stream.readline(MAX_COMMAND_LINE_BYTES + 1)
        if not line:
            return
        yield line


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline FunASR diarization JSONL sidecar."
    )
    parser.add_argument("--vad-dir", type=Path)
    parser.add_argument("--camplus-dir", type=Path)
    parser.add_argument("--describe", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, backend: Any | None = None) -> int:
    with _protocol_output_channel(sys.stdout, sys.stderr) as protocol_output:
        args = parse_args(argv)
        enforce_offline_environment()
        if args.describe:
            _emit_jsonl(protocol_output, build_description())
            return 0
        if backend is None:
            try:
                backend = load_default_backend(args.vad_dir, args.camplus_dir)
            except DiarizationUnavailable as error:
                _emit_jsonl(protocol_output, _unavailable_event(error))
                return 0
        run_jsonl(_stdin_lines(sys.stdin), backend=backend, output=protocol_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

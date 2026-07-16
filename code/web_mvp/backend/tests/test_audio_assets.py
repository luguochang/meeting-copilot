import hashlib
import json
from pathlib import Path
import struct
import wave

import pytest

from meeting_copilot_web_mvp.audio_assets import (
    RealtimeWavAssetWriter,
    assemble_realtime_wav_asset,
    audio_metadata_for_file,
    delete_audio_asset,
    persist_uploaded_audio_asset_from_path,
    safe_audio_path,
)


def _float32_payload(sample_count: int, value: float = 0.1) -> bytes:
    return b"".join(struct.pack("<f", value) for _ in range(sample_count))


def _pcm16_sample(value: float) -> int:
    return int(value * 32767)


def _read_wav_samples(path: Path) -> tuple[wave._wave_params, tuple[int, ...]]:
    with wave.open(str(path), "rb") as wav_file:
        params = wav_file.getparams()
        frames = wav_file.readframes(wav_file.getnframes())
    return params, tuple(sample[0] for sample in struct.iter_unpack("<h", frames))


def test_realtime_wav_writer_commits_chunks_and_assembles_partial_tail(tmp_path):
    committed_chunks = []
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="assemble_audio_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
        on_chunk_committed=committed_chunks.append,
    )

    writer.write_float32_pcm(_float32_payload(10, value=0.1))
    writer.write_float32_pcm(_float32_payload(10, value=0.2))
    writer.write_float32_pcm(_float32_payload(3, value=0.3))
    metadata = writer.close()

    session_dir = tmp_path / "audio_assets" / "assemble_audio_1"
    params, samples = _read_wav_samples(session_dir / "audio.wav")
    manifest = json.loads((session_dir / "audio.manifest.json").read_text())

    assert params.nchannels == 1
    assert params.sampwidth == 2
    assert params.framerate == 100
    assert params.nframes == 23
    assert samples == (
        *([_pcm16_sample(0.1)] * 10),
        *([_pcm16_sample(0.2)] * 10),
        *([_pcm16_sample(0.3)] * 3),
    )
    assert [chunk["sample_count"] for chunk in manifest["chunks"]] == [10, 10, 3]
    assert len(list((session_dir / "chunks").glob("*.pcm"))) == 3
    assert metadata["duration_ms"] == 230
    assert [chunk["chunk_index"] for chunk in committed_chunks] == [0, 1, 2]
    assert [chunk["duration_ms"] for chunk in committed_chunks] == [100, 100, 30]
    assert committed_chunks[0]["relative_path"].endswith("chunk-00000000.pcm")


def test_realtime_wav_writer_seals_journal_without_blocking_on_wav_export(tmp_path):
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="background_export_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
    )
    writer.write_float32_pcm(_float32_payload(23, value=0.25))

    sealed = writer.seal()
    audio_path = tmp_path / "audio_assets" / "background_export_1" / "audio.wav"

    assert sealed["assembled"] is False
    assert sealed["duration_ms"] == 230
    assert sealed["chunk_count"] == 3
    assert not audio_path.exists()

    exported = assemble_realtime_wav_asset(
        data_dir=tmp_path,
        session_id="background_export_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
    )

    assert exported["assembled"] is True
    assert exported["duration_ms"] == 230
    assert exported["chunk_count"] == 3
    assert exported["sha256"]
    assert _read_wav_samples(audio_path)[0].nframes == 23


def test_realtime_wav_export_rejects_a_stale_journal_digest(tmp_path):
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="stale_export_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
    )
    writer.write_float32_pcm(_float32_payload(10))
    sealed = writer.seal()

    with pytest.raises(ValueError, match="recording journal changed before export"):
        assemble_realtime_wav_asset(
            data_dir=tmp_path,
            session_id="stale_export_1",
            source_type="browser_live_mic",
            sample_rate_hz=100,
            expected_chunk_count=sealed["chunk_count"],
            expected_sample_count=sealed["sample_count"],
            expected_journal_sha256="0" * 64,
        )

    assert not (tmp_path / "audio_assets" / "stale_export_1" / "audio.wav").exists()


def test_realtime_writer_fences_chunk_before_touching_filesystem(tmp_path):
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="fenced_writer_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
        authorize_chunk_commit=lambda _chunk: False,
    )

    with pytest.raises(RuntimeError, match="capture lease fence rejected"):
        writer.write_float32_pcm(_float32_payload(10))

    chunks_dir = tmp_path / "audio_assets" / "fenced_writer_1" / "chunks"
    assert list(chunks_dir.glob("*.pcm")) == []
    manifest = json.loads(
        (tmp_path / "audio_assets" / "fenced_writer_1" / "audio.manifest.json").read_text()
    )
    assert manifest["chunks"] == []


def test_realtime_wav_writer_recovers_committed_chunks_after_crash(tmp_path):
    interrupted = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="recover_audio_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
    )
    interrupted.write_float32_pcm(_float32_payload(12, value=0.1))

    recovered_chunks = []
    recovered = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="recover_audio_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
        on_chunk_committed=recovered_chunks.append,
    )
    recovered.write_float32_pcm(_float32_payload(5, value=0.2))
    metadata = recovered.close()

    audio_path = tmp_path / "audio_assets" / "recover_audio_1" / "audio.wav"
    _, samples = _read_wav_samples(audio_path)
    assert samples == (
        *([_pcm16_sample(0.1)] * 10),
        *([_pcm16_sample(0.2)] * 5),
    )
    assert metadata["duration_ms"] == 150
    assert recovered_chunks[0]["chunk_index"] == 0
    assert recovered_chunks[0]["sample_count"] == 10


def test_realtime_wav_writer_removes_tmp_and_recovers_renamed_orphan_chunk(tmp_path):
    session_dir = tmp_path / "audio_assets" / "recover_orphan_1"
    chunks_dir = session_dir / "chunks"
    chunks_dir.mkdir(parents=True)
    committed = struct.pack("<hhhh", 100, 200, 300, 400)
    (chunks_dir / "chunk-00000000.pcm").write_bytes(committed)
    (chunks_dir / "chunk-00000001.pcm.tmp").write_bytes(struct.pack("<h", 999))
    (session_dir / "audio.manifest.json.tmp").write_text("incomplete")
    (session_dir / "audio.wav.tmp").write_bytes(b"incomplete")

    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="recover_orphan_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
    )
    metadata = writer.close()

    _, samples = _read_wav_samples(session_dir / "audio.wav")
    assert samples == (100, 200, 300, 400)
    assert metadata["duration_ms"] == 40
    assert not list(session_dir.rglob("*.tmp"))
    manifest = json.loads((session_dir / "audio.manifest.json").read_text())
    assert [chunk["name"] for chunk in manifest["chunks"]] == ["chunk-00000000.pcm"]


def test_realtime_wav_writer_resumes_existing_session_audio_without_overwrite(tmp_path):
    first = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="resume_audio_1",
        source_type="browser_live_mic",
    )
    first.write_float32_pcm(_float32_payload(4_800))
    first_metadata = first.close()

    second = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="resume_audio_1",
        source_type="browser_live_mic",
    )
    second.write_float32_pcm(_float32_payload(4_800, value=0.2))
    second_metadata = second.close()

    audio_path = tmp_path / "audio_assets" / "resume_audio_1" / "audio.wav"
    with wave.open(str(audio_path), "rb") as wav_file:
        assert wav_file.getnframes() == 9_600
        assert wav_file.getframerate() == 16_000

    assert first_metadata["duration_ms"] == 300
    assert second_metadata["duration_ms"] == 600
    assert second_metadata["file_size_bytes"] > first_metadata["file_size_bytes"]
    assert second_metadata["sha256"] != first_metadata["sha256"]
    manifest = json.loads(
        (audio_path.parent / "audio.manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["chunk_duration_ms"] == 5_000


def test_realtime_wav_writer_appends_to_legacy_wav_without_duplicate_frames(tmp_path):
    session_dir = tmp_path / "audio_assets" / "legacy_audio_1"
    session_dir.mkdir(parents=True)
    audio_path = session_dir / "audio.wav"
    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(100)
        wav_file.writeframes(struct.pack("<hhhh", 1, 2, 3, 4))

    first = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="legacy_audio_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.02,
    )
    first.write_float32_pcm(_float32_payload(2, value=0.1))
    first.close()
    second = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="legacy_audio_1",
        source_type="browser_live_mic",
        sample_rate_hz=100,
        chunk_duration_seconds=0.02,
    )
    second.write_float32_pcm(_float32_payload(2, value=0.2))
    second.close()

    _, samples = _read_wav_samples(audio_path)
    assert samples == (
        1,
        2,
        3,
        4,
        _pcm16_sample(0.1),
        _pcm16_sample(0.1),
        _pcm16_sample(0.2),
        _pcm16_sample(0.2),
    )


def test_audio_metadata_hashes_incrementally_without_path_read_bytes(monkeypatch, tmp_path):
    relative_path = Path("audio_assets") / "hash_audio_1" / "audio.wav"
    audio_path = tmp_path / relative_path
    audio_path.parent.mkdir(parents=True)
    payload = b"stream-this-file" * 8_192
    audio_path.write_bytes(payload)

    def reject_read_bytes(_path):
        raise AssertionError("metadata must not load the complete audio with Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    metadata = audio_metadata_for_file(
        data_dir=tmp_path,
        session_id="hash_audio_1",
        relative_path=relative_path,
        source_type="upload",
        sample_rate_hz=None,
        sample_count=None,
    )

    assert metadata["file_size_bytes"] == len(payload)
    assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()


def test_uploaded_audio_is_streamed_from_path_into_managed_storage(tmp_path):
    source = tmp_path / "incoming.m4a"
    payload = b"streamed-upload" * 100_000
    source.write_bytes(payload)

    metadata = persist_uploaded_audio_asset_from_path(
        data_dir=tmp_path / "data",
        session_id="file_stream_1",
        source_type="uploaded_file",
        filename="meeting.m4a",
        source_path=source,
    )

    stored = tmp_path / "data" / metadata["relative_path"]
    assert stored.read_bytes() == payload
    assert metadata["file_size_bytes"] == len(payload)
    assert metadata["sha256"] == hashlib.sha256(payload).hexdigest()
    assert not stored.with_suffix(".m4a.tmp").exists()


@pytest.mark.parametrize(
    "relative_path",
    ["../outside.wav", "audio_assets/../../outside.wav", "/tmp/outside.wav"],
)
def test_safe_audio_path_rejects_paths_outside_data_dir(tmp_path, relative_path):
    with pytest.raises(ValueError):
        safe_audio_path(tmp_path, relative_path)


@pytest.mark.parametrize(
    "relative_path",
    ["other/session/audio.wav", "audio_assets/../session/audio.wav", "audio_assets/session"],
)
def test_delete_audio_asset_rejects_paths_not_owned_by_controlled_session(tmp_path, relative_path):
    with pytest.raises(ValueError):
        delete_audio_asset(tmp_path, {"relative_path": relative_path})


def test_delete_audio_asset_removes_entire_controlled_session_directory(tmp_path):
    session_dir = tmp_path / "audio_assets" / "delete_audio_1"
    chunks_dir = session_dir / "chunks"
    chunks_dir.mkdir(parents=True)
    (session_dir / "audio.wav").write_bytes(b"wav")
    (session_dir / "audio.manifest.json").write_text("{}")
    (chunks_dir / "chunk-00000000.pcm").write_bytes(b"pcm")

    result = delete_audio_asset(
        tmp_path,
        {"relative_path": "audio_assets/delete_audio_1/audio.wav"},
    )

    assert result == "deleted"
    assert not session_dir.exists()
    assert delete_audio_asset(
        tmp_path,
        {"relative_path": "audio_assets/delete_audio_1/audio.wav"},
    ) == "already_missing"

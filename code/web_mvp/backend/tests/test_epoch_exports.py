from __future__ import annotations

import hashlib
import struct
import wave

import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.audio_assets import (
    RealtimeWavAssetWriter,
    assemble_realtime_wav_asset,
    inspect_realtime_audio_journal,
    realtime_audio_relative_path,
    resolve_realtime_recording_layout,
)
from meeting_copilot_web_mvp.recording_recovery import (
    reconcile_and_recover_abandoned_recordings,
    reconcile_and_recover_expired_recordings,
)
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _capture(persistence, data_dir, *, meeting_id="epoch-export", track="microphone", epoch=1,
             tracked=True, orphan=False, native=False):
    source_type = "browser_system_audio" if track == "system_audio" else "browser_live_mic"
    persistence.begin_recording(meeting_id=meeting_id, track=track, epoch=epoch, source_type=source_type,
                                sample_rate_hz=100, lease_owner="old-capture", lease_ms=500, now_ms=1_000)

    def committed(chunk):
        if orphan and chunk["chunk_index"] == 2:
            return
        persistence.record_audio_chunk(
            meeting_id=meeting_id, track=track, epoch=epoch, chunk_seq=chunk["chunk_index"],
            relative_path=chunk["relative_path"], sha256=chunk["sha256"], sample_rate_hz=100,
            sample_count=chunk["sample_count"], duration_ms=chunk["duration_ms"],
            file_size_bytes=chunk["file_size_bytes"], captured_at_ms=chunk["timestamp_ms"], now_ms=1_100,
            source_sequence_start=chunk.get("source_sequence_start"), source_sequence_end=chunk.get("source_sequence_end"),
            source_timestamp_start_ms=chunk.get("source_timestamp_start_ms"),
            source_timestamp_end_ms=chunk.get("source_timestamp_end_ms"),
        )

    writer = RealtimeWavAssetWriter(data_dir=data_dir, session_id=meeting_id, source_type=source_type,
                                    sample_rate_hz=100, chunk_duration_seconds=0.1,
                                    track_id=track if tracked else None, epoch=epoch, started_at_ms=1_004,
                                    on_chunk_committed=committed)
    source = {"source_track": track, "capture_epoch": epoch, "track_sequence": 1,
              "source_timestamp_ms": 5_000} if native else None
    writer.write_float32_pcm(struct.pack("<f", 0.2) * 30, source_frame=source)
    sealed = writer.seal()
    return writer, sealed


@pytest.mark.parametrize("track,epoch,tracked,recovery", [
    ("microphone", 1, True, "expired"),
    ("system_audio", 3, True, "abandoned"),
    ("microphone", 0, False, "expired"),
])
def test_recovery_exports_exact_epoch_with_clock_and_native_ranges(tmp_path, track, epoch, tracked, recovery):
    persistence = V2Persistence(tmp_path / "recordings.db")
    try:
        _, sealed = _capture(persistence, tmp_path, track=track, epoch=epoch, tracked=tracked, orphan=True, native=True)
        manifest_path = (tmp_path / sealed["relative_path"]).parent / "audio.manifest.json"
        manifest_before = manifest_path.read_bytes()
        journal = inspect_realtime_audio_journal(data_dir=tmp_path, session_id="epoch-export", sample_rate_hz=100,
                                                track_id=track if tracked else None, epoch=epoch)
        assert journal["journal_sha256"] == sealed["journal_sha256"]
        assert manifest_path.read_bytes() == manifest_before
        before = persistence.list_audio_chunks("epoch-export")
        assert len(before) == 2
        recover = (reconcile_and_recover_expired_recordings if recovery == "expired"
                   else reconcile_and_recover_abandoned_recordings)
        assert recover(persistence, data_dir=tmp_path, now_ms=2_000) == ["epoch-export"]
        chunks = persistence.list_audio_chunks("epoch-export")
        assert len(chunks) == 3
        assert chunks[:2] == before
        assert [chunk["timestamp_ms"] for chunk in chunks] == [1_004, 1_104, 1_204]
        assert all(chunk["source_sequence_start"] == 1 for chunk in chunks)
        recording = persistence.get_recording_session("epoch-export", track=track, epoch=epoch)
        job = persistence.list_recording_exports(meeting_id="epoch-export")[0]
        assert recording["status"] == "interrupted"
        assert recording["output_relative_path"] == job["output_relative_path"] == sealed["relative_path"]
        assert job["input_journal_sha256"] == sealed["journal_sha256"]
        output = assemble_realtime_wav_asset(
            data_dir=tmp_path, session_id="epoch-export", source_type=recording["source_type"], sample_rate_hz=100,
            track_id=track if tracked else None, epoch=epoch, expected_chunk_count=3, expected_sample_count=30,
            expected_journal_sha256=job["input_journal_sha256"], started_at_ms=recording["started_at_ms"],
        )
        with wave.open(str(tmp_path / output["relative_path"]), "rb") as audio:
            assert audio.getnframes() == 30
        assert persistence.get_recording_session("epoch-export", track=track, epoch=epoch)["status"] == "interrupted"
    finally:
        persistence.close()


def test_new_empty_epoch_never_absorbs_old_legacy_chunks(tmp_path):
    persistence = V2Persistence(tmp_path / "recordings.db")
    try:
        legacy = RealtimeWavAssetWriter(data_dir=tmp_path, session_id="legacy-meeting", source_type="browser_live_mic",
                                        sample_rate_hz=100, chunk_duration_seconds=0.1, epoch=0)
        legacy.write_float32_pcm(struct.pack("<f", 0.2) * 10)
        old_chunk = tmp_path / "audio_assets/legacy-meeting/chunks/chunk-00000000.pcm"
        old_bytes = old_chunk.read_bytes()
        tracked_path = realtime_audio_relative_path("legacy-meeting", track_id="microphone", epoch=4).as_posix()
        recording = persistence.begin_recording(
            meeting_id="legacy-meeting", track="microphone", epoch=4, source_type="browser_live_mic",
            sample_rate_hz=100, lease_owner="new-capture", lease_ms=500, now_ms=1_000,
            output_relative_path=tracked_path,
        )
        assert resolve_realtime_recording_layout(recording=recording, chunks=[], data_dir=tmp_path)["track_id"] == "microphone"
        reconcile_and_recover_expired_recordings(persistence, data_dir=tmp_path, now_ms=2_000)
        assert persistence.list_audio_chunks("legacy-meeting") == []
        assert persistence.list_recording_exports(meeting_id="legacy-meeting") == []
        assert old_chunk.read_bytes() == old_bytes
    finally:
        persistence.close()


@pytest.mark.parametrize("path", [
    "audio_assets/another-meeting/tracks/microphone/epoch-1/chunks/chunk-00000000.pcm",
    "audio_assets/layout/tracks/system_audio/epoch-1/chunks/chunk-00000000.pcm",
    "audio_assets/layout/tracks/microphone/epoch-4/chunks/chunk-00000000.pcm",
    "audio_assets/layout/../layout/chunks/chunk-00000000.pcm",
])
def test_layout_rejects_foreign_capture_paths(path):
    with pytest.raises(ValueError, match="capture identity"):
        resolve_realtime_recording_layout(recording={"meeting_id": "layout", "track": "microphone", "epoch": 1},
                                          chunks=[{"chunk_seq": 0, "relative_path": path}])


def test_layout_rejects_mixed_legacy_and_epoch_chunks():
    with pytest.raises(ValueError, match="mixed layouts"):
        resolve_realtime_recording_layout(
            recording={"meeting_id": "mixed", "track": "microphone", "epoch": 1},
            chunks=[{"chunk_seq": 0, "relative_path": "audio_assets/mixed/chunks/chunk-00000000.pcm"},
                    {"chunk_seq": 1, "relative_path": "audio_assets/mixed/tracks/microphone/epoch-1/chunks/chunk-00000001.pcm"}],
        )


def test_old_pending_export_resolves_chunks_and_does_not_overwrite_new_capture(tmp_path):
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    try:
        _, sealed = _capture(persistence, tmp_path)
        original_chunks = {chunk["relative_path"]: (tmp_path / chunk["relative_path"]).read_bytes()
                           for chunk in persistence.list_audio_chunks("epoch-export")}
        queued = persistence.seal_recording_and_enqueue_export(
            meeting_id="epoch-export", track="microphone", epoch=1, lease_owner="old-capture",
            output_relative_path="audio_assets/epoch-export/audio.wav", interrupted=True, now_ms=1_200,
        )
        # Reproduce the old handler's exact wrong-layout ValueError locally.
        with pytest.raises(ValueError, match="recording journal changed before export"):
            assemble_realtime_wav_asset(data_dir=tmp_path, session_id="epoch-export", source_type="browser_live_mic",
                                        sample_rate_hz=100, epoch=1, expected_chunk_count=3,
                                        expected_sample_count=30, expected_journal_sha256=sealed["journal_sha256"])
        job = persistence.claim_next_recording_export(worker_id="export-worker", lease_ms=10_000, now_ms=1_300)
        assert job["id"] == queued["export"]["id"]
        persistence.begin_recording(meeting_id="epoch-export", track="microphone", epoch=4, source_type="browser_live_mic",
                                    sample_rate_hz=100, lease_owner="new-capture", lease_ms=10_000, now_ms=1_300,
                                    output_relative_path=realtime_audio_relative_path("epoch-export", track_id="microphone", epoch=4).as_posix())
        latest_audio = {"track_id": "microphone", "epoch": 4, "format": "pcm_s16le_chunk_journal"}
        app.state.asr_live_repository.create({"session_id": "epoch-export", "audio": latest_audio})
        output = app.state.recording_export_executor._export_handler(job)
        assert output["relative_path"] == sealed["relative_path"]
        assert app.state.asr_live_repository.get("epoch-export")["audio"] == latest_audio
        completed = persistence.complete_recording_export(export_id=job["id"], worker_id="export-worker",
                                                         output=output, now_ms=1_400)
        assert completed["status"] == "succeeded"
        assert completed["output_relative_path"] == output["relative_path"]
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=1)["output_relative_path"] == output["relative_path"]
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=4)["status"] == "active"
        assert all((tmp_path / path).read_bytes() == raw for path, raw in original_chunks.items())
        assert hashlib.sha256((tmp_path / output["relative_path"]).read_bytes()).hexdigest() == output["sha256"]
    finally:
        persistence.close()


def test_failed_export_requires_explicit_identity_checked_retry(tmp_path):
    persistence = V2Persistence(tmp_path / "recordings.db")
    try:
        _, sealed = _capture(persistence, tmp_path)
        queued = persistence.seal_recording_and_enqueue_export(
            meeting_id="epoch-export", track="microphone", epoch=1, lease_owner="old-capture",
            output_relative_path="audio_assets/epoch-export/audio.wav", interrupted=True, now_ms=1_200,
        )
        export_id = queued["export"]["id"]
        for attempt in range(3):
            now = 1_300 + attempt * 100
            job = persistence.claim_next_recording_export(worker_id="export-worker", now_ms=now, lease_ms=10_000)
            persistence.retry_recording_export(export_id=job["id"], worker_id="export-worker", error_class="ValueError",
                                               next_attempt_at_ms=now, now_ms=now)
        assert persistence.list_recording_exports(meeting_id="epoch-export")[0]["status"] == "failed"
        persistence.begin_recording(meeting_id="epoch-export", track="microphone", epoch=4, source_type="browser_live_mic",
                                    sample_rate_hz=100, lease_owner="new-capture", lease_ms=10_000, now_ms=1_600,
                                    output_relative_path=realtime_audio_relative_path("epoch-export", track_id="microphone", epoch=4).as_posix())
        with pytest.raises(ValueError, match="changed before explicit retry"):
            persistence.retry_failed_recording_export(export_id=export_id, expected_journal_sha256="f" * 64, now_ms=2_000)
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=1)["status"] == "failed"
        retried = persistence.retry_failed_recording_export(export_id=export_id,
                                                            expected_journal_sha256=sealed["journal_sha256"], now_ms=2_000)
        assert retried["status"] == "pending"
        assert retried["attempts"] == 0
        assert retried["output_relative_path"] == sealed["relative_path"]
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=1)["status"] == "sealed"
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=4)["status"] == "active"
        audit = [event for event in persistence.list_events("epoch-export") if event["type"] == "recording.export.retry_requested"]
        assert audit[0]["payload"]["previous_error_class"] == "ValueError"
        assert audit[0]["payload"]["previous_attempts"] == 3
        with pytest.raises(RuntimeError, match="only a failed"):
            persistence.retry_failed_recording_export(export_id=export_id, expected_journal_sha256=sealed["journal_sha256"], now_ms=2_001)
    finally:
        persistence.close()


def test_export_completion_rejects_foreign_output_without_marking_ready(tmp_path):
    persistence = V2Persistence(tmp_path / "recordings.db")
    try:
        _, sealed = _capture(persistence, tmp_path)
        persistence.seal_recording_and_enqueue_export(
            meeting_id="epoch-export", track="microphone", epoch=1, lease_owner="old-capture",
            output_relative_path=sealed["relative_path"], interrupted=False, now_ms=1_200,
        )
        job = persistence.claim_next_recording_export(worker_id="export-worker", now_ms=1_300, lease_ms=10_000)
        with pytest.raises(ValueError, match="does not match its journal layout"):
            persistence.complete_recording_export(export_id=job["id"], worker_id="export-worker", now_ms=1_400,
                                                 output={"sha256": "f" * 64, "file_size_bytes": 104,
                                                         "relative_path": "audio_assets/other/audio.wav"})
        assert persistence.list_recording_exports(meeting_id="epoch-export")[0]["status"] == "running"
        assert persistence.get_recording_session("epoch-export", track="microphone", epoch=1)["status"] == "exporting"
    finally:
        persistence.close()

from __future__ import annotations

import struct

from meeting_copilot_web_mvp.audio_assets import SAMPLE_RATE_HZ, RealtimeWavAssetWriter
from meeting_copilot_web_mvp.recording_recovery import reconcile_and_recover_expired_recordings
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def test_recovery_reconciles_fsynced_chunk_missing_from_sqlite(tmp_path):
    meeting_id = "orphan-chunk-recovery"
    persistence = V2Persistence(tmp_path / "meeting_copilot.db")
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=SAMPLE_RATE_HZ,
        lease_owner="crashed-capture",
        lease_ms=500,
        now_ms=1_000,
    )
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id=meeting_id,
        source_type="browser_live_mic",
    )
    writer.write_float32_pcm(struct.pack("<f", 0.1) * (SAMPLE_RATE_HZ * 9))

    assert persistence.list_audio_chunks(meeting_id) == []
    assert len(list((tmp_path / "audio_assets" / meeting_id / "chunks").glob("*.pcm"))) == 1

    recovered = reconcile_and_recover_expired_recordings(
        persistence,
        data_dir=tmp_path,
        now_ms=2_000,
    )

    chunks = persistence.list_audio_chunks(meeting_id)
    recording = persistence.get_recording_session(meeting_id, track="microphone", epoch=0)
    export = persistence.list_recording_exports(meeting_id=meeting_id)[0]
    assert recovered == [meeting_id]
    assert len(chunks) == 1
    assert chunks[0]["duration_ms"] == 5_000
    assert recording["status"] == "interrupted"
    assert recording["journal_sha256"] == export["input_journal_sha256"]
    persistence.close()


def test_stale_recovery_scan_cannot_write_after_capture_generation_resumes(
    tmp_path,
    monkeypatch,
):
    meeting_id = "stale-recovery-scan"
    persistence = V2Persistence(tmp_path / "meeting_copilot.db")
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=SAMPLE_RATE_HZ,
        lease_owner="expired-owner",
        lease_ms=500,
        now_ms=1_000,
    )
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id=meeting_id,
        source_type="browser_live_mic",
    )
    writer.write_float32_pcm(struct.pack("<f", 0.1) * (SAMPLE_RATE_HZ * 5))
    stale_scan = persistence.list_expired_recording_sessions(now_ms=2_000)
    resumed = persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=SAMPLE_RATE_HZ,
        lease_owner="resumed-owner",
        lease_ms=5_000,
        now_ms=2_000,
    )
    monkeypatch.setattr(
        persistence,
        "list_expired_recording_sessions",
        lambda *, now_ms: stale_scan,
    )

    recovered = reconcile_and_recover_expired_recordings(
        persistence,
        data_dir=tmp_path,
        now_ms=2_100,
    )

    assert recovered == []
    assert persistence.list_audio_chunks(meeting_id) == []
    current = persistence.get_recording_session(
        meeting_id,
        track="microphone",
        epoch=0,
    )
    assert current["status"] == "active"
    assert current["capture_generation"] == resumed["capture_generation"] == 2
    persistence.close()


def test_partial_init_directory_does_not_block_other_expired_recordings(tmp_path):
    persistence = V2Persistence(tmp_path / "meeting_copilot.db")
    for meeting_id in ("a-partial-init", "b-fsynced-audio"):
        persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
        persistence.begin_recording(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=SAMPLE_RATE_HZ,
            lease_owner=f"owner-{meeting_id}",
            lease_ms=500,
            now_ms=1_000,
        )
    (tmp_path / "audio_assets" / "a-partial-init").mkdir(parents=True)
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="b-fsynced-audio",
        source_type="browser_live_mic",
    )
    writer.write_float32_pcm(struct.pack("<f", 0.1) * (SAMPLE_RATE_HZ * 5))

    recovered = reconcile_and_recover_expired_recordings(
        persistence,
        data_dir=tmp_path,
        now_ms=2_000,
    )

    assert recovered == ["a-partial-init", "b-fsynced-audio"]
    assert persistence.get_recording_session(
        "a-partial-init", track="microphone", epoch=0
    )["status"] == "interrupted"
    assert len(persistence.list_audio_chunks("b-fsynced-audio")) == 1
    assert persistence.list_recording_exports(meeting_id="b-fsynced-audio")[0][
        "status"
    ] == "pending"
    persistence.close()

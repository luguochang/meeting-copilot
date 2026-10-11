from __future__ import annotations

import hashlib
import time
import wave

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.recording_status import recording_attempt_failed, recording_needs_assembly


@pytest.fixture
def workspace(tmp_path):
    app = create_app(data_dir=tmp_path, prewarm_funasr=False)
    with TestClient(app) as client:
        persistence = app.state.v2_persistence
        persistence.create_meeting(meeting_id="status-story", title="合成录音状态", now_ms=time.time_ns() // 1_000_000)
        yield client, persistence, tmp_path


def begin(persistence, epoch):
    return persistence.begin_recording(meeting_id="status-story", track="microphone", epoch=epoch,
        source_type="browser_live_mic", sample_rate_hz=16_000, lease_owner=f"capture-{epoch}",
        lease_ms=30_000, now_ms=time.time_ns() // 1_000_000)


def abort(persistence, epoch):
    row = begin(persistence, epoch)
    return persistence.abort_recording_setup(meeting_id="status-story", track="microphone", epoch=epoch,
        lease_owner=f"capture-{epoch}", capture_generation=row["capture_generation"], now_ms=time.time_ns() // 1_000_000)


def saved(persistence, data_dir):
    begin(persistence, 1)
    path = data_dir / "audio_assets/status-story/audio.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16_000)
        audio.writeframes(b"\x00\x00" * 16_000)
    with persistence._write_transaction():
        persistence._conn.execute("UPDATE recording_sessions SET status='ready',chunk_count=1,sample_count=16000,"
            "duration_ms=1000,lease_owner=NULL,lease_until_ms=NULL,output_relative_path=?,output_sha256=?,"
            "output_file_size_bytes=? WHERE meeting_id='status-story' AND epoch=1",
            (path.relative_to(data_dir).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size))


def test_three_empty_setup_failures_are_not_an_infinite_assembly(workspace):
    client, persistence, _ = workspace
    for epoch in (9, 10, 11):
        abort(persistence, epoch)
    snapshot = client.get("/v2/meetings/status-story/snapshot").json()
    assert snapshot["audio"]["status"] == "failed"
    assert snapshot["runtime"]["recording"]["state"] == "error"
    audio = client.get("/v2/meetings/status-story/audio").json()
    assert audio["status"] == "failed" and not audio["assembled"]
    assert len(audio["recordings"]) == 3
    assert all(row["error_class"] == "recording_setup_failed" for row in audio["recordings"])


def test_saved_wave_stays_playable_when_later_empty_setup_fails(workspace):
    client, persistence, directory = workspace
    saved(persistence, directory)
    for epoch in (9, 10, 11):
        abort(persistence, epoch)
    snapshot = client.get("/v2/meetings/status-story/snapshot").json()
    assert snapshot["audio"]["status"] == "partial_failure"
    assert snapshot["runtime"]["recording"]["label"] == "部分录音未成功，已保存部分可回放"
    audio = client.get("/v2/meetings/status-story/audio").json()
    assert audio["status"] == "partial_failure" and audio["assembled"]
    response = client.get(audio["playback_url"])
    assert response.status_code == 200 and response.content.startswith(b"RIFF")
    assert len(audio["recordings"]) == 4


def test_new_active_capture_takes_priority_over_old_failed_attempt(workspace):
    client, persistence, directory = workspace
    saved(persistence, directory)
    abort(persistence, 9)
    begin(persistence, 12)
    snapshot = client.get("/v2/meetings/status-story/snapshot").json()
    assert snapshot["audio"]["status"] == "recording"
    assert snapshot["runtime"]["recording"]["state"] == "active"
    assert client.get("/v2/meetings/status-story/audio").json()["status"] == "recording"


@pytest.mark.parametrize("row,failed,pending", [
    ({"status": "interrupted", "chunk_count": 0, "sample_count": 0}, True, False),
    ({"status": "interrupted", "chunk_count": 1, "sample_count": 16_000}, False, True),
    ({"status": "interrupted"}, False, True),
    ({"status": "exporting", "chunk_count": 1, "sample_count": 16_000}, False, True),
])
def test_nonempty_or_unknown_capture_is_not_mislabeled_as_empty_failure(row, failed, pending):
    assert recording_attempt_failed(row) is failed
    assert recording_needs_assembly(row) is pending

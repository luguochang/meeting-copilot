from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.audio_assets import RealtimeWavAssetWriter
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _float32_payload(sample_count: int, value: float) -> bytes:
    return struct.pack("<f", value) * sample_count


def _seed_ready_track(
    app: object,
    *,
    data_dir: Path,
    meeting_id: str,
    track_id: str,
    source_type: str,
    value: float,
    started_at_ms: int,
) -> dict:
    persistence = app.state.v2_persistence
    lease_owner = f"capture-{track_id}"
    persistence.begin_recording(
        meeting_id=meeting_id,
        track=track_id,
        epoch=0,
        source_type=source_type,
        sample_rate_hz=16_000,
        lease_owner=lease_owner,
        lease_ms=60_000,
        now_ms=started_at_ms,
    )

    def commit_chunk(chunk: dict) -> None:
        persistence.record_audio_chunk(
            meeting_id=meeting_id,
            track=track_id,
            epoch=int(chunk["epoch"]),
            chunk_seq=int(chunk["sequence"]),
            relative_path=str(chunk["relative_path"]),
            sha256=str(chunk["sha256"]),
            sample_rate_hz=int(chunk["sample_rate_hz"]),
            sample_count=int(chunk["sample_count"]),
            duration_ms=int(chunk["duration_ms"]),
            file_size_bytes=int(chunk["file_size_bytes"]),
            captured_at_ms=int(chunk["timestamp_ms"]),
            now_ms=started_at_ms + 10,
            lease_owner=lease_owner,
            lease_ms=60_000,
        )

    writer = RealtimeWavAssetWriter(
        data_dir=data_dir,
        session_id=meeting_id,
        source_type=source_type,
        track_id=track_id,
        epoch=0,
        started_at_ms=started_at_ms,
        on_chunk_committed=commit_chunk,
    )
    writer.write_float32_pcm(_float32_payload(1_600, value))
    metadata = writer.close()
    persistence.seal_recording_and_enqueue_export(
        meeting_id=meeting_id,
        track=track_id,
        epoch=0,
        lease_owner=lease_owner,
        output_relative_path=str(metadata["relative_path"]),
        expected_journal_sha256=str(metadata["journal_sha256"]),
        interrupted=False,
        now_ms=started_at_ms + 20,
    )
    export = persistence.claim_next_recording_export(
        worker_id=f"export-{track_id}",
        now_ms=started_at_ms + 21,
        lease_ms=60_000,
    )
    assert export is not None
    assert export["track_id"] == track_id
    completed = persistence.complete_recording_export(
        export_id=str(export["id"]),
        worker_id=f"export-{track_id}",
        output=metadata,
        now_ms=started_at_ms + 22,
    )
    assert completed is not None
    return metadata


@pytest.fixture
def dual_track_app(tmp_path: Path) -> Iterator[tuple[TestClient, object]]:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        yield client, app


def test_dual_track_writer_uses_independent_owned_paths_and_stable_timeline(tmp_path: Path):
    committed: list[dict] = []
    microphone = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="dual-paths",
        source_type="browser_live_mic",
        track_id="microphone",
        epoch=3,
        started_at_ms=10_000,
        chunk_duration_seconds=0.1,
        sample_rate_hz=100,
        on_chunk_committed=committed.append,
    )
    system_audio = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="dual-paths",
        source_type="macos_system_audio",
        track_id="system_audio",
        epoch=7,
        started_at_ms=10_025,
        chunk_duration_seconds=0.1,
        sample_rate_hz=100,
        on_chunk_committed=committed.append,
    )

    microphone.write_float32_pcm(_float32_payload(10, 0.2))
    system_audio.write_float32_pcm(_float32_payload(10, -0.3))
    mic_metadata = microphone.close()
    system_metadata = system_audio.close()

    assert mic_metadata["relative_path"] == (
        "audio_assets/dual-paths/tracks/microphone/epoch-3/audio.wav"
    )
    assert system_metadata["relative_path"] == (
        "audio_assets/dual-paths/tracks/system_audio/epoch-7/audio.wav"
    )
    assert mic_metadata["sha256"] != system_metadata["sha256"]
    assert [
        (item["track_id"], item["epoch"], item["sequence"], item["timestamp_ms"])
        for item in committed
    ] == [
        ("microphone", 3, 0, 10_000),
        ("system_audio", 7, 0, 10_025),
    ]


def test_dual_track_timeline_and_failure_state_survive_sqlite_reopen(tmp_path: Path):
    database_path = tmp_path / "meeting.sqlite3"
    persistence = V2Persistence(database_path)
    for track_id, source_type in (
        ("microphone", "browser_live_mic"),
        ("system_audio", "macos_system_audio"),
    ):
        persistence.begin_recording(
            meeting_id="dual-durable",
            track=track_id,
            epoch=2,
            source_type=source_type,
            sample_rate_hz=16_000,
            lease_owner=f"owner-{track_id}",
            lease_ms=5_000,
            now_ms=1_000,
        )
        persistence.record_audio_chunk(
            meeting_id="dual-durable",
            track=track_id,
            epoch=2,
            chunk_seq=4,
            relative_path=(
                f"audio_assets/dual-durable/tracks/{track_id}/epoch-2/"
                "chunks/chunk-00000004.pcm"
            ),
            sha256=("a" if track_id == "microphone" else "b") * 64,
            sample_rate_hz=16_000,
            sample_count=1_600,
            duration_ms=100,
            file_size_bytes=3_200,
            captured_at_ms=1_400,
            now_ms=1_410,
            lease_owner=f"owner-{track_id}",
            lease_ms=5_000,
        )
    failed = persistence.fail_recording_track(
        meeting_id="dual-durable",
        track="system_audio",
        epoch=2,
        error_class="screen_capture_permission_denied",
        now_ms=1_500,
    )
    persistence.close()

    reopened = V2Persistence(database_path)
    try:
        chunks = reopened.list_audio_chunks("dual-durable")
        tracks = reopened.list_recording_sessions("dual-durable")
    finally:
        reopened.close()

    assert failed["track_id"] == "system_audio"
    assert failed["status"] == "failed"
    assert failed["error_class"] == "screen_capture_permission_denied"
    assert {
        (item["track_id"], item["epoch"], item["sequence"], item["timestamp_ms"])
        for item in chunks
    } == {
        ("microphone", 2, 4, 1_400),
        ("system_audio", 2, 4, 1_400),
    }
    assert {item["track_id"]: item["status"] for item in tracks} == {
        "microphone": "active",
        "system_audio": "failed",
    }


def test_audio_api_lists_tracks_plays_each_and_explicitly_derives_mixed(
    dual_track_app,
    tmp_path: Path,
):
    client, app = dual_track_app
    meeting_id = "dual-api"
    app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id,
        title="双轨会议",
        now_ms=1_000,
    )
    microphone = _seed_ready_track(
        app,
        data_dir=tmp_path,
        meeting_id=meeting_id,
        track_id="microphone",
        source_type="browser_live_mic",
        value=0.2,
        started_at_ms=10_000,
    )
    system_audio = _seed_ready_track(
        app,
        data_dir=tmp_path,
        meeting_id=meeting_id,
        track_id="system_audio",
        source_type="macos_system_audio",
        value=-0.1,
        started_at_ms=10_025,
    )

    listing = client.get(f"/v2/meetings/{meeting_id}/audio")
    mic_content = client.get(
        f"/v2/meetings/{meeting_id}/audio/tracks/microphone/content?epoch=0"
    )
    mic_range = client.get(
        f"/v2/meetings/{meeting_id}/audio/tracks/microphone/content?epoch=0",
        headers={"Range": "bytes=0-3"},
    )
    system_content = client.get(
        f"/v2/meetings/{meeting_id}/audio/tracks/system_audio/content?epoch=0"
    )
    mixed = client.post(f"/v2/meetings/{meeting_id}/audio/mixed")

    assert listing.status_code == 200
    assert listing.json()["tracks"] == ["microphone", "system_audio"]
    assert {
        item["track_id"]: (item["source"], item["status"], item["epoch"])
        for item in listing.json()["track_states"]
    } == {
        "microphone": ("microphone", "ready", 0),
        "system_audio": ("system_audio", "ready", 0),
    }
    assert mic_content.status_code == 200
    assert mic_content.content == (tmp_path / microphone["relative_path"]).read_bytes()
    assert mic_range.status_code == 206
    assert mic_range.headers["content-range"].startswith("bytes 0-3/")
    assert mic_range.content == mic_content.content[:4]
    assert system_content.status_code == 200
    assert system_content.content == (tmp_path / system_audio["relative_path"]).read_bytes()
    assert mixed.status_code == 201, mixed.text

    asset = mixed.json()["asset"]
    assert asset["kind"] == "mixed"
    assert asset["derivation"] == "local_pcm16_timeline_mix"
    assert asset["retention_policy"] == "local_until_user_deletes"
    assert [(source["track_id"], source["epoch"]) for source in asset["sources"]] == [
        ("microphone", 0),
        ("system_audio", 0),
    ]
    assert asset["remote_upload_used"] is False
    assert app.state.v2_persistence.get_recording_session(
        meeting_id, track="microphone", epoch=0
    )["output_sha256"] == microphone["sha256"]
    assert app.state.v2_persistence.get_recording_session(
        meeting_id, track="system_audio", epoch=0
    )["output_sha256"] == system_audio["sha256"]

    mixed_content = client.get(asset["playback_url"])
    replayed = client.post(f"/v2/meetings/{meeting_id}/audio/mixed")
    assert mixed_content.status_code == 200
    assert mixed_content.content.startswith(b"RIFF")
    assert replayed.status_code == 201
    assert replayed.json()["asset"]["asset_id"] == asset["asset_id"]
    assert len(app.state.v2_persistence.list_recording_derivations(meeting_id)) == 1

    deleted = client.delete(
        f"/v2/meetings/{meeting_id}?scope=recording",
        headers={"Idempotency-Key": "next002-recording-owner"},
    )
    assert deleted.status_code == 200, deleted.text
    assert app.state.v2_persistence.meeting_exists(meeting_id) is True
    assert app.state.v2_persistence.list_recording_sessions(meeting_id) == []
    assert app.state.v2_persistence.list_recording_derivations(meeting_id) == []
    assert not (tmp_path / "audio_assets" / meeting_id).exists()


def test_mixed_api_refuses_to_hide_a_failed_source_track(dual_track_app):
    client, app = dual_track_app
    meeting_id = "dual-failed"
    persistence = app.state.v2_persistence
    persistence.create_meeting(meeting_id=meeting_id, title="失败双轨", now_ms=1_000)
    for track_id, source_type in (
        ("microphone", "browser_live_mic"),
        ("system_audio", "macos_system_audio"),
    ):
        persistence.begin_recording(
            meeting_id=meeting_id,
            track=track_id,
            epoch=0,
            source_type=source_type,
            sample_rate_hz=16_000,
            lease_owner=f"owner-{track_id}",
            lease_ms=10_000,
            now_ms=1_100,
        )
    persistence.fail_recording_track(
        meeting_id=meeting_id,
        track="system_audio",
        epoch=0,
        error_class="screen_capture_permission_denied",
        now_ms=1_200,
    )

    listing = client.get(f"/v2/meetings/{meeting_id}/audio")
    mixed = client.post(f"/v2/meetings/{meeting_id}/audio/mixed")

    system_state = next(
        item for item in listing.json()["track_states"] if item["track_id"] == "system_audio"
    )
    assert system_state["status"] == "failed"
    assert system_state["error_class"] == "screen_capture_permission_denied"
    assert mixed.status_code == 409
    assert mixed.json()["detail"]["code"] == "audio_tracks_not_ready"
    assert mixed.json()["detail"]["tracks"]["system_audio"]["status"] == "failed"


def test_mixed_api_does_not_expose_storage_paths_on_derivation_failure(
    dual_track_app, tmp_path: Path, monkeypatch
):
    client, app = dual_track_app
    meeting_id = "dual-redacted-error"
    app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id, title="脱敏失败", now_ms=1_000
    )
    for track_id, source_type, value in (
        ("microphone", "browser_live_mic", 0.1),
        ("system_audio", "macos_system_audio", -0.1),
    ):
        _seed_ready_track(
            app,
            data_dir=tmp_path,
            meeting_id=meeting_id,
            track_id=track_id,
            source_type=source_type,
            value=value,
            started_at_ms=10_000,
        )
    monkeypatch.setattr(
        "meeting_copilot_web_mvp.app.audio_assets.derive_local_mixed_wav_asset",
        lambda **_kwargs: (_ for _ in ()).throw(
            OSError("disk write failed at /Users/private/meeting.wav")
        ),
    )

    response = client.post(f"/v2/meetings/{meeting_id}/audio/mixed")

    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "mixed_audio_derivation_failed"}
    assert "/Users/" not in response.text

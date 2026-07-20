from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app


@pytest.fixture(autouse=True)
def _force_fake_recognizer(monkeypatch) -> None:
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda _session_id: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda _session_id: None)


def test_meeting_preparation_binds_local_hotwords_and_deletes_with_meeting(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.put(
            "/v2/meetings/meeting-prepared/preparation",
            json={
                "hotwords": ["P99", "checkout-service"],
                "input_source": "microphone",
                "input_device_id": "mic-1",
                "input_device_name": "MacBook Microphone",
                "notice_acknowledged": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["hotwords"] == ["P99", "checkout-service"]
        assert asr_stream.session_hotwords("meeting-prepared") == (
            "P99",
            "checkout-service",
        )

        assert client.post(
            "/v2/meetings",
            json={"meeting_id": "meeting-prepared"},
        ).status_code == 201
        assert client.delete("/v2/meetings/meeting-prepared").status_code == 200

    assert not (tmp_path / "meeting_preparation" / "meeting-prepared.json").exists()
    assert asr_stream.session_hotwords("meeting-prepared") == ()


def test_meeting_preparation_rejects_unsupported_capture_source(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.put(
            "/v2/meetings/meeting-source/preparation",
            json={"hotwords": [], "input_source": "mixed"},
        )

    assert response.status_code == 422
    assert "input_source must be" in response.json()["detail"]


def test_meeting_preparation_accepts_packaged_system_audio_without_mixed_mode(
    tmp_path,
) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.put(
            "/v2/meetings/meeting-system-audio/preparation",
            json={
                "hotwords": ["ScreenCaptureKit"],
                "input_source": "system_audio",
                "input_device_id": None,
                "input_device_name": "系统音频",
                "notice_acknowledged": True,
            },
        )
        reloaded = client.get(
            "/v2/meetings/meeting-system-audio/preparation"
        )

    assert response.status_code == 200
    assert response.json()["input_source"] == "system_audio"
    assert reloaded.json()["input_source"] == "system_audio"


def test_meeting_preparation_persists_packaged_dual_track_identity(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        response = client.put(
            "/v2/meetings/meeting-dual-track/preparation",
            json={
                "hotwords": ["ScreenCaptureKit", "AVAudioEngine"],
                "input_source": "dual_track",
                "input_device_id": None,
                "input_device_name": "麦克风 + 系统音频",
                "notice_acknowledged": True,
            },
        )
        reloaded = client.get("/v2/meetings/meeting-dual-track/preparation")

    assert response.status_code == 200
    assert response.json()["input_source"] == "dual_track"
    assert reloaded.status_code == 200
    assert reloaded.json()["input_source"] == "dual_track"


@pytest.mark.parametrize("saved_acknowledgement", [None, False])
def test_existing_v2_meeting_stream_requires_recording_notice_acknowledgement(
    tmp_path,
    saved_acknowledgement,
) -> None:
    app = create_app(data_dir=tmp_path, allow_fake_asr_fallback=True)
    meeting_id = "meeting-notice-required"

    with TestClient(app) as client:
        created = client.post(
            "/v2/meetings",
            json={"meeting_id": meeting_id, "title": "治理门禁测试"},
        )
        assert created.status_code == 201
        if saved_acknowledgement is not None:
            preparation = client.put(
                f"/v2/meetings/{meeting_id}/preparation",
                json={
                    "hotwords": [],
                    "input_source": "microphone",
                    "notice_acknowledged": saved_acknowledgement,
                },
            )
            assert preparation.status_code == 200

        with client.websocket_connect(
            f"/live/asr/stream/ws/{meeting_id}?audio_source=browser_live_mic"
        ) as websocket:
            websocket.send_text("END")
            error = json.loads(websocket.receive_text())

        assert error["event_type"] == "provider_error"
        assert error["error_code"] == "recording_notice_required"
        assert error["recording_saved"] is False
        assert app.state.v2_persistence.list_recording_sessions(meeting_id) == []
        assert app.state.v2_persistence.list_audio_chunks(meeting_id) == []
        snapshot = app.state.v2_persistence.get_snapshot(meeting_id)
        assert snapshot["segments"] == []
        assert snapshot["jobs"] == []
        with pytest.raises(KeyError):
            app.state.asr_live_repository.get(meeting_id)


def test_existing_v2_meeting_stream_starts_after_recording_notice_acknowledgement(
    tmp_path,
) -> None:
    app = create_app(data_dir=tmp_path, allow_fake_asr_fallback=True)
    meeting_id = "meeting-notice-acknowledged"

    with TestClient(app) as client:
        assert client.post(
            "/v2/meetings",
            json={"meeting_id": meeting_id, "title": "已确认录音告知"},
        ).status_code == 201
        assert client.put(
            f"/v2/meetings/{meeting_id}/preparation",
            json={
                "hotwords": ["P99"],
                "input_source": "microphone",
                "notice_acknowledged": True,
            },
        ).status_code == 200

        with client.websocket_connect(
            f"/live/asr/stream/ws/{meeting_id}?audio_source=browser_live_mic"
        ) as websocket:
            websocket.send_bytes(b"\x00" * 3_200)
            partial = json.loads(websocket.receive_text())
            websocket.send_text("END")
            final = json.loads(websocket.receive_text())

        assert partial["event_type"] == "partial"
        assert final["event_type"] == "final"
        assert app.state.v2_persistence.list_recording_sessions(meeting_id)
        assert app.state.v2_persistence.list_audio_chunks(meeting_id)
        assert app.state.v2_persistence.get_snapshot(meeting_id)["segments"]


def test_stream_without_existing_v2_meeting_keeps_legacy_focused_behavior(
    tmp_path,
) -> None:
    app = create_app(data_dir=tmp_path, allow_fake_asr_fallback=True)
    meeting_id = "legacy-focused-stream"

    with TestClient(app) as client:
        assert app.state.v2_persistence.meeting_exists(meeting_id) is False
        with client.websocket_connect(
            f"/live/asr/stream/ws/{meeting_id}?audio_source=simulated_realtime_wav"
        ) as websocket:
            websocket.send_bytes(b"\x00" * 3_200)
            partial = json.loads(websocket.receive_text())
            websocket.send_text("END")
            final = json.loads(websocket.receive_text())

        assert partial["event_type"] == "partial"
        assert final["event_type"] == "final"
        assert app.state.v2_persistence.get_snapshot(meeting_id)["segments"]

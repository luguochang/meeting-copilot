import json
import struct

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.degradation_controller import (
    LEVEL_HEAVY,
    LEVEL_LIGHT,
    LEVEL_MODERATE,
    LEVEL_STOP,
    get_degradation_controller,
)


@pytest.fixture(autouse=True)
def _reset_degradation():
    controller = get_degradation_controller()
    controller.reset()
    yield controller
    controller.reset()


def _create_session(client: TestClient, session_id: str) -> None:
    response = client.post("/live/asr/mock/sessions", json={
        "session_id": session_id,
        "provider": "local_mock_asr",
        "streaming_events": [{
            "event_type": "final",
            "segment_id": "seg_1",
            "text": "支付网关先灰度 5%，张三负责回滚。",
            "start_ms": 0,
            "end_ms": 3000,
            "received_at_ms": 3200,
            "confidence": 0.95,
        }],
    })
    assert response.status_code == 201


def test_level_two_blocks_paid_ai_before_provider_call_and_persists_reason(
    monkeypatch,
    tmp_path,
):
    calls = 0

    class NeverCalledClient:
        def post_json(self, url, headers, body, timeout):
            nonlocal calls
            calls += 1
            raise AssertionError("provider must not be called at degradation level 2")

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: NeverCalledClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-degradation-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "degradation-model")
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "degradation_paid_ai"
    _create_session(client, session_id)
    get_degradation_controller().set_level(LEVEL_MODERATE, "provider_unstable")

    response = client.post(
        f"/live/asr/demo/sessions/{session_id}/minutes",
        json={"mode": "enabled"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "llm_disabled_by_degradation"
    assert calls == 0
    session = client.get(f"/live/asr/sessions/{session_id}/events").json()
    assert "degradation_level_2_paid_ai_disabled" in session["degradation_reasons"]


def test_level_two_blocks_auto_suggestion_before_provider_and_exposes_policy(
    monkeypatch,
    tmp_path,
):
    calls = 0

    class NeverCalledClient:
        def post_json(self, url, headers, body, timeout):
            nonlocal calls
            calls += 1
            raise AssertionError("auto suggestion provider must be gated at level 2")

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: NeverCalledClient())
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "degradation_auto_suggestion"
    _create_session(client, session_id)
    get_degradation_controller().set_level(LEVEL_MODERATE, "provider_unstable")

    response = client.post(
        f"/live/asr/sessions/{session_id}/auto-suggestions/run-once"
    )
    status = client.get(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status"
    ).json()["status"]

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "llm_disabled_by_degradation"
    assert calls == 0
    assert status["effective_policy"]["degradation_level"] == LEVEL_MODERATE
    assert status["last_suppression_reason"] == "disabled_by_degradation"


def test_auto_suggestion_status_get_refreshes_current_degradation_level(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "degradation_status_refresh"
    _create_session(client, session_id)

    initial = client.get(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status"
    )
    get_degradation_controller().set_level(LEVEL_LIGHT, "high_confidence_only")
    refreshed = client.get(
        f"/live/asr/sessions/{session_id}/auto-suggestions/status"
    )

    assert initial.status_code == 200
    assert initial.json()["status"]["effective_policy"]["degradation_level"] == 0
    assert refreshed.status_code == 200
    assert (
        refreshed.json()["status"]["effective_policy"]["degradation_level"]
        == LEVEL_LIGHT
    )
    assert (
        refreshed.json()["status"]["effective_policy"][
            "effective_confidence_threshold"
        ]
        == 0.9
    )
    assert refreshed.json()["status"]["last_evaluated_at_ms"] == 0
    assert refreshed.json()["status"]["suppressed"] == []


def test_level_three_records_audio_without_running_asr(tmp_path):
    get_degradation_controller().set_level(LEVEL_HEAVY, "asr_unavailable")
    client = TestClient(create_app(data_dir=tmp_path))
    session_id = "recording_only_session"

    audio_check = client.get("/audio/check")
    assert audio_check.status_code == 200
    assert audio_check.json()["available"] is True
    assert audio_check.json()["recording_only"] is True
    assert audio_check.json()["asr_available"] is False

    with client.websocket_connect(
        f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic"
    ) as websocket:
        state = json.loads(websocket.receive_text())
        assert state["event_type"] == "recording_only"
        websocket.send_bytes(struct.pack("<f", 0.1) * 4_800)
        websocket.send_text("END")

    session = client.get(f"/live/asr/sessions/{session_id}/events")
    assert session.status_code == 200
    body = session.json()
    assert body["audio"]["saved"] is True
    assert body["audio"]["duration_ms"] == 300
    assert body["event_source"]["final_count"] == 0
    assert "degradation_level_3_recording_only" in body["degradation_reasons"]


def test_level_four_blocks_recording_with_actionable_websocket_error(tmp_path):
    get_degradation_controller().set_level(LEVEL_STOP, "audio_capture_failed")
    client = TestClient(create_app(data_dir=tmp_path))

    audio_check = client.get("/audio/check")
    assert audio_check.status_code == 200
    assert audio_check.json()["available"] is False
    assert audio_check.json()["recording_only"] is False

    with client.websocket_connect("/live/asr/stream/ws/blocked_recording") as websocket:
        error = json.loads(websocket.receive_text())
        assert error["event_type"] == "provider_error"
        assert error["error_code"] == "recording_unavailable"
        assert error["degradation_level"] == LEVEL_STOP

    assert client.get("/live/asr/sessions/blocked_recording/events").status_code == 404


def test_successful_asr_ready_recovers_sticky_asr_degradation():
    controller = get_degradation_controller()
    controller.set_level(LEVEL_HEAVY, "asr_sidecar_crashed: exit_code=1")

    controller.recover("asr_ready")

    assert controller.level == 0
    assert controller.reason == ""
    assert controller.to_status_dict()["can_call_llm"] is True


def test_asr_recovery_does_not_clear_unrelated_degradation():
    controller = get_degradation_controller()
    controller.set_level(LEVEL_MODERATE, "provider_unstable")

    controller.recover("asr_ready")

    assert controller.level == LEVEL_MODERATE
    assert controller.reason == "provider_unstable"

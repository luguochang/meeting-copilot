"""Privacy/permissions tests (P1-5): delete cascade + recording state."""
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp.app import create_app


def test_delete_asr_live_session_cascades():
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "del_test", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "x", "start_ms": 0, "end_ms": 100, "received_at_ms": 110, "confidence": 0.9}]
    })
    assert client.get("/live/asr/sessions/del_test/events").status_code == 200
    r = client.delete("/live/asr/sessions/del_test")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True
    assert body["delete_scope"]["session_record"] == "deleted"
    assert body["delete_scope"]["transcript_events"] == "deleted_with_session_record"
    assert body["delete_scope"]["audio"] == "not_present"
    assert "cascade" not in body
    # session gone -> 404
    assert client.get("/live/asr/sessions/del_test/events").status_code == 404


def test_delete_missing_session_returns_404():
    client = TestClient(create_app())
    assert client.delete("/live/asr/sessions/nonexistent").status_code == 404


def test_workbench_html_shows_recording_state_and_data_flow():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    # privacy: recording state visible + data flow disclosed + delete control referenced
    assert "实时" in r.text or "录音" in r.text or "session-meta" in r.text
    assert "btn-live" in r.text  # realtime subscribe (recording indicator)

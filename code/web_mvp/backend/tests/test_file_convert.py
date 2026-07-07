"""Tests for meeting-recording file conversion (G1)."""
from io import BytesIO
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import batch_transcribe, llm_service
from meeting_copilot_web_mvp.app import create_app


def test_transcribe_file_session_creates_session_from_audio(monkeypatch):
    # mock the slow FunASR batch subprocess
    monkeypatch.setattr(batch_transcribe, "transcribe_file", lambda p: "先灰度 5%。谁负责回滚？")
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: True)

    client = TestClient(create_app())
    r = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(b"fake-audio"), "audio/wav")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "local_funasr_batch"
    assert "灰度" in body["transcript"]
    sid = body["session_id"]
    assert sid.startswith("file_")

    # session queryable
    ev = client.get(f"/live/asr/sessions/{sid}/events")
    assert ev.status_code == 200
    assert ev.json()["events"], "no events persisted from file conversion"

    # llm-execution-runs works on the file-converted session (mock LLM)
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}], "usage": {"total_tokens": 50}}

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    cards = client.post(f"/live/asr/sessions/{sid}/llm-execution-runs", json={"mode": "enabled"})
    assert cards.status_code == 200
    assert cards.json()["run_count"] >= 1


def test_transcribe_file_session_unavailable_when_funasr_missing(monkeypatch):
    monkeypatch.setattr(batch_transcribe, "is_available", lambda: False)
    client = TestClient(create_app())
    r = client.post(
        "/live/asr/transcribe-file/sessions",
        files={"file": ("meeting.wav", BytesIO(b"x"), "audio/wav")},
    )
    assert r.status_code == 422
    assert "unavailable" in r.json()["detail"]

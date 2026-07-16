"""Tests for G3 (audio check), G4 (engineering-context gate), G5 (JSON minutes)."""
import json
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


def test_g3_audio_check_returns_devices_and_status():
    client = TestClient(create_app())
    r = client.get("/audio/check")
    assert r.status_code == 200
    body = r.json()
    assert "mic_available" in body
    assert "mic_devices" in body
    assert "funasr_available" in body
    assert "sherpa_available" in body
    assert "llm_configured" in body


def test_g4_non_engineering_text_produces_no_suggestion_candidates():
    client = TestClient(create_app())
    # non-engineering text that still triggers a state spec (谁负责 -> action/question)
    client.post("/live/asr/mock/sessions", json={
        "session_id": "g4_noneng", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "谁负责订餐？", "start_ms": 0, "end_ms": 1000, "received_at_ms": 1100, "confidence": 0.9}]
    })
    events = client.get("/live/asr/sessions/g4_noneng/events").json()["events"]
    candidates = [e for e in events if e["event_type"] == "suggestion_candidate_event"]
    assert candidates == [], f"non-engineering meeting produced engineering candidates: {candidates}"


def test_g4_engineering_text_produces_suggestion_candidates():
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "g4_eng", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "谁负责回滚？", "start_ms": 0, "end_ms": 1000, "received_at_ms": 1100, "confidence": 0.9}]
    })
    events = client.get("/live/asr/sessions/g4_eng/events").json()["events"]
    candidates = [e for e in events if e["event_type"] == "suggestion_candidate_event"]
    assert candidates, "engineering meeting should produce suggestion candidates"


def test_g5_minutes_json_returns_structured_json_and_persists_across_restart(monkeypatch, tmp_path):
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": json.dumps({
                "background": "灰度发布评审", "decisions": ["灰度 5%"],
                "action_items": [{"item": "补测试用例", "owner": "张三", "deadline": "下周三"}],
                "risks": ["rollback 负责人未确认"], "open_questions": ["P99 阈值"],
                "evidence_quotes": ["回滚"],
            }, ensure_ascii=False)}}], "usage": {"total_tokens": 100}}

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/live/asr/mock/sessions", json={
        "session_id": "g5_json", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    r = client.post("/live/asr/demo/sessions/g5_json/minutes.json", json={"mode": "enabled"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is False
    minutes = body["minutes"]
    assert minutes["background"] == "灰度发布评审"
    assert "灰度 5%" in minutes["decisions"]
    assert minutes["action_items"][0]["owner"] == "张三"
    assert "rollback" in minutes["risks"][0]
    assert body["llm_usage"]["total_tokens"] == 100

    reopened = TestClient(create_app(data_dir=tmp_path))
    restored = reopened.get("/live/asr/sessions/g5_json/events")
    assert restored.status_code == 200
    restored_minutes = restored.json()["minutes"]
    assert restored_minutes["minutes_json"]["background"] == "灰度发布评审"
    assert restored_minutes["minutes_json_llm_usage"]["total_tokens"] == 100
    assert restored.json()["formal_derivation_status"] == "available"

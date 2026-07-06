"""Phase 3 workbench frontend tests."""
import json
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp.app import create_app


def test_workbench_route_serves_html():
    client = TestClient(create_app())
    r = client.get("/workbench")
    assert r.status_code == 200
    assert "Meeting Copilot Workbench" in r.text
    assert "实时转写" in r.text
    assert "/static/workbench.js" in r.text


def test_workbench_js_served():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    assert "live/asr/mock/sessions" in r.text
    assert "approach-cards" in r.text


def test_workbench_full_flow_with_fake_llm(monkeypatch):
    """The API flow the workbench drives: mock session -> events -> LLM cards -> approach cards."""
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                return {"choices": [{"message": {"content": json.dumps([{"card_type": "approach.alternative", "suggestion_text": "加 50% 档", "confidence": 0.85, "trigger_reason": "灰度档位", "evidence_quote": "先灰度 5%"}])}}], "usage": {"total_tokens": 90}}
            return {"choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}

    fake = FakeClient()
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")

    client = TestClient(create_app())
    create = client.post("/live/asr/mock/sessions", json={
        "session_id": "wb_flow", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    assert create.status_code == 201

    ev = client.get("/live/asr/sessions/wb_flow/events")
    assert ev.status_code == 200 and ev.json()["events"]

    cards = client.post("/live/asr/sessions/wb_flow/llm-execution-runs", json={"mode": "enabled"})
    assert cards.status_code == 200
    assert any(r.get("card_status") == "new" for r in cards.json()["runs"])

    ap = client.post("/live/asr/sessions/wb_flow/approach-cards", json={"mode": "enabled"})
    assert ap.status_code == 200
    assert ap.json()["count"] >= 1

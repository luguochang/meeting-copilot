"""Tests for post-meeting minutes (P1-3)."""
import json
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


def _minutes_response():
    return {
        "choices": [{"message": {"content": json.dumps({
            "background": "支付服务灰度发布评审",
            "decisions": ["灰度 5% 起步"],
            "action_items": [{"item": "补兼容性测试用例", "owner": "张三", "deadline": "下周三"}],
            "risks": ["rollback 负责人未确认"],
            "open_questions": ["P99 阈值多少"],
            "evidence_quotes": ["先灰度 5%", "谁负责回滚"],
        }, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 150, "completion_tokens": 60, "total_tokens": 210},
    }


def test_build_minutes_to_markdown_with_evidence():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    md, usage, degraded = llm_service.build_minutes("转写", config, client=FakeClient())
    assert degraded is False
    assert "# 会议纪要" in md
    assert "## 已确认决策" in md
    assert "灰度 5% 起步" in md
    assert "## 行动项" in md and "张三" in md and "下周三" in md
    assert "## 风险" in md and "rollback 负责人未确认" in md
    assert "## 未闭环问题" in md and "P99 阈值多少" in md
    assert "## 证据片段" in md and "先灰度 5%" in md
    assert usage["total_tokens"] == 210


def test_minutes_endpoint_returns_markdown(monkeypatch):
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_test", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    r = client.post("/live/asr/sessions/minutes_test/minutes", json={"mode": "enabled"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is False
    assert "# 会议纪要" in body["minutes_md"]
    assert "证据片段" in body["minutes_md"]
    assert body["llm_usage"]["total_tokens"] == 210


def test_minutes_endpoint_degrades_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            raise RuntimeError("502")

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FailingClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_deg", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "x", "start_ms": 0, "end_ms": 100, "received_at_ms": 110, "confidence": 0.9}]
    })
    r = client.post("/live/asr/sessions/minutes_deg/minutes", json={"mode": "enabled"})
    assert r.status_code == 200  # degraded, not 500
    assert r.json()["degraded"] is True

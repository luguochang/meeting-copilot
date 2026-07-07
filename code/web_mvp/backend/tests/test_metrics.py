"""Tests for metrics + config validation (P3)."""
from meeting_copilot_web_mvp import metrics as metrics_mod
from meeting_copilot_web_mvp.app import create_app


def test_metrics_inc_and_snapshot():
    m = metrics_mod.Metrics()
    m.inc("llm_calls", 3)
    m.inc("cards_created", 2)
    snap = m.snapshot()
    assert snap["llm_calls"] == 3
    assert snap["cards_created"] == 2


def test_metrics_observe_latency():
    m = metrics_mod.Metrics()
    m.observe("asr_latency", 100.0)
    m.observe("asr_latency", 200.0)
    snap = m.snapshot()
    assert snap["asr_latency_count"] == 2
    assert snap["asr_latency_sum_ms"] == 300.0
    assert snap["asr_latency_max_ms"] == 200.0
    assert snap["asr_latency_avg_ms"] == 150.0


def test_metrics_endpoint_exposed():
    client = create_app().__call__  # sanity
    from fastapi.testclient import TestClient
    c = TestClient(create_app())
    r = c.get("/metrics")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_validate_config_detects_missing_llm_env(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    issues = metrics_mod.validate_config()
    assert any("LLM_GATEWAY_BASE_URL" in i for i in issues)
    assert any("LLM_GATEWAY_API_KEY" in i for i in issues)


def test_metrics_incremented_on_llm_execution(monkeypatch):
    from meeting_copilot_web_mvp import llm_service
    from fastapi.testclient import TestClient

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": '{"suggestion_text":"ok","confidence":0.8,"trigger_reason":"x"}'}}], "usage": {"total_tokens": 10}}

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    before = metrics_mod.metrics.snapshot().get("llm_calls", 0)
    c = TestClient(create_app())
    c.post("/live/asr/mock/sessions", json={
        "session_id": "metrics_test", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    c.post("/live/asr/sessions/metrics_test/llm-execution-runs", json={"mode": "enabled"})
    after = metrics_mod.metrics.snapshot().get("llm_calls", 0)
    assert after > before

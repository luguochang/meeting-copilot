"""Tests for approach-consideration cards (Phase 2.5)."""
import json
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


class FakeClient:
    def __init__(self, response):
        self.response = response

    def post_json(self, url, headers, body, timeout):
        return self.response


def _config():
    return llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")


def test_build_approach_cards_applies_confidence_and_evidence_gates():
    resp = {"choices": [{"message": {"content": json.dumps([
        {"card_type": "approach.alternative", "suggestion_text": "加 50% 档", "confidence": 0.85, "trigger_reason": "灰度档位", "evidence_quote": "先灰度 5%"},
        {"card_type": "approach.consideration", "suggestion_text": "低置信", "confidence": 0.5, "trigger_reason": "x", "evidence_quote": "有"},
        {"card_type": "approach.consideration", "suggestion_text": "无证据", "confidence": 0.9, "trigger_reason": "y", "evidence_quote": ""},
    ])}}], "usage": {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}}
    cards, usage, degraded = llm_service.build_approach_cards("转写文本", _config(), client=FakeClient(resp))
    assert degraded is False
    assert len(cards) == 1  # 0.5 dropped (confidence), empty evidence dropped
    assert cards[0]["card_type"] == "approach.alternative"
    assert cards[0]["card_status"] == "new"
    assert cards[0]["confidence"] == 0.85
    assert cards[0]["evidence_quote"] == "先灰度 5%"
    assert cards[0]["llm_trace"]["prompt_version"] == llm_service.APPROACH_PROMPT_VERSION
    assert usage["total_tokens"] == 250


def test_build_approach_cards_caps_at_three():
    items = [{"card_type": "approach.consideration", "suggestion_text": f"s{i}", "confidence": 0.8, "trigger_reason": "r", "evidence_quote": "q"} for i in range(6)]
    resp = {"choices": [{"message": {"content": json.dumps(items)}}], "usage": {"total_tokens": 10}}
    cards, _, degraded = llm_service.build_approach_cards("t", _config(), client=FakeClient(resp))
    assert degraded is False
    assert len(cards) == 3  # capped at APPROACH_MAX_PER_SESSION


def test_approach_cards_endpoint_returns_cards(monkeypatch):
    resp = {"choices": [{"message": {"content": json.dumps([
        {"card_type": "approach.alternative", "suggestion_text": "是否考虑过加 50% 档", "confidence": 0.85, "trigger_reason": "灰度档位", "evidence_quote": "先灰度 5%"}
    ])}}], "usage": {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250}}
    fake = FakeClient(resp)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    create = client.post("/live/asr/mock/sessions", json={
        "session_id": "approach_test", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    assert create.status_code == 201
    r = client.post("/live/asr/sessions/approach_test/approach-cards", json={"mode": "enabled"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    card = body["approach_cards"][0]
    assert card["card_type"] == "approach.alternative"
    assert card["card_status"] == "new"
    assert card["evidence_quote"]
    assert body["llm_usage"]["total_tokens"] == 250


def test_approach_cards_endpoint_without_config_returns_422(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "approach_nocfg", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "x", "start_ms": 0, "end_ms": 100, "received_at_ms": 110, "confidence": 0.9}]
    })
    r = client.post("/live/asr/sessions/approach_nocfg/approach-cards", json={"mode": "enabled"})
    assert r.status_code == 422


def test_build_approach_cards_degrades_gracefully_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            raise RuntimeError("502 Bad Gateway")

    cards, usage, degraded = llm_service.build_approach_cards("转写", _config(), client=FailingClient())
    assert cards == []
    assert degraded is True
    assert usage["total_tokens"] == 0


def test_call_with_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)

    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def post_json(self, url, headers, body, timeout):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("503 Service unavailable")
            return {"choices": [{"message": {"content": '{"suggestion_text":"ok","confidence":0.8,"trigger_reason":"x"}'}}], "usage": {"total_tokens": 5}}

    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    preview = {"target_candidate_id": "c1", "gap_rule_id": "g", "target_type": "Risk",
               "input_summary": "s", "evidence_span_ids": [], "source_event_ids": []}
    run = llm_service.execute_candidate(preview, config, client=FlakyClient())
    assert run["run_status"] == "completed"
    assert run["card"]["suggestion_text"] == "ok"


def test_approach_cards_endpoint_returns_degraded_flag_when_llm_fails(monkeypatch):
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
        "session_id": "approach_deg", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "x", "start_ms": 0, "end_ms": 100, "received_at_ms": 110, "confidence": 0.9}]
    })
    r = client.post("/live/asr/sessions/approach_deg/approach-cards", json={"mode": "enabled"})
    assert r.status_code == 200  # degraded, not 500
    body = r.json()
    assert body["degraded"] is True
    assert body["count"] == 0

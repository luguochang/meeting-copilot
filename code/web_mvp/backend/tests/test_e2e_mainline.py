"""End-to-end mainline test — the test the 471-test suite was missing.

Proves the real mainline: ASR events -> state -> suggestion candidate -> real LLM
call -> real suggestion card that references an ACTIVE EvidenceSpan, with LLM
usage recorded. Uses a fake LLM client (no network, no real gateway).
"""
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


def _asr_live_payload(session_id: str = "e2e_mainline") -> dict:
    return {
        "session_id": session_id,
        "provider": "local_mock_asr",
        "streaming_events": [
            {"event_type": "final", "segment_id": "asr_seg_001", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.91},
            {"event_type": "final", "segment_id": "asr_seg_002", "text": "谁负责回滚？", "start_ms": 3400, "end_ms": 6100, "received_at_ms": 7000, "confidence": 0.9},
            {"event_type": "final", "segment_id": "asr_seg_003", "text": "如果错误率超过 0.1% 就回滚。", "start_ms": 6100, "end_ms": 8200, "received_at_ms": 8800, "confidence": 0.9},
        ],
    }


class _FakeLlmClient:
    def __init__(self):
        self.calls = 0

    def post_json(self, url, headers, body, timeout):
        self.calls += 1
        return {
            "choices": [{"message": {"content": '{"suggestion_text":"建议确认 rollback 负责人","confidence":0.85,"trigger_reason":"rollback owner 缺失"}'}}],
            "usage": {"prompt_tokens": 110, "completion_tokens": 35, "total_tokens": 145},
        }


def test_e2e_mainline_real_card_references_active_evidence_with_llm_usage(monkeypatch):
    fake = _FakeLlmClient()
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "test-model")

    client = TestClient(create_app())

    # 1. ingest ASR streaming events
    create = client.post("/live/asr/mock/sessions", json=_asr_live_payload())
    assert create.status_code == 201

    # 2. collect active evidence spans + suggestion candidates from the event stream
    events = client.get("/live/asr/sessions/e2e_mainline/events").json()["events"]
    evidence: dict[str, dict] = {}
    for e in events:
        if e.get("event_type") == "transcript_final":
            for span in (e.get("payload") or {}).get("evidence_spans", []):
                evidence[span["id"]] = span
    candidates = [e for e in events if e.get("event_type") == "suggestion_candidate_event"]
    assert evidence, "mainline produced no evidence spans"
    assert candidates, "mainline produced no suggestion candidates"

    # candidates must reference existing, ACTIVE evidence spans
    for c in candidates:
        for eid in c["payload"]["evidence_span_ids"]:
            assert eid in evidence, f"candidate references missing evidence {eid}"
            assert evidence[eid]["status"] == "active", f"evidence {eid} not active"

    # 3. execute real LLM -> real suggestion cards
    resp = client.post(
        "/live/asr/demo/sessions/e2e_mainline/llm-execution-runs",
        json={"mode": "enabled"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["executor_mode"] == "enabled"
    assert body["run_count"] >= 1

    # 4. each completed run yields a real card referencing active evidence + usage
    completed = [r for r in body["runs"] if r["run_status"] == "completed"]
    assert completed, "no completed LLM runs"
    for run in completed:
        card = run["card"]
        assert card["card_status"] == "new"
        assert card["suggestion_text"], "card has no suggestion text"
        assert card["evidence_span_ids"], "card references no evidence"
        for eid in card["evidence_span_ids"]:
            assert eid in evidence, f"card references missing evidence {eid}"
            assert evidence[eid]["status"] == "active", f"evidence {eid} not active"
        assert card["llm_trace"]["model"] == "test-model"
        assert card["llm_trace"]["prompt_version"] == llm_service.PROMPT_VERSION
        assert card["llm_trace"]["usage"]["total_tokens"] > 0
        assert run["llm_usage"]["total_tokens"] == 145

    # 5. the LLM was really called once per candidate
    assert fake.calls == body["run_count"]
    assert fake.calls > 0

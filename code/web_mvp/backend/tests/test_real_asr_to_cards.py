"""End-to-end connection test: real WS ASR stream -> persisted session -> LLM cards.

Proves the real mic -> ASR -> session -> LLM card pipeline is CONNECTED (was the
core gap: WS stream didn't persist to the session repo, so card flow only worked
on mock sessions). Uses a fake recognizer + fake LLM but the REAL connection path
(WS -> handle_stream -> build_asr_live_events -> asr_live_repo.create -> llm-execution-runs).
"""
import json

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import asr_stream, llm_service
from meeting_copilot_web_mvp.app import create_app


class _EngineeringFakeRecognizer:
    """Fake recognizer whose final text triggers state/candidate detection."""
    provider = "test_contract_real_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id):
        self._seq = 0

    def recognize_chunk(self, pcm: bytes):
        self._seq += 1
        return [{"event_type": "partial", "segment_id": "real_seg", "text": "先灰度", "start_ms": 0, "end_ms": 300, "confidence": 0.8}]

    def finalize(self):
        return [{"event_type": "final", "segment_id": "real_seg", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 900, "confidence": 0.9}]


def test_real_ws_asr_stream_persists_session_and_feeds_llm_cards(monkeypatch):
    # real recognizer path -> engineering fake; real LLM path -> fake client
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _EngineeringFakeRecognizer(sid))
    # skip L2 correction in the connection test (tested separately, avoids slow network)
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))

    class FakeLlmClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": '{"suggestion_text":"建议确认 rollback 负责人","confidence":0.85,"trigger_reason":"owner 缺失"}'}}], "usage": {"prompt_tokens": 90, "completion_tokens": 30, "total_tokens": 120}}

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeLlmClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")

    client = TestClient(create_app())
    sid = "real_e2e_conn"

    # 1. real WS ASR stream: send audio chunks + END
    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        ws.send_bytes(b"\x00" * 6400)
        ws.send_bytes(b"\x00" * 6400)
        ws.send_text("END")
        # drain WS events (partials + final)
        received = []
        while True:
            msg = ws.receive_text()
            received.append(json.loads(msg))
            if json.loads(msg).get("event_type") == "final":
                break

    # 2. session persisted from the REAL WS stream (not mock)
    events_resp = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()["events"]
    assert events, "no events persisted from real WS stream"
    event_types = [e["event_type"] for e in events]
    assert "transcript_final" in event_types, "real ASR final not persisted"
    assert "suggestion_candidate_event" in event_types, "candidates not built from real ASR"

    # 3. llm-execution-runs reads the REAL ASR session -> real cards
    cards_resp = client.post(f"/live/asr/sessions/{sid}/llm-execution-runs", json={"mode": "enabled"})
    assert cards_resp.status_code == 200, cards_resp.text
    body = cards_resp.json()
    assert body["run_count"] >= 1, "no candidates on real ASR session"
    run = body["runs"][0]
    assert run["run_status"] == "completed"
    assert run["card"]["card_status"] == "new"
    assert run["card"]["suggestion_text"]
    assert run["card"]["evidence_span_ids"], "real card must reference evidence from real ASR"
    assert run["llm_usage"]["total_tokens"] == 120

    # 4. minutes also work on the real ASR session
    minutes = client.post(f"/live/asr/sessions/{sid}/minutes", json={"mode": "enabled"}).json()
    assert minutes["minutes_md"]
    assert minutes["degraded"] is False

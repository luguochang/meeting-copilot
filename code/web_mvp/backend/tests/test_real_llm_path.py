"""P0-4: verify the REAL LLM code path (httpx -> real HTTP -> parse -> card).

codexai.club gateway is currently 502/503 (external outage, can't control). To
verify the real integration code path (not a fake client), this test stands up a
local OpenAI-compatible mock server and points the real HttpxLlmClient at it via
LLM_GATEWAY_BASE_URL. Real httpx HTTP call -> real response parsing -> real card.
When codexai.club recovers, the same code path works against it.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app


def _start_mock_openai_server():
    """A tiny OpenAI-compatible /v1/chat/completions server returning canned JSON."""
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            try:
                payload = json.loads(body)
            except Exception:
                payload = {}
            sys_msg = ""
            for m in payload.get("messages", []):
                if m.get("role") == "system":
                    sys_msg = m.get("content", "")
            if "方案考量" in sys_msg:
                content = json.dumps([{"card_type": "approach.alternative", "suggestion_text": "是否考虑过加 50% 档", "confidence": 0.85, "trigger_reason": "灰度档位", "evidence_quote": "先灰度 5%"}], ensure_ascii=False)
            else:
                content = json.dumps({"suggestion_text": "建议确认 rollback 负责人", "confidence": 0.82, "trigger_reason": "owner 缺失"}, ensure_ascii=False)
            resp = {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 88, "completion_tokens": 33, "total_tokens": 121},
            }
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


def test_real_llm_code_path_suggestion_cards_via_local_server(monkeypatch):
    server, port = _start_mock_openai_server()
    try:
        monkeypatch.setenv("LLM_GATEWAY_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_GATEWAY_MODEL", "mock-model")
        client = TestClient(create_app())
        create = client.post("/live/asr/mock/sessions", json={
            "session_id": "real_llm_1", "provider": "local_mock_asr",
            "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
        })
        assert create.status_code == 201
        r = client.post("/live/asr/demo/sessions/real_llm_1/llm-execution-runs", json={"mode": "enabled"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["run_count"] >= 1
        run = body["runs"][0]
        # real httpx call -> real parse -> real card
        assert run["run_status"] == "completed"
        assert run["llm_call_status"] == "called"
        assert run["card"]["card_status"] == "new"
        assert run["card"]["suggestion_text"] == "建议确认 rollback 负责人"
        assert run["card"]["llm_trace"]["model"] == "mock-model"
        assert run["llm_usage"]["total_tokens"] == 121
    finally:
        server.shutdown()


def test_real_llm_code_path_approach_cards_via_local_server(monkeypatch):
    server, port = _start_mock_openai_server()
    try:
        monkeypatch.setenv("LLM_GATEWAY_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_GATEWAY_MODEL", "mock-model")
        client = TestClient(create_app())
        client.post("/live/asr/mock/sessions", json={
            "session_id": "real_llm_2", "provider": "local_mock_asr",
            "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
        })
        r = client.post("/live/asr/demo/sessions/real_llm_2/approach-cards", json={"mode": "enabled"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["degraded"] is False
        assert body["count"] == 1
        card = body["approach_cards"][0]
        assert card["card_type"] == "approach.alternative"
        assert card["suggestion_text"] == "是否考虑过加 50% 档"
        assert card["evidence_quote"] == "先灰度 5%"
        assert body["llm_usage"]["total_tokens"] == 121
    finally:
        server.shutdown()

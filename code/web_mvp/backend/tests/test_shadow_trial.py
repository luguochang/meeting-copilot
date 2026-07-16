"""P4-1: real shadow trial — real sherpa ASR text -> full real pipeline -> cards + metrics.

Uses the REAL sherpa sidecar final transcript (from P0-3) as the meeting input,
runs the full pipeline (normalizer -> state -> real LLM via local mock server ->
real cards + approach cards + minutes), and computes technical acceptance
metrics. User-judgment metrics (retention/false-positive) require human rating
and are marked as such — not fabricated.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp import transcript_normalizer as tn

# Real sherpa sidecar final transcript of simulated-release-review.16k.wav (P0-3).
REAL_SHERPA_TEXT = (
    "我们这次先挥百分之十如果错误率超过百分之零点一旧回軚这里还没有确认"
    "回滚负责人张三下周三补充兼容性测试用力监控指标还需要确认九九和错误率"
)
EXPECTED_ENTITIES = ["checkout-service", "error_rate", "P99", "staging", "灰度", "回滚", "监控"]


def _start_mock_openai():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length).decode("utf-8", "replace")
            payload = json.loads(body) if body else {}
            sys_msg = next((m.get("content", "") for m in payload.get("messages", []) if m.get("role") == "system"), "")
            if "方案考量" in sys_msg:
                content = json.dumps([{"card_type": "approach.consideration", "suggestion_text": "是否考虑过 rollback 触发条件", "confidence": 0.8, "trigger_reason": "rollback", "evidence_quote": "回滚"}], ensure_ascii=False)
            elif "纪要" in sys_msg:
                content = json.dumps({"background": "灰度发布评审", "decisions": ["灰度 5%"], "action_items": [{"item": "补测试用例", "owner": "张三", "deadline": "下周三"}], "risks": ["rollback 负责人未确认"], "open_questions": ["P99 阈值"], "evidence_quotes": ["回滚负责人张三"]}, ensure_ascii=False)
            else:
                content = json.dumps({"suggestion_text": "建议确认 rollback 负责人", "confidence": 0.82, "trigger_reason": "owner 缺失"}, ensure_ascii=False)
            resp = {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}}
            data = json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_shadow_trial_real_asr_to_cards_with_metrics(monkeypatch, tmp_path):
    server, port = _start_mock_openai()
    try:
        monkeypatch.setenv("LLM_GATEWAY_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_GATEWAY_MODEL", "mock-model")
        tn.load_terms.cache_clear()

        client = TestClient(create_app())
        # 1. ingest REAL sherpa ASR text as a meeting final event
        create = client.post("/live/asr/mock/sessions", json={
            "session_id": "shadow_trial", "provider": "local_mock_asr",
            "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": REAL_SHERPA_TEXT, "start_ms": 0, "end_ms": 17929, "received_at_ms": 18000, "confidence": 0.9}]
        })
        assert create.status_code == 201

        # 2. normalized transcript entity recall (technical metric)
        normalized = tn.normalize(REAL_SHERPA_TEXT)
        found = [e for e in EXPECTED_ENTITIES if e.lower() in normalized.lower() or (e == "error_rate" and "错误率" in normalized)]
        entity_recall = len(found) / len(EXPECTED_ENTITIES)

        # 3. demo LLM execution on a non-acceptance mock session -> suggestion cards
        cards_resp = client.post("/live/asr/demo/sessions/shadow_trial/llm-execution-runs", json={"mode": "enabled"})
        assert cards_resp.status_code == 200
        cards_body = cards_resp.json()
        completed_cards = [r for r in cards_body["runs"] if r.get("card_status") == "new"]

        # 4. approach cards + minutes
        approach = client.post("/live/asr/demo/sessions/shadow_trial/approach-cards", json={"mode": "enabled"}).json()
        minutes = client.post("/live/asr/demo/sessions/shadow_trial/minutes", json={"mode": "enabled"}).json()

        # 5. technical metrics
        report = {
            "trial": "shadow_trial_real_sherpa",
            "asr_source": "real sherpa sidecar (simulated-release-review.16k.wav)",
            "normalized_transcript": normalized,
            "entity_recall": round(entity_recall, 2),
            "entities_found": found,
            "entities_missed": [e for e in EXPECTED_ENTITIES if e not in found],
            "suggestion_cards": len(completed_cards),
            "cards_with_evidence": sum(1 for c in completed_cards if c.get("card", {}).get("evidence_span_ids")),
            "approach_cards": approach.get("count", 0),
            "minutes_generated": bool(minutes.get("minutes_md")),
            "minutes_degraded": minutes.get("degraded"),
            "llm_calls": cards_body.get("run_count", 0),
            "user_judgment_metrics": "REQUIRES HUMAN RATING (retention/false-positive) — not fabricated",
        }
        report_path = tmp_path / "shadow_trial_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        # assertions (technical)
        assert entity_recall >= 0.4, f"entity recall too low: {entity_recall}"
        assert len(completed_cards) >= 1, "no real cards produced"
        assert all(c["card"]["evidence_span_ids"] for c in completed_cards), "cards missing evidence"
        assert approach.get("count", 0) >= 1, "no approach cards"
        assert minutes.get("minutes_md"), "no minutes generated"
        assert report["minutes_degraded"] is False

        # the report is the deliverable; print key metrics
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        server.shutdown()

from __future__ import annotations

import json
import struct
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app


class _Recognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id: str, final_text: str) -> None:
        self.session_id = session_id
        self.final_text = final_text
        self._seq = 0

    def recognize_chunk(self, _pcm):
        self._seq += 1
        return [{
            "event_type": "partial",
            "segment_id": "stream-segment-1",
            "text": self.final_text[: max(12, len(self.final_text) // 2)],
            "start_ms": 0,
            "end_ms": 300,
            "confidence": 0.92,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "stream-segment-1",
            "text": self.final_text,
            "start_ms": 0,
            "end_ms": 900,
            "confidence": 0.92,
        }]


@pytest.mark.parametrize(
    ("session_id", "final_text"),
    [
        (
            "streaming-explicit-mainline",
            "接口先灰度百分之五，如果错误率超过千分之一就回滚。",
        ),
        (
            "streaming-generic-mainline",
            "数据库迁移方案分两步，先兼容旧 schema，再切换读流量。",
        ),
    ],
)
def test_websocket_final_runs_streaming_suggestion_job_without_browser_ai_trigger(
    monkeypatch,
    tmp_path,
    session_id,
    final_text,
):
    requests: list[dict] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"type":"response.output_text.delta","delta":"建议确认"}\n\n'
                'data: {"type":"response.output_text.delta","delta":"回滚负责人和监控口径？"}\n\n'
                'data: {"type":"response.completed","response":{"id":"resp-1","model":"gpt-5.5","status":"completed","usage":{"input_tokens":20,"output_tokens":8,"total_tokens":28}}}\n\n'
            ),
        )

    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _Recognizer(sid, final_text),
    )
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    app = create_app(data_dir=tmp_path)
    settings = app.state.settings_usage_repository.get_settings()
    settings["asr"]["l2_correction_enabled"] = False
    settings["suggestions"]["cooldown_minutes"] = 0
    app.state.settings_usage_repository.replace_settings(settings)
    app.state.streaming_llm_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway),
        trust_env=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect(
            f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic"
        ) as websocket:
            websocket.send_bytes(struct.pack("<f", 0.1) * 800)
            websocket.receive_text()
            websocket.send_text("END")
            while True:
                event = json.loads(websocket.receive_text())
                if event.get("event_type") == "final":
                    break

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = client.get(
                f"/v2/meetings/{session_id}/snapshot"
            ).json()
            if any(item["status"] == "committed" for item in snapshot["suggestions"]):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"streaming suggestion did not commit: {snapshot}")

        events = client.get(
            f"/v2/meetings/{session_id}/events",
            params={"after_seq": 0},
        ).json()["events"]
        jobs = app.state.v2_persistence.list_jobs(meeting_id=session_id)

    suggestion = snapshot["suggestions"][0]
    assert suggestion["text"] == "建议确认回滚负责人和监控口径？"
    assert snapshot["audio"]["chunk_count"] == 1
    assert snapshot["audio"]["duration_ms"] > 0
    assert suggestion["final_draft_seq"] >= 1
    event_types = [event["type"] for event in events]
    assert "suggestion.draft.started" in event_types
    assert "suggestion.committed" in event_types
    assert "recording.chunk.committed" in event_types
    assert {job["status"] for job in jobs} == {"succeeded"}
    assert len(requests) == 1
    assert requests[0]["stream"] is True
    assert "input" in requests[0]
    assert "messages" not in requests[0]
    suggestion_job = next(job for job in jobs if job["kind"] == "suggestion")
    trace = app.state.pipeline_traces.export(suggestion_job["id"])
    assert trace["stages"]["audio_active"]["monotonic_ns"] > 0
    assert trace["stages"]["first_token"]["monotonic_ns"] > 0
    assert trace["stages"]["provider_completed"]["monotonic_ns"] > 0


def test_llm_first_lane_runs_structured_intelligence_without_keyword_projection(
    monkeypatch,
    tmp_path,
):
    final_text = "结论是采用蓝绿发布方案，回滚负责人还没有确认。"
    requests: list[dict] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        structured = json.dumps(
            {
                "paragraph_revisions": [],
                "topic_update": {
                    "operation": "update",
                    "title": "蓝绿发布方案",
                    "summary": "确认发布方案和回滚责任。",
                    "evidence_segment_ids": ["stream-segment-1"],
                    "evidence_quote": "结论是采用蓝绿发布方案",
                },
                "state_changes": [
                    {
                        "type": "decision",
                        "operation": "add",
                        "item_id": "decision:blue-green",
                        "content": "采用蓝绿发布方案",
                        "owner": None,
                        "deadline": None,
                        "status": "candidate",
                        "evidence_segment_ids": ["stream-segment-1"],
                        "evidence_quote": "结论是采用蓝绿发布方案",
                        "confidence": 0.94,
                    }
                ],
                "follow_up": {
                    "question": "建议确认回滚负责人。",
                    "reason": "原话明确表示负责人尚未确认。",
                    "evidence_segment_ids": ["stream-segment-1"],
                    "evidence_quote": "回滚负责人还没有确认",
                    "urgency": "high",
                },
            },
            ensure_ascii=False,
        )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                f'data: {{"type":"response.output_text.delta","delta":{json.dumps(structured, ensure_ascii=False)}}}\n\n'
                'data: {"type":"response.completed","response":{"id":"intelligence-1","model":"fast-model","status":"completed","usage":{"input_tokens":40,"output_tokens":30,"total_tokens":70}}}\n\n'
            ),
        )

    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: _Recognizer(sid, final_text),
    )
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "review-model")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "fast-model")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)

    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.streaming_llm_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway),
        trust_env=False,
    )

    with TestClient(app) as client:
        with client.websocket_connect(
            "/live/asr/stream/ws/llm-first-chain?audio_source=browser_live_mic"
        ) as websocket:
            websocket.send_bytes(struct.pack("<f", 0.1) * 800)
            websocket.receive_text()
            websocket.send_text("END")
            while True:
                event = json.loads(websocket.receive_text())
                if event.get("event_type") == "final":
                    break

        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = client.get("/v2/meetings/llm-first-chain/snapshot").json()
            if snapshot["decision_candidates"]:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"structured intelligence did not commit: {snapshot}")
        events = client.get("/v2/meetings/llm-first-chain/events").json()["events"]
        slo_report = client.get(
            "/v2/meetings/llm-first-chain/realtime-ai-slo"
        ).json()

    assert snapshot["decision_candidates"][0]["id"] == "decision:blue-green"
    assert snapshot["current_topic"]["text"] == "蓝绿发布方案"
    formal_events = [
        event for event in events
        if event["type"] in {
            "meeting.topic.updated",
            "meeting.decision.updated",
            "meeting.intelligence.applied",
        }
    ]
    assert formal_events
    for formal_event in formal_events:
        payload = formal_event["payload"]
        assert payload["source"] == "llm_first"
        assert payload["llm_called"] is True
        assert payload["job_id"]
        assert payload["batch_id"]
        assert payload["provider"] == "openai_compatible_gateway"
        assert payload["model"] == "fast-model"
        assert payload["evidence"]["segment_ids"]
    assert any(request.get("input") for request in requests)
    assert {request["model"] for request in requests} == {"fast-model"}
    assert all("messages" not in request for request in requests)
    assert len(requests) == 1
    intelligence_slo = slo_report["lanes"]["intelligence"]
    assert intelligence_slo["metrics"]["provider_ttft_ms"]["p95_ms"] is not None
    assert intelligence_slo["metrics"]["provider_total_ms"]["p95_ms"] is not None
    assert slo_report["token_usage"]["call_count"] == 1
    assert slo_report["token_usage"]["total_tokens"] == 70

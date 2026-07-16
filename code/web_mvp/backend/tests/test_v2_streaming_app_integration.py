from __future__ import annotations

import json
import struct
import time

from fastapi.testclient import TestClient
import httpx

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app


class _Recognizer:
    provider = "test_contract_realtime_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = 0

    def recognize_chunk(self, _pcm):
        self._seq += 1
        return [{
            "event_type": "partial",
            "segment_id": "stream-segment-1",
            "text": "接口先灰度百分之五",
            "start_ms": 0,
            "end_ms": 300,
            "confidence": 0.92,
        }]

    def finalize(self):
        return [{
            "event_type": "final",
            "segment_id": "stream-segment-1",
            "text": "接口先灰度百分之五，如果错误率超过千分之一就回滚。",
            "start_ms": 0,
            "end_ms": 900,
            "confidence": 0.92,
        }]


def test_websocket_final_runs_streaming_suggestion_job_without_browser_ai_trigger(
    monkeypatch,
    tmp_path,
):
    requests: list[dict] = []

    def gateway(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"id":"chat-1","choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"id":"chat-1","choices":[{"delta":{"content":"建议确认"}}]}\n\n'
                'data: {"id":"chat-1","choices":[{"delta":{"content":"回滚负责人和监控口径？"},"finish_reason":"stop"}]}\n\n'
                'data: {"id":"chat-1","choices":[],"usage":{"prompt_tokens":20,"completion_tokens":8,"total_tokens":28}}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: _Recognizer(sid))
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
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
            "/live/asr/stream/ws/streaming-mainline?audio_source=browser_live_mic"
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
                "/v2/meetings/streaming-mainline/snapshot"
            ).json()
            if any(item["status"] == "committed" for item in snapshot["suggestions"]):
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"streaming suggestion did not commit: {snapshot}")

        events = client.get(
            "/v2/meetings/streaming-mainline/events",
            params={"after_seq": 0},
        ).json()["events"]
        jobs = app.state.v2_persistence.list_jobs(meeting_id="streaming-mainline")

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
    suggestion_job = next(job for job in jobs if job["kind"] == "suggestion")
    trace = app.state.pipeline_traces.export(suggestion_job["id"])
    assert trace["stages"]["audio_active"]["monotonic_ns"] > 0
    assert trace["stages"]["first_token"]["monotonic_ns"] > 0
    assert trace["stages"]["provider_completed"]["monotonic_ns"] > 0

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import asr_correct, llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


_RAW_PROVIDER_DETAIL = "gateway body at https://private.example/v1?api_key=sk-secret"


def _configure_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-5.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "team_gateway")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")


def _final_event(
    *,
    segment_id: str = "segment-1",
    text: str = "接口先灰度百分之五，确认负责人和回滚窗口。",
) -> dict[str, Any]:
    return {
        "event_type": "final",
        "segment_id": segment_id,
        "text": text,
        "normalized_text": text,
        "start_ms": 100,
        "end_ms": 900,
    }


def _live_record(meeting_id: str, event: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": meeting_id,
        "source": "live_asr_stream",
        "trace_kind": "live_event",
        "provider": "funasr_realtime",
        "provider_mode": "real",
        "is_mock": False,
        "input_source": "browser_live_mic",
        "ingest_mode": "live_asr_stream",
        "asr_fallback_used": False,
        "degradation_reasons": [],
        "events": [
            {
                "id": f"transcript_final:{event['segment_id']}",
                "event_type": "transcript_final",
                "at_ms": event["end_ms"],
                "payload": {
                    "segment_id": event["segment_id"],
                    "text": event["text"],
                    "normalized_text": event["normalized_text"],
                    "start_ms": event["start_ms"],
                    "end_ms": event["end_ms"],
                },
            }
        ],
    }


def _provider_error(kind: str) -> Exception:
    if kind == "timeout":
        error: Exception = llm_service.LlmProviderTransportError("timeout")
    elif kind == "transport":
        error = llm_service.LlmProviderTransportError("transport")
    elif kind == "502":
        error = llm_service.LlmProviderHttpError(502)
    else:  # pragma: no cover - parametrization guards this branch
        raise AssertionError(f"unknown provider error kind: {kind}")

    # The real transport classes are retryable by default. Set this test
    # failure to terminal so the durable executor can prove the final status
    # in one attempt while retaining the provider classification attributes.
    error.retryable = False  # type: ignore[attr-defined]
    error.args = (_RAW_PROVIDER_DETAIL,)
    return error


def _expected_error_code(kind: str) -> str:
    return {
        "timeout": "provider_timeout",
        "transport": "provider_transport",
        "502": "provider_502",
    }[kind]


@pytest.mark.parametrize("kind", ["timeout", "transport", "502"])
def test_realtime_correction_api_and_live_audit_keep_provider_error_provenance_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: str,
) -> None:
    _configure_llm(monkeypatch)
    provider_error = _provider_error(kind)

    def fail(_raw: str, _config: Any, **_kwargs: Any) -> Any:
        raise provider_error

    monkeypatch.setattr(asr_correct, "correct_transcript", fail)
    app = create_app(data_dir=tmp_path)
    meeting_id = f"api-provider-error-{kind}"
    event = _final_event()
    app.state.asr_live_repository.create(_live_record(meeting_id, event))

    client = TestClient(app, raise_server_exceptions=False)
    response = client.post(
        f"/live/asr/sessions/{meeting_id}/realtime-corrections/run-once",
        json={"force": True},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "error_code": "realtime_correction_provider_failed",
            "message": "Realtime correction provider request failed",
        }
    }
    assert _RAW_PROVIDER_DETAIL not in response.text
    assert "private.example" not in response.text
    assert "sk-secret" not in response.text

    events_response = client.get(f"/live/asr/sessions/{meeting_id}/events")
    assert events_response.status_code == 200
    public_events = events_response.json()
    status = public_events["realtime_transcript_correction"]
    assert status["reservation"]["status"] == "provider_failed"
    assert status["batch_audits"][-1]["error_code"] == _expected_error_code(kind)
    serialized_events = json.dumps(public_events, ensure_ascii=False)
    assert _RAW_PROVIDER_DETAIL not in serialized_events
    assert "private.example" not in serialized_events
    assert "sk-secret" not in serialized_events


@pytest.mark.parametrize("kind", ["timeout", "transport", "502"])
def test_durable_correction_job_and_snapshot_preserve_safe_provider_error_class(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    kind: str,
) -> None:
    _configure_llm(monkeypatch)
    provider_error = _provider_error(kind)

    def fail(_raw: str, _config: Any, **_kwargs: Any) -> Any:
        raise provider_error

    monkeypatch.setattr(asr_correct, "correct_transcript", fail)
    app = create_app(data_dir=tmp_path)
    meeting_id = f"durable-provider-error-{kind}"
    event = _final_event()
    app.state.asr_live_repository.create(_live_record(meeting_id, event))
    committed = app.state.commit_v2_final(meeting_id, event)
    correction_job_id = committed["job_ids"]["correction"]

    # Ending the meeting forces the correction worker to execute immediately
    # and keeps the test independent from the 15-second live batch debounce.
    app.state.v2_persistence.end_meeting(
        meeting_id=meeting_id,
        now_ms=10_000,
    )

    executor = DurableJobExecutor(
        app.state.v2_persistence,
        correction_handler=app.state.v2_correction_job_handler_impl,
        suggestion_handler=lambda job: {"job_id": job["id"]},
        worker_id=f"provider-error-{kind}",
        poll_interval_ms=5,
    )

    async def run_executor() -> None:
        await executor.start()
        try:
            async with asyncio.timeout(2):
                while app.state.v2_persistence.get_job(correction_job_id)["status"] != "failed":
                    await asyncio.sleep(0.005)
        finally:
            await executor.stop()

    asyncio.run(run_executor())

    expected_code = _expected_error_code(kind)
    durable_job = app.state.v2_persistence.get_job(correction_job_id)
    assert durable_job["status"] == "failed"
    assert durable_job["error_class"] == expected_code

    snapshot = app.state.v2_persistence.get_snapshot(meeting_id)
    snapshot_job = next(job for job in snapshot["jobs"] if job["id"] == correction_job_id)
    assert snapshot_job["status"] == "failed"
    assert snapshot_job["error_class"] == expected_code
    segment = snapshot["segments"][0]
    assert segment["correction_status"] == "failed_preserved_original"
    assert segment["correction_error_class"] == expected_code

    serialized_snapshot = json.dumps(snapshot, ensure_ascii=False)
    assert _RAW_PROVIDER_DETAIL not in serialized_snapshot
    assert "private.example" not in serialized_snapshot
    assert "sk-secret" not in serialized_snapshot

    public_job_snapshot = TestClient(app).get(f"/v2/meetings/{meeting_id}/snapshot")
    assert public_job_snapshot.status_code == 200
    assert next(
        job for job in public_job_snapshot.json()["jobs"] if job["id"] == correction_job_id
    )["error_class"] == expected_code

    app.state.v2_persistence.close()

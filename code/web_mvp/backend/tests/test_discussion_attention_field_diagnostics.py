"""Fixed subobject diagnostics through native, isolated, transport-mocked state jobs."""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from discussion_attention_fixtures import wire_packet, with_attention
from meeting_copilot_web_mvp import discussion_attention
from meeting_copilot_web_mvp.discussion_state_diagnostics import (
    sanitize_state_failure_output, state_failure_output,
)
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError
from test_discussion_state_background import state_app as state_app  # noqa: F401
from test_discussion_state_observability import run_consumer, state_response
from test_discussion_state_validation_diagnostics import queue


FIELDS = ("envelope", "focus", "observation", "object", "setting", "purpose", "opportunity")
PRIVATE = "PRIVATE_https_gateway_key_raw_model_text"


def body_for(packet, field):
    body = with_attention({"paragraph_revisions": [], "topic_update": None,
        "state_changes": [], "follow_up": None}, packet, status="none")
    value = body["current_attention"]
    if field == "envelope":
        target = value
    elif field == "focus":
        target = value["current_focus"]
    elif field == "observation":
        target = value["current_focus"]["observations"][0]
    elif field in {"object", "setting", "purpose"}:
        target = copy.deepcopy(value["current_focus"]["observations"][0])
        value["current_focus"][field] = target
    elif field == "opportunity":
        target = value["contribution_opportunity"]
    else:
        return body
    target[PRIVATE] = PRIVATE
    return body


def native_rejection(state_app, monkeypatch, field=None):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    calls = []

    async def run():
        job = await queue(app, now)
        before = p.get_current_discussion("review")

        def gateway(request):
            calls.append(request.method)
            body = body_for(wire_packet(json.loads(request.content)), field)
            return state_response(json.dumps(body, ensure_ascii=False))

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return job, before, await run_consumer(app, job)

    job, before, final = asyncio.run(run())
    assert calls == ["POST"]
    assert final["status"] == "failed" and final["error_class"] == "intelligence_validation_structural"
    assert final["attempts"] == final["max_attempts"] == 1
    assert final["output"]["provider_attempt_count"] == 1
    assert final["output"]["usage"]["total_tokens"] == 80
    ledger = list(p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id=? AND purpose='discussion_state'", ("review",)))
    assert [row[0] for row in ledger] == [80]
    assert p.get_current_discussion("review") == before
    assert not p._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (job["id"],)).fetchone()
    trace = app.state.pipeline_traces.get(job["id"]).to_dict()
    assert len(trace["execution"]["provider_attempts"]) == 1
    assert trace["execution"]["provider_attempts"][0]["outcome"] == "validation_error"
    assert trace["retry_count"] == 0
    public = next(item for item in TestClient(app).get("/v2/meetings/review/snapshot").json()["jobs"] if item["id"] == job["id"])
    assert public["failure_diagnostics"] == final["output"]["failure_diagnostics"]
    assert PRIVATE not in json.dumps(public) and PRIVATE not in json.dumps(final["output"])
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    return final["output"]["failure_diagnostics"]


@pytest.mark.parametrize("field", FIELDS)
def test_native_subobject_failure_retains_reported_usage_and_public_fixed_field(state_app, monkeypatch, field):
    diagnostic = native_rejection(state_app, monkeypatch, field)
    assert diagnostic == {"category": "structural", "field_group": "current_attention",
        "rule": "invalid_fields", "paragraph_counts": {"new": 3, "context": 0, "retrieval": 0},
        "failure_stage": "provider_result", "failure_source": "discussion_state_handler",
        "validation_phase": "current_attention", "attention_field": field}


@pytest.mark.parametrize("attribute", [PRIVATE, {"focus": PRIVATE}, ["focus", PRIVATE]])
def test_native_unknown_sensitive_error_attribute_is_not_durable_or_public(state_app, monkeypatch, attribute):
    def reject(*_args, **_kwargs):
        error = IntelligenceResponseValidationError(PRIVATE)
        error.validation_field_group = "current_attention"
        error.validation_rule = "invalid_fields"
        error.validation_attention_field = attribute
        raise error

    monkeypatch.setattr(discussion_attention, "parse_current_attention", reject)
    diagnostic = native_rejection(state_app, monkeypatch)
    assert diagnostic["validation_phase"] == "current_attention" and diagnostic["rule"] == "invalid_fields"
    assert "attention_field" not in diagnostic


def legacy_output():
    error = IntelligenceResponseValidationError("known host failure")
    error.validation_field_group = "current_attention"
    error.validation_rule = "invalid_fields"
    return state_failure_output(error, failure_stage="provider_result", validation_phase="current_attention",
        provider_attempt_count=1, paragraph_counts={"new": 3, "context": 0, "retrieval": 0}, usage={"total_tokens": 80})


@pytest.mark.parametrize("patch", [
    {"validation_phase": "core_contract"}, {"validation_phase": None},
    {"failure_stage": "provider_call", "validation_phase": None},
    {"failure_stage": "host_projection", "validation_phase": None},
    {"field_group": "state_changes"}, {"rule": "unsupported_relation"},
])
def test_known_field_cannot_cross_phase_stage_group_or_rule(patch):
    value = legacy_output()
    value["failure_diagnostics"].update(patch)
    if patch.get("validation_phase", "current_attention") is None:
        value["failure_diagnostics"].pop("validation_phase")
    expected = sanitize_state_failure_output(value)
    assert expected is not None
    value["failure_diagnostics"]["attention_field"] = "focus"
    assert sanitize_state_failure_output(value) == expected


@pytest.mark.parametrize("attribute", [PRIVATE, "scene_title", {"focus": PRIVATE}, ["focus"], None, True])
def test_sanitizer_strips_unknown_subobject_without_losing_paid_failure(attribute):
    value = legacy_output()
    value["failure_diagnostics"]["attention_field"] = attribute
    assert sanitize_state_failure_output(value) == legacy_output()


def test_legacy_exact_diagnostic_does_not_invent_subobject():
    value = legacy_output()
    assert value == {"schema_version": "v2_discussion_state_failure_output.v1", "task_mode": "discussion_state",
        "provider_attempt_count": 1, "usage": {"total_tokens": 80}, "failure_diagnostics": {
            "category": "structural", "field_group": "current_attention", "rule": "invalid_fields",
            "paragraph_counts": {"new": 3, "context": 0, "retrieval": 0},
            "failure_stage": "provider_result", "failure_source": "discussion_state_handler",
            "validation_phase": "current_attention"}}
    assert sanitize_state_failure_output(value) == value

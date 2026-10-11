"""Paid native state rejection diagnostics, entirely isolated and transport-mocked."""
from __future__ import annotations

import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from discussion_attention_fixtures import wire_packet, with_attention
from meeting_copilot_web_mvp.discussion_state_diagnostics import (
    sanitize_state_failure_output, state_failure_output,
)
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError
from test_discussion_state_background import append, state_app as state_app  # noqa: F401
from test_discussion_state_observability import run_consumer, state_response


async def queue(app, now):
    p = app.state.v2_persistence
    for seq in range(1, 4):
        append(p, seq, f"这是第{seq}段独立条件：需要确认第{seq}项操作的边界和处理顺序。", now=now - 4_000 + seq)
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    assert await app.state.refresh_due_coach_items() == 1
    return next(j for j in p.list_jobs(meeting_id="review") if j["request_context"].get("task_mode") == "discussion_state")


def content_for(packet, case):
    body = with_attention({"paragraph_revisions": [], "topic_update": None,
        "state_changes": [], "follow_up": None}, packet, status="none")
    if case == "malformed-json":
        return "{"
    if case == "malformed-fence":
        return "```json\n{}"
    if case == "top-array":
        return "[]"
    if case == "private-field":
        body["PRIVATE_RESPONSE_KEY_https_private_key"] = "PRIVATE_BODY"
    if case == "changes-object":
        body["state_changes"] = {}
    if case == "topic-object":
        body["topic_update"] = []
    if case == "topic-enum":
        body["topic_update"] = {"operation": "PRIVATE_ENUM"}
    if case == "revisions-forbidden":
        body["paragraph_revisions"] = [{}]
    if case == "missing-attention":
        body.pop("current_attention")
    value = body.get("current_attention")
    if case == "attention-fields":
        value["PRIVATE_UNKNOWN_FIELD"] = "PRIVATE_BODY"
    if case == "attention-relation":
        value["current_focus"]["relation"] = "PRIVATE_ENUM"
    if case == "attention-status":
        value["contribution_opportunity"]["status"] = "PRIVATE_ENUM"
    if case == "attention-intent":
        value["contribution_opportunity"]["intent"] = "freeform"
    if case == "attention-ref":
        value["current_focus"]["observations"][0]["evidence_segment_ids"] = ["PRIVATE_REF"]
    if case == "attention-quote":
        value["current_focus"]["observations"][0]["evidence_quote"] = "PRIVATE_NONLITERAL_QUOTE"
    if case == "focus-padding":
        source = next(item for item in packet["new_paragraphs"] if item["id"] == "s1")
        value["current_focus"]["observations"] = [{"evidence_segment_ids": ["s1", "s3"], "evidence_quote": source["text"]}]
    if case == "opportunity-id":
        value["contribution_opportunity"]["opportunity_id"] = "PRIVATE_ID"
    if case == "reason-long":
        value["contribution_opportunity"]["reason"] = "x" * 201
    if case in {"core-ref", "core-quote"}:
        body["state_changes"] = [{"type": "open_question", "operation": "add", "item_id": "q1",
            "content": "需要确认条件。", "evidence_segment_ids": ["PRIVATE_REF"] if case == "core-ref" else ["s1"],
            "evidence_quote": "PRIVATE_NONLITERAL_QUOTE", "confidence": .9}]
    if case == "valid-optional-omitted":
        body.pop("topic_update")
        body.pop("follow_up")
    text = json.dumps(body, ensure_ascii=False)
    return "```json\n" + text + "\n```" if case == "valid-fenced" else text


CASES = [
    ("malformed-json", "response_decode", "response", "invalid_json", "structural"),
    ("malformed-fence", "response_decode", "response", "invalid_fence", "structural"),
    ("top-array", "response_decode", "response", "non_object_json", "structural"),
    ("private-field", "core_contract", "response", "unsupported_fields", "structural"),
    ("changes-object", "core_contract", "state_changes", "array_required", "structural"),
    ("topic-object", "core_contract", "topic_update", "object_required", "structural"),
    ("topic-enum", "core_contract", "topic_update", "unsupported_enum", "structural"),
    ("revisions-forbidden", "core_contract", "paragraph_revisions", "paragraph_revisions_forbidden", "structural"),
    ("missing-attention", "current_attention", "current_attention", "required", "structural"),
    ("attention-fields", "current_attention", "current_attention", "invalid_fields", "structural"),
    ("attention-relation", "current_attention", "current_attention", "unsupported_relation", "structural"),
    ("attention-status", "current_attention", "current_attention", "unsupported_status", "structural"),
    ("attention-intent", "current_attention", "current_attention", "status_intent_mismatch", "structural"),
    ("attention-ref", "current_attention", "current_attention", "frozen_reference_required", "structural"),
    ("attention-quote", "current_attention", "current_attention", "quote_not_verbatim", "structural"),
    ("focus-padding", "current_attention", "current_attention", "focus_not_current", "structural"),
    ("opportunity-id", "current_attention", "current_attention", "unknown_opportunity_id", "structural"),
    ("reason-long", "current_attention", "current_attention", "reason_invalid", "structural"),
    ("core-ref", "core_contract", "state_changes", "unknown_reference", "evidence"),
    ("core-quote", "core_contract", "state_changes", "quote_not_verbatim", "evidence"),
]


@pytest.mark.parametrize("case,phase,field,rule,category", CASES)
def test_paid_native_rejection_has_precise_bounded_diagnostics(state_app, monkeypatch, case, phase, field, rule, category):
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
            return state_response(content_for(wire_packet(json.loads(request.content)), case))

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return job, before, await run_consumer(app, job)

    job, before, final = asyncio.run(run())
    assert calls == ["POST"] and final["attempts"] == final["max_attempts"] == 1
    assert final["status"] == "failed" and final["error_class"] == f"intelligence_validation_{category}"
    safe = final["output"]
    assert safe["provider_attempt_count"] == 1 and safe["usage"]["total_tokens"] == 80
    ledger = list(p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id=? AND purpose='discussion_state'", ("review",)))
    assert [row[0] for row in ledger] == [80]
    assert p.get_current_discussion("review") == before
    assert not p._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (job["id"],)).fetchone()
    assert "PRIVATE" not in json.dumps(safe)
    execution = app.state.pipeline_traces.get(job["id"]).to_dict()["execution"]
    assert len(execution["provider_attempts"]) == 1 and execution["provider_attempts"][0]["outcome"] == "validation_error"
    public = next(item for item in TestClient(app).get("/v2/meetings/review/snapshot").json()["jobs"] if item["id"] == job["id"])
    assert public["failure_diagnostics"] == safe["failure_diagnostics"]
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    diag = safe["failure_diagnostics"]
    assert (diag.get("validation_phase"), diag["field_group"], diag["rule"]) == (phase, field, rule)
    assert diag["failure_stage"] == "provider_result" and diag["category"] == category


@pytest.mark.parametrize("case", ["valid-plain", "valid-fenced", "valid-optional-omitted"])
def test_compatible_native_success_still_uses_one_paid_result(state_app, monkeypatch, case):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    calls = []

    async def run():
        job = await queue(app, now)
        def gateway(request):
            calls.append(request.method)
            return state_response(content_for(wire_packet(json.loads(request.content)), case))
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return await run_consumer(app, job)

    final = asyncio.run(run())
    assert calls == ["POST"] and final["status"] == "succeeded"
    assert final["output"]["usage"]["total_tokens"] == 80 and final["output"]["repair_attempted"] is False
    assert app.state.v2_persistence.get_current_discussion("review")["context"]["current_attention"]["covered_seq"] == 3


@pytest.mark.parametrize("failure", ["empty-content", "unknown-exception"])
def test_transport_failure_does_not_fabricate_a_parsing_phase(state_app, monkeypatch, failure):
    app, now = state_app
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    calls = []

    async def run():
        job = await queue(app, now)
        def gateway(_request):
            calls.append("POST")
            if failure == "empty-content":
                return state_response("")
            raise ValueError("PRIVATE_URL_AND_KEY")
        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return await run_consumer(app, job)

    final = asyncio.run(run())
    assert calls == ["POST"] and final["status"] == "failed"
    assert final["error_class"] == "StreamingProviderError"
    safe = final["output"]
    assert safe["provider_attempt_count"] == 1 and safe["usage"] is None
    assert safe["failure_diagnostics"]["failure_stage"] == "provider_call"
    assert "validation_phase" not in safe["failure_diagnostics"] and "PRIVATE" not in json.dumps(safe)


def legacy_output():
    return state_failure_output(IntelligenceResponseValidationError("unknown"), failure_stage="provider_result",
        provider_attempt_count=1, paragraph_counts={"new": 3, "context": 0, "retrieval": 0}, usage={"total_tokens": 80})


@pytest.mark.parametrize("phase", ["private", [], True, None])
def test_unbounded_or_non_scalar_phase_is_not_durable(phase):
    value = legacy_output()
    value["failure_diagnostics"]["validation_phase"] = phase
    assert sanitize_state_failure_output(value) is None


def test_optional_phase_keeps_legacy_output_and_strips_arbitrary_fields():
    value = legacy_output()
    assert sanitize_state_failure_output(value) == value
    modern = copy.deepcopy(value)
    modern["failure_diagnostics"].update(validation_phase="core_contract", raw_body="PRIVATE_BODY")
    expected = copy.deepcopy(modern)
    expected["failure_diagnostics"].pop("raw_body")
    assert sanitize_state_failure_output(modern) == expected
    modern["failure_diagnostics"]["failure_stage"] = "host_projection"
    assert sanitize_state_failure_output(modern) is None


def test_unknown_core_error_stays_unknown_despite_a_trusted_phase():
    error = IntelligenceResponseValidationError("PRIVATE_UNRECOGNIZED_RESPONSE")
    safe = state_failure_output(error, failure_stage="provider_result", validation_phase="core_contract",
        provider_attempt_count=1, paragraph_counts={"new": 3, "context": 0, "retrieval": 0}, usage={"total_tokens": 80})
    assert safe["failure_diagnostics"]["validation_phase"] == "core_contract"
    assert safe["failure_diagnostics"]["field_group"] == safe["failure_diagnostics"]["rule"] == "unclassified"
    assert "PRIVATE" not in json.dumps(safe)

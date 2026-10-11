"""Native contract/paid-attempt mechanics; actual model semantics are separate."""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError
from meeting_copilot_web_mvp.streaming_llm_provider import (
    ChatCompletionResult, NonStreamingProviderTimings, TokenUsage, TransportMode,
)
from test_discussion_state_background import prepare, state_app as _state_app


@pytest.fixture
def state_app(tmp_path, monkeypatch):
    yield from _state_app.__wrapped__(tmp_path, monkeypatch)


def attention(status="uncertain", relation="uncertain", quote="通知可以稍后发送"):
    return {"current_focus": {"observations": [{"evidence_segment_ids": ["s1"], "evidence_quote": quote}],
            "relation": relation, "object": None, "setting": None, "purpose": None},
        "contribution_opportunity": {"status": status, "intent": "ask_question" if status == "present" else None,
            "reason": "当前有明确待核对条件。" if status == "present" else "当前回应需求尚不确定。",
            "evidence_segment_ids": ["s1"] if status == "present" else [],
            "evidence_quote": quote if status == "present" else "", "opportunity_id": None}}


def inputs():
    return {"s1": SimpleNamespace(id="s1", text="通知可以稍后发送；这个操作会删历史吗？"),
            "old": SimpleNamespace(id="old", text="较早讨论预算优化。")}, {
        "evidence": [{"segment_id": "s1", "revision": 1, "evidence_hash": "h1"},
                     {"segment_id": "old", "revision": 1, "evidence_hash": "old-hash"}],
        "attention_contract": "discussion_attention.v1", "attention_focus_segment_ids": ["s1"],
        "discussion_context": {"current_attention": {"opportunity_id": "known-host-opportunity"}},
    }


def test_quote_observation_and_uncertain_relation_can_support_a_real_opportunity():
    from meeting_copilot_web_mvp.discussion_attention import validate_current_attention
    sources, metadata = inputs()
    value = attention("present", quote="这个操作会删历史吗？")
    result = validate_current_attention(value, paragraphs_by_id=sources, metadata=metadata)
    assert result == value
    assert result["current_focus"]["relation"] == "uncertain"
    assert result["current_focus"]["object"] is None
    assert result["contribution_opportunity"]["status"] == "present"


@pytest.mark.parametrize("pad_current_id", [False, True])
def test_current_observations_cannot_all_reference_only_an_old_topic(pad_current_id):
    from meeting_copilot_web_mvp.discussion_attention import validate_current_attention
    sources, metadata = inputs()
    value = attention("present")
    value["current_focus"]["observations"] = [{"evidence_segment_ids": ["old"], "evidence_quote": "较早讨论预算优化。"}]
    if pad_current_id:
        value["current_focus"]["observations"][0]["evidence_segment_ids"].append("s1")
    with pytest.raises(IntelligenceResponseValidationError):
        validate_current_attention(value, paragraphs_by_id=sources, metadata=metadata)


@pytest.mark.parametrize("case", ["scene-string", "scene-quote", "unknown-ref", "unfrozen-ref", "host-clock", "unknown-id", "no-anchor", "no-intent", "oversized", "unknown-field"])
def test_unsupported_or_unbound_observation_cannot_become_a_valid_attention(case):
    from meeting_copilot_web_mvp.discussion_attention import validate_current_attention
    sources, metadata = inputs()
    value = attention("present")
    if case == "scene-string":
        value["current_focus"]["setting"] = "店内展示"
    if case == "scene-quote":
        value["current_focus"]["setting"] = {"evidence_segment_ids": ["s1"], "evidence_quote": "店内展示"}
    if case == "unknown-ref":
        value["current_focus"]["observations"][0]["evidence_segment_ids"] = ["missing"]
    if case == "unfrozen-ref":
        metadata["evidence"] = []
    if case == "host-clock":
        value["covered_seq"] = 100
    if case == "unknown-id":
        value["contribution_opportunity"]["opportunity_id"] = "made-up-id"
    if case == "no-anchor":
        value["contribution_opportunity"]["evidence_segment_ids"] = []
    if case == "no-intent":
        value["contribution_opportunity"]["intent"] = None
    if case == "oversized":
        value["contribution_opportunity"]["reason"] = "x" * 201
    if case == "unknown-field":
        value["current_focus"]["summary"] = "模型补猜的活动目的"
    with pytest.raises(IntelligenceResponseValidationError):
        validate_current_attention(value, paragraphs_by_id=sources, metadata=metadata)


def test_known_opportunity_id_and_literal_known_detail_are_allowed_but_not_semantic_proof():
    from meeting_copilot_web_mvp.discussion_attention import validate_current_attention
    sources, metadata = inputs()
    value = attention("present", relation="same_topic")
    value["contribution_opportunity"]["opportunity_id"] = "known-host-opportunity"
    value["current_focus"]["purpose"] = copy.deepcopy(value["current_focus"]["observations"][0])
    assert validate_current_attention(value, paragraphs_by_id=sources, metadata=metadata) == value
    # Literal provenance alone is deliberately not a model entailment assertion.


def test_missing_legacy_attention_is_unknown_but_new_contract_requires_it():
    from meeting_copilot_web_mvp.discussion_attention import validate_current_attention
    sources, metadata = inputs()
    assert validate_current_attention(None, paragraphs_by_id=sources, metadata=metadata, required=False) is None
    with pytest.raises(IntelligenceResponseValidationError):
        validate_current_attention(None, paragraphs_by_id=sources, metadata=metadata)


class Provider:
    def __init__(self, content): self.content, self.calls = content, []

    async def complete(self, messages, **parameters):
        self.calls.append((messages, parameters))
        return ChatCompletionResult(content=self.content, transport_mode=TransportMode.NON_STREAMING,
            timings=NonStreamingProviderTimings(started_at=1.0, completed_at=1.02),
            usage=TokenUsage(50, 30, 80), response_id="attention-fake", model="synthetic-model", finish_reason="stop")


def required_job(app, now):
    job = prepare(app, now, 3)
    # Isolated DB fixture: mark the baseline request as the new mandatory contract.
    # New enqueue already supplies this marker once B's implementation lands.
    metadata = job["request_context"]
    if "attention_contract" not in metadata:
        metadata["attention_contract"] = "discussion_attention.v1"
        app.state.v2_persistence._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?",
            (json.dumps(metadata, ensure_ascii=False), job["id"]))
    return job


def core(value=None):
    result = {"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None}
    if value is not None:
        result["current_attention"] = value
    return result


@pytest.mark.parametrize("fenced", [False, True])
def test_native_state_attention_noop_commits_once_after_one_paid_call(state_app, monkeypatch, fenced):
    app, now = state_app
    job = required_job(app, now)
    value = attention()
    value["current_focus"]["observations"][0]["evidence_segment_ids"] = ["s3"]
    content = json.dumps(core(value), ensure_ascii=False)
    provider = Provider("```json\n" + content + "\n```" if fenced else content)
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)
    try:
        result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    except IntelligenceResponseValidationError as error:
        assert error.safe_failure_output["usage"]["total_tokens"] == 80
        assert len(provider.calls) == error.safe_failure_output["provider_attempt_count"] == 1
        raise
    assert len(provider.calls) == result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False and result["usage"]["total_tokens"] == 80
    stored = app.state.v2_persistence.get_current_discussion("review")["context"]["current_attention"]
    assert stored["current_focus"] == value["current_focus"]
    assert stored["contribution_opportunity"]["status"] == "uncertain"
    assert stored["covered_seq"] == 3
    assert stored["source_bindings"]
    assert result["applied"]["current_attention"] == stored
    assert result["applied"]["accepted_topic_update"] is None
    assert result["state_interpretation"]["topic_operation"] is None
    assert result["applied"]["coach_intervention"] is None
    assert json.loads(provider.calls[0][0][1]["content"])["state_task"]["attention_contract"] == "discussion_attention.v1"


@pytest.mark.parametrize("invalid", ["missing", "scene", "malformed-json"])
def test_paid_invalid_attention_never_repairs_applies_or_loses_usage(state_app, monkeypatch, invalid):
    app, now = state_app
    job = required_job(app, now)
    value = attention()
    value["current_focus"]["setting"] = "店内展示"
    content = "{" if invalid == "malformed-json" else json.dumps(core(None if invalid == "missing" else value), ensure_ascii=False)
    provider = Provider(content)
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)
    with pytest.raises(IntelligenceResponseValidationError) as raised:
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert len(provider.calls) == 1
    assert raised.value.safe_failure_output["provider_attempt_count"] == 1
    assert raised.value.safe_failure_output["usage"]["total_tokens"] == 80
    trace = app.state.pipeline_traces.get(job["id"]).to_dict()["execution"]
    assert len(trace["provider_attempts"]) == 1
    assert trace["provider_attempts"][0]["outcome"] == "validation_error"
    assert app.state.v2_persistence.get_current_discussion("review")["context"].get("last_semantic_seq", 0) == 0
    assert not app.state.v2_persistence.get_current_discussion("review")["context"].get("current_attention")


def test_native_state_to_manual_sdk_packet_keeps_the_same_frozen_attention(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    state_job = required_job(app, now)
    value = attention()
    value["current_focus"]["observations"][0]["evidence_segment_ids"] = ["s3"]
    provider = Provider(json.dumps(core(value), ensure_ascii=False))
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kw: provider)
    state_result = asyncio.run(app.state.v2_discussion_state_job_handler_impl(state_job))
    assert p.complete_job(job_id=state_job["id"], worker_id="state", now_ms=now + 1, output=state_result)
    stored = p.get_current_discussion("review")["context"]["current_attention"]
    queued = p.enqueue_user_coach_request(meeting_id="review", intent="ask_question",
        user_request="回看早先的通知方案，帮我提一个问题。", idempotency_key="attention-native-sidecar", now_ms=now + 2)
    job = p.claim_next_job(worker_id="manual", lane="pi_deep", now_ms=now + 2, lease_ms=30_000)
    assert job["id"] == queued["id"]
    packets = []

    class FakeSidecar:
        available = True

        async def evaluate(self, payload):
            packets.append(payload)
            return {"action": "silent", "reason": "历史条件暂无新增建议。",
                    "metrics": {"turns": 1, "tool_calls": 1, "tool_names": ["keep_silent"]}}

    app.state.pi_coach_runtime = FakeSidecar()
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_a, **_k: None)
    asyncio.run(app.state.v2_intelligence_job_handler_impl(job))
    assert len(packets) == 1
    packet = packets[0]["context"]
    assert packet["request_metadata"]["current_attention"] == stored
    assert packet["trigger_type"] == "user_request"
    available_ids = {item["id"] for group in ("new_paragraphs", "context_paragraphs") for item in packet[group]}
    assert all(ref in available_ids for item in stored["current_focus"]["observations"] for ref in item["evidence_segment_ids"])

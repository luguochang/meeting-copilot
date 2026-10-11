"""Actual StateOnly wire plus unchanged strict consumption; no external model calls."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json

import httpx
import pytest

from discussion_attention_fixtures import wire_packet, with_attention
from test_discussion_state_background import append, state_app as state_app  # noqa: F401
from test_discussion_state_observability import run_consumer, state_response


TEXTS = (
    "本轮评审需要确认多租户数据隔离方案，讨论历史记录的保留边界与执行条件。",
    "已经确认的方案里存在直接删除历史记录的步骤吗？",
    "没有。",
)


def response_body(packet, case):
    body = with_attention({"paragraph_revisions": [], "topic_update": None,
        "state_changes": [], "follow_up": None}, packet, status="none")
    paragraphs = {item["id"]: item for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs") for item in packet[group]}
    focus = body["current_attention"]["current_focus"]
    literal = copy.deepcopy(focus["observations"][0])
    if case == "valid-nonnull":
        for field in ("object", "setting", "purpose"):
            focus[field] = copy.deepcopy(literal)
    elif case == "summary-object":
        focus["object"] = {"summary": "用户猜测的对象"}
    elif case == "extra-object":
        focus["object"] = {**literal, "text": "多余的字段"}
    elif case == "unknown-reference":
        focus["object"] = {**literal, "evidence_segment_ids": ["not-a-frozen-source"]}
    elif case == "attention-rewrite":
        focus["observations"][0]["evidence_quote"] = "已经确认可以删除所有历史记录。"
    elif case in {"legacy-multiline", "topic-rewrite", "topic-concat"}:
        first, second = paragraphs["s1"]["text"], paragraphs["s2"]["text"]
        quote = first + "\n" + second if case == "legacy-multiline" else first + second
        if case == "topic-rewrite":
            quote = "已经确认可以删除所有历史记录。"
        body["topic_update"] = {"operation": "add", "title": "历史记录保留边界",
            "summary": "讨论已确认方案的历史记录处理边界", "evidence_segment_ids": ["s1", "s2"], "evidence_quote": quote}
    return body


def run_native(state_app, monkeypatch, case):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    captured = []

    async def run():
        for seq, text in enumerate(TEXTS, 1):
            append(p, seq, text, now=now - 4_000 + seq)
        p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
        app.state.provider_lane_registry.sync_realtime_work(set())
        assert await app.state.refresh_due_coach_items() == 1
        job = next(j for j in p.list_jobs(meeting_id="review") if j["request_context"].get("task_mode") == "discussion_state")
        before = p.get_current_discussion("review")

        def gateway(request):
            assert request.method == "POST"
            wire = json.loads(request.content)
            captured.append(wire)
            return state_response(json.dumps(response_body(wire_packet(wire), case), ensure_ascii=False))

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            return job, before, await run_consumer(app, job)

    job, before, final = asyncio.run(run())
    assert len(captured) == 1
    assert final["attempts"] == final["max_attempts"] == 1
    assert final["output"]["provider_attempt_count"] == 1 and final["output"]["usage"]["total_tokens"] == 80
    assert [r[0] for r in p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id=? AND purpose='discussion_state'", ("review",))] == [80]
    trace = app.state.pipeline_traces.get(job["id"]).to_dict()
    assert len(trace["execution"]["provider_attempts"]) == 1 and trace["retry_count"] == 0
    return app, job, before, final, captured[0]


@pytest.mark.parametrize("field", ["object", "setting", "purpose"])
def test_native_wire_exposes_nullable_exact_two_key_example(state_app, monkeypatch, field, record_property):
    _app, job, _before, final, wire = run_native(state_app, monkeypatch, "valid-nonnull")
    assert final["status"] == "succeeded" and final["output"]["repair_attempted"] is False
    packet = wire_packet(wire)
    description = packet["output_contract"]["current_attention"]["current_focus"][field]
    example = json.loads(description[description.index("{"):description.rindex("}") + 1])
    assert set(example) == {"evidence_segment_ids", "evidence_quote"}
    assert isinstance(example["evidence_segment_ids"], list) and isinstance(example["evidence_quote"], str)
    assert "null" in description and "exactly these two keys" in description
    system = wire["messages"][0]["content"]
    assert "同一正式段内连续复制" in system and "恰好只有evidence_segment_ids和evidence_quote两键" in system
    paragraphs = {p["id"]: p["text"] for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs") for p in packet[group]}
    assert paragraphs == dict(zip(("s1", "s2", "s3"), TEXTS))
    assert [p["id"] for p in packet["state_task"]["latest_focus_paragraphs"]] == job["request_context"]["attention_focus_segment_ids"] == ["s2", "s3"]
    record_property("actual_system_utf8_bytes", len(system.encode("utf-8")))
    record_property("actual_system_sha256", hashlib.sha256(system.encode("utf-8")).hexdigest())


@pytest.mark.parametrize("case", ["valid-nonnull", "short-answer-null", "legacy-multiline"])
def test_native_supported_nonnull_null_short_answer_and_legacy_multiline_remain_accepted(state_app, monkeypatch, case):
    app, _job, _before, final, _wire = run_native(state_app, monkeypatch, case)
    assert final["status"] == "succeeded" and final["output"]["repair_attempted"] is False
    attention = app.state.v2_persistence.get_current_discussion("review")["context"]["current_attention"]
    assert attention["covered_seq"] == 3
    assert "failure_diagnostics" not in final["output"]
    if case == "short-answer-null":
        assert all(attention["current_focus"][key] is None for key in ("object", "setting", "purpose"))
        assert any(item["evidence_quote"] == "没有。" for item in attention["current_focus"]["observations"])
    elif case == "valid-nonnull":
        assert all(set(attention["current_focus"][key]) == {"evidence_segment_ids", "evidence_quote"} for key in ("object", "setting", "purpose"))


@pytest.mark.parametrize("case,phase,group,rule,field", [
    ("summary-object", "current_attention", "current_attention", "invalid_fields", "object"),
    ("extra-object", "current_attention", "current_attention", "invalid_fields", "object"),
    ("unknown-reference", "current_attention", "current_attention", "frozen_reference_required", None),
    ("attention-rewrite", "current_attention", "current_attention", "quote_not_verbatim", None),
    ("topic-rewrite", "core_contract", "topic_update", "quote_not_verbatim", None),
    ("topic-concat", "core_contract", "topic_update", "quote_not_verbatim", None),
])
def test_native_invalid_fields_or_quotes_keep_existing_strict_rejection(state_app, monkeypatch, case, phase, group, rule, field):
    app, job, before, final, _wire = run_native(state_app, monkeypatch, case)
    assert final["status"] == "failed"
    diagnostic = final["output"]["failure_diagnostics"]
    assert (diagnostic["validation_phase"], diagnostic["field_group"], diagnostic["rule"], diagnostic.get("attention_field")) == (phase, group, rule, field)
    assert app.state.v2_persistence.get_current_discussion("review") == before
    assert not app.state.v2_persistence._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (job["id"],)).fetchone()

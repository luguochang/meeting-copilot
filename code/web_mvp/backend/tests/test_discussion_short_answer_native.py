"""Exercise answer-only evidence through the native handler and real host projection.

The HTTP responses are explicit offline model fixtures. These tests establish
the host contract and one-call behavior, not a model's understanding of Chinese.
Stage11.88's actual response remains the semantic failure baseline.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import IntelligenceEvidenceSuperseded
from discussion_attention_fixtures import wire_packet, with_attention


QUESTION = "存在备用方案吗？"


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-6-sol")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    now = time.time_ns() // 1_000_000
    app.state.v2_persistence.create_meeting(meeting_id="short-review", title=None, now_ms=now - 10_000)
    app.state.meeting_preparation_store.save(
        "short-review", live_analysis_profile="participation", updated_at_ms=now,
    )
    yield app, now
    app.state.v2_persistence.close()


def prepare(app, now, texts):
    p = app.state.v2_persistence
    for seq, text in enumerate(texts, 1):
        p.commit_final_and_enqueue(
            meeting_id="short-review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1_000,
            ended_at_ms=seq * 1_000 + 800, evidence_hash=f"h{seq}",
            now_ms=now - 5_000 + seq, enqueue_intelligence=False,
        )
    job = p.enqueue_discussion_state(
        meeting_id="short-review", now_ms=now, cooldown_ms=0,
        stable_ms=0, minimum_segments=1, minimum_characters=0,
    )
    assert job is not None
    running = p.claim_next_job(worker_id="short-state", lane="discussion_state", now_ms=now, lease_ms=5_000)
    assert running["id"] == job["id"]
    app.state.provider_lane_registry.sync_realtime_work(set())
    return running


def change(operation, quote, source, *, content=None, status=None):
    return {
        "type": "open_question", "operation": operation, "item_id": "backup-question",
        "content": content or quote, "status": status,
        "evidence_segment_ids": [source], "evidence_quote": quote, "confidence": 0.9,
    }


def invoke_native(app, job, changes, *, question_source="s1", on_request=None, wire_calls=None):
    calls = [] if wire_calls is None else wire_calls
    payload = json.dumps({
        "paragraph_revisions": [], "follow_up": None,
        "topic_update": {
            "operation": "add", "title": "备用方案确认", "summary": "确认是否存在备用方案",
            "evidence_segment_ids": [question_source], "evidence_quote": QUESTION,
        },
        "state_changes": changes,
    }, ensure_ascii=False)

    async def run():
        def gateway(request):
            calls.append(json.loads(request.content))
            assert request.method == "POST" and request.url.path == "/v1/responses"
            typed_payload = with_attention(payload, wire_packet(calls[-1]))
            if on_request is not None:
                on_request()
            return httpx.Response(200, json={
                "id": "synthetic-short-answer", "model": "gpt-6-sol", "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": typed_payload}]}],
                "usage": {"input_tokens": 80, "output_tokens": 60, "total_tokens": 140},
            })

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            app.state.streaming_llm_client = client
            output = await app.state.v2_discussion_state_job_handler_impl(job)
            assert not client.is_closed
            return output

    output = asyncio.run(run())
    assert len(calls) == output["provider_attempt_count"] == 1
    assert output["repair_attempted"] is False
    assert output["transport_mode"] == "non_streaming"
    assert output["usage"]["total_tokens"] == 140
    assert output["applied"]["coach_intervention"] is None
    assert output["applied"]["coach_decision"] is None
    assert calls[0]["stream"] is False and calls[0]["reasoning"] == {"effort": "low"}
    packet = json.loads(calls[0]["input"][0]["content"])
    paragraphs = packet["new_paragraphs"] + packet["context_paragraphs"] + packet["retrieval_paragraphs"]
    assert [(p["id"], p["transcript_seq"], p["revision"], p["start_ms"], p["end_ms"]) for p in paragraphs] == [
        (f"s{seq}", seq, 1, seq * 1_000, seq * 1_000 + 800) for seq in range(1, len(paragraphs) + 1)
    ]
    return output, calls


@pytest.mark.parametrize("same_segment,answer,meaning", [
    (True, "有。", "存在备用方案"),
    (False, "有。", "存在备用方案"),
    (True, "没有。", "不存在备用方案"),
    (False, "没有。", "不存在备用方案"),
])
def test_answer_only_short_reply_closes_bound_question(native_app, same_segment, answer, meaning):
    app, now = native_app
    texts = [QUESTION + answer] if same_segment else [QUESTION, answer]
    job = prepare(app, now, texts)
    output, _ = invoke_native(app, job, [
        change("add", QUESTION, "s1"),
        change("resolve", answer, "s1" if same_segment else "s2", content=meaning),
    ])
    projection = output["state_projection"]
    assert projection["accepted_operation_count"] == 2
    assert projection["rejected_operation_count"] == 0
    context = app.state.v2_persistence.get_current_discussion("short-review")["context"]
    assert context["open_points"] == []
    point = context["responded_points"][0]
    assert point["id"] == "backup-question" and point["text"] == meaning
    proof = point["resolution_proof"]
    assert proof["question_quote"] == QUESTION
    q_pos, a_pos = proof["question_position"], proof["answer_position"]
    assert a_pos["transcript_seq"] > q_pos["transcript_seq"] or (
        a_pos["segment_id"] == q_pos["segment_id"] and a_pos["start"] >= q_pos["end"]
    )
    accepted_answer = projection["accepted_operations"][1]
    assert accepted_answer["evidence_quote"] == answer
    assert accepted_answer["evidence_segment_ids"] == ["s1" if same_segment else "s2"]


def test_uncertain_reply_fixture_preserves_original_question_evidence(native_app):
    app, now = native_app
    job = prepare(app, now, [QUESTION, "暂未确定。"])
    output, _ = invoke_native(app, job, [change("add", QUESTION, "s1", status="open")])
    assert output["state_projection"]["rejected_operation_count"] == 0
    context = app.state.v2_persistence.get_current_discussion("short-review")["context"]
    assert context["responded_points"] == []
    assert len(context["open_points"]) == 1
    assert context["open_points"][0]["text"] == QUESTION
    assert context["open_points"][0]["evidence_quote"] == QUESTION
    assert context["open_points"][0]["source_refs"] == ["s1"]
    entity = app.state.v2_persistence._conn.execute(
        "SELECT status FROM meeting_entities WHERE meeting_id=? AND entity_id=?",
        ("short-review", "backup-question"),
    ).fetchone()
    assert entity["status"] == "open"


@pytest.mark.parametrize("texts,question_source,answer_quote,answer_source,rule", [
    ([QUESTION + "有。"], "s1", QUESTION + "有。", "s1", "answer_not_after_question"),
    (["有备用方案。", QUESTION], "s2", "有备用方案。", "s1", "answer_not_after_question"),
    ([QUESTION + "有。另一个补偿方案也有。"], "s1", "有。", "s1", "question_answer_quote_ambiguous"),
])
def test_unsafe_model_closure_stays_open_through_native_handler(
    native_app, texts, question_source, answer_quote, answer_source, rule,
):
    app, now = native_app
    job = prepare(app, now, texts)
    output, _ = invoke_native(app, job, [
        change("add", QUESTION, question_source),
        change("resolve", answer_quote, answer_source, content="存在备用方案"),
    ], question_source=question_source)
    projection = output["state_projection"]
    assert projection["accepted_operation_count"] == projection["rejected_operation_count"] == 1
    assert projection["rejected_operations"][0]["rule"] == rule
    assert [op["operation"] for op in output["state_interpretation"]["state_operations"]] == ["add"]
    assert [op["operation"] for op in output["declared_state_operations"]] == ["add", "resolve"]
    context = app.state.v2_persistence.get_current_discussion("short-review")["context"]
    assert context["current_problem"] == "备用方案确认"
    assert context["responded_points"] == []
    assert context["open_points"][0]["id"] == "backup-question"


def test_revision_during_short_reply_request_cannot_commit_stale_answer(native_app):
    app, now = native_app
    job = prepare(app, now, [QUESTION, "有。"])
    p = app.state.v2_persistence

    def revise():
        p.commit_transcript_revision(
            meeting_id="short-review", segment_id="s2", expected_evidence_hash="h2",
            corrected_text="没有。", revision_id="answer-corrected", now_ms=now + 1,
        )

    calls = []
    with pytest.raises(IntelligenceEvidenceSuperseded):
        invoke_native(
            app, job, [change("add", QUESTION, "s1"), change("resolve", "有。", "s2")],
            on_request=revise, wire_calls=calls,
        )
    assert len(calls) == 1
    context = p.get_current_discussion("short-review")["context"]
    assert context["responded_points"] == []
    assert p.get_job(job["id"])["status"] == "cancelled"
    lease = app.state.provider_lane_registry.try_acquire_discussion_state()
    assert lease is not None
    lease.release()

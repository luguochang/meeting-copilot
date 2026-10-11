"""Native state classification/projection, with isolated paid MockTransport only."""
from __future__ import annotations

import asyncio
import copy
import json
import time

import httpx
import pytest

from discussion_attention_fixtures import wire_packet, with_attention
from meeting_copilot_web_mvp.v2_persistence import IntelligenceProjectionError
from test_discussion_state_background import state_app as state_app  # noqa: F401
from test_discussion_state_observability import run_consumer, state_response


CANDIDATE = "本轮暂定先补充需求边界，尚未拍板。"
FACT = "目前已在做平台推广，也购买了置顶卡并邀请达人探店。"
ADOPTED = "本轮明确采用先补充需求边界的方案。"
CAPTURED_WIRES = []


@pytest.fixture
def projection_app(state_app):
    app, now = state_app
    # A real executor and its events stay on one loop across sequential jobs.
    with asyncio.Runner() as runner:
        app.state.projection_test_runner = runner
        yield app, now


def change(status, quote, source, *, item_id="fact", operation="add", kind="decision"):
    return {"type": kind, "operation": operation, "item_id": item_id, "content": quote,
            "status": status, "evidence_segment_ids": [source], "evidence_quote": quote,
            "confidence": .9}


def body(changes):
    return {"paragraph_revisions": [], "topic_update": None,
            "state_changes": changes, "follow_up": None}


def rows(p, table, *, where="meeting_id='review'"):
    return [dict(row) for row in p._conn.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY rowid")]


def decision_events(p, job_id):
    return rows(p, "meeting_events", where=f"meeting_id='review' AND type='meeting.decision.updated' AND causation_id='{job_id}'")


def queue(app, texts):
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    first = int(p._conn.execute("SELECT COALESCE(MAX(transcript_seq),0) FROM transcript_segments WHERE meeting_id='review'").fetchone()[0]) + 1
    for seq, text in enumerate(texts, first):
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=now - 5000, enqueue_intelligence=False)
    # Only this native state lane is executed. Audio/correction lanes are never started.
    p._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_other_lanes_disabled' WHERE status='pending'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now, cooldown_ms=0,
        stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert job is not None
    return job


def consume(app, monkeypatch, texts, changes, *, on_return=None, projection_failure=False):
    """Actual enqueue -> executor claim -> HTTPX provider -> parser -> transaction."""
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    p = app.state.v2_persistence
    job = queue(app, texts)
    captured = []
    before = {"discussion": copy.deepcopy(p.get_current_discussion("review")),
              "entities": rows(p, "meeting_entities")}
    original_apply = p.apply_intelligence_response

    def capture_apply(**kwargs):
        captured.append(copy.deepcopy(kwargs["response"]))
        return original_apply(**kwargs)

    original_observe = p._observe_discussion_semantics_locked

    def fail_after_semantic_write(**kwargs):
        original_observe(**kwargs)
        raise IntelligenceProjectionError("synthetic late atomic projection failure")

    calls = []

    async def run():
        def gateway(request):
            wire = json.loads(request.content)
            calls.append(wire)
            CAPTURED_WIRES.append(wire)
            packet = wire_packet(wire)
            content = with_attention(body(changes), packet, status="none")
            if on_return is not None:
                on_return(p, job)
            return state_response(json.dumps(content, ensure_ascii=False))

        with monkeypatch.context() as scoped:
            scoped.setattr(p, "apply_intelligence_response", capture_apply)
            if projection_failure:
                scoped.setattr(p, "_observe_discussion_semantics_locked", fail_after_semantic_write)
            async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
                app.state.streaming_llm_client = client
                return await run_consumer(app, job)

    final = app.state.projection_test_runner.run(run())
    assert len(calls) == 1
    assert final["attempts"] == final["max_attempts"] == 1
    if final["output"] is not None:
        assert final["output"]["provider_attempt_count"] == 1
        assert final["output"]["usage"]["total_tokens"] == 80
        assert final["output"].get("repair_attempted", False) is False
    else:
        # A real source revision revokes the running lease; it does not allow
        # the old executor to overwrite the durable cancellation with output.
        assert final["status"] == "cancelled" and final["error_class"] == "discussion_evidence_revised"
        assert p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id='review' AND purpose='discussion_state' ORDER BY rowid DESC LIMIT 1").fetchone()[0] == 80
    assert len(app.state.pipeline_traces.get(job["id"]).to_dict()["execution"]["provider_attempts"]) == 1
    return final, captured, before


def success(final):
    assert final["status"] == "succeeded", final["output"]
    return final["output"]["applied"]


def seed_candidate(app, monkeypatch):
    final, _, _ = consume(app, monkeypatch, [CANDIDATE] * 3,
        [change("candidate", CANDIDATE, "s1", item_id="proposal")])
    success(final)
    return app.state.v2_persistence.get_current_discussion("review")["context"]["current_solution"]


@pytest.mark.parametrize("operation", ["add", "update", "resolve"])
def test_native_confirmed_background_fact_stays_in_memory_without_decision_projection(projection_app, monkeypatch, operation):
    app, _ = projection_app
    p = app.state.v2_persistence
    solution = seed_candidate(app, monkeypatch)
    if operation != "add":
        seed, _, _ = consume(app, monkeypatch, [FACT] * 3, [change("confirmed", FACT, "s4")])
        success(seed)
    source = "s4" if operation == "add" else "s7"
    final, _, _ = consume(app, monkeypatch, [FACT] * 3, [change("confirmed", FACT, source, operation=operation)])
    applied = success(final)
    assert applied["state_projection"]["accepted_operation_count"] == 1
    assert applied["state_change_count"] == 0
    assert not [item for item in p.get_snapshot("review")["decision_candidates"] if item["id"] == "fact"]
    assert decision_events(p, final["id"]) == []
    context = p.get_current_discussion("review")["context"]
    assert context["current_solution"] == solution
    field = "responded_points" if operation == "resolve" else "constraints"
    fact = next(item for item in context[field] if item["id"] == "fact")
    assert fact["kind"] == "decision_fact" and fact["is_current_solution"] is False
    assert fact["status"] == ("inactive" if operation == "resolve" else "confirmed")
    assert fact["evidence"] == [{"segment_id": source, "revision": 1, "evidence_hash": f"h{source[1:]}"}]


@pytest.mark.parametrize("operation", ["add", "update"])
def test_native_explicit_adoption_projects_confirmed_without_rewriting_memory(projection_app, monkeypatch, operation):
    app, _ = projection_app
    p = app.state.v2_persistence
    if operation == "update":
        seed_candidate(app, monkeypatch)
    source = "s1" if operation == "add" else "s4"
    declaration = change("adopted", ADOPTED, source, item_id="proposal", operation=operation)
    final, captured, _ = consume(app, monkeypatch, [ADOPTED] * 3, [declaration])
    applied = success(final)
    assert applied["state_change_count"] == 1
    entity = p.get_snapshot("review")["decision_candidates"][0]
    assert entity["status"] == "confirmed"
    assert rows(p, "meeting_entities")[0]["owner"] is None and rows(p, "meeting_entities")[0]["deadline"] is None
    assert p.get_snapshot("review")["action_items"] == []
    assert captured[0]["state_changes"][0]["status"] == "adopted"
    assert final["output"]["declared_state_operations"][0]["status"] == "adopted"
    point = p.get_current_discussion("review")["context"]["constraints"][0]
    assert point["kind"] == "decision_adopted" and point["status"] == "adopted"
    assert point["is_current_solution"] is True


def test_native_undecided_proposal_remains_candidate(projection_app, monkeypatch):
    app, _ = projection_app
    seed_candidate(app, monkeypatch)
    p = app.state.v2_persistence
    assert p.get_snapshot("review")["decision_candidates"][0]["status"] == "candidate"
    context = p.get_current_discussion("review")["context"]
    assert context["constraints"][0]["kind"] == "decision_candidate"
    assert context["current_solution"] == f"候选方案（尚未宣告采用）：{CANDIDATE}"


@pytest.mark.parametrize("answer", ["是的。", "没有。"])
def test_native_factual_yes_no_answers_close_only_their_question(projection_app, monkeypatch, answer):
    app, _ = projection_app
    p = app.state.v2_persistence
    question = "目前已经安排平台推广了吗？"
    final, _, _ = consume(app, monkeypatch, [question, answer, "接下来继续核对其他背景条件。"], [
        change(None, question, "s1", item_id="q", kind="open_question"),
        change(None, answer, "s2", item_id="q", operation="resolve", kind="open_question")])
    applied = success(final)
    assert applied["state_change_count"] == 2
    assert applied["state_projection"]["rejected_operations"] == []
    snapshot = p.get_snapshot("review")
    assert snapshot["decision_candidates"] == snapshot["action_items"] == []
    context = p.get_current_discussion("review")["context"]
    assert context["open_points"] == []
    point = context["responded_points"][0]
    assert point["id"] == "q" and point["resolution_proof"]["question_quote"] == question
    assert point["evidence_quote"] == answer


@pytest.mark.parametrize("invalid", ["unknown", "quote", "revision"])
def test_native_fact_projection_rejects_invalid_or_revised_sources_atomically(projection_app, monkeypatch, invalid):
    app, _ = projection_app
    p = app.state.v2_persistence
    changes = [change("adopted", ADOPTED, "s1", item_id="proposal"), change("confirmed", FACT, "s2")]
    callback = None
    if invalid == "unknown":
        changes[1]["evidence_segment_ids"] = ["absent-source"]
    elif invalid == "quote":
        changes[1]["evidence_quote"] = "原话没有这个概括。"
    else:
        def callback(store, _job):
            assert store.commit_transcript_revision(meeting_id="review", segment_id="s2",
                expected_evidence_hash="h2", corrected_text="校对后修订了原来的背景内容。",
                revision_id="native-revised-source", now_ms=time.time_ns() // 1_000_000) is not None
    final, _, before = consume(app, monkeypatch, [ADOPTED, FACT, "继续核对实际来源。"], changes, on_return=callback)
    assert final["status"] in {"failed", "cancelled"}
    assert rows(p, "meeting_entities") == before["entities"]
    context = p.get_current_discussion("review")["context"]
    assert context["constraints"] == [] and context.get("last_semantic_seq", 0) == 0
    assert not p._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (final["id"],)).fetchone()
    assert [row[0] for row in p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id='review' AND purpose='discussion_state'")] == [80]


@pytest.mark.parametrize("human_status", ["confirmed", "dismissed"])
def test_manual_and_legacy_confirmed_decisions_are_unchanged(projection_app, monkeypatch, human_status):
    app, now = projection_app
    p = app.state.v2_persistence
    p.create_user_entity(meeting_id="review", entity_id="fact", kind="decision_candidate",
        text="用户手工保存的历史决定。", evidence=[], now_ms=now)
    p.set_entity_status(meeting_id="review", entity_id="fact", status=human_status, now_ms=now + 1)
    before = rows(p, "meeting_entities")
    final, _, _ = consume(app, monkeypatch, [FACT] * 3, [change("confirmed", FACT, "s1")])
    assert success(final)["state_change_count"] == 0
    assert rows(p, "meeting_entities") == before
    assert decision_events(p, final["id"]) == []


def test_true_non_state_legacy_confirmed_projection_is_unchanged(projection_app):
    app, now = projection_app
    p = app.state.v2_persistence
    committed = p.commit_final_and_enqueue(meeting_id="review", final_id="legacy-final", segment_id="legacy-source",
        text=FACT, normalized_text=FACT, started_at_ms=0, ended_at_ms=800, evidence_hash="legacy-hash", now_ms=now)
    job = p.get_job(committed["job_ids"]["intelligence"])
    assert job["request_context"].get("task_mode") != "discussion_state"
    applied = p.apply_intelligence_response(meeting_id="review", job_id=job["id"],
        response=body([change("confirmed", FACT, "legacy-source")]), now_ms=now + 1)
    assert applied["state_change_count"] == 1
    assert p.get_snapshot("review")["decision_candidates"][0]["status"] == "confirmed"
    assert len(decision_events(p, job["id"])) == 1


@pytest.mark.parametrize("status", ["confirmed", "adopted"])
def test_native_fact_and_adoption_apply_are_idempotent(projection_app, monkeypatch, status):
    app, _ = projection_app
    p = app.state.v2_persistence
    text = FACT if status == "confirmed" else ADOPTED
    final, captured, _ = consume(app, monkeypatch, [text] * 3, [change(status, text, "s1")])
    applied = success(final)
    before = {"discussion": copy.deepcopy(p.get_current_discussion("review")),
              "entities": rows(p, "meeting_entities"), "events": rows(p, "meeting_events")}
    repeated = p.apply_intelligence_response(meeting_id="review", job_id=final["id"],
        response=captured[0], now_ms=time.time_ns() // 1_000_000)
    assert repeated["idempotent"] and repeated["state_change_count"] == applied["state_change_count"]
    assert p.get_current_discussion("review") == before["discussion"]
    assert rows(p, "meeting_entities") == before["entities"]
    assert rows(p, "meeting_events") == before["events"]
    assert [row[0] for row in p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id='review' AND purpose='discussion_state'")] == [80]


@pytest.mark.parametrize("status", ["confirmed", "adopted"])
def test_native_late_projection_failure_rolls_back_fact_and_decision(projection_app, monkeypatch, status):
    app, _ = projection_app
    p = app.state.v2_persistence
    text = FACT if status == "confirmed" else ADOPTED
    final, _, before = consume(app, monkeypatch, [text] * 3,
        [change(status, text, "s1")], projection_failure=True)
    assert final["status"] == "failed"
    assert p.get_current_discussion("review") == before["discussion"]
    assert rows(p, "meeting_entities") == before["entities"]
    assert decision_events(p, final["id"]) == []
    assert not p._conn.execute("SELECT 1 FROM meeting_events WHERE causation_id=? AND type='meeting.intelligence.applied'", (final["id"],)).fetchone()
    assert [row[0] for row in p._conn.execute("SELECT total_tokens FROM llm_usage_ledger WHERE session_id='review' AND purpose='discussion_state'")] == [80]


@pytest.mark.parametrize("status", ["adopted", "resolved"])
def test_native_terminal_decision_projection_keeps_original_resolve_contract(projection_app, monkeypatch, status):
    app, _ = projection_app
    p = app.state.v2_persistence
    final, _, _ = consume(app, monkeypatch, [ADOPTED] * 3,
        [change(status, ADOPTED, "s1", operation="resolve")])
    assert success(final)["state_change_count"] == 1
    assert p.get_snapshot("review")["decision_candidates"][0]["status"] == status
    point = p.get_current_discussion("review")["context"]["responded_points"][0]
    assert point["status"] == "inactive" and point["is_current_solution"] is False

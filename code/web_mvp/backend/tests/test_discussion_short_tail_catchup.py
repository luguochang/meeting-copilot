"""Stable short speech gets one native semantic read after prior understanding.

No audio or external Provider. Native persistence enqueues/fences/projects the
read; only the budget boundary uses the app handler with a local mock Provider.
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.v2_persistence import V2Persistence
from test_discussion_state_background import Provider, state_app as shared_state_app
from discussion_attention_fixtures import state_response

state_app = shared_state_app


def append(p, seq, text, at, *, correction=False):
    return p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
        text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
        evidence_hash=f"h{seq}", now_ms=at, enqueue_jobs=correction,
        enqueue_intelligence=False, enqueue_answer=False)


def seed_understood(p, *, at=10_000):
    for seq in range(1, 9):
        append(p, seq, f"第{seq}项先核对通知失败时的处理边界。", at - 9000 + seq)
    queued = p.enqueue_discussion_state(meeting_id="review", now_ms=at)
    assert queued is not None
    running = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=at, lease_ms=30_000)
    assert running["id"] == queued["id"]
    p.apply_intelligence_response(meeting_id="review", job_id=running["id"], now_ms=at + 1,
        response=state_response(p, running, {"paragraph_revisions": [], "follow_up": None, "state_changes": [],
            "topic_update": {"operation": "add", "title": "通知边界", "summary": "核对失败处理",
                "evidence_segment_ids": ["s8"], "evidence_quote": "先核对通知失败时的处理边界"}}))
    assert p.complete_job(job_id=queued["id"], worker_id="state", now_ms=at + 2, output={})
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    return queued


@pytest.fixture
def store(tmp_path):
    p = V2Persistence(tmp_path / "short-tail.db", semantic_projection_mode="llm_first")
    p.create_meeting(meeting_id="review", title=None, now_ms=0)
    seed_understood(p)
    yield p
    p.close()


def state_jobs(p):
    return [job for job in p.list_jobs(meeting_id="review")
        if job["request_context"].get("task_mode") == "discussion_state"]


def assert_tail_job(p, job, anchor):
    assert job is not None and job["max_attempts"] == 1 and job["attempts"] == 0
    assert job["request_context"]["semantic_from_seq"] == 8
    assert job["request_context"]["anchor_seq"] == anchor
    assert p.discussion_request_state("review", job["request_context"]) == "valid"


def test_two_short_segments_get_one_read_at_exact_ten_second_boundary(store):
    # Public synthetic 52-character reproduction of actual91's two-segment gap.
    text9 = "当前先讨论通知失败的处理办法，稍后再确认页面的具体提示内容。"
    text10 = "还需要检查用户重复提交时是否会再次发送通知。"
    assert len(text9 + text10) == 52
    append(store, 9, text9, 11_000)
    append(store, 10, text10, 12_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=21_999) is None
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=22_000)
    assert_tail_job(store, job, 10)
    assert job["created_at_ms"] - state_jobs(store)[0]["created_at_ms"] < 60_000
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=22_001) is None
    assert len(state_jobs(store)) == 2
    claimed = store.claim_next_job(worker_id="tail", lane="discussion_state", now_ms=22_000, lease_ms=30_000)
    store.apply_intelligence_response(meeting_id="review", job_id=claimed["id"], now_ms=22_001,
        response=state_response(store, claimed, {"paragraph_revisions": [], "topic_update": None,
            "state_changes": [], "follow_up": None}, status="none"))
    assert store.complete_job(job_id=claimed["id"], worker_id="tail", now_ms=22_002, output={})
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 10
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=100_000) is None
    assert len(state_jobs(store)) == 2  # Silence is a valid semantic result, not a forced card.


@pytest.mark.parametrize("text", ["有。", "没有。", "暂时还不清楚。"])
def test_short_reply_gets_an_opportunity_without_local_semantic_dictionary(store, text):
    append(store, 9, text, 11_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=20_999) is None
    assert_tail_job(store, store.enqueue_discussion_state(meeting_id="review", now_ms=21_000), 9)


def test_earlier_unprocessed_revision_restarts_stability_even_when_latest_seq_unchanged(store):
    append(store, 9, "通知需要单独核对。", 11_000)
    append(store, 10, "继续确认。", 12_000)
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s9", expected_evidence_hash="h9",
        corrected_text="通知不需要单独核对。", revision_id="unprocessed-earlier", now_ms=16_000)
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=25_999) is None
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=26_000)
    assert_tail_job(store, job, 10)
    assert next(item["revision"] for item in job["request_context"]["evidence"] if item["segment_id"] == "s9") == 2


def test_new_final_resets_short_tail_wait_without_accumulating_jobs(store):
    append(store, 9, "有。", 11_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=20_999) is None
    append(store, 10, "补充一点。", 21_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=30_999) is None
    assert_tail_job(store, store.enqueue_discussion_state(meeting_id="review", now_ms=31_000), 10)
    assert len(state_jobs(store)) == 2


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_short_tail_waits_for_same_meeting_correction_without_stealing_its_job(store, status):
    append(store, 9, "没有。", 11_000, correction=True)
    correction = next(job for job in store.list_jobs(meeting_id="review") if job["kind"] == "correction")
    store._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, correction["id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
    assert store.get_job(correction["id"])["status"] == status and len(state_jobs(store)) == 1
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (correction["id"],))
    assert_tail_job(store, store.enqueue_discussion_state(meeting_id="review", now_ms=80_001), 9)


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_manual_request_retains_priority_over_eligible_tail(store, status):
    append(store, 9, "有。", 11_000)
    manual = store.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
        idempotency_key="manual-priority", now_ms=12_000)
    store._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, manual["id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
    assert store.get_job(manual["id"])["status"] == status
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (manual["id"],))
    assert_tail_job(store, store.enqueue_discussion_state(meeting_id="review", now_ms=80_001), 9)


@pytest.mark.parametrize("status", ["pending", "running", "retry_wait"])
def test_single_latest_tail_state_does_not_requeue_or_steal_active_work(store, status):
    append(store, 9, "有。", 11_000)
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=21_000)
    assert_tail_job(store, job, 9)
    store._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job["id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
    assert store.get_job(job["id"])["status"] == status and len(state_jobs(store)) == 2


@pytest.mark.parametrize("status,error", [("failed", "ReadTimeout"), ("cancelled", "deadline_exceeded")])
def test_same_failed_tail_fingerprint_never_retries_after_wait(store, status, error):
    append(store, 9, "有。", 11_000)
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=21_000)
    assert_tail_job(store, job, 9)
    store._conn.execute("UPDATE jobs SET status=?,error_class=? WHERE id=?", (status, error, job["id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
    assert len(state_jobs(store)) == 2


def test_first_short_input_still_never_uses_tail_bypass(tmp_path):
    p = V2Persistence(tmp_path / "initial-short.db", semantic_projection_mode="llm_first")
    try:
        p.create_meeting(meeting_id="review", title=None, now_ms=0)
        append(p, 1, "好的。", 1_000)
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None
        assert p.enqueue_discussion_state(meeting_id="review", now_ms=180_000) is None
        assert state_jobs(p) == []
    finally:
        p.close()


def test_no_new_text_material_authorization_and_ended_never_bypass(store, monkeypatch):
    from meeting_copilot_web_mvp import user_memory
    from meeting_copilot_web_mvp.discussion_persistence import invalidate_discussion_inputs

    old = store.get_current_discussion("review")["input_context"]
    replacement = {**old, "synthetic_authorization_revision": 2}
    monkeypatch.setattr(user_memory, "input_context_state", lambda _c, _m, value:
        "valid" if value == replacement else "sources_superseded")
    monkeypatch.setattr(user_memory, "capture_input_context", lambda *_args: replacement)
    with store._write_transaction():
        invalidate_discussion_inputs(store._conn, "review", 11_000)
    refresh = store.enqueue_discussion_state(meeting_id="review", now_ms=80_000)
    assert refresh is not None
    assert refresh["request_context"]["semantic_from_seq"] == 8
    assert refresh["request_context"]["anchor_seq"] == 8
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_001) is None
    running = store.claim_next_job(worker_id="refresh", lane="discussion_state", now_ms=80_010, lease_ms=5_000)
    assert running["id"] == refresh["id"]
    result = store.apply_intelligence_response(meeting_id="review", job_id=running["id"], now_ms=80_020,
        response=state_response(store, running, {"paragraph_revisions": [], "topic_update": None,
            "state_changes": [], "follow_up": None}, status="none"))
    assert store.complete_job(job_id=running["id"], worker_id="refresh", now_ms=80_030, output=result)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_040) is None
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 8
    append(store, 9, "有。", 81_000)
    store._conn.execute("UPDATE meetings SET state='ended' WHERE id='review'")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=180_000) is None
    assert len(state_jobs(store)) == 2


def test_legacy_empty_or_whitespace_fresh_row_does_not_buy_another_call(store):
    append(store, 9, "占位的公开输入。", 11_000)
    # Canonical write normally rejects empty text; legacy/corrupt rows are not an AI trigger.
    store._conn.execute("UPDATE transcript_segments SET text='   ',normalized_text='   ' WHERE segment_id='s9'")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000) is None


@pytest.mark.parametrize("texts", [["正常正文。" * 30], ["第一段补充。", "第二段补充。", "第三段补充。"]])
def test_ordinary_sufficient_batch_keeps_existing_cooldown(store, texts):
    for seq, text in enumerate(texts, 9):
        append(store, seq, text, 11_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=25_000) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=70_000) is not None


def test_compatible_existing_kwargs_and_explicit_tail_stability_override(store):
    append(store, 9, "有。", 11_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=11_499,
        cooldown_ms=60_000, stable_ms=0, minimum_segments=3, minimum_characters=120, tail_catchup_ms=500) is None
    assert_tail_job(store, store.enqueue_discussion_state(meeting_id="review", now_ms=11_500,
        cooldown_ms=60_000, stable_ms=0, minimum_segments=3, minimum_characters=120, tail_catchup_ms=500), 9)


def test_corrected_used_source_keeps_existing_two_second_fast_reanalysis(store):
    assert store.commit_transcript_revision(meeting_id="review", segment_id="s8", expected_evidence_hash="h8",
        corrected_text="本项不再需要同步等待。", revision_id="used-source-reanalysis", now_ms=11_000)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_999) is None
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=13_000)
    assert job is not None and job["request_context"]["semantic_from_seq"] == 0


def test_state_off_switch_blocks_scheduler_short_tail_without_affecting_existing_work(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    seed_understood(p, at=now - 20_000)
    append(p, 9, "有。", now - 10_000)
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert len(state_jobs(p)) == 1
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "1")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    assert_tail_job(p, state_jobs(p)[-1], 9)


def test_tail_handler_budget_gate_runs_before_any_mock_provider_call(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    seed_understood(p, at=now - 20_000)
    append(p, 9, "没有。", now - 10_000)
    job = p.enqueue_discussion_state(meeting_id="review", now_ms=now)
    assert_tail_job(p, job, 9)
    running = p.claim_next_job(worker_id="tail", lane="discussion_state", now_ms=now, lease_ms=30_000)
    settings = app.state.settings_usage_repository.get_settings()
    settings["budget"]["session_limit_cny"] = 0
    app.state.settings_usage_repository.replace_settings(settings)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "0")
    provider = Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    with pytest.raises(HTTPException) as raised:
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(running))
    assert raised.value.status_code == 429 and raised.value.detail["error"] == "llm_budget_exceeded"
    assert provider.calls == [] and app.state.settings_usage_repository.list_usage(session_id="review") == []

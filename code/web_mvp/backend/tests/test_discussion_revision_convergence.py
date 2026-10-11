"""Canonical revision coalescing uses the durable clock, without model calls."""
from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def store(tmp_path):
    p = V2Persistence(tmp_path / "convergence.db", semantic_projection_mode="llm_first")
    p.create_meeting(meeting_id="review", title=None, now_ms=0)
    for seq in range(1, 4):
        text = f"第{seq}项先确认支付后权益和通知失败的边界。"
        p.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=1000 + seq)
    yield p
    p.close()


def queue(store, now=10_000):
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=now)
    assert job is not None
    return job


def revise(store, at, *, seq=1, revision="revision"):
    segment = store._conn.execute("SELECT evidence_hash FROM transcript_segments WHERE segment_id=?", (f"s{seq}",)).fetchone()
    return store.commit_transcript_revision(meeting_id="review", segment_id=f"s{seq}",
        expected_evidence_hash=segment["evidence_hash"], corrected_text=f"{revision}：本轮先明确通知的失败重试边界。",
        revision_id=revision, now_ms=at)


def state_jobs(store):
    return [j for j in store.list_jobs(meeting_id="review")
            if j["request_context"].get("task_mode") == "discussion_state"]


def test_correction_cancelled_running_state_requeues_latest_before_old_cooldown(store):
    original = queue(store)
    assert store.claim_next_job(worker_id="old", lane="discussion_state", now_ms=10_000, lease_ms=5000)["id"] == original["id"]
    assert revise(store, 10_100)
    cancelled = store.get_job(original["id"])
    assert cancelled["status"] == "cancelled" and cancelled["error_class"] == "discussion_evidence_revised"
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_099) is None
    latest = store.enqueue_discussion_state(meeting_id="review", now_ms=12_100)
    assert latest is not None and latest["id"] != original["id"]
    assert latest["created_at_ms"] - original["created_at_ms"] < 60_000
    assert latest["max_attempts"] == 1 and latest["priority"] == original["priority"]
    assert next(e for e in latest["request_context"]["evidence"] if e["segment_id"] == "s1")["revision"] == 2
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_101) is None
    assert len([j for j in state_jobs(store) if j["status"] == "pending"]) == 1


def test_earlier_segment_revision_starts_settle_clock_even_if_anchor_is_old(store):
    queue(store)
    revise(store, 10_100, seq=1, revision="earlier")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10_101, cooldown_ms=0) is None
    revise(store, 11_000, seq=2, revision="newer")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_999, cooldown_ms=0) is None
    latest = store.enqueue_discussion_state(meeting_id="review", now_ms=13_000, cooldown_ms=0)
    assert latest is not None
    assert {e["segment_id"]: e["revision"] for e in latest["request_context"]["evidence"]} == {"s1": 2, "s2": 2, "s3": 1}


def test_sustained_revisions_show_deferred_without_unstable_provider_work(store):
    queue(store)
    revise(store, 10_100, revision="first")
    for at in range(11_100, 26_101, 1000):
        revise(store, at, revision=f"revision-{at}")
        assert store.enqueue_discussion_state(meeting_id="review", now_ms=at) is None
    capability = store.get_discussion_state_capability("review", now_ms=26_100)
    assert capability["decision"] == "not_started" and capability["understanding_status"] == "revision_deferred"
    assert capability["error_class"] == "evidence_changing"
    assert len(state_jobs(store)) == 1
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=28_100) is not None


def test_waiting_pending_and_running_reanalysis_are_distinct(store):
    queue(store)
    revise(store, 10_100)
    waiting = store.get_discussion_state_capability("review", now_ms=10_100)
    assert waiting["decision"] == "not_started" and waiting["understanding_status"] == "waiting_reanalysis" and waiting["error_class"] is None
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=12_100)
    assert job is not None
    assert store.get_discussion_state_capability("review", now_ms=12_100)["understanding_status"] == "reanalysis_queued"
    assert store.claim_next_job(worker_id="latest", lane="discussion_state", now_ms=12_100, lease_ms=5000)["id"] == job["id"]
    assert store.get_discussion_state_capability("review", now_ms=12_100)["understanding_status"] == "reanalysis_running"


def test_failed_latest_fingerprint_is_never_automatically_retried(store):
    queue(store)
    revise(store, 10_100)
    latest = store.enqueue_discussion_state(meeting_id="review", now_ms=12_100, cooldown_ms=0)
    assert latest is not None
    store._conn.execute("UPDATE jobs SET status='failed',error_class='ReadTimeout' WHERE id=?", (latest["id"],))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=80_000, cooldown_ms=0) is None
    capability = store.get_discussion_state_capability("review", now_ms=80_000)
    assert capability["decision"] == "failed" and capability["error_class"] == "provider_timeout"
    assert len(state_jobs(store)) == 2


def test_user_request_precedes_reanalysis_with_explicit_defer(store):
    queue(store)
    revise(store, 10_100)
    manual = store.enqueue_user_coach_request(meeting_id="review", user_request="请比较这两个方案。",
        intent="compare_options", idempotency_key="manual", now_ms=10_101)
    assert manual["trigger_type"] == "user_request"
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_100, cooldown_ms=0) is None
    capability = store.get_discussion_state_capability("review", now_ms=12_100)
    assert capability["decision"] == "not_started" and capability["understanding_status"] == "reanalysis_deferred" and capability["error_class"] == "priority_request"
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (manual["id"],))
    # The last defer observation is history, not proof the priority job is
    # still running before the next scheduler scan.
    assert store.get_discussion_state_capability("review", now_ms=12_101)["understanding_status"] == "waiting_reanalysis"
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_101) is not None


def test_success_clears_dirty_epoch_without_reprocessing_same_speech(store):
    queue(store)
    revise(store, 10_100)
    job = store.enqueue_discussion_state(meeting_id="review", now_ms=12_100, cooldown_ms=0)
    assert job is not None
    with store._write_transaction():
        store._mark_discussion_semantic_checked_locked("review", 3, 12_200)
    context = store.get_current_discussion("review")["context"]
    assert context["last_semantic_seq"] == 3 and "reanalysis" not in context
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (job["id"],))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=90_000) is None


def test_reanalysis_clock_is_host_managed(store):
    queue(store)
    topic = store.get_current_discussion("review")
    with pytest.raises(ValueError, match="host managed"):
        store.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"],
            expected_version=topic["version"], now_ms=11_000, context_patch={"reanalysis": {"last_revision_at_ms": 0}})


def test_default_scheduler_tick_admits_latest_only_after_two_second_settle(store):
    queue(store)
    revise(store, 10_100)
    enqueued = []
    for tick in (11_000, 12_000, 13_000, 14_000):
        job = store.enqueue_discussion_state(meeting_id="review", now_ms=tick)
        if job is not None:
            enqueued.append(job)
    assert len(enqueued) == 1 and enqueued[0]["created_at_ms"] == 13_000
    assert enqueued[0]["created_at_ms"] - 10_100 <= 3000


def test_legacy_corrected_dirty_context_backfills_from_evidence_revision(store):
    queue(store)
    revise(store, 10_100)
    topic = store.get_current_discussion("review")
    context = dict(topic["context"])
    context.pop("reanalysis", None)
    store._conn.execute("UPDATE agent_work_items SET context_json=? WHERE work_item_id=?",
        (json.dumps(context, ensure_ascii=False), topic["work_item_id"]))
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=11_000, cooldown_ms=0) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_100) is not None


def test_later_revision_replaces_pending_input_and_old_lease_cannot_complete(store):
    original = queue(store)
    assert store.claim_next_job(worker_id="old", lane="discussion_state", now_ms=10_000, lease_ms=5000)
    revise(store, 10_100, revision="first")
    pending = store.enqueue_discussion_state(meeting_id="review", now_ms=12_100)
    assert pending is not None
    revise(store, 12_200, revision="latest")
    assert store.get_job(pending["id"])["status"] == "cancelled"
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=14_199) is None
    latest = store.enqueue_discussion_state(meeting_id="review", now_ms=14_200)
    assert latest is not None
    assert next(e for e in latest["request_context"]["evidence"] if e["segment_id"] == "s1")["revision"] == 3
    assert len([j for j in state_jobs(store) if j["status"] in {"pending", "retry_wait", "running"}]) == 1
    assert store.complete_job(job_id=original["id"], worker_id="old", now_ms=14_201, output={"stale": True}) is None
    assert store.get_current_discussion("review")["context"]["last_semantic_seq"] == 0


def test_normal_new_speech_still_respects_successful_creation_cooldown(store):
    original = queue(store)
    with store._write_transaction():
        store._mark_discussion_semantic_checked_locked("review", 3, 10_001)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (original["id"],))
    for seq in range(4, 7):
        text = "新增一轮讨论，确认通知是否需要重试以及错误如何处理。"
        store.commit_final_and_enqueue(meeting_id="review", final_id=f"f{seq}", segment_id=f"s{seq}",
            text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
            evidence_hash=f"h{seq}", now_ms=11_000 + seq)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=14_000) is None
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=70_000) is not None


def test_material_authorization_invalidation_preserves_understood_speech_cursor(store, monkeypatch):
    from meeting_copilot_web_mvp import user_memory
    from meeting_copilot_web_mvp.discussion_persistence import invalidate_discussion_inputs

    original = queue(store)
    with store._write_transaction():
        store._mark_discussion_semantic_checked_locked("review", 3, 10_001)
    store._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (original["id"],))
    topic = store.get_current_discussion("review")
    original_binding = topic["input_context"]
    new_binding = {**original_binding, "synthetic_authorization_revision": 2}
    monkeypatch.setattr(user_memory, "input_context_state", lambda _c, _m, binding:
        "valid" if binding == new_binding else "sources_superseded")
    monkeypatch.setattr(user_memory, "capture_input_context", lambda *_args: new_binding)
    with store._write_transaction():
        invalidate_discussion_inputs(store._conn, "review", 10_100)
    invalidated = store.get_current_discussion("review")
    assert invalidated["context"]["phase"] == "needs_review"
    assert invalidated["context"]["last_semantic_seq"] == 3
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_100) is None
    refreshed = store.get_current_discussion("review")
    assert refreshed["context"]["last_semantic_seq"] == 3
    assert refreshed["input_context"] == new_binding and "reanalysis" not in refreshed["context"]


def test_identical_scheduler_defer_ticks_do_not_append_duplicate_events(store):
    queue(store)
    revise(store, 10_100)
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=10_200) is None
    event_count = store._conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0]
    for tick in (10_300, 10_400, 10_500):
        assert store.enqueue_discussion_state(meeting_id="review", now_ms=tick) is None
    assert store._conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0] == event_count


def test_ended_meeting_never_enqueues_revision_reanalysis(store):
    queue(store)
    revise(store, 10_100)
    store._conn.execute("UPDATE meetings SET state='ended' WHERE id='review'")
    assert store.enqueue_discussion_state(meeting_id="review", now_ms=12_100) is None


@pytest.mark.parametrize("clock", [
    {"first_revision_at_ms": True, "last_revision_at_ms": 2},
    {"first_revision_at_ms": 3, "last_revision_at_ms": 2},
    {"first_revision_at_ms": 1, "last_revision_at_ms": 2, "defer_reason": []},
    {"first_revision_at_ms": 1, "last_revision_at_ms": 2, "defer_reason": "private free-form reason"},
])
def test_reanalysis_clock_rejects_unbounded_or_invalid_values(clock):
    from meeting_copilot_web_mvp.discussion_persistence import bounded_discussion_context

    with pytest.raises(ValueError, match="invalid discussion reanalysis clock"):
        bounded_discussion_context({"reanalysis": clock})

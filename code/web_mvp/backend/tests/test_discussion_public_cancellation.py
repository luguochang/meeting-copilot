"""The snapshot preserves safe lifecycle reasons without exposing arbitrary errors."""
from __future__ import annotations

import time

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.mark.parametrize("reason", [
    "discussion_evidence_revised", "discussion_sources_changed", "discussion_basis_changed",
    "discussion_topic_changed", "discussion_constraints_changed", "discussion_coalesced",
    "preempted_by_user_request",
])
def test_cancelled_state_snapshot_does_not_describe_input_changes_as_provider_failure(tmp_path, reason):
    p = V2Persistence(tmp_path / "public.db", semantic_projection_mode="llm_first")
    try:
        now = time.time_ns() // 1_000_000
        p.create_meeting(meeting_id="review", title=None, now_ms=now - 10_000)
        p.commit_final_and_enqueue(meeting_id="review", final_id="f1", segment_id="s1",
            text="先确定通知的调用时序与失败处理。", normalized_text="先确定通知的调用时序与失败处理。",
            started_at_ms=0, ended_at_ms=800, evidence_hash="h1", now_ms=now - 5_000,
            enqueue_jobs=False, enqueue_intelligence=False, enqueue_answer=False)
        job = p.enqueue_discussion_state(meeting_id="review", now_ms=now,
            stable_ms=0, minimum_segments=1, minimum_characters=0)
        assert job is not None
        claimed = p.claim_next_job(worker_id="state", lane="discussion_state", now_ms=now, lease_ms=10_000)
        assert claimed is not None and claimed["id"] == job["id"]
        result = p.cancel_job(job_id=job["id"], worker_id="state", now_ms=now + 1, error_class=reason)
        assert result is not None and result["status"] == "cancelled"
        public = next(item for item in p.get_snapshot("review")["jobs"] if item["id"] == job["id"])
        assert public["error_class"] == reason
        assert public["status"] == "cancelled" and public["attempts"] == 1
        assert p.get_job(job["id"])["error_class"] == reason
    finally:
        p.close()


def test_arbitrary_cancel_exception_is_still_redacted_at_persistence_boundary(tmp_path):
    p = V2Persistence(tmp_path / "public.db", semantic_projection_mode="llm_first")
    try:
        now = time.time_ns() // 1_000_000
        p.create_meeting(meeting_id="review", title=None, now_ms=now)
        p.commit_final_and_enqueue(meeting_id="review", final_id="f1", segment_id="s1",
            text="保留原始文字", normalized_text="保留原始文字", started_at_ms=0, ended_at_ms=800,
            evidence_hash="h1", now_ms=now, enqueue_intelligence=False)
        claimed = p.claim_next_job(worker_id="state", lane="correction", now_ms=now, lease_ms=10_000)
        assert claimed is not None
        result = p.cancel_job(job_id=claimed["id"], worker_id="state", now_ms=now + 1,
            error_class="PrivateRuntimeError: confidential simulated payload")
        assert result is not None and result["error_class"] == "job_failed"
        assert "confidential" not in str(p.get_snapshot("review"))
    finally:
        p.close()

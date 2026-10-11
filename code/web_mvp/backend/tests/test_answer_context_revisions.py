from __future__ import annotations

import pytest

from meeting_copilot_web_mvp.v2_persistence import (
    JobLeaseLostError,
    StaleEvidenceError,
    V2Persistence,
    transcript_revision_requires_refresh,
)


@pytest.fixture
def repository(tmp_path):
    instance = V2Persistence(tmp_path / "answers.db", semantic_projection_mode="llm_first")
    yield instance
    instance.close()


def seeded_answer(repository, *, committed=True, create_draft=True, bind=True):
    for index, (segment_id, text, track) in enumerate([
        ("context", "方案已验收。", "microphone"),
        ("question", "现在可以发布吗？", "system_audio"),
    ]):
        repository.commit_final_and_enqueue(
            meeting_id="meeting", final_id=f"final-{index}", segment_id=segment_id,
            text=text, normalized_text=text, source_track=track,
            started_at_ms=index * 1_000, ended_at_ms=index * 1_000 + 900,
            evidence_hash=f"hash-{segment_id}", now_ms=1_000 + index * 1_000,
        )
    job = repository.claim_next_job(worker_id="worker", lane="answer", now_ms=2_100, lease_ms=60_000)
    assert job is not None
    bindings = [repository.get_transcript_segment("meeting", item) for item in ("context", "question")]
    if not create_draft:
        return job, bindings
    answer = repository.upsert_suggestion_draft(
        suggestion_id="answer", meeting_id="meeting", job_id=job["id"], generation_id=job["generation_id"],
        evidence_segment_id="question", evidence_transcript_seq=2, evidence_hash="hash-question",
        state_revision=job["input_version"], draft_text="方案已验收，可以发布。", draft_seq=1,
        kind="answer", question_text="现在可以发布吗？", now_ms=2_200, lease_owner="worker",
    )
    if bind:
        repository.bind_answer_context_evidence(
            suggestion_id="answer", segments=bindings, now_ms=2_201,
            expected_job_id=job["id"], expected_lease_owner="worker",
        )
    if committed:
        repository.commit_suggestion(
            suggestion_id="answer", generation_id=answer["generation_id"], expected_evidence_hash="hash-question",
            final_draft_seq=1, text="方案已验收，可以发布。", now_ms=2_300,
            expected_job_id=job["id"], expected_lease_owner="worker",
        )
        repository.complete_job(job_id=job["id"], worker_id="worker", now_ms=2_301, output={"ok": True})
    return job, bindings


def revise(repository, text="方案未验收。", *, now_ms=3_000):
    return repository.commit_transcript_revision(
        meeting_id="meeting", segment_id="context", expected_evidence_hash="hash-context",
        corrected_text=text, revision_id="revision", now_ms=now_ms,
        evidence_remap_reason="validated_meaning_preserved_correction",
    )


def test_earlier_context_revision_retracts_committed_answer_and_queues_one_update(repository):
    _, _ = seeded_answer(repository)
    deep = repository.enqueue_answer_coach_request(meeting_id="meeting", answer_id="answer", now_ms=2_500)
    updated = revise(repository)
    assert updated["text"] == "方案已验收。"  # Raw ASR remains available.
    assert updated["normalized_text"] == "方案未验收。"
    answer = repository.get_suggestion("answer")
    assert answer["status"] == "superseded"
    assert answer["text"] == "方案已验收，可以发布。"  # History remains readable.
    assert answer["error_class"] == "transcript_revised"
    assert repository.get_job(deep["id"])["status"] == "cancelled"
    updates = [job for job in repository.list_jobs(meeting_id="meeting", lane="answer") if job["trigger_type"] == "transcript_revision"]
    assert len(updates) == 1
    assert updates[0]["work_item_id"] == "answer"
    assert updates[0]["evidence_hash"] == "hash-question"  # Question itself did not change.
    revise(repository, now_ms=3_100)
    assert len([job for job in repository.list_jobs(meeting_id="meeting", lane="answer") if job["trigger_type"] == "transcript_revision"]) == 1


def test_inflight_answer_cannot_commit_after_context_revision(repository):
    job, _ = seeded_answer(repository, committed=False)
    revise(repository)
    assert repository.get_job(job["id"])["status"] == "cancelled"
    with pytest.raises(JobLeaseLostError):
        repository.commit_suggestion(
            suggestion_id="answer", generation_id=job["generation_id"], expected_evidence_hash="hash-question",
            final_draft_seq=1, text="迟到的错误回答", now_ms=3_100,
            expected_job_id=job["id"], expected_lease_owner="worker",
        )


def test_format_only_revision_remaps_bindings_without_new_answer_call(repository):
    seeded_answer(repository)
    updated = revise(repository, "方案已验收")
    answer = repository.get_suggestion("answer")
    assert answer["status"] == "committed"
    assert answer["evidence_hash"] == "hash-question"
    assert answer["context_evidence"][0]["evidence_hash"] == updated["evidence_hash"]
    assert len(repository.list_jobs(meeting_id="meeting", lane="answer")) == 1


def test_format_only_revision_reuses_unstarted_deep_with_current_versions(repository):
    seeded_answer(repository)
    deep = repository.enqueue_answer_coach_request(meeting_id="meeting", answer_id="answer", now_ms=2_500)
    updated = repository.commit_transcript_revision(
        meeting_id="meeting", segment_id="question", expected_evidence_hash="hash-question",
        corrected_text=" 现在可以发布吗？ ", revision_id="question-format", now_ms=3_000,
        evidence_remap_reason="validated_meaning_preserved_correction",
    )
    current = repository.get_job(deep["id"])
    assert current["status"] == "pending"
    assert current["evidence_hash"] == updated["evidence_hash"]
    paragraph = repository._conn.execute(
        "SELECT p.revision FROM semantic_paragraph_checkpoints c JOIN semantic_paragraphs p "
        "ON p.meeting_id=c.meeting_id AND p.paragraph_id=c.paragraph_id WHERE c.checkpoint_id='question'",
    ).fetchone()
    assert current["input_version"] == paragraph["revision"]
    assert len(repository.list_jobs(meeting_id="meeting", lane="answer")) == 1


def test_ended_meeting_revision_does_not_start_live_answer_work(repository):
    seeded_answer(repository)
    repository.end_meeting(meeting_id="meeting", now_ms=2_500)
    revise(repository)
    assert repository.get_suggestion("answer")["status"] == "superseded"
    assert len(repository.list_jobs(meeting_id="meeting", lane="answer")) == 1


def test_context_binding_rejects_stale_version_before_model_request(repository):
    job, bindings = seeded_answer(repository, committed=False)
    with pytest.raises(StaleEvidenceError, match="context changed"):
        repository.bind_answer_context_evidence(
            suggestion_id="answer", segments=[{**bindings[0], "evidence_hash": "old-version"}, bindings[1]],
            now_ms=2_400, expected_job_id=job["id"], expected_lease_owner="worker",
        )


def test_context_commit_barrier_checks_all_bindings_even_without_lease(repository):
    job, _ = seeded_answer(repository, committed=False)
    # Simulate a source replacement not accompanied by the canonical revision
    # outbox. The final barrier must still reject the stale context.
    repository._conn.execute("UPDATE transcript_segments SET evidence_hash = 'new' WHERE segment_id = 'context'")
    repository._conn.commit()
    assert repository.commit_suggestion(
        suggestion_id="answer", generation_id=job["generation_id"], expected_evidence_hash="hash-question",
        final_draft_seq=1, text="错误旧事实", now_ms=2_400,
    ) is None


@pytest.mark.parametrize("running", [False, True])
def test_anchor_revision_before_first_draft_replaces_job(repository, running):
    job, _ = seeded_answer(repository, committed=False, create_draft=False)
    if not running:
        repository._conn.execute("UPDATE jobs SET status = 'pending', lease_owner = NULL, lease_until_ms = NULL WHERE id = ?", (job["id"],))
        repository._conn.commit()
    updated = repository.commit_transcript_revision(
        meeting_id="meeting", segment_id="question", expected_evidence_hash="hash-question",
        corrected_text="现在还不能发布吗？", revision_id="question-revision", now_ms=3_000,
        evidence_remap_reason="validated_meaning_preserved_correction",
    )
    assert repository.get_job(job["id"])["status"] == "cancelled"
    replacements = [item for item in repository.list_jobs(meeting_id="meeting", lane="answer") if item["trigger_type"] == "transcript_revision"]
    assert len(replacements) == 1
    assert replacements[0]["evidence_hash"] == updated["evidence_hash"]
    assert replacements[0]["work_item_id"] is None
    replacement = repository.claim_next_job(worker_id="new-worker", lane="answer", now_ms=3_100, lease_ms=60_000)
    assert replacement["id"] == replacements[0]["id"]
    repository.upsert_suggestion_draft(
        suggestion_id="new-answer", meeting_id="meeting", job_id=replacement["id"], generation_id=replacement["generation_id"],
        evidence_segment_id="question", evidence_transcript_seq=2, evidence_hash=updated["evidence_hash"],
        state_revision=replacement["input_version"], draft_text="", draft_seq=0,
        kind="answer", question_text=updated["normalized_text"], now_ms=3_200, lease_owner="new-worker",
    )


def test_binding_selects_latest_context_atomically_before_question_not_snapshot_tail(repository):
    job, _ = seeded_answer(repository, committed=False, bind=False)
    updated = revise(repository)
    for index in range(20):
        repository.commit_final_and_enqueue(
            meeting_id="meeting", final_id=f"later-final-{index}", segment_id=f"later-{index}",
            text=f"后续无关内容{index}", normalized_text=f"后续无关内容{index}", source_track="microphone",
            started_at_ms=5_000 + index * 1_000, ended_at_ms=5_900 + index * 1_000,
            evidence_hash=f"later-hash-{index}", now_ms=5_000 + index * 1_000, enqueue_answer=False,
        )
    bound = repository.bind_answer_context_evidence(
        suggestion_id="answer", now_ms=30_000, expected_job_id=job["id"], expected_lease_owner="worker",
    )
    assert [item["segment_id"] for item in bound["context_segments"]] == ["context", "question"]
    assert bound["context_segments"][0]["normalized_text"] == "方案未验收。"
    assert bound["context_evidence"][0]["evidence_hash"] == updated["evidence_hash"]


def test_binding_requires_question_anchor(repository):
    job, bindings = seeded_answer(repository, committed=False)
    with pytest.raises(ValueError, match="question anchor"):
        repository.bind_answer_context_evidence(
            suggestion_id="answer", segments=[bindings[0]], now_ms=2_400,
            expected_job_id=job["id"], expected_lease_owner="worker",
        )


@pytest.mark.parametrize(("before", "after", "material"), [
    ("已验收。", "未验收。", True),
    ("延迟1.2秒", "延迟12秒", True),
    ("预算-100元", "预算100元", True),
    ("已验收？", "已验收。", True),
    ("不，可以上线", "不可以上线", True),
    ("压测通过。", " 压测通过 ", False),
])
def test_only_narrow_formatting_changes_can_remap(before, after, material):
    assert transcript_revision_requires_refresh(before, after) is material

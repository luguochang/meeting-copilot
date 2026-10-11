from __future__ import annotations

import pytest

from meeting_copilot_web_mvp.v2_persistence import ReviewSourceChangedError, V2Persistence


@pytest.fixture
def repository(tmp_path):
    value = V2Persistence(tmp_path / "review.db")
    value.commit_final_and_enqueue(
        meeting_id="meeting", final_id="final", segment_id="segment", text="方案已验收。",
        normalized_text="方案已验收。", source_track="microphone", started_at_ms=0,
        ended_at_ms=900, evidence_hash="old-hash", now_ms=1_000, enqueue_answer=False,
    )
    yield value
    value.close()


def save_ai(repository, author="ai:one"):
    return repository.save_ai_generated_document(
        meeting_id="meeting", document_kind="minutes", content={"markdown": "AI 旧稿"},
        author=author, now_ms=2_000,
    )


def revise(repository, text="方案未验收。"):
    return repository.commit_transcript_revision(
        meeting_id="meeting", segment_id="segment", expected_evidence_hash="old-hash",
        corrected_text=text, revision_id="revision", now_ms=3_000,
        evidence_remap_reason="validated_meaning_preserved_correction",
    )


def test_revision_marks_review_stale_and_invalidates_old_search(repository):
    document = save_ai(repository)
    repository.rebuild_search_document(meeting_id="meeting", job_id="index-one", now_ms=2_100)
    source = repository.capture_review_source("meeting")
    revise(repository)
    current = repository.get_review_document("meeting", "minutes")
    assert current["dirty_state"] == "stale"
    assert current["revision"] == document["revision"] + 1
    assert current["ai_generated"]["content"] == {"markdown": "AI 旧稿"}
    assert repository._conn.execute("SELECT 1 FROM search_documents WHERE meeting_id='meeting'").fetchone() is None
    with pytest.raises(ReviewSourceChangedError):
        repository.save_minutes(
            meeting_id="meeting", job_id="late-minutes", markdown="迟到旧稿", structured=None,
            degraded=False, now_ms=3_100, expected_transcript_hash=source["hash"],
        )
    with pytest.raises(ReviewSourceChangedError):
        repository.save_approach_cards(
            meeting_id="meeting", job_id="late-approach", cards=[], degraded=False,
            now_ms=3_100, expected_transcript_hash=source["hash"],
        )


def test_ai_regeneration_keeps_human_source_version_and_stale_notice(repository):
    initial = save_ai(repository)
    human = repository.save_user_final_document(
        meeting_id="meeting", document_kind="minutes", expected_revision=initial["revision"],
        content={"markdown": "人工最终稿"}, now_ms=2_500,
    )
    revise(repository)
    refreshed = repository.save_ai_generated_document(
        meeting_id="meeting", document_kind="minutes", content={"markdown": "新的 AI 初稿"},
        author="ai:two", now_ms=4_000,
    )
    assert refreshed["user_final"]["content"] == human["user_final"]["content"]
    assert refreshed["user_final"]["source_transcript_revision"] == 1
    assert refreshed["ai_generated"]["source_transcript_revision"] == 2
    assert refreshed["source_transcript_revision"] == 1
    assert refreshed["dirty_state"] == "stale"
    confirmed = repository.save_user_final_document(
        meeting_id="meeting", document_kind="minutes", expected_revision=refreshed["revision"],
        content={"markdown": "人工核对新版本"}, now_ms=5_000,
    )
    assert confirmed["source_transcript_revision"] == 2
    assert confirmed["dirty_state"] == "saved"


def test_fresh_ai_regeneration_clears_stale_for_unmodified_copy(repository):
    save_ai(repository)
    revise(repository)
    refreshed = save_ai(repository, "ai:two")
    assert refreshed["user_final"]["modified"] is False
    assert refreshed["dirty_state"] == "saved"
    assert refreshed["source_transcript_revision"] == 2
    assert refreshed["ai_generated"]["source_transcript_revision"] == 2
    assert refreshed["user_final"]["source_transcript_revision"] == 1


def test_format_only_revision_does_not_invalidate_review(repository):
    save_ai(repository)
    revise(repository, " 方案已验收 ")
    assert repository.get_review_document("meeting", "minutes")["dirty_state"] == "saved"


def test_new_final_invalidates_existing_review(repository):
    save_ai(repository)
    repository.commit_final_and_enqueue(
        meeting_id="meeting", final_id="next-final", segment_id="next-segment", text="暂缓发布。",
        normalized_text="暂缓发布。", source_track="microphone", started_at_ms=1_000,
        ended_at_ms=1_900, evidence_hash="next-hash", now_ms=3_000, enqueue_answer=False,
    )
    assert repository.get_review_document("meeting", "minutes")["dirty_state"] == "stale"

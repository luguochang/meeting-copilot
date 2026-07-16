from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from meeting_copilot_web_mvp.v2_persistence import JobLeaseLostError, V2Persistence


def _commit_final(
    persistence: V2Persistence,
    *,
    final_id: str = "final-1",
    segment_id: str = "segment-1",
    text: str = "我们需要确认发布负责人和回滚时间。",
    now_ms: int = 1_000,
):
    return persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id=final_id,
        segment_id=segment_id,
        text=text,
        normalized_text=text,
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash=f"hash-{final_id}",
        now_ms=now_ms,
    )


@pytest.fixture
def persistence(tmp_path):
    instance = V2Persistence(tmp_path / "meeting_copilot.db")
    yield instance
    instance.close()


def test_schema_is_additive_and_does_not_use_record_json(tmp_path):
    database_path = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE existing_data (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_data VALUES ('preserved')")

    persistence = V2Persistence(database_path)
    persistence.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT value FROM existing_data").fetchone()[0] == "preserved"
        for table in ("meetings", "meeting_events", "jobs", "transcript_segments", "suggestions"):
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            assert columns
            assert "record_json" not in columns
        entity_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(meeting_entities)").fetchall()
        }
        assert {"version", "first_seen_seq", "last_updated_seq"} <= entity_columns


def test_duplicate_final_is_idempotent_and_meeting_sequence_is_strict(persistence):
    first = _commit_final(persistence)
    duplicate = _commit_final(persistence)
    second = _commit_final(
        persistence,
        final_id="final-2",
        segment_id="segment-2",
        text="上线前还要补充容量压测。",
        now_ms=2_000,
    )

    assert first["created"] is True
    assert duplicate == {**first, "created": False}
    assert first["event_seq"] == 1
    assert second["event_seq"] > first["event_seq"]
    events = persistence.list_events("meeting-1")
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert [event["seq"] for event in events if event["type"] == "transcript.segment.finalized"] == [
        first["event_seq"],
        second["event_seq"],
    ]
    assert len(persistence.list_jobs(meeting_id="meeting-1")) == 4


def test_event_page_is_bounded_and_advances_with_a_durable_cursor(persistence):
    for index in range(3):
        _commit_final(
            persistence,
            final_id=f"page-final-{index}",
            segment_id=f"page-segment-{index}",
            text=f"第{index + 1}段会议内容。",
            now_ms=1_000 + index,
        )

    first = persistence.list_event_page("meeting-1", limit=2)
    second = persistence.list_event_page(
        "meeting-1",
        after_seq=first["next_after_seq"],
        limit=2,
    )

    assert len(first["events"]) == 2
    assert first["has_more"] is True
    assert first["next_after_seq"] == first["events"][-1]["seq"]
    assert second["after_seq"] == first["next_after_seq"]
    assert second["has_more"] is True
    assert second["events"][0]["seq"] > first["events"][-1]["seq"]
    assert persistence.list_events("meeting-1", limit=2) == first["events"]


def test_event_page_rejects_unbounded_or_invalid_limits(persistence):
    with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
        persistence.list_event_page("meeting-1", limit=0)
    with pytest.raises(ValueError, match="limit must be between 1 and 1000"):
        persistence.list_events("meeting-1", limit=1_001)


def test_meeting_entity_versions_advance_across_merge_and_answer(persistence):
    _commit_final(
        persistence,
        final_id="question-1",
        segment_id="question-segment-1",
        text="数据库迁移由谁负责？",
        now_ms=1_000,
    )
    created = persistence.get_snapshot("meeting-1")["open_questions"][0]
    _commit_final(
        persistence,
        final_id="question-2",
        segment_id="question-segment-2",
        text="我再确认一下，数据库迁移由谁负责？",
        now_ms=2_000,
    )
    merged = persistence.get_snapshot("meeting-1")["open_questions"][0]
    _commit_final(
        persistence,
        final_id="answer-1",
        segment_id="answer-segment-1",
        text="数据库迁移由李明负责。",
        now_ms=3_000,
    )
    answered = persistence.get_snapshot("meeting-1")["open_questions"][0]

    assert created["version"] == 1
    assert created["first_seen_seq"] == created["last_updated_seq"] == 1
    assert merged["version"] == 2
    assert merged["first_seen_seq"] == 1
    assert merged["last_updated_seq"] == 2
    assert answered["version"] == 3
    assert answered["first_seen_seq"] == 1
    assert answered["last_updated_seq"] == 3
    assert answered["status"] == "answered"
    assert answered["evidence_segment_ids"] == [
        "question-segment-1",
        "question-segment-2",
        "answer-segment-1",
    ]


def test_reopening_question_outside_latest_three_preserves_entity_history(persistence):
    questions = (
        "接口超时时间是多少？",
        "最大重试次数是多少？",
        "熔断阈值是多少？",
        "日志保留多少天？",
    )
    for seq, text in enumerate(questions, start=1):
        persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id=f"question-{seq}",
            segment_id=f"question-segment-{seq}",
            text=text,
            normalized_text=text,
            started_at_ms=seq * 1_000 - 500,
            ended_at_ms=seq * 1_000,
            evidence_hash=f"question-hash-{seq}",
            now_ms=seq * 1_000,
        )

    assert questions[0] not in {
        item["text"] for item in persistence.get_snapshot("meeting-1")["open_questions"]
    }
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="question-reopened",
        segment_id="question-segment-reopened",
        text=questions[0],
        normalized_text=questions[0],
        started_at_ms=4_500,
        ended_at_ms=5_000,
        evidence_hash="question-hash-reopened",
        now_ms=5_000,
    )

    reopened = next(
        item
        for item in persistence.get_snapshot("meeting-1")["open_questions"]
        if item["text"] == questions[0]
    )
    assert reopened["version"] == 2
    assert reopened["first_seen_seq"] == 1
    assert reopened["last_updated_seq"] == 5
    assert reopened["evidence_segment_ids"] == [
        "question-segment-1",
        "question-segment-reopened",
    ]


def test_final_segment_event_and_both_jobs_roll_back_together(tmp_path):
    database_path = tmp_path / "meeting_copilot.db"
    persistence = V2Persistence(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TRIGGER fail_suggestion_job "
            "BEFORE INSERT ON jobs WHEN NEW.kind = 'suggestion' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic suggestion failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="synthetic suggestion failure"):
        _commit_final(persistence)

    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["meeting_id"] == "meeting-1"
    assert snapshot["last_seq"] == 0
    assert snapshot["segments"] == []
    assert snapshot["suggestions"] == []
    assert snapshot["runtime"]["phase"] == "unknown"
    assert persistence.list_jobs(meeting_id="meeting-1") == []

    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TRIGGER fail_suggestion_job")
    assert _commit_final(persistence)["event_seq"] == 1
    persistence.close()


def test_final_enqueues_independent_correction_and_suggestion_lanes(persistence):
    committed = _commit_final(persistence)
    jobs = persistence.list_jobs(meeting_id="meeting-1")

    assert committed["job_ids"] == {
        "correction": jobs[0]["id"],
        "suggestion": jobs[1]["id"],
    }
    assert [job["kind"] for job in jobs] == ["correction", "suggestion"]
    assert {job["status"] for job in jobs} == {"pending"}
    assert {job["input_transcript_seq"] for job in jobs} == {1}
    assert {job["evidence_hash"] for job in jobs} == {"hash-final-1"}
    assert jobs[1]["generation_id"] == "suggestion:meeting-1:final-1"


def test_snapshot_jobs_are_strictly_redacted_status_summaries(persistence):
    _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="correction-worker",
        lane="correction",
        now_ms=1_100,
        lease_ms=1_000,
    )
    assert claimed is not None
    completed = persistence.complete_job(
        job_id=claimed["id"],
        worker_id="correction-worker",
        now_ms=1_200,
        output={
            "output": "private-transcript-output",
            "prompt": "private-correction-prompt",
            "evidence_hash": "private-evidence-hash",
        },
    )
    assert completed is not None

    summaries = persistence.get_snapshot("meeting-1")["jobs"]

    allowed_fields = {
        "id",
        "kind",
        "status",
        "attempts",
        "max_attempts",
        "error_class",
        "created_at_ms",
        "updated_at_ms",
        "completed_at_ms",
    }
    assert len(summaries) == 2
    assert all(set(summary) == allowed_fields for summary in summaries)
    correction = next(summary for summary in summaries if summary["kind"] == "correction")
    assert correction == {
        "id": claimed["id"],
        "kind": "correction",
        "status": "succeeded",
        "attempts": 1,
        "max_attempts": 3,
        "error_class": None,
        "created_at_ms": 1_000,
        "updated_at_ms": 1_200,
        "completed_at_ms": 1_200,
    }
    serialized = json.dumps(summaries, ensure_ascii=False)
    assert "private-transcript-output" not in serialized
    assert "private-correction-prompt" not in serialized
    assert "private-evidence-hash" not in serialized
    for forbidden_field in (
        "output",
        "evidence_hash",
        "evidence_segment_id",
        "lease_owner",
        "lease_until_ms",
        "generation_id",
        "idempotency_key",
        "input_transcript_seq",
    ):
        assert all(forbidden_field not in summary for summary in summaries)


def test_job_lease_heartbeat_retry_completion_and_expiry_recovery(persistence):
    _commit_final(persistence)

    claimed = persistence.claim_next_job(
        worker_id="worker-a",
        lane="suggestion",
        now_ms=2_000,
        lease_ms=500,
    )
    assert claimed is not None
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["lease_until_ms"] == 2_500
    assert persistence.heartbeat_job(
        job_id=claimed["id"],
        worker_id="wrong-worker",
        now_ms=2_100,
        lease_ms=500,
    ) is False
    assert persistence.heartbeat_job(
        job_id=claimed["id"],
        worker_id="worker-a",
        now_ms=2_100,
        lease_ms=500,
    ) is True

    retried = persistence.retry_job(
        job_id=claimed["id"],
        worker_id="worker-a",
        now_ms=2_200,
        next_attempt_at_ms=3_000,
        error_class="provider_429",
    )
    assert retried["status"] == "retry_wait"
    assert persistence.claim_next_job(
        worker_id="worker-b", lane="suggestion", now_ms=2_999, lease_ms=500
    ) is None

    reclaimed = persistence.claim_next_job(
        worker_id="worker-b", lane="suggestion", now_ms=3_000, lease_ms=500
    )
    assert reclaimed["attempts"] == 2
    assert persistence.recover_expired_leases(now_ms=3_500) == 1
    recovered = persistence.get_job(reclaimed["id"])
    assert recovered["status"] == "retry_wait"
    assert recovered["lease_owner"] is None

    final_claim = persistence.claim_next_job(
        worker_id="worker-c", lane="suggestion", now_ms=3_500, lease_ms=500
    )
    completed = persistence.complete_job(
        job_id=final_claim["id"],
        worker_id="worker-c",
        now_ms=3_600,
        output={"suggestion_text": "请确认发布负责人。"},
    )
    assert completed["status"] == "succeeded"
    assert completed["output"] == {"suggestion_text": "请确认发布负责人。"}
    assert persistence.complete_job(
        job_id=final_claim["id"],
        worker_id="worker-c",
        now_ms=3_700,
        output={},
    ) is None


def test_concurrent_claim_across_connections_claims_job_once(tmp_path):
    database_path = tmp_path / "meeting_copilot.db"
    seed = V2Persistence(database_path)
    _commit_final(seed)
    seed.close()

    def claim(worker_id: str):
        worker = V2Persistence(database_path)
        try:
            return worker.claim_next_job(
                worker_id=worker_id,
                lane="suggestion",
                now_ms=2_000,
                lease_ms=1_000,
            )
        finally:
            worker.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    claims = [result for result in results if result is not None]
    assert len(claims) == 1
    assert claims[0]["lease_owner"] in {"worker-a", "worker-b"}


def test_suggestion_generation_evidence_uniqueness_and_snapshot(persistence):
    committed = _commit_final(persistence)
    suggestion_job_id = committed["job_ids"]["suggestion"]
    persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-1",
        meeting_id="meeting-1",
        job_id=suggestion_job_id,
        generation_id="generation-1",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=3,
        draft_text="请确认",
        draft_seq=1,
        now_ms=2_000,
    )
    persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-1",
        meeting_id="meeting-1",
        job_id=suggestion_job_id,
        generation_id="generation-1",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=3,
        draft_text="请确认发布负责人",
        draft_seq=2,
        now_ms=2_100,
    )
    committed_suggestion = persistence.commit_suggestion(
        suggestion_id="suggestion-1",
        generation_id="generation-1",
        expected_evidence_hash="hash-final-1",
        final_draft_seq=2,
        text="请确认发布负责人。",
        now_ms=2_200,
    )

    assert committed_suggestion["status"] == "committed"
    assert committed_suggestion["evidence_segment_id"] == "segment-1"
    assert committed_suggestion["generation_id"] == "generation-1"
    with pytest.raises(sqlite3.IntegrityError):
        persistence.upsert_suggestion_draft(
            suggestion_id="suggestion-2",
            meeting_id="meeting-1",
            job_id=None,
            generation_id="generation-1",
            evidence_segment_id="segment-1",
            evidence_transcript_seq=1,
            evidence_hash="hash-final-1",
            state_revision=3,
            draft_text="重复 generation",
            draft_seq=1,
            now_ms=2_300,
        )

    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["last_seq"] == persistence.list_events("meeting-1")[-1]["seq"]
    assert snapshot["segments"] == [
        {
            "meeting_id": "meeting-1",
            "segment_id": "segment-1",
            "final_id": "final-1",
            "transcript_seq": 1,
            "text": "我们需要确认发布负责人和回滚时间。",
            "normalized_text": "我们需要确认发布负责人和回滚时间。",
            "started_at_ms": 100,
            "ended_at_ms": 900,
            "revision": 1,
            "evidence_hash": "hash-final-1",
            "created_at_ms": 1_000,
            "updated_at_ms": 1_000,
        }
    ]
    assert snapshot["suggestions"] == [committed_suggestion]
    event_types = [event["type"] for event in persistence.list_events("meeting-1")]
    assert event_types[0] == "transcript.segment.finalized"
    assert event_types[-3:] == [
        "suggestion.draft.started",
        "suggestion.draft.delta",
        "suggestion.committed",
    ]


def test_suggestion_draft_and_commit_are_fenced_by_the_claimed_job_lease(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="suggestion-worker",
        lane="suggestion",
        now_ms=1_100,
        lease_ms=1_000,
    )
    assert claimed is not None
    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="lease-fenced-suggestion",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id=str(claimed["generation_id"]),
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="建议确认发布负责人。",
        draft_seq=1,
        now_ms=1_200,
        lease_owner="suggestion-worker",
    )

    persistence.recover_expired_leases(now_ms=2_100)

    with pytest.raises(JobLeaseLostError, match="lease is no longer active"):
        persistence.upsert_suggestion_draft(
            suggestion_id=suggestion["suggestion_id"],
            meeting_id="meeting-1",
            job_id=committed["job_ids"]["suggestion"],
            generation_id=str(claimed["generation_id"]),
            evidence_segment_id="segment-1",
            evidence_transcript_seq=1,
            evidence_hash="hash-final-1",
            state_revision=1,
            draft_text="建议确认发布负责人和回滚窗口。",
            draft_seq=2,
            now_ms=2_100,
            lease_owner="suggestion-worker",
        )
    with pytest.raises(JobLeaseLostError, match="lease is no longer active"):
        persistence.commit_suggestion(
            suggestion_id=suggestion["suggestion_id"],
            generation_id=str(claimed["generation_id"]),
            expected_evidence_hash="hash-final-1",
            final_draft_seq=1,
            text="建议确认发布负责人。",
            now_ms=2_100,
            expected_job_id=committed["job_ids"]["suggestion"],
            expected_lease_owner="suggestion-worker",
        )
    assert persistence.get_suggestion(suggestion["suggestion_id"])["status"] == "draft"


def test_transcript_and_meeting_event_sequences_are_independent(persistence):
    first = _commit_final(persistence)
    persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-1",
        meeting_id="meeting-1",
        job_id=first["job_ids"]["suggestion"],
        generation_id="generation-1",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="请确认负责人",
        draft_seq=1,
        now_ms=1_100,
    )

    second = _commit_final(
        persistence,
        final_id="final-2",
        segment_id="segment-2",
        text="还需要明确回滚时限。",
        now_ms=1_200,
    )

    snapshot = persistence.get_snapshot("meeting-1")
    assert [segment["transcript_seq"] for segment in snapshot["segments"]] == [1, 2]
    assert second["transcript_seq"] == 2
    assert second["event_seq"] > first["event_seq"]


def test_transcript_revision_updates_canonical_fact_and_outbox_atomically(persistence):
    committed = _commit_final(persistence)

    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-before-revision",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id="generation-before-revision",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="请确认回滚时间。",
        draft_seq=1,
        now_ms=1_500,
    )
    persistence.commit_suggestion(
        suggestion_id=suggestion["suggestion_id"],
        generation_id=suggestion["generation_id"],
        expected_evidence_hash=suggestion["evidence_hash"],
        final_draft_seq=1,
        text="请确认回滚时间。",
        now_ms=1_600,
    )

    revised = persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id="segment-1",
        expected_evidence_hash="hash-final-1",
        corrected_text="我们需要确认发布负责人和回滚时间点。",
        revision_id="revision-1",
        now_ms=2_000,
        correlation_id="meeting-1",
        causation_id=committed["job_ids"]["correction"],
        evidence_remap_reason="validated_meaning_preserved_correction",
    )
    duplicate = persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id="segment-1",
        expected_evidence_hash="hash-final-1",
        corrected_text="我们需要确认发布负责人和回滚时间点。",
        revision_id="revision-1",
        now_ms=2_100,
    )

    assert revised["revision"] == 2
    assert revised["normalized_text"] == "我们需要确认发布负责人和回滚时间点。"
    assert revised["evidence_hash"] != "hash-final-1"
    assert duplicate == revised
    remapped_suggestion = persistence.get_snapshot("meeting-1")["suggestions"][0]
    assert remapped_suggestion["status"] == "committed"
    assert remapped_suggestion["evidence_hash"] == revised["evidence_hash"]
    transcript_event_types = [
        event["type"]
        for event in persistence.list_events("meeting-1")
        if event["type"].startswith("transcript.segment.")
    ]
    assert transcript_event_types == [
        "transcript.segment.finalized",
        "transcript.segment.revised",
    ]
    assert "suggestion.evidence.remapped" in [
        event["type"] for event in persistence.list_events("meeting-1")
    ]
    assert persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id="segment-1",
        expected_evidence_hash="stale-hash",
        corrected_text="错误覆盖",
        revision_id="revision-2",
        now_ms=2_200,
    ) is None


def test_meeting_end_and_suggestion_feedback_are_durable_events(persistence):
    committed = _commit_final(persistence)
    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="suggestion-1",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id="generation-1",
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="建议确认发布负责人。",
        draft_seq=1,
        now_ms=2_000,
    )
    persistence.commit_suggestion(
        suggestion_id=suggestion["suggestion_id"],
        generation_id=suggestion["generation_id"],
        expected_evidence_hash=suggestion["evidence_hash"],
        final_draft_seq=1,
        text="建议确认发布负责人。",
        now_ms=2_100,
    )

    feedback = persistence.save_suggestion_feedback(
        meeting_id="meeting-1",
        suggestion_id="suggestion-1",
        feedback="kept",
        now_ms=2_200,
    )
    ended = persistence.end_meeting(meeting_id="meeting-1", now_ms=2_300)
    duplicate_end = persistence.end_meeting(meeting_id="meeting-1", now_ms=2_400)

    assert feedback["feedback"] == "kept"
    assert ended["state"] == "ended"
    assert duplicate_end["ended_at_ms"] == ended["ended_at_ms"]
    assert {job["kind"] for job in persistence.list_jobs(meeting_id="meeting-1")} == {
        "correction",
        "suggestion",
        "minutes",
        "approach",
        "index",
    }
    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["runtime"]["phase"] == "ended"
    assert snapshot["suggestions"][0]["feedback"] == "kept"
    assert [event["type"] for event in persistence.list_events("meeting-1")][-2:] == [
        "suggestion.feedback.updated",
        "meeting.ended",
    ]


def test_snapshot_window_and_transcript_cursor_pagination(persistence):
    for number in range(1, 6):
        _commit_final(
            persistence,
            final_id=f"final-{number}",
            segment_id=f"segment-{number}",
            text=f"第 {number} 段会议文字。",
            now_ms=number * 1_000,
        )

    snapshot = persistence.get_snapshot("meeting-1", segment_limit=2)
    first_page = persistence.list_transcript_segments("meeting-1", limit=2)
    second_page = persistence.list_transcript_segments(
        "meeting-1",
        after_transcript_seq=first_page["next_after_transcript_seq"],
        limit=2,
    )

    assert [segment["transcript_seq"] for segment in snapshot["segments"]] == [4, 5]
    assert snapshot["transcript_page"] == {
        "returned": 2,
        "total": 5,
        "has_more": True,
        "first_seq": 4,
        "last_seq": 5,
    }
    assert [segment["transcript_seq"] for segment in first_page["segments"]] == [1, 2]
    assert first_page["has_more"] is True
    assert [segment["transcript_seq"] for segment in second_page["segments"]] == [3, 4]


def test_audio_chunk_fact_is_idempotent_and_visible_before_first_final(persistence):
    chunk = persistence.record_audio_chunk(
        meeting_id="audio-meeting",
        track="microphone",
        epoch=0,
        chunk_seq=0,
        relative_path="audio_assets/audio-meeting/chunks/chunk-00000000.pcm",
        sha256="a" * 64,
        sample_rate_hz=16_000,
        sample_count=80_000,
        duration_ms=5_000,
        file_size_bytes=160_000,
        now_ms=1_000,
    )
    duplicate = persistence.record_audio_chunk(
        meeting_id="audio-meeting",
        track="microphone",
        epoch=0,
        chunk_seq=0,
        relative_path="audio_assets/audio-meeting/chunks/chunk-00000000.pcm",
        sha256="a" * 64,
        sample_rate_hz=16_000,
        sample_count=80_000,
        duration_ms=5_000,
        file_size_bytes=160_000,
        now_ms=2_000,
    )

    assert duplicate == chunk
    snapshot = persistence.get_snapshot("audio-meeting")
    assert snapshot["runtime"]["phase"] == "live"
    assert snapshot["segments"] == []
    assert snapshot["audio"] == {
        "chunk_count": 1,
        "duration_ms": 5_000,
        "file_size_bytes": 160_000,
        "tracks": ["microphone"],
        "status": "recording",
    }
    assert [event["type"] for event in persistence.list_events("audio-meeting")] == [
        "recording.chunk.committed"
    ]

    assert persistence.recover_interrupted_recordings(now_ms=2_000) == [
        "audio-meeting"
    ]
    assert persistence.get_snapshot("audio-meeting")["runtime"]["phase"] == "interrupted"

    persistence.record_audio_chunk(
        meeting_id="audio-meeting",
        track="microphone",
        epoch=1,
        chunk_seq=0,
        relative_path="audio_assets/audio-meeting/chunks/chunk-00000001.pcm",
        sha256="b" * 64,
        sample_rate_hz=16_000,
        sample_count=16_000,
        duration_ms=1_000,
        file_size_bytes=32_000,
        now_ms=3_000,
    )
    assert persistence.get_snapshot("audio-meeting")["runtime"]["phase"] == "live"


def test_recording_lease_seal_and_export_commit_are_durable(persistence):
    persistence.create_meeting(
        meeting_id="recording-contract",
        title="录音合同",
        now_ms=1_000,
    )
    active = persistence.begin_recording(
        meeting_id="recording-contract",
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="capture-worker",
        lease_ms=1_000,
        now_ms=1_100,
    )

    assert active["status"] == "active"
    assert active["lease_until_ms"] == 2_100
    assert persistence.heartbeat_recording(
        meeting_id="recording-contract",
        track="microphone",
        epoch=0,
        lease_owner="capture-worker",
        lease_ms=1_000,
        now_ms=1_500,
    ) is True

    persistence.record_audio_chunk(
        meeting_id="recording-contract",
        track="microphone",
        epoch=0,
        chunk_seq=0,
        relative_path="audio_assets/recording-contract/chunks/chunk-00000000.pcm",
        sha256="a" * 64,
        sample_rate_hz=16_000,
        sample_count=16_000,
        duration_ms=1_000,
        file_size_bytes=32_000,
        now_ms=1_600,
    )
    sealed = persistence.seal_recording_and_enqueue_export(
        meeting_id="recording-contract",
        track="microphone",
        epoch=0,
        lease_owner="capture-worker",
        output_relative_path="audio_assets/recording-contract/audio.wav",
        interrupted=False,
        now_ms=1_700,
    )

    assert sealed["recording"]["status"] == "sealed"
    assert sealed["recording"]["chunk_count"] == 1
    assert sealed["export"]["status"] == "pending"

    claimed = persistence.claim_next_recording_export(
        worker_id="export-worker",
        now_ms=1_800,
        lease_ms=1_000,
    )
    assert claimed["id"] == sealed["export"]["id"]
    assert claimed["status"] == "running"
    assert persistence.heartbeat_recording_export(
        export_id=claimed["id"],
        worker_id="export-worker",
        now_ms=1_900,
        lease_ms=1_000,
    ) is True

    completed = persistence.complete_recording_export(
        export_id=claimed["id"],
        worker_id="export-worker",
        output={"sha256": "b" * 64, "file_size_bytes": 32_044},
        now_ms=2_000,
    )
    recording = persistence.get_recording_session(
        "recording-contract",
        track="microphone",
        epoch=0,
    )

    assert completed["status"] == "succeeded"
    assert recording["status"] == "ready"
    assert recording["output_sha256"] == "b" * 64
    assert [event["type"] for event in persistence.list_events("recording-contract")][-3:] == [
        "recording.sealed",
        "recording.export.queued",
        "recording.export.ready",
    ]


def test_only_expired_recording_leases_are_recovered_and_exported(persistence):
    for meeting_id, lease_ms in (("expired-recording", 500), ("active-recording", 5_000)):
        persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
        persistence.begin_recording(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner=f"capture-{meeting_id}",
            lease_ms=lease_ms,
            now_ms=1_000,
        )
        persistence.record_audio_chunk(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            chunk_seq=0,
            relative_path=f"audio_assets/{meeting_id}/chunks/chunk-00000000.pcm",
            sha256="c" * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=32_000,
            now_ms=1_100,
        )

    recovered = persistence.recover_expired_recording_leases(now_ms=2_000)

    assert recovered == ["expired-recording"]
    assert persistence.get_recording_session(
        "expired-recording", track="microphone", epoch=0
    )["status"] == "interrupted"
    assert persistence.get_snapshot("expired-recording")["runtime"]["phase"] == "interrupted"
    assert persistence.get_recording_session(
        "active-recording", track="microphone", epoch=0
    )["status"] == "active"
    assert persistence.get_snapshot("active-recording")["runtime"]["phase"] == "live"
    pending = persistence.list_recording_exports(meeting_id="expired-recording")
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"


def test_active_recording_lease_cannot_be_stolen_or_sealed_by_another_owner(persistence):
    persistence.create_meeting(meeting_id="leased-recording", title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id="leased-recording",
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="owner-a",
        lease_ms=1_000,
        now_ms=1_100,
    )

    with pytest.raises(RuntimeError, match="already owned"):
        persistence.begin_recording(
            meeting_id="leased-recording",
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner="owner-b",
            lease_ms=1_000,
            now_ms=1_200,
        )
    with pytest.raises(RuntimeError, match="lease was lost"):
        persistence.record_audio_chunk(
            meeting_id="leased-recording",
            track="microphone",
            epoch=0,
            chunk_seq=0,
            relative_path="audio_assets/leased-recording/chunks/chunk-00000000.pcm",
            sha256="d" * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=32_000,
            lease_owner="owner-b",
            lease_ms=1_000,
            now_ms=1_300,
        )


def test_resumed_recording_requeues_same_export_id_for_new_journal_version(persistence):
    meeting_id = "resumed-export"
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)

    def begin(owner: str, now_ms: int) -> None:
        persistence.begin_recording(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner=owner,
            lease_ms=10_000,
            now_ms=now_ms,
        )

    def chunk(seq: int, owner: str, now_ms: int) -> None:
        persistence.record_audio_chunk(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            chunk_seq=seq,
            relative_path=f"audio_assets/{meeting_id}/chunks/chunk-{seq:08d}.pcm",
            sha256=str(seq + 1) * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=32_000,
            lease_owner=owner,
            lease_ms=10_000,
            now_ms=now_ms,
        )

    def seal_and_complete(owner: str, now_ms: int, output_sha: str) -> dict:
        sealed = persistence.seal_recording_and_enqueue_export(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            lease_owner=owner,
            output_relative_path=f"audio_assets/{meeting_id}/audio.wav",
            interrupted=False,
            now_ms=now_ms,
        )
        claimed = persistence.claim_next_recording_export(
            worker_id="export-worker",
            now_ms=now_ms + 1,
            lease_ms=1_000,
        )
        persistence.complete_recording_export(
            export_id=claimed["id"],
            worker_id="export-worker",
            output={"sha256": output_sha, "file_size_bytes": 32_044},
            now_ms=now_ms + 2,
        )
        return sealed

    begin("owner-a", 1_100)
    chunk(0, "owner-a", 1_200)
    first = seal_and_complete("owner-a", 1_300, "a" * 64)
    begin("owner-b", 2_000)
    chunk(1, "owner-b", 2_100)
    second = seal_and_complete("owner-b", 2_200, "b" * 64)

    assert first["export"]["id"] == second["export"]["id"]
    assert first["export"]["input_journal_sha256"] != second["export"]["input_journal_sha256"]
    completed = persistence.list_recording_exports(meeting_id=meeting_id)[0]
    assert completed["status"] == "succeeded"
    assert completed["attempts"] == 1
    assert completed["input_chunk_count"] == 2
    assert [event["type"] for event in persistence.list_events(meeting_id)].count(
        "recording.export.ready"
    ) == 2


def test_same_epoch_can_expire_twice_without_event_idempotency_collision(persistence):
    meeting_id = "twice-interrupted"
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    for generation, now_ms in (("owner-a", 1_000), ("owner-b", 3_000)):
        recording = persistence.begin_recording(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner=generation,
            lease_ms=500,
            now_ms=now_ms,
        )
        assert recording["status"] == "active"
        assert persistence.recover_expired_recording_leases(now_ms=now_ms + 1_000) == [meeting_id]

    recording = persistence.get_recording_session(meeting_id, track="microphone", epoch=0)
    interrupted = [
        event
        for event in persistence.list_events(meeting_id)
        if event["type"] == "recording.interrupted"
    ]
    assert recording["status"] == "interrupted"
    assert recording["capture_generation"] == 2
    assert len(interrupted) == 2
    assert len({event["idempotency_key"] for event in interrupted}) == 2


def test_late_seal_cannot_downgrade_ready_recording(persistence):
    meeting_id = "late-seal"
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="capture-owner",
        lease_ms=10_000,
        now_ms=1_100,
    )
    persistence.record_audio_chunk(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        chunk_seq=0,
        relative_path=f"audio_assets/{meeting_id}/chunks/chunk-00000000.pcm",
        sha256="e" * 64,
        sample_rate_hz=16_000,
        sample_count=16_000,
        duration_ms=1_000,
        file_size_bytes=32_000,
        lease_owner="capture-owner",
        lease_ms=10_000,
        now_ms=1_200,
    )
    sealed = persistence.seal_recording_and_enqueue_export(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        lease_owner="capture-owner",
        output_relative_path=f"audio_assets/{meeting_id}/audio.wav",
        expected_journal_sha256=None,
        interrupted=False,
        now_ms=1_300,
    )
    claimed = persistence.claim_next_recording_export(
        worker_id="export-worker", now_ms=1_400, lease_ms=1_000
    )
    persistence.complete_recording_export(
        export_id=claimed["id"],
        worker_id="export-worker",
        output={"sha256": "f" * 64, "file_size_bytes": 32_044},
        now_ms=1_500,
    )

    repeated = persistence.seal_recording_and_enqueue_export(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        lease_owner="stale-owner",
        output_relative_path=f"audio_assets/{meeting_id}/audio.wav",
        expected_journal_sha256=sealed["recording"]["journal_sha256"],
        interrupted=True,
        now_ms=2_000,
    )

    assert repeated["recording"]["status"] == "ready"
    assert repeated["export"]["status"] == "succeeded"
    with pytest.raises(RuntimeError, match="terminal recording journal"):
        persistence.seal_recording_and_enqueue_export(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            lease_owner="stale-owner",
            output_relative_path=f"audio_assets/{meeting_id}/audio.wav",
            expected_journal_sha256="0" * 64,
            interrupted=True,
            now_ms=2_100,
        )


def test_durable_deletion_job_purges_meeting_facts_but_keeps_audit_status(persistence):
    _commit_final(persistence)
    deletion = persistence.create_deletion_job(
        meeting_id="meeting-1",
        managed_paths=["audio_assets/meeting-1"],
        now_ms=2_000,
    )
    running = persistence.mark_deletion_running(
        job_id=deletion["id"],
        now_ms=2_100,
    )
    completed = persistence.complete_deletion_and_purge(
        job_id=deletion["id"],
        now_ms=2_200,
    )

    assert running["attempts"] == 1
    assert completed["status"] == "completed"
    assert persistence.get_snapshot("meeting-1")["segments"] == []
    assert persistence.get_snapshot("meeting-1")["runtime"]["phase"] == "unknown"
    assert persistence.list_jobs(meeting_id="meeting-1") == []
    assert persistence.list_events("meeting-1") == []
    assert persistence.list_deletion_jobs() == [completed]


def test_deletion_tombstone_fences_all_late_meeting_and_recording_writes(persistence):
    meeting_id = "tombstoned-meeting"
    persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_000)
    persistence.begin_recording(
        meeting_id=meeting_id,
        track="microphone",
        epoch=0,
        source_type="browser_live_mic",
        sample_rate_hz=16_000,
        lease_owner="capture-owner",
        lease_ms=10_000,
        now_ms=1_100,
    )
    deletion = persistence.create_deletion_job(
        meeting_id=meeting_id,
        managed_paths=[f"audio_assets/{meeting_id}"],
        now_ms=1_200,
    )

    with pytest.raises(RuntimeError, match="deleted"):
        persistence.create_meeting(meeting_id=meeting_id, title=None, now_ms=1_300)
    with pytest.raises(RuntimeError, match="deleted"):
        persistence.commit_final_and_enqueue(
            meeting_id=meeting_id,
            final_id="late-final",
            segment_id="late-segment",
            text="这是删除后的迟到文字。",
            normalized_text="这是删除后的迟到文字。",
            started_at_ms=0,
            ended_at_ms=1_000,
            evidence_hash="late-hash",
            now_ms=1_300,
        )
    with pytest.raises(RuntimeError, match="deleted"):
        persistence.record_audio_chunk(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            chunk_seq=0,
            relative_path=f"audio_assets/{meeting_id}/chunks/chunk-00000000.pcm",
            sha256="a" * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=32_000,
            lease_owner="capture-owner",
            lease_ms=10_000,
            now_ms=1_300,
        )
    with pytest.raises(RuntimeError, match="deleted"):
        persistence.seal_recording_and_enqueue_export(
            meeting_id=meeting_id,
            track="microphone",
            epoch=0,
            lease_owner="capture-owner",
            output_relative_path=f"audio_assets/{meeting_id}/audio.wav",
            interrupted=True,
            now_ms=1_300,
        )

    persistence.mark_deletion_running(job_id=deletion["id"], now_ms=1_400)
    persistence.complete_deletion_and_purge(job_id=deletion["id"], now_ms=1_500)
    assert persistence.is_meeting_tombstoned(meeting_id) is True
    assert persistence.meeting_exists(meeting_id) is False


def test_create_meeting_is_idempotent_and_history_is_normalized(persistence):
    first = persistence.create_meeting(
        meeting_id="meeting-created",
        title="架构评审",
        now_ms=1_000,
    )
    repeated = persistence.create_meeting(
        meeting_id="meeting-created",
        title="不会覆盖已有标题",
        now_ms=2_000,
    )

    assert first["state"] == "live"
    assert repeated["id"] == first["id"]
    assert repeated["title"] == "架构评审"
    assert [event["type"] for event in persistence.list_events("meeting-created")] == [
        "meeting.started"
    ]
    history = persistence.list_meetings()
    assert history == [
        {
            **repeated,
            "segment_count": 0,
            "suggestion_count": 0,
            "audio_duration_ms": 0,
            "has_minutes": False,
        }
    ]

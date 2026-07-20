from __future__ import annotations

import sqlite3

import pytest

from meeting_copilot_web_mvp.v2_persistence import (
    SpeakerAttributionConflict,
    SpeakerAttributionRevisionConflict,
    V2Persistence,
)


@pytest.fixture
def persistence(tmp_path):
    instance = V2Persistence(tmp_path / "meeting_copilot.db")
    yield instance
    instance.close()


def _commit_segment(persistence: V2Persistence) -> dict[str, object]:
    return persistence.commit_final_and_enqueue(
        meeting_id="meeting-diarization",
        final_id="final-1",
        segment_id="segment-1",
        text="原始文字事实不可被说话人归因改写。",
        normalized_text="原始文字事实不可被说话人归因改写。",
        started_at_ms=1_000,
        ended_at_ms=2_000,
        evidence_hash="evidence-1",
        now_ms=1_000,
        enqueue_jobs=False,
    )


def _create_run(persistence: V2Persistence, *, status: str = "running") -> dict[str, object]:
    if not persistence.meeting_exists("meeting-diarization"):
        persistence.create_meeting(meeting_id="meeting-diarization", title=None, now_ms=1_000)
    return persistence.create_or_update_speaker_run(
        meeting_id="meeting-diarization",
        run_id="run-1",
        source="microphone",
        model="local-test",
        status=status,
        now_ms=2_000,
    )


def test_diarization_schema_has_fact_tables_indexes_and_projection_columns(tmp_path):
    database_path = tmp_path / "schema.db"
    persistence = V2Persistence(database_path)
    persistence.close()

    with sqlite3.connect(database_path) as connection:
        for table in ("speaker_runs", "speaker_turns", "speaker_attributions"):
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        speaker_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(meeting_speakers)")
        }
        transcript_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(transcript_segments)")
        }
        assert {"label_source", "label_locked"} <= speaker_columns
        assert {"speaker_attribution_revision", "speaker_attribution_source", "speaker_attribution_reason"} <= transcript_columns
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(speaker_runs)")
        }
        indexes |= {row[1] for row in connection.execute("PRAGMA index_list(speaker_turns)")}
        indexes |= {row[1] for row in connection.execute("PRAGMA index_list(speaker_attributions)")}
        assert {
            "idx_speaker_runs_meeting",
            "idx_speaker_turns_run",
            "idx_speaker_attributions_segment",
        } <= indexes


def test_run_and_turn_replay_is_idempotent_but_conflicting_identity_is_rejected(persistence):
    first_run = _create_run(persistence)
    replayed_run = _create_run(persistence)
    assert replayed_run == first_run

    updated_run = _create_run(persistence, status="completed")
    assert updated_run["status"] == "completed"
    assert updated_run["created_at_ms"] == first_run["created_at_ms"]

    first_turn = persistence.append_speaker_turn(
        meeting_id="meeting-diarization",
        run_id="run-1",
        turn_id="turn-1",
        start_ms=1_000,
        end_ms=1_900,
        cluster_label="cluster-a",
        speaker_id="speaker-a",
        confidence=0.91,
        is_stable=True,
        window_ids=["window-1"],
        now_ms=2_100,
    )
    assert persistence.append_speaker_turn(
        meeting_id="meeting-diarization",
        run_id="run-1",
        turn_id="turn-1",
        start_ms=1_000,
        end_ms=1_900,
        cluster_label="cluster-a",
        speaker_id="speaker-a",
        confidence=0.91,
        is_stable=True,
        window_ids=["window-1"],
        now_ms=2_200,
    ) == first_turn
    with pytest.raises(ValueError, match="conflicting"):
        persistence.append_speaker_turn(
            meeting_id="meeting-diarization",
            run_id="run-1",
            turn_id="turn-1",
            start_ms=1_000,
            end_ms=1_900,
            cluster_label="cluster-b",
            speaker_id="speaker-b",
            confidence=0.91,
            is_stable=True,
            window_ids=["window-1"],
            now_ms=2_300,
        )


def test_segment_attribution_replay_conflict_revision_gate_and_text_fact_immutability(persistence):
    _commit_segment(persistence)
    _create_run(persistence)
    first = persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=1,
        speaker_id="speaker-a",
        confidence=0.88,
        source="diarization",
        reason="attributed",
        now_ms=3_000,
    )
    replay = persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=1,
        speaker_id="speaker-a",
        confidence=0.88,
        source="diarization",
        reason="attributed",
        now_ms=3_100,
    )
    assert replay == first
    with pytest.raises(SpeakerAttributionConflict, match="conflicting"):
        persistence.apply_segment_speaker_attribution(
            meeting_id="meeting-diarization",
            run_id="run-1",
            segment_id="segment-1",
            attribution_revision=1,
            speaker_id="speaker-b",
            confidence=0.88,
            source="diarization",
            reason="attributed",
            now_ms=3_200,
        )
    with pytest.raises(SpeakerAttributionRevisionConflict, match="stale"):
        persistence.apply_segment_speaker_attribution(
            meeting_id="meeting-diarization",
            run_id="run-1",
            segment_id="segment-1",
            attribution_revision=0,
            speaker_id="speaker-b",
            confidence=0.88,
            source="diarization",
            reason="late-replay",
            now_ms=3_300,
        )

    revised = persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=2,
        speaker_id="speaker-b",
        confidence=0.93,
        source="diarization",
        reason="reclustered",
        now_ms=3_400,
    )
    assert revised["attribution_revision"] == 2
    segment = persistence.get_transcript_segment("meeting-diarization", "segment-1")
    assert {
        key: segment[key]
        for key in ("text", "normalized_text", "evidence_hash", "revision")
    } == {
        "text": "原始文字事实不可被说话人归因改写。",
        "normalized_text": "原始文字事实不可被说话人归因改写。",
        "evidence_hash": "evidence-1",
        "revision": 1,
    }
    assert {
        key: segment[key]
        for key in ("speaker_id", "speaker_confidence", "speaker_attribution_revision", "speaker_attribution_source", "speaker_attribution_reason")
    } == {
        "speaker_id": "speaker-b",
        "speaker_confidence": 0.93,
        "speaker_attribution_revision": 2,
        "speaker_attribution_source": "diarization",
        "speaker_attribution_reason": "reclustered",
    }
    assert len([event for event in persistence.list_events("meeting-diarization") if event["type"] == "transcript.segment.speaker_revised"]) == 2
    assert persistence._conn.execute(
        "SELECT COUNT(*) FROM speaker_attributions WHERE meeting_id = ? AND segment_id = ?",
        ("meeting-diarization", "segment-1"),
    ).fetchone()[0] == 2


def test_manual_rename_locks_label_and_auto_attribution_preserves_it_after_reopen(persistence, tmp_path):
    _commit_segment(persistence)
    _create_run(persistence)
    persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=1,
        speaker_id="speaker-a",
        confidence=0.9,
        source="diarization",
        reason="attributed",
        now_ms=3_000,
    )
    persistence.rename_meeting_speaker(
        meeting_id="meeting-diarization",
        speaker_id="speaker-a",
        speaker_label="张工",
        now_ms=3_100,
    )
    assert tuple(persistence._conn.execute(
        "SELECT label_source, label_locked FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
        ("meeting-diarization", "speaker-a"),
    ).fetchone()) == ("user", 1)

    persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=2,
        speaker_id="speaker-a",
        confidence=0.95,
        source="diarization",
        reason="reclustered",
        now_ms=3_200,
    )
    assert persistence.get_transcript_segment("meeting-diarization", "segment-1")["speaker_label"] == "张工"
    persistence.close()

    reopened = V2Persistence(tmp_path / "meeting_copilot.db")
    try:
        snapshot = reopened.get_snapshot("meeting-diarization")
        assert snapshot["segments"][0]["speaker_label"] == "张工"
        assert snapshot["segments"][0]["speaker_attribution_revision"] == 2
        assert tuple(reopened._conn.execute(
            "SELECT label_source, label_locked FROM meeting_speakers WHERE meeting_id = ? AND speaker_id = ?",
            ("meeting-diarization", "speaker-a"),
        ).fetchone()) == ("user", 1)
    finally:
        reopened.close()


@pytest.mark.parametrize("scope", ["derived", "all"])
def test_diarization_rows_and_events_follow_derived_and_all_deletion(scope, persistence):
    _commit_segment(persistence)
    _create_run(persistence)
    persistence.append_speaker_turn(
        meeting_id="meeting-diarization",
        run_id="run-1",
        turn_id="turn-1",
        start_ms=1_000,
        end_ms=1_900,
        cluster_label="cluster-a",
        speaker_id="speaker-a",
        confidence=0.91,
        is_stable=True,
        now_ms=2_100,
    )
    persistence.apply_segment_speaker_attribution(
        meeting_id="meeting-diarization",
        run_id="run-1",
        segment_id="segment-1",
        attribution_revision=1,
        speaker_id="speaker-a",
        confidence=0.91,
        source="diarization",
        reason="attributed",
        now_ms=3_000,
    )
    job = persistence.create_deletion_job(
        meeting_id="meeting-diarization",
        managed_paths=[],
        deletion_scope=scope,
        now_ms=4_000,
        idempotency_key=f"delete:{scope}",
    )
    persistence.mark_deletion_running(job_id=job["id"], now_ms=4_100)
    persistence.complete_deletion_and_purge(job_id=job["id"], now_ms=4_200)

    with persistence._lock:
        for table in ("speaker_runs", "speaker_turns", "speaker_attributions"):
            assert persistence._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE meeting_id = ?",
                ("meeting-diarization",),
            ).fetchone()[0] == 0
    if scope == "derived":
        assert persistence.meeting_exists("meeting-diarization")
        assert persistence.get_transcript_segment("meeting-diarization", "segment-1")["text"] == "原始文字事实不可被说话人归因改写。"
    else:
        assert not persistence.meeting_exists("meeting-diarization")

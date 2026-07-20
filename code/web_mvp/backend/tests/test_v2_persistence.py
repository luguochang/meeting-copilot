from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from meeting_copilot_web_mvp.v2_persistence import (
    IntelligenceProjectionError,
    JobLeaseLostError,
    TranscriptRevisionIdentityConflict,
    V2Persistence,
)


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
        for table in (
            "meetings",
            "meeting_events",
            "jobs",
            "transcript_segments",
            "asr_checkpoints",
            "semantic_paragraphs",
            "semantic_paragraph_checkpoints",
            "suggestions",
        ):
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


@pytest.mark.skipif(os.name == "nt", reason="Windows uses inherited per-user ACLs")
def test_sqlite_database_and_live_sidecars_are_owner_only(tmp_path):
    database_path = tmp_path / "private" / "meeting_copilot.db"
    persistence = V2Persistence(database_path)
    try:
        assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{database_path}{suffix}")
            if sidecar.exists():
                assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600
    finally:
        persistence.close()


def test_old_meeting_entities_check_schema_is_rebuilt_without_losing_rows(tmp_path):
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE meeting_entities ("
            "meeting_id TEXT NOT NULL, entity_id TEXT NOT NULL, "
            "kind TEXT NOT NULL CHECK (kind IN ('current_topic', 'open_question')), "
            "status TEXT NOT NULL, text TEXT NOT NULL, "
            "evidence_segment_ids_json TEXT NOT NULL, updated_at_ms INTEGER, "
            "PRIMARY KEY (meeting_id, entity_id))"
        )
        connection.execute(
            "INSERT INTO meeting_entities VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("meeting-legacy", "question-1", "open_question", "open", "旧问题？", "[\"seg-1\"]", 10),
        )

    persistence = V2Persistence(database_path)
    try:
        with sqlite3.connect(database_path) as connection:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'meeting_entities'"
            ).fetchone()[0]
            assert "decision_candidate" in schema
            assert connection.execute(
                "SELECT text FROM meeting_entities WHERE entity_id = 'question-1'"
            ).fetchone()[0] == "旧问题？"
    finally:
        persistence.close()


def test_new_fact_projection_events_and_confirmation_are_durable(persistence):
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="facts-1",
        segment_id="facts-segment-1",
        text="结论是采用蓝绿发布方案。由李明负责在周五前完成数据库迁移。风险是双写数据不一致，需要先做校验。",
        normalized_text="结论是采用蓝绿发布方案。由李明负责在周五前完成数据库迁移。风险是双写数据不一致，需要先做校验。",
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash="facts-hash-1",
        now_ms=1_000,
    )

    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["decision_candidates"][0]["status"] == "candidate"
    action = snapshot["action_items"][0]
    assert action["owner"] == "李明"
    assert action["deadline"] == "周五"
    risk = snapshot["risks"][0]
    assert risk["mitigation"] == "先做校验"
    event_types = [event["type"] for event in persistence.list_events("meeting-1")]
    assert "meeting.decision.updated" in event_types
    assert "meeting.action_item.updated" in event_types
    assert "meeting.risk.updated" in event_types

    confirmed = persistence.confirm_entity(
        meeting_id="meeting-1",
        entity_id=action["id"],
        now_ms=2_000,
    )
    assert confirmed["status"] == "confirmed"

    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="facts-2",
        segment_id="facts-segment-2",
        text="结论是采用蓝绿发布方案。",
        normalized_text="结论是采用蓝绿发布方案。",
        started_at_ms=1_000,
        ended_at_ms=1_900,
        evidence_hash="facts-hash-2",
        now_ms=3_000,
    )
    retained = next(
        item for item in persistence.get_snapshot("meeting-1")["action_items"]
        if item["id"] == action["id"]
    )
    assert retained["status"] == "confirmed"


def test_llm_first_mode_does_not_project_keywords_and_applies_structured_response_once(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first.db",
        semantic_projection_mode="llm_first",
    )
    try:
        committed = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="llm-first-final",
            segment_id="llm-first-segment",
            text="结论是采用蓝绿发布方案。由李明负责在周五前完成迁移。",
            normalized_text="结论是采用蓝绿发布方案。由李明负责在周五前完成迁移。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="llm-first-hash",
            now_ms=1_000,
        )
        assert persistence.get_snapshot("meeting-1")["decision_candidates"] == []
        assert persistence.get_snapshot("meeting-1")["action_items"] == []
        assert "intelligence" in committed["job_ids"]

        response = {
            "paragraph_revisions": [
                {
                    "target_id": "llm-first-segment",
                    "expected_revision": 1,
                    "corrected_text": "结论是采用蓝绿发布方案。由李明负责在周五前完成迁移。",
                    "change_count": 0,
                    "changed": False,
                }
            ],
            "topic_update": {
                "operation": "update",
                "title": "蓝绿发布",
                "summary": "确认发布方案和负责人。",
            },
            "state_changes": [
                {
                    "type": "decision",
                    "operation": "add",
                    "item_id": "decision:blue-green",
                    "content": "采用蓝绿发布方案",
                    "owner": None,
                    "deadline": None,
                    "status": "candidate",
                    "evidence_segment_ids": ["llm-first-segment"],
                    "evidence_quote": "结论是采用蓝绿发布方案",
                    "confidence": 0.95,
                }
            ],
            "follow_up": {
                "question": "建议确认发布窗口。",
                "reason": "发布窗口尚未出现在原话中。",
                "evidence_segment_ids": ["llm-first-segment"],
                "evidence_quote": "采用蓝绿发布方案",
                "urgency": "medium",
            },
        }
        applied = persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=committed["job_ids"]["intelligence"],
            response=response,
            now_ms=2_000,
        )
        assert applied["state_change_count"] == 1
        assert persistence.get_snapshot("meeting-1")["decision_candidates"][0]["id"] == "decision:blue-green"
        assert persistence.get_snapshot("meeting-1")["current_topic"]["text"] == "蓝绿发布"
        assert persistence.get_snapshot("meeting-1")["follow_up"] == response["follow_up"]
        assert persistence.get_snapshot("meeting-1")["segments"][0]["correction_status"] == "no_change"
        second = persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=committed["job_ids"]["intelligence"],
            response=response,
            now_ms=3_000,
        )
        assert second["idempotent"] is True
        event_keys = [event["idempotency_key"] for event in persistence.list_events("meeting-1")]
        assert event_keys.count(f"meeting.intelligence.applied:{committed['job_ids']['intelligence']}") == 1
    finally:
        persistence.close()


def test_llm_first_evidence_quote_matches_normalized_text_sent_to_model(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-normalized-evidence.db",
        semantic_projection_mode="llm_first",
    )
    try:
        committed = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="normalized-evidence-final",
            segment_id="normalized-evidence-segment",
            text="我们先挥百分之五验证发布流程。",
            normalized_text="我们灰度百分之五验证发布流程。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="normalized-evidence-hash",
            now_ms=1_000,
        )
        response = {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [
                {
                    "type": "decision",
                    "operation": "add",
                    "item_id": "decision:normalized-evidence",
                    "content": "先灰度百分之五",
                    "owner": None,
                    "deadline": None,
                    "status": "candidate",
                    "evidence_segment_ids": ["normalized-evidence-segment"],
                    "evidence_quote": "我们灰度百分之五验证发布流程",
                    "confidence": 0.95,
                }
            ],
            "follow_up": None,
        }

        applied = persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=committed["job_ids"]["intelligence"],
            response=response,
            now_ms=2_000,
        )

        assert applied["state_change_count"] == 1
        assert persistence.get_snapshot("meeting-1")["decision_candidates"][0]["id"] == (
            "decision:normalized-evidence"
        )
    finally:
        persistence.close()


def test_llm_first_add_deduplicates_identical_facts_and_merges_evidence(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-fact-deduplication.db",
        semantic_projection_mode="llm_first",
    )
    try:
        first = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="dedupe-final-1",
            segment_id="dedupe-segment-1",
            text="我们先进行小比例灰度验证。",
            normalized_text="我们先进行小比例灰度验证。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="dedupe-hash-1",
            now_ms=1_000,
        )
        persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=first["job_ids"]["intelligence"],
            response={
                "paragraph_revisions": [],
                "topic_update": None,
                "state_changes": [{
                    "type": "decision",
                    "operation": "add",
                    "item_id": "decision:first",
                    "content": "先进行小比例灰度验证，异常时立即回滚。",
                    "status": "candidate",
                    "evidence_segment_ids": ["dedupe-segment-1"],
                    "evidence_quote": "小比例灰度验证",
                    "confidence": 0.9,
                }],
                "follow_up": None,
            },
            now_ms=2_000,
        )
        persistence.confirm_entity(
            meeting_id="meeting-1",
            entity_id="decision:first",
            now_ms=2_500,
        )

        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="dedupe-final-2",
            segment_id="dedupe-segment-2",
            text="异常时立即回滚，监控负责人需要确认。",
            normalized_text="异常时立即回滚，监控负责人需要确认。",
            started_at_ms=4_000,
            ended_at_ms=4_900,
            evidence_hash="dedupe-hash-2",
            now_ms=5_000,
        )
        persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=second["job_ids"]["intelligence"],
            response={
                "paragraph_revisions": [],
                "topic_update": None,
                "state_changes": [{
                    "type": "decision",
                    "operation": "add",
                    "item_id": "decision:second",
                    "content": "  先进行小比例灰度验证，异常时立即回滚 ",
                    "status": "candidate",
                    "evidence_segment_ids": ["dedupe-segment-2"],
                    "evidence_quote": "异常时立即回滚",
                    "confidence": 0.94,
                }],
                "follow_up": None,
            },
            now_ms=6_000,
        )

        decisions = persistence.get_snapshot("meeting-1")["decision_candidates"]
        assert len(decisions) == 1
        assert decisions[0]["id"] == "decision:first"
        assert decisions[0]["status"] == "confirmed"
        assert decisions[0]["evidence_segment_ids"] == ["dedupe-segment-1", "dedupe-segment-2"]
        event = next(
            item for item in reversed(persistence.list_events("meeting-1"))
            if item["type"] == "meeting.decision.updated" and item["causation_id"] == second["job_ids"]["intelligence"]
        )
        assert event["aggregate_id"] == "decision:first"
        assert event["payload"]["deduplicated"] is True
        assert event["payload"]["requested_item_id"] == "decision:second"
    finally:
        persistence.close()


def test_semantic_paragraph_projection_merges_checkpoints_and_is_idempotent(persistence):
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="paragraph-final-1",
        segment_id="paragraph-segment-1",
        text="先确认发布窗口。",
        normalized_text="先确认发布窗口。",
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash="paragraph-hash-1",
        now_ms=1_000,
    )
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="paragraph-final-2",
        segment_id="paragraph-segment-2",
        text="再确认回滚负责人。",
        normalized_text="再确认回滚负责人。",
        started_at_ms=1_000,
        ended_at_ms=1_700,
        evidence_hash="paragraph-hash-2",
        now_ms=2_000,
    )
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="paragraph-final-3",
        segment_id="paragraph-segment-3",
        text="下一段讨论监控告警。",
        normalized_text="下一段讨论监控告警。",
        started_at_ms=4_000,
        ended_at_ms=4_700,
        evidence_hash="paragraph-hash-3",
        now_ms=5_000,
    )

    snapshot = persistence.get_snapshot("meeting-1")
    paragraphs = snapshot["semantic_paragraphs"]
    assert len(paragraphs) == 2
    assert paragraphs[0]["status"] == "stable"
    assert paragraphs[0]["checkpoint_ids"] == ["paragraph-segment-1", "paragraph-segment-2"]
    assert paragraphs[0]["text"] == "先确认发布窗口。再确认回滚负责人。"
    assert snapshot["active_paragraph"]["checkpoint_ids"] == ["paragraph-segment-3"]

    duplicate = persistence.commit_final_and_enqueue(
        meeting_id="meeting-1",
        final_id="paragraph-final-3",
        segment_id="paragraph-segment-3",
        text="下一段讨论监控告警。",
        normalized_text="下一段讨论监控告警。",
        started_at_ms=4_000,
        ended_at_ms=4_700,
        evidence_hash="paragraph-hash-3",
        now_ms=6_000,
    )
    assert duplicate["created"] is False
    assert len(persistence.get_snapshot("meeting-1")["semantic_paragraphs"]) == 2


def test_llm_first_coalesces_finals_into_one_debounced_paragraph_job(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-batch.db",
        semantic_projection_mode="llm_first",
    )
    try:
        first = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="batch-final-1",
            segment_id="batch-segment-1",
            text="先确认发布窗口。",
            normalized_text="先确认发布窗口。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="batch-hash-1",
            now_ms=1_000,
        )
        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="batch-final-2",
            segment_id="batch-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="batch-hash-2",
            now_ms=2_000,
        )

        intelligence_jobs = [
            job for job in persistence.list_jobs(meeting_id="meeting-1")
            if job["kind"] == "intelligence"
        ]
        assert len(intelligence_jobs) == 1
        assert second["job_ids"]["intelligence"] == first["job_ids"]["intelligence"]
        assert intelligence_jobs[0]["evidence_segment_id"] == "batch-segment-2"
        assert intelligence_jobs[0]["input_version"] == 2
        assert intelligence_jobs[0]["deadline_at_ms"] == 9_000
        assert persistence.claim_next_job(
            worker_id="worker-1",
            lane="intelligence",
            now_ms=3_999,
            lease_ms=5_000,
        ) is None
        claimed = persistence.claim_next_job(
            worker_id="worker-1",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=5_000,
        )
        assert claimed is not None
        assert claimed["evidence_segment_id"] == "batch-segment-2"
    finally:
        persistence.close()


def test_llm_first_debounce_never_moves_past_the_first_enqueue_max_wait(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-max-wait.db",
        semantic_projection_mode="llm_first",
    )
    try:
        intelligence_job_id = None
        for index, now_ms in enumerate((1_000, 2_500, 4_000, 5_500, 7_000, 8_500), start=1):
            committed = persistence.commit_final_and_enqueue(
                meeting_id="meeting-1",
                final_id=f"max-wait-final-{index}",
                segment_id=f"max-wait-segment-{index}",
                text=f"连续发言第 {index} 段。",
                normalized_text=f"连续发言第 {index} 段。",
                started_at_ms=index * 1_000,
                ended_at_ms=index * 1_000 + 500,
                evidence_hash=f"max-wait-hash-{index}",
                now_ms=now_ms,
            )
            intelligence_job_id = intelligence_job_id or committed["job_ids"]["intelligence"]
            assert committed["job_ids"]["intelligence"] == intelligence_job_id

        intelligence_job = persistence.get_job(str(intelligence_job_id))
        assert intelligence_job["input_transcript_seq"] == 6
        assert intelligence_job["input_version"] == 6
        assert intelligence_job["deadline_at_ms"] == 9_000
        assert intelligence_job["next_attempt_at_ms"] == 9_000
        assert persistence.claim_next_job(
            worker_id="max-wait-worker",
            lane="intelligence",
            now_ms=8_999,
            lease_ms=5_000,
        ) is None
        claimed = persistence.claim_next_job(
            worker_id="max-wait-worker",
            lane="intelligence",
            now_ms=9_000,
            lease_ms=5_000,
        )
        assert claimed is not None
        assert claimed["id"] == intelligence_job_id
    finally:
        persistence.close()


def test_llm_first_creates_successor_job_for_new_final_after_terminal_batch(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-successor.db",
        semantic_projection_mode="llm_first",
    )
    try:
        first = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="successor-final-1",
            segment_id="successor-segment-1",
            text="先确认发布窗口。",
            normalized_text="先确认发布窗口。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="successor-hash-1",
            now_ms=1_000,
        )
        claimed = persistence.claim_next_job(
            worker_id="successor-worker",
            lane="intelligence",
            now_ms=3_100,
            lease_ms=5_000,
        )
        assert claimed is not None
        completed = persistence.complete_job(
            job_id=claimed["id"],
            worker_id="successor-worker",
            now_ms=3_200,
            output={"paragraph_revisions": []},
        )
        assert completed is not None

        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="successor-final-2",
            segment_id="successor-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="successor-hash-2",
            now_ms=4_000,
        )

        assert second["job_ids"]["intelligence"] != first["job_ids"]["intelligence"]
        jobs = [
            job for job in persistence.list_jobs(meeting_id="meeting-1")
            if job["kind"] == "intelligence"
        ]
        assert [job["status"] for job in jobs] == ["succeeded", "pending"]
        assert jobs[-1]["input_version"] == 2
        assert jobs[-1]["evidence_segment_id"] == "successor-segment-2"
    finally:
        persistence.close()


def test_llm_first_creates_pending_successor_when_previous_batch_is_running(tmp_path):
    persistence = V2Persistence(
        tmp_path / "llm-first-running-successor.db",
        semantic_projection_mode="llm_first",
    )
    try:
        first = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="running-final-1",
            segment_id="running-segment-1",
            text="先确认发布窗口。",
            normalized_text="先确认发布窗口。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="running-hash-1",
            now_ms=1_000,
        )
        claimed = persistence.claim_next_job(
            worker_id="running-worker",
            lane="intelligence",
            now_ms=3_100,
            lease_ms=8_000,
        )
        assert claimed is not None

        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="running-final-2",
            segment_id="running-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="running-hash-2",
            now_ms=3_200,
        )

        assert second["job_ids"]["intelligence"] != first["job_ids"]["intelligence"]
        jobs = [
            job for job in persistence.list_jobs(meeting_id="meeting-1")
            if job["kind"] == "intelligence"
        ]
        assert [job["status"] for job in jobs] == ["running", "pending"]
        assert jobs[-1]["input_version"] == 2
    finally:
        persistence.close()


def test_intelligence_job_status_preserves_original_text_on_terminal_failure(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-failure.db",
        semantic_projection_mode="llm_first",
    )
    try:
        persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="failure-final",
            segment_id="failure-segment",
            text="保留这段原始文字。",
            normalized_text="保留这段原始文字。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="failure-hash",
            now_ms=1_000,
        )
        claimed = persistence.claim_next_job(
            worker_id="worker-1",
            lane="intelligence",
            now_ms=3_100,
            lease_ms=5_000,
        )
        assert claimed is not None
        assert persistence.get_snapshot("meeting-1")["segments"][0]["correction_status"] == "processing"

        failed = persistence.fail_job(
            job_id=claimed["id"],
            worker_id="worker-1",
            now_ms=1_200,
            error_class="TimeoutError",
        )
        segment = persistence.get_snapshot("meeting-1")["segments"][0]
        assert failed is not None
        assert segment["normalized_text"] == "保留这段原始文字。"
        assert segment["correction_status"] == "failed_preserved_original"
        assert segment["correction_error_class"] == "TimeoutError"
    finally:
        persistence.close()


def test_llm_first_status_tracks_every_checkpoint_in_the_active_paragraph(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-paragraph-status.db",
        semantic_projection_mode="llm_first",
    )
    try:
        persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="paragraph-status-1",
            segment_id="paragraph-status-segment-1",
            text="先确认发布窗口。",
            normalized_text="先确认发布窗口。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="paragraph-status-hash-1",
            now_ms=1_000,
        )
        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="paragraph-status-2",
            segment_id="paragraph-status-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="paragraph-status-hash-2",
            now_ms=2_000,
        )
        claimed = persistence.claim_next_job(
            worker_id="paragraph-status-worker",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=5_000,
        )
        assert claimed is not None
        assert claimed["id"] == second["job_ids"]["intelligence"]
        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"processing"}

        failed = persistence.fail_job(
            job_id=claimed["id"],
            worker_id="paragraph-status-worker",
            now_ms=4_100,
            error_class="TimeoutError",
        )
        assert failed is not None
        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"failed_preserved_original"}
    finally:
        persistence.close()


def test_intelligence_success_marks_every_covered_checkpoint_terminal(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-success-status.db",
        semantic_projection_mode="llm_first",
    )
    try:
        persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="success-status-1",
            segment_id="success-status-segment-1",
            text="先灰度百分之无。",
            normalized_text="先灰度百分之无。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="success-status-hash-1",
            now_ms=1_000,
        )
        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="success-status-2",
            segment_id="success-status-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="success-status-hash-2",
            now_ms=2_000,
        )
        claimed = persistence.claim_next_job(
            worker_id="success-status-worker",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=5_000,
        )
        assert claimed is not None

        persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=second["job_ids"]["intelligence"],
            response={
                "paragraph_revisions": [
                    {
                        "target_id": "success-status-segment-1",
                        "expected_revision": 1,
                        "corrected_text": "先灰度百分之五。",
                        "change_count": 1,
                        "changed": True,
                    }
                ],
                "topic_update": None,
                "state_changes": [],
                "follow_up": None,
            },
            now_ms=4_100,
        )
        completed = persistence.complete_job(
            job_id=claimed["id"],
            worker_id="success-status-worker",
            now_ms=4_200,
            output={"applied": True},
        )

        assert completed is not None
        assert completed["status"] == "succeeded"
        segments = persistence.get_snapshot("meeting-1")["segments"]
        assert [segment["correction_status"] for segment in segments] == ["changed", "no_change"]
    finally:
        persistence.close()


def test_llm_first_compatibility_correction_job_cannot_clobber_intelligence_status(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-correction-lane-status.db",
        semantic_projection_mode="llm_first",
    )
    try:
        for index in (1, 2):
            persistence.commit_final_and_enqueue(
                meeting_id="meeting-1",
                final_id=f"lane-status-final-{index}",
                segment_id=f"lane-status-segment-{index}",
                text=f"待检查内容 {index}。",
                normalized_text=f"待检查内容 {index}。",
                started_at_ms=index * 1_000,
                ended_at_ms=index * 1_000 + 500,
                evidence_hash=f"lane-status-hash-{index}",
                now_ms=index * 1_000,
            )
        intelligence_claim = persistence.claim_next_job(
            worker_id="lane-status-intelligence-worker",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=5_000,
        )
        correction_claim = persistence.claim_next_job(
            worker_id="lane-status-correction-worker",
            lane="correction",
            now_ms=4_050,
            lease_ms=5_000,
        )
        assert intelligence_claim is not None
        assert correction_claim is not None

        persistence.complete_job(
            job_id=correction_claim["id"],
            worker_id="lane-status-correction-worker",
            now_ms=4_100,
            output={"skipped": True, "reason": "llm_first_intelligence_lane"},
        )

        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"processing"}
        persistence.complete_job(
            job_id=intelligence_claim["id"],
            worker_id="lane-status-intelligence-worker",
            now_ms=4_200,
            output={"applied": True},
        )
        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"no_change"}
    finally:
        persistence.close()


def test_intelligence_idempotent_completion_restores_status_after_expired_lease(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-expired-lease-status.db",
        semantic_projection_mode="llm_first",
    )
    try:
        persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="expired-status-1",
            segment_id="expired-status-segment-1",
            text="先灰度百分之无。",
            normalized_text="先灰度百分之无。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="expired-status-hash-1",
            now_ms=1_000,
        )
        second = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="expired-status-2",
            segment_id="expired-status-segment-2",
            text="再确认回滚负责人。",
            normalized_text="再确认回滚负责人。",
            started_at_ms=1_000,
            ended_at_ms=1_700,
            evidence_hash="expired-status-hash-2",
            now_ms=2_000,
        )
        response = {
            "paragraph_revisions": [
                {
                    "target_id": "expired-status-segment-1",
                    "expected_revision": 1,
                    "corrected_text": "先灰度百分之五。",
                    "change_count": 1,
                    "changed": True,
                }
            ],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        }
        first_claim = persistence.claim_next_job(
            worker_id="expired-status-worker-1",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=100,
        )
        assert first_claim is not None
        persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=second["job_ids"]["intelligence"],
            response=response,
            now_ms=4_050,
        )

        assert persistence.recover_expired_leases(now_ms=4_100) == 1
        recovered = persistence.get_job(first_claim["id"])
        assert recovered["status"] == "retry_wait"
        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"pending"}

        second_claim = persistence.claim_next_job(
            worker_id="expired-status-worker-2",
            lane="intelligence",
            now_ms=4_100,
            lease_ms=1_000,
        )
        assert second_claim is not None
        assert {
            segment["correction_status"]
            for segment in persistence.get_snapshot("meeting-1")["segments"]
        } == {"processing"}
        repeated = persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=second["job_ids"]["intelligence"],
            response=response,
            now_ms=4_200,
        )
        assert repeated["idempotent"] is True
        completed = persistence.complete_job(
            job_id=second_claim["id"],
            worker_id="expired-status-worker-2",
            now_ms=4_300,
            output={"applied": repeated},
        )

        assert completed is not None
        assert completed["status"] == "succeeded"
        segments = persistence.get_snapshot("meeting-1")["segments"]
        assert [segment["correction_status"] for segment in segments] == ["changed", "no_change"]
    finally:
        persistence.close()


def test_intelligence_retry_accepts_its_own_revision_before_applied_event(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-partial-commit-retry.db",
        semantic_projection_mode="llm_first",
    )
    try:
        committed = persistence.commit_final_and_enqueue(
            meeting_id="meeting-1",
            final_id="partial-commit-final",
            segment_id="partial-commit-segment",
            text="先灰度百分之无。",
            normalized_text="先灰度百分之无。",
            started_at_ms=100,
            ended_at_ms=900,
            evidence_hash="partial-commit-hash",
            now_ms=1_000,
        )
        revision = {
            "target_id": "partial-commit-segment",
            "expected_revision": 1,
            "corrected_text": "先灰度百分之五。",
            "change_count": 1,
            "changed": True,
        }
        with pytest.raises(IntelligenceProjectionError, match="evidence quote is not present"):
            persistence.apply_intelligence_response(
                meeting_id="meeting-1",
                job_id=committed["job_ids"]["intelligence"],
                response={
                    "paragraph_revisions": [revision],
                    "topic_update": None,
                    "state_changes": [
                        {
                            "type": "decision",
                            "operation": "add",
                            "item_id": "decision:invalid-evidence",
                            "content": "采用全量发布",
                            "owner": None,
                            "deadline": None,
                            "status": "candidate",
                            "evidence_segment_ids": ["partial-commit-segment"],
                            "evidence_quote": "原文中不存在的证据",
                            "confidence": 0.9,
                        }
                    ],
                    "follow_up": None,
                },
                now_ms=3_100,
            )
        segment_after_failure = persistence.get_snapshot("meeting-1")["segments"][0]
        assert segment_after_failure["revision"] == 2
        assert segment_after_failure["correction_status"] == "changed"
        assert "meeting.intelligence.applied" not in {
            event["type"] for event in persistence.list_events("meeting-1")
        }

        applied = persistence.apply_intelligence_response(
            meeting_id="meeting-1",
            job_id=committed["job_ids"]["intelligence"],
            response={
                "paragraph_revisions": [revision],
                "topic_update": None,
                "state_changes": [],
                "follow_up": None,
            },
            now_ms=3_200,
        )

        assert applied["idempotent"] is False
        assert applied["revision_count"] == 1
        segment_after_retry = persistence.get_snapshot("meeting-1")["segments"][0]
        assert segment_after_retry["revision"] == 2
        assert [
            event["type"] for event in persistence.list_events("meeting-1")
        ].count("transcript.segment.revised") == 1
    finally:
        persistence.close()


def test_intelligence_expired_final_lease_fails_every_covered_checkpoint(tmp_path):
    persistence = V2Persistence(
        tmp_path / "intelligence-expired-final-lease.db",
        semantic_projection_mode="llm_first",
    )
    try:
        for index in (1, 2):
            persistence.commit_final_and_enqueue(
                meeting_id="meeting-1",
                final_id=f"expired-final-{index}",
                segment_id=f"expired-final-segment-{index}",
                text=f"待检查内容 {index}。",
                normalized_text=f"待检查内容 {index}。",
                started_at_ms=index * 1_000,
                ended_at_ms=index * 1_000 + 500,
                evidence_hash=f"expired-final-hash-{index}",
                now_ms=index * 1_000,
                max_attempts=1,
            )
        claimed = persistence.claim_next_job(
            worker_id="expired-final-worker",
            lane="intelligence",
            now_ms=4_000,
            lease_ms=100,
        )
        assert claimed is not None

        assert persistence.recover_expired_leases(now_ms=4_100) == 1
        failed = persistence.get_job(claimed["id"])
        assert failed["status"] == "failed"
        assert failed["error_class"] == "lease_expired"
        segments = persistence.get_snapshot("meeting-1")["segments"]
        assert {segment["correction_status"] for segment in segments} == {
            "failed_preserved_original"
        }
        assert {segment["correction_error_class"] for segment in segments} == {"lease_expired"}
    finally:
        persistence.close()


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


def test_running_correction_for_superseded_evidence_cannot_complete(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="stale-correction-worker",
        lane="correction",
        now_ms=1_100,
        lease_ms=5_000,
    )
    assert claimed is not None

    replacement = persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id=committed["segment_id"],
        expected_evidence_hash="hash-final-1",
        corrected_text="我们需要确认发布负责人以及回滚时间。",
        revision_id="replacement-revision-1",
        now_ms=1_200,
        causation_id="replacement-correction-job",
    )
    assert replacement is not None

    superseded = persistence.supersede_correction_jobs(
        meeting_id="meeting-1",
        segment_ids=[committed["segment_id"]],
        except_job_id="replacement-correction-job",
        now_ms=1_300,
    )

    assert superseded == 1
    stale_job = persistence.get_job(claimed["id"])
    assert stale_job["status"] == "cancelled"
    assert stale_job["error_class"] == "evidence_superseded"
    assert persistence.complete_job(
        job_id=claimed["id"],
        worker_id="stale-correction-worker",
        now_ms=1_400,
        output={"no_revision_needed": True},
    ) is None
    current = persistence.get_transcript_segment("meeting-1", committed["segment_id"])
    assert current["normalized_text"] == "我们需要确认发布负责人以及回滚时间。"
    assert current["correction_status"] == "changed"


def test_successful_correction_for_old_evidence_is_marked_superseded(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="successful-old-correction",
        lane="correction",
        now_ms=1_100,
        lease_ms=5_000,
    )
    assert claimed is not None
    completed = persistence.complete_job(
        job_id=claimed["id"],
        worker_id="successful-old-correction",
        now_ms=1_200,
        output={"no_revision_needed": True},
    )
    assert completed is not None
    assert completed["status"] == "succeeded"

    replacement = persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id=committed["segment_id"],
        expected_evidence_hash="hash-final-1",
        corrected_text="我们需要确认发布负责人以及回滚时间。",
        revision_id="replacement-after-success",
        now_ms=1_300,
        causation_id="replacement-correction-job",
    )
    assert replacement is not None

    assert persistence.supersede_correction_jobs(
        meeting_id="meeting-1",
        segment_ids=[committed["segment_id"]],
        except_job_id="replacement-correction-job",
        now_ms=1_400,
    ) == 1
    superseded = persistence.get_job(claimed["id"])
    assert superseded["status"] == "cancelled"
    assert superseded["error_class"] == "evidence_superseded"


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


@pytest.mark.parametrize(
    ("failed_lane", "stored_error_class", "public_error_class"),
    [
        ("correction", "ConnectionError", "ConnectionError"),
        ("suggestion", "PrivateProviderError: sk-sensitive", "job_failed"),
    ],
)
def test_runtime_ai_fails_closed_after_generation_job_terminal_failure(
    persistence,
    failed_lane,
    stored_error_class,
    public_error_class,
):
    _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="failed-worker",
        lane=failed_lane,
        now_ms=1_100,
        lease_ms=1_000,
    )
    persistence.fail_job(
        job_id=claimed["id"],
        worker_id="failed-worker",
        now_ms=1_200,
        error_class=stored_error_class,
    )

    assert persistence.get_snapshot("meeting-1")["runtime"]["ai"] == {
        "state": "busy",
        "label": "AI 正在处理",
        "error_class": None,
    }

    remaining_lane = "suggestion" if failed_lane == "correction" else "correction"
    remaining = persistence.claim_next_job(
        worker_id="success-worker",
        lane=remaining_lane,
        now_ms=1_300,
        lease_ms=1_000,
    )
    persistence.complete_job(
        job_id=remaining["id"],
        worker_id="success-worker",
        now_ms=1_400,
        output={"ok": True},
    )

    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["runtime"]["ai"] == {
        "state": "error",
        "label": "AI 处理失败",
        "error_class": public_error_class,
    }
    failed_summary = next(job for job in snapshot["jobs"] if job["id"] == claimed["id"])
    assert failed_summary["error_class"] == public_error_class
    assert stored_error_class not in json.dumps(snapshot, ensure_ascii=False) or (
        stored_error_class == public_error_class
    )
    assert persistence.get_job(claimed["id"])["error_class"] == stored_error_class


def test_suggestion_retry_wait_keeps_draft_and_terminal_failure_rejects_with_event(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="suggestion-worker-1",
        lane="suggestion",
        now_ms=1_100,
        lease_ms=1_000,
    )
    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="terminal-failure-draft",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id=str(claimed["generation_id"]),
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="建议确认发布负责人。",
        draft_seq=1,
        now_ms=1_150,
        lease_owner="suggestion-worker-1",
    )

    for attempt, (worker_id, now_ms) in enumerate(
        (("suggestion-worker-1", 1_200), ("suggestion-worker-2", 1_400), ("suggestion-worker-3", 1_600)),
        start=1,
    ):
        if attempt > 1:
            claimed = persistence.claim_next_job(
                worker_id=worker_id,
                lane="suggestion",
                now_ms=now_ms - 100,
                lease_ms=1_000,
            )
        result = persistence.retry_job(
            job_id=claimed["id"],
            worker_id=worker_id,
            now_ms=now_ms,
            next_attempt_at_ms=now_ms + 100,
            error_class="ConnectionError",
        )
        expected_status = "failed" if attempt == 3 else "retry_wait"
        assert result["status"] == expected_status
        expected_suggestion_status = "rejected" if attempt == 3 else "draft"
        assert persistence.get_suggestion(suggestion["suggestion_id"])["status"] == expected_suggestion_status
        rejected_events = [
            event
            for event in persistence.list_events("meeting-1")
            if event["type"] == "suggestion.rejected"
        ]
        assert len(rejected_events) == (1 if attempt == 3 else 0)

    rejected = rejected_events[0]
    assert rejected["aggregate_id"] == suggestion["suggestion_id"]
    assert rejected["causation_id"] == claimed["id"]
    assert rejected["payload"]["status"] == "rejected"
    assert rejected["payload"]["error_class"] == "ConnectionError"


def test_suggestion_terminal_failure_and_rejection_event_are_atomic(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="suggestion-worker",
        lane="suggestion",
        now_ms=1_100,
        lease_ms=1_000,
    )
    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="atomic-failure-draft",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id=str(claimed["generation_id"]),
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="建议确认回滚窗口。",
        draft_seq=1,
        now_ms=1_150,
        lease_owner="suggestion-worker",
    )
    with persistence._lock:
        persistence._conn.execute(
            "CREATE TRIGGER fail_suggestion_rejected_event "
            "BEFORE INSERT ON meeting_events WHEN NEW.type = 'suggestion.rejected' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic rejection event failure'); END"
        )

    with pytest.raises(sqlite3.DatabaseError, match="synthetic rejection event failure"):
        persistence.fail_job(
            job_id=claimed["id"],
            worker_id="suggestion-worker",
            now_ms=1_200,
            error_class="NonRetryableProviderError",
        )

    assert persistence.get_job(claimed["id"])["status"] == "running"
    assert persistence.get_suggestion(suggestion["suggestion_id"])["status"] == "draft"


@pytest.mark.parametrize(
    ("deferred_error_class", "stored_error_class"),
    [
        ("provider_not_synced", "provider_not_synced"),
        ("ProviderRuntimeNotConfiguredDeferred", "ProviderRuntimeNotConfiguredDeferred"),
        ("PrivateProviderError: sk-sensitive", "job_failed"),
    ],
)
def test_defer_job_preserves_attempt_budget_schedule_and_suggestion_draft(
    persistence,
    deferred_error_class,
    stored_error_class,
):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="provider-wait-worker",
        lane="suggestion",
        now_ms=1_100,
        lease_ms=1_000,
    )
    suggestion = persistence.upsert_suggestion_draft(
        suggestion_id="provider-wait-draft",
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["suggestion"],
        generation_id=str(claimed["generation_id"]),
        evidence_segment_id="segment-1",
        evidence_transcript_seq=1,
        evidence_hash="hash-final-1",
        state_revision=1,
        draft_text="等待 Provider 连接后继续。",
        draft_seq=1,
        now_ms=1_150,
        lease_owner="provider-wait-worker",
    )

    deferred = persistence.defer_job(
        job_id=claimed["id"],
        worker_id="provider-wait-worker",
        now_ms=1_200,
        next_attempt_at_ms=5_000,
        error_class=deferred_error_class,
    )

    assert deferred["status"] == "retry_wait"
    assert deferred["attempts"] == 0
    assert deferred["next_attempt_at_ms"] == 5_000
    assert deferred["error_class"] == stored_error_class
    assert deferred["lease_owner"] is None
    assert persistence.get_suggestion(suggestion["suggestion_id"])["status"] == "draft"
    assert persistence.claim_next_job(
        worker_id="too-early-worker",
        lane="suggestion",
        now_ms=4_999,
        lease_ms=1_000,
    ) is None
    reclaimed = persistence.claim_next_job(
        worker_id="provider-ready-worker",
        lane="suggestion",
        now_ms=5_000,
        lease_ms=1_000,
    )
    assert reclaimed["id"] == claimed["id"]
    assert reclaimed["attempts"] == 1


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
                "source_track": None,
                "duplicate_of_segment_id": None,
                "source_duplicate_similarity": None,
                "speaker_id": None,
                "speaker_label": None,
                "speaker_confidence": None,
                "speaker_attribution_revision": 0,
                "speaker_attribution_source": None,
                "speaker_attribution_reason": None,
                "revision": 1,
            "evidence_hash": "hash-final-1",
            "correction_status": "pending",
            "correction_before_text": None,
            "correction_after_text": None,
            "correction_error_class": None,
            "correction_updated_at_ms": None,
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
    with pytest.raises(TranscriptRevisionIdentityConflict, match="idempotency key conflicts"):
        persistence.commit_transcript_revision(
            meeting_id="meeting-1",
            segment_id="segment-1",
            expected_evidence_hash="hash-final-1",
            corrected_text="同一个修订标识不能指向另一份文字。",
            revision_id="revision-1",
            now_ms=2_150,
        )
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
    correction_jobs = persistence.list_jobs(meeting_id="meeting-1", lane="correction")
    assert [job["id"] for job in correction_jobs] == [committed["job_ids"]["correction"]]
    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["runtime"]["phase"] == "ended"
    assert snapshot["suggestions"][0]["feedback"] == "kept"
    assert snapshot["active_paragraph"] is None
    assert snapshot["semantic_paragraphs"][0]["status"] == "stable"
    assert [event["type"] for event in persistence.list_events("meeting-1")][-2:] == [
        "suggestion.feedback.updated",
        "meeting.ended",
    ]


def test_meeting_end_enqueues_one_replacement_after_terminal_correction_failure(persistence):
    committed = _commit_final(persistence)
    claimed = persistence.claim_next_job(
        worker_id="failed-before-end-worker",
        lane="correction",
        now_ms=1_100,
        lease_ms=5_000,
    )
    assert claimed is not None
    persistence.fail_job(
        job_id=claimed["id"],
        worker_id="failed-before-end-worker",
        now_ms=1_200,
        error_class="TimeoutError",
    )

    persistence.end_meeting(meeting_id="meeting-1", now_ms=1_300)

    correction_jobs = persistence.list_jobs(meeting_id="meeting-1", lane="correction")
    active = [job for job in correction_jobs if job["status"] in {"pending", "running", "retry_wait"}]
    assert len(correction_jobs) == 2
    assert persistence.get_job(committed["job_ids"]["correction"])["status"] == "failed"
    assert len(active) == 1
    assert active[0]["idempotency_key"].endswith("meeting.ended")


def test_transcript_revision_rebuilds_the_durable_semantic_paragraph(persistence):
    committed = _commit_final(
        persistence,
        text="接口先恢度百分之五。",
        now_ms=1_000,
    )

    revised = persistence.commit_transcript_revision(
        meeting_id="meeting-1",
        segment_id="segment-1",
        expected_evidence_hash="hash-final-1",
        corrected_text="接口先灰度百分之五。",
        revision_id="revision-paragraph-1",
        now_ms=2_000,
    )

    assert revised is not None
    snapshot = persistence.get_snapshot("meeting-1")
    assert snapshot["segments"][0]["normalized_text"] == "接口先灰度百分之五。"
    assert snapshot["segments"][0]["correction_status"] == "changed"
    assert snapshot["semantic_paragraphs"][0]["text"] == "接口先灰度百分之五。"
    assert committed["segment_id"] == "segment-1"


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
            "source_duplicate_count": 0,
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


def test_native_audio_chunk_source_range_is_durable_and_part_of_idempotency(persistence):
    parameters = {
        "meeting_id": "native-range",
        "track": "system_audio",
        "epoch": 7,
        "chunk_seq": 0,
        "relative_path": "audio_assets/native-range/chunks/chunk-00000000.pcm",
        "sha256": "c" * 64,
        "sample_rate_hz": 16_000,
        "sample_count": 80_000,
        "duration_ms": 5_000,
        "file_size_bytes": 320_000,
        "source_sequence_start": 11,
        "source_sequence_end": 27,
        "source_timestamp_start_ms": 12_000,
        "source_timestamp_end_ms": 16_800,
    }

    first = persistence.record_audio_chunk(**parameters, now_ms=20_000)
    duplicate = persistence.record_audio_chunk(**parameters, now_ms=21_000)

    assert duplicate == first
    assert persistence.list_audio_chunks("native-range")[0] == first
    assert {
        key: first[key]
        for key in (
            "source_sequence_start",
            "source_sequence_end",
            "source_timestamp_start_ms",
            "source_timestamp_end_ms",
        )
    } == {
        "source_sequence_start": 11,
        "source_sequence_end": 27,
        "source_timestamp_start_ms": 12_000,
        "source_timestamp_end_ms": 16_800,
    }
    with pytest.raises(ValueError, match="conflicting content"):
        persistence.record_audio_chunk(
            **{**parameters, "source_sequence_end": 28},
            now_ms=22_000,
        )


@pytest.mark.parametrize(
    "source_range",
    [
        {"source_sequence_start": 1},
        {
            "source_sequence_start": 2,
            "source_sequence_end": 1,
            "source_timestamp_start_ms": 100,
            "source_timestamp_end_ms": 200,
        },
        {
            "source_sequence_start": 1,
            "source_sequence_end": 2,
            "source_timestamp_start_ms": 200,
            "source_timestamp_end_ms": 100,
        },
    ],
)
def test_audio_chunk_rejects_partial_or_reversed_native_source_ranges(
    persistence, source_range
):
    with pytest.raises(ValueError, match="native PCM source"):
        persistence.record_audio_chunk(
            meeting_id="invalid-native-range",
            track="system_audio",
            epoch=1,
            chunk_seq=0,
            relative_path="audio_assets/invalid/chunk-00000000.pcm",
            sha256="d" * 64,
            sample_rate_hz=16_000,
            sample_count=16_000,
            duration_ms=1_000,
            file_size_bytes=64_000,
            now_ms=1_000,
            **source_range,
        )

    assert persistence.list_audio_chunks("invalid-native-range") == []


def test_audio_chunk_query_fails_closed_on_partial_durable_native_source_range(
    persistence,
):
    persistence.record_audio_chunk(
        meeting_id="corrupted-native-range",
        track="system_audio",
        epoch=1,
        chunk_seq=0,
        relative_path="audio_assets/corrupted/chunk-00000000.pcm",
        sha256="f" * 64,
        sample_rate_hz=16_000,
        sample_count=16_000,
        duration_ms=1_000,
        file_size_bytes=64_000,
        now_ms=1_000,
    )
    persistence._conn.execute(
        "UPDATE audio_chunks SET source_sequence_start = 1 "
        "WHERE meeting_id = 'corrupted-native-range'"
    )

    with pytest.raises(ValueError, match="requires sequence and timestamp start/end"):
        persistence.list_audio_chunks("corrupted-native-range")


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
            "review_jobs": {},
        }
    ]


def test_history_search_status_and_cursor_are_database_backed(persistence):
    for index, (meeting_id, title) in enumerate(
        (("meeting-a", "支付发布评审"), ("meeting-b", "搜索服务复盘"), ("meeting-c", "支付告警复盘"))
    ):
        persistence.create_meeting(
            meeting_id=meeting_id,
            title=title,
            now_ms=1_000 + index,
        )
    persistence.end_meeting(meeting_id="meeting-b", now_ms=2_000)
    persistence.end_meeting(meeting_id="meeting-c", now_ms=3_000)

    search = persistence.list_meetings_page(query="支付", limit=10)
    first_page = persistence.list_meetings_page(limit=2)
    cursor = first_page["next_cursor"]
    second_page = persistence.list_meetings_page(limit=2, **cursor)
    live = persistence.list_meetings_page(status="live", limit=10)
    ready = persistence.list_meetings_page(status="ready", limit=10)

    assert {item["id"] for item in search["meetings"]} == {"meeting-a", "meeting-c"}
    assert first_page["has_more"] is True
    assert {item["id"] for item in first_page["meetings"]}.isdisjoint(
        {item["id"] for item in second_page["meetings"]}
    )
    assert [item["id"] for item in live["meetings"]] == ["meeting-a"]
    assert {item["id"] for item in ready["meetings"]} == {"meeting-b", "meeting-c"}

from __future__ import annotations

import pytest
import time
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import _coach_due_work_items, create_app
from meeting_copilot_web_mvp.v2_persistence import (
    IntelligenceProjectionError,
    V2Persistence,
)


def _commit(
    persistence: V2Persistence,
    *,
    meeting_id: str,
    final_id: str,
    segment_id: str,
    text: str,
    now_ms: int,
) -> dict:
    return persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id=final_id,
        segment_id=segment_id,
        text=text,
        normalized_text=text,
        started_at_ms=now_ms,
        ended_at_ms=now_ms + 100,
        evidence_hash=f"hash-{final_id}",
        source_track="microphone",
        now_ms=now_ms,
    )


def _response(
    *,
    decision_id: str,
    segment_id: str,
    quote: str,
    status: str = "intervention",
    candidate_key: str = "coach-candidate:commitment_without_condition:stable",
    intervention: bool = True,
    **decision_overrides,
) -> dict:
    decision = {
        "origin": "pi",
        "status": status,
        "runtime_requested": "pi",
        "runtime_used": "pi",
        "pi_provider_attempted": True,
        "decision_id": decision_id,
        "candidate_event": "commitment_without_condition",
        "candidate_key": candidate_key,
        "candidate_events": [
            {
                "event_type": "commitment_without_condition",
                "candidate_key": candidate_key,
                "evidence_segment_ids": [segment_id],
                "candidate_priority": 100,
                "reason": "条件尚未确认",
            }
        ],
        **decision_overrides,
    }
    return {
        "paragraph_revisions": [],
        "topic_update": None,
        "state_changes": [],
        "follow_up": None,
        "coach_intervention": (
            {
                "event_type": "commitment_risk",
                "title": "确认承诺条件",
                "recommendation": "确认负责人和截止时间。",
                "say_this": "确认负责人和截止时间。",
                "reason": "承诺缺少可验收条件。",
                "why_now": "承诺缺少可验收条件。",
                "evidence_segment_ids": [segment_id],
                "evidence_quote": quote,
                "urgency": "high",
                "confidence": 0.95,
                "valid_until_ms": 100_000,
                "origin": "pi",
                "runtime_used": "pi",
                "pi_provider_attempted": True,
            }
            if intervention
            else None
        ),
        "coach_decision": decision,
    }


def test_intervention_creates_restartable_ready_work_item(tmp_path):
    database_path = tmp_path / "work-items.db"
    persistence = V2Persistence(database_path, semantic_projection_mode="llm_first")
    committed = _commit(
        persistence,
        meeting_id="meeting-1",
        final_id="final-1",
        segment_id="segment-1",
        text="周五上线，但负责人和验收时间还没有确认。",
        now_ms=1_000,
    )
    applied = persistence.apply_intelligence_response(
        meeting_id="meeting-1",
        job_id=committed["job_ids"]["intelligence"],
        response=_response(
            decision_id="decision-1",
            segment_id="segment-1",
            quote="周五上线，但负责人和验收时间还没有确认。",
        ),
        now_ms=2_000,
    )
    item = applied["work_item"]
    assert item["state"] == "ready"
    assert item["version"] == 1
    assert item["meeting_id"] == "meeting-1"
    assert item["evidence_links"][0]["segment_id"] == "segment-1"
    assert item["evidence_links"][0]["revision"] == 1
    assert item["evidence_links"][0]["relation"] in {"supporting", "trigger"}
    persistence.close()

    reopened = V2Persistence(database_path, semantic_projection_mode="llm_first")
    try:
        restored = reopened.list_agent_work_items("meeting-1")
        assert len(restored) == 1
        assert restored[0]["work_item_id"] == item["work_item_id"]
        assert restored[0]["state"] == "ready"
    finally:
        reopened.close()


def test_provider_timeout_waits_for_evidence_and_keeps_failure_provenance(tmp_path):
    persistence = V2Persistence(tmp_path / "timeout.db", semantic_projection_mode="llm_first")
    try:
        committed = _commit(
            persistence,
            meeting_id="meeting-timeout",
            final_id="final-timeout",
            segment_id="segment-timeout",
            text="上线条件还没有确认。",
            now_ms=1_000,
        )
        applied = persistence.apply_intelligence_response(
            meeting_id="meeting-timeout",
            job_id=committed["job_ids"]["intelligence"],
            response=_response(
                decision_id="decision-timeout",
                segment_id="segment-timeout",
                quote="上线条件还没有确认。",
                status="timed_out",
                intervention=False,
                fallback_error_code="provider_timeout",
                fallback_reason="provider_timeout",
                status_reason="provider_timeout",
            ),
            now_ms=2_000,
        )
        assert applied["work_item"]["state"] == "waiting_for_evidence"
        assert applied["work_item"]["last_execution_status"] == "failed"
        assert applied["work_item"]["failure_reason"] == "provider_timeout"
    finally:
        persistence.close()


def test_lifecycle_refresh_reuses_item_and_marks_resolved(tmp_path):
    persistence = V2Persistence(tmp_path / "lifecycle.db", semantic_projection_mode="llm_first")
    try:
        first = _commit(
            persistence,
            meeting_id="meeting-lifecycle",
            final_id="final-1",
            segment_id="segment-1",
            text="回滚负责人还没有确认。",
            now_ms=1_000,
        )
        first_applied = persistence.apply_intelligence_response(
            meeting_id="meeting-lifecycle",
            job_id=first["job_ids"]["intelligence"],
            response=_response(
                decision_id="decision-open",
                segment_id="segment-1",
                quote="回滚负责人还没有确认。",
            ),
            now_ms=2_000,
        )
        first_item = first_applied["work_item"]
        second = _commit(
            persistence,
            meeting_id="meeting-lifecycle",
            final_id="final-2",
            segment_id="segment-2",
            text="回滚负责人是王工，已经确认负责。",
            now_ms=3_000,
        )
        second_applied = persistence.apply_intelligence_response(
            meeting_id="meeting-lifecycle",
            job_id=second["job_ids"]["intelligence"],
            response=_response(
                decision_id="decision-resolved",
                segment_id="segment-2",
                quote="回滚负责人是王工，已经确认负责。",
                status="protected_silent",
                intervention=False,
                candidate_key="lifecycle-resolution:decision-open:segment-2",
                lifecycle_refresh=True,
                lifecycle_refresh_previous_decision_id="decision-open",
                lifecycle_action="deprioritize",
            ),
            now_ms=4_000,
        )
        resolved = second_applied["work_item"]
        assert resolved["work_item_id"] == first_item["work_item_id"]
        assert resolved["state"] == "resolved"
        assert resolved["version"] == 2
        assert resolved["resolved_at_ms"] == 4_000
        assert {link["segment_id"] for link in resolved["evidence_links"]} == {"segment-1", "segment-2"}
    finally:
        persistence.close()


def test_cross_meeting_evidence_is_rejected_without_creating_item(tmp_path):
    persistence = V2Persistence(tmp_path / "scope.db", semantic_projection_mode="llm_first")
    try:
        committed = _commit(
            persistence,
            meeting_id="meeting-a",
            final_id="final-a",
            segment_id="segment-a",
            text="本场负责人还没有确认。",
            now_ms=1_000,
        )
        _commit(
            persistence,
            meeting_id="meeting-b",
            final_id="final-b",
            segment_id="segment-b",
            text="另一场会议的证据。",
            now_ms=1_000,
        )
        with pytest.raises(IntelligenceProjectionError, match="outside the meeting"):
            persistence.apply_intelligence_response(
                meeting_id="meeting-a",
                job_id=committed["job_ids"]["intelligence"],
                response=_response(
                    decision_id="decision-cross-meeting",
                    segment_id="segment-b",
                    quote="另一场会议的证据。",
                ),
                now_ms=2_000,
            )
        assert persistence.list_agent_work_items("meeting-a") == []
    finally:
        persistence.close()


def test_work_item_state_update_uses_compare_and_swap(tmp_path):
    persistence = V2Persistence(tmp_path / "cas.db", semantic_projection_mode="llm_first")
    try:
        committed = _commit(
            persistence,
            meeting_id="meeting-cas",
            final_id="final-cas",
            segment_id="segment-cas",
            text="等待验收人确认。",
            now_ms=1_000,
        )
        item = persistence.apply_intelligence_response(
            meeting_id="meeting-cas",
            job_id=committed["job_ids"]["intelligence"],
            response=_response(
                decision_id="decision-cas",
                segment_id="segment-cas",
                quote="等待验收人确认。",
            ),
            now_ms=2_000,
        )["work_item"]
        dismissed = persistence.update_agent_work_item_state(
            meeting_id="meeting-cas",
            work_item_id=item["work_item_id"],
            state="dismissed",
            expected_version=1,
            now_ms=3_000,
        )
        assert dismissed["state"] == "dismissed"
        assert dismissed["version"] == 2
        with pytest.raises(RuntimeError, match="version conflict"):
            persistence.update_agent_work_item_state(
                meeting_id="meeting-cas",
                work_item_id=item["work_item_id"],
                state="ready",
                expected_version=1,
                now_ms=4_000,
            )
    finally:
        persistence.close()


def test_durable_ready_item_can_restore_task_due_after_event_history_is_unavailable():
    due = _coach_due_work_items(
        [],
        now_ms=10_000,
        durable_items=[
            {
                "work_item_id": "work-item-1",
                "state": "ready",
                "latest_decision_id": "decision-1",
                "next_check_at_ms": 9_000,
                "kind": "commitment",
                "title": "确认负责人",
                "evidence_segment_ids": ["segment-1"],
                "evidence_links": [{"quote": "负责人还没有确认。"}],
                "created_at_ms": 1_000,
            }
        ],
    )
    assert due == [
        {
            "item_id": "decision-1",
            "work_item_id": "work-item-1",
            "status": "due",
            "next_check_at_ms": 9_000,
            "coach_event_type": "commitment",
            "title": "确认负责人",
            "evidence_segment_ids": ["segment-1"],
            "evidence_quote": "负责人还没有确认。",
            "created_at_ms": 1_000,
        }
    ]


def test_work_item_api_is_meeting_scoped_and_supports_dismiss(tmp_path):
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    with TestClient(app) as client:
        persistence = app.state.v2_persistence
        base_ms = time.time_ns() // 1_000_000
        committed = _commit(
            persistence,
            meeting_id="meeting-api",
            final_id="final-api",
            segment_id="segment-api",
            text="请确认最终验收负责人。",
            now_ms=base_ms,
        )
        item = persistence.apply_intelligence_response(
            meeting_id="meeting-api",
            job_id=committed["job_ids"]["intelligence"],
            response=_response(
                decision_id="decision-api",
                segment_id="segment-api",
                quote="请确认最终验收负责人。",
            ),
            now_ms=base_ms + 1_000,
        )["work_item"]
        listed = client.get("/v2/meetings/meeting-api/work-items")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["work_item_id"] == item["work_item_id"]
        updated = client.patch(
            f"/v2/meetings/meeting-api/work-items/{item['work_item_id']}",
            json={"state": "dismissed", "expected_version": 1},
        )
        assert updated.status_code == 200
        assert updated.json()["work_item"]["state"] == "dismissed"
        assert client.get("/v2/meetings/other-meeting/work-items").status_code == 404

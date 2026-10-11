"""Invalidate projected document evidence on the source writer's transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping


def _append(connection, meeting_id, event_type, aggregate_id, now_ms, key, payload):
    if connection.execute("SELECT 1 FROM meeting_events WHERE meeting_id=? AND idempotency_key=?", (meeting_id, key)).fetchone():
        return
    seq = connection.execute(
        "SELECT MAX(COALESCE((SELECT latest_seq FROM meetings WHERE id=?),0),COALESCE(MAX(seq),0))+1 "
        "FROM meeting_events WHERE meeting_id=?", (meeting_id, meeting_id),
    ).fetchone()[0]
    event_id = "evt_" + hashlib.sha256(f"{meeting_id}:{key}".encode()).hexdigest()[:32]
    connection.execute(
        "INSERT INTO meeting_events(meeting_id,seq,event_id,type,aggregate_type,aggregate_id,occurred_at_ms,"
        "correlation_id,causation_id,idempotency_key,payload_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (meeting_id, seq, event_id, event_type, "suggestion" if event_type == "suggestion.superseded" else "intelligence",
         aggregate_id, now_ms, meeting_id, "knowledge_context", key, json.dumps(payload, ensure_ascii=False)),
    )
    connection.execute("UPDATE meetings SET latest_seq=?,revision=revision+1,updated_at_ms=? WHERE id=?", (seq, now_ms, meeting_id))


def _current_coach(connection, meeting_id):
    superseded: set[str] = set()
    for row in connection.execute("SELECT payload_json FROM meeting_events WHERE meeting_id=? AND type='meeting.intelligence.applied' ORDER BY seq DESC", (meeting_id,)):
        payload = json.loads(row["payload_json"])
        decision = payload.get("coach_decision")
        if not isinstance(decision, Mapping):
            continue
        previous_id = decision.get("supersedes_decision_id") or decision.get("lifecycle_refresh_previous_decision_id")
        if previous_id:
            superseded.add(previous_id)
        if isinstance(payload.get("coach_intervention"), Mapping) and decision.get("decision_id") not in superseded:
            return decision.get("decision_id")
    return None


def invalidate_meeting_sources(connection: sqlite3.Connection, meeting_id: str, now_ms: int) -> None:
    """No cross-repository callbacks/locks; every event shares the source commit."""
    _invalidate(connection, meeting_id, now_ms, check_sources=True)


def invalidate_input_context(connection: sqlite3.Connection, meeting_id: str, now_ms: int) -> None:
    """Project state and explicit memory revisions share the same durable lifecycle."""
    _invalidate(connection, meeting_id, now_ms, check_sources=False)


def _stale_reason(connection, meeting_id, row, *, check_sources):
    from .knowledge_context import source_evidence_state
    from .user_memory import capture_input_context, input_context_state

    sources = json.loads(row["source_evidence_json"] or "[]")
    if check_sources and sources and source_evidence_state(connection, meeting_id, sources) != "valid":
        return "knowledge_evidence_superseded", "引用资料或本场资料范围已变化，旧建议已失效。"
    binding = json.loads(row["input_context_json"] or "{}")
    if binding and input_context_state(connection, meeting_id, binding) != "valid":
        current = capture_input_context(connection, meeting_id, binding.get("memory_scene") or "general")
        if current.get("material_set_id") != binding.get("material_set_id") or current.get("material_context_revision") != binding.get("material_context_revision"):
            return "meeting_materials_superseded", "本场附件或使用范围已更新，旧建议已失效。"
        if current["project_id"] != binding.get("project_id") or current["context_revision"] != binding.get("context_revision"):
            return "knowledge_context_superseded", "本场项目或资料范围已更新，旧建议已失效。"
        if current["project_revision"] != binding.get("project_revision"):
            return "project_state_superseded", "已确认项目状态已更新，旧建议已失效。"
        return "user_memory_superseded", "本场术语或表达偏好已更新，旧建议已失效。"
    return None


def _invalidate(connection, meeting_id, now_ms, *, check_sources):

    if not connection.in_transaction:
        raise RuntimeError("source invalidation requires the caller's write transaction")
    from .discussion_persistence import invalidate_discussion_inputs

    invalidate_discussion_inputs(connection, meeting_id, now_ms)
    # Proactive participation may be queued before it has a published suggestion
    # or coach binding. Revoke its frozen input in this same source transaction.
    if any(row[1] == "request_context_json" for row in connection.execute("PRAGMA table_info(jobs)")):
        from .knowledge_context import source_evidence_state
        from .user_memory import input_context_state

        for job in connection.execute("SELECT id,request_context_json FROM jobs WHERE meeting_id=? AND kind='intelligence' "
                                      "AND status IN ('pending','retry_wait','running')", (meeting_id,)).fetchall():
            request = json.loads(job["request_context_json"] or "{}")
            if request.get("profile") != "participation":
                continue
            binding, sources = request.get("input_context") or {}, request.get("source_evidence") or []
            if (binding and input_context_state(connection, meeting_id, binding) != "valid") or (
                    sources and source_evidence_state(connection, meeting_id, sources) != "valid"):
                connection.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,error_class='evidence_superseded',"
                                   "completed_at_ms=?,updated_at_ms=? WHERE id=?", (now_ms, now_ms, job["id"]))
    latest = connection.execute("SELECT suggestion_id FROM suggestions WHERE meeting_id=? AND kind='answer' AND status IN ('draft','committed') ORDER BY evidence_transcript_seq DESC,created_at_ms DESC LIMIT 1", (meeting_id,)).fetchone()
    invalidated_latest: Any = None
    for row in connection.execute("SELECT * FROM suggestions WHERE meeting_id=? AND status IN ('draft','committed') AND (source_evidence_json!='[]' OR input_context_json!='{}')", (meeting_id,)).fetchall():
        stale = _stale_reason(connection, meeting_id, row, check_sources=check_sources)
        if stale is None:
            continue
        reason, _ = stale
        connection.execute("UPDATE suggestions SET status='superseded',error_class=?,updated_at_ms=? WHERE suggestion_id=?", (reason, now_ms, row["suggestion_id"]))
        payload = dict(row)
        payload.update(status="superseded", error_class=reason, updated_at_ms=now_ms,
                       context_evidence=json.loads(payload.pop("context_evidence_json")),
                       source_evidence=json.loads(payload.pop("source_evidence_json")),
                       input_context=json.loads(payload.pop("input_context_json")))
        _append(connection, meeting_id, "suggestion.superseded", row["suggestion_id"], now_ms,
                f"suggestion.knowledge_superseded:{row['suggestion_id']}", payload)
        connection.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL,error_class='evidence_superseded',completed_at_ms=?,updated_at_ms=? "
                           "WHERE meeting_id=? AND status IN ('pending','retry_wait','running') AND (id=? OR (kind='intelligence' AND work_item_id=?))",
                           (now_ms, now_ms, meeting_id, row["job_id"], row["suggestion_id"]))
        if latest is not None and row["suggestion_id"] == latest["suggestion_id"]:
            invalidated_latest = row
    active_coach = _current_coach(connection, meeting_id)
    for row in connection.execute("SELECT * FROM coach_source_bindings WHERE meeting_id=? AND invalidated_at_ms IS NULL", (meeting_id,)).fetchall():
        stale = _stale_reason(connection, meeting_id, row, check_sources=check_sources)
        if stale is None:
            continue
        connection.execute("UPDATE coach_source_bindings SET invalidated_at_ms=? WHERE meeting_id=? AND decision_id=?", (now_ms, meeting_id, row["decision_id"]))
        if row["decision_id"] != active_coach:
            continue
        decision = json.loads(row["payload_json"])
        reason, explanation = stale
        decision.update(decision_id=f"{row['decision_id']}:source-stale", status="stale", status_reason=reason,
                        decision_reason=explanation, lifecycle_action="retract", lifecycle_refresh=True,
                        lifecycle_refresh_previous_decision_id=row["decision_id"], supersedes_decision_id=row["decision_id"])
        _append(connection, meeting_id, "meeting.intelligence.applied", row["job_id"], now_ms,
                f"coach.knowledge_superseded:{row['decision_id']}", {"job_id": row["job_id"], "coach_decision": decision, "coach_intervention": None, "follow_up": None})
    if invalidated_latest is not None:
        _enqueue_latest_answer(connection, invalidated_latest, now_ms)


def _enqueue_latest_answer(connection, answer, now_ms):
    meeting = connection.execute("SELECT state FROM meetings WHERE id=?", (answer["meeting_id"],)).fetchone()
    if meeting is None or meeting["state"] != "live":
        return
    newer = connection.execute("SELECT 1 FROM jobs WHERE meeting_id=? AND kind='answer' AND input_transcript_seq>? LIMIT 1", (answer["meeting_id"], answer["evidence_transcript_seq"])).fetchone()
    if newer is not None:
        return
    anchor = connection.execute("SELECT * FROM transcript_segments WHERE meeting_id=? AND segment_id=?", (answer["meeting_id"], answer["evidence_segment_id"])).fetchone()
    context = connection.execute("SELECT context_revision FROM meeting_knowledge_context WHERE meeting_id=?", (answer["meeting_id"],)).fetchone()
    if anchor is None:
        return
    from .user_memory import capture_input_context

    previous_binding = json.loads(answer["input_context_json"] or "{}")
    binding = capture_input_context(connection, answer["meeting_id"], previous_binding.get("memory_scene") or "general")
    snapshot_hash = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()[:24]
    identity = f"{answer['suggestion_id']}:{context['context_revision'] if context else 0}:{snapshot_hash}:knowledge"
    job_id = "job_" + hashlib.sha256(identity.encode()).hexdigest()[:32]
    old_job = connection.execute("SELECT trigger_type FROM jobs WHERE id=?", (answer["job_id"],)).fetchone()
    trigger = "transcript_revision_discussion" if old_job is not None and old_job["trigger_type"] in {"discussion", "transcript_revision_discussion"} else "transcript_revision"
    connection.execute("INSERT OR IGNORE INTO jobs(id,meeting_id,kind,status,priority,input_transcript_seq,input_version,evidence_segment_id,evidence_hash,generation_id,trigger_type,work_item_id,idempotency_key,attempts,max_attempts,next_attempt_at_ms,final_committed_at_ms,created_at_ms,updated_at_ms) "
                       "VALUES(?,?,'answer','pending',140,?,?,?,?,?,?,?, ?,0,3,?,?,?,?)",
                       (job_id, answer["meeting_id"], anchor["transcript_seq"], anchor["revision"], anchor["segment_id"], anchor["evidence_hash"],
                        f"answer-revision:{job_id}", trigger, answer["suggestion_id"], f"answer:knowledge:{identity}", now_ms, now_ms, now_ms, now_ms))

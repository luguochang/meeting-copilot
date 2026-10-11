"""Bind new coach DOM receipts to exact host publications, without backfilling."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .coach_lifecycle import coach_decision_replaces_prior_intervention
from .knowledge_context import source_evidence_state

MEASUREMENT_VERSION = "formal_coach_ui.v1"
RECEIPT_FIELDS = frozenset({"meeting_id", "job_id", "decision_id", "event_seq", "input_version", "draft_seq"})


def normalize_receipt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or set(value) != RECEIPT_FIELDS:
        return None
    for name in ("meeting_id", "job_id", "decision_id"):
        text = value[name]
        if not isinstance(text, str) or not text or text != text.strip() or len(text) > 300:
            return None
    if any(type(value[name]) is not int or value[name] < 1 for name in ("event_seq", "input_version")):
        return None
    if type(value["draft_seq"]) is not int or value["draft_seq"] != 0:
        return None
    return dict(value)


def publication_marker(metadata, decision, intervention, *, input_version: int) -> dict[str, Any]:
    """Called only by the host's typed apply transaction; never copy model keys."""
    if (metadata.get("profile") != "participation" or metadata.get("task_mode") == "discussion_state"
            or not isinstance(decision, Mapping) or not isinstance(intervention, Mapping)
            or decision.get("origin") != "pi" or decision.get("runtime_used") != "pi"
            or decision.get("status") != "intervention" or decision.get("lifecycle_action") != "retain"
            or decision.get("pi_provider_attempted") is not True
            or not decision.get("decision_id") or intervention.get("decision_id") != decision.get("decision_id")
            or type(input_version) is not int or input_version < 1):
        return {}
    return {"ui_render_measurement_version": MEASUREMENT_VERSION, "ui_render_input_version": input_version}


def current_publication_receipt(persistence, *, meeting_id: str, job_id: str, decision_id: str,
                                now_ms: int, event_seq: int | None = None) -> dict[str, Any] | None:
    """Read one exact canonical publication under the existing persistence lock."""
    from .user_memory import input_context_state

    with persistence._lock:
        c = persistence._conn
        meeting = c.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        job = c.execute("SELECT * FROM jobs WHERE id=? AND meeting_id=?", (job_id, meeting_id)).fetchone()
        if meeting is None or meeting["state"] != "live" or job is None or job["status"] not in {"running", "succeeded"}:
            return None
        if job["status"] == "running" and (not job["lease_owner"] or int(job["lease_until_ms"] or 0) <= now_ms
                or int(job["deadline_at_ms"] or 0) <= now_ms):
            return None
        event = c.execute("SELECT * FROM meeting_events WHERE meeting_id=? AND type='meeting.intelligence.applied' "
            "AND idempotency_key=?", (meeting_id, f"meeting.intelligence.applied:{job_id}")).fetchone()
        if event is None or event_seq is not None and event["seq"] != event_seq:
            return None
        payload = json.loads(event["payload_json"] or "{}")
        metadata = json.loads(job["request_context_json"] or "{}")
        decision = payload.get("coach_decision") or {}
        card = payload.get("coach_intervention") or {}
        marker = publication_marker(metadata, decision, card, input_version=int(job["input_version"]))
        if not marker or any(payload.get(key) != value for key, value in marker.items()) or payload.get("source") != "llm_first":
            return None
        if decision.get("decision_id") != decision_id or card.get("decision_id") != decision_id:
            return None
        if type(card.get("valid_until_ms")) is not int or now_ms >= card["valid_until_ms"]:
            return None
        # A later actual replacement/retract is a barrier, even on the same
        # topic/basis. Do not skip retracts and revive an earlier intervention.
        for newer in c.execute("SELECT payload_json FROM meeting_events WHERE meeting_id=? "
                "AND type='meeting.intelligence.applied' AND seq>? ORDER BY seq DESC", (meeting_id, event["seq"])):
            newer_payload = json.loads(newer["payload_json"] or "{}")
            newer_decision = newer_payload.get("coach_decision")
            if isinstance(newer_decision, Mapping) and coach_decision_replaces_prior_intervention(newer_payload, newer_decision):
                return None
        if persistence._discussion_request_state_locked(meeting_id, metadata) != "valid":
            return None
        binding = c.execute("SELECT * FROM coach_source_bindings WHERE meeting_id=? AND job_id=? AND decision_id=?",
                            (meeting_id, job_id, decision_id)).fetchone()
        if binding is None or binding["invalidated_at_ms"] is not None:
            return None
        bound_decision = json.loads(binding["payload_json"] or "{}")
        bound_until = bound_decision.get("valid_until_ms")
        if bound_until is not None and (type(bound_until) is not int or now_ms >= bound_until):
            return None
        sources = json.loads(binding["source_evidence_json"] or "[]")
        inputs = json.loads(binding["input_context_json"] or "{}")
        if sources and source_evidence_state(c, meeting_id, sources) != "valid" or input_context_state(c, meeting_id, inputs) != "valid":
            return None
        return {"meeting_id": meeting_id, "job_id": job_id, "decision_id": decision_id,
                "event_seq": int(event["seq"]), "input_version": int(job["input_version"]), "draft_seq": 0}

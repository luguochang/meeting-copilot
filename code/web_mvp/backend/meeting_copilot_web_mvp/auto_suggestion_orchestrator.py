from __future__ import annotations

from dataclasses import replace
import time
from typing import Any
import uuid

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp import realtime_transcript_correction
from meeting_copilot_web_mvp.logging_config import get_logger


DEFAULT_COOLDOWN_MS = 30_000
DEFAULT_MAX_CALLS_PER_HOUR = 80
DEFAULT_MAX_RUNS_PER_REQUEST = 1
MAX_PROVIDER_ATTEMPTS = 2
ONE_HOUR_MS = 3_600_000
LOW_CONFIDENCE_THRESHOLD = 0.80
RESERVATION_LEASE_MS = 60_000
LEVEL_ONE_CONFIDENCE_THRESHOLD = 0.90
POLICY_VERSION = "auto-suggestion-orchestrator.v2"
MAX_SUPPRESSED_ENTRIES = 512
MAX_PROCESSED_CANDIDATE_IDS = 2_048
MAX_TERMINAL_FAILED_CANDIDATE_IDS = 1_024
MAX_CANDIDATE_RESERVATIONS = 2_048
MAX_CANDIDATE_ATTEMPT_COUNTS = 2_048

_log = get_logger(__name__)


def build_runtime_policy(
    suggestion_settings: dict[str, Any],
    *,
    degradation_level: int,
) -> dict[str, Any]:
    user_threshold = float(
        suggestion_settings.get("confidence_threshold", LOW_CONFIDENCE_THRESHOLD)
    )
    effective_threshold = max(
        user_threshold,
        LEVEL_ONE_CONFIDENCE_THRESHOLD if degradation_level == 1 else 0.0,
    )
    window_seconds = max(1, int(suggestion_settings.get("window_seconds", 20)))
    cooldown_minutes = max(0, int(suggestion_settings.get("cooldown_minutes", 5)))
    return {
        "policy_version": POLICY_VERSION,
        "enabled": bool(suggestion_settings.get("enabled", True)),
        "degradation_level": max(0, int(degradation_level)),
        "user_confidence_threshold": user_threshold,
        "effective_confidence_threshold": effective_threshold,
        "window_seconds": window_seconds,
        "window_ms": window_seconds * 1_000,
        "cooldown_minutes": cooldown_minutes,
        "cooldown_ms": cooldown_minutes * 60_000,
        "reservation_lease_seconds": RESERVATION_LEASE_MS // 1_000,
        "reservation_lease_ms": RESERVATION_LEASE_MS,
    }


def default_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "paused": False,
        "status": "running",
        "policy_version": POLICY_VERSION,
        "min_confidence": LOW_CONFIDENCE_THRESHOLD,
        "cooldown_ms": DEFAULT_COOLDOWN_MS,
        "max_calls_per_hour": DEFAULT_MAX_CALLS_PER_HOUR,
        "last_evaluated_at_ms": 0,
        "last_triggered_at_ms": 0,
        "last_successful_card_at_ms": 0,
        "call_timestamps_ms": [],
        "processed_candidate_ids": [],
        "terminal_failed_candidate_ids": [],
        "candidate_attempt_counts": {},
        "candidate_reservations": {},
        "suppressed": [],
        "effective_policy": {},
        "last_suppression_reason": None,
        "last_reservation_recovery_reason": None,
        "state_truncated": False,
        "truncated_count": 0,
        "truncated_counts": {},
        "truncation_policy": "retain_recent",
        "compacted_count": 0,
        "compacted_counts": {},
        "semantic_state_over_capacity": False,
        "capacity_policy": "fail_closed",
        "capacity_blocked_count": 0,
        "capacity_limits": {},
    }


def status_from_record(record: dict[str, Any]) -> dict[str, Any]:
    status = default_status()
    status.update(dict(record.get("auto_suggestion") or {}))
    status["processed_candidate_ids"] = list(status.get("processed_candidate_ids") or [])
    status["terminal_failed_candidate_ids"] = list(status.get("terminal_failed_candidate_ids") or [])
    status["candidate_attempt_counts"] = {
        str(candidate_id): max(0, int(count or 0))
        for candidate_id, count in dict(status.get("candidate_attempt_counts") or {}).items()
    }
    status["candidate_reservations"] = {
        str(candidate_id): dict(reservation or {})
        for candidate_id, reservation in dict(status.get("candidate_reservations") or {}).items()
    }
    status["call_timestamps_ms"] = [int(value) for value in list(status.get("call_timestamps_ms") or [])]
    status["suppressed"] = list(status.get("suppressed") or [])
    status["effective_policy"] = dict(status.get("effective_policy") or {})
    _compact_status(status)
    status["processed_candidate_count"] = len(status["processed_candidate_ids"])
    status["suppressed_count"] = len(status["suppressed"])
    status["call_count_last_hour"] = len(status["call_timestamps_ms"])
    return status


def apply_runtime_policy(
    record: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    status = status_from_record(record)
    effective_policy = dict(policy)
    status.update({
        "enabled": bool(effective_policy.get("enabled", True)),
        "policy_version": str(effective_policy.get("policy_version") or POLICY_VERSION),
        "min_confidence": float(
            effective_policy.get(
                "effective_confidence_threshold",
                LOW_CONFIDENCE_THRESHOLD,
            )
        ),
        "cooldown_ms": max(0, int(effective_policy.get("cooldown_ms") or 0)),
        "window_ms": max(0, int(effective_policy.get("window_ms") or 0)),
        "effective_policy": effective_policy,
    })
    updated = {**record, "auto_suggestion": _persistable_status(status)}
    return updated, status_from_record(updated)


def suppress(
    record: dict[str, Any],
    *,
    reason: str,
    now_ms: int,
    candidate_id: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    status = status_from_record(record)
    _append_suppressed(
        status,
        candidate_id=candidate_id,
        reason=reason,
        at_ms=now_ms,
        candidate_at_ms=0,
    )
    updated = {**record, "auto_suggestion": _persistable_status(status)}
    return updated, status_from_record(updated)


def patch_status(record: dict[str, Any], *, paused: bool | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    status = status_from_record(record)
    if paused is not None:
        status["paused"] = bool(paused)
        status["status"] = "paused" if paused else "running"
    updated = {**record, "auto_suggestion": _persistable_status(status)}
    return updated, status_from_record(updated)


def claim_candidate(
    record: dict[str, Any],
    *,
    previews: list[dict[str, Any]],
    config: llm_service.LlmConfig,
    acceptance_blockers: list[str],
    claim_id: str,
    now_ms: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    status = status_from_record(record)
    reservations = dict(status.get("candidate_reservations") or {})
    attempt_counts = dict(status.get("candidate_attempt_counts") or {})
    processed_candidate_ids = set(status.get("processed_candidate_ids") or [])
    effective_policy = dict(status.get("effective_policy") or {})
    lease_ms = max(
        1,
        int(effective_policy.get("reservation_lease_ms") or RESERVATION_LEASE_MS),
    )
    provider_metadata = llm_service.provider_audit_metadata(
        config,
        purpose="auto_suggestion",
    )
    queued: list[tuple[dict[str, Any], str, dict[str, Any], int]] = []
    stale_candidate_ids: set[str] = set()

    for preview in previews:
        candidate_id = str(preview.get("target_candidate_id") or "")
        candidate_payload = _candidate_payload(record, candidate_id)
        if str(candidate_payload.get("scheduler_event_type") or "") != "llm_candidate_queued":
            continue
        candidate_at_ms = _candidate_at_ms(record, candidate_id)
        queued.append((preview, candidate_id, candidate_payload, candidate_at_ms))
        reservation = dict(reservations.get(candidate_id) or {})
        reservation_status = str(reservation.get("status") or "")
        if reservation_status == "reserved":
            reserved_at_ms = max(0, int(reservation.get("reserved_at_ms") or 0))
            lease_expires_at_ms = max(
                reserved_at_ms + lease_ms,
                int(reservation.get("lease_expires_at_ms") or 0),
            )
            reservation["lease_expires_at_ms"] = lease_expires_at_ms
            reservations[candidate_id] = reservation
            status["candidate_reservations"] = reservations
            if now_ms >= lease_expires_at_ms:
                stale_candidate_ids.add(candidate_id)
                continue
            _append_suppressed(
                status,
                candidate_id=candidate_id,
                reason="in_flight",
                at_ms=now_ms,
                candidate_at_ms=candidate_at_ms,
            )
            updated = {**record, "auto_suggestion": _persistable_status(status)}
            return updated, status_from_record(updated), None

    pending_candidate_id = next(
        (
            candidate_id
            for _preview, candidate_id, _payload, _candidate_at_ms_value in queued
            if candidate_id not in processed_candidate_ids
            and str((reservations.get(candidate_id) or {}).get("status") or "")
            not in {"completed", "terminal_failed"}
        ),
        "",
    )
    control_suppression_reason = (
        "acceptance_blocked"
        if acceptance_blockers
        else "paused"
        if status.get("paused")
        else None
    )
    if pending_candidate_id and control_suppression_reason:
        _append_suppressed(
            status,
            candidate_id=pending_candidate_id,
            reason=control_suppression_reason,
            at_ms=now_ms,
            candidate_at_ms=_candidate_at_ms(record, pending_candidate_id),
        )
        updated = {**record, "auto_suggestion": _persistable_status(status)}
        return updated, status_from_record(updated), None
    window_ms = max(0, int(effective_policy.get("window_ms") or status.get("window_ms") or 0))
    last_evaluated_at_ms = max(0, int(status.get("last_evaluated_at_ms") or 0))
    pending_quality_suppression = None
    if pending_candidate_id:
        pending_preview, _candidate_id, pending_payload, _candidate_at = next(
            item for item in queued if item[1] == pending_candidate_id
        )
        candidate_suppression = _suppression_reason(
            status=status,
            preview=pending_preview,
            candidate_payload=pending_payload,
            candidate_id=pending_candidate_id,
            now_ms=now_ms,
            acceptance_blockers=acceptance_blockers,
        )
        if candidate_suppression in {"low_confidence", "partial_not_final"}:
            pending_quality_suppression = candidate_suppression
    if (
        pending_candidate_id
        and pending_quality_suppression is None
        and not stale_candidate_ids
        and last_evaluated_at_ms
        and now_ms - last_evaluated_at_ms < window_ms
    ):
        _append_suppressed(
            status,
            candidate_id=pending_candidate_id,
            reason="evaluation_window",
            at_ms=now_ms,
            candidate_at_ms=_candidate_at_ms(record, pending_candidate_id),
        )
        updated = {**record, "auto_suggestion": _persistable_status(status)}
        return updated, status_from_record(updated), None
    if pending_candidate_id:
        status["last_evaluated_at_ms"] = max(0, int(now_ms))

    for preview, candidate_id, candidate_payload, candidate_at_ms in queued:
        reservation = dict(reservations.get(candidate_id) or {})
        reservation_status = str(reservation.get("status") or "")
        stale_recovery = candidate_id in stale_candidate_ids
        prior_attempt_count = max(
            int(attempt_counts.get(candidate_id) or 0),
            int(reservation.get("attempt_count") or 0),
        )
        capacity_reason = _semantic_capacity_reason(status, candidate_id=candidate_id)
        if capacity_reason:
            reservations = dict(status.get("candidate_reservations") or {})
            attempt_counts = dict(status.get("candidate_attempt_counts") or {})
            status["capacity_blocked_count"] = (
                max(0, int(status.get("capacity_blocked_count") or 0)) + 1
            )
            _append_suppressed(
                status,
                candidate_id=candidate_id,
                reason=capacity_reason,
                at_ms=now_ms,
                candidate_at_ms=candidate_at_ms,
            )
            continue
        if stale_recovery and prior_attempt_count >= MAX_PROVIDER_ATTEMPTS:
            reservation.update({
                "status": "terminal_failed",
                "completed_at_ms": max(0, int(now_ms)),
                "terminal_reason": "reservation_lease_expired",
            })
            reservations[candidate_id] = reservation
            status["candidate_reservations"] = reservations
            if candidate_id not in status["processed_candidate_ids"]:
                status["processed_candidate_ids"].append(candidate_id)
            if candidate_id not in status["terminal_failed_candidate_ids"]:
                status["terminal_failed_candidate_ids"].append(candidate_id)
            status["last_reservation_recovery_reason"] = (
                "reservation_lease_expired_terminal"
            )
            _append_suppressed(
                status,
                candidate_id=candidate_id,
                reason="reservation_lease_expired_terminal",
                at_ms=now_ms,
                candidate_at_ms=candidate_at_ms,
            )
            continue

        _prune_call_timestamps(status, now_ms=now_ms)
        suppression_reason = _suppression_reason(
            status=status,
            preview=preview,
            candidate_payload=candidate_payload,
            candidate_id=candidate_id,
            now_ms=now_ms,
            acceptance_blockers=acceptance_blockers,
        )
        if suppression_reason:
            _append_suppressed(
                status,
                candidate_id=candidate_id,
                reason=suppression_reason,
                at_ms=now_ms,
                candidate_at_ms=candidate_at_ms,
            )
            continue

        attempt_count = prior_attempt_count + 1
        attempt_counts[candidate_id] = attempt_count
        reservation = {
            "candidate_id": candidate_id,
            "claim_id": str(claim_id),
            "status": "reserved",
            "reserved_at_ms": max(0, int(now_ms)),
            "lease_expires_at_ms": max(0, int(now_ms)) + lease_ms,
            "attempt_count": attempt_count,
            "retry_count": max(0, attempt_count - 1),
            **provider_metadata,
        }
        if stale_recovery:
            reservation["recovered_from_stale_reservation"] = True
            status["last_reservation_recovery_reason"] = (
                "reservation_lease_expired_retry"
            )
        reservations[candidate_id] = reservation
        status["candidate_attempt_counts"] = attempt_counts
        status["candidate_reservations"] = reservations
        status["status"] = "in_flight"
        updated = {**record, "auto_suggestion": _persistable_status(status)}
        return updated, status_from_record(updated), {
            **reservation,
            "preview": dict(preview),
        }

    updated = {**record, "auto_suggestion": _persistable_status(status)}
    return updated, status_from_record(updated), None


def run_once(
    record: dict[str, Any],
    *,
    previews: list[dict[str, Any]],
    config: llm_service.LlmConfig,
    acceptance_blockers: list[str],
    correction_enabled: bool = True,
    claimed_candidate_id: str | None = None,
    claim_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if claimed_candidate_id is None or claim_id is None:
        generated_claim_id = f"auto_claim_{uuid.uuid4().hex}"
        record, status, claim = claim_candidate(
            record,
            previews=previews,
            config=config,
            acceptance_blockers=acceptance_blockers,
            claim_id=generated_claim_id,
            now_ms=_wall_clock_ms(),
        )
        if claim is None:
            return record, status, []
        claimed_candidate_id = str(claim["candidate_id"])
        claim_id = str(claim["claim_id"])
        previews = [dict(claim["preview"])]

    status = status_from_record(record)
    generated_runs: list[dict[str, Any]] = []
    generated_revisions: list[dict[str, Any]] = []
    combined_attempted_segment_ids: set[str] = set()
    combined_no_revision_needed_segment_ids: set[str] = set()
    combined_rejected_segment_ids: set[str] = set()
    correction_disabled_by_setting = False
    existing_cards = list(record.get("suggestion_cards") or [])
    existing_card_ids = {str(card.get("card_id") or "") for card in existing_cards}

    for preview in previews:
        candidate_id = str(preview.get("target_candidate_id") or "")
        if candidate_id != claimed_candidate_id:
            continue
        candidate_payload = _candidate_payload(record, candidate_id)
        if str(candidate_payload.get("scheduler_event_type") or "") != "llm_candidate_queued":
            continue
        candidate_at_ms = _candidate_at_ms(record, candidate_id)
        reservations = dict(status.get("candidate_reservations") or {})
        reservation = dict(reservations.get(candidate_id) or {})
        if (
            str(reservation.get("claim_id") or "") != claim_id
            or reservation.get("status") != "reserved"
        ):
            continue

        call_started_at_ms = _wall_clock_ms()
        try:
            run = llm_service.execute_candidate(
                preview,
                replace(config, timeout_seconds=min(config.timeout_seconds, 25.0), max_retries=0),
            )
        except Exception as exc:
            call_finished_at_ms = _wall_clock_ms()
            _log.warning(
                "auto_suggestion.provider_failed",
                candidate_id=candidate_id,
                claim_id=claim_id,
                attempt_count=max(1, int(reservation.get("attempt_count") or 1)),
                error_code=type(exc).__name__,
                duration_ms=max(0, call_finished_at_ms - call_started_at_ms),
            )
            run = {
                **preview,
                "run_status": "provider_failed",
                "execution_status": "failed",
                "llm_call_status": "failed",
                "provider_error_type": type(exc).__name__,
                **llm_service.provider_error_payload(
                    error_code="llm_provider_failed",
                    message="LLM provider request failed",
                ),
            }
        else:
            call_finished_at_ms = _wall_clock_ms()
        generated_runs.append(run)
        run["call_started_at_ms"] = call_started_at_ms
        run["call_finished_at_ms"] = call_finished_at_ms
        accepted_card = False
        generated_low_confidence = False
        if run.get("run_status") == "completed" and isinstance(run.get("card"), dict):
            card = dict(run["card"])
            effective_policy = dict(status.get("effective_policy") or {})
            effective_threshold = float(
                effective_policy.get(
                    "effective_confidence_threshold",
                    status.get("min_confidence") or LOW_CONFIDENCE_THRESHOLD,
                )
            )
            try:
                generated_confidence = float(
                    card.get("confidence", preview.get("candidate_confidence", 0.0))
                )
            except (TypeError, ValueError):
                generated_confidence = 0.0
            if generated_confidence < effective_threshold:
                generated_low_confidence = True
                run["suppressed_card"] = card
                run.pop("card", None)
                run["card_status"] = "suppressed"
                run["suppression_reason"] = "generated_low_confidence"
                _append_suppressed(
                    status,
                    candidate_id=candidate_id,
                    reason="generated_low_confidence",
                    at_ms=call_started_at_ms,
                    candidate_at_ms=candidate_at_ms,
                    details={
                        "observed_confidence": generated_confidence,
                        "effective_confidence_threshold": effective_threshold,
                        "llm_usage": dict(run.get("llm_usage") or {}),
                    },
                )
            else:
                accepted_card = True
                if str(card.get("card_id") or "") not in existing_card_ids:
                    existing_cards.append(card)
                    existing_card_ids.add(str(card.get("card_id") or ""))
        attempted_segment_ids = _combined_segment_ids(preview)
        transcript_correction = run.get("transcript_correction")
        if isinstance(transcript_correction, dict):
            correction_segment_id = str(transcript_correction.get("segment_id") or "").strip()
            if correction_segment_id:
                attempted_segment_ids.add(correction_segment_id)
        if (
            run.get("run_status") == "completed"
            and attempted_segment_ids
            and not correction_enabled
        ):
            correction_disabled_by_setting = True
            run["transcript_correction_outcome"] = (
                "correction_disabled_by_setting"
            )
        elif run.get("run_status") == "completed" and attempted_segment_ids:
            combined_attempted_segment_ids.update(attempted_segment_ids)
            if isinstance(transcript_correction, dict):
                final_event = _final_event_for_segment(
                    record,
                    str(transcript_correction.get("segment_id") or ""),
                )
                revision = None
                if final_event is not None:
                    revision = realtime_transcript_correction.build_revision_event(
                        session_id=str(record.get("session_id") or ""),
                        final_event=final_event,
                        corrected_text=str(transcript_correction.get("corrected_text") or ""),
                        source=str(transcript_correction.get("source") or "combined_suggestion"),
                        usage=dict(transcript_correction.get("usage") or run.get("llm_usage") or {}),
                    )
                if revision is not None:
                    run["transcript_revision"] = revision
                    run["transcript_correction_outcome"] = "revised"
                    generated_revisions.append(revision)
                else:
                    run["transcript_correction_outcome"] = "rejected"
                    combined_rejected_segment_ids.update(attempted_segment_ids)
            else:
                run["transcript_correction_outcome"] = "no_revision_needed"
                combined_no_revision_needed_segment_ids.update(attempted_segment_ids)
        attempt_count = max(1, int(reservation.get("attempt_count") or 1))
        completed_reservation = {
            **reservation,
            "completed_at_ms": _wall_clock_ms(),
        }
        if run.get("run_status") == "provider_failed":
            if attempt_count < MAX_PROVIDER_ATTEMPTS:
                completed_reservation["status"] = "retry_pending"
            else:
                completed_reservation["status"] = "terminal_failed"
                if candidate_id and candidate_id not in status["processed_candidate_ids"]:
                    status["processed_candidate_ids"].append(candidate_id)
                if candidate_id and candidate_id not in status["terminal_failed_candidate_ids"]:
                    status["terminal_failed_candidate_ids"].append(candidate_id)
        else:
            completed_reservation["status"] = "completed"
            if candidate_id and candidate_id not in status["processed_candidate_ids"]:
                status["processed_candidate_ids"].append(candidate_id)
        reservations[candidate_id] = completed_reservation
        status["candidate_reservations"] = reservations
        status["call_timestamps_ms"].append(call_started_at_ms)
        if accepted_card:
            status["last_triggered_at_ms"] = call_finished_at_ms
            status["last_successful_card_at_ms"] = call_finished_at_ms
        if run.get("run_status") == "provider_failed":
            _append_suppressed(
                status,
                candidate_id=candidate_id,
                reason="provider_error",
                at_ms=call_finished_at_ms,
                candidate_at_ms=candidate_at_ms,
            )
        elif not generated_low_confidence:
            status["status"] = "running"
        if len(generated_runs) >= DEFAULT_MAX_RUNS_PER_REQUEST:
            break

    if any(
        run.get("run_status") == "completed"
        and run.get("card_status") != "suppressed"
        for run in generated_runs
    ):
        status["status"] = "running"
    status = status_from_record({"auto_suggestion": status})
    updated = {
        **record,
        "suggestion_cards": existing_cards,
        "auto_suggestion": _persistable_status(status),
    }
    if correction_disabled_by_setting:
        correction_status = dict(record.get("realtime_transcript_correction") or {})
        correction_status.update({
            "status": "correction_disabled_by_setting",
            "last_source": "combined_suggestion",
            "processed_segment_ids": list(
                correction_status.get("processed_segment_ids") or []
            ),
        })
        updated["realtime_transcript_correction"] = correction_status
    elif combined_attempted_segment_ids:
        correction_status = dict(record.get("realtime_transcript_correction") or {})
        processed_segment_ids = set(correction_status.get("processed_segment_ids") or [])
        processed_segment_ids.update(combined_attempted_segment_ids)
        correction_status.update({
            "status": (
                "combined_rejected"
                if combined_rejected_segment_ids
                else "combined_no_revision_needed"
                if combined_no_revision_needed_segment_ids and not generated_revisions
                else "completed"
            ),
            "last_source": "combined_suggestion",
            "combined_attempted_segment_ids": sorted(
                set(correction_status.get("combined_attempted_segment_ids") or []).union(combined_attempted_segment_ids)
            ),
            "combined_no_revision_needed_segment_ids": sorted(
                set(correction_status.get("combined_no_revision_needed_segment_ids") or []).union(combined_no_revision_needed_segment_ids)
            ),
            "combined_rejected_segment_ids": sorted(
                set(correction_status.get("combined_rejected_segment_ids") or []).union(combined_rejected_segment_ids)
            ),
            "processed_segment_ids": sorted(processed_segment_ids),
        })
        updated = realtime_transcript_correction.apply_revision_events(
            updated,
            generated_revisions,
            status=correction_status,
        )
    return updated, status_from_record(updated), generated_runs


def _suppression_reason(
    *,
    status: dict[str, Any],
    preview: dict[str, Any],
    candidate_payload: dict[str, Any],
    candidate_id: str,
    now_ms: int,
    acceptance_blockers: list[str],
) -> str | None:
    if acceptance_blockers:
        return "acceptance_blocked"
    if status.get("paused"):
        return "paused"
    if candidate_id and candidate_id in set(status.get("processed_candidate_ids") or []):
        return "duplicate"
    effective_policy = dict(status.get("effective_policy") or {})
    confidence_threshold = float(
        effective_policy.get(
            "effective_confidence_threshold",
            status.get("min_confidence") or LOW_CONFIDENCE_THRESHOLD,
        )
    )
    confidence = preview.get("candidate_confidence", candidate_payload.get("confidence"))
    if confidence is None:
        return "low_confidence"
    try:
        if float(confidence) < confidence_threshold:
            return "low_confidence"
    except (TypeError, ValueError):
        return "low_confidence"
    degradation_reasons = set(preview.get("candidate_degradation_reasons") or candidate_payload.get("degradation_reasons") or [])
    if "partial_not_final" in degradation_reasons:
        return "partial_not_final"
    if degradation_reasons.intersection({"low_asr_confidence", "evidence_text_short"}):
        return "low_confidence"
    last_successful_card_at_ms = int(
        status.get("last_successful_card_at_ms")
        or status.get("last_triggered_at_ms")
        or 0
    )
    if "cooldown_ms" in effective_policy:
        cooldown_ms = max(0, int(effective_policy["cooldown_ms"]))
    else:
        cooldown_ms = max(0, int(status.get("cooldown_ms") or DEFAULT_COOLDOWN_MS))
    if (
        last_successful_card_at_ms
        and now_ms - last_successful_card_at_ms < cooldown_ms
    ):
        return "cooldown"
    if len(status.get("call_timestamps_ms") or []) >= int(status.get("max_calls_per_hour") or DEFAULT_MAX_CALLS_PER_HOUR):
        return "rate_limited"
    return None


def _candidate_at_ms(record: dict[str, Any], candidate_id: str) -> int:
    for event in record.get("events") or []:
        payload = event.get("payload") or {}
        if event.get("event_type") == "suggestion_candidate_event" and str(payload.get("candidate_id") or "") == candidate_id:
            return int(event.get("at_ms") or 0)
    return 0


def _candidate_payload(record: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for event in record.get("events") or []:
        payload = event.get("payload") or {}
        if event.get("event_type") == "suggestion_candidate_event" and str(payload.get("candidate_id") or "") == candidate_id:
            return dict(payload)
    return {}


def _final_event_for_segment(record: dict[str, Any], segment_id: str) -> dict[str, Any] | None:
    if not segment_id:
        return None
    for event in record.get("events") or []:
        if event.get("event_type") not in {"final", "transcript_final"}:
            continue
        payload = event.get("payload") or {}
        if str(payload.get("segment_id") or event.get("segment_id") or "") == segment_id:
            return dict(event)
    return None


def _append_suppressed(
    status: dict[str, Any],
    *,
    candidate_id: str,
    reason: str,
    at_ms: int,
    candidate_at_ms: int,
    details: dict[str, Any] | None = None,
) -> None:
    suppressed = list(status.get("suppressed") or [])
    entry = {
        "candidate_id": candidate_id,
        "reason": reason,
        "at_ms": at_ms,
        "candidate_at_ms": candidate_at_ms,
    }
    if details:
        entry.update(details)
    suppressed.append(entry)
    status["suppressed"] = suppressed
    status["last_suppression_reason"] = reason
    status["status"] = _status_for_suppression(reason)


def _prune_call_timestamps(status: dict[str, Any], *, now_ms: int) -> None:
    timestamps = [int(value) for value in list(status.get("call_timestamps_ms") or [])]
    if now_ms <= 0:
        status["call_timestamps_ms"] = timestamps
        return
    status["call_timestamps_ms"] = [
        timestamp
        for timestamp in timestamps
        if 0 <= now_ms - timestamp < ONE_HOUR_MS
    ]


def _wall_clock_ms() -> int:
    return int(time.time() * 1_000)


def _combined_segment_ids(preview: dict[str, Any]) -> set[str]:
    segment_ids = {
        str(span.get("segment_id") or "").strip()
        for span in list(preview.get("evidence_spans") or [])
        if isinstance(span, dict)
    }
    segment_ids.update(
        str(segment_id).strip()
        for segment_id in list(preview.get("segment_batch") or [])
    )
    segment_ids.discard("")
    return segment_ids


def _status_for_suppression(reason: str) -> str:
    return {
        "paused": "paused",
        "cooldown": "cooldown",
        "low_confidence": "low_quality_suppressed",
        "rate_limited": "rate_limited",
        "acceptance_blocked": "blocked",
        "duplicate": "running",
        "partial_not_final": "low_quality_suppressed",
        "provider_error": "provider_error",
        "in_flight": "in_flight",
        "disabled_by_setting": "disabled",
        "disabled_by_degradation": "disabled",
        "budget_blocked": "blocked",
        "evaluation_window": "evaluation_window",
        "generated_low_confidence": "low_quality_suppressed",
        "reservation_lease_expired_terminal": "terminal_failed",
        "state_capacity_exceeded": "blocked",
    }.get(reason, "running")


def order_preserving_union(*values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for items in values:
        for item in items:
            normalized = str(item)
            if normalized in seen:
                continue
            seen.add(normalized)
            result.append(normalized)
    return result


def persistable_status(status: dict[str, Any]) -> dict[str, Any]:
    return _persistable_status(status)


def _persistable_status(status: dict[str, Any]) -> dict[str, Any]:
    persisted = dict(status)
    _compact_status(persisted)
    persisted.pop("processed_candidate_count", None)
    persisted.pop("suppressed_count", None)
    persisted.pop("call_count_last_hour", None)
    return persisted


def _compact_status(status: dict[str, Any]) -> None:
    truncated_counts = {
        str(field): max(0, int(count or 0))
        for field, count in dict(status.get("truncated_counts") or {}).items()
    }
    processed_candidate_ids = list(status.get("processed_candidate_ids") or [])
    processed = set(processed_candidate_ids)
    suppressed = list(status.get("suppressed") or [])
    suppressed_dropped = max(0, len(suppressed) - MAX_SUPPRESSED_ENTRIES)
    if suppressed_dropped:
        status["suppressed"] = suppressed[-MAX_SUPPRESSED_ENTRIES:]
        truncated_counts["suppressed"] = (
            truncated_counts.get("suppressed", 0) + suppressed_dropped
        )
    terminal_failed = list(status.get("terminal_failed_candidate_ids") or [])
    terminal_dropped = max(
        0,
        len(terminal_failed) - MAX_TERMINAL_FAILED_CANDIDATE_IDS,
    )
    if terminal_dropped and all(
        candidate_id in processed
        for candidate_id in terminal_failed[:terminal_dropped]
    ):
        status["terminal_failed_candidate_ids"] = terminal_failed[
            -MAX_TERMINAL_FAILED_CANDIDATE_IDS:
        ]
        truncated_counts["terminal_failed_candidate_ids"] = (
            truncated_counts.get("terminal_failed_candidate_ids", 0)
            + terminal_dropped
        )
    _release_completed_semantic_entries(
        status,
        reservation_target=MAX_CANDIDATE_RESERVATIONS,
        attempt_target=MAX_CANDIDATE_ATTEMPT_COUNTS,
    )
    status["truncated_counts"] = {
        field: count
        for field, count in truncated_counts.items()
        if count > 0
    }
    status["truncated_count"] = sum(status["truncated_counts"].values())
    status["state_truncated"] = status["truncated_count"] > 0
    status["truncation_policy"] = "retain_recent"
    status["semantic_state_over_capacity"] = (
        len(processed_candidate_ids) > MAX_PROCESSED_CANDIDATE_IDS
        or len(status.get("candidate_reservations") or {})
        > MAX_CANDIDATE_RESERVATIONS
        or len(status.get("candidate_attempt_counts") or {})
        > MAX_CANDIDATE_ATTEMPT_COUNTS
    )
    status["capacity_policy"] = "fail_closed"
    status["capacity_limits"] = {
        "processed_candidate_ids": MAX_PROCESSED_CANDIDATE_IDS,
        "terminal_failed_candidate_ids": MAX_TERMINAL_FAILED_CANDIDATE_IDS,
        "candidate_reservations": MAX_CANDIDATE_RESERVATIONS,
        "candidate_attempt_counts": MAX_CANDIDATE_ATTEMPT_COUNTS,
        "suppressed": MAX_SUPPRESSED_ENTRIES,
    }


def _release_completed_semantic_entries(
    status: dict[str, Any],
    *,
    reservation_target: int,
    attempt_target: int,
) -> None:
    processed = set(status.get("processed_candidate_ids") or [])
    reservations = dict(status.get("candidate_reservations") or {})
    attempts = dict(status.get("candidate_attempt_counts") or {})
    compacted_counts = {
        str(field): max(0, int(count or 0))
        for field, count in dict(status.get("compacted_counts") or {}).items()
    }
    removable_reservations = [
        candidate_id
        for candidate_id, reservation in reservations.items()
        if candidate_id in processed
        and str((reservation or {}).get("status") or "")
        in {"completed", "terminal_failed"}
    ]
    for candidate_id in removable_reservations:
        if len(reservations) <= max(0, reservation_target):
            break
        reservations.pop(candidate_id, None)
        compacted_counts["candidate_reservations"] = (
            compacted_counts.get("candidate_reservations", 0) + 1
        )
        if candidate_id in attempts:
            attempts.pop(candidate_id, None)
            compacted_counts["candidate_attempt_counts"] = (
                compacted_counts.get("candidate_attempt_counts", 0) + 1
            )
    removable_attempts = [
        candidate_id
        for candidate_id in attempts
        if candidate_id in processed
        and (
            candidate_id not in reservations
            or str((reservations.get(candidate_id) or {}).get("status") or "")
            in {"completed", "terminal_failed"}
        )
    ]
    for candidate_id in removable_attempts:
        if len(attempts) <= max(0, attempt_target):
            break
        attempts.pop(candidate_id, None)
        compacted_counts["candidate_attempt_counts"] = (
            compacted_counts.get("candidate_attempt_counts", 0) + 1
        )
    status["candidate_reservations"] = reservations
    status["candidate_attempt_counts"] = attempts
    status["compacted_counts"] = {
        field: count
        for field, count in compacted_counts.items()
        if count > 0
    }
    status["compacted_count"] = sum(status["compacted_counts"].values())


def _semantic_capacity_reason(
    status: dict[str, Any],
    *,
    candidate_id: str,
) -> str | None:
    processed = set(status.get("processed_candidate_ids") or [])
    if candidate_id in processed:
        return None
    reservations = dict(status.get("candidate_reservations") or {})
    attempts = dict(status.get("candidate_attempt_counts") or {})
    needs_reservation_slot = candidate_id not in reservations
    needs_attempt_slot = candidate_id not in attempts
    _release_completed_semantic_entries(
        status,
        reservation_target=(
            MAX_CANDIDATE_RESERVATIONS - 1
            if needs_reservation_slot
            else MAX_CANDIDATE_RESERVATIONS
        ),
        attempt_target=(
            MAX_CANDIDATE_ATTEMPT_COUNTS - 1
            if needs_attempt_slot
            else MAX_CANDIDATE_ATTEMPT_COUNTS
        ),
    )
    reservations = dict(status.get("candidate_reservations") or {})
    attempts = dict(status.get("candidate_attempt_counts") or {})
    unprocessed_reservations = {
        reserved_candidate_id
        for reserved_candidate_id in reservations
        if reserved_candidate_id not in processed
    }
    reserved_processed_slots = len(processed) + len(unprocessed_reservations)
    if candidate_id in unprocessed_reservations:
        processed_capacity_exceeded = (
            reserved_processed_slots > MAX_PROCESSED_CANDIDATE_IDS
        )
    else:
        processed_capacity_exceeded = (
            reserved_processed_slots >= MAX_PROCESSED_CANDIDATE_IDS
        )
    if (
        processed_capacity_exceeded
        or (needs_reservation_slot and len(reservations) >= MAX_CANDIDATE_RESERVATIONS)
        or (needs_attempt_slot and len(attempts) >= MAX_CANDIDATE_ATTEMPT_COUNTS)
    ):
        status["semantic_state_over_capacity"] = True
        return "state_capacity_exceeded"
    return None

"""Admission for automatic second passes; explicit requests remain independent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .discussion_persistence import CURRENT_NEED_CONTRACT, _attention_ready


@dataclass(frozen=True)
class AutomaticCoachAdmission:
    allowed: bool
    reason: str


def automatic_deep_coach_admission(
    *,
    policy: str,
    meeting_goal: str | None,
    has_selected_evidence: bool,
    prior_jobs: Sequence[Mapping[str, Any]],
    now_ms: int,
    current_job_id: str | None = None,
) -> AutomaticCoachAdmission:
    """A Fast completion is not itself a reason to spend a second model turn.

    Low-frequency expansion needs an explicit goal or grounded project/document
    context. Cadence counts durable admissions, including failed attempts, so
    retries and a process restart cannot produce a burst of automatic coaching.
    """

    if policy == "off":
        return AutomaticCoachAdmission(False, "disabled_by_meeting_policy")
    if policy == "low_frequency" and not (
        str(meeting_goal or "").strip() or has_selected_evidence
    ):
        return AutomaticCoachAdmission(False, "fast_answer_sufficient_without_goal_or_evidence")
    interval_ms = 60_000 if policy == "low_frequency" else 20_000
    admitted_at = [
        int(job.get("created_at_ms") or 0)
        for job in prior_jobs
        if (
            job.get("trigger_type") == "answer_ready"
            or job.get("trigger_type") in {"delta", "transcript_delta", "task_due", "discussion_update"}
            and (
                int((job.get("output") or {}).get("provider_attempt_count") or 0) > 0
                or bool(((job.get("output") or {}).get("coach") or {}).get("pi_provider_attempted"))
                or job.get("status") == "running"
                or job.get("trigger_type") == "task_due" and job.get("status") in {"pending", "retry_wait"}
            )
        )
        and str(job.get("id") or "") != str(current_job_id or "")
        and job.get("status") != "cancelled"
        and int(job.get("created_at_ms") or 0) <= now_ms
        and not (
            isinstance(job.get("output"), Mapping)
            and job["output"].get("transport_mode") == "automatic_coach_policy_suppressed"
        )
    ]
    if admitted_at and now_ms - max(admitted_at) < interval_ms:
        return AutomaticCoachAdmission(False, "automatic_deep_coach_cooldown")
    return AutomaticCoachAdmission(True, "goal_or_evidence_expansion" if policy == "low_frequency" else "standard_expansion")


def automatic_intelligence_admission(
    *,
    policy: str,
    prior_jobs: Sequence[Mapping[str, Any]],
    now_ms: int,
    current_job_id: str,
    urgent: bool = False,
    has_new_evidence: bool = False,
) -> AutomaticCoachAdmission:
    """Bound low-frequency background interpretation across different topics.

    Explicit/manual work and material commitment risks are gated separately by
    the caller. Actual model attempts, rather than local skips, spend cadence.
    """

    if policy == "off":
        return AutomaticCoachAdmission(False, "disabled_by_meeting_policy")
    if policy != "low_frequency" or (urgent and has_new_evidence):
        return AutomaticCoachAdmission(True, "material_risk_priority" if urgent else "standard_interpretation")
    attempts = []
    for job in prior_jobs:
        created = int(job.get("created_at_ms") or 0)
        if str(job.get("id") or "") == current_job_id or created > now_ms:
            continue
        if job.get("trigger_type") not in {"delta", "transcript_delta", "answer_ready", "task_due", "discussion_update"}:
            continue
        output = job.get("output") or {}
        attempted = int(output.get("provider_attempt_count") or 0) > 0 or bool(
            (output.get("coach") or {}).get("pi_provider_attempted")
        )
        if attempted or job.get("status") == "running" or (
            job.get("trigger_type") in {"answer_ready", "task_due", "discussion_update"}
            and job.get("status") in {"pending", "retry_wait"}
        ):
            attempts.append(created)
    if attempts and now_ms - max(attempts) < 60_000:
        return AutomaticCoachAdmission(False, "low_frequency_background_cooldown")
    return AutomaticCoachAdmission(True, "low_frequency_background_due")


def is_automatic_participation_discussion(job: Mapping[str, Any]) -> bool:
    metadata = job.get("request_context") or {}
    return (job.get("trigger_type") == "discussion_update"
        and metadata.get("profile") == "participation"
        and metadata.get("task_mode") != "discussion_state")


def is_typed_automatic_participation_discussion(job: Mapping[str, Any]) -> bool:
    metadata = job.get("request_context") or {}
    return (is_automatic_participation_discussion(job)
        and metadata.get("current_need_contract") == CURRENT_NEED_CONTRACT
        and (metadata.get("automatic_direct") is True or metadata.get("automatic_attention") is True
             and _attention_ready({"current_attention": metadata.get("current_attention")})))


def discussion_understanding_ready(discussion: Mapping[str, Any] | None, prior_jobs: Sequence[Mapping[str, Any]]) -> bool:
    """Typed current opportunities need not wait for unrelated ongoing L2."""
    context = (discussion or {}).get("context") or {}
    if context.get("phase") != "discussing" or int(context.get("last_semantic_seq") or 0) <= 0:
        return False
    return not any(job.get("status") in {"pending", "running", "retry_wait"}
        and (job.get("kind") == "correction" and not _attention_ready(context)
            or (job.get("request_context") or {}).get("task_mode") == "discussion_state") for job in prior_jobs)


def automatic_discussion_admission(*, policy: str, prior_jobs: Sequence[Mapping[str, Any]], now_ms: int,
                                    current_job_id: str | None = None,
                                    revised_recovery_job_ids: Sequence[str] = ()) -> AutomaticCoachAdmission:
    """An established discussion is sufficient; preparation is optional."""
    if policy == "off":
        return AutomaticCoachAdmission(False, "disabled_by_meeting_policy")
    for job in prior_jobs:
        if str(job.get("id") or "") == str(current_job_id or "") or job.get("trigger_type") == "user_request":
            continue
        if job.get("trigger_type") not in {"discussion_update", "answer_ready", "task_due"}:
            continue
        if job.get("status") in {"pending", "retry_wait", "running"}:
            return AutomaticCoachAdmission(False, "automatic_analysis_already_pending")
        if (str(job.get("id") or "") in revised_recovery_job_ids
                and is_automatic_participation_discussion(job)
                and job.get("status") == "cancelled"
                and job.get("error_class") == "discussion_evidence_revised"):
            continue
        output = job.get("output") or {}
        if int(output.get("provider_attempt_count") or 0) > 0 and now_ms - int(job.get("created_at_ms") or 0) < 30_000:
            return AutomaticCoachAdmission(False, "automatic_discussion_cooldown")
    return AutomaticCoachAdmission(True, "discussion_opportunity")


def automatic_task_due_admission(
    *,
    policy: str,
    meeting_goal: str | None,
    has_selected_evidence: bool,
    source_valid: bool,
    has_new_evidence: bool,
    decision_id: str,
    input_transcript_seq: int,
    prior_jobs: Sequence[Mapping[str, Any]],
    now_ms: int,
    current_job_id: str | None = None,
) -> AutomaticCoachAdmission:
    """An expired automatic card is not an explicit request for another answer.

    One checked evidence version cannot rearm itself on scheduler ticks. Fresh
    closure is handled locally by the host before this model admission gate.
    """
    if not source_valid:
        return AutomaticCoachAdmission(False, "task_due_source_superseded")
    if policy == "off":
        return AutomaticCoachAdmission(False, "disabled_by_meeting_policy")
    if policy == "low_frequency" and not (
        str(meeting_goal or "").strip() or has_selected_evidence
    ):
        return AutomaticCoachAdmission(False, "task_due_without_goal_or_evidence")
    for job in prior_jobs:
        if (job.get("trigger_type") == "task_due"
                and str(job.get("id") or "") != str(current_job_id or "")
                and str(job.get("work_item_id") or "") == decision_id
                and int(job.get("input_transcript_seq") or 0) >= input_transcript_seq):
            return AutomaticCoachAdmission(False, "task_due_evidence_already_checked")
    if not has_new_evidence and not (str(meeting_goal or "").strip() or has_selected_evidence):
        return AutomaticCoachAdmission(False, "task_due_no_new_evidence")
    # Share cadence with all automatic model work, including failed calls and
    # currently running automatic jobs. Manual user_request remains separate.
    cadence = automatic_deep_coach_admission(
        policy=policy, meeting_goal=meeting_goal, has_selected_evidence=has_selected_evidence,
        prior_jobs=prior_jobs, now_ms=now_ms, current_job_id=current_job_id,
    )
    if not cadence.allowed:
        return cadence
    return AutomaticCoachAdmission(True, "task_due_goal_or_evidence_check")

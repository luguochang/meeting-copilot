#!/usr/bin/env python3
"""Specify and validate real mic shadow-test reports without touching audio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

DRV_ID = "DRV-033"
REPORT_MODE = "real_mic_shadow_test_report_schema"
SCHEMA_VERSION = "real_mic_shadow_test_report.v1"
SCHEMA_STATUS = "specified_not_executable"
EXECUTION_BOUNDARY = "schema_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

APPROVED_REPORT_ROOT = "artifacts/tmp/real_mic_shadow_reports"
APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"

REQUIRED_SECTIONS = [
    "schema_version",
    "session_id",
    "meeting_profile",
    "transcript",
    "asr_metrics",
    "evidence_span_timeline",
    "state_timeline",
    "candidate_card_timeline",
    "feedback_summary",
    "final_decision",
    "privacy_cost_flags",
    "audio_retention",
    "known_limitations",
]
FEEDBACK_LABELS = [
    "useful",
    "would_have_asked",
    "wrong",
    "too_late",
    "too_intrusive",
    "dismissed",
]
FINAL_DECISION_ALLOWED_VALUES = [
    "go",
    "pivot",
    "stop",
    "inconclusive_requires_more_shadow_tests",
]
ASR_METRIC_FIELDS = [
    "duration_seconds",
    "first_partial_latency_ms",
    "final_latency_p95_ms",
    "rtf",
    "raw_cer",
    "normalized_cer",
    "raw_technical_entity_recall",
    "normalized_technical_entity_recall",
    "technical_entity_precision",
    "error_event_count",
    "end_of_stream_event_count",
]
PRIVACY_COST_FALSE_FIELDS = [
    "raw_audio_uploaded",
    "remote_asr_called",
    "llm_called",
    "configs_local_read",
    "user_audio_committed_to_repo",
]
ALLOWED_AUDIO_CHUNK_WRITE_STATUSES = [
    "not_written",
    "written_by_user_approved_shadow_test",
]
ALLOWED_AUDIO_DELETE_STATUSES = [
    "not_applicable_no_audio_written",
    "deleted_after_review",
    "retained_in_ignored_artifact_root_for_user_review",
]
FALSE_SAFETY_FLAGS = [
    "safe_to_access_microphone_now",
    "safe_to_enumerate_audio_devices_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_real_user_audio_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunk_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_mutate_web_session_now",
]
FORBIDDEN_PATH_LABELS = [
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _is_under_root(path: Path, repo_root: Path, approved_root: str) -> bool:
    relative = _repo_relative_path(path, repo_root)
    if relative is None:
        return False
    relative_text = relative.as_posix()
    return relative_text == approved_root or relative_text.startswith(f"{approved_root}/")


def _candidate_report_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts) or _path_has_suffix_parts(
            resolved,
            suffix_parts,
        ):
            errors.append(f"candidate_report_path is blocked: {label}")
    if errors:
        return errors
    if _repo_relative_path(resolved, REPO_ROOT) is None:
        return ["candidate_report_path is outside repository"]
    if not _is_under_root(path, REPO_ROOT, APPROVED_REPORT_ROOT) or not _is_under_root(
        resolved,
        REPO_ROOT,
        APPROVED_REPORT_ROOT,
    ):
        errors.append(f"candidate_report_path is not under approved root: {APPROVED_REPORT_ROOT}")
    if path.suffix.lower() != ".json":
        errors.append("candidate_report_path must be a JSON report file")
    return errors


def _candidate_report_display_path(path_text: str) -> str:
    path = Path(path_text)
    resolved = path if path.is_absolute() else REPO_ROOT / path
    relative = _repo_relative_path(resolved, REPO_ROOT)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_negative_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0


def _is_non_negative_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _validate_top_level(candidate_report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for section in REQUIRED_SECTIONS:
        if section not in candidate_report:
            errors.append(f"missing required section: {section}")
    if candidate_report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if not _is_non_empty_string(candidate_report.get("session_id")):
        errors.append("session_id must be a non-empty string")
    return errors


def _validate_meeting_profile(meeting_profile: Any) -> list[str]:
    if not isinstance(meeting_profile, dict):
        return ["meeting_profile must be an object"]
    errors: list[str] = []
    if meeting_profile.get("meeting_type") != "chinese_technical_review":
        errors.append("meeting_profile.meeting_type must be chinese_technical_review")
    if not _is_non_negative_number(meeting_profile.get("duration_minutes")):
        errors.append("meeting_profile.duration_minutes must be a non-negative number")
    if not _is_non_negative_int(meeting_profile.get("participant_count")):
        errors.append("meeting_profile.participant_count must be a non-negative integer")
    if meeting_profile.get("language") != "zh-CN":
        errors.append("meeting_profile.language must be zh-CN")
    domain_tags = meeting_profile.get("domain_tags")
    if not isinstance(domain_tags, list) or not all(isinstance(tag, str) for tag in domain_tags):
        errors.append("meeting_profile.domain_tags must be a list of strings")
    return errors


def _validate_transcript(transcript: Any) -> list[str]:
    if not isinstance(transcript, dict):
        return ["transcript must be an object"]
    errors: list[str] = []
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return ["transcript.segments must be a list"]
    if transcript.get("segment_count") != len(segments):
        errors.append("transcript.segment_count must equal len(transcript.segments)")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"transcript.segments[{index}] must be an object")
            continue
        for field in ["segment_id", "speaker_label", "text", "source_event_id"]:
            if not _is_non_empty_string(segment.get(field)):
                errors.append(f"transcript.segments[{index}].{field} must be a non-empty string")
        start_ms = segment.get("start_ms")
        end_ms = segment.get("end_ms")
        if not _is_non_negative_int(start_ms):
            errors.append(f"transcript.segments[{index}].start_ms must be a non-negative integer")
        if not _is_non_negative_int(end_ms):
            errors.append(f"transcript.segments[{index}].end_ms must be a non-negative integer")
        if _is_non_negative_int(start_ms) and _is_non_negative_int(end_ms) and end_ms < start_ms:
            errors.append(f"transcript.segments[{index}].end_ms must be >= start_ms")
    return errors


def _validate_asr_metrics(asr_metrics: Any) -> list[str]:
    if not isinstance(asr_metrics, dict):
        return ["asr_metrics must be an object"]
    errors: list[str] = []
    for field in ASR_METRIC_FIELDS:
        if not _is_non_negative_number(asr_metrics.get(field)):
            errors.append(f"asr_metrics.{field} must be a non-negative number")
    return errors


def _validate_timeline(name: str, timeline: Any) -> list[str]:
    if not isinstance(timeline, list):
        return [f"{name} must be a list"]
    errors: list[str] = []
    for index, item in enumerate(timeline):
        if not isinstance(item, dict):
            errors.append(f"{name}[{index}] must be an object")
    return errors


def _validate_evidence_span_timeline(timeline: Any) -> list[str]:
    errors = _validate_timeline("evidence_span_timeline", timeline)
    if errors or not isinstance(timeline, list):
        return errors
    for index, item in enumerate(timeline):
        if not isinstance(item, dict):
            continue
        for field in ["evidence_id", "segment_id", "text", "supports_candidate_id"]:
            if not _is_non_empty_string(item.get(field)):
                errors.append(
                    f"evidence_span_timeline[{index}].{field} must be a non-empty string"
                )
        start_ms = item.get("start_ms")
        end_ms = item.get("end_ms")
        if not _is_non_negative_int(start_ms):
            errors.append(f"evidence_span_timeline[{index}].start_ms must be a non-negative integer")
        if not _is_non_negative_int(end_ms):
            errors.append(f"evidence_span_timeline[{index}].end_ms must be a non-negative integer")
        if _is_non_negative_int(start_ms) and _is_non_negative_int(end_ms) and end_ms < start_ms:
            errors.append(f"evidence_span_timeline[{index}].end_ms must be >= start_ms")
    return errors


def _validate_state_timeline(timeline: Any) -> list[str]:
    errors = _validate_timeline("state_timeline", timeline)
    if errors or not isinstance(timeline, list):
        return errors
    for index, item in enumerate(timeline):
        if not isinstance(item, dict):
            continue
        for field in ["state_id", "state_type", "evidence_id"]:
            if not _is_non_empty_string(item.get(field)):
                errors.append(f"state_timeline[{index}].{field} must be a non-empty string")
        if not _is_non_negative_int(item.get("at_ms")):
            errors.append(f"state_timeline[{index}].at_ms must be a non-negative integer")
    return errors


def _validate_candidate_card_timeline(timeline: Any) -> list[str]:
    errors = _validate_timeline("candidate_card_timeline", timeline)
    if errors or not isinstance(timeline, list):
        return errors
    for index, item in enumerate(timeline):
        if not isinstance(item, dict):
            continue
        for field in ["candidate_id", "card_type", "text"]:
            if not _is_non_empty_string(item.get(field)):
                errors.append(
                    f"candidate_card_timeline[{index}].{field} must be a non-empty string"
                )
        for field in ["created_at_ms", "latency_ms"]:
            if not _is_non_negative_int(item.get(field)):
                errors.append(
                    f"candidate_card_timeline[{index}].{field} must be a non-negative integer"
                )
        evidence_ids = item.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(_is_non_empty_string(evidence_id) for evidence_id in evidence_ids)
        ):
            errors.append(
                "candidate_card_timeline"
                f"[{index}].evidence_ids must be a non-empty list of strings"
            )
    return errors


def _validate_timeline_cross_references(candidate_report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    transcript = candidate_report.get("transcript")
    evidence_spans = candidate_report.get("evidence_span_timeline")
    states = candidate_report.get("state_timeline")
    cards = candidate_report.get("candidate_card_timeline")
    if not (
        isinstance(transcript, dict)
        and isinstance(transcript.get("segments"), list)
        and isinstance(evidence_spans, list)
        and isinstance(states, list)
        and isinstance(cards, list)
    ):
        return errors

    segment_ids = {
        segment.get("segment_id")
        for segment in transcript["segments"]
        if isinstance(segment, dict) and _is_non_empty_string(segment.get("segment_id"))
    }
    evidence_ids = {
        evidence.get("evidence_id")
        for evidence in evidence_spans
        if isinstance(evidence, dict) and _is_non_empty_string(evidence.get("evidence_id"))
    }
    candidate_ids = {
        card.get("candidate_id")
        for card in cards
        if isinstance(card, dict) and _is_non_empty_string(card.get("candidate_id"))
    }

    for index, evidence in enumerate(evidence_spans):
        if not isinstance(evidence, dict):
            continue
        segment_id = evidence.get("segment_id")
        if _is_non_empty_string(segment_id) and segment_id not in segment_ids:
            errors.append(
                f"evidence_span_timeline[{index}].segment_id must reference transcript.segments"
            )
        supports_candidate_id = evidence.get("supports_candidate_id")
        if _is_non_empty_string(supports_candidate_id) and supports_candidate_id not in candidate_ids:
            errors.append(
                "evidence_span_timeline"
                f"[{index}].supports_candidate_id must reference candidate_card_timeline"
            )

    for index, state in enumerate(states):
        if not isinstance(state, dict):
            continue
        evidence_id = state.get("evidence_id")
        if _is_non_empty_string(evidence_id) and evidence_id not in evidence_ids:
            errors.append(f"state_timeline[{index}].evidence_id must reference evidence_span_timeline")

    for index, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        card_evidence_ids = card.get("evidence_ids")
        if isinstance(card_evidence_ids, list) and card_evidence_ids:
            if any(evidence_id not in evidence_ids for evidence_id in card_evidence_ids):
                errors.append(
                    "candidate_card_timeline"
                    f"[{index}].evidence_ids must reference evidence_span_timeline"
                )
    return errors


def _validate_feedback(feedback_summary: Any) -> list[str]:
    if not isinstance(feedback_summary, dict):
        return ["feedback_summary must be an object"]
    errors: list[str] = []
    labels = feedback_summary.get("labels")
    if not isinstance(labels, dict):
        return ["feedback_summary.labels must be an object"]
    for label in FEEDBACK_LABELS:
        if not _is_non_negative_int(labels.get(label)):
            errors.append(f"feedback label {label} must be a non-negative integer")
    for field in ["useful_or_would_have_asked_count", "negative_feedback_count"]:
        if not _is_non_negative_int(feedback_summary.get(field)):
            errors.append(f"feedback_summary.{field} must be a non-negative integer")
    if not errors:
        useful_count = labels["useful"] + labels["would_have_asked"]
        negative_count = labels["wrong"] + labels["too_late"] + labels["too_intrusive"]
        if feedback_summary["useful_or_would_have_asked_count"] != useful_count:
            errors.append(
                "feedback_summary.useful_or_would_have_asked_count must equal useful + would_have_asked"
            )
        if feedback_summary["negative_feedback_count"] != negative_count:
            errors.append(
                "feedback_summary.negative_feedback_count must equal wrong + too_late + too_intrusive"
            )
    return errors


def _validate_final_decision(final_decision: Any) -> list[str]:
    if not isinstance(final_decision, dict):
        return ["final_decision must be an object"]
    errors: list[str] = []
    if final_decision.get("decision") not in FINAL_DECISION_ALLOWED_VALUES:
        errors.append(
            "final_decision.decision must be one of "
            + ", ".join(FINAL_DECISION_ALLOWED_VALUES)
        )
    if not _is_non_empty_string(final_decision.get("reason")):
        errors.append("final_decision.reason must be a non-empty string")
    return errors


def _validate_privacy_cost_flags(privacy_cost_flags: Any) -> list[str]:
    if not isinstance(privacy_cost_flags, dict):
        return ["privacy_cost_flags must be an object"]
    errors: list[str] = []
    for field in PRIVACY_COST_FALSE_FIELDS:
        if privacy_cost_flags.get(field) is not False:
            errors.append(f"privacy_cost_flags.{field} must remain false")
    return errors


def _validate_audio_retention(audio_retention: Any) -> list[str]:
    if not isinstance(audio_retention, dict):
        return ["audio_retention must be an object"]
    errors: list[str] = []
    if audio_retention.get("audio_chunk_root") != APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT:
        errors.append(
            "audio_retention.audio_chunk_root must be "
            + APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT
        )
    if audio_retention.get("audio_chunk_write_status") not in ALLOWED_AUDIO_CHUNK_WRITE_STATUSES:
        errors.append(
            "audio_retention.audio_chunk_write_status must be one of "
            + ", ".join(ALLOWED_AUDIO_CHUNK_WRITE_STATUSES)
        )
    if audio_retention.get("audio_delete_status") not in ALLOWED_AUDIO_DELETE_STATUSES:
        errors.append(
            "audio_retention.audio_delete_status must be one of "
            + ", ".join(ALLOWED_AUDIO_DELETE_STATUSES)
        )
    if audio_retention.get("retention_policy") != "delete_audio_chunks_before_session_discard":
        errors.append("audio_retention.retention_policy must be delete_audio_chunks_before_session_discard")
    return errors


def _validate_known_limitations(known_limitations: Any) -> list[str]:
    if not isinstance(known_limitations, list):
        return ["known_limitations must be a list"]
    if not known_limitations:
        return ["known_limitations must contain at least one limitation"]
    if not all(isinstance(item, str) and item.strip() for item in known_limitations):
        return ["known_limitations must contain non-empty strings"]
    return []


def _validate_decision_support(candidate_report: dict[str, Any]) -> list[str]:
    feedback = candidate_report.get("feedback_summary")
    final_decision = candidate_report.get("final_decision")
    if not isinstance(feedback, dict) or not isinstance(final_decision, dict):
        return []
    if final_decision.get("decision") != "go":
        return []
    errors: list[str] = []
    useful_or_would_have_asked_count = feedback.get("useful_or_would_have_asked_count")
    negative_feedback_count = feedback.get("negative_feedback_count")
    if _is_non_negative_int(useful_or_would_have_asked_count) and useful_or_would_have_asked_count < 2:
        errors.append("final_decision.go requires useful_or_would_have_asked_count >= 2")
    if _is_non_negative_int(negative_feedback_count) and negative_feedback_count > 1:
        errors.append("final_decision.go requires negative_feedback_count <= 1")
    return errors


def validate_candidate_report(candidate_report: Any) -> list[str]:
    if not isinstance(candidate_report, dict):
        return ["candidate report must be an object"]
    errors = _validate_top_level(candidate_report)
    if errors:
        present = set(candidate_report)
    else:
        present = set(REQUIRED_SECTIONS)
    if "meeting_profile" in present:
        errors.extend(_validate_meeting_profile(candidate_report.get("meeting_profile")))
    if "transcript" in present:
        errors.extend(_validate_transcript(candidate_report.get("transcript")))
    if "asr_metrics" in present:
        errors.extend(_validate_asr_metrics(candidate_report.get("asr_metrics")))
    if "evidence_span_timeline" in present:
        errors.extend(_validate_evidence_span_timeline(candidate_report.get("evidence_span_timeline")))
    if "state_timeline" in present:
        errors.extend(_validate_state_timeline(candidate_report.get("state_timeline")))
    if "candidate_card_timeline" in present:
        errors.extend(_validate_candidate_card_timeline(candidate_report.get("candidate_card_timeline")))
    if "feedback_summary" in present:
        errors.extend(_validate_feedback(candidate_report.get("feedback_summary")))
    if "final_decision" in present:
        errors.extend(_validate_final_decision(candidate_report.get("final_decision")))
    if "privacy_cost_flags" in present:
        errors.extend(_validate_privacy_cost_flags(candidate_report.get("privacy_cost_flags")))
    if "audio_retention" in present:
        errors.extend(_validate_audio_retention(candidate_report.get("audio_retention")))
    if "known_limitations" in present:
        errors.extend(_validate_known_limitations(candidate_report.get("known_limitations")))
    errors.extend(_validate_timeline_cross_references(candidate_report))
    errors.extend(_validate_decision_support(candidate_report))
    return errors


def _candidate_summary(candidate_report: dict[str, Any]) -> dict[str, Any]:
    meeting_profile = candidate_report["meeting_profile"]
    transcript = candidate_report["transcript"]
    feedback = candidate_report["feedback_summary"]
    return {
        "session_id": candidate_report["session_id"],
        "duration_minutes": meeting_profile["duration_minutes"],
        "segment_count": transcript["segment_count"],
        "evidence_span_count": len(candidate_report["evidence_span_timeline"]),
        "state_event_count": len(candidate_report["state_timeline"]),
        "candidate_card_count": len(candidate_report["candidate_card_timeline"]),
        "useful_or_would_have_asked_count": feedback["useful_or_would_have_asked_count"],
        "negative_feedback_count": feedback["negative_feedback_count"],
        "final_decision": candidate_report["final_decision"]["decision"],
    }


def _base_report() -> dict[str, Any]:
    return {
        "drv_id": DRV_ID,
        "report_mode": REPORT_MODE,
        "schema_version": SCHEMA_VERSION,
        "schema_status": SCHEMA_STATUS,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_report_root": APPROVED_REPORT_ROOT,
        "approved_pre_pilot_audio_chunk_root": APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT,
        "required_sections": REQUIRED_SECTIONS,
        "feedback_labels": FEEDBACK_LABELS,
        "final_decision_allowed_values": FINAL_DECISION_ALLOWED_VALUES,
        "asr_metric_fields": ASR_METRIC_FIELDS,
        "privacy_cost_false_fields": PRIVACY_COST_FALSE_FIELDS,
        "candidate_report_path": None,
        "candidate_report_read_status": "not_requested",
        "candidate_report_status": "not_provided",
        "candidate_report_validation_status": "not_run",
        "candidate_report_validation_errors": [],
        "candidate_report_summary": None,
        **_false_safety_flags(),
    }


def _candidate_path_blocked_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report["candidate_report_read_status"] = "blocked"
    report["candidate_report_status"] = "blocked_by_path_guard"
    report["candidate_report_validation_status"] = "failed"
    report["candidate_report_validation_errors"] = errors
    return report


def _load_candidate_report(path_text: str) -> tuple[Any | None, list[str], str]:
    path = Path(path_text)
    path_errors = _candidate_report_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked"
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        return json.loads(resolved.read_text(encoding="utf-8")), [], "read"
    except FileNotFoundError:
        return None, ["candidate_report_path does not exist"], "failed"
    except json.JSONDecodeError:
        return None, ["candidate_report_path must contain valid JSON"], "failed"


def build_real_mic_shadow_test_report_schema(
    *,
    candidate_report: dict[str, Any] | None = None,
    candidate_report_path: str | None = None,
) -> dict[str, Any]:
    if candidate_report is not None and candidate_report_path is not None:
        report = _base_report()
        report["candidate_report_status"] = "blocked_by_schema_validation"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = [
            "provide either candidate_report or candidate_report_path, not both"
        ]
        return report

    report = _base_report()
    resolved_candidate = candidate_report

    if candidate_report_path is not None:
        candidate, load_errors, read_status = _load_candidate_report(candidate_report_path)
        if read_status == "blocked":
            return _candidate_path_blocked_report(load_errors)
        report["candidate_report_path"] = APPROVED_REPORT_ROOT
        report["candidate_report_read_status"] = read_status
        if load_errors:
            report["candidate_report_status"] = "blocked_by_schema_validation"
            report["candidate_report_validation_status"] = "failed"
            report["candidate_report_validation_errors"] = load_errors
            return report
        report["candidate_report_path"] = _candidate_report_display_path(candidate_report_path)
        resolved_candidate = candidate

    if resolved_candidate is None:
        return report

    errors = validate_candidate_report(resolved_candidate)
    if errors:
        report["candidate_report_status"] = "blocked_by_schema_validation"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = errors
        return report

    report["candidate_report_status"] = "schema_validated_no_audio_access"
    report["candidate_report_validation_status"] = "passed"
    report["candidate_report_validation_errors"] = []
    report["candidate_report_summary"] = _candidate_summary(resolved_candidate)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_real_mic_shadow_test_report_schema(candidate_report_path=args.candidate_report)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["candidate_report_validation_status"] in {"not_run", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Apply user feedback labels to DRV-033/035 shadow reports without side effects."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import shadow_report_ingestion_export_feedback  # noqa: E402


DRV_ID = "DRV-038"
REPORT_MODE = "shadow_report_feedback_ingestion"
REPORT_VERSION = "shadow_report_feedback_ingestion.v1"
EXECUTION_BOUNDARY = "feedback_preview_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

FEEDBACK_LABELS = [
    "useful",
    "would_have_asked",
    "wrong",
    "too_late",
    "too_intrusive",
    "dismissed",
]
POSITIVE_LABELS = {"useful", "would_have_asked"}
NEGATIVE_LABELS = {"wrong", "too_late", "too_intrusive"}
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
    "safe_to_write_candidate_report_now",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _sync_imported_tool_roots() -> None:
    shadow_report_ingestion_export_feedback.REPO_ROOT = REPO_ROOT
    shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema.REPO_ROOT = REPO_ROOT


def _base_report() -> dict[str, Any]:
    return {
        "drv_id": DRV_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "feedback_ingestion_status": "not_run",
        "candidate_report_path": None,
        "candidate_report_read_status": "not_requested",
        "source_report_kind": None,
        "candidate_report_validation_status": "not_run",
        "candidate_report_validation_errors": [],
        "feedback_entry_count": 0,
        "feedback_summary_delta": None,
        "updated_candidate_report": None,
        "readiness_report": None,
        "go_evidence_status": "not_evaluated",
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _load_candidate_from_path(path_text: str) -> tuple[Any | None, str | None, str, str | None, list[str]]:
    loaded, errors, read_status, display_path = shadow_report_ingestion_export_feedback._load_json_report(
        path_text,
    )
    if errors:
        return None, None, read_status, display_path, errors
    candidate, source_kind, extraction_errors = shadow_report_ingestion_export_feedback._extract_candidate_report(
        loaded,
    )
    return candidate, source_kind, read_status, display_path, extraction_errors


def _candidate_cards(candidate_report: dict[str, Any]) -> list[dict[str, Any]]:
    cards = candidate_report.get("candidate_card_timeline")
    return cards if isinstance(cards, list) else []


def _feedback_validation_errors(
    candidate_report: dict[str, Any],
    feedback_entries: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(feedback_entries, list) or not feedback_entries:
        return ["feedback_entries must be a non-empty list"]

    candidate_ids = {
        card.get("candidate_id")
        for card in _candidate_cards(candidate_report)
        if isinstance(card, dict) and isinstance(card.get("candidate_id"), str)
    }
    seen_candidate_ids: set[str] = set()
    duplicate_found = False
    valid_labels = set(FEEDBACK_LABELS)
    label_error_text = "feedback_entries[{index}].label must be one of " + ", ".join(sorted(valid_labels))
    for index, entry in enumerate(feedback_entries):
        if not isinstance(entry, dict):
            errors.append(f"feedback_entries[{index}] must be an object")
            continue
        candidate_id = entry.get("candidate_id")
        label = entry.get("label")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            errors.append(f"feedback_entries[{index}].candidate_id must be a non-empty string")
        elif candidate_id not in candidate_ids:
            errors.append(
                f"feedback_entries[{index}].candidate_id must reference candidate_card_timeline"
            )
        elif candidate_id in seen_candidate_ids:
            duplicate_found = True
        else:
            seen_candidate_ids.add(candidate_id)
        if label not in valid_labels:
            errors.append(label_error_text.format(index=index))
    if duplicate_found:
        errors.append("feedback_entries must contain at most one label per candidate_id")
    return errors


def _apply_feedback_entries(
    candidate_report: dict[str, Any],
    feedback_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(candidate_report)
    labels_by_candidate = {
        entry["candidate_id"]: entry["label"]
        for entry in feedback_entries
        if isinstance(entry, dict)
    }
    for card in _candidate_cards(updated):
        if not isinstance(card, dict):
            continue
        candidate_id = card.get("candidate_id")
        if candidate_id in labels_by_candidate:
            card["feedback_label"] = labels_by_candidate[candidate_id]
    updated["feedback_summary"] = _feedback_summary_from_cards(updated)
    updated["final_decision"] = _final_decision_from_feedback(updated)
    return updated


def _feedback_summary_from_cards(candidate_report: dict[str, Any]) -> dict[str, Any]:
    labels = {label: 0 for label in FEEDBACK_LABELS}
    for card in _candidate_cards(candidate_report):
        if not isinstance(card, dict):
            continue
        label = card.get("feedback_label")
        if label in labels:
            labels[label] += 1
    return {
        "labels": labels,
        "useful_or_would_have_asked_count": sum(labels[label] for label in POSITIVE_LABELS),
        "negative_feedback_count": sum(labels[label] for label in NEGATIVE_LABELS),
    }


def _final_decision_from_feedback(candidate_report: dict[str, Any]) -> dict[str, str]:
    feedback = candidate_report.get("feedback_summary")
    audio_status = candidate_report.get("audio_retention", {}).get("audio_chunk_write_status")
    if not isinstance(feedback, dict):
        return {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Feedback summary is unavailable.",
        }
    positive_count = int(feedback.get("useful_or_would_have_asked_count") or 0)
    negative_count = int(feedback.get("negative_feedback_count") or 0)
    feedback_total = positive_count + negative_count + int(
        feedback.get("labels", {}).get("dismissed", 0)
        if isinstance(feedback.get("labels"), dict)
        else 0
    )
    if audio_status != "written_by_user_approved_shadow_test":
        return {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Replay or preview feedback cannot be used as Go evidence.",
        }
    if positive_count >= 2 and negative_count <= 1:
        return {
            "decision": "go",
            "reason": "Real shadow-test feedback met the minimum Go evidence threshold.",
        }
    if feedback_total == 0:
        return {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Feedback has not been collected yet.",
        }
    if negative_count >= 2 and positive_count == 0:
        return {
            "decision": "stop",
            "reason": "Negative feedback dominated the real shadow-test cards.",
        }
    return {
        "decision": "pivot",
        "reason": "Feedback was collected but did not meet the Go evidence threshold.",
    }


def _feedback_summary_delta(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_labels = before.get("feedback_summary", {}).get("labels", {})
    after_labels = after.get("feedback_summary", {}).get("labels", {})
    label_delta = {
        label: int(after_labels.get(label, 0)) - int(before_labels.get(label, 0))
        for label in FEEDBACK_LABELS
    }
    return {
        "labels": label_delta,
        "useful_or_would_have_asked_count": after["feedback_summary"][
            "useful_or_would_have_asked_count"
        ]
        - int(before.get("feedback_summary", {}).get("useful_or_would_have_asked_count") or 0),
        "negative_feedback_count": after["feedback_summary"]["negative_feedback_count"]
        - int(before.get("feedback_summary", {}).get("negative_feedback_count") or 0),
    }


def _status_for_updated_report(updated_candidate_report: dict[str, Any]) -> tuple[str, str]:
    audio_status = updated_candidate_report.get("audio_retention", {}).get("audio_chunk_write_status")
    decision = updated_candidate_report.get("final_decision", {}).get("decision")
    if audio_status != "written_by_user_approved_shadow_test":
        return (
            "shadow_report_feedback_ingested_preview_only",
            "not_go_evidence_replay_or_feedback_missing",
        )
    if decision == "go":
        return (
            "shadow_report_feedback_ingested",
            "go_evidence_supported_by_real_feedback_report",
        )
    return (
        "shadow_report_feedback_ingested",
        "not_go_evidence_feedback_collected_but_go_threshold_not_met",
    )


def build_shadow_report_feedback_ingestion(
    *,
    candidate_report: dict[str, Any] | None = None,
    candidate_report_path: str | None = None,
    feedback_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _sync_imported_tool_roots()
    report = _base_report()
    if candidate_report is not None and candidate_report_path is not None:
        report["feedback_ingestion_status"] = "blocked_by_validation"
        report["validation_errors"] = [
            "provide either candidate_report or candidate_report_path, not both"
        ]
        return report

    raw_candidate_report: Any = candidate_report
    if candidate_report_path is not None:
        candidate, source_kind, read_status, display_path, errors = _load_candidate_from_path(
            candidate_report_path,
        )
        report["candidate_report_read_status"] = read_status
        report["candidate_report_path"] = display_path
        if read_status == "blocked":
            report["feedback_ingestion_status"] = "blocked_by_path_guard"
            report["validation_errors"] = errors
            return report
        if errors:
            report["feedback_ingestion_status"] = "blocked_by_source_report"
            report["validation_errors"] = errors
            return report
        raw_candidate_report = candidate
        report["source_report_kind"] = source_kind
    else:
        report["source_report_kind"] = "drv033_candidate_report" if candidate_report is not None else None

    if raw_candidate_report is None:
        return report
    if not isinstance(raw_candidate_report, dict):
        report["feedback_ingestion_status"] = "blocked_by_candidate_report_schema"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = ["candidate report must be an object"]
        return report

    candidate_errors = (
        shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema.validate_candidate_report(
            raw_candidate_report,
        )
    )
    if candidate_errors:
        report["feedback_ingestion_status"] = "blocked_by_candidate_report_schema"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = candidate_errors
        return report
    report["candidate_report_validation_status"] = "passed"

    entries = feedback_entries or []
    report["feedback_entry_count"] = len(entries) if isinstance(entries, list) else 0
    feedback_errors = _feedback_validation_errors(raw_candidate_report, entries)
    if feedback_errors:
        report["feedback_ingestion_status"] = "blocked_by_feedback_validation"
        report["validation_errors"] = feedback_errors
        return report

    updated = _apply_feedback_entries(raw_candidate_report, entries)
    updated_errors = (
        shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema.validate_candidate_report(
            updated,
        )
    )
    if updated_errors:
        report["feedback_ingestion_status"] = "blocked_by_updated_candidate_report_schema"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = updated_errors
        return report

    readiness_report = shadow_report_ingestion_export_feedback.build_shadow_report_ingestion_export_feedback(
        candidate_report=updated,
    )
    status, go_evidence_status = _status_for_updated_report(updated)
    report["feedback_ingestion_status"] = status
    report["feedback_summary_delta"] = _feedback_summary_delta(raw_candidate_report, updated)
    report["updated_candidate_report"] = updated
    report["readiness_report"] = readiness_report
    report["go_evidence_status"] = go_evidence_status
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report-path")
    parser.add_argument(
        "--feedback-json",
        default="[]",
        help="JSON array of {candidate_id,label} feedback entries",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        feedback_entries = json.loads(args.feedback_json)
    except json.JSONDecodeError:
        feedback_entries = []
    report = build_shadow_report_feedback_ingestion(
        candidate_report_path=args.candidate_report_path,
        feedback_entries=feedback_entries,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["feedback_ingestion_status"] in {
        "not_run",
        "shadow_report_feedback_ingested",
        "shadow_report_feedback_ingested_preview_only",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

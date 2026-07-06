#!/usr/bin/env python3
"""Ingest DRV-033/035 shadow reports into export and feedback readiness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import real_mic_shadow_test_report_schema  # noqa: E402


DRV_ID = "DRV-036"
REPORT_MODE = "shadow_report_ingestion_export_feedback"
REPORT_VERSION = "shadow_report_ingestion_export_feedback.v1"
EXECUTION_BOUNDARY = "report_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

APPROVED_CANDIDATE_REPORT_ROOT = "artifacts/tmp/real_mic_shadow_reports"
APPROVED_ADAPTER_REPORT_ROOT = "artifacts/tmp/asr_reports"
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
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


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_root(path: Path, root: str) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    relative_text = relative.as_posix()
    return relative_text == root or relative_text.startswith(f"{root}/")


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
    if _repo_relative_path(resolved) is None:
        return ["candidate_report_path is outside repository"]
    allowed = (
        _is_under_root(path, APPROVED_CANDIDATE_REPORT_ROOT)
        and _is_under_root(resolved, APPROVED_CANDIDATE_REPORT_ROOT)
    ) or (
        _is_under_root(path, APPROVED_ADAPTER_REPORT_ROOT)
        and _is_under_root(resolved, APPROVED_ADAPTER_REPORT_ROOT)
    )
    if not allowed:
        errors.append(
            "candidate_report_path is not under approved roots: "
            + APPROVED_CANDIDATE_REPORT_ROOT
            + ", "
            + APPROVED_ADAPTER_REPORT_ROOT
        )
    if path.suffix.lower() != ".json":
        errors.append("candidate_report_path must be a JSON report file")
    return errors


def _base_report() -> dict[str, Any]:
    return {
        "drv_id": DRV_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_candidate_report_root": APPROVED_CANDIDATE_REPORT_ROOT,
        "approved_adapter_report_root": APPROVED_ADAPTER_REPORT_ROOT,
        "ingestion_status": "not_run",
        "candidate_report_path": None,
        "candidate_report_read_status": "not_requested",
        "source_report_kind": None,
        "candidate_report_validation_status": "not_run",
        "candidate_report_validation_errors": [],
        "timeline_counts": None,
        "feedback_analysis": None,
        "feedback_collection_status": "not_run",
        "final_decision_readiness_status": "not_run",
        "export_readiness_status": "not_run",
        "json_export_preview": None,
        "markdown_export_preview": None,
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _load_json_report(path_text: str) -> tuple[Any | None, list[str], str, str | None]:
    path = Path(path_text)
    path_errors = _candidate_report_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked", None
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        relative = _repo_relative_path(resolved)
        display_path = relative.as_posix() if relative is not None else "<redacted_invalid_path>"
        return json.loads(resolved.read_text(encoding="utf-8")), [], "read", display_path
    except FileNotFoundError:
        return None, ["candidate_report_path does not exist"], "failed", None
    except json.JSONDecodeError:
        return None, ["candidate_report_path must contain valid JSON"], "failed", None


def _extract_candidate_report(report: Any) -> tuple[Any | None, str, list[str]]:
    if not isinstance(report, dict):
        return None, "unknown", ["candidate report must be an object"]
    if report.get("adapter_id") == "DRV-035":
        errors: list[str] = []
        if report.get("adapter_status") != "shadow_report_draft_created":
            errors.append("DRV-035 adapter_status must be shadow_report_draft_created")
        if report.get("candidate_report_validation_status") != "passed":
            errors.append("DRV-035 candidate_report_validation_status must be passed")
        candidate = report.get("candidate_report")
        if candidate is None:
            errors.append("DRV-035 candidate_report must be present")
        return candidate, "drv035_adapter_report", errors
    return report, "drv033_candidate_report", []


def _timeline_counts(candidate_report: dict[str, Any]) -> dict[str, int]:
    transcript = candidate_report.get("transcript") if isinstance(candidate_report.get("transcript"), dict) else {}
    return {
        "transcript_segments": len(transcript.get("segments") or []),
        "evidence_spans": len(candidate_report.get("evidence_span_timeline") or []),
        "state_events": len(candidate_report.get("state_timeline") or []),
        "candidate_cards": len(candidate_report.get("candidate_card_timeline") or []),
    }


def _feedback_analysis(candidate_report: dict[str, Any], timeline_counts: dict[str, int]) -> dict[str, Any]:
    feedback = candidate_report.get("feedback_summary") if isinstance(candidate_report.get("feedback_summary"), dict) else {}
    useful_count = int(feedback.get("useful_or_would_have_asked_count") or 0)
    negative_count = int(feedback.get("negative_feedback_count") or 0)
    card_count = timeline_counts["candidate_cards"]
    if card_count:
        usefulness_ratio = min(1.0, round(useful_count / card_count, 3))
        negative_ratio = min(1.0, round(negative_count / card_count, 3))
    else:
        usefulness_ratio = 0.0
        negative_ratio = 0.0
    return {
        "candidate_card_count": card_count,
        "useful_or_would_have_asked_count": useful_count,
        "negative_feedback_count": negative_count,
        "usefulness_ratio": usefulness_ratio,
        "negative_ratio": negative_ratio,
    }


def _feedback_collection_status(
    candidate_report: dict[str, Any],
    feedback_analysis: dict[str, Any],
) -> str:
    decision = candidate_report.get("final_decision", {}).get("decision")
    feedback_total = (
        feedback_analysis["useful_or_would_have_asked_count"]
        + feedback_analysis["negative_feedback_count"]
    )
    if decision == "inconclusive_requires_more_shadow_tests" and feedback_total == 0:
        return "feedback_required_before_decision"
    if feedback_total > 0:
        return "feedback_collected"
    return "feedback_missing"


def _final_decision_readiness_status(
    candidate_report: dict[str, Any],
    feedback_analysis: dict[str, Any],
) -> str:
    decision = candidate_report.get("final_decision", {}).get("decision")
    if decision == "go":
        if (
            feedback_analysis["useful_or_would_have_asked_count"] >= 2
            and feedback_analysis["negative_feedback_count"] <= 1
        ):
            return "go_supported_by_feedback"
        return "go_not_supported_by_feedback"
    if decision in {"pivot", "stop"}:
        return f"{decision}_recorded"
    return "inconclusive_requires_more_shadow_tests"


def _export_readiness_status(
    candidate_report: dict[str, Any],
    final_decision_readiness_status: str,
) -> str:
    audio_status = candidate_report.get("audio_retention", {}).get("audio_chunk_write_status")
    decision = candidate_report.get("final_decision", {}).get("decision")
    if audio_status == "not_written" or decision == "inconclusive_requires_more_shadow_tests":
        return "draft_export_preview_only"
    if final_decision_readiness_status == "go_supported_by_feedback" or decision in {"pivot", "stop"}:
        return "ready_for_shadow_test_export"
    return "export_preview_requires_feedback_review"


def _json_export_preview(
    candidate_report: dict[str, Any],
    timeline_counts: dict[str, int],
    feedback_analysis: dict[str, Any],
    final_decision_readiness_status: str,
) -> dict[str, Any]:
    return {
        "session_id": candidate_report.get("session_id"),
        "meeting_profile": candidate_report.get("meeting_profile"),
        "timeline_counts": timeline_counts,
        "asr_metrics": candidate_report.get("asr_metrics"),
        "feedback_analysis": feedback_analysis,
        "final_decision": candidate_report.get("final_decision"),
        "go_pivot_stop": {
            "decision": candidate_report.get("final_decision", {}).get("decision"),
            "readiness_status": final_decision_readiness_status,
        },
        "candidate_cards": candidate_report.get("candidate_card_timeline"),
        "privacy_cost_flags": candidate_report.get("privacy_cost_flags"),
        "audio_retention": candidate_report.get("audio_retention"),
    }


def _markdown_export_preview(
    candidate_report: dict[str, Any],
    timeline_counts: dict[str, int],
    feedback_analysis: dict[str, Any],
    export_readiness_status: str,
    final_decision_readiness_status: str,
) -> str:
    decision = candidate_report.get("final_decision", {})
    cards = candidate_report.get("candidate_card_timeline") or []
    evidence_spans = candidate_report.get("evidence_span_timeline") or []
    lines = [
        "# Shadow Test Report: " + str(candidate_report.get("session_id", "unknown-session")),
    ]
    if export_readiness_status == "draft_export_preview_only":
        lines.append("")
        lines.append("Draft only; not real mic validation.")
    lines.extend(
        [
            "",
            "## Go/Pivot/Stop",
            "- decision: " + str(decision.get("decision")),
            "- readiness: " + final_decision_readiness_status,
            "- reason: " + str(decision.get("reason", "")),
            "",
            "## Feedback Summary",
            "- useful_or_would_have_asked: "
            + str(feedback_analysis["useful_or_would_have_asked_count"]),
            "- negative: " + str(feedback_analysis["negative_feedback_count"]),
            "",
            "## Timeline Counts",
            "- transcript_segments: " + str(timeline_counts["transcript_segments"]),
            "- evidence_spans: " + str(timeline_counts["evidence_spans"]),
            "- state_events: " + str(timeline_counts["state_events"]),
            "- candidate_cards: " + str(timeline_counts["candidate_cards"]),
            "",
            "## Candidate Cards",
        ]
    )
    for card in cards:
        if isinstance(card, dict):
            lines.append("- " + str(card.get("candidate_id")) + ": " + str(card.get("text", "")))
    lines.append("")
    lines.append("## Evidence")
    for evidence in evidence_spans:
        if isinstance(evidence, dict):
            lines.append("- " + str(evidence.get("evidence_id")) + ": " + str(evidence.get("text", "")))
    return "\n".join(lines)


def build_shadow_report_ingestion_export_feedback(
    *,
    candidate_report: dict[str, Any] | None = None,
    candidate_report_path: str | None = None,
) -> dict[str, Any]:
    if candidate_report is not None and candidate_report_path is not None:
        report = _base_report()
        report["ingestion_status"] = "blocked_by_validation"
        report["validation_errors"] = [
            "provide either candidate_report or candidate_report_path, not both"
        ]
        return report

    report = _base_report()
    raw_report: Any = candidate_report
    if candidate_report_path is not None:
        loaded, errors, read_status, display_path = _load_json_report(candidate_report_path)
        report["candidate_report_read_status"] = read_status
        report["candidate_report_path"] = display_path
        if read_status == "blocked":
            report["ingestion_status"] = "blocked_by_path_guard"
            report["validation_errors"] = errors
            return report
        if errors:
            report["ingestion_status"] = "blocked_by_validation"
            report["validation_errors"] = errors
            return report
        raw_report = loaded

    if raw_report is None:
        return report

    resolved_candidate, source_kind, extraction_errors = _extract_candidate_report(raw_report)
    report["source_report_kind"] = source_kind
    if extraction_errors:
        report["ingestion_status"] = "blocked_by_source_report"
        report["validation_errors"] = extraction_errors
        return report

    candidate_errors = real_mic_shadow_test_report_schema.validate_candidate_report(
        resolved_candidate
    )
    if candidate_errors:
        report["ingestion_status"] = "blocked_by_candidate_report_schema"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = candidate_errors
        return report

    counts = _timeline_counts(resolved_candidate)
    feedback = _feedback_analysis(resolved_candidate, counts)
    feedback_status = _feedback_collection_status(resolved_candidate, feedback)
    decision_status = _final_decision_readiness_status(resolved_candidate, feedback)
    export_status = _export_readiness_status(resolved_candidate, decision_status)
    report["ingestion_status"] = "shadow_report_ingested_for_export_feedback"
    report["candidate_report_validation_status"] = "passed"
    report["candidate_report_validation_errors"] = []
    report["timeline_counts"] = counts
    report["feedback_analysis"] = feedback
    report["feedback_collection_status"] = feedback_status
    report["final_decision_readiness_status"] = decision_status
    report["export_readiness_status"] = export_status
    report["json_export_preview"] = _json_export_preview(
        resolved_candidate,
        counts,
        feedback,
        decision_status,
    )
    report["markdown_export_preview"] = _markdown_export_preview(
        resolved_candidate,
        counts,
        feedback,
        export_status,
        decision_status,
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report-path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_shadow_report_ingestion_export_feedback(
        candidate_report_path=args.candidate_report_path,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["ingestion_status"] in {
        "not_run",
        "shadow_report_ingested_for_export_feedback",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

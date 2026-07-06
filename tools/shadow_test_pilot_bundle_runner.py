#!/usr/bin/env python3
"""Bundle shadow-test feedback ingestion and ignored export file writing."""

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

import shadow_report_export_file_writer  # noqa: E402
import shadow_report_feedback_ingestion  # noqa: E402


DRV_ID = "DRV-039"
REPORT_MODE = "shadow_test_pilot_bundle_runner"
REPORT_VERSION = "shadow_test_pilot_bundle_runner.v1"
EXECUTION_BOUNDARY = "pilot_bundle_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

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
    "safe_to_download_public_audio_now",
    "safe_to_download_models_now",
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_spawn_worker_process_now",
    "safe_to_mutate_web_session_now",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _sync_imported_tool_roots() -> None:
    shadow_report_feedback_ingestion.REPO_ROOT = REPO_ROOT
    shadow_report_feedback_ingestion.shadow_report_ingestion_export_feedback.REPO_ROOT = REPO_ROOT
    shadow_report_feedback_ingestion.shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema.REPO_ROOT = REPO_ROOT

    shadow_report_export_file_writer.REPO_ROOT = REPO_ROOT
    shadow_report_export_file_writer.shadow_report_ingestion_export_feedback.REPO_ROOT = REPO_ROOT
    shadow_report_export_file_writer.shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema.REPO_ROOT = REPO_ROOT


def _base_report(
    *,
    candidate_report_path: str,
    output_root: str,
) -> dict[str, Any]:
    return {
        "drv_id": DRV_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "pilot_bundle_status": "not_run",
        "candidate_report_path": candidate_report_path,
        "output_root": output_root,
        "feedback_ingestion_status": None,
        "export_file_write_status": None,
        "go_evidence_status": "not_evaluated",
        "final_decision": None,
        "written_file_count": 0,
        "bundle_artifacts": [],
        "feedback_report": None,
        "export_report": None,
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _feedback_blocked(feedback_report: dict[str, Any]) -> bool:
    status = feedback_report.get("feedback_ingestion_status")
    return isinstance(status, str) and status.startswith("blocked_")


def _export_blocked(export_report: dict[str, Any]) -> bool:
    status = export_report.get("export_file_write_status")
    return isinstance(status, str) and status.startswith("blocked_")


def _export_write_ok(export_report: dict[str, Any]) -> bool:
    return export_report.get("export_file_write_status") in {
        "written_to_ignored_artifact_root",
        "idempotent_existing_files_match",
    }


def build_shadow_test_pilot_bundle(
    *,
    candidate_report_path: str,
    feedback_entries: list[dict[str, Any]],
    output_root: str = shadow_report_export_file_writer.APPROVED_EXPORT_ROOT,
) -> dict[str, Any]:
    _sync_imported_tool_roots()
    report = _base_report(
        candidate_report_path=candidate_report_path,
        output_root=output_root,
    )

    output_errors = shadow_report_export_file_writer._output_root_errors(output_root)
    if output_errors:
        report["pilot_bundle_status"] = "blocked_by_output_root_guard"
        report["validation_errors"] = output_errors
        return report

    feedback_report = shadow_report_feedback_ingestion.build_shadow_report_feedback_ingestion(
        candidate_report_path=candidate_report_path,
        feedback_entries=feedback_entries,
    )
    report["feedback_report"] = feedback_report
    report["feedback_ingestion_status"] = feedback_report.get("feedback_ingestion_status")
    report["go_evidence_status"] = feedback_report.get("go_evidence_status", "not_evaluated")

    updated_candidate_report = feedback_report.get("updated_candidate_report")
    if isinstance(updated_candidate_report, dict):
        report["final_decision"] = updated_candidate_report.get("final_decision", {}).get(
            "decision",
        )

    if _feedback_blocked(feedback_report):
        report["pilot_bundle_status"] = "blocked_by_feedback_ingestion"
        report["validation_errors"] = list(feedback_report.get("validation_errors") or [])
        report["validation_errors"].extend(
            feedback_report.get("candidate_report_validation_errors") or [],
        )
        return report

    if not isinstance(updated_candidate_report, dict):
        report["pilot_bundle_status"] = "blocked_by_feedback_ingestion"
        report["validation_errors"] = ["updated_candidate_report must be present"]
        return report

    export_report = shadow_report_export_file_writer.build_shadow_report_export_file_write(
        candidate_report=updated_candidate_report,
        output_root=output_root,
    )
    report["export_report"] = export_report
    report["export_file_write_status"] = export_report.get("export_file_write_status")
    report["go_evidence_status"] = export_report.get(
        "go_evidence_status",
        report["go_evidence_status"],
    )
    report["written_file_count"] = int(export_report.get("written_file_count") or 0)
    report["bundle_artifacts"] = list(export_report.get("written_files") or [])

    if _export_blocked(export_report):
        report["pilot_bundle_status"] = "blocked_by_export_file_writer"
        report["validation_errors"] = list(export_report.get("validation_errors") or [])
        return report

    if not _export_write_ok(export_report):
        report["pilot_bundle_status"] = "blocked_by_export_file_writer"
        report["validation_errors"] = [
            f"unexpected export_file_write_status: {export_report.get('export_file_write_status')}"
        ]
        return report

    if report["go_evidence_status"] == "go_evidence_supported_by_real_feedback_report":
        report["pilot_bundle_status"] = "pilot_bundle_written"
    else:
        report["pilot_bundle_status"] = "pilot_bundle_preview_written_not_go_evidence"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report-path", required=True)
    parser.add_argument(
        "--feedback-json",
        default="[]",
        help="JSON array of {candidate_id,label} feedback entries",
    )
    parser.add_argument(
        "--output-root",
        default=shadow_report_export_file_writer.APPROVED_EXPORT_ROOT,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        feedback_entries = json.loads(args.feedback_json)
    except json.JSONDecodeError:
        feedback_entries = []
    report = build_shadow_test_pilot_bundle(
        candidate_report_path=args.candidate_report_path,
        feedback_entries=feedback_entries,
        output_root=args.output_root,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["pilot_bundle_status"] in {
        "pilot_bundle_written",
        "pilot_bundle_preview_written_not_go_evidence",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

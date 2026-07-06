#!/usr/bin/env python3
"""Write DRV-036 shadow report export previews to ignored artifact files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import shadow_report_ingestion_export_feedback  # noqa: E402


DRV_ID = "DRV-037"
REPORT_MODE = "shadow_report_export_file_writer"
REPORT_VERSION = "shadow_report_export_file_writer.v1"
EXECUTION_BOUNDARY = "write_export_preview_files_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

APPROVED_EXPORT_ROOT = "artifacts/tmp/shadow_report_exports"
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
    "safe_to_write_repo_tracked_export_now",
]
SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


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


def _output_root_errors(output_root: str) -> list[str]:
    path = Path(output_root)
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts) or _path_has_suffix_parts(
            resolved,
            suffix_parts,
        ):
            errors.append(f"output_root is blocked: {label}")
    if errors:
        return errors
    if _repo_relative_path(resolved) is None:
        return ["output_root is outside repository"]
    if not _is_under_root(path, APPROVED_EXPORT_ROOT) or not _is_under_root(
        resolved,
        APPROVED_EXPORT_ROOT,
    ):
        errors.append(f"output_root is not under approved root: {APPROVED_EXPORT_ROOT}")
    if resolved.exists() and not resolved.is_dir():
        errors.append("output_root must be a directory")
    return errors


def _display_path(path: Path) -> str:
    relative = _repo_relative_path(path if path.is_absolute() else REPO_ROOT / path)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _base_report(output_root: str) -> dict[str, Any]:
    return {
        "drv_id": DRV_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_export_root": APPROVED_EXPORT_ROOT,
        "output_root": _display_path(Path(output_root)),
        "export_file_write_status": "not_run",
        "source_ingestion_status": None,
        "source_export_readiness_status": None,
        "go_evidence_status": "not_evaluated",
        "written_files": [],
        "written_file_count": 0,
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _is_safe_session_id(session_id: Any) -> bool:
    return isinstance(session_id, str) and SAFE_SESSION_ID_RE.fullmatch(session_id) is not None


def _file_record(kind: str, path: Path, content: str) -> dict[str, Any]:
    encoded = content.encode("utf-8")
    return {
        "kind": kind,
        "path": _display_path(path),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_count": len(encoded),
    }


def _go_evidence_status(ingestion_report: dict[str, Any]) -> str:
    if ingestion_report.get("export_readiness_status") != "ready_for_shadow_test_export":
        return "not_go_evidence_replay_or_feedback_missing"
    if ingestion_report.get("final_decision_readiness_status") == "go_supported_by_feedback":
        return "go_evidence_supported_by_real_feedback_report"
    return "not_go_evidence_replay_or_feedback_missing"


def _content_for_exports(ingestion_report: dict[str, Any]) -> tuple[str | None, str | None, list[str]]:
    json_preview = ingestion_report.get("json_export_preview")
    markdown_preview = ingestion_report.get("markdown_export_preview")
    errors: list[str] = []
    if not isinstance(json_preview, dict):
        errors.append("json_export_preview must be present")
    if not isinstance(markdown_preview, str) or not markdown_preview.strip():
        errors.append("markdown_export_preview must be present")
    if errors:
        return None, None, errors
    return (
        json.dumps(json_preview, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        markdown_preview.rstrip() + "\n",
        [],
    )


def _export_paths(output_root_path: Path, session_id: str) -> tuple[Path, Path]:
    return (
        output_root_path / f"{session_id}.shadow-report.json",
        output_root_path / f"{session_id}.shadow-report.md",
    )


def _existing_conflict_errors(export_files: list[tuple[Path, str]]) -> list[str]:
    errors: list[str] = []
    for path, content in export_files:
        if path.exists() and path.read_text(encoding="utf-8") != content:
            errors.append("existing export file differs")
            break
    return errors


def _all_existing_files_match(export_files: list[tuple[Path, str]]) -> bool:
    return all(path.exists() and path.read_text(encoding="utf-8") == content for path, content in export_files)


def build_shadow_report_export_file_write(
    *,
    candidate_report: dict[str, Any] | None = None,
    candidate_report_path: str | None = None,
    output_root: str = APPROVED_EXPORT_ROOT,
) -> dict[str, Any]:
    report = _base_report(output_root)
    output_errors = _output_root_errors(output_root)
    if output_errors:
        report["export_file_write_status"] = "blocked_by_output_root_guard"
        report["validation_errors"] = output_errors
        return report

    if candidate_report is not None and candidate_report_path is not None:
        report["export_file_write_status"] = "blocked_by_validation"
        report["validation_errors"] = [
            "provide either candidate_report or candidate_report_path, not both"
        ]
        return report

    ingestion_report = shadow_report_ingestion_export_feedback.build_shadow_report_ingestion_export_feedback(
        candidate_report=candidate_report,
        candidate_report_path=candidate_report_path,
    )
    report["source_ingestion_status"] = ingestion_report.get("ingestion_status")
    report["source_export_readiness_status"] = ingestion_report.get("export_readiness_status")
    report["go_evidence_status"] = _go_evidence_status(ingestion_report)
    if ingestion_report.get("ingestion_status") != "shadow_report_ingested_for_export_feedback":
        report["export_file_write_status"] = "blocked_by_ingestion_report"
        report["validation_errors"] = list(ingestion_report.get("validation_errors") or [])
        report["validation_errors"].extend(
            ingestion_report.get("candidate_report_validation_errors") or []
        )
        return report

    json_content, markdown_content, content_errors = _content_for_exports(ingestion_report)
    if content_errors or json_content is None or markdown_content is None:
        report["export_file_write_status"] = "blocked_by_missing_export_preview"
        report["validation_errors"] = content_errors
        return report

    session_id = ingestion_report["json_export_preview"].get("session_id")
    if not _is_safe_session_id(session_id):
        report["export_file_write_status"] = "blocked_by_export_filename_guard"
        report["validation_errors"] = ["session_id is not safe for export filename"]
        return report

    output_root_path = Path(output_root)
    resolved_output_root = output_root_path if output_root_path.is_absolute() else REPO_ROOT / output_root_path
    json_path, markdown_path = _export_paths(resolved_output_root, session_id)
    export_files = [(json_path, json_content), (markdown_path, markdown_content)]
    conflict_errors = _existing_conflict_errors(export_files)
    if conflict_errors:
        report["export_file_write_status"] = "blocked_by_existing_export_conflict"
        report["validation_errors"] = conflict_errors
        return report

    records = [
        _file_record("json", json_path, json_content),
        _file_record("markdown", markdown_path, markdown_content),
    ]
    if _all_existing_files_match(export_files):
        report["export_file_write_status"] = "idempotent_existing_files_match"
        report["written_files"] = records
        report["written_file_count"] = len(records)
        return report

    resolved_output_root.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json_content, encoding="utf-8")
    markdown_path.write_text(markdown_content, encoding="utf-8")
    report["export_file_write_status"] = "written_to_ignored_artifact_root"
    report["written_files"] = records
    report["written_file_count"] = len(records)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-report-path")
    parser.add_argument("--output-root", default=APPROVED_EXPORT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_shadow_report_export_file_write(
        candidate_report_path=args.candidate_report_path,
        output_root=args.output_root,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["export_file_write_status"] in {
        "not_run",
        "written_to_ignored_artifact_root",
        "idempotent_existing_files_match",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

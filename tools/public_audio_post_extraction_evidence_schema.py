#!/usr/bin/env python3
"""Validate public-audio post-extraction evidence JSON without touching audio."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

DECISION_ID = "DRV-034"
REPORT_MODE = "public_audio_post_extraction_evidence_schema"
SCHEMA_VERSION = "public_audio_post_extraction_evidence.v1"
SCHEMA_STATUS = "specified_not_executable"
EXECUTION_BOUNDARY = "schema_only_no_audio_read_no_download_no_asr"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

APPROVED_EVIDENCE_REPORT_ROOT = "artifacts/tmp/public_audio"
APPROVED_DERIVED_ARTIFACT_ROOTS = {
    "artifacts/tmp/public_audio",
}
APPROVED_SOURCES = {
    "alimeeting_openslr_slr119": {
        "source_url": "https://www.openslr.org/119/",
        "source_license": "CC BY-SA 4.0",
    },
    "aishell4_openslr_slr111": {
        "source_url": "https://www.openslr.org/111/",
        "source_license": "CC BY-SA 4.0",
    },
    "aishell1_openslr_slr33": {
        "source_url": "https://www.openslr.org/33/",
        "source_license": "Apache License v2.0",
    },
}
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
REQUIRED_FIELDS = [
    "manifest_version",
    "planned_sample_id",
    "source_id",
    "source_url",
    "source_license",
    "source_snapshot_date",
    "archive_name",
    "archive_member_path",
    "clip_start_seconds",
    "clip_end_seconds",
    "expected_duration_seconds",
    "expected_sha256_after_extract",
    "observed_sha256",
    "observed_duration_seconds",
    "sample_rate_hz",
    "channel_count",
    "container_format",
    "codec",
    "license_citation",
    "cleanup_status",
    "derived_artifact_root",
    "safe_to_download_public_audio",
    "safe_to_extract_public_audio",
    "safe_to_transcode_audio",
    "safe_to_read_audio_file",
    "safe_to_call_asr",
    "safe_to_call_remote_asr",
    "safe_to_call_llm",
]
FALSE_EVIDENCE_FLAGS = [
    "safe_to_download_public_audio",
    "safe_to_extract_public_audio",
    "safe_to_transcode_audio",
    "safe_to_read_audio_file",
    "safe_to_call_asr",
    "safe_to_call_remote_asr",
    "safe_to_call_llm",
]
FALSE_SAFETY_FLAGS = [
    "safe_to_download_public_audio_now",
    "safe_to_extract_public_audio_now",
    "safe_to_transcode_audio_now",
    "safe_to_read_audio_file_now",
    "safe_to_call_asr_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_run_external_command_now",
]
ALLOWED_CLEANUP_STATUSES = [
    "deleted_after_evidence_recorded",
    "kept_ignored_until_manual_cleanup",
]
_LOWERCASE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _is_under_approved_report_root(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    path_text = relative.as_posix()
    return path_text == APPROVED_EVIDENCE_REPORT_ROOT or path_text.startswith(
        f"{APPROVED_EVIDENCE_REPORT_ROOT}/"
    )


def _evidence_report_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts) or _path_has_suffix_parts(resolved, suffix_parts):
            errors.append(f"evidence_report_path is blocked: {label}")
    if errors:
        return errors
    if _repo_relative_path(resolved) is None:
        return ["evidence_report_path is outside repository"]
    if not _is_under_approved_report_root(path) or not _is_under_approved_report_root(resolved):
        errors.append(
            "evidence_report_path is not under approved root: "
            + APPROVED_EVIDENCE_REPORT_ROOT
        )
    if path.suffix.lower() != ".json":
        errors.append("evidence_report_path must be a JSON report file")
    return errors


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_negative_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _is_relative_safe_path(path_text: str) -> bool:
    if not path_text or path_text.startswith("/") or "\\" in path_text:
        return False
    parts = PurePosixPath(path_text).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path_text: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path_text):
        return False
    return any(path_text == root or path_text.startswith(f"{root}/") for root in roots)


def _contains_local_path_text(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    if normalized.startswith("/") or "/" in normalized:
        return True
    if "/users/" in lowered or ".m4a" in lowered:
        return True
    return any(label in lowered for label, _suffix_parts in FORBIDDEN_PATH_LABELS)


def _safe_summary(evidence_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "planned_sample_id": evidence_report["planned_sample_id"],
        "source_id": evidence_report["source_id"],
        "observed_duration_seconds": evidence_report["observed_duration_seconds"],
        "sample_rate_hz": evidence_report["sample_rate_hz"],
        "channel_count": evidence_report["channel_count"],
        "observed_sha256": evidence_report["observed_sha256"],
        "cleanup_status": evidence_report["cleanup_status"],
    }


def validate_evidence_report(evidence_report: Any) -> list[str]:
    if not isinstance(evidence_report, dict):
        return ["evidence report must be an object"]

    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in evidence_report:
            errors.append(f"{field} is required")

    if evidence_report.get("manifest_version") != SCHEMA_VERSION:
        errors.append(f"manifest_version must be {SCHEMA_VERSION}")

    for field in [
        "planned_sample_id",
        "source_id",
        "source_url",
        "source_license",
        "source_snapshot_date",
        "archive_name",
        "archive_member_path",
        "container_format",
        "codec",
        "license_citation",
        "cleanup_status",
        "derived_artifact_root",
    ]:
        if field in evidence_report and not _is_non_empty_string(evidence_report.get(field)):
            errors.append(f"{field} must be a non-empty string")

    for field in ["planned_sample_id", "source_id"]:
        value = evidence_report.get(field)
        if isinstance(value, str) and _contains_local_path_text(value):
            errors.append(f"{field} must not contain local path text")

    source_id = evidence_report.get("source_id")
    source = APPROVED_SOURCES.get(source_id) if isinstance(source_id, str) else None
    if source is None:
        errors.append("source_id is not approved")
    else:
        if evidence_report.get("source_url") != source["source_url"]:
            errors.append("source_url must match approved source")
        if evidence_report.get("source_license") != source["source_license"]:
            errors.append("source_license must match approved source")

    archive_name = evidence_report.get("archive_name")
    if isinstance(archive_name, str) and not _is_relative_safe_path(archive_name):
        errors.append("archive_name must be a safe relative archive name")
    archive_member_path = evidence_report.get("archive_member_path")
    if isinstance(archive_member_path, str):
        if not _is_relative_safe_path(archive_member_path):
            errors.append("archive_member_path must be a safe relative archive member path")
        if any(label in archive_member_path.casefold() for label, _ in FORBIDDEN_PATH_LABELS):
            errors.append("archive_member_path must not contain local path text")

    clip_start = evidence_report.get("clip_start_seconds")
    clip_end = evidence_report.get("clip_end_seconds")
    expected_duration = evidence_report.get("expected_duration_seconds")
    observed_duration = evidence_report.get("observed_duration_seconds")
    duration_seconds: float | None = None
    if not _is_non_negative_number(clip_start):
        errors.append("clip_start_seconds must be a non-negative number")
    if not _is_non_negative_number(clip_end):
        errors.append("clip_end_seconds must be a non-negative number")
    if _is_non_negative_number(clip_start) and _is_non_negative_number(clip_end):
        if float(clip_end) <= float(clip_start):
            errors.append("clip_end_seconds must be greater than clip_start_seconds")
        else:
            duration_seconds = float(clip_end) - float(clip_start)
    if not _is_non_negative_number(expected_duration):
        errors.append("expected_duration_seconds must be a non-negative number")
    if not _is_non_negative_number(observed_duration):
        errors.append("observed_duration_seconds must be a non-negative number")
    if (
        duration_seconds is not None
        and _is_non_negative_number(expected_duration)
        and abs(float(expected_duration) - duration_seconds) > 0.001
    ):
        errors.append("expected_duration_seconds must equal clip_end_seconds minus clip_start_seconds")
    if (
        _is_non_negative_number(expected_duration)
        and _is_non_negative_number(observed_duration)
        and abs(float(observed_duration) - float(expected_duration)) > 1.0
    ):
        errors.append("observed_duration_seconds must match expected_duration_seconds within 1 second")

    for field in ["expected_sha256_after_extract", "observed_sha256"]:
        value = evidence_report.get(field)
        if not isinstance(value, str) or _LOWERCASE_SHA256_RE.fullmatch(value) is None:
            errors.append(f"{field} must be 64 lowercase hex characters")
    if (
        isinstance(evidence_report.get("expected_sha256_after_extract"), str)
        and isinstance(evidence_report.get("observed_sha256"), str)
        and evidence_report.get("expected_sha256_after_extract") != evidence_report.get("observed_sha256")
    ):
        errors.append("observed_sha256 must match expected_sha256_after_extract")

    if not _is_positive_int(evidence_report.get("sample_rate_hz")):
        errors.append("sample_rate_hz must be a positive integer")
    if not _is_positive_int(evidence_report.get("channel_count")):
        errors.append("channel_count must be a positive integer")

    cleanup_status = evidence_report.get("cleanup_status")
    if cleanup_status not in ALLOWED_CLEANUP_STATUSES:
        errors.append("cleanup_status must be one of " + ", ".join(ALLOWED_CLEANUP_STATUSES))

    derived_artifact_root = evidence_report.get("derived_artifact_root")
    if isinstance(derived_artifact_root, str) and not _is_under_any_root(
        derived_artifact_root,
        APPROVED_DERIVED_ARTIFACT_ROOTS,
    ):
        errors.append("derived_artifact_root must be under approved public audio artifact root")

    for flag in FALSE_EVIDENCE_FLAGS:
        if evidence_report.get(flag) is not False:
            errors.append(f"{flag} must be false")

    return errors


def _base_report() -> dict[str, Any]:
    return {
        "decision_id": DECISION_ID,
        "report_mode": REPORT_MODE,
        "schema_version": SCHEMA_VERSION,
        "schema_status": SCHEMA_STATUS,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_evidence_report_root": APPROVED_EVIDENCE_REPORT_ROOT,
        "required_fields": REQUIRED_FIELDS,
        "allowed_cleanup_statuses": ALLOWED_CLEANUP_STATUSES,
        "evidence_report_path": None,
        "evidence_report_read_status": "not_requested",
        "evidence_report_status": "not_provided",
        "evidence_report_validation_status": "not_run",
        "evidence_report_validation_errors": [],
        "evidence_summary": None,
        **_false_safety_flags(),
    }


def _path_blocked_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report["evidence_report_read_status"] = "blocked"
    report["evidence_report_status"] = "blocked_by_path_guard"
    report["evidence_report_validation_status"] = "failed"
    report["evidence_report_validation_errors"] = errors
    return report


def _display_report_path(path_text: str) -> str:
    path = Path(path_text)
    resolved = path if path.is_absolute() else REPO_ROOT / path
    relative = _repo_relative_path(resolved)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _load_evidence_report(path_text: str) -> tuple[Any | None, list[str], str, str | None]:
    path = Path(path_text)
    path_errors = _evidence_report_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked", None
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        return json.loads(resolved.read_text(encoding="utf-8")), [], "read", _display_report_path(path_text)
    except FileNotFoundError:
        return None, ["evidence_report_path does not exist"], "failed", _display_report_path(path_text)
    except json.JSONDecodeError:
        return None, ["evidence_report_path must contain valid JSON"], "failed", _display_report_path(path_text)


def build_public_audio_post_extraction_evidence_schema(
    *,
    evidence_report: dict[str, Any] | None = None,
    evidence_report_path: str | None = None,
) -> dict[str, Any]:
    if evidence_report is not None and evidence_report_path is not None:
        report = _base_report()
        report["evidence_report_status"] = "blocked_by_schema_validation"
        report["evidence_report_validation_status"] = "failed"
        report["evidence_report_validation_errors"] = [
            "provide either evidence_report or evidence_report_path, not both"
        ]
        return report

    report = _base_report()
    resolved_evidence = evidence_report

    if evidence_report_path is not None:
        loaded, load_errors, read_status, display_path = _load_evidence_report(evidence_report_path)
        if read_status == "blocked":
            return _path_blocked_report(load_errors)
        report["evidence_report_path"] = display_path
        report["evidence_report_read_status"] = read_status
        if load_errors:
            report["evidence_report_status"] = "blocked_by_schema_validation"
            report["evidence_report_validation_status"] = "failed"
            report["evidence_report_validation_errors"] = load_errors
            return report
        resolved_evidence = loaded

    if resolved_evidence is None:
        return report

    errors = validate_evidence_report(resolved_evidence)
    if errors:
        report["evidence_report_status"] = "blocked_by_schema_validation"
        report["evidence_report_validation_status"] = "failed"
        report["evidence_report_validation_errors"] = errors
        return report

    report["evidence_report_status"] = "schema_validated_no_audio_access"
    report["evidence_report_validation_status"] = "passed"
    report["evidence_summary"] = _safe_summary(resolved_evidence)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-report-path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_public_audio_post_extraction_evidence_schema(
        evidence_report_path=args.evidence_report_path,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["evidence_report_validation_status"] in {"not_run", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

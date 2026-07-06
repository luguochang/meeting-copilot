#!/usr/bin/env python3
"""Build retention/delete reports for approved local Meeting Copilot artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

REPORT_MODE = "local_artifact_retention"
SCHEMA_VERSION = "local_artifact_retention.v1"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
APPROVED_ARTIFACT_ROOTS = (
    Path("artifacts/tmp/audio_health"),
    Path("artifacts/tmp/mainline_selftests"),
    Path("artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"),
    Path("artifacts/tmp/real_mic_shadow_tests"),
    Path("artifacts/tmp/real_mic_shadow_reports"),
    Path("artifacts/tmp/asr_events"),
    Path("artifacts/tmp/asr_reports"),
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)


def build_local_artifact_retention_report(
    *,
    session_id: str,
    artifact_paths: list[Path],
    repo_root: Path = REPO_ROOT,
    delete: bool = False,
) -> dict[str, Any]:
    session_errors = _session_id_errors(session_id)
    items = [
        _artifact_item(path=path, repo_root=repo_root, delete=delete)
        for path in artifact_paths
    ]
    if session_errors:
        status = "blocked_by_session_id_validation"
    elif any(item["action"] == "blocked" for item in items):
        status = "blocked_by_artifact_path_guard"
    elif delete:
        status = "approved_artifacts_deleted"
    else:
        status = "local_artifacts_retained"
    return {
        "report_mode": REPORT_MODE,
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id if not session_errors else "<redacted_invalid_session_id>",
        "retention_status": status,
        "delete_requested": delete,
        "validation_errors": session_errors,
        "retention_policy": "local_artifacts_retained_until_explicit_delete",
        "approved_roots": [root.as_posix() for root in APPROVED_ARTIFACT_ROOTS],
        "artifacts": items,
        "retained_artifact_count": sum(1 for item in items if item["action"] == "retained"),
        "deleted_artifact_count": sum(1 for item in items if item["action"] == "deleted"),
        "blocked_artifact_count": sum(1 for item in items if item["action"] == "blocked"),
        "privacy_cost_flags": _privacy_cost_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--artifact-path", type=Path, action="append", default=[])
    parser.add_argument("--delete", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_local_artifact_retention_report(
        session_id=args.session_id,
        artifact_paths=args.artifact_path,
        repo_root=args.repo_root,
        delete=args.delete,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["retention_status"] != "blocked_by_artifact_path_guard" else 1


def _artifact_item(*, path: Path, repo_root: Path, delete: bool) -> dict[str, Any]:
    display_path, errors = _validate_artifact_path(path, repo_root)
    if errors:
        return {
            "path": display_path,
            "action": "blocked",
            "exists_before": False,
            "exists_after": False,
            "size_bytes": 0,
            "validation_errors": errors,
        }
    resolved = path if path.is_absolute() else repo_root / path
    exists_before = resolved.exists()
    size_bytes = resolved.stat().st_size if exists_before and resolved.is_file() else 0
    action = "retained"
    if delete and exists_before and resolved.is_file():
        resolved.unlink()
        action = "deleted"
    elif delete and not exists_before:
        action = "delete_requested_missing"
    return {
        "path": display_path,
        "action": action,
        "exists_before": exists_before,
        "exists_after": resolved.exists(),
        "size_bytes": size_bytes,
        "validation_errors": [],
    }


def _validate_artifact_path(path: Path, repo_root: Path) -> tuple[str, list[str]]:
    errors: list[str] = []
    forbidden_errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            forbidden_errors.append(f"artifact_path is blocked: {label}")
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved, suffix_parts):
            message = f"artifact_path is blocked: {label}"
            if message not in forbidden_errors:
                forbidden_errors.append(message)
    if forbidden_errors:
        return "<redacted_invalid_path>", forbidden_errors
    relative = _repo_relative_path(resolved, repo_root)
    if relative is None:
        return "<redacted_invalid_path>", ["artifact_path is outside repository"]
    if not _is_under_approved_root(relative):
        errors.append("artifact_path is not under an approved artifact root")
    if errors:
        return "<redacted_invalid_path>", errors
    return relative.as_posix(), []


def _session_id_errors(session_id: str) -> list[str]:
    if SESSION_ID_PATTERN.match(session_id):
        return []
    return ["session_id must contain only letters, numbers, underscore, or hyphen"]


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_root(relative: Path) -> bool:
    return any(relative == root or root in relative.parents for root in APPROVED_ARTIFACT_ROOTS)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _privacy_cost_flags() -> dict[str, bool]:
    return {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())

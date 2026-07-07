#!/usr/bin/env python3
"""Build a no-side-effect desktop ASR worker handoff preflight report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-handoff-preflight.policy.json"
)

PCWEB_ID = "PCWEB-095"
POLICY_NAME = "Desktop ASR Worker Handoff Preflight"
POLICY_STATUS = "asr_worker_handoff_preflight_policy_only"
DESCRIPTOR_VERSION = "desktop_asr_worker_handoff_preflight.v1"
ALLOWED_EVENT_FILE_ROOT = "artifacts/tmp/asr_events"
HANDOFF_API_ENDPOINT = "/live/asr/local-event-files/sessions"
ALLOWED_SOURCE_KINDS_NOW = ("preflight_only", "synthetic")
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file")
REQUIRED_DESCRIPTOR_FIELDS = (
    "descriptor_version",
    "session_id",
    "provider",
    "event_file_path",
    "source_kind",
    "chunk_lifecycle",
)
REQUIRED_CHUNK_LIFECYCLE_FIELDS = (
    "chunk_id",
    "chunk_index",
    "chunk_start_ms",
    "chunk_end_ms",
    "source_kind",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
SAFETY_FLAGS = (
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
)
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True
FILE_WRITE_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _blocked_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"event_file_path is blocked: {label}")
    return errors


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_event_root(path: Path, repo_root: Path) -> bool:
    relative = _repo_relative_path(path, repo_root)
    if relative is None:
        return False
    path_text = relative.as_posix()
    return (
        path_text == ALLOWED_EVENT_FILE_ROOT
        or path_text.startswith(f"{ALLOWED_EVENT_FILE_ROOT}/")
    )


def _event_file_path_errors(path: Path, repo_root: Path) -> list[str]:
    errors = _blocked_path_errors_for(path)
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    for error in _blocked_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if not _is_under_approved_event_root(path, repo_root) or not _is_under_approved_event_root(
        resolved,
        repo_root,
    ):
        errors.append("event_file_path is not under approved ASR events root")
    return errors


def _display_event_file_path(path: Path, repo_root: Path) -> str:
    if _event_file_path_errors(path, repo_root):
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(path, repo_root)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-095")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop ASR Worker Handoff Preflight")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be asr_worker_handoff_preflight_policy_only")
    if policy.get("default_quality_gate_status") != "included_in_root_pytest":
        errors.append("default_quality_gate_status must be included_in_root_pytest")
    if policy.get("preflight_mode") != "descriptor_schema_only":
        errors.append("preflight_mode must be descriptor_schema_only")
    if policy.get("worker_execution_status") != "not_started":
        errors.append("worker_execution_status must be not_started")
    if policy.get("microphone_capture_status") != "not_started":
        errors.append("microphone_capture_status must be not_started")
    if policy.get("event_file_write_status") != "not_written":
        errors.append("event_file_write_status must be not_written")
    if policy.get("handoff_api_endpoint") != HANDOFF_API_ENDPOINT:
        errors.append("handoff_api_endpoint must be /live/asr/local-event-files/sessions")
    if policy.get("allowed_event_file_root") != ALLOWED_EVENT_FILE_ROOT:
        errors.append("allowed_event_file_root must be artifacts/tmp/asr_events")
    if policy.get("allowed_source_kinds_now") != list(ALLOWED_SOURCE_KINDS_NOW):
        errors.append("allowed_source_kinds_now must be ['preflight_only', 'synthetic']")
    if policy.get("future_source_kinds_requiring_approval") != list(
        FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL
    ):
        errors.append("future_source_kinds_requiring_approval must be ['mic', 'file']")
    if policy.get("required_descriptor_fields") != list(REQUIRED_DESCRIPTOR_FIELDS):
        errors.append("required_descriptor_fields must match PCWEB-095 descriptor contract")
    if policy.get("required_chunk_lifecycle_fields") != list(REQUIRED_CHUNK_LIFECYCLE_FIELDS):
        errors.append("required_chunk_lifecycle_fields must match PCWEB-095 chunk contract")
    if policy.get("forbidden_roots") != [label for label, _parts in FORBIDDEN_PATH_LABELS]:
        errors.append("forbidden_roots must match desktop ASR handoff privacy boundary")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_non_negative_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and float(value) >= 0


def _is_non_negative_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _source_kind_errors(source_kind: object, label: str) -> list[str]:
    if not _is_non_empty_string(source_kind):
        return [f"{label} must be a non-empty string"]
    source_kind_text = str(source_kind)
    if source_kind_text in ALLOWED_SOURCE_KINDS_NOW:
        return []
    if source_kind_text in FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL:
        return [f"{label} requires later approval: {source_kind_text}"]
    return [f"{label} is unsupported: {source_kind_text}"]


def _validate_chunk_lifecycle_item(
    item: object,
    index: int,
) -> list[str]:
    if not isinstance(item, dict):
        return [f"chunk_lifecycle[{index}] must be an object"]
    errors: list[str] = []
    for field in REQUIRED_CHUNK_LIFECYCLE_FIELDS:
        if field not in item:
            errors.append(f"chunk_lifecycle[{index}].{field} is required")
    chunk_id = item.get("chunk_id")
    if not _is_non_empty_string(chunk_id):
        errors.append(f"chunk_lifecycle[{index}].chunk_id must be a non-empty string")
    chunk_index = item.get("chunk_index")
    if not _is_non_negative_integer(chunk_index):
        errors.append(f"chunk_lifecycle[{index}].chunk_index must be a non-negative integer")
    start_ms = item.get("chunk_start_ms")
    end_ms = item.get("chunk_end_ms")
    if not _is_non_negative_number(start_ms):
        errors.append(f"chunk_lifecycle[{index}].chunk_start_ms must be a non-negative number")
    if not _is_non_negative_number(end_ms):
        errors.append(f"chunk_lifecycle[{index}].chunk_end_ms must be a non-negative number")
    if _is_non_negative_number(start_ms) and _is_non_negative_number(end_ms):
        if float(end_ms) < float(start_ms):
            errors.append(
                f"chunk_lifecycle[{index}].chunk_end_ms must be greater than or equal to chunk_start_ms"
            )
    errors.extend(_source_kind_errors(item.get("source_kind"), f"chunk_lifecycle[{index}].source_kind"))
    return errors


def validate_descriptor(
    descriptor: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], str, dict[str, str] | None]:
    if descriptor is None:
        return "not_provided", [], "<not_provided>", None
    errors: list[str] = []
    for field in REQUIRED_DESCRIPTOR_FIELDS:
        if field not in descriptor:
            errors.append(f"{field} is required")
    if descriptor.get("descriptor_version") != DESCRIPTOR_VERSION:
        errors.append(f"descriptor_version must be {DESCRIPTOR_VERSION}")
    session_id = descriptor.get("session_id")
    if not _is_non_empty_string(session_id) or SESSION_ID_PATTERN.fullmatch(str(session_id)) is None:
        errors.append("session_id must be a safe session id")
    provider = descriptor.get("provider")
    if not _is_non_empty_string(provider):
        errors.append("provider must be a non-empty string")
    event_file_value = descriptor.get("event_file_path")
    event_file_path = Path(str(event_file_value)) if _is_non_empty_string(event_file_value) else Path("")
    if not _is_non_empty_string(event_file_value):
        errors.append("event_file_path must be a non-empty string")
    else:
        errors.extend(_event_file_path_errors(event_file_path, repo_root))
    errors.extend(_source_kind_errors(descriptor.get("source_kind"), "source_kind"))

    chunk_lifecycle = descriptor.get("chunk_lifecycle")
    if not isinstance(chunk_lifecycle, list) or not chunk_lifecycle:
        errors.append("chunk_lifecycle must be a non-empty list")
    elif len(chunk_lifecycle) > 100:
        errors.append("chunk_lifecycle must contain at most 100 chunks")
    elif isinstance(chunk_lifecycle, list):
        for index, item in enumerate(chunk_lifecycle):
            errors.extend(_validate_chunk_lifecycle_item(item, index))

    displayed_path = (
        _display_event_file_path(event_file_path, repo_root)
        if _is_non_empty_string(event_file_value)
        else "<redacted_invalid_path>"
    )
    preview = None
    if not errors:
        preview = {
            "session_id": str(session_id),
            "provider": str(provider),
            "events_path": displayed_path,
        }
    return ("failed" if errors else "passed"), errors, displayed_path, preview


def _preflight_status(policy_errors: list[str], descriptor_status: str, descriptor_errors: list[str]) -> str:
    if policy_errors:
        return "blocked_by_policy_validation"
    if descriptor_status == "not_provided":
        return "blocked_until_descriptor_provided"
    if descriptor_errors:
        return "blocked_by_descriptor_validation"
    return "ready_for_web_handoff_contract_review"


def build_asr_worker_handoff_preflight_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    descriptor: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    descriptor_status, descriptor_errors, event_file_path, preview = validate_descriptor(
        descriptor,
        repo_root=repo_root,
    )
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": "desktop_asr_worker_handoff_preflight_static_report",
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "descriptor_validation_status": descriptor_status,
        "descriptor_validation_errors": descriptor_errors,
        "preflight_status": _preflight_status(policy_errors, descriptor_status, descriptor_errors),
        "worker_execution_status": "not_started",
        "microphone_capture_status": "not_started",
        "event_file_write_status": "not_written",
        "event_file_read_status": "not_read",
        "web_handoff_mutation_status": "not_mutated",
        "handoff_api_endpoint": HANDOFF_API_ENDPOINT,
        "event_file_path": event_file_path,
        "future_web_handoff_request_preview": preview,
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "next_action": (
            "implement_web_handoff_call_after_worker_output_exists"
            if not policy_errors and descriptor_status == "passed"
            else "fix_policy_or_descriptor_before_worker_handoff"
        ),
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--descriptor-json")
    return parser.parse_args(argv)


def _descriptor_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("descriptor JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    descriptor = _descriptor_from_json(args.descriptor_json)
    report = build_asr_worker_handoff_preflight_report(
        policy_path=args.policy_path,
        descriptor=descriptor,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if str(report["preflight_status"]).startswith("blocked_by_policy") else 0


if __name__ == "__main__":
    raise SystemExit(main())

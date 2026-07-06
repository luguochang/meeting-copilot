#!/usr/bin/env python3
"""Build a no-side-effect desktop ASR worker process contract report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-process-contract.policy.json"
)

PCWEB_ID = "PCWEB-098"
POLICY_NAME = "Desktop ASR Worker Process Contract"
POLICY_STATUS = "desktop_asr_worker_process_contract_policy_only"
REPORT_MODE = "desktop_asr_worker_process_contract_static_report"
CONTRACT_VERSION = "desktop_asr_worker_process_contract.v1"
CONTRACT_MODE = "process_contract_only"
WORKER_PROCESS_STATUS = "not_spawned"
WORKER_LIFECYCLE_STATUS = "specified_not_started"
WORKER_HEALTH_STATUS = "not_checked"
WORKER_COMMAND_TRANSPORT_STATUS = "not_bound"
WORKER_OUTPUT_CONTRACT_STATUS = "event_file_or_stream_specified"
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
HANDOFF_API_ENDPOINT = "/live/asr/local-event-files/sessions"
REQUIRED_PREFLIGHT_SOURCES = ("PCWEB-095", "PCWEB-096", "PCWEB-097")
ALLOWED_SOURCE_KINDS_NOW = ("preflight_only", "synthetic")
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
EVENT_TYPES = ("partial", "final", "revision", "error", "end_of_stream")
COMMAND_IDS = (
    "worker.prepare",
    "worker.start",
    "worker.stop",
    "worker.health",
    "worker.collect_events",
    "worker.cleanup",
)
ALLOWED_EVENT_OUTPUT_MODES = ("event_file", "event_stream")
ALLOWED_COMMAND_TRANSPORTS = ("stdio_jsonl", "local_ipc")
REQUIRED_CONTRACT_FIELDS = (
    "contract_version",
    "worker_id",
    "session_id",
    "provider",
    "source_kind",
    "event_output_mode",
    "event_output_path",
    "runtime_root",
    "handoff_api_endpoint",
    "command_transport",
    "declared_event_types",
    "declared_commands",
)
RESOURCE_LIMITS = {
    "max_chunk_ms": 30000,
    "max_event_file_bytes": 10485760,
    "max_session_duration_minutes": 30,
    "max_worker_memory_mb": 4096,
    "max_worker_cpu_percent": 300,
}
FALSE_SAFETY_FLAGS = (
    "safe_to_run_subprocess_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_check_worker_health_now",
    "safe_to_bind_worker_command_transport_now",
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
    "safe_to_run_tauri_or_cargo_now",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _worker_command_catalog() -> list[dict[str, object]]:
    return [{"command_id": command_id, "safe_to_execute_now": False} for command_id in COMMAND_IDS]


def _event_output_contract() -> dict[str, object]:
    return {
        "event_types": list(EVENT_TYPES),
        "final_or_revision_required_for_evidence": True,
        "partial_creates_formal_evidence": False,
        "worker_direct_llm_status": "forbidden",
        "worker_direct_card_creation_status": "forbidden",
    }


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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


def _path_errors_for(path: Path, *, label: str) -> list[str]:
    errors: list[str] = []
    for path_label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"{label} is blocked: {path_label}")
    return errors


def _is_under_root(path: Path, repo_root: Path, approved_root: str) -> bool:
    relative = _repo_relative_path(path, repo_root)
    if relative is None:
        return False
    relative_text = relative.as_posix()
    return relative_text == approved_root or relative_text.startswith(f"{approved_root}/")


def _bounded_path_errors(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    approved_root: str,
) -> list[str]:
    errors = _path_errors_for(path, label=label)
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    for error in _path_errors_for(resolved, label=label):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if _repo_relative_path(resolved, repo_root) is None:
        return [f"{label} is outside repository"]
    if not _is_under_root(path, repo_root, approved_root) or not _is_under_root(
        resolved,
        repo_root,
        approved_root,
    ):
        errors.append(f"{label} is not under approved root: {approved_root}")
    return errors


def _display_path(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    approved_root: str,
) -> str:
    if _bounded_path_errors(path, repo_root=repo_root, label=label, approved_root=approved_root):
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(path, repo_root)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_id_errors(value: object, label: str) -> list[str]:
    if not _is_non_empty_string(value) or SAFE_ID_PATTERN.fullmatch(str(value)) is None:
        return [f"{label} must be a safe id"]
    return []


def _source_kind_errors(source_kind: object) -> list[str]:
    if not _is_non_empty_string(source_kind):
        return ["source_kind must be a non-empty string"]
    source_kind_text = str(source_kind)
    if source_kind_text in ALLOWED_SOURCE_KINDS_NOW:
        return []
    if source_kind_text in FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL:
        return [f"source_kind requires later approval: {source_kind_text}"]
    return [f"source_kind is unsupported: {source_kind_text}"]


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "contract_mode": CONTRACT_MODE,
        "worker_process_status": WORKER_PROCESS_STATUS,
        "worker_lifecycle_status": WORKER_LIFECYCLE_STATUS,
        "worker_health_status": WORKER_HEALTH_STATUS,
        "worker_command_transport_status": WORKER_COMMAND_TRANSPORT_STATUS,
        "worker_output_contract_status": WORKER_OUTPUT_CONTRACT_STATUS,
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "handoff_api_endpoint": HANDOFF_API_ENDPOINT,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    if policy.get("required_preflight_sources") != list(REQUIRED_PREFLIGHT_SOURCES):
        errors.append("required_preflight_sources must be ['PCWEB-095', 'PCWEB-096', 'PCWEB-097']")
    if policy.get("allowed_source_kinds_now") != list(ALLOWED_SOURCE_KINDS_NOW):
        errors.append("allowed_source_kinds_now must be ['preflight_only', 'synthetic']")
    if policy.get("future_source_kinds_requiring_approval") != list(
        FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL
    ):
        errors.append(
            "future_source_kinds_requiring_approval must be ['mic', 'file', 'system_audio']"
        )
    if policy.get("event_output_contract") != _event_output_contract():
        errors.append("event_output_contract must match PCWEB-098 event contract")
    if policy.get("worker_command_catalog") != _worker_command_catalog():
        errors.append("worker_command_catalog must match PCWEB-098 command catalog")
    if policy.get("resource_limits") != RESOURCE_LIMITS:
        errors.append("resource_limits must match PCWEB-098 resource limits")
    if policy.get("forbidden_roots") != [label for label, _parts in FORBIDDEN_PATH_LABELS]:
        errors.append("forbidden_roots must match desktop privacy boundary")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def validate_worker_contract(
    worker_contract: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], str, str, dict[str, str] | None]:
    if worker_contract is None:
        return "not_provided", [], "<not_provided>", "<not_provided>", None
    errors: list[str] = []
    for field in REQUIRED_CONTRACT_FIELDS:
        if field not in worker_contract:
            errors.append(f"{field} is required")
    if worker_contract.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION}")
    errors.extend(_safe_id_errors(worker_contract.get("worker_id"), "worker_id"))
    errors.extend(_safe_id_errors(worker_contract.get("session_id"), "session_id"))
    if not _is_non_empty_string(worker_contract.get("provider")):
        errors.append("provider must be a non-empty string")
    errors.extend(_source_kind_errors(worker_contract.get("source_kind")))
    event_output_mode = worker_contract.get("event_output_mode")
    if event_output_mode not in ALLOWED_EVENT_OUTPUT_MODES:
        errors.append("event_output_mode must be event_file or event_stream")
    command_transport = worker_contract.get("command_transport")
    if command_transport not in ALLOWED_COMMAND_TRANSPORTS:
        errors.append("command_transport must be stdio_jsonl or local_ipc")
    if worker_contract.get("handoff_api_endpoint") != HANDOFF_API_ENDPOINT:
        errors.append("handoff_api_endpoint must be /live/asr/local-event-files/sessions")
    if worker_contract.get("declared_event_types") != list(EVENT_TYPES):
        errors.append("declared_event_types must match the unified ASR event contract")
    if worker_contract.get("declared_commands") != list(COMMAND_IDS):
        errors.append("declared_commands must match the worker command catalog")

    event_value = worker_contract.get("event_output_path")
    event_path = Path(str(event_value)) if _is_non_empty_string(event_value) else Path("")
    if not _is_non_empty_string(event_value):
        errors.append("event_output_path must be a non-empty string")
    else:
        errors.extend(
            _bounded_path_errors(
                event_path,
                repo_root=repo_root,
                label="event_output_path",
                approved_root=APPROVED_EVENT_OUTPUT_ROOT,
            )
        )

    runtime_value = worker_contract.get("runtime_root")
    runtime_path = Path(str(runtime_value)) if _is_non_empty_string(runtime_value) else Path("")
    if not _is_non_empty_string(runtime_value):
        errors.append("runtime_root must be a non-empty string")
    else:
        errors.extend(
            _bounded_path_errors(
                runtime_path,
                repo_root=repo_root,
                label="runtime_root",
                approved_root=APPROVED_RUNTIME_ROOT,
            )
        )

    event_display = (
        _display_path(
            event_path,
            repo_root=repo_root,
            label="event_output_path",
            approved_root=APPROVED_EVENT_OUTPUT_ROOT,
        )
        if _is_non_empty_string(event_value)
        else "<redacted_invalid_path>"
    )
    runtime_display = (
        _display_path(
            runtime_path,
            repo_root=repo_root,
            label="runtime_root",
            approved_root=APPROVED_RUNTIME_ROOT,
        )
        if _is_non_empty_string(runtime_value)
        else "<redacted_invalid_path>"
    )
    preview = None
    if not errors:
        preview = {
            "session_id": str(worker_contract["session_id"]),
            "provider": str(worker_contract["provider"]),
            "events_path": event_display,
        }
    return ("failed" if errors else "passed"), errors, event_display, runtime_display, preview


def _base_report(
    *,
    policy_errors: list[str],
    worker_contract_status: str,
    worker_contract_errors: list[str],
    event_output_path: str,
    runtime_root: str,
    preview: dict[str, str] | None,
) -> dict[str, object]:
    if policy_errors:
        process_status = "blocked_by_policy_validation"
    elif worker_contract_status == "not_provided":
        process_status = "specified_not_executable"
    elif worker_contract_errors:
        process_status = "blocked_by_worker_contract_validation"
    else:
        process_status = "ready_for_no_spawn_contract_review"
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "worker_contract_validation_status": worker_contract_status,
        "worker_contract_validation_errors": worker_contract_errors,
        "process_contract_status": process_status,
        "contract_mode": CONTRACT_MODE,
        "worker_process_status": WORKER_PROCESS_STATUS,
        "worker_lifecycle_status": WORKER_LIFECYCLE_STATUS,
        "worker_health_status": WORKER_HEALTH_STATUS,
        "worker_command_transport_status": WORKER_COMMAND_TRANSPORT_STATUS,
        "worker_output_contract_status": WORKER_OUTPUT_CONTRACT_STATUS,
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "handoff_api_endpoint": HANDOFF_API_ENDPOINT,
        "required_preflight_sources": list(REQUIRED_PREFLIGHT_SOURCES),
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "event_output_contract": _event_output_contract(),
        "worker_command_catalog": _worker_command_catalog(),
        "resource_limits": RESOURCE_LIMITS,
        "event_output_path": event_output_path,
        "runtime_root": runtime_root,
        "future_web_handoff_request_preview": preview,
        **_false_safety_flags(),
    }


def build_desktop_asr_worker_process_contract_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    worker_contract: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    contract_status, contract_errors, event_path, runtime_root, preview = validate_worker_contract(
        worker_contract,
        repo_root=repo_root,
    )
    return _base_report(
        policy_errors=policy_errors,
        worker_contract_status=contract_status,
        worker_contract_errors=contract_errors,
        event_output_path=event_path,
        runtime_root=runtime_root,
        preview=preview,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--worker-contract-json")
    return parser.parse_args(argv)


def _worker_contract_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("worker contract JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_desktop_asr_worker_process_contract_report(
        policy_path=args.policy_path,
        worker_contract=_worker_contract_from_json(args.worker_contract_json),
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if str(report.get("process_contract_status", "")).startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

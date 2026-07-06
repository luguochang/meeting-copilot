#!/usr/bin/env python3
"""Build a no-side-effect desktop ASR worker command protocol report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-command-protocol.policy.json"
)

PCWEB_ID = "PCWEB-099"
POLICY_NAME = "Desktop ASR Worker Command Protocol"
POLICY_STATUS = "desktop_asr_worker_command_protocol_policy_only"
REPORT_MODE = "desktop_asr_worker_command_protocol_static_report"
REQUIRED_PREVIOUS_CONTRACT = "PCWEB-098"
PROTOCOL_MODE = "command_envelope_contract_only"
PROTOCOL_VERSION = "desktop_asr_worker_command_protocol.v1"
WORKER_COMMAND_EXECUTION_STATUS = "not_executed"
TRANSITION_PREVIEW_STATUS = "specified_not_executed"
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
ALLOWED_SOURCE_KINDS_NOW = ("preflight_only", "synthetic")
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
COMMAND_TRANSITIONS = {
    "worker.prepare": (("not_prepared",), "prepared"),
    "worker.start": (("prepared",), "running"),
    "worker.stop": (("running",), "stopped"),
    "worker.health": (("not_prepared", "prepared", "running", "stopped", "cleaned"), "unchanged"),
    "worker.collect_events": (("running", "stopped"), "unchanged"),
    "worker.cleanup": (("stopped",), "cleaned"),
}
REQUIRED_COMMAND_FIELDS = (
    "protocol_version",
    "command_id",
    "request_id",
    "session_id",
    "worker_id",
    "source_kind",
    "current_state",
    "requested_state_after",
    "event_output_path",
    "runtime_root",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_execute_worker_command_now",
    "safe_to_accept_command_now",
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


def _command_transition_catalog() -> list[dict[str, object]]:
    return [
        {
            "command_id": command_id,
            "allowed_current_states": list(allowed_states),
            "requested_state_after": requested_state,
            "safe_to_execute_now": False,
        }
        for command_id, (allowed_states, requested_state) in COMMAND_TRANSITIONS.items()
    ]


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
        "required_previous_contract": REQUIRED_PREVIOUS_CONTRACT,
        "protocol_mode": PROTOCOL_MODE,
        "protocol_version": PROTOCOL_VERSION,
        "worker_command_execution_status": WORKER_COMMAND_EXECUTION_STATUS,
        "transition_preview_status": TRANSITION_PREVIEW_STATUS,
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    if policy.get("allowed_source_kinds_now") != list(ALLOWED_SOURCE_KINDS_NOW):
        errors.append("allowed_source_kinds_now must be ['preflight_only', 'synthetic']")
    if policy.get("future_source_kinds_requiring_approval") != list(
        FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL
    ):
        errors.append(
            "future_source_kinds_requiring_approval must be ['mic', 'file', 'system_audio']"
        )
    if policy.get("command_transition_catalog") != _command_transition_catalog():
        errors.append("command_transition_catalog must match PCWEB-099 command catalog")
    if policy.get("forbidden_roots") != [label for label, _parts in FORBIDDEN_PATH_LABELS]:
        errors.append("forbidden_roots must match desktop privacy boundary")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _validate_lifecycle_transition(command_request: dict[str, object]) -> list[str]:
    command_id = command_request.get("command_id")
    if command_id not in COMMAND_TRANSITIONS:
        return [f"command_id is unsupported: {command_id}"]
    allowed_states, requested_state = COMMAND_TRANSITIONS[str(command_id)]
    errors: list[str] = []
    current_state = command_request.get("current_state")
    if current_state not in allowed_states:
        errors.append(f"{command_id} requires current_state in {list(allowed_states)}")
    requested_state_after = command_request.get("requested_state_after")
    if requested_state_after != requested_state:
        errors.append(f"{command_id} requires requested_state_after={requested_state}")
    return errors


def validate_command_request(
    command_request: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], str, str, dict[str, object] | None]:
    if command_request is None:
        return "not_provided", [], "<not_provided>", "<not_provided>", None
    errors: list[str] = []
    for field in REQUIRED_COMMAND_FIELDS:
        if field not in command_request:
            errors.append(f"{field} is required")
    if command_request.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"protocol_version must be {PROTOCOL_VERSION}")
    errors.extend(_safe_id_errors(command_request.get("request_id"), "request_id"))
    errors.extend(_safe_id_errors(command_request.get("session_id"), "session_id"))
    errors.extend(_safe_id_errors(command_request.get("worker_id"), "worker_id"))
    errors.extend(_source_kind_errors(command_request.get("source_kind")))
    errors.extend(_validate_lifecycle_transition(command_request))

    event_value = command_request.get("event_output_path")
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

    runtime_value = command_request.get("runtime_root")
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
    response_preview = None
    if not errors:
        response_preview = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": str(command_request["request_id"]),
            "command_id": str(command_request["command_id"]),
            "accepted": False,
            "status": "validated_not_executed",
            "worker_lifecycle_status": "unchanged_not_executed",
            "current_state": str(command_request["current_state"]),
            "requested_state_after": str(command_request["requested_state_after"]),
            "event_output_path": event_display,
            "runtime_root": runtime_display,
            "errors": [],
        }
    return ("failed" if errors else "passed"), errors, event_display, runtime_display, response_preview


def _base_report(
    *,
    policy_errors: list[str],
    command_request_status: str,
    command_request_errors: list[str],
    event_output_path: str,
    runtime_root: str,
    response_preview: dict[str, object] | None,
) -> dict[str, object]:
    if policy_errors:
        protocol_status = "blocked_by_policy_validation"
    elif command_request_status == "not_provided":
        protocol_status = "specified_not_executable"
    elif command_request_errors:
        protocol_status = "blocked_by_command_request_validation"
    else:
        protocol_status = "ready_for_command_protocol_review"
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "command_request_validation_status": command_request_status,
        "command_request_validation_errors": command_request_errors,
        "command_protocol_status": protocol_status,
        "required_previous_contract": REQUIRED_PREVIOUS_CONTRACT,
        "protocol_mode": PROTOCOL_MODE,
        "protocol_version": PROTOCOL_VERSION,
        "worker_command_execution_status": WORKER_COMMAND_EXECUTION_STATUS,
        "transition_preview_status": TRANSITION_PREVIEW_STATUS,
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "command_transition_catalog": _command_transition_catalog(),
        "event_output_path": event_output_path,
        "runtime_root": runtime_root,
        "command_response_preview": response_preview,
        **_false_safety_flags(),
    }


def build_desktop_asr_worker_command_protocol_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    command_request: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    request_status, request_errors, event_path, runtime_root, response_preview = (
        validate_command_request(command_request, repo_root=repo_root)
    )
    return _base_report(
        policy_errors=policy_errors,
        command_request_status=request_status,
        command_request_errors=request_errors,
        event_output_path=event_path,
        runtime_root=runtime_root,
        response_preview=response_preview,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--command-request-json")
    return parser.parse_args(argv)


def _command_request_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("command request JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_desktop_asr_worker_command_protocol_report(
        policy_path=args.policy_path,
        command_request=_command_request_from_json(args.command_request_json),
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if str(report.get("command_protocol_status", "")).startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

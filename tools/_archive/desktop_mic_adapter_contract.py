#!/usr/bin/env python3
"""Build a no-side-effect desktop microphone adapter contract report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "mic-adapter-contract.policy.json"

PCWEB_ID = "PCWEB-105"
POLICY_NAME = "Desktop Microphone Adapter Contract"
POLICY_STATUS = "desktop_mic_adapter_contract_policy_only"
REPORT_MODE = "desktop_mic_adapter_contract_static_report"
REQUIRED_PREVIOUS_CONTRACTS = ("PCWEB-104", "DRV-032")
CONTRACT_MODE = "mic_adapter_command_contract_only"
CONTRACT_VERSION = "desktop_mic_adapter_contract.v1"
ADAPTER_EXECUTION_STATUS = "not_bound_not_executed"
PERMISSION_REQUEST_STATUS = "not_requested"
USER_START_BOUNDARY = "explicit_user_start_required_before_capture"
APPROVED_RUNTIME_AUDIO_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime"
APPROVED_AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
DELETE_SEMANTICS = "delete_audio_chunks_before_session_discard"
SOURCE_KIND = "mic"
FORBIDDEN_SOURCE_KINDS_BEFORE_SEPARATE_APPROVAL = ("file", "system_audio")
APPROVED_POLICY_INPUT_ROOT = "code/desktop_tauri"
APPROVED_COMMAND_REQUEST_INPUT_ROOT = APPROVED_RUNTIME_AUDIO_ROOT
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

COMMAND_TRANSITIONS = {
    "mic_adapter.prepare": (("not_prepared",), "prepared"),
    "mic_adapter.status": (
        ("not_prepared", "prepared", "recording", "paused", "stopped", "deleted"),
        "unchanged",
    ),
    "mic_adapter.start": (("prepared", "stopped"), "recording"),
    "mic_adapter.pause": (("recording",), "paused"),
    "mic_adapter.resume": (("paused",), "recording"),
    "mic_adapter.stop": (("recording", "paused"), "stopped"),
    "mic_adapter.delete_audio_chunks": (("stopped",), "deleted"),
}
REQUIRED_COMMAND_FIELDS = (
    "contract_version",
    "command_id",
    "request_id",
    "session_id",
    "adapter_id",
    "source_kind",
    "current_state",
    "requested_state_after",
    "runtime_audio_root",
    "audio_chunk_root",
    "user_consent_state",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_bind_mic_adapter_now",
    "safe_to_accept_mic_command_now",
    "safe_to_execute_mic_command_now",
    "safe_to_select_input_device_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_pause_recording_now",
    "safe_to_resume_recording_now",
    "safe_to_stop_recording_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
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
    if source_kind != SOURCE_KIND:
        if source_kind in FORBIDDEN_SOURCE_KINDS_BEFORE_SEPARATE_APPROVAL:
            return [f"source_kind is forbidden before separate approval: {source_kind}"]
        return [f"source_kind must be {SOURCE_KIND}"]
    return []


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "contract_mode": CONTRACT_MODE,
        "contract_version": CONTRACT_VERSION,
        "adapter_execution_status": ADAPTER_EXECUTION_STATUS,
        "permission_request_status": PERMISSION_REQUEST_STATUS,
        "user_start_boundary": USER_START_BOUNDARY,
        "approved_runtime_audio_root": APPROVED_RUNTIME_AUDIO_ROOT,
        "approved_audio_chunk_root": APPROVED_AUDIO_CHUNK_ROOT,
        "delete_semantics": DELETE_SEMANTICS,
        "source_kind": SOURCE_KIND,
        "future_forbidden_source_kinds_before_separate_approval": list(
            FORBIDDEN_SOURCE_KINDS_BEFORE_SEPARATE_APPROVAL
        ),
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    if policy.get("command_transition_catalog") != _command_transition_catalog():
        errors.append("command_transition_catalog must match PCWEB-105 command catalog")
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
    if command_id == "mic_adapter.start" and command_request.get("user_consent_state") != (
        "explicit_user_start_granted"
    ):
        errors.append("mic_adapter.start requires user_consent_state=explicit_user_start_granted")
    return errors


def validate_mic_command_request(
    mic_command_request: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], str, str, dict[str, object] | None]:
    if mic_command_request is None:
        return "not_provided", [], "<not_provided>", "<not_provided>", None

    errors: list[str] = []
    for field in REQUIRED_COMMAND_FIELDS:
        if field not in mic_command_request:
            errors.append(f"{field} is required")
    if mic_command_request.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version must be {CONTRACT_VERSION}")
    for field in ("request_id", "session_id", "adapter_id"):
        errors.extend(_safe_id_errors(mic_command_request.get(field), field))
    errors.extend(_source_kind_errors(mic_command_request.get("source_kind")))
    errors.extend(_validate_lifecycle_transition(mic_command_request))

    runtime_audio_root = str(mic_command_request.get("runtime_audio_root", ""))
    audio_chunk_root = str(mic_command_request.get("audio_chunk_root", ""))
    runtime_audio_root_path = Path(runtime_audio_root)
    audio_chunk_root_path = Path(audio_chunk_root)
    errors.extend(
        _bounded_path_errors(
            runtime_audio_root_path,
            repo_root=repo_root,
            label="runtime_audio_root",
            approved_root=APPROVED_RUNTIME_AUDIO_ROOT,
        )
    )
    errors.extend(
        _bounded_path_errors(
            audio_chunk_root_path,
            repo_root=repo_root,
            label="audio_chunk_root",
            approved_root=APPROVED_AUDIO_CHUNK_ROOT,
        )
    )

    runtime_audio_root_display = _display_path(
        runtime_audio_root_path,
        repo_root=repo_root,
        label="runtime_audio_root",
        approved_root=APPROVED_RUNTIME_AUDIO_ROOT,
    )
    audio_chunk_root_display = _display_path(
        audio_chunk_root_path,
        repo_root=repo_root,
        label="audio_chunk_root",
        approved_root=APPROVED_AUDIO_CHUNK_ROOT,
    )
    sanitized_request = dict(mic_command_request)
    sanitized_request["runtime_audio_root"] = runtime_audio_root_display
    sanitized_request["audio_chunk_root"] = audio_chunk_root_display
    return ("failed" if errors else "passed"), errors, runtime_audio_root_display, audio_chunk_root_display, sanitized_request


def _response_preview(
    mic_command_request: dict[str, object],
    *,
    runtime_audio_root: str,
    audio_chunk_root: str,
    errors: list[str],
) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "request_id": mic_command_request.get("request_id"),
        "command_id": mic_command_request.get("command_id"),
        "accepted": False,
        "status": "blocked_by_validation" if errors else "validated_not_executed",
        "adapter_lifecycle_status": "unchanged_not_executed",
        "current_state": mic_command_request.get("current_state"),
        "requested_state_after": mic_command_request.get("requested_state_after"),
        "runtime_audio_root": runtime_audio_root,
        "audio_chunk_root": audio_chunk_root,
        "errors": errors,
    }


def _blocked_policy_report(policy_validation_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_validation_errors,
        "mic_command_request_validation_status": "not_evaluated",
        "mic_command_request_validation_errors": [],
        "mic_adapter_contract_status": "blocked_by_policy_validation",
        "adapter_execution_status": ADAPTER_EXECUTION_STATUS,
        "permission_request_status": PERMISSION_REQUEST_STATUS,
        "audio_capture_status": "not_started",
        "audio_chunk_write_status": "not_written",
        "audio_chunk_delete_status": "not_executed",
        "runtime_audio_root": "<not_evaluated>",
        "audio_chunk_root": "<not_evaluated>",
        "command_transition_catalog": _command_transition_catalog(),
        "mic_command_response_preview": None,
        **_false_safety_flags(),
    }


def build_desktop_mic_adapter_contract_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    mic_command_request: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy_path_errors = _bounded_path_errors(
        policy_path,
        repo_root=repo_root,
        label="policy_path",
        approved_root=APPROVED_POLICY_INPUT_ROOT,
    )
    if policy_path_errors:
        return _blocked_policy_report(policy_path_errors)

    policy = _load_json(policy_path)
    policy_validation_errors = validate_policy(policy)
    if policy_validation_errors:
        return _blocked_policy_report(policy_validation_errors)

    (
        request_status,
        request_errors,
        runtime_audio_root,
        audio_chunk_root,
        sanitized_request,
    ) = validate_mic_command_request(mic_command_request, repo_root=repo_root)
    command_response_preview = (
        None
        if sanitized_request is None
        else _response_preview(
            sanitized_request,
            runtime_audio_root=runtime_audio_root,
            audio_chunk_root=audio_chunk_root,
            errors=request_errors,
        )
    )
    if request_status == "not_provided":
        contract_status = "specified_not_executable"
    elif request_status == "passed":
        contract_status = "ready_for_mic_adapter_contract_review"
    else:
        contract_status = "blocked_by_mic_command_request_validation"

    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "passed",
        "policy_validation_errors": [],
        "mic_command_request_validation_status": request_status,
        "mic_command_request_validation_errors": request_errors,
        "mic_adapter_contract_status": contract_status,
        "adapter_execution_status": ADAPTER_EXECUTION_STATUS,
        "permission_request_status": PERMISSION_REQUEST_STATUS,
        "user_start_boundary": USER_START_BOUNDARY,
        "audio_capture_status": "not_started",
        "audio_chunk_write_status": "not_written",
        "audio_chunk_delete_status": "not_executed",
        "runtime_audio_root": runtime_audio_root,
        "audio_chunk_root": audio_chunk_root,
        "command_transition_catalog": _command_transition_catalog(),
        "mic_command_response_preview": command_response_preview,
        "delete_semantics": DELETE_SEMANTICS,
        "future_worker_handoff_status": "not_connected",
        **_false_safety_flags(),
    }


def _load_mic_command_request_file(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    path_errors = _bounded_path_errors(
        path,
        repo_root=REPO_ROOT,
        label="mic_command_request_path",
        approved_root=APPROVED_COMMAND_REQUEST_INPUT_ROOT,
    )
    if path_errors:
        return None, path_errors
    payload = _load_json(path)
    return payload, []


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--mic-command-request", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    mic_command_request = None
    mic_command_request_errors: list[str] = []
    if args.mic_command_request is not None:
        mic_command_request, mic_command_request_errors = _load_mic_command_request_file(
            args.mic_command_request
        )
    if mic_command_request_errors:
        report = _blocked_policy_report(mic_command_request_errors)
    else:
        report = build_desktop_mic_adapter_contract_report(
            policy_path=args.policy,
            mic_command_request=mic_command_request,
        )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["policy_validation_status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a no-execution desktop ASR worker command-runner binding report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-command-runner-binding.policy.json"
)

PCWEB_ID = "PCWEB-103"
POLICY_NAME = "Desktop ASR Worker Command Runner Binding"
POLICY_STATUS = "desktop_asr_worker_command_runner_binding_policy_only"
REPORT_MODE = "desktop_asr_worker_command_runner_binding_static_report"
REQUIRED_PREVIOUS_CONTRACTS = ("PCWEB-102",)
BINDING_MODE = "command_runner_binding_preview_only"
EXECUTION_MODE = "no_execution"
BINDING_VERSION = "desktop_asr_worker_command_runner_binding.v1"
COMMAND_RUNNER_BINDING_STATUS = "specified_not_executable"
NATIVE_COMMAND_RUNNER_STATUS = "path_reserved_not_bound"
SIDECAR_MODULE_PATH = "code/asr_runtime/scripts/asr_worker_sidecar.py"
FUTURE_NATIVE_COMMAND_RUNNER_PATH = (
    "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
)
NATIVE_COMMAND_RUNNER_ROOT = "code/desktop_tauri/src-tauri/src"
COMMAND_TRANSPORT_PREVIEW = "stdio_jsonl"
COMMAND_CATALOG = (
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
)
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
APPROVED_POLICY_INPUT_ROOT = "code/desktop_tauri"
APPROVED_BINDING_REQUEST_INPUT_ROOT = APPROVED_RUNTIME_ROOT
ALLOWED_PROVIDER_MODES_NOW = ("mock_streaming", "sherpa_onnx_streaming")
FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL = ("funasr_streaming",)
FORBIDDEN_PROVIDER_MODES = ("remote_asr", "remote_llm_asr")
ALLOWED_SOURCE_KINDS_NOW = ("synthetic",)
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
REQUIRED_BINDING_FIELDS = (
    "binding_version",
    "binding_id",
    "sidecar_module_path",
    "native_command_runner_path",
    "command_transport",
    "command_catalog",
    "provider_mode",
    "source_kind",
    "event_output_root",
    "runtime_root",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_execute_command_runner_now",
    "safe_to_bind_command_runner_now",
    "safe_to_accept_worker_command_now",
    "safe_to_dispatch_worker_command_now",
    "safe_to_execute_worker_command_now",
    "safe_to_spawn_process_now",
    "safe_to_run_subprocess_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_check_worker_health_now",
    "safe_to_collect_worker_events_now",
    "safe_to_bind_worker_command_transport_now",
    "safe_to_bind_tauri_command_now",
    "safe_to_invoke_tauri_ipc_now",
    "safe_to_import_provider_now",
    "safe_to_execute_provider_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_model" + "scope_now",
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


def _input_file_path_errors(
    path: Path,
    *,
    label: str,
    repo_root: Path,
    approved_root: str,
) -> list[str]:
    return _bounded_path_errors(
        path,
        repo_root=repo_root,
        label=label,
        approved_root=approved_root,
    )


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_id_errors(value: object, label: str) -> list[str]:
    if not _is_non_empty_string(value) or SAFE_ID_PATTERN.fullmatch(str(value)) is None:
        return [f"{label} must be a safe id"]
    return []


def _provider_mode_errors(provider_mode: object) -> list[str]:
    if not _is_non_empty_string(provider_mode):
        return ["provider_mode must be a non-empty string"]
    provider_mode_text = str(provider_mode)
    if provider_mode_text in ALLOWED_PROVIDER_MODES_NOW:
        return []
    if provider_mode_text in FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL:
        return [f"provider_mode requires later approval: {provider_mode_text}"]
    if provider_mode_text in FORBIDDEN_PROVIDER_MODES:
        return [f"provider_mode is forbidden: {provider_mode_text}"]
    return [f"provider_mode is unsupported: {provider_mode_text}"]


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
    expected_fields: dict[str, object] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "binding_mode": BINDING_MODE,
        "execution_mode": EXECUTION_MODE,
        "binding_version": BINDING_VERSION,
        "command_runner_binding_status": COMMAND_RUNNER_BINDING_STATUS,
        "native_command_runner_status": NATIVE_COMMAND_RUNNER_STATUS,
        "sidecar_module_path": SIDECAR_MODULE_PATH,
        "future_native_command_runner_path": FUTURE_NATIVE_COMMAND_RUNNER_PATH,
        "command_transport_preview": COMMAND_TRANSPORT_PREVIEW,
        "command_catalog": list(COMMAND_CATALOG),
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "allowed_provider_modes_now": list(ALLOWED_PROVIDER_MODES_NOW),
        "future_provider_modes_requiring_approval": list(
            FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL
        ),
        "forbidden_provider_modes": list(FORBIDDEN_PROVIDER_MODES),
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "forbidden_roots": [label for label, _parts in FORBIDDEN_PATH_LABELS],
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report(*, policy_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "binding_request_validation_status": "not_started" if policy_errors else "not_provided",
        "binding_request_validation_errors": [],
        "validation_status": "failed" if policy_errors else "not_started",
        "validation_errors": list(policy_errors),
        "command_runner_binding_status": (
            "blocked_by_policy_validation"
            if policy_errors
            else COMMAND_RUNNER_BINDING_STATUS
        ),
        "native_command_runner_status": NATIVE_COMMAND_RUNNER_STATUS,
        "sidecar_module_status": (
            "blocked_by_policy_validation"
            if policy_errors
            else "path_validated_not_executed"
        ),
        "worker_execution_status": "not_executed",
        "process_spawn_status": "not_spawned",
        "binding_mode": BINDING_MODE,
        "execution_mode": EXECUTION_MODE,
        "binding_version": BINDING_VERSION,
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "command_transport_preview": COMMAND_TRANSPORT_PREVIEW,
        "command_catalog": list(COMMAND_CATALOG),
        "sidecar_module_path": SIDECAR_MODULE_PATH,
        "native_command_runner_path": FUTURE_NATIVE_COMMAND_RUNNER_PATH,
        "event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "runtime_root": APPROVED_RUNTIME_ROOT,
        "future_native_command_preview": None,
        "binding_request_required_for_review": True,
        "ready_for_no_execution_binding_review": False,
        "next_action": "fix_policy_validation_errors" if policy_errors else "provide_binding_request",
        **_false_safety_flags(),
    }


def _path_value(
    binding_request: dict[str, object],
    field: str,
) -> tuple[Path, list[str]]:
    value = binding_request.get(field)
    if not _is_non_empty_string(value):
        return Path(""), [f"{field} must be a non-empty string"]
    return Path(str(value)), []


def _validate_exact_path(
    binding_request: dict[str, object],
    *,
    field: str,
    expected_path: str,
    approved_root: str,
    repo_root: Path,
) -> tuple[list[str], str]:
    path, errors = _path_value(binding_request, field)
    if errors:
        return errors, "<redacted_invalid_path>"
    errors.extend(
        _bounded_path_errors(
            path,
            repo_root=repo_root,
            label=field,
            approved_root=approved_root,
        )
    )
    if _display_path(path, repo_root=repo_root, label=field, approved_root=approved_root) != (
        expected_path
    ):
        errors.append(f"{field} must be {expected_path}")
    display = _display_path(path, repo_root=repo_root, label=field, approved_root=approved_root)
    return errors, display if not errors else "<redacted_invalid_path>"


def validate_binding_request(
    binding_request: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], dict[str, str], dict[str, object] | None]:
    if binding_request is None:
        return (
            "not_provided",
            [],
            {
                "sidecar_module_path": SIDECAR_MODULE_PATH,
                "native_command_runner_path": FUTURE_NATIVE_COMMAND_RUNNER_PATH,
                "event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
                "runtime_root": APPROVED_RUNTIME_ROOT,
            },
            None,
        )

    errors: list[str] = []
    for field in REQUIRED_BINDING_FIELDS:
        if field not in binding_request:
            errors.append(f"{field} is required")
    if binding_request.get("binding_version") != BINDING_VERSION:
        errors.append(f"binding_version must be {BINDING_VERSION}")
    errors.extend(_safe_id_errors(binding_request.get("binding_id"), "binding_id"))
    if binding_request.get("command_transport") != COMMAND_TRANSPORT_PREVIEW:
        errors.append(f"command_transport must be {COMMAND_TRANSPORT_PREVIEW}")
    if binding_request.get("command_catalog") != list(COMMAND_CATALOG):
        errors.append("command_catalog must match sidecar command catalog")
    errors.extend(_provider_mode_errors(binding_request.get("provider_mode")))
    errors.extend(_source_kind_errors(binding_request.get("source_kind")))

    sidecar_errors, sidecar_display = _validate_exact_path(
        binding_request,
        field="sidecar_module_path",
        expected_path=SIDECAR_MODULE_PATH,
        approved_root="code/asr_runtime/scripts",
        repo_root=repo_root,
    )
    native_errors, native_display = _validate_exact_path(
        binding_request,
        field="native_command_runner_path",
        expected_path=FUTURE_NATIVE_COMMAND_RUNNER_PATH,
        approved_root=NATIVE_COMMAND_RUNNER_ROOT,
        repo_root=repo_root,
    )
    errors.extend(sidecar_errors)
    errors.extend(native_errors)

    event_path, event_errors = _path_value(binding_request, "event_output_root")
    runtime_path, runtime_errors = _path_value(binding_request, "runtime_root")
    errors.extend(event_errors)
    errors.extend(runtime_errors)
    if not event_errors:
        errors.extend(
            _bounded_path_errors(
                event_path,
                repo_root=repo_root,
                label="event_output_root",
                approved_root=APPROVED_EVENT_OUTPUT_ROOT,
            )
        )
    if not runtime_errors:
        errors.extend(
            _bounded_path_errors(
                runtime_path,
                repo_root=repo_root,
                label="runtime_root",
                approved_root=APPROVED_RUNTIME_ROOT,
            )
        )

    displays = {
        "sidecar_module_path": sidecar_display,
        "native_command_runner_path": native_display,
        "event_output_root": (
            _display_path(
                event_path,
                repo_root=repo_root,
                label="event_output_root",
                approved_root=APPROVED_EVENT_OUTPUT_ROOT,
            )
            if not event_errors
            else "<redacted_invalid_path>"
        ),
        "runtime_root": (
            _display_path(
                runtime_path,
                repo_root=repo_root,
                label="runtime_root",
                approved_root=APPROVED_RUNTIME_ROOT,
            )
            if not runtime_errors
            else "<redacted_invalid_path>"
        ),
    }
    if errors:
        for key, value in list(displays.items()):
            if value != {
                "sidecar_module_path": SIDECAR_MODULE_PATH,
                "native_command_runner_path": FUTURE_NATIVE_COMMAND_RUNNER_PATH,
                "event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
                "runtime_root": APPROVED_RUNTIME_ROOT,
            }[key]:
                displays[key] = "<redacted_invalid_path>"
        return "failed", errors, displays, None

    preview = {
        "reserved_tauri_command_name_preview": "asr_worker_command_runner_preview",
        "native_command_runner_path": FUTURE_NATIVE_COMMAND_RUNNER_PATH,
        "sidecar_module_path": SIDECAR_MODULE_PATH,
        "command_transport": COMMAND_TRANSPORT_PREVIEW,
        "command_catalog": list(COMMAND_CATALOG),
        "binding_status": "validated_not_bound",
        "command_dispatch_status": "not_dispatched",
        "tauri_ipc_status": "not_invoked",
        "process_spawn_status": "not_spawned",
        "health_probe_status": "not_executed",
        "event_collection_status": "not_executed",
        "worker_execution_status": "not_executed",
    }
    return "passed", [], displays, preview


def build_desktop_asr_worker_command_runner_binding_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    binding_request: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy_path_errors = _input_file_path_errors(
        policy_path,
        repo_root=repo_root,
        label="policy_path",
        approved_root=APPROVED_POLICY_INPUT_ROOT,
    )
    if policy_path_errors:
        return _base_report(policy_errors=policy_path_errors)

    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    report = _base_report(policy_errors=policy_errors)
    if policy_errors:
        return report

    request_status, request_errors, displays, preview = validate_binding_request(
        binding_request,
        repo_root=repo_root,
    )
    report["binding_request_validation_status"] = request_status
    report["binding_request_validation_errors"] = request_errors
    if request_status == "not_provided":
        report["validation_status"] = "policy_validation_passed_request_not_provided"
    else:
        report["validation_status"] = "failed" if request_errors else "passed"
    report["validation_errors"] = request_errors
    report["sidecar_module_path"] = displays["sidecar_module_path"]
    report["native_command_runner_path"] = displays["native_command_runner_path"]
    report["event_output_root"] = displays["event_output_root"]
    report["runtime_root"] = displays["runtime_root"]
    report["future_native_command_preview"] = preview
    if request_status == "passed":
        report["command_runner_binding_status"] = "ready_for_no_execution_binding_review"
        report["ready_for_no_execution_binding_review"] = True
        report["next_action"] = "review_before_any_real_command_runner_binding_or_spawn"
    elif request_status == "failed":
        report["command_runner_binding_status"] = "blocked_by_binding_request_validation"
        report["next_action"] = "fix_binding_request_validation_errors"
    else:
        report["next_action"] = "provide_binding_request"
    return report


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a no-execution desktop ASR worker command-runner binding report."
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--binding-request", type=Path)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    binding_request = None
    binding_request_path_errors: list[str] = []
    if args.binding_request:
        binding_request_path_errors = _input_file_path_errors(
            args.binding_request,
            repo_root=args.repo_root,
            label="binding_request_path",
            approved_root=APPROVED_BINDING_REQUEST_INPUT_ROOT,
        )
        if not binding_request_path_errors:
            binding_request = _load_json(args.binding_request)
    report = build_desktop_asr_worker_command_runner_binding_report(
        policy_path=args.policy,
        binding_request=binding_request,
        repo_root=args.repo_root,
    )
    if binding_request_path_errors and report["policy_validation_status"] == "passed":
        report["binding_request_validation_status"] = "failed"
        report["binding_request_validation_errors"] = binding_request_path_errors
        report["validation_status"] = "failed"
        report["validation_errors"] = binding_request_path_errors
        report["command_runner_binding_status"] = "blocked_by_binding_request_validation"
        report["next_action"] = "fix_binding_request_path"
    stdout.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    stdout.write("\n")
    return 1 if report["validation_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

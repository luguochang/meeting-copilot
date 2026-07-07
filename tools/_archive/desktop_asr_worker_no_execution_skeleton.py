#!/usr/bin/env python3
"""Build a no-execution desktop ASR worker skeleton report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-no-execution-skeleton.policy.json"
)
SIDECAR_MODULE_PATH = REPO_ROOT / "code" / "asr_runtime" / "scripts" / "asr_worker_sidecar.py"

PCWEB_ID = "PCWEB-102"
POLICY_NAME = "Desktop ASR Worker No-Execution Skeleton"
POLICY_STATUS = "desktop_asr_worker_no_execution_skeleton_policy_only"
REPORT_MODE = "desktop_asr_worker_no_execution_skeleton_static_report"
REQUIRED_PREVIOUS_CONTRACTS = ("PCWEB-101",)
SKELETON_MODE = "module_boundary_only"
EXECUTION_MODE = "no_execution"
WORKER_SKELETON_STATUS = "specified_not_executable"
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
ALLOWED_PROVIDER_MODES_NOW = ("mock_streaming", "sherpa_onnx_streaming")
FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL = ("funasr_streaming",)
FORBIDDEN_PROVIDER_MODES = ("remote_asr", "remote_llm_asr")
ALLOWED_SOURCE_KINDS_NOW = ("synthetic",)
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
MODULE_BOUNDARIES = (
    "worker_identity_preview",
    "command_envelope_intake_preview",
    "lifecycle_state_preview",
    "event_writer_preview_contract",
    "provider_adapter_preview_contract",
    "health_status_preview",
    "cleanup_plan_preview",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_execute_worker_now",
    "safe_to_spawn_process_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_model" + "scope_now",
    "safe_to_import_model_now",
    "safe_to_execute_provider_now",
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
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_sidecar_module():
    spec = importlib.util.spec_from_file_location("asr_worker_sidecar", SIDECAR_MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    absolute_path = path if path.is_absolute() else repo_root / path
    resolved = absolute_path.resolve(strict=False)
    for error in _path_errors_for(resolved, label=label):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if path.is_absolute() and _repo_relative_path(path, repo_root) is None:
        return [f"{label} is outside repository"]
    if _repo_relative_path(resolved, repo_root) is None:
        return [f"{label} is outside repository"]
    if not _is_under_root(path, repo_root, approved_root) or not _is_under_root(
        resolved,
        repo_root,
        approved_root,
    ):
        errors.append(f"{label} is not under approved root: {approved_root}")
    return errors


def _display_path(path: Path, *, repo_root: Path, label: str, approved_root: str) -> str:
    errors = _bounded_path_errors(
        path,
        repo_root=repo_root,
        label=label,
        approved_root=approved_root,
    )
    if errors:
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(path, repo_root)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, object] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "skeleton_mode": SKELETON_MODE,
        "execution_mode": EXECUTION_MODE,
        "worker_skeleton_status": WORKER_SKELETON_STATUS,
        "sidecar_module_path": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "allowed_provider_modes_now": list(ALLOWED_PROVIDER_MODES_NOW),
        "future_provider_modes_requiring_approval": list(FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL),
        "forbidden_provider_modes": list(FORBIDDEN_PROVIDER_MODES),
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "module_boundaries": list(MODULE_BOUNDARIES),
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
        "sidecar_validation_status": "not_started",
        "validation_status": "failed" if policy_errors else "not_started",
        "validation_errors": list(policy_errors),
        "worker_skeleton_status": (
            "blocked_by_policy_validation" if policy_errors else WORKER_SKELETON_STATUS
        ),
        "worker_execution_status": "not_executed",
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "skeleton_mode": SKELETON_MODE,
        "execution_mode": EXECUTION_MODE,
        "module_boundaries": list(MODULE_BOUNDARIES),
        "provider_mode": None,
        "source_kind": None,
        "event_output_root": None,
        "runtime_root": None,
        "sidecar_report": None,
        "next_action": "fix_policy_validation_errors" if policy_errors else "validate_sidecar_preview",
        **_false_safety_flags(),
    }


def _root_validation_errors(
    *,
    event_output_root: str,
    runtime_root: str,
    repo_root: Path,
) -> tuple[list[str], str, str]:
    event_path = Path(event_output_root)
    runtime_path = Path(runtime_root)
    errors = [
        *_bounded_path_errors(
            event_path,
            repo_root=repo_root,
            label="event_output_root",
            approved_root=APPROVED_EVENT_OUTPUT_ROOT,
        ),
        *_bounded_path_errors(
            runtime_path,
            repo_root=repo_root,
            label="runtime_root",
            approved_root=APPROVED_RUNTIME_ROOT,
        ),
    ]
    return (
        errors,
        _display_path(
            event_path,
            repo_root=repo_root,
            label="event_output_root",
            approved_root=APPROVED_EVENT_OUTPUT_ROOT,
        ),
        _display_path(
            runtime_path,
            repo_root=repo_root,
            label="runtime_root",
            approved_root=APPROVED_RUNTIME_ROOT,
        ),
    )


def build_desktop_asr_worker_no_execution_skeleton_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    provider_mode: str = "mock_streaming",
    source_kind: str = "synthetic",
    event_output_root: str = APPROVED_EVENT_OUTPUT_ROOT,
    runtime_root: str = APPROVED_RUNTIME_ROOT,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    report = _base_report(policy_errors=policy_errors)
    if policy_errors:
        return report

    root_errors, display_event_root, display_runtime_root = _root_validation_errors(
        event_output_root=event_output_root,
        runtime_root=runtime_root,
        repo_root=repo_root,
    )
    sidecar = _load_sidecar_module()
    sidecar_report = sidecar.build_no_execution_worker_skeleton_report(
        provider_mode=provider_mode,
        source_kind=source_kind,
        event_output_root=display_event_root,
        runtime_root=display_runtime_root,
        extra_validation_errors=root_errors,
    )
    report["sidecar_validation_status"] = sidecar_report["validation_status"]
    report["validation_status"] = sidecar_report["validation_status"]
    report["validation_errors"] = sidecar_report["validation_errors"]
    report["provider_mode"] = provider_mode
    report["source_kind"] = source_kind
    report["event_output_root"] = display_event_root
    report["runtime_root"] = display_runtime_root
    report["sidecar_report"] = sidecar_report
    report["next_action"] = sidecar_report["next_action"]
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--provider-mode", default="mock_streaming")
    parser.add_argument("--source-kind", default="synthetic")
    parser.add_argument("--event-output-root", default=APPROVED_EVENT_OUTPUT_ROOT)
    parser.add_argument("--runtime-root", default=APPROVED_RUNTIME_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=args.policy_path,
        provider_mode=args.provider_mode,
        source_kind=args.source_kind,
        event_output_root=args.event_output_root,
        runtime_root=args.runtime_root,
    )
    stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
    stdout.write("\n")
    if report["policy_validation_status"] != "passed":
        return 1
    if report["validation_status"] != "passed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

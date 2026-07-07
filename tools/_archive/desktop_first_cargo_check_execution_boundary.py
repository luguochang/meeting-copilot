#!/usr/bin/env python3
"""Build the PCWEB-090 first cargo-check execution boundary report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "first-cargo-check-execution.policy.json"
DEFAULT_ARTIFACT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "cargo-check.policy.json"
DEFAULT_PROBE_RESULT_INTAKE_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-result-intake.policy.json"
)

PCWEB_ID = "PCWEB-090"
POLICY_NAME = "Desktop First Cargo Check Execution Boundary"
POLICY_STATUS = "first_cargo_check_execution_boundary_policy_only"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

FIRST_CARGO_CHECK_COMMAND = (
    "cargo",
    "check",
    "--manifest-path",
    "code/desktop_tauri/src-tauri/Cargo.toml",
)
REQUIRED_CARGO_ENV = {"CARGO_TARGET_DIR": "artifacts/tmp/desktop_tauri_target"}
ALLOWED_ARTIFACTS = (
    "code/desktop_tauri/src-tauri/Cargo.lock",
    "artifacts/tmp/desktop_tauri_target",
)
REQUIRED_PRECONDITIONS = (
    "pcweb_084_artifact_policy_validation_passed",
    "pcweb_089_toolchain_result_validation_passed",
    "rustc_status_available",
    "cargo_status_available",
    "rustup_status_available",
    "macos_xcode_select_status_available_or_not_applicable",
    "cargo_lock_policy_acknowledged",
    "cargo_target_dir_policy_acknowledged",
    "first_dependency_resolution_network_fetch_approved_or_cache_preseeded",
    "cleanup_policy_acknowledged",
    "explicit_user_approval_for_first_cargo_check",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
SAFETY_FLAGS = (
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_spawn_process_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_raw_probe_output_now",
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_provider_config_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_provider_now",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _canonical_payload() -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "execution_boundary_mode": "explicit_manual_execution_packet_only",
        "accepted_artifact_policy_source": "pcweb_084_cargo_check_policy_only",
        "accepted_toolchain_result_source": "pcweb_089_normalized_result_only",
        "cargo_check_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "approval_status": "explicit_user_approval_not_recorded",
        "first_manual_cargo_check_command": list(FIRST_CARGO_CHECK_COMMAND),
        "first_manual_cargo_check_env": dict(REQUIRED_CARGO_ENV),
        "allowed_artifacts_after_explicit_manual_run": list(ALLOWED_ARTIFACTS),
        "required_preconditions_before_manual_execution": list(REQUIRED_PRECONDITIONS),
        **_false_safety_flags(),
    }


def _not_generated_packet() -> dict[str, object]:
    return {
        "packet_status": "not_generated",
        "command": list(FIRST_CARGO_CHECK_COMMAND),
        "env": dict(REQUIRED_CARGO_ENV),
        "allowed_artifacts_after_explicit_manual_run": list(ALLOWED_ARTIFACTS),
        "approval_required": True,
        "command_must_be_run_by": "user_or_separately_approved_runner",
        "post_run_required_action": "record_result_without_raw_output_or_paths",
    }


def _ready_packet() -> dict[str, object]:
    packet = _not_generated_packet()
    packet["packet_status"] = "ready_for_manual_review"
    return packet


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _blocked_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"path is blocked: {label}")
    return errors


def validate_path(path: Path) -> list[str]:
    errors = _blocked_path_errors_for(path)
    resolved = path.resolve(strict=False)
    for error in _blocked_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    return errors


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-090")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop First Cargo Check Execution Boundary")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be first_cargo_check_execution_boundary_policy_only")
    if policy.get("execution_boundary_mode") != "explicit_manual_execution_packet_only":
        errors.append("execution_boundary_mode must be explicit_manual_execution_packet_only")
    if policy.get("accepted_artifact_policy_source") != "pcweb_084_cargo_check_policy_only":
        errors.append("accepted_artifact_policy_source must be pcweb_084_cargo_check_policy_only")
    if policy.get("accepted_toolchain_result_source") != "pcweb_089_normalized_result_only":
        errors.append("accepted_toolchain_result_source must be pcweb_089_normalized_result_only")
    if policy.get("cargo_check_execution_status") != "not_run":
        errors.append("cargo_check_execution_status must be not_run")
    if policy.get("external_command_execution_status") != "not_run":
        errors.append("external_command_execution_status must be not_run")
    if policy.get("approval_status") != "explicit_user_approval_not_recorded":
        errors.append("approval_status must be explicit_user_approval_not_recorded")
    if policy.get("first_manual_cargo_check_command") != list(FIRST_CARGO_CHECK_COMMAND):
        errors.append("first_manual_cargo_check_command must match PCWEB-084 cargo check command")
    if policy.get("first_manual_cargo_check_env") != REQUIRED_CARGO_ENV:
        errors.append("first_manual_cargo_check_env must set CARGO_TARGET_DIR to artifacts/tmp/desktop_tauri_target")
    if policy.get("allowed_artifacts_after_explicit_manual_run") != list(ALLOWED_ARTIFACTS):
        errors.append("allowed_artifacts_after_explicit_manual_run must match PCWEB-084 artifact policy")
    if policy.get("required_preconditions_before_manual_execution") != list(REQUIRED_PRECONDITIONS):
        errors.append("required_preconditions_before_manual_execution must match PCWEB-090 preconditions")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _nested_dict(policy: dict[str, object], key: str) -> dict[str, object]:
    value = policy.get(key)
    if isinstance(value, dict):
        return value
    return {}


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_artifact_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != "PCWEB-084":
        errors.append("pcweb_id must be PCWEB-084")
    if policy.get("policy_status") != "cargo_check_artifact_policy_only":
        errors.append("policy_status must be cargo_check_artifact_policy_only")
    if policy.get("safe_to_run_cargo_check_now") is not False:
        errors.append("safe_to_run_cargo_check_now must be false")

    first_check = _nested_dict(policy, "future_first_approved_cargo_check")
    first_command = first_check.get("command")
    if not _is_string_list(first_command):
        errors.append("future_first_approved_cargo_check.command must be a list of strings")
    elif first_command != list(FIRST_CARGO_CHECK_COMMAND):
        errors.append("future_first_approved_cargo_check.command must match the approved cargo check command")
    if first_check.get("env") != REQUIRED_CARGO_ENV:
        errors.append("future_first_approved_cargo_check.env must set CARGO_TARGET_DIR to artifacts/tmp/desktop_tauri_target")

    cargo_lock_policy = _nested_dict(policy, "cargo_lock_policy")
    if cargo_lock_policy.get("path") != ALLOWED_ARTIFACTS[0]:
        errors.append("cargo_lock_policy.path must be code/desktop_tauri/src-tauri/Cargo.lock")
    target_dir_policy = _nested_dict(policy, "cargo_target_dir_policy")
    if target_dir_policy.get("path") != ALLOWED_ARTIFACTS[1]:
        errors.append("cargo_target_dir_policy.path must be artifacts/tmp/desktop_tauri_target")

    if policy.get("allowed_future_artifacts_after_explicit_approval") != list(ALLOWED_ARTIFACTS):
        errors.append("allowed_future_artifacts_after_explicit_approval must match PCWEB-090 artifacts")
    return errors


def _load_probe_result_intake_tool():
    module_path = REPO_ROOT / "tools" / "desktop_rust_post_install_probe_result_intake.py"
    spec = importlib.util.spec_from_file_location(
        "desktop_rust_post_install_probe_result_intake",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _safe_delegated_result_errors(errors: object) -> list[str]:
    if not isinstance(errors, list):
        return ["toolchain result validation failed"]

    safe_errors: list[str] = []
    for error in errors:
        if not isinstance(error, str):
            safe_error = "toolchain result validation failed"
        elif error.startswith("unknown result field:"):
            safe_error = "unknown result field present"
        elif error.startswith("forbidden raw result field present:"):
            safe_error = error
        elif error.endswith(" has invalid status"):
            safe_error = error
        elif error.startswith("missing result field:"):
            safe_error = error
        elif error in {
            "probe_result must be an object",
            "macos_xcode_select_path_status must be not_applicable when macos_xcode_select_status is not_applicable",
        }:
            safe_error = error
        else:
            safe_error = "toolchain result validation failed"
        if safe_error not in safe_errors:
            safe_errors.append(safe_error)
    return safe_errors


def _evaluate_toolchain_result(
    *,
    probe_result_intake_policy_path: Path,
    probe_result: object | None,
    probe_result_path: Path | None,
) -> dict[str, object]:
    intake_tool = _load_probe_result_intake_tool()
    intake_report = intake_tool.build_rust_post_install_probe_result_intake_report(
        policy_path=probe_result_intake_policy_path,
        probe_result=probe_result,
        result_path=probe_result_path,
    )
    result_read_status = intake_report.get("result_read_status", "not_requested")
    validation_status = intake_report.get("result_validation_status")
    if validation_status == "not_provided":
        return {
            "toolchain_result_status": "missing",
            "toolchain_result_errors": [],
            "result_read_status": result_read_status,
        }
    if validation_status != "passed":
        errors = _safe_delegated_result_errors(intake_report.get("result_validation_errors", []))
        return {
            "toolchain_result_status": "rejected",
            "toolchain_result_errors": errors,
            "result_read_status": result_read_status,
        }

    normalized = intake_report.get("normalized_probe_result")
    if not isinstance(normalized, dict):
        return {
            "toolchain_result_status": "rejected",
            "toolchain_result_errors": ["normalized_probe_result must be an object"],
            "result_read_status": result_read_status,
        }

    errors: list[str] = []
    for field in ("rustc_status", "cargo_status", "rustup_status"):
        if normalized.get(field) != "available":
            errors.append(f"{field} must be available")
    if normalized.get("macos_xcode_select_status") not in ("available", "not_applicable"):
        errors.append("macos_xcode_select_status must be available or not_applicable")

    if errors:
        return {
            "toolchain_result_status": "blocked_by_toolchain_result",
            "toolchain_result_errors": errors,
            "result_read_status": result_read_status,
        }
    return {
        "toolchain_result_status": "accepted",
        "toolchain_result_errors": [],
        "result_read_status": result_read_status,
    }


def _path_guard_report(
    *,
    policy_errors: list[str] | None = None,
    artifact_errors: list[str] | None = None,
    result_errors: list[str] | None = None,
) -> dict[str, object]:
    policy_errors = policy_errors or []
    artifact_errors = artifact_errors or []
    result_errors = result_errors or []
    return {
        **_canonical_payload(),
        "report_mode": "first_cargo_check_execution_boundary_static_report",
        "policy_read_status": "blocked" if policy_errors else "not_requested",
        "policy_validation_status": "failed" if policy_errors else "not_run",
        "policy_validation_errors": policy_errors,
        "artifact_policy_read_status": "blocked" if artifact_errors else "not_requested",
        "artifact_policy_validation_status": "failed" if artifact_errors else "not_run",
        "artifact_policy_validation_errors": artifact_errors,
        "result_read_status": "blocked" if result_errors else "not_requested",
        "toolchain_result_status": "blocked_by_path_guard",
        "toolchain_result_errors": result_errors,
        "execution_packet_status": "blocked_by_path_guard",
        "manual_execution_packet": _not_generated_packet(),
    }


def build_first_cargo_check_execution_boundary_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    artifact_policy_path: Path = DEFAULT_ARTIFACT_POLICY_PATH,
    probe_result_intake_policy_path: Path = DEFAULT_PROBE_RESULT_INTAKE_POLICY_PATH,
    probe_result: object | None = None,
    probe_result_path: Path | None = None,
) -> dict[str, object]:
    policy_path_errors = validate_path(policy_path)
    if policy_path_errors:
        return _path_guard_report(policy_errors=policy_path_errors[:1])

    artifact_path_errors = validate_path(artifact_policy_path)
    if artifact_path_errors:
        return _path_guard_report(artifact_errors=artifact_path_errors[:1])

    intake_policy_path_errors = validate_path(probe_result_intake_policy_path)
    if intake_policy_path_errors:
        return _path_guard_report(result_errors=intake_policy_path_errors[:1])

    if probe_result_path is not None:
        result_path_errors = validate_path(probe_result_path)
        if result_path_errors:
            return _path_guard_report(result_errors=result_path_errors[:1])

    policy = _load_json(policy_path)
    artifact_policy = _load_json(artifact_policy_path)
    policy_errors = validate_policy(policy)
    artifact_errors = validate_artifact_policy(artifact_policy)
    toolchain = _evaluate_toolchain_result(
        probe_result_intake_policy_path=probe_result_intake_policy_path,
        probe_result=probe_result,
        probe_result_path=probe_result_path,
    )

    policy_status = "passed" if not policy_errors else "failed"
    artifact_status = "passed" if not artifact_errors else "failed"
    toolchain_status = str(toolchain["toolchain_result_status"])

    execution_packet_status = "ready_for_explicit_user_approval"
    manual_packet = _ready_packet()
    if policy_errors:
        execution_packet_status = "blocked_by_policy_validation"
        manual_packet = _not_generated_packet()
    elif artifact_errors:
        execution_packet_status = "blocked_by_artifact_policy_validation"
        manual_packet = _not_generated_packet()
    elif toolchain_status == "missing":
        execution_packet_status = "blocked_by_missing_toolchain_result"
        manual_packet = _not_generated_packet()
    elif toolchain_status != "accepted":
        execution_packet_status = "blocked_by_toolchain_result"
        manual_packet = _not_generated_packet()

    return {
        **_canonical_payload(),
        "report_mode": "first_cargo_check_execution_boundary_static_report",
        "policy_read_status": "read",
        "policy_validation_status": policy_status,
        "policy_validation_errors": policy_errors,
        "artifact_policy_read_status": "read",
        "artifact_policy_validation_status": artifact_status,
        "artifact_policy_validation_errors": artifact_errors,
        "result_read_status": toolchain["result_read_status"],
        "toolchain_result_status": toolchain_status,
        "toolchain_result_errors": toolchain["toolchain_result_errors"],
        "execution_packet_status": execution_packet_status,
        "manual_execution_packet": manual_packet,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--artifact-policy", type=Path, default=DEFAULT_ARTIFACT_POLICY_PATH)
    parser.add_argument("--probe-result-intake-policy", type=Path, default=DEFAULT_PROBE_RESULT_INTAKE_POLICY_PATH)
    parser.add_argument("--probe-result-json", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_first_cargo_check_execution_boundary_report(
        policy_path=args.policy,
        artifact_policy_path=args.artifact_policy,
        probe_result_intake_policy_path=args.probe_result_intake_policy,
        probe_result_path=args.probe_result_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

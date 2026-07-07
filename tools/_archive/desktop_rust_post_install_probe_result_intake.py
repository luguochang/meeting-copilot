#!/usr/bin/env python3
"""Build the PCWEB-089 Rust post-install probe result intake report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-result-intake.policy.json"
)

PCWEB_ID = "PCWEB-089"
POLICY_NAME = "Desktop Rust Post-Install Probe Result Intake Policy"
POLICY_STATUS = "rust_post_install_probe_result_intake_policy_only"
CARGO_CHECK_BLOCKED = "blocked_until_pcweb_084_and_user_approval"

SAFETY_FLAGS = (
    "safe_to_run_post_install_probe_now",
    "safe_to_run_rustc_version_now",
    "safe_to_run_cargo_version_now",
    "safe_to_run_rustup_version_now",
    "safe_to_run_xcode_select_probe_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_raw_probe_output_now",
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
    "safe_to_accept_raw_probe_output_now",
)
ALLOWED_RESULT_FIELDS = (
    "rustc_status",
    "cargo_status",
    "rustup_status",
    "macos_xcode_select_status",
    "macos_xcode_select_path_status",
    "first_cargo_check_readiness",
)
ALLOWED_STATUS_VALUES = {
    "rustc_status": ("available", "missing", "unexpected_error", "not_run"),
    "cargo_status": ("available", "missing", "unexpected_error", "not_run"),
    "rustup_status": ("available", "missing", "unexpected_error", "not_run"),
    "macos_xcode_select_status": (
        "available",
        "missing",
        "unexpected_error",
        "not_run",
        "not_applicable",
    ),
    "macos_xcode_select_path_status": (
        "path_present",
        "path_missing",
        "not_applicable",
        "not_run",
    ),
    "first_cargo_check_readiness": (CARGO_CHECK_BLOCKED,),
}
FORBIDDEN_RAW_RESULT_FIELDS = (
    "stdout",
    "stderr",
    "raw_stdout",
    "raw_stderr",
    "command",
    "command_text",
    "executable_path",
    "xcode_path",
    "developer_tools_path",
    "path",
    "env",
    "cwd",
    "shell_profile",
    "path_environment",
    "cargo_home",
    "rustup_home",
    "dependency_cache_path",
    "target_dir",
    "cargo_lock_path",
    "provider_config",
    "api_key",
    "authorization",
    "bearer_token",
)
NEXT_REQUIRED_DECISIONS = (
    "explicit_first_cargo_check_approval_still_required",
    "future_probe_execution_requires_separate_approval",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
CARGO_CHECK_BLOCKERS = (
    "pcweb_084_artifact_policy_reacknowledged",
    "first_dependency_resolution_network_or_cache_policy_approved",
    "cargo_target_dir_artifact_tmp_approved",
    "cargo_lock_generation_commit_policy_approved",
    "explicit_user_approval_for_first_cargo_check",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
FORBIDDEN_POLICY_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
)
DEFAULT_NORMALIZED_RESULT = {
    "rustc_status": "not_run",
    "cargo_status": "not_run",
    "rustup_status": "not_run",
    "macos_xcode_select_status": "not_run",
    "macos_xcode_select_path_status": "not_run",
    "first_cargo_check_readiness": CARGO_CHECK_BLOCKED,
}


def _as_list_map(values: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    return {key: list(value) for key, value in values.items()}


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _blocked_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_POLICY_PATH_LABELS:
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


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    path_errors = validate_path(policy_path)
    if path_errors:
        raise ValueError(path_errors[0])
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _canonical_payload() -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "result_intake_mode": "manual_result_validation_only",
        "accepted_result_source": "caller_provided_json_only",
        "probe_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "cargo_check_readiness": CARGO_CHECK_BLOCKED,
        "next_required_decision": "explicit_first_cargo_check_approval_still_required",
        "next_required_decisions": list(NEXT_REQUIRED_DECISIONS),
        "cargo_check_blockers": list(CARGO_CHECK_BLOCKERS),
        "allowed_result_fields": list(ALLOWED_RESULT_FIELDS),
        "allowed_status_values": _as_list_map(ALLOWED_STATUS_VALUES),
        "forbidden_raw_result_fields": list(FORBIDDEN_RAW_RESULT_FIELDS),
        **_false_safety_flags(),
    }


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-089")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop Rust Post-Install Probe Result Intake Policy")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be rust_post_install_probe_result_intake_policy_only")
    if policy.get("result_intake_mode") != "manual_result_validation_only":
        errors.append("result_intake_mode must be manual_result_validation_only")
    if policy.get("accepted_result_source") != "caller_provided_json_only":
        errors.append("accepted_result_source must be caller_provided_json_only")
    if policy.get("probe_execution_status") != "not_run":
        errors.append("probe_execution_status must be not_run")
    if policy.get("external_command_execution_status") != "not_run":
        errors.append("external_command_execution_status must be not_run")
    if policy.get("cargo_check_readiness") != CARGO_CHECK_BLOCKED:
        errors.append("cargo_check_readiness must be blocked_until_pcweb_084_and_user_approval")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if policy.get("allowed_result_fields") != list(ALLOWED_RESULT_FIELDS):
        errors.append("allowed_result_fields must match PCWEB-089 result fields")
    if policy.get("allowed_status_values") != _as_list_map(ALLOWED_STATUS_VALUES):
        errors.append("allowed_status_values must match PCWEB-089 status enums")
    if policy.get("forbidden_raw_result_fields") != list(FORBIDDEN_RAW_RESULT_FIELDS):
        errors.append("forbidden_raw_result_fields must match PCWEB-089 forbidden raw fields")
    if policy.get("next_required_decisions") != list(NEXT_REQUIRED_DECISIONS):
        errors.append("next_required_decisions must match PCWEB-089 required decisions")
    if policy.get("cargo_check_blockers") != list(CARGO_CHECK_BLOCKERS):
        errors.append("cargo_check_blockers must match PCWEB-089 cargo check blockers")
    return errors


def _blocked_path_report(path_errors: list[str]) -> dict[str, object]:
    return {
        **_canonical_payload(),
        "report_mode": "rust_post_install_probe_result_intake_static_report",
        "policy_read_status": "blocked",
        "policy_validation_status": "failed",
        "policy_validation_errors": path_errors,
        "result_read_status": "blocked",
        "result_validation_status": "blocked_by_policy_validation",
        "result_validation_errors": [],
        "normalized_probe_result": dict(DEFAULT_NORMALIZED_RESULT),
        "toolchain_presence_summary_status": "no_result_provided",
    }


def _normalize_valid_result(probe_result: dict[str, object]) -> dict[str, str]:
    normalized = dict(DEFAULT_NORMALIZED_RESULT)
    for field in ALLOWED_RESULT_FIELDS:
        value = probe_result.get(field)
        if isinstance(value, str) and value in ALLOWED_STATUS_VALUES[field]:
            normalized[field] = value
    normalized["first_cargo_check_readiness"] = CARGO_CHECK_BLOCKED
    return normalized


def _validate_probe_result(probe_result: object | None) -> tuple[str, list[str], dict[str, str]]:
    if probe_result is None:
        return "not_provided", [], dict(DEFAULT_NORMALIZED_RESULT)
    if not isinstance(probe_result, dict):
        return "failed", ["probe_result must be an object"], dict(DEFAULT_NORMALIZED_RESULT)

    errors: list[str] = []
    for field in probe_result:
        if field in FORBIDDEN_RAW_RESULT_FIELDS:
            errors.append(f"forbidden raw result field present: {field}")
        elif field not in ALLOWED_RESULT_FIELDS:
            errors.append(f"unknown result field: {field}")

    for field in ALLOWED_RESULT_FIELDS:
        value = probe_result.get(field)
        if value is None:
            errors.append(f"missing result field: {field}")
        elif not isinstance(value, str) or value not in ALLOWED_STATUS_VALUES[field]:
            errors.append(f"{field} has invalid status")

    if (
        probe_result.get("macos_xcode_select_status") == "not_applicable"
        and probe_result.get("macos_xcode_select_path_status") != "not_applicable"
    ):
        errors.append(
            "macos_xcode_select_path_status must be not_applicable when "
            "macos_xcode_select_status is not_applicable"
        )

    if errors:
        return "failed", errors, dict(DEFAULT_NORMALIZED_RESULT)
    return "passed", errors, _normalize_valid_result(probe_result)


def _summary_status(result_status: str, normalized: dict[str, str]) -> str:
    if result_status == "not_provided":
        return "no_result_provided"
    if result_status == "failed":
        return "toolchain_probe_result_rejected"
    if all(normalized[field] == "available" for field in ("rustc_status", "cargo_status", "rustup_status")):
        return "toolchain_probe_result_available"
    return "toolchain_probe_result_incomplete_or_unavailable"


def _read_probe_result(result_path: Path | None) -> tuple[str, object | None, list[str]]:
    if result_path is None:
        return "not_requested", None, []
    path_errors = validate_path(result_path)
    if path_errors:
        return "blocked", None, path_errors
    return "read", json.loads(result_path.read_text(encoding="utf-8")), []


def build_rust_post_install_probe_result_intake_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    probe_result: object | None = None,
    result_path: Path | None = None,
) -> dict[str, object]:
    path_errors = validate_path(policy_path)
    if result_path is not None:
        for error in validate_path(result_path):
            if error not in path_errors:
                path_errors.append(error)
    if path_errors:
        return _blocked_path_report(path_errors[:1])

    policy = load_policy(policy_path)
    policy_errors = validate_policy(policy)
    policy_passed = not policy_errors
    if not policy_passed:
        return {
            **_canonical_payload(),
            "report_mode": "rust_post_install_probe_result_intake_static_report",
            "policy_read_status": "read",
            "policy_validation_status": "failed",
            "policy_validation_errors": policy_errors,
            "result_read_status": "blocked_by_policy_validation",
            "result_validation_status": "blocked_by_policy_validation",
            "result_validation_errors": [],
            "normalized_probe_result": dict(DEFAULT_NORMALIZED_RESULT),
            "toolchain_presence_summary_status": "toolchain_probe_result_rejected",
        }

    result_read_status, result_object, result_read_errors = _read_probe_result(result_path)
    if result_read_errors:
        return _blocked_path_report(result_read_errors[:1])
    if result_path is None and probe_result is not None:
        result_read_status = "provided_inline"
        result_object = probe_result

    result_status, result_errors, normalized = _validate_probe_result(result_object)

    return {
        **_canonical_payload(),
        "report_mode": "rust_post_install_probe_result_intake_static_report",
        "policy_read_status": "read",
        "policy_validation_status": "passed",
        "policy_validation_errors": [],
        "result_read_status": result_read_status,
        "result_validation_status": result_status,
        "result_validation_errors": result_errors,
        "normalized_probe_result": normalized,
        "toolchain_presence_summary_status": _summary_status(result_status, normalized),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-089 result intake policy JSON.",
    )
    parser.add_argument(
        "--result-json",
        type=Path,
        default=None,
        help="Optional path to caller-provided bounded probe result JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_rust_post_install_probe_result_intake_report(
        policy_path=args.policy,
        result_path=args.result_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

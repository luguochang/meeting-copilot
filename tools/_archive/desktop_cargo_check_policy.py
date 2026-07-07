#!/usr/bin/env python3
"""Report PCWEB-084 desktop cargo-check artifact policy without running Cargo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "cargo-check.policy.json"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True
FIRST_APPROVED_COMMAND = [
    "cargo",
    "check",
    "--manifest-path",
    "code/desktop_tauri/src-tauri/Cargo.toml",
]
REPEAT_LOCKED_OFFLINE_COMMAND = [
    "cargo",
    "check",
    "--manifest-path",
    "code/desktop_tauri/src-tauri/Cargo.toml",
    "--locked",
    "--offline",
]
REQUIRED_CARGO_TARGET_ENV = {"CARGO_TARGET_DIR": "artifacts/tmp/desktop_tauri_target"}


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _nested_dict(policy: dict[str, object], key: str) -> dict[str, object]:
    value = policy.get(key)
    if isinstance(value, dict):
        return value
    return {}


def validate_policy(policy: dict[str, object]) -> list[str]:
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
    elif first_command != FIRST_APPROVED_COMMAND:
        errors.append("future_first_approved_cargo_check.command must match the approved cargo check command")
    if first_check.get("env") != REQUIRED_CARGO_TARGET_ENV:
        errors.append("future_first_approved_cargo_check.env must set CARGO_TARGET_DIR to artifacts/tmp/desktop_tauri_target")

    repeat_check = _nested_dict(policy, "future_repeat_locked_offline_cargo_check")
    repeat_command = repeat_check.get("command")
    if not _is_string_list(repeat_command):
        errors.append("future_repeat_locked_offline_cargo_check.command must be a list of strings")
    elif repeat_command != REPEAT_LOCKED_OFFLINE_COMMAND:
        errors.append("future_repeat_locked_offline_cargo_check.command must match the locked offline cargo check command")
    if repeat_check.get("env") != REQUIRED_CARGO_TARGET_ENV:
        errors.append("future_repeat_locked_offline_cargo_check.env must set CARGO_TARGET_DIR to artifacts/tmp/desktop_tauri_target")

    cargo_lock_policy = _nested_dict(policy, "cargo_lock_policy")
    if cargo_lock_policy.get("path") != "code/desktop_tauri/src-tauri/Cargo.lock":
        errors.append("cargo_lock_policy.path must be code/desktop_tauri/src-tauri/Cargo.lock")
    target_dir_policy = _nested_dict(policy, "cargo_target_dir_policy")
    if target_dir_policy.get("path") != "artifacts/tmp/desktop_tauri_target":
        errors.append("cargo_target_dir_policy.path must be artifacts/tmp/desktop_tauri_target")

    return errors


def _relative_path_exists(repo_root: Path, relative_path: object) -> bool:
    if not isinstance(relative_path, str):
        return False
    return (repo_root / relative_path).exists()


def build_cargo_check_policy_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = load_policy(policy_path)
    validation_errors = validate_policy(policy)
    validation_passed = not validation_errors

    cargo_lock_policy = _nested_dict(policy, "cargo_lock_policy")
    target_dir_policy = _nested_dict(policy, "cargo_target_dir_policy")
    first_check = dict(_nested_dict(policy, "future_first_approved_cargo_check"))
    repeat_check = dict(_nested_dict(policy, "future_repeat_locked_offline_cargo_check"))

    if not validation_passed:
        first_check["status"] = "blocked_by_policy_validation"
        repeat_check["status"] = "blocked_by_policy_validation"

    return {
        "pcweb_id": policy.get("pcweb_id"),
        "policy_name": policy.get("policy_name"),
        "report_mode": "cargo_check_policy_static_report",
        "policy_status": policy.get("policy_status"),
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "cargo_check_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "safe_to_run_cargo_check_now": False,
        "safe_to_install_toolchain_now": policy.get("safe_to_install_toolchain_now"),
        "safe_to_fetch_dependencies_now": policy.get("safe_to_fetch_dependencies_now"),
        "safe_to_generate_cargo_lock_now": policy.get("safe_to_generate_cargo_lock_now"),
        "safe_to_generate_target_dir_now": policy.get("safe_to_generate_target_dir_now"),
        "cargo_lock_policy": cargo_lock_policy,
        "cargo_target_dir_policy": target_dir_policy,
        "network_dependency_fetch_policy": policy.get("network_dependency_fetch_policy"),
        "cleanup_policy": policy.get("cleanup_policy"),
        "cargo_lock_exists": _relative_path_exists(repo_root, cargo_lock_policy.get("path")),
        "approved_target_dir_exists": _relative_path_exists(repo_root, target_dir_policy.get("path")),
        "forbidden_in_source_target_dir_exists": _relative_path_exists(
            repo_root,
            "code/desktop_tauri/src-tauri/target",
        ),
        "first_approved_cargo_check_plan": first_check,
        "repeat_locked_offline_check_plan": repeat_check,
        "allowed_future_artifacts_after_explicit_approval": policy.get(
            "allowed_future_artifacts_after_explicit_approval",
            [],
        ),
        "forbidden_source_tree_artifacts": policy.get("forbidden_source_tree_artifacts", []),
        "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
        "remaining_preconditions_before_first_cargo_check": policy.get(
            "remaining_preconditions_before_first_cargo_check",
            [],
        ),
        "official_sources": policy.get("official_sources", []),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-084 desktop cargo-check policy JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_cargo_check_policy_report(policy_path=args.policy)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Report PCWEB-086 Rust toolchain installation decisions without installing."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-installation.policy.json"

SAFETY_FLAGS = (
    "safe_to_install_toolchain_now",
    "safe_to_modify_shell_profile_now",
    "safe_to_run_install_command_now",
    "safe_to_run_rustup_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
)
REQUIRED_APPROVAL_TOKENS = (
    "explicit_user_approval_for_rust_toolchain_install",
    "approved_install_provider_official_rustup",
    "approved_shell_profile_modification_policy",
    "approved_network_download_policy_for_rustup",
    "approved_post_install_probe_policy",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
POST_INSTALL_VERIFICATION_ORDER = (
    "rustc_version",
    "cargo_version",
    "rustup_version",
    "macos_xcode_select_presence_redacted",
    "pcweb_084_cargo_check_preflight",
)
FORBIDDEN_POLICY_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def validate_policy_path(policy_path: Path) -> list[str]:
    resolved = policy_path.resolve(strict=False)
    repo_root = REPO_ROOT.resolve(strict=False)
    try:
        relative = resolved.relative_to(repo_root)
    except ValueError:
        return []

    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_POLICY_PATH_LABELS:
        if _path_has_suffix_parts(relative, suffix_parts):
            errors.append(f"policy path is blocked: {label}")
    return errors


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    path_errors = validate_policy_path(policy_path)
    if path_errors:
        raise ValueError(path_errors[0])
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != "PCWEB-086":
        errors.append("pcweb_id must be PCWEB-086")
    if policy.get("policy_status") != "rust_toolchain_installation_decision_policy_only":
        errors.append("policy_status must be rust_toolchain_installation_decision_policy_only")
    if policy.get("installation_decision_mode") != "no_install_decision_report_only":
        errors.append("installation_decision_mode must be no_install_decision_report_only")
    if policy.get("recommended_install_provider") != "official_rustup":
        errors.append("recommended_install_provider must be official_rustup")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    approval_tokens = policy.get("required_approval_tokens_before_install")
    if list(REQUIRED_APPROVAL_TOKENS) != approval_tokens:
        errors.append("required_approval_tokens_before_install must match PCWEB-086 required tokens")
    verification_order = policy.get("post_install_verification_order")
    if list(POST_INSTALL_VERIFICATION_ORDER) != verification_order:
        errors.append("post_install_verification_order must match PCWEB-086 verification order")
    if not _is_string_list(policy.get("remaining_preconditions_before_first_install")):
        errors.append("remaining_preconditions_before_first_install must be a list of strings")
    platform_notes = policy.get("platform_notes")
    if not isinstance(platform_notes, dict) or set(platform_notes) != {"macos", "windows", "linux"}:
        errors.append("platform_notes must contain macos, windows, and linux")
    official_sources = policy.get("official_sources")
    if not isinstance(official_sources, list) or not official_sources:
        errors.append("official_sources must be a non-empty list")
    return errors


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def build_rust_toolchain_installation_decision_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> dict[str, object]:
    policy_path_errors = validate_policy_path(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = load_policy(policy_path)
    validation_errors = validate_policy(policy)
    validation_passed = not validation_errors
    decision_status = (
        "blocked_requires_explicit_user_approval"
        if validation_passed
        else "blocked_by_policy_validation"
    )

    return {
        "pcweb_id": policy.get("pcweb_id"),
        "policy_name": policy.get("policy_name"),
        "report_mode": "rust_toolchain_installation_decision_static_report",
        "policy_status": policy.get("policy_status"),
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "installation_decision_mode": policy.get("installation_decision_mode"),
        "installation_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "installation_decision_status": decision_status,
        "recommended_install_provider": policy.get("recommended_install_provider"),
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_install_verification_order": list(POST_INSTALL_VERIFICATION_ORDER),
        "platform_notes": policy.get("platform_notes", {}),
        "remaining_preconditions_before_first_install": policy.get(
            "remaining_preconditions_before_first_install",
            [],
        ),
        "remaining_preconditions_before_first_cargo_check_after_install": policy.get(
            "remaining_preconditions_before_first_cargo_check_after_install",
            [],
        ),
        "forbidden_commands": policy.get("forbidden_commands", []),
        "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
        "official_sources": policy.get("official_sources", []),
        **_false_safety_flags(),
    }


def _blocked_policy_path_report(policy_path_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": "PCWEB-086",
        "policy_name": "Desktop Rust Toolchain Installation Decision Policy",
        "report_mode": "rust_toolchain_installation_decision_static_report",
        "policy_status": "blocked_policy_path",
        "policy_read_status": "blocked",
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_path_errors,
        "installation_decision_mode": "no_install_decision_report_only",
        "installation_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "installation_decision_status": "blocked_by_policy_validation",
        "recommended_install_provider": "official_rustup",
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_install_verification_order": list(POST_INSTALL_VERIFICATION_ORDER),
        "platform_notes": {},
        "remaining_preconditions_before_first_install": list(REQUIRED_APPROVAL_TOKENS),
        "remaining_preconditions_before_first_cargo_check_after_install": [],
        "forbidden_commands": [],
        "forbidden_default_side_effects": [],
        "official_sources": [],
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-086 Rust toolchain installation decision policy JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_rust_toolchain_installation_decision_report(policy_path=args.policy)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the PCWEB-088 Rust post-install probe approval packet as static JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-post-install-probe-approval.policy.json"

PCWEB_ID = "PCWEB-088"
POLICY_NAME = "Desktop Rust Post-Install Probe Approval Policy"
POLICY_STATUS = "rust_post_install_probe_approval_policy_only"

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
    "safe_to_read_shell_profiles_now",
    "safe_to_read_cargo_home_now",
    "safe_to_read_rustup_home_now",
)
REQUIRED_APPROVAL_TOKENS = (
    "explicit_user_approval_for_post_install_probe",
    "rust_toolchain_install_completed_by_user",
    "approved_post_install_probe_command_allowlist",
    "approved_probe_output_redaction_policy",
    "approved_no_cargo_check_boundary_reconfirmed",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
FUTURE_PROBE_COMMAND_ALLOWLIST = (
    {
        "probe_id": "rustc_version",
        "command_text": "rustc --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "cargo_version",
        "command_text": "cargo --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "rustup_version",
        "command_text": "rustup --version",
        "platform": "all",
        "output_policy": "version_text_only",
    },
    {
        "probe_id": "macos_xcode_select_presence",
        "command_text": "xcode-select -p",
        "platform": "macos",
        "output_policy": "presence_only_no_path",
    },
)
EXPECTED_PROBE_RESULT_SCHEMA_FIELDS = (
    "rustc_status",
    "cargo_status",
    "rustup_status",
    "macos_xcode_select_status",
    "macos_xcode_select_path_status",
    "first_cargo_check_readiness",
)
REDACTION_REQUIREMENTS = (
    "macos_xcode_select_path_presence_only",
    "do_not_return_shell_profile_paths",
    "do_not_return_path_environment",
    "do_not_return_cargo_home",
    "do_not_return_rustup_home",
    "do_not_return_dependency_cache_paths",
    "do_not_return_credentials_or_provider_config",
)
CARGO_CHECK_BLOCKERS = (
    "pcweb_084_artifact_policy_reacknowledged",
    "first_dependency_resolution_network_or_cache_policy_approved",
    "cargo_target_dir_artifact_tmp_approved",
    "cargo_lock_generation_commit_policy_approved",
    "explicit_user_approval_for_first_cargo_check",
    "no_audio_worker_secret_remote_boundary_reconfirmed",
)
OFFICIAL_SOURCE_URLS = {
    "https://www.rust-lang.org/tools/install",
    "https://rust-lang.github.io/rustup/installation/index.html",
    "https://doc.rust-lang.org/cargo/commands/cargo-check.html",
    "https://doc.rust-lang.org/cargo/reference/build-cache.html",
    "https://v2.tauri.app/start/prerequisites/",
}
FORBIDDEN_DEFAULT_SIDE_EFFECTS = (
    "probe_command_execution",
    "shell_execution",
    "installer_execution",
    "package_manager_execution",
    "cargo_command_execution",
    "tauri_command_execution",
    "shell_profile_read",
    "path_environment_read",
    "cargo_home_read",
    "rustup_home_read",
    "dependency_cache_read",
    "cargo_lock_read",
    "target_dir_read",
    "dependency_download",
    "cargo_artifact_generation",
    "audio_permission_request",
    "audio_device_enumeration",
    "microphone_capture",
    "system_audio_capture",
    "asr_worker_spawn",
    "provider_config_read",
    "secret_read",
    "configs_local_read",
    "remote_asr_call",
    "remote_llm_call",
    "installer_creation",
    "signing",
    "notarization",
)
FORBIDDEN_POLICY_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _forbidden_policy_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_POLICY_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"policy path is blocked: {label}")
    return errors


def validate_policy_path(policy_path: Path) -> list[str]:
    errors = _forbidden_policy_path_errors_for(policy_path)
    resolved = policy_path.resolve(strict=False)
    for error in _forbidden_policy_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    return errors


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    path_errors = validate_policy_path(policy_path)
    if path_errors:
        raise ValueError(path_errors[0])
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _source_urls(policy: dict[str, object]) -> set[str]:
    official_sources = policy.get("official_sources")
    if not isinstance(official_sources, list):
        return set()
    urls = set()
    for source in official_sources:
        if isinstance(source, dict) and isinstance(source.get("url"), str):
            urls.add(source["url"])
    return urls


def _trusted_official_sources() -> list[dict[str, str]]:
    return [
        {
            "label": "Install Rust",
            "url": "https://www.rust-lang.org/tools/install",
        },
        {
            "label": "rustup installation",
            "url": "https://rust-lang.github.io/rustup/installation/index.html",
        },
        {
            "label": "Cargo check command",
            "url": "https://doc.rust-lang.org/cargo/commands/cargo-check.html",
        },
        {
            "label": "Cargo build cache",
            "url": "https://doc.rust-lang.org/cargo/reference/build-cache.html",
        },
        {
            "label": "Tauri v2 prerequisites",
            "url": "https://v2.tauri.app/start/prerequisites/",
        },
    ]


def _trusted_future_probe_command_allowlist() -> list[dict[str, str]]:
    return [dict(item) for item in FUTURE_PROBE_COMMAND_ALLOWLIST]


def _trusted_forbidden_default_side_effects() -> list[str]:
    return list(FORBIDDEN_DEFAULT_SIDE_EFFECTS)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-088")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop Rust Post-Install Probe Approval Policy")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be rust_post_install_probe_approval_policy_only")
    if policy.get("probe_approval_mode") != "no_probe_execution_approval_packet_only":
        errors.append("probe_approval_mode must be no_probe_execution_approval_packet_only")
    if policy.get("probe_execution_status") != "not_run":
        errors.append("probe_execution_status must be not_run")
    if policy.get("external_command_execution_status") != "not_run":
        errors.append("external_command_execution_status must be not_run")
    if policy.get("cargo_check_readiness") != "blocked_until_pcweb_084_and_user_approval":
        errors.append("cargo_check_readiness must be blocked_until_pcweb_084_and_user_approval")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if policy.get("required_approval_tokens_before_probe") != list(REQUIRED_APPROVAL_TOKENS):
        errors.append("required_approval_tokens_before_probe must match PCWEB-088 required tokens")
    if policy.get("future_probe_command_allowlist") != _trusted_future_probe_command_allowlist():
        errors.append("future_probe_command_allowlist must match PCWEB-088 allowlist")
    if policy.get("expected_probe_result_schema_fields") != list(EXPECTED_PROBE_RESULT_SCHEMA_FIELDS):
        errors.append("expected_probe_result_schema_fields must match PCWEB-088 schema fields")
    if policy.get("redaction_requirements") != list(REDACTION_REQUIREMENTS):
        errors.append("redaction_requirements must match PCWEB-088 redaction requirements")
    if policy.get("cargo_check_blockers") != list(CARGO_CHECK_BLOCKERS):
        errors.append("cargo_check_blockers must match PCWEB-088 cargo check blockers")
    if policy.get("forbidden_default_side_effects") != _trusted_forbidden_default_side_effects():
        errors.append("forbidden_default_side_effects must match PCWEB-088 forbidden side effects")
    if not OFFICIAL_SOURCE_URLS.issubset(_source_urls(policy)):
        errors.append("official_sources must contain required PCWEB-088 official URLs")
    return errors


def _report_packet_sections(policy: dict[str, object], *, validation_passed: bool) -> dict[str, object]:
    if validation_passed:
        return {
            "future_probe_command_allowlist": policy.get("future_probe_command_allowlist", []),
            "expected_probe_result_schema_fields": policy.get("expected_probe_result_schema_fields", []),
            "redaction_requirements": policy.get("redaction_requirements", []),
            "cargo_check_blockers": policy.get("cargo_check_blockers", []),
            "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
            "official_sources": policy.get("official_sources", []),
        }
    return {
        "future_probe_command_allowlist": _trusted_future_probe_command_allowlist(),
        "expected_probe_result_schema_fields": list(EXPECTED_PROBE_RESULT_SCHEMA_FIELDS),
        "redaction_requirements": list(REDACTION_REQUIREMENTS),
        "cargo_check_blockers": list(CARGO_CHECK_BLOCKERS),
        "forbidden_default_side_effects": _trusted_forbidden_default_side_effects(),
        "official_sources": _trusted_official_sources(),
    }


def build_rust_post_install_probe_approval_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
) -> dict[str, object]:
    policy_path_errors = validate_policy_path(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = load_policy(policy_path)
    validation_errors = validate_policy(policy)
    validation_passed = not validation_errors
    packet_sections = _report_packet_sections(policy, validation_passed=validation_passed)

    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "report_mode": "rust_post_install_probe_approval_static_report",
        "policy_status": POLICY_STATUS,
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "probe_approval_status": (
            "generated_for_manual_review" if validation_passed else "blocked_by_policy_validation"
        ),
        "probe_approval_mode": "no_probe_execution_approval_packet_only",
        "probe_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        **packet_sections,
        **_false_safety_flags(),
    }


def _blocked_policy_path_report(policy_path_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "report_mode": "rust_post_install_probe_approval_static_report",
        "policy_status": "blocked_policy_path",
        "policy_read_status": "blocked",
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_path_errors,
        "probe_approval_status": "blocked_by_policy_validation",
        "probe_approval_mode": "no_probe_execution_approval_packet_only",
        "probe_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "cargo_check_readiness": "blocked_until_pcweb_084_and_user_approval",
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "future_probe_command_allowlist": _trusted_future_probe_command_allowlist(),
        "expected_probe_result_schema_fields": list(EXPECTED_PROBE_RESULT_SCHEMA_FIELDS),
        "redaction_requirements": list(REDACTION_REQUIREMENTS),
        "cargo_check_blockers": list(CARGO_CHECK_BLOCKERS),
        "forbidden_default_side_effects": _trusted_forbidden_default_side_effects(),
        "official_sources": _trusted_official_sources(),
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-088 Rust post-install probe approval policy JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_rust_post_install_probe_approval_report(policy_path=args.policy)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

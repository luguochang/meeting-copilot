#!/usr/bin/env python3
"""Build the PCWEB-087 Rust toolchain install approval packet as static JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-install-approval.policy.json"

SAFETY_FLAGS = (
    "safe_to_execute_install_now",
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
    "approved_manual_user_run_only_boundary",
    "approved_rustup_uninstall_or_rollback_understanding",
)
POST_INSTALL_VERIFICATION_ORDER = (
    "rustc_version",
    "cargo_version",
    "rustup_version",
    "macos_xcode_select_presence_redacted",
    "pcweb_084_cargo_check_preflight",
)
OFFICIAL_SOURCE_URLS = {
    "https://www.rust-lang.org/tools/install",
    "https://rust-lang.github.io/rustup/installation/index.html",
    "https://v2.tauri.app/start/prerequisites/",
}
RUSTUP_SHELL_MANUAL_TEXT = "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
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


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


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
            "label": "Tauri v2 prerequisites",
            "url": "https://v2.tauri.app/start/prerequisites/",
        },
    ]


def _trusted_manual_instruction_text_by_platform() -> dict[str, dict[str, object]]:
    return {
        "macos": {
            "source": "official_rust_install_page",
            "manual_command_text": RUSTUP_SHELL_MANUAL_TEXT,
            "execution_boundary": "manual_user_run_only",
            "notes": [
                "The repository tool must not execute this text.",
                "A new shell session may be required before rustc, cargo, or rustup are visible.",
            ],
        },
        "windows": {
            "source": "official_rust_install_page_and_tauri_prerequisites",
            "manual_installer_text": (
                "Download and run rustup-init.exe from the official Rust install page, "
                "then ensure MSVC Build Tools and WebView2 prerequisites are present before any future Tauri build."
            ),
            "manual_optional_winget_text": (
                "No package-manager command is approved by PCWEB-087; any winget, scoop, "
                "or choco command remains manual text outside default gates."
            ),
            "execution_boundary": "manual_text_only_not_executable_by_tool",
            "notes": [
                "Windows validation is future-platform work and was not executed on this macOS machine.",
                "MSVC Build Tools and WebView2 readiness remain separate future checks.",
            ],
        },
        "linux": {
            "source": "official_rust_install_page_and_tauri_prerequisites",
            "manual_command_text": RUSTUP_SHELL_MANUAL_TEXT,
            "execution_boundary": "manual_user_run_only",
            "notes": [
                "Distribution-specific Tauri system packages remain future manual prerequisites.",
                "No apt, dnf, yum, pacman, zypper, or package-manager command is approved by PCWEB-087.",
            ],
        },
    }


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != "PCWEB-087":
        errors.append("pcweb_id must be PCWEB-087")
    if policy.get("policy_status") != "rust_toolchain_install_approval_packet_policy_only":
        errors.append("policy_status must be rust_toolchain_install_approval_packet_policy_only")
    if policy.get("approval_packet_mode") != "manual_user_run_only":
        errors.append("approval_packet_mode must be manual_user_run_only")
    if policy.get("recommended_install_provider") != "official_rustup":
        errors.append("recommended_install_provider must be official_rustup")
    if policy.get("manual_instruction_text_status") != "inert_text_only":
        errors.append("manual_instruction_text_status must be inert_text_only")
    if policy.get("command_execution_status") != "not_run":
        errors.append("command_execution_status must be not_run")
    if policy.get("installation_execution_status") != "not_run":
        errors.append("installation_execution_status must be not_run")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if policy.get("required_approval_tokens_before_install") != list(REQUIRED_APPROVAL_TOKENS):
        errors.append("required_approval_tokens_before_install must match PCWEB-087 required tokens")
    if policy.get("post_install_verification_order") != list(POST_INSTALL_VERIFICATION_ORDER):
        errors.append("post_install_verification_order must match PCWEB-087 verification order")
    if not OFFICIAL_SOURCE_URLS.issubset(_source_urls(policy)):
        errors.append("official_sources must contain required PCWEB-087 official URLs")

    manual_text = policy.get("manual_instruction_text_by_platform")
    if not isinstance(manual_text, dict) or set(manual_text) != {"macos", "windows", "linux"}:
        errors.append("manual_instruction_text_by_platform must contain macos, windows, and linux")
    elif not _manual_text_is_valid(manual_text):
        errors.append("manual_instruction_text_by_platform must keep official instructions as inert text")

    platform_notes = policy.get("platform_notes")
    if not isinstance(platform_notes, dict) or set(platform_notes) != {"macos", "windows", "linux"}:
        errors.append("platform_notes must contain macos, windows, and linux")
    if not _is_string_list(policy.get("risk_notes")):
        errors.append("risk_notes must be a list of strings")
    if not _is_string_list(policy.get("rollback_notes")):
        errors.append("rollback_notes must be a list of strings")
    return errors


def _manual_text_is_valid(manual_text: dict[str, object]) -> bool:
    macos = manual_text.get("macos")
    windows = manual_text.get("windows")
    linux = manual_text.get("linux")
    if not all(isinstance(item, dict) for item in (macos, windows, linux)):
        return False
    return (
        macos.get("manual_command_text") == RUSTUP_SHELL_MANUAL_TEXT
        and linux.get("manual_command_text") == RUSTUP_SHELL_MANUAL_TEXT
        and isinstance(windows.get("manual_installer_text"), str)
        and "rustup-init.exe" in windows["manual_installer_text"]
        and macos.get("execution_boundary") == "manual_user_run_only"
        and linux.get("execution_boundary") == "manual_user_run_only"
        and windows.get("execution_boundary") == "manual_text_only_not_executable_by_tool"
    )


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _report_packet_sections(policy: dict[str, object], *, validation_passed: bool) -> dict[str, object]:
    if validation_passed:
        return {
            "manual_instruction_text_by_platform": policy.get("manual_instruction_text_by_platform", {}),
            "platform_notes": policy.get("platform_notes", {}),
            "risk_notes": policy.get("risk_notes", []),
            "rollback_notes": policy.get("rollback_notes", []),
            "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
            "official_sources": policy.get("official_sources", []),
        }
    return {
        "manual_instruction_text_by_platform": _trusted_manual_instruction_text_by_platform(),
        "platform_notes": {},
        "risk_notes": [],
        "rollback_notes": [],
        "forbidden_default_side_effects": [],
        "official_sources": _trusted_official_sources(),
    }


def build_rust_toolchain_install_approval_packet(
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
        "pcweb_id": policy.get("pcweb_id"),
        "policy_name": policy.get("policy_name"),
        "report_mode": "rust_toolchain_install_approval_packet_static_report",
        "policy_status": policy.get("policy_status"),
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "approval_packet_status": (
            "generated_for_manual_review" if validation_passed else "blocked_by_policy_validation"
        ),
        "approval_packet_mode": "manual_user_run_only",
        "execution_mode": "manual_user_run_only",
        "manual_instruction_text_status": "inert_text_only",
        "command_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "installation_execution_status": "not_run",
        "recommended_install_provider": "official_rustup",
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_install_verification_order": list(POST_INSTALL_VERIFICATION_ORDER),
        **packet_sections,
        **_false_safety_flags(),
    }


def _blocked_policy_path_report(policy_path_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": "PCWEB-087",
        "policy_name": "Desktop Rust Toolchain Install Approval Packet Policy",
        "report_mode": "rust_toolchain_install_approval_packet_static_report",
        "policy_status": "blocked_policy_path",
        "policy_read_status": "blocked",
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_path_errors,
        "approval_packet_status": "blocked_by_policy_validation",
        "approval_packet_mode": "manual_user_run_only",
        "execution_mode": "manual_user_run_only",
        "manual_instruction_text_status": "inert_text_only",
        "command_execution_status": "not_run",
        "external_command_execution_status": "not_run",
        "installation_execution_status": "not_run",
        "recommended_install_provider": "official_rustup",
        "approval_blockers": list(REQUIRED_APPROVAL_TOKENS),
        "post_install_verification_order": list(POST_INSTALL_VERIFICATION_ORDER),
        "manual_instruction_text_by_platform": _trusted_manual_instruction_text_by_platform(),
        "platform_notes": {},
        "risk_notes": [],
        "rollback_notes": [],
        "forbidden_default_side_effects": [],
        "official_sources": _trusted_official_sources(),
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-087 Rust toolchain install approval policy JSON.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_rust_toolchain_install_approval_packet(policy_path=args.policy)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

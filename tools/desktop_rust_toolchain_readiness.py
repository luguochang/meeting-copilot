#!/usr/bin/env python3
"""Report PCWEB-085 Rust toolchain readiness without installing or building."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "rust-toolchain-readiness.policy.json"
EXECUTABLE_PROBE_ALLOWLIST = {
    ("rustc", "--version"),
    ("cargo", "--version"),
    ("rustup", "--version"),
    ("xcode-select", "-p"),
}
BLOCKED_PROBE_RESULT = {
    "returncode": 126,
    "stdout": "",
    "stderr": "blocked by desktop rust toolchain readiness probe allowlist",
}
SAFETY_FLAGS = (
    "safe_to_install_toolchain_now",
    "safe_to_modify_shell_profile_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_read_configs_local_now",
)


CommandRunner = Callable[[list[str]], dict[str, object]]


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    return json.loads(policy_path.read_text(encoding="utf-8"))


def _is_string_list_list(value: object) -> bool:
    return isinstance(value, list) and all(
        isinstance(command, list) and all(isinstance(item, str) for item in command)
        for command in value
    )


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != "PCWEB-085":
        errors.append("pcweb_id must be PCWEB-085")
    if policy.get("policy_status") != "rust_toolchain_readiness_policy_only":
        errors.append("policy_status must be rust_toolchain_readiness_policy_only")
    if policy.get("toolchain_probe_mode") != "local_version_and_platform_probe_only":
        errors.append("toolchain_probe_mode must be local_version_and_platform_probe_only")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    if not _is_string_list_list(policy.get("allowed_probe_commands")):
        errors.append("allowed_probe_commands must be a list of string lists")
    redaction_policy = policy.get("probe_output_redaction_policy")
    if not isinstance(redaction_policy, dict) or redaction_policy.get("xcode_select_path") != "presence_only":
        errors.append("probe_output_redaction_policy.xcode_select_path must be presence_only")
    return errors


def run_probe_command(command: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        executable = exc.filename or command[0]
        return {
            "command": command,
            "returncode": 127,
            "stdout": "",
            "stderr": f"missing executable: {executable}",
        }
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _sanitize_probe_result(result: dict[str, object]) -> dict[str, object]:
    command = result.get("command")
    sanitized = dict(result)
    if command == ["xcode-select", "-p"]:
        sanitized["stderr"] = ""
        if result.get("returncode") == 0 and str(result.get("stdout", "")).strip():
            sanitized["stdout"] = "[redacted:xcode-select-path-present]"
            sanitized["path_status"] = "path_present"
        else:
            sanitized["stdout"] = ""
            sanitized["path_status"] = "path_missing"
    return sanitized


def run_allowed_probe_command(command: list[str], runner: CommandRunner) -> dict[str, object]:
    if tuple(command) not in EXECUTABLE_PROBE_ALLOWLIST:
        return {"command": command, **BLOCKED_PROBE_RESULT}
    return _sanitize_probe_result(runner(command))


def _status_for_probe(results: list[dict[str, object]], command: list[str]) -> str:
    for result in results:
        if result.get("command") == command:
            if result.get("returncode") == 0:
                return "available"
            if result.get("returncode") == 126:
                return "blocked"
            if result.get("returncode") == 127:
                return "missing"
            return "error"
    return "not_probed"


def _summarize_probe_results(results: list[dict[str, object]]) -> dict[str, str]:
    summary = {
        "rustc_status": _status_for_probe(results, ["rustc", "--version"]),
        "cargo_status": _status_for_probe(results, ["cargo", "--version"]),
        "rustup_status": _status_for_probe(results, ["rustup", "--version"]),
        "macos_command_line_tools_status": _status_for_probe(results, ["xcode-select", "-p"]),
    }
    if summary["rustc_status"] != "available" or summary["cargo_status"] != "available":
        summary["first_cargo_check_blocker"] = "missing_required_rust_toolchain"
    elif summary["macos_command_line_tools_status"] != "available":
        summary["first_cargo_check_blocker"] = "missing_platform_prerequisite"
    else:
        summary["first_cargo_check_blocker"] = "explicit_approval_required"
    return summary


def build_rust_toolchain_readiness_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    probe_local_toolchain: bool = False,
    runner: CommandRunner = run_probe_command,
) -> dict[str, object]:
    policy = load_policy(policy_path)
    validation_errors = validate_policy(policy)
    validation_passed = not validation_errors
    probe_results: list[dict[str, object]] = []

    if probe_local_toolchain and validation_passed:
        for command in policy["allowed_probe_commands"]:
            probe_results.append(run_allowed_probe_command(list(command), runner))

    probe_summary = _summarize_probe_results(probe_results) if probe_results else {
        "rustc_status": "not_probed",
        "cargo_status": "not_probed",
        "rustup_status": "not_probed",
        "macos_command_line_tools_status": "not_probed",
        "first_cargo_check_blocker": "not_evaluated",
    }
    if not probe_local_toolchain:
        readiness_status = "not_evaluated"
    elif not validation_passed:
        readiness_status = "blocked_by_policy_validation"
    elif probe_summary["first_cargo_check_blocker"] == "explicit_approval_required":
        readiness_status = "blocked_until_explicit_approval_for_first_cargo_check"
    else:
        readiness_status = probe_summary["first_cargo_check_blocker"]

    return {
        "pcweb_id": policy.get("pcweb_id"),
        "policy_name": policy.get("policy_name"),
        "report_mode": "local_version_and_platform_probe_only" if probe_local_toolchain else "rust_toolchain_readiness_static_report",
        "policy_status": policy.get("policy_status"),
        "policy_validation_status": "passed" if validation_passed else "failed",
        "policy_validation_errors": validation_errors,
        "toolchain_probe_status": (
            "blocked_by_policy_validation"
            if probe_local_toolchain and not validation_passed
            else "probed"
            if probe_local_toolchain
            else "not_run"
        ),
        "toolchain_probe_results": probe_results,
        "toolchain_probe_summary": probe_summary,
        "first_cargo_check_readiness_status": readiness_status,
        "safe_to_install_toolchain_now": False,
        "safe_to_modify_shell_profile_now": False,
        "safe_to_run_cargo_check_now": False,
        "safe_to_run_cargo_build_now": False,
        "safe_to_run_tauri_dev_now": False,
        "safe_to_run_tauri_build_now": False,
        "safe_to_fetch_dependencies_now": False,
        "safe_to_generate_cargo_lock_now": False,
        "safe_to_generate_target_dir_now": False,
        "safe_to_read_configs_local_now": False,
        "probe_output_redaction_policy": policy.get("probe_output_redaction_policy"),
        "toolchain_component_policy": policy.get("toolchain_component_policy"),
        "remaining_preconditions_before_first_cargo_check": policy.get(
            "remaining_preconditions_before_first_cargo_check",
            [],
        ),
        "forbidden_default_side_effects": policy.get("forbidden_default_side_effects", []),
        "official_sources": policy.get("official_sources", []),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-085 Rust toolchain readiness policy JSON.",
    )
    parser.add_argument(
        "--probe-local-toolchain",
        action="store_true",
        help="Run only allowlisted local toolchain/platform probes; never install or build.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_rust_toolchain_readiness_report(
        policy_path=args.policy,
        probe_local_toolchain=args.probe_local_toolchain,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

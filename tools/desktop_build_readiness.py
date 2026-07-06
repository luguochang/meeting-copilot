#!/usr/bin/env python3
"""Report PCWEB-083 desktop build readiness without running a build."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "build-readiness.policy.json"
EXECUTABLE_PROBE_ALLOWLIST = {
    ("rustc", "--version"),
    ("cargo", "--version"),
}
BLOCKED_PROBE_RESULT = {
    "returncode": 126,
    "stdout": "",
    "stderr": "blocked by desktop build readiness probe allowlist",
}


CommandRunner = Callable[[list[str]], dict[str, object]]


def load_policy(policy_path: Path = DEFAULT_POLICY_PATH) -> dict[str, object]:
    return json.loads(policy_path.read_text(encoding="utf-8"))


def run_version_command(command: list[str]) -> dict[str, object]:
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


def run_allowed_probe_command(command: list[str], runner: CommandRunner) -> dict[str, object]:
    if tuple(command) not in EXECUTABLE_PROBE_ALLOWLIST:
        return {"command": command, **BLOCKED_PROBE_RESULT}
    return runner(command)


def build_readiness_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    probe_toolchain: bool = False,
    runner: CommandRunner = run_version_command,
) -> dict[str, object]:
    policy = load_policy(policy_path)
    probe_results: list[dict[str, object]] = []

    if probe_toolchain:
        for command in policy["allowed_probe_commands"]:
            probe_results.append(run_allowed_probe_command(list(command), runner))

    return {
        "pcweb_id": policy["pcweb_id"],
        "policy_name": policy["policy_name"],
        "report_mode": "toolchain_version_probe_only" if probe_toolchain else "static_policy_only",
        "policy_status": policy["policy_status"],
        "toolchain_probe_status": "probed" if probe_toolchain else "not_run",
        "toolchain_probe_results": probe_results,
        "dependency_install_status": policy["dependency_install_status"],
        "build_execution_status": policy["build_execution_status"],
        "tauri_cli_execution_status": policy["tauri_cli_execution_status"],
        "safe_to_probe_toolchain_versions_now": policy["safe_to_probe_toolchain_versions_now"],
        "safe_to_run_cargo_check_now": policy["safe_to_run_cargo_check_now"],
        "safe_to_run_tauri_dev_now": policy["safe_to_run_tauri_dev_now"],
        "safe_to_run_tauri_build_now": policy["safe_to_run_tauri_build_now"],
        "safe_to_install_dependencies_now": policy["safe_to_install_dependencies_now"],
        "safe_to_generate_lockfiles_now": policy["safe_to_generate_lockfiles_now"],
        "safe_to_generate_build_artifacts_now": policy["safe_to_generate_build_artifacts_now"],
        "future_build_check_candidate": policy["future_build_check_candidate"],
        "future_build_check_status": policy["future_build_check_status"],
        "required_future_preconditions": policy["required_future_preconditions"],
        "forbidden_default_artifacts": policy["forbidden_default_artifacts"],
        "forbidden_default_side_effects": policy["forbidden_default_side_effects"],
        "official_sources": policy["official_sources"],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        type=Path,
        default=DEFAULT_POLICY_PATH,
        help="Path to the PCWEB-083 desktop build readiness policy JSON.",
    )
    parser.add_argument(
        "--probe-toolchain",
        action="store_true",
        help="Run only rustc/cargo version probes; never run cargo check/build or Tauri CLI.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_readiness_report(
        policy_path=args.policy,
        probe_toolchain=args.probe_toolchain,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

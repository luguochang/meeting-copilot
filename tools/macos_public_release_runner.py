#!/usr/bin/env python3
"""Run the macOS public release signing, notarization, and Gatekeeper sequence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import subprocess
from typing import Any, Callable, NamedTuple


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_TMP = (REPO_ROOT / "artifacts" / "tmp").resolve()
DEFAULT_APP = REPO_ROOT / "artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app"
DEFAULT_BUNDLE_DMG_SCRIPT = REPO_ROOT / "artifacts/tmp/desktop_tauri_target/release/bundle/dmg/bundle_dmg.sh"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/macos_public_release_current"


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], CommandResult]


def resolve_output_root(path: Path) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(ARTIFACTS_TMP)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def build_release_evidence(
    *,
    app_path: Path,
    bundle_dmg_script: Path,
    output_root: Path,
    developer_id_application: str | None,
    notary_profile: str | None,
    run_id: str,
    execute: bool,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    app_path = _resolve_path(app_path)
    bundle_dmg_script = _resolve_path(bundle_dmg_script)
    output_root = resolve_output_root(output_root)
    source_dir = output_root / "source"
    staged_app = source_dir / app_path.name
    dmg_path = output_root / "Meeting Copilot_0.1.0_aarch64.public-release.dmg"

    command_results: list[dict[str, Any]] = []
    identity_result = _record(command_results, command_runner(["security", "find-identity", "-v", "-p", "codesigning"]))
    notarytool_result = _record(command_results, command_runner(["xcrun", "--find", "notarytool"]))

    identities = parse_developer_id_application_identities(identity_result.stdout)
    selected_identity = _select_identity(identities, developer_id_application)
    notarytool_available = notarytool_result.returncode == 0

    remaining_blockers: list[str] = []
    if selected_identity is None:
        remaining_blockers.append("developer_id_signing_not_done")
    if not notarytool_available or not notary_profile:
        remaining_blockers.append("notarization_not_done")
    if not execute and not remaining_blockers:
        remaining_blockers.append("release_execution_not_enabled")

    mutating_results: list[dict[str, Any]] = []
    notarization_submitted = False
    if not remaining_blockers and selected_identity and notary_profile:
        output_root.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        mutating_results = _execute_release_sequence(
            app_path=app_path,
            bundle_dmg_script=bundle_dmg_script,
            staged_app=staged_app,
            dmg_path=dmg_path,
            identity=selected_identity,
            notary_profile=notary_profile,
            command_runner=command_runner,
        )
        notarization_submitted = _command_was_successful(mutating_results, ["xcrun", "notarytool", "submit"])
        remaining_blockers = _execution_blockers(mutating_results)

    status = (
        "go_public_release_signed_notarized_gatekeeper"
        if execute and not remaining_blockers
        else "blocked_public_release_execution_requirements"
    )
    return {
        "schema_version": "macos_public_release_runner.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host_platform": platform.platform(),
        "status": status,
        "source_app": _display_path(app_path),
        "bundle_dmg_script": _display_path(bundle_dmg_script),
        "staged_app": _display_path(staged_app),
        "dmg_path": _display_path(dmg_path),
        "developer_id": {
            "requested_identity": developer_id_application or "",
            "selected_identity": selected_identity or "",
            "developer_id_application_count": len(identities),
            "security_find_identity_exit_code": identity_result.returncode,
        },
        "notarization": {
            "notarytool_available": notarytool_available,
            "notary_profile_provided": bool(notary_profile),
            "notary_profile_name_recorded": bool(notary_profile),
        },
        "counts_as_public_release_package": execute and not remaining_blockers,
        "executed_mutating_command_count": len(mutating_results),
        "privacy_safety": {
            "secret_values_read": False,
            "keychain_password_read": False,
            "notarization_submitted": notarization_submitted,
            "remote_service_called": notarization_submitted,
        },
        "remaining_blockers": remaining_blockers,
        "preflight_commands": command_results,
        "mutating_commands": mutating_results,
    }


def parse_developer_id_application_identities(output: str) -> list[str]:
    identities: list[str] = []
    for line in output.splitlines():
        match = re.search(r'\)\s+[A-Fa-f0-9]{3,40}\s+"([^"]+)"', line)
        if match and match.group(1).startswith("Developer ID Application:"):
            identities.append(match.group(1))
    return identities


def write_evidence(evidence: dict[str, Any], output_root: Path) -> Path:
    output_root = resolve_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "evidence.json"
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def _select_identity(identities: list[str], requested: str | None) -> str | None:
    if requested:
        return requested if requested in identities else None
    return identities[0] if identities else None


def _execute_release_sequence(
    *,
    app_path: Path,
    bundle_dmg_script: Path,
    staged_app: Path,
    dmg_path: Path,
    identity: str,
    notary_profile: str,
    command_runner: CommandRunner,
) -> list[dict[str, Any]]:
    source_dir = staged_app.parent
    commands = [
        ["ditto", str(app_path), str(staged_app)],
        ["codesign", "--force", "--deep", "--options", "runtime", "--timestamp", "--sign", identity, str(staged_app)],
        ["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(staged_app)],
        [
            str(bundle_dmg_script),
            "--skip-jenkins",
            "--volname",
            "Meeting Copilot",
            "--app-drop-link",
            "360",
            "170",
            "--no-internet-enable",
            str(dmg_path),
            str(source_dir),
        ],
        ["codesign", "--force", "--timestamp", "--sign", identity, str(dmg_path)],
        ["xcrun", "notarytool", "submit", str(dmg_path), "--keychain-profile", notary_profile, "--wait"],
        ["xcrun", "stapler", "staple", str(dmg_path)],
        ["spctl", "--assess", "--type", "execute", "--verbose=4", str(staged_app)],
        ["spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "--verbose=4", str(dmg_path)],
    ]
    return [_command_payload(command, command_runner(command)) for command in commands]


def _execution_blockers(results: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    if _first_failed(results, "codesign"):
        blockers.append("developer_id_signing_failed")
    if _first_failed(results, "xcrun", "notarytool", "submit") or _first_failed(results, "xcrun", "stapler", "staple"):
        blockers.append("notarization_failed")
    if _first_failed(results, "spctl"):
        blockers.append("gatekeeper_acceptance_not_done")
    return blockers


def _first_failed(results: list[dict[str, Any]], *prefix: str) -> bool:
    for result in results:
        command = list(result.get("argv") or [])
        if command[: len(prefix)] == list(prefix) and result.get("returncode") != 0:
            return True
    return False


def _command_was_successful(results: list[dict[str, Any]], prefix: list[str]) -> bool:
    for result in results:
        command = list(result.get("argv") or [])
        if command[: len(prefix)] == prefix and result.get("returncode") == 0:
            return True
    return False


def _record(results: list[dict[str, Any]], command_result: CommandResult) -> CommandResult:
    results.append(
        {
            "returncode": command_result.returncode,
            "stdout": command_result.stdout[-4000:],
            "stderr": command_result.stderr[-4000:],
        }
    )
    return command_result


def _command_payload(command: list[str], result: CommandResult) -> dict[str, Any]:
    return {
        "argv": command,
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def _resolve_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _run(command: list[str]) -> CommandResult:
    proc = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return CommandResult(returncode=proc.returncode, stdout=proc.stdout[-4000:], stderr=proc.stderr[-4000:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=DEFAULT_APP)
    parser.add_argument("--bundle-dmg-script", type=Path, default=DEFAULT_BUNDLE_DMG_SCRIPT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--developer-id-application", default=None)
    parser.add_argument("--notary-profile", default=None)
    parser.add_argument("--run-id", default="macos-public-release-current")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    evidence = build_release_evidence(
        app_path=args.app,
        bundle_dmg_script=args.bundle_dmg_script,
        output_root=args.output_root,
        developer_id_application=args.developer_id_application,
        notary_profile=args.notary_profile,
        run_id=args.run_id,
        execute=args.execute,
        command_runner=_run,
    )
    output_path = write_evidence(evidence, args.output_root)
    print(json.dumps({"status": evidence["status"], "evidence": str(output_path)}, ensure_ascii=False))
    return 0 if not evidence["remaining_blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

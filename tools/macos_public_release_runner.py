#!/usr/bin/env python3
"""Run the macOS Apple distribution signing and validation lane."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any, Callable, NamedTuple


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import macos_codesign  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_TMP = (REPO_ROOT / "artifacts" / "tmp").resolve()
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/macos_public_release_current"
PUBLIC_RELEASE_EVIDENCE_BLOCKERS = (
    "redistribution_provenance_unverified",
    "independent_clean_machine_unverified",
)


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], CommandResult]


class ReleaseSequenceResult(NamedTuple):
    command_results: list[dict[str, Any]]
    strict_codesign: dict[str, Any]
    blockers: list[str]


def resolve_output_root(path: Path) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(ARTIFACTS_TMP)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def _execution_input_blockers(
    app_path: Path | None,
    bundle_dmg_script: Path | None,
) -> list[str]:
    blockers: list[str] = []
    if app_path is None:
        blockers.append("release_app_input_not_provided")
    elif not app_path.exists():
        blockers.append("release_app_input_not_found")
    elif not app_path.is_dir() or app_path.suffix != ".app":
        blockers.append("release_app_input_invalid")

    if bundle_dmg_script is None:
        blockers.append("bundle_dmg_script_input_not_provided")
    elif not bundle_dmg_script.exists():
        blockers.append("bundle_dmg_script_input_not_found")
    elif not bundle_dmg_script.is_file():
        blockers.append("bundle_dmg_script_input_invalid")
    elif not os.access(bundle_dmg_script, os.X_OK):
        blockers.append("bundle_dmg_script_input_not_executable")
    return blockers


def build_release_evidence(
    *,
    app_path: Path | None,
    bundle_dmg_script: Path | None,
    output_root: Path,
    developer_id_application: str | None,
    notary_profile: str | None,
    run_id: str,
    execute: bool,
    command_runner: CommandRunner,
) -> dict[str, Any]:
    app_path = _resolve_path(app_path) if app_path is not None else None
    bundle_dmg_script = (
        _resolve_path(bundle_dmg_script) if bundle_dmg_script is not None else None
    )
    output_root = resolve_output_root(output_root)
    source_dir = output_root / "source"
    staged_app = source_dir / (app_path.name if app_path is not None else "app")
    dmg_path = output_root / "Meeting Copilot_0.1.0_aarch64.public-release.dmg"

    command_results: list[dict[str, Any]] = []
    execution_input_blockers = (
        _execution_input_blockers(app_path, bundle_dmg_script) if execute else []
    )
    identity_result: CommandResult | None = None
    notarytool_result: CommandResult | None = None
    if not execution_input_blockers:
        identity_result = _record(
            command_results,
            command_runner(["security", "find-identity", "-v", "-p", "codesigning"]),
        )
        notarytool_result = _record(
            command_results, command_runner(["xcrun", "--find", "notarytool"])
        )

    identities = parse_developer_id_application_identities(
        identity_result.stdout if identity_result is not None else ""
    )
    selected_identity = _select_identity(identities, developer_id_application)
    notarytool_available = (
        notarytool_result is not None and notarytool_result.returncode == 0
    )

    apple_lane_blockers = list(execution_input_blockers)
    if not execution_input_blockers:
        if selected_identity is None:
            apple_lane_blockers.append("developer_id_signing_not_done")
        if not notarytool_available or not notary_profile:
            apple_lane_blockers.append("notarization_not_done")
        if not execute and not apple_lane_blockers:
            apple_lane_blockers.append("release_execution_not_enabled")

    mutating_results: list[dict[str, Any]] = []
    strict_codesign = _strict_codesign_not_executed()
    notarization_submitted = False
    if (
        not apple_lane_blockers
        and app_path is not None
        and bundle_dmg_script is not None
        and selected_identity
        and notary_profile
    ):
        output_root.mkdir(parents=True, exist_ok=True)
        source_dir.mkdir(parents=True, exist_ok=True)
        sequence = _execute_release_sequence(
            app_path=app_path,
            bundle_dmg_script=bundle_dmg_script,
            staged_app=staged_app,
            dmg_path=dmg_path,
            identity=selected_identity,
            notary_profile=notary_profile,
            command_runner=command_runner,
        )
        mutating_results = sequence.command_results
        strict_codesign = sequence.strict_codesign
        notarization_submitted = _command_was_successful(
            mutating_results, ["xcrun", "notarytool", "submit"]
        )
        apple_lane_blockers = sequence.blockers

    apple_lane_passed = execute and not apple_lane_blockers
    remaining_blockers = [
        *apple_lane_blockers,
        *PUBLIC_RELEASE_EVIDENCE_BLOCKERS,
    ]
    status = (
        "apple_distribution_lane_passed_public_release_blocked"
        if apple_lane_passed
        else "apple_distribution_lane_blocked"
    )
    return {
        "schema_version": "macos_public_release_runner.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "host_platform": platform.platform(),
        "status": status,
        "source_app": _display_optional_path(app_path),
        "bundle_dmg_script": _display_optional_path(bundle_dmg_script),
        "staged_app": _display_path(staged_app),
        "dmg_path": _display_path(dmg_path),
        "execution_inputs": {
            "app_provided": app_path is not None,
            "bundle_dmg_script_provided": bundle_dmg_script is not None,
            "validated_for_execute": execute and not execution_input_blockers,
            "validation_blockers": execution_input_blockers,
        },
        "apple_distribution_lane": {
            "status": "passed" if apple_lane_passed else "blocked",
            "result": (
                "signed_notarized_stapled_gatekeeper_accepted"
                if apple_lane_passed
                else "not_completed"
            ),
            "remaining_blockers": apple_lane_blockers,
        },
        "developer_id": {
            "requested_identity": developer_id_application or "",
            "selected_identity": selected_identity or "",
            "developer_id_application_count": len(identities),
            "security_find_identity_exit_code": (
                identity_result.returncode if identity_result is not None else None
            ),
        },
        "notarization": {
            "notarytool_available": notarytool_available,
            "notary_profile_provided": bool(notary_profile),
            "notary_profile_name_recorded": bool(notary_profile),
        },
        "strict_codesign": strict_codesign,
        "counts_as_public_release_package": False,
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
    output_path.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
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
) -> ReleaseSequenceResult:
    source_dir = staged_app.parent
    command_results: list[dict[str, Any]] = []
    strict_codesign = _strict_codesign_not_executed()

    if not _run_recorded(
        command_results, command_runner, ["ditto", str(app_path), str(staged_app)]
    ):
        return ReleaseSequenceResult(
            command_results, strict_codesign, ["release_staging_failed"]
        )

    def strict_command_runner(
        command: list[str],
        **_kwargs: Any,
    ) -> subprocess.CompletedProcess[str]:
        result = command_runner(list(command))
        command_results.append(_command_payload(list(command), result))
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    try:
        strict_result = macos_codesign.sign_and_verify(
            staged_app,
            mode="developer-id",
            identity=identity,
            command_runner=strict_command_runner,
        )
        strict_codesign = _validate_strict_codesign_result(
            strict_result, identity=identity
        )
    except (OSError, RuntimeError, ValueError) as exc:
        strict_codesign = {
            **_strict_codesign_not_executed(),
            "status": "failed",
            "failure_type": type(exc).__name__,
            "failure": str(exc)[-4000:],
        }
        return ReleaseSequenceResult(
            command_results,
            strict_codesign,
            ["developer_id_signing_failed"],
        )

    commands_and_blockers = [
        (
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
            "release_package_build_failed",
        ),
        (
            ["codesign", "--force", "--timestamp", "--sign", identity, str(dmg_path)],
            "developer_id_signing_failed",
        ),
        (
            [
                "xcrun",
                "notarytool",
                "submit",
                str(dmg_path),
                "--keychain-profile",
                notary_profile,
                "--wait",
            ],
            "notarization_failed",
        ),
        (["xcrun", "stapler", "staple", str(dmg_path)], "notarization_failed"),
        (
            ["spctl", "--assess", "--type", "execute", "--verbose=4", str(staged_app)],
            "gatekeeper_acceptance_not_done",
        ),
        [
            [
                "spctl",
                "--assess",
                "--type",
                "open",
                "--context",
                "context:primary-signature",
                "--verbose=4",
                str(dmg_path),
            ],
            "gatekeeper_acceptance_not_done",
        ],
    ]
    for command, blocker in commands_and_blockers:
        if not _run_recorded(command_results, command_runner, command):
            return ReleaseSequenceResult(command_results, strict_codesign, [blocker])
    return ReleaseSequenceResult(command_results, strict_codesign, [])


def _strict_codesign_not_executed() -> dict[str, Any]:
    return {
        "contract": "tools/macos_codesign.py::sign_and_verify",
        "status": "not_executed",
        "mode": "developer-id",
        "hardened_runtime": False,
        "secure_timestamp": False,
        "uses_deep_signing": False,
        "uses_deep_verification": False,
        "signed_target_count": 0,
        "verified_target_count": 0,
        "runtime_verified": False,
        "identity_verified": False,
        "entitlements_verified": False,
    }


def _validate_strict_codesign_result(
    result: dict[str, Any],
    *,
    identity: str,
) -> dict[str, Any]:
    plan = result.get("signing_plan") or {}
    signing = result.get("signing") or {}
    verification = result.get("verification") or {}
    signing_steps = plan.get("signing_steps") or []
    verification_results = verification.get("results") or []
    expected_count = len(signing_steps)
    contract_is_strict = all(
        (
            plan.get("mode") == "developer-id",
            plan.get("identity") == identity,
            plan.get("hardened_runtime") is True,
            plan.get("secure_timestamp") is True,
            plan.get("uses_deep_signing") is False,
            expected_count > 0,
            all("--deep" not in (step.get("command") or []) for step in signing_steps),
            signing.get("status") == "passed",
            signing.get("mode") == "developer-id",
            signing.get("identity") == identity,
            signing.get("hardened_runtime") is True,
            signing.get("uses_deep_signing") is False,
            signing.get("signed_target_count") == expected_count,
            verification.get("status") == "passed",
            verification.get("mode") == "developer-id",
            verification.get("identity") == identity,
            verification.get("uses_deep_verification") is False,
            len(verification_results) == expected_count,
            all(
                verification_result.get("runtime_verified") is True
                and verification_result.get("identity_verified") is True
                and verification_result.get("entitlements_verified") is True
                for verification_result in verification_results
            ),
        )
    )
    if not contract_is_strict:
        raise RuntimeError(
            "strict macOS codesign result does not satisfy the release contract"
        )
    return {
        "contract": "tools/macos_codesign.py::sign_and_verify",
        "status": "passed",
        "mode": "developer-id",
        "hardened_runtime": True,
        "secure_timestamp": True,
        "uses_deep_signing": False,
        "uses_deep_verification": False,
        "signed_target_count": expected_count,
        "verified_target_count": len(verification_results),
        "runtime_verified": True,
        "identity_verified": True,
        "entitlements_verified": True,
    }


def _run_recorded(
    results: list[dict[str, Any]],
    command_runner: CommandRunner,
    command: list[str],
) -> bool:
    result = command_runner(command)
    results.append(_command_payload(command, result))
    return result.returncode == 0


def _command_was_successful(results: list[dict[str, Any]], prefix: list[str]) -> bool:
    for result in results:
        command = list(result.get("argv") or [])
        if command[: len(prefix)] == prefix and result.get("returncode") == 0:
            return True
    return False


def _record(
    results: list[dict[str, Any]], command_result: CommandResult
) -> CommandResult:
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


def _display_optional_path(path: Path | None) -> str:
    return _display_path(path) if path is not None else ""


def _run(command: list[str]) -> CommandResult:
    proc = subprocess.run(
        command, cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    return CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout[-4000:],
        stderr=proc.stderr[-4000:],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=None)
    parser.add_argument("--bundle-dmg-script", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--developer-id-application", default=None)
    parser.add_argument("--notary-profile", default=None)
    parser.add_argument("--run-id", default="macos-public-release-current")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        missing_inputs = []
        if args.app is None:
            missing_inputs.append("--app")
        if args.bundle_dmg_script is None:
            missing_inputs.append("--bundle-dmg-script")
        if missing_inputs:
            parser.error(
                "--execute requires explicit current inputs: "
                + ", ".join(missing_inputs)
            )
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
    print(
        json.dumps(
            {"status": evidence["status"], "evidence": str(output_path)},
            ensure_ascii=False,
        )
    )
    return 0 if not evidence["remaining_blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

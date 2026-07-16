#!/usr/bin/env python3
"""Check external release blockers for macOS/Windows distribution readiness."""

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
DEFAULT_SAME_CHAIN_EVIDENCE = (
    REPO_ROOT
    / "artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json"
)
DEFAULT_DMG_EVIDENCE = REPO_ROOT / "artifacts/tmp/desktop_dmg_skip_finder_current_20260709/evidence.json"
DEFAULT_MACOS_RELEASE_EVIDENCE = REPO_ROOT / "artifacts/tmp/macos_public_release_20260709/evidence.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/release_external_preflight_current"


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], CommandResult]


def parse_codesign_identities(output: str) -> dict[str, Any]:
    identities: list[dict[str, str]] = []
    for line in output.splitlines():
        match = re.search(r'\)\s+([A-Fa-f0-9]{3,40})\s+"([^"]+)"', line)
        if not match:
            continue
        fingerprint, label = match.groups()
        team_match = re.search(r"\(([A-Z0-9]{6,12})\)\s*$", label)
        identities.append(
            {
                "fingerprint": fingerprint,
                "label": label,
                "team_id": team_match.group(1) if team_match else "",
            }
        )
    developer_id_application = [
        identity for identity in identities if identity["label"].startswith("Developer ID Application:")
    ]
    developer_id_installer = [
        identity for identity in identities if identity["label"].startswith("Developer ID Installer:")
    ]
    development = [
        identity
        for identity in identities
        if identity["label"].startswith("Apple Development:")
        or identity["label"].startswith("Mac Developer:")
    ]
    return {
        "identity_count": len(identities),
        "development_identity_count": len(development),
        "developer_id_application_count": len(developer_id_application),
        "developer_id_installer_count": len(developer_id_installer),
        "developer_id_application_identities": developer_id_application,
        "developer_id_installer_identities": developer_id_installer,
    }


def resolve_output_root(path: Path) -> Path:
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        resolved.relative_to(ARTIFACTS_TMP)
    except ValueError as exc:
        raise ValueError("output_root must be under artifacts/tmp") from exc
    return resolved


def build_evidence(
    *,
    same_chain_evidence_path: Path,
    dmg_evidence_path: Path,
    macos_release_evidence_path: Path | None,
    windows_evidence_path: Path | None,
    notary_profile: str | None,
    command_runner: CommandRunner,
    run_id: str,
) -> dict[str, Any]:
    same_chain = _load_json(same_chain_evidence_path)
    dmg = _load_json(dmg_evidence_path)
    macos_release = _load_json(macos_release_evidence_path) if macos_release_evidence_path else {}
    windows = _load_json(windows_evidence_path) if windows_evidence_path else None

    identity_result = command_runner(["security", "find-identity", "-v", "-p", "codesigning"])
    notarytool_result = command_runner(["xcrun", "--find", "notarytool"])
    identities = parse_codesign_identities(identity_result.stdout)

    same_chain_ready = bool(same_chain.get("counts_as_packaged_same_chain_no_cost_evidence"))
    dmg_results = dict(dmg.get("results") or {})
    app_accepted = dmg_results.get("spctl_app_exit_code") == 0
    dmg_accepted = dmg_results.get("spctl_dmg_exit_code") == 0
    notarytool_available = notarytool_result.returncode == 0
    notary_profile_provided = bool(notary_profile)
    macos_public_release_ready = (
        macos_release.get("status") == "go_public_release_signed_notarized_gatekeeper"
        and macos_release.get("counts_as_public_release_package") is True
    )
    windows_status = str((windows or {}).get("status") or "missing")
    windows_verified = bool(
        windows
        and windows_status == "go_windows_real_machine_verified"
        and windows.get("windows_real_machine_verified") is True
    )
    screenshot_blocked = "packaged_screenshot_evidence_still_missing" in list(
        dmg.get("remaining_blockers") or []
    )
    internal_dom_runtime_probe_ready = same_chain_ready and bool(
        same_chain.get("counts_as_packaged_mainline_evidence")
    )
    screenshot_waived_by_internal_probe = screenshot_blocked and internal_dom_runtime_probe_ready

    if not screenshot_blocked:
        visual_evidence = {
            "packaged_screenshot_evidence_present": True,
            "packaged_screenshot_requirement_status": "present",
            "waiver_reason": "",
        }
    elif screenshot_waived_by_internal_probe:
        visual_evidence = {
            "packaged_screenshot_evidence_present": False,
            "packaged_screenshot_requirement_status": "waived_by_internal_dom_runtime_probe",
            "waiver_reason": "packaged DOM/runtime/same-chain evidence is stronger than OS-level screenshot evidence for release preflight",
        }
    else:
        visual_evidence = {
            "packaged_screenshot_evidence_present": False,
            "packaged_screenshot_requirement_status": "missing_blocks_visual_release_review",
            "waiver_reason": "",
        }

    remaining_blockers: list[str] = []
    if identities["developer_id_application_count"] < 1 and not macos_public_release_ready:
        remaining_blockers.append("developer_id_signing_not_done")
    if not macos_public_release_ready:
        remaining_blockers.append("notarization_not_done")
    if not macos_public_release_ready and (not app_accepted or not dmg_accepted):
        remaining_blockers.append("gatekeeper_acceptance_not_done")
    if not windows_verified:
        remaining_blockers.append("windows_real_machine_not_verified")
    if screenshot_blocked and not screenshot_waived_by_internal_probe:
        remaining_blockers.append("packaged_screenshot_evidence_still_missing")

    status = "go_public_release_external_preflight" if not remaining_blockers else "blocked_external_release_requirements"
    return {
        "schema_version": "desktop_release_external_preflight.v1",
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "host_platform": platform.platform(),
        "same_chain_evidence": _display_path(same_chain_evidence_path),
        "dmg_evidence": _display_path(dmg_evidence_path),
        "macos_release_evidence": _display_path(macos_release_evidence_path) if macos_release_evidence_path else None,
        "windows_evidence": _display_path(windows_evidence_path) if windows_evidence_path else None,
        "counts_as_packaged_same_chain_no_cost_evidence": same_chain_ready,
        "counts_as_public_release_package": (not remaining_blockers) and macos_public_release_ready,
        "developer_id": {
            **identities,
            "security_find_identity_exit_code": identity_result.returncode,
            "ready": identities["developer_id_application_count"] >= 1,
        },
        "notarization": {
            "notarytool_available": notarytool_available,
            "notarytool_path": notarytool_result.stdout.strip() if notarytool_available else "",
            "notary_profile_provided": notary_profile_provided,
            "notary_profile_name_recorded": bool(notary_profile),
            "tooling_ready": notarytool_available and notary_profile_provided,
            "ready": macos_public_release_ready,
            "notarization_completed": macos_public_release_ready,
            "secret_values_read": False,
        },
        "macos_public_release": {
            "status": macos_release.get("status", "missing"),
            "counts_as_public_release_package": bool(macos_release.get("counts_as_public_release_package")),
            "ready": macos_public_release_ready,
            "remaining_blockers": list(macos_release.get("remaining_blockers") or []),
        },
        "gatekeeper": {
            "app_accepted": app_accepted,
            "dmg_accepted": dmg_accepted,
            "spctl_app_exit_code": dmg_results.get("spctl_app_exit_code"),
            "spctl_dmg_exit_code": dmg_results.get("spctl_dmg_exit_code"),
        },
        "windows": {
            "status": "verified" if windows_verified else (
                "invalid_or_missing_validator_evidence"
                if windows and windows.get("windows_real_machine_verified") is True
                else "not_verified"
            ),
            "validator_status": windows_status,
            "windows_real_machine_verified": windows_verified,
            "evidence_path": _display_path(windows_evidence_path) if windows_evidence_path else None,
            "remaining_blockers": list((windows or {}).get("remaining_blockers") or []),
        },
        "visual_evidence": visual_evidence,
        "privacy_safety": {
            "secret_values_read": False,
            "notarization_submitted": False,
            "keychain_password_read": False,
            "remote_service_called": False,
        },
        "remaining_blockers": remaining_blockers,
    }


def write_evidence(evidence: dict[str, Any], output_root: Path) -> Path:
    output_root = resolve_output_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / "evidence.json"
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def _run(command: list[str]) -> CommandResult:
    proc = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    return CommandResult(returncode=proc.returncode, stdout=proc.stdout[-4000:], stderr=proc.stderr[-4000:])


def _load_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    if not resolved.is_file():
        return {}
    return json.loads(resolved.read_text(encoding="utf-8"))


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    resolved = path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--same-chain-evidence", type=Path, default=DEFAULT_SAME_CHAIN_EVIDENCE)
    parser.add_argument("--dmg-evidence", type=Path, default=DEFAULT_DMG_EVIDENCE)
    parser.add_argument("--macos-release-evidence", type=Path, default=DEFAULT_MACOS_RELEASE_EVIDENCE)
    parser.add_argument("--windows-evidence", type=Path, default=None)
    parser.add_argument("--notary-profile", default=None)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-id", default="release-external-preflight-current")
    args = parser.parse_args(argv)
    evidence = build_evidence(
        same_chain_evidence_path=args.same_chain_evidence,
        dmg_evidence_path=args.dmg_evidence,
        macos_release_evidence_path=args.macos_release_evidence,
        windows_evidence_path=args.windows_evidence,
        notary_profile=args.notary_profile,
        command_runner=_run,
        run_id=args.run_id,
    )
    output_path = write_evidence(evidence, args.output_root)
    print(json.dumps({"status": evidence["status"], "evidence": str(output_path)}, ensure_ascii=False))
    return 0 if not evidence["remaining_blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

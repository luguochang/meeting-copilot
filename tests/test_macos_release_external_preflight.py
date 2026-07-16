import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_release_external_preflight.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "macos_release_external_preflight",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_codesign_identities_detects_developer_id_application():
    tool = load_tool_module()

    parsed = tool.parse_codesign_identities(
        """
  1) ABCDEF1234567890ABCDEF1234567890ABCDEF12 "Apple Development: Chase Example (TEAM123456)"
  2) 1234567890ABCDEF1234567890ABCDEF12345678 "Developer ID Application: Chase Example (TEAM123456)"
  3) 9999999999999999999999999999999999999999 "Developer ID Installer: Chase Example (TEAM123456)"
     3 valid identities found
"""
    )

    assert parsed["developer_id_application_count"] == 1
    assert parsed["developer_id_installer_count"] == 1
    assert parsed["development_identity_count"] == 1
    assert parsed["developer_id_application_identities"][0]["team_id"] == "TEAM123456"


def test_release_preflight_blocks_without_external_signing_notary_gatekeeper_and_windows(tmp_path):
    tool = load_tool_module()
    same_chain_path = tmp_path / "same-chain.json"
    dmg_path = tmp_path / "dmg-evidence.json"
    same_chain_path.write_text(
        json.dumps(
            {
                "status": "go_packaged_webview_runtime_probe",
                "counts_as_packaged_same_chain_no_cost_evidence": True,
                "counts_as_packaged_mainline_evidence": True,
                "remaining_blockers": [
                    "developer_id_signing_not_done",
                    "notarization_not_done",
                    "gatekeeper_acceptance_not_done",
                    "windows_real_machine_not_verified",
                ],
            }
        ),
        encoding="utf-8",
    )
    dmg_path.write_text(
        json.dumps(
            {
                "status": "go_development_dmg_not_public_release",
                "counts_as_public_release_package": False,
                "dmg_path": "artifacts/tmp/desktop_dmg_skip_finder_current_20260709/Meeting Copilot.dmg",
                "results": {
                    "spctl_app_exit_code": 3,
                    "spctl_dmg_exit_code": 3,
                    "app_codesign_verify_exit_code": 0,
                    "dmg_mount_smoke_exit_code": 0,
                },
                "remaining_blockers": [
                    "developer_id_codesign_not_done",
                    "notarization_not_done",
                    "gatekeeper_rejects_unsigned_or_adhoc_artifacts",
                    "packaged_screenshot_evidence_still_missing",
                ],
            }
        ),
        encoding="utf-8",
    )

    commands = {
        ("security", "find-identity", "-v", "-p", "codesigning"): tool.CommandResult(
            returncode=0,
            stdout='  1) ABC "Apple Development: Chase Example (TEAM123456)"\n     1 valid identities found\n',
            stderr="",
        ),
        ("xcrun", "--find", "notarytool"): tool.CommandResult(
            returncode=1,
            stdout="",
            stderr="xcrun: error: unable to find utility notarytool",
        ),
    }

    evidence = tool.build_evidence(
        same_chain_evidence_path=same_chain_path,
        dmg_evidence_path=dmg_path,
        macos_release_evidence_path=None,
        windows_evidence_path=None,
        notary_profile=None,
        command_runner=lambda command: commands[tuple(command)],
        run_id="unit",
    )

    assert evidence["status"] == "blocked_external_release_requirements"
    assert evidence["counts_as_packaged_same_chain_no_cost_evidence"] is True
    assert evidence["counts_as_public_release_package"] is False
    assert evidence["developer_id"]["developer_id_application_count"] == 0
    assert evidence["notarization"]["notarytool_available"] is False
    assert evidence["gatekeeper"]["app_accepted"] is False
    assert evidence["gatekeeper"]["dmg_accepted"] is False
    assert evidence["windows"]["status"] == "not_verified"
    assert evidence["remaining_blockers"] == [
        "developer_id_signing_not_done",
        "notarization_not_done",
        "gatekeeper_acceptance_not_done",
        "windows_real_machine_not_verified",
    ]
    assert evidence["visual_evidence"] == {
        "packaged_screenshot_evidence_present": False,
        "packaged_screenshot_requirement_status": "waived_by_internal_dom_runtime_probe",
        "waiver_reason": "packaged DOM/runtime/same-chain evidence is stronger than OS-level screenshot evidence for release preflight",
    }
    assert "packaged_same_chain_realtime_meeting_flow_not_verified" not in evidence["remaining_blockers"]


def test_release_preflight_requires_output_under_artifacts_tmp(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="output_root must be under artifacts/tmp"):
        tool.resolve_output_root(tmp_path / "outside")


def test_release_preflight_keeps_notarization_blocker_until_public_release_runner_go(tmp_path):
    tool = load_tool_module()
    same_chain_path = tmp_path / "same-chain.json"
    dmg_path = tmp_path / "dmg-evidence.json"
    macos_release_path = tmp_path / "macos-release.json"
    same_chain_path.write_text(
        json.dumps(
            {
                "counts_as_packaged_same_chain_no_cost_evidence": True,
                "counts_as_packaged_mainline_evidence": True,
            }
        ),
        encoding="utf-8",
    )
    dmg_path.write_text(
        json.dumps(
            {
                "counts_as_public_release_package": False,
                "results": {
                    "spctl_app_exit_code": 3,
                    "spctl_dmg_exit_code": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    macos_release_path.write_text(
        json.dumps(
            {
                "status": "blocked_public_release_execution_requirements",
                "counts_as_public_release_package": False,
                "remaining_blockers": ["developer_id_signing_not_done"],
                "privacy_safety": {
                    "notarization_submitted": False,
                    "remote_service_called": False,
                },
            }
        ),
        encoding="utf-8",
    )

    commands = {
        ("security", "find-identity", "-v", "-p", "codesigning"): tool.CommandResult(
            returncode=0,
            stdout='  1) ABC "Apple Development: Chase Example (TEAM123456)"\n',
            stderr="",
        ),
        ("xcrun", "--find", "notarytool"): tool.CommandResult(
            returncode=0,
            stdout="/usr/bin/notarytool\n",
            stderr="",
        ),
    }

    evidence = tool.build_evidence(
        same_chain_evidence_path=same_chain_path,
        dmg_evidence_path=dmg_path,
        macos_release_evidence_path=macos_release_path,
        windows_evidence_path=None,
        notary_profile="meeting-copilot-notary",
        command_runner=lambda command: commands[tuple(command)],
        run_id="unit-with-release-runner",
    )

    assert evidence["macos_public_release"]["status"] == "blocked_public_release_execution_requirements"
    assert evidence["notarization"]["notary_profile_provided"] is True
    assert evidence["notarization"]["tooling_ready"] is True
    assert evidence["notarization"]["ready"] is False
    assert evidence["remaining_blockers"] == [
        "developer_id_signing_not_done",
        "notarization_not_done",
        "gatekeeper_acceptance_not_done",
        "windows_real_machine_not_verified",
    ]


def test_release_preflight_uses_public_release_runner_go_as_public_package(tmp_path):
    tool = load_tool_module()
    same_chain_path = tmp_path / "same-chain.json"
    dmg_path = tmp_path / "dmg-evidence.json"
    macos_release_path = tmp_path / "macos-release.json"
    windows_path = tmp_path / "windows.json"
    same_chain_path.write_text(
        json.dumps(
            {
                "counts_as_packaged_same_chain_no_cost_evidence": True,
                "counts_as_packaged_mainline_evidence": True,
            }
        ),
        encoding="utf-8",
    )
    dmg_path.write_text(
        json.dumps(
            {
                "counts_as_public_release_package": False,
                "results": {
                    "spctl_app_exit_code": 3,
                    "spctl_dmg_exit_code": 3,
                },
            }
        ),
        encoding="utf-8",
    )
    macos_release_path.write_text(
        json.dumps(
            {
                "status": "go_public_release_signed_notarized_gatekeeper",
                "counts_as_public_release_package": True,
                "remaining_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    windows_path.write_text(
        json.dumps(
            {
                "status": "go_windows_real_machine_verified",
                "windows_real_machine_verified": True,
                "remaining_blockers": [],
            }
        ),
        encoding="utf-8",
    )

    commands = {
        ("security", "find-identity", "-v", "-p", "codesigning"): tool.CommandResult(
            returncode=0,
            stdout="",
            stderr="",
        ),
        ("xcrun", "--find", "notarytool"): tool.CommandResult(
            returncode=0,
            stdout="/usr/bin/notarytool\n",
            stderr="",
        ),
    }

    evidence = tool.build_evidence(
        same_chain_evidence_path=same_chain_path,
        dmg_evidence_path=dmg_path,
        macos_release_evidence_path=macos_release_path,
        windows_evidence_path=windows_path,
        notary_profile=None,
        command_runner=lambda command: commands[tuple(command)],
        run_id="unit-release-go",
    )

    assert evidence["status"] == "go_public_release_external_preflight"
    assert evidence["remaining_blockers"] == []
    assert evidence["counts_as_public_release_package"] is True
    assert evidence["macos_public_release"]["ready"] is True


def test_release_preflight_rejects_unvalidated_windows_boolean(tmp_path):
    tool = load_tool_module()
    same_chain_path = tmp_path / "same-chain.json"
    dmg_path = tmp_path / "dmg-evidence.json"
    macos_release_path = tmp_path / "macos-release.json"
    windows_path = tmp_path / "windows.json"
    same_chain_path.write_text(
        json.dumps(
            {
                "counts_as_packaged_same_chain_no_cost_evidence": True,
                "counts_as_packaged_mainline_evidence": True,
            }
        ),
        encoding="utf-8",
    )
    dmg_path.write_text(json.dumps({"results": {}}), encoding="utf-8")
    macos_release_path.write_text(
        json.dumps(
            {
                "status": "go_public_release_signed_notarized_gatekeeper",
                "counts_as_public_release_package": True,
                "remaining_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    windows_path.write_text(json.dumps({"windows_real_machine_verified": True}), encoding="utf-8")

    commands = {
        ("security", "find-identity", "-v", "-p", "codesigning"): tool.CommandResult(
            returncode=0,
            stdout="",
            stderr="",
        ),
        ("xcrun", "--find", "notarytool"): tool.CommandResult(
            returncode=0,
            stdout="/usr/bin/notarytool\n",
            stderr="",
        ),
    }

    evidence = tool.build_evidence(
        same_chain_evidence_path=same_chain_path,
        dmg_evidence_path=dmg_path,
        macos_release_evidence_path=macos_release_path,
        windows_evidence_path=windows_path,
        notary_profile=None,
        command_runner=lambda command: commands[tuple(command)],
        run_id="unit-unvalidated-windows",
    )

    assert evidence["windows"]["status"] == "invalid_or_missing_validator_evidence"
    assert evidence["remaining_blockers"] == ["windows_real_machine_not_verified"]

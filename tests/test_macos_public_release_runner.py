import importlib.util
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_public_release_runner.py"
PUBLIC_RELEASE_EVIDENCE_BLOCKERS = [
    "redistribution_provenance_unverified",
    "independent_clean_machine_unverified",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "macos_public_release_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_inputs(tmp_path):
    app = tmp_path / "Meeting Copilot.app"
    app.mkdir()
    script = tmp_path / "bundle_dmg.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    return app, script


def make_strict_codesign_result(app_path, identity, *, entitlements_verified=True):
    app_path = str(app_path)
    signing_command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--options",
        "runtime",
        "--timestamp",
        "--entitlements",
        str(REPO_ROOT / "code/desktop_tauri/src-tauri/Entitlements.plist"),
        app_path,
    ]
    return {
        "signing_plan": {
            "mode": "developer-id",
            "identity": identity,
            "hardened_runtime": True,
            "secure_timestamp": True,
            "uses_deep_signing": False,
            "signing_steps": [{"command": signing_command}],
        },
        "signing": {
            "status": "passed",
            "mode": "developer-id",
            "identity": identity,
            "hardened_runtime": True,
            "uses_deep_signing": False,
            "signed_target_count": 1,
        },
        "verification": {
            "status": "passed",
            "mode": "developer-id",
            "identity": identity,
            "uses_deep_verification": False,
            "results": [
                {
                    "runtime_verified": True,
                    "identity_verified": True,
                    "entitlements_verified": entitlements_verified,
                }
            ],
        },
    }


def test_public_release_runner_blocks_without_developer_id_before_mutating(tmp_path):
    tool = load_tool_module()
    app, script = make_inputs(tmp_path)
    commands = []

    def runner(command):
        commands.append(tuple(command))
        if command[:4] == ["security", "find-identity", "-v", "-p"]:
            return tool.CommandResult(
                returncode=0,
                stdout='  1) ABC "Apple Development: Chase Example (TEAM123456)"\n',
                stderr="",
            )
        if command == ["xcrun", "--find", "notarytool"]:
            return tool.CommandResult(
                returncode=0, stdout="/usr/bin/notarytool\n", stderr=""
            )
        raise AssertionError(
            f"unexpected mutating command before blockers clear: {command}"
        )

    evidence = tool.build_release_evidence(
        app_path=app,
        bundle_dmg_script=script,
        output_root=REPO_ROOT / "artifacts/tmp/unit-public-release-blocked",
        developer_id_application=None,
        notary_profile="meeting-copilot-notary",
        run_id="unit-blocked",
        execute=True,
        command_runner=runner,
    )

    assert evidence["status"] == "apple_distribution_lane_blocked"
    assert evidence["apple_distribution_lane"] == {
        "status": "blocked",
        "result": "not_completed",
        "remaining_blockers": ["developer_id_signing_not_done"],
    }
    assert evidence["remaining_blockers"] == [
        "developer_id_signing_not_done",
        *PUBLIC_RELEASE_EVIDENCE_BLOCKERS,
    ]
    assert evidence["counts_as_public_release_package"] is False
    assert evidence["privacy_safety"] == {
        "secret_values_read": False,
        "keychain_password_read": False,
        "notarization_submitted": False,
        "remote_service_called": False,
    }
    assert evidence["executed_mutating_command_count"] == 0
    assert evidence["strict_codesign"]["status"] == "not_executed"
    assert commands == [
        ("security", "find-identity", "-v", "-p", "codesigning"),
        ("xcrun", "--find", "notarytool"),
    ]


def test_public_release_runner_uses_strict_developer_id_signer_before_notarization(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    app, script = make_inputs(tmp_path)
    commands = []
    strict_calls = []
    identity = "Developer ID Application: Chase Example (TEAM123456)"

    def strict_signer(app_path, *, mode, identity, command_runner):
        strict_calls.append(
            {
                "app_path": app_path,
                "mode": mode,
                "identity": identity,
            }
        )
        strict_result = make_strict_codesign_result(app_path, identity)
        strict_commands = [
            strict_result["signing_plan"]["signing_steps"][0]["command"],
            ["codesign", "--verify", "--strict", "--verbose=2", str(app_path)],
            ["codesign", "--display", "--verbose=4", str(app_path)],
            ["codesign", "--display", "--entitlements", ":-", "--xml", str(app_path)],
        ]
        for command in strict_commands:
            result = command_runner(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
            assert isinstance(result, subprocess.CompletedProcess)
            assert result.returncode == 0
        return strict_result

    monkeypatch.setattr(tool.macos_codesign, "sign_and_verify", strict_signer)

    def runner(command):
        commands.append(tuple(command))
        if command[:4] == ["security", "find-identity", "-v", "-p"]:
            return tool.CommandResult(
                returncode=0,
                stdout=f'  1) ABCDEF "{identity}"\n',
                stderr="",
            )
        if command == ["xcrun", "--find", "notarytool"]:
            return tool.CommandResult(
                returncode=0, stdout="/usr/bin/notarytool\n", stderr=""
            )
        return tool.CommandResult(returncode=0, stdout="ok\n", stderr="")

    evidence = tool.build_release_evidence(
        app_path=app,
        bundle_dmg_script=script,
        output_root=REPO_ROOT / "artifacts/tmp/unit-public-release-ready",
        developer_id_application=identity,
        notary_profile="meeting-copilot-notary",
        run_id="unit-ready",
        execute=True,
        command_runner=runner,
    )

    assert evidence["status"] == "apple_distribution_lane_passed_public_release_blocked"
    assert evidence["apple_distribution_lane"] == {
        "status": "passed",
        "result": "signed_notarized_stapled_gatekeeper_accepted",
        "remaining_blockers": [],
    }
    assert evidence["remaining_blockers"] == PUBLIC_RELEASE_EVIDENCE_BLOCKERS
    assert evidence["counts_as_public_release_package"] is False
    assert evidence["privacy_safety"]["notarization_submitted"] is True
    assert evidence["executed_mutating_command_count"] == 11
    assert evidence["strict_codesign"] == {
        "contract": "tools/macos_codesign.py::sign_and_verify",
        "status": "passed",
        "mode": "developer-id",
        "hardened_runtime": True,
        "secure_timestamp": True,
        "uses_deep_signing": False,
        "uses_deep_verification": False,
        "signed_target_count": 1,
        "verified_target_count": 1,
        "runtime_verified": True,
        "identity_verified": True,
        "entitlements_verified": True,
    }
    assert strict_calls == [
        {
            "app_path": REPO_ROOT
            / "artifacts/tmp/unit-public-release-ready/source/Meeting Copilot.app",
            "mode": "developer-id",
            "identity": identity,
        }
    ]

    command_names = [command[0] for command in commands]
    assert command_names == [
        "security",
        "xcrun",
        "ditto",
        "codesign",
        "codesign",
        "codesign",
        "codesign",
        str(script),
        "codesign",
        "xcrun",
        "xcrun",
        "spctl",
        "spctl",
    ]
    assert all("--deep" not in command for command in commands)
    assert commands[3][1:] == (
        "--force",
        "--sign",
        identity,
        "--options",
        "runtime",
        "--timestamp",
        "--entitlements",
        str(REPO_ROOT / "code/desktop_tauri/src-tauri/Entitlements.plist"),
        str(strict_calls[0]["app_path"]),
    )
    assert commands[4][1:4] == ("--verify", "--strict", "--verbose=2")
    assert commands[6][1:5] == ("--display", "--entitlements", ":-", "--xml")
    assert commands[9][:3] == ("xcrun", "notarytool", "submit")
    assert Path(commands[9][3]).name == Path(evidence["dmg_path"]).name
    assert commands[10][:3] == ("xcrun", "stapler", "staple")


def test_public_release_runner_has_no_deep_fallback_when_strict_verification_fails(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    app, script = make_inputs(tmp_path)
    commands = []
    identity = "Developer ID Application: Chase Example (TEAM123456)"

    def strict_signer(app_path, *, mode, identity, command_runner):
        assert mode == "developer-id"
        assert command_runner
        return make_strict_codesign_result(
            app_path,
            identity,
            entitlements_verified=False,
        )

    monkeypatch.setattr(tool.macos_codesign, "sign_and_verify", strict_signer)

    def runner(command):
        commands.append(tuple(command))
        if command[:4] == ["security", "find-identity", "-v", "-p"]:
            return tool.CommandResult(
                returncode=0,
                stdout=f'  1) ABCDEF "{identity}"\n',
                stderr="",
            )
        if command == ["xcrun", "--find", "notarytool"]:
            return tool.CommandResult(
                returncode=0, stdout="/usr/bin/notarytool\n", stderr=""
            )
        if command[0] == "ditto":
            return tool.CommandResult(returncode=0, stdout="ok\n", stderr="")
        raise AssertionError(
            f"strict signer failure must stop the release sequence: {command}"
        )

    evidence = tool.build_release_evidence(
        app_path=app,
        bundle_dmg_script=script,
        output_root=REPO_ROOT / "artifacts/tmp/unit-public-release-strict-failed",
        developer_id_application=identity,
        notary_profile="meeting-copilot-notary",
        run_id="unit-strict-failed",
        execute=True,
        command_runner=runner,
    )

    assert evidence["status"] == "apple_distribution_lane_blocked"
    assert evidence["apple_distribution_lane"] == {
        "status": "blocked",
        "result": "not_completed",
        "remaining_blockers": ["developer_id_signing_failed"],
    }
    assert evidence["remaining_blockers"] == [
        "developer_id_signing_failed",
        *PUBLIC_RELEASE_EVIDENCE_BLOCKERS,
    ]
    assert evidence["counts_as_public_release_package"] is False
    assert evidence["strict_codesign"]["status"] == "failed"
    assert evidence["strict_codesign"]["entitlements_verified"] is False
    assert evidence["privacy_safety"]["notarization_submitted"] is False
    assert evidence["executed_mutating_command_count"] == 1
    assert all("--deep" not in command for command in commands)
    runner_source = TOOL_PATH.read_text(encoding="utf-8")
    assert '"--force", "--deep"' not in runner_source
    assert '"--verify", "--deep"' not in runner_source
    assert commands == [
        ("security", "find-identity", "-v", "-p", "codesigning"),
        ("xcrun", "--find", "notarytool"),
        (
            "ditto",
            str(app),
            str(
                REPO_ROOT
                / "artifacts/tmp/unit-public-release-strict-failed/source/Meeting Copilot.app"
            ),
        ),
    ]


def test_execute_requires_explicit_current_inputs_before_any_command(
    monkeypatch,
    capsys,
):
    tool = load_tool_module()

    def forbidden_runner(command):
        raise AssertionError(f"execute input failure must precede commands: {command}")

    monkeypatch.setattr(tool, "_run", forbidden_runner)

    with pytest.raises(SystemExit) as exc_info:
        tool.main(["--execute"])

    assert exc_info.value.code == 2
    assert (
        "--execute requires explicit current inputs: --app, --bundle-dmg-script"
        in capsys.readouterr().err
    )
    runner_source = TOOL_PATH.read_text(encoding="utf-8")
    assert "desktop_tauri_target/release/bundle" not in runner_source


def test_execute_rejects_stale_inputs_before_preflight_or_mutation(tmp_path):
    tool = load_tool_module()
    commands = []

    def forbidden_runner(command):
        commands.append(tuple(command))
        raise AssertionError(f"stale inputs must fail before commands: {command}")

    evidence = tool.build_release_evidence(
        app_path=tmp_path / "stale/Meeting Copilot.app",
        bundle_dmg_script=tmp_path / "stale/bundle_dmg.sh",
        output_root=REPO_ROOT / "artifacts/tmp/unit-public-release-stale-inputs",
        developer_id_application="Developer ID Application: Example (TEAM123456)",
        notary_profile="meeting-copilot-notary",
        run_id="unit-stale-inputs",
        execute=True,
        command_runner=forbidden_runner,
    )

    assert commands == []
    assert evidence["preflight_commands"] == []
    assert evidence["mutating_commands"] == []
    assert evidence["executed_mutating_command_count"] == 0
    assert evidence["execution_inputs"] == {
        "app_provided": True,
        "bundle_dmg_script_provided": True,
        "validated_for_execute": False,
        "validation_blockers": [
            "release_app_input_not_found",
            "bundle_dmg_script_input_not_found",
        ],
    }
    assert evidence["apple_distribution_lane"]["remaining_blockers"] == [
        "release_app_input_not_found",
        "bundle_dmg_script_input_not_found",
    ]
    assert evidence["remaining_blockers"] == [
        "release_app_input_not_found",
        "bundle_dmg_script_input_not_found",
        *PUBLIC_RELEASE_EVIDENCE_BLOCKERS,
    ]
    assert evidence["counts_as_public_release_package"] is False


def test_public_release_runner_requires_output_under_artifacts_tmp(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="output_root must be under artifacts/tmp"):
        tool.resolve_output_root(tmp_path / "outside")

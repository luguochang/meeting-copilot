import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_public_release_runner.py"


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
    return app, script


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
            return tool.CommandResult(returncode=0, stdout="/usr/bin/notarytool\n", stderr="")
        raise AssertionError(f"unexpected mutating command before blockers clear: {command}")

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

    assert evidence["status"] == "blocked_public_release_execution_requirements"
    assert evidence["remaining_blockers"] == ["developer_id_signing_not_done"]
    assert evidence["counts_as_public_release_package"] is False
    assert evidence["privacy_safety"] == {
        "secret_values_read": False,
        "keychain_password_read": False,
        "notarization_submitted": False,
        "remote_service_called": False,
    }
    assert evidence["executed_mutating_command_count"] == 0
    assert commands == [
        ("security", "find-identity", "-v", "-p", "codesigning"),
        ("xcrun", "--find", "notarytool"),
    ]


def test_public_release_runner_executes_sign_notarize_gatekeeper_sequence(tmp_path):
    tool = load_tool_module()
    app, script = make_inputs(tmp_path)
    commands = []

    def runner(command):
        commands.append(tuple(command))
        if command[:4] == ["security", "find-identity", "-v", "-p"]:
            return tool.CommandResult(
                returncode=0,
                stdout='  1) ABCDEF "Developer ID Application: Chase Example (TEAM123456)"\n',
                stderr="",
            )
        if command == ["xcrun", "--find", "notarytool"]:
            return tool.CommandResult(returncode=0, stdout="/usr/bin/notarytool\n", stderr="")
        return tool.CommandResult(returncode=0, stdout="ok\n", stderr="")

    evidence = tool.build_release_evidence(
        app_path=app,
        bundle_dmg_script=script,
        output_root=REPO_ROOT / "artifacts/tmp/unit-public-release-ready",
        developer_id_application="Developer ID Application: Chase Example (TEAM123456)",
        notary_profile="meeting-copilot-notary",
        run_id="unit-ready",
        execute=True,
        command_runner=runner,
    )

    assert evidence["status"] == "go_public_release_signed_notarized_gatekeeper"
    assert evidence["remaining_blockers"] == []
    assert evidence["counts_as_public_release_package"] is True
    assert evidence["privacy_safety"]["notarization_submitted"] is True
    assert evidence["executed_mutating_command_count"] == 9

    command_names = [command[0] for command in commands]
    assert command_names == [
        "security",
        "xcrun",
        "ditto",
        "codesign",
        "codesign",
        str(script),
        "codesign",
        "xcrun",
        "xcrun",
        "spctl",
        "spctl",
    ]
    assert commands[3][1:8] == (
        "--force",
        "--deep",
        "--options",
        "runtime",
        "--timestamp",
        "--sign",
        "Developer ID Application: Chase Example (TEAM123456)",
    )
    assert commands[7][:3] == ("xcrun", "notarytool", "submit")
    assert Path(commands[7][3]).name == Path(evidence["dmg_path"]).name
    assert commands[8][:3] == ("xcrun", "stapler", "staple")


def test_public_release_runner_requires_output_under_artifacts_tmp(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="output_root must be under artifacts/tmp"):
        tool.resolve_output_root(tmp_path / "outside")

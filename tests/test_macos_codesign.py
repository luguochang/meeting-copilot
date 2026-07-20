import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_codesign.py"
MAIN_ENTITLEMENTS = REPO_ROOT / "code/desktop_tauri/src-tauri/Entitlements.plist"
NATIVE_MIC_ENTITLEMENTS = REPO_ROOT / "code/desktop_tauri/native_mic/Entitlements.plist"
TAURI_CONFIG = REPO_ROOT / "code/desktop_tauri/src-tauri/tauri.conf.json"

MACHO_MAGICS = {
    "MH_MAGIC": bytes.fromhex("ce fa ed fe"),
    "MH_CIGAM": bytes.fromhex("fe ed fa ce"),
    "MH_MAGIC_64": bytes.fromhex("cf fa ed fe"),
    "MH_CIGAM_64": bytes.fromhex("fe ed fa cf"),
    "FAT_MAGIC": bytes.fromhex("ca fe ba be"),
    "FAT_CIGAM": bytes.fromhex("be ba fe ca"),
    "FAT_MAGIC_64": bytes.fromhex("ca fe ba bf"),
    "FAT_CIGAM_64": bytes.fromhex("bf ba fe ca"),
}


def load_tool_module():
    spec = importlib.util.spec_from_file_location("macos_codesign", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def write_macho(path: Path, magic: bytes = MACHO_MAGICS["MH_MAGIC_64"]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(magic + b"\x00" * 28)


def write_entitlements(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(payload, sort_keys=True))


def make_app(tmp_path: Path) -> tuple[Path, Path, Path]:
    app = tmp_path / "Meeting Copilot.app"
    write_macho(app / "Contents/MacOS/Meeting Copilot")
    write_macho(app / "Contents/Frameworks/libnative.dylib")
    write_macho(app / "Contents/Resources/runtime/python/lib/extensions/native.so")
    write_macho(app / "Contents/Resources/runtime/python/bin/python3")
    write_macho(
        app
        / "Contents/Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-mic"
    )
    write_macho(app / "Contents/Helpers/diagnostic-helper")
    script = app / "Contents/Resources/runtime/bin/plain-script"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)

    main_entitlements = tmp_path / "main.plist"
    native_mic_entitlements = tmp_path / "native-mic.plist"
    minimal = {"com.apple.security.device.audio-input": True}
    write_entitlements(main_entitlements, minimal)
    write_entitlements(native_mic_entitlements, minimal)
    return app, main_entitlements, native_mic_entitlements


def test_macho_inventory_recognizes_thin_and_fat_magic_and_excludes_ordinary_files(tmp_path):
    tool = load_tool_module()
    app = tmp_path / "Magic.app"
    expected_paths = []
    for index, (magic_name, magic) in enumerate(MACHO_MAGICS.items()):
        path = app / f"Contents/Resources/runtime/item-{index}"
        write_macho(path, magic)
        expected_paths.append(path)
        assert tool.macho_magic_name(path) == magic_name

    ordinary = app / "Contents/Resources/runtime/executable-script"
    ordinary.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    ordinary.chmod(0o755)
    short_file = app / "Contents/Resources/runtime/short"
    short_file.write_bytes(b"abc")
    symlink = app / "Contents/Resources/runtime/python-link"
    symlink.symlink_to(expected_paths[0].name)

    assert tool.macho_magic_name(ordinary) is None
    assert tool.macho_magic_name(short_file) is None
    assert tool.macho_magic_name(symlink) is None
    assert tool.enumerate_macho_files(app) == sorted(expected_paths, key=lambda path: path.as_posix())


def test_ad_hoc_plan_is_inside_out_runtime_hardened_auditable_and_never_deep(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)

    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    assert plan["schema_version"] == "meeting_copilot.macos_codesign_plan.v1"
    assert plan["mode"] == "ad-hoc"
    assert plan["identity"] == "-"
    assert plan["hardened_runtime"] is True
    assert plan["uses_deep_signing"] is False
    assert len(plan["macho_inventory"]) == 6
    assert all(item["magic"] == "MH_MAGIC_64" for item in plan["macho_inventory"])
    assert all("plain-script" not in item["relative_path"] for item in plan["macho_inventory"])

    steps = plan["signing_steps"]
    assert len(steps) == 6
    assert steps[-1]["role"] == "main-app"
    assert steps[-1]["relative_path"] == "."
    assert all(step["role"] != "main-executable" for step in steps)
    assert plan["main_executable_relative_path"] == "Contents/MacOS/Meeting Copilot"
    assert [step["order"] for step in steps] == list(range(1, len(steps) + 1))
    assert all("--deep" not in step["command"] for step in steps)
    assert all("--options" in step["command"] for step in steps)
    assert all(step["command"][step["command"].index("--options") + 1] == "runtime" for step in steps)

    entitled = [step for step in steps if step["entitlements"] is not None]
    assert [(step["role"], Path(step["entitlements"]).name) for step in entitled] == [
        ("native-mic", "native-mic.plist"),
        ("main-app", "main.plist"),
    ]
    assert all(step["entitlements"] is None for step in steps if step["role"] == "nested-code")
    assert json.loads(json.dumps(plan, sort_keys=True)) == plan
    assert tool.validate_signing_plan(plan) == plan


def test_developer_id_plan_requires_application_identity_and_keeps_runtime_option(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    identity = "Developer ID Application: Example Corp (TEAM123456)"

    plan = tool.build_signing_plan(
        app,
        mode="developer-id",
        identity=identity,
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    assert plan["mode"] == "developer-id"
    assert plan["identity"] == identity
    assert all(identity in step["command"] for step in plan["signing_steps"])
    assert all("runtime" in step["command"] for step in plan["signing_steps"])

    with pytest.raises(ValueError, match="Developer ID Application"):
        tool.build_signing_plan(
            app,
            mode="developer-id",
            identity="Apple Development: Example Corp (TEAM123456)",
            main_entitlements=main_entitlements,
            native_mic_entitlements=native_mic_entitlements,
        )
    with pytest.raises(ValueError, match="identity"):
        tool.build_signing_plan(
            app,
            mode="ad-hoc",
            identity=identity,
            main_entitlements=main_entitlements,
            native_mic_entitlements=native_mic_entitlements,
        )


@pytest.mark.parametrize(
    "entitlement",
    [
        "com.apple.security.get-task-allow",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.app-sandbox",
        "com.example.unknown-entitlement",
    ],
)
def test_entitlement_policy_rejects_debug_broad_sandbox_and_unknown_keys(tmp_path, entitlement):
    tool = load_tool_module()
    path = tmp_path / "unsafe.plist"
    write_entitlements(path, {entitlement: True})

    with pytest.raises(ValueError, match="entitlement"):
        tool.validate_entitlements(path, target_role="main-app")


def test_only_main_app_and_native_mic_may_receive_exact_audio_input_entitlement(tmp_path):
    tool = load_tool_module()
    minimal = {"com.apple.security.device.audio-input": True}
    path = tmp_path / "minimal.plist"
    write_entitlements(path, minimal)

    assert tool.validate_entitlements(path, target_role="main-app") == minimal
    assert tool.validate_entitlements(path, target_role="native-mic") == minimal
    with pytest.raises(ValueError, match="must not use entitlements"):
        tool.validate_entitlements(path, target_role="nested-code")

    write_entitlements(path, {"com.apple.security.device.audio-input": False})
    with pytest.raises(ValueError, match="exactly"):
        tool.validate_entitlements(path, target_role="main-app")
    write_entitlements(path, {})
    with pytest.raises(ValueError, match="exactly"):
        tool.validate_entitlements(path, target_role="native-mic")


def test_native_mic_entitlement_requires_the_fixed_packaged_helper_path(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    lookalike = app / "Contents/Frameworks/meeting-copilot-native-mic"
    write_macho(lookalike)

    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    native_steps = [step for step in plan["signing_steps"] if step["role"] == "native-mic"]
    assert [step["relative_path"] for step in native_steps] == [
        "Contents/Resources/MeetingCopilotRuntime.bundle/bin/meeting-copilot-native-mic"
    ]
    lookalike_step = next(
        step for step in plan["signing_steps"] if step["target"] == str(lookalike)
    )
    assert lookalike_step["role"] == "nested-code"
    assert lookalike_step["entitlements"] is None


def test_plan_validator_detects_entitlement_file_changes_after_planning(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    write_entitlements(
        main_entitlements,
        {"com.apple.security.cs.disable-library-validation": True},
    )

    with pytest.raises(ValueError, match="changed after planning"):
        tool.validate_signing_plan(plan)


def test_plan_validator_detects_macho_inventory_changes_after_planning(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    write_macho(app / "Contents/Resources/runtime/late-extension.so")

    with pytest.raises(ValueError, match="Mach-O inventory changed after planning"):
        tool.validate_signing_plan(plan)


def test_plan_validator_rejects_target_escape_and_native_mic_role_spoofing(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    escaped = json.loads(json.dumps(plan))
    escaped_step = next(step for step in escaped["signing_steps"] if step["role"] == "nested-code")
    escaped_step["target"] = "/tmp/outside-app.dylib"
    escaped_step["command"][-1] = escaped_step["target"]
    with pytest.raises(ValueError, match="inside app_path"):
        tool.validate_signing_plan(escaped)

    spoofed = json.loads(json.dumps(plan))
    native_step = next(step for step in spoofed["signing_steps"] if step["role"] == "native-mic")
    spoofed_step = next(step for step in spoofed["signing_steps"] if step["role"] == "nested-code")
    spoofed_step["role"] = "native-mic"
    spoofed_step["entitlements"] = native_step["entitlements"]
    spoofed_step["entitlements_payload"] = native_step["entitlements_payload"]
    spoofed_step["entitlements_sha256"] = native_step["entitlements_sha256"]
    spoofed_step["command"][-1:-1] = ["--entitlements", native_step["entitlements"]]
    with pytest.raises(ValueError, match="fixed packaged path"):
        tool.validate_signing_plan(spoofed)


def test_plan_validator_rejects_deep_missing_runtime_and_app_not_last(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    deep_plan = json.loads(json.dumps(plan))
    deep_plan["signing_steps"][0]["command"].insert(1, "--deep")
    with pytest.raises(ValueError, match="--deep"):
        tool.validate_signing_plan(deep_plan)

    no_runtime_plan = json.loads(json.dumps(plan))
    command = no_runtime_plan["signing_steps"][0]["command"]
    runtime_index = command.index("--options")
    del command[runtime_index : runtime_index + 2]
    with pytest.raises(ValueError, match="runtime"):
        tool.validate_signing_plan(no_runtime_plan)

    wrong_order_plan = json.loads(json.dumps(plan))
    wrong_order_plan["signing_steps"][-1], wrong_order_plan["signing_steps"][0] = (
        wrong_order_plan["signing_steps"][0],
        wrong_order_plan["signing_steps"][-1],
    )
    with pytest.raises(ValueError, match="last"):
        tool.validate_signing_plan(wrong_order_plan)


def test_verification_plan_is_strict_inside_out_and_never_uses_deep(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    verification = tool.build_verification_plan(signing_plan)

    assert verification["schema_version"] == "meeting_copilot.macos_codesign_verification_plan.v1"
    assert verification["mode"] == "ad-hoc"
    assert verification["uses_deep_verification"] is False
    assert verification["verification_steps"][-1]["role"] == "main-app"
    for step in verification["verification_steps"]:
        assert step["command"][:3] == ["codesign", "--verify", "--strict"]
        assert "--deep" not in step["command"]


def test_execute_signing_plan_runs_every_target_inside_out_without_deep(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    calls = []

    def command_runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = tool.execute_signing_plan(signing_plan, command_runner=command_runner)

    assert [command for command, _kwargs in calls] == [
        step["command"] for step in signing_plan["signing_steps"]
    ]
    assert all("--deep" not in command for command, _kwargs in calls)
    assert all(
        command[command.index("--options") + 1] == "runtime"
        for command, _kwargs in calls
    )
    assert all(
        kwargs == {"capture_output": True, "text": True, "check": False}
        for _command, kwargs in calls
    )
    assert result["status"] == "passed"
    assert result["signed_target_count"] == len(signing_plan["signing_steps"])
    assert result["uses_deep_signing"] is False
    assert result["hardened_runtime"] is True


def test_execute_signing_plan_fails_closed_on_the_first_failed_target(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    calls = []

    def command_runner(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            1 if len(calls) == 2 else 0,
            stdout="",
            stderr="signing failed",
        )

    with pytest.raises(RuntimeError, match="codesign signing failed.*signing failed"):
        tool.execute_signing_plan(signing_plan, command_runner=command_runner)

    assert calls == [
        signing_plan["signing_steps"][0]["command"],
        signing_plan["signing_steps"][1]["command"],
    ]


def test_sign_and_verify_signs_all_targets_before_strict_verification(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    expected_entitlements = {
        step["target"]: step["entitlements_payload"] or {}
        for step in signing_plan["signing_steps"]
    }
    expected_entitlements[
        str(app / signing_plan["main_executable_relative_path"])
    ] = expected_entitlements[str(app)]
    calls = []

    def command_runner(command, **kwargs):
        calls.append((command, kwargs))
        target = command[-1]
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "--display" in command and "--entitlements" in command:
            plist = plistlib.dumps(expected_entitlements[target]).decode("utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=plist, stderr="")
        if "--display" in command:
            metadata = "CodeDirectory flags=0x10002(adhoc,runtime)\nSignature=adhoc\n"
            return subprocess.CompletedProcess(command, 0, stdout="", stderr=metadata)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    result = tool.sign_and_verify(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
        command_runner=command_runner,
    )

    signing_command_count = len(signing_plan["signing_steps"])
    assert [command for command, _kwargs in calls[:signing_command_count]] == [
        step["command"] for step in signing_plan["signing_steps"]
    ]
    assert all("--verify" not in command for command, _kwargs in calls[:signing_command_count])
    assert calls[signing_command_count][0][:3] == ["codesign", "--verify", "--strict"]
    assert result["signing_plan"] == signing_plan
    assert result["signing"]["status"] == "passed"
    assert result["verification"]["status"] == "passed"


def test_signed_app_verifier_checks_runtime_identity_and_entitlements_per_target(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )
    expected_entitlements = {
        step["target"]: step["entitlements_payload"] or {}
        for step in signing_plan["signing_steps"]
    }
    expected_entitlements[
        str(app / signing_plan["main_executable_relative_path"])
    ] = expected_entitlements[str(app)]
    calls = []

    def command_runner(command, **kwargs):
        calls.append((command, kwargs))
        target = command[-1]
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "--entitlements" in command:
            plist = plistlib.dumps(expected_entitlements[target]).decode("utf-8")
            return subprocess.CompletedProcess(command, 0, stdout=plist, stderr="")
        metadata = "CodeDirectory flags=0x10002(adhoc,runtime)\nSignature=adhoc\n"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr=metadata)

    result = tool.verify_signed_app(signing_plan, command_runner=command_runner)

    assert result["status"] == "passed"
    assert len(result["results"]) == len(signing_plan["signing_steps"])
    assert all("--deep" not in command for command, _kwargs in calls)
    assert all(
        kwargs == {"capture_output": True, "text": True, "check": False}
        for _command, kwargs in calls
    )


def test_signed_app_verifier_reads_runtime_only_from_codesign_flags(tmp_path):
    tool = load_tool_module()
    app, main_entitlements, native_mic_entitlements = make_app(tmp_path)
    signing_plan = tool.build_signing_plan(
        app,
        mode="ad-hoc",
        main_entitlements=main_entitlements,
        native_mic_entitlements=native_mic_entitlements,
    )

    def command_runner(command, **_kwargs):
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        metadata = f"Executable={command[-1]}\nSignature=adhoc\n"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr=metadata)

    with pytest.raises(RuntimeError, match="hardened runtime flag missing"):
        tool.verify_signed_app(signing_plan, command_runner=command_runner)


def test_repository_entitlements_are_minimal_and_tauri_binds_only_the_main_app_plist():
    minimal = {"com.apple.security.device.audio-input": True}
    assert plistlib.loads(MAIN_ENTITLEMENTS.read_bytes()) == minimal
    assert plistlib.loads(NATIVE_MIC_ENTITLEMENTS.read_bytes()) == minimal

    config = json.loads(TAURI_CONFIG.read_text(encoding="utf-8"))
    assert config["bundle"]["macOS"]["entitlements"] == "Entitlements.plist"
    assert "native_mic" not in json.dumps(config)

import importlib.util
from pathlib import Path
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "macos_dmg_install_smoke.py"
if str(TOOL_PATH.parent) not in sys.path:
    sys.path.insert(0, str(TOOL_PATH.parent))


def load_tool_module():
    spec = importlib.util.spec_from_file_location("macos_dmg_install_smoke", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_attach_plist_returns_mount_point_and_device():
    tool = load_tool_module()

    raw = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>system-entities</key><array>
<dict><key>dev-entry</key><string>/dev/disk9s1</string></dict>
<dict><key>mount-point</key><string>/Volumes/Meeting Copilot</string><key>dev-entry</key><string>/dev/disk9s1</string></dict>
</array></dict></plist>"""

    assert tool.parse_attach_plist(raw) == (Path("/Volumes/Meeting Copilot"), "/dev/disk9s1")


def test_direct_dmg_and_attach_commands_cannot_use_finder_or_privileged_tools(tmp_path):
    tool = load_tool_module()

    create = tool.build_direct_hdiutil_command(
        output_dmg=tmp_path / "Meeting Copilot.dmg",
        source_dir=tmp_path / "source",
        volume_name="Meeting Copilot",
    )
    attach = tool.build_attach_command(tmp_path / "Meeting Copilot.dmg")
    all_commands = " ".join(create + attach)

    assert create[:2] == ["hdiutil", "create"]
    assert "-srcfolder" in create
    assert "osascript" not in all_commands
    assert "open" not in all_commands
    assert "sudo" not in all_commands
    assert "security" not in all_commands


def test_detach_command_has_stable_force_argument_layout(tmp_path):
    tool = load_tool_module()
    mount_dir = Path("/Volumes/Meeting Copilot")

    assert tool.build_detach_command(mount_dir) == [
        "hdiutil",
        "detach",
        "/Volumes/Meeting Copilot",
    ]
    assert tool.build_detach_command(mount_dir, force=True) == [
        "hdiutil",
        "detach",
        "-force",
        "/Volumes/Meeting Copilot",
    ]


def test_smoke_passed_fails_closed_on_install_and_runtime_gates(tmp_path):
    tool = load_tool_module()
    dmg = tmp_path / "smoke.dmg"
    dmg.write_bytes(b"dmg")
    base = {
        "cleanup_errors": [],
        "resolved_dmg": dmg,
        "command_results": {
            "verify_source_app_codesign": {"returncode": 0},
            "verify_mounted_app_codesign": {"returncode": 0},
            "verify_installed_app_codesign": {"returncode": 0},
        },
        "applications_link_present": True,
        "mount_detached": True,
        "installed_removed": True,
        "backend": {"pid": 123, "port": 54321},
        "responses": {
            "health": 200,
            "bootstrap": 303,
            "workbench": 200,
            "providers": 200,
            "asr_runtime": 200,
        },
        "probe": {
            "health_identity_verified": True,
            "bootstrap_authenticated": True,
            "resident_ready": True,
            "workbench_loopback": True,
            "workbench_body_bytes": 1,
        },
        "app_exited": True,
        "backend_exited": True,
        "port_closed": True,
        "funasr_exited": True,
        "funasr_forced_cleanup": False,
    }

    assert tool.smoke_passed(**base) is True
    for gate in (
        "applications_link_present",
        "bootstrap_authenticated",
        "resident_ready",
        "funasr_exited",
    ):
        candidate = dict(base)
        candidate["probe"] = dict(base["probe"])
        if gate == "applications_link_present":
            candidate[gate] = False
        elif gate in candidate["probe"]:
            candidate["probe"][gate] = False
        else:
            candidate[gate] = False
        assert tool.smoke_passed(**candidate) is False, gate

    forced = dict(base)
    forced["funasr_forced_cleanup"] = True
    assert tool.smoke_passed(**forced) is False


def test_sanitized_environment_isolated_and_does_not_inherit_provider_secrets(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-pass")
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", "old-token")
    monkeypatch.setenv("PATH", "/usr/bin")

    environment = tool.sanitized_environment(tmp_path / "home", "smoke-token")

    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE"] == "smoke-token"
    assert "OPENAI_API_KEY" not in environment
    assert "MEETING_COPILOT_LOCAL_API_TOKEN" not in environment
    assert environment["PATH"] == "/usr/bin"


def test_output_root_and_run_id_are_bounded(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    (repo_root / "artifacts/tmp").mkdir(parents=True)

    assert tool.resolve_output_root(repo_root, Path("artifacts/tmp/install")) == (
        repo_root / "artifacts/tmp/install"
    ).resolve()
    with pytest.raises(ValueError, match="artifacts/tmp"):
        tool.resolve_output_root(repo_root, tmp_path / "outside")
    with pytest.raises(ValueError, match="run_id"):
        tool.validate_run_id("../escape")

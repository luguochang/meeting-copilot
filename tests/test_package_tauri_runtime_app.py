import importlib.util
import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "package_tauri_runtime_app.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("package_tauri_runtime_app", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlay_maps_runtime_to_stable_app_resources_path(tmp_path):
    tool = load_tool_module()
    bundle = tmp_path / "MeetingCopilotRuntime.bundle"
    overlay_path = tmp_path / "overlay.json"

    overlay = tool.build_overlay(bundle, overlay_path)

    assert overlay["bundle"]["resources"][str(bundle.resolve())] == "MeetingCopilotRuntime.bundle"
    assert json.loads(overlay_path.read_text(encoding="utf-8")) == overlay


def test_validate_runtime_bundle_fails_closed_for_missing_inventory(tmp_path):
    tool = load_tool_module()

    with pytest.raises(ValueError, match="runtime bundle missing required files"):
        tool.validate_runtime_bundle(tmp_path / "missing")


def test_build_command_uses_app_bundle_and_explicit_target_dir(tmp_path):
    tool = load_tool_module()
    command = tool.build_command(overlay_path=tmp_path / "overlay.json", rust_target_dir=tmp_path / "target")

    assert command[:4] == ["cargo-tauri", "build", "--bundles", "app"]
    assert "--no-sign" in command
    assert "--locked" in command
    assert str(tmp_path / "overlay.json") in command
    assert str(tmp_path / "target") in command


def test_runtime_package_is_not_public_release_evidence():
    tool = load_tool_module()
    assert tool.RUN_ID_PATTERN.fullmatch("packaged-runtime-20260716")
    with pytest.raises(ValueError):
        tool.validate_run_id("../escape")

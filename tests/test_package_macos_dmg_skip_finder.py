import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "package_macos_dmg_skip_finder.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "package_macos_dmg_skip_finder",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_mount_dir_handles_volume_names_with_spaces():
    tool = load_tool_module()

    output = """预计CRC32 $4132D7F0
/dev/disk5          \tGUID_partition_scheme          \t
/dev/disk5s1        \tApple_HFS                      \t/Volumes/Meeting Copilot
"""

    assert tool.parse_mount_dir(output) == Path("/Volumes/Meeting Copilot")


def test_bundle_command_uses_skip_jenkins_and_no_internet_enable(tmp_path):
    tool = load_tool_module()

    command = tool.build_bundle_command(
        script=tmp_path / "bundle_dmg.sh",
        output_dmg=tmp_path / "Meeting Copilot.dmg",
        source_dir=tmp_path / "source",
        volume_name="Meeting Copilot",
    )

    assert command[:2] == [str(tmp_path / "bundle_dmg.sh"), "--skip-jenkins"]
    assert "--no-internet-enable" in command
    assert "/usr/bin/osascript" not in " ".join(command)
    assert str(tmp_path / "Meeting Copilot.dmg") in command
    assert str(tmp_path / "source") in command


def test_resolve_output_root_requires_artifacts_tmp_for_safety(tmp_path):
    tool = load_tool_module()

    safe_root = tool.resolve_output_root(Path("artifacts/tmp/desktop_dmg_ci"))
    assert safe_root == (tool.REPO_ROOT / "artifacts/tmp/desktop_dmg_ci").resolve()

    with pytest.raises(ValueError, match="output_root must be under artifacts/tmp"):
        tool.resolve_output_root(tmp_path / "outside")


def test_remaining_blockers_do_not_claim_packaged_dom_missing_after_runtime_probe_route():
    tool = load_tool_module()

    blockers = tool.remaining_release_blockers()

    assert "packaged_app_dom_or_screenshot_mainline_evidence_still_missing" not in blockers
    assert "packaged_screenshot_evidence_still_missing" in blockers
    assert "packaged_same_chain_realtime_meeting_flow_not_verified" not in blockers
    assert "developer_id_codesign_not_done" in blockers

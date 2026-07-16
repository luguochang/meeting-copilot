import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "packaged_native_mic_smoke.py"


def load_tool_module():
    sys.path.insert(0, str(TOOL_PATH.parent))
    spec = importlib.util.spec_from_file_location("packaged_native_mic_smoke", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(TOOL_PATH.parent))
    return module


def test_native_helper_command_keeps_cookie_out_of_process_arguments(tmp_path):
    tool = load_tool_module()
    command = tool.native_helper_command(
        helper=tmp_path / "meeting-copilot-native-mic",
        ws_url="ws://127.0.0.1:54321/live/asr/stream/ws/meeting_1",
        meeting_id="meeting_1",
        ready_file=tmp_path / "ready.json",
        duration_seconds=30.0,
    )

    assert "--cookie" not in command
    assert "meeting_1" in command
    assert "30.0" in command


def test_native_helper_environment_is_minimal_and_child_scoped(tmp_path):
    tool = load_tool_module()
    environment = tool.native_helper_environment(
        home=tmp_path / "home",
        cookie="meeting_copilot_session=redacted-test-cookie",
    )

    assert environment == {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LANG": "C",
        "LC_ALL": "C",
        "MEETING_COPILOT_SESSION_COOKIE": "meeting_copilot_session=redacted-test-cookie",
    }


def test_runner_declares_provenance_compatible_artifact_binding():
    source = TOOL_PATH.read_text(encoding="utf-8")

    assert '"status": decision_status' in source
    assert '"counts_as_public_release_package": False' in source
    assert '"artifact_path": artifact_path' in source
    assert '"artifact_sha256": artifact_sha256' in source

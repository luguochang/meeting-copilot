import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/packaged_runtime_supervisor_smoke.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("packaged_runtime_supervisor_smoke", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_process_table_and_find_backend_child(tmp_path):
    tool = load_tool_module()
    app_path = tmp_path / "Meeting Copilot.app"
    runtime = app_path / "Contents/Resources/MeetingCopilotRuntime.bundle"
    runtime.mkdir(parents=True)
    (runtime / "runtime-bundle-manifest.json").write_text(
        '{"schema_version":"meeting_copilot.runtime_bundle.v1",'
        '"runtimes":{"backend":{"executable":"runtime/backend-python/bin/python3.13"}}}',
        encoding="utf-8",
    )
    output = (
        " 100 1 /tmp/Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop\n"
        f" 101 100 {runtime}/runtime/backend-python/bin/python3.13 "
        "-m uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port 54321\n"
    )

    backend = tool.find_backend_process(
        tool.parse_process_table(output),
        app_pid=100,
        app_path=app_path,
    )

    assert backend["pid"] == 101
    assert backend["port"] == 54321


def test_find_backend_rejects_unrelated_or_non_child_process(tmp_path):
    tool = load_tool_module()
    app_path = tmp_path / "Meeting Copilot.app"
    runtime = app_path / "Contents/Resources/MeetingCopilotRuntime.bundle"
    runtime.mkdir(parents=True)
    (runtime / "runtime-bundle-manifest.json").write_text(
        '{"schema_version":"meeting_copilot.runtime_bundle.v1",'
        '"runtimes":{"backend":{"executable":"runtime/backend-python/bin/python3.13"}}}',
        encoding="utf-8",
    )
    processes = [{
        "pid": 101,
        "ppid": 999,
        "command": f"{app_path}/Contents/Resources/MeetingCopilotRuntime.bundle/"
        "runtime/backend-python/bin/python3.13 -m uvicorn app:app --port 54321",
    }]

    assert tool.find_backend_process(processes, app_pid=100, app_path=app_path) is None


def test_output_root_and_run_id_are_bounded(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    assert tool.resolve_output_root(repo_root, repo_root / "artifacts/tmp/smoke") == (
        repo_root / "artifacts/tmp/smoke"
    )
    with pytest.raises(ValueError, match="artifacts/tmp"):
        tool.resolve_output_root(repo_root, tmp_path / "outside")
    with pytest.raises(ValueError, match="run_id"):
        tool.validate_run_id("../escape")


def test_health_proof_matches_backend_and_rust_contract():
    tool = load_tool_module()

    assert tool.health_proof("a" * 64) == (
        "cbf9aaeaeb5cad9d6c602451cd8d10734b2a52461c8cfe72acd97e088fdf9783"
    )

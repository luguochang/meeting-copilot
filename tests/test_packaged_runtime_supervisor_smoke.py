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


def test_find_packaged_funasr_worker_requires_backend_parent_and_bundle_path(tmp_path):
    tool = load_tool_module()
    app_path = tmp_path / "Meeting Copilot.app"
    runtime = app_path / "Contents/Resources/MeetingCopilotRuntime.bundle"
    command = (
        f"{runtime}/runtime/funasr-python/bin/python3.11 "
        f"{runtime}/app/code/asr_runtime/scripts/funasr_stream_worker.py --resident"
    )
    worker = {"pid": 202, "ppid": 101, "command": command}

    assert tool.find_funasr_process([worker], backend_pid=101, app_path=app_path) == worker
    assert tool.find_funasr_process([worker], backend_pid=999, app_path=app_path) is None
    assert tool.find_funasr_process(
        [{**worker, "command": command.replace(str(runtime), "/tmp/other-runtime")}],
        backend_pid=101,
        app_path=app_path,
    ) is None


def test_find_conflicting_packaged_app_instances_reports_only_bounded_identity(tmp_path):
    tool = load_tool_module()
    candidate = tmp_path / "candidate/Meeting Copilot.app"
    binary_suffix = "Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop"
    processes = [
        {"pid": 101, "ppid": 1, "command": f"/Applications/{binary_suffix}"},
        {"pid": 102, "ppid": 1, "command": "/usr/bin/python3 local-server.py"},
    ]

    conflicts = tool.find_conflicting_app_instances(processes, app_path=candidate)

    assert conflicts == [{"pid": 101, "same_candidate": False}]
    assert "command" not in conflicts[0]


def test_supervisor_smoke_fails_closed_before_launch_when_app_instance_exists(
    tmp_path, monkeypatch
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    app_path = repo_root / "candidate/Meeting Copilot.app"
    binary = app_path / "Contents/MacOS/meeting-copilot-desktop"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"candidate")
    output_root = repo_root / "artifacts/tmp/smoke"
    monkeypatch.setattr(
        tool,
        "read_process_table",
        lambda: [
            {
                "pid": 4321,
                "ppid": 1,
                "command": (
                    "/Applications/Meeting Copilot.app/Contents/MacOS/"
                    "meeting-copilot-desktop"
                ),
            }
        ],
    )
    monkeypatch.setattr(
        tool.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("conflicting preflight must not launch another app")
        ),
    )
    monkeypatch.setattr(tool.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(tool.platform, "machine", lambda: "arm64")

    evidence = tool.smoke_packaged_app(
        repo_root=repo_root,
        app_path=app_path,
        output_root=output_root,
        run_id="conflicting-instance",
    )

    assert evidence["decision"]["status"] == "no_go_conflicting_app_instance"
    assert evidence["decision"]["counts_as_packaged_runtime_supervisor_evidence"] is False
    assert evidence["preflight"] == {
        "conflicting_app_instance_count": 1,
        "conflicting_app_instances": [{"pid": 4321, "same_candidate": False}],
    }


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


def test_packaged_audio_smoke_acknowledges_recording_notice_before_capture():
    tool = load_tool_module()

    payload = tool.meeting_preparation_payload()

    assert payload["notice_acknowledged"] is True
    assert payload["input_source"] == "microphone"
    assert payload["input_device_id"] == "packaged-smoke-fixture"
    assert payload["hotwords"]


def test_packaged_audio_smoke_paces_float32_pcm_in_realtime():
    tool = load_tool_module()

    assert tool.pcm_float32_duration_seconds(b"\x00" * (4 * 4_800)) == 0.3
    with pytest.raises(ValueError, match="divisible by four"):
        tool.pcm_float32_duration_seconds(b"\x00")

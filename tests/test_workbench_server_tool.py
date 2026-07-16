import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "workbench_server.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("workbench_server", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_uvicorn_command_defaults_to_workbench_port():
    tool = _load_tool()

    command = tool.build_uvicorn_command(port=8765)

    assert command == [
        "uvicorn",
        "meeting_copilot_web_mvp.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--log-level",
        "warning",
        "--timeout-graceful-shutdown",
        "8",
    ]


def test_stop_timeout_includes_cleanup_margin_after_uvicorn_grace_period():
    tool = _load_tool()

    assert tool.STOP_TIMEOUT_SECONDS > tool.GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS


def test_build_child_env_uses_data_dir_and_strips_paid_provider_secrets(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://paid.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-paid")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("PYTHONPATH", "existing")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="safe")

    assert env["MEETING_COPILOT_DATA_DIR"] == str(tmp_path / "data")
    assert str(tool.WEB_BACKEND_ROOT) in env["PYTHONPATH"]
    assert str(tool.CORE_ROOT) in env["PYTHONPATH"]
    assert "existing" in env["PYTHONPATH"]
    for key in ("LLM_GATEWAY_BASE_URL", "LLM_GATEWAY_API_KEY", "LLM_GATEWAY_MODEL", "OPENAI_API_KEY"):
        assert key not in env


def test_build_child_env_resolves_relative_data_dir_from_repo_root(monkeypatch):
    tool = _load_tool()

    env = tool.build_child_env(
        data_dir=Path("artifacts/tmp/relative-runtime"),
        provider_mode="safe",
    )

    assert env["MEETING_COPILOT_DATA_DIR"] == str(
        (tool.REPO_ROOT / "artifacts/tmp/relative-runtime").resolve()
    )


def test_build_child_env_can_explicitly_inherit_provider_env(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="inherit")

    assert env["LLM_GATEWAY_BASE_URL"] == "https://gw.example"
    assert env["LLM_GATEWAY_API_KEY"] == "sk-test"
    assert env["LLM_GATEWAY_MODEL"] == "m1"


def test_status_report_prefers_health_when_server_reachable(monkeypatch, tmp_path):
    tool = _load_tool()

    monkeypatch.setattr(tool, "read_pid", lambda pid_file: None)
    monkeypatch.setattr(tool, "check_health", lambda port, timeout_seconds=1.0: {"ok": True, "body": {"status": "ok"}})

    report = tool.status_report(port=8765, pid_file=tmp_path / "server.pid")

    assert report["status"] == "running"
    assert report["health_ok"] is True
    assert report["workbench_url"] == "http://127.0.0.1:8765/workbench"


def test_start_blocks_when_port_is_used_by_unknown_process(monkeypatch, tmp_path):
    tool = _load_tool()

    monkeypatch.setattr(tool, "check_health", lambda port, timeout_seconds=1.0: {"ok": False, "error": "connection refused"})
    monkeypatch.setattr(tool, "is_port_open", lambda port: True)

    report = tool.start_server(
        port=8765,
        pid_file=tmp_path / "server.pid",
        log_file=tmp_path / "server.log",
        data_dir=tmp_path / "data",
        provider_mode="safe",
    )

    assert report["status"] == "blocked_port_in_use"
    assert report["safe_to_kill_existing_process"] is False


def test_stop_server_reports_missing_pid_file(tmp_path):
    tool = _load_tool()

    report = tool.stop_server(pid_file=tmp_path / "missing.pid")

    assert report["status"] == "not_running"
    assert report["pid"] is None

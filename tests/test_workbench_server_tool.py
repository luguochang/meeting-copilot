import importlib.util
import os
from pathlib import Path

import pytest


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

    expected_launcher = [tool.sys._base_executable, "-m", "uvicorn"] if os.name == "nt" else ["uvicorn"]
    assert command == expected_launcher + [
        "meeting_copilot_web_mvp.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        "--log-level",
        "warning",
        "--ws",
        "websockets-sansio",
        "--timeout-graceful-shutdown",
        "8",
    ]


def test_stop_timeout_includes_cleanup_margin_after_uvicorn_grace_period():
    tool = _load_tool()

    assert tool.STOP_TIMEOUT_SECONDS > tool.GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS


def test_startup_health_timeout_covers_local_funasr_model_prewarm():
    tool = _load_tool()

    assert tool.STARTUP_HEALTH_TIMEOUT_SECONDS >= 60


def test_windows_child_process_does_not_inherit_launcher_console():
    tool = _load_tool()

    options = tool.popen_platform_options()

    if os.name == "nt":
        assert options == {
            "creationflags": tool.subprocess.CREATE_NO_WINDOW
            | tool.subprocess.CREATE_NEW_PROCESS_GROUP
        }
    else:
        assert options == {"start_new_session": True}


def test_build_child_env_uses_data_dir_and_strips_paid_provider_secrets(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://paid.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-paid")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "gpt-realtime")
    monkeypatch.setenv("LLM_GATEWAY_CORRECTION_MODEL", "gpt-correction")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "responses")
    monkeypatch.setenv("LLM_GATEWAY_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("LLM_GATEWAY_PROVIDER_LABEL", "temporary-provider")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("PYTHONPATH", "existing")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="safe")

    assert env["MEETING_COPILOT_DATA_DIR"] == str(tmp_path / "data")
    assert str(tool.WEB_BACKEND_ROOT) in env["PYTHONPATH"]
    assert str(tool.CORE_ROOT) in env["PYTHONPATH"]
    assert "existing" in env["PYTHONPATH"]
    for key in (
        "LLM_GATEWAY_BASE_URL",
        "LLM_GATEWAY_API_KEY",
        "LLM_GATEWAY_MODEL",
        "LLM_GATEWAY_REALTIME_MODEL",
        "LLM_GATEWAY_CORRECTION_MODEL",
        "LLM_GATEWAY_API_STYLE",
        "LLM_GATEWAY_TIMEOUT_SECONDS",
        "LLM_GATEWAY_PROVIDER_LABEL",
        "LLM_GATEWAY_IS_MOCK",
        "OPENAI_API_KEY",
    ):
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
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "m1-fast")
    monkeypatch.setenv("LLM_GATEWAY_CORRECTION_MODEL", "m1-correction")
    monkeypatch.setenv("LLM_GATEWAY_API_STYLE", "chat_completions")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="inherit")

    assert env["LLM_GATEWAY_BASE_URL"] == "https://gw.example"
    assert env["LLM_GATEWAY_API_KEY"] == "sk-test"
    assert env["LLM_GATEWAY_MODEL"] == "m1"
    assert env["LLM_GATEWAY_REALTIME_MODEL"] == "m1-fast"
    assert env["LLM_GATEWAY_CORRECTION_MODEL"] == "m1-correction"
    assert env["LLM_GATEWAY_API_STYLE"] == "chat_completions"
    assert env["MEETING_COPILOT_REALTIME_REFINER_POLICY"] == "prewarm"
    assert env["MEETING_COPILOT_REALTIME_COACH_RUNTIME"] == "pi"
    assert env["MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST"] == "0"
    assert env["MEETING_COPILOT_PI_BRIDGE_PREWARM"] == "1"


def test_build_child_env_acceptance_overrides_are_explicit(monkeypatch, tmp_path):
    tool = _load_tool()

    env = tool.build_child_env(
        data_dir=tmp_path / "data",
        provider_mode="inherit",
        correction_pricing_mode="unmetered",
        realtime_coach_cutoff_ms=5_000,
    )

    assert env["LLM_CORRECTION_PRICING_MODE"] == "unmetered"
    assert env["MEETING_COPILOT_REALTIME_READY_CUTOFF_MS"] == "5000"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"correction_pricing_mode": "unknown"},
        {"realtime_coach_cutoff_ms": 999},
        {"realtime_coach_cutoff_ms": 10_001},
    ],
)
def test_build_child_env_rejects_invalid_acceptance_overrides(tmp_path, kwargs):
    tool = _load_tool()

    with pytest.raises(ValueError):
        tool.build_child_env(
            data_dir=tmp_path / "data",
            provider_mode="inherit",
            **kwargs,
        )


def test_build_child_env_allows_explicit_realtime_refiner_policy(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_POLICY", "online_only")

    env = tool.build_child_env(
        data_dir=tmp_path / "data",
        provider_mode="inherit",
        realtime_refiner_policy="on-demand",
    )

    assert env["MEETING_COPILOT_REALTIME_REFINER_POLICY"] == "on_demand"


def test_build_child_env_selects_complete_onnx_preview_runtime(monkeypatch, tmp_path):
    tool = _load_tool()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("MEETING_COPILOT_FUNASR_ENGINE", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_FUNASR_MODEL_DIR", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_FUNASR_PYTHONPATH", raising=False)
    preview_root = tmp_path / "artifacts" / "tmp" / "asr_preview_bakeoff"
    model_dir = preview_root / "onnx-online"
    runtime_dir = preview_root / "runtime"
    model_dir.mkdir(parents=True)
    (runtime_dir / "funasr_onnx").mkdir(parents=True)
    (runtime_dir / "onnxruntime").mkdir()
    for filename in ("model.onnx", "decoder.onnx", "config.yaml", "am.mvn", "tokens.json"):
        (model_dir / filename).write_bytes(b"ready")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="safe")

    assert env["MEETING_COPILOT_FUNASR_ENGINE"] == "onnx"
    assert env["MEETING_COPILOT_FUNASR_MODEL_DIR"] == str(model_dir)
    assert env["MEETING_COPILOT_FUNASR_PYTHONPATH"] == str(runtime_dir)


def _write_cached_file_asr_models(root: Path, *, missing: tuple[str, ...] = ()) -> dict[str, Path]:
    tool = _load_tool()
    models = {
        name: root / directory
        for name, directory in tool.FILE_ASR_MODEL_CACHE_LAYOUT.items()
    }
    for name, path in models.items():
        path.mkdir(parents=True)
        if name not in missing:
            (path / "model.pt").write_bytes(b"model")
            (path / "config.yaml").write_text("model: local\n", encoding="utf-8")
    return models


def test_managed_env_discovers_complete_modelscope_file_asr_cache(monkeypatch, tmp_path):
    tool = _load_tool()
    cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    models = _write_cached_file_asr_models(cache_root)
    monkeypatch.setenv(tool.LOCAL_FILE_ASR_MODEL_CACHE_ENV, str(cache_root))
    monkeypatch.delenv("MEETING_COPILOT_RUNTIME_MANIFEST", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_REFINER_MODEL", raising=False)

    discovered = tool.discover_cached_file_asr_models()

    assert discovered == models


def test_managed_env_leaves_incomplete_modelscope_cache_fail_closed(monkeypatch, tmp_path):
    tool = _load_tool()
    cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    _write_cached_file_asr_models(cache_root, missing=("punc",))
    monkeypatch.setenv(tool.LOCAL_FILE_ASR_MODEL_CACHE_ENV, str(cache_root))
    monkeypatch.delenv("MEETING_COPILOT_RUNTIME_MANIFEST", raising=False)

    assert tool.discover_cached_file_asr_models() is None


def test_managed_env_does_not_use_user_cache_when_runtime_manifest_is_configured(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    cache_root = tmp_path / "modelscope" / "hub" / "models" / "iic"
    _write_cached_file_asr_models(cache_root)
    monkeypatch.setenv(tool.LOCAL_FILE_ASR_MODEL_CACHE_ENV, str(cache_root))
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(tmp_path / "sealed.json"))

    assert tool.discover_cached_file_asr_models() is None


def test_build_child_env_wires_cached_file_asr_models_when_staging_dir_is_absent(
    monkeypatch, tmp_path
):
    tool = _load_tool()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    monkeypatch.delenv("MEETING_COPILOT_RUNTIME_MANIFEST", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_REFINER_PYTHON", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_REALTIME_REFINER_WORKER", raising=False)
    cache_root = tmp_path / "cache" / "models" / "iic"
    models = _write_cached_file_asr_models(cache_root)
    monkeypatch.setenv(tool.LOCAL_FILE_ASR_MODEL_CACHE_ENV, str(cache_root))
    python = tmp_path / "code" / "asr_runtime" / ".venv-funasr" / "bin" / "python"
    worker = tmp_path / "code" / "asr_runtime" / "scripts" / "funasr_offline_refiner_worker.py"
    python.parent.mkdir(parents=True)
    worker.parent.mkdir(parents=True)
    python.write_text("fixture", encoding="utf-8")
    worker.write_text("fixture", encoding="utf-8")

    env = tool.build_child_env(data_dir=tmp_path / "data", provider_mode="safe")

    assert env["MEETING_COPILOT_REALTIME_REFINER_PYTHON"] == str(python)
    assert env["MEETING_COPILOT_REALTIME_REFINER_WORKER"] == str(worker)
    assert env["MEETING_COPILOT_REALTIME_REFINER_MODEL"] == str(models["offline"])
    assert env["MEETING_COPILOT_REALTIME_REFINER_VAD_MODEL"] == str(models["vad"])
    assert env["MEETING_COPILOT_REALTIME_REFINER_PUNC_MODEL"] == str(models["punc"])


def test_status_report_rejects_health_without_runtime_identity(monkeypatch, tmp_path):
    tool = _load_tool()

    monkeypatch.setattr(tool, "read_pid", lambda pid_file: None)
    monkeypatch.setattr(
        tool,
        "check_health",
        lambda port, timeout_seconds=1.0: {"ok": True, "body": {"status": "ok"}},
    )

    report = tool.status_report(port=8765, pid_file=tmp_path / "server.pid")

    assert report["status"] == "not_running"
    assert report["health_ok"] is False
    assert report["runtime_identity_verified"] is False
    assert (
        report["health"]["runtime_identity"]["reason"]
        == "runtime_identity_contract_missing"
    )
    assert report["workbench_url"] == "http://127.0.0.1:8765/workbench"


def test_start_blocks_when_port_is_used_by_unknown_process(monkeypatch, tmp_path):
    tool = _load_tool()

    monkeypatch.setattr(
        tool,
        "check_health",
        lambda port, timeout_seconds=1.0: {"ok": False, "error": "connection refused"},
    )
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


@pytest.mark.skipif(os.name != "nt", reason="Windows process liveness contract")
def test_windows_pid_liveness_uses_process_handle_state_not_posix_signal(monkeypatch):
    tool = _load_tool()
    monkeypatch.setattr(tool, "process_start_marker", lambda _pid: None)

    def unexpected_kill(_pid: int, _signal: int):
        pytest.fail("Windows PID liveness must not use os.kill(pid, 0)")

    monkeypatch.setattr(tool.os, "kill", unexpected_kill)

    assert tool.pid_running(12345) is False

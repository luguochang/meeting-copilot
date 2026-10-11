"""Candidate CLI controls online prewarm independently of offline refinement."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("product_candidate_server", ROOT / "tools/product_candidate_server.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def invoke_candidate(monkeypatch, tmp_path, flags):
    data = tmp_path / "artifacts" / "isolated-data"
    frontend = tmp_path / "artifacts" / "candidate-frontend"
    frontend.mkdir(parents=True)
    (frontend / "index.html").write_text("candidate", encoding="utf-8")
    observed = {}
    application = object()

    def quality_environment(models, data_dir):
        observed["quality_inputs"] = (models, data_dir)
        return {"MEETING_COPILOT_REALTIME_REFINER_POLICY": "prewarm", "MEETING_COPILOT_SOURCE_RUNTIME": "1"}

    def create_app(**kwargs):
        observed["create_app"] = kwargs
        observed["env"] = dict(tool.os.environ)
        return application

    def run(app, **kwargs):
        assert app is application
        observed["serve"] = kwargs

    def load_dotenv(path, override):
        observed["dotenv"] = (path, override)

    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool.os, "environ", {})
    monkeypatch.setitem(sys.modules, "web_quality", SimpleNamespace(
        DEFAULT_MODELS=tmp_path / "model-files", quality_environment=quality_environment,
    ))
    monkeypatch.setitem(sys.modules, "dotenv", SimpleNamespace(load_dotenv=load_dotenv))
    monkeypatch.setitem(sys.modules, "meeting_copilot_web_mvp.app", SimpleNamespace(create_app=create_app))
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace(run=run))
    monkeypatch.setattr(tool.sys, "argv", [
        "product_candidate_server.py", "--port", "8767", "--data-dir", str(data),
        "--frontend-dir", str(frontend), *flags,
    ])
    tool.main()
    return observed, data, frontend


@pytest.mark.parametrize("flags,online_prewarm,refiner_policy", [
    ([], False, "online_only"),
    (["--prewarm-asr"], True, "prewarm"),
    (["--prewarm-asr", "--refiner-policy", "online_only"], True, "online_only"),
    (["--refiner-policy", "prewarm"], False, "prewarm"),
])
def test_cli_preserves_defaults_and_can_prewarm_only_the_online_model(monkeypatch, tmp_path, flags, online_prewarm, refiner_policy):
    observed, data, frontend = invoke_candidate(monkeypatch, tmp_path, flags)
    assert observed["create_app"] == {
        "data_dir": data, "frontend_dir": frontend, "prewarm_funasr": online_prewarm,
        "semantic_projection_mode": "llm_first",
    }
    assert observed["env"]["MEETING_COPILOT_REALTIME_REFINER_POLICY"] == refiner_policy
    assert observed["env"]["MEETING_COPILOT_PI_BRIDGE_PREWARM"] == "0"
    assert observed["serve"] == {"host": "127.0.0.1", "port": 8767, "log_level": "warning"}
    assert observed["dotenv"] == (tmp_path / ".env", False)


def test_unknown_refiner_policy_is_rejected_before_app_import_or_start(monkeypatch, tmp_path):
    with pytest.raises(SystemExit) as error:
        invoke_candidate(monkeypatch, tmp_path, ["--refiner-policy", "on_demand"])
    assert error.value.code == 2

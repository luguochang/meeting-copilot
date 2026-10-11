"""Distribution must not silently lose recognition, refinement, or Pi."""
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("web_quality", ROOT / "tools/web_quality.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_quality_profile_overrides_low_quality_inherited_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_POLICY", "online_only")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "direct")
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_ENGINE", "onnx")
    monkeypatch.setenv("MEETING_COPILOT_FUNASR_PYTHONPATH", "/developer-only/preview")
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", "/another/app/manifest.json")
    env = tool.quality_environment(tmp_path / "models", tmp_path / "data")
    assert env["MEETING_COPILOT_REALTIME_REFINER_POLICY"] == "prewarm"
    assert env["MEETING_COPILOT_REALTIME_COACH_RUNTIME"] == "pi"
    assert env["MEETING_COPILOT_FUNASR_ENGINE"] == "pytorch"
    assert "MEETING_COPILOT_FUNASR_PYTHONPATH" not in env
    assert "MEETING_COPILOT_RUNTIME_MANIFEST" not in env
    assert env["MEETING_COPILOT_SOURCE_RUNTIME"] == "1"
    assert env["MEETING_COPILOT_FILE_ASR_MODEL_DIR"] == env["MEETING_COPILOT_REALTIME_REFINER_MODEL"]
    assert env["MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR"] == env["MEETING_COPILOT_DIARIZATION_VAD_DIR"]


def test_profile_does_not_invent_provider_or_free_pricing(monkeypatch, tmp_path):
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("LLM_CORRECTION_PRICING_MODE", raising=False)
    env = tool.quality_environment(tmp_path, tmp_path)
    assert "LLM_GATEWAY_API_KEY" not in env
    assert "LLM_CORRECTION_PRICING_MODE" not in env


def test_missing_and_corrupt_model_files_are_distinguished(tmp_path):
    model = {"files": {"model.pt": "0" * 64}}
    assert tool.model_errors(tmp_path, model) == ["missing:model.pt"]
    (tmp_path / "model.pt").write_bytes(b"incomplete download")
    assert tool.model_errors(tmp_path, model) == ["sha256_mismatch:model.pt"]
    model["files"]["model.pt"] = tool.sha256(tmp_path / "model.pt")
    assert tool.model_errors(tmp_path, model) == []


def test_download_reuses_only_verified_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "ASR_ROOT", tmp_path / "asr")
    interpreter = tool.python_in(tool.ASR_ROOT / ".venv-funasr")
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    cached = tmp_path / "cache/vendor/model"
    cached.mkdir(parents=True)
    (cached / "model.pt").write_bytes(b"verified weights")
    models = {"online": {"model_id": "vendor/model", "files": {"model.pt": tool.sha256(cached / "model.pt")}}}
    monkeypatch.setattr(tool, "model_specs", lambda: models)
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not download")))
    tool.download_models(tmp_path / "models", tmp_path / "cache")
    assert (tmp_path / "models/online/model.pt").read_bytes() == b"verified weights"


def test_incomplete_profile_refuses_to_start(monkeypatch, tmp_path):
    monkeypatch.setattr(tool, "doctor", lambda *a: {"ready": False, "checks": []})
    monkeypatch.setattr(tool.sys, "argv", ["web_quality.py", "start", "--data-dir", str(tmp_path)])
    monkeypatch.setattr(tool.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not start")))
    assert tool.main() == 1


def test_doctor_handles_utf8_dependency_output_on_legacy_windows_locale(monkeypatch, tmp_path):
    frontend = tmp_path / "code/web_mvp/frontend_v2/dist/index.html"
    frontend.parent.mkdir(parents=True)
    frontend.touch()
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    monkeypatch.setattr(tool, "model_specs", lambda: {})
    real_run = tool.subprocess.run
    outputs = []
    script = (
        "import sys; sys.stdout.buffer.write("
        + repr("本地语音预热中…\n".encode("utf-8"))
        + "); sys.stderr.buffer.write(b'\\xae')"
    )

    def run_dependency_probe(command, **kwargs):
        # Simulate a GBK Windows host while retaining actual subprocess decoding.
        kwargs.setdefault("encoding", "cp936")
        kwargs.setdefault("errors", "strict")
        result = real_run([sys.executable, "-c", script], **kwargs)
        outputs.append(result)
        return result

    monkeypatch.setattr(tool.subprocess, "run", run_dependency_probe)
    report = tool.doctor(tmp_path / "models", tmp_path / "data")
    assert report["ready"] is True
    assert len(outputs) == 4
    assert all(result.stdout == "本地语音预热中…\n" for result in outputs)
    assert all(result.stderr == "\ufffd" for result in outputs)

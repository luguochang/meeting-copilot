from __future__ import annotations

import json
import struct
import sys
import wave
from pathlib import Path

from meeting_copilot_web_mvp import asr_refiner


def _write_packaged_refiner_runtime(tmp_path: Path) -> Path:
    paths = {
        "python": "runtime/funasr-python/python.exe",
        "python_home": "runtime/funasr-python",
        "shared_runtime": "runtime/funasr-venv",
        "site_packages": "runtime/funasr-venv/Lib/site-packages",
        "worker": "app/code/asr_runtime/scripts/funasr_offline_refiner_worker.py",
        "offline": "models/funasr-file/offline-paraformer",
        "vad": "models/funasr-file/vad",
        "punc": "models/funasr-file/punc",
    }
    for relative in (paths["python"], paths["worker"], f'{paths["site_packages"]}/funasr/__init__.py'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    for model_name in ("offline", "vad", "punc"):
        model = tmp_path / paths[model_name]
        model.mkdir(parents=True)
        (model / "model.pt").write_text("fixture", encoding="utf-8")
        (model / "config.yaml").write_text("fixture", encoding="utf-8")
    manifest = {
        "schema_version": "meeting_copilot.runtime_bundle.v1",
        "packaged_python": {"funasr": {"path": paths["python_home"]}},
        "runtimes": {
            "funasr": {
                "venv_executable": paths["python"],
                "root": paths["shared_runtime"],
                "site_packages": paths["site_packages"],
            }
        },
        "file_asr": {
            "runtime": {"executable": paths["python"], "root": paths["shared_runtime"]},
            "models": {
                name: {"root": paths[name]}
                for name in ("offline", "vad", "punc")
            },
        },
        "offline_refiner": {
            "worker": {"path": paths["worker"]},
            "models": {
                name: paths[name]
                for name in ("offline", "vad", "punc")
            },
        },
        "component_inventory": {
            "schema_version": "meeting_copilot.runtime_component_inventory.v1",
            "status": "sealed",
            "components": {
                "file_asr.python_launcher": {"kind": "file", "path": paths["python"]},
                "shared_asr.python_runtime": {
                    "kind": "directory",
                    "path": paths["python_home"],
                },
                "shared_asr.runtime": {
                    "kind": "directory",
                    "path": paths["shared_runtime"],
                },
                "realtime_asr.offline_refiner_worker": {
                    "kind": "file",
                    "path": paths["worker"],
                },
                **{
                    f"file_asr.model.{name}": {
                        "kind": "directory",
                        "path": paths[name],
                    }
                    for name in ("offline", "vad", "punc")
                },
            },
        },
    }
    manifest_path = tmp_path / "runtime-bundle-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _float32_payload(samples: list[float]) -> bytes:
    return b"".join(struct.pack("<f", sample) for sample in samples)


def test_packaged_refiner_resolves_components_and_python_environment(monkeypatch, tmp_path):
    manifest_path = _write_packaged_refiner_runtime(tmp_path)
    monkeypatch.setenv("MEETING_COPILOT_RUNTIME_MANIFEST", str(manifest_path))
    monkeypatch.setenv("PYTHONHOME", "backend-python-home")
    monkeypatch.setenv("PYTHONPATH", "backend-site-packages")
    for name in (
        "MEETING_COPILOT_REALTIME_REFINER_PYTHON",
        "MEETING_COPILOT_BATCH_FUNASR_PYTHON",
        "MEETING_COPILOT_REALTIME_REFINER_WORKER",
        "MEETING_COPILOT_REALTIME_REFINER_MODEL",
        "MEETING_COPILOT_FILE_ASR_MODEL_DIR",
        "MEETING_COPILOT_REALTIME_REFINER_VAD_MODEL",
        "MEETING_COPILOT_FILE_ASR_VAD_MODEL_DIR",
        "MEETING_COPILOT_REALTIME_REFINER_PUNC_MODEL",
        "MEETING_COPILOT_FILE_ASR_PUNC_MODEL_DIR",
        "MEETING_COPILOT_REALTIME_REFINER_PYTHON_HOME",
        "MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH",
    ):
        monkeypatch.delenv(name, raising=False)

    python, worker, model, vad, punc = asr_refiner._configured_components()
    environment = asr_refiner._offline_environment()

    assert asr_refiner.refinement_capability()["status"] == "ready"
    assert python == tmp_path / "runtime" / "funasr-python" / "python.exe"
    assert worker == tmp_path / "app" / "code" / "asr_runtime" / "scripts" / "funasr_offline_refiner_worker.py"
    assert model == tmp_path / "models" / "funasr-file" / "offline-paraformer"
    assert vad == tmp_path / "models" / "funasr-file" / "vad"
    assert punc == tmp_path / "models" / "funasr-file" / "punc"
    assert environment["PYTHONHOME"] == str(tmp_path / "runtime" / "funasr-python")
    assert str(tmp_path / "runtime" / "funasr-venv" / "Lib" / "site-packages") in environment["PYTHONPATH"]
    assert "backend-site-packages" not in environment["PYTHONPATH"]


def test_refiner_fails_closed_without_local_components(monkeypatch):
    monkeypatch.setattr(
        asr_refiner,
        "_configured_components",
        lambda: (None, None, None, None, None),
    )
    monkeypatch.setattr(
        asr_refiner.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker must not start")),
    )

    result = asr_refiner.refine_pcm_f32(_float32_payload([0.0]))

    assert result.status == "unavailable"
    assert result.reason == "offline_refinement_components_missing"
    assert result.authoritative is False


def test_refiner_rejects_unaligned_and_non_finite_pcm_before_capability_probe(monkeypatch):
    monkeypatch.setattr(
        asr_refiner,
        "refinement_capability",
        lambda: (_ for _ in ()).throw(AssertionError("capability must not be probed")),
    )

    unaligned = asr_refiner.refine_pcm_f32(b"\x00")
    non_finite = asr_refiner.refine_pcm_f32(_float32_payload([float("nan")]))

    assert (unaligned.status, unaligned.reason) == ("invalid_audio", "pcm_unaligned")
    assert (non_finite.status, non_finite.reason) == ("invalid_audio", "pcm_non_finite")


def test_refiner_converts_float32_to_pcm16_for_resident_worker(monkeypatch):
    observed = {}

    class FakeResidentRefiner:
        def refine(self, pcm16, *, timeout_s):
            observed["samples"] = [sample[0] for sample in struct.iter_unpack("<h", pcm16)]
            observed["timeout"] = timeout_s
            return asr_refiner.RefinementResult(
                text="接口灰度百分之五。",
                status="refined",
                model_id="offline-model",
            )

    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: FakeResidentRefiner())

    result = asr_refiner.refine_pcm_f32(
        _float32_payload([-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]),
        timeout_s=7.5,
    )

    assert result.text == "接口灰度百分之五。"
    assert result.status == "refined"
    assert result.authoritative is True
    assert observed["samples"] == [
        -32_768,
        -32_768,
        -16_384,
        0,
        16_384,
        32_767,
        32_767,
    ]
    assert observed["timeout"] == 7.5
    assert asr_refiner._offline_environment()["MODELSCOPE_OFFLINE"] == "1"
    assert asr_refiner._offline_environment()["HF_HUB_OFFLINE"] == "1"
    assert asr_refiner._offline_environment()["HF_DATASETS_OFFLINE"] == "1"
    assert asr_refiner._offline_environment()["TRANSFORMERS_OFFLINE"] == "1"


def test_refiner_uses_its_packaged_python_home_and_path_instead_of_backend_values(
    monkeypatch,
):
    monkeypatch.setenv("PYTHONHOME", "backend-python-home")
    monkeypatch.setenv("PYTHONPATH", "backend-site-packages")

    without_override = asr_refiner._offline_environment()

    assert "PYTHONHOME" not in without_override
    assert "PYTHONPATH" not in without_override

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_PYTHON_HOME", "asr-python-home")
    monkeypatch.setenv(
        "MEETING_COPILOT_REALTIME_REFINER_PYTHONPATH",
        "asr-site-packages",
    )

    with_override = asr_refiner._offline_environment()

    assert with_override["PYTHONHOME"] == "asr-python-home"
    assert with_override["PYTHONPATH"] == "asr-site-packages"


def test_refiner_rejects_non_ok_worker_result(monkeypatch):
    class FailedResidentRefiner:
        def refine(self, _pcm16, *, timeout_s):
            assert timeout_s == 30.0
            return asr_refiner.RefinementResult(
                text="",
                status="failed",
                reason="offline_worker_failed",
            )

    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: FailedResidentRefiner())

    result = asr_refiner.refine_pcm_f32(_float32_payload([0.0]))

    assert result.status == "failed"
    assert result.reason == "offline_worker_failed"
    assert result.authoritative is False


def test_refine_wav_file_splits_long_audio_and_reuses_resident_worker(monkeypatch, tmp_path):
    audio_path = tmp_path / "long.wav"
    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x00\x00" * 16_000 * 61)

    observed_chunk_samples = []

    class FakeResidentRefiner:
        def refine(self, pcm16, *, timeout_s):
            assert timeout_s > 0
            observed_chunk_samples.append(len(pcm16) // 2)
            return asr_refiner.RefinementResult(
                text=f"part-{len(observed_chunk_samples)}.",
                status="refined",
                model_id="offline-model",
            )

    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: FakeResidentRefiner())

    result = asr_refiner.refine_wav_file(audio_path, timeout_s=30.0)

    assert result.text == "part-1.part-2.part-3."
    assert result.authoritative is True
    assert len(observed_chunk_samples) == 3
    assert sum(observed_chunk_samples) == 61 * 16_000
    assert max(observed_chunk_samples) <= 30 * 16_000


def test_resident_refiner_reuses_one_process_and_forces_offline_environment(tmp_path):
    worker = tmp_path / "fake_refiner_worker.py"
    worker.write_text(
        """
import base64
import json
import os
import sys

offline_names = ("MODELSCOPE_OFFLINE", "HF_HUB_OFFLINE", "HF_DATASETS_OFFLINE", "TRANSFORMERS_OFFLINE")
print(json.dumps({"event_type": "ready", "model_id": "fake-offline-model", "network_offline": all(os.environ.get(name) == "1" for name in offline_names)}), flush=True)
count = 0
for line in sys.stdin:
    request = json.loads(line)
    if request.get("command") == "shutdown":
        break
    count += 1
    pcm16 = base64.b64decode(request["pcm16_base64"])
    print(json.dumps({"event_type": "result", "request_id": request["request_id"], "status": "ok", "text": f"segment-{count}-bytes-{len(pcm16)}", "model_id": "fake-offline-model"}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    model_paths = []
    for name in ("model", "vad", "punc"):
        path = tmp_path / name
        path.mkdir()
        (path / "model.pt").write_bytes(b"model")
        (path / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        model_paths.append(path)
    resident = asr_refiner._ResidentOfflineRefiner((Path(sys.executable), worker, *model_paths))

    try:
        assert resident.start(timeout_s=5.0) is True
        first = resident.refine(b"\x00\x00", timeout_s=5.0)
        second = resident.refine(b"\x00\x00\x01\x00", timeout_s=5.0)
        status = resident.status()
        assert first.text == "segment-1-bytes-2"
        assert second.text == "segment-2-bytes-4"
        assert first.authoritative is True
        assert second.authoritative is True
        assert status["process_start_count"] == 1
        assert status["completed_request_count"] == 2
        assert status["process_ready"] is True
        assert status["network_offline"] is True
    finally:
        resident.shutdown()
    assert resident.status()["process_running"] is False

from __future__ import annotations

import json
import os
import struct
import sys
import time
import wave
from pathlib import Path

import pytest

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


def test_realtime_refiner_defaults_to_online_only_without_spawning_worker(monkeypatch):
    monkeypatch.delenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, raising=False)
    monkeypatch.setattr(
        asr_refiner,
        "_get_resident_refiner",
        lambda: (_ for _ in ()).throw(AssertionError("worker must not be resolved")),
    )

    policy = asr_refiner.realtime_refiner_policy()
    result = asr_refiner.refine_pcm_f32(_float32_payload([0.0]))

    assert policy == {
        "schema_version": "realtime_refiner_policy.v1",
        "mode": "online_only",
        "source": "default_resource_guard",
        "environment_variable": "MEETING_COPILOT_REALTIME_REFINER_POLICY",
        "configured_value": None,
        "realtime_refinement_enabled": False,
        "prewarm_enabled": False,
        "cold_start_deferred": False,
        "degradation_reason": asr_refiner.ONLINE_ONLY_REFINEMENT_REASON,
        "warning": None,
    }
    assert result.status == "bypassed"
    assert result.reason == asr_refiner.ONLINE_ONLY_REFINEMENT_REASON
    assert result.authoritative is False


def test_invalid_realtime_refiner_policy_fails_closed_with_auditable_source(monkeypatch):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "load-everything")

    policy = asr_refiner.realtime_refiner_policy()

    assert policy["mode"] == "online_only"
    assert policy["source"] == "invalid_environment_fallback"
    assert policy["configured_value"] == "load-everything"
    assert policy["prewarm_enabled"] is False
    assert policy["warning"] == "invalid_realtime_refiner_policy_fell_back_to_online_only"


@pytest.mark.parametrize(
    ("configured", "prewarm_enabled", "cold_start_deferred", "warning"),
    [
        ("on-demand", False, True, "offline_refiner_cold_start_deferred_to_first_final"),
        ("prewarm", True, False, None),
    ],
)
def test_explicit_realtime_refiner_policy_preserves_compatibility_modes(
    configured,
    prewarm_enabled,
    cold_start_deferred,
    warning,
):
    policy = asr_refiner.realtime_refiner_policy(
        {asr_refiner.REALTIME_REFINER_POLICY_ENV: configured}
    )

    assert policy["mode"] == configured.replace("-", "_")
    assert policy["source"] == "environment"
    assert policy["realtime_refinement_enabled"] is True
    assert policy["prewarm_enabled"] is prewarm_enabled
    assert policy["cold_start_deferred"] is cold_start_deferred
    assert policy["warning"] == warning


def test_prewarm_is_blocked_unless_policy_explicitly_requests_it(monkeypatch):
    monkeypatch.delenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, raising=False)
    monkeypatch.setattr(
        asr_refiner,
        "refinement_capability",
        lambda: (_ for _ in ()).throw(AssertionError("capability must not trigger prewarm")),
    )

    assert asr_refiner.prewarm_refiner_worker() is False


def test_prewarm_degrades_when_capability_is_stale_but_components_are_missing(monkeypatch):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(
        asr_refiner,
        "refinement_capability",
        lambda: {"status": "ready", "model_id": "stale-capability"},
    )
    monkeypatch.setattr(
        asr_refiner,
        "_configured_components",
        lambda *_args, **_kwargs: (None, None, None, None, None),
    )

    assert asr_refiner.prewarm_refiner_worker() is False


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


def test_explicit_refiner_python_preserves_venv_symlink_for_execution(tmp_path):
    base_python = tmp_path / "base" / "python3.11"
    venv_python = tmp_path / "venv" / "bin" / "python"
    base_python.parent.mkdir(parents=True)
    venv_python.parent.mkdir(parents=True)
    base_python.write_text("fixture", encoding="utf-8")
    try:
        venv_python.symlink_to(base_python)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    python, *_ = asr_refiner._configured_components(
        {"MEETING_COPILOT_REALTIME_REFINER_PYTHON": str(venv_python)}
    )

    assert python == venv_python
    assert python.resolve() == base_python.resolve()


def test_refiner_fails_closed_without_local_components(monkeypatch):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
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
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
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
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
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


def test_resident_refiner_startup_obeys_request_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
    worker = tmp_path / "deadline_refiner_worker.py"
    worker.write_text(
        "import time\n"
        "time.sleep(5)\n",
        encoding="utf-8",
    )
    model_paths = []
    for name in ("model", "vad", "punc"):
        path = tmp_path / name
        path.mkdir()
        (path / "model.pt").write_bytes(b"model")
        (path / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        model_paths.append(path)
    resident = asr_refiner._ResidentOfflineRefiner(
        (Path(sys.executable), worker, *model_paths)
    )

    started = time.monotonic()
    try:
        result = resident.refine(b"\x00\x00", timeout_s=0.05)
    finally:
        resident.shutdown()

    assert result.status == "failed"
    assert result.reason == "offline_worker_not_ready"
    assert time.monotonic() - started < 1.0


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


def test_refine_wav_file_skips_empty_final_all_zero_silence_chunk(monkeypatch, tmp_path):
    audio_path = tmp_path / "trailing-silence.wav"
    with wave.open(str(audio_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16_000)
        wav_file.writeframes(b"\x01\x00" * 16_000)

    class FakeResidentRefiner:
        def refine(self, pcm16, *, timeout_s):
            assert timeout_s > 0
            if asr_refiner._pcm16_is_exact_silence(pcm16):
                return asr_refiner.RefinementResult(
                    text="",
                    status="empty",
                    model_id="offline-model",
                    reason="offline_text_empty",
                )
            return asr_refiner.RefinementResult(
                text="spoken part",
                status="refined",
                model_id="offline-model",
            )

    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: FakeResidentRefiner())
    monkeypatch.setattr(
        asr_refiner,
        "_split_pcm16_for_file_refinement",
        lambda _pcm16: [b"\x01\x00", b"\x00\x00"],
    )

    result = asr_refiner.refine_wav_file(audio_path, timeout_s=5.0)

    assert result.authoritative is True
    assert result.text == "spoken part"


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


def test_resident_refiner_unloads_after_idle_and_restarts_on_next_request(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "0.05")
    worker = tmp_path / "idle_refiner_worker.py"
    worker.write_text(
        """
import base64
import json
import sys

print(json.dumps({"event_type": "ready", "model_id": "idle-test-model"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("command") == "shutdown":
        break
    pcm16 = base64.b64decode(request["pcm16_base64"])
    print(json.dumps({"event_type": "result", "request_id": request["request_id"], "status": "ok", "text": f"bytes-{len(pcm16)}"}), flush=True)
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
    resident = asr_refiner._ResidentOfflineRefiner(
        (Path(sys.executable), worker, *model_paths)
    )

    try:
        assert resident.start(timeout_s=5.0) is True
        initial_pid = resident.status()["pid"]
        deadline = time.monotonic() + 2.0
        while resident.status()["process_running"] and time.monotonic() < deadline:
            time.sleep(0.01)

        idle_status = resident.status()
        assert idle_status["process_running"] is False
        assert idle_status["idle_unload_count"] == 1
        assert idle_status["last_stop_reason"] == "idle_timeout"

        result = resident.refine(b"\x00\x00", timeout_s=5.0)
        restarted_status = resident.status()
        assert result.text == "bytes-2"
        assert result.authoritative is True
        assert restarted_status["pid"] != initial_pid
        assert restarted_status["process_start_count"] == 2
        assert restarted_status["idle_unload_scheduled"] is True
    finally:
        resident.shutdown()


def test_resident_refiner_stays_loaded_for_active_meeting_then_unloads(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "0.05")
    worker = tmp_path / "meeting_resident_refiner_worker.py"
    worker.write_text(
        """
import json
import sys

print(json.dumps({"event_type": "ready", "model_id": "meeting-residency-model"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if request.get("command") == "shutdown":
        break
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
    resident = asr_refiner._ResidentOfflineRefiner(
        (Path(sys.executable), worker, *model_paths)
    )

    try:
        resident.retain_for_meeting("meeting-active")
        assert resident.start(timeout_s=5.0) is True
        time.sleep(0.12)

        active_status = resident.status()
        assert active_status["process_running"] is True
        assert active_status["process_start_count"] == 1
        assert active_status["idle_unload_count"] == 0
        assert active_status["active_meeting_lease_count"] == 1
        assert active_status["idle_unload_blocked_by_active_meeting"] is True
        assert active_status["idle_unload_scheduled"] is False

        resident.release_for_meeting("meeting-active")
        deadline = time.monotonic() + 2.0
        while resident.status()["process_running"] and time.monotonic() < deadline:
            time.sleep(0.01)

        released_status = resident.status()
        assert released_status["process_running"] is False
        assert released_status["idle_unload_count"] == 1
        assert released_status["active_meeting_lease_count"] == 0
        assert released_status["idle_unload_blocked_by_active_meeting"] is False
        assert released_status["last_stop_reason"] == "idle_timeout"
    finally:
        resident.shutdown()

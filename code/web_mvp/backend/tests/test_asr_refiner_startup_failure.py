from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import asr_refiner


@pytest.fixture(autouse=True)
def fake_capacity_for_fake_workers(monkeypatch):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(16 * guard.GIB, 16 * guard.GIB, 1))


def resident_fixture(tmp_path, script):
    worker = tmp_path / "fixture_worker.py"
    worker.write_text(script, encoding="utf-8")
    paths = []
    for name in ("model", "vad", "punc"):
        path = tmp_path / name
        path.mkdir()
        (path / "model.pt").write_bytes(b"fixture")
        (path / "config.yaml").write_text("fixture: true", encoding="utf-8")
        paths.append(path)
    return asr_refiner._ResidentOfflineRefiner((Path(sys.executable), worker, *paths)), worker


READY_SCRIPT = '''import json
import sys
print(json.dumps({"event_type":"ready","model_id":"recovered-fixture"}), flush=True)
for line in sys.stdin:
    if json.loads(line).get("command") == "shutdown":
        break
'''


@pytest.mark.parametrize(("message", "code"), [
    ("RuntimeError: DefaultCPUAllocator: not enough memory: allocate 4194304 bytes", "worker_out_of_memory"),
    ("OSError: [WinError 1455] The paging file is too small", "worker_out_of_memory"),
    ("ImportError: DLL load failed while importing torch", "worker_dependency_load_failed"),
    ("ModuleNotFoundError: no module named example", "worker_dependency_unavailable"),
    ("FileNotFoundError: C:/private/model/path", "worker_model_file_missing"),
    ("_pickle.UnpicklingError: private checkpoint data", "worker_checkpoint_load_failed"),
    ("ValueError: private incompatible model parameter", "worker_model_load_failed"),
])
def test_actual_child_startup_failure_is_classified_private_and_latched_without_auto_respawn(tmp_path, message, code):
    script = f"import sys\nprint({message!r}, file=sys.stderr, flush=True)\nprint('private diagnostic content', file=sys.stderr)\nraise SystemExit(1)\n"
    resident, _ = resident_fixture(tmp_path, script)
    try:
        assert resident.start(timeout_s=5.0) is False
        status = resident.status()
        assert status["process_running"] is False and status["process_ready"] is False
        assert status["last_stop_reason"] == "worker_exit"
        assert status["last_error_code"] == code and status["last_exit_code"] == 1
        assert status["recovery_required"] is True
        assert message not in str(status) and "private diagnostic content" not in str(status)
        assert status["startup_diagnostics"]["phase"] == "failed"
        assert status["startup_diagnostics"]["exception_name"] in asr_refiner._STARTUP_EXCEPTION_CODES
        assert resident.start(timeout_s=0.05) is False
        result = resident.refine(b"\x00\x00", timeout_s=0.05)
        assert result.status == "failed" and not result.authoritative
        assert resident.status()["process_start_count"] == 1
    finally:
        resident.shutdown()


def test_startup_diagnostics_are_bounded_fixed_labels_and_never_retain_child_text():
    generation = asr_refiner._RefinerGeneration(object())
    for _ in range(10_002):
        generation.record_startup_line("private transcript C:/Users/private key=secret")
    generation.record_startup_line("funasr_cpu_checkpoint_loader status=stock model=CTTransformer code=unsupported_policy")
    assert generation.startup_diagnostics()["last_completed_model"] is None
    generation.record_startup_line("funasr_cpu_checkpoint_loader status=optimized model=SeacoParaformer code=ordinary_strict_copy")
    generation.record_startup_line("CustomPrivateError: sensitive user text")
    generation.record_startup_line("ValueError: private file and key")
    diagnostic = generation.startup_diagnostics()
    assert diagnostic["stderr_line_count"] == 10_000
    assert diagnostic["last_completed_model"] == "SeacoParaformer"
    assert diagnostic["exception_name"] == "ValueError"
    assert diagnostic["phase"] == "model_loading"
    assert all(word not in json.dumps(diagnostic) for word in ("private", "secret", "sensitive", "C:/Users"))


def test_early_child_exit_wakes_startup_waiter_before_host_inference_lock(tmp_path):
    resident, _ = resident_fixture(tmp_path, READY_SCRIPT)

    class ExitedChild:
        stdout = io.StringIO("")
        def poll(self):
            return 1

    generation = asr_refiner._RefinerGeneration(ExitedChild())
    generation.stderr_done.set()
    resident._generation = generation
    reader = threading.Thread(target=resident._read_stdout, args=(generation,))
    with resident._lock:
        reader.start()
        assert generation.ready.wait(1), "a failed startup must release waiters before acquiring the host lock"
        assert generation.failure_code == "worker_exited_before_ready"
    reader.join(timeout=1)
    assert not reader.is_alive()
    assert resident._last_error_code == "worker_exited_before_ready"


def test_one_explicit_recovery_attempt_replaces_failure_with_actual_new_process_metadata(tmp_path):
    resident, worker = resident_fixture(tmp_path, "import sys\nprint('not enough memory', file=sys.stderr, flush=True)\nraise SystemExit(1)\n")
    try:
        assert resident.start(timeout_s=5.0) is False
        worker.write_text(READY_SCRIPT, encoding="utf-8")
        assert resident.start(timeout_s=5.0) is False
        assert resident.start(timeout_s=5.0, retry_failed=True) is True
        status = resident.status()
        assert status["model_id"] == "recovered-fixture" and status["process_ready"] is True
        assert status["process_start_count"] == 2
        assert status["last_error_code"] is None and status["last_exit_code"] is None
        assert status["recovery_required"] is False and status["last_stop_reason"] is None
    finally:
        resident.shutdown()


def test_concurrent_prewarm_joins_one_process_and_short_joiner_does_not_kill_loading(tmp_path):
    resident, _ = resident_fixture(tmp_path, "import time\ntime.sleep(0.3)\n" + READY_SCRIPT)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(resident.start, timeout_s=5.0)
            deadline = time.monotonic() + 2
            while not resident.status()["process_running"] and time.monotonic() < deadline:
                time.sleep(0.001)
            assert resident.status()["process_running"]
            assert resident.start(timeout_s=0.01, retry_failed=True) is False
            assert resident.status()["process_running"] and resident.status()["process_start_count"] == 1
            joined = executor.submit(resident.start, timeout_s=5.0)
            assert first.result(timeout=5.0) is True and joined.result(timeout=5.0) is True
        assert resident.status()["process_start_count"] == 1
        assert resident.status()["recovery_required"] is False
    finally:
        resident.shutdown()


def test_start_timeout_records_failure_and_cannot_start_again_from_each_segment(tmp_path):
    resident, _ = resident_fixture(tmp_path, "import time\ntime.sleep(10)\n")
    try:
        assert resident.start(timeout_s=0.05) is False
        assert resident.status()["last_error_code"] == "worker_start_timeout"
        assert resident.status()["recovery_required"] is True
        assert resident.refine(b"\x00\x00", timeout_s=0.05).authoritative is False
        assert resident.status()["process_start_count"] == 1
    finally:
        resident.shutdown()


def test_spawn_error_is_auditable_without_raw_exception_or_unbounded_retries(monkeypatch, tmp_path):
    resident, _ = resident_fixture(tmp_path, READY_SCRIPT)
    calls = []
    def reject(*args, **kwargs):
        calls.append(True)
        raise OSError("private process details")
    monkeypatch.setattr(subprocess, "Popen", reject)
    assert resident.start(timeout_s=1) is False
    assert resident.start(timeout_s=1) is False
    status = resident.status()
    assert status["last_error_code"] == "worker_spawn_failed" and status["recovery_required"] is True
    assert status["last_exit_code"] is None and status["process_start_count"] == 0
    assert "private process details" not in str(status) and len(calls) == 1


def test_inference_timeout_does_not_repeatedly_reload_the_model_on_following_segments(tmp_path):
    script = '''import json
import sys
print(json.dumps({"event_type":"ready","model_id":"timeout-fixture"}), flush=True)
for line in sys.stdin:
    if json.loads(line).get("command") == "shutdown":
        break
'''
    resident, _ = resident_fixture(tmp_path, script)
    try:
        assert resident.start(timeout_s=5.0) is True
        assert resident.refine(b"\x00\x00", timeout_s=0.05).reason == "offline_worker_timeout"
        assert resident.status()["last_error_code"] == "worker_timeout"
        assert resident.status()["recovery_required"] is True
        assert resident.refine(b"\x00\x00", timeout_s=0.05).reason == "offline_worker_not_ready"
        assert resident.status()["process_start_count"] == 1
    finally:
        resident.shutdown()


def test_ready_payload_cannot_override_host_process_diagnostics(tmp_path):
    script = READY_SCRIPT.replace('"model_id":"recovered-fixture"', '"model_id":"recovered-fixture","process_running":False,"last_error_code":"private"')
    resident, _ = resident_fixture(tmp_path, script)
    try:
        assert resident.start(timeout_s=5.0) is True
        status = resident.status()
        assert status["process_running"] is True and status["last_error_code"] is None
        assert status["last_exit_code"] is None and status["recovery_required"] is False
    finally:
        resident.shutdown()


@pytest.mark.parametrize("code", ["worker_out_of_memory", "unrecognized private diagnostics"])
def test_prewarm_failure_endpoint_reports_fixed_code_and_memory_guidance_without_raw_logs(monkeypatch, tmp_path, code):
    from meeting_copilot_web_mvp import app as app_module
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setattr(asr_refiner, "realtime_refiner_policy", lambda: {"prewarm_enabled": True})
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "prewarm_refiner_worker", lambda **_kwargs: False)
    monkeypatch.setattr(asr_refiner, "refiner_worker_status", lambda: {"spawned": True, "process_running": False,
        "process_ready": False, "last_error_code": code, "last_exit_code": 1, "recovery_required": True,
        "stderr": "private raw provider log"})
    client = TestClient(app_module.create_app())
    response = client.post("/providers/asr/prewarm", headers={"x-meeting-copilot-verification": "1"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "refiner_not_ready" and detail["last_exit_code"] == 1
    assert detail["recovery_required"] is True and detail["started"] is False
    if code == "worker_out_of_memory":
        assert detail["error_code"] == code and "内存不足" in detail["message"]
    else:
        assert detail["error_code"] is None and code not in response.text
    assert "private raw provider log" not in response.text

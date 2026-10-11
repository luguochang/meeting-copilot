from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import io
import subprocess
import sys
import time

import pytest

from meeting_copilot_web_mvp import asr_refiner
from meeting_copilot_web_mvp import asr_resource_guard as guard


def memory(commit=16 * guard.GIB, physical=16 * guard.GIB):
    return guard.MemorySnapshot(commit, physical, 1)


READY_SCRIPT = '''import json
import sys
print(json.dumps({"event_type":"ready","model_id":"fixture"}), flush=True)
for line in sys.stdin:
    if json.loads(line).get("command") == "shutdown":
        break
'''


def resident(tmp_path, reader, script=READY_SCRIPT):
    worker = tmp_path / "fixture.py"
    worker.write_text(script, encoding="utf-8")
    paths = []
    for name in ("model", "vad", "punc"):
        path = tmp_path / name
        path.mkdir()
        paths.append(path)
    return asr_refiner._ResidentOfflineRefiner((Path(sys.executable), worker, *paths),
                                             memory_reader=reader, monitor_interval_s=.005)


@pytest.mark.parametrize("code", ["asr_capacity_unavailable", "asr_resource_probe_failed"])
def test_cold_refusal_never_spawns_and_latches_until_one_explicit_retry(monkeypatch, tmp_path, code):
    observed = []
    def failed_probe():
        if code == "asr_resource_probe_failed":
            raise OSError("private path")
        return memory(7 * guard.GIB)
    pool = resident(tmp_path, failed_probe)
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: observed.append(True))
    assert pool.start(timeout_s=.01) is False
    status = pool.status()
    assert status["last_error_code"] == code and status["resource"]["last_error_code"] == code
    assert status["recovery_required"] and not status["spawned"]
    pool._memory_reader = lambda: memory()
    assert pool.start(timeout_s=.01) is False and observed == []
    assert pool.refine(b"\0\0", timeout_s=.01).reason == code
    assert pool.process_start_count == 0
    # A refused attempt released the shared cold lease.
    with guard.cold_start_lease():
        pass


def test_explicit_retry_rechecks_resources_and_success_clears_resource_failure(tmp_path):
    snapshot = [memory(7 * guard.GIB)]
    pool = resident(tmp_path, lambda: snapshot[0])
    try:
        assert not pool.start(timeout_s=.01)
        assert not pool.start(timeout_s=.01, retry_failed=True)
        snapshot[0] = memory()
        assert not pool.start(timeout_s=.01)
        assert pool.start(timeout_s=3, retry_failed=True)
        status = pool.status()
        assert status["process_start_count"] == 1 and status["process_ready"]
        assert status["last_error_code"] is None and not status["recovery_required"]
        assert status["resource"]["last_error_code"] is None
        with guard.cold_start_lease():
            pass  # readiness released the cold lease while keeping warm watch
    finally:
        pool.shutdown()


def test_busy_lease_refusal_is_nonblocking_and_does_not_cancel_other_loading(tmp_path):
    pool = resident(tmp_path, lambda: memory())
    held = guard.cold_start_lease()
    try:
        started = time.monotonic()
        assert not pool.start(timeout_s=3)
        assert time.monotonic() - started < .25
        assert pool.status()["last_error_code"] == "asr_cold_start_busy"
        assert pool.process_start_count == 0
    finally:
        held.release()
        pool.shutdown()


def test_runtime_decline_interrupts_wait_under_inference_lock_and_preserves_first_resource_cause(tmp_path):
    snapshot = [memory()]
    pool = resident(tmp_path, lambda: snapshot[0])
    try:
        assert pool.start(timeout_s=3)
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(pool.refine, b"\0\0", timeout_s=5)
            time.sleep(.03)
            snapshot[0] = memory(guard.GIB)
            result = pending.result(timeout=2)
        assert result.reason == "asr_resource_guard_triggered" and not result.authoritative
        status = pool.status()
        assert status["last_stop_reason"] == "asr_resource_guard_triggered"
        assert status["recovery_required"] and not status["process_running"]
        snapshot[0] = memory()
        assert pool.refine(b"\0\0", timeout_s=.1).reason == "asr_resource_guard_triggered"
        assert pool.process_start_count == 1
        assert pool.start(timeout_s=3, retry_failed=True)
        assert pool.process_start_count == 2
    finally:
        pool.shutdown()


def test_runtime_probe_failure_during_cold_load_wakes_start_and_releases_lease_after_exit(tmp_path):
    allowed = [True]
    def reader():
        if not allowed[0]:
            raise OSError("private")
        return memory()
    pool = resident(tmp_path, reader, "import time\ntime.sleep(5)\n" + READY_SCRIPT)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(pool.start, timeout_s=4)
            deadline = time.monotonic() + 2
            while pool.process_start_count == 0 and time.monotonic() < deadline:
                time.sleep(.002)
            allowed[0] = False
            assert not pending.result(timeout=2)
        deadline = time.monotonic() + 2
        while pool.status()["process_running"] and time.monotonic() < deadline:
            time.sleep(.005)
        assert pool.status()["last_error_code"] == "asr_resource_probe_failed"
        pool.shutdown()
        with guard.cold_start_lease():
            pass
    finally:
        pool.shutdown()


def test_ready_worker_is_reused_below_cold_floor_and_input_refusal_does_not_latch_it(tmp_path):
    snapshot = [memory()]
    pool = resident(tmp_path, lambda: snapshot[0])
    try:
        assert pool.start(timeout_s=3)
        snapshot[0] = memory(4 * guard.GIB, 4 * guard.GIB)
        assert pool.start(timeout_s=.01)
        class LargePcmWithoutAllocation:
            def __len__(self):
                return guard.GIB
        result = pool.refine(LargePcmWithoutAllocation(), timeout_s=.01)
        assert result.reason == "asr_capacity_unavailable"
        status = pool.status()
        assert status["process_ready"] and not status["recovery_required"]
        assert status["last_error_code"] is None and status["process_start_count"] == 1
    finally:
        pool.shutdown()


class FakeWav:
    def __init__(self, *, nframes=1_000_000):
        self.nframes = nframes
        self.reads = 0
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass
    def getnchannels(self):
        return 1
    def getsampwidth(self):
        return 2
    def getframerate(self):
        return 16000
    def getcomptype(self):
        return "NONE"
    def getnframes(self):
        return self.nframes
    def readframes(self, _frames):
        self.reads += 1
        return b"\1\0"


def test_file_input_admission_precedes_full_pcm_read_and_leaves_existing_pool_untouched(monkeypatch):
    wav = FakeWav()
    monkeypatch.setattr(asr_refiner.wave, "open", lambda *_args: wav)
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: memory(2 * guard.GIB + 8_000_000))
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: pytest.fail("must not touch the existing pool"))
    result = asr_refiner.refine_wav_file(Path("synthetic.wav"), retry_failed=True)
    assert result.reason == "asr_capacity_unavailable" and wav.reads == 0


@pytest.mark.parametrize("resource_code", list(guard.RESOURCE_ERROR_CODES))
def test_file_resource_reason_stays_bare_and_manual_retry_is_only_first_chunk(monkeypatch, resource_code):
    wav = FakeWav(nframes=1)
    monkeypatch.setattr(asr_refiner.wave, "open", lambda *_args: wav)
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: memory())
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_split_pcm16_for_file_refinement", lambda *_args: [b"\1\0", b"\1\0"])
    attempts = []
    class FakePool:
        def refine(self, _pcm, *, timeout_s, retry_failed=False):
            attempts.append(retry_failed)
            return asr_refiner.RefinementResult("first", "refined") if len(attempts) == 1 else asr_refiner.RefinementResult("", "failed", reason=resource_code)
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", FakePool)
    result = asr_refiner.refine_wav_file(Path("synthetic.wav"), retry_failed=True)
    assert result.reason == resource_code and attempts == [True, False]


def test_automatic_prewarm_does_not_clear_recovery_lock_and_explicit_retry_is_forwarded(monkeypatch):
    attempts = []
    class Pool:
        def start(self, *, timeout_s, retry_failed):
            attempts.append(retry_failed)
            return False
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_configured_components", lambda: (Path("fixture"),) * 5)
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", Pool)
    assert not asr_refiner.prewarm_refiner_worker()
    assert not asr_refiner.prewarm_refiner_worker(retry_failed=True)
    assert attempts == [False, True]


class StubbornOwnedProcess:
    def __init__(self):
        self.returncode = None
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = None
        self.pid = 1  # Synthetic metadata only; no PID API is called.
        self.terminated = 0
        self.killed = 0
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("synthetic", timeout)
        return self.returncode
    def terminate(self):
        self.terminated += 1
    def kill(self):
        self.killed += 1


def stubborn_pool():
    components = (Path("old-component"),) * 5
    pool = asr_refiner._ResidentOfflineRefiner(components, memory_reader=lambda: memory())
    process = StubbornOwnedProcess()
    generation = asr_refiner._RefinerGeneration(process)
    generation.monitor = guard.OwnedPopenMonitor(process)
    generation.metadata = {"model_id": "already-ready"}
    generation.stderr_done.set()
    pool._process = process
    pool._generation = generation
    pool._ready_metadata = generation.metadata
    return pool, process, generation


def test_component_change_retains_old_owner_until_exact_exit_and_never_routes_new_config_to_old(monkeypatch):
    pool, process, _generation = stubborn_pool()
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    new_components = (Path("new-component"),) * 5
    monkeypatch.setattr(asr_refiner, "_configured_components", lambda: new_components)
    with pytest.raises(guard.AsrResourceError) as observed:
        asr_refiner._get_resident_refiner()
    assert observed.value.code == "asr_resource_guard_triggered"
    assert asr_refiner._REFINER is pool and not pool.cleanup_complete()
    assert pool.status()["cleanup_incomplete"] and pool.process_start_count == 0
    process.returncode = -9
    new_pool = asr_refiner._get_resident_refiner()
    assert new_pool is not pool and new_pool.components == new_components
    assert new_pool.process_start_count == 0 and pool.cleanup_complete()


def test_global_shutdown_does_not_discard_live_owner_and_late_exit_allows_cleanup(monkeypatch):
    pool, process, _generation = stubborn_pool()
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    asr_refiner.shutdown_refiner_worker()
    assert asr_refiner._REFINER is pool
    assert pool.status()["recovery_required"] and pool.status()["cleanup_incomplete"]
    assert not pool.start(timeout_s=.01, retry_failed=True)
    assert pool.process_start_count == 0
    process.returncode = -9
    asr_refiner.shutdown_refiner_worker()
    assert asr_refiner._REFINER is None and pool.cleanup_complete()


def test_eof_with_owned_child_still_alive_attempts_cleanup_and_retains_failure_owner():
    pool, process, generation = stubborn_pool()
    pool._read_stdout(generation)
    assert process.terminated == 1 and process.killed == 1
    assert generation.failure_code == "asr_resource_guard_triggered"
    status = pool.status()
    assert status["cleanup_incomplete"] and status["recovery_required"]
    assert not pool.cleanup_complete()
    process.returncode = -9
    pool.shutdown()
    assert pool.cleanup_complete()


def test_dead_child_with_late_watcher_retains_owner_until_both_acknowledge(monkeypatch):
    pool, process, generation = stubborn_pool()
    process.returncode = 0
    class SlowWatcher:
        is_alive = True
        failure = None
        def close(self):
            return not self.is_alive
    generation.monitor = SlowWatcher()
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    asr_refiner.shutdown_refiner_worker()
    assert asr_refiner._REFINER is pool and pool._generation is generation
    assert not pool.cleanup_complete()
    assert not pool.start(timeout_s=.01, retry_failed=True)
    assert pool._generation is generation and pool.process_start_count == 0
    generation.monitor.is_alive = False
    asr_refiner.shutdown_refiner_worker()
    assert asr_refiner._REFINER is None and pool.cleanup_complete()


def test_blocked_old_owner_prewarm_cannot_abort_online_startup_or_replace_owner(monkeypatch):
    pool, process, _generation = stubborn_pool()
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    monkeypatch.setattr(asr_refiner, "_configured_components", lambda: (Path("new-component"),) * 5)
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    assert asr_refiner.prewarm_refiner_worker() is False
    assert asr_refiner._REFINER is pool and pool.process_start_count == 0
    assert pool.status()["last_error_code"] == "asr_resource_guard_triggered"
    assert asr_refiner.prewarm_refiner_worker(retry_failed=True) is False
    assert asr_refiner._REFINER is pool and pool.process_start_count == 0
    process.returncode = -9
    pool.shutdown()


@pytest.mark.parametrize("entry", ["pcm", "wav"])
def test_public_refinement_returns_bare_resource_error_when_old_owner_blocks_replacement(monkeypatch, entry):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: memory())
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    def blocked():
        raise guard.AsrResourceError("asr_resource_guard_triggered")
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", blocked)
    if entry == "pcm":
        result = asr_refiner.refine_pcm_f32(b"\0" * 4)
    else:
        monkeypatch.setattr(asr_refiner.wave, "open", lambda *_args: FakeWav(nframes=1))
        result = asr_refiner.refine_wav_file(Path("synthetic.wav"))
    assert result.reason == "asr_resource_guard_triggered" and result.status == "failed"

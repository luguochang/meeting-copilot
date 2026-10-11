"""Controlled resident subprocess tests; no ASR model or external provider is used."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp import asr_refiner


PCM = struct.pack("<f", 0.05) * 160


def _wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def demand_runtime(monkeypatch, tmp_path):
    guard = asr_refiner.resource_guard
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "0.03")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS", "2")
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(
        16 * guard.GIB, 16 * guard.GIB, 1,
    ))
    gate = tmp_path / "controlled-startup-gate"
    worker = tmp_path / "controlled_refiner.py"
    worker.write_text(f'''import json, sys, time
from pathlib import Path
while not Path({str(gate)!r}).exists():
    time.sleep(0.005)
print(json.dumps({{"event_type":"ready","model_id":"controlled-fixture"}}), flush=True)
for line in sys.stdin:
    request=json.loads(line)
    if request.get("command")=="shutdown":
        break
    print(json.dumps({{"request_id":request["request_id"],"status":"ok","text":"已准备好的离线精修结果。"}},ensure_ascii=False),flush=True)
''', encoding="utf-8")
    pool = asr_refiner._ResidentOfflineRefiner((
        Path(sys.executable), worker, tmp_path / "model", tmp_path / "vad", tmp_path / "punc",
    ))
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: pool)
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    yield SimpleNamespace(pool=pool, gate=gate)
    gate.touch()
    pool.shutdown()


def test_first_meeting_pcm_prepares_once_in_background_without_waiting_for_ready(
    monkeypatch, demand_runtime,
):
    pool = demand_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("demand-stream")
    assert pool.process_start_count == 0, "on_demand retain must not become prewarm"
    caller = threading.get_ident()
    original_start = pool.start
    start_threads = []

    def observe_start(**kwargs):
        start_threads.append(threading.get_ident())
        assert kwargs["retry_failed"] is False
        return original_start(**kwargs)

    monkeypatch.setattr(pool, "start", observe_start)
    started = time.monotonic()
    first = asr_refiner.refine_pcm_f32(PCM)
    assert time.monotonic() - started < 0.5
    assert first.status == "unavailable" and first.reason == asr_refiner.WARMING_REFINEMENT_REASON
    assert not first.authoritative
    assert _wait_until(lambda: pool.process_start_count == 1)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: asr_refiner.refine_pcm_f32(PCM), range(8)))
    assert all(result.reason == asr_refiner.WARMING_REFINEMENT_REASON for result in results)
    assert start_threads and caller not in start_threads and len(start_threads) == 1
    assert pool.process_start_count == 1
    assert asr_refiner.get_refiner_readiness_waiter("demand-stream") is None
    demand_runtime.gate.touch()
    assert _wait_until(lambda: pool.status()["process_ready"])
    ready = asr_refiner.refine_pcm_f32(PCM)
    assert ready.authoritative and ready.text == "已准备好的离线精修结果。"
    assert len(start_threads) == 1 and pool.completed_request_count == 1
    time.sleep(0.08)
    assert pool.status()["process_running"] and pool.status()["idle_unload_count"] == 0
    asr_refiner.release_refiner_for_meeting("demand-stream")
    assert _wait_until(lambda: not pool.status()["process_running"])
    assert pool.status()["idle_unload_count"] == 1


def test_pending_generation_before_thread_runs_is_warming_and_shutdown_cannot_revive_it(
    monkeypatch, demand_runtime,
):
    pending = []

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            pending.append(self)

    monkeypatch.setattr(asr_refiner, "threading", SimpleNamespace(Thread=DeferredThread))
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("closed owner must not spawn"))
    assert asr_refiner.retain_refiner_for_meeting("pending-stream")
    for _ in range(3):
        result = asr_refiner.refine_pcm_f32(PCM)
        assert result.reason == asr_refiner.WARMING_REFINEMENT_REASON
    assert len(pending) == 1 and demand_runtime.pool._generation is None
    demand_runtime.pool.shutdown()
    pending[0].target()
    assert demand_runtime.pool.process_start_count == 0
    assert demand_runtime.pool.status()["closed"]
    assert asr_refiner.refine_pcm_f32(PCM).reason == "offline_worker_closed"


def test_live_pcm_does_not_wait_for_background_admission_lock(monkeypatch, demand_runtime):
    pool = demand_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("admission-pending")
    acquired, release = threading.Event(), threading.Event()

    def held_admission():
        with pool._lock:
            pool._meeting_prewarm_pending = True
            acquired.set()
            release.wait(2.0)

    owner = threading.Thread(target=held_admission)
    owner.start()
    try:
        assert acquired.wait(1.0)
        before = time.monotonic()
        result = asr_refiner.refine_pcm_f32(PCM)
        assert time.monotonic() - before < 0.5
        assert result.reason == asr_refiner.WARMING_REFINEMENT_REASON
        assert not result.authoritative
    finally:
        release.set()
        owner.join(timeout=1.0)
        pool._meeting_prewarm_pending = False
    assert not owner.is_alive()
    assert pool.process_start_count == 0


def test_capacity_refusal_is_latched_without_automatic_retry_or_guard_bypass(
    monkeypatch, demand_runtime,
):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(0, 0, 1))
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("cold capacity admission must precede spawn"))
    pool = demand_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("capacity-denied")
    first = asr_refiner.refine_pcm_f32(PCM)
    assert first.reason == asr_refiner.WARMING_REFINEMENT_REASON
    assert _wait_until(lambda: pool.status()["recovery_required"])
    for _ in range(3):
        result = asr_refiner.refine_pcm_f32(PCM)
        assert result.status == "failed" and result.reason == "asr_capacity_unavailable"
    assert pool.process_start_count == 0
    assert pool.status()["resource"]["last_check"]["required_commit_bytes"] >= 8 * guard.GIB


def test_no_active_meeting_does_not_demand_cold_worker(monkeypatch, demand_runtime):
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("unretained PCM cannot spawn"))
    result = asr_refiner.refine_pcm_f32(PCM)
    assert result.reason == "offline_worker_prewarm_required" and not result.authoritative
    assert demand_runtime.pool.process_start_count == 0


def test_known_non_capacity_startup_failure_is_not_retried_or_reported_as_warming(
    monkeypatch, demand_runtime,
):
    pool = demand_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("failed-generation")
    pool._last_error_code = "worker_start_timeout"
    pool._recovery_required = True
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_k: pytest.fail("latched failure must not retry"))
    for _ in range(2):
        result = asr_refiner.refine_pcm_f32(PCM)
        assert result.status == "failed" and result.reason == "worker_start_timeout"
        assert not result.authoritative
    assert pool.process_start_count == 0


def test_reported_generation_failure_is_latched_before_it_can_be_mislabelled_warming(demand_runtime):
    pool = demand_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("generation-failure")
    assert asr_refiner.refine_pcm_f32(PCM).reason == asr_refiner.WARMING_REFINEMENT_REASON
    assert _wait_until(lambda: pool.process_start_count == 1)
    pool._generation.record_failure("worker_dependency_unavailable")
    for _ in range(2):
        result = asr_refiner.refine_pcm_f32(PCM)
        assert result.status == "failed" and result.reason == "worker_dependency_unavailable"
    assert pool.status()["recovery_required"] and pool.process_start_count == 1

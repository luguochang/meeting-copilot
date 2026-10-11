from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

from meeting_copilot_web_mvp import asr_refiner


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def meeting_runtime(monkeypatch, tmp_path):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(16 * guard.GIB, 16 * guard.GIB, 1))
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "0.03")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS", "2")
    gate = tmp_path / "synthetic-startup-gate"
    worker = tmp_path / "synthetic_worker.py"
    worker.write_text(f'''import json, sys, time
from pathlib import Path
while not Path({str(gate)!r}).exists():
    time.sleep(0.005)
print(json.dumps({{"event_type":"ready","model_id":"synthetic"}}), flush=True)
for line in sys.stdin:
    if json.loads(line).get("command") == "shutdown":
        break
''', encoding="utf-8")
    paths = (Path(sys.executable), worker, tmp_path / "model", tmp_path / "vad", tmp_path / "punc")
    pool = asr_refiner._ResidentOfflineRefiner(paths)
    monkeypatch.setattr(asr_refiner, "_REFINER", pool)
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: pool)
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    yield SimpleNamespace(pool=pool, gate=gate)
    gate.touch()
    pool.shutdown()


def test_new_stream_retain_starts_one_background_prewarm_and_pcm_does_not_wait(meeting_runtime):
    pool = meeting_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("synthetic-session")
    assert wait_until(lambda: pool.process_start_count == 1), "new stream retain must actually arrange preparation"
    before = time.monotonic()
    result = asr_refiner.refine_pcm_f32(b"\x00\x00\x00\x3f")
    assert time.monotonic() - before < 0.5
    assert result.reason == "offline_worker_warming_up" and not result.authoritative
    assert pool.process_start_count == 1
    meeting_runtime.gate.touch()
    assert wait_until(lambda: pool.status()["process_ready"])
    time.sleep(0.08)
    status = pool.status()
    assert status["process_running"] and status["idle_unload_count"] == 0
    assert status["active_meeting_lease_count"] == 1
    assert not status["idle_unload_scheduled"]


def test_idle_unload_then_new_meeting_retain_restarts_same_owner_once(meeting_runtime):
    pool = meeting_runtime.pool
    meeting_runtime.gate.touch()
    assert pool.start(timeout_s=2.0)
    assert wait_until(lambda: not pool.status()["process_running"])
    assert pool.status()["last_stop_reason"] == "idle_timeout"
    assert asr_refiner.retain_refiner_for_meeting("after-idle")
    assert wait_until(lambda: pool.process_start_count == 2 and pool.status()["process_ready"]), "idle unload must not leave new meetings permanently prewarm_required"
    assert asr_refiner._get_resident_refiner() is pool
    asr_refiner.release_refiner_for_meeting("after-idle")
    assert wait_until(lambda: not pool.status()["process_running"])
    assert pool.status()["idle_unload_count"] == 2


def test_duplicate_and_concurrent_retain_deduplicate_pending_and_running_prewarm(meeting_runtime):
    pool = meeting_runtime.pool
    sessions = ["session-a", "session-a", "session-b", "session-a"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(executor.map(asr_refiner.retain_refiner_for_meeting, sessions))
    assert wait_until(lambda: pool.process_start_count == 1)
    for _ in range(3):
        assert asr_refiner.retain_refiner_for_meeting("session-a")
    assert pool.process_start_count == 1 and pool.status()["active_meeting_lease_count"] == 2
    meeting_runtime.gate.touch()
    assert wait_until(lambda: pool.status()["process_ready"])
    assert asr_refiner.retain_refiner_for_meeting("session-c")
    assert pool.process_start_count == 1 and pool.status()["active_meeting_lease_count"] == 3


def test_failed_worker_is_not_retried_by_each_new_retain(monkeypatch, meeting_runtime):
    pool = meeting_runtime.pool
    pool._recovery_required = True
    pool._last_error_code = "worker_timeout"
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("recovery lock must prevent implicit spawn"))
    for _ in range(3):
        assert asr_refiner.retain_refiner_for_meeting("failed-session")
    assert pool.process_start_count == 0 and pool._last_error_code == "worker_timeout"
    assert pool.status()["active_meeting_lease_count"] == 1


def test_meeting_prewarm_keeps_cold_capacity_admission_and_latches_failure(monkeypatch, meeting_runtime):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(0, 0, 1))
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("capacity admission must precede worker spawn"))
    pool = meeting_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("low-memory-session")
    assert wait_until(lambda: pool.status()["recovery_required"])
    assert pool.status()["last_error_code"] == "asr_capacity_unavailable"
    assert asr_refiner.retain_refiner_for_meeting("low-memory-session")
    assert pool.process_start_count == 0


@pytest.mark.parametrize("policy", ["on_demand", "online_only"])
def test_other_policies_do_not_schedule_meeting_prewarm(monkeypatch, meeting_runtime, policy):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, policy)
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("only prewarm policy may prepare on retain"))
    retained = asr_refiner.retain_refiner_for_meeting("other-policy")
    assert retained is (policy == "on_demand")
    assert meeting_runtime.pool.process_start_count == 0


def test_shutdown_latches_owner_closed_before_a_delayed_meeting_task_runs(monkeypatch, meeting_runtime):
    pending = []

    class DeferredThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            pending.append(self)

    monkeypatch.setattr(asr_refiner, "threading", SimpleNamespace(Thread=DeferredThread))
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("closed owner must not be resurrected"))
    pool = meeting_runtime.pool
    assert asr_refiner.retain_refiner_for_meeting("pending-shutdown")
    assert asr_refiner.retain_refiner_for_meeting("pending-shutdown")
    assert asr_refiner.retain_refiner_for_meeting("pending-other-session")
    assert len(pending) == 1
    pool.shutdown()
    pending[0].target()
    assert pool.process_start_count == 0
    assert pool.status()["closed"] and not pool.status()["meeting_prewarm_pending"]
    assert not pool.start(timeout_s=0.01, retry_failed=True)


def test_explicit_start_cannot_revive_a_closed_owner(monkeypatch, meeting_runtime):
    pool = meeting_runtime.pool
    pool.shutdown()
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("closed owner must reject Popen even for explicit retry"))
    assert not pool.start(timeout_s=0.01, retry_failed=True)

from __future__ import annotations

import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import wave

import pytest

from meeting_copilot_web_mvp import asr_refiner


@pytest.fixture
def resident(monkeypatch, tmp_path):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(16 * guard.GIB, 16 * guard.GIB, 1))
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "off")
    clock = [0.0]
    monkeypatch.setattr(asr_refiner, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class Child:
        running = True
        pid = 123
        stdin = io.StringIO()

        def poll(self):
            return None if self.running else 1

    child = Child()
    paths = (Path("fake-python"), Path("fake-worker"), tmp_path / "model", tmp_path / "vad", tmp_path / "punc")
    pool = asr_refiner._ResidentOfflineRefiner(paths)
    generation = asr_refiner._RefinerGeneration(child)
    generation.metadata = {"event_type": "ready", "model_id": "synthetic"}
    generation.ready.set()
    pool._process = child
    pool._generation = generation
    pool._ready_metadata = generation.metadata
    pool._responses = generation.responses
    starts, stops = [], []

    def start(**kwargs):
        starts.append(kwargs)
        return True

    def stop(**kwargs):
        stops.append(kwargs)
        child.running = False

    monkeypatch.setattr(pool, "start", start)
    monkeypatch.setattr(pool, "_stop_locked", stop)
    return SimpleNamespace(pool=pool, child=child, generation=generation, clock=clock, starts=starts, stops=stops)


@pytest.mark.parametrize("expired_during", ["cold_start", "queue"])
def test_unsent_request_expiring_during_cold_start_or_queue_preserves_ready_shared_worker(monkeypatch, resident, expired_during):
    pool = resident.pool
    if expired_during == "cold_start":
        def delayed_start(**kwargs):
            resident.starts.append(kwargs)
            resident.clock[0] = 2.0
            return True
        monkeypatch.setattr(pool, "start", delayed_start)
    else:
        class QueuedLock:
            def acquire(self, **_kwargs):
                resident.clock[0] = 2.0
                return True

            def release(self):
                pass

            def __enter__(self):
                self.acquire()

            def __exit__(self, *_args):
                self.release()
        pool._lock = QueuedLock()
    result = pool.refine(b"\x01\x00", timeout_s=1.0)
    assert resident.child.stdin.getvalue() == "", "expired unsent requests must not write PCM to the worker"
    assert resident.stops == [], "an expired caller must not stop the shared ready worker"
    assert pool._recovery_required is False and pool._last_error_code is None
    assert result.status == "failed" and result.reason == "offline_request_deadline_expired"
    assert not result.authoritative and resident.child.running


@pytest.mark.parametrize("warming", [False, True])
def test_live_prewarm_pcm_neither_spawns_nor_joins_cold_worker(monkeypatch, resident, warming):
    pool = resident.pool
    pool._ready_metadata = {}
    resident.generation.metadata = {}
    if not warming:
        pool._generation = None
        pool._process = None
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: pool)
    result = asr_refiner.refine_pcm_f32(b"\x00\x00\x00\x3f", timeout_s=30.0)
    assert resident.starts == [], "live prewarm PCM must never join a cold start or spawn it"
    assert resident.child.stdin.getvalue() == "" and resident.stops == []
    assert result.reason == ("offline_worker_warming_up" if warming else "offline_worker_prewarm_required")
    assert not result.authoritative and pool._recovery_required is False


def test_sent_inference_timeout_still_stops_and_latches_worker(monkeypatch, resident):
    class NoResponse:
        def get(self, *, timeout):
            resident.clock[0] += timeout
            raise asr_refiner.queue.Empty
    resident.pool._responses = NoResponse()
    result = resident.pool.refine(b"\x01\x00", timeout_s=1.0)
    written = json.loads(resident.child.stdin.getvalue())
    assert written["command"] == "refine"
    assert result.reason == "offline_worker_timeout" and not result.authoritative
    assert resident.pool._recovery_required and resident.pool._last_error_code == "worker_timeout"
    assert len(resident.stops) == 1 and not resident.child.running


def test_expired_lock_wait_returns_without_releasing_another_callers_lock_or_stopping_worker(resident):
    acquired, release = threading.Event(), threading.Event()

    def hold_inference_lock():
        with resident.pool._lock:
            acquired.set()
            release.wait(2.0)

    owner = threading.Thread(target=hold_inference_lock)
    owner.start()
    try:
        assert acquired.wait(1.0)
        result = resident.pool.refine(b"\x01\x00", timeout_s=0.01)
        assert result.reason == "offline_request_deadline_expired"
        assert resident.child.stdin.getvalue() == "" and resident.stops == []
        assert resident.child.running and not resident.pool._recovery_required
    finally:
        release.set()
        owner.join(timeout=1.0)
    assert not owner.is_alive()


def test_expiry_while_encoding_request_does_not_write_or_poison_worker(monkeypatch, resident):
    original_dumps = asr_refiner.json.dumps

    def slow_encoding(*args, **kwargs):
        data = original_dumps(*args, **kwargs)
        resident.clock[0] = 2.0
        return data

    monkeypatch.setattr(asr_refiner.json, "dumps", slow_encoding)
    result = resident.pool.refine(b"\x01\x00", timeout_s=1.0)
    assert result.reason == "offline_request_deadline_expired"
    assert resident.child.stdin.getvalue() == "" and resident.stops == []
    assert resident.child.running and not resident.pool._recovery_required


def test_live_prewarm_does_not_respawn_a_failed_worker_on_following_segments(monkeypatch, resident):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: resident.pool)
    resident.pool._recovery_required = True
    resident.pool._last_error_code = "worker_timeout"
    for _ in range(3):
        result = asr_refiner.refine_pcm_f32(b"\x00\x00\x00\x3f")
        assert result.reason == "offline_worker_not_ready" and not result.authoritative
    assert resident.starts == [] and resident.stops == []
    assert resident.child.stdin.getvalue() == "" and resident.pool._last_error_code == "worker_timeout"


def test_live_prewarm_retains_resource_guard_failure_without_sending(monkeypatch, resident):
    guard = asr_refiner.resource_guard
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: resident.pool)
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(0, 0, 1))
    result = asr_refiner.refine_pcm_f32(b"\x00\x00\x00\x3f")
    assert result.reason == "asr_capacity_unavailable" and not result.authoritative
    assert resident.starts == [] and resident.stops == [] and resident.child.stdin.getvalue() == ""


@pytest.mark.parametrize("entry_point", ["prewarm_pcm", "on_demand_pcm", "explicit_wav"])
def test_live_pcm_uses_ready_worker_without_cold_start_and_file_keeps_explicit_start(monkeypatch, resident, tmp_path, entry_point):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "on_demand" if entry_point == "on_demand_pcm" else "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: resident.pool)

    class ResultWriter(io.StringIO):
        def write(self, data):
            request = json.loads(data)
            resident.generation.responses.put({"request_id": request["request_id"], "status": "ok", "text": "synthetic result"})
            return super().write(data)
    resident.child.stdin = ResultWriter()
    if entry_point == "explicit_wav":
        audio_path = tmp_path / "synthetic.wav"
        with wave.open(str(audio_path), "wb") as audio:
            audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
            audio.writeframes(b"\x01\x00" * 160)
        result = asr_refiner.refine_wav_file(audio_path, timeout_s=1.0)
    else:
        result = asr_refiner.refine_pcm_f32(b"\x00\x00\x00\x3f", timeout_s=1.0)
    assert result.authoritative and result.text == "synthetic result"
    assert len(resident.starts) == (1 if entry_point == "explicit_wav" else 0)
    assert resident.stops == []

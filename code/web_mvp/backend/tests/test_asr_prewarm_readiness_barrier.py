"""Prewarm admission uses an isolated synthetic child, never an ASR model."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meeting_copilot_web_mvp import asr_refiner, asr_stream
from meeting_copilot_web_mvp.app import _realtime_correction_blockers, create_app
from meeting_copilot_web_mvp.asr_live_repository import InMemoryAsrLiveSessionRepository
from meeting_copilot_web_mvp.native_pcm_protocol import encode_native_pcm_v2_frame


@pytest.fixture
def isolated_prewarm(monkeypatch, tmp_path):
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(16 * guard.GIB, 16 * guard.GIB, 1))
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_IDLE_UNLOAD_SECONDS", "0")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_REFINER_PREWARM_TIMEOUT_SECONDS", "2")
    gate = tmp_path / "gate"
    child = tmp_path / "child.py"
    child.write_text(f'''import json, sys, time
from pathlib import Path
while not Path({str(gate)!r}).exists():
    time.sleep(0.005)
print(json.dumps({{"event_type": "ready", "model_id": "synthetic"}}), flush=True)
for line in sys.stdin:
    if json.loads(line).get("command") == "shutdown":
        break
''', encoding="utf-8")
    owner = asr_refiner._ResidentOfflineRefiner((Path(sys.executable), child, tmp_path / "m", tmp_path / "v", tmp_path / "p"))
    monkeypatch.setattr(asr_refiner, "_REFINER", owner)
    monkeypatch.setattr(asr_refiner, "_get_resident_refiner", lambda: owner)
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_stream, "ASR_READY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", lambda _pcm: SimpleNamespace(
        text="", status="unavailable", model_id=None, reason="synthetic_unavailable", authoritative=False,
    ))
    yield SimpleNamespace(owner=owner, gate=gate, audio_dir=tmp_path / "audio")
    gate.touch()
    owner.shutdown()


class OnlineRecognizer:
    provider = "funasr_realtime"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons = []

    def __init__(self, ready_delay=0.0):
        self.ready_delay = ready_delay
        self.ready_calls = []
        self.pcm = []
        self._seq = 0
        self.aborted = threading.Event()
        self.abort_calls = 0
        self.finalize_calls = 0

    def wait_ready(self, timeout):
        self.ready_calls.append(timeout)
        return not self.aborted.wait(self.ready_delay)

    def recognize_chunk(self, payload):
        self.pcm.append(payload)
        self._seq += 1
        return [{"event_type": "partial", "segment_id": "synthetic", "text": "synthetic draft"}]

    def finalize(self):
        self.finalize_calls += 1
        return []

    def abort(self):
        self.abort_calls += 1
        self.aborted.set()


class ControlledWebSocket:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
        self.closed = False

    async def accept(self):
        return None

    async def receive(self):
        message = await self.messages.get()
        if isinstance(message, BaseException):
            raise message
        return message

    async def send_text(self, payload):
        self.sent.append(json.loads(payload))

    async def close(self):
        self.closed = True


async def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "synthetic condition did not become true"
        await asyncio.sleep(0.005)


def saved_record(repo, session_id, audio_dir):
    record = repo.get(session_id)
    assert record is not None
    audio = record["audio"]
    path = audio_dir / audio["relative_path"]
    assert audio["saved"] and path.is_file()
    assert audio["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert audio["duration_ms"] > 0
    return record


def test_online_ready_does_not_admit_pcm_until_retained_offline_generation_is_ready(monkeypatch, isolated_prewarm):
    async def scenario():
        runtime = isolated_prewarm
        online, ws, repo = OnlineRecognizer(), ControlledWebSocket(), InMemoryAsrLiveSessionRepository()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        pcm = [struct.pack("<f", value) * 1600 for value in (0.1, 0.2)]
        task = asyncio.create_task(asr_stream.handle_stream(
            ws, "prewarm-buffer", asr_live_repo=repo, audio_asset_data_dir=runtime.audio_dir,
        ))
        try:
            await ws.messages.put({"bytes": pcm[0]})
            await ws.messages.put({"bytes": pcm[1]})
            await wait_until(lambda: runtime.owner.process_start_count == 1 and len(online.ready_calls) == 1)
            await asyncio.sleep(0.03)
            assert [e["event_type"] for e in ws.sent] == ["asr_starting"]
            assert online.pcm == []
            runtime.gate.touch()
            await wait_until(lambda: any(e["event_type"] == "asr_ready" for e in ws.sent))
            await wait_until(lambda: online.pcm == pcm)
            await ws.messages.put({"text": "END"})
            await task
            record = saved_record(repo, "prewarm-buffer", runtime.audio_dir)
            assert record["audio"]["duration_ms"] == 200
            assert len([e for e in ws.sent if e["event_type"] == "asr_ready"]) == 1
            assert runtime.owner.process_start_count == 1
            assert runtime.owner.status()["active_meeting_lease_count"] == 0
        finally:
            runtime.gate.touch()
            if not task.done():
                await ws.messages.put({"text": "END"})
                await task

    asyncio.run(scenario())


@pytest.mark.parametrize("terminal", ["END", "disconnect", "cancel", "invalid_pcm"])
def test_offline_readiness_terminal_saves_pcm_and_does_not_stop_shared_owner(monkeypatch, isolated_prewarm, terminal):
    async def scenario():
        runtime = isolated_prewarm
        online, ws, repo = OnlineRecognizer(), ControlledWebSocket(), InMemoryAsrLiveSessionRepository()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(
            ws, "prewarm-terminal", asr_live_repo=repo, audio_asset_data_dir=runtime.audio_dir,
        ))
        await ws.messages.put({"bytes": struct.pack("<f", 0.25) * 1600})
        await wait_until(lambda: runtime.owner.process_start_count == 1 and len(online.ready_calls) == 1)
        await asyncio.sleep(0.03)
        began = time.monotonic()
        if terminal == "cancel":
            task.cancel()
        elif terminal == "invalid_pcm":
            await ws.messages.put({"bytes": b"x"})
        else:
            await ws.messages.put({"text": "END"} if terminal == "END" else WebSocketDisconnect())
        try:
            await task
        except asyncio.CancelledError:
            assert terminal == "cancel"
        assert time.monotonic() - began < 0.5, "readiness observation must not wait another startup budget at stop"
        record = saved_record(repo, "prewarm-terminal", runtime.audio_dir)
        expected_reason = (
            "asr_not_ready_at_stop" if terminal == "END"
            else "audio_payload_alignment" if terminal == "invalid_pcm"
            else "stream_interrupted"
        )
        assert expected_reason in record["degradation_reasons"]
        assert not any(e["event_type"] == "transcript_final" for e in record["events"])
        assert not any(e["event_type"] == "asr_ready" for e in ws.sent)
        assert online.pcm == [] and online.abort_calls == 1 and online.finalize_calls == 0
        status = runtime.owner.status()
        assert status["process_running"] and not status["closed"]
        assert status["active_meeting_lease_count"] == 0 and runtime.owner.process_start_count == 1

    asyncio.run(scenario())


def test_offline_readiness_uses_remaining_online_deadline_and_preserves_pcm(monkeypatch, isolated_prewarm):
    async def scenario():
        runtime = isolated_prewarm
        online, ws, repo = OnlineRecognizer(ready_delay=0.08), ControlledWebSocket(), InMemoryAsrLiveSessionRepository()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        monkeypatch.setattr(asr_stream, "ASR_READY_TIMEOUT_S", 0.12)
        offline_budgets = []
        get_waiter = asr_refiner.get_refiner_readiness_waiter

        def observing_waiter(session_id):
            waiter = get_waiter(session_id)

            def wait(remaining, cancelled):
                offline_budgets.append(remaining)
                return waiter(remaining, cancelled)

            return wait

        monkeypatch.setattr(asr_stream, "get_refiner_readiness_waiter", observing_waiter)
        began = time.monotonic()
        task = asyncio.create_task(asr_stream.handle_stream(
            ws, "prewarm-timeout", asr_live_repo=repo, audio_asset_data_dir=runtime.audio_dir,
        ))
        await ws.messages.put({"bytes": struct.pack("<f", -0.25) * 1600})
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
            elapsed = time.monotonic() - began
            assert 0.1 <= elapsed < 0.2
            assert len(offline_budgets) == 1 and 0 < offline_budgets[0] < 0.05
            assert ws.sent[-1]["error_code"] == "asr_ready_timeout"
            assert not any(e["event_type"] == "asr_ready" for e in ws.sent)
            record = saved_record(repo, "prewarm-timeout", runtime.audio_dir)
            assert "asr_ready_timeout" in record["degradation_reasons"]
            assert online.pcm == [] and online.abort_calls == 1
            assert runtime.owner.process_start_count == 1 and runtime.owner.status()["process_running"]
        finally:
            if not task.done():
                await ws.messages.put({"text": "END"})
                await task

    asyncio.run(scenario())


@pytest.mark.parametrize("policy", ["online_only", "on_demand"])
def test_other_refiner_policies_keep_online_readiness(monkeypatch, isolated_prewarm, policy):
    async def scenario():
        monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, policy)
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "other-policy"))
        await wait_until(lambda: any(e["event_type"] == "asr_ready" for e in ws.sent))
        await ws.messages.put({"text": "END"})
        await task
        assert isolated_prewarm.owner.process_start_count == 0

    asyncio.run(scenario())


def test_passive_waiter_binds_one_generation_and_cancellation_does_not_retry(isolated_prewarm):
    runtime = isolated_prewarm
    assert asr_refiner.retain_refiner_for_meeting("passive")
    waiter = asr_refiner.get_refiner_readiness_waiter("passive")
    assert waiter is not None
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(waiter, 1.0, cancel)
        time.sleep(0.03)
        cancel.set()
        assert future.result(timeout=0.3) is False
    assert runtime.owner.process_start_count == 1
    assert runtime.owner.status()["process_running"]
    runtime.gate.touch()
    assert waiter(1.0, threading.Event()) is True
    original = runtime.owner._generation
    try:
        with runtime.owner._lock:
            runtime.owner._generation = SimpleNamespace()
        assert waiter(0.1, threading.Event()) is False
        assert runtime.owner.process_start_count == 1
    finally:
        runtime.owner._generation = original


def test_failed_prewarm_waiter_does_not_start_another_generation(monkeypatch, isolated_prewarm):
    owner = isolated_prewarm.owner
    owner._recovery_required = True
    owner._last_error_code = "worker_timeout"
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_args, **_kwargs: pytest.fail("observer must not spawn"))
    assert asr_refiner.retain_refiner_for_meeting("failed")
    waiter = asr_refiner.get_refiner_readiness_waiter("failed")
    assert waiter is not None and waiter(0.1, threading.Event()) is False
    assert owner.process_start_count == 0 and owner._last_error_code == "worker_timeout"


@pytest.mark.parametrize("invalid", [False, True])
def test_native_identity_and_flush_stay_ordered_across_offline_preparation(monkeypatch, isolated_prewarm, invalid):
    async def scenario():
        runtime = isolated_prewarm
        online, ws, repo = OnlineRecognizer(), ControlledWebSocket(), InMemoryAsrLiveSessionRepository()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        pcm = struct.pack("<f", 0.0) * 4800
        first = encode_native_pcm_v2_frame(
            track_id="microphone", capture_epoch=7, sequence=1, timestamp_ms=1000, pcm=pcm,
        )
        second = encode_native_pcm_v2_frame(
            track_id="microphone", capture_epoch=7, sequence=2, timestamp_ms=1300, pcm=pcm,
        )
        task = asyncio.create_task(asr_stream.handle_stream(
            ws, "prewarm-native", asr_live_repo=repo, audio_asset_data_dir=runtime.audio_dir,
            audio_source="tauri_native_mic", pcm_protocol="native_pcm_v2", native_track_id="microphone", native_capture_epoch=7,
        ))
        try:
            await ws.messages.put({"bytes": first})
            await ws.messages.put({"text": "FLUSH"})
            await wait_until(lambda: runtime.owner.process_start_count == 1 and len(online.ready_calls) == 1)
            await asyncio.sleep(0.03)
            assert online.pcm == []
            if invalid:
                await ws.messages.put({"bytes": first})  # Duplicate sequence, not a second valid recording.
                await task
                record = saved_record(repo, "prewarm-native", runtime.audio_dir)
                assert "native_pcm_sequence_invalid" in record["degradation_reasons"]
                assert online.abort_calls == 1 and online.finalize_calls == 0
                assert record["audio"]["duration_ms"] == 300
                assert runtime.owner.status()["process_running"]
                assert not any(e["event_type"] == "asr_ready" for e in ws.sent)
            else:
                await ws.messages.put({"bytes": second})
                runtime.gate.touch()
                await wait_until(lambda: len(online.pcm) == 2)
                await ws.messages.put({"text": "END"})
                await task
                record = saved_record(repo, "prewarm-native", runtime.audio_dir)
                assert record["audio"]["duration_ms"] == 600
                partials = [e for e in ws.sent if e["event_type"] == "partial"]
                assert [e["track_sequence"] for e in partials] == [1, 2]
                assert [e["source_timestamp_ms"] for e in partials] == [1000, 1300]
                assert all(e["capture_epoch"] == 7 and e["source_track"] == "microphone" for e in partials)
                assert online.pcm == [pcm, pcm]
            assert runtime.owner.status()["active_meeting_lease_count"] == 0
            assert runtime.owner.process_start_count == 1
        finally:
            runtime.gate.touch()
            if not task.done():
                await ws.messages.put({"text": "END"})
                await task

    asyncio.run(scenario())


def test_warmup_buffer_overflow_preserves_full_recording_and_truthful_degradation(monkeypatch, isolated_prewarm):
    async def scenario():
        runtime = isolated_prewarm
        online, ws, repo = OnlineRecognizer(), ControlledWebSocket(), InMemoryAsrLiveSessionRepository()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        monkeypatch.setattr(asr_stream, "ASR_READY_BUFFER_MAX_CHUNKS", 1)
        task = asyncio.create_task(asr_stream.handle_stream(
            ws, "prewarm-overflow", asr_live_repo=repo, audio_asset_data_dir=runtime.audio_dir,
        ))
        pcm = [struct.pack("<f", value) * 1600 for value in (0.1, 0.2)]
        try:
            for frame in pcm:
                await ws.messages.put({"bytes": frame})
            await wait_until(lambda: runtime.owner.process_start_count == 1 and len(online.ready_calls) == 1)
            await asyncio.sleep(0.03)
            runtime.gate.touch()
            await wait_until(lambda: len(online.pcm) == 1)
            await ws.messages.put({"text": "END"})
            await task
            record = saved_record(repo, "prewarm-overflow", runtime.audio_dir)
            assert record["audio"]["duration_ms"] == 200
            assert "asr_ready_buffer_overflow" in record["degradation_reasons"]
            assert online.pcm == [pcm[0]]
            assert not any(e["event_type"] == "transcript_final" for e in record["events"])
        finally:
            runtime.gate.touch()
            if not task.done():
                await ws.messages.put({"text": "END"})
                await task

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["sherpa_onnx_realtime", "synthetic_realtime_asr"])
def test_other_recognizers_do_not_wait_for_the_offline_refiner(monkeypatch, isolated_prewarm, provider):
    async def scenario():
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        online.provider = provider
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "other-recognizer"))
        await wait_until(lambda: any(e["event_type"] == "asr_ready" for e in ws.sent))
        assert not isolated_prewarm.owner.status()["process_ready"]
        await ws.messages.put({"text": "END"})
        await task

    asyncio.run(scenario())


def test_unconfigured_optional_refiner_does_not_block_online_readiness(monkeypatch, isolated_prewarm):
    async def scenario():
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "unavailable"})
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "unconfigured-refiner"))
        await wait_until(lambda: any(e["event_type"] == "asr_ready" for e in ws.sent))
        await ws.messages.put({"text": "END"})
        await task
        assert isolated_prewarm.owner.process_start_count == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", sorted(asr_refiner.ONLINE_FINAL_RESOURCE_GUARD_ERRORS))
def test_confirmed_prewarm_resource_refusal_admits_online_without_claiming_offline_ready(
    monkeypatch, isolated_prewarm, reason,
):
    async def scenario():
        owner = isolated_prewarm.owner
        owner._recovery_required = True
        owner._last_error_code = reason
        monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("latched resource refusal cannot retry"))
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "resource-admission"))
        await wait_until(lambda: any(event["event_type"] == "asr_ready" for event in ws.sent))
        ready = next(event for event in ws.sent if event["event_type"] == "asr_ready")
        assert ready["ready"] is True
        assert ready["offline_refinement_ready"] is False
        assert ready["offline_refinement_reason"] == reason
        assert owner.status()["process_ready"] is False
        assert owner.process_start_count == 0
        pcm = struct.pack("<f", 0.2) * 1600
        await ws.messages.put({"bytes": pcm})
        await wait_until(lambda: online.pcm == [pcm])
        await ws.messages.put({"text": "END"})
        await task
        assert owner.status()["active_meeting_lease_count"] == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("reason", ["asr_resource_probe_failed", "worker_timeout", "worker_model_load_failed", "worker_write_failed"])
def test_non_resource_offline_readiness_failure_still_refuses_online_admission(
    monkeypatch, isolated_prewarm, reason,
):
    async def scenario():
        owner = isolated_prewarm.owner
        owner._recovery_required = True
        owner._last_error_code = reason
        monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("failed owner cannot implicitly retry"))
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "bad-offline"))
        await ws.messages.put({"bytes": struct.pack("<f", 0.2) * 1600})
        await asyncio.wait_for(task, timeout=0.5)
        assert not any(event["event_type"] == "asr_ready" for event in ws.sent)
        assert ws.sent[-1]["error_code"] == "asr_ready_timeout"
        assert online.pcm == [] and owner.process_start_count == 0

    asyncio.run(scenario())


def test_refiner_resource_observer_requires_active_owner_and_a_real_resource_failure(isolated_prewarm):
    owner = isolated_prewarm.owner
    owner._last_error_code = "asr_capacity_unavailable"
    assert owner.resource_refusal_for_meeting("not-retained") is None
    owner._active_meeting_leases.add("retained")
    assert owner.resource_refusal_for_meeting("retained") is None, "a stale error without a failed owner is not a resource refusal"
    owner._recovery_required = True
    assert owner.resource_refusal_for_meeting("retained") == "asr_capacity_unavailable"
    owner.release_for_meeting("retained")
    assert owner.resource_refusal_for_meeting("retained") is None
    owner.shutdown()
    owner._active_meeting_leases.add("retained")
    assert owner.resource_refusal_for_meeting("retained") is None


def test_offline_resource_refusal_cannot_claim_an_unready_online_recognizer(monkeypatch, isolated_prewarm):
    async def scenario():
        owner = isolated_prewarm.owner
        owner._recovery_required = True
        owner._last_error_code = "asr_capacity_unavailable"
        monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("resource failure cannot retry"))
        online, ws = OnlineRecognizer(), ControlledWebSocket()
        online.wait_ready = lambda _timeout: False
        monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
        task = asyncio.create_task(asr_stream.handle_stream(ws, "online-not-ready"))
        await ws.messages.put({"bytes": struct.pack("<f", 0.2) * 1600})
        await asyncio.wait_for(task, timeout=0.5)
        assert not any(event["event_type"] == "asr_ready" for event in ws.sent)
        assert ws.sent[-1]["error_code"] == "asr_ready_timeout"
        assert online.pcm == []

    asyncio.run(scenario())


def test_native_prewarm_capacity_guard_keeps_two_formal_originals_and_public_l2_eligibility(
    monkeypatch, isolated_prewarm, tmp_path,
):
    """Actual native WS/SQLite path with controlled online text and real guard, no models."""
    guard = asr_refiner.resource_guard
    monkeypatch.setattr(guard, "read_memory_snapshot", lambda: guard.MemorySnapshot(4 * guard.GIB, 4 * guard.GIB, 1))
    monkeypatch.setattr(asr_refiner.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("cold capacity guard must precede spawning"))
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", asr_refiner.refine_pcm_f32)
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "DiarizationRuntime", lambda *_a, **_kw: SimpleNamespace(
        start=lambda: None, submit_pcm=lambda _pcm: None, observe_final=lambda _event: None,
        finish=lambda: None, abort=lambda: None,
    ))
    texts = ["支付成功但短信失败，是否要回滚支付？", "权益先幂等生效，短信单独重试，页面显示已确认状态。"]

    class ClosedOnlineRecognizer(OnlineRecognizer):
        def __init__(self):
            super().__init__()
            self.flush_calls = []
            self.utterance_count = 0

        def recognize_chunk(self, pcm):
            self.pcm.append(pcm)
            self._seq += 1
            if struct.unpack("<f", pcm[:4])[0] <= asr_stream.VAD_SILENCE_RMS_THRESHOLD:
                return []
            self.utterance_count += 1
            return [{"event_type": "partial", "partial_semantics": "incremental_chunk",
                     "segment_id": f"online-{self.utterance_count}", "text": texts[self.utterance_count - 1],
                     "start_ms": (self._seq - 1) * 300, "end_ms": self._seq * 300, "authoritative": False}]

        def flush_utterance(self, boundary_id, timeout=1.25):
            self.flush_calls.append(boundary_id)
            return []

    online = ClosedOnlineRecognizer()
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: online)
    application = create_app(data_dir=tmp_path / "public-runtime", prewarm_funasr=False, semantic_projection_mode="llm_first")
    client = TestClient(application)  # No lifespan or executor/provider calls.
    mid = "prewarm-resource-public"
    owner = isolated_prewarm.owner
    with client.websocket_connect(f"/live/asr/stream/ws/{mid}?audio_source=browser_live_mic") as ws:
        while (event := json.loads(ws.receive_text()))["event_type"] != "asr_ready":
            assert event["event_type"] == "asr_starting"
        assert event["offline_refinement_ready"] is False
        assert event["offline_refinement_reason"] == "asr_capacity_unavailable"
        assert owner.process_start_count == 0 and owner.status()["recovery_required"]
        assert owner.status()["resource"]["last_check"]["required_commit_bytes"] == 8 * guard.GIB
        speech, silence = (struct.pack("<f", value) * 4800 for value in (0.05, 0.0))
        snapshots = []
        for text in texts:
            for pcm in (speech, silence, silence, silence):
                ws.send_bytes(pcm)
            for _ in range(8):
                final = json.loads(ws.receive_text())
                if final["event_type"] == "final":
                    break
            assert final["event_type"] == "final" and final["text"] == text
            assert final["final_source"] == "local_realtime_online_final"
            assert final["refinement_status"] == "failed" and final["refinement_reason"] == "asr_capacity_unavailable"
            snapshots.append(client.get(f"/v2/meetings/{mid}/snapshot").json())
        assert [segment["text"] for segment in snapshots[-1]["segments"]] == texts
        assert snapshots[-1]["segments"][0] == snapshots[0]["segments"][0]
        assert snapshots[-1]["runtime"]["ai"]["capabilities"]["transcript"]["label"] == "在线正文已保存，精修未完成"
        record = application.state.asr_live_repository.get(mid)
        assert _realtime_correction_blockers(record) == [], "actual prewarm closed finals remain eligible for independent native L2"
        assert asr_refiner.realtime_refiner_policy()["mode"] == "prewarm"
        assert len(set(online.flush_calls)) == 2 and owner.process_start_count == 0
        ws.send_text("END")
        with pytest.raises(WebSocketDisconnect):
            while True:
                ws.receive_text()
    assert owner.status()["active_meeting_lease_count"] == 0

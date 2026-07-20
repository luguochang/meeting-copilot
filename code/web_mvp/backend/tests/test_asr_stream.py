"""Tests for the realtime ASR WebSocket stream."""

import asyncio
import hashlib
import json
import math
from pathlib import Path
import struct
import threading
import time
import wave
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.asr_live_repository import InMemoryAsrLiveSessionRepository
from meeting_copilot_web_mvp.canonical_transcript import project_canonical_transcript


@pytest.fixture(autouse=True)
def _force_fake_recognizer(monkeypatch):
    """Force the Fake recognizer so WS tests are deterministic and don't spawn
    the real sherpa/funasr sidecar (tested separately)."""
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)


def test_asr_stream_ws_emits_partial_per_chunk_and_final_on_end():
    client = TestClient(create_app(allow_fake_asr_fallback=True))
    with client.websocket_connect("/live/asr/stream/ws/sess_stream_1") as ws:
        ws.send_bytes(b"\x00" * 3200)
        p1 = json.loads(ws.receive_text())
        ws.send_bytes(b"\x00" * 1600)
        p2 = json.loads(ws.receive_text())
        ws.send_text("END")
        final = json.loads(ws.receive_text())
    assert p1["event_type"] == "partial"
    assert p1["segment_id"] == "stream_seg_sess_stream_1"
    assert "3200" in p1["text"]
    assert p2["event_type"] == "partial"
    assert "1600" in p2["text"]
    assert final["event_type"] == "final"
    assert final["segment_id"] == "stream_seg_sess_stream_1"
    assert final["confidence"] == 0.9


def test_asr_stream_ws_sessions_are_independent():
    client = TestClient(create_app(allow_fake_asr_fallback=True))
    with client.websocket_connect("/live/asr/stream/ws/sess_a") as ws:
        ws.send_bytes(b"\x01" * 100)
        p = json.loads(ws.receive_text())
        assert p["segment_id"] == "stream_seg_sess_a"


def test_asr_stream_reports_invalid_float32_payload_and_preserves_recording(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, allow_fake_asr_fallback=True))

    with client.websocket_connect("/live/asr/stream/ws/invalid_float32_session?audio_source=browser_live_mic") as ws:
        ws.send_bytes(struct.pack("<f", math.nan))
        error = json.loads(ws.receive_text())
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()

    assert error["event_type"] == "provider_error"
    assert error["error_code"] == "audio_payload_non_finite"
    assert error["recoverable"] is True
    assert error["recording_saved"] is True
    record = client.get("/live/asr/sessions/invalid_float32_session/events").json()
    assert "audio_payload_non_finite" in record["degradation_reasons"]
    assert record["audio"]["saved"] is True
    assert record["audio"]["duration_ms"] == 0


def test_asr_readiness_buffer_covers_the_cold_start_timeout_without_unbounded_memory():
    buffered_audio_seconds = asr_stream.ASR_READY_BUFFER_MAX_CHUNKS * 0.3

    assert buffered_audio_seconds >= asr_stream.ASR_READY_TIMEOUT_S
    assert asr_stream.ASR_READY_BUFFER_MAX_CHUNKS <= 300


def test_asr_stream_waits_for_recognizer_ready_before_accepting_audio(monkeypatch):
    class ReadyRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self.ready_calls = []
            self.recognized = False
            self._seq = 0

        def wait_ready(self, timeout):
            self.ready_calls.append(timeout)
            return True

        def recognize_chunk(self, pcm):
            self.recognized = True
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": "ready_gate_segment",
                    "text": "模型已就绪",
                    "confidence": 0.9,
                }
            ]

        def finalize(self):
            return []

        def abort(self):
            return None

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]
            self.sent = []
            self.closed = False

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            self.closed = True

    recognizer = ReadyRecognizer()
    ws = CapturingWebSocket()
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(asr_stream.handle_stream(ws, "ready_gate_session"))

    assert recognizer.ready_calls
    assert recognizer.recognized is True
    assert [event["event_type"] for event in ws.sent[:3]] == [
        "asr_starting",
        "asr_ready",
        "partial",
    ]
    assert ws.sent[1]["ready"] is True


def test_asr_stream_marks_controller_healthy_after_real_recognizer_ready(monkeypatch):
    class ReadyRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []
        _seq = 0

        def wait_ready(self, timeout):
            return True

        def recognize_chunk(self, pcm):
            self._seq += 1
            return []

        def finalize(self):
            return []

        def abort(self):
            return None

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [{"text": "END"}]
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    class HealthySpy:
        def __init__(self):
            self.recovered = []

        def recover(self, reason):
            self.recovered.append(reason)

    recognizer = ReadyRecognizer()
    websocket = CapturingWebSocket()
    degradation = HealthySpy()
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)
    monkeypatch.setattr(asr_stream, "get_degradation_controller", lambda: degradation)

    asyncio.run(asr_stream.handle_stream(websocket, "ready_recovery_session"))

    assert degradation.recovered == ["asr_ready"]


def test_asr_stream_reports_provider_error_when_recognizer_never_ready(monkeypatch):
    class NeverReadyRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self.recognize_calls = 0
            self.abort_calls = 0

        def wait_ready(self, timeout):
            return False

        def recognize_chunk(self, pcm):
            self.recognize_calls += 1
            return []

        def finalize(self):
            return []

        def abort(self):
            self.abort_calls += 1

    class CapturingWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def accept(self):
            return None

        async def receive(self):
            await asyncio.sleep(1)
            return {"text": "END"}

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            self.closed = True

    recognizer = NeverReadyRecognizer()
    ws = CapturingWebSocket()
    monkeypatch.setattr(asr_stream, "ASR_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(asr_stream.handle_stream(ws, "never_ready_session"))

    assert [event["event_type"] for event in ws.sent] == [
        "asr_starting",
        "provider_error",
    ]
    assert ws.sent[-1]["error_code"] == "asr_ready_timeout"
    assert recognizer.recognize_calls == 0
    assert recognizer.abort_calls == 1
    assert ws.closed is True


def test_asr_stream_persists_audio_when_end_arrives_before_asr_ready(monkeypatch, tmp_path):
    class NeverReadyUntilAbortRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self.abort_event = threading.Event()
            self.abort_calls = 0
            self.recognize_calls = 0

        def wait_ready(self, timeout):
            self.abort_event.wait(timeout)
            return False

        def recognize_chunk(self, pcm):
            self.recognize_calls += 1
            return []

        def finalize(self):
            return []

        def abort(self):
            self.abort_calls += 1
            self.abort_event.set()

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [
                {"bytes": struct.pack("<f", 0.25) * 3_200},
                {"text": "END"},
            ]
            self.sent = []
            self.closed = False

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            self.closed = True

    monkeypatch.setattr(asr_stream, "ASR_READY_TIMEOUT_S", 0.05)
    recognizer = NeverReadyUntilAbortRecognizer()
    ws = CapturingWebSocket()
    repo = InMemoryAsrLiveSessionRepository()
    session_id = "asr_not_ready_audio_session"
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            session_id,
            asr_live_repo=repo,
            audio_source="browser_live_mic",
            audio_asset_data_dir=tmp_path,
        )
    )

    record = repo.get(session_id)
    audio = record["audio"]
    audio_path = Path(tmp_path, audio["relative_path"])
    assert audio["saved"] is True
    assert audio["duration_ms"] > 0
    assert audio["file_size_bytes"] > 44
    assert audio_path.is_file()
    assert hashlib.sha256(audio_path.read_bytes()).hexdigest() == audio["sha256"]
    assert "asr_not_ready_at_stop" in record["degradation_reasons"]
    assert not any(event["event_type"] == "transcript_final" for event in record["events"])
    assert recognizer.recognize_calls == 0
    assert recognizer.abort_calls == 1
    assert ws.closed is True


def test_asr_stream_persists_audio_when_asr_readiness_times_out(monkeypatch, tmp_path):
    class NeverReadyRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self.abort_calls = 0
            self.abort_event = threading.Event()

        def wait_ready(self, timeout):
            self.abort_event.wait(timeout)
            return False

        def recognize_chunk(self, pcm):
            raise AssertionError("ASR must not consume audio after readiness timeout")

        def finalize(self):
            raise AssertionError("ASR must not finalize after readiness timeout")

        def abort(self):
            self.abort_calls += 1
            self.abort_event.set()

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [{"bytes": struct.pack("<f", -0.25) * 1_600}]
            self.sent = []
            self.closed = False

        async def accept(self):
            return None

        async def receive(self):
            if self.messages:
                return self.messages.pop(0)
            await asyncio.sleep(1)
            return {"text": "END"}

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            self.closed = True

    monkeypatch.setattr(asr_stream, "ASR_READY_TIMEOUT_S", 0.01)
    recognizer = NeverReadyRecognizer()
    ws = CapturingWebSocket()
    repo = InMemoryAsrLiveSessionRepository()
    session_id = "asr_ready_timeout_audio_session"
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            session_id,
            asr_live_repo=repo,
            audio_source="browser_live_mic",
            audio_asset_data_dir=tmp_path,
        )
    )

    record = repo.get(session_id)
    audio = record["audio"]
    audio_path = Path(tmp_path, audio["relative_path"])
    assert audio["saved"] is True
    assert audio["duration_ms"] > 0
    assert audio["file_size_bytes"] > 44
    assert audio_path.is_file()
    assert hashlib.sha256(audio_path.read_bytes()).hexdigest() == audio["sha256"]
    assert "asr_ready_timeout" in record["degradation_reasons"]
    assert not any(event["event_type"] == "transcript_final" for event in record["events"])
    assert recognizer.abort_calls == 1
    assert ws.sent[-1]["error_code"] == "asr_ready_timeout"
    assert ws.closed is True


def test_asr_stream_abnormal_disconnect_aborts_recognizer_once_via_worker_thread(monkeypatch):
    class ResourceRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self._seq = 0
            self.finalize_calls = 0
            self.abort_calls = 0
            self.abort_thread = None

        def recognize_chunk(self, pcm):
            return []

        def finalize(self):
            self.finalize_calls += 1
            return []

        def abort(self):
            self.abort_calls += 1
            self.abort_thread = threading.current_thread().name

    class DisconnectingWebSocket:
        async def accept(self):
            return None

        async def receive(self):
            raise RuntimeError("disconnect")

        async def send_text(self, payload):
            return None

        async def close(self):
            return None

    recognizer = ResourceRecognizer()
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(asr_stream.handle_stream(DisconnectingWebSocket(), "sess_abort_once"))

    assert recognizer.abort_calls == 1
    assert recognizer.finalize_calls == 0
    assert recognizer.abort_thread is not None
    assert recognizer.abort_thread.startswith("meeting-stream-sess_abort_once")


def test_asr_stream_end_finalizes_via_worker_thread_without_abort(monkeypatch):
    class ResourceRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self._seq = 0
            self.finalize_calls = 0
            self.abort_calls = 0
            self.finalize_thread = None

        def recognize_chunk(self, pcm):
            return []

        def finalize(self):
            self.finalize_calls += 1
            self.finalize_thread = threading.current_thread().name
            return []

        def abort(self):
            self.abort_calls += 1

    class EndingWebSocket:
        def __init__(self):
            self._messages = [{"text": "END"}]

        async def accept(self):
            return None

        async def receive(self):
            return self._messages.pop(0)

        async def send_text(self, payload):
            return None

        async def close(self):
            return None

    recognizer = ResourceRecognizer()
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: recognizer)

    asyncio.run(asr_stream.handle_stream(EndingWebSocket(), "sess_finalize_thread"))

    assert recognizer.finalize_calls == 1
    assert recognizer.abort_calls == 0
    assert recognizer.finalize_thread is not None
    assert recognizer.finalize_thread.startswith("meeting-stream-sess_finalize_thread")


def test_asr_stream_slow_audio_and_final_callbacks_do_not_block_event_loop(monkeypatch):
    class SlowRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self._seq = 0

        def recognize_chunk(self, pcm):
            time.sleep(0.08)
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": "slow-segment",
                    "text": "实时识别中的一段技术讨论",
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.9,
                }
            ]

        def finalize(self):
            time.sleep(0.08)
            return [
                {
                    "event_type": "final",
                    "segment_id": "slow-segment",
                    "text": "实时识别中的一段技术讨论已经确认。",
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.9,
                }
            ]

        def abort(self):
            return None

    class WebSocket:
        def __init__(self):
            self._messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]

        async def accept(self):
            return None

        async def receive(self):
            return self._messages.pop(0)

        async def send_text(self, payload):
            return None

        async def close(self):
            return None

    final_callback_threads = []

    def slow_final_callback(_event):
        final_callback_threads.append(threading.current_thread().name)
        time.sleep(0.08)

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: SlowRecognizer(sid))
    live_repo = InMemoryAsrLiveSessionRepository()

    async def run():
        stream_task = asyncio.create_task(
            asr_stream.handle_stream(
                WebSocket(),
                "sess_slow_hot_path",
                asr_live_repo=live_repo,
                on_final_committed=slow_final_callback,
            )
        )
        heartbeat_ticks = 0
        while not stream_task.done():
            await asyncio.sleep(0.01)
            heartbeat_ticks += 1
        await stream_task
        return heartbeat_ticks

    heartbeat_ticks = asyncio.run(run())

    assert heartbeat_ticks >= 12
    assert final_callback_threads
    assert final_callback_threads[0].startswith("meeting-stream-sess_slow_hot_path")


def test_asr_stream_reconnect_preserves_audio_and_prior_final_events(monkeypatch, tmp_path):
    connection_number = 0

    class ReconnectRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id, connection_id):
            self.session_id = session_id
            self.connection_id = connection_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "final",
                    "segment_id": f"reconnect_{self.connection_id}",
                    "text": f"第{self.connection_id}段会议内容需要保留。",
                    "start_ms": (self.connection_id - 1) * 300,
                    "end_ms": self.connection_id * 300,
                    "confidence": 0.9,
                }
            ]

        def finalize(self):
            return []

    def recognizer_factory(session_id):
        nonlocal connection_number
        connection_number += 1
        return ReconnectRecognizer(session_id, connection_number)

    class ReconnectWebSocket:
        def __init__(self, *, interrupted):
            self.messages = [{"bytes": struct.pack("<f", 0.1) * 4_800}]
            if not interrupted:
                self.messages.append({"text": "END"})
            self.interrupted = interrupted
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            if self.messages:
                return self.messages.pop(0)
            raise RuntimeError("simulated websocket disconnect")

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", recognizer_factory)
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    repo = InMemoryAsrLiveSessionRepository()
    sid = "reconnect_audio_session"

    asyncio.run(
        asr_stream.handle_stream(
            ReconnectWebSocket(interrupted=True),
            sid,
            asr_live_repo=repo,
            audio_source="browser_live_mic",
            audio_asset_data_dir=tmp_path,
        )
    )
    interrupted_record = repo.get(sid)
    assert interrupted_record["audio"]["saved"] is True
    assert "stream_interrupted" in interrupted_record["degradation_reasons"]

    asyncio.run(
        asr_stream.handle_stream(
            ReconnectWebSocket(interrupted=False),
            sid,
            asr_live_repo=repo,
            audio_source="browser_live_mic",
            audio_asset_data_dir=tmp_path,
        )
    )
    completed_record = repo.get(sid)
    final_texts = [
        (event.get("payload") or {}).get("text")
        for event in completed_record["events"]
        if event.get("event_type") == "transcript_final"
    ]
    assert final_texts == ["第1段会议内容需要保留。", "第2段会议内容需要保留。"]
    assert completed_record["audio"]["duration_ms"] == 600

    audio_path = tmp_path / completed_record["audio"]["relative_path"]
    with wave.open(str(audio_path), "rb") as wav_file:
        assert wav_file.getnframes() == 9_600


def test_asr_stream_recording_setup_failure_aborts_recognizer(monkeypatch, tmp_path):
    class SetupRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self):
            self.aborted = False

        def recognize_chunk(self, _pcm):
            return []

        def finalize(self):
            return []

        def abort(self):
            self.aborted = True

    class SetupWebSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        async def accept(self):
            return None

        async def receive(self):
            raise AssertionError("setup failure must stop before receiving audio")

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            self.closed = True

    recognizer = SetupRecognizer()
    websocket = SetupWebSocket()
    rollback_calls = []
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)

    asyncio.run(
        asr_stream.handle_stream(
            websocket,
            "recording-setup-failure",
            audio_asset_data_dir=tmp_path,
            on_audio_recording_started=lambda _metadata: (_ for _ in ()).throw(
                RuntimeError("recording lease is already owned")
            ),
            on_audio_recording_setup_failed=lambda: rollback_calls.append("rolled_back"),
        )
    )

    assert recognizer.aborted is True
    assert websocket.closed is True
    assert websocket.sent[-1]["error_code"] == "recording_resume_failed"
    assert websocket.sent[-1]["recoverable"] is True
    assert rollback_calls == ["rolled_back"]


def test_app_recording_setup_failure_releases_capture_lease(monkeypatch, tmp_path):
    class FailingWriter:
        def __init__(self, **_kwargs):
            raise ValueError("simulated writer replay conflict")

    monkeypatch.setattr(asr_stream, "RealtimeWavAssetWriter", FailingWriter)
    app = create_app(data_dir=tmp_path, allow_fake_asr_fallback=True)
    persistence = app.state.v2_persistence
    session_id = "recording-setup-lease-rollback"

    with TestClient(app) as client:
        with client.websocket_connect(f"/live/asr/stream/ws/{session_id}?audio_source=browser_live_mic") as websocket:
            error = json.loads(websocket.receive_text())
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_text()

        recording = persistence.get_recording_session(
            session_id,
            track="microphone",
            epoch=0,
        )
        resumed = persistence.begin_recording(
            meeting_id=session_id,
            track="microphone",
            epoch=0,
            source_type="browser_live_mic",
            sample_rate_hz=16_000,
            lease_owner="retry-after-setup-failure",
            lease_ms=30_000,
            now_ms=recording["updated_at_ms"] + 1,
        )

    assert error["error_code"] == "recording_resume_failed"
    assert recording["status"] == "interrupted"
    assert recording["lease_owner"] is None
    assert recording["error_class"] == "recording_setup_failed"
    assert resumed["status"] == "active"
    assert resumed["capture_generation"] == recording["capture_generation"] + 1


def test_asr_stream_claims_recording_before_replaying_existing_chunks(monkeypatch, tmp_path):
    session_id = "recording-restart-order"
    previous_writer = asr_stream.RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id=session_id,
        source_type="browser_live_mic",
    )
    previous_writer.write_float32_pcm(struct.pack("<f", 0.1) * (16_000 * 5))

    class ResumeRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []
        _seq = 0

        def recognize_chunk(self, _pcm):
            return []

        def finalize(self):
            return []

        def abort(self):
            raise AssertionError("successful resume must not abort")

    class ResumeWebSocket:
        def __init__(self):
            self.messages = [{"text": "END"}]
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    setup_order = []

    def recording_started(_metadata):
        setup_order.append("lease_claimed")

    def chunk_replayed(_chunk):
        assert setup_order == ["lease_claimed"]
        setup_order.append("chunk_replayed")

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: ResumeRecognizer())

    asyncio.run(
        asr_stream.handle_stream(
            ResumeWebSocket(),
            session_id,
            audio_source="browser_live_mic",
            audio_asset_data_dir=tmp_path,
            on_audio_recording_started=recording_started,
            on_audio_chunk_committed=chunk_replayed,
        )
    )

    assert setup_order[:2] == ["lease_claimed", "chunk_replayed"]


def test_asr_stream_ws_recognizer_is_pluggable(monkeypatch):
    from meeting_copilot_web_mvp import asr_stream

    class CustomRecognizer:
        def __init__(self, session_id):
            self.session_id = session_id

        def recognize_chunk(self, pcm):
            return [
                {
                    "event_type": "partial",
                    "segment_id": "custom",
                    "text": "custom",
                    "start_ms": 0,
                    "end_ms": 100,
                    "confidence": 0.5,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": "custom",
                    "text": "custom-final",
                    "start_ms": 0,
                    "end_ms": 100,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: CustomRecognizer(sid))
    client = TestClient(create_app(allow_fake_asr_fallback=True))
    with client.websocket_connect("/live/asr/stream/ws/sess_custom") as ws:
        ws.send_bytes(b"\x00" * 12)
        p = json.loads(ws.receive_text())
        ws.send_text("END")
        fin = json.loads(ws.receive_text())
    assert p["segment_id"] == "custom"
    assert fin["text"] == "custom-final"


def test_asr_stream_sends_no_cost_partial_hint_over_same_websocket():
    source = Path(asr_stream.__file__).read_text(encoding="utf-8")
    partial_branch = source[source.index("for ev in events:") :]
    partial_branch = partial_branch[: partial_branch.index('elif msg.get("text") == "END"')]

    assert "build_partial_hint_event" in source
    assert "partial_hint = build_partial_hint_event(ev)" in partial_branch
    assert "outgoing_events.append(partial_hint)" in partial_branch
    assert "for outgoing_event in outgoing_events:" in partial_branch
    assert "await websocket.send_text(json.dumps(outgoing_event" in partial_branch
    assert partial_branch.index("outgoing_events = [ev]") < partial_branch.index("outgoing_events.append(partial_hint)")
    assert partial_branch.index("outgoing_events.append(partial_hint)") < partial_branch.index(
        "for outgoing_event in outgoing_events:"
    )


def test_asr_stream_deduplicates_progressive_partial_hint_over_websocket(monkeypatch, tmp_path):
    class ProgressivePartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            texts = [
                "如果 P99 延迟超过九百毫秒",
                "如果 P99 延迟超过九百毫秒就要回滚",
                "如果 P99 延迟超过九百毫秒就要回滚，owner 张三确认",
            ]
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"risk_partial_{self._seq:03d}",
                    "text": texts[self._seq - 1],
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.8,
                }
            ]

        def finalize(self):
            return []

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: ProgressivePartialRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_progressive_partial_hint_dedupe"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        first_partial = json.loads(ws.receive_text())
        first_hint = json.loads(ws.receive_text())
        ws.send_bytes(b"\x00" * 3200)
        second_partial = json.loads(ws.receive_text())
        ws.send_bytes(b"\x00" * 3200)
        third_partial = json.loads(ws.receive_text())
        ws.send_text("END")

    assert first_partial["event_type"] == "partial"
    assert first_hint["event_type"] == "partial_hint_event"
    assert first_hint["payload"]["dedupe_key"] == "risk_confirmation:p99:latency_threshold"
    assert second_partial["event_type"] == "partial"
    assert third_partial["event_type"] == "partial"

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    persisted_hints = [event for event in body["events"] if event["event_type"] == "partial_hint_event"]
    assert len(persisted_hints) == 1


def test_asr_stream_recognizer_without_metadata_fails_closed_by_default(monkeypatch, tmp_path):
    class CustomRecognizerWithoutMetadata:
        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": "custom",
                    "text": "custom",
                    "start_ms": 0,
                    "end_ms": 100,
                    "confidence": 0.5,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": "custom",
                    "text": "custom-final",
                    "start_ms": 0,
                    "end_ms": 100,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: CustomRecognizerWithoutMetadata(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_missing_metadata"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        error = json.loads(ws.receive_text())

    assert error["event_type"] == "provider_error"
    assert error["error_code"] == "real_asr_sidecar_unavailable"
    assert error["provider_mode"] == "unknown"
    assert error["is_mock"] is True
    assert error["asr_fallback_used"] is True
    assert "recognizer_metadata_missing" in error["degradation_reasons"]

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 404


def test_asr_stream_fake_fallback_hard_fails_by_default_without_persisting_session(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_fake_fallback"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        error = json.loads(ws.receive_text())

    assert error["event_type"] == "provider_error"
    assert error["error_code"] == "real_asr_sidecar_unavailable"

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 404


def test_asr_stream_fake_fallback_can_be_enabled_explicitly_for_demo_tests(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path, allow_fake_asr_fallback=True))
    sid = "sess_fake_fallback"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["provider"] == "fake"
    assert body["provider_mode"] == "mock"
    assert body["is_mock"] is True
    assert body["asr_fallback_used"] is True
    assert "real_asr_sidecar_unavailable" in body["degradation_reasons"]
    assert body["event_source"]["provider"] == "fake"
    assert body["event_source"]["is_mock"] is True
    assert body["event_source"]["input_source"] == "mock"
    assert body["event_source"]["acceptance_eligible"] is False
    assert "mock_or_demo_session" in body["event_source"]["acceptance_blockers"]
    assert "asr_fallback_used" in body["event_source"]["acceptance_blockers"]

    list_response = client.get("/live/asr/sessions?include_demo=true")
    assert list_response.status_code == 200
    session = list_response.json()["sessions"][0]
    assert session["session_id"] == sid
    assert session["provider"] == "fake"
    assert session["provider_mode"] == "mock"
    assert session["is_mock"] is True
    assert session["asr_fallback_used"] is True
    assert session["event_source"]["acceptance_eligible"] is False


def test_asr_stream_empty_real_final_is_persisted_as_degraded_session(monkeypatch, tmp_path):
    class EmptyRealRecognizer:
        provider = "test_real_empty_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.7,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: EmptyRealRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_empty_real_final"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        ws.send_bytes(b"\x00" * 3200)
        partial = json.loads(ws.receive_text())
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert partial["event_type"] == "partial"
    assert partial["text"] == ""
    assert final["event_type"] == "final"
    assert final["text"] == ""

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["provider"] == "test_real_empty_asr"
    assert body["provider_mode"] == "real"
    assert body["is_mock"] is False
    assert body["asr_fallback_used"] is False
    assert "asr_final_empty" in body["degradation_reasons"]
    assert body["event_source"]["acceptance_eligible"] is False
    assert "degraded_asr_session" in body["event_source"]["acceptance_blockers"]
    assert [event["event_type"] for event in body["events"]] == ["evaluation_summary"]
    evaluation = body["events"][0]["payload"]
    assert evaluation["passes_minimum_gate"] is False
    assert evaluation["final_event_count"] == 0
    assert evaluation["end_of_stream_event_count"] == 1

    list_response = client.get("/live/asr/sessions")
    assert list_response.status_code == 200
    session = list_response.json()["sessions"][0]
    assert session["session_id"] == sid
    assert session["provider"] == "test_real_empty_asr"
    assert session["provider_mode"] == "real"
    assert session["is_mock"] is False
    assert session["final_count"] == 0
    assert "asr_final_empty" in session["degradation_reasons"]
    assert session["event_source"]["acceptance_eligible"] is False


def test_asr_stream_persists_non_empty_partial_before_final_with_normalized_text(monkeypatch, tmp_path):
    class DelayedPartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            if self._seq == 1:
                text = ""
            else:
                text = "P 九九延迟和<unk>看板需要观察"
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"delayed_partial_{self.session_id}",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.82,
                }
            ]

        def finalize(self):
            return []

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: DelayedPartialRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_delayed_partial_visible"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        first_partial = json.loads(ws.receive_text())
        ws.send_bytes(b"\x00" * 3200)
        second_partial = json.loads(ws.receive_text())

        assert first_partial["event_type"] == "partial"
        assert first_partial["text"] == ""
        assert second_partial["event_type"] == "partial"
        assert second_partial["normalized_text"] == "P99延迟和<unk>看板需要观察"

        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        assert events_response.status_code == 200
        body = events_response.json()
        partials = [event for event in body["events"] if event["event_type"] == "transcript_partial"]
        assert len(partials) == 1
        assert partials[0]["payload"]["text"] == "P 九九延迟和<unk>看板需要观察"
        assert partials[0]["payload"]["normalized_text"] == "P99延迟和<unk>看板需要观察"
        assert body["event_source"]["partial_count"] == 1
        assert body["event_source"]["final_count"] == 0

        ws.send_text("END")


def test_asr_stream_promotes_stable_partial_to_candidate_before_final(monkeypatch, tmp_path):
    class StablePartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"stable_partial_{self.session_id}",
                    "text": "发布评审 payment-gateway 先灰度百分之五，如果 P99 延迟超过九百毫秒就回滚，张三今天补 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: StablePartialRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_stable_partial_candidate"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        partial = json.loads(ws.receive_text())
        hint = json.loads(ws.receive_text())

        assert partial["event_type"] == "partial"
        assert hint["event_type"] == "partial_hint_event"

        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        assert events_response.status_code == 200
        body = events_response.json()
        candidates = [event for event in body["events"] if event["event_type"] == "suggestion_candidate_event"]
        assert candidates
        assert candidates[0]["payload"]["candidate_origin"] == "local_deterministic_asr_stable_partial_skeleton"
        assert candidates[0]["payload"]["degradation_reasons"] == ["partial_not_final"]
        assert candidates[0]["payload"]["scheduler_event_type"] == "llm_candidate_deferred"
        assert body["event_source"]["final_count"] == 0

        ws.send_text("END")


def test_asr_stream_sends_stable_partial_candidate_over_same_websocket(monkeypatch):
    class StablePartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"stable_partial_{self.session_id}",
                    "text": "发布评审 payment-gateway 先灰度百分之五，如果 P99 延迟超过九百毫秒就回滚，张三今天补 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: StablePartialRecognizer(sid))
    repo = InMemoryAsrLiveSessionRepository()
    ws = CapturingWebSocket()

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            "stable_partial_candidate_ws",
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    event_types = [event["event_type"] for event in ws.sent]
    assert event_types[:2] == ["partial", "partial_hint_event"]
    live_candidates = [event for event in ws.sent if event["event_type"] == "suggestion_candidate_event"]
    assert live_candidates
    assert live_candidates[0]["payload"]["candidate_origin"] == "local_deterministic_asr_stable_partial_skeleton"
    assert live_candidates[0]["payload"]["degradation_reasons"] == ["partial_not_final"]
    assert live_candidates[0]["payload"]["scheduler_event_type"] == "llm_candidate_deferred"


def test_asr_stream_sends_live_final_candidate_over_same_websocket(monkeypatch):
    class ImmediateFinalRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "final",
                    "segment_id": f"live_final_{self.session_id}",
                    "text": "支付网关先灰度百分之五，如果 P99 延迟超过九百毫秒就回滚，张三负责 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.91,
                }
            ]

        def finalize(self):
            return []

    class CapturingWebSocket:
        def __init__(self):
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: ImmediateFinalRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    repo = InMemoryAsrLiveSessionRepository()
    ws = CapturingWebSocket()

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            "live_final_candidate_ws",
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    event_types = [event["event_type"] for event in ws.sent]
    assert event_types[0] == "final"
    live_candidates = [event for event in ws.sent if event["event_type"] == "suggestion_candidate_event"]
    assert live_candidates
    assert live_candidates[0]["payload"]["candidate_origin"] == "local_deterministic_asr_skeleton"


def test_asr_stream_preserves_external_realtime_correction_during_later_upsert(monkeypatch):
    class TwoFinalRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "final",
                    "segment_id": f"seg_{self._seq}",
                    "text": f"第{self._seq}段发布评审需要明确回滚负责人和监控阈值。",
                    "start_ms": (self._seq - 1) * 300,
                    "end_ms": self._seq * 300,
                    "confidence": 0.91,
                }
            ]

        def finalize(self):
            return []

    repo = InMemoryAsrLiveSessionRepository()
    session_id = "preserve_external_revision"

    class InjectingWebSocket:
        def __init__(self):
            self.receive_count = 0
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            self.receive_count += 1
            if self.receive_count == 1:
                return {"bytes": b"\x00" * 3200}
            if self.receive_count == 2:
                repo.update(
                    session_id,
                    lambda record: {
                        **record,
                        "events": [
                            *record["events"],
                            {
                                "id": "transcript_revision:seg_1:rtc-v1",
                                "event_type": "transcript_revision",
                                "at_ms": 300,
                                "payload": {
                                    "segment_id": "seg_1:rtc-v1",
                                    "revision_of": "seg_1",
                                    "supersedes_segment_id": "seg_1",
                                    "text": "第一段发布评审需要明确回滚负责人和监控阈值。",
                                    "normalized_text": "第一段发布评审需要明确回滚负责人和监控阈值。",
                                    "correction": {"policy_version": "realtime-transcript-correction.v1"},
                                },
                            },
                        ],
                        "realtime_transcript_correction": {
                            "policy_version": "realtime-transcript-correction.v1",
                            "revised_segment_ids": ["seg_1"],
                        },
                    },
                )
                return {"bytes": b"\x00" * 3200}
            return {"text": "END"}

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: TwoFinalRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))

    asyncio.run(
        asr_stream.handle_stream(
            InjectingWebSocket(),
            session_id,
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    record = repo.get(session_id)
    revisions = [event for event in record["events"] if event.get("event_type") == "transcript_revision"]
    assert [event["id"] for event in revisions] == ["transcript_revision:seg_1:rtc-v1"]
    assert record["realtime_transcript_correction"]["revised_segment_ids"] == ["seg_1"]


def test_asr_stream_keeps_stable_partial_candidate_after_later_short_partial(monkeypatch, tmp_path):
    class StableThenShortPartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            text = (
                "发布评审 payment-gateway 先灰度百分之五，如果 P99 延迟超过九百毫秒就回滚，张三今天补 SLO 看板。"
                if self._seq == 1
                else "看板"
            )
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"same_segment_{self.session_id}",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: StableThenShortPartialRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_stable_partial_retained"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        assert json.loads(ws.receive_text())["event_type"] == "partial"
        assert json.loads(ws.receive_text())["event_type"] == "partial_hint_event"
        live_candidate = json.loads(ws.receive_text())
        assert live_candidate["event_type"] == "suggestion_candidate_event"
        assert live_candidate["payload"]["candidate_origin"] == "local_deterministic_asr_stable_partial_skeleton"
        ws.send_bytes(b"\x00" * 3200)
        for _ in range(8):
            event = json.loads(ws.receive_text())
            if event["event_type"] == "partial":
                break
            assert event["event_type"] == "suggestion_candidate_event"
        else:
            raise AssertionError("did not receive later short partial")

        body = client.get(f"/live/asr/sessions/{sid}/events").json()
        candidates = [event for event in body["events"] if event["event_type"] == "suggestion_candidate_event"]
        partials = [event for event in body["events"] if event["event_type"] == "transcript_partial"]
        assert candidates
        assert candidates[0]["payload"]["candidate_origin"] == "local_deterministic_asr_stable_partial_skeleton"
        assert any("发布评审" in event["payload"]["normalized_text"] for event in partials)
        assert any(event["payload"]["normalized_text"] == "看板" for event in partials)

        ws.send_text("END")


def test_asr_stream_persists_live_final_before_sending_to_browser(monkeypatch):
    class ImmediateFinalRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "final",
                    "segment_id": "live_final_before_send",
                    "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": 900,
                    "confidence": 0.91,
                }
            ]

        def finalize(self):
            return []

    class ObservingWebSocket:
        def __init__(self, repo):
            self.repo = repo
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]
            self.final_send_saw_persisted_session = None

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            body = json.loads(payload)
            if body.get("event_type") == "final":
                try:
                    record = self.repo.get("live_final_before_send")
                except KeyError:
                    self.final_send_saw_persisted_session = False
                else:
                    self.final_send_saw_persisted_session = bool(record.get("events"))

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: ImmediateFinalRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    repo = InMemoryAsrLiveSessionRepository()
    ws = ObservingWebSocket(repo)

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            "live_final_before_send",
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    assert ws.final_send_saw_persisted_session is True


def test_asr_stream_persists_finalize_final_before_sending_to_browser(monkeypatch):
    class FinalizeOnlyRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return []

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": "finalize_final_before_send",
                    "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": 900,
                    "confidence": 0.91,
                }
            ]

    class ObservingWebSocket:
        def __init__(self, repo):
            self.repo = repo
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]
            self.final_send_saw_persisted_session = None

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            body = json.loads(payload)
            if body.get("event_type") == "final":
                try:
                    record = self.repo.get("finalize_final_before_send")
                except KeyError:
                    self.final_send_saw_persisted_session = False
                else:
                    self.final_send_saw_persisted_session = any(
                        event.get("event_type") == "transcript_final" for event in record.get("events") or []
                    )

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: FinalizeOnlyRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    repo = InMemoryAsrLiveSessionRepository()
    ws = ObservingWebSocket(repo)

    asyncio.run(
        asr_stream.handle_stream(
            ws,
            "finalize_final_before_send",
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    assert ws.final_send_saw_persisted_session is True


def test_asr_stream_persists_eos_after_browser_disconnect_during_finalize_send(monkeypatch):
    class FinalizeOnlyRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return []

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": "disconnect_finalize_segment",
                    "text": "需要确认回滚负责人。",
                    "start_ms": 0,
                    "end_ms": 900,
                    "confidence": 0.91,
                }
            ]

        def abort(self):
            return None

    class DisconnectingWebSocket:
        def __init__(self):
            self.messages = [{"bytes": b"\x00" * 3200}, {"text": "END"}]

        async def accept(self):
            return None

        async def receive(self):
            return self.messages.pop(0)

        async def send_text(self, payload):
            if json.loads(payload).get("event_type") == "final":
                raise RuntimeError("browser disconnected after finalize")

        async def close(self):
            return None

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: FinalizeOnlyRecognizer(sid))
    repo = InMemoryAsrLiveSessionRepository()

    asyncio.run(
        asr_stream.handle_stream(
            DisconnectingWebSocket(),
            "disconnect_finalize_session",
            asr_live_repo=repo,
            audio_source="browser_live_mic",
        )
    )

    record = repo.get("disconnect_finalize_session")
    evaluation = next(event for event in record["events"] if event["event_type"] == "evaluation_summary")
    assert evaluation["payload"]["end_of_stream_event_count"] == 1
    assert "stream_interrupted" not in record["degradation_reasons"]


def test_asr_stream_persists_partial_events_drained_during_finalize(monkeypatch, tmp_path):
    class FinalizeBacklogRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"empty_backlog_{self.session_id}",
                    "text": "",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.7,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"backlog_{self.session_id}",
                    "text": "P 九九延迟和<unk>看板",
                    "start_ms": 300,
                    "end_ms": 900,
                    "confidence": 0.81,
                },
                {
                    "event_type": "final",
                    "segment_id": f"backlog_{self.session_id}",
                    "text": "P 九九延迟和<unk>看板需要观察",
                    "start_ms": 300,
                    "end_ms": 1200,
                    "confidence": 0.9,
                },
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: FinalizeBacklogRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_finalize_backlog_partial"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        assert json.loads(ws.receive_text())["text"] == ""
        ws.send_text("END")
        drained_partial = json.loads(ws.receive_text())
        drained_final = json.loads(ws.receive_text())

    assert drained_partial["event_type"] == "partial"
    assert drained_partial["normalized_text"] == "P99延迟和<unk>看板"
    assert drained_final["event_type"] == "final"

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    partials = [event for event in body["events"] if event["event_type"] == "transcript_partial"]
    assert len(partials) == 1
    assert partials[0]["payload"]["normalized_text"] == "P99延迟和<unk>看板"
    assert body["event_source"]["partial_count"] == 1
    assert body["event_source"]["final_count"] == 1


def test_asr_stream_simulated_realtime_wav_source_is_persisted_without_claiming_real_mic(monkeypatch, tmp_path):
    class SimulatedRealtimeRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "先灰度",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.7,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "先灰度 5%。谁负责回滚？",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: SimulatedRealtimeRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_simulated_realtime"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=simulated_realtime_wav") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["provider"] == "test_realtime_asr"
    assert body["provider_mode"] == "real"
    assert body["is_mock"] is False
    assert body["asr_fallback_used"] is False
    assert body["event_source"]["input_source"] == "simulated_realtime_wav"
    assert body["event_source"]["acceptance_eligible"] is True
    assert "input_source_not_acceptance_lane" not in body["event_source"]["acceptance_blockers"]


def test_asr_stream_browser_live_mic_source_is_acceptance_lane(monkeypatch, tmp_path):
    class BrowserLiveMicRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"browser_seg_{self.session_id}",
                    "text": "先灰度",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.7,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"browser_seg_{self.session_id}",
                    "text": "先灰度 5%。谁负责回滚？",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: BrowserLiveMicRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    client.__enter__()
    sid = "sess_browser_live_mic"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        current = events_response.json()
        evaluation_persisted = any(
            event.get("event_type") == "evaluation_summary" for event in current.get("events", [])
        )
        if current.get("audio", {}).get("assembled") and evaluation_persisted:
            break
        time.sleep(0.01)
    else:
        raise AssertionError(
            f"background audio export or session finalization did not finish: {events_response.json()}"
        )
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["event_source"]["input_source"] == "browser_live_mic"
    assert body["event_source"]["acceptance_eligible"] is True
    assert "input_source_not_acceptance_lane" not in body["event_source"]["acceptance_blockers"]
    evaluation = next(event for event in body["events"] if event["event_type"] == "evaluation_summary")
    assert evaluation["payload"]["end_of_stream_event_count"] == 1
    audio = body["audio"]
    assert audio["saved"] is True
    assert audio["source_type"] == "browser_live_mic"
    assert audio["format"] == "wav"
    assert audio["sample_rate_hz"] == 16000
    assert audio["channel_count"] == 1
    assert audio["duration_ms"] > 0
    assert audio["file_size_bytes"] > 44
    audio_path = Path(tmp_path, audio["relative_path"])
    assert audio_path.is_file()
    assert audio_path.read_bytes().startswith(b"RIFF")
    assert hashlib.sha256(audio_path.read_bytes()).hexdigest() == audio["sha256"]

    download_response = client.get(f"/live/asr/sessions/{sid}/audio.wav")
    assert download_response.status_code == 200
    assert download_response.content == audio_path.read_bytes()
    list_response = client.get("/live/asr/sessions")
    assert list_response.status_code == 200
    listed = {item["session_id"]: item for item in list_response.json()["sessions"]}
    assert listed[sid]["has_audio"] is True

    delete_response = client.delete(f"/live/asr/sessions/{sid}")
    assert delete_response.status_code == 200
    assert delete_response.json()["delete_scope"]["audio"] == "deleted"
    client.__exit__(None, None, None)
    assert not audio_path.exists()


def test_asr_stream_warns_when_transcript_is_clear_but_not_technical(monkeypatch, tmp_path):
    class BadSemanticRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"bad_semantic_{self.session_id}",
                    "text": "今天天气不错",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"bad_semantic_{self.session_id}",
                    "text": "今天天气不错，我们吃饭聊天，然后大家都很开心，下午去散步。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: BadSemanticRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_bad_semantic"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    assert "asr_semantic_quality_blocked" not in body["degradation_reasons"]
    assert body["event_source"]["asr_semantic_quality"]["status"] == "warning"
    assert body["event_source"]["acceptance_eligible"] is True
    assert "asr_semantic_quality_blocked" not in body["event_source"]["acceptance_blockers"]
    assert any(event["event_type"] == "transcript_final" for event in body["events"])


def test_asr_stream_marks_good_realtime_transcript_semantic_quality_passed(monkeypatch, tmp_path):
    class GoodSemanticRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"good_semantic_{self.session_id}",
                    "text": "接口先灰度",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"good_semantic_{self.session_id}",
                    "text": "接口先灰度 5%，如果错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.9,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: GoodSemanticRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_good_semantic"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    assert body["event_source"]["asr_semantic_quality"]["status"] == "passed"
    assert "asr_semantic_quality_blocked" not in body["degradation_reasons"]
    assert body["event_source"]["acceptance_eligible"] is True


def test_asr_stream_end_does_not_call_llm_or_guess_revision_boundaries(monkeypatch, tmp_path):
    class LongFinalRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"long_final_{self.session_id}",
                    "text": "接口灰度",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.8,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"long_final_{self.session_id}",
                    "text": (
                        "接口先灰度 5% 如果错误率超过 0.1% 就回滚 "
                        "Redis 缓存穿透风险需要王五今天处理 "
                        "Kafka 消费堆积导致 P95 延迟升高 李四补监控看板"
                    ),
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.92,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: LongFinalRecognizer(sid))
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    correction_calls = []
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: correction_calls.append(raw))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_long_final_split"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        for _ in range(12):
            ws.send_bytes(b"\x00" * 3200)
            ws.receive_text()
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    transcript_finals = [event for event in body["events"] if event["event_type"] == "transcript_final"]
    transcript_revisions = [event for event in body["events"] if event["event_type"] == "transcript_revision"]
    assert len(transcript_finals) == 1
    assert transcript_revisions == []
    assert correction_calls == []
    assert body["event_source"]["final_count"] == 1
    assert body["event_source"]["asr_semantic_quality"]["status"] == "passed"
    assert transcript_finals[0]["payload"]["segment_id"] == f"long_final_{sid}"


def test_asr_stream_end_preserves_original_final_evidence_without_llm_correction(monkeypatch, tmp_path):
    class CorrectedReviewRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "final",
                    "segment_id": f"raw_release_{self.session_id}",
                    "text": "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.82,
                }
            ]

        def finalize(self):
            return []

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: CorrectedReviewRecognizer(sid))
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    correction_calls = []
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: correction_calls.append(raw))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_end_revision_evidence_stable"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        ws.send_bytes(b"\x00" * 3200)
        live_final = json.loads(ws.receive_text())
        assert live_final["event_type"] == "final"
        ws.send_text("END")

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    body = events_response.json()
    events = body["events"]
    original_segment_id = f"raw_release_{sid}"
    original_evidence_id = f"asr_ev_{original_segment_id}"

    final = next(
        event
        for event in events
        if event["event_type"] == "transcript_final" and event["payload"]["segment_id"] == original_segment_id
    )
    assert final["payload"]["evidence_spans"][0]["id"] == original_evidence_id
    assert final["payload"]["evidence_spans"][0]["quote"] == "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒"

    assert not any(event["event_type"] == "transcript_revision" for event in events)
    assert correction_calls == []
    assert any(
        event["event_type"] == "llm_request_draft_event"
        and original_evidence_id in event["payload"]["evidence_span_ids"]
        for event in events
    )

    previews_response = client.get(f"/live/asr/sessions/{sid}/llm-execution-previews")
    assert previews_response.status_code == 200
    previews = previews_response.json()["execution_previews"]
    original_previews = [preview for preview in previews if original_evidence_id in preview["evidence_span_ids"]]
    assert original_previews
    assert original_previews[0]["evidence_spans"][0]["id"] == original_evidence_id
    assert "先灰度" in original_previews[0]["evidence_context"]
    assert "P99" in original_previews[0]["evidence_context"]
    assert "先恢度" not in original_previews[0]["evidence_context"]


def test_asr_stream_promotes_stable_partial_to_realtime_final_after_silence(monkeypatch, tmp_path):
    class StablePartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            text = "接口先灰度 5%，谁负责回滚？" if self._seq <= 2 else ""
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"stable_partial_{self.session_id}",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    def pcm(value: float) -> bytes:
        return struct.pack("<f", value) * 4800

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: StablePartialRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_vad_endpoint_final"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:

        def receive_until_event(event_type: str):
            for _ in range(4):
                event = json.loads(ws.receive_text())
                if event["event_type"] == event_type:
                    return event
            raise AssertionError(f"did not receive {event_type}")

        ws.send_bytes(pcm(0.05))
        assert receive_until_event("partial")["event_type"] == "partial"
        ws.send_bytes(pcm(0.05))
        assert receive_until_event("partial")["event_type"] == "partial"
        for _ in range(3):
            ws.send_bytes(pcm(0.0))
            assert receive_until_event("partial")["event_type"] == "partial"

        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        assert events_response.status_code == 200
        body = events_response.json()
        transcript_finals = [event for event in body["events"] if event["event_type"] == "transcript_final"]
        assert len(transcript_finals) == 1
        assert transcript_finals[0]["payload"]["text"] == "接口先灰度 5%，谁负责回滚？"
        assert transcript_finals[0]["payload"]["segment_id"].startswith("vad_endpoint_")
        assert "source_segment_id" not in transcript_finals[0]["payload"]
        assert body["event_source"]["acceptance_eligible"] is True

        live_final = json.loads(ws.receive_text())
        assert live_final["event_type"] == "final"
        assert live_final["text"] == "接口先灰度 5%，谁负责回滚？"

        ws.send_text("END")


def test_asr_stream_promotes_long_continuous_partial_to_realtime_final_before_stop(monkeypatch, tmp_path):
    class ContinuousPartialRecognizer:
        provider = "test_realtime_asr"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"continuous_partial_{self.session_id}",
                    "text": "接口先灰度 5%，谁负责回滚？持续讨论中。",
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    def pcm(value: float) -> bytes:
        return struct.pack("<f", value) * 4800

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: ContinuousPartialRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_fixed_window_final"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        observed = []
        for _ in range(60):
            ws.send_bytes(pcm(0.05))
            observed.append(json.loads(ws.receive_text()))
            if observed[-1]["event_type"] == "final":
                break

        assert any(event["event_type"] == "final" for event in observed), (
            "continuous speech must produce a bounded realtime final before END "
            "so recording-time correction and AI suggestions can run"
        )

        ws.send_text("END")


def test_asr_stream_splits_cumulative_funasr_partials_into_non_repeating_endpoint_finals(monkeypatch, tmp_path):
    first = "第一段主要介绍嘉宾背景"
    second = "第二段继续讨论拍摄计划"

    class CumulativePartialRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            text = first if self._seq == 1 else first + second if self._seq == 5 else ""
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"{self.session_id}_funasr_sc_001",
                    "source_segment_id": "worker_seg_1",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    def pcm(value: float) -> bytes:
        return struct.pack("<f", value) * 4800

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: CumulativePartialRecognizer(sid))
    monkeypatch.setattr(asr_stream, "_correct_transcript", lambda raw, cfg: (raw, {"total_tokens": 0}, False))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_cumulative_endpoint_finals"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        live_finals = []
        second_live_partial = None
        for chunk_index in range(8):
            ws.send_bytes(pcm(0.05 if chunk_index in {0, 4} else 0.0))
            event = json.loads(ws.receive_text())
            assert event["event_type"] == "partial"
            if chunk_index == 4:
                second_live_partial = event
            if chunk_index in {3, 7}:
                live_finals.append(json.loads(ws.receive_text()))

        assert [event["event_type"] for event in live_finals] == ["final", "final"]
        assert [event["text"] for event in live_finals] == [first, second]
        assert live_finals[0]["segment_id"] != live_finals[1]["segment_id"]
        assert second_live_partial is not None
        assert second_live_partial["text"] == second
        assert second_live_partial["normalized_text"] == second
        assert second_live_partial["source_snapshot_text"] == first + second
        assert second_live_partial["segment_id"] == live_finals[1]["segment_id"]
        assert second_live_partial["source_segment_id"] == f"{sid}_worker_seg_1"
        assert [event["source_segment_id"] for event in live_finals] == [
            f"{sid}_worker_seg_1",
            f"{sid}_worker_seg_1",
        ]

        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        assert events_response.status_code == 200
        transcript_finals = [
            event for event in events_response.json()["events"] if event["event_type"] == "transcript_final"
        ]
        assert [event["payload"]["text"] for event in transcript_finals] == [first, second]

        ws.send_text("END")


@pytest.mark.parametrize(
    "source_segment_id_template",
    ["worker_seg_009", "{session_id}_worker_seg_009"],
    ids=["unscoped", "already_scoped"],
)
def test_asr_stream_funasr_end_final_keeps_worker_source_segment_id(
    monkeypatch,
    tmp_path,
    source_segment_id_template,
):
    sid = f"sess_funasr_end_source_{source_segment_id_template.count('{')}"
    source_segment_id = source_segment_id_template.format(session_id=sid)

    class FinalOnlyFunasrRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 1

        def recognize_chunk(self, pcm):
            return []

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"{self.session_id}_funasr_sc_009",
                    "source_segment_id": source_segment_id,
                    "text": "最终确认灰度发布。",
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.91,
                }
            ]

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: FinalOnlyFunasrRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as ws:
        ws.send_text("END")
        final = json.loads(ws.receive_text())

    assert final["event_type"] == "final"
    assert final["segment_id"] == "vad_endpoint_001"
    assert final["source_segment_id"] == f"{sid}_worker_seg_009"

    events_response = client.get(f"/live/asr/sessions/{sid}/events")
    assert events_response.status_code == 200
    transcript_final = next(
        event for event in events_response.json()["events"] if event["event_type"] == "transcript_final"
    )
    assert transcript_final["payload"]["source_segment_id"] == f"{sid}_worker_seg_009"


@pytest.mark.parametrize("l3_normalize_enabled", [False, True], ids=["raw", "normalized"])
def test_funasr_partial_final_and_canonical_share_l3_normalization_snapshot(
    monkeypatch,
    tmp_path,
    l3_normalize_enabled,
):
    raw_text = "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。"

    class SnapshotFunasrRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"{self.session_id}_funasr_sc_042",
                    "source_segment_id": "worker_seg_42",
                    "text": raw_text,
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.92,
                }
            ]

        def finalize(self):
            return [
                {
                    "event_type": "final",
                    "segment_id": f"{self.session_id}_funasr_sc_042",
                    "source_segment_id": "worker_seg_42",
                    "text": raw_text,
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.92,
                }
            ]

    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: SnapshotFunasrRecognizer(sid),
    )
    client = TestClient(create_app(data_dir=tmp_path))
    settings = client.get("/settings").json()
    settings["asr"]["l3_normalize_enabled"] = l3_normalize_enabled
    assert client.patch("/settings", json=settings).status_code == 200
    sid = f"sess_l3_snapshot_{str(l3_normalize_enabled).lower()}"
    expected_text = asr_stream._normalize_text(raw_text) if l3_normalize_enabled else raw_text
    expected_source_segment_id = f"{sid}_worker_seg_42"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}") as websocket:
        websocket.send_bytes(b"\x00" * 3200)
        partial = json.loads(websocket.receive_text())
        websocket.send_text("END")
        while True:
            final = json.loads(websocket.receive_text())
            if final.get("event_type") == "final":
                break

    assert partial["event_type"] == "partial"
    assert partial["text"] == raw_text
    assert partial["normalized_text"] == expected_text
    assert partial["source_snapshot_text"] == raw_text
    assert partial["source_segment_id"] == expected_source_segment_id
    assert final["event_type"] == "final"
    assert final["text"] == raw_text
    assert final["normalized_text"] == expected_text
    assert final["source_snapshot_text"] == raw_text
    assert final["source_segment_id"] == expected_source_segment_id

    record = client.get(f"/live/asr/sessions/{sid}/events").json()
    transcript_final = next(event for event in record["events"] if event["event_type"] == "transcript_final")
    assert transcript_final["payload"]["text"] == raw_text
    assert transcript_final["payload"]["normalized_text"] == expected_text
    assert transcript_final["payload"]["source_snapshot_text"] == raw_text
    assert transcript_final["payload"]["source_segment_id"] == expected_source_segment_id
    assert record["canonical_transcript"]["full_text"] == expected_text
    assert record["settings_snapshot"] == {
        "asr": {"l3_normalize_enabled": l3_normalize_enabled},
        "scope": "websocket_connection_start",
    }


@pytest.mark.parametrize(
    ("first_l3", "second_l3"),
    [(True, False), (False, True)],
    ids=["normalized-to-raw", "raw-to-normalized"],
)
def test_asr_stream_same_session_reconnect_reprojects_from_raw_source(
    monkeypatch,
    first_l3,
    second_l3,
):
    raw_text = "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒就回滚。"
    connection_number = 0

    class ReconnectL3Recognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, _session_id):
            nonlocal connection_number
            connection_number += 1
            self.connection_number = connection_number
            self._seq = 0

        def recognize_chunk(self, _pcm):
            self._seq += 1
            if self.connection_number != 1:
                return []
            return [
                {
                    "event_type": "final",
                    "segment_id": "reconnect_l3_seg",
                    "source_segment_id": "worker_l3_42",
                    "text": raw_text,
                    "start_ms": 0,
                    "end_ms": 300,
                    "confidence": 0.92,
                }
            ]

        def finalize(self):
            return []

    class ReconnectWebSocket:
        def __init__(self, interrupted):
            self.messages = [{"bytes": b"\x00" * 3200}]
            if not interrupted:
                self.messages.append({"text": "END"})
            self.sent = []

        async def accept(self):
            return None

        async def receive(self):
            if self.messages:
                return self.messages.pop(0)
            raise RuntimeError("simulated reconnect")

        async def send_text(self, payload):
            self.sent.append(json.loads(payload))

        async def close(self):
            return None

    monkeypatch.setattr(
        asr_stream,
        "get_recognizer",
        lambda sid: ReconnectL3Recognizer(sid),
    )
    monkeypatch.setattr(
        asr_stream,
        "_correct_transcript",
        lambda raw, cfg: (raw, {"total_tokens": 0}, False),
    )
    repo = InMemoryAsrLiveSessionRepository()
    sid = f"sess_l3_reconnect_{str(first_l3).lower()}_{str(second_l3).lower()}"

    asyncio.run(
        asr_stream.handle_stream(
            ReconnectWebSocket(interrupted=True),
            sid,
            asr_live_repo=repo,
            l3_normalize_enabled=first_l3,
        )
    )
    asyncio.run(
        asr_stream.handle_stream(
            ReconnectWebSocket(interrupted=False),
            sid,
            asr_live_repo=repo,
            l3_normalize_enabled=second_l3,
        )
    )

    record = repo.get(sid)
    transcript_final = next(event for event in record["events"] if event["event_type"] == "transcript_final")
    expected_projection = asr_stream._normalize_text(raw_text) if second_l3 else raw_text
    assert transcript_final["payload"]["text"] == raw_text
    assert transcript_final["payload"]["normalized_text"] == expected_projection
    assert transcript_final["payload"]["source_snapshot_text"] == raw_text
    assert transcript_final["payload"]["source_segment_id"] == "worker_l3_42"
    canonical = project_canonical_transcript(session_id=sid, events=record["events"])
    assert canonical["full_text"] == expected_projection
    assert record["settings_snapshot"] == {
        "asr": {"l3_normalize_enabled": second_l3},
        "scope": "websocket_connection_start",
    }


def test_asr_stream_marks_bounded_cumulative_rerecognition_without_dropping_source(monkeypatch, tmp_path):
    first = "我们讨论产品目标"
    rerecognized = "我们讨论产品目的和实现计划"

    class RerecognizingFunasrRecognizer:
        provider = "funasr_realtime"
        provider_mode = "real"
        is_mock = False
        fallback_used = False
        degradation_reasons = []

        def __init__(self, session_id):
            self.session_id = session_id
            self._seq = 0

        def recognize_chunk(self, pcm):
            self._seq += 1
            text = first if self._seq == 1 else rerecognized if self._seq == 5 else ""
            return [
                {
                    "event_type": "partial",
                    "segment_id": f"{self.session_id}_funasr_sc_001",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": self._seq * 300,
                    "confidence": 0.88,
                }
            ]

        def finalize(self):
            return []

    def pcm(value: float) -> bytes:
        return struct.pack("<f", value) * 4800

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: RerecognizingFunasrRecognizer(sid))
    client = TestClient(create_app(data_dir=tmp_path))
    sid = "sess_cumulative_rerecognition"

    with client.websocket_connect(f"/live/asr/stream/ws/{sid}?audio_source=browser_live_mic") as ws:
        second_partial = None
        second_final = None
        for chunk_index in range(8):
            ws.send_bytes(pcm(0.05 if chunk_index in {0, 4} else 0.0))
            event = json.loads(ws.receive_text())
            if chunk_index == 3:
                assert json.loads(ws.receive_text())["event_type"] == "final"
            if chunk_index == 4:
                second_partial = event
            if chunk_index == 7:
                second_final = json.loads(ws.receive_text())

        assert second_partial is not None
        assert second_partial["source_snapshot_text"] == rerecognized
        assert second_partial["projection_reconciled"] is True
        assert second_partial["text"]
        assert second_partial["normalized_text"] == asr_stream._normalize_text(second_partial["text"])
        assert second_final is not None
        assert second_final["event_type"] == "final"
        assert second_final["projection_reconciled"] is True

        events_response = client.get(f"/live/asr/sessions/{sid}/events")
        assert events_response.status_code == 200
        body = events_response.json()
        assert body["canonical_transcript"]["active_tail"] is None
        assert body["canonical_transcript"]["full_text"] == rerecognized

        ws.send_text("END")


def test_compacts_cumulative_source_snapshots_to_bounded_restore_checkpoints():
    events = [
        {
            "event_type": "transcript_final",
            "payload": {
                "segment_id": "segment-1",
                "source_snapshot_text": "第一段",
            },
        },
        {
            "event_type": "transcript_final",
            "payload": {
                "segment_id": "segment-2",
                "source_snapshot_text": "第一段第二段重识别",
                "projection_reconciled": True,
            },
        },
        {
            "event_type": "transcript_final",
            "payload": {
                "segment_id": "segment-3",
                "source_snapshot_text": "第一段第二段重识别第三段",
            },
        },
        {
            "event_type": "transcript_partial",
            "payload": {
                "segment_id": "segment-4",
                "source_snapshot_text": "第一段第二段重识别第三段第四段",
            },
        },
    ]

    compacted = asr_stream._compact_cumulative_source_snapshots(events)

    assert "source_snapshot_text" not in compacted[0]["payload"]
    assert compacted[1]["payload"]["source_snapshot_text"] == "第一段第二段重识别"
    assert compacted[2]["payload"]["source_snapshot_text"] == "第一段第二段重识别第三段"
    assert compacted[3]["payload"]["source_snapshot_text"] == "第一段第二段重识别第三段第四段"
    assert events[0]["payload"]["source_snapshot_text"] == "第一段"


def test_bounds_legacy_live_projection_without_dropping_the_latest_window():
    finals = [
        {"event_type": "final", "segment_id": f"final-{index}", "text": str(index)}
        for index in range(asr_stream.LIVE_PROJECTION_MAX_FINALS + 5)
    ]
    partials = [
        {"event_type": "partial", "segment_id": f"partial-{index}", "text": str(index)}
        for index in range(asr_stream.LIVE_PROJECTION_MAX_PARTIALS + 3)
    ]
    end_of_stream = {"event_type": "end_of_stream", "segment_id": "eos"}

    bounded, dropped = asr_stream._bound_streaming_projection_events([*finals, *partials, end_of_stream])

    retained_finals = [event for event in bounded if event["event_type"] == "final"]
    retained_partials = [event for event in bounded if event["event_type"] == "partial"]
    assert len(retained_finals) == asr_stream.LIVE_PROJECTION_MAX_FINALS
    assert retained_finals[0]["segment_id"] == "final-5"
    assert len(retained_partials) == asr_stream.LIVE_PROJECTION_MAX_PARTIALS
    assert retained_partials[0]["segment_id"] == "partial-3"
    assert bounded[-1] == end_of_stream
    assert dropped == 8

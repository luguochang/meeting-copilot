from __future__ import annotations

import json
import struct

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.native_pcm_protocol import encode_native_pcm_v2_frame


class _Recognizer:
    provider = "native_pcm_contract_asr"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons: list[str] = []

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._seq = 0

    def recognize_chunk(self, _pcm: bytes) -> list[dict]:
        self._seq += 1
        return [
            {
                "event_type": "partial",
                "segment_id": "native-segment",
                "text": "正在验证系统音频采集批次",
                "start_ms": 0,
                "end_ms": 300,
                "confidence": 0.9,
            }
        ]

    def finalize(self) -> list[dict]:
        return [
            {
                "event_type": "final",
                "segment_id": "native-segment",
                "text": "正在验证系统音频采集批次和录音持久化。",
                "start_ms": 0,
                "end_ms": 300,
                "confidence": 0.9,
            }
        ]

    def abort(self) -> None:
        return None


def _pcm() -> bytes:
    return struct.pack("<f", 0.125) * 4_800


def test_native_audio_source_without_v2_identity_is_rejected_before_recording(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)

    with TestClient(app) as client:
        with client.websocket_connect(
            "/live/asr/stream/ws/native-missing-envelope?audio_source=tauri_system_audio"
        ) as websocket:
            event = json.loads(websocket.receive_text())
        recordings = app.state.v2_persistence.list_recording_sessions("native-missing-envelope")

    assert event["event_type"] == "provider_error"
    assert event["error_code"] == "native_pcm_identity_invalid"
    assert recordings == []


def test_native_v2_identity_reaches_events_and_track_epoch_persistence(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda session_id: _Recognizer(session_id))
    app = create_app(data_dir=tmp_path)
    session_id = "native-system-envelope"
    url = (
        f"/live/asr/stream/ws/{session_id}"
        "?audio_source=tauri_system_audio"
        "&pcm_protocol=native_pcm_v2"
        "&capture_epoch=7"
    )

    with TestClient(app) as client:
        with client.websocket_connect(url) as websocket:
            websocket.send_bytes(
                encode_native_pcm_v2_frame(
                    track_id="system_audio",
                    capture_epoch=7,
                    sequence=1,
                    timestamp_ms=12_345,
                    pcm=_pcm(),
                )
            )
            partial = json.loads(websocket.receive_text())
            websocket.send_text("END")
            final = None
            while True:
                try:
                    event = json.loads(websocket.receive_text())
                except Exception:
                    break
                if event.get("event_type") == "final":
                    final = event
        recordings = app.state.v2_persistence.list_recording_sessions(session_id)
        chunks = app.state.v2_persistence.list_audio_chunks(session_id)

    assert partial["pcm_protocol"] == "native_pcm_v2"
    assert partial["source_track"] == "system_audio"
    assert partial["capture_epoch"] == 7
    assert partial["track_sequence"] == 1
    assert partial["source_timestamp_ms"] == 12_345
    assert final is not None
    assert final["source_track"] == "system_audio"
    assert final["capture_epoch"] == 7
    assert final["track_sequence"] == 1
    assert len(recordings) == 1
    assert recordings[0]["track"] == "system_audio"
    assert recordings[0]["epoch"] == 7
    assert len(chunks) == 1
    assert chunks[0]["track"] == "system_audio"
    assert chunks[0]["epoch"] == 7
    assert chunks[0]["source_sequence_start"] == 1
    assert chunks[0]["source_sequence_end"] == 1
    assert chunks[0]["source_timestamp_start_ms"] == 12_345
    assert chunks[0]["source_timestamp_end_ms"] == 12_345


def test_native_v2_duplicate_sequence_fails_closed_and_preserves_first_audio(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda session_id: _Recognizer(session_id))
    app = create_app(data_dir=tmp_path)
    session_id = "native-sequence-rejected"
    url = (
        f"/live/asr/stream/ws/{session_id}"
        "?audio_source=tauri_native_mic"
        "&pcm_protocol=native_pcm_v2"
        "&capture_epoch=2"
    )
    frame = encode_native_pcm_v2_frame(
        track_id="microphone",
        capture_epoch=2,
        sequence=1,
        timestamp_ms=1_000,
        pcm=_pcm(),
    )

    with TestClient(app) as client:
        with client.websocket_connect(url) as websocket:
            websocket.send_bytes(frame)
            websocket.receive_text()
            websocket.send_bytes(frame)
            error = json.loads(websocket.receive_text())
        recordings = app.state.v2_persistence.list_recording_sessions(session_id)

    assert error["event_type"] == "provider_error"
    assert error["error_code"] == "native_pcm_sequence_invalid"
    assert len(recordings) == 1
    assert recordings[0]["track"] == "microphone"
    assert recordings[0]["epoch"] == 2
    assert recordings[0]["status"] in {"interrupted", "ready", "exporting"}

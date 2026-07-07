"""Tests for the realtime ASR WebSocket stream."""
import json
import pytest
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp.app import create_app


@pytest.fixture(autouse=True)
def _force_fake_recognizer(monkeypatch):
    """Force the Fake recognizer so WS tests are deterministic and don't spawn
    the real sherpa/funasr sidecar (tested separately)."""
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda sid: None)


def test_asr_stream_ws_emits_partial_per_chunk_and_final_on_end():
    client = TestClient(create_app())
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
    client = TestClient(create_app())
    with client.websocket_connect("/live/asr/stream/ws/sess_a") as ws:
        ws.send_bytes(b"\x01" * 100)
        p = json.loads(ws.receive_text())
        assert p["segment_id"] == "stream_seg_sess_a"


def test_asr_stream_ws_recognizer_is_pluggable(monkeypatch):
    from meeting_copilot_web_mvp import asr_stream

    class CustomRecognizer:
        def __init__(self, session_id):
            self.session_id = session_id

        def recognize_chunk(self, pcm):
            return {"event_type": "partial", "segment_id": "custom", "text": "custom", "start_ms": 0, "end_ms": 100, "confidence": 0.5}

        def finalize(self):
            return {"event_type": "final", "segment_id": "custom", "text": "custom-final", "start_ms": 0, "end_ms": 100, "confidence": 0.9}

    monkeypatch.setattr(asr_stream, "get_recognizer", lambda sid: CustomRecognizer(sid))
    client = TestClient(create_app())
    with client.websocket_connect("/live/asr/stream/ws/sess_custom") as ws:
        ws.send_bytes(b"\x00" * 10)
        p = json.loads(ws.receive_text())
        ws.send_text("END")
        fin = json.loads(ws.receive_text())
    assert p["segment_id"] == "custom"
    assert fin["text"] == "custom-final"

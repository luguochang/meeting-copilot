from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"
TOOL_PATH = REPO_ROOT / "tools" / "silent_agent_acceptance.py"
for path in (BACKEND_ROOT, CORE_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from meeting_copilot_web_mvp.native_pcm_protocol import NativePcmV2Decoder  # noqa: E402


def _load_tool():
    spec = importlib.util.spec_from_file_location("silent_agent_acceptance", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _WebSocket:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []

    def send_binary(self, payload: bytes) -> None:
        self.payloads.append(payload)


def _args(tool, *, audio_source: str, capture_epoch: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        audio_source=audio_source,
        base_url="ws://127.0.0.1:8993",
        capture_epoch=capture_epoch,
        meeting_id="silent dual/source",
        seconds=10.1,
        tail_silence_seconds=4.0,
    )


def test_native_stream_url_carries_authenticated_track_identity() -> None:
    tool = _load_tool()

    url = tool._stream_url(
        _args(tool, audio_source="macos_system_audio", capture_epoch=7)
    )

    assert "/silent%20dual/source?" in url
    assert "audio_source=macos_system_audio" in url
    assert "pcm_protocol=native_pcm_v2" in url
    assert "capture_epoch=7" in url
    assert "transport_handshake=1" in url
    assert "expected_duration_seconds=15" in url


def test_native_transport_emits_full_frames_then_one_ordered_partial_tail() -> None:
    tool = _load_tool()
    websocket = _WebSocket()
    transport = tool._PcmTransport(
        websocket,
        audio_source="native_microphone_streaming",
        capture_epoch=3,
    )
    full_bytes = tool.NATIVE_PCM_FRAME_SAMPLES * 4

    transport.feed(b"\0" * (full_bytes + 400))
    transport.flush()

    assert transport.sequence == 2
    assert transport.sent_frames == tool.NATIVE_PCM_FRAME_SAMPLES + 100
    decoder = NativePcmV2Decoder(
        expected_track_id="microphone",
        expected_capture_epoch=3,
    )
    first = decoder.decode(websocket.payloads[0])
    second = decoder.decode(websocket.payloads[1])
    assert first.sequence == 1
    assert first.timestamp_ms == 0
    assert first.final_partial is False
    assert len(first.payload) == full_bytes
    assert second.sequence == 2
    assert second.timestamp_ms == 300
    assert second.final_partial is True
    assert len(second.payload) == 400


def test_raw_microphone_transport_preserves_payload_without_native_envelope() -> None:
    tool = _load_tool()
    websocket = _WebSocket()
    transport = tool._PcmTransport(
        websocket,
        audio_source="microphone",
        capture_epoch=None,
    )

    transport.feed(b"\0" * 400)
    transport.flush()

    assert websocket.payloads == [b"\0" * 400]
    assert transport.sequence == 0
    assert transport.sent_frames == 100

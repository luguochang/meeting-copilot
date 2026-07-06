"""Realtime ASR streaming over WebSocket.

Accepts binary PCM audio chunks, runs a streaming recognizer, emits ASR events
(partial/final) back over the socket. The recognizer is pluggable:

- FakeStreamRecognizer (default): deterministic, used for tests and as a
  no-sherpa fallback. Produces a partial per chunk and a final on end.
- A sherpa-backed recognizer plugs in here in Phase 4 via the ASR worker sidecar
  (sherpa-onnx lives in a separate Python 3.11 venv, so it cannot be imported
  directly by the 3.14 web backend).

Protocol: client sends binary PCM chunks; sends text "END" to finalize. Server
responds with one JSON ASR event per chunk (partial) and one final event.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.asr_stream")


class StreamRecognizer(Protocol):
    def recognize_chunk(self, pcm: bytes) -> dict[str, Any]: ...
    def finalize(self) -> dict[str, Any]: ...


class FakeStreamRecognizer:
    """Deterministic recognizer for tests / no-sherpa fallback."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._seq = 0

    def recognize_chunk(self, pcm: bytes) -> dict[str, Any]:
        self._seq += 1
        return {
            "event_type": "partial",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": f"partial {self._seq} ({len(pcm)} bytes)",
            "start_ms": (self._seq - 1) * 300,
            "end_ms": self._seq * 300,
            "confidence": 0.7,
        }

    def finalize(self) -> dict[str, Any]:
        return {
            "event_type": "final",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": f"final transcript for {self.session_id}",
            "start_ms": 0,
            "end_ms": self._seq * 300,
            "confidence": 0.9,
        }


def get_recognizer(session_id: str) -> StreamRecognizer:
    """Return the active stream recognizer for a session.

    Phase 4 will route this to a sherpa-backed sidecar recognizer when available.
    """
    return FakeStreamRecognizer(session_id)


async def handle_stream(websocket, session_id: str) -> None:
    """Handle one WS audio stream: read chunks, emit ASR events back over the WS."""
    await websocket.accept()
    recognizer = get_recognizer(session_id)
    _log.info("asr.stream.start", session_id=session_id)
    try:
        while True:
            msg = await websocket.receive()
            if msg.get("bytes") is not None:
                partial = recognizer.recognize_chunk(msg["bytes"])
                await websocket.send_text(json.dumps(partial, ensure_ascii=False))
            elif msg.get("text") == "END":
                final = recognizer.finalize()
                await websocket.send_text(json.dumps(final, ensure_ascii=False))
                await websocket.close()
                _log.info("asr.stream.end", session_id=session_id, chunks=recognizer._seq)
                return
    except Exception as exc:
        _log.warning("asr.stream.aborted", session_id=session_id, error=str(exc))
        try:
            await websocket.close()
        except Exception:
            pass

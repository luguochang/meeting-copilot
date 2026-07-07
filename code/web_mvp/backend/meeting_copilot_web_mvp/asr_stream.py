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
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any, Protocol

from meeting_copilot_web_mvp.logging_config import get_logger
from meeting_copilot_web_mvp.asr_live_events import (
    ASR_LIVE_SOURCE,
    ASR_LIVE_TRACE_KIND,
    build_asr_live_events,
)
from meeting_copilot_web_mvp.transcript_normalizer import hotwords as _hotwords

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

    Prefers FunASR streaming (better Chinese, G2) when available, then sherpa,
    then FakeStreamRecognizer.
    """
    funasr = _maybe_funasr_sidecar(session_id)
    if funasr is not None:
        return funasr
    sherpa = _maybe_sherpa_sidecar(session_id)
    if sherpa is not None:
        return sherpa
    return FakeStreamRecognizer(session_id)


# Paths resolved relative to this package (code/web_mvp/backend/meeting_copilot_web_mvp).
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SHERPA_VENV_PY = _REPO_ROOT / "code" / "asr_runtime" / ".venv-sherpa" / "bin" / "python"
_SHERPA_WORKER = _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "sherpa_stream_worker.py"
_SHERPA_MODEL = _REPO_ROOT / "code" / "asr_runtime" / "models" / "sherpa-onnx"


class SherpaSidecarRecognizer:
    """Real ASR sidecar: spawns sherpa_stream_worker.py (sherpa 3.11 venv) as a
    subprocess, feeds float32 PCM chunks via stdin, reads JSON ASR events from
    stdout. This bridges the 3.14 web backend to the 3.11 sherpa-onnx runtime."""

    def __init__(self, session_id: str, model_dir: Path, venv_python: Path | None = None):
        self.session_id = session_id
        python = str(venv_python or _SHERPA_VENV_PY)
        self._proc = subprocess.Popen(
            [python, str(_SHERPA_WORKER), "--model-dir", str(model_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._seq = 0
        _log.info("asr.sidecar.start", session_id=session_id, model=str(model_dir))

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    def recognize_chunk(self, pcm: bytes) -> dict[str, Any]:
        self._seq += 1
        try:
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
        except Exception:
            pass
        latest = None
        while not self._q.empty():
            latest = self._q.get_nowait()
        if latest:
            latest["segment_id"] = f"stream_seg_{self.session_id}"
            latest.setdefault("confidence", 0.8)
            return latest
        return {
            "event_type": "partial",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": "",
            "start_ms": (self._seq - 1) * 300,
            "end_ms": self._seq * 300,
            "confidence": 0.7,
        }

    def finalize(self) -> dict[str, Any]:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        final = {
            "event_type": "final",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": "",
            "confidence": 0.9,
        }
        try:
            self._proc.wait(timeout=10)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        while not self._q.empty():
            ev = self._q.get_nowait()
            if ev.get("event_type") == "final":
                final["text"] = ev.get("text", "")
        _log.info("asr.sidecar.end", session_id=self.session_id)
        return final


_FUNASR_VENV_PY = _REPO_ROOT / "code" / "asr_runtime" / ".venv-funasr" / "bin" / "python"
_FUNASR_WORKER = _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "funasr_stream_worker.py"


class FunasrSidecarRecognizer:
    """Real-time FunASR streaming sidecar (G2). Spawns funasr_stream_worker.py
    (funasr 3.11 venv) with technical hotwords; feeds float32 PCM via stdin,
    reads JSON ASR events from stdout. Better Chinese accuracy than sherpa."""

    def __init__(self, session_id: str, venv_python: Path | None = None):
        self.session_id = session_id
        python = str(venv_python or _FUNASR_VENV_PY)
        cmd = [python, str(_FUNASR_WORKER)]
        try:
            hw = _hotwords()
            if hw:
                cmd += ["--hotwords", " ".join(hw)]
        except Exception:
            pass
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._seq = 0
        _log.info("asr.sidecar.funasr.start", session_id=session_id)

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._q.put(json.loads(line))
                except Exception:
                    pass
        except Exception:
            pass

    def recognize_chunk(self, pcm: bytes) -> dict[str, Any]:
        self._seq += 1
        try:
            self._proc.stdin.write(pcm)
            self._proc.stdin.flush()
        except Exception:
            pass
        latest = None
        while not self._q.empty():
            latest = self._q.get_nowait()
        if latest:
            latest["segment_id"] = f"stream_seg_{self.session_id}"
            latest.setdefault("confidence", 0.8)
            return latest
        return {"event_type": "partial", "segment_id": f"stream_seg_{self.session_id}", "text": "", "start_ms": (self._seq - 1) * 300, "end_ms": self._seq * 300, "confidence": 0.7}

    def finalize(self) -> dict[str, Any]:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        final = {"event_type": "final", "segment_id": f"stream_seg_{self.session_id}", "text": "", "confidence": 0.9}
        try:
            self._proc.wait(timeout=15)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        while not self._q.empty():
            ev = self._q.get_nowait()
            if ev.get("event_type") == "final":
                final["text"] = ev.get("text", "")
        _log.info("asr.sidecar.funasr.end", session_id=self.session_id)
        return final


def _maybe_funasr_sidecar(session_id: str) -> FunasrSidecarRecognizer | None:
    """Return a FunasrSidecarRecognizer if the funasr venv + worker exist, else None."""
    if not _FUNASR_VENV_PY.is_file() or not _FUNASR_WORKER.is_file():
        return None
    try:
        return FunasrSidecarRecognizer(session_id)
    except Exception as exc:
        _log.warning("asr.sidecar.funasr.spawn_failed", error=str(exc))
        return None


def _maybe_sherpa_sidecar(session_id: str) -> SherpaSidecarRecognizer | None:
    """Return a SherpaSidecarRecognizer if venv + worker + model dir exist, else None."""
    if not _SHERPA_VENV_PY.is_file() or not _SHERPA_WORKER.is_file():
        return None
    if not _SHERPA_MODEL.is_dir():
        return None
    # pick the first model dir containing a .onnx
    for child in sorted(_SHERPA_MODEL.iterdir()):
        if child.is_dir() and any(child.glob("*.onnx")):
            try:
                return SherpaSidecarRecognizer(session_id, child)
            except Exception as exc:
                _log.warning("asr.sidecar.spawn_failed", error=str(exc))
                return None
    return None


async def handle_stream(websocket, session_id: str, asr_live_repo=None, provider: str = "local_real_asr") -> None:
    """Handle one WS audio stream: read chunks, emit ASR events back over the WS.

    If asr_live_repo is provided, accumulates real ASR final events and persists
    a session record on END — so the real mic -> ASR -> session -> LLM cards
    pipeline is connected end-to-end (llm-execution-runs / approach-cards /
    minutes can then run on the real ASR session).
    """
    await websocket.accept()
    recognizer = get_recognizer(session_id)
    _log.info("asr.stream.start", session_id=session_id)
    accumulated_finals: list[dict[str, Any]] = []
    chunk_ms = 300

    def _to_streaming_final(ev: dict[str, Any], idx: int) -> dict[str, Any]:
        return {
            "event_type": "final",
            "segment_id": ev.get("segment_id") or f"real_seg_{idx}",
            "text": ev.get("text", ""),
            "start_ms": idx * chunk_ms,
            "end_ms": (idx + 1) * chunk_ms,
            "received_at_ms": idx * chunk_ms + chunk_ms,
            "confidence": ev.get("confidence", 0.85),
        }

    try:
        while True:
            msg = await websocket.receive()
            if msg.get("bytes") is not None:
                event = recognizer.recognize_chunk(msg["bytes"])
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
                if event.get("event_type") == "final" and event.get("text"):
                    accumulated_finals.append(_to_streaming_final(event, recognizer._seq))
            elif msg.get("text") == "END":
                final = recognizer.finalize()
                await websocket.send_text(json.dumps(final, ensure_ascii=False))
                if final.get("text"):
                    accumulated_finals.append(_to_streaming_final(final, recognizer._seq + 1))
                if asr_live_repo is not None and accumulated_finals:
                    live_events = build_asr_live_events(
                        session_id=session_id,
                        provider=provider,
                        streaming_events=accumulated_finals,
                        is_mock=False,
                    )
                    try:
                        asr_live_repo.create({
                            "session_id": session_id,
                            "provider": provider,
                            "source": ASR_LIVE_SOURCE,
                            "trace_kind": ASR_LIVE_TRACE_KIND,
                            "events": live_events,
                        })
                        _log.info("asr.stream.persisted", session_id=session_id, finals=len(accumulated_finals), events=len(live_events))
                    except Exception as exc:
                        _log.warning("asr.stream.persist_failed", session_id=session_id, error=str(exc))
                await websocket.close()
                _log.info("asr.stream.end", session_id=session_id, chunks=recognizer._seq, finals=len(accumulated_finals))
                return
    except Exception as exc:
        _log.warning("asr.stream.aborted", session_id=session_id, error=str(exc))
        try:
            await websocket.close()
        except Exception:
            pass

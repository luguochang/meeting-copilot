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

import asyncio
from asyncio import CancelledError as AsyncCancelledError
from asyncio import get_running_loop as _get_running_loop
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import functools
import json
import os
import queue
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from meeting_copilot_web_mvp.logging_config import get_logger
from meeting_copilot_web_mvp.asr_live_events import (
    ASR_LIVE_SOURCE,
    ASR_LIVE_TRACE_KIND,
    build_asr_live_events,
    build_partial_hint_event,
)
from meeting_copilot_web_mvp.transcript_normalizer import hotwords as _hotwords
from meeting_copilot_web_mvp.transcript_normalizer import normalize as _normalize_text
# Retained as a test injection point; realtime LLM correction is route-controlled.
from meeting_copilot_web_mvp.asr_correct import correct_transcript as _correct_transcript  # noqa: F401
from meeting_copilot_web_mvp.asr_semantic_quality import (
    BLOCKER as ASR_SEMANTIC_QUALITY_BLOCKER,
    evaluate_semantic_quality,
)
from meeting_copilot_web_mvp.audio_assets import RealtimeWavAssetWriter
from meeting_copilot_web_mvp.funasr_resident import (
    FunasrResidentBusyError,
    FunasrResidentSession,
    FunasrResidentUnavailableError,
    FunasrResidentWorkerManager,
)
from meeting_copilot_web_mvp.realtime_transcript_correction import POLICY_VERSION as REALTIME_CORRECTION_POLICY_VERSION
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller, LEVEL_HEAVY

_log = get_logger("meeting_copilot_web_mvp.asr_stream")
VAD_SILENCE_RMS_THRESHOLD = 0.003
VAD_ENDPOINT_SILENCE_MS = 900
# Long uninterrupted speech must still produce bounded confirmed segments so
# downstream correction and suggestion work can run while the meeting is live.
VAD_MAX_SEGMENT_MS = 15_000
VAD_MIN_FINAL_TEXT_CHARS = 6
STABLE_PARTIAL_CANDIDATE_MIN_CHARS = 24
STABLE_PARTIAL_CANDIDATE_MIN_CONFIDENCE = 0.80
LIVE_PROJECTION_MAX_FINALS = 512
LIVE_PROJECTION_MAX_PARTIALS = 32
LIVE_PROJECTION_MAX_EXTERNAL_REVISIONS = 128
SIDECAR_READER_DRAIN_TIMEOUT_S = 2.0
SIDECAR_WRITER_DRAIN_TIMEOUT_S = 2.0
SIDECAR_THREAD_JOIN_TIMEOUT_S = 1.0
SIDECAR_PROCESS_WAIT_TIMEOUT_S = 5.0
SIDECAR_WRITE_QUEUE_MAX_CHUNKS = 64
ASR_READY_TIMEOUT_S = 60.0
ASR_READY_BUFFER_MAX_CHUNKS = 240
SIDECAR_GRACEFUL_DRAIN_MARGIN_S = 5.0
SIDECAR_GRACEFUL_DRAIN_MAX_S = 30.0
SIDECAR_GRACEFUL_DRAIN_AUDIO_FACTOR = 2.0
STABLE_PARTIAL_CANDIDATE_MARKERS = (
    "灰度",
    "发布",
    "回滚",
    "P99",
    "错误率",
    "延迟",
    "超过",
    "负责",
    "补充",
    "确认",
    "SLO",
    "feature flag",
    "rollback",
    "checklist",
    "风险",
)


class StreamRecognizer(Protocol):
    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]: ...
    def finalize(self) -> list[dict[str, Any]]: ...

    def abort(self) -> None: ...


class FakeStreamRecognizer:
    """Deterministic recognizer for tests / no-sherpa fallback."""

    provider = "fake"
    provider_mode = "mock"
    is_mock = True
    fallback_used = True
    degradation_reasons = ["real_asr_sidecar_unavailable"]

    def __init__(self, session_id: str):
        self.session_id = session_id
        self._seq = 0

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._seq += 1
        return [{
            "event_type": "partial",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": f"partial {self._seq} ({len(pcm)} bytes)",
            "start_ms": (self._seq - 1) * 300,
            "end_ms": self._seq * 300,
            "confidence": 0.7,
        }]

    def finalize(self) -> list[dict[str, Any]]:
        return [{
            "event_type": "final",
            "segment_id": f"stream_seg_{self.session_id}",
            "text": f"final transcript for {self.session_id}",
            "start_ms": 0,
            "end_ms": self._seq * 300,
            "confidence": 0.9,
        }]

    def abort(self) -> None:
        return None


def get_recognizer(session_id: str) -> StreamRecognizer:
    """Return the active stream recognizer for a session.

    Prefers FunASR for Chinese real-time meeting accuracy, then sherpa as a
    fast fallback, then Fake. FunASR uses a balanced Chinese meeting profile;
    sherpa finals are LLM-corrected downstream (L2).
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


@dataclass
class _SidecarGeneration:
    number: int
    proc: Any
    write_q: "queue.Queue[bytes | None]"
    stderr_lines: list[str]
    start_time: float
    ready_time: float | None = None
    ready_event: threading.Event = field(default_factory=threading.Event)
    stderr_reader: threading.Thread | None = None
    reader: threading.Thread | None = None
    writer: threading.Thread | None = None
    watchdog: threading.Thread | None = None
    terminal: bool = False
    accepting_audio: bool = True
    accepting_events: bool = True
    audio_chunks_enqueued: int = 0
    audio_bytes_enqueued: int = 0
    audio_chunks_written: int = 0
    audio_bytes_written: int = 0
    max_write_queue_depth: int = 0


class AsrSidecarUnavailableError(RuntimeError):
    pass


def _mark_generation_terminal_locked(generation: _SidecarGeneration) -> None:
    generation.terminal = True
    generation.accepting_audio = False
    generation.accepting_events = False


def _restart_sidecar_generation(
    recognizer: Any,
    generation: _SidecarGeneration,
    exit_code: int,
) -> tuple[bool, Exception | None]:
    """Atomically claim the one restart and replace only the current generation."""
    restart_error: Exception | None = None
    with recognizer._state_lock:
        if exit_code == 0 or recognizer._finalizing or recognizer._generation is not generation:
            return False, None
        _mark_generation_terminal_locked(generation)
        if recognizer._restart_attempted:
            return False, None
        recognizer._restart_attempted = True
        try:
            generation.write_q.put_nowait(None)
        except queue.Full:
            pass
        try:
            replacement = recognizer._new_generation_locked(generation.number + 1)
            recognizer._activate_generation_locked(replacement)
        except Exception as exc:
            restart_error = exc
    return True, restart_error


def _mark_clean_sidecar_exit(recognizer: Any, generation: _SidecarGeneration) -> None:
    with recognizer._state_lock:
        if recognizer._generation is generation:
            _mark_generation_terminal_locked(generation)


def _enqueue_sidecar_audio(recognizer: Any, pcm: bytes) -> None:
    with recognizer._state_lock:
        generation = recognizer._generation
        if recognizer._finalizing or generation.terminal or not generation.accepting_audio:
            raise AsrSidecarUnavailableError("ASR sidecar is not accepting audio")
        try:
            generation.write_q.put_nowait(pcm)
            generation.audio_chunks_enqueued += 1
            generation.audio_bytes_enqueued += len(pcm)
            generation.max_write_queue_depth = max(
                generation.max_write_queue_depth,
                generation.write_q.qsize(),
            )
        except queue.Full as exc:
            raise AsrSidecarUnavailableError("ASR sidecar audio queue is full") from exc


def _record_sidecar_audio_written(generation: _SidecarGeneration, pcm: bytes) -> None:
    generation.audio_chunks_written += 1
    generation.audio_bytes_written += len(pcm)


def _close_sidecar_pipe(pipe: Any) -> None:
    try:
        pipe.close()
    except Exception:
        pass


def _kill_sidecar_process(proc: Any) -> None:
    try:
        proc.kill()
    except Exception:
        pass


def _wait_and_reap_sidecar_process(proc: Any, *, timeout_s: float | None = None) -> None:
    wait_timeout_s = SIDECAR_PROCESS_WAIT_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        proc.wait(timeout=wait_timeout_s)
        return
    except Exception:
        _kill_sidecar_process(proc)
    try:
        proc.wait(timeout=SIDECAR_PROCESS_WAIT_TIMEOUT_S)
    except Exception:
        pass


def _join_sidecar_thread(thread: threading.Thread | None, timeout: float | None = None) -> bool:
    if thread is None:
        return True
    thread.join(timeout=SIDECAR_THREAD_JOIN_TIMEOUT_S if timeout is None else timeout)
    return not thread.is_alive()


def _sidecar_graceful_drain_timeout_s(recognizer: Any) -> float:
    """Give burst-fed local ASR enough time to consume stdin and emit final."""
    chunk_count = max(0, int(getattr(recognizer, "_seq", 0) or 0))
    if chunk_count <= 1:
        return SIDECAR_PROCESS_WAIT_TIMEOUT_S
    estimated_audio_s = chunk_count * 0.3
    return min(
        SIDECAR_GRACEFUL_DRAIN_MAX_S,
        max(
            SIDECAR_PROCESS_WAIT_TIMEOUT_S,
            SIDECAR_GRACEFUL_DRAIN_MARGIN_S
            + estimated_audio_s * SIDECAR_GRACEFUL_DRAIN_AUDIO_FACTOR,
        ),
    )


def _wait_for_sidecar_reader_drain(
    recognizer: Any,
    generation: _SidecarGeneration,
    *,
    timeout_s: float | None = None,
) -> bool:
    """Wait briefly for stdout finals; timeout returns safely with only queued events."""
    reader = generation.reader
    if reader is None:
        return True
    drain_timeout_s = (
        SIDECAR_READER_DRAIN_TIMEOUT_S
        if timeout_s is None
        else max(0.0, min(SIDECAR_READER_DRAIN_TIMEOUT_S, timeout_s))
    )
    reader.join(timeout=drain_timeout_s)
    if not reader.is_alive():
        return True
    reason = f"asr_sidecar_reader_drain_timeout: generation={generation.number}"
    _log.error(
        "asr.sidecar.reader_drain_timeout",
        session_id=recognizer.session_id,
        provider=recognizer.provider,
        generation=generation.number,
        timeout_s=drain_timeout_s,
    )
    try:
        get_degradation_controller().set_level(LEVEL_HEAVY, reason)
    except Exception:
        pass
    with recognizer._state_lock:
        _mark_generation_terminal_locked(generation)
    _kill_sidecar_process(generation.proc)
    _close_sidecar_pipe(getattr(generation.proc, "stdout", None))
    _close_sidecar_pipe(getattr(generation.proc, "stderr", None))
    _wait_and_reap_sidecar_process(generation.proc)
    _join_sidecar_thread(reader)
    _join_sidecar_thread(generation.stderr_reader)
    return False


def _claim_sidecar_shutdown(recognizer: Any, *, abort: bool) -> _SidecarGeneration | None:
    with recognizer._state_lock:
        if recognizer._shutdown_started:
            return None
        recognizer._shutdown_started = True
        recognizer._finalizing = True
        generation = recognizer._generation
        generation.accepting_audio = False
        if abort:
            generation.terminal = True
            generation.accepting_events = False
        return generation


def _remaining_sidecar_shutdown_s(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _shutdown_sidecar_generation(recognizer: Any, generation: _SidecarGeneration, *, abort: bool) -> None:
    proc = generation.proc
    writer = generation.writer
    started_at = time.monotonic()
    total_timeout_s = (
        SIDECAR_PROCESS_WAIT_TIMEOUT_S
        if abort
        else _sidecar_graceful_drain_timeout_s(recognizer)
    )
    deadline = started_at + total_timeout_s
    write_queue_depth_at_end = generation.write_q.qsize()
    sentinel_started_at = time.monotonic()
    try:
        if abort:
            generation.write_q.put_nowait(None)
        else:
            generation.write_q.put(
                None,
                timeout=_remaining_sidecar_shutdown_s(deadline),
            )
    except queue.Full:
        pass
    sentinel_ms = round((time.monotonic() - sentinel_started_at) * 1_000, 2)

    writer_started_at = time.monotonic()
    if abort:
        _kill_sidecar_process(proc)
        writer_stopped = _join_sidecar_thread(
            writer,
            _remaining_sidecar_shutdown_s(deadline),
        )
    else:
        writer_stopped = _join_sidecar_thread(
            writer,
            _remaining_sidecar_shutdown_s(deadline),
        )
        if not writer_stopped:
            _kill_sidecar_process(proc)
            writer_stopped = _join_sidecar_thread(writer)
    writer_drain_ms = round((time.monotonic() - writer_started_at) * 1_000, 2)

    if writer_stopped:
        _close_sidecar_pipe(getattr(proc, "stdin", None))
    else:
        _log.error(
            "asr.sidecar.writer_shutdown_timeout",
            session_id=recognizer.session_id,
            provider=recognizer.provider,
            generation=generation.number,
        )

    process_started_at = time.monotonic()
    _wait_and_reap_sidecar_process(
        proc,
        timeout_s=_remaining_sidecar_shutdown_s(deadline),
    )
    process_wait_ms = round((time.monotonic() - process_started_at) * 1_000, 2)
    reader_started_at = time.monotonic()
    if abort:
        _close_sidecar_pipe(getattr(proc, "stdout", None))
        _close_sidecar_pipe(getattr(proc, "stderr", None))
        _join_sidecar_thread(
            generation.reader,
            _remaining_sidecar_shutdown_s(deadline),
        )
    else:
        _wait_for_sidecar_reader_drain(
            recognizer,
            generation,
            timeout_s=_remaining_sidecar_shutdown_s(deadline),
        )
    reader_drain_ms = round((time.monotonic() - reader_started_at) * 1_000, 2)
    _join_sidecar_thread(
        generation.stderr_reader,
        _remaining_sidecar_shutdown_s(deadline),
    )
    total_ms = round((time.monotonic() - started_at) * 1_000, 2)
    diagnostics = {
        "abort": abort,
        "budget_ms": round(total_timeout_s * 1_000, 2),
        "write_queue_depth_at_end": write_queue_depth_at_end,
        "max_write_queue_depth": generation.max_write_queue_depth,
        "audio_chunks_enqueued": generation.audio_chunks_enqueued,
        "audio_chunks_written": generation.audio_chunks_written,
        "audio_bytes_enqueued": generation.audio_bytes_enqueued,
        "audio_bytes_written": generation.audio_bytes_written,
        "unprocessed_chunks": max(
            0,
            generation.audio_chunks_enqueued - generation.audio_chunks_written,
        ),
        "sentinel_ms": sentinel_ms,
        "writer_drain_ms": writer_drain_ms,
        "process_wait_ms": process_wait_ms,
        "reader_drain_ms": reader_drain_ms,
        "total_ms": total_ms,
        "deadline_exhausted": time.monotonic() >= deadline,
        "writer_stopped": writer_stopped,
    }
    worker_diagnostics = getattr(recognizer, "worker_diagnostics", None)
    if isinstance(worker_diagnostics, dict) and worker_diagnostics:
        diagnostics["worker"] = dict(worker_diagnostics)
    recognizer.shutdown_diagnostics = diagnostics
    _log.info(
        "asr.sidecar.shutdown",
        session_id=recognizer.session_id,
        provider=recognizer.provider,
        generation=generation.number,
        **diagnostics,
    )
    with recognizer._state_lock:
        _mark_generation_terminal_locked(generation)
        recognizer._shutdown_complete = True


class SherpaSidecarRecognizer:
    """Real ASR sidecar: spawns sherpa_stream_worker.py (sherpa 3.11 venv) as a
    subprocess, feeds float32 PCM chunks via stdin, reads JSON ASR events from
    stdout. This bridges the 3.14 web backend to the 3.11 sherpa-onnx runtime."""

    provider = "sherpa_onnx_realtime"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons: list[str] = []

    def __init__(self, session_id: str, model_dir: Path, venv_python: Path | None = None):
        self.session_id = session_id
        python = str(venv_python or _SHERPA_VENV_PY)
        self._cmd = [python, str(_SHERPA_WORKER), "--model-dir", str(model_dir)]
        self._model_dir = model_dir
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._seq = 0
        self._state_lock = threading.Lock()
        self._finalizing = False
        self._restart_attempted = False
        self._shutdown_started = False
        self._shutdown_complete = False
        with self._state_lock:
            generation = self._new_generation_locked(1)
            self._activate_generation_locked(generation)
        _log.info("asr.sidecar.start", session_id=session_id, model=str(model_dir))

    def _new_generation_locked(self, number: int) -> _SidecarGeneration:
        proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return _SidecarGeneration(
            number=number,
            proc=proc,
            write_q=queue.Queue(maxsize=SIDECAR_WRITE_QUEUE_MAX_CHUNKS),
            stderr_lines=[],
            start_time=time.monotonic(),
        )

    def _activate_generation_locked(self, generation: _SidecarGeneration) -> None:
        generation.stderr_reader = threading.Thread(
            target=self._stderr_loop,
            args=(generation,),
            daemon=True,
        )
        generation.reader = threading.Thread(
            target=self._read_loop,
            args=(generation,),
            daemon=True,
        )
        generation.writer = threading.Thread(
            target=self._write_loop,
            args=(generation,),
            daemon=True,
        )
        generation.watchdog = threading.Thread(
            target=self._cold_start_watchdog,
            args=(generation,),
            daemon=True,
        )
        self._generation = generation
        self._proc = generation.proc
        self._write_q = generation.write_q
        self._stderr_lines = generation.stderr_lines
        self._start_time = generation.start_time
        self._ready_time = generation.ready_time
        self._stderr_reader = generation.stderr_reader
        self._reader = generation.reader
        self._writer = generation.writer
        generation.stderr_reader.start()
        generation.reader.start()
        generation.writer.start()
        generation.watchdog.start()

    def _read_loop(self, generation: _SidecarGeneration) -> None:
        proc = generation.proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                became_ready = False
                with self._state_lock:
                    if self._generation is not generation or not generation.accepting_events:
                        continue
                    self._q.put(event)
                    if generation.ready_time is None:
                        generation.ready_time = time.monotonic()
                        self._ready_time = generation.ready_time
                        became_ready = True
                if became_ready and generation.ready_time is not None:
                    elapsed = generation.ready_time - generation.start_time
                    if elapsed > 15.0:
                        _log.warning("asr.sidecar.cold_start_slow", session_id=self.session_id, elapsed_s=round(elapsed, 1))
        except Exception as exc:
            _log.warning("asr.sidecar.read_loop_error", session_id=self.session_id, error=str(exc))
        rc = proc.poll()
        if rc == 0:
            _mark_clean_sidecar_exit(self, generation)
        elif rc is not None:
            self._handle_crash(rc, generation=generation)

    def _stderr_loop(self, generation: _SidecarGeneration) -> None:
        proc = generation.proc
        try:
            for line in proc.stderr:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if line_str:
                    generation.stderr_lines.append(line_str)
                    _log.debug("asr.sidecar.stderr", session_id=self.session_id, line=line_str)
        except Exception:
            pass

    def _cold_start_watchdog(self, generation: _SidecarGeneration) -> None:
        time.sleep(15.0)
        with self._state_lock:
            timed_out = (
                self._generation is generation
                and not self._finalizing
                and generation.ready_time is None
            )
        if timed_out:
            _log.warning("asr.sidecar.cold_start_timeout", session_id=self.session_id, timeout_s=15.0)

    def _handle_crash(self, exit_code: int, *, generation: _SidecarGeneration) -> None:
        handled, restart_error = _restart_sidecar_generation(self, generation, exit_code)
        if not handled:
            return

        stderr_tail = "\n".join(generation.stderr_lines[-20:])
        _log.error(
            "asr.sidecar.crashed",
            session_id=self.session_id,
            exit_code=exit_code,
            stderr=stderr_tail,
        )
        try:
            get_degradation_controller().set_level(LEVEL_HEAVY, f"asr_sidecar_crashed: exit_code={exit_code}")
        except Exception:
            pass
        if restart_error is None:
            _log.info("asr.sidecar.restarted", session_id=self.session_id)
        else:
            _log.error("asr.sidecar.restart_failed", session_id=self.session_id, error=str(restart_error))
            try:
                get_degradation_controller().set_level(LEVEL_HEAVY, f"asr_sidecar_restart_failed: {restart_error}")
            except Exception:
                pass

    def _write_loop(self, generation: _SidecarGeneration) -> None:
        """Writer thread: drains _write_q and writes to stdin (blocking write
        happens here, not in the async event loop). Prevents burst PCM from
        blocking the WebSocket handler while the worker loads the model."""
        proc = generation.proc
        write_q = generation.write_q
        try:
            while True:
                item = write_q.get()
                if item is None:
                    break
                try:
                    proc.stdin.write(item)
                    proc.stdin.flush()
                    _record_sidecar_audio_written(generation, item)
                except Exception:
                    break
        except Exception:
            pass

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._seq += 1
        _enqueue_sidecar_audio(self, pcm)
        events: list[dict[str, Any]] = []
        while not self._q.empty():
            ev = self._q.get_nowait()
            ev["segment_id"] = _session_scoped_segment_id(
                self.session_id,
                ev.get("segment_id"),
                fallback=f"stream_seg_{self.session_id}",
            )
            ev.setdefault("confidence", 0.8)
            events.append(ev)
        if not events:
            events.append({
                "event_type": "partial",
                "segment_id": f"stream_seg_{self.session_id}",
                "text": "",
                "start_ms": (self._seq - 1) * 300,
                "end_ms": self._seq * 300,
                "confidence": 0.7,
            })
        return events

    def finalize(self) -> list[dict[str, Any]]:
        generation = _claim_sidecar_shutdown(self, abort=False)
        if generation is None:
            return []
        _shutdown_sidecar_generation(self, generation, abort=False)
        # drain ALL remaining events (multiple finals may arrive during burst streaming)
        events: list[dict[str, Any]] = []
        while not self._q.empty():
            ev = self._q.get_nowait()
            ev["segment_id"] = _session_scoped_segment_id(
                self.session_id,
                ev.get("segment_id"),
                fallback=f"stream_seg_{self.session_id}",
            )
            ev.setdefault("confidence", 0.9)
            events.append(ev)
        if not events:
            events.append({"event_type": "final", "segment_id": f"stream_seg_{self.session_id}", "text": "", "confidence": 0.9})
        _log.info("asr.sidecar.end", session_id=self.session_id, events=len(events))
        return events

    def abort(self) -> None:
        generation = _claim_sidecar_shutdown(self, abort=True)
        if generation is None:
            return
        _shutdown_sidecar_generation(self, generation, abort=True)


def _configured_local_path(env_name: str, default: Path) -> Path:
    configured = os.environ.get(env_name, "").strip()
    return Path(configured).expanduser() if configured else default


def _funasr_process_environment() -> dict[str, str]:
    """Use the bundled FunASR runtime instead of the backend's Python home."""
    environment = os.environ.copy()
    python_home = os.environ.get("MEETING_COPILOT_FUNASR_PYTHON_HOME", "").strip()
    python_path = os.environ.get("MEETING_COPILOT_FUNASR_PYTHONPATH", "").strip()
    if python_home:
        environment["PYTHONHOME"] = python_home
    if python_path:
        environment["PYTHONPATH"] = python_path
    return environment


_FUNASR_VENV_PY = _configured_local_path(
    "MEETING_COPILOT_FUNASR_PYTHON",
    _REPO_ROOT / "code" / "asr_runtime" / ".venv-funasr" / "bin" / "python",
)
_FUNASR_WORKER = _configured_local_path(
    "MEETING_COPILOT_FUNASR_WORKER",
    _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "funasr_stream_worker.py",
)
_FUNASR_MODEL_DIR = _configured_local_path(
    "MEETING_COPILOT_FUNASR_MODEL_DIR",
    Path.home()
    / ".cache"
    / "modelscope"
    / "hub"
    / "models"
    / "iic"
    / "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
)
FUNASR_REALTIME_PROFILE = "balanced_chinese_meeting"
FUNASR_REALTIME_CHUNK_SIZE = [0, 30, 15]
FUNASR_REALTIME_ENCODER_CHUNK_LOOK_BACK = 4
FUNASR_REALTIME_DECODER_CHUNK_LOOK_BACK = 1


def _funasr_worker_command(venv_python: Path | None = None) -> list[str]:
    command = [
        str(venv_python or _FUNASR_VENV_PY),
        str(_FUNASR_WORKER),
        "--model",
        str(_FUNASR_MODEL_DIR),
        "--chunk-size",
        ",".join(str(value) for value in FUNASR_REALTIME_CHUNK_SIZE),
        "--encoder-chunk-look-back",
        str(FUNASR_REALTIME_ENCODER_CHUNK_LOOK_BACK),
        "--decoder-chunk-look-back",
        str(FUNASR_REALTIME_DECODER_CHUNK_LOOK_BACK),
    ]
    try:
        hotwords = _hotwords()
        if hotwords:
            command += ["--hotwords", " ".join(hotwords)]
    except Exception:
        pass
    return command


class FunasrSidecarRecognizer:
    """Real-time FunASR streaming sidecar (G2). Spawns funasr_stream_worker.py
    (funasr 3.11 venv) with technical hotwords; feeds float32 PCM via stdin,
    reads JSON ASR events from stdout. Better Chinese accuracy than sherpa."""

    provider = "funasr_realtime"
    provider_mode = "real"
    is_mock = False
    fallback_used = False
    degradation_reasons: list[str] = []
    asr_profile = FUNASR_REALTIME_PROFILE
    chunk_size = FUNASR_REALTIME_CHUNK_SIZE

    def __init__(self, session_id: str, venv_python: Path | None = None):
        self.session_id = session_id
        self.asr_profile = FUNASR_REALTIME_PROFILE
        self.chunk_size = list(FUNASR_REALTIME_CHUNK_SIZE)
        self._cmd = _funasr_worker_command(venv_python)
        self._q: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._seq = 0
        self._state_lock = threading.Lock()
        self._finalizing = False
        self._restart_attempted = False
        self._shutdown_started = False
        self._shutdown_complete = False
        self.worker_diagnostics: dict[str, Any] = {}
        self.shutdown_diagnostics: dict[str, Any] = {}
        with self._state_lock:
            generation = self._new_generation_locked(1)
            self._activate_generation_locked(generation)
        _log.info("asr.sidecar.funasr.start", session_id=session_id)

    def _new_generation_locked(self, number: int) -> _SidecarGeneration:
        proc = subprocess.Popen(
            self._cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_funasr_process_environment(),
        )
        return _SidecarGeneration(
            number=number,
            proc=proc,
            write_q=queue.Queue(maxsize=SIDECAR_WRITE_QUEUE_MAX_CHUNKS),
            stderr_lines=[],
            start_time=time.monotonic(),
        )

    def _activate_generation_locked(self, generation: _SidecarGeneration) -> None:
        generation.stderr_reader = threading.Thread(
            target=self._stderr_loop,
            args=(generation,),
            daemon=True,
        )
        generation.reader = threading.Thread(
            target=self._read_loop,
            args=(generation,),
            daemon=True,
        )
        generation.writer = threading.Thread(
            target=self._write_loop,
            args=(generation,),
            daemon=True,
        )
        generation.watchdog = threading.Thread(
            target=self._cold_start_watchdog,
            args=(generation,),
            daemon=True,
        )
        self._generation = generation
        self._proc = generation.proc
        self._write_q = generation.write_q
        self._stderr_lines = generation.stderr_lines
        self._start_time = generation.start_time
        self._ready_time = generation.ready_time
        self._stderr_reader = generation.stderr_reader
        self._reader = generation.reader
        self._writer = generation.writer
        generation.stderr_reader.start()
        generation.reader.start()
        generation.writer.start()
        generation.watchdog.start()

    def _write_loop(self, generation: _SidecarGeneration) -> None:
        proc = generation.proc
        write_q = generation.write_q
        try:
            while True:
                item = write_q.get()
                if item is None:
                    break
                try:
                    proc.stdin.write(item)
                    proc.stdin.flush()
                    _record_sidecar_audio_written(generation, item)
                except Exception:
                    break
        except Exception:
            pass

    def _read_loop(self, generation: _SidecarGeneration) -> None:
        proc = generation.proc
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                became_ready = False
                with self._state_lock:
                    if self._generation is not generation or not generation.accepting_events:
                        continue
                    if event.get("event_type") == "telemetry":
                        self.worker_diagnostics = {
                            key: event[key]
                            for key in (
                                "input_samples",
                                "input_seconds",
                                "inference_calls",
                                "inference_total_ms",
                                "inference_max_ms",
                                "worker_total_ms",
                                "realtime_factor",
                            )
                            if key in event
                        }
                        continue
                    if event.get("event_type") == "ready" and generation.ready_time is None:
                        generation.ready_time = time.monotonic()
                        self._ready_time = generation.ready_time
                        generation.ready_event.set()
                        became_ready = True
                    if event.get("event_type") != "ready":
                        self._q.put(event)
                if became_ready and generation.ready_time is not None:
                    elapsed = generation.ready_time - generation.start_time
                    if elapsed > 15.0:
                        _log.warning("asr.sidecar.funasr.cold_start_slow", session_id=self.session_id, elapsed_s=round(elapsed, 1))
        except Exception as exc:
            _log.warning("asr.sidecar.funasr.read_loop_error", session_id=self.session_id, error=str(exc))
        rc = proc.poll()
        if rc == 0:
            _mark_clean_sidecar_exit(self, generation)
        elif rc is not None:
            self._handle_crash(rc, generation=generation)

    def _stderr_loop(self, generation: _SidecarGeneration) -> None:
        proc = generation.proc
        try:
            for line in proc.stderr:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if line_str:
                    generation.stderr_lines.append(line_str)
                    _log.debug("asr.sidecar.funasr.stderr", session_id=self.session_id, line=line_str)
        except Exception:
            pass

    def _cold_start_watchdog(self, generation: _SidecarGeneration) -> None:
        time.sleep(15.0)
        with self._state_lock:
            timed_out = (
                self._generation is generation
                and not self._finalizing
                and generation.ready_time is None
            )
        if timed_out:
            _log.warning("asr.sidecar.funasr.cold_start_timeout", session_id=self.session_id, timeout_s=15.0)

    def _handle_crash(self, exit_code: int, *, generation: _SidecarGeneration) -> None:
        handled, restart_error = _restart_sidecar_generation(self, generation, exit_code)
        if not handled:
            return

        stderr_tail = "\n".join(generation.stderr_lines[-20:])
        _log.error(
            "asr.sidecar.funasr.crashed",
            session_id=self.session_id,
            exit_code=exit_code,
            stderr=stderr_tail,
        )
        try:
            get_degradation_controller().set_level(LEVEL_HEAVY, f"asr_sidecar_crashed: exit_code={exit_code}")
        except Exception:
            pass
        if restart_error is None:
            _log.info("asr.sidecar.funasr.restarted", session_id=self.session_id)
        else:
            _log.error("asr.sidecar.funasr.restart_failed", session_id=self.session_id, error=str(restart_error))
            try:
                get_degradation_controller().set_level(LEVEL_HEAVY, f"asr_sidecar_restart_failed: {restart_error}")
            except Exception:
                pass

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._seq += 1
        _enqueue_sidecar_audio(self, pcm)
        events: list[dict[str, Any]] = []
        while not self._q.empty():
            ev = self._q.get_nowait()
            ev["segment_id"] = _session_scoped_segment_id(
                self.session_id,
                ev.get("segment_id"),
                fallback=f"stream_seg_{self.session_id}",
            )
            ev.setdefault("confidence", 0.8)
            events.append(ev)
        if not events:
            events.append({"event_type": "partial", "segment_id": f"stream_seg_{self.session_id}", "text": "", "start_ms": (self._seq - 1) * 300, "end_ms": self._seq * 300, "confidence": 0.7})
        return events

    def wait_ready(self, timeout: float | None = None) -> bool:
        """Wait until the worker has loaded its local model and emitted ready."""
        with self._state_lock:
            generation = self._generation
            if generation.ready_event.is_set():
                return True
        return generation.ready_event.wait(timeout)

    def finalize(self) -> dict[str, Any]:
        generation = _claim_sidecar_shutdown(self, abort=False)
        if generation is None:
            return []
        _shutdown_sidecar_generation(self, generation, abort=False)
        events: list[dict[str, Any]] = []
        while not self._q.empty():
            ev = self._q.get_nowait()
            ev["segment_id"] = _session_scoped_segment_id(
                self.session_id,
                ev.get("segment_id"),
                fallback=f"stream_seg_{self.session_id}",
            )
            ev.setdefault("confidence", 0.9)
            events.append(ev)
        if not events:
            events.append({"event_type": "final", "segment_id": f"stream_seg_{self.session_id}", "text": "", "confidence": 0.9})
        _log.info("asr.sidecar.funasr.end", session_id=self.session_id, events=len(events))
        return events

    def abort(self) -> None:
        generation = _claim_sidecar_shutdown(self, abort=True)
        if generation is None:
            return
        _shutdown_sidecar_generation(self, generation, abort=True)


class _UnavailableFunasrRecognizer:
    provider = "funasr_realtime"
    provider_mode = "unavailable"
    is_mock = False
    fallback_used = True

    def __init__(self, reason: str):
        self.degradation_reasons = [reason]

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        raise FunasrResidentUnavailableError(self.degradation_reasons[0])

    def finalize(self) -> list[dict[str, Any]]:
        return []

    def abort(self) -> None:
        return None


_FUNASR_RESIDENT_MANAGER_LOCK = threading.Lock()
_FUNASR_RESIDENT_MANAGER: FunasrResidentWorkerManager | None = None


def _funasr_resident_enabled() -> bool:
    return os.environ.get("MEETING_COPILOT_FUNASR_RESIDENT", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _get_funasr_resident_manager() -> FunasrResidentWorkerManager:
    global _FUNASR_RESIDENT_MANAGER
    with _FUNASR_RESIDENT_MANAGER_LOCK:
        if _FUNASR_RESIDENT_MANAGER is None:
            _FUNASR_RESIDENT_MANAGER = FunasrResidentWorkerManager(
                _funasr_worker_command(),
                environment=_funasr_process_environment(),
            )
        return _FUNASR_RESIDENT_MANAGER


def shutdown_funasr_resident_manager() -> None:
    """Terminate and reap the process-level worker during app shutdown/tests."""
    global _FUNASR_RESIDENT_MANAGER
    with _FUNASR_RESIDENT_MANAGER_LOCK:
        manager = _FUNASR_RESIDENT_MANAGER
        _FUNASR_RESIDENT_MANAGER = None
    if manager is not None:
        manager.shutdown()


def prewarm_funasr_resident_manager() -> bool:
    """Start loading the local model before the first meeting claims it."""
    if not _funasr_resident_enabled() or not funasr_realtime_available():
        return False
    try:
        _get_funasr_resident_manager().start()
    except Exception as exc:
        _log.warning("asr.sidecar.funasr.resident_prewarm_failed", error=str(exc))
        return False
    return True


def _maybe_funasr_sidecar(
    session_id: str,
) -> FunasrSidecarRecognizer | FunasrResidentSession | _UnavailableFunasrRecognizer | None:
    """Return a FunasrSidecarRecognizer if the funasr venv + worker exist, else None."""
    if not funasr_realtime_available():
        return None
    if _funasr_resident_enabled():
        try:
            recognizer = _get_funasr_resident_manager().create_session(session_id)
            recognizer.asr_profile = FUNASR_REALTIME_PROFILE
            recognizer.chunk_size = list(FUNASR_REALTIME_CHUNK_SIZE)
            _log.info("asr.sidecar.funasr.resident_session_start", session_id=session_id)
            return recognizer
        except FunasrResidentBusyError:
            _log.warning("asr.sidecar.funasr.resident_busy", session_id=session_id)
            return _UnavailableFunasrRecognizer("funasr_resident_worker_busy")
        except Exception as exc:
            _log.warning("asr.sidecar.funasr.resident_spawn_failed", error=str(exc))
    try:
        return FunasrSidecarRecognizer(session_id)
    except Exception as exc:
        _log.warning("asr.sidecar.funasr.spawn_failed", error=str(exc))
        return None


def funasr_realtime_available() -> bool:
    """Return true only when the local runtime and model files are ready.

    Passing a model name to FunASR allows ModelScope to download at runtime. The
    meeting product must fail closed instead of downloading an 840MB model during
    a live meeting, so the local model directory is part of readiness.
    """
    return (
        _FUNASR_VENV_PY.is_file()
        and _FUNASR_WORKER.is_file()
        and _FUNASR_MODEL_DIR.is_dir()
        and (_FUNASR_MODEL_DIR / "model.pt").is_file()
        and (_FUNASR_MODEL_DIR / "config.yaml").is_file()
    )


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


def _session_scoped_segment_id(session_id: str, segment_id: Any, *, fallback: str) -> str:
    raw_segment_id = str(segment_id or "").strip()
    if not raw_segment_id:
        return fallback
    if raw_segment_id.startswith(f"{session_id}_"):
        return raw_segment_id
    return f"{session_id}_{raw_segment_id}"


def _funasr_source_segment_id(session_id: str, event: dict[str, Any]) -> str:
    source_segment_id = event.get("source_segment_id") or event.get("segment_id")
    if not str(source_segment_id or "").strip():
        return ""
    return _session_scoped_segment_id(
        session_id,
        source_segment_id,
        fallback=f"stream_seg_{session_id}",
    )


async def reject_recording_stream(
    websocket,
    *,
    degradation_level: int,
    reason: str,
) -> None:
    await websocket.accept()
    await websocket.send_text(json.dumps({
        "event_type": "provider_error",
        "error_code": "recording_unavailable",
        "message": "录音当前不可用，请检查麦克风权限、音频设备或本地服务后重试。",
        "degradation_level": degradation_level,
        "reason": reason,
    }, ensure_ascii=False))
    await websocket.close()


async def handle_recording_only_stream(
    websocket,
    session_id: str,
    *,
    asr_live_repo,
    audio_source: str | None,
    audio_asset_data_dir: str | Path | None,
    degradation_reason: str,
    on_audio_chunk_committed: Callable[[dict[str, Any]], Any] | None = None,
    authorize_audio_chunk_commit: Callable[[dict[str, Any]], bool] | None = None,
    on_audio_recording_started: Callable[[dict[str, Any]], Any] | None = None,
    on_audio_recording_sealed: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    await websocket.accept()
    if audio_asset_data_dir is None:
        await websocket.send_text(json.dumps({
            "event_type": "provider_error",
            "error_code": "recording_storage_unavailable",
            "message": "录音存储目录未配置，当前不能进入仅录音模式。",
            "degradation_level": 3,
        }, ensure_ascii=False))
        await websocket.close()
        return

    source_type = audio_source or "live_asr_stream"
    if on_audio_recording_started is not None:
        on_audio_recording_started({
            "session_id": session_id,
            "source_type": source_type,
            "sample_rate_hz": 16_000,
        })
    writer = RealtimeWavAssetWriter(
        data_dir=audio_asset_data_dir,
        session_id=session_id,
        source_type=source_type,
        on_chunk_committed=on_audio_chunk_committed,
        authorize_chunk_commit=authorize_audio_chunk_commit,
    )
    await websocket.send_text(json.dumps({
        "event_type": "recording_only",
        "message": "实时识别暂不可用，本次会议将继续保留录音。",
        "degradation_level": 3,
    }, ensure_ascii=False))

    def persist(audio_asset: dict[str, Any], *, interrupted: bool) -> None:
        reasons = [degradation_reason]
        if interrupted:
            reasons.append("stream_interrupted")
        streaming_events = [] if interrupted else [{
            "event_type": "end_of_stream",
            "end_ms": int(audio_asset.get("duration_ms") or 0),
            "received_at_ms": int(audio_asset.get("duration_ms") or 0),
        }]
        live_events = build_asr_live_events(
            session_id=session_id,
            provider="recording_only_local_audio",
            streaming_events=streaming_events,
            is_mock=False,
        )
        base_record = {
            "session_id": session_id,
            "provider": "recording_only_local_audio",
            "provider_mode": "recording_only",
            "is_mock": False,
            "asr_fallback_used": False,
            "degradation_reasons": reasons,
            "audio_source": audio_source,
            "input_source": audio_source,
            "audio": audio_asset,
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "events": live_events,
            "last_activity_at_epoch_ms": time.time_ns() // 1_000_000,
        }
        try:
            asr_live_repo.get(session_id)
        except KeyError:
            asr_live_repo.create(base_record)
        else:
            asr_live_repo.update(
                session_id,
                lambda existing: {
                    **existing,
                    **base_record,
                    "degradation_reasons": _dedupe_values([
                        *list(existing.get("degradation_reasons") or []),
                        *reasons,
                    ]),
                    "suggestion_cards": list(existing.get("suggestion_cards") or []),
                    "approach_cards": list(existing.get("approach_cards") or []),
                    "minutes": dict(existing.get("minutes") or {}),
                },
            )

    def seal_audio(*, interrupted: bool) -> dict[str, Any]:
        if on_audio_recording_sealed is None:
            return writer.close()
        audio_asset = writer.seal()
        on_audio_recording_sealed({**audio_asset, "interrupted": interrupted})
        return audio_asset

    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                writer.write_float32_pcm(message["bytes"])
            elif message.get("text") == "END":
                audio_asset = seal_audio(interrupted=False)
                persist(audio_asset, interrupted=False)
                await websocket.close()
                return
    except Exception as exc:
        try:
            audio_asset = seal_audio(interrupted=True)
            persist(audio_asset, interrupted=True)
        except Exception as persist_exc:
            writer.discard()
            _log.warning(
                "asr.recording_only.persist_failed",
                session_id=session_id,
                error=str(persist_exc),
            )
        _log.warning("asr.recording_only.aborted", session_id=session_id, error=str(exc))
        try:
            await websocket.close()
        except Exception:
            pass


def _dedupe_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _compact_cumulative_source_snapshots(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only the snapshots needed to restore the latest canonical projection."""

    latest_by_type: dict[str, int] = {}
    latest_reconciled_index: int | None = None
    for index, event in enumerate(events):
        event_type = str(event.get("event_type") or "")
        if event_type not in {"transcript_partial", "transcript_final"}:
            continue
        payload = dict(event.get("payload") or {})
        if not payload.get("source_snapshot_text"):
            continue
        latest_by_type[event_type] = index
        if payload.get("projection_reconciled"):
            latest_reconciled_index = index

    retained_indexes = set(latest_by_type.values())
    if latest_reconciled_index is not None:
        retained_indexes.add(latest_reconciled_index)

    compacted: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        payload = dict(event.get("payload") or {})
        if index not in retained_indexes and payload.get("source_snapshot_text"):
            payload.pop("source_snapshot_text", None)
            compacted.append({**event, "payload": payload})
        else:
            compacted.append(event)
    return compacted


def _bound_streaming_projection_events(
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Keep a recent live projection window; V2 tables retain complete facts."""

    final_indexes = [
        index for index, event in enumerate(events)
        if event.get("event_type") == "final"
    ]
    partial_indexes = [
        index for index, event in enumerate(events)
        if event.get("event_type") == "partial"
    ]
    other_indexes = [
        index for index, event in enumerate(events)
        if event.get("event_type") not in {"final", "partial"}
    ]
    retained_indexes = {
        *final_indexes[-LIVE_PROJECTION_MAX_FINALS:],
        *partial_indexes[-LIVE_PROJECTION_MAX_PARTIALS:],
        *other_indexes,
    }
    bounded = [event for index, event in enumerate(events) if index in retained_indexes]
    return bounded, max(0, len(events) - len(bounded))


async def handle_stream(
    websocket,
    session_id: str,
    asr_live_repo=None,
    provider: str = "local_real_asr",
    *,
    allow_fake_fallback: bool = False,
    audio_source: str | None = None,
    audio_asset_data_dir: str | Path | None = None,
    l3_normalize_enabled: bool = True,
    on_final_committed: Callable[[dict[str, Any]], Any] | None = None,
    on_audio_chunk_committed: Callable[[dict[str, Any]], Any] | None = None,
    authorize_audio_chunk_commit: Callable[[dict[str, Any]], bool] | None = None,
    on_audio_active: Callable[[dict[str, Any]], Any] | None = None,
    on_audio_recording_started: Callable[[dict[str, Any]], Any] | None = None,
    on_audio_recording_sealed: Callable[[dict[str, Any]], Any] | None = None,
) -> None:
    """Handle one WS audio stream: read chunks, emit ASR events back over the WS.

    If asr_live_repo is provided, accumulates real ASR final events and persists
    a session record on END — so the real mic -> ASR -> session -> LLM cards
    pipeline is connected end-to-end (llm-execution-runs / approach-cards /
    minutes can then run on the real ASR session).
    """
    await websocket.accept()
    recognizer = get_recognizer(session_id)
    provider_metadata = _recognizer_provider_metadata(recognizer, configured_provider=provider)
    if _should_block_recognizer(provider_metadata, allow_fake_fallback=allow_fake_fallback):
        await websocket.send_text(json.dumps(_blocked_recognizer_event(provider_metadata), ensure_ascii=False))
        await websocket.close()
        _log.warning(
            "asr.stream.blocked_unavailable_real_provider",
            session_id=session_id,
            provider=provider_metadata["provider"],
            provider_mode=provider_metadata["provider_mode"],
            fallback_used=provider_metadata["fallback_used"],
        )
        return
    # Keep blocking audio/ASR/persistence work ordered per meeting while
    # allowing the async server to continue heartbeats and other requests.
    session_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix=f"meeting-stream-{session_id[:24]}",
    )
    event_loop = _get_running_loop()

    async def _run_blocking(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        operation = functools.partial(fn, *args, **kwargs)
        future = event_loop.run_in_executor(session_executor, operation)
        return await future

    async def _shutdown_session_executor() -> None:
        # All stream operations are awaited in sequence. The shutdown itself
        # is still moved off the event loop so an interrupted stream cannot
        # accidentally block the server while joining a worker thread.
        await asyncio.to_thread(
            session_executor.shutdown,
            wait=True,
            cancel_futures=True,
        )
    _log.info("asr.stream.start", session_id=session_id)
    existing_record: dict[str, Any] = {}
    if asr_live_repo is not None:
        try:
            existing_record = asr_live_repo.get(session_id)
        except KeyError:
            existing_record = {}

    def _stream_normalized_text(
        text: str,
        existing_normalized_text: Any = None,
    ) -> str:
        if not l3_normalize_enabled:
            return text
        return str(existing_normalized_text or _normalize_text(text))

    def _commit_normalized_final(event: dict[str, Any]) -> None:
        if on_final_committed is None:
            return
        on_final_committed(dict(event))

    def _persisted_raw_transcript_events() -> list[dict[str, Any]]:
        restored: list[dict[str, Any]] = []
        for event in list(existing_record.get("events") or []):
            event_type = str(event.get("event_type") or "")
            if event_type not in {"transcript_partial", "transcript_final"}:
                continue
            payload = dict(event.get("payload") or {})
            raw_source_text = str(
                payload.get("source_snapshot_text")
                or payload.get("text")
                or payload.get("normalized_text")
                or ""
            ).strip()
            text = raw_source_text
            segment_id = str(payload.get("segment_id") or "").strip()
            if not text or not segment_id:
                continue
            normalized_text = (
                _normalize_text(raw_source_text)
                if l3_normalize_enabled
                else raw_source_text
            )
            restored.append({
                "event_type": "final" if event_type == "transcript_final" else "partial",
                "segment_id": segment_id,
                "text": text,
                "normalized_text": normalized_text,
                "start_ms": int(payload.get("start_ms") or 0),
                "end_ms": int(payload.get("end_ms") or 0),
                "received_at_ms": int(event.get("at_ms") or payload.get("end_ms") or 0),
                "confidence": payload.get("confidence"),
                **(
                    {"source_segment_id": str(payload["source_segment_id"])}
                    if payload.get("source_segment_id")
                    else {}
                ),
                **(
                    {"source_snapshot_text": raw_source_text}
                    if raw_source_text
                    else {}
                ),
                **({"projection_reconciled": True} if payload.get("projection_reconciled") else {}),
            })
        return restored

    restored_transcript_events = _persisted_raw_transcript_events()
    accumulated_finals: list[dict[str, Any]] = [
        event for event in restored_transcript_events if event["event_type"] == "final"
    ]
    latest_partials: dict[str, dict[str, Any]] = {
        str(event["segment_id"]): event
        for event in restored_transcript_events
        if event["event_type"] == "partial"
    }
    sent_partial_hint_keys: set[str] = set()
    sent_live_candidate_event_ids: set[str] = {
        str(event.get("id") or "")
        for event in list(existing_record.get("events") or [])
        if event.get("event_type") == "suggestion_candidate_event"
    }
    saw_empty_final = False
    chunk_ms = 300
    endpoint_silence_ms = 0
    audio_active_streak_ms = 0
    audio_active_reported = False

    def _observe_audio_activity(payload: bytes) -> dict[str, Any] | None:
        nonlocal audio_active_streak_ms, audio_active_reported
        if audio_active_reported:
            return None
        if _float32_pcm_rms(payload) > VAD_SILENCE_RMS_THRESHOLD:
            audio_active_streak_ms += chunk_ms
        else:
            audio_active_streak_ms = 0
        if audio_active_streak_ms < 300:
            return None
        audio_active_reported = True
        return {
            "session_id": session_id,
            "monotonic_ns": time.monotonic_ns(),
            "active_streak_ms": audio_active_streak_ms,
        }
    endpoint_final_count = max(
        len(accumulated_finals),
        int((existing_record.get("live_projection") or {}).get("total_final_count") or 0),
    )
    endpoint_candidate: dict[str, Any] = {}
    endpoint_committed_source_text = str(
        accumulated_finals[-1].get("source_snapshot_text")
        if accumulated_finals
        else ""
    )
    endpoint_committed_end_ms = max(
        [int(event.get("end_ms") or 0) for event in accumulated_finals],
        default=0,
    )
    audio_asset: dict[str, Any] | None = dict(existing_record.get("audio") or {}) or None
    audio_writer: RealtimeWavAssetWriter | None = None
    if audio_asset_data_dir is not None:
        source_type = audio_source or "live_asr_stream"
        if on_audio_recording_started is not None:
            on_audio_recording_started({
                "session_id": session_id,
                "source_type": source_type,
                "sample_rate_hz": 16_000,
            })
        audio_writer = RealtimeWavAssetWriter(
            data_dir=audio_asset_data_dir,
            session_id=session_id,
            source_type=source_type,
            on_chunk_committed=on_audio_chunk_committed,
            authorize_chunk_commit=authorize_audio_chunk_commit,
        )

    def _to_streaming_final(ev: dict[str, Any], idx: int) -> dict[str, Any]:
        text = str(ev.get("text") or "")
        return {
            "event_type": "final",
            "segment_id": ev.get("segment_id") or f"real_seg_{idx}",
            "text": text,
            "normalized_text": _stream_normalized_text(
                text,
                ev.get("normalized_text"),
            ),
            **(
                {"source_segment_id": str(ev["source_segment_id"])}
                if ev.get("source_segment_id")
                else {}
            ),
            **(
                {"source_snapshot_text": str(ev["source_snapshot_text"])}
                if ev.get("source_snapshot_text")
                else {}
            ),
            **({"projection_reconciled": True} if ev.get("projection_reconciled") else {}),
            "start_ms": int(ev.get("start_ms") if ev.get("start_ms") is not None else idx * chunk_ms),
            "end_ms": int(ev.get("end_ms") if ev.get("end_ms") is not None else (idx + 1) * chunk_ms),
            "received_at_ms": int(
                ev.get("received_at_ms")
                if ev.get("received_at_ms") is not None
                else ev.get("end_ms")
                if ev.get("end_ms") is not None
                else idx * chunk_ms + chunk_ms
            ),
            "confidence": ev.get("confidence", 0.85),
        }

    def _append_accumulated_final(ev: dict[str, Any], idx: int) -> bool:
        text = str(ev.get("text") or "").strip()
        if not text:
            return False
        if accumulated_finals:
            previous_text = str(accumulated_finals[-1].get("text") or "").strip()
            previous_segment_id = str(accumulated_finals[-1].get("segment_id") or "")
            segment_id = str(ev.get("segment_id") or "")
            if text == previous_text:
                return False
            if segment_id and segment_id == previous_segment_id:
                accumulated_finals[-1] = _to_streaming_final(ev, idx)
                return True
        accumulated_finals.append(_to_streaming_final(ev, idx))
        if len(accumulated_finals) > LIVE_PROJECTION_MAX_FINALS:
            del accumulated_finals[:-LIVE_PROJECTION_MAX_FINALS]
        return True

    def _next_endpoint_segment_id() -> str:
        return f"vad_endpoint_{endpoint_final_count + 1:03d}"

    def _incremental_endpoint_projection(source_text: str) -> tuple[str, bool]:
        source = str(source_text or "").strip()
        previous = str(endpoint_committed_source_text or "").strip()
        if not source:
            return "", False
        if not previous:
            return source, False
        if source == previous or previous.startswith(source):
            return "", False
        if source.startswith(previous):
            return source[len(previous):].strip(), False
        common_prefix = 0
        for previous_char, source_char in zip(previous, source):
            if previous_char != source_char:
                break
            common_prefix += 1
        bounded_threshold = max(2, min(len(previous), len(source)) // 2)
        if common_prefix >= bounded_threshold:
            return source[common_prefix:].strip(), True
        max_overlap = min(len(previous), len(source))
        for overlap in range(max_overlap, 0, -1):
            if previous[-overlap:] == source[:overlap]:
                return source[overlap:].strip(), True
        return source, True

    def _incremental_endpoint_text(source_text: str) -> str:
        return _incremental_endpoint_projection(source_text)[0]

    def _to_endpoint_partial(ev: dict[str, Any]) -> dict[str, Any]:
        source_text = str(ev.get("text") or "").strip()
        source_segment_id = _funasr_source_segment_id(session_id, ev)
        display_text, projection_reconciled = _incremental_endpoint_projection(source_text)
        return {
            **ev,
            "segment_id": _next_endpoint_segment_id(),
            **({"source_segment_id": source_segment_id} if source_segment_id else {}),
            "source_snapshot_text": source_text,
            "text": display_text,
            "normalized_text": _stream_normalized_text(display_text) if display_text else "",
            "projection_reconciled": projection_reconciled,
            "start_ms": endpoint_committed_end_ms,
        }

    def _remember_live_partial(ev: dict[str, Any]) -> bool:
        if ev.get("event_type") != "partial":
            return False
        text = str(ev.get("text") or "").strip()
        if not text:
            return False
        segment_id = str(ev.get("segment_id") or f"partial_{len(latest_partials) + 1:03d}")
        previous = latest_partials.get(segment_id)
        if previous and str(previous.get("text") or "") == text:
            return False
        candidate_eligible = _should_queue_stable_partial_candidate(text, ev)
        partial_record = {
            "event_type": "partial",
            "segment_id": segment_id,
            "text": text,
            **({"normalized_text": ev["normalized_text"]} if ev.get("normalized_text") else {}),
            **({"source_segment_id": ev["source_segment_id"]} if ev.get("source_segment_id") else {}),
            **({"source_snapshot_text": ev["source_snapshot_text"]} if ev.get("source_snapshot_text") else {}),
            **({"projection_reconciled": True} if ev.get("projection_reconciled") else {}),
            "start_ms": int(ev.get("start_ms") or 0),
            "end_ms": int(ev.get("end_ms") or getattr(recognizer, "_seq", 0) * chunk_ms),
            "received_at_ms": int(
                ev.get("received_at_ms")
                if ev.get("received_at_ms") is not None
                else ev.get("end_ms")
                if ev.get("end_ms") is not None
                else getattr(recognizer, "_seq", 0) * chunk_ms
            ),
            "confidence": ev.get("confidence", 0.8),
            **({"candidate_eligible": True, "candidate_source": "stable_partial"} if candidate_eligible else {}),
        }
        if (
            previous
            and previous.get("candidate_eligible")
            and not candidate_eligible
            and len(_compact_text(text)) < len(_compact_text(str(previous.get("text") or "")))
        ):
            tail_segment_id = f"{segment_id}_live_tail"
            latest_partials.pop(tail_segment_id, None)
            latest_partials[tail_segment_id] = {
                **partial_record,
                "segment_id": tail_segment_id,
            }
            while len(latest_partials) > LIVE_PROJECTION_MAX_PARTIALS:
                latest_partials.pop(next(iter(latest_partials)))
            return True
        latest_partials.pop(segment_id, None)
        latest_partials[segment_id] = partial_record
        while len(latest_partials) > LIVE_PROJECTION_MAX_PARTIALS:
            latest_partials.pop(next(iter(latest_partials)))
        return True

    def _current_session_streaming_events() -> list[dict[str, Any]]:
        events, _dropped = _bound_streaming_projection_events(
            [*latest_partials.values(), *accumulated_finals]
        )
        return events

    def _track_endpoint_candidate(source_ev: dict[str, Any], display_ev: dict[str, Any]) -> None:
        nonlocal endpoint_candidate
        if source_ev.get("event_type") != "partial":
            return
        text = str(display_ev.get("text") or "").strip()
        if len(text) < VAD_MIN_FINAL_TEXT_CHARS:
            return
        endpoint_candidate = {
            "text": text,
            "source_text": str(source_ev.get("text") or "").strip(),
            "segment_id": (
                str(display_ev.get("segment_id") or _next_endpoint_segment_id())
                if provider_metadata["provider"] == "funasr_realtime"
                else _next_endpoint_segment_id()
            ),
            "start_ms": int(display_ev.get("start_ms") or endpoint_committed_end_ms),
            "end_ms": int(display_ev.get("end_ms") or getattr(recognizer, "_seq", 0) * chunk_ms),
            "confidence": display_ev.get("confidence", 0.8),
            **(
                {"source_segment_id": display_ev["source_segment_id"]}
                if display_ev.get("source_segment_id")
                else {}
            ),
            "projection_reconciled": bool(display_ev.get("projection_reconciled")),
        }

    def _clear_endpoint_candidate() -> None:
        nonlocal endpoint_candidate, endpoint_silence_ms
        endpoint_candidate = {}
        endpoint_silence_ms = 0

    def _maybe_vad_endpoint_final() -> dict[str, Any] | None:
        nonlocal endpoint_final_count, endpoint_committed_source_text, endpoint_committed_end_ms
        text = str(endpoint_candidate.get("text") or "").strip()
        candidate_end_ms = int(
            endpoint_candidate.get("end_ms")
            or getattr(recognizer, "_seq", 0) * chunk_ms
        )
        candidate_duration_ms = max(0, candidate_end_ms - endpoint_committed_end_ms)
        reached_natural_endpoint = endpoint_silence_ms >= VAD_ENDPOINT_SILENCE_MS
        reached_bounded_endpoint = candidate_duration_ms >= VAD_MAX_SEGMENT_MS
        if (
            not (reached_natural_endpoint or reached_bounded_endpoint)
            or len(text) < VAD_MIN_FINAL_TEXT_CHARS
        ):
            return None
        endpoint_final_count += 1
        end_ms = candidate_end_ms
        segment_id = str(endpoint_candidate.get("segment_id") or f"vad_endpoint_{endpoint_final_count:03d}")
        endpoint_committed_source_text = str(endpoint_candidate.get("source_text") or text).strip()
        endpoint_committed_end_ms = end_ms
        latest_partials.pop(segment_id, None)
        return {
            "event_type": "final",
            "segment_id": segment_id,
            "text": text,
            "start_ms": int(endpoint_candidate.get("start_ms") or max(0, end_ms - VAD_ENDPOINT_SILENCE_MS)),
            "end_ms": end_ms,
            "received_at_ms": end_ms,
            "confidence": endpoint_candidate.get("confidence", 0.8),
            "endpoint_source": (
                "server_vad_stable_partial"
                if reached_natural_endpoint
                else "server_vad_max_segment"
            ),
            "source_snapshot_text": endpoint_committed_source_text,
            **(
                {"source_segment_id": endpoint_candidate["source_segment_id"]}
                if endpoint_candidate.get("source_segment_id")
                else {}
            ),
            "normalized_text": _stream_normalized_text(text),
            "projection_reconciled": bool(endpoint_candidate.get("projection_reconciled")),
        }

    def _upsert_live_session(streaming_events: list[dict[str, Any]], degradation_reasons: list[str]) -> list[dict[str, Any]]:
        if asr_live_repo is None:
            return []
        bounded_streaming_events, dropped_streaming_events = _bound_streaming_projection_events(
            streaming_events
        )
        semantic_quality = _semantic_quality_for_streaming_events(bounded_streaming_events)
        effective_degradation_reasons = list(degradation_reasons)
        if semantic_quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER:
            effective_degradation_reasons.append(ASR_SEMANTIC_QUALITY_BLOCKER)
        live_events = build_asr_live_events(
            session_id=session_id,
            provider=provider_metadata["provider"],
            streaming_events=bounded_streaming_events,
            is_mock=provider_metadata["is_mock"],
        )
        source_segment_ids = {
            str(event.get("segment_id") or ""): str(event["source_segment_id"])
            for event in bounded_streaming_events
            if event.get("segment_id") and event.get("source_segment_id")
        }
        for live_event in live_events:
            if live_event.get("event_type") not in {"transcript_partial", "transcript_final"}:
                continue
            payload = live_event.get("payload") or {}
            source_segment_id = source_segment_ids.get(str(payload.get("segment_id") or ""))
            if source_segment_id:
                payload["source_segment_id"] = source_segment_id
        live_events = _compact_cumulative_source_snapshots(live_events)
        base_record = {
            "session_id": session_id,
            "provider": provider_metadata["provider"],
            "provider_mode": provider_metadata["provider_mode"],
            "is_mock": provider_metadata["is_mock"],
            "asr_fallback_used": provider_metadata["fallback_used"],
            "degradation_reasons": _dedupe(effective_degradation_reasons),
            "asr_semantic_quality": semantic_quality,
            **({"asr_runtime_profile": dict(provider_metadata["asr_runtime_profile"])} if provider_metadata.get("asr_runtime_profile") else {}),
            "audio_source": audio_source,
            "input_source": audio_source,
            **({"audio": audio_asset} if audio_asset is not None else {}),
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "settings_snapshot": {
                "asr": {"l3_normalize_enabled": bool(l3_normalize_enabled)},
                "scope": "websocket_connection_start",
            },
            "live_projection": {
                "policy_version": "recent-canonical-window.v1",
                "complete_transcript_source": "v2_normalized_tables",
                "max_finals": LIVE_PROJECTION_MAX_FINALS,
                "max_partials": LIVE_PROJECTION_MAX_PARTIALS,
                "max_external_revisions": LIVE_PROJECTION_MAX_EXTERNAL_REVISIONS,
                "dropped_streaming_event_count": dropped_streaming_events,
                "dropped_external_revision_count": 0,
                "total_final_count": endpoint_final_count,
            },
            "events": live_events,
            "last_activity_at_epoch_ms": time.time_ns() // 1_000_000,
        }
        try:
            asr_live_repo.get(session_id)
        except KeyError:
            asr_live_repo.create(base_record)
        else:
            def merge_latest(existing: dict[str, Any]) -> dict[str, Any]:
                live_event_ids = {str(event.get("id") or "") for event in live_events}
                all_external_revisions = [
                    event
                    for event in list(existing.get("events") or [])
                    if event.get("event_type") == "transcript_revision"
                    and (event.get("payload") or {}).get("correction", {}).get("policy_version")
                    == REALTIME_CORRECTION_POLICY_VERSION
                    and str(event.get("id") or "") not in live_event_ids
                ]
                external_revisions = all_external_revisions[-LIVE_PROJECTION_MAX_EXTERNAL_REVISIONS:]
                merged_events = [*live_events, *external_revisions]
                for sequence, event in enumerate(merged_events, start=1):
                    event["sequence"] = sequence
                retained_degradation_reasons = [
                    reason
                    for reason in list(existing.get("degradation_reasons") or [])
                    if reason != ASR_SEMANTIC_QUALITY_BLOCKER
                ]
                return {
                    **existing,
                    **base_record,
                    "degradation_reasons": _dedupe([
                        *retained_degradation_reasons,
                        *list(base_record.get("degradation_reasons") or []),
                    ]),
                    "events": merged_events,
                    "live_projection": {
                        **dict(base_record["live_projection"]),
                        "dropped_external_revision_count": max(
                            0,
                            len(all_external_revisions) - len(external_revisions),
                        ),
                    },
                    "suggestion_cards": list(existing.get("suggestion_cards") or []),
                    "approach_cards": list(existing.get("approach_cards") or []),
                    "minutes": dict(existing.get("minutes") or {}),
                    "auto_suggestion": dict(existing.get("auto_suggestion") or {}),
                    "realtime_transcript_correction": dict(existing.get("realtime_transcript_correction") or {}),
                }

            asr_live_repo.update(session_id, merge_latest)
        _log.info(
            "asr.stream.persisted",
            session_id=session_id,
            finals=len(bounded_streaming_events),
            events=len(live_events),
            dropped_streaming_events=dropped_streaming_events,
        )
        return live_events

    def _unsent_realtime_candidate_events(live_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        outgoing: list[dict[str, Any]] = []
        for event in live_events:
            if event.get("event_type") != "suggestion_candidate_event":
                continue
            event_id = str(event.get("id") or "")
            if not event_id:
                continue
            if event_id in sent_live_candidate_event_ids:
                continue
            sent_live_candidate_event_ids.add(event_id)
            outgoing.append(event)
        return outgoing

    def _semantic_quality_for_streaming_events(streaming_events: list[dict[str, Any]]) -> dict[str, Any]:
        transcript = " ".join(
            str(event.get("text") or "").strip()
            for event in streaming_events
            if event.get("event_type") == "final" and str(event.get("text") or "").strip()
        ).strip()
        if not transcript:
            return {
                "schema_version": "asr_semantic_quality.v1",
                "policy_version": "general_chinese_technical_meeting.v3",
                "status": "not_evaluated",
                "blocker": None,
                "matched_entities": [],
                "matched_entity_groups": [],
                "missing_entity_groups": [],
                "technical_entity_hit_count": 0,
                "technical_group_hit_count": 0,
                "gibberish_score": 0.0,
                "latin_token_count": 0,
                "unknown_latin_token_count": 0,
                "unknown_latin_tokens": [],
                "mixed_language_fragmentation_score": 0.0,
                "quality_failure_reasons": [],
                "reason": "transcript_empty",
            }
        return evaluate_semantic_quality(transcript)

    def _dedupe(values: list[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            if value not in deduped:
                deduped.append(value)
        return deduped

    readiness_waiter = getattr(recognizer, "wait_ready", None)
    readiness_is_required = callable(readiness_waiter)
    readiness_event_required = readiness_is_required or provider_metadata["provider"] == "sherpa_onnx_realtime"
    pending_messages: deque[dict[str, Any]] = deque()
    pending_message_limit = ASR_READY_BUFFER_MAX_CHUNKS
    pending_asr_audio_dropped = False
    readiness_degradation_reasons: list[str] = []
    finalization_started = False

    def _current_degradation_reasons() -> list[str]:
        return _dedupe([
            *list(provider_metadata["degradation_reasons"]),
            *readiness_degradation_reasons,
        ])

    def _close_audio_writer(*, interrupted: bool = False) -> None:
        nonlocal audio_asset, audio_writer
        if audio_writer is None:
            return
        try:
            if on_audio_recording_sealed is None:
                audio_asset = audio_writer.close()
            else:
                audio_asset = audio_writer.seal()
                on_audio_recording_sealed({
                    **audio_asset,
                    "interrupted": interrupted,
                })
        except Exception:
            audio_writer.discard()
            raise
        finally:
            audio_writer = None

    def _record_audio_payload(payload: bytes) -> None:
        if audio_writer is not None:
            audio_writer.write_float32_pcm(payload)

    def _record_and_recognize_audio_payload(
        payload: bytes,
        *,
        audio_already_recorded: bool,
    ) -> list[dict[str, Any]]:
        if audio_writer is not None and not audio_already_recorded:
            audio_writer.write_float32_pcm(payload)
        return recognizer.recognize_chunk(payload)

    def _upsert_and_commit_final(
        streaming_events: list[dict[str, Any]],
        degradation_reasons: list[str],
        committed_final: dict[str, Any],
    ) -> list[dict[str, Any]]:
        live_events = _upsert_live_session(streaming_events, degradation_reasons)
        _commit_normalized_final(committed_final)
        return live_events

    def _upsert_and_commit_finals(
        streaming_events: list[dict[str, Any]],
        degradation_reasons: list[str],
        committed_finals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        live_events = _upsert_live_session(streaming_events, degradation_reasons)
        for committed_final in committed_finals:
            _commit_normalized_final(committed_final)
        return live_events

    async def _persist_terminal_readiness_stream(reasons: list[str]) -> None:
        await _run_blocking(_close_audio_writer)
        if asr_live_repo is None:
            return
        end_ms = int((audio_asset or {}).get("duration_ms") or 0)
        try:
            await _run_blocking(
                _upsert_live_session,
                [{
                    "event_type": "end_of_stream",
                    "end_ms": end_ms,
                    "received_at_ms": end_ms,
                }],
                reasons,
            )
        except Exception as exc:
            _log.warning(
                "asr.stream.readiness_terminal_persist_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def _cancel_readiness_task(readiness_task: asyncio.Task[Any] | None) -> None:
        if readiness_task is None or readiness_task.done():
            return
        readiness_task.cancel()
        try:
            await readiness_task
        except AsyncCancelledError:
            pass
        except Exception:
            pass

    async def _abort_before_readiness_terminal(readiness_task: asyncio.Task[Any] | None) -> None:
        abort = getattr(recognizer, "abort", None)
        if callable(abort):
            try:
                # Abort directly so a recognizer waiting in a worker thread can
                # release that thread before the readiness task is cancelled.
                abort()
            except Exception as exc:
                _log.warning(
                    "asr.stream.ready_abort_failed",
                    session_id=session_id,
                    error=str(exc),
                )
        await _cancel_readiness_task(readiness_task)

    def _mark_real_asr_ready_healthy() -> None:
        if provider_metadata["is_mock"] or provider_metadata["provider_mode"] != "real":
            return
        try:
            get_degradation_controller().recover("asr_ready")
        except Exception as exc:
            _log.warning(
                "asr.stream.ready_recovery_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def _finish_before_readiness(
        readiness_task: asyncio.Task[Any] | None,
        *,
        error_code: str,
        message: str,
        reason: str,
    ) -> bool:
        await _abort_before_readiness_terminal(readiness_task)
        reasons = _current_degradation_reasons()
        reasons.append(reason)
        if pending_asr_audio_dropped:
            reasons.append("asr_ready_buffer_overflow")
        try:
            await _persist_terminal_readiness_stream(_dedupe(reasons))
        except Exception as exc:
            _log.warning(
                "asr.stream.readiness_terminal_audio_persist_failed",
                session_id=session_id,
                error=str(exc),
            )
        await websocket.send_text(json.dumps({
            "event_type": "provider_error",
            "error_code": error_code,
            "message": message,
            "provider": provider_metadata["provider"],
            "provider_mode": provider_metadata["provider_mode"],
            "degradation_reasons": _dedupe(reasons),
            "recording_saved": bool(audio_asset and audio_asset.get("saved")),
        }, ensure_ascii=False))
        await websocket.close()
        return False

    async def _prepare_before_asr_ready() -> bool:
        nonlocal pending_asr_audio_dropped
        if not readiness_event_required:
            return True
        await websocket.send_text(json.dumps({
            "event_type": "asr_starting",
            "provider": provider_metadata["provider"],
            "ready": False,
            "message": "正在准备实时识别，请稍候。",
        }, ensure_ascii=False))
        if not readiness_is_required:
            await websocket.send_text(json.dumps({
                "event_type": "asr_ready",
                "provider": provider_metadata["provider"],
                "ready": True,
                "ready_latency_ms": 0.0,
                "message": "实时识别已就绪。",
            }, ensure_ascii=False))
            _mark_real_asr_ready_healthy()
            return True

        ready_started_at = time.monotonic()
        readiness_task = asyncio.create_task(
            asyncio.to_thread(readiness_waiter, ASR_READY_TIMEOUT_S)
        )
        while True:
            receive_task = asyncio.create_task(websocket.receive())
            done, _pending = await asyncio.wait(
                {readiness_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                try:
                    message = receive_task.result()
                except Exception as exc:
                    await _finish_before_readiness(
                        readiness_task,
                        error_code="stream_interrupted",
                        message="实时识别未就绪，会议录音已保留但本次转写被中断。",
                        reason="stream_interrupted",
                    )
                    _log.warning(
                        "asr.stream.ready_receive_failed",
                        session_id=session_id,
                        error=str(exc),
                    )
                    return False
                pcm_payload = message.get("bytes")
                if pcm_payload is not None:
                    audio_activity = _observe_audio_activity(pcm_payload)
                    if audio_activity is not None and on_audio_active is not None:
                        await _run_blocking(on_audio_active, audio_activity)
                    await _run_blocking(_record_audio_payload, pcm_payload)
                    if len(pending_messages) < pending_message_limit:
                        pending_messages.append({
                            "bytes": pcm_payload,
                            "_audio_recorded": True,
                        })
                    else:
                        pending_asr_audio_dropped = True
                    if not readiness_task.done():
                        continue
                if message.get("text") == "END":
                    if not readiness_task.done():
                        return await _finish_before_readiness(
                            readiness_task,
                            error_code="asr_not_ready_at_stop",
                            message="实时识别尚未就绪，本次会议录音已保存，可稍后重新转写。",
                            reason="asr_not_ready_at_stop",
                        )
                    try:
                        ready_when_end_received = bool(readiness_task.result())
                    except Exception:
                        ready_when_end_received = False
                    if not ready_when_end_received:
                        return await _finish_before_readiness(
                            readiness_task,
                            error_code="asr_ready_timeout",
                            message="本地实时识别模型未在限定时间内就绪，会议录音已保存，请稍后重新转写。",
                            reason="asr_ready_timeout",
                        )
                    pending_messages.append({"text": "END"})
                    # The readiness task and END completed together. Preserve
                    # message order and let the normal stream loop finalize.
                if not readiness_task.done():
                    continue

            if not receive_task.done():
                receive_task.cancel()
                await asyncio.gather(receive_task, return_exceptions=True)
            try:
                ready = bool(readiness_task.result())
            except Exception as exc:
                ready = False
                _log.warning(
                    "asr.stream.ready_check_failed",
                    session_id=session_id,
                    provider=provider_metadata["provider"],
                    error=str(exc),
                )
            if not ready:
                return await _finish_before_readiness(
                    readiness_task,
                    error_code="asr_ready_timeout",
                    message="本地实时识别模型未在限定时间内就绪，会议录音已保存，请稍后重新转写。",
                    reason="asr_ready_timeout",
                )
            readiness_latency_ms = round((time.monotonic() - ready_started_at) * 1000, 1)
            await websocket.send_text(json.dumps({
                "event_type": "asr_ready",
                "provider": provider_metadata["provider"],
                "ready": True,
                "ready_latency_ms": readiness_latency_ms,
                "message": "实时识别已就绪。",
            }, ensure_ascii=False))
            _mark_real_asr_ready_healthy()
            return True

    async def _next_stream_message() -> dict[str, Any]:
        if pending_messages:
            return pending_messages.popleft()
        return await websocket.receive()

    try:
        ready_for_stream = await _prepare_before_asr_ready()
    except BaseException:
        await _shutdown_session_executor()
        raise
    if not ready_for_stream:
        await _shutdown_session_executor()
        return

    try:
        while True:
            msg = await _next_stream_message()
            if msg.get("bytes") is not None:
                pcm_payload = msg["bytes"]
                audio_activity = _observe_audio_activity(pcm_payload)
                if audio_activity is not None and on_audio_active is not None:
                    await _run_blocking(on_audio_active, audio_activity)
                events = await _run_blocking(
                    _record_and_recognize_audio_payload,
                    pcm_payload,
                    audio_already_recorded=bool(msg.get("_audio_recorded")),
                )
                for ev in events:
                    source_ev = _normalize_client_stream_event(
                        ev,
                        l3_normalize_enabled=l3_normalize_enabled,
                    )
                    ev = (
                        _to_endpoint_partial(source_ev)
                        if source_ev.get("event_type") == "partial" and provider_metadata["provider"] == "funasr_realtime"
                        else source_ev
                    )
                    partial_hint = build_partial_hint_event(ev)
                    if partial_hint:
                        partial_hint_key = str(
                            partial_hint.get("payload", {}).get("dedupe_key")
                            or partial_hint["id"]
                        )
                    outgoing_events = [ev]
                    if partial_hint and partial_hint_key not in sent_partial_hint_keys:
                        sent_partial_hint_keys.add(partial_hint_key)
                        outgoing_events.append(partial_hint)
                    if ev.get("event_type") == "final" and ev.get("text"):
                        if _append_accumulated_final(ev, getattr(recognizer, "_seq", len(accumulated_finals) + 1)):
                            live_events = await _run_blocking(
                                _upsert_and_commit_final,
                                _current_session_streaming_events(),
                                _current_degradation_reasons(),
                                ev,
                            )
                            outgoing_events.extend(_unsent_realtime_candidate_events(live_events))
                        _clear_endpoint_candidate()
                    else:
                        if _remember_live_partial(ev):
                            live_events = await _run_blocking(
                                _upsert_live_session,
                                _current_session_streaming_events(),
                                _current_degradation_reasons(),
                            )
                            outgoing_events.extend(_unsent_realtime_candidate_events(live_events))
                        _track_endpoint_candidate(source_ev, ev)
                    for outgoing_event in outgoing_events:
                        await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
                if _float32_pcm_rms(pcm_payload) <= VAD_SILENCE_RMS_THRESHOLD:
                    endpoint_silence_ms += chunk_ms
                else:
                    endpoint_silence_ms = 0
                endpoint_final = _maybe_vad_endpoint_final()
                if endpoint_final is not None:
                    endpoint_final = _normalize_client_stream_event(
                        endpoint_final,
                        l3_normalize_enabled=l3_normalize_enabled,
                    )
                    endpoint_outgoing_events = [endpoint_final]
                    if _append_accumulated_final(endpoint_final, getattr(recognizer, "_seq", len(accumulated_finals) + 1)):
                        live_events = await _run_blocking(
                            _upsert_and_commit_final,
                            _current_session_streaming_events(),
                            _current_degradation_reasons(),
                            endpoint_final,
                        )
                        endpoint_outgoing_events.extend(_unsent_realtime_candidate_events(live_events))
                    for outgoing_event in endpoint_outgoing_events:
                        await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
                    _clear_endpoint_candidate()
            elif msg.get("text") == "END":
                await _run_blocking(_close_audio_writer)
                final_events = await _run_blocking(recognizer.finalize)
                finalization_started = True
                normalized_final_events: list[dict[str, Any]] = []
                newly_appended_finals: list[dict[str, Any]] = []
                for ev in final_events:
                    ev = _normalize_client_stream_event(
                        ev,
                        l3_normalize_enabled=l3_normalize_enabled,
                    )
                    if ev.get("event_type") == "final" and provider_metadata["provider"] == "funasr_realtime":
                        source_text = str(ev.get("text") or "").strip()
                        source_segment_id = _funasr_source_segment_id(session_id, ev)
                        incremental_text, projection_reconciled = _incremental_endpoint_projection(source_text)
                        if not incremental_text:
                            continue
                        endpoint_final_count += 1
                        end_ms = int(ev.get("end_ms") or getattr(recognizer, "_seq", 0) * chunk_ms)
                        ev = {
                            **ev,
                            "segment_id": f"vad_endpoint_{endpoint_final_count:03d}",
                            **({"source_segment_id": source_segment_id} if source_segment_id else {}),
                            "source_snapshot_text": source_text,
                            "text": incremental_text,
                            "normalized_text": _stream_normalized_text(incremental_text),
                            "projection_reconciled": projection_reconciled,
                            "start_ms": endpoint_committed_end_ms,
                            "end_ms": end_ms,
                        }
                        endpoint_committed_source_text = source_text
                        endpoint_committed_end_ms = end_ms
                    normalized_final_events.append(ev)
                    if ev.get("event_type") == "final":
                        if ev.get("text"):
                            if _append_accumulated_final(ev, getattr(recognizer, "_seq", 0) + 1):
                                newly_appended_finals.append(ev)
                        else:
                            saw_empty_final = True
                    elif ev.get("event_type") == "partial":
                        _remember_live_partial(ev)
                finalize_candidate_events: list[dict[str, Any]] = []
                if asr_live_repo is not None and accumulated_finals:
                    try:
                        live_events = await _run_blocking(
                            _upsert_and_commit_finals,
                            _current_session_streaming_events(),
                            list(provider_metadata["degradation_reasons"]),
                            newly_appended_finals,
                        )
                        finalize_candidate_events = _unsent_realtime_candidate_events(live_events)
                    except Exception as exc:
                        _log.warning("asr.stream.persist_before_finalize_send_failed", session_id=session_id, error=str(exc))
                for outgoing_event in [*normalized_final_events, *finalize_candidate_events]:
                    await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
                if asr_live_repo is not None:
                    degradation_reasons = _current_degradation_reasons()
                    if not accumulated_finals:
                        if saw_empty_final:
                            degradation_reasons.append("asr_final_empty")
                        else:
                            degradation_reasons.append("asr_no_final")
                    end_of_stream = {
                        "event_type": "end_of_stream",
                        "end_ms": getattr(recognizer, "_seq", 0) * chunk_ms,
                        "received_at_ms": getattr(recognizer, "_seq", 0) * chunk_ms,
                    }
                    streaming_events = [*_current_session_streaming_events(), end_of_stream]
                    try:
                        live_events = await _run_blocking(
                            _upsert_live_session,
                            streaming_events,
                            degradation_reasons,
                        )
                        for outgoing_event in _unsent_realtime_candidate_events(live_events):
                            await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
                    except Exception as exc:
                        _log.warning("asr.stream.persist_failed", session_id=session_id, error=str(exc))
                await websocket.close()
                _log.info("asr.stream.end", session_id=session_id, chunks=recognizer._seq, finals=len(accumulated_finals))
                return
    except (Exception, AsyncCancelledError) as exc:
        abort = getattr(recognizer, "abort", None)
        if callable(abort):
            try:
                await _run_blocking(abort)
            except Exception as abort_exc:
                _log.warning("asr.stream.abort_failed", session_id=session_id, error=str(abort_exc))
        if audio_writer is not None:
            try:
                await _run_blocking(_close_audio_writer, interrupted=True)
            except Exception as audio_exc:
                _log.warning(
                    "asr.stream.audio_checkpoint_failed",
                    session_id=session_id,
                    error=str(audio_exc),
                )
        if asr_live_repo is not None:
            try:
                if finalization_started:
                    end_of_stream = {
                        "event_type": "end_of_stream",
                        "end_ms": getattr(recognizer, "_seq", 0) * chunk_ms,
                        "received_at_ms": getattr(recognizer, "_seq", 0) * chunk_ms,
                    }
                    await _run_blocking(
                        _upsert_live_session,
                        [*_current_session_streaming_events(), end_of_stream],
                        _current_degradation_reasons(),
                    )
                else:
                    await _run_blocking(
                        _upsert_live_session,
                        _current_session_streaming_events(),
                        [*_current_degradation_reasons(), "stream_interrupted"],
                    )
            except Exception as persist_exc:
                _log.warning(
                    "asr.stream.interrupted_persist_failed",
                    session_id=session_id,
                    error=str(persist_exc),
                )
        _log.warning("asr.stream.aborted", session_id=session_id, error=str(exc))
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        await _shutdown_session_executor()


def _normalize_client_stream_event(
    ev: dict[str, Any],
    *,
    l3_normalize_enabled: bool = True,
) -> dict[str, Any]:
    event = dict(ev)
    if event.get("event_type") in {"partial", "final", "revision"}:
        text = str(event.get("text") or "")
        if text:
            event["normalized_text"] = (
                str(event.get("normalized_text") or _normalize_text(text))
                if l3_normalize_enabled
                else text
            )
    return event


def _recognizer_provider_metadata(recognizer: StreamRecognizer, *, configured_provider: str) -> dict[str, Any]:
    """Describe the recognizer that actually handled the stream.

    The websocket endpoint is a real product entry point, so a missing local
    ASR sidecar must not silently look like a real ASR session when it falls
    back to the deterministic test recognizer.
    """
    required_metadata = ("provider", "provider_mode", "is_mock", "fallback_used")
    missing_metadata = [name for name in required_metadata if not hasattr(recognizer, name)]
    provider = str(getattr(recognizer, "provider", configured_provider) or configured_provider)
    is_mock = bool(getattr(recognizer, "is_mock", provider in {"fake", "local_mock_asr"}))
    fallback_used = bool(getattr(recognizer, "fallback_used", is_mock))
    reasons = list(getattr(recognizer, "degradation_reasons", []) or [])
    if missing_metadata:
        is_mock = True
        fallback_used = True
        if "recognizer_metadata_missing" not in reasons:
            reasons.append("recognizer_metadata_missing")
    if fallback_used and not reasons:
        reasons.append("real_asr_sidecar_unavailable")
    provider_mode = str(getattr(recognizer, "provider_mode", "mock" if is_mock else "real"))
    if missing_metadata:
        provider_mode = "unknown"
    runtime_profile = {
        "profile": str(getattr(recognizer, "asr_profile", "") or ""),
        "chunk_size": list(getattr(recognizer, "chunk_size", []) or []),
    }
    runtime_profile = {key: value for key, value in runtime_profile.items() if value}
    return {
        "provider": provider,
        "provider_mode": provider_mode,
        "is_mock": is_mock,
        "fallback_used": fallback_used,
        "degradation_reasons": reasons,
        **({"asr_runtime_profile": runtime_profile} if runtime_profile else {}),
    }


def _float32_pcm_rms(payload: bytes) -> float:
    usable = payload[: len(payload) - (len(payload) % 4)]
    if not usable:
        return 0.0
    total = 0.0
    count = 0
    for (sample,) in struct.iter_unpack("<f", usable):
        total += float(sample) * float(sample)
        count += 1
    if count <= 0:
        return 0.0
    return (total / count) ** 0.5


def _should_queue_stable_partial_candidate(text: str, ev: dict[str, Any]) -> bool:
    compact_text = _compact_text(text)
    if len(compact_text) < STABLE_PARTIAL_CANDIDATE_MIN_CHARS:
        return False
    try:
        confidence = float(ev.get("confidence", 0.0))
    except (TypeError, ValueError):
        return False
    if confidence < STABLE_PARTIAL_CANDIDATE_MIN_CONFIDENCE:
        return False
    lower = str(text or "").lower()
    return any(marker.lower() in lower for marker in STABLE_PARTIAL_CANDIDATE_MARKERS)


def _compact_text(text: str) -> str:
    return "".join(str(text or "").split())


def _should_block_recognizer(provider_metadata: dict[str, Any], *, allow_fake_fallback: bool) -> bool:
    if allow_fake_fallback:
        return False
    return (
        bool(provider_metadata.get("is_mock"))
        or bool(provider_metadata.get("fallback_used"))
        or str(provider_metadata.get("provider_mode") or "") != "real"
    )


def _blocked_recognizer_event(provider_metadata: dict[str, Any]) -> dict[str, Any]:
    reasons = list(provider_metadata.get("degradation_reasons") or [])
    if not reasons:
        reasons.append("real_asr_sidecar_unavailable")
    return {
        "event_type": "provider_error",
        "error_code": "real_asr_sidecar_unavailable",
        "message": "真实会议模式需要可用的本地实时 ASR；当前不会使用 fake fallback 生成会议文字。",
        "provider": provider_metadata.get("provider"),
        "provider_mode": provider_metadata.get("provider_mode"),
        "is_mock": bool(provider_metadata.get("is_mock")),
        "asr_fallback_used": bool(provider_metadata.get("fallback_used")),
        "degradation_reasons": reasons,
    }

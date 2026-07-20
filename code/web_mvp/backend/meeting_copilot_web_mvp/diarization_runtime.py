"""Bounded, fail-open runtime bridge for local speaker diarization.

The realtime ASR path owns audio latency.  This module is deliberately a
旁路: PCM submission is non-blocking, sidecar failures become diagnostics, and
speaker projections are written by a separate bounded worker.  The sidecar
protocol is JSONL and is implemented by the offline-only FunASR diarization
worker; tests may inject an in-process client with the same small interface.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Protocol

from meeting_copilot_web_mvp.audio_assets import validate_float32_pcm_payload
from meeting_copilot_web_mvp.diarization import SpeakerTurn, attribute_segment
from meeting_copilot_web_mvp.logging_config import get_logger


SAMPLE_RATE = 16_000
AUDIO_ENCODING = "pcm_f32le"
# The byte budget is authoritative. The count cap is only a defense against
# pathological tiny frames and is deliberately above a normal 200 ms startup
# window covered by the 16 MiB raw-PCM budget.
DEFAULT_QUEUE_MAX_CHUNKS = 2048
DEFAULT_AUDIO_QUEUE_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_PROJECTION_QUEUE_MAX_ITEMS = 256
DEFAULT_FINISH_TIMEOUT_S = 8.0
DEFAULT_STARTUP_DEADLINE_S = 20.0
DEFAULT_ABORT_TIMEOUT_S = 3.0
DEFAULT_MODEL = "cam++-zh-cn-local"
DIARIZATION_RUN_SOURCE = "local_realtime_diarization"
DIARIZATION_WORKER_ENV = "MEETING_COPILOT_DIARIZATION_WORKER"
DIARIZATION_FAILURE_REASON = "diarization_unavailable"
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")

_log = get_logger("meeting_copilot_web_mvp.diarization_runtime")
_REPO_ROOT = Path(__file__).resolve().parents[4]


class DiarizationSidecar(Protocol):
    def start(self, session_id: str) -> None: ...

    def submit(self, pcm: bytes, *, sample_start: int) -> bool: ...

    def finish(self, *, timeout_s: float) -> None: ...

    def abort(self) -> None: ...


def default_worker_command() -> list[str] | None:
    """Resolve the bundled worker without ever downloading a model."""

    configured = str(os.environ.get(DIARIZATION_WORKER_ENV) or "").strip()
    worker = Path(configured) if configured else _REPO_ROOT / "code/asr_runtime/scripts/funasr_diarization_worker.py"
    if not worker.is_file() or not worker.is_absolute():
        return None
    configured_python = str(os.environ.get("MEETING_COPILOT_FUNASR_PYTHON") or "").strip()
    funasr_python = (
        Path(configured_python) if configured_python else _REPO_ROOT / "code/asr_runtime/.venv-funasr/bin/python"
    )
    interpreter = str(funasr_python) if funasr_python.is_file() else sys.executable
    command = [interpreter, str(worker)]
    vad_dir = str(os.environ.get("MEETING_COPILOT_DIARIZATION_VAD_DIR") or "").strip()
    camplus_dir = str(os.environ.get("MEETING_COPILOT_DIARIZATION_CAMPLUS_DIR") or "").strip()
    if not vad_dir:
        default_vad_dir = Path.home() / ".cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
        if default_vad_dir.is_dir():
            vad_dir = str(default_vad_dir)
    if not camplus_dir:
        default_camplus_dir = _REPO_ROOT / "artifacts/tmp/diarization-camplus-zh-cn-v2.0.2"
        if default_camplus_dir.is_dir():
            camplus_dir = str(default_camplus_dir)
    if vad_dir:
        command.extend(["--vad-dir", vad_dir])
    if camplus_dir:
        command.extend(["--camplus-dir", camplus_dir])
    return command


class SubprocessDiarizationSidecar:
    """JSONL subprocess client with bounded audio input and deterministic cleanup."""

    def __init__(
        self,
        command: list[str],
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_diagnostic: Callable[[str], None],
        queue_max_chunks: int = DEFAULT_QUEUE_MAX_CHUNKS,
        max_audio_queue_bytes: int = DEFAULT_AUDIO_QUEUE_MAX_BYTES,
        startup_deadline_s: float = DEFAULT_STARTUP_DEADLINE_S,
        abort_timeout_s: float = DEFAULT_ABORT_TIMEOUT_S,
        process_factory: Callable[..., Any] = subprocess.Popen,
    ) -> None:
        self.command = list(command)
        self._on_event = on_event
        self._on_diagnostic = on_diagnostic
        self._process_factory = process_factory
        self._write_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max(1, int(queue_max_chunks)) + 2)
        self._max_audio_queue_bytes = max(1, int(max_audio_queue_bytes))
        self._startup_deadline_s = max(0.1, float(startup_deadline_s))
        self._abort_timeout_s = max(0.1, float(abort_timeout_s))
        self._queued_audio_bytes = 0
        self.max_queued_audio_bytes = 0
        self.dropped_audio_bytes = 0
        self.dropped_audio_chunks = 0
        self.stderr_bytes_drained = 0
        self._process: Any | None = None
        self._writer: threading.Thread | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._startup_watchdog: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._startup_outcome_event = threading.Event()
        self._session_id: str | None = None
        self._startup_deadline_at: float | None = None
        self.last_submit_reason: str | None = None
        self._started = False
        self._closing = False
        self._closed = False
        self._lock = threading.Lock()

    def start(self, session_id: str) -> None:
        with self._lock:
            if self._started:
                return
            normalized_session_id = str(session_id)
            if SESSION_ID_PATTERN.fullmatch(normalized_session_id) is None:
                raise ValueError("diarization session_id is invalid")
            self._session_id = normalized_session_id
            self._startup_deadline_at = time.monotonic() + self._startup_deadline_s
            environment = os.environ.copy()
            environment.update(
                {
                    "MODELSCOPE_OFFLINE": "1",
                    "HF_HUB_OFFLINE": "1",
                    "TRANSFORMERS_OFFLINE": "1",
                }
            )
            try:
                self._process = self._process_factory(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=environment,
                    bufsize=0,
                )
            except Exception as exc:
                self._on_diagnostic(f"sidecar_spawn_failed:{type(exc).__name__}")
                self._closed = True
                return
            self._started = True
            self._writer = threading.Thread(target=self._write_loop, name="diarization-sidecar-writer", daemon=True)
            self._reader = threading.Thread(target=self._read_loop, name="diarization-sidecar-reader", daemon=True)
            self._stderr_reader = threading.Thread(
                target=self._stderr_loop,
                name="diarization-sidecar-stderr-reader",
                daemon=True,
            )
            self._startup_watchdog = threading.Thread(
                target=self._startup_watchdog_loop,
                name="diarization-sidecar-startup-watchdog",
                daemon=True,
            )
            self._writer.start()
            self._reader.start()
            self._stderr_reader.start()
            self._startup_watchdog.start()
            try:
                self._write_queue.put_nowait(
                    {
                        "type": "session",
                        "session_id": self._session_id,
                        "sample_rate": SAMPLE_RATE,
                        "channels": 1,
                        "audio_encoding": AUDIO_ENCODING,
                    }
                )
            except queue.Full:
                self._on_diagnostic("sidecar_control_queue_full")

    def submit(self, pcm: bytes, *, sample_start: int) -> bool:
        with self._lock:
            self.last_submit_reason = None
            if not self._started or self._closing or self._closed or self._process is None:
                self.last_submit_reason = "sidecar_not_running"
                return False
            if self._process.poll() is not None:
                self._on_diagnostic("sidecar_exited")
                self._closed = True
                self.last_submit_reason = "sidecar_exited"
                return False
            audio_bytes = len(pcm)
            if audio_bytes > self._max_audio_queue_bytes:
                self.dropped_audio_bytes += audio_bytes
                self.dropped_audio_chunks += 1
                self.last_submit_reason = "audio_byte_budget_exhausted"
                self._on_diagnostic("sidecar_audio_byte_budget_exhausted")
                return False
            if self._queued_audio_bytes + audio_bytes > self._max_audio_queue_bytes:
                self.dropped_audio_bytes += audio_bytes
                self.dropped_audio_chunks += 1
                self.last_submit_reason = "audio_byte_budget_exhausted"
                self._on_diagnostic("sidecar_audio_byte_budget_exhausted")
                return False
            payload = {
                "type": "audio",
                "session_id": self._session_id,
                "sample_start": int(sample_start),
                "sample_count": audio_bytes // 4,
                "sample_rate": SAMPLE_RATE,
                "channels": 1,
                "audio_encoding": AUDIO_ENCODING,
                "pcm_base64": base64.b64encode(pcm).decode("ascii"),
                "_runtime_audio_bytes": audio_bytes,
            }
            try:
                self._write_queue.put_nowait(payload)
            except queue.Full:
                self.dropped_audio_bytes += audio_bytes
                self.dropped_audio_chunks += 1
                self.last_submit_reason = "audio_queue_full"
                self._on_diagnostic("sidecar_audio_queue_full")
                return False
            self._queued_audio_bytes += audio_bytes
            self.max_queued_audio_bytes = max(self.max_queued_audio_bytes, self._queued_audio_bytes)
            return True

    def finish(self, *, timeout_s: float) -> None:
        deadline = time.monotonic() + max(0.1, timeout_s)
        with self._lock:
            if not self._started or self._closing or self._closed:
                return
            self._closing = True

        startup_resolved = self._startup_outcome_event.wait(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            process_running = self._process is not None and self._process.poll() is None
            send_end = self._ready_event.is_set() and process_running
            if not startup_resolved and process_running:
                self._on_diagnostic("sidecar_not_ready_at_finish")
                self._terminate_locked()
        control = {"type": "end", "session_id": self._session_id} if send_end else {"_runtime_stop": True}
        try:
            self._write_queue.put(
                control,
                timeout=max(0.0, deadline - time.monotonic()),
            )
        except queue.Full:
            self._on_diagnostic("sidecar_finish_queue_full")
            with self._lock:
                self._terminate_locked()
        if self._writer is not None:
            self._writer.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            if self._writer is not None and self._writer.is_alive():
                self._on_diagnostic("sidecar_writer_shutdown_timeout")
                self._terminate_locked()
        self._wait_process(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._closed = True
            self._stop_event.set()
        self._join_threads_until(deadline)
        with self._lock:
            self._close_pipes_locked()

    def abort(self) -> None:
        with self._lock:
            if not self._started or self._closing or self._closed:
                return
            self._closing = True
            self._drop_queued_audio_locked()
            try:
                self._write_queue.put_nowait(
                    {
                        "type": "abort",
                        "session_id": self._session_id,
                        "reason": "abort",
                    }
                )
            except queue.Full:
                self._on_diagnostic("sidecar_abort_queue_full")
        deadline = time.monotonic() + self._abort_timeout_s
        if self._writer is not None:
            self._writer.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            if self._writer is not None and self._writer.is_alive():
                self._terminate_locked()
        self._wait_process(max(0.0, deadline - time.monotonic()))
        with self._lock:
            self._closed = True
            self._stop_event.set()
        self._join_threads_until(deadline)
        with self._lock:
            self._close_pipes_locked()

    def _write_loop(self) -> None:
        process = self._process
        if process is None or process.stdin is None:
            return
        try:
            while True:
                payload = self._write_queue.get()
                if payload.get("_runtime_stop"):
                    self._write_queue.task_done()
                    return
                try:
                    json_payload = {key: value for key, value in payload.items() if not key.startswith("_runtime_")}
                    process.stdin.write(
                        (json.dumps(json_payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
                    )
                    process.stdin.flush()
                except Exception as exc:
                    self._on_diagnostic(f"sidecar_write_failed:{type(exc).__name__}")
                    try:
                        process.stdin.close()
                    except Exception:
                        pass
                    return
                finally:
                    audio_bytes = int(payload.get("_runtime_audio_bytes") or 0)
                    if audio_bytes:
                        with self._lock:
                            self._queued_audio_bytes = max(0, self._queued_audio_bytes - audio_bytes)
                    self._write_queue.task_done()
                if payload.get("type") in {"end", "abort"}:
                    try:
                        process.stdin.close()
                    except Exception:
                        pass
                    return
        except Exception as exc:
            self._on_diagnostic(f"sidecar_writer_failed:{type(exc).__name__}")

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw_line in process.stdout:
                line = (
                    raw_line.decode("utf-8", errors="replace").strip()
                    if isinstance(raw_line, bytes)
                    else str(raw_line).strip()
                )
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    self._on_diagnostic("sidecar_invalid_json")
                    continue
                if isinstance(event, dict):
                    if event.get("event_type") == "ready":
                        self._ready_event.set()
                    self._on_event(event)
                    if event.get("event_type") in {"ready", "diarization_unavailable"}:
                        self._startup_outcome_event.set()
                else:
                    self._on_diagnostic("sidecar_invalid_event")
        except Exception as exc:
            self._on_diagnostic(f"sidecar_reader_failed:{type(exc).__name__}")
        return_code = process.poll()
        self._startup_outcome_event.set()
        if return_code not in (None, 0):
            self._on_diagnostic(f"sidecar_exit_code:{return_code}")

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            while True:
                chunk = process.stderr.read(8192)
                if not chunk:
                    return
                with self._lock:
                    self.stderr_bytes_drained += len(chunk)
        except Exception as exc:
            self._on_diagnostic(f"sidecar_stderr_drain_failed:{type(exc).__name__}")

    def _startup_watchdog_loop(self) -> None:
        deadline = self._startup_deadline_at
        if deadline is None:
            return
        remaining = max(0.0, deadline - time.monotonic())
        if self._stop_event.wait(remaining):
            return
        if not self._ready_event.is_set() and not self._closed:
            self._on_diagnostic("model_loading_timeout")

    def _drop_queued_audio_locked(self) -> None:
        controls: list[dict[str, Any]] = []
        while True:
            try:
                payload = self._write_queue.get_nowait()
            except queue.Empty:
                break
            audio_bytes = int(payload.get("_runtime_audio_bytes") or 0)
            if audio_bytes:
                self._queued_audio_bytes = max(0, self._queued_audio_bytes - audio_bytes)
                self.dropped_audio_bytes += audio_bytes
                self.dropped_audio_chunks += 1
            else:
                controls.append(payload)
            self._write_queue.task_done()
        for payload in controls:
            try:
                self._write_queue.put_nowait(payload)
            except queue.Full:
                self._on_diagnostic("sidecar_control_queue_full")
                break

    def _join_threads_until(self, deadline: float) -> None:
        for thread in (self._writer, self._reader, self._stderr_reader, self._startup_watchdog):
            if thread is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _close_pipes_locked(self) -> None:
        process = self._process
        for pipe_name in ("stdin", "stdout", "stderr"):
            pipe = getattr(process, pipe_name, None) if process is not None else None
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass

    def _wait_process(self, timeout_s: float) -> None:
        """Wait without the state lock so stdout/stderr drainers can progress."""

        process = self._process
        if process is None:
            return
        try:
            process.wait(timeout=max(0.0, timeout_s))
        except subprocess.TimeoutExpired:
            self._on_diagnostic("sidecar_process_shutdown_timeout")
            self._terminate_locked()
            try:
                process.wait(timeout=1.0)
            except Exception:
                self._on_diagnostic("sidecar_process_reap_failed")
        except Exception:
            self._on_diagnostic("sidecar_process_wait_failed")
            self._terminate_locked()

    def _terminate_locked(self) -> None:
        process = self._process
        if process is None:
            return
        try:
            process.kill()
        except Exception:
            pass


class DiarizationRuntime:
    """Session-scoped diarization fan-out and speaker projection coordinator."""

    def __init__(
        self,
        meeting_id: str,
        *,
        persistence: Any | None = None,
        source: str = DIARIZATION_RUN_SOURCE,
        model: str = DEFAULT_MODEL,
        sidecar_factory: Callable[[Callable[[dict[str, Any]], None], Callable[[str], None]], DiarizationSidecar]
        | None = None,
        worker_command: list[str] | None = None,
        audio_queue_max_chunks: int = DEFAULT_QUEUE_MAX_CHUNKS,
        audio_queue_max_bytes: int = DEFAULT_AUDIO_QUEUE_MAX_BYTES,
        startup_deadline_s: float = DEFAULT_STARTUP_DEADLINE_S,
        projection_queue_max_items: int = DEFAULT_PROJECTION_QUEUE_MAX_ITEMS,
        finish_timeout_s: float = DEFAULT_FINISH_TIMEOUT_S,
    ) -> None:
        self.meeting_id = str(meeting_id)
        self.persistence = persistence
        self.source = str(source)
        self.model = str(model)
        self.run_id = f"diarization:{self.meeting_id}"
        self._sidecar_factory = sidecar_factory
        self._worker_command = list(worker_command) if worker_command is not None else default_worker_command()
        self._finish_timeout_s = max(0.1, float(finish_timeout_s))
        self._projection_queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue(
            maxsize=max(1, int(projection_queue_max_items))
        )
        self._audio_queue_max_chunks = max(1, int(audio_queue_max_chunks))
        self._audio_queue_max_bytes = max(1, int(audio_queue_max_bytes))
        self._startup_deadline_s = max(0.1, float(startup_deadline_s))
        self._sidecar: DiarizationSidecar | None = None
        self._projection_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._started = False
        self._closed = False
        self._audio_disabled = False
        self._next_sample_start = 0
        self._ready = False
        self._fatal = False
        self._turns: list[SpeakerTurn] = []
        self._turn_ids: set[str] = set()
        self._finals: dict[str, dict[str, Any]] = {}
        self._attribution_revisions: dict[str, int] = {}
        self._applied_attributions: dict[str, tuple[str | None, float | None, str]] = {}
        self._diagnostic_reasons: list[str] = []
        self._speaker_turn_count = 0
        self._run_started = False

    @property
    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            sidecar = self._sidecar
            startup_state = (
                "ready" if self._ready else "unavailable" if self._fatal or self._closed else "model_loading"
            )
            return {
                "enabled": True,
                "run_id": self.run_id,
                "source": self.source,
                "model": self.model,
                "ready": self._ready,
                "startup_state": startup_state,
                "startup_deadline_ms": round(self._startup_deadline_s * 1_000),
                "worker_lifecycle": "per_session",
                "resident_worker": False,
                "closed": self._closed,
                "audio_disabled": self._audio_disabled,
                "speaker_turn_count": self._speaker_turn_count,
                "queue_depth": self._projection_queue.qsize(),
                "audio_queue_byte_budget": self._audio_queue_max_bytes,
                "audio_queue_max_bytes_observed": int(getattr(sidecar, "max_queued_audio_bytes", 0) or 0),
                "audio_dropped_bytes": int(getattr(sidecar, "dropped_audio_bytes", 0) or 0),
                "audio_dropped_chunks": int(getattr(sidecar, "dropped_audio_chunks", 0) or 0),
                "stderr_bytes_drained": int(getattr(sidecar, "stderr_bytes_drained", 0) or 0),
                "degradation_reasons": list(dict.fromkeys(self._diagnostic_reasons)),
                "model_download_status": "not_performed",
                "remote_asr_used": False,
            }

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._projection_thread = threading.Thread(
                target=self._projection_loop,
                name=f"diarization-projection-{self.meeting_id[:24]}",
                daemon=True,
            )
            self._projection_thread.start()
            if self.persistence is not None:
                try:
                    self.persistence.create_or_update_speaker_run(
                        meeting_id=self.meeting_id,
                        run_id=self.run_id,
                        source=self.source,
                        model=self.model,
                        status="running",
                        metadata=self._run_metadata(),
                        now_ms=_now_ms(),
                    )
                    self._run_started = True
                except Exception as exc:
                    self._record_diagnostic(f"persistence_run_start_failed:{type(exc).__name__}")
            try:
                if self._sidecar_factory is not None:
                    self._sidecar = self._sidecar_factory(self._on_sidecar_event, self._record_diagnostic)
                elif self._worker_command:
                    self._sidecar = SubprocessDiarizationSidecar(
                        self._worker_command,
                        on_event=self._on_sidecar_event,
                        on_diagnostic=self._record_diagnostic,
                        queue_max_chunks=self._audio_queue_max_chunks,
                        max_audio_queue_bytes=self._audio_queue_max_bytes,
                        startup_deadline_s=self._startup_deadline_s,
                    )
                else:
                    self._record_diagnostic("worker_not_found")
                    self._fatal = True
                    return
                self._sidecar.start(self.meeting_id)
            except Exception as exc:
                self._record_diagnostic(f"sidecar_start_failed:{type(exc).__name__}")
                self._fatal = True

    def submit_pcm(self, pcm: bytes) -> bool:
        """Try to fan out one validated frame without waiting on the sidecar."""

        with self._lock:
            if not self._started or self._closed or self._audio_disabled or self._sidecar is None:
                return False
            try:
                validate_float32_pcm_payload(pcm)
            except Exception as exc:
                self._record_diagnostic(f"invalid_pcm:{type(exc).__name__}")
                self._audio_disabled = True
                return False
            sample_start = self._next_sample_start
            sample_count = len(pcm) // 4
            self._next_sample_start += sample_count
            accepted = False
            try:
                accepted = bool(self._sidecar.submit(pcm, sample_start=sample_start))
            except Exception as exc:
                self._record_diagnostic(f"sidecar_submit_failed:{type(exc).__name__}")
            if not accepted:
                # Once one frame cannot be delivered, stop sending later frames.
                # This preserves the sidecar's sample continuity contract while
                # keeping ASR and recording completely independent.
                self._audio_disabled = True
                submit_reason = str(getattr(self._sidecar, "last_submit_reason", "") or "")
                if submit_reason in {"audio_queue_full", "audio_byte_budget_exhausted"}:
                    self._record_diagnostic("diarization_backpressure")
                    self._record_diagnostic("diarization_audio_drop")
                else:
                    self._record_diagnostic("diarization_audio_fanout_stopped")
            return accepted

    def observe_final(self, event: dict[str, Any]) -> None:
        segment_id = str(event.get("segment_id") or "").strip()
        if not segment_id:
            return
        self._enqueue_projection("final", dict(event))

    def finish(self) -> dict[str, Any]:
        with self._lock:
            if not self._started or self._closed:
                return self.diagnostics
            sidecar = self._sidecar
        deadline = time.monotonic() + self._finish_timeout_s
        if sidecar is not None:
            try:
                sidecar.finish(timeout_s=max(0.1, deadline - time.monotonic()))
            except Exception as exc:
                self._record_diagnostic(f"sidecar_finish_failed:{type(exc).__name__}")
                self._fatal = True
        with self._lock:
            self._closed = True
        self._enqueue_projection("close", {})
        self._wait_projection(deadline)
        self._stop_projection_thread(deadline)
        status = "completed" if self._ready and not self._fatal else "failed"
        self._complete_run(status)
        return self.diagnostics

    def abort(self) -> dict[str, Any]:
        with self._lock:
            if not self._started or self._closed:
                return self.diagnostics
            self._closed = True
            sidecar = self._sidecar
        if sidecar is not None:
            try:
                sidecar.abort()
            except Exception as exc:
                self._record_diagnostic(f"sidecar_abort_failed:{type(exc).__name__}")
        self._record_diagnostic("diarization_aborted")
        self._fatal = True
        self._stop_projection_thread(time.monotonic() + 1.0)
        self._complete_run("failed")
        return self.diagnostics

    def _on_sidecar_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type") or "")
        if event_type == "ready":
            with self._lock:
                self._ready = True
            return
        if event_type == "speaker.turn":
            self._enqueue_projection("turn", dict(event))
            return
        if event_type in {"diarization_unavailable", "error", "session_aborted"}:
            reason = str(event.get("reason") or event.get("error_code") or event_type)
            self._record_diagnostic(f"{DIARIZATION_FAILURE_REASON}:{reason}")
            self._fatal = True
            return
        if event_type == "speaker.done":
            return
        if event_type:
            self._record_diagnostic(f"sidecar_event:{event_type}")

    def _enqueue_projection(self, kind: str, payload: dict[str, Any]) -> bool:
        try:
            self._projection_queue.put_nowait((kind, payload))
            return True
        except queue.Full:
            self._record_diagnostic("diarization_projection_queue_full")
            self._fatal = True
            return False

    def _projection_loop(self) -> None:
        while True:
            item = self._projection_queue.get()
            try:
                if item is None:
                    return
                kind, payload = item
                if kind == "turn":
                    self._handle_turn(payload)
                elif kind == "final":
                    self._handle_final(payload)
                elif kind == "close":
                    with self._lock:
                        self._reconcile_locked(final_ids=None, allow_unknown=True)
            except Exception as exc:
                self._record_diagnostic(f"diarization_projection_failed:{type(exc).__name__}")
                self._fatal = True
            finally:
                self._projection_queue.task_done()

    def _handle_turn(self, event: dict[str, Any]) -> None:
        speaker_id = str(event.get("speaker_id") or event.get("speaker") or "").strip()
        if not speaker_id:
            self._record_diagnostic("speaker_turn_missing_speaker")
            return
        try:
            start_ms = round(int(event.get("sample_start") or 0) * 1000 / SAMPLE_RATE)
            end_ms = round(int(event.get("sample_end") or 0) * 1000 / SAMPLE_RATE)
            confidence = event.get("confidence")
            confidence = float(confidence) if confidence is not None else None
            turn = SpeakerTurn(
                start_ms=start_ms,
                end_ms=end_ms,
                cluster_label=speaker_id,
                confidence=confidence,
                is_stable=bool(event.get("is_stable", True)),
                window_ids=(str(event.get("turn_id") or f"window:{start_ms}:{end_ms}"),),
            )
        except (TypeError, ValueError) as exc:
            self._record_diagnostic(f"invalid_speaker_turn:{type(exc).__name__}")
            return
        turn_id = str(event.get("turn_id") or f"turn:{speaker_id}:{start_ms}:{end_ms}")
        with self._lock:
            if turn_id in self._turn_ids:
                return
            self._turn_ids.add(turn_id)
            self._turns.append(turn)
            self._speaker_turn_count += 1
            self._turns.sort(key=lambda item: (item.start_ms, item.end_ms, item.cluster_label or ""))
            if self.persistence is not None and self._run_started:
                try:
                    self.persistence.append_speaker_turn(
                        meeting_id=self.meeting_id,
                        run_id=self.run_id,
                        turn_id=turn_id,
                        start_ms=round(start_ms),
                        end_ms=round(end_ms),
                        cluster_label=speaker_id,
                        speaker_id=speaker_id,
                        confidence=confidence,
                        is_stable=turn.is_stable,
                        window_ids=list(turn.window_ids),
                        now_ms=_now_ms(),
                    )
                except Exception as exc:
                    self._record_diagnostic(f"speaker_turn_persist_failed:{type(exc).__name__}")
                    self._fatal = True
            self._reconcile_locked(final_ids=None, allow_unknown=False)

    def _handle_final(self, event: dict[str, Any]) -> None:
        segment_id = str(event.get("segment_id") or "").strip()
        if not segment_id:
            return
        with self._lock:
            self._finals[segment_id] = dict(event)
            self._reconcile_locked(final_ids=[segment_id], allow_unknown=False)

    def _reconcile_locked(self, *, final_ids: list[str] | None, allow_unknown: bool) -> None:
        ids = list(self._finals) if final_ids is None else [item for item in final_ids if item in self._finals]
        for segment_id in ids:
            event = self._finals[segment_id]
            start_ms = int(event.get("start_ms") or 0)
            end_ms = int(event.get("end_ms") or event.get("received_at_ms") or 0)
            if end_ms <= start_ms:
                continue
            attribution = attribute_segment(
                segment_id,
                start_ms,
                end_ms,
                tuple(self._turns),
                stable_speaker_ids={
                    turn.cluster_label: turn.cluster_label for turn in self._turns if turn.cluster_label
                },
            )
            if attribution.is_unknown and not allow_unknown:
                continue
            speaker_id = attribution.speaker_id
            confidence = attribution.confidence
            signature = (speaker_id, confidence, attribution.reason)
            if self._applied_attributions.get(segment_id) == signature:
                continue
            previous_revision = self._attribution_revisions.get(segment_id, 0)
            revision = previous_revision + 1
            if self.persistence is None or not self._run_started:
                self._attribution_revisions[segment_id] = revision
                self._applied_attributions[segment_id] = signature
                continue
            try:
                self.persistence.apply_segment_speaker_attribution(
                    meeting_id=self.meeting_id,
                    run_id=self.run_id,
                    segment_id=segment_id,
                    attribution_revision=revision,
                    speaker_id=speaker_id,
                    confidence=confidence,
                    source="local_realtime_diarization",
                    reason=attribution.reason,
                    now_ms=_now_ms(),
                )
            except KeyError:
                # The final callback and persistence transaction may race in a
                # custom caller. Keep the final and retry on the next turn/end.
                continue
            except Exception as exc:
                self._record_diagnostic(f"speaker_attribution_persist_failed:{type(exc).__name__}")
                self._fatal = True
                continue
            self._attribution_revisions[segment_id] = revision
            self._applied_attributions[segment_id] = signature

    def _wait_projection(self, deadline: float) -> None:
        while time.monotonic() < deadline:
            if self._projection_queue.unfinished_tasks == 0:
                return
            time.sleep(0.005)
        if self._projection_queue.unfinished_tasks:
            self._record_diagnostic("diarization_projection_drain_timeout")
            self._fatal = True

    def _stop_projection_thread(self, deadline: float) -> None:
        thread = self._projection_thread
        if thread is None:
            return
        try:
            self._projection_queue.put(None, timeout=max(0.0, deadline - time.monotonic()))
        except queue.Full:
            self._record_diagnostic("diarization_projection_stop_queue_full")
        thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _complete_run(self, status: str) -> None:
        if self.persistence is None or not self._run_started:
            return
        try:
            self.persistence.create_or_update_speaker_run(
                meeting_id=self.meeting_id,
                run_id=self.run_id,
                source=self.source,
                model=self.model,
                status=status,
                metadata=self._run_metadata(),
                now_ms=_now_ms(),
                completed_at_ms=_now_ms() if status == "completed" else None,
            )
        except Exception as exc:
            self._record_diagnostic(f"persistence_run_complete_failed:{type(exc).__name__}")

    @staticmethod
    def _run_metadata() -> dict[str, Any]:
        return {"protocol": "funasr-diarization-jsonl.v1", "offline": True}

    def _record_diagnostic(self, reason: str) -> None:
        normalized = str(reason or "").strip()[:160]
        if not normalized:
            return
        with self._lock:
            if normalized not in self._diagnostic_reasons:
                self._diagnostic_reasons.append(normalized)
        _log.warning("diarization.runtime.degraded", session_id=self.meeting_id, reason=normalized)


def _now_ms() -> int:
    return time.time_ns() // 1_000_000

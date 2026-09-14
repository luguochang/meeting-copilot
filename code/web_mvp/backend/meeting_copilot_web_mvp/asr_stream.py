"""Realtime ASR streaming over WebSocket.

Accepts binary PCM audio chunks, runs a streaming recognizer, emits ASR events
(partial/final) back over the socket. The recognizer is pluggable:

- FakeStreamRecognizer (default): deterministic, used for tests and as a
  no-sherpa fallback. Produces a partial per chunk and a final on end.
- A sherpa-backed recognizer plugs in here in Phase 4 via the ASR worker sidecar
  (sherpa-onnx lives in a separate Python 3.11 venv, so it cannot be imported
  directly by the 3.14 web backend).

Protocol: client sends binary PCM chunks, text "FLUSH" to close the current
utterance without ending capture, and text "END" to finalize. Server
responds with one JSON ASR event per chunk (partial) and one final event.
"""

from __future__ import annotations

import asyncio
from asyncio import CancelledError as AsyncCancelledError
from asyncio import get_running_loop as _get_running_loop
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import functools
import json
import math
import os
import queue
import struct
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from meeting_copilot_web_mvp.logging_config import get_logger
from meeting_copilot_web_mvp.meeting_preparation import (
    normalize_hotwords,
    normalize_meeting_id,
)
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
from meeting_copilot_web_mvp.audio_assets import (
    Float32PcmPayloadError,
    RealtimeWavAssetWriter,
    validate_float32_pcm_payload,
)
from meeting_copilot_web_mvp.funasr_resident import (
    FUNASR_BOUNDARY_ACK_TIMEOUT_S,
    FunasrResidentBoundaryTimeoutError,
    FunasrResidentBusyError,
    FunasrResidentSession,
    FunasrResidentUnavailableError,
    FunasrResidentWorkerManager,
)
from meeting_copilot_web_mvp.native_pcm_protocol import (
    NativePcmFrame,
    NativePcmProtocolError,
    NativePcmV2Decoder,
    PROTOCOL_NAME as NATIVE_PCM_PROTOCOL_NAME,
)
from meeting_copilot_web_mvp.realtime_transcript_correction import POLICY_VERSION as REALTIME_CORRECTION_POLICY_VERSION
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller, LEVEL_HEAVY
from meeting_copilot_web_mvp.diarization_runtime import DiarizationRuntime
from meeting_copilot_web_mvp.local_runtime_paths import (
    RuntimeManifest,
    manifest_value,
    packaged_funasr_environment,
    read_runtime_manifest,
    resolve_manifest_component,
    venv_python_path,
)
from meeting_copilot_web_mvp.asr_refiner import (
    ONLINE_ONLY_REFINEMENT_REASON,
    refine_pcm_f32,
    release_refiner_for_meeting,
    retain_refiner_for_meeting,
)

_log = get_logger("meeting_copilot_web_mvp.asr_stream")
# Real Windows microphone captures keep a low room-noise floor around -45 dB.
# A -50.5 dB threshold misses natural one-second pauses and defers every final
# to the 15-second hard boundary. -44.4 dB preserves quiet speech while
# allowing those measured pauses to close the current ASR checkpoint.
VAD_SILENCE_RMS_THRESHOLD = 0.006
VAD_ENDPOINT_SILENCE_MS = 900
# Keep a short amount of room tone before the first voiced frame for endpoint
# refinement, but never let a long pre-speech pause grow the ASR buffer.
VAD_PREROLL_MS = 300
# A disconnected socket must contain more than one short energy spike before
# offline refinement is allowed to invent an authoritative recovery segment.
# Browser capture commonly delivers 100 ms frames; 300 ms preserves genuine
# short utterances while rejecting the measured transient-noise failure.
VAD_INTERRUPTED_BACKFILL_MIN_VOICED_MS = 300
# Long uninterrupted speech must still produce bounded confirmed segments so
# downstream correction and suggestion work can run while the meeting is live.
VAD_MAX_SEGMENT_MS = 15_000
# Once the soft limit is reached, prefer the first low-energy frame instead of
# slicing continuous speech at an arbitrary PCM boundary. The grace remains
# bounded so downstream realtime work cannot be deferred indefinitely.
VAD_MAX_SEGMENT_GRACE_MS = 900
# Generic snapshot partials need a stronger admission floor. Explicit FunASR
# incremental chunks are accumulated separately and use the authoritative-final
# guard when an endpoint is reached.
VAD_MIN_FINAL_TEXT_CHARS = 6
STREAM_FLUSH_COMMAND = "FLUSH"
ASR_CONFIDENCE_SOURCE_REALTIME_REPORTED = "realtime_provider_reported_score"
ASR_CONFIDENCE_SOURCE_REALTIME_UNAVAILABLE = "realtime_provider_no_score"
ASR_CONFIDENCE_SOURCE_REALTIME_INVALID = "realtime_provider_invalid_score"
ASR_CONFIDENCE_SOURCE_OFFLINE_UNAVAILABLE = "offline_refiner_no_score"
ASR_CONFIDENCE_SOURCE_LEGACY_UNATTRIBUTED = "legacy_unattributed_score"
# FunASR's resident protocol labels each streaming result as an
# ``incremental_chunk``.  Keep the accumulator bounded even if a provider
# violates the expected VAD segment duration or starts emitting unusually
# large chunks.  This is deliberately separate from the live projection cap:
# it is the raw hypothesis used for endpoint finalization.
INCREMENTAL_ENDPOINT_TEXT_MAX_CHARS = 1_024
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
MAX_SESSION_HOTWORD_REGISTRATIONS = 128
# A boundary wait is split into an initial bounded slice and at most one retry.
# The resident session keeps the same in-flight command across slices, so this
# budget limits endpoint latency without issuing duplicate flush commands.
FUNASR_BOUNDARY_MAX_WAIT_S = FUNASR_BOUNDARY_ACK_TIMEOUT_S * 2
FUNASR_BOUNDARY_MAX_WAIT_ATTEMPTS = 2
FUNASR_BOUNDARY_MIN_WAIT_S = 0.001

_SESSION_HOTWORDS: dict[str, tuple[str, ...]] = {}
_SESSION_HOTWORDS_LOCK = threading.RLock()


def _is_meaningful_authoritative_final(text: str) -> bool:
    """Reject single-character endpoint hallucinations without dropping short replies."""

    return sum(character.isalnum() for character in str(text or "")) >= 2


def _asr_confidence_metadata(
    event: Mapping[str, Any],
    *,
    offline_refinement: bool = False,
) -> dict[str, Any]:
    """Preserve real ASR scores and keep unavailable scores explicit."""

    if offline_refinement:
        return {
            "confidence": None,
            "confidence_source": ASR_CONFIDENCE_SOURCE_OFFLINE_UNAVAILABLE,
        }
    confidence = event.get("confidence")
    if isinstance(confidence, bool):
        return {
            "confidence": None,
            "confidence_source": ASR_CONFIDENCE_SOURCE_REALTIME_INVALID,
        }
    if confidence is not None:
        try:
            numeric_confidence = float(confidence)
        except (TypeError, ValueError):
            return {
                "confidence": None,
                "confidence_source": ASR_CONFIDENCE_SOURCE_REALTIME_INVALID,
            }
        if not math.isfinite(numeric_confidence) or not 0.0 <= numeric_confidence <= 1.0:
            return {
                "confidence": None,
                "confidence_source": ASR_CONFIDENCE_SOURCE_REALTIME_INVALID,
            }
    confidence_source = str(event.get("confidence_source") or "").strip()
    if not confidence_source:
        confidence_source = (
            ASR_CONFIDENCE_SOURCE_REALTIME_REPORTED
            if confidence is not None
            else ASR_CONFIDENCE_SOURCE_REALTIME_UNAVAILABLE
        )
    return {
        "confidence": confidence,
        "confidence_source": confidence_source,
    }


def _merge_incremental_chunk_text(
    previous: str,
    current: str,
    *,
    max_chars: int = INCREMENTAL_ENDPOINT_TEXT_MAX_CHARS,
) -> str:
    """Join explicitly incremental ASR chunks without touching snapshots.

    FunASR's online Paraformer returns newly decoded audio windows rather than
    a cumulative hypothesis.  Chunks can overlap by a few characters, while
    English words often arrive split at a boundary.  The function therefore
    removes the longest suffix/prefix overlap and inserts a separator only at
    two ASCII-word boundaries.  A hard character limit keeps malformed or
    adversarial providers from growing the endpoint state without bound.
    """

    previous_text = str(previous or "").strip()
    current_text = str(current or "").strip()
    if not current_text:
        return previous_text[-max_chars:]
    if not previous_text:
        return current_text[-max_chars:]
    if current_text == previous_text:
        return previous_text[-max_chars:]
    if current_text.startswith(previous_text):
        merged = current_text
    else:
        overlap = 0
        for size in range(min(len(previous_text), len(current_text)), 0, -1):
            if previous_text[-size:] == current_text[:size]:
                overlap = size
                break
        suffix = current_text[overlap:]
        needs_space = (
            bool(suffix)
            and previous_text[-1].isascii()
            and previous_text[-1].isalnum()
            and suffix[0].isascii()
            and suffix[0].isalnum()
        )
        merged = previous_text + (" " if needs_space else "") + suffix
    return merged[-max_chars:]


def _uses_online_final_resource_policy(refinement: Any, fallback_text: str) -> bool:
    return (
        str(getattr(refinement, "status", "")) == "bypassed"
        and str(getattr(refinement, "reason", "")) == ONLINE_ONLY_REFINEMENT_REASON
        and _is_meaningful_authoritative_final(fallback_text)
    )


def _refinement_degradation_reason(
    refinement: Any,
    *,
    rejected_short_text: bool = False,
) -> str:
    """Classify a non-authoritative refinement without masking policy bypasses.

    ``online_only`` is an explicit resource policy, not a missing worker. Keep
    that reason distinct from an unavailable/failed offline worker so session
    diagnostics identify the actionable remediation. Text that was rejected by
    the semantic length guard remains a separate quality reason.
    """

    if rejected_short_text:
        return "offline_refinement_text_too_short"
    if str(getattr(refinement, "reason", "") or "").strip() == ONLINE_ONLY_REFINEMENT_REASON:
        return ONLINE_ONLY_REFINEMENT_REASON
    return "offline_refinement_unavailable"


def _exception_origin(error: BaseException) -> str:
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return "unknown"
    frame = frames[-1]
    return f"{Path(frame.filename).stem}.{frame.name}"


def _source_segment_namespace(audio_source: str | None) -> str | None:
    normalized = str(audio_source or "").strip().lower()
    if normalized in {"tauri_native_mic", "native_microphone_streaming"}:
        return "microphone"
    if normalized in {"tauri_system_audio", "macos_system_audio"}:
        return "system_audio"
    return None


def _native_pcm_decoder(
    *,
    pcm_protocol: str | None,
    native_track_id: str | None,
    native_capture_epoch: int | None,
) -> NativePcmV2Decoder | None:
    normalized_protocol = str(pcm_protocol or "").strip().lower()
    if not normalized_protocol:
        return None
    if normalized_protocol != NATIVE_PCM_PROTOCOL_NAME:
        raise NativePcmProtocolError(
            "native_pcm_version_invalid",
            "本地音频协议版本不受支持，请更新客户端。",
        )
    try:
        return NativePcmV2Decoder(
            expected_track_id=str(native_track_id or ""),
            expected_capture_epoch=int(native_capture_epoch or 0),
        )
    except (TypeError, ValueError) as exc:
        raise NativePcmProtocolError(
            "native_pcm_identity_invalid",
            "本地音频轨道或采集批次无效，请重新开始会议。",
        ) from exc


def _decode_native_pcm_payload(
    payload: bytes,
    *,
    decoder: NativePcmV2Decoder | None,
) -> tuple[bytes, NativePcmFrame | None]:
    if decoder is None:
        return payload, None
    frame = decoder.decode(payload)
    return frame.payload, frame


def _native_pcm_event_identity(frame: NativePcmFrame | None) -> dict[str, Any]:
    if frame is None:
        return {}
    return {
        "pcm_protocol": NATIVE_PCM_PROTOCOL_NAME,
        "source_track": frame.track_id,
        "capture_epoch": frame.capture_epoch,
        "track_sequence": frame.sequence,
        "source_timestamp_ms": frame.timestamp_ms,
    }


# These fields are deliberately limited to timing/counter data.  The
# resident worker diagnostics are exported with the terminal ASR event so a
# replay can prove the boundary ACK causal barrier without exposing command
# payloads, transcript text, or process environment values.
_ASR_DIAGNOSTIC_FIELDS = frozenset(
    {
        "abort",
        "budget_ms",
        "write_queue_depth_at_end",
        "max_write_queue_depth",
        "audio_chunks_enqueued",
        "audio_chunks_written",
        "audio_bytes_enqueued",
        "audio_bytes_written",
        "unprocessed_chunks",
        "sentinel_ms",
        "writer_drain_ms",
        "process_wait_ms",
        "reader_drain_ms",
        "total_ms",
        "deadline_exhausted",
        "writer_stopped",
        "process_reused",
        "boundary_diagnostics",
        "worker",
    }
)
_ASR_BOUNDARY_DIAGNOSTIC_FIELDS = frozenset(
    {
        "boundary_id",
        "status",
        "enqueued_at_ms",
        "ack_received_at_ms",
        "ack_consumed_at_ms",
        "ack_latency_ms",
        "timeout_count",
        "duplicate",
        "utterance_index",
        "drain_ms",
        "skipped_silence_bytes",
    }
)
_ASR_WORKER_DIAGNOSTIC_FIELDS = frozenset(
    {
        "input_samples",
        "input_seconds",
        "inference_calls",
        "inference_total_ms",
        "inference_max_ms",
        "worker_total_ms",
        "realtime_factor",
    }
)


def _content_free_asr_diagnostics(value: Any) -> dict[str, Any]:
    """Return a JSON-safe, content-free ASR shutdown diagnostic snapshot."""

    if not isinstance(value, Mapping):
        return {}

    def scalar(raw: Any) -> Any:
        if raw is None or isinstance(raw, (bool, int)):
            return raw
        if isinstance(raw, float):
            return raw if math.isfinite(raw) else None
        return None

    result: dict[str, Any] = {}
    for key in _ASR_DIAGNOSTIC_FIELDS:
        raw = value.get(key)
        if key == "boundary_diagnostics":
            if not isinstance(raw, (list, tuple)):
                continue
            boundaries: list[dict[str, Any]] = []
            for item in raw:
                if not isinstance(item, Mapping):
                    continue
                boundary: dict[str, Any] = {}
                for field in _ASR_BOUNDARY_DIAGNOSTIC_FIELDS:
                    converted = scalar(item.get(field))
                    if isinstance(item.get(field), str) and field in {"boundary_id", "status"}:
                        converted = str(item[field])[:192]
                    if converted is not None:
                        boundary[field] = converted
                if boundary.get("boundary_id"):
                    boundaries.append(boundary)
            if boundaries:
                result[key] = boundaries
            continue
        if key == "worker":
            if not isinstance(raw, Mapping):
                continue
            worker = {
                field: converted
                for field in _ASR_WORKER_DIAGNOSTIC_FIELDS
                if (converted := scalar(raw.get(field))) is not None
            }
            if worker:
                result[key] = worker
            continue
        converted = scalar(raw)
        if converted is not None:
            result[key] = converted
    return result


def _source_qualified_streaming_events(
    events: list[dict[str, Any]],
    *,
    audio_source: str | None,
) -> list[dict[str, Any]]:
    namespace = _source_segment_namespace(audio_source)
    if namespace is None:
        return events
    qualified: list[dict[str, Any]] = []
    for event in events:
        copied = dict(event)
        segment_id = str(copied.get("segment_id") or "").strip()
        if segment_id and not segment_id.startswith(f"{namespace}:"):
            copied["source_segment_id"] = str(copied.get("source_segment_id") or segment_id)
            copied["segment_id"] = f"{namespace}:{segment_id}"
        qualified.append(copied)
    return qualified


def set_session_hotwords(session_id: str, hotwords: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Bind validated local ASR hotwords to one meeting before recognizer start."""

    normalized_session_id = normalize_meeting_id(session_id)
    normalized_hotwords = normalize_hotwords(hotwords)
    with _SESSION_HOTWORDS_LOCK:
        if not normalized_hotwords:
            _SESSION_HOTWORDS.pop(normalized_session_id, None)
            return ()
        _SESSION_HOTWORDS[normalized_session_id] = normalized_hotwords
        while len(_SESSION_HOTWORDS) > MAX_SESSION_HOTWORD_REGISTRATIONS:
            oldest_session_id = next(iter(_SESSION_HOTWORDS))
            if oldest_session_id == normalized_session_id and len(_SESSION_HOTWORDS) > 1:
                oldest_session_id = next(key for key in _SESSION_HOTWORDS if key != normalized_session_id)
            _SESSION_HOTWORDS.pop(oldest_session_id, None)
    return normalized_hotwords


def session_hotwords(session_id: str) -> tuple[str, ...]:
    normalized_session_id = normalize_meeting_id(session_id)
    with _SESSION_HOTWORDS_LOCK:
        return tuple(_SESSION_HOTWORDS.get(normalized_session_id, ()))


def clear_session_hotwords(session_id: str) -> None:
    normalized_session_id = normalize_meeting_id(session_id)
    with _SESSION_HOTWORDS_LOCK:
        _SESSION_HOTWORDS.pop(normalized_session_id, None)


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
        return [
            {
                "event_type": "partial",
                "segment_id": f"stream_seg_{self.session_id}",
                "text": f"partial {self._seq} ({len(pcm)} bytes)",
                "start_ms": (self._seq - 1) * 300,
                "end_ms": self._seq * 300,
                "confidence": 0.7,
            }
        ]

    def finalize(self) -> list[dict[str, Any]]:
        return [
            {
                "event_type": "final",
                "segment_id": f"stream_seg_{self.session_id}",
                "text": f"final transcript for {self.session_id}",
                "start_ms": 0,
                "end_ms": self._seq * 300,
                "confidence": 0.9,
            }
        ]

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
_SHERPA_VENV_PY = venv_python_path(_REPO_ROOT / "code" / "asr_runtime" / ".venv-sherpa")
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
        if proc.poll() is not None:
            return
    except Exception:
        # Test doubles and unusual process wrappers may not expose poll().
        pass
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
            SIDECAR_GRACEFUL_DRAIN_MARGIN_S + estimated_audio_s * SIDECAR_GRACEFUL_DRAIN_AUDIO_FACTOR,
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
    total_timeout_s = SIDECAR_PROCESS_WAIT_TIMEOUT_S if abort else _sidecar_graceful_drain_timeout_s(recognizer)
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
                        _log.warning(
                            "asr.sidecar.cold_start_slow", session_id=self.session_id, elapsed_s=round(elapsed, 1)
                        )
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
            timed_out = self._generation is generation and not self._finalizing and generation.ready_time is None
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
            ev.update(_asr_confidence_metadata(ev))
            events.append(ev)
        if not events:
            events.append(
                {
                    "event_type": "partial",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    "start_ms": (self._seq - 1) * 300,
                    "end_ms": self._seq * 300,
                    **_asr_confidence_metadata({}),
                }
            )
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
            ev.update(_asr_confidence_metadata(ev))
            events.append(ev)
        if not events:
            events.append(
                {
                    "event_type": "final",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    **_asr_confidence_metadata({}),
                }
            )
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


@dataclass(frozen=True)
class _FunasrRuntimeConfig:
    python: Path | None
    worker: Path | None
    model: Path | None
    engine: str
    manifest: RuntimeManifest
    errors: tuple[str, ...] = ()


def _resolve_funasr_runtime(
    environ: Mapping[str, str] | None = None,
) -> _FunasrRuntimeConfig:
    effective_env = os.environ if environ is None else environ
    manifest = read_runtime_manifest(effective_env)
    errors: list[str] = list(manifest.errors)

    def component_path(
        env_name: str,
        default: Path,
        *,
        component_name: str,
        expected_kind: str,
        mirrored_fields: tuple[tuple[str, ...], ...],
        preserve_symlink: bool = False,
    ) -> Path | None:
        configured = str(effective_env.get(env_name) or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if preserve_symlink:
                return Path(os.path.abspath(path))
            return path.resolve(strict=False)
        if not manifest.configured:
            return default
        result = resolve_manifest_component(
            manifest,
            component_name=component_name,
            expected_kind=expected_kind,
            mirrored_fields=mirrored_fields,
        )
        errors.extend(error for error in result.errors if error not in errors)
        return result.path

    python = component_path(
        "MEETING_COPILOT_FUNASR_PYTHON",
        _FUNASR_VENV_PY,
        component_name="file_asr.python_launcher",
        expected_kind="file",
        mirrored_fields=(
            ("runtimes", "funasr", "venv_executable"),
            ("file_asr", "runtime", "executable"),
        ),
        preserve_symlink=True,
    )
    worker = component_path(
        "MEETING_COPILOT_FUNASR_WORKER",
        _FUNASR_WORKER,
        component_name="realtime_asr.worker",
        expected_kind="file",
        mirrored_fields=(
            ("workers", "realtime"),
            ("worker_inventory", "realtime", "path"),
        ),
    )
    model = component_path(
        "MEETING_COPILOT_FUNASR_MODEL_DIR",
        _FUNASR_MODEL_DIR,
        component_name="realtime_asr.model",
        expected_kind="directory",
        mirrored_fields=(("realtime_model", "root"),),
    )

    configured_engine = str(effective_env.get("MEETING_COPILOT_FUNASR_ENGINE") or "").strip().lower()
    if configured_engine:
        engine = configured_engine
    elif manifest.configured:
        declared_engines = {
            str(manifest_value(manifest.payload, "realtime_model", "engine") or "").strip().lower(),
            str(manifest_value(manifest.payload, "realtime_runtime", "engine") or "").strip().lower(),
        }
        declared_engines.discard("")
        if len(declared_engines) == 1:
            engine = next(iter(declared_engines))
        else:
            engine = "pytorch"
            errors.append("realtime_engine_manifest_mismatch")
    else:
        engine = _FUNASR_ENGINE
    if engine not in {"pytorch", "onnx"}:
        errors.append("realtime_engine_invalid")
        engine = "pytorch"

    return _FunasrRuntimeConfig(
        python=python,
        worker=worker,
        model=model,
        engine=engine,
        manifest=manifest,
        errors=tuple(dict.fromkeys(errors)),
    )


def _funasr_process_environment(
    runtime: _FunasrRuntimeConfig | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Use the bundled FunASR runtime instead of the backend's Python home."""
    effective_env = os.environ if environ is None else environ
    runtime = runtime or _resolve_funasr_runtime(effective_env)
    environment = dict(effective_env)
    for key in list(environment):
        upper = key.upper()
        if upper.endswith("_API_KEY") or upper in {
            "AUTHORIZATION",
            "MEETING_COPILOT_LOCAL_API_TOKEN",
        }:
            environment.pop(key, None)
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    python_home = str(effective_env.get("MEETING_COPILOT_FUNASR_PYTHON_HOME") or "").strip()
    python_path = str(effective_env.get("MEETING_COPILOT_FUNASR_PYTHONPATH") or "").strip()
    site_packages = str(effective_env.get("MEETING_COPILOT_FUNASR_SITE_PACKAGES") or "").strip()
    packaged_environment = packaged_funasr_environment(
        runtime.manifest,
        worker_path=runtime.worker,
        include_realtime_runtime=runtime.engine == "onnx",
    )
    if packaged_environment.errors:
        _log.warning(
            "asr.sidecar.funasr.packaged_environment_invalid",
            errors=list(packaged_environment.errors),
        )
    else:
        python_home = python_home or packaged_environment.values.get("PYTHONHOME", "")
        python_path = python_path or packaged_environment.values.get("PYTHONPATH", "")
        site_packages = site_packages or packaged_environment.values.get(
            "MEETING_COPILOT_FUNASR_SITE_PACKAGES", ""
        )
    if python_home:
        environment["PYTHONHOME"] = python_home
    if python_path:
        environment["PYTHONPATH"] = python_path
    if site_packages:
        environment["MEETING_COPILOT_FUNASR_SITE_PACKAGES"] = site_packages
    # The resident JSONL protocol is UTF-8. Windows otherwise inherits the
    # active ANSI code page for redirected stdout and corrupts Chinese text.
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["MODELSCOPE_OFFLINE"] = "1"
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    return environment


_FUNASR_VENV_PY = _configured_local_path(
    "MEETING_COPILOT_FUNASR_PYTHON",
    venv_python_path(_REPO_ROOT / "code" / "asr_runtime" / ".venv-funasr"),
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
_FUNASR_ENGINE = os.environ.get("MEETING_COPILOT_FUNASR_ENGINE", "pytorch").strip().lower()
if _FUNASR_ENGINE not in {"pytorch", "onnx"}:
    _FUNASR_ENGINE = "pytorch"
# The streaming model owns replaceable preview text only. A 960 ms stride leaves
# room for CPU inference and transport inside the 1.2 s visible-preview target,
# while increasing inference frequency by only 25% over the stable 1.2 s
# profile. The independent offline refiner still owns every authoritative final
# and therefore the quality/accuracy contract.
FUNASR_REALTIME_PROFILE = "responsive_preview_authoritative_refiner"
FUNASR_REALTIME_CHUNK_SIZE = [0, 16, 8]
FUNASR_REALTIME_ENCODER_CHUNK_LOOK_BACK = 4
FUNASR_REALTIME_DECODER_CHUNK_LOOK_BACK = 1


def _funasr_worker_command(
    venv_python: Path | None = None,
    *,
    session_hotword_values: tuple[str, ...] = (),
    runtime: _FunasrRuntimeConfig | None = None,
) -> list[str]:
    runtime = runtime or _resolve_funasr_runtime()
    command = [
        str(venv_python or runtime.python),
        str(runtime.worker),
        "--model",
        str(runtime.model),
        "--engine",
        runtime.engine,
        "--chunk-size",
        ",".join(str(value) for value in FUNASR_REALTIME_CHUNK_SIZE),
        "--encoder-chunk-look-back",
        str(FUNASR_REALTIME_ENCODER_CHUNK_LOOK_BACK),
        "--decoder-chunk-look-back",
        str(FUNASR_REALTIME_DECODER_CHUNK_LOOK_BACK),
    ]
    try:
        hotwords = list(
            dict.fromkeys(
                [
                    *[str(value).strip() for value in _hotwords() if str(value).strip()],
                    *session_hotword_values,
                ]
            )
        )
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
    inference_engine = _FUNASR_ENGINE

    def __init__(
        self,
        session_id: str,
        venv_python: Path | None = None,
        *,
        session_hotword_values: tuple[str, ...] = (),
    ):
        self.session_id = session_id
        runtime = _resolve_funasr_runtime()
        self.asr_profile = FUNASR_REALTIME_PROFILE
        self.chunk_size = list(FUNASR_REALTIME_CHUNK_SIZE)
        self.inference_engine = runtime.engine
        self._cmd = _funasr_worker_command(
            venv_python,
            session_hotword_values=session_hotword_values,
            runtime=runtime,
        )
        self._environment = _funasr_process_environment(runtime)
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
            env=self._environment,
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
                        _log.warning(
                            "asr.sidecar.funasr.cold_start_slow",
                            session_id=self.session_id,
                            elapsed_s=round(elapsed, 1),
                        )
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
            timed_out = self._generation is generation and not self._finalizing and generation.ready_time is None
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

    def _drain_events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        while not self._q.empty():
            ev = self._q.get_nowait()
            ev["segment_id"] = _session_scoped_segment_id(
                self.session_id,
                ev.get("segment_id"),
                fallback=f"stream_seg_{self.session_id}",
            )
            ev.update(_asr_confidence_metadata(ev))
            events.append(ev)
        return events

    def recognize_chunk(self, pcm: bytes) -> list[dict[str, Any]]:
        self._seq += 1
        _enqueue_sidecar_audio(self, pcm)
        events = self._drain_events()
        if not events:
            events.append(
                {
                    "event_type": "partial",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    "start_ms": (self._seq - 1) * 300,
                    "end_ms": self._seq * 300,
                    **_asr_confidence_metadata({}),
                }
            )
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
        events = self._drain_events()
        if not events:
            events.append(
                {
                    "event_type": "final",
                    "segment_id": f"stream_seg_{self.session_id}",
                    "text": "",
                    **_asr_confidence_metadata({}),
                }
            )
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
            runtime = _resolve_funasr_runtime()
            _FUNASR_RESIDENT_MANAGER = FunasrResidentWorkerManager(
                _funasr_worker_command(runtime=runtime),
                environment=_funasr_process_environment(runtime),
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
        manager = _get_funasr_resident_manager()
        manager.start()
        timeout_seconds = max(
            1.0,
            min(55.0, float(os.environ.get("MEETING_COPILOT_FUNASR_PREWARM_TIMEOUT_SECONDS") or 45.0)),
        )
        if not manager.wait_process_ready(timeout_seconds):
            status = manager.status()
            _log.warning(
                "asr.sidecar.funasr.resident_prewarm_not_ready",
                timeout_seconds=timeout_seconds,
                status=status,
            )
            return False
    except Exception as exc:
        _log.warning("asr.sidecar.funasr.resident_prewarm_failed", error=str(exc))
        return False
    return True


def funasr_resident_status() -> dict[str, Any]:
    with _FUNASR_RESIDENT_MANAGER_LOCK:
        manager = _FUNASR_RESIDENT_MANAGER
    if manager is None:
        return {
            "schema_version": "funasr_resident_status.v1",
            "spawned": False,
            "process_running": False,
            "process_ready": False,
            "pid": None,
            "generation": None,
            "active_session_id": None,
            "process_start_count": 0,
            "completed_session_count": 0,
            "last_exit_code": None,
            "last_error": None,
        }
    return manager.status()


def _maybe_funasr_sidecar(
    session_id: str,
) -> FunasrSidecarRecognizer | FunasrResidentSession | _UnavailableFunasrRecognizer | None:
    """Return a FunasrSidecarRecognizer if the funasr venv + worker exist, else None."""
    if not funasr_realtime_available():
        return None
    meeting_hotwords = session_hotwords(session_id)
    if _funasr_resident_enabled():
        try:
            recognizer = _get_funasr_resident_manager().create_session(
                session_id,
                hotwords=meeting_hotwords,
            )
            recognizer.asr_profile = FUNASR_REALTIME_PROFILE
            recognizer.chunk_size = list(FUNASR_REALTIME_CHUNK_SIZE)
            recognizer.inference_engine = _resolve_funasr_runtime().engine
            _log.info("asr.sidecar.funasr.resident_session_start", session_id=session_id)
            return recognizer
        except FunasrResidentBusyError:
            _log.warning("asr.sidecar.funasr.resident_busy", session_id=session_id)
            return _UnavailableFunasrRecognizer("funasr_resident_worker_busy")
        except Exception as exc:
            _log.warning("asr.sidecar.funasr.resident_spawn_failed", error=str(exc))
    try:
        return FunasrSidecarRecognizer(
            session_id,
            session_hotword_values=meeting_hotwords,
        )
    except Exception as exc:
        _log.warning("asr.sidecar.funasr.spawn_failed", error=str(exc))
        return None


def funasr_realtime_available(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return true only when the local runtime and model files are ready.

    Passing a model name to FunASR allows ModelScope to download at runtime. The
    meeting product must fail closed instead of downloading an 840MB model during
    a live meeting, so the local model directory is part of readiness.
    """
    runtime = _resolve_funasr_runtime(environ)
    if runtime.errors or runtime.python is None or runtime.worker is None or runtime.model is None:
        return False
    model_ready = (
        (runtime.model / "model.onnx").is_file()
        and (runtime.model / "decoder.onnx").is_file()
        if runtime.engine == "onnx"
        else (runtime.model / "model.pt").is_file()
    )
    return (
        runtime.python.is_file()
        and runtime.worker.is_file()
        and runtime.model.is_dir()
        and model_ready
        and (runtime.model / "config.yaml").is_file()
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
    await websocket.send_text(
        json.dumps(
            {
                "event_type": "provider_error",
                "error_code": "recording_unavailable",
                "message": "录音当前不可用，请检查麦克风权限、音频设备或本地服务后重试。",
                "degradation_level": degradation_level,
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )
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
    on_audio_recording_setup_failed: Callable[[], Any] | None = None,
    audio_asset_lock: Any | None = None,
    pcm_protocol: str | None = None,
    native_track_id: str | None = None,
    native_capture_epoch: int | None = None,
    emit_transport_ready: bool = False,
) -> None:
    await websocket.accept()
    if emit_transport_ready:
        # The browser can observe a close before its WebSocket `open` callback
        # when the recognizer fails during startup. Make transport acceptance
        # explicit so the UI can preserve the recording and surface the real
        # provider error instead of rolling the meeting back as a handshake
        # failure.
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "asr_transport_ready",
                    "provider": "recording_only",
                    "ready": True,
                },
                ensure_ascii=False,
            )
        )
    if audio_asset_data_dir is None:
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": "recording_storage_unavailable",
                    "message": "录音存储目录未配置，当前不能进入仅录音模式。",
                    "degradation_level": 3,
                },
                ensure_ascii=False,
            )
        )
        await websocket.close()
        return

    source_type = audio_source or "live_asr_stream"
    try:
        native_decoder = _native_pcm_decoder(
            pcm_protocol=pcm_protocol,
            native_track_id=native_track_id,
            native_capture_epoch=native_capture_epoch,
        )
    except NativePcmProtocolError as exc:
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": exc.code,
                    "message": exc.user_message,
                    "recording_saved": False,
                    "recoverable": True,
                },
                ensure_ascii=False,
            )
        )
        await websocket.close()
        return

    def _setup_writer() -> RealtimeWavAssetWriter:
        with audio_asset_lock if audio_asset_lock is not None else nullcontext():
            if on_audio_recording_started is not None:
                on_audio_recording_started(
                    {
                        "session_id": session_id,
                        "source_type": source_type,
                        "sample_rate_hz": 16_000,
                        "track_id": native_track_id,
                        "epoch": int(native_capture_epoch or 0),
                    }
                )
            return RealtimeWavAssetWriter(
                data_dir=audio_asset_data_dir,
                session_id=session_id,
                source_type=source_type,
                track_id=native_track_id,
                epoch=int(native_capture_epoch or 0),
                on_chunk_committed=on_audio_chunk_committed,
                authorize_chunk_commit=authorize_audio_chunk_commit,
            )

    try:
        writer = await asyncio.to_thread(_setup_writer)
    except Exception as exc:
        if on_audio_recording_setup_failed is not None:
            try:
                await asyncio.to_thread(on_audio_recording_setup_failed)
            except Exception as rollback_exc:
                _log.error(
                    "asr.recording_only.setup_rollback_failed",
                    session_id=session_id,
                    error_class=type(rollback_exc).__name__,
                    error_origin=_exception_origin(rollback_exc),
                )
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": "recording_resume_failed",
                    "message": "录音恢复失败，请稍后重试。",
                    "recording_saved": False,
                    "recoverable": True,
                },
                ensure_ascii=False,
            )
        )
        _log.error(
            "asr.recording_only.recording_setup_failed",
            session_id=session_id,
            error_class=type(exc).__name__,
            error_origin=_exception_origin(exc),
        )
        await websocket.close()
        return
    await websocket.send_text(
        json.dumps(
            {
                "event_type": "recording_only",
                "message": "实时识别暂不可用，本次会议将继续保留录音。",
                "degradation_level": 3,
            },
            ensure_ascii=False,
        )
    )

    def persist(
        audio_asset: dict[str, Any],
        *,
        interrupted: bool,
        extra_reasons: tuple[str, ...] = (),
    ) -> None:
        reasons = [degradation_reason, *extra_reasons]
        if interrupted:
            reasons.append("stream_interrupted")
        streaming_events = (
            []
            if interrupted
            else [
                {
                    "event_type": "end_of_stream",
                    "end_ms": int(audio_asset.get("duration_ms") or 0),
                    "received_at_ms": int(audio_asset.get("duration_ms") or 0),
                }
            ]
        )
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
                    "degradation_reasons": _dedupe_values(
                        [
                            *list(existing.get("degradation_reasons") or []),
                            *reasons,
                        ]
                    ),
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

    audio_asset: dict[str, Any] | None = None
    try:
        while True:
            message = await websocket.receive()
            if message.get("bytes") is not None:
                pcm_payload, _native_frame = _decode_native_pcm_payload(
                    message["bytes"],
                    decoder=native_decoder,
                )
                writer.write_float32_pcm(pcm_payload)
            elif message.get("text") == "END":
                audio_asset = seal_audio(interrupted=False)
                persist(audio_asset, interrupted=False)
                await websocket.close()
                return
    except Float32PcmPayloadError as exc:
        try:
            audio_asset = seal_audio(interrupted=True)
            persist(audio_asset, interrupted=True, extra_reasons=(exc.code,))
        except Exception as persist_exc:
            writer.discard()
            _log.warning(
                "asr.recording_only.persist_failed",
                session_id=session_id,
                error=str(persist_exc),
            )
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": exc.code,
                    "message": exc.user_message,
                    "provider": "recording_only_local_audio",
                    "provider_mode": "recording_only",
                    "recording_saved": bool(audio_asset and audio_asset.get("saved")),
                    "recoverable": True,
                },
                ensure_ascii=False,
            )
        )
        _log.warning(
            "asr.recording_only.invalid_audio_payload",
            session_id=session_id,
            error_code=exc.code,
        )
        try:
            await websocket.close()
        except Exception:
            pass
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

    final_indexes = [index for index, event in enumerate(events) if event.get("event_type") == "final"]
    partial_indexes = [index for index, event in enumerate(events) if event.get("event_type") == "partial"]
    other_indexes = [index for index, event in enumerate(events) if event.get("event_type") not in {"final", "partial"}]
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
    on_audio_recording_setup_failed: Callable[[], Any] | None = None,
    audio_asset_lock: Any | None = None,
    diarization_persistence: Any | None = None,
    diarization_enabled: bool = False,
    diarization_sidecar_factory: Callable[..., Any] | None = None,
    pcm_protocol: str | None = None,
    native_track_id: str | None = None,
    native_capture_epoch: int | None = None,
    emit_transport_ready: bool = False,
) -> None:
    """Handle one WS audio stream: read chunks, emit ASR events back over the WS.

    If asr_live_repo is provided, accumulates real ASR final events and persists
    a session record on END — so the real mic -> ASR -> session -> LLM cards
    pipeline is connected end-to-end (llm-execution-runs / approach-cards /
    minutes can then run on the real ASR session).
    """
    await websocket.accept()
    if emit_transport_ready:
        # See the recording-only path above. This frame is deliberately sent
        # before recognizer construction, which may take a cold-start-sized
        # amount of time or fail independently of WebSocket transport.
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "asr_transport_ready",
                    "provider": "transport",
                    "ready": True,
                },
                ensure_ascii=False,
            )
        )
    try:
        native_decoder = _native_pcm_decoder(
            pcm_protocol=pcm_protocol,
            native_track_id=native_track_id,
            native_capture_epoch=native_capture_epoch,
        )
    except NativePcmProtocolError as exc:
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": exc.code,
                    "message": exc.user_message,
                    "recording_saved": False,
                    "recoverable": True,
                },
                ensure_ascii=False,
            )
        )
        await websocket.close()
        return
    recognizer = get_recognizer(session_id)
    provider_metadata = _recognizer_provider_metadata(recognizer, configured_provider=provider)

    def _boundary_diagnostics() -> list[dict[str, Any]]:
        snapshot = _content_free_asr_diagnostics(
            {"boundary_diagnostics": getattr(recognizer, "boundary_diagnostics", [])}
        )
        values = snapshot.get("boundary_diagnostics")
        return list(values) if isinstance(values, list) else []

    def _normalize_capture_event(event: dict[str, Any]) -> dict[str, Any]:
        normalized = _normalize_client_stream_event(
            event,
            l3_normalize_enabled=l3_normalize_enabled,
        )
        capture_epoch = max(0, int(native_capture_epoch or 0))
        capture_track = str(native_track_id or "").strip()
        segment_id = str(normalized.get("segment_id") or "").strip()
        if capture_epoch > 0 and capture_track in {"microphone", "system_audio"} and segment_id:
            prefix = f"{capture_track}:e{capture_epoch}:"
            if not segment_id.startswith(prefix):
                normalized["segment_id"] = f"{prefix}{segment_id}"
            normalized["source_track"] = capture_track
            normalized["capture_epoch"] = capture_epoch
        return normalized
    is_funasr_realtime = (
        str(getattr(recognizer, "provider", "")) == "funasr_realtime"
        or provider_metadata["provider"] == "funasr_realtime"
    )
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
    session_executor_shutdown = False
    event_loop = _get_running_loop()
    diarization_runtime: DiarizationRuntime | None = None

    async def _run_blocking(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        operation = functools.partial(fn, *args, **kwargs)
        future = event_loop.run_in_executor(session_executor, operation)
        return await future

    async def _shutdown_session_executor() -> None:
        nonlocal session_executor_shutdown
        if session_executor_shutdown:
            return
        # All stream operations are awaited in sequence. The shutdown itself
        # is still moved off the event loop so an interrupted stream cannot
        # accidentally block the server while joining a worker thread.
        await asyncio.to_thread(
            session_executor.shutdown,
            wait=True,
            cancel_futures=True,
        )
        session_executor_shutdown = True

    _log.info("asr.stream.start", session_id=session_id)
    existing_record: dict[str, Any] = {}
    if asr_live_repo is not None:
        try:
            existing_record = asr_live_repo.get(session_id)
        except KeyError:
            existing_record = {}
    interrupted_backfill_state = dict(existing_record.get("transcript_backfill") or {})

    def _stream_normalized_text(
        text: str,
        existing_normalized_text: Any = None,
    ) -> str:
        if not l3_normalize_enabled:
            return text
        return str(existing_normalized_text or _normalize_text(text))

    def _commit_normalized_final(event: dict[str, Any]) -> None:
        committed: Any = None
        if on_final_committed is not None:
            committed = on_final_committed(dict(event))
        if diarization_runtime is not None:
            observed = dict(event)
            # The application may namespace microphone/system-audio segment IDs
            # during the durable commit. Use that returned ID for attribution.
            if isinstance(committed, dict) and committed.get("segment_id"):
                observed["segment_id"] = committed["segment_id"]
            diarization_runtime.observe_final(observed)

    def _persisted_raw_transcript_events() -> list[dict[str, Any]]:
        restored: list[dict[str, Any]] = []
        for event in list(existing_record.get("events") or []):
            event_type = str(event.get("event_type") or "")
            if event_type not in {"transcript_partial", "transcript_final"}:
                continue
            payload = dict(event.get("payload") or {})
            raw_source_text = str(
                payload.get("source_snapshot_text") or payload.get("text") or payload.get("normalized_text") or ""
            ).strip()
            text = raw_source_text
            segment_id = str(payload.get("segment_id") or "").strip()
            if not text or not segment_id:
                continue
            authoritative = payload.get("authoritative") is not False
            normalized_text = _normalize_text(raw_source_text) if l3_normalize_enabled else raw_source_text
            restored.append(
                {
                    "event_type": (
                        "final"
                        if event_type == "transcript_final" and authoritative
                        else "partial"
                    ),
                    "segment_id": segment_id,
                    "text": text,
                    "normalized_text": normalized_text,
                    "start_ms": int(payload.get("start_ms") or 0),
                    "end_ms": int(payload.get("end_ms") or 0),
                    "received_at_ms": int(event.get("at_ms") or payload.get("end_ms") or 0),
                    "confidence": payload.get("confidence"),
                    "confidence_source": str(
                        payload.get("confidence_source")
                        or (
                            ASR_CONFIDENCE_SOURCE_LEGACY_UNATTRIBUTED
                            if payload.get("confidence") is not None
                            else ASR_CONFIDENCE_SOURCE_REALTIME_UNAVAILABLE
                        )
                    ),
                    **(
                        {"source_segment_id": str(payload["source_segment_id"])}
                        if payload.get("source_segment_id")
                        else {}
                    ),
                    **({"source_snapshot_text": raw_source_text} if raw_source_text else {}),
                    **({"source_track": str(payload["source_track"])} if payload.get("source_track") else {}),
                    **(
                        {"capture_epoch": int(payload["capture_epoch"])}
                        if payload.get("capture_epoch") is not None
                        else {}
                    ),
                    **({"projection_reconciled": True} if payload.get("projection_reconciled") else {}),
                    **(
                        {"authoritative": bool(payload["authoritative"])}
                        if "authoritative" in payload
                        else {}
                    ),
                    **({"final_source": str(payload["final_source"])} if payload.get("final_source") else {}),
                    **(
                        {"refinement_status": str(payload["refinement_status"])}
                        if payload.get("refinement_status")
                        else {}
                    ),
                    **(
                        {"refinement_reason": str(payload["refinement_reason"])}
                        if payload.get("refinement_reason")
                        else {}
                    ),
                }
            )
        return restored

    restored_transcript_events = _persisted_raw_transcript_events()
    accumulated_finals: list[dict[str, Any]] = [
        event for event in restored_transcript_events if event["event_type"] == "final"
    ]
    latest_partials: dict[str, dict[str, Any]] = {
        str(event["segment_id"]): event for event in restored_transcript_events if event["event_type"] == "partial"
    }
    # Keep provenance outside the event payload.  A partial loaded from the
    # previous connection may justify a protocol-tail recovery on a silent
    # reconnect; a partial produced by this connection must not.
    persisted_partial_segment_ids = set(latest_partials)
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
        nonlocal audio_active_streak_ms, audio_active_reported, chunk_ms
        chunk_ms = _float32_pcm_duration_ms(payload)
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
    funasr_boundary_sequence = 0
    pending_funasr_boundary_id: str | None = None
    pending_funasr_boundary_started_at: float | None = None
    pending_funasr_boundary_attempts = 0
    pending_funasr_boundary_budget_exhausted = False
    # Raw PCM for the current VAD segment. This is the only input accepted by
    # the post-endpoint refiner; online partial text is never used as audio
    # evidence and is never concatenated into a final.
    segment_pcm_buffer = bytearray()
    segment_has_speech = False
    segment_voiced_ms = 0.0
    segment_speech_start_ms: int | None = None
    segment_speech_start_offset_bytes: int | None = None
    endpoint_committed_source_text = str(
        accumulated_finals[-1].get("source_snapshot_text") if accumulated_finals else ""
    )
    endpoint_committed_end_ms = max(
        [int(event.get("end_ms") or 0) for event in accumulated_finals],
        default=0,
    )
    stream_elapsed_ms = float(endpoint_committed_end_ms)

    def _reset_segment_audio() -> None:
        nonlocal segment_pcm_buffer, segment_has_speech, segment_voiced_ms
        nonlocal segment_speech_start_ms, segment_speech_start_offset_bytes
        segment_pcm_buffer.clear()
        segment_has_speech = False
        segment_voiced_ms = 0.0
        segment_speech_start_ms = None
        segment_speech_start_offset_bytes = None

    def _current_stream_end_ms() -> int:
        return int(round(stream_elapsed_ms))

    audio_asset: dict[str, Any] | None = dict(existing_record.get("audio") or {}) or None
    audio_writer: RealtimeWavAssetWriter | None = None
    if audio_asset_data_dir is not None:
        source_type = audio_source or "live_asr_stream"

        def _setup_audio_writer() -> RealtimeWavAssetWriter:
            with audio_asset_lock if audio_asset_lock is not None else nullcontext():
                if on_audio_recording_started is not None:
                    on_audio_recording_started(
                        {
                            "session_id": session_id,
                            "source_type": source_type,
                            "sample_rate_hz": 16_000,
                            "track_id": native_track_id,
                            "epoch": int(native_capture_epoch or 0),
                        }
                    )
                return RealtimeWavAssetWriter(
                    data_dir=audio_asset_data_dir,
                    session_id=session_id,
                    source_type=source_type,
                    track_id=native_track_id,
                    epoch=int(native_capture_epoch or 0),
                    on_chunk_committed=on_audio_chunk_committed,
                    authorize_chunk_commit=authorize_audio_chunk_commit,
                )

        try:
            audio_writer = await _run_blocking(_setup_audio_writer)
        except Exception as exc:
            if on_audio_recording_setup_failed is not None:
                try:
                    await _run_blocking(on_audio_recording_setup_failed)
                except Exception as rollback_exc:
                    _log.error(
                        "asr.stream.recording_setup_rollback_failed",
                        session_id=session_id,
                        error_class=type(rollback_exc).__name__,
                        error_origin=_exception_origin(rollback_exc),
                    )
            abort = getattr(recognizer, "abort", None)
            if callable(abort):
                try:
                    await _run_blocking(abort)
                except Exception as abort_exc:
                    _log.warning(
                        "asr.stream.setup_abort_failed",
                        session_id=session_id,
                        error_class=type(abort_exc).__name__,
                    )
            try:
                await websocket.send_text(
                    json.dumps(
                        {
                            "event_type": "provider_error",
                            "error_code": "recording_resume_failed",
                            "message": "录音恢复失败，实时识别已安全停止，请稍后重试。",
                            "provider": provider_metadata["provider"],
                            "provider_mode": provider_metadata["provider_mode"],
                            "recording_saved": bool(audio_asset and audio_asset.get("saved")),
                            "recoverable": True,
                        },
                        ensure_ascii=False,
                    )
                )
            except Exception:
                pass
            _log.error(
                "asr.stream.recording_setup_failed",
                session_id=session_id,
                error_class=type(exc).__name__,
                error_origin=_exception_origin(exc),
            )
            await _shutdown_session_executor()
            try:
                await websocket.close()
            except Exception:
                pass
            return

    if diarization_enabled and (
        not provider_metadata["is_mock"] or diarization_sidecar_factory is not None
    ):
        diarization_runtime = DiarizationRuntime(
            session_id,
            persistence=diarization_persistence,
            sidecar_factory=diarization_sidecar_factory,
        )
        try:
            await _run_blocking(diarization_runtime.start)
        except Exception as exc:
            # Diarization is deliberately fail-open. ASR and recording remain
            # usable even if the local speaker sidecar cannot start.
            _log.warning(
                "asr.stream.diarization_start_failed",
                session_id=session_id,
                error_class=type(exc).__name__,
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
            **({"source_segment_id": str(ev["source_segment_id"])} if ev.get("source_segment_id") else {}),
            **({"source_snapshot_text": str(ev["source_snapshot_text"])} if ev.get("source_snapshot_text") else {}),
            **({"source_track": str(ev["source_track"])} if ev.get("source_track") else {}),
            **({"capture_epoch": int(ev["capture_epoch"])} if ev.get("capture_epoch") is not None else {}),
            **({"projection_reconciled": True} if ev.get("projection_reconciled") else {}),
            **(
                {"authoritative": bool(ev["authoritative"])}
                if "authoritative" in ev
                else {}
            ),
            **({"final_source": str(ev["final_source"])} if ev.get("final_source") else {}),
            **(
                {"refinement_status": str(ev["refinement_status"])}
                if ev.get("refinement_status")
                else {}
            ),
            **(
                {"refinement_reason": str(ev["refinement_reason"])}
                if ev.get("refinement_reason")
                else {}
            ),
            "start_ms": int(ev.get("start_ms") if ev.get("start_ms") is not None else idx * chunk_ms),
            "end_ms": int(ev.get("end_ms") if ev.get("end_ms") is not None else (idx + 1) * chunk_ms),
            "received_at_ms": int(
                ev.get("received_at_ms")
                if ev.get("received_at_ms") is not None
                else ev.get("end_ms")
                if ev.get("end_ms") is not None
                else idx * chunk_ms + chunk_ms
            ),
            **_asr_confidence_metadata(ev),
        }

    def _authoritative_final_has_valid_span(ev: dict[str, Any], idx: int) -> bool:
        if ev.get("authoritative") is False:
            return True
        normalized_event = _to_streaming_final(ev, idx)
        start_ms = int(normalized_event["start_ms"])
        end_ms = int(normalized_event["end_ms"])
        if end_ms > start_ms:
            return True
        _log.warning(
            "asr.stream.authoritative_final_rejected_invalid_span",
            session_id=session_id,
            segment_id=str(ev.get("segment_id") or ""),
            start_ms=start_ms,
            end_ms=end_ms,
        )
        return False

    def _append_accumulated_final(ev: dict[str, Any], idx: int) -> bool:
        if ev.get("authoritative") is False:
            return False
        text = str(ev.get("text") or "").strip()
        if not text:
            return False
        # Every authoritative final must describe real captured audio.  This
        # is the central guard for worker/protocol paths that bypass the VAD
        # endpoint helper; never persist a zero-duration (or reversed) final.
        normalized_event = _to_streaming_final(ev, idx)
        if not _authoritative_final_has_valid_span(ev, idx):
            return False
        segment_id = str(ev.get("segment_id") or "").strip()
        source_segment_id = str(ev.get("source_segment_id") or "").strip()
        source_snapshot = str(ev.get("source_snapshot_text") or "").strip()
        capture_epoch = max(0, int(ev.get("capture_epoch") or 0))
        if is_funasr_realtime:
            for partial_segment_id, partial in list(latest_partials.items()):
                partial_source_segment_id = str(partial.get("source_segment_id") or "").strip()
                partial_source_snapshot = str(
                    partial.get("source_snapshot_text") or partial.get("text") or ""
                ).strip()
                if (
                    (segment_id and partial_segment_id == segment_id)
                    or (
                        source_segment_id
                        and partial_source_segment_id
                        and source_segment_id == partial_source_segment_id
                    )
                    or (
                        source_snapshot
                        and partial_source_snapshot
                        and source_snapshot == partial_source_snapshot
                    )
                ):
                    latest_partials.pop(partial_segment_id, None)
        for existing_index, existing in enumerate(accumulated_finals):
            existing_source = str(existing.get("source_snapshot_text") or "").strip()
            same_epoch = max(0, int(existing.get("capture_epoch") or 0)) == capture_epoch
            if same_epoch and (
                (source_snapshot and existing_source == source_snapshot)
                or (not source_snapshot and str(existing.get("text") or "").strip() == text)
            ):
                accumulated_finals[existing_index] = normalized_event
                return False
        if accumulated_finals:
            previous_text = str(accumulated_finals[-1].get("text") or "").strip()
            previous_segment_id = str(accumulated_finals[-1].get("segment_id") or "")
            previous_capture_epoch = max(
                0,
                int(accumulated_finals[-1].get("capture_epoch") or 0),
            )
            segment_id = str(ev.get("segment_id") or "")
            if text == previous_text and capture_epoch == previous_capture_epoch:
                return False
            if segment_id and segment_id == previous_segment_id:
                accumulated_finals[-1] = normalized_event
                return False
        accumulated_finals.append(normalized_event)
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
            return source[len(previous) :].strip(), False
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
        persisted_partial_segment_ids.discard(segment_id)
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
            **(
                {"authoritative": bool(ev["authoritative"])}
                if "authoritative" in ev
                else {}
            ),
            **({"final_source": str(ev["final_source"])} if ev.get("final_source") else {}),
            **(
                {"refinement_status": str(ev["refinement_status"])}
                if ev.get("refinement_status")
                else {}
            ),
            **(
                {"refinement_reason": str(ev["refinement_reason"])}
                if ev.get("refinement_reason")
                else {}
            ),
            **(
                {"authoritative": bool(ev["authoritative"])}
                if "authoritative" in ev
                else {}
            ),
            **({"final_source": str(ev["final_source"])} if ev.get("final_source") else {}),
            **(
                {"refinement_status": str(ev["refinement_status"])}
                if ev.get("refinement_status")
                else {}
            ),
            **(
                {"refinement_reason": str(ev["refinement_reason"])}
                if ev.get("refinement_reason")
                else {}
            ),
            "start_ms": int(ev.get("start_ms") or 0),
            "end_ms": int(ev.get("end_ms") or _current_stream_end_ms()),
            "received_at_ms": int(
                ev.get("received_at_ms")
                if ev.get("received_at_ms") is not None
                else ev.get("end_ms")
                if ev.get("end_ms") is not None
                else _current_stream_end_ms()
            ),
            **_asr_confidence_metadata(ev),
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
        events, _dropped = _bound_streaming_projection_events([*latest_partials.values(), *accumulated_finals])
        return events

    def _track_endpoint_candidate(
        source_ev: dict[str, Any],
        display_ev: dict[str, Any],
    ) -> dict[str, Any] | None:
        nonlocal endpoint_candidate
        if source_ev.get("event_type") != "partial":
            return None
        semantics = str(
            source_ev.get("partial_semantics")
            or display_ev.get("partial_semantics")
            or ""
        ).strip().casefold()
        source_text = str(source_ev.get("text") or "").strip()
        text = str(display_ev.get("text") or "").strip()
        if semantics == "incremental_chunk":
            # Only the worker's explicit protocol contract opts into joining
            # chunks.  Cumulative snapshots and terminal snapshots continue
            # to replace the candidate as before.
            previous = (
                str(endpoint_candidate.get("source_text") or "").strip()
                if str(endpoint_candidate.get("partial_semantics") or "").casefold()
                == "incremental_chunk"
                else ""
            )
            merged_source = _merge_incremental_chunk_text(previous, source_text or text)
            if not merged_source:
                return None
            segment_id = str(
                endpoint_candidate.get("segment_id")
                or display_ev.get("segment_id")
                or _next_endpoint_segment_id()
            )
            merged_event = {
                **display_ev,
                "event_type": "partial",
                "segment_id": segment_id,
                "text": merged_source,
                "source_snapshot_text": merged_source,
                "normalized_text": _stream_normalized_text(merged_source),
                # This is a segment-local hypothesis, not a cumulative
                # transcript snapshot.  Canonical projection must not trim
                # already committed segments against it.
                "projection_reconciled": False,
                "partial_semantics": "incremental_chunk",
            }
            endpoint_candidate = {
                "text": merged_source,
                "source_text": merged_source,
                "segment_id": segment_id,
                "start_ms": int(
                    endpoint_candidate.get("start_ms")
                    or display_ev.get("start_ms")
                    or endpoint_committed_end_ms
                ),
                "end_ms": int(display_ev.get("end_ms") or _current_stream_end_ms()),
                **_asr_confidence_metadata(display_ev),
                **(
                    {"source_segment_id": display_ev["source_segment_id"]}
                    if display_ev.get("source_segment_id")
                    else {}
                ),
                "projection_reconciled": False,
                "partial_semantics": "incremental_chunk",
            }
            return merged_event
        if (
            semantics == "terminal_snapshot"
            and str(endpoint_candidate.get("partial_semantics") or "").casefold()
            == "incremental_chunk"
            and endpoint_candidate.get("text")
        ):
            # The worker's terminal event repeats only its latest decoded
            # chunk.  It closes the stream but must not replace the complete
            # segment-local accumulator built from preceding chunks.
            accumulated_text = str(endpoint_candidate["text"])
            return {
                **display_ev,
                "segment_id": str(
                    endpoint_candidate.get("segment_id")
                    or display_ev.get("segment_id")
                    or _next_endpoint_segment_id()
                ),
                "text": accumulated_text,
                "source_snapshot_text": accumulated_text,
                "normalized_text": _stream_normalized_text(accumulated_text),
                "projection_reconciled": False,
                "partial_semantics": "terminal_snapshot",
            }
        if len(text) < VAD_MIN_FINAL_TEXT_CHARS:
            return None
        endpoint_candidate = {
            "text": text,
            "source_text": source_text or text,
            "segment_id": (
                str(display_ev.get("segment_id") or _next_endpoint_segment_id())
                if is_funasr_realtime
                else _next_endpoint_segment_id()
            ),
            "start_ms": int(display_ev.get("start_ms") or endpoint_committed_end_ms),
            "end_ms": int(display_ev.get("end_ms") or _current_stream_end_ms()),
            **_asr_confidence_metadata(display_ev),
            **({"source_segment_id": display_ev["source_segment_id"]} if display_ev.get("source_segment_id") else {}),
            "projection_reconciled": bool(display_ev.get("projection_reconciled")),
            **({"partial_semantics": semantics} if semantics else {}),
        }
        return display_ev

    def _clear_endpoint_candidate() -> None:
        nonlocal endpoint_candidate, endpoint_silence_ms
        endpoint_candidate = {}
        endpoint_silence_ms = 0

    def _has_meaningful_endpoint_tail(
        candidate: Mapping[str, Any] | None = None,
        *,
        require_persisted_partial: bool = False,
    ) -> bool:
        """Accept synthetic/worker terminal text only when it has a real span."""

        item = candidate if candidate is not None else endpoint_candidate
        if require_persisted_partial and not item.get("restored_from_persisted_partial"):
            return False
        text = str(item.get("text") or "").strip()
        start_value = item.get("start_ms")
        end_value = item.get("end_ms")
        start_ms = int(start_value if start_value is not None else endpoint_committed_end_ms)
        end_ms = int(end_value if end_value is not None else _current_stream_end_ms())
        return bool(text) and end_ms - start_ms >= 300

    async def _maybe_vad_endpoint_event(
        *,
        force_boundary: bool = False,
    ) -> dict[str, Any] | None:
        nonlocal endpoint_final_count, endpoint_committed_source_text, endpoint_committed_end_ms
        # The online worker can emit a delayed partial after the preceding
        # authoritative final. Do not refine that stale text over a buffer
        # containing only room noise or silence.
        if not segment_has_speech:
            return None
        text = str(endpoint_candidate.get("text") or "").strip()
        current_end_ms = _current_stream_end_ms()
        segment_start_ms = int(
            segment_speech_start_ms
            if segment_speech_start_ms is not None
            else endpoint_committed_end_ms
        )
        candidate_duration_ms = max(0, current_end_ms - segment_start_ms)
        reached_natural_endpoint = endpoint_silence_ms >= VAD_ENDPOINT_SILENCE_MS
        reached_soft_bounded_endpoint = (
            candidate_duration_ms >= VAD_MAX_SEGMENT_MS
            and endpoint_silence_ms > 0
        )
        reached_hard_bounded_endpoint = (
            candidate_duration_ms
            >= VAD_MAX_SEGMENT_MS + VAD_MAX_SEGMENT_GRACE_MS
        )
        if not force_boundary and not (
            reached_natural_endpoint
            or reached_soft_bounded_endpoint
            or reached_hard_bounded_endpoint
        ):
            return None
        # A delayed or overloaded online preview must not block sentence
        # closure. Raw PCM plus VAD is sufficient input for the authoritative
        # offline refiner; online text is only a fallback when it exists.
        end_ms = max(
            segment_start_ms,
            current_end_ms - endpoint_silence_ms
            if not force_boundary
            and (reached_natural_endpoint or reached_soft_bounded_endpoint)
            else current_end_ms,
        )
        if end_ms <= segment_start_ms:
            # Do not persist zero-duration finals produced by a delayed worker
            # event or a noise-only VAD boundary.
            return None
        segment_id = str(endpoint_candidate.get("segment_id") or _next_endpoint_segment_id())
        candidate_source_text = str(endpoint_candidate.get("source_text") or text).strip()
        speech_offset = max(0, int(segment_speech_start_offset_bytes or 0))
        refinement = await _run_blocking(
            refine_pcm_f32,
            bytes(segment_pcm_buffer[speech_offset:]),
        )
        refined_text = refinement.text.strip()
        rejected_short_text = bool(
            refinement.authoritative
            and refined_text
            and not _is_meaningful_authoritative_final(refined_text)
        )
        online_policy_final = _uses_online_final_resource_policy(refinement, text)
        authoritative = (
            refinement.authoritative and not rejected_short_text
        ) or online_policy_final
        refinement_status = "rejected_short_text" if rejected_short_text else refinement.status
        refinement_reason = "offline_refinement_text_too_short" if rejected_short_text else refinement.reason
        if authoritative:
            endpoint_final_count += 1
            latest_partials.pop(segment_id, None)
        if online_policy_final:
            provider_metadata["degradation_reasons"].append(
                ONLINE_ONLY_REFINEMENT_REASON
            )
        elif not authoritative:
            provider_metadata["degradation_reasons"].append(
                _refinement_degradation_reason(
                    refinement,
                    rejected_short_text=rejected_short_text,
                )
            )
        final_text = (refined_text or text) if authoritative else text
        if not final_text:
            return None
        if authoritative:
            # A failed/short refinement must not advance the committed clock;
            # otherwise the following voiced segment inherits a stale boundary.
            endpoint_committed_source_text = candidate_source_text
            endpoint_committed_end_ms = end_ms
        return {
            "event_type": "final" if authoritative else "partial",
            "segment_id": segment_id,
            "text": final_text,
            "start_ms": segment_start_ms,
            "end_ms": end_ms,
            "received_at_ms": end_ms,
            **_asr_confidence_metadata(
                endpoint_candidate,
                offline_refinement=authoritative and not online_policy_final,
            ),
            "authoritative": authoritative,
            "final_source": (
                "local_realtime_online_final"
                if online_policy_final
                else "local_offline_refinement"
                if authoritative
                else "online_terminal_partial"
            ),
            "refinement_status": refinement_status,
            **({"refinement_model_id": refinement.model_id} if refinement.model_id else {}),
            **({"refinement_reason": refinement_reason} if refinement_reason else {}),
            "endpoint_source": (
                "server_vad_online_final_resource_policy"
                if online_policy_final
                else "server_vad_offline_refined"
                if authoritative
                else "server_vad_refinement_unavailable"
            ),
            **({"endpoint_trigger": "client_flush"} if force_boundary else {}),
            **(
                {"partial_semantics": "terminal_snapshot"}
                if not authoritative
                else {}
            ),
            "source_snapshot_text": candidate_source_text,
            **(
                {"source_segment_id": endpoint_candidate["source_segment_id"]}
                if endpoint_candidate.get("source_segment_id")
                else {}
            ),
            "normalized_text": _stream_normalized_text(final_text),
            # An authoritative offline refinement is a complete sentence. It
            # must not inherit the online cumulative-partial projection
            # boundary, otherwise canonical projection can replace the full
            # refined text with a short partial prefix.
            "projection_reconciled": bool(
                endpoint_candidate.get("projection_reconciled")
                and not authoritative
            ),
        }

    def _backfill_interrupted_tail_once() -> bool:
        nonlocal endpoint_final_count, endpoint_committed_source_text, endpoint_committed_end_ms
        nonlocal interrupted_backfill_state, segment_pcm_buffer, segment_has_speech
        if is_funasr_realtime and pending_funasr_boundary_id is not None:
            # A transport or finalizer failure must not turn the disconnect
            # recovery path into a way around the resident worker's causal ACK.
            # The sealed recording remains available for a later audited retry.
            reason = "funasr_boundary_unacknowledged"
            provider_metadata["degradation_reasons"].append(reason)
            interrupted_backfill_state = {
                **interrupted_backfill_state,
                "schema_version": "transcript_backfill.v1",
                "status": "blocked",
                "error_class": reason,
                "boundary_id": pending_funasr_boundary_id,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
            return False
        protocol_tail = _has_meaningful_endpoint_tail(
            require_persisted_partial=True,
        )
        if not is_funasr_realtime or not segment_pcm_buffer:
            return False
        if segment_has_speech:
            if segment_voiced_ms < VAD_INTERRUPTED_BACKFILL_MIN_VOICED_MS:
                return False
        elif not protocol_tail:
            return False
        segment_start_ms = int(
            segment_speech_start_ms
            if segment_speech_start_ms is not None
            else endpoint_candidate.get("start_ms") or endpoint_committed_end_ms
        )
        segment_end_ms = max(
            segment_start_ms,
            _current_stream_end_ms() - endpoint_silence_ms,
        )
        interrupted_backfill_state = {
            "schema_version": "transcript_backfill.v1",
            "status": "running",
            "capture_epoch": max(0, int(native_capture_epoch or 0)),
            "start_ms": segment_start_ms,
            "end_ms": segment_end_ms,
            "attempt": int(interrupted_backfill_state.get("attempt") or 0) + 1,
            "updated_at_ms": time.time_ns() // 1_000_000,
        }
        _upsert_live_session(
            _current_session_streaming_events(),
            [*_current_degradation_reasons(), "stream_interrupted"],
        )
        speech_offset = max(0, int(segment_speech_start_offset_bytes or 0))
        refinement = refine_pcm_f32(bytes(segment_pcm_buffer[speech_offset:]))
        refined_text = refinement.text.strip()
        rejected_short_text = bool(
            refinement.authoritative
            and refined_text
            and not _is_meaningful_authoritative_final(refined_text)
        )
        if not refinement.authoritative or rejected_short_text or not refined_text:
            degradation_reason = _refinement_degradation_reason(
                refinement,
                rejected_short_text=rejected_short_text,
            )
            interrupted_backfill_state = {
                **interrupted_backfill_state,
                "status": "failed",
                "error_class": degradation_reason,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
            provider_metadata["degradation_reasons"].append(degradation_reason)
            _upsert_live_session(
                _current_session_streaming_events(),
                [*_current_degradation_reasons(), "stream_interrupted"],
            )
            return False

        segment_id = _next_endpoint_segment_id()
        endpoint_final_count += 1
        endpoint_committed_source_text = str(
            endpoint_candidate.get("source_text") or endpoint_candidate.get("text") or ""
        ).strip()
        endpoint_committed_end_ms = segment_end_ms
        backfilled_event = _normalize_capture_event(
            {
                "event_type": "final",
                "segment_id": segment_id,
                "text": refined_text,
                "normalized_text": _stream_normalized_text(refined_text),
                "start_ms": segment_start_ms,
                "end_ms": segment_end_ms,
                "received_at_ms": segment_end_ms,
                **_asr_confidence_metadata(
                    endpoint_candidate,
                    offline_refinement=True,
                ),
                "authoritative": True,
                "final_source": "local_offline_disconnect_backfill",
                "refinement_status": "backfilled",
                **({"refinement_model_id": refinement.model_id} if refinement.model_id else {}),
                "endpoint_source": "stream_interrupted_offline_refined",
                "source_snapshot_text": endpoint_committed_source_text,
                **(
                    {"source_segment_id": endpoint_candidate["source_segment_id"]}
                    if endpoint_candidate.get("source_segment_id")
                    else {}
                ),
                "projection_reconciled": False,
            }
        )
        backfilled_event.update(_native_pcm_event_identity(last_native_frame))
        latest_partials.clear()
        _reset_segment_audio()
        appended = _append_accumulated_final(
            backfilled_event,
            getattr(recognizer, "_seq", len(accumulated_finals) + 1),
        )
        interrupted_backfill_state = {
            **interrupted_backfill_state,
            "status": "completed",
            "segment_id": str(backfilled_event["segment_id"]),
            "final_source": str(backfilled_event["final_source"]),
            "updated_at_ms": time.time_ns() // 1_000_000,
        }
        if not appended:
            _upsert_live_session(
                _current_session_streaming_events(),
                [*_current_degradation_reasons(), "stream_interrupted"],
            )
            return True
        _upsert_and_commit_final(
            _current_session_streaming_events(),
            [*_current_degradation_reasons(), "stream_interrupted"],
            backfilled_event,
        )
        _log.info(
            "asr.stream.interrupted_tail_backfilled",
            session_id=session_id,
            segment_id=backfilled_event["segment_id"],
            start_ms=segment_start_ms,
            end_ms=segment_end_ms,
        )
        return True

    def _backfill_interrupted_tail() -> bool:
        nonlocal interrupted_backfill_state
        try:
            return _backfill_interrupted_tail_once()
        except Exception as exc:
            interrupted_backfill_state = {
                **interrupted_backfill_state,
                "schema_version": "transcript_backfill.v1",
                "status": "failed",
                "error_class": type(exc).__name__,
                "updated_at_ms": time.time_ns() // 1_000_000,
            }
            provider_metadata["degradation_reasons"].append("offline_refinement_unavailable")
            try:
                _upsert_live_session(
                    _current_session_streaming_events(),
                    [*_current_degradation_reasons(), "stream_interrupted"],
                )
            except Exception:
                pass
            _log.warning(
                "asr.stream.interrupted_tail_backfill_failed",
                session_id=session_id,
                error_class=type(exc).__name__,
            )
            raise

    def _upsert_live_session(
        streaming_events: list[dict[str, Any]], degradation_reasons: list[str]
    ) -> list[dict[str, Any]]:
        if asr_live_repo is None:
            return []
        bounded_streaming_events, dropped_streaming_events = _bound_streaming_projection_events(streaming_events)
        projection_streaming_events = _source_qualified_streaming_events(
            bounded_streaming_events,
            audio_source=audio_source,
        )
        semantic_quality = _semantic_quality_for_streaming_events(projection_streaming_events)
        effective_degradation_reasons = list(degradation_reasons)
        if semantic_quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER:
            effective_degradation_reasons.append(ASR_SEMANTIC_QUALITY_BLOCKER)
        live_events = build_asr_live_events(
            session_id=session_id,
            provider=provider_metadata["provider"],
            streaming_events=projection_streaming_events,
            is_mock=provider_metadata["is_mock"],
        )
        source_segment_ids = {
            str(event.get("segment_id") or ""): str(event["source_segment_id"])
            for event in projection_streaming_events
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
            **(
                {"asr_runtime_profile": dict(provider_metadata["asr_runtime_profile"])}
                if provider_metadata.get("asr_runtime_profile")
                else {}
            ),
            "audio_source": audio_source,
            "input_source": audio_source,
            **({"audio": audio_asset} if audio_asset is not None else {}),
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "settings_snapshot": {
                "asr": {"l3_normalize_enabled": bool(l3_normalize_enabled)},
                "scope": "websocket_connection_start",
            },
            **(
                {"transcript_backfill": dict(interrupted_backfill_state)}
                if interrupted_backfill_state
                else {}
            ),
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
                    "degradation_reasons": _dedupe(
                        [
                            *retained_degradation_reasons,
                            *list(base_record.get("degradation_reasons") or []),
                        ]
                    ),
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
    last_native_frame: NativePcmFrame | None = None

    def _decode_stream_message(message: dict[str, Any]) -> dict[str, Any]:
        nonlocal last_native_frame
        pcm_envelope = message.get("bytes")
        if pcm_envelope is None or message.get("_native_pcm_decoded"):
            return message
        pcm_payload, native_frame = _decode_native_pcm_payload(
            pcm_envelope,
            decoder=native_decoder,
        )
        if native_frame is not None:
            last_native_frame = native_frame
        return {
            **message,
            "bytes": pcm_payload,
            "_native_pcm_decoded": True,
            "_native_pcm_frame": native_frame,
        }

    def _current_degradation_reasons() -> list[str]:
        return _dedupe(
            [
                *list(provider_metadata["degradation_reasons"]),
                *readiness_degradation_reasons,
            ]
        )

    def _clear_resolved_refinement_degradation() -> None:
        if not accumulated_finals:
            return
        unresolved_refinement = any(
            str(partial.get("text") or "").strip()
            and str(partial.get("refinement_status") or "") != "refined"
            and (
                partial.get("final_source") == "online_terminal_partial"
                or bool(partial.get("refinement_status"))
            )
            for partial in latest_partials.values()
        )
        if unresolved_refinement:
            return
        provider_metadata["degradation_reasons"] = [
            reason
            for reason in provider_metadata["degradation_reasons"]
            if reason != "offline_refinement_unavailable"
        ]

    def _close_audio_writer(*, interrupted: bool = False) -> None:
        nonlocal audio_asset, audio_writer
        if audio_writer is None:
            return
        try:
            if on_audio_recording_sealed is None:
                audio_asset = audio_writer.close()
            else:
                audio_asset = audio_writer.seal()
                on_audio_recording_sealed(
                    {
                        **audio_asset,
                        "interrupted": interrupted,
                    }
                )
        except Exception:
            audio_writer.discard()
            raise
        finally:
            audio_writer = None

    def _record_audio_payload(
        payload: bytes,
        *,
        native_frame: NativePcmFrame | None,
    ) -> None:
        if audio_writer is not None:
            audio_writer.write_float32_pcm(
                payload,
                source_frame=_native_pcm_event_identity(native_frame) or None,
            )

    def _record_and_recognize_audio_payload(
        payload: bytes,
        *,
        audio_already_recorded: bool,
        native_frame: NativePcmFrame | None,
    ) -> list[dict[str, Any]]:
        if audio_writer is not None and not audio_already_recorded:
            audio_writer.write_float32_pcm(
                payload,
                source_frame=_native_pcm_event_identity(native_frame) or None,
            )
        if diarization_runtime is not None:
            diarization_runtime.submit_pcm(payload)
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
                [
                    {
                        "event_type": "end_of_stream",
                        "end_ms": end_ms,
                        "received_at_ms": end_ms,
                    }
                ],
                reasons,
            )
        except Exception as exc:
            _log.warning(
                "asr.stream.readiness_terminal_persist_failed",
                session_id=session_id,
                error=str(exc),
            )

    async def _finish_diarization() -> None:
        if diarization_runtime is None:
            return
        try:
            await _run_blocking(diarization_runtime.finish)
        except Exception as exc:
            _log.warning(
                "asr.stream.diarization_finish_failed",
                session_id=session_id,
                error_class=type(exc).__name__,
            )

    async def _abort_diarization() -> None:
        if diarization_runtime is None:
            return
        try:
            await _run_blocking(diarization_runtime.abort)
        except Exception as exc:
            _log.warning(
                "asr.stream.diarization_abort_failed",
                session_id=session_id,
                error_class=type(exc).__name__,
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
        await _abort_diarization()
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
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": error_code,
                    "message": message,
                    "provider": provider_metadata["provider"],
                    "provider_mode": provider_metadata["provider_mode"],
                    "degradation_reasons": _dedupe(reasons),
                    "recording_saved": bool(audio_asset and audio_asset.get("saved")),
                },
                ensure_ascii=False,
            )
        )
        await websocket.close()
        return False

    async def _finish_invalid_audio_payload(exc: Float32PcmPayloadError) -> bool:
        reasons = _dedupe([*_current_degradation_reasons(), exc.code])
        await _abort_diarization()
        try:
            await _run_blocking(_close_audio_writer, interrupted=True)
        except Exception as persist_exc:
            _log.warning(
                "asr.stream.invalid_audio_payload_persist_failed",
                session_id=session_id,
                error=str(persist_exc),
            )
        if asr_live_repo is not None:
            try:
                await _run_blocking(
                    _upsert_live_session,
                    _current_session_streaming_events(),
                    reasons,
                )
            except Exception as persist_exc:
                _log.warning(
                    "asr.stream.invalid_audio_payload_state_failed",
                    session_id=session_id,
                    error=str(persist_exc),
                )
        _log.warning(
            "asr.stream.invalid_audio_payload",
            session_id=session_id,
            error_code=exc.code,
        )
        # Complete all ordered persistence/sidecar work before exposing the
        # terminal frame. Otherwise a client that closes immediately after
        # reading the error can cancel the ASGI task during executor teardown.
        await _shutdown_session_executor()
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "provider_error",
                    "error_code": exc.code,
                    "message": exc.user_message,
                    "provider": provider_metadata["provider"],
                    "provider_mode": provider_metadata["provider_mode"],
                    "degradation_reasons": reasons,
                    "recording_saved": bool(audio_asset and audio_asset.get("saved")),
                    "recoverable": True,
                },
                ensure_ascii=False,
            )
        )
        await websocket.close()
        return False

    async def _prepare_before_asr_ready() -> bool:
        nonlocal pending_asr_audio_dropped
        if not readiness_event_required:
            return True
        await websocket.send_text(
            json.dumps(
                {
                    "event_type": "asr_starting",
                    "provider": provider_metadata["provider"],
                    "ready": False,
                    "message": "正在准备实时识别，请稍候。",
                },
                ensure_ascii=False,
            )
        )
        if not readiness_is_required:
            await websocket.send_text(
                json.dumps(
                    {
                        "event_type": "asr_ready",
                        "provider": provider_metadata["provider"],
                        "ready": True,
                        "ready_latency_ms": 0.0,
                        "message": "实时识别已就绪。",
                    },
                    ensure_ascii=False,
                )
            )
            _mark_real_asr_ready_healthy()
            return True

        ready_started_at = time.monotonic()
        readiness_task = asyncio.create_task(asyncio.to_thread(readiness_waiter, ASR_READY_TIMEOUT_S))
        while True:
            receive_task = asyncio.create_task(websocket.receive())
            done, _pending = await asyncio.wait(
                {readiness_task, receive_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receive_task in done:
                try:
                    message = _decode_stream_message(receive_task.result())
                except NativePcmProtocolError as exc:
                    await _cancel_readiness_task(readiness_task)
                    return await _finish_invalid_audio_payload(exc)
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
                    try:
                        audio_activity = _observe_audio_activity(pcm_payload)
                    except Float32PcmPayloadError as exc:
                        return await _finish_invalid_audio_payload(exc)
                    if audio_activity is not None:
                        audio_activity.update(
                            _native_pcm_event_identity(message.get("_native_pcm_frame"))
                        )
                    if audio_activity is not None and on_audio_active is not None:
                        await _run_blocking(on_audio_active, audio_activity)
                    await _run_blocking(
                        _record_audio_payload,
                        pcm_payload,
                        native_frame=message.get("_native_pcm_frame"),
                    )
                    if len(pending_messages) < pending_message_limit:
                        pending_messages.append(
                            {
                                "bytes": pcm_payload,
                                "_audio_recorded": True,
                                "_native_pcm_decoded": True,
                                "_native_pcm_frame": message.get("_native_pcm_frame"),
                            }
                        )
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
                elif message.get("text") == STREAM_FLUSH_COMMAND:
                    # Pause is a non-terminal utterance boundary. Preserve it
                    # behind any PCM buffered during model warm-up so the same
                    # authoritative refinement path runs once readiness wins.
                    pending_messages.append({"text": STREAM_FLUSH_COMMAND})
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
            await websocket.send_text(
                json.dumps(
                    {
                        "event_type": "asr_ready",
                        "provider": provider_metadata["provider"],
                        "ready": True,
                        "ready_latency_ms": readiness_latency_ms,
                        "message": "实时识别已就绪。",
                    },
                    ensure_ascii=False,
                )
            )
            _mark_real_asr_ready_healthy()
            return True

    async def _next_stream_message() -> dict[str, Any]:
        if pending_messages:
            return pending_messages.popleft()
        return _decode_stream_message(await websocket.receive())

    def _funasr_boundary_is_imminent() -> bool:
        if not segment_has_speech:
            return False
        segment_start_ms = int(
            segment_speech_start_ms
            if segment_speech_start_ms is not None
            else endpoint_committed_end_ms
        )
        candidate_duration_ms = max(0, _current_stream_end_ms() - segment_start_ms)
        return bool(
            endpoint_silence_ms >= VAD_ENDPOINT_SILENCE_MS
            or (
                candidate_duration_ms >= VAD_MAX_SEGMENT_MS
                and endpoint_silence_ms > 0
            )
            or candidate_duration_ms
            >= VAD_MAX_SEGMENT_MS + VAD_MAX_SEGMENT_GRACE_MS
        )

    async def _process_recognizer_events(
        events: list[dict[str, Any]],
        *,
        native_frame: NativePcmFrame | None,
    ) -> None:
        nonlocal saw_empty_final
        for ev in events:
            source_ev = _normalize_capture_event(ev)
            if is_funasr_realtime:
                source_ev.update(_asr_confidence_metadata(source_ev))
            source_ev.update(_native_pcm_event_identity(native_frame))
            ev = (
                _to_endpoint_partial(source_ev)
                if source_ev.get("event_type") == "partial"
                and is_funasr_realtime
                else source_ev
            )
            if (
                ev.get("event_type") == "final"
                and not _authoritative_final_has_valid_span(
                    ev,
                    getattr(recognizer, "_seq", len(accumulated_finals) + 1),
                )
            ):
                # Do not expose an invalid worker final to the browser when
                # persistence rejects it below.
                continue
            outgoing_events = [ev]
            if ev.get("event_type") == "final":
                if not ev.get("text"):
                    # Empty terminal snapshots are protocol noise, not
                    # user-visible transcript evidence.
                    saw_empty_final = True
                    outgoing_events = []
                elif is_funasr_realtime:
                    # FunASR's terminal event is a model snapshot, not a
                    # committed product final. Keep it visible only as a
                    # replaceable partial until VAD refinement.
                    ev = {
                        **ev,
                        "event_type": "partial",
                        "authoritative": False,
                        "partial_semantics": "terminal_snapshot",
                        "final_source": "online_terminal_snapshot",
                    }
                    outgoing_events = [ev]
                else:
                    appended_final = _append_accumulated_final(
                        ev,
                        getattr(recognizer, "_seq", len(accumulated_finals) + 1),
                    )
                    if appended_final:
                        live_events = await _run_blocking(
                            _upsert_and_commit_final,
                            _current_session_streaming_events(),
                            _current_degradation_reasons(),
                            ev,
                        )
                        outgoing_events.extend(
                            _unsent_realtime_candidate_events(live_events)
                        )
                    else:
                        await _run_blocking(
                            _upsert_live_session,
                            _current_session_streaming_events(),
                            _current_degradation_reasons(),
                        )
                        # A duplicate or invalid-span final was rejected by the
                        # durable append gate. Do not expose a UI-only final.
                        outgoing_events = []
                    _clear_endpoint_candidate()
            if ev.get("event_type") != "final":
                tracked_event = _track_endpoint_candidate(
                    (
                        {**source_ev, "event_type": "partial"}
                        if is_funasr_realtime
                        else source_ev
                    ),
                    ev,
                )
                if tracked_event is not None:
                    ev = tracked_event
                    outgoing_events = [ev]
                partial_hint = build_partial_hint_event(ev)
                if partial_hint:
                    partial_hint_key = str(
                        partial_hint.get("payload", {}).get("dedupe_key")
                        or partial_hint["id"]
                    )
                    if partial_hint_key not in sent_partial_hint_keys:
                        sent_partial_hint_keys.add(partial_hint_key)
                        outgoing_events.append(partial_hint)
                if _remember_live_partial(ev):
                    live_events = await _run_blocking(
                        _upsert_live_session,
                        _current_session_streaming_events(),
                        _current_degradation_reasons(),
                    )
                    outgoing_events.extend(
                        _unsent_realtime_candidate_events(live_events)
                    )
            for outgoing_event in outgoing_events:
                await websocket.send_text(
                    json.dumps(outgoing_event, ensure_ascii=False)
                )

    async def _synchronize_funasr_boundary(
        *,
        force_boundary: bool,
    ) -> dict[str, Any]:
        nonlocal funasr_boundary_sequence, pending_funasr_boundary_id
        nonlocal pending_funasr_boundary_started_at, pending_funasr_boundary_attempts
        nonlocal pending_funasr_boundary_budget_exhausted
        if not is_funasr_realtime or not segment_has_speech:
            return {
                "acknowledged": True,
                "status": "not_required",
                "boundary_id": None,
            }
        if not force_boundary and not _funasr_boundary_is_imminent():
            return {
                "acknowledged": True,
                "status": "not_due",
                "boundary_id": None,
            }
        flush_utterance = getattr(recognizer, "flush_utterance", None)
        if not callable(flush_utterance):
            # The legacy one-shot sidecar predates the resident JSONL protocol.
            # Preserve that adapter's existing endpoint behavior; production
            # resident sessions use the causal ACK path below.
            return {
                "acknowledged": True,
                "status": "unsupported",
                "boundary_id": None,
            }
        if pending_funasr_boundary_id is None:
            funasr_boundary_sequence += 1
            pending_funasr_boundary_id = (
                f"utterance-boundary-{funasr_boundary_sequence:08d}"
            )
            pending_funasr_boundary_started_at = time.monotonic()
            pending_funasr_boundary_attempts = 0
            pending_funasr_boundary_budget_exhausted = False
        boundary_id = pending_funasr_boundary_id
        started_at = time.monotonic()
        boundary_started_at = pending_funasr_boundary_started_at or started_at
        elapsed_s = max(0.0, started_at - boundary_started_at)
        remaining_s = max(0.0, FUNASR_BOUNDARY_MAX_WAIT_S - elapsed_s)
        if pending_funasr_boundary_budget_exhausted or (
            pending_funasr_boundary_attempts >= FUNASR_BOUNDARY_MAX_WAIT_ATTEMPTS
        ):
            # Keep the pending token and PCM for the terminal fail-closed path,
            # but do not re-enter the wait loop for every subsequent audio
            # frame or client FLUSH.  A later explicit END still observes the
            # same stable timeout result and cannot enqueue another command.
            pending_funasr_boundary_budget_exhausted = True
            reason = "funasr_boundary_ack_timeout"
            if reason not in provider_metadata["degradation_reasons"]:
                provider_metadata["degradation_reasons"].append(reason)
            return {
                "acknowledged": False,
                "status": "timeout",
                "boundary_id": boundary_id,
                "boundary_diagnostics": _boundary_diagnostics(),
                "error_code": reason,
                "wait_budget_exhausted": True,
                "wait_attempt": pending_funasr_boundary_attempts,
                "wait_budget_remaining_ms": round(remaining_s * 1_000, 1),
            }
        # Keep the first attempt at the historical 1.25s slice.  A retry gets
        # only the remaining absolute budget, preventing repeated FLUSH/END
        # messages from extending the endpoint indefinitely.
        wait_timeout_s = min(FUNASR_BOUNDARY_ACK_TIMEOUT_S, remaining_s)
        wait_timeout_s = max(FUNASR_BOUNDARY_MIN_WAIT_S, wait_timeout_s)
        pending_funasr_boundary_attempts += 1
        wait_attempt = pending_funasr_boundary_attempts
        try:
            boundary_events = await _run_blocking(
                flush_utterance,
                boundary_id,
                timeout=wait_timeout_s,
            )
        except (FunasrResidentBoundaryTimeoutError, TimeoutError):
            reason = "funasr_boundary_ack_timeout"
            provider_metadata["degradation_reasons"].append(reason)
            if pending_funasr_boundary_attempts >= FUNASR_BOUNDARY_MAX_WAIT_ATTEMPTS:
                pending_funasr_boundary_budget_exhausted = True
            _log.warning(
                "asr.stream.funasr_boundary_timeout",
                session_id=session_id,
                boundary_id=boundary_id,
                elapsed_ms=round((time.monotonic() - started_at) * 1_000, 1),
            )
            return {
                "acknowledged": False,
                "status": "timeout",
                "boundary_id": boundary_id,
                "boundary_diagnostics": _boundary_diagnostics(),
                "error_code": reason,
                "wait_timeout_ms": round(wait_timeout_s * 1_000, 1),
                "wait_budget_remaining_ms": round(remaining_s * 1_000, 1),
                "wait_attempt": wait_attempt,
                "wait_budget_exhausted": pending_funasr_boundary_attempts
                >= FUNASR_BOUNDARY_MAX_WAIT_ATTEMPTS,
            }
        except FunasrResidentUnavailableError as exc:
            reason = "funasr_boundary_failed"
            provider_metadata["degradation_reasons"].append(reason)
            _log.warning(
                "asr.stream.funasr_boundary_failed",
                session_id=session_id,
                boundary_id=boundary_id,
                error_class=type(exc).__name__,
            )
            return {
                "acknowledged": False,
                "status": "failed",
                "boundary_id": boundary_id,
                "boundary_diagnostics": _boundary_diagnostics(),
                "error_code": reason,
                "wait_timeout_ms": round(wait_timeout_s * 1_000, 1),
                "wait_budget_remaining_ms": round(remaining_s * 1_000, 1),
                "wait_attempt": wait_attempt,
            }
        # Reader dispatch is FIFO: these residual partials were emitted before
        # the matching ACK. Merge them while the original raw PCM is still
        # owned by this endpoint, then run the authoritative refinement.
        await _process_recognizer_events(
            [dict(event) for event in boundary_events],
            native_frame=last_native_frame,
        )
        pending_funasr_boundary_id = None
        pending_funasr_boundary_started_at = None
        pending_funasr_boundary_attempts = 0
        pending_funasr_boundary_budget_exhausted = False
        return {
            "acknowledged": True,
            "status": "acknowledged",
            "boundary_id": boundary_id,
            "boundary_diagnostics": _boundary_diagnostics(),
            "event_count": len(boundary_events),
            "wait_timeout_ms": round(wait_timeout_s * 1_000, 1),
            "wait_budget_remaining_ms": round(remaining_s * 1_000, 1),
            "wait_attempt": wait_attempt,
        }

    async def _process_endpoint_boundary(
        *,
        force_boundary: bool = False,
    ) -> tuple[bool, bool, dict[str, Any]]:
        if not force_boundary and not _funasr_boundary_is_imminent():
            return False, False, {
                "acknowledged": True,
                "status": "not_due",
                "boundary_id": None,
            }
        boundary_result = await _synchronize_funasr_boundary(
            force_boundary=force_boundary,
        )
        if not boundary_result["acknowledged"]:
            # Fail closed: retain endpoint_candidate and segment_pcm_buffer so
            # the same token can be retried by FLUSH or END.
            return False, False, boundary_result
        endpoint_event = await _maybe_vad_endpoint_event(
            force_boundary=force_boundary,
        )
        if endpoint_event is None:
            # The resident ACK is a causal hand-off: the worker has already
            # discarded or consumed this utterance and reset its streaming
            # cache.  Even when refinement produces no usable text, the host
            # must close the matching VAD segment as well.  Keeping
            # ``segment_has_speech`` set here makes every following silence
            # frame look like another due endpoint, creating a flush storm and
            # eventually exhausting the 1.25s ACK budget behind preview work.
            _clear_endpoint_candidate()
            _reset_segment_audio()
            await _run_blocking(
                _upsert_live_session,
                _current_session_streaming_events(),
                _current_degradation_reasons(),
            )
            return True, False, boundary_result
        endpoint_event = _normalize_capture_event(endpoint_event)
        endpoint_event.update(_native_pcm_event_identity(last_native_frame))
        endpoint_outgoing_events = [endpoint_event]
        final_committed = False
        if endpoint_event.get("event_type") == "final":
            final_committed = _append_accumulated_final(
                endpoint_event,
                getattr(recognizer, "_seq", len(accumulated_finals) + 1),
            )
            if final_committed:
                live_events = await _run_blocking(
                    _upsert_and_commit_final,
                    _current_session_streaming_events(),
                    _current_degradation_reasons(),
                    endpoint_event,
                )
                endpoint_outgoing_events.extend(
                    _unsent_realtime_candidate_events(live_events)
                )
            else:
                await _run_blocking(
                    _upsert_live_session,
                    _current_session_streaming_events(),
                    _current_degradation_reasons(),
                )
        elif (
            endpoint_event.get("event_type") == "partial"
            and _remember_live_partial(endpoint_event)
        ):
            await _run_blocking(
                _upsert_live_session,
                _current_session_streaming_events(),
                _current_degradation_reasons(),
            )
        endpoint_finalized = endpoint_event.get("event_type") == "final"
        if endpoint_finalized:
            # Persistence owns this PCM before transport does. If the peer
            # disconnects while the final is being sent, the abort path must
            # not refine and commit the same audio as a disconnect backfill.
            _clear_endpoint_candidate()
            _reset_segment_audio()
        for outgoing_event in endpoint_outgoing_events:
            await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
        if not endpoint_finalized:
            _clear_endpoint_candidate()
            _reset_segment_audio()
        return True, final_committed, boundary_result

    refiner_meeting_lease = retain_refiner_for_meeting(session_id)
    try:
        ready_for_stream = await _prepare_before_asr_ready()
    except BaseException:
        if refiner_meeting_lease:
            release_refiner_for_meeting(session_id)
        await _shutdown_session_executor()
        raise
    if not ready_for_stream:
        if refiner_meeting_lease:
            release_refiner_for_meeting(session_id)
        await _shutdown_session_executor()
        return

    try:
        while True:
            try:
                msg = await _next_stream_message()
            except NativePcmProtocolError as exc:
                await _finish_invalid_audio_payload(exc)
                return
            if msg.get("bytes") is not None:
                pcm_payload = msg["bytes"]
                try:
                    chunk_ms = _float32_pcm_duration_ms(pcm_payload)
                    pcm_rms = _float32_pcm_rms(pcm_payload)
                    audio_activity = _observe_audio_activity(pcm_payload)
                except Float32PcmPayloadError as exc:
                    await _finish_invalid_audio_payload(exc)
                    return
                segment_start_before_chunk_ms = _current_stream_end_ms()
                if pcm_rms > VAD_SILENCE_RMS_THRESHOLD and not segment_has_speech:
                    segment_speech_start_ms = segment_start_before_chunk_ms
                    preroll_bytes = int(VAD_PREROLL_MS * 16_000 * 4 / 1_000)
                    segment_speech_start_offset_bytes = max(
                        0,
                        len(segment_pcm_buffer) - preroll_bytes,
                    )
                    segment_has_speech = True
                if pcm_rms > VAD_SILENCE_RMS_THRESHOLD:
                    segment_voiced_ms += chunk_ms
                stream_elapsed_ms += chunk_ms
                segment_pcm_buffer.extend(pcm_payload)
                if not segment_has_speech:
                    # Audio is still persisted by the recording writer; this
                    # bounded buffer is only for the next ASR refinement.
                    max_preroll_bytes = int(VAD_PREROLL_MS * 16_000 * 4 / 1_000)
                    if len(segment_pcm_buffer) > max_preroll_bytes:
                        del segment_pcm_buffer[:-max_preroll_bytes]
                if audio_activity is not None:
                    audio_activity.update(
                        _native_pcm_event_identity(msg.get("_native_pcm_frame"))
                    )
                if audio_activity is not None and on_audio_active is not None:
                    await _run_blocking(on_audio_active, audio_activity)
                events = await _run_blocking(
                    _record_and_recognize_audio_payload,
                    pcm_payload,
                    audio_already_recorded=bool(msg.get("_audio_recorded")),
                    native_frame=msg.get("_native_pcm_frame"),
                )
                if pcm_rms <= VAD_SILENCE_RMS_THRESHOLD:
                    endpoint_silence_ms += chunk_ms
                else:
                    endpoint_silence_ms = 0
                await _process_recognizer_events(
                    events,
                    native_frame=msg.get("_native_pcm_frame"),
                )
                # Once the same boundary has exhausted its bounded ACK
                # budget, retain the pending PCM for the terminal fail-closed
                # path but stop invoking the barrier on every incoming frame.
                # This is an automatic-VAD guard only; an explicit FLUSH/END
                # still returns the stable timeout result below.
                if not pending_funasr_boundary_budget_exhausted:
                    await _process_endpoint_boundary()
            elif msg.get("text") == STREAM_FLUSH_COMMAND:
                (
                    boundary_processed,
                    final_committed,
                    boundary_result,
                ) = await _process_endpoint_boundary(
                    force_boundary=True,
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "event_type": "flush_complete",
                            "boundary_processed": boundary_processed,
                            "final_committed": final_committed,
                            "boundary_acknowledged": bool(
                                boundary_result["acknowledged"]
                            ),
                            "boundary_status": boundary_result["status"],
                            "boundary_diagnostics": boundary_result.get(
                                "boundary_diagnostics", []
                            ),
                            **(
                                {"boundary_id": boundary_result["boundary_id"]}
                                if boundary_result.get("boundary_id")
                                else {}
                            ),
                            **(
                                {"error_code": boundary_result["error_code"]}
                                if boundary_result.get("error_code")
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg.get("text") == "END":
                terminal_boundary_failed = False
                if pending_funasr_boundary_id is not None:
                    # Retry the same token before ending the resident session.
                    # A late ACK can now close the retained PCM exactly once.
                    # If it still does not arrive, END must not bypass the
                    # causal barrier by promoting an offline refinement.
                    _, _, terminal_boundary_result = await _process_endpoint_boundary(
                        force_boundary=True
                    )
                    terminal_boundary_failed = not bool(
                        terminal_boundary_result["acknowledged"]
                    )
                await _run_blocking(_close_audio_writer)
                if terminal_boundary_failed:
                    # The causal boundary never became safe to finalize. A
                    # resident worker may still be draining a large FIFO and
                    # ``finalize`` can wait for its full session budget (up to
                    # 30s), which is longer than the browser END deadline.
                    # Abort the ASR session instead: keep the recorded PCM and
                    # timeout diagnostics, but close this stream promptly and
                    # never promote an unacknowledged snapshot to final text.
                    provider_metadata["degradation_reasons"].append(
                        "asr_stream_terminated_after_boundary_timeout"
                    )
                    abort = getattr(recognizer, "abort", None)
                    if callable(abort):
                        try:
                            await _run_blocking(abort)
                        except Exception as abort_exc:
                            _log.warning(
                                "asr.stream.boundary_timeout_abort_failed",
                                session_id=session_id,
                                error_class=type(abort_exc).__name__,
                            )
                    final_events = []
                    # Treat the bounded abort as the terminal lifecycle action
                    # so the exception path does not attempt an unsafe offline
                    # backfill over the unresolved boundary.
                    finalization_started = True
                else:
                    final_events = await _run_blocking(recognizer.finalize)
                    finalization_started = True
                asr_shutdown_diagnostics = _content_free_asr_diagnostics(
                    getattr(recognizer, "shutdown_diagnostics", {})
                )
                # A bounded pre-roll buffer may contain room tone even when no
                # voiced frame was observed. Never refine that noise into a
                # terminal final merely because the meeting has no prior text.
                pending_refinement_audio = bool(
                    not terminal_boundary_failed
                    and segment_has_speech
                    and segment_pcm_buffer
                )
                if (
                    not terminal_boundary_failed
                    and not pending_refinement_audio
                    and not accumulated_finals
                    and segment_pcm_buffer
                ):
                    # A FunASR reconnect can leave a meaningful terminal
                    # snapshot without a voiced RMS frame in this socket's
                    # short pre-roll. Preserve that protocol evidence, while
                    # still rejecting a pure-silence/no-text tail.
                    pending_refinement_audio = _has_meaningful_endpoint_tail(
                        require_persisted_partial=True,
                    )
                    if not pending_refinement_audio:
                        pending_refinement_audio = any(
                            str(event.get("event_type") or "") == "final"
                            and _has_meaningful_endpoint_tail(
                                event,
                                require_persisted_partial=True,
                            )
                            for event in final_events
                        )
                normalized_final_events: list[dict[str, Any]] = []
                newly_appended_finals: list[dict[str, Any]] = []
                for ev in final_events:
                    ev = _normalize_capture_event(ev)
                    if is_funasr_realtime:
                        ev.update(_asr_confidence_metadata(ev))
                    ev.update(_native_pcm_event_identity(last_native_frame))
                    if is_funasr_realtime and ev.get("event_type") == "final":
                        # FunASR may emit a terminal snapshot even after a VAD
                        # endpoint already consumed and cleared the PCM. Do
                        # not run the offline worker on an empty buffer and do
                        # not turn that stale snapshot into a new partial.
                        if not pending_refinement_audio:
                            continue
                        # END is a VAD boundary when the client stops without a
                        # silence tail. Refine the remaining raw segment once,
                        # then expose the terminal model snapshot only as a
                        # non-authoritative partial if refinement is missing.
                        speech_offset = max(0, int(segment_speech_start_offset_bytes or 0))
                        refinement = await _run_blocking(
                            refine_pcm_f32,
                            bytes(segment_pcm_buffer[speech_offset:]),
                        )
                        accumulated_endpoint_text = (
                            str(endpoint_candidate.get("source_text") or "").strip()
                            if str(endpoint_candidate.get("partial_semantics") or "").casefold()
                            == "incremental_chunk"
                            else ""
                        )
                        fallback_text = accumulated_endpoint_text or str(ev.get("text") or "").strip()
                        candidate_refined_text = refinement.text.strip()
                        rejected_short_text = bool(
                            refinement.authoritative
                            and candidate_refined_text
                            and not _is_meaningful_authoritative_final(candidate_refined_text)
                        )
                        online_policy_final = _uses_online_final_resource_policy(
                            refinement,
                            fallback_text,
                        )
                        authoritative = (
                            refinement.authoritative and not rejected_short_text
                        ) or online_policy_final
                        refined_text = (
                            fallback_text
                            if online_policy_final
                            else (candidate_refined_text or fallback_text)
                            if authoritative
                            else fallback_text
                        )
                        refinement_status = "rejected_short_text" if rejected_short_text else refinement.status
                        refinement_reason = (
                            "offline_refinement_text_too_short" if rejected_short_text else refinement.reason
                        )
                        ev = {
                            **ev,
                            **_asr_confidence_metadata(
                                ev,
                                offline_refinement=(
                                    authoritative and not online_policy_final
                                ),
                            ),
                            "event_type": "final" if authoritative else "partial",
                            "text": refined_text,
                            "source_snapshot_text": fallback_text,
                            "authoritative": authoritative,
                            "final_source": (
                                "local_realtime_online_final"
                                if online_policy_final
                                else "local_offline_refinement"
                                if authoritative
                                else "online_terminal_partial"
                            ),
                            "refinement_status": refinement_status,
                            **({"refinement_model_id": refinement.model_id} if refinement.model_id else {}),
                            **({"refinement_reason": refinement_reason} if refinement_reason else {}),
                            **(
                                {"partial_semantics": "terminal_snapshot"}
                                if not authoritative
                                else {}
                            ),
                        }
                        if online_policy_final:
                            provider_metadata["degradation_reasons"].append(
                                ONLINE_ONLY_REFINEMENT_REASON
                            )
                        elif not authoritative:
                            provider_metadata["degradation_reasons"].append(
                                _refinement_degradation_reason(
                                    refinement,
                                    rejected_short_text=rejected_short_text,
                                )
                            )
                        # Keep speech-origin metadata until the terminal event
                        # has been timestamped below; reset the segment only
                        # after END has consumed this final snapshot.
                        segment_pcm_buffer.clear()
                    if ev.get("event_type") == "final" and is_funasr_realtime:
                        source_text = str(ev.get("source_snapshot_text") or ev.get("text") or "").strip()
                        refined_text = str(ev.get("text") or "").strip()
                        source_segment_id = _funasr_source_segment_id(session_id, ev)
                        if not refined_text:
                            continue
                        segment_start_ms = int(
                            segment_speech_start_ms
                            if segment_speech_start_ms is not None
                            else endpoint_committed_end_ms
                        )
                        end_ms = max(
                            segment_start_ms,
                            int(ev.get("end_ms") or 0),
                            _current_stream_end_ms(),
                        )
                        if end_ms <= segment_start_ms:
                            continue
                        endpoint_final_count += 1
                        ev = {
                            **ev,
                            "segment_id": f"vad_endpoint_{endpoint_final_count:03d}",
                            **({"source_segment_id": source_segment_id} if source_segment_id else {}),
                            "source_snapshot_text": source_text,
                            "text": refined_text,
                            "normalized_text": _stream_normalized_text(refined_text),
                            "projection_reconciled": False,
                            "start_ms": segment_start_ms,
                            "end_ms": end_ms,
                        }
                        if ev.get("authoritative") is not False:
                            endpoint_committed_source_text = source_text
                            endpoint_committed_end_ms = end_ms
                    if (
                        ev.get("event_type") == "final"
                        and not _authoritative_final_has_valid_span(
                            ev,
                            getattr(recognizer, "_seq", len(accumulated_finals) + 1),
                        )
                    ):
                        continue
                    normalized_final_events.append(ev)
                    if ev.get("event_type") == "final":
                        if ev.get("text"):
                            if _append_accumulated_final(ev, getattr(recognizer, "_seq", 0) + 1):
                                newly_appended_finals.append(ev)
                        else:
                            saw_empty_final = True
                    elif ev.get("event_type") == "partial":
                        _remember_live_partial(ev)
                if is_funasr_realtime and normalized_final_events and pending_refinement_audio:
                    _clear_endpoint_candidate()
                    _reset_segment_audio()
                if is_funasr_realtime and not endpoint_candidate:
                    # A reconnect may carry only the persisted online snapshot;
                    # restore it as a replaceable endpoint candidate so END can
                    # still run offline refinement over the recorded PCM.
                    restored_partial = (
                        next(reversed(latest_partials.values()), None)
                        if latest_partials
                        else None
                    )
                    restored_from_persisted_partial = bool(
                        restored_partial
                        and str(restored_partial.get("segment_id") or "")
                        in persisted_partial_segment_ids
                    )
                    if restored_partial is None:
                        for persisted_event in reversed(list(existing_record.get("events") or [])):
                            if persisted_event.get("event_type") not in {"transcript_partial", "transcript_final"}:
                                continue
                            payload = dict(persisted_event.get("payload") or {})
                            if str(payload.get("text") or payload.get("source_snapshot_text") or "").strip():
                                restored_partial = payload
                                restored_partial.setdefault("event_type", "partial")
                                restored_from_persisted_partial = True
                                break
                    if restored_partial is None:
                        restored_partial = {}
                    restored_text = str(restored_partial.get("text") or "").strip()
                    restored_source_text = str(
                        restored_partial.get("source_snapshot_text") or restored_text
                    ).strip()
                    if restored_text:
                        endpoint_candidate = {
                            "text": restored_text,
                            "source_text": restored_source_text,
                            "segment_id": str(restored_partial.get("segment_id") or _next_endpoint_segment_id()),
                            "start_ms": int(restored_partial.get("start_ms") or endpoint_committed_end_ms),
                            "end_ms": int(restored_partial.get("end_ms") or _current_stream_end_ms()),
                            "confidence": restored_partial.get("confidence"),
                            **(
                                {"confidence_source": str(restored_partial["confidence_source"])}
                                if restored_partial.get("confidence_source")
                                else {}
                            ),
                            **(
                                {"source_segment_id": restored_partial["source_segment_id"]}
                                if restored_partial.get("source_segment_id")
                                else {}
                            ),
                            "projection_reconciled": bool(restored_partial.get("projection_reconciled")),
                            "restored_from_persisted_partial": restored_from_persisted_partial,
                        }
                    # The candidate may only become available after the first
                    # pending-audio check above (for example, when a reconnect
                    # restores a persisted FunASR terminal snapshot). Re-run
                    # the protocol-tail admission now so END can refine and
                    # commit that bounded PCM exactly once. A prior
                    # authoritative final still suppresses a text-only stale
                    # tail; voiced PCM remains admitted through the original
                    # ``segment_has_speech`` path.
                    if (
                        not terminal_boundary_failed
                        and not pending_refinement_audio
                        and not accumulated_finals
                        and segment_pcm_buffer
                    ):
                        pending_refinement_audio = _has_meaningful_endpoint_tail(
                            require_persisted_partial=True,
                        )
                if (
                    is_funasr_realtime
                    and pending_refinement_audio
                    and not normalized_final_events
                    and endpoint_candidate.get("text")
                ):
                    # The resident worker deliberately emits no authoritative
                    # terminal event. END is therefore the final boundary for
                    # any speech not already closed by VAD silence.
                    speech_offset = max(0, int(segment_speech_start_offset_bytes or 0))
                    refinement = await _run_blocking(
                        refine_pcm_f32,
                        bytes(segment_pcm_buffer[speech_offset:]),
                    )
                    fallback_text = str(endpoint_candidate.get("text") or "").strip()
                    candidate_refined_text = refinement.text.strip()
                    rejected_short_text = bool(
                        refinement.authoritative
                        and candidate_refined_text
                        and not _is_meaningful_authoritative_final(candidate_refined_text)
                    )
                    online_policy_final = _uses_online_final_resource_policy(
                        refinement,
                        fallback_text,
                    )
                    authoritative = (
                        refinement.authoritative and not rejected_short_text
                    ) or online_policy_final
                    final_text = (
                        fallback_text
                        if online_policy_final
                        else (candidate_refined_text or fallback_text)
                        if authoritative
                        else fallback_text
                    )
                    refinement_status = "rejected_short_text" if rejected_short_text else refinement.status
                    refinement_reason = (
                        "offline_refinement_text_too_short" if rejected_short_text else refinement.reason
                    )
                    segment_start_ms = int(
                        segment_speech_start_ms
                        if segment_speech_start_ms is not None
                        else endpoint_committed_end_ms
                    )
                    end_ms = max(
                        segment_start_ms,
                        int(endpoint_candidate.get("end_ms") or 0),
                        _current_stream_end_ms(),
                    )
                    if end_ms <= segment_start_ms:
                        endpoint_candidate = {}
                        _reset_segment_audio()
                        continue
                    if authoritative:
                        endpoint_final_count += 1
                    terminal_event = {
                        "event_type": "final" if authoritative else "partial",
                        "segment_id": str(
                            endpoint_candidate.get("segment_id")
                            or _next_endpoint_segment_id()
                        ),
                        "text": final_text,
                        "normalized_text": _stream_normalized_text(final_text),
                        "start_ms": segment_start_ms,
                        "end_ms": end_ms,
                        "received_at_ms": end_ms,
                        **_asr_confidence_metadata(
                            endpoint_candidate,
                            offline_refinement=(
                                authoritative and not online_policy_final
                            ),
                        ),
                        "authoritative": authoritative,
                        "final_source": (
                            "local_realtime_online_final"
                            if online_policy_final
                            else "local_offline_refinement"
                            if authoritative
                            else "online_terminal_partial"
                        ),
                        "refinement_status": refinement_status,
                        **({"refinement_model_id": refinement.model_id} if refinement.model_id else {}),
                        **({"refinement_reason": refinement_reason} if refinement_reason else {}),
                        "endpoint_source": (
                            "end_of_stream_online_final_resource_policy"
                            if online_policy_final
                            else "end_of_stream_offline_refined"
                            if authoritative
                            else "end_of_stream_refinement_unavailable"
                        ),
                        **(
                            {"partial_semantics": "terminal_snapshot"}
                            if not authoritative
                            else {}
                        ),
                        "source_snapshot_text": str(endpoint_candidate.get("source_text") or fallback_text),
                        **(
                            {"source_segment_id": endpoint_candidate["source_segment_id"]}
                            if endpoint_candidate.get("source_segment_id")
                            else {}
                        ),
                    }
                    normalized_final_events.append(terminal_event)
                    if authoritative:
                        endpoint_committed_source_text = str(
                            terminal_event.get("source_snapshot_text") or fallback_text
                        ).strip()
                        endpoint_committed_end_ms = end_ms
                        source_snapshot = str(terminal_event.get("source_snapshot_text") or "").strip()
                        replaced_existing = False
                        if source_snapshot:
                            for index, existing_final in enumerate(accumulated_finals):
                                existing_source = str(existing_final.get("source_snapshot_text") or "").strip()
                                if existing_source and existing_source == source_snapshot:
                                    terminal_event = {
                                        **terminal_event,
                                        "segment_id": str(
                                            existing_final.get("segment_id")
                                            or terminal_event["segment_id"]
                                        ),
                                    }
                                    accumulated_finals[index] = _to_streaming_final(
                                        terminal_event,
                                        getattr(recognizer, "_seq", 0) + 1,
                                    )
                                    replaced_existing = True
                                    break
                        if not replaced_existing and _append_accumulated_final(
                            terminal_event,
                            getattr(recognizer, "_seq", 0) + 1,
                        ):
                            newly_appended_finals.append(terminal_event)
                    else:
                        _remember_live_partial(terminal_event)
                    endpoint_candidate = {}
                    if online_policy_final:
                        provider_metadata["degradation_reasons"].append(
                            ONLINE_ONLY_REFINEMENT_REASON
                        )
                    elif not authoritative:
                        provider_metadata["degradation_reasons"].append(
                            _refinement_degradation_reason(
                                refinement,
                                rejected_short_text=rejected_short_text,
                            )
                        )
                    _reset_segment_audio()
                if (
                    is_funasr_realtime
                    and not terminal_boundary_failed
                    and not pending_refinement_audio
                    and accumulated_finals
                ):
                    # Any remaining online partial is a terminal snapshot for
                    # audio already committed by the authoritative endpoint.
                    # Keeping it would surface a duplicate active tail.
                    latest_partials.clear()
                _clear_resolved_refinement_degradation()
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
                        _log.warning(
                            "asr.stream.persist_before_finalize_send_failed", session_id=session_id, error=str(exc)
                        )
                for outgoing_event in [*normalized_final_events, *finalize_candidate_events]:
                    await websocket.send_text(json.dumps(outgoing_event, ensure_ascii=False))
                degradation_reasons = _current_degradation_reasons()
                if not accumulated_finals:
                    if saw_empty_final:
                        degradation_reasons.append("asr_final_empty")
                    else:
                        degradation_reasons.append("asr_no_final")
                end_of_stream = {
                    "event_type": "end_of_stream",
                    "end_ms": _current_stream_end_ms(),
                    "received_at_ms": _current_stream_end_ms(),
                    **_native_pcm_event_identity(last_native_frame),
                    **(
                        {"asr_shutdown_diagnostics": asr_shutdown_diagnostics}
                        if asr_shutdown_diagnostics
                        else {}
                    ),
                }
                if asr_live_repo is not None:
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
                await _finish_diarization()
                await _shutdown_session_executor()
                # The browser stop path waits for this explicit terminal frame
                # before releasing its socket. Persist and finalize first so a
                # received end_of_stream also proves the resident session has
                # returned to the pool; a quiet period after an early final is
                # not a terminal condition.
                await websocket.send_text(json.dumps(end_of_stream, ensure_ascii=False))
                await websocket.close()
                _log.info(
                    "asr.stream.end", session_id=session_id, chunks=recognizer._seq, finals=len(accumulated_finals)
                )
                return
    except (Exception, AsyncCancelledError) as exc:
        await _abort_diarization()
        if audio_writer is not None:
            try:
                await _run_blocking(_close_audio_writer, interrupted=True)
            except Exception as audio_exc:
                _log.warning(
                    "asr.stream.audio_checkpoint_failed",
                    session_id=session_id,
                    error=str(audio_exc),
                )
        if not finalization_started:
            try:
                await _run_blocking(_backfill_interrupted_tail)
            except Exception as backfill_exc:
                provider_metadata["degradation_reasons"].append("offline_refinement_unavailable")
                interrupted_backfill_state = {
                    **interrupted_backfill_state,
                    "schema_version": "transcript_backfill.v1",
                    "status": "failed",
                    "error_class": type(backfill_exc).__name__,
                    "updated_at_ms": time.time_ns() // 1_000_000,
                }
                _log.warning(
                    "asr.stream.interrupted_tail_backfill_failed",
                    session_id=session_id,
                    error_class=type(backfill_exc).__name__,
                )
        abort = getattr(recognizer, "abort", None)
        if callable(abort):
            try:
                await _run_blocking(abort)
            except Exception as abort_exc:
                _log.warning("asr.stream.abort_failed", session_id=session_id, error=str(abort_exc))
        if asr_live_repo is not None:
            try:
                if finalization_started:
                    end_of_stream = {
                        "event_type": "end_of_stream",
                        "end_ms": _current_stream_end_ms(),
                        "received_at_ms": _current_stream_end_ms(),
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
        _log.warning(
            "asr.stream.aborted",
            session_id=session_id,
            error_class=type(exc).__name__,
            error_origin=_exception_origin(exc),
        )
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        if refiner_meeting_lease:
            release_refiner_for_meeting(session_id)
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
                str(event.get("normalized_text") or _normalize_text(text)) if l3_normalize_enabled else text
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
        "inference_engine": str(getattr(recognizer, "inference_engine", "") or ""),
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
    usable = validate_float32_pcm_payload(payload)
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


def _float32_pcm_duration_ms(payload: bytes) -> float:
    usable = validate_float32_pcm_payload(payload)
    return (len(usable) / 4) * 1_000 / 16_000


def _should_queue_stable_partial_candidate(text: str, ev: dict[str, Any]) -> bool:
    if ev.get("authoritative") is False:
        return False
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

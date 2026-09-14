"""FunASR streaming ASR sidecar worker (subprocess, funasr 3.11 venv).

By default, reads 16kHz mono float32 PCM chunks from stdin and finalizes on EOF.
With --resident, reads strict JSONL commands carrying base64 pcm_f32le and reuses
one model across isolated sessions. Events are emitted as JSONL on stdout. Used
by FunasrSidecarRecognizer for the real-time-meeting use case (G2).
"""
import argparse
import base64
import binascii
import contextlib
import json
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field

_REAL_STDOUT = sys.stdout
DEFAULT_CHUNK_SIZE = [0, 10, 5]
RESIDENT_PROTOCOL = "funasr-resident-jsonl.v1"
MAX_RESIDENT_PCM_BYTES = 4 * 1024 * 1024
MAX_RESIDENT_COMMAND_LINE_BYTES = 6 * 1024 * 1024
PREVIEW_VAD_FRAME_SAMPLES = 160
PREVIEW_VAD_RMS_THRESHOLD = 0.006
PREVIEW_VAD_PREROLL_SAMPLES = 1_280
RESIDENT_PENDING_AUDIO_MAX_BYTES = 4 * 1024 * 1024
RESIDENT_PENDING_AUDIO_MAX_COMMANDS = 64
RESIDENT_PENDING_CONTROL_MAX_COMMANDS = 64
# Preserve a short, not-yet-started tail so normal paced input keeps its
# preview.  Once inference is already running, a boundary discards every
# queued preview command and waits for at most that one in-flight model call.
RESIDENT_BOUNDARY_GRACE_AUDIO_BYTES = 32 * 1024
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RESIDENT_COMMAND_FIELDS = {
    "start_session": frozenset({"command", "session_id"}),
    "audio": frozenset({"command", "session_id", "pcm_base64"}),
    "flush_utterance": frozenset({"command", "session_id", "boundary_id"}),
    "end_session": frozenset({"command", "session_id"}),
    "abort_session": frozenset({"command", "session_id"}),
    "shutdown": frozenset({"command"}),
}


class ResidentProtocolError(ValueError):
    def __init__(self, code: str, *, session_id: str | None = None):
        super().__init__(code)
        self.code = code
        self.session_id = session_id


class ResidentInferenceError(RuntimeError):
    def __init__(self, session_id: str):
        super().__init__("inference_failed")
        self.session_id = session_id


class OnnxStreamingModelAdapter:
    """Expose funasr-onnx online inference through the worker model contract."""

    def __init__(self, model):
        self._model = model

    def generate(self, *, input, cache: dict, is_final: bool, **_kwargs) -> list[dict[str, str]]:
        result = self._model(
            input,
            param_dict={"cache": cache, "is_final": is_final},
        )
        texts: list[str] = []
        if isinstance(result, list):
            for item in result:
                if not isinstance(item, dict):
                    continue
                value = item.get("preds")
                if isinstance(value, (list, tuple)):
                    value = value[0] if value else ""
                text = str(value or "").strip()
                if text:
                    texts.append(text)
        return [{"text": "".join(texts)}]


@dataclass(frozen=True)
class ResidentCommandHeader:
    command: str
    session_id: str | None


@dataclass(frozen=True)
class ResidentCommand:
    command: str
    session_id: str | None
    pcm_bytes: bytes = b""
    hotwords: tuple[str, ...] = ()
    boundary_id: str | None = None


@dataclass(frozen=True)
class _BufferedResidentCommand:
    command: ResidentCommand
    skipped_preview_bytes: int = 0


@dataclass(frozen=True)
class _ResidentInputTerminal:
    error: ResidentProtocolError | None = None


class _ResidentCommandBuffer:
    """Bound the audio plane while keeping ordered control commands runnable."""

    def __init__(self, *, preview_stride_bytes: int) -> None:
        if preview_stride_bytes <= 0 or preview_stride_bytes % 4:
            raise ValueError("preview_stride_bytes must be positive and frame-aligned")
        self._condition = threading.Condition()
        self._preview_stride_bytes = preview_stride_bytes
        self._boundary_grace_audio_bytes = min(
            RESIDENT_BOUNDARY_GRACE_AUDIO_BYTES,
            preview_stride_bytes * 2,
        )
        self._items: deque[_BufferedResidentCommand | _ResidentInputTerminal] = deque()
        self._pending_audio_bytes = 0
        self._pending_audio_commands = 0
        self._pending_control_commands = 0
        self._audio_in_flight_session_id: str | None = None
        self._overflow_skipped_bytes: dict[str, int] = {}
        self._seen_boundaries: dict[tuple[str, str], None] = {}
        self._terminal_enqueued = False

    def put(self, command: ResidentCommand) -> None:
        with self._condition:
            if self._terminal_enqueued:
                return
            if command.command == "audio":
                self._put_audio_locked(command)
            else:
                self._put_control_locked(command)
            self._condition.notify()

    def put_terminal(self, error: ResidentProtocolError | None = None) -> None:
        with self._condition:
            if self._terminal_enqueued:
                return
            self._terminal_enqueued = True
            # EOF/protocol failure preserves every valid command that appeared
            # earlier on the wire. Explicit session controls use the bounded
            # preview-discard path when low-latency termination is required.
            if self._pending_control_commands >= RESIDENT_PENDING_CONTROL_MAX_COMMANDS:
                # A hostile stream can otherwise fill the control allowance
                # and leave no slot for the fatal marker, deadlocking the main
                # loop after the reader exits.
                self._items.clear()
                self._pending_audio_bytes = 0
                self._pending_audio_commands = 0
                self._pending_control_commands = 0
                self._overflow_skipped_bytes.clear()
            self._append_control_locked(_ResidentInputTerminal(error=error))
            self._condition.notify()

    def get(self) -> _BufferedResidentCommand | _ResidentInputTerminal:
        with self._condition:
            while not self._items:
                self._condition.wait()
            item = self._items.popleft()
            if isinstance(item, _ResidentInputTerminal):
                self._pending_control_commands -= 1
                return item
            if item.command.command == "audio":
                self._pending_audio_commands -= 1
                self._pending_audio_bytes -= len(item.command.pcm_bytes)
                self._audio_in_flight_session_id = item.command.session_id
            else:
                self._pending_control_commands -= 1
            return item

    def command_finished(self, item: _BufferedResidentCommand) -> None:
        with self._condition:
            session_id = item.command.session_id
            if (
                item.command.command == "audio"
                and self._audio_in_flight_session_id == session_id
            ):
                self._audio_in_flight_session_id = None

    def _put_audio_locked(self, command: ResidentCommand) -> None:
        session_id = str(command.session_id or "")
        pcm_bytes = command.pcm_bytes
        for offset in range(0, len(pcm_bytes), self._preview_stride_bytes):
            fragment = pcm_bytes[offset : offset + self._preview_stride_bytes]
            if not self._put_audio_fragment_locked(command, fragment):
                skipped_bytes = len(pcm_bytes) - offset
                self._overflow_skipped_bytes[session_id] = (
                    self._overflow_skipped_bytes.get(session_id, 0) + skipped_bytes
                )
                return

    def _put_audio_fragment_locked(
        self,
        command: ResidentCommand,
        pcm_bytes: bytes,
    ) -> bool:
        pcm_size = len(pcm_bytes)
        if (
            self._pending_audio_commands >= RESIDENT_PENDING_AUDIO_MAX_COMMANDS
            or self._pending_audio_bytes + pcm_size > RESIDENT_PENDING_AUDIO_MAX_BYTES
        ):
            return False
        fragment_command = ResidentCommand(
            command="audio",
            session_id=command.session_id,
            pcm_bytes=pcm_bytes,
        )
        self._items.append(_BufferedResidentCommand(command=fragment_command))
        self._pending_audio_commands += 1
        self._pending_audio_bytes += pcm_size
        return True

    def _put_control_locked(self, command: ResidentCommand) -> None:
        session_id = str(command.session_id or "")
        skipped_preview_bytes = 0
        if command.command == "flush_utterance":
            boundary_key = (session_id, str(command.boundary_id or ""))
            if boundary_key in self._seen_boundaries:
                # A retry token is an idempotent ACK request for the previous
                # utterance. Place it before the current utterance's trailing
                # audio without discarding or reclassifying that audio.
                insert_at = self._trailing_audio_start_locked()
                self._insert_control_locked(
                    insert_at,
                    _BufferedResidentCommand(command=command),
                )
                return
            self._seen_boundaries[boundary_key] = None
            while len(self._seen_boundaries) > 128:
                self._seen_boundaries.pop(next(iter(self._seen_boundaries)))
            overflow_bytes = self._overflow_skipped_bytes.pop(session_id, 0)
            trailing_bytes = self._trailing_audio_bytes_locked()
            if (
                self._audio_in_flight_session_id == command.session_id
                or overflow_bytes
                or trailing_bytes > self._boundary_grace_audio_bytes
            ):
                skipped_preview_bytes = (
                    overflow_bytes + self._discard_trailing_audio_locked(session_id)
                )
        elif command.command in {"abort_session", "end_session"}:
            skipped_preview_bytes = self._overflow_skipped_bytes.pop(session_id, 0)
            # Session controls are never allowed to wait behind a sustained
            # preview backlog. END may preserve one small tail when no model
            # call is active so the normal short-utterance path remains useful.
            trailing_bytes = self._trailing_audio_bytes_locked()
            must_discard = command.command == "abort_session" or (
                self._audio_in_flight_session_id == command.session_id
                or skipped_preview_bytes
                or trailing_bytes > self._boundary_grace_audio_bytes
            )
            if must_discard:
                skipped_preview_bytes += self._discard_trailing_audio_locked(session_id)
        self._append_control_locked(
            _BufferedResidentCommand(
                command=command,
                skipped_preview_bytes=skipped_preview_bytes,
            )
        )

    def _append_control_locked(
        self,
        item: _BufferedResidentCommand | _ResidentInputTerminal,
    ) -> None:
        if self._pending_control_commands >= RESIDENT_PENDING_CONTROL_MAX_COMMANDS:
            raise ResidentProtocolError("command_queue_overflow")
        self._items.append(item)
        self._pending_control_commands += 1

    def _insert_control_locked(
        self,
        index: int,
        item: _BufferedResidentCommand,
    ) -> None:
        if self._pending_control_commands >= RESIDENT_PENDING_CONTROL_MAX_COMMANDS:
            raise ResidentProtocolError(
                "command_queue_overflow",
                session_id=item.command.session_id,
            )
        self._items.insert(index, item)
        self._pending_control_commands += 1

    def _trailing_audio_start_locked(self) -> int:
        index = len(self._items)
        while index > 0:
            item = self._items[index - 1]
            if (
                not isinstance(item, _BufferedResidentCommand)
                or item.command.command != "audio"
            ):
                break
            index -= 1
        return index

    def _trailing_audio_bytes_locked(self) -> int:
        total = 0
        for item in reversed(self._items):
            if (
                not isinstance(item, _BufferedResidentCommand)
                or item.command.command != "audio"
            ):
                break
            total += len(item.command.pcm_bytes)
        return total

    def _discard_trailing_audio_locked(self, session_id: str | None = None) -> int:
        skipped_bytes = 0
        while self._items:
            item = self._items[-1]
            if (
                not isinstance(item, _BufferedResidentCommand)
                or item.command.command != "audio"
            ):
                break
            if session_id is not None and item.command.session_id != session_id:
                break
            self._items.pop()
            pcm_size = len(item.command.pcm_bytes)
            skipped_bytes += pcm_size
            self._pending_audio_commands -= 1
            self._pending_audio_bytes -= pcm_size
        return skipped_bytes


@dataclass
class SessionState:
    session_id: str | None
    started_at: float = field(default_factory=time.monotonic)
    cache: dict = field(default_factory=dict)
    audio_buffer: bytearray = field(default_factory=bytearray)
    last_text: str = ""
    latest_partial_text: str = ""
    input_samples: int = 0
    inference_calls: int = 0
    inference_total_s: float = 0.0
    inference_max_s: float = 0.0
    hotwords: tuple[str, ...] = ()
    preview_speech_started: bool = False
    utterance_index: int = 1
    completed_boundaries: dict[str, int] = field(default_factory=dict)


def encode_resident_command(
    command: str,
    *,
    session_id: str | None = None,
    pcm_bytes: bytes = b"",
    hotwords: list[str] | tuple[str, ...] | None = None,
    boundary_id: str | None = None,
) -> bytes:
    if command != "audio" and pcm_bytes:
        raise ResidentProtocolError("invalid_command_fields", session_id=session_id)
    if command != "start_session" and hotwords is not None:
        raise ResidentProtocolError("invalid_command_fields", session_id=session_id)
    if command != "flush_utterance" and boundary_id is not None:
        raise ResidentProtocolError("invalid_command_fields", session_id=session_id)
    payload: dict[str, object] = {"command": command}
    if session_id is not None:
        payload["session_id"] = session_id
    if command == "audio":
        payload["pcm_base64"] = base64.b64encode(pcm_bytes).decode("ascii")
    elif command == "start_session" and hotwords is not None:
        payload["hotwords"] = list(_normalize_session_hotwords(hotwords))
    elif command == "flush_utterance":
        payload["boundary_id"] = _normalize_boundary_id(
            boundary_id,
            session_id=session_id,
        )
    decode_resident_command_header(payload)
    if command == "audio":
        _decode_pcm_base64(payload["pcm_base64"], session_id=session_id)
    return (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")


def decode_resident_command_header(payload: object) -> ResidentCommandHeader:
    if not isinstance(payload, dict):
        raise ResidentProtocolError("invalid_command_shape")
    command = payload.get("command")
    candidate_session_id = payload.get("session_id")
    error_session_id = candidate_session_id if _valid_session_id(candidate_session_id) else None
    if not isinstance(command, str):
        raise ResidentProtocolError("invalid_command", session_id=error_session_id)
    expected_fields = _RESIDENT_COMMAND_FIELDS.get(command)
    if expected_fields is None:
        raise ResidentProtocolError("unknown_command", session_id=error_session_id)
    actual_fields = frozenset(payload)
    accepted_fields = {expected_fields}
    if command == "start_session":
        accepted_fields.add(expected_fields | {"hotwords"})
    if actual_fields not in accepted_fields:
        raise ResidentProtocolError("invalid_command_fields", session_id=error_session_id)
    if command == "shutdown":
        return ResidentCommandHeader(command=command, session_id=None)
    if not _valid_session_id(candidate_session_id):
        raise ResidentProtocolError("invalid_session_id")
    return ResidentCommandHeader(command=command, session_id=candidate_session_id)


def decode_resident_command(line: bytes | str) -> ResidentCommand:
    if isinstance(line, bytes):
        if len(line) > MAX_RESIDENT_COMMAND_LINE_BYTES:
            raise ResidentProtocolError("command_line_too_large")
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ResidentProtocolError("invalid_utf8") from exc
    elif isinstance(line, str):
        if len(line.encode("utf-8")) > MAX_RESIDENT_COMMAND_LINE_BYTES:
            raise ResidentProtocolError("command_line_too_large")
        text = line
    else:
        raise ResidentProtocolError("invalid_command_shape")
    try:
        payload = json.loads(text, object_pairs_hook=_json_object_without_duplicate_keys)
    except ResidentProtocolError:
        raise
    except json.JSONDecodeError as exc:
        raise ResidentProtocolError("invalid_json") from exc
    header = decode_resident_command_header(payload)
    pcm_bytes = b""
    hotwords: tuple[str, ...] = ()
    boundary_id: str | None = None
    if header.command == "audio":
        pcm_bytes = _decode_pcm_base64(payload["pcm_base64"], session_id=header.session_id)
    elif header.command == "start_session" and "hotwords" in payload:
        hotwords = _normalize_session_hotwords(payload["hotwords"], session_id=header.session_id)
    elif header.command == "flush_utterance":
        boundary_id = _normalize_boundary_id(
            payload.get("boundary_id"),
            session_id=header.session_id,
        )
    return ResidentCommand(
        command=header.command,
        session_id=header.session_id,
        pcm_bytes=pcm_bytes,
        hotwords=hotwords,
        boundary_id=boundary_id,
    )


def read_resident_command(stdin) -> ResidentCommand | None:
    line = stdin.readline(MAX_RESIDENT_COMMAND_LINE_BYTES + 1)
    if not line:
        return None
    if len(line) > MAX_RESIDENT_COMMAND_LINE_BYTES:
        raise ResidentProtocolError("command_line_too_large")
    return decode_resident_command(line)


def _json_object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ResidentProtocolError("duplicate_json_field")
        payload[key] = value
    return payload


def _valid_session_id(value: object) -> bool:
    return isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) is not None


def _decode_pcm_base64(value: object, *, session_id: str | None) -> bytes:
    if not isinstance(value, str):
        raise ResidentProtocolError("invalid_pcm_base64", session_id=session_id)
    try:
        pcm_bytes = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ResidentProtocolError("invalid_pcm_base64", session_id=session_id) from exc
    if not pcm_bytes:
        raise ResidentProtocolError("empty_audio_payload", session_id=session_id)
    if len(pcm_bytes) > MAX_RESIDENT_PCM_BYTES:
        raise ResidentProtocolError("audio_payload_too_large", session_id=session_id)
    if len(pcm_bytes) % 4:
        raise ResidentProtocolError("audio_payload_unaligned", session_id=session_id)
    return pcm_bytes


def _normalize_session_hotwords(
    value: object,
    *,
    session_id: str | None = None,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 50:
        raise ResidentProtocolError("invalid_hotwords", session_id=session_id)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        if not isinstance(raw_item, str):
            raise ResidentProtocolError("invalid_hotwords", session_id=session_id)
        item = raw_item.strip()
        key = item.casefold()
        if not item or len(item) > 64 or any(ord(character) < 32 for character in item):
            raise ResidentProtocolError("invalid_hotwords", session_id=session_id)
        if key not in seen:
            seen.add(key)
            normalized.append(item)
    return tuple(normalized)


def _normalize_boundary_id(
    value: object,
    *,
    session_id: str | None,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 192
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise ResidentProtocolError("invalid_boundary_id", session_id=session_id)
    return value


def _merge_hotwords(*groups: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return tuple(merged)


def _parse_chunk_size(value: str) -> list[int]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        raise argparse.ArgumentTypeError("chunk size must use the form left,current,right, for example 0,30,15")
    try:
        parsed = [int(part) for part in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("chunk size values must be integers") from exc
    if parsed[1] <= 0:
        raise argparse.ArgumentTypeError("current chunk size must be positive")
    return parsed


def chunk_stride_samples(chunk_size: list[int]) -> int:
    return max(1, chunk_size[1] * 960)


def merge_partial_hypothesis(previous: str, current: str) -> str:
    previous = previous.strip()
    current = current.strip()
    if not previous:
        return current
    if not current:
        return previous
    if current.startswith(previous):
        return current
    overlap = _suffix_prefix_overlap(previous, current)
    if overlap:
        return previous + current[overlap:]
    if _common_prefix_length(previous, current) > 0 and len(current) >= len(previous):
        return current
    return previous + current


def _suffix_prefix_overlap(previous: str, current: str) -> int:
    max_overlap = min(len(previous), len(current))
    for size in range(max_overlap, 0, -1):
        if previous[-size:] == current[:size]:
            return size
    return 0


def _common_prefix_length(left: str, right: str) -> int:
    count = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        count += 1
    return count


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="FunASR streaming ASR sidecar.")
    ap.add_argument("--resident", action="store_true", help="reuse one model across JSONL-framed sessions")
    ap.add_argument("--model", default="paraformer-zh-streaming")
    ap.add_argument("--engine", choices=("pytorch", "onnx"), default="pytorch")
    ap.add_argument("--onnx-device-id", default="-1")
    ap.add_argument("--onnx-intra-op-threads", type=int, default=4)
    ap.add_argument("--hotwords", default="")
    ap.add_argument("--chunk-size", type=_parse_chunk_size, default=DEFAULT_CHUNK_SIZE)
    ap.add_argument("--encoder-chunk-look-back", type=int, default=4)
    ap.add_argument("--decoder-chunk-look-back", type=int, default=1)
    return ap.parse_args(argv)


def _write_event(payload: dict) -> None:
    if "session_id" not in payload:
        raise ValueError("stdout events must declare session_id")
    _REAL_STDOUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _REAL_STDOUT.flush()


def _emit(event_type: str, text: str, idx: int, *, session_id: str | None = None) -> None:
    is_partial = event_type == "partial"
    is_terminal_snapshot = event_type == "final"
    _write_event(
        {
            "event_type": event_type,
            "session_id": session_id,
            "segment_id": f"funasr_sc_{idx:03d}",
            "text": text,
            "sample_rate": 16000,
            # A worker `final` is only the model's terminal snapshot. Product
            # code must run segment refinement before treating any text as an
            # authoritative transcript final.
            "authoritative": False if (is_partial or is_terminal_snapshot) else None,
            "partial_semantics": (
                "incremental_chunk" if is_partial else "terminal_snapshot" if is_terminal_snapshot else None
            ),
            "final_source": "online_terminal_snapshot" if is_terminal_snapshot else None,
        }
    )


def _emit_ready(*, resident: bool, engine: str = "pytorch") -> None:
    payload = {
        "event_type": "ready",
        "session_id": None,
        "scope": "process",
        "provider": "funasr_realtime",
        "inference_engine": engine,
        "model_resolution": "local_model_dir",
        "sample_rate": 16000,
    }
    if resident:
        payload["protocol"] = RESIDENT_PROTOCOL
    _write_event(payload)


def _emit_session_started(session_id: str) -> None:
    _write_event(
        {
            "event_type": "session_started",
            "session_id": session_id,
            "scope": "session",
            "sample_rate": 16000,
            "channels": 1,
            "audio_encoding": "pcm_f32le",
        }
    )


def _emit_utterance_boundary_complete(
    *,
    session_id: str,
    boundary_id: str,
    utterance_index: int,
    duplicate: bool,
    drain_ms: float | None = None,
    skipped_preview_bytes: int = 0,
    skipped_silence_bytes: int = 0,
) -> None:
    payload = {
        "event_type": "utterance_boundary_complete",
        "session_id": session_id,
        "scope": "utterance",
        "boundary_id": boundary_id,
        "utterance_index": utterance_index,
        "duplicate": duplicate,
    }
    if drain_ms is not None:
        payload["drain_ms"] = drain_ms
    if skipped_preview_bytes:
        payload["skipped_preview_bytes"] = skipped_preview_bytes
    if skipped_silence_bytes:
        payload["skipped_silence_bytes"] = skipped_silence_bytes
    _write_event(payload)


def _emit_session_terminal(
    event_type: str,
    *,
    session_id: str,
    status: str,
    reason: str,
    final_emitted: bool,
) -> None:
    _write_event(
        {
            "event_type": event_type,
            "session_id": session_id,
            "scope": "session",
            "status": status,
            "reason": reason,
            "final_emitted": final_emitted,
        }
    )


def _emit_error(*, session_id: str | None, error_code: str) -> None:
    _write_event(
        {
            "event_type": "error",
            "session_id": session_id,
            "scope": "session" if session_id is not None else "process",
            "error_code": error_code,
            "fatal": True,
        }
    )


def _emit_telemetry(
    *,
    session_id: str | None,
    input_samples: int,
    inference_calls: int,
    inference_total_s: float,
    inference_max_s: float,
    worker_total_s: float,
) -> None:
    input_seconds = input_samples / 16_000
    _write_event(
        {
            "event_type": "telemetry",
            "session_id": session_id,
            "input_samples": input_samples,
            "input_seconds": round(input_seconds, 3),
            "inference_calls": inference_calls,
            "inference_total_ms": round(inference_total_s * 1_000, 2),
            "inference_max_ms": round(inference_max_s * 1_000, 2),
            "worker_total_ms": round(worker_total_s * 1_000, 2),
            "realtime_factor": round(inference_total_s / input_seconds, 4)
            if input_seconds > 0
            else None,
        }
    )


def _generate_chunk(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    state: SessionState,
    pcm_bytes: bytes,
    dtype: str,
    is_final: bool = False,
) -> None:
    chunk = np_module.frombuffer(pcm_bytes, dtype=dtype)
    kw = {
        "input": chunk,
        "cache": state.cache,
        "is_final": is_final,
        "chunk_size": args.chunk_size,
        "encoder_chunk_look_back": args.encoder_chunk_look_back,
        "decoder_chunk_look_back": args.decoder_chunk_look_back,
    }
    if hotwords:
        kw["hotword"] = hotwords
    inference_started_at = time.monotonic()
    try:
        res = model.generate(**kw)
    except Exception as exc:
        if state.session_id is not None:
            raise ResidentInferenceError(state.session_id) from exc
        raise
    inference_elapsed_s = time.monotonic() - inference_started_at
    state.inference_calls += 1
    state.inference_total_s += inference_elapsed_s
    state.inference_max_s = max(state.inference_max_s, inference_elapsed_s)
    text = "".join(item.get("text", "") for item in res).strip()
    if text and text != state.last_text:
        # Paraformer streaming returns the newly decoded chunk, not a stable
        # cumulative transcript. Keep it replaceable and never synthesize a
        # final by concatenating these snapshots.
        state.latest_partial_text = text
        _emit(
            "partial",
            text,
            state.utterance_index,
            session_id=state.session_id,
        )
        state.last_text = text


def _emit_state_telemetry(state: SessionState, *, elapsed_s: float) -> None:
    _emit_telemetry(
        session_id=state.session_id,
        input_samples=state.input_samples,
        inference_calls=state.inference_calls,
        inference_total_s=state.inference_total_s,
        inference_max_s=state.inference_max_s,
        worker_total_s=elapsed_s,
    )


def _run_legacy_mode(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    worker_started_at: float,
    stdin,
) -> None:
    state = SessionState(session_id=None, started_at=worker_started_at)
    chunk_stride_bytes = chunk_stride_samples(args.chunk_size) * 4
    while True:
        data = stdin.read(chunk_stride_bytes)
        if not data:
            break
        state.input_samples += len(data) // 4
        _generate_chunk(
            model=model,
            np_module=np_module,
            args=args,
            hotwords=hotwords,
            state=state,
            pcm_bytes=data,
            dtype="float32",
        )
    if state.latest_partial_text:
        _emit("final", state.latest_partial_text, 1, session_id=None)
    _emit_state_telemetry(state, elapsed_s=time.monotonic() - worker_started_at)


def _process_resident_audio(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    state: SessionState,
    flush: bool,
) -> int:
    """Run preview inference for complete strides and return skipped bytes.

    The server-side VAD owns the authoritative boundary.  A flush commonly
    leaves a short tail made entirely of silence (the endpoint's pause).  It
    is safe to discard that tail from the *online preview* path because the
    backend retains the original PCM for authoritative refinement.  Avoiding
    an ``is_final`` call for silence removes a frequent 0.5-1s tail stall while
    preserving the causal ACK and evidence boundary.
    """
    chunk_stride_bytes = chunk_stride_samples(args.chunk_size) * 4
    # Emit preview inference as soon as one complete stride is available.
    # Authoritative finals come from the independent offline refiner, so holding
    # a full stride for END only adds one transport cycle to visible latency.
    while len(state.audio_buffer) >= chunk_stride_bytes:
        chunk_bytes = bytes(state.audio_buffer[:chunk_stride_bytes])
        del state.audio_buffer[:chunk_stride_bytes]
        _generate_chunk(
            model=model,
            np_module=np_module,
            args=args,
            hotwords=hotwords,
            state=state,
            pcm_bytes=chunk_bytes,
            dtype="<f4",
        )
    skipped_silence_bytes = 0
    if flush and state.audio_buffer:
        tail_bytes = bytes(state.audio_buffer)
        if _pcm_contains_preview_speech(np_module=np_module, pcm_bytes=tail_bytes):
            state.audio_buffer.clear()
            _generate_chunk(
                model=model,
                np_module=np_module,
                args=args,
                hotwords=hotwords,
                state=state,
                pcm_bytes=tail_bytes,
                dtype="<f4",
                is_final=True,
            )
        else:
            # Keep this accounting explicit in the ACK so diagnostics can
            # distinguish an intentional silent-tail fast path from dropped
            # audio.  The backend still owns the complete raw PCM segment.
            skipped_silence_bytes = len(tail_bytes)
            state.audio_buffer.clear()
    return skipped_silence_bytes


def _pcm_contains_preview_speech(*, np_module, pcm_bytes: bytes) -> bool:
    """Return true when any complete/partial VAD frame exceeds the preview floor."""

    if not pcm_bytes:
        return False
    samples = np_module.frombuffer(pcm_bytes, dtype="<f4")
    if len(samples) == 0:
        return False
    # A malformed non-finite sample must never make us silently skip a tail.
    try:
        if not bool(np_module.isfinite(samples).all()):
            return True
    except (AttributeError, TypeError, ValueError):
        # Test doubles and alternate array implementations may not expose
        # ``isfinite``; retain the conservative speech path in that case.
        return True
    for frame_start in range(0, len(samples), PREVIEW_VAD_FRAME_SAMPLES):
        frame = samples[frame_start : frame_start + PREVIEW_VAD_FRAME_SAMPLES]
        if len(frame) and float(np_module.sqrt(np_module.mean(frame * frame))) > PREVIEW_VAD_RMS_THRESHOLD:
            return True
    return False


def _reset_resident_utterance(state: SessionState) -> None:
    state.cache = {}
    state.audio_buffer.clear()
    state.last_text = ""
    state.latest_partial_text = ""
    state.preview_speech_started = False
    state.utterance_index += 1


def _trim_preview_leading_silence(*, np_module, state: SessionState, pcm_bytes: bytes) -> bytes:
    if state.preview_speech_started or not pcm_bytes:
        return pcm_bytes
    samples = np_module.frombuffer(pcm_bytes, dtype="<f4")
    complete_frames = len(samples) // PREVIEW_VAD_FRAME_SAMPLES
    for frame_index in range(complete_frames):
        frame_start = frame_index * PREVIEW_VAD_FRAME_SAMPLES
        frame = samples[frame_start : frame_start + PREVIEW_VAD_FRAME_SAMPLES]
        rms = float(np_module.sqrt(np_module.mean(frame * frame)))
        if rms <= PREVIEW_VAD_RMS_THRESHOLD:
            continue
        state.preview_speech_started = True
        preview_start = max(0, frame_start - PREVIEW_VAD_PREROLL_SAMPLES)
        return pcm_bytes[preview_start * 4 :]
    return b""


def _read_resident_commands(*, stdin, command_buffer: _ResidentCommandBuffer) -> None:
    """Decode stdin continuously and publish semantically ordered commands."""

    active_session_id: str | None = None
    try:
        while True:
            command = read_resident_command(stdin)
            if command is None:
                error = (
                    ResidentProtocolError(
                        "unexpected_eof",
                        session_id=active_session_id,
                    )
                    if active_session_id is not None
                    else None
                )
                command_buffer.put_terminal(error)
                return
            if command.command == "start_session":
                if active_session_id is not None:
                    raise ResidentProtocolError(
                        "concurrent_session",
                        session_id=command.session_id,
                    )
                active_session_id = command.session_id
            elif command.command == "shutdown":
                if active_session_id is not None:
                    raise ResidentProtocolError(
                        "shutdown_during_session",
                        session_id=active_session_id,
                    )
                command_buffer.put(command)
                return
            else:
                if active_session_id is None:
                    raise ResidentProtocolError(
                        "no_active_session",
                        session_id=command.session_id,
                    )
                if command.session_id != active_session_id:
                    raise ResidentProtocolError(
                        "session_mismatch",
                        session_id=command.session_id,
                    )
                if command.command in {"abort_session", "end_session"}:
                    active_session_id = None
            command_buffer.put(command)
    except ResidentProtocolError as exc:
        command_buffer.put_terminal(exc)
    except Exception:
        command_buffer.put_terminal(
            ResidentProtocolError(
                "stdin_read_failed",
                session_id=active_session_id,
            )
        )


def _run_resident_mode(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    stdin,
) -> None:
    state: SessionState | None = None
    command_buffer = _ResidentCommandBuffer(
        preview_stride_bytes=chunk_stride_samples(args.chunk_size) * 4,
    )
    reader = threading.Thread(
        target=_read_resident_commands,
        kwargs={"stdin": stdin, "command_buffer": command_buffer},
        daemon=True,
        name="funasr-resident-stdin-reader",
    )
    reader.start()
    while True:
        buffered = command_buffer.get()
        if isinstance(buffered, _ResidentInputTerminal):
            if buffered.error is not None:
                raise buffered.error
            return
        command = buffered.command
        try:
            if command.command == "start_session":
                if state is not None:
                    raise ResidentProtocolError("concurrent_session", session_id=command.session_id)
                state = SessionState(
                    session_id=command.session_id,
                    hotwords=_merge_hotwords(tuple(hotwords), command.hotwords),
                )
                _emit_session_started(command.session_id)
                continue
            if command.command == "shutdown":
                if state is not None:
                    raise ResidentProtocolError("shutdown_during_session", session_id=state.session_id)
                return
            if state is None:
                raise ResidentProtocolError("no_active_session", session_id=command.session_id)
            if command.session_id != state.session_id:
                raise ResidentProtocolError("session_mismatch", session_id=command.session_id)
            if buffered.skipped_preview_bytes:
                state.input_samples += buffered.skipped_preview_bytes // 4
            if command.command == "audio":
                state.input_samples += len(command.pcm_bytes) // 4
                state.audio_buffer.extend(
                    _trim_preview_leading_silence(
                        np_module=np_module,
                        state=state,
                        pcm_bytes=command.pcm_bytes,
                    )
                )
                _process_resident_audio(
                    model=model,
                    np_module=np_module,
                    args=args,
                    hotwords=list(state.hotwords),
                    state=state,
                    flush=False,
                )
                continue
            if command.command == "flush_utterance":
                boundary_id = str(command.boundary_id or "")
                if boundary_id in state.completed_boundaries:
                    _emit_utterance_boundary_complete(
                        session_id=state.session_id,
                        boundary_id=boundary_id,
                        utterance_index=state.completed_boundaries[boundary_id],
                        duplicate=True,
                    )
                    continue
                completed_utterance_index = state.utterance_index
                started_at = time.monotonic()
                skipped_silence_bytes = _process_resident_audio(
                    model=model,
                    np_module=np_module,
                    args=args,
                    hotwords=list(state.hotwords),
                    state=state,
                    flush=True,
                )
                state.completed_boundaries[boundary_id] = completed_utterance_index
                if len(state.completed_boundaries) > 128:
                    oldest_boundary_id = next(iter(state.completed_boundaries))
                    state.completed_boundaries.pop(oldest_boundary_id, None)
                _reset_resident_utterance(state)
                # `_generate_chunk` writes any residual partial synchronously.
                # The ACK proves that older preview bytes were either processed
                # or explicitly invalidated and that the next command sees a
                # fresh cache.
                _emit_utterance_boundary_complete(
                    session_id=state.session_id,
                    boundary_id=boundary_id,
                    utterance_index=completed_utterance_index,
                    duplicate=False,
                    drain_ms=round((time.monotonic() - started_at) * 1_000, 2),
                    skipped_preview_bytes=buffered.skipped_preview_bytes,
                    skipped_silence_bytes=skipped_silence_bytes,
                )
                continue
            if command.command == "end_session":
                _process_resident_audio(
                    model=model,
                    np_module=np_module,
                    args=args,
                    hotwords=list(state.hotwords),
                    state=state,
                    flush=True,
                )
                # This is a terminal model snapshot for protocol compatibility,
                # not an authoritative product final. The backend refines it over
                # raw segment PCM before persistence.
                final_emitted = bool(state.latest_partial_text)
                if final_emitted:
                    _emit(
                        "final",
                        state.latest_partial_text,
                        state.utterance_index,
                        session_id=state.session_id,
                    )
                _emit_state_telemetry(state, elapsed_s=time.monotonic() - state.started_at)
                _emit_session_terminal(
                    "session_ended",
                    session_id=state.session_id,
                    status="completed",
                    reason="end_session",
                    final_emitted=final_emitted,
                )
                state = None
                continue
            if command.command == "abort_session":
                _emit_state_telemetry(state, elapsed_s=time.monotonic() - state.started_at)
                _emit_session_terminal(
                    "session_aborted",
                    session_id=state.session_id,
                    status="aborted",
                    reason="abort_session",
                    final_emitted=False,
                )
                state = None
                continue
            raise ResidentProtocolError("unknown_command", session_id=command.session_id)
        finally:
            command_buffer.command_finished(buffered)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    worker_started_at = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np

        if args.engine == "onnx":
            from funasr_onnx.paraformer_online_bin import Paraformer

            model = OnnxStreamingModelAdapter(
                Paraformer(
                    args.model,
                    chunk_size=args.chunk_size,
                    device_id=args.onnx_device_id,
                    intra_op_num_threads=args.onnx_intra_op_threads,
                )
            )
        else:
            from funasr import AutoModel

            model = AutoModel(
                model=args.model,
                device="cpu",
                disable_update=True,
                chunk_size=args.chunk_size,
                encoder_chunk_look_back=args.encoder_chunk_look_back,
                decoder_chunk_look_back=args.decoder_chunk_look_back,
            )
        _emit_ready(resident=args.resident, engine=args.engine)
        hotwords = [word for word in args.hotwords.split() if word]
        if not args.resident:
            _run_legacy_mode(
                model=model,
                np_module=np,
                args=args,
                hotwords=hotwords,
                worker_started_at=worker_started_at,
                stdin=sys.stdin.buffer,
            )
            return
        try:
            _run_resident_mode(
                model=model,
                np_module=np,
                args=args,
                hotwords=hotwords,
                stdin=sys.stdin.buffer,
            )
        except ResidentProtocolError as exc:
            _emit_error(session_id=exc.session_id, error_code=exc.code)
            raise SystemExit(2) from None
        except ResidentInferenceError as exc:
            _emit_error(session_id=exc.session_id, error_code="inference_failed")
            _emit_session_terminal(
                "session_ended",
                session_id=exc.session_id,
                status="failed",
                reason="inference_failed",
                final_emitted=False,
            )
            raise SystemExit(3) from None


if __name__ == "__main__":
    main()

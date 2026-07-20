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
import time
from dataclasses import dataclass, field

_REAL_STDOUT = sys.stdout
DEFAULT_CHUNK_SIZE = [0, 10, 5]
RESIDENT_PROTOCOL = "funasr-resident-jsonl.v1"
MAX_RESIDENT_PCM_BYTES = 4 * 1024 * 1024
MAX_RESIDENT_COMMAND_LINE_BYTES = 6 * 1024 * 1024
_SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RESIDENT_COMMAND_FIELDS = {
    "start_session": frozenset({"command", "session_id"}),
    "audio": frozenset({"command", "session_id", "pcm_base64"}),
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


@dataclass
class SessionState:
    session_id: str | None
    started_at: float = field(default_factory=time.monotonic)
    cache: dict = field(default_factory=dict)
    audio_buffer: bytearray = field(default_factory=bytearray)
    last_text: str = ""
    merged_text: str = ""
    input_samples: int = 0
    inference_calls: int = 0
    inference_total_s: float = 0.0
    inference_max_s: float = 0.0
    hotwords: tuple[str, ...] = ()


def encode_resident_command(
    command: str,
    *,
    session_id: str | None = None,
    pcm_bytes: bytes = b"",
    hotwords: list[str] | tuple[str, ...] | None = None,
) -> bytes:
    payload: dict[str, object] = {"command": command}
    if session_id is not None:
        payload["session_id"] = session_id
    if command == "audio":
        payload["pcm_base64"] = base64.b64encode(pcm_bytes).decode("ascii")
    elif command == "start_session" and hotwords is not None:
        payload["hotwords"] = list(_normalize_session_hotwords(hotwords))
    elif pcm_bytes:
        raise ResidentProtocolError("invalid_command_fields", session_id=session_id)
    elif hotwords is not None:
        raise ResidentProtocolError("invalid_command_fields", session_id=session_id)
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
    if header.command == "audio":
        pcm_bytes = _decode_pcm_base64(payload["pcm_base64"], session_id=header.session_id)
    elif header.command == "start_session" and "hotwords" in payload:
        hotwords = _normalize_session_hotwords(payload["hotwords"], session_id=header.session_id)
    return ResidentCommand(
        command=header.command,
        session_id=header.session_id,
        pcm_bytes=pcm_bytes,
        hotwords=hotwords,
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
    _write_event(
        {
            "event_type": event_type,
            "session_id": session_id,
            "segment_id": f"funasr_sc_{idx:03d}",
            "text": text,
            "sample_rate": 16000,
        }
    )


def _emit_ready(*, resident: bool) -> None:
    payload = {
        "event_type": "ready",
        "session_id": None,
        "scope": "process",
        "provider": "funasr_realtime",
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
        state.merged_text = merge_partial_hypothesis(state.merged_text, text)
        _emit("partial", state.merged_text, 1, session_id=state.session_id)
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
    # FunASR's is_final call is unreliable on long audio; finalize from merged partials.
    if state.merged_text:
        _emit("final", state.merged_text, 1, session_id=None)
    _emit_state_telemetry(state, elapsed_s=time.monotonic() - worker_started_at)


def _process_resident_audio(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    state: SessionState,
    flush: bool,
) -> None:
    chunk_stride_bytes = chunk_stride_samples(args.chunk_size) * 4
    # Keep one stride pending so END can mark the true final audio as is_final.
    # The 300 ms transport cadence bounds the added streaming delay.
    while len(state.audio_buffer) > chunk_stride_bytes:
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
    if flush and state.audio_buffer:
        tail_bytes = bytes(state.audio_buffer)
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


def _run_resident_mode(
    *,
    model,
    np_module,
    args: argparse.Namespace,
    hotwords: list[str],
    stdin,
) -> None:
    state: SessionState | None = None
    while True:
        command = read_resident_command(stdin)
        if command is None:
            if state is not None:
                raise ResidentProtocolError("unexpected_eof", session_id=state.session_id)
            return
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
        if command.command == "audio":
            state.audio_buffer.extend(command.pcm_bytes)
            state.input_samples += len(command.pcm_bytes) // 4
            _process_resident_audio(
                model=model,
                np_module=np_module,
                args=args,
                hotwords=list(state.hotwords),
                state=state,
                flush=False,
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
            final_emitted = bool(state.merged_text)
            if final_emitted:
                _emit("final", state.merged_text, 1, session_id=state.session_id)
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


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    worker_started_at = time.monotonic()
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        from funasr import AutoModel

        model = AutoModel(
            model=args.model,
            device="cpu",
            disable_update=True,
            chunk_size=args.chunk_size,
            encoder_chunk_look_back=args.encoder_chunk_look_back,
            decoder_chunk_look_back=args.decoder_chunk_look_back,
        )
        _emit_ready(resident=args.resident)
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

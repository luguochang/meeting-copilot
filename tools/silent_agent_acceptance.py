#!/usr/bin/env python3
"""Replay a local WAV into the live ASR socket without using an audio device."""

from __future__ import annotations

from array import array
import argparse
import json
import math
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlencode, urlsplit
import wave

import websocket


SAMPLE_RATE = 16_000
NATIVE_PCM_FRAME_SAMPLES = 4_800
_NATIVE_AUDIO_SOURCE_TRACKS = {
    "tauri_native_mic": "microphone",
    "native_microphone_streaming": "microphone",
    "tauri_system_audio": "system_audio",
    "macos_system_audio": "system_audio",
}
_AUDIO_SOURCE_CHOICES = (
    "microphone",
    "browser_live_mic",
    *_NATIVE_AUDIO_SOURCE_TRACKS,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--wav", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--tail-silence-seconds", type=float, default=9.0)
    parser.add_argument("--chunk-seconds", type=float, default=0.3)
    parser.add_argument("--base-url", default="ws://127.0.0.1:8766")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument(
        "--audio-source",
        choices=_AUDIO_SOURCE_CHOICES,
        default="microphone",
        help="Select browser/raw PCM or one desktop native PCM track.",
    )
    parser.add_argument(
        "--capture-epoch",
        type=int,
        help="Required positive desktop capture epoch for native PCM sources.",
    )
    return parser.parse_args()


def _pcm16_to_float32(payload: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    normalized = array("f", (sample / 32768.0 for sample in samples))
    if sys.byteorder != "little":
        normalized.byteswap()
    return normalized.tobytes()


def _native_track_id(audio_source: str) -> str | None:
    return _NATIVE_AUDIO_SOURCE_TRACKS.get(audio_source)


def _stream_url(args: argparse.Namespace) -> str:
    native_track_id = _native_track_id(args.audio_source)
    if native_track_id is not None and (args.capture_epoch is None or args.capture_epoch <= 0):
        raise ValueError("--capture-epoch must be positive for a native PCM source")
    if native_track_id is None and args.capture_epoch is not None:
        raise ValueError("--capture-epoch is only valid for a native PCM source")
    query: dict[str, object] = {
        "audio_source": args.audio_source,
        "expected_duration_seconds": max(
            1,
            math.ceil(args.seconds + args.tail_silence_seconds),
        ),
    }
    if native_track_id is not None:
        query.update(
            {
                "pcm_protocol": "native_pcm_v2",
                "capture_epoch": args.capture_epoch,
                "transport_handshake": 1,
            }
        )
    return (
        f"{args.base_url.rstrip('/')}/live/asr/stream/ws/"
        f"{quote(args.meeting_id)}?{urlencode(query)}"
    )


class _PcmTransport:
    def __init__(
        self,
        ws: websocket.WebSocket,
        *,
        audio_source: str,
        capture_epoch: int | None,
    ) -> None:
        self._ws = ws
        self._track_id = _native_track_id(audio_source)
        self._capture_epoch = capture_epoch
        self._native_buffer = bytearray()
        self.sequence = 0
        self.sent_frames = 0

    @property
    def native(self) -> bool:
        return self._track_id is not None

    def feed(self, pcm: bytes) -> None:
        if not pcm:
            return
        if len(pcm) % 4:
            raise ValueError("float32 PCM payload must be frame-aligned")
        if not self.native:
            self._ws.send_binary(pcm)
            self.sent_frames += len(pcm) // 4
            return
        self._native_buffer.extend(pcm)
        frame_bytes = NATIVE_PCM_FRAME_SAMPLES * 4
        while len(self._native_buffer) >= frame_bytes:
            payload = bytes(self._native_buffer[:frame_bytes])
            del self._native_buffer[:frame_bytes]
            self._send_native(payload, final_partial=False)

    def flush(self) -> None:
        if self.native and self._native_buffer:
            payload = bytes(self._native_buffer)
            self._native_buffer.clear()
            self._send_native(payload, final_partial=True)

    def _send_native(self, pcm: bytes, *, final_partial: bool) -> None:
        from meeting_copilot_web_mvp.native_pcm_protocol import (  # noqa: PLC0415
            encode_native_pcm_v2_frame,
        )

        if self._track_id is None or self._capture_epoch is None:
            raise RuntimeError("native PCM transport is missing its track identity")
        self.sequence += 1
        self._ws.send_binary(
            encode_native_pcm_v2_frame(
                track_id=self._track_id,
                capture_epoch=self._capture_epoch,
                sequence=self.sequence,
                timestamp_ms=round(self.sent_frames * 1_000 / SAMPLE_RATE),
                pcm=pcm,
                final_partial=final_partial,
            )
        )
        self.sent_frames += len(pcm) // 4


def _drain(
    ws: websocket.WebSocket,
    events: list[dict[str, object]],
    event_counts: dict[str, int],
    timeout_seconds: float,
) -> bool:
    ws.settimeout(max(0.01, timeout_seconds))
    got_end = False
    while True:
        try:
            message = ws.recv()
        except websocket.WebSocketConnectionClosedException:
            return True
        except (TimeoutError, websocket.WebSocketTimeoutException, OSError):
            return got_end
        if not isinstance(message, str):
            continue
        if not message:
            return True
        payload_received_at = time.time_ns() // 1_000_000
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            payload = {"event_type": "unparsed", "raw": message}
        payload["acceptance_received_at_ms"] = payload_received_at
        events.append(payload)
        event_type = str(payload.get("event_type") or "unknown")
        event_counts[event_type] = event_counts.get(event_type, 0) + 1
        if event_type in {"end_of_stream", "error", "provider_error"}:
            got_end = True


def _run(args: argparse.Namespace) -> None:
    if args.pace <= 0:
        raise ValueError("--pace must be greater than zero")
    if args.chunk_seconds <= 0:
        raise ValueError("--chunk-seconds must be greater than zero")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, object]] = []
    event_counts: dict[str, int] = {}
    url = _stream_url(args)
    base = urlsplit(args.base_url)
    origin = f"http://{base.netloc or '127.0.0.1:8766'}"
    ws = websocket.create_connection(url, timeout=90, origin=origin)
    transport = _PcmTransport(
        ws,
        audio_source=args.audio_source,
        capture_epoch=args.capture_epoch,
    )
    ready = False
    consumed_frames = 0
    chunk_frames = max(1, round(SAMPLE_RATE * args.chunk_seconds))
    frame_limit = max(0, round(SAMPLE_RATE * args.seconds))
    started_at = time.monotonic()
    next_report_at = 15.0
    try:
        deadline = time.monotonic() + 75
        while not ready and time.monotonic() < deadline:
            terminal = _drain(
                ws,
                events,
                event_counts,
                min(0.5, max(0.01, deadline - time.monotonic())),
            )
            ready = any(
                event.get("event_type") == "asr_ready" and event.get("ready") is True
                for event in events
            )
            if terminal and not ready:
                raise ConnectionError("ASR connection closed before it became ready")
        if not ready:
            raise TimeoutError("ASR did not become ready")

        with wave.open(str(args.wav), "rb") as wav_file:
            if wav_file.getnchannels() != 1:
                raise ValueError("WAV must be mono")
            if wav_file.getsampwidth() != 2:
                raise ValueError("WAV must contain signed 16-bit PCM")
            if wav_file.getframerate() != SAMPLE_RATE:
                raise ValueError(f"WAV must be {SAMPLE_RATE} Hz")
            while consumed_frames < frame_limit:
                frame_count = min(chunk_frames, frame_limit - consumed_frames)
                pcm16 = wav_file.readframes(frame_count)
                if not pcm16:
                    break
                actual_frames = len(pcm16) // 2
                send_started = time.monotonic()
                ws.settimeout(90)
                transport.feed(_pcm16_to_float32(pcm16))
                consumed_frames += actual_frames
                if _drain(ws, events, event_counts, 0.05):
                    raise ConnectionError("ASR connection closed while sending WAV audio")
                target = started_at + (consumed_frames / SAMPLE_RATE) / args.pace
                if target > time.monotonic():
                    time.sleep(target - time.monotonic())
                sent_seconds = consumed_frames / SAMPLE_RATE
                if sent_seconds >= next_report_at:
                    print(
                        json.dumps(
                            {
                                "status": "streaming",
                                "sent_seconds": round(sent_seconds, 1),
                                "events": event_counts,
                                "send_ms": round((time.monotonic() - send_started) * 1000, 1),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    next_report_at += 15.0

        silence_frames = max(0, round(SAMPLE_RATE * args.tail_silence_seconds))
        zero_chunk = array("f", [0.0] * chunk_frames).tobytes()
        silence_sent = 0
        while silence_sent < silence_frames:
            frame_count = min(chunk_frames, silence_frames - silence_sent)
            ws.settimeout(90)
            transport.feed(zero_chunk[: frame_count * 4])
            silence_sent += frame_count
            consumed_frames += frame_count
            if _drain(ws, events, event_counts, 0.05):
                raise ConnectionError("ASR connection closed while sending tail silence")
            time.sleep(frame_count / SAMPLE_RATE / args.pace)

        transport.flush()
        if _drain(ws, events, event_counts, 0.05):
            raise ConnectionError("ASR connection closed before END")

        ws.settimeout(90)
        ws.send("END")
        end_deadline = time.monotonic() + 180
        got_end = False
        while time.monotonic() < end_deadline and not got_end:
            got_end = _drain(ws, events, event_counts, 0.5)
        if not got_end:
            print(json.dumps({"status": "warning", "warning": "end_of_stream timeout"}), flush=True)
        time.sleep(2)
    finally:
        ws.close()
        with args.output.open("w", encoding="utf-8") as output_file:
            for event in events:
                output_file.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "status": "complete",
                "meeting_id": args.meeting_id,
                "events": event_counts,
                "output": str(args.output),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    _run(_parse_args())

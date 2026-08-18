#!/usr/bin/env python3
"""Replay a local WAV into the live ASR socket without using an audio device."""

from __future__ import annotations

from array import array
import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import quote, urlsplit
import wave

import websocket


SAMPLE_RATE = 16_000


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
        except (TimeoutError, websocket.WebSocketTimeoutException, OSError):
            return got_end
        if not isinstance(message, str):
            continue
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, object]] = []
    event_counts: dict[str, int] = {}
    url = (
        f"{args.base_url.rstrip('/')}/live/asr/stream/ws/"
        f"{quote(args.meeting_id)}?audio_source=microphone"
        f"&expected_duration_seconds={max(1, int(args.seconds + args.tail_silence_seconds))}"
    )
    base = urlsplit(args.base_url)
    origin = f"http://{base.netloc or '127.0.0.1:8766'}"
    ws = websocket.create_connection(url, timeout=90, origin=origin)
    ready = False
    sent_frames = 0
    chunk_frames = max(1, round(SAMPLE_RATE * args.chunk_seconds))
    frame_limit = max(0, round(SAMPLE_RATE * args.seconds))
    started_at = time.monotonic()
    next_report_at = 15.0
    try:
        deadline = time.monotonic() + 75
        while not ready and time.monotonic() < deadline:
            _drain(ws, events, event_counts, min(0.5, max(0.01, deadline - time.monotonic())))
            ready = any(
                event.get("event_type") == "asr_ready" and event.get("ready") is True
                for event in events
            )
        if not ready:
            raise TimeoutError("ASR did not become ready")

        with wave.open(str(args.wav), "rb") as wav_file:
            if wav_file.getnchannels() != 1:
                raise ValueError("WAV must be mono")
            if wav_file.getsampwidth() != 2:
                raise ValueError("WAV must contain signed 16-bit PCM")
            if wav_file.getframerate() != SAMPLE_RATE:
                raise ValueError(f"WAV must be {SAMPLE_RATE} Hz")
            while sent_frames < frame_limit:
                frame_count = min(chunk_frames, frame_limit - sent_frames)
                pcm16 = wav_file.readframes(frame_count)
                if not pcm16:
                    break
                actual_frames = len(pcm16) // 2
                send_started = time.monotonic()
                ws.settimeout(90)
                ws.send_binary(_pcm16_to_float32(pcm16))
                sent_frames += actual_frames
                _drain(ws, events, event_counts, 0.05)
                target = started_at + (sent_frames / SAMPLE_RATE) / args.pace
                if target > time.monotonic():
                    time.sleep(target - time.monotonic())
                sent_seconds = sent_frames / SAMPLE_RATE
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
            ws.send_binary(zero_chunk[: frame_count * 4])
            silence_sent += frame_count
            _drain(ws, events, event_counts, 0.05)
            time.sleep(frame_count / SAMPLE_RATE / args.pace)

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

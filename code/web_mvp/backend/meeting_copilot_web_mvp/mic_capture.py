"""Mic capture client (Phase 4).

Real mic: captures 16kHz mono audio via sounddevice and streams float32 PCM to
the /live/asr/stream/ws/{id} WebSocket.
  pip install sounddevice websocket-client   (sounddevice needs portaudio)

--simulate <wav>: reads a 16-bit PCM wav, converts to float32, streams to the WS.
Used for headless testing (no mic required).
"""
from __future__ import annotations

import argparse
import json
import struct
import wave
from pathlib import Path
from typing import Iterator


def pcm_chunks_from_wav(wav_path: Path, chunk_samples: int = 4800) -> Iterator[bytes]:
    """Yield float32 mono PCM bytes from a 16-bit PCM wav, chunk_samples per block."""
    with wave.open(str(wav_path), "rb") as w:
        nframes = w.getnframes()
        raw = w.readframes(nframes)
        sw = w.getsampwidth()
        nchan = w.getnchannels()
    if sw != 2:
        raise ValueError(f"expected 16-bit PCM wav, got sample width {sw}")
    count = len(raw) // 2
    samples = struct.unpack("<" + "h" * count, raw)
    if nchan > 1:
        samples = samples[::nchan]
    floats = [s / 32768.0 for s in samples]
    pcm = b"".join(struct.pack("<f", v) for v in floats)
    chunk_bytes = chunk_samples * 4
    for i in range(0, len(pcm), chunk_bytes):
        yield pcm[i:i + chunk_bytes]


def stream_to_ws(ws_url: str, chunk_iter: Iterator[bytes]) -> list[dict]:
    import websocket  # pip install websocket-client
    ws = websocket.create_connection(ws_url)
    for chunk in chunk_iter:
        ws.send_binary(chunk)
    ws.send("END")
    events: list[dict] = []
    while True:
        try:
            msg = ws.recv()
            ev = json.loads(msg)
            events.append(ev)
            if ev.get("event_type") == "final":
                break
        except Exception:
            break
    ws.close()
    return events


def _mic_chunks(chunk_samples: int = 4800, sample_rate: int = 16000) -> Iterator[bytes]:
    import sounddevice as sd
    while True:
        block = sd.rec(chunk_samples, samplerate=sample_rate, channels=1, dtype="float32")
        sd.wait()
        yield block.tobytes()


def main() -> None:
    ap = argparse.ArgumentParser(description="Stream mic audio to the ASR WebSocket.")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--ws-url", required=True)
    ap.add_argument("--simulate", type=Path, help="wav file to stream instead of real mic")
    args = ap.parse_args()
    chunks = pcm_chunks_from_wav(args.simulate) if args.simulate else _mic_chunks()
    events = stream_to_ws(args.ws_url, chunks)
    for e in events:
        print(json.dumps(e, ensure_ascii=False))


if __name__ == "__main__":
    main()

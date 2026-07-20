from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

try:
    import soundfile as sf
except ModuleNotFoundError:  # CLI help and capability probes must remain available.
    sf = None

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.streaming_contract import (
    StreamingTranscriptEvent,
    build_provider_transcript_from_stream,
)


def transcribe(
    audio_path: Path,
    model_dir: Path,
    num_threads: int = 2,
    chunk_ms: int = 500,
    events_output: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    events = stream_events(
        audio_path=audio_path,
        model_dir=model_dir,
        num_threads=num_threads,
        chunk_ms=chunk_ms,
    )
    if events_output:
        events_output.parent.mkdir(parents=True, exist_ok=True)
        events_output.write_text(
            json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    transcript = build_provider_transcript_from_stream("sherpa-onnx", events)
    latency_ms = int((time.monotonic() - started) * 1000)
    duration_seconds = (
        max((event.end_ms for event in events), default=0) / 1000
        if events
        else 0.0
    )
    transcript["latency_ms"] = latency_ms
    transcript["raw"].update(
        {
            "provider": "sherpa-onnx",
            "model_id": model_dir.name,
            "model_file": _find_first(model_dir, ["*.onnx"]).name,
            "num_threads": num_threads,
            "chunk_ms": chunk_ms,
            "mode": "file_replayed_streaming_events",
        }
    )
    return {
        "status": "ok",
        "text": transcript["text"],
        "latency_ms": latency_ms,
        "audio_duration_seconds": round(duration_seconds, 6),
        "rtf": round((latency_ms / 1000) / duration_seconds, 6) if duration_seconds else 0.0,
        "entities": [],
        "segments": transcript["segments"],
        "raw": transcript["raw"],
    }


def stream_events(
    audio_path: Path,
    model_dir: Path,
    num_threads: int = 2,
    chunk_ms: int = 500,
) -> list[StreamingTranscriptEvent]:
    if sf is None:
        raise RuntimeError("soundfile is required to transcribe audio with sherpa-onnx")
    started = time.monotonic()
    import sherpa_onnx

    model_path = _find_first(model_dir, ["*.onnx"])
    tokens_path = model_dir / "tokens.txt"
    recognizer = sherpa_onnx.OnlineRecognizer.from_zipformer2_ctc(
        tokens=str(tokens_path),
        model=str(model_path),
        num_threads=num_threads,
        provider="cpu",
        enable_endpoint_detection=True,
    )
    samples, sample_rate = sf.read(str(audio_path), dtype="float32", always_2d=False)
    if getattr(samples, "ndim", 1) > 1:
        samples = samples[:, 0]
    samples = np.ascontiguousarray(samples)
    stream = recognizer.create_stream()
    events: list[StreamingTranscriptEvent] = []
    segment_index = 1
    segment_start_sample = 0
    last_partial_text = ""
    chunk_size = max(1, int(sample_rate * (chunk_ms / 1000)))

    for chunk_start in range(0, len(samples), chunk_size):
        chunk = samples[chunk_start : chunk_start + chunk_size]
        stream.accept_waveform(sample_rate, chunk)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        result = recognizer.get_result_all(stream)
        segment_end_sample = chunk_start + len(chunk)
        if recognizer.is_endpoint(stream):
            final_text = result.text.strip()
            if final_text:
                events.append(
                    _event_from_result(
                        event_type="final",
                        segment_id=f"sherpa_{segment_index:03d}",
                        text=final_text,
                        start_sample=segment_start_sample,
                        end_sample=segment_end_sample,
                        sample_rate=sample_rate,
                        started=started,
                    )
                )
                segment_index += 1
            recognizer.reset(stream)
            segment_start_sample = segment_end_sample
            last_partial_text = ""
            continue
        partial_text = result.text.strip()
        if partial_text and partial_text != last_partial_text:
            events.append(
                _event_from_result(
                    event_type="partial",
                    segment_id=f"sherpa_{segment_index:03d}",
                    text=partial_text,
                    start_sample=segment_start_sample,
                    end_sample=segment_end_sample,
                    sample_rate=sample_rate,
                    started=started,
                )
            )
            last_partial_text = partial_text

    tail = np.zeros(int(0.5 * sample_rate), dtype=np.float32)
    stream.accept_waveform(sample_rate, tail)
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result_all(stream)
    final_end_sample = len(samples)
    final_text = result.text.strip()
    if final_text:
        events.append(
            _event_from_result(
                event_type="final",
                segment_id=f"sherpa_{segment_index:03d}",
                text=final_text,
                start_sample=segment_start_sample,
                end_sample=final_end_sample,
                sample_rate=sample_rate,
                started=started,
            )
        )
    events.append(
        StreamingTranscriptEvent(
            event_type="end_of_stream",
            segment_id="sherpa_eos",
            text="",
            start_ms=_sample_to_ms(final_end_sample, sample_rate),
            end_ms=_sample_to_ms(final_end_sample, sample_rate),
            received_at_ms=_elapsed_ms(started),
        )
    )
    return events


def _sample_to_ms(sample_index: int, sample_rate: int) -> int:
    return int((sample_index / sample_rate) * 1000) if sample_rate else 0


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _event_from_result(
    event_type: str,
    segment_id: str,
    text: str,
    start_sample: int,
    end_sample: int,
    sample_rate: int,
    started: float,
) -> StreamingTranscriptEvent:
    return StreamingTranscriptEvent(
        event_type=event_type,
        segment_id=segment_id,
        text=text,
        start_ms=_sample_to_ms(start_sample, sample_rate),
        end_ms=_sample_to_ms(end_sample, sample_rate),
        received_at_ms=_elapsed_ms(started),
    )


def _find_first(root: Path, patterns: list[str]) -> Path:
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        if matches:
            return matches[0]
    raise FileNotFoundError("no model file found in configured model directory")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Transcribe audio with sherpa-onnx streaming CTC.")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--num-threads", type=int, default=2)
    parser.add_argument("--chunk-ms", type=int, default=500)
    parser.add_argument("--events-output", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            transcribe(
                args.audio,
                args.model_dir,
                args.num_threads,
                args.chunk_ms,
                events_output=args.events_output,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

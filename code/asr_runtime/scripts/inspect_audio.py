from __future__ import annotations

import argparse
import json
import wave
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class AudioMetadata:
    path: Path
    sample_rate: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_seconds: float


def inspect_audio(path: Path) -> AudioMetadata:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        frame_count = wav.getnframes()
    duration = frame_count / sample_rate if sample_rate else 0.0
    return AudioMetadata(
        path=path,
        sample_rate=sample_rate,
        channels=channels,
        sample_width_bytes=sample_width,
        frame_count=frame_count,
        duration_seconds=round(duration, 6),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect WAV audio metadata for ASR runtime.")
    parser.add_argument("audio", type=Path)
    args = parser.parse_args()
    metadata = inspect_audio(args.audio)
    data = asdict(metadata)
    data["path"] = str(metadata.path)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

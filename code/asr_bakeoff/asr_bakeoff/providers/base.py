from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    latency_ms: int = 0
    entities: list[str] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    raw: dict | None = None


class AsrProvider:
    name: str

    def transcribe(self, sample_id: str, audio_path: Path) -> TranscriptResult:
        raise NotImplementedError

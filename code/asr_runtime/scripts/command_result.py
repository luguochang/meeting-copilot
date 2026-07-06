from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderTranscript:
    text: str
    latency_ms: int
    raw: dict[str, Any]
    segments: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float | None = None


def parse_provider_stdout(stdout: str) -> ProviderTranscript:
    data = json.loads(stdout)
    if "text" not in data:
        raise ValueError("provider output missing text")
    return ProviderTranscript(
        text=str(data["text"]),
        latency_ms=int(data.get("latency_ms", 0)),
        raw=dict(data.get("raw", {})),
        segments=list(data.get("segments", [])),
        duration_seconds=_optional_float(data.get("audio_duration_seconds")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)

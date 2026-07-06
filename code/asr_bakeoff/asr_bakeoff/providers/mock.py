from __future__ import annotations

from pathlib import Path
from typing import Any

from asr_bakeoff.providers.base import AsrProvider, TranscriptResult


class MockProvider(AsrProvider):
    name = "mock"

    def __init__(self, transcripts: dict[str, str | dict[str, Any]] | None = None) -> None:
        self._transcripts = transcripts or {}

    def transcribe(self, sample_id: str, audio_path: Path) -> TranscriptResult:
        value = self._transcripts.get(sample_id, "")
        if isinstance(value, dict):
            text = str(value.get("text", ""))
            return TranscriptResult(
                text=text,
                latency_ms=int(value.get("latency_ms", 0)),
                entities=[str(entity) for entity in value.get("entities", _extract_simple_entities(text))],
                raw=value.get("raw"),
            )
        text = str(value)
        return TranscriptResult(text=text, latency_ms=0, entities=_extract_simple_entities(text))


def _extract_simple_entities(text: str) -> list[str]:
    known = ["Kafka", "K8s", "trace_id", "QPS", "灰度", "回滚", "兼容", "调用方"]
    return [entity for entity in known if entity in text]

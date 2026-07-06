from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from asr_bakeoff.providers.base import AsrProvider, TranscriptResult


class ProviderExecutionError(RuntimeError):
    pass


class CommandProvider(AsrProvider):
    """Run an external ASR command that emits one JSON transcript result."""

    def __init__(self, name: str, command: list[str], timeout_seconds: float = 300.0) -> None:
        if not command:
            raise ValueError("command must not be empty")
        self.name = name
        self._command = command
        self._timeout_seconds = timeout_seconds

    def transcribe(self, sample_id: str, audio_path: Path) -> TranscriptResult:
        command = [
            part.replace("{sample_id}", sample_id).replace("{audio_path}", str(audio_path))
            for part in self._command
        ]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                check=False,
                encoding="utf-8",
                timeout=self._timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderExecutionError(f"provider timed out after {self._timeout_seconds}s") from exc

        measured_latency_ms = int((time.monotonic() - started) * 1000)
        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            raise ProviderExecutionError(f"provider exited with code {completed.returncode}{detail}")

        data = _load_stdout_json(completed.stdout)
        if "text" not in data:
            raise ProviderExecutionError("provider output missing required field: text")
        return TranscriptResult(
            text=str(data["text"]),
            latency_ms=int(data.get("latency_ms", measured_latency_ms)),
            entities=[str(entity) for entity in data.get("entities", [])],
            segments=[dict(segment) for segment in data.get("segments", [])],
            raw=data.get("raw"),
        )


def _load_stdout_json(stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ProviderExecutionError("provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderExecutionError("provider JSON output must be an object")
    return data

"""Batch transcribe an audio file via FunASR (subprocess in the funasr 3.11 venv).

For the meeting-recording-file-conversion use case (G1): upload a recorded
meeting audio file -> FunASR batch transcribe (more accurate than streaming
sherpa) -> text. The web backend (3.14) cannot import funasr directly, so it
spawns funasr_batch_worker.py in the funasr venv as a subprocess.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.batch_transcribe")

_REPO_ROOT = Path(__file__).resolve().parents[4]
_FUNASR_PY = _REPO_ROOT / "code" / "asr_runtime" / ".venv-funasr" / "bin" / "python"
_WORKER = _REPO_ROOT / "code" / "asr_runtime" / "scripts" / "funasr_batch_worker.py"


def is_available() -> bool:
    """Return True if the funasr venv + worker exist (file conversion possible)."""
    return _FUNASR_PY.is_file() and _WORKER.is_file()


def transcribe_file(audio_path: Path, timeout: int = 180) -> str:
    """Run FunASR batch transcribe on audio_path, return transcript text.

    Raises RuntimeError if the funasr venv is missing or transcription fails.
    """
    if not is_available():
        raise RuntimeError("FunASR venv or batch worker not found — file conversion unavailable")
    _log.info("batch.transcribe.start", audio=str(audio_path))
    proc = subprocess.run(
        [str(_FUNASR_PY), str(_WORKER), "--audio", str(audio_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        _log.error("batch.transcribe.failed", returncode=proc.returncode, stderr=proc.stderr[-400:])
        raise RuntimeError(f"FunASR batch transcribe failed: {proc.stderr[-300:]}")
    text = json.loads(proc.stdout)["text"]
    _log.info("batch.transcribe.end", chars=len(text))
    return text

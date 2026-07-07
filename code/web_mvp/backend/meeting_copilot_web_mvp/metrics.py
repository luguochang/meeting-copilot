"""Simple in-memory metrics + startup config validation (Phase P3)."""
from __future__ import annotations

import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.metrics")


class Metrics:
    """Thread-safe in-memory counters (latency histogram would need more; keep simple)."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] += n

    def observe(self, name: str, value: float) -> None:
        """Record a latency observation (ms); tracks count + sum + max."""
        with self._lock:
            self._counters[f"{name}_count"] += 1
            self._counters[f"{name}_sum_ms"] += value
            self._counters[f"{name}_max_ms"] = max(self._counters.get(f"{name}_max_ms", 0.0), value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            data = dict(self._counters)
        out: dict[str, Any] = {}
        for k, v in sorted(data.items()):
            out[k] = v
        # derive averages for latency metrics
        for prefix in ("asr_latency", "llm_latency"):
            c = data.get(f"{prefix}_count", 0)
            if c:
                out[f"{prefix}_avg_ms"] = round(data.get(f"{prefix}_sum_ms", 0) / c, 2)
        return out


metrics = Metrics()


def validate_config() -> list[str]:
    """Check required env + paths. Returns a list of issue strings (empty = ok)."""
    issues: list[str] = []
    if not os.environ.get("LLM_GATEWAY_BASE_URL"):
        issues.append("LLM_GATEWAY_BASE_URL not set — LLM features (cards/minutes/approach) disabled")
    if not os.environ.get("LLM_GATEWAY_API_KEY"):
        issues.append("LLM_GATEWAY_API_KEY not set — LLM features disabled")
    repo_root = Path(__file__).resolve().parents[4]
    sherpa_venv = repo_root / "code" / "asr_runtime" / ".venv-sherpa" / "bin" / "python"
    if not sherpa_venv.is_file():
        issues.append(f"sherpa venv python not found at {sherpa_venv} — real ASR sidecar unavailable (falls back to Fake)")
    return issues


def log_config_status() -> None:
    """Log config validation issues at startup (does not raise — degrades gracefully)."""
    issues = validate_config()
    if issues:
        _log.warning("config.issues", count=len(issues), issues=issues)
    else:
        _log.info("config.ok")

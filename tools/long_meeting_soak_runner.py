#!/usr/bin/env python3
"""Build a deterministic long-meeting soak report.

This runner is intentionally synthetic by default. It does not open a
microphone, read private audio, call remote ASR, or call a real LLM. Tests and
callers inject metrics so the long-meeting decision logic can be exercised
without sleeping for the full meeting duration.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "tmp" / "soak"
SCHEMA_VERSION = "long_meeting_soak_report.v1"
DEFAULT_CHUNK_SECONDS = 2
DEFAULT_MAX_CARDS_PER_10_MINUTES = 6
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|token|secret)\s*[:=]\s*[^\s,;}]+"),
]


def run_long_meeting_soak(
    *,
    run_id: str | None = None,
    artifact_root: Path | str | None = None,
    duration_minutes: int = 20,
    metrics: dict[str, Any] | None = None,
    chunk_seconds: int = DEFAULT_CHUNK_SECONDS,
    max_cards_per_10_minutes: int = DEFAULT_MAX_CARDS_PER_10_MINUTES,
) -> dict[str, Any]:
    run_id = run_id or _default_run_id()
    output_root = _resolve_output_root(
        artifact_root=Path(artifact_root) if artifact_root is not None else DEFAULT_ARTIFACT_ROOT,
        run_id=run_id,
    )
    output_root.mkdir(parents=True, exist_ok=True)

    expected_audio_seconds = duration_minutes * 60
    input_plan = build_simulated_realtime_plan(
        duration_minutes=duration_minutes,
        chunk_seconds=chunk_seconds,
    )
    clean_metrics = _sanitize(metrics or {})
    blockers = _metric_blockers(metrics or {})
    privacy_cost_flags = _privacy_cost_flags(metrics or {})

    if privacy_cost_flags["remote_asr_called"]:
        blockers.append("remote_asr_called")

    asr_elapsed_seconds = _number_or_none((metrics or {}).get("asr_elapsed_seconds"))
    asr_rtf = None
    if asr_elapsed_seconds is not None and expected_audio_seconds > 0:
        asr_rtf = round(asr_elapsed_seconds / expected_audio_seconds, 4)

    card_count = _non_negative_int((metrics or {}).get("card_count"), default=0)
    allowed_cards = _allowed_card_count(duration_minutes, max_cards_per_10_minutes)
    suppression_count = max(0, card_count - allowed_cards)
    if suppression_count:
        privacy_cost_flags["suggestion_frequency_capped"] = True
        blockers.append("suggestion_frequency_cap_exceeded")

    memory_rss = _memory_rss(metrics or {})
    if memory_rss["status"] == "available":
        memory_growth_mb = memory_rss["end_mb"] - memory_rss["start_mb"]
        if memory_growth_mb > 256:
            blockers.append("memory_growth_exceeded")

    report = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": _now(),
        "duration_minutes": duration_minutes,
        "expected_audio_seconds": expected_audio_seconds,
        "chunk_seconds": chunk_seconds,
        "chunk_count": len(input_plan["chunks"]),
        "input_plan": input_plan,
        "asr_rtf": asr_rtf if asr_rtf is not None else "unavailable",
        "llm_call_count": _non_negative_int((metrics or {}).get("llm_call_count"), default=0),
        "llm_usage_total_tokens": _non_negative_int((metrics or {}).get("llm_usage_total_tokens"), default=0),
        "memory_rss": memory_rss,
        "memory": {"rss": memory_rss},
        "card_count": card_count,
        "max_cards_per_10_minutes": max_cards_per_10_minutes,
        "suppression_count": suppression_count,
        "privacy_cost_flags": privacy_cost_flags,
        "metrics": clean_metrics,
        "verdict": _verdict(blockers),
        "blockers": sorted(set(blockers)),
    }
    report["privacy_cost_flags"]["secret_leaked"] = _contains_secret(report)

    _write_json(output_root / "soak_report.json", report)
    return {"artifact_root": str(output_root), "report": report}


def build_simulated_realtime_plan(*, duration_minutes: int = 20, chunk_seconds: int = DEFAULT_CHUNK_SECONDS) -> dict[str, Any]:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be positive")

    expected_audio_seconds = duration_minutes * 60
    chunks = []
    for index, start_second in enumerate(range(0, expected_audio_seconds, chunk_seconds)):
        chunks.append(
            {
                "index": index,
                "start_second": start_second,
                "duration_seconds": min(chunk_seconds, expected_audio_seconds - start_second),
                "source": "synthetic_silence_plus_scripted_transcript",
            }
        )
    return {
        "kind": "deterministic_simulated_realtime_meeting",
        "seed": "p1-4-long-meeting-soak-v1",
        "duration_minutes": duration_minutes,
        "expected_audio_seconds": expected_audio_seconds,
        "chunks": chunks,
    }


def _resolve_output_root(*, artifact_root: Path, run_id: str) -> Path:
    if artifact_root.name == run_id:
        return artifact_root
    return artifact_root / run_id


def _metric_blockers(metrics: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    required_numbers = [
        "asr_elapsed_seconds",
        "llm_call_count",
        "llm_usage_total_tokens",
        "card_count",
    ]
    for key in required_numbers:
        value = metrics.get(key)
        if value is None:
            blockers.append(f"metric_missing:{key}")
            continue
        number = _number_or_none(value)
        if number is None or number < 0:
            blockers.append(f"metric_invalid:{key}")
    return blockers


def _memory_rss(metrics: dict[str, Any]) -> dict[str, Any]:
    start = _number_or_none(metrics.get("rss_mb_start"))
    peak = _number_or_none(metrics.get("rss_mb_peak"))
    end = _number_or_none(metrics.get("rss_mb_end"))
    if start is None or peak is None or end is None:
        return {"status": "unavailable", "reason": "rss_metrics_missing"}
    if min(start, peak, end) < 0:
        return {"status": "unavailable", "reason": "rss_metrics_invalid"}
    return {
        "status": "available",
        "start_mb": start,
        "peak_mb": peak,
        "end_mb": end,
        "growth_mb": round(end - start, 3),
    }


def _privacy_cost_flags(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "remote_asr_called": bool(metrics.get("remote_asr_called", False)),
        "llm_called": bool(metrics.get("llm_called", False)),
        "raw_audio_uploaded": bool(metrics.get("raw_audio_uploaded", False)),
        "real_microphone_started": bool(metrics.get("real_microphone_started", False)),
        "configs_local_read": bool(metrics.get("configs_local_read", False)),
        "suggestion_frequency_capped": False,
        "secret_leaked": False,
    }


def _allowed_card_count(duration_minutes: int, max_cards_per_10_minutes: int) -> int:
    windows = max(1, (duration_minutes + 9) // 10)
    return windows * max_cards_per_10_minutes


def _verdict(blockers: list[str]) -> str:
    if any(blocker.startswith("metric_") for blocker in blockers):
        return "blocked"
    if blockers:
        return "no_go"
    return "go"


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _non_negative_int(value: Any, *, default: int) -> int:
    number = _number_or_none(value)
    if number is None or number < 0:
        return default
    return int(number)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        sanitized = value
        for pattern in SECRET_PATTERNS:
            sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
        return sanitized
    return value


def _contains_secret(value: Any) -> bool:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return any(pattern.search(raw) for pattern in SECRET_PATTERNS)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-long-meeting-soak")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a synthetic long-meeting soak report.")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--duration-minutes", type=int, default=20)
    parser.add_argument("--chunk-seconds", type=int, default=DEFAULT_CHUNK_SECONDS)
    parser.add_argument("--max-cards-per-10-minutes", type=int, default=DEFAULT_MAX_CARDS_PER_10_MINUTES)
    parser.add_argument("--fake-metrics-json", default=None)
    args = parser.parse_args(argv)

    metrics = json.loads(args.fake_metrics_json) if args.fake_metrics_json else _default_fake_metrics(args.duration_minutes)
    result = run_long_meeting_soak(
        run_id=args.run_id,
        artifact_root=args.artifact_root,
        duration_minutes=args.duration_minutes,
        metrics=metrics,
        chunk_seconds=args.chunk_seconds,
        max_cards_per_10_minutes=args.max_cards_per_10_minutes,
    )
    print(result["artifact_root"])
    return 0 if result["report"]["verdict"] == "go" else 1


def _default_fake_metrics(duration_minutes: int) -> dict[str, Any]:
    expected_audio_seconds = duration_minutes * 60
    return {
        "asr_elapsed_seconds": round(expected_audio_seconds * 0.1, 3),
        "llm_call_count": 0,
        "llm_usage_total_tokens": 0,
        "rss_mb_start": 128.0,
        "rss_mb_peak": 144.0,
        "rss_mb_end": 140.0,
        "card_count": 0,
        "remote_asr_called": False,
        "llm_called": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())

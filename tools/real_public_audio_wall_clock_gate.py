#!/usr/bin/env python3
"""Gate repaired ASR quality against real/public wall-clock audio evidence.

The repaired local synthetic ASR gate is useful, but it must not be confused
with a real meeting or public-audio wall-clock soak. This gate consumes existing
machine-readable evidence and decides whether the project can move from
synthetic ASR quality candidate to a real-meeting pilot.

It does not read audio, open microphones, download public datasets, call ASR, or
call an LLM.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "real_public_audio_wall_clock_gate.v1"
DEFAULT_MIN_DURATION_MINUTES = 20


def build_real_public_audio_wall_clock_gate_report(
    *,
    quality_report_path: Path,
    output_path: Path,
    synthetic_soak_report_path: Path | None = None,
    real_mic_manifest_path: Path | None = None,
    wall_clock_soak_report_path: Path | None = None,
    min_duration_minutes: int = DEFAULT_MIN_DURATION_MINUTES,
) -> dict[str, Any]:
    quality_report = _read_json(quality_report_path)
    synthetic_soak_report = _read_json(synthetic_soak_report_path) if synthetic_soak_report_path else None
    real_mic_manifest = _read_json(real_mic_manifest_path) if real_mic_manifest_path else None
    wall_clock_soak_report = _read_json(wall_clock_soak_report_path) if wall_clock_soak_report_path else None

    repaired_quality = _repaired_synthetic_quality_summary(quality_report)
    synthetic_soak = _synthetic_soak_summary(synthetic_soak_report, min_duration_minutes=min_duration_minutes)
    real_mic_short = _real_mic_short_summary(real_mic_manifest, min_duration_minutes=min_duration_minutes)
    wall_clock_soak = _wall_clock_soak_summary(
        wall_clock_soak_report,
        min_duration_minutes=min_duration_minutes,
    )

    blockers: list[str] = []
    if not repaired_quality["ready"]:
        blockers.append("repaired_synthetic_quality_not_ready")
    if not wall_clock_soak["ready"]:
        blockers.append("real_or_public_wall_clock_soak_missing")

    gate_status = (
        "candidate_for_real_meeting_pilot"
        if not blockers
        else "blocked_repaired_synthetic_quality_not_ready"
        if "repaired_synthetic_quality_not_ready" in blockers
        else "blocked_real_or_public_wall_clock_soak_missing"
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "gate_status": gate_status,
        "blockers": blockers,
        "recommended_next_action": (
            "run_real_microphone_or_public_audio_wall_clock_soak"
            if "real_or_public_wall_clock_soak_missing" in blockers
            else "repair_repaired_synthetic_quality_gate"
            if "repaired_synthetic_quality_not_ready" in blockers
            else "enter_real_meeting_pilot_with_human_review"
        ),
        "min_duration_minutes": min_duration_minutes,
        "inputs": {
            "quality_report_path": str(quality_report_path),
            "synthetic_soak_report_path": str(synthetic_soak_report_path) if synthetic_soak_report_path else None,
            "real_mic_manifest_path": str(real_mic_manifest_path) if real_mic_manifest_path else None,
            "wall_clock_soak_report_path": str(wall_clock_soak_report_path) if wall_clock_soak_report_path else None,
        },
        "repaired_synthetic_quality": repaired_quality,
        "synthetic_soak": synthetic_soak,
        "real_mic_short": real_mic_short,
        "wall_clock_soak": wall_clock_soak,
        "privacy_cost_flags": _privacy_cost_flags(
            repaired_quality,
            synthetic_soak,
            real_mic_short,
            wall_clock_soak,
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _repaired_synthetic_quality_summary(report: dict[str, Any]) -> dict[str, Any]:
    decision = dict(report.get("default_decision") or {})
    aggregate = dict(report.get("aggregate") or {})
    privacy = dict(report.get("privacy_cost_flags") or {})
    sample_provider_count = _int(aggregate.get("sample_provider_count"))
    ready = (
        decision.get("decision_status") == "candidate_for_next_real_audio_gate"
        and _int(aggregate.get("sample_count")) >= 4
        and sample_provider_count > 0
        and _int(aggregate.get("pipeline_closed_sample_provider_count")) == sample_provider_count
        and _int(aggregate.get("quality_pass_sample_provider_count")) == sample_provider_count
        and _int(aggregate.get("samples_with_quality_pass_count")) == _int(aggregate.get("sample_count"))
        and _int(aggregate.get("suspected_reference_artifact_mismatch_sample_count")) == 0
        and not _flag(privacy, "remote_asr_called")
        and not _flag(privacy, "llm_called")
        and not _flag(privacy, "raw_audio_uploaded")
    )
    return {
        "ready": ready,
        "decision_status": decision.get("decision_status"),
        "sample_count": _int(aggregate.get("sample_count")),
        "sample_provider_count": sample_provider_count,
        "pipeline_closed_sample_provider_count": _int(aggregate.get("pipeline_closed_sample_provider_count")),
        "quality_pass_sample_provider_count": _int(aggregate.get("quality_pass_sample_provider_count")),
        "samples_with_quality_pass_count": _int(aggregate.get("samples_with_quality_pass_count")),
        "suspected_reference_artifact_mismatch_sample_count": _int(
            aggregate.get("suspected_reference_artifact_mismatch_sample_count")
        ),
        "privacy_cost_flags": _selected_privacy_flags(privacy),
    }


def _synthetic_soak_summary(report: dict[str, Any] | None, *, min_duration_minutes: int) -> dict[str, Any]:
    if not report:
        return {
            "ready": False,
            "present": False,
            "counts_as_real_or_public_wall_clock_soak": False,
        }
    privacy = dict(report.get("privacy_cost_flags") or {})
    ready = (
        report.get("verdict") == "go"
        and _number(report.get("duration_minutes")) >= min_duration_minutes
        and not _flag(privacy, "remote_asr_called")
        and not _flag(privacy, "raw_audio_uploaded")
    )
    return {
        "ready": ready,
        "present": True,
        "verdict": report.get("verdict"),
        "duration_minutes": _number(report.get("duration_minutes")),
        "asr_rtf": report.get("asr_rtf"),
        "counts_as_real_or_public_wall_clock_soak": False,
        "reason": "synthetic_soak_is_not_real_or_public_audio",
        "privacy_cost_flags": _selected_privacy_flags(privacy),
    }


def _real_mic_short_summary(report: dict[str, Any] | None, *, min_duration_minutes: int) -> dict[str, Any]:
    if not report:
        return {
            "ready": False,
            "present": False,
            "counts_as_wall_clock_soak": False,
        }
    privacy = dict(report.get("privacy_cost_flags") or {})
    duration = _number(report.get("duration_minutes"))
    ready = (
        report.get("verdict") == "go"
        and report.get("input_audio_path_kind") == "browser_get_user_media"
        and report.get("asr_provider_mode") == "real"
        and report.get("asr_semantic_quality_status") == "passed"
        and not _flag(privacy, "remote_asr_called")
        and not _flag(privacy, "raw_audio_uploaded")
    )
    return {
        "ready": ready,
        "present": True,
        "verdict": report.get("verdict"),
        "input_audio_path_kind": report.get("input_audio_path_kind"),
        "asr_provider": report.get("asr_provider"),
        "asr_provider_mode": report.get("asr_provider_mode"),
        "asr_semantic_quality_status": report.get("asr_semantic_quality_status"),
        "duration_minutes": duration,
        "counts_as_wall_clock_soak": ready and duration >= min_duration_minutes,
        "duration_status": "long_enough" if duration >= min_duration_minutes else "missing_or_too_short",
        "privacy_cost_flags": _selected_privacy_flags(privacy),
    }


def _wall_clock_soak_summary(report: dict[str, Any] | None, *, min_duration_minutes: int) -> dict[str, Any]:
    if not report:
        return {
            "ready": False,
            "present": False,
            "source_kind": None,
        }
    privacy = dict(report.get("privacy_cost_flags") or {})
    source_kind = report.get("source_kind")
    duration = _number(report.get("duration_minutes"))
    ready = (
        report.get("verdict") == "go"
        and source_kind in {"real_microphone", "public_audio"}
        and duration >= min_duration_minutes
        and report.get("asr_provider_mode") == "real"
        and report.get("pipeline_closed") is True
        and report.get("quality_gate_passed") is True
        and not _flag(privacy, "remote_asr_called")
        and not _flag(privacy, "raw_audio_uploaded")
        and not _flag(privacy, "configs_local_read")
    )
    return {
        "ready": ready,
        "present": True,
        "verdict": report.get("verdict"),
        "source_kind": source_kind,
        "duration_minutes": duration,
        "asr_provider_mode": report.get("asr_provider_mode"),
        "pipeline_closed": report.get("pipeline_closed"),
        "quality_gate_passed": report.get("quality_gate_passed"),
        "privacy_cost_flags": _selected_privacy_flags(privacy),
    }


def _privacy_cost_flags(*summaries: dict[str, Any]) -> dict[str, bool]:
    flags = {
        "remote_asr_called": False,
        "llm_called": False,
        "raw_audio_uploaded": False,
        "configs_local_read": False,
    }
    for summary in summaries:
        privacy = dict(summary.get("privacy_cost_flags") or {})
        for key in flags:
            flags[key] = flags[key] or bool(privacy.get(key, False))
    return flags


def _selected_privacy_flags(privacy: dict[str, Any]) -> dict[str, bool]:
    return {
        "remote_asr_called": bool(privacy.get("remote_asr_called", False)),
        "llm_called": bool(privacy.get("llm_called", False)),
        "raw_audio_uploaded": bool(privacy.get("raw_audio_uploaded", False)),
        "configs_local_read": bool(privacy.get("configs_local_read", False)),
    }


def _flag(payload: dict[str, Any], key: str) -> bool:
    return bool(payload.get(key, False))


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quality-report", required=True, type=Path)
    parser.add_argument("--synthetic-soak-report", type=Path)
    parser.add_argument("--real-mic-manifest", type=Path)
    parser.add_argument("--wall-clock-soak-report", type=Path)
    parser.add_argument("--min-duration-minutes", type=int, default=DEFAULT_MIN_DURATION_MINUTES)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_real_public_audio_wall_clock_gate_report(
        quality_report_path=args.quality_report,
        synthetic_soak_report_path=args.synthetic_soak_report,
        real_mic_manifest_path=args.real_mic_manifest,
        wall_clock_soak_report_path=args.wall_clock_soak_report,
        min_duration_minutes=args.min_duration_minutes,
        output_path=args.output,
    )
    print(json.dumps({"gate_status": report["gate_status"], "blockers": report["blockers"]}, ensure_ascii=False))
    return 0 if report["gate_status"] == "candidate_for_real_meeting_pilot" else 2


if __name__ == "__main__":
    raise SystemExit(main())

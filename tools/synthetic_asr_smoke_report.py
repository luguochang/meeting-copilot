#!/usr/bin/env python3
"""Summarize local synthetic ASR smoke outputs without running models."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "synthetic_asr_smoke_report.v1"

ALLOWED_PROVIDER_ROOTS = {"artifacts/tmp/asr_reports"}
ALLOWED_TRANSCRIPT_REPORT_ROOTS = {"artifacts/tmp/asr_reports"}
ALLOWED_EVENTS_ROOTS = {"artifacts/tmp/asr_events"}
ALLOWED_SCRIPT_ROOTS = {"data/asr_eval/synthetic_meetings/scripts"}
FORBIDDEN_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}
FORBIDDEN_PATH_LABELS = tuple(
    (root, tuple(PurePosixPath(root).parts))
    for root in sorted(FORBIDDEN_ROOTS)
)
EVENT_TYPES = ["partial", "final", "revision", "error", "end_of_stream"]


def _base_report(status: str, errors: list[str]) -> dict[str, object]:
    return {
        "report_mode": "synthetic_asr_smoke_report",
        "report_version": REPORT_VERSION,
        "status": status,
        "validation_errors": errors,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
    }


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.relative_to(REPO_ROOT)
    except ValueError:
        return None


def _is_under_allowed_resolved_root(relative_path: Path, roots: set[str]) -> bool:
    path_text = relative_path.as_posix()
    return any(path_text == root or path_text.startswith(f"{root}/") for root in roots)


def _validate_relative_path(label: str, path: str, allowed_roots: set[str]) -> list[str]:
    errors: list[str] = []
    if not _is_under_any_root(path, allowed_roots):
        errors.append(f"{label} is not allowed")
    if _is_under_any_root(path, FORBIDDEN_ROOTS):
        errors.append(f"{label} is forbidden")
    return errors


def _resolve_validated_input_path(
    *,
    label: str,
    path: str,
    allowed_roots: set[str],
) -> tuple[Path | None, list[str]]:
    errors = _validate_relative_path(label, path, allowed_roots)
    if errors:
        return None, errors

    absolute_path = REPO_ROOT / path
    resolved_path = absolute_path.resolve(strict=False)
    for forbidden_root, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved_path, suffix_parts):
            errors.append(f"{label} resolved path is forbidden: {forbidden_root}")

    resolved_relative = _repo_relative_path(resolved_path)
    if resolved_relative is None:
        errors.append(f"{label} resolved path is outside repository")
    elif not _is_under_allowed_resolved_root(resolved_relative, allowed_roots):
        errors.append(f"{label} resolved path is not allowed")

    if errors:
        return None, errors
    return resolved_path, []


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_events(events: object) -> dict[str, int]:
    counts = {event_type: 0 for event_type in EVENT_TYPES}
    if not isinstance(events, list):
        return counts
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if isinstance(event_type, str) and event_type in counts:
            counts[event_type] += 1
    return counts


def _entity_matches(text: str, entities: list[str]) -> set[str]:
    normalized_text = text.lower()
    return {entity for entity in entities if entity.lower() in normalized_text}


def _entity_score(text: str, entities: list[str]) -> tuple[float, float, list[str], list[str]]:
    if not entities:
        return 1.0, 1.0, [], []
    matched = sorted(_entity_matches(text, entities))
    missing = sorted(set(entities) - set(matched))
    recall = round(len(matched) / len(entities), 6)
    # In this smoke report, precision is scoped to expected canonical entities only.
    precision = round(len(matched) / len(matched), 6) if matched else 0.0
    return recall, precision, matched, missing


def build_synthetic_asr_smoke_report(
    *,
    provider_json_path: Path,
    transcript_report_path: Path,
    events_json_path: Path,
    script_json_path: Path,
) -> dict[str, object]:
    provider = _load_json(provider_json_path)
    transcript_report = _load_json(transcript_report_path)
    events = _load_json(events_json_path)
    script = _load_json(script_json_path)
    if not isinstance(provider, dict) or not isinstance(transcript_report, dict) or not isinstance(script, dict):
        return _base_report("blocked", ["input JSON files must contain objects"])

    technical_entities = [
        str(entity)
        for entity in script.get("technical_entities", [])
        if isinstance(entity, str)
    ]
    raw_text = str(provider.get("text", transcript_report.get("text", "")))
    normalized_text = str(transcript_report.get("normalized_text", raw_text))
    raw_recall, raw_precision, raw_matched, raw_missing = _entity_score(raw_text, technical_entities)
    normalized_recall, normalized_precision, normalized_matched, normalized_missing = _entity_score(
        normalized_text,
        technical_entities,
    )
    event_counts = _count_events(events)

    return {
        **_base_report("completed", []),
        "script_id": script.get("script_id"),
        "duration_seconds": provider.get("audio_duration_seconds"),
        "latency_ms": provider.get("latency_ms"),
        "rtf": provider.get("rtf"),
        "event_counts": event_counts,
        "segment_count": len(provider.get("segments", [])) if isinstance(provider.get("segments"), list) else 0,
        "unk_token_count": len(re.findall(r"<unk>", raw_text)),
        "technical_entity_count": len(technical_entities),
        "raw_technical_entity_recall": raw_recall,
        "raw_technical_entity_precision": raw_precision,
        "raw_matched_entities": raw_matched,
        "raw_missing_entities": raw_missing,
        "normalized_technical_entity_recall": normalized_recall,
        "normalized_technical_entity_precision": normalized_precision,
        "normalized_matched_entities": normalized_matched,
        "normalized_missing_entities": normalized_missing,
        "normalization_change_count": len(transcript_report.get("normalization_changes", []))
        if isinstance(transcript_report.get("normalization_changes"), list)
        else 0,
        "quality_gate": {
            "passes_first_pilot_entity_threshold": normalized_recall >= 0.8,
            "passes_product_entity_target": normalized_recall >= 0.9 and normalized_precision >= 0.9,
            "has_final_event": event_counts["final"] > 0,
            "has_end_of_stream": event_counts["end_of_stream"] == 1,
        },
    }


def build_synthetic_asr_smoke_report_from_relative_paths(
    *,
    provider_json_path: str,
    transcript_report_path: str,
    events_json_path: str,
    script_json_path: str,
) -> dict[str, object]:
    errors: list[str] = []
    provider_path, provider_errors = _resolve_validated_input_path(
        label="provider_json_path",
        path=provider_json_path,
        allowed_roots=ALLOWED_PROVIDER_ROOTS,
    )
    transcript_path, transcript_errors = _resolve_validated_input_path(
        label="transcript_report_path",
        path=transcript_report_path,
        allowed_roots=ALLOWED_TRANSCRIPT_REPORT_ROOTS,
    )
    events_path, events_errors = _resolve_validated_input_path(
        label="events_json_path",
        path=events_json_path,
        allowed_roots=ALLOWED_EVENTS_ROOTS,
    )
    script_path, script_errors = _resolve_validated_input_path(
        label="script_json_path",
        path=script_json_path,
        allowed_roots=ALLOWED_SCRIPT_ROOTS,
    )
    errors.extend(provider_errors)
    errors.extend(transcript_errors)
    errors.extend(events_errors)
    errors.extend(script_errors)
    if errors:
        return _base_report("blocked", errors)
    return build_synthetic_asr_smoke_report(
        provider_json_path=provider_path,
        transcript_report_path=transcript_path,
        events_json_path=events_path,
        script_json_path=script_path,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-json", required=True)
    parser.add_argument("--transcript-report", required=True)
    parser.add_argument("--events-json", required=True)
    parser.add_argument("--script-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_asr_smoke_report_from_relative_paths(
        provider_json_path=args.provider_json,
        transcript_report_path=args.transcript_report,
        events_json_path=args.events_json,
        script_json_path=args.script_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

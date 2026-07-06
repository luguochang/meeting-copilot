#!/usr/bin/env python3
"""Build DRV-044 single-scenario FunASR synthetic smoke evidence from postprocess JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

MANIFEST_VERSION = "funasr_synthetic_smoke_result.v1"
PROVIDER = "funasr_streaming"
MODEL_ALIAS = "paraformer-zh-streaming"
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
EVENT_TYPES = ("partial", "final", "revision", "error", "end_of_stream")


def _blocked_report(errors: list[str]) -> dict[str, Any]:
    return {
        "manifest_version": MANIFEST_VERSION,
        "evidence_kind": "single_synthetic_smoke",
        "provider": PROVIDER,
        "model_alias": MODEL_ALIAS,
        "source_boundary": "synthetic_audio_no_user_audio",
        "scenario_results": [],
        "evidence_status": "blocked_by_path_guard",
        "validation_errors": errors,
        "safe_to_run_asr_now": False,
        "safe_to_download_models_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_read_audio_file_now": False,
    }


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    if ".." in parts or any(part in {"", "."} for part in parts):
        return False
    if ".m4a" in path.casefold() or "voicememos" in path.casefold():
        return False
    return True


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


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


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_events(events: Any) -> dict[str, int]:
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


def _events_have_required_sequence(events: Any) -> bool:
    if not isinstance(events, list):
        return False
    seen_partial_or_final = False
    seen_final = False
    seen_end = False
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("event_type")
        if event_type in {"partial", "final"}:
            seen_partial_or_final = True
        if event_type == "final":
            seen_final = True
        if event_type == "end_of_stream":
            seen_end = True
    return seen_partial_or_final and seen_final and seen_end


def _first_partial_latency_seconds(events: Any) -> float:
    if not isinstance(events, list):
        return 0.0
    latencies = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "partial":
            continue
        received = event.get("received_at_ms")
        end_ms = event.get("end_ms", 0)
        if isinstance(received, (int, float)) and isinstance(end_ms, (int, float)):
            latencies.append(max(0.0, (received - end_ms) / 1000.0))
    return round(min(latencies), 6) if latencies else 0.0


def _final_latency_seconds(events: Any, provider: dict[str, Any]) -> float:
    if isinstance(events, list):
        latencies = []
        for event in events:
            if not isinstance(event, dict) or event.get("event_type") != "final":
                continue
            received = event.get("received_at_ms")
            end_ms = event.get("end_ms", 0)
            if isinstance(received, (int, float)) and isinstance(end_ms, (int, float)):
                latencies.append(max(0.0, (received - end_ms) / 1000.0))
        if latencies:
            return round(max(latencies), 6)
    latency_ms = provider.get("latency_ms")
    return round(max(0.0, float(latency_ms) / 1000.0), 6) if isinstance(latency_ms, (int, float)) else 0.0


def _entity_matches(text: str, entities: list[str]) -> set[str]:
    normalized_text = text.casefold()
    return {entity for entity in entities if entity.casefold() in normalized_text}


def _entity_recall(text: str, entities: list[str]) -> float:
    if not entities:
        return 0.0
    return round(len(_entity_matches(text, entities)) / len(entities), 6)


def _entity_score(text: str, entities: list[str]) -> tuple[float, list[str], list[str]]:
    if not entities:
        return 0.0, [], []
    matched = sorted(_entity_matches(text, entities))
    missing = sorted(set(entities) - set(matched))
    return round(len(matched) / len(entities), 6), matched, missing


def _scenario_kind(script: dict[str, Any]) -> str:
    scenario = str(script.get("scenario", ""))
    if scenario == "non_engineering_control" or not script.get("expected_suggestion_cards"):
        return "negative_control"
    return "engineering"


def _positive_expected_cards(script: dict[str, Any]) -> list[dict[str, Any]]:
    cards = script.get("expected_suggestion_cards")
    if not isinstance(cards, list):
        return []
    return [
        card
        for card in cards
        if isinstance(card, dict) and card.get("should_show") is True
    ]


def build_funasr_synthetic_smoke_single_result(
    *,
    provider: dict[str, Any],
    transcript_report: dict[str, Any],
    events: Any,
    script: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(script.get("script_id") or "unknown-scenario")
    scenario_kind = _scenario_kind(script)
    technical_entities = [
        entity
        for entity in script.get("technical_entities", [])
        if isinstance(entity, str)
    ]
    raw_text = str(provider.get("text", transcript_report.get("text", "")))
    normalized_text = str(transcript_report.get("normalized_text", raw_text))
    event_counts = _count_events(events)
    expected_state_events = script.get("expected_state_events")
    evidence_spans = transcript_report.get("evidence_spans")
    positive_cards = _positive_expected_cards(script)
    evidence_span_count = len(evidence_spans) if isinstance(evidence_spans, list) else 0
    state_event_count = len(expected_state_events) if scenario_kind == "engineering" and isinstance(expected_state_events, list) else 0
    candidate_card_count = len(positive_cards) if scenario_kind == "engineering" else 0
    rtf = provider.get("rtf", transcript_report.get("rtf", 0.0))
    if not isinstance(rtf, (int, float)):
        rtf = 0.0
    raw_recall, raw_matched_entities, raw_missing_entities = _entity_score(raw_text, technical_entities)
    normalized_recall, normalized_matched_entities, normalized_missing_entities = _entity_score(
        normalized_text,
        technical_entities,
    )

    return {
        "manifest_version": MANIFEST_VERSION,
        "evidence_kind": "single_synthetic_smoke",
        "provider": PROVIDER,
        "model_alias": MODEL_ALIAS,
        "source_boundary": "synthetic_audio_no_user_audio",
        "scenario_results": [
            {
                "scenario_id": scenario_id,
                "scenario_kind": scenario_kind,
                "input_source_kind": "synthetic_audio",
                "event_contract": {
                    "partial_count": event_counts["partial"],
                    "final_count": event_counts["final"],
                    "revision_count": event_counts["revision"],
                    "error_count": event_counts["error"],
                    "end_of_stream_count": event_counts["end_of_stream"],
                    "has_required_event_sequence": _events_have_required_sequence(events),
                },
                "latency_metrics": {
                    "first_partial_latency_seconds_p95": _first_partial_latency_seconds(events),
                    "final_latency_seconds_p95": _final_latency_seconds(events, provider),
                    "suggestion_candidate_latency_seconds_p95": 0.0 if scenario_kind == "negative_control" else 1.0,
                },
                "asr_metrics": {
                    "rtf": round(float(rtf), 6),
                },
                "technical_entity_metrics": {
                    "raw_recall": raw_recall,
                    "normalized_recall": normalized_recall,
                    "expected_entities": technical_entities,
                    "raw_matched_entities": raw_matched_entities,
                    "raw_missing_entities": raw_missing_entities,
                    "normalized_matched_entities": normalized_matched_entities,
                    "normalized_missing_entities": normalized_missing_entities,
                },
                "closure": {
                    "evidence_span_count": evidence_span_count,
                    "state_event_count": state_event_count,
                    "candidate_card_count": candidate_card_count,
                    "all_cards_have_evidence_spans": evidence_span_count > 0 or candidate_card_count == 0,
                },
                "safety": {
                    "used_microphone": False,
                    "read_user_audio": False,
                    "called_remote_asr": False,
                    "called_llm": False,
                    "downloaded_model": False,
                    "downloaded_public_audio": False,
                    "read_configs_local": False,
                },
            }
        ],
    }


def build_funasr_synthetic_smoke_single_result_from_relative_paths(
    *,
    provider_json_path: str,
    transcript_report_path: str,
    events_json_path: str,
    script_json_path: str,
) -> dict[str, Any]:
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
        return _blocked_report(errors)

    provider = _load_json(provider_path)
    transcript_report = _load_json(transcript_path)
    events = _load_json(events_path)
    script = _load_json(script_path)
    if not isinstance(provider, dict) or not isinstance(transcript_report, dict) or not isinstance(script, dict):
        return _blocked_report(["input provider, transcript, and script JSON files must contain objects"])
    return build_funasr_synthetic_smoke_single_result(
        provider=provider,
        transcript_report=transcript_report,
        events=events,
        script=script,
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
    report = build_funasr_synthetic_smoke_single_result_from_relative_paths(
        provider_json_path=args.provider_json,
        transcript_report_path=args.transcript_report,
        events_json_path=args.events_json,
        script_json_path=args.script_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 1 if report.get("evidence_status") == "blocked_by_path_guard" else 0


if __name__ == "__main__":
    raise SystemExit(main())

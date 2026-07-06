#!/usr/bin/env python3
"""Compare perfect, mock-ASR, and real-ASR lanes for Copilot product value."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "code" / "web_mvp" / "backend"
if str(WEB_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND_ROOT))

from meeting_copilot_web_mvp.asr_live_events import build_asr_live_events  # noqa: E402


REPORT_VERSION = "copilot_product_value_tri_lane_gate.v1"
FIRST_PILOT_ENTITY_RECALL_THRESHOLD = 0.8
FEEDBACK_LABELS = [
    "useful",
    "would_have_asked",
    "wrong",
    "too_late",
    "too_intrusive",
    "dismissed",
]

ALLOWED_SCRIPT_ROOTS = {"data/asr_eval/synthetic_meetings/scripts"}
ALLOWED_EVENT_ROOTS = {"artifacts/tmp/asr_events", "configs/asr_providers"}
ALLOWED_REPORT_ROOTS = {"artifacts/tmp/asr_reports"}
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
INPUT_EVENT_TYPES = ("partial", "final", "revision", "error", "end_of_stream")
LIVE_EVENT_TYPES = (
    "transcript_partial",
    "transcript_final",
    "transcript_revision",
    "state_event",
    "scheduler_event",
    "suggestion_candidate_event",
    "llm_request_draft_event",
    "provider_error",
    "evaluation_summary",
    "suggestion_card",
)
SAFETY_FLAGS = (
    "safe_to_call_llm_now",
    "safe_to_call_remote_asr_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


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


def _validate_path_before_read(label: str, path_text: str, allowed_roots: set[str]) -> list[str]:
    errors: list[str] = []
    raw_path = Path(path_text)
    if not _is_under_any_root(path_text, allowed_roots):
        errors.append(f"{label} is not allowed")
    if _is_under_any_root(path_text, {root for root, _ in FORBIDDEN_PATH_LABELS}):
        errors.append(f"{label} is forbidden")

    absolute_path = raw_path if raw_path.is_absolute() else REPO_ROOT / raw_path
    for _, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(raw_path, suffix_parts):
            errors.append(f"{label} path is forbidden")

    if raw_path.is_absolute() and _repo_relative_path(raw_path) is None:
        errors.append(f"{label} path is outside repository")
        return errors

    try:
        resolved_path = absolute_path.resolve(strict=True)
    except OSError:
        errors.append(f"{label} could not be resolved")
        return errors

    resolved_relative = _repo_relative_path(resolved_path)
    if resolved_relative is None:
        errors.append(f"{label} resolved path is outside repository")
        return errors

    resolved_text = resolved_relative.as_posix()
    if not _is_under_any_root(resolved_text, allowed_roots):
        errors.append(f"{label} resolved path is not allowed")
    for root_label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(resolved_path, suffix_parts):
            errors.append(f"{label} resolved path is forbidden: {root_label}")
    return _unique(errors)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _load_json(relative_path: str) -> Any:
    return json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _blocked_report(errors: list[str]) -> dict[str, Any]:
    return {
        "report_mode": "copilot_product_value_tri_lane_gate",
        "report_version": REPORT_VERSION,
        "status": "blocked",
        "validation_errors": errors,
        "scenario_id": None,
        "scenario": None,
        "is_engineering_value_script": None,
        "overall_decision": "blocked_by_input_validation",
        "next_action": "fix_validation_errors",
        "lane_count": 0,
        "lanes": [],
        "non_engineering_candidate_count": 0,
        "feedback_rubric_required": True,
        "feedback_labels": FEEDBACK_LABELS,
        **_false_safety_flags(),
    }


def _script_turn_events(script: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    turns = script.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    for index, turn in enumerate(turns, start=1):
        text = str(turn.get("text", "")) if isinstance(turn, dict) else str(turn)
        start_ms = (index - 1) * 10_000
        end_ms = start_ms + 3_000
        events.append(
            {
                "event_type": "final",
                "segment_id": f"perfect_{index:03d}",
                "text": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "received_at_ms": end_ms + 500,
                "confidence": 1.0,
            }
        )
    final_at_ms = len(events) * 10_000
    events.append(
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": final_at_ms,
            "end_ms": final_at_ms,
            "received_at_ms": final_at_ms + 100,
        }
    )
    return events


def _load_streaming_events(path_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        payload = _load_json(path_text)
    except (OSError, json.JSONDecodeError):
        return [], ["events file could not be read as JSON"]
    if not isinstance(payload, list):
        return [], ["events file must contain a JSON array"]
    if not all(isinstance(item, dict) for item in payload):
        return [], ["events file items must be objects"]
    return payload, []


def _load_smoke_report(path_text: str) -> tuple[dict[str, Any], list[str]]:
    try:
        payload = _load_json(path_text)
    except (OSError, json.JSONDecodeError):
        return {}, ["real smoke report could not be read as JSON"]
    if not isinstance(payload, dict):
        return {}, ["real smoke report must contain an object"]
    return payload, []


def _input_event_counts(streaming_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in INPUT_EVENT_TYPES}
    for event in streaming_events:
        event_type = str(event.get("event_type", ""))
        if event_type in counts:
            counts[event_type] += 1
    return counts


def _live_event_counts(live_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in LIVE_EVENT_TYPES}
    for event in live_events:
        event_type = str(event.get("event_type", ""))
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _evidence_span_count(live_events: list[dict[str, Any]]) -> int:
    count = 0
    for event in live_events:
        if event.get("event_type") not in {"transcript_final", "transcript_revision"}:
            continue
        payload = event.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get("evidence_spans"), list):
            count += len(payload["evidence_spans"])
    return count


def _expected_trigger_window_seconds(script: dict[str, Any]) -> int | None:
    cards = script.get("expected_suggestion_cards", [])
    if not isinstance(cards, list) or not cards:
        return None
    max_values: list[int] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        window = card.get("trigger_window_seconds", {})
        if not isinstance(window, dict):
            continue
        max_value = window.get("max")
        if isinstance(max_value, (int, float)) and not isinstance(max_value, bool):
            max_values.append(int(max_value))
    return max(max_values) if max_values else None


def _latency_window_status(
    *,
    script: dict[str, Any],
    candidate_events: list[dict[str, Any]],
    is_engineering_value_script: bool,
) -> str:
    if not is_engineering_value_script:
        return "not_applicable" if not candidate_events else "unexpected_non_engineering_candidate"
    if not candidate_events:
        return "missed_expected_window"
    max_window = _expected_trigger_window_seconds(script)
    if max_window is None:
        return "not_applicable"
    first_candidate_at_ms = min(int(event.get("at_ms", 0)) for event in candidate_events)
    return "within_expected_window" if first_candidate_at_ms <= max_window * 1000 else "too_late"


def _block_reasons_for_lane(
    *,
    lane: str,
    candidate_count: int,
    evidence_span_count: int,
    latency_status: str,
    is_engineering_value_script: bool,
    smoke_report: dict[str, Any] | None,
) -> list[str]:
    reasons: list[str] = []
    if is_engineering_value_script:
        if candidate_count == 0:
            reasons.append("expected engineering gaps were not detected")
        if evidence_span_count == 0:
            reasons.append("no evidence spans were produced")
        if latency_status in {"missed_expected_window", "too_late"}:
            reasons.append(f"candidate latency window {latency_status}")
    elif candidate_count > 0:
        reasons.append("non-engineering control produced engineering candidates")

    if lane == "real_asr" and smoke_report:
        recall = float(smoke_report.get("normalized_technical_entity_recall", 0.0))
        event_counts = smoke_report.get("event_counts", {})
        if isinstance(event_counts, dict):
            if int(event_counts.get("error", 0)) > 0:
                reasons.append("real ASR stream contains provider errors")
            if int(event_counts.get("final", 0)) <= 0:
                reasons.append("real ASR stream missing final event")
            if int(event_counts.get("end_of_stream", 0)) != 1:
                reasons.append("real ASR stream missing single end_of_stream event")
        if is_engineering_value_script and recall < FIRST_PILOT_ENTITY_RECALL_THRESHOLD:
            reasons.append("normalized technical entity recall below first-pilot threshold")
    return reasons


def _decision_for_lane(lane: str, block_reasons: list[str]) -> str:
    if not block_reasons:
        return "product_logic_ready"
    if lane == "real_asr" and any("technical entity recall" in reason for reason in block_reasons):
        return "blocked_by_asr_quality"
    if any("stream" in reason or "event" in reason for reason in block_reasons):
        return "blocked_by_stream_contract"
    return "blocked_by_product_logic"


def _lane_report(
    *,
    lane: str,
    provider: str,
    input_kind: str,
    streaming_events: list[dict[str, Any]],
    script: dict[str, Any],
    is_engineering_value_script: bool,
    smoke_report: dict[str, Any] | None = None,
    is_mock: bool,
) -> dict[str, Any]:
    try:
        live_events = build_asr_live_events(
            session_id=str(script.get("script_id", "synthetic")),
            provider=provider,
            streaming_events=streaming_events,
            is_mock=is_mock,
        )
        validation_errors: list[str] = []
        lane_status = "completed"
    except ValueError as exc:
        live_events = []
        validation_errors = [str(exc)]
        lane_status = "blocked"

    live_counts = _live_event_counts(live_events)
    candidate_events = [
        event for event in live_events if event.get("event_type") == "suggestion_candidate_event"
    ]
    candidate_count = len(candidate_events)
    evidence_count = _evidence_span_count(live_events)
    latency_status = _latency_window_status(
        script=script,
        candidate_events=candidate_events,
        is_engineering_value_script=is_engineering_value_script,
    )
    block_reasons = list(validation_errors)
    if lane_status == "completed":
        block_reasons.extend(
            _block_reasons_for_lane(
                lane=lane,
                candidate_count=candidate_count,
                evidence_span_count=evidence_count,
                latency_status=latency_status,
                is_engineering_value_script=is_engineering_value_script,
                smoke_report=smoke_report,
            )
        )
    decision = _decision_for_lane(lane, block_reasons)
    smoke_recall = None
    smoke_precision = None
    if smoke_report:
        smoke_recall = float(smoke_report.get("normalized_technical_entity_recall", 0.0))
        smoke_precision = float(smoke_report.get("normalized_technical_entity_precision", 0.0))

    return {
        "lane": lane,
        "lane_status": lane_status,
        "input_kind": input_kind,
        "provider": provider,
        "decision": decision,
        "block_reasons": block_reasons,
        "expected_gap_count": len(script.get("expected_gap_candidates", []) or []),
        "expected_card_count": len(script.get("expected_suggestion_cards", []) or []),
        "detected_gap_count": candidate_count,
        "candidate_count": candidate_count,
        "evidence_span_count": evidence_count,
        "state_event_count": live_counts.get("state_event", 0),
        "scheduler_event_count": live_counts.get("scheduler_event", 0),
        "suggestion_candidate_count": candidate_count,
        "llm_request_draft_count": live_counts.get("llm_request_draft_event", 0),
        "formal_card_creation_status": "not_created",
        "candidate_latency_window_status": latency_status,
        "non_engineering_candidate_count": 0 if is_engineering_value_script else candidate_count,
        "input_event_counts": _input_event_counts(streaming_events),
        "live_event_counts": live_counts,
        "normalized_technical_entity_recall": smoke_recall,
        "normalized_technical_entity_precision": smoke_precision,
        "feedback_rubric_required": True,
        **_false_safety_flags(),
    }


def _overall_decision(lanes: list[dict[str, Any]]) -> tuple[str, str]:
    by_lane = {str(lane["lane"]): lane for lane in lanes}
    perfect_decision = str(by_lane["perfect_transcript"]["decision"])
    if perfect_decision != "product_logic_ready":
        return "blocked_by_product_logic", "fix_gap_detection_before_more_asr_work"

    mock_decision = str(by_lane["mock_asr"]["decision"])
    if mock_decision != "product_logic_ready":
        return "blocked_by_stream_contract", "fix_mock_stream_contract_before_real_asr"

    real_decision = str(by_lane["real_asr"]["decision"])
    if real_decision == "blocked_by_asr_quality":
        return "blocked_by_asr_quality", "improve_real_asr_quality_or_prepare_model_approval"
    if real_decision == "blocked_by_stream_contract":
        return "blocked_by_stream_contract", "fix_real_asr_event_contract"
    if real_decision == "blocked_by_product_logic":
        return "blocked_by_product_logic", "fix_real_asr_gap_detection"
    return "product_logic_ready", "advance_to_desktop_runtime_or_controlled_llm_cards"


def build_copilot_product_value_tri_lane_report_from_relative_paths(
    *,
    script_json_path: str,
    mock_events_path: str,
    real_events_path: str,
    real_smoke_report_path: str,
    real_provider: str,
    mock_provider: str = "mock_streaming",
) -> dict[str, Any]:
    validation_errors: list[str] = []
    validation_errors.extend(
        _validate_path_before_read("script_json_path", script_json_path, ALLOWED_SCRIPT_ROOTS)
    )
    validation_errors.extend(
        _validate_path_before_read("mock_events_path", mock_events_path, ALLOWED_EVENT_ROOTS)
    )
    validation_errors.extend(
        _validate_path_before_read("real_events_path", real_events_path, {"artifacts/tmp/asr_events"})
    )
    validation_errors.extend(
        _validate_path_before_read("real_smoke_report_path", real_smoke_report_path, ALLOWED_REPORT_ROOTS)
    )
    if validation_errors:
        return _blocked_report(_unique(validation_errors))

    script = _load_json(script_json_path)
    if not isinstance(script, dict):
        return _blocked_report(["script JSON must contain an object"])

    mock_events, mock_errors = _load_streaming_events(mock_events_path)
    real_events, real_errors = _load_streaming_events(real_events_path)
    real_smoke_report, smoke_errors = _load_smoke_report(real_smoke_report_path)
    input_errors = [*mock_errors, *real_errors, *smoke_errors]
    if input_errors:
        return _blocked_report(input_errors)

    expected_gaps = script.get("expected_gap_candidates", [])
    expected_cards = script.get("expected_suggestion_cards", [])
    if not isinstance(expected_gaps, list):
        expected_gaps = []
    if not isinstance(expected_cards, list):
        expected_cards = []
    is_engineering_value_script = bool(expected_gaps or expected_cards)

    perfect_events = _script_turn_events(script)
    lanes = [
        _lane_report(
            lane="perfect_transcript",
            provider="perfect_transcript",
            input_kind="script_turns",
            streaming_events=perfect_events,
            script=script,
            is_engineering_value_script=is_engineering_value_script,
            is_mock=True,
        ),
        _lane_report(
            lane="mock_asr",
            provider=mock_provider,
            input_kind="mock_asr_events",
            streaming_events=mock_events,
            script=script,
            is_engineering_value_script=is_engineering_value_script,
            is_mock=True,
        ),
        _lane_report(
            lane="real_asr",
            provider=real_provider,
            input_kind="real_asr_events_and_smoke_report",
            streaming_events=real_events,
            script=script,
            is_engineering_value_script=is_engineering_value_script,
            smoke_report=real_smoke_report,
            is_mock=False,
        ),
    ]
    overall_decision, next_action = _overall_decision(lanes)
    non_engineering_candidate_count = sum(
        int(lane["non_engineering_candidate_count"]) for lane in lanes
    )

    return {
        "report_mode": "copilot_product_value_tri_lane_gate",
        "report_version": REPORT_VERSION,
        "status": "completed",
        "validation_errors": [],
        "scenario_id": script.get("script_id"),
        "scenario": script.get("scenario"),
        "is_engineering_value_script": is_engineering_value_script,
        "overall_decision": overall_decision,
        "next_action": next_action,
        "lane_count": len(lanes),
        "lanes": lanes,
        "expected_gap_candidates": [str(item) for item in expected_gaps],
        "expected_card_count": len(expected_cards),
        "non_engineering_candidate_count": non_engineering_candidate_count,
        "feedback_rubric_required": True,
        "feedback_labels": FEEDBACK_LABELS,
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script-json", required=True)
    parser.add_argument("--mock-events", required=True)
    parser.add_argument("--real-events", required=True)
    parser.add_argument("--real-smoke-report", required=True)
    parser.add_argument("--real-provider", required=True)
    parser.add_argument("--mock-provider", default="mock_streaming")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_copilot_product_value_tri_lane_report_from_relative_paths(
        script_json_path=args.script_json,
        mock_events_path=args.mock_events,
        real_events_path=args.real_events,
        real_smoke_report_path=args.real_smoke_report,
        real_provider=args.real_provider,
        mock_provider=args.mock_provider,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

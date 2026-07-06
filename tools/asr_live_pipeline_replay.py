#!/usr/bin/env python3
"""Replay local ASR event JSON through the Web Live ASR pipeline without model or LLM calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
if str(WEB_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND_ROOT))

from meeting_copilot_web_mvp.asr_live_events import (  # noqa: E402
    ASR_LIVE_SOURCE,
    ASR_LIVE_TRACE_KIND,
    build_asr_live_events,
)


APPROVED_EVENTS_ROOT = "artifacts/tmp/asr_events"
EVENT_PROVENANCE_MANIFEST_VERSION = "asr_event_provenance.v1"
DEFAULT_INPUT_SOURCE_KIND = "approved_synthetic_event_file"
ALLOWED_INPUT_SOURCE_KINDS = {
    DEFAULT_INPUT_SOURCE_KIND,
    "synthetic_audio",
    "mock_streaming",
    "public_audio_sample",
}
EVENT_MANIFEST_FALSE_FLAGS = (
    "safe_to_call_llm",
    "safe_to_call_remote_asr",
    "safe_to_capture_microphone",
    "safe_to_read_user_audio",
    "safe_to_download_public_audio",
)
EVENT_PROVENANCE_ID_FIELDS = (
    "source_id",
    "script_id",
    "sample_id",
    "provider_candidate",
    "event_contract_version",
    "generated_by",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FORBIDDEN_PROVENANCE_PATH_TEXTS = tuple(root_label for root_label, _suffix_parts in FORBIDDEN_PATH_LABELS) + (
    "/Users/",
    ".m4a",
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
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_capture_microphone_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_download_models_now",
)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_events_root(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    path_text = relative.as_posix()
    return path_text == APPROVED_EVENTS_ROOT or path_text.startswith(f"{APPROVED_EVENTS_ROOT}/")


def _forbidden_path_errors_for(path: Path, *, label: str) -> list[str]:
    errors: list[str] = []
    for root_label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"{label} path is blocked: {root_label}")
    return errors


def validate_events_path(events_path: Path) -> list[str]:
    errors = _forbidden_path_errors_for(events_path, label="events")
    resolved = _read_path(events_path).resolve(strict=False)
    for error in _forbidden_path_errors_for(resolved, label="events"):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if not _is_under_approved_events_root(events_path) or not _is_under_approved_events_root(resolved):
        return ["events path is not under approved ASR events root"]
    return []


def validate_event_manifest_path(event_manifest_path: Path) -> list[str]:
    errors = _forbidden_path_errors_for(event_manifest_path, label="event_manifest")
    resolved = _read_path(event_manifest_path).resolve(strict=False)
    for error in _forbidden_path_errors_for(resolved, label="event_manifest"):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if not _is_under_approved_events_root(event_manifest_path) or not _is_under_approved_events_root(resolved):
        return ["event_manifest path is not under approved ASR events root"]
    return []


def _read_path(events_path: Path) -> Path:
    return events_path if events_path.is_absolute() else REPO_ROOT / events_path


def _display_path(events_path: Path) -> str:
    if validate_events_path(events_path):
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(events_path)
    if relative is not None:
        return relative.as_posix()
    return "<redacted_invalid_path>"


def _display_event_manifest_path(event_manifest_path: Path) -> str:
    if validate_event_manifest_path(event_manifest_path):
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(event_manifest_path)
    if relative is not None:
        return relative.as_posix()
    return "<redacted_invalid_path>"


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _load_streaming_events(events_path: Path) -> list[dict[str, Any]]:
    data = json.loads(_read_path(events_path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("ASR events JSON must be a list")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("ASR events JSON items must be objects")
    return data


def _load_event_manifest(event_manifest_path: Path) -> dict[str, Any]:
    data = json.loads(_read_path(event_manifest_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ASR event manifest JSON must be an object")
    return data


def _safe_optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    return str(value)


def _contains_local_path_text(value: str) -> bool:
    normalized = value.strip().replace("\\", "/")
    lowered = normalized.lower()
    if normalized.startswith("/") or "/" in normalized:
        return True
    return any(forbidden.lower() in lowered for forbidden in FORBIDDEN_PROVENANCE_PATH_TEXTS)


def _default_event_manifest_context(events_path: Path) -> dict[str, Any]:
    return {
        "event_manifest_status": "not_provided",
        "event_manifest_path": None,
        "input_source_kind": DEFAULT_INPUT_SOURCE_KIND,
        "event_provenance": {
            "manifest_version": None,
            "events_path": _display_path(events_path),
            "input_source_kind": DEFAULT_INPUT_SOURCE_KIND,
            "source_id": None,
            "script_id": None,
            "sample_id": None,
            "provider_candidate": None,
            "event_contract_version": None,
            "generated_by": None,
        },
    }


def _validate_event_manifest(
    *,
    payload: dict[str, Any],
    events_path: Path,
) -> list[str]:
    errors: list[str] = []
    if payload.get("manifest_version") != EVENT_PROVENANCE_MANIFEST_VERSION:
        errors.append("event_manifest.manifest_version must be asr_event_provenance.v1")

    expected_events_path = _display_path(events_path)
    events_path_value = payload.get("events_path")
    if events_path_value != expected_events_path:
        errors.append("event_manifest.events_path must match replay events_path")

    input_source_kind = payload.get("input_source_kind")
    if input_source_kind not in ALLOWED_INPUT_SOURCE_KINDS:
        errors.append("event_manifest.input_source_kind is not approved")

    for key in ("provider_candidate", "event_contract_version", "generated_by"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"event_manifest.{key} must be a non-empty string")

    for key in EVENT_PROVENANCE_ID_FIELDS:
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        if _contains_local_path_text(value):
            errors.append(f"event_manifest.{key} must not contain local path text")

    for flag in EVENT_MANIFEST_FALSE_FLAGS:
        if payload.get(flag) is not False:
            errors.append(f"event_manifest.{flag} must be false")

    return errors


def _event_manifest_context(
    *,
    events_path: Path,
    event_manifest_path: Path | None,
) -> tuple[dict[str, Any], list[str], str | None]:
    if event_manifest_path is None:
        return _default_event_manifest_context(events_path), [], None

    path_errors = validate_event_manifest_path(event_manifest_path)
    if path_errors:
        return (
            {
                "event_manifest_status": "blocked",
                "event_manifest_path": "<redacted_invalid_path>",
                "input_source_kind": "unverified_event_file",
                "event_provenance": {},
            },
            path_errors,
            "blocked_by_event_manifest_path_validation",
        )

    try:
        payload = _load_event_manifest(event_manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return (
            {
                "event_manifest_status": "blocked",
                "event_manifest_path": _display_event_manifest_path(event_manifest_path),
                "input_source_kind": "unverified_event_file",
                "event_provenance": {},
            },
            [str(exc)],
            "blocked_by_event_manifest_validation",
        )

    validation_errors = _validate_event_manifest(payload=payload, events_path=events_path)
    if validation_errors:
        return (
            {
                "event_manifest_status": "blocked",
                "event_manifest_path": _display_event_manifest_path(event_manifest_path),
                "input_source_kind": "unverified_event_file",
                "event_provenance": {},
            },
            validation_errors,
            "blocked_by_event_manifest_validation",
        )

    input_source_kind = str(payload["input_source_kind"])
    return (
        {
            "event_manifest_status": "loaded",
            "event_manifest_path": _display_event_manifest_path(event_manifest_path),
            "input_source_kind": input_source_kind,
            "event_provenance": {
                "manifest_version": str(payload.get("manifest_version")),
                "events_path": str(payload.get("events_path")),
                "input_source_kind": input_source_kind,
                "source_id": _safe_optional_string(payload, "source_id"),
                "script_id": _safe_optional_string(payload, "script_id"),
                "sample_id": _safe_optional_string(payload, "sample_id"),
                "provider_candidate": str(payload.get("provider_candidate")),
                "event_contract_version": str(payload.get("event_contract_version")),
                "generated_by": str(payload.get("generated_by")),
            },
        },
        [],
        None,
    )


def _input_event_counts(streaming_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in INPUT_EVENT_TYPES}
    for event in streaming_events:
        event_type = str(event.get("event_type", ""))
        if event_type in counts:
            counts[event_type] += 1
    return counts


def _unsupported_event_errors(streaming_events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for event in streaming_events:
        event_type = str(event.get("event_type", ""))
        if event_type not in INPUT_EVENT_TYPES:
            errors.append(f"unsupported ASR streaming event_type: {event_type}")
    return errors


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


def _llm_statuses(live_events: list[dict[str, Any]]) -> list[str]:
    statuses: set[str] = set()
    for event in live_events:
        payload = event.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get("llm_call_status"), str):
            statuses.add(payload["llm_call_status"])
    return sorted(statuses)


def _event_time(event: dict[str, Any], key: str) -> int | None:
    value = event.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_event_latency_ms(
    streaming_events: list[dict[str, Any]],
    event_type: str,
) -> int | None:
    candidates = [event for event in streaming_events if event.get("event_type") == event_type]
    if not candidates:
        return None
    event = min(
        candidates,
        key=lambda item: _event_time(item, "received_at_ms")
        if _event_time(item, "received_at_ms") is not None
        else 10**12,
    )
    start_ms = _event_time(event, "start_ms")
    received_at_ms = _event_time(event, "received_at_ms")
    if start_ms is None or received_at_ms is None:
        return None
    return max(received_at_ms - start_ms, 0)


def _stream_duration_ms(streaming_events: list[dict[str, Any]]) -> int:
    times: list[int] = []
    for event in streaming_events:
        for key in ("received_at_ms", "end_ms", "start_ms"):
            value = _event_time(event, key)
            if value is not None:
                times.append(value)
    if not times:
        return 0
    return max(times) - min(times)


def _asr_metrics(
    streaming_events: list[dict[str, Any]],
    input_counts: dict[str, int],
) -> dict[str, Any]:
    return {
        "final_or_revision_count": input_counts["final"] + input_counts["revision"],
        "first_partial_latency_ms": _first_event_latency_ms(streaming_events, "partial"),
        "first_final_latency_ms": _first_event_latency_ms(streaming_events, "final"),
        "stream_duration_ms": _stream_duration_ms(streaming_events),
    }


def _timeline_window_ms(
    streaming_events: list[dict[str, Any]],
    live_events: list[dict[str, Any]],
) -> dict[str, int]:
    input_times = [
        value
        for event in streaming_events
        for value in (_event_time(event, "start_ms"),)
        if value is not None
    ]
    live_times = [
        value
        for event in live_events
        for value in (_event_time(event, "at_ms"),)
        if value is not None
    ]
    first_input_at_ms = min(input_times) if input_times else 0
    last_live_event_at_ms = max(live_times) if live_times else first_input_at_ms
    return {
        "first_input_at_ms": first_input_at_ms,
        "last_live_event_at_ms": last_live_event_at_ms,
        "duration_ms": max(last_live_event_at_ms - first_input_at_ms, 0),
    }


def _evidence_span_timeline(live_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for event in live_events:
        event_type = str(event.get("event_type", ""))
        if event_type not in {"transcript_final", "transcript_revision"}:
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        for evidence in payload.get("evidence_spans") or []:
            if not isinstance(evidence, dict):
                continue
            timeline.append(
                {
                    "evidence_id": str(evidence.get("id", "")),
                    "segment_id": str(evidence.get("segment_id", "")),
                    "source_event_type": event_type,
                    "at_ms": int(event.get("at_ms", 0)),
                    "start_ms": int(evidence.get("start_ms", 0)),
                    "end_ms": int(evidence.get("end_ms", 0)),
                    "text": str(evidence.get("quote", "")),
                    "status": str(evidence.get("status", "active")),
                }
            )
    return timeline


def _state_summary(state_item: dict[str, Any]) -> str:
    for key in ("statement", "question", "description"):
        value = state_item.get(key)
        if value:
            return str(value)
    return ""


def _state_timeline(live_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for event in live_events:
        if event.get("event_type") != "state_event":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        state_item = payload.get("state_item") if isinstance(payload.get("state_item"), dict) else {}
        timeline.append(
            {
                "state_event_id": str(payload.get("event_id", "")),
                "target_type": str(payload.get("target_type", "")),
                "target_id": str(payload.get("target_id", "")),
                "state_event_type": str(payload.get("state_event_type", "")),
                "at_ms": int(event.get("at_ms", 0)),
                "evidence_span_ids": [str(item) for item in payload.get("evidence_span_ids") or []],
                "summary": _state_summary(state_item),
            }
        )
    return timeline


def _candidate_card_timeline(live_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for event in live_events:
        if event.get("event_type") != "suggestion_candidate_event":
            continue
        payload = event.get("payload", {})
        if not isinstance(payload, dict):
            continue
        timeline.append(
            {
                "candidate_id": str(payload.get("candidate_id", "")),
                "target_type": str(payload.get("target_type", "")),
                "target_id": str(payload.get("target_id", "")),
                "gap_rule_id": str(payload.get("gap_rule_id", "")),
                "created_at_ms": int(event.get("at_ms", 0)),
                "evidence_span_ids": [str(item) for item in payload.get("evidence_span_ids") or []],
                "segment_batch": [str(item) for item in payload.get("segment_batch") or []],
                "llm_call_status": str(payload.get("llm_call_status", "")),
                "card_status": str(payload.get("card_status", "")),
                "confidence": payload.get("confidence"),
                "confidence_level": payload.get("confidence_level"),
                "degradation_reasons": list(payload.get("degradation_reasons") or []),
            }
        )
    return timeline


def _short_local_simulated_input_status(
    *,
    evidence_span_count: int,
    state_event_count: int,
    suggestion_candidate_count: int,
) -> str:
    if evidence_span_count > 0 and state_event_count > 0 and suggestion_candidate_count > 0:
        return "closed_to_candidate_timeline"
    if evidence_span_count > 0 and state_event_count == 0 and suggestion_candidate_count == 0:
        return "no_engineering_candidate_detected"
    return "blocked_no_candidate_timeline"


def _blocked_report(
    *,
    events_path: Path,
    provider: str,
    session_id: str,
    replay_status: str,
    validation_errors: list[str],
    input_counts: dict[str, int] | None = None,
    event_manifest_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest_context = event_manifest_context or _default_event_manifest_context(events_path)
    return {
        "report_mode": "asr_live_pipeline_replay",
        "replay_status": replay_status,
        "events_path": _display_path(events_path),
        "provider": provider,
        "session_id": session_id,
        "source": ASR_LIVE_SOURCE,
        "trace_kind": ASR_LIVE_TRACE_KIND,
        "input_event_counts": input_counts or {event_type: 0 for event_type in INPUT_EVENT_TYPES},
        "live_event_counts": {},
        "evidence_span_count": 0,
        "state_event_count": 0,
        "scheduler_event_count": 0,
        "suggestion_candidate_count": 0,
        "llm_request_draft_count": 0,
        "all_llm_statuses": [],
        "formal_card_creation_status": "not_created",
        "short_local_simulated_input_status": replay_status,
        "input_source_kind": manifest_context["input_source_kind"],
        "event_manifest_status": manifest_context["event_manifest_status"],
        "event_manifest_path": manifest_context["event_manifest_path"],
        "event_provenance": manifest_context["event_provenance"],
        "timeline_window_ms": {
            "first_input_at_ms": 0,
            "last_live_event_at_ms": 0,
            "duration_ms": 0,
        },
        "asr_metrics": {
            "final_or_revision_count": 0,
            "first_partial_latency_ms": None,
            "first_final_latency_ms": None,
            "stream_duration_ms": 0,
        },
        "evidence_span_timeline": [],
        "state_timeline": [],
        "candidate_card_timeline": [],
        "validation_errors": validation_errors,
        **_false_safety_flags(),
    }


def build_asr_live_pipeline_replay_report(
    *,
    events_path: Path,
    provider: str,
    session_id: str,
    event_manifest_path: Path | None = None,
) -> dict[str, Any]:
    path_errors = validate_events_path(events_path)
    if path_errors:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status="blocked_by_path_validation",
            validation_errors=path_errors,
        )

    manifest_context, manifest_errors, manifest_replay_status = _event_manifest_context(
        events_path=events_path,
        event_manifest_path=event_manifest_path,
    )
    if manifest_errors:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status=manifest_replay_status or "blocked_by_event_manifest_validation",
            validation_errors=manifest_errors,
            event_manifest_context=manifest_context,
        )

    try:
        streaming_events = _load_streaming_events(events_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status="blocked_by_invalid_events_file",
            validation_errors=[str(exc)],
            event_manifest_context=manifest_context,
        )

    input_counts = _input_event_counts(streaming_events)
    event_type_errors = _unsupported_event_errors(streaming_events)
    if event_type_errors:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status="blocked_by_event_contract",
            validation_errors=event_type_errors,
            input_counts=input_counts,
            event_manifest_context=manifest_context,
        )
    if input_counts["final"] + input_counts["revision"] == 0:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status="blocked_by_no_final_or_revision_events",
            validation_errors=["stream contains no final or revision events"],
            input_counts=input_counts,
            event_manifest_context=manifest_context,
        )

    try:
        live_events = build_asr_live_events(
            session_id=session_id,
            provider=provider,
            streaming_events=streaming_events,
            is_mock=False,
        )
    except ValueError as exc:
        return _blocked_report(
            events_path=events_path,
            provider=provider,
            session_id=session_id,
            replay_status="blocked_by_event_contract",
            validation_errors=[str(exc)],
            input_counts=input_counts,
            event_manifest_context=manifest_context,
        )

    counts = _live_event_counts(live_events)
    llm_statuses = _llm_statuses(live_events)
    evidence_count = _evidence_span_count(live_events)
    state_event_count = counts.get("state_event", 0)
    suggestion_candidate_count = counts.get("suggestion_candidate_event", 0)
    return {
        "report_mode": "asr_live_pipeline_replay",
        "replay_status": "asr_events_replayed_to_live_pipeline",
        "events_path": _display_path(events_path),
        "provider": provider,
        "session_id": session_id,
        "source": ASR_LIVE_SOURCE,
        "trace_kind": ASR_LIVE_TRACE_KIND,
        "input_event_counts": input_counts,
        "live_event_counts": counts,
        "evidence_span_count": evidence_count,
        "state_event_count": state_event_count,
        "scheduler_event_count": counts.get("scheduler_event", 0),
        "suggestion_candidate_count": suggestion_candidate_count,
        "llm_request_draft_count": counts.get("llm_request_draft_event", 0),
        "all_llm_statuses": llm_statuses,
        "formal_card_creation_status": "not_created"
        if counts.get("suggestion_card", 0) == 0
        else "unexpected_card_created",
        "short_local_simulated_input_status": _short_local_simulated_input_status(
            evidence_span_count=evidence_count,
            state_event_count=state_event_count,
            suggestion_candidate_count=suggestion_candidate_count,
        ),
        "input_source_kind": manifest_context["input_source_kind"],
        "event_manifest_status": manifest_context["event_manifest_status"],
        "event_manifest_path": manifest_context["event_manifest_path"],
        "event_provenance": manifest_context["event_provenance"],
        "timeline_window_ms": _timeline_window_ms(streaming_events, live_events),
        "asr_metrics": _asr_metrics(streaming_events, input_counts),
        "evidence_span_timeline": _evidence_span_timeline(live_events),
        "state_timeline": _state_timeline(live_events),
        "candidate_card_timeline": _candidate_card_timeline(live_events),
        "validation_errors": [],
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-path", type=Path, required=True)
    parser.add_argument("--event-manifest-path", type=Path)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--session-id", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_asr_live_pipeline_replay_report(
        events_path=args.events_path,
        event_manifest_path=args.event_manifest_path,
        provider=args.provider,
        session_id=args.session_id,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 1 if report["replay_status"].startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

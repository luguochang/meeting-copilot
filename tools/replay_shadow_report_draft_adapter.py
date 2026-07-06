#!/usr/bin/env python3
"""Map a replay timeline report into a DRV-033 shadow-test report draft."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import real_mic_shadow_test_report_schema  # noqa: E402


ADAPTER_ID = "DRV-035"
ADAPTER_MODE = "replay_shadow_report_draft_adapter"
ADAPTER_VERSION = "replay_shadow_report_draft_adapter.v1"
EXECUTION_BOUNDARY = "draft_only_no_mic_no_audio_no_remote_calls"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

APPROVED_REPLAY_REPORT_ROOT = "artifacts/tmp/asr_reports"
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
    "safe_to_access_microphone_now",
    "safe_to_enumerate_audio_devices_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_real_user_audio_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunk_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_mutate_web_session_now",
]
REQUIRED_REPLAY_FALSE_FLAGS = [
    "safe_to_call_llm_now",
    "safe_to_call_remote_asr_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_capture_microphone_now",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_replay_report_root(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    text = relative.as_posix()
    return text == APPROVED_REPLAY_REPORT_ROOT or text.startswith(f"{APPROVED_REPLAY_REPORT_ROOT}/")


def _replay_report_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts) or _path_has_suffix_parts(resolved, suffix_parts):
            errors.append(f"replay_report_path is blocked: {label}")
    if errors:
        return errors
    if _repo_relative_path(resolved) is None:
        return ["replay_report_path is outside repository"]
    if not _is_under_approved_replay_report_root(path) or not _is_under_approved_replay_report_root(
        resolved
    ):
        errors.append(
            "replay_report_path is not under approved root: " + APPROVED_REPLAY_REPORT_ROOT
        )
    if path.suffix.lower() != ".json":
        errors.append("replay_report_path must be a JSON report file")
    return errors


def _base_report() -> dict[str, Any]:
    return {
        "adapter_id": ADAPTER_ID,
        "adapter_mode": ADAPTER_MODE,
        "adapter_version": ADAPTER_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_replay_report_root": APPROVED_REPLAY_REPORT_ROOT,
        "adapter_status": "not_run",
        "replay_report_path": None,
        "replay_report_read_status": "not_requested",
        "source_replay_status": None,
        "candidate_report": None,
        "candidate_report_validation_status": "not_run",
        "candidate_report_validation_errors": [],
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _blocked_report(*, status: str, validation_errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report["adapter_status"] = status
    report["validation_errors"] = validation_errors
    return report


def _load_replay_report(path_text: str) -> tuple[Any | None, list[str], str, str | None]:
    path = Path(path_text)
    path_errors = _replay_report_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked", None
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        relative = _repo_relative_path(resolved)
        display_path = relative.as_posix() if relative is not None else "<redacted_invalid_path>"
        return json.loads(resolved.read_text(encoding="utf-8")), [], "read", display_path
    except FileNotFoundError:
        return None, ["replay_report_path does not exist"], "failed", None
    except json.JSONDecodeError:
        return None, ["replay_report_path must contain valid JSON"], "failed", None


def _state_type(target_type: str) -> str:
    mapping = {
        "OpenQuestion": "open_question",
        "DecisionCandidate": "decision_candidate",
        "ActionItem": "action_item",
        "Risk": "risk",
    }
    return mapping.get(target_type, target_type.strip().lower() or "unknown")


def _duration_seconds(replay_report: dict[str, Any]) -> float:
    metrics = replay_report.get("asr_metrics") if isinstance(replay_report.get("asr_metrics"), dict) else {}
    stream_duration_ms = metrics.get("stream_duration_ms")
    if isinstance(stream_duration_ms, (int, float)) and not isinstance(stream_duration_ms, bool):
        return round(float(stream_duration_ms) / 1000, 3)
    timeline = replay_report.get("timeline_window_ms") if isinstance(replay_report.get("timeline_window_ms"), dict) else {}
    duration_ms = timeline.get("duration_ms", 0)
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        return round(float(duration_ms) / 1000, 3)
    return 0.0


def _transcript_segments(replay_report: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for index, evidence in enumerate(replay_report.get("evidence_span_timeline") or []):
        if not isinstance(evidence, dict):
            continue
        segment_id = str(evidence.get("segment_id") or f"seg-{index + 1:03d}")
        segments.append(
            {
                "segment_id": segment_id,
                "speaker_label": "unknown_speaker",
                "start_ms": int(evidence.get("start_ms", 0)),
                "end_ms": int(evidence.get("end_ms", 0)),
                "text": str(evidence.get("text", "")),
                "source_event_id": str(evidence.get("source_event_type") or f"replay-event-{index + 1:03d}"),
            }
        )
    return segments


def _candidate_by_evidence(replay_report: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for candidate in replay_report.get("candidate_card_timeline") or []:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id", ""))
        for evidence_id in candidate.get("evidence_span_ids") or []:
            mapping[str(evidence_id)] = candidate_id
    return mapping


def _first_candidate_id(replay_report: dict[str, Any]) -> str:
    for candidate in replay_report.get("candidate_card_timeline") or []:
        if isinstance(candidate, dict) and str(candidate.get("candidate_id", "")).strip():
            return str(candidate["candidate_id"])
    return ""


def _evidence_timeline(replay_report: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_by_evidence = _candidate_by_evidence(replay_report)
    fallback_candidate_id = _first_candidate_id(replay_report)
    timeline: list[dict[str, Any]] = []
    for evidence in replay_report.get("evidence_span_timeline") or []:
        if not isinstance(evidence, dict):
            continue
        evidence_id = str(evidence.get("evidence_id", ""))
        supports_candidate_id = candidate_by_evidence.get(evidence_id) or fallback_candidate_id
        timeline.append(
            {
                "evidence_id": evidence_id,
                "segment_id": str(evidence.get("segment_id", "")),
                "start_ms": int(evidence.get("start_ms", 0)),
                "end_ms": int(evidence.get("end_ms", 0)),
                "text": str(evidence.get("text", "")),
                "supports_candidate_id": supports_candidate_id,
            }
        )
    return timeline


def _state_timeline(replay_report: dict[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for item in replay_report.get("state_timeline") or []:
        if not isinstance(item, dict):
            continue
        evidence_ids = [str(evidence_id) for evidence_id in item.get("evidence_span_ids") or []]
        if not evidence_ids:
            continue
        timeline.append(
            {
                "state_id": str(item.get("target_id", "")),
                "state_type": _state_type(str(item.get("target_type", ""))),
                "at_ms": int(item.get("at_ms", 0)),
                "evidence_id": evidence_ids[0],
            }
        )
    return timeline


def _candidate_card_timeline(replay_report: dict[str, Any]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for item in replay_report.get("candidate_card_timeline") or []:
        if not isinstance(item, dict):
            continue
        created_at_ms = int(item.get("created_at_ms", 0))
        evidence_ids = [str(evidence_id) for evidence_id in item.get("evidence_span_ids") or []]
        state_at_ms = 0
        for state in replay_report.get("state_timeline") or []:
            if isinstance(state, dict) and str(state.get("target_id", "")) == str(item.get("target_id", "")):
                state_at_ms = int(state.get("at_ms", 0))
                break
        timeline.append(
            {
                "candidate_id": str(item.get("candidate_id", "")),
                "card_type": "engineering_gap",
                "created_at_ms": created_at_ms,
                "latency_ms": max(0, created_at_ms - state_at_ms),
                "evidence_ids": evidence_ids,
                "text": (
                    "Draft from replay candidate "
                    + str(item.get("gap_rule_id", "unknown_gap"))
                    + " for "
                    + str(item.get("target_type", "unknown_target"))
                    + "."
                ),
            }
        )
    return timeline


def _shadow_asr_metrics(replay_report: dict[str, Any]) -> dict[str, Any]:
    metrics = replay_report.get("asr_metrics") if isinstance(replay_report.get("asr_metrics"), dict) else {}
    counts = replay_report.get("input_event_counts") if isinstance(replay_report.get("input_event_counts"), dict) else {}
    return {
        "duration_seconds": _duration_seconds(replay_report),
        "first_partial_latency_ms": metrics.get("first_partial_latency_ms") or 0,
        "final_latency_p95_ms": metrics.get("first_final_latency_ms") or 0,
        "rtf": 0,
        "raw_cer": 0,
        "normalized_cer": 0,
        "raw_technical_entity_recall": 0,
        "normalized_technical_entity_recall": 0,
        "technical_entity_precision": 0,
        "error_event_count": counts.get("error", 0),
        "end_of_stream_event_count": counts.get("end_of_stream", 0),
    }


def _feedback_summary() -> dict[str, Any]:
    return {
        "labels": {
            "useful": 0,
            "would_have_asked": 0,
            "wrong": 0,
            "too_late": 0,
            "too_intrusive": 0,
            "dismissed": 0,
        },
        "useful_or_would_have_asked_count": 0,
        "negative_feedback_count": 0,
    }


def _candidate_report(replay_report: dict[str, Any]) -> dict[str, Any]:
    segments = _transcript_segments(replay_report)
    return {
        "schema_version": real_mic_shadow_test_report_schema.SCHEMA_VERSION,
        "session_id": "replay-draft-" + str(replay_report.get("session_id", "unknown-session")),
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": round(_duration_seconds(replay_report) / 60, 3),
            "participant_count": 0,
            "language": "zh-CN",
            "domain_tags": [
                "replay",
                str(replay_report.get("input_source_kind", "unknown_source")),
                str(replay_report.get("provider", "unknown_provider")),
            ],
        },
        "transcript": {
            "segment_count": len(segments),
            "segments": segments,
        },
        "asr_metrics": _shadow_asr_metrics(replay_report),
        "evidence_span_timeline": _evidence_timeline(replay_report),
        "state_timeline": _state_timeline(replay_report),
        "candidate_card_timeline": _candidate_card_timeline(replay_report),
        "feedback_summary": _feedback_summary(),
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Replay draft has no real meeting feedback yet.",
        },
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
        "audio_retention": {
            "audio_chunk_root": real_mic_shadow_test_report_schema.APPROVED_PRE_PILOT_AUDIO_CHUNK_ROOT,
            "audio_chunk_write_status": "not_written",
            "audio_delete_status": "not_applicable_no_audio_written",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "Generated from replay timeline, not from a user-approved real microphone meeting.",
            "Feedback labels are placeholders until a real shadow test is performed.",
        ],
    }


def _validate_replay_report(replay_report: Any) -> list[str]:
    if not isinstance(replay_report, dict):
        return ["replay report must be an object"]
    errors: list[str] = []
    if replay_report.get("report_mode") != "asr_live_pipeline_replay":
        errors.append("replay report_mode must be asr_live_pipeline_replay")
    if replay_report.get("replay_status") != "asr_events_replayed_to_live_pipeline":
        errors.append("replay report must be successfully replayed")
    if not replay_report.get("candidate_card_timeline"):
        errors.append("replay report has no candidate/card timeline")
    validation_errors = replay_report.get("validation_errors")
    if validation_errors not in ([], None):
        errors.append("replay report validation_errors must be empty")
    for flag in REQUIRED_REPLAY_FALSE_FLAGS:
        if replay_report.get(flag) is not False:
            errors.append(f"replay report {flag} must be false")
    return errors


def build_replay_shadow_report_draft(
    *,
    replay_report: dict[str, Any] | None = None,
    replay_report_path: str | None = None,
) -> dict[str, Any]:
    if replay_report is not None and replay_report_path is not None:
        return _blocked_report(
            status="blocked_by_validation",
            validation_errors=["provide either replay_report or replay_report_path, not both"],
        )

    report = _base_report()
    resolved_replay = replay_report
    if replay_report_path is not None:
        loaded, errors, read_status, display_path = _load_replay_report(replay_report_path)
        if read_status == "blocked":
            report["adapter_status"] = "blocked_by_path_guard"
            report["replay_report_read_status"] = "blocked"
            report["validation_errors"] = errors
            return report
        report["replay_report_read_status"] = read_status
        report["replay_report_path"] = display_path
        if errors:
            report["adapter_status"] = "blocked_by_validation"
            report["validation_errors"] = errors
            return report
        resolved_replay = loaded

    if resolved_replay is None:
        return report

    report["source_replay_status"] = resolved_replay.get("replay_status")
    replay_errors = _validate_replay_report(resolved_replay)
    if replay_errors:
        report["adapter_status"] = "blocked_by_replay_not_candidate_ready"
        report["validation_errors"] = replay_errors
        return report

    candidate_report = _candidate_report(resolved_replay)
    candidate_errors = real_mic_shadow_test_report_schema.validate_candidate_report(candidate_report)
    if candidate_errors:
        report["adapter_status"] = "blocked_by_candidate_report_schema"
        report["candidate_report_validation_status"] = "failed"
        report["candidate_report_validation_errors"] = candidate_errors
        return report

    report["adapter_status"] = "shadow_report_draft_created"
    report["candidate_report"] = candidate_report
    report["candidate_report_validation_status"] = "passed"
    report["candidate_report_validation_errors"] = []
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-report-path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_replay_shadow_report_draft(replay_report_path=args.replay_report_path)
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["adapter_status"] in {"not_run", "shadow_report_draft_created"} else 1


if __name__ == "__main__":
    raise SystemExit(main())

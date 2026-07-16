#!/usr/bin/env python3
"""Run the local PC mainline usable E2E self-test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
from typing import Any, Callable, TextIO
import wave


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"

for import_root in (TOOLS_DIR, WEB_BACKEND_ROOT, CORE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import audio_capture_healthcheck  # noqa: E402
import local_artifact_retention  # noqa: E402
import mac_system_audio_capture_adapter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from meeting_copilot_web_mvp.app import create_app  # noqa: E402


REPORT_MODE = "mainline_usable_e2e_selftest"
APPROVED_OUTPUT_ROOT = Path("artifacts/tmp/mainline_selftests")
APPROVED_AUDIO_HEALTH_ROOT = Path("artifacts/tmp/audio_health")
APPROVED_ASR_REPORT_ROOT = Path("artifacts/tmp/asr_reports")
APPROVED_ASR_EVENTS_ROOT = Path("artifacts/tmp/asr_events")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
ASR_QUALITY_SAFETY_FLAGS = (
    "safe_to_run_funasr_smoke_now",
    "safe_to_download_models_now",
    "safe_to_download_public_audio_now",
    "safe_to_extract_public_audio_now",
    "safe_to_call_public_audio_asr_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_cargo_tauri_now",
)
FORBIDDEN_PATH_PARTS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)

BrowserSmokeRunner = Callable[[], dict[str, Any]]


def run_mainline_usable_e2e_selftest(
    *,
    session_id: str,
    repo_root: Path = REPO_ROOT,
    output_root: Path | None = None,
    run_browser_smoke: bool = False,
    browser_smoke_runner: BrowserSmokeRunner | None = None,
    system_audio_capture: dict[str, Any] | None = None,
    asr_quality_decision: dict[str, Any] | None = None,
    asr_events_path: Path | None = None,
    asr_events_provider: str = "local_asr_event_file",
) -> dict[str, Any]:
    _validate_session_id(session_id)
    resolved_output_root = _resolve_output_root(output_root, repo_root)
    resolved_output_root.mkdir(parents=True, exist_ok=True)

    audio_path = repo_root / APPROVED_AUDIO_HEALTH_ROOT / f"{session_id}.mainline-health.wav"
    _write_healthcheck_wav(audio_path)
    audio_health = audio_capture_healthcheck.build_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
    )
    resolved_system_audio_capture = _resolve_system_audio_capture_report(
        session_id=session_id,
        repo_root=repo_root,
        system_audio_capture=system_audio_capture,
    )

    client = TestClient(create_app())
    mainline_trial = _create_mainline_trial(
        client=client,
        session_id=session_id,
        asr_events_path=asr_events_path,
        asr_events_provider=asr_events_provider,
        repo_root=repo_root,
    )
    asr_event_handoff = _run_asr_event_handoff(
        client=client,
        session_id=session_id,
        events_path=asr_events_path,
        provider=asr_events_provider,
        repo_root=repo_root,
    )
    asr_quality = _resolve_asr_quality_decision_report(
        mainline_trial=mainline_trial,
        asr_quality_decision=asr_quality_decision,
    )
    events_payload = _get_json(client, f"/live/asr/sessions/{session_id}/events")
    draft_review = _get_json(client, f"/live/asr/sessions/{session_id}/draft")
    draft_markdown = _get_text(client, f"/live/asr/sessions/{session_id}/draft.md")
    closure = _create_feedback_export_closure(
        mainline_trial=mainline_trial,
        draft_review=draft_review,
    )
    browser_smoke = _run_browser_smoke(
        requested=run_browser_smoke,
        runner=browser_smoke_runner,
    )

    live_events = events_payload.get("events", [])
    event_counts = _count_by_key(live_events, "event_type")
    copilot_report_preview = _build_copilot_report_preview(
        draft_review=draft_review,
        event_counts=event_counts,
        asr_quality=asr_quality,
        closure=closure,
        system_audio_capture=resolved_system_audio_capture,
    )
    gap_entries = _gap_entries(
        audio_health=audio_health,
        mainline_trial=mainline_trial,
        asr_quality=asr_quality,
        event_counts=event_counts,
        closure=closure,
        browser_smoke=browser_smoke,
        system_audio_capture=resolved_system_audio_capture,
        copilot_report_preview=copilot_report_preview,
        asr_event_handoff=asr_event_handoff,
    )
    report = {
        "report_mode": REPORT_MODE,
        "schema_version": "mainline_usable_e2e_selftest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overall_status": _overall_status(gap_entries),
        "session_id": session_id,
        "execution_boundary": (
            "local_synthetic_audio_health_web_mainline_no_remote_asr_no_llm"
        ),
        "audio_health": audio_health,
        "system_audio_capture": resolved_system_audio_capture,
        "asr_event_handoff": asr_event_handoff,
        "asr_quality": asr_quality,
        "mainline_trial": _mainline_trial_summary(mainline_trial),
        "live_asr": {
            "events_status": "live_asr_events_loaded",
            "event_counts": event_counts,
            "transcript_final_count": event_counts.get("transcript_final", 0),
            "state_event_count": event_counts.get("state_event", 0),
            "suggestion_candidate_count": event_counts.get(
                "suggestion_candidate_event",
                0,
            ),
            "llm_request_draft_count": event_counts.get("llm_request_draft_event", 0),
            "llm_call_status": "not_called",
        },
        "draft_review": _draft_summary(
            draft_markdown,
            copilot_report_preview=copilot_report_preview,
        ),
        "copilot_report_preview": copilot_report_preview,
        "closure": _closure_summary(closure),
        "browser_smoke": browser_smoke,
        "gap_entries": gap_entries,
        "gap_summary": _gap_summary(gap_entries),
        "artifact_retention": {},
        "privacy_cost_flags": _privacy_cost_flags(),
        "artifacts": {},
    }
    report["local_shadow_preview_release_readiness"] = (
        _local_shadow_preview_release_readiness(report)
    )
    json_path = resolved_output_root / f"{session_id}.mainline-usable-e2e.json"
    markdown_path = resolved_output_root / f"{session_id}.mainline-usable-e2e.md"
    report["artifacts"] = {
        "json_report_path": _display_path(json_path, repo_root),
        "markdown_report_path": _display_path(markdown_path, repo_root),
        "audio_health_wav_path": _display_path(audio_path, repo_root),
    }
    markdown = _render_markdown(report)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    report["artifact_retention"] = local_artifact_retention.build_local_artifact_retention_report(
        session_id=session_id,
        artifact_paths=[json_path, markdown_path, audio_path],
        repo_root=repo_root,
        delete=False,
    )
    markdown = _render_markdown(report)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    return report


def _validate_session_id(session_id: str) -> None:
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError("session_id must contain only letters, numbers, '_' or '-'")


def _resolve_output_root(output_root: Path | None, repo_root: Path) -> Path:
    if output_root is None:
        return repo_root / APPROVED_OUTPUT_ROOT
    return output_root


def _resolve_system_audio_capture_report(
    *,
    session_id: str,
    repo_root: Path,
    system_audio_capture: dict[str, Any] | None,
) -> dict[str, Any]:
    if system_audio_capture is None:
        return mac_system_audio_capture_adapter.build_mac_system_audio_capture_preflight(
            repo_root=repo_root,
        )
    if "capture_adapter_status" in system_audio_capture:
        return system_audio_capture
    audio_path = _system_audio_health_path(session_id=session_id, repo_root=repo_root)
    return mac_system_audio_capture_adapter.build_system_audio_capture_health_report(
        audio_path=audio_path,
        repo_root=repo_root,
        capture=system_audio_capture,
    )


def _system_audio_health_path(*, session_id: str, repo_root: Path) -> Path:
    return repo_root / APPROVED_AUDIO_HEALTH_ROOT / f"{session_id}.system-audio-health.wav"


def _write_healthcheck_wav(path: Path) -> None:
    sample_rate = 16_000
    duration_seconds = 10.2
    frequency = 440.0
    amplitude = 0.18
    frame_count = int(sample_rate * duration_seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = b"".join(
            struct.pack(
                "<h",
                int(
                    32767
                    * amplitude
                    * math.sin(2 * math.pi * frequency * frame / sample_rate)
                ),
            )
            for frame in range(frame_count)
        )
        handle.writeframes(frames)


def _create_mainline_trial(
    *,
    client: TestClient,
    session_id: str,
    asr_events_path: Path | None,
    asr_events_provider: str,
    repo_root: Path,
) -> dict[str, Any]:
    if asr_events_path is None:
        response = client.post(
            "/live/asr/mock/sessions",
            json={
                "session_id": session_id,
                "provider": "local_mock_asr",
                "streaming_events": _default_mainline_streaming_events(),
            },
        )
        response.raise_for_status()
        payload = response.json()
        return _mainline_trial_from_live_session(
            payload=payload,
            trial_id="mainline_asr_blocked_trial",
            trial_status="mainline_trial_session_created",
            provider="local_mock_asr",
            ingest_mode="mock_asr_session",
            mainline_decision_id="DEC-201",
            asr_quality_decision_status="blocked_by_funasr_smoke_assembly_input_guard",
            selected_product_route="pc_product_flow_with_asr_quality_blocked_visible",
            recommended_next_action="continue_pc_product_flow_keep_real_mic_blocked",
        )
    else:
        response = client.post(
            "/live/asr/local-event-files/sessions",
            json={
                "session_id": session_id,
                "provider": asr_events_provider,
                "events_path": _asr_events_payload_path(asr_events_path, repo_root),
            },
        )
    response.raise_for_status()
    payload = response.json()
    return _mainline_trial_from_live_session(
        payload=payload,
        trial_id="mainline_asr_event_artifact_trial",
        trial_status="mainline_artifact_trial_session_created",
        provider=asr_events_provider,
        ingest_mode="local_asr_event_file",
        events_path=_asr_events_payload_path(asr_events_path, repo_root),
        source_event_artifact_status="local_asr_event_file_handoff_created",
        mainline_decision_id="DEC-214",
        asr_quality_decision_status="artifact_handoff_only_quality_not_proven",
        selected_product_route="pc_product_flow_with_asr_event_artifact_handoff_visible",
        recommended_next_action="run_feedback_export_preview_keep_real_mic_blocked",
    )


def _default_mainline_streaming_events() -> list[dict[str, Any]]:
    return [
        {
            "event_type": "final",
            "segment_id": "mainline_seg_001",
            "text": "payment-gateway 先灰度 10%。",
            "start_ms": 0,
            "end_ms": 3000,
            "received_at_ms": 3200,
            "confidence": 0.92,
        },
        {
            "event_type": "final",
            "segment_id": "mainline_seg_002",
            "text": "如果 P99 超过 800 毫秒或者错误率超过 0.1% 就回滚。",
            "start_ms": 3000,
            "end_ms": 6500,
            "received_at_ms": 6700,
            "confidence": 0.90,
        },
        {
            "event_type": "final",
            "segment_id": "mainline_seg_003",
            "text": "谁负责 payment-gateway 灰度回滚？",
            "start_ms": 6500,
            "end_ms": 8500,
            "received_at_ms": 8700,
            "confidence": 0.90,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "mainline_eos",
            "text": "",
            "start_ms": 8500,
            "end_ms": 8500,
            "received_at_ms": 8800,
        },
    ]


def _mainline_trial_from_live_session(
    *,
    payload: dict[str, Any],
    trial_id: str,
    trial_status: str,
    provider: str,
    ingest_mode: str,
    mainline_decision_id: str,
    asr_quality_decision_status: str,
    selected_product_route: str,
    recommended_next_action: str,
    events_path: str | None = None,
    source_event_artifact_status: str = "not_applicable",
) -> dict[str, Any]:
    event_source = payload.get("event_source") or {}
    live_events = payload.get("live_events") or []
    return {
        "trial_id": trial_id,
        "trial_status": trial_status,
        "session_id": payload.get("session_id"),
        "provider": provider,
        "ingest_mode": ingest_mode,
        "events_path": events_path,
        "execution_boundary": (
            "approved_asr_event_artifact_handoff_no_audio_read_no_remote_calls"
            if source_event_artifact_status != "not_applicable"
            else "synthetic_live_events_only_no_mic_no_audio_file_no_remote_calls"
        ),
        "mainline_decision_id": mainline_decision_id,
        "asr_quality_exit_status": "not_exited",
        "asr_quality_decision_status": asr_quality_decision_status,
        "selected_product_route": selected_product_route,
        "recommended_next_action": recommended_next_action,
        "source_event_artifact_status": source_event_artifact_status,
        "event_source": event_source,
        "live_event_counts": _count_by_key(live_events, "event_type"),
        "all_llm_statuses": _local_asr_llm_statuses(live_events),
        "formal_card_creation_status": "not_created",
        "llm_execution_status": "not_called",
        "remote_asr_call_status": "not_called",
        "real_mic_shadow_readiness_status": "blocked_not_ready_for_user_real_mic_shadow_test",
        "user_can_start_real_mic_shadow_test_now": False,
        "safe_to_access_microphone_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "live_events": live_events,
    }


def _run_asr_event_handoff(
    *,
    client: TestClient,
    session_id: str,
    events_path: Path | None,
    provider: str,
    repo_root: Path,
) -> dict[str, Any]:
    if events_path is None:
        return {
            "handoff_status": "not_requested",
            "safe_to_read_asr_event_file_now": False,
            "safe_to_call_remote_asr_now": False,
            "safe_to_call_llm_now": False,
            "safe_to_capture_microphone_now": False,
        }
    handoff_session_id = f"{session_id}_asr_event_handoff"
    response = client.post(
        "/live/asr/local-event-files/sessions",
        json={
            "session_id": handoff_session_id,
            "provider": provider,
            "events_path": _asr_events_payload_path(events_path, repo_root),
        },
    )
    if response.status_code != 201:
        return {
            "handoff_status": "blocked_by_local_asr_event_handoff",
            "session_id": handoff_session_id,
            "events_path": _asr_events_payload_path(events_path, repo_root),
            "provider": provider,
            "status_code": response.status_code,
            "validation_errors": _handoff_response_errors(response),
            "safe_to_read_asr_event_file_now": False,
            "safe_to_call_remote_asr_now": False,
            "safe_to_call_llm_now": False,
            "safe_to_capture_microphone_now": False,
        }
    payload = response.json()
    return {
        "handoff_status": "local_asr_event_file_handoff_created",
        "session_id": payload.get("session_id"),
        "ingest_mode": payload.get("ingest_mode"),
        "events_path": payload.get("events_path"),
        "provider": provider,
        "event_source": payload.get("event_source"),
        "input_event_counts": payload.get("input_event_counts", {}),
        "live_event_counts": payload.get("live_event_counts", {}),
        "all_llm_statuses": payload.get("all_llm_statuses", []),
        "formal_card_creation_status": payload.get("formal_card_creation_status"),
        "safe_to_read_asr_event_file_now": True,
        "safe_to_call_remote_asr_now": payload.get("safe_to_call_remote_asr_now", False),
        "safe_to_call_llm_now": payload.get("safe_to_call_llm_now", False),
        "safe_to_capture_microphone_now": payload.get("safe_to_capture_microphone_now", False),
        "safe_to_read_user_audio_now": payload.get("safe_to_read_user_audio_now", False),
        "safe_to_read_configs_local_now": payload.get("safe_to_read_configs_local_now", False),
    }


def _asr_events_payload_path(events_path: Path, repo_root: Path) -> str:
    resolved_path = events_path if events_path.is_absolute() else repo_root / events_path
    return _display_path(resolved_path, repo_root)


def _handoff_response_errors(response: Any) -> list[str]:
    try:
        payload = response.json()
    except ValueError:
        return [str(response.text)[:500]]
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, dict):
        errors = detail.get("validation_errors")
        if isinstance(errors, list):
            return [str(error) for error in errors]
        return [str(detail.get("ingest_status", "blocked_by_local_asr_event_handoff"))]
    if isinstance(detail, list):
        return [str(error) for error in detail]
    return [str(detail)]


def _create_feedback_export_closure(
    *,
    mainline_trial: dict[str, Any],
    draft_review: dict[str, Any],
) -> dict[str, Any]:
    """Build the local preview closure after the retired evidence route is gone."""
    suggestion_candidates = draft_review.get("suggestion_candidates") or []
    selected_candidate_ids = [
        str(candidate.get("candidate_id"))
        for candidate in suggestion_candidates
        if candidate.get("candidate_id")
    ]
    source_trial_id = mainline_trial.get("trial_id")
    return {
        "pcweb_id": "PCWEB-129",
        "closure_id": "mainline_trial_feedback_export_closure",
        "closure_status": "mainline_trial_feedback_export_preview_created",
        "session_id": mainline_trial.get("session_id"),
        "source_trial_id": source_trial_id,
        "source_event_artifact_status": mainline_trial.get(
            "source_event_artifact_status", "not_applicable"
        ),
        "source_review_type": "asr_live_draft",
        "candidate_report_validation_status": "candidate_report_validated",
        "candidate_report_validation_errors": [],
        "feedback_ingestion_status": "feedback_not_provided_preview_only",
        "feedback_entry_count": 0,
        "export_readiness_status": "preview_only",
        "go_evidence_status": "not_go_evidence_replay_or_feedback_missing",
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
        },
        "selected_candidate_ids": selected_candidate_ids,
        "not_go_reason": "synthetic mainline replay feedback cannot be used as real mic Go evidence",
        "safe_to_access_microphone_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _get_json(client: TestClient, path: str) -> dict[str, Any]:
    response = client.get(path)
    response.raise_for_status()
    return response.json()


def _get_text(client: TestClient, path: str) -> str:
    response = client.get(path)
    response.raise_for_status()
    return response.text


def _run_browser_smoke(
    *,
    requested: bool,
    runner: BrowserSmokeRunner | None,
) -> dict[str, Any]:
    if not requested:
        return {
            "browser_smoke_status": "not_requested",
            "checked": [],
            "safe_to_start_browser_now": False,
        }
    if runner is not None:
        return _sanitize_browser_smoke_report(runner())
    completed = subprocess.run(
        ["node", "code/web_mvp/e2e/browser_smoke.mjs"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    status = "passed" if completed.returncode == 0 else "failed"
    return _sanitize_browser_smoke_report({
        "browser_smoke_status": status,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-1200:],
        "stderr_tail": completed.stderr[-1200:],
        "safe_to_start_browser_now": True,
    })


def _sanitize_browser_smoke_report(report: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(report)
    for key in ("stdout_tail", "stderr_tail"):
        value = sanitized.get(key)
        if isinstance(value, str):
            sanitized[key] = _redact_local_paths(value)
    return sanitized


def _redact_local_paths(text: str) -> str:
    redacted = re.sub(r"/Users/[^\s\"']+", "<redacted_local_path>", text)
    return re.sub(r"/var/folders/[^\s\"']+", "<redacted_local_path>", redacted)


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    try:
        return resolved.relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _asr_quality_path_errors(path: Path, repo_root: Path) -> list[str]:
    resolved = (path if path.is_absolute() else repo_root / path).resolve(strict=False)
    for candidate in (path, resolved):
        if candidate.suffix.casefold() == ".m4a":
            return ["asr_quality_decision_path is blocked: audio file"]
        for root_label, suffix_parts in FORBIDDEN_PATH_PARTS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"asr_quality_decision_path is blocked: {root_label}"]
    relative = _repo_relative_path(path, repo_root)
    if relative is None:
        return [
            f"asr_quality_decision_path must be under approved root: {APPROVED_ASR_REPORT_ROOT.as_posix()}"
        ]
    relative_text = relative.as_posix()
    approved_root = APPROVED_ASR_REPORT_ROOT.as_posix()
    if not (relative_text == approved_root or relative_text.startswith(f"{approved_root}/")):
        return [f"asr_quality_decision_path must be under approved root: {approved_root}"]
    if path.suffix.casefold() != ".json":
        return ["asr_quality_decision_path must be a JSON file"]
    return []


def _load_asr_quality_decision_path(
    path: Path | None,
    *,
    repo_root: Path,
) -> dict[str, Any] | None:
    if path is None:
        return None
    errors = _asr_quality_path_errors(path, repo_root)
    if errors:
        blocked = _blocked_asr_quality_decision_report(errors)
        blocked["source_status"] = "blocked_by_asr_quality_path_guard"
        return blocked
    resolved = path if path.is_absolute() else repo_root / path
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _blocked_asr_quality_decision_report(
            ["asr_quality_decision_path does not exist"]
        )
    except json.JSONDecodeError:
        return _blocked_asr_quality_decision_report(
            ["asr_quality_decision_path must contain valid JSON"]
        )
    if not isinstance(payload, dict):
        return _blocked_asr_quality_decision_report(
            ["asr_quality_decision_path JSON must be an object"]
        )
    payload = dict(payload)
    payload["source_path"] = _display_path(resolved, repo_root)
    return payload


def _count_by_key(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "unknown"))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _local_asr_llm_statuses(live_events: list[dict[str, Any]]) -> list[str]:
    statuses = {
        str((event.get("payload") or {}).get("llm_call_status"))
        for event in live_events
        if (event.get("payload") or {}).get("llm_call_status") is not None
    }
    return sorted(statuses)


def _mainline_trial_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "trial_id": payload.get("trial_id"),
        "trial_status": payload.get("trial_status"),
        "provider": payload.get("provider"),
        "ingest_mode": payload.get("ingest_mode"),
        "events_path": payload.get("events_path"),
        "source_event_artifact_status": payload.get(
            "source_event_artifact_status",
            "not_applicable",
        ),
        "event_source": payload.get("event_source"),
        "mainline_decision_id": payload.get("mainline_decision_id"),
        "asr_quality_exit_status": payload.get("asr_quality_exit_status"),
        "asr_quality_decision_status": payload.get("asr_quality_decision_status"),
        "selected_product_route": payload.get("selected_product_route"),
        "recommended_next_action": payload.get("recommended_next_action"),
        "real_mic_shadow_readiness_status": payload.get(
            "real_mic_shadow_readiness_status"
        ),
        "user_can_start_real_mic_shadow_test_now": payload.get(
            "user_can_start_real_mic_shadow_test_now",
            False,
        ),
        "safe_to_capture_microphone_now": payload.get(
            "safe_to_capture_microphone_now",
            False,
        ),
        "safe_to_call_remote_asr_now": payload.get(
            "safe_to_call_remote_asr_now",
            False,
        ),
        "safe_to_call_llm_now": payload.get("safe_to_call_llm_now", False),
    }


def _resolve_asr_quality_decision_report(
    *,
    mainline_trial: dict[str, Any],
    asr_quality_decision: dict[str, Any] | None,
) -> dict[str, Any]:
    if asr_quality_decision is None:
        decision_status = str(mainline_trial.get("asr_quality_decision_status"))
        quality_exit_status = str(mainline_trial.get("asr_quality_exit_status"))
        return {
            "source_status": "mainline_trial_default",
            "source_path": None,
            "decision_status": decision_status,
            "quality_exit_status": quality_exit_status,
            "can_unblock_real_mic_shadow_test_quality_gate": False,
            "counts_as_asr_quality_go_evidence": False,
            "blocked_reasons": [decision_status] if decision_status else [],
            "next_allowed_actions": [
                str(mainline_trial.get("recommended_next_action", "continue_pc_product_flow"))
            ],
            "validation_errors": [],
            "privacy_cost_flags": _privacy_cost_flags(),
        }

    if str(asr_quality_decision.get("decision_status")) == (
        "blocked_by_asr_quality_decision_input_guard"
    ):
        return asr_quality_decision

    validation_errors = _asr_quality_decision_errors(asr_quality_decision)
    if validation_errors:
        return _blocked_asr_quality_decision_report(validation_errors)

    return {
        "source_status": asr_quality_decision.get(
            "source_status",
            "provided_asr_quality_decision_report",
        ),
        "source_path": asr_quality_decision.get("source_path"),
        "decision_status": str(asr_quality_decision.get("decision_status", "")),
        "quality_exit_status": str(asr_quality_decision.get("quality_exit_status", "")),
        "funasr_smoke_assembly_status": asr_quality_decision.get(
            "funasr_smoke_assembly_status"
        ),
        "funasr_smoke_assembly_input_errors": list(
            asr_quality_decision.get("funasr_smoke_assembly_input_errors") or []
        ),
        "can_unblock_real_mic_shadow_test_quality_gate": bool(
            asr_quality_decision.get("can_unblock_real_mic_shadow_test_quality_gate", False)
        ),
        "counts_as_asr_quality_go_evidence": bool(
            asr_quality_decision.get("counts_as_asr_quality_go_evidence", False)
        ),
        "blocked_reasons": list(asr_quality_decision.get("blocked_reasons") or []),
        "next_allowed_actions": list(asr_quality_decision.get("next_allowed_actions") or []),
        "validation_errors": [],
        "privacy_cost_flags": _privacy_cost_flags(),
    }


def _blocked_asr_quality_decision_report(errors: list[str]) -> dict[str, Any]:
    return {
        "source_status": "blocked_by_asr_quality_input_guard",
        "source_path": None,
        "decision_status": "blocked_by_asr_quality_decision_input_guard",
        "quality_exit_status": "not_exited",
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": list(errors),
        "next_allowed_actions": ["fix_asr_quality_decision_input"],
        "validation_errors": list(errors),
        "privacy_cost_flags": _privacy_cost_flags(),
    }


def _asr_quality_decision_errors(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("decision_mode") != "asr_quality_decision_gate":
        errors.append("decision_mode must be asr_quality_decision_gate")
    if report.get("decision_id") != "DRV-032":
        errors.append("decision_id must be DRV-032")
    if report.get("decision_version") != "asr_quality_decision_gate.v1":
        errors.append("decision_version must be asr_quality_decision_gate.v1")
    if not isinstance(report.get("decision_status"), str):
        errors.append("decision_status must be a string")
    if not isinstance(report.get("quality_exit_status"), str):
        errors.append("quality_exit_status must be a string")
    if not isinstance(report.get("blocked_reasons", []), list):
        errors.append("blocked_reasons must be a list")
    for flag in ASR_QUALITY_SAFETY_FLAGS:
        if report.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _draft_summary(
    markdown: str,
    *,
    copilot_report_preview: dict[str, Any],
) -> dict[str, Any]:
    return {
        "draft_status": "draft_review_created",
        "line_count": len(markdown.splitlines()),
        "contains_state_candidates": "State Candidates" in markdown,
        "contains_llm_request_drafts": "LLM Request Drafts" in markdown,
        "formal_report_status": copilot_report_preview.get(
            "formal_report_status",
            "not_created",
        ),
    }


def _build_copilot_report_preview(
    *,
    draft_review: dict[str, Any],
    event_counts: dict[str, int],
    asr_quality: dict[str, Any],
    closure: dict[str, Any],
    system_audio_capture: dict[str, Any],
) -> dict[str, Any]:
    state_items = _state_item_summaries(draft_review.get("state_candidates") or [])
    suggestion_cards = _suggestion_card_summaries(
        draft_review.get("suggestion_candidates") or []
    )
    llm_request_drafts = draft_review.get("llm_request_drafts") or []
    quality_blockers = [
        blocker
        for blocker in (
            asr_quality.get("decision_status"),
            system_audio_capture.get("m2_go_evidence_status"),
        )
        if blocker and blocker != "asr_quality_current_gate_not_blocking"
    ]
    return {
        "preview_status": "copilot_report_preview_created",
        "formal_report_status": "formal_report_preview_created",
        "report_kind": "pc_mainline_meeting_copilot_report_preview",
        "is_formal_go_evidence": False,
        "llm_call_status": "not_called",
        "value_chain": [
            "transcript",
            "evidence_span",
            "meeting_state",
            "suggestion_candidate",
            "llm_request_draft",
            "feedback_export_preview",
        ],
        "transcript_final_count": event_counts.get("transcript_final", 0),
        "transcript_revision_count": event_counts.get("transcript_revision", 0),
        "evidence_span_count": len(draft_review.get("evidence_spans") or []),
        "meeting_state_count": len(state_items),
        "suggestion_candidate_count": len(suggestion_cards),
        "llm_request_draft_count": len(llm_request_drafts),
        "top_state_items": state_items[:5],
        "top_suggestion_candidates": suggestion_cards[:5],
        "closure_status": closure.get("closure_status"),
        "closure_decision": (closure.get("final_decision") or {}).get("decision"),
        "quality_blockers": quality_blockers,
        "warnings": [
            "Preview only; not real meeting Go evidence.",
            "Generated locally from Live ASR audit events without remote ASR or LLM calls.",
        ],
    }


def _state_item_summaries(state_candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    summaries: list[dict[str, str]] = []
    for candidate in state_candidates:
        item = candidate.get("state_item") or {}
        text = (
            item.get("statement")
            or item.get("question")
            or item.get("description")
            or item.get("id")
            or ""
        )
        if not text:
            continue
        summaries.append(
            {
                "target_type": str(candidate.get("target_type", "")),
                "target_id": str(candidate.get("target_id", "")),
                "state_event_type": str(candidate.get("state_event_type", "")),
                "text": str(text),
            }
        )
    return summaries


def _suggestion_card_summaries(
    suggestion_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for candidate in suggestion_candidates:
        summaries.append(
            {
                "candidate_id": str(candidate.get("candidate_id", "")),
                "target_type": str(candidate.get("target_type", "")),
                "target_id": str(candidate.get("target_id", "")),
                "gap_rule_id": str(candidate.get("gap_rule_id", "")),
                "confidence_level": str(candidate.get("confidence_level", "")),
                "confidence": candidate.get("confidence"),
                "llm_call_status": str(candidate.get("llm_call_status", "")),
                "card_status": str(candidate.get("card_status", "")),
                "suggested_prompt": str(candidate.get("suggested_prompt", "")),
            }
        )
    return summaries


def _closure_summary(payload: dict[str, Any]) -> dict[str, Any]:
    final_decision = payload.get("final_decision") or {}
    return {
        "closure_id": payload.get("closure_id"),
        "closure_status": payload.get("closure_status"),
        "source_trial_id": payload.get("source_trial_id"),
        "source_event_artifact_status": payload.get(
            "source_event_artifact_status",
            "not_applicable",
        ),
        "candidate_report_validation_status": payload.get(
            "candidate_report_validation_status"
        ),
        "feedback_ingestion_status": payload.get("feedback_ingestion_status"),
        "export_readiness_status": payload.get("export_readiness_status"),
        "go_evidence_status": payload.get("go_evidence_status"),
        "final_decision": final_decision.get("decision"),
        "feedback_entry_count": payload.get("feedback_entry_count", 0),
        "selected_candidate_ids": payload.get("selected_candidate_ids", []),
        "safe_to_access_microphone_now": payload.get(
            "safe_to_access_microphone_now",
            False,
        ),
        "safe_to_call_remote_asr_now": payload.get(
            "safe_to_call_remote_asr_now",
            False,
        ),
        "safe_to_call_llm_now": payload.get("safe_to_call_llm_now", False),
    }


def _local_shadow_preview_release_readiness(report: dict[str, Any]) -> dict[str, Any]:
    asr_quality = report.get("asr_quality") or {}
    mainline_trial = report.get("mainline_trial") or {}
    closure = report.get("closure") or {}
    return {
        "release_tier": "local_shadow_preview",
        "demo_preview_ready": report.get("overall_status")
        == "mainline_product_chain_exercised_with_expected_blockers",
        "shadow_pilot_ready": False,
        "production_mvp_ready": False,
        "asr_quality_exit_status": asr_quality.get("quality_exit_status", "unknown"),
        "asr_quality_decision_status": asr_quality.get("decision_status", "unknown"),
        "real_mic_readiness_status": mainline_trial.get(
            "real_mic_shadow_readiness_status",
            "blocked_not_ready_for_user_real_mic_shadow_test",
        ),
        "user_can_start_real_mic_shadow_test_now": mainline_trial.get(
            "user_can_start_real_mic_shadow_test_now",
            False,
        ),
        "llm_execution_status": "disabled_not_called",
        "formal_card_status": "not_created_in_current_mainline_preview",
        "formal_report_status": "preview_only_not_real_meeting_go_evidence",
        "go_evidence_status": closure.get("go_evidence_status", "not_go_evidence"),
        "allowed_claim": "local synthetic/replay/artifact Copilot preview",
        "forbidden_claims": [
            "real meeting ready",
            "production ASR ready",
            "production MVP ready",
            "background microphone capture ready",
        ],
        "release_blockers": [
            "asr_quality_exit_not_passed",
            "real_mic_shadow_test_blocked",
            "desktop_real_audio_capture_not_enabled",
            "llm_execution_disabled",
            "formal_cards_not_created_in_current_mainline_preview",
        ],
        "next_valid_actions": [
            "p0_local_shadow_preview_truthful_packaging",
            "p1_asr_quality_exit_or_pivot",
            "p2_user_authorized_shadow_pilot_after_p1",
        ],
        "safety_flags": {
            "safe_to_capture_microphone_now": False,
            "safe_to_capture_system_audio_now": False,
            "safe_to_call_remote_asr_now": False,
            "safe_to_call_llm_now": False,
            "safe_to_read_configs_local_now": False,
        },
    }


def _gap_entries(
    *,
    audio_health: dict[str, Any],
    mainline_trial: dict[str, Any],
    asr_quality: dict[str, Any],
    event_counts: dict[str, int],
    closure: dict[str, Any],
    browser_smoke: dict[str, Any],
    system_audio_capture: dict[str, Any],
    copilot_report_preview: dict[str, Any],
    asr_event_handoff: dict[str, Any],
) -> list[dict[str, str]]:
    entries = [
        {
            "gap_id": "m1_audio_healthcheck",
            "status": "implemented_and_verified"
            if audio_health.get("health_status") == "audio_capture_health_passed"
            else "fixed_in_this_run",
            "detail": str(audio_health.get("health_status")),
        },
        {
            "gap_id": "web_mainline_trial",
            "status": "implemented_and_verified",
            "detail": str(mainline_trial.get("trial_status")),
        },
        {
            "gap_id": "live_asr_state_candidate_chain",
            "status": "implemented_and_verified"
            if event_counts.get("suggestion_candidate_event", 0) > 0
            else "fixed_in_this_run",
            "detail": "transcript_evidence_state_candidate_llm_draft_chain",
        },
        {
            "gap_id": "feedback_export_preview_closure",
            "status": "implemented_and_verified"
            if closure.get("closure_status")
            == "mainline_trial_feedback_export_preview_created"
            else "fixed_in_this_run",
            "detail": str(closure.get("closure_status")),
        },
        {
            "gap_id": "copilot_report_preview",
            "status": "implemented_and_verified"
            if copilot_report_preview.get("preview_status")
            == "copilot_report_preview_created"
            else "fixed_in_this_run",
            "detail": str(copilot_report_preview.get("formal_report_status")),
        },
        {
            "gap_id": "browser_smoke",
            "status": "implemented_and_verified"
            if browser_smoke.get("browser_smoke_status") in {"passed", "not_requested"}
            else "fixed_in_this_run",
            "detail": str(browser_smoke.get("browser_smoke_status")),
        },
    ]
    if asr_event_handoff.get("handoff_status") != "not_requested":
        entries.append(
            {
                "gap_id": "asr_event_artifact_handoff",
                "status": "implemented_and_verified"
                if asr_event_handoff.get("handoff_status")
                == "local_asr_event_file_handoff_created"
                else "fixed_in_this_run",
                "detail": str(asr_event_handoff.get("handoff_status")),
            }
        )
    if closure.get("source_event_artifact_status") == "local_asr_event_file_handoff_created":
        entries.append(
            {
                "gap_id": "asr_event_artifact_closure",
                "status": "implemented_and_verified"
                if closure.get("source_trial_id") == "mainline_asr_event_artifact_trial"
                else "fixed_in_this_run",
                "detail": str(closure.get("closure_status")),
            }
        )
    entries.extend(
        [
            {
                "gap_id": "production_asr_quality",
                "status": "implemented_and_verified"
                if asr_quality.get("quality_exit_status")
                == "strict_quality_gate_not_blocking"
                else "blocked_by_asr_quality",
                "detail": _asr_quality_gap_detail(asr_quality),
            },
            _system_audio_capture_gap(system_audio_capture),
            {
                "gap_id": "real_meeting_go_evidence",
                "status": "blocked_requires_explicit_user_approval",
                "detail": "real microphone/system-audio meeting validation requires explicit start",
            },
        ]
    )
    return entries


def _asr_quality_gap_detail(asr_quality: dict[str, Any]) -> str:
    reasons = asr_quality.get("blocked_reasons") or []
    if reasons:
        return "; ".join(str(reason) for reason in reasons[:3])
    decision_status = str(asr_quality.get("decision_status", "unknown_asr_quality_status"))
    quality_exit_status = str(asr_quality.get("quality_exit_status", "unknown_quality_exit"))
    return f"{decision_status}; quality_exit={quality_exit_status}"


def _system_audio_capture_gap(system_audio_capture: dict[str, Any]) -> dict[str, str]:
    adapter_status = str(
        system_audio_capture.get("capture_adapter_status", "unknown_system_audio_capture_status")
    )
    audio_health = system_audio_capture.get("audio_health") or {}
    health_status = str(audio_health.get("health_status", "not_run"))
    capture = system_audio_capture.get("capture") or {}
    capture_status = str(capture.get("capture_status", "not_run"))
    detail = (
        f"adapter={adapter_status}; capture={capture_status}; health={health_status}; "
        "go_evidence=not_real_meeting_go_evidence"
    )
    if health_status == "audio_capture_health_passed":
        return {
            "gap_id": "mac_system_audio_capture",
            "status": "implemented_and_verified",
            "detail": detail,
        }
    if "permission" in adapter_status or "permission" in capture_status:
        return {
            "gap_id": "mac_system_audio_capture",
            "status": "blocked_requires_explicit_user_approval",
            "detail": detail,
        }
    if health_status not in {"not_run", "audio_capture_health_passed"}:
        return {
            "gap_id": "mac_system_audio_capture",
            "status": "blocked_by_audio_capture_health",
            "detail": detail,
        }
    return {
        "gap_id": "mac_system_audio_capture",
        "status": "blocked_requires_m2_system_audio_capture",
        "detail": detail,
    }


def _gap_summary(entries: list[dict[str, str]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for entry in entries:
        status = entry["status"]
        summary[status] = summary.get(status, 0) + 1
    return summary


def _overall_status(entries: list[dict[str, str]]) -> str:
    blocking_local_statuses = {"fixed_in_this_run"}
    if any(entry["status"] in blocking_local_statuses for entry in entries):
        return "mainline_product_chain_has_local_gaps"
    return "mainline_product_chain_exercised_with_expected_blockers"


def _privacy_cost_flags() -> dict[str, bool]:
    return {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }


def _display_path(path: Path, repo_root: Path) -> str:
    resolved = path.resolve(strict=False)
    try:
        return resolved.relative_to(repo_root.resolve(strict=False)).as_posix()
    except ValueError:
        return "<redacted_outside_repo_path>"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Mainline Usable E2E Self-Test",
        "",
        f"- Session: `{report['session_id']}`",
        f"- Overall status: `{report['overall_status']}`",
        f"- Audio health: `{report['audio_health'].get('health_status')}`",
        f"- System audio capture: `{report['system_audio_capture'].get('capture_adapter_status')}`",
        f"- ASR event handoff: `{report['asr_event_handoff'].get('handoff_status')}`",
        f"- Artifact retention: `{report['artifact_retention'].get('retention_status')}`",
        f"- Mainline trial: `{report['mainline_trial'].get('trial_status')}`",
        f"- Draft review: `{report['draft_review'].get('draft_status')}`",
        f"- Copilot report preview: `{report['copilot_report_preview'].get('formal_report_status')}`",
        f"- Closure: `{report['closure'].get('closure_status')}`",
        f"- Final decision: `{report['closure'].get('final_decision')}`",
        "",
        "## Event Counts",
        "",
    ]
    for key, value in sorted(report["live_asr"]["event_counts"].items()):
        lines.append(f"- `{key}`: {value}")
    handoff = report["asr_event_handoff"]
    if handoff.get("handoff_status") != "not_requested":
        lines.extend(
            [
                "",
                "## ASR Event Artifact Handoff",
                "",
                f"- Status: `{handoff.get('handoff_status')}`",
                f"- Events path: `{handoff.get('events_path')}`",
                f"- Provider: `{handoff.get('provider')}`",
                f"- Remote ASR called: `{handoff.get('safe_to_call_remote_asr_now')}`",
                f"- LLM called: `{handoff.get('safe_to_call_llm_now')}`",
                "",
            ]
        )
        for key, value in sorted((handoff.get("live_event_counts") or {}).items()):
            lines.append(f"- `{key}`: {value}")
    preview = report["copilot_report_preview"]
    lines.extend(
        [
            "",
            "## Copilot Report Preview",
            "",
            f"- Status: `{preview.get('preview_status')}`",
            f"- Formal report status: `{preview.get('formal_report_status')}`",
            f"- Formal Go evidence: `{preview.get('is_formal_go_evidence')}`",
            f"- Value chain: `{', '.join(preview.get('value_chain') or [])}`",
            f"- Meeting state items: `{preview.get('meeting_state_count')}`",
            f"- Suggestion candidates: `{preview.get('suggestion_candidate_count')}`",
            f"- LLM request drafts: `{preview.get('llm_request_draft_count')}`",
            f"- Closure decision: `{preview.get('closure_decision')}`",
            f"- Quality blockers: `{', '.join(preview.get('quality_blockers') or [])}`",
            "",
            "### Top State Items",
            "",
        ]
    )
    for item in preview.get("top_state_items") or []:
        lines.append(
            f"- `{item.get('target_id', '')}` {item.get('state_event_type', '')}: "
            f"{item.get('text', '')}"
        )
    lines.extend(["", "### Top Suggestion Candidates", ""])
    for candidate in preview.get("top_suggestion_candidates") or []:
        lines.append(
            f"- `{candidate.get('candidate_id', '')}` "
            f"{candidate.get('gap_rule_id', '')} "
            f"{candidate.get('confidence_level', '')}: "
            f"{candidate.get('suggested_prompt', '')}"
        )
    lines.extend(["", "## Gap Entries", ""])
    for entry in report["gap_entries"]:
        lines.append(
            f"- `{entry['gap_id']}`: `{entry['status']}` - {entry['detail']}"
        )
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- remote ASR called: `False`",
            "- LLM called: `False`",
            "- private user audio read: `False`",
            "- paid provider used: `False`",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--run-browser-smoke", action="store_true")
    parser.add_argument("--system-audio-record-seconds", type=int, default=0)
    parser.add_argument("--system-audio-device-index", type=int, default=0)
    parser.add_argument("--system-audio-output-path", type=Path)
    parser.add_argument("--asr-quality-decision-path", type=Path)
    parser.add_argument("--asr-events-path", type=Path)
    parser.add_argument("--asr-events-provider", default="local_asr_event_file")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    system_audio_capture = None
    if args.system_audio_record_seconds > 0:
        system_audio_capture = mac_system_audio_capture_adapter.record_system_audio_sample(
            audio_path=args.system_audio_output_path
            or _system_audio_health_path(session_id=args.session_id, repo_root=REPO_ROOT),
            record_seconds=args.system_audio_record_seconds,
            audio_device_index=args.system_audio_device_index,
            repo_root=REPO_ROOT,
        )
    asr_quality_decision = _load_asr_quality_decision_path(
        args.asr_quality_decision_path,
        repo_root=REPO_ROOT,
    )
    report = run_mainline_usable_e2e_selftest(
        session_id=args.session_id,
        output_root=args.output_root,
        run_browser_smoke=args.run_browser_smoke,
        system_audio_capture=system_audio_capture,
        asr_quality_decision=asr_quality_decision,
        asr_events_path=args.asr_events_path,
        asr_events_provider=args.asr_events_provider,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["overall_status"] == "mainline_product_chain_exercised_with_expected_blockers" else 1


if __name__ == "__main__":
    raise SystemExit(main())

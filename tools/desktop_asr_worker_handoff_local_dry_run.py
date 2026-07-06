#!/usr/bin/env python3
"""Run a bounded local dry-run from desktop ASR descriptor to Web handoff API."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
DEFAULT_POLICY_PATH = DESKTOP_ROOT / "asr-worker-handoff-local-dry-run.policy.json"
PRELIGHT_TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_handoff_preflight.py"
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"

PCWEB_ID = "PCWEB-096"
POLICY_NAME = "Desktop ASR Worker Handoff Local Dry Run"
POLICY_STATUS = "desktop_asr_worker_handoff_local_dry_run_policy_only"
REPORT_MODE = "desktop_asr_worker_handoff_local_dry_run"
DEFAULT_MODE = "preview_only"
ALLOWED_MODES = ("preview_only", "synthetic_local_test")
HANDOFF_API_ENDPOINT = "/live/asr/local-event-files/sessions"
REQUIRED_PREFLIGHT_SOURCE = "PCWEB-095"
APPROVED_EVENT_FILE_ROOT = "artifacts/tmp/asr_events"
APPROVED_DATA_DIR_ROOT = "artifacts/tmp/desktop_handoff_dry_run"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

FALSE_SAFETY_FLAGS = (
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_runtime_session_now",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_preflight_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_handoff_preflight",
        PRELIGHT_TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ensure_web_import_path() -> None:
    for path in (WEB_BACKEND_ROOT, CORE_ROOT):
        path_text = str(path)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)


def _load_web_app_module():
    _ensure_web_import_path()
    from meeting_copilot_web_mvp import app as app_module

    return app_module


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _path_errors_for(path: Path, *, label: str) -> list[str]:
    errors: list[str] = []
    for path_label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"{label} is blocked: {path_label}")
    return errors


def _validate_data_dir(data_dir: Path, repo_root: Path) -> tuple[str, list[str]]:
    raw_errors = _path_errors_for(data_dir, label="data_dir")
    resolved = data_dir.resolve(strict=False)
    errors = list(raw_errors)
    for error in _path_errors_for(resolved, label="data_dir"):
        if error not in errors:
            errors.append(error)
    if errors:
        return "<redacted_invalid_path>", errors
    relative = _repo_relative_path(resolved, repo_root)
    if relative is None:
        return "<redacted_invalid_path>", ["data_dir is outside repository"]
    relative_text = relative.as_posix()
    if relative_text != APPROVED_DATA_DIR_ROOT and not relative_text.startswith(
        f"{APPROVED_DATA_DIR_ROOT}/"
    ):
        return "<redacted_invalid_path>", ["data_dir is not under approved dry-run root"]
    return relative_text, []


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-096")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop ASR Worker Handoff Local Dry Run")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be desktop_asr_worker_handoff_local_dry_run_policy_only")
    if policy.get("default_quality_gate_status") != "included_in_root_pytest":
        errors.append("default_quality_gate_status must be included_in_root_pytest")
    if policy.get("default_mode") != DEFAULT_MODE:
        errors.append("default_mode must be preview_only")
    if policy.get("allowed_modes") != list(ALLOWED_MODES):
        errors.append("allowed_modes must be ['preview_only', 'synthetic_local_test']")
    if policy.get("handoff_api_endpoint") != HANDOFF_API_ENDPOINT:
        errors.append("handoff_api_endpoint must be /live/asr/local-event-files/sessions")
    if policy.get("required_preflight_source") != REQUIRED_PREFLIGHT_SOURCE:
        errors.append("required_preflight_source must be PCWEB-095")
    if policy.get("approved_event_file_root") != APPROVED_EVENT_FILE_ROOT:
        errors.append("approved_event_file_root must be artifacts/tmp/asr_events")
    if policy.get("approved_data_dir_root") != APPROVED_DATA_DIR_ROOT:
        errors.append("approved_data_dir_root must be artifacts/tmp/desktop_handoff_dry_run")
    if policy.get("synthetic_local_test_status") != "explicit_mode_only":
        errors.append("synthetic_local_test_status must be explicit_mode_only")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report(
    *,
    policy_errors: list[str],
    preflight_report: dict[str, object],
    mode: str,
) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "mode": mode,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "preflight_status": preflight_report.get("preflight_status"),
        "preflight_errors": preflight_report.get("descriptor_validation_errors", []),
        "handoff_api_endpoint": HANDOFF_API_ENDPOINT,
        "future_web_handoff_request_preview": preflight_report.get(
            "future_web_handoff_request_preview"
        ),
        "event_file_path": preflight_report.get("event_file_path"),
        "data_dir": None,
        "data_dir_validation_errors": [],
        "event_file_read_status": "not_read",
        "web_handoff_mutation_status": "not_mutated",
        "web_handoff_response_status_code": None,
        "web_handoff_response_summary": None,
        "safe_to_read_approved_asr_event_file_now": False,
        "safe_to_mutate_temp_web_session_now": False,
        **_false_safety_flags(),
    }


def _summarize_web_handoff_response(payload: dict[str, Any]) -> dict[str, object]:
    event_source = payload.get("event_source") if isinstance(payload.get("event_source"), dict) else {}
    counts = payload.get("live_event_counts") if isinstance(payload.get("live_event_counts"), dict) else {}
    live_events = payload.get("live_events") if isinstance(payload.get("live_events"), list) else []
    evidence_span_count = _evidence_span_count(live_events)
    state_event_count = int(counts.get("state_event", 0) or 0)
    scheduler_event_count = int(counts.get("scheduler_event", 0) or 0)
    suggestion_candidate_count = int(counts.get("suggestion_candidate_event", 0) or 0)
    llm_request_draft_count = int(counts.get("llm_request_draft_event", 0) or 0)
    return {
        "session_id": payload.get("session_id"),
        "ingest_mode": payload.get("ingest_mode"),
        "event_source_provider": event_source.get("provider"),
        "transcript_final_count": counts.get("transcript_final", 0),
        "evidence_span_count": evidence_span_count,
        "state_event_count": state_event_count,
        "scheduler_event_count": scheduler_event_count,
        "suggestion_candidate_count": suggestion_candidate_count,
        "llm_request_draft_count": llm_request_draft_count,
        "suggestion_card_count": counts.get("suggestion_card", 0),
        "all_llm_statuses": payload.get("all_llm_statuses", []),
        "worker_to_web_live_session_closure_status": _live_session_closure_status(
            transcript_final_count=int(counts.get("transcript_final", 0) or 0),
            evidence_span_count=evidence_span_count,
            state_event_count=state_event_count,
            scheduler_event_count=scheduler_event_count,
            suggestion_candidate_count=suggestion_candidate_count,
            llm_request_draft_count=llm_request_draft_count,
        ),
    }


def _evidence_span_count(live_events: list[object]) -> int:
    count = 0
    for event in live_events:
        if not isinstance(event, dict):
            continue
        if event.get("event_type") not in {"transcript_final", "transcript_revision"}:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        evidence_spans = payload.get("evidence_spans")
        if isinstance(evidence_spans, list):
            count += len(evidence_spans)
    return count


def _live_session_closure_status(
    *,
    transcript_final_count: int,
    evidence_span_count: int,
    state_event_count: int,
    scheduler_event_count: int,
    suggestion_candidate_count: int,
    llm_request_draft_count: int,
) -> str:
    if transcript_final_count <= 0:
        return "blocked_no_transcript_final"
    if evidence_span_count <= 0:
        return "blocked_no_evidence_span"
    if state_event_count <= 0 or suggestion_candidate_count <= 0:
        return "blocked_no_state_or_gap_candidate"
    if scheduler_event_count <= 0:
        return "blocked_no_scheduler_event"
    if llm_request_draft_count <= 0:
        return "blocked_no_llm_request_draft"
    return "closed_to_evidence_state_gap"


def _run_synthetic_web_handoff(
    *,
    preview: dict[str, object],
    repo_root: Path,
    data_dir: Path,
) -> tuple[int, dict[str, Any]]:
    from fastapi.testclient import TestClient

    app_module = _load_web_app_module()
    original_repo_root = app_module.REPO_ROOT
    try:
        app_module.REPO_ROOT = repo_root
        client = TestClient(app_module.create_app(data_dir=data_dir))
        response = client.post(HANDOFF_API_ENDPOINT, json=preview)
        return response.status_code, response.json()
    finally:
        app_module.REPO_ROOT = original_repo_root


def build_desktop_asr_worker_handoff_local_dry_run_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    descriptor: dict[str, object] | None = None,
    mode: str = DEFAULT_MODE,
    repo_root: Path = REPO_ROOT,
    data_dir: Path | None = None,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    preflight = _load_preflight_module()
    preflight_report = preflight.build_asr_worker_handoff_preflight_report(
        descriptor=descriptor,
        repo_root=repo_root,
    )
    report = _base_report(policy_errors=policy_errors, preflight_report=preflight_report, mode=mode)

    if mode not in ALLOWED_MODES:
        report["dry_run_status"] = "blocked_by_mode_validation"
        report["mode_validation_errors"] = [f"mode is unsupported: {mode}"]
        return report
    report["mode_validation_errors"] = []

    if policy_errors:
        report["dry_run_status"] = "blocked_by_policy_validation"
        return report
    if preflight_report.get("preflight_status") != "ready_for_web_handoff_contract_review":
        report["dry_run_status"] = "blocked_by_preflight"
        return report

    if mode == "preview_only":
        report["dry_run_status"] = "preview_ready_no_web_mutation"
        return report

    target_data_dir = data_dir or repo_root / APPROVED_DATA_DIR_ROOT / "data"
    display_data_dir, data_dir_errors = _validate_data_dir(target_data_dir, repo_root)
    report["data_dir"] = display_data_dir
    report["data_dir_validation_errors"] = data_dir_errors
    if data_dir_errors:
        report["dry_run_status"] = "blocked_by_data_dir_validation"
        return report

    preview = preflight_report.get("future_web_handoff_request_preview")
    status_code, payload = _run_synthetic_web_handoff(
        preview=preview,
        repo_root=repo_root,
        data_dir=target_data_dir,
    )
    report["web_handoff_response_status_code"] = status_code
    if status_code != 201:
        report["dry_run_status"] = "blocked_by_web_handoff_response"
        report["web_handoff_response_summary"] = {
            "detail": payload.get("detail") if isinstance(payload, dict) else payload
        }
        return report

    report["dry_run_status"] = "synthetic_web_handoff_passed"
    report["event_file_read_status"] = "read_by_web_handoff_api"
    report["web_handoff_mutation_status"] = "mutated_temp_web_session"
    summary = _summarize_web_handoff_response(payload)
    report["web_handoff_response_summary"] = summary
    report["safe_to_read_approved_asr_event_file_now"] = True
    report["safe_to_mutate_temp_web_session_now"] = True
    if summary.get("worker_to_web_live_session_closure_status") != (
        "closed_to_evidence_state_gap"
    ):
        report["dry_run_status"] = "blocked_by_live_session_closure"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--descriptor-json")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=ALLOWED_MODES)
    parser.add_argument("--data-dir", type=Path)
    return parser.parse_args(argv)


def _descriptor_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("descriptor JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=args.policy_path,
        descriptor=_descriptor_from_json(args.descriptor_json),
        mode=args.mode,
        data_dir=args.data_dir,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if str(report.get("dry_run_status", "")).startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

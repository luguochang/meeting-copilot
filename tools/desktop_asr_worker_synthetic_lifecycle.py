#!/usr/bin/env python3
"""Run a bounded synthetic desktop ASR worker lifecycle harness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
DEFAULT_POLICY_PATH = DESKTOP_ROOT / "asr-worker-synthetic-lifecycle.policy.json"
COMMAND_PROTOCOL_TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_command_protocol.py"
LOCAL_DRY_RUN_TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_handoff_local_dry_run.py"
LOCAL_DRY_RUN_POLICY_PATH = DESKTOP_ROOT / "asr-worker-handoff-local-dry-run.policy.json"

PCWEB_ID = "PCWEB-100"
POLICY_NAME = "Desktop ASR Worker Synthetic Lifecycle Harness"
POLICY_STATUS = "desktop_asr_worker_synthetic_lifecycle_policy_only"
REPORT_MODE = "desktop_asr_worker_synthetic_lifecycle_harness"
REQUIRED_PREVIOUS_CONTRACTS = ("PCWEB-096", "PCWEB-099")
LIFECYCLE_MODE = "synthetic_event_file_lifecycle_only"
DEFAULT_MODE = "synthetic_lifecycle_test"
SYNTHETIC_LIFECYCLE_TEST_STATUS = "explicit_mode_only"
APPROVED_EVENT_FILE_ROOT = "artifacts/tmp/asr_events"
APPROVED_DATA_DIR_ROOT = "artifacts/tmp/desktop_handoff_dry_run"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
REQUIRED_COMMAND_SEQUENCE = (
    "worker.prepare",
    "worker.start",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
)
ALLOWED_SOURCE_KINDS_NOW = ("synthetic",)
FALSE_SAFETY_FLAGS = (
    "safe_to_spawn_worker_now",
    "safe_to_start_real_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_mutate_production_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
)
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _load_command_protocol_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_command_protocol",
        COMMAND_PROTOCOL_TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_local_dry_run_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_handoff_local_dry_run",
        LOCAL_DRY_RUN_TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "lifecycle_mode": LIFECYCLE_MODE,
        "default_mode": DEFAULT_MODE,
        "synthetic_lifecycle_test_status": SYNTHETIC_LIFECYCLE_TEST_STATUS,
        "approved_event_file_root": APPROVED_EVENT_FILE_ROOT,
        "approved_data_dir_root": APPROVED_DATA_DIR_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    if policy.get("required_previous_contracts") != list(REQUIRED_PREVIOUS_CONTRACTS):
        errors.append("required_previous_contracts must be ['PCWEB-096', 'PCWEB-099']")
    if policy.get("required_command_sequence") != list(REQUIRED_COMMAND_SEQUENCE):
        errors.append("required_command_sequence must match PCWEB-100 lifecycle sequence")
    if policy.get("allowed_source_kinds_now") != list(ALLOWED_SOURCE_KINDS_NOW):
        errors.append("allowed_source_kinds_now must be ['synthetic']")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report(*, policy_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "lifecycle_harness_status": "not_started",
        "command_protocol_validation_status": "not_started",
        "synthetic_handoff_status": "not_started",
        "errors": [],
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "lifecycle_mode": LIFECYCLE_MODE,
        "mode": DEFAULT_MODE,
        "required_command_sequence": list(REQUIRED_COMMAND_SEQUENCE),
        "command_sequence_observed": [],
        "state_timeline": [],
        "final_worker_state": "not_prepared",
        "event_file_read_status": "not_read",
        "web_handoff_mutation_status": "not_mutated",
        "web_handoff_response_summary": None,
        "safe_to_read_approved_asr_event_file_now": False,
        "safe_to_mutate_temp_web_session_now": False,
        **_false_safety_flags(),
    }


def _sequence_errors(command_requests: list[dict[str, object]]) -> list[str]:
    observed = [str(request.get("command_id")) for request in command_requests]
    if observed == list(REQUIRED_COMMAND_SEQUENCE):
        return []
    return [
        (
            "command sequence must be "
            "worker.prepare -> worker.start -> worker.collect_events -> worker.stop -> worker.cleanup"
        )
    ]


def _validate_command_requests(
    command_requests: list[dict[str, object]],
    *,
    repo_root: Path,
) -> list[str]:
    command_protocol = _load_command_protocol_module()
    errors: list[str] = []
    for index, request in enumerate(command_requests):
        report = command_protocol.build_desktop_asr_worker_command_protocol_report(
            command_request=request,
            repo_root=repo_root,
        )
        if report.get("command_request_validation_status") != "passed":
            request_errors = report.get("command_request_validation_errors", [])
            errors.extend([f"command_requests[{index}]: {error}" for error in request_errors])
    return errors


def _apply_synthetic_transition(
    *,
    command_id: str,
    current_state: str,
    state_timeline: list[dict[str, object]],
) -> str:
    transitions = {
        "worker.prepare": "prepared",
        "worker.start": "running",
        "worker.collect_events": current_state,
        "worker.stop": "stopped",
        "worker.cleanup": "cleaned",
    }
    next_state = transitions[command_id]
    state_timeline.append(
        {
            "command_id": command_id,
            "state_before": current_state,
            "state_after": next_state,
            "applied_by": "synthetic_lifecycle_harness",
        }
    )
    return next_state


def _run_synthetic_handoff(
    *,
    descriptor: dict[str, object],
    repo_root: Path,
    data_dir: Path | None,
) -> dict[str, object]:
    local_dry_run = _load_local_dry_run_module()
    return local_dry_run.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=LOCAL_DRY_RUN_POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=repo_root,
        data_dir=data_dir,
    )


def build_desktop_asr_worker_synthetic_lifecycle_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    descriptor: dict[str, object] | None = None,
    command_requests: list[dict[str, object]] | None = None,
    repo_root: Path = REPO_ROOT,
    data_dir: Path | None = None,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    report = _base_report(policy_errors=policy_errors)
    if policy_errors:
        report["lifecycle_harness_status"] = "blocked_by_policy_validation"
        return report

    resolved_requests = command_requests or []
    report["command_sequence_observed"] = [
        str(request.get("command_id")) for request in resolved_requests
    ]
    sequence_errors = _sequence_errors(resolved_requests)
    if sequence_errors:
        report["lifecycle_harness_status"] = "blocked_by_lifecycle_sequence"
        report["errors"] = sequence_errors
        return report

    command_errors = _validate_command_requests(resolved_requests, repo_root=repo_root)
    if command_errors:
        report["lifecycle_harness_status"] = "blocked_by_command_protocol"
        report["command_protocol_validation_status"] = "failed"
        report["errors"] = command_errors
        return report
    report["command_protocol_validation_status"] = "passed"

    current_state = "not_prepared"
    state_timeline: list[dict[str, object]] = []
    for request in resolved_requests:
        command_id = str(request["command_id"])
        current_state = _apply_synthetic_transition(
            command_id=command_id,
            current_state=current_state,
            state_timeline=state_timeline,
        )
        report["state_timeline"] = state_timeline
        report["final_worker_state"] = current_state

        if command_id != "worker.collect_events":
            continue

        handoff_report = _run_synthetic_handoff(
            descriptor=descriptor or {},
            repo_root=repo_root,
            data_dir=data_dir,
        )
        report["synthetic_handoff_status"] = str(handoff_report.get("dry_run_status"))
        report["event_file_read_status"] = str(handoff_report.get("event_file_read_status"))
        report["web_handoff_mutation_status"] = str(
            handoff_report.get("web_handoff_mutation_status")
        )
        report["web_handoff_response_summary"] = handoff_report.get(
            "web_handoff_response_summary"
        )
        report["safe_to_read_approved_asr_event_file_now"] = bool(
            handoff_report.get("safe_to_read_approved_asr_event_file_now")
        )
        report["safe_to_mutate_temp_web_session_now"] = bool(
            handoff_report.get("safe_to_mutate_temp_web_session_now")
        )
        if handoff_report.get("dry_run_status") != "synthetic_web_handoff_passed":
            report["lifecycle_harness_status"] = "blocked_by_synthetic_handoff"
            report["errors"] = list(handoff_report.get("preflight_errors", []))
            return report

    report["lifecycle_harness_status"] = "synthetic_lifecycle_completed"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--descriptor-json")
    parser.add_argument("--command-requests-json")
    parser.add_argument("--data-dir", type=Path)
    return parser.parse_args(argv)


def _descriptor_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("descriptor JSON must be an object")
    return payload


def _command_requests_from_json(text: str | None) -> list[dict[str, object]] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("command requests JSON must be a list of objects")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_desktop_asr_worker_synthetic_lifecycle_report(
        policy_path=args.policy_path,
        descriptor=_descriptor_from_json(args.descriptor_json),
        command_requests=_command_requests_from_json(args.command_requests_json),
        data_dir=args.data_dir,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if str(report.get("lifecycle_harness_status", "")).startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

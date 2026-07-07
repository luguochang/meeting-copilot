#!/usr/bin/env python3
"""Compose the desktop mic adapter and ASR worker contracts without side effects."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import desktop_asr_worker_command_protocol  # noqa: E402
import desktop_mic_adapter_contract  # noqa: E402


DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "worker-mic-connector-contract.policy.json"
)

PCWEB_ID = "PCWEB-112"
POLICY_NAME = "Desktop Worker Mic Connector Contract"
POLICY_STATUS = "desktop_worker_mic_connector_contract_policy_only"
REPORT_MODE = "desktop_worker_mic_connector_contract_static_report"
REQUIRED_PREVIOUS_CONTRACTS = [
    "PCWEB-099",
    "PCWEB-105",
    "PCWEB-107",
    "PCWEB-109",
    "DRV-039",
]
CONNECTOR_MODE = "worker_mic_connector_contract_only"
CONNECTOR_VERSION = "desktop_worker_mic_connector_contract.v1"
CONNECTOR_EXECUTION_STATUS = "not_bound_not_executed"
WORKER_MIC_SOURCE_STATUS = "requires_future_worker_mic_source_approval"
MIC_CAPTURE_STATUS = "not_started"
WORKER_EXECUTION_STATUS = "not_started"
WEB_HANDOFF_STATUS = "not_mutated"
APPROVED_RUNTIME_AUDIO_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime"
APPROVED_AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
APPROVED_WORKER_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
APPROVED_WORKER_EVENT_ROOT = "artifacts/tmp/asr_events"
FORBIDDEN_ROOTS = [
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
]
FALSE_SAFETY_FLAGS = [
    "safe_to_bind_worker_mic_connector_now",
    "safe_to_execute_connector_now",
    "safe_to_accept_mic_command_now",
    "safe_to_accept_worker_command_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_tauri_or_cargo_now",
]
REQUIRED_CONNECTOR_FIELDS = [
    "connector_version",
    "connector_id",
    "session_id",
    "adapter_id",
    "worker_id",
    "mic_source_kind",
    "worker_source_kind",
    "mic_command_id",
    "worker_command_id",
    "mic_current_state",
    "worker_current_state",
    "user_consent_state",
    "runtime_audio_root",
    "audio_chunk_root",
    "worker_runtime_root",
    "worker_event_output_path",
]
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_id_errors(value: Any, label: str) -> list[str]:
    if not _is_non_empty_string(value) or SAFE_ID_PATTERN.fullmatch(str(value)) is None:
        return [f"{label} must be a safe id"]
    return []


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "required_previous_contracts": REQUIRED_PREVIOUS_CONTRACTS,
        "connector_mode": CONNECTOR_MODE,
        "connector_version": CONNECTOR_VERSION,
        "connector_execution_status": CONNECTOR_EXECUTION_STATUS,
        "worker_mic_source_status": WORKER_MIC_SOURCE_STATUS,
        "mic_capture_status": MIC_CAPTURE_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        "approved_runtime_audio_root": APPROVED_RUNTIME_AUDIO_ROOT,
        "approved_audio_chunk_root": APPROVED_AUDIO_CHUNK_ROOT,
        "approved_worker_runtime_root": APPROVED_WORKER_RUNTIME_ROOT,
        "approved_worker_event_root": APPROVED_WORKER_EVENT_ROOT,
        "forbidden_roots": FORBIDDEN_ROOTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _blocked_policy_report(policy_errors: list[str]) -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed",
        "policy_validation_errors": policy_errors,
        "connector_request_validation_status": "not_evaluated",
        "connector_request_validation_errors": [],
        "worker_mic_connector_status": "blocked_by_policy_validation",
        "connector_execution_status": CONNECTOR_EXECUTION_STATUS,
        "mic_capture_status": MIC_CAPTURE_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        "runtime_audio_root": "<not_evaluated>",
        "audio_chunk_root": "<not_evaluated>",
        "worker_runtime_root": "<not_evaluated>",
        "worker_event_output_path": "<not_evaluated>",
        "mic_command_request_preview": None,
        "worker_command_request_preview": None,
        "worker_command_blocker": None,
        "connector_readiness_summary": None,
        **_false_safety_flags(),
    }


def _default_report() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "passed",
        "policy_validation_errors": [],
        "connector_request_validation_status": "not_provided",
        "connector_request_validation_errors": [],
        "worker_mic_connector_status": "specified_not_executable",
        "connector_execution_status": CONNECTOR_EXECUTION_STATUS,
        "mic_capture_status": MIC_CAPTURE_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        "runtime_audio_root": "<not_provided>",
        "audio_chunk_root": "<not_provided>",
        "worker_runtime_root": "<not_provided>",
        "worker_event_output_path": "<not_provided>",
        "mic_command_request_preview": None,
        "worker_command_request_preview": None,
        "worker_command_blocker": None,
        "connector_readiness_summary": None,
        **_false_safety_flags(),
    }


def _connector_request_errors(connector_request: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_CONNECTOR_FIELDS:
        if field not in connector_request:
            errors.append(f"{field} is required")
    if connector_request.get("connector_version") != CONNECTOR_VERSION:
        errors.append(f"connector_version must be {CONNECTOR_VERSION}")
    for field in ("connector_id", "session_id", "adapter_id", "worker_id"):
        errors.extend(_safe_id_errors(connector_request.get(field), field))
    session_id = connector_request.get("session_id")
    if (
        _is_non_empty_string(session_id)
        and (connector_request.get("adapter_id") != session_id or connector_request.get("worker_id") != session_id)
    ):
        errors.append("adapter_id and worker_id must match session_id")
    if connector_request.get("mic_source_kind") != "mic":
        errors.append("mic_source_kind must be mic")
    if connector_request.get("worker_source_kind") != "mic":
        errors.append("worker_source_kind must be mic")
    if connector_request.get("mic_command_id") != "mic_adapter.start":
        errors.append("mic_command_id must be mic_adapter.start")
    if connector_request.get("worker_command_id") != "worker.prepare":
        errors.append("worker_command_id must be worker.prepare")
    return errors


def _mic_command_request(connector_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": desktop_mic_adapter_contract.CONTRACT_VERSION,
        "command_id": connector_request.get("mic_command_id"),
        "request_id": f"{connector_request.get('connector_id')}_mic_start",
        "session_id": connector_request.get("session_id"),
        "adapter_id": connector_request.get("adapter_id"),
        "source_kind": connector_request.get("mic_source_kind"),
        "current_state": connector_request.get("mic_current_state"),
        "requested_state_after": "recording",
        "runtime_audio_root": connector_request.get("runtime_audio_root"),
        "audio_chunk_root": connector_request.get("audio_chunk_root"),
        "user_consent_state": connector_request.get("user_consent_state"),
    }


def _worker_command_request(connector_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_version": desktop_asr_worker_command_protocol.PROTOCOL_VERSION,
        "command_id": connector_request.get("worker_command_id"),
        "request_id": f"{connector_request.get('connector_id')}_worker_prepare",
        "session_id": connector_request.get("session_id"),
        "worker_id": connector_request.get("worker_id"),
        "source_kind": connector_request.get("worker_source_kind"),
        "current_state": connector_request.get("worker_current_state"),
        "requested_state_after": "prepared",
        "event_output_path": connector_request.get("worker_event_output_path"),
        "runtime_root": connector_request.get("worker_runtime_root"),
    }


def _worker_blocker(worker_report: dict[str, Any]) -> str | None:
    errors = worker_report.get("command_request_validation_errors") or []
    for error in errors:
        if isinstance(error, str) and error.startswith("source_kind requires later approval"):
            return error
    return errors[0] if errors else None


def _request_report(
    *,
    connector_request: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    connector_errors = _connector_request_errors(connector_request)
    mic_report = desktop_mic_adapter_contract.build_desktop_mic_adapter_contract_report(
        mic_command_request=_mic_command_request(connector_request),
        repo_root=repo_root,
    )
    worker_report = desktop_asr_worker_command_protocol.build_desktop_asr_worker_command_protocol_report(
        command_request=_worker_command_request(connector_request),
        repo_root=repo_root,
    )

    connector_errors.extend(mic_report.get("mic_command_request_validation_errors") or [])
    worker_errors = worker_report.get("command_request_validation_errors") or []
    non_source_worker_errors = [
        error
        for error in worker_errors
        if not (
            isinstance(error, str)
            and error.startswith("source_kind requires later approval: mic")
        )
    ]
    connector_errors.extend(non_source_worker_errors)

    status = "passed" if not connector_errors else "failed"
    worker_source_blocker = _worker_blocker(worker_report)
    mic_preview = mic_report.get("mic_command_response_preview")
    worker_request_preview = dict(_worker_command_request(connector_request))
    return {
        "connector_request_validation_status": status,
        "connector_request_validation_errors": connector_errors,
        "worker_mic_connector_status": (
            "ready_for_worker_mic_connector_contract_review"
            if status == "passed"
            else "blocked_by_connector_request_validation"
        ),
        "runtime_audio_root": mic_report.get("runtime_audio_root"),
        "audio_chunk_root": mic_report.get("audio_chunk_root"),
        "worker_runtime_root": worker_report.get("runtime_root"),
        "worker_event_output_path": worker_report.get("event_output_path"),
        "mic_command_request_preview": mic_preview,
        "worker_command_request_preview": worker_request_preview,
        "worker_command_blocker": worker_source_blocker,
        "connector_readiness_summary": (
            {
                "mic_contract_status": mic_report.get("mic_adapter_contract_status"),
                "worker_command_protocol_status": worker_report.get("command_protocol_status"),
                "worker_mic_source_status": WORKER_MIC_SOURCE_STATUS,
                "connector_execution_status": CONNECTOR_EXECUTION_STATUS,
                "next_required_decision": "approve_worker_mic_source_after_real_tauri_noop_run",
            }
            if status == "passed"
            else None
        ),
    }


def build_desktop_worker_mic_connector_contract_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    connector_request: dict[str, Any] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    if connector_request is None:
        return _default_report()

    request_report = _request_report(
        connector_request=connector_request,
        repo_root=repo_root,
    )
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "passed",
        "policy_validation_errors": [],
        "connector_execution_status": CONNECTOR_EXECUTION_STATUS,
        "mic_capture_status": MIC_CAPTURE_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        **request_report,
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--connector-request-json")
    return parser.parse_args(argv)


def _connector_request_from_json(text: str | None) -> dict[str, Any] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("connector request JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_desktop_worker_mic_connector_contract_report(
        policy_path=args.policy_path,
        connector_request=_connector_request_from_json(args.connector_request_json),
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 1 if str(report.get("worker_mic_connector_status", "")).startswith("blocked_") else 0


if __name__ == "__main__":
    raise SystemExit(main())

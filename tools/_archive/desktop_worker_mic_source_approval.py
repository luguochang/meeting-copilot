#!/usr/bin/env python3
"""Build a manual worker mic source approval packet without executing anything."""

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

import desktop_tauri_noop_run_result_intake  # noqa: E402
import desktop_worker_mic_connector_contract  # noqa: E402


DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "worker-mic-source-approval.policy.json"
)

PCWEB_ID = "PCWEB-114"
POLICY_NAME = "Desktop Worker Mic Source Approval Packet"
POLICY_STATUS = "desktop_worker_mic_source_approval_policy_only"
REPORT_MODE = "desktop_worker_mic_source_approval_static_report"
APPROVAL_MODE = "manual_review_packet_only"
APPROVAL_SCOPE = "allow_worker_prepare_source_kind_mic_after_manual_approval"
APPROVAL_STATUS = "not_approved"
REQUIRED_PREVIOUS_CONTRACTS = ["PCWEB-112", "PCWEB-113"]
REQUIRED_CONNECTOR_STATUS = "ready_for_worker_mic_connector_contract_review"
REQUIRED_TAURI_EVIDENCE_STATUS = "ready_for_worker_mic_source_approval_review"
NEXT_DECISION_MISSING = "provide_connector_request_and_valid_tauri_noop_result_or_keep_blocked"
NEXT_DECISION_MANUAL = "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked"
FORBIDDEN_ROOTS = [
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
]
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
    "approved_to_execute_now",
    "safe_to_accept_worker_mic_source_now",
    "safe_to_execute_worker_prepare_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_" + "car" + "go_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _policy_path_errors(path: Path) -> list[str]:
    paths_to_check = [path, path.resolve(strict=False)]
    for candidate in paths_to_check:
        if candidate.suffix.casefold() == ".m4a":
            return ["policy path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"policy path is blocked: {label}"]
    return []


def _canonical_payload() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "approval_mode": APPROVAL_MODE,
        "approval_scope": APPROVAL_SCOPE,
        "worker_mic_source_approval_status": APPROVAL_STATUS,
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "required_connector_status": REQUIRED_CONNECTOR_STATUS,
        "required_tauri_evidence_status": REQUIRED_TAURI_EVIDENCE_STATUS,
        "forbidden_roots": list(FORBIDDEN_ROOTS),
        **_false_safety_flags(),
    }


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "approval_mode": APPROVAL_MODE,
        "approval_scope": APPROVAL_SCOPE,
        "worker_mic_source_approval_status": APPROVAL_STATUS,
        "required_previous_contracts": REQUIRED_PREVIOUS_CONTRACTS,
        "required_connector_status": REQUIRED_CONNECTOR_STATUS,
        "required_tauri_evidence_status": REQUIRED_TAURI_EVIDENCE_STATUS,
        "forbidden_roots": FORBIDDEN_ROOTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must match PCWEB-114 policy")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report() -> dict[str, Any]:
    return {
        **_canonical_payload(),
        "report_mode": REPORT_MODE,
        "policy_validation_status": "not_run",
        "policy_validation_errors": [],
        "connector_request_validation_status": "not_provided",
        "tauri_result_validation_status": "not_provided",
        "worker_mic_source_approval_packet_status": "blocked_missing_required_evidence",
        "approval_blockers": [],
        "connector_session_id": "<not_provided>",
        "tauri_run_id": "<not_provided>",
        "worker_source_kind": "<not_provided>",
        "worker_command_blocker": None,
        "real_tauri_noop_run_evidence_status": "not_available",
        "runtime_audio_root": "<not_provided>",
        "audio_chunk_root": "<not_provided>",
        "worker_runtime_root": "<not_provided>",
        "worker_event_output_path": "<not_provided>",
        "manual_review_packet": None,
        "next_required_decision": NEXT_DECISION_MISSING,
    }


def _blocked_policy_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "failed",
            "policy_validation_errors": errors,
            "worker_mic_source_approval_packet_status": "blocked_by_policy_validation",
            "approval_blockers": list(errors),
        }
    )
    return report


def _blocked_policy_path_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "blocked_by_policy_path_guard",
            "policy_validation_errors": errors,
            "worker_mic_source_approval_packet_status": "blocked_by_policy_path_guard",
            "approval_blockers": list(errors),
        }
    )
    return report


def _connector_report(connector_request: dict[str, Any] | None) -> dict[str, Any]:
    return desktop_worker_mic_connector_contract.build_desktop_worker_mic_connector_contract_report(
        connector_request=connector_request
    )


def _tauri_report(tauri_run_result: dict[str, Any] | None) -> dict[str, Any]:
    return desktop_tauri_noop_run_result_intake.build_tauri_noop_run_result_intake_report(
        run_result=tauri_run_result
    )


def _run_id(tauri_run_result: dict[str, Any] | None) -> str:
    value = tauri_run_result.get("run_id") if isinstance(tauri_run_result, dict) else None
    return value if isinstance(value, str) else "<not_provided>"


def _copy_safe_connector_fields(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "runtime_audio_root": report.get("runtime_audio_root", "<not_provided>"),
        "audio_chunk_root": report.get("audio_chunk_root", "<not_provided>"),
        "worker_runtime_root": report.get("worker_runtime_root", "<not_provided>"),
        "worker_event_output_path": report.get("worker_event_output_path", "<not_provided>"),
        "worker_command_blocker": report.get("worker_command_blocker"),
    }


def _blocked_status(
    *,
    connector_status: str,
    tauri_status: str,
    connector_errors: list[str],
    tauri_errors: list[str],
) -> tuple[str, list[str]]:
    if connector_status == "not_provided" or tauri_status == "not_provided":
        blockers: list[str] = []
        if connector_status == "not_provided":
            blockers.append("connector_request is required")
        if tauri_status == "not_provided":
            blockers.append("valid_tauri_noop_run_result is required")
        return "blocked_missing_required_evidence", blockers
    if connector_status != "passed":
        return "blocked_by_connector_request_validation", list(connector_errors)
    if tauri_status != "passed":
        return "blocked_by_tauri_noop_result_validation", list(tauri_errors)
    return "ready_for_manual_review_not_executable", []


def _manual_review_packet(
    *,
    connector_report: dict[str, Any],
    tauri_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "approval_scope": APPROVAL_SCOPE,
        "connector_status": connector_report.get("worker_mic_connector_status"),
        "tauri_noop_run_result_status": tauri_report.get("tauri_noop_run_result_status"),
        "worker_prepare_source_kind": "mic",
        "worker_prepare_blocker_to_remove_later": connector_report.get("worker_command_blocker"),
        "execution_status_after_packet": "still_not_executable",
        "next_required_decision": NEXT_DECISION_MANUAL,
    }


def build_worker_mic_source_approval_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    connector_request: dict[str, Any] | None = None,
    tauri_run_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_path_errors = _policy_path_errors(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy if isinstance(policy, dict) else {})
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    connector_report = _connector_report(connector_request)
    tauri_report = _tauri_report(tauri_run_result)

    connector_status = str(connector_report.get("connector_request_validation_status"))
    tauri_status = str(tauri_report.get("result_validation_status"))
    connector_errors = connector_report.get("connector_request_validation_errors") or []
    tauri_errors = tauri_report.get("result_validation_errors") or []

    packet_status, blockers = _blocked_status(
        connector_status=connector_status,
        tauri_status=tauri_status,
        connector_errors=connector_errors,
        tauri_errors=tauri_errors,
    )

    connector_session_id = (
        connector_request.get("session_id")
        if isinstance(connector_request, dict) and isinstance(connector_request.get("session_id"), str)
        else "<not_provided>"
    )
    tauri_run_id = _run_id(tauri_run_result)
    if packet_status == "ready_for_manual_review_not_executable" and tauri_run_id != connector_session_id:
        packet_status = "blocked_by_cross_session_evidence"
        blockers = ["tauri run_id must match connector session_id"]

    report = _base_report()
    report.update(
        {
            "policy_validation_status": "passed",
            "connector_request_validation_status": connector_status,
            "tauri_result_validation_status": tauri_status,
            "worker_mic_source_approval_packet_status": packet_status,
            "approval_blockers": blockers,
            "connector_session_id": connector_session_id,
            "tauri_run_id": tauri_run_id,
            "worker_source_kind": (
                connector_request.get("worker_source_kind")
                if isinstance(connector_request, dict)
                and isinstance(connector_request.get("worker_source_kind"), str)
                else "<not_provided>"
            ),
            "real_tauri_noop_run_evidence_status": tauri_report.get(
                "real_tauri_noop_run_evidence_status", "not_available"
            ),
            **_copy_safe_connector_fields(connector_report),
        }
    )

    if packet_status == "ready_for_manual_review_not_executable":
        report.update(
            {
                "manual_review_packet": _manual_review_packet(
                    connector_report=connector_report,
                    tauri_report=tauri_report,
                ),
                "next_required_decision": NEXT_DECISION_MANUAL,
            }
        )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--connector-request-json")
    parser.add_argument("--tauri-run-result-json")
    return parser.parse_args(argv)


def _json_object_from_text(text: str | None, label: str) -> dict[str, Any] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_worker_mic_source_approval_report(
        policy_path=args.policy_path,
        connector_request=_json_object_from_text(
            args.connector_request_json,
            "connector request",
        ),
        tauri_run_result=_json_object_from_text(
            args.tauri_run_result_json,
            "Tauri run result",
        ),
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return (
        0
        if report.get("worker_mic_source_approval_packet_status")
        == "ready_for_manual_review_not_executable"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

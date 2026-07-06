#!/usr/bin/env python3
"""Build single-shadow-test worker mic source approval evidence without execution."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "worker-mic-source-single-shadow-approval.policy.json"
)

PCWEB_ID = "PCWEB-123"
POLICY_NAME = "Desktop Worker Mic Source Single Shadow Approval"
POLICY_STATUS = "desktop_worker_mic_source_single_shadow_approval_policy_only"
REPORT_MODE = "desktop_worker_mic_source_single_shadow_approval_static_report"
APPROVAL_MODE = "single_shadow_test_approval_evidence_only"
REQUIRED_PREVIOUS_CONTRACTS = ["PCWEB-114", "PCWEB-120", "PCWEB-122"]
REQUIRED_PACKET_STATUS = "ready_for_manual_review_not_executable"
APPROVED_STATUS = "manually_approved_for_single_shadow_test"
NOT_APPROVED_STATUS = "not_approved"
APPROVAL_SCOPE = "single_user_real_mic_shadow_test_only"
APPROVAL_RECORD_VERSION = "worker_mic_source_single_shadow_approval.v1"
APPROVAL_TOKEN = "APPROVE_WORKER_MIC_SOURCE_FOR_SINGLE_SHADOW_TEST_NO_EXECUTION"
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
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
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
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "required_packet_status": REQUIRED_PACKET_STATUS,
        "approved_status": APPROVED_STATUS,
        "approval_scope": APPROVAL_SCOPE,
        "approval_token": APPROVAL_TOKEN,
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
        "required_previous_contracts": REQUIRED_PREVIOUS_CONTRACTS,
        "required_packet_status": REQUIRED_PACKET_STATUS,
        "approved_status": APPROVED_STATUS,
        "approval_scope": APPROVAL_SCOPE,
        "approval_token": APPROVAL_TOKEN,
        "forbidden_roots": FORBIDDEN_ROOTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must match PCWEB-123 policy")
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
        "manual_review_packet_validation_status": "not_provided",
        "approval_record_validation_status": "not_provided",
        "approval_evidence_status": "blocked_missing_manual_review_packet",
        "approval_blockers": [],
        "worker_mic_source_approval_packet_status": "<not_provided>",
        "worker_mic_source_approval_status": NOT_APPROVED_STATUS,
        "connector_session_id": "<not_provided>",
        "approved_connector_session_id": "<not_provided>",
        "approval_id": "<not_provided>",
        "next_required_decision": "provide_manual_review_packet_and_single_shadow_approval_record",
    }


def _blocked_policy_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "failed",
            "policy_validation_errors": errors,
            "approval_evidence_status": "blocked_by_policy_validation",
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
            "approval_evidence_status": "blocked_by_policy_path_guard",
            "approval_blockers": list(errors),
        }
    )
    return report


def _safe_id_errors(value: object, label: str) -> list[str]:
    if not isinstance(value, str) or SAFE_ID_PATTERN.fullmatch(value) is None:
        return [f"{label} must be a safe id"]
    return []


def validate_manual_review_packet_report(report: dict[str, Any] | None) -> tuple[str, list[str]]:
    if report is None:
        return "not_provided", ["manual_review_packet_report is required"]
    errors: list[str] = []
    if report.get("worker_mic_source_approval_packet_status") != REQUIRED_PACKET_STATUS:
        errors.append(f"worker_mic_source_approval_packet_status must be {REQUIRED_PACKET_STATUS}")
    if report.get("worker_mic_source_approval_status") != NOT_APPROVED_STATUS:
        errors.append("worker_mic_source_approval_status must be not_approved before PCWEB-123")
    if report.get("connector_session_id") != report.get("tauri_run_id"):
        errors.append("connector_session_id must match tauri_run_id")
    if report.get("worker_source_kind") != "mic":
        errors.append("worker_source_kind must be mic")
    if report.get("worker_command_blocker") != "source_kind requires later approval: mic":
        errors.append("worker_command_blocker must still require later mic approval")
    if not isinstance(report.get("manual_review_packet"), dict):
        errors.append("manual_review_packet must be present")
    for flag in FALSE_SAFETY_FLAGS:
        if report.get(flag) is not False:
            errors.append(f"{flag} must be false")
    connector_session_id = report.get("connector_session_id")
    errors.extend(_safe_id_errors(connector_session_id, "connector_session_id"))
    return ("failed" if errors else "passed"), errors


def validate_approval_record(
    approval_record: dict[str, Any] | None,
    *,
    connector_session_id: str,
) -> tuple[str, list[str]]:
    if approval_record is None:
        return "not_provided", ["approval_record is required"]
    errors: list[str] = []
    if approval_record.get("approval_record_version") != APPROVAL_RECORD_VERSION:
        errors.append(f"approval_record_version must be {APPROVAL_RECORD_VERSION}")
    if approval_record.get("approval_token") != APPROVAL_TOKEN:
        errors.append("approval_token must match single-shadow-test approval token")
    if approval_record.get("approval_scope") != APPROVAL_SCOPE:
        errors.append(f"approval_scope must be {APPROVAL_SCOPE}")
    if approval_record.get("approved_connector_session_id") != connector_session_id:
        errors.append("approved_connector_session_id must match connector_session_id")
    errors.extend(_safe_id_errors(approval_record.get("approval_id"), "approval_id"))
    return ("failed" if errors else "passed"), errors


def build_worker_mic_source_single_shadow_approval_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    manual_review_packet_report: dict[str, Any] | None = None,
    approval_record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_path_errors = _policy_path_errors(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy if isinstance(policy, dict) else {})
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    packet_status, packet_errors = validate_manual_review_packet_report(
        manual_review_packet_report
    )
    connector_session_id = (
        manual_review_packet_report.get("connector_session_id")
        if isinstance(manual_review_packet_report, dict)
        and isinstance(manual_review_packet_report.get("connector_session_id"), str)
        else "<not_provided>"
    )
    approval_status, approval_errors = validate_approval_record(
        approval_record,
        connector_session_id=connector_session_id,
    )
    blockers = [*packet_errors, *approval_errors]

    report = _base_report()
    report.update(
        {
            "policy_validation_status": "passed",
            "manual_review_packet_validation_status": packet_status,
            "approval_record_validation_status": approval_status,
            "approval_blockers": blockers,
            "worker_mic_source_approval_packet_status": (
                manual_review_packet_report.get("worker_mic_source_approval_packet_status")
                if isinstance(manual_review_packet_report, dict)
                else "<not_provided>"
            ),
            "connector_session_id": connector_session_id,
            "approved_connector_session_id": (
                approval_record.get("approved_connector_session_id")
                if isinstance(approval_record, dict)
                and isinstance(approval_record.get("approved_connector_session_id"), str)
                else "<not_provided>"
            ),
            "approval_id": (
                approval_record.get("approval_id")
                if isinstance(approval_record, dict)
                and isinstance(approval_record.get("approval_id"), str)
                else "<not_provided>"
            ),
        }
    )
    if packet_status == "not_provided":
        report["approval_evidence_status"] = "blocked_missing_manual_review_packet"
    elif approval_status == "not_provided":
        report["approval_evidence_status"] = "blocked_missing_approval_record"
    elif blockers:
        report["approval_evidence_status"] = "blocked_by_approval_validation"
    else:
        report.update(
            {
                "approval_evidence_status": "single_shadow_test_approval_evidence_ready",
                "worker_mic_source_approval_status": APPROVED_STATUS,
                "worker_mic_source_approval_packet_status": REQUIRED_PACKET_STATUS,
                "next_required_decision": (
                    "worker_mic_source_single_shadow_approval_ready_still_no_execution"
                ),
            }
        )
    return report


def _json_object_from_text(text: str | None, label: str) -> dict[str, Any] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} JSON must be an object")
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--manual-review-packet-json")
    parser.add_argument("--approval-record-json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_worker_mic_source_single_shadow_approval_report(
        policy_path=args.policy_path,
        manual_review_packet_report=_json_object_from_text(
            args.manual_review_packet_json,
            "manual review packet",
        ),
        approval_record=_json_object_from_text(
            args.approval_record_json,
            "approval record",
        ),
    )
    json.dump(report, out, ensure_ascii=False, indent=2, sort_keys=True)
    out.write("\n")
    return (
        0
        if report.get("approval_evidence_status") == "single_shadow_test_approval_evidence_ready"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

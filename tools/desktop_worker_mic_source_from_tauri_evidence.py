#!/usr/bin/env python3
"""Build a worker mic source review packet from validated Tauri no-op evidence."""

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

import desktop_tauri_noop_run_result_intake  # noqa: E402
import desktop_worker_mic_source_approval  # noqa: E402


DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "worker-mic-source-from-tauri-evidence.policy.json"
)

PCWEB_ID = "PCWEB-120"
POLICY_NAME = "Desktop Worker Mic Source From Tauri Evidence"
POLICY_STATUS = "desktop_worker_mic_source_from_tauri_evidence_policy_only"
REPORT_MODE = "desktop_worker_mic_source_from_tauri_evidence_static_report"
EVIDENCE_SOURCE_KIND = "pcweb_119_capture_evidence_json"
APPROVAL_MODE = "manual_review_packet_from_real_tauri_evidence_only"
REQUIRED_PREVIOUS_CONTRACTS = ["PCWEB-112", "PCWEB-114", "PCWEB-119"]
ALLOWED_EVIDENCE_ROOT = "artifacts/tmp/desktop_tauri_noop_run_results"
CONNECTOR_CONSENT_SCOPE = "tauri_noop_ipc_only_not_real_audio_capture"
APPROVED_RUNTIME_AUDIO_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime"
APPROVED_AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
APPROVED_WORKER_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
APPROVED_WORKER_EVENT_ROOT = "artifacts/tmp/asr_events"
CAPTURE_VERSION = "desktop_tauri_noop_webview_run_capture.v1"
CAPTURE_STATUS = "captured_validated_tauri_noop_run"
VALIDATION_ENDPOINT = "/desktop/tauri-noop-run-results/validations"
READY_EVIDENCE_STATUS = "ready_for_worker_mic_source_approval_review"
NEXT_DECISION_MISSING = "provide_valid_pcweb119_tauri_evidence_or_keep_blocked"
NEXT_DECISION_MANUAL = "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked"
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
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


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _path_guard_errors(path: Path) -> list[str]:
    errors: list[str] = []
    for candidate in (path, path.resolve(strict=False)):
        if candidate.suffix.casefold() == ".m4a":
            errors.append("path is blocked: audio file")
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            error = f"path is blocked: {label}"
            if _path_has_suffix_parts(candidate, suffix_parts) and error not in errors:
                errors.append(error)
    return errors


def _evidence_path_errors(path: Path) -> list[str]:
    guard_errors = _path_guard_errors(path)
    if guard_errors:
        return guard_errors[:1]
    relative = _repo_relative_path(path)
    if relative is None:
        return ["tauri_evidence_path must be under approved artifacts root"]
    relative_text = relative.as_posix()
    if not (
        relative_text == ALLOWED_EVIDENCE_ROOT
        or relative_text.startswith(f"{ALLOWED_EVIDENCE_ROOT}/")
    ):
        return ["tauri_evidence_path must be under approved artifacts root"]
    if path.suffix != ".json":
        return ["tauri_evidence_path must be a JSON file"]
    if not path.is_file():
        return ["tauri_evidence_path must exist"]
    return []


def _policy_path_errors(path: Path) -> list[str]:
    guard_errors = _path_guard_errors(path)
    if guard_errors:
        return guard_errors[:1]
    relative = _repo_relative_path(path)
    if relative is None:
        return ["policy path must be under repository"]
    return []


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "evidence_source_kind": EVIDENCE_SOURCE_KIND,
        "approval_mode": APPROVAL_MODE,
        "required_previous_contracts": REQUIRED_PREVIOUS_CONTRACTS,
        "allowed_evidence_root": ALLOWED_EVIDENCE_ROOT,
        "connector_consent_scope": CONNECTOR_CONSENT_SCOPE,
        "approved_runtime_audio_root": APPROVED_RUNTIME_AUDIO_ROOT,
        "approved_audio_chunk_root": APPROVED_AUDIO_CHUNK_ROOT,
        "approved_worker_runtime_root": APPROVED_WORKER_RUNTIME_ROOT,
        "approved_worker_event_root": APPROVED_WORKER_EVENT_ROOT,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must match PCWEB-120 policy")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "report_mode": REPORT_MODE,
        "evidence_source_kind": EVIDENCE_SOURCE_KIND,
        "approval_mode": APPROVAL_MODE,
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "allowed_evidence_root": ALLOWED_EVIDENCE_ROOT,
        "connector_consent_scope": CONNECTOR_CONSENT_SCOPE,
        "policy_validation_status": "not_run",
        "policy_validation_errors": [],
        "tauri_evidence_read_status": "not_requested",
        "tauri_evidence_validation_status": "not_provided",
        "tauri_evidence_validation_errors": [],
        "tauri_evidence_status": "not_available",
        "tauri_evidence_path": "<not_provided>",
        "tauri_run_id": "<not_provided>",
        "derived_connector_request_status": "not_derived",
        "connector_session_id": "<not_provided>",
        "worker_mic_source_approval_packet_status": "blocked_missing_pcweb119_evidence",
        "worker_mic_source_approval_status": "not_approved",
        "real_tauri_noop_run_evidence_status": "not_available",
        "manual_review_packet": None,
        "next_required_decision": NEXT_DECISION_MISSING,
        **_false_safety_flags(),
    }


def _blocked_policy_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "failed",
            "policy_validation_errors": errors,
            "worker_mic_source_approval_packet_status": "blocked_by_policy_validation",
            "tauri_evidence_validation_errors": errors,
        }
    )
    return report


def _safe_run_id(run_result: dict[str, Any]) -> str:
    run_id = run_result.get("run_id")
    return run_id if isinstance(run_id, str) else "<not_provided>"


def _validate_tauri_evidence(evidence: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if evidence.get("capture_version") != CAPTURE_VERSION:
        errors.append("capture_version must be desktop_tauri_noop_webview_run_capture.v1")
    if evidence.get("capture_status") != CAPTURE_STATUS:
        errors.append("capture_status must be captured_validated_tauri_noop_run")
    if evidence.get("source_endpoint") != VALIDATION_ENDPOINT:
        errors.append("source_endpoint must be /desktop/tauri-noop-run-results/validations")
    if evidence.get("validated_command_count") != 10:
        errors.append("validated_command_count must be 10")
    if evidence.get("returned_command_count") != 10:
        errors.append("returned_command_count must be 10")
    for flag in (
        "safe_to_request_audio_permission_now",
        "safe_to_capture_audio_now",
        "safe_to_start_asr_worker_now",
        "safe_to_read_audio_chunk_now",
        "safe_to_write_audio_chunk_now",
        "safe_to_read_configs_local_now",
        "safe_to_call_remote_asr_now",
        "safe_to_call_llm_now",
    ):
        if evidence.get(flag) is not False:
            errors.append(f"{flag} must be false")

    run_result = evidence.get("run_result")
    if not isinstance(run_result, dict):
        errors.append("run_result must be an object")
        return errors, None
    run_id = _safe_run_id(run_result)
    if SAFE_ID_PATTERN.fullmatch(run_id) is None:
        errors.append("run_result.run_id must be a safe id")
    if run_result.get("run_environment") != "tauri_webview":
        errors.append("run_result.run_environment must be tauri_webview")
    if run_result.get("explicit_tauri_run_approval_recorded") is not True:
        errors.append("run_result.explicit_tauri_run_approval_recorded must be true")
    if run_result.get("web_app_url_status") != "local_dev_url_loaded":
        errors.append("run_result.web_app_url_status must be local_dev_url_loaded")
    if run_result.get("ipc_transport_status") != "tauri_ipc_available":
        errors.append("run_result.ipc_transport_status must be tauri_ipc_available")

    validation_report = evidence.get("validation_report")
    if not isinstance(validation_report, dict):
        errors.append("validation_report must be an object")
    else:
        if validation_report.get("result_validation_status") != "passed":
            errors.append("validation_report.result_validation_status must be passed")
        if validation_report.get("real_tauri_noop_run_evidence_status") != READY_EVIDENCE_STATUS:
            errors.append(
                "validation_report.real_tauri_noop_run_evidence_status must be ready_for_worker_mic_source_approval_review"
            )

    intake_report = desktop_tauri_noop_run_result_intake.build_tauri_noop_run_result_intake_report(
        run_result=run_result
    )
    if intake_report.get("result_validation_status") != "passed":
        for error in intake_report.get("result_validation_errors") or []:
            if isinstance(error, str):
                errors.append(error)
        if not intake_report.get("result_validation_errors"):
            errors.append("run_result failed PCWEB-113 validation")
    return errors, run_result


def _derive_connector_request(run_result: dict[str, Any]) -> dict[str, Any]:
    run_id = _safe_run_id(run_result)
    return {
        "connector_version": "desktop_worker_mic_connector_contract.v1",
        "connector_id": run_id,
        "session_id": run_id,
        "adapter_id": run_id,
        "worker_id": run_id,
        "mic_source_kind": "mic",
        "worker_source_kind": "mic",
        "mic_command_id": "mic_adapter.start",
        "worker_command_id": "worker.prepare",
        "mic_current_state": "prepared",
        "worker_current_state": "not_prepared",
        "user_consent_state": "explicit_user_start_granted",
        "runtime_audio_root": APPROVED_RUNTIME_AUDIO_ROOT,
        "audio_chunk_root": APPROVED_AUDIO_CHUNK_ROOT,
        "worker_runtime_root": f"{APPROVED_WORKER_RUNTIME_ROOT}/{run_id}",
        "worker_event_output_path": f"{APPROVED_WORKER_EVENT_ROOT}/{run_id}.events.json",
    }


def _repo_relative_text(path: Path) -> str:
    relative = _repo_relative_path(path)
    return relative.as_posix() if relative is not None else "<outside_repository>"


def _resolve_evidence(
    *,
    tauri_evidence: dict[str, Any] | None,
    tauri_evidence_path: Path | None,
) -> tuple[dict[str, Any] | None, str, str, list[str]]:
    if tauri_evidence is not None and tauri_evidence_path is not None:
        return None, "not_read", "<multiple_sources>", ["provide only one Tauri evidence source"]
    if tauri_evidence is not None:
        return tauri_evidence, "provided_inline", "<inline>", []
    if tauri_evidence_path is None:
        return None, "not_provided", "<not_provided>", ["pcweb119 tauri evidence is required"]
    path_errors = _evidence_path_errors(tauri_evidence_path)
    if path_errors:
        return None, "blocked_by_path_guard", _repo_relative_text(tauri_evidence_path), path_errors
    payload = _load_json(tauri_evidence_path)
    if not isinstance(payload, dict):
        return None, "read", _repo_relative_text(tauri_evidence_path), ["tauri evidence JSON must be an object"]
    return payload, "read", _repo_relative_text(tauri_evidence_path), []


def build_worker_mic_source_from_tauri_evidence_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    tauri_evidence: dict[str, Any] | None = None,
    tauri_evidence_path: Path | None = None,
) -> dict[str, Any]:
    policy_path_errors = _policy_path_errors(policy_path)
    if policy_path_errors:
        return _blocked_policy_report(policy_path_errors)
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy if isinstance(policy, dict) else {})
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    report = _base_report()
    report["policy_validation_status"] = "passed"

    evidence, read_status, evidence_path_text, read_errors = _resolve_evidence(
        tauri_evidence=tauri_evidence,
        tauri_evidence_path=tauri_evidence_path,
    )
    report.update(
        {
            "tauri_evidence_read_status": read_status,
            "tauri_evidence_path": evidence_path_text,
        }
    )
    if read_errors:
        report.update(
            {
                "tauri_evidence_validation_status": "blocked",
                "tauri_evidence_validation_errors": read_errors,
                "worker_mic_source_approval_packet_status": (
                    "blocked_by_tauri_evidence_path_guard"
                    if read_status == "blocked_by_path_guard"
                    else "blocked_missing_pcweb119_evidence"
                ),
            }
        )
        return report

    evidence_errors, run_result = _validate_tauri_evidence(evidence or {})
    if evidence_errors or run_result is None:
        report.update(
            {
                "tauri_evidence_validation_status": "failed",
                "tauri_evidence_validation_errors": evidence_errors,
                "tauri_evidence_status": (
                    evidence.get("capture_status", "invalid") if isinstance(evidence, dict) else "invalid"
                ),
                "worker_mic_source_approval_packet_status": "blocked_by_tauri_evidence_validation",
            }
        )
        return report

    connector_request = _derive_connector_request(run_result)
    approval_report = desktop_worker_mic_source_approval.build_worker_mic_source_approval_report(
        connector_request=connector_request,
        tauri_run_result=run_result,
    )
    packet_status = str(
        approval_report.get("worker_mic_source_approval_packet_status", "blocked_by_approval_packet")
    )
    report.update(
        {
            "tauri_evidence_validation_status": "passed",
            "tauri_evidence_validation_errors": [],
            "tauri_evidence_status": CAPTURE_STATUS,
            "tauri_run_id": _safe_run_id(run_result),
            "derived_connector_request_status": "derived_same_session_connector_request",
            "connector_session_id": connector_request["session_id"],
            "worker_mic_source_approval_packet_status": packet_status,
            "worker_mic_source_approval_status": approval_report.get(
                "worker_mic_source_approval_status", "not_approved"
            ),
            "real_tauri_noop_run_evidence_status": approval_report.get(
                "real_tauri_noop_run_evidence_status", "not_available"
            ),
            "manual_review_packet": approval_report.get("manual_review_packet"),
            "next_required_decision": approval_report.get(
                "next_required_decision", NEXT_DECISION_MISSING
            ),
        }
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--tauri-evidence-path", type=Path)
    parser.add_argument("--tauri-evidence-json")
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
    report = build_worker_mic_source_from_tauri_evidence_report(
        policy_path=args.policy_path,
        tauri_evidence=_json_object_from_text(args.tauri_evidence_json, "Tauri evidence"),
        tauri_evidence_path=args.tauri_evidence_path,
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

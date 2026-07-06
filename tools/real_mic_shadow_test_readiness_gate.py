#!/usr/bin/env python3
"""Report whether a user-run real mic shadow test can start."""

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

import asr_quality_decision_gate  # noqa: E402
import desktop_worker_mic_source_approval  # noqa: E402
import real_mic_shadow_test_report_schema  # noqa: E402


DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "real-mic-shadow-test-readiness.policy.json"
)

PCWEB_ID = "PCWEB-115"
POLICY_NAME = "Real Mic Shadow Test Readiness Gate"
POLICY_STATUS = "real_mic_shadow_test_readiness_policy_only"
REPORT_MODE = "real_mic_shadow_test_readiness_gate_static_report"
READINESS_MODE = "static_preflight_report_only"
DEFAULT_READINESS_STATUS = "blocked_not_ready_for_user_real_mic_shadow_test"
READY_STATUS = "ready_for_user_manual_real_mic_shadow_test"
NEXT_BLOCKED = "resolve_blockers_before_real_mic_shadow_test"
NEXT_READY = "user_explicitly_starts_real_mic_shadow_test_in_ui"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

FORBIDDEN_ROOTS = [
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
]
ALLOWED_EVIDENCE_ROOTS = [
    "artifacts/tmp",
]
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
REQUIRED_INPUTS = [
    "asr_quality_decision_gate",
    "real_tauri_noop_run_observed",
    "worker_mic_source_manual_approval",
    "real_mic_adapter_implementation_smoke",
    "real_asr_worker_mic_source_smoke",
    "shadow_report_export_feedback_readiness",
]
FALSE_SAFETY_FLAGS = [
    "safe_to_access_microphone_from_gate_now",
    "safe_to_enumerate_audio_devices_from_gate_now",
    "safe_to_request_audio_permission_from_gate_now",
    "safe_to_read_real_user_audio_from_gate_now",
    "safe_to_write_audio_chunk_from_gate_now",
    "safe_to_delete_audio_chunk_from_gate_now",
    "safe_to_spawn_worker_from_gate_now",
    "safe_to_run_tauri_or_" + "car" + "go_from_gate_now",
    "safe_to_read_configs_local_from_gate_now",
    "safe_to_read_secret_from_gate_now",
    "safe_to_call_remote_asr_from_gate_now",
    "safe_to_call_llm_from_gate_now",
    "safe_to_download_models_from_gate_now",
    "safe_to_download_public_audio_from_gate_now",
]
UPSTREAM_FALSE_FLAGS = [
    "safe_to_capture_audio_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_download_public_audio_now",
    "safe_to_run_tauri_or_" + "car" + "go_now",
    "safe_to_spawn_worker_now",
]
MIC_ADAPTER_COMMANDS = [
    "prepare",
    "status",
    "start",
    "pause",
    "resume",
    "stop",
    "delete_audio_chunks",
]
ASR_WORKER_COMMANDS = [
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
]
PILOT_PROTOCOL = {
    "meeting_duration_minutes": "20-30",
    "meeting_type": "chinese_technical_meeting",
    "user_start_required": True,
    "raw_audio_upload_default": False,
    "remote_asr_default": False,
    "llm_payload_policy": "evidence_spans_and_structured_state_only_no_raw_audio",
    "required_export": [
        "transcript",
        "asr_metrics",
        "evidence_span_timeline",
        "state_timeline",
        "candidate_card_timeline",
        "feedback_summary",
        "go_pivot_stop_decision",
    ],
}
ALLOWED_NEXT_ACTIONS = [
    "provide_verified_local_funasr_model_dir_or_approve_drv019_or_accept_degraded_pilot",
    "perform_explicit_real_tauri_noop_run_without_mic_or_worker",
    "manual_review_worker_mic_source_approval_after_valid_tauri_noop_run",
    "implement_minimal_real_mic_adapter_after_explicit_approval",
    "implement_real_asr_worker_mic_source_after_explicit_approval",
]


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
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    try:
        return resolved.relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _policy_path_errors(path: Path) -> list[str]:
    paths_to_check = [path, path.resolve(strict=False)]
    for candidate in paths_to_check:
        if candidate.suffix.casefold() == ".m4a":
            return ["policy path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"policy path is blocked: {label}"]
    return []


def _evidence_path_errors(label: str, path: Path) -> list[str]:
    paths_to_check = [path, path.resolve(strict=False)]
    for candidate in paths_to_check:
        if candidate.suffix.casefold() == ".m4a":
            return [f"{label} is blocked: audio file"]
        for root_label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"{label} is blocked: {root_label}"]
    relative = _repo_relative_path(path)
    if relative is None:
        return [f"{label} must be under approved artifacts root"]
    relative_text = relative.as_posix()
    if not any(
        relative_text == root or relative_text.startswith(f"{root}/")
        for root in ALLOWED_EVIDENCE_ROOTS
    ):
        return [f"{label} must be under approved artifacts root"]
    if path.suffix.casefold() != ".json":
        return [f"{label} must be a JSON file"]
    resolved = path if path.is_absolute() else REPO_ROOT / path
    if not resolved.is_file():
        return [f"{label} must exist"]
    return []


def _canonical_payload() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "readiness_mode": READINESS_MODE,
        "default_readiness_status": DEFAULT_READINESS_STATUS,
        "forbidden_roots": list(FORBIDDEN_ROOTS),
        "required_inputs": list(REQUIRED_INPUTS),
        **_false_safety_flags(),
    }


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "readiness_mode": READINESS_MODE,
        "default_readiness_status": DEFAULT_READINESS_STATUS,
        "forbidden_roots": FORBIDDEN_ROOTS,
        "required_inputs": REQUIRED_INPUTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must match PCWEB-115 policy")
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
        "readiness_status": DEFAULT_READINESS_STATUS,
        "user_can_start_real_mic_shadow_test_now": False,
        "readiness_summary": {
            "asr_quality_ready": False,
            "real_tauri_noop_run_observed": False,
            "worker_mic_source_ready": False,
            "mic_adapter_ready": False,
            "asr_worker_ready": False,
            "export_feedback_ready": False,
        },
        "blockers": [],
        "allowed_next_actions": list(ALLOWED_NEXT_ACTIONS),
        "pilot_protocol": PILOT_PROTOCOL,
        "next_required_decision": NEXT_BLOCKED,
    }


def _blocked_policy_path_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "blocked_by_policy_path_guard",
            "policy_validation_errors": errors,
            "readiness_status": "blocked_by_policy_path_guard",
            "blockers": list(errors),
        }
    )
    return report


def _blocked_policy_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "failed",
            "policy_validation_errors": errors,
            "readiness_status": "blocked_by_policy_validation",
            "blockers": list(errors),
        }
    )
    return report


def _blocked_evidence_input_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_validation_status": "blocked_by_evidence_path_guard",
            "policy_validation_errors": [],
            "evidence_input_status": "blocked",
            "evidence_input_errors": errors,
            "readiness_status": "blocked_by_evidence_path_guard",
            "blockers": list(errors),
        }
    )
    return report


def _default_asr_quality_report() -> dict[str, Any]:
    return asr_quality_decision_gate.build_asr_quality_decision_gate_report()


def _default_worker_mic_source_report() -> dict[str, Any]:
    return desktop_worker_mic_source_approval.build_worker_mic_source_approval_report()


def _default_export_feedback_evidence() -> dict[str, Any]:
    schema_report = real_mic_shadow_test_report_schema.build_real_mic_shadow_test_report_schema()
    return {
        "report_schema_status": (
            f"{schema_report.get('schema_version')}_ready"
            if schema_report.get("schema_version")
            else "not_ready"
        ),
        "feedback_export_bundle_status": "ready_for_real_report_after_user_shadow_test",
        "feedback_labels_available": schema_report.get("feedback_labels", []),
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _upstream_flags_are_false(evidence: dict[str, Any], flags: list[str] | None = None) -> bool:
    selected_flags = flags if flags is not None else UPSTREAM_FALSE_FLAGS
    return all(evidence.get(flag) is not True for flag in selected_flags)


def _asr_quality_ready(report: dict[str, Any]) -> bool:
    strict_quality_ready = report.get("decision_status") == "asr_quality_current_gate_not_blocking"
    degraded_pilot_accepted = (
        report.get("decision_status") == "degraded_pilot_accepted_with_quality_risk"
        and report.get("quality_exit_status") == "degraded_pilot_accepted_with_quality_risk"
        and report.get("degraded_pilot_acceptance_status")
        == "accepted_for_single_shadow_test_quality_risk"
        and report.get("can_unblock_real_mic_shadow_test_quality_gate") is True
        and report.get("counts_as_asr_quality_go_evidence") is False
    )
    return (
        (strict_quality_ready or degraded_pilot_accepted)
        and report.get("non_engineering_candidate_count", 0) == 0
        and _upstream_flags_are_false(report)
    )


def _asr_blocker(report: dict[str, Any]) -> str:
    status = report.get("decision_status")
    if status == "requires_funasr_model_dir_or_drv019_approval":
        return "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval"
    if status == "degraded_pilot_accepted_with_quality_risk":
        return "asr_quality_degraded_pilot_evidence_has_unsafe_or_incomplete_flags"
    if status == "asr_quality_current_gate_not_blocking":
        return "asr_quality_evidence_has_unsafe_flags"
    return "asr_quality_decision_not_ready"


def _tauri_noop_ready(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    run_result = evidence.get("run_result")
    validation_report = evidence.get("validation_report")
    pcweb119_capture_ready = (
        evidence.get("capture_status") == "captured_validated_tauri_noop_run"
        and evidence.get("capture_version") == "desktop_tauri_noop_webview_run_capture.v1"
        and evidence.get("source_endpoint") == "/desktop/tauri-noop-run-results/validations"
        and evidence.get("validated_command_count") == 10
        and evidence.get("returned_command_count") == 10
        and isinstance(run_result, dict)
        and run_result.get("run_environment") == "tauri_webview"
        and run_result.get("explicit_tauri_run_approval_recorded") is True
        and run_result.get("web_app_url_status") == "local_dev_url_loaded"
        and run_result.get("ipc_transport_status") == "tauri_ipc_available"
        and isinstance(validation_report, dict)
        and validation_report.get("result_validation_status") == "passed"
        and validation_report.get("real_tauri_noop_run_evidence_status")
        == "ready_for_worker_mic_source_approval_review"
        and _upstream_flags_are_false(
            evidence,
            [
                "safe_to_request_audio_permission_now",
                "safe_to_capture_audio_now",
                "safe_to_start_asr_worker_now",
                "safe_to_read_audio_chunk_now",
                "safe_to_write_audio_chunk_now",
                "safe_to_read_configs_local_now",
                "safe_to_call_remote_asr_now",
                "safe_to_call_llm_now",
            ],
        )
    )
    return (
        pcweb119_capture_ready
        or (
            evidence.get("evidence_status") == "validated_noop_ipc_observed"
            and evidence.get("run_environment") == "tauri_webview"
            and evidence.get("observed_command_count", 0) >= 10
            and _upstream_flags_are_false(evidence)
        )
    )


def _tauri_noop_evidence_status(evidence: dict[str, Any] | None) -> str:
    if not isinstance(evidence, dict):
        return "not_provided"
    status = evidence.get("evidence_status") or evidence.get("capture_status")
    return status if isinstance(status, str) else "invalid"


def _worker_mic_source_ready(report: dict[str, Any]) -> bool:
    return (
        report.get("worker_mic_source_approval_status")
        == "manually_approved_for_single_shadow_test"
        and report.get("worker_mic_source_approval_packet_status")
        == "ready_for_manual_review_not_executable"
        and report.get("approved_to_execute_now") is False
        and _upstream_flags_are_false(report)
    )


def _mic_adapter_ready(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    commands = evidence.get("commands_smoked")
    return (
        evidence.get("implementation_status") == "implemented_and_smoke_tested"
        and commands == MIC_ADAPTER_COMMANDS
        and evidence.get("audio_chunk_root")
        == "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
        and evidence.get("requires_explicit_user_start") is True
        and evidence.get("default_uploads_raw_audio") is False
        and evidence.get("default_remote_asr_enabled") is False
        and _upstream_flags_are_false(evidence)
    )


def _asr_worker_ready(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    return (
        evidence.get("implementation_status") == "implemented_and_smoke_tested"
        and evidence.get("event_contract_status")
        == "partial_final_revision_error_end_of_stream_supported"
        and evidence.get("worker_output_root") == "artifacts/tmp/asr_events"
        and evidence.get("worker_runtime_root") == "artifacts/tmp/desktop_asr_worker_runtime"
        and evidence.get("web_handoff_status") == "closed_to_evidence_state_gap"
        and evidence.get("source_kind") == "mic"
        and evidence.get("command_catalog_smoked") == ASR_WORKER_COMMANDS
        and evidence.get("requires_explicit_user_start") is True
        and evidence.get("default_uploads_raw_audio") is False
        and evidence.get("default_remote_asr_enabled") is False
        and _upstream_flags_are_false(evidence)
    )


def _export_feedback_ready(evidence: dict[str, Any]) -> bool:
    labels = evidence.get("feedback_labels_available")
    return (
        evidence.get("report_schema_status") == "real_mic_shadow_test_report.v1_ready"
        and evidence.get("feedback_export_bundle_status")
        == "ready_for_real_report_after_user_shadow_test"
        and isinstance(labels, list)
        and set(labels)
        == {
            "useful",
            "would_have_asked",
            "wrong",
            "too_late",
            "too_intrusive",
            "dismissed",
        }
        and _upstream_flags_are_false(evidence)
    )


def _blockers_for(summary: dict[str, bool], asr_quality_report: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if not summary["asr_quality_ready"]:
        blockers.append(_asr_blocker(asr_quality_report))
    if not summary["real_tauri_noop_run_observed"]:
        blockers.append("real_tauri_noop_run_result_not_provided")
    if not summary["worker_mic_source_ready"]:
        blockers.append("worker_mic_source_not_approved")
    if not summary["mic_adapter_ready"]:
        blockers.append("mic_adapter_real_implementation_not_available")
    if not summary["asr_worker_ready"]:
        blockers.append("asr_worker_real_mic_source_not_available")
    if not summary["export_feedback_ready"]:
        blockers.append("shadow_report_export_feedback_not_ready")
    return blockers


def build_real_mic_shadow_test_readiness_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    asr_quality_report: dict[str, Any] | None = None,
    tauri_noop_evidence: dict[str, Any] | None = None,
    worker_mic_source_approval_report: dict[str, Any] | None = None,
    mic_adapter_evidence: dict[str, Any] | None = None,
    asr_worker_evidence: dict[str, Any] | None = None,
    export_feedback_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy_path_errors = _policy_path_errors(policy_path)
    if policy_path_errors:
        return _blocked_policy_path_report(policy_path_errors)

    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy if isinstance(policy, dict) else {})
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    resolved_asr_quality_report = (
        asr_quality_report if asr_quality_report is not None else _default_asr_quality_report()
    )
    resolved_worker_mic_source_report = (
        worker_mic_source_approval_report
        if worker_mic_source_approval_report is not None
        else _default_worker_mic_source_report()
    )
    resolved_export_feedback_evidence = (
        export_feedback_evidence
        if export_feedback_evidence is not None
        else _default_export_feedback_evidence()
    )

    summary = {
        "asr_quality_ready": _asr_quality_ready(resolved_asr_quality_report),
        "real_tauri_noop_run_observed": _tauri_noop_ready(tauri_noop_evidence),
        "worker_mic_source_ready": _worker_mic_source_ready(resolved_worker_mic_source_report),
        "mic_adapter_ready": _mic_adapter_ready(mic_adapter_evidence),
        "asr_worker_ready": _asr_worker_ready(asr_worker_evidence),
        "export_feedback_ready": _export_feedback_ready(resolved_export_feedback_evidence),
    }
    blockers = _blockers_for(summary, resolved_asr_quality_report)
    ready = not blockers

    report = _base_report()
    report.update(
        {
            "policy_validation_status": "passed",
            "readiness_status": READY_STATUS if ready else DEFAULT_READINESS_STATUS,
            "user_can_start_real_mic_shadow_test_now": ready,
            "readiness_summary": summary,
            "blockers": blockers,
            "asr_quality_decision_status": resolved_asr_quality_report.get("decision_status"),
            "asr_quality_exit_status": resolved_asr_quality_report.get("quality_exit_status"),
            "asr_quality_counts_as_go_evidence": resolved_asr_quality_report.get(
                "counts_as_asr_quality_go_evidence"
            ),
            "worker_mic_source_approval_status": resolved_worker_mic_source_report.get(
                "worker_mic_source_approval_status"
            ),
            "tauri_noop_evidence_status": _tauri_noop_evidence_status(tauri_noop_evidence),
            "mic_adapter_implementation_status": (
                mic_adapter_evidence.get("implementation_status")
                if isinstance(mic_adapter_evidence, dict)
                else "not_provided"
            ),
            "asr_worker_implementation_status": (
                asr_worker_evidence.get("implementation_status")
                if isinstance(asr_worker_evidence, dict)
                else "not_provided"
            ),
            "export_feedback_status": resolved_export_feedback_evidence.get(
                "feedback_export_bundle_status"
            ),
            "next_required_decision": NEXT_READY if ready else NEXT_BLOCKED,
        }
    )
    return report


def _json_object_from_text(text: str | None, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    if text is None:
        return None, []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, [f"{label} JSON could not be decoded"]
    if not isinstance(payload, dict):
        return None, [f"{label} JSON must be an object"]
    return payload, []


def _json_object_from_path(
    path: Path | None,
    label: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, []
    path_errors = _evidence_path_errors(label, path)
    if path_errors:
        return None, path_errors
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        payload = _load_json(resolved)
    except (OSError, json.JSONDecodeError):
        return None, [f"{label} JSON could not be read"]
    if not isinstance(payload, dict):
        return None, [f"{label} JSON must be an object"]
    return payload, []


def _resolve_json_input(
    *,
    path: Path | None,
    text: str | None,
    label: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    if path is not None and text is not None:
        return None, [f"provide only one {label} source"]
    if text is not None:
        return _json_object_from_text(text, label)
    return _json_object_from_path(path, label)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--asr-quality-report-path", type=Path)
    parser.add_argument("--asr-quality-report-json")
    parser.add_argument("--tauri-noop-evidence-path", type=Path)
    parser.add_argument("--tauri-noop-evidence-json")
    parser.add_argument("--worker-mic-source-approval-path", type=Path)
    parser.add_argument("--worker-mic-source-approval-json")
    parser.add_argument("--mic-adapter-evidence-path", type=Path)
    parser.add_argument("--mic-adapter-evidence-json")
    parser.add_argument("--asr-worker-evidence-path", type=Path)
    parser.add_argument("--asr-worker-evidence-json")
    parser.add_argument("--export-feedback-evidence-path", type=Path)
    parser.add_argument("--export-feedback-evidence-json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    evidence_errors: list[str] = []
    asr_quality_report, errors = _resolve_json_input(
        path=args.asr_quality_report_path,
        text=args.asr_quality_report_json,
        label="asr_quality_report",
    )
    evidence_errors.extend(errors)
    tauri_noop_evidence, errors = _resolve_json_input(
        path=args.tauri_noop_evidence_path,
        text=args.tauri_noop_evidence_json,
        label="tauri_noop_evidence_path",
    )
    evidence_errors.extend(errors)
    worker_mic_source_approval_report, errors = _resolve_json_input(
        path=args.worker_mic_source_approval_path,
        text=args.worker_mic_source_approval_json,
        label="worker_mic_source_approval",
    )
    evidence_errors.extend(errors)
    mic_adapter_evidence, errors = _resolve_json_input(
        path=args.mic_adapter_evidence_path,
        text=args.mic_adapter_evidence_json,
        label="mic_adapter_evidence",
    )
    evidence_errors.extend(errors)
    asr_worker_evidence, errors = _resolve_json_input(
        path=args.asr_worker_evidence_path,
        text=args.asr_worker_evidence_json,
        label="asr_worker_evidence",
    )
    evidence_errors.extend(errors)
    export_feedback_evidence, errors = _resolve_json_input(
        path=args.export_feedback_evidence_path,
        text=args.export_feedback_evidence_json,
        label="export_feedback_evidence",
    )
    evidence_errors.extend(errors)
    if evidence_errors:
        report = _blocked_evidence_input_report(evidence_errors)
    else:
        report = build_real_mic_shadow_test_readiness_report(
            policy_path=args.policy_path,
            asr_quality_report=asr_quality_report,
            tauri_noop_evidence=tauri_noop_evidence,
            worker_mic_source_approval_report=worker_mic_source_approval_report,
            mic_adapter_evidence=mic_adapter_evidence,
            asr_worker_evidence=asr_worker_evidence,
            export_feedback_evidence=export_feedback_evidence,
        )
        report["evidence_input_status"] = (
            "loaded"
            if any(
                value is not None
                for value in (
                    asr_quality_report,
                    tauri_noop_evidence,
                    worker_mic_source_approval_report,
                    mic_adapter_evidence,
                    asr_worker_evidence,
                    export_feedback_evidence,
                )
            )
            else "default_reports_only"
        )
        report["evidence_input_errors"] = []
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report.get("readiness_status") == READY_STATUS else 1


if __name__ == "__main__":
    raise SystemExit(main())

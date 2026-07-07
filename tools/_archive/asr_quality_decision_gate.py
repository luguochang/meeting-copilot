#!/usr/bin/env python3
"""Decide the next ASR-quality action without running ASR or downloading models."""

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

import copilot_product_value_batch_gate  # noqa: E402
import funasr_model_download_approval_packet  # noqa: E402
import funasr_synthetic_smoke_result_evidence  # noqa: E402
import funasr_synthetic_smoke_readiness  # noqa: E402
import public_audio_planned_sample_manifest_decision  # noqa: E402
import simulated_shadow_pipeline_smoke  # noqa: E402


DECISION_ID = "DRV-032"
DECISION_MODE = "asr_quality_decision_gate"
DECISION_VERSION = "asr_quality_decision_gate.v1"
DEGRADED_PILOT_ACCEPTANCE_VERSION = "asr_quality_degraded_pilot_acceptance.v1"
DEGRADED_PILOT_ACCEPTANCE_ID = "degraded_asr_single_shadow_test"
DEGRADED_PILOT_ACCEPTANCE_SCOPE = "single_user_real_mic_shadow_test_only"
DEGRADED_PILOT_ACCEPTANCE_TOKEN = "ACCEPT_DEGRADED_ASR_FOR_SINGLE_SHADOW_TEST"
DEGRADED_PILOT_ACCEPTANCE_STATUS = "accepted_for_single_shadow_test_quality_risk"
DEGRADED_PILOT_DECISION_STATUS = "degraded_pilot_accepted_with_quality_risk"

REQUIRED_DEGRADED_PILOT_RISKS = [
    "chinese_technical_entity_recall_below_first_pilot_target",
    "suggestion_cards_may_be_late_wrong_or_missing",
    "result_cannot_be_used_as_asr_quality_go_evidence",
]

DEFAULT_FUNASR_AUDIO_PATH = "artifacts/tmp/synthetic_audio/api-review-001.wav"
DEFAULT_FUNASR_EVENTS_OUTPUT_PATH = "artifacts/tmp/asr_events/api-review-001.funasr.events.json"
DEFAULT_FUNASR_PROVIDER_OUTPUT_PATH = "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json"
DEFAULT_FUNASR_TRANSCRIPT_REPORT_PATH = "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json"
DEFAULT_FUNASR_SMOKE_REPORT_PATH = "artifacts/tmp/asr_reports/api-review-001.funasr.smoke-report.json"

ALLOWED_ACCEPTANCE_ROOTS = [
    "artifacts/tmp",
]
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
SAFETY_FLAGS = (
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
FUNASR_SMOKE_ASSEMBLY_FALSE_FLAGS = (
    "safe_to_run_asr_now",
    "safe_to_download_models_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_public_audio_now",
    "safe_to_read_audio_file_now",
    "safe_to_write_artifacts_now",
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


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


def _acceptance_path_errors(label: str, path: Path) -> list[str]:
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
        for root in ALLOWED_ACCEPTANCE_ROOTS
    ):
        return [f"{label} must be under approved artifacts root"]
    if path.suffix.casefold() != ".json":
        return [f"{label} must be a JSON file"]
    resolved = path if path.is_absolute() else REPO_ROOT / path
    if not resolved.is_file():
        return [f"{label} must exist"]
    return []


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
    path_errors = _acceptance_path_errors(label, path)
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


def _blocked_acceptance_input_report(errors: list[str]) -> dict[str, Any]:
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "decision_status": "blocked_by_degraded_pilot_acceptance_input_guard",
        "quality_exit_status": "not_exited",
        "acceptance_input_status": "blocked",
        "acceptance_input_errors": list(errors),
        "degraded_pilot_acceptance_status": "blocked",
        "degraded_pilot_acceptance_errors": list(errors),
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": list(errors),
        "next_allowed_actions": ["fix_degraded_pilot_acceptance_input"],
        **_false_safety_flags(),
    }


def _blocked_funasr_readiness_input_report(errors: list[str]) -> dict[str, Any]:
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "decision_status": "blocked_by_funasr_readiness_input_guard",
        "quality_exit_status": "not_exited",
        "funasr_readiness_input_status": "blocked",
        "funasr_readiness_input_errors": list(errors),
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": list(errors),
        "next_allowed_actions": ["fix_funasr_readiness_input"],
        **_false_safety_flags(),
    }


def _blocked_funasr_smoke_result_input_report(errors: list[str]) -> dict[str, Any]:
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "decision_status": "blocked_by_funasr_smoke_result_input_guard",
        "quality_exit_status": "not_exited",
        "funasr_smoke_result_input_status": "blocked",
        "funasr_smoke_result_input_errors": list(errors),
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": list(errors),
        "next_allowed_actions": ["fix_funasr_smoke_result_input"],
        **_false_safety_flags(),
    }


def _blocked_funasr_smoke_assembly_input_report(errors: list[str]) -> dict[str, Any]:
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
        "quality_exit_status": "not_exited",
        "funasr_smoke_assembly_input_status": "blocked",
        "funasr_smoke_assembly_input_errors": list(errors),
        "funasr_smoke_assembly_status": "blocked",
        "funasr_smoke_result_source": "blocked_drv046_batch_assembly",
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": list(errors),
        "next_allowed_actions": ["fix_funasr_smoke_assembly_input"],
        **_false_safety_flags(),
    }


def _funasr_smoke_result_gate_report_errors(report: dict[str, Any] | None) -> list[str]:
    if report is None:
        return []
    if (
        report.get("decision_id") != "DRV-044"
        or report.get("report_mode") != "funasr_synthetic_smoke_result_evidence_gate"
        or report.get("schema_version") != "funasr_synthetic_smoke_result.v1"
    ):
        return ["funasr_smoke_result must be a DRV-044 gate report"]
    allowed_statuses = {
        "not_evaluated",
        "blocked",
        "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation",
        "funasr_synthetic_smoke_quality_batch_confirmed",
    }
    if report.get("quality_evidence_status") not in allowed_statuses:
        return ["funasr_smoke_result has unknown quality_evidence_status"]
    if report.get("counts_as_real_mic_go_evidence") is not False:
        return ["funasr_smoke_result must not count as real mic go evidence"]
    return []


def _funasr_smoke_assembly_report_errors(report: dict[str, Any] | None) -> list[str]:
    if report is None:
        return []
    if not isinstance(report, dict):
        return ["funasr_smoke_assembly must be a JSON object"]

    errors: list[str] = []
    if report.get("decision_id") != "DRV-046":
        errors.append("funasr_smoke_assembly decision_id must be DRV-046")
    if report.get("assembly_mode") != "funasr_synthetic_smoke_batch_evidence_assembler":
        errors.append(
            "funasr_smoke_assembly assembly_mode must be "
            "funasr_synthetic_smoke_batch_evidence_assembler"
        )
    if report.get("assembly_version") != "funasr_synthetic_smoke_batch_evidence_assembler.v1":
        errors.append(
            "funasr_smoke_assembly assembly_version must be "
            "funasr_synthetic_smoke_batch_evidence_assembler.v1"
        )
    if report.get("assembly_status") != "drv044_batch_evidence_validated":
        errors.append("funasr_smoke_assembly must be drv044_batch_evidence_validated")
    if report.get("artifact_read_status") != "read":
        errors.append("funasr_smoke_assembly artifact_read_status must be read")
    if report.get("artifact_count") != 5:
        errors.append("funasr_smoke_assembly artifact_count must be 5")
    if report.get("counts_as_asr_quality_go_evidence") is not True:
        errors.append("funasr_smoke_assembly must count as ASR quality go evidence")
    if report.get("counts_as_real_mic_go_evidence") is not False:
        errors.append("funasr_smoke_assembly must not count as real mic go evidence")
    if report.get("validation_errors") not in ([], None):
        errors.append("funasr_smoke_assembly validation_errors must be empty")
    for flag in FUNASR_SMOKE_ASSEMBLY_FALSE_FLAGS:
        if report.get(flag) is not False:
            errors.append(f"funasr_smoke_assembly {flag} must be false")

    nested_report = report.get("drv044_gate_report")
    if not isinstance(nested_report, dict):
        errors.append("funasr_smoke_assembly drv044_gate_report must be an object")
        return errors

    nested_gate_errors = _funasr_smoke_result_gate_report_errors(nested_report)
    errors.extend(f"funasr_smoke_assembly nested {error}" for error in nested_gate_errors)
    if nested_gate_errors:
        return errors

    if nested_report.get("quality_evidence_status") != (
        "funasr_synthetic_smoke_quality_batch_confirmed"
    ):
        errors.append("funasr_smoke_assembly nested DRV-044 must be batch confirmed")
    if nested_report.get("counts_as_asr_quality_go_evidence") is not True:
        errors.append("funasr_smoke_assembly nested DRV-044 must count as ASR quality go evidence")
    errors.extend(_funasr_smoke_validation_errors(nested_report))
    return errors


def _funasr_smoke_result_from_assembly(report: dict[str, Any]) -> dict[str, Any]:
    return report["drv044_gate_report"]


def _sync_imported_tool_roots() -> None:
    copilot_product_value_batch_gate.REPO_ROOT = REPO_ROOT
    funasr_synthetic_smoke_result_evidence.REPO_ROOT = REPO_ROOT
    funasr_synthetic_smoke_readiness.REPO_ROOT = REPO_ROOT
    public_audio_planned_sample_manifest_decision.REPO_ROOT = REPO_ROOT
    asr_live_pipeline_replay = simulated_shadow_pipeline_smoke.asr_live_pipeline_replay
    replay_adapter = simulated_shadow_pipeline_smoke.replay_shadow_report_draft_adapter
    ingestion = simulated_shadow_pipeline_smoke.shadow_report_ingestion_export_feedback
    asr_live_pipeline_replay.REPO_ROOT = REPO_ROOT
    replay_adapter.REPO_ROOT = REPO_ROOT
    ingestion.REPO_ROOT = REPO_ROOT


def _default_batch_report() -> dict[str, Any]:
    _sync_imported_tool_roots()
    return copilot_product_value_batch_gate.build_copilot_product_value_batch_report_from_relative_roots(
        scripts_root="data/asr_eval/synthetic_meetings/scripts",
        mock_events_pattern="artifacts/tmp/asr_events/{script_id}.mock.events.json",
        real_events_pattern="artifacts/tmp/asr_events/{script_id}.sherpa.events.json",
        real_smoke_report_pattern="artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
        real_provider="sherpa_onnx_streaming",
    )


def _default_funasr_readiness_report() -> dict[str, Any]:
    _sync_imported_tool_roots()
    return funasr_synthetic_smoke_readiness.build_funasr_synthetic_smoke_readiness_report(
        audio_path=DEFAULT_FUNASR_AUDIO_PATH,
        events_output_path=DEFAULT_FUNASR_EVENTS_OUTPUT_PATH,
        provider_output_path=DEFAULT_FUNASR_PROVIDER_OUTPUT_PATH,
        transcript_report_path=DEFAULT_FUNASR_TRANSCRIPT_REPORT_PATH,
        smoke_report_path=DEFAULT_FUNASR_SMOKE_REPORT_PATH,
    )


def _default_funasr_approval_packet() -> dict[str, Any]:
    return funasr_model_download_approval_packet.build_funasr_model_download_approval_packet()


def _default_funasr_smoke_result_report() -> dict[str, Any]:
    _sync_imported_tool_roots()
    return funasr_synthetic_smoke_result_evidence.build_funasr_synthetic_smoke_result_evidence_gate()


def _default_public_audio_decision() -> dict[str, Any]:
    _sync_imported_tool_roots()
    return public_audio_planned_sample_manifest_decision.build_public_audio_planned_sample_manifest_decision()


def _default_simulated_shadow_batch_report() -> dict[str, Any]:
    _sync_imported_tool_roots()
    return simulated_shadow_pipeline_smoke.build_simulated_shadow_pipeline_batch_smoke(
        scenario_specs=simulated_shadow_pipeline_smoke.DEFAULT_MOCK_SCENARIO_SPECS,
        provider="mock_streaming",
    )


def _public_audio_evidence_status(public_audio_decision: dict[str, Any]) -> str:
    if public_audio_decision.get("decision_status") == "schema_validated_no_download":
        return "manual_evidence_pending_no_download"
    return "blocked_or_not_planned_no_download"


def _quality_exit_options(
    *,
    funasr_readiness_report: dict[str, Any],
    funasr_approval_packet: dict[str, Any],
) -> list[dict[str, Any]]:
    cached_models_status = funasr_readiness_report.get("required_cached_models_status")
    funasr_ready_for_execution_approval = (
        funasr_readiness_report.get("readiness_status")
        == "cache_preflight_passed_offline_execution_not_proven"
        and cached_models_status == "present"
    )
    return [
        {
            "path_id": "verified_local_funasr_model_dir",
            "default_enabled": True,
            "extra_provider_fee": False,
            "requires_explicit_user_approval": False,
            "current_status": "ready_for_single_smoke_approval"
            if funasr_ready_for_execution_approval
            else "blocked_missing_verified_local_model_dir",
            "unblocks_when": "funasr_synthetic_smoke_passes_and_batch_gate_no_longer_blocks_asr_quality",
            "counts_as_asr_quality_go_evidence": True,
        },
        {
            "path_id": "drv019_manual_model_download",
            "default_enabled": False,
            "extra_provider_fee": False,
            "requires_explicit_user_approval": True,
            "current_status": funasr_approval_packet.get("approval_packet_status"),
            "unblocks_when": "manual_model_download_then_post_download_verification_then_single_smoke",
            "counts_as_asr_quality_go_evidence": True,
        },
        {
            "path_id": "optional_remote_asr_comparison",
            "default_enabled": False,
            "extra_provider_fee": True,
            "requires_explicit_user_approval": True,
            "current_status": "not_selected_disabled_by_default",
            "unblocks_when": "explicit_remote_asr_decision_and_cost_privacy_review_pass",
            "counts_as_asr_quality_go_evidence": "comparison_only_until_tri_lane_gate_passes",
        },
        {
            "path_id": "explicit_degraded_pilot_acceptance",
            "default_enabled": False,
            "extra_provider_fee": False,
            "requires_explicit_user_approval": True,
            "current_status": "available_but_not_selected_by_default",
            "unblocks_when": "explicit_acceptance_record_passes_and_pcweb115_other_preconditions_pass",
            "counts_as_asr_quality_go_evidence": False,
        },
    ]


def _degraded_pilot_acceptance_status(
    acceptance: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    if acceptance is None:
        return "not_requested", []
    if not isinstance(acceptance, dict):
        return "rejected", ["degraded pilot acceptance must be a JSON object"]

    errors: list[str] = []
    if acceptance.get("acceptance_record_version") != DEGRADED_PILOT_ACCEPTANCE_VERSION:
        errors.append("acceptance_record_version must match degraded pilot schema")
    if acceptance.get("acceptance_id") != DEGRADED_PILOT_ACCEPTANCE_ID:
        errors.append("acceptance_id must match degraded pilot acceptance id")
    if acceptance.get("acceptance_scope") != DEGRADED_PILOT_ACCEPTANCE_SCOPE:
        errors.append("acceptance_scope must be single user shadow test only")
    if acceptance.get("acceptance_token") != DEGRADED_PILOT_ACCEPTANCE_TOKEN:
        errors.append("acceptance_token must match explicit degraded pilot token")
    if acceptance.get("accepted_risks") != REQUIRED_DEGRADED_PILOT_RISKS:
        errors.append("accepted_risks must explicitly match degraded pilot risk list")
    if not isinstance(acceptance.get("operator_note"), str) or not acceptance.get("operator_note"):
        errors.append("operator_note is required")

    if errors:
        return "rejected", errors
    return DEGRADED_PILOT_ACCEPTANCE_STATUS, []


def _funasr_smoke_validation_errors(report: dict[str, Any]) -> list[str]:
    errors = list(report.get("validation_errors", []))
    quality_status = report.get("quality_evidence_status")
    counts_as_go = report.get("counts_as_asr_quality_go_evidence") is True
    if quality_status == "funasr_synthetic_smoke_quality_batch_confirmed" or counts_as_go:
        if report.get("batch_artifact_provenance_status") != "validated":
            errors.append(
                "batch confirmed FunASR smoke result requires validated artifact provenance"
            )
        else:
            summary = report.get("scenario_summary", {})
            required_artifact_count = int(summary.get("engineering_scenario_count", 0)) + int(
                summary.get("negative_control_count", 0)
            )
            artifact_count = report.get("batch_artifact_count")
            if not isinstance(artifact_count, int) or artifact_count < required_artifact_count:
                errors.append(
                    "batch confirmed FunASR smoke result artifact count must cover every scenario"
                )
    return errors


def _common_report_fields(
    *,
    batch_report: dict[str, Any],
    simulated_shadow_batch_report: dict[str, Any],
    funasr_readiness_report: dict[str, Any],
    funasr_smoke_result_report: dict[str, Any],
    funasr_smoke_assembly_report: dict[str, Any] | None,
    funasr_smoke_result_source: str,
    funasr_approval_packet: dict[str, Any],
    public_audio_decision: dict[str, Any],
    degraded_pilot_acceptance_status: str,
    degraded_pilot_acceptance_errors: list[str],
) -> dict[str, Any]:
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "product_value_batch_status": batch_report.get("status"),
        "product_value_batch_overall_decision": batch_report.get("overall_decision"),
        "product_value_batch_next_action": batch_report.get("next_action"),
        "scenario_count": batch_report.get("scenario_count", 0),
        "engineering_scenario_count": batch_report.get("engineering_scenario_count", 0),
        "negative_control_count": batch_report.get("negative_control_count", 0),
        "perfect_lane_ready_count": batch_report.get("perfect_lane_ready_count", 0),
        "mock_lane_ready_count": batch_report.get("mock_lane_ready_count", 0),
        "real_asr_blocked_count": batch_report.get("real_asr_blocked_count", 0),
        "non_engineering_candidate_count": batch_report.get("non_engineering_candidate_count", 0),
        "decision_counts": batch_report.get("decision_counts", {}),
        "simulated_shadow_batch_status": simulated_shadow_batch_report.get("batch_status"),
        "simulated_shadow_scenario_count": simulated_shadow_batch_report.get(
            "scenario_count",
            0,
        ),
        "simulated_shadow_engineering_preview_created_count": (
            simulated_shadow_batch_report.get("engineering_preview_created_count", 0)
        ),
        "simulated_shadow_negative_control_blocked_count": simulated_shadow_batch_report.get(
            "negative_control_blocked_count",
            0,
        ),
        "simulated_shadow_negative_control_fake_candidate_count": (
            simulated_shadow_batch_report.get("negative_control_fake_candidate_count", 0)
        ),
        "simulated_shadow_batch_go_evidence_status": simulated_shadow_batch_report.get(
            "go_evidence_status"
        ),
        "funasr_readiness_status": funasr_readiness_report.get("readiness_status"),
        "funasr_provider": funasr_readiness_report.get("provider"),
        "funasr_model_alias": funasr_readiness_report.get("model_alias"),
        "funasr_required_cached_models_status": funasr_readiness_report.get(
            "required_cached_models_status"
        ),
        "funasr_offline_guard_status": funasr_readiness_report.get("offline_guard_status"),
        "funasr_model_download_status": funasr_readiness_report.get("model_download_status"),
        "funasr_readiness_validation_errors": funasr_readiness_report.get("validation_errors", []),
        "funasr_smoke_result_source": funasr_smoke_result_source,
        "funasr_smoke_assembly_status": (
            funasr_smoke_assembly_report.get("assembly_status")
            if funasr_smoke_assembly_report is not None
            else "not_provided"
        ),
        "funasr_smoke_assembly_artifact_count": (
            funasr_smoke_assembly_report.get("artifact_count")
            if funasr_smoke_assembly_report is not None
            else 0
        ),
        "funasr_smoke_assembly_counts_as_quality_go_evidence": (
            funasr_smoke_assembly_report.get("counts_as_asr_quality_go_evidence")
            if funasr_smoke_assembly_report is not None
            else False
        ),
        "funasr_smoke_assembly_counts_as_real_mic_go_evidence": (
            funasr_smoke_assembly_report.get("counts_as_real_mic_go_evidence")
            if funasr_smoke_assembly_report is not None
            else False
        ),
        "funasr_smoke_assembly_validation_errors": (
            funasr_smoke_assembly_report.get("validation_errors", [])
            if funasr_smoke_assembly_report is not None
            else []
        ),
        "funasr_smoke_evidence_status": funasr_smoke_result_report.get("evidence_status"),
        "funasr_smoke_quality_evidence_status": funasr_smoke_result_report.get(
            "quality_evidence_status"
        ),
        "funasr_smoke_counts_as_quality_go_evidence": funasr_smoke_result_report.get(
            "counts_as_asr_quality_go_evidence"
        ),
        "funasr_smoke_counts_as_real_mic_go_evidence": funasr_smoke_result_report.get(
            "counts_as_real_mic_go_evidence"
        ),
        "funasr_smoke_scenario_summary": funasr_smoke_result_report.get("scenario_summary", {}),
        "funasr_smoke_validation_errors": _funasr_smoke_validation_errors(
            funasr_smoke_result_report
        ),
        "funasr_approval_packet_status": funasr_approval_packet.get("approval_packet_status"),
        "funasr_approval_packet_mode": funasr_approval_packet.get("approval_packet_mode"),
        "funasr_model_download_execution_status": funasr_approval_packet.get(
            "model_download_execution_status"
        ),
        "funasr_approval_blockers": funasr_approval_packet.get("approval_blockers", []),
        "public_audio_decision_status": public_audio_decision.get("decision_status"),
        "public_audio_stage_status": public_audio_decision.get("public_audio_stage_status"),
        "public_audio_evidence_status": _public_audio_evidence_status(public_audio_decision),
        "quality_exit_status": "not_exited",
        "recommended_quality_exit_path_id": (
            "local_funasr_model_dir_if_available_else_explicit_degraded_pilot_decision"
        ),
        "default_cost_policy": "no_new_paid_provider_by_default",
        "quality_exit_options": _quality_exit_options(
            funasr_readiness_report=funasr_readiness_report,
            funasr_approval_packet=funasr_approval_packet,
        ),
        "mainline_stop_conditions": [
            "do_not_expand_provider_bakeoff_without_funasr_model_or_explicit_remote_asr_decision",
            "do_not_add_more_report_only_readiness_wrappers",
            "do_not_start_real_mic_shadow_test_without_quality_exit_or_explicit_degraded_acceptance",
        ],
        "degraded_pilot_acceptance_status": degraded_pilot_acceptance_status,
        "degraded_pilot_acceptance_errors": degraded_pilot_acceptance_errors,
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "remote_asr_policy": "disabled_by_default_requires_explicit_future_decision",
        "llm_policy": "disabled_for_asr_quality_gate",
        **_false_safety_flags(),
    }


def _decision_for(
    *,
    batch_report: dict[str, Any],
    simulated_shadow_batch_report: dict[str, Any],
    funasr_readiness_report: dict[str, Any],
    funasr_smoke_result_report: dict[str, Any],
    degraded_pilot_acceptance_status: str,
) -> tuple[str, list[str], list[str]]:
    batch_status = batch_report.get("status")
    batch_decision = batch_report.get("overall_decision")
    simulated_shadow_batch_status = simulated_shadow_batch_report.get("batch_status")
    funasr_status = funasr_readiness_report.get("readiness_status")
    cached_models_status = funasr_readiness_report.get("required_cached_models_status")
    funasr_smoke_status = funasr_smoke_result_report.get("quality_evidence_status")
    funasr_smoke_counts_as_go = (
        funasr_smoke_result_report.get("counts_as_asr_quality_go_evidence") is True
    )
    funasr_smoke_validation_errors = _funasr_smoke_validation_errors(
        funasr_smoke_result_report
    )

    if simulated_shadow_batch_status != "simulated_shadow_pipeline_batch_passed":
        return (
            "fix_simulated_shadow_pipeline_first",
            [
                "simulated_shadow_pipeline_batch_not_passed",
                "do_not_blame_asr_provider_until_mock_shadow_pipeline_gate_recovers",
            ],
            ["repair_simulated_shadow_pipeline_batch_inputs_or_gap_logic"],
        )

    if batch_status != "completed":
        return (
            "fix_product_value_batch_inputs_first",
            ["product_value_batch_gate_not_completed"],
            ["repair_batch_gate_inputs"],
        )

    if batch_decision == "blocked_by_product_logic":
        return (
            "fix_product_logic_first",
            [
                "perfect_or_mock_lane_not_ready",
                "do_not_blame_asr_provider_until_product_logic_gate_recovers",
            ],
            ["repair_evidence_gap_candidate_logic"],
        )

    if batch_decision == "blocked_by_stream_contract":
        return (
            "fix_stream_contract_first",
            ["stream_contract_not_ready"],
            ["repair_asr_event_contract_before_provider_work"],
        )

    if batch_decision == "blocked_by_asr_quality":
        if funasr_smoke_status == "blocked":
            return (
                "fix_funasr_smoke_result_evidence_first",
                ["funasr_smoke_result_evidence_blocked"],
                ["fix_funasr_smoke_result_evidence"],
            )
        if (
            funasr_smoke_status == "funasr_synthetic_smoke_quality_batch_confirmed"
            and funasr_smoke_validation_errors
        ):
            return (
                "fix_funasr_smoke_result_evidence_first",
                ["funasr_smoke_result_evidence_blocked"],
                ["fix_funasr_smoke_result_evidence"],
            )
        if (
            funasr_smoke_status == "funasr_synthetic_smoke_quality_batch_confirmed"
            and funasr_smoke_counts_as_go
        ):
            return (
                "asr_quality_current_gate_not_blocking",
                [],
                [
                    "advance_desktop_runtime_or_controlled_llm_cards",
                    "keep_real_mic_shadow_test_behind_user_start_boundary",
                ],
            )
        if funasr_smoke_status == (
            "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
        ):
            return (
                "funasr_smoke_candidate_requires_batch_confirmation",
                [
                    "single_funasr_synthetic_smoke_is_not_batch_confirmed",
                    "do_not_claim_asr_quality_go_until_four_engineering_and_negative_control_pass",
                ],
                [
                    "run_funasr_batch_confirmation_with_four_engineering_scenarios_and_negative_control",
                    "keep_real_mic_shadow_test_blocked_until_batch_confirmation_or_degraded_acceptance",
                ],
            )
        if degraded_pilot_acceptance_status == DEGRADED_PILOT_ACCEPTANCE_STATUS:
            return (
                DEGRADED_PILOT_DECISION_STATUS,
                [
                    "real_sherpa_asr_quality_still_below_first_pilot_target",
                    "degraded_pilot_is_only_for_timing_feedback_loop_validation",
                ],
                [
                    "use_pcweb115_to_verify_all_non_asr_shadow_test_preconditions",
                    "run_one_user_manual_shadow_test_only_after_explicit_ui_start",
                    "collect_feedback_and_treat_result_as_degraded_pilot_evidence_not_asr_quality_go",
                ],
            )
        if (
            funasr_status == "cache_preflight_passed_offline_execution_not_proven"
            and cached_models_status == "present"
        ):
            return (
                "funasr_cache_preflight_ready_requires_execution_approval",
                [
                    "real_sherpa_asr_blocked_by_chinese_technical_entity_recall",
                    "funasr_cache_preflight_ready_but_not_executed",
                ],
                [
                    "approve_single_funasr_synthetic_smoke_run",
                    "then_rerun_copilot_product_value_batch_gate",
                    "keep_remote_asr_and_llm_disabled_by_default",
                ],
            )
        return (
            "requires_funasr_model_dir_or_drv019_approval",
            [
                "real_sherpa_asr_blocked_by_chinese_technical_entity_recall",
                "funasr_local_model_dir_or_cache_not_ready",
                "drv019_model_download_requires_explicit_user_approval",
            ],
            [
                "provide_verified_local_funasr_model_dir",
                "approve_drv019_manual_model_download_packet",
                "accept_degraded_pilot_with_explicit_quality_risk",
                "continue_desktop_noop_or_mic_adapter_contract_without_claiming_asr_quality_solved",
            ],
        )

    return (
        "asr_quality_current_gate_not_blocking",
        [],
        [
            "advance_desktop_runtime_or_controlled_llm_cards",
            "keep_real_mic_shadow_test_behind_user_start_boundary",
        ],
    )


def build_asr_quality_decision_gate_report(
    *,
    batch_report: dict[str, Any] | None = None,
    simulated_shadow_batch_report: dict[str, Any] | None = None,
    funasr_readiness_report: dict[str, Any] | None = None,
    funasr_smoke_result_report: dict[str, Any] | None = None,
    funasr_smoke_assembly_report: dict[str, Any] | None = None,
    funasr_approval_packet: dict[str, Any] | None = None,
    public_audio_decision: dict[str, Any] | None = None,
    degraded_pilot_acceptance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_batch_report = batch_report if batch_report is not None else _default_batch_report()
    resolved_simulated_shadow_batch_report = (
        simulated_shadow_batch_report
        if simulated_shadow_batch_report is not None
        else _default_simulated_shadow_batch_report()
    )
    resolved_funasr_readiness_report = (
        funasr_readiness_report
        if funasr_readiness_report is not None
        else _default_funasr_readiness_report()
    )
    resolved_funasr_approval_packet = (
        funasr_approval_packet
        if funasr_approval_packet is not None
        else _default_funasr_approval_packet()
    )
    if funasr_smoke_result_report is not None and funasr_smoke_assembly_report is not None:
        return _blocked_funasr_smoke_assembly_input_report(
            ["provide only one FunASR smoke evidence source"]
        )
    funasr_smoke_assembly_errors = _funasr_smoke_assembly_report_errors(
        funasr_smoke_assembly_report
    )
    if funasr_smoke_assembly_errors:
        return _blocked_funasr_smoke_assembly_input_report(funasr_smoke_assembly_errors)
    if funasr_smoke_assembly_report is not None:
        resolved_funasr_smoke_result_report = _funasr_smoke_result_from_assembly(
            funasr_smoke_assembly_report
        )
        funasr_smoke_result_source = "drv046_batch_assembly"
    elif funasr_smoke_result_report is not None:
        resolved_funasr_smoke_result_report = funasr_smoke_result_report
        funasr_smoke_result_source = "direct_drv044_gate_report"
    else:
        resolved_funasr_smoke_result_report = _default_funasr_smoke_result_report()
        funasr_smoke_result_source = "default_drv044_gate_report"
    resolved_public_audio_decision = (
        public_audio_decision
        if public_audio_decision is not None
        else _default_public_audio_decision()
    )
    degraded_status, degraded_errors = _degraded_pilot_acceptance_status(
        degraded_pilot_acceptance
    )

    decision_status, blocked_reasons, next_allowed_actions = _decision_for(
        batch_report=resolved_batch_report,
        simulated_shadow_batch_report=resolved_simulated_shadow_batch_report,
        funasr_readiness_report=resolved_funasr_readiness_report,
        funasr_smoke_result_report=resolved_funasr_smoke_result_report,
        degraded_pilot_acceptance_status=degraded_status,
    )

    quality_exit_status = (
        DEGRADED_PILOT_DECISION_STATUS
        if decision_status == DEGRADED_PILOT_DECISION_STATUS
        else "strict_quality_gate_not_blocking"
        if decision_status == "asr_quality_current_gate_not_blocking"
        else "not_exited"
    )
    can_unblock_real_mic_shadow_test_quality_gate = decision_status in {
        "asr_quality_current_gate_not_blocking",
        DEGRADED_PILOT_DECISION_STATUS,
    }
    counts_as_go_evidence = decision_status == "asr_quality_current_gate_not_blocking"

    return _common_report_fields(
        batch_report=resolved_batch_report,
        simulated_shadow_batch_report=resolved_simulated_shadow_batch_report,
        funasr_readiness_report=resolved_funasr_readiness_report,
        funasr_smoke_result_report=resolved_funasr_smoke_result_report,
        funasr_smoke_assembly_report=funasr_smoke_assembly_report,
        funasr_smoke_result_source=funasr_smoke_result_source,
        funasr_approval_packet=resolved_funasr_approval_packet,
        public_audio_decision=resolved_public_audio_decision,
        degraded_pilot_acceptance_status=degraded_status,
        degraded_pilot_acceptance_errors=degraded_errors,
    ) | {
        "decision_status": decision_status,
        "quality_exit_status": quality_exit_status,
        "can_unblock_real_mic_shadow_test_quality_gate": (
            can_unblock_real_mic_shadow_test_quality_gate
        ),
        "counts_as_asr_quality_go_evidence": counts_as_go_evidence,
        "blocked_reasons": blocked_reasons,
        "next_allowed_actions": next_allowed_actions,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--degraded-pilot-acceptance-path", type=Path)
    parser.add_argument("--degraded-pilot-acceptance-json")
    parser.add_argument("--funasr-readiness-path", type=Path)
    parser.add_argument("--funasr-smoke-result-path", type=Path)
    parser.add_argument("--funasr-smoke-assembly-path", type=Path)
    parser.add_argument("--funasr-smoke-assembly-json")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    batch_report: dict[str, Any] | None = None,
    simulated_shadow_batch_report: dict[str, Any] | None = None,
    funasr_readiness_report: dict[str, Any] | None = None,
    funasr_smoke_result_report: dict[str, Any] | None = None,
    funasr_smoke_assembly_report: dict[str, Any] | None = None,
    funasr_approval_packet: dict[str, Any] | None = None,
    public_audio_decision: dict[str, Any] | None = None,
    degraded_pilot_acceptance: dict[str, Any] | None = None,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    resolved_funasr_readiness_report, funasr_readiness_errors = _json_object_from_path(
        args.funasr_readiness_path,
        "funasr_readiness",
    )
    if funasr_readiness_report is not None:
        if resolved_funasr_readiness_report is not None:
            funasr_readiness_errors.append("provide only one funasr_readiness source")
        else:
            resolved_funasr_readiness_report = funasr_readiness_report
    if funasr_readiness_errors:
        report = _blocked_funasr_readiness_input_report(funasr_readiness_errors)
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    resolved_funasr_smoke_result_report, funasr_smoke_result_errors = _json_object_from_path(
        args.funasr_smoke_result_path,
        "funasr_smoke_result",
    )
    if funasr_smoke_result_report is not None:
        if resolved_funasr_smoke_result_report is not None:
            funasr_smoke_result_errors.append("provide only one funasr_smoke_result source")
        else:
            resolved_funasr_smoke_result_report = funasr_smoke_result_report
    funasr_smoke_result_errors.extend(
        _funasr_smoke_result_gate_report_errors(resolved_funasr_smoke_result_report)
    )
    if funasr_smoke_result_errors:
        report = _blocked_funasr_smoke_result_input_report(funasr_smoke_result_errors)
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    resolved_funasr_smoke_assembly_report, funasr_smoke_assembly_errors = _resolve_json_input(
        path=args.funasr_smoke_assembly_path,
        text=args.funasr_smoke_assembly_json,
        label="funasr_smoke_assembly",
    )
    if funasr_smoke_assembly_report is not None:
        if resolved_funasr_smoke_assembly_report is not None:
            funasr_smoke_assembly_errors.append("provide only one funasr_smoke_assembly source")
        else:
            resolved_funasr_smoke_assembly_report = funasr_smoke_assembly_report
    if (
        resolved_funasr_smoke_result_report is not None
        and resolved_funasr_smoke_assembly_report is not None
    ):
        funasr_smoke_assembly_errors.append("provide only one FunASR smoke evidence source")
    funasr_smoke_assembly_errors.extend(
        _funasr_smoke_assembly_report_errors(resolved_funasr_smoke_assembly_report)
    )
    if funasr_smoke_assembly_errors:
        report = _blocked_funasr_smoke_assembly_input_report(funasr_smoke_assembly_errors)
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    resolved_acceptance, acceptance_errors = _resolve_json_input(
        path=args.degraded_pilot_acceptance_path,
        text=args.degraded_pilot_acceptance_json,
        label="degraded_pilot_acceptance",
    )
    if degraded_pilot_acceptance is not None:
        if resolved_acceptance is not None:
            acceptance_errors.append("provide only one degraded_pilot_acceptance source")
        else:
            resolved_acceptance = degraded_pilot_acceptance
    if acceptance_errors:
        report = _blocked_acceptance_input_report(acceptance_errors)
        json.dump(report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 1

    report = build_asr_quality_decision_gate_report(
        batch_report=batch_report,
        simulated_shadow_batch_report=simulated_shadow_batch_report,
        funasr_readiness_report=resolved_funasr_readiness_report,
        funasr_smoke_result_report=resolved_funasr_smoke_result_report,
        funasr_smoke_assembly_report=resolved_funasr_smoke_assembly_report,
        funasr_approval_packet=funasr_approval_packet,
        public_audio_decision=public_audio_decision,
        degraded_pilot_acceptance=resolved_acceptance,
    )
    report["funasr_readiness_input_status"] = (
        "loaded" if resolved_funasr_readiness_report is not None else "not_requested"
    )
    report["funasr_readiness_input_errors"] = []
    report["funasr_smoke_result_input_status"] = (
        "loaded" if resolved_funasr_smoke_result_report is not None else "not_requested"
    )
    report["funasr_smoke_result_input_errors"] = []
    report["funasr_smoke_assembly_input_status"] = (
        "loaded" if resolved_funasr_smoke_assembly_report is not None else "not_requested"
    )
    report["funasr_smoke_assembly_input_errors"] = []
    report["acceptance_input_status"] = "loaded" if resolved_acceptance is not None else "not_requested"
    report["acceptance_input_errors"] = []
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["decision_status"] == "asr_quality_current_gate_not_blocking" else 1


if __name__ == "__main__":
    raise SystemExit(main())

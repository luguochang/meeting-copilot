import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "asr_quality_decision_gate.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "asr_quality_decision_gate",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _batch_report(
    *,
    overall_decision: str = "blocked_by_asr_quality",
    perfect_ready: int = 5,
    mock_ready: int = 5,
    real_blocked: int = 4,
    non_engineering_candidates: int = 0,
) -> dict[str, object]:
    return {
        "report_mode": "copilot_product_value_batch_gate",
        "status": "completed",
        "overall_decision": overall_decision,
        "next_action": "improve_real_asr_quality_or_prepare_model_approval",
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "perfect_lane_ready_count": perfect_ready,
        "mock_lane_ready_count": mock_ready,
        "real_asr_blocked_count": real_blocked,
        "non_engineering_candidate_count": non_engineering_candidates,
        "decision_counts": {
            "blocked_by_asr_quality": real_blocked,
            "product_logic_ready": 1,
        },
        "safe_to_call_llm_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_download_models_now": False,
        "safe_to_write_runtime_audio_now": False,
    }


def _funasr_readiness(*, status: str = "blocked", cached_models: str = "missing") -> dict[str, object]:
    return {
        "report_mode": "funasr_synthetic_smoke_readiness",
        "readiness_status": status,
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "required_cached_models_status": cached_models,
        "model_download_status": "not_started",
        "offline_guard_status": "required_before_execution",
        "execution_mode": "preflight_only_no_execution_authorization",
        "safe_to_execute_local_funasr_now": False,
        "safe_to_download_models": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "validation_errors": ["required FunASR cached model files are missing"]
        if status == "blocked"
        else [],
    }


def _approval_packet() -> dict[str, object]:
    return {
        "drv_id": "DRV-019",
        "report_mode": "funasr_model_download_approval_packet_static_report",
        "approval_packet_status": "generated_for_manual_review",
        "approval_packet_mode": "manual_user_run_only",
        "execution_mode": "manual_user_run_only",
        "model_download_execution_status": "not_run",
        "approval_blockers": [
            "explicit_user_approval_for_funasr_model_download",
            "approved_model_provider_modelscope_iic",
        ],
        "safe_to_execute_download_now": False,
        "safe_to_download_models_now": False,
        "safe_to_run_modelscope_now": False,
        "safe_to_run_funasr_smoke_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _public_audio_decision(*, status: str = "blocked_no_verified_public_sample_manifest") -> dict[str, object]:
    return {
        "decision_mode": "public_audio_planned_sample_manifest_decision_only",
        "decision_id": "DRV-031",
        "decision_status": status,
        "public_audio_stage_status": "blocked_no_planned_samples"
        if status == "blocked_no_verified_public_sample_manifest"
        else "ready_for_manual_download_review",
        "safe_to_download_now": False,
        "safe_to_extract_now": False,
        "safe_to_transcode_now": False,
        "safe_to_call_asr_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
    }


def _funasr_smoke_result(
    *,
    status: str = "not_provided",
    counts_as_go: bool = False,
) -> dict[str, object]:
    return {
        "decision_id": "DRV-044",
        "report_mode": "funasr_synthetic_smoke_result_evidence_gate",
        "schema_version": "funasr_synthetic_smoke_result.v1",
        "evidence_status": "schema_validated_no_asr_execution"
        if status != "not_provided"
        else "not_provided",
        "quality_evidence_status": status,
        "counts_as_asr_quality_go_evidence": counts_as_go,
        "counts_as_real_mic_go_evidence": False,
        "scenario_summary": {
            "engineering_scenario_count": 4 if counts_as_go else 1,
            "negative_control_count": 1 if counts_as_go else 0,
            "engineering_min_normalized_recall": 0.86 if status != "blocked" else 0.79,
            "negative_control_candidate_cards": 0,
        },
        "batch_artifact_provenance_status": "validated" if counts_as_go else "not_required",
        "batch_artifact_count": 5 if counts_as_go else 0,
        "validation_errors": [] if status != "blocked" else ["engineering normalized_recall must be >= 0.8"],
        "safe_to_run_asr_now": False,
        "safe_to_download_models_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _funasr_smoke_assembly_report(
    *,
    status: str = "drv044_batch_evidence_validated",
    nested_gate_report: dict[str, object] | None = None,
    counts_as_go: bool = True,
) -> dict[str, object]:
    return {
        "decision_id": "DRV-046",
        "assembly_mode": "funasr_synthetic_smoke_batch_evidence_assembler",
        "assembly_version": "funasr_synthetic_smoke_batch_evidence_assembler.v1",
        "assembly_status": status,
        "artifact_read_status": "read",
        "artifact_count": 5 if counts_as_go else 0,
        "drv044_gate_report": nested_gate_report
        if nested_gate_report is not None
        else _funasr_smoke_result(
            status="funasr_synthetic_smoke_quality_batch_confirmed",
            counts_as_go=counts_as_go,
        ),
        "counts_as_asr_quality_go_evidence": counts_as_go,
        "counts_as_real_mic_go_evidence": False,
        "validation_errors": [],
        "safe_to_run_asr_now": False,
        "safe_to_download_models_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_read_audio_file_now": False,
        "safe_to_write_artifacts_now": False,
    }


def _simulated_shadow_batch_report(
    *,
    status: str = "simulated_shadow_pipeline_batch_passed",
    engineering_preview_count: int = 4,
    negative_fake_count: int = 0,
) -> dict[str, object]:
    return {
        "batch_runner_id": "DRV-042",
        "runner_mode": "simulated_shadow_pipeline_batch_smoke",
        "batch_status": status,
        "scenario_count": 5,
        "engineering_scenario_count": 4,
        "negative_control_count": 1,
        "engineering_preview_created_count": engineering_preview_count,
        "negative_control_blocked_count": 1 if negative_fake_count == 0 else 0,
        "negative_control_fake_candidate_count": negative_fake_count,
        "go_evidence_status": "not_go_evidence_batch_replay_or_feedback_missing",
        "artifact_write_status": "not_written",
        "public_audio_download_status": "not_downloaded",
        "remote_asr_call_status": "not_called",
        "llm_call_status": "not_called",
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def _degraded_pilot_acceptance(*, token: str = "ACCEPT_DEGRADED_ASR_FOR_SINGLE_SHADOW_TEST") -> dict[str, object]:
    return {
        "acceptance_record_version": "asr_quality_degraded_pilot_acceptance.v1",
        "acceptance_id": "degraded_asr_single_shadow_test",
        "acceptance_scope": "single_user_real_mic_shadow_test_only",
        "acceptance_token": token,
        "accepted_risks": [
            "chinese_technical_entity_recall_below_first_pilot_target",
            "suggestion_cards_may_be_late_wrong_or_missing",
            "result_cannot_be_used_as_asr_quality_go_evidence",
        ],
        "operator_note": "Accept degraded ASR only to test desktop timing and feedback loop.",
    }


def test_asr_quality_decision_requires_funasr_model_or_drv019_approval_when_real_asr_is_blocked():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        simulated_shadow_batch_report=_simulated_shadow_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_id"] == "DRV-032"
    assert report["decision_mode"] == "asr_quality_decision_gate"
    assert report["decision_status"] == "requires_funasr_model_dir_or_drv019_approval"
    assert report["product_value_batch_overall_decision"] == "blocked_by_asr_quality"
    assert report["perfect_lane_ready_count"] == 5
    assert report["mock_lane_ready_count"] == 5
    assert report["real_asr_blocked_count"] == 4
    assert report["non_engineering_candidate_count"] == 0
    assert report["simulated_shadow_batch_status"] == "simulated_shadow_pipeline_batch_passed"
    assert report["simulated_shadow_engineering_preview_created_count"] == 4
    assert report["simulated_shadow_negative_control_fake_candidate_count"] == 0
    assert report["simulated_shadow_batch_go_evidence_status"] == (
        "not_go_evidence_batch_replay_or_feedback_missing"
    )
    assert report["funasr_readiness_status"] == "blocked"
    assert report["funasr_required_cached_models_status"] == "missing"
    assert report["funasr_approval_packet_status"] == "generated_for_manual_review"
    assert report["public_audio_decision_status"] == "blocked_no_verified_public_sample_manifest"
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_download_models_now"] is False
    assert report["safe_to_download_public_audio_now"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_call_llm_now"] is False
    assert report["quality_exit_status"] == "not_exited"
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is False
    assert report["recommended_quality_exit_path_id"] == (
        "local_funasr_model_dir_if_available_else_explicit_degraded_pilot_decision"
    )
    assert report["mainline_stop_conditions"] == [
        "do_not_expand_provider_bakeoff_without_funasr_model_or_explicit_remote_asr_decision",
        "do_not_add_more_report_only_readiness_wrappers",
        "do_not_start_real_mic_shadow_test_without_quality_exit_or_explicit_degraded_acceptance",
    ]
    assert [option["path_id"] for option in report["quality_exit_options"]] == [
        "verified_local_funasr_model_dir",
        "drv019_manual_model_download",
        "optional_remote_asr_comparison",
        "explicit_degraded_pilot_acceptance",
    ]
    assert report["quality_exit_options"][0]["extra_provider_fee"] is False
    assert report["quality_exit_options"][1]["requires_explicit_user_approval"] is True
    assert report["quality_exit_options"][2]["default_enabled"] is False
    assert report["quality_exit_options"][3]["counts_as_asr_quality_go_evidence"] is False
    assert report["next_allowed_actions"] == [
        "provide_verified_local_funasr_model_dir",
        "approve_drv019_manual_model_download_packet",
        "accept_degraded_pilot_with_explicit_quality_risk",
        "continue_desktop_noop_or_mic_adapter_contract_without_claiming_asr_quality_solved",
    ]
    assert report["blocked_reasons"] == [
        "real_sherpa_asr_blocked_by_chinese_technical_entity_recall",
        "funasr_local_model_dir_or_cache_not_ready",
        "drv019_model_download_requires_explicit_user_approval",
    ]


def test_asr_quality_decision_fixes_simulated_shadow_batch_before_asr_provider_work():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        simulated_shadow_batch_report=_simulated_shadow_batch_report(
            status="failed_engineering_preview_or_negative_control",
            engineering_preview_count=3,
            negative_fake_count=1,
        ),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "fix_simulated_shadow_pipeline_first"
    assert report["quality_exit_status"] == "not_exited"
    assert report["simulated_shadow_batch_status"] == (
        "failed_engineering_preview_or_negative_control"
    )
    assert report["simulated_shadow_engineering_preview_created_count"] == 3
    assert report["simulated_shadow_negative_control_fake_candidate_count"] == 1
    assert report["blocked_reasons"] == [
        "simulated_shadow_pipeline_batch_not_passed",
        "do_not_blame_asr_provider_until_mock_shadow_pipeline_gate_recovers",
    ]
    assert report["next_allowed_actions"] == [
        "repair_simulated_shadow_pipeline_batch_inputs_or_gap_logic",
    ]
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_asr_quality_decision_fixes_product_logic_before_asr_provider_work():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(overall_decision="blocked_by_product_logic", perfect_ready=3, mock_ready=4),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "fix_product_logic_first"
    assert report["blocked_reasons"] == [
        "perfect_or_mock_lane_not_ready",
        "do_not_blame_asr_provider_until_product_logic_gate_recovers",
    ]
    assert report["next_allowed_actions"] == ["repair_evidence_gap_candidate_logic"]
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_download_models_now"] is False


def test_asr_quality_decision_keeps_funasr_preflight_ready_as_non_executable():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "funasr_cache_preflight_ready_requires_execution_approval"
    assert report["funasr_readiness_status"] == "cache_preflight_passed_offline_execution_not_proven"
    assert report["funasr_required_cached_models_status"] == "present"
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_download_models_now"] is False
    assert report["next_allowed_actions"] == [
        "approve_single_funasr_synthetic_smoke_run",
        "then_rerun_copilot_product_value_batch_gate",
        "keep_remote_asr_and_llm_disabled_by_default",
    ]


def test_asr_quality_decision_treats_single_funasr_smoke_candidate_as_not_exited():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_smoke_result_report=_funasr_smoke_result(
            status="funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation",
            counts_as_go=False,
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "funasr_smoke_candidate_requires_batch_confirmation"
    assert report["quality_exit_status"] == "not_exited"
    assert report["funasr_smoke_quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
    )
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["next_allowed_actions"] == [
        "run_funasr_batch_confirmation_with_four_engineering_scenarios_and_negative_control",
        "keep_real_mic_shadow_test_blocked_until_batch_confirmation_or_degraded_acceptance",
    ]


def test_asr_quality_decision_accepts_batch_confirmed_funasr_smoke_as_strict_quality_exit():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_smoke_result_report=_funasr_smoke_result(
            status="funasr_synthetic_smoke_quality_batch_confirmed",
            counts_as_go=True,
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "asr_quality_current_gate_not_blocking"
    assert report["quality_exit_status"] == "strict_quality_gate_not_blocking"
    assert report["funasr_smoke_quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_batch_confirmed"
    )
    assert report["counts_as_asr_quality_go_evidence"] is True
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is True
    assert report["safe_to_capture_microphone_now"] is False
    assert report["next_allowed_actions"] == [
        "advance_desktop_runtime_or_controlled_llm_cards",
        "keep_real_mic_shadow_test_behind_user_start_boundary",
    ]


def test_asr_quality_decision_accepts_drv046_assembly_report_as_strict_quality_exit():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_smoke_assembly_report=_funasr_smoke_assembly_report(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "asr_quality_current_gate_not_blocking"
    assert report["quality_exit_status"] == "strict_quality_gate_not_blocking"
    assert report["funasr_smoke_result_source"] == "drv046_batch_assembly"
    assert report["funasr_smoke_assembly_status"] == "drv044_batch_evidence_validated"
    assert report["funasr_smoke_quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_batch_confirmed"
    )
    assert report["counts_as_asr_quality_go_evidence"] is True
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is True
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_asr_quality_decision_rejects_drv046_assembly_report_without_validated_nested_drv044():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_smoke_assembly_report=_funasr_smoke_assembly_report(
            nested_gate_report={"decision_id": "DRV-046"}
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "blocked_by_funasr_smoke_assembly_input_guard"
    assert report["quality_exit_status"] == "not_exited"
    assert report["funasr_smoke_assembly_input_status"] == "blocked"
    assert report["funasr_smoke_assembly_status"] == "blocked"
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["safe_to_capture_microphone_now"] is False


def test_asr_quality_decision_rejects_batch_confirmed_funasr_smoke_without_validated_provenance():
    tool = load_tool_module()
    smoke_report = _funasr_smoke_result(
        status="funasr_synthetic_smoke_quality_batch_confirmed",
        counts_as_go=True,
    )
    smoke_report["batch_artifact_provenance_status"] = "missing"
    smoke_report["batch_artifact_count"] = 0

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_smoke_result_report=smoke_report,
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert report["decision_status"] == "fix_funasr_smoke_result_evidence_first"
    assert report["quality_exit_status"] == "not_exited"
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["funasr_smoke_validation_errors"] == [
        "batch confirmed FunASR smoke result requires validated artifact provenance"
    ]
    assert report["safe_to_capture_microphone_now"] is False


def test_asr_quality_decision_keeps_public_audio_schema_validated_as_manual_evidence_pending():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(status="schema_validated_no_download"),
    )

    assert report["decision_status"] == "requires_funasr_model_dir_or_drv019_approval"
    assert report["public_audio_decision_status"] == "schema_validated_no_download"
    assert report["public_audio_evidence_status"] == "manual_evidence_pending_no_download"
    assert report["safe_to_download_public_audio_now"] is False
    assert report["safe_to_call_public_audio_asr_now"] is False


def test_asr_quality_decision_accepts_explicit_degraded_pilot_without_claiming_quality_go():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
        degraded_pilot_acceptance=_degraded_pilot_acceptance(),
    )

    assert report["decision_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["quality_exit_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["degraded_pilot_acceptance_status"] == (
        "accepted_for_single_shadow_test_quality_risk"
    )
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is True
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["blocked_reasons"] == [
        "real_sherpa_asr_quality_still_below_first_pilot_target",
        "degraded_pilot_is_only_for_timing_feedback_loop_validation",
    ]
    assert report["next_allowed_actions"] == [
        "use_pcweb115_to_verify_all_non_asr_shadow_test_preconditions",
        "run_one_user_manual_shadow_test_only_after_explicit_ui_start",
        "collect_feedback_and_treat_result_as_degraded_pilot_evidence_not_asr_quality_go",
    ]
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_call_llm_now"] is False


def test_asr_quality_decision_rejects_invalid_degraded_pilot_acceptance_token():
    tool = load_tool_module()

    report = tool.build_asr_quality_decision_gate_report(
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
        degraded_pilot_acceptance=_degraded_pilot_acceptance(token="WRONG"),
    )

    assert report["decision_status"] == "requires_funasr_model_dir_or_drv019_approval"
    assert report["quality_exit_status"] == "not_exited"
    assert report["degraded_pilot_acceptance_status"] == "rejected"
    assert report["degraded_pilot_acceptance_errors"] == [
        "acceptance_token must match explicit degraded pilot token"
    ]
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is False


def test_asr_quality_decision_cli_defaults_output_safe_blocked_decision(capsys):
    tool = load_tool_module()

    exit_code = tool.main(
        [],
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    assert exit_code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["decision_id"] == "DRV-032"
    assert report["decision_status"] == "requires_funasr_model_dir_or_drv019_approval"
    assert report["safe_to_capture_microphone_now"] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_asr_quality_decision_cli_accepts_inline_degraded_pilot_acceptance_json():
    tool = load_tool_module()
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--degraded-pilot-acceptance-json",
            json.dumps(_degraded_pilot_acceptance()),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["decision_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["quality_exit_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["acceptance_input_status"] == "loaded"
    assert report["acceptance_input_errors"] == []
    assert report["can_unblock_real_mic_shadow_test_quality_gate"] is True
    assert report["counts_as_asr_quality_go_evidence"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_call_llm_now"] is False


def test_asr_quality_decision_cli_loads_allowed_acceptance_path_and_blocks_forbidden_path_before_reading(
    monkeypatch,
):
    tool = load_tool_module()
    acceptance_root = REPO_ROOT / "artifacts" / "tmp" / "asr-quality-decision-test"
    acceptance_root.mkdir(parents=True, exist_ok=True)
    acceptance_path = acceptance_root / "degraded-acceptance.json"
    acceptance_path.write_text(json.dumps(_degraded_pilot_acceptance()), encoding="utf-8")

    allowed_out = io.StringIO()
    allowed_exit_code = tool.main(
        [
            "--degraded-pilot-acceptance-path",
            str(acceptance_path),
        ],
        out=allowed_out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    allowed_report = json.loads(allowed_out.getvalue())
    assert allowed_exit_code == 1
    assert allowed_report["decision_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert allowed_report["acceptance_input_status"] == "loaded"

    def fail_if_read(*args, **kwargs):
        raise AssertionError("forbidden acceptance file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)
    blocked_out = io.StringIO()
    blocked_exit_code = tool.main(
        [
            "--degraded-pilot-acceptance-path",
            str(REPO_ROOT / "configs" / "local" / "degraded-acceptance.json"),
        ],
        out=blocked_out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    blocked_report = json.loads(blocked_out.getvalue())
    assert blocked_exit_code == 1
    assert blocked_report["decision_status"] == "blocked_by_degraded_pilot_acceptance_input_guard"
    assert blocked_report["acceptance_input_status"] == "blocked"
    assert blocked_report["acceptance_input_errors"] == [
        "degraded_pilot_acceptance is blocked: configs/local"
    ]
    assert blocked_report["can_unblock_real_mic_shadow_test_quality_gate"] is False
    assert blocked_report["safe_to_read_configs_local_now"] is False


def test_asr_quality_decision_cli_loads_allowed_funasr_readiness_path():
    tool = load_tool_module()
    readiness_root = REPO_ROOT / "artifacts" / "tmp" / "asr-quality-decision-test"
    readiness_root.mkdir(parents=True, exist_ok=True)
    readiness_path = readiness_root / "funasr-readiness.json"
    readiness_path.write_text(
        json.dumps(
            _funasr_readiness(
                status="cache_preflight_passed_offline_execution_not_proven",
                cached_models="present",
            )
        ),
        encoding="utf-8",
    )

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-readiness-path",
            str(readiness_path),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["funasr_readiness_input_status"] == "loaded"
    assert report["decision_status"] == "funasr_cache_preflight_ready_requires_execution_approval"
    assert report["funasr_readiness_status"] == "cache_preflight_passed_offline_execution_not_proven"
    assert report["funasr_required_cached_models_status"] == "present"
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_download_models_now"] is False
    assert report["next_allowed_actions"] == [
        "approve_single_funasr_synthetic_smoke_run",
        "then_rerun_copilot_product_value_batch_gate",
        "keep_remote_asr_and_llm_disabled_by_default",
    ]


def test_asr_quality_decision_cli_loads_allowed_funasr_smoke_result_gate_report_path():
    tool = load_tool_module()
    smoke_result_root = REPO_ROOT / "artifacts" / "tmp" / "asr-quality-decision-test"
    smoke_result_root.mkdir(parents=True, exist_ok=True)
    smoke_result_path = smoke_result_root / "funasr-smoke-result-gate.json"
    smoke_result_path.write_text(
        json.dumps(
            _funasr_smoke_result(
                status="funasr_synthetic_smoke_quality_batch_confirmed",
                counts_as_go=True,
            )
        ),
        encoding="utf-8",
    )

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-smoke-result-path",
            str(smoke_result_path),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 0
    assert report["funasr_smoke_result_input_status"] == "loaded"
    assert report["decision_status"] == "asr_quality_current_gate_not_blocking"
    assert report["quality_exit_status"] == "strict_quality_gate_not_blocking"
    assert report["safe_to_capture_microphone_now"] is False


def test_asr_quality_decision_cli_loads_allowed_funasr_smoke_assembly_path():
    tool = load_tool_module()
    assembly_root = REPO_ROOT / "artifacts" / "tmp" / "asr-quality-decision-test"
    assembly_root.mkdir(parents=True, exist_ok=True)
    assembly_path = assembly_root / "funasr-smoke-assembly.json"
    assembly_path.write_text(
        json.dumps(_funasr_smoke_assembly_report()),
        encoding="utf-8",
    )

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-smoke-assembly-path",
            str(assembly_path),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(
            status="cache_preflight_passed_offline_execution_not_proven",
            cached_models="present",
        ),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 0
    assert report["funasr_smoke_assembly_input_status"] == "loaded"
    assert report["funasr_smoke_result_source"] == "drv046_batch_assembly"
    assert report["decision_status"] == "asr_quality_current_gate_not_blocking"
    assert report["quality_exit_status"] == "strict_quality_gate_not_blocking"
    assert report["safe_to_capture_microphone_now"] is False


def test_asr_quality_decision_cli_rejects_raw_unvalidated_funasr_smoke_result_path():
    tool = load_tool_module()
    smoke_result_root = REPO_ROOT / "artifacts" / "tmp" / "asr-quality-decision-test"
    smoke_result_root.mkdir(parents=True, exist_ok=True)
    raw_smoke_result_path = smoke_result_root / "raw-funasr-smoke-result.json"
    raw_smoke_result_path.write_text(
        json.dumps(
            {
                "manifest_version": "funasr_synthetic_smoke_result.v1",
                "evidence_kind": "batch_synthetic_confirmation",
                "provider": "funasr_streaming",
                "scenario_results": [],
            }
        ),
        encoding="utf-8",
    )

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-smoke-result-path",
            str(raw_smoke_result_path),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["decision_status"] == "blocked_by_funasr_smoke_result_input_guard"
    assert report["funasr_smoke_result_input_status"] == "blocked"
    assert report["funasr_smoke_result_input_errors"] == [
        "funasr_smoke_result must be a DRV-044 gate report"
    ]
    assert report["safe_to_run_funasr_smoke_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_asr_quality_decision_cli_blocks_forbidden_funasr_readiness_path_before_reading(
    monkeypatch,
):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("forbidden FunASR readiness file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-readiness-path",
            str(REPO_ROOT / "configs" / "local" / "funasr-readiness.json"),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["decision_status"] == "blocked_by_funasr_readiness_input_guard"
    assert report["funasr_readiness_input_status"] == "blocked"
    assert report["funasr_readiness_input_errors"] == [
        "funasr_readiness is blocked: configs/local"
    ]
    assert report["quality_exit_status"] == "not_exited"
    assert report["safe_to_read_configs_local_now"] is False
    assert report["safe_to_run_funasr_smoke_now"] is False


def test_asr_quality_decision_cli_blocks_forbidden_funasr_smoke_assembly_path_before_reading(
    monkeypatch,
):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("forbidden FunASR smoke assembly file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    out = io.StringIO()
    exit_code = tool.main(
        [
            "--funasr-smoke-assembly-path",
            str(REPO_ROOT / "configs" / "local" / "funasr-assembly.json"),
        ],
        out=out,
        batch_report=_batch_report(),
        funasr_readiness_report=_funasr_readiness(),
        funasr_approval_packet=_approval_packet(),
        public_audio_decision=_public_audio_decision(),
    )

    report = json.loads(out.getvalue())
    assert exit_code == 1
    assert report["decision_status"] == "blocked_by_funasr_smoke_assembly_input_guard"
    assert report["funasr_smoke_assembly_input_status"] == "blocked"
    assert report["funasr_smoke_assembly_input_errors"] == [
        "funasr_smoke_assembly is blocked: configs/local"
    ]
    assert report["quality_exit_status"] == "not_exited"
    assert report["safe_to_read_configs_local_now"] is False
    assert report["safe_to_run_funasr_smoke_now"] is False

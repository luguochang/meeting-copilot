import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "real-mic-shadow-test-readiness.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_readiness_gate.py"


EXPECTED_FALSE_FLAGS = [
    "safe_to_access_microphone_from_gate_now",
    "safe_to_enumerate_audio_devices_from_gate_now",
    "safe_to_request_audio_permission_from_gate_now",
    "safe_to_read_real_user_audio_from_gate_now",
    "safe_to_write_audio_chunk_from_gate_now",
    "safe_to_delete_audio_chunk_from_gate_now",
    "safe_to_spawn_worker_from_gate_now",
    "safe_to_run_tauri_or_cargo_from_gate_now",
    "safe_to_read_configs_local_from_gate_now",
    "safe_to_read_secret_from_gate_now",
    "safe_to_call_remote_asr_from_gate_now",
    "safe_to_call_llm_from_gate_now",
    "safe_to_download_models_from_gate_now",
    "safe_to_download_public_audio_from_gate_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "real_mic_shadow_test_readiness_gate",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def passing_asr_quality_report() -> dict:
    return {
        "decision_status": "asr_quality_current_gate_not_blocking",
        "product_value_batch_status": "completed",
        "product_value_batch_overall_decision": "asr_quality_current_gate_not_blocking",
        "perfect_lane_ready_count": 5,
        "mock_lane_ready_count": 5,
        "real_asr_blocked_count": 0,
        "non_engineering_candidate_count": 0,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_run_cargo_tauri_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
    }


def degraded_pilot_asr_quality_report() -> dict:
    return {
        "decision_status": "degraded_pilot_accepted_with_quality_risk",
        "quality_exit_status": "degraded_pilot_accepted_with_quality_risk",
        "degraded_pilot_acceptance_status": "accepted_for_single_shadow_test_quality_risk",
        "can_unblock_real_mic_shadow_test_quality_gate": True,
        "counts_as_asr_quality_go_evidence": False,
        "product_value_batch_status": "completed",
        "product_value_batch_overall_decision": "blocked_by_asr_quality",
        "perfect_lane_ready_count": 5,
        "mock_lane_ready_count": 5,
        "real_asr_blocked_count": 4,
        "non_engineering_candidate_count": 0,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_run_cargo_tauri_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
    }


def approved_worker_mic_source_report() -> dict:
    return {
        "pcweb_id": "PCWEB-114",
        "worker_mic_source_approval_packet_status": "ready_for_manual_review_not_executable",
        "worker_mic_source_approval_status": "manually_approved_for_single_shadow_test",
        "approved_to_execute_now": False,
        "safe_to_accept_worker_mic_source_now": False,
        "safe_to_execute_worker_prepare_now": False,
        "safe_to_capture_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def observed_tauri_noop_evidence() -> dict:
    return {
        "evidence_status": "validated_noop_ipc_observed",
        "run_environment": "tauri_webview",
        "observed_command_count": 10,
        "safe_to_capture_audio_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def pcweb119_capture_evidence() -> dict:
    return {
        "capture_version": "desktop_tauri_noop_webview_run_capture.v1",
        "capture_status": "captured_validated_tauri_noop_run",
        "source_endpoint": "/desktop/tauri-noop-run-results/validations",
        "validated_command_count": 10,
        "returned_command_count": 10,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_audio_now": False,
        "safe_to_start_asr_worker_now": False,
        "safe_to_read_audio_chunk_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "run_result": {
            "run_id": "pcweb115-cli-evidence",
            "run_environment": "tauri_webview",
            "explicit_tauri_run_approval_recorded": True,
            "web_app_url_status": "local_dev_url_loaded",
            "ipc_transport_status": "tauri_ipc_available",
        },
        "validation_report": {
            "result_validation_status": "passed",
            "real_tauri_noop_run_evidence_status": "ready_for_worker_mic_source_approval_review",
        },
    }


def real_mic_adapter_evidence() -> dict:
    return {
        "implementation_status": "implemented_and_smoke_tested",
        "commands_smoked": [
            "prepare",
            "status",
            "start",
            "pause",
            "resume",
            "stop",
            "delete_audio_chunks",
        ],
        "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
        "requires_explicit_user_start": True,
        "default_uploads_raw_audio": False,
        "default_remote_asr_enabled": False,
        "safe_to_capture_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def real_asr_worker_evidence() -> dict:
    return {
        "implementation_status": "implemented_and_smoke_tested",
        "event_contract_status": "partial_final_revision_error_end_of_stream_supported",
        "worker_output_root": "artifacts/tmp/asr_events",
        "worker_runtime_root": "artifacts/tmp/desktop_asr_worker_runtime",
        "web_handoff_status": "closed_to_evidence_state_gap",
        "source_kind": "mic",
        "command_catalog_smoked": [
            "worker.prepare",
            "worker.start",
            "worker.health",
            "worker.collect_events",
            "worker.stop",
            "worker.cleanup",
        ],
        "requires_explicit_user_start": True,
        "default_uploads_raw_audio": False,
        "default_remote_asr_enabled": False,
        "safe_to_spawn_worker_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def export_feedback_evidence() -> dict:
    return {
        "report_schema_status": "real_mic_shadow_test_report.v1_ready",
        "feedback_export_bundle_status": "ready_for_real_report_after_user_shadow_test",
        "feedback_labels_available": [
            "useful",
            "would_have_asked",
            "wrong",
            "too_late",
            "too_intrusive",
            "dismissed",
        ],
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
    }


def test_real_mic_shadow_test_readiness_policy_exists_and_blocks_side_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-115"
    assert policy["policy_name"] == "Real Mic Shadow Test Readiness Gate"
    assert policy["policy_status"] == "real_mic_shadow_test_readiness_policy_only"
    assert policy["readiness_mode"] == "static_preflight_report_only"
    assert policy["default_readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_access_mic_network_models_audio_or_processes():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "cargo",
        "tauri dev",
        "ffmpeg",
        "afconvert",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "cpal",
        "rodio",
        "wave.open",
        "requests.",
        "urllib.request",
        "modelscope",
        "AutoModel",
        "getUserMedia",
        "MediaRecorder",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_default_report_blocks_real_mic_shadow_test_with_actionable_reasons():
    tool = load_tool_module()

    report = tool.build_real_mic_shadow_test_readiness_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-115"
    assert report["report_mode"] == "real_mic_shadow_test_readiness_gate_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert report["user_can_start_real_mic_shadow_test_now"] is False
    assert report["next_required_decision"] == "resolve_blockers_before_real_mic_shadow_test"
    assert report["readiness_summary"] == {
        "asr_quality_ready": False,
        "real_tauri_noop_run_observed": False,
        "worker_mic_source_ready": False,
        "mic_adapter_ready": False,
        "asr_worker_ready": False,
        "export_feedback_ready": True,
    }
    assert report["blockers"] == [
        "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval",
        "real_tauri_noop_run_result_not_provided",
        "worker_mic_source_not_approved",
        "mic_adapter_real_implementation_not_available",
        "asr_worker_real_mic_source_not_available",
    ]
    assert "provide_verified_local_funasr_model_dir_or_approve_drv019_or_accept_degraded_pilot" in (
        report["allowed_next_actions"]
    )
    assert "perform_explicit_real_tauri_noop_run_without_mic_or_worker" in (
        report["allowed_next_actions"]
    )
    assert "implement_minimal_real_mic_adapter_after_explicit_approval" in (
        report["allowed_next_actions"]
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_future_positive_path_requires_all_preconditions_but_gate_still_has_no_side_effects():
    tool = load_tool_module()

    report = tool.build_real_mic_shadow_test_readiness_report(
        policy_path=POLICY_PATH,
        asr_quality_report=passing_asr_quality_report(),
        tauri_noop_evidence=observed_tauri_noop_evidence(),
        worker_mic_source_approval_report=approved_worker_mic_source_report(),
        mic_adapter_evidence=real_mic_adapter_evidence(),
        asr_worker_evidence=real_asr_worker_evidence(),
        export_feedback_evidence=export_feedback_evidence(),
    )

    assert report["readiness_status"] == "ready_for_user_manual_real_mic_shadow_test"
    assert report["user_can_start_real_mic_shadow_test_now"] is True
    assert report["next_required_decision"] == "user_explicitly_starts_real_mic_shadow_test_in_ui"
    assert report["blockers"] == []
    assert report["readiness_summary"] == {
        "asr_quality_ready": True,
        "real_tauri_noop_run_observed": True,
        "worker_mic_source_ready": True,
        "mic_adapter_ready": True,
        "asr_worker_ready": True,
        "export_feedback_ready": True,
    }
    assert report["pilot_protocol"] == {
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
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_degraded_pilot_acceptance_can_satisfy_asr_quality_precondition_with_risk_label():
    tool = load_tool_module()

    report = tool.build_real_mic_shadow_test_readiness_report(
        policy_path=POLICY_PATH,
        asr_quality_report=degraded_pilot_asr_quality_report(),
        tauri_noop_evidence=observed_tauri_noop_evidence(),
        worker_mic_source_approval_report=approved_worker_mic_source_report(),
        mic_adapter_evidence=real_mic_adapter_evidence(),
        asr_worker_evidence=real_asr_worker_evidence(),
        export_feedback_evidence=export_feedback_evidence(),
    )

    assert report["readiness_status"] == "ready_for_user_manual_real_mic_shadow_test"
    assert report["user_can_start_real_mic_shadow_test_now"] is True
    assert report["asr_quality_decision_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["asr_quality_exit_status"] == "degraded_pilot_accepted_with_quality_risk"
    assert report["asr_quality_counts_as_go_evidence"] is False
    assert report["blockers"] == []
    assert report["readiness_summary"]["asr_quality_ready"] is True
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_tauri_noop_ready_accepts_pcweb119_capture_evidence_shape():
    tool = load_tool_module()

    assert tool._tauri_noop_ready(pcweb119_capture_evidence()) is True

    evidence = pcweb119_capture_evidence()
    evidence["safe_to_capture_audio_now"] = True

    assert tool._tauri_noop_ready(evidence) is False


def test_cli_accepts_inline_evidence_json_and_reports_ready_without_side_effects():
    tool = load_tool_module()
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--asr-quality-report-json",
            json.dumps(degraded_pilot_asr_quality_report()),
            "--tauri-noop-evidence-json",
            json.dumps(pcweb119_capture_evidence()),
            "--worker-mic-source-approval-json",
            json.dumps(approved_worker_mic_source_report()),
            "--mic-adapter-evidence-json",
            json.dumps(real_mic_adapter_evidence()),
            "--asr-worker-evidence-json",
            json.dumps(real_asr_worker_evidence()),
            "--export-feedback-evidence-json",
            json.dumps(export_feedback_evidence()),
        ],
        out=out,
    )

    report = json.loads(out.getvalue())
    assert exit_code == 0
    assert report["readiness_status"] == "ready_for_user_manual_real_mic_shadow_test"
    assert report["user_can_start_real_mic_shadow_test_now"] is True
    assert report["readiness_summary"] == {
        "asr_quality_ready": True,
        "real_tauri_noop_run_observed": True,
        "worker_mic_source_ready": True,
        "mic_adapter_ready": True,
        "asr_worker_ready": True,
        "export_feedback_ready": True,
    }
    assert report["tauri_noop_evidence_status"] == "captured_validated_tauri_noop_run"
    assert report["asr_quality_counts_as_go_evidence"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_cli_loads_allowed_artifact_evidence_path_and_rejects_forbidden_path_before_reading():
    tool = load_tool_module()
    evidence_root = REPO_ROOT / "artifacts" / "tmp" / "pcweb115-readiness-test"
    evidence_root.mkdir(parents=True, exist_ok=True)
    tauri_evidence_path = evidence_root / "tauri-evidence.json"
    tauri_evidence_path.write_text(
        json.dumps(pcweb119_capture_evidence()),
        encoding="utf-8",
    )

    allowed_out = io.StringIO()
    allowed_exit_code = tool.main(
        [
            "--asr-quality-report-json",
            json.dumps(degraded_pilot_asr_quality_report()),
            "--tauri-noop-evidence-path",
            str(tauri_evidence_path),
            "--worker-mic-source-approval-json",
            json.dumps(approved_worker_mic_source_report()),
            "--mic-adapter-evidence-json",
            json.dumps(real_mic_adapter_evidence()),
            "--asr-worker-evidence-json",
            json.dumps(real_asr_worker_evidence()),
            "--export-feedback-evidence-json",
            json.dumps(export_feedback_evidence()),
        ],
        out=allowed_out,
    )

    allowed_report = json.loads(allowed_out.getvalue())
    assert allowed_exit_code == 0
    assert allowed_report["readiness_summary"]["real_tauri_noop_run_observed"] is True
    assert allowed_report["evidence_input_status"] == "loaded"

    blocked_out = io.StringIO()
    blocked_exit_code = tool.main(
        [
            "--tauri-noop-evidence-path",
            str(REPO_ROOT / "configs" / "local" / "tauri-evidence.json"),
        ],
        out=blocked_out,
    )

    blocked_report = json.loads(blocked_out.getvalue())
    assert blocked_exit_code == 1
    assert blocked_report["readiness_status"] == "blocked_by_evidence_path_guard"
    assert blocked_report["blockers"] == ["tauri_noop_evidence_path is blocked: configs/local"]
    assert blocked_report["safe_to_read_configs_local_from_gate_now"] is False


def test_asr_worker_ready_requires_full_pcweb122_evidence_shape():
    tool = load_tool_module()
    evidence = real_asr_worker_evidence()
    evidence.pop("source_kind")

    assert tool._asr_worker_ready(evidence) is False

    evidence["source_kind"] = "mic"

    assert tool._asr_worker_ready(evidence) is True


def test_any_missing_future_precondition_keeps_real_mic_shadow_test_blocked():
    tool = load_tool_module()

    report = tool.build_real_mic_shadow_test_readiness_report(
        policy_path=POLICY_PATH,
        asr_quality_report=passing_asr_quality_report(),
        tauri_noop_evidence=observed_tauri_noop_evidence(),
        worker_mic_source_approval_report=approved_worker_mic_source_report(),
        mic_adapter_evidence=real_mic_adapter_evidence(),
        export_feedback_evidence=export_feedback_evidence(),
    )

    assert report["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert report["user_can_start_real_mic_shadow_test_now"] is False
    assert report["readiness_summary"]["asr_worker_ready"] is False
    assert report["blockers"] == ["asr_worker_real_mic_source_not_available"]


def test_policy_path_rejects_forbidden_roots_before_reading():
    tool = load_tool_module()
    forbidden_policy_path = REPO_ROOT / "configs" / "local" / "real-mic-shadow-test-readiness.policy.json"

    report = tool.build_real_mic_shadow_test_readiness_report(
        policy_path=forbidden_policy_path,
    )

    assert report["policy_validation_status"] == "blocked_by_policy_path_guard"
    assert report["readiness_status"] == "blocked_by_policy_path_guard"
    assert report["blockers"] == ["policy path is blocked: configs/local"]
    assert report["safe_to_read_configs_local_from_gate_now"] is False


def test_custom_policy_cannot_enable_gate_side_effects(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_access_microphone_from_gate_now"] = True
    policy["safe_to_call_remote_asr_from_gate_now"] = True
    policy["safe_to_download_models_from_gate_now"] = True
    policy_path = tmp_path / "real-mic-shadow-test-readiness.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_real_mic_shadow_test_readiness_report(policy_path=policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["readiness_status"] == "blocked_by_policy_validation"
    assert "safe_to_access_microphone_from_gate_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_call_remote_asr_from_gate_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_download_models_from_gate_now must be false" in report[
        "policy_validation_errors"
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False

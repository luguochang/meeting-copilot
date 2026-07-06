import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "worker-mic-source-single-shadow-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_worker_mic_source_single_shadow_approval.py"
READINESS_TOOL_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_readiness_gate.py"

APPROVAL_TOKEN = "APPROVE_WORKER_MIC_SOURCE_FOR_SINGLE_SHADOW_TEST_NO_EXECUTION"
EXPECTED_FALSE_FLAGS = [
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
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_module():
    return load_module(TOOL_PATH, "desktop_worker_mic_source_single_shadow_approval")


def load_readiness_module():
    return load_module(READINESS_TOOL_PATH, "real_mic_shadow_test_readiness_gate")


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_manual_review_packet_report() -> dict:
    return {
        "pcweb_id": "PCWEB-114",
        "report_mode": "desktop_worker_mic_source_approval_static_report",
        "policy_validation_status": "passed",
        "connector_request_validation_status": "passed",
        "tauri_result_validation_status": "passed",
        "worker_mic_source_approval_packet_status": "ready_for_manual_review_not_executable",
        "worker_mic_source_approval_status": "not_approved",
        "approval_scope": "allow_worker_prepare_source_kind_mic_after_manual_approval",
        "connector_session_id": "worker_mic_connector_review",
        "tauri_run_id": "worker_mic_connector_review",
        "worker_source_kind": "mic",
        "worker_command_blocker": "source_kind requires later approval: mic",
        "manual_review_packet": {
            "approval_scope": "allow_worker_prepare_source_kind_mic_after_manual_approval",
            "connector_status": "ready_for_worker_mic_connector_contract_review",
            "tauri_noop_run_result_status": "validated_noop_ipc_observed",
            "worker_prepare_source_kind": "mic",
            "worker_prepare_blocker_to_remove_later": "source_kind requires later approval: mic",
            "execution_status_after_packet": "still_not_executable",
            "next_required_decision": "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked",
        },
        **{flag: False for flag in EXPECTED_FALSE_FLAGS},
    }


def valid_approval_record() -> dict:
    return {
        "approval_record_version": "worker_mic_source_single_shadow_approval.v1",
        "approval_id": "worker_mic_connector_review",
        "approved_connector_session_id": "worker_mic_connector_review",
        "approval_scope": "single_user_real_mic_shadow_test_only",
        "approval_token": APPROVAL_TOKEN,
        "approval_note": "User explicitly approved one shadow-test worker mic source boundary.",
    }


def test_policy_exists_and_blocks_all_execution_side_effects():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-123"
    assert policy["policy_name"] == "Desktop Worker Mic Source Single Shadow Approval"
    assert policy["policy_status"] == "desktop_worker_mic_source_single_shadow_approval_policy_only"
    assert policy["approval_mode"] == "single_shadow_test_approval_evidence_only"
    assert policy["required_previous_contracts"] == ["PCWEB-114", "PCWEB-120", "PCWEB-122"]
    assert policy["required_packet_status"] == "ready_for_manual_review_not_executable"
    assert policy["approved_status"] == "manually_approved_for_single_shadow_test"
    assert policy["approval_scope"] == "single_user_real_mic_shadow_test_only"
    assert policy["approval_token"] == APPROVAL_TOKEN
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_execute_worker_audio_network_or_models():
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


def test_missing_approval_record_keeps_worker_mic_source_not_approved():
    tool = load_tool_module()

    report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["approval_record_validation_status"] == "not_provided"
    assert report["worker_mic_source_approval_status"] == "not_approved"
    assert report["worker_mic_source_approval_packet_status"] == "ready_for_manual_review_not_executable"
    assert report["approval_evidence_status"] == "blocked_missing_approval_record"
    assert "approval_record is required" in report["approval_blockers"]
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_single_shadow_approval_record_returns_readiness_compatible_evidence():
    tool = load_tool_module()
    readiness = load_readiness_module()

    report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
        approval_record=valid_approval_record(),
    )

    assert report["pcweb_id"] == "PCWEB-123"
    assert report["report_mode"] == "desktop_worker_mic_source_single_shadow_approval_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["manual_review_packet_validation_status"] == "passed"
    assert report["approval_record_validation_status"] == "passed"
    assert report["approval_evidence_status"] == "single_shadow_test_approval_evidence_ready"
    assert report["worker_mic_source_approval_packet_status"] == "ready_for_manual_review_not_executable"
    assert report["worker_mic_source_approval_status"] == "manually_approved_for_single_shadow_test"
    assert report["approved_connector_session_id"] == "worker_mic_connector_review"
    assert report["approval_scope"] == "single_user_real_mic_shadow_test_only"
    assert report["approved_to_execute_now"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    assert readiness._worker_mic_source_ready(report) is True


def test_approval_record_rejects_wrong_token_session_scope_and_unready_packet():
    tool = load_tool_module()
    wrong_token = valid_approval_record()
    wrong_token["approval_token"] = "APPROVE_REAL_WORKER_EXECUTION"
    wrong_session = valid_approval_record()
    wrong_session["approved_connector_session_id"] = "other-session"
    wrong_scope = valid_approval_record()
    wrong_scope["approval_scope"] = "always_allow"
    unready_packet = valid_manual_review_packet_report()
    unready_packet["worker_mic_source_approval_packet_status"] = "blocked_missing_required_evidence"

    token_report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
        approval_record=wrong_token,
    )
    session_report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
        approval_record=wrong_session,
    )
    scope_report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
        approval_record=wrong_scope,
    )
    packet_report = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=unready_packet,
        approval_record=valid_approval_record(),
    )

    assert "approval_token must match single-shadow-test approval token" in token_report[
        "approval_blockers"
    ]
    assert "approved_connector_session_id must match connector_session_id" in session_report[
        "approval_blockers"
    ]
    assert "approval_scope must be single_user_real_mic_shadow_test_only" in scope_report[
        "approval_blockers"
    ]
    assert packet_report["manual_review_packet_validation_status"] == "failed"
    assert packet_report["worker_mic_source_approval_status"] == "not_approved"
    assert packet_report["safe_to_spawn_worker_now"] is False


def test_single_shadow_approval_removes_only_worker_mic_source_readiness_blocker():
    tool = load_tool_module()
    readiness = load_readiness_module()
    worker_approval = tool.build_worker_mic_source_single_shadow_approval_report(
        policy_path=POLICY_PATH,
        manual_review_packet_report=valid_manual_review_packet_report(),
        approval_record=valid_approval_record(),
    )

    readiness_report = readiness.build_real_mic_shadow_test_readiness_report(
        worker_mic_source_approval_report=worker_approval
    )

    assert readiness_report["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert readiness_report["readiness_summary"]["worker_mic_source_ready"] is True
    assert "worker_mic_source_not_approved" not in readiness_report["blockers"]
    assert "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval" in (
        readiness_report["blockers"]
    )
    assert "mic_adapter_real_implementation_not_available" in readiness_report["blockers"]
    assert "asr_worker_real_mic_source_not_available" in readiness_report["blockers"]

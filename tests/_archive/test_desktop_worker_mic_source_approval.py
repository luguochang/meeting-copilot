import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "worker-mic-source-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_worker_mic_source_approval.py"


EXPECTED_COMMANDS = [
    ("runtime.get_status", "runtime_get_status"),
    ("session.prepare", "session_prepare"),
    ("asr_worker.health", "asr_worker_health"),
    ("mic_adapter.prepare", "mic_adapter_prepare"),
    ("mic_adapter.status", "mic_adapter_status"),
    ("mic_adapter.start", "mic_adapter_start"),
    ("mic_adapter.pause", "mic_adapter_pause"),
    ("mic_adapter.resume", "mic_adapter_resume"),
    ("mic_adapter.stop", "mic_adapter_stop"),
    ("mic_adapter.delete_audio_chunks", "mic_adapter_delete_audio_chunks"),
]

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


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_worker_mic_source_approval",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_connector_request() -> dict:
    return {
        "connector_version": "desktop_worker_mic_connector_contract.v1",
        "connector_id": "worker_mic_connector_review",
        "session_id": "worker_mic_connector_review",
        "adapter_id": "worker_mic_connector_review",
        "worker_id": "worker_mic_connector_review",
        "mic_source_kind": "mic",
        "worker_source_kind": "mic",
        "mic_command_id": "mic_adapter.start",
        "worker_command_id": "worker.prepare",
        "mic_current_state": "prepared",
        "worker_current_state": "not_prepared",
        "user_consent_state": "explicit_user_start_granted",
        "runtime_audio_root": "artifacts/tmp/desktop_mic_adapter_runtime",
        "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
        "worker_runtime_root": (
            "artifacts/tmp/desktop_asr_worker_runtime/worker-mic-connector-review"
        ),
        "worker_event_output_path": (
            "artifacts/tmp/asr_events/worker-mic-connector-review.events.json"
        ),
    }


def valid_tauri_noop_run_result() -> dict:
    return {
        "run_result_version": "desktop_tauri_noop_run_result.v1",
        "run_id": "worker_mic_connector_review",
        "run_environment": "tauri_webview",
        "explicit_tauri_run_approval_recorded": True,
        "web_app_url_status": "local_dev_url_loaded",
        "ipc_transport_status": "tauri_ipc_available",
        "command_results": [
            {
                "command_id": command_id,
                "command_name": command_name,
                "invoke_status": "returned",
                "result": {
                    "command_id": command_id,
                    "command_status": "noop_bound",
                    "implementation_status": "noop_only",
                    "transport_status": "tauri_ipc_bound",
                    "side_effect_status": "none",
                    "safe_to_invoke_noop": True,
                    "safe_to_execute_real_action": False,
                    "captures_audio": False,
                    "spawns_process": False,
                    "calls_remote_provider": False,
                    "writes_local_files": False,
                },
            }
            for command_id, command_name in EXPECTED_COMMANDS
        ],
    }


def test_worker_mic_source_approval_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-114"
    assert policy["policy_name"] == "Desktop Worker Mic Source Approval Packet"
    assert policy["policy_status"] == "desktop_worker_mic_source_approval_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["approval_mode"] == "manual_review_packet_only"
    assert policy["approval_scope"] == (
        "allow_worker_prepare_source_kind_mic_after_manual_approval"
    )
    assert policy["worker_mic_source_approval_status"] == "not_approved"
    assert policy["required_previous_contracts"] == ["PCWEB-112", "PCWEB-113"]
    assert policy["required_connector_status"] == "ready_for_worker_mic_connector_contract_review"
    assert policy["required_tauri_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_execute_tauri_worker_audio_network_or_models():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "cargo",
        "tauri dev",
        "npm",
        "pnpm",
        "yarn",
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


def test_default_report_requires_connector_and_tauri_noop_evidence():
    tool = load_tool_module()

    report = tool.build_worker_mic_source_approval_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-114"
    assert report["report_mode"] == "desktop_worker_mic_source_approval_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["connector_request_validation_status"] == "not_provided"
    assert report["tauri_result_validation_status"] == "not_provided"
    assert report["worker_mic_source_approval_packet_status"] == (
        "blocked_missing_required_evidence"
    )
    assert report["worker_mic_source_approval_status"] == "not_approved"
    assert report["next_required_decision"] == (
        "provide_connector_request_and_valid_tauri_noop_result_or_keep_blocked"
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_connector_and_tauri_noop_result_create_manual_packet_not_execution():
    tool = load_tool_module()

    report = tool.build_worker_mic_source_approval_report(
        policy_path=POLICY_PATH,
        connector_request=valid_connector_request(),
        tauri_run_result=valid_tauri_noop_run_result(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["connector_request_validation_status"] == "passed"
    assert report["tauri_result_validation_status"] == "passed"
    assert report["worker_mic_source_approval_packet_status"] == (
        "ready_for_manual_review_not_executable"
    )
    assert report["worker_mic_source_approval_status"] == "not_approved"
    assert report["approval_scope"] == (
        "allow_worker_prepare_source_kind_mic_after_manual_approval"
    )
    assert report["connector_session_id"] == "worker_mic_connector_review"
    assert report["tauri_run_id"] == "worker_mic_connector_review"
    assert report["worker_source_kind"] == "mic"
    assert report["worker_command_blocker"] == "source_kind requires later approval: mic"
    assert report["real_tauri_noop_run_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert report["manual_review_packet"] == {
        "approval_scope": "allow_worker_prepare_source_kind_mic_after_manual_approval",
        "connector_status": "ready_for_worker_mic_connector_contract_review",
        "tauri_noop_run_result_status": "validated_noop_ipc_observed",
        "worker_prepare_source_kind": "mic",
        "worker_prepare_blocker_to_remove_later": "source_kind requires later approval: mic",
        "execution_status_after_packet": "still_not_executable",
        "next_required_decision": "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked",
    }
    assert report["next_required_decision"] == (
        "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked"
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_approval_blocks_failed_tauri_result_or_failed_connector_request():
    tool = load_tool_module()
    failed_result = valid_tauri_noop_run_result()
    failed_result["command_results"][0]["invoke_status"] = "failed"
    no_consent_connector = valid_connector_request()
    no_consent_connector["user_consent_state"] = "not_requested"

    tauri_blocked_report = tool.build_worker_mic_source_approval_report(
        policy_path=POLICY_PATH,
        connector_request=valid_connector_request(),
        tauri_run_result=failed_result,
    )
    connector_blocked_report = tool.build_worker_mic_source_approval_report(
        policy_path=POLICY_PATH,
        connector_request=no_consent_connector,
        tauri_run_result=valid_tauri_noop_run_result(),
    )

    assert tauri_blocked_report["tauri_result_validation_status"] == "failed"
    assert tauri_blocked_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_tauri_noop_result_validation"
    )
    assert "command runtime.get_status invoke_status must be returned" in (
        tauri_blocked_report["approval_blockers"]
    )
    assert connector_blocked_report["connector_request_validation_status"] == "failed"
    assert connector_blocked_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_connector_request_validation"
    )
    assert "mic_adapter.start requires user_consent_state=explicit_user_start_granted" in (
        connector_blocked_report["approval_blockers"]
    )
    assert tauri_blocked_report["safe_to_capture_audio_now"] is False
    assert connector_blocked_report["safe_to_spawn_worker_now"] is False


def test_approval_blocks_cross_session_evidence_and_forbidden_paths_without_echoing_values():
    tool = load_tool_module()
    mismatched_result = valid_tauri_noop_run_result()
    mismatched_result["run_id"] = "other_tauri_run"
    unsafe_connector = valid_connector_request()
    unsafe_connector["audio_chunk_root"] = "data/asr_eval/local_samples/private"

    mismatch_report = tool.build_worker_mic_source_approval_report(
        policy_path=POLICY_PATH,
        connector_request=valid_connector_request(),
        tauri_run_result=mismatched_result,
    )
    unsafe_report = tool.build_worker_mic_source_approval_report(
        policy_path=POLICY_PATH,
        connector_request=unsafe_connector,
        tauri_run_result=valid_tauri_noop_run_result(),
    )

    assert mismatch_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_cross_session_evidence"
    )
    assert "tauri run_id must match connector session_id" in mismatch_report[
        "approval_blockers"
    ]
    assert unsafe_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_connector_request_validation"
    )
    assert unsafe_report["audio_chunk_root"] == "<redacted_invalid_path>"
    unsafe_json = json.dumps(unsafe_report, ensure_ascii=False)
    assert "data/asr_eval/local_samples/private" not in unsafe_json


def test_custom_policy_cannot_enable_worker_mic_source_or_audio_side_effects(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_accept_worker_mic_source_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["approved_to_execute_now"] = True
    policy_path = tmp_path / "worker-mic-source-approval.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_worker_mic_source_approval_report(policy_path=policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_policy_validation"
    )
    assert "safe_to_accept_worker_mic_source_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "approved_to_execute_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_accept_worker_mic_source_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_policy_path_rejects_forbidden_roots_before_reading():
    tool = load_tool_module()
    forbidden_policy_path = REPO_ROOT / "configs" / "local" / "worker-mic-source-approval.policy.json"

    report = tool.build_worker_mic_source_approval_report(
        policy_path=forbidden_policy_path,
    )

    assert report["policy_validation_status"] == "blocked_by_policy_path_guard"
    assert report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_policy_path_guard"
    )
    assert report["policy_validation_errors"] == ["policy path is blocked: configs/local"]
    assert report["safe_to_read_configs_local_now"] is False

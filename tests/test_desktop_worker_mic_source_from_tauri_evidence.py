import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "worker-mic-source-from-tauri-evidence.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_worker_mic_source_from_tauri_evidence.py"


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
        "desktop_worker_mic_source_from_tauri_evidence",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_run_result(run_id: str = "tauri-noop-webview-2026-07-03T13-15-25-808Z") -> dict:
    return {
        "run_result_version": "desktop_tauri_noop_run_result.v1",
        "run_id": run_id,
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


def valid_pcweb119_evidence(
    run_id: str = "tauri-noop-webview-2026-07-03T13-15-25-808Z",
) -> dict:
    return {
        "capture_version": "desktop_tauri_noop_webview_run_capture.v1",
        "capture_status": "captured_validated_tauri_noop_run",
        "source_endpoint": "/desktop/tauri-noop-run-results/validations",
        "run_id": run_id,
        "run_result": valid_run_result(run_id),
        "validation_report": {
            "result_validation_status": "passed",
            "tauri_noop_run_result_status": "validated_noop_ipc_observed",
            "real_tauri_noop_run_evidence_status": (
                "ready_for_worker_mic_source_approval_review"
            ),
            "validated_command_count": 10,
            "returned_command_count": 10,
            "failed_command_count": 0,
        },
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
    }


def test_pcweb120_policy_exists_and_keeps_real_execution_blocked():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-120"
    assert policy["policy_name"] == "Desktop Worker Mic Source From Tauri Evidence"
    assert policy["policy_status"] == (
        "desktop_worker_mic_source_from_tauri_evidence_policy_only"
    )
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["evidence_source_kind"] == "pcweb_119_capture_evidence_json"
    assert policy["approval_mode"] == "manual_review_packet_from_real_tauri_evidence_only"
    assert policy["required_previous_contracts"] == ["PCWEB-112", "PCWEB-114", "PCWEB-119"]
    assert policy["allowed_evidence_root"] == "artifacts/tmp/desktop_tauri_noop_run_results"
    assert policy["connector_consent_scope"] == "tauri_noop_ipc_only_not_real_audio_capture"
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


def test_valid_pcweb119_evidence_builds_same_session_manual_packet_not_execution():
    tool = load_tool_module()

    report = tool.build_worker_mic_source_from_tauri_evidence_report(
        policy_path=POLICY_PATH,
        tauri_evidence=valid_pcweb119_evidence(),
    )

    assert report["pcweb_id"] == "PCWEB-120"
    assert report["policy_validation_status"] == "passed"
    assert report["tauri_evidence_validation_status"] == "passed"
    assert report["tauri_evidence_status"] == "captured_validated_tauri_noop_run"
    assert report["tauri_run_id"] == "tauri-noop-webview-2026-07-03T13-15-25-808Z"
    assert report["connector_session_id"] == report["tauri_run_id"]
    assert report["derived_connector_request_status"] == (
        "derived_same_session_connector_request"
    )
    assert report["connector_consent_scope"] == "tauri_noop_ipc_only_not_real_audio_capture"
    assert report["worker_mic_source_approval_packet_status"] == (
        "ready_for_manual_review_not_executable"
    )
    assert report["worker_mic_source_approval_status"] == "not_approved"
    assert report["real_tauri_noop_run_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert report["manual_review_packet"]["worker_prepare_source_kind"] == "mic"
    assert report["manual_review_packet"]["execution_status_after_packet"] == (
        "still_not_executable"
    )
    assert report["next_required_decision"] == (
        "manual_approve_worker_prepare_source_kind_mic_or_keep_blocked"
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_pcweb120_blocks_invalid_or_browser_fallback_evidence_without_manual_packet():
    tool = load_tool_module()
    browser_fallback = valid_pcweb119_evidence()
    browser_fallback["run_result"]["run_environment"] = "browser_fallback"
    failed_validation = valid_pcweb119_evidence()
    failed_validation["validation_report"]["result_validation_status"] = "failed"

    browser_report = tool.build_worker_mic_source_from_tauri_evidence_report(
        policy_path=POLICY_PATH,
        tauri_evidence=browser_fallback,
    )
    failed_report = tool.build_worker_mic_source_from_tauri_evidence_report(
        policy_path=POLICY_PATH,
        tauri_evidence=failed_validation,
    )

    assert browser_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_tauri_evidence_validation"
    )
    assert "run_result.run_environment must be tauri_webview" in browser_report[
        "tauri_evidence_validation_errors"
    ]
    assert browser_report["manual_review_packet"] is None
    assert failed_report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_tauri_evidence_validation"
    )
    assert "validation_report.result_validation_status must be passed" in failed_report[
        "tauri_evidence_validation_errors"
    ]
    assert failed_report["safe_to_capture_audio_now"] is False


def test_pcweb120_blocks_forbidden_evidence_path_before_reading():
    tool = load_tool_module()

    report = tool.build_worker_mic_source_from_tauri_evidence_report(
        policy_path=POLICY_PATH,
        tauri_evidence_path=REPO_ROOT / "configs" / "local" / "pcweb119.json",
    )

    assert report["tauri_evidence_read_status"] == "blocked_by_path_guard"
    assert report["worker_mic_source_approval_packet_status"] == (
        "blocked_by_tauri_evidence_path_guard"
    )
    assert "path is blocked: configs/local" in report["tauri_evidence_validation_errors"]
    assert report["safe_to_read_configs_local_now"] is False

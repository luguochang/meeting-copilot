import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "tauri-noop-run-result-intake.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_tauri_noop_run_result_intake.py"


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
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_spawn_process_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_tauri_noop_run_result_intake",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_run_result() -> dict:
    return {
        "run_result_version": "desktop_tauri_noop_run_result.v1",
        "run_id": "tauri_noop_run_review",
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


def test_tauri_noop_run_result_intake_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-113"
    assert policy["policy_name"] == "Desktop Tauri No-op Run Result Intake"
    assert policy["policy_status"] == "desktop_tauri_noop_run_result_intake_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["result_intake_mode"] == "caller_provided_tauri_noop_result_validation_only"
    assert policy["accepted_result_source"] == "caller_provided_json_only"
    assert policy["tauri_run_execution_status"] == "not_run_by_intake"
    assert policy["external_command_execution_status"] == "not_run"
    assert policy["required_previous_contracts"] == ["PCWEB-091", "PCWEB-107", "PCWEB-109", "PCWEB-112"]
    assert policy["expected_noop_commands"] == [command_name for _, command_name in EXPECTED_COMMANDS]
    assert policy["expected_bridge_command_ids"] == [command_id for command_id, _ in EXPECTED_COMMANDS]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_execute_tauri_cargo_audio_network_or_models():
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


def test_default_report_accepts_no_result_but_does_not_run_anything():
    tool = load_tool_module()

    report = tool.build_tauri_noop_run_result_intake_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-113"
    assert report["report_mode"] == "desktop_tauri_noop_run_result_intake_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["result_validation_status"] == "not_provided"
    assert report["tauri_noop_run_result_status"] == "no_result_provided"
    assert report["tauri_run_execution_status"] == "not_run_by_intake"
    assert report["external_command_execution_status"] == "not_run"
    assert report["validated_command_count"] == 0
    assert report["next_required_decision"] == "perform_explicit_real_tauri_noop_run_or_keep_blocked"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_tauri_noop_run_result_is_normalized_for_worker_mic_source_review():
    tool = load_tool_module()

    report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=valid_run_result(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["result_validation_status"] == "passed"
    assert report["result_validation_errors"] == []
    assert report["tauri_noop_run_result_status"] == "validated_noop_ipc_observed"
    assert report["real_tauri_noop_run_evidence_status"] == (
        "ready_for_worker_mic_source_approval_review"
    )
    assert report["validated_command_count"] == len(EXPECTED_COMMANDS)
    assert report["returned_command_count"] == len(EXPECTED_COMMANDS)
    assert report["failed_command_count"] == 0
    assert report["normalized_command_results"] == [
        {
            "command_id": command_id,
            "command_name": command_name,
            "invoke_status": "returned",
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
        }
        for command_id, command_name in EXPECTED_COMMANDS
    ]
    assert report["next_required_decision"] == "review_worker_mic_source_approval_packet"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_result_blocks_missing_failed_extra_or_side_effecting_commands():
    tool = load_tool_module()
    missing = valid_run_result()
    missing["command_results"] = missing["command_results"][:-1]
    failed = valid_run_result()
    failed["command_results"][0]["invoke_status"] = "failed"
    failed["command_results"][0]["error_message"] = "invoke failed"
    extra = valid_run_result()
    extra["command_results"].append(
        {
            "command_id": "audio.capture_start",
            "command_name": "audio_capture_start",
            "invoke_status": "returned",
            "result": {},
        }
    )
    side_effect = valid_run_result()
    side_effect["command_results"][3]["result"]["captures_audio"] = True

    missing_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=missing,
    )
    failed_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=failed,
    )
    extra_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=extra,
    )
    side_effect_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=side_effect,
    )

    assert missing_report["result_validation_status"] == "failed"
    assert "command_results must contain exactly the expected no-op command ids" in (
        missing_report["result_validation_errors"]
    )
    assert failed_report["result_validation_status"] == "failed"
    assert "command runtime.get_status invoke_status must be returned" in failed_report[
        "result_validation_errors"
    ]
    assert extra_report["result_validation_status"] == "failed"
    assert "command_results must contain exactly the expected no-op command ids" in extra_report[
        "result_validation_errors"
    ]
    assert side_effect_report["result_validation_status"] == "failed"
    assert "command mic_adapter.prepare captures_audio must be false" in side_effect_report[
        "result_validation_errors"
    ]
    assert side_effect_report["safe_to_capture_audio_now"] is False


def test_result_rejects_raw_paths_secrets_unknown_fields_without_echoing_values():
    tool = load_tool_module()
    unsafe_result = valid_run_result()
    unsafe_result["stdout"] = "raw Tauri output /private/tmp/local-audio-secret SECRET_TOKEN"
    unsafe_result["cwd"] = "/Users/chase/Documents/面试/meeting-copilot"
    unsafe_result["api_key"] = "sk-should-not-echo"
    unsafe_result["unexpected"] = "Bearer SECRET_TOKEN_123456"
    unsafe_result["command_results"][0]["result"]["message"] = (
        "No real desktop action /private/tmp/local-audio-secret"
    )

    report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        run_result=unsafe_result,
    )

    assert report["result_validation_status"] == "failed"
    assert set(report["result_validation_errors"]) >= {
        "forbidden raw result field present: stdout",
        "forbidden raw result field present: cwd",
        "forbidden raw result field present: api_key",
        "unknown result field present",
        "command runtime.get_status result contains unsupported field: message",
    }
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/private/tmp/local-audio-secret" not in report_json
    assert "sk-should-not-echo" not in report_json
    assert "SECRET_TOKEN" not in report_json
    assert "Bearer" not in report_json


def test_result_path_must_be_under_approved_artifacts_root(tmp_path):
    tool = load_tool_module()
    safe_root = REPO_ROOT / "artifacts" / "tmp" / "desktop_tauri_noop_run_results"
    safe_root.mkdir(parents=True, exist_ok=True)
    safe_result_path = safe_root / "tauri-noop-run-review.json"
    safe_result_path.write_text(json.dumps(valid_run_result()), encoding="utf-8")

    safe_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        result_path=safe_result_path,
    )

    assert safe_report["result_read_status"] == "read"
    assert safe_report["result_validation_status"] == "passed"
    assert str(safe_result_path) not in json.dumps(safe_report)

    unsafe_result_path = tmp_path / "tauri-noop-run-review.json"
    unsafe_result_path.write_text(json.dumps(valid_run_result()), encoding="utf-8")
    blocked_report = tool.build_tauri_noop_run_result_intake_report(
        policy_path=POLICY_PATH,
        result_path=unsafe_result_path,
    )

    assert blocked_report["result_read_status"] == "blocked"
    assert blocked_report["result_validation_status"] == "blocked_by_result_path_guard"
    assert blocked_report["tauri_noop_run_result_status"] == "blocked_by_result_path_guard"

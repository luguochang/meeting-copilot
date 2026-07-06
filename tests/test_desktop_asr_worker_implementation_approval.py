import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-implementation-approval.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_implementation_approval.py"

EXPECTED_FALSE_FLAGS = [
    "safe_to_implement_worker_now",
    "safe_to_execute_worker_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_modelscope_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
]

REQUIRED_APPROVAL_TOKENS = [
    "pcweb_101_worker_implementation_design_reviewed",
    "no_mic_no_real_audio_ack",
    "no_remote_asr_no_llm_ack",
    "no_model_download_ack",
    "no_tauri_cargo_run_ack",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_implementation_approval",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_approval_packet() -> dict:
    return {
        "packet_version": "desktop_asr_worker_implementation_approval.v1",
        "provider_mode": "sherpa_onnx_streaming",
        "source_kind": "synthetic",
        "future_worker_entrypoint": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "future_command_runner": "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs",
        "event_output_root": "artifacts/tmp/asr_events",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime",
        "max_runtime_seconds": 1800,
        "max_memory_mb": 2048,
        "max_cpu_percent": 250,
        "approval_tokens": REQUIRED_APPROVAL_TOKENS,
        "allow_remote_asr": False,
        "allow_llm": False,
        "allow_model_download": False,
        "allow_mic": False,
        "allow_real_audio": False,
        "allow_tauri_cargo_run": False,
    }


def test_implementation_approval_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-101"
    assert policy["policy_name"] == "Desktop ASR Worker Implementation Approval Packet"
    assert policy["policy_status"] == "desktop_asr_worker_implementation_approval_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-098", "PCWEB-099", "PCWEB-100"]
    assert policy["approval_mode"] == "approval_packet_preview_only"
    assert policy["implementation_status"] == "not_implemented"
    assert policy["worker_execution_status"] == "not_executed"
    assert policy["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["allowed_provider_modes_now"] == ["mock_streaming", "sherpa_onnx_streaming"]
    assert policy["future_provider_modes_requiring_approval"] == ["funasr_streaming"]
    assert policy["forbidden_provider_modes"] == ["remote_asr", "remote_llm_asr"]
    assert policy["allowed_source_kinds_now"] == ["synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file", "system_audio"]
    assert policy["required_approval_tokens"] == REQUIRED_APPROVAL_TOKENS
    assert policy["implementation_boundaries"] == [
        "worker_command_runner_contract",
        "sidecar_process_launcher_contract",
        "event_writer_contract",
        "resource_budget_contract",
        "cleanup_contract",
        "provider_adapter_contract",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_spawn_processes_or_access_audio_or_models():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "modelscope",
        "AutoModel",
        "requests.",
        "urllib.request",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_default_report_requires_implementation_approval_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-101"
    assert report["report_mode"] == "desktop_asr_worker_implementation_approval_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["approval_packet_validation_status"] == "not_provided"
    assert report["implementation_approval_status"] == "implementation_approval_required"
    assert report["implementation_packet_status"] == "not_ready"
    assert report["implementation_status"] == "not_implemented"
    assert report["worker_execution_status"] == "not_executed"
    assert report["required_previous_contracts"] == ["PCWEB-098", "PCWEB-099", "PCWEB-100"]
    assert report["required_approval_tokens"] == REQUIRED_APPROVAL_TOKENS
    assert report["next_action"] == "submit_bounded_worker_implementation_approval_packet"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_approval_packet_is_ready_for_manual_review_but_still_not_executable():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=POLICY_PATH,
        approval_packet=valid_approval_packet(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["approval_packet_validation_status"] == "passed"
    assert report["implementation_approval_status"] == "ready_for_manual_review_not_executable"
    assert report["implementation_packet_status"] == "preview_ready"
    assert report["provider_mode"] == "sherpa_onnx_streaming"
    assert report["source_kind"] == "synthetic"
    assert report["future_worker_entrypoint"] == "code/asr_runtime/scripts/asr_worker_sidecar.py"
    assert report["future_command_runner"] == (
        "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
    )
    assert report["event_output_root"] == "artifacts/tmp/asr_events"
    assert report["runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert report["resource_budget"] == {
        "max_runtime_seconds": 1800,
        "max_memory_mb": 2048,
        "max_cpu_percent": 250,
    }
    assert report["approval_tokens_status"] == "all_required_tokens_present"
    assert report["approved_to_implement_now"] is False
    assert report["approved_to_execute_now"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_approval_packet_rejects_mic_source_remote_asr_and_model_download():
    tool = load_tool_module()
    packet = valid_approval_packet()
    packet["source_kind"] = "mic"
    packet["provider_mode"] = "remote_asr"
    packet["allow_remote_asr"] = True
    packet["allow_model_download"] = True
    packet["allow_mic"] = True

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=POLICY_PATH,
        approval_packet=packet,
    )

    assert report["approval_packet_validation_status"] == "failed"
    assert report["implementation_approval_status"] == "blocked_by_approval_packet_validation"
    assert "source_kind requires later approval: mic" in report["approval_packet_validation_errors"]
    assert "provider_mode is forbidden: remote_asr" in report["approval_packet_validation_errors"]
    assert "allow_remote_asr must be false" in report["approval_packet_validation_errors"]
    assert "allow_model_download must be false" in report["approval_packet_validation_errors"]
    assert "allow_mic must be false" in report["approval_packet_validation_errors"]
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_download_models_now"] is False


def test_approval_packet_rejects_funasr_until_local_model_or_drv019_approval():
    tool = load_tool_module()
    packet = valid_approval_packet()
    packet["provider_mode"] = "funasr_streaming"

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=POLICY_PATH,
        approval_packet=packet,
    )

    assert report["approval_packet_validation_status"] == "failed"
    assert "provider_mode requires later approval: funasr_streaming" in report[
        "approval_packet_validation_errors"
    ]
    assert report["next_action"] == "fix_approval_packet_or_seek_required_approval"
    assert report["safe_to_download_models_now"] is False


def test_approval_packet_rejects_forbidden_repo_outside_and_symlink_paths(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path
    approved_runtime_root = repo_root / "artifacts" / "tmp" / "desktop_asr_worker_runtime"
    forbidden_root = repo_root / "configs" / "local"
    approved_runtime_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    runtime_symlink = approved_runtime_root / "linked-runtime"
    runtime_symlink.symlink_to(forbidden_root)

    packet = valid_approval_packet()
    packet["runtime_root"] = str(runtime_symlink)
    packet["event_output_root"] = "/tmp/outside-asr-events"
    packet["future_worker_entrypoint"] = "../outside_worker.py"
    packet["future_command_runner"] = "configs/local/runner.rs"

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=POLICY_PATH,
        approval_packet=packet,
        repo_root=repo_root,
    )

    assert report["approval_packet_validation_status"] == "failed"
    assert report["runtime_root"] == "<redacted_invalid_path>"
    assert report["event_output_root"] == "<redacted_invalid_path>"
    assert report["future_worker_entrypoint"] == "<redacted_invalid_path>"
    assert report["future_command_runner"] == "<redacted_invalid_path>"
    assert "runtime_root is blocked: configs/local" in report[
        "approval_packet_validation_errors"
    ]
    assert "event_output_root is outside repository" in report[
        "approval_packet_validation_errors"
    ]
    assert "future_worker_entrypoint is not under approved code root" in report[
        "approval_packet_validation_errors"
    ]
    assert "future_command_runner is blocked: configs/local" in report[
        "approval_packet_validation_errors"
    ]
    assert str(runtime_symlink) not in json.dumps(report, ensure_ascii=False)


def test_custom_policy_cannot_enable_implementation_execution_or_audio(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_implement_worker_now"] = True
    policy["safe_to_execute_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy_path = tmp_path / "bad-policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_implementation_approval_report(
        policy_path=policy_path,
        approval_packet=valid_approval_packet(),
    )

    assert report["policy_validation_status"] == "failed"
    assert report["implementation_approval_status"] == "blocked_by_policy_validation"
    assert "safe_to_implement_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_execute_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_implement_worker_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_cli_returns_nonzero_for_blocked_approval_packet(capsys):
    tool = load_tool_module()
    packet = valid_approval_packet()
    packet["source_kind"] = "mic"

    exit_code = tool.main(
        [
            "--approval-packet-json",
            json.dumps(packet),
        ],
        stdout=io.StringIO(),
    )

    assert exit_code == 1

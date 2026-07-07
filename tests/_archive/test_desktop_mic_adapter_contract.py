import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "mic-adapter-contract.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_mic_adapter_contract.py"

EXPECTED_COMMAND_IDS = [
    "mic_adapter.prepare",
    "mic_adapter.status",
    "mic_adapter.start",
    "mic_adapter.pause",
    "mic_adapter.resume",
    "mic_adapter.stop",
    "mic_adapter.delete_audio_chunks",
]

EXPECTED_FALSE_FLAGS = [
    "safe_to_bind_mic_adapter_now",
    "safe_to_accept_mic_command_now",
    "safe_to_execute_mic_command_now",
    "safe_to_select_input_device_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_pause_recording_now",
    "safe_to_resume_recording_now",
    "safe_to_stop_recording_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_mic_adapter_contract",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_status_request() -> dict:
    return {
        "contract_version": "desktop_mic_adapter_contract.v1",
        "command_id": "mic_adapter.status",
        "request_id": "desktop_mic_adapter_status_001",
        "session_id": "desktop_mic_adapter_contract_review",
        "adapter_id": "desktop_mic_adapter_contract_review",
        "source_kind": "mic",
        "current_state": "not_prepared",
        "requested_state_after": "unchanged",
        "runtime_audio_root": "artifacts/tmp/desktop_mic_adapter_runtime",
        "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
        "user_consent_state": "not_requested",
    }


def test_mic_adapter_contract_policy_exists_and_blocks_capture():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-105"
    assert policy["policy_name"] == "Desktop Microphone Adapter Contract"
    assert policy["policy_status"] == "desktop_mic_adapter_contract_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-104", "DRV-032"]
    assert policy["contract_mode"] == "mic_adapter_command_contract_only"
    assert policy["contract_version"] == "desktop_mic_adapter_contract.v1"
    assert policy["adapter_execution_status"] == "not_bound_not_executed"
    assert policy["permission_request_status"] == "not_requested"
    assert policy["user_start_boundary"] == "explicit_user_start_required_before_capture"
    assert policy["approved_runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert policy["approved_audio_chunk_root"] == (
        "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    )
    assert policy["delete_semantics"] == "delete_audio_chunks_before_session_discard"
    assert [item["command_id"] for item in policy["command_transition_catalog"]] == (
        EXPECTED_COMMAND_IDS
    )
    assert all(
        item["safe_to_execute_now"] is False
        for item in policy["command_transition_catalog"]
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


def test_tool_source_does_not_access_microphone_process_network_or_models():
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
        "cpal",
        "rodio",
        "requests.",
        "urllib.request",
        "modelscope",
        "AutoModel",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_default_report_specifies_mic_adapter_contract_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_mic_adapter_contract_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-105"
    assert report["report_mode"] == "desktop_mic_adapter_contract_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["mic_command_request_validation_status"] == "not_provided"
    assert report["mic_adapter_contract_status"] == "specified_not_executable"
    assert report["adapter_execution_status"] == "not_bound_not_executed"
    assert report["permission_request_status"] == "not_requested"
    assert report["audio_capture_status"] == "not_started"
    assert report["audio_chunk_write_status"] == "not_written"
    assert report["audio_chunk_delete_status"] == "not_executed"
    assert report["mic_command_response_preview"] is None
    assert [item["command_id"] for item in report["command_transition_catalog"]] == (
        EXPECTED_COMMAND_IDS
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_status_request_returns_blocked_preview_without_audio_access():
    tool = load_tool_module()

    report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=valid_status_request(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["mic_command_request_validation_status"] == "passed"
    assert report["mic_adapter_contract_status"] == "ready_for_mic_adapter_contract_review"
    assert report["runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert report["audio_chunk_root"] == "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    assert report["mic_command_response_preview"] == {
        "contract_version": "desktop_mic_adapter_contract.v1",
        "request_id": "desktop_mic_adapter_status_001",
        "command_id": "mic_adapter.status",
        "accepted": False,
        "status": "validated_not_executed",
        "adapter_lifecycle_status": "unchanged_not_executed",
        "current_state": "not_prepared",
        "requested_state_after": "unchanged",
        "runtime_audio_root": "artifacts/tmp/desktop_mic_adapter_runtime",
        "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
        "errors": [],
    }
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_request_audio_permission_now"] is False
    assert report["safe_to_write_audio_chunk_now"] is False


def test_start_request_requires_explicit_user_start_but_still_does_not_execute():
    tool = load_tool_module()
    start_request = valid_status_request()
    start_request["command_id"] = "mic_adapter.start"
    start_request["current_state"] = "prepared"
    start_request["requested_state_after"] = "recording"

    blocked_report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=start_request,
    )

    assert blocked_report["mic_command_request_validation_status"] == "failed"
    assert blocked_report["mic_adapter_contract_status"] == "blocked_by_mic_command_request_validation"
    assert "mic_adapter.start requires user_consent_state=explicit_user_start_granted" in blocked_report[
        "mic_command_request_validation_errors"
    ]
    assert blocked_report["safe_to_capture_audio_now"] is False

    start_request["user_consent_state"] = "explicit_user_start_granted"
    preview_report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=start_request,
    )

    assert preview_report["mic_command_request_validation_status"] == "passed"
    assert preview_report["mic_command_response_preview"]["accepted"] is False
    assert preview_report["mic_command_response_preview"]["status"] == "validated_not_executed"
    assert preview_report["safe_to_start_recording_now"] is False
    assert preview_report["safe_to_capture_audio_now"] is False


def test_command_request_rejects_forbidden_roots_and_file_or_system_audio_sources():
    tool = load_tool_module()
    forbidden_request = valid_status_request()
    forbidden_request["audio_chunk_root"] = "data/asr_eval/local_samples/private"
    file_request = valid_status_request()
    file_request["source_kind"] = "file"
    system_audio_request = valid_status_request()
    system_audio_request["source_kind"] = "system_audio"

    forbidden_report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=forbidden_request,
    )
    file_report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=file_request,
    )
    system_audio_report = tool.build_desktop_mic_adapter_contract_report(
        policy_path=POLICY_PATH,
        mic_command_request=system_audio_request,
    )

    assert forbidden_report["mic_command_request_validation_status"] == "failed"
    assert "audio_chunk_root is blocked: data/asr_eval/local_samples" in forbidden_report[
        "mic_command_request_validation_errors"
    ]
    assert forbidden_report["audio_chunk_root"] == "<redacted_invalid_path>"
    assert "source_kind is forbidden before separate approval: file" in file_report[
        "mic_command_request_validation_errors"
    ]
    assert "source_kind is forbidden before separate approval: system_audio" in system_audio_report[
        "mic_command_request_validation_errors"
    ]
    assert file_report["safe_to_read_user_audio_now"] is False
    assert system_audio_report["safe_to_capture_audio_now"] is False


def test_cli_rejects_policy_path_under_configs_local_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("policy file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)
    out = io.StringIO()

    exit_code = tool.main(
        ["--policy", "configs/local/mic-adapter-contract.policy.json"],
        out=out,
    )

    assert exit_code == 1
    report = json.loads(out.getvalue())
    assert report["policy_validation_status"] == "failed"
    assert "policy_path is blocked: configs/local" in report["policy_validation_errors"]
    assert report["safe_to_capture_audio_now"] is False

import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-command-protocol.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_command_protocol.py"

EXPECTED_FALSE_FLAGS = [
    "safe_to_execute_worker_command_now",
    "safe_to_accept_command_now",
    "safe_to_run_subprocess_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_check_worker_health_now",
    "safe_to_bind_worker_command_transport_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
]

EXPECTED_COMMAND_IDS = [
    "worker.prepare",
    "worker.start",
    "worker.stop",
    "worker.health",
    "worker.collect_events",
    "worker.cleanup",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_command_protocol",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_prepare_request() -> dict:
    return {
        "protocol_version": "desktop_asr_worker_command_protocol.v1",
        "command_id": "worker.prepare",
        "request_id": "desktop_worker_command_prepare_001",
        "session_id": "desktop_worker_command_protocol_review",
        "worker_id": "desktop_worker_command_protocol_review",
        "source_kind": "synthetic",
        "current_state": "not_prepared",
        "requested_state_after": "prepared",
        "event_output_path": "artifacts/tmp/asr_events/desktop-worker-command-protocol.events.json",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-command-protocol",
    }


def test_worker_command_protocol_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-099"
    assert policy["policy_name"] == "Desktop ASR Worker Command Protocol"
    assert policy["policy_status"] == "desktop_asr_worker_command_protocol_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contract"] == "PCWEB-098"
    assert policy["protocol_mode"] == "command_envelope_contract_only"
    assert policy["protocol_version"] == "desktop_asr_worker_command_protocol.v1"
    assert policy["worker_command_execution_status"] == "not_executed"
    assert policy["transition_preview_status"] == "specified_not_executed"
    assert policy["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["allowed_source_kinds_now"] == ["preflight_only", "synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file", "system_audio"]
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
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_default_report_specifies_command_protocol_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-099"
    assert report["report_mode"] == "desktop_asr_worker_command_protocol_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["command_request_validation_status"] == "not_provided"
    assert report["command_protocol_status"] == "specified_not_executable"
    assert report["worker_command_execution_status"] == "not_executed"
    assert report["transition_preview_status"] == "specified_not_executed"
    assert report["command_response_preview"] is None
    assert [item["command_id"] for item in report["command_transition_catalog"]] == (
        EXPECTED_COMMAND_IDS
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_prepare_request_returns_response_preview_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=POLICY_PATH,
        command_request=valid_prepare_request(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["command_request_validation_status"] == "passed"
    assert report["command_protocol_status"] == "ready_for_command_protocol_review"
    assert report["event_output_path"] == (
        "artifacts/tmp/asr_events/desktop-worker-command-protocol.events.json"
    )
    assert report["runtime_root"] == (
        "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-command-protocol"
    )
    assert report["command_response_preview"] == {
        "protocol_version": "desktop_asr_worker_command_protocol.v1",
        "request_id": "desktop_worker_command_prepare_001",
        "command_id": "worker.prepare",
        "accepted": False,
        "status": "validated_not_executed",
        "worker_lifecycle_status": "unchanged_not_executed",
        "current_state": "not_prepared",
        "requested_state_after": "prepared",
        "event_output_path": "artifacts/tmp/asr_events/desktop-worker-command-protocol.events.json",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-command-protocol",
        "errors": [],
    }
    assert report["safe_to_execute_worker_command_now"] is False
    assert report["safe_to_spawn_worker_now"] is False
    assert report["safe_to_write_event_file_now"] is False
    assert report["safe_to_mutate_web_session_now"] is False


def test_command_request_rejects_mic_source_until_adapter_is_approved():
    tool = load_tool_module()
    command_request = valid_prepare_request()
    command_request["source_kind"] = "mic"

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=POLICY_PATH,
        command_request=command_request,
    )

    assert report["command_request_validation_status"] == "failed"
    assert report["command_protocol_status"] == "blocked_by_command_request_validation"
    assert "source_kind requires later approval: mic" in report[
        "command_request_validation_errors"
    ]
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_execute_worker_command_now"] is False


def test_command_request_rejects_invalid_lifecycle_transition():
    tool = load_tool_module()
    command_request = valid_prepare_request()
    command_request["command_id"] = "worker.start"
    command_request["current_state"] = "not_prepared"
    command_request["requested_state_after"] = "running"

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=POLICY_PATH,
        command_request=command_request,
    )

    assert report["command_request_validation_status"] == "failed"
    assert report["command_protocol_status"] == "blocked_by_command_request_validation"
    assert "worker.start requires current_state in ['prepared']" in report[
        "command_request_validation_errors"
    ]
    assert report["command_response_preview"] is None


def test_command_request_rejects_forbidden_or_repo_outside_paths_and_redacts(tmp_path):
    tool = load_tool_module()
    approved_event_dir = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_dir = tmp_path / "configs" / "local"
    approved_event_dir.mkdir(parents=True)
    forbidden_dir.mkdir(parents=True)
    linked_event_path = approved_event_dir / "linked.events.json"
    linked_event_path.symlink_to(forbidden_dir / "private.events.json")

    command_request = valid_prepare_request()
    command_request["event_output_path"] = str(linked_event_path)
    command_request["runtime_root"] = "/tmp/outside-worker-runtime"

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=POLICY_PATH,
        command_request=command_request,
        repo_root=tmp_path,
    )

    assert report["command_request_validation_status"] == "failed"
    assert report["command_protocol_status"] == "blocked_by_command_request_validation"
    assert report["event_output_path"] == "<redacted_invalid_path>"
    assert report["runtime_root"] == "<redacted_invalid_path>"
    assert "event_output_path is blocked: configs/local" in report[
        "command_request_validation_errors"
    ]
    assert "runtime_root is outside repository" in report["command_request_validation_errors"]
    assert str(linked_event_path) not in json.dumps(report, ensure_ascii=False)


def test_custom_policy_cannot_enable_command_execution_audio_remote_or_model_download(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_execute_worker_command_now"] = True
    policy["safe_to_accept_command_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy_path = tmp_path / "asr-worker-command-protocol.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_command_protocol_report(
        policy_path=policy_path,
        command_request=valid_prepare_request(),
    )

    assert report["policy_validation_status"] == "failed"
    assert report["command_protocol_status"] == "blocked_by_policy_validation"
    assert "safe_to_execute_worker_command_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_accept_command_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_execute_worker_command_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_cli_returns_nonzero_for_blocked_command_request():
    tool = load_tool_module()
    command_request = valid_prepare_request()
    command_request["runtime_root"] = "outputs/worker-runtime"
    output = io.StringIO()

    exit_code = tool.main(
        [
            "--command-request-json",
            json.dumps(command_request),
        ],
        out=output,
    )
    report = json.loads(output.getvalue())

    assert exit_code == 1
    assert report["command_protocol_status"] == "blocked_by_command_request_validation"
    assert "runtime_root is blocked: outputs" in report["command_request_validation_errors"]

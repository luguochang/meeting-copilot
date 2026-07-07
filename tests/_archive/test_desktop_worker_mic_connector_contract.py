import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "worker-mic-connector-contract.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_worker_mic_connector_contract.py"


EXPECTED_FALSE_FLAGS = [
    "safe_to_bind_worker_mic_connector_now",
    "safe_to_execute_connector_now",
    "safe_to_accept_mic_command_now",
    "safe_to_accept_worker_command_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_tauri_or_cargo_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_worker_mic_connector_contract",
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


def test_worker_mic_connector_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-112"
    assert policy["policy_name"] == "Desktop Worker Mic Connector Contract"
    assert policy["policy_status"] == "desktop_worker_mic_connector_contract_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == [
        "PCWEB-099",
        "PCWEB-105",
        "PCWEB-107",
        "PCWEB-109",
        "DRV-039",
    ]
    assert policy["connector_mode"] == "worker_mic_connector_contract_only"
    assert policy["connector_version"] == "desktop_worker_mic_connector_contract.v1"
    assert policy["connector_execution_status"] == "not_bound_not_executed"
    assert policy["worker_mic_source_status"] == "requires_future_worker_mic_source_approval"
    assert policy["mic_capture_status"] == "not_started"
    assert policy["worker_execution_status"] == "not_started"
    assert policy["web_handoff_status"] == "not_mutated"
    assert policy["approved_runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert policy["approved_audio_chunk_root"] == (
        "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    )
    assert policy["approved_worker_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["approved_worker_event_root"] == "artifacts/tmp/asr_events"
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_access_audio_network_processes_or_models():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
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


def test_default_report_specifies_connector_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_worker_mic_connector_contract_report(policy_path=POLICY_PATH)

    assert report["pcweb_id"] == "PCWEB-112"
    assert report["report_mode"] == "desktop_worker_mic_connector_contract_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["connector_request_validation_status"] == "not_provided"
    assert report["worker_mic_connector_status"] == "specified_not_executable"
    assert report["connector_execution_status"] == "not_bound_not_executed"
    assert report["mic_capture_status"] == "not_started"
    assert report["worker_execution_status"] == "not_started"
    assert report["web_handoff_status"] == "not_mutated"
    assert report["mic_command_request_preview"] is None
    assert report["worker_command_request_preview"] is None
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_connector_request_links_mic_start_to_worker_prepare_but_does_not_execute():
    tool = load_tool_module()

    report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=valid_connector_request(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["connector_request_validation_status"] == "passed"
    assert report["worker_mic_connector_status"] == (
        "ready_for_worker_mic_connector_contract_review"
    )
    assert report["connector_readiness_summary"] == {
        "mic_contract_status": "ready_for_mic_adapter_contract_review",
        "worker_command_protocol_status": "blocked_by_command_request_validation",
        "worker_mic_source_status": "requires_future_worker_mic_source_approval",
        "connector_execution_status": "not_bound_not_executed",
        "next_required_decision": "approve_worker_mic_source_after_real_tauri_noop_run",
    }
    assert report["mic_command_request_preview"]["command_id"] == "mic_adapter.start"
    assert report["mic_command_request_preview"]["status"] == "validated_not_executed"
    assert report["worker_command_request_preview"]["command_id"] == "worker.prepare"
    assert report["worker_command_request_preview"]["source_kind"] == "mic"
    assert report["worker_command_blocker"] == "source_kind requires later approval: mic"
    assert report["runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert report["audio_chunk_root"] == "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    assert report["worker_runtime_root"] == (
        "artifacts/tmp/desktop_asr_worker_runtime/worker-mic-connector-review"
    )
    assert report["worker_event_output_path"] == (
        "artifacts/tmp/asr_events/worker-mic-connector-review.events.json"
    )
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_spawn_worker_now"] is False
    assert report["safe_to_mutate_web_session_now"] is False


def test_connector_blocks_without_explicit_user_start_or_with_mismatched_session():
    tool = load_tool_module()
    no_consent = valid_connector_request()
    no_consent["user_consent_state"] = "not_requested"
    mismatch = valid_connector_request()
    mismatch["worker_id"] = "worker_mic_connector_other"

    no_consent_report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=no_consent,
    )
    mismatch_report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=mismatch,
    )

    assert no_consent_report["connector_request_validation_status"] == "failed"
    assert no_consent_report["worker_mic_connector_status"] == (
        "blocked_by_connector_request_validation"
    )
    assert "mic_adapter.start requires user_consent_state=explicit_user_start_granted" in (
        no_consent_report["connector_request_validation_errors"]
    )
    assert mismatch_report["connector_request_validation_status"] == "failed"
    assert "adapter_id and worker_id must match session_id" in mismatch_report[
        "connector_request_validation_errors"
    ]
    assert no_consent_report["safe_to_capture_audio_now"] is False
    assert mismatch_report["safe_to_spawn_worker_now"] is False


def test_connector_blocks_forbidden_roots_and_file_or_system_audio_sources():
    tool = load_tool_module()
    forbidden = valid_connector_request()
    forbidden["audio_chunk_root"] = "data/asr_eval/local_samples/private"
    file_source = valid_connector_request()
    file_source["mic_source_kind"] = "file"
    file_source["worker_source_kind"] = "file"
    system_audio = valid_connector_request()
    system_audio["mic_source_kind"] = "system_audio"
    system_audio["worker_source_kind"] = "system_audio"

    forbidden_report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=forbidden,
    )
    file_report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=file_source,
    )
    system_audio_report = tool.build_desktop_worker_mic_connector_contract_report(
        policy_path=POLICY_PATH,
        connector_request=system_audio,
    )

    assert forbidden_report["connector_request_validation_status"] == "failed"
    assert "audio_chunk_root is blocked: data/asr_eval/local_samples" in forbidden_report[
        "connector_request_validation_errors"
    ]
    assert forbidden_report["audio_chunk_root"] == "<redacted_invalid_path>"
    assert "mic_source_kind must be mic" in file_report["connector_request_validation_errors"]
    assert "worker_source_kind must be mic" in file_report["connector_request_validation_errors"]
    assert "mic_source_kind must be mic" in system_audio_report[
        "connector_request_validation_errors"
    ]
    assert "worker_source_kind must be mic" in system_audio_report[
        "connector_request_validation_errors"
    ]


def test_custom_policy_cannot_enable_connector_or_audio_worker_side_effects(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_spawn_worker_now"] = True
    policy_path = tmp_path / "worker-mic-connector-contract.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_worker_mic_connector_contract_report(policy_path=policy_path)

    assert report["policy_validation_status"] == "failed"
    assert report["worker_mic_connector_status"] == "blocked_by_policy_validation"
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_spawn_worker_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_spawn_worker_now"] is False

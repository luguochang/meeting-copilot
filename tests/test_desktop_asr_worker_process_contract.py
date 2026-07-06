import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-process-contract.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_process_contract.py"

EXPECTED_FALSE_FLAGS = [
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

EXPECTED_EVENT_TYPES = [
    "partial",
    "final",
    "revision",
    "error",
    "end_of_stream",
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
        "desktop_asr_worker_process_contract",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_worker_contract() -> dict:
    return {
        "contract_version": "desktop_asr_worker_process_contract.v1",
        "worker_id": "desktop_worker_process_contract_review",
        "session_id": "desktop_worker_process_contract_review",
        "provider": "sherpa_onnx_streaming",
        "source_kind": "synthetic",
        "event_output_mode": "event_file",
        "event_output_path": "artifacts/tmp/asr_events/desktop-worker-process-contract.events.json",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-process-contract",
        "handoff_api_endpoint": "/live/asr/local-event-files/sessions",
        "command_transport": "stdio_jsonl",
        "declared_event_types": EXPECTED_EVENT_TYPES,
        "declared_commands": EXPECTED_COMMAND_IDS,
    }


def test_worker_process_contract_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-098"
    assert policy["policy_name"] == "Desktop ASR Worker Process Contract"
    assert policy["policy_status"] == "desktop_asr_worker_process_contract_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["contract_mode"] == "process_contract_only"
    assert policy["worker_process_status"] == "not_spawned"
    assert policy["worker_lifecycle_status"] == "specified_not_started"
    assert policy["worker_health_status"] == "not_checked"
    assert policy["worker_command_transport_status"] == "not_bound"
    assert policy["worker_output_contract_status"] == "event_file_or_stream_specified"
    assert policy["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["handoff_api_endpoint"] == "/live/asr/local-event-files/sessions"
    assert policy["required_preflight_sources"] == ["PCWEB-095", "PCWEB-096", "PCWEB-097"]
    assert policy["allowed_source_kinds_now"] == ["preflight_only", "synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file", "system_audio"]
    assert policy["event_output_contract"]["event_types"] == EXPECTED_EVENT_TYPES
    assert [item["command_id"] for item in policy["worker_command_catalog"]] == (
        EXPECTED_COMMAND_IDS
    )
    assert all(item["safe_to_execute_now"] is False for item in policy["worker_command_catalog"])
    assert policy["resource_limits"] == {
        "max_chunk_ms": 30000,
        "max_event_file_bytes": 10485760,
        "max_session_duration_minutes": 30,
        "max_worker_memory_mb": 4096,
        "max_worker_cpu_percent": 300,
    }
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


def test_default_report_specifies_no_spawn_contract():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_process_contract_report(
        policy_path=POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-098"
    assert report["report_mode"] == "desktop_asr_worker_process_contract_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["worker_contract_validation_status"] == "not_provided"
    assert report["process_contract_status"] == "specified_not_executable"
    assert report["worker_process_status"] == "not_spawned"
    assert report["worker_lifecycle_status"] == "specified_not_started"
    assert report["worker_health_status"] == "not_checked"
    assert report["worker_command_transport_status"] == "not_bound"
    assert report["worker_output_contract_status"] == "event_file_or_stream_specified"
    assert report["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert report["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert report["handoff_api_endpoint"] == "/live/asr/local-event-files/sessions"
    assert report["event_output_contract"]["event_types"] == EXPECTED_EVENT_TYPES
    assert [item["command_id"] for item in report["worker_command_catalog"]] == (
        EXPECTED_COMMAND_IDS
    )
    assert report["future_web_handoff_request_preview"] is None
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_worker_contract_preview_reports_ready_without_spawning():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_process_contract_report(
        policy_path=POLICY_PATH,
        worker_contract=valid_worker_contract(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["worker_contract_validation_status"] == "passed"
    assert report["process_contract_status"] == "ready_for_no_spawn_contract_review"
    assert report["event_output_path"] == (
        "artifacts/tmp/asr_events/desktop-worker-process-contract.events.json"
    )
    assert report["runtime_root"] == (
        "artifacts/tmp/desktop_asr_worker_runtime/desktop-worker-process-contract"
    )
    assert report["future_web_handoff_request_preview"] == {
        "session_id": "desktop_worker_process_contract_review",
        "provider": "sherpa_onnx_streaming",
        "events_path": "artifacts/tmp/asr_events/desktop-worker-process-contract.events.json",
    }
    assert report["safe_to_spawn_worker_now"] is False
    assert report["safe_to_write_event_file_now"] is False
    assert report["safe_to_read_event_file_now"] is False
    assert report["safe_to_mutate_web_session_now"] is False


def test_worker_contract_rejects_mic_source_until_adapter_is_approved():
    tool = load_tool_module()
    worker_contract = valid_worker_contract()
    worker_contract["source_kind"] = "mic"

    report = tool.build_desktop_asr_worker_process_contract_report(
        policy_path=POLICY_PATH,
        worker_contract=worker_contract,
    )

    assert report["worker_contract_validation_status"] == "failed"
    assert report["process_contract_status"] == "blocked_by_worker_contract_validation"
    assert "source_kind requires later approval: mic" in report["worker_contract_validation_errors"]
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_spawn_worker_now"] is False


def test_worker_contract_rejects_forbidden_or_repo_outside_paths_and_redacts(tmp_path):
    tool = load_tool_module()
    approved_event_dir = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_dir = tmp_path / "configs" / "local"
    approved_event_dir.mkdir(parents=True)
    forbidden_dir.mkdir(parents=True)
    linked_event_path = approved_event_dir / "linked.events.json"
    linked_event_path.symlink_to(forbidden_dir / "private.events.json")

    worker_contract = valid_worker_contract()
    worker_contract["event_output_path"] = str(linked_event_path)
    worker_contract["runtime_root"] = "/tmp/outside-worker-runtime"

    report = tool.build_desktop_asr_worker_process_contract_report(
        policy_path=POLICY_PATH,
        worker_contract=worker_contract,
        repo_root=tmp_path,
    )

    assert report["worker_contract_validation_status"] == "failed"
    assert report["process_contract_status"] == "blocked_by_worker_contract_validation"
    assert report["event_output_path"] == "<redacted_invalid_path>"
    assert report["runtime_root"] == "<redacted_invalid_path>"
    assert "event_output_path is blocked: configs/local" in report[
        "worker_contract_validation_errors"
    ]
    assert "runtime_root is outside repository" in report["worker_contract_validation_errors"]
    assert str(linked_event_path) not in json.dumps(report, ensure_ascii=False)


def test_custom_policy_cannot_enable_spawn_audio_remote_or_model_download(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_spawn_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy_path = tmp_path / "asr-worker-process-contract.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_process_contract_report(
        policy_path=policy_path,
        worker_contract=valid_worker_contract(),
    )

    assert report["policy_validation_status"] == "failed"
    assert report["process_contract_status"] == "blocked_by_policy_validation"
    assert "safe_to_spawn_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_spawn_worker_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_cli_returns_nonzero_for_blocked_worker_contract():
    tool = load_tool_module()
    worker_contract = valid_worker_contract()
    worker_contract["runtime_root"] = "outputs/worker-runtime"
    output = io.StringIO()

    exit_code = tool.main(
        [
            "--worker-contract-json",
            json.dumps(worker_contract),
        ],
        out=output,
    )
    report = json.loads(output.getvalue())

    assert exit_code == 1
    assert report["process_contract_status"] == "blocked_by_worker_contract_validation"
    assert "runtime_root is blocked: outputs" in report["worker_contract_validation_errors"]

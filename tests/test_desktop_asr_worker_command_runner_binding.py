import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-command-runner-binding.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_command_runner_binding.py"

EXPECTED_COMMAND_IDS = [
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
]

EXPECTED_FALSE_FLAGS = [
    "safe_to_execute_command_runner_now",
    "safe_to_bind_command_runner_now",
    "safe_to_accept_worker_command_now",
    "safe_to_dispatch_worker_command_now",
    "safe_to_execute_worker_command_now",
    "safe_to_spawn_process_now",
    "safe_to_run_subprocess_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_check_worker_health_now",
    "safe_to_collect_worker_events_now",
    "safe_to_bind_worker_command_transport_now",
    "safe_to_bind_tauri_command_now",
    "safe_to_invoke_tauri_ipc_now",
    "safe_to_import_provider_now",
    "safe_to_execute_provider_now",
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


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_command_runner_binding",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_binding_request() -> dict:
    return {
        "binding_version": "desktop_asr_worker_command_runner_binding.v1",
        "binding_id": "desktop_worker_command_runner_binding_review",
        "sidecar_module_path": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "native_command_runner_path": (
            "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
        ),
        "command_transport": "stdio_jsonl",
        "command_catalog": EXPECTED_COMMAND_IDS,
        "provider_mode": "sherpa_onnx_streaming",
        "source_kind": "synthetic",
        "event_output_root": "artifacts/tmp/asr_events",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime",
    }


def test_command_runner_binding_policy_exists_and_blocks_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-103"
    assert policy["policy_name"] == "Desktop ASR Worker Command Runner Binding"
    assert policy["policy_status"] == (
        "desktop_asr_worker_command_runner_binding_policy_only"
    )
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-102"]
    assert policy["binding_mode"] == "command_runner_binding_preview_only"
    assert policy["execution_mode"] == "no_execution"
    assert policy["binding_version"] == "desktop_asr_worker_command_runner_binding.v1"
    assert policy["command_runner_binding_status"] == "specified_not_executable"
    assert policy["native_command_runner_status"] == "path_reserved_not_bound"
    assert policy["sidecar_module_path"] == "code/asr_runtime/scripts/asr_worker_sidecar.py"
    assert policy["future_native_command_runner_path"] == (
        "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
    )
    assert policy["command_transport_preview"] == "stdio_jsonl"
    assert policy["command_catalog"] == EXPECTED_COMMAND_IDS
    assert policy["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["allowed_provider_modes_now"] == ["mock_streaming", "sherpa_onnx_streaming"]
    assert policy["future_provider_modes_requiring_approval"] == ["funasr_streaming"]
    assert policy["forbidden_provider_modes"] == ["remote_asr", "remote_llm_asr"]
    assert policy["allowed_source_kinds_now"] == ["synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file", "system_audio"]
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_spawn_processes_or_access_audio_models_network_or_tauri():
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


def test_default_report_specifies_static_binding_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-103"
    assert report["report_mode"] == (
        "desktop_asr_worker_command_runner_binding_static_report"
    )
    assert report["policy_validation_status"] == "passed"
    assert report["binding_request_validation_status"] == "not_provided"
    assert report["validation_status"] == "policy_validation_passed_request_not_provided"
    assert report["binding_request_required_for_review"] is True
    assert report["ready_for_no_execution_binding_review"] is False
    assert report["command_runner_binding_status"] == "specified_not_executable"
    assert report["native_command_runner_status"] == "path_reserved_not_bound"
    assert report["sidecar_module_status"] == "path_validated_not_executed"
    assert report["worker_execution_status"] == "not_executed"
    assert report["process_spawn_status"] == "not_spawned"
    assert report["command_catalog"] == EXPECTED_COMMAND_IDS
    assert report["future_native_command_preview"] is None
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_binding_request_returns_native_command_preview_without_binding_or_spawn():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=POLICY_PATH,
        binding_request=valid_binding_request(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["binding_request_validation_status"] == "passed"
    assert report["command_runner_binding_status"] == (
        "ready_for_no_execution_binding_review"
    )
    assert report["sidecar_module_path"] == "code/asr_runtime/scripts/asr_worker_sidecar.py"
    assert report["native_command_runner_path"] == (
        "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
    )
    assert report["event_output_root"] == "artifacts/tmp/asr_events"
    assert report["runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert report["future_native_command_preview"] == {
        "reserved_tauri_command_name_preview": "asr_worker_command_runner_preview",
        "native_command_runner_path": (
            "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
        ),
        "sidecar_module_path": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "command_transport": "stdio_jsonl",
        "command_catalog": EXPECTED_COMMAND_IDS,
        "binding_status": "validated_not_bound",
        "command_dispatch_status": "not_dispatched",
        "tauri_ipc_status": "not_invoked",
        "process_spawn_status": "not_spawned",
        "health_probe_status": "not_executed",
        "event_collection_status": "not_executed",
        "worker_execution_status": "not_executed",
    }
    assert "tauri_command_name" not in report["future_native_command_preview"]
    assert report["ready_for_no_execution_binding_review"] is True
    assert report["safe_to_execute_command_runner_now"] is False
    assert report["safe_to_dispatch_worker_command_now"] is False
    assert report["safe_to_run_subprocess_now"] is False
    assert report["safe_to_invoke_tauri_ipc_now"] is False
    assert report["safe_to_spawn_process_now"] is False
    assert report["safe_to_bind_worker_command_transport_now"] is False
    assert report["safe_to_write_event_file_now"] is False


def test_binding_request_rejects_mic_remote_provider_and_funasr_until_later_approval():
    tool = load_tool_module()
    mic_request = valid_binding_request()
    mic_request["source_kind"] = "mic"
    remote_request = valid_binding_request()
    remote_request["provider_mode"] = "remote_asr"
    funasr_request = valid_binding_request()
    funasr_request["provider_mode"] = "funasr_streaming"

    mic_report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=POLICY_PATH,
        binding_request=mic_request,
    )
    remote_report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=POLICY_PATH,
        binding_request=remote_request,
    )
    funasr_report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=POLICY_PATH,
        binding_request=funasr_request,
    )

    assert mic_report["binding_request_validation_status"] == "failed"
    assert "source_kind requires later approval: mic" in mic_report[
        "binding_request_validation_errors"
    ]
    assert mic_report["safe_to_capture_audio_now"] is False

    assert remote_report["binding_request_validation_status"] == "failed"
    assert "provider_mode is forbidden: remote_asr" in remote_report[
        "binding_request_validation_errors"
    ]
    assert remote_report["safe_to_call_remote_asr_now"] is False

    assert funasr_report["binding_request_validation_status"] == "failed"
    assert "provider_mode requires later approval: funasr_streaming" in funasr_report[
        "binding_request_validation_errors"
    ]
    assert funasr_report["safe_to_download_models_now"] is False


def test_binding_request_rejects_forbidden_repo_outside_and_symlink_paths(tmp_path):
    tool = load_tool_module()
    policy_dir = tmp_path / "code" / "desktop_tauri"
    approved_event_root = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_root = tmp_path / "configs" / "local"
    policy_dir.mkdir(parents=True)
    approved_event_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    policy_path = policy_dir / "asr-worker-command-runner-binding.policy.json"
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    linked_event_root = approved_event_root / "linked"
    linked_event_root.symlink_to(forbidden_root)

    binding_request = valid_binding_request()
    binding_request["event_output_root"] = str(linked_event_root)
    binding_request["runtime_root"] = "/tmp/outside-worker-runtime"
    binding_request["native_command_runner_path"] = "configs/local/asr_worker_command_runner.rs"

    report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=policy_path,
        binding_request=binding_request,
        repo_root=tmp_path,
    )

    assert report["binding_request_validation_status"] == "failed"
    assert report["command_runner_binding_status"] == "blocked_by_binding_request_validation"
    assert report["event_output_root"] == "<redacted_invalid_path>"
    assert report["runtime_root"] == "<redacted_invalid_path>"
    assert report["native_command_runner_path"] == "<redacted_invalid_path>"
    assert "event_output_root is blocked: configs/local" in report[
        "binding_request_validation_errors"
    ]
    assert "runtime_root is outside repository" in report[
        "binding_request_validation_errors"
    ]
    assert "native_command_runner_path is blocked: configs/local" in report[
        "binding_request_validation_errors"
    ]
    assert str(linked_event_root) not in json.dumps(report, ensure_ascii=False)


def test_custom_policy_cannot_enable_binding_spawn_audio_remote_models_or_tauri(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_execute_command_runner_now"] = True
    policy["safe_to_spawn_process_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy["safe_to_run_tauri_or_cargo_now"] = True
    policy_dir = tmp_path / "code" / "desktop_tauri"
    policy_dir.mkdir(parents=True)
    policy_path = policy_dir / "bad-binding-policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=policy_path,
        binding_request=valid_binding_request(),
        repo_root=tmp_path,
    )

    assert report["policy_validation_status"] == "failed"
    assert report["command_runner_binding_status"] == "blocked_by_policy_validation"
    assert "safe_to_execute_command_runner_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_spawn_process_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert "safe_to_run_tauri_or_cargo_now must be false" in report[
        "policy_validation_errors"
    ]


def test_policy_path_is_rejected_before_reading_forbidden_or_repo_outside_file(tmp_path):
    tool = load_tool_module()
    forbidden_dir = tmp_path / "configs" / "local"
    forbidden_dir.mkdir(parents=True)
    forbidden_policy_path = forbidden_dir / "policy.json"
    forbidden_policy_path.write_text("{not-json", encoding="utf-8")

    report = tool.build_desktop_asr_worker_command_runner_binding_report(
        policy_path=forbidden_policy_path,
        binding_request=valid_binding_request(),
        repo_root=tmp_path,
    )

    assert report["policy_validation_status"] == "failed"
    assert "policy_path is blocked: configs/local" in report["policy_validation_errors"]
    assert report["command_runner_binding_status"] == "blocked_by_policy_validation"
    assert str(forbidden_policy_path) not in json.dumps(report, ensure_ascii=False)


def test_cli_returns_nonzero_for_blocked_binding_request(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path
    policy_dir = repo_root / "code" / "desktop_tauri"
    request_dir = repo_root / "artifacts" / "tmp" / "desktop_asr_worker_runtime"
    policy_dir.mkdir(parents=True)
    request_dir.mkdir(parents=True)
    policy_path = policy_dir / "asr-worker-command-runner-binding.policy.json"
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    request_path = request_dir / "blocked-binding-request.json"
    request = valid_binding_request()
    request["source_kind"] = "mic"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = tool.main(
        [
            "--policy",
            str(policy_path),
            "--binding-request",
            str(request_path),
            "--repo-root",
            str(repo_root),
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["binding_request_validation_status"] == "failed"
    assert payload["safe_to_capture_audio_now"] is False


def test_cli_main_reads_sys_argv_when_argv_is_not_injected(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path
    policy_dir = repo_root / "code" / "desktop_tauri"
    request_dir = repo_root / "artifacts" / "tmp" / "desktop_asr_worker_runtime"
    policy_dir.mkdir(parents=True)
    request_dir.mkdir(parents=True)
    policy_path = policy_dir / "asr-worker-command-runner-binding.policy.json"
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    request_path = request_dir / "blocked-binding-request.json"
    request = valid_binding_request()
    request["provider_mode"] = "remote_asr"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    monkeypatch.setattr(
        tool.sys,
        "argv",
        [
            "desktop_asr_worker_command_runner_binding.py",
            "--policy",
            str(policy_path),
            "--binding-request",
            str(request_path),
            "--repo-root",
            str(repo_root),
        ],
    )
    stdout = io.StringIO()

    exit_code = tool.main(stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["binding_request_validation_status"] == "failed"
    assert "provider_mode is forbidden: remote_asr" in payload[
        "binding_request_validation_errors"
    ]
    assert payload["safe_to_call_remote_asr_now"] is False


def test_cli_rejects_binding_request_path_before_reading_forbidden_file(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path
    policy_dir = repo_root / "code" / "desktop_tauri"
    forbidden_dir = repo_root / "configs" / "local"
    policy_dir.mkdir(parents=True)
    forbidden_dir.mkdir(parents=True)
    policy_path = policy_dir / "asr-worker-command-runner-binding.policy.json"
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    forbidden_request_path = forbidden_dir / "request.json"
    forbidden_request_path.write_text("{not-json", encoding="utf-8")
    stdout = io.StringIO()

    exit_code = tool.main(
        [
            "--policy",
            str(policy_path),
            "--binding-request",
            str(forbidden_request_path),
            "--repo-root",
            str(repo_root),
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["binding_request_validation_status"] == "failed"
    assert "binding_request_path is blocked: configs/local" in payload[
        "binding_request_validation_errors"
    ]
    assert str(forbidden_request_path) not in json.dumps(payload, ensure_ascii=False)

import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-command-runner-implementation-skeleton.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_command_runner_implementation_skeleton.py"
RUST_SKELETON_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "asr_worker_command_runner.rs"
LIB_RS_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"

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
        "desktop_asr_worker_command_runner_implementation_skeleton",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def valid_skeleton_request() -> dict:
    return {
        "skeleton_version": "desktop_asr_worker_command_runner_implementation_skeleton.v1",
        "skeleton_review_id": "desktop_worker_command_runner_implementation_skeleton_review",
        "native_command_runner_path": (
            "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
        ),
        "sidecar_module_path": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "command_transport": "stdio_jsonl",
        "command_catalog": EXPECTED_COMMAND_IDS,
        "provider_mode": "mock_streaming",
        "source_kind": "synthetic",
        "event_output_root": "artifacts/tmp/asr_events",
        "runtime_root": "artifacts/tmp/desktop_asr_worker_runtime",
        "preview_command_id": "worker.health",
    }


def test_implementation_skeleton_policy_exists_and_blocks_dispatch():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-104"
    assert policy["policy_name"] == "Desktop ASR Worker Command Runner Implementation Skeleton"
    assert policy["policy_status"] == (
        "desktop_asr_worker_command_runner_implementation_skeleton_policy_only"
    )
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-103"]
    assert policy["skeleton_mode"] == "command_runner_implementation_skeleton_only"
    assert policy["execution_mode"] == "no_dispatch_no_execution"
    assert policy["skeleton_version"] == (
        "desktop_asr_worker_command_runner_implementation_skeleton.v1"
    )
    assert policy["runner_implementation_status"] == "skeleton_not_bound"
    assert policy["native_command_runner_status"] == "skeleton_file_not_bound"
    assert policy["native_command_runner_path"] == (
        "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
    )
    assert policy["sidecar_module_path"] == "code/asr_runtime/scripts/asr_worker_sidecar.py"
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


def test_rust_skeleton_exists_but_is_not_bound_to_tauri_or_process_execution():
    rust_source = RUST_SKELETON_PATH.read_text(encoding="utf-8")
    lib_source = LIB_RS_PATH.read_text(encoding="utf-8")

    assert "AsrWorkerCommandRunnerPreview" in rust_source
    assert "BlockedCommandRunnerResponse" in rust_source
    assert "command_catalog_preview" in rust_source
    assert "preview_blocked_response" in rust_source
    assert "mod asr_worker_command_runner" not in lib_source
    assert "asr_worker_command_runner" not in lib_source

    forbidden_rust_snippets = [
        "std::process",
        "Command::new",
        "std::fs",
        "File::",
        "read_to_string",
        "write(",
        "TcpStream",
        "reqwest",
        "ureq",
        "cpal",
        "rodio",
        "tauri::command",
        "#[tauri::command]",
    ]
    for snippet in forbidden_rust_snippets:
        assert snippet not in rust_source


def test_default_report_validates_static_skeleton_without_dispatch():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-104"
    assert report["report_mode"] == (
        "desktop_asr_worker_command_runner_implementation_skeleton_static_report"
    )
    assert report["policy_validation_status"] == "passed"
    assert report["rust_skeleton_validation_status"] == "passed"
    assert report["skeleton_request_validation_status"] == "not_provided"
    assert report["validation_status"] == "policy_and_skeleton_validation_passed_request_not_provided"
    assert report["skeleton_request_required_for_review"] is True
    assert report["ready_for_no_dispatch_skeleton_review"] is False
    assert report["runner_implementation_status"] == "skeleton_not_bound_no_dispatch"
    assert report["native_command_runner_status"] == "source_validated_not_bound"
    assert report["command_dispatch_status"] == "not_dispatched"
    assert report["tauri_ipc_status"] == "not_invoked"
    assert report["process_spawn_status"] == "not_spawned"
    assert report["worker_execution_status"] == "not_executed"
    assert report["event_file_read_status"] == "not_read"
    assert report["event_file_write_status"] == "not_written"
    assert report["future_native_command_runner_skeleton"] is None
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_valid_skeleton_request_returns_blocked_command_preview_without_dispatch():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
        skeleton_request=valid_skeleton_request(),
    )

    assert report["policy_validation_status"] == "passed"
    assert report["rust_skeleton_validation_status"] == "passed"
    assert report["skeleton_request_validation_status"] == "passed"
    assert report["runner_implementation_status"] == "ready_for_no_dispatch_skeleton_review"
    assert report["ready_for_no_dispatch_skeleton_review"] is True
    assert report["future_native_command_runner_skeleton"] == {
        "native_command_runner_path": (
            "code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs"
        ),
        "sidecar_module_path": "code/asr_runtime/scripts/asr_worker_sidecar.py",
        "command_transport": "stdio_jsonl",
        "command_catalog": EXPECTED_COMMAND_IDS,
        "implementation_status": "skeleton_source_validated_not_bound",
        "binding_status": "not_bound",
        "command_dispatch_status": "not_dispatched",
        "tauri_ipc_status": "not_invoked",
        "process_spawn_status": "not_spawned",
        "worker_execution_status": "not_executed",
    }
    assert report["blocked_command_preview"] == {
        "command_id": "worker.health",
        "accepted": False,
        "binding_status": "not_bound",
        "dispatch_status": "not_dispatched",
        "worker_execution_status": "not_executed",
        "tauri_ipc_status": "not_invoked",
        "process_spawn_status": "not_spawned",
        "event_file_read_status": "not_read",
        "event_file_write_status": "not_written",
        "safe_to_execute_now": False,
    }
    assert report["safe_to_dispatch_worker_command_now"] is False
    assert report["safe_to_spawn_process_now"] is False
    assert report["safe_to_invoke_tauri_ipc_now"] is False


def test_skeleton_request_rejects_invalid_command_mic_remote_and_funasr():
    tool = load_tool_module()
    invalid_command_request = valid_skeleton_request()
    invalid_command_request["preview_command_id"] = "worker.capture_mic"
    mic_request = valid_skeleton_request()
    mic_request["source_kind"] = "mic"
    remote_request = valid_skeleton_request()
    remote_request["provider_mode"] = "remote_asr"
    funasr_request = valid_skeleton_request()
    funasr_request["provider_mode"] = "funasr_streaming"

    invalid_command_report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
        skeleton_request=invalid_command_request,
    )
    mic_report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
        skeleton_request=mic_request,
    )
    remote_report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
        skeleton_request=remote_request,
    )
    funasr_report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=POLICY_PATH,
        rust_skeleton_path=RUST_SKELETON_PATH,
        skeleton_request=funasr_request,
    )

    assert invalid_command_report["skeleton_request_validation_status"] == "failed"
    assert "preview_command_id is unsupported: worker.capture_mic" in invalid_command_report[
        "skeleton_request_validation_errors"
    ]
    assert invalid_command_report["safe_to_dispatch_worker_command_now"] is False

    assert mic_report["skeleton_request_validation_status"] == "failed"
    assert "source_kind requires later approval: mic" in mic_report[
        "skeleton_request_validation_errors"
    ]
    assert mic_report["safe_to_capture_audio_now"] is False

    assert remote_report["skeleton_request_validation_status"] == "failed"
    assert "provider_mode is forbidden: remote_asr" in remote_report[
        "skeleton_request_validation_errors"
    ]
    assert remote_report["safe_to_call_remote_asr_now"] is False

    assert funasr_report["skeleton_request_validation_status"] == "failed"
    assert "provider_mode requires later approval: funasr_streaming" in funasr_report[
        "skeleton_request_validation_errors"
    ]
    assert funasr_report["safe_to_download_models_now"] is False


def test_custom_policy_cannot_enable_dispatch_spawn_audio_remote_models_or_tauri(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_dispatch_worker_command_now"] = True
    policy["safe_to_spawn_process_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy["safe_to_run_tauri_or_cargo_now"] = True
    policy_dir = tmp_path / "code" / "desktop_tauri"
    skeleton_dir = tmp_path / "code" / "desktop_tauri" / "src-tauri" / "src"
    policy_dir.mkdir(parents=True)
    skeleton_dir.mkdir(parents=True)
    policy_path = policy_dir / "bad-skeleton-policy.json"
    rust_path = skeleton_dir / "asr_worker_command_runner.rs"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    rust_path.write_text(RUST_SKELETON_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    report = tool.build_desktop_asr_worker_command_runner_implementation_skeleton_report(
        policy_path=policy_path,
        rust_skeleton_path=rust_path,
        skeleton_request=valid_skeleton_request(),
        repo_root=tmp_path,
    )

    assert report["policy_validation_status"] == "failed"
    assert report["runner_implementation_status"] == "blocked_by_policy_validation"
    assert "safe_to_dispatch_worker_command_now must be false" in report[
        "policy_validation_errors"
    ]
    assert "safe_to_spawn_process_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert "safe_to_run_tauri_or_cargo_now must be false" in report[
        "policy_validation_errors"
    ]


def test_cli_rejects_skeleton_request_path_before_reading_forbidden_file(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path
    policy_dir = repo_root / "code" / "desktop_tauri"
    skeleton_dir = repo_root / "code" / "desktop_tauri" / "src-tauri" / "src"
    forbidden_dir = repo_root / "configs" / "local"
    policy_dir.mkdir(parents=True)
    skeleton_dir.mkdir(parents=True)
    forbidden_dir.mkdir(parents=True)
    policy_path = policy_dir / "asr-worker-command-runner-implementation-skeleton.policy.json"
    rust_path = skeleton_dir / "asr_worker_command_runner.rs"
    forbidden_request_path = forbidden_dir / "request.json"
    policy_path.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    rust_path.write_text(RUST_SKELETON_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    forbidden_request_path.write_text("{not-json", encoding="utf-8")
    stdout = io.StringIO()

    exit_code = tool.main(
        [
            "--policy",
            str(policy_path),
            "--rust-skeleton",
            str(rust_path),
            "--skeleton-request",
            str(forbidden_request_path),
            "--repo-root",
            str(repo_root),
        ],
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert exit_code == 1
    assert payload["skeleton_request_validation_status"] == "failed"
    assert "skeleton_request_path is blocked: configs/local" in payload[
        "skeleton_request_validation_errors"
    ]
    assert str(forbidden_request_path) not in json.dumps(payload, ensure_ascii=False)

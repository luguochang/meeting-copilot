import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-no-execution-skeleton.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_no_execution_skeleton.py"
SIDECAR_PATH = REPO_ROOT / "code" / "asr_runtime" / "scripts" / "asr_worker_sidecar.py"

EXPECTED_FALSE_FLAGS = [
    "safe_to_execute_worker_now",
    "safe_to_spawn_process_now",
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
    "safe_to_import_model_now",
    "safe_to_execute_provider_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_module():
    return load_module(TOOL_PATH, "desktop_asr_worker_no_execution_skeleton")


def load_sidecar_module():
    return load_module(SIDECAR_PATH, "asr_worker_sidecar")


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_no_execution_skeleton_policy_exists_and_blocks_all_real_execution():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-102"
    assert policy["policy_name"] == "Desktop ASR Worker No-Execution Skeleton"
    assert policy["policy_status"] == "desktop_asr_worker_no_execution_skeleton_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["required_previous_contracts"] == ["PCWEB-101"]
    assert policy["skeleton_mode"] == "module_boundary_only"
    assert policy["execution_mode"] == "no_execution"
    assert policy["worker_skeleton_status"] == "specified_not_executable"
    assert policy["sidecar_module_path"] == "code/asr_runtime/scripts/asr_worker_sidecar.py"
    assert policy["approved_event_output_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["allowed_provider_modes_now"] == ["mock_streaming", "sherpa_onnx_streaming"]
    assert policy["future_provider_modes_requiring_approval"] == ["funasr_streaming"]
    assert policy["forbidden_provider_modes"] == ["remote_asr", "remote_llm_asr"]
    assert policy["allowed_source_kinds_now"] == ["synthetic"]
    assert policy["future_source_kinds_requiring_approval"] == ["mic", "file", "system_audio"]
    assert policy["module_boundaries"] == [
        "worker_identity_preview",
        "command_envelope_intake_preview",
        "lifecycle_state_preview",
        "event_writer_preview_contract",
        "provider_adapter_preview_contract",
        "health_status_preview",
        "cleanup_plan_preview",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_sidecar_and_tool_sources_do_not_spawn_processes_or_access_audio_models_or_network():
    combined_source = "\n".join(
        [
            SIDECAR_PATH.read_text(encoding="utf-8"),
            TOOL_PATH.read_text(encoding="utf-8"),
        ]
    )

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
        assert snippet not in combined_source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in combined_source


def test_sidecar_default_report_is_module_boundary_only_and_not_executable():
    sidecar = load_sidecar_module()

    report = sidecar.build_no_execution_worker_skeleton_report()

    assert report["pcweb_id"] == "PCWEB-102"
    assert report["module_name"] == "asr_worker_sidecar"
    assert report["skeleton_mode"] == "module_boundary_only"
    assert report["execution_mode"] == "no_execution"
    assert report["worker_skeleton_status"] == "specified_not_executable"
    assert report["worker_execution_status"] == "not_executed"
    assert report["lifecycle_state_preview"]["current_state"] == "not_prepared"
    assert report["event_writer_preview"]["write_status"] == "not_written"
    assert report["runtime_audio_preview"]["write_status"] == "not_written"
    assert report["health_status_preview"]["worker_health"] == "not_started"
    assert report["cleanup_plan_preview"]["cleanup_execution_status"] == "not_executed"
    assert report["validation_status"] == "passed"
    assert report["next_action"] == "bind_skeleton_to_desktop_command_runner_after_explicit_approval"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_tool_default_report_validates_policy_and_sidecar_without_execution():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
    )

    assert report["pcweb_id"] == "PCWEB-102"
    assert report["report_mode"] == "desktop_asr_worker_no_execution_skeleton_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["sidecar_validation_status"] == "passed"
    assert report["worker_skeleton_status"] == "specified_not_executable"
    assert report["worker_execution_status"] == "not_executed"
    assert report["required_previous_contracts"] == ["PCWEB-101"]
    assert report["module_boundaries"] == [
        "worker_identity_preview",
        "command_envelope_intake_preview",
        "lifecycle_state_preview",
        "event_writer_preview_contract",
        "provider_adapter_preview_contract",
        "health_status_preview",
        "cleanup_plan_preview",
    ]
    assert report["sidecar_report"]["worker_execution_status"] == "not_executed"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_sherpa_synthetic_preview_returns_provider_boundaries_without_running_provider():
    tool = load_tool_module()

    report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
        provider_mode="sherpa_onnx_streaming",
        source_kind="synthetic",
    )

    assert report["validation_status"] == "passed"
    assert report["sidecar_report"]["provider_adapter_preview"] == {
        "provider_mode": "sherpa_onnx_streaming",
        "provider_adapter_status": "preview_only_not_imported",
        "provider_execution_status": "not_executed",
        "model_import_status": "not_imported",
    }
    assert report["sidecar_report"]["source_preview"] == {
        "source_kind": "synthetic",
        "source_status": "preview_only_no_audio_read",
        "audio_capture_status": "not_started",
        "user_audio_read_status": "not_read",
    }
    assert report["safe_to_execute_provider_now"] is False


def test_rejects_mic_file_remote_asr_and_funasr_until_later_approval():
    tool = load_tool_module()

    mic_report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
        source_kind="mic",
    )
    remote_report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
        provider_mode="remote_asr",
    )
    funasr_report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
        provider_mode="funasr_streaming",
    )

    assert mic_report["validation_status"] == "failed"
    assert "source_kind requires later approval: mic" in mic_report["validation_errors"]
    assert mic_report["safe_to_capture_audio_now"] is False

    assert remote_report["validation_status"] == "failed"
    assert "provider_mode is forbidden: remote_asr" in remote_report["validation_errors"]
    assert remote_report["safe_to_call_remote_asr_now"] is False

    assert funasr_report["validation_status"] == "failed"
    assert "provider_mode requires later approval: funasr_streaming" in funasr_report[
        "validation_errors"
    ]
    assert funasr_report["safe_to_download_models_now"] is False


def test_rejects_forbidden_repo_outside_and_symlink_roots(tmp_path):
    tool = load_tool_module()
    repo_root = tmp_path
    approved_runtime_root = repo_root / "artifacts" / "tmp" / "desktop_asr_worker_runtime"
    forbidden_root = repo_root / "configs" / "local"
    approved_runtime_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    runtime_symlink = approved_runtime_root / "linked-runtime"
    runtime_symlink.symlink_to(forbidden_root)

    report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=POLICY_PATH,
        event_output_root="/tmp/outside-asr-events",
        runtime_root=str(runtime_symlink),
        repo_root=repo_root,
    )

    assert report["validation_status"] == "failed"
    assert report["event_output_root"] == "<redacted_invalid_path>"
    assert report["runtime_root"] == "<redacted_invalid_path>"
    assert "event_output_root is outside repository" in report["validation_errors"]
    assert "runtime_root is blocked: configs/local" in report["validation_errors"]
    assert str(runtime_symlink) not in json.dumps(report, ensure_ascii=False)


def test_custom_policy_cannot_enable_execution_audio_remote_or_models(tmp_path):
    tool = load_tool_module()
    policy = load_policy()
    policy["safe_to_execute_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy["safe_to_download_models_now"] = True
    policy_path = tmp_path / "bad-policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_no_execution_skeleton_report(
        policy_path=policy_path,
    )

    assert report["policy_validation_status"] == "failed"
    assert report["worker_skeleton_status"] == "blocked_by_policy_validation"
    assert "safe_to_execute_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert "safe_to_download_models_now must be false" in report["policy_validation_errors"]
    assert report["safe_to_execute_worker_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_cli_returns_nonzero_for_blocked_skeleton_config():
    tool = load_tool_module()

    exit_code = tool.main(
        [
            "--provider-mode",
            "remote_asr",
        ],
        stdout=io.StringIO(),
    )

    assert exit_code == 1

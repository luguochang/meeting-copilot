import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "mic-adapter-implementation-boundary.policy.json"
RUST_MODULE_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "mic_adapter_runtime.rs"
LIB_RS_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_mic_adapter_implementation_boundary.py"
READINESS_TOOL_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_readiness_gate.py"

EXPECTED_COMMANDS = [
    "prepare",
    "status",
    "start",
    "pause",
    "resume",
    "stop",
    "delete_audio_chunks",
]
EXPECTED_FALSE_FLAGS = [
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_start_recording_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_spawn_worker_now",
    "safe_to_run_tauri_or_cargo_now",
]


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_module():
    return load_module(TOOL_PATH, "desktop_mic_adapter_implementation_boundary")


def load_readiness_module():
    return load_module(READINESS_TOOL_PATH, "real_mic_shadow_test_readiness_gate")


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_policy_exists_and_keeps_all_execution_side_effects_false():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-121"
    assert policy["policy_name"] == "Desktop Mic Adapter Implementation Boundary"
    assert policy["policy_status"] == "desktop_mic_adapter_implementation_boundary_policy_only"
    assert policy["implementation_mode"] == "static_runtime_boundary_no_device_access"
    assert policy["implementation_status"] == "implemented_and_smoke_tested"
    assert policy["runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert policy["audio_chunk_root"] == "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    assert policy["commands_smoked"] == EXPECTED_COMMANDS
    assert policy["requires_explicit_user_start"] is True
    assert policy["default_uploads_raw_audio"] is False
    assert policy["default_remote_asr_enabled"] is False
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_rust_runtime_boundary_module_is_inert_and_bound_to_lib():
    rust_source = RUST_MODULE_PATH.read_text(encoding="utf-8")
    lib_source = LIB_RS_PATH.read_text(encoding="utf-8")

    assert "pub mod mic_adapter_runtime;" in lib_source
    assert 'MIC_ADAPTER_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested"' in (
        rust_source
    )
    assert 'AUDIO_CHUNK_ROOT: &str = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"' in (
        rust_source
    )
    for command in EXPECTED_COMMANDS:
        assert f'"{command}"' in rust_source

    forbidden_snippets = [
        "cpal",
        "rodio",
        "AVAudioEngine",
        "AudioQueue",
        "CoreAudio",
        "MediaRecorder",
        "getUserMedia",
        "std::fs",
        "File::create",
        "OpenOptions",
        "std::process",
        "Command::new",
        "TcpStream",
        "reqwest",
        "api_key",
        "authorization",
        "configs/local",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in rust_source


def test_tool_source_does_not_execute_mic_audio_network_process_or_model_code():
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
        "wave.open",
        "ffmpeg",
        "afconvert",
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


def test_tool_returns_readiness_gate_compatible_mic_adapter_evidence():
    tool = load_tool_module()
    readiness = load_readiness_module()

    report = tool.build_desktop_mic_adapter_implementation_boundary_report(
        policy_path=POLICY_PATH
    )

    assert report["pcweb_id"] == "PCWEB-121"
    assert report["report_mode"] == "desktop_mic_adapter_implementation_boundary_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["rust_boundary_validation_status"] == "passed"
    assert report["implementation_status"] == "implemented_and_smoke_tested"
    assert report["commands_smoked"] == EXPECTED_COMMANDS
    assert report["runtime_audio_root"] == "artifacts/tmp/desktop_mic_adapter_runtime"
    assert report["audio_chunk_root"] == "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    assert report["requires_explicit_user_start"] is True
    assert report["default_uploads_raw_audio"] is False
    assert report["default_remote_asr_enabled"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    assert readiness._mic_adapter_ready(report) is True


def test_invalid_policy_cannot_enable_audio_or_remote_side_effects():
    tool = load_tool_module()
    bad_policy = load_policy()
    bad_policy["safe_to_capture_audio_now"] = True
    bad_policy["default_uploads_raw_audio"] = True
    bad_policy["commands_smoked"] = EXPECTED_COMMANDS + ["record_to_file"]

    report = tool.build_desktop_mic_adapter_implementation_boundary_report(policy=bad_policy)

    assert report["policy_validation_status"] == "failed"
    assert report["implementation_status"] == "not_accepted"
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "default_uploads_raw_audio must be false" in report["policy_validation_errors"]
    assert "commands_smoked must match PCWEB-121 command list" in (
        report["policy_validation_errors"]
    )
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_mic_adapter_evidence_removes_only_mic_adapter_readiness_blocker():
    tool = load_tool_module()
    readiness = load_readiness_module()
    mic_adapter_evidence = tool.build_desktop_mic_adapter_implementation_boundary_report(
        policy_path=POLICY_PATH
    )

    readiness_report = readiness.build_real_mic_shadow_test_readiness_report(
        mic_adapter_evidence=mic_adapter_evidence
    )

    assert readiness_report["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert readiness_report["readiness_summary"]["mic_adapter_ready"] is True
    assert "mic_adapter_real_implementation_not_available" not in readiness_report["blockers"]
    assert "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval" in (
        readiness_report["blockers"]
    )
    assert "worker_mic_source_not_approved" in readiness_report["blockers"]
    assert "asr_worker_real_mic_source_not_available" in readiness_report["blockers"]

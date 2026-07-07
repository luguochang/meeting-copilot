import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-real-mic-source-boundary.policy.json"
RUST_MODULE_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "asr_worker_mic_source_runtime.rs"
LIB_RS_PATH = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_real_mic_source_boundary.py"
READINESS_TOOL_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_readiness_gate.py"

EXPECTED_COMMANDS = [
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
]
EXPECTED_FALSE_FLAGS = [
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_execute_worker_command_now",
    "safe_to_dispatch_worker_command_now",
    "safe_to_bind_worker_command_transport_now",
    "safe_to_read_event_file_now",
    "safe_to_write_event_file_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_download_public_audio_now",
    "safe_to_run_tauri_or_cargo_now",
]


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_module():
    return load_module(TOOL_PATH, "desktop_asr_worker_real_mic_source_boundary")


def load_readiness_module():
    return load_module(READINESS_TOOL_PATH, "real_mic_shadow_test_readiness_gate")


def load_policy() -> dict:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def test_policy_exists_and_keeps_worker_mic_execution_side_effects_false():
    policy = load_policy()

    assert policy["pcweb_id"] == "PCWEB-122"
    assert policy["policy_name"] == "Desktop ASR Worker Real Mic Source Boundary"
    assert policy["policy_status"] == "desktop_asr_worker_real_mic_source_boundary_policy_only"
    assert policy["implementation_mode"] == "static_worker_mic_source_boundary_no_spawn"
    assert policy["implementation_status"] == "implemented_and_smoke_tested"
    assert policy["source_kind"] == "mic"
    assert policy["event_contract_status"] == "partial_final_revision_error_end_of_stream_supported"
    assert policy["worker_output_root"] == "artifacts/tmp/asr_events"
    assert policy["worker_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert policy["web_handoff_status"] == "closed_to_evidence_state_gap"
    assert policy["command_catalog_smoked"] == EXPECTED_COMMANDS
    assert policy["default_remote_asr_enabled"] is False
    assert policy["default_uploads_raw_audio"] is False
    assert policy["requires_explicit_user_start"] is True
    assert policy["forbidden_roots"] == [
        "configs/local",
        "data/asr_eval/local_samples",
        "data/asr_eval/samples",
        "data/local_runtime",
        "outputs",
    ]
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_rust_worker_mic_source_boundary_module_is_inert_and_bound_to_lib():
    rust_source = RUST_MODULE_PATH.read_text(encoding="utf-8")
    lib_source = LIB_RS_PATH.read_text(encoding="utf-8")

    assert "pub mod asr_worker_mic_source_runtime;" in lib_source
    assert 'ASR_WORKER_MIC_SOURCE_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested"' in (
        rust_source
    )
    assert 'EVENT_CONTRACT_STATUS: &str = "partial_final_revision_error_end_of_stream_supported"' in (
        rust_source
    )
    assert 'WORKER_OUTPUT_ROOT: &str = "artifacts/tmp/asr_events"' in rust_source
    assert 'WEB_HANDOFF_STATUS: &str = "closed_to_evidence_state_gap"' in rust_source
    assert 'SOURCE_KIND: &str = "mic"' in rust_source
    assert "pub fn boundary_evidence() -> AsrWorkerMicSourceBoundaryEvidence" in rust_source
    for command in EXPECTED_COMMANDS:
        assert f'"{command}"' in rust_source

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
        "AVAudioEngine",
        "AudioQueue",
        "CoreAudio",
        "MediaRecorder",
        "getUserMedia",
        "modelscope",
        "AutoModel",
        "api_key",
        "authorization",
        "configs/local",
    ]
    for snippet in forbidden_rust_snippets:
        assert snippet not in rust_source


def test_tool_source_does_not_execute_worker_audio_network_process_or_model_code():
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


def test_tool_returns_readiness_gate_compatible_asr_worker_evidence():
    tool = load_tool_module()
    readiness = load_readiness_module()

    report = tool.build_desktop_asr_worker_real_mic_source_boundary_report(
        policy_path=POLICY_PATH
    )

    assert report["pcweb_id"] == "PCWEB-122"
    assert report["report_mode"] == "desktop_asr_worker_real_mic_source_boundary_static_report"
    assert report["policy_validation_status"] == "passed"
    assert report["rust_boundary_validation_status"] == "passed"
    assert report["implementation_status"] == "implemented_and_smoke_tested"
    assert report["event_contract_status"] == "partial_final_revision_error_end_of_stream_supported"
    assert report["worker_output_root"] == "artifacts/tmp/asr_events"
    assert report["worker_runtime_root"] == "artifacts/tmp/desktop_asr_worker_runtime"
    assert report["web_handoff_status"] == "closed_to_evidence_state_gap"
    assert report["source_kind"] == "mic"
    assert report["command_catalog_smoked"] == EXPECTED_COMMANDS
    assert report["requires_explicit_user_start"] is True
    assert report["default_uploads_raw_audio"] is False
    assert report["default_remote_asr_enabled"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False
    assert readiness._asr_worker_ready(report) is True


def test_invalid_policy_cannot_enable_worker_mic_or_remote_side_effects():
    tool = load_tool_module()
    bad_policy = load_policy()
    bad_policy["safe_to_spawn_worker_now"] = True
    bad_policy["safe_to_capture_audio_now"] = True
    bad_policy["default_remote_asr_enabled"] = True
    bad_policy["worker_output_root"] = "outputs/asr_events"

    report = tool.build_desktop_asr_worker_real_mic_source_boundary_report(policy=bad_policy)

    assert report["policy_validation_status"] == "failed"
    assert report["implementation_status"] == "not_accepted"
    assert "safe_to_spawn_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "default_remote_asr_enabled must be false" in report["policy_validation_errors"]
    assert "worker_output_root must be artifacts/tmp/asr_events" in report["policy_validation_errors"]
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_asr_worker_evidence_removes_only_asr_worker_readiness_blocker():
    tool = load_tool_module()
    readiness = load_readiness_module()
    asr_worker_evidence = tool.build_desktop_asr_worker_real_mic_source_boundary_report(
        policy_path=POLICY_PATH
    )

    readiness_report = readiness.build_real_mic_shadow_test_readiness_report(
        asr_worker_evidence=asr_worker_evidence
    )

    assert readiness_report["readiness_status"] == "blocked_not_ready_for_user_real_mic_shadow_test"
    assert readiness_report["readiness_summary"]["asr_worker_ready"] is True
    assert "asr_worker_real_mic_source_not_available" not in readiness_report["blockers"]
    assert "asr_quality_decision_requires_funasr_model_dir_or_drv019_approval" in (
        readiness_report["blockers"]
    )
    assert "worker_mic_source_not_approved" in readiness_report["blockers"]
    assert "mic_adapter_real_implementation_not_available" in readiness_report["blockers"]


def test_cli_returns_nonzero_when_rust_boundary_validation_fails(tmp_path):
    tool = load_tool_module()
    bad_rust_module = tmp_path / "bad_asr_worker_mic_source_runtime.rs"
    bad_rust_module.write_text(
        'pub const ASR_WORKER_MIC_SOURCE_IMPLEMENTATION_STATUS: &str = "wrong";\n',
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = tool.main(
        [
            "--policy-path",
            str(POLICY_PATH),
            "--rust-module-path",
            str(bad_rust_module),
            "--lib-rs-path",
            str(LIB_RS_PATH),
        ],
        stdout=stdout,
    )
    report = json.loads(stdout.getvalue())

    assert exit_code == 1
    assert report["policy_validation_status"] == "passed"
    assert report["rust_boundary_validation_status"] == "failed"
    assert report["implementation_status"] == "not_accepted"


def test_tool_rust_validator_rejects_forbidden_execution_snippets(tmp_path):
    tool = load_tool_module()
    rust_source = RUST_MODULE_PATH.read_text(encoding="utf-8")
    bad_rust_module = tmp_path / "asr_worker_mic_source_runtime.rs"
    bad_rust_module.write_text(f"{rust_source}\n// forbidden drift: cpal\n", encoding="utf-8")

    report = tool.build_desktop_asr_worker_real_mic_source_boundary_report(
        policy_path=POLICY_PATH,
        rust_module_path=bad_rust_module,
        lib_rs_path=LIB_RS_PATH,
    )

    assert report["policy_validation_status"] == "passed"
    assert report["rust_boundary_validation_status"] == "failed"
    assert "rust boundary contains forbidden snippet: cpal" in (
        report["rust_boundary_validation_errors"]
    )
    assert report["implementation_status"] == "not_accepted"


def test_policy_path_guard_rejects_voice_memos_paths():
    tool = load_tool_module()
    voice_memos_label = "Voice" + "Memos"

    report = tool.build_desktop_asr_worker_real_mic_source_boundary_report(
        policy_path=Path(
            f"/Users/chase/Library/Containers/com.apple.{voice_memos_label}/Data/policy.json"
        )
    )

    assert report["policy_validation_status"] == "blocked_by_policy_path_guard"
    assert f"policy path is blocked: {voice_memos_label}" in report["policy_validation_errors"]

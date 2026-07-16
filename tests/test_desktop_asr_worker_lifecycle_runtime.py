import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
LIB_RS = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"
RUNTIME_RS = DESKTOP_ROOT / "src-tauri" / "src" / "desktop_asr_worker_lifecycle_runtime.rs"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _command_body(source: str, fn_name: str) -> str:
    match = re.search(
        rf"#\[tauri::command\]\s*fn {fn_name}\([^)]*\)[^{{]*\{{(?P<body>.*?)\n\}}",
        source,
        re.S,
    )
    assert match is not None, f"{fn_name} command is missing"
    return match.group("body")


def test_asr_worker_lifecycle_runtime_module_exists_with_safe_roots_and_commands():
    assert RUNTIME_RS.is_file()
    source = _read(RUNTIME_RS)

    required_snippets = [
        'WORKER_RUNTIME_ROOT: &str = "artifacts/tmp/desktop_asr_worker_runtime"',
        'WORKER_EVENT_ROOT: &str = "artifacts/tmp/asr_events"',
        "pub struct AsrWorkerLifecycleResponse",
        "pub fn prepare_worker",
        "pub fn start_worker",
        "pub fn worker_health",
        "pub fn collect_events",
        "pub fn stop_worker",
        "pub fn cleanup_worker",
        "safe_session_id",
        "session_runtime_dir",
        "worker_state_path",
        "event_output_path",
        "write_state",
        "write_synthetic_event_file",
        "count_streaming_events",
    ]
    for snippet in required_snippets:
        assert snippet in source

    forbidden_snippets = [
        "configs/local",
        "data/asr_eval/local_samples",
        "std::process",
        "Command::new",
        "reqwest",
        "TcpStream",
        "sounddevice",
        "cpal",
        "rodio",
        "AVAudioEngine",
        "AudioQueue",
        "api_key",
        "authorization",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_tauri_asr_worker_commands_delegate_to_lifecycle_runtime():
    lib_source = _read(LIB_RS)

    assert "pub mod desktop_asr_worker_lifecycle_runtime;" in lib_source

    for fn_name, runtime_call, command_id in [
        ("asr_worker_prepare", "desktop_asr_worker_lifecycle_runtime::prepare_worker", "worker.prepare"),
        ("asr_worker_start", "desktop_asr_worker_lifecycle_runtime::start_worker", "worker.start"),
        ("asr_worker_health", "desktop_asr_worker_lifecycle_runtime::worker_health", "worker.health"),
        (
            "asr_worker_collect_events",
            "desktop_asr_worker_lifecycle_runtime::collect_events",
            "worker.collect_events",
        ),
        ("asr_worker_stop", "desktop_asr_worker_lifecycle_runtime::stop_worker", "worker.stop"),
        (
            "asr_worker_cleanup",
            "desktop_asr_worker_lifecycle_runtime::cleanup_worker",
            "worker.cleanup",
        ),
    ]:
        body = _command_body(lib_source, fn_name)
        assert runtime_call in body
        assert f'NoopBridgeResponse::for_command("{command_id}")' not in body


def test_asr_worker_lifecycle_response_contract_proves_executable_local_boundary():
    source = _read(RUNTIME_RS)

    for field in [
        "command_id",
        "command_status",
        "implementation_status",
        "worker_lifecycle_status",
        "session_id",
        "runtime_root",
        "worker_state_path",
        "event_output_path",
        "event_file_status",
        "final_event_count",
        "end_of_stream_event_count",
        "safe_to_execute_real_action",
        "captures_audio",
        "spawns_process",
        "calls_remote_provider",
        "reads_user_audio",
        "writes_local_files",
        "errors",
    ]:
        assert field in source

    for snippet in [
        '"local_asr_worker_lifecycle_runtime"',
        '"prepared"',
        '"running"',
        '"stopped"',
        '"cleaned"',
        '"events_written"',
        '"events_collected"',
        "captures_audio: false",
        "spawns_process: false",
        "calls_remote_provider: false",
        "reads_user_audio: false",
        "writes_local_files: true",
    ]:
        assert snippet in source


def test_asr_worker_command_catalog_includes_lifecycle_tauri_commands():
    lib_source = _read(LIB_RS)

    for command_id in [
        "worker.prepare",
        "worker.start",
        "worker.health",
        "worker.collect_events",
        "worker.stop",
        "worker.cleanup",
    ]:
        assert f'"{command_id}"' in lib_source

    handler_match = re.search(r"generate_handler!\s*\\?\[\s*(.*?)\s*\]", lib_source, re.S)
    assert handler_match is not None
    handler_names = {
        name.strip().removeprefix("commands::")
        for name in handler_match.group(1).split(",")
        if name.strip()
    }
    assert {
        "asr_worker_prepare",
        "asr_worker_start",
        "asr_worker_health",
        "asr_worker_collect_events",
        "asr_worker_stop",
        "asr_worker_cleanup",
    }.issubset(handler_names)

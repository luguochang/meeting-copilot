import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
LIB_RS = DESKTOP_ROOT / "src-tauri" / "src" / "lib.rs"
RUNTIME_RS = DESKTOP_ROOT / "src-tauri" / "src" / "desktop_audio_chunk_runtime.rs"
CARGO_TOML = DESKTOP_ROOT / "src-tauri" / "Cargo.toml"


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


def test_audio_chunk_runtime_module_exists_with_session_scoped_safe_root_contract():
    assert RUNTIME_RS.is_file()
    source = _read(RUNTIME_RS)

    required_snippets = [
        'AUDIO_CHUNK_ROOT: &str = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"',
        "pub struct AudioChunkCommandResponse",
        "pub fn prepare_session",
        "pub fn start_recording",
        "pub fn stop_recording",
        "pub fn delete_audio_chunks",
        "safe_session_id",
        "repo_root().join(AUDIO_CHUNK_ROOT)",
        "forbid_path_escape",
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
        "cpal",
        "rodio",
        "AVAudioEngine",
        "AudioQueue",
        "api_key",
        "authorization",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source


def test_tauri_mic_adapter_commands_delegate_to_audio_chunk_runtime_instead_of_noop():
    lib_source = _read(LIB_RS)

    assert "pub mod desktop_audio_chunk_runtime;" in lib_source

    for fn_name, runtime_call, command_id in [
        ("mic_adapter_start", "desktop_audio_chunk_runtime::start_recording", "mic_adapter.start"),
        ("mic_adapter_stop", "desktop_audio_chunk_runtime::stop_recording", "mic_adapter.stop"),
        (
            "mic_adapter_delete_audio_chunks",
            "desktop_audio_chunk_runtime::delete_audio_chunks",
            "mic_adapter.delete_audio_chunks",
        ),
    ]:
        body = _command_body(lib_source, fn_name)
        assert runtime_call in body
        assert f'NoopBridgeResponse::for_command("{command_id}")' not in body


def test_desktop_runtime_response_contract_distinguishes_executable_local_lifecycle():
    lib_source = _read(LIB_RS)
    runtime_source = _read(RUNTIME_RS)

    assert "AudioChunkCommandResponse" in lib_source
    for field in [
        "command_id",
        "command_status",
        "implementation_status",
        "transport_status",
        "side_effect_status",
        "session_id",
        "audio_chunk_dir",
        "audio_chunk_manifest",
        "safe_to_execute_real_action",
        "captures_audio",
        "spawns_process",
        "calls_remote_provider",
        "writes_local_files",
        "errors",
    ]:
        assert field in runtime_source

    assert '"local_audio_chunk_runtime"' in runtime_source
    assert '"session_audio_chunk_dir_created"' in runtime_source
    assert '"session_audio_chunks_deleted"' in runtime_source
    assert "calls_remote_provider: false" in runtime_source
    assert "spawns_process: false" in runtime_source


def test_cargo_manifest_declares_serde_json_for_runtime_manifest_writes():
    manifest = _read(CARGO_TOML)

    assert "serde_json" in manifest

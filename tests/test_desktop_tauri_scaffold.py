import json
import re
import struct
import tomllib
import zlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
TAURI_ROOT = DESKTOP_ROOT / "src-tauri"
WEB_STATIC_ROOT = (
    REPO_ROOT
    / "code"
    / "web_mvp"
    / "backend"
    / "meeting_copilot_web_mvp"
    / "frontend_static"
)


REQUIRED_SCAFFOLD_FILES = (
    "README.md",
    "src-tauri/Cargo.toml",
    "src-tauri/Cargo.lock",
    "src-tauri/build.rs",
    "src-tauri/tauri.conf.json",
    "src-tauri/capabilities/default.json",
    "src-tauri/icons/icon.png",
    "src-tauri/src/main.rs",
    "src-tauri/src/lib.rs",
)

FORBIDDEN_GENERATED_FILE_NAMES = {
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}

FORBIDDEN_GENERATED_DIRECTORY_NAMES = {
    "node_modules",
    "target",
    "dist",
    "bundle",
}

FORBIDDEN_GENERATED_SUFFIXES = (
    ".dmg",
    ".pkg",
    ".msi",
    ".exe",
    ".app",
)

EXPECTED_NOOP_COMMANDS = {
    "runtime_get_status": "runtime.get_status",
    "session_prepare": "session.prepare",
    "asr_worker_health": "asr_worker.health",
    "mic_adapter_prepare": "mic_adapter.prepare",
    "mic_adapter_status": "mic_adapter.status",
    "mic_adapter_start": "mic_adapter.start",
    "mic_adapter_pause": "mic_adapter.pause",
    "mic_adapter_resume": "mic_adapter.resume",
    "mic_adapter_stop": "mic_adapter.stop",
    "mic_adapter_delete_audio_chunks": "mic_adapter.delete_audio_chunks",
}


def _read(relative_path: str) -> str:
    return (DESKTOP_ROOT / relative_path).read_text(encoding="utf-8")


def _tauri_config() -> dict:
    return json.loads((TAURI_ROOT / "tauri.conf.json").read_text(encoding="utf-8"))


def _cargo_manifest() -> dict:
    return tomllib.loads((TAURI_ROOT / "Cargo.toml").read_text(encoding="utf-8"))


def test_required_tauri_scaffold_files_exist_without_generated_artifacts():
    missing = [
        relative_path
        for relative_path in REQUIRED_SCAFFOLD_FILES
        if not (DESKTOP_ROOT / relative_path).is_file()
    ]
    assert missing == []

    forbidden_files = [
        path.relative_to(DESKTOP_ROOT).as_posix()
        for path in DESKTOP_ROOT.rglob("*")
        if path.is_file()
        and (
            path.name in FORBIDDEN_GENERATED_FILE_NAMES
            or path.suffix in FORBIDDEN_GENERATED_SUFFIXES
        )
    ]
    assert forbidden_files == []

    forbidden_directories = [
        path.relative_to(DESKTOP_ROOT).as_posix()
        for path in DESKTOP_ROOT.rglob("*")
        if path.is_dir() and path.name in FORBIDDEN_GENERATED_DIRECTORY_NAMES
    ]
    assert forbidden_directories == []


def test_tauri_config_points_to_existing_web_mvp_and_keeps_spike_safe():
    config = _tauri_config()

    assert config["productName"] == "Meeting Copilot"
    assert config["version"] == "0.1.0"
    assert re.fullmatch(r"com\.meetingcopilot\.desktop", config["identifier"])

    build = config["build"]
    assert build["devUrl"] == "http://127.0.0.1:8765/"
    assert build["beforeDevCommand"] == ""
    assert build["beforeBuildCommand"] == ""
    assert (TAURI_ROOT / build["frontendDist"]).resolve() == WEB_STATIC_ROOT
    assert WEB_STATIC_ROOT.is_dir()

    app = config["app"]
    assert app["withGlobalTauri"] is True
    assert [window["label"] for window in app["windows"]] == ["main"]
    main_window = app["windows"][0]
    assert main_window["title"] == "Meeting Copilot"
    assert main_window["resizable"] is True
    assert main_window["minWidth"] >= 1024
    assert main_window["minHeight"] >= 720

    assert config["bundle"]["active"] is False


def test_tauri_default_icon_exists_for_generate_context_without_bundle_artifacts():
    icon_path = TAURI_ROOT / "icons" / "icon.png"
    icon_bytes = icon_path.read_bytes()

    assert icon_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(icon_bytes) <= 4096

    offset = 8
    width = height = bit_depth = color_type = None
    idat_chunks = []
    while offset < len(icon_bytes):
        chunk_length = struct.unpack(">I", icon_bytes[offset : offset + 4])[0]
        chunk_type = icon_bytes[offset + 4 : offset + 8]
        chunk_data = icon_bytes[offset + 8 : offset + 8 + chunk_length]
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        if chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        offset += 12 + chunk_length

    assert (width, height, bit_depth, color_type) == (32, 32, 8, 6)
    decoded_scanlines = zlib.decompress(b"".join(idat_chunks))
    assert len(decoded_scanlines) == 32 * (1 + 32 * 4)


def test_cargo_manifest_is_tauri_v2_scaffold_without_dependency_lockfiles():
    manifest = _cargo_manifest()

    assert manifest["package"]["name"] == "meeting-copilot-desktop"
    assert manifest["package"]["version"] == "0.1.0"
    assert manifest["package"]["edition"] == "2021"
    assert manifest["lib"]["name"] == "meeting_copilot_desktop_lib"
    assert set(manifest["lib"]["crate-type"]) == {"staticlib", "cdylib", "rlib"}
    assert "tauri" in manifest["dependencies"]
    assert "serde" in manifest["dependencies"]
    assert "tauri-build" in manifest["build-dependencies"]
    assert (TAURI_ROOT / "Cargo.lock").exists()
    assert not (DESKTOP_ROOT / "package.json").exists()


def test_tauri_capability_stays_minimal_for_noop_shell():
    capability = json.loads(
        (TAURI_ROOT / "capabilities" / "default.json").read_text(encoding="utf-8")
    )

    assert capability["windows"] == ["main"]
    assert capability["permissions"] == ["core:default"]
    forbidden_permissions = {"shell", "fs", "process", "http", "dialog", "clipboard"}
    permission_text = "\n".join(capability["permissions"]).lower()
    for permission in forbidden_permissions:
        assert permission not in permission_text


def test_tauri_lib_binds_noop_bridge_and_mic_adapter_commands():
    lib_rs = _read("src-tauri/src/lib.rs")

    public_command_functions = re.findall(r"#\[tauri::command\]\s*pub fn ([a-z0-9_]+)\(", lib_rs)
    assert public_command_functions == []

    command_functions = re.findall(r"#\[tauri::command\]\s*fn ([a-z0-9_]+)\(", lib_rs)
    assert set(command_functions) == set(EXPECTED_NOOP_COMMANDS)

    handler_match = re.search(r"generate_handler!\s*\\?\[\s*(.*?)\s*\]", lib_rs, re.S)
    assert handler_match is not None
    handler_names = {
        name.strip().removeprefix("commands::")
        for name in handler_match.group(1).split(",")
        if name.strip()
    }
    assert handler_names == set(EXPECTED_NOOP_COMMANDS)

    for rust_function, command_id in EXPECTED_NOOP_COMMANDS.items():
        assert rust_function in lib_rs
        assert f'"{command_id}"' in lib_rs

    for mic_function in [
        "mic_adapter_prepare",
        "mic_adapter_status",
        "mic_adapter_start",
        "mic_adapter_pause",
        "mic_adapter_resume",
        "mic_adapter_stop",
        "mic_adapter_delete_audio_chunks",
    ]:
        assert re.search(
            rf"#\[tauri::command\]\s*fn {mic_function}\(\) -> NoopBridgeResponse",
            lib_rs,
        )


def test_noop_bridge_response_contract_declares_no_side_effects():
    lib_rs = _read("src-tauri/src/lib.rs")

    expected_fields = [
        "command_id",
        "command_status",
        "implementation_status",
        "transport_status",
        "side_effect_status",
        "safe_to_invoke_noop",
        "safe_to_execute_real_action",
        "captures_audio",
        "spawns_process",
        "calls_remote_provider",
        "writes_local_files",
    ]
    for field in expected_fields:
        assert field in lib_rs
    assert "pub message:" not in lib_rs
    assert "message:" not in lib_rs

    for expected_value in [
        "noop_bound",
        "noop_only",
        "tauri_ipc_bound",
        "none",
    ]:
        assert f'"{expected_value}"' in lib_rs
    assert "No real desktop action is implemented in PCWEB-082." not in lib_rs

    assert "safe_to_invoke_noop: true" in lib_rs
    assert "safe_to_execute_real_action: false" in lib_rs
    assert "captures_audio: false" in lib_rs
    assert "spawns_process: false" in lib_rs
    assert "calls_remote_provider: false" in lib_rs
    assert "writes_local_files: false" in lib_rs


def test_scaffold_keeps_audio_worker_secret_remote_and_process_boundaries_unbound():
    checked_paths = [
        "src-tauri/Cargo.toml",
        "src-tauri/build.rs",
        "src-tauri/tauri.conf.json",
        "src-tauri/capabilities/default.json",
        "src-tauri/src/main.rs",
        "src-tauri/src/lib.rs",
    ]
    scaffold_text = "\n".join(_read(path) for path in checked_paths)

    forbidden_snippets = [
        "audio_permissions_status",
        "audio_devices_list",
        "audio_capture_start",
        "audio_capture_stop",
        "audio.permissions_status",
        "audio.devices_list",
        "audio.capture_start",
        "audio.capture_stop",
        "getUserMedia",
        "enumerateDevices",
        "MediaRecorder",
        "asr_worker_start",
        "asr_worker.start",
        "Command::new",
        "std::process",
        "tokio::process",
        "std::fs",
        "File::create",
        "OpenOptions",
        "env::var",
        "CoreAudio",
        "coreaudio",
        "ScreenCaptureKit",
        "WASAPI",
        "wasapi",
        "cpal",
        "rodio",
        "keychain",
        "api_key",
        "OPENAI_API_KEY",
        "Authorization",
        "configs/local",
        "reqwest",
        "ureq",
        "hyper",
        "openai",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in scaffold_text

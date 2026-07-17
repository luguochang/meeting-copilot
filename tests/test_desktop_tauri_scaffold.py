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

EXPECTED_BRIDGE_COMMANDS = {
    "runtime_get_status": "runtime.get_status",
    "runtime_write_frontend_probe": "runtime.write_frontend_probe",
    "session_prepare": "session.prepare",
    "asr_worker_prepare": "worker.prepare",
    "asr_worker_start": "worker.start",
    "asr_worker_health": "worker.health",
    "asr_worker_collect_events": "worker.collect_events",
    "asr_worker_stop": "worker.stop",
    "asr_worker_cleanup": "worker.cleanup",
    "mic_adapter_prepare": "mic_adapter.prepare",
    "mic_adapter_status": "mic_adapter.status",
    "mic_adapter_start": "mic_adapter.start",
    "mic_adapter_pause": "mic_adapter.pause",
    "mic_adapter_resume": "mic_adapter.resume",
    "mic_adapter_stop": "mic_adapter.stop",
    "mic_adapter_delete_audio_chunks": "mic_adapter.delete_audio_chunks",
    "provider_config_status": "provider_config.status",
    "provider_config_save": "provider_config.save",
    "provider_config_clear": "provider_config.clear",
}

EXPECTED_NATIVE_MIC_COMMANDS = {
    "mic_adapter_prepare": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
    "mic_adapter_status": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
    "mic_adapter_start": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
    "mic_adapter_pause": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
    "mic_adapter_resume": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
    "mic_adapter_stop": "native_mic_capture_runtime::NativeMicCaptureSupervisor",
}

EXPECTED_AUDIO_CHUNK_RUNTIME_COMMANDS = {
    "mic_adapter_start": "desktop_audio_chunk_runtime::start_recording",
    "mic_adapter_stop": "desktop_audio_chunk_runtime::stop_recording",
    "mic_adapter_delete_audio_chunks": "desktop_audio_chunk_runtime::delete_audio_chunks",
}

EXPECTED_ASR_WORKER_LIFECYCLE_RUNTIME_COMMANDS = {
    "asr_worker_prepare": "desktop_asr_worker_lifecycle_runtime::prepare_worker",
    "asr_worker_start": "desktop_asr_worker_lifecycle_runtime::start_worker",
    "asr_worker_health": "desktop_asr_worker_lifecycle_runtime::worker_health",
    "asr_worker_collect_events": "desktop_asr_worker_lifecycle_runtime::collect_events",
    "asr_worker_stop": "desktop_asr_worker_lifecycle_runtime::stop_worker",
    "asr_worker_cleanup": "desktop_asr_worker_lifecycle_runtime::cleanup_worker",
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


def test_tauri_config_points_to_existing_web_mvp_and_declares_mac_dev_bundle_targets():
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
    assert main_window["visible"] is False
    assert main_window["minWidth"] >= 1024
    assert main_window["minHeight"] >= 720

    bundle = config["bundle"]
    assert bundle["active"] is True
    assert set(bundle["targets"]) == {"app", "dmg"}
    assert bundle["icon"] == ["icons/icon.png"]
    assert bundle["macOS"]["infoPlist"] == "Info.plist"
    assert bundle["macOS"]["minimumSystemVersion"] == "13.0"

    info_plist = (TAURI_ROOT / "Info.plist").read_text(encoding="utf-8")
    assert "NSMicrophoneUsageDescription" in info_plist
    assert "NSAudioCaptureUsageDescription" in info_plist
    assert "NSScreenCaptureUsageDescription" in info_plist


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


def test_static_tauri_capability_stays_local_and_minimal():
    capability = json.loads(
        (TAURI_ROOT / "capabilities" / "default.json").read_text(encoding="utf-8")
    )

    assert capability["windows"] == ["main"]
    assert capability["permissions"] == ["core:default"]
    forbidden_permissions = {"shell", "fs", "process", "http", "dialog", "clipboard"}
    permission_text = "\n".join(capability["permissions"]).lower()
    for permission in forbidden_permissions:
        assert permission not in permission_text


def test_packaged_workbench_gets_exact_runtime_origin_and_command_permissions():
    build_rs = _read("src-tauri/build.rs")
    manifest_rs = _read("src-tauri/src/app_command_manifest.rs")
    lib_rs = _read("src-tauri/src/lib.rs")

    assert "AppManifest::new().commands(app_command_manifest::APP_COMMAND_NAMES)" in build_rs
    assert "CapabilityBuilder::new(\"packaged-loopback-workbench\")" in lib_rs
    assert ".local(false)" in lib_rs
    assert '.window("main")' in lib_rs
    assert ".remote(remote_pattern)" in lib_rs
    assert 'parsed.host_str() != Some("127.0.0.1")' in lib_rs
    assert 'format!("http://127.0.0.1:{port}/*")' in lib_rs
    assert "app.add_capability(packaged_remote_capability(remote_pattern))" in lib_rs
    assert 'format!("allow-{}", command.replace(\'_\', "-"))' in manifest_rs
    for command in EXPECTED_BRIDGE_COMMANDS:
        assert f'"{command}"' in manifest_rs


def test_tauri_lib_binds_bridge_and_mic_adapter_commands():
    lib_rs = _read("src-tauri/src/lib.rs")

    public_command_functions = re.findall(r"#\[tauri::command\]\s*pub fn ([a-z0-9_]+)\(", lib_rs)
    assert public_command_functions == []

    command_functions = re.findall(r"#\[tauri::command\]\s*fn ([a-z0-9_]+)\(", lib_rs)
    assert set(command_functions) == set(EXPECTED_BRIDGE_COMMANDS)

    handler_match = re.search(r"generate_handler!\s*\\?\[\s*(.*?)\s*\]", lib_rs, re.S)
    assert handler_match is not None
    handler_names = {
        name.strip().removeprefix("commands::")
        for name in handler_match.group(1).split(",")
        if name.strip()
    }
    assert handler_names == set(EXPECTED_BRIDGE_COMMANDS)

    for rust_function, command_id in EXPECTED_BRIDGE_COMMANDS.items():
        assert rust_function in lib_rs
        assert f'"{command_id}"' in lib_rs

    for mic_function, runtime_type in EXPECTED_NATIVE_MIC_COMMANDS.items():
        assert runtime_type in lib_rs

    assert "microphone.prepare()" in lib_rs
    assert "microphone.status()" in lib_rs
    assert "microphone.start(session_id, &backend)" in lib_rs
    assert "microphone.pause()" in lib_rs
    assert "microphone.resume()" in lib_rs
    assert "microphone.stop()" in lib_rs

    for worker_function, runtime_call in EXPECTED_ASR_WORKER_LIFECYCLE_RUNTIME_COMMANDS.items():
        assert re.search(
            rf"#\[tauri::command\]\s*fn {worker_function}\(\s*session_id: Option<String>,?\s*\) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse",
            lib_rs,
        )
        assert runtime_call in lib_rs


def test_tauri_runtime_status_exposes_packaged_same_chain_probe_flag():
    lib_rs = _read("src-tauri/src/lib.rs")

    assert "packaged_same_chain_probe_enabled" in lib_rs
    assert "MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE" in lib_rs


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


def test_desktop_frontend_probe_command_is_bound_to_safe_artifact_runtime():
    lib_rs = _read("src-tauri/src/lib.rs")
    probe_rs = _read("src-tauri/src/desktop_frontend_probe_runtime.rs")

    assert "runtime.write_frontend_probe" in lib_rs
    assert "runtime_write_frontend_probe" in lib_rs
    assert "desktop_frontend_probe_runtime::write_frontend_probe" in lib_rs
    assert "runtime_write_frontend_probe" in lib_rs.split("generate_handler!")[1]
    assert "FRONTEND_PROBE_ROOT" in probe_rs
    assert "artifacts/tmp/desktop_frontend_probe_runtime" in probe_rs
    assert "latest-page-load.json" in probe_rs
    assert "latest-inline-dom.json" in probe_rs
    assert "latest-workbench-runtime.json" in probe_rs
    assert "serde_json::to_string_pretty" in probe_rs
    assert "calls_remote_provider: false" in probe_rs
    assert "captures_audio: false" in probe_rs


def test_desktop_frontend_probe_records_packaged_page_load_from_rust_side():
    lib_rs = _read("src-tauri/src/lib.rs")

    assert ".on_page_load(" in lib_rs
    assert "rust_page_load_probe" in lib_rs
    assert "PageLoadEvent::Finished" in lib_rs
    assert "desktop_frontend_probe_runtime::write_frontend_probe" in lib_rs


def test_scaffold_binds_local_backend_supervisor_without_general_remote_client_dependencies():
    checked_paths = [
        "src-tauri/Cargo.toml",
        "src-tauri/build.rs",
        "src-tauri/tauri.conf.json",
        "src-tauri/capabilities/default.json",
        "src-tauri/src/main.rs",
        "src-tauri/src/lib.rs",
        "src-tauri/src/desktop_backend_supervisor.rs",
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
        "asr_worker.start",
        "tokio::process",
        "File::create",
        "CoreAudio",
        "coreaudio",
        "ScreenCaptureKit",
        "WASAPI",
        "wasapi",
        "cpal",
        "rodio",
        "keychain",
        "OPENAI_API_KEY",
        "configs/local",
        "reqwest",
        "ureq",
        "hyper",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in scaffold_text

    for required in [
        "BackendSupervisor",
        "start_packaged",
        "reserve_loopback_port",
        "wait_for_health",
        "stop_child_process_group",
        'Command::new("/bin/sh")',
        'env_remove("PYTHONPATH")',
        "RunEvent::Exit",
        "WindowEvent::CloseRequested",
        'navigate(url)',
    ]:
        assert required in scaffold_text

    env_reads = set(re.findall(r'env::var\("([^"]+)"\)', scaffold_text))
    assert env_reads == {
        "MEETING_COPILOT_DESKTOP_API_BASE_URL",
        "MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE",
    }

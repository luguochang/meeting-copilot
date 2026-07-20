from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TAURI_LIB = REPO_ROOT / "code" / "desktop_tauri" / "src-tauri" / "src" / "lib.rs"
TAURI_COMMANDS = (
    REPO_ROOT
    / "code"
    / "desktop_tauri"
    / "src-tauri"
    / "src"
    / "app_command_manifest.rs"
)
LEGACY_RUNTIME = (
    REPO_ROOT
    / "code"
    / "desktop_tauri"
    / "src-tauri"
    / "src"
    / "desktop_audio_chunk_runtime.rs"
)
FRONTEND_CLIENT = REPO_ROOT / "code" / "web_mvp" / "frontend_v2" / "src" / "api" / "client.ts"
BACKEND_APP = (
    REPO_ROOT
    / "code"
    / "web_mvp"
    / "backend"
    / "meeting_copilot_web_mvp"
    / "app.py"
)


def test_backend_v2_is_the_only_product_audio_lifecycle_owner() -> None:
    tauri_source = TAURI_LIB.read_text(encoding="utf-8")
    command_source = TAURI_COMMANDS.read_text(encoding="utf-8")
    frontend_source = FRONTEND_CLIENT.read_text(encoding="utf-8")
    backend_source = BACKEND_APP.read_text(encoding="utf-8")

    assert not LEGACY_RUNTIME.exists()
    assert "desktop_audio_chunk_runtime" not in tauri_source
    assert "mic_adapter_delete_audio_chunks" not in tauri_source
    assert "mic_adapter_delete_audio_chunks" not in command_source

    assert 'method: "DELETE"' in frontend_source
    assert '`/v2/meetings/${encodeURIComponent(meetingId)}`' in frontend_source
    assert '@app.delete("/v2/meetings/{meeting_id}")' in backend_source
    assert "audio_assets.delete_audio_asset" in backend_source
    assert "complete_deletion_and_purge" in backend_source


def test_native_microphone_only_streams_to_the_backend_canonical_chain() -> None:
    tauri_source = TAURI_LIB.read_text(encoding="utf-8")

    assert "native_mic_capture_runtime::NativeMicCaptureSupervisor" in tauri_source
    assert "microphone.start_with_epoch(" in tauri_source
    assert "microphone.stop_for_session(session_id.as_deref())" in tauri_source


def test_native_dual_track_capture_keeps_two_named_loopback_tracks() -> None:
    tauri_source = TAURI_LIB.read_text(encoding="utf-8")
    command_source = TAURI_COMMANDS.read_text(encoding="utf-8")

    assert "DualTrackCaptureCoordinator" in tauri_source
    assert "claim_dual_track" in tauri_source
    assert "CaptureTrack::Microphone" in tauri_source
    assert "CaptureTrack::SystemAudio" in tauri_source
    assert 'requested_mode: "dual_track"' in tauri_source
    assert "mixed_audio_created: false" in tauri_source
    assert "raw_audio_uploaded: false" in tauri_source
    assert '"dual_track_adapter_collect_events"' in command_source
    assert '"mic_adapter_cleanup"' in command_source
    assert '"system_audio_adapter_cleanup"' in command_source

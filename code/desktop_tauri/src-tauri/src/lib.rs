use serde::Serialize;
use serde_json::json;
use std::env;
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use tauri::ipc::CapabilityBuilder;
use tauri::webview::PageLoadEvent;
use tauri::Manager;

pub mod app_command_manifest;
pub mod asr_worker_mic_source_runtime;
pub mod desktop_asr_worker_lifecycle_runtime;
pub mod desktop_audio_adapter_runtime;
pub mod desktop_backend_supervisor;
pub mod desktop_frontend_probe_runtime;
pub mod mic_adapter_runtime;
pub mod native_mic_capture_runtime;
pub mod native_system_audio_capture_runtime;
pub mod private_storage;
pub mod provider_config_runtime;
#[cfg(windows)]
pub mod windows_audio_capture_runtime;

const STORAGE_DIR_ENV: &str = "MEETING_COPILOT_STORAGE_DIR";

fn resolve_storage_root(
    configured: Option<OsString>,
    local_default: &Path,
    legacy_default: &Path,
) -> Result<PathBuf, String> {
    if let Some(value) = configured.filter(|value| !value.is_empty()) {
        let path = PathBuf::from(value);
        if !path.is_absolute() {
            return Err(format!("{STORAGE_DIR_ENV} must be an absolute path"));
        }
        return Ok(path);
    }
    if local_default != legacy_default && !local_default.exists() && legacy_default.exists() {
        return Ok(legacy_default.to_path_buf());
    }
    Ok(local_default.to_path_buf())
}

#[tauri::command]
fn windows_audio_devices(flow: String) -> Result<serde_json::Value, String> {
    #[cfg(windows)]
    {
        let parsed = windows_audio_capture_runtime::parse_flow(&flow)?;
        let response = windows_audio_capture_runtime::enumerate_devices(parsed)?;
        return serde_json::to_value(response)
            .map_err(|error| format!("Windows audio device response encoding failed: {error}"));
    }
    #[cfg(not(windows))]
    {
        let _ = flow;
        Err("Windows audio device enumeration is unavailable on this platform".to_string())
    }
}

pub const BRIDGE_COMMAND_IDS: &[&str] = &[
    "runtime.get_status",
    "runtime.write_frontend_probe",
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
    "mic_adapter.prepare",
    "mic_adapter.probe",
    "mic_adapter.status",
    "mic_adapter.collect_events",
    "mic_adapter.start",
    "mic_adapter.pause",
    "mic_adapter.resume",
    "mic_adapter.stop",
    "mic_adapter.cleanup",
    "windows.audio_devices",
    "system_audio_adapter.prepare",
    "system_audio_adapter.status",
    "system_audio_adapter.collect_events",
    "system_audio_adapter.start",
    "system_audio_adapter.stop",
    "system_audio_adapter.cleanup",
    "dual_track_adapter.start",
    "dual_track_adapter.status",
    "dual_track_adapter.collect_events",
    "dual_track_adapter.stop",
    "dual_track_adapter.cleanup",
    "provider_config.status",
    "provider_config.sync",
    "provider_config.save",
    "provider_config.clear",
];

fn packaged_remote_pattern(workbench_url: &str) -> Result<String, String> {
    let parsed = tauri::Url::parse(workbench_url)
        .map_err(|error| format!("invalid packaged workbench URL: {error}"))?;
    if parsed.scheme() != "http" || parsed.host_str() != Some("127.0.0.1") {
        return Err("packaged workbench must use an explicit 127.0.0.1 HTTP origin".to_string());
    }
    let port = parsed.port().ok_or_else(|| {
        "packaged workbench URL must include its random loopback port".to_string()
    })?;
    Ok(format!("http://127.0.0.1:{port}/*"))
}

fn packaged_remote_capability(remote_pattern: String) -> CapabilityBuilder {
    app_command_manifest::APP_COMMAND_NAMES.iter().fold(
        CapabilityBuilder::new("packaged-loopback-workbench")
            .local(false)
            .window("main")
            .remote(remote_pattern),
        |capability, command| capability.permission(app_command_manifest::permission_name(command)),
    )
}

#[derive(Debug, Clone, Serialize)]
pub struct RuntimeStatusResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub spawns_process: bool,
    pub writes_local_files: bool,
    pub desktop_api_base_url: Option<String>,
    pub backend_runtime_status: String,
    pub backend_runtime_mode: String,
    pub backend_pid: Option<u32>,
    pub backend_port: Option<u16>,
    pub file_asr_status: String,
    pub file_asr_available: bool,
    pub file_asr_missing_components: Vec<String>,
    pub packaged_same_chain_probe_enabled: bool,
}

#[tauri::command]
fn runtime_get_status(
    supervisor: tauri::State<'_, desktop_backend_supervisor::BackendSupervisor>,
) -> RuntimeStatusResponse {
    let backend = supervisor.snapshot();
    RuntimeStatusResponse {
        command_id: "runtime.get_status",
        command_status: "ok",
        implementation_status: "real",
        spawns_process: backend.spawns_process,
        writes_local_files: backend.spawns_process,
        desktop_api_base_url: backend.base_url,
        backend_runtime_status: backend.status,
        backend_runtime_mode: backend.mode,
        backend_pid: backend.pid,
        backend_port: backend.port,
        file_asr_status: backend.file_asr_status,
        file_asr_available: backend.file_asr_available,
        file_asr_missing_components: backend.file_asr_missing_components,
        packaged_same_chain_probe_enabled: env::var("MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE")
            .map(|value| {
                matches!(
                    value.trim().to_ascii_lowercase().as_str(),
                    "1" | "true" | "yes" | "on"
                )
            })
            .unwrap_or(false),
    }
}

#[tauri::command]
fn runtime_write_frontend_probe(
    payload: serde_json::Value,
) -> desktop_frontend_probe_runtime::FrontendProbeResponse {
    desktop_frontend_probe_runtime::write_frontend_probe(payload)
}

#[tauri::command]
fn asr_worker_prepare(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::prepare_worker(session_id)
}

#[tauri::command]
fn asr_worker_start(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::start_worker(session_id)
}

#[tauri::command]
fn asr_worker_health(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::worker_health(session_id)
}

#[tauri::command]
fn asr_worker_collect_events(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::collect_events(session_id)
}

#[tauri::command]
fn asr_worker_stop(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::stop_worker(session_id)
}

#[tauri::command]
fn asr_worker_cleanup(
    session_id: Option<String>,
) -> desktop_asr_worker_lifecycle_runtime::AsrWorkerLifecycleResponse {
    desktop_asr_worker_lifecycle_runtime::cleanup_worker(session_id)
}

#[derive(Debug, Clone, Serialize)]
pub struct DualTrackCaptureResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub status: &'static str,
    pub requested_mode: &'static str,
    pub active_mode: &'static str,
    pub session_id: Option<String>,
    pub active_track_count: u8,
    pub mixed_audio_created: bool,
    pub raw_audio_uploaded: bool,
    pub microphone: native_mic_capture_runtime::NativeMicCaptureResponse,
    pub system_audio: native_system_audio_capture_runtime::SystemAudioCaptureResponse,
    pub coordinator: native_system_audio_capture_runtime::DualTrackCoordinatorSnapshot,
}

#[derive(Debug, Clone, Serialize)]
pub struct DualTrackEventsResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub requested_mode: &'static str,
    pub session_id: Option<String>,
    pub mixed_audio_created: bool,
    pub raw_audio_uploaded: bool,
    pub microphone: native_mic_capture_runtime::NativeMicEventsResponse,
    pub system_audio: native_system_audio_capture_runtime::SystemAudioEventsResponse,
}

fn release_microphone_lease(
    coordinator: &native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    response: &native_mic_capture_runtime::NativeMicCaptureResponse,
) -> bool {
    match (response.session_id.as_deref(), response.capture_epoch) {
        (Some(session_id), Some(capture_epoch)) => coordinator.release_track(
            native_system_audio_capture_runtime::CaptureTrack::Microphone,
            session_id,
            capture_epoch,
        ),
        _ => false,
    }
}

fn release_system_audio_lease(
    coordinator: &native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    response: &native_system_audio_capture_runtime::SystemAudioCaptureResponse,
) -> bool {
    match (response.session_id.as_deref(), response.capture_epoch) {
        (Some(session_id), Some(capture_epoch)) => coordinator.release_track(
            native_system_audio_capture_runtime::CaptureTrack::SystemAudio,
            session_id,
            capture_epoch,
        ),
        _ => false,
    }
}

fn reconcile_capture_coordinator(
    coordinator: &native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    audio: &desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters,
) {
    let microphone_status = audio.microphone_status();
    if !matches!(microphone_status.status, "recording" | "paused") {
        release_microphone_lease(coordinator, &microphone_status);
    }
    let system_audio_status = audio.system_audio_status();
    if system_audio_status.status != "recording" {
        release_system_audio_lease(coordinator, &system_audio_status);
    }
}

fn dual_track_capture_response(
    command_id: &'static str,
    session_id: Option<String>,
    microphone: native_mic_capture_runtime::NativeMicCaptureResponse,
    system_audio: native_system_audio_capture_runtime::SystemAudioCaptureResponse,
    coordinator: native_system_audio_capture_runtime::DualTrackCoordinatorSnapshot,
) -> DualTrackCaptureResponse {
    let successful_commands =
        u8::from(microphone.command_status == "ok") + u8::from(system_audio.command_status == "ok");
    let command_status = match successful_commands {
        2 => "ok",
        1 => "partial",
        _ => "blocked",
    };
    let status = match command_id {
        "dual_track_adapter.start" | "dual_track_adapter.status" => {
            match coordinator.active_track_count {
                2 => "recording",
                1 => "degraded_single_track",
                _ => "not_recording",
            }
        }
        "dual_track_adapter.stop" => {
            if successful_commands == 2 {
                "stopped"
            } else {
                "stop_incomplete"
            }
        }
        "dual_track_adapter.cleanup" => {
            if successful_commands == 2 {
                "cleaned"
            } else {
                "cleanup_incomplete"
            }
        }
        _ => "unknown",
    };
    DualTrackCaptureResponse {
        command_id,
        command_status,
        status,
        requested_mode: "dual_track",
        active_mode: coordinator.mode,
        session_id: session_id.or_else(|| coordinator.session_id.clone()),
        active_track_count: coordinator.active_track_count,
        mixed_audio_created: false,
        raw_audio_uploaded: false,
        microphone,
        system_audio,
        coordinator,
    }
}

#[tauri::command]
fn mic_adapter_prepare(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    audio.microphone_prepare()
}

#[tauri::command]
async fn mic_adapter_probe(
    app: tauri::AppHandle,
    device_id: Option<String>,
) -> Result<native_mic_capture_runtime::NativeMicProbeResponse, String> {
    // The explicit preflight click owns the permission request and 2.5-second sample.
    tauri::async_runtime::spawn_blocking(move || {
        let coordinator =
            app.state::<native_system_audio_capture_runtime::DualTrackCaptureCoordinator>();
        let audio = app.state::<desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>();
        reconcile_capture_coordinator(&coordinator, &audio);
        coordinator.claim_microphone_probe()?;
        let response = audio.microphone_probe(device_id.as_deref());
        coordinator.release_microphone_probe();
        Ok(response)
    })
    .await
    .map_err(|error| format!("native microphone probe task failed: {error}"))?
}

#[tauri::command]
fn mic_adapter_status(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    let response = audio.microphone_status();
    if !matches!(response.status, "recording" | "paused") {
        release_microphone_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
fn mic_adapter_collect_events(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_mic_capture_runtime::NativeMicEventsResponse {
    audio.microphone_events(session_id)
}

#[tauri::command]
async fn mic_adapter_start(
    app: tauri::AppHandle,
    session_id: Option<String>,
    device_id: Option<String>,
) -> Result<native_mic_capture_runtime::NativeMicCaptureResponse, String> {
    // The helper startup waits for macOS permission, WebSocket readiness, and audio setup.
    // Keep that blocking work off the Tauri UI command path so the window remains responsive.
    tauri::async_runtime::spawn_blocking(move || {
        let requested_session = session_id.clone().unwrap_or_default();
        let coordinator =
            app.state::<native_system_audio_capture_runtime::DualTrackCaptureCoordinator>();
        let audio = app.state::<desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>();
        reconcile_capture_coordinator(&coordinator, &audio);
        let lease = coordinator.claim_track(
            native_system_audio_capture_runtime::CaptureTrack::Microphone,
            &requested_session,
        )?;
        let backend = app.state::<desktop_backend_supervisor::BackendSupervisor>();
        let response =
            audio.microphone_start(session_id, Some(lease.capture_epoch), device_id, &backend);
        if response.command_status != "ok" {
            coordinator.release_track(
                native_system_audio_capture_runtime::CaptureTrack::Microphone,
                &lease.session_id,
                lease.capture_epoch,
            );
        }
        Ok(response)
    })
    .await
    .map_err(|error| format!("native microphone startup task failed: {error}"))?
}

#[tauri::command]
fn mic_adapter_pause(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    audio.microphone_pause()
}

#[tauri::command]
fn mic_adapter_resume(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    audio.microphone_resume()
}

#[tauri::command]
fn mic_adapter_stop(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    let response = audio.microphone_stop(session_id.as_deref());
    if response.command_status == "ok" && !matches!(response.status, "recording" | "paused") {
        release_microphone_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
fn mic_adapter_cleanup(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_mic_capture_runtime::NativeMicCaptureResponse {
    let response = audio.microphone_cleanup(session_id.as_deref());
    if response.command_status == "ok" {
        release_microphone_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
fn system_audio_adapter_prepare(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_system_audio_capture_runtime::SystemAudioCaptureResponse {
    audio.system_audio_prepare()
}

#[tauri::command]
fn system_audio_adapter_status(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_system_audio_capture_runtime::SystemAudioCaptureResponse {
    let response = audio.system_audio_status();
    if !audio.system_audio_is_active() {
        release_system_audio_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
fn system_audio_adapter_collect_events(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> native_system_audio_capture_runtime::SystemAudioEventsResponse {
    audio.system_audio_events(session_id)
}

#[tauri::command]
async fn system_audio_adapter_start(
    app: tauri::AppHandle,
    session_id: Option<String>,
    display_id: Option<u32>,
    request_permission: Option<bool>,
    device_id: Option<String>,
) -> Result<native_system_audio_capture_runtime::SystemAudioCaptureResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let requested_session = session_id.clone().unwrap_or_default();
        let audio = app.state::<desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>();
        let coordinator =
            app.state::<native_system_audio_capture_runtime::DualTrackCaptureCoordinator>();
        reconcile_capture_coordinator(&coordinator, &audio);
        let lease = coordinator.claim_track(
            native_system_audio_capture_runtime::CaptureTrack::SystemAudio,
            &requested_session,
        )?;
        let backend = app.state::<desktop_backend_supervisor::BackendSupervisor>();
        let response = audio.system_audio_start(
            session_id,
            display_id,
            request_permission.unwrap_or(false),
            Some(lease.capture_epoch),
            device_id,
            &backend,
        );
        if response.command_status != "ok" {
            coordinator.release_track(
                native_system_audio_capture_runtime::CaptureTrack::SystemAudio,
                &lease.session_id,
                lease.capture_epoch,
            );
        }
        Ok(response)
    })
    .await
    .map_err(|error| format!("native system audio startup task failed: {error}"))?
}

#[tauri::command]
fn system_audio_adapter_stop(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_system_audio_capture_runtime::SystemAudioCaptureResponse {
    let response = audio.system_audio_stop(session_id.as_deref());
    if response.command_status == "ok" && !audio.system_audio_is_active() {
        release_system_audio_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
fn system_audio_adapter_cleanup(
    session_id: Option<String>,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> native_system_audio_capture_runtime::SystemAudioCaptureResponse {
    let response = audio.system_audio_cleanup(session_id.as_deref());
    if response.command_status == "ok" {
        release_system_audio_lease(&capture_coordinator, &response);
    }
    response
}

#[tauri::command]
async fn dual_track_adapter_start(
    app: tauri::AppHandle,
    session_id: String,
    display_id: Option<u32>,
    request_system_audio_permission: bool,
    microphone_device_id: Option<String>,
    system_audio_device_id: Option<String>,
) -> Result<DualTrackCaptureResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let audio = app.state::<desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>();
        let coordinator =
            app.state::<native_system_audio_capture_runtime::DualTrackCaptureCoordinator>();
        reconcile_capture_coordinator(&coordinator, &audio);
        let leases = coordinator.claim_dual_track(&session_id)?;
        let backend = app.state::<desktop_backend_supervisor::BackendSupervisor>();

        let microphone_response = audio.microphone_start(
            Some(session_id.clone()),
            Some(leases.microphone.capture_epoch),
            microphone_device_id,
            &backend,
        );
        let system_audio_response = audio.system_audio_start(
            Some(session_id.clone()),
            display_id,
            request_system_audio_permission,
            Some(leases.system_audio.capture_epoch),
            system_audio_device_id,
            &backend,
        );
        if microphone_response.command_status != "ok" {
            coordinator.release_track(
                native_system_audio_capture_runtime::CaptureTrack::Microphone,
                &session_id,
                leases.microphone.capture_epoch,
            );
        }
        if system_audio_response.command_status != "ok" {
            coordinator.release_track(
                native_system_audio_capture_runtime::CaptureTrack::SystemAudio,
                &session_id,
                leases.system_audio.capture_epoch,
            );
        }
        Ok(dual_track_capture_response(
            "dual_track_adapter.start",
            Some(session_id),
            microphone_response,
            system_audio_response,
            coordinator.snapshot(),
        ))
    })
    .await
    .map_err(|error| format!("native dual-track startup task failed: {error}"))?
}

#[tauri::command]
fn dual_track_adapter_status(
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> DualTrackCaptureResponse {
    reconcile_capture_coordinator(&capture_coordinator, &audio);
    let coordinator = capture_coordinator.snapshot();
    dual_track_capture_response(
        "dual_track_adapter.status",
        coordinator.session_id.clone(),
        audio.microphone_status(),
        audio.system_audio_status(),
        coordinator,
    )
}

#[tauri::command]
fn dual_track_adapter_collect_events(
    session_id: String,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
) -> DualTrackEventsResponse {
    let microphone = audio.microphone_events(Some(session_id.clone()));
    let system_audio = audio.system_audio_events(Some(session_id.clone()));
    let successful_commands =
        u8::from(microphone.command_status == "ok") + u8::from(system_audio.command_status == "ok");
    DualTrackEventsResponse {
        command_id: "dual_track_adapter.collect_events",
        command_status: match successful_commands {
            2 => "ok",
            1 => "partial",
            _ => "blocked",
        },
        requested_mode: "dual_track",
        session_id: Some(session_id),
        mixed_audio_created: false,
        raw_audio_uploaded: false,
        microphone,
        system_audio,
    }
}

#[tauri::command]
fn dual_track_adapter_stop(
    session_id: String,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> DualTrackCaptureResponse {
    let microphone_response = audio.microphone_stop(Some(&session_id));
    let system_audio_response = audio.system_audio_stop(Some(&session_id));
    if microphone_response.command_status == "ok" {
        release_microphone_lease(&capture_coordinator, &microphone_response);
    }
    if system_audio_response.command_status == "ok" {
        release_system_audio_lease(&capture_coordinator, &system_audio_response);
    }
    dual_track_capture_response(
        "dual_track_adapter.stop",
        Some(session_id),
        microphone_response,
        system_audio_response,
        capture_coordinator.snapshot(),
    )
}

#[tauri::command]
fn dual_track_adapter_cleanup(
    session_id: String,
    audio: tauri::State<'_, desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>,
    capture_coordinator: tauri::State<
        '_,
        native_system_audio_capture_runtime::DualTrackCaptureCoordinator,
    >,
) -> DualTrackCaptureResponse {
    let microphone_response = audio.microphone_cleanup(Some(&session_id));
    let system_audio_response = audio.system_audio_cleanup(Some(&session_id));
    if microphone_response.command_status == "ok" {
        release_microphone_lease(&capture_coordinator, &microphone_response);
    }
    if system_audio_response.command_status == "ok" {
        release_system_audio_lease(&capture_coordinator, &system_audio_response);
    }
    dual_track_capture_response(
        "dual_track_adapter.cleanup",
        Some(session_id),
        microphone_response,
        system_audio_response,
        capture_coordinator.snapshot(),
    )
}

#[tauri::command]
fn provider_config_status(
    provider: tauri::State<'_, provider_config_runtime::ProviderConfigSupervisor>,
) -> provider_config_runtime::ProviderConfigResponse {
    provider.status()
}

#[tauri::command]
async fn provider_config_sync(
    app: tauri::AppHandle,
) -> Result<provider_config_runtime::ProviderConfigResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let provider = app.state::<provider_config_runtime::ProviderConfigSupervisor>();
        let backend = app.state::<desktop_backend_supervisor::BackendSupervisor>();
        let _ = provider.sync(&backend);
        provider.status()
    })
    .await
    .map_err(|error| format!("AI 配置后台同步失败: {error}"))
}

#[tauri::command]
async fn provider_config_save(
    app: tauri::AppHandle,
    base_url: String,
    api_key: String,
    model: String,
    realtime_model: Option<String>,
    api_style: String,
) -> Result<provider_config_runtime::ProviderConfigResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let provider = app.state::<provider_config_runtime::ProviderConfigSupervisor>();
        provider.save(base_url, api_key, model, realtime_model, api_style)
    })
    .await
    .map_err(|error| format!("AI 配置后台保存失败: {error}"))
}

#[tauri::command]
async fn provider_config_clear(
    app: tauri::AppHandle,
) -> Result<provider_config_runtime::ProviderConfigResponse, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let provider = app.state::<provider_config_runtime::ProviderConfigSupervisor>();
        let backend = app.state::<desktop_backend_supervisor::BackendSupervisor>();
        provider.clear(&backend)
    })
    .await
    .map_err(|error| format!("AI 配置后台清除失败: {error}"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum ProviderStartupSyncPolicy {
    ExplicitConnectionOnly,
}

fn provider_startup_sync_policy(
    _legacy_auto_sync_setting: Option<&str>,
) -> ProviderStartupSyncPolicy {
    ProviderStartupSyncPolicy::ExplicitConnectionOnly
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let supervisor = desktop_backend_supervisor::BackendSupervisor::default();
            let resource_dir = app.path().resource_dir()?;
            let local_app_data_dir = app.path().app_local_data_dir()?;
            let legacy_app_data_dir = app.path().app_data_dir()?;
            let app_data_dir = resolve_storage_root(
                env::var_os(STORAGE_DIR_ENV),
                &local_app_data_dir,
                &legacy_app_data_dir,
            )
            .map_err(std::io::Error::other)?;
            private_storage::ensure_private_directory(&app_data_dir)?;
            let app_log_dir = app_data_dir.join("logs");
            private_storage::ensure_private_directory(&app_log_dir)?;
            let provider_config =
                provider_config_runtime::ProviderConfigSupervisor::new(app_data_dir.clone());
            let provider_startup_sync_policy = provider_startup_sync_policy(
                env::var("MEETING_COPILOT_AUTO_SYNC_PROVIDER")
                    .ok()
                    .as_deref(),
            );
            let runtime_override = env::var_os("MEETING_COPILOT_RUNTIME_BUNDLE").map(PathBuf::from);
            let mut runtime_bundle = desktop_backend_supervisor::resolve_capability_aware_runtime_bundle(
                &resource_dir,
                &app_data_dir,
                runtime_override.as_deref(),
            );
            if runtime_bundle.is_dir() {
                let start_result = supervisor.start_packaged(
                    &runtime_bundle,
                    &app_data_dir.join("runtime-data"),
                    &app_log_dir,
                );
                if let Err(active_error) = start_result {
                    let bundled_runtime = desktop_backend_supervisor::resolve_runtime_bundle(
                        &resource_dir,
                        None,
                    );
                    let can_fallback = runtime_override.is_none()
                        && runtime_bundle != bundled_runtime
                        && bundled_runtime.is_dir();
                    if !can_fallback {
                        return Err(std::io::Error::other(active_error).into());
                    }
                    supervisor
                        .start_packaged(
                            &bundled_runtime,
                            &app_data_dir.join("runtime-data"),
                            &app_log_dir,
                        )
                        .map_err(|fallback_error| {
                            std::io::Error::other(format!(
                                "active capability runtime failed: {active_error}; bundled runtime fallback failed: {fallback_error}"
                            ))
                        })?;
                    runtime_bundle = bundled_runtime;
                }
            } else if cfg!(debug_assertions) {
                let base_url = env::var("MEETING_COPILOT_DESKTOP_API_BASE_URL")
                    .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
                supervisor.use_external(base_url);
            } else {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    format!(
                        "packaged runtime is missing at {}",
                        runtime_bundle.display()
                    ),
                )
                .into());
            }
            let audio_adapters = desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters::new(
                runtime_bundle.join("bin/meeting-copilot-native-mic"),
                app_data_dir.join("native-mic"),
                runtime_bundle.join("bin/meeting-copilot-native-system-audio"),
                app_data_dir.join("native-system-audio"),
                app_log_dir,
            );
            let capture_coordinator =
                native_system_audio_capture_runtime::DualTrackCaptureCoordinator::default();
            let workbench_url = supervisor.workbench_url().map_err(std::io::Error::other)?;
            let remote_pattern =
                packaged_remote_pattern(&workbench_url).map_err(std::io::Error::other)?;
            app.add_capability(packaged_remote_capability(remote_pattern))?;
            app.manage(supervisor);
            app.manage(audio_adapters);
            app.manage(capture_coordinator);
            app.manage(provider_config);
            let window = app
                .get_webview_window("main")
                .ok_or_else(|| std::io::Error::other("main webview window is missing"))?;
            let url = tauri::Url::parse(&workbench_url)?;
            window.navigate(url)?;
            window.show()?;
            match provider_startup_sync_policy {
                ProviderStartupSyncPolicy::ExplicitConnectionOnly => {}
            }
            Ok(())
        })
        .on_page_load(|_webview, payload| {
            if matches!(payload.event(), PageLoadEvent::Finished) {
                let _ = desktop_frontend_probe_runtime::write_frontend_probe(json!({
                    "rust_page_load_probe": true,
                    "page_load_event": "finished",
                    "url": payload.url().to_string(),
                    "captures_audio": false,
                    "calls_remote_provider": false,
                }));
            }
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                window.app_handle().exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![
            runtime_get_status,
            runtime_write_frontend_probe,
            asr_worker_prepare,
            asr_worker_start,
            asr_worker_health,
            asr_worker_collect_events,
            asr_worker_stop,
            asr_worker_cleanup,
            mic_adapter_prepare,
            mic_adapter_probe,
            mic_adapter_status,
            mic_adapter_collect_events,
            mic_adapter_start,
            mic_adapter_pause,
            mic_adapter_resume,
            mic_adapter_stop,
            mic_adapter_cleanup,
            windows_audio_devices,
            system_audio_adapter_prepare,
            system_audio_adapter_status,
            system_audio_adapter_collect_events,
            system_audio_adapter_start,
            system_audio_adapter_stop,
            system_audio_adapter_cleanup,
            dual_track_adapter_start,
            dual_track_adapter_status,
            dual_track_adapter_collect_events,
            dual_track_adapter_stop,
            dual_track_adapter_cleanup,
            provider_config_status,
            provider_config_sync,
            provider_config_save,
            provider_config_clear
        ])
        .build(tauri::generate_context!())
        .expect("error while building Talktrace desktop shell");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(audio) =
                app_handle.try_state::<desktop_audio_adapter_runtime::DesktopAudioCaptureAdapters>()
            {
                audio.system_audio_stop(None);
                audio.microphone_stop(None);
            }
            if let Some(supervisor) =
                app_handle.try_state::<desktop_backend_supervisor::BackendSupervisor>()
            {
                supervisor.stop();
            }
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    use std::fs;
    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn storage_root_uses_local_data_supports_override_and_preserves_legacy_installs() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-storage-root-test-{}-{nonce}",
            std::process::id()
        ));
        let local = root.join("local");
        let legacy = root.join("legacy");
        let custom = root.join("custom");

        assert_eq!(resolve_storage_root(None, &local, &legacy).unwrap(), local);
        fs::create_dir_all(&legacy).unwrap();
        assert_eq!(resolve_storage_root(None, &local, &legacy).unwrap(), legacy);
        fs::create_dir_all(&local).unwrap();
        assert_eq!(resolve_storage_root(None, &local, &legacy).unwrap(), local);
        assert_eq!(
            resolve_storage_root(Some(custom.clone().into_os_string()), &local, &legacy).unwrap(),
            custom
        );
        assert!(resolve_storage_root(Some("relative".into()), &local, &legacy).is_err());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn packaged_capability_is_scoped_to_the_runtime_loopback_port() {
        assert_eq!(
            packaged_remote_pattern("http://127.0.0.1:54321/desktop/bootstrap?token=not-recorded")
                .unwrap(),
            "http://127.0.0.1:54321/*"
        );
    }

    #[test]
    fn packaged_capability_rejects_non_loopback_or_implicit_ports() {
        assert!(packaged_remote_pattern("https://example.com/workbench").is_err());
        assert!(packaged_remote_pattern("http://localhost:54321/workbench").is_err());
        assert!(packaged_remote_pattern("http://127.0.0.1/workbench").is_err());
    }

    #[test]
    fn provider_startup_requires_explicit_connection_for_every_legacy_setting() {
        for legacy_setting in [None, Some("1"), Some("true"), Some("yes"), Some("on")] {
            assert_eq!(
                provider_startup_sync_policy(legacy_setting),
                ProviderStartupSyncPolicy::ExplicitConnectionOnly
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn dual_track_partial_start_never_reports_dual_track_success() {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-dual-track-partial-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir_all(&root).unwrap();
        let helper = root.join("fake-microphone.sh");
        fs::write(
            &helper,
            r#"#!/bin/sh
ready=''
session=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ready-file) ready="$2"; shift 2 ;;
    --session-id) session="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$(dirname "$ready")"
printf '{"schema_version":"meeting_copilot.native_mic_ready.v1","status":"ready","session_id":"%s","source":"av_audio_engine_microphone","sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":true,"first_pcm_rms":0.125,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":1}' "$session" > "$ready"
trap 'exit 0' TERM INT
while :; do sleep 1; done
"#,
        )
        .unwrap();
        let mut permissions = fs::metadata(&helper).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&helper, permissions).unwrap();

        let microphone = native_mic_capture_runtime::NativeMicCaptureSupervisor::new(
            helper,
            root.join("mic-runtime"),
            root.join("logs"),
        );
        let system_audio = native_system_audio_capture_runtime::SystemAudioCaptureSupervisor::new(
            root.join("missing-system-audio-helper"),
            root.join("system-runtime"),
            root.join("logs"),
        );
        let backend = desktop_backend_supervisor::BackendSupervisor::default();
        backend.use_external("http://127.0.0.1:8765".to_string());
        let coordinator =
            native_system_audio_capture_runtime::DualTrackCaptureCoordinator::default();
        let leases = coordinator.claim_dual_track("meeting_partial").unwrap();

        let microphone_response = microphone.start_with_epoch(
            Some("meeting_partial".to_string()),
            Some(leases.microphone.capture_epoch),
            &backend,
        );
        let system_audio_response = system_audio.start_with_epoch(
            Some("meeting_partial".to_string()),
            None,
            false,
            Some(leases.system_audio.capture_epoch),
            &backend,
        );
        assert_eq!(microphone_response.command_status, "ok");
        assert_eq!(system_audio_response.command_status, "blocked");
        coordinator.release_track(
            native_system_audio_capture_runtime::CaptureTrack::SystemAudio,
            "meeting_partial",
            leases.system_audio.capture_epoch,
        );

        let response = dual_track_capture_response(
            "dual_track_adapter.start",
            Some("meeting_partial".to_string()),
            microphone_response,
            system_audio_response,
            coordinator.snapshot(),
        );
        assert_eq!(response.command_status, "partial");
        assert_eq!(response.status, "degraded_single_track");
        assert_eq!(response.active_track_count, 1);
        assert_eq!(response.active_mode, "single_track");
        assert!(!response.mixed_audio_created);
        assert!(!response.raw_audio_uploaded);

        let cleaned = microphone.cleanup_for_session(Some("meeting_partial"));
        release_microphone_lease(&coordinator, &cleaned);
        assert_eq!(coordinator.snapshot().active_track_count, 0);
        let _ = fs::remove_dir_all(root);
    }
}

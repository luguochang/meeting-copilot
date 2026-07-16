use serde::Serialize;
use serde_json::json;
use std::env;
use std::path::PathBuf;
use tauri::webview::PageLoadEvent;
use tauri::Manager;

pub mod asr_worker_mic_source_runtime;
pub mod desktop_asr_worker_lifecycle_runtime;
pub mod desktop_audio_chunk_runtime;
pub mod desktop_backend_supervisor;
pub mod desktop_frontend_probe_runtime;
pub mod mic_adapter_runtime;

pub const BRIDGE_COMMAND_IDS: &[&str] = &[
    "runtime.get_status",
    "runtime.write_frontend_probe",
    "session.prepare",
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
    "mic_adapter.prepare",
    "mic_adapter.status",
    "mic_adapter.start",
    "mic_adapter.pause",
    "mic_adapter.resume",
    "mic_adapter.stop",
    "mic_adapter.delete_audio_chunks",
];

#[derive(Debug, Clone, Serialize)]
pub struct NoopBridgeResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub transport_status: &'static str,
    pub side_effect_status: &'static str,
    pub safe_to_invoke_noop: bool,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub desktop_api_base_url: Option<String>,
    pub backend_runtime_status: String,
    pub backend_runtime_mode: String,
    pub backend_pid: Option<u32>,
    pub backend_port: Option<u16>,
    pub packaged_same_chain_probe_enabled: bool,
}

impl NoopBridgeResponse {
    fn for_command(command_id: &'static str) -> Self {
        Self {
            command_id,
            command_status: "noop_bound",
            implementation_status: "noop_only",
            transport_status: "tauri_ipc_bound",
            side_effect_status: "none",
            safe_to_invoke_noop: true,
            safe_to_execute_real_action: false,
            captures_audio: false,
            spawns_process: false,
            calls_remote_provider: false,
            writes_local_files: false,
            desktop_api_base_url: None,
            backend_runtime_status: "not_started".to_string(),
            backend_runtime_mode: "unconfigured".to_string(),
            backend_pid: None,
            backend_port: None,
            packaged_same_chain_probe_enabled: false,
        }
    }
}

#[tauri::command]
fn runtime_get_status(
    supervisor: tauri::State<'_, desktop_backend_supervisor::BackendSupervisor>,
) -> NoopBridgeResponse {
    // Real status (not noop): reports the desktop shell is live.
    let mut r = NoopBridgeResponse::for_command("runtime.get_status");
    r.command_status = "ok";
    r.implementation_status = "real";
    r.safe_to_invoke_noop = true;
    let backend = supervisor.snapshot();
    r.desktop_api_base_url = backend.base_url;
    r.backend_runtime_status = backend.status;
    r.backend_runtime_mode = backend.mode;
    r.backend_pid = backend.pid;
    r.backend_port = backend.port;
    r.spawns_process = backend.spawns_process;
    r.writes_local_files = backend.spawns_process;
    r.packaged_same_chain_probe_enabled = env::var("MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE")
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            )
        })
        .unwrap_or(false);
    r
}

#[tauri::command]
fn runtime_write_frontend_probe(
    payload: serde_json::Value,
) -> desktop_frontend_probe_runtime::FrontendProbeResponse {
    desktop_frontend_probe_runtime::write_frontend_probe(payload)
}

#[tauri::command]
fn session_prepare() -> NoopBridgeResponse {
    let mut r = NoopBridgeResponse::for_command("session.prepare");
    r.command_status = "ok";
    r.implementation_status = "real";
    r
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

#[tauri::command]
fn mic_adapter_prepare() -> NoopBridgeResponse {
    // Mic capture delegates to the Python mic_capture.py client (sounddevice),
    // which streams PCM to the backend WS. macOS mic permission is declared via
    // NSMicrophoneUsageDescription in tauri.conf.json Info.plist.
    let mut r = NoopBridgeResponse::for_command("mic_adapter.prepare");
    r.command_status = "ok";
    r.implementation_status = "real";
    r
}

#[tauri::command]
fn mic_adapter_status() -> NoopBridgeResponse {
    let mut r = NoopBridgeResponse::for_command("mic_adapter.status");
    r.command_status = "ok";
    r.implementation_status = "real";
    r
}

#[tauri::command]
fn mic_adapter_start(
    session_id: Option<String>,
) -> desktop_audio_chunk_runtime::AudioChunkCommandResponse {
    desktop_audio_chunk_runtime::start_recording(session_id)
}

#[tauri::command]
fn mic_adapter_pause() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.pause")
}

#[tauri::command]
fn mic_adapter_resume() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.resume")
}

#[tauri::command]
fn mic_adapter_stop(
    session_id: Option<String>,
) -> desktop_audio_chunk_runtime::AudioChunkCommandResponse {
    desktop_audio_chunk_runtime::stop_recording(session_id)
}

#[tauri::command]
fn mic_adapter_delete_audio_chunks(
    session_id: Option<String>,
) -> desktop_audio_chunk_runtime::AudioChunkCommandResponse {
    desktop_audio_chunk_runtime::delete_audio_chunks(session_id)
}

pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let supervisor = desktop_backend_supervisor::BackendSupervisor::default();
            let resource_dir = app.path().resource_dir()?;
            let runtime_override = env::var_os("MEETING_COPILOT_RUNTIME_BUNDLE").map(PathBuf::from);
            let runtime_bundle = desktop_backend_supervisor::resolve_runtime_bundle(
                &resource_dir,
                runtime_override.as_deref(),
            );
            let backend = if runtime_bundle.is_dir() {
                supervisor
                    .start_packaged(
                        &runtime_bundle,
                        &app.path().app_data_dir()?.join("runtime-data"),
                        &app.path().app_log_dir()?,
                    )
                    .map_err(std::io::Error::other)?
            } else if cfg!(debug_assertions) {
                let base_url = env::var("MEETING_COPILOT_DESKTOP_API_BASE_URL")
                    .unwrap_or_else(|_| "http://127.0.0.1:8765".to_string());
                supervisor.use_external(base_url)
            } else {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::NotFound,
                    format!(
                        "packaged runtime is missing at {}",
                        runtime_bundle.display()
                    ),
                )
                .into());
            };
            let base_url = backend
                .base_url
                .clone()
                .ok_or_else(|| std::io::Error::other("backend did not publish a base URL"))?;
            app.manage(supervisor);
            let window = app
                .get_webview_window("main")
                .ok_or_else(|| std::io::Error::other("main webview window is missing"))?;
            let url = tauri::Url::parse(&format!("{base_url}/workbench"))?;
            window.navigate(url)?;
            window.show()?;
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
            session_prepare,
            asr_worker_prepare,
            asr_worker_start,
            asr_worker_health,
            asr_worker_collect_events,
            asr_worker_stop,
            asr_worker_cleanup,
            mic_adapter_prepare,
            mic_adapter_status,
            mic_adapter_start,
            mic_adapter_pause,
            mic_adapter_resume,
            mic_adapter_stop,
            mic_adapter_delete_audio_chunks
        ])
        .build(tauri::generate_context!())
        .expect("error while building Meeting Copilot desktop shell");
    app.run(|app_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(supervisor) =
                app_handle.try_state::<desktop_backend_supervisor::BackendSupervisor>()
            {
                supervisor.stop();
            }
        }
    });
}

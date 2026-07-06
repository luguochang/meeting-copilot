use serde::Serialize;

pub mod asr_worker_mic_source_runtime;
pub mod mic_adapter_runtime;

pub const BRIDGE_COMMAND_IDS: &[&str] = &[
    "runtime.get_status",
    "session.prepare",
    "asr_worker.health",
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
        }
    }
}

#[tauri::command]
fn runtime_get_status() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("runtime.get_status")
}

#[tauri::command]
fn session_prepare() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("session.prepare")
}

#[tauri::command]
fn asr_worker_health() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("asr_worker.health")
}

#[tauri::command]
fn mic_adapter_prepare() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.prepare")
}

#[tauri::command]
fn mic_adapter_status() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.status")
}

#[tauri::command]
fn mic_adapter_start() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.start")
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
fn mic_adapter_stop() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.stop")
}

#[tauri::command]
fn mic_adapter_delete_audio_chunks() -> NoopBridgeResponse {
    NoopBridgeResponse::for_command("mic_adapter.delete_audio_chunks")
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            runtime_get_status,
            session_prepare,
            asr_worker_health,
            mic_adapter_prepare,
            mic_adapter_status,
            mic_adapter_start,
            mic_adapter_pause,
            mic_adapter_resume,
            mic_adapter_stop,
            mic_adapter_delete_audio_chunks
        ])
        .run(tauri::generate_context!())
        .expect("error while running Meeting Copilot desktop shell");
}

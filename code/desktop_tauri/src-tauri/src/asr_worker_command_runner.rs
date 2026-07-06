use serde::Serialize;

pub const COMMAND_CATALOG: &[&str] = &[
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
];

#[derive(Debug, Clone, Serialize)]
pub struct AsrWorkerCommandRunnerPreview {
    pub implementation_status: &'static str,
    pub binding_status: &'static str,
    pub command_transport: &'static str,
    pub command_catalog: &'static [&'static str],
    pub safe_to_execute_now: bool,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct BlockedCommandRunnerResponse {
    pub command_id: String,
    pub accepted: bool,
    pub binding_status: &'static str,
    pub dispatch_status: &'static str,
    pub worker_execution_status: &'static str,
    pub tauri_ipc_status: &'static str,
    pub process_spawn_status: &'static str,
    pub event_file_read_status: &'static str,
    pub event_file_write_status: &'static str,
    pub safe_to_execute_now: bool,
}

pub fn command_catalog_preview() -> &'static [&'static str] {
    COMMAND_CATALOG
}

pub fn implementation_preview() -> AsrWorkerCommandRunnerPreview {
    AsrWorkerCommandRunnerPreview {
        implementation_status: "skeleton_source_validated_not_bound",
        binding_status: "not_bound",
        command_transport: "stdio_jsonl",
        command_catalog: COMMAND_CATALOG,
        safe_to_execute_now: false,
    }
}

pub fn preview_blocked_response(command_id: &str) -> BlockedCommandRunnerResponse {
    BlockedCommandRunnerResponse {
        command_id: command_id.to_owned(),
        accepted: false,
        binding_status: "not_bound",
        dispatch_status: "not_dispatched",
        worker_execution_status: "not_executed",
        tauri_ipc_status: "not_invoked",
        process_spawn_status: "not_spawned",
        event_file_read_status: "not_read",
        event_file_write_status: "not_written",
        safe_to_execute_now: false,
    }
}

use serde::Serialize;

pub const ASR_WORKER_MIC_SOURCE_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested";
pub const EVENT_CONTRACT_STATUS: &str = "partial_final_revision_error_end_of_stream_supported";
pub const WORKER_OUTPUT_ROOT: &str = "artifacts/tmp/asr_events";
pub const WORKER_RUNTIME_ROOT: &str = "artifacts/tmp/desktop_asr_worker_runtime";
pub const WEB_HANDOFF_STATUS: &str = "closed_to_evidence_state_gap";
pub const SOURCE_KIND: &str = "mic";
pub const COMMAND_CATALOG_SMOKED: &[&str] = &[
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
];

#[derive(Debug, Clone, Serialize)]
pub struct AsrWorkerMicSourceBoundaryEvidence {
    pub implementation_status: &'static str,
    pub event_contract_status: &'static str,
    pub worker_output_root: &'static str,
    pub worker_runtime_root: &'static str,
    pub web_handoff_status: &'static str,
    pub source_kind: &'static str,
    pub command_catalog_smoked: &'static [&'static str],
    pub requires_explicit_user_start: bool,
    pub default_uploads_raw_audio: bool,
    pub default_remote_asr_enabled: bool,
    pub safe_to_spawn_worker_now: bool,
    pub safe_to_start_worker_now: bool,
    pub safe_to_stop_worker_now: bool,
    pub safe_to_execute_worker_command_now: bool,
    pub safe_to_dispatch_worker_command_now: bool,
    pub safe_to_bind_worker_command_transport_now: bool,
    pub safe_to_read_event_file_now: bool,
    pub safe_to_write_event_file_now: bool,
    pub safe_to_capture_audio_now: bool,
    pub safe_to_request_audio_permission_now: bool,
    pub safe_to_read_audio_chunk_now: bool,
    pub safe_to_write_audio_chunk_now: bool,
    pub safe_to_read_user_audio_now: bool,
    pub safe_to_read_configs_local_now: bool,
    pub safe_to_read_secret_now: bool,
    pub safe_to_call_remote_asr_now: bool,
    pub safe_to_call_llm_now: bool,
    pub safe_to_download_models_now: bool,
    pub safe_to_download_public_audio_now: bool,
    pub safe_to_run_tauri_or_cargo_now: bool,
}

pub fn boundary_evidence() -> AsrWorkerMicSourceBoundaryEvidence {
    AsrWorkerMicSourceBoundaryEvidence {
        implementation_status: ASR_WORKER_MIC_SOURCE_IMPLEMENTATION_STATUS,
        event_contract_status: EVENT_CONTRACT_STATUS,
        worker_output_root: WORKER_OUTPUT_ROOT,
        worker_runtime_root: WORKER_RUNTIME_ROOT,
        web_handoff_status: WEB_HANDOFF_STATUS,
        source_kind: SOURCE_KIND,
        command_catalog_smoked: COMMAND_CATALOG_SMOKED,
        requires_explicit_user_start: true,
        default_uploads_raw_audio: false,
        default_remote_asr_enabled: false,
        safe_to_spawn_worker_now: false,
        safe_to_start_worker_now: false,
        safe_to_stop_worker_now: false,
        safe_to_execute_worker_command_now: false,
        safe_to_dispatch_worker_command_now: false,
        safe_to_bind_worker_command_transport_now: false,
        safe_to_read_event_file_now: false,
        safe_to_write_event_file_now: false,
        safe_to_capture_audio_now: false,
        safe_to_request_audio_permission_now: false,
        safe_to_read_audio_chunk_now: false,
        safe_to_write_audio_chunk_now: false,
        safe_to_read_user_audio_now: false,
        safe_to_read_configs_local_now: false,
        safe_to_read_secret_now: false,
        safe_to_call_remote_asr_now: false,
        safe_to_call_llm_now: false,
        safe_to_download_models_now: false,
        safe_to_download_public_audio_now: false,
        safe_to_run_tauri_or_cargo_now: false,
    }
}

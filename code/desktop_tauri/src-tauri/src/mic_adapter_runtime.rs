use serde::Serialize;

pub const MIC_ADAPTER_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested";
pub const RUNTIME_AUDIO_ROOT: &str = "artifacts/tmp/desktop_mic_adapter_runtime";
pub const AUDIO_CHUNK_ROOT: &str = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks";
pub const COMMANDS_SMOKED: &[&str] = &[
    "prepare",
    "status",
    "start",
    "pause",
    "resume",
    "stop",
    "delete_audio_chunks",
];

#[derive(Debug, Clone, Serialize)]
pub struct MicAdapterRuntimeBoundaryEvidence {
    pub implementation_status: &'static str,
    pub commands_smoked: &'static [&'static str],
    pub runtime_audio_root: &'static str,
    pub audio_chunk_root: &'static str,
    pub requires_explicit_user_start: bool,
    pub default_uploads_raw_audio: bool,
    pub default_remote_asr_enabled: bool,
    pub safe_to_capture_audio_now: bool,
    pub safe_to_request_audio_permission_now: bool,
    pub safe_to_write_audio_chunk_now: bool,
    pub safe_to_start_recording_now: bool,
    pub safe_to_read_audio_chunk_now: bool,
    pub safe_to_delete_audio_chunks_now: bool,
    pub safe_to_spawn_worker_now: bool,
}

pub fn boundary_evidence() -> MicAdapterRuntimeBoundaryEvidence {
    MicAdapterRuntimeBoundaryEvidence {
        implementation_status: MIC_ADAPTER_IMPLEMENTATION_STATUS,
        commands_smoked: COMMANDS_SMOKED,
        runtime_audio_root: RUNTIME_AUDIO_ROOT,
        audio_chunk_root: AUDIO_CHUNK_ROOT,
        requires_explicit_user_start: true,
        default_uploads_raw_audio: false,
        default_remote_asr_enabled: false,
        safe_to_capture_audio_now: false,
        safe_to_request_audio_permission_now: false,
        safe_to_write_audio_chunk_now: false,
        safe_to_start_recording_now: false,
        safe_to_read_audio_chunk_now: false,
        safe_to_delete_audio_chunks_now: false,
        safe_to_spawn_worker_now: false,
    }
}

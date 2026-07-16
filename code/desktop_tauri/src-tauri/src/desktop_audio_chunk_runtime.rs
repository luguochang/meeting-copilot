use serde::Serialize;
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};

pub const AUDIO_CHUNK_ROOT: &str = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks";
const DEFAULT_SESSION_ID: &str = "desktop_dev_session";

#[derive(Debug, Clone, Serialize)]
pub struct AudioChunkCommandResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub transport_status: &'static str,
    pub side_effect_status: &'static str,
    pub session_id: String,
    pub audio_chunk_dir: String,
    pub audio_chunk_manifest: String,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub errors: Vec<String>,
}

pub fn prepare_session(session_id: Option<String>) -> AudioChunkCommandResponse {
    write_manifest_command(
        "mic_adapter.prepare",
        session_id,
        "prepared",
        "session_audio_chunk_dir_created",
    )
}

pub fn start_recording(session_id: Option<String>) -> AudioChunkCommandResponse {
    write_manifest_command(
        "mic_adapter.start",
        session_id,
        "recording",
        "session_audio_chunk_dir_created",
    )
}

pub fn stop_recording(session_id: Option<String>) -> AudioChunkCommandResponse {
    write_manifest_command(
        "mic_adapter.stop",
        session_id,
        "stopped",
        "session_audio_chunk_manifest_stopped",
    )
}

pub fn delete_audio_chunks(session_id: Option<String>) -> AudioChunkCommandResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match session_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => {
            return blocked_response("mic_adapter.delete_audio_chunks", session_id, vec![error]);
        }
    };

    if let Err(error) = fs::remove_dir_all(&paths.audio_chunk_dir) {
        if error.kind() != std::io::ErrorKind::NotFound {
            return blocked_response(
                "mic_adapter.delete_audio_chunks",
                session_id,
                vec![format!("failed to delete session audio chunks: {error}")],
            );
        }
    }

    AudioChunkCommandResponse {
        command_id: "mic_adapter.delete_audio_chunks",
        command_status: "ok",
        implementation_status: "local_audio_chunk_runtime",
        transport_status: "tauri_ipc_bound",
        side_effect_status: "session_audio_chunks_deleted",
        session_id,
        audio_chunk_dir: display_path(&paths.audio_chunk_dir),
        audio_chunk_manifest: display_path(&paths.audio_chunk_manifest),
        safe_to_execute_real_action: true,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: true,
        errors: Vec::new(),
    }
}

fn write_manifest_command(
    command_id: &'static str,
    session_id: Option<String>,
    lifecycle_state: &'static str,
    side_effect_status: &'static str,
) -> AudioChunkCommandResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match session_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response(command_id, session_id, vec![error]),
    };

    if let Err(error) = fs::create_dir_all(&paths.audio_chunk_dir) {
        return blocked_response(
            command_id,
            session_id,
            vec![format!(
                "failed to create session audio chunk directory: {error}"
            )],
        );
    }

    let manifest = json!({
        "schema_version": "desktop_audio_chunk_manifest.v1",
        "session_id": session_id,
        "lifecycle_state": lifecycle_state,
        "audio_chunk_root": AUDIO_CHUNK_ROOT,
        "audio_chunk_dir": display_path(&paths.audio_chunk_dir),
        "chunks": [],
        "sample_rate_hz": 16000,
        "channels": 1,
        "sample_format": "float32le",
        "captures_audio": false,
        "spawns_process": false,
        "calls_remote_provider": false,
        "default_remote_asr_enabled": false,
        "default_uploads_raw_audio": false
    });

    if let Err(error) = fs::write(
        &paths.audio_chunk_manifest,
        serde_json::to_string_pretty(&manifest).unwrap_or_else(|_| "{}".to_string()),
    ) {
        return blocked_response(
            command_id,
            session_id,
            vec![format!(
                "failed to write session audio chunk manifest: {error}"
            )],
        );
    }

    AudioChunkCommandResponse {
        command_id,
        command_status: "ok",
        implementation_status: "local_audio_chunk_runtime",
        transport_status: "tauri_ipc_bound",
        side_effect_status,
        session_id,
        audio_chunk_dir: display_path(&paths.audio_chunk_dir),
        audio_chunk_manifest: display_path(&paths.audio_chunk_manifest),
        safe_to_execute_real_action: true,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: true,
        errors: Vec::new(),
    }
}

fn blocked_response(
    command_id: &'static str,
    session_id: String,
    errors: Vec<String>,
) -> AudioChunkCommandResponse {
    AudioChunkCommandResponse {
        command_id,
        command_status: "blocked",
        implementation_status: "local_audio_chunk_runtime",
        transport_status: "tauri_ipc_bound",
        side_effect_status: "none",
        session_id,
        audio_chunk_dir: "<blocked>".to_string(),
        audio_chunk_manifest: "<blocked>".to_string(),
        safe_to_execute_real_action: false,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: false,
        errors,
    }
}

fn normalize_session_id(session_id: Option<String>) -> String {
    session_id
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_SESSION_ID.to_string())
}

fn session_paths(session_id: &str) -> Result<SessionPaths, String> {
    safe_session_id(session_id)?;
    let audio_chunk_dir = forbid_path_escape(session_id)?;
    let audio_chunk_manifest = audio_chunk_dir.join("manifest.json");
    Ok(SessionPaths {
        audio_chunk_dir,
        audio_chunk_manifest,
    })
}

fn safe_session_id(session_id: &str) -> Result<(), String> {
    if session_id.is_empty() || session_id.len() > 128 {
        return Err("session_id must be 1-128 safe characters".to_string());
    }
    if session_id
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
    {
        return Ok(());
    }
    Err("session_id contains unsafe characters".to_string())
}

fn forbid_path_escape(session_id: &str) -> Result<PathBuf, String> {
    let root = repo_root().join(AUDIO_CHUNK_ROOT);
    let audio_chunk_dir = root.join(session_id);
    if audio_chunk_dir.starts_with(&root) {
        return Ok(audio_chunk_dir);
    }
    Err("audio chunk path escaped approved root".to_string())
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

fn display_path(path: &Path) -> String {
    let repo_root = repo_root();
    path.strip_prefix(&repo_root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

struct SessionPaths {
    audio_chunk_dir: PathBuf,
    audio_chunk_manifest: PathBuf,
}

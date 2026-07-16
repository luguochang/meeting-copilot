use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

pub const WORKER_RUNTIME_ROOT: &str = "artifacts/tmp/desktop_asr_worker_runtime";
pub const WORKER_EVENT_ROOT: &str = "artifacts/tmp/asr_events";
const DEFAULT_SESSION_ID: &str = "desktop_worker_dev_session";

#[derive(Debug, Clone, Serialize)]
pub struct AsrWorkerLifecycleResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub worker_lifecycle_status: &'static str,
    pub session_id: String,
    pub runtime_root: String,
    pub worker_state_path: String,
    pub event_output_path: String,
    pub event_file_status: &'static str,
    pub final_event_count: usize,
    pub end_of_stream_event_count: usize,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub reads_user_audio: bool,
    pub writes_local_files: bool,
    pub errors: Vec<String>,
}

pub fn prepare_worker(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.prepare", session_id, vec![error]),
    };

    if let Err(error) = fs::create_dir_all(&paths.session_runtime_dir) {
        return blocked_response(
            "worker.prepare",
            session_id,
            vec![format!(
                "failed to create worker runtime directory: {error}"
            )],
        );
    }
    if let Err(error) = fs::create_dir_all(worker_event_root()) {
        return blocked_response(
            "worker.prepare",
            session_id,
            vec![format!("failed to create worker event directory: {error}")],
        );
    }
    if let Err(error) = write_state(&paths, "prepared") {
        return blocked_response("worker.prepare", session_id, vec![error]);
    }

    ok_response(
        "worker.prepare",
        "prepared",
        "not_written",
        session_id,
        paths,
        0,
        0,
    )
}

pub fn start_worker(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.start", session_id, vec![error]),
    };

    match read_lifecycle_status(&paths) {
        Some("prepared") => {}
        _ => {
            return blocked_response(
                "worker.start",
                session_id,
                vec!["worker.start requires current state prepared".to_string()],
            );
        }
    }
    if let Err(error) = fs::create_dir_all(worker_event_root()) {
        return blocked_response(
            "worker.start",
            session_id,
            vec![format!("failed to create worker event directory: {error}")],
        );
    }
    if let Err(error) = write_synthetic_event_file(&paths) {
        return blocked_response("worker.start", session_id, vec![error]);
    }
    if let Err(error) = write_state(&paths, "running") {
        return blocked_response("worker.start", session_id, vec![error]);
    }
    let counts = count_streaming_events(&paths.event_output_path).unwrap_or_default();

    ok_response(
        "worker.start",
        "running",
        "events_written",
        session_id,
        paths,
        counts.final_event_count,
        counts.end_of_stream_event_count,
    )
}

pub fn worker_health(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.health", session_id, vec![error]),
    };
    let lifecycle_status = read_lifecycle_status(&paths).unwrap_or("not_prepared");
    let counts = count_streaming_events(&paths.event_output_path).unwrap_or_default();

    ok_response(
        "worker.health",
        lifecycle_status,
        if counts.total_count > 0 {
            "events_present"
        } else {
            "not_written"
        },
        session_id,
        paths,
        counts.final_event_count,
        counts.end_of_stream_event_count,
    )
}

pub fn collect_events(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.collect_events", session_id, vec![error]),
    };
    let lifecycle_status = match read_lifecycle_status(&paths) {
        Some("running") => "running",
        Some("stopped") => "stopped",
        _ => {
            return blocked_response(
                "worker.collect_events",
                session_id,
                vec!["worker.collect_events requires current state running or stopped".to_string()],
            );
        }
    };
    let counts = match count_streaming_events(&paths.event_output_path) {
        Ok(counts) => counts,
        Err(error) => return blocked_response("worker.collect_events", session_id, vec![error]),
    };

    ok_response(
        "worker.collect_events",
        lifecycle_status,
        "events_collected",
        session_id,
        paths,
        counts.final_event_count,
        counts.end_of_stream_event_count,
    )
}

pub fn stop_worker(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.stop", session_id, vec![error]),
    };
    if let Err(error) = write_state(&paths, "stopped") {
        return blocked_response("worker.stop", session_id, vec![error]);
    }
    let counts = count_streaming_events(&paths.event_output_path).unwrap_or_default();

    ok_response(
        "worker.stop",
        "stopped",
        if counts.total_count > 0 {
            "events_present"
        } else {
            "not_written"
        },
        session_id,
        paths,
        counts.final_event_count,
        counts.end_of_stream_event_count,
    )
}

pub fn cleanup_worker(session_id: Option<String>) -> AsrWorkerLifecycleResponse {
    let session_id = normalize_session_id(session_id);
    let paths = match worker_paths(&session_id) {
        Ok(paths) => paths,
        Err(error) => return blocked_response("worker.cleanup", session_id, vec![error]),
    };
    let counts = count_streaming_events(&paths.event_output_path).unwrap_or_default();
    if let Err(error) = fs::remove_dir_all(&paths.session_runtime_dir) {
        if error.kind() != std::io::ErrorKind::NotFound {
            return blocked_response(
                "worker.cleanup",
                session_id,
                vec![format!(
                    "failed to remove worker runtime directory: {error}"
                )],
            );
        }
    }

    ok_response(
        "worker.cleanup",
        "cleaned",
        if counts.total_count > 0 {
            "events_present"
        } else {
            "not_written"
        },
        session_id,
        paths,
        counts.final_event_count,
        counts.end_of_stream_event_count,
    )
}

fn write_state(paths: &WorkerPaths, lifecycle_status: &'static str) -> Result<(), String> {
    let state = json!({
        "schema_version": "desktop_asr_worker_state.v1",
        "session_id": paths.session_id,
        "worker_lifecycle_status": lifecycle_status,
        "runtime_root": display_path(&paths.session_runtime_dir),
        "worker_state_path": display_path(&paths.worker_state_path),
        "event_output_path": display_path(&paths.event_output_path),
        "source_kind": "synthetic",
        "provider_mode": "local_synthetic_streaming",
        "captures_audio": false,
        "spawns_process": false,
        "calls_remote_provider": false,
        "reads_user_audio": false
    });
    fs::write(
        &paths.worker_state_path,
        serde_json::to_string_pretty(&state).unwrap_or_else(|_| "{}".to_string()),
    )
    .map_err(|error| format!("failed to write worker state: {error}"))
}

fn write_synthetic_event_file(paths: &WorkerPaths) -> Result<(), String> {
    let events = json!([
        {
            "event_type": "final",
            "segment_id": "desktop_worker_seg_001",
            "text": "API review needs owner deadline rollback P99 and alert follow up.",
            "start_ms": 0,
            "end_ms": 3000,
            "received_at_ms": 3500,
            "confidence": 0.98
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "desktop_worker_eos",
            "text": "",
            "start_ms": 3000,
            "end_ms": 3000,
            "received_at_ms": 3100
        }
    ]);
    fs::write(
        &paths.event_output_path,
        serde_json::to_string_pretty(&events).unwrap_or_else(|_| "[]".to_string()),
    )
    .map_err(|error| format!("failed to write worker event file: {error}"))
}

fn count_streaming_events(event_output_path: &Path) -> Result<EventCounts, String> {
    let payload = fs::read_to_string(event_output_path)
        .map_err(|error| format!("failed to read worker event file: {error}"))?;
    let events: Value = serde_json::from_str(&payload)
        .map_err(|error| format!("invalid worker event JSON: {error}"))?;
    let Some(items) = events.as_array() else {
        return Err("worker event file must contain a JSON array".to_string());
    };
    let mut counts = EventCounts::default();
    counts.total_count = items.len();
    for item in items {
        match item
            .get("event_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
        {
            "final" => counts.final_event_count += 1,
            "end_of_stream" => counts.end_of_stream_event_count += 1,
            _ => {}
        }
    }
    Ok(counts)
}

fn read_lifecycle_status(paths: &WorkerPaths) -> Option<&'static str> {
    let payload = fs::read_to_string(&paths.worker_state_path).ok()?;
    let value: Value = serde_json::from_str(&payload).ok()?;
    match value.get("worker_lifecycle_status").and_then(Value::as_str) {
        Some("prepared") => Some("prepared"),
        Some("running") => Some("running"),
        Some("stopped") => Some("stopped"),
        Some("cleaned") => Some("cleaned"),
        _ => None,
    }
}

fn ok_response(
    command_id: &'static str,
    worker_lifecycle_status: &'static str,
    event_file_status: &'static str,
    session_id: String,
    paths: WorkerPaths,
    final_event_count: usize,
    end_of_stream_event_count: usize,
) -> AsrWorkerLifecycleResponse {
    AsrWorkerLifecycleResponse {
        command_id,
        command_status: "ok",
        implementation_status: "local_asr_worker_lifecycle_runtime",
        worker_lifecycle_status,
        session_id,
        runtime_root: display_path(&paths.session_runtime_dir),
        worker_state_path: display_path(&paths.worker_state_path),
        event_output_path: display_path(&paths.event_output_path),
        event_file_status,
        final_event_count,
        end_of_stream_event_count,
        safe_to_execute_real_action: true,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        reads_user_audio: false,
        writes_local_files: true,
        errors: Vec::new(),
    }
}

fn blocked_response(
    command_id: &'static str,
    session_id: String,
    errors: Vec<String>,
) -> AsrWorkerLifecycleResponse {
    AsrWorkerLifecycleResponse {
        command_id,
        command_status: "blocked",
        implementation_status: "local_asr_worker_lifecycle_runtime",
        worker_lifecycle_status: "blocked",
        session_id,
        runtime_root: "<blocked>".to_string(),
        worker_state_path: "<blocked>".to_string(),
        event_output_path: "<blocked>".to_string(),
        event_file_status: "not_available",
        final_event_count: 0,
        end_of_stream_event_count: 0,
        safe_to_execute_real_action: false,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        reads_user_audio: false,
        writes_local_files: false,
        errors,
    }
}

fn normalize_session_id(session_id: Option<String>) -> String {
    session_id
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_SESSION_ID.to_string())
}

fn worker_paths(session_id: &str) -> Result<WorkerPaths, String> {
    safe_session_id(session_id)?;
    let session_runtime_dir = session_runtime_dir(session_id)?;
    let worker_state_path = worker_state_path(&session_runtime_dir);
    let event_output_path = event_output_path(session_id)?;
    Ok(WorkerPaths {
        session_id: session_id.to_string(),
        session_runtime_dir,
        worker_state_path,
        event_output_path,
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

fn session_runtime_dir(session_id: &str) -> Result<PathBuf, String> {
    let root = repo_root().join(WORKER_RUNTIME_ROOT);
    let candidate = root.join(session_id);
    if candidate.starts_with(&root) {
        return Ok(candidate);
    }
    Err("worker runtime path escaped approved root".to_string())
}

fn worker_state_path(session_runtime_dir: &Path) -> PathBuf {
    session_runtime_dir.join("worker_state.json")
}

fn event_output_path(session_id: &str) -> Result<PathBuf, String> {
    let root = worker_event_root();
    let candidate = root.join(format!("{session_id}.desktop-worker.events.json"));
    if candidate.starts_with(&root) {
        return Ok(candidate);
    }
    Err("worker event path escaped approved root".to_string())
}

fn worker_event_root() -> PathBuf {
    repo_root().join(WORKER_EVENT_ROOT)
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

#[derive(Debug, Clone)]
struct WorkerPaths {
    session_id: String,
    session_runtime_dir: PathBuf,
    worker_state_path: PathBuf,
    event_output_path: PathBuf,
}

#[derive(Debug, Clone, Default)]
struct EventCounts {
    total_count: usize,
    final_event_count: usize,
    end_of_stream_event_count: usize,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reset_session(session_id: &str) {
        let _ = fs::remove_dir_all(repo_root().join(WORKER_RUNTIME_ROOT).join(session_id));
        let _ = fs::remove_file(
            repo_root()
                .join(WORKER_EVENT_ROOT)
                .join(format!("{session_id}.desktop-worker.events.json")),
        );
    }

    #[test]
    fn lifecycle_blocks_start_until_prepared() {
        let session_id = "desktop_worker_lifecycle_order_start";
        reset_session(session_id);

        let response = start_worker(Some(session_id.to_string()));

        assert_eq!(response.command_id, "worker.start");
        assert_eq!(response.command_status, "blocked");
        assert_eq!(response.worker_lifecycle_status, "blocked");
        assert!(response
            .errors
            .contains(&"worker.start requires current state prepared".to_string()));
        assert!(!response.safe_to_execute_real_action);
    }

    #[test]
    fn lifecycle_runs_prepare_start_collect_stop_cleanup_in_order() {
        let session_id = "desktop_worker_lifecycle_order_ok";
        reset_session(session_id);

        let prepared = prepare_worker(Some(session_id.to_string()));
        assert_eq!(prepared.worker_lifecycle_status, "prepared");

        let started = start_worker(Some(session_id.to_string()));
        assert_eq!(started.worker_lifecycle_status, "running");
        assert_eq!(started.event_file_status, "events_written");
        assert_eq!(started.final_event_count, 1);
        assert_eq!(started.end_of_stream_event_count, 1);

        let collected = collect_events(Some(session_id.to_string()));
        assert_eq!(collected.command_status, "ok");
        assert_eq!(collected.event_file_status, "events_collected");
        assert_eq!(collected.final_event_count, 1);

        let stopped = stop_worker(Some(session_id.to_string()));
        assert_eq!(stopped.worker_lifecycle_status, "stopped");

        let cleaned = cleanup_worker(Some(session_id.to_string()));
        assert_eq!(cleaned.worker_lifecycle_status, "cleaned");
    }

    #[test]
    fn lifecycle_blocks_collect_until_running_or_stopped() {
        let session_id = "desktop_worker_lifecycle_order_collect";
        reset_session(session_id);
        let _prepared = prepare_worker(Some(session_id.to_string()));

        let response = collect_events(Some(session_id.to_string()));

        assert_eq!(response.command_id, "worker.collect_events");
        assert_eq!(response.command_status, "blocked");
        assert!(response.errors.contains(
            &"worker.collect_events requires current state running or stopped".to_string()
        ));
    }
}

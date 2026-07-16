use serde::Serialize;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

pub const FRONTEND_PROBE_ROOT: &str = "artifacts/tmp/desktop_frontend_probe_runtime";

#[derive(Debug, Clone, Serialize)]
pub struct FrontendProbeResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub transport_status: &'static str,
    pub side_effect_status: &'static str,
    pub probe_path: String,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub errors: Vec<String>,
}

pub fn write_frontend_probe(payload: Value) -> FrontendProbeResponse {
    let root = repo_root().join(FRONTEND_PROBE_ROOT);
    let probe_path = root.join("latest.json");
    let category_probe_path = root.join(category_probe_file_name(&payload));
    if let Err(error) = fs::create_dir_all(&root) {
        return blocked_response(
            probe_path,
            format!("failed to create frontend probe root: {error}"),
        );
    }

    let evidence = json!({
        "schema_version": "desktop_frontend_probe.v1",
        "source": "tauri_packaged_webview",
        "payload": payload,
        "captures_audio": false,
        "spawns_process": false,
        "calls_remote_provider": false,
        "writes_local_files": true,
    });
    if let Err(error) = fs::write(
        &probe_path,
        serde_json::to_string_pretty(&evidence).unwrap_or_else(|_| "{}".to_string()),
    ) {
        return blocked_response(
            probe_path,
            format!("failed to write frontend probe: {error}"),
        );
    }
    if let Err(error) = fs::write(
        &category_probe_path,
        serde_json::to_string_pretty(&evidence).unwrap_or_else(|_| "{}".to_string()),
    ) {
        return blocked_response(
            category_probe_path,
            format!("failed to write category frontend probe: {error}"),
        );
    }

    FrontendProbeResponse {
        command_id: "runtime.write_frontend_probe",
        command_status: "ok",
        implementation_status: "local_frontend_probe_runtime",
        transport_status: "tauri_ipc_bound",
        side_effect_status: "frontend_probe_written",
        probe_path: display_path(&probe_path),
        safe_to_execute_real_action: true,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: true,
        errors: Vec::new(),
    }
}

fn category_probe_file_name(payload: &Value) -> &'static str {
    if payload
        .get("rust_page_load_probe")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "latest-page-load.json"
    } else if payload
        .get("inline_probe")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "latest-inline-dom.json"
    } else if payload
        .get("packaged_api_probe")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "latest-backend-api.json"
    } else if payload
        .get("packaged_same_chain_probe")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        "latest-same-chain.json"
    } else {
        "latest-workbench-runtime.json"
    }
}

fn blocked_response(probe_path: PathBuf, error: String) -> FrontendProbeResponse {
    FrontendProbeResponse {
        command_id: "runtime.write_frontend_probe",
        command_status: "blocked",
        implementation_status: "local_frontend_probe_runtime",
        transport_status: "tauri_ipc_bound",
        side_effect_status: "none",
        probe_path: display_path(&probe_path),
        safe_to_execute_real_action: false,
        captures_audio: false,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: false,
        errors: vec![error],
    }
}

fn repo_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    for candidate in manifest_dir.ancestors() {
        if candidate.join("docs").is_dir() && candidate.join("code").join("desktop_tauri").is_dir()
        {
            return candidate.to_path_buf();
        }
    }

    manifest_dir
        .parent()
        .and_then(Path::parent)
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    static FRONTEND_PROBE_TEST_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn repo_root_resolves_to_project_root_not_code_directory() {
        let root = repo_root();

        assert!(
            root.join("docs").is_dir(),
            "expected repo root docs directory at {}",
            root.display()
        );
        assert!(root.join("code").join("desktop_tauri").is_dir());
        assert_eq!(
            root.join("code").join("desktop_tauri").join("src-tauri"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        );
        assert!(!root.ends_with("code"));
    }

    #[test]
    fn write_frontend_probe_preserves_category_specific_dom_evidence() {
        let _guard = FRONTEND_PROBE_TEST_LOCK.lock().unwrap();
        let root = repo_root().join(FRONTEND_PROBE_ROOT);
        let wrong_root = repo_root().join("code").join(FRONTEND_PROBE_ROOT);
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&wrong_root);

        let response = write_frontend_probe(json!({
            "inline_probe": true,
            "selectors": {
                "history-list": true,
                "session-meta": true,
                "transcript-stream": true,
                "suggestions-panel": true,
                "approach-panel": true,
                "minutes-panel": true,
                "s-desktop": true
            }
        }));

        assert_eq!(response.command_status, "ok");
        assert!(root.join("latest.json").is_file());
        assert!(root.join("latest-inline-dom.json").is_file());
        assert!(!wrong_root.exists());
    }

    #[test]
    fn write_frontend_probe_preserves_packaged_backend_api_evidence() {
        let _guard = FRONTEND_PROBE_TEST_LOCK.lock().unwrap();
        let root = repo_root().join(FRONTEND_PROBE_ROOT);
        let _ = fs::remove_dir_all(&root);

        let response = write_frontend_probe(json!({
            "packaged_api_probe": true,
            "api_base_url": "http://127.0.0.1:8765",
            "health_ok": true,
            "sessions_loaded": true
        }));

        assert_eq!(response.command_status, "ok");
        assert!(root.join("latest-backend-api.json").is_file());
    }

    #[test]
    fn write_frontend_probe_preserves_packaged_same_chain_evidence() {
        let _guard = FRONTEND_PROBE_TEST_LOCK.lock().unwrap();
        let root = repo_root().join(FRONTEND_PROBE_ROOT);
        let _ = fs::remove_dir_all(&root);

        let response = write_frontend_probe(json!({
            "packaged_same_chain_probe": true,
            "no_cost": true,
            "session_created": true,
            "transcript_visible": true,
            "suggestion_card_count": 3,
            "approach_card_count": 1,
            "minutes_visible": true,
            "history_visible": true,
            "delete_verified": true,
            "errors": []
        }));

        assert_eq!(response.command_status, "ok");
        assert!(root.join("latest-same-chain.json").is_file());
    }
}

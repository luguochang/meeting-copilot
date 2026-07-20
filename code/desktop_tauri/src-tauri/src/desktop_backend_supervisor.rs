use crate::private_storage::{ensure_private_directory, open_private_file};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(60);
const BACKEND_STOP_TIMEOUT: Duration = Duration::from_secs(5);
const BACKEND_MONITOR_INTERVAL: Duration = Duration::from_millis(100);
const BACKEND_RESTART_INITIAL_BACKOFF: Duration = Duration::from_millis(100);
const BACKEND_RESTART_MAX_BACKOFF: Duration = Duration::from_secs(2);
const MAX_BACKEND_RESTARTS: u32 = 5;
const RUNTIME_MANIFEST_SCHEMA: &str = "meeting_copilot.runtime_bundle.v1";
const COMPONENT_INVENTORY_SCHEMA: &str = "meeting_copilot.runtime_component_inventory.v1";
const RUNTIME_MANIFEST_ENV: &str = "MEETING_COPILOT_RUNTIME_MANIFEST";
const LOCAL_API_TOKEN_ENV: &str = "MEETING_COPILOT_LOCAL_API_TOKEN";
const TEST_TOKEN_OVERRIDE_ENV: &str = "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE";
const ALLOW_TEST_TOKEN_OVERRIDE_ENV: &str = "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE";
const HEALTH_PROOF_CONTEXT: &[u8] = b"meeting-copilot-health-v1";
const SESSION_COOKIE_CONTEXT: &[u8] = b"meeting-copilot-session-v1";
const STARTUP_REQUIRED_FILE_HASH_MAX_BYTES: u64 = 8 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize)]
struct RuntimeBundleManifest {
    schema_version: String,
    required_files: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ComponentInventoryMeasurement {
    size_bytes: u64,
    sha256: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
struct DirectoryComponentShape {
    size_bytes: u64,
    file_count: u64,
    symlink_count: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct RequiredFileSpec {
    relative: String,
    sha256: Option<String>,
}

#[derive(Debug, Clone)]
struct FileAsrCapability {
    status: String,
    available: bool,
    missing_components: Vec<String>,
    user_message: Option<String>,
    network_offline: Option<bool>,
    formats: BTreeMap<String, bool>,
    converter_path: Option<PathBuf>,
}

#[derive(Debug, Clone, Serialize)]
pub struct BackendRuntimeSnapshot {
    pub status: String,
    pub mode: String,
    pub base_url: Option<String>,
    pub port: Option<u16>,
    pub pid: Option<u32>,
    pub health_ready: bool,
    pub spawns_process: bool,
    pub resource_bundle_present: bool,
    pub authenticated_loopback: bool,
    pub health_identity_verified: bool,
    /// Total replacement attempts during this supervisor lifetime, including
    /// attempts whose spawn or health check failed.
    pub restart_count: u32,
    pub file_asr_status: String,
    pub file_asr_available: bool,
    pub file_asr_missing_components: Vec<String>,
    pub file_asr_user_message: Option<String>,
    pub file_asr_network_offline: Option<bool>,
    pub file_asr_formats: BTreeMap<String, bool>,
    pub errors: Vec<String>,
}

#[derive(Clone)]
pub struct BackendWebSocketConnection {
    pub url: String,
    pub cookie: String,
}

#[derive(Debug, Clone)]
pub struct RuntimeProviderConfig {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub realtime_model: String,
    pub api_style: String,
    pub provider_label: String,
}

impl Default for BackendRuntimeSnapshot {
    fn default() -> Self {
        Self {
            status: "not_started".to_string(),
            mode: "unconfigured".to_string(),
            base_url: None,
            port: None,
            pid: None,
            health_ready: false,
            spawns_process: false,
            resource_bundle_present: false,
            authenticated_loopback: false,
            health_identity_verified: false,
            restart_count: 0,
            file_asr_status: "not_configured".to_string(),
            file_asr_available: false,
            file_asr_missing_components: Vec::new(),
            file_asr_user_message: None,
            file_asr_network_offline: None,
            file_asr_formats: BTreeMap::new(),
            errors: Vec::new(),
        }
    }
}

#[derive(Clone)]
struct BackendLaunchConfig {
    launcher: PathBuf,
    runtime_bundle: PathBuf,
    data_dir: PathBuf,
    log_dir: PathBuf,
    port: u16,
    api_token: String,
    converter_path: Option<PathBuf>,
}

struct BackendMonitorHandle {
    stop_requested: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
}

#[derive(Default)]
struct BackendRuntimeState {
    child: Option<Child>,
    launch_config: Option<BackendLaunchConfig>,
    monitor: Option<BackendMonitorHandle>,
    stopping: bool,
    snapshot: BackendRuntimeSnapshot,
    api_token: Option<String>,
}

pub struct BackendSupervisor {
    inner: Arc<Mutex<BackendRuntimeState>>,
}

impl Default for BackendSupervisor {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(BackendRuntimeState::default())),
        }
    }
}

impl BackendSupervisor {
    fn take_child_for_shutdown(&self) -> Option<Child> {
        self.inner.lock().ok().and_then(|mut state| {
            state.stopping = true;
            state.launch_config = None;
            state.child.take()
        })
    }

    pub fn start_packaged(
        &self,
        runtime_bundle: &Path,
        data_dir: &Path,
        log_dir: &Path,
    ) -> Result<BackendRuntimeSnapshot, String> {
        let _ = self.stop();
        let file_asr = validate_runtime_bundle_and_inspect_file_asr(runtime_bundle)?;
        let api_token = generate_local_api_token()?;
        ensure_private_directory(data_dir)
            .map_err(|error| format!("failed to create backend data directory: {error}"))?;
        ensure_private_directory(log_dir)
            .map_err(|error| format!("failed to create backend log directory: {error}"))?;

        let port = reserve_loopback_port()?;
        let launch_config = BackendLaunchConfig {
            launcher: runtime_bundle.join("bin/meeting-copilot-backend"),
            runtime_bundle: runtime_bundle.to_path_buf(),
            data_dir: data_dir.to_path_buf(),
            log_dir: log_dir.to_path_buf(),
            port,
            api_token: api_token.clone(),
            converter_path: file_asr.converter_path.clone(),
        };
        let child = spawn_backend(&launch_config)?;
        let pid = child.id();
        let base_url = format!("http://127.0.0.1:{port}");
        let starting_snapshot = BackendRuntimeSnapshot {
            status: "starting".to_string(),
            mode: "packaged_bundled_backend".to_string(),
            base_url: Some(base_url.clone()),
            port: Some(port),
            pid: Some(pid),
            health_ready: false,
            spawns_process: true,
            resource_bundle_present: true,
            authenticated_loopback: true,
            health_identity_verified: false,
            restart_count: 0,
            file_asr_status: file_asr.status.clone(),
            file_asr_available: file_asr.available,
            file_asr_missing_components: file_asr.missing_components.clone(),
            file_asr_user_message: file_asr.user_message.clone(),
            file_asr_network_offline: file_asr.network_offline,
            file_asr_formats: file_asr.formats.clone(),
            errors: Vec::new(),
        };
        {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "backend supervisor lock poisoned")?;
            state.stopping = false;
            state.child = Some(child);
            state.launch_config = Some(launch_config);
            state.api_token = Some(api_token);
            state.snapshot = starting_snapshot;
        }
        let health_result = {
            let mut state = self
                .inner
                .lock()
                .map_err(|_| "backend supervisor lock poisoned")?;
            let api_token = state
                .api_token
                .clone()
                .ok_or_else(|| "packaged backend token is unavailable".to_string())?;
            wait_for_health(
                state
                    .child
                    .as_mut()
                    .ok_or_else(|| "packaged backend child is unavailable".to_string())?,
                port,
                &api_token,
                BACKEND_START_TIMEOUT,
            )
        };
        if let Err(error) = health_result {
            let child = self.take_child_for_shutdown();
            if let Some(mut child) = child {
                stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
            }
            return Err(error);
        }

        let snapshot = BackendRuntimeSnapshot {
            status: "ready".to_string(),
            mode: "packaged_bundled_backend".to_string(),
            base_url: Some(base_url),
            port: Some(port),
            pid: Some(pid),
            health_ready: true,
            spawns_process: true,
            resource_bundle_present: true,
            authenticated_loopback: true,
            health_identity_verified: true,
            restart_count: 0,
            file_asr_status: file_asr.status,
            file_asr_available: file_asr.available,
            file_asr_missing_components: file_asr.missing_components,
            file_asr_user_message: file_asr.user_message,
            file_asr_network_offline: file_asr.network_offline,
            file_asr_formats: file_asr.formats,
            errors: Vec::new(),
        };
        let mut state = self
            .inner
            .lock()
            .map_err(|_| "backend supervisor lock poisoned")?;
        state.snapshot = snapshot.clone();
        state.monitor = Some(start_backend_monitor(&self.inner));
        Ok(snapshot)
    }

    pub fn use_external(&self, base_url: String) -> BackendRuntimeSnapshot {
        let _ = self.stop();
        let snapshot = BackendRuntimeSnapshot {
            status: "ready".to_string(),
            mode: "external_development_backend".to_string(),
            base_url: Some(base_url),
            port: None,
            pid: None,
            health_ready: false,
            spawns_process: false,
            resource_bundle_present: false,
            authenticated_loopback: false,
            health_identity_verified: false,
            restart_count: 0,
            file_asr_status: "external_backend_not_probed".to_string(),
            file_asr_available: false,
            file_asr_missing_components: Vec::new(),
            file_asr_user_message: None,
            file_asr_network_offline: None,
            file_asr_formats: BTreeMap::new(),
            errors: Vec::new(),
        };
        if let Ok(mut state) = self.inner.lock() {
            state.api_token = None;
            state.snapshot = snapshot.clone();
        }
        snapshot
    }

    pub fn snapshot(&self) -> BackendRuntimeSnapshot {
        self.inner
            .lock()
            .map(|state| state.snapshot.clone())
            .unwrap_or_else(|_| BackendRuntimeSnapshot {
                status: "failed".to_string(),
                errors: vec!["backend supervisor lock poisoned".to_string()],
                ..BackendRuntimeSnapshot::default()
            })
    }

    pub fn workbench_url(&self) -> Result<String, String> {
        let state = self
            .inner
            .lock()
            .map_err(|_| "backend supervisor lock poisoned")?;
        let base_url = state
            .snapshot
            .base_url
            .as_deref()
            .ok_or_else(|| "backend base URL is unavailable".to_string())?;
        Ok(match state.api_token.as_deref() {
            Some(token) => format!("{base_url}/desktop/bootstrap?token={token}"),
            None => format!("{base_url}/workbench"),
        })
    }

    pub fn native_microphone_connection(
        &self,
        session_id: &str,
    ) -> Result<BackendWebSocketConnection, String> {
        if session_id.is_empty()
            || session_id.len() > 128
            || !session_id.chars().all(|character| {
                character.is_ascii_alphanumeric() || matches!(character, '_' | '-' | '.')
            })
        {
            return Err("session_id contains unsafe characters".to_string());
        }
        let state = self
            .inner
            .lock()
            .map_err(|_| "backend supervisor lock poisoned")?;
        let base_url = state
            .snapshot
            .base_url
            .as_deref()
            .ok_or_else(|| "backend base URL is unavailable".to_string())?;
        let ws_base = if let Some(value) = base_url.strip_prefix("http://") {
            format!("ws://{value}")
        } else if let Some(value) = base_url.strip_prefix("https://") {
            format!("wss://{value}")
        } else {
            return Err("backend base URL must use HTTP or HTTPS".to_string());
        };
        let ws_base = ws_base.trim_end_matches('/');
        let cookie = state
            .api_token
            .as_deref()
            .map(|token| {
                format!(
                    "meeting_copilot_session={}",
                    hmac_sha256_hex(token.as_bytes(), SESSION_COOKIE_CONTEXT)
                )
            })
            .unwrap_or_default();
        Ok(BackendWebSocketConnection {
            url: format!("{ws_base}/live/asr/stream/ws/{session_id}?audio_source=tauri_native_mic"),
            cookie,
        })
    }

    pub fn configure_provider(&self, config: &RuntimeProviderConfig) -> Result<(), String> {
        let body = serde_json::to_vec(&serde_json::json!({
            "base_url": config.base_url,
            "api_key": config.api_key,
            "model": config.model,
            "realtime_model": config.realtime_model,
            "api_style": config.api_style,
            "provider_label": config.provider_label,
        }))
        .map_err(|error| format!("failed to encode provider config: {error}"))?;
        self.send_authenticated_request("PUT", "/desktop/provider/config", Some(&body))
    }

    pub fn clear_provider(&self) -> Result<(), String> {
        self.send_authenticated_request("DELETE", "/desktop/provider/config", None)
    }

    fn send_authenticated_request(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
    ) -> Result<(), String> {
        let (port, api_token) = {
            let state = self
                .inner
                .lock()
                .map_err(|_| "backend supervisor lock poisoned".to_string())?;
            let port = state
                .snapshot
                .port
                .ok_or_else(|| "packaged backend port is unavailable".to_string())?;
            let api_token = state
                .api_token
                .clone()
                .ok_or_else(|| "packaged backend token is unavailable".to_string())?;
            (port, api_token)
        };
        let body = body.unwrap_or_default();
        let mut stream = TcpStream::connect_timeout(
            &SocketAddr::from(([127, 0, 0, 1], port)),
            Duration::from_secs(2),
        )
        .map_err(|error| format!("failed to connect to packaged backend: {error}"))?;
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .map_err(|error| format!("failed to configure backend read timeout: {error}"))?;
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .map_err(|error| format!("failed to configure backend write timeout: {error}"))?;
        let request = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nX-Meeting-Copilot-Token: {api_token}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            body.len(),
        );
        stream
            .write_all(request.as_bytes())
            .and_then(|_| stream.write_all(body))
            .map_err(|error| format!("failed to send provider config to backend: {error}"))?;
        let mut response = Vec::with_capacity(4096);
        stream
            .read_to_end(&mut response)
            .map_err(|error| format!("failed to read provider config response: {error}"))?;
        if response.starts_with(b"HTTP/1.1 200") || response.starts_with(b"HTTP/1.0 200") {
            Ok(())
        } else {
            let status = String::from_utf8_lossy(
                response
                    .split(|byte| *byte == b'\n')
                    .next()
                    .unwrap_or_default(),
            );
            Err(format!(
                "packaged backend rejected provider config: {}",
                status.trim()
            ))
        }
    }

    pub fn stop(&self) -> BackendRuntimeSnapshot {
        let (monitor, child) = match self.inner.lock() {
            Ok(mut state) => {
                state.stopping = true;
                (state.monitor.take(), state.child.take())
            }
            Err(_) => {
                return BackendRuntimeSnapshot {
                    status: "failed".to_string(),
                    errors: vec!["backend supervisor lock poisoned".to_string()],
                    ..BackendRuntimeSnapshot::default()
                }
            }
        };
        if let Some(mut monitor) = monitor {
            monitor.stop_and_join();
        }
        if let Some(mut child) = child {
            stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
        }
        self.inner
            .lock()
            .map(|mut state| {
                state.api_token = None;
                state.launch_config = None;
                state.snapshot.status = "stopped".to_string();
                state.snapshot.health_ready = false;
                state.snapshot.pid = None;
                state.snapshot.clone()
            })
            .unwrap_or_else(|_| BackendRuntimeSnapshot {
                status: "failed".to_string(),
                errors: vec!["backend supervisor lock poisoned".to_string()],
                ..BackendRuntimeSnapshot::default()
            })
    }
}

impl Drop for BackendSupervisor {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

pub fn resolve_runtime_bundle(resource_dir: &Path, override_path: Option<&Path>) -> PathBuf {
    override_path
        .map(Path::to_path_buf)
        .unwrap_or_else(|| resource_dir.join("MeetingCopilotRuntime.bundle"))
}

pub fn validate_runtime_bundle(runtime_bundle: &Path) -> Result<(), String> {
    validate_runtime_bundle_and_inspect_file_asr(runtime_bundle).map(|_| ())
}

fn validate_runtime_bundle_and_inspect_file_asr(
    runtime_bundle: &Path,
) -> Result<FileAsrCapability, String> {
    let manifest_path = runtime_bundle.join("runtime-bundle-manifest.json");
    let manifest_text = fs::read_to_string(&manifest_path).map_err(|error| {
        format!("bundled runtime is incomplete: runtime-bundle-manifest.json ({error})")
    })?;
    let manifest: RuntimeBundleManifest = serde_json::from_str(&manifest_text)
        .map_err(|error| format!("bundled runtime manifest is invalid: {error}"))?;
    if manifest.schema_version != RUNTIME_MANIFEST_SCHEMA {
        return Err(format!(
            "bundled runtime manifest schema is invalid: {}",
            manifest.schema_version
        ));
    }
    if manifest.required_files.is_empty() {
        return Err("bundled runtime manifest required_files are empty".to_string());
    }
    for relative in &manifest.required_files {
        validate_bundle_relative_path(relative)?;
    }
    let missing: Vec<&str> = manifest
        .required_files
        .iter()
        .map(String::as_str)
        .filter(|relative| !runtime_bundle.join(relative).is_file())
        .collect();
    if !missing.is_empty() {
        Err(format!(
            "bundled runtime is incomplete: {}",
            missing.join(", ")
        ))
    } else {
        inspect_file_asr_capability(runtime_bundle)
    }
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let mut file = fs::File::open(path).map_err(|error| {
        format!(
            "failed to read runtime component {}: {error}",
            path.display()
        )
    })?;
    let mut digest = Sha256::new();
    let mut buffer = [0_u8; 1024 * 1024];
    loop {
        let count = file.read(&mut buffer).map_err(|error| {
            format!(
                "failed to hash runtime component {}: {error}",
                path.display()
            )
        })?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    Ok(hex::encode(digest.finalize()))
}

fn file_component_inventory(path: &Path) -> Result<ComponentInventoryMeasurement, String> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        format!(
            "runtime component file is missing or unreadable: {} ({error})",
            path.display()
        )
    })?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!(
            "runtime component is not a regular file: {}",
            path.display()
        ));
    }
    Ok(ComponentInventoryMeasurement {
        size_bytes: metadata.len(),
        sha256: sha256_file(path)?,
    })
}

fn directory_component_shape(
    path: &Path,
    allowed_root: &Path,
) -> Result<DirectoryComponentShape, String> {
    let metadata = fs::symlink_metadata(path).map_err(|error| {
        format!(
            "runtime component directory is missing or unreadable: {} ({error})",
            path.display()
        )
    })?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(format!(
            "runtime component is not a regular directory: {}",
            path.display()
        ));
    }

    let mut shape = DirectoryComponentShape::default();
    collect_directory_shape(path, allowed_root, &mut shape)?;
    Ok(shape)
}

fn collect_directory_shape(
    current: &Path,
    allowed_root: &Path,
    shape: &mut DirectoryComponentShape,
) -> Result<(), String> {
    let read_dir = fs::read_dir(current).map_err(|error| {
        format!(
            "failed to inspect runtime component directory {}: {error}",
            current.display()
        )
    })?;
    for entry in read_dir {
        let entry = entry.map_err(|error| {
            format!(
                "failed to inspect runtime component directory {}: {error}",
                current.display()
            )
        })?;
        let candidate = entry.path();
        let metadata = fs::symlink_metadata(&candidate).map_err(|error| {
            format!(
                "failed to inspect runtime component entry {}: {error}",
                candidate.display()
            )
        })?;
        if metadata.file_type().is_symlink() {
            let resolved = fs::canonicalize(&candidate).map_err(|error| {
                format!(
                    "runtime component contains an unreadable symlink: {} ({error})",
                    candidate.display()
                )
            })?;
            if resolved.strip_prefix(allowed_root).is_err() {
                return Err(format!(
                    "runtime component contains external symlink: {}",
                    candidate.display()
                ));
            }
            let target_metadata = fs::metadata(&candidate).map_err(|error| {
                format!(
                    "runtime component symlink target is unreadable: {} ({error})",
                    candidate.display()
                )
            })?;
            if !target_metadata.is_file() {
                return Err(format!(
                    "runtime component symlink target is not a file: {}",
                    candidate.display()
                ));
            }
            shape.size_bytes = shape
                .size_bytes
                .checked_add(target_metadata.len())
                .ok_or_else(|| format!("runtime component size overflow: {}", current.display()))?;
            shape.file_count = shape.file_count.checked_add(1).ok_or_else(|| {
                format!(
                    "runtime component file count overflow: {}",
                    current.display()
                )
            })?;
            shape.symlink_count = shape.symlink_count.checked_add(1).ok_or_else(|| {
                format!(
                    "runtime component symlink count overflow: {}",
                    current.display()
                )
            })?;
        } else if metadata.is_file() {
            shape.size_bytes = shape
                .size_bytes
                .checked_add(metadata.len())
                .ok_or_else(|| format!("runtime component size overflow: {}", current.display()))?;
            shape.file_count = shape.file_count.checked_add(1).ok_or_else(|| {
                format!(
                    "runtime component file count overflow: {}",
                    current.display()
                )
            })?;
        } else if metadata.is_dir() {
            collect_directory_shape(&candidate, allowed_root, shape)?;
        }
    }
    Ok(())
}

fn is_sha256_hex(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn manifest_string<'a>(
    manifest: &'a serde_json::Value,
    pointer: &str,
    field: &str,
) -> Result<&'a str, String> {
    manifest
        .pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("sealed file ASR manifest field is missing or invalid: {field}"))
}

fn sealed_component_inventory_components<'a>(
    manifest: &'a serde_json::Value,
) -> Result<Option<&'a serde_json::Map<String, serde_json::Value>>, String> {
    let Some(inventory) = manifest.get("component_inventory") else {
        return Ok(None);
    };
    let schema = inventory
        .get("schema_version")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "runtime component inventory schema is missing".to_string())?;
    if schema != COMPONENT_INVENTORY_SCHEMA {
        return Err(format!(
            "runtime component inventory schema is invalid: {schema}"
        ));
    }
    let status = inventory
        .get("status")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| "runtime component inventory status is missing".to_string())?;
    if status == "unsealed" {
        return Ok(None);
    }
    if status != "sealed" {
        return Err(format!(
            "runtime component inventory status is invalid: {status}"
        ));
    }
    inventory
        .get("components")
        .and_then(serde_json::Value::as_object)
        .filter(|components| !components.is_empty())
        .ok_or_else(|| "runtime sealed component inventory is empty or invalid".to_string())
        .map(Some)
}

fn manifest_u64(manifest: &serde_json::Value, pointer: &str, field: &str) -> Result<u64, String> {
    manifest
        .pointer(pointer)
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| format!("sealed file ASR manifest field is missing or invalid: {field}"))
}

fn manifest_sha256<'a>(
    manifest: &'a serde_json::Value,
    pointer: &str,
    field: &str,
) -> Result<&'a str, String> {
    let value = manifest_string(manifest, pointer, field)?;
    if !is_sha256_hex(value) {
        return Err(format!("sealed file ASR manifest hash is invalid: {field}"));
    }
    Ok(value)
}

fn sealed_component<'a>(
    components: &'a serde_json::Map<String, serde_json::Value>,
    component_name: &str,
    manifest_relative: &str,
    expected_kind: &str,
) -> Result<&'a serde_json::Value, String> {
    validate_bundle_relative_path(manifest_relative)?;
    let component = components.get(component_name).ok_or_else(|| {
        format!(
            "runtime sealed component inventory is missing required file ASR component: {component_name}"
        )
    })?;
    let inventory_relative = component
        .get("path")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("runtime sealed component path is invalid: {component_name}"))?;
    validate_bundle_relative_path(inventory_relative)?;
    if inventory_relative != manifest_relative {
        return Err(format!(
            "runtime sealed component path mismatch: {component_name}"
        ));
    }
    let kind = component
        .get("kind")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("runtime sealed component kind is invalid: {component_name}"))?;
    if kind != expected_kind {
        return Err(format!(
            "runtime sealed component kind mismatch: {component_name}"
        ));
    }
    Ok(component)
}

fn sealed_component_measurement(
    component: &serde_json::Value,
    component_name: &str,
) -> Result<ComponentInventoryMeasurement, String> {
    let size_bytes = component
        .get("size_bytes")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| format!("runtime sealed component size is invalid: {component_name}"))?;
    let sha256 = component
        .get("sha256")
        .and_then(serde_json::Value::as_str)
        .ok_or_else(|| format!("runtime sealed component hash is invalid: {component_name}"))?;
    if !is_sha256_hex(sha256) {
        return Err(format!(
            "runtime sealed component hash is invalid: {component_name}"
        ));
    }
    Ok(ComponentInventoryMeasurement {
        size_bytes,
        sha256: sha256.to_string(),
    })
}

fn validate_business_manifest_mirror(
    manifest: &serde_json::Value,
    component_name: &str,
    business_pointer: &str,
    expected: &ComponentInventoryMeasurement,
) -> Result<(), String> {
    let size_pointer = format!("{business_pointer}/size_bytes");
    let hash_pointer = format!("{business_pointer}/sha256");
    let business_size = manifest_u64(manifest, &size_pointer, &size_pointer)?;
    if business_size != expected.size_bytes {
        return Err(format!(
            "runtime sealed component business manifest size mismatch: {component_name}"
        ));
    }
    let business_sha256 = manifest_sha256(manifest, &hash_pointer, &hash_pointer)?;
    if business_sha256 != expected.sha256 {
        return Err(format!(
            "runtime sealed component business manifest hash mismatch: {component_name}"
        ));
    }
    Ok(())
}

fn validate_sealed_file_component(
    runtime_bundle: &Path,
    manifest: &serde_json::Value,
    components: &serde_json::Map<String, serde_json::Value>,
    component_name: &str,
    manifest_relative: &str,
    business_pointers: &[&str],
) -> Result<(), String> {
    let component = sealed_component(components, component_name, manifest_relative, "file")?;
    let expected = sealed_component_measurement(component, component_name)?;
    for pointer in business_pointers {
        validate_business_manifest_mirror(manifest, component_name, pointer, &expected)?;
    }
    let path = runtime_bundle.join(manifest_relative);
    let observed = file_component_inventory(&path)?;
    if observed.size_bytes != expected.size_bytes {
        return Err(format!(
            "runtime sealed component size mismatch: {component_name}"
        ));
    }
    if observed.sha256 != expected.sha256 {
        return Err(format!(
            "runtime sealed component hash mismatch: {component_name}"
        ));
    }
    Ok(())
}

fn model_required_file_specs(
    manifest: &serde_json::Value,
    model_name: &str,
) -> Result<Vec<RequiredFileSpec>, String> {
    let model_pointer = format!("/file_asr/models/{model_name}");
    let Some(model) = manifest
        .pointer(&model_pointer)
        .and_then(serde_json::Value::as_object)
    else {
        return Ok(Vec::new());
    };
    let Some(required_files) = model
        .get("required_files")
        .and_then(serde_json::Value::as_array)
    else {
        return Ok(Vec::new());
    };

    let mut mapped_hashes = BTreeMap::new();
    for field in ["required_file_hashes", "required_file_sha256"] {
        let Some(value) = model.get(field) else {
            continue;
        };
        let hashes = value.as_object().ok_or_else(|| {
            format!("sealed file ASR model required file hashes are invalid: {model_name}")
        })?;
        for (relative, hash) in hashes {
            validate_bundle_relative_path(relative)?;
            let hash = hash.as_str().ok_or_else(|| {
                format!("sealed file ASR model required file hash is invalid: {model_name}")
            })?;
            if !is_sha256_hex(hash) {
                return Err(format!(
                    "sealed file ASR model required file hash is invalid: {model_name}"
                ));
            }
            if let Some(previous) = mapped_hashes.insert(relative.clone(), hash.to_string()) {
                if previous != hash {
                    return Err(format!(
                        "sealed file ASR model required file hashes disagree: {model_name}"
                    ));
                }
            }
        }
    }

    let mut specs = Vec::with_capacity(required_files.len());
    for value in required_files {
        let (relative, inline_hash) = if let Some(relative) = value.as_str() {
            (relative.to_string(), None)
        } else if let Some(spec) = value.as_object() {
            let relative = spec
                .get("path")
                .or_else(|| spec.get("relative"))
                .and_then(serde_json::Value::as_str)
                .ok_or_else(|| {
                    format!("sealed file ASR model required file is invalid: {model_name}")
                })?;
            let hash = spec.get("sha256").and_then(serde_json::Value::as_str);
            if hash.is_some_and(|hash| !is_sha256_hex(hash)) {
                return Err(format!(
                    "sealed file ASR model required file hash is invalid: {model_name}"
                ));
            }
            (relative.to_string(), hash.map(str::to_string))
        } else {
            return Err(format!(
                "sealed file ASR model required file is invalid: {model_name}"
            ));
        };
        validate_bundle_relative_path(&relative)?;
        let mapped_hash = mapped_hashes.remove(&relative);
        if inline_hash.is_some() && mapped_hash.is_some() && inline_hash != mapped_hash {
            return Err(format!(
                "sealed file ASR model required file hashes disagree: {model_name}"
            ));
        }
        specs.push(RequiredFileSpec {
            relative,
            sha256: inline_hash.or(mapped_hash),
        });
    }
    if !mapped_hashes.is_empty() {
        return Err(format!(
            "sealed file ASR model required file hash has no matching path: {model_name}"
        ));
    }
    Ok(specs)
}

fn validate_required_files(
    root: &Path,
    allowed_root: &Path,
    component_name: &str,
    required_files: &[RequiredFileSpec],
) -> Result<(), String> {
    for required in required_files {
        validate_bundle_relative_path(&required.relative)?;
        let path = root.join(&required.relative);
        let metadata = fs::symlink_metadata(&path).map_err(|error| {
            format!(
                "runtime sealed component required file is missing: {component_name}: {} ({error})",
                required.relative
            )
        })?;
        let (hash_path, file_size) = if metadata.file_type().is_symlink() {
            let resolved = fs::canonicalize(&path).map_err(|error| {
                format!(
                    "runtime sealed component required file symlink is unreadable: {component_name}: {} ({error})",
                    required.relative
                )
            })?;
            if resolved.strip_prefix(allowed_root).is_err() {
                return Err(format!(
                    "runtime sealed component required file contains external symlink: {component_name}: {}",
                    required.relative
                ));
            }
            let target = fs::metadata(&resolved).map_err(|error| {
                format!(
                    "runtime sealed component required file is unreadable: {component_name}: {} ({error})",
                    required.relative
                )
            })?;
            if !target.is_file() {
                return Err(format!(
                    "runtime sealed component required path is not a file: {component_name}: {}",
                    required.relative
                ));
            }
            (resolved, target.len())
        } else {
            if !metadata.is_file() {
                return Err(format!(
                    "runtime sealed component required path is not a file: {component_name}: {}",
                    required.relative
                ));
            }
            (path, metadata.len())
        };
        if let Some(expected_sha256) = &required.sha256 {
            // Small configs can be hashed cheaply; large weights remain covered by the sealed
            // build-time directory hash, startup shape validation, and release code signing.
            if file_size <= STARTUP_REQUIRED_FILE_HASH_MAX_BYTES
                && sha256_file(&hash_path)? != *expected_sha256
            {
                return Err(format!(
                    "runtime sealed component required file hash mismatch: {component_name}: {}",
                    required.relative
                ));
            }
        }
    }
    Ok(())
}

fn validate_sealed_directory_component(
    runtime_bundle: &Path,
    allowed_root: &Path,
    manifest: &serde_json::Value,
    components: &serde_json::Map<String, serde_json::Value>,
    component_name: &str,
    manifest_relative: &str,
    business_pointers: &[&str],
    required_files: &[RequiredFileSpec],
) -> Result<(), String> {
    let component = sealed_component(components, component_name, manifest_relative, "directory")?;
    let expected = sealed_component_measurement(component, component_name)?;
    for pointer in business_pointers {
        validate_business_manifest_mirror(manifest, component_name, pointer, &expected)?;
    }
    let expected_file_count = component
        .get("file_count")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| {
            format!("runtime sealed component file count is invalid: {component_name}")
        })?;
    let expected_symlink_count = component
        .get("symlink_count")
        .and_then(serde_json::Value::as_u64)
        .ok_or_else(|| {
            format!("runtime sealed component symlink count is invalid: {component_name}")
        })?;

    // Packaging computes the full directory SHA-256 and mirrors it into the business manifest.
    // Startup verifies only metadata shape to avoid reading multi-GB model/runtime contents. Public
    // release authenticity still depends on a valid macOS code signature over sealed resources.
    let root = runtime_bundle.join(manifest_relative);
    let observed = directory_component_shape(&root, allowed_root)?;
    if observed.size_bytes != expected.size_bytes {
        return Err(format!(
            "runtime sealed component size mismatch: {component_name}"
        ));
    }
    if observed.file_count != expected_file_count {
        return Err(format!(
            "runtime sealed component file count mismatch: {component_name}"
        ));
    }
    // Tauri may materialize every internal venv file symlink while copying app resources.
    let tauri_materialized_internal_symlinks =
        expected_symlink_count > 0 && observed.symlink_count == 0;
    if observed.symlink_count != expected_symlink_count && !tauri_materialized_internal_symlinks {
        return Err(format!(
            "runtime sealed component symlink count mismatch: {component_name}"
        ));
    }
    validate_required_files(&root, allowed_root, component_name, required_files)
}

fn validate_sealed_file_asr_inventory(
    runtime_bundle: &Path,
    manifest: &serde_json::Value,
) -> Result<(), String> {
    let Some(components) = sealed_component_inventory_components(manifest)? else {
        return Ok(());
    };
    let allowed_root = fs::canonicalize(runtime_bundle).map_err(|error| {
        format!(
            "runtime bundle root is missing or unreadable: {} ({error})",
            runtime_bundle.display()
        )
    })?;

    let runtime_root =
        manifest_string(manifest, "/file_asr/runtime/root", "file_asr.runtime.root")?;
    let shared_runtime_root =
        manifest_string(manifest, "/runtimes/funasr/root", "runtimes.funasr.root")?;
    if runtime_root != shared_runtime_root {
        return Err("sealed file ASR runtime roots disagree".to_string());
    }
    let runtime_executable = manifest_string(
        manifest,
        "/file_asr/runtime/executable",
        "file_asr.runtime.executable",
    )?;
    let worker = manifest_string(manifest, "/file_asr/worker/path", "file_asr.worker.path")?;
    let workers_worker = manifest_string(manifest, "/workers/file_asr", "workers.file_asr")?;
    if worker != workers_worker {
        return Err("sealed file ASR worker paths disagree".to_string());
    }
    let inventory_worker = manifest_string(
        manifest,
        "/worker_inventory/file_asr/path",
        "worker_inventory.file_asr.path",
    )?;
    if worker != inventory_worker {
        return Err("sealed file ASR worker inventory paths disagree".to_string());
    }
    let converter = manifest_string(
        manifest,
        "/file_asr/converter/path",
        "file_asr.converter.path",
    )?;
    let converter_license = manifest_string(
        manifest,
        "/file_asr/converter/license_path",
        "file_asr.converter.license_path",
    )?;
    let converter_path = runtime_bundle.join(converter);
    let converter_license_path = runtime_bundle.join(converter_license);

    validate_sealed_file_component(
        runtime_bundle,
        manifest,
        components,
        "file_asr.python_launcher",
        runtime_executable,
        &[],
    )?;
    validate_sealed_file_component(
        runtime_bundle,
        manifest,
        components,
        "file_asr.worker",
        worker,
        &["/file_asr/worker", "/worker_inventory/file_asr"],
    )?;
    if (converter_path.is_file() && converter_license_path.is_file())
        || components.contains_key("file_asr.converter")
    {
        validate_sealed_file_component(
            runtime_bundle,
            manifest,
            components,
            "file_asr.converter",
            converter,
            &["/file_asr/converter"],
        )?;
    }
    validate_sealed_directory_component(
        runtime_bundle,
        &allowed_root,
        manifest,
        components,
        "shared_asr.runtime",
        runtime_root,
        &["/file_asr/runtime", "/runtimes/funasr"],
        &[],
    )?;

    for (model_name, component_name) in [
        ("offline", "file_asr.model.offline"),
        ("vad", "file_asr.model.vad"),
        ("punc", "file_asr.model.punc"),
    ] {
        let root_pointer = format!("/file_asr/models/{model_name}/root");
        let root = manifest_string(
            manifest,
            &root_pointer,
            &format!("file_asr.models.{model_name}.root"),
        )?;
        let path = runtime_bundle.join(root);
        if path.is_dir() || components.contains_key(component_name) {
            let required_files = model_required_file_specs(manifest, model_name)?;
            if required_files.is_empty() {
                return Err(format!(
                    "sealed file ASR model required files are missing: {model_name}"
                ));
            }
            let business_pointer = format!("/file_asr/models/{model_name}");
            validate_sealed_directory_component(
                runtime_bundle,
                &allowed_root,
                manifest,
                components,
                component_name,
                root,
                &[business_pointer.as_str()],
                &required_files,
            )?;
        }
    }
    Ok(())
}

fn inspect_file_asr_capability(runtime_bundle: &Path) -> Result<FileAsrCapability, String> {
    let manifest_path = runtime_bundle.join("runtime-bundle-manifest.json");
    let manifest_text = fs::read_to_string(&manifest_path).map_err(|error| {
        format!("bundled runtime is incomplete: runtime-bundle-manifest.json ({error})")
    })?;
    let manifest: serde_json::Value = serde_json::from_str(&manifest_text)
        .map_err(|error| format!("bundled runtime manifest is invalid: {error}"))?;
    let mut missing_components = Vec::new();

    inspect_file_component(
        runtime_bundle,
        manifest
            .pointer("/file_asr/runtime/executable")
            .and_then(|value| value.as_str()),
        "funasr_python",
        &mut missing_components,
    )?;
    inspect_file_component(
        runtime_bundle,
        manifest
            .pointer("/file_asr/worker/path")
            .and_then(|value| value.as_str())
            .or_else(|| {
                manifest
                    .pointer("/workers/file_asr")
                    .and_then(|value| value.as_str())
            }),
        "worker",
        &mut missing_components,
    )?;
    for (manifest_name, component_name) in [
        ("offline", "offline_model"),
        ("vad", "vad_model"),
        ("punc", "punc_model"),
    ] {
        let root_pointer = format!("/file_asr/models/{manifest_name}/root");
        let root = manifest
            .pointer(&root_pointer)
            .and_then(|value| value.as_str());
        let required = model_required_file_specs(&manifest, manifest_name)?;
        inspect_directory_component(
            runtime_bundle,
            root,
            &required,
            component_name,
            &mut missing_components,
        )?;
    }
    validate_sealed_file_asr_inventory(runtime_bundle, &manifest)?;
    let core_available = missing_components.is_empty();
    let only_models_missing = !core_available
        && missing_components
            .iter()
            .all(|name| name.ends_with("_model"));
    let converter_relative = manifest
        .pointer("/file_asr/converter/path")
        .and_then(|value| value.as_str());
    let converter_license_relative = manifest
        .pointer("/file_asr/converter/license_path")
        .and_then(|value| value.as_str());
    let converter_path = match converter_relative {
        Some(relative) => {
            validate_bundle_relative_path(relative)?;
            let path = runtime_bundle.join(relative);
            path.is_file().then_some(path)
        }
        None => None,
    };
    let converter_license_ready = match converter_license_relative {
        Some(relative) => {
            validate_bundle_relative_path(relative)?;
            runtime_bundle.join(relative).is_file()
        }
        None => false,
    };
    let converter_ready = converter_path.is_some() && converter_license_ready;
    let package_approved = manifest
        .pointer("/file_asr/package/install_status")
        .and_then(|value| value.as_str())
        == Some("bundled")
        && manifest
            .pointer("/file_asr/package/redistribution_status")
            .and_then(|value| value.as_str())
            == Some("approved")
        && manifest
            .pointer("/file_asr/redistribution/status")
            .and_then(|value| value.as_str())
            == Some("approved_by_upstream_license");
    if core_available && !package_approved {
        missing_components.push("redistribution_evidence".to_string());
    }
    let available = core_available && package_approved;
    let mut formats = BTreeMap::new();
    formats.insert("wav".to_string(), available);
    formats.insert("m4a".to_string(), available && converter_ready);
    formats.insert("mp3".to_string(), available && converter_ready);
    let status = if available && converter_ready {
        "ready"
    } else if available {
        "file_asr_converter_not_installed"
    } else if only_models_missing {
        "file_asr_models_not_installed"
    } else {
        "file_asr_runtime_not_installed"
    };
    Ok(FileAsrCapability {
        status: status.to_string(),
        available,
        missing_components,
        user_message: if status == "ready" {
            None
        } else if status == "file_asr_converter_not_installed" {
            Some("部分文件格式转换组件未安装".to_string())
        } else {
            Some("文件导入组件未安装".to_string())
        },
        network_offline: None,
        formats,
        converter_path: converter_ready.then_some(converter_path).flatten(),
    })
}

fn inspect_file_component(
    runtime_bundle: &Path,
    relative: Option<&str>,
    component_name: &str,
    missing_components: &mut Vec<String>,
) -> Result<(), String> {
    let Some(relative) = relative else {
        missing_components.push(component_name.to_string());
        return Ok(());
    };
    validate_bundle_relative_path(relative)?;
    if !runtime_bundle.join(relative).is_file() {
        missing_components.push(component_name.to_string());
    }
    Ok(())
}

fn inspect_directory_component(
    runtime_bundle: &Path,
    relative: Option<&str>,
    required_files: &[RequiredFileSpec],
    component_name: &str,
    missing_components: &mut Vec<String>,
) -> Result<(), String> {
    let Some(relative) = relative else {
        missing_components.push(component_name.to_string());
        return Ok(());
    };
    validate_bundle_relative_path(relative)?;
    let root = runtime_bundle.join(relative);
    let mut missing = !root.is_dir() || required_files.is_empty();
    for required in required_files {
        validate_bundle_relative_path(&required.relative)?;
        missing |= !root.join(&required.relative).is_file();
    }
    if missing {
        missing_components.push(component_name.to_string());
    }
    Ok(())
}

fn validate_bundle_relative_path(relative: &str) -> Result<(), String> {
    use std::path::Component;

    if relative.is_empty() || relative.contains('\\') {
        return Err(format!(
            "bundled runtime manifest contains unsafe path: {relative}"
        ));
    }
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return Err(format!(
            "bundled runtime manifest contains unsafe path: {relative}"
        ));
    }
    if path.components().any(|component| {
        let value = component.as_os_str().to_string_lossy().to_ascii_lowercase();
        matches!(value.as_str(), ".cache" | ".venv" | ".venv-funasr")
    }) {
        return Err(format!(
            "bundled runtime manifest contains forbidden development/cache path: {relative}"
        ));
    }
    Ok(())
}

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("failed to reserve loopback port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("failed to read loopback port: {error}"))
}

fn spawn_backend(config: &BackendLaunchConfig) -> Result<Child, String> {
    let stdout = open_private_file(&config.log_dir.join("backend.stdout.log"), true)
        .map_err(|error| format!("failed to open backend stdout log: {error}"))?;
    let stderr = open_private_file(&config.log_dir.join("backend.stderr.log"), true)
        .map_err(|error| format!("failed to open backend stderr log: {error}"))?;

    let mut command = Command::new("/bin/sh");
    command
        .arg(&config.launcher)
        .current_dir(&config.runtime_bundle)
        .env("MEETING_COPILOT_PORT", config.port.to_string())
        .env("MEETING_COPILOT_DATA_DIR", &config.data_dir)
        .env("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
        .env("MEETING_COPILOT_PARENT_PID", std::process::id().to_string())
        .env(
            RUNTIME_MANIFEST_ENV,
            config.runtime_bundle.join("runtime-bundle-manifest.json"),
        )
        .env(LOCAL_API_TOKEN_ENV, &config.api_token)
        .env_remove("LLM_GATEWAY_BASE_URL")
        .env_remove("LLM_GATEWAY_API_KEY")
        .env_remove("LLM_GATEWAY_MODEL")
        .env_remove("LLM_GATEWAY_TIMEOUT_SECONDS")
        .env_remove("LLM_GATEWAY_PROVIDER_LABEL")
        .env_remove("LLM_GATEWAY_IS_MOCK")
        .env_remove("PYTHONPATH")
        .env_remove("VIRTUAL_ENV")
        .env_remove("UV_PROJECT_ENVIRONMENT")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    if let Some(converter_path) = &config.converter_path {
        command.env("IMAGEIO_FFMPEG_EXE", converter_path);
    } else {
        command.env_remove("IMAGEIO_FFMPEG_EXE");
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }

    command
        .spawn()
        .map_err(|error| format!("failed to spawn bundled backend: {error}"))
}

fn start_backend_monitor(inner: &Arc<Mutex<BackendRuntimeState>>) -> BackendMonitorHandle {
    let stop_requested = Arc::new(AtomicBool::new(false));
    let thread_stop_requested = Arc::clone(&stop_requested);
    let thread_inner = Arc::clone(inner);
    let join = thread::spawn(move || monitor_backend(thread_inner, thread_stop_requested));
    BackendMonitorHandle {
        stop_requested,
        join: Some(join),
    }
}

impl BackendMonitorHandle {
    fn stop_and_join(&mut self) {
        self.stop_requested.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

fn monitor_backend(inner: Arc<Mutex<BackendRuntimeState>>, stop_requested: Arc<AtomicBool>) {
    loop {
        if stop_requested.load(Ordering::Acquire) {
            return;
        }

        let exited_child = {
            let mut state = match inner.lock() {
                Ok(state) => state,
                Err(_) => return,
            };
            if state.stopping {
                return;
            }
            let Some(child) = state.child.as_mut() else {
                return;
            };
            match child.try_wait() {
                Ok(None) => None,
                Ok(Some(status)) => {
                    state.snapshot.status = "restarting".to_string();
                    state.snapshot.health_ready = false;
                    state.snapshot.health_identity_verified = false;
                    state.snapshot.pid = None;
                    state.snapshot.errors.clear();
                    state.child.take().map(|child| (child, status.to_string()))
                }
                Err(error) => {
                    state.snapshot.status = "failed".to_string();
                    state.snapshot.health_ready = false;
                    state.snapshot.health_identity_verified = false;
                    state.snapshot.pid = None;
                    state.snapshot.errors = vec![format!(
                        "failed to inspect bundled backend process: {error}"
                    )];
                    return;
                }
            }
        };

        let Some((mut exited_child, _status)) = exited_child else {
            thread::sleep(BACKEND_MONITOR_INTERVAL);
            continue;
        };
        stop_child_process_group(&mut exited_child, BACKEND_STOP_TIMEOUT);

        let config = match inner.lock() {
            Ok(state) if !state.stopping => state.launch_config.clone(),
            _ => None,
        };
        let Some(config) = config else {
            return;
        };

        let mut last_error = "bundled backend exited unexpectedly".to_string();
        let mut replacement = None;
        loop {
            if stop_requested.load(Ordering::Acquire) || supervisor_is_stopping(&inner) {
                return;
            }
            let attempt = if let Ok(mut state) = inner.lock() {
                let Some(attempt) = claim_restart_attempt(&mut state.snapshot) else {
                    break;
                };
                attempt
            } else {
                return;
            };
            if let Ok(mut state) = inner.lock() {
                state.snapshot.status = "restarting".to_string();
                state.snapshot.health_ready = false;
                state.snapshot.health_identity_verified = false;
                state.snapshot.pid = None;
            } else {
                return;
            }

            if !wait_for_restart_backoff(&stop_requested, restart_backoff(attempt)) {
                return;
            }

            let result = spawn_and_wait_for_health(
                || spawn_backend(&config),
                |child| {
                    wait_for_health_cancelable(
                        child,
                        config.port,
                        &config.api_token,
                        BACKEND_START_TIMEOUT,
                        || stop_requested.load(Ordering::Acquire) || supervisor_is_stopping(&inner),
                    )
                },
                |mut child| stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT),
            );
            match result {
                Ok(child) => {
                    if supervisor_is_stopping(&inner) || stop_requested.load(Ordering::Acquire) {
                        let mut child = child;
                        stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
                        return;
                    }
                    replacement = Some(child);
                    break;
                }
                Err(error) => {
                    last_error = error;
                }
            }
        }

        if let Some(child) = replacement {
            if let Ok(mut state) = inner.lock() {
                if state.stopping {
                    drop(state);
                    let mut child = child;
                    stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
                    return;
                }
                let pid = child.id();
                state.child = Some(child);
                state.snapshot.status = "ready".to_string();
                state.snapshot.health_ready = true;
                state.snapshot.health_identity_verified = true;
                state.snapshot.pid = Some(pid);
                state.snapshot.errors.clear();
                continue;
            }
            let mut child = child;
            stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
            return;
        }

        if let Ok(mut state) = inner.lock() {
            if state.stopping {
                return;
            }
            state.snapshot.status = "failed".to_string();
            state.snapshot.health_ready = false;
            state.snapshot.health_identity_verified = false;
            state.snapshot.pid = None;
            state.snapshot.errors = vec![format!(
                "bundled backend restart budget exhausted after {MAX_BACKEND_RESTARTS} attempts: {last_error}"
            )];
        }
        return;
    }
}

fn supervisor_is_stopping(inner: &Arc<Mutex<BackendRuntimeState>>) -> bool {
    inner.lock().map(|state| state.stopping).unwrap_or(true)
}

fn claim_restart_attempt(snapshot: &mut BackendRuntimeSnapshot) -> Option<u32> {
    if snapshot.restart_count >= MAX_BACKEND_RESTARTS {
        return None;
    }
    snapshot.restart_count += 1;
    Some(snapshot.restart_count)
}

fn wait_for_restart_backoff(stop_requested: &AtomicBool, delay: Duration) -> bool {
    let deadline = Instant::now() + delay;
    while Instant::now() < deadline {
        if stop_requested.load(Ordering::Acquire) {
            return false;
        }
        thread::sleep(Duration::from_millis(25).min(delay));
    }
    true
}

fn restart_backoff(attempt: u32) -> Duration {
    let multiplier = 2_u64.saturating_pow(attempt.saturating_sub(1));
    let milliseconds = (BACKEND_RESTART_INITIAL_BACKOFF.as_millis() as u64)
        .saturating_mul(multiplier)
        .min(BACKEND_RESTART_MAX_BACKOFF.as_millis() as u64);
    Duration::from_millis(milliseconds)
}

fn spawn_and_wait_for_health<C, Spawn, Health, Cleanup>(
    mut spawn: Spawn,
    mut health: Health,
    mut cleanup: Cleanup,
) -> Result<C, String>
where
    Spawn: FnMut() -> Result<C, String>,
    Health: FnMut(&mut C) -> Result<(), String>,
    Cleanup: FnMut(C),
{
    let mut child = spawn()?;
    if let Err(error) = health(&mut child) {
        cleanup(child);
        return Err(error);
    }
    Ok(child)
}

fn wait_for_health(
    child: &mut Child,
    port: u16,
    api_token: &str,
    timeout: Duration,
) -> Result<(), String> {
    wait_for_health_cancelable(child, port, api_token, timeout, || false)
}

fn wait_for_health_cancelable<F>(
    child: &mut Child,
    port: u16,
    api_token: &str,
    timeout: Duration,
    should_cancel: F,
) -> Result<(), String>
where
    F: Fn() -> bool,
{
    poll_until_healthy(timeout, should_cancel, || {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("failed to inspect backend process: {error}"))?
        {
            return Err(format!("bundled backend exited before health: {status}"));
        }
        Ok(health_request(port, api_token))
    })
}

fn poll_until_healthy<Cancel, Probe>(
    timeout: Duration,
    should_cancel: Cancel,
    mut probe: Probe,
) -> Result<(), String>
where
    Cancel: Fn() -> bool,
    Probe: FnMut() -> Result<bool, String>,
{
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if should_cancel() {
            return Err("bundled backend health wait cancelled".to_string());
        }
        if probe()? {
            return Ok(());
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        thread::sleep(Duration::from_millis(100).min(remaining));
    }
    Err(format!(
        "bundled backend health timed out after {}s",
        timeout.as_secs()
    ))
}

fn health_request(port: u16, api_token: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = Vec::with_capacity(4096);
    let Ok(_) = stream.read_to_end(&mut response) else {
        return false;
    };
    let status_ok = response.starts_with(b"HTTP/1.1 200") || response.starts_with(b"HTTP/1.0 200");
    if !status_ok {
        return false;
    }
    let expected = health_proof(api_token);
    response
        .windows(expected.len())
        .any(|window| window == expected.as_bytes())
}

fn generate_local_api_token() -> Result<String, String> {
    let allow_override = env::var(ALLOW_TEST_TOKEN_OVERRIDE_ENV)
        .map(|value| matches!(value.trim(), "1" | "true" | "yes" | "on"))
        .unwrap_or(false);
    if allow_override {
        if let Ok(value) = env::var(TEST_TOKEN_OVERRIDE_ENV) {
            let token = value.trim();
            if token.len() == 64 && token.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Ok(token.to_ascii_lowercase());
            }
            return Err(
                "test local API token override must be 64 hexadecimal characters".to_string(),
            );
        }
    }
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("failed to generate local API token: {error}"))?;
    Ok(hex::encode(bytes))
}

fn health_proof(api_token: &str) -> String {
    hmac_sha256_hex(api_token.as_bytes(), HEALTH_PROOF_CONTEXT)
}

fn hmac_sha256_hex(key: &[u8], message: &[u8]) -> String {
    const BLOCK_SIZE: usize = 64;
    let mut normalized = [0_u8; BLOCK_SIZE];
    if key.len() > BLOCK_SIZE {
        normalized[..32].copy_from_slice(&Sha256::digest(key));
    } else {
        normalized[..key.len()].copy_from_slice(key);
    }
    let mut inner_pad = [0x36_u8; BLOCK_SIZE];
    let mut outer_pad = [0x5c_u8; BLOCK_SIZE];
    for index in 0..BLOCK_SIZE {
        inner_pad[index] ^= normalized[index];
        outer_pad[index] ^= normalized[index];
    }
    let mut inner = Sha256::new();
    inner.update(inner_pad);
    inner.update(message);
    let inner_digest = inner.finalize();
    let mut outer = Sha256::new();
    outer.update(outer_pad);
    outer.update(inner_digest);
    hex::encode(outer.finalize())
}

#[cfg(unix)]
fn process_group_exists(process_group_id: u32) -> bool {
    let result = unsafe { libc::kill(-(process_group_id as i32), 0) };
    if result == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

fn stop_child_process_group(child: &mut Child, timeout: Duration) {
    #[cfg(unix)]
    {
        let process_group_id = child.id();
        unsafe {
            libc::kill(-(process_group_id as i32), libc::SIGTERM);
        }
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            let _ = child.try_wait();
            if !process_group_exists(process_group_id) {
                let _ = child.wait();
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        unsafe {
            libc::kill(-(process_group_id as i32), libc::SIGKILL);
        }
        let kill_deadline = Instant::now() + Duration::from_secs(1);
        while Instant::now() < kill_deadline && process_group_exists(process_group_id) {
            thread::sleep(Duration::from_millis(25));
        }
        let _ = child.kill();
        let _ = child.wait();
    }
    #[cfg(not(unix))]
    {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn stopping_an_exited_group_leader_still_reaps_its_descendant() {
        use std::os::unix::process::CommandExt;

        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-process-group-test-{}-{}",
            std::process::id(),
            Instant::now().elapsed().as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let pid_path = root.join("descendant.pid");
        let pid_ready_path = root.join("descendant.pid.ready");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg(format!(
                "sleep 30 & printf '%s\\n' $! > '{}'; mv '{}' '{}'; exit 0",
                pid_ready_path.display(),
                pid_ready_path.display(),
                pid_path.display()
            ))
            .process_group(0);
        let mut leader = command.spawn().unwrap();
        let _ = leader.wait();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !pid_path.is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let descendant_pid: i32 = fs::read_to_string(&pid_path)
            .unwrap()
            .trim()
            .parse()
            .unwrap();

        stop_child_process_group(&mut leader, Duration::from_millis(300));

        let exit_deadline = Instant::now() + Duration::from_secs(2);
        while unsafe { libc::kill(descendant_pid, 0) } == 0 && Instant::now() < exit_deadline {
            thread::sleep(Duration::from_millis(25));
        }
        let descendant_alive = unsafe { libc::kill(descendant_pid, 0) } == 0;
        if descendant_alive {
            unsafe {
                libc::kill(descendant_pid, libc::SIGKILL);
            }
        }
        let _ = fs::remove_dir_all(root);
        assert!(!descendant_alive, "process-group descendant was not reaped");
    }

    #[cfg(unix)]
    #[test]
    fn dropping_supervisor_after_later_startup_failure_reaps_process_group() {
        use std::os::unix::process::CommandExt;

        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-supervisor-drop-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let pid_path = root.join("descendant.pid");
        let pid_ready_path = root.join("descendant.pid.ready");
        let mut command = Command::new("/bin/sh");
        command
            .arg("-c")
            .arg(format!(
                "sleep 30 & printf '%s\\n' $! > '{}'; mv '{}' '{}'; wait",
                pid_ready_path.display(),
                pid_ready_path.display(),
                pid_path.display()
            ))
            .process_group(0);
        let child = command.spawn().unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !pid_path.is_file() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(10));
        }
        let descendant_pid: i32 = fs::read_to_string(&pid_path)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let supervisor = BackendSupervisor::default();
        supervisor.inner.lock().unwrap().child = Some(child);

        drop(supervisor);

        let exit_deadline = Instant::now() + Duration::from_secs(2);
        while unsafe { libc::kill(descendant_pid, 0) } == 0 && Instant::now() < exit_deadline {
            thread::sleep(Duration::from_millis(25));
        }
        let descendant_alive = unsafe { libc::kill(descendant_pid, 0) } == 0;
        if descendant_alive {
            unsafe {
                libc::kill(descendant_pid, libc::SIGKILL);
            }
        }
        let _ = fs::remove_dir_all(root);
        assert!(
            !descendant_alive,
            "supervisor drop left an orphan descendant"
        );
    }

    #[test]
    fn runtime_bundle_override_does_not_depend_on_compile_time_repo_path() {
        let resource = Path::new("/Applications/Meeting Copilot.app/Contents/Resources");
        let override_path = Path::new("/tmp/runtime with spaces");
        assert_eq!(
            resolve_runtime_bundle(resource, Some(override_path)),
            override_path
        );
        assert_eq!(
            resolve_runtime_bundle(resource, None),
            resource.join("MeetingCopilotRuntime.bundle")
        );
    }

    #[test]
    fn runtime_inventory_fails_closed_when_required_files_are_missing() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-runtime-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        let error = validate_runtime_bundle(&root).unwrap_err();
        assert!(error.contains("runtime-bundle-manifest.json"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn runtime_inventory_uses_manifest_paths_without_python_version_hardcoding() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-runtime-manifest-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("runtime/backend/bin")).unwrap();
        fs::write(root.join("runtime/backend/bin/python9.8"), b"fixture").unwrap();
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            br#"{
              "schema_version": "meeting_copilot.runtime_bundle.v1",
              "required_files": [
                "runtime-bundle-manifest.json",
                "runtime/backend/bin/python9.8"
              ]
            }"#,
        )
        .unwrap();

        assert!(validate_runtime_bundle(&root).is_ok());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn runtime_inventory_rejects_manifest_path_escape() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-runtime-escape-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            br#"{
              "schema_version": "meeting_copilot.runtime_bundle.v1",
              "required_files": ["../outside"]
            }"#,
        )
        .unwrap();

        assert!(validate_runtime_bundle(&root)
            .unwrap_err()
            .contains("unsafe path"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn file_asr_inventory_reports_optional_models_as_concrete_missing_components() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-file-asr-inventory-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("runtime/funasr/bin")).unwrap();
        fs::create_dir_all(root.join("app/asr")).unwrap();
        fs::write(root.join("runtime/funasr/bin/python3.11"), b"fixture").unwrap();
        fs::write(root.join("app/asr/transcribe.py"), b"fixture").unwrap();
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            br#"{
              "schema_version": "meeting_copilot.runtime_bundle.v1",
              "workers": {"file_asr": "app/asr/transcribe.py"},
              "file_asr": {
                "runtime": {"executable": "runtime/funasr/bin/python3.11"},
                "converter": {
                  "path": "runtime/tools/ffmpeg",
                  "license_path": "runtime/tools/FFMPEG-LICENSE"
                },
                "package": {"install_status": "not_bundled", "redistribution_status": "not_approved"},
                "redistribution": {"status": "not_approved"},
                "models": {
                  "offline": {"root": "models/offline", "required_files": ["model.pt", "config.yaml"]},
                  "vad": {"root": "models/vad", "required_files": ["model.pt", "config.yaml"]},
                  "punc": {"root": "models/punc", "required_files": ["model.pt", "config.yaml"]}
                }
              },
              "required_files": [
                "runtime-bundle-manifest.json",
                "runtime/funasr/bin/python3.11",
                "app/asr/transcribe.py"
              ]
            }"#,
        )
        .unwrap();

        let capability = inspect_file_asr_capability(&root).unwrap();

        assert_eq!(capability.status, "file_asr_models_not_installed");
        assert!(!capability.available);
        assert_eq!(
            capability.user_message.as_deref(),
            Some("文件导入组件未安装")
        );
        assert_eq!(capability.network_offline, None);
        assert_eq!(capability.formats.get("wav"), Some(&false));
        assert_eq!(
            capability.missing_components,
            vec!["offline_model", "vad_model", "punc_model"]
        );
        assert!(validate_runtime_bundle(&root).is_ok());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn file_asr_inventory_rejects_user_cache_model_path() {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-file-asr-cache-test-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            br#"{
              "schema_version": "meeting_copilot.runtime_bundle.v1",
              "file_asr": {"models": {
                "offline": {"root": ".cache/modelscope/offline", "required_files": ["model.pt"]}
              }},
              "required_files": ["runtime-bundle-manifest.json"]
            }"#,
        )
        .unwrap();

        assert!(validate_runtime_bundle(&root)
            .unwrap_err()
            .contains("forbidden development/cache path"));
        let _ = fs::remove_dir_all(&root);
    }

    fn sealed_file_asr_fixture_root(test_name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "meeting-copilot-sealed-file-asr-{test_name}-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("runtime/funasr-venv")).unwrap();
        fs::create_dir_all(root.join("bin")).unwrap();
        fs::create_dir_all(root.join("app/asr")).unwrap();
        fs::create_dir_all(root.join("runtime/tools")).unwrap();
        for name in ["offline", "vad", "punc"] {
            fs::create_dir_all(root.join(format!("models/{name}"))).unwrap();
        }
        fs::write(root.join("runtime/funasr-venv/runtime.py"), b"runtime").unwrap();
        fs::write(
            root.join("bin/meeting-copilot-file-asr-python"),
            b"launcher",
        )
        .unwrap();
        fs::write(root.join("app/asr/transcribe.py"), b"worker").unwrap();
        fs::write(root.join("runtime/tools/ffmpeg"), b"converter").unwrap();
        fs::write(root.join("runtime/tools/FFMPEG-LICENSE"), b"license").unwrap();
        for name in ["offline", "vad", "punc"] {
            fs::write(
                root.join(format!("models/{name}/model.pt")),
                format!("{name}-model"),
            )
            .unwrap();
            fs::write(
                root.join(format!("models/{name}/config.yaml")),
                format!("{name}-config"),
            )
            .unwrap();
        }
        root
    }

    fn sealed_file_asr_fixture_manifest() -> serde_json::Value {
        serde_json::json!({
            "schema_version": RUNTIME_MANIFEST_SCHEMA,
            "runtimes": {
                "funasr": {
                    "root": "runtime/funasr-venv",
                    "size_bytes": 7,
                    "sha256": "c862b8bdd04381b8152ac22b630c1893d8c86d95e2c6c74afb73bcfc3eed7cd6"
                }
            },
            "workers": {
                "file_asr": "app/asr/transcribe.py"
            },
            "worker_inventory": {
                "file_asr": {
                    "path": "app/asr/transcribe.py",
                    "size_bytes": 6,
                    "sha256": "87eba76e7f3164534045ba922e7770fb58bbd14ad732bbf5ba6f11cc56989e6e"
                }
            },
            "file_asr": {
                "runtime": {
                    "root": "runtime/funasr-venv",
                    "executable": "bin/meeting-copilot-file-asr-python",
                    "size_bytes": 7,
                    "sha256": "c862b8bdd04381b8152ac22b630c1893d8c86d95e2c6c74afb73bcfc3eed7cd6"
                },
                "worker": {
                    "path": "app/asr/transcribe.py",
                    "size_bytes": 6,
                    "sha256": "87eba76e7f3164534045ba922e7770fb58bbd14ad732bbf5ba6f11cc56989e6e"
                },
                "converter": {
                    "path": "runtime/tools/ffmpeg",
                    "license_path": "runtime/tools/FFMPEG-LICENSE",
                    "size_bytes": 9,
                    "sha256": "428457d1ce5a0721f7bb21c85b2e36380566f51bba7b3996b2a63356dfa732f0"
                },
                "package": {
                    "install_status": "bundled",
                    "redistribution_status": "approved"
                },
                "redistribution": {
                    "status": "approved_by_upstream_license"
                },
                "models": {
                    "offline": {
                        "root": "models/offline",
                        "required_files": ["model.pt", "config.yaml"],
                        "size_bytes": 27,
                        "sha256": "37320c7d56802cc71ffd53df03d90d46ffda62eed8ab5d294c9c302b36f576ab"
                    },
                    "vad": {
                        "root": "models/vad",
                        "required_files": ["model.pt", "config.yaml"],
                        "size_bytes": 19,
                        "sha256": "dd480b4687fc1eed25bd071a80af2a9fda5ae4c8999e1accf722c38aa730a089"
                    },
                    "punc": {
                        "root": "models/punc",
                        "required_files": ["model.pt", "config.yaml"],
                        "size_bytes": 21,
                        "sha256": "88833f68e34448732f5623e696fc036f778350f5e9761acad6a3acde4e4d877f"
                    }
                }
            },
            "component_inventory": {
                "schema_version": "meeting_copilot.runtime_component_inventory.v1",
                "status": "sealed",
                "components": {
                    "shared_asr.runtime": {
                        "path": "runtime/funasr-venv",
                        "kind": "directory",
                        "size_bytes": 7,
                        "file_count": 1,
                        "symlink_count": 0,
                        "sha256": "c862b8bdd04381b8152ac22b630c1893d8c86d95e2c6c74afb73bcfc3eed7cd6"
                    },
                    "file_asr.python_launcher": {
                        "path": "bin/meeting-copilot-file-asr-python",
                        "kind": "file",
                        "size_bytes": 8,
                        "sha256": "ec9a6e9fe278eb1a471fbab6f40367d8548078b651d9c71581c57c2a6ca379e0"
                    },
                    "file_asr.worker": {
                        "path": "app/asr/transcribe.py",
                        "kind": "file",
                        "size_bytes": 6,
                        "sha256": "87eba76e7f3164534045ba922e7770fb58bbd14ad732bbf5ba6f11cc56989e6e"
                    },
                    "file_asr.converter": {
                        "path": "runtime/tools/ffmpeg",
                        "kind": "file",
                        "size_bytes": 9,
                        "sha256": "428457d1ce5a0721f7bb21c85b2e36380566f51bba7b3996b2a63356dfa732f0"
                    },
                    "file_asr.model.offline": {
                        "path": "models/offline",
                        "kind": "directory",
                        "size_bytes": 27,
                        "file_count": 2,
                        "symlink_count": 0,
                        "sha256": "37320c7d56802cc71ffd53df03d90d46ffda62eed8ab5d294c9c302b36f576ab"
                    },
                    "file_asr.model.vad": {
                        "path": "models/vad",
                        "kind": "directory",
                        "size_bytes": 19,
                        "file_count": 2,
                        "symlink_count": 0,
                        "sha256": "dd480b4687fc1eed25bd071a80af2a9fda5ae4c8999e1accf722c38aa730a089"
                    },
                    "file_asr.model.punc": {
                        "path": "models/punc",
                        "kind": "directory",
                        "size_bytes": 21,
                        "file_count": 2,
                        "symlink_count": 0,
                        "sha256": "88833f68e34448732f5623e696fc036f778350f5e9761acad6a3acde4e4d877f"
                    }
                }
            },
            "required_files": [
                "runtime-bundle-manifest.json",
                "bin/meeting-copilot-file-asr-python",
                "app/asr/transcribe.py",
                "runtime/tools/ffmpeg",
                "runtime/tools/FFMPEG-LICENSE"
            ]
        })
    }

    fn write_sealed_file_asr_fixture(test_name: &str) -> PathBuf {
        let root = sealed_file_asr_fixture_root(test_name);
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            serde_json::to_vec(&sealed_file_asr_fixture_manifest()).unwrap(),
        )
        .unwrap();
        root
    }

    #[test]
    fn file_asr_sealed_inventory_is_ready_before_file_tampering() {
        let root = write_sealed_file_asr_fixture("ready");

        let capability = inspect_file_asr_capability(&root).unwrap();

        assert_eq!(capability.status, "ready");
        assert!(capability.available);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_tampered_worker_hash() {
        let root = write_sealed_file_asr_fixture("worker-tamper");
        fs::write(root.join("app/asr/transcribe.py"), b"tamper").unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.worker"));
        assert!(error.contains("hash mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_tampered_launcher_hash() {
        let root = write_sealed_file_asr_fixture("launcher-tamper");
        fs::write(
            root.join("bin/meeting-copilot-file-asr-python"),
            b"launchee",
        )
        .unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.python_launcher"));
        assert!(error.contains("hash mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_tampered_model_size() {
        let root = write_sealed_file_asr_fixture("model-tamper");
        fs::OpenOptions::new()
            .append(true)
            .open(root.join("models/offline/model.pt"))
            .unwrap()
            .write_all(b"x")
            .unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.model.offline"));
        assert!(error.contains("size mismatch") || error.contains("hash mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_does_not_rehash_large_runtime_contents() {
        let root = write_sealed_file_asr_fixture("runtime-tamper");
        fs::write(root.join("runtime/funasr-venv/runtime.py"), b"runtimf").unwrap();

        let capability = inspect_file_asr_capability(&root).unwrap();

        assert_eq!(capability.status, "ready");
        assert!(capability.available);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_large_directory_file_count_mismatch() {
        let root = write_sealed_file_asr_fixture("model-count-tamper");
        fs::write(root.join("models/offline/extra.bin"), b"").unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.model.offline"));
        assert!(error.contains("file count mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_tampered_converter_hash() {
        let root = write_sealed_file_asr_fixture("converter-tamper");
        fs::write(root.join("runtime/tools/ffmpeg"), b"convertes").unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.converter"));
        assert!(error.contains("hash mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_component_path_mismatch() {
        let root = sealed_file_asr_fixture_root("path-mismatch");
        let mut manifest = sealed_file_asr_fixture_manifest();
        *manifest
            .pointer_mut("/component_inventory/components/file_asr.worker/path")
            .unwrap() = serde_json::json!("runtime/tools/ffmpeg");
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.worker"));
        assert!(error.contains("path mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_component_kind_mismatch() {
        let root = sealed_file_asr_fixture_root("kind-mismatch");
        let mut manifest = sealed_file_asr_fixture_manifest();
        *manifest
            .pointer_mut("/component_inventory/components/file_asr.model.offline/kind")
            .unwrap() = serde_json::json!("file");
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.model.offline"));
        assert!(error.contains("kind mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_rejects_business_manifest_mirror_mismatch() {
        let root = sealed_file_asr_fixture_root("mirror-mismatch");
        let mut manifest = sealed_file_asr_fixture_manifest();
        *manifest
            .pointer_mut("/file_asr/models/offline/size_bytes")
            .unwrap() = serde_json::json!(28);
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.model.offline"));
        assert!(error.contains("business manifest size mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_sealed_inventory_hashes_small_required_file_when_manifest_provides_hash() {
        let root = sealed_file_asr_fixture_root("required-hash");
        let mut manifest = sealed_file_asr_fixture_manifest();
        manifest
            .pointer_mut("/file_asr/models/offline")
            .unwrap()
            .as_object_mut()
            .unwrap()
            .insert(
                "required_file_hashes".to_string(),
                serde_json::json!({
                    "config.yaml": "8fe6bacf217a7fe008f33b592115c57e5f8d4dc6134adbd299bcf5d985ed28d7"
                }),
            );
        fs::write(
            root.join("runtime-bundle-manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        fs::write(root.join("models/offline/config.yaml"), b"offline-confiq").unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("file_asr.model.offline"));
        assert!(error.contains("required file hash mismatch"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn file_asr_directory_shape_matches_sealed_metadata_without_content_hashing() {
        let root = sealed_file_asr_fixture_root("directory-shape");

        let shape = directory_component_shape(
            &root.join("models/offline"),
            &fs::canonicalize(&root).unwrap(),
        )
        .unwrap();

        assert_eq!(shape.size_bytes, 27);
        assert_eq!(shape.file_count, 2);
        assert_eq!(shape.symlink_count, 0);
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn file_asr_directory_shape_rejects_external_symlink() {
        use std::os::unix::fs::symlink;

        let root = write_sealed_file_asr_fixture("external-symlink");
        let outside = root.parent().unwrap().join(format!(
            "meeting-copilot-outside-model-file-{}",
            std::process::id()
        ));
        fs::write(&outside, b"offline-config").unwrap();
        fs::remove_file(root.join("models/offline/config.yaml")).unwrap();
        symlink(&outside, root.join("models/offline/config.yaml")).unwrap();

        let error = inspect_file_asr_capability(&root).unwrap_err();

        assert!(error.contains("external symlink"));
        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_file(outside);
    }

    #[test]
    #[ignore = "requires MEETING_COPILOT_NEXT022_RUNTIME_BUNDLE pointing at the real packaged runtime"]
    fn file_asr_real_packaged_shape_preflight_completes_under_ten_seconds() {
        let runtime_bundle = PathBuf::from(
            env::var_os("MEETING_COPILOT_NEXT022_RUNTIME_BUNDLE")
                .expect("MEETING_COPILOT_NEXT022_RUNTIME_BUNDLE is required"),
        );
        let started = Instant::now();
        let manifest_text =
            fs::read_to_string(runtime_bundle.join("runtime-bundle-manifest.json")).unwrap();
        let mut manifest: serde_json::Value = serde_json::from_str(&manifest_text).unwrap();

        if manifest
            .pointer("/component_inventory/components/file_asr.python_launcher")
            .is_none()
        {
            let legacy_started = Instant::now();
            let legacy_error =
                validate_sealed_file_asr_inventory(&runtime_bundle, &manifest).unwrap_err();
            assert!(legacy_error.contains("file_asr.python_launcher"));
            assert!(
                legacy_started.elapsed() < Duration::from_secs(1),
                "legacy packaged inventory did not fail before large-directory traversal"
            );
            let launcher_relative = manifest
                .pointer("/file_asr/runtime/executable")
                .and_then(serde_json::Value::as_str)
                .unwrap()
                .to_string();
            let launcher = file_component_inventory(&runtime_bundle.join(&launcher_relative))
                .expect("real packaged file ASR launcher must be a regular file");
            manifest
                .pointer_mut("/component_inventory/components")
                .and_then(serde_json::Value::as_object_mut)
                .unwrap()
                .insert(
                    "file_asr.python_launcher".to_string(),
                    serde_json::json!({
                        "path": launcher_relative,
                        "kind": "file",
                        "size_bytes": launcher.size_bytes,
                        "sha256": launcher.sha256
                    }),
                );
        }

        validate_sealed_file_asr_inventory(&runtime_bundle, &manifest).unwrap();
        let elapsed = started.elapsed();
        assert!(
            elapsed < Duration::from_secs(10),
            "real packaged file ASR preflight took {elapsed:?}"
        );
    }

    #[test]
    fn loopback_port_reservation_returns_nonzero_local_port() {
        assert!(reserve_loopback_port().unwrap() > 0);
    }

    #[test]
    fn generated_local_api_token_is_high_entropy_hex() {
        let token = generate_local_api_token().unwrap();
        assert_eq!(token.len(), 64);
        assert!(token.bytes().all(|byte| byte.is_ascii_hexdigit()));
    }

    #[derive(Debug)]
    struct InjectedChild {
        port: u16,
        token: String,
    }

    #[test]
    fn injected_spawn_restart_reuses_port_and_local_token_without_a_real_child() {
        let port = 43123;
        let token = "c".repeat(64);
        let mut spawns = Vec::new();
        let child = spawn_and_wait_for_health(
            || {
                spawns.push((port, token.clone()));
                Ok(InjectedChild {
                    port,
                    token: token.clone(),
                })
            },
            |child| {
                assert_eq!(child.port, port);
                assert_eq!(child.token, token);
                Ok(())
            },
            |_child| {},
        )
        .unwrap();

        assert_eq!(spawns, vec![(port, token.clone())]);
        assert_eq!(child.port, port);
        assert_eq!(child.token, token);
        let snapshot = BackendRuntimeSnapshot {
            status: "restarting".to_string(),
            port: Some(port),
            restart_count: 1,
            ..BackendRuntimeSnapshot::default()
        };
        let serialized = serde_json::to_string(&snapshot).unwrap();
        assert!(serialized.contains("restart_count"));
        assert!(!serialized.contains(&token));
    }

    #[test]
    fn injected_restart_budget_is_bounded_and_backoff_is_capped() {
        let mut spawn_count = 0_u32;
        let mut cleanup_count = 0_u32;
        for _attempt in 1..=MAX_BACKEND_RESTARTS {
            let result = spawn_and_wait_for_health(
                || {
                    spawn_count += 1;
                    Ok(InjectedChild {
                        port: 43124,
                        token: "d".repeat(64),
                    })
                },
                |_child| Err("injected health failure".to_string()),
                |_child| cleanup_count += 1,
            );
            assert!(result.is_err());
        }

        assert_eq!(spawn_count, MAX_BACKEND_RESTARTS);
        assert_eq!(cleanup_count, MAX_BACKEND_RESTARTS);
        assert_eq!(restart_backoff(1), BACKEND_RESTART_INITIAL_BACKOFF);
        assert_eq!(
            restart_backoff(MAX_BACKEND_RESTARTS + 1),
            Duration::from_millis(2000)
        );
    }

    #[test]
    fn restart_budget_is_cumulative_across_multiple_child_crashes() {
        let mut snapshot = BackendRuntimeSnapshot::default();
        for expected in 1..=MAX_BACKEND_RESTARTS {
            assert_eq!(claim_restart_attempt(&mut snapshot), Some(expected));
        }
        assert_eq!(snapshot.restart_count, MAX_BACKEND_RESTARTS);
        assert_eq!(claim_restart_attempt(&mut snapshot), None);
        assert_eq!(snapshot.restart_count, MAX_BACKEND_RESTARTS);
    }

    #[test]
    fn cancelled_health_poll_returns_without_waiting_for_the_timeout() {
        let stop_requested = AtomicBool::new(true);
        let mut probe_count = 0_u32;
        let started = Instant::now();
        let result = poll_until_healthy(
            Duration::from_secs(60),
            || stop_requested.load(Ordering::Acquire),
            || {
                probe_count += 1;
                Ok(false)
            },
        );

        assert_eq!(result.unwrap_err(), "bundled backend health wait cancelled");
        assert_eq!(probe_count, 0);
        assert!(started.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn stop_gate_prevents_an_injected_restart_from_starting() {
        let stop_requested = AtomicBool::new(true);
        let mut spawn_count = 0_u32;
        if wait_for_restart_backoff(&stop_requested, BACKEND_RESTART_INITIAL_BACKOFF) {
            spawn_count += 1;
        }
        assert_eq!(spawn_count, 0);
    }

    #[test]
    fn health_proof_matches_backend_contract() {
        assert_eq!(
            health_proof(&"a".repeat(64)),
            "cbf9aaeaeb5cad9d6c602451cd8d10734b2a52461c8cfe72acd97e088fdf9783"
        );
    }

    #[test]
    fn workbench_url_uses_bootstrap_without_exposing_token_in_snapshot() {
        let supervisor = BackendSupervisor::default();
        {
            let mut state = supervisor.inner.lock().unwrap();
            state.snapshot.base_url = Some("http://127.0.0.1:54321".to_string());
            state.api_token = Some("a".repeat(64));
        }

        assert_eq!(
            supervisor.workbench_url().unwrap(),
            format!(
                "http://127.0.0.1:54321/desktop/bootstrap?token={}",
                "a".repeat(64)
            )
        );
        assert!(!serde_json::to_string(&supervisor.snapshot())
            .unwrap()
            .contains(&"a".repeat(64)));
    }

    #[test]
    fn native_microphone_connection_uses_authenticated_ws_without_exposing_token() {
        let supervisor = BackendSupervisor::default();
        let token = "a".repeat(64);
        {
            let mut state = supervisor.inner.lock().unwrap();
            state.snapshot.base_url = Some("http://127.0.0.1:54321/".to_string());
            state.api_token = Some(token.clone());
        }

        let connection = supervisor
            .native_microphone_connection("meeting_native_01")
            .unwrap();
        assert_eq!(
            connection.url,
            "ws://127.0.0.1:54321/live/asr/stream/ws/meeting_native_01?audio_source=tauri_native_mic"
        );
        assert_eq!(
            connection.cookie,
            format!(
                "meeting_copilot_session={}",
                hmac_sha256_hex(token.as_bytes(), SESSION_COOKIE_CONTEXT)
            )
        );
        assert!(!connection.cookie.contains(&token));
        assert!(supervisor
            .native_microphone_connection("../private")
            .is_err());
    }

    #[test]
    fn provider_config_is_sent_only_to_authenticated_packaged_backend() {
        let listener = TcpListener::bind(("127.0.0.1", 0)).unwrap();
        let port = listener.local_addr().unwrap().port();
        let receiver = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(2)))
                .unwrap();
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let count = stream.read(&mut chunk).unwrap();
                if count == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..count]);
                let Some(header_end) = request.windows(4).position(|value| value == b"\r\n\r\n")
                else {
                    continue;
                };
                let headers = String::from_utf8_lossy(&request[..header_end]);
                let content_length = headers
                    .lines()
                    .find_map(|line| {
                        line.strip_prefix("Content-Length: ")
                            .and_then(|value| value.parse::<usize>().ok())
                    })
                    .unwrap_or(0);
                if request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
                .unwrap();
            String::from_utf8(request).unwrap()
        });
        let supervisor = BackendSupervisor::default();
        {
            let mut state = supervisor.inner.lock().unwrap();
            state.snapshot.port = Some(port);
            state.snapshot.base_url = Some(format!("http://127.0.0.1:{port}"));
            state.api_token = Some("b".repeat(64));
        }
        supervisor
            .configure_provider(&RuntimeProviderConfig {
                base_url: "https://relay.example".to_string(),
                api_key: "sk-test-only-secret".to_string(),
                model: "test-model".to_string(),
                realtime_model: "test-realtime-model".to_string(),
                api_style: "chat_completions".to_string(),
                provider_label: "openai_compatible_gateway".to_string(),
            })
            .unwrap();
        let request = receiver.join().unwrap();
        assert!(request.starts_with("PUT /desktop/provider/config HTTP/1.1"));
        assert!(request.contains(&format!("X-Meeting-Copilot-Token: {}", "b".repeat(64))));
        assert!(request.contains("sk-test-only-secret"));
        assert!(request.contains("\"model\":\"test-model\""));
        assert!(request.contains("\"realtime_model\":\"test-realtime-model\""));
        assert!(!serde_json::to_string(&supervisor.snapshot())
            .unwrap()
            .contains("sk-test-only-secret"));
    }
}

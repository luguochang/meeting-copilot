use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::env;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(60);
const BACKEND_STOP_TIMEOUT: Duration = Duration::from_secs(5);
const RUNTIME_MANIFEST_SCHEMA: &str = "meeting_copilot.runtime_bundle.v1";
const LOCAL_API_TOKEN_ENV: &str = "MEETING_COPILOT_LOCAL_API_TOKEN";
const TEST_TOKEN_OVERRIDE_ENV: &str = "MEETING_COPILOT_LOCAL_API_TOKEN_OVERRIDE";
const ALLOW_TEST_TOKEN_OVERRIDE_ENV: &str = "MEETING_COPILOT_ALLOW_TEST_TOKEN_OVERRIDE";
const HEALTH_PROOF_CONTEXT: &[u8] = b"meeting-copilot-health-v1";
const SESSION_COOKIE_CONTEXT: &[u8] = b"meeting-copilot-session-v1";

#[derive(Debug, Clone, Deserialize)]
struct RuntimeBundleManifest {
    schema_version: String,
    required_files: Vec<String>,
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
            errors: Vec::new(),
        }
    }
}

#[derive(Default)]
struct BackendRuntimeState {
    child: Option<Child>,
    snapshot: BackendRuntimeSnapshot,
    api_token: Option<String>,
}

#[derive(Default)]
pub struct BackendSupervisor {
    inner: Mutex<BackendRuntimeState>,
}

impl BackendSupervisor {
    pub fn start_packaged(
        &self,
        runtime_bundle: &Path,
        data_dir: &Path,
        log_dir: &Path,
    ) -> Result<BackendRuntimeSnapshot, String> {
        validate_runtime_bundle(runtime_bundle)?;
        let api_token = generate_local_api_token()?;
        fs::create_dir_all(data_dir)
            .map_err(|error| format!("failed to create backend data directory: {error}"))?;
        fs::create_dir_all(log_dir)
            .map_err(|error| format!("failed to create backend log directory: {error}"))?;

        let port = reserve_loopback_port()?;
        let base_url = format!("http://127.0.0.1:{port}");
        let stdout = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_dir.join("backend.stdout.log"))
            .map_err(|error| format!("failed to open backend stdout log: {error}"))?;
        let stderr = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_dir.join("backend.stderr.log"))
            .map_err(|error| format!("failed to open backend stderr log: {error}"))?;

        let launcher = runtime_bundle.join("bin/meeting-copilot-backend");
        let mut command = Command::new("/bin/sh");
        command
            .arg(&launcher)
            .current_dir(runtime_bundle)
            .env("MEETING_COPILOT_PORT", port.to_string())
            .env("MEETING_COPILOT_DATA_DIR", data_dir)
            .env("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
            .env("MEETING_COPILOT_PARENT_PID", std::process::id().to_string())
            .env(LOCAL_API_TOKEN_ENV, &api_token)
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
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }

        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to spawn bundled backend: {error}"))?;
        let pid = child.id();
        if let Err(error) = wait_for_health(&mut child, port, &api_token, BACKEND_START_TIMEOUT) {
            stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
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
            errors: Vec::new(),
        };
        let mut state = self
            .inner
            .lock()
            .map_err(|_| "backend supervisor lock poisoned")?;
        if let Some(mut previous) = state.child.take() {
            stop_child_process_group(&mut previous, BACKEND_STOP_TIMEOUT);
        }
        state.child = Some(child);
        state.api_token = Some(api_token);
        state.snapshot = snapshot.clone();
        Ok(snapshot)
    }

    pub fn use_external(&self, base_url: String) -> BackendRuntimeSnapshot {
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
        let mut state = match self.inner.lock() {
            Ok(state) => state,
            Err(_) => {
                return BackendRuntimeSnapshot {
                    status: "failed".to_string(),
                    errors: vec!["backend supervisor lock poisoned".to_string()],
                    ..BackendRuntimeSnapshot::default()
                }
            }
        };
        if let Some(mut child) = state.child.take() {
            stop_child_process_group(&mut child, BACKEND_STOP_TIMEOUT);
        }
        state.api_token = None;
        state.snapshot.status = "stopped".to_string();
        state.snapshot.health_ready = false;
        state.snapshot.pid = None;
        state.snapshot.clone()
    }
}

pub fn resolve_runtime_bundle(resource_dir: &Path, override_path: Option<&Path>) -> PathBuf {
    override_path
        .map(Path::to_path_buf)
        .unwrap_or_else(|| resource_dir.join("MeetingCopilotRuntime.bundle"))
}

pub fn validate_runtime_bundle(runtime_bundle: &Path) -> Result<(), String> {
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
    if missing.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "bundled runtime is incomplete: {}",
            missing.join(", ")
        ))
    }
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

fn wait_for_health(
    child: &mut Child,
    port: u16,
    api_token: &str,
    timeout: Duration,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("failed to inspect backend process: {error}"))?
        {
            return Err(format!("bundled backend exited before health: {status}"));
        }
        if health_request(port, api_token) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
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

fn stop_child_process_group(child: &mut Child, timeout: Duration) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
    }
    #[cfg(not(unix))]
    {
        let _ = child.kill();
    }
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn loopback_port_reservation_returns_nonzero_local_port() {
        assert!(reserve_loopback_port().unwrap() > 0);
    }

    #[test]
    fn generated_local_api_token_is_high_entropy_hex() {
        let token = generate_local_api_token().unwrap();
        assert_eq!(token.len(), 64);
        assert!(token.bytes().all(|byte| byte.is_ascii_hexdigit()));
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
                provider_label: "openai_compatible_gateway".to_string(),
            })
            .unwrap();
        let request = receiver.join().unwrap();
        assert!(request.starts_with("PUT /desktop/provider/config HTTP/1.1"));
        assert!(request.contains(&format!("X-Meeting-Copilot-Token: {}", "b".repeat(64))));
        assert!(request.contains("sk-test-only-secret"));
        assert!(!serde_json::to_string(&supervisor.snapshot())
            .unwrap()
            .contains("sk-test-only-secret"));
    }
}

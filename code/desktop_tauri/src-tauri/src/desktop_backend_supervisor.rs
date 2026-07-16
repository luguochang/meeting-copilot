use serde::Serialize;
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
    pub errors: Vec<String>,
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
            errors: Vec::new(),
        }
    }
}

#[derive(Default)]
struct BackendRuntimeState {
    child: Option<Child>,
    snapshot: BackendRuntimeSnapshot,
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
        if let Err(error) = wait_for_health(&mut child, port, BACKEND_START_TIMEOUT) {
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
            errors: Vec::new(),
        };
        if let Ok(mut state) = self.inner.lock() {
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
    let required = [
        "bin/meeting-copilot-backend",
        "runtime/backend-python/bin/python3.12",
        "runtime/funasr-python/bin/python3.11",
        "app/code/web_mvp/backend/meeting_copilot_web_mvp/app.py",
        "app/code/web_mvp/frontend_v2/dist/index.html",
        "app/code/core/meeting_copilot_core/__init__.py",
        "app/code/asr_runtime/scripts/funasr_stream_worker.py",
        "models/funasr-online/model.pt",
        "models/funasr-online/config.yaml",
    ];
    let missing: Vec<&str> = required
        .iter()
        .copied()
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

fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("failed to reserve loopback port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("failed to read loopback port: {error}"))
}

fn wait_for_health(child: &mut Child, port: u16, timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| format!("failed to inspect backend process: {error}"))?
        {
            return Err(format!("bundled backend exited before health: {status}"));
        }
        if health_request(port) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!(
        "bundled backend health timed out after {}s",
        timeout.as_secs()
    ))
}

fn health_request(port: u16) -> bool {
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
    let mut response = [0_u8; 256];
    let Ok(size) = stream.read(&mut response) else {
        return false;
    };
    response[..size].starts_with(b"HTTP/1.1 200") || response[..size].starts_with(b"HTTP/1.0 200")
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
        assert!(error.contains("bin/meeting-copilot-backend"));
        assert!(error.contains("models/funasr-online/model.pt"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn loopback_port_reservation_returns_nonzero_local_port() {
        assert!(reserve_loopback_port().unwrap() > 0);
    }
}

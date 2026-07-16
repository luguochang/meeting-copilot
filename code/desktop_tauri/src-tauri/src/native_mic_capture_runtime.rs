use crate::desktop_backend_supervisor::BackendSupervisor;
use serde::Serialize;
use serde_json::Value;
use std::fs::{self, File, OpenOptions};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

const READY_TIMEOUT: Duration = Duration::from_secs(30);
const STOP_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Debug, Clone, Serialize)]
pub struct NativeMicCaptureResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub transport_status: &'static str,
    pub side_effect_status: &'static str,
    pub session_id: Option<String>,
    pub pid: Option<u32>,
    pub status: &'static str,
    pub helper_present: bool,
    pub ready_file: Option<String>,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub errors: Vec<String>,
}

struct NativeMicState {
    child: Option<Child>,
    session_id: Option<String>,
    ready_file: Option<PathBuf>,
    status: &'static str,
}

pub struct NativeMicCaptureSupervisor {
    helper_path: PathBuf,
    runtime_root: PathBuf,
    log_dir: PathBuf,
    state: Mutex<NativeMicState>,
}

impl NativeMicCaptureSupervisor {
    pub fn new(helper_path: PathBuf, runtime_root: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            helper_path,
            runtime_root,
            log_dir,
            state: Mutex::new(NativeMicState {
                child: None,
                session_id: None,
                ready_file: None,
                status: "not_started",
            }),
        }
    }

    pub fn start(
        &self,
        session_id: Option<String>,
        backend: &BackendSupervisor,
    ) -> NativeMicCaptureResponse {
        let session_id = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => return self.error_response("mic_adapter.start", None, error),
        };
        if !self.helper_path.is_file() {
            return self.error_response(
                "mic_adapter.start",
                Some(session_id),
                format!(
                    "native microphone helper is missing: {}",
                    self.helper_path.display()
                ),
            );
        }
        let connection = match backend.native_microphone_connection(&session_id) {
            Ok(value) => value,
            Err(error) => return self.error_response("mic_adapter.start", Some(session_id), error),
        };
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    "native microphone state lock poisoned".to_string(),
                )
            }
        };
        let active_pid = if let Some(child) = state.child.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                Some(child.id())
            } else {
                None
            }
        } else {
            None
        };
        if let Some(active_pid) = active_pid {
            return self.response(
                "mic_adapter.start",
                "conflict",
                "already_recording",
                state.session_id.clone(),
                Some(active_pid),
                state.ready_file.clone(),
                false,
                vec!["only one native microphone session is supported".to_string()],
            );
        }
        if state.child.is_some() {
            state.child = None;
        }
        let ready_file = self.runtime_root.join(&session_id).join("ready.json");
        if let Some(parent) = ready_file.parent() {
            if let Err(error) = fs::create_dir_all(parent) {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to create native microphone runtime: {error}"),
                );
            }
        }
        let _ = fs::remove_file(&ready_file);
        if let Err(error) = fs::create_dir_all(&self.log_dir) {
            return self.error_response(
                "mic_adapter.start",
                Some(session_id),
                format!("failed to create native microphone log directory: {error}"),
            );
        }
        let stdout = match OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.log_dir.join("native-mic.stdout.log"))
        {
            Ok(file) => file,
            Err(error) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to open native microphone stdout: {error}"),
                )
            }
        };
        let stderr = match OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.log_dir.join("native-mic.stderr.log"))
        {
            Ok(file) => file,
            Err(error) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to open native microphone stderr: {error}"),
                )
            }
        };
        let mut command = Command::new(&self.helper_path);
        command
            .arg("--ws-url")
            .arg(connection.url)
            .arg("--session-id")
            .arg(&session_id)
            .arg("--ready-file")
            .arg(&ready_file)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env(
                "HOME",
                self.runtime_root.parent().unwrap_or(&self.runtime_root),
            )
            .env("MEETING_COPILOT_SESSION_COOKIE", connection.cookie)
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        let mut child = match command.spawn() {
            Ok(value) => value,
            Err(error) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to spawn native microphone helper: {error}"),
                )
            }
        };
        let pid = child.id();
        let deadline = Instant::now() + READY_TIMEOUT;
        loop {
            if ready_file.is_file() && ready_file_is_ready(&ready_file, &session_id) {
                state.child = Some(child);
                state.session_id = Some(session_id.clone());
                state.ready_file = Some(ready_file.clone());
                state.status = "recording";
                return self.response(
                    "mic_adapter.start",
                    "ok",
                    "recording",
                    Some(session_id),
                    Some(pid),
                    Some(ready_file),
                    true,
                    Vec::new(),
                );
            }
            if let Ok(Some(status)) = child.try_wait() {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("native microphone helper exited before ready: {status}"),
                );
            }
            if Instant::now() >= deadline {
                stop_process_group(&mut child, STOP_TIMEOUT);
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    "native microphone helper readiness timed out".to_string(),
                );
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    pub fn prepare(&self) -> NativeMicCaptureResponse {
        self.snapshot("mic_adapter.prepare")
    }

    pub fn status(&self) -> NativeMicCaptureResponse {
        self.snapshot("mic_adapter.status")
    }

    fn snapshot(&self, command_id: &'static str) -> NativeMicCaptureResponse {
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    command_id,
                    None,
                    "native microphone state lock poisoned".to_string(),
                )
            }
        };
        if let Some(child) = state.child.as_mut() {
            if child.try_wait().ok().flatten().is_some() {
                state.child = None;
                state.status = "stopped";
            }
        }
        self.response(
            command_id,
            "ok",
            state.status,
            state.session_id.clone(),
            state.child.as_ref().map(Child::id),
            state.ready_file.clone(),
            false,
            Vec::new(),
        )
    }

    #[cfg(unix)]
    pub fn pause(&self) -> NativeMicCaptureResponse {
        self.signal("mic_adapter.pause", libc::SIGUSR1, "paused")
    }

    #[cfg(not(unix))]
    pub fn pause(&self) -> NativeMicCaptureResponse {
        self.error_response(
            "mic_adapter.pause",
            None,
            "native microphone capture is currently supported only on macOS".to_string(),
        )
    }

    #[cfg(unix)]
    pub fn resume(&self) -> NativeMicCaptureResponse {
        self.signal("mic_adapter.resume", libc::SIGUSR2, "recording")
    }

    #[cfg(not(unix))]
    pub fn resume(&self) -> NativeMicCaptureResponse {
        self.error_response(
            "mic_adapter.resume",
            None,
            "native microphone capture is currently supported only on macOS".to_string(),
        )
    }

    pub fn stop(&self) -> NativeMicCaptureResponse {
        self.stop_for_session(None)
    }

    pub fn stop_for_session(&self, requested_session_id: Option<&str>) -> NativeMicCaptureResponse {
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    "mic_adapter.stop",
                    None,
                    "native microphone state lock poisoned".to_string(),
                )
            }
        };
        if let Some(requested_session_id) = requested_session_id {
            if state.child.is_some() && state.session_id.as_deref() != Some(requested_session_id) {
                return self.response(
                    "mic_adapter.stop",
                    "conflict",
                    "session_mismatch",
                    state.session_id.clone(),
                    state.child.as_ref().map(Child::id),
                    state.ready_file.clone(),
                    false,
                    vec![
                        "requested session does not match active native microphone session"
                            .to_string(),
                    ],
                );
            }
        }
        if let Some(mut child) = state.child.take() {
            stop_process_group(&mut child, STOP_TIMEOUT);
        }
        state.status = "stopped";
        self.response(
            "mic_adapter.stop",
            "ok",
            "stopped",
            state.session_id.clone(),
            None,
            state.ready_file.clone(),
            false,
            Vec::new(),
        )
    }

    fn signal(
        &self,
        command_id: &'static str,
        signal: i32,
        status: &'static str,
    ) -> NativeMicCaptureResponse {
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    command_id,
                    None,
                    "native microphone state lock poisoned".to_string(),
                )
            }
        };
        let Some(child) = state.child.as_ref() else {
            return self.error_response(
                command_id,
                state.session_id.clone(),
                "native microphone is not recording".to_string(),
            );
        };
        let pid = child.id();
        #[cfg(unix)]
        unsafe {
            if libc::kill(-(pid as i32), signal) != 0 {
                return self.error_response(
                    command_id,
                    state.session_id.clone(),
                    format!(
                        "failed to signal native microphone: {}",
                        std::io::Error::last_os_error()
                    ),
                );
            }
        }
        state.status = status;
        self.response(
            command_id,
            "ok",
            status,
            state.session_id.clone(),
            Some(pid),
            state.ready_file.clone(),
            false,
            Vec::new(),
        )
    }

    fn error_response(
        &self,
        command_id: &'static str,
        session_id: Option<String>,
        error: String,
    ) -> NativeMicCaptureResponse {
        self.response(
            command_id,
            "blocked",
            "error",
            session_id,
            None,
            None,
            false,
            vec![error],
        )
    }

    fn response(
        &self,
        command_id: &'static str,
        command_status: &'static str,
        status: &'static str,
        session_id: Option<String>,
        pid: Option<u32>,
        ready_file: Option<PathBuf>,
        captures_audio: bool,
        errors: Vec<String>,
    ) -> NativeMicCaptureResponse {
        let writes_local_files = ready_file.is_some();
        NativeMicCaptureResponse {
            command_id,
            command_status,
            implementation_status: "native_av_audio_engine_helper",
            transport_status: "tauri_ipc_bound",
            side_effect_status: if captures_audio {
                "native_microphone_streaming"
            } else {
                "none"
            },
            session_id,
            pid,
            status,
            helper_present: self.helper_path.is_file(),
            ready_file: ready_file.map(|path| display_path(&path)),
            safe_to_execute_real_action: captures_audio || command_status == "ok",
            captures_audio,
            spawns_process: pid.is_some(),
            calls_remote_provider: false,
            writes_local_files,
            errors,
        }
    }
}

fn normalize_session_id(session_id: Option<String>) -> Result<String, String> {
    let value = session_id.unwrap_or_default();
    if value.is_empty()
        || value.len() > 128
        || !value
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.'))
    {
        return Err("session_id contains unsafe characters".to_string());
    }
    Ok(value)
}

fn ready_file_is_ready(path: &Path, expected_session_id: &str) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let mut content = String::new();
    if file.read_to_string(&mut content).is_err() {
        return false;
    }
    let Ok(value) = serde_json::from_str::<Value>(&content) else {
        return false;
    };
    value.get("schema_version").and_then(Value::as_str)
        == Some("meeting_copilot.native_mic_ready.v1")
        && value.get("status").and_then(Value::as_str) == Some("ready")
        && value.get("session_id").and_then(Value::as_str) == Some(expected_session_id)
        && value.get("sample_rate_hz").and_then(Value::as_u64) == Some(16_000)
        && value.get("channels").and_then(Value::as_u64) == Some(1)
        && value.get("sample_format").and_then(Value::as_str) == Some("pcm_f32le")
        && value.get("frame_samples").and_then(Value::as_u64) == Some(4_800)
}

fn stop_process_group(child: &mut Child, timeout: Duration) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGTERM);
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

fn display_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn test_root(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock must be after unix epoch")
            .as_nanos();
        std::env::temp_dir().join(format!("meeting-copilot-native-mic-{name}-{nonce}"))
    }

    fn fake_helper(root: &Path) -> PathBuf {
        let helper = root.join("fake-native-mic.sh");
        fs::create_dir_all(root).unwrap();
        fs::write(
            &helper,
            r#"#!/bin/sh
ready=""
session=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ready-file) ready="$2"; shift 2 ;;
    --session-id) session="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$(dirname "$ready")"
printf '%s' "{\"schema_version\":\"meeting_copilot.native_mic_ready.v1\",\"status\":\"ready\",\"session_id\":\"$session\",\"sample_rate_hz\":16000,\"channels\":1,\"sample_format\":\"pcm_f32le\",\"frame_samples\":4800}" > "$ready"
trap 'exit 0' TERM INT
trap ':' USR1
trap ':' USR2
while :; do sleep 1; done
"#,
        )
        .unwrap();
        let mut permissions = fs::metadata(&helper).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&helper, permissions).unwrap();
        helper
    }

    fn external_backend() -> BackendSupervisor {
        let backend = BackendSupervisor::default();
        backend.use_external("http://127.0.0.1:8765".to_string());
        backend
    }

    #[test]
    fn session_ids_are_restricted_before_process_spawn() {
        assert!(normalize_session_id(Some("meeting_01".to_string())).is_ok());
        assert!(normalize_session_id(Some("../private".to_string())).is_err());
        assert!(normalize_session_id(Some("meeting/01".to_string())).is_err());
        assert!(normalize_session_id(Some("a".repeat(129))).is_err());
    }

    #[test]
    fn missing_helper_fails_closed_without_spawning_or_audio() {
        let root = test_root("missing");
        let supervisor = NativeMicCaptureSupervisor::new(
            root.join("missing-helper"),
            root.join("runtime"),
            root.join("logs"),
        );
        let response = supervisor.start(Some("meeting_missing".to_string()), &external_backend());
        assert_eq!(response.command_status, "blocked");
        assert!(!response.captures_audio);
        assert!(!response.spawns_process);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn ready_file_requires_native_mic_protocol_and_matching_session() {
        let root = test_root("ready");
        fs::create_dir_all(&root).unwrap();
        let ready = root.join("ready.json");
        fs::write(
            &ready,
            r#"{"schema_version":"meeting_copilot.native_mic_ready.v1","status":"ready","session_id":"meeting_ready","sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800}"#,
        )
        .unwrap();
        assert!(ready_file_is_ready(&ready, "meeting_ready"));
        assert!(!ready_file_is_ready(&ready, "other_session"));
        fs::write(&ready, br#"{"status":"ready"}"#).unwrap();
        assert!(!ready_file_is_ready(&ready, "meeting_ready"));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn lifecycle_has_one_active_session_and_cleans_up_process_group() {
        let root = test_root("lifecycle");
        let helper = fake_helper(&root);
        let supervisor =
            NativeMicCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));
        let backend = external_backend();

        let started = supervisor.start(Some("meeting_lifecycle".to_string()), &backend);
        assert_eq!(started.command_status, "ok");
        assert_eq!(started.status, "recording");
        assert!(started.captures_audio);
        assert!(started.spawns_process);
        assert!(started.writes_local_files);
        assert!(!serde_json::to_string(&started)
            .unwrap()
            .contains("meeting_copilot_session"));

        let conflict = supervisor.start(Some("meeting_other".to_string()), &backend);
        assert_eq!(conflict.command_status, "conflict");
        assert!(!conflict.captures_audio);

        assert_eq!(supervisor.pause().status, "paused");
        assert_eq!(supervisor.resume().status, "recording");
        let mismatch = supervisor.stop_for_session(Some("meeting_other"));
        assert_eq!(mismatch.command_status, "conflict");
        assert_eq!(supervisor.status().status, "recording");
        let stopped = supervisor.stop_for_session(Some("meeting_lifecycle"));
        assert_eq!(stopped.status, "stopped");
        assert!(!stopped.spawns_process);
        assert_eq!(supervisor.status().status, "stopped");

        let _ = fs::remove_dir_all(root);
    }
}

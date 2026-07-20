use crate::desktop_backend_supervisor::{BackendSupervisor, BackendWebSocketConnection};
use crate::private_storage::{
    ensure_private_directory, harden_private_file, open_private_file,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::VecDeque;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const READY_TIMEOUT: Duration = Duration::from_secs(30);
const STOP_TIMEOUT: Duration = Duration::from_secs(15);
const PROBE_TIMEOUT: Duration = Duration::from_secs(8);
const PROBE_DURATION_SECONDS: &str = "2.5";
const AUDIBLE_RMS_THRESHOLD: f64 = 0.002;

#[derive(Debug, Clone, Serialize)]
pub struct NativeMicCaptureResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub implementation_status: &'static str,
    pub transport_status: &'static str,
    pub side_effect_status: &'static str,
    pub source: &'static str,
    pub track_id: &'static str,
    pub session_id: Option<String>,
    pub capture_epoch: Option<u64>,
    pub pid: Option<u32>,
    pub status: &'static str,
    pub health_status: &'static str,
    pub helper_present: bool,
    pub ready_file: Option<String>,
    pub transport_ready: bool,
    pub pcm_seen: bool,
    pub audible_pcm_seen: bool,
    pub first_pcm_rms: Option<f32>,
    pub pcm_bytes_sent: u64,
    pub pcm_protocol: Option<&'static str>,
    pub safe_to_execute_real_action: bool,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub writes_raw_audio_files: bool,
    pub raw_audio_uploaded: bool,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NativeMicEventsResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub source: &'static str,
    pub track_id: &'static str,
    pub session_id: Option<String>,
    pub capture_epoch: Option<u64>,
    pub health_status: &'static str,
    pub transport_ready: bool,
    pub pcm_seen: bool,
    pub audible_pcm_seen: bool,
    pub pcm_protocol: Option<&'static str>,
    pub events: Vec<Value>,
    pub raw_audio_uploaded: bool,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct NativeMicProbeResponse {
    pub command_id: &'static str,
    pub command_status: &'static str,
    pub probe_status: &'static str,
    pub helper_present: bool,
    pub sampled: bool,
    pub rms: f64,
    pub peak_rms: f64,
    pub level: f64,
    pub duration_ms: u64,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub calls_remote_provider: bool,
    pub writes_local_files: bool,
    pub creates_meeting_assets: bool,
    pub starts_asr: bool,
    pub errors: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct NativeMicProbePayload {
    schema_version: String,
    probe_status: String,
    sampled: bool,
    rms: f64,
    peak_rms: f64,
    duration_ms: u64,
}

#[derive(Debug, Deserialize)]
struct NativeMicReadyPayload {
    schema_version: String,
    status: String,
    session_id: String,
    source: String,
    sample_rate_hz: u32,
    channels: u32,
    sample_format: String,
    frame_samples: u32,
    transport_ready: bool,
    pcm_seen: bool,
    audible_pcm_seen: bool,
    first_pcm_rms: Option<f32>,
    pcm_bytes_sent: u64,
    pcm_protocol: String,
    capture_epoch: u64,
}

#[derive(Debug, Clone, Copy, Default)]
struct CaptureReadiness {
    transport_ready: bool,
    pcm_seen: bool,
    audible_pcm_seen: bool,
    first_pcm_rms: Option<f32>,
    pcm_bytes_sent: u64,
}

impl CaptureReadiness {
    fn from_ready(ready: &NativeMicReadyPayload) -> Self {
        Self {
            transport_ready: ready.transport_ready,
            pcm_seen: ready.pcm_seen,
            audible_pcm_seen: ready.audible_pcm_seen,
            first_pcm_rms: ready.first_pcm_rms,
            pcm_bytes_sent: ready.pcm_bytes_sent,
        }
    }
}

struct NativeMicState {
    child: Option<Child>,
    session_id: Option<String>,
    ready_file: Option<PathBuf>,
    capture_epoch: Option<u64>,
    next_capture_epoch: u64,
    status: &'static str,
}

pub struct NativeMicCaptureSupervisor {
    helper_path: PathBuf,
    runtime_root: PathBuf,
    log_dir: PathBuf,
    state: Mutex<NativeMicState>,
    events: Arc<Mutex<VecDeque<Value>>>,
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
                capture_epoch: None,
                next_capture_epoch: 0,
                status: "not_started",
            }),
            events: Arc::new(Mutex::new(VecDeque::new())),
        }
    }

    pub fn start(
        &self,
        session_id: Option<String>,
        backend: &BackendSupervisor,
    ) -> NativeMicCaptureResponse {
        self.start_with_epoch(session_id, None, backend)
    }

    pub fn start_with_epoch(
        &self,
        session_id: Option<String>,
        requested_capture_epoch: Option<u64>,
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
                state.capture_epoch,
                Some(active_pid),
                state.ready_file.clone(),
                false,
                vec!["only one native microphone session is supported".to_string()],
            );
        }
        if state.child.is_some() {
            state.child = None;
        }
        let capture_epoch = match requested_capture_epoch {
            Some(0) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    "capture_epoch must be greater than zero".to_string(),
                )
            }
            Some(epoch) => {
                state.next_capture_epoch = state.next_capture_epoch.max(epoch);
                epoch
            }
            None => match state.next_capture_epoch.checked_add(1) {
                Some(epoch) => {
                    state.next_capture_epoch = epoch;
                    epoch
                }
                None => {
                    return self.error_response(
                        "mic_adapter.start",
                        Some(session_id),
                        "microphone capture epoch exhausted".to_string(),
                    )
                }
            },
        };
        let connection = match native_microphone_connection(backend, &session_id, capture_epoch) {
            Ok(value) => value,
            Err(error) => return self.error_response("mic_adapter.start", Some(session_id), error),
        };
        if let Ok(mut events) = self.events.lock() {
            events.clear();
        }
        let ready_file = self.runtime_root.join(&session_id).join("ready.json");
        if let Some(parent) = ready_file.parent() {
            if let Err(error) = ensure_private_directory(parent) {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to create native microphone runtime: {error}"),
                );
            }
        }
        let _ = fs::remove_file(&ready_file);
        if let Err(error) = ensure_private_directory(&self.log_dir) {
            return self.error_response(
                "mic_adapter.start",
                Some(session_id),
                format!("failed to create native microphone log directory: {error}"),
            );
        }
        let stdout_path = self
            .log_dir
            .join(format!("native-mic-{session_id}.stdout.log"));
        let stderr_path = self
            .log_dir
            .join(format!("native-mic-{session_id}.stderr.log"));
        let stdout = match open_private_file(&stdout_path, false) {
            Ok(file) => file,
            Err(error) => {
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!("failed to open native microphone stdout: {error}"),
                )
            }
        };
        let stderr = match open_private_file(&stderr_path, false) {
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
            .stdout(Stdio::piped())
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
        if let Some(stdout_pipe) = child.stdout.take() {
            let events = Arc::clone(&self.events);
            thread::spawn(move || {
                collect_stdout_events(stdout_pipe, stdout, events, capture_epoch)
            });
        }
        let deadline = Instant::now() + READY_TIMEOUT;
        loop {
            if read_ready_file(&ready_file, &session_id, capture_epoch).is_some() {
                if let Err(error) = harden_private_file(&ready_file) {
                    stop_process_group(&mut child, STOP_TIMEOUT);
                    return self.error_response(
                        "mic_adapter.start",
                        Some(session_id),
                        format!("failed to protect native microphone state: {error}"),
                    );
                }
                state.child = Some(child);
                state.session_id = Some(session_id.clone());
                state.ready_file = Some(ready_file.clone());
                state.capture_epoch = Some(capture_epoch);
                state.status = "recording";
                return self.response(
                    "mic_adapter.start",
                    "ok",
                    "recording",
                    Some(session_id),
                    Some(capture_epoch),
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
                    helper_exit_error(&stderr_path, status),
                );
            }
            if Instant::now() >= deadline {
                stop_process_group(&mut child, STOP_TIMEOUT);
                return self.error_response(
                    "mic_adapter.start",
                    Some(session_id),
                    format!(
                        "native microphone helper readiness timed out{}",
                        helper_stderr_tail(&stderr_path)
                            .map(|tail| format!(": {tail}"))
                            .unwrap_or_default()
                    ),
                );
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    pub fn prepare(&self) -> NativeMicCaptureResponse {
        self.snapshot("mic_adapter.prepare")
    }

    pub fn probe(&self) -> NativeMicProbeResponse {
        if !self.helper_path.is_file() {
            return self.probe_error(
                "device_unavailable",
                format!(
                    "native microphone helper is missing: {}",
                    self.helper_path.display()
                ),
                false,
            );
        }
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.probe_error(
                    "error",
                    "native microphone state lock poisoned".to_string(),
                    false,
                )
            }
        };
        if let Some(child) = state.child.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                return self.probe_response(
                    "conflict",
                    "error",
                    false,
                    0.0,
                    0.0,
                    0,
                    false,
                    vec!["cannot probe while native microphone capture is active".to_string()],
                );
            }
            state.child = None;
            state.status = "stopped";
        }
        // Serialize the short probe with formal capture startup so AVAudioEngine is never
        // opened by two helper processes from the same desktop supervisor.

        let mut command = Command::new(&self.helper_path);
        command
            .arg("--probe")
            .arg("--duration")
            .arg(PROBE_DURATION_SECONDS)
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env(
                "HOME",
                self.runtime_root.parent().unwrap_or(&self.runtime_root),
            )
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = match command.spawn() {
            Ok(value) => value,
            Err(error) => {
                return self.probe_error(
                    "device_unavailable",
                    format!("failed to spawn native microphone probe: {error}"),
                    false,
                )
            }
        };
        let deadline = Instant::now() + PROBE_TIMEOUT;
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) if Instant::now() < deadline => {
                    thread::sleep(Duration::from_millis(25));
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return self.probe_error(
                        "error",
                        "native microphone probe timed out".to_string(),
                        true,
                    );
                }
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return self.probe_error(
                        "error",
                        format!("failed to wait for native microphone probe: {error}"),
                        true,
                    );
                }
            }
        };
        let mut stdout = String::new();
        if let Some(mut pipe) = child.stdout.take() {
            let _ = pipe.read_to_string(&mut stdout);
        }
        let mut stderr = String::new();
        if let Some(mut pipe) = child.stderr.take() {
            let _ = pipe.read_to_string(&mut stderr);
        }
        if !status.success() {
            let diagnostic = stderr
                .lines()
                .rev()
                .find(|line| !line.trim().is_empty())
                .unwrap_or("native microphone probe exited before sampling")
                .trim();
            return self.probe_error(
                classify_probe_failure(diagnostic),
                format!("native microphone probe failed: {diagnostic}"),
                true,
            );
        }
        let payload = stdout
            .lines()
            .rev()
            .filter(|line| !line.trim().is_empty())
            .find_map(|line| serde_json::from_str::<NativeMicProbePayload>(line).ok());
        let Some(payload) = payload else {
            return self.probe_error(
                "error",
                "native microphone probe returned no valid result".to_string(),
                true,
            );
        };
        if payload.schema_version != "meeting_copilot.native_mic_probe.v1"
            || !payload.sampled
            || !matches!(payload.probe_status.as_str(), "audible" | "silent")
            || !payload.rms.is_finite()
            || !payload.peak_rms.is_finite()
            || !(0.0..=1.0).contains(&payload.rms)
            || !(0.0..=1.0).contains(&payload.peak_rms)
            || !(2_000..=3_000).contains(&payload.duration_ms)
        {
            return self.probe_error(
                "error",
                "native microphone probe returned an invalid result".to_string(),
                true,
            );
        }
        let probe_status = if payload.peak_rms >= AUDIBLE_RMS_THRESHOLD {
            "audible"
        } else {
            "silent"
        };
        drop(state);
        self.probe_response(
            "ok",
            probe_status,
            true,
            payload.rms,
            payload.peak_rms,
            payload.duration_ms,
            true,
            Vec::new(),
        )
    }

    pub fn status(&self) -> NativeMicCaptureResponse {
        self.snapshot("mic_adapter.status")
    }

    pub fn collect_events(&self, session_id: Option<String>) -> NativeMicEventsResponse {
        let requested_session = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => return self.events_error(None, error),
        };
        let state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.events_error(
                    Some(requested_session),
                    "native microphone state lock poisoned".to_string(),
                )
            }
        };
        if state.session_id.as_deref() != Some(requested_session.as_str()) {
            return self.events_error(
                Some(requested_session),
                "native microphone session does not match the active session".to_string(),
            );
        }
        let capture_epoch = state.capture_epoch;
        let readiness = readiness_from_file(
            state.ready_file.as_deref(),
            state.session_id.as_deref(),
            state.capture_epoch,
        );
        let current_health = health_status(state.status, state.child.is_some(), readiness);
        drop(state);
        let events = self
            .events
            .lock()
            .map(|mut queue| queue.drain(..).collect())
            .unwrap_or_default();
        NativeMicEventsResponse {
            command_id: "mic_adapter.collect_events",
            command_status: "ok",
            source: "microphone",
            track_id: "microphone",
            session_id: Some(requested_session),
            capture_epoch,
            health_status: current_health,
            transport_ready: readiness.transport_ready,
            pcm_seen: readiness.pcm_seen,
            audible_pcm_seen: readiness.audible_pcm_seen,
            pcm_protocol: readiness.pcm_seen.then_some("native_pcm_v2"),
            events,
            raw_audio_uploaded: false,
            errors: Vec::new(),
        }
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
            state.capture_epoch,
            state.child.as_ref().map(Child::id),
            state.ready_file.clone(),
            state.child.is_some(),
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
                    state.capture_epoch,
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
            state.capture_epoch,
            None,
            state.ready_file.clone(),
            false,
            Vec::new(),
        )
    }

    pub fn cleanup_for_session(
        &self,
        requested_session_id: Option<&str>,
    ) -> NativeMicCaptureResponse {
        let normalized_requested = match requested_session_id {
            Some(value) => match normalize_session_id(Some(value.to_string())) {
                Ok(value) => Some(value),
                Err(error) => return self.error_response("mic_adapter.cleanup", None, error),
            },
            None => None,
        };
        if let Some(requested_session_id) = normalized_requested.as_deref() {
            let state = match self.state.lock() {
                Ok(state) => state,
                Err(_) => {
                    return self.error_response(
                        "mic_adapter.cleanup",
                        None,
                        "native microphone state lock poisoned".to_string(),
                    )
                }
            };
            if state
                .session_id
                .as_deref()
                .is_some_and(|session_id| session_id != requested_session_id)
            {
                return self.response(
                    "mic_adapter.cleanup",
                    "conflict",
                    "session_mismatch",
                    state.session_id.clone(),
                    state.capture_epoch,
                    state.child.as_ref().map(Child::id),
                    state.ready_file.clone(),
                    state.child.is_some(),
                    vec!["requested session does not match native microphone state".to_string()],
                );
            }
        }
        let stopped = self.stop_for_session(normalized_requested.as_deref());
        if stopped.command_status != "ok" {
            return stopped;
        }
        let session_id = normalized_requested.or(stopped.session_id.clone());
        let capture_epoch = stopped.capture_epoch;
        let mut errors = Vec::new();
        if let Some(session_id) = session_id.as_deref() {
            remove_path_if_exists(&self.runtime_root.join(session_id), &mut errors);
            remove_path_if_exists(
                &self
                    .log_dir
                    .join(format!("native-mic-{session_id}.stdout.log")),
                &mut errors,
            );
            remove_path_if_exists(
                &self
                    .log_dir
                    .join(format!("native-mic-{session_id}.stderr.log")),
                &mut errors,
            );
        }
        if let Ok(mut events) = self.events.lock() {
            events.clear();
        } else {
            errors.push("native microphone event queue lock poisoned".to_string());
        }
        if let Ok(mut state) = self.state.lock() {
            if session_id.is_none() || state.session_id == session_id {
                state.session_id = None;
                state.ready_file = None;
                state.capture_epoch = None;
                state.status = "cleaned";
            }
        } else {
            errors.push("native microphone state lock poisoned".to_string());
        }
        self.response(
            "mic_adapter.cleanup",
            if errors.is_empty() { "ok" } else { "blocked" },
            if errors.is_empty() {
                "cleaned"
            } else {
                "cleanup_failed"
            },
            session_id,
            capture_epoch,
            None,
            None,
            false,
            errors,
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
            state.capture_epoch,
            Some(pid),
            state.ready_file.clone(),
            true,
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
            None,
            false,
            vec![error],
        )
    }

    fn events_error(&self, session_id: Option<String>, error: String) -> NativeMicEventsResponse {
        NativeMicEventsResponse {
            command_id: "mic_adapter.collect_events",
            command_status: "blocked",
            source: "microphone",
            track_id: "microphone",
            session_id,
            capture_epoch: None,
            health_status: "blocked",
            transport_ready: false,
            pcm_seen: false,
            audible_pcm_seen: false,
            pcm_protocol: None,
            events: Vec::new(),
            raw_audio_uploaded: false,
            errors: vec![error],
        }
    }

    fn probe_error(
        &self,
        probe_status: &'static str,
        error: String,
        spawned: bool,
    ) -> NativeMicProbeResponse {
        self.probe_response(
            "blocked",
            probe_status,
            false,
            0.0,
            0.0,
            0,
            spawned,
            vec![error],
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn probe_response(
        &self,
        command_status: &'static str,
        probe_status: &'static str,
        sampled: bool,
        rms: f64,
        peak_rms: f64,
        duration_ms: u64,
        spawned: bool,
        errors: Vec<String>,
    ) -> NativeMicProbeResponse {
        NativeMicProbeResponse {
            command_id: "mic_adapter.probe",
            command_status,
            probe_status,
            helper_present: self.helper_path.is_file(),
            sampled,
            rms,
            peak_rms,
            level: (peak_rms * 6.0).clamp(0.0, 1.0),
            duration_ms,
            captures_audio: sampled,
            spawns_process: spawned,
            calls_remote_provider: false,
            writes_local_files: false,
            creates_meeting_assets: false,
            starts_asr: false,
            errors,
        }
    }

    fn response(
        &self,
        command_id: &'static str,
        command_status: &'static str,
        status: &'static str,
        session_id: Option<String>,
        capture_epoch: Option<u64>,
        pid: Option<u32>,
        ready_file: Option<PathBuf>,
        captures_audio: bool,
        errors: Vec<String>,
    ) -> NativeMicCaptureResponse {
        let writes_local_files = ready_file.is_some();
        let readiness = readiness_from_file(
            ready_file.as_deref(),
            session_id.as_deref(),
            capture_epoch,
        );
        let captures_audio = captures_audio && readiness.transport_ready && readiness.pcm_seen;
        NativeMicCaptureResponse {
            command_id,
            command_status,
            implementation_status: "native_av_audio_engine_helper",
            transport_status: if readiness.transport_ready {
                "authenticated_loopback_websocket_ready"
            } else {
                "authenticated_loopback_websocket_not_ready"
            },
            side_effect_status: if captures_audio {
                "native_microphone_streaming"
            } else {
                "none"
            },
            source: "microphone",
            track_id: "microphone",
            session_id,
            capture_epoch,
            pid,
            status,
            health_status: health_status(status, pid.is_some(), readiness),
            helper_present: self.helper_path.is_file(),
            ready_file: ready_file.map(|path| display_path(&path)),
            transport_ready: readiness.transport_ready,
            pcm_seen: readiness.pcm_seen,
            audible_pcm_seen: readiness.audible_pcm_seen,
            first_pcm_rms: readiness.first_pcm_rms,
            pcm_bytes_sent: readiness.pcm_bytes_sent,
            pcm_protocol: readiness.pcm_seen.then_some("native_pcm_v2"),
            safe_to_execute_real_action: captures_audio || command_status == "ok",
            captures_audio,
            spawns_process: pid.is_some(),
            calls_remote_provider: false,
            writes_local_files,
            writes_raw_audio_files: false,
            raw_audio_uploaded: false,
            errors,
        }
    }
}

fn collect_stdout_events(
    stdout_pipe: impl Read,
    mut log: File,
    events: Arc<Mutex<VecDeque<Value>>>,
    capture_epoch: u64,
) {
    let mut track_sequence = 0_u64;
    for line in BufReader::new(stdout_pipe).lines() {
        let Ok(line) = line else { break };
        let _ = writeln!(log, "{line}");
        let Ok(mut value) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let event_type = value
            .get("event_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if !matches!(
            event_type.as_str(),
            "asr_starting"
                | "asr_ready"
                | "partial"
                | "final"
                | "end_of_stream"
                | "error"
                | "provider_error"
                | "input_level"
        ) {
            continue;
        }
        let Some(object) = value.as_object_mut() else {
            continue;
        };
        track_sequence = track_sequence.saturating_add(1);
        object.insert(
            "source".to_string(),
            Value::String("microphone".to_string()),
        );
        object.insert(
            "track_id".to_string(),
            Value::String("microphone".to_string()),
        );
        object.insert("capture_epoch".to_string(), Value::from(capture_epoch));
        object.insert("track_sequence".to_string(), Value::from(track_sequence));
        object.insert(
            "observed_at_unix_ms".to_string(),
            Value::from(unix_timestamp_ms()),
        );
        object.insert(
            "health_status".to_string(),
            Value::String(
                if matches!(event_type.as_str(), "error" | "provider_error") {
                    "error"
                } else {
                    "healthy"
                }
                .to_string(),
            ),
        );
        object.insert("raw_audio_uploaded".to_string(), Value::Bool(false));
        if let Ok(mut queue) = events.lock() {
            queue.push_back(value);
            while queue.len() > 128 {
                queue.pop_front();
            }
        }
    }
}

fn unix_timestamp_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or(0)
}

fn health_status(status: &str, running: bool, readiness: CaptureReadiness) -> &'static str {
    if running && matches!(status, "recording" | "paused") {
        if !readiness.transport_ready || !readiness.pcm_seen {
            "starting"
        } else if !readiness.audible_pcm_seen {
            "silent"
        } else {
            "healthy"
        }
    } else {
        match status {
            "not_started" | "stopped" | "cleaned" => "stopped",
            "session_mismatch" => "conflict",
            "error" | "cleanup_failed" => "blocked",
            _ => "unknown",
        }
    }
}

fn remove_path_if_exists(path: &Path, errors: &mut Vec<String>) {
    let result = if path.is_dir() {
        fs::remove_dir_all(path)
    } else if path.exists() {
        fs::remove_file(path)
    } else {
        Ok(())
    };
    if let Err(error) = result {
        errors.push(format!("failed to remove {}: {error}", path.display()));
    }
}

fn native_microphone_connection(
    backend: &BackendSupervisor,
    session_id: &str,
    capture_epoch: u64,
) -> Result<BackendWebSocketConnection, String> {
    if capture_epoch == 0 {
        return Err("microphone capture epoch must be greater than zero".to_string());
    }
    let mut connection = backend.native_microphone_connection(session_id)?;
    let mut parsed = url::Url::parse(&connection.url)
        .map_err(|error| format!("backend websocket URL is invalid: {error}"))?;
    if parsed.scheme() != "ws"
        || parsed.host_str() != Some("127.0.0.1")
        || parsed.port().is_none()
        || !parsed.path().starts_with("/live/asr/stream/ws/")
    {
        return Err(
            "microphone transport is restricted to the authenticated packaged loopback backend"
                .to_string(),
        );
    }
    parsed
        .query_pairs_mut()
        .clear()
        .append_pair("audio_source", "tauri_native_mic")
        .append_pair("pcm_protocol", "native_pcm_v2")
        .append_pair("capture_epoch", &capture_epoch.to_string());
    connection.url = parsed.into();
    Ok(connection)
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

fn classify_probe_failure(diagnostic: &str) -> &'static str {
    let normalized = diagnostic.to_ascii_lowercase();
    if normalized.contains("permission") && normalized.contains("denied") {
        "permission_denied"
    } else if normalized.contains("input format")
        || normalized.contains("no input")
        || normalized.contains("no samples")
        || normalized.contains("device")
        || normalized.contains("audio error")
    {
        "device_unavailable"
    } else {
        "error"
    }
}

fn readiness_from_file(
    path: Option<&Path>,
    expected_session_id: Option<&str>,
    expected_capture_epoch: Option<u64>,
) -> CaptureReadiness {
    let (Some(path), Some(session_id), Some(capture_epoch)) =
        (path, expected_session_id, expected_capture_epoch)
    else {
        return CaptureReadiness::default();
    };
    read_ready_file(path, session_id, capture_epoch)
        .as_ref()
        .map(CaptureReadiness::from_ready)
        .unwrap_or_default()
}

fn read_ready_file(
    path: &Path,
    expected_session_id: &str,
    expected_capture_epoch: u64,
) -> Option<NativeMicReadyPayload> {
    let Ok(mut file) = File::open(path) else {
        return None;
    };
    let mut content = String::new();
    if file.read_to_string(&mut content).is_err() {
        return None;
    }
    let Ok(payload) = serde_json::from_str::<NativeMicReadyPayload>(&content) else {
        return None;
    };
    let valid = payload.schema_version == "meeting_copilot.native_mic_ready.v1"
        && payload.status == "ready"
        && payload.session_id == expected_session_id
        && payload.source == "av_audio_engine_microphone"
        && payload.sample_rate_hz == 16_000
        && payload.channels == 1
        && payload.sample_format == "pcm_f32le"
        && payload.frame_samples == 4_800
        && payload.transport_ready
        && payload.pcm_seen
        && payload
            .first_pcm_rms
            .is_some_and(|rms| rms.is_finite() && (0.0..=1.0).contains(&rms))
        && payload.pcm_bytes_sent >= 4_800 * std::mem::size_of::<f32>() as u64
        && payload.pcm_protocol == "native_pcm_v2"
        && payload.capture_epoch == expected_capture_epoch;
    valid.then_some(payload)
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

fn helper_stderr_tail(path: &Path) -> Option<String> {
    let mut content = String::new();
    File::open(path).ok()?.read_to_string(&mut content).ok()?;
    let tail = content
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())?
        .trim()
        .to_string();
    if tail.is_empty() {
        None
    } else {
        Some(tail.chars().take(500).collect())
    }
}

fn helper_exit_error(path: &Path, status: std::process::ExitStatus) -> String {
    match helper_stderr_tail(path) {
        Some(tail) => format!("native microphone helper exited before ready: {status}: {tail}"),
        None => format!("native microphone helper exited before ready: {status}"),
    }
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
printf '%s' "{\"schema_version\":\"meeting_copilot.native_mic_ready.v1\",\"status\":\"ready\",\"session_id\":\"$session\",\"source\":\"av_audio_engine_microphone\",\"sample_rate_hz\":16000,\"channels\":1,\"sample_format\":\"pcm_f32le\",\"frame_samples\":4800,\"transport_ready\":true,\"pcm_seen\":true,\"audible_pcm_seen\":true,\"first_pcm_rms\":0.125,\"pcm_bytes_sent\":19200,\"pcm_protocol\":\"native_pcm_v2\",\"capture_epoch\":1}" > "$ready"
printf '%s\n' "{\"event_type\":\"partial\",\"segment_id\":\"native_partial_1\",\"text\":\"正在讨论灰度发布\",\"start_ms\":1200}"
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

    fn failing_helper(root: &Path) -> PathBuf {
        let helper = root.join("failing-native-mic.sh");
        fs::create_dir_all(root).unwrap();
        fs::write(
            &helper,
            "#!/bin/sh\nprintf '%s\\n' 'fatal: microphone permission was denied' >&2\nexit 70\n",
        )
        .unwrap();
        let mut permissions = fs::metadata(&helper).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&helper, permissions).unwrap();
        helper
    }

    fn probe_helper(root: &Path, body: &str) -> PathBuf {
        let helper = root.join("probe-native-mic.sh");
        fs::create_dir_all(root).unwrap();
        fs::write(
            &helper,
            format!(
                "#!/bin/sh\n[ \"$1\" = \"--probe\" ] || exit 64\n[ \"$2\" = \"--duration\" ] || exit 64\n[ \"$3\" = \"2.5\" ] || exit 64\n{body}\n"
            ),
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
    fn connection_binds_native_pcm_v2_microphone_identity_and_epoch() {
        let backend = BackendSupervisor::default();
        backend.use_external("http://127.0.0.1:8765".to_string());

        let connection = native_microphone_connection(&backend, "meeting_01", 4).unwrap();

        assert_eq!(
            connection.url,
            "ws://127.0.0.1:8765/live/asr/stream/ws/meeting_01?audio_source=tauri_native_mic&pcm_protocol=native_pcm_v2&capture_epoch=4"
        );
        assert!(native_microphone_connection(&backend, "meeting_01", 0).is_err());
        backend.use_external("https://example.com".to_string());
        assert!(native_microphone_connection(&backend, "meeting_01", 4).is_err());
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
    fn ready_file_requires_transported_pcm_native_protocol_and_matching_epoch() {
        let root = test_root("ready");
        fs::create_dir_all(&root).unwrap();
        let ready = root.join("ready.json");
        fs::write(
            &ready,
            r#"{"schema_version":"meeting_copilot.native_mic_ready.v1","status":"ready","session_id":"meeting_ready","source":"av_audio_engine_microphone","sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":false,"first_pcm_rms":0.0,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":7}"#,
        )
        .unwrap();
        let payload = read_ready_file(&ready, "meeting_ready", 7).unwrap();
        assert!(payload.transport_ready);
        assert!(payload.pcm_seen);
        assert!(!payload.audible_pcm_seen);
        assert_eq!(payload.first_pcm_rms, Some(0.0));
        assert_eq!(payload.pcm_bytes_sent, 19_200);
        assert!(read_ready_file(&ready, "other_session", 7).is_none());
        assert!(read_ready_file(&ready, "meeting_ready", 8).is_none());
        fs::write(
            &ready,
            r#"{"schema_version":"meeting_copilot.native_mic_ready.v1","status":"ready","session_id":"meeting_ready","source":"av_audio_engine_microphone","sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"transport_ready":true,"pcm_seen":false,"audible_pcm_seen":false,"first_pcm_rms":null,"pcm_bytes_sent":0,"pcm_protocol":"native_pcm_v2","capture_epoch":7}"#,
        )
        .unwrap();
        assert!(read_ready_file(&ready, "meeting_ready", 7).is_none());
        fs::write(&ready, br#"{"status":"ready"}"#).unwrap();
        assert!(read_ready_file(&ready, "meeting_ready", 7).is_none());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn helper_stderr_is_returned_when_start_fails_before_ready() {
        let root = test_root("stderr");
        let helper = failing_helper(&root);
        let supervisor =
            NativeMicCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));
        let response =
            supervisor.start(Some("meeting_permission".to_string()), &external_backend());
        assert_eq!(response.command_status, "blocked");
        assert!(response
            .errors
            .iter()
            .any(|error| error.contains("microphone permission was denied")));
        assert!(!response.captures_audio);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn probe_samples_audio_without_creating_a_meeting_runtime_or_asr_connection() {
        let root = test_root("probe-audible");
        let helper = probe_helper(
            &root,
            r#"printf '%s\n' '{"schema_version":"meeting_copilot.native_mic_probe.v1","probe_status":"audible","sampled":true,"rms":0.04,"peak_rms":0.08,"duration_ms":2500}'"#,
        );
        let runtime_root = root.join("runtime");
        let supervisor =
            NativeMicCaptureSupervisor::new(helper, runtime_root.clone(), root.join("logs"));

        let response = supervisor.probe();

        assert_eq!(response.command_id, "mic_adapter.probe");
        assert_eq!(response.command_status, "ok");
        assert_eq!(response.probe_status, "audible");
        assert!(response.sampled);
        assert_eq!(response.duration_ms, 2_500);
        assert!((response.rms - 0.04).abs() < f64::EPSILON);
        assert!((response.peak_rms - 0.08).abs() < f64::EPSILON);
        assert!((response.level - 0.48).abs() < f64::EPSILON);
        assert!(!response.creates_meeting_assets);
        assert!(!response.starts_asr);
        assert!(!runtime_root.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn probe_preserves_a_real_silent_sample_instead_of_reporting_prepare_success() {
        let root = test_root("probe-silent");
        let helper = probe_helper(
            &root,
            r#"printf '%s\n' '{"schema_version":"meeting_copilot.native_mic_probe.v1","probe_status":"silent","sampled":true,"rms":0.0,"peak_rms":0.0,"duration_ms":2500}'"#,
        );
        let supervisor =
            NativeMicCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));

        let response = supervisor.probe();

        assert_eq!(response.command_status, "ok");
        assert_eq!(response.probe_status, "silent");
        assert!(response.sampled);
        assert_eq!(response.duration_ms, 2_500);
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn probe_classifies_permission_denial_and_unavailable_input() {
        for (name, diagnostic, expected_status) in [
            (
                "permission",
                "fatal: microphone permission was denied",
                "permission_denied",
            ),
            (
                "device",
                "fatal: microphone input format is unavailable",
                "device_unavailable",
            ),
        ] {
            let root = test_root(name);
            let helper = probe_helper(
                &root,
                &format!("printf '%s\\n' '{diagnostic}' >&2\nexit 70"),
            );
            let supervisor =
                NativeMicCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));

            let response = supervisor.probe();

            assert_eq!(response.command_status, "blocked");
            assert_eq!(response.probe_status, expected_status);
            assert!(!response.sampled);
            assert_eq!(response.duration_ms, 0);
            assert!(response
                .errors
                .iter()
                .any(|error| error.contains(diagnostic)));
            let _ = fs::remove_dir_all(root);
        }
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
        assert_eq!(started.source, "microphone");
        assert_eq!(started.track_id, "microphone");
        assert_eq!(started.capture_epoch, Some(1));
        assert_eq!(started.health_status, "healthy");
        assert!(started.transport_ready);
        assert!(started.pcm_seen);
        assert!(started.audible_pcm_seen);
        assert_eq!(started.first_pcm_rms, Some(0.125));
        assert_eq!(started.pcm_bytes_sent, 19_200);
        assert_eq!(started.pcm_protocol, Some("native_pcm_v2"));
        assert_eq!(
            started.transport_status,
            "authenticated_loopback_websocket_ready"
        );
        assert!(started.captures_audio);
        assert!(started.spawns_process);
        assert!(started.writes_local_files);
        assert!(!serde_json::to_string(&started)
            .unwrap()
            .contains("meeting_copilot_session"));
        #[cfg(unix)]
        {
            assert_eq!(
                fs::metadata(root.join("runtime/meeting_lifecycle"))
                    .unwrap()
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
            for path in [
                root.join("runtime/meeting_lifecycle/ready.json"),
                root.join("logs/native-mic-meeting_lifecycle.stdout.log"),
                root.join("logs/native-mic-meeting_lifecycle.stderr.log"),
            ] {
                assert_eq!(
                    fs::metadata(path).unwrap().permissions().mode() & 0o777,
                    0o600
                );
            }
        }

        let probe_conflict = supervisor.probe();
        assert_eq!(probe_conflict.command_status, "conflict");
        assert!(!probe_conflict.sampled);

        let deadline = Instant::now() + Duration::from_secs(1);
        let partial = loop {
            let response = supervisor.collect_events(Some("meeting_lifecycle".to_string()));
            if let Some(event) = response.events.into_iter().next() {
                break event;
            }
            assert!(
                Instant::now() < deadline,
                "native microphone partial event was not collected"
            );
            thread::sleep(Duration::from_millis(20));
        };
        assert_eq!(partial["event_type"], "partial");
        assert_eq!(partial["text"], "正在讨论灰度发布");
        assert_eq!(partial["source"], "microphone");
        assert_eq!(partial["track_id"], "microphone");
        assert_eq!(partial["capture_epoch"], 1);
        assert_eq!(partial["track_sequence"], 1);
        assert!(partial["observed_at_unix_ms"].as_u64().unwrap() > 0);
        assert_eq!(partial["health_status"], "healthy");
        assert_eq!(partial["raw_audio_uploaded"], false);

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
        assert_eq!(stopped.capture_epoch, started.capture_epoch);
        assert_eq!(stopped.health_status, "stopped");
        assert!(!stopped.spawns_process);
        assert_eq!(supervisor.status().status, "stopped");

        let cleanup_mismatch = supervisor.cleanup_for_session(Some("meeting_other"));
        assert_eq!(cleanup_mismatch.command_status, "conflict");
        assert!(root.join("runtime/meeting_lifecycle").exists());
        let cleaned = supervisor.cleanup_for_session(Some("meeting_lifecycle"));
        assert_eq!(cleaned.command_status, "ok");
        assert_eq!(cleaned.status, "cleaned");
        assert_eq!(cleaned.capture_epoch, started.capture_epoch);
        assert!(!root.join("runtime/meeting_lifecycle").exists());
        assert_eq!(supervisor.status().status, "cleaned");

        let _ = fs::remove_dir_all(root);
    }
}

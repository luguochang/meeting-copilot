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

const READY_TIMEOUT: Duration = Duration::from_secs(60);
const STOP_TIMEOUT: Duration = Duration::from_secs(15);
const EVENT_QUEUE_LIMIT: usize = 256;
const AUDIBLE_RMS_THRESHOLD: f32 = 0.000_1;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureTrack {
    Microphone,
    SystemAudio,
}

impl CaptureTrack {
    pub fn source(self) -> &'static str {
        match self {
            Self::Microphone => "microphone",
            Self::SystemAudio => "system_audio",
        }
    }

    pub fn track_id(self) -> &'static str {
        self.source()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CaptureTrackLease {
    pub source: &'static str,
    pub track_id: &'static str,
    pub session_id: String,
    pub capture_epoch: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DualTrackLeases {
    pub microphone: CaptureTrackLease,
    pub system_audio: CaptureTrackLease,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DualTrackCoordinatorSnapshot {
    pub session_id: Option<String>,
    pub mode: &'static str,
    pub active_track_count: u8,
    pub microphone_active: bool,
    pub system_audio_active: bool,
    pub microphone_epoch: Option<u64>,
    pub system_audio_epoch: Option<u64>,
    pub microphone_probe_active: bool,
    pub mixed_audio_created: bool,
}

#[derive(Default)]
struct DualTrackCoordinatorState {
    microphone: Option<CaptureTrackLease>,
    system_audio: Option<CaptureTrackLease>,
    microphone_epoch: u64,
    system_audio_epoch: u64,
    microphone_probe_active: bool,
}

#[derive(Default)]
pub struct DualTrackCaptureCoordinator {
    state: Mutex<DualTrackCoordinatorState>,
}

impl DualTrackCaptureCoordinator {
    pub fn claim_track(
        &self,
        track: CaptureTrack,
        session_id: &str,
    ) -> Result<CaptureTrackLease, String> {
        validate_coordinator_session_id(session_id)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "dual-track capture coordinator lock poisoned".to_string())?;
        if state.microphone_probe_active {
            return Err("microphone probe is active; formal capture is blocked".to_string());
        }
        ensure_same_meeting(&state, session_id)?;
        if lease_for_track(&state, track).is_some() {
            return Err(format!(
                "{} track is already active for session {session_id}",
                track.track_id()
            ));
        }
        let capture_epoch = next_epoch(&mut state, track)?;
        let lease = CaptureTrackLease {
            source: track.source(),
            track_id: track.track_id(),
            session_id: session_id.to_string(),
            capture_epoch,
        };
        *lease_for_track_mut(&mut state, track) = Some(lease.clone());
        Ok(lease)
    }

    pub fn claim_dual_track(&self, session_id: &str) -> Result<DualTrackLeases, String> {
        validate_coordinator_session_id(session_id)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "dual-track capture coordinator lock poisoned".to_string())?;
        if state.microphone_probe_active {
            return Err("microphone probe is active; dual-track capture is blocked".to_string());
        }
        ensure_same_meeting(&state, session_id)?;
        let mut active = Vec::new();
        if state.microphone.is_some() {
            active.push("microphone");
        }
        if state.system_audio.is_some() {
            active.push("system_audio");
        }
        if !active.is_empty() {
            return Err(format!(
                "cannot atomically claim dual-track capture; active tracks: {}",
                active.join(",")
            ));
        }
        let microphone = CaptureTrackLease {
            source: CaptureTrack::Microphone.source(),
            track_id: CaptureTrack::Microphone.track_id(),
            session_id: session_id.to_string(),
            capture_epoch: next_epoch(&mut state, CaptureTrack::Microphone)?,
        };
        let system_audio = CaptureTrackLease {
            source: CaptureTrack::SystemAudio.source(),
            track_id: CaptureTrack::SystemAudio.track_id(),
            session_id: session_id.to_string(),
            capture_epoch: next_epoch(&mut state, CaptureTrack::SystemAudio)?,
        };
        state.microphone = Some(microphone.clone());
        state.system_audio = Some(system_audio.clone());
        Ok(DualTrackLeases {
            microphone,
            system_audio,
        })
    }

    pub fn release_track(&self, track: CaptureTrack, session_id: &str, capture_epoch: u64) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        let slot = lease_for_track_mut(&mut state, track);
        if slot.as_ref().is_some_and(|lease| {
            lease.session_id == session_id && lease.capture_epoch == capture_epoch
        }) {
            *slot = None;
            true
        } else {
            false
        }
    }

    pub fn claim_microphone_probe(&self) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "dual-track capture coordinator lock poisoned".to_string())?;
        if state.microphone_probe_active
            || state.microphone.is_some()
            || state.system_audio.is_some()
        {
            return Err(
                "formal audio capture or another probe is active; microphone probe is blocked"
                    .to_string(),
            );
        }
        state.microphone_probe_active = true;
        Ok(())
    }

    pub fn release_microphone_probe(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.microphone_probe_active = false;
        }
    }

    pub fn snapshot(&self) -> DualTrackCoordinatorSnapshot {
        let Ok(state) = self.state.lock() else {
            return DualTrackCoordinatorSnapshot {
                session_id: None,
                mode: "blocked",
                active_track_count: 0,
                microphone_active: false,
                system_audio_active: false,
                microphone_epoch: None,
                system_audio_epoch: None,
                microphone_probe_active: false,
                mixed_audio_created: false,
            };
        };
        let microphone_active = state.microphone.is_some();
        let system_audio_active = state.system_audio.is_some();
        let active_track_count = u8::from(microphone_active) + u8::from(system_audio_active);
        DualTrackCoordinatorSnapshot {
            session_id: state
                .microphone
                .as_ref()
                .or(state.system_audio.as_ref())
                .map(|lease| lease.session_id.clone()),
            mode: match active_track_count {
                2 => "dual_track",
                1 => "single_track",
                _ => "inactive",
            },
            active_track_count,
            microphone_active,
            system_audio_active,
            microphone_epoch: state.microphone.as_ref().map(|lease| lease.capture_epoch),
            system_audio_epoch: state.system_audio.as_ref().map(|lease| lease.capture_epoch),
            microphone_probe_active: state.microphone_probe_active,
            mixed_audio_created: false,
        }
    }
}

fn ensure_same_meeting(
    state: &DualTrackCoordinatorState,
    requested_session_id: &str,
) -> Result<(), String> {
    if let Some(active) = state.microphone.as_ref().or(state.system_audio.as_ref()) {
        if active.session_id != requested_session_id {
            return Err(format!(
                "capture session conflict: active meeting {} does not match requested meeting {}",
                active.session_id, requested_session_id
            ));
        }
    }
    Ok(())
}

fn lease_for_track(
    state: &DualTrackCoordinatorState,
    track: CaptureTrack,
) -> Option<&CaptureTrackLease> {
    match track {
        CaptureTrack::Microphone => state.microphone.as_ref(),
        CaptureTrack::SystemAudio => state.system_audio.as_ref(),
    }
}

fn lease_for_track_mut(
    state: &mut DualTrackCoordinatorState,
    track: CaptureTrack,
) -> &mut Option<CaptureTrackLease> {
    match track {
        CaptureTrack::Microphone => &mut state.microphone,
        CaptureTrack::SystemAudio => &mut state.system_audio,
    }
}

fn next_epoch(state: &mut DualTrackCoordinatorState, track: CaptureTrack) -> Result<u64, String> {
    let epoch = match track {
        CaptureTrack::Microphone => &mut state.microphone_epoch,
        CaptureTrack::SystemAudio => &mut state.system_audio_epoch,
    };
    *epoch = epoch
        .checked_add(1)
        .ok_or_else(|| format!("{} capture epoch exhausted", track.track_id()))?;
    Ok(*epoch)
}

fn validate_coordinator_session_id(session_id: &str) -> Result<(), String> {
    if session_id.is_empty()
        || session_id.len() > 128
        || !session_id
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.'))
    {
        return Err("session_id contains unsafe characters".to_string());
    }
    Ok(())
}

#[derive(Debug, Clone, Serialize)]
pub struct SystemAudioCaptureResponse {
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
    pub permission_status: &'static str,
    pub selected_display_id: Option<u32>,
    pub helper_present: bool,
    pub ready_file: Option<String>,
    pub transport_ready: bool,
    pub pcm_seen: bool,
    pub audible_pcm_seen: bool,
    pub asr_ready: bool,
    pub first_pcm_rms: Option<f32>,
    pub pcm_bytes_sent: u64,
    pub captures_audio: bool,
    pub spawns_process: bool,
    pub writes_local_metadata: bool,
    pub writes_raw_audio_files: bool,
    pub raw_audio_uploaded: bool,
    pub calls_remote_provider: bool,
    pub fallback_source: Option<&'static str>,
    pub packaged_gate_proven: bool,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SystemAudioEventsResponse {
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
    pub asr_ready: bool,
    pub events: Vec<Value>,
    pub raw_pcm_in_events: bool,
    pub raw_audio_uploaded: bool,
    pub errors: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ReadyPayload {
    schema_version: String,
    status: String,
    session_id: String,
    source: String,
    capture_framework: String,
    permission: String,
    display_id: u32,
    sample_rate_hz: u32,
    channels: u32,
    sample_format: String,
    frame_samples: u32,
    excludes_current_process_audio: bool,
    raw_audio_files_written: bool,
    remote_upload_allowed: bool,
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
    asr_ready: bool,
    first_pcm_rms: Option<f32>,
    pcm_bytes_sent: u64,
}

impl CaptureReadiness {
    fn from_ready(ready: &ReadyPayload) -> Self {
        Self {
            transport_ready: ready.transport_ready,
            pcm_seen: ready.pcm_seen,
            audible_pcm_seen: ready.audible_pcm_seen,
            asr_ready: false,
            first_pcm_rms: ready.first_pcm_rms,
            pcm_bytes_sent: ready.pcm_bytes_sent,
        }
    }
}

struct SystemAudioState {
    child: Option<Child>,
    session_id: Option<String>,
    ready_file: Option<PathBuf>,
    capture_epoch: Option<u64>,
    next_capture_epoch: u64,
    status: &'static str,
    permission_status: &'static str,
    selected_display_id: Option<u32>,
}

pub struct SystemAudioCaptureSupervisor {
    helper_path: PathBuf,
    runtime_root: PathBuf,
    log_dir: PathBuf,
    state: Mutex<SystemAudioState>,
    events: Arc<Mutex<VecDeque<Value>>>,
    readiness: Arc<Mutex<CaptureReadiness>>,
}

impl SystemAudioCaptureSupervisor {
    pub fn new(helper_path: PathBuf, runtime_root: PathBuf, log_dir: PathBuf) -> Self {
        Self {
            helper_path,
            runtime_root,
            log_dir,
            state: Mutex::new(SystemAudioState {
                child: None,
                session_id: None,
                ready_file: None,
                capture_epoch: None,
                next_capture_epoch: 0,
                status: "not_started",
                permission_status: "not_checked",
                selected_display_id: None,
            }),
            events: Arc::new(Mutex::new(VecDeque::new())),
            readiness: Arc::new(Mutex::new(CaptureReadiness::default())),
        }
    }

    pub fn prepare(&self) -> SystemAudioCaptureResponse {
        if !self.helper_path.is_file() {
            return self.error_response(
                "system_audio_adapter.prepare",
                None,
                "helper_missing",
                format!(
                    "native system audio helper is missing: {}",
                    self.helper_path.display()
                ),
            );
        }
        self.snapshot("system_audio_adapter.prepare")
    }

    pub fn start(
        &self,
        session_id: Option<String>,
        display_id: Option<u32>,
        request_permission: bool,
        backend: &BackendSupervisor,
    ) -> SystemAudioCaptureResponse {
        self.start_with_epoch(session_id, display_id, request_permission, None, backend)
    }

    pub fn start_with_epoch(
        &self,
        session_id: Option<String>,
        display_id: Option<u32>,
        request_permission: bool,
        requested_capture_epoch: Option<u64>,
        backend: &BackendSupervisor,
    ) -> SystemAudioCaptureResponse {
        let session_id = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    None,
                    "not_checked",
                    error,
                )
            }
        };
        if !self.helper_path.is_file() {
            return self.error_response(
                "system_audio_adapter.start",
                Some(session_id),
                "not_checked",
                format!(
                    "native system audio helper is missing: {}",
                    self.helper_path.display()
                ),
            );
        }
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
                    "native system audio state lock poisoned".to_string(),
                )
            }
        };
        let active_pid = state.child.as_mut().and_then(|child| {
            child
                .try_wait()
                .ok()
                .flatten()
                .is_none()
                .then(|| child.id())
        });
        if let Some(active_pid) = active_pid {
            return self.response(
                "system_audio_adapter.start",
                "conflict",
                "already_recording",
                state.permission_status,
                state.session_id.clone(),
                state.capture_epoch,
                Some(active_pid),
                state.ready_file.clone(),
                state.selected_display_id,
                false,
                vec!["only one native system audio session is supported".to_string()],
            );
        }
        if state.child.is_some() {
            state.child = None;
        }
        let capture_epoch = match requested_capture_epoch {
            Some(0) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
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
                        "system_audio_adapter.start",
                        Some(session_id),
                        "not_checked",
                        "system audio capture epoch exhausted".to_string(),
                    )
                }
            },
        };
        let connection = match system_audio_connection(backend, &session_id, capture_epoch) {
            Ok(value) => value,
            Err(error) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
                    error,
                )
            }
        };
        if let Ok(mut events) = self.events.lock() {
            events.clear();
        }
        self.reset_readiness();

        let session_root = self.runtime_root.join(&session_id);
        let ready_file = session_root.join("ready.json");
        if let Err(error) = ensure_private_directory(&session_root) {
            return self.error_response(
                "system_audio_adapter.start",
                Some(session_id),
                "not_checked",
                format!("failed to create native system audio runtime: {error}"),
            );
        }
        let _ = fs::remove_file(&ready_file);
        if let Err(error) = ensure_private_directory(&self.log_dir) {
            return self.error_response(
                "system_audio_adapter.start",
                Some(session_id),
                "not_checked",
                format!("failed to create native system audio log directory: {error}"),
            );
        }
        let stdout_path = self
            .log_dir
            .join(format!("native-system-audio-{session_id}.stdout.log"));
        let stderr_path = self
            .log_dir
            .join(format!("native-system-audio-{session_id}.stderr.log"));
        let stdout = match open_log(&stdout_path) {
            Ok(file) => file,
            Err(error) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
                    format!("failed to open native system audio stdout: {error}"),
                )
            }
        };
        let stderr = match open_log(&stderr_path) {
            Ok(file) => file,
            Err(error) => {
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
                    format!("failed to open native system audio stderr: {error}"),
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
            .arg(if request_permission {
                "--request-permission"
            } else {
                "--no-request-permission"
            });
        if let Some(display_id) = display_id {
            command.arg("--display-id").arg(display_id.to_string());
        }
        command
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
                    "system_audio_adapter.start",
                    Some(session_id),
                    "not_checked",
                    format!("failed to spawn native system audio helper: {error}"),
                )
            }
        };
        let pid = child.id();
        if let Some(stdout_pipe) = child.stdout.take() {
            let events = Arc::clone(&self.events);
            let readiness = Arc::clone(&self.readiness);
            thread::spawn(move || {
                collect_stdout_events(stdout_pipe, stdout, events, readiness, capture_epoch)
            });
        }

        let deadline = Instant::now() + READY_TIMEOUT;
        loop {
            if let Some(ready) = read_ready_file(&ready_file, &session_id, capture_epoch) {
                if let Err(error) = harden_private_file(&ready_file) {
                    stop_process_group(&mut child, STOP_TIMEOUT);
                    return self.error_response(
                        "system_audio_adapter.start",
                        Some(session_id),
                        "error",
                        format!("failed to protect native system audio state: {error}"),
                    );
                }
                state.child = Some(child);
                state.session_id = Some(session_id.clone());
                state.ready_file = Some(ready_file.clone());
                state.capture_epoch = Some(capture_epoch);
                state.status = "recording";
                state.permission_status = "authorized";
                state.selected_display_id = Some(ready.display_id);
                self.set_readiness(CaptureReadiness::from_ready(&ready));
                return self.response(
                    "system_audio_adapter.start",
                    "ok",
                    "recording",
                    "authorized",
                    Some(session_id),
                    Some(capture_epoch),
                    Some(pid),
                    Some(ready_file),
                    Some(ready.display_id),
                    true,
                    Vec::new(),
                );
            }
            if let Ok(Some(status)) = child.try_wait() {
                let (permission_status, diagnostic) = helper_exit_error(&stderr_path, status);
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    permission_status,
                    diagnostic,
                );
            }
            if Instant::now() >= deadline {
                stop_process_group(&mut child, STOP_TIMEOUT);
                let suffix = helper_stderr_tail(&stderr_path)
                    .map(|tail| format!(": {tail}"))
                    .unwrap_or_default();
                return self.error_response(
                    "system_audio_adapter.start",
                    Some(session_id),
                    "timed_out",
                    format!("native system audio helper readiness timed out{suffix}"),
                );
            }
            thread::sleep(Duration::from_millis(50));
        }
    }

    pub fn status(&self) -> SystemAudioCaptureResponse {
        self.snapshot("system_audio_adapter.status")
    }

    pub fn is_active(&self) -> bool {
        let Ok(mut state) = self.state.lock() else {
            return false;
        };
        let running = state
            .child
            .as_mut()
            .is_some_and(|child| child.try_wait().ok().flatten().is_none());
        if !running && state.child.is_some() {
            state.child = None;
            state.status = "stopped";
        }
        running
    }

    pub fn collect_events(&self, session_id: Option<String>) -> SystemAudioEventsResponse {
        let requested_session = match normalize_session_id(session_id) {
            Ok(value) => value,
            Err(error) => return self.events_error(None, error),
        };
        let state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.events_error(
                    Some(requested_session),
                    "native system audio state lock poisoned".to_string(),
                )
            }
        };
        if state.session_id.as_deref() != Some(requested_session.as_str()) {
            return self.events_error(
                Some(requested_session),
                "native system audio session does not match the active session".to_string(),
            );
        }
        let capture_epoch = state.capture_epoch;
        let status = state.status;
        let running = state.child.is_some();
        drop(state);
        let events = self
            .events
            .lock()
            .map(|mut queue| queue.drain(..).collect())
            .unwrap_or_default();
        let readiness = self.readiness_snapshot();
        SystemAudioEventsResponse {
            command_id: "system_audio_adapter.collect_events",
            command_status: "ok",
            source: "system_audio",
            track_id: "system_audio",
            session_id: Some(requested_session),
            capture_epoch,
            health_status: health_status(status, running, readiness),
            transport_ready: readiness.transport_ready,
            pcm_seen: readiness.pcm_seen,
            audible_pcm_seen: readiness.audible_pcm_seen,
            asr_ready: readiness.asr_ready,
            events,
            raw_pcm_in_events: false,
            raw_audio_uploaded: false,
            errors: Vec::new(),
        }
    }

    pub fn stop(&self) -> SystemAudioCaptureResponse {
        self.stop_for_session(None)
    }

    pub fn stop_for_session(
        &self,
        requested_session_id: Option<&str>,
    ) -> SystemAudioCaptureResponse {
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    "system_audio_adapter.stop",
                    None,
                    "unknown",
                    "native system audio state lock poisoned".to_string(),
                )
            }
        };
        if let Some(requested_session_id) = requested_session_id {
            if state.child.is_some() && state.session_id.as_deref() != Some(requested_session_id) {
                return self.response(
                    "system_audio_adapter.stop",
                    "conflict",
                    "session_mismatch",
                    state.permission_status,
                    state.session_id.clone(),
                    state.capture_epoch,
                    state.child.as_ref().map(Child::id),
                    state.ready_file.clone(),
                    state.selected_display_id,
                    false,
                    vec![
                        "requested session does not match active system audio session".to_string(),
                    ],
                );
            }
        }
        if let Some(mut child) = state.child.take() {
            stop_process_group(&mut child, STOP_TIMEOUT);
        }
        state.status = "stopped";
        self.response(
            "system_audio_adapter.stop",
            "ok",
            "stopped",
            state.permission_status,
            state.session_id.clone(),
            state.capture_epoch,
            None,
            state.ready_file.clone(),
            state.selected_display_id,
            false,
            Vec::new(),
        )
    }

    pub fn cleanup_for_session(
        &self,
        requested_session_id: Option<&str>,
    ) -> SystemAudioCaptureResponse {
        let normalized_requested = match requested_session_id {
            Some(value) => match normalize_session_id(Some(value.to_string())) {
                Ok(value) => Some(value),
                Err(error) => {
                    return self.error_response(
                        "system_audio_adapter.cleanup",
                        None,
                        "unknown",
                        error,
                    )
                }
            },
            None => None,
        };
        if let Some(requested_session_id) = normalized_requested.as_deref() {
            let state = match self.state.lock() {
                Ok(state) => state,
                Err(_) => {
                    return self.error_response(
                        "system_audio_adapter.cleanup",
                        None,
                        "unknown",
                        "native system audio state lock poisoned".to_string(),
                    )
                }
            };
            if state
                .session_id
                .as_deref()
                .is_some_and(|session_id| session_id != requested_session_id)
            {
                return self.response(
                    "system_audio_adapter.cleanup",
                    "conflict",
                    "session_mismatch",
                    state.permission_status,
                    state.session_id.clone(),
                    state.capture_epoch,
                    state.child.as_ref().map(Child::id),
                    state.ready_file.clone(),
                    state.selected_display_id,
                    state.child.is_some(),
                    vec!["requested session does not match system audio state".to_string()],
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
                    .join(format!("native-system-audio-{session_id}.stdout.log")),
                &mut errors,
            );
            remove_path_if_exists(
                &self
                    .log_dir
                    .join(format!("native-system-audio-{session_id}.stderr.log")),
                &mut errors,
            );
        }
        if let Ok(mut events) = self.events.lock() {
            events.clear();
        } else {
            errors.push("native system audio event queue lock poisoned".to_string());
        }
        if let Ok(mut state) = self.state.lock() {
            if session_id.is_none() || state.session_id == session_id {
                state.session_id = None;
                state.ready_file = None;
                state.capture_epoch = None;
                state.selected_display_id = None;
                state.status = "cleaned";
                self.reset_readiness();
            }
        } else {
            errors.push("native system audio state lock poisoned".to_string());
        }
        self.response(
            "system_audio_adapter.cleanup",
            if errors.is_empty() { "ok" } else { "blocked" },
            if errors.is_empty() {
                "cleaned"
            } else {
                "cleanup_failed"
            },
            stopped.permission_status,
            session_id,
            capture_epoch,
            None,
            None,
            None,
            false,
            errors,
        )
    }

    fn snapshot(&self, command_id: &'static str) -> SystemAudioCaptureResponse {
        let mut state = match self.state.lock() {
            Ok(value) => value,
            Err(_) => {
                return self.error_response(
                    command_id,
                    None,
                    "unknown",
                    "native system audio state lock poisoned".to_string(),
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
            state.permission_status,
            state.session_id.clone(),
            state.capture_epoch,
            state.child.as_ref().map(Child::id),
            state.ready_file.clone(),
            state.selected_display_id,
            state.child.is_some(),
            Vec::new(),
        )
    }

    fn events_error(&self, session_id: Option<String>, error: String) -> SystemAudioEventsResponse {
        SystemAudioEventsResponse {
            command_id: "system_audio_adapter.collect_events",
            command_status: "blocked",
            source: "system_audio",
            track_id: "system_audio",
            session_id,
            capture_epoch: None,
            health_status: "blocked",
            transport_ready: false,
            pcm_seen: false,
            audible_pcm_seen: false,
            asr_ready: false,
            events: Vec::new(),
            raw_pcm_in_events: false,
            raw_audio_uploaded: false,
            errors: vec![error],
        }
    }

    fn error_response(
        &self,
        command_id: &'static str,
        session_id: Option<String>,
        permission_status: &'static str,
        error: String,
    ) -> SystemAudioCaptureResponse {
        self.response_with_readiness(
            command_id,
            "blocked",
            if permission_status == "denied" {
                "permission_denied"
            } else {
                "error"
            },
            permission_status,
            session_id,
            None,
            None,
            None,
            None,
            false,
            CaptureReadiness::default(),
            vec![error],
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn response(
        &self,
        command_id: &'static str,
        command_status: &'static str,
        status: &'static str,
        permission_status: &'static str,
        session_id: Option<String>,
        capture_epoch: Option<u64>,
        pid: Option<u32>,
        ready_file: Option<PathBuf>,
        selected_display_id: Option<u32>,
        captures_audio: bool,
        errors: Vec<String>,
    ) -> SystemAudioCaptureResponse {
        self.response_with_readiness(
            command_id,
            command_status,
            status,
            permission_status,
            session_id,
            capture_epoch,
            pid,
            ready_file,
            selected_display_id,
            captures_audio,
            self.readiness_snapshot(),
            errors,
        )
    }

    #[allow(clippy::too_many_arguments)]
    fn response_with_readiness(
        &self,
        command_id: &'static str,
        command_status: &'static str,
        status: &'static str,
        permission_status: &'static str,
        session_id: Option<String>,
        capture_epoch: Option<u64>,
        pid: Option<u32>,
        ready_file: Option<PathBuf>,
        selected_display_id: Option<u32>,
        process_captures_audio: bool,
        readiness: CaptureReadiness,
        errors: Vec<String>,
    ) -> SystemAudioCaptureResponse {
        let writes_local_metadata = ready_file.is_some();
        let captures_audio = process_captures_audio
            && readiness.transport_ready
            && readiness.pcm_seen;
        SystemAudioCaptureResponse {
            command_id,
            command_status,
            implementation_status: "native_screencapturekit_system_audio_helper",
            transport_status: if readiness.transport_ready {
                "authenticated_loopback_websocket_ready"
            } else {
                "authenticated_loopback_websocket_not_ready"
            },
            side_effect_status: if captures_audio {
                "native_system_audio_streaming"
            } else {
                "none"
            },
            source: "system_audio",
            track_id: "system_audio",
            session_id,
            capture_epoch,
            pid,
            status,
            health_status: health_status(status, process_captures_audio, readiness),
            permission_status,
            selected_display_id,
            helper_present: self.helper_path.is_file(),
            ready_file: ready_file.map(|path| display_path(&path)),
            transport_ready: readiness.transport_ready,
            pcm_seen: readiness.pcm_seen,
            audible_pcm_seen: readiness.audible_pcm_seen,
            asr_ready: readiness.asr_ready,
            first_pcm_rms: readiness.first_pcm_rms,
            pcm_bytes_sent: readiness.pcm_bytes_sent,
            captures_audio,
            spawns_process: pid.is_some(),
            writes_local_metadata,
            writes_raw_audio_files: false,
            raw_audio_uploaded: false,
            calls_remote_provider: false,
            fallback_source: None,
            packaged_gate_proven: false,
            errors,
        }
    }

    fn readiness_snapshot(&self) -> CaptureReadiness {
        self.readiness
            .lock()
            .map(|value| *value)
            .unwrap_or_default()
    }

    fn set_readiness(&self, readiness: CaptureReadiness) {
        if let Ok(mut current) = self.readiness.lock() {
            *current = readiness;
        }
    }

    fn reset_readiness(&self) {
        self.set_readiness(CaptureReadiness::default());
    }
}

impl Drop for SystemAudioCaptureSupervisor {
    fn drop(&mut self) {
        let _ = self.stop();
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

fn system_audio_connection(
    backend: &BackendSupervisor,
    session_id: &str,
    capture_epoch: u64,
) -> Result<BackendWebSocketConnection, String> {
    if capture_epoch == 0 {
        return Err("system audio capture epoch must be greater than zero".to_string());
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
            "system audio transport is restricted to the authenticated packaged loopback backend"
                .to_string(),
        );
    }
    parsed
        .query_pairs_mut()
        .clear()
        .append_pair("audio_source", "tauri_system_audio")
        .append_pair("pcm_protocol", "native_pcm_v2")
        .append_pair("capture_epoch", &capture_epoch.to_string());
    connection.url = parsed.into();
    Ok(connection)
}

fn open_log(path: &Path) -> std::io::Result<File> {
    open_private_file(path, false)
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

fn collect_stdout_events(
    stdout_pipe: impl Read,
    mut log: File,
    events: Arc<Mutex<VecDeque<Value>>>,
    readiness: Arc<Mutex<CaptureReadiness>>,
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
            "content_selected"
                | "pcm"
                | "stopped"
                | "asr_starting"
                | "asr_ready"
                | "partial"
                | "final"
                | "correction"
                | "suggestion_card"
                | "end_of_stream"
                | "error"
                | "provider_error"
        ) {
            continue;
        }
        if event_type == "pcm"
            && (value.get("data").is_some()
                || value.get("bytes").is_some()
                || value.get("pcm").is_some())
        {
            continue;
        }
        let Some(object) = value.as_object_mut() else {
            continue;
        };
        if let Ok(mut current) = readiness.lock() {
            match event_type.as_str() {
                "pcm" => {
                    current.transport_ready = true;
                    current.pcm_seen = true;
                    if object
                        .get("rms")
                        .and_then(Value::as_f64)
                        .is_some_and(|rms| rms >= f64::from(AUDIBLE_RMS_THRESHOLD))
                    {
                        current.audible_pcm_seen = true;
                    }
                }
                "asr_starting" => current.asr_ready = false,
                "asr_ready" => {
                    current.asr_ready = object
                        .get("ready")
                        .and_then(Value::as_bool)
                        .unwrap_or(true);
                }
                "end_of_stream" => current.asr_ready = false,
                _ => {}
            }
        }
        track_sequence = track_sequence.saturating_add(1);
        object.insert(
            "source".to_string(),
            Value::String("system_audio".to_string()),
        );
        object.insert(
            "track_id".to_string(),
            Value::String("system_audio".to_string()),
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
                match event_type.as_str() {
                    "error" | "provider_error" => "error",
                    "asr_starting" => "starting",
                    "pcm"
                        if object
                            .get("rms")
                            .and_then(Value::as_f64)
                            .is_some_and(|rms| rms < f64::from(AUDIBLE_RMS_THRESHOLD)) =>
                    {
                        "silent"
                    }
                    _ => "healthy",
                }
                .to_string(),
            ),
        );
        object.insert("raw_audio_uploaded".to_string(), Value::Bool(false));
        if let Ok(mut queue) = events.lock() {
            queue.push_back(value);
            while queue.len() > EVENT_QUEUE_LIMIT {
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

fn health_status(
    status: &str,
    running: bool,
    readiness: CaptureReadiness,
) -> &'static str {
    if running && matches!(status, "recording" | "paused") {
        if !readiness.transport_ready || !readiness.pcm_seen {
            "starting"
        } else if !readiness.audible_pcm_seen {
            "silent"
        } else if !readiness.asr_ready {
            "asr_starting"
        } else {
            "healthy"
        }
    } else {
        match status {
            "not_started" | "stopped" | "cleaned" => "stopped",
            "permission_denied" => "permission_denied",
            "session_mismatch" => "conflict",
            "error" | "cleanup_failed" => "blocked",
            _ => "unknown",
        }
    }
}

fn read_ready_file(
    path: &Path,
    expected_session_id: &str,
    expected_capture_epoch: u64,
) -> Option<ReadyPayload> {
    let mut content = String::new();
    File::open(path).ok()?.read_to_string(&mut content).ok()?;
    let payload = serde_json::from_str::<ReadyPayload>(&content).ok()?;
    let valid = payload.schema_version == "meeting_copilot.native_system_audio_ready.v1"
        && payload.status == "ready"
        && payload.session_id == expected_session_id
        && payload.source == "system_audio"
        && payload.capture_framework == "ScreenCaptureKit"
        && payload.permission == "authorized"
        && payload.sample_rate_hz == 16_000
        && payload.channels == 1
        && payload.sample_format == "pcm_f32le"
        && payload.frame_samples == 4_800
        && payload.excludes_current_process_audio
        && !payload.raw_audio_files_written
        && !payload.remote_upload_allowed
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

fn helper_stderr_tail(path: &Path) -> Option<String> {
    let mut content = String::new();
    File::open(path).ok()?.read_to_string(&mut content).ok()?;
    content
        .lines()
        .rev()
        .find(|line| !line.trim().is_empty())
        .map(|line| line.trim().chars().take(1_000).collect())
}

fn helper_exit_error(path: &Path, status: std::process::ExitStatus) -> (&'static str, String) {
    let tail = helper_stderr_tail(path);
    let permission_status = tail
        .as_deref()
        .and_then(|line| serde_json::from_str::<Value>(line).ok())
        .and_then(|value| {
            value
                .get("error_code")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .filter(|code| code == "permission_denied")
        .map(|_| "denied")
        .unwrap_or("not_authorized");
    let message = match tail {
        Some(tail) => format!("native system audio helper exited before ready: {status}: {tail}"),
        None => format!("native system audio helper exited before ready: {status}"),
    };
    (permission_status, message)
}

#[cfg(unix)]
fn stop_process_group(child: &mut Child, timeout: Duration) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.wait();
}

#[cfg(not(unix))]
fn stop_process_group(child: &mut Child, _timeout: Duration) {
    let _ = child.kill();
    let _ = child.wait();
}

fn display_path(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[cfg(unix)]
    use std::os::unix::fs::PermissionsExt;

    fn test_root(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!(
            "meeting-copilot-system-audio-{label}-{}-{nonce}",
            std::process::id()
        ))
    }

    #[cfg(unix)]
    fn helper(root: &Path, body: &str) -> PathBuf {
        fs::create_dir_all(root).unwrap();
        let path = root.join("system-audio-helper.sh");
        fs::write(&path, format!("#!/bin/sh\nset -eu\n{body}\n")).unwrap();
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&path, permissions).unwrap();
        path
    }

    #[cfg(unix)]
    fn microphone_helper(root: &Path) -> PathBuf {
        let path = root.join("microphone-helper.sh");
        fs::write(
            &path,
            r#"#!/bin/sh
set -eu
ready=''
session=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ready-file) ready="$2"; shift 2 ;;
    --session-id) session="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$(dirname "$ready")"
printf '{"schema_version":"meeting_copilot.native_mic_ready.v1","status":"ready","session_id":"%s","source":"av_audio_engine_microphone","sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":true,"first_pcm_rms":0.125,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":1}' "$session" > "$ready"
trap 'exit 0' TERM INT
while :; do sleep 1; done
"#,
        )
        .unwrap();
        let mut permissions = fs::metadata(&path).unwrap().permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(&path, permissions).unwrap();
        path
    }

    fn backend(base_url: &str) -> BackendSupervisor {
        let backend = BackendSupervisor::default();
        backend.use_external(base_url.to_string());
        backend
    }

    #[test]
    fn session_id_and_ready_protocol_fail_closed() {
        assert!(normalize_session_id(Some("meeting_01".to_string())).is_ok());
        assert!(normalize_session_id(Some("../private".to_string())).is_err());
        let root = test_root("ready");
        fs::create_dir_all(&root).unwrap();
        let ready = root.join("ready.json");
        fs::write(
            &ready,
            r#"{"schema_version":"meeting_copilot.native_system_audio_ready.v1","status":"ready","session_id":"meeting_01","source":"system_audio","capture_framework":"ScreenCaptureKit","permission":"authorized","display_id":42,"sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"excludes_current_process_audio":true,"raw_audio_files_written":false,"remote_upload_allowed":false,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":true,"first_pcm_rms":0.125,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":7}"#,
        )
        .unwrap();
        assert_eq!(
            read_ready_file(&ready, "meeting_01", 7).unwrap().display_id,
            42
        );
        assert!(read_ready_file(&ready, "meeting_other", 7).is_none());
        assert!(read_ready_file(&ready, "meeting_01", 8).is_none());
        fs::write(
            &ready,
            r#"{"schema_version":"meeting_copilot.native_system_audio_ready.v1","status":"ready","session_id":"meeting_01","source":"system_audio","capture_framework":"ScreenCaptureKit","permission":"authorized","display_id":42,"sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"excludes_current_process_audio":true,"raw_audio_files_written":false,"remote_upload_allowed":false}"#,
        )
        .unwrap();
        assert!(read_ready_file(&ready, "meeting_01", 7).is_none());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn connection_is_rewritten_to_named_loopback_system_audio_source() {
        let connection = system_audio_connection(
            &backend("http://127.0.0.1:8765"),
            "meeting_01",
            7,
        )
        .unwrap();
        assert_eq!(
            connection.url,
            "ws://127.0.0.1:8765/live/asr/stream/ws/meeting_01?audio_source=tauri_system_audio&pcm_protocol=native_pcm_v2&capture_epoch=7"
        );
        assert!(system_audio_connection(&backend("https://example.com"), "meeting_01", 7).is_err());
        assert!(system_audio_connection(&backend("http://127.0.0.1:8765"), "meeting_01", 0).is_err());
    }

    #[test]
    fn health_status_keeps_transport_pcm_audibility_and_asr_distinct() {
        assert_eq!(
            health_status("recording", true, CaptureReadiness::default()),
            "starting"
        );
        let silent = CaptureReadiness {
            transport_ready: true,
            pcm_seen: true,
            ..CaptureReadiness::default()
        };
        assert_eq!(health_status("recording", true, silent), "silent");
        let audible = CaptureReadiness {
            audible_pcm_seen: true,
            ..silent
        };
        assert_eq!(health_status("recording", true, audible), "asr_starting");
        assert_eq!(
            health_status(
                "recording",
                true,
                CaptureReadiness {
                    asr_ready: true,
                    ..audible
                },
            ),
            "healthy"
        );
    }

    #[cfg(unix)]
    #[test]
    fn permission_denial_is_explicit_and_never_reports_capture_or_fallback() {
        let root = test_root("permission");
        let helper = helper(
            &root,
            r#"printf '%s\n' '{"schema_version":"meeting_copilot.native_system_audio_error.v1","error_code":"permission_denied","captured_audio":false,"fallback_source":null}' >&2
exit 77"#,
        );
        let supervisor =
            SystemAudioCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));
        let response = supervisor.start(
            Some("meeting_permission".to_string()),
            None,
            true,
            &backend("http://127.0.0.1:8765"),
        );
        assert_eq!(response.command_status, "blocked");
        assert_eq!(response.status, "permission_denied");
        assert_eq!(response.permission_status, "denied");
        assert!(!response.captures_audio);
        assert!(!response.raw_audio_uploaded);
        assert_eq!(response.fallback_source, None);
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn lifecycle_selects_content_collects_pcm_metadata_and_stops_the_process_group() {
        let root = test_root("lifecycle");
        let helper = helper(
            &root,
            r#"ws=''
session=''
ready=''
display='42'
permission=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ws-url) ws="$2"; shift 2 ;;
    --session-id) session="$2"; shift 2 ;;
    --ready-file) ready="$2"; shift 2 ;;
    --display-id) display="$2"; shift 2 ;;
    --request-permission|--no-request-permission) permission="$1"; shift ;;
    *) exit 64 ;;
  esac
done
case "$ws" in
  'ws://127.0.0.1:8765/live/asr/stream/ws/'*'?audio_source=tauri_system_audio&pcm_protocol=native_pcm_v2&capture_epoch='*) ;;
  *) exit 78 ;;
esac
[ "$permission" = '--no-request-permission' ] || exit 64
mkdir -p "$(dirname "$ready")"
printf '{"schema_version":"meeting_copilot.native_system_audio_ready.v1","status":"ready","session_id":"%s","source":"system_audio","capture_framework":"ScreenCaptureKit","permission":"authorized","display_id":%s,"sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"excludes_current_process_audio":true,"raw_audio_files_written":false,"remote_upload_allowed":false,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":true,"first_pcm_rms":0.125,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":1}' "$session" "$display" > "$ready"
printf '%s\n' '{"event_type":"content_selected","source":"system_audio","display_id":42}'
printf '%s\n' '{"event_type":"pcm","source":"system_audio","sequence":1,"frame_samples":4800,"raw_pcm_in_event":false}'
printf '%s\n' '{"event_type":"partial","text":"正在讨论系统音频"}'
trap 'exit 0' TERM INT
while :; do sleep 1; done"#,
        );
        let supervisor =
            SystemAudioCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));
        let response = supervisor.start(
            Some("meeting_lifecycle".to_string()),
            Some(42),
            false,
            &backend("http://127.0.0.1:8765"),
        );
        assert_eq!(response.command_status, "ok");
        assert_eq!(response.status, "recording");
        assert_eq!(response.source, "system_audio");
        assert_eq!(response.track_id, "system_audio");
        assert_eq!(response.capture_epoch, Some(1));
        assert_eq!(response.health_status, "asr_starting");
        assert_eq!(response.selected_display_id, Some(42));
        assert!(response.transport_ready);
        assert!(response.pcm_seen);
        assert!(response.audible_pcm_seen);
        assert!(!response.asr_ready);
        assert_eq!(response.first_pcm_rms, Some(0.125));
        assert_eq!(response.pcm_bytes_sent, 19_200);
        assert!(response.captures_audio);
        assert!(!response.writes_raw_audio_files);
        assert!(!response.raw_audio_uploaded);
        assert!(supervisor.is_active());
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
                root.join("logs/native-system-audio-meeting_lifecycle.stdout.log"),
                root.join("logs/native-system-audio-meeting_lifecycle.stderr.log"),
            ] {
                assert_eq!(
                    fs::metadata(path).unwrap().permissions().mode() & 0o777,
                    0o600
                );
            }
        }

        let deadline = Instant::now() + Duration::from_secs(1);
        let mut collected = Vec::new();
        while Instant::now() < deadline && collected.len() < 3 {
            collected.extend(
                supervisor
                    .collect_events(Some("meeting_lifecycle".to_string()))
                    .events,
            );
            thread::sleep(Duration::from_millis(20));
        }
        assert!(collected
            .iter()
            .any(|event| event["event_type"] == "content_selected"));
        assert!(collected.iter().any(|event| event["event_type"] == "pcm"));
        assert!(collected
            .iter()
            .any(|event| event["event_type"] == "partial"));
        for (index, event) in collected.iter().enumerate() {
            assert_eq!(event["source"], "system_audio");
            assert_eq!(event["track_id"], "system_audio");
            assert_eq!(event["capture_epoch"], 1);
            assert_eq!(event["track_sequence"], (index + 1) as u64);
            assert!(event["observed_at_unix_ms"].as_u64().unwrap() > 0);
            assert_eq!(event["health_status"], "healthy");
            assert_eq!(event["raw_audio_uploaded"], false);
        }

        let mismatch = supervisor.stop_for_session(Some("meeting_other"));
        assert_eq!(mismatch.command_status, "conflict");
        assert!(supervisor.is_active());
        let stopped = supervisor.stop_for_session(Some("meeting_lifecycle"));
        assert_eq!(stopped.status, "stopped");
        assert_eq!(stopped.capture_epoch, response.capture_epoch);
        assert_eq!(stopped.health_status, "stopped");
        assert!(!supervisor.is_active());
        let cleanup_mismatch = supervisor.cleanup_for_session(Some("meeting_other"));
        assert_eq!(cleanup_mismatch.command_status, "conflict");
        assert!(root.join("runtime/meeting_lifecycle").exists());
        let cleaned = supervisor.cleanup_for_session(Some("meeting_lifecycle"));
        assert_eq!(cleaned.command_status, "ok");
        assert_eq!(cleaned.status, "cleaned");
        assert_eq!(cleaned.capture_epoch, response.capture_epoch);
        assert!(!root.join("runtime/meeting_lifecycle").exists());
        assert_eq!(supervisor.status().status, "cleaned");
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn same_meeting_runs_two_helpers_and_stops_each_track_independently() {
        let root = test_root("dual-lifecycle");
        fs::create_dir_all(&root).unwrap();
        let microphone = crate::native_mic_capture_runtime::NativeMicCaptureSupervisor::new(
            microphone_helper(&root),
            root.join("mic-runtime"),
            root.join("logs"),
        );
        let system_audio = SystemAudioCaptureSupervisor::new(
            helper(
                &root,
                r#"ready=''
session=''
display='42'
while [ "$#" -gt 0 ]; do
  case "$1" in
    --ready-file) ready="$2"; shift 2 ;;
    --session-id) session="$2"; shift 2 ;;
    --display-id) display="$2"; shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$(dirname "$ready")"
printf '{"schema_version":"meeting_copilot.native_system_audio_ready.v1","status":"ready","session_id":"%s","source":"system_audio","capture_framework":"ScreenCaptureKit","permission":"authorized","display_id":%s,"sample_rate_hz":16000,"channels":1,"sample_format":"pcm_f32le","frame_samples":4800,"excludes_current_process_audio":true,"raw_audio_files_written":false,"remote_upload_allowed":false,"transport_ready":true,"pcm_seen":true,"audible_pcm_seen":true,"first_pcm_rms":0.125,"pcm_bytes_sent":19200,"pcm_protocol":"native_pcm_v2","capture_epoch":1}' "$session" "$display" > "$ready"
trap 'exit 0' TERM INT
while :; do sleep 1; done"#,
            ),
            root.join("system-runtime"),
            root.join("logs"),
        );
        let backend = backend("http://127.0.0.1:8765");
        let coordinator = DualTrackCaptureCoordinator::default();
        let leases = coordinator.claim_dual_track("meeting_dual").unwrap();

        let microphone_started = microphone.start_with_epoch(
            Some("meeting_dual".to_string()),
            Some(leases.microphone.capture_epoch),
            &backend,
        );
        let system_audio_started = system_audio.start_with_epoch(
            Some("meeting_dual".to_string()),
            Some(42),
            false,
            Some(leases.system_audio.capture_epoch),
            &backend,
        );

        assert_eq!(microphone_started.command_status, "ok");
        assert_eq!(system_audio_started.command_status, "ok");
        assert!(microphone.status().captures_audio);
        assert!(system_audio.status().captures_audio);
        assert_eq!(coordinator.snapshot().mode, "dual_track");
        assert!(!coordinator.snapshot().mixed_audio_created);

        let microphone_stopped = microphone.stop_for_session(Some("meeting_dual"));
        assert!(coordinator.release_track(
            CaptureTrack::Microphone,
            "meeting_dual",
            microphone_stopped.capture_epoch.unwrap(),
        ));
        assert_eq!(coordinator.snapshot().mode, "single_track");
        assert!(!microphone.status().captures_audio);
        assert!(system_audio.status().captures_audio);

        let microphone_cleaned = microphone.cleanup_for_session(Some("meeting_dual"));
        assert_eq!(microphone_cleaned.status, "cleaned");
        let system_audio_stopped = system_audio.stop_for_session(Some("meeting_dual"));
        assert!(coordinator.release_track(
            CaptureTrack::SystemAudio,
            "meeting_dual",
            system_audio_stopped.capture_epoch.unwrap(),
        ));
        let system_audio_cleaned = system_audio.cleanup_for_session(Some("meeting_dual"));
        assert_eq!(system_audio_cleaned.status, "cleaned");
        assert_eq!(coordinator.snapshot().active_track_count, 0);
        let _ = fs::remove_dir_all(root);
    }

    #[cfg(unix)]
    #[test]
    fn remote_backend_is_blocked_before_the_helper_is_spawned() {
        let root = test_root("remote");
        let marker = root.join("spawned");
        let helper = helper(&root, &format!("touch '{}'", marker.display()));
        let supervisor =
            SystemAudioCaptureSupervisor::new(helper, root.join("runtime"), root.join("logs"));
        let response = supervisor.start(
            Some("meeting_remote".to_string()),
            None,
            false,
            &backend("https://example.com"),
        );
        assert_eq!(response.command_status, "blocked");
        assert!(!response.spawns_process);
        assert!(!marker.exists());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn coordinator_allows_explicit_same_meeting_dual_track_without_mixed_source() {
        let coordinator = DualTrackCaptureCoordinator::default();

        let microphone = coordinator
            .claim_track(CaptureTrack::Microphone, "meeting_01")
            .unwrap();
        let system_audio = coordinator
            .claim_track(CaptureTrack::SystemAudio, "meeting_01")
            .unwrap();
        let snapshot = coordinator.snapshot();

        assert_eq!(microphone.track_id, "microphone");
        assert_eq!(system_audio.track_id, "system_audio");
        assert_eq!(snapshot.session_id.as_deref(), Some("meeting_01"));
        assert_eq!(snapshot.active_track_count, 2);
        assert_eq!(snapshot.mode, "dual_track");
        assert!(!snapshot.mixed_audio_created);
        assert!(snapshot.microphone_active);
        assert!(snapshot.system_audio_active);
    }

    #[test]
    fn coordinator_blocks_cross_meeting_capture_and_hidden_mixing() {
        let coordinator = DualTrackCaptureCoordinator::default();
        coordinator
            .claim_track(CaptureTrack::Microphone, "meeting_01")
            .unwrap();

        let conflict = coordinator
            .claim_track(CaptureTrack::SystemAudio, "meeting_02")
            .unwrap_err();

        assert!(conflict.contains("meeting_01"));
        assert!(conflict.contains("meeting_02"));
        assert!(!coordinator.snapshot().system_audio_active);
    }

    #[test]
    fn coordinator_releases_tracks_independently_and_never_claims_dual_after_partial_failure() {
        let coordinator = DualTrackCaptureCoordinator::default();
        let leases = coordinator.claim_dual_track("meeting_01").unwrap();
        assert_ne!(leases.microphone.capture_epoch, 0);
        assert_ne!(leases.system_audio.capture_epoch, 0);

        assert!(coordinator.release_track(
            CaptureTrack::Microphone,
            "meeting_01",
            leases.microphone.capture_epoch,
        ));
        let partial = coordinator.snapshot();
        assert_eq!(partial.active_track_count, 1);
        assert!(!partial.microphone_active);
        assert!(partial.system_audio_active);
        assert_eq!(partial.mode, "single_track");

        assert!(coordinator
            .claim_dual_track("meeting_01")
            .unwrap_err()
            .contains("system_audio"));
    }

    #[test]
    fn coordinator_probe_is_fail_closed_while_either_formal_track_is_active() {
        let coordinator = DualTrackCaptureCoordinator::default();
        let lease = coordinator
            .claim_track(CaptureTrack::SystemAudio, "meeting_01")
            .unwrap();
        assert!(coordinator.claim_microphone_probe().is_err());
        assert!(coordinator.release_track(
            CaptureTrack::SystemAudio,
            "meeting_01",
            lease.capture_epoch,
        ));
        coordinator.claim_microphone_probe().unwrap();
        assert!(coordinator
            .claim_track(CaptureTrack::Microphone, "meeting_01")
            .is_err());
        coordinator.release_microphone_probe();
        assert!(coordinator
            .claim_track(CaptureTrack::Microphone, "meeting_01")
            .is_ok());
    }
}

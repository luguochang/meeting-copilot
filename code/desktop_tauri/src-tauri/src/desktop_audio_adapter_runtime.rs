use crate::desktop_backend_supervisor::BackendSupervisor;
#[cfg(not(windows))]
use crate::native_mic_capture_runtime::NativeMicCaptureSupervisor;
use crate::native_mic_capture_runtime::{
    NativeMicCaptureResponse, NativeMicEventsResponse, NativeMicProbeResponse,
};
#[cfg(not(windows))]
use crate::native_system_audio_capture_runtime::SystemAudioCaptureSupervisor;
use crate::native_system_audio_capture_runtime::{
    SystemAudioCaptureResponse, SystemAudioEventsResponse,
};
use std::path::PathBuf;
#[cfg(windows)]
use std::time::Duration;

#[cfg(windows)]
use crate::windows_audio_capture_runtime::{
    probe_device, AudioFlow, WindowsAudioCaptureSupervisor, WindowsCaptureSnapshot,
};

pub struct DesktopAudioCaptureAdapters {
    #[cfg(not(windows))]
    native_microphone: NativeMicCaptureSupervisor,
    #[cfg(not(windows))]
    native_system_audio: SystemAudioCaptureSupervisor,
    #[cfg(windows)]
    windows: WindowsAudioCaptureSupervisor,
}

impl DesktopAudioCaptureAdapters {
    pub fn new(
        microphone_helper: PathBuf,
        microphone_runtime_root: PathBuf,
        system_audio_helper: PathBuf,
        system_audio_runtime_root: PathBuf,
        log_dir: PathBuf,
    ) -> Self {
        #[cfg(windows)]
        let windows_spool_root = microphone_runtime_root.join("spool");
        #[cfg(windows)]
        let _ = (
            microphone_helper,
            system_audio_helper,
            system_audio_runtime_root,
            log_dir,
        );
        Self {
            #[cfg(not(windows))]
            native_microphone: NativeMicCaptureSupervisor::new(
                microphone_helper,
                microphone_runtime_root,
                log_dir.clone(),
            ),
            #[cfg(not(windows))]
            native_system_audio: SystemAudioCaptureSupervisor::new(
                system_audio_helper,
                system_audio_runtime_root,
                log_dir,
            ),
            #[cfg(windows)]
            windows: WindowsAudioCaptureSupervisor::new(windows_spool_root),
        }
    }

    pub fn microphone_prepare(&self) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.prepare",
                self.windows.status(AudioFlow::Microphone),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.prepare()
    }

    pub fn microphone_probe(&self, endpoint_id: Option<&str>) -> NativeMicProbeResponse {
        #[cfg(windows)]
        {
            return match probe_device(
                AudioFlow::Microphone,
                endpoint_id,
                Duration::from_millis(2_500),
            ) {
                Ok(probe) => NativeMicProbeResponse {
                    command_id: "mic_adapter.probe",
                    command_status: "ok",
                    probe_status: if probe.audible_pcm_seen {
                        "audible"
                    } else {
                        "silent"
                    },
                    helper_present: true,
                    sampled: probe.sample_count > 0,
                    rms: probe.rms as f64,
                    peak_rms: probe.rms as f64,
                    level: (probe.rms as f64 / 0.05).clamp(0.0, 1.0),
                    duration_ms: probe.duration_ms,
                    captures_audio: probe.sample_count > 0,
                    spawns_process: false,
                    calls_remote_provider: false,
                    writes_local_files: false,
                    creates_meeting_assets: false,
                    starts_asr: false,
                    errors: Vec::new(),
                },
                Err(error) => NativeMicProbeResponse {
                    command_id: "mic_adapter.probe",
                    command_status: "error",
                    probe_status: if error.to_ascii_lowercase().contains("denied") {
                        "permission_denied"
                    } else {
                        "device_unavailable"
                    },
                    helper_present: true,
                    sampled: false,
                    rms: 0.0,
                    peak_rms: 0.0,
                    level: 0.0,
                    duration_ms: 0,
                    captures_audio: false,
                    spawns_process: false,
                    calls_remote_provider: false,
                    writes_local_files: false,
                    creates_meeting_assets: false,
                    starts_asr: false,
                    errors: vec![error],
                },
            };
        }
        #[cfg(not(windows))]
        {
            let _ = endpoint_id;
            self.native_microphone.probe()
        }
    }

    pub fn microphone_start(
        &self,
        session_id: Option<String>,
        capture_epoch: Option<u64>,
        endpoint_id: Option<String>,
        backend: &BackendSupervisor,
    ) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.start",
                self.windows.start(
                    AudioFlow::Microphone,
                    session_id,
                    capture_epoch,
                    endpoint_id,
                    backend,
                ),
            );
        }
        #[cfg(not(windows))]
        {
            let _ = endpoint_id;
            self.native_microphone
                .start_with_epoch(session_id, capture_epoch, backend)
        }
    }

    pub fn microphone_status(&self) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.status",
                self.windows.status(AudioFlow::Microphone),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.status()
    }

    pub fn microphone_events(&self, session_id: Option<String>) -> NativeMicEventsResponse {
        #[cfg(windows)]
        {
            return windows_microphone_events(
                self.windows
                    .collect_events(AudioFlow::Microphone, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.collect_events(session_id)
    }

    pub fn microphone_pause(&self) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.pause",
                self.windows.pause(AudioFlow::Microphone),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.pause()
    }

    pub fn microphone_resume(&self) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.resume",
                self.windows.resume(AudioFlow::Microphone),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.resume()
    }

    pub fn microphone_stop(&self, session_id: Option<&str>) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.stop",
                self.windows.stop(AudioFlow::Microphone, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.stop_for_session(session_id)
    }

    pub fn microphone_cleanup(&self, session_id: Option<&str>) -> NativeMicCaptureResponse {
        #[cfg(windows)]
        {
            return windows_microphone_response(
                "mic_adapter.cleanup",
                self.windows.cleanup(AudioFlow::Microphone, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_microphone.cleanup_for_session(session_id)
    }

    pub fn system_audio_prepare(&self) -> SystemAudioCaptureResponse {
        #[cfg(windows)]
        {
            return windows_system_audio_response(
                "system_audio_adapter.prepare",
                self.windows.status(AudioFlow::RenderLoopback),
            );
        }
        #[cfg(not(windows))]
        self.native_system_audio.prepare()
    }

    pub fn system_audio_start(
        &self,
        session_id: Option<String>,
        display_id: Option<u32>,
        request_permission: bool,
        capture_epoch: Option<u64>,
        endpoint_id: Option<String>,
        backend: &BackendSupervisor,
    ) -> SystemAudioCaptureResponse {
        #[cfg(windows)]
        {
            let _ = (display_id, request_permission);
            return windows_system_audio_response(
                "system_audio_adapter.start",
                self.windows.start(
                    AudioFlow::RenderLoopback,
                    session_id,
                    capture_epoch,
                    endpoint_id,
                    backend,
                ),
            );
        }
        #[cfg(not(windows))]
        {
            let _ = endpoint_id;
            self.native_system_audio.start_with_epoch(
                session_id,
                display_id,
                request_permission,
                capture_epoch,
                backend,
            )
        }
    }

    pub fn system_audio_status(&self) -> SystemAudioCaptureResponse {
        #[cfg(windows)]
        {
            return windows_system_audio_response(
                "system_audio_adapter.status",
                self.windows.status(AudioFlow::RenderLoopback),
            );
        }
        #[cfg(not(windows))]
        self.native_system_audio.status()
    }

    pub fn system_audio_is_active(&self) -> bool {
        matches!(self.system_audio_status().status, "recording" | "paused")
    }

    pub fn system_audio_events(&self, session_id: Option<String>) -> SystemAudioEventsResponse {
        #[cfg(windows)]
        {
            return windows_system_audio_events(
                self.windows
                    .collect_events(AudioFlow::RenderLoopback, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_system_audio.collect_events(session_id)
    }

    pub fn system_audio_stop(&self, session_id: Option<&str>) -> SystemAudioCaptureResponse {
        #[cfg(windows)]
        {
            return windows_system_audio_response(
                "system_audio_adapter.stop",
                self.windows.stop(AudioFlow::RenderLoopback, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_system_audio.stop_for_session(session_id)
    }

    pub fn system_audio_cleanup(&self, session_id: Option<&str>) -> SystemAudioCaptureResponse {
        #[cfg(windows)]
        {
            return windows_system_audio_response(
                "system_audio_adapter.cleanup",
                self.windows.cleanup(AudioFlow::RenderLoopback, session_id),
            );
        }
        #[cfg(not(windows))]
        self.native_system_audio.cleanup_for_session(session_id)
    }
}

#[cfg(windows)]
fn windows_command_status(snapshot: &WindowsCaptureSnapshot) -> &'static str {
    if snapshot.status == "already_recording" || snapshot.status == "conflict" {
        "conflict"
    } else if snapshot.errors.is_empty() {
        "ok"
    } else {
        "error"
    }
}

#[cfg(windows)]
fn windows_microphone_response(
    command_id: &'static str,
    snapshot: WindowsCaptureSnapshot,
) -> NativeMicCaptureResponse {
    let captures_audio =
        matches!(snapshot.status, "recording" | "paused") && snapshot.readiness.transport_ready;
    NativeMicCaptureResponse {
        command_id,
        command_status: windows_command_status(&snapshot),
        implementation_status: "native_wasapi_microphone",
        transport_status: if snapshot.readiness.transport_ready {
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
        session_id: snapshot.session_id,
        capture_epoch: snapshot.capture_epoch,
        pid: None,
        status: snapshot.status,
        health_status: snapshot.health_status,
        helper_present: true,
        ready_file: None,
        transport_ready: snapshot.readiness.transport_ready,
        pcm_seen: snapshot.readiness.pcm_seen,
        audible_pcm_seen: snapshot.readiness.audible_pcm_seen,
        first_pcm_rms: snapshot.readiness.first_pcm_rms,
        pcm_bytes_sent: snapshot.readiness.pcm_bytes_sent,
        pcm_protocol: snapshot.readiness.pcm_seen.then_some("native_pcm_v2"),
        safe_to_execute_real_action: snapshot.errors.is_empty(),
        captures_audio,
        spawns_process: false,
        calls_remote_provider: false,
        writes_local_files: snapshot.writes_raw_audio_files,
        writes_raw_audio_files: snapshot.writes_raw_audio_files,
        raw_audio_uploaded: false,
        errors: snapshot.errors,
    }
}

#[cfg(windows)]
fn windows_microphone_events(snapshot: WindowsCaptureSnapshot) -> NativeMicEventsResponse {
    NativeMicEventsResponse {
        command_id: "mic_adapter.collect_events",
        command_status: windows_command_status(&snapshot),
        source: "microphone",
        track_id: "microphone",
        session_id: snapshot.session_id,
        capture_epoch: snapshot.capture_epoch,
        health_status: snapshot.health_status,
        transport_ready: snapshot.readiness.transport_ready,
        pcm_seen: snapshot.readiness.pcm_seen,
        audible_pcm_seen: snapshot.readiness.audible_pcm_seen,
        pcm_protocol: snapshot.readiness.pcm_seen.then_some("native_pcm_v2"),
        events: snapshot.events,
        raw_audio_uploaded: false,
        errors: snapshot.errors,
    }
}

#[cfg(windows)]
fn windows_system_audio_response(
    command_id: &'static str,
    snapshot: WindowsCaptureSnapshot,
) -> SystemAudioCaptureResponse {
    let captures_audio = snapshot.status == "recording" && snapshot.readiness.transport_ready;
    let permission_status = if snapshot.errors.iter().any(|error| {
        let normalized = error.to_ascii_lowercase();
        normalized.contains("denied") || normalized.contains("access")
    }) {
        "permission_denied"
    } else if captures_audio {
        "authorized"
    } else {
        "not_checked"
    };
    SystemAudioCaptureResponse {
        command_id,
        command_status: windows_command_status(&snapshot),
        implementation_status: "native_wasapi_loopback",
        transport_status: if snapshot.readiness.transport_ready {
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
        session_id: snapshot.session_id,
        capture_epoch: snapshot.capture_epoch,
        pid: None,
        status: snapshot.status,
        health_status: snapshot.health_status,
        permission_status,
        selected_display_id: None,
        helper_present: true,
        ready_file: None,
        transport_ready: snapshot.readiness.transport_ready,
        pcm_seen: snapshot.readiness.pcm_seen,
        audible_pcm_seen: snapshot.readiness.audible_pcm_seen,
        asr_ready: snapshot.readiness.pcm_seen,
        first_pcm_rms: snapshot.readiness.first_pcm_rms,
        pcm_bytes_sent: snapshot.readiness.pcm_bytes_sent,
        captures_audio,
        spawns_process: false,
        writes_local_metadata: snapshot.writes_raw_audio_files,
        writes_raw_audio_files: snapshot.writes_raw_audio_files,
        raw_audio_uploaded: false,
        calls_remote_provider: false,
        fallback_source: None,
        packaged_gate_proven: false,
        errors: snapshot.errors,
    }
}

#[cfg(windows)]
fn windows_system_audio_events(snapshot: WindowsCaptureSnapshot) -> SystemAudioEventsResponse {
    SystemAudioEventsResponse {
        command_id: "system_audio_adapter.collect_events",
        command_status: windows_command_status(&snapshot),
        source: "system_audio",
        track_id: "system_audio",
        session_id: snapshot.session_id,
        capture_epoch: snapshot.capture_epoch,
        health_status: snapshot.health_status,
        transport_ready: snapshot.readiness.transport_ready,
        pcm_seen: snapshot.readiness.pcm_seen,
        audible_pcm_seen: snapshot.readiness.audible_pcm_seen,
        asr_ready: snapshot.readiness.pcm_seen,
        events: snapshot.events,
        raw_pcm_in_events: false,
        raw_audio_uploaded: false,
        errors: snapshot.errors,
    }
}

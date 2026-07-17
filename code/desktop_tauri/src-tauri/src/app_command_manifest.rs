pub const APP_COMMAND_NAMES: &[&str] = &[
    "runtime_get_status",
    "runtime_write_frontend_probe",
    "session_prepare",
    "asr_worker_prepare",
    "asr_worker_start",
    "asr_worker_health",
    "asr_worker_collect_events",
    "asr_worker_stop",
    "asr_worker_cleanup",
    "mic_adapter_prepare",
    "mic_adapter_status",
    "mic_adapter_start",
    "mic_adapter_pause",
    "mic_adapter_resume",
    "mic_adapter_stop",
    "mic_adapter_delete_audio_chunks",
    "provider_config_status",
    "provider_config_save",
    "provider_config_clear",
];

#[allow(dead_code)]
pub fn permission_name(command: &str) -> String {
    format!("allow-{}", command.replace('_', "-"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_command_permissions_use_tauri_slug_format() {
        assert_eq!(
            permission_name("mic_adapter_prepare"),
            "allow-mic-adapter-prepare"
        );
        assert_eq!(
            permission_name("provider_config_status"),
            "allow-provider-config-status"
        );
    }
}

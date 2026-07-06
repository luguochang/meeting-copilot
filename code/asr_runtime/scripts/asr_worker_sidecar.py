#!/usr/bin/env python3
"""No-execution desktop ASR worker sidecar skeleton.

This module defines the future worker boundary only. It intentionally avoids
process spawning, audio capture, model imports, event-file IO, and network IO.
"""

from __future__ import annotations


PCWEB_ID = "PCWEB-102"
MODULE_NAME = "asr_worker_sidecar"
SKELETON_MODE = "module_boundary_only"
EXECUTION_MODE = "no_execution"
WORKER_SKELETON_STATUS = "specified_not_executable"
WORKER_EXECUTION_STATUS = "not_executed"
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
ALLOWED_PROVIDER_MODES_NOW = ("mock_streaming", "sherpa_onnx_streaming")
FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL = ("funasr_streaming",)
FORBIDDEN_PROVIDER_MODES = ("remote_asr", "remote_llm_asr")
ALLOWED_SOURCE_KINDS_NOW = ("synthetic",)
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
COMMAND_CATALOG = (
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
)
MODULE_BOUNDARIES = (
    "worker_identity_preview",
    "command_envelope_intake_preview",
    "lifecycle_state_preview",
    "event_writer_preview_contract",
    "provider_adapter_preview_contract",
    "health_status_preview",
    "cleanup_plan_preview",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_execute_worker_now",
    "safe_to_spawn_process_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_run_model" + "scope_now",
    "safe_to_import_model_now",
    "safe_to_execute_provider_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
)
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _provider_mode_errors(provider_mode: object) -> list[str]:
    if not isinstance(provider_mode, str) or not provider_mode.strip():
        return ["provider_mode must be a non-empty string"]
    if provider_mode in ALLOWED_PROVIDER_MODES_NOW:
        return []
    if provider_mode in FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL:
        return [f"provider_mode requires later approval: {provider_mode}"]
    if provider_mode in FORBIDDEN_PROVIDER_MODES:
        return [f"provider_mode is forbidden: {provider_mode}"]
    return [f"provider_mode is unsupported: {provider_mode}"]


def _source_kind_errors(source_kind: object) -> list[str]:
    if not isinstance(source_kind, str) or not source_kind.strip():
        return ["source_kind must be a non-empty string"]
    if source_kind in ALLOWED_SOURCE_KINDS_NOW:
        return []
    if source_kind in FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL:
        return [f"source_kind requires later approval: {source_kind}"]
    return [f"source_kind is unsupported: {source_kind}"]


def _provider_adapter_preview(provider_mode: object) -> dict[str, object]:
    return {
        "provider_mode": provider_mode if isinstance(provider_mode, str) else None,
        "provider_adapter_status": "preview_only_not_imported",
        "provider_execution_status": "not_executed",
        "model_import_status": "not_imported",
    }


def _source_preview(source_kind: object) -> dict[str, object]:
    return {
        "source_kind": source_kind if isinstance(source_kind, str) else None,
        "source_status": "preview_only_no_audio_read",
        "audio_capture_status": "not_started",
        "user_audio_read_status": "not_read",
    }


def build_no_execution_worker_skeleton_report(
    *,
    provider_mode: str = "mock_streaming",
    source_kind: str = "synthetic",
    event_output_root: str = APPROVED_EVENT_OUTPUT_ROOT,
    runtime_root: str = APPROVED_RUNTIME_ROOT,
    extra_validation_errors: list[str] | None = None,
) -> dict[str, object]:
    validation_errors = [
        *(_provider_mode_errors(provider_mode)),
        *(_source_kind_errors(source_kind)),
        *(extra_validation_errors or []),
    ]
    validation_status = "failed" if validation_errors else "passed"
    next_action = (
        "fix_skeleton_preview_config"
        if validation_errors
        else "bind_skeleton_to_desktop_command_runner_after_explicit_approval"
    )
    return {
        "pcweb_id": PCWEB_ID,
        "module_name": MODULE_NAME,
        "skeleton_mode": SKELETON_MODE,
        "execution_mode": EXECUTION_MODE,
        "worker_skeleton_status": WORKER_SKELETON_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "implementation_status": "skeleton_module_boundary_only",
        "validation_status": validation_status,
        "validation_errors": validation_errors,
        "next_action": next_action,
        "worker_identity_preview": {
            "worker_kind": "desktop_asr_sidecar",
            "worker_instance_status": "not_created",
            "session_binding_status": "not_bound",
        },
        "command_envelope_intake_preview": {
            "command_catalog": list(COMMAND_CATALOG),
            "intake_status": "specified_not_bound",
            "command_execution_status": "not_executed",
        },
        "lifecycle_state_preview": {
            "current_state": "not_prepared",
            "allowed_future_states": [
                "not_prepared",
                "prepared",
                "running",
                "stopped",
                "cleaned",
            ],
            "transition_execution_status": "not_executed",
        },
        "event_writer_preview": {
            "event_output_root": event_output_root,
            "event_writer_status": "preview_only_not_bound",
            "write_status": "not_written",
            "read_status": "not_read",
        },
        "runtime_audio_preview": {
            "runtime_root": runtime_root,
            "runtime_audio_status": "not_bound",
            "write_status": "not_written",
            "delete_status": "not_executed",
        },
        "provider_adapter_preview": _provider_adapter_preview(provider_mode),
        "source_preview": _source_preview(source_kind),
        "health_status_preview": {
            "worker_health": "not_started",
            "heartbeat_status": "not_scheduled",
            "resource_probe_status": "not_executed",
        },
        "cleanup_plan_preview": {
            "cleanup_scope": [
                "runtime_temp_dir",
                "event_output_preview",
                "provider_adapter_preview",
            ],
            "cleanup_execution_status": "not_executed",
        },
        **false_safety_flags(),
    }

#!/usr/bin/env python3
"""Build the PCWEB-091 Tauri no-op shell local smoke readiness report."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
TAURI_ROOT = DESKTOP_ROOT / "src-tauri"
DEFAULT_POLICY_PATH = DESKTOP_ROOT / "tauri-noop-shell-run-smoke.policy.json"
DEFAULT_TAURI_CONFIG_PATH = TAURI_ROOT / "tauri.conf.json"
DEFAULT_CAPABILITY_PATH = TAURI_ROOT / "capabilities" / "default.json"
DEFAULT_LIB_RS_PATH = TAURI_ROOT / "src" / "lib.rs"
DEFAULT_CARGO_CHECK_BOUNDARY_POLICY_PATH = DESKTOP_ROOT / "first-cargo-check-execution.policy.json"

PCWEB_ID = "PCWEB-091"
POLICY_NAME = "Desktop Tauri No-op Shell Local Run Smoke"
POLICY_STATUS = "tauri_noop_shell_local_run_smoke_policy_only"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

EXPECTED_DEV_URL = "http://127.0.0.1:8765/"
EXPECTED_FRONTEND_DIST = "../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static"
EXPECTED_NOOP_COMMANDS = (
    "runtime_get_status",
    "session_prepare",
    "asr_worker_health",
    "mic_adapter_prepare",
    "mic_adapter_status",
    "mic_adapter_start",
    "mic_adapter_pause",
    "mic_adapter_resume",
    "mic_adapter_stop",
    "mic_adapter_delete_audio_chunks",
)
EXPECTED_BRIDGE_COMMAND_IDS = (
    "runtime.get_status",
    "session.prepare",
    "asr_worker.health",
    "mic_adapter.prepare",
    "mic_adapter.status",
    "mic_adapter.start",
    "mic_adapter.pause",
    "mic_adapter.resume",
    "mic_adapter.stop",
    "mic_adapter.delete_audio_chunks",
)
MINIMAL_CAPABILITY_PERMISSIONS = ("core:default",)
GENERATED_ARTIFACT_BLOCKERS = (
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "node_modules",
    "target",
    "dist",
    "bundle",
    ".dmg",
    ".pkg",
    ".msi",
    ".exe",
    ".app",
)
SAFETY_FLAGS = (
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_spawn_process_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_generate_installer_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_provider_config_now",
    "safe_to_read_secret_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_provider_now",
)
PCWEB_090_FALSE_FLAGS = (
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_run_cargo_check_now",
    "safe_to_run_cargo_build_now",
    "safe_to_spawn_process_now",
    "safe_to_fetch_dependencies_now",
    "safe_to_generate_cargo_lock_now",
    "safe_to_generate_target_dir_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_provider_config_now",
    "safe_to_read_secret_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_provider_now",
)
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
    ("artifacts/tmp", ("artifacts", "tmp")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _canonical_payload() -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "smoke_boundary_mode": "readiness_report_only",
        "accepted_desktop_scaffold_source": "pcweb_082_tauri_shell_scaffold",
        "accepted_cargo_check_boundary_source": "pcweb_090_first_cargo_check_execution_boundary",
        "tauri_shell_run_status": "not_run",
        "external_command_execution_status": "not_run",
        "approval_status": "explicit_tauri_run_approval_not_recorded",
        "dev_url": EXPECTED_DEV_URL,
        "frontend_dist": EXPECTED_FRONTEND_DIST,
        "expected_noop_commands": list(EXPECTED_NOOP_COMMANDS),
        "expected_bridge_command_ids": list(EXPECTED_BRIDGE_COMMAND_IDS),
        "bundle_active": False,
        "with_global_tauri": True,
        "minimal_capability_permissions": list(MINIMAL_CAPABILITY_PERMISSIONS),
        "generated_artifact_blockers": list(GENERATED_ARTIFACT_BLOCKERS),
        **_false_safety_flags(),
    }


def _not_generated_packet() -> dict[str, object]:
    return {
        "packet_status": "not_generated",
        "dev_url": EXPECTED_DEV_URL,
        "frontend_dist": EXPECTED_FRONTEND_DIST,
        "expected_noop_commands": list(EXPECTED_NOOP_COMMANDS),
        "expected_bridge_command_ids": list(EXPECTED_BRIDGE_COMMAND_IDS),
        "approval_required": True,
        "command_must_be_run_by": "user_or_separately_approved_runner",
        "post_run_required_action": "record_noop_ipc_result_without_raw_paths_or_secrets",
    }


def _ready_packet() -> dict[str, object]:
    packet = _not_generated_packet()
    packet["packet_status"] = "ready_for_manual_review"
    return packet


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _blocked_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"path is blocked: {label}")
    return errors


def validate_path(path: Path) -> list[str]:
    errors = _blocked_path_errors_for(path)
    resolved = path.resolve(strict=False)
    for error in _blocked_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    return errors


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != PCWEB_ID:
        errors.append("pcweb_id must be PCWEB-091")
    if policy.get("policy_name") != POLICY_NAME:
        errors.append("policy_name must be Desktop Tauri No-op Shell Local Run Smoke")
    if policy.get("policy_status") != POLICY_STATUS:
        errors.append("policy_status must be tauri_noop_shell_local_run_smoke_policy_only")
    if policy.get("default_quality_gate_status") != "included_in_root_pytest":
        errors.append("default_quality_gate_status must be included_in_root_pytest")
    if policy.get("smoke_boundary_mode") != "readiness_report_only":
        errors.append("smoke_boundary_mode must be readiness_report_only")
    if policy.get("accepted_desktop_scaffold_source") != "pcweb_082_tauri_shell_scaffold":
        errors.append("accepted_desktop_scaffold_source must be pcweb_082_tauri_shell_scaffold")
    if policy.get("accepted_cargo_check_boundary_source") != "pcweb_090_first_cargo_check_execution_boundary":
        errors.append(
            "accepted_cargo_check_boundary_source must be pcweb_090_first_cargo_check_execution_boundary"
        )
    if policy.get("tauri_shell_run_status") != "not_run":
        errors.append("tauri_shell_run_status must be not_run")
    if policy.get("external_command_execution_status") != "not_run":
        errors.append("external_command_execution_status must be not_run")
    if policy.get("approval_status") != "explicit_tauri_run_approval_not_recorded":
        errors.append("approval_status must be explicit_tauri_run_approval_not_recorded")
    if policy.get("dev_url") != EXPECTED_DEV_URL:
        errors.append("dev_url must be http://127.0.0.1:8765/")
    if policy.get("frontend_dist") != EXPECTED_FRONTEND_DIST:
        errors.append(
            "frontend_dist must be ../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static"
        )
    if policy.get("expected_noop_commands") != list(EXPECTED_NOOP_COMMANDS):
        errors.append("expected_noop_commands must match PCWEB-107 catalog")
    if policy.get("expected_bridge_command_ids") != list(EXPECTED_BRIDGE_COMMAND_IDS):
        errors.append("expected_bridge_command_ids must match PCWEB-107 catalog")
    if policy.get("bundle_active") is not False:
        errors.append("bundle_active must be false")
    if policy.get("with_global_tauri") is not True:
        errors.append("with_global_tauri must be true")
    if policy.get("minimal_capability_permissions") != list(MINIMAL_CAPABILITY_PERMISSIONS):
        errors.append("minimal_capability_permissions must be ['core:default']")
    if policy.get("generated_artifact_blockers") != list(GENERATED_ARTIFACT_BLOCKERS):
        errors.append("generated_artifact_blockers must match PCWEB-091 blockers")
    for flag in SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def validate_tauri_config(config: dict[str, object]) -> list[str]:
    errors: list[str] = []
    build = config.get("build")
    if not isinstance(build, dict):
        return ["build must be an object"]
    if build.get("devUrl") != EXPECTED_DEV_URL:
        errors.append("build.devUrl must be http://127.0.0.1:8765/")
    if build.get("frontendDist") != EXPECTED_FRONTEND_DIST:
        errors.append(
            "build.frontendDist must be ../../web_mvp/backend/meeting_copilot_web_mvp/frontend_static"
        )
    if build.get("beforeDevCommand") != "":
        errors.append("build.beforeDevCommand must be empty")
    if build.get("beforeBuildCommand") != "":
        errors.append("build.beforeBuildCommand must be empty")

    app = config.get("app")
    if not isinstance(app, dict):
        errors.append("app must be an object")
    elif app.get("withGlobalTauri") is not True:
        errors.append("app.withGlobalTauri must be true for window.__TAURI__ no-op IPC collector")

    bundle = config.get("bundle")
    if not isinstance(bundle, dict):
        errors.append("bundle must be an object")
    elif bundle.get("active") is not False:
        errors.append("bundle.active must be false")
    return errors


def validate_capability(capability: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if capability.get("windows") != ["main"]:
        errors.append("capability.windows must remain ['main']")
    if capability.get("permissions") != list(MINIMAL_CAPABILITY_PERMISSIONS):
        errors.append("capability.permissions must remain ['core:default']")
    return errors


def validate_noop_commands(lib_rs: str) -> list[str]:
    errors: list[str] = []
    public_command_functions = re.findall(r"#\[tauri::command\]\s*pub fn ([a-z0-9_]+)\(", lib_rs)
    if public_command_functions:
        errors.append("noop command functions must remain private to avoid Tauri macro reexport conflicts")

    command_functions = re.findall(r"#\[tauri::command\]\s*fn ([a-z0-9_]+)\(", lib_rs)
    if set(command_functions) != set(EXPECTED_NOOP_COMMANDS):
        errors.append("noop command functions must match PCWEB-107 catalog")

    handler_match = re.search(r"generate_handler!\s*\\?\[\s*(.*?)\s*\]", lib_rs, re.S)
    if handler_match is None:
        errors.append("generate_handler must be present")
    else:
        handler_names = {
            name.strip().removeprefix("commands::")
            for name in handler_match.group(1).split(",")
            if name.strip()
        }
        if handler_names != set(EXPECTED_NOOP_COMMANDS):
            errors.append("generate_handler commands must match PCWEB-107 catalog")

    bridge_ids = set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', lib_rs))
    if bridge_ids != set(EXPECTED_BRIDGE_COMMAND_IDS):
        errors.append("bridge command ids must match PCWEB-107 catalog")

    expected_mapping = {
        "runtime_get_status": "runtime.get_status",
        "session_prepare": "session.prepare",
        "asr_worker_health": "asr_worker.health",
        "mic_adapter_prepare": "mic_adapter.prepare",
        "mic_adapter_status": "mic_adapter.status",
        "mic_adapter_start": "mic_adapter.start",
        "mic_adapter_pause": "mic_adapter.pause",
        "mic_adapter_resume": "mic_adapter.resume",
        "mic_adapter_stop": "mic_adapter.stop",
        "mic_adapter_delete_audio_chunks": "mic_adapter.delete_audio_chunks",
    }
    for function_name, command_id in expected_mapping.items():
        pattern = (
            rf"#\[tauri::command\]\s*fn {function_name}\(\) -> NoopBridgeResponse\s*"
            rf"\{{\s*NoopBridgeResponse::for_command\(\"{re.escape(command_id)}\"\)\s*\}}"
        )
        if re.search(pattern, lib_rs, re.S) is None:
            errors.append(f"{function_name} must return {command_id}")

    required_noop_response_literals = {
        "safe_to_invoke_noop: true": "safe_to_invoke_noop must remain true",
        "safe_to_execute_real_action: false": "safe_to_execute_real_action must remain false",
        "captures_audio: false": "captures_audio must remain false",
        "spawns_process: false": "spawns_process must remain false",
        "calls_remote_provider: false": "calls_remote_provider must remain false",
        "writes_local_files: false": "writes_local_files must remain false",
    }
    for literal, error in required_noop_response_literals.items():
        if literal not in lib_rs:
            errors.append(error)

    forbidden_snippets = (
        "audio_capture_start",
        "audio.capture_start",
        "audio_permissions_status",
        "audio.devices_list",
        "asr_worker_start",
        "env::var",
        "File::create",
        "OpenOptions",
        "configs/local",
        "api_key",
        "Authorization",
        "reqwest",
        "openai",
    )
    for snippet in forbidden_snippets:
        if snippet in lib_rs:
            errors.append(f"forbidden desktop shell snippet present: {snippet}")
    return errors


def validate_generated_artifacts(desktop_root: Path) -> list[str]:
    errors: list[str] = []
    if not desktop_root.exists():
        return [f"desktop_root missing: {desktop_root.as_posix()}"]
    for path in desktop_root.rglob("*"):
        if path.name == ".DS_Store":
            continue
        blocked = path.name in GENERATED_ARTIFACT_BLOCKERS or path.suffix in GENERATED_ARTIFACT_BLOCKERS
        if blocked:
            errors.append(f"generated artifact present: {path.relative_to(desktop_root).as_posix()}")
    return errors


def validate_cargo_check_boundary(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if policy.get("pcweb_id") != "PCWEB-090":
        errors.append("pcweb_090 pcweb_id must be PCWEB-090")
    if policy.get("policy_status") != "first_cargo_check_execution_boundary_policy_only":
        errors.append(
            "pcweb_090 policy_status must be first_cargo_check_execution_boundary_policy_only"
        )
    if policy.get("execution_boundary_mode") != "explicit_manual_execution_packet_only":
        errors.append("pcweb_090 execution_boundary_mode must be explicit_manual_execution_packet_only")
    if policy.get("cargo_check_execution_status") != "not_run":
        errors.append("pcweb_090 cargo_check_execution_status must be not_run")
    if policy.get("external_command_execution_status") != "not_run":
        errors.append("pcweb_090 external_command_execution_status must be not_run")
    for flag in PCWEB_090_FALSE_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"pcweb_090 {flag} must be false")
    return errors


def _base_report() -> dict[str, object]:
    return {
        **_canonical_payload(),
        "report_mode": "tauri_noop_shell_local_run_smoke_static_report",
        "policy_read_status": "not_requested",
        "policy_validation_status": "not_run",
        "policy_validation_errors": [],
        "tauri_config_read_status": "not_requested",
        "tauri_config_validation_status": "not_run",
        "tauri_config_validation_errors": [],
        "capability_read_status": "not_requested",
        "capability_validation_status": "not_run",
        "capability_validation_errors": [],
        "noop_command_read_status": "not_requested",
        "noop_command_validation_status": "not_run",
        "noop_command_validation_errors": [],
        "generated_artifact_validation_status": "not_run",
        "generated_artifact_validation_errors": [],
        "cargo_check_boundary_read_status": "not_requested",
        "cargo_check_boundary_validation_status": "not_run",
        "cargo_check_boundary_validation_errors": [],
        "smoke_packet_status": "blocked_by_path_guard",
        "manual_smoke_packet": _not_generated_packet(),
    }


def _path_guard_report(status_field: str, error_field: str, errors: list[str]) -> dict[str, object]:
    report = _base_report()
    read_status_field = status_field.replace("_validation_status", "_read_status")
    report[read_status_field] = "blocked"
    report[status_field] = "failed"
    report[error_field] = errors[:1]
    return report


def _first_path_error(path: Path) -> list[str]:
    return validate_path(path)[:1]


def _status(errors: list[str]) -> str:
    return "failed" if errors else "passed"


def _packet_status_for(report: dict[str, object]) -> str:
    checks = (
        ("policy_validation_status", "blocked_by_policy_validation"),
        ("tauri_config_validation_status", "blocked_by_tauri_config_validation"),
        ("capability_validation_status", "blocked_by_capability_validation"),
        ("noop_command_validation_status", "blocked_by_noop_command_validation"),
        ("generated_artifact_validation_status", "blocked_by_generated_artifact_validation"),
        ("cargo_check_boundary_validation_status", "blocked_by_cargo_check_boundary_validation"),
    )
    for field, blocked_status in checks:
        if report[field] != "passed":
            return blocked_status
    return "ready_for_explicit_tauri_run_approval"


def build_tauri_noop_shell_run_smoke_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    tauri_config_path: Path = DEFAULT_TAURI_CONFIG_PATH,
    capability_path: Path = DEFAULT_CAPABILITY_PATH,
    lib_rs_path: Path = DEFAULT_LIB_RS_PATH,
    cargo_check_boundary_policy_path: Path = DEFAULT_CARGO_CHECK_BOUNDARY_POLICY_PATH,
    desktop_root: Path = DESKTOP_ROOT,
) -> dict[str, object]:
    path_checks = (
        (policy_path, "policy_validation_status", "policy_validation_errors"),
        (tauri_config_path, "tauri_config_validation_status", "tauri_config_validation_errors"),
        (capability_path, "capability_validation_status", "capability_validation_errors"),
        (lib_rs_path, "noop_command_validation_status", "noop_command_validation_errors"),
        (
            cargo_check_boundary_policy_path,
            "cargo_check_boundary_validation_status",
            "cargo_check_boundary_validation_errors",
        ),
        (desktop_root, "generated_artifact_validation_status", "generated_artifact_validation_errors"),
    )
    for path, status_field, error_field in path_checks:
        errors = _first_path_error(path)
        if errors:
            return _path_guard_report(status_field, error_field, errors)

    policy_errors = validate_policy(_load_json(policy_path))
    tauri_config_errors = validate_tauri_config(_load_json(tauri_config_path))
    capability_errors = validate_capability(_load_json(capability_path))
    noop_command_errors = validate_noop_commands(lib_rs_path.read_text(encoding="utf-8"))
    generated_artifact_errors = validate_generated_artifacts(desktop_root)
    cargo_check_boundary_errors = validate_cargo_check_boundary(_load_json(cargo_check_boundary_policy_path))

    report = _base_report()
    report.update(
        {
            "policy_read_status": "read",
            "policy_validation_status": _status(policy_errors),
            "policy_validation_errors": policy_errors,
            "tauri_config_read_status": "read",
            "tauri_config_validation_status": _status(tauri_config_errors),
            "tauri_config_validation_errors": tauri_config_errors,
            "capability_read_status": "read",
            "capability_validation_status": _status(capability_errors),
            "capability_validation_errors": capability_errors,
            "noop_command_read_status": "read",
            "noop_command_validation_status": _status(noop_command_errors),
            "noop_command_validation_errors": noop_command_errors,
            "generated_artifact_validation_status": _status(generated_artifact_errors),
            "generated_artifact_validation_errors": generated_artifact_errors,
            "cargo_check_boundary_read_status": "read",
            "cargo_check_boundary_validation_status": _status(cargo_check_boundary_errors),
            "cargo_check_boundary_validation_errors": cargo_check_boundary_errors,
        }
    )

    smoke_packet_status = _packet_status_for(report)
    report["smoke_packet_status"] = smoke_packet_status
    if smoke_packet_status == "ready_for_explicit_tauri_run_approval":
        report["manual_smoke_packet"] = _ready_packet()
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--tauri-config", type=Path, default=DEFAULT_TAURI_CONFIG_PATH)
    parser.add_argument("--capability", type=Path, default=DEFAULT_CAPABILITY_PATH)
    parser.add_argument("--lib-rs", type=Path, default=DEFAULT_LIB_RS_PATH)
    parser.add_argument("--cargo-check-boundary-policy", type=Path, default=DEFAULT_CARGO_CHECK_BOUNDARY_POLICY_PATH)
    parser.add_argument("--desktop-root", type=Path, default=DESKTOP_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_tauri_noop_shell_run_smoke_report(
        policy_path=args.policy,
        tauri_config_path=args.tauri_config,
        capability_path=args.capability,
        lib_rs_path=args.lib_rs,
        cargo_check_boundary_policy_path=args.cargo_check_boundary_policy,
        desktop_root=args.desktop_root,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

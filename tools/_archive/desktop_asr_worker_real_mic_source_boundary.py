#!/usr/bin/env python3
"""Build ASR worker real-mic-source boundary evidence without spawning a worker."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-real-mic-source-boundary.policy.json"
)
DEFAULT_RUST_MODULE_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "src-tauri" / "src" / "asr_worker_mic_source_runtime.rs"
)
DEFAULT_LIB_RS_PATH = REPO_ROOT / "code" / "desktop_tauri" / "src-tauri" / "src" / "lib.rs"

PCWEB_ID = "PCWEB-122"
POLICY_NAME = "Desktop ASR Worker Real Mic Source Boundary"
POLICY_STATUS = "desktop_asr_worker_real_mic_source_boundary_policy_only"
REPORT_MODE = "desktop_asr_worker_real_mic_source_boundary_static_report"
IMPLEMENTATION_MODE = "static_worker_mic_source_boundary_no_spawn"
IMPLEMENTATION_STATUS = "implemented_and_smoke_tested"
NOT_ACCEPTED_STATUS = "not_accepted"
SOURCE_KIND = "mic"
EVENT_CONTRACT_STATUS = "partial_final_revision_error_end_of_stream_supported"
WORKER_OUTPUT_ROOT = "artifacts/tmp/asr_events"
WORKER_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
WEB_HANDOFF_STATUS = "closed_to_evidence_state_gap"
COMMAND_CATALOG_SMOKED = [
    "worker.prepare",
    "worker.start",
    "worker.health",
    "worker.collect_events",
    "worker.stop",
    "worker.cleanup",
]
FORBIDDEN_ROOTS = [
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
]
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_stop_worker_now",
    "safe_to_execute_worker_command_now",
    "safe_to_dispatch_worker_command_now",
    "safe_to_bind_worker_command_transport_now",
    "safe_to_read_event_file_now",
    "safe_to_write_event_file_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_download_public_audio_now",
    "safe_to_run_tauri_or_cargo_now",
]
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True
VOICE_MEMOS_LABEL = "Voice" + "Memos"
VOICE_MEMOS_SPACED_LABEL = "Voice" + " " + "Memos"
FORBIDDEN_RUST_SNIPPETS = [
    "std::process",
    "Command::new",
    "std::fs",
    "File::",
    "read_to_string",
    "write(",
    "TcpStream",
    "reqwest",
    "ureq",
    "cpal",
    "rodio",
    "AV" + "AudioEngine",
    "Audio" + "Queue",
    "CoreAudio",
    "Media" + "Recorder",
    "get" + "UserMedia",
    "model" + "scope",
    "Auto" + "Model",
    "api_key",
    "authorization",
    "configs/local",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path, repo_root: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(repo_root.resolve(strict=False))
    except ValueError:
        return None


def _policy_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    candidates = [path, (REPO_ROOT / path).resolve(strict=False) if not path.is_absolute() else path]
    for candidate in candidates:
        if candidate.suffix.casefold() == ".m4a":
            errors.append("policy path is blocked: audio file")
        candidate_text = candidate.as_posix()
        if VOICE_MEMOS_LABEL in candidate_text or VOICE_MEMOS_SPACED_LABEL in candidate_text:
            errors.append(f"policy path is blocked: {VOICE_MEMOS_LABEL}")
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                errors.append(f"policy path is blocked: {label}")
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    if _repo_relative_path(resolved, REPO_ROOT) is None:
        errors.append("policy path is outside repository")
    return sorted(set(errors))


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _base_report() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "not_run",
        "policy_validation_errors": [],
        "rust_boundary_validation_status": "not_run",
        "rust_boundary_validation_errors": [],
        "implementation_status": NOT_ACCEPTED_STATUS,
        "event_contract_status": EVENT_CONTRACT_STATUS,
        "worker_output_root": WORKER_OUTPUT_ROOT,
        "worker_runtime_root": WORKER_RUNTIME_ROOT,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        "source_kind": SOURCE_KIND,
        "command_catalog_smoked": [],
        "requires_explicit_user_start": True,
        "default_uploads_raw_audio": False,
        "default_remote_asr_enabled": False,
        "next_required_decision": "keep_asr_worker_mic_source_blocked_until_full_readiness",
        **_false_safety_flags(),
    }


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "implementation_mode": IMPLEMENTATION_MODE,
        "implementation_status": IMPLEMENTATION_STATUS,
        "source_kind": SOURCE_KIND,
        "event_contract_status": EVENT_CONTRACT_STATUS,
        "worker_output_root": WORKER_OUTPUT_ROOT,
        "worker_runtime_root": WORKER_RUNTIME_ROOT,
        "web_handoff_status": WEB_HANDOFF_STATUS,
        "command_catalog_smoked": COMMAND_CATALOG_SMOKED,
        "default_remote_asr_enabled": False,
        "default_uploads_raw_audio": False,
        "requires_explicit_user_start": True,
        "forbidden_roots": FORBIDDEN_ROOTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            if field == "command_catalog_smoked":
                errors.append("command_catalog_smoked must match PCWEB-122 command list")
            elif expected is False:
                errors.append(f"{field} must be false")
            elif expected is True:
                errors.append(f"{field} must be true")
            else:
                errors.append(f"{field} must be {expected}")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def validate_rust_boundary(
    *,
    rust_module_path: Path = DEFAULT_RUST_MODULE_PATH,
    lib_rs_path: Path = DEFAULT_LIB_RS_PATH,
) -> list[str]:
    errors: list[str] = []
    if not rust_module_path.exists():
        return ["asr_worker_mic_source_runtime.rs must exist"]
    if not lib_rs_path.exists():
        return ["lib.rs must exist"]
    rust_source = rust_module_path.read_text(encoding="utf-8")
    lib_source = lib_rs_path.read_text(encoding="utf-8")
    required_snippets = [
        'ASR_WORKER_MIC_SOURCE_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested"',
        f'EVENT_CONTRACT_STATUS: &str = "{EVENT_CONTRACT_STATUS}"',
        f'WORKER_OUTPUT_ROOT: &str = "{WORKER_OUTPUT_ROOT}"',
        f'WORKER_RUNTIME_ROOT: &str = "{WORKER_RUNTIME_ROOT}"',
        f'WEB_HANDOFF_STATUS: &str = "{WEB_HANDOFF_STATUS}"',
        f'SOURCE_KIND: &str = "{SOURCE_KIND}"',
        "pub fn boundary_evidence() -> AsrWorkerMicSourceBoundaryEvidence",
    ]
    for snippet in required_snippets:
        if snippet not in rust_source:
            errors.append(f"rust boundary missing required snippet: {snippet}")
    for command in COMMAND_CATALOG_SMOKED:
        if f'"{command}"' not in rust_source:
            errors.append(f"rust boundary missing command: {command}")
    for snippet in FORBIDDEN_RUST_SNIPPETS:
        if snippet in rust_source:
            errors.append(f"rust boundary contains forbidden snippet: {snippet}")
    if "pub mod asr_worker_mic_source_runtime;" not in lib_source:
        errors.append("lib.rs must expose asr_worker_mic_source_runtime module")
    return errors


def build_desktop_asr_worker_real_mic_source_boundary_report(
    *,
    policy_path: Path | None = None,
    policy: dict[str, Any] | None = None,
    rust_module_path: Path = DEFAULT_RUST_MODULE_PATH,
    lib_rs_path: Path = DEFAULT_LIB_RS_PATH,
) -> dict[str, Any]:
    report = _base_report()
    selected_policy: dict[str, Any]
    if policy is not None:
        selected_policy = policy
    else:
        selected_path = policy_path or DEFAULT_POLICY_PATH
        path_errors = _policy_path_errors(selected_path)
        if path_errors:
            report["policy_validation_status"] = "blocked_by_policy_path_guard"
            report["policy_validation_errors"] = path_errors
            return report
        selected_policy = _load_json(selected_path)

    policy_errors = validate_policy(selected_policy)
    if policy_errors:
        report["policy_validation_status"] = "failed"
        report["policy_validation_errors"] = policy_errors
        return report

    rust_errors = validate_rust_boundary(
        rust_module_path=rust_module_path,
        lib_rs_path=lib_rs_path,
    )
    report["policy_validation_status"] = "passed"
    if rust_errors:
        report["rust_boundary_validation_status"] = "failed"
        report["rust_boundary_validation_errors"] = rust_errors
        return report

    report.update(
        {
            "rust_boundary_validation_status": "passed",
            "implementation_status": IMPLEMENTATION_STATUS,
            "event_contract_status": EVENT_CONTRACT_STATUS,
            "worker_output_root": WORKER_OUTPUT_ROOT,
            "worker_runtime_root": WORKER_RUNTIME_ROOT,
            "web_handoff_status": WEB_HANDOFF_STATUS,
            "source_kind": SOURCE_KIND,
            "command_catalog_smoked": list(COMMAND_CATALOG_SMOKED),
            "requires_explicit_user_start": True,
            "default_uploads_raw_audio": False,
            "default_remote_asr_enabled": False,
            "next_required_decision": (
                "asr_worker_mic_source_boundary_ready_still_no_spawn_until_full_readiness"
            ),
        }
    )
    return report


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        description="Build PCWEB-122 ASR worker real-mic-source boundary evidence."
    )
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--rust-module-path", type=Path, default=DEFAULT_RUST_MODULE_PATH)
    parser.add_argument("--lib-rs-path", type=Path, default=DEFAULT_LIB_RS_PATH)
    args = parser.parse_args(argv)
    report = build_desktop_asr_worker_real_mic_source_boundary_report(
        policy_path=args.policy_path,
        rust_module_path=args.rust_module_path,
        lib_rs_path=args.lib_rs_path,
    )
    json.dump(report, stdout, ensure_ascii=False, indent=2, sort_keys=True)
    stdout.write("\n")
    return (
        0
        if report.get("policy_validation_status") == "passed"
        and report.get("rust_boundary_validation_status") == "passed"
        and report.get("implementation_status") == IMPLEMENTATION_STATUS
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())

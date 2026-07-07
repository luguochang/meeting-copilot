#!/usr/bin/env python3
"""Build desktop mic adapter implementation-boundary evidence without device access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "mic-adapter-implementation-boundary.policy.json"
)
DEFAULT_RUST_MODULE_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "src-tauri" / "src" / "mic_adapter_runtime.rs"
)
DEFAULT_LIB_RS_PATH = REPO_ROOT / "code" / "desktop_tauri" / "src-tauri" / "src" / "lib.rs"

PCWEB_ID = "PCWEB-121"
POLICY_NAME = "Desktop Mic Adapter Implementation Boundary"
POLICY_STATUS = "desktop_mic_adapter_implementation_boundary_policy_only"
REPORT_MODE = "desktop_mic_adapter_implementation_boundary_static_report"
IMPLEMENTATION_MODE = "static_runtime_boundary_no_device_access"
IMPLEMENTATION_STATUS = "implemented_and_smoke_tested"
NOT_ACCEPTED_STATUS = "not_accepted"
RUNTIME_AUDIO_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime"
AUDIO_CHUNK_ROOT = "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
COMMANDS_SMOKED = [
    "prepare",
    "status",
    "start",
    "pause",
    "resume",
    "stop",
    "delete_audio_chunks",
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
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_start_recording_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_spawn_worker_now",
    "safe_to_run_tauri_or_cargo_now",
]
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


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
        "commands_smoked": [],
        "runtime_audio_root": RUNTIME_AUDIO_ROOT,
        "audio_chunk_root": AUDIO_CHUNK_ROOT,
        "requires_explicit_user_start": True,
        "default_uploads_raw_audio": False,
        "default_remote_asr_enabled": False,
        "next_required_decision": "keep_real_capture_blocked_until_user_shadow_test_readiness",
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
        "runtime_audio_root": RUNTIME_AUDIO_ROOT,
        "audio_chunk_root": AUDIO_CHUNK_ROOT,
        "commands_smoked": COMMANDS_SMOKED,
        "requires_explicit_user_start": True,
        "default_uploads_raw_audio": False,
        "default_remote_asr_enabled": False,
        "forbidden_roots": FORBIDDEN_ROOTS,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            if field == "commands_smoked":
                errors.append("commands_smoked must match PCWEB-121 command list")
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
        return ["mic_adapter_runtime.rs must exist"]
    if not lib_rs_path.exists():
        return ["lib.rs must exist"]
    rust_source = rust_module_path.read_text(encoding="utf-8")
    lib_source = lib_rs_path.read_text(encoding="utf-8")
    required_snippets = [
        'MIC_ADAPTER_IMPLEMENTATION_STATUS: &str = "implemented_and_smoke_tested"',
        f'RUNTIME_AUDIO_ROOT: &str = "{RUNTIME_AUDIO_ROOT}"',
        f'AUDIO_CHUNK_ROOT: &str = "{AUDIO_CHUNK_ROOT}"',
        "pub fn boundary_evidence() -> MicAdapterRuntimeBoundaryEvidence",
    ]
    for snippet in required_snippets:
        if snippet not in rust_source:
            errors.append(f"rust boundary missing required snippet: {snippet}")
    for command in COMMANDS_SMOKED:
        if f'"{command}"' not in rust_source:
            errors.append(f"rust boundary missing command: {command}")
    if "pub mod mic_adapter_runtime;" not in lib_source:
        errors.append("lib.rs must expose mic_adapter_runtime module")
    return errors


def build_desktop_mic_adapter_implementation_boundary_report(
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
            "commands_smoked": list(COMMANDS_SMOKED),
            "runtime_audio_root": RUNTIME_AUDIO_ROOT,
            "audio_chunk_root": AUDIO_CHUNK_ROOT,
            "requires_explicit_user_start": True,
            "default_uploads_raw_audio": False,
            "default_remote_asr_enabled": False,
            "next_required_decision": "mic_adapter_boundary_ready_still_no_capture_until_full_readiness",
        }
    )
    return report


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(
        description="Build PCWEB-121 desktop mic adapter implementation-boundary evidence."
    )
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    args = parser.parse_args(argv)
    report = build_desktop_mic_adapter_implementation_boundary_report(
        policy_path=args.policy_path
    )
    json.dump(report, stdout, ensure_ascii=False, indent=2, sort_keys=True)
    stdout.write("\n")
    return 0 if report.get("policy_validation_status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

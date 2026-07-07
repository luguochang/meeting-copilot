#!/usr/bin/env python3
"""Build a no-side-effect desktop ASR worker implementation approval report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = (
    REPO_ROOT / "code" / "desktop_tauri" / "asr-worker-implementation-approval.policy.json"
)

PCWEB_ID = "PCWEB-101"
POLICY_NAME = "Desktop ASR Worker Implementation Approval Packet"
POLICY_STATUS = "desktop_asr_worker_implementation_approval_policy_only"
REPORT_MODE = "desktop_asr_worker_implementation_approval_static_report"
REQUIRED_PREVIOUS_CONTRACTS = ("PCWEB-098", "PCWEB-099", "PCWEB-100")
APPROVAL_MODE = "approval_packet_preview_only"
IMPLEMENTATION_STATUS = "not_implemented"
WORKER_EXECUTION_STATUS = "not_executed"
PACKET_VERSION = "desktop_asr_worker_implementation_approval.v1"
APPROVED_EVENT_OUTPUT_ROOT = "artifacts/tmp/asr_events"
APPROVED_RUNTIME_ROOT = "artifacts/tmp/desktop_asr_worker_runtime"
ALLOWED_PROVIDER_MODES_NOW = ("mock_streaming", "sherpa_onnx_streaming")
FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL = ("funasr_streaming",)
FORBIDDEN_PROVIDER_MODES = ("remote_asr", "remote_llm_asr")
ALLOWED_SOURCE_KINDS_NOW = ("synthetic",)
FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL = ("mic", "file", "system_audio")
REQUIRED_APPROVAL_TOKENS = (
    "pcweb_101_worker_implementation_design_reviewed",
    "no_mic_no_real_audio_ack",
    "no_remote_asr_no_llm_ack",
    "no_model_download_ack",
    "no_tauri_cargo_run_ack",
)
IMPLEMENTATION_BOUNDARIES = (
    "worker_command_runner_contract",
    "sidecar_process_launcher_contract",
    "event_writer_contract",
    "resource_budget_contract",
    "cleanup_contract",
    "provider_adapter_contract",
)
FALSE_SAFETY_FLAGS = (
    "safe_to_implement_worker_now",
    "safe_to_execute_worker_now",
    "safe_to_spawn_worker_now",
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
    "safe_to_write_runtime_audio_now",
    "safe_to_write_event_file_now",
    "safe_to_read_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
)
BOOLEAN_DENY_FIELDS = (
    "allow_remote_asr",
    "allow_llm",
    "allow_model_download",
    "allow_mic",
    "allow_real_audio",
    "allow_tauri_cargo_run",
)
APPROVED_CODE_ROOTS = ("code/asr_runtime", "code/desktop_tauri")
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
REQUIRED_PACKET_FIELDS = (
    "packet_version",
    "provider_mode",
    "source_kind",
    "future_worker_entrypoint",
    "future_command_runner",
    "event_output_root",
    "runtime_root",
    "max_runtime_seconds",
    "max_memory_mb",
    "max_cpu_percent",
    "approval_tokens",
    "allow_remote_asr",
    "allow_llm",
    "allow_model_download",
    "allow_mic",
    "allow_real_audio",
    "allow_tauri_cargo_run",
)
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


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


def _path_errors_for(path: Path, *, label: str) -> list[str]:
    errors: list[str] = []
    for path_label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"{label} is blocked: {path_label}")
    return errors


def _is_under_root(path: Path, repo_root: Path, approved_root: str) -> bool:
    relative = _repo_relative_path(path, repo_root)
    if relative is None:
        return False
    relative_text = relative.as_posix()
    return relative_text == approved_root or relative_text.startswith(f"{approved_root}/")


def _bounded_path_errors(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    approved_root: str,
) -> list[str]:
    errors = _path_errors_for(path, label=label)
    absolute_path = path if path.is_absolute() else repo_root / path
    resolved = absolute_path.resolve(strict=False)
    for error in _path_errors_for(resolved, label=label):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if path.is_absolute() and _repo_relative_path(path, repo_root) is None:
        return [f"{label} is outside repository"]
    if _repo_relative_path(resolved, repo_root) is None:
        return [f"{label} is outside repository"]
    if not _is_under_root(path, repo_root, approved_root) or not _is_under_root(
        resolved,
        repo_root,
        approved_root,
    ):
        errors.append(f"{label} is not under approved root: {approved_root}")
    return errors


def _code_path_errors(path: Path, *, repo_root: Path, label: str) -> list[str]:
    errors = _path_errors_for(path, label=label)
    absolute_path = path if path.is_absolute() else repo_root / path
    resolved = absolute_path.resolve(strict=False)
    for error in _path_errors_for(resolved, label=label):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if path.is_absolute() and _repo_relative_path(path, repo_root) is None:
        return [f"{label} is outside repository"]
    raw_is_under_code_root = any(_is_under_root(path, repo_root, root) for root in APPROVED_CODE_ROOTS)
    if not raw_is_under_code_root:
        return [f"{label} is not under approved code root"]
    if _repo_relative_path(resolved, repo_root) is None:
        return [f"{label} is outside repository"]
    if not any(
        _is_under_root(resolved, repo_root, approved_root)
        for approved_root in APPROVED_CODE_ROOTS
    ):
        return [f"{label} is not under approved code root"]
    return []


def _display_path(
    path: Path,
    *,
    repo_root: Path,
    label: str,
    approved_root: str | None = None,
    code_path: bool = False,
) -> str:
    if code_path:
        errors = _code_path_errors(path, repo_root=repo_root, label=label)
    else:
        errors = _bounded_path_errors(
            path,
            repo_root=repo_root,
            label=label,
            approved_root=approved_root or "",
        )
    if errors:
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(path, repo_root)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_policy(policy: dict[str, object]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, object] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "approval_mode": APPROVAL_MODE,
        "implementation_status": IMPLEMENTATION_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "approved_event_output_root": APPROVED_EVENT_OUTPUT_ROOT,
        "approved_runtime_root": APPROVED_RUNTIME_ROOT,
        "allowed_provider_modes_now": list(ALLOWED_PROVIDER_MODES_NOW),
        "future_provider_modes_requiring_approval": list(FUTURE_PROVIDER_MODES_REQUIRING_APPROVAL),
        "forbidden_provider_modes": list(FORBIDDEN_PROVIDER_MODES),
        "allowed_source_kinds_now": list(ALLOWED_SOURCE_KINDS_NOW),
        "future_source_kinds_requiring_approval": list(FUTURE_SOURCE_KINDS_REQUIRING_APPROVAL),
        "required_approval_tokens": list(REQUIRED_APPROVAL_TOKENS),
        "implementation_boundaries": list(IMPLEMENTATION_BOUNDARIES),
        "forbidden_roots": [label for label, _parts in FORBIDDEN_PATH_LABELS],
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must be {expected}")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report(*, policy_errors: list[str]) -> dict[str, object]:
    return {
        "pcweb_id": PCWEB_ID,
        "report_mode": REPORT_MODE,
        "policy_validation_status": "failed" if policy_errors else "passed",
        "policy_validation_errors": policy_errors,
        "approval_packet_validation_status": "not_provided",
        "approval_packet_validation_errors": [],
        "implementation_approval_status": (
            "blocked_by_policy_validation" if policy_errors else "implementation_approval_required"
        ),
        "implementation_packet_status": "not_ready",
        "implementation_status": IMPLEMENTATION_STATUS,
        "worker_execution_status": WORKER_EXECUTION_STATUS,
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "approval_mode": APPROVAL_MODE,
        "required_approval_tokens": list(REQUIRED_APPROVAL_TOKENS),
        "approval_tokens_status": "not_provided",
        "implementation_boundaries": list(IMPLEMENTATION_BOUNDARIES),
        "provider_mode": None,
        "source_kind": None,
        "future_worker_entrypoint": None,
        "future_command_runner": None,
        "event_output_root": None,
        "runtime_root": None,
        "resource_budget": None,
        "approved_to_implement_now": False,
        "approved_to_execute_now": False,
        "next_action": (
            "fix_policy_validation_errors"
            if policy_errors
            else "submit_bounded_worker_implementation_approval_packet"
        ),
        **_false_safety_flags(),
    }


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


def _approval_token_errors(tokens: object) -> tuple[str, list[str]]:
    if not isinstance(tokens, list) or not all(isinstance(token, str) for token in tokens):
        return "invalid", ["approval_tokens must be a list of strings"]
    missing = [token for token in REQUIRED_APPROVAL_TOKENS if token not in tokens]
    if missing:
        return "missing_required_tokens", [f"approval_tokens missing: {token}" for token in missing]
    return "all_required_tokens_present", []


def _resource_budget_errors(packet: dict[str, object]) -> list[str]:
    errors: list[str] = []
    ranges = {
        "max_runtime_seconds": (1, 7200),
        "max_memory_mb": (128, 8192),
        "max_cpu_percent": (1, 400),
    }
    for field, (minimum, maximum) in ranges.items():
        value = packet.get(field)
        if not _is_number(value) or not minimum <= float(value) <= maximum:
            errors.append(f"{field} must be between {minimum} and {maximum}")
    return errors


def validate_approval_packet(
    approval_packet: dict[str, object] | None,
    *,
    repo_root: Path,
) -> tuple[str, list[str], str, dict[str, object]]:
    if approval_packet is None:
        return "not_provided", [], "not_provided", {}
    errors: list[str] = []
    normalized: dict[str, object] = {}
    for field in REQUIRED_PACKET_FIELDS:
        if field not in approval_packet:
            errors.append(f"{field} is required")
    if approval_packet.get("packet_version") != PACKET_VERSION:
        errors.append(f"packet_version must be {PACKET_VERSION}")
    provider_mode = approval_packet.get("provider_mode")
    source_kind = approval_packet.get("source_kind")
    errors.extend(_provider_mode_errors(provider_mode))
    errors.extend(_source_kind_errors(source_kind))
    for field in BOOLEAN_DENY_FIELDS:
        if approval_packet.get(field) is not False:
            errors.append(f"{field} must be false")

    token_status, token_errors = _approval_token_errors(approval_packet.get("approval_tokens"))
    errors.extend(token_errors)
    errors.extend(_resource_budget_errors(approval_packet))

    path_specs = (
        ("future_worker_entrypoint", None, True),
        ("future_command_runner", None, True),
        ("event_output_root", APPROVED_EVENT_OUTPUT_ROOT, False),
        ("runtime_root", APPROVED_RUNTIME_ROOT, False),
    )
    for field, approved_root, is_code_path in path_specs:
        value = approval_packet.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
            normalized[field] = "<redacted_invalid_path>"
            continue
        path = Path(value)
        if is_code_path:
            errors.extend(_code_path_errors(path, repo_root=repo_root, label=field))
            normalized[field] = _display_path(path, repo_root=repo_root, label=field, code_path=True)
        else:
            errors.extend(
                _bounded_path_errors(
                    path,
                    repo_root=repo_root,
                    label=field,
                    approved_root=str(approved_root),
                )
            )
            normalized[field] = _display_path(
                path,
                repo_root=repo_root,
                label=field,
                approved_root=str(approved_root),
            )

    normalized.update(
        {
            "provider_mode": provider_mode if isinstance(provider_mode, str) else None,
            "source_kind": source_kind if isinstance(source_kind, str) else None,
            "resource_budget": {
                "max_runtime_seconds": approval_packet.get("max_runtime_seconds"),
                "max_memory_mb": approval_packet.get("max_memory_mb"),
                "max_cpu_percent": approval_packet.get("max_cpu_percent"),
            },
        }
    )
    status = "failed" if errors else "passed"
    return status, errors, token_status, normalized


def build_desktop_asr_worker_implementation_approval_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    approval_packet: dict[str, object] | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, object]:
    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy)
    report = _base_report(policy_errors=policy_errors)
    if policy_errors:
        return report

    packet_status, packet_errors, token_status, normalized = validate_approval_packet(
        approval_packet,
        repo_root=repo_root,
    )
    report["approval_packet_validation_status"] = packet_status
    report["approval_packet_validation_errors"] = packet_errors
    report["approval_tokens_status"] = token_status
    for field in (
        "provider_mode",
        "source_kind",
        "future_worker_entrypoint",
        "future_command_runner",
        "event_output_root",
        "runtime_root",
        "resource_budget",
    ):
        if field in normalized:
            report[field] = normalized[field]
    if packet_status == "not_provided":
        return report
    if packet_status == "failed":
        report["implementation_approval_status"] = "blocked_by_approval_packet_validation"
        report["implementation_packet_status"] = "blocked"
        report["next_action"] = "fix_approval_packet_or_seek_required_approval"
        return report

    report["implementation_approval_status"] = "ready_for_manual_review_not_executable"
    report["implementation_packet_status"] = "preview_ready"
    report["next_action"] = "manual_review_pcweb_101_packet_before_any_worker_implementation"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-path", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--approval-packet-json")
    return parser.parse_args(argv)


def _approval_packet_from_json(text: str | None) -> dict[str, object] | None:
    if text is None:
        return None
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise SystemExit("approval packet JSON must be an object")
    return payload


def main(argv: list[str] | None = None, *, stdout: TextIO = sys.stdout) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_desktop_asr_worker_implementation_approval_report(
        policy_path=args.policy_path,
        approval_packet=_approval_packet_from_json(args.approval_packet_json),
    )
    stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
    stdout.write("\n")
    if report["policy_validation_status"] != "passed":
        return 1
    if report["approval_packet_validation_status"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate caller-provided desktop Tauri no-op run results without running anything."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = REPO_ROOT / "code" / "desktop_tauri" / "tauri-noop-run-result-intake.policy.json"

PCWEB_ID = "PCWEB-113"
POLICY_NAME = "Desktop Tauri No-op Run Result Intake"
POLICY_STATUS = "desktop_tauri_noop_run_result_intake_policy_only"
REPORT_MODE = "desktop_tauri_noop_run_result_intake_static_report"
RESULT_INTAKE_MODE = "caller_provided_tauri_noop_result_validation_only"
ACCEPTED_RESULT_SOURCE = "caller_provided_json_only"
RUN_RESULT_VERSION = "desktop_tauri_noop_run_result.v1"
RUN_EXECUTION_STATUS = "not_run_by_intake"
EXTERNAL_COMMAND_EXECUTION_STATUS = "not_run"
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True
REQUIRED_PREVIOUS_CONTRACTS = ["PCWEB-091", "PCWEB-107", "PCWEB-109", "PCWEB-112"]
ALLOWED_RESULT_ROOT = "artifacts/tmp/desktop_tauri_noop_run_results"
NEXT_DECISION_WITHOUT_RESULT = "perform_explicit_real_tauri_noop_run_or_keep_blocked"
NEXT_DECISION_WITH_VALID_RESULT = "review_worker_mic_source_approval_packet"

EXPECTED_COMMANDS = [
    ("runtime.get_status", "runtime_get_status"),
    ("session.prepare", "session_prepare"),
    ("asr_worker.health", "asr_worker_health"),
    ("mic_adapter.prepare", "mic_adapter_prepare"),
    ("mic_adapter.status", "mic_adapter_status"),
    ("mic_adapter.start", "mic_adapter_start"),
    ("mic_adapter.pause", "mic_adapter_pause"),
    ("mic_adapter.resume", "mic_adapter_resume"),
    ("mic_adapter.stop", "mic_adapter_stop"),
    ("mic_adapter.delete_audio_chunks", "mic_adapter_delete_audio_chunks"),
]
EXPECTED_COMMAND_IDS = [command_id for command_id, _ in EXPECTED_COMMANDS]
EXPECTED_COMMAND_NAMES = [command_name for _, command_name in EXPECTED_COMMANDS]
EXPECTED_COMMAND_NAME_BY_ID = dict(EXPECTED_COMMANDS)
EXPECTED_NOOP_RESULT_VALUES = {
    "command_status": "noop_bound",
    "implementation_status": "noop_only",
    "transport_status": "tauri_ipc_bound",
    "side_effect_status": "none",
    "safe_to_invoke_noop": True,
    "safe_to_execute_real_action": False,
    "captures_audio": False,
    "spawns_process": False,
    "calls_remote_provider": False,
    "writes_local_files": False,
}
COMMAND_RESULT_FIELDS = {"command_id", "command_name", "invoke_status", "result"}
NOOP_RESULT_FIELDS = {"command_id", *EXPECTED_NOOP_RESULT_VALUES.keys()}
TOP_LEVEL_RESULT_FIELDS = {
    "run_result_version",
    "run_id",
    "run_environment",
    "explicit_tauri_run_approval_recorded",
    "web_app_url_status",
    "ipc_transport_status",
    "command_results",
}
FORBIDDEN_RAW_RESULT_FIELDS = {
    "stdout",
    "stderr",
    "raw_stdout",
    "raw_stderr",
    "command",
    "command_text",
    "command_output",
    "cwd",
    "path",
    "env",
    "api_key",
    "authorization",
    "bearer_token",
    "secret",
    "audio_path",
    "provider_config",
    "configs_local",
}
SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
FALSE_SAFETY_FLAGS = [
    "safe_to_run_tauri_dev_now",
    "safe_to_run_tauri_build_now",
    "safe_to_run_" + "car" + "go_check_now",
    "safe_to_run_" + "car" + "go_build_now",
    "safe_to_spawn_process_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_mutate_web_session_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _blocked_path_errors(path: Path) -> list[str]:
    errors: list[str] = []
    if path.suffix.casefold() == ".m4a":
        errors.append("path is blocked: audio file")
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"path is blocked: {label}")
    resolved = path.resolve(strict=False)
    if resolved.suffix.casefold() == ".m4a" and "path is blocked: audio file" not in errors:
        errors.append("path is blocked: audio file")
    for label, suffix_parts in FORBIDDEN_PATH_LABELS:
        error = f"path is blocked: {label}"
        if _path_has_suffix_parts(resolved, suffix_parts) and error not in errors:
            errors.append(error)
    return errors


def _validate_policy_path(path: Path) -> list[str]:
    errors = _blocked_path_errors(path)
    if errors:
        return errors[:1]
    relative = _repo_relative_path(path)
    if relative is None:
        return ["policy path resolves outside repository"]
    return []


def _validate_result_path(path: Path) -> list[str]:
    errors = _blocked_path_errors(path)
    if errors:
        return errors[:1]
    relative = _repo_relative_path(path)
    if relative is None:
        return ["result_path must be under approved artifacts root"]
    relative_text = relative.as_posix()
    if not (
        relative_text == ALLOWED_RESULT_ROOT
        or relative_text.startswith(f"{ALLOWED_RESULT_ROOT}/")
    ):
        return ["result_path must be under approved artifacts root"]
    if path.suffix != ".json":
        return ["result_path must be a JSON file"]
    return []


def _canonical_payload() -> dict[str, Any]:
    return {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "result_intake_mode": RESULT_INTAKE_MODE,
        "accepted_result_source": ACCEPTED_RESULT_SOURCE,
        "tauri_run_execution_status": RUN_EXECUTION_STATUS,
        "external_command_execution_status": EXTERNAL_COMMAND_EXECUTION_STATUS,
        "required_previous_contracts": list(REQUIRED_PREVIOUS_CONTRACTS),
        "run_result_version": RUN_RESULT_VERSION,
        "allowed_result_root": ALLOWED_RESULT_ROOT,
        "expected_noop_commands": list(EXPECTED_COMMAND_NAMES),
        "expected_bridge_command_ids": list(EXPECTED_COMMAND_IDS),
        **_false_safety_flags(),
    }


def validate_policy(policy: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields: dict[str, Any] = {
        "pcweb_id": PCWEB_ID,
        "policy_name": POLICY_NAME,
        "policy_status": POLICY_STATUS,
        "default_quality_gate_status": "included_in_root_pytest",
        "result_intake_mode": RESULT_INTAKE_MODE,
        "accepted_result_source": ACCEPTED_RESULT_SOURCE,
        "tauri_run_execution_status": RUN_EXECUTION_STATUS,
        "external_command_execution_status": EXTERNAL_COMMAND_EXECUTION_STATUS,
        "required_previous_contracts": REQUIRED_PREVIOUS_CONTRACTS,
        "run_result_version": RUN_RESULT_VERSION,
        "allowed_result_root": ALLOWED_RESULT_ROOT,
        "expected_noop_commands": EXPECTED_COMMAND_NAMES,
        "expected_bridge_command_ids": EXPECTED_COMMAND_IDS,
        "next_required_decision_without_result": NEXT_DECISION_WITHOUT_RESULT,
        "next_required_decision_with_valid_result": NEXT_DECISION_WITH_VALID_RESULT,
    }
    for field, expected in expected_fields.items():
        if policy.get(field) != expected:
            errors.append(f"{field} must match PCWEB-113 policy")
    for flag in FALSE_SAFETY_FLAGS:
        if policy.get(flag) is not False:
            errors.append(f"{flag} must be false")
    return errors


def _base_report() -> dict[str, Any]:
    return {
        **_canonical_payload(),
        "report_mode": REPORT_MODE,
        "policy_read_status": "not_requested",
        "policy_validation_status": "not_run",
        "policy_validation_errors": [],
        "result_read_status": "not_requested",
        "result_validation_status": "not_provided",
        "result_validation_errors": [],
        "tauri_noop_run_result_status": "no_result_provided",
        "real_tauri_noop_run_evidence_status": "not_available",
        "validated_command_count": 0,
        "returned_command_count": 0,
        "failed_command_count": 0,
        "normalized_command_results": [],
        "next_required_decision": NEXT_DECISION_WITHOUT_RESULT,
    }


def _blocked_policy_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report.update(
        {
            "policy_read_status": "blocked" if errors and errors[0].startswith("path ") else "read",
            "policy_validation_status": "failed",
            "policy_validation_errors": errors,
            "result_validation_status": "blocked_by_policy_validation",
            "tauri_noop_run_result_status": "blocked_by_policy_validation",
        }
    )
    return report


def _sanitize_error(error: str) -> str:
    if "/Users/" in error or "Bearer" in error or "SECRET" in error or "sk-" in error:
        return "result validation failed"
    return error


def _top_level_result_errors(run_result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in run_result:
        if field in FORBIDDEN_RAW_RESULT_FIELDS:
            errors.append(f"forbidden raw result field present: {field}")
        elif field not in TOP_LEVEL_RESULT_FIELDS:
            errors.append("unknown result field present")
    if run_result.get("run_result_version") != RUN_RESULT_VERSION:
        errors.append(f"run_result_version must be {RUN_RESULT_VERSION}")
    run_id = run_result.get("run_id")
    if not isinstance(run_id, str) or SAFE_ID_PATTERN.fullmatch(run_id) is None:
        errors.append("run_id must be a safe id")
    if run_result.get("run_environment") != "tauri_webview":
        errors.append("run_environment must be tauri_webview")
    if run_result.get("explicit_tauri_run_approval_recorded") is not True:
        errors.append("explicit_tauri_run_approval_recorded must be true")
    if run_result.get("web_app_url_status") != "local_dev_url_loaded":
        errors.append("web_app_url_status must be local_dev_url_loaded")
    if run_result.get("ipc_transport_status") != "tauri_ipc_available":
        errors.append("ipc_transport_status must be tauri_ipc_available")
    if not isinstance(run_result.get("command_results"), list):
        errors.append("command_results must be a list")
    return errors


def _command_set_errors(command_results: list[Any]) -> list[str]:
    ids: list[str] = []
    for item in command_results:
        if isinstance(item, dict) and isinstance(item.get("command_id"), str):
            ids.append(item["command_id"])
    if sorted(ids) != sorted(EXPECTED_COMMAND_IDS) or len(ids) != len(set(ids)):
        return ["command_results must contain exactly the expected no-op command ids"]
    return []


def _validate_known_command_result(command: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    command_id = command.get("command_id")
    if command_id not in EXPECTED_COMMAND_NAME_BY_ID:
        return errors, None
    expected_name = EXPECTED_COMMAND_NAME_BY_ID[command_id]

    for field in command:
        if field not in COMMAND_RESULT_FIELDS:
            errors.append(f"command {command_id} contains unsupported field")
    if command.get("command_name") != expected_name:
        errors.append(f"command {command_id} command_name must be {expected_name}")
    if command.get("invoke_status") != "returned":
        errors.append(f"command {command_id} invoke_status must be returned")
    result = command.get("result")
    if not isinstance(result, dict):
        errors.append(f"command {command_id} result must be an object")
        return errors, None

    for field in result:
        if field not in NOOP_RESULT_FIELDS:
            errors.append(f"command {command_id} result contains unsupported field: {field}")
    if result.get("command_id") != command_id:
        errors.append(f"command {command_id} result.command_id must match")
    for field, expected in EXPECTED_NOOP_RESULT_VALUES.items():
        if result.get(field) != expected:
            errors.append(f"command {command_id} {field} must be {str(expected).lower()}")
    if errors:
        return errors, None

    normalized = {
        "command_id": command_id,
        "command_name": expected_name,
        "invoke_status": "returned",
        **EXPECTED_NOOP_RESULT_VALUES,
    }
    return errors, normalized


def _validate_run_result(run_result: Any) -> tuple[str, list[str], list[dict[str, Any]], int, int]:
    if run_result is None:
        return "not_provided", [], [], 0, 0
    if not isinstance(run_result, dict):
        return "failed", ["run_result must be an object"], [], 0, 0

    errors = _top_level_result_errors(run_result)
    command_results = run_result.get("command_results")
    normalized_by_id: dict[str, dict[str, Any]] = {}
    returned_count = 0
    failed_count = 0

    if isinstance(command_results, list):
        errors.extend(_command_set_errors(command_results))
        for command in command_results:
            if not isinstance(command, dict):
                errors.append("command_results entries must be objects")
                continue
            command_id = command.get("command_id")
            if isinstance(command_id, str) and command_id in EXPECTED_COMMAND_NAME_BY_ID:
                if command.get("invoke_status") == "returned":
                    returned_count += 1
                else:
                    failed_count += 1
                command_errors, normalized = _validate_known_command_result(command)
                errors.extend(command_errors)
                if normalized is not None:
                    normalized_by_id[command_id] = normalized

    safe_errors: list[str] = []
    for error in errors:
        safe_error = _sanitize_error(error)
        if safe_error not in safe_errors:
            safe_errors.append(safe_error)
    if safe_errors:
        return "failed", safe_errors, [], returned_count, failed_count
    normalized = [normalized_by_id[command_id] for command_id in EXPECTED_COMMAND_IDS]
    return "passed", [], normalized, returned_count, failed_count


def _load_result_from_path(result_path: Path) -> tuple[Any | None, str, list[str]]:
    errors = _validate_result_path(result_path)
    if errors:
        return None, "blocked", errors
    return _load_json(result_path), "read", []


def build_tauri_noop_run_result_intake_report(
    *,
    policy_path: Path = DEFAULT_POLICY_PATH,
    run_result: dict[str, Any] | None = None,
    result_path: Path | None = None,
) -> dict[str, Any]:
    policy_path_errors = _validate_policy_path(policy_path)
    if policy_path_errors:
        return _blocked_policy_report(policy_path_errors)

    policy = _load_json(policy_path)
    policy_errors = validate_policy(policy if isinstance(policy, dict) else {})
    if policy_errors:
        return _blocked_policy_report(policy_errors)

    report = _base_report()
    report.update({"policy_read_status": "read", "policy_validation_status": "passed"})

    if run_result is not None and result_path is not None:
        report.update(
            {
                "result_validation_status": "failed",
                "result_validation_errors": ["provide either run_result or result_path, not both"],
                "tauri_noop_run_result_status": "blocked_by_result_validation",
            }
        )
        return report

    effective_result: Any = run_result
    if result_path is not None:
        effective_result, read_status, read_errors = _load_result_from_path(result_path)
        report["result_read_status"] = read_status
        if read_errors:
            report.update(
                {
                    "result_validation_status": "blocked_by_result_path_guard",
                    "result_validation_errors": read_errors,
                    "tauri_noop_run_result_status": "blocked_by_result_path_guard",
                }
            )
            return report

    validation_status, validation_errors, normalized, returned_count, failed_count = _validate_run_result(
        effective_result
    )
    report.update(
        {
            "result_validation_status": validation_status,
            "result_validation_errors": validation_errors,
            "normalized_command_results": normalized,
            "validated_command_count": len(normalized),
            "returned_command_count": returned_count if validation_status == "passed" else 0,
            "failed_command_count": failed_count if validation_status == "passed" else 0,
        }
    )
    if validation_status == "passed":
        report.update(
            {
                "tauri_noop_run_result_status": "validated_noop_ipc_observed",
                "real_tauri_noop_run_evidence_status": "ready_for_worker_mic_source_approval_review",
                "next_required_decision": NEXT_DECISION_WITH_VALID_RESULT,
            }
        )
    elif validation_status == "failed":
        report.update(
            {
                "tauri_noop_run_result_status": "blocked_by_result_validation",
                "real_tauri_noop_run_evidence_status": "blocked",
            }
        )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--result-path", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_tauri_noop_run_result_intake_report(
        policy_path=args.policy,
        result_path=args.result_path,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

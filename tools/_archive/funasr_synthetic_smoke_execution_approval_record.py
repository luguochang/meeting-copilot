#!/usr/bin/env python3
"""Build a local FunASR synthetic smoke approval record template."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_ID = "DRV-049"
REPORT_MODE = "funasr_synthetic_smoke_execution_approval_record"
REPORT_VERSION = "funasr_synthetic_smoke_execution_approval_record.v1"
APPROVAL_RECORD_VERSION = "funasr_synthetic_smoke_execution_approval.v1"
APPROVAL_SCOPE = "local_funasr_synthetic_smoke_5_scenarios_only"
APPROVAL_TOKEN = "APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY"
APPROVED_PACKET_ROOT = "artifacts/tmp/asr_reports"
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
    "safe_to_run_asr_now",
    "safe_to_read_synthetic_audio_now",
    "safe_to_write_ignored_asr_artifacts_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _base_report() -> dict[str, Any]:
    return {
        "approval_record_builder_id": BUILDER_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "approval_template_status": "not_run",
        "packet_read_status": "not_requested",
        "approval_record_template": None,
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    try:
        return resolved.relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _packet_path_errors(path: Path) -> list[str]:
    for candidate in (path, path.resolve(strict=False)):
        if candidate.suffix.casefold() == ".m4a":
            return ["execution_packet_path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"execution_packet_path is blocked: {label}"]
    relative = _repo_relative_path(path)
    if relative is None:
        return [f"execution_packet_path must be under approved root: {APPROVED_PACKET_ROOT}"]
    relative_text = relative.as_posix()
    if not (
        relative_text == APPROVED_PACKET_ROOT
        or relative_text.startswith(f"{APPROVED_PACKET_ROOT}/")
    ):
        return [f"execution_packet_path must be under approved root: {APPROVED_PACKET_ROOT}"]
    if path.suffix.casefold() != ".json":
        return ["execution_packet_path must be a JSON file"]
    return []


def _load_packet(path_text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    path = Path(path_text)
    errors = _packet_path_errors(path)
    if errors:
        return None, errors, "blocked"
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["execution_packet_path does not exist"], "failed"
    except json.JSONDecodeError:
        return None, ["execution_packet_path must contain valid JSON"], "failed"
    if not isinstance(payload, dict):
        return None, ["execution_packet_path JSON must be an object"], "failed"
    return payload, [], "read"


def _packet_errors(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("packet_mode") != "funasr_synthetic_smoke_execution_packet":
        errors.append("packet_mode must be funasr_synthetic_smoke_execution_packet")
    if packet.get("packet_status") != "ready_for_manual_batch_funasr_synthetic_smoke_run":
        errors.append("packet_status must be ready_for_manual_batch_funasr_synthetic_smoke_run")
    if packet.get("execution_approval_status") != "not_approved_manual_run_only":
        errors.append("execution_approval_status must be not_approved_manual_run_only")
    if packet.get("scenario_count") != 5:
        errors.append("scenario_count must be 5")
    if packet.get("engineering_scenario_count") != 4:
        errors.append("engineering_scenario_count must be 4")
    if packet.get("negative_control_count") != 1:
        errors.append("negative_control_count must be 1")
    if not isinstance(packet.get("command_previews"), list) or len(packet["command_previews"]) != 5:
        errors.append("command_previews must contain 5 entries")
    if not isinstance(packet.get("postprocess_command_previews"), list) or len(packet["postprocess_command_previews"]) != 5:
        errors.append("postprocess_command_previews must contain 5 entries")
    return errors


def _approval_record(
    *,
    approved_packet_path: str,
    approval_id: str,
    confirmed: bool,
) -> dict[str, Any]:
    return {
        "approval_record_version": APPROVAL_RECORD_VERSION,
        "approval_id": approval_id,
        "approval_scope": APPROVAL_SCOPE,
        "approval_token": APPROVAL_TOKEN,
        "approval_confirmed_by_user": confirmed,
        "approved_packet_path": approved_packet_path,
        "approved_scenario_count": 5,
        "allow_read_synthetic_audio": True,
        "allow_write_ignored_asr_artifacts": True,
        "allow_run_local_funasr": True,
        "deny_real_user_audio": True,
        "deny_microphone": True,
        "deny_remote_asr": True,
        "deny_llm": True,
        "deny_model_download": True,
    }


def build_funasr_synthetic_smoke_execution_approval_record_report(
    *,
    execution_packet: dict[str, Any] | None = None,
    execution_packet_path: str | None = None,
    approved_packet_path: str | None = None,
    approval_id: str = "funasr-synthetic-smoke-approval-draft",
    confirm: bool = False,
) -> dict[str, Any]:
    report = _base_report()
    packet = execution_packet
    packet_path_for_record = approved_packet_path or execution_packet_path
    if execution_packet is not None and execution_packet_path is not None:
        report["approval_template_status"] = "blocked_invalid_packet_input"
        report["validation_errors"] = ["provide only one execution packet input source"]
        return report
    if execution_packet_path is not None:
        packet, errors, read_status = _load_packet(execution_packet_path)
        report["packet_read_status"] = read_status
        if errors:
            report["approval_template_status"] = "blocked_by_packet_path_guard" if read_status == "blocked" else "blocked_invalid_packet"
            report["validation_errors"] = errors
            return report
    elif packet is not None:
        report["packet_read_status"] = "provided_inline"
    else:
        report["approval_template_status"] = "blocked_missing_execution_packet"
        report["validation_errors"] = ["execution_packet is required"]
        return report

    packet_errors = _packet_errors(packet)
    if packet_errors:
        report["approval_template_status"] = "blocked_invalid_packet"
        report["validation_errors"] = packet_errors
        return report
    if not packet_path_for_record:
        report["approval_template_status"] = "blocked_missing_approved_packet_path"
        report["validation_errors"] = ["approved_packet_path is required"]
        return report

    report["approval_record_template"] = _approval_record(
        approved_packet_path=packet_path_for_record,
        approval_id=approval_id,
        confirmed=confirm,
    )
    report["approval_template_status"] = (
        "approval_record_confirmed_for_local_synthetic_smoke"
        if confirm
        else "approval_record_template_ready_not_confirmed"
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-packet-path", required=True)
    parser.add_argument("--approval-id", default="funasr-synthetic-smoke-approval-draft")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Create a confirmed approval record. Use only after explicit user approval.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_execution_approval_record_report(
        execution_packet_path=args.execution_packet_path,
        approval_id=args.approval_id,
        confirm=args.confirm,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["approval_template_status"] in {
        "approval_record_template_ready_not_confirmed",
        "approval_record_confirmed_for_local_synthetic_smoke",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

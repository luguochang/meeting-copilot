#!/usr/bin/env python3
"""Run or dry-run an approved local FunASR synthetic smoke packet."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_ID = "DRV-048"
REPORT_MODE = "funasr_synthetic_smoke_approved_runner"
REPORT_VERSION = "funasr_synthetic_smoke_approved_runner.v1"
APPROVAL_RECORD_VERSION = "funasr_synthetic_smoke_execution_approval.v1"
APPROVAL_SCOPE = "local_funasr_synthetic_smoke_5_scenarios_only"
APPROVAL_TOKEN = "APPROVE_LOCAL_FUNASR_SYNTHETIC_SMOKE_ONLY"
APPROVED_PACKET_ROOT = "artifacts/tmp/asr_reports"
APPROVED_PACKET_STATUS = "ready_for_manual_batch_funasr_synthetic_smoke_run"
EXPECTED_PACKET_DECISION_ID = "DRV-045"
EXPECTED_PACKET_VERSION = "funasr_synthetic_smoke_execution_packet.v1"
LOCAL_MODEL_PLACEHOLDER_PREFIX = "<modelscope_runtime_models_iic/"
FUNASR_PYTHON = "code/asr_runtime/.venv-funasr/bin/python"
FUNASR_SCRIPT = "code/asr_runtime/scripts/transcribe_funasr.py"
PYTHON = "python3"
TRANSCRIPT_REPORT_SCRIPT = "code/asr_runtime/scripts/transcript_report.py"
SYNTHETIC_ASR_SMOKE_REPORT_SCRIPT = "tools/funasr_synthetic_smoke_single_result_builder.py"
MODEL_ALIAS = "paraformer-zh-streaming"
DEVICE = "cpu"
CHUNK_SIZE = "0,10,5"
ENCODER_CHUNK_LOOK_BACK = "4"
DECODER_CHUNK_LOOK_BACK = "1"
FINAL_WINDOW_MS = "3000"
TECHNICAL_GLOSSARY_PATH = "data/asr_eval/glossaries/technical-terms.zh.json"
APPROVED_AUDIO_ROOT = "artifacts/tmp/synthetic_audio"
APPROVED_EVENTS_ROOT = "artifacts/tmp/asr_events"
APPROVED_REPORT_ROOT = "artifacts/tmp/asr_reports"
APPROVED_SYNTHETIC_SCRIPT_ROOT = "data/asr_eval/synthetic_meetings/scripts"
DEFAULT_SCENARIOS = [
    ("api-review-001", "engineering", "api-review"),
    ("architecture-review-001", "engineering", "architecture-review"),
    ("incident-review-001", "engineering", "incident-review"),
    ("release-review-001", "engineering", "release-review"),
    ("non-engineering-control-001", "negative_control", "non-engineering-control"),
]
EXPECTED_SCENARIO_IDS = [scenario_id for scenario_id, _kind, _script_stem in DEFAULT_SCENARIOS]
EXPECTED_SCENARIO_KINDS = {
    scenario_id: scenario_kind
    for scenario_id, scenario_kind, _script_stem in DEFAULT_SCENARIOS
}
EXPECTED_SCRIPT_STEMS = {
    scenario_id: script_stem
    for scenario_id, _scenario_kind, script_stem in DEFAULT_SCENARIOS
}
FALSE_SAFETY_FLAGS = [
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
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
REQUIRED_LOCAL_MODEL_FILES = ("model.pt", "config.yaml")


CommandRunner = Callable[[list[str]], dict[str, Any]]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _base_report() -> dict[str, Any]:
    return {
        "runner_id": RUNNER_ID,
        "report_mode": REPORT_MODE,
        "report_version": REPORT_VERSION,
        "runner_status": "not_run",
        "packet_read_status": "not_requested",
        "packet_validation_status": "not_run",
        "approval_record_status": "not_provided",
        "local_model_dir_status": "not_provided",
        "planned_provider_command_count": 0,
        "planned_postprocess_command_count": 0,
        "executed_command_count": 0,
        "command_results": [],
        "validation_errors": [],
        "safe_to_run_asr_now": False,
        "safe_to_read_synthetic_audio_now": False,
        "safe_to_write_ignored_asr_artifacts_now": False,
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


def _path_guard_errors(label: str, path: Path, *, approved_root: str | None = None) -> list[str]:
    for candidate in (path, path.resolve(strict=False)):
        if ("voice" + "memos") in candidate.as_posix().casefold():
            return [f"{label} is blocked: voice memo app path"]
        if candidate.suffix.casefold() == ".m4a":
            return [f"{label} is blocked: audio file"]
        for root_label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"{label} is blocked: {root_label}"]
    if approved_root is None:
        return []
    relative = _repo_relative_path(path)
    if relative is None:
        return [f"{label} must be under approved root: {approved_root}"]
    relative_text = relative.as_posix()
    if not (relative_text == approved_root or relative_text.startswith(f"{approved_root}/")):
        return [f"{label} must be under approved root: {approved_root}"]
    return []


def _path_for(root: str, scenario_id: str, suffix: str) -> str:
    return f"{root}/{scenario_id}{suffix}"


def _expected_audio_path(scenario_id: str) -> str:
    return _path_for(APPROVED_AUDIO_ROOT, scenario_id, ".wav")


def _expected_events_path(scenario_id: str) -> str:
    return _path_for(APPROVED_EVENTS_ROOT, scenario_id, ".funasr.events.json")


def _expected_provider_path(scenario_id: str) -> str:
    return _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.provider.json")


def _expected_transcript_path(scenario_id: str) -> str:
    return _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.transcript-report.json")


def _expected_smoke_path(scenario_id: str) -> str:
    return _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.smoke-report.json")


def _expected_script_json_path(scenario_id: str) -> str:
    return f"{APPROVED_SYNTHETIC_SCRIPT_ROOT}/{EXPECTED_SCRIPT_STEMS[scenario_id]}.json"


def _option_value(argv: list[str], option: str, *, label: str, errors: list[str]) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        errors.append(f"{label} must include {option}")
        return None
    if index + 1 >= len(argv):
        errors.append(f"{label} must include value for {option}")
        return None
    value = argv[index + 1]
    if not isinstance(value, str):
        errors.append(f"{label} value for {option} must be a string")
        return None
    return value


def _append_path_errors(
    errors: list[str],
    *,
    label: str,
    value: Any,
    approved_root: str,
    expected_path: str | None = None,
) -> None:
    if not isinstance(value, str):
        errors.append(f"{label} must be a path string")
        return
    errors.extend(_path_guard_errors(label, Path(value), approved_root=approved_root))
    if expected_path is not None and value != expected_path:
        errors.append(f"{label} must be {expected_path}")


def _validate_provider_command_preview(preview: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(preview, dict):
        return ["provider command preview must be an object"]
    scenario_id = preview.get("scenario_id")
    scenario_label = scenario_id if isinstance(scenario_id, str) and scenario_id else "<missing>"
    if scenario_id not in EXPECTED_SCENARIO_IDS:
        return [f"provider command scenario_id is not approved: {scenario_label}"]
    if preview.get("scenario_kind") != EXPECTED_SCENARIO_KINDS[scenario_id]:
        errors.append(f"provider command scenario_kind for {scenario_id} must be {EXPECTED_SCENARIO_KINDS[scenario_id]}")

    argv = preview.get("argv")
    if not isinstance(argv, list):
        errors.append(f"provider command for {scenario_id} must include argv")
        return errors
    if len(argv) != 20:
        errors.append(f"provider argv for {scenario_id} must contain 20 items")
    if len(argv) >= 2 and argv[:2] != [FUNASR_PYTHON, FUNASR_SCRIPT]:
        errors.append(f"provider argv executable for {scenario_id} must match local FunASR script")
    if len(argv) >= 3:
        _append_path_errors(
            errors,
            label=f"provider argv audio path for {scenario_id}",
            value=argv[2],
            approved_root=APPROVED_AUDIO_ROOT,
            expected_path=_expected_audio_path(scenario_id),
        )
    local_model_value = _option_value(argv, "--local-model-dir", label=f"provider argv for {scenario_id}", errors=errors)
    if isinstance(local_model_value, str) and not (
        local_model_value.startswith(LOCAL_MODEL_PLACEHOLDER_PREFIX)
        and local_model_value.endswith(">")
    ):
        errors.append(f"provider argv local-model-dir for {scenario_id} must use approved placeholder")
    events_output = _option_value(argv, "--events-output", label=f"provider argv for {scenario_id}", errors=errors)
    if events_output is not None:
        _append_path_errors(
            errors,
            label=f"provider argv events-output for {scenario_id}",
            value=events_output,
            approved_root=APPROVED_EVENTS_ROOT,
            expected_path=_expected_events_path(scenario_id),
        )
    expected_tail = [
        "--streaming",
        "--model",
        MODEL_ALIAS,
        "--local-model-dir",
        local_model_value,
        "--device",
        DEVICE,
        "--chunk-size",
        CHUNK_SIZE,
        "--encoder-chunk-look-back",
        ENCODER_CHUNK_LOOK_BACK,
        "--decoder-chunk-look-back",
        DECODER_CHUNK_LOOK_BACK,
        "--final-window-ms",
        FINAL_WINDOW_MS,
        "--events-output",
        events_output,
    ]
    if len(argv) >= 20 and argv[3:] != expected_tail:
        errors.append(f"provider argv options for {scenario_id} must match approved FunASR smoke command")
    _append_path_errors(
        errors,
        label=f"provider stdout_redirect_path for {scenario_id}",
        value=preview.get("stdout_redirect_path"),
        approved_root=APPROVED_REPORT_ROOT,
        expected_path=_expected_provider_path(scenario_id),
    )
    return errors


def _validate_transcript_report_argv(scenario_id: str, argv: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(argv, list):
        return [f"transcript report command for {scenario_id} must include transcript_report_argv"]
    if len(argv) != 10:
        errors.append(f"transcript report argv for {scenario_id} must contain 10 items")
    if len(argv) >= 2 and argv[:2] != [PYTHON, TRANSCRIPT_REPORT_SCRIPT]:
        errors.append(f"transcript report executable for {scenario_id} must match approved script")
    audio_path = _option_value(argv, "--audio", label=f"transcript report argv for {scenario_id}", errors=errors)
    if audio_path is not None:
        _append_path_errors(
            errors,
            label=f"transcript audio path for {scenario_id}",
            value=audio_path,
            approved_root=APPROVED_AUDIO_ROOT,
            expected_path=_expected_audio_path(scenario_id),
        )
    provider_json = _option_value(argv, "--provider-json", label=f"transcript report argv for {scenario_id}", errors=errors)
    if provider_json is not None:
        _append_path_errors(
            errors,
            label=f"transcript provider-json for {scenario_id}",
            value=provider_json,
            approved_root=APPROVED_REPORT_ROOT,
            expected_path=_expected_provider_path(scenario_id),
        )
    glossary_path = _option_value(argv, "--glossary", label=f"transcript report argv for {scenario_id}", errors=errors)
    if glossary_path != TECHNICAL_GLOSSARY_PATH:
        errors.append(f"transcript glossary for {scenario_id} must be {TECHNICAL_GLOSSARY_PATH}")
    output_path = _option_value(argv, "--output", label=f"transcript report argv for {scenario_id}", errors=errors)
    if output_path is not None:
        _append_path_errors(
            errors,
            label=f"transcript output for {scenario_id}",
            value=output_path,
            approved_root=APPROVED_REPORT_ROOT,
            expected_path=_expected_transcript_path(scenario_id),
        )
    return errors


def _validate_smoke_report_argv(scenario_id: str, argv: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(argv, list):
        return [f"smoke report command for {scenario_id} must include smoke_report_argv"]
    if len(argv) != 10:
        errors.append(f"smoke report argv for {scenario_id} must contain 10 items")
    if len(argv) >= 2 and argv[:2] != [PYTHON, SYNTHETIC_ASR_SMOKE_REPORT_SCRIPT]:
        errors.append(f"smoke report executable for {scenario_id} must match approved script")
    provider_json = _option_value(argv, "--provider-json", label=f"smoke report argv for {scenario_id}", errors=errors)
    if provider_json is not None:
        _append_path_errors(
            errors,
            label=f"smoke provider-json for {scenario_id}",
            value=provider_json,
            approved_root=APPROVED_REPORT_ROOT,
            expected_path=_expected_provider_path(scenario_id),
        )
    transcript_report = _option_value(argv, "--transcript-report", label=f"smoke report argv for {scenario_id}", errors=errors)
    if transcript_report is not None:
        _append_path_errors(
            errors,
            label=f"smoke transcript-report for {scenario_id}",
            value=transcript_report,
            approved_root=APPROVED_REPORT_ROOT,
            expected_path=_expected_transcript_path(scenario_id),
        )
    events_json = _option_value(argv, "--events-json", label=f"smoke report argv for {scenario_id}", errors=errors)
    if events_json is not None:
        _append_path_errors(
            errors,
            label=f"smoke events-json for {scenario_id}",
            value=events_json,
            approved_root=APPROVED_EVENTS_ROOT,
            expected_path=_expected_events_path(scenario_id),
        )
    script_json = _option_value(argv, "--script-json", label=f"smoke report argv for {scenario_id}", errors=errors)
    if script_json is not None:
        _append_path_errors(
            errors,
            label=f"smoke script-json for {scenario_id}",
            value=script_json,
            approved_root=APPROVED_SYNTHETIC_SCRIPT_ROOT,
            expected_path=_expected_script_json_path(scenario_id),
        )
    return errors


def _validate_postprocess_command_preview(preview: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(preview, dict):
        return ["postprocess command preview must be an object"]
    scenario_id = preview.get("scenario_id")
    scenario_label = scenario_id if isinstance(scenario_id, str) and scenario_id else "<missing>"
    if scenario_id not in EXPECTED_SCENARIO_IDS:
        return [f"postprocess command scenario_id is not approved: {scenario_label}"]
    if preview.get("scenario_kind") != EXPECTED_SCENARIO_KINDS[scenario_id]:
        errors.append(f"postprocess command scenario_kind for {scenario_id} must be {EXPECTED_SCENARIO_KINDS[scenario_id]}")
    errors.extend(_validate_transcript_report_argv(scenario_id, preview.get("transcript_report_argv")))
    errors.extend(_validate_smoke_report_argv(scenario_id, preview.get("smoke_report_argv")))
    _append_path_errors(
        errors,
        label=f"smoke_report_stdout_redirect_path for {scenario_id}",
        value=preview.get("smoke_report_stdout_redirect_path"),
        approved_root=APPROVED_REPORT_ROOT,
        expected_path=_expected_smoke_path(scenario_id),
    )
    return errors


def _scenario_set_errors(label: str, previews: list[Any]) -> list[str]:
    ids = [
        preview.get("scenario_id")
        for preview in previews
        if isinstance(preview, dict)
    ]
    if ids != EXPECTED_SCENARIO_IDS:
        return [f"{label} scenario_ids must match approved DRV-045 order"]
    return []


def _load_json_from_path(path_text: str, *, label: str, approved_root: str | None = None) -> tuple[dict[str, Any] | None, list[str], str]:
    path = Path(path_text)
    errors = _path_guard_errors(label, path, approved_root=approved_root)
    if errors:
        return None, errors, "blocked"
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"{label} does not exist"], "failed"
    except json.JSONDecodeError:
        return None, [f"{label} must contain valid JSON"], "failed"
    if not isinstance(payload, dict):
        return None, [f"{label} JSON must be an object"], "failed"
    return payload, [], "read"


def _validate_packet(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("decision_id") != EXPECTED_PACKET_DECISION_ID:
        errors.append(f"decision_id must be {EXPECTED_PACKET_DECISION_ID}")
    if packet.get("packet_mode") != "funasr_synthetic_smoke_execution_packet":
        errors.append("packet_mode must be funasr_synthetic_smoke_execution_packet")
    if packet.get("packet_version") != EXPECTED_PACKET_VERSION:
        errors.append(f"packet_version must be {EXPECTED_PACKET_VERSION}")
    if packet.get("packet_status") != APPROVED_PACKET_STATUS:
        errors.append(f"packet_status must be {APPROVED_PACKET_STATUS}")
    if packet.get("execution_approval_status") != "not_approved_manual_run_only":
        errors.append("execution_approval_status must be not_approved_manual_run_only before runner approval")
    if packet.get("scenario_count") != 5:
        errors.append("scenario_count must be 5")
    if packet.get("engineering_scenario_count") != 4:
        errors.append("engineering_scenario_count must be 4")
    if packet.get("negative_control_count") != 1:
        errors.append("negative_control_count must be 1")
    command_previews = packet.get("command_previews")
    postprocess_previews = packet.get("postprocess_command_previews")
    if not isinstance(command_previews, list) or len(command_previews) != 5:
        errors.append("command_previews must contain 5 provider commands")
    elif not errors:
        errors.extend(_scenario_set_errors("command_previews", command_previews))
        for preview in command_previews:
            errors.extend(_validate_provider_command_preview(preview))
    if not isinstance(postprocess_previews, list) or len(postprocess_previews) != 5:
        errors.append("postprocess_command_previews must contain 5 scenario entries")
    elif not errors:
        errors.extend(_scenario_set_errors("postprocess_command_previews", postprocess_previews))
        for preview in postprocess_previews:
            errors.extend(_validate_postprocess_command_preview(preview))
    for field in [
        "safe_to_run_asr_now",
        "safe_to_read_audio_file_now",
        "safe_to_write_artifacts_now",
        "safe_to_capture_microphone_now",
        "safe_to_call_remote_asr_now",
        "safe_to_call_llm_now",
        "safe_to_download_models_now",
    ]:
        if packet.get(field) is not False:
            errors.append(f"{field} must be false in packet")
    return errors


def _validate_approval_record(approval_record: dict[str, Any] | None, *, packet_path: str | None) -> tuple[str, list[str]]:
    if approval_record is None:
        return "not_provided", ["approval_record is required when execute=true"]
    errors: list[str] = []
    if approval_record.get("approval_record_version") != APPROVAL_RECORD_VERSION:
        errors.append(f"approval_record_version must be {APPROVAL_RECORD_VERSION}")
    if approval_record.get("approval_scope") != APPROVAL_SCOPE:
        errors.append(f"approval_scope must be {APPROVAL_SCOPE}")
    if approval_record.get("approval_token") != APPROVAL_TOKEN:
        errors.append("approval_token must match local FunASR synthetic smoke approval token")
    if approval_record.get("approval_confirmed_by_user") is not True:
        errors.append("approval_confirmed_by_user must be true")
    if packet_path is not None and approval_record.get("approved_packet_path") != packet_path:
        errors.append("approved_packet_path must match execution_packet_path")
    if approval_record.get("approved_scenario_count") != 5:
        errors.append("approved_scenario_count must be 5")
    required_true = [
        "allow_read_synthetic_audio",
        "allow_write_ignored_asr_artifacts",
        "allow_run_local_funasr",
        "deny_real_user_audio",
        "deny_microphone",
        "deny_remote_asr",
        "deny_llm",
        "deny_model_download",
    ]
    for field in required_true:
        if approval_record.get(field) is not True:
            errors.append(f"{field} must be true")
    return ("failed" if errors else "passed"), errors


def _local_model_dir_errors(local_model_dir: Path | None) -> list[str]:
    if local_model_dir is None:
        return ["local_model_dir is required when execute=true"]
    errors = _path_guard_errors("local_model_dir", local_model_dir)
    if not local_model_dir.is_absolute():
        errors.append("local_model_dir must be absolute")
    if not local_model_dir.is_dir():
        errors.append("local_model_dir is missing")
        return errors
    for filename in REQUIRED_LOCAL_MODEL_FILES:
        if not (local_model_dir / filename).is_file():
            errors.append(f"local_model_dir is missing required file: {filename}")
    return errors


def _planned_postprocess_command_count(packet: dict[str, Any]) -> int:
    count = 0
    for preview in packet.get("postprocess_command_previews") or []:
        if isinstance(preview, dict):
            if isinstance(preview.get("transcript_report_argv"), list):
                count += 1
            if isinstance(preview.get("smoke_report_argv"), list):
                count += 1
    return count


def _redacted_command_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "returncode": int(result.get("returncode", 0)),
        "stdout_path": result.get("stdout_path"),
        "stderr": str(result.get("stderr", ""))[:500],
    }


def _default_run_command(argv: list[str], *, stdout_path: str | None = None) -> dict[str, Any]:
    if stdout_path:
        stdout_errors = _path_guard_errors(
            "stdout_path",
            Path(stdout_path),
            approved_root=APPROVED_REPORT_ROOT,
        )
        if stdout_errors:
            return {
                "returncode": 1,
                "stderr": "; ".join(stdout_errors),
                "stdout_path": None,
            }
        output_path = REPO_ROOT / stdout_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as stdout_file:
            completed = subprocess.run(
                argv,
                cwd=REPO_ROOT,
                check=False,
                stdout=stdout_file,
                stderr=subprocess.PIPE,
                text=True,
            )
    else:
        completed = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    return {
        "returncode": completed.returncode,
        "stderr": completed.stderr,
        "stdout_path": stdout_path,
    }


def _provider_argv_with_local_model_dir(argv: list[str], local_model_dir: Path) -> list[str]:
    updated = list(argv)
    try:
        index = updated.index("--local-model-dir")
    except ValueError:
        return updated
    if index + 1 < len(updated) and str(updated[index + 1]).startswith(LOCAL_MODEL_PLACEHOLDER_PREFIX):
        updated[index + 1] = str(local_model_dir)
    return updated


def _execute_packet(
    *,
    packet: dict[str, Any],
    local_model_dir: Path,
    run_command: Callable[..., dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for preview in packet.get("command_previews") or []:
        argv = _provider_argv_with_local_model_dir(list(preview["argv"]), local_model_dir)
        result = run_command(argv, stdout_path=preview.get("stdout_redirect_path"))
        results.append(_redacted_command_result(result))
        if int(result.get("returncode", 1)) != 0:
            errors.append(f"provider command failed for scenario {preview.get('scenario_id')}")
            return results, errors
    for preview in packet.get("postprocess_command_previews") or []:
        transcript_result = run_command(list(preview["transcript_report_argv"]))
        results.append(_redacted_command_result(transcript_result))
        if int(transcript_result.get("returncode", 1)) != 0:
            errors.append(f"transcript report command failed for scenario {preview.get('scenario_id')}")
            return results, errors
        smoke_result = run_command(
            list(preview["smoke_report_argv"]),
            stdout_path=preview.get("smoke_report_stdout_redirect_path"),
        )
        results.append(_redacted_command_result(smoke_result))
        if int(smoke_result.get("returncode", 1)) != 0:
            errors.append(f"smoke report command failed for scenario {preview.get('scenario_id')}")
            return results, errors
    return results, errors


def build_funasr_synthetic_smoke_approved_runner_report(
    *,
    execution_packet: dict[str, Any] | None = None,
    execution_packet_path: str | None = None,
    approval_record: dict[str, Any] | None = None,
    approval_record_path: str | None = None,
    execute: bool = False,
    local_model_dir: Path | None = None,
    run_command: Callable[..., dict[str, Any]] = _default_run_command,
) -> dict[str, Any]:
    report = _base_report()
    packet = execution_packet
    packet_path_for_approval = execution_packet_path
    if execution_packet is not None and execution_packet_path is not None:
        report["runner_status"] = "blocked_invalid_packet_input"
        report["validation_errors"] = ["provide only one execution packet input source"]
        return report
    if execution_packet_path is not None:
        packet, errors, read_status = _load_json_from_path(
            execution_packet_path,
            label="execution_packet_path",
            approved_root=APPROVED_PACKET_ROOT,
        )
        report["packet_read_status"] = read_status
        if errors:
            report["runner_status"] = "blocked_by_packet_path_guard" if read_status == "blocked" else "blocked_invalid_execution_packet"
            report["validation_errors"] = errors
            return report
    elif packet is not None:
        report["packet_read_status"] = "provided_inline"
    else:
        report["runner_status"] = "blocked_missing_execution_packet"
        report["validation_errors"] = ["execution_packet is required"]
        return report

    packet_errors = _validate_packet(packet)
    report["packet_validation_status"] = "failed" if packet_errors else "passed"
    report["planned_provider_command_count"] = len(packet.get("command_previews") or [])
    report["planned_postprocess_command_count"] = _planned_postprocess_command_count(packet)
    if packet_errors:
        report["runner_status"] = "blocked_invalid_execution_packet"
        report["validation_errors"] = packet_errors
        return report

    if not execute:
        report["runner_status"] = "dry_run_ready_requires_execute_flag_and_approval"
        return report

    if approval_record_path is not None:
        approval_record, approval_errors, approval_read_status = _load_json_from_path(
            approval_record_path,
            label="approval_record_path",
            approved_root="artifacts/tmp",
        )
        if approval_errors:
            report["runner_status"] = "blocked_missing_or_invalid_execution_approval"
            report["approval_record_status"] = approval_read_status
            report["validation_errors"] = approval_errors
            return report
        if isinstance(approval_record, dict) and isinstance(
            approval_record.get("approval_record_template"),
            dict,
        ):
            approval_record = approval_record["approval_record_template"]
    approval_status, approval_errors = _validate_approval_record(
        approval_record,
        packet_path=packet_path_for_approval,
    )
    report["approval_record_status"] = approval_status
    if approval_errors:
        report["runner_status"] = "blocked_missing_or_invalid_execution_approval"
        report["validation_errors"] = approval_errors
        return report

    model_errors = _local_model_dir_errors(local_model_dir)
    report["local_model_dir_status"] = "failed" if model_errors else "passed_no_path_echo"
    if model_errors:
        report["runner_status"] = "blocked_invalid_local_model_dir"
        report["validation_errors"] = model_errors
        return report

    report["safe_to_run_asr_now"] = True
    report["safe_to_read_synthetic_audio_now"] = True
    report["safe_to_write_ignored_asr_artifacts_now"] = True
    command_results, execution_errors = _execute_packet(
        packet=packet,
        local_model_dir=local_model_dir,
        run_command=run_command,
    )
    report["command_results"] = command_results
    report["executed_command_count"] = len(command_results)
    if execution_errors:
        report["runner_status"] = "blocked_by_command_failure"
        report["validation_errors"] = execution_errors
        return report
    report["runner_status"] = "executed_local_funasr_synthetic_smoke_commands"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-packet-path")
    parser.add_argument("--approval-record-path")
    parser.add_argument("--local-model-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_approved_runner_report(
        execution_packet_path=args.execution_packet_path,
        approval_record_path=args.approval_record_path,
        local_model_dir=args.local_model_dir,
        execute=args.execute,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["runner_status"] in {
        "dry_run_ready_requires_execute_flag_and_approval",
        "executed_local_funasr_synthetic_smoke_commands",
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())

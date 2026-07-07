#!/usr/bin/env python3
"""Build a no-execution FunASR synthetic smoke execution packet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

DECISION_ID = "DRV-045"
PACKET_MODE = "funasr_synthetic_smoke_execution_packet"
PACKET_VERSION = "funasr_synthetic_smoke_execution_packet.v1"
EXECUTION_BOUNDARY = (
    "manual_packet_only_no_asr_execution_no_audio_read_no_model_download_no_remote_provider"
)

FUNASR_PYTHON = "code/asr_runtime/.venv-funasr/bin/python"
FUNASR_SCRIPT = "code/asr_runtime/scripts/transcribe_funasr.py"
PYTHON = "python3"
TRANSCRIPT_REPORT_SCRIPT = "code/asr_runtime/scripts/transcript_report.py"
SYNTHETIC_ASR_SMOKE_REPORT_SCRIPT = "tools/funasr_synthetic_smoke_single_result_builder.py"
TECHNICAL_GLOSSARY_PATH = "data/asr_eval/glossaries/technical-terms.zh.json"
SYNTHETIC_SCRIPT_ROOT = "data/asr_eval/synthetic_meetings/scripts"
MODEL_ALIAS = "paraformer-zh-streaming"
DEVICE = "cpu"
CHUNK_SIZE = "0,10,5"
ENCODER_CHUNK_LOOK_BACK = "4"
DECODER_CHUNK_LOOK_BACK = "1"
FINAL_WINDOW_MS = "3000"
EXPECTED_READINESS_VERSION = "funasr_synthetic_smoke_readiness.v1"

APPROVED_READINESS_ROOTS = ("artifacts/tmp",)
APPROVED_AUDIO_ROOT = "artifacts/tmp/synthetic_audio"
APPROVED_EVENTS_ROOT = "artifacts/tmp/asr_events"
APPROVED_REPORT_ROOT = "artifacts/tmp/asr_reports"
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
    "safe_to_execute_now",
    "safe_to_run_asr_now",
    "safe_to_download_models_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_public_audio_now",
    "safe_to_read_audio_file_now",
    "safe_to_write_artifacts_now",
]

DEFAULT_SCENARIOS = [
    ("api-review-001", "engineering"),
    ("architecture-review-001", "engineering"),
    ("incident-review-001", "engineering"),
    ("release-review-001", "engineering"),
    ("non-engineering-control-001", "negative_control"),
]
REQUIRED_SCENARIOS = {scenario_id for scenario_id, _kind in DEFAULT_SCENARIOS}
REQUIRED_NEGATIVE_CONTROL = "non-engineering-control-001"
SAFE_SCENARIO_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")
READINESS_FALSE_FIELDS = {
    "safe_to_execute_local_funasr_now": "funasr_readiness safe_to_execute_local_funasr_now must be false",
    "safe_to_download_models": "funasr_readiness safe_to_download_models must be false",
    "safe_to_read_user_audio": "funasr_readiness safe_to_read_user_audio must be false",
    "safe_to_read_configs_local": "funasr_readiness safe_to_read_configs_local must be false",
    "safe_to_call_remote_asr": "funasr_readiness safe_to_call_remote_asr must be false",
    "safe_to_call_llm": "funasr_readiness safe_to_call_llm must be false",
}


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _base_report() -> dict[str, Any]:
    return {
        "decision_id": DECISION_ID,
        "packet_mode": PACKET_MODE,
        "packet_version": PACKET_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "funasr_readiness_read_status": "not_requested",
        "packet_status": "blocked_missing_funasr_readiness",
        "execution_approval_status": "not_approved",
        "provider": None,
        "model_alias": None,
        "scenario_count": 0,
        "engineering_scenario_count": 0,
        "negative_control_count": 0,
        "scenario_execution_specs": [],
        "command_previews": [],
        "postprocess_command_previews": [],
        "expected_outputs": {
            "events_paths": [],
            "provider_output_paths": [],
            "transcript_report_paths": [],
            "smoke_report_paths": [],
        },
        "expected_drv044_batch_artifact_provenance": None,
        "validation_errors": [],
        "next_action": "provide_drv043_funasr_readiness_evidence",
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


def _readiness_path_errors(path: Path) -> list[str]:
    for candidate in (path, path.resolve(strict=False)):
        if candidate.suffix.casefold() == ".m4a":
            return ["funasr_readiness_path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"funasr_readiness_path is blocked: {label}"]
    relative = _repo_relative_path(path)
    if relative is None:
        return ["funasr_readiness_path must be under approved artifacts root"]
    relative_text = relative.as_posix()
    if not any(
        relative_text == root or relative_text.startswith(f"{root}/")
        for root in APPROVED_READINESS_ROOTS
    ):
        return ["funasr_readiness_path must be under approved artifacts root"]
    if path.suffix.casefold() != ".json":
        return ["funasr_readiness_path must be a JSON file"]
    return []


def _load_readiness_from_path(path_text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    path = Path(path_text)
    errors = _readiness_path_errors(path)
    if errors:
        return None, errors, "blocked"
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["funasr_readiness_path does not exist"], "failed"
    except json.JSONDecodeError:
        return None, ["funasr_readiness_path must contain valid JSON"], "failed"
    if not isinstance(payload, dict):
        return None, ["funasr_readiness_path JSON must be an object"], "failed"
    return payload, [], "read"


def _load_readiness_from_text(text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, ["funasr_readiness_json must contain valid JSON"], "failed"
    if not isinstance(payload, dict):
        return None, ["funasr_readiness_json must be an object"], "failed"
    return payload, [], "inline_json"


def _safe_label(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    lowered = value.replace("\\", "/").casefold()
    if lowered.startswith("/") or "/users/" in lowered or ".m4a" in lowered:
        return False
    return not any(label in lowered for label, _parts in FORBIDDEN_PATH_LABELS)


def _validate_readiness(readiness: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if readiness.get("report_mode") != "funasr_synthetic_smoke_readiness":
        errors.append("funasr_readiness report_mode must be funasr_synthetic_smoke_readiness")
    if readiness.get("report_version") != EXPECTED_READINESS_VERSION:
        errors.append("funasr_readiness report_version must be funasr_synthetic_smoke_readiness.v1")
    if readiness.get("readiness_status") != "cache_preflight_passed_offline_execution_not_proven":
        errors.append("funasr_readiness_status must be cache_preflight_passed_offline_execution_not_proven")
    if readiness.get("provider") != "funasr_streaming":
        errors.append("funasr_readiness provider must be funasr_streaming")
    if readiness.get("model_alias") != MODEL_ALIAS:
        errors.append("funasr_readiness model_alias must be paraformer-zh-streaming")
    if readiness.get("required_cached_models_status") != "present":
        errors.append("funasr_readiness required_cached_models_status must be present")
    if readiness.get("offline_guard_status") != "required_before_execution":
        errors.append("funasr_readiness offline_guard_status must be required_before_execution")
    if readiness.get("model_download_status") != "not_started":
        errors.append("funasr_readiness model_download_status must be not_started")
    if readiness.get("execution_mode") != "preflight_only_no_execution_authorization":
        errors.append("funasr_readiness execution_mode must be preflight_only_no_execution_authorization")
    if readiness.get("validation_errors") not in ([], None):
        errors.append("funasr_readiness validation_errors must be empty")
    if not _safe_label(readiness.get("local_model_dir_label")):
        errors.append("funasr_readiness local_model_dir_label is unsafe")
    if not _safe_label(readiness.get("venv_python")):
        errors.append("funasr_readiness venv_python is unsafe")
    if not _safe_label(readiness.get("funasr_script")):
        errors.append("funasr_readiness funasr_script is unsafe")

    for field, message in READINESS_FALSE_FIELDS.items():
        if readiness.get(field) is not False:
            errors.append(message)
    return errors


def _scenario_guard_errors(scenario_ids: list[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for scenario_id in scenario_ids:
        if (
            not isinstance(scenario_id, str)
            or not scenario_id
            or any(character not in SAFE_SCENARIO_CHARS for character in scenario_id)
        ):
            return [f"scenario_id is unsafe: {scenario_id}"]
        lowered = scenario_id.casefold()
        if any(label in lowered for label, _parts in FORBIDDEN_PATH_LABELS):
            return [f"scenario_id is unsafe: {scenario_id}"]
        if scenario_id not in REQUIRED_SCENARIOS:
            return [f"scenario_id is not approved for DRV-045 batch: {scenario_id}"]
        if scenario_id in seen:
            return [f"scenario_id is duplicated: {scenario_id}"]
        seen.add(scenario_id)
    return errors


def _required_scenario_errors(scenario_ids: list[str]) -> list[str]:
    scenario_set = set(scenario_ids)
    errors = [
        f"missing required engineering scenario: {scenario_id}"
        for scenario_id, kind in DEFAULT_SCENARIOS
        if kind == "engineering" and scenario_id not in scenario_set
    ]
    if REQUIRED_NEGATIVE_CONTROL not in scenario_set:
        errors.append(f"missing required negative-control scenario: {REQUIRED_NEGATIVE_CONTROL}")
    return errors


def _path_for(root: str, scenario_id: str, suffix: str) -> str:
    return f"{root}/{scenario_id}{suffix}"


def _argv_for(readiness: dict[str, Any], scenario_id: str, events_path: str) -> list[str]:
    return [
        str(readiness.get("venv_python") or FUNASR_PYTHON),
        str(readiness.get("funasr_script") or FUNASR_SCRIPT),
        _path_for(APPROVED_AUDIO_ROOT, scenario_id, ".wav"),
        "--streaming",
        "--model",
        MODEL_ALIAS,
        "--local-model-dir",
        f"<{readiness.get('local_model_dir_label')}>",
        "--device",
        str(readiness.get("device") or DEVICE),
        "--chunk-size",
        CHUNK_SIZE,
        "--encoder-chunk-look-back",
        ENCODER_CHUNK_LOOK_BACK,
        "--decoder-chunk-look-back",
        DECODER_CHUNK_LOOK_BACK,
        "--final-window-ms",
        FINAL_WINDOW_MS,
        "--events-output",
        events_path,
    ]


def _scenario_kind(scenario_id: str) -> str:
    return dict(DEFAULT_SCENARIOS)[scenario_id]


def _script_json_path_for(scenario_id: str) -> str:
    stem = scenario_id.removesuffix("-001")
    return f"{SYNTHETIC_SCRIPT_ROOT}/{stem}.json"


def _transcript_report_argv(audio_path: str, provider_output_path: str, transcript_report_path: str) -> list[str]:
    return [
        PYTHON,
        TRANSCRIPT_REPORT_SCRIPT,
        "--audio",
        audio_path,
        "--provider-json",
        provider_output_path,
        "--glossary",
        TECHNICAL_GLOSSARY_PATH,
        "--output",
        transcript_report_path,
    ]


def _smoke_report_argv(
    provider_output_path: str,
    transcript_report_path: str,
    events_path: str,
    script_json_path: str,
) -> list[str]:
    return [
        PYTHON,
        SYNTHETIC_ASR_SMOKE_REPORT_SCRIPT,
        "--provider-json",
        provider_output_path,
        "--transcript-report",
        transcript_report_path,
        "--events-json",
        events_path,
        "--script-json",
        script_json_path,
    ]


def _build_ready_packet(report: dict[str, Any], readiness: dict[str, Any], scenario_ids: list[str]) -> dict[str, Any]:
    scenario_execution_specs: list[dict[str, Any]] = []
    command_previews: list[dict[str, Any]] = []
    postprocess_command_previews: list[dict[str, Any]] = []
    expected_outputs = {
        "events_paths": [],
        "provider_output_paths": [],
        "transcript_report_paths": [],
        "smoke_report_paths": [],
    }
    provenance_artifacts: list[dict[str, str]] = []

    for scenario_id in scenario_ids:
        events_path = _path_for(APPROVED_EVENTS_ROOT, scenario_id, ".funasr.events.json")
        provider_output_path = _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.provider.json")
        transcript_report_path = _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.transcript-report.json")
        smoke_report_path = _path_for(APPROVED_REPORT_ROOT, scenario_id, ".funasr.smoke-report.json")
        audio_path = _path_for(APPROVED_AUDIO_ROOT, scenario_id, ".wav")
        script_json_path = _script_json_path_for(scenario_id)
        kind = _scenario_kind(scenario_id)
        spec = {
            "scenario_id": scenario_id,
            "scenario_kind": kind,
            "audio_path": audio_path,
            "events_output_path": events_path,
            "provider_output_path": provider_output_path,
            "transcript_report_path": transcript_report_path,
            "smoke_report_path": smoke_report_path,
            "script_json_path": script_json_path,
        }
        scenario_execution_specs.append(spec)
        command_previews.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": kind,
                "argv": _argv_for(readiness, scenario_id, events_path),
                "stdout_redirect_path": provider_output_path,
            }
        )
        postprocess_command_previews.append(
            {
                "scenario_id": scenario_id,
                "scenario_kind": kind,
                "transcript_report_argv": _transcript_report_argv(
                    audio_path,
                    provider_output_path,
                    transcript_report_path,
                ),
                "smoke_report_argv": _smoke_report_argv(
                    provider_output_path,
                    transcript_report_path,
                    events_path,
                    script_json_path,
                ),
                "smoke_report_stdout_redirect_path": smoke_report_path,
            }
        )
        expected_outputs["events_paths"].append(events_path)
        expected_outputs["provider_output_paths"].append(provider_output_path)
        expected_outputs["transcript_report_paths"].append(transcript_report_path)
        expected_outputs["smoke_report_paths"].append(smoke_report_path)
        provenance_artifacts.append(
            {
                "artifact_kind": "funasr_synthetic_smoke_result_report",
                "scenario_id": scenario_id,
                "path": smoke_report_path,
                "sha256_source": "compute_after_manual_run",
            }
        )

    report.update(
        {
            "funasr_readiness_read_status": report.get("funasr_readiness_read_status")
            if report.get("funasr_readiness_read_status") != "not_requested"
            else "provided_inline",
            "packet_status": "ready_for_manual_batch_funasr_synthetic_smoke_run",
            "execution_approval_status": "not_approved_manual_run_only",
            "provider": "funasr_streaming",
            "model_alias": MODEL_ALIAS,
            "scenario_count": len(scenario_execution_specs),
            "engineering_scenario_count": sum(
                1 for spec in scenario_execution_specs if spec["scenario_kind"] == "engineering"
            ),
            "negative_control_count": sum(
                1 for spec in scenario_execution_specs if spec["scenario_kind"] == "negative_control"
            ),
            "scenario_execution_specs": scenario_execution_specs,
            "command_previews": command_previews,
            "postprocess_command_previews": postprocess_command_previews,
            "expected_outputs": expected_outputs,
            "expected_drv044_batch_artifact_provenance": {
                "source_kind": "local_funasr_synthetic_smoke_artifacts",
                "artifacts": provenance_artifacts,
            },
            "next_action": "manual_user_run_each_command_then_compute_sha256_and_submit_drv044_batch_evidence",
        }
    )
    return report


def build_funasr_synthetic_smoke_execution_packet(
    *,
    funasr_readiness_report: dict[str, Any] | None = None,
    funasr_readiness_path: str | None = None,
    funasr_readiness_json: str | None = None,
    scenario_ids: list[str] | None = None,
) -> dict[str, Any]:
    report = _base_report()
    if sum(value is not None for value in (funasr_readiness_report, funasr_readiness_path, funasr_readiness_json)) > 1:
        report["packet_status"] = "blocked_invalid_funasr_readiness_input"
        report["validation_errors"] = ["provide only one funasr readiness input source"]
        report["next_action"] = "fix_funasr_readiness_input"
        return report

    readiness = funasr_readiness_report
    if funasr_readiness_path is not None:
        readiness, errors, read_status = _load_readiness_from_path(funasr_readiness_path)
        report["funasr_readiness_read_status"] = read_status
        if errors:
            report["packet_status"] = "blocked_by_readiness_path_guard" if read_status == "blocked" else "blocked_invalid_funasr_readiness"
            report["validation_errors"] = errors
            report["next_action"] = "fix_funasr_readiness_input"
            return report
    elif funasr_readiness_json is not None:
        readiness, errors, read_status = _load_readiness_from_text(funasr_readiness_json)
        report["funasr_readiness_read_status"] = read_status
        if errors:
            report["packet_status"] = "blocked_invalid_funasr_readiness"
            report["validation_errors"] = errors
            report["next_action"] = "fix_funasr_readiness_input"
            return report
    elif readiness is not None:
        report["funasr_readiness_read_status"] = "provided_inline"

    if readiness is None:
        return report

    readiness_errors = _validate_readiness(readiness)
    if readiness_errors:
        report["packet_status"] = "blocked_invalid_funasr_readiness"
        report["validation_errors"] = readiness_errors
        report["next_action"] = "fix_drv043_funasr_readiness_evidence"
        return report

    selected_scenario_ids = scenario_ids if scenario_ids is not None else [scenario_id for scenario_id, _kind in DEFAULT_SCENARIOS]
    scenario_guard_errors = _scenario_guard_errors(selected_scenario_ids)
    if scenario_guard_errors:
        report["packet_status"] = "blocked_by_scenario_guard"
        report["validation_errors"] = scenario_guard_errors
        report["next_action"] = "fix_drv045_scenario_set"
        return report

    required_errors = _required_scenario_errors(selected_scenario_ids)
    if required_errors:
        report["packet_status"] = "blocked_missing_required_scenarios"
        report["validation_errors"] = required_errors
        report["next_action"] = "include_full_drv045_engineering_and_negative_control_scenario_set"
        return report

    return _build_ready_packet(report, readiness, selected_scenario_ids)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--funasr-readiness-path")
    parser.add_argument("--funasr-readiness-json")
    parser.add_argument("--scenario-id", action="append", dest="scenario_ids")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_execution_packet(
        funasr_readiness_path=args.funasr_readiness_path,
        funasr_readiness_json=args.funasr_readiness_json,
        scenario_ids=args.scenario_ids,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 0 if report["packet_status"] == "ready_for_manual_batch_funasr_synthetic_smoke_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())

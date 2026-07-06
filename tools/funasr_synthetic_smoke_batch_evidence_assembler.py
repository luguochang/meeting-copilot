#!/usr/bin/env python3
"""Assemble manual FunASR smoke artifacts into DRV-044 batch evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, TextIO


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import funasr_synthetic_smoke_result_evidence  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]

DECISION_ID = "DRV-046"
ASSEMBLY_MODE = "funasr_synthetic_smoke_batch_evidence_assembler"
ASSEMBLY_VERSION = "funasr_synthetic_smoke_batch_evidence_assembler.v1"
EXECUTION_BOUNDARY = (
    "artifact_assembly_only_no_asr_execution_no_audio_read_no_model_download_no_remote_provider"
)
EXPECTED_PACKET_ID = "DRV-045"
EXPECTED_PACKET_MODE = "funasr_synthetic_smoke_execution_packet"
EXPECTED_PACKET_VERSION = "funasr_synthetic_smoke_execution_packet.v1"
APPROVED_PACKET_ROOTS = ("artifacts/tmp",)
APPROVED_SMOKE_REPORT_ROOT = "artifacts/tmp/asr_reports"
EXPECTED_SOURCE_KIND = "local_funasr_synthetic_smoke_artifacts"
EXPECTED_SCENARIOS = {
    "api-review-001",
    "architecture-review-001",
    "incident-review-001",
    "release-review-001",
    "non-engineering-control-001",
}
FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
FALSE_SAFETY_FLAGS = [
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
PACKET_FALSE_FLAGS = [
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


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _base_report() -> dict[str, Any]:
    return {
        "decision_id": DECISION_ID,
        "assembly_mode": ASSEMBLY_MODE,
        "assembly_version": ASSEMBLY_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "execution_packet_read_status": "not_requested",
        "assembly_status": "blocked_missing_drv045_execution_packet",
        "artifact_read_status": "not_requested",
        "artifact_count": 0,
        "assembled_evidence_report": None,
        "drv044_gate_report": None,
        "counts_as_asr_quality_go_evidence": False,
        "counts_as_real_mic_go_evidence": False,
        "validation_errors": [],
        "next_action": "provide_drv045_execution_packet_after_manual_smoke_run",
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
        return ["execution_packet_path must be under approved artifacts root"]
    relative_text = relative.as_posix()
    if not any(
        relative_text == root or relative_text.startswith(f"{root}/")
        for root in APPROVED_PACKET_ROOTS
    ):
        return ["execution_packet_path must be under approved artifacts root"]
    if path.suffix.casefold() != ".json":
        return ["execution_packet_path must be a JSON file"]
    return []


def _smoke_artifact_path_errors(path_text: str) -> list[str]:
    path = Path(path_text)
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    relative = _repo_relative_path(path)
    resolved_relative = _repo_relative_path(resolved)
    for candidate in (path, resolved):
        if candidate.suffix.casefold() == ".m4a":
            return ["artifact path is blocked: audio file"]
    if relative is None or resolved_relative is None:
        return ["artifact path is outside repository"]
    relative_text = relative.as_posix()
    resolved_text = resolved_relative.as_posix()
    if not (
        (relative_text == APPROVED_SMOKE_REPORT_ROOT or relative_text.startswith(f"{APPROVED_SMOKE_REPORT_ROOT}/"))
        and (
            resolved_text == APPROVED_SMOKE_REPORT_ROOT
            or resolved_text.startswith(f"{APPROVED_SMOKE_REPORT_ROOT}/")
        )
    ):
        return [f"artifact path is not under approved root: {APPROVED_SMOKE_REPORT_ROOT}"]
    for candidate in (path, resolved):
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"artifact path is blocked: {label}"]
    if path.suffix.casefold() != ".json":
        return ["artifact path must be a JSON file"]
    return []


def _display_path(path_text: str) -> str:
    relative = _repo_relative_path(Path(path_text))
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _load_packet_from_path(path_text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    path = Path(path_text)
    path_errors = _packet_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked"
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


def _load_packet_from_text(text: str) -> tuple[dict[str, Any] | None, list[str], str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None, ["execution_packet_json must contain valid JSON"], "failed"
    if not isinstance(payload, dict):
        return None, ["execution_packet_json must be an object"], "failed"
    return payload, [], "inline_json"


def _validate_packet(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if packet.get("decision_id") != EXPECTED_PACKET_ID:
        errors.append("execution packet decision_id must be DRV-045")
    if packet.get("packet_mode") != EXPECTED_PACKET_MODE:
        errors.append("execution packet packet_mode must be funasr_synthetic_smoke_execution_packet")
    if packet.get("packet_version") != EXPECTED_PACKET_VERSION:
        errors.append("execution packet packet_version must be funasr_synthetic_smoke_execution_packet.v1")
    if packet.get("packet_status") != "ready_for_manual_batch_funasr_synthetic_smoke_run":
        errors.append("execution packet must be ready_for_manual_batch_funasr_synthetic_smoke_run")
    if packet.get("execution_approval_status") != "not_approved_manual_run_only":
        errors.append("execution packet approval must be not_approved_manual_run_only")
    if packet.get("provider") != "funasr_streaming":
        errors.append("execution packet provider must be funasr_streaming")
    if packet.get("model_alias") != "paraformer-zh-streaming":
        errors.append("execution packet model_alias must be paraformer-zh-streaming")
    if packet.get("scenario_count") != 5:
        errors.append("execution packet scenario_count must be 5")
    if packet.get("engineering_scenario_count") != 4:
        errors.append("execution packet engineering_scenario_count must be 4")
    if packet.get("negative_control_count") != 1:
        errors.append("execution packet negative_control_count must be 1")
    if packet.get("validation_errors") not in ([], None):
        errors.append("execution packet validation_errors must be empty")
    for flag in PACKET_FALSE_FLAGS:
        if packet.get(flag) is not False:
            errors.append(f"execution packet {flag} must be false")

    expected_outputs = packet.get("expected_outputs")
    if not isinstance(expected_outputs, dict):
        errors.append("execution packet expected_outputs must be an object")
        smoke_paths: list[Any] = []
    else:
        smoke_paths = expected_outputs.get("smoke_report_paths", [])
        if not isinstance(smoke_paths, list):
            errors.append("execution packet smoke_report_paths must be a list")
            smoke_paths = []

    provenance = packet.get("expected_drv044_batch_artifact_provenance")
    if not isinstance(provenance, dict):
        errors.append("execution packet expected_drv044_batch_artifact_provenance must be an object")
        artifacts: list[Any] = []
    else:
        if provenance.get("source_kind") != EXPECTED_SOURCE_KIND:
            errors.append("execution packet provenance source_kind must be local_funasr_synthetic_smoke_artifacts")
        artifacts = provenance.get("artifacts", [])
        if not isinstance(artifacts, list):
            errors.append("execution packet provenance artifacts must be a list")
            artifacts = []

    if len(smoke_paths) != 5:
        errors.append("execution packet smoke_report_paths must contain 5 paths")
    if len(artifacts) != 5:
        errors.append("execution packet provenance artifacts must contain 5 entries")

    scenario_ids: set[str] = set()
    artifact_paths: list[str] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("execution packet provenance artifact entries must be objects")
            continue
        scenario_id = artifact.get("scenario_id")
        path_text = artifact.get("path")
        if not isinstance(scenario_id, str):
            errors.append("execution packet artifact scenario_id must be a string")
        else:
            scenario_ids.add(scenario_id)
        if not isinstance(path_text, str):
            errors.append("execution packet artifact path must be a string")
            continue
        artifact_paths.append(path_text)
        errors.extend(_smoke_artifact_path_errors(path_text))
        if artifact.get("sha256_source") != "compute_after_manual_run":
            errors.append("execution packet artifact sha256_source must be compute_after_manual_run")
    if scenario_ids and scenario_ids != EXPECTED_SCENARIOS:
        errors.append("execution packet scenario ids must match DRV-045 default batch")
    if smoke_paths and artifact_paths and set(smoke_paths) != set(artifact_paths):
        errors.append("execution packet smoke_report_paths must match provenance artifact paths")
    return errors


def _read_artifacts(packet: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, str]], list[str]]:
    artifacts = packet["expected_drv044_batch_artifact_provenance"]["artifacts"]
    scenario_results: list[dict[str, Any]] = []
    provenance_artifacts: list[dict[str, str]] = []
    errors: list[str] = []
    for artifact in artifacts:
        path_text = artifact["path"]
        path_errors = _smoke_artifact_path_errors(path_text)
        if path_errors:
            errors.extend(path_errors)
            continue
        resolved = REPO_ROOT / path_text
        try:
            artifact_bytes = resolved.read_bytes()
        except FileNotFoundError:
            errors.append(f"manual smoke artifact missing: {_display_path(path_text)}")
            continue
        except OSError:
            errors.append(f"manual smoke artifact could not be read: {_display_path(path_text)}")
            continue
        try:
            payload = json.loads(artifact_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            errors.append(f"manual smoke artifact must be valid JSON: {_display_path(path_text)}")
            continue
        if not isinstance(payload, dict):
            errors.append(f"manual smoke artifact JSON must be an object: {_display_path(path_text)}")
            continue
        scenario_payloads = payload.get("scenario_results")
        if (
            not isinstance(scenario_payloads, list)
            or len(scenario_payloads) != 1
            or not isinstance(scenario_payloads[0], dict)
        ):
            errors.append(f"manual smoke artifact must contain exactly one scenario_result: {_display_path(path_text)}")
            continue
        scenario_result = scenario_payloads[0]
        if scenario_result.get("scenario_id") != artifact.get("scenario_id"):
            errors.append(f"manual smoke artifact scenario_id mismatch: {_display_path(path_text)}")
            continue
        scenario_results.append(scenario_result)
        provenance_artifacts.append(
            {
                "artifact_kind": "scenario_gate_report",
                "scenario_id": artifact["scenario_id"],
                "path": _display_path(path_text),
                "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            }
        )
    return scenario_results, provenance_artifacts, errors


def _assemble_evidence(
    scenario_results: list[dict[str, Any]],
    provenance_artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "manifest_version": "funasr_synthetic_smoke_result.v1",
        "evidence_kind": "batch_synthetic_confirmation",
        "provider": "funasr_streaming",
        "model_alias": "paraformer-zh-streaming",
        "source_boundary": "synthetic_audio_no_user_audio",
        "scenario_results": scenario_results,
        "batch_artifact_provenance": {
            "source_kind": EXPECTED_SOURCE_KIND,
            "artifacts": provenance_artifacts,
        },
    }


def _run_drv044_gate(assembled_evidence: dict[str, Any]) -> dict[str, Any]:
    funasr_synthetic_smoke_result_evidence.REPO_ROOT = REPO_ROOT
    return funasr_synthetic_smoke_result_evidence.build_funasr_synthetic_smoke_result_evidence_gate(
        evidence_report=assembled_evidence,
    )


def build_funasr_synthetic_smoke_batch_evidence_assembly(
    *,
    execution_packet: dict[str, Any] | None = None,
    execution_packet_path: str | None = None,
    execution_packet_json: str | None = None,
) -> dict[str, Any]:
    report = _base_report()
    input_count = sum(value is not None for value in (execution_packet, execution_packet_path, execution_packet_json))
    if input_count > 1:
        report["assembly_status"] = "blocked_invalid_execution_packet_input"
        report["validation_errors"] = ["provide only one execution packet input source"]
        report["next_action"] = "fix_drv045_execution_packet_input"
        return report

    packet = execution_packet
    if execution_packet_path is not None:
        packet, errors, read_status = _load_packet_from_path(execution_packet_path)
        report["execution_packet_read_status"] = read_status
        if errors:
            report["assembly_status"] = "blocked_by_packet_path_guard" if read_status == "blocked" else "blocked_invalid_drv045_execution_packet"
            report["validation_errors"] = errors
            report["next_action"] = "fix_drv045_execution_packet_input"
            return report
    elif execution_packet_json is not None:
        packet, errors, read_status = _load_packet_from_text(execution_packet_json)
        report["execution_packet_read_status"] = read_status
        if errors:
            report["assembly_status"] = "blocked_invalid_drv045_execution_packet"
            report["validation_errors"] = errors
            report["next_action"] = "fix_drv045_execution_packet_input"
            return report
    elif packet is not None:
        report["execution_packet_read_status"] = "provided_inline"

    if packet is None:
        return report

    packet_errors = _validate_packet(packet)
    if packet_errors:
        report["assembly_status"] = "blocked_invalid_drv045_execution_packet"
        report["validation_errors"] = packet_errors
        report["next_action"] = "fix_drv045_execution_packet"
        return report

    scenario_results, provenance_artifacts, artifact_errors = _read_artifacts(packet)
    if artifact_errors:
        report["artifact_read_status"] = "blocked_or_missing"
        report["assembly_status"] = "blocked_missing_manual_smoke_artifacts"
        report["artifact_count"] = len(provenance_artifacts)
        report["validation_errors"] = artifact_errors
        report["next_action"] = "run_drv045_manual_commands_or_fix_smoke_artifacts"
        return report

    assembled_evidence = _assemble_evidence(scenario_results, provenance_artifacts)
    drv044_gate_report = _run_drv044_gate(assembled_evidence)
    gate_quality_status = drv044_gate_report.get("quality_evidence_status")
    gate_counts_as_go = drv044_gate_report.get("counts_as_asr_quality_go_evidence") is True
    report.update(
        {
            "artifact_read_status": "read",
            "artifact_count": len(provenance_artifacts),
            "assembled_evidence_report": assembled_evidence,
            "drv044_gate_report": drv044_gate_report,
            "counts_as_asr_quality_go_evidence": gate_counts_as_go,
            "counts_as_real_mic_go_evidence": False,
            "assembly_status": "drv044_batch_evidence_validated"
            if gate_quality_status == "funasr_synthetic_smoke_quality_batch_confirmed"
            and gate_counts_as_go
            else "drv044_batch_evidence_blocked",
            "next_action": "submit_drv044_gate_report_to_drv032_asr_quality_decision"
            if gate_counts_as_go
            else "fix_manual_funasr_smoke_quality_or_provenance",
        }
    )
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-packet-path")
    parser.add_argument("--execution-packet-json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_batch_evidence_assembly(
        execution_packet_path=args.execution_packet_path,
        execution_packet_json=args.execution_packet_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 0 if report["assembly_status"] == "drv044_batch_evidence_validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate FunASR synthetic smoke result evidence without running ASR."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]

DECISION_ID = "DRV-044"
REPORT_MODE = "funasr_synthetic_smoke_result_evidence_gate"
SCHEMA_VERSION = "funasr_synthetic_smoke_result.v1"
SCHEMA_STATUS = "specified_not_executable"
EXECUTION_BOUNDARY = "evidence_only_no_asr_execution_no_audio_read_no_download"
APPROVED_EVIDENCE_REPORT_ROOT = "artifacts/tmp/asr_reports"
APPROVED_BATCH_ARTIFACT_ROOTS = (
    "artifacts/tmp/asr_reports",
    "artifacts/tmp/asr_events",
)
PROVIDER = "funasr_streaming"
ALLOWED_EVIDENCE_KINDS = {
    "single_synthetic_smoke",
    "batch_synthetic_confirmation",
}
ALLOWED_SCENARIO_KINDS = {
    "engineering",
    "negative_control",
}
ALLOWED_INPUT_SOURCE_KINDS = {
    "synthetic_audio",
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
]
SCENARIO_SAFETY_FIELDS = [
    "used_microphone",
    "read_user_audio",
    "called_remote_asr",
    "called_llm",
    "downloaded_model",
    "downloaded_public_audio",
    "read_configs_local",
]
THRESHOLDS = {
    "normalized_technical_entity_recall_min": 0.8,
    "first_partial_latency_seconds_p95_max": 2.0,
    "final_latency_seconds_p95_max": 8.0,
    "asr_rtf_max": 0.6,
    "suggestion_candidate_latency_seconds_p95_max": 30.0,
    "batch_engineering_scenario_count_min": 4,
    "batch_negative_control_count_min": 1,
}
SHA256_HEX_CHARS = set("0123456789abcdef")


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    suffix = tuple(part.casefold() for part in suffix_parts)
    width = len(suffix)
    return any(parts[index : index + width] == suffix for index in range(len(parts) - width + 1))


def _repo_relative_path(path: Path) -> Path | None:
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        return resolved.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_approved_report_root(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    text = relative.as_posix()
    return text == APPROVED_EVIDENCE_REPORT_ROOT or text.startswith(
        f"{APPROVED_EVIDENCE_REPORT_ROOT}/"
    )


def _is_under_any_approved_root(path: Path, approved_roots: tuple[str, ...]) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    text = relative.as_posix()
    return any(text == root or text.startswith(f"{root}/") for root in approved_roots)


def _evidence_report_path_errors(path: Path) -> list[str]:
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    for candidate in (path, resolved):
        if candidate.suffix.casefold() == ".m4a":
            return ["evidence_report_path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"evidence_report_path is blocked: {label}"]
    if _repo_relative_path(resolved) is None:
        return ["evidence_report_path is outside repository"]
    if not _is_under_approved_report_root(path) or not _is_under_approved_report_root(resolved):
        return [
            "evidence_report_path is not under approved root: "
            + APPROVED_EVIDENCE_REPORT_ROOT
        ]
    if path.suffix.casefold() != ".json":
        return ["evidence_report_path must be a JSON report file"]
    return []


def _artifact_path_errors(path: Path) -> list[str]:
    resolved = (path if path.is_absolute() else REPO_ROOT / path).resolve(strict=False)
    for candidate in (path, resolved):
        if candidate.suffix.casefold() == ".m4a":
            return ["artifact path is blocked: audio file"]
        for label, suffix_parts in FORBIDDEN_PATH_LABELS:
            if _path_has_suffix_parts(candidate, suffix_parts):
                return [f"artifact path is blocked: {label}"]
    if _repo_relative_path(resolved) is None:
        return ["artifact path is outside repository"]
    if not _is_under_any_approved_root(path, APPROVED_BATCH_ARTIFACT_ROOTS) or not _is_under_any_approved_root(
        resolved, APPROVED_BATCH_ARTIFACT_ROOTS
    ):
        return [
            "artifact path is not under approved roots: "
            + ", ".join(APPROVED_BATCH_ARTIFACT_ROOTS)
        ]
    if path.suffix.casefold() != ".json":
        return ["artifact path must be a JSON file"]
    return []


def _sha256_text_is_valid(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in SHA256_HEX_CHARS for character in value)
    )


def _sha256_file(path_text: str) -> tuple[str | None, str | None]:
    path = Path(path_text)
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        return hashlib.sha256(resolved.read_bytes()).hexdigest(), None
    except FileNotFoundError:
        return None, "artifact path does not exist"
    except OSError:
        return None, "artifact path could not be read"


def _display_report_path(path_text: str) -> str:
    path = Path(path_text)
    relative = _repo_relative_path(path)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _load_evidence_report(path_text: str) -> tuple[Any | None, list[str], str, str | None]:
    path = Path(path_text)
    path_errors = _evidence_report_path_errors(path)
    if path_errors:
        return None, path_errors, "blocked", None
    resolved = path if path.is_absolute() else REPO_ROOT / path
    try:
        return json.loads(resolved.read_text(encoding="utf-8")), [], "read", _display_report_path(path_text)
    except FileNotFoundError:
        return None, ["evidence_report_path does not exist"], "failed", _display_report_path(path_text)
    except json.JSONDecodeError:
        return None, ["evidence_report_path must contain valid JSON"], "failed", _display_report_path(path_text)


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _is_non_negative_number(value: Any) -> bool:
    return _is_number(value) and value >= 0


def _is_positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _safe_string(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.replace("\\", "/").casefold()
    if lowered.startswith("/") or ".m4a" in lowered or "/users/" in lowered:
        return False
    return not any(label in lowered for label, _parts in FORBIDDEN_PATH_LABELS)


def _base_report() -> dict[str, Any]:
    return {
        "decision_id": DECISION_ID,
        "report_mode": REPORT_MODE,
        "schema_version": SCHEMA_VERSION,
        "schema_status": SCHEMA_STATUS,
        "execution_boundary": EXECUTION_BOUNDARY,
        "approved_evidence_report_root": APPROVED_EVIDENCE_REPORT_ROOT,
        "approved_batch_artifact_roots": list(APPROVED_BATCH_ARTIFACT_ROOTS),
        "thresholds": THRESHOLDS,
        "evidence_report_path": None,
        "evidence_report_read_status": "not_requested",
        "evidence_status": "not_provided",
        "quality_evidence_status": "not_evaluated",
        "counts_as_asr_quality_go_evidence": False,
        "counts_as_real_mic_go_evidence": False,
        "scenario_summary": {
            "engineering_scenario_count": 0,
            "negative_control_count": 0,
            "engineering_min_normalized_recall": None,
            "negative_control_candidate_cards": 0,
        },
        "batch_artifact_provenance_status": "not_evaluated",
        "batch_artifact_count": 0,
        "batch_artifact_scenario_ids": [],
        "validation_errors": [],
        "next_action": "provide_funasr_synthetic_smoke_result_evidence",
        **_false_safety_flags(),
    }


def _path_blocked_report(errors: list[str]) -> dict[str, Any]:
    report = _base_report()
    report["evidence_report_read_status"] = "blocked"
    report["evidence_status"] = "blocked_by_path_guard"
    report["quality_evidence_status"] = "blocked"
    report["validation_errors"] = list(errors)
    report["next_action"] = "fix_funasr_smoke_result_evidence_path"
    return report


def _scenario_summary(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    engineering_recalls = [
        scenario["technical_entity_metrics"]["normalized_recall"]
        for scenario in scenarios
        if scenario.get("scenario_kind") == "engineering"
        and isinstance(scenario.get("technical_entity_metrics"), dict)
        and _is_number(scenario["technical_entity_metrics"].get("normalized_recall"))
    ]
    negative_candidate_cards = sum(
        int(scenario.get("closure", {}).get("candidate_card_count", 0))
        for scenario in scenarios
        if scenario.get("scenario_kind") == "negative_control"
    )
    return {
        "engineering_scenario_count": sum(
            1 for scenario in scenarios if scenario.get("scenario_kind") == "engineering"
        ),
        "negative_control_count": sum(
            1 for scenario in scenarios if scenario.get("scenario_kind") == "negative_control"
        ),
        "engineering_min_normalized_recall": min(engineering_recalls)
        if engineering_recalls
        else None,
        "negative_control_candidate_cards": negative_candidate_cards,
    }


def _validate_event_contract(event_contract: Any) -> list[str]:
    if not isinstance(event_contract, dict):
        return ["event_contract must be an object"]
    errors: list[str] = []
    if not _is_positive_int(event_contract.get("partial_count")):
        errors.append("partial_count must be a positive integer")
    if not _is_positive_int(event_contract.get("final_count")):
        errors.append("final_count must be a positive integer")
    if not _is_non_negative_number(event_contract.get("revision_count")):
        errors.append("revision_count must be a non-negative number")
    if event_contract.get("error_count") != 0:
        errors.append("error_count must be 0")
    if event_contract.get("end_of_stream_count") != 1:
        errors.append("end_of_stream_count must be 1")
    if event_contract.get("has_required_event_sequence") is not True:
        errors.append("has_required_event_sequence must be true")
    return errors


def _validate_latency_and_rtf(scenario: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    latency = scenario.get("latency_metrics")
    asr_metrics = scenario.get("asr_metrics")
    if not isinstance(latency, dict):
        return ["latency_metrics must be an object"]
    if not isinstance(asr_metrics, dict):
        errors.append("asr_metrics must be an object")
        asr_metrics = {}

    threshold_map = {
        "first_partial_latency_seconds_p95": "first_partial_latency_seconds_p95_max",
        "final_latency_seconds_p95": "final_latency_seconds_p95_max",
        "suggestion_candidate_latency_seconds_p95": "suggestion_candidate_latency_seconds_p95_max",
    }
    for metric, threshold_key in threshold_map.items():
        value = latency.get(metric)
        if not _is_non_negative_number(value):
            errors.append(f"{metric} must be a non-negative number")
        elif float(value) > float(THRESHOLDS[threshold_key]):
            errors.append(f"{metric} must be <= {THRESHOLDS[threshold_key]}")

    rtf = asr_metrics.get("rtf")
    if not _is_non_negative_number(rtf):
        errors.append("rtf must be a non-negative number")
    elif float(rtf) > float(THRESHOLDS["asr_rtf_max"]):
        errors.append(f"rtf must be <= {THRESHOLDS['asr_rtf_max']}")
    return errors


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _recall_from_detail(matched_entities: list[str], expected_entities: list[str]) -> float:
    if not expected_entities:
        return 0.0
    return round(len(set(matched_entities)) / len(set(expected_entities)), 6)


def _validate_entity_detail_metrics(technical: dict[str, Any]) -> list[str]:
    detail_fields = (
        "expected_entities",
        "raw_matched_entities",
        "raw_missing_entities",
        "normalized_matched_entities",
        "normalized_missing_entities",
    )
    if not any(field in technical for field in detail_fields):
        return []
    errors: list[str] = []
    for field in detail_fields:
        if not _is_string_list(technical.get(field)):
            errors.append(f"{field} must be a list of strings")
    if errors:
        return errors

    expected = set(technical["expected_entities"])
    for prefix in ("raw", "normalized"):
        matched = set(technical[f"{prefix}_matched_entities"])
        missing = set(technical[f"{prefix}_missing_entities"])
        if matched & missing:
            errors.append(f"{prefix} matched/missing entities must be disjoint")
        if matched | missing != expected:
            errors.append(f"{prefix} matched/missing entities must cover expected_entities")
        recall = technical.get(f"{prefix}_recall")
        if _is_number(recall) and float(recall) != _recall_from_detail(
            technical[f"{prefix}_matched_entities"],
            technical["expected_entities"],
        ):
            errors.append(f"{prefix}_recall must match {prefix}_matched_entities / expected_entities")
    return errors


def _validate_scenario(scenario: Any) -> list[str]:
    if not isinstance(scenario, dict):
        return ["scenario result must be an object"]
    errors: list[str] = []
    if not _safe_string(scenario.get("scenario_id")):
        errors.append("scenario_id must be a safe non-empty string")
    if scenario.get("scenario_kind") not in ALLOWED_SCENARIO_KINDS:
        errors.append("scenario_kind must be engineering or negative_control")
    if scenario.get("input_source_kind") not in ALLOWED_INPUT_SOURCE_KINDS:
        errors.append("input_source_kind must be synthetic_audio")

    errors.extend(_validate_event_contract(scenario.get("event_contract")))
    errors.extend(_validate_latency_and_rtf(scenario))

    technical = scenario.get("technical_entity_metrics")
    if not isinstance(technical, dict):
        errors.append("technical_entity_metrics must be an object")
        technical = {}
    raw_recall = technical.get("raw_recall")
    normalized_recall = technical.get("normalized_recall")
    if not _is_non_negative_number(raw_recall):
        errors.append("raw_recall must be a non-negative number")
    if not _is_non_negative_number(normalized_recall):
        errors.append("normalized_recall must be a non-negative number")
    elif scenario.get("scenario_kind") == "engineering" and float(normalized_recall) < float(
        THRESHOLDS["normalized_technical_entity_recall_min"]
    ):
        errors.append("engineering normalized_recall must be >= 0.8")
    errors.extend(_validate_entity_detail_metrics(technical))

    closure = scenario.get("closure")
    if not isinstance(closure, dict):
        errors.append("closure must be an object")
        closure = {}
    if not _is_non_negative_number(closure.get("evidence_span_count")):
        errors.append("evidence_span_count must be a non-negative number")
    if not _is_non_negative_number(closure.get("state_event_count")):
        errors.append("state_event_count must be a non-negative number")
    if not _is_non_negative_number(closure.get("candidate_card_count")):
        errors.append("candidate_card_count must be a non-negative number")
    if closure.get("all_cards_have_evidence_spans") is not True:
        errors.append("all_cards_have_evidence_spans must be true")

    if scenario.get("scenario_kind") == "engineering":
        if _is_number(closure.get("evidence_span_count")) and closure.get("evidence_span_count") <= 0:
            errors.append("engineering evidence_span_count must be > 0")
        if _is_number(closure.get("state_event_count")) and closure.get("state_event_count") <= 0:
            errors.append("engineering state_event_count must be > 0")
        if _is_number(closure.get("candidate_card_count")) and closure.get("candidate_card_count") <= 0:
            errors.append("engineering candidate_card_count must be > 0")
    if scenario.get("scenario_kind") == "negative_control":
        if closure.get("state_event_count") != 0:
            errors.append("negative control state_event_count must be 0")
        if closure.get("candidate_card_count") != 0:
            errors.append("negative control candidate_card_count must be 0")

    safety = scenario.get("safety")
    if not isinstance(safety, dict):
        errors.append("safety must be an object")
        safety = {}
    for field in SCENARIO_SAFETY_FIELDS:
        if safety.get(field) is not False:
            errors.append(f"{field} must be false")
    return errors


def _batch_artifact_provenance_meta(
    *,
    status: str,
    artifacts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    safe_scenario_ids: list[str] = []
    for artifact in artifacts or []:
        scenario_id = artifact.get("scenario_id")
        if _safe_string(scenario_id):
            safe_scenario_ids.append(scenario_id)
    return {
        "batch_artifact_provenance_status": status,
        "batch_artifact_count": len(artifacts or []),
        "batch_artifact_scenario_ids": safe_scenario_ids,
    }


def _validate_batch_artifact_provenance(evidence_report: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    scenarios = [
        scenario
        for scenario in evidence_report.get("scenario_results", [])
        if isinstance(scenario, dict)
    ]
    scenario_ids = {
        scenario.get("scenario_id")
        for scenario in scenarios
        if _safe_string(scenario.get("scenario_id"))
    }
    provenance = evidence_report.get("batch_artifact_provenance")
    if not isinstance(provenance, dict):
        return ["batch confirmation requires artifact provenance"], _batch_artifact_provenance_meta(
            status="missing"
        )

    errors: list[str] = []
    if provenance.get("source_kind") != "local_funasr_synthetic_smoke_artifacts":
        errors.append(
            "batch artifact provenance source_kind must be local_funasr_synthetic_smoke_artifacts"
        )

    artifacts = provenance.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return errors + [
            "batch artifact provenance artifacts must be a non-empty list"
        ], _batch_artifact_provenance_meta(status="blocked")

    valid_artifacts: list[dict[str, Any]] = []
    covered_scenario_ids: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"batch artifact {index} must be an object")
            continue
        valid_artifacts.append(artifact)
        scenario_id = artifact.get("scenario_id")
        scenario_label = scenario_id if _safe_string(scenario_id) else f"index-{index}"
        if artifact.get("artifact_kind") != "scenario_gate_report":
            errors.append(f"artifact_kind for {scenario_label} must be scenario_gate_report")
        if not _safe_string(scenario_id):
            errors.append(f"artifact scenario_id for index-{index} must be a safe non-empty string")
        elif scenario_id not in scenario_ids:
            errors.append(f"artifact scenario_id is not in scenario_results: {scenario_id}")
        else:
            covered_scenario_ids.add(scenario_id)

        path_text = artifact.get("path")
        if not isinstance(path_text, str) or not path_text.strip():
            errors.append(f"artifact path for {scenario_label} must be a safe non-empty string")
            continue
        path_errors = _artifact_path_errors(Path(path_text))
        if path_errors:
            errors.extend(f"{error} for {scenario_label}" for error in path_errors)
            continue

        expected_sha256 = artifact.get("sha256")
        if not _sha256_text_is_valid(expected_sha256):
            errors.append(f"artifact sha256 for {scenario_label} must be 64 lowercase hex characters")
            continue
        observed_sha256, read_error = _sha256_file(path_text)
        if read_error is not None:
            errors.append(f"{read_error} for {scenario_label}")
            continue
        if observed_sha256 != expected_sha256:
            errors.append(f"artifact sha256 mismatch for {scenario_label}")

    missing_scenario_ids = sorted(scenario_ids - covered_scenario_ids)
    if missing_scenario_ids:
        errors.append(
            "batch artifact provenance must include every scenario_id: "
            + ",".join(missing_scenario_ids)
        )

    return errors, _batch_artifact_provenance_meta(
        status="blocked" if errors else "validated",
        artifacts=valid_artifacts,
    )


def validate_evidence_report(evidence_report: Any) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    if not isinstance(evidence_report, dict):
        return ["evidence report must be an object"], _scenario_summary([]), _batch_artifact_provenance_meta(
            status="not_evaluated"
        )
    errors: list[str] = []
    if evidence_report.get("manifest_version") != SCHEMA_VERSION:
        errors.append(f"manifest_version must be {SCHEMA_VERSION}")
    if evidence_report.get("evidence_kind") not in ALLOWED_EVIDENCE_KINDS:
        errors.append("evidence_kind must be single_synthetic_smoke or batch_synthetic_confirmation")
    if evidence_report.get("provider") != PROVIDER:
        errors.append(f"provider must be {PROVIDER}")
    if not _safe_string(evidence_report.get("model_alias")):
        errors.append("model_alias must be a safe non-empty string")
    if evidence_report.get("source_boundary") != "synthetic_audio_no_user_audio":
        errors.append("source_boundary must be synthetic_audio_no_user_audio")

    scenarios = evidence_report.get("scenario_results")
    if not isinstance(scenarios, list) or not scenarios:
        return errors + ["scenario_results must be a non-empty list"], _scenario_summary([]), _batch_artifact_provenance_meta(
            status="not_evaluated"
        )
    for scenario in scenarios:
        errors.extend(_validate_scenario(scenario))
    summary = _scenario_summary([scenario for scenario in scenarios if isinstance(scenario, dict)])

    evidence_kind = evidence_report.get("evidence_kind")
    if evidence_kind == "single_synthetic_smoke":
        if summary["engineering_scenario_count"] != 1 or summary["negative_control_count"] != 0:
            errors.append("single_synthetic_smoke requires exactly one engineering scenario")
    if evidence_kind == "batch_synthetic_confirmation":
        if summary["engineering_scenario_count"] < THRESHOLDS["batch_engineering_scenario_count_min"]:
            errors.append("batch confirmation requires at least 4 engineering scenarios")
        if summary["negative_control_count"] < THRESHOLDS["batch_negative_control_count_min"]:
            errors.append("batch confirmation requires at least 1 negative control")
        if summary["negative_control_candidate_cards"] != 0:
            errors.append("negative control candidate_card_count must be 0")
        provenance_errors, provenance_meta = _validate_batch_artifact_provenance(evidence_report)
        errors.extend(provenance_errors)
    else:
        provenance_meta = _batch_artifact_provenance_meta(status="not_required")

    return errors, summary, provenance_meta


def build_funasr_synthetic_smoke_result_evidence_gate(
    *,
    evidence_report: dict[str, Any] | None = None,
    evidence_report_path: str | None = None,
) -> dict[str, Any]:
    if evidence_report is not None and evidence_report_path is not None:
        report = _base_report()
        report["evidence_status"] = "blocked_by_quality_thresholds"
        report["quality_evidence_status"] = "blocked"
        report["validation_errors"] = [
            "provide either evidence_report or evidence_report_path, not both"
        ]
        report["next_action"] = "fix_funasr_smoke_result_evidence"
        return report

    report = _base_report()
    resolved_evidence = evidence_report

    if evidence_report_path is not None:
        loaded, load_errors, read_status, display_path = _load_evidence_report(evidence_report_path)
        if read_status == "blocked":
            return _path_blocked_report(load_errors)
        report["evidence_report_path"] = display_path
        report["evidence_report_read_status"] = read_status
        if load_errors:
            report["evidence_status"] = "blocked_by_quality_thresholds"
            report["quality_evidence_status"] = "blocked"
            report["validation_errors"] = load_errors
            report["next_action"] = "fix_funasr_smoke_result_evidence"
            return report
        resolved_evidence = loaded

    if resolved_evidence is None:
        return report

    validation_errors, summary, provenance_meta = validate_evidence_report(resolved_evidence)
    report["scenario_summary"] = summary
    report.update(provenance_meta)
    if validation_errors:
        report["evidence_status"] = "blocked_by_quality_thresholds"
        report["quality_evidence_status"] = "blocked"
        report["validation_errors"] = validation_errors
        report["next_action"] = "fix_funasr_smoke_result_evidence"
        return report

    report["evidence_status"] = "schema_validated_no_asr_execution"
    if resolved_evidence.get("evidence_kind") == "batch_synthetic_confirmation":
        report["quality_evidence_status"] = "funasr_synthetic_smoke_quality_batch_confirmed"
        report["counts_as_asr_quality_go_evidence"] = True
        report["next_action"] = "allow_asr_quality_gate_to_exit_without_claiming_real_mic_ready"
    else:
        report["quality_evidence_status"] = (
            "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
        )
        report["next_action"] = "run_batch_confirmation_before_quality_exit"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-report-path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_funasr_synthetic_smoke_result_evidence_gate(
        evidence_report_path=args.evidence_report_path,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["counts_as_asr_quality_go_evidence"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Gate synthetic ASR smoke results against Copilot product-value expectations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path, PurePosixPath
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "synthetic_product_value_gate.v1"
FIRST_PILOT_ENTITY_RECALL_THRESHOLD = 0.8
PRODUCT_ENTITY_RECALL_TARGET = 0.9

ALLOWED_SMOKE_REPORT_ROOTS = {"artifacts/tmp/asr_reports"}
ALLOWED_SCRIPT_ROOTS = {"data/asr_eval/synthetic_meetings/scripts"}
FORBIDDEN_ROOTS = {
    "configs/local",
    "data/asr_eval/local_samples",
    "data/asr_eval/samples",
    "data/local_runtime",
    "outputs",
}


def _base_report(status: str, errors: list[str]) -> dict[str, object]:
    return {
        "report_mode": "synthetic_product_value_gate",
        "report_version": REPORT_VERSION,
        "status": status,
        "validation_errors": errors,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "safe_to_download_models": False,
    }


def _is_relative_safe_path(path: str) -> bool:
    if not path or path.startswith("/"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and all(part not in {"", "."} for part in parts)


def _is_under_any_root(path: str, roots: set[str]) -> bool:
    if not _is_relative_safe_path(path):
        return False
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_relative_path(label: str, path: str, allowed_roots: set[str]) -> list[str]:
    errors: list[str] = []
    if not _is_under_any_root(path, allowed_roots):
        errors.append(f"{label} is not allowed")
    if _is_under_any_root(path, FORBIDDEN_ROOTS):
        errors.append(f"{label} is forbidden")
    return errors


def _resolved_path_errors(label: str, path: Path, allowed_roots: set[str]) -> list[str]:
    errors: list[str] = []
    try:
        resolved_path = path.resolve(strict=True)
    except OSError:
        return [f"{label} could not be resolved"]

    resolved_allowed_roots = [
        (REPO_ROOT / root).resolve(strict=False)
        for root in allowed_roots
    ]
    if not any(
        resolved_path == root or _is_relative_to(resolved_path, root)
        for root in resolved_allowed_roots
    ):
        errors.append(f"{label} resolved path is not allowed")

    resolved_forbidden_roots = [
        (REPO_ROOT / root).resolve(strict=False)
        for root in FORBIDDEN_ROOTS
    ]
    if any(
        resolved_path == root or _is_relative_to(resolved_path, root)
        for root in resolved_forbidden_roots
    ):
        errors.append(f"{label} resolved path is forbidden")
    return errors


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_card_range(script: dict[str, object]) -> dict[str, int]:
    return {
        "min": int(script.get("expected_engineering_card_count_min", 0)),
        "max": int(script.get("expected_engineering_card_count_max", 0)),
    }


def _event_counts(smoke_report: dict[str, object]) -> dict[str, int]:
    raw_counts = smoke_report.get("event_counts", {})
    if not isinstance(raw_counts, dict):
        raw_counts = {}
    return {
        "partial": int(raw_counts.get("partial", 0)),
        "final": int(raw_counts.get("final", 0)),
        "revision": int(raw_counts.get("revision", 0)),
        "error": int(raw_counts.get("error", 0)),
        "end_of_stream": int(raw_counts.get("end_of_stream", 0)),
    }


def _gate_failures(
    *,
    is_engineering_value_script: bool,
    normalized_recall: float,
    event_counts: dict[str, int],
) -> list[str]:
    failures: list[str] = []
    if event_counts["final"] <= 0:
        failures.append("missing final ASR event")
    if event_counts["end_of_stream"] != 1:
        failures.append("missing single end_of_stream event")
    if event_counts["error"] > 0:
        failures.append("ASR stream contains provider errors")
    if is_engineering_value_script and normalized_recall < FIRST_PILOT_ENTITY_RECALL_THRESHOLD:
        failures.append("normalized technical entity recall below first-pilot threshold")
    return failures


def _decision(
    *,
    is_engineering_value_script: bool,
    failures: list[str],
) -> tuple[str, str, bool]:
    if not is_engineering_value_script:
        if failures:
            return "negative_control_blocked", "fix_event_contract_or_negative_control_inputs", False
        return "negative_control_passed", "keep_as_negative_control_in_batch_gate", False
    if (
        "missing final ASR event" in failures
        or "missing single end_of_stream event" in failures
        or "ASR stream contains provider errors" in failures
    ):
        return "blocked_by_event_contract", "fix_asr_event_contract", False
    if failures:
        return (
            "needs_asr_quality_work",
            "improve_local_asr_quality_or_prepare_model_approval",
            False,
        )
    return (
        "go_to_desktop_runtime_validation",
        "validate_desktop_runtime_before_real_mic",
        True,
    )


def build_synthetic_product_value_gate_report(
    *,
    smoke_report_path: Path,
    script_json_path: Path,
) -> dict[str, object]:
    path_errors: list[str] = []
    path_errors.extend(
        _resolved_path_errors(
            "smoke_report_path",
            smoke_report_path,
            ALLOWED_SMOKE_REPORT_ROOTS,
        )
    )
    path_errors.extend(
        _resolved_path_errors(
            "script_json_path",
            script_json_path,
            ALLOWED_SCRIPT_ROOTS,
        )
    )
    if path_errors:
        return _base_report("blocked", path_errors)

    smoke_report = _load_json(smoke_report_path)
    script = _load_json(script_json_path)
    if not isinstance(smoke_report, dict) or not isinstance(script, dict):
        return _base_report("blocked", ["input JSON files must contain objects"])

    expected_cards = script.get("expected_suggestion_cards", [])
    expected_gaps = script.get("expected_gap_candidates", [])
    if not isinstance(expected_cards, list):
        expected_cards = []
    if not isinstance(expected_gaps, list):
        expected_gaps = []

    card_range = _expected_card_range(script)
    is_engineering_value_script = bool(
        card_range["max"] > 0 or expected_cards or expected_gaps
    )
    normalized_recall = float(smoke_report.get("normalized_technical_entity_recall", 0.0))
    normalized_precision = float(smoke_report.get("normalized_technical_entity_precision", 0.0))
    event_counts = _event_counts(smoke_report)
    failures = _gate_failures(
        is_engineering_value_script=is_engineering_value_script,
        normalized_recall=normalized_recall,
        event_counts=event_counts,
    )
    decision, next_action, ready_for_desktop = _decision(
        is_engineering_value_script=is_engineering_value_script,
        failures=failures,
    )

    return {
        **_base_report("completed", []),
        "script_id": script.get("script_id", smoke_report.get("script_id")),
        "scenario": script.get("scenario"),
        "is_engineering_value_script": is_engineering_value_script,
        "expected_gap_candidates": [str(gap) for gap in expected_gaps],
        "expected_card_count": len(expected_cards),
        "expected_engineering_card_count_range": card_range,
        "baseline_expectations": script.get("baseline_expectations", {}),
        "normalized_technical_entity_recall": normalized_recall,
        "normalized_technical_entity_precision": normalized_precision,
        "normalized_missing_entities": smoke_report.get("normalized_missing_entities", []),
        "technical_entity_count": int(smoke_report.get("technical_entity_count", 0)),
        "event_counts": event_counts,
        "entity_gate": {
            "first_pilot_threshold": FIRST_PILOT_ENTITY_RECALL_THRESHOLD,
            "product_target": PRODUCT_ENTITY_RECALL_TARGET,
            "passes_first_pilot": normalized_recall >= FIRST_PILOT_ENTITY_RECALL_THRESHOLD,
            "passes_product_target": (
                normalized_recall >= PRODUCT_ENTITY_RECALL_TARGET
                and normalized_precision >= PRODUCT_ENTITY_RECALL_TARGET
            ),
        },
        "event_gate": {
            "has_final_event": event_counts["final"] > 0,
            "has_single_end_of_stream": event_counts["end_of_stream"] == 1,
            "has_provider_errors": event_counts["error"] > 0,
        },
        "gate_failures": failures,
        "product_value_decision": decision,
        "ready_for_desktop_runtime_validation": ready_for_desktop,
        "ready_for_real_mic_pilot": False,
        "next_action": next_action,
    }


def build_synthetic_product_value_gate_report_from_relative_paths(
    *,
    smoke_report_path: str,
    script_json_path: str,
) -> dict[str, object]:
    errors: list[str] = []
    errors.extend(_validate_relative_path("smoke_report_path", smoke_report_path, ALLOWED_SMOKE_REPORT_ROOTS))
    errors.extend(_validate_relative_path("script_json_path", script_json_path, ALLOWED_SCRIPT_ROOTS))
    candidate_smoke_report_path = REPO_ROOT / smoke_report_path
    candidate_script_json_path = REPO_ROOT / script_json_path
    errors.extend(
        _resolved_path_errors(
            "smoke_report_path",
            candidate_smoke_report_path,
            ALLOWED_SMOKE_REPORT_ROOTS,
        )
    )
    errors.extend(
        _resolved_path_errors(
            "script_json_path",
            candidate_script_json_path,
            ALLOWED_SCRIPT_ROOTS,
        )
    )
    if errors:
        return _base_report("blocked", errors)
    return build_synthetic_product_value_gate_report(
        smoke_report_path=candidate_smoke_report_path,
        script_json_path=candidate_script_json_path,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-report", required=True)
    parser.add_argument("--script-json", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_product_value_gate_report_from_relative_paths(
        smoke_report_path=args.smoke_report,
        script_json_path=args.script_json,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

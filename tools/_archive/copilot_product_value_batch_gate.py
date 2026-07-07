#!/usr/bin/env python3
"""Batch Copilot product-value tri-lane gate over synthetic meeting scripts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import copilot_product_value_tri_lane_gate as tri_lane  # noqa: E402


REPORT_VERSION = "copilot_product_value_batch_gate.v1"
SAFETY_FLAGS = (
    "safe_to_call_llm_now",
    "safe_to_call_remote_asr_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
)


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in SAFETY_FLAGS}


def _blocked_report(errors: list[str]) -> dict[str, Any]:
    return {
        "report_mode": "copilot_product_value_batch_gate",
        "report_version": REPORT_VERSION,
        "status": "blocked",
        "validation_errors": errors,
        "overall_decision": "blocked_by_input_validation",
        "next_action": "fix_validation_errors",
        "scenario_count": 0,
        "engineering_scenario_count": 0,
        "negative_control_count": 0,
        "scenario_ids": [],
        "decision_counts": {},
        "perfect_lane_ready_count": 0,
        "mock_lane_ready_count": 0,
        "real_asr_blocked_count": 0,
        "non_engineering_candidate_count": 0,
        "scenarios": [],
        **_false_safety_flags(),
    }


def _validate_scripts_root_before_read(scripts_root: str) -> list[str]:
    errors = tri_lane._validate_path_before_read(
        "scripts_root",
        scripts_root,
        tri_lane.ALLOWED_SCRIPT_ROOTS,
    )
    if errors:
        return errors
    root_path = REPO_ROOT / scripts_root
    if not root_path.is_dir():
        return ["scripts_root must be a directory"]
    return []


def _script_files(scripts_root: str) -> list[Path]:
    return sorted((REPO_ROOT / scripts_root).glob("*.json"))


def _repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _load_script_id(script_path: Path) -> tuple[str | None, list[str]]:
    try:
        payload = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, [f"{_repo_relative(script_path)} could not be read as script JSON"]
    if not isinstance(payload, dict) or not isinstance(payload.get("script_id"), str):
        return None, [f"{_repo_relative(script_path)} missing script_id"]
    return str(payload["script_id"]), []


def _format_pattern(pattern: str, *, script_id: str) -> str:
    return pattern.format(script_id=script_id)


def _scenario_summary(tri_report: dict[str, Any]) -> dict[str, Any]:
    lanes = {
        str(lane.get("lane")): lane
        for lane in tri_report.get("lanes", [])
        if isinstance(lane, dict)
    }
    perfect = lanes.get("perfect_transcript", {})
    mock = lanes.get("mock_asr", {})
    real = lanes.get("real_asr", {})
    return {
        "scenario_id": tri_report.get("scenario_id"),
        "scenario": tri_report.get("scenario"),
        "is_engineering_value_script": tri_report.get("is_engineering_value_script"),
        "overall_decision": tri_report.get("overall_decision"),
        "next_action": tri_report.get("next_action"),
        "perfect_lane_decision": perfect.get("decision"),
        "mock_lane_decision": mock.get("decision"),
        "real_asr_lane_decision": real.get("decision"),
        "perfect_candidate_count": perfect.get("candidate_count", 0),
        "mock_candidate_count": mock.get("candidate_count", 0),
        "real_asr_candidate_count": real.get("candidate_count", 0),
        "real_asr_normalized_technical_entity_recall": real.get("normalized_technical_entity_recall"),
        "non_engineering_candidate_count": tri_report.get("non_engineering_candidate_count", 0),
        "feedback_rubric_required": tri_report.get("feedback_rubric_required"),
    }


def _decision_counts(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for scenario in scenarios:
        decision = str(scenario.get("overall_decision"))
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def _batch_decision(scenarios: list[dict[str, Any]]) -> tuple[str, str]:
    decisions = {str(scenario.get("overall_decision")) for scenario in scenarios}
    if "blocked_by_product_logic" in decisions:
        return "blocked_by_product_logic", "fix_gap_detection_before_more_asr_work"
    if "blocked_by_stream_contract" in decisions:
        return "blocked_by_stream_contract", "fix_stream_contract_before_real_mic"
    if "blocked_by_asr_quality" in decisions:
        return "blocked_by_asr_quality", "improve_real_asr_quality_or_prepare_model_approval"
    if "blocked_by_input_validation" in decisions:
        return "blocked_by_input_validation", "fix_validation_errors"
    return "product_logic_ready", "advance_to_desktop_runtime_or_controlled_llm_cards"


def build_copilot_product_value_batch_report_from_relative_roots(
    *,
    scripts_root: str,
    mock_events_pattern: str,
    real_events_pattern: str,
    real_smoke_report_pattern: str,
    real_provider: str,
    mock_provider: str = "mock_streaming",
) -> dict[str, Any]:
    tri_lane.REPO_ROOT = REPO_ROOT
    errors = _validate_scripts_root_before_read(scripts_root)
    if errors:
        return _blocked_report(errors)

    script_paths = _script_files(scripts_root)
    if not script_paths:
        return _blocked_report(["scripts_root contains no script JSON files"])

    validation_errors: list[str] = []
    scenario_reports: list[dict[str, Any]] = []
    for script_path in script_paths:
        script_id, script_errors = _load_script_id(script_path)
        validation_errors.extend(script_errors)
        if script_id is None:
            continue
        script_rel = _repo_relative(script_path)
        mock_events_path = _format_pattern(mock_events_pattern, script_id=script_id)
        real_events_path = _format_pattern(real_events_pattern, script_id=script_id)
        real_smoke_report_path = _format_pattern(real_smoke_report_pattern, script_id=script_id)
        tri_report = tri_lane.build_copilot_product_value_tri_lane_report_from_relative_paths(
            script_json_path=script_rel,
            mock_events_path=mock_events_path,
            real_events_path=real_events_path,
            real_smoke_report_path=real_smoke_report_path,
            real_provider=real_provider,
            mock_provider=mock_provider,
        )
        if tri_report["status"] == "blocked":
            for error in tri_report.get("validation_errors", []):
                validation_errors.append(f"{script_id}: {error}")
            continue
        scenario_reports.append(tri_report)

    if validation_errors:
        return _blocked_report(validation_errors)

    scenario_summaries = [_scenario_summary(report) for report in scenario_reports]
    decision_counts = _decision_counts(scenario_summaries)
    overall_decision, next_action = _batch_decision(scenario_summaries)

    return {
        "report_mode": "copilot_product_value_batch_gate",
        "report_version": REPORT_VERSION,
        "status": "completed",
        "validation_errors": [],
        "overall_decision": overall_decision,
        "next_action": next_action,
        "scenario_count": len(scenario_summaries),
        "engineering_scenario_count": sum(
            1 for scenario in scenario_summaries if scenario.get("is_engineering_value_script") is True
        ),
        "negative_control_count": sum(
            1 for scenario in scenario_summaries if scenario.get("is_engineering_value_script") is False
        ),
        "scenario_ids": [str(scenario["scenario_id"]) for scenario in scenario_summaries],
        "decision_counts": decision_counts,
        "perfect_lane_ready_count": sum(
            1 for scenario in scenario_summaries if scenario.get("perfect_lane_decision") == "product_logic_ready"
        ),
        "mock_lane_ready_count": sum(
            1 for scenario in scenario_summaries if scenario.get("mock_lane_decision") == "product_logic_ready"
        ),
        "real_asr_blocked_count": sum(
            1
            for scenario in scenario_summaries
            if str(scenario.get("real_asr_lane_decision", "")).startswith("blocked_")
        ),
        "non_engineering_candidate_count": sum(
            int(scenario.get("non_engineering_candidate_count", 0) or 0)
            for scenario in scenario_summaries
        ),
        "scenarios": scenario_summaries,
        **_false_safety_flags(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scripts-root", default="data/asr_eval/synthetic_meetings/scripts")
    parser.add_argument(
        "--mock-events-pattern",
        default="artifacts/tmp/asr_events/{script_id}.mock.events.json",
    )
    parser.add_argument(
        "--real-events-pattern",
        default="artifacts/tmp/asr_events/{script_id}.sherpa.events.json",
    )
    parser.add_argument(
        "--real-smoke-report-pattern",
        default="artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json",
    )
    parser.add_argument("--real-provider", default="sherpa_onnx_streaming")
    parser.add_argument("--mock-provider", default="mock_streaming")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_copilot_product_value_batch_report_from_relative_roots(
        scripts_root=args.scripts_root,
        mock_events_pattern=args.mock_events_pattern,
        real_events_pattern=args.real_events_pattern,
        real_smoke_report_pattern=args.real_smoke_report_pattern,
        real_provider=args.real_provider,
        mock_provider=args.mock_provider,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())

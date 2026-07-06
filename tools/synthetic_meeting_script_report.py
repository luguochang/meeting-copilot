#!/usr/bin/env python3
"""Report synthetic Chinese technical meeting script coverage without audio generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCRIPT_DIR = REPO_ROOT / "data" / "asr_eval" / "synthetic_meetings" / "scripts"
ALLOWED_SCRIPT_DIR = DEFAULT_SCRIPT_DIR.resolve()

REQUIRED_SCENARIOS = {
    "api_review",
    "release_review",
    "incident_review",
    "architecture_review",
    "non_engineering_control",
}
REQUIRED_KEYS = {
    "script_id",
    "scenario",
    "language",
    "turns",
    "technical_entities",
    "expected_state_events",
    "expected_gap_candidates",
    "expected_suggestion_cards",
    "baseline_expectations",
    "expected_engineering_card_count_min",
    "expected_engineering_card_count_max",
}
REQUIRED_PRODUCT_ANNOTATIONS = [
    "expected_state_events",
    "expected_gap_candidates",
    "expected_suggestion_cards",
    "baseline_expectations",
]
REQUIRED_BASELINE_KEYS = {
    "transcript_only_detects_gap",
    "summary_only_detects_within_window",
    "copilot_should_detect_within_window",
}
ALLOWED_GAP_TYPES = {
    "owner",
    "deadline",
    "rollback",
    "test_verification",
    "metric_monitoring",
}


def _load_scripts(script_dir: Path) -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(script_dir.glob("*.json"))
    ]


def _is_allowed_script_dir(script_dir: Path) -> bool:
    candidate = script_dir if script_dir.is_absolute() else REPO_ROOT / script_dir
    return candidate.resolve() == ALLOWED_SCRIPT_DIR


def validate_script(script: dict[str, object]) -> list[str]:
    errors: list[str] = []
    script_id = script.get("script_id", "<unknown>")
    missing = REQUIRED_KEYS - set(script)
    if missing:
        errors.append(f"{script_id} missing keys: {sorted(missing)}")
        return errors

    if script.get("language") != "zh-CN":
        errors.append(f"{script_id} language must be zh-CN")
    if script.get("scenario") not in REQUIRED_SCENARIOS:
        errors.append(f"{script_id} scenario is not approved")

    turns = script.get("turns")
    if not isinstance(turns, list) or not turns:
        errors.append(f"{script_id} turns must be a non-empty list")
    elif not all(isinstance(turn, dict) and turn.get("speaker") and turn.get("text") for turn in turns):
        errors.append(f"{script_id} turns must include speaker and text")

    baseline = script.get("baseline_expectations")
    if not isinstance(baseline, dict) or set(baseline) != REQUIRED_BASELINE_KEYS:
        errors.append(f"{script_id} baseline_expectations must contain the required keys")

    expected_cards = script.get("expected_suggestion_cards")
    if not isinstance(expected_cards, list):
        errors.append(f"{script_id} expected_suggestion_cards must be a list")
    else:
        for card in expected_cards:
            if not isinstance(card, dict):
                errors.append(f"{script_id} expected_suggestion_cards entries must be objects")
                continue
            if card.get("gap_type") not in ALLOWED_GAP_TYPES:
                errors.append(f"{script_id} card gap_type is not allowed")
            if not card.get("suggested_question"):
                errors.append(f"{script_id} card suggested_question must be non-empty")
            if card.get("evidence_span_required") is not True:
                errors.append(f"{script_id} card evidence_span_required must be true")
            trigger_window = card.get("trigger_window_seconds")
            if not isinstance(trigger_window, dict):
                errors.append(f"{script_id} card trigger_window_seconds must be an object")
            elif trigger_window.get("max", 999) > 30 or trigger_window.get("min", -1) < 0:
                errors.append(f"{script_id} card trigger window must be within 0-30 seconds")

    if script.get("scenario") == "non_engineering_control":
        if script.get("technical_entities") != []:
            errors.append(f"{script_id} non-engineering technical_entities must be empty")
        if script.get("expected_gap_candidates") != []:
            errors.append(f"{script_id} non-engineering expected_gap_candidates must be empty")
        if script.get("expected_suggestion_cards") != []:
            errors.append(f"{script_id} non-engineering expected_suggestion_cards must be empty")
        if script.get("expected_engineering_card_count_max") != 0:
            errors.append(f"{script_id} non-engineering max card count must be zero")
    else:
        if not script.get("expected_suggestion_cards"):
            errors.append(f"{script_id} engineering scripts must include expected cards")
        if not script.get("expected_gap_candidates"):
            errors.append(f"{script_id} engineering scripts must include expected gaps")
        if script.get("expected_engineering_card_count_min", 0) < 1:
            errors.append(f"{script_id} engineering min card count must be at least one")

    return errors


def build_synthetic_meeting_script_report(
    script_dir: Path = DEFAULT_SCRIPT_DIR,
) -> dict[str, object]:
    errors: list[str] = []
    if not _is_allowed_script_dir(script_dir):
        scripts = []
        errors.append("script_dir is not allowed")
    else:
        scripts = _load_scripts(ALLOWED_SCRIPT_DIR)
        for script in scripts:
            errors.extend(validate_script(script))

    scenarios = {script.get("scenario") for script in scripts}
    if scenarios != REQUIRED_SCENARIOS:
        errors.append("synthetic scripts must cover exactly the required scenarios")

    blocked = bool(errors)
    return {
        "report_mode": "synthetic_meeting_script_coverage",
        "coverage_status": "failed" if blocked else "passed",
        "coverage_errors": errors,
        "script_count": len(scripts),
        "scenarios": sorted(str(scenario) for scenario in scenarios),
        "script_ids": sorted(str(script.get("script_id")) for script in scripts),
        "required_product_annotations": REQUIRED_PRODUCT_ANNOTATIONS,
        "safe_to_generate_audio_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_remote_asr": False,
        "safe_to_call_llm": False,
        "next_action": "fix_synthetic_scripts" if blocked else "create_synthetic_audio_generation_plan",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script-dir", type=Path, default=DEFAULT_SCRIPT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_synthetic_meeting_script_report(args.script_dir)
    json.dump(report, out, ensure_ascii=False, indent=2)
    print(file=out)
    return 1 if report["coverage_status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

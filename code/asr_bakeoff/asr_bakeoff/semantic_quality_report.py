from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from meeting_copilot_web_mvp.asr_semantic_quality import evaluate_semantic_quality


PROVIDER_UNDER_TEST = "meeting_copilot_asr_semantic_quality_gate"


def run_semantic_quality_report(dataset_path: Path, output_path: Path) -> dict[str, Any]:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    sample_reports = [_score_sample(sample) for sample in list(dataset.get("samples") or [])]
    false_pass_count = sum(
        1
        for sample in sample_reports
        if sample["expected_status"] == "blocked" and sample["actual_status"] == "passed"
    )
    false_block_count = sum(
        1
        for sample in sample_reports
        if sample["expected_status"] == "passed" and sample["actual_status"] == "blocked"
    )
    status_match_count = sum(1 for sample in sample_reports if sample["status_matches_expected"])
    unexpected_status_count = len(sample_reports) - status_match_count
    keyword_recalls = [
        float(sample["keyword_recall"])
        for sample in sample_reports
        if sample["keyword_recall"] is not None
    ]
    report = {
        "schema_version": "asr_semantic_quality_report.v1",
        "dataset_path": str(dataset_path),
        "provider_under_test": PROVIDER_UNDER_TEST,
        "language": str(dataset.get("language") or "unknown"),
        "domain": str(dataset.get("domain") or "unknown"),
        "cost_status": "no_paid_remote_service",
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
        "summary": {
            "sample_count": len(sample_reports),
            "expected_passed_count": sum(1 for sample in sample_reports if sample["expected_status"] == "passed"),
            "expected_blocked_count": sum(1 for sample in sample_reports if sample["expected_status"] == "blocked"),
            "expected_warning_count": sum(1 for sample in sample_reports if sample["expected_status"] == "warning"),
            "actual_passed_count": sum(1 for sample in sample_reports if sample["actual_status"] == "passed"),
            "actual_blocked_count": sum(1 for sample in sample_reports if sample["actual_status"] == "blocked"),
            "actual_warning_count": sum(1 for sample in sample_reports if sample["actual_status"] == "warning"),
            "expected_status_match_count": status_match_count,
            "unexpected_status_count": unexpected_status_count,
            "false_pass_count": false_pass_count,
            "false_block_count": false_block_count,
            "keyword_recall_average": _avg(keyword_recalls),
        },
        "default_provider_decision": {
            "file_asr_default": "local_funasr_batch",
            "realtime_asr_default_order": ["sherpa_onnx_realtime", "funasr_realtime"],
            "remote_asr_default_enabled": False,
            "semantic_quality_gate_required": True,
            "decision_status": "accepted"
            if unexpected_status_count == 0 and false_pass_count == 0 and false_block_count == 0
            else "blocked",
        },
        "samples": sample_reports,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _score_sample(sample: dict[str, Any]) -> dict[str, Any]:
    text = str(sample.get("text") or "")
    expected_status = str(sample.get("expected_status") or "")
    expected_keywords = [str(item) for item in list(sample.get("expected_keywords") or [])]
    quality = evaluate_semantic_quality(text)
    actual_status = str(quality.get("status") or "unknown")
    matched_keywords = [
        keyword
        for keyword in expected_keywords
        if _normalize(keyword) in _normalize(text)
        or _normalize(keyword) in {_normalize(item) for item in list(quality.get("matched_entities") or [])}
    ]
    keyword_recall = None
    if expected_keywords:
        keyword_recall = round(len(matched_keywords) / len(expected_keywords), 6)
    return {
        "id": str(sample.get("id") or ""),
        "text": text,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "status_matches_expected": actual_status == expected_status,
        "expected_keywords": expected_keywords,
        "matched_expected_keywords": matched_keywords,
        "keyword_recall": keyword_recall,
        "semantic_quality": quality,
    }


def _normalize(value: str) -> str:
    return "".join(str(value or "").lower().split())


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Meeting Copilot ASR semantic quality report.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    report = run_semantic_quality_report(args.dataset, args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["default_provider_decision"]["decision_status"] == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())

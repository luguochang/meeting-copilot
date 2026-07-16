#!/usr/bin/env python3
"""Evaluate local realtime partial hints without remote ASR or LLM calls."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
if str(WEB_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND_ROOT))

from meeting_copilot_web_mvp.asr_live_events import build_asr_live_events  # noqa: E402


REPORT_VERSION = "partial_hint_eval.v1"


def _load_dataset(dataset_path: Path) -> dict[str, Any]:
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("partial hint dataset must be a JSON object")
    cases = data.get("cases")
    if not isinstance(cases, list) or not all(isinstance(item, dict) for item in cases):
        raise ValueError("partial hint dataset cases must be a list of objects")
    return data


def _stream_event_for_case(case: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "event_type": "partial",
        "segment_id": str(case["case_id"]),
        "text": str(case["text"]),
        "start_ms": max(0, index - 1) * 600,
        "end_ms": index * 600,
        "received_at_ms": index * 600 + 50,
        "confidence": float(case.get("confidence", 0.82)),
    }


def _prediction_for_case(case: dict[str, Any], index: int) -> str | None:
    events = build_asr_live_events(
        session_id=f"partial_hint_eval_{case['case_id']}",
        provider="local_no_cost_partial_hint_eval",
        is_mock=False,
        streaming_events=[_stream_event_for_case(case, index)],
    )
    hints = [event for event in events if event["event_type"] == "partial_hint_event"]
    if not hints:
        return None
    return str(hints[0]["payload"]["hint_type"])


def _duplicate_group_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        group = case.get("duplicate_group")
        if group:
            grouped[str(group)].append(case)

    groups: list[dict[str, Any]] = []
    progressive_duplicate_hint_count = 0
    for group_name, group_cases in sorted(grouped.items()):
        streaming_events = [
            _stream_event_for_case(case, index)
            for index, case in enumerate(group_cases, start=1)
        ]
        events = build_asr_live_events(
            session_id=f"partial_hint_eval_duplicate_{group_name}",
            provider="local_no_cost_partial_hint_eval",
            is_mock=False,
            streaming_events=streaming_events,
        )
        hints = [event for event in events if event["event_type"] == "partial_hint_event"]
        progressive_duplicate_hint_count += len(hints)
        groups.append(
            {
                "duplicate_group": group_name,
                "case_ids": [str(case["case_id"]) for case in group_cases],
                "input_count": len(group_cases),
                "hint_count": len(hints),
                "dedupe_keys": [
                    str(event.get("payload", {}).get("dedupe_key"))
                    for event in hints
                ],
            }
        )

    return {
        "progressive_duplicate_hint_count": progressive_duplicate_hint_count,
        "groups": groups,
    }


def _quality_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    true_negative = 0
    for result in results:
        expected = result["expected_hint_type"]
        predicted = result["predicted_hint_type"]
        if expected is None and predicted is None:
            true_negative += 1
        elif expected is None and predicted is not None:
            false_positive += 1
        elif expected is not None and predicted is None:
            false_negative += 1
        elif expected == predicted:
            true_positive += 1
        else:
            false_positive += 1
            false_negative += 1

    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
    }


def build_partial_hint_eval_report(
    *,
    dataset_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    dataset = _load_dataset(dataset_path)
    cases = list(dataset["cases"])
    results: list[dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        expected = case.get("expected_hint_type")
        predicted = _prediction_for_case(case, index)
        passed = expected == predicted
        results.append(
            {
                "case_id": str(case["case_id"]),
                "expected_hint_type": expected,
                "predicted_hint_type": predicted,
                "passed": passed,
                "tags": list(case.get("tags") or []),
                **({"duplicate_group": case["duplicate_group"]} if case.get("duplicate_group") else {}),
            }
        )

    metrics = _quality_metrics(results)
    failed = [result for result in results if not result["passed"]]
    tag_counts = Counter(tag for result in results for tag in result["tags"])
    report = {
        "report_mode": "partial_hint_eval",
        "report_version": REPORT_VERSION,
        "status": "passed" if not failed and metrics["precision"] >= 0.8 and metrics["recall"] >= 0.8 else "failed",
        "dataset_version": dataset.get("dataset_version"),
        "case_count": len(results),
        "passed_count": len(results) - len(failed),
        "failed_count": len(failed),
        "metrics": metrics,
        "tag_counts": dict(sorted(tag_counts.items())),
        "duplicate_suppression": _duplicate_group_report(cases),
        "remote_llm_called": False,
        "remote_asr_called": False,
        "results": results,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None, stdout: TextIO = sys.stdout) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        default="data/asr_eval/partial_hint/partial_hint_cases.json",
        help="Dataset JSON path, relative to the repository root unless absolute.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write summary.json.",
    )
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = REPO_ROOT / dataset_path
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = REPO_ROOT / output_dir

    report = build_partial_hint_eval_report(
        dataset_path=dataset_path,
        output_dir=output_dir,
    )
    stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

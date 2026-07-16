#!/usr/bin/env python3
"""Run and review the 20-scenario Chinese technical suggestion value gate."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
CORE_ROOT = REPO_ROOT / "code" / "core"
for import_root in (BACKEND_ROOT, CORE_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from meeting_copilot_web_mvp import llm_service  # noqa: E402
from meeting_copilot_web_mvp.streaming_llm_provider import (  # noqa: E402
    OpenAICompatibleStreamingProvider,
    StreamingProviderError,
)
from meeting_copilot_web_mvp.v2_streaming_suggestions import (  # noqa: E402
    build_realtime_suggestion_messages,
)


DATASET_SCHEMA = "meeting_copilot.chinese_technical_trigger_points.v1"
RESULTS_SCHEMA = "meeting_copilot.chinese_suggestion_value_results.v1"
REPORT_SCHEMA = "meeting_copilot.chinese_suggestion_value_gate.v1"
DEFAULT_DATASET = REPO_ROOT / "data/product_value_gate/chinese_technical_trigger_points.v1.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts/tmp/product_value_gate"
TIMELY_LATENCY_MS = 20_000


def load_dataset(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    scenarios = payload.get("scenarios")
    if payload.get("schema_version") != DATASET_SCHEMA or not isinstance(scenarios, list):
        raise ValueError("invalid Chinese trigger-point dataset")
    if len(scenarios) != 20:
        raise ValueError("Chinese trigger-point dataset must contain exactly 20 scenarios")
    scenario_ids = [str(item.get("id") or "") for item in scenarios if isinstance(item, dict)]
    if len(set(scenario_ids)) != 20 or any(not value for value in scenario_ids):
        raise ValueError("Chinese trigger-point scenario ids must be unique and non-empty")
    for scenario in scenarios:
        for field in ("title", "evidence", "gap"):
            if not str(scenario.get(field) or "").strip():
                raise ValueError(f"scenario {scenario['id']} is missing {field}")
    return [dict(item) for item in scenarios]


async def run_remote_scenarios(
    scenarios: list[dict[str, Any]],
    *,
    config: llm_service.LlmConfig,
) -> dict[str, Any]:
    timeout_seconds = min(float(config.timeout_seconds), 30.0)
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
        provider = OpenAICompatibleStreamingProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            client=client,
            timeout_seconds=timeout_seconds,
        )
        for scenario in scenarios:
            started_at = time.perf_counter()
            try:
                result = await provider.complete(
                    build_realtime_suggestion_messages(
                        gap=scenario["gap"],
                        evidence=scenario["evidence"],
                    ),
                    idempotency_key=f"value-gate:{DATASET_SCHEMA}:{scenario['id']}",
                    temperature=0.2,
                    max_completion_tokens=128,
                )
                suggestion = " ".join(result.content.split())
                elapsed_ms = round((time.perf_counter() - started_at) * 1_000, 3)
                rows.append(
                    {
                        "scenario_id": scenario["id"],
                        "title": scenario["title"],
                        "evidence": scenario["evidence"],
                        "gap": scenario["gap"],
                        "anchor_terms": list(scenario.get("anchor_terms") or []),
                        "expected_focus_terms": list(scenario.get("expected_focus_terms") or []),
                        "evidence_ids": [f"evidence:{scenario['id']}"],
                        "status": "succeeded",
                        "suggestion": suggestion,
                        "elapsed_ms": elapsed_ms,
                        "ttft_ms": round(result.timings.time_to_first_token_seconds * 1_000, 3),
                        "transport_mode": result.transport_mode.value,
                        "usage": _usage_dict(result.usage),
                        "automatic_review": _automatic_review(scenario, suggestion),
                    }
                )
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started_at) * 1_000, 3)
                rows.append(
                    {
                        "scenario_id": scenario["id"],
                        "title": scenario["title"],
                        "evidence_ids": [f"evidence:{scenario['id']}"],
                        "status": "failed",
                        "suggestion": "",
                        "elapsed_ms": elapsed_ms,
                        "error_class": type(exc).__name__,
                        "error_category": (
                            exc.category.value if isinstance(exc, StreamingProviderError) else None
                        ),
                    }
                )
    return {
        "schema_version": RESULTS_SCHEMA,
        "provider": llm_service.provider_identifier(config),
        "model": config.model,
        "gateway_base_url_kind": llm_service.gateway_base_url_kind(config.base_url),
        "is_mock": bool(config.is_mock),
        "scenario_count": len(rows),
        "results": rows,
        "usage": _aggregate_usage(rows),
        "privacy_cost_flags": {
            "llm_called": True,
            "remote_asr_called": False,
            "raw_audio_uploaded": False,
            "user_audio_read": False,
        },
    }


def build_gate_report(
    *,
    results_payload: dict[str, Any],
    annotations_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    rows = list(results_payload.get("results") or [])
    result_ids = {str(row.get("scenario_id") or "") for row in rows}
    if results_payload.get("schema_version") != RESULTS_SCHEMA or len(rows) != 20 or len(result_ids) != 20:
        raise ValueError("value-gate results must contain 20 unique scenarios")

    reviews = list((annotations_payload or {}).get("reviews") or [])
    review_by_id = {str(review.get("scenario_id") or ""): dict(review) for review in reviews}
    reviews_complete = len(review_by_id) == 20 and set(review_by_id) == result_ids
    succeeded = [row for row in rows if row.get("status") == "succeeded"]
    evidence_correct_count = 0
    directly_askable_timely_count = 0
    duplicate_count = 0
    unsupported_claim_count = 0
    if reviews_complete:
        for row in rows:
            review = review_by_id[str(row["scenario_id"])]
            unsupported = bool(review.get("unsupported_claim"))
            if bool(review.get("evidence_correct")) and not unsupported:
                evidence_correct_count += 1
            if bool(review.get("directly_askable")) and float(row.get("elapsed_ms") or 0) <= TIMELY_LATENCY_MS:
                directly_askable_timely_count += 1
            if str(review.get("duplicate_of") or "").strip():
                duplicate_count += 1
            if unsupported:
                unsupported_claim_count += 1

    formal_without_evidence_count = sum(
        1 for row in succeeded if not list(row.get("evidence_ids") or [])
    )
    blockers: list[str] = []
    if len(succeeded) != 20:
        blockers.append("provider_results_incomplete")
    if reviews_complete:
        if evidence_correct_count < 18:
            blockers.append("correct_evidence_below_18_of_20")
        if directly_askable_timely_count < 16:
            blockers.append("directly_askable_timely_below_16_of_20")
        if duplicate_count > 2:
            blockers.append("duplicate_formal_suggestions_above_2_of_20")
        if formal_without_evidence_count:
            blockers.append("formal_suggestion_without_evidence")
    verdict = "awaiting_manual_review" if not reviews_complete else "go" if not blockers else "no_go"
    return {
        "schema_version": REPORT_SCHEMA,
        "verdict": verdict,
        "blockers": blockers,
        "thresholds": {
            "correct_evidence_min": 18,
            "directly_askable_timely_min": 16,
            "duplicate_formal_max": 2,
            "formal_without_evidence_max": 0,
            "timely_latency_ms": TIMELY_LATENCY_MS,
        },
        "counts": {
            "scenario_count": len(rows),
            "provider_succeeded": len(succeeded),
            "manual_reviews": len(review_by_id),
            "evidence_correct": evidence_correct_count,
            "directly_askable_timely": directly_askable_timely_count,
            "duplicates": duplicate_count,
            "unsupported_claims": unsupported_claim_count,
            "formal_without_evidence": formal_without_evidence_count,
            "automatic_ready": sum(
                1 for row in succeeded if bool((row.get("automatic_review") or {}).get("ready"))
            ),
        },
        "provider": {
            "provider": results_payload.get("provider"),
            "model": results_payload.get("model"),
            "gateway_base_url_kind": results_payload.get("gateway_base_url_kind"),
            "is_mock": results_payload.get("is_mock"),
        },
        "usage": dict(results_payload.get("usage") or {}),
        "privacy_cost_flags": dict(results_payload.get("privacy_cost_flags") or {}),
        "reviews": [review_by_id[key] for key in sorted(review_by_id)] if reviews_complete else [],
    }


def _automatic_review(scenario: dict[str, Any], suggestion: str) -> dict[str, Any]:
    anchor_terms = [str(value) for value in scenario.get("anchor_terms") or []]
    focus_terms = [str(value) for value in scenario.get("expected_focus_terms") or []]
    assertive_terms = ["必须", "一定", "已经确认", "肯定", "无需确认"]
    starts_as_advice = suggestion.startswith(("建议确认", "是否考虑"))
    question_like = suggestion.endswith(("？", "?"))
    anchor_match = any(term.lower() in suggestion.lower() for term in anchor_terms)
    focus_match = any(term.lower() in suggestion.lower() for term in focus_terms)
    no_assertive_claim = not any(term in suggestion for term in assertive_terms)
    return {
        "starts_as_advice": starts_as_advice,
        "question_like": question_like,
        "anchor_match": anchor_match,
        "focus_match": focus_match,
        "no_assertive_claim": no_assertive_claim,
        "within_length": 8 <= len(suggestion) <= 240,
        "ready": all(
            (starts_as_advice, question_like, anchor_match, focus_match, no_assertive_claim, 8 <= len(suggestion) <= 240)
        ),
    }


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _aggregate_usage(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        key: sum(int((row.get("usage") or {}).get(key) or 0) for row in rows)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON object required: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--existing-results", type=Path)
    parser.add_argument("--annotations", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_root / args.run_id
    if args.existing_results:
        results_payload = _read_json(args.existing_results)
    else:
        scenarios = load_dataset(args.dataset)
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise SystemExit("LLM_GATEWAY_* is not configured")
        if config.is_mock:
            raise SystemExit("mock LLM provider cannot count for the value gate")
        results_payload = asyncio.run(run_remote_scenarios(scenarios, config=config))
        _write_json(output_dir / "results.json", results_payload)
    annotations = _read_json(args.annotations) if args.annotations else None
    report = build_gate_report(results_payload=results_payload, annotations_payload=annotations)
    _write_json(output_dir / "report.json", report)
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "verdict": report["verdict"],
                "blockers": report["blockers"],
                "counts": report["counts"],
                "usage": report["usage"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 2 if report["verdict"] == "no_go" else 0


if __name__ == "__main__":
    raise SystemExit(main())

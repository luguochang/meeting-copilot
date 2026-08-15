from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "code/web_mvp/backend"
CORE_ROOT = REPO_ROOT / "code/core"
for import_root in (str(BACKEND_ROOT), str(CORE_ROOT), str(REPO_ROOT)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from meeting_copilot_web_mvp.llm_service import LlmConfig  # noqa: E402
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar  # noqa: E402
from meeting_copilot_web_mvp.realtime_intelligence import (  # noqa: E402
    RealtimeIntelligenceRequest,
    run_realtime_coach_routed,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (  # noqa: E402
    OpenAICompatibleStreamingProvider,
)
from tools.realtime_coach_eval.score import score_predictions  # noqa: E402


def load_dataset(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"dataset line {line_number} must be an object")
        case = dict(value)
        if not str(case.get("case_id") or "").strip():
            raise ValueError(f"dataset line {line_number} is missing case_id")
        if not isinstance(case.get("expected"), Mapping):
            raise ValueError(f"dataset line {line_number} is missing expected")
        cases.append(case)
    if not cases:
        raise ValueError("dataset must contain at least one case")
    return cases


def request_from_case(case: Mapping[str, Any]) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id=str(case.get("session_id") or case["case_id"]),
        state_revision=case.get("state_revision") or 1,
        new_paragraphs=case.get("new_paragraphs") or [],
        context_paragraphs=case.get("context_paragraphs") or [],
        semantic_windows=case.get("semantic_windows") or [],
        rolling_state=case.get("rolling_state") or {},
        glossary=case.get("glossary") or [],
        meeting_goal=case.get("meeting_goal"),
        allow_paragraph_revisions=False,
    )


def prediction_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    intervention = result.get("intervention")
    if intervention is None:
        return {"action": "silent"}
    return {
        "action": "intervention",
        "event_type": intervention.event_type,
        "title": intervention.title,
        "recommendation": intervention.recommendation,
        "confidence": intervention.confidence,
        "evidence_segment_ids": list(intervention.evidence_segment_ids),
    }


async def replay_mode(
    cases: list[dict[str, Any]],
    *,
    runtime_name: str,
    config: LlmConfig,
    client: httpx.AsyncClient,
    pi_runtime: PiCoachSidecar,
) -> list[dict[str, Any]]:
    provider = OpenAICompatibleStreamingProvider(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        client=client,
        timeout_seconds=min(config.timeout_seconds, 30.0),
        api_style=config.api_style,
    )
    records: list[dict[str, Any]] = []
    for case in cases:
        started_at = time.perf_counter()
        base_record = {
            "case_id": case["case_id"],
            "difficulty": list(case.get("difficulty") or []),
            "expected": dict(case["expected"]),
            "runtime_requested": runtime_name,
        }
        try:
            result = await run_realtime_coach_routed(
                request=request_from_case(case),
                provider=provider,
                requested_runtime=runtime_name,
                pi_runtime=pi_runtime,
                pi_provider_config={
                    "base_url": config.base_url,
                    "api_key": config.api_key,
                    "model": config.model,
                    "api_style": config.api_style,
                    "timeout_seconds": min(config.timeout_seconds, 25.0),
                },
            )
            metrics = result.get("agent_metrics") if isinstance(result.get("agent_metrics"), Mapping) else {}
            records.append(
                {
                    **base_record,
                    "runtime_used": result.get("runtime_used"),
                    "fallback_error_code": result.get("fallback_error_code"),
                    "prediction": prediction_from_result(result),
                    "latency_ms": round((time.perf_counter() - started_at) * 1_000, 2),
                    "agent_turns": metrics.get("turns"),
                    "agent_tool_calls": metrics.get("tool_calls"),
                    "usage": result.get("usage"),
                }
            )
        except Exception as exc:
            records.append(
                {
                    **base_record,
                    "runtime_used": None,
                    "prediction": {"action": "error"},
                    "latency_ms": round((time.perf_counter() - started_at) * 1_000, 2),
                    "error": {"class": type(exc).__name__, "message": str(exc)[:300]},
                }
            )
    return records


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = LlmConfig.from_env()
    if config is None:
        raise RuntimeError(
            "LLM provider is not configured; set LLM_GATEWAY_BASE_URL, LLM_GATEWAY_API_KEY, and LLM_GATEWAY_MODEL"
        )
    cases = load_dataset(args.dataset)
    modes = ["direct", "pi"] if args.runtime == "both" else [args.runtime]
    pi_runtime = PiCoachSidecar()
    results: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            for mode in modes:
                records = await replay_mode(
                    cases,
                    runtime_name=mode,
                    config=config,
                    client=client,
                    pi_runtime=pi_runtime,
                )
                results[mode] = {
                    "score": score_predictions(records),
                    "records": records,
                }
    finally:
        await asyncio.to_thread(pi_runtime.close)
    return {
        "schema_version": "talktrace.realtime_coach_eval.v1",
        "dataset": str(args.dataset),
        "model": config.model,
        "api_style": config.api_style,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay source-aware transcripts through direct and/or Pi coach runtimes.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--runtime", choices=("direct", "pi", "both"), default="both")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = asyncio.run(run(args))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

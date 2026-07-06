from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.incremental_scheduler import (
    AnalysisDecision,
    AnalysisSchedulerConfig,
    AnalysisSchedulerState,
    TranscriptUpdate,
    decide_analysis,
)
from scripts.streaming_contract import (
    StreamingTranscriptEvent,
    build_provider_transcript_from_stream,
)


@dataclass(frozen=True)
class RealtimeSimulationResult:
    provider_transcript: dict
    decisions: list[AnalysisDecision]
    llm_trigger_count: int


def run_realtime_simulation(
    provider: str,
    events: list[StreamingTranscriptEvent],
    scheduler_config: AnalysisSchedulerConfig,
    state_change_segment_ids: set[str] | None = None,
) -> RealtimeSimulationResult:
    scheduler_state = AnalysisSchedulerState()
    decisions: list[AnalysisDecision] = []
    state_change_ids = state_change_segment_ids or set()

    for event in events:
        decision = decide_analysis(
            TranscriptUpdate(
                update_type=event.event_type,
                segment_id=event.segment_id,
                text=event.text,
                received_at_ms=event.received_at_ms,
                has_state_change=_has_state_change(event, state_change_ids),
            ),
            state=scheduler_state,
            config=scheduler_config,
        )
        decisions.append(decision)
        scheduler_state = decision.state_after

    return RealtimeSimulationResult(
        provider_transcript=build_provider_transcript_from_stream(provider, events),
        decisions=decisions,
        llm_trigger_count=sum(1 for decision in decisions if decision.should_call_llm),
    )


def _has_state_change(event: StreamingTranscriptEvent, state_change_ids: set[str]) -> bool:
    return event.segment_id in state_change_ids or (
        event.revision_of is not None and event.revision_of in state_change_ids
    )


def result_to_dict(result: RealtimeSimulationResult) -> dict[str, Any]:
    return {
        "provider_transcript": result.provider_transcript,
        "decisions": [asdict(decision) for decision in result.decisions],
        "llm_trigger_count": result.llm_trigger_count,
    }


def load_streaming_events(path: Path) -> list[StreamingTranscriptEvent]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("events JSON must be a list of streaming transcript events")
    return [StreamingTranscriptEvent(**item) for item in payload]


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a mock realtime transcript simulation.")
    parser.add_argument("--events-json", required=True, type=Path)
    parser.add_argument("--provider", default="mock-stream")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-final-interval-ms", default=30_000, type=int)
    parser.add_argument("--min-state-change-interval-ms", default=10_000, type=int)
    parser.add_argument("--max-calls-per-hour", default=80, type=int)
    parser.add_argument(
        "--state-change-segment-id",
        action="append",
        default=[],
        help="Segment id that should use the shorter state-change trigger interval.",
    )
    args = parser.parse_args()

    result = run_realtime_simulation(
        provider=args.provider,
        events=load_streaming_events(args.events_json),
        scheduler_config=AnalysisSchedulerConfig(
            min_final_interval_ms=args.min_final_interval_ms,
            min_state_change_interval_ms=args.min_state_change_interval_ms,
            max_calls_per_hour=args.max_calls_per_hour,
        ),
        state_change_segment_ids=set(args.state_change_segment_id),
    )
    output = json.dumps(result_to_dict(result), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()

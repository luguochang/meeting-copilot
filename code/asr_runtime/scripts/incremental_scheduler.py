from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TranscriptUpdate:
    update_type: str
    segment_id: str
    text: str
    received_at_ms: int
    has_state_change: bool = False


@dataclass(frozen=True)
class AnalysisSchedulerConfig:
    min_final_interval_ms: int = 30_000
    min_state_change_interval_ms: int = 10_000
    max_calls_per_hour: int = 80


@dataclass(frozen=True)
class AnalysisSchedulerState:
    call_timestamps_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class AnalysisDecision:
    should_call_llm: bool
    reason: str
    state_after: AnalysisSchedulerState
    cooldown_remaining_ms: int = 0


def decide_analysis(
    update: TranscriptUpdate,
    state: AnalysisSchedulerState,
    config: AnalysisSchedulerConfig,
) -> AnalysisDecision:
    if update.update_type == "partial":
        return AnalysisDecision(
            should_call_llm=False,
            reason="partial_ignored",
            state_after=state,
        )
    if update.update_type in {"error", "end_of_stream"}:
        return AnalysisDecision(
            should_call_llm=False,
            reason="control_event_ignored",
            state_after=state,
        )
    if update.update_type not in {"final", "revision"}:
        return AnalysisDecision(
            should_call_llm=False,
            reason="unsupported_update_ignored",
            state_after=state,
        )
    if not update.text.strip():
        return AnalysisDecision(
            should_call_llm=False,
            reason="empty_final_ignored",
            state_after=state,
        )

    active_calls = _calls_in_last_hour(state.call_timestamps_ms, update.received_at_ms)
    if len(active_calls) >= config.max_calls_per_hour:
        return AnalysisDecision(
            should_call_llm=False,
            reason="budget_exhausted",
            state_after=AnalysisSchedulerState(call_timestamps_ms=active_calls),
        )

    interval_ms = (
        config.min_state_change_interval_ms
        if update.has_state_change
        else config.min_final_interval_ms
    )
    last_call_ms = active_calls[-1] if active_calls else None
    if last_call_ms is not None:
        elapsed_ms = update.received_at_ms - last_call_ms
        if elapsed_ms < interval_ms:
            return AnalysisDecision(
                should_call_llm=False,
                reason="cooldown",
                state_after=AnalysisSchedulerState(call_timestamps_ms=active_calls),
                cooldown_remaining_ms=interval_ms - elapsed_ms,
            )

    reason = _trigger_reason(update)
    return AnalysisDecision(
        should_call_llm=True,
        reason=reason,
        state_after=AnalysisSchedulerState(
            call_timestamps_ms=(*active_calls, update.received_at_ms)
        ),
    )


def _calls_in_last_hour(call_timestamps_ms: tuple[int, ...], now_ms: int) -> tuple[int, ...]:
    window_start_ms = now_ms - 3_600_000
    return tuple(timestamp for timestamp in call_timestamps_ms if timestamp >= window_start_ms)


def _trigger_reason(update: TranscriptUpdate) -> str:
    if update.has_state_change:
        return "state_change"
    if update.update_type == "revision":
        return "revision"
    return "final_segment"

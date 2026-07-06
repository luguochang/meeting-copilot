from scripts.incremental_scheduler import (
    AnalysisSchedulerConfig,
    AnalysisSchedulerState,
    TranscriptUpdate,
    decide_analysis,
)


def test_scheduler_ignores_partial_updates():
    decision = decide_analysis(
        TranscriptUpdate(
            update_type="partial",
            segment_id="seg_001",
            text="先灰度",
            received_at_ms=1000,
        ),
        state=AnalysisSchedulerState(),
        config=AnalysisSchedulerConfig(),
    )

    assert decision.should_call_llm is False
    assert decision.reason == "partial_ignored"
    assert decision.state_after.call_timestamps_ms == ()


def test_scheduler_triggers_on_first_final_segment():
    decision = decide_analysis(
        TranscriptUpdate(
            update_type="final",
            segment_id="seg_001",
            text="先灰度 10%",
            received_at_ms=1000,
        ),
        state=AnalysisSchedulerState(),
        config=AnalysisSchedulerConfig(min_final_interval_ms=20_000),
    )

    assert decision.should_call_llm is True
    assert decision.reason == "final_segment"
    assert decision.state_after.call_timestamps_ms == (1000,)


def test_scheduler_ignores_empty_final_segments():
    state = AnalysisSchedulerState()

    decision = decide_analysis(
        TranscriptUpdate(
            update_type="final",
            segment_id="seg_empty",
            text="   ",
            received_at_ms=1000,
        ),
        state=state,
        config=AnalysisSchedulerConfig(),
    )

    assert decision.should_call_llm is False
    assert decision.reason == "empty_final_ignored"
    assert decision.state_after == state


def test_scheduler_treats_revision_as_stable_update():
    decision = decide_analysis(
        TranscriptUpdate(
            update_type="revision",
            segment_id="seg_001",
            text="先灰度 10%",
            received_at_ms=1000,
        ),
        state=AnalysisSchedulerState(),
        config=AnalysisSchedulerConfig(),
    )

    assert decision.should_call_llm is True
    assert decision.reason == "revision"
    assert decision.state_after.call_timestamps_ms == (1000,)


def test_scheduler_ignores_control_events():
    decision = decide_analysis(
        TranscriptUpdate(
            update_type="end_of_stream",
            segment_id="eos",
            text="",
            received_at_ms=2000,
        ),
        state=AnalysisSchedulerState(),
        config=AnalysisSchedulerConfig(),
    )

    assert decision.should_call_llm is False
    assert decision.reason == "control_event_ignored"


def test_scheduler_cools_down_regular_final_segments():
    state = AnalysisSchedulerState(call_timestamps_ms=(1000,))

    decision = decide_analysis(
        TranscriptUpdate(
            update_type="final",
            segment_id="seg_002",
            text="继续讨论监控指标",
            received_at_ms=10_000,
        ),
        state=state,
        config=AnalysisSchedulerConfig(min_final_interval_ms=20_000),
    )

    assert decision.should_call_llm is False
    assert decision.reason == "cooldown"
    assert decision.cooldown_remaining_ms == 11_000
    assert decision.state_after == state


def test_scheduler_allows_state_change_after_shorter_interval():
    decision = decide_analysis(
        TranscriptUpdate(
            update_type="final",
            segment_id="seg_002",
            text="这里还没有确认回滚负责人",
            received_at_ms=12_000,
            has_state_change=True,
        ),
        state=AnalysisSchedulerState(call_timestamps_ms=(1000,)),
        config=AnalysisSchedulerConfig(
            min_final_interval_ms=20_000,
            min_state_change_interval_ms=10_000,
        ),
    )

    assert decision.should_call_llm is True
    assert decision.reason == "state_change"
    assert decision.state_after.call_timestamps_ms == (1000, 12_000)


def test_scheduler_enforces_hourly_call_budget():
    state = AnalysisSchedulerState(call_timestamps_ms=(1000, 5000))

    decision = decide_analysis(
        TranscriptUpdate(
            update_type="final",
            segment_id="seg_003",
            text="继续讨论测试计划",
            received_at_ms=30_000,
        ),
        state=state,
        config=AnalysisSchedulerConfig(max_calls_per_hour=2),
    )

    assert decision.should_call_llm is False
    assert decision.reason == "budget_exhausted"
    assert decision.state_after == state

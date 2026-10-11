from meeting_copilot_web_mvp.automatic_coach_policy import (
    automatic_deep_coach_admission,
    automatic_intelligence_admission,
    discussion_understanding_ready,
)


def test_automatic_readiness_waits_for_new_state_even_when_old_state_is_successful():
    discussion = {"context": {"phase": "discussing", "last_semantic_seq": 3}}
    assert discussion_understanding_ready(discussion, [])
    for status in ("pending", "running", "retry_wait"):
        assert not discussion_understanding_ready(discussion, [{"status": status, "request_context": {"task_mode": "discussion_state"}}])
    assert not discussion_understanding_ready({"context": {"phase": "discussing", "last_semantic_seq": 0}}, [])
    assert not discussion_understanding_ready({"context": {"phase": "needs_review", "last_semantic_seq": 3}}, [])


def admission(**values):
    return automatic_deep_coach_admission(
        **{"policy": "low_frequency", "meeting_goal": None, "has_selected_evidence": False,
           "prior_jobs": [], "now_ms": 100_000, **values}
    )


def test_low_frequency_fast_does_not_automatically_buy_a_second_answer():
    assert not admission().allowed
    assert not admission(policy="off", meeting_goal="核对发布条件", has_selected_evidence=True).allowed
    assert admission(meeting_goal="核对发布条件").allowed
    assert admission(has_selected_evidence=True).allowed


def test_low_frequency_counts_durable_attempts_including_failures():
    job = {"id": "prior", "trigger_type": "answer_ready", "created_at_ms": 41_000, "status": "failed"}
    assert not admission(meeting_goal="核对发布条件", prior_jobs=[job]).allowed
    assert admission(meeting_goal="核对发布条件", prior_jobs=[job], now_ms=101_000).allowed
    assert admission(meeting_goal="核对发布条件", prior_jobs=[job], current_job_id="prior").allowed
    assert admission(policy="standard", prior_jobs=[job]).allowed
    assert not admission(policy="standard", prior_jobs=[job], now_ms=60_999).allowed


def test_explicit_work_or_local_skips_do_not_consume_auto_expansion_cadence():
    jobs = [
        {"id": "manual", "trigger_type": "user_request", "created_at_ms": 99_000},
        {"id": "skip", "trigger_type": "answer_ready", "created_at_ms": 99_000,
         "output": {"transport_mode": "automatic_coach_policy_suppressed"}},
    ]
    assert admission(meeting_goal="核对发布条件", prior_jobs=jobs).allowed


def test_background_cadence_follows_actual_provider_attempts_not_local_skips():
    jobs = [{"id": "paid", "trigger_type": "delta", "created_at_ms": 41_000,
             "output": {"provider_attempt_count": 1}},
            {"id": "skip", "trigger_type": "delta", "created_at_ms": 99_000,
             "output": {"provider_attempt_count": 0}}]
    kwargs = {"policy": "low_frequency", "prior_jobs": jobs, "now_ms": 100_000, "current_job_id": "new"}
    assert not automatic_intelligence_admission(**kwargs).allowed
    assert automatic_intelligence_admission(**{**kwargs, "now_ms": 101_000}).allowed
    assert automatic_intelligence_admission(**kwargs, urgent=True, has_new_evidence=True).allowed
    assert automatic_intelligence_admission(**{**kwargs, "policy": "standard"}).allowed


def test_busy_background_admission_does_not_burst_and_manual_is_independent():
    jobs = [{"id": "busy", "trigger_type": "answer_ready", "created_at_ms": 99_000,
             "status": "running"}, {"id": "manual", "trigger_type": "user_request",
             "created_at_ms": 99_000, "status": "running"}]
    kwargs = {"policy": "low_frequency", "prior_jobs": jobs, "now_ms": 100_000}
    assert not automatic_intelligence_admission(**kwargs, current_job_id="new").allowed
    assert automatic_intelligence_admission(**kwargs, current_job_id="busy").allowed

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


TOOLS = Path(__file__).resolve().parents[4] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("global_timer_acceptance", TOOLS / "participation_global_timer_acceptance.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_global_plan_does_not_read_runtime_or_call_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.value, "ROOT", tmp_path)
    monkeypatch.setattr(runner.value, "RUNTIME", tmp_path / "no-runtime")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run attempted actual execution")

    monkeypatch.setattr(runner, "execute", forbidden)
    assert runner.main(["--report", "artifacts/global-plan.json"]) == 0
    report = json.loads((tmp_path / "artifacts/global-plan.json").read_text(encoding="utf-8"))
    assert report["executed"] is False
    assert report["timer_gates_overridden"] is False
    assert report["global_timer_directly_invoked_by_tool"] is False
    assert report["budget"] == {"state_host_jobs": 1, "automatic_host_jobs": 1, "manual_host_jobs": 0, "retries": 0}


@pytest.mark.parametrize("seconds", [30, 120])
def test_same_basis_observation_is_explicit_and_bounded_without_provider(tmp_path, monkeypatch, seconds):
    monkeypatch.setattr(runner.value, "ROOT", tmp_path)
    monkeypatch.setattr(runner.value, "RUNTIME", tmp_path / "no-runtime")
    monkeypatch.setattr(runner, "execute", lambda _report: pytest.fail("dry-run executed"))
    assert runner.main(["--report", "artifacts/global-plan.json", "--same-basis-seconds", str(seconds)]) == 0
    report = json.loads((tmp_path / "artifacts/global-plan.json").read_text(encoding="utf-8"))
    assert report["same_basis_observation_seconds"] == seconds
    assert report["budget"]["state_host_jobs"] == report["budget"]["automatic_host_jobs"] == 1
    for invalid in (29, 121):
        with pytest.raises(SystemExit):
            runner.main(["--same-basis-seconds", str(invalid)])


def _job(*, state=False, attempts=1, kind="intelligence", trigger="discussion_update"):
    return {"kind": kind, "trigger_type": trigger, "attempts": attempts,
        "request_context": {"task_mode": "discussion_state"} if state else {}}


def test_global_budget_counts_retry_attempts_and_unrelated_started_lanes():
    jobs = [_job(state=True), _job(), _job(attempts=0, kind="correction")]
    assert not runner.over_budget(runner.started_budget(jobs))
    jobs[0]["attempts"] = 2
    assert runner.over_budget(runner.started_budget(jobs))
    jobs[0]["attempts"] = 1
    jobs[-1]["attempts"] = 1
    assert runner.over_budget(runner.started_budget(jobs))
    assert runner.over_budget(runner.started_budget([_job(state=True), _job(), _job()]))


def test_existing_live_scope_aborts_before_http_seed_or_writes(tmp_path, monkeypatch):
    database = tmp_path / "meeting_copilot.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meetings(id TEXT PRIMARY KEY,state TEXT)")
        connection.execute("INSERT INTO meetings VALUES('existing-live','live')")
    monkeypatch.setattr(runner.value, "RUNTIME", tmp_path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("live-scope guard allowed mutable repository or HTTP")

    monkeypatch.setattr(runner.value, "open_synthetic_store", forbidden)
    monkeypatch.setattr(runner.httpx, "Client", forbidden)
    report = {}
    with pytest.raises(RuntimeError, match="zero live scopes"):
        runner.execute(report)
    assert report == {"preexisting_live_ids": ["existing-live"]}
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT id,state FROM meetings").fetchall() == [("existing-live", "live")]


def test_observer_does_not_directly_invoke_scheduler_admission_or_coach():
    tree = ast.parse((TOOLS / "participation_global_timer_acceptance.py").read_text(encoding="utf-8"))
    forbidden = {"enqueue_discussion_state_job", "enqueue_discussion_job", "automatic_discussion_admission",
        "_refresh_live_agent_work", "_run_v2_discussion_state_job", "_run_v2_intelligence_job"}
    for call in [node for node in ast.walk(tree) if isinstance(node, ast.Call)]:
        name = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else None
        assert name not in forbidden
    assert "/coach/request" not in (TOOLS / "participation_global_timer_acceptance.py").read_text(encoding="utf-8")


def test_failed_state_without_output_is_unknown_until_its_usage_is_observed():
    job = {**_job(state=True), "id": "state-failed", "status": "failed", "output": None}
    missing = runner.model_call_accounting([job], [])
    assert missing["observed_model_calls"] is None
    assert missing["observed_model_calls_minimum"] == 0
    assert missing["unaccounted_started_job_ids"] == ["state-failed"]
    accounted = runner.model_call_accounting([job], [{"purpose": "discussion_state", "total_tokens": 2120}])
    assert accounted["observed_model_calls"] == 1
    assert accounted["model_call_count_complete"] is True


def test_usage_row_does_not_hide_unknown_pi_turns_or_assume_cancelled_seed_was_called():
    failed = {**_job(), "id": "auto-failed", "output": None}
    seed = {**_job(attempts=0, kind="correction"), "id": "seed-cancelled", "output": None}
    result = runner.model_call_accounting([failed, seed], [{"purpose": "realtime_coach"}])
    assert result["observed_model_calls"] is None
    assert result["observed_model_calls_minimum"] == 1
    assert result["unaccounted_started_job_ids"] == ["auto-failed"]
    success = {**_job(), "id": "auto-success", "output": {"provider_attempt_count": 1,
        "coach": {"agent_metrics": {"turns": 2}}}}
    assert runner.model_call_accounting([success, seed], []) ["observed_model_calls"] == 2


def test_durable_projection_success_does_not_hide_this_jobs_timed_out_decision():
    job = {**_job(), "id": "actual-timeout", "status": "succeeded",
        "output": {"applied": {"coach_decision": {"status": "timed_out"}}}}
    assert runner.failed_automatic_decision([job]) == "actual-timeout"
    job["output"]["applied"]["coach_decision"]["status"] = "keep_silent"
    assert runner.failed_automatic_decision([job]) is None
    state = {**_job(state=True), "id": "state", "output": {"coach": {"status": "failed"}}}
    assert runner.failed_automatic_decision([state]) is None


def test_long_observation_allows_ttl_expiry_only_after_actual_publication_and_typed_history():
    card = {"job_id": "new-job", "decision_id": "new-decision", "analysis_kind": "participation",
        "proposal_type": "professional_proposal", "topic_id": "topic", "analysis_basis_revision": 2,
        "say_this": "独立补发通知。", "recommendation": "独立补发通知。",
        "supporting_facts": [{"text": "通知可以稍后发送", "source_ref": "s1"}], "assumptions": []}
    initial = {"job_id": "new-job", "valid_intervention": True, "current_card_for_request": True,
               "own_intervention": card}
    final = {"coach_intervention": None, "coach_history": [{**card, "valid_until_ms": 90_000}]}
    discussion = {"work_item_id": "topic", "context": {"analysis_basis_revision": 2}}
    review = runner.publication_and_history_review(initial, final, discussion)
    assert review["initial_current_card_for_request"] and review["typed_history_retained"] and review["same_topic_and_basis_retained"]
    assert review["final_history_valid_until_ms"] == 90_000
    follow_up_history = dict(card)
    follow_up_history.pop("recommendation")  # Existing FollowUp history uses say_this/question.
    assert runner.publication_and_history_review(initial, {"coach_history": [follow_up_history]}, discussion)["typed_history_retained"]
    assert not runner.publication_and_history_review({**initial, "current_card_for_request": False}, final, discussion)["initial_current_card_for_request"]
    assert not runner.publication_and_history_review(None, final, discussion)["initial_current_card_for_request"]
    assert not runner.publication_and_history_review(initial, {"coach_history": [{**card, "job_id": "old-job"}]}, discussion)["typed_history_retained"]
    assert not runner.publication_and_history_review(initial, {"coach_history": [{**card, "supporting_facts": []}]}, discussion)["typed_history_retained"]
    assert not runner.publication_and_history_review(initial, {"coach_history": [{**card, "say_this": "不同的新建议。"}]}, discussion)["typed_history_retained"]
    assert not runner.publication_and_history_review(initial, final, {"work_item_id": "new-topic", "context": {"analysis_basis_revision": 2}})["same_topic_and_basis_retained"]

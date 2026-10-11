from __future__ import annotations

import importlib.util
import json
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[4] / "tools/participation_value_acceptance.py"
spec = importlib.util.spec_from_file_location("participation_value_acceptance", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_previous_valid_card_cannot_pass_failed_current_request():
    job = {"id": "new-job", "status": "succeeded", "output": {"coach": {"status": "failed", "agent_metrics": {"provider_attempt_count": 1,
           "provider_attempted": True, "response_validation_error": "invalid evidence"}}}}
    result = runner.own_job_outcome(job, {"coach_intervention": {"job_id": "old-job", "decision_id": "old-card"}})
    assert result["valid_intervention"] is False
    assert result["current_card_for_request"] is False
    assert result["validated_silence"] is False
    assert result["validation_error"] == "invalid evidence"


def test_called_validated_silence_is_distinct_from_no_call_or_timeout():
    job = {"id": "silent", "status": "succeeded", "output": {"provider_attempt_count": 1,
        "applied": {"coach_decision": {"status": "protected_silent", "pi_provider_attempted": True}}}}
    assert runner.own_job_outcome(job, {})["validated_silence"] is True
    job["output"]["applied"]["coach_decision"]["pi_provider_attempted"] = False
    assert runner.own_job_outcome(job, {})["validated_silence"] is False
    job["output"]["applied"]["coach_decision"].update(status="timed_out", pi_provider_attempted=True)
    assert runner.own_job_outcome(job, {})["validated_silence"] is False


def test_pi_attempt_is_counted_when_semantic_branch_reports_zero_without_double_counting():
    job = {"output": {"provider_attempt_count": 0, "provider_availability": {"provider_attempt_count": 1},
        "coach": {"agent_metrics": {"provider_attempt_count": 1}}}}
    assert runner.provider_attempt_count(job) == 1
    job["output"]["provider_attempt_count"] = 2
    assert runner.provider_attempt_count(job) == 2
    assert runner.provider_attempt_count({"status": "cancelled"}) == 0


def test_paid_noop_state_is_not_mistaken_for_persistent_understanding():
    entry = {"case_id": "requirement_business_boundary", "state_jobs": [{"discussion": {"context": {"last_semantic_seq": 1}}}],
        "requests": [], "seeded_other_jobs": [], "all_jobs": []}
    result = runner.automated_review(entry)
    assert "candidate_solution_not_persisted_by_actual_state_model" in result["hard_failures"]
    assert result["automation_contract_passed"] is False


def test_fact_verification_uses_actual_registered_quote():
    result = runner.verify_facts({"source_evidence": [{"source_id": "attachment", "quote": "超时不表示通知未送达"}],
        "supporting_facts": [{"text": "通知可以稍后发送", "source_ref": "s1"}, {"text": "超时不表示通知未送达", "source_ref": "attachment"},
                             {"text": "第三方会去重", "source_ref": "attachment"}]}, {"s1": "本轮通知可以稍后发送。"})
    assert [item["verbatim"] for item in result] == [True, True, False]


def test_actual_cited_attachment_without_visible_fact_is_reported_as_explanation_gap():
    entry = {"case_id": "architecture_tradeoff_with_material", "state_jobs": [{"discussion": {"context": {"current_solution": "候选方案"}}}],
        "requests": [{"valid_intervention": True, "current_card_for_request": True, "own_intervention": {
            "analysis_kind": "participation", "source_evidence": [{"source_type": "meeting_attachment", "source_id": "doc1", "quote": "超时可能已送达"}]},
            "fact_checks": [{"verbatim": True, "source_ref": "s1"}]}], "seeded_other_jobs": [], "all_jobs": []}
    failures = runner.automated_review(entry)["hard_failures"]
    assert failures == ["referenced_attachment_fact_not_visible_in_supporting_facts"]
    entry["requests"][0]["fact_checks"].append({"verbatim": True, "source_ref": "doc1"})
    assert runner.automated_review(entry)["automation_contract_passed"]


def test_called_silence_does_not_hide_stale_topic_state():
    entry = {"case_id": "topic_switch_and_silence", "state_jobs": [{"discussion": {"context": {
        "open_points": [{"text": "旧题问题"}], "current_solution": "已确认通知"}}}],
        "requests": [{"valid_intervention": True, "current_card_for_request": True, "own_intervention": {"analysis_kind": "participation"},
                      "fact_checks": [{"verbatim": True, "source_ref": "old"}]},
                     {"valid_intervention": False, "validated_silence": True, "topic_changed": True,
                      "previous_card_is_current": False, "fact_checks": []}], "seeded_other_jobs": [], "all_jobs": []}
    assert runner.automated_review(entry)["hard_failures"] == [
        "closed_topic_open_points_carried_into_new_notice", "confirmed_notice_projected_as_candidate_solution"]


def test_missing_safe_diagnostic_is_distinct_from_available_empty_map():
    job = {"id": "failed", "status": "succeeded", "output": {"coach": {"status": "failed", "agent_metrics": {}}}}
    assert not runner.own_job_outcome(job, {})["validation_claim_fields_available"]
    job["output"]["coach"]["agent_metrics"]["response_validation_claim_fields"] = {}
    result = runner.own_job_outcome(job, {})
    assert result["validation_claim_fields_available"] is True
    assert result["validation_claim_fields"] == {}


def test_default_plan_does_not_read_runtime_or_call_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "RUNTIME", tmp_path / "does-not-exist")
    def forbidden(*_args):
        raise AssertionError("dry run attempted execution")
    monkeypatch.setattr(runner, "execute", forbidden)
    assert runner.main(["--report", "artifacts/dry-plan.json"]) == 0
    report = json.loads((tmp_path / "artifacts/dry-plan.json").read_text(encoding="utf-8"))
    assert report["executed"] is False and report["semantic_fixture_applied"] is False
    assert report["budget"]["manual_request_limit"] == 5
    assert report["budget"]["state_call_limit"] == 4
    assert report["cases"] == []
    assert report["keep_fixtures_live"] is False


def test_keep_live_is_explicit_disclosed_plan_without_capture_or_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("plan attempted execution")
    monkeypatch.setattr(runner, "execute", forbidden)
    assert runner.main(["--keep-fixtures-live", "--report", "artifacts/keep-live-plan.json"]) == 0
    report = json.loads((tmp_path / "artifacts/keep-live-plan.json").read_text(encoding="utf-8"))
    assert report["keep_fixtures_live"] is True and report["executed"] is False
    assert any("explicit isolated cleanup" in text for text in report["harness_overrides"])


def test_synthetic_live_mirror_uses_existing_repository_without_claiming_asr(tmp_path):
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    repository = SqliteAsrLiveSessionRepository(tmp_path)
    try:
        repository.create({"session_id": "mirror-test", "source": "synthetic_text_fixture", "events": []})
        turn = {"segment_id": "s1", "text": "先比较同步和异步方案。"}
        runner.mirror_synthetic_final(repository, "mirror-test", turn, 1)
        runner.mirror_synthetic_final(repository, "mirror-test", turn, 1)
        record = repository.get("mirror-test")
        assert record["source"] == "synthetic_text_fixture"
        assert len(record["events"]) == 1
        event = record["events"][0]
        assert event["event_type"] == "final" and event["normalized_text"] == turn["text"]
        assert event["synthetic_fixture"] is True
    finally:
        repository.close()

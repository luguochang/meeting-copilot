"""One isolated automatic opportunity: dry-run by default, no audio devices.

An explicitly authorized run uses one real state call and at most one automatic
Pi evaluation. It invokes the production admission and durable enqueue methods
directly, so it does not prove the global timer was exercised.
"""
from __future__ import annotations

import argparse
import json
import time
import uuid

import httpx

import participation_value_acceptance as value


def review(report):
    failures = []
    state = report.get("state") or {}
    context = (state.get("discussion") or {}).get("context") or {}
    outcome = report.get("automatic") or {}
    if state.get("status") != "succeeded" or not context.get("current_solution"):
        failures.append("actual_candidate_state_missing")
    if not outcome.get("valid_intervention") or not outcome.get("current_card_for_request"):
        failures.append("own_automatic_contribution_missing")
    if not outcome.get("fact_checks") or any(not item["verbatim"] for item in outcome.get("fact_checks") or []):
        failures.append("automatic_fact_not_verified")
    if not report.get("same_basis_zero_job"):
        failures.append("same_basis_not_coalesced")
    if not report.get("cas_retracted_card"):
        failures.append("corrected_basis_did_not_retract_card")
    if report.get("seed_other_provider_attempts", 0):
        failures.append("unbudgeted_seed_call")
    return {"hard_failures": failures, "automation_contract_passed": not failures,
            "manual_value_review": "pending", "global_timer_verified": False}


def execute(report):
    from meeting_copilot_web_mvp.automatic_coach_policy import automatic_discussion_admission
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    store = value.open_synthetic_store(value.RUNTIME / "meeting_copilot.db")
    live = SqliteAsrLiveSessionRepository(value.RUNTIME)
    client = httpx.Client(base_url="http://127.0.0.1:8768", trust_env=False, timeout=40)
    meeting_id = f"participation_automatic_{uuid.uuid4().hex[:12]}"
    report["meeting_id"] = meeting_id
    def http(method, path, **kwargs):
        result = client.request(method, path, **kwargs)
        result.raise_for_status()
        return result.json()
    def wait(job_id):
        started = time.monotonic()
        while time.monotonic() - started < 45:
            job = store.get_job(job_id)
            if job["status"] in value.TERMINALS:
                return job
            time.sleep(0.25)
        raise TimeoutError("isolated automatic job did not finish; no retry is permitted")
    try:
        http("POST", "/v2/meetings", json={"meeting_id": meeting_id, "title": "合成自动机会：权益与通知"},
             headers={"Idempotency-Key": meeting_id})
        assert store.get_meeting(meeting_id)["id"] == meeting_id
        preparation = http("GET", f"/v2/meetings/{meeting_id}/preparation")
        preparation = http("PUT", f"/v2/meetings/{meeting_id}/preparation", json={**preparation, "live_analysis_profile": "participation"})
        assert preparation["live_analysis_profile"] == "participation"
        report["live_analysis_profile"] = preparation["live_analysis_profile"]
        value.create_synthetic_live_record(live, meeting_id)
        turn = {"segment_id": "automatic-boundary-1", "text": "用户付钱后要立即看见权益，短信通知可以稍后送达。当前候选是把发权益和短信放在同一个同步流程，任何一步失败就整体重试。我们还没有讨论这种整体重试是否会影响已经生效的权益。"}
        seeded = value.seed_synthetic_final(store, live, meeting_id, turn, 1)
        report["seeded_job_ids"] = seeded["cancelled_job_ids"]
        queued = store.enqueue_discussion_state(meeting_id=meeting_id, now_ms=time.time_ns() // 1_000_000,
            cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=1)
        if queued is None:
            raise RuntimeError("single synthetic state task was not admitted")
        job = wait(queued["id"])
        output = job.get("output") or {}
        discussion = store.get_current_discussion(meeting_id)
        report["state"] = {"job_id": job["id"], "status": job["status"], "error_class": job.get("error_class"),
            "provider_attempt_count": value.provider_attempt_count(job), "usage": output.get("usage"),
            "discussion": discussion, "state_interpretation": output.get("state_interpretation")}
        if job["status"] != "succeeded" or not (discussion or {}).get("context", {}).get("current_solution"):
            report["skipped_automatic_reason"] = "actual_candidate_state_missing; no second call spent"
            return
        now = time.time_ns() // 1_000_000
        prior = [item for item in store.list_jobs(meeting_id=meeting_id, lane="intelligence")
                 if item["request_context"].get("task_mode") != "discussion_state"]
        admission = automatic_discussion_admission(policy="low_frequency", prior_jobs=prior, now_ms=now)
        report["admission"] = {"allowed": admission.allowed, "reason": admission.reason}
        if not admission.allowed:
            return
        queued = store.enqueue_discussion_update(meeting_id=meeting_id, now_ms=now)
        if queued is None:
            raise RuntimeError("production automatic enqueue did not admit new actual state")
        started = time.monotonic()
        job = wait(queued["id"])
        snapshot = http("GET", f"/v2/meetings/{meeting_id}/snapshot")
        outcome = value.own_job_outcome(job, snapshot)
        outcome.update(elapsed_ms=round((time.monotonic() - started) * 1_000, 1),
            fact_checks=value.verify_facts(outcome["own_intervention"], {turn["segment_id"]: turn["text"]}),
            request_metadata=job["request_context"], trigger_type=job["trigger_type"])
        report["automatic"] = outcome
        # No new task at the same basis, even beyond the old deadline/cooldown.
        report["same_basis_zero_job"] = store.enqueue_discussion_update(meeting_id=meeting_id, now_ms=now + 90_000) is None
        discussion = store.get_current_discussion(meeting_id)
        corrected = store.update_discussion_context(meeting_id=meeting_id, work_item_id=discussion["work_item_id"],
            expected_version=discussion["version"], now_ms=time.time_ns() // 1_000_000,
            context_patch={"current_problem": "使用者纠正：暂缓原方案讨论，先核对需求范围。"})
        report["corrected_basis"] = corrected["context"]["analysis_basis_revision"]
        report["cas_retracted_card"] = http("GET", f"/v2/meetings/{meeting_id}/snapshot").get("coach_intervention") is None
    finally:
        meeting_exists = store._conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is not None
        jobs = store.list_jobs(meeting_id=meeting_id) if meeting_exists else []
        report["all_jobs"] = [{"id": job["id"], "kind": job["kind"], "status": job["status"],
            "trigger_type": job.get("trigger_type"), "task_mode": job["request_context"].get("task_mode"),
            "durable_attempts": job["attempts"], "provider_attempt_count": value.provider_attempt_count(job)} for job in jobs]
        report["seed_other_provider_attempts"] = sum(value.provider_attempt_count(job) for job in jobs if job["id"] in report.get("seeded_job_ids", []))
        report["observed_host_provider_attempts"] = sum(value.provider_attempt_count(job) for job in jobs)
        report["observed_model_calls"] = int((report.get("state") or {}).get("provider_attempt_count") or 0) + int((report.get("automatic") or {}).get("actual_agent_turns") or 0)
        with store._write_transaction():
            store._conn.execute("UPDATE meetings SET state='ended',ended_at_ms=? WHERE id=?", (time.time_ns() // 1_000_000, meeting_id))
        report["review"] = review(report)
        live.close()
        store.close()
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/automatic-acceptance-plan.json")
    args = parser.parse_args(argv)
    destination = (value.ROOT / args.report).resolve()
    if not destination.is_relative_to((value.ROOT / "artifacts").resolve()):
        raise ValueError("Report must remain inside this checkout's artifacts")
    report = {"schema": "talktrace.automatic_opportunity_acceptance.v1", "executed": args.execute,
        "budget": {"state_attempts": 1, "automatic_pi_attempts": 1, "manual_attempts": 0, "retry": 0},
        "scope": "synthetic text; no microphone/speaker/ASR; no semantic fixture",
        "harness_overrides": ["single scope calls production admission/enqueue directly, global timer is not verified",
            "state input/cooldown gates overridden", "same-basis 90 seconds is simulated time",
            "basis correction is a synthetic user CAS action, no model called", "scope ended without paid postprocessing"]}
    try:
        if args.execute:
            execute(report)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
    finally:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"executed": args.execute, "report": str(destination), "failure": report.get("failure")}, ensure_ascii=False))
    return int(bool(report.get("failure")) or bool(args.execute and not (report.get("review") or {}).get("automation_contract_passed")))


if __name__ == "__main__":
    raise SystemExit(main())

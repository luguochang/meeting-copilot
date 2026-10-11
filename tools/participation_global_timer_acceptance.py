"""Observe the production global timer on one synthetic scope; dry-run default.

This tool never invokes state admission, automatic admission, coach/request or
executor callbacks. A coordinator must restart the isolated 8768 service with
automatic discussion enabled first. Canonical input and its disclosed synthetic
live record are the only seed; unrelated legacy jobs are cancelled atomically.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid

import httpx

import participation_value_acceptance as value


def started_budget(jobs):
    active = [job for job in jobs if int(job.get("attempts") or 0) > 0]
    state = [job for job in active if job["request_context"].get("task_mode") == "discussion_state"]
    automatic = [job for job in active if job["kind"] == "intelligence" and job["trigger_type"] == "discussion_update"
                 and not job["request_context"].get("task_mode")]
    return {"state_host_jobs": len(state), "automatic_host_jobs": len(automatic),
        "state_host_attempts": sum(int(job.get("attempts") or 0) for job in state),
        "automatic_host_attempts": sum(int(job.get("attempts") or 0) for job in automatic),
        "other_started_jobs": len(active) - len(state) - len(automatic)}


def over_budget(budget):
    return (budget["state_host_jobs"] > 1 or budget["automatic_host_jobs"] > 1
        or budget["state_host_attempts"] > 1 or budget["automatic_host_attempts"] > 1
        or budget["other_started_jobs"] > 0)


def failed_automatic_decision(jobs):
    """Durable succeeded means projected, not that a usable card succeeded."""
    for job in jobs:
        if job["trigger_type"] != "discussion_update" or job["request_context"].get("task_mode"):
            continue
        output = job.get("output") or {}
        decision = (output.get("applied") or {}).get("coach_decision") or {}
        status = decision.get("status") or (output.get("coach") or {}).get("status")
        if status in {"timed_out", "failed", "stale", "cancelled"}:
            return job["id"]
    return None


def model_call_accounting(jobs, ledger):
    """A missing failed-job output does not prove that no model was called."""
    started = [job for job in jobs if int(job.get("attempts") or 0) > 0]
    unknown = [job["id"] for job in started if not job.get("output")]
    completed_calls = len(ledger)
    # The state handler permits one call and disables repair. One unique state
    # job plus its purpose-specific usage row fully accounts for that call.
    states = [job for job in started if job["request_context"].get("task_mode") == "discussion_state"]
    state_ledger = [entry for entry in ledger if entry["purpose"] == "discussion_state"]
    if len(states) == len(state_ledger) == 1 and int(states[0]["attempts"]) == 1:
        unknown = [job_id for job_id in unknown if job_id != states[0]["id"]]
    output_calls = 0
    for job in started:
        output = job.get("output") or {}
        if job["request_context"].get("task_mode") == "discussion_state":
            output_calls += value.provider_attempt_count(job)
        else:
            decision = (output.get("applied") or {}).get("coach_decision") or {}
            metrics = (output.get("coach") or {}).get("agent_metrics") or decision.get("agent_metrics") or {}
            output_calls += int(metrics.get("turns") or value.provider_attempt_count(job))
    minimum = max(completed_calls, output_calls)
    return {"observed_completed_usage_calls": completed_calls, "observed_model_calls_minimum": minimum,
        "observed_model_calls": None if unknown else minimum, "model_call_count_complete": not unknown,
        "unaccounted_started_job_ids": unknown, "usage_ledger": ledger}


def publication_and_history_review(initial_outcome, final_snapshot, discussion):
    """Check publication inside its TTL separately from long duplicate observation."""
    initial = initial_outcome or {}
    own = initial.get("own_intervention") or {}
    history = next((item for item in final_snapshot.get("coach_history") or []
        if item.get("job_id") == initial.get("job_id")
        and item.get("decision_id") == own.get("decision_id")), None)
    typed_fields = ("analysis_kind", "proposal_type", "topic_id", "analysis_basis_revision", "supporting_facts", "assumptions")
    # Existing history is the FollowUp wire shape: say_this/question carries the
    # rendered recommendation without requiring the duplicate intervention alias.
    def rendered_text(card):
        return card.get("say_this") or card.get("recommendation") or card.get("question")
    same_history = bool(own and history is not None and rendered_text(history) == rendered_text(own)
        and all(history.get(key) == own.get(key) for key in typed_fields))
    context = (discussion or {}).get("context") or {}
    return {"initial_current_card_for_request": bool(initial.get("valid_intervention") and initial.get("current_card_for_request")),
        "typed_history_retained": same_history,
        "same_topic_and_basis_retained": bool(own and (discussion or {}).get("work_item_id") == own.get("topic_id")
            and context.get("analysis_basis_revision") == own.get("analysis_basis_revision")),
        "final_history_card": value.compact_card(history),
        "final_history_valid_until_ms": (history or {}).get("valid_until_ms")}


def execute(report):
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    database = value.RUNTIME / "meeting_copilot.db"
    with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as observer:
        live_ids = [row[0] for row in observer.execute("SELECT id FROM meetings WHERE state='live'")]
    if live_ids:
        report["preexisting_live_ids"] = live_ids
        raise RuntimeError("Global acceptance requires zero live scopes; none were modified")
    store = value.open_synthetic_store(database)
    live = SqliteAsrLiveSessionRepository(value.RUNTIME)
    client = httpx.Client(base_url="http://127.0.0.1:8768", trust_env=False, timeout=30)
    mid = f"participation_global_{uuid.uuid4().hex[:12]}"
    report["meeting_id"] = mid
    observation_seconds = int(report.get("same_basis_observation_seconds", 35))
    completed_reason = f"completed_and_observed_same_basis_{observation_seconds}_seconds"
    def http(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    try:
        http("POST", "/v2/meetings", json={"meeting_id": mid, "title": "自然定时器：付款权益与通知"}, headers={"Idempotency-Key": mid})
        assert store.get_meeting(mid)["id"] == mid
        preparation = http("GET", f"/v2/meetings/{mid}/preparation")
        preparation = http("PUT", f"/v2/meetings/{mid}/preparation", json={**preparation, "live_analysis_profile": "participation"})
        assert preparation["live_analysis_profile"] == "participation"
        report["preparation_version"] = preparation["version"]
        report["live_analysis_profile"] = preparation["live_analysis_profile"]
        value.create_synthetic_live_record(live, mid)
        text = ("用户付钱后要立即看见权益，短信通知可以稍后送达。当前候选是把发权益和短信放在同一个同步流程，任何一步失败就整体重试。"
                "我们还没有讨论这种整体重试是否会影响已经生效的权益。付款是否成功的页面展示，还需要和实际权益生效的结果一致。"
                "用户可能会重复打开购买结果页面，当前还没有明确短信失败时对页面和权益状态应如何处理。")
        assert len(text) >= 120
        report["source_characters"] = len(text)
        report["seed"] = value.seed_synthetic_final(store, live, mid, {"segment_id": "global-boundary-1", "text": text}, 1)
        report["source_seed_completed_at_ms"] = time.time_ns() // 1_000_000
        print(json.dumps({"global_scope_created": mid, "source_characters": len(text), "timer_gates_overridden": False}, ensure_ascii=False), flush=True)
        started = time.monotonic()
        stable_until = None
        seen = {}
        while time.monotonic() - started < 180:
            jobs = store.list_jobs(meeting_id=mid)
            budget = started_budget(jobs)
            report["started_budget"] = budget
            for job in jobs:
                signature = (job["status"], job["attempts"])
                if seen.get(job["id"]) != signature:
                    seen[job["id"]] = signature
                    print(json.dumps({"global_job_id": job["id"], "kind": job["kind"], "task_mode": job["request_context"].get("task_mode"),
                        "trigger_type": job["trigger_type"], "status": job["status"], "attempts": job["attempts"],
                        "elapsed_ms": round((time.monotonic() - started) * 1000)}, ensure_ascii=False), flush=True)
            if over_budget(budget):
                report["stop_reason"] = "started_host_budget_exceeded"
                break
            states = [job for job in jobs if job["request_context"].get("task_mode") == "discussion_state"]
            autos = [job for job in jobs if job["trigger_type"] == "discussion_update" and not job["request_context"].get("task_mode")]
            if states and states[0]["status"] in {"failed", "cancelled"} or autos and autos[0]["status"] in {"failed", "cancelled"}:
                report["stop_reason"] = "actual_job_failure_preserved"
                break
            failed_decision = failed_automatic_decision(jobs)
            if failed_decision is not None:
                report["stop_reason"] = "actual_decision_failure_preserved"
                report["failed_decision_job_id"] = failed_decision
                break
            if states and autos and all(job["status"] == "succeeded" for job in [states[0], autos[0]]):
                if stable_until is None:
                    initial_snapshot = http("GET", f"/v2/meetings/{mid}/snapshot")
                    report["initial_automatic_snapshot_at_ms"] = time.time_ns() // 1_000_000
                    report["initial_automatic_outcome"] = value.own_job_outcome(autos[0], initial_snapshot)
                stable_until = stable_until or time.monotonic() + observation_seconds
                if time.monotonic() >= stable_until:
                    report["stop_reason"] = completed_reason
                    break
            time.sleep(0.25)
        else:
            report["stop_reason"] = "natural_timer_timeout_180_seconds"
        snapshot = http("GET", f"/v2/meetings/{mid}/snapshot")
        jobs = store.list_jobs(meeting_id=mid)
        states = [job for job in jobs if job["request_context"].get("task_mode") == "discussion_state"]
        autos = [job for job in jobs if job["trigger_type"] == "discussion_update" and not job["request_context"].get("task_mode")]
        if states:
            job = states[0]
            output = job.get("output") or {}
            report["state"] = {"job_id": job["id"], "status": job["status"], "error_class": job.get("error_class"),
                "created_at_ms": job["created_at_ms"], "provider_attempt_count": value.provider_attempt_count(job),
                "discussion": store.get_current_discussion(mid), "state_interpretation": output.get("state_interpretation"), "usage": output.get("usage")}
            report["state"]["failure_diagnostics"] = output.get("failure_diagnostics")
            report["first_state_admission_after_seed_ms"] = job["created_at_ms"] - report["source_seed_completed_at_ms"]
            report["first_state_admission_meeting_age_ms"] = job["created_at_ms"] - store.get_meeting(mid)["created_at_ms"]
        if autos:
            job = autos[0]
            outcome = value.own_job_outcome(job, snapshot)
            if outcome.get("own_decision_status") in {"failed", "timed_out"} and not any((outcome.get("usage") or {}).values()):
                outcome["usage"] = None
                outcome["usage_unknown_reason"] = "Provider did not report usage; initialized zero counters do not establish zero tokens or cost."
            outcome.update(created_at_ms=job["created_at_ms"], fact_checks=value.verify_facts(outcome["own_intervention"], {"global-boundary-1": text}))
            report["automatic"] = outcome
        report["all_jobs"] = [{"id": job["id"], "kind": job["kind"], "status": job["status"], "attempts": job["attempts"],
            "trigger_type": job["trigger_type"], "task_mode": job["request_context"].get("task_mode"), "created_at_ms": job["created_at_ms"],
            "provider_attempt_count": value.provider_attempt_count(job), "error_class": job.get("error_class")} for job in jobs]
        with sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True) as observer:
            observer.row_factory = sqlite3.Row
            ledger = [dict(row) for row in observer.execute(
                "SELECT id,purpose,model,prompt_tokens,completion_tokens,total_tokens,timestamp_ms "
                "FROM llm_usage_ledger WHERE session_id=? ORDER BY timestamp_ms", (mid,))]
        report.update(model_call_accounting(jobs, ledger))
        report["observed_host_job_attempts"] = sum(int(job.get("attempts") or 0) for job in jobs)
        report["observed_host_provider_attempts"] = (sum(value.provider_attempt_count(job) for job in jobs)
            if all(job.get("output") or not int(job.get("attempts") or 0) for job in jobs) else None)
        report["same_basis_observation_completed"] = report["stop_reason"] == completed_reason
        state = report.get("state") or {}
        outcome = report.get("automatic") or {}
        report["publication_and_history"] = publication_and_history_review(report.get("initial_automatic_outcome"), snapshot, state.get("discussion"))
        publication = report["publication_and_history"]
        report["automation_contract_passed"] = bool(report["stop_reason"] == completed_reason
            and len(states) == 1 and len(autos) == 1
            and state.get("status") == "succeeded" and (state.get("discussion") or {}).get("context", {}).get("current_solution")
            and outcome.get("valid_intervention") and publication["initial_current_card_for_request"]
            and publication["typed_history_retained"] and publication["same_topic_and_basis_retained"] and outcome.get("fact_checks")
            and all(check["verbatim"] for check in outcome["fact_checks"]))
        report["manual_value_review"] = "pending"
    finally:
        # A scope stop revokes leases so a timed-out/excess task cannot publish
        # later. Canonical originals are retained. No paid postprocessing runs.
        with store._write_transaction():
            store._conn.execute("UPDATE meetings SET state='ended',ended_at_ms=? WHERE id=?", (time.time_ns() // 1_000_000, mid))
            store._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_global_scope_closed',lease_owner=NULL,lease_until_ms=NULL WHERE meeting_id=? AND status IN ('pending','running','retry_wait')", (mid,))
        live.close()
        store.close()
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/global-timer-plan.json")
    parser.add_argument("--same-basis-seconds", type=int, choices=range(30, 121), default=35,
        metavar="30..120", help="Live duplicate observation after two actual successful jobs; failures close immediately.")
    args = parser.parse_args(argv)
    destination = (value.ROOT / args.report).resolve()
    if not destination.is_relative_to((value.ROOT / "artifacts").resolve()):
        raise ValueError("Report must remain in this checkout's artifacts")
    report = {"schema": "talktrace.global_timer_acceptance.v1", "executed": args.execute,
        "same_basis_observation_seconds": args.same_basis_seconds,
        "budget": {"state_host_jobs": 1, "automatic_host_jobs": 1, "manual_host_jobs": 0, "retries": 0},
        "scope": "one isolated synthetic canonical final with official mock-labelled mirror; no audio devices",
        "timer_gates_overridden": False, "global_timer_directly_invoked_by_tool": False,
        "expected_service_flags": {"MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED": "1", "MEETING_COPILOT_DISCUSSION_STATE_ENABLED": "1"},
        "limitations": ["legacy seed derivatives cancelled atomically, not proof formal ASR producer is consolidated",
            "existing subsequent 60-second state cooldown and stability/input gates unchanged",
            f"{args.same_basis_seconds}-second duplicate observation runs only after both jobs succeed; failure closes immediately and is not a real failed-packet dedupe proof",
            "current card is sampled at actual publication; later TTL expiry is compatible only with preserved typed history and unchanged topic/basis",
            "no forced admission, no model state fixture, no project library change", "scope cleanup suppresses paid postprocessing"]}
    try:
        if args.execute:
            execute(report)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
    finally:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"executed": args.execute, "report": str(destination), "stop_reason": report.get("stop_reason"), "failure": report.get("failure")}, ensure_ascii=False))
    return int(bool(report.get("failure")) or bool(args.execute and not report.get("automation_contract_passed")))


if __name__ == "__main__":
    raise SystemExit(main())

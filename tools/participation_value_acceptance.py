"""Device-free synthetic value acceptance; dry-run unless --execute is supplied.

Uses real HTTP / durable execution / configured models when authorized. It never
applies the fixture's semantic responses and never opens an audio device.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core")]
RUNTIME = ROOT / "artifacts/realtime-participation-20261007/runtime"
FIXTURE = ROOT / "code/web_mvp/backend/tests/fixtures/realtime_participation_value_scenarios.json"
TERMINALS = {"succeeded", "failed", "cancelled"}
CARD_FIELDS = ("decision_id", "job_id", "status", "status_reason", "origin", "runtime_used", "pi_provider_attempted",
    "analysis_kind", "proposal_type", "coach_profile", "topic_id", "analysis_basis_revision", "say_this", "recommendation",
    "why_now", "reason", "supporting_facts", "assumptions", "source_evidence", "evidence_segment_ids", "evidence_quote",
    "lifecycle_action", "supersedes_decision_id", "superseded_by", "delivery_status", "material_context")


def provider_attempt_count(job):
    """Count actual attempts without mistaking the semantic-only zero for Pi."""
    output = job.get("output") or {}
    availability = output.get("provider_availability") or (output.get("formal_event_context") or {}).get("provider_availability") or {}
    decision = (output.get("applied") or {}).get("coach_decision") or {}
    metrics = (output.get("coach") or {}).get("agent_metrics") or decision.get("agent_metrics") or {}
    # These overlap in current outputs; taking the maximum avoids double-counting
    # the same provider attempt and retains the host's aggregate if nonzero.
    return max(int(value or 0) for value in (output.get("provider_attempt_count"),
        availability.get("provider_attempt_count"), metrics.get("provider_attempt_count")))


def open_synthetic_store(database_path):
    """Atomic device-free seed support shared by the isolated acceptance tools."""
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence

    class SyntheticStore(V2Persistence):
        @contextmanager
        def _write_transaction(self):
            with self._lock:
                if self._conn.in_transaction:
                    yield
                else:
                    with super()._write_transaction():
                        yield

    return SyntheticStore(database_path, semantic_projection_mode="llm_first")


def create_synthetic_live_record(repository, meeting_id):
    return repository.create({"session_id": meeting_id, "source": "synthetic_text_fixture",
        "trace_kind": "synthetic_acceptance", "provider": "synthetic_text_input",
        "provider_mode": "synthetic", "is_mock": True, "input_source": "synthetic_text_fixture", "events": []})


def compact_card(value):
    return {key: value[key] for key in CARD_FIELDS if key in value} if isinstance(value, dict) else None


def mirror_synthetic_final(repository, meeting_id, turn, seq):
    """Use the existing live-record repository adapter; disclose no ASR ran."""
    event = {"event_type": "final", "segment_id": turn["segment_id"], "text": turn["text"],
        "normalized_text": turn["text"], "start_ms": turn.get("started_at_ms", seq * 10_000),
        "end_ms": turn.get("ended_at_ms", seq * 10_000 + 8_000),
        "synthetic_fixture": True}
    repository.update(meeting_id, lambda record: {**record, "events": [
        *[item for item in record.get("events") or [] if item.get("segment_id") != turn["segment_id"]], event]})


def seed_synthetic_final(store, repository, meeting_id, turn, seq):
    """Publish source and cancel incidental model lanes in the same transaction."""
    from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash
    source_hash = transcript_evidence_hash(turn["segment_id"], turn["text"])
    with store._write_transaction():
        committed = store.commit_final_and_enqueue(meeting_id=meeting_id, final_id=f"{meeting_id}:f{seq}",
            segment_id=turn["segment_id"], text=turn["text"], normalized_text=turn["text"],
            started_at_ms=turn.get("started_at_ms", seq * 10_000),
            ended_at_ms=turn.get("ended_at_ms", seq * 10_000 + 8_000),
            evidence_hash=source_hash, now_ms=time.time_ns() // 1_000_000)
        ids = [value for value in committed["job_ids"].values() if value]
        if ids:
            store._conn.execute("UPDATE jobs SET status='cancelled',error_class='synthetic_harness_other_lanes_disabled',"
                "lease_owner=NULL,lease_until_ms=NULL WHERE id IN (" + ",".join("?" for _ in ids) + ")", ids)
    mirror_synthetic_final(repository, meeting_id, turn, seq)
    return {"cancelled_job_ids": ids, "source_hash": source_hash, "live_record_mirrored": True}


def own_job_outcome(job, snapshot):
    """Classify this request from its durable output, never from a previous card."""
    output = job.get("output") or {}
    applied = output.get("applied") or {}
    own_decision = applied.get("coach_decision") or {}
    coach = output.get("coach") or {}
    metrics = coach.get("agent_metrics") or own_decision.get("agent_metrics") or {}
    status = own_decision.get("status") or coach.get("status")
    attempts = provider_attempt_count(job)
    called = bool(own_decision.get("pi_provider_attempted") or coach.get("pi_provider_attempted") or metrics.get("provider_attempted"))
    own_intervention = applied.get("coach_intervention")
    current_card = snapshot.get("coach_intervention") or {}
    current_decision = snapshot.get("coach_decision") or {}
    identity_matches = current_card.get("job_id") == job["id"] and current_card.get("decision_id") == own_decision.get("decision_id")
    valid_intervention = bool(job["status"] == "succeeded" and status == "intervention" and own_intervention and called and attempts > 0)
    validated_silence = bool(job["status"] == "succeeded" and status == "protected_silent" and called and attempts > 0
        and not own_intervention and not metrics.get("response_validation_error") and not coach.get("fallback_error_code"))
    return {"job_id": job["id"], "durable_status": job["status"], "error_class": job.get("error_class"),
        "own_decision_status": status, "own_decision": compact_card(own_decision), "own_intervention": compact_card(own_intervention),
        "provider_attempt_count": attempts, "provider_called": called, "usage": output.get("usage") or metrics.get("usage"),
        "actual_agent_turns": metrics.get("turns"), "actual_agent_tool_names": metrics.get("tool_names"),
        "actual_tool_errors": metrics.get("tool_errors"), "provider_availability_scope": metrics.get("provider_access_scope"),
        "validation_error": metrics.get("response_validation_error"), "fallback_error_code": coach.get("fallback_error_code"),
        "validation_claim_fields": metrics.get("response_validation_claim_fields"),
        "validation_claim_fields_available": "response_validation_claim_fields" in metrics,
        "valid_intervention": valid_intervention, "validated_silence": validated_silence,
        "current_card_for_request": identity_matches, "current_decision_for_request": current_decision.get("job_id") == job["id"],
        "current_card": compact_card(current_card), "current_decision": compact_card(current_decision)}


def verify_facts(intervention, texts):
    if not intervention:
        return []
    sources = dict(texts)
    sources.update({source["source_id"]: source["quote"] for source in intervention.get("source_evidence") or []
                    if source.get("source_id") and source.get("quote")})
    return [{"text": fact.get("text"), "source_ref": fact.get("source_ref"),
             "known_source": fact.get("source_ref") in sources,
             "verbatim": bool(fact.get("text")) and fact["text"] in sources.get(fact.get("source_ref"), "")}
            for fact in intervention.get("supporting_facts") or []]


def automated_review(entry):
    failures = []
    if len(entry["state_jobs"]) != 1 or entry.get("state_failure_preserved"):
        failures.append("missing_or_failed_actual_state_model")
    elif entry["state_jobs"]:
        context = (entry["state_jobs"][0].get("discussion") or {}).get("context") or {}
        if entry["case_id"] in {"requirement_business_boundary", "architecture_tradeoff_with_material", "answered_point_progression"} and not context.get("current_solution"):
            failures.append("candidate_solution_not_persisted_by_actual_state_model")
        if entry["case_id"] == "answered_point_progression" and not context.get("responded_points"):
            failures.append("answered_point_not_persisted_by_actual_state_model")
    for request in entry["requests"]:
        if request["valid_intervention"] and not request["current_card_for_request"]:
            failures.append("own_intervention_not_current_snapshot")
        if any(not check["verbatim"] for check in request["fact_checks"]):
            failures.append("supporting_fact_not_verbatim")
        if request["valid_intervention"]:
            if not request["fact_checks"]:
                failures.append("professional_proposal_has_no_verified_fact")
            if (request["own_intervention"] or {}).get("analysis_kind") != "participation":
                failures.append("typed_contribution_missing")
    if entry["case_id"] == "topic_switch_and_silence":
        context = ((entry["state_jobs"][0].get("discussion") or {}).get("context") or {}) if entry["state_jobs"] else {}
        if context.get("open_points"):
            failures.append("closed_topic_open_points_carried_into_new_notice")
        if context.get("current_solution"):
            failures.append("confirmed_notice_projected_as_candidate_solution")
        if len(entry["requests"]) != 2 or not entry["requests"][0]["valid_intervention"]:
            failures.append("initial_refund_contribution_missing")
        if len(entry["requests"]) != 2 or not entry["requests"][-1]["validated_silence"]:
            failures.append("actual_validated_silence_missing")
        if entry["requests"] and (not entry["requests"][-1].get("topic_changed") or entry["requests"][-1].get("previous_card_is_current")):
            failures.append("old_topic_not_retracted_after_switch")
    elif len(entry["requests"]) != 1 or not entry["requests"][0]["valid_intervention"]:
        failures.append("own_useful_contribution_missing")
    if entry["case_id"] == "architecture_tradeoff_with_material" and entry["requests"]:
        intervention = entry["requests"][0]["own_intervention"] or {}
        attachment_refs = {source["source_id"] for source in intervention.get("source_evidence") or [] if source.get("source_type") == "meeting_attachment"}
        if not attachment_refs:
            failures.append("attachment_not_referenced")
        elif not any(check["verbatim"] and check["source_ref"] in attachment_refs for check in entry["requests"][0]["fact_checks"]):
            failures.append("referenced_attachment_fact_not_visible_in_supporting_facts")
    if any(job["id"] in entry["seeded_other_jobs"] and job["provider_attempt_count"] > 0 for job in entry["all_jobs"]):
        failures.append("unbudgeted_seed_lane_provider_call")
    return {"hard_failures": list(dict.fromkeys(failures)), "automation_contract_passed": not failures,
            "value_verdict": "manual_review_pending", "note": "Schema/identity success cannot determine usefulness; score the actual words separately."}


def execute(cases, report, base_url, *, keep_fixtures_live=False):
    from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
    store = open_synthetic_store(RUNTIME / "meeting_copilot.db")
    live_repository = SqliteAsrLiveSessionRepository(RUNTIME)
    client = httpx.Client(base_url=base_url, trust_env=False, timeout=40)
    def http(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    def wait_job(job_id):
        started = time.monotonic()
        while time.monotonic() - started < 45:
            job = store.get_job(job_id)
            if job["status"] in TERMINALS:
                return job
            time.sleep(0.25)
        raise TimeoutError(f"durable job did not finish within 45 seconds: {job_id}")
    def materials(case):
        if not case["materials"]:
            return {}
        material_set = http("POST", "/v2/material-sets", json={})["material_set"]
        for material in case["materials"]:
            upload = http("POST", f"/v2/material-sets/{material_set['id']}/attachments",
                files={"file": (material["filename"], material["text"].encode("utf-8"), "text/markdown")},
                data={"title": material["title"], "purpose": material["purpose"]})
            attachment_id = upload["attachment"]["id"]
            for _ in range(80):
                detail = http("GET", f"/v2/material-sets/{material_set['id']}/attachments/{attachment_id}")
                if detail.get("version"):
                    break
                if (detail.get("job") or {}).get("state") in {"failed", "cancelled"}:
                    raise RuntimeError("synthetic material conversion failed")
                time.sleep(0.25)
            if not detail.get("version"):
                raise TimeoutError("synthetic material preview was not produced")
            http("PUT", f"/v2/material-sets/{material_set['id']}/attachments/{attachment_id}/usage", json={
                "version_id": detail["version"]["id"], "enabled": True, "selected_span_ids": None,
                "expected_revision": detail["attachment"]["revision"]})
        fresh = http("GET", f"/v2/material-sets/{material_set['id']}")["material_set"]
        return {"material_set_id": fresh["id"], "expected_material_set_revision": fresh["revision"]}
    def seed_turn(meeting_id, turn, seq):
        return seed_synthetic_final(store, live_repository, meeting_id, turn, seq)["cancelled_job_ids"]
    try:
        for case in cases:
            entry = {"case_id": case["id"], "title": case["title"], "expected": case["expected"],
                     "requests": [], "state_jobs": [], "seeded_other_jobs": [], "manual_review": {
                         "score_0_to_2": {key: None for key in ("increment", "grounding", "sayability", "context_progression", "timeliness")},
                         "has_value": None, "reason": None, "material_changed_advice": None}}
            report["cases"].append(entry)
            meeting_id = f"participation_value_{uuid.uuid4().hex[:12]}"
            entry["meeting_id"] = meeting_id
            http("POST", "/v2/meetings", json={"meeting_id": meeting_id, "title": f"合成价值验收：{case['title']}", **materials(case)},
                 headers={"Idempotency-Key": meeting_id})
            print(json.dumps({"acceptance_scope_created": meeting_id, "case_id": case["id"], "keep_fixtures_live": keep_fixtures_live}, ensure_ascii=False), flush=True)
            # Prove the HTTP service and harness refer to the same isolated DB.
            assert store.get_meeting(meeting_id)["id"] == meeting_id
            preparation = http("GET", f"/v2/meetings/{meeting_id}/preparation")
            preparation = http("PUT", f"/v2/meetings/{meeting_id}/preparation", json={**preparation, "live_analysis_profile": "participation"})
            assert preparation["live_analysis_profile"] == "participation"
            entry["live_analysis_profile"] = preparation["live_analysis_profile"]
            entry["preparation_version"] = preparation["version"]
            create_synthetic_live_record(live_repository, meeting_id)
            texts = {}
            old_snapshot = None
            try:
                for seq, turn in enumerate(case["turns"], start=1):
                    texts[turn["segment_id"]] = turn["text"]
                    entry["seeded_other_jobs"].extend(seed_turn(meeting_id, turn, seq))
                    if "coach_request" not in turn:
                        continue
                    # Exactly one real state call per case. The switch scene
                    # uses it AFTER the second topic, not a fake semantic seed.
                    state_here = case["id"] != "topic_switch_and_silence" or seq == len(case["turns"])
                    if state_here and not entry["state_jobs"]:
                        state_job = store.enqueue_discussion_state(meeting_id=meeting_id, now_ms=time.time_ns() // 1_000_000,
                            cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=1)
                        if state_job is None:
                            raise RuntimeError("state-only job was not admitted")
                        finished = wait_job(state_job["id"])
                        state_output = finished.get("output") or {}
                        entry["state_jobs"].append({"job_id": state_job["id"], "status": finished["status"], "error_class": finished.get("error_class"),
                            "provider_attempt_count": state_output.get("provider_attempt_count", 0), "usage": state_output.get("usage"),
                            "repair_attempted": state_output.get("repair_attempted"), "discussion": store.get_current_discussion(meeting_id),
                            "applied": state_output.get("applied"), "state_interpretation": state_output.get("state_interpretation"),
                            "operation_diagnostic_available": "state_interpretation" in state_output})
                        if finished["status"] != "succeeded" or state_output.get("provider_attempt_count") != 1:
                            entry["state_failure_preserved"] = True
                    started = time.monotonic()
                    queued = http("POST", f"/v2/meetings/{meeting_id}/coach/request", json=turn["coach_request"],
                        headers={"Idempotency-Key": f"{meeting_id}:intent:{seq}"})
                    job = wait_job(queued["job"]["id"])
                    snapshot = http("GET", f"/v2/meetings/{meeting_id}/snapshot")
                    outcome = own_job_outcome(job, snapshot)
                    outcome.update(intent=turn["coach_request"]["intent"], elapsed_ms=round((time.monotonic() - started) * 1_000, 1),
                        discussion=snapshot.get("discussion"), fact_checks=verify_facts(outcome["own_intervention"], texts))
                    if old_snapshot is not None:
                        old_card = old_snapshot.get("coach_intervention") or {}
                        outcome["previous_card_is_current"] = bool(old_card.get("decision_id") and old_card.get("decision_id") == (snapshot.get("coach_intervention") or {}).get("decision_id"))
                        outcome["topic_changed"] = (old_snapshot.get("discussion") or {}).get("work_item_id") != (snapshot.get("discussion") or {}).get("work_item_id")
                        outcome["old_topic_history"] = [compact_card(card) for card in snapshot.get("coach_history") or [] if card.get("decision_id") == old_card.get("decision_id")]
                    entry["requests"].append(outcome)
                    old_snapshot = snapshot
                    print(json.dumps({"case_id": case["id"], "intent": outcome["intent"], "job_id": job["id"],
                        "status": job["status"], "own_decision_status": outcome["own_decision_status"], "elapsed_ms": outcome["elapsed_ms"]}, ensure_ascii=False), flush=True)
            except Exception as exc:
                entry["failure"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
            finally:
                # Optional live scope is for read-only UI inspection; no device capture starts.
                # Default cleanup bypasses unrelated paid minutes for synthetic inputs.
                if not keep_fixtures_live:
                    with store._write_transaction():
                        store._conn.execute("UPDATE meetings SET state='ended',ended_at_ms=? WHERE id=?", (time.time_ns() // 1_000_000, meeting_id))
                entry["fixture_scope_state"] = "kept_live_for_read_only_ui" if keep_fixtures_live else "ended_without_postprocessing"
                entry["all_jobs"] = [{"id": job["id"], "kind": job["kind"], "status": job["status"], "trigger_type": job.get("trigger_type"),
                    "task_mode": job["request_context"].get("task_mode"), "provider_attempt_count": provider_attempt_count(job)}
                    for job in store.list_jobs(meeting_id=meeting_id)]
                entry["observed_provider_attempts"] = sum(job["provider_attempt_count"] for job in entry["all_jobs"])
                entry["automated_review"] = automated_review(entry)
    finally:
        live_repository.close()
        store.close()
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Explicitly perform the bounded real model acceptance; otherwise produce plan only")
    parser.add_argument("--base-url", default="http://127.0.0.1:8768")
    parser.add_argument("--case-id", action="append")
    parser.add_argument("--report", default="artifacts/realtime-participation-20261007/value-acceptance-plan.json")
    parser.add_argument("--keep-fixtures-live", action="store_true", help="Keep synthetic scopes live for read-only UI inspection; never starts capture")
    args = parser.parse_args(argv)
    if args.base_url not in {"http://127.0.0.1:8768", "http://localhost:8768"}:
        raise ValueError("Only the isolated acceptance service at port 8768 is allowed")
    destination = (ROOT / args.report).resolve()
    if not destination.is_relative_to((ROOT / "artifacts").resolve()):
        raise ValueError("Report must remain inside this source checkout's artifacts directory")
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    cases = [case for case in fixture["cases"] if not args.case_id or case["id"] in args.case_id]
    if not cases or args.case_id and set(args.case_id) - {case["id"] for case in cases}:
        raise ValueError("Unknown or empty case selection")
    report = {"schema": "talktrace.participation_value_acceptance.v1", "executed": args.execute,
        "scope": "synthetic original text/materials; actual backend models only with --execute; no microphone/speaker/ASR",
        "semantic_fixture_applied": False, "started_at_ms": time.time_ns() // 1_000_000, "cases": [],
        "keep_fixtures_live": args.keep_fixtures_live,
        "budget": {"manual_request_limit": sum("coach_request" in turn for case in cases for turn in case["turns"]), "state_call_limit": len(cases),
                   "background_delta_correction": "cancelled atomically with synthetic transcript seed", "silent_filling_retries": 0},
        "harness_overrides": ["state admission cooldown/stability/minimum input gates explicitly overridden for synthetic scope",
            "fixture audio timestamps are synthetic positions, not elapsed meeting duration",
            ("synthetic scopes kept live for read-only UI inspection; explicit isolated cleanup is required" if args.keep_fixtures_live else
             "synthetic meetings ended directly in isolated DB to avoid paid minutes"), "no semantic response or professional card is seeded",
            "canonical V2 source hashes and existing SQLite live-record adapter are synchronized; records disclose synthetic text, no ASR"],
        "plan": [{"case_id": case["id"], "state_at": "after_last_turn", "manual_intents": [turn["coach_request"]["intent"] for turn in case["turns"] if "coach_request" in turn],
                  "materials": [material["title"] for material in case["materials"]], "expected": case["expected"]} for case in cases]}
    try:
        if args.execute:
            if not (RUNTIME / "meeting_copilot.db").is_file():
                raise ValueError("Named isolated acceptance database does not exist")
            execute(cases, report, args.base_url, keep_fixtures_live=args.keep_fixtures_live)
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:300]}
    finally:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"executed": args.execute, "report": str(destination), "budget": report["budget"], "failure": report.get("failure")}, ensure_ascii=False))
    return int(bool(report.get("failure")) or any(case.get("failure") or not case["automated_review"]["automation_contract_passed"] for case in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(main())

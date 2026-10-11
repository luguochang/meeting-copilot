"""Dry-run by default: one natural state and two real Pi requests, no audio.

This isolated 8771/8791 profile owns a fresh five-POST ledger. It copies only
public input literals and support functions, never old semantic/job results.
The root observer opens the page before each request and signals inspection
through local files. Those files are not DOM receipts or model instructions.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid


_SUPPORT_PATH = Path(__file__).with_name("participation_continuous_acceptance.py")
_SPEC = importlib.util.spec_from_file_location("_quote_postcheck_continuous_support", _SUPPORT_PATH)
support = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = support
_SPEC.loader.exec_module(support)

PROFILE = "participation_quote_postcheck.v1"
DEFAULT_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/quote-postcheck-r01"
support.LIMITS = {"discussion_state": 1, "automatic": 0, "manual": 2}
support.HOST_LIMIT, support.UPSTREAM_LIMIT = 3, 5
support.SCOPE_PREFIX = "participation_quote_postcheck_"
support.BOUNDARY_SCHEMA = "participation_quote_postcheck_boundary.v1"
support.STOP_CODES |= {"ui_ready_timeout", "initial_state_incomplete", "quote_postcheck_contract_failed"}

PUBLIC_PAYMENT_TEXT = support.INPUTS[1]
SIGNAL_FIELDS = frozenset({"meeting_id", "stage", "signal", "nonce"})
STAGES = frozenset({"manual1", "manual2"})
TOOL_NAMES = frozenset({"submit_intervention", "keep_silent", "read_realtime_context", "search_prior_evidence", "read_transcript_span",
    "search_project_documents", "read_project_state"})
TOOL_ERROR_CODES = frozenset({"unknown_evidence", "evidence_quote_not_verbatim", "output_language_mismatch",
    "recommendation_restates_evidence", "evidence_revision_changed", "ungrounded_discovery", "cross_topic_deadline",
    "invalid_intervention_fields", "prior_evidence_required", "multiple_terminal_actions", "checklist_not_reviewed",
    "unsupported_intervention_enum", "tool_schema_validation", "project_source_mismatch", "project_revision_mismatch",
    "project_assignment_unquoted", "host_proposal_mismatch", "project_state_mismatch", "document_quote_mismatch",
    "sentence_alias_mismatch", "reason_alias_mismatch", "missing_intervention_reason", "duplicate_evidence_ids",
    "insufficient_clarity_fragments", "tool_execution_error"})
QUOTE_REPAIR_CODES = frozenset({"document_quote_mismatch", "evidence_quote_not_verbatim"})
CARD_FIELDS = frozenset((*support.value.CARD_FIELDS, "title", "question", "valid_until_ms", "created_at_ms",
    "confidence", "ui_render_receipt"))


class QuoteAttemptBudget(support.proxy.AttemptBudget):
    def __post_init__(self):
        if type(self.limit) is not int or self.limit != 5:
            raise ValueError("quote postcheck requires exactly five upstream attempts")


support.ContinuousAttemptBudget = QuoteAttemptBudget
_regular_preflight = support.preflight
_regular_persistence_type = support.guarded_persistence_type


def guarded_persistence_type(base):
    bounded = _regular_persistence_type(base)

    class QuotePersistence(bounded):
        def enqueue_user_coach_request(self, **kwargs):
            if kwargs.get("intent") not in {"ask_question", "formulate_response"}:
                raise RuntimeError("quote postcheck requires its explicit participation intent")
            return super().enqueue_user_coach_request(**kwargs)
    return QuotePersistence


support.guarded_persistence_type = guarded_persistence_type


def preflight(run_dir, client):
    result = _regular_preflight(run_dir, client)
    identity = result["identity"]
    if identity.get("automatic_enabled") is not False or identity.get("discussion_state_enabled") is not True:
        raise support.AcceptanceStop("preflight_boundary_mismatch", "quote postcheck profile mismatch",
            checks={"app_identity": False})
    return result


support.preflight = preflight


def plan(run_dir: Path) -> dict:
    return {"schema_version": "participation_quote_postcheck_acceptance.v1", "profile": PROFILE,
        "executed": False, "run_dir": str(run_dir), "runtime": str(run_dir / "runtime"),
        "app_url": f"http://127.0.0.1:{support.APP_PORT}", "proxy_url": f"http://127.0.0.1:{support.PROXY_PORT}",
        "budget": {"host_jobs": 3, "state": 1, "automatic": 0, "manual": 2,
            "forwarded_upstream_posts": 5, "attempts_per_host_job": 1, "retries": 0},
        "runtime_flags": {"discussion_automatic_enabled": False, "discussion_state_enabled": True,
            "preparation_policy": "off", "refiner_policy": "on_demand", "real_pi_transport": True},
        "instrumented_test_wrapper": {"atomic_admission_ceiling": True, "manual_max_attempts": 1,
            "activation_is_failure": True, "production_manual_retry_policy_covered": False},
        "timer_gates_overridden": False, "direct_state_or_automatic_admission": False,
        "semantic_fixture_applied": False, "clock_advanced": False, "ttl_shortened": False,
        "audio_capture": False, "audio_asr": False, "websocket_input_disabled": True,
        "production_ports_untouched": [8767, 8768], "old_proxy_ledger_untouched": True,
        "application_schema_version": support.DB_SCHEMA_VERSION,
        "input": "Public payment-timeout text and short v1/v2 material literals from continuous R02; no semantic outputs copied",
        "stages": [{"id": "natural_state", "wait": "normal first-state timer, material v1 already enabled"},
            {"id": "manual1", "intent": "ask_question", "page_ready_seconds": 15, "inspection_max_seconds": 20},
            {"id": "material_v2", "operations": "official save then explicit enable, no fixed pause or second-state request"},
            {"id": "manual2", "intent": "formulate_response", "page_ready_seconds": 15, "inspection_max_seconds": 20}],
        "inspection_seconds": 0,
        "ui_signal_contract": {"storage": "run_dir/ui-signals/{stage}.{page_ready|inspected}.json",
            "payload_fields": sorted(SIGNAL_FIELDS), "poll_seconds": .2,
            "page_ready_timeout": "stop before dispatch", "inspection_timeout": "record observation unknown and continue",
            "early_finish": "root writes matching stage signal after actual observation",
            "signals_are_not_dom_ack": True},
        "evaluation_limits": "Two Pi requests only; no full continuous re-run, ASR proof or latency percentile.",
        "value_verdict": "manual_review_pending", "proxy_credentials": "environment only; never in report or arguments"}


def operation_allowed(method: str, path: str) -> bool:
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if method == "POST" and path == "/v2/meetings":
        return True
    if path.startswith("/v2/meetings/" + support.SCOPE_PREFIX):
        return (method == "PUT" and path.endswith("/preparation")
            or method == "POST" and path.endswith(("/material-set", "/coach/request")))
    if path.startswith("/v2/material-sets"):
        return (method == "POST" and (path == "/v2/material-sets" or path.endswith(("/attachments", "/versions")))
            or method == "PUT" and path.endswith("/usage"))
    return method == "POST" and path.startswith("/v2/traces/") and path.endswith("/ui-rendered")


def build_app(run_dir: Path):
    application = support.build_app(run_dir, automatic_enabled=False)
    scope_lock = asyncio.Lock()
    selected_scope = None

    @application.middleware("http")
    async def quote_boundary(request, call_next):
        nonlocal selected_scope
        from starlette.responses import JSONResponse
        denied = JSONResponse({"detail": {"code": "quote_postcheck_operation_disabled"}}, status_code=403)
        path = request.url.path
        if not operation_allowed(request.method, path):
            return denied
        if request.method == "POST" and path == "/v2/meetings":
            try:
                body = await request.json()
            except (ValueError, UnicodeError):
                return denied
            mid = body.get("meeting_id") if isinstance(body, dict) else None
            if not isinstance(mid, str) or not mid.startswith(support.SCOPE_PREFIX):
                return denied
            async with scope_lock:
                if selected_scope is not None and selected_scope != mid:
                    return denied
                response = await call_next(request)
                if response.status_code < 400:
                    selected_scope = mid
                return response
        if request.method not in {"GET", "HEAD", "OPTIONS"} and path.startswith("/v2/meetings/"):
            if path.split("/")[3] != selected_scope:
                return denied
        if request.method == "POST" and path.endswith("/coach/request"):
            try:
                body = await request.json()
            except (ValueError, UnicodeError):
                return denied
            # Without intent the old endpoint can create an unclassified legacy
            # job, outside the participation lane ceiling. Reject before enqueue.
            if not isinstance(body, dict) or body.get("intent") not in {"ask_question", "formulate_response"}:
                return denied
        if request.method == "POST" and path.startswith("/v2/traces/"):
            try:
                body = await request.json()
            except (ValueError, UnicodeError):
                return denied
            if not isinstance(body, dict) or body.get("meeting_id") != selected_scope:
                return denied
        if selected_scope is None and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse({"detail": {"code": "quote_postcheck_operation_disabled"}}, status_code=403)
        return await call_next(request)
    return application


def serve_app(run_dir):
    import uvicorn
    application = build_app(run_dir)
    print(json.dumps({"status": "starting", "profile": PROFILE, "port": support.APP_PORT,
        "pid": os.getpid(), "data_dir": str(run_dir / "runtime"), "host_limit": 3}), flush=True)
    uvicorn.run(application, host="127.0.0.1", port=support.APP_PORT, log_level="warning", access_log=False)


def signal_state(path: Path, expected: dict) -> str:
    try:
        if path.stat().st_size > 1024:
            return "invalid"
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError):
        return "invalid"
    return "matched" if isinstance(payload, dict) and set(payload) == SIGNAL_FIELDS and payload == expected else "invalid"


def wait_local_signal(path: Path, expected: dict, *, max_seconds: float, tick,
                      monotonic=time.monotonic, sleep=time.sleep) -> dict:
    start = monotonic()
    invalid_seen = False
    while True:
        tick()  # A budget/timer failure wins even if an inspection signal exists.
        state = signal_state(path, expected)
        invalid_seen |= state == "invalid"
        if state == "matched":
            return {"matched": True, "elapsed_ms": round((monotonic() - start) * 1000), "invalid_signal_seen": invalid_seen}
        remaining = max_seconds - (monotonic() - start)
        if remaining <= 0:
            return {"matched": False, "elapsed_ms": round((monotonic() - start) * 1000), "invalid_signal_seen": invalid_seen}
        sleep(min(.2, remaining))


def compact_card(card):
    return {key: value for key, value in (card or {}).items() if key in CARD_FIELDS}


def tool_diagnostics(job: dict) -> dict:
    output = job.get("output") or {}
    metrics = (output.get("coach") or {}).get("agent_metrics") or ((output.get("applied") or {}).get("coach_decision") or {}).get("agent_metrics") or {}
    errors = [{"tool": item.get("tool") if item.get("tool") in TOOL_NAMES else "unclassified",
        "code": item.get("code") if item.get("code") in TOOL_ERROR_CODES else "unclassified"}
        for item in metrics.get("tool_errors") or [] if isinstance(item, dict)]
    return {"turns": metrics.get("turns") if type(metrics.get("turns")) is int else None,
        "tool_calls": metrics.get("tool_calls") if type(metrics.get("tool_calls")) is int else None,
        "tool_names": [name if name in TOOL_NAMES else "unclassified" for name in metrics.get("tool_names") or []],
        "tool_errors": errors, "context_reads": metrics.get("context_reads") if type(metrics.get("context_reads")) is int else None,
        "history_searches": metrics.get("history_searches") if type(metrics.get("history_searches")) is int else None,
        "history_results": metrics.get("history_results") if type(metrics.get("history_results")) is int else None,
        "main_quote_repair_observed": any(item["code"] in QUOTE_REPAIR_CODES for item in errors),
        "tool_result_bodies": "not_saved; available metrics expose fixed names/error codes and validated terminal outcome only"}


def dom_receipt_observation(card: dict, traces: list[dict]) -> dict:
    from meeting_copilot_web_mvp.coach_ui_render_receipt import normalize_receipt
    receipt = normalize_receipt(card.get("ui_render_receipt"))
    trace = next((item for item in traces if item.get("job_id") == card.get("job_id")), {})
    mark = (trace.get("stages") or {}).get("ui_rendered") or {}
    marked_receipt = normalize_receipt(mark.get("attributes"))
    provenance = ((trace.get("execution") or {}).get("provenance") or {}).get("ui_render_ack") or {}
    attributes = provenance.get("attributes") or {}
    matched = bool(receipt and receipt["job_id"] == card.get("job_id") and receipt["decision_id"] == card.get("decision_id")
        and trace.get("meeting_id") == receipt["meeting_id"] and trace.get("job_id") == receipt["job_id"]
        and marked_receipt == receipt and provenance.get("status") == "observed"
        and type(mark.get("monotonic_ns")) is int and provenance.get("at_monotonic_ns") == mark["monotonic_ns"]
        and attributes.get("source") == "formal_coach_ui.v1" and attributes.get("event_seq") == receipt["event_seq"]
        and attributes.get("input_version") == receipt["input_version"])
    return {"publication_receipt": receipt, "actual_dom_receipt": marked_receipt if matched else None,
        "exact_six_field_dom_ack": True if matched else "unknown",
        "host_first_mark_monotonic_ns": mark.get("monotonic_ns") if matched else None,
        "observation_source": "GET trace; root signal alone cannot prove DOM acknowledgement"}


def request_checks(stage: str, job: dict, snapshot: dict, *, material_version: str,
                   material_text: str, public_texts: dict, observed_at_ms: int | None = None) -> tuple[dict, dict]:
    outcome = support.value.own_job_outcome(job, snapshot)
    card = snapshot.get("coach_intervention") or {}
    facts = support.value.verify_facts(outcome["own_intervention"], public_texts)
    attachment_sources = [source for source in card.get("source_evidence") or []
        if source.get("source_type") == "meeting_attachment" and source.get("version_id") == material_version]
    refs = {source.get("source_id") for source in attachment_sources}
    quoted_sources = bool(attachment_sources) and all(source.get("quote") and source["quote"] in material_text for source in attachment_sources)
    visible_attachment_fact = any(fact["source_ref"] in refs and fact["verbatim"] for fact in facts)
    receipt = dom_receipt_observation(card, [])['publication_receipt']
    metadata = job.get("request_context") or {}
    discussion = snapshot.get("discussion") or {}
    discussion_context = discussion.get("context") or {}
    now_ms = observed_at_ms if observed_at_ms is not None else time.time_ns() // 1_000_000
    expected_intent = "ask_question" if stage == "manual1" else "formulate_response"
    checks = {f"{stage}_own_current_formal_intervention": outcome["valid_intervention"] and outcome["current_card_for_request"]
            and outcome["current_decision_for_request"] and card.get("analysis_kind") == "participation",
        f"{stage}_expected_intent": (job.get("request_context") or {}).get("intent") == expected_intent,
        f"{stage}_current_topic_and_basis": bool(metadata.get("topic_id")
            and card.get("topic_id") == discussion.get("work_item_id") == metadata["topic_id"]
            and type(metadata.get("analysis_basis_revision")) is int
            and card.get("analysis_basis_revision") == discussion_context.get("analysis_basis_revision") == metadata["analysis_basis_revision"]),
        f"{stage}_inside_normal_current_ttl": type(card.get("valid_until_ms")) is int and now_ms < card["valid_until_ms"],
        f"{stage}_typed_facts_verbatim": bool(facts) and all(fact["known_source"] and fact["verbatim"] for fact in facts),
        f"{stage}_active_attachment_quoted_verbatim": quoted_sources,
        f"{stage}_no_stale_attachment_version": all(source.get("version_id") == material_version
            for source in card.get("source_evidence") or [] if source.get("source_type") == "meeting_attachment"),
        f"{stage}_referenced_attachment_fact_visible": visible_attachment_fact,
        f"{stage}_current_publication_receipt": bool(receipt and receipt["job_id"] == job["id"]
            and receipt["meeting_id"] == job.get("meeting_id") and receipt["decision_id"] == card.get("decision_id")
            and receipt["input_version"] == job.get("input_version"))}
    if stage == "manual2":
        checks["manual2_visible_fact_unique_to_v2"] = any(fact["source_ref"] in refs and fact["verbatim"]
            and fact.get("text") in support.MATERIAL_V2 and fact["text"] not in support.MATERIAL_V1
            and not fact["text"].lstrip().startswith("#") for fact in facts)
    details = {"job_id": job["id"], "intent": (job.get("request_context") or {}).get("intent"),
        "own_outcome": outcome, "current_card": compact_card(card), "fact_checks": facts,
        "tool_diagnostics": tool_diagnostics(job), "material_context": card.get("material_context"),
        "quote_efficiency": {"no_main_quote_repair": not tool_diagnostics(job)["main_quote_repair_observed"]},
        "source_evidence": card.get("source_evidence"), "actual_usage": outcome["usage"],
        "provider_attempt_count": outcome["provider_attempt_count"], "dispatch_or_cancel_usage": "known_output" if job.get("output") else "unknown_not_zero"}
    return checks, details


class QuotePostcheckObserver(support.ContinuousObserver):
    def signal(self, stage: str, name: str, *, maximum: int) -> dict:
        if stage not in STAGES or name not in {"page_ready", "inspected"}:
            raise ValueError("invalid local inspection stage")
        expected = {"meeting_id": self.mid, "stage": stage, "signal": name, "nonce": uuid.uuid4().hex}
        path = self.run_dir / "ui-signals" / f"{stage}.{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.emit("inspection_signal_requested", inspection_stage=stage, signal=name,
            local_signal_path=str(path), expected_payload=expected, maximum_seconds=maximum,
            workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
        result = wait_local_signal(path, expected, max_seconds=maximum, tick=self.collect)
        result.update(stage=stage, signal=name, maximum_seconds=maximum, local_signal_path=str(path))
        self.report.setdefault("ui_signal_observations", []).append(result)
        self.emit("inspection_signal_completed", inspection_stage=stage, signal=name,
            matched=result["matched"], elapsed_ms=result["elapsed_ms"])
        if name == "page_ready" and not result["matched"]:
            raise support.AcceptanceStop("ui_ready_timeout", "root page readiness was not observed; no manual dispatch")
        return result

    def append_payment(self):
        segment_id = "quote-postcheck-public-1"
        self.public_texts[segment_id] = PUBLIC_PAYMENT_TEXT
        seed = support.value.seed_synthetic_final(self.store, self.live, self.mid,
            {"segment_id": segment_id, "text": PUBLIC_PAYMENT_TEXT}, 1)
        self.report["sources"] = [{"segment_id": segment_id, "text": PUBLIC_PAYMENT_TEXT,
            "seed": seed, "appended_at_ms": time.time_ns() // 1_000_000}]
        self.emit("public_payment_input_appended", segment_id=segment_id, characters=len(PUBLIC_PAYMENT_TEXT),
            semantic_fixture_applied=False)

    def inspect_publication(self, stage, job, initial):
        signal = self.signal(stage, "inspected", maximum=20)
        checkpoint = self.checkpoint(stage + "_inspection_finished", job=job)
        card = initial["snapshot"]["coach_intervention"]
        observation = dom_receipt_observation(card, checkpoint["traces"])
        observation.update(stage=stage, root_inspected_signal=signal["matched"],
            current_still_same_job=(checkpoint["snapshot"].get("coach_intervention") or {}).get("job_id") == job["id"],
            actual_observed_at_ms=checkpoint["observed_at_ms"])
        self.report.setdefault("dom_observations", []).append(observation)
        return observation

    def material_v2(self, previous_card):
        # Keep the official save/use/source checks; inherited inspection defaults
        # to zero for this profile, so no fixed wait consumes the state cooldown.
        super().material_v2(previous_card)

    def run(self):
        from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
        self.report["preflight"] = support.preflight(self.run_dir, self.client)
        self.store = support.value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
        self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
        self.mid = support.SCOPE_PREFIX + uuid.uuid4().hex[:12]
        self.report["meeting_id"] = self.mid
        try:
            self.http("POST", "/v2/meetings", json={"meeting_id": self.mid, "title": "真实Pi引文后验 · 公开合成资料 · 无音频"},
                headers={"Idempotency-Key": self.mid})
            prep = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
            self.report["preparation"] = self.http("PUT", f"/v2/meetings/{self.mid}/preparation", json={**prep,
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
            support.value.create_synthetic_live_record(self.live, self.mid)
            self.emit("scope_created", profile=PROFILE,
                workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
            self.material_v1()
            self.append_payment()
            state = self.wait_job("discussion_state", 1)
            initial_state = self.checkpoint("natural_first_state_ready", job=state)
            context = (initial_state.get("discussion") or {}).get("context") or {}
            if context.get("phase") != "discussing" or context.get("last_semantic_seq") != 1:
                raise support.AcceptanceStop("initial_state_incomplete", "first actual state was not applied; no paid compensation")
            self.signal("manual1", "page_ready", maximum=15)
            self.manual("ask_question", "请结合当前付款接口超时的讨论和本场需求资料，帮我提出一个有价值的具体问题，补齐还未确定的决策边界。")
            job1 = self.wait_job("manual", 1, seconds=45)
            first = self.checkpoint("manual1_current_published", job=job1)
            checks1, details1 = request_checks("manual1", job1, first["snapshot"],
                material_version=self.report["material"]["version1_id"], material_text=support.MATERIAL_V1,
                public_texts=self.public_texts, observed_at_ms=first["observed_at_ms"])
            self.report.setdefault("requests", []).append(details1)
            self.report.setdefault("quote_efficiency_checks", {})["manual1_no_main_quote_repair"] = details1["quote_efficiency"]["no_main_quote_repair"]
            self.report["contract_checks"] = checks1
            if not all(checks1.values()):
                raise support.AcceptanceStop("quote_postcheck_contract_failed", "first quote postcheck contract failed; no paid compensation")
            observation1 = self.inspect_publication("manual1", job1, first)
            self.material_v2(first["snapshot"]["coach_intervention"])
            self.signal("manual2", "page_ready", maximum=15)
            self.manual("formulate_response", "请基于当前议题和最新已启用的需求资料，帮我组织一句可以在会上提出的回应，说明新增约束对方案的影响，并指出尚待确认的问题。")
            job2 = self.wait_job("manual", 2, seconds=45)
            second = self.checkpoint("manual2_current_published", job=job2)
            checks2, details2 = request_checks("manual2", job2, second["snapshot"],
                material_version=self.report["material"]["version2_id"], material_text=support.MATERIAL_V2,
                public_texts=self.public_texts, observed_at_ms=second["observed_at_ms"])
            self.report["requests"].append(details2)
            self.report["quote_efficiency_checks"]["manual2_no_main_quote_repair"] = details2["quote_efficiency"]["no_main_quote_repair"]
            self.report["contract_checks"].update(checks2)
            if not all(checks2.values()):
                raise support.AcceptanceStop("quote_postcheck_contract_failed", "second quote postcheck contract failed; no paid compensation")
            observation2 = self.inspect_publication("manual2", job2, second)
            jobs, proxy_state = self.collect()
            counts = support.host_budget(jobs)
            material = self.report["material"]
            self.report["contract_checks"].update(
                one_natural_state_two_manual_no_automatic=counts["discussion_state"] == 1 and counts["manual"] == 2 and counts["automatic"] == 0,
                three_host_jobs_single_attempt=counts["host_jobs"] == counts["host_attempts"] == 3 and not counts["retried_jobs"],
                no_budget_guard_or_upstream_retry=not self.report["guard_activations"] and not proxy_state["budget_rejections"] and not proxy_state["upstream_retries"],
                actual_forwarded_calls_within_five=3 <= proxy_state["reserved_forward_attempts"] <= 5,
                save_v2_did_not_enable_it=material["save_only_active_v1_unchanged"],
                explicit_v2_use_retracted_old_current=material["old_current_retracted"],
                v2_usage_changed_before_old_normal_ttl=material["old_card_inside_ttl_at_usage_change"],
                old_typed_card_retained_in_history=material["old_typed_history_retained"],
                old_sources_rejected_current_readable_historically=bool(material["old_source_read_checks"])
                    and all(item["current_status"] == 409 and item["historical_status"] == 200 for item in material["old_source_read_checks"]))
            self.report["ui_checks"] = {"manual1_exact_dom_ack": observation1["exact_six_field_dom_ack"],
                "manual2_exact_dom_ack": observation2["exact_six_field_dom_ack"],
                "state_catchup_current_page": "root_observation_pending_not_inferred_from_signal"}
            self.report["contract_failures"] = [key for key, passed in self.report["contract_checks"].items() if not passed]
            self.report["automated_contract_passed"] = not self.report["contract_failures"]
            self.report["quote_efficiency_passed"] = all(self.report["quote_efficiency_checks"].values())
            self.report["stop_reason"] = "quote_postcheck_results_observed_value_review_pending"
        finally:
            self.finish()

    def finish(self):
        if self.store is not None and self.mid:
            self.report["contract_failures"] = [key for key, passed in self.report.get("contract_checks", {}).items() if not passed]
            # A partial run cannot be promoted to success by an empty check list.
            self.report.setdefault("automated_contract_passed", False)
        super().finish()


support.ContinuousObserver = QuotePostcheckObserver


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--execute", action="store_true")
    modes.add_argument("--serve-app", action="store_true")
    modes.add_argument("--serve-proxy", action="store_true")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    run_dir = args.run_dir.resolve()
    if args.serve_proxy:
        support.serve_proxy(run_dir)
        return 0
    if args.serve_app:
        serve_app(run_dir)
        return 0
    output = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
    report = plan(run_dir)
    report.update(executed=bool(args.execute), tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        support_tool_sha256=hashlib.sha256(_SUPPORT_PATH.read_bytes()).hexdigest())
    support.fresh_json(output, report)
    if args.execute:
        return support.execute(run_dir, output, report)
    print(json.dumps({"executed": False, "profile": PROFILE, "plan": str(output), "budget": report["budget"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Plan only by default: fixed-v1, fixed-input real Pi quote postcheck.

The page handshake precedes any canonical text or paid state work. One natural
state is followed by two intents on the same input basis, with root-signalled
read-only observation windows. The prior operator-timeout run and its remaining
four forwards are never resumed, reset or reused.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import uuid


_BASE_PATH = Path(__file__).with_name("participation_quote_postcheck.py")
_SPEC = importlib.util.spec_from_file_location("_single_basis_quote_postcheck_base", _BASE_PATH)
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)
support = base.support

PROFILE = "participation_quote_single_basis.v1"
DEFAULT_RUN_DIR = support.ROOT / "artifacts/realtime-participation-20261007/quote-single-basis-r01"
support.SCOPE_PREFIX = "participation_quote_single_basis_"
support.BOUNDARY_SCHEMA = "participation_quote_single_basis_boundary.v1"
support.STOP_CODES |= {"bootstrap_ui_ready_timeout", "single_basis_contract_failed"}
base.PROFILE = PROFILE
base.STAGES |= {"bootstrap"}


def plan(run_dir: Path) -> dict:
    result = base.plan(run_dir)
    result.update(schema_version="participation_quote_single_basis_acceptance.v1", profile=PROFILE,
        input="Same public payment-timeout text and fixed v1 material; no second segment, v2 or semantic result copied",
        stages=[{"id": "bootstrap", "page_ready_seconds": 60,
                "timing": "after scope/preparation/v1 creation, before canonical input or any paid work"},
            {"id": "natural_state", "wait": "normal first-state timer; exactly one actual state"},
            {"id": "manual1", "intent": "ask_question", "inspection_max_seconds": 60},
            {"id": "manual2", "intent": "formulate_response", "inspection_max_seconds": 60,
                "basis": "same source revision/hash, v1 authorization, topic, basis and input version"}],
        prior_operator_handshake_failure={"report": "artifacts/realtime-participation-20261007/quote-postcheck-r01/report.json",
            "report_sha256": "ae41f30c48a4956272456c45bd45ae172438e3806c7d3189e61f6d9843ef38fd",
            "actual_state_calls": 1, "actual_pi_requests": 0, "classification": "operator_ui_ready_timeout",
            "old_remaining_forwards": 4, "remaining_budget_reused": False},
        cumulative_scope_budget={"prior_actual_upstream_posts": 1, "new_maximum_posts": 5, "combined_maximum_posts": 6},
        material_version_change_tested=False,
        evaluation_limits="Narrow two-intent fixed-v1 quote follow-up, not a retry of a Pi failure or a v1/v2 lifecycle re-test; no latency percentile.")
    result["ui_signal_contract"].update(storage="run_dir/ui-signals/{bootstrap.page_ready|manual1.inspected|manual2.inspected}.json",
        bootstrap_page_ready_seconds=60, post_publication_max_seconds=60,
        page_ready_timeout="stop before canonical input, verify zero paid work", manual_page_ready_handshakes=0)
    return result


def fixed_v1_request_checks(stage, job, snapshot, *, material_version, public_texts, observed_at_ms):
    checks, details = base.request_checks(stage, job, snapshot, material_version=material_version,
        material_text=support.MATERIAL_V1, public_texts=public_texts, observed_at_ms=observed_at_ms)
    # This new profile explicitly does not switch material versions. The old
    # tool's unique-v2 test remains unchanged and is not this profile's claim.
    checks.pop("manual2_visible_fact_unique_to_v2", None)
    details["material_version_policy"] = "fixed_v1; version-change coverage not claimed"
    return checks, details


def fixed_basis_checks(first_job: dict, second_job: dict, *, source: dict, material_version: str,
                       initial_discussion: dict, initial_state_job: dict) -> tuple[dict, dict]:
    first = first_job.get("request_context") or {}
    second = second_job.get("request_context") or {}

    def evidence(metadata):
        return {(item.get("segment_id"), item.get("revision"), item.get("evidence_hash"))
            for item in metadata.get("evidence") or [] if isinstance(item, dict)}

    def material_sources(job):
        card = ((job.get("output") or {}).get("applied") or {}).get("coach_intervention") or {}
        return [item for item in card.get("source_evidence") or [] if item.get("source_type") == "meeting_attachment"]

    versions1, versions2 = material_sources(first_job), material_sources(second_job)
    source_tuple = (source["segment_id"], 1, source["seed"]["source_hash"])
    hashes1 = {item.get("content_hash") for item in versions1}
    hashes2 = {item.get("content_hash") for item in versions2}
    initial_basis = (initial_discussion.get("context") or {}).get("analysis_basis_revision")
    checks = {
        "both_requests_match_natural_state_basis": bool(initial_discussion.get("work_item_id")) and type(initial_basis) is int
            and first.get("topic_id") == second.get("topic_id") == initial_discussion["work_item_id"]
            and first.get("analysis_basis_revision") == second.get("analysis_basis_revision") == initial_basis,
        "same_topic_and_basis_both_requests": bool(first.get("topic_id"))
            and (first.get("topic_id"), first.get("analysis_basis_revision")) == (second.get("topic_id"), second.get("analysis_basis_revision")),
        "same_canonical_input_sequence_and_version": first_job.get("input_transcript_seq") == second_job.get("input_transcript_seq") == 1
            and type(initial_state_job.get("input_version")) is int
            and first_job.get("input_version") == second_job.get("input_version") == initial_state_job["input_version"],
        "same_canonical_job_source_identity": first_job.get("evidence_segment_id") == second_job.get("evidence_segment_id") == source_tuple[0]
            and first_job.get("evidence_hash") == second_job.get("evidence_hash") == source_tuple[2],
        "same_anchor_sequence": first.get("anchor_seq") == second.get("anchor_seq") == 1,
        "same_frozen_source_revision_hash": evidence(first) == evidence(second) == {source_tuple},
        "same_authorized_input_bindings": bool(first.get("input_context")) and first["input_context"] == second.get("input_context"),
        "same_v1_attachment_version_and_hash": bool(versions1 and versions2) and hashes1 == hashes2
            and None not in hashes1 and all(item.get("version_id") == material_version for item in versions1 + versions2),
    }
    details = {"topic_id": first.get("topic_id"), "analysis_basis_revision": first.get("analysis_basis_revision"),
        "natural_state_topic_id": initial_discussion.get("work_item_id"), "natural_state_basis": initial_basis,
        "natural_state_input_version": initial_state_job.get("input_version"),
        "input_transcript_seq": first_job.get("input_transcript_seq"), "input_version": first_job.get("input_version"),
        "anchor_seq": first.get("anchor_seq"), "evidence": [dict(segment_id=item[0], revision=item[1], evidence_hash=item[2])
            for item in sorted(evidence(first))], "active_attachment_version": material_version,
        "attachment_content_hashes": sorted(item for item in hashes1 if isinstance(item, str)),
        "whole_discussion_context_or_packet_hash_compared": False}
    return checks, details


class SingleBasisObserver(base.QuotePostcheckObserver):
    def signal(self, stage: str, name: str, *, maximum: int = 60) -> dict:
        try:
            return super().signal(stage, name, maximum=60)
        except support.AcceptanceStop as exc:
            if stage == "bootstrap" and exc.code == "ui_ready_timeout":
                raise support.AcceptanceStop("bootstrap_ui_ready_timeout", "bootstrap page readiness timed out before paid input") from None
            raise

    def run(self):
        from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
        self.report["preflight"] = support.preflight(self.run_dir, self.client)
        self.store = support.value.open_synthetic_store(self.run_dir / "runtime/meeting_copilot.db")
        self.live = SqliteAsrLiveSessionRepository(self.run_dir / "runtime")
        self.mid = support.SCOPE_PREFIX + uuid.uuid4().hex[:12]
        self.report["meeting_id"] = self.mid
        try:
            self.http("POST", "/v2/meetings", json={"meeting_id": self.mid,
                "title": "固定v1真实Pi引文后验 · 公开合成资料 · 无音频"}, headers={"Idempotency-Key": self.mid})
            prep = self.http("GET", f"/v2/meetings/{self.mid}/preparation")
            self.report["preparation"] = self.http("PUT", f"/v2/meetings/{self.mid}/preparation", json={**prep,
                "live_analysis_profile": "participation", "proactive_suggestion_policy": "off"})
            support.value.create_synthetic_live_record(self.live, self.mid)
            self.emit("scope_created", profile=PROFILE,
                workbench_url=f"http://127.0.0.1:{support.APP_PORT}/workbench?meeting_id={self.mid}")
            self.material_v1()
            self.signal("bootstrap", "page_ready")
            self.append_payment()
            state = self.wait_job("discussion_state", 1)
            ready = self.checkpoint("natural_first_state_ready", job=state)
            context = (ready.get("discussion") or {}).get("context") or {}
            if context.get("phase") != "discussing" or context.get("last_semantic_seq") != 1:
                raise support.AcceptanceStop("initial_state_incomplete", "first actual state was not applied; no paid compensation")
            self.manual("ask_question", "请结合当前付款接口超时的讨论和本场需求资料，帮我提出一个有价值的具体问题，补齐还未确定的决策边界。")
            job1 = self.wait_job("manual", 1, seconds=45)
            first = self.checkpoint("manual1_current_published", job=job1)
            checks1, details1 = fixed_v1_request_checks("manual1", job1, first["snapshot"],
                material_version=self.report["material"]["version1_id"], public_texts=self.public_texts,
                observed_at_ms=first["observed_at_ms"])
            self.report["requests"] = [details1]
            self.report["contract_checks"] = checks1
            self.report["quote_efficiency_checks"] = {"manual1_no_main_quote_repair": details1["quote_efficiency"]["no_main_quote_repair"]}
            if not all(checks1.values()):
                raise support.AcceptanceStop("single_basis_contract_failed", "first fixed-v1 contract failed; no compensation")
            observation1 = self.inspect_publication("manual1", job1, first)
            self.manual("formulate_response", "请结合当前付款接口超时讨论和同一份已启用的需求资料，帮我组织一句可以在会上提出的回应，说明怎样处理查询不可用和用户重新付款的边界，并注明尚待确认之处。")
            job2 = self.wait_job("manual", 2, seconds=45)
            second = self.checkpoint("manual2_current_published", job=job2)
            checks2, details2 = fixed_v1_request_checks("manual2", job2, second["snapshot"],
                material_version=self.report["material"]["version1_id"], public_texts=self.public_texts,
                observed_at_ms=second["observed_at_ms"])
            identity_checks, identity_proof = fixed_basis_checks(job1, job2, source=self.report["sources"][0],
                material_version=self.report["material"]["version1_id"], initial_discussion=ready["discussion"], initial_state_job=state)
            self.report["fixed_basis_proof"] = identity_proof
            self.report["requests"].append(details2)
            self.report["contract_checks"].update(checks2 | identity_checks)
            self.report["quote_efficiency_checks"]["manual2_no_main_quote_repair"] = details2["quote_efficiency"]["no_main_quote_repair"]
            if not all(checks2.values()) or not all(identity_checks.values()):
                raise support.AcceptanceStop("single_basis_contract_failed", "second fixed-v1 contract failed; no compensation")
            observation2 = self.inspect_publication("manual2", job2, second)
            jobs, proxy_state = self.collect()
            counts = support.host_budget(jobs)
            self.report["contract_checks"].update(
                one_natural_state_two_manual_no_automatic=counts["discussion_state"] == 1 and counts["manual"] == 2 and counts["automatic"] == 0,
                three_host_jobs_single_attempt=counts["host_jobs"] == counts["host_attempts"] == 3 and not counts["retried_jobs"],
                no_budget_guard_or_upstream_retry=not self.report["guard_activations"] and not proxy_state["budget_rejections"] and not proxy_state["upstream_retries"],
                actual_forwarded_calls_within_five=3 <= proxy_state["reserved_forward_attempts"] <= 5,
                first_card_is_historical_after_second=not (second["snapshot"].get("coach_intervention") or {}).get("job_id") == job1["id"]
                    and support.history_contains(second["snapshot"], first["snapshot"]["coach_intervention"]))
            self.report["ui_checks"] = {"manual1_exact_dom_ack": observation1["exact_six_field_dom_ack"],
                "manual2_exact_dom_ack": observation2["exact_six_field_dom_ack"],
                "state_catchup_current_page": "root_observation_pending_not_inferred_from_bootstrap_signal"}
            self.report["contract_failures"] = [key for key, passed in self.report["contract_checks"].items() if not passed]
            self.report["automated_contract_passed"] = not self.report["contract_failures"]
            self.report["quote_efficiency_passed"] = all(self.report["quote_efficiency_checks"].values())
            self.report["stop_reason"] = "single_basis_results_observed_value_review_pending"
        finally:
            self.finish()

    def finish(self):
        if self.store is not None and self.mid and not self.report.get("sources"):
            try:
                # Cleanup never depends on another network/identity collection;
                # that could replace the original stop with a second exception.
                jobs = self.store.list_jobs(meeting_id=self.mid)
                count = self.store._conn.execute("SELECT COUNT(*) FROM transcript_segments WHERE meeting_id=?", (self.mid,)).fetchone()[0]
                semantic = self.store._conn.execute("SELECT COUNT(*) FROM semantic_paragraphs WHERE meeting_id=?", (self.mid,)).fetchone()[0]
                usage = self.store._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger WHERE session_id=?", (self.mid,)).fetchone()[0]
                ledger = json.loads((self.run_dir / "proxy-ledger.json").read_text(encoding="utf-8"))
                forwards = ledger.get("reserved_forward_attempts")
                if type(forwards) is not int or not isinstance(ledger.get("events"), list):
                    raise ValueError("invalid_local_ledger_identity")
                self.report["pre_input_paid_work_proof"] = {"canonical_segments": count, "semantic_paragraphs": semantic,
                    "durable_jobs": len(jobs), "usage_rows": usage, "actual_upstream_posts": forwards,
                    "zero_paid_work": count == semantic == len(jobs) == usage == forwards == len(ledger["events"]) == 0}
            except Exception:
                self.report["pre_input_paid_work_proof"] = {"zero_paid_work": "unknown_not_zero",
                    "reason": "local_proof_unavailable"}
        try:
            super().finish()
        except Exception as exc:
            self.report["cleanup_error_class"] = type(exc).__name__
            self.report["automated_contract_passed"] = False
            self.report.setdefault("upstream_accounting", {"status": "unknown_not_zero", "reason": "final_ledger_unavailable"})
            if self.store is not None and self.mid:
                try:
                    support.close_scope(self.store, self.mid)
                    self.report["scope_closed_without_paid_postprocessing"] = True
                except Exception as closing:
                    self.report["scope_closed_without_paid_postprocessing"] = False
                    self.report["metadata_close_error_class"] = type(closing).__name__


support.ContinuousObserver = SingleBasisObserver


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
        base.serve_app(run_dir)
        return 0
    output = (args.report or run_dir / ("report.json" if args.execute else "plan.json")).resolve()
    report = plan(run_dir)
    report.update(executed=bool(args.execute), tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        inherited_quote_tool_sha256=hashlib.sha256(_BASE_PATH.read_bytes()).hexdigest(),
        support_tool_sha256=hashlib.sha256(base._SUPPORT_PATH.read_bytes()).hexdigest())
    support.fresh_json(output, report)
    if args.execute:
        return support.execute(run_dir, output, report)
    print(json.dumps({"executed": False, "profile": PROFILE, "plan": str(output), "budget": report["budget"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

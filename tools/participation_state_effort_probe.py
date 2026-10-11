"""Dry-run first: bounded omitted/low state-only pairs from synthetic history.

This tool never creates meetings, starts a service, uses sound devices, calls a
coach, or mutates the source database. Execution needs explicit --execute and a
configured gpt-6-sol/Responses provider inherited privately through environment.
Request bodies, credentials and rejected outputs are never archived. Only
accepted, host-validated synthetic state is retained for quality review.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "code/web_mvp/backend"), str(ROOT / "code/core")]
DEFAULT_DB = ROOT / "artifacts/realtime-participation-20261007/runtime/meeting_copilot.db"
DEFAULT_REPORT = ROOT / "artifacts/realtime-participation-20261007/state-effort-probe-r01.json"
CASES = (
    ("business_boundary", "global-timer-acceptance-r03.json", None,
     "job_discussion_state_fc65b44d29bb08f5ee64a9f1146a"),
    ("attachment_tradeoff", "value-acceptance-r03.json", "architecture_tradeoff_with_material",
     "job_discussion_state_6192e23e033058f3cb8dec1c42c8"),
    ("answered_progression", "value-acceptance-r04.json", "answered_point_progression",
     "job_discussion_state_a55540fdb2973c065c592b61a439"),
)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def accepted_state_checks(case: "FrozenCase", typed: dict) -> dict:
    """Record accepted synthetic semantics; structure gates do not replace review."""
    operations = typed["state_changes"]
    candidates = [op for op in operations if op["type"] == "decision" and op.get("status") == "candidate"]
    resolved = [op for op in operations if op["type"] == "open_question" and op["operation"] == "resolve"]
    opened = [op for op in operations if op["type"] == "open_question" and op["operation"] in {"add", "update"}]
    quoted = ([typed["topic_update"]] if typed["topic_update"] else []) + operations
    exact_count = sum(bool(item.get("evidence_quote")) for item in quoted)
    prior = case.request.rolling_state.get("discussion", {}).get("open_points", [])
    prior_ids = {item["id"] for item in prior if isinstance(item, dict) and item.get("id")}
    prior_retry_ids = {item["id"] for item in prior if isinstance(item, dict) and item.get("id")
                       and "重试" in str(item.get("text") or "")}
    old_id_matches = [op["item_id"] for op in resolved if op.get("item_id") in prior_ids]
    latest_retry_resolutions = [op for op in resolved if "value-response-2" in op.get("evidence_segment_ids", [])
                                and "重试" in (op.get("content") or "")]
    latest_retry_ids = {op.get("item_id") for op in latest_retry_resolutions}
    preserved_retry_identity = not prior_retry_ids or bool(prior_retry_ids & latest_retry_ids)
    minimum = bool(operations and candidates and exact_count)
    if case.case_id == "answered_progression":
        minimum = minimum and bool(opened and latest_retry_resolutions and preserved_retry_identity)
    return {"minimum_semantic_structure": minimum, "candidate_decision_count": len(candidates),
            "resolved_open_question_count": len(resolved), "new_or_updated_open_question_count": len(opened),
            "exact_supported_quote_count": exact_count, "prior_open_question_id_matches": old_id_matches,
            "prior_retry_open_question_ids": sorted(prior_retry_ids),
            "retry_resolution_preserves_known_question_identity": preserved_retry_identity,
            "retry_resolution_uses_latest_answer_evidence": bool(latest_retry_resolutions),
            "resolution_check_scope": "exact prior-ID check where present; fixed synthetic retry content/evidence check is only a diagnostic, not semantic grading",
            "accepted_state": {"topic_update": typed["topic_update"], "state_changes": operations},
            "accepted_synthetic_state_only_not_raw_provider_response": True}


def official_state_prompt(path: Path | None = None) -> str:
    """Read the literal production prompt without importing/starting app.py."""
    path = path or ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp/app.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StateOnlyProvider":
            for assignment in ast.walk(node):
                if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant):
                    value = assignment.value.value
                    if isinstance(value, str) and value.startswith("你是中文讨论工作记忆整理器"):
                        candidates.append(value)
    if len(candidates) != 1:
        raise ValueError("production_state_prompt_contract_changed")
    return candidates[0]


@dataclass(repr=False)
class FrozenCase:
    case_id: str
    state_job_id: str
    request: Any
    messages: list[dict[str, str]]
    evidence: list[dict[str, Any]]
    frozen_metadata_hash: str
    report_sha256: str
    source_revision_hash_verified: bool = False

    def safe_plan(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "state_job_id": self.state_job_id,
                "message_packet_sha256": fingerprint(self.messages),
                "frozen_metadata_sha256": self.frozen_metadata_hash,
                "approved_report_sha256": self.report_sha256,
                "message_characters": sum(len(m["content"]) for m in self.messages),
                "paragraph_count": len(self.evidence), "evidence": self.evidence,
                "source_revision_hash_verified": self.source_revision_hash_verified,
                "historical_wire_packet_saved": False,
                "reconstruction_scope": "frozen original metadata/revision/hash + current formal request/prompt contract; not a historical byte-for-byte wire claim"}


def prepare_cases(database: Path, pairs: int) -> list[FrozenCase]:
    from meeting_copilot_web_mvp.realtime_intelligence import (
        MAX_CONTEXT_PARAGRAPHS, MAX_NEW_PARAGRAPHS, RealtimeIntelligenceRequest,
        build_realtime_intelligence_messages,
    )
    from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash
    if pairs not in {2, 3}:
        raise ValueError("pairs_must_be_two_or_three")
    prompt = official_state_prompt()
    source_root = ROOT / "artifacts/realtime-participation-20261007"
    # For two pairs cover business candidate and resolved-response progression.
    chosen = CASES if pairs == 3 else (CASES[0], CASES[2])
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        result = []
        for case_id, report_name, report_case_id, job_id in chosen:
            report_path = source_root / report_name
            report_bytes = report_path.read_bytes()
            report = json.loads(report_bytes)
            if report_case_id is None:
                assert report["automatic"]["valid_intervention"]
                assert report["automatic"]["own_decision_status"] == "intervention"
                meeting_id = report["meeting_id"]
            else:
                case = next(item for item in report["cases"] if item["case_id"] == report_case_id)
                assert any(q["valid_intervention"] and q["own_decision_status"] == "intervention"
                           for q in case["requests"])
                meeting_id = case["meeting_id"]
            if not meeting_id.startswith(("participation_global_", "participation_value_")):
                raise ValueError("only_approved_synthetic_meetings_allowed")
            job = connection.execute("SELECT meeting_id, status, request_context_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if job is None or job["meeting_id"] != meeting_id or job["status"] != "succeeded":
                raise ValueError("original_successful_state_job_missing")
            metadata = json.loads(job["request_context_json"])
            if metadata.get("profile") != "participation" or metadata.get("task_mode") != "discussion_state":
                raise ValueError("original_state_scope_mismatch")
            evidence = metadata["evidence"]
            ids = {item["segment_id"] for item in evidence}
            segments = [dict(row) for row in connection.execute(
                "SELECT segment_id,transcript_seq,revision,text,normalized_text FROM transcript_segments "
                "WHERE meeting_id=? ORDER BY transcript_seq", (meeting_id,)) if row["segment_id"] in ids]
            if len(segments) != len(ids):
                raise ValueError("frozen_source_missing")
            by_id = {item["segment_id"]: item for item in segments}
            for item in evidence:
                segment = by_id[item["segment_id"]]
                text = str(segment.get("normalized_text") or segment.get("text") or "")
                if segment["revision"] != item["revision"] or transcript_evidence_hash(item["segment_id"], text) != item["evidence_hash"]:
                    raise ValueError("frozen_source_revision_or_hash_changed")
            from_seq = int(metadata.get("semantic_from_seq") or 0)
            fresh = [item for item in segments if item["transcript_seq"] > from_seq]
            def paragraph(item):
                return {"id": item["segment_id"], "text": item["normalized_text"] or item["text"] or "",
                        "revision": int(item["revision"] or 1)}
            new = [paragraph(item) for item in fresh[-MAX_NEW_PARAGRAPHS:]]
            new_ids = {item["id"] for item in new}
            prior = [paragraph(item) for item in segments if item["segment_id"] not in new_ids]
            context = metadata["discussion_context"]
            open_items = [{"id": point["id"], "type": point.get("entity_type") or kind,
                           "text": point.get("text") or "", "status": point.get("status") or "open"}
                          for field, kind in (("constraints", "decision"), ("open_points", "open_question"))
                          for point in context.get(field, []) if isinstance(point, dict) and point.get("id")]
            request = RealtimeIntelligenceRequest.from_payload(
                meeting_id=meeting_id, state_revision=int(metadata["anchor_seq"]), trigger_type="discussion_update",
                work_item_id=job_id, new_paragraphs=new, context_paragraphs=prior[-MAX_CONTEXT_PARAGRAPHS:],
                retrieval_paragraphs=prior[:-MAX_CONTEXT_PARAGRAPHS],
                rolling_state={"topic": {"title": context.get("current_problem") or ""} if context.get("last_semantic_seq") else None,
                               "discussion": context, "open_items": open_items, "current_answer": None,
                               "version": int(metadata["anchor_seq"])}, allow_paragraph_revisions=False)
            messages = build_realtime_intelligence_messages(request)
            messages[0]["content"] = prompt
            packet = json.loads(messages[1]["content"])
            packet["retrieval_paragraphs"] = [p.to_prompt_dict() for p in request.retrieval_paragraphs]
            packet["state_task"] = {"scope": "current_meeting_discussion", "semantic_from_seq": from_seq,
                                    "pending_segment_ids": [item["segment_id"] for item in fresh]}
            messages[1]["content"] = json.dumps(packet, ensure_ascii=False)
            result.append(FrozenCase(case_id, job_id, request, messages, evidence,
                                     fingerprint(metadata), hashlib.sha256(report_bytes).hexdigest(), True))
        return result
    finally:
        connection.close()


class WireBudgetTransport(httpx.AsyncBaseTransport):
    """Count every outbound POST including unexpected transport fallbacks."""
    def __init__(self, inner: httpx.AsyncBaseTransport, maximum: int, journal_path: Path | None = None):
        self.inner, self.maximum = inner, maximum
        self.count = 0
        self.forward_count = 0
        self.records: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self.journal_path = journal_path
        if journal_path is not None:
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            # A second process/run cannot silently reset an existing budget.
            with journal_path.open("x", encoding="utf-8") as stream:
                json.dump(self._journal(), stream)
                stream.flush()
                os.fsync(stream.fileno())

    def _journal(self) -> dict:
        return {"schema_version": "state_effort_wire_budget.v1", "maximum_actual_POSTs": self.maximum,
                "reserved_attempts": self.count,
                "forward_dispatch_marked_attempts": sum(bool(r.get("forward_dispatch_marked")) for r in self.records),
                "forward_started_attempts": self.forward_count,
                "remote_receipt_or_charge_known": False,
                "reservation_consumes_budget_even_on_failure": True, "records": self.records,
                "raw_request_response_headers_keys_archived": False}

    def _publish(self) -> None:
        if self.journal_path is None:
            return
        temporary = self.journal_path.with_name(self.journal_path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self._journal(), stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.journal_path)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/v1/responses"):
            raise RuntimeError("unexpected_outbound_request")
        body = json.loads(request.content)
        if body.get("model") != "gpt-6-sol" or body.get("stream") is not True:
            raise RuntimeError("unexpected_provider_contract")
        reasoning = body.get("reasoning")
        if reasoning is not None and reasoning != {"effort": "low"}:
            raise RuntimeError("unexpected_reasoning_effort")
        no_effort = {key: value for key, value in body.items() if key != "reasoning"}
        async with self._lock:
            if self.count >= self.maximum:
                raise RuntimeError("actual_post_budget_exhausted")
            self.count += 1
            record = {"actual_post_index": self.count, "model": body["model"], "api_style": "responses",
                      "stream": body["stream"], "reasoning_effort_present": "reasoning" in body,
                      "reasoning_effort": (reasoning or {}).get("effort"),
                      "max_output_tokens": body.get("max_output_tokens"),
                      "wire_without_reasoning_sha256": fingerprint(no_effort), "HTTP_status": None,
                      "forward_dispatch_marked": False, "forward_started": False}
            self.records.append(record)
            self._publish()  # Durable reservation exists before any upstream work.
            # Persist dispatch intent first. A crash in the following tiny gap
            # cannot establish arrival/billing, and must never reset the budget.
            record["forward_dispatch_marked"] = True
            self._publish()
            record["forward_started"] = True
            self.forward_count += 1
        try:
            response = await self.inner.handle_async_request(request)
            record["HTTP_status"] = response.status_code
            self._publish()
            return response
        except BaseException as error:
            record["transport_error_class"] = type(error).__name__
            self._publish()
            raise

    async def aclose(self) -> None:
        await self.inner.aclose()


async def evaluate_pair(cases: list[FrozenCase], config: Any, *, inner: httpx.AsyncBaseTransport | None = None,
                        journal_path: Path | None = None) -> dict:
    from meeting_copilot_web_mvp.discussion_state_diagnostics import state_validation_failure_output
    from meeting_copilot_web_mvp.realtime_intelligence import run_realtime_intelligence
    from meeting_copilot_web_mvp.streaming_llm_provider import OpenAICompatibleStreamingProvider

    if config.model != "gpt-6-sol" or config.api_style != "responses" or config.is_mock:
        raise ValueError("requires_original_gpt6sol_responses_config")
    maximum = len(cases) * 2
    budget = WireBudgetTransport(inner or httpx.AsyncHTTPTransport(retries=0), maximum, journal_path)
    started = time.perf_counter()
    run_nonce = f"state-effort:{time.time_ns()}"
    results = []
    async with httpx.AsyncClient(transport=budget, timeout=25, trust_env=False) as client:
        for index, case in enumerate(cases):
            order = ("omitted", "low") if index % 2 == 0 else ("low", "omitted")
            for effort in order:
                transport = OpenAICompatibleStreamingProvider(
                    base_url=config.base_url, api_key=config.api_key, model=config.model, api_style=config.api_style,
                    client=client, timeout_seconds=25, allow_non_streaming_fallback=False)
                captured = {}
                observed_usage = None
                before_count = budget.count
                before_forward_count = budget.forward_count
                class FrozenProvider:
                    async def complete(self, _messages, **parameters):
                        parameters["idempotency_key"] = f"{run_nonce}:{case.case_id}:{effort}"
                        if effort == "low":
                            parameters["reasoning_effort"] = "low"
                        response = await transport.complete(case.messages, **parameters)
                        captured["timings"] = response.timings
                        captured["finish_reason"] = response.finish_reason
                        captured["output_characters"] = len(response.content)
                        return response
                def before_attempt(attempt):
                    if attempt != 1:
                        raise RuntimeError("state_probe_repair_forbidden")
                def usage_seen(usage, _attempt):
                    nonlocal observed_usage
                    observed_usage = usage
                item = {"case_id": case.case_id, "reasoning_variant": effort,
                        "message_packet_sha256": fingerprint(case.messages), "host_evidence_validated": False,
                        "manual_semantic_equivalence": "pending", "raw_model_text_archived": False}
                call_started = time.perf_counter()
                try:
                    result = await run_realtime_intelligence(request=case.request, provider=FrozenProvider(),
                        before_attempt=before_attempt, on_usage=usage_seen, allow_repair=False)
                    typed = result["response"].to_dict()
                    operations = typed["state_changes"]
                    item.update(host_evidence_validated=True, validated_response_sha256=fingerprint(typed),
                        state_change_count=len(operations), topic_update_present=typed["topic_update"] is not None,
                        state_operations=[{"type": op["type"], "operation": op["operation"],
                            "status": op.get("status"), "item_id_sha256": fingerprint(op.get("item_id")),
                            "content_characters": len(op.get("content") or ""),
                            "content_sha256": fingerprint(op.get("content")),
                            "evidence_segment_ids": op.get("evidence_segment_ids"),
                            "evidence_quote_characters": len(op.get("evidence_quote") or "")} for op in operations])
                    item.update(accepted_state_checks(case, typed))
                except Exception as error:
                    item["error_class"] = type(error).__name__
                    if type(error).__name__ == "IntelligenceResponseValidationError":
                        item["failure_diagnostics"] = state_validation_failure_output(error,
                            provider_attempt_count=1, paragraph_counts={"new": len(case.request.new_paragraphs),
                            "context": len(case.request.context_paragraphs), "retrieval": len(case.request.retrieval_paragraphs)},
                            usage=observed_usage)["failure_diagnostics"]
                    elif getattr(error, "status_code", None) is not None:
                        item["HTTP_status"] = error.status_code
                item["complete_host_validation_wall_ms"] = round((time.perf_counter()-call_started)*1000, 3)
                item["actual_upstream_POSTs"] = budget.forward_count-before_forward_count
                item["budget_reserved_attempts"] = budget.count-before_count
                item["usage"] = observed_usage
                item["wire"] = budget.records[before_count:]
                timings = captured.get("timings")
                if timings is not None:
                    item["provider_complete_ms"] = round(timings.total_seconds*1000, 3)
                    item["provider_first_observed_text_ms"] = round(timings.time_to_first_token_seconds*1000, 3)
                    item["provider_output_tail_ms"] = round(timings.generation_seconds*1000, 3)
                    item["finish_reason"] = captured["finish_reason"]
                    item["model_json_output_characters"] = captured["output_characters"]
                results.append(item)
                # One failed pair stops the experiment; no implicit fallback or retry.
                if not item["host_evidence_validated"] or not item.get("minimum_semantic_structure"):
                    return {"results": results, "actual_upstream_POSTs": budget.forward_count,
                            "budget_reserved_attempts": budget.count, "maximum_actual_POSTs": maximum,
                            "stop_reason": "first_failed_validation_transport_or_minimum_state", "all_host_evidence_validated": all(i["host_evidence_validated"] for i in results),
                            "all_minimum_semantic_structure": False}
    return {"results": results, "actual_upstream_POSTs": budget.forward_count,
            "budget_reserved_attempts": budget.count, "maximum_actual_POSTs": maximum,
            "stop_reason": "planned_pairs_completed", "all_host_evidence_validated": all(i["host_evidence_validated"] for i in results),
            "all_minimum_semantic_structure": all(i["minimum_semantic_structure"] for i in results),
            "total_wall_ms": round((time.perf_counter()-started)*1000, 3)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, choices=(2, 3), default=2)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    journal_path = args.report.with_name(args.report.stem + ".wire-journal.json")
    if args.report.exists() or journal_path.exists():
        raise ValueError("refuse_to_overwrite_existing_probe")
    report = {"schema_version": "participation_state_effort_probe.v1", "executed": args.execute,
              "scope": "state-only fixed synthetic packets; no coach/natural timer/ASR/devices/production mutation",
              "maximum_actual_POSTs": args.pairs*2,
              "raw_requests_or_provider_responses_or_credentials_archived": False,
              "accepted_synthetic_state_quotes_only_after_host_validation": True,
              "upstream_count_scope": "transport dispatch attempts, not proof of remote receipt or billing",
              "manual_semantic_equivalence": "pending", "P50_P95_or_representative_value_claimed": False}
    try:
        cases = prepare_cases(args.database, args.pairs)
        report["plans"] = [case.safe_plan() for case in cases]
        report["production_prompt_sha256"] = hashlib.sha256(official_state_prompt().encode()).hexdigest()
        report["current_contract_source_sha256"] = {
            name: hashlib.sha256((ROOT / "code/web_mvp/backend/meeting_copilot_web_mvp" / name).read_bytes()).hexdigest()
            for name in ("app.py", "realtime_intelligence.py", "openai_protocol.py", "streaming_llm_provider.py")}
        if args.execute:
            from meeting_copilot_web_mvp.llm_service import LlmConfig
            config = LlmConfig.from_env()
            if config is None:
                raise ValueError("provider_environment_not_configured")
            report.update(asyncio.run(evaluate_pair(cases, config, journal_path=journal_path)))
        else:
            report["actual_upstream_POSTs"] = 0
            report["budget_reserved_attempts"] = 0
    except BaseException as error:
        report.update(stop_reason="unhandled_safe_failure", error_class=type(error).__name__,
                      all_host_evidence_validated=False, all_minimum_semantic_structure=False)
        # Preserve attempted/could-be-billed work even if the coroutine escapes.
        if journal_path.exists():
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                started_count = journal["forward_started_attempts"]
                marked_count = journal["forward_dispatch_marked_attempts"]
                report["actual_upstream_POSTs"] = started_count if started_count == marked_count else None
                report["forward_started_attempts_recorded"] = started_count
                report["forward_dispatch_marked_attempts"] = marked_count
                report["budget_reserved_attempts"] = journal["reserved_attempts"]
                report["budget_journal"] = str(journal_path)
                report["remote_receipt_or_charge_known"] = False
            except (OSError, ValueError, KeyError):
                report["actual_upstream_POSTs"] = None
                report["budget_reserved_attempts"] = "unknown_do_not_reset"
        else:
            report["actual_upstream_POSTs"] = 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({"executed": args.execute, "planned_pairs": args.pairs,
                      "actual_upstream_POSTs": report["actual_upstream_POSTs"], "report": str(args.report),
                      "stop_reason": report.get("stop_reason", "dry_run_only")}, ensure_ascii=False))
    return 0 if (not args.execute and report.get("stop_reason") != "unhandled_safe_failure") or (
        report.get("all_host_evidence_validated") and report.get("all_minimum_semantic_structure")) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Avoid exception strings, provider URL/config repr or response bodies.
        print(json.dumps({"error_class": type(exc).__name__, "executed_provider_claim": "none_or_unknown"}))
        raise SystemExit(1)

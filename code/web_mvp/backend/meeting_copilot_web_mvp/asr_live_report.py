from __future__ import annotations

from typing import Any


def build_asr_live_draft_review(record: dict[str, Any]) -> dict[str, Any]:
    events = list(record.get("events") or [])
    transcript_segments: list[dict[str, Any]] = []
    evidence_spans: list[dict[str, Any]] = []
    state_candidates: list[dict[str, Any]] = []
    scheduler_decisions: list[dict[str, Any]] = []
    suggestion_candidates: list[dict[str, Any]] = []
    llm_request_drafts: list[dict[str, Any]] = []
    provider_errors: list[dict[str, Any]] = []
    evaluation_summary: dict[str, Any] = {}

    for event in events:
        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload") or {})
        if event_type in {"transcript_final", "transcript_revision"}:
            transcript_segments.append(
                {
                    "id": str(payload.get("segment_id", "")),
                    "event_type": event_type,
                    "text": str(payload.get("text", "")),
                    "start_ms": int(payload.get("start_ms", 0)),
                    "end_ms": int(payload.get("end_ms", 0)),
                    "confidence": payload.get("confidence"),
                    "revision_of": payload.get("revision_of"),
                    "evidence_span_ids": [
                        str(evidence.get("id", ""))
                        for evidence in payload.get("evidence_spans") or []
                    ],
                }
            )
            evidence_spans.extend(payload.get("superseded_evidence_spans") or [])
            evidence_spans.extend(payload.get("evidence_spans") or [])
        elif event_type == "state_event":
            state_candidates.append(
                {
                    "event_id": str(payload.get("event_id", "")),
                    "target_type": str(payload.get("target_type", "")),
                    "target_id": str(payload.get("target_id", "")),
                    "state_event_type": str(payload.get("state_event_type", "")),
                    "evidence_span_ids": list(payload.get("evidence_span_ids") or []),
                    "state_item": dict(payload.get("state_item") or {}),
                }
            )
        elif event_type == "scheduler_event":
            scheduler_decisions.append(
                {
                    "scheduler_event_type": str(payload.get("scheduler_event_type", "")),
                    "decision_reason": str(payload.get("decision_reason", "")),
                    "would_call_llm": bool(payload.get("would_call_llm", False)),
                    "llm_call_status": str(payload.get("llm_call_status", "")),
                    "cooldown_remaining_ms": int(payload.get("cooldown_remaining_ms", 0)),
                    "call_count_last_hour": int(payload.get("call_count_last_hour", 0)),
                    "budget_remaining": int(payload.get("budget_remaining", 0)),
                    "source_event_ids": list(payload.get("source_event_ids") or []),
                    "segment_batch": list(payload.get("segment_batch") or []),
                    "prompt_version": str(payload.get("prompt_version", "")),
                    "model": str(payload.get("model", "")),
                }
            )
        elif event_type == "suggestion_candidate_event":
            suggestion_candidates.append(
                {
                    "candidate_id": str(payload.get("candidate_id", "")),
                    "candidate_type": str(payload.get("candidate_type", "")),
                    "candidate_policy_version": str(payload.get("candidate_policy_version", "")),
                    "confidence_source": str(payload.get("confidence_source", "")),
                    "target_type": str(payload.get("target_type", "")),
                    "target_id": str(payload.get("target_id", "")),
                    "gap_rule_id": str(payload.get("gap_rule_id", "")),
                    "suggested_prompt": str(payload.get("suggested_prompt", "")),
                    "trigger_reason": str(payload.get("trigger_reason", "")),
                    "decision_reason": str(payload.get("decision_reason", "")),
                    "source_event_ids": list(payload.get("source_event_ids") or []),
                    "scheduler_event_type": str(payload.get("scheduler_event_type", "")),
                    "evidence_span_ids": list(payload.get("evidence_span_ids") or []),
                    "segment_batch": list(payload.get("segment_batch") or []),
                    "llm_call_status": str(payload.get("llm_call_status", "")),
                    "card_status": str(payload.get("card_status", "")),
                    "confidence": payload.get("confidence"),
                    "confidence_level": str(payload.get("confidence_level", "")),
                    "degradation_reasons": list(payload.get("degradation_reasons") or []),
                    "source": str(payload.get("source", "")),
                    "candidate_origin": str(payload.get("candidate_origin", "")),
                }
            )
        elif event_type == "llm_request_draft_event":
            llm_request_drafts.append(
                {
                    "request_id": str(payload.get("request_id", "")),
                    "request_type": str(payload.get("request_type", "")),
                    "request_status": str(payload.get("request_status", "")),
                    "target_candidate_id": str(payload.get("target_candidate_id", "")),
                    "target_type": str(payload.get("target_type", "")),
                    "target_id": str(payload.get("target_id", "")),
                    "gap_rule_id": str(payload.get("gap_rule_id", "")),
                    "prompt_version": str(payload.get("prompt_version", "")),
                    "model": str(payload.get("model", "")),
                    "llm_call_status": str(payload.get("llm_call_status", "")),
                    "card_status": str(payload.get("card_status", "")),
                    "schema_status": str(payload.get("schema_status", "")),
                    "suggested_prompt": str(payload.get("suggested_prompt", "")),
                    "input_summary": str(payload.get("input_summary", "")),
                    "source_event_ids": list(payload.get("source_event_ids") or []),
                    "evidence_span_ids": list(payload.get("evidence_span_ids") or []),
                    "segment_batch": list(payload.get("segment_batch") or []),
                    "candidate_confidence": payload.get("candidate_confidence"),
                    "candidate_confidence_level": str(
                        payload.get("candidate_confidence_level", "")
                    ),
                    "candidate_degradation_reasons": list(
                        payload.get("candidate_degradation_reasons") or []
                    ),
                    "request_origin": str(payload.get("request_origin", "")),
                    "source": str(payload.get("source", "")),
                }
            )
        elif event_type == "provider_error":
            provider_errors.append(payload)
        elif event_type == "evaluation_summary":
            evaluation_summary = payload

    return {
        "session_id": str(record.get("session_id", "")),
        "provider": str(record.get("provider", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "review_type": "asr_live_draft",
        "is_formal_report": False,
        "llm_call_status": "not_called",
        "transcript_text": "".join(segment["text"] for segment in transcript_segments),
        "transcript_segments": transcript_segments,
        "evidence_spans": evidence_spans,
        "state_candidates": state_candidates,
        "scheduler_decisions": scheduler_decisions,
        "suggestion_candidates": suggestion_candidates,
        "llm_request_drafts": llm_request_drafts,
        "provider_errors": provider_errors,
        "evaluation_summary": evaluation_summary,
        "suggestion_cards": [],
        "llm_schema_results": [],
        "warnings": [
            "Draft only; not a formal gated meeting report.",
            "Generated from local Live ASR audit events without LLM calls.",
        ],
    }


def render_asr_live_draft_markdown(review: dict[str, Any]) -> str:
    lines = [
        f"# Live ASR Draft Review: {review['session_id']}",
        "",
        "Draft only; not a formal gated meeting report.",
        "Generated from local Live ASR audit events without LLM calls.",
        "",
        "## Transcript Draft",
        "",
    ]
    for segment in review.get("transcript_segments") or []:
        lines.append(
            f"- `{segment['id']}` {segment['start_ms']}-{segment['end_ms']}ms: {segment['text']}"
        )
    lines.extend(["", "## Evidence Spans", ""])
    for evidence in review.get("evidence_spans") or []:
        status = evidence.get("status", "active")
        lines.append(
            f"- `{evidence.get('id', '')}` [{status}] "
            f"{evidence.get('start_ms', 0)}-{evidence.get('end_ms', 0)}ms: "
            f"{evidence.get('quote', '')}"
        )
    lines.extend(["", "## State Candidates", ""])
    for candidate in review.get("state_candidates") or []:
        item = candidate.get("state_item") or {}
        text = item.get("statement") or item.get("question") or item.get("description") or item.get("id", "")
        lines.append(
            f"- {candidate.get('target_type', '')} `{candidate.get('target_id', '')}`: {text}"
        )
    lines.extend(["", "## Scheduler Decisions", ""])
    for decision in review.get("scheduler_decisions") or []:
        lines.append(
            "- "
            f"{decision.get('scheduler_event_type', '')} · "
            f"{decision.get('decision_reason', '')} · "
            f"{decision.get('llm_call_status', '')} · "
            f"budget {decision.get('budget_remaining', 0)}"
        )
    lines.extend(["", "## Suggestion Candidates", ""])
    for candidate in review.get("suggestion_candidates") or []:
        degradation = ", ".join(candidate.get("degradation_reasons") or []) or "none"
        lines.append(
            "- "
            f"{candidate.get('target_type', '')} `{candidate.get('target_id', '')}` · "
            f"{candidate.get('gap_rule_id', '')} · "
            f"confidence {candidate.get('confidence_level', '')}/{candidate.get('confidence', '')} · "
            f"degraded {degradation} · "
            f"{candidate.get('candidate_policy_version', '')} · "
            f"{candidate.get('confidence_source', '')} · "
            f"{candidate.get('llm_call_status', '')} · "
            f"{candidate.get('card_status', '')}: "
            f"{candidate.get('suggested_prompt', '')}"
        )
    lines.extend(["", "## LLM Request Drafts", ""])
    for draft in review.get("llm_request_drafts") or []:
        degradation = ", ".join(draft.get("candidate_degradation_reasons") or []) or "none"
        source_events = ", ".join(draft.get("source_event_ids") or []) or "none"
        evidence_ids = ", ".join(draft.get("evidence_span_ids") or []) or "none"
        segment_ids = ", ".join(draft.get("segment_batch") or []) or "none"
        lines.append(
            "- "
            f"{draft.get('request_type', '')} `{draft.get('request_id', '')}` · "
            f"{draft.get('request_status', '')} · "
            f"candidate `{draft.get('target_candidate_id', '')}` · "
            f"source events {source_events} · "
            f"evidence {evidence_ids} · "
            f"segments {segment_ids} · "
            f"{draft.get('input_summary', '')} · "
            f"{draft.get('gap_rule_id', '')} · "
            f"{draft.get('llm_call_status', '')} · "
            f"{draft.get('schema_status', '')} · "
            f"{draft.get('card_status', '')} · "
            f"confidence {draft.get('candidate_confidence_level', '')}/"
            f"{draft.get('candidate_confidence', '')} · "
            f"degraded {degradation}: "
            f"{draft.get('suggested_prompt', '')}"
        )
    lines.extend(["", "## Provider Errors", ""])
    if review.get("provider_errors"):
        for error in review["provider_errors"]:
            lines.append(f"- {error.get('provider', '')}: {error.get('message', '')}")
    else:
        lines.append("- none")
    summary = review.get("evaluation_summary") or {}
    lines.extend(
        [
            "",
            "## Stream Summary",
            "",
            f"- provider: {summary.get('provider', review.get('provider', ''))}",
            f"- final events: {summary.get('final_event_count', 0)}",
            f"- revision events: {summary.get('revision_event_count', 0)}",
            f"- error events: {summary.get('error_event_count', 0)}",
            f"- passes minimum gate: {summary.get('passes_minimum_gate', False)}",
            "",
        ]
    )
    return "\n".join(lines)

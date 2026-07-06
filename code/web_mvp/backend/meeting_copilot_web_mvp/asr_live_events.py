from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from meeting_copilot_web_mvp.live_events import render_sse_events


ASR_LIVE_SOURCE = "live_asr_stream"
ASR_LIVE_TRACE_KIND = "live_event"

DEFAULT_MIN_FINAL_INTERVAL_MS = 30_000
DEFAULT_MIN_STATE_CHANGE_INTERVAL_MS = 10_000
DEFAULT_MAX_CALLS_PER_HOUR = 80
CANDIDATE_POLICY_VERSION = "asr-candidate-policy.v1"
LOW_ASR_CONFIDENCE_THRESHOLD = 0.80
SHORT_EVIDENCE_TEXT_LENGTH = 6
QUESTION_MARKERS = ("谁", "吗", "怎么", "是否", "有没有", "还没有确认", "?", "？")
ENGINEERING_CONTEXT_MARKERS = (
    "API",
    "api",
    "接口",
    "服务",
    "错误码",
    "错误率",
    "P99",
    "p99",
    "延迟",
    "灰度",
    "回滚",
    "发布",
    "上线",
    "告警",
    "故障",
    "扩容",
    "降级",
    "监控",
    "测试",
    "兼容",
    "压测",
    "缓存",
    "数据库",
    "脚本",
    "指标",
    "QPS",
    "qps",
    "mysql",
    "MySQL",
    "redis",
    "Redis",
    "cluster",
    "feature-store",
    "recommendation-service",
    "request_id",
    "trace_id",
)
ACTION_MARKERS = ("负责", "补充", "推进", "跟进", "处理", "确认", "整理")
RISK_MARKERS = ("风险", "如果", "超过", "异常", "失败", "故障")
UNRESOLVED_QUESTION_MARKERS = ("还没", "还没有", "没定", "未定", "未安排", "待定")
ARCHITECTURE_RISK_MARKERS = (
    "缓存穿透",
    "打到 mysql",
    "打到 MySQL",
    "打到数据库",
    "timeout",
    "超时",
    "堆积",
    "告警延迟",
)
ARCHITECTURE_RISK_UNCERTAINTY_MARKERS = ("可能", "如果", "会", "增多", "峰值")
ACTION_ASSIGNMENT_CUES = ("由", "请", "让", "麻烦", "安排")
ACTION_OWNER_REJECT_MARKERS = ("我们", "大家", "团队", "这边", "这个", "那个")
ASSIGNMENT_CUE_ACTION_MARKERS = ("负责", "补充", "推进", "跟进", "处理", "整理")
NEGATED_RISK_MARKERS = (
    "没有风险",
    "没有明显风险",
    "无风险",
    "无明显风险",
    "暂无风险",
    "风险不大",
    "风险可控",
    "风险已解除",
    "风险已经解除",
    "风险解除",
    "风险已消除",
    "风险已经消除",
    "风险关闭",
    "风险已关闭",
    "风险已经关闭",
)
DEADLINE_PATTERN = re.compile(
    r"(今天|明天|后天|本周[一二三四五六日天]?|下周[一二三四五六日天]?|周[一二三四五六日天]|[0-9一二三四五六七八九十]+号)"
)
DEADLINE_AT_END_PATTERN = re.compile(
    r"(今天|明天|后天|本周[一二三四五六日天]?|下周[一二三四五六日天]?|周[一二三四五六日天]|[0-9一二三四五六七八九十]+号)$"
)
ACTION_MARKER_PATTERN = re.compile(r"(负责(?!人)|补充|推进|跟进|处理|确认|整理)")
OWNER_AT_END_PATTERN = re.compile(r"([\u4e00-\u9fff]{2,3})$")

EVENT_ORDER = {
    "transcript_partial": 5,
    "transcript_final": 10,
    "transcript_revision": 15,
    "state_event": 20,
    "scheduler_event": 30,
    "suggestion_candidate_event": 35,
    "llm_request_draft_event": 40,
    "provider_error": 80,
    "evaluation_summary": 90,
}


def asr_event_source_metadata(provider: str, *, is_mock: bool = True) -> dict[str, Any]:
    return {
        "source": ASR_LIVE_SOURCE,
        "trace_kind": ASR_LIVE_TRACE_KIND,
        "transport": "sse",
        "provider": provider,
        "is_mock": is_mock,
    }


def build_asr_live_events(
    *,
    session_id: str,
    provider: str,
    streaming_events: list[dict[str, Any]],
    is_mock: bool = True,
) -> list[dict[str, Any]]:
    del session_id
    events: list[dict[str, Any]] = []
    evidence_by_segment_id: dict[str, dict[str, Any]] = {}
    scheduler_state = _SchedulerState()
    counts = {
        "partial_event_count": 0,
        "final_event_count": 0,
        "revision_event_count": 0,
        "error_event_count": 0,
        "end_of_stream_event_count": 0,
    }

    for group_index, raw_event in enumerate(streaming_events):
        event_type = str(raw_event.get("event_type", ""))
        if event_type == "partial":
            counts["partial_event_count"] += 1
            events.append(_with_sort_group(_partial_event(raw_event), group_index))
            continue
        if event_type == "final":
            counts["final_event_count"] += 1
            event, evidence = _final_event(raw_event)
            evidence_by_segment_id[str(raw_event["segment_id"])] = evidence
            events.append(_with_sort_group(event, group_index))
            derived_events, scheduler_state = _local_state_scheduler_events(
                raw_event,
                evidence,
                event["at_ms"],
                scheduler_state,
            )
            events.extend(
                _with_sort_group(derived_event, group_index)
                for derived_event in derived_events
            )
            continue
        if event_type == "revision":
            counts["revision_event_count"] += 1
            event, evidence = _revision_event(raw_event, evidence_by_segment_id)
            evidence_by_segment_id[str(raw_event["segment_id"])] = evidence
            events.append(_with_sort_group(event, group_index))
            derived_events, scheduler_state = _local_state_scheduler_events(
                raw_event,
                evidence,
                event["at_ms"],
                scheduler_state,
            )
            events.extend(
                _with_sort_group(derived_event, group_index)
                for derived_event in derived_events
            )
            continue
        if event_type == "error":
            counts["error_event_count"] += 1
            events.append(_with_sort_group(_provider_error_event(raw_event, provider), group_index))
            continue
        if event_type == "end_of_stream":
            counts["end_of_stream_event_count"] += 1
            events.append(
                _with_sort_group(
                    _evaluation_event(raw_event, provider, counts, is_mock=is_mock),
                    group_index,
                )
            )
            continue
        raise ValueError(f"unsupported ASR streaming event_type: {event_type}")

    return [
        {
            **_public_event(event),
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "sequence": index,
        }
        for index, event in enumerate(sorted(events, key=_event_sort_key), start=1)
    ]


def render_asr_sse_events(events: list[dict[str, Any]]) -> str:
    return render_sse_events(events)


def _partial_event(raw_event: dict[str, Any]) -> dict[str, Any]:
    segment_id = str(raw_event["segment_id"])
    return {
        "id": f"transcript_partial:{segment_id}",
        "event_type": "transcript_partial",
        "at_ms": int(raw_event.get("received_at_ms", raw_event.get("end_ms", 0))),
        "payload": {
            "segment_id": segment_id,
            "start_ms": int(raw_event.get("start_ms", 0)),
            "end_ms": int(raw_event.get("end_ms", 0)),
            "text": str(raw_event.get("text", "")),
            "confidence": raw_event.get("confidence"),
            "is_final": False,
        },
    }


def _final_event(raw_event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    segment_id = str(raw_event["segment_id"])
    evidence = _active_evidence(raw_event)
    return (
        {
            "id": f"transcript_final:{segment_id}",
            "event_type": "transcript_final",
            "at_ms": int(raw_event.get("received_at_ms", raw_event.get("end_ms", 0))),
            "payload": _final_payload(raw_event, evidence_spans=[evidence]),
        },
        evidence,
    )


def _revision_event(
    raw_event: dict[str, Any],
    evidence_by_segment_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    segment_id = str(raw_event["segment_id"])
    revision_of = str(raw_event.get("revision_of") or "")
    if not revision_of:
        raise ValueError(f"{segment_id} revision missing revision_of")
    previous_evidence = evidence_by_segment_id.get(revision_of)
    evidence = _active_evidence(raw_event)
    payload = _final_payload(raw_event, evidence_spans=[evidence])
    payload["revision_of"] = revision_of
    payload["supersedes_segment_id"] = revision_of
    if previous_evidence:
        evidence["revision_of"] = str(previous_evidence["id"])
        payload["evidence_spans"] = [evidence]
        superseded = dict(previous_evidence)
        superseded["status"] = "superseded"
        superseded["replaced_by"] = evidence["id"]
        payload["superseded_evidence_spans"] = [superseded]
    else:
        payload["superseded_evidence_spans"] = []
    return (
        {
            "id": f"transcript_revision:{segment_id}",
            "event_type": "transcript_revision",
            "at_ms": int(raw_event.get("received_at_ms", raw_event.get("end_ms", 0))),
            "payload": payload,
        },
        evidence,
    )


def _provider_error_event(raw_event: dict[str, Any], provider: str) -> dict[str, Any]:
    error_id = str(raw_event["segment_id"])
    return {
        "id": f"provider_error:{error_id}",
        "event_type": "provider_error",
        "at_ms": int(raw_event.get("received_at_ms", raw_event.get("end_ms", 0))),
        "payload": {
            "provider": provider,
            "error_id": error_id,
            "message": str(raw_event.get("text", "")),
            "start_ms": int(raw_event.get("start_ms", 0)),
            "end_ms": int(raw_event.get("end_ms", 0)),
        },
    }


def _evaluation_event(
    raw_event: dict[str, Any],
    provider: str,
    counts: dict[str, int],
    *,
    is_mock: bool,
) -> dict[str, Any]:
    return {
        "id": "evaluation:asr_stream_summary",
        "event_type": "evaluation_summary",
        "at_ms": int(raw_event.get("received_at_ms", raw_event.get("end_ms", 0))),
        "payload": {
            "source": ASR_LIVE_SOURCE,
            "provider": provider,
            "is_mock": is_mock,
            "passes_minimum_gate": counts["final_event_count"] > 0,
            **counts,
        },
    }


def _final_payload(
    raw_event: dict[str, Any],
    *,
    evidence_spans: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "segment_id": str(raw_event["segment_id"]),
        "start_ms": int(raw_event.get("start_ms", 0)),
        "end_ms": int(raw_event.get("end_ms", 0)),
        "text": str(raw_event.get("text", "")),
        "confidence": raw_event.get("confidence"),
        "is_final": True,
        "evidence_spans": evidence_spans,
    }


def _active_evidence(raw_event: dict[str, Any]) -> dict[str, Any]:
    segment_id = str(raw_event["segment_id"])
    return {
        "id": f"asr_ev_{segment_id}",
        "segment_id": segment_id,
        "start_ms": int(raw_event.get("start_ms", 0)),
        "end_ms": int(raw_event.get("end_ms", 0)),
        "quote": str(raw_event.get("text", "")),
        "status": "active",
    }


def _local_state_scheduler_events(
    raw_event: dict[str, Any],
    evidence: dict[str, Any],
    transcript_event_at_ms: int,
    scheduler_state: "_SchedulerState",
) -> tuple[list[dict[str, Any]], "_SchedulerState"]:
    text = str(raw_event.get("text", ""))
    segment_id = str(raw_event["segment_id"])
    evidence_id = str(evidence["id"])
    state_specs = _extract_local_state_specs(text, segment_id, evidence_id)
    if not state_specs:
        return [], scheduler_state

    events: list[dict[str, Any]] = []
    current_scheduler_state = scheduler_state
    for state_index, state_spec in enumerate(state_specs):
        state_event_id = str(state_spec["state_event_id"])
        scheduler_decision = _decide_scheduler_event(
            received_at_ms=transcript_event_at_ms,
            has_state_change=True,
            state=current_scheduler_state,
        )
        state_event = {
            "id": f"state:{state_event_id}",
            "event_type": "state_event",
            "at_ms": transcript_event_at_ms,
            "_sort_step": state_index * 4,
            "payload": {
                "event_id": state_event_id,
                "target_type": state_spec["target_type"],
                "target_id": state_spec["target_id"],
                "state_event_type": "created",
                "evidence_span_ids": [evidence_id],
                "state_item": state_spec["state_item"],
            },
        }
        scheduler_event = {
            "id": f"scheduler:{state_event_id}",
            "event_type": "scheduler_event",
            "at_ms": transcript_event_at_ms,
            "_sort_step": state_index * 4 + 1,
            "payload": {
                "scheduler_event_type": scheduler_decision.event_type,
                "card_id": "",
                "gap_rule_id": "asr.state_candidate.review",
                "trigger_source": "live_asr_scheduler_log",
                "trigger_reason": scheduler_decision.reason,
                "decision_reason": scheduler_decision.reason,
                "would_call_llm": scheduler_decision.would_call_llm,
                "llm_call_status": "not_called",
                "cooldown_remaining_ms": scheduler_decision.cooldown_remaining_ms,
                "call_count_last_hour": scheduler_decision.call_count_last_hour,
                "budget_remaining": scheduler_decision.budget_remaining,
                "segment_batch": [segment_id],
                "source_event_ids": [state_event_id],
                "prompt_version": "not-called",
                "model": "not-called",
            },
        }
        candidate_payload = _suggestion_candidate_payload(
            state_spec=state_spec,
            scheduler_decision=scheduler_decision,
            segment_id=segment_id,
            evidence_id=evidence_id,
            evidence_quote=str(evidence.get("quote", "")),
            asr_confidence=raw_event.get("confidence"),
        )
        suggestion_candidate_event = {
            "id": f"suggestion_candidate:{state_event_id}",
            "event_type": "suggestion_candidate_event",
            "at_ms": transcript_event_at_ms,
            "_sort_step": state_index * 4 + 2,
            "payload": candidate_payload,
        }
        llm_request_draft_event = {
            "id": f"llm_request_draft:{state_event_id}",
            "event_type": "llm_request_draft_event",
            "at_ms": transcript_event_at_ms,
            "_sort_step": state_index * 4 + 3,
            "payload": _llm_request_draft_payload(candidate_payload),
        }
        events.extend([
            state_event,
            scheduler_event,
            suggestion_candidate_event,
            llm_request_draft_event,
        ])
        current_scheduler_state = scheduler_decision.state_after
    return events, current_scheduler_state


def _suggestion_candidate_payload(
    *,
    state_spec: dict[str, Any],
    scheduler_decision: "_SchedulerDecision",
    segment_id: str,
    evidence_id: str,
    evidence_quote: str,
    asr_confidence: Any,
) -> dict[str, Any]:
    rule = _suggestion_candidate_rule(str(state_spec["target_type"]))
    state_event_id = str(state_spec["state_event_id"])
    quality = _suggestion_candidate_quality(
        state_spec=state_spec,
        scheduler_decision=scheduler_decision,
        evidence_quote=evidence_quote,
        asr_confidence=asr_confidence,
    )
    return {
        "candidate_id": f"asr_suggestion_candidate_{state_event_id}",
        "candidate_type": "state_gap_review",
        "candidate_policy_version": CANDIDATE_POLICY_VERSION,
        "confidence_source": "local_deterministic_heuristic",
        "target_type": state_spec["target_type"],
        "target_id": state_spec["target_id"],
        "gap_rule_id": rule["gap_rule_id"],
        "suggested_prompt": rule["suggested_prompt"],
        "trigger_reason": rule["trigger_reason"],
        "decision_reason": scheduler_decision.reason,
        "source_event_ids": [state_event_id],
        "scheduler_event_type": scheduler_decision.event_type,
        "evidence_span_ids": [evidence_id],
        "segment_batch": [segment_id],
        "llm_call_status": "not_called",
        "card_status": "not_created",
        "confidence": quality["confidence"],
        "confidence_level": quality["confidence_level"],
        "degradation_reasons": quality["degradation_reasons"],
        "source": ASR_LIVE_SOURCE,
        "candidate_origin": "local_deterministic_asr_skeleton",
    }


def _suggestion_candidate_quality(
    *,
    state_spec: dict[str, Any],
    scheduler_decision: "_SchedulerDecision",
    evidence_quote: str,
    asr_confidence: Any,
) -> dict[str, Any]:
    degradation_reasons = _candidate_degradation_reasons(
        state_spec=state_spec,
        scheduler_decision=scheduler_decision,
        evidence_quote=evidence_quote,
        asr_confidence=asr_confidence,
    )
    score = 0.90
    penalties = {
        "low_asr_confidence": 0.20,
        "missing_asr_confidence": 0.15,
        "evidence_text_short": 0.15,
        "action_owner_missing": 0.10,
        "action_deadline_missing": 0.10,
        "risk_mitigation_missing": 0.10,
    }
    for reason in degradation_reasons:
        score -= penalties[reason]
    confidence = round(min(max(score, 0.10), 0.99), 2)
    return {
        "confidence": confidence,
        "confidence_level": _confidence_level(confidence),
        "degradation_reasons": degradation_reasons,
    }


def _llm_request_draft_payload(candidate_payload: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(candidate_payload["candidate_id"])
    target_type = str(candidate_payload["target_type"])
    target_id = str(candidate_payload["target_id"])
    evidence_span_ids = [str(item) for item in candidate_payload.get("evidence_span_ids") or []]
    segment_batch = [str(item) for item in candidate_payload.get("segment_batch") or []]
    return {
        "request_id": f"asr_llm_request_draft_{candidate_id}",
        "request_type": "llm_suggestion_card_draft",
        "request_status": "draft_only",
        "target_candidate_id": candidate_id,
        "target_type": target_type,
        "target_id": target_id,
        "gap_rule_id": candidate_payload["gap_rule_id"],
        "prompt_version": "not-called",
        "model": "not-called",
        "llm_call_status": "not_called",
        "card_status": "not_created",
        "schema_status": "not_generated",
        "suggested_prompt": candidate_payload["suggested_prompt"],
        "input_summary": (
            f"{target_type} {target_id} from {', '.join(segment_batch)} "
            f"using {', '.join(evidence_span_ids)}"
        ),
        "source_event_ids": list(candidate_payload.get("source_event_ids") or []),
        "evidence_span_ids": evidence_span_ids,
        "segment_batch": segment_batch,
        "candidate_confidence": candidate_payload.get("confidence"),
        "candidate_confidence_level": candidate_payload.get("confidence_level"),
        "candidate_degradation_reasons": list(
            candidate_payload.get("degradation_reasons") or []
        ),
        "request_origin": "local_deterministic_asr_request_draft",
        "source": ASR_LIVE_SOURCE,
    }


def _candidate_degradation_reasons(
    *,
    state_spec: dict[str, Any],
    scheduler_decision: "_SchedulerDecision",
    evidence_quote: str,
    asr_confidence: Any,
) -> list[str]:
    reasons: list[str] = []
    del scheduler_decision
    if asr_confidence is None:
        reasons.append("missing_asr_confidence")
    elif _is_low_asr_confidence(asr_confidence):
        reasons.append("low_asr_confidence")
    if len("".join(evidence_quote.split())) < SHORT_EVIDENCE_TEXT_LENGTH:
        reasons.append("evidence_text_short")

    target_type = str(state_spec["target_type"])
    state_item = state_spec.get("state_item", {})
    if target_type == "ActionItem":
        if not state_item.get("owner"):
            reasons.append("action_owner_missing")
        if not state_item.get("deadline"):
            reasons.append("action_deadline_missing")
    if target_type == "Risk" and not state_item.get("mitigation"):
        reasons.append("risk_mitigation_missing")
    return reasons


def _is_low_asr_confidence(asr_confidence: Any) -> bool:
    if asr_confidence is None:
        return False
    try:
        return float(asr_confidence) < LOW_ASR_CONFIDENCE_THRESHOLD
    except (TypeError, ValueError):
        return False


def _confidence_level(confidence: float) -> str:
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def _suggestion_candidate_rule(target_type: str) -> dict[str, str]:
    if target_type == "DecisionCandidate":
        return {
            "gap_rule_id": "release.rollback.owner.required",
            "suggested_prompt": "确认决策是否包含 owner、回滚条件和监控口径。",
            "trigger_reason": "Live ASR captured a decision candidate that may need release-readiness review.",
        }
    if target_type == "OpenQuestion":
        return {
            "gap_rule_id": "open.question.followup",
            "suggested_prompt": "确认未闭环问题是否需要现场追问或记录 owner。",
            "trigger_reason": "Live ASR captured an open question that may need follow-up.",
        }
    if target_type == "Risk":
        return {
            "gap_rule_id": "risk.rollback.validation",
            "suggested_prompt": "确认风险触发条件、回滚动作和监控指标是否明确。",
            "trigger_reason": "Live ASR captured a risk candidate that may need mitigation validation.",
        }
    if target_type == "ActionItem":
        return {
            "gap_rule_id": "action.owner.deadline.confirmation",
            "suggested_prompt": "确认行动项 owner、deadline 和验收口径是否完整。",
            "trigger_reason": "Live ASR captured an action item that may need owner/deadline confirmation.",
        }
    return {
        "gap_rule_id": "state.candidate.review",
        "suggested_prompt": "确认该状态候选是否需要补充上下文。",
        "trigger_reason": "Live ASR captured a state candidate that may need review.",
    }


def _extract_local_state_specs(
    text: str,
    segment_id: str,
    evidence_id: str,
) -> list[dict[str, Any]]:
    state_specs: list[dict[str, Any]] = []
    if "灰度" in text:
        state_specs.append(
            {
                "state_event_id": f"asr_state_event_{segment_id}",
                "target_type": "DecisionCandidate",
                "target_id": f"asr_decision_{segment_id}",
                "state_item": {
                    "id": f"asr_decision_{segment_id}",
                    "statement": text,
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    if _looks_like_open_question(text):
        state_specs.append(
            {
                "state_event_id": f"asr_question_event_{segment_id}",
                "target_type": "OpenQuestion",
                "target_id": f"asr_question_{segment_id}",
                "state_item": {
                    "id": f"asr_question_{segment_id}",
                    "question": text,
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    if _looks_like_action_item(text):
        state_specs.append(
            {
                "state_event_id": f"asr_action_event_{segment_id}",
                "target_type": "ActionItem",
                "target_id": f"asr_action_{segment_id}",
                "state_item": {
                    "id": f"asr_action_{segment_id}",
                    "description": text,
                    "owner": _extract_action_owner(text),
                    "deadline": _extract_action_deadline(text),
                    "status": "candidate",
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    if _looks_like_risk(text):
        state_specs.append(
            {
                "state_event_id": f"asr_risk_event_{segment_id}",
                "target_type": "Risk",
                "target_id": f"asr_risk_{segment_id}",
                "state_item": {
                    "id": f"asr_risk_{segment_id}",
                    "description": text,
                    "impact": _extract_risk_impact(text),
                    "mitigation": _extract_risk_mitigation(text),
                    "status": "open",
                    "evidence_span_ids": [evidence_id],
                    "source": ASR_LIVE_SOURCE,
                    "state_origin": "local_deterministic_asr_skeleton",
                },
            }
        )
    return state_specs


def _looks_like_open_question(text: str) -> bool:
    return _has_engineering_context(text) and (
        any(marker in text for marker in QUESTION_MARKERS)
        or any(marker in text for marker in UNRESOLVED_QUESTION_MARKERS)
    )


def _has_engineering_context(text: str) -> bool:
    return any(marker in text for marker in ENGINEERING_CONTEXT_MARKERS)


def _looks_like_action_item(text: str) -> bool:
    if not _has_engineering_context(text):
        return False
    if _looks_like_open_question(text):
        return False
    if not _has_action_marker(text):
        return False
    if _extract_action_owner(text):
        return True
    if _extract_action_deadline(text) and any(
        marker in text for marker in ASSIGNMENT_CUE_ACTION_MARKERS
    ):
        return True
    return any(cue in text for cue in ACTION_ASSIGNMENT_CUES) and any(
        marker in text for marker in ASSIGNMENT_CUE_ACTION_MARKERS
    )


def _has_action_marker(text: str) -> bool:
    return ACTION_MARKER_PATTERN.search(text) is not None


def _extract_action_owner(text: str) -> str | None:
    match = ACTION_MARKER_PATTERN.search(text)
    if not match:
        return None

    before_action = text[: match.start()].rstrip()
    before_action = DEADLINE_AT_END_PATTERN.sub("", before_action).rstrip()
    before_action = before_action.rstrip("，,。；;：: ")
    for cue in ACTION_ASSIGNMENT_CUES:
        if before_action.endswith(cue):
            before_action = before_action[: -len(cue)].rstrip()

    owner_match = OWNER_AT_END_PATTERN.search(before_action)
    if not owner_match:
        return None
    owner = owner_match.group(1)
    if any(marker in owner for marker in ACTION_OWNER_REJECT_MARKERS):
        return None
    owner_start = owner_match.start(1)
    if owner_start > 0 and not (
        before_action[owner_start - 1] in "，,。；;：: "
        or any(
            before_action[:owner_start].endswith(cue)
            for cue in ACTION_ASSIGNMENT_CUES
        )
    ):
        return None
    return owner


def _extract_action_deadline(text: str) -> str | None:
    match = DEADLINE_PATTERN.search(text)
    if not match:
        return None
    return match.group(1)


def _looks_like_risk(text: str) -> bool:
    if any(marker in text for marker in NEGATED_RISK_MARKERS):
        return False
    if _looks_like_architecture_risk(text):
        return True
    if "如果" in text and "超过" in text:
        return True
    return "风险" in text or ("超过" in text and any(marker in text for marker in ("错误率", "P99", "延迟", "失败")))


def _looks_like_architecture_risk(text: str) -> bool:
    return (
        _has_engineering_context(text)
        and any(marker in text for marker in ARCHITECTURE_RISK_MARKERS)
        and any(marker in text for marker in ARCHITECTURE_RISK_UNCERTAINTY_MARKERS)
    )


def _extract_risk_impact(text: str) -> str:
    if "超过" in text:
        return "condition_exceeded"
    if "风险" in text:
        return "risk_detected"
    if _looks_like_architecture_risk(text):
        return "runtime_issue"
    if any(marker in text for marker in RISK_MARKERS):
        return "runtime_issue"
    return ""


def _extract_risk_mitigation(text: str) -> str:
    if "回滚" in text:
        return "回滚"
    return ""


@dataclass(frozen=True)
class _SchedulerState:
    call_timestamps_ms: tuple[int, ...] = ()


@dataclass(frozen=True)
class _SchedulerDecision:
    event_type: str
    reason: str
    would_call_llm: bool
    state_after: _SchedulerState
    cooldown_remaining_ms: int
    call_count_last_hour: int
    budget_remaining: int


def _decide_scheduler_event(
    *,
    received_at_ms: int,
    has_state_change: bool,
    state: _SchedulerState,
) -> _SchedulerDecision:
    active_calls = _calls_in_last_hour(state.call_timestamps_ms, received_at_ms)
    if len(active_calls) >= DEFAULT_MAX_CALLS_PER_HOUR:
        return _SchedulerDecision(
            event_type="llm_candidate_skipped",
            reason="budget_exhausted",
            would_call_llm=False,
            state_after=_SchedulerState(call_timestamps_ms=active_calls),
            cooldown_remaining_ms=0,
            call_count_last_hour=len(active_calls),
            budget_remaining=0,
        )

    interval_ms = (
        DEFAULT_MIN_STATE_CHANGE_INTERVAL_MS
        if has_state_change
        else DEFAULT_MIN_FINAL_INTERVAL_MS
    )
    last_call_ms = active_calls[-1] if active_calls else None
    if last_call_ms is not None:
        elapsed_ms = received_at_ms - last_call_ms
        if elapsed_ms < interval_ms:
            return _SchedulerDecision(
                event_type="llm_candidate_skipped",
                reason="cooldown",
                would_call_llm=False,
                state_after=_SchedulerState(call_timestamps_ms=active_calls),
                cooldown_remaining_ms=interval_ms - elapsed_ms,
                call_count_last_hour=len(active_calls),
                budget_remaining=DEFAULT_MAX_CALLS_PER_HOUR - len(active_calls),
            )

    updated_calls = (*active_calls, received_at_ms)
    return _SchedulerDecision(
        event_type="llm_candidate_queued",
        reason="state_change" if has_state_change else "final_segment",
        would_call_llm=True,
        state_after=_SchedulerState(call_timestamps_ms=updated_calls),
        cooldown_remaining_ms=0,
        call_count_last_hour=len(updated_calls),
        budget_remaining=DEFAULT_MAX_CALLS_PER_HOUR - len(updated_calls),
    )


def _calls_in_last_hour(call_timestamps_ms: tuple[int, ...], now_ms: int) -> tuple[int, ...]:
    window_start_ms = now_ms - 3_600_000
    return tuple(timestamp for timestamp in call_timestamps_ms if timestamp >= window_start_ms)


def _with_sort_group(event: dict[str, Any], group_index: int) -> dict[str, Any]:
    return {**event, "_sort_group": group_index}


def _public_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in event.items()
        if not key.startswith("_sort_")
    }


def _event_sort_key(event: dict[str, Any]) -> tuple[int, int, int, int, str, str]:
    event_type = str(event["event_type"])
    return (
        int(event["at_ms"]),
        int(event.get("_sort_group", 0)),
        int(event.get("_sort_step", 0)),
        EVENT_ORDER.get(event_type, 999),
        event_type,
        str(event["id"]),
    )

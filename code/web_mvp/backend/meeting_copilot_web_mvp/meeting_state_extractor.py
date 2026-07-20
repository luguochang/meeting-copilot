from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import re
from typing import Any


_SENTENCE_RE = re.compile(r"[^。！？?!；;\n]+[。！？?!；;]?")
_SPEAKER_PREFIX_RE = re.compile(
    r"^(?:[A-Za-z0-9_\-\u4e00-\u9fff]{1,12})(?:说)?[：:]\s*"
)
_PUNCTUATION_RE = re.compile(r"[\s，。！？?!；;、：:,.'\"“”‘’（）()【】\[\]<>《》]+")
_QUESTION_PUNCTUATION_RE = re.compile(r"[？?]\s*$")

_QUESTION_TRIGGER_RE = re.compile(
    r"(?:^|[，,。；;\s])(?:请问|想确认|确认一下|是否|能否|为什么|为何|怎么|如何|谁|"
    r"哪里|哪种|哪个|多少|几天|几次|什么时候|何时|是不是|要不要|能不能|可不可以|"
    r"有没有|有无|啥|什么原因)"
)
_QUESTION_WORD_ANYWHERE_RE = re.compile(
    r"是否|能否|为什么|为何|怎么|如何|谁(?:来|去|负责)?|哪里|哪种|哪个|多少|"
    r"几天|几次|什么时候|何时|是不是|要不要|能不能|可不可以|有没有|有无|"
    r"什么原因"
)
_EXPLANATORY_FRAME_RE = re.compile(
    r"(?:说明|解释|阐述)(?:一下)?(?:为什么|为何)|(?:介绍|展示)(?:一下)?(?:如何|怎么)"
)
_UNRESOLVED_RE = re.compile(
    r"(?:还没|尚未|仍未|暂未)(?:确定|确认|明确)|待确认|需要确认|还不清楚|仍不清楚|"
    r"没有结论|尚无结论|暂时未定|还没定"
)
_UNCERTAINTY_RE = re.compile(
    r"可能|也许|或许|大概|应该|不确定|还要(?:再)?确认|需要(?:再)?确认|待确认|"
    r"还得看|要看情况|暂时不知道|说不准|尚未明确|还没有结论|先不定"
)
_CLEAR_ANSWER_RE = re.compile(
    r"确定为|定为|结论是|答案是|已确认|已经确认|明确为|设置为|设为|采用|"
    r"由.{1,20}负责|时间定在|计划(?:在|于)|支持|不支持|可以|不可以|"
    r"能够|不能|会在|不会|就是"
)
_DIRECT_ANSWER_RE = re.compile(r"^(?:是的|不是|对|不对|可以|不可以|支持|不支持)[，,。；;\s]")
_ANAPHORIC_ANSWER_RE = re.compile(r"^(?:这个问题|刚才的问题)?(?:的)?(?:答案|结论)(?:是|为)")

# Formal facts require explicit commitment or an observed risk. Tentative
# planning language is deliberately excluded from these patterns.
_DECISION_RE = re.compile(
    r"(?:结论是|决定(?:采用|使用|选择|改为|切换到)?|最终(?:采用|使用|选择)|"
    r"确定为|已确认|已经确认|明确为|定为|拍板|同意(?:采用|使用|选择)?|"
    r"通过(?:了)?|正式采用|正式使用|采用)"
)
_DECISION_WEAK_RE = re.compile(r"(?:可能|也许|或许|大概|考虑|倾向于|建议|可以考虑|暂定)")
_ACTION_RE = re.compile(
    r"(?:负责|完成|处理|跟进|落实|提交|验证|检查|修复|切换|迁移|上线|回滚)"
)
_ACTION_OWNER_RE = re.compile(
    r"(?:由|请|让)\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,20}?)\s*"
    r"(?:负责|完成|处理|跟进|落实|提交|验证|检查|修复|切换|迁移|上线|回滚)"
    r"|^([A-Za-z0-9_\-\u4e00-\u9fff]{1,20}?)\s*(?:负责|将|要)"
)
_ACTION_COMMITMENT_RE = re.compile(
    r"(?:负责|需要在|必须在|应在|请|安排|计划在|截止|最晚|落实|完成|跟进)"
)
_DEADLINE_RE = re.compile(
    r"(?:在|于|截止(?:到|于)?|最晚(?:在|于)?|不晚于)\s*"
    r"([^，。！？?!；;\s]+?)(?:之前|以前|前|完成|结束|上线|提交|落地)"
    r"|(?:截止|最晚)\s*([^，。！？?!；;\s]+)"
)
_RISK_RE = re.compile(
    r"(?:风险是|风险为|存在[^，。！？?!；;]{0,24}风险|有[^，。！？?!；;]{0,24}风险|"
    r"会导致|可能导致|担心(?:会)?|隐患是|回滚失败|数据不一致)"
)
_RISK_DISCUSSION_RE = re.compile(r"(?:讨论|看看|评估|分析)(?:一下)?[^。！？?!；;]*(?:风险|隐患)")
_MITIGATION_RE = re.compile(r"(?:需要|应当|应该|建议|可通过|通过|先)\s*([^。！？?!；;]+)")

_FILLER_VALUES = {
    "嗯",
    "嗯嗯",
    "好的",
    "好",
    "行",
    "可以",
    "收到",
    "知道了",
    "明白",
    "明白了",
    "没问题",
    "对",
    "是的",
    "那就这样",
    "继续",
    "下一个",
    "嗯好的",
}
_MEETING_BOILERPLATE_RE = re.compile(
    r"^(?:大家|你们|各位)?(?:能|可以)?(?:听见|听到|看到)(?:我|声音|屏幕|画面)?(?:吗|吧)?$|"
    r"^(?:开始|结束)(?:开会|会议)?$"
)

QUESTION_CARRY_OVER_AFTER_MS = 5 * 60_000
QUESTION_EXPIRE_AFTER_MS = 15 * 60_000
TOPIC_EXPIRE_AFTER_MS = 5 * 60_000

_LEADING_QUESTION_NOISE_RE = re.compile(
    r"^(?:那|所以|然后|另外|我再|再|我想|想|麻烦|请|我们)?(?:确认|问)(?:一下)?[，,:：]?"
)
_QUESTION_SYNONYMS = (
    ("能不能", "能否"),
    ("可不可以", "能否"),
    ("是不是", "是否"),
    ("有没有", "是否有"),
    ("怎么", "如何"),
    ("啥", "什么"),
)
_SEMANTIC_STOP_WORDS = (
    "请问",
    "确认一下",
    "我想确认",
    "具体",
    "到底",
    "现在",
    "目前",
    "这次",
    "我们",
    "是否",
    "能否",
    "为什么",
    "为何",
    "如何",
    "怎么",
    "什么时间",
    "什么时候",
    "何时",
    "多少",
    "哪个",
    "哪种",
    "哪里",
    "什么",
    "是不是",
    "要不要",
    "有没有",
    "有无",
    "可不可以",
    "能不能",
    "由谁",
    "谁",
    "吗",
    "呢",
    "的",
)


def extract_meeting_state(
    segments: Sequence[Mapping[str, Any]],
    *,
    previous_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project topic and open questions from ordered canonical transcript segments.

    Passing ``previous_state`` makes the same pure function usable incrementally: the
    caller supplies only newly committed canonical segments. With no previous state,
    replaying all committed segments produces the complete projection.
    """

    topic, questions, decision_candidates, action_items, risks = _load_previous_state(previous_state)

    for raw_segment in segments:
        segment_id, text, updated_at_ms = _canonical_segment(raw_segment)
        segment_context = _segment_context(raw_segment, segment_id, text)
        topic, questions = _advance_lifecycle(
            topic,
            questions,
            observed_at_ms=updated_at_ms,
        )
        for sentence in _sentences(text):
            if _is_boilerplate(sentence):
                continue

            uncertain = _is_uncertain(sentence)
            matching_question = _best_matching_question(sentence, questions, only_open=True)

            if uncertain and matching_question is not None:
                # An uncertain response is not new evidence that the question is answered.
                topic = _topic_projection(
                    sentence,
                    segment_id,
                    updated_at_ms,
                    context=segment_context,
                )
                continue

            if _looks_like_question(sentence):
                questions = _record_question(
                    questions,
                    sentence=sentence,
                    segment_id=segment_id,
                    updated_at_ms=updated_at_ms,
                    context=segment_context,
                )
            else:
                questions = _apply_clear_answer(
                    questions,
                    sentence=sentence,
                    segment_id=segment_id,
                    updated_at_ms=updated_at_ms,
                    context=segment_context,
                )

            if _is_meaningful(sentence):
                topic = _topic_projection(
                    sentence,
                    segment_id,
                    updated_at_ms,
                    context=segment_context,
                )

            decision = _decision_candidate(sentence, segment_context)
            if decision is not None:
                decision_candidates = _merge_fact(
                    decision_candidates,
                    decision,
                    fact_type="decision_candidate",
                )
            action = _action_item(sentence, segment_context)
            if action is not None:
                action_items = _merge_fact(action_items, action, fact_type="action_item")
            risk = _risk(sentence, segment_context)
            if risk is not None:
                risks = _merge_fact(risks, risk, fact_type="risk")

    state = {
        "current_topic": deepcopy(topic),
        "open_questions": deepcopy(questions[-3:]),
    }
    # Existing callers rely on the historical two-key shape for ordinary
    # transcripts. Persistence expands these to empty arrays in its snapshot.
    if decision_candidates or action_items or risks:
        state.update(
            {
                "decision_candidates": deepcopy(decision_candidates),
                "action_items": deepcopy(action_items),
                "risks": deepcopy(risks),
            }
        )
    return state


def _advance_lifecycle(
    topic: dict[str, Any] | None,
    questions: list[dict[str, Any]],
    *,
    observed_at_ms: int | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if observed_at_ms is None:
        return topic, questions

    if topic is not None:
        topic_updated_at = topic.get("updated_at_ms")
        if (
            isinstance(topic_updated_at, int)
            and not isinstance(topic_updated_at, bool)
            and observed_at_ms >= topic_updated_at + TOPIC_EXPIRE_AFTER_MS
        ):
            topic["status"] = "expired"

    for question in questions:
        question_updated_at = question.get("updated_at_ms")
        if not isinstance(question_updated_at, int) or isinstance(question_updated_at, bool):
            continue
        age_ms = observed_at_ms - question_updated_at
        if age_ms < 0 or question.get("status") == "answered":
            continue
        if age_ms >= QUESTION_EXPIRE_AFTER_MS:
            question["status"] = "expired"
        elif age_ms >= QUESTION_CARRY_OVER_AFTER_MS and question.get("status") in {
            "open",
            "unknown",
        }:
            question["status"] = "carried_over"
    return topic, questions


def _canonical_segment(segment: Mapping[str, Any]) -> tuple[str, str, int | None]:
    if not isinstance(segment, Mapping):
        raise TypeError("each transcript segment must be a mapping")

    segment_id = str(segment.get("segment_id") or segment.get("segmentId") or "").strip()
    if not segment_id:
        raise ValueError("segment_id must not be empty")

    canonical = segment.get("normalized_text", segment.get("normalizedText"))
    if not isinstance(canonical, str) or not canonical.strip():
        canonical = segment.get("text")
    if not isinstance(canonical, str):
        raise ValueError("segment text must be a string")

    updated_at = segment.get("updated_at_ms", segment.get("updatedAtMs"))
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = segment.get("ended_at_ms", segment.get("endedAtMs"))
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = segment.get("started_at_ms", segment.get("startedAtMs"))
    if not isinstance(updated_at, int) or isinstance(updated_at, bool):
        updated_at = None

    return segment_id, canonical.strip(), updated_at


def _segment_context(
    segment: Mapping[str, Any],
    segment_id: str,
    text: str,
) -> dict[str, Any]:
    def integer(*names: str) -> int | None:
        for name in names:
            value = segment.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    return {
        "segment_id": segment_id,
        "transcript_seq": integer("transcript_seq", "transcriptSeq"),
        "start_ms": integer("start_ms", "startMs", "started_at_ms", "startedAtMs"),
        "end_ms": integer("end_ms", "endMs", "ended_at_ms", "endedAtMs"),
        "text": text,
    }


def _load_previous_state(
    previous_state: Mapping[str, Any] | None,
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    if previous_state is None:
        return None, [], [], [], []
    if not isinstance(previous_state, Mapping):
        raise TypeError("previous_state must be a mapping")

    topic_value = previous_state.get("current_topic", previous_state.get("currentTopic"))
    topic = deepcopy(topic_value) if isinstance(topic_value, Mapping) else None

    raw_questions = previous_state.get("open_questions", previous_state.get("openQuestions", []))
    if not isinstance(raw_questions, Sequence) or isinstance(raw_questions, (str, bytes)):
        raise TypeError("previous_state open_questions must be a sequence")

    questions: list[dict[str, Any]] = []
    for raw_question in raw_questions:
        if not isinstance(raw_question, Mapping):
            continue
        text = str(raw_question.get("text") or "").strip()
        if not text:
            continue
        status = str(raw_question.get("status") or "open")
        if status not in {"open", "carried_over", "answered", "expired", "unknown"}:
            status = "unknown"
        evidence = raw_question.get(
            "evidence_segment_ids",
            raw_question.get("evidenceSegmentIds", []),
        )
        questions.append(
            {
                "id": str(raw_question.get("id") or _stable_id("question", _question_key(text))),
                "text": text,
                "status": status,
                "evidence_segment_ids": _unique_strings(evidence),
                "confidence": float(raw_question.get("confidence") or 0.8),
                "evidence": dict(raw_question.get("evidence") or {})
                if isinstance(raw_question.get("evidence"), Mapping)
                else {},
                "updated_at_ms": raw_question.get(
                    "updated_at_ms",
                    raw_question.get("updatedAtMs"),
                ),
            }
        )
    return (
        topic,
        questions[-3:],
        _load_fact_list(previous_state.get("decision_candidates", previous_state.get("decisionCandidates", []))),
        _load_fact_list(previous_state.get("action_items", previous_state.get("actionItems", []))),
        _load_fact_list(previous_state.get("risks", [])),
    )


def _load_fact_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    facts: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, Mapping) or not str(raw.get("text") or "").strip():
            continue
        evidence = raw.get("evidence")
        evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
        evidence_items = raw.get("evidence_items", raw.get("evidenceItems", []))
        if not isinstance(evidence_items, Sequence) or isinstance(evidence_items, (str, bytes)):
            evidence_items = []
        evidence_items = [dict(item) for item in evidence_items if isinstance(item, Mapping)]
        if evidence and not evidence_items:
            evidence_items = [evidence]
        facts.append(
            {
                "id": str(raw.get("id") or "").strip(),
                "text": str(raw.get("text") or "").strip(),
                "status": str(raw.get("status") or "candidate"),
                "confidence": float(raw.get("confidence") or 0.0),
                "evidence": evidence,
                "evidence_items": evidence_items,
                "evidence_segment_ids": _unique_strings(raw.get("evidence_segment_ids", [])),
                "updated_at_ms": raw.get("updated_at_ms"),
                "owner": raw.get("owner"),
                "deadline": raw.get("deadline"),
                "mitigation": raw.get("mitigation"),
            }
        )
    return facts


def _evidence(context: Mapping[str, Any], quote: str) -> dict[str, Any]:
    return {
        "segment_id": context.get("segment_id"),
        "transcript_seq": context.get("transcript_seq"),
        "start_ms": context.get("start_ms"),
        "end_ms": context.get("end_ms"),
        "quote": quote,
    }


def _fact_base(
    *,
    prefix: str,
    text: str,
    confidence: float,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    evidence = _evidence(context, text)
    return {
        "id": _stable_id(prefix, _compact(text)),
        "text": text,
        "status": "candidate",
        "confidence": confidence,
        "evidence": evidence,
        "evidence_items": [evidence],
        "evidence_segment_ids": [str(context["segment_id"])],
        "updated_at_ms": context.get("end_ms"),
    }


def _decision_candidate(sentence: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    compact = _compact(sentence)
    if not _DECISION_RE.search(sentence) or _DECISION_WEAK_RE.search(compact):
        return None
    if _EXPLANATORY_FRAME_RE.search(compact) or _looks_like_question(sentence):
        return None
    return _fact_base(
        prefix="decision",
        text=sentence,
        confidence=0.9 if re.search(r"(?:结论是|决定|确定为|已确认|最终|拍板)", sentence) else 0.82,
        context=context,
    )


def _action_item(sentence: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    if not _ACTION_RE.search(sentence) or not _ACTION_COMMITMENT_RE.search(sentence):
        return None
    if _is_uncertain(sentence) or re.search(r"(?:以后|有空|可以让|看看)", sentence):
        return None
    owner_match = _ACTION_OWNER_RE.search(sentence)
    owner = next((value for value in owner_match.groups() if value), None) if owner_match else None
    if sentence.lstrip().startswith(("需要", "必须", "应当", "应该", "计划", "安排")):
        owner = None
    deadline_match = _DEADLINE_RE.search(sentence)
    deadline = next((value for value in deadline_match.groups() if value), None) if deadline_match else None
    action = _fact_base(prefix="action", text=sentence, confidence=0.88, context=context)
    action.update({"owner": owner, "deadline": deadline})
    return action


def _risk(sentence: str, context: Mapping[str, Any]) -> dict[str, Any] | None:
    if not _RISK_RE.search(sentence) or _RISK_DISCUSSION_RE.search(sentence):
        return None
    risk = _fact_base(prefix="risk", text=sentence, confidence=0.84, context=context)
    mitigation_match = _MITIGATION_RE.search(sentence)
    mitigation = mitigation_match.group(1).strip(" ，,：:") if mitigation_match else None
    risk["mitigation"] = mitigation or None
    return risk


def _merge_fact(
    facts: list[dict[str, Any]],
    candidate: dict[str, Any],
    *,
    fact_type: str,
) -> list[dict[str, Any]]:
    existing = next((fact for fact in facts if fact.get("id") == candidate.get("id")), None)
    if existing is None:
        return [*facts, candidate]

    evidence_ids = _unique_strings(
        [*(existing.get("evidence_segment_ids") or []), *(candidate.get("evidence_segment_ids") or [])]
    )
    evidence_items = list(existing.get("evidence_items") or [])
    for item in candidate.get("evidence_items") or []:
        if item not in evidence_items:
            evidence_items.append(item)
    existing.update(
        {
            "text": candidate["text"],
            "confidence": max(float(existing.get("confidence") or 0.0), float(candidate["confidence"])),
            "evidence": candidate["evidence"],
            "evidence_items": evidence_items,
            "evidence_segment_ids": evidence_ids,
            "updated_at_ms": candidate.get("updated_at_ms"),
        }
    )
    if existing.get("status") not in {"confirmed", "dismissed"}:
        existing["status"] = "candidate"
    if fact_type == "action_item":
        if existing.get("owner") is None:
            existing["owner"] = candidate.get("owner")
        if existing.get("deadline") is None:
            existing["deadline"] = candidate.get("deadline")
    if fact_type == "risk" and existing.get("mitigation") is None:
        existing["mitigation"] = candidate.get("mitigation")
    return facts


def _sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0).strip()
        sentence = _SPEAKER_PREFIX_RE.sub("", sentence, count=1).strip()
        if sentence:
            sentences.append(sentence)
    return sentences


def _compact(text: str) -> str:
    return _PUNCTUATION_RE.sub("", text).lower()


def _question_key(text: str) -> str:
    value = _compact(text)
    value = _LEADING_QUESTION_NOISE_RE.sub("", value)
    for source, replacement in _QUESTION_SYNONYMS:
        value = value.replace(source, replacement)
    return value


def _semantic_key(text: str) -> str:
    value = _question_key(text)
    for stop_word in _SEMANTIC_STOP_WORDS:
        value = value.replace(stop_word, "")
    return value


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _looks_like_question(sentence: str) -> bool:
    compact = _compact(sentence)
    if len(compact) < 2:
        return False
    if _QUESTION_PUNCTUATION_RE.search(sentence):
        return True
    if _UNRESOLVED_RE.search(compact):
        return True
    if _EXPLANATORY_FRAME_RE.search(compact):
        return False
    return bool(
        _QUESTION_TRIGGER_RE.search(sentence)
        or _QUESTION_WORD_ANYWHERE_RE.search(compact)
    )


def _is_uncertain(sentence: str) -> bool:
    return bool(_UNCERTAINTY_RE.search(_compact(sentence)))


def _is_boilerplate(sentence: str) -> bool:
    compact = _compact(sentence)
    return compact in _FILLER_VALUES or bool(_MEETING_BOILERPLATE_RE.fullmatch(compact))


def _is_meaningful(sentence: str) -> bool:
    compact = _compact(sentence)
    return len(compact) >= 5 and not _is_boilerplate(sentence)


def _topic_projection(
    sentence: str,
    segment_id: str,
    updated_at_ms: int | None,
    *,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = _evidence(context or {"segment_id": segment_id}, sentence)
    return {
        "id": _stable_id("topic", _compact(sentence)),
        "text": sentence,
        "status": "active",
        "confidence": 0.8,
        "evidence": evidence,
        "evidence_segment_ids": [segment_id],
        "updated_at_ms": updated_at_ms,
    }


def _record_question(
    questions: list[dict[str, Any]],
    *,
    sentence: str,
    segment_id: str,
    updated_at_ms: int | None,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    evidence = _evidence(context or {"segment_id": segment_id}, sentence)
    existing = _best_matching_question(sentence, questions, only_open=False, for_deduplication=True)
    if existing is not None:
        evidence = existing["evidence_segment_ids"]
        if segment_id not in evidence:
            evidence.append(segment_id)
        existing["updated_at_ms"] = updated_at_ms
        existing["status"] = "open"
        existing["confidence"] = max(float(existing.get("confidence") or 0.0), 0.8)
        existing["evidence"] = evidence
        # Move a repeated question to the end so the three-item window remains recent.
        return [question for question in questions if question is not existing] + [existing]

    question = {
        "id": _stable_id("question", _question_key(sentence)),
        "text": sentence,
        "status": "open",
        "confidence": 0.8,
        "evidence": evidence,
        "evidence_segment_ids": [segment_id],
        "updated_at_ms": updated_at_ms,
    }
    return (questions + [question])[-3:]


def _apply_clear_answer(
    questions: list[dict[str, Any]],
    *,
    sentence: str,
    segment_id: str,
    updated_at_ms: int | None,
    context: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if _is_uncertain(sentence) or not _looks_like_clear_answer(sentence):
        return questions

    matching = _best_matching_question(sentence, questions, only_open=True)
    if matching is None and (_DIRECT_ANSWER_RE.search(sentence) or _ANAPHORIC_ANSWER_RE.search(sentence)):
        matching = next(
            (
                question
                for question in reversed(questions)
                if question["status"] in {"open", "carried_over", "unknown"}
            ),
            None,
        )
    if matching is None:
        return questions

    matching["status"] = "answered"
    if segment_id not in matching["evidence_segment_ids"]:
        matching["evidence_segment_ids"].append(segment_id)
    matching["updated_at_ms"] = updated_at_ms
    matching["confidence"] = max(float(matching.get("confidence") or 0.0), 0.8)
    matching["evidence"] = _evidence(context or {"segment_id": segment_id}, sentence)
    return questions


def _looks_like_clear_answer(sentence: str) -> bool:
    compact = sentence.strip()
    return bool(
        _CLEAR_ANSWER_RE.search(compact)
        or _DIRECT_ANSWER_RE.search(compact)
        or _ANAPHORIC_ANSWER_RE.search(compact)
    )


def _best_matching_question(
    sentence: str,
    questions: list[dict[str, Any]],
    *,
    only_open: bool,
    for_deduplication: bool = False,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = 0.0
    for question in questions:
        if only_open and question["status"] not in {"open", "carried_over", "unknown"}:
            continue
        score = _text_similarity(
            sentence,
            str(question["text"]),
            semantic=not for_deduplication,
        )
        if not for_deduplication and _yes_no_predicate_matches(
            sentence,
            str(question["text"]),
        ):
            score = max(score, 0.9)
        threshold = 0.72 if for_deduplication else 0.55
        if score >= threshold and score >= best_score:
            best = question
            best_score = score
    return best


def _yes_no_predicate_matches(answer: str, question: str) -> bool:
    question_key = _question_key(question)
    answer_key = _question_key(answer)
    for marker in ("是否", "能否", "要不要", "是否有"):
        if marker not in question_key:
            continue
        predicate = question_key.split(marker, 1)[1]
        if len(predicate) >= 4 and predicate in answer_key:
            return True
    return False


def _text_similarity(left: str, right: str, *, semantic: bool) -> float:
    left_key = _semantic_key(left) if semantic else _question_key(left)
    right_key = _semantic_key(right) if semantic else _question_key(right)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return len(shorter) / len(longer)

    left_bigrams = _bigrams(left_key)
    right_bigrams = _bigrams(right_key)
    if not left_bigrams or not right_bigrams:
        common = set(left_key) & set(right_key)
        return 2 * len(common) / (len(set(left_key)) + len(set(right_key)))
    return 2 * len(left_bigrams & right_bigrams) / (len(left_bigrams) + len(right_bigrams))


def _bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _unique_strings(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item and item not in result:
            result.append(item)
    return result

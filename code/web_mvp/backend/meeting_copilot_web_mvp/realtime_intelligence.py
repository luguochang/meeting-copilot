"""Structured, evidence-bound incremental meeting intelligence.

This module deliberately contains no keyword or regular-expression semantic
fallback. Provider output either satisfies the contract and evidence barrier or
is rejected while the canonical transcript remains available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import inspect
import json
import math
import re
import time
from typing import Any, Mapping, Sequence

from meeting_copilot_web_mvp.coach_skills import (
    BASE_COACH_EVENT_TYPES,
    SCENE_COACH_EVENT_TYPES,
    coach_skill_event_types,
    coach_skill_prompt,
    normalize_coach_skill_id,
)
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar, build_pi_coach_request
from meeting_copilot_web_mvp.realtime_transcript_correction import correction_is_safe


MAX_NEW_PARAGRAPHS = 8
MAX_CONTEXT_PARAGRAPHS = 3
MAX_RETRIEVAL_PARAGRAPHS = 48
MAX_PARAGRAPH_CHARACTERS = 12_000
MAX_ROLLING_STATE_BYTES = 24_000
MAX_GLOSSARY_ITEMS = 100
MAX_GLOSSARY_ITEM_CHARACTERS = 120
MAX_REPAIR_SOURCE_CHARACTERS = 16_000
MAX_SEMANTIC_WINDOWS = 8
MAX_SEMANTIC_WINDOW_CHARACTERS = 16_000
COACH_DISCOURSE_MAX_WINDOW_MS = 90_000
COACH_DISCOURSE_MAX_GAP_MS = 5_000
COACH_DISCOURSE_MAX_PARAGRAPHS = 12
COACH_DISCOURSE_MAX_CHARACTERS = 1_600

_STATE_KINDS = frozenset({"decision", "action_item", "risk", "open_question"})
_STATE_OPERATIONS = frozenset({"add", "update", "resolve", "noop"})
_TOPIC_OPERATIONS = frozenset({"add", "update", "noop"})
_URGENCY_VALUES = frozenset({"low", "medium", "high"})
_ROLLING_STATE_KEYS = frozenset({"topic", "open_items", "summary", "version"})
_SOURCE_TRACKS = frozenset({"microphone", "system_audio", "unknown"})
_ROLE_HINTS = frozenset({"self_or_room", "remote_mix", "unknown"})
_SEMANTIC_WINDOW_STATUSES = frozenset({"active", "stable"})
_ASR_CORRECTION_STATUSES = frozenset(
    {"unknown", "pending", "processing", "no_change", "changed", "failed_preserved_original"}
)
_COACH_EVENT_TYPES = BASE_COACH_EVENT_TYPES | SCENE_COACH_EVENT_TYPES
_TRIGGER_TYPES = frozenset({"delta", "transcript_delta", "task_due", "user_request"})

_COACH_MATERIAL_FACT_TERMS = frozenset(
    {
        "p90",
        "p95",
        "p99",
        "qps",
        "sla",
        "slo",
        "安全测试",
        "并发",
        "灰度",
        "回滚",
        "延迟",
        "折扣",
        "版本号",
        "监控",
        "金额",
        "错误率",
        "预算",
        "阈值",
        "门槛",
        "压测",
        "费率",
    }
)
_COACH_PROVISIONAL_ASR_STATUSES = frozenset(
    {"unknown", "pending", "processing", "failed_preserved_original"}
)
_COACH_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:p\s*)?\d+(?:\.\d+)?\s*(?:%|％|ms|毫秒|秒|分钟|小时|天|周|人|个|次|倍|万|千)?",
    re.IGNORECASE,
)
_COACH_CHINESE_NUMBER_RE = re.compile(
    r"(?:百分之|千分之|万分之)[零〇一二两三四五六七八九十百千万亿]+"
    r"|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九十]+)?"
    r"(?:毫秒|秒|分钟|小时|天|周|人|次|倍|万|千|并发|qps)",
    re.IGNORECASE,
)
_COACH_CONTEXTUAL_CHINESE_NUMBER_RE = re.compile(
    r"(?:并发|qps|错误率|金额|预算|折扣|阈值|门槛|版本号).{0,8}?"
    r"(?P<value>[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九十]+)?)",
    re.IGNORECASE,
)
_COACH_TIME_RE = re.compile(
    r"(?:今天|明天|后天|本周(?:[一二三四五六日天])?|下周(?:[一二三四五六日天])?"
    r"|周[一二三四五六日天]|星期[一二三四五六日天])"
    r"(?:上午|中午|下午|晚上|凌晨)?(?:[一二三四五六七八九十两0-9]{1,3}点(?:半|[一二三四五六七八九十0-9]{1,3}分)?)?(?:前|后|之前|以后)?"
    r"|(?:上午|中午|下午|晚上|凌晨)[一二三四五六七八九十两0-9]{1,3}点(?:半|[一二三四五六七八九十0-9]{1,3}分)?(?:前|后|之前|以后)?"
    r"|\d{1,4}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?",
)
_COACH_ORPHAN_LATIN_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z](?=[\u3400-\u9fff])|(?<=[\u3400-\u9fff])[A-Za-z](?![A-Za-z])"
)
_COACH_OWNER_ASSIGNMENT_RES = (
    re.compile(
        r"(?:^|[，。；：,:;\s])(?:请)?(?:由|让)"
        r"([A-Za-z\u3400-\u9fff][A-Za-z0-9_.\-\u3400-\u9fff]{0,19})(?:来)?负责",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[，。；：,:;\s])"
        r"([A-Za-z\u3400-\u9fff][A-Za-z0-9_.\-\u3400-\u9fff]{0,19})(?:作为|是|担任)负责人",
        re.IGNORECASE,
    ),
    re.compile(
        r"负责人(?:是|为|定为|改为)"
        r"([A-Za-z\u3400-\u9fff][A-Za-z0-9_.\-\u3400-\u9fff]{0,19})",
        re.IGNORECASE,
    ),
)
_COACH_NEGATIVE_STATE_RE = re.compile(
    r"(?:尚未|还没有|还没|未能|没有|不能|不可以|不要|不建议|不应|不会|并非|不是|未|没|无)"
    r".{0,16}(?:完成|通过|确认|解决|关闭|交付|上线|发布|承诺|确定|定下|评估|验收|校验|测试)"
    r"|(?:尚未定|还没定|未定|没定|待定)"
)
_COACH_POSITIVE_STATE_RE = re.compile(
    r"(?:已经|已).{0,16}(?:完成|通过|确认|解决|关闭|交付|上线|发布|确定|定下|评估|验收|校验|测试)"
)
_COACH_OWNER_GAP_RE = re.compile(
    r"(?:(?:谁|哪位|哪个人|何人).{0,16}(?:负责|来(?:改|做|推进|处理)|修改|执行)"
    r"|(?:负责人|责任人).{0,10}(?:是谁|为谁|未定|没定|待定|待确认)"
    r"|由谁.{0,12}(?:负责|修改|处理|推进)?)"
)
_COACH_EXPLICIT_EXECUTION_GAP_RE = re.compile(
    r"(?:(?:尚未|还没有|还没|没有|未|没)(?:指定|明确|确认|敲定|安排)"
    r".{0,20}(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r"|(?:尚未|还没有|还没|没有|未|没)(?:指定|明确|确认|敲定|安排)?"
    r".{0,8}(?:谁|由谁)(?:来)?(?:负责|修改|改|处理|推进|执行|验收|回滚)"
    r"|(?:谁|由谁)(?:来)?(?:负责|修改|改|处理|推进|执行|验收|回滚)"
    r".{0,8}(?:尚未|还没有|还没|没有|未|没)(?:指定|明确|确认|敲定|安排)?"
    r"|(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r".{0,6}(?:尚未|还没有|还没|没有|未|没)(?:指定|明确|确认|敲定|安排)"
    r"|(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r".{0,16}(?:尚未|还没有|还没|没有)?(?:指定|明确|确认|敲定|安排)?"
    r"(?:未定|没定|待定|待明确|待确认|不明确)"
    # ASR commonly preserves the natural phrase "负责人还没有确定" rather
    # than the shorter "负责人未定" form. Keep the negation mandatory here
    # so a positive "负责人确定" statement cannot become a false candidate.
    r"|(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r".{0,6}(?:尚未|还没有|还没|没有|未|没)(?:指定|明确|确认|敲定|安排)?(?:确定|明确))"
)
_COACH_EXECUTION_GAP_RESOLVED_RE = re.compile(
    r"(?:(?:现在|目前|后来).{0,4})?(?:现已|已经|已).{0,8}(?:指定|明确|确认|敲定|安排)"
    r".{0,20}(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r"|(?:负责人|责任人|验收人|回滚人|回滚负责人|最终验收人)"
    r".{0,12}(?:(?:现在|目前|后来).{0,4})?(?:现已|已经|已)(?:指定|明确|确认|敲定|安排|为|是)"
)
_COACH_TOPIC_BOUNDARY_RE = re.compile(
    r"(?:[。！？；;!?]+|[，,]?(?:但是|不过|然而|另外|至于|但)(?=[^，,。！？；;!?]))"
)

# Coach provenance is deliberately separate from ``runtime_used``. The latter
# describes execution plumbing; these fields tell event consumers whether the
# visible card was a Pi decision, a direct-intelligence decision, a direct
# fallback, or a protected failure/silence. They are additive so existing job
# payload readers can adopt them independently.
COACH_PROVENANCE_VERSION = "realtime_coach_provenance.v1"
_COACH_ORIGINS = frozenset(
    {"pi", "direct_intelligence", "direct_fallback", "local_reflex"}
)
_COACH_DECISION_STATUSES = frozenset(
    {"intervention", "not_triggered", "protected_silent", "timed_out", "failed", "stale"}
)
COACH_TRIGGER_EVENT_TYPES = frozenset(
    {
        "question_pending",
        "objection_detected",
        "commitment_without_condition",
        "goal_at_risk",
        "monologue_duration",
        "repetition",
        "topic_drift",
        "missing_next_step",
    }
)
_COACH_TRIGGER_PRIORITIES = {
    "question_pending": 100,
    "commitment_without_condition": 95,
    "objection_detected": 90,
    "goal_at_risk": 85,
    "missing_next_step": 80,
    "topic_drift": 75,
    "repetition": 70,
    "monologue_duration": 65,
}


class IntelligenceResponseValidationError(ValueError):
    """A model response failed schema, version, or evidence validation."""

    retryable = False

    def __init__(self, message: str, *, category: str = "structural") -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class CoachCandidateEvent:
    """One deterministic, evidence-bound reason to ask the coach for a decision."""

    event_type: str
    evidence_segment_ids: tuple[str, ...]
    reason: str
    candidate_key: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "evidence_segment_ids": list(self.evidence_segment_ids),
            "reason": self.reason,
            "candidate_key": self.candidate_key,
        }


@dataclass(frozen=True)
class IntelligenceParagraph:
    id: str
    text: str
    revision: int
    start_ms: int | None
    end_ms: int | None
    speaker: str | None
    speaker_confidence: float | None
    source_track: str
    role_hint: str
    correction_status: str

    @classmethod
    def from_payload(cls, value: Mapping[str, Any], *, field: str) -> "IntelligenceParagraph":
        if not isinstance(value, Mapping):
            raise ValueError(f"{field} must be an object")
        paragraph_id = _required_text(value.get("id"), f"{field}.id", maximum=200)
        text = _required_text(
            value.get("text"),
            f"{field}.text",
            maximum=MAX_PARAGRAPH_CHARACTERS,
        )
        revision = _positive_integer(value.get("revision", 1), f"{field}.revision")
        start_ms = _optional_non_negative_integer(value.get("start_ms"), f"{field}.start_ms")
        end_ms = _optional_non_negative_integer(value.get("end_ms"), f"{field}.end_ms")
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise ValueError(f"{field}.end_ms must not precede start_ms")
        speaker = _optional_text(value.get("speaker"), maximum=120)
        speaker_confidence = _optional_confidence(
            value.get("speaker_confidence"),
            f"{field}.speaker_confidence",
        )
        source_track = _optional_text(value.get("source_track"), maximum=40) or "unknown"
        if source_track not in _SOURCE_TRACKS:
            raise ValueError(f"{field}.source_track is unsupported")
        role_hint = _optional_text(value.get("role_hint"), maximum=40) or _role_hint_for_source_track(source_track)
        if role_hint not in _ROLE_HINTS:
            raise ValueError(f"{field}.role_hint is unsupported")
        correction_status = _optional_text(value.get("correction_status"), maximum=40) or "unknown"
        if correction_status not in _ASR_CORRECTION_STATUSES:
            raise ValueError(f"{field}.correction_status is unsupported")
        return cls(
            id=paragraph_id,
            text=text,
            revision=revision,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker=speaker,
            speaker_confidence=speaker_confidence,
            source_track=source_track,
            role_hint=role_hint,
            correction_status=correction_status,
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "revision": self.revision,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker": self.speaker,
            "speaker_confidence": self.speaker_confidence,
            "source_track": self.source_track,
            "role_hint": self.role_hint,
            "correction_status": self.correction_status,
            "evidence_quality": _coach_evidence_quality(self),
        }


@dataclass(frozen=True)
class IntelligenceSemanticWindow:
    id: str
    text: str
    revision: int
    start_ms: int | None
    end_ms: int | None
    status: str
    segment_ids: tuple[str, ...]
    source_tracks: tuple[str, ...]
    role_hints: tuple[str, ...]

    @classmethod
    def from_payload(
        cls,
        value: Mapping[str, Any],
        *,
        field: str,
        allowed_segment_ids: frozenset[str],
    ) -> "IntelligenceSemanticWindow":
        if not isinstance(value, Mapping):
            raise ValueError(f"{field} must be an object")
        window_id = _required_text(value.get("id"), f"{field}.id", maximum=240)
        text = _required_text(
            value.get("text"),
            f"{field}.text",
            maximum=MAX_SEMANTIC_WINDOW_CHARACTERS,
        )
        revision = _positive_integer(value.get("revision", 1), f"{field}.revision")
        start_ms = _optional_non_negative_integer(value.get("start_ms"), f"{field}.start_ms")
        end_ms = _optional_non_negative_integer(value.get("end_ms"), f"{field}.end_ms")
        if start_ms is not None and end_ms is not None and end_ms < start_ms:
            raise ValueError(f"{field}.end_ms must not precede start_ms")
        status = _optional_text(value.get("status"), maximum=20) or "active"
        if status not in _SEMANTIC_WINDOW_STATUSES:
            raise ValueError(f"{field}.status is unsupported")
        segment_ids = _bounded_unique_text_items(
            value.get("segment_ids"),
            f"{field}.segment_ids",
            maximum_items=MAX_NEW_PARAGRAPHS + MAX_CONTEXT_PARAGRAPHS,
            maximum_characters=200,
        )
        if not segment_ids:
            raise ValueError(f"{field}.segment_ids must not be empty")
        unknown_ids = set(segment_ids) - allowed_segment_ids
        if unknown_ids:
            raise ValueError(f"{field}.segment_ids reference paragraphs outside this request")
        source_tracks = _bounded_unique_text_items(
            value.get("source_tracks"),
            f"{field}.source_tracks",
            maximum_items=3,
            maximum_characters=40,
        )
        if any(item not in _SOURCE_TRACKS for item in source_tracks):
            raise ValueError(f"{field}.source_tracks contains an unsupported value")
        role_hints = _bounded_unique_text_items(
            value.get("role_hints"),
            f"{field}.role_hints",
            maximum_items=3,
            maximum_characters=40,
        )
        if any(item not in _ROLE_HINTS for item in role_hints):
            raise ValueError(f"{field}.role_hints contains an unsupported value")
        return cls(
            id=window_id,
            text=text,
            revision=revision,
            start_ms=start_ms,
            end_ms=end_ms,
            status=status,
            segment_ids=segment_ids,
            source_tracks=source_tracks,
            role_hints=role_hints,
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "revision": self.revision,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "status": self.status,
            "segment_ids": list(self.segment_ids),
            "source_tracks": list(self.source_tracks),
            "role_hints": list(self.role_hints),
        }


@dataclass(frozen=True)
class RealtimeIntelligenceRequest:
    meeting_id: str
    state_revision: int
    trigger_type: str
    work_item_id: str | None
    user_request: str | None
    new_paragraphs: tuple[IntelligenceParagraph, ...]
    context_paragraphs: tuple[IntelligenceParagraph, ...]
    retrieval_paragraphs: tuple[IntelligenceParagraph, ...]
    semantic_windows: tuple[IntelligenceSemanticWindow, ...]
    rolling_state: Mapping[str, Any]
    glossary: tuple[str, ...]
    meeting_goal: str | None
    coach_skill_id: str
    allow_paragraph_revisions: bool

    @classmethod
    def from_payload(
        cls,
        *,
        meeting_id: Any,
        state_revision: Any,
        trigger_type: Any = "delta",
        work_item_id: Any = None,
        user_request: Any = None,
        new_paragraphs: Sequence[Mapping[str, Any]],
        context_paragraphs: Sequence[Mapping[str, Any]],
        retrieval_paragraphs: Sequence[Mapping[str, Any]] | None = None,
        semantic_windows: Sequence[Mapping[str, Any]] | None = None,
        rolling_state: Mapping[str, Any],
        glossary: Sequence[Any] | None = None,
        meeting_goal: Any = None,
        coach_skill_id: Any = "general",
        allow_paragraph_revisions: bool = True,
    ) -> "RealtimeIntelligenceRequest":
        normalized_meeting_id = _required_text(meeting_id, "meeting_id", maximum=240)
        normalized_revision = _positive_integer(state_revision, "state_revision")
        normalized_trigger_type = _required_text(
            trigger_type, "trigger_type", maximum=40
        ).casefold()
        if normalized_trigger_type not in _TRIGGER_TYPES:
            raise ValueError(
                "trigger_type must be delta, transcript_delta, task_due, or user_request"
            )
        normalized_work_item_id = _optional_text(work_item_id, maximum=240)
        normalized_user_request = _optional_text(user_request, maximum=2_000)
        if not isinstance(new_paragraphs, Sequence) or isinstance(new_paragraphs, (str, bytes)):
            raise ValueError("new_paragraphs must be an array")
        if len(new_paragraphs) > MAX_NEW_PARAGRAPHS:
            raise ValueError(f"new_paragraphs must contain at most {MAX_NEW_PARAGRAPHS} items")
        if normalized_trigger_type in {"delta", "transcript_delta"} and not new_paragraphs:
            raise ValueError(f"delta new_paragraphs must contain 1 to {MAX_NEW_PARAGRAPHS} items")
        if normalized_trigger_type == "task_due" and normalized_work_item_id is None:
            raise ValueError("task_due requires work_item_id")
        if normalized_trigger_type == "user_request" and normalized_user_request is None:
            raise ValueError("user_request trigger requires user_request")
        if not isinstance(context_paragraphs, Sequence) or isinstance(context_paragraphs, (str, bytes)):
            raise ValueError("context_paragraphs must be an array")
        if len(context_paragraphs) > MAX_CONTEXT_PARAGRAPHS:
            raise ValueError(f"context_paragraphs must contain at most {MAX_CONTEXT_PARAGRAPHS} items")
        raw_retrieval_paragraphs = list(retrieval_paragraphs or [])
        if len(raw_retrieval_paragraphs) > MAX_RETRIEVAL_PARAGRAPHS:
            raise ValueError(f"retrieval_paragraphs must contain at most {MAX_RETRIEVAL_PARAGRAPHS} items")
        if not isinstance(rolling_state, Mapping):
            raise ValueError("rolling_state must be an object")
        if not isinstance(allow_paragraph_revisions, bool):
            raise ValueError("allow_paragraph_revisions must be a boolean")

        new_items = tuple(
            IntelligenceParagraph.from_payload(item, field=f"new_paragraphs[{index}]")
            for index, item in enumerate(new_paragraphs)
        )
        context_items = tuple(
            IntelligenceParagraph.from_payload(item, field=f"context_paragraphs[{index}]")
            for index, item in enumerate(context_paragraphs)
        )
        retrieval_items = tuple(
            IntelligenceParagraph.from_payload(item, field=f"retrieval_paragraphs[{index}]")
            for index, item in enumerate(raw_retrieval_paragraphs)
        )
        all_ids = [item.id for item in (*retrieval_items, *context_items, *new_items)]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("paragraph ids must be unique within one intelligence request")
        raw_windows = list(semantic_windows or [])
        if len(raw_windows) > MAX_SEMANTIC_WINDOWS:
            raise ValueError(f"semantic_windows must contain at most {MAX_SEMANTIC_WINDOWS} items")
        allowed_segment_ids = frozenset(all_ids)
        window_items = tuple(
            IntelligenceSemanticWindow.from_payload(
                item,
                field=f"semantic_windows[{index}]",
                allowed_segment_ids=allowed_segment_ids,
            )
            for index, item in enumerate(raw_windows)
        )
        if len({item.id for item in window_items}) != len(window_items):
            raise ValueError("semantic window ids must be unique within one intelligence request")
        if (
            normalized_trigger_type == "task_due"
            and not new_items
            and not context_items
            and not retrieval_items
        ):
            raise ValueError("task_due without new_paragraphs requires persisted evidence context")

        bounded_state = {
            key: _json_compatible(value) for key, value in rolling_state.items() if key in _ROLLING_STATE_KEYS
        }
        encoded_state = json.dumps(
            bounded_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded_state) > MAX_ROLLING_STATE_BYTES:
            raise ValueError("rolling_state exceeds its bounded size")

        glossary_items: list[str] = []
        for raw_item in list(glossary or [])[:MAX_GLOSSARY_ITEMS]:
            item = _optional_text(raw_item, maximum=MAX_GLOSSARY_ITEM_CHARACTERS)
            if item and item not in glossary_items:
                glossary_items.append(item)

        return cls(
            meeting_id=normalized_meeting_id,
            state_revision=normalized_revision,
            trigger_type=normalized_trigger_type,
            work_item_id=normalized_work_item_id,
            user_request=normalized_user_request,
            new_paragraphs=new_items,
            context_paragraphs=context_items,
            retrieval_paragraphs=retrieval_items,
            semantic_windows=window_items,
            rolling_state=bounded_state,
            glossary=tuple(glossary_items),
            meeting_goal=_optional_text(meeting_goal, maximum=2_000),
            coach_skill_id=normalize_coach_skill_id(coach_skill_id),
            allow_paragraph_revisions=allow_paragraph_revisions,
        )

    @property
    def paragraphs_by_id(self) -> dict[str, IntelligenceParagraph]:
        return {item.id: item for item in (*self.retrieval_paragraphs, *self.context_paragraphs, *self.new_paragraphs)}

    @property
    def writable_paragraph_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.new_paragraphs)

    @property
    def input_characters(self) -> int:
        return sum(len(item.text) for item in (*self.context_paragraphs, *self.new_paragraphs)) + sum(
            len(item.text) for item in self.semantic_windows
        )


@dataclass(frozen=True)
class ParagraphRevision:
    target_id: str
    expected_revision: int
    corrected_text: str
    change_count: int
    changed: bool


@dataclass(frozen=True)
class TopicUpdate:
    operation: str
    title: str
    summary: str
    evidence_segment_ids: tuple[str, ...]
    evidence_quote: str


@dataclass(frozen=True)
class StateChange:
    kind: str
    operation: str
    item_id: str
    content: str
    owner: str | None
    deadline: str | None
    status: str | None
    evidence_segment_ids: tuple[str, ...]
    evidence_quote: str
    confidence: float


@dataclass(frozen=True)
class FollowUp:
    question: str
    reason: str
    evidence_segment_ids: tuple[str, ...]
    evidence_quote: str
    urgency: str
    coach_event_type: str | None = None
    title: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class CoachIntervention:
    event_type: str
    title: str
    recommendation: str
    reason: str
    evidence_segment_ids: tuple[str, ...]
    evidence_quote: str
    urgency: str
    confidence: float
    origin: str | None = None
    local_reflex_kind: str | None = None
    runtime_used: str | None = None
    pi_provider_attempted: bool | None = None
    valid_until_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize one coach card for independent event persistence."""

        payload = {
            "event_type": self.event_type,
            "title": self.title,
            "recommendation": self.recommendation,
            # Keep the product-facing card vocabulary explicit while retaining
            # ``reason``/``recommendation`` for older API consumers.  The
            # application adds validity and lifecycle metadata at projection
            # time because those values depend on the durable job clock.
            "why_now": self.reason,
            "say_this": self.recommendation,
            "reason": self.reason,
            "evidence_segment_ids": list(self.evidence_segment_ids),
            "evidence_quote": self.evidence_quote,
            "urgency": self.urgency,
            "confidence": self.confidence,
        }
        if self.origin is not None:
            payload["origin"] = self.origin
        if self.local_reflex_kind is not None:
            payload["local_reflex_kind"] = self.local_reflex_kind
        if self.runtime_used is not None:
            payload["runtime_used"] = self.runtime_used
        if self.pi_provider_attempted is not None:
            payload["pi_provider_attempted"] = self.pi_provider_attempted
        if self.valid_until_ms is not None:
            payload["valid_until_ms"] = self.valid_until_ms
        return payload

    def to_follow_up(self) -> FollowUp:
        return FollowUp(
            question=self.recommendation,
            reason=self.reason,
            evidence_segment_ids=self.evidence_segment_ids,
            evidence_quote=self.evidence_quote,
            urgency=self.urgency,
            coach_event_type=self.event_type,
            title=self.title,
            confidence=self.confidence,
        )


@dataclass(frozen=True)
class RealtimeIntelligenceResponse:
    paragraph_revisions: tuple[ParagraphRevision, ...]
    topic_update: TopicUpdate | None
    state_changes: tuple[StateChange, ...]
    follow_up: FollowUp | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paragraph_revisions": [asdict(item) for item in self.paragraph_revisions],
            "topic_update": asdict(self.topic_update) if self.topic_update is not None else None,
            "state_changes": [
                {
                    **asdict(item),
                    "type": item.kind,
                    "evidence_segment_ids": list(item.evidence_segment_ids),
                }
                for item in self.state_changes
            ],
            "follow_up": (
                {
                    **asdict(self.follow_up),
                    "evidence_segment_ids": list(self.follow_up.evidence_segment_ids),
                }
                if self.follow_up is not None
                else None
            ),
        }


def build_realtime_intelligence_messages(
    request: RealtimeIntelligenceRequest,
) -> list[dict[str, str]]:
    """Build the only production prompt for incremental meeting semantics."""

    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    revision_rule = (
        "paragraph_revisions 只能修改 new_paragraphs 中的目标，context_paragraphs 只读；修正不得改变事实。"
        if request.allow_paragraph_revisions
        else "正文校对由独立任务处理；paragraph_revisions 必须返回空数组，不得尝试修正文段。"
    )
    system = "".join(
        (
            "你是中文会议实时理解引擎。只依据输入中的会议原话返回一个 JSON 对象，不要输出 Markdown。",
            "不得用关键词匹配、常识补全或猜测生成决定、待办、风险、问题或追问；没有充分证据时返回空数组或 null。",
            "source_track 是采集来源；system_audio/remote_mix 通常是电脑中对方的混音，microphone/self_or_room 可能是用户本人也可能是同处一室的人，归因必须保守。",
            "semantic_windows 是从原始段落派生的理解窗口，只用于理解上下文；所有证据 ID 仍必须从 new_paragraphs 或 context_paragraphs 的 id 中复制。",
            "trigger_type=delta 表示新转写增量；task_due 表示持久事项到期复核；user_request 表示用户主动请求。",
            "task_due 没有新段落时只能基于持久事项和旧证据复核状态，不得假造进展或默认生成新的强提醒。",
            "user_request 必须直接回应 user_request 的授权范围；需要更多证据时只能引用输入或受控检索返回的原文。",
            revision_rule,
            "state_changes.operation 只能是 add、update、resolve、noop，正式变更必须携带可逐字核验的证据 ID 和原话。",
            "输出字段固定且必须全部存在：paragraph_revisions、topic_update、state_changes、follow_up。",
            "无段落修正时 paragraph_revisions 必须是 []；无状态变更时 state_changes 必须是 []。",
            "无主题变更时 topic_update 必须是 null，不得返回 noop 对象。",
            "无值得追问的问题时 follow_up 必须是 null，不得返回 [] 或空对象。",
            "follow_up.urgency 必须是 low、medium、high 之一，不得为 null。",
            "state_changes.type 必须是 decision、action_item、risk、open_question 之一；",
            "topic_update.operation 只能是 add 或 update。",
            "所有证据 ID 和修正 target_id 必须从输入 paragraph id 原样复制。",
        )
    )
    payload = {
        "state_revision": request.state_revision,
        "trigger_type": request.trigger_type,
        "work_item_id": request.work_item_id,
        "user_request": request.user_request,
        "new_paragraphs": [item.to_prompt_dict() for item in request.new_paragraphs],
        "context_paragraphs": [item.to_prompt_dict() for item in request.context_paragraphs],
        "semantic_windows": [item.to_prompt_dict() for item in request.semantic_windows],
        "rolling_state": request.rolling_state,
        "glossary": list(request.glossary),
        "meeting_goal": request.meeting_goal,
        "allow_paragraph_revisions": request.allow_paragraph_revisions,
        "output_contract": {
            "empty_result": {
                "paragraph_revisions": [],
                "topic_update": None,
                "state_changes": [],
                "follow_up": None,
            },
            "paragraph_revisions": [
                {
                    "target_id": "string: new_paragraphs.id",
                    "expected_revision": "positive_integer: target.revision",
                    "corrected_text": "non_empty_string",
                    "change_count": "non_negative_integer; 0 iff text unchanged",
                }
            ],
            "topic_update": {
                "operation": "add|update",
                "title": "non_empty_string",
                "summary": "non_empty_string",
                "evidence_segment_ids": "non_empty_array: input paragraph ids",
                "evidence_quote": "non_empty verbatim substring of referenced paragraphs",
            },
            "state_changes": [
                {
                    "type": "decision|action_item|risk|open_question",
                    "operation": "add|update|resolve",
                    "item_id": "non_empty_string",
                    "content": "non_empty_string",
                    "owner": "string|null",
                    "deadline": "string|null",
                    "status": "string|null",
                    "evidence_segment_ids": "non_empty_array: input paragraph ids",
                    "evidence_quote": "non_empty verbatim substring of referenced paragraphs",
                    "confidence": "number: 0..1",
                }
            ],
            "follow_up": {
                "question": "non_empty_string",
                "reason": "non_empty_string",
                "evidence_segment_ids": "non_empty_array: input paragraph ids",
                "evidence_quote": "non_empty verbatim substring of referenced paragraphs",
                "urgency": "low|medium|high",
            },
        },
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def parse_realtime_intelligence_response(
    content: Any,
    *,
    request: RealtimeIntelligenceRequest,
) -> RealtimeIntelligenceResponse:
    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    payload = _decode_json_object(content)
    allowed_top_level = {
        "paragraph_revisions",
        "topic_update",
        "state_changes",
        "follow_up",
    }
    unexpected = set(payload) - allowed_top_level
    if unexpected:
        raise IntelligenceResponseValidationError(
            f"response contains unsupported fields: {', '.join(sorted(unexpected))}"
        )

    raw_revisions = _required_array(payload.get("paragraph_revisions"), "paragraph_revisions")
    raw_changes = _required_array(payload.get("state_changes"), "state_changes")
    if raw_revisions and not request.allow_paragraph_revisions:
        raise IntelligenceResponseValidationError(
            "paragraph_revisions must be empty because transcript correction is handled independently"
        )
    revisions = tuple(
        _parse_paragraph_revision(value, request=request, index=index) for index, value in enumerate(raw_revisions)
    )
    target_ids = [item.target_id for item in revisions]
    if len(set(target_ids)) != len(target_ids):
        raise IntelligenceResponseValidationError("paragraph revision targets must be unique")

    topic_update = _parse_topic_update(payload.get("topic_update"), request=request)
    changes = tuple(_parse_state_change(value, request=request, index=index) for index, value in enumerate(raw_changes))
    identities = [(item.kind, item.item_id, item.operation) for item in changes]
    if len(set(identities)) != len(identities):
        raise IntelligenceResponseValidationError("state changes must not be duplicated")
    follow_up = _parse_follow_up(payload.get("follow_up"), request=request)
    return RealtimeIntelligenceResponse(
        paragraph_revisions=revisions,
        topic_update=topic_update,
        state_changes=changes,
        follow_up=follow_up,
    )


def realtime_intelligence_idempotency_key(request: RealtimeIntelligenceRequest) -> str:
    canonical = {
        "state_revision": request.state_revision,
        "trigger_type": request.trigger_type,
        "work_item_id": request.work_item_id,
        "user_request": request.user_request,
        "new_paragraphs": [item.to_prompt_dict() for item in request.new_paragraphs],
        "context_paragraphs": [item.to_prompt_dict() for item in request.context_paragraphs],
        "semantic_windows": [item.to_prompt_dict() for item in request.semantic_windows],
        "rolling_state": request.rolling_state,
        "glossary": list(request.glossary),
        "meeting_goal": request.meeting_goal,
        "allow_paragraph_revisions": request.allow_paragraph_revisions,
    }
    private_identity = json.dumps(
        {"meeting_id": request.meeting_id, **canonical},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(private_identity.encode("utf-8")).hexdigest()
    return f"realtime-intelligence:{digest[:40]}"


def realtime_intelligence_batch_id(request: RealtimeIntelligenceRequest) -> str:
    """Return a stable identity for one bounded incremental input batch."""

    canonical = {
        "meeting_id": request.meeting_id,
        "state_revision": request.state_revision,
        "trigger_type": request.trigger_type,
        "work_item_id": request.work_item_id,
        "new_paragraph_ids": [item.id for item in request.new_paragraphs],
        "context_paragraph_ids": [item.id for item in request.context_paragraphs],
        "semantic_window_ids": [item.id for item in request.semantic_windows],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"llm-first-batch:{digest[:40]}"


def build_llm_first_event_context(
    *,
    request: RealtimeIntelligenceRequest,
    response: RealtimeIntelligenceResponse,
    job_id: str,
    batch_id: str,
    provider: str,
    model: str,
    evidence_hash: str | None = None,
) -> dict[str, Any]:
    """Build durable provenance shared by every formal realtime AI event.

    The context intentionally contains bounded evidence references rather than
    the full transcript. Event readers can use the segment ids and quote while
    the canonical transcript remains the source of truth.
    """

    normalized_job_id = _required_text(job_id, "job_id", maximum=240)
    normalized_batch_id = _required_text(batch_id, "batch_id", maximum=240)
    normalized_provider = _required_text(provider, "provider", maximum=160)
    normalized_model = _required_text(model, "model", maximum=160)
    # Delta jobs normally carry fresh paragraphs. Explicit user requests are
    # snapshot reads and deliberately carry no ``new_paragraphs``; use their
    # bounded context window as evidence without relabelling it as new speech.
    paragraphs = list(request.new_paragraphs or request.context_paragraphs)
    evidence_scope = "new_delta" if request.new_paragraphs else "current_context"
    overall_evidence = {
        "segment_ids": [paragraph.id for paragraph in paragraphs],
        "quote": paragraphs[0].text[:1_000] if paragraphs else "",
        "state_revision": request.state_revision,
        "evidence_hash": str(evidence_hash or "") or None,
        "scope": evidence_scope,
    }
    topic_evidence = (
        {
            "segment_ids": list(response.topic_update.evidence_segment_ids),
            "quote": response.topic_update.evidence_quote,
        }
        if response.topic_update is not None
        else None
    )
    follow_up_evidence = (
        {
            "segment_ids": list(response.follow_up.evidence_segment_ids),
            "quote": response.follow_up.evidence_quote,
        }
        if response.follow_up is not None
        else None
    )
    return {
        "schema_version": "llm_first_formal_event_context.v1",
        "source": "llm_first",
        "job_id": normalized_job_id,
        "batch_id": normalized_batch_id,
        "provider": normalized_provider,
        "model": normalized_model,
        "llm_called": True,
        "llm_call_status": "called",
        "evidence": overall_evidence,
        "topic_evidence": topic_evidence,
        "follow_up_evidence": follow_up_evidence,
    }


def dynamic_output_token_limit(input_characters: int) -> int:
    characters = max(0, int(input_characters))
    estimate = 512 + (characters * 1.25)
    rounded = int(math.floor((estimate / 256) + 0.5) * 256)
    return min(4_096, max(768, rounded))


def _coach_candidate_key(
    event_type: str,
    *,
    paragraphs: Sequence[IntelligenceParagraph],
) -> str:
    canonical = {
        "event_type": event_type,
        "evidence": [
            {
                "id": item.id,
                "revision": item.revision,
                "text": " ".join(item.text.casefold().split()),
            }
            for item in paragraphs
        ],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"coach-candidate:{event_type}:{digest[:24]}"


def _coach_episode_candidate_key(
    event_type: str,
    *,
    anchor: IntelligenceParagraph,
) -> str:
    """Keep one sustained-discourse episode on the same cooldown identity."""

    canonical = {
        "event_type": event_type,
        "anchor": {
            "id": anchor.id,
            "revision": anchor.revision,
            "text": " ".join(anchor.text.casefold().split()),
            "source_track": anchor.source_track,
            "speaker": anchor.speaker,
        },
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"coach-candidate:{event_type}:{digest[:24]}"


def _repeated_coach_clause(text: str) -> bool:
    clauses = [
        "".join(part.casefold().split())
        for part in re.split(r"[。！？!?；;，,：:\n]+", text)
        if len("".join(part.split())) >= 6
    ]
    return len(clauses) != len(set(clauses))


def _coach_repetition_touches_fresh(
    paragraphs: Sequence[IntelligenceParagraph],
    *,
    fresh_ids: frozenset[str],
) -> bool:
    occurrences: dict[str, list[str]] = {}
    for paragraph in paragraphs:
        clauses = [
            "".join(part.casefold().split())
            for part in re.split(r"[。！？!?；;，,：:\n]+", paragraph.text)
            if len("".join(part.split())) >= 6
        ]
        for clause in clauses:
            occurrences.setdefault(clause, []).append(paragraph.id)
    return any(
        len(paragraph_ids) >= 2
        and any(paragraph_id in fresh_ids for paragraph_id in paragraph_ids)
        for paragraph_ids in occurrences.values()
    )


def _recent_coach_discourse_window(
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, ...]:
    """Return the bounded continuous tail ending in fresh audible evidence."""

    audible_new = tuple(
        item
        for item in request.new_paragraphs
        if item.source_track in {"system_audio", "microphone"}
    )
    if not audible_new:
        return ()
    anchor = audible_new[-1]
    ordered = (
        *request.retrieval_paragraphs,
        *request.context_paragraphs,
        *request.new_paragraphs,
    )
    try:
        anchor_index = max(index for index, item in enumerate(ordered) if item.id == anchor.id)
    except ValueError:
        return ()

    reverse_window = [anchor]
    later = anchor
    new_ids = request.writable_paragraph_ids
    normalized_characters = len("".join(anchor.text.split()))
    latest_end_ms = anchor.end_ms if anchor.end_ms is not None else anchor.start_ms
    if anchor.source_track == "system_audio" and not anchor.speaker:
        return (anchor,)
    for earlier in reversed(ordered[:anchor_index]):
        if len(reverse_window) >= COACH_DISCOURSE_MAX_PARAGRAPHS:
            break
        if earlier.source_track != anchor.source_track:
            break
        if earlier.speaker and later.speaker and earlier.speaker != later.speaker:
            break
        crosses_batch = earlier.id not in new_ids or later.id not in new_ids
        if crosses_batch and (
            earlier.start_ms is None
            or earlier.end_ms is None
            or later.start_ms is None
            or later.end_ms is None
        ):
            break
        if earlier.start_ms is not None and later.start_ms is not None:
            if earlier.start_ms > later.start_ms:
                break
        if earlier.end_ms is not None and later.start_ms is not None:
            if later.start_ms - earlier.end_ms > COACH_DISCOURSE_MAX_GAP_MS:
                break
        if latest_end_ms is not None and earlier.start_ms is not None:
            if latest_end_ms - earlier.start_ms > COACH_DISCOURSE_MAX_WINDOW_MS:
                break
        earlier_characters = len("".join(earlier.text.split()))
        if normalized_characters + earlier_characters > COACH_DISCOURSE_MAX_CHARACTERS:
            break
        reverse_window.append(earlier)
        normalized_characters += earlier_characters
        later = earlier

    return tuple(reversed(reverse_window))


def _coach_discourse_duration_ms(
    paragraphs: Sequence[IntelligenceParagraph],
) -> int:
    intervals = sorted(
        (int(item.start_ms), int(item.end_ms))
        for item in paragraphs
        if item.start_ms is not None and item.end_ms is not None
    )
    if not intervals:
        return 0
    duration_ms = 0
    active_start, active_end = intervals[0]
    for start_ms, end_ms in intervals[1:]:
        if start_ms <= active_end:
            active_end = max(active_end, end_ms)
            continue
        duration_ms += max(0, active_end - active_start)
        active_start, active_end = start_ms, end_ms
    return duration_ms + max(0, active_end - active_start)


def _communication_clarity_explicit_risk(text: str) -> bool:
    normalized = text.casefold()
    return bool(
        re.search(
            r"(?:继续|再|又|先).{0,12}(?:补充|重复).{0,12}(?:背景|旁支|来龙去脉)"
            r"|(?:结论|下一步).{0,16}(?:稍后|后面|暂时|以后|再说|先放|放到|放在)"
            r"|(?:先不|暂不|还不).{0,8}(?:下|给|说)?结论"
            r"|(?:没有|还没有|还没|仍然没有|仍未|尚未|未能).{0,24}(?:结论|收束|重点|下一步)"
            r"|(?:失焦|跑题|几个旁支|没有收束|反复表达|重复背景|结论稍后)",
            normalized,
        )
    )


def _communication_clarity_is_closed(text: str) -> bool:
    """Recognize a structured conclusion and next step, not keyword presence."""

    normalized = text.casefold()
    if _communication_clarity_explicit_risk(normalized):
        return False
    has_conclusion = bool(
        re.search(
            r"(?:结论|最终结论|决定|最终决定)(?:是|为|：|:)"
            r"|(?:这就是|以上就是).{0,12}(?:本轮|这轮|当前)?.{0,8}结论",
            normalized,
        )
    )
    has_next_step = bool(
        re.search(
            r"(?:下一步|接下来)(?:是|为|：|:|分|由)"
            r"|(?:负责人|责任人)(?:是|为|：|:)"
            r"|(?:由|请|让).{1,20}(?:负责|跟进|完成)",
            normalized,
        )
    )
    return has_conclusion and has_next_step


def _coach_clarity_candidate_specs(
    request: RealtimeIntelligenceRequest,
) -> tuple[
    tuple[
        str,
        str,
        tuple[IntelligenceParagraph, ...],
        tuple[IntelligenceParagraph, ...],
    ],
    ...,
]:
    """Build risk-gated clarity candidates across adjacent ASR batches."""

    window = _recent_coach_discourse_window(request)
    if not window:
        return ()
    text = " ".join(item.text for item in window)
    normalized = text.casefold()
    new_ids = request.writable_paragraph_ids
    repeated = _coach_repetition_touches_fresh(window, fresh_ids=new_ids)
    explicit_risk = _communication_clarity_explicit_risk(normalized)
    fresh_risk = any(
        item.id in new_ids and _communication_clarity_explicit_risk(item.text)
        for item in window
    )
    long_enough = (
        _coach_discourse_duration_ms(window) >= 45_000
        or len("".join(normalized.split())) >= 120
    )
    if _communication_clarity_is_closed(normalized):
        return ()
    if not repeated and not (long_enough and explicit_risk and fresh_risk):
        return ()

    fresh = tuple(item for item in window if item.id in new_ids)
    if not fresh:
        return ()
    prior = tuple(item for item in window if item.id not in new_ids)
    if prior:
        risk_prior = tuple(
            item for item in prior if _communication_clarity_explicit_risk(item.text)
        )
        evidence = ((risk_prior or prior)[-1], fresh[-1])
        risk_window = tuple(
            item for item in window if _communication_clarity_explicit_risk(item.text)
        )
        key_paragraphs = ((risk_window or window)[0],)
    else:
        evidence = window
        key_paragraphs = evidence

    specs: list[
        tuple[
            str,
            str,
            tuple[IntelligenceParagraph, ...],
            tuple[IntelligenceParagraph, ...],
        ]
    ] = []
    if repeated:
        specs.append(
            (
                "repetition",
                "连续原话出现重复表达，需要判断是否仍值得立刻收束。",
                evidence,
                key_paragraphs,
            )
        )
    elif long_enough and explicit_risk and fresh_risk:
        specs.append(
            (
                "monologue_duration",
                "连续表达较长且仍有未收束信号，需要检查是否应当介入。",
                evidence,
                key_paragraphs,
            )
        )
    return tuple(specs)


def _resolved_objection_signal(text: str) -> bool:
    """Recognize a concern that the latest sentence explicitly closes."""

    return bool(
        re.search(
            r"(?:担心|反对|异议|问题).{0,48}(?:已|已经|全部|逐条).{0,36}(?:验证|记录|解决|确认|关闭|通过|达到)",
            text,
        )
    )


def _resolved_prior_state_signal(text: str) -> bool:
    """Recognize a newer evidence-backed update that explicitly closes old uncertainty."""

    return bool(
        re.search(
            r"(?:新|最新).{0,24}(?:报告|证据|结果).{0,72}(?:旧|此前).{0,36}(?:状态|结论|记录)?.{0,16}(?:关闭|更新|失效|取代)",
            text,
        )
    )


def _pending_question_signal(text: str) -> bool:
    """Recognize a direct question without routing subordinate ``whether`` clauses."""

    if any(mark in text for mark in ("?", "？")):
        return True

    # Chinese ASR commonly drops the final question particle and leaves a
    # request tail such as ``还缺什么`` or ``我还缺少``.  Treat only this
    # bounded tail as a pending question; it is a high-signal request for the
    # coach to identify the missing owner/condition, not a generic mention of
    # a missing field in the meeting history.
    if re.search(r"(?:还缺什么|缺少什么|缺什么|还缺少)(?:[。！？!?，,；;]|$)", text):
        return True

    # ASR finals often omit terminal punctuation. An imperative request to
    # resolve an owner or deadline is still pending even though ``确认`` also
    # introduces subordinate research descriptions below.
    if re.search(
        r"(?:麻烦|请(?:你|您|大家)?|我们(?:还|也)?需要|需要).{0,8}确认.{0,20}"
        r"(?:谁(?:来)?(?:负责|处理)|什么时候(?:完成|交付|上线|给出结果)?)",
        text,
    ):
        return True

    # Statements such as ``再看看其他团队怎么做`` describe a research step;
    # they do not ask the listener a question. Remove the bounded subordinate
    # clause before checking question words without terminal punctuation.
    without_subordinate_questions = re.sub(
        r"(?:看看|看一看|查看|了解|确认|调查|分析|研究|讨论|评估|梳理|观察|记录|决定|判断)"
        r".{0,12}(?:为什么|怎么|什么时候|谁来|谁负责|能否|有没有|是不是|可不可以)",
        "",
        text,
    )
    if any(
        term in without_subordinate_questions
        for term in (
            "请问",
            "为什么",
            "怎么",
            "什么时候",
            "谁来",
            "谁负责",
            "能否",
            "有没有",
            "是不是",
            "可不可以",
        )
    ):
        return True
    if re.search(r"吗(?:[\s。！!，,；;]|$)", text):
        return True

    # ``达到阈值后再决定是否/要不要扩大`` states the next decision rule;
    # nobody is asking the user a pending question. Treating every ``是否`` or
    # ``要不要`` as an interrogative wastes a Provider call and can manufacture
    # low-value coaching after a complete experiment or plan.
    without_subordinate_whether = re.sub(
        r"(?:决定|确认|评估|判断|验证|考虑|讨论|研究|检查|测试)(?:是否|要不要)",
        "",
        without_subordinate_questions,
    )
    return "是否" in without_subordinate_whether or "要不要" in without_subordinate_whether


def _future_commitment_signal(text: str) -> bool:
    """Recognize an actual future commitment, not discussion about a date."""

    # Remove the whole negated prediction. Removing only ``不一定`` would leave
    # ``周五上线`` behind and accidentally turn uncertainty into a commitment.
    routed = re.sub(
        r"(?:不一定|未必|不见得|不能确定|无法确定|还不能确定|尚不能确定)"
        r".{0,24}(?:上线|交付|发布|完成|截止|开工|落地)",
        "",
        text,
    )
    routed = re.sub(
        r"(?:不想|不愿|不打算|不能|无法|尚未|还没有|还没|没有|未能|暂不)"
        r".{0,12}(?:明确|确定|承诺|保证|上线|交付|发布|截止)",
        "",
        routed,
    )
    routed = re.sub(
        r"(?:不|未)(?:明确|确定|承诺|保证|上线|交付|发布)",
        "",
        routed,
    )
    routed = re.sub(
        r"(?:担心|讨论|关注|考虑|评估|提到|回顾|围绕)"
        r".{0,12}(?:上线|交付|发布|截止|日期|时间)",
        "",
        routed,
    )
    time_marker = (
        r"(?:今天|明天|后天|本周|下周|周[一二三四五六日天]|月底|月末|"
        r"\d{1,2}[月日号点]|第[一二三四]季度)"
    )
    action = r"(?:上线|交付|(?<!预)发布|完成|截止|开工|落地)"
    if re.search(r"(?:承诺|保证)", routed):
        return True
    if re.search(
        rf"(?:一定|确定(?!性)(?:要|会|能|将)?).{{0,16}}{action}"
        rf"|{action}.{{0,16}}(?:一定|确定(?!性))",
        routed,
    ):
        return True
    return bool(
        re.search(rf"{time_marker}.{{0,16}}{action}|{action}.{{0,16}}{time_marker}", routed)
    )


def _explicit_execution_gap_signal(text: str) -> bool:
    """Recognize a stated missing execution owner without inventing one.

    ASR often removes punctuation from phrases such as ``还没有指定回滚负责人``.
    This is only a routing signal; Pi must still cite and validate the exact
    evidence before it can produce an intervention.
    """

    match = _COACH_EXPLICIT_EXECUTION_GAP_RE.search(text)
    if match is None:
        return False
    resolution = _COACH_EXECUTION_GAP_RESOLVED_RE.search(text, match.start())
    return resolution is None or resolution.start() <= match.start()


def _current_high_loss_objection_signal(
    paragraphs: Sequence[IntelligenceParagraph],
) -> bool:
    """Keep current first-person safety objections distinct from clarity noise."""

    high_loss_terms = (
        "安全",
        "隐私",
        "合规",
        "数据丢失",
        "丢失数据",
        "丢数据",
        "数据泄露",
        "泄露数据",
        "越权",
        "违法",
        "违规",
    )
    current_terms = ("现在", "当前", "目前", "仍然", "仍", "还", "这次")
    historical_terms = ("之前", "此前", "过去", "曾经", "当时", "原来", "一度")
    for paragraph in paragraphs:
        clauses = re.split(r"[。！？!?；;，,\n]+", paragraph.text.casefold())
        for clause in clauses:
            if _resolved_objection_signal(clause):
                continue
            if not any(term in clause for term in high_loss_terms):
                continue
            if not re.search(
                r"(?:我|我们|我方).{0,20}(?:担心|担忧|顾虑|反对|不能接受|无法接受|不接受|有异议|有问题)",
                clause,
            ):
                continue
            if any(term in clause for term in historical_terms) and not any(
                term in clause for term in current_terms
            ):
                continue
            return True
    return False


def _current_weak_objection_signal(
    paragraphs: Sequence[IntelligenceParagraph],
) -> bool:
    """Ignore weak concerns that are explicitly framed as historical context."""

    historical_terms = (
        "上个月",
        "上周",
        "上次",
        "之前",
        "此前",
        "过去",
        "曾经",
        "当时",
        "原来",
        "一度",
    )
    current_terms = (
        "现在",
        "当前",
        "目前",
        "如今",
        "这次",
        "今天仍",
        "今天还",
        "仍然",
        "依然",
        "还是",
    )
    for paragraph in paragraphs:
        text = paragraph.text.casefold()
        if _resolved_objection_signal(text):
            continue
        for match in re.finditer(r"担心|有问题", text):
            left = text[max(0, match.start() - 48) : match.start()]
            last_historical = max(
                (left.rfind(term) for term in historical_terms),
                default=-1,
            )
            last_current = max(
                (left.rfind(term) for term in current_terms),
                default=-1,
            )
            if last_historical >= 0 and last_historical > last_current:
                continue
            return True
    return False


def realtime_coach_candidate_events(
    request: RealtimeIntelligenceRequest,
) -> tuple[CoachCandidateEvent, ...]:
    """Detect bounded state events before spending a Pi turn.

    This is routing, not semantic advice. The model still decides whether to
    intervene and must pass the evidence validator. Candidate records make the
    trigger auditable and give persistence a stable cooldown identity.
    """

    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    paragraphs = tuple(
        item
        for item in request.new_paragraphs
        if item.source_track in {"system_audio", "microphone"}
    )
    if not paragraphs:
        return ()
    text = " ".join(item.text for item in paragraphs)
    normalized = text.casefold()
    evidence_ids = tuple(item.id for item in paragraphs)
    candidates: list[tuple[str, str]] = []
    clarity_specs = _coach_clarity_candidate_specs(request)

    if _pending_question_signal(normalized):
        candidates.append(("question_pending", "新证据包含仍可能需要回应的问题。"))

    objection_strong_terms = ("不同意", "不能接受", "不接受", "不行", "太贵", "反对", "异议")
    objection_weak_terms = ("担心", "有问题")
    objection_is_resolved = _resolved_objection_signal(normalized)
    has_strong_objection = any(term in normalized for term in objection_strong_terms) and not objection_is_resolved
    has_weak_objection = (
        any(term in normalized for term in objection_weak_terms)
        and not objection_is_resolved
        and _current_weak_objection_signal(paragraphs)
    )
    has_current_high_loss_objection = _current_high_loss_objection_signal(paragraphs)
    # A sustained clarity episode already carries weak concerns and discourse
    # connectives as evidence. Explicit rejection and a current first-person
    # high-loss objection remain independently eligible.
    if has_strong_objection or has_current_high_loss_objection or (
        not clarity_specs and has_weak_objection
    ):
        candidates.append(("objection_detected", "新证据出现反对、担忧或不可接受条件。"))

    condition_terms = ("如果", "只要", "前提", "条件", "之后", "通过后", "确认后", "取决于", "暂定", "目标")
    retrospective_completion = any(
        term in normalized
        for term in (
            "已经完成",
            "已完成",
            "完成了",
            "做完了",
            "已经交付",
            "已交付",
            "已经验证",
            "已验证",
            "已经确认",
            "已确认",
            "已经记录",
            "已记录",
            "已经关闭",
            "已关闭",
        )
    )
    has_future_commitment = _future_commitment_signal(normalized) and not retrospective_completion
    if has_future_commitment and not any(
        term in normalized for term in condition_terms
    ):
        candidates.append(("commitment_without_condition", "日期、结果或责任承诺缺少明确前提。"))
    if _explicit_execution_gap_signal(normalized):
        candidates.append(("missing_next_step", "新证据明确指出执行责任或验收责任尚未闭环。"))

    drift_terms = (
        "换个话题",
        "先不讨论",
        "先不说",
        "回头再说",
        "另一个问题",
        "题外话",
        "进入下一个议题",
        "进入下一议题",
        "下一个议题",
        "换到下个议题",
    )
    if any(term in normalized for term in drift_terms):
        open_items = request.rolling_state.get("open_items")
        has_open_items = isinstance(open_items, list) and any(
            isinstance(item, Mapping)
            and str(item.get("status") or "open") in {"open", "carried_over", "unknown"}
            for item in open_items
        )
        if has_open_items or any(term in normalized for term in ("先不讨论", "先不说", "回头再说")):
            candidates.append(("topic_drift", "对话正在显式离开当前议题。"))
        if request.meeting_goal and (has_open_items or any(term in normalized for term in ("先不讨论", "先不说"))):
            candidates.append(("goal_at_risk", "对话转场时会议目标仍可能未覆盖。"))

    # A deterministic routing lead for cross-paragraph contradictions. The
    # Agent still decides whether the terminal event is ``contradiction`` and
    # must cite both the new assertion and the older unresolved evidence.
    certainty_terms = ("已经确认", "已确认", "按已经", "按已", "确定支持", "确认支持")
    unresolved_terms = ("尚未", "还没有", "未完成", "未验证", "只是测试目标", "没有确认", "没确认")
    prior_text = " ".join(
        item.text
        for item in (*request.context_paragraphs, *request.semantic_windows)
    ).casefold()
    if not _resolved_prior_state_signal(normalized) and any(term in normalized for term in certainty_terms) and any(
        term in prior_text for term in unresolved_terms
    ):
        candidates.append(("objection_detected", "新证据的确定性表述可能覆盖了较早的未验证条件。"))

    skill_id = normalize_coach_skill_id(request.coach_skill_id)
    open_items = request.rolling_state.get("open_items")
    has_open_items = isinstance(open_items, list) and any(
        isinstance(item, Mapping)
        and str(item.get("status") or "open") in {"open", "carried_over", "unknown"}
        for item in open_items
    )
    if skill_id == "decision" and has_open_items and any(
        term in normalized for term in ("直接定", "就定", "不再讨论", "拍板", "采用方案")
    ):
        candidates.append(("commitment_without_condition", "决策正在收口，但仍有未关闭的决策条件。"))
    if skill_id == "project" and has_open_items and any(
        term in normalized for term in ("就先这么安排", "就这么办", "推进", "落地")
    ):
        candidates.append(("missing_next_step", "项目话题正在收口，但仍缺少可执行的下一步。"))
    if skill_id == "interview" and any(
        term in normalized for term in ("很麻烦", "不好用", "不方便", "太慢", "不稳定")
    ):
        candidates.append(("question_pending", "访谈中的问题描述仍缺少具体场景或影响。"))
    if skill_id == "brainstorm" and has_open_items and any(
        term in normalized for term in ("直接做它", "直接做", "马上开发", "就做")
    ) and not any(term in normalized for term in ("原型", "试用", "样本", "采纳率", "指标", "实验")):
        candidates.append(("commitment_without_condition", "方案正在过早收口，仍缺少可验证的最小实验。"))

    closing_terms = (
        "就这样",
        "先到这里",
        "先到这",
        "今天到这里",
        "今天到这",
        "先结束",
        "散会",
    )
    next_step_terms = ("下一步", "负责人", "截止", "什么时候", "谁来", "跟进")
    if any(term in normalized for term in closing_terms) and not any(
        term in normalized for term in next_step_terms
    ):
        candidates.append(("missing_next_step", "对话正在收束，但尚未出现可执行的下一步。"))

    unique: dict[str, CoachCandidateEvent] = {}
    for event_type, reason in candidates:
        unique.setdefault(
            event_type,
            CoachCandidateEvent(
                event_type=event_type,
                evidence_segment_ids=evidence_ids,
                reason=reason,
                candidate_key=_coach_candidate_key(event_type, paragraphs=paragraphs),
            ),
        )
    for event_type, reason, evidence, key_paragraphs in clarity_specs:
        has_prior_evidence = any(item.id not in request.writable_paragraph_ids for item in evidence)
        unique.setdefault(
            event_type,
            CoachCandidateEvent(
                event_type=event_type,
                evidence_segment_ids=tuple(item.id for item in evidence),
                reason=reason,
                candidate_key=(
                    _coach_episode_candidate_key(event_type, anchor=key_paragraphs[0])
                    if has_prior_evidence
                    else _coach_candidate_key(event_type, paragraphs=key_paragraphs)
                ),
            ),
        )
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (-_COACH_TRIGGER_PRIORITIES[item.event_type], item.event_type),
        )
    )


_LOCAL_REFLEX_BLOCKING_CANDIDATES = frozenset(
    {
        "question_pending",
        "commitment_without_condition",
        "goal_at_risk",
        "topic_drift",
        "contradiction",
        "decision_readiness",
        "execution_gap",
        "discovery_gap",
        "experiment_gap",
    }
)
_LOCAL_REFLEX_NEXT_STEP_TERMS = (
    "下一步",
    "接下来",
    "负责人",
    "责任人",
    "截止",
    "什么时候",
    "谁来",
    "谁负责",
    "跟进",
    "行动项",
    "会后",
    "回看",
)
_LOCAL_REFLEX_STRONG_OBJECTION_TERMS = (
    "不同意",
    "不能接受",
    "不接受",
    "反对",
)


def _local_reflex_candidate_types(
    candidates: Sequence[CoachCandidateEvent | Mapping[str, Any]],
) -> frozenset[str]:
    event_types: set[str] = set()
    for candidate in candidates:
        value = (
            candidate.event_type
            if isinstance(candidate, CoachCandidateEvent)
            else candidate.get("event_type")
            if isinstance(candidate, Mapping)
            else None
        )
        normalized = str(value or "").strip()
        if normalized:
            event_types.add(normalized)
    return frozenset(event_types)


def _local_reflex_closing_fragment(text: str) -> str | None:
    for clause in re.split(r"[\n。！？!?;；]+", str(text or "")):
        stripped = clause.strip()
        normalized = stripped.casefold()
        if not stripped:
            continue
        if re.search(
            r"(?:就这样|先到这里|先到这|今天到这里|今天到这|先结束|散会)(?:吧|了|为止)?$",
            normalized,
        ):
            return stripped[:500]
    return None


def _local_reflex_execution_gap_evidence(
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, str] | None:
    """Return an exact current fragment that explicitly leaves ownership open.

    This is deliberately narrower than the candidate detector: only an
    explicit owner/approver gap can produce an immediate local clarification.
    The reflex never infers who the owner is or invents a date.
    """

    for paragraph in reversed(request.new_paragraphs):
        text = paragraph.text.strip()
        if not text or not _explicit_execution_gap_signal(text.casefold()):
            continue
        for fragment in re.split(r"[\n。！？!?;；]+", text):
            stripped = fragment.strip()
            if stripped and _explicit_execution_gap_signal(stripped.casefold()):
                return paragraph, stripped[:500]
        return paragraph, text[:500]
    return None


def _local_reflex_pending_question_evidence(
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, str] | None:
    """Return the newest exact fragment that still contains a question.

    This helper is only used by the explicit post-Pi-timeout fallback. It does
    not infer an answer, owner, or deadline; it simply quotes the unresolved
    question so the UI can ask for a response without pretending that Pi
    completed.
    """

    for paragraph in reversed(request.new_paragraphs):
        text = paragraph.text.strip()
        if not text or not _pending_question_signal(text.casefold()):
            continue
        fragments = [
            fragment.strip()
            for fragment in re.split(r"[\n。！？!?;；]+", text)
            if fragment.strip()
        ]
        for fragment in reversed(fragments):
            if _pending_question_signal(fragment.casefold()):
                return paragraph, fragment[:500]
        return paragraph, text[:500]
    return None


def _local_reflex_strong_objection(
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, str] | None:
    historical_terms = ("之前", "此前", "过去", "曾经", "当时", "原来", "一度")
    current_terms = ("现在", "当前", "目前", "今天", "这次", "仍然", "依然")
    resolved_terms = ("已经解决", "已解决", "现在同意", "可以接受", "可以继续", "不再反对")
    for paragraph in reversed(request.new_paragraphs):
        if paragraph.source_track != "system_audio":
            continue
        normalized = paragraph.text.casefold()
        if not any(term in normalized for term in _LOCAL_REFLEX_STRONG_OBJECTION_TERMS):
            continue
        if _resolved_objection_signal(normalized) or any(term in normalized for term in resolved_terms):
            continue
        for fragment in re.split(r"[\n。！？!?;；]+", paragraph.text):
            stripped = fragment.strip()
            normalized_fragment = stripped.casefold()
            if not stripped:
                continue
            for term in _LOCAL_REFLEX_STRONG_OBJECTION_TERMS:
                objection_at = normalized_fragment.find(term)
                if objection_at < 0:
                    continue
                left = normalized_fragment[:objection_at]
                latest_historical = max((left.rfind(item) for item in historical_terms), default=-1)
                latest_current = max((left.rfind(item) for item in current_terms), default=-1)
                if latest_historical > latest_current:
                    continue
                return paragraph, stripped[:500]
    return None


def _local_reflex_clarity_evidence(
    request: RealtimeIntelligenceRequest,
    *,
    event_type: str,
) -> tuple[tuple[str, ...], str] | None:
    for candidate_type, _reason, evidence, _key_paragraphs in _coach_clarity_candidate_specs(request):
        if candidate_type != event_type or not evidence:
            continue
        if len(evidence) >= 2:
            selected = (evidence[0], evidence[-1])
            quote_lines = tuple(item.text.strip()[:450] for item in selected if item.text.strip())
        else:
            selected = evidence
            clauses = tuple(
                clause.strip()[:450]
                for clause in re.split(r"[。！？!?;；，,\n]+", evidence[0].text)
                if len("".join(clause.split())) >= 6
            )
            if event_type == "repetition":
                repeated: dict[str, list[str]] = {}
                for clause in clauses:
                    repeated.setdefault(_normalize_for_evidence(clause).casefold(), []).append(clause)
                quote_lines = next(
                    (
                        (occurrences[0], occurrences[1])
                        for occurrences in repeated.values()
                        if len(occurrences) >= 2
                    ),
                    (),
                )
            else:
                quote_lines = (clauses[0], clauses[-1]) if len(clauses) >= 2 else ()
        if len(quote_lines) < 2:
            continue
        return tuple(dict.fromkeys(item.id for item in selected)), "\n".join(quote_lines)
    return None


def _validated_local_reflex_intervention(
    *,
    request: RealtimeIntelligenceRequest,
    local_reflex_kind: str,
    event_type: str,
    title: str,
    recommendation: str,
    reason: str,
    evidence_ids: tuple[str, ...],
    evidence_quote: str,
    urgency: str,
    valid_until_ms: int,
) -> CoachIntervention:
    _validate_evidence_quote(
        evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
        field="local_reflex.evidence_quote",
    )
    _validate_coach_claim_grounding(
        title=title,
        recommendation=recommendation,
        reason=reason,
        evidence_quote=evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
    )
    return CoachIntervention(
        event_type=event_type,
        title=title,
        recommendation=recommendation,
        reason=reason,
        evidence_segment_ids=evidence_ids,
        evidence_quote=evidence_quote,
        urgency=urgency,
        confidence=1.0,
        origin="local_reflex",
        local_reflex_kind=local_reflex_kind,
        runtime_used="local_reflex",
        pi_provider_attempted=False,
        valid_until_ms=valid_until_ms,
    )


def build_local_reflex_intervention(
    request: RealtimeIntelligenceRequest,
    candidates: Sequence[CoachCandidateEvent | Mapping[str, Any]],
    *,
    now_ms: int | None = None,
    allow_pending_question: bool = False,
) -> dict[str, Any] | None:
    """Build one deterministic, fact-free coaching reflex without a Provider call.

    This is intentionally narrower than the Pi routing gate. It only handles an
    explicit close without a next step, an already risk-gated clarity episode,
    or a current unresolved strong objection from system audio. The optional
    pending-question path is reserved for a post-Pi-timeout fallback; callers
    must opt in explicitly so a normal local pass cannot swallow a higher-value
    Pi candidate.
    """

    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Sequence):
        raise TypeError("candidates must be a sequence")
    if now_ms is None:
        resolved_now_ms = int(time.time() * 1_000)
    elif isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    else:
        resolved_now_ms = now_ms
    valid_until_ms = resolved_now_ms + 90_000

    candidate_types = _local_reflex_candidate_types(candidates)
    blocking_types = candidate_types & _LOCAL_REFLEX_BLOCKING_CANDIDATES
    if blocking_types:
        # Natural language often combines an explicit owner gap with a
        # follow-up request (which also routes as ``question_pending``). The
        # owner gap is the higher-signal, fact-grounded local safety reflex;
        # other blocking candidates must still suppress it.
        explicit_owner_gap = _local_reflex_execution_gap_evidence(request)
        if not (
            blocking_types == {"question_pending"}
            and "missing_next_step" in candidate_types
            and explicit_owner_gap is not None
        ) and not (allow_pending_question and blocking_types == {"question_pending"}):
            return None

    intervention: CoachIntervention | None = None
    if "objection_detected" in candidate_types:
        objection = _local_reflex_strong_objection(request)
        if objection is not None:
            paragraph, quote = objection
            intervention = _validated_local_reflex_intervention(
                request=request,
                local_reflex_kind="strong_objection",
                event_type="discovery_gap",
                title="先理解反对条件",
                recommendation="我听到了明确反对。你最担心的风险是什么，什么条件下可以继续？",
                reason="对方刚刚明确表达反对，需要先确认担心的风险和继续条件。",
                evidence_ids=(paragraph.id,),
                evidence_quote=quote,
                urgency="high",
                valid_until_ms=valid_until_ms,
            )
        else:
            # Weak, ambiguous, historical, or non-remote objections remain Pi
            # work. A lower-priority local close/clarity card must not hide them.
            return None

    if intervention is None and "missing_next_step" in candidate_types:
        explicit_gap = _local_reflex_execution_gap_evidence(request)
        if explicit_gap is not None:
            paragraph, quote = explicit_gap
            intervention = _validated_local_reflex_intervention(
                request=request,
                local_reflex_kind="missing_next_step",
                event_type="execution_gap",
                title="先补齐执行责任",
                recommendation="先确认一下：谁负责推进、谁负责验收、什么时候回看？",
                reason="原文明确表示执行责任或验收责任尚未闭环。",
                evidence_ids=(paragraph.id,),
                evidence_quote=quote,
                urgency="high",
                valid_until_ms=valid_until_ms,
            )
        else:
            fresh_text = " ".join(item.text.casefold() for item in request.new_paragraphs)
            if not any(term in fresh_text for term in _LOCAL_REFLEX_NEXT_STEP_TERMS):
                for paragraph in reversed(request.new_paragraphs):
                    quote = _local_reflex_closing_fragment(paragraph.text)
                    if quote is None:
                        continue
                    intervention = _validated_local_reflex_intervention(
                        request=request,
                        local_reflex_kind="missing_next_step",
                        event_type="execution_gap",
                        title="收口前补齐执行信息",
                        recommendation="先确认一下：下一步是什么、谁来负责、什么时候回看？",
                        reason="对话正在收尾，但还没有明确下一步。",
                        evidence_ids=(paragraph.id,),
                        evidence_quote=quote,
                        urgency="high",
                        valid_until_ms=valid_until_ms,
                    )
                    break

    if intervention is None and allow_pending_question and "question_pending" in candidate_types:
        pending_question = _local_reflex_pending_question_evidence(request)
        if pending_question is not None:
            paragraph, quote = pending_question
            intervention = _validated_local_reflex_intervention(
                request=request,
                local_reflex_kind="pending_question",
                event_type="question_to_user",
                title="先回应这个问题",
                recommendation="先直接回应这个问题，并说明当前结论。",
                reason="原文包含待回应的问题，先给出明确回应。",
                evidence_ids=(paragraph.id,),
                evidence_quote=quote,
                urgency="high",
                valid_until_ms=valid_until_ms,
            )

    if intervention is None:
        clarity_type = next(
            (item for item in ("repetition", "monologue_duration") if item in candidate_types),
            None,
        )
        if clarity_type is not None:
            clarity = _local_reflex_clarity_evidence(request, event_type=clarity_type)
            if clarity is not None:
                evidence_ids, evidence_quote = clarity
                intervention = _validated_local_reflex_intervention(
                    request=request,
                    local_reflex_kind="communication_clarity",
                    event_type="communication_clarity",
                    title="现在收束核心观点",
                    recommendation="我先收束一下：核心观点是……，依据是……，下一步是……",
                    reason="连续表达尚未收束，此刻用观点、依据和下一步重组表达。",
                    evidence_ids=evidence_ids,
                    evidence_quote=evidence_quote,
                    urgency="medium",
                    valid_until_ms=valid_until_ms,
                )

    if intervention is None:
        return None
    return _with_coach_provenance(
        {
            "intervention": intervention,
            "source": "local_reflex",
            "runtime_requested": "local_reflex",
            "runtime_used": "local_reflex",
            "local_reflex_kind": intervention.local_reflex_kind,
            "llm_called": False,
            "llm_call_status": "not_called",
            "pi_provider_attempted": False,
            "valid_until_ms": valid_until_ms,
        },
        request=request,
        origin="local_reflex",
        status="intervention",
    )


def _coach_candidate_evidence_paragraphs(
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, ...]:
    """Expose only candidate-referenced retrieval evidence to the direct lane."""

    prompt_ids = {
        item.id for item in (*request.context_paragraphs, *request.new_paragraphs)
    }
    ordered_ids: list[str] = []
    for candidate in realtime_coach_candidate_events(request):
        for paragraph_id in candidate.evidence_segment_ids:
            if paragraph_id not in prompt_ids and paragraph_id not in ordered_ids:
                ordered_ids.append(paragraph_id)
    paragraphs_by_id = request.paragraphs_by_id
    return tuple(
        paragraph
        for paragraph_id in ordered_ids[:8]
        if (paragraph := paragraphs_by_id.get(paragraph_id)) is not None
    )


def should_run_realtime_coach(
    request: RealtimeIntelligenceRequest,
    *,
    requested_runtime: str = "direct",
) -> bool:
    """Gate Pi on a likely coaching moment instead of every microphone turn.

    Pi is useful for stateful decisions, not for paraphrasing ordinary speech.
    The lexical gate is intentionally permissive and only controls whether the
    expensive coach lane runs; ASR and the normal intelligence lane are left
    untouched. System audio remains eligible for the direct lane because it is
    the strongest signal that a remote question or commitment just happened.
    """

    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    # A user-request trigger is an explicit coaching command and may contain
    # no fresh transcript. It must not be forced through the lexical delta
    # gate, otherwise the production route silently drops valid requests.
    if request.trigger_type in {"user_request", "task_due"}:
        return True
    source_tracks = {item.source_track for item in request.new_paragraphs}
    if str(requested_runtime or "direct").strip().lower() == "pi":
        if not source_tracks & {"system_audio", "microphone"}:
            return False
        return bool(realtime_coach_candidate_events(request))
    return "system_audio" in source_tracks


def build_realtime_coach_messages(
    request: RealtimeIntelligenceRequest,
) -> list[dict[str, str]]:
    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    system = "".join(
        (
            "你是仅服务于软件使用者的实时私人对话教练。只返回 JSON，不要输出 Markdown。",
            "任务不是总结、复盘或重复原话，而是判断刚发生的时刻是否值得立刻给用户一句可说出口的建议。",
            "基础介入类型包括：question_to_user（对方问题尚未完整回答）、commitment_risk（用户可能形成缺少条件的承诺）、",
            "goal_at_risk（用户目标即将被跳过）、contradiction（当前说法与前文存在有损决策的冲突）、",
            "communication_clarity（持续表达出现重复、失焦或缺少结论，此刻需要收束或重组下一句话）。",
            "当前场景技能包可以增加一种受限介入类型；只能使用 output_contract 列出的类型，并严格执行技能包检查项。",
            "communication_clarity 必须由至少两处逐字原话证明持续模式，不能针对单句措辞或 ASR 错字；建议必须给出下一句或具体结构，不得只做批评或泛泛而谈。",
            "system_audio/remote_mix 通常来自电脑中对方的混音；microphone/self_or_room 不能确定就是用户本人。",
            "涉及承诺、立场或责任时，不得仅凭 microphone/self_or_room 归因给用户本人。",
            "correction_status 为 pending、processing 或 failed_preserved_original 时，原话仍是待核实证据；只能建议澄清，不得据此断言负责人、期限、数字、完成状态或技术名词。",
            "但 communication_clarity 可以直接使用持续的 microphone/self_or_room 原话判断当前可听见的表达模式；建议只针对表达本身，不推断说话者身份，也不能仅因身份未确认而保持静默。",
            "candidate_evidence_paragraphs 只包含当前候选引用的极少量较早原话；引用时仍必须复制其 id 和逐字原文。",
            "对 communication_clarity 而言，听众抓不住核心观点或错过及时收束结论属于具体损失，只要此刻能用一句下一步表达修正，就具有介入价值。",
            "没有高价值、可执行且仍来得及的介入时 intervention 必须是 null。不要为了显得有帮助而生成提示。",
            "不得猜测说话人身份、公司信息、数字、期限、关键技术名词或用户立场；title、reason 和 recommendation 中的实质事实都必须逐字来自 evidence_quote，不能借用同一引用段落里未逐字引用的其他内容。",
            "recommendation 必须是 8 到 120 个字符的可直接说出口短句；不要写分析过程。",
        )
    ) + coach_skill_prompt(request.coach_skill_id)
    candidate_evidence = _coach_candidate_evidence_paragraphs(request)
    payload = {
        "state_revision": request.state_revision,
        "new_paragraphs": [item.to_prompt_dict() for item in request.new_paragraphs],
        "context_paragraphs": [item.to_prompt_dict() for item in request.context_paragraphs],
        "candidate_evidence_paragraphs": [
            item.to_prompt_dict() for item in candidate_evidence
        ],
        "semantic_windows": [item.to_prompt_dict() for item in request.semantic_windows],
        "rolling_state": request.rolling_state,
        "meeting_goal": request.meeting_goal,
        "coach_skill_id": request.coach_skill_id,
        "output_contract": {
            "intervention": {
                "event_type": "|".join(sorted(coach_skill_event_types(request.coach_skill_id))),
                "title": "short_string",
                "recommendation": "directly_speakable_string",
                "reason": "short_string",
                "evidence_segment_ids": "non_empty_array: input paragraph ids",
                "evidence_quote": "non_empty verbatim substring of referenced paragraphs",
                "urgency": "low|medium|high",
                "confidence": "number: 0..1",
            },
            "empty_result": {"intervention": None},
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    ]


def parse_realtime_coach_response(
    content: Any,
    *,
    request: RealtimeIntelligenceRequest,
) -> CoachIntervention | None:
    payload = _decode_json_object(content)
    unexpected = set(payload) - {"intervention"}
    if unexpected:
        raise IntelligenceResponseValidationError(
            f"coach response contains unsupported fields: {', '.join(sorted(unexpected))}"
        )
    value = payload.get("intervention")
    if value is None:
        return None
    item = _required_object(value, "intervention")
    event_type = _required_response_text(item.get("event_type"), "intervention.event_type", maximum=40)
    if event_type not in coach_skill_event_types(request.coach_skill_id):
        raise IntelligenceResponseValidationError("intervention.event_type is unsupported")
    evidence_ids = _parse_evidence_ids(
        item.get("evidence_segment_ids"),
        request=request,
        field="intervention",
    )
    evidence_quote = _required_response_text(
        item.get("evidence_quote"),
        "intervention.evidence_quote",
        maximum=1_000,
    )
    _validate_evidence_quote(
        evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
        field="intervention.evidence_quote",
    )
    if (
        event_type == "communication_clarity"
        and len([line for line in evidence_quote.splitlines() if _normalize_for_evidence(line)]) < 2
    ):
        raise IntelligenceResponseValidationError(
            "communication_clarity requires two verbatim evidence fragments",
            category="evidence",
        )
    urgency = _required_response_text(item.get("urgency"), "intervention.urgency", maximum=20)
    if urgency not in _URGENCY_VALUES:
        raise IntelligenceResponseValidationError("intervention.urgency is unsupported")
    recommendation = _response_alias_text(
        item,
        primary_key="recommendation",
        alias_keys=("say_this", "sayThis"),
        field="intervention.recommendation",
        alias_field="intervention.say_this",
        maximum=120,
    )
    if len(recommendation) < 8:
        raise IntelligenceResponseValidationError("intervention.recommendation is too short")
    title = _required_response_text(item.get("title"), "intervention.title", maximum=80)
    reason = _response_alias_text(
        item,
        primary_key="reason",
        alias_keys=("why_now", "whyNow"),
        field="intervention.reason",
        alias_field="intervention.why_now",
        maximum=300,
    )
    _validate_coach_claim_grounding(
        title=title,
        recommendation=recommendation,
        reason=reason,
        evidence_quote=evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
    )
    return CoachIntervention(
        event_type=event_type,
        title=title,
        recommendation=recommendation,
        reason=reason,
        evidence_segment_ids=evidence_ids,
        evidence_quote=evidence_quote,
        urgency=urgency,
        confidence=_response_confidence(item.get("confidence"), "intervention.confidence"),
    )


async def run_realtime_coach(
    *,
    request: RealtimeIntelligenceRequest,
    provider: Any,
    before_attempt: Any = None,
    on_usage: Any = None,
) -> dict[str, Any]:
    """Run the focused coach lane without coupling its failure to fact extraction."""

    if not hasattr(provider, "complete"):
        raise TypeError("provider must expose an async complete method")
    await _notify_callback(before_attempt, 1)
    result = await provider.complete(
        build_realtime_coach_messages(request),
        idempotency_key=f"{_coach_run_id(request)}:v1",
        temperature=0.1,
        max_completion_tokens=768,
    )
    await _notify_callback(on_usage, _usage_dict(result.usage), 1)
    intervention = parse_realtime_coach_response(result.content, request=request)
    if intervention is not None and intervention.confidence < 0.78:
        intervention = None
    return _with_coach_provenance(
        {
            "intervention": intervention,
            "transport_mode": result.transport_mode.value,
            "ttft_ms": result.timings.time_to_first_token_seconds * 1_000,
            "decision_latency_ms": max(
                0.0,
                float(result.timings.completed_at - result.timings.started_at) * 1_000,
            ),
            "usage": _usage_dict(result.usage),
            "model": result.model,
            "response_id": result.response_id,
            "finish_reason": result.finish_reason,
        },
        request=request,
        origin="direct_intelligence",
    )


def _coach_run_id(request: RealtimeIntelligenceRequest) -> str:
    """Return a stable ID for one coach evaluation input.

    The application owns job IDs.  This ID instead identifies the bounded
    evidence/state input so diagnostics and projections can correlate a coach
    decision before an application job has been persisted.
    """

    canonical = {
        "intelligence_input": realtime_intelligence_idempotency_key(request),
        "candidate_evidence": [
            {"id": item.id, "revision": item.revision, "text": item.text}
            for item in _coach_candidate_evidence_paragraphs(request)
        ],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"realtime-intelligence:{digest[:40]}:coach"


def _coach_evidence_revision(request: RealtimeIntelligenceRequest) -> str:
    """Return an opaque version for the exact transcript batch the coach evaluated."""

    canonical = {
        "state_revision": request.state_revision,
        "new_paragraphs": [
            {"id": item.id, "revision": item.revision, "text": item.text}
            for item in request.new_paragraphs
        ],
        "candidate_evidence": [
            {"id": item.id, "revision": item.revision, "text": item.text}
            for item in _coach_candidate_evidence_paragraphs(request)
        ],
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"coach-evidence:{request.state_revision}:{digest[:24]}"


def _coach_decision_id(
    *,
    request: RealtimeIntelligenceRequest,
    origin: str,
    status: str,
    intervention: CoachIntervention | None,
) -> str:
    """Return an opaque ID for the exact decision, including protected silence."""

    canonical = {
        "run_id": _coach_run_id(request),
        "origin": origin,
        "status": status,
        "intervention": intervention.to_dict() if intervention is not None else None,
    }
    digest = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"coach-decision:{digest[:32]}"


def _default_coach_silent_reason(*, status: str) -> str:
    if status == "not_triggered":
        return "当前输入未达到实时教练触发门槛，未启动教练判断。"
    if status == "stale":
        return "本轮建议只引用了过期证据，已避免重复提醒。"
    if status == "timed_out":
        return "实时教练本轮超出响应预算，已保持静默。"
    if status == "failed":
        return "实时教练本轮未能形成可靠建议，已保持静默。"
    return "本轮信息不足以形成高置信度、可立即执行的建议，已保持静默。"


def _with_coach_provenance(
    result: Mapping[str, Any],
    *,
    request: RealtimeIntelligenceRequest,
    origin: str,
    status: str | None = None,
    status_reason: str | None = None,
    provider_lane: str = "realtime",
) -> dict[str, Any]:
    """Attach the routed-coach audit contract without removing legacy fields."""

    if origin not in _COACH_ORIGINS:
        raise ValueError("coach origin is unsupported")
    existing_status = str(result.get("status") or "").strip()
    resolved_status = (
        status or existing_status or ("intervention" if result.get("intervention") is not None else "protected_silent")
    )
    if resolved_status not in _COACH_DECISION_STATUSES:
        raise ValueError("coach decision status is unsupported")
    intervention = result.get("intervention")
    if intervention is not None and not isinstance(intervention, CoachIntervention):
        raise TypeError("coach result intervention must be a CoachIntervention or None")
    if resolved_status == "intervention" and intervention is None:
        raise ValueError("an intervention status requires a CoachIntervention")
    if resolved_status != "intervention" and intervention is not None:
        raise ValueError("a non-intervention status cannot contain a CoachIntervention")
    decision_reason = str(result.get("decision_reason") or "").strip()[:160] or None
    if resolved_status != "intervention" and decision_reason is None:
        decision_reason = _default_coach_silent_reason(status=resolved_status)
    existing_status_reason = str(result.get("status_reason") or "").strip() or None
    resolved_status_reason = (
        status_reason
        or existing_status_reason
        or (
            "intervention_submitted"
            if resolved_status == "intervention"
            else "trigger_gate"
            if resolved_status == "not_triggered"
            else "stale_evidence"
            if resolved_status == "stale"
            else "provider_timeout"
            if resolved_status == "timed_out"
            else "runtime_failure"
            if resolved_status == "failed"
            else "no_actionable_intervention"
        )
    )
    return {
        **dict(result),
        "provenance_version": COACH_PROVENANCE_VERSION,
        "run_id": _coach_run_id(request),
        "decision_id": _coach_decision_id(
            request=request,
            origin=origin,
            status=resolved_status,
            intervention=result.get("intervention"),
        ),
        "evidence_revision": _coach_evidence_revision(request),
        "provider_lane": str(provider_lane or "realtime"),
        "origin": origin,
        "status": resolved_status,
        "status_reason": resolved_status_reason,
        "decision_reason": decision_reason,
    }


def build_realtime_coach_provenance_decision(
    *,
    request: RealtimeIntelligenceRequest,
    origin: str,
    status: str,
    status_reason: str | None = None,
    decision_reason: str | None = None,
    intervention: CoachIntervention | None = None,
) -> dict[str, Any]:
    """Build a coach decision when app orchestration does not run a provider.

    This public helper is for trigger-gate and exception paths. It intentionally
    shares the same run, decision, and evidence IDs as completed coach runners,
    while avoiding wall-clock fields so retries remain comparable.
    """

    if not isinstance(request, RealtimeIntelligenceRequest):
        raise TypeError("request must be a RealtimeIntelligenceRequest")
    return _with_coach_provenance(
        {"intervention": intervention, "decision_reason": decision_reason},
        request=request,
        origin=origin,
        status=status,
        status_reason=status_reason,
    )


async def run_realtime_coach_via_pi(
    *,
    request: RealtimeIntelligenceRequest,
    pi_runtime: Any,
    provider_config: Mapping[str, Any],
    candidate_events: Sequence[Mapping[str, Any]] | None = None,
    before_attempt: Any = None,
    on_usage: Any = None,
    priority_mode: str = "realtime",
) -> dict[str, Any]:
    """Run the same evidence contract through the restricted Pi sidecar."""

    if not hasattr(pi_runtime, "evaluate"):
        raise TypeError("pi_runtime must expose an async evaluate method")
    await _notify_callback(before_attempt, 1)
    request_id = f"{_coach_run_id(request)}:pi:v1"
    payload = build_pi_coach_request(
        request,
        request_id=request_id,
        base_url=provider_config.get("base_url"),
        api_key=provider_config.get("api_key"),
        model=provider_config.get("model"),
        api_style=provider_config.get("api_style") or "chat_completions",
        timeout_seconds=float(provider_config.get("timeout_seconds") or 25.0),
        candidate_events=candidate_events,
        priority_mode=priority_mode,
    )
    result = await pi_runtime.evaluate(payload)
    if isinstance(pi_runtime, PiCoachSidecar) and result.get("host_evidence"):
        registered = tuple(
            IntelligenceParagraph.from_payload(
                pi_runtime._host_evidence_paragraph(row), field="host_evidence",
            ) for row in result["host_evidence"]
        )
        original = {p.id: p for p in (*request.retrieval_paragraphs,
                                      *request.context_paragraphs, *request.new_paragraphs)}
        for paragraph in registered:
            previous = original.get(paragraph.id)
            if previous and (previous.text != paragraph.text or previous.revision != paragraph.revision):
                raise ValueError("host evidence conflicts with the request revision")
        request = replace(request, retrieval_paragraphs=request.retrieval_paragraphs + registered)
    metrics = dict(result.get("metrics")) if isinstance(result.get("metrics"), Mapping) else {}
    usage = metrics.get("usage") if isinstance(metrics.get("usage"), Mapping) else None
    await _notify_callback(on_usage, dict(usage) if usage is not None else None, 1)
    intervention = parse_realtime_coach_response(
        json.dumps({"intervention": result.get("intervention")}, ensure_ascii=False),
        request=request,
    )
    if intervention is not None and intervention.confidence < 0.78:
        intervention = None
    decision_reason = str(result.get("decision_reason") or "")[:160] or None
    allowed_evidence_ids = (
        request.writable_paragraph_ids
        if request.trigger_type not in {"user_request", "task_due"}
        else frozenset(request.paragraphs_by_id)
    )
    if intervention is not None and set(intervention.evidence_segment_ids).isdisjoint(allowed_evidence_ids):
        intervention = None
        metrics.update(
            {
                "intervention_suppressed": True,
                "suppression_reason": "stale_evidence",
            }
        )
        decision_reason = "Pi 建议未引用本轮新内容，已抑制重复提醒，保留上一条有依据建议。"
    raw_decision_latency_ms = metrics.get("decision_latency_ms")
    if raw_decision_latency_ms is None:
        raw_decision_latency_ms = metrics.get("elapsed_ms")
    decision_latency_ms = _non_negative_finite_metric(raw_decision_latency_ms)
    # Only an explicitly observed streamed token may populate TTFT. Never use
    # the full decision duration as a substitute for a missing observation.
    ttft_ms = _non_negative_finite_metric(metrics.get("ttft_ms"))
    if (
        ttft_ms is not None
        and decision_latency_ms is not None
        and ttft_ms > decision_latency_ms
    ):
        ttft_ms = None
    timings = _pi_decision_timings(metrics)
    return _with_coach_provenance(
        {
            "intervention": intervention,
            "transport_mode": "pi_agent_jsonl",
            "ttft_ms": ttft_ms,
            "decision_latency_ms": decision_latency_ms,
            "timings": timings,
            "usage": dict(usage) if usage is not None else None,
            "model": str(provider_config.get("model") or ""),
            "response_id": request_id,
            "finish_reason": str(result.get("action") or ""),
            "decision_reason": decision_reason,
            "agent_metrics": metrics,
            "host_evidence": result.get("host_evidence", []) if isinstance(pi_runtime, PiCoachSidecar) else [],
        },
        request=request,
        origin="pi",
        provider_lane="pi_deep" if priority_mode == "deep" else "realtime",
        status="stale" if metrics.get("intervention_suppressed") else None,
        status_reason=("stale_evidence" if metrics.get("intervention_suppressed") else None),
    )


def _non_negative_finite_metric(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    normalized = float(value)
    return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _pi_decision_timings(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the bridge's observed wall-clock chain without inventing stages."""

    raw = metrics.get("timings")
    timing_values = raw if isinstance(raw, Mapping) else {}
    clock = str(timing_values.get("clock") or "").strip()
    started_at_ms = _non_negative_finite_metric(timing_values.get("started_at_ms"))
    first_token_at_ms = _non_negative_finite_metric(timing_values.get("first_token_at_ms"))
    completed_at_ms = _non_negative_finite_metric(timing_values.get("completed_at_ms"))
    if clock != "unix_epoch_ms":
        started_at_ms = None
        completed_at_ms = None
        first_token_at_ms = None
    elif started_at_ms is None or completed_at_ms is None or completed_at_ms < started_at_ms:
        started_at_ms = None
        completed_at_ms = None
        first_token_at_ms = None
    elif first_token_at_ms is not None and not started_at_ms <= first_token_at_ms <= completed_at_ms:
        first_token_at_ms = None
    return {
        "clock": "unix_epoch_ms" if clock == "unix_epoch_ms" else None,
        "started_at_ms": started_at_ms,
        "first_token_at_ms": first_token_at_ms,
        "completed_at_ms": completed_at_ms,
    }


def _pi_fallback_reason(error: Exception) -> str:
    """Classify Pi failures without reflecting provider payloads or credentials."""

    message = str(error or "").strip().lower()
    code = str(getattr(error, "code", type(error).__name__))[:120]
    if any(token in message for token in ("temporarily unavailable", "service unavailable", "upstream")):
        return "provider_temporarily_unavailable"
    if "timed out" in message or "timeout" in message or code in {
        "agent_deadline_exceeded",
        "pi_timeout",
        "pi_startup_timeout",
    }:
        return "provider_timeout"
    if code in {"pi_unavailable", "pi_spawn_failed", "pi_transport_error"}:
        return "runtime_unavailable"
    if "protocol" in code or "protocol" in message:
        return "protocol_error"
    return code


def _can_fall_back_before_model_work(error: Exception) -> bool:
    """Avoid a second model call after Pi already consumed the realtime budget."""

    return str(getattr(error, "code", "")) in {
        "pi_unavailable",
        "pi_spawn_failed",
        "pi_startup_timeout",
        "pi_transport_error",
    }


def _silent_pi_failure(
    *,
    request: RealtimeIntelligenceRequest,
    provider_config: Mapping[str, Any],
    error: Exception,
    priority_mode: str = "realtime",
) -> dict[str, Any]:
    reason = _pi_fallback_reason(error)
    failure_metrics = (
        dict(getattr(error, "metrics"))
        if isinstance(getattr(error, "metrics", None), Mapping)
        else {}
    )
    # Keep validation failures diagnosable without archiving provider output.
    # The exception text contains only a bounded field/category reason; it does
    # not include the model response or meeting transcript.
    if isinstance(error, IntelligenceResponseValidationError):
        failure_metrics.update(
            {
                "response_validation_category": str(error.category or "structural")[:40],
                "response_validation_error": str(error)[:240],
            }
        )
    failure_metrics["fallback_suppressed"] = True
    return _with_coach_provenance(
        {
            "intervention": None,
            "transport_mode": "pi_agent_jsonl",
            "ttft_ms": None,
            "decision_latency_ms": None,
            "timings": _pi_decision_timings({}),
            "usage": None,
            "model": str(provider_config.get("model") or ""),
            "response_id": f"{_coach_run_id(request)}:pi:v1",
            "finish_reason": "failed_open_silent",
            "decision_reason": "实时教练本轮超出响应预算，已保持静默。"
            if reason == "provider_timeout"
            else "实时教练本轮未能形成可靠建议，已保持静默。",
            "agent_metrics": failure_metrics,
            "runtime_requested": "pi",
            "runtime_used": "pi",
            "fallback_error_code": str(getattr(error, "code", type(error).__name__))[:120],
            "fallback_reason": reason,
        },
        request=request,
        origin="pi",
        status="timed_out" if reason == "provider_timeout" else "failed",
        status_reason=reason,
        provider_lane="pi_deep" if priority_mode == "deep" else "realtime",
    )


async def run_realtime_coach_routed(
    *,
    request: RealtimeIntelligenceRequest,
    provider: Any,
    requested_runtime: str,
    pi_runtime: Any = None,
    pi_provider_config: Mapping[str, Any] | None = None,
    candidate_events: Sequence[Mapping[str, Any]] | None = None,
    before_attempt: Any = None,
    on_usage: Any = None,
    soft_deadline_at_ms: int | None = None,
    max_provider_timeout_ms: int | None = None,
    priority_mode: str = "realtime",
) -> dict[str, Any]:
    """Select direct or Pi execution and fail back to direct on Pi errors."""

    normalized_runtime = str(requested_runtime or "direct").strip().lower()
    if normalized_runtime == "pi":
        effective_pi_config = dict(pi_provider_config or {})
        if max_provider_timeout_ms is not None:
            bounded_timeout_ms = max(1, int(max_provider_timeout_ms))
            configured_timeout_ms = max(
                1,
                int(float(effective_pi_config.get("timeout_seconds") or 25.0) * 1_000),
            )
            effective_pi_config["timeout_seconds"] = min(
                configured_timeout_ms,
                bounded_timeout_ms,
            ) / 1_000
        try:
            result = await run_realtime_coach_via_pi(
                request=request,
                pi_runtime=pi_runtime,
                provider_config=effective_pi_config,
                candidate_events=candidate_events,
                before_attempt=before_attempt,
                on_usage=on_usage,
                priority_mode=priority_mode,
            )
            return _with_coach_provenance(
                {
                    **result,
                    "runtime_requested": "pi",
                    "runtime_used": "pi",
                    "fallback_error_code": None,
                    "fallback_reason": None,
                },
                request=request,
                origin="pi",
                provider_lane="pi_deep" if priority_mode == "deep" else "realtime",
            )
        except Exception as exc:
            # Once the product window has closed, a transport/startup error
            # must not trigger a second request against the same gateway. The
            # caller still receives a durable, provenance-safe timeout marker.
            if (
                soft_deadline_at_ms is not None
                and time.time_ns() // 1_000_000 >= int(soft_deadline_at_ms)
            ):
                result = _silent_pi_failure(
                    request=request,
                    provider_config=effective_pi_config,
                    error=exc,
                    priority_mode=priority_mode,
                )
                result.update(
                    {
                        "status": "timed_out",
                        "status_reason": "soft_deadline_exceeded",
                        "fallback_reason": "soft_deadline_exceeded",
                        "delivery_status": "too_late",
                        "soft_deadline_at_ms": int(soft_deadline_at_ms),
                        "soft_cutoff_triggered": True,
                        "late_result_discarded": True,
                    }
                )
                metrics = dict(result.get("agent_metrics") or {})
                metrics.update(
                    {
                        "soft_deadline_at_ms": int(soft_deadline_at_ms),
                        "soft_cutoff_triggered": True,
                        "late_result_discarded": True,
                    }
                )
                result["agent_metrics"] = metrics
                return result
            if _can_fall_back_before_model_work(exc):
                result = await run_realtime_coach(
                    request=request,
                    provider=provider,
                    before_attempt=before_attempt,
                    on_usage=on_usage,
                )
                return _with_coach_provenance(
                    {
                        **result,
                        "runtime_requested": "pi",
                        "runtime_used": "direct",
                        "fallback_error_code": str(getattr(exc, "code", type(exc).__name__))[:120],
                        "fallback_reason": _pi_fallback_reason(exc),
                    },
                    request=request,
                    origin="direct_fallback",
                    provider_lane="pi_deep" if priority_mode == "deep" else "realtime",
                )
            return _silent_pi_failure(
                request=request,
                provider_config=dict(pi_provider_config or {}),
                error=exc,
                priority_mode=priority_mode,
            )
    result = await run_realtime_coach(
        request=request,
        provider=provider,
        before_attempt=before_attempt,
        on_usage=on_usage,
    )
    return _with_coach_provenance(
        {
            **result,
            "runtime_requested": "direct",
            "runtime_used": "direct",
            "fallback_error_code": None,
            "fallback_reason": None,
        },
        request=request,
        origin="direct_intelligence",
    )


def apply_coach_intervention(
    response: RealtimeIntelligenceResponse,
    intervention: CoachIntervention | None,
) -> RealtimeIntelligenceResponse:
    if not isinstance(response, RealtimeIntelligenceResponse):
        raise TypeError("response must be a RealtimeIntelligenceResponse")
    if intervention is None:
        return response
    if not isinstance(intervention, CoachIntervention):
        raise TypeError("intervention must be a CoachIntervention or None")
    return replace(response, follow_up=intervention.to_follow_up())


def build_realtime_intelligence_repair_messages(
    request: RealtimeIntelligenceRequest,
    *,
    invalid_content: Any,
    validation_error: IntelligenceResponseValidationError,
) -> list[dict[str, str]]:
    """Ask the provider once to repair structure without weakening evidence checks."""

    messages = build_realtime_intelligence_messages(request)
    bounded_content = str(invalid_content or "")[:MAX_REPAIR_SOURCE_CHARACTERS]
    messages.extend(
        [
            {"role": "assistant", "content": bounded_content},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": "repair_previous_response",
                        "validation_error": str(validation_error),
                        "rules": [
                            "Return only the complete JSON object required above.",
                            "Copy paragraph ids and revisions exactly from the original input.",
                            "Delete any item that cannot satisfy the evidence quote barrier.",
                            "Do not add facts, evidence, decisions, risks, owners, or deadlines.",
                        ],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ]
    )
    return messages


async def run_realtime_intelligence(
    *,
    request: RealtimeIntelligenceRequest,
    provider: Any,
    on_delta: Any = None,
    before_attempt: Any = None,
    on_usage: Any = None,
) -> dict[str, Any]:
    """Run one bounded provider call and validate it before returning semantics."""

    if not hasattr(provider, "complete"):
        raise TypeError("provider must expose an async complete method")
    output_token_limit = dynamic_output_token_limit(request.input_characters)
    await _notify_callback(before_attempt, 1)
    result = await provider.complete(
        build_realtime_intelligence_messages(request),
        on_delta=on_delta,
        idempotency_key=realtime_intelligence_idempotency_key(request),
        temperature=0.1,
        max_completion_tokens=output_token_limit,
    )
    first_result = result
    await _notify_callback(on_usage, _usage_dict(first_result.usage), 1)
    first_validation_error: IntelligenceResponseValidationError | None = None
    try:
        response = parse_realtime_intelligence_response(result.content, request=request)
    except IntelligenceResponseValidationError as exc:
        if exc.category not in {"structural", "truncated", "evidence"}:
            raise
        first_validation_error = exc
        await _notify_callback(before_attempt, 2)
        result = await provider.complete(
            build_realtime_intelligence_repair_messages(
                request,
                invalid_content=first_result.content,
                validation_error=exc,
            ),
            idempotency_key=f"{realtime_intelligence_idempotency_key(request)}:repair:v1",
            temperature=0,
            max_completion_tokens=output_token_limit,
        )
        await _notify_callback(on_usage, _usage_dict(result.usage), 2)
        response = parse_realtime_intelligence_response(result.content, request=request)
    usage = _combined_usage(first_result.usage, result.usage if result is not first_result else None)
    return {
        "response": response,
        "idempotency_key": realtime_intelligence_idempotency_key(request),
        "transport_mode": result.transport_mode.value,
        "fallback_reason": result.fallback_reason,
        "ttft_ms": first_result.timings.time_to_first_token_seconds * 1_000,
        "repair_ttft_ms": (
            result.timings.time_to_first_token_seconds * 1_000 if first_validation_error is not None else None
        ),
        "provider_attempt_count": 2 if first_validation_error is not None else 1,
        "repair_attempted": first_validation_error is not None,
        "timings": {
            "started_at": first_result.timings.started_at,
            "connected_at": first_result.timings.connected_at,
            "first_token_at": first_result.timings.first_token_at,
            "completed_at": result.timings.completed_at,
        },
        "usage": usage,
        "response_id": result.response_id,
        "model": result.model,
        "finish_reason": result.finish_reason,
    }


def _combined_usage(first: Any, second: Any = None) -> dict[str, int] | None:
    usages = [item for item in (first, second) if item is not None]
    if not usages:
        return None
    return {
        "prompt_tokens": sum(int(item.prompt_tokens) for item in usages),
        "completion_tokens": sum(int(item.completion_tokens) for item in usages),
        "total_tokens": sum(int(item.total_tokens) for item in usages),
    }


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


async def _notify_callback(callback: Any, *args: Any) -> None:
    if callback is None:
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


def _parse_paragraph_revision(
    value: Any,
    *,
    request: RealtimeIntelligenceRequest,
    index: int,
) -> ParagraphRevision:
    field = f"paragraph_revisions[{index}]"
    item = _required_object(value, field)
    target_id = _required_response_text(item.get("target_id"), f"{field}.target_id", maximum=200)
    if target_id not in request.writable_paragraph_ids:
        raise IntelligenceResponseValidationError(
            f"{field}.target_id does not reference a writable new paragraph",
            category="stale",
        )
    paragraph = request.paragraphs_by_id[target_id]
    expected_revision = _response_positive_integer(
        item.get("expected_revision"),
        f"{field}.expected_revision",
    )
    if expected_revision != paragraph.revision:
        raise IntelligenceResponseValidationError(
            f"{field} targets a stale paragraph revision",
            category="stale",
        )
    corrected_text = _required_response_text(
        item.get("corrected_text"),
        f"{field}.corrected_text",
        maximum=MAX_PARAGRAPH_CHARACTERS,
    )
    change_count = _response_non_negative_integer(item.get("change_count"), f"{field}.change_count")
    changed = _normalize_for_evidence(corrected_text) != _normalize_for_evidence(paragraph.text)
    if changed != (change_count > 0):
        raise IntelligenceResponseValidationError(
            f"{field}.change_count does not match the actual text change",
            category="semantic_safety",
        )
    if changed and not correction_is_safe(paragraph.text, corrected_text):
        raise IntelligenceResponseValidationError(
            f"{field}.corrected_text failed the fact-preservation safety gate",
            category="semantic_safety",
        )
    return ParagraphRevision(
        target_id=target_id,
        expected_revision=expected_revision,
        corrected_text=corrected_text,
        change_count=change_count,
        changed=changed,
    )


def _parse_topic_update(
    value: Any,
    *,
    request: RealtimeIntelligenceRequest,
) -> TopicUpdate | None:
    if value is None:
        return None
    item = _required_object(value, "topic_update")
    operation = _required_response_text(item.get("operation"), "topic_update.operation", maximum=20)
    if operation not in _TOPIC_OPERATIONS:
        raise IntelligenceResponseValidationError("topic_update.operation is unsupported")
    if operation == "noop":
        return None
    title = _required_response_text(item.get("title"), "topic_update.title", maximum=160)
    summary = _required_response_text(item.get("summary"), "topic_update.summary", maximum=1_200)
    evidence_ids = _parse_evidence_ids(item.get("evidence_segment_ids"), field="topic_update", request=request)
    evidence_quote = _required_response_text(
        item.get("evidence_quote"),
        "topic_update.evidence_quote",
        maximum=1_000,
    )
    _validate_evidence_quote(
        evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
        field="topic_update.evidence_quote",
    )
    return TopicUpdate(
        operation=operation,
        title=title,
        summary=summary,
        evidence_segment_ids=evidence_ids,
        evidence_quote=evidence_quote,
    )


def _parse_state_change(
    value: Any,
    *,
    request: RealtimeIntelligenceRequest,
    index: int,
) -> StateChange:
    field = f"state_changes[{index}]"
    item = _required_object(value, field)
    kind = _required_response_text(item.get("type"), f"{field}.type", maximum=30)
    if kind not in _STATE_KINDS:
        raise IntelligenceResponseValidationError(f"{field}.type is unsupported")
    operation = _required_response_text(item.get("operation"), f"{field}.operation", maximum=20)
    if operation not in _STATE_OPERATIONS:
        raise IntelligenceResponseValidationError(f"{field}.operation is unsupported")
    if operation == "noop":
        raise IntelligenceResponseValidationError("state change noop must be omitted from state_changes")
    item_id = _required_response_text(item.get("item_id"), f"{field}.item_id", maximum=200)
    content = _required_response_text(item.get("content"), f"{field}.content", maximum=2_000)
    owner = _optional_response_text(item.get("owner"), f"{field}.owner", maximum=200)
    deadline = _optional_response_text(item.get("deadline"), f"{field}.deadline", maximum=200)
    status = _optional_response_text(item.get("status"), f"{field}.status", maximum=80)
    evidence_ids = _parse_evidence_ids(item.get("evidence_segment_ids"), field=field, request=request)
    evidence_quote = _required_response_text(
        item.get("evidence_quote"),
        f"{field}.evidence_quote",
        maximum=1_000,
    )
    _validate_evidence_quote(
        evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
        field=f"{field}.evidence_quote",
    )
    confidence = _response_confidence(item.get("confidence"), f"{field}.confidence")
    return StateChange(
        kind=kind,
        operation=operation,
        item_id=item_id,
        content=content,
        owner=owner,
        deadline=deadline,
        status=status,
        evidence_segment_ids=evidence_ids,
        evidence_quote=evidence_quote,
        confidence=confidence,
    )


def _parse_follow_up(
    value: Any,
    *,
    request: RealtimeIntelligenceRequest,
) -> FollowUp | None:
    if value is None:
        return None
    if value == []:
        return None
    item = _required_object(value, "follow_up")
    question = _required_response_text(item.get("question"), "follow_up.question", maximum=300)
    reason = _required_response_text(item.get("reason"), "follow_up.reason", maximum=500)
    evidence_ids = _parse_evidence_ids(
        item.get("evidence_segment_ids"),
        field="follow_up",
        request=request,
    )
    evidence_quote = _required_response_text(
        item.get("evidence_quote"),
        "follow_up.evidence_quote",
        maximum=1_000,
    )
    _validate_evidence_quote(
        evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
        field="follow_up.evidence_quote",
    )
    urgency = _required_response_text(item.get("urgency"), "follow_up.urgency", maximum=20)
    if urgency not in _URGENCY_VALUES:
        raise IntelligenceResponseValidationError("follow_up.urgency is unsupported")
    return FollowUp(
        question=question,
        reason=reason,
        evidence_segment_ids=evidence_ids,
        evidence_quote=evidence_quote,
        urgency=urgency,
    )


def _parse_evidence_ids(
    value: Any,
    *,
    field: str,
    request: RealtimeIntelligenceRequest,
) -> tuple[str, ...]:
    raw_ids = _required_array(value, f"{field}.evidence_segment_ids")
    if not raw_ids:
        raise IntelligenceResponseValidationError(f"{field}.evidence_segment_ids must not be empty")
    evidence_ids = tuple(
        _required_response_text(item, f"{field}.evidence_segment_ids", maximum=200) for item in raw_ids
    )
    if len(set(evidence_ids)) != len(evidence_ids):
        raise IntelligenceResponseValidationError(f"{field}.evidence_segment_ids contains duplicates")
    unknown = set(evidence_ids) - set(request.paragraphs_by_id)
    if unknown:
        raise IntelligenceResponseValidationError(
            f"{field} references unknown evidence paragraphs",
            category="evidence",
        )
    return evidence_ids


def _coach_evidence_quality(paragraph: IntelligenceParagraph) -> str:
    if (
        paragraph.correction_status in _COACH_PROVISIONAL_ASR_STATUSES
        or _COACH_ORPHAN_LATIN_RE.search(paragraph.text)
    ):
        return "provisional"
    if paragraph.correction_status in {"changed", "no_change"}:
        return "reviewed"
    return "unknown"


def _normalize_claim_token(value: str) -> str:
    return "".join(str(value or "").casefold().split()).replace("％", "%")


def _claim_tokens(pattern: re.Pattern[str], value: str) -> set[str]:
    return {
        normalized
        for match in pattern.finditer(str(value or ""))
        if (normalized := _normalize_claim_token(match.group(0)))
    }


def _number_claim_tokens(value: str) -> set[str]:
    tokens = _claim_tokens(_COACH_NUMBER_RE, value) | _claim_tokens(
        _COACH_CHINESE_NUMBER_RE,
        value,
    )
    tokens.update(
        normalized
        for match in _COACH_CONTEXTUAL_CHINESE_NUMBER_RE.finditer(str(value or ""))
        if (normalized := _normalize_claim_token(match.group("value")))
    )
    return tokens


def _owner_assignment_values(value: str) -> set[str]:
    owners: set[str] = set()
    for pattern in _COACH_OWNER_ASSIGNMENT_RES:
        for match in pattern.finditer(str(value or "")):
            owner = _normalize_claim_token(match.group(1))
            if owner and owner not in {"谁", "哪位", "哪个人", "何人"}:
                owners.add(owner)
    return owners


def _state_polarities(value: str) -> set[str]:
    polarities: set[str] = set()
    if _COACH_NEGATIVE_STATE_RE.search(str(value or "")):
        polarities.add("negative")
    if _COACH_POSITIVE_STATE_RE.search(str(value or "")):
        polarities.add("positive")
    return polarities


def _material_fact_terms(value: str) -> set[str]:
    normalized = str(value or "").casefold()
    return {term for term in _COACH_MATERIAL_FACT_TERMS if term in normalized}


def _glossary_claim_terms(
    value: str,
    *,
    request: RealtimeIntelligenceRequest,
) -> set[str]:
    normalized = _normalize_claim_token(value)
    return {
        term
        for raw_term in request.glossary
        if len(term := _normalize_claim_token(raw_term)) >= 2 and term in normalized
    }


def _quoted_evidence_paragraphs(
    *,
    evidence_quote: str,
    evidence_ids: tuple[str, ...],
    request: RealtimeIntelligenceRequest,
) -> tuple[IntelligenceParagraph, ...]:
    quote_lines = tuple(
        normalized
        for line in evidence_quote.splitlines()
        if (normalized := _normalize_for_evidence(line))
    )
    return tuple(
        paragraph
        for item_id in evidence_ids
        if (paragraph := request.paragraphs_by_id[item_id])
        and any(line in _normalize_for_evidence(paragraph.text) for line in quote_lines)
    )


def _is_clarification_only_recommendation(value: str) -> bool:
    normalized = _normalize_claim_token(value)
    clarification_cues = (
        "请确认",
        "先确认",
        "直接确认",
        "请核实",
        "先核实",
        "请问",
        "追问",
        "询问",
        "问清",
        "谁",
        "哪位",
        "什么时候",
        "是否",
        "能否",
        "待确认",
        "?",
        "？",
    )
    if not any(cue in normalized for cue in clarification_cues):
        return False
    if _owner_assignment_values(value):
        return False
    return not bool(
        re.search(
            r"(?:我|我们|你|你们|团队).{0,12}(?:承诺|保证|一定(?:完成|交付|上线|发布))",
            str(value or ""),
        )
    )


def _card_contains_material_assertion(
    *values: str,
    request: RealtimeIntelligenceRequest,
) -> bool:
    return any(
        _number_claim_tokens(value)
        or _claim_tokens(_COACH_TIME_RE, value)
        or _owner_assignment_values(value)
        or _state_polarities(value)
        or _material_fact_terms(value)
        or _glossary_claim_terms(value, request=request)
        for value in values
    )


def _coach_topic_clauses(value: str) -> tuple[str, ...]:
    return tuple(
        clause.strip()
        for clause in _COACH_TOPIC_BOUNDARY_RE.split(str(value or ""))
        if clause.strip()
    )


def _validate_coach_relationship_scope(
    *,
    title: str,
    recommendation: str,
    reason: str,
    evidence_quote: str,
    request: RealtimeIntelligenceRequest,
) -> None:
    card_text = "\n".join((title, recommendation, reason))
    if not _COACH_OWNER_GAP_RE.search(card_text):
        return
    card_times = _claim_tokens(_COACH_TIME_RE, card_text)
    card_anchors = _material_fact_terms(card_text) | _glossary_claim_terms(
        card_text,
        request=request,
    )
    if not card_times or not card_anchors:
        return

    clauses = _coach_topic_clauses(evidence_quote)
    for deadline in card_times:
        scoped = any(
            deadline in _claim_tokens(_COACH_TIME_RE, clause)
            and bool(_COACH_OWNER_GAP_RE.search(clause))
            and bool(
                card_anchors
                & (
                    _material_fact_terms(clause)
                    | _glossary_claim_terms(clause, request=request)
                )
            )
            for clause in clauses
        )
        if not scoped:
            raise IntelligenceResponseValidationError(
                "coach intervention moves a deadline across unrelated evidence clauses: deadline_scope",
                category="semantic_safety",
            )


def _validate_coach_claim_grounding(
    *,
    title: str,
    recommendation: str,
    reason: str,
    evidence_quote: str,
    evidence_ids: tuple[str, ...],
    request: RealtimeIntelligenceRequest,
) -> None:
    # The paragraph ids establish provenance, but only the exact quote establishes
    # which facts the card may repeat.  Otherwise a model can cite an innocuous
    # fragment and silently borrow an owner, deadline, number, polarity, or term
    # from somewhere else in the same (potentially noisy) ASR paragraph.
    evidence_numbers = _number_claim_tokens(evidence_quote)
    evidence_times = _claim_tokens(_COACH_TIME_RE, evidence_quote)
    evidence_owners = _owner_assignment_values(evidence_quote)
    evidence_polarities = _state_polarities(evidence_quote)
    evidence_terms = _material_fact_terms(evidence_quote)
    evidence_products = _glossary_claim_terms(evidence_quote, request=request)

    violations: set[str] = set()
    for value in (title, recommendation, reason):
        if _number_claim_tokens(value) - evidence_numbers:
            violations.add("number")
        if _claim_tokens(_COACH_TIME_RE, value) - evidence_times:
            violations.add("deadline")
        # A person's name being present in the quote does not establish their
        # ownership.  The exact quote must contain the same assignment relation.
        if _owner_assignment_values(value) - evidence_owners:
            violations.add("owner")
        if _state_polarities(value) - evidence_polarities:
            violations.add("state")
        if _material_fact_terms(value) - evidence_terms:
            violations.add("material_term")
        if _glossary_claim_terms(value, request=request) - evidence_products:
            violations.add("product_name")
    if violations:
        raise IntelligenceResponseValidationError(
            "coach intervention introduces material facts absent from referenced ASR evidence: "
            + ", ".join(sorted(violations)),
            category="semantic_safety",
        )

    _validate_coach_relationship_scope(
        title=title,
        recommendation=recommendation,
        reason=reason,
        evidence_quote=evidence_quote,
        request=request,
    )

    quoted_paragraphs = _quoted_evidence_paragraphs(
        evidence_quote=evidence_quote,
        evidence_ids=evidence_ids,
        request=request,
    )
    provisional = any(_coach_evidence_quality(paragraph) == "provisional" for paragraph in quoted_paragraphs)
    if (
        provisional
        and not _is_clarification_only_recommendation(recommendation)
        and _card_contains_material_assertion(
            title,
            recommendation,
            reason,
            request=request,
        )
    ):
        raise IntelligenceResponseValidationError(
            "provisional ASR evidence may only support a clarification, not a material coach claim",
            category="semantic_safety",
        )


def _validate_evidence_quote(
    quote: str,
    *,
    evidence_ids: tuple[str, ...],
    request: RealtimeIntelligenceRequest,
    field: str,
) -> None:
    quote_lines = tuple(normalized for line in quote.splitlines() if (normalized := _normalize_for_evidence(line)))
    if not quote_lines:
        raise IntelligenceResponseValidationError(
            f"{field} must not be empty",
            category="evidence",
        )
    evidence_texts = tuple(_normalize_for_evidence(request.paragraphs_by_id[item_id].text) for item_id in evidence_ids)
    if not all(any(line in evidence_text for evidence_text in evidence_texts) for line in quote_lines):
        raise IntelligenceResponseValidationError(
            f"{field} is not present in the referenced meeting evidence",
            category="evidence",
        )


def _decode_json_object(content: Any) -> Mapping[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise IntelligenceResponseValidationError("intelligence response must be non-empty JSON text")
    value = content.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if len(lines) < 3 or not lines[-1].strip().startswith("```"):
            raise IntelligenceResponseValidationError("malformed fenced JSON response")
        value = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise IntelligenceResponseValidationError("intelligence response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise IntelligenceResponseValidationError("intelligence response must be a JSON object")
    return payload


def _required_array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise IntelligenceResponseValidationError(f"{field} must be an array")
    return value


def _required_object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntelligenceResponseValidationError(f"{field} must be an object")
    return value


def _required_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _required_response_text(value: Any, field: str, *, maximum: int) -> str:
    try:
        return _required_text(value, field, maximum=maximum)
    except ValueError as exc:
        raise IntelligenceResponseValidationError(str(exc)) from exc


def _response_alias_text(
    item: Mapping[str, Any],
    *,
    primary_key: str,
    alias_keys: tuple[str, ...],
    field: str,
    alias_field: str,
    maximum: int,
) -> str:
    """Read a card field using its legacy and product-facing names.

    Both names may be present during the migration, but accepting divergent
    values would make the rendered card disagree with the audited payload.
    Fail closed instead of choosing one silently.
    """

    primary = _optional_response_text(item.get(primary_key), field, maximum=maximum)
    alias_value = next((item[key] for key in alias_keys if key in item), None)
    alias = _optional_response_text(alias_value, alias_field, maximum=maximum)
    if primary is not None and alias is not None and primary != alias:
        raise IntelligenceResponseValidationError(
            f"{field} and {alias_field} must match",
            category="structural",
        )
    value = primary or alias
    if value is None:
        return _required_response_text(None, field, maximum=maximum)
    return value


def _optional_text(value: Any, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional text value must be text or null")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > maximum:
        raise ValueError(f"optional text exceeds {maximum} characters")
    return normalized


def _optional_response_text(value: Any, field: str, *, maximum: int) -> str | None:
    try:
        return _optional_text(value, maximum=maximum)
    except ValueError as exc:
        raise IntelligenceResponseValidationError(f"{field}: {exc}") from exc


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if number <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return number


def _response_positive_integer(value: Any, field: str) -> int:
    try:
        return _positive_integer(value, field)
    except ValueError as exc:
        raise IntelligenceResponseValidationError(str(exc)) from exc


def _response_non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise IntelligenceResponseValidationError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise IntelligenceResponseValidationError(f"{field} must be a non-negative integer") from exc
    if number < 0:
        raise IntelligenceResponseValidationError(f"{field} must be a non-negative integer")
    return number


def _optional_non_negative_integer(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return number


def _optional_confidence(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number from 0 to 1")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number from 0 to 1") from exc
    if not 0 <= number <= 1:
        raise ValueError(f"{field} must be a number from 0 to 1")
    return number


def _bounded_unique_text_items(
    value: Any,
    field: str,
    *,
    maximum_items: int,
    maximum_characters: int,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be an array")
    if len(value) > maximum_items:
        raise ValueError(f"{field} must contain at most {maximum_items} items")
    items: list[str] = []
    for index, raw_item in enumerate(value):
        item = _required_text(raw_item, f"{field}[{index}]", maximum=maximum_characters)
        if item not in items:
            items.append(item)
    return tuple(items)


def _role_hint_for_source_track(source_track: str) -> str:
    if source_track == "system_audio":
        return "remote_mix"
    if source_track == "microphone":
        return "self_or_room"
    return "unknown"


def _response_confidence(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise IntelligenceResponseValidationError(f"{field} must be a number from 0 to 1")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise IntelligenceResponseValidationError(f"{field} must be a number from 0 to 1") from exc
    if not 0 <= number <= 1:
        raise IntelligenceResponseValidationError(f"{field} must be a number from 0 to 1")
    return number


def _normalize_for_evidence(value: str) -> str:
    return "".join(str(value or "").split())


def _json_compatible(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError("rolling_state must contain only JSON-compatible values") from exc

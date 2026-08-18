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
from typing import Any, Mapping, Sequence

from meeting_copilot_web_mvp.coach_skills import (
    BASE_COACH_EVENT_TYPES,
    SCENE_COACH_EVENT_TYPES,
    coach_skill_event_types,
    coach_skill_prompt,
    normalize_coach_skill_id,
)
from meeting_copilot_web_mvp.pi_coach_runtime import build_pi_coach_request
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

_STATE_KINDS = frozenset({"decision", "action_item", "risk", "open_question"})
_STATE_OPERATIONS = frozenset({"add", "update", "resolve", "noop"})
_TOPIC_OPERATIONS = frozenset({"add", "update", "noop"})
_URGENCY_VALUES = frozenset({"low", "medium", "high"})
_ROLLING_STATE_KEYS = frozenset({"topic", "open_items", "summary", "version"})
_SOURCE_TRACKS = frozenset({"microphone", "system_audio", "unknown"})
_ROLE_HINTS = frozenset({"self_or_room", "remote_mix", "unknown"})
_SEMANTIC_WINDOW_STATUSES = frozenset({"active", "stable"})
_COACH_EVENT_TYPES = BASE_COACH_EVENT_TYPES | SCENE_COACH_EVENT_TYPES


class IntelligenceResponseValidationError(ValueError):
    """A model response failed schema, version, or evidence validation."""

    retryable = False

    def __init__(self, message: str, *, category: str = "structural") -> None:
        super().__init__(message)
        self.category = category


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
        if not isinstance(new_paragraphs, Sequence) or isinstance(new_paragraphs, (str, bytes)):
            raise ValueError("new_paragraphs must be an array")
        if not 1 <= len(new_paragraphs) <= MAX_NEW_PARAGRAPHS:
            raise ValueError(f"new_paragraphs must contain 1 to {MAX_NEW_PARAGRAPHS} items")
        if not isinstance(context_paragraphs, Sequence) or isinstance(context_paragraphs, (str, bytes)):
            raise ValueError("context_paragraphs must be an array")
        if len(context_paragraphs) > MAX_CONTEXT_PARAGRAPHS:
            raise ValueError(f"context_paragraphs must contain at most {MAX_CONTEXT_PARAGRAPHS} items")
        raw_retrieval_paragraphs = list(retrieval_paragraphs or [])
        if len(raw_retrieval_paragraphs) > MAX_RETRIEVAL_PARAGRAPHS:
            raise ValueError(
                f"retrieval_paragraphs must contain at most {MAX_RETRIEVAL_PARAGRAPHS} items"
            )
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

        bounded_state = {
            key: _json_compatible(value)
            for key, value in rolling_state.items()
            if key in _ROLLING_STATE_KEYS
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
        return {
            item.id: item
            for item in (*self.retrieval_paragraphs, *self.context_paragraphs, *self.new_paragraphs)
        }

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
        _parse_paragraph_revision(value, request=request, index=index)
        for index, value in enumerate(raw_revisions)
    )
    target_ids = [item.target_id for item in revisions]
    if len(set(target_ids)) != len(target_ids):
        raise IntelligenceResponseValidationError("paragraph revision targets must be unique")

    topic_update = _parse_topic_update(payload.get("topic_update"), request=request)
    changes = tuple(
        _parse_state_change(value, request=request, index=index)
        for index, value in enumerate(raw_changes)
    )
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
    paragraphs = list(request.new_paragraphs)
    overall_evidence = {
        "segment_ids": [paragraph.id for paragraph in paragraphs],
        "quote": paragraphs[0].text[:1_000],
        "state_revision": request.state_revision,
        "evidence_hash": str(evidence_hash or "") or None,
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
    source_tracks = {item.source_track for item in request.new_paragraphs}
    if str(requested_runtime or "direct").strip().lower() == "pi":
        if not source_tracks & {"system_audio", "microphone"}:
            return False
        text = " ".join(item.text for item in request.new_paragraphs).casefold()
        # Questions and execution/decision language are high-signal moments
        # for a private coach. A longer multi-paragraph turn is also eligible
        # for the clarity check, while isolated ASR fragments stay silent.
        signal_terms = (
            "?",
            "？",
            "吗",
            "能否",
            "是否",
            "为什么",
            "怎么",
            "什么时候",
            "请问",
            "承诺",
            "保证",
            "一定",
            "上线",
            "交付",
            "截止",
            "负责人",
            "验收",
            "预算",
            "成本",
            "风险",
            "阻塞",
            "决定",
            "结论",
            "方案",
            "下一步",
            "完成",
            "需要",
        )
        if any(term in text for term in signal_terms):
            return True
        return len(request.new_paragraphs) >= 2 and len(text) >= 120
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
            "但 communication_clarity 可以直接使用持续的 microphone/self_or_room 原话判断当前可听见的表达模式；建议只针对表达本身，不推断说话者身份，也不能仅因身份未确认而保持静默。",
            "对 communication_clarity 而言，听众抓不住核心观点或错过及时收束结论属于具体损失，只要此刻能用一句下一步表达修正，就具有介入价值。",
            "没有高价值、可执行且仍来得及的介入时 intervention 必须是 null。不要为了显得有帮助而生成提示。",
            "不得猜测说话人身份、公司信息、数字、期限或用户立场。依据必须逐字出现在引用的 paragraph 中。",
            "recommendation 必须是 8 到 120 个字符的可直接说出口短句；不要写分析过程。",
        )
    ) + coach_skill_prompt(request.coach_skill_id)
    payload = {
        "state_revision": request.state_revision,
        "new_paragraphs": [item.to_prompt_dict() for item in request.new_paragraphs],
        "context_paragraphs": [item.to_prompt_dict() for item in request.context_paragraphs],
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
    if event_type == "communication_clarity" and len(
        [line for line in evidence_quote.splitlines() if _normalize_for_evidence(line)]
    ) < 2:
        raise IntelligenceResponseValidationError(
            "communication_clarity requires two verbatim evidence fragments",
            category="evidence",
        )
    urgency = _required_response_text(item.get("urgency"), "intervention.urgency", maximum=20)
    if urgency not in _URGENCY_VALUES:
        raise IntelligenceResponseValidationError("intervention.urgency is unsupported")
    recommendation = _required_response_text(
        item.get("recommendation"),
        "intervention.recommendation",
        maximum=120,
    )
    if len(recommendation) < 8:
        raise IntelligenceResponseValidationError("intervention.recommendation is too short")
    return CoachIntervention(
        event_type=event_type,
        title=_required_response_text(item.get("title"), "intervention.title", maximum=80),
        recommendation=recommendation,
        reason=_required_response_text(item.get("reason"), "intervention.reason", maximum=300),
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
        idempotency_key=f"{realtime_intelligence_idempotency_key(request)}:coach:v1",
        temperature=0.1,
        max_completion_tokens=768,
    )
    await _notify_callback(on_usage, _usage_dict(result.usage), 1)
    intervention = parse_realtime_coach_response(result.content, request=request)
    if intervention is not None and intervention.confidence < 0.78:
        intervention = None
    return {
        "intervention": intervention,
        "transport_mode": result.transport_mode.value,
        "ttft_ms": result.timings.time_to_first_token_seconds * 1_000,
        "usage": _usage_dict(result.usage),
        "model": result.model,
        "response_id": result.response_id,
        "finish_reason": result.finish_reason,
    }


async def run_realtime_coach_via_pi(
    *,
    request: RealtimeIntelligenceRequest,
    pi_runtime: Any,
    provider_config: Mapping[str, Any],
    before_attempt: Any = None,
    on_usage: Any = None,
) -> dict[str, Any]:
    """Run the same evidence contract through the restricted Pi sidecar."""

    if not hasattr(pi_runtime, "evaluate"):
        raise TypeError("pi_runtime must expose an async evaluate method")
    await _notify_callback(before_attempt, 1)
    request_id = f"{realtime_intelligence_idempotency_key(request)}:coach:pi:v1"
    payload = build_pi_coach_request(
        request,
        request_id=request_id,
        base_url=provider_config.get("base_url"),
        api_key=provider_config.get("api_key"),
        model=provider_config.get("model"),
        api_style=provider_config.get("api_style") or "chat_completions",
        timeout_seconds=float(provider_config.get("timeout_seconds") or 25.0),
    )
    result = await pi_runtime.evaluate(payload)
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
    if intervention is not None and set(intervention.evidence_segment_ids).isdisjoint(
        request.writable_paragraph_ids
    ):
        intervention = None
        metrics.update(
            {
                "intervention_suppressed": True,
                "suppression_reason": "stale_evidence",
            }
        )
        decision_reason = (
            "Pi 建议未引用本轮新内容，已抑制重复提醒，保留上一条有依据建议。"
        )
    elapsed_ms = float(metrics.get("elapsed_ms") or 0.0)
    return {
        "intervention": intervention,
        "transport_mode": "pi_agent_jsonl",
        "ttft_ms": elapsed_ms,
        "decision_latency_ms": elapsed_ms,
        "usage": dict(usage) if usage is not None else None,
        "model": str(provider_config.get("model") or ""),
        "response_id": request_id,
        "finish_reason": str(result.get("action") or ""),
        "decision_reason": decision_reason,
        "agent_metrics": metrics,
    }


def _pi_fallback_reason(error: Exception) -> str:
    """Classify Pi failures without reflecting provider payloads or credentials."""

    message = str(error or "").strip().lower()
    code = str(getattr(error, "code", type(error).__name__))[:120]
    if any(token in message for token in ("temporarily unavailable", "service unavailable", "upstream")):
        return "provider_temporarily_unavailable"
    if "timed out" in message or "timeout" in message or code in {"pi_timeout", "pi_startup_timeout"}:
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
) -> dict[str, Any]:
    reason = _pi_fallback_reason(error)
    return {
        "intervention": None,
        "transport_mode": "pi_agent_jsonl",
        "ttft_ms": None,
        "decision_latency_ms": None,
        "usage": None,
        "model": str(provider_config.get("model") or ""),
        "response_id": f"{realtime_intelligence_idempotency_key(request)}:coach:pi:v1",
        "finish_reason": "failed_open_silent",
        "decision_reason": "实时教练本轮超出响应预算，已保持静默。" if reason == "provider_timeout" else "实时教练本轮未能形成可靠建议，已保持静默。",
        "agent_metrics": {"fallback_suppressed": True},
        "runtime_requested": "pi",
        "runtime_used": "pi",
        "fallback_error_code": str(getattr(error, "code", type(error).__name__))[:120],
        "fallback_reason": reason,
    }


async def run_realtime_coach_routed(
    *,
    request: RealtimeIntelligenceRequest,
    provider: Any,
    requested_runtime: str,
    pi_runtime: Any = None,
    pi_provider_config: Mapping[str, Any] | None = None,
    before_attempt: Any = None,
    on_usage: Any = None,
) -> dict[str, Any]:
    """Select direct or Pi execution and fail back to direct on Pi errors."""

    normalized_runtime = str(requested_runtime or "direct").strip().lower()
    if normalized_runtime == "pi":
        try:
            result = await run_realtime_coach_via_pi(
                request=request,
                pi_runtime=pi_runtime,
                provider_config=dict(pi_provider_config or {}),
                before_attempt=before_attempt,
                on_usage=on_usage,
            )
            return {
                **result,
                "runtime_requested": "pi",
                "runtime_used": "pi",
                "fallback_error_code": None,
                "fallback_reason": None,
            }
        except Exception as exc:
            if _can_fall_back_before_model_work(exc):
                result = await run_realtime_coach(
                    request=request,
                    provider=provider,
                    before_attempt=before_attempt,
                    on_usage=on_usage,
                )
                return {
                    **result,
                    "runtime_requested": "pi",
                    "runtime_used": "direct",
                    "fallback_error_code": str(getattr(exc, "code", type(exc).__name__))[:120],
                    "fallback_reason": _pi_fallback_reason(exc),
                }
            return _silent_pi_failure(
                request=request,
                provider_config=dict(pi_provider_config or {}),
                error=exc,
            )
    result = await run_realtime_coach(
        request=request,
        provider=provider,
        before_attempt=before_attempt,
        on_usage=on_usage,
    )
    return {
        **result,
        "runtime_requested": "direct",
        "runtime_used": "direct",
        "fallback_error_code": None,
        "fallback_reason": None,
    }


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
            result.timings.time_to_first_token_seconds * 1_000
            if first_validation_error is not None
            else None
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
        _required_response_text(item, f"{field}.evidence_segment_ids", maximum=200)
        for item in raw_ids
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


def _validate_evidence_quote(
    quote: str,
    *,
    evidence_ids: tuple[str, ...],
    request: RealtimeIntelligenceRequest,
    field: str,
) -> None:
    quote_lines = tuple(
        normalized
        for line in quote.splitlines()
        if (normalized := _normalize_for_evidence(line))
    )
    if not quote_lines:
        raise IntelligenceResponseValidationError(
            f"{field} must not be empty",
            category="evidence",
        )
    evidence_texts = tuple(
        _normalize_for_evidence(request.paragraphs_by_id[item_id].text)
        for item_id in evidence_ids
    )
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

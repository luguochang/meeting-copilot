"""Structured, evidence-bound incremental meeting intelligence.

This module deliberately contains no keyword or regular-expression semantic
fallback. Provider output either satisfies the contract and evidence barrier or
is rejected while the canonical transcript remains available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import inspect
import json
import math
from typing import Any, Mapping, Sequence

from meeting_copilot_web_mvp.realtime_transcript_correction import correction_is_safe


MAX_NEW_PARAGRAPHS = 8
MAX_CONTEXT_PARAGRAPHS = 3
MAX_PARAGRAPH_CHARACTERS = 12_000
MAX_ROLLING_STATE_BYTES = 24_000
MAX_GLOSSARY_ITEMS = 100
MAX_GLOSSARY_ITEM_CHARACTERS = 120
MAX_REPAIR_SOURCE_CHARACTERS = 16_000

_STATE_KINDS = frozenset({"decision", "action_item", "risk", "open_question"})
_STATE_OPERATIONS = frozenset({"add", "update", "resolve", "noop"})
_TOPIC_OPERATIONS = frozenset({"add", "update", "noop"})
_URGENCY_VALUES = frozenset({"low", "medium", "high"})
_ROLLING_STATE_KEYS = frozenset({"topic", "open_items", "summary", "version"})


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
        return cls(
            id=paragraph_id,
            text=text,
            revision=revision,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker=speaker,
        )

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "revision": self.revision,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "speaker": self.speaker,
        }


@dataclass(frozen=True)
class RealtimeIntelligenceRequest:
    meeting_id: str
    state_revision: int
    new_paragraphs: tuple[IntelligenceParagraph, ...]
    context_paragraphs: tuple[IntelligenceParagraph, ...]
    rolling_state: Mapping[str, Any]
    glossary: tuple[str, ...]
    meeting_goal: str | None

    @classmethod
    def from_payload(
        cls,
        *,
        meeting_id: Any,
        state_revision: Any,
        new_paragraphs: Sequence[Mapping[str, Any]],
        context_paragraphs: Sequence[Mapping[str, Any]],
        rolling_state: Mapping[str, Any],
        glossary: Sequence[Any] | None = None,
        meeting_goal: Any = None,
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
        if not isinstance(rolling_state, Mapping):
            raise ValueError("rolling_state must be an object")

        new_items = tuple(
            IntelligenceParagraph.from_payload(item, field=f"new_paragraphs[{index}]")
            for index, item in enumerate(new_paragraphs)
        )
        context_items = tuple(
            IntelligenceParagraph.from_payload(item, field=f"context_paragraphs[{index}]")
            for index, item in enumerate(context_paragraphs)
        )
        all_ids = [item.id for item in (*context_items, *new_items)]
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("paragraph ids must be unique within one intelligence request")

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
            rolling_state=bounded_state,
            glossary=tuple(glossary_items),
            meeting_goal=_optional_text(meeting_goal, maximum=2_000),
        )

    @property
    def paragraphs_by_id(self) -> dict[str, IntelligenceParagraph]:
        return {item.id: item for item in (*self.context_paragraphs, *self.new_paragraphs)}

    @property
    def writable_paragraph_ids(self) -> frozenset[str]:
        return frozenset(item.id for item in self.new_paragraphs)

    @property
    def input_characters(self) -> int:
        return sum(len(item.text) for item in (*self.context_paragraphs, *self.new_paragraphs))


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
    system = (
        "你是中文会议实时理解引擎。只依据输入中的会议原话返回一个 JSON 对象，不要输出 Markdown。"
        "不得用关键词匹配、常识补全或猜测生成决定、待办、风险、问题或追问；没有充分证据时返回空数组或 null。"
        "paragraph_revisions 只能修改 new_paragraphs 中的目标，context_paragraphs 只读；修正不得改变事实。"
        "state_changes.operation 只能是 add、update、resolve、noop，正式变更必须携带可逐字核验的证据 ID 和原话。"
        "输出字段固定且必须全部存在：paragraph_revisions、topic_update、state_changes、follow_up。"
        "无段落修正时 paragraph_revisions 必须是 []；无状态变更时 state_changes 必须是 []。"
        "无主题变更时 topic_update 必须是 null，不得返回 noop 对象。"
        "无值得追问的问题时 follow_up 必须是 null，不得返回 [] 或空对象。"
        "follow_up.urgency 必须是 low、medium、high 之一，不得为 null。"
        "state_changes.type 必须是 decision、action_item、risk、open_question 之一；"
        "topic_update.operation 只能是 add 或 update。"
        "所有证据 ID 和修正 target_id 必须从输入 paragraph id 原样复制。"
    )
    payload = {
        "state_revision": request.state_revision,
        "new_paragraphs": [item.to_prompt_dict() for item in request.new_paragraphs],
        "context_paragraphs": [item.to_prompt_dict() for item in request.context_paragraphs],
        "rolling_state": request.rolling_state,
        "glossary": list(request.glossary),
        "meeting_goal": request.meeting_goal,
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
        "rolling_state": request.rolling_state,
        "glossary": list(request.glossary),
        "meeting_goal": request.meeting_goal,
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
        if exc.category not in {"structural", "truncated"}:
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
    normalized_quote = _normalize_for_evidence(quote)
    if not normalized_quote:
        raise IntelligenceResponseValidationError(
            f"{field} must not be empty",
            category="evidence",
        )
    if not any(
        normalized_quote in _normalize_for_evidence(request.paragraphs_by_id[item_id].text)
        for item_id in evidence_ids
    ):
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

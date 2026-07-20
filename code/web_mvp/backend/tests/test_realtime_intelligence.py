from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    build_realtime_intelligence_messages,
    build_realtime_intelligence_repair_messages,
    dynamic_output_token_limit,
    parse_realtime_intelligence_response,
    realtime_intelligence_idempotency_key,
    realtime_intelligence_batch_id,
    build_llm_first_event_context,
    run_realtime_intelligence,
)


def _paragraph(
    paragraph_id: str,
    text: str,
    *,
    revision: int = 1,
    start_ms: int = 0,
    end_ms: int = 1_000,
    speaker: str = "speaker-1",
) -> dict:
    return {
        "id": paragraph_id,
        "text": text,
        "revision": revision,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "speaker": speaker,
    }


def _request() -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-private-id",
        state_revision=7,
        new_paragraphs=[
            _paragraph(
                "paragraph-3",
                "订单服务先灰度百分之五，错误率超过千分之一时立即回滚。",
                start_ms=20_000,
                end_ms=28_000,
            )
        ],
        context_paragraphs=[
            _paragraph("paragraph-1", "今天讨论订单服务的发布方案。", start_ms=0, end_ms=8_000),
            _paragraph("paragraph-2", "目前还没有确认回滚负责人。", start_ms=9_000, end_ms=18_000),
        ],
        rolling_state={
            "topic": {"title": "订单服务发布", "summary": "讨论灰度与回滚"},
            "open_items": [],
            "private_internal_field": "must-not-leak",
        },
        glossary=["checkout-service", "P99"],
        meeting_goal="确认发布和回滚方案",
    )


def test_prompt_contains_only_bounded_incremental_context_and_schema_contract() -> None:
    request = _request()

    messages = build_realtime_intelligence_messages(request)

    assert [message["role"] for message in messages] == ["system", "user"]
    system = messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert "关键词" in system
    assert "不得" in system
    assert payload["state_revision"] == 7
    assert [item["id"] for item in payload["new_paragraphs"]] == ["paragraph-3"]
    assert [item["id"] for item in payload["context_paragraphs"]] == ["paragraph-1", "paragraph-2"]
    assert payload["rolling_state"] == {
        "topic": {"title": "订单服务发布", "summary": "讨论灰度与回滚"},
        "open_items": [],
    }
    assert payload["glossary"] == ["checkout-service", "P99"]
    assert payload["meeting_goal"] == "确认发布和回滚方案"
    assert "private_internal_field" not in messages[1]["content"]


def test_request_rejects_more_than_three_read_only_context_paragraphs() -> None:
    with pytest.raises(ValueError, match="at most 3"):
        RealtimeIntelligenceRequest.from_payload(
            meeting_id="meeting-1",
            state_revision=1,
            new_paragraphs=[_paragraph("new", "新增内容")],
            context_paragraphs=[_paragraph(f"context-{index}", f"上下文 {index}") for index in range(4)],
            rolling_state={},
        )


def test_parser_validates_revisions_state_changes_and_follow_up_evidence() -> None:
    request = _request()
    content = json.dumps(
        {
            "paragraph_revisions": [
                {
                    "target_id": "paragraph-3",
                    "expected_revision": 1,
                    "corrected_text": "订单服务先灰度 5%，错误率超过 0.1% 时立即回滚。",
                    "change_count": 2,
                }
            ],
            "topic_update": {
                "operation": "update",
                "title": "订单服务灰度与回滚",
                "summary": "明确灰度比例和回滚阈值。",
                "evidence_segment_ids": ["paragraph-3"],
                "evidence_quote": "订单服务先灰度百分之五",
            },
            "state_changes": [
                {
                    "type": "decision",
                    "operation": "add",
                    "item_id": "decision-release-threshold",
                    "content": "订单服务先灰度 5%，错误率超过 0.1% 时立即回滚。",
                    "owner": None,
                    "deadline": None,
                    "status": "candidate",
                    "evidence_segment_ids": ["paragraph-3"],
                    "evidence_quote": "错误率超过千分之一时立即回滚",
                    "confidence": 0.94,
                }
            ],
            "follow_up": {
                "question": "建议确认由谁负责执行回滚？",
                "reason": "回滚负责人尚未明确。",
                "evidence_segment_ids": ["paragraph-2"],
                "evidence_quote": "还没有确认回滚负责人",
                "urgency": "high",
            },
        },
        ensure_ascii=False,
    )

    response = parse_realtime_intelligence_response(content, request=request)

    assert response.paragraph_revisions[0].target_id == "paragraph-3"
    assert response.paragraph_revisions[0].changed is True
    assert response.state_changes[0].kind == "decision"
    assert response.state_changes[0].evidence_segment_ids == ("paragraph-3",)
    assert response.follow_up is not None
    assert response.follow_up.evidence_segment_ids == ("paragraph-2",)
    assert response.to_dict()["state_changes"][0]["confidence"] == pytest.approx(0.94)


def test_llm_first_event_context_has_a_called_provider_and_bounded_evidence() -> None:
    request = _request()
    response = parse_realtime_intelligence_response(
        json.dumps({
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        }),
        request=request,
    )

    context = build_llm_first_event_context(
        request=request,
        response=response,
        job_id="job-1",
        batch_id=realtime_intelligence_batch_id(request),
        provider="openai_compatible",
        model="fast-model",
        evidence_hash="hash-1",
    )

    assert context["source"] == "llm_first"
    assert context["llm_called"] is True
    assert context["job_id"] == "job-1"
    assert context["batch_id"].startswith("llm-first-batch:")
    assert context["provider"] == "openai_compatible"
    assert context["model"] == "fast-model"
    assert context["evidence"]["segment_ids"] == ["paragraph-3"]
    assert context["evidence"]["quote"]


def test_parser_rejects_evidence_quote_not_present_in_referenced_paragraph() -> None:
    request = _request()
    content = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [
                {
                    "type": "risk",
                    "operation": "add",
                    "item_id": "risk-made-up",
                    "content": "数据库可能丢失数据。",
                    "status": "candidate",
                    "evidence_segment_ids": ["paragraph-3"],
                    "evidence_quote": "数据库可能丢失数据",
                    "confidence": 0.91,
                }
            ],
            "follow_up": None,
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError, match="evidence_quote"):
        parse_realtime_intelligence_response(content, request=request)


def test_parser_rejects_fact_changing_paragraph_correction_without_repair() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-fact-safety",
        state_revision=1,
        new_paragraphs=[
            _paragraph(
                "paragraph-fact",
                "错误率超过百分之一时立即回滚。",
            )
        ],
        context_paragraphs=[],
        rolling_state={},
    )
    content = json.dumps(
        {
            "paragraph_revisions": [
                {
                    "target_id": "paragraph-fact",
                    "expected_revision": 1,
                    "corrected_text": "错误率超过百分之五时立即回滚。",
                    "change_count": 1,
                }
            ],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_intelligence_response(content, request=request)

    assert caught.value.category == "semantic_safety"
    assert "fact-preservation" in str(caught.value)


def test_parser_normalizes_provider_empty_noop_conventions_without_creating_facts() -> None:
    request = _request()
    response = parse_realtime_intelligence_response(
        json.dumps(
            {
                "paragraph_revisions": [],
                "topic_update": {"operation": "noop", "title": None, "summary": None},
                "state_changes": [],
                "follow_up": [],
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert response.paragraph_revisions == ()
    assert response.topic_update is None
    assert response.state_changes == ()
    assert response.follow_up is None


def test_parser_rejects_non_empty_array_as_follow_up() -> None:
    request = _request()
    with pytest.raises(IntelligenceResponseValidationError, match="follow_up must be an object"):
        parse_realtime_intelligence_response(
            json.dumps(
                {
                    "paragraph_revisions": [],
                    "topic_update": None,
                    "state_changes": [],
                    "follow_up": [{"question": "unsupported shape"}],
                },
                ensure_ascii=False,
            ),
            request=request,
        )


def test_parser_rejects_revision_of_read_only_context_and_stale_target_version() -> None:
    request = _request()
    base = {
        "topic_update": None,
        "state_changes": [],
        "follow_up": None,
    }

    for revision in (
        {
            "target_id": "paragraph-2",
            "expected_revision": 1,
            "corrected_text": "不能改写只读上下文。",
            "change_count": 1,
        },
        {
            "target_id": "paragraph-3",
            "expected_revision": 6,
            "corrected_text": "目标版本已经过期。",
            "change_count": 1,
        },
    ):
        with pytest.raises(IntelligenceResponseValidationError):
            parse_realtime_intelligence_response(
                json.dumps({**base, "paragraph_revisions": [revision]}, ensure_ascii=False),
                request=request,
            )


def test_noop_change_cannot_fabricate_a_semantic_item() -> None:
    request = _request()
    content = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [
                {
                    "type": "action_item",
                    "operation": "noop",
                    "item_id": "action-1",
                    "content": "新增一个任务",
                    "evidence_segment_ids": ["paragraph-3"],
                    "evidence_quote": "先灰度百分之五",
                    "confidence": 0.8,
                }
            ],
            "follow_up": None,
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError, match="noop"):
        parse_realtime_intelligence_response(content, request=request)


def test_idempotency_key_is_stable_and_does_not_disclose_meeting_id() -> None:
    request = _request()

    first = realtime_intelligence_idempotency_key(request)
    second = realtime_intelligence_idempotency_key(request)

    assert first == second
    assert first.startswith("realtime-intelligence:")
    assert request.meeting_id not in first
    changed = RealtimeIntelligenceRequest.from_payload(
        meeting_id=request.meeting_id,
        state_revision=8,
        new_paragraphs=[_paragraph("paragraph-3", "相同文字")],
        context_paragraphs=[],
        rolling_state={},
    )
    assert realtime_intelligence_idempotency_key(changed) != first


@pytest.mark.parametrize(
    ("characters", "expected"),
    [(1, 768), (500, 1_024), (2_000, 3_072), (20_000, 4_096)],
)
def test_dynamic_output_token_limit_is_bounded(characters: int, expected: int) -> None:
    assert dynamic_output_token_limit(characters) == expected


class _Provider:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = [content] if isinstance(content, str) else list(content)
        self.messages = None
        self.parameters = None
        self.idempotency_key = None
        self.calls = []

    async def complete(self, messages, *, on_delta=None, idempotency_key=None, **parameters):
        content = self.contents[min(len(self.calls), len(self.contents) - 1)]
        self.messages = messages
        self.parameters = parameters
        self.idempotency_key = idempotency_key
        self.calls.append(
            {
                "messages": messages,
                "parameters": parameters,
                "idempotency_key": idempotency_key,
            }
        )
        if on_delta is not None:
            await on_delta(SimpleNamespace(text=content, sequence=1))
        return SimpleNamespace(
            content=content,
            transport_mode=SimpleNamespace(value="streaming"),
            fallback_reason=None,
            timings=SimpleNamespace(
                time_to_first_token_seconds=0.4,
                started_at=1.0,
                connected_at=1.1,
                first_token_at=1.4,
                completed_at=1.8,
            ),
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=30, total_tokens=70),
            response_id="response-1",
            model="fast-model",
            finish_reason="stop",
        )


def test_runner_validates_one_structured_response_and_returns_latency_usage() -> None:
    asyncio.run(_test_runner_validates_one_structured_response_and_returns_latency_usage())


async def _test_runner_validates_one_structured_response_and_returns_latency_usage() -> None:
    request = _request()
    content = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )
    provider = _Provider(content)

    result = await run_realtime_intelligence(request=request, provider=provider)

    assert result["response"].state_changes == ()
    assert result["ttft_ms"] == pytest.approx(400)
    assert result["usage"]["total_tokens"] == 70
    assert result["provider_attempt_count"] == 1
    assert result["repair_attempted"] is False
    assert result["idempotency_key"] == provider.idempotency_key
    assert provider.parameters["max_completion_tokens"] == dynamic_output_token_limit(request.input_characters)
    assert provider.parameters["temperature"] == pytest.approx(0.1)


def test_runner_does_not_turn_invalid_provider_json_into_semantic_results() -> None:
    asyncio.run(_test_runner_does_not_turn_invalid_provider_json_into_semantic_results())


async def _test_runner_does_not_turn_invalid_provider_json_into_semantic_results() -> None:
    provider = _Provider("不是 JSON")

    with pytest.raises(IntelligenceResponseValidationError):
        await run_realtime_intelligence(request=_request(), provider=provider)
    assert len(provider.calls) == 2


def test_repair_prompt_is_bounded_and_keeps_the_original_evidence_contract() -> None:
    request = _request()
    messages = build_realtime_intelligence_repair_messages(
        request,
        invalid_content="x" * 20_000,
        validation_error=IntelligenceResponseValidationError("follow_up.urgency must be text"),
    )

    assert [message["role"] for message in messages] == ["system", "user", "assistant", "user"]
    assert len(messages[2]["content"]) == 16_000
    repair = json.loads(messages[3]["content"])
    assert repair["task"] == "repair_previous_response"
    assert repair["validation_error"] == "follow_up.urgency must be text"
    assert "Delete any item" in " ".join(repair["rules"])


def test_runner_repairs_one_invalid_structured_response_and_counts_all_usage() -> None:
    asyncio.run(_test_runner_repairs_one_invalid_structured_response_and_counts_all_usage())


async def _test_runner_repairs_one_invalid_structured_response_and_counts_all_usage() -> None:
    valid = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )
    provider = _Provider(["not-json", valid])

    attempts = []
    usages = []
    result = await run_realtime_intelligence(
        request=_request(),
        provider=provider,
        before_attempt=attempts.append,
        on_usage=lambda usage, attempt: usages.append((attempt, usage)),
    )

    assert result["response"].state_changes == ()
    assert result["provider_attempt_count"] == 2
    assert result["repair_attempted"] is True
    assert result["repair_ttft_ms"] == pytest.approx(400)
    assert result["usage"] == {
        "prompt_tokens": 80,
        "completion_tokens": 60,
        "total_tokens": 140,
    }
    assert provider.calls[1]["idempotency_key"].endswith(":repair:v1")
    assert provider.calls[1]["parameters"]["temperature"] == 0
    assert provider.calls[1]["messages"][2]["content"] == "not-json"
    assert attempts == [1, 2]
    assert usages == [
        (1, {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70}),
        (2, {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70}),
    ]


def test_runner_does_not_repair_unknown_evidence() -> None:
    asyncio.run(_test_runner_does_not_repair_unknown_evidence())


async def _test_runner_does_not_repair_unknown_evidence() -> None:
    invalid = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [
                {
                    "type": "risk",
                    "operation": "add",
                    "item_id": "risk-1",
                    "content": "无依据风险",
                    "owner": None,
                    "deadline": None,
                    "status": "candidate",
                    "evidence_segment_ids": ["unknown-paragraph"],
                    "evidence_quote": "无依据风险",
                    "confidence": 0.9,
                }
            ],
            "follow_up": None,
        },
        ensure_ascii=False,
    )
    provider = _Provider(invalid)

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        await run_realtime_intelligence(request=_request(), provider=provider)

    assert caught.value.category == "evidence"
    assert len(provider.calls) == 1


def test_runner_does_not_repair_stale_paragraph_revision() -> None:
    asyncio.run(_test_runner_does_not_repair_stale_paragraph_revision())


async def _test_runner_does_not_repair_stale_paragraph_revision() -> None:
    invalid = json.dumps(
        {
            "paragraph_revisions": [
                {
                    "target_id": "paragraph-3",
                    "expected_revision": 99,
                    "corrected_text": "过期修正",
                    "change_count": 1,
                }
            ],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )
    provider = _Provider(invalid)

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        await run_realtime_intelligence(request=_request(), provider=provider)

    assert caught.value.category == "stale"
    assert len(provider.calls) == 1

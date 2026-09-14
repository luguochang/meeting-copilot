from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    _pi_fallback_reason,
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    apply_coach_intervention,
    build_local_reflex_intervention,
    build_realtime_coach_provenance_decision,
    build_realtime_coach_messages,
    build_realtime_intelligence_messages,
    build_realtime_intelligence_repair_messages,
    dynamic_output_token_limit,
    parse_realtime_intelligence_response,
    parse_realtime_coach_response,
    realtime_intelligence_idempotency_key,
    realtime_intelligence_batch_id,
    realtime_coach_candidate_events,
    build_llm_first_event_context,
    run_realtime_intelligence,
    run_realtime_coach,
    run_realtime_coach_routed,
    run_realtime_coach_via_pi,
    should_run_realtime_coach,
    CoachIntervention,
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


def test_prompt_preserves_source_roles_and_bounded_semantic_windows() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-source-aware",
        state_revision=2,
        new_paragraphs=[
            {
                **_paragraph("remote-question", "这个方案周五能上线吗？"),
                "source_track": "system_audio",
                "speaker_confidence": 0.92,
            }
        ],
        context_paragraphs=[
            {
                **_paragraph("local-answer", "我先确认压测结果。"),
                "source_track": "microphone",
            }
        ],
        semantic_windows=[
            {
                "id": "window-1",
                "text": "[self_or_room] 我先确认压测结果。\n[remote_mix] 这个方案周五能上线吗？",
                "revision": 3,
                "start_ms": 0,
                "end_ms": 2_000,
                "status": "stable",
                "segment_ids": ["local-answer", "remote-question"],
                "source_tracks": ["microphone", "system_audio"],
                "role_hints": ["self_or_room", "remote_mix"],
            }
        ],
        rolling_state={},
    )

    payload = json.loads(build_realtime_intelligence_messages(request)[1]["content"])

    assert payload["new_paragraphs"][0]["role_hint"] == "remote_mix"
    assert payload["new_paragraphs"][0]["speaker_confidence"] == pytest.approx(0.92)
    assert payload["context_paragraphs"][0]["role_hint"] == "self_or_room"
    assert payload["semantic_windows"][0]["segment_ids"] == ["local-answer", "remote-question"]
    assert "理解窗口" in build_realtime_intelligence_messages(request)[0]["content"]


def test_semantic_window_rejects_evidence_ids_outside_the_bounded_request() -> None:
    with pytest.raises(ValueError, match="outside this request"):
        RealtimeIntelligenceRequest.from_payload(
            meeting_id="meeting-source-aware",
            state_revision=2,
            new_paragraphs=[_paragraph("remote-question", "这个方案周五能上线吗？")],
            context_paragraphs=[],
            semantic_windows=[
                {
                    "id": "window-1",
                    "text": "越界窗口",
                    "segment_ids": ["not-visible"],
                    "source_tracks": ["unknown"],
                    "role_hints": ["unknown"],
                }
            ],
            rolling_state={},
        )


def test_request_rejects_more_than_three_read_only_context_paragraphs() -> None:
    with pytest.raises(ValueError, match="at most 3"):
        RealtimeIntelligenceRequest.from_payload(
            meeting_id="meeting-1",
            state_revision=1,
            new_paragraphs=[_paragraph("new", "新增内容")],
            context_paragraphs=[_paragraph(f"context-{index}", f"上下文 {index}") for index in range(4)],
            rolling_state={},
        )


def test_trigger_contract_allows_due_or_user_request_without_fake_delta() -> None:
    due = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-due",
        state_revision=9,
        trigger_type="task_due",
        work_item_id="decision-7",
        new_paragraphs=[],
        context_paragraphs=[_paragraph("evidence-1", "回滚负责人仍待确认。")],
        rolling_state={},
    )
    user_requested = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-user-request",
        state_revision=10,
        trigger_type="user_request",
        user_request="发布前还缺哪些条件？",
        new_paragraphs=[],
        context_paragraphs=[],
        rolling_state={},
    )

    assert due.trigger_type == "task_due"
    assert due.work_item_id == "decision-7"
    assert due.new_paragraphs == ()
    assert user_requested.trigger_type == "user_request"
    assert user_requested.user_request == "发布前还缺哪些条件？"
    assert user_requested.new_paragraphs == ()
    prompt = json.loads(build_realtime_intelligence_messages(user_requested)[1]["content"])
    assert prompt["trigger_type"] == "user_request"
    assert prompt["user_request"] == "发布前还缺哪些条件？"

    transcript_delta = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-transcript-delta",
        state_revision=11,
        trigger_type="transcript_delta",
        new_paragraphs=[_paragraph("fresh-1", "新的会议原话")],
        context_paragraphs=[],
        rolling_state={},
    )
    assert transcript_delta.trigger_type == "transcript_delta"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "delta new_paragraphs"),
        ({"trigger_type": "task_due"}, "task_due requires work_item_id"),
        (
            {"trigger_type": "task_due", "work_item_id": "decision-1"},
            "requires persisted evidence context",
        ),
        ({"trigger_type": "user_request"}, "requires user_request"),
        ({"trigger_type": "timer"}, "trigger_type must be"),
    ],
)
def test_trigger_contract_rejects_incomplete_or_unsupported_triggers(
    kwargs: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        RealtimeIntelligenceRequest.from_payload(
            meeting_id="meeting-trigger-invalid",
            state_revision=1,
            new_paragraphs=[],
            context_paragraphs=[],
            rolling_state={},
            **kwargs,
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
        json.dumps(
            {
                "paragraph_revisions": [],
                "topic_update": None,
                "state_changes": [],
                "follow_up": None,
            }
        ),
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


def test_parser_accepts_verbatim_multiline_quotes_in_a_different_evidence_order() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-multiline-evidence",
        state_revision=1,
        new_paragraphs=[_paragraph("paragraph-current", "We can ship on Friday without conditions.")],
        context_paragraphs=[_paragraph("paragraph-prior", "Earlier we required the load test to pass first.")],
        rolling_state={},
    )
    content = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": {
                "question": "Should we resolve the changed release condition before committing?",
                "reason": "The current commitment conflicts with the earlier condition.",
                "evidence_segment_ids": ["paragraph-current", "paragraph-prior"],
                "evidence_quote": (
                    "Earlier we required the load test to pass first.\nWe can ship on Friday without conditions."
                ),
                "urgency": "high",
            },
        }
    )

    response = parse_realtime_intelligence_response(content, request=request)

    assert response.follow_up is not None
    assert response.follow_up.evidence_segment_ids == ("paragraph-current", "paragraph-prior")


def test_parser_rejects_multiline_quote_when_any_line_is_not_verbatim_evidence() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-invalid-multiline-evidence",
        state_revision=1,
        new_paragraphs=[_paragraph("paragraph-current", "We can ship on Friday without conditions.")],
        context_paragraphs=[_paragraph("paragraph-prior", "Earlier we required the load test to pass first.")],
        rolling_state={},
    )
    content = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": {
                "question": "Should we resolve the changed release condition before committing?",
                "reason": "The current commitment conflicts with the earlier condition.",
                "evidence_segment_ids": ["paragraph-current", "paragraph-prior"],
                "evidence_quote": (
                    "Earlier we required the load test to pass first.\nThe customer approved unconditional release."
                ),
                "urgency": "high",
            },
        }
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


def _coach_request() -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-coach",
        state_revision=4,
        new_paragraphs=[
            {
                **_paragraph("remote-4", "你能承诺周五一定上线吗？"),
                "source_track": "system_audio",
                "correction_status": "no_change",
            }
        ],
        context_paragraphs=[
            {
                **_paragraph("local-3", "压测还没有完成。"),
                "source_track": "microphone",
                "correction_status": "no_change",
            }
        ],
        semantic_windows=[
            {
                "id": "window-coach",
                "text": "[self_or_room] 压测还没有完成。\n[remote_mix] 你能承诺周五一定上线吗？",
                "segment_ids": ["local-3", "remote-4"],
                "source_tracks": ["microphone", "system_audio"],
                "role_hints": ["self_or_room", "remote_mix"],
                "status": "stable",
            }
        ],
        rolling_state={"open_items": []},
        meeting_goal="避免在压测完成前承诺上线日期",
    )


def test_coach_lane_only_triggers_for_new_system_audio() -> None:
    assert should_run_realtime_coach(_coach_request()) is True
    assert should_run_realtime_coach(_request()) is False
    mic_request = replace(
        _coach_request(),
        new_paragraphs=tuple(
            replace(item, source_track="microphone", role_hint="self_or_room")
            for item in _coach_request().new_paragraphs
        ),
    )
    assert should_run_realtime_coach(mic_request) is False
    assert should_run_realtime_coach(mic_request, requested_runtime="pi") is True


def test_pi_coach_triggers_for_one_long_repetitive_paragraph() -> None:
    long_turn = "我们先从当前背景开始说明。" * 12
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text=long_turn,
                start_ms=1_000,
                end_ms=61_000,
            ),
        ),
    )

    assert len(long_turn) >= 120
    assert should_run_realtime_coach(request, requested_runtime="pi") is True


def test_pi_coach_keeps_a_short_ordinary_paragraph_silent() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="今天先同步一下当前进展。",
            ),
        ),
    )

    assert should_run_realtime_coach(request, requested_runtime="pi") is False


def _cross_batch_clarity_request(
    *,
    state_revision: int = 3,
    first_revision: int = 1,
    latest_text: str = "我继续补充流程背景，仍然没有给出明确结论或下一步。",
    latest_start_ms: int | None = 40_000,
    latest_end_ms: int | None = 60_000,
    latest_source_track: str = "microphone",
    latest_speaker: str | None = "speaker-1",
    context_paragraphs: list[dict] | None = None,
) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-cross-batch-clarity",
        state_revision=state_revision,
        retrieval_paragraphs=[
            {
                **_paragraph(
                    "clarity-prior-1",
                    "关于迁移方案我先继续补充背景，结论先放到后面。",
                    revision=first_revision,
                    start_ms=0,
                    end_ms=20_000,
                ),
                "source_track": "microphone",
            },
            {
                **_paragraph(
                    "clarity-prior-2",
                    "前面的背景还没有收束，我又补了几个旁支。",
                    start_ms=20_000,
                    end_ms=40_000,
                ),
                "source_track": "microphone",
            },
        ],
        context_paragraphs=context_paragraphs or [],
        new_paragraphs=[
            {
                **_paragraph(
                    "clarity-fresh-3" if state_revision == 3 else "clarity-fresh-4",
                    latest_text,
                    start_ms=latest_start_ms,
                    end_ms=latest_end_ms,
                    speaker=latest_speaker,
                ),
                "source_track": latest_source_track,
            }
        ],
        rolling_state={"topic": "迁移方案", "open_items": []},
        meeting_goal="清晰给出迁移方案的结论和下一步",
        allow_paragraph_revisions=False,
    )


def test_pi_clarity_gate_spans_batches_with_prior_and_fresh_evidence() -> None:
    request = _cross_batch_clarity_request()

    candidates = realtime_coach_candidate_events(request)

    assert [item.event_type for item in candidates] == ["monologue_duration"]
    assert candidates[0].evidence_segment_ids == (
        "clarity-prior-2",
        "clarity-fresh-3",
    )
    assert set(candidates[0].evidence_segment_ids) & request.writable_paragraph_ids
    assert should_run_realtime_coach(request, requested_runtime="pi") is True

    payload = json.loads(build_realtime_coach_messages(request)[1]["content"])
    assert [item["id"] for item in payload["candidate_evidence_paragraphs"]] == [
        "clarity-prior-2"
    ]


def test_pi_clarity_gate_keeps_a_healthy_sixty_second_discussion_silent() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-healthy-long-turn",
        state_revision=2,
        retrieval_paragraphs=[
            {
                **_paragraph(
                    "healthy-prior",
                    "关于迁移范围，结论是保持当前方案，依据是验证结果已经满足要求。",
                    start_ms=0,
                    end_ms=30_000,
                ),
                "source_track": "microphone",
            }
        ],
        context_paragraphs=[],
        new_paragraphs=[
            {
                **_paragraph(
                    "healthy-fresh",
                    "接下来分两步：先整理记录，再复核结果。这就是本轮的结论、依据和下一步。",
                    start_ms=30_000,
                    end_ms=60_000,
                ),
                "source_track": "microphone",
            }
        ],
        rolling_state={"topic": "迁移范围", "open_items": []},
        meeting_goal="明确结论和下一步",
        allow_paragraph_revisions=False,
    )

    assert realtime_coach_candidate_events(request) == ()
    assert should_run_realtime_coach(request, requested_runtime="pi") is False


@pytest.mark.parametrize("boundary", ["source", "speaker", "gap", "missing_time"])
def test_pi_clarity_gate_resets_at_a_real_discourse_boundary(boundary: str) -> None:
    prior_speaker = "speaker-1"
    prior_start_ms: int | None = 0
    prior_end_ms: int | None = 30_000
    latest_source_track = "microphone"
    latest_speaker = "speaker-1"
    latest_start_ms: int | None = 30_000
    latest_end_ms: int | None = 50_000
    if boundary == "source":
        latest_source_track = "system_audio"
    elif boundary == "speaker":
        latest_speaker = "speaker-2"
    elif boundary == "gap":
        latest_start_ms = 36_000
        latest_end_ms = 56_000
    else:
        prior_start_ms = None
        prior_end_ms = None

    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id=f"meeting-clarity-boundary-{boundary}",
        state_revision=2,
        retrieval_paragraphs=[
            {
                **_paragraph(
                    "boundary-prior",
                    "我先继续补充背景，结论先放到后面。",
                    start_ms=prior_start_ms,
                    end_ms=prior_end_ms,
                    speaker=prior_speaker,
                ),
                "source_track": "microphone",
            }
        ],
        context_paragraphs=[],
        new_paragraphs=[
            {
                **_paragraph(
                    "boundary-fresh",
                    "我再补充一些背景，结论稍后再说。",
                    start_ms=latest_start_ms,
                    end_ms=latest_end_ms,
                    speaker=latest_speaker,
                ),
                "source_track": latest_source_track,
            }
        ],
        rolling_state={},
        allow_paragraph_revisions=False,
    )

    assert realtime_coach_candidate_events(request) == ()


def test_pi_clarity_candidate_key_stays_stable_within_one_episode() -> None:
    first_request = _cross_batch_clarity_request()
    second_request = _cross_batch_clarity_request(
        state_revision=4,
        latest_text="我又补充了一个旁支，仍然没有形成明确结论或下一步。",
        latest_start_ms=60_000,
        latest_end_ms=75_000,
        context_paragraphs=[
            {
                **_paragraph(
                    "clarity-fresh-3",
                    "我继续补充流程背景，仍然没有给出明确结论或下一步。",
                    start_ms=40_000,
                    end_ms=60_000,
                ),
                "source_track": "microphone",
            }
        ],
    )
    revised_anchor_request = _cross_batch_clarity_request(first_revision=2)

    first_candidate = realtime_coach_candidate_events(first_request)[0]
    second_candidate = realtime_coach_candidate_events(second_request)[0]
    revised_candidate = realtime_coach_candidate_events(revised_anchor_request)[0]

    assert first_candidate.candidate_key == second_candidate.candidate_key
    assert revised_candidate.candidate_key != first_candidate.candidate_key
    assert set(second_candidate.evidence_segment_ids) & second_request.writable_paragraph_ids


def test_pi_trigger_covers_scene_and_cross_paragraph_positive_boundaries() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[4]
        / "tools/realtime_coach_eval/fixtures/private_coach_smoke.jsonl"
    )
    cases = [
        json.loads(line)
        for line in fixture_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    positive_cases = [case for case in cases if case["expected"]["action"] == "intervention"]
    negative_cases = [case for case in cases if case["expected"]["action"] == "silent"]

    def request_for(case: dict) -> RealtimeIntelligenceRequest:
        return RealtimeIntelligenceRequest.from_payload(
            meeting_id=str(case.get("session_id") or case["case_id"]),
            state_revision=case.get("state_revision") or 1,
            new_paragraphs=case.get("new_paragraphs") or [],
            context_paragraphs=case.get("context_paragraphs") or [],
            semantic_windows=case.get("semantic_windows") or [],
            rolling_state=case.get("rolling_state") or {},
            meeting_goal=case.get("meeting_goal"),
            coach_skill_id=case.get("coach_skill_id") or "general",
            allow_paragraph_revisions=False,
        )

    positive_gate = {
        case["case_id"]: should_run_realtime_coach(request_for(case), requested_runtime="pi")
        for case in positive_cases
    }
    assert all(positive_gate.values()), positive_gate
    negative_gate = {
        case["case_id"]: should_run_realtime_coach(request_for(case), requested_runtime="pi")
        for case in negative_cases
    }
    assert not any(negative_gate.values()), negative_gate

    for case in positive_cases:
        request = request_for(case)
        new_ids = {item.id for item in request.new_paragraphs}
        assert realtime_coach_candidate_events(request)
        assert all(
            set(candidate.evidence_segment_ids) <= new_ids
            for candidate in realtime_coach_candidate_events(request)
        )


def test_pi_trigger_exposes_stable_evidence_bound_candidate_events() -> None:
    request = _coach_request()

    first = realtime_coach_candidate_events(request)
    second = realtime_coach_candidate_events(request)

    assert first
    assert first[0].event_type == "question_pending"
    assert first[0].evidence_segment_ids == ("remote-4",)
    assert first[0].candidate_key == second[0].candidate_key
    assert first[0].candidate_key.startswith("coach-candidate:question_pending:")
    assert {item.event_type for item in first} >= {
        "question_pending",
        "commitment_without_condition",
    }


def test_pi_trigger_does_not_route_resolved_or_conditional_objections() -> None:
    conditional = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="如果全量压测通过，我们暂定周三上线，不过最终日期等验收确认后再承诺。",
            ),
        ),
    )
    resolved = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="担心的回滚风险已经在预发布环境验证，反对意见也逐条记录。",
            ),
        ),
    )

    assert realtime_coach_candidate_events(conditional) == ()
    assert realtime_coach_candidate_events(resolved) == ()


def test_pi_trigger_does_not_treat_a_subordinate_whether_clause_as_a_question() -> None:
    complete_experiment = replace(
        _coach_request(),
        coach_skill_id="brainstorm",
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text=(
                    "先做只读原型，请6名一线用户试用一周，以采纳率至少60%"
                    "且误报率不高于10%为阈值，达到后再决定是否扩大开发。"
                ),
            ),
        ),
        rolling_state={
            "topic": "智能摘要",
            "summary": "已定义最小实验、样本和判断指标。",
            "open_items": [{"text": "执行最小实验", "status": "open"}],
        },
    )
    direct_question = replace(
        complete_experiment,
        new_paragraphs=(
            replace(
                complete_experiment.new_paragraphs[0],
                text="这个实验是否已经有人负责",
            ),
        ),
    )

    assert realtime_coach_candidate_events(complete_experiment) == ()
    assert {item.event_type for item in realtime_coach_candidate_events(direct_question)} == {
        "question_pending",
    }

    bounded_experiment_with_yaobuyao = replace(
        complete_experiment,
        new_paragraphs=(
            replace(
                complete_experiment.new_paragraphs[0],
                text="先做一个不自动发送的推荐原型，找十位用户试用一周，用采纳率决定要不要开发正式版。",
            ),
        ),
    )
    assert realtime_coach_candidate_events(bounded_experiment_with_yaobuyao) == ()


def test_pi_trigger_does_not_treat_a_subordinate_how_clause_as_a_question() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="我们可以继续观察，也可以再看看其他团队怎么做，暂时还没有明确下一步。",
            ),
        ),
    )

    assert "question_pending" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


@pytest.mark.parametrize(
    "text",
    (
        "麻烦确认谁负责",
        "请确认谁来处理",
        "我们需要确认什么时候完成",
        "我还缺少",
        "请教练提示我还缺什么",
    ),
)
def test_pi_trigger_routes_unpunctuated_owner_and_deadline_requests(text: str) -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
    )

    assert "question_pending" in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


@pytest.mark.parametrize(
    "text",
    (
        "我们还没有指定回滚负责人和最终验收人请现在明确这两个人",
        "上线负责人还没定，验收人也待确认",
        "回滚负责人尚未明确",
        "我们计划周五上线，但是回滚负责人还没有确定，发布前还需要确认回滚条件和具体负责人。请给出现在最重要的一步。",
    ),
)
def test_pi_trigger_routes_explicit_execution_owner_gaps(text: str) -> None:
    request = replace(
        _coach_request(),
        coach_skill_id="project",
        new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
    )

    assert "missing_next_step" in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }
    assert should_run_realtime_coach(request, requested_runtime="pi") is True


@pytest.mark.parametrize(
    "text",
    (
        "没有明确谁负责修改，请先确认一下负责人和验收条件。",
        "还没明确谁来改，周三之前要回看。",
        "谁来修改还没定，先把验收条件补齐。",
        "没有确定由谁负责推进这个问题。",
        "我们计划周五上线，但是回滚负责人还没有确定，发布前还需要确认回滚条件和具体负责人。请给出现在最重要的一步。",
    ),
)
def test_pi_trigger_routes_natural_owner_gap_variants_from_live_asr(text: str) -> None:
    request = replace(
        _coach_request(),
        coach_skill_id="project",
        new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
    )

    assert "missing_next_step" in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }
    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=1_000,
    )
    assert result is not None
    assert result["origin"] == "local_reflex"
    assert result["intervention"].local_reflex_kind == "missing_next_step"


def test_pi_trigger_ignores_execution_gap_closed_later_in_same_final() -> None:
    request = replace(
        _coach_request(),
        coach_skill_id="project",
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="之前还没有指定回滚负责人，现在已经明确回滚负责人是李娜。",
            ),
        ),
    )

    assert "missing_next_step" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


def test_pi_trigger_does_not_treat_schedule_concerns_or_negated_dates_as_commitments() -> None:
    for text in (
        "大家主要担心上线时间，今天我还是想先补充背景，结论稍后再说。",
        "我暂时不想确定方向，也不想明确负责人和截止时间。",
        "这个版本不一定周五上线。",
        "这个方案的确定性还不足。",
    ):
        request = replace(
            _coach_request(),
            new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
        )
        assert "commitment_without_condition" not in {
            item.event_type for item in realtime_coach_candidate_events(request)
        }


def test_pi_trigger_does_not_treat_a_short_connective_as_an_objection() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="不过我觉得还可以继续补充背景。",
            ),
        ),
    )

    assert "objection_detected" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


@pytest.mark.parametrize(
    "text",
    (
        (
            "这个项目从上个月开始讨论，当时大家主要担心上线时间，"
            "也担心现有流程改动以后会不会影响其他团队。"
        ),
        "上周会上有人担心培训成本，这只是在回顾上次会议的背景。",
        "此前团队认为这个方案有问题，本段只记录历史背景。",
    ),
)
def test_pi_trigger_ignores_explicitly_historical_weak_concerns(text: str) -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
    )

    assert "objection_detected" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


@pytest.mark.parametrize(
    "text",
    (
        "我现在担心上线时间会挤压验证。",
        "大家担心目前的回滚风险还没有验证。",
        "之前大家担心成本，现在仍然担心合规风险。",
    ),
)
def test_pi_trigger_keeps_current_weak_concerns(text: str) -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(replace(_coach_request().new_paragraphs[0], text=text),),
    )

    assert "objection_detected" in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


def test_pi_trigger_lets_clarity_own_weak_concerns_in_the_same_episode() -> None:
    request = _cross_batch_clarity_request(
        latest_text="有人担心培训成本，不过我还要继续补充背景，暂时先不下结论。",
    )

    events = realtime_coach_candidate_events(request)
    assert {item.event_type for item in events} == {"monologue_duration"}
    assert events[0].evidence_segment_ids == (
        "clarity-prior-2",
        "clarity-fresh-3",
    )


@pytest.mark.parametrize(
    "risk",
    (
        "我目前担心这个方案存在严重安全风险",
        "我现在担心这个方案会泄露用户隐私",
        "我仍然担心这个方案不符合合规要求",
        "我当前担心这个方案会造成数据丢失",
    ),
)
def test_pi_trigger_keeps_current_first_person_high_loss_objection_with_clarity(
    risk: str,
) -> None:
    request = _cross_batch_clarity_request(
        latest_text=f"{risk}，不过我还要继续补充背景，暂时先不下结论。",
    )

    events = realtime_coach_candidate_events(request)

    assert {item.event_type for item in events} == {
        "objection_detected",
        "monologue_duration",
    }


@pytest.mark.parametrize(
    "concern",
    (
        "此前他们担心这个方案会造成数据丢失",
        "我之前担心这个方案会造成数据丢失，不过现在我还要继续补充背景",
    ),
)
def test_pi_trigger_lets_clarity_absorb_historical_or_third_party_high_loss_concern(
    concern: str,
) -> None:
    request = _cross_batch_clarity_request(
        latest_text=f"{concern}，不过我还要继续补充背景，暂时先不下结论。",
    )

    assert {
        item.event_type for item in realtime_coach_candidate_events(request)
    } == {"monologue_duration"}


def test_pi_trigger_keeps_an_unresolved_objection_candidate() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="但是回滚风险仍未验证，团队不能接受现在就上线。",
            ),
        ),
    )

    assert {item.event_type for item in realtime_coach_candidate_events(request)} == {
        "objection_detected",
    }


def test_pi_trigger_does_not_treat_pre_release_or_completed_validation_as_a_future_commitment() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="担心的回滚风险已经在预发布环境验证；由王经理负责，两周内通过验收视为成功。",
            ),
        ),
    )

    assert "commitment_without_condition" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


def test_pi_trigger_does_not_treat_an_explicit_evidence_backed_state_update_as_a_contradiction() -> None:
    request = replace(
        _coach_request(),
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="新报告证明已经达到五百并发；旧的未确认状态因此关闭。",
            ),
        ),
        context_paragraphs=(
            replace(
                _coach_request().context_paragraphs[0],
                text="此前全量压测尚未完成，没有确认达到五百并发。",
            ),
        ),
        rolling_state={"topic": "压测", "open_items": []},
    )

    assert "objection_detected" not in {
        item.event_type for item in realtime_coach_candidate_events(request)
    }


def test_pi_trigger_models_repetition_topic_drift_and_missing_next_step() -> None:
    request = replace(
        _coach_request(),
        meeting_goal="确认回滚负责人和下一步",
        new_paragraphs=(
            replace(
                _coach_request().new_paragraphs[0],
                text="先不讨论发布。这个方案需要再确认，这个方案需要再确认。今天先到这里。",
            ),
        ),
    )

    event_types = {item.event_type for item in realtime_coach_candidate_events(request)}

    assert {"goal_at_risk", "topic_drift", "repetition", "missing_next_step"} <= event_types


def _local_reflex_request(
    text: str,
    *,
    source_track: str = "microphone",
) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-local-reflex",
        state_revision=1,
        new_paragraphs=[
            {
                **_paragraph("local-reflex-fresh", text),
                "source_track": source_track,
                "correction_status": "no_change",
            }
        ],
        context_paragraphs=[],
        rolling_state={"open_items": []},
        allow_paragraph_revisions=False,
    )


def test_local_reflex_builds_an_immediate_evidence_bound_close_card() -> None:
    request = _local_reflex_request("那今天就先到这里。")

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=1_000,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.event_type == "execution_gap"
    assert intervention.origin == "local_reflex"
    assert intervention.local_reflex_kind == "missing_next_step"
    assert intervention.evidence_segment_ids == ("local-reflex-fresh",)
    assert intervention.evidence_quote == "那今天就先到这里"
    assert result["origin"] == "local_reflex"
    assert result["runtime_requested"] == "local_reflex"
    assert result["runtime_used"] == "local_reflex"
    assert result["local_reflex_kind"] == "missing_next_step"
    assert result["llm_called"] is False
    assert result["llm_call_status"] == "not_called"
    assert result["pi_provider_attempted"] is False
    assert result["valid_until_ms"] == 91_000
    intervention_payload = intervention.to_dict()
    assert intervention_payload["origin"] == "local_reflex"
    assert intervention_payload["runtime_used"] == "local_reflex"
    assert intervention_payload["pi_provider_attempted"] is False
    assert intervention_payload["valid_until_ms"] == result["valid_until_ms"]


@pytest.mark.parametrize(
    "text",
    [
        "回滚负责人还没有指定，验收人也待确认。",
        "目前最终验收人未定，先把方案发出去。",
    ],
)
def test_local_reflex_builds_immediate_owner_clarification_without_close_word(
    text: str,
) -> None:
    request = _local_reflex_request(text, source_track="system_audio")

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=1_000,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.local_reflex_kind == "missing_next_step"
    assert intervention.event_type == "execution_gap"
    assert intervention.evidence_quote in {
        "回滚负责人还没有指定，验收人也待确认",
        "目前最终验收人未定，先把方案发出去",
    }
    assert "谁负责推进" in intervention.recommendation
    assert intervention.origin == "local_reflex"


def test_local_reflex_owner_clarification_does_not_override_resolved_owner() -> None:
    request = _local_reflex_request("回滚负责人已经确认由李雷负责，验收人也已指定。")
    candidates = [{"event_type": "missing_next_step"}]

    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None


def test_local_reflex_pending_question_is_only_enabled_for_pi_timeout_fallback() -> None:
    request = _local_reflex_request("测试什么时候完成我先回答")
    candidates = [{"event_type": "question_pending"}]

    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None

    result = build_local_reflex_intervention(
        request,
        candidates,
        now_ms=1_000,
        allow_pending_question=True,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.local_reflex_kind == "pending_question"
    assert intervention.event_type == "question_to_user"
    assert intervention.evidence_quote == "测试什么时候完成我先回答"
    assert result["runtime_used"] == "local_reflex"
    assert result["pi_provider_attempted"] is False


@pytest.mark.parametrize(
    "text",
    [
        "我们已经把方案讨论完了今天先到这。",
        "那今天到这。",
        "先到这吧。",
    ],
)
def test_local_reflex_accepts_explicit_colloquial_close_at_sentence_end(
    text: str,
) -> None:
    request = _local_reflex_request(text)

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=1_000,
    )

    assert result is not None
    assert result["local_reflex_kind"] == "missing_next_step"
    assert result["intervention"].evidence_quote == text.rstrip("。")


def test_local_reflex_builds_a_two_quote_clarity_card() -> None:
    request = _cross_batch_clarity_request()

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=2_000,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.event_type == "communication_clarity"
    assert intervention.local_reflex_kind == "communication_clarity"
    assert intervention.evidence_segment_ids == (
        "clarity-prior-2",
        "clarity-fresh-3",
    )
    assert len(intervention.evidence_quote.splitlines()) == 2
    assert set(intervention.evidence_segment_ids) & request.writable_paragraph_ids


def test_local_reflex_accepts_two_exact_repetitions_inside_one_fresh_paragraph() -> None:
    repeated_clause = "我们先从当前背景开始说明"
    request = _local_reflex_request("。".join([repeated_clause] * 4) + "。")

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=2_000,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.local_reflex_kind == "communication_clarity"
    assert intervention.evidence_segment_ids == ("local-reflex-fresh",)
    assert intervention.evidence_quote.splitlines() == [repeated_clause, repeated_clause]


def test_local_reflex_builds_a_system_audio_strong_objection_card() -> None:
    request = _local_reflex_request(
        "我不同意这样继续推进。",
        source_track="system_audio",
    )

    result = build_local_reflex_intervention(
        request,
        realtime_coach_candidate_events(request),
        now_ms=3_000,
    )

    assert result is not None
    intervention = result["intervention"]
    assert intervention.event_type == "discovery_gap"
    assert intervention.local_reflex_kind == "strong_objection"
    assert intervention.evidence_quote == "我不同意这样继续推进"
    assert intervention.evidence_segment_ids == ("local-reflex-fresh",)
    assert "风险" in intervention.recommendation
    assert "条件" in intervention.recommendation


@pytest.mark.parametrize(
    "text",
    [
        "那今天先到这里，下一步我们会后跟进。",
        "那今天先到这里，接下来由已经确认的人处理。",
        "这个话题就这样分析，我们继续。",
        "我们先到这里分析一下，再继续。",
        "我们先到这分析一下，再继续。",
        "今天已经到这里了，我们还没有开始。",
    ],
)
def test_local_reflex_close_requires_an_explicit_fresh_close_without_a_next_step(
    text: str,
) -> None:
    request = _local_reflex_request(text)
    candidates = [
        {
            "event_type": "missing_next_step",
            "evidence_segment_ids": ["local-reflex-fresh"],
        }
    ]

    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None


@pytest.mark.parametrize(
    ("text", "source_track"),
    [
        ("我不同意这样继续推进。", "microphone"),
        ("之前我不同意这样继续推进。", "system_audio"),
        ("之前我不同意，现在我们讨论别的事。", "system_audio"),
        ("我之前反对，现在同意继续。", "system_audio"),
        ("我有一点担心。", "system_audio"),
        ("这个连接不行了。", "system_audio"),
        ("不能接受的问题已经解决。", "system_audio"),
    ],
)
def test_local_reflex_strong_objection_rejects_unsafe_or_resolved_evidence(
    text: str,
    source_track: str,
) -> None:
    request = _local_reflex_request(text, source_track=source_track)
    candidates = [
        {
            "event_type": "objection_detected",
            "evidence_segment_ids": ["local-reflex-fresh"],
        }
    ]

    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None


def test_local_reflex_close_does_not_hide_a_weak_objection_reserved_for_pi() -> None:
    request = _local_reflex_request(
        "我担心这个方案。那今天就先到这里。",
        source_track="system_audio",
    )
    candidates = realtime_coach_candidate_events(request)

    assert {item.event_type for item in candidates} == {
        "objection_detected",
        "missing_next_step",
    }
    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None


@pytest.mark.parametrize(
    "blocking_type",
    [
        "question_pending",
        "commitment_without_condition",
        "goal_at_risk",
        "topic_drift",
        "contradiction",
        "decision_readiness",
        "execution_gap",
        "discovery_gap",
        "experiment_gap",
    ],
)
def test_local_reflex_does_not_swallow_a_higher_value_pi_candidate(
    blocking_type: str,
) -> None:
    request = _local_reflex_request("那今天就先到这里。")
    candidates = [
        {"event_type": "missing_next_step"},
        {"event_type": blocking_type},
    ]

    assert build_local_reflex_intervention(request, candidates, now_ms=1_000) is None


def test_local_reflex_is_fail_closed_for_candidate_mismatch_and_bad_clock() -> None:
    closing = _local_reflex_request("那今天就先到这里。")
    objection = _local_reflex_request(
        "我不同意这样继续推进。",
        source_track="system_audio",
    )

    assert build_local_reflex_intervention(closing, [], now_ms=1_000) is None
    assert (
        build_local_reflex_intervention(
            objection,
            [{"event_type": "missing_next_step"}],
            now_ms=1_000,
        )
        is None
    )
    with pytest.raises(ValueError, match="now_ms"):
        build_local_reflex_intervention(closing, [], now_ms=-1)


def test_coach_prompt_is_focused_on_timely_action_instead_of_summary() -> None:
    messages = build_realtime_coach_messages(_coach_request())
    payload = json.loads(messages[1]["content"])

    assert [item["id"] for item in payload["new_paragraphs"]] == ["remote-4"]
    assert payload["meeting_goal"] == "避免在压测完成前承诺上线日期"
    assert "任务不是总结" in messages[0]["content"]
    assert "不要为了显得有帮助" in messages[0]["content"]


def test_coach_prompt_loads_the_selected_scene_skill() -> None:
    request = replace(_coach_request(), coach_skill_id="interview")

    messages = build_realtime_coach_messages(request)
    payload = json.loads(messages[1]["content"])

    assert payload["coach_skill_id"] == "interview"
    assert "User interview coach" in messages[0]["content"]
    assert "discovery_gap" in messages[0]["content"]
    assert "discovery_gap" in payload["output_contract"]["intervention"]["event_type"]
    assert "execution_gap" not in payload["output_contract"]["intervention"]["event_type"]


def test_coach_parser_rejects_an_event_from_an_inactive_scene_skill() -> None:
    request = replace(_coach_request(), coach_skill_id="interview")
    content = json.dumps(
        {
            "intervention": {
                "event_type": "execution_gap",
                "title": "缺少执行条件",
                "recommendation": "请先确认负责人和截止时间，再结束当前话题。",
                "reason": "这不是用户访谈技能允许的场景介入。",
                "evidence_segment_ids": ["remote-4"],
                "evidence_quote": "周五一定上线吗",
                "urgency": "medium",
                "confidence": 0.9,
            }
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError, match="unsupported"):
        parse_realtime_coach_response(content, request=request)


def test_coach_parser_builds_an_evidence_bound_private_intervention() -> None:
    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "commitment_risk",
                    "title": "先限定承诺条件",
                    "recommendation": "可以把周五作为目标，但需要以压测达标为上线条件。",
                    "reason": "对方要求确定日期，但压测尚未完成。",
                    "evidence_segment_ids": ["local-3", "remote-4"],
                    "evidence_quote": "压测还没有完成\n周五一定上线吗",
                    "urgency": "high",
                    "confidence": 0.91,
                }
            },
            ensure_ascii=False,
        ),
        request=_coach_request(),
    )

    assert intervention is not None
    assert intervention.event_type == "commitment_risk"
    assert intervention.evidence_segment_ids == ("local-3", "remote-4")
    response = apply_coach_intervention(
        parse_realtime_intelligence_response(
            json.dumps(
                {
                    "paragraph_revisions": [],
                    "topic_update": None,
                    "state_changes": [],
                    "follow_up": None,
                }
            ),
            request=_coach_request(),
        ),
        intervention,
    )
    assert response.follow_up is not None
    assert response.follow_up.coach_event_type == "commitment_risk"
    assert response.follow_up.question == intervention.recommendation


def test_coach_parser_rejects_a_quote_not_present_in_evidence() -> None:
    content = json.dumps(
        {
            "intervention": {
                "event_type": "commitment_risk",
                "title": "虚构证据",
                "recommendation": "先确认条件，再给出准确日期。",
                "reason": "证据并不存在。",
                "evidence_segment_ids": ["remote-4"],
                "evidence_quote": "客户要求本周无条件交付",
                "urgency": "high",
                "confidence": 0.9,
            }
        },
        ensure_ascii=False,
    )
    with pytest.raises(IntelligenceResponseValidationError, match="meeting evidence"):
        parse_realtime_coach_response(content, request=_coach_request())


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("recommendation", "请让王工负责，并在今天下午六点前完成修改。", "deadline, owner"),
        ("reason", "负责人是王工，王工已经确认通过。", "owner, state"),
        ("title", "监控阈值需要修改", "material_term"),
    ],
)
def test_coach_parser_rejects_material_claims_absent_from_cited_evidence(
    field: str,
    value: str,
    match: str,
) -> None:
    intervention = {
        "event_type": "question_to_user",
        "title": "先确认原问题",
        "recommendation": "请确认刚才的问题，再给出准确答复。",
        "reason": "对方提出了一个待回答的问题。",
        "evidence_segment_ids": ["remote-4"],
        "evidence_quote": "周五一定上线吗",
        "urgency": "high",
        "confidence": 0.91,
    }
    intervention[field] = value

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps({"intervention": intervention}, ensure_ascii=False),
            request=_coach_request(),
        )

    assert caught.value.category == "semantic_safety"
    for expected in match.split(", "):
        assert expected in str(caught.value)


def test_coach_parser_accepts_equivalent_assessment_state_wording() -> None:
    """Equivalent negative assessment wording must remain quote-grounded."""

    evidence = "A方案的回滚成本还没有评估。"
    request = replace(_reviewed_quote_scope_request(evidence), coach_skill_id="decision")
    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "decision_readiness",
                    "title": "回滚成本未评估",
                    "recommendation": "A方案回滚成本还没评估，先确认后再定案？",
                    "reason": "先核实回滚成本，再决定是否定案。",
                    "evidence_segment_ids": ["reviewed-asr"],
                    "evidence_quote": evidence,
                    "urgency": "high",
                    "confidence": 0.9,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.event_type == "decision_readiness"


def test_coach_parser_accepts_unresolved_owner_wording_equivalent_to_hai_mei_ding() -> None:
    """Do not reject a grounded paraphrase of an unresolved owner question."""

    evidence = "复盘报告我们下周一发，但监控阈值谁来改还没定。"
    request = replace(_reviewed_quote_scope_request(evidence), coach_skill_id="decision")
    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "decision_readiness",
                    "title": "监控阈值负责人未定",
                    "recommendation": "先确认监控阈值由谁来改。",
                    "reason": "原话中的负责人还没定，需要先问清楚。",
                    "evidence_segment_ids": ["reviewed-asr"],
                    "evidence_quote": evidence,
                    "urgency": "high",
                    "confidence": 0.9,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.event_type == "decision_readiness"


def test_coach_parser_rejects_deadline_borrowed_from_another_topic_clause() -> None:
    evidence = "复盘报告我们下周一发，但监控阈值谁来改还没定。"
    request = replace(_reviewed_quote_scope_request(evidence), coach_skill_id="decision")

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps(
                {
                    "intervention": {
                        "event_type": "decision_readiness",
                        "title": "监控阈值负责人和期限待确认",
                        "recommendation": "监控阈值具体由谁来改？需要到下周一定下来。",
                        "reason": "监控阈值负责人还没定，需要先问清楚。",
                        "evidence_segment_ids": ["reviewed-asr"],
                        "evidence_quote": evidence,
                        "urgency": "high",
                        "confidence": 0.9,
                    }
                },
                ensure_ascii=False,
            ),
            request=request,
        )

    assert caught.value.category == "semantic_safety"
    assert "deadline_scope" in str(caught.value)


def test_coach_parser_accepts_deadline_scoped_to_same_owner_gap_clause() -> None:
    evidence = "监控阈值由谁来改，需要在下周一前定下来？"
    request = replace(_reviewed_quote_scope_request(evidence), coach_skill_id="decision")
    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "decision_readiness",
                    "title": "确认监控阈值负责人和期限",
                    "recommendation": "监控阈值具体由谁来改，需要在下周一前定下来？",
                    "reason": "原话同时询问监控阈值负责人和期限。",
                    "evidence_segment_ids": ["reviewed-asr"],
                    "evidence_quote": evidence,
                    "urgency": "high",
                    "confidence": 0.9,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.event_type == "decision_readiness"


def _scene_c_asr_request(*, correction_status: str) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="scene-c-asr-risk",
        state_revision=2,
        new_paragraphs=[
            {
                **_paragraph("microphone:e1:vad_endpoint_002", "一发但监控预值谁来改"),
                "source_track": "microphone",
                "correction_status": correction_status,
            }
        ],
        context_paragraphs=[
            {
                **_paragraph("microphone:e1:vad_endpoint_001", "c复盘报告我们下周"),
                "source_track": "microphone",
                "correction_status": "no_change",
            }
        ],
        rolling_state={},
    )


def test_scene_c_asr_gate_rejects_silent_critical_term_rewrite() -> None:
    request = _scene_c_asr_request(correction_status="failed_preserved_original")
    content = json.dumps(
        {
            "intervention": {
                "event_type": "question_to_user",
                "title": "回应监控阈值负责人问题",
                "recommendation": "请直接确认：一发后监控阈值由谁负责修改？",
                "reason": "原话询问监控阈值由谁修改。",
                "evidence_segment_ids": ["microphone:e1:vad_endpoint_002"],
                "evidence_quote": "一发但监控预值谁来改",
                "urgency": "high",
                "confidence": 0.91,
            }
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(content, request=request)

    assert caught.value.category == "semantic_safety"
    assert "material_term" in str(caught.value)


def _reviewed_quote_scope_request(text: str) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="reviewed-quote-scope",
        state_revision=1,
        new_paragraphs=[
            {
                **_paragraph("reviewed-asr", text),
                "source_track": "system_audio",
                "correction_status": "no_change",
            }
        ],
        context_paragraphs=[],
        rolling_state={},
    )


@pytest.mark.parametrize(
    ("paragraph_text", "evidence_quote", "title", "recommendation", "reason", "violation"),
    [
        (
            "一发但监控预值谁来改。后面确认监控阈值只是口误。",
            "一发但监控预值谁来改",
            "回应监控阈值问题",
            "请直接确认监控阈值由谁修改。",
            "当前需要回应监控阈值问题。",
            "material_term",
        ),
        (
            "这件事请尽快处理，今天下午六点前完成。",
            "这件事请尽快处理",
            "明确处理时间",
            "请确认今天下午六点前完成。",
            "当前需要明确完成时间。",
            "deadline",
        ),
        (
            "这项工作还没有完成，另一个事项已经完成。",
            "这项工作还没有完成",
            "确认工作状态",
            "请确认这项工作已经完成。",
            "当前需要确认工作状态。",
            "state",
        ),
        (
            "谁来修改配置？负责人是王工，今天下午六点前完成。",
            "谁来修改配置？",
            "确认执行信息",
            "请确认负责人是王工，今天下午六点前完成。",
            "当前需要确认执行安排。",
            "deadline, owner",
        ),
        (
            "先讨论发布节奏，预算定为 300 万。",
            "先讨论发布节奏",
            "确认预算",
            "请确认 300 万预算是否已获批。",
            "当前需要确认预算安排。",
            "number",
        ),
        (
            "王工参加会议，负责人是李工。",
            "王工参加会议",
            "确认负责人",
            "请由王工负责推进。",
            "当前需要明确执行责任。",
            "owner",
        ),
    ],
)
def test_coach_parser_rejects_material_claims_outside_the_exact_quote(
    paragraph_text: str,
    evidence_quote: str,
    title: str,
    recommendation: str,
    reason: str,
    violation: str,
) -> None:
    request = _reviewed_quote_scope_request(paragraph_text)

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps(
                {
                    "intervention": {
                        "event_type": "question_to_user",
                        "title": title,
                        "recommendation": recommendation,
                        "reason": reason,
                        "evidence_segment_ids": ["reviewed-asr"],
                        "evidence_quote": evidence_quote,
                        "urgency": "high",
                        "confidence": 0.91,
                    }
                },
                ensure_ascii=False,
            ),
            request=request,
        )

    assert caught.value.category == "semantic_safety"
    for expected in violation.split(", "):
        assert expected in str(caught.value)


def test_coach_parser_rejects_glossary_product_outside_exact_quote() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="reviewed-product-quote-scope",
        state_revision=1,
        new_paragraphs=[
            {
                **_paragraph("reviewed-asr", "先讨论网络方案，ApolloEdge 由架构组维护。"),
                "source_track": "system_audio",
                "correction_status": "no_change",
            }
        ],
        context_paragraphs=[],
        rolling_state={},
        glossary=["ApolloEdge"],
    )

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps(
                {
                    "intervention": {
                        "event_type": "question_to_user",
                        "title": "确认 ApolloEdge 的责任边界",
                        "recommendation": "请确认 ApolloEdge 是否由架构组维护。",
                        "reason": "当前需要明确产品责任。",
                        "evidence_segment_ids": ["reviewed-asr"],
                        "evidence_quote": "先讨论网络方案",
                        "urgency": "high",
                        "confidence": 0.91,
                    }
                },
                ensure_ascii=False,
            ),
            request=request,
        )

    assert caught.value.category == "semantic_safety"
    assert "product_name" in str(caught.value)


def test_direct_and_pi_paths_share_quote_level_material_fact_gate() -> None:
    request = _reviewed_quote_scope_request("谁来修改配置？负责人是王工，今天下午六点前完成。")
    unsafe_intervention = {
        "event_type": "question_to_user",
        "title": "确认执行信息",
        "recommendation": "请确认负责人是王工，今天下午六点前完成。",
        "reason": "当前需要确认执行安排。",
        "evidence_segment_ids": ["reviewed-asr"],
        "evidence_quote": "谁来修改配置？",
        "urgency": "high",
        "confidence": 0.91,
    }

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps({"intervention": unsafe_intervention}, ensure_ascii=False),
            request=request,
        )
    assert caught.value.category == "semantic_safety"

    async def direct_scenario() -> None:
        with pytest.raises(IntelligenceResponseValidationError) as caught:
            await run_realtime_coach(
                request=request,
                provider=_Provider(json.dumps({"intervention": unsafe_intervention}, ensure_ascii=False)),
            )
        assert caught.value.category == "semantic_safety"

    asyncio.run(direct_scenario())

    pi_runtime = _PiRuntime(
        {
            "action": "intervention",
            "intervention": unsafe_intervention,
            "metrics": {"turns": 1, "tool_calls": 1},
            "decision_reason": None,
        }
    )

    async def scenario() -> None:
        with pytest.raises(IntelligenceResponseValidationError) as caught:
            await run_realtime_coach_via_pi(
                request=request,
                pi_runtime=pi_runtime,
                provider_config=_pi_provider_config(),
            )
        assert caught.value.category == "semantic_safety"

    asyncio.run(scenario())


def test_scene_c_provisional_asr_allows_only_non_assertive_clarification() -> None:
    request = _scene_c_asr_request(correction_status="failed_preserved_original")
    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "question_to_user",
                    "title": "先澄清原问题",
                    "recommendation": "请确认刚才问的是哪项监控指标，以及由谁修改。",
                    "reason": "这段原话仍需澄清后再回答。",
                    "evidence_segment_ids": ["microphone:e1:vad_endpoint_002"],
                    "evidence_quote": "一发但监控预值谁来改",
                    "urgency": "high",
                    "confidence": 0.91,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.recommendation == "请确认刚才问的是哪项监控指标，以及由谁修改。"


def test_provisional_asr_rejects_a_grounded_material_assertion() -> None:
    request = _scene_c_asr_request(correction_status="processing")
    content = json.dumps(
        {
            "intervention": {
                "event_type": "question_to_user",
                "title": "直接回应监控问题",
                "recommendation": "监控预值由值班负责人负责修改。",
                "reason": "原话提出了监控预值修改问题。",
                "evidence_segment_ids": ["microphone:e1:vad_endpoint_002"],
                "evidence_quote": "一发但监控预值谁来改",
                "urgency": "high",
                "confidence": 0.91,
            }
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(content, request=request)

    assert caught.value.category == "semantic_safety"
    assert "provisional ASR evidence" in str(caught.value)


@pytest.mark.parametrize(
    ("evidence", "recommendation", "glossary"),
    [
        ("当前并发达到五百。", "当前并发已经达到五百，可以继续推进。", []),
        ("本次不能上线。", "本次不能上线，需要先处理风险。", []),
        ("王工是负责人。", "王工是负责人，请直接联系王工推进。", []),
        ("今天下午六点前完成。", "今天下午六点前完成，请按此时间推进。", []),
        ("ApolloEdge 当前可用。", "ApolloEdge 当前可用，可以继续推进。", ["ApolloEdge"]),
    ],
)
def test_unreviewed_asr_cannot_support_high_risk_material_assertions(
    evidence: str,
    recommendation: str,
    glossary: list[str],
) -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="unreviewed-high-risk-asr",
        state_revision=1,
        new_paragraphs=[
            {
                **_paragraph("raw-asr", evidence),
                "source_track": "microphone",
                "correction_status": "unknown",
            }
        ],
        context_paragraphs=[],
        rolling_state={},
        glossary=glossary,
    )

    with pytest.raises(IntelligenceResponseValidationError) as caught:
        parse_realtime_coach_response(
            json.dumps(
                {
                    "intervention": {
                        "event_type": "question_to_user",
                        "title": "及时回应当前信息",
                        "recommendation": recommendation,
                        "reason": "原话包含需要及时回应的信息。",
                        "evidence_segment_ids": ["raw-asr"],
                        "evidence_quote": evidence,
                        "urgency": "high",
                        "confidence": 0.99,
                    }
                },
                ensure_ascii=False,
            ),
            request=request,
        )

    assert caught.value.category == "semantic_safety"
    assert "provisional ASR evidence" in str(caught.value)


def test_reviewed_asr_can_support_grounded_high_risk_material_assertions() -> None:
    evidence = "ApolloEdge 由王工负责，今天下午六点前完成，并发达到五百。"
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="reviewed-high-risk-asr",
        state_revision=1,
        new_paragraphs=[
            {
                **_paragraph("reviewed-asr", evidence),
                "source_track": "microphone",
                "correction_status": "no_change",
            }
        ],
        context_paragraphs=[],
        rolling_state={},
        glossary=["ApolloEdge"],
    )

    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "question_to_user",
                    "title": "确认执行信息",
                    "recommendation": "请复述确认：ApolloEdge 由王工负责，今天下午六点前完成，并发达到五百。",
                    "reason": "原话已经给出负责人、期限和并发目标。",
                    "evidence_segment_ids": ["reviewed-asr"],
                    "evidence_quote": evidence,
                    "urgency": "high",
                    "confidence": 0.91,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.evidence_segment_ids == ("reviewed-asr",)


def test_coach_prompt_exposes_asr_evidence_quality_without_rewriting_text() -> None:
    request = _scene_c_asr_request(correction_status="failed_preserved_original")

    payload = json.loads(build_realtime_coach_messages(request)[1]["content"])

    assert payload["new_paragraphs"][0]["text"] == "一发但监控预值谁来改"
    assert payload["new_paragraphs"][0]["correction_status"] == "failed_preserved_original"
    assert payload["new_paragraphs"][0]["evidence_quality"] == "provisional"
    assert payload["context_paragraphs"][0]["text"] == "c复盘报告我们下周"
    assert payload["context_paragraphs"][0]["evidence_quality"] == "provisional"
    assert "correction_status" in build_realtime_coach_messages(request)[0]["content"]


def test_coach_parser_accepts_grounded_communication_clarity_intervention() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-clarity",
        state_revision=2,
        new_paragraphs=[
            {
                **_paragraph("local-1", "说白了直播很重要，大家说对不对。"),
                "source_track": "microphone",
            },
            {
                **_paragraph("local-2", "所以说直播真的很重要，你们觉得我说得对吗。"),
                "source_track": "microphone",
            },
        ],
        context_paragraphs=[],
        rolling_state={},
    )

    intervention = parse_realtime_coach_response(
        json.dumps(
            {
                "intervention": {
                    "event_type": "communication_clarity",
                    "title": "先收束核心观点",
                    "recommendation": "下一句先给结论：直播的价值是持续交流；然后只补一个例子。",
                    "reason": "连续两段重复强调重要性，但还没有形成清晰结论。",
                    "evidence_segment_ids": ["local-1", "local-2"],
                    "evidence_quote": "说白了直播很重要\n所以说直播真的很重要",
                    "urgency": "medium",
                    "confidence": 0.9,
                }
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    assert intervention is not None
    assert intervention.event_type == "communication_clarity"


def test_coach_parser_rejects_communication_clarity_with_one_fragment() -> None:
    content = json.dumps(
        {
            "intervention": {
                "event_type": "communication_clarity",
                "title": "表达需要收束",
                "recommendation": "下一句先给出明确结论，再补充一个具体例子。",
                "reason": "没有足够证据证明这是持续表达模式。",
                "evidence_segment_ids": ["remote-4"],
                "evidence_quote": "周五一定上线吗",
                "urgency": "medium",
                "confidence": 0.9,
            }
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError, match="two verbatim"):
        parse_realtime_coach_response(content, request=_coach_request())


def test_coach_runner_suppresses_low_confidence_interventions() -> None:
    asyncio.run(_test_coach_runner_suppresses_low_confidence_interventions())


async def _test_coach_runner_suppresses_low_confidence_interventions() -> None:
    provider = _Provider(
        json.dumps(
            {
                "intervention": {
                    "event_type": "question_to_user",
                    "title": "可能需要回答",
                    "recommendation": "我先确认当前状态，再给出准确上线日期。",
                    "reason": "对方询问了上线时间。",
                    "evidence_segment_ids": ["remote-4"],
                    "evidence_quote": "周五一定上线吗",
                    "urgency": "medium",
                    "confidence": 0.62,
                }
            },
            ensure_ascii=False,
        )
    )

    result = await run_realtime_coach(request=_coach_request(), provider=provider)

    assert result["intervention"] is None
    assert result["ttft_ms"] == pytest.approx(400)
    assert result["decision_latency_ms"] == pytest.approx(800)
    assert provider.parameters["max_completion_tokens"] == 768
    assert provider.idempotency_key.endswith(":coach:v1")


class _PiRuntime:
    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.payload = None

    async def evaluate(self, payload):
        self.payload = payload
        if self.error is not None:
            raise self.error
        return self.response


def _pi_provider_config() -> dict:
    return {
        "base_url": "https://llm.example.test",
        "api_key": "test-key",
        "model": "coach-model",
        "api_style": "chat_completions",
        "timeout_seconds": 12,
    }


def test_pi_coach_runner_preserves_the_existing_evidence_contract() -> None:
    asyncio.run(_test_pi_coach_runner_preserves_the_existing_evidence_contract())


async def _test_pi_coach_runner_preserves_the_existing_evidence_contract() -> None:
    request = _coach_request()
    quote = request.new_paragraphs[0].text
    pi_runtime = _PiRuntime(
        {
            "action": "intervention",
            "intervention": {
                "event_type": "question_to_user",
                "title": "Answer now",
                "recommendation": "State the dependency before promising a date.",
                "reason": "The remote party asked for a firm date.",
                "evidence_segment_ids": ["remote-4"],
                "evidence_quote": quote,
                "urgency": "high",
                "confidence": 0.92,
            },
            "metrics": {
                "elapsed_ms": 840,
                "turns": 2,
                "tool_calls": 2,
                "context_reads": 1,
                "checklist_reviewed": True,
                "checklist_reviews": 1,
                "history_searches": 1,
                "history_results": 2,
                "usage": {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110},
            },
            "decision_reason": None,
        }
    )
    usages = []

    result = await run_realtime_coach_via_pi(
        request=request,
        pi_runtime=pi_runtime,
        provider_config=_pi_provider_config(),
        on_usage=lambda usage, attempt: usages.append((attempt, usage)),
    )

    assert result["intervention"] is not None
    assert result["intervention"].evidence_segment_ids == ("remote-4",)
    assert result["transport_mode"] == "pi_agent_jsonl"
    assert result["ttft_ms"] is None
    assert result["decision_latency_ms"] == pytest.approx(840)
    assert result["timings"] == {
        "clock": None,
        "started_at_ms": None,
        "first_token_at_ms": None,
        "completed_at_ms": None,
    }
    assert result["agent_metrics"]["turns"] == 2
    assert result["agent_metrics"]["checklist_reviewed"] is True
    assert result["decision_reason"] is None
    assert result["origin"] == "pi"
    assert result["status"] == "intervention"
    assert result["status_reason"] == "intervention_submitted"
    assert result["provenance_version"] == "realtime_coach_provenance.v1"
    assert result["run_id"].endswith(":coach")
    assert result["decision_id"].startswith("coach-decision:")
    assert result["evidence_revision"].startswith("coach-evidence:4:")
    assert pi_runtime.payload["context"]["new_paragraphs"][0]["source_track"] == "system_audio"
    assert pi_runtime.payload["context"]["coach_skill"]["id"] == "general"
    assert pi_runtime.payload["provider"]["model"] == "coach-model"
    assert usages == [(1, {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110})]


def test_pi_coach_runner_exposes_only_observed_timing_stages() -> None:
    asyncio.run(_test_pi_coach_runner_exposes_only_observed_timing_stages())


async def _test_pi_coach_runner_exposes_only_observed_timing_stages() -> None:
    result = await run_realtime_coach_via_pi(
        request=_coach_request(),
        pi_runtime=_PiRuntime(
            {
                "action": "silent",
                "intervention": None,
                "decision_reason": "No actionable moment.",
                "metrics": {
                    "elapsed_ms": 999,
                    "decision_latency_ms": 840,
                    "ttft_ms": 125,
                    "timings": {
                        "clock": "unix_epoch_ms",
                        "started_at_ms": 1_000,
                        "first_token_at_ms": 1_125,
                        "completed_at_ms": 1_840,
                    },
                },
            }
        ),
        provider_config=_pi_provider_config(),
    )

    assert result["ttft_ms"] == pytest.approx(125)
    assert result["decision_latency_ms"] == pytest.approx(840)
    assert result["timings"] == {
        "clock": "unix_epoch_ms",
        "started_at_ms": 1_000,
        "first_token_at_ms": 1_125,
        "completed_at_ms": 1_840,
    }


@pytest.mark.parametrize("invalid_ttft", [True, -1, 900, float("inf"), float("nan"), "840"])
def test_pi_coach_runner_does_not_publish_invalid_ttft(invalid_ttft) -> None:
    async def scenario() -> None:
        result = await run_realtime_coach_via_pi(
            request=_coach_request(),
            pi_runtime=_PiRuntime(
                {
                    "action": "silent",
                    "intervention": None,
                    "decision_reason": "No actionable moment.",
                    "metrics": {"elapsed_ms": 840, "ttft_ms": invalid_ttft},
                }
            ),
            provider_config=_pi_provider_config(),
        )
        assert result["ttft_ms"] is None

    asyncio.run(scenario())


def test_pi_coach_runner_rejects_timestamps_without_an_epoch_clock() -> None:
    async def scenario() -> None:
        result = await run_realtime_coach_via_pi(
            request=_coach_request(),
            pi_runtime=_PiRuntime(
                {
                    "action": "silent",
                    "intervention": None,
                    "decision_reason": "No actionable moment.",
                    "metrics": {
                        "elapsed_ms": 840,
                        "timings": {
                            "clock": "monotonic_ms",
                            "started_at_ms": 1_000,
                            "first_token_at_ms": 1_125,
                            "completed_at_ms": 1_840,
                        },
                    },
                }
            ),
            provider_config=_pi_provider_config(),
        )
        assert result["timings"] == {
            "clock": None,
            "started_at_ms": None,
            "first_token_at_ms": None,
            "completed_at_ms": None,
        }

    asyncio.run(scenario())


def test_pi_coach_suppresses_an_intervention_grounded_only_in_old_context() -> None:
    asyncio.run(_test_pi_coach_suppresses_an_intervention_grounded_only_in_old_context())


async def _test_pi_coach_suppresses_an_intervention_grounded_only_in_old_context() -> None:
    request = _coach_request()
    pi_runtime = _PiRuntime(
        {
            "action": "intervention",
            "intervention": {
                "event_type": "commitment_risk",
                "title": "重复提醒",
                "recommendation": "请再次说明压测完成后才能承诺上线日期。",
                "reason": "历史上下文提到压测尚未完成。",
                "evidence_segment_ids": ["local-3"],
                "evidence_quote": request.context_paragraphs[0].text,
                "urgency": "medium",
                "confidence": 0.91,
            },
            "metrics": {"turns": 1, "tool_calls": 1},
            "decision_reason": None,
        }
    )

    result = await run_realtime_coach_routed(
        request=request,
        provider=_Provider(json.dumps({"intervention": None})),
        requested_runtime="pi",
        pi_runtime=pi_runtime,
        pi_provider_config=_pi_provider_config(),
    )

    assert result["intervention"] is None
    assert result["runtime_used"] == "pi"
    assert result["decision_reason"] == ("Pi 建议未引用本轮新内容，已抑制重复提醒，保留上一条有依据建议。")
    assert result["agent_metrics"]["intervention_suppressed"] is True
    assert result["agent_metrics"]["suppression_reason"] == "stale_evidence"
    assert result["origin"] == "pi"
    assert result["status"] == "stale"
    assert result["status_reason"] == "stale_evidence"


def test_pi_coach_allows_combined_new_and_historical_evidence() -> None:
    asyncio.run(_test_pi_coach_allows_combined_new_and_historical_evidence())


async def _test_pi_coach_allows_combined_new_and_historical_evidence() -> None:
    request = _coach_request()
    pi_runtime = _PiRuntime(
        {
            "action": "intervention",
            "intervention": {
                "event_type": "commitment_risk",
                "title": "先限定承诺条件",
                "recommendation": "先说明压测尚未完成，再把周五定义为有条件目标。",
                "reason": "新问题要求明确承诺，而历史证据表明压测尚未完成。",
                "evidence_segment_ids": ["local-3", "remote-4"],
                "evidence_quote": (
                    request.context_paragraphs[0].text
                    + "\n"
                    + request.new_paragraphs[0].text
                ),
                "urgency": "high",
                "confidence": 0.93,
            },
            "metrics": {"turns": 1, "tool_calls": 1},
            "decision_reason": None,
        }
    )

    result = await run_realtime_coach_routed(
        request=request,
        provider=_Provider(json.dumps({"intervention": None})),
        requested_runtime="pi",
        pi_runtime=pi_runtime,
        pi_provider_config=_pi_provider_config(),
    )

    assert result["intervention"] is not None
    assert result["intervention"].evidence_segment_ids == ("local-3", "remote-4")
    assert result["runtime_used"] == "pi"
    assert result["agent_metrics"].get("intervention_suppressed") is None


def test_pi_runtime_failure_falls_back_to_the_direct_coach() -> None:
    asyncio.run(_test_pi_runtime_failure_falls_back_to_the_direct_coach())


async def _test_pi_runtime_failure_falls_back_to_the_direct_coach() -> None:
    class ExpectedPiError(RuntimeError):
        code = "pi_unavailable"

    direct_provider = _Provider(json.dumps({"intervention": None}))
    result = await run_realtime_coach_routed(
        request=_coach_request(),
        provider=direct_provider,
        requested_runtime="pi",
        pi_runtime=_PiRuntime(error=ExpectedPiError("not installed")),
        pi_provider_config=_pi_provider_config(),
    )

    assert result["intervention"] is None
    assert result["runtime_requested"] == "pi"
    assert result["runtime_used"] == "direct"
    assert result["fallback_error_code"] == "pi_unavailable"
    assert result["fallback_reason"] == "runtime_unavailable"
    assert result["origin"] == "direct_fallback"
    assert result["status"] == "protected_silent"
    assert result["status_reason"] == "no_actionable_intervention"
    assert result["decision_reason"]
    assert result["decision_id"].startswith("coach-decision:")
    assert len(direct_provider.calls) == 1


def test_pi_fallback_reason_redacts_provider_detail() -> None:
    class ExpectedPiError(RuntimeError):
        code = "agent_provider_error"

    error = ExpectedPiError("Upstream service temporarily unavailable: request body omitted")

    assert _pi_fallback_reason(error) == "provider_temporarily_unavailable"


def test_pi_agent_total_deadline_is_classified_as_a_timeout_without_detail() -> None:
    class ExpectedPiError(RuntimeError):
        code = "agent_deadline_exceeded"

    assert _pi_fallback_reason(ExpectedPiError("bounded decision ended")) == "provider_timeout"


def test_pi_provider_timeout_does_not_double_call_the_model() -> None:
    asyncio.run(_test_pi_provider_timeout_does_not_double_call_the_model())


async def _test_pi_provider_timeout_does_not_double_call_the_model() -> None:
    class ExpectedPiError(RuntimeError):
        code = "agent_provider_error"

    direct_provider = _Provider(json.dumps({"intervention": None}))
    result = await run_realtime_coach_routed(
        request=_coach_request(),
        provider=direct_provider,
        requested_runtime="pi",
        pi_runtime=_PiRuntime(error=ExpectedPiError("provider timeout")),
        pi_provider_config=_pi_provider_config(),
    )

    assert result["intervention"] is None
    assert result["runtime_used"] == "pi"
    assert result["fallback_reason"] == "provider_timeout"
    assert result["agent_metrics"] == {"fallback_suppressed": True}
    assert result["origin"] == "pi"
    assert result["status"] == "timed_out"
    assert result["status_reason"] == "provider_timeout"
    assert result["decision_reason"]
    assert direct_provider.calls == []


def test_pi_response_validation_failure_keeps_bounded_diagnostic() -> None:
    async def scenario() -> None:
        request = _coach_request()
        invalid_intervention = {
            "event_type": "question_to_user",
            "title": "Answer now",
            "recommendation": "Short",
            "reason": "The remote party asked a question.",
            "evidence_segment_ids": ["remote-4"],
            "evidence_quote": "你能承诺周五一定上线吗？",
            "urgency": "high",
            "confidence": 0.92,
        }
        result = await run_realtime_coach_routed(
            request=request,
            provider=_Provider(json.dumps({"intervention": None})),
            requested_runtime="pi",
            pi_runtime=_PiRuntime(
                {
                    "action": "intervention",
                    "intervention": invalid_intervention,
                    "metrics": {"turns": 1},
                }
            ),
            pi_provider_config=_pi_provider_config(),
        )

        assert result["intervention"] is None
        assert result["fallback_error_code"] == "IntelligenceResponseValidationError"
        assert result["fallback_reason"] == "IntelligenceResponseValidationError"
        assert result["agent_metrics"]["response_validation_category"] == "structural"
        assert result["agent_metrics"]["response_validation_error"] == (
            "intervention.recommendation is too short"
        )
        assert result["agent_metrics"]["fallback_suppressed"] is True

    asyncio.run(scenario())


def test_pi_router_blocks_direct_fallback_after_soft_cutoff() -> None:
    class ExpectedPiError(RuntimeError):
        code = "pi_transport_error"

    async def scenario() -> None:
        direct_provider = _Provider(json.dumps({"intervention": None}))
        result = await run_realtime_coach_routed(
            request=_coach_request(),
            provider=direct_provider,
            requested_runtime="pi",
            pi_runtime=_PiRuntime(error=ExpectedPiError("bridge transport closed")),
            pi_provider_config=_pi_provider_config(),
            # Deliberately closed before the call: a runtime/transport failure
            # must become a timeout audit, never a second model request.
            soft_deadline_at_ms=1,
            max_provider_timeout_ms=250,
        )

        assert result["origin"] == "pi"
        assert result["runtime_requested"] == "pi"
        assert result["status"] == "timed_out"
        assert result["status_reason"] == "soft_deadline_exceeded"
        assert result["fallback_reason"] == "soft_deadline_exceeded"
        assert result["delivery_status"] == "too_late"
        assert result["soft_cutoff_triggered"] is True
        assert result["late_result_discarded"] is True
        assert direct_provider.calls == []

    asyncio.run(scenario())


def test_direct_coach_silence_has_explicit_provenance_and_reason() -> None:
    asyncio.run(_test_direct_coach_silence_has_explicit_provenance_and_reason())


async def _test_direct_coach_silence_has_explicit_provenance_and_reason() -> None:
    result = await run_realtime_coach_routed(
        request=_coach_request(),
        provider=_Provider(json.dumps({"intervention": None})),
        requested_runtime="direct",
    )

    assert result["origin"] == "direct_intelligence"
    assert result["runtime_requested"] == "direct"
    assert result["runtime_used"] == "direct"
    assert result["status"] == "protected_silent"
    assert result["status_reason"] == "no_actionable_intervention"
    assert result["decision_reason"]
    assert result["decision_id"].startswith("coach-decision:")


def test_coach_intervention_to_dict_uses_json_safe_evidence_lists() -> None:
    intervention = CoachIntervention(
        event_type="question_to_user",
        title="回答问题",
        recommendation="先确认压测结果，再承诺上线日期。",
        reason="对方要求明确承诺。",
        evidence_segment_ids=("remote-4", "local-3"),
        evidence_quote="你能承诺周五一定上线吗？",
        urgency="high",
        confidence=0.92,
    )

    payload = intervention.to_dict()

    assert payload["evidence_segment_ids"] == ["remote-4", "local-3"]
    assert payload["recommendation"] == intervention.recommendation
    assert payload["say_this"] == intervention.recommendation
    assert payload["why_now"] == intervention.reason
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_public_provenance_helper_supports_not_triggered_without_wall_clock_fields() -> None:
    result = build_realtime_coach_provenance_decision(
        request=_coach_request(),
        origin="direct_intelligence",
        status="not_triggered",
        status_reason="trigger_gate",
        decision_reason="本轮没有达到教练触发条件。",
    )

    assert result["origin"] == "direct_intelligence"
    assert result["status"] == "not_triggered"
    assert result["status_reason"] == "trigger_gate"
    assert result["decision_reason"] == "本轮没有达到教练触发条件。"
    assert result["intervention"] is None
    assert result["run_id"].endswith(":coach")
    assert result["decision_id"].startswith("coach-decision:")
    assert result["evidence_revision"].startswith("coach-evidence:")
    assert not any(key.endswith("_at") or key.endswith("_ms") for key in result)


def test_public_provenance_helper_rejects_intervention_status_without_card() -> None:
    with pytest.raises(ValueError, match="requires a CoachIntervention"):
        build_realtime_coach_provenance_decision(
            request=_coach_request(),
            origin="pi",
            status="intervention",
        )


def test_pi_protected_silence_has_its_own_observable_decision() -> None:
    asyncio.run(_test_pi_protected_silence_has_its_own_observable_decision())


async def _test_pi_protected_silence_has_its_own_observable_decision() -> None:
    request = _coach_request()
    pi_runtime = _PiRuntime(
        {"action": "silent", "intervention": None, "metrics": {}, "decision_reason": None}
    )
    candidate_events = [
        {
            "event_type": "question_pending",
            "candidate_key": "coach-candidate:question_pending:eligible",
            "evidence_segment_ids": ["remote-4"],
            "reason": "The latest evidence contains a pending question.",
        }
    ]
    result = await run_realtime_coach_routed(
        request=request,
        provider=_Provider(json.dumps({"intervention": None})),
        requested_runtime="pi",
        pi_runtime=pi_runtime,
        pi_provider_config=_pi_provider_config(),
        candidate_events=candidate_events,
    )

    assert result["origin"] == "pi"
    assert result["status"] == "protected_silent"
    assert result["status_reason"] == "no_actionable_intervention"
    assert result["intervention"] is None
    assert result["decision_id"].startswith("coach-decision:")
    assert result["evidence_revision"].startswith("coach-evidence:4:")
    assert pi_runtime.payload["context"]["candidate_events"] == candidate_events


def test_coach_provenance_ids_are_stable_for_a_retry_and_change_with_evidence() -> None:
    asyncio.run(_test_coach_provenance_ids_are_stable_for_a_retry_and_change_with_evidence())


async def _test_coach_provenance_ids_are_stable_for_a_retry_and_change_with_evidence() -> None:
    request = _coach_request()
    first = await run_realtime_coach(
        request=request,
        provider=_Provider(json.dumps({"intervention": None})),
    )
    retry = await run_realtime_coach(
        request=request,
        provider=_Provider(json.dumps({"intervention": None})),
    )
    revised_request = replace(
        request,
        state_revision=request.state_revision + 1,
        new_paragraphs=(replace(request.new_paragraphs[0], revision=2),),
    )
    revised = await run_realtime_coach(
        request=revised_request,
        provider=_Provider(json.dumps({"intervention": None})),
    )

    assert (first["run_id"], first["decision_id"], first["evidence_revision"]) == (
        retry["run_id"],
        retry["decision_id"],
        retry["evidence_revision"],
    )
    assert revised["run_id"] != first["run_id"]
    assert revised["decision_id"] != first["decision_id"]
    assert revised["evidence_revision"] != first["evidence_revision"]


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


def test_runner_repairs_unknown_evidence_once_before_failing_closed() -> None:
    asyncio.run(_test_runner_repairs_unknown_evidence_once_before_failing_closed())


async def _test_runner_repairs_unknown_evidence_once_before_failing_closed() -> None:
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
    valid = json.dumps(
        {
            "paragraph_revisions": [],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )
    provider = _Provider([invalid, valid])

    result = await run_realtime_intelligence(request=_request(), provider=provider)

    assert result["response"].state_changes == ()
    assert result["repair_attempted"] is True
    assert len(provider.calls) == 2


def test_parser_rejects_transcript_revisions_when_independent_lane_is_disabled() -> None:
    request = RealtimeIntelligenceRequest.from_payload(
        meeting_id="meeting-independent-correction",
        state_revision=1,
        new_paragraphs=[_paragraph("paragraph-1", "原始转写")],
        context_paragraphs=[],
        rolling_state={},
        allow_paragraph_revisions=False,
    )
    content = json.dumps(
        {
            "paragraph_revisions": [
                {
                    "target_id": "paragraph-1",
                    "expected_revision": 1,
                    "corrected_text": "修正后的转写",
                    "change_count": 1,
                }
            ],
            "topic_update": None,
            "state_changes": [],
            "follow_up": None,
        },
        ensure_ascii=False,
    )

    with pytest.raises(IntelligenceResponseValidationError, match="handled independently"):
        parse_realtime_intelligence_response(content, request=request)


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

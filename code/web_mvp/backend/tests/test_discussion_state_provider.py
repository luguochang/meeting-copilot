"""Discussion-state nonstream contract against the production adapter.
All requests terminate at httpx.MockTransport; no real Provider/config/UI calls.
Fixtures are synthetic and do not claim historical native wire identity.
"""

from __future__ import annotations
import asyncio
import json

import httpx
import pytest

from meeting_copilot_web_mvp.realtime_intelligence import (
    IntelligenceResponseValidationError,
    RealtimeIntelligenceRequest,
    dynamic_output_token_limit,
    realtime_intelligence_idempotency_key,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (
    ProviderErrorCategory,
    StreamingProviderError,
    provider_idempotency_header_value,
)

from meeting_copilot_web_mvp.discussion_state_provider import NonStreamingStateProvider
from meeting_copilot_web_mvp.realtime_intelligence import run_realtime_intelligence

SYSTEM = "Synthetic state-only system for protocol tests; real handler system is frozen separately."
MODEL = "gpt-6-sol"
TEXT = "\u5f53\u524d\u65b9\u6848\u662f\u5148\u51bb\u7ed3\u660e\u786e\u539f\u6587\uff0c\u7136\u540e\u6838\u9a8c\u5f15\u7528\u3002"


def state_request():
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id="offline-synthetic-state-only",
        state_revision=3,
        trigger_type="discussion_update",
        work_item_id="offline-synthetic-state-task",
        new_paragraphs=[{"id": "p-new", "text": TEXT, "revision": 1}],
        context_paragraphs=[
            {"id": "p-context", "text": "\u8fd9\u662f\u4e00\u6bb5\u6d4b\u8bd5\u4e0a\u4e0b\u6587\u3002", "revision": 1}
        ],
        retrieval_paragraphs=[
            {
                "id": "p-retrieval",
                "text": "\u8fd9\u662f\u4e00\u6bb5\u53ea\u8bfb\u6d4b\u8bd5\u539f\u6587\u3002",
                "revision": 1,
            }
        ],
        rolling_state={
            "topic": None,
            "discussion": {"inferred": True},
            "open_items": [],
            "current_answer": None,
            "version": 3,
        },
        allow_paragraph_revisions=False,
    )


class StateOnlyFacade:
    """Synthetic state-only packet facade; production handler is covered separately."""

    def __init__(self, request, client, *, timeout_seconds=25.0):
        self.request = request
        self.calls = 0
        self.last_messages = None
        self.transport = NonStreamingStateProvider(
            base_url="https://offline.invalid",
            api_key="fake-public-test",
            model=MODEL,
            client=client,
            api_style="responses",
            timeout_seconds=timeout_seconds,
        )

    async def complete(self, messages, **parameters):
        if self.calls:
            raise RuntimeError("discussion state permits exactly one provider call")
        self.calls += 1
        bounded_messages = [dict(message) for message in messages]
        bounded_messages[0]["content"] = SYSTEM
        packet = json.loads(bounded_messages[1]["content"])
        packet["retrieval_paragraphs"] = [paragraph.to_prompt_dict() for paragraph in self.request.retrieval_paragraphs]
        packet["state_task"] = {
            "scope": "current_meeting_discussion",
            "semantic_from_seq": 0,
            "pending_segment_ids": [
                paragraph.id
                for paragraph in (
                    *self.request.new_paragraphs,
                    *self.request.context_paragraphs,
                    *self.request.retrieval_paragraphs,
                )
            ],
        }
        bounded_messages[1]["content"] = json.dumps(packet, ensure_ascii=False)
        self.last_messages = bounded_messages
        parameters["reasoning_effort"] = "low"
        return await self.transport.complete(bounded_messages, **parameters)


def valid_content():
    return {"paragraph_revisions": [], "topic_update": None, "state_changes": [], "follow_up": None}


def responses_payload(content):
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {
        "id": "offline-response",
        "model": MODEL,
        "status": "completed",
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
        "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
    }


async def run_case(content=None, *, handler=None, timeout_seconds=25.0):
    posts = []
    usage = []

    async def record(request):
        posts.append(request)
        if handler is not None:
            return await handler(request)
        return httpx.Response(200, json=responses_payload(valid_content() if content is None else content))

    async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
        request = state_request()
        provider = StateOnlyFacade(request, client, timeout_seconds=timeout_seconds)
        result = await run_realtime_intelligence(
            request=request,
            provider=provider,
            allow_repair=False,
            on_usage=lambda value, attempt: usage.append((value, attempt)),
        )
        return result, posts, usage, provider, request


def test_initial_request_is_nonstream_and_preserves_full_frozen_state_shape():
    result, posts, _usage, provider, request = asyncio.run(run_case())
    assert len(posts) == provider.calls == result["provider_attempt_count"] == 1
    body = json.loads(posts[0].content)
    assert body["model"] == MODEL
    assert body["reasoning"] == {"effort": "low"}
    assert body["max_output_tokens"] == dynamic_output_token_limit(request.input_characters)
    assert body["instructions"] == SYSTEM
    packet = json.loads(body["input"][0]["content"])
    assert packet["retrieval_paragraphs"] == [item.to_prompt_dict() for item in request.retrieval_paragraphs]
    assert packet["state_task"]["pending_segment_ids"] == ["p-new", "p-context", "p-retrieval"]
    assert "output_contract" in packet
    assert posts[0].headers["Idempotency-Key"] == provider_idempotency_header_value(
        realtime_intelligence_idempotency_key(request)
    )
    assert posts[0].extensions["timeout"]["read"] == 25.0
    assert body["store"] is False
    assert body["stream"] is False


def test_explicit_nonstream_result_has_no_failure_fallback_label():
    result, posts, usage, _provider, _request = asyncio.run(run_case())
    assert len(posts) == 1
    assert result["repair_attempted"] is False
    assert result["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
    assert usage == [(result["usage"], 1)]
    assert result["transport_mode"] == "non_streaming"
    assert result["fallback_reason"] is None


def test_nonstream_post_adapter_does_not_invent_connection_time():
    result, _posts, _usage, _provider, _request = asyncio.run(run_case())
    assert result["timings"]["connected_at"] is None
    assert result["timings"]["first_token_at"] is None
    assert result["ttft_ms"] is None
    assert result["timings"]["completed_at"] >= result["timings"]["started_at"]


def test_valid_empty_state_is_allowed_correct_silence():
    result, posts, _usage, _provider, _request = asyncio.run(run_case())
    assert len(posts) == 1
    assert result["response"].state_changes == ()
    assert result["response"].topic_update is None
    assert result["response"].follow_up is None


@pytest.mark.parametrize("case", ["malformed", "missing_fields", "unknown_id", "false_quote"])
def test_full_response_and_evidence_validation_rejects_without_repair(case):
    content = valid_content()
    if case == "malformed":
        content = "not-json"
    elif case == "missing_fields":
        content = {"state_changes": []}
    else:
        content["state_changes"] = [
            {
                "type": "decision",
                "operation": "add",
                "item_id": "test-decision",
                "content": TEXT,
                "status": "candidate",
                "confidence": 0.9,
                "evidence_segment_ids": ["p-new" if case == "false_quote" else "p-unseen"],
                "evidence_quote": "\u539f\u6587\u6ca1\u6709\u8fd9\u4e2a\u4e8b\u5b9e" if case == "false_quote" else TEXT,
            }
        ]

    async def run():
        posts = []
        usage = []

        async def record(request):
            posts.append(request)
            return httpx.Response(200, json=responses_payload(content))

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(IntelligenceResponseValidationError):
                await run_realtime_intelligence(
                    request=provider.request,
                    provider=provider,
                    allow_repair=False,
                    on_usage=lambda value, attempt: usage.append((value, attempt)),
                )
            assert len(posts) == provider.calls == 1
            assert len(usage) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    ("status", "category"),
    [
        (401, ProviderErrorCategory.AUTHENTICATION),
        (429, ProviderErrorCategory.RATE_LIMIT),
        (500, ProviderErrorCategory.PROVIDER_SERVER),
    ],
)
def test_redacted_http_error_is_one_post_and_not_retried(status, category):
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            return httpx.Response(status, json={"error": {"message": "private synthetic sk-do-not-expose"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert len(posts) == provider.calls == 1
            assert caught.value.category is category
            assert caught.value.status_code == status
            assert "sk-do-not-expose" not in str(caught.value)

    asyncio.run(run())


@pytest.mark.parametrize(
    "timeout_type,origin",
    [
        (httpx.ReadTimeout, "read"),
        (httpx.ConnectTimeout, "connect"),
        (httpx.WriteTimeout, "write"),
        (httpx.PoolTimeout, "pool"),
        (httpx.TimeoutException, "unknown"),
    ],
)
def test_timeout_retains_safe_origin_without_raw_exception_text(timeout_type, origin):
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            raise timeout_type("private synthetic sk-do-not-expose", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert len(posts) == provider.calls == 1
            assert caught.value.category is ProviderErrorCategory.TIMEOUT
            assert caught.value.retryable is True
            assert "sk-do-not-expose" not in str(caught.value)
            assert getattr(caught.value, "timeout_origin", "unknown") == origin

    asyncio.run(run())


def test_cancellation_unwinds_single_request_and_keeps_injected_owner_client_open():
    async def run():
        entered = asyncio.Event()
        unwound = asyncio.Event()
        posts = []

        async def record(request):
            posts.append(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                unwound.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            task = asyncio.create_task(
                run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert unwound.is_set()
            assert len(posts) == provider.calls == 1
            assert not client.is_closed

    asyncio.run(run())


def test_total_deadline_is_bounded_without_timeout_fallback_or_retry():
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            await asyncio.Event().wait()

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client, timeout_seconds=0.005)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert caught.value.category is ProviderErrorCategory.TIMEOUT
            assert len(posts) == provider.calls == 1

    asyncio.run(run())


def test_incomplete_response_is_rejected_even_when_json_text_is_parseable():
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            payload = responses_payload(valid_content())
            payload["status"] = "incomplete"
            payload["incomplete_details"] = {"reason": "max_output_tokens"}
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert caught.value.category is ProviderErrorCategory.PROTOCOL
            assert caught.value.retryable is False
            assert len(posts) == 1

    asyncio.run(run())


def test_state_paragraph_revision_cannot_change_transcript():
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            content = valid_content()
            content["paragraph_revisions"] = [
                {
                    "target_id": "p-new",
                    "expected_revision": 1,
                    "corrected_text": "different fact",
                    "change_count": 1,
                }
            ]
            return httpx.Response(200, json=responses_payload(content))

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(IntelligenceResponseValidationError):
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert len(posts) == 1

    asyncio.run(run())


@pytest.mark.parametrize("status", ["failed", "cancelled", "in_progress"])
def test_noncompleted_responses_payload_is_not_accepted_as_valid_state(status):
    async def run():
        posts = []

        async def record(request):
            posts.append(request)
            payload = responses_payload(valid_content())
            payload["status"] = status
            payload["error"] = {"message": "private synthetic sk-do-not-expose"}
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(record), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert caught.value.category is ProviderErrorCategory.PROTOCOL
            assert "sk-do-not-expose" not in str(caught.value)
            assert len(posts) == 1

    asyncio.run(run())


@pytest.mark.parametrize("native_usage", ["missing", None])
def test_native_responses_unknown_usage_remains_none(native_usage):
    async def run():
        usage_observed = []

        async def gateway(_request):
            payload = responses_payload(valid_content())
            if native_usage == "missing":
                payload.pop("usage")
            else:
                payload["usage"] = native_usage
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            result = await run_realtime_intelligence(
                request=provider.request,
                provider=provider,
                allow_repair=False,
                on_usage=lambda usage, attempt: usage_observed.append((usage, attempt)),
            )
            assert result["usage"] is None
            assert usage_observed == [(None, 1)]

    asyncio.run(run())


@pytest.mark.parametrize(
    "native_usage",
    [
        {"input_tokens": 3, "output_tokens": 2},
        {"input_tokens": True, "output_tokens": 2, "total_tokens": 3},
        {"input_tokens": -1, "output_tokens": 2, "total_tokens": 2},
        {"input_tokens": 3, "output_tokens": 2, "total_tokens": 0},
    ],
)
def test_invalid_or_partial_native_usage_is_not_coerced_to_known_tokens(native_usage):
    async def run():
        posts = []

        async def gateway(request):
            posts.append(request)
            payload = responses_payload(valid_content())
            payload["usage"] = native_usage
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            with pytest.raises(StreamingProviderError) as caught:
                await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert caught.value.category is ProviderErrorCategory.PROTOCOL
            assert len(posts) == 1

    asyncio.run(run())


def test_explicit_native_zero_usage_is_preserved_without_guessing():
    async def run():
        async def gateway(_request):
            payload = responses_payload(valid_content())
            payload["usage"] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(gateway), trust_env=False) as client:
            provider = StateOnlyFacade(state_request(), client)
            result = await run_realtime_intelligence(request=provider.request, provider=provider, allow_repair=False)
            assert result["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    asyncio.run(run())

from __future__ import annotations

import asyncio
import functools
import json

import httpx
import pytest

from meeting_copilot_web_mvp.streaming_llm_provider import (
    OpenAICompatibleStreamingProvider,
    ProviderErrorCategory,
    StreamingProviderError,
    TransportMode,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        current = self.value
        self.value += 0.25
        return current


def _async_test(function):
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


def _provider(
    handler,
    *,
    allow_non_streaming_fallback: bool = True,
    clock=None,
) -> OpenAICompatibleStreamingProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    return OpenAICompatibleStreamingProvider(
        base_url="https://gateway.example/openai",
        api_key="sk-test-secret",
        model="test-model",
        client=client,
        allow_non_streaming_fallback=allow_non_streaming_fallback,
        clock=clock,
    )


@_async_test
async def test_streams_real_sse_deltas_usage_and_timings() -> None:
    request_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_bodies.append(json.loads(request.content))
        assert request.url == "https://gateway.example/openai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test-secret"
        body = "".join(
            [
                'data: {"id":"chat-1","model":"served-model","choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n\n',
                ": keep-alive\n\n",
                'data: {"id":"chat-1","choices":[{"index":0,"delta":{"content":"你"}}]}\n\n',
                'data: {"id":"chat-1","choices":[{"index":0,"delta":{}}]}\n\n',
                'data: {"id":"chat-1","choices":[{"index":0,"delta":{"content":"好"},"finish_reason":"stop"}]}\n\n',
                'data: {"id":"chat-1","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2,"total_tokens":14}}\n\n',
                "data: [DONE]\n\n",
            ]
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    clock = _Clock()
    provider = _provider(handler, clock=clock)
    deltas = []
    result = await provider.complete(
        [{"role": "user", "content": "请给建议"}],
        on_delta=deltas.append,
        temperature=0.2,
        max_completion_tokens=64,
    )

    assert [delta.text for delta in deltas] == ["你", "好"]
    assert [delta.sequence for delta in deltas] == [1, 2]
    assert all(delta.transport_mode is TransportMode.STREAMING for delta in deltas)
    assert result.content == "你好"
    assert result.transport_mode is TransportMode.STREAMING
    assert result.response_id == "chat-1"
    assert result.model == "served-model"
    assert result.finish_reason == "stop"
    assert result.fallback_reason is None
    assert result.usage is not None
    assert result.usage.prompt_tokens == 12
    assert result.usage.completion_tokens == 2
    assert result.usage.total_tokens == 14
    assert result.timings.started_at == 100.0
    assert result.timings.connected_at == 100.25
    assert result.timings.first_token_at == 100.5
    assert result.timings.completed_at == 100.75
    assert result.timings.time_to_connect_seconds == pytest.approx(0.25)
    assert result.timings.time_to_first_token_seconds == pytest.approx(0.5)

    assert request_bodies == [
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "请给建议"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0.2,
            "max_completion_tokens": 64,
        }
    ]
    await provider.aclose()


@_async_test
async def test_supports_async_delta_callback() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream; charset=utf-8"},
            content=(
                'data: {"choices":[{"delta":{"content":"建议"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    seen: list[str] = []

    async def on_delta(delta) -> None:
        seen.append(delta.text)

    provider = _provider(handler)
    result = await provider.complete([{"role": "user", "content": "x"}], on_delta=on_delta)

    assert seen == ["建议"]
    assert result.content == "建议"
    await provider.aclose()


@_async_test
async def test_plain_json_response_is_explicit_non_streaming_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={
                "id": "chat-json",
                "model": "gateway-model",
                "choices": [{"message": {"role": "assistant", "content": "请确认回滚方案"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )

    provider = _provider(handler)
    deltas = []
    result = await provider.complete([{"role": "user", "content": "x"}], on_delta=deltas.append)

    assert result.content == "请确认回滚方案"
    assert result.transport_mode is TransportMode.NON_STREAMING_FALLBACK
    assert result.fallback_reason == "provider_returned_non_streaming_response"
    assert len(deltas) == 1
    assert deltas[0].transport_mode is TransportMode.NON_STREAMING_FALLBACK
    assert deltas[0].fallback_reason == result.fallback_reason
    assert result.timings.first_token_at is not None
    await provider.aclose()


@_async_test
async def test_explicit_stream_unsupported_error_retries_once_without_streaming() -> None:
    request_bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_bodies.append(body)
        if body["stream"] is True:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "stream is not supported by this model",
                        "type": "invalid_request_error",
                        "param": "stream",
                        "code": "unsupported_parameter",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chat-fallback",
                "choices": [{"message": {"content": "降级结果"}, "finish_reason": "stop"}],
            },
        )

    provider = _provider(handler)
    result = await provider.complete([{"role": "user", "content": "x"}])

    assert [body["stream"] for body in request_bodies] == [True, False]
    assert "stream_options" in request_bodies[0]
    assert "stream_options" not in request_bodies[1]
    assert result.content == "降级结果"
    assert result.transport_mode is TransportMode.NON_STREAMING_FALLBACK
    assert result.fallback_reason == "provider_rejected_streaming"
    await provider.aclose()


@_async_test
async def test_idempotency_header_is_redacted_and_reused_for_stream_fallback() -> None:
    request_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_headers.append(request.headers)
        body = json.loads(request.content)
        if body["stream"] is True:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "stream is not supported",
                        "param": "stream",
                        "code": "unsupported_parameter",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chat-fallback",
                "choices": [{"message": {"content": "降级结果"}, "finish_reason": "stop"}],
            },
        )

    raw_key = "suggestion:private-meeting-id:final-1"
    provider = _provider(handler)
    result = await provider.complete(
        [{"role": "user", "content": "x"}],
        idempotency_key=raw_key,
    )

    keys = [headers["idempotency-key"] for headers in request_headers]
    assert len(keys) == 2
    assert keys[0] == keys[1]
    assert keys[0].startswith("meeting-copilot-")
    assert raw_key not in keys[0]
    assert result.content == "降级结果"
    await provider.aclose()


@_async_test
async def test_stream_fallback_can_be_disabled_without_a_second_request() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"message": "stream unsupported", "param": "stream", "code": "unsupported_parameter"}},
        )

    provider = _provider(handler, allow_non_streaming_fallback=False)
    with pytest.raises(StreamingProviderError) as caught:
        await provider.complete([{"role": "user", "content": "x"}])

    assert calls == 1
    assert caught.value.category is ProviderErrorCategory.STREAM_UNSUPPORTED
    assert caught.value.status_code == 400
    assert caught.value.retryable is False
    await provider.aclose()


@_async_test
@pytest.mark.parametrize(
    ("status", "category", "retryable"),
    [
        (401, ProviderErrorCategory.AUTHENTICATION, False),
        (403, ProviderErrorCategory.AUTHENTICATION, False),
        (429, ProviderErrorCategory.RATE_LIMIT, True),
        (422, ProviderErrorCategory.PROVIDER_CLIENT, False),
        (503, ProviderErrorCategory.PROVIDER_SERVER, True),
    ],
)
async def test_classifies_http_errors(status, category, retryable) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "provider rejected request", "code": "test"}})

    provider = _provider(handler)
    with pytest.raises(StreamingProviderError) as caught:
        await provider.complete([{"role": "user", "content": "x"}])

    assert caught.value.category is category
    assert caught.value.status_code == status
    assert caught.value.retryable is retryable
    assert caught.value.provider_code == "test"
    assert "sk-test-secret" not in str(caught.value)
    await provider.aclose()


@_async_test
async def test_classifies_transport_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("gateway timed out", request=request)

    provider = _provider(handler)
    with pytest.raises(StreamingProviderError) as caught:
        await provider.complete([{"role": "user", "content": "x"}])

    assert caught.value.category is ProviderErrorCategory.TIMEOUT
    assert caught.value.retryable is True
    await provider.aclose()


@_async_test
@pytest.mark.parametrize(
    ("body", "category"),
    [
        ("data: not-json\n\ndata: [DONE]\n\n", ProviderErrorCategory.PROTOCOL),
        (
            'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"stop"}]}\n\n',
            ProviderErrorCategory.PROTOCOL,
        ),
        ("data: [DONE]\n\n", ProviderErrorCategory.EMPTY_RESPONSE),
        (
            'data: {"choices":[],"usage":{"prompt_tokens":"bad","completion_tokens":1,"total_tokens":1}}\n\n'
            "data: [DONE]\n\n",
            ProviderErrorCategory.PROTOCOL,
        ),
    ],
)
async def test_rejects_malformed_truncated_or_empty_streams(body, category) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    provider = _provider(handler)
    with pytest.raises(StreamingProviderError) as caught:
        await provider.complete([{"role": "user", "content": "x"}])

    assert caught.value.category is category
    await provider.aclose()


@_async_test
async def test_sse_error_payload_is_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                'data: {"error":{"message":"quota exceeded","type":"rate_limit_error","code":"rate_limit_exceeded"}}\n\n'
            ),
        )

    provider = _provider(handler)
    with pytest.raises(StreamingProviderError) as caught:
        await provider.complete([{"role": "user", "content": "x"}])

    assert caught.value.category is ProviderErrorCategory.RATE_LIMIT
    assert caught.value.provider_code == "rate_limit_exceeded"
    assert caught.value.retryable is True
    await provider.aclose()


@_async_test
async def test_injected_client_is_not_closed_by_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content='data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    provider = OpenAICompatibleStreamingProvider(
        base_url="https://gateway.example",
        api_key="sk-test",
        model="m1",
        client=client,
    )
    await provider.complete([{"role": "user", "content": "x"}])
    await provider.aclose()

    assert client.is_closed is False
    await client.aclose()

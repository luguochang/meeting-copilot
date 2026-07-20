"""OpenAI-compatible Chat Completions streaming transport.

The provider owns protocol concerns only: request streaming, SSE decoding,
explicit non-streaming fallback, timing, usage, and stable error categories.
Callers remain responsible for prompt construction and result validation.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from meeting_copilot_web_mvp.openai_protocol import (
    chat_body_to_responses,
    responses_finish_reason,
    responses_payload_to_chat,
    responses_usage_to_chat,
)


class TransportMode(str, Enum):
    STREAMING = "streaming"
    NON_STREAMING_FALLBACK = "non_streaming_fallback"


class ProviderErrorCategory(str, Enum):
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"
    PROVIDER_CLIENT = "provider_client"
    PROVIDER_SERVER = "provider_server"
    PROTOCOL = "protocol"
    STREAM_UNSUPPORTED = "stream_unsupported"
    EMPTY_RESPONSE = "empty_response"


class StreamingProviderError(RuntimeError):
    """A provider failure with a durable category suitable for job retries."""

    def __init__(
        self,
        category: ProviderErrorCategory,
        *,
        retryable: bool,
        status_code: int | None = None,
        provider_code: str | None = None,
        detail: str | None = None,
    ) -> None:
        message = detail or "LLM provider request failed"
        super().__init__(f"{category.value}: {message}")
        self.category = category
        self.retryable = retryable
        self.status_code = status_code
        self.provider_code = _safe_provider_code(provider_code)


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ProviderTimings:
    """Monotonic timestamps captured around one logical completion."""

    started_at: float
    connected_at: float
    first_token_at: float
    completed_at: float

    @property
    def time_to_connect_seconds(self) -> float:
        return self.connected_at - self.started_at

    @property
    def time_to_first_token_seconds(self) -> float:
        return self.first_token_at - self.started_at

    @property
    def generation_seconds(self) -> float:
        return self.completed_at - self.first_token_at

    @property
    def total_seconds(self) -> float:
        return self.completed_at - self.started_at


@dataclass(frozen=True)
class CompletionDelta:
    text: str
    sequence: int
    transport_mode: TransportMode
    fallback_reason: str | None = None


@dataclass(frozen=True)
class ChatCompletionResult:
    content: str
    transport_mode: TransportMode
    timings: ProviderTimings
    usage: TokenUsage | None
    response_id: str | None
    model: str | None
    finish_reason: str | None
    fallback_reason: str | None = None


DeltaCallback = Callable[[CompletionDelta], Awaitable[None] | None]
Clock = Callable[[], float]

_RESERVED_PARAMETERS = frozenset({"model", "messages", "stream", "stream_options"})
_SAFE_PROVIDER_CODES = frozenset(
    {
        "API_KEY_REQUIRED",
        "AUTHENTICATION_ERROR",
        "ENDPOINT_NOT_FOUND",
        "INSUFFICIENT_QUOTA",
        "INVALID_API_KEY",
        "INVALID_MODEL",
        "INVALID_REQUEST_ERROR",
        "MODEL_NOT_FOUND",
        "NO_AVAILABLE_ACCOUNTS",
        "PERMISSION_DENIED",
        "RATE_LIMIT_EXCEEDED",
        "UNSUPPORTED_ENDPOINT",
        "UNSUPPORTED_PARAMETER",
    }
)


def provider_idempotency_header_value(value: Any) -> str | None:
    """Return a stable provider key without exposing meeting or job identifiers."""

    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
    return f"meeting-copilot-{digest[:40]}"


class OpenAICompatibleStreamingProvider:
    """Reusable Chat Completions client with streaming-first semantics.

    Inject one lifespan-scoped ``httpx.AsyncClient`` in production. When no
    client is injected, this object creates and owns one and ``aclose`` must be
    called. An injected client is never closed by this provider.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        allow_non_streaming_fallback: bool = True,
        api_style: str = "chat_completions",
        clock: Clock | None = None,
    ) -> None:
        self._base_url = _validated_base_url(base_url)
        self._api_key = str(api_key or "")
        self._model = str(model or "").strip()
        if not self._api_key:
            raise ValueError("api_key is required")
        if not self._model:
            raise ValueError("model is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._timeout_seconds = float(timeout_seconds)
        self._allow_non_streaming_fallback = bool(allow_non_streaming_fallback)
        normalized_api_style = str(api_style or "").strip().lower()
        if normalized_api_style not in {"chat_completions", "responses"}:
            raise ValueError("api_style must be chat_completions or responses")
        self._api_style = normalized_api_style
        self._clock = clock or time.perf_counter
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=self._timeout_seconds,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owns_client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> "OpenAICompatibleStreamingProvider":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.aclose()

    async def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        on_delta: DeltaCallback | None = None,
        idempotency_key: str | None = None,
        **parameters: Any,
    ) -> ChatCompletionResult:
        """Run one streaming-first completion and deliver non-empty deltas.

        A gateway that returns a regular JSON completion, or explicitly says
        ``stream`` is unsupported, may use the non-streaming fallback. That
        mode is always visible on both deltas and the final result.
        """

        reserved = _RESERVED_PARAMETERS.intersection(parameters)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"reserved completion parameters cannot be overridden: {names}")
        if not messages:
            raise ValueError("messages must not be empty")

        request_body: dict[str, Any] = {
            "model": self._model,
            "messages": [dict(message) for message in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            **parameters,
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        request_identity = provider_idempotency_header_value(idempotency_key)
        if request_identity is not None:
            headers["Idempotency-Key"] = request_identity
        started_at = self._clock()

        if self._api_style == "responses":
            return await self._complete_via_responses(
                request_body,
                headers=headers,
                on_delta=on_delta,
                started_at=started_at,
            )

        try:
            async with self._client.stream(
                "POST",
                self._completion_url,
                headers=headers,
                json=request_body,
                timeout=self._timeout_seconds,
            ) as response:
                connected_at = self._clock()
                if response.status_code >= 400:
                    error_payload = await _response_json(response)
                    if _is_stream_unsupported(response.status_code, error_payload):
                        if not self._allow_non_streaming_fallback:
                            raise StreamingProviderError(
                                ProviderErrorCategory.STREAM_UNSUPPORTED,
                                retryable=False,
                                status_code=response.status_code,
                                provider_code=_provider_code(error_payload),
                                detail="provider does not support streaming",
                            )
                        return await self._retry_without_streaming(
                            request_body,
                            headers=headers,
                            on_delta=on_delta,
                            started_at=started_at,
                            connected_at=connected_at,
                        )
                    raise _http_error(response.status_code, error_payload)

                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" in content_type:
                    return await self._consume_sse(
                        response,
                        on_delta=on_delta,
                        started_at=started_at,
                        connected_at=connected_at,
                    )

                payload = await _response_json(response)
                if payload is None:
                    raise StreamingProviderError(
                        ProviderErrorCategory.PROTOCOL,
                        retryable=False,
                        status_code=response.status_code,
                        detail="provider returned neither SSE nor JSON",
                    )
                if not self._allow_non_streaming_fallback:
                    raise StreamingProviderError(
                        ProviderErrorCategory.STREAM_UNSUPPORTED,
                        retryable=False,
                        status_code=response.status_code,
                        provider_code=_provider_code(payload),
                        detail="provider returned a non-streaming response",
                    )
                return await self._consume_non_streaming(
                    payload,
                    on_delta=on_delta,
                    started_at=started_at,
                    connected_at=connected_at,
                    fallback_reason="provider_returned_non_streaming_response",
                )
        except StreamingProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise StreamingProviderError(
                ProviderErrorCategory.TIMEOUT,
                retryable=True,
                detail="provider request timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise StreamingProviderError(
                ProviderErrorCategory.TRANSPORT,
                retryable=True,
                detail="provider transport failed",
            ) from exc
    @property
    def _completion_url(self) -> str:
        return f"{self._base_url}/v1/chat/completions"

    @property
    def _responses_url(self) -> str:
        return f"{self._base_url}/v1/responses"

    async def _complete_via_responses(
        self,
        chat_body: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        on_delta: DeltaCallback | None,
        started_at: float,
    ) -> ChatCompletionResult:
        responses_body = chat_body_to_responses(chat_body, stream=True)
        try:
            async with self._client.stream(
                "POST",
                self._responses_url,
                headers=headers,
                json=responses_body,
                timeout=self._timeout_seconds,
            ) as response:
                connected_at = self._clock()
                if response.status_code >= 400:
                    raise _http_error(response.status_code, await _response_json(response))

                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" in content_type:
                    return await self._consume_responses_sse(
                        response,
                        on_delta=on_delta,
                        started_at=started_at,
                        connected_at=connected_at,
                    )

                payload = await _response_json(response)
                if not isinstance(payload, dict):
                    raise StreamingProviderError(
                        ProviderErrorCategory.PROTOCOL,
                        retryable=False,
                        status_code=response.status_code,
                        detail="Responses API returned invalid JSON",
                    )
                normalized = responses_payload_to_chat(payload)
                return await self._consume_non_streaming(
                    normalized,
                    on_delta=on_delta,
                    started_at=started_at,
                    connected_at=connected_at,
                    fallback_reason="responses_api_returned_non_streaming_response",
                )
        except StreamingProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise StreamingProviderError(
                ProviderErrorCategory.TIMEOUT,
                retryable=True,
                detail="provider request timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise StreamingProviderError(
                ProviderErrorCategory.TRANSPORT,
                retryable=True,
                detail="provider transport failed",
            ) from exc
        except ValueError as exc:
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=False,
                detail="Responses API returned an invalid completion",
            ) from exc

    async def _consume_responses_sse(
        self,
        response: httpx.Response,
        *,
        on_delta: DeltaCallback | None,
        started_at: float,
        connected_at: float,
    ) -> ChatCompletionResult:
        parts: list[str] = []
        sequence = 0
        first_token_at: float | None = None
        usage: TokenUsage | None = None
        response_id: str | None = None
        served_model: str | None = None
        finish_reason: str | None = None
        done_seen = False

        async for raw_data in _iter_sse_data(response):
            data = raw_data.strip()
            if data == "[DONE]":
                break
            try:
                payload = json.loads(data)
            except (TypeError, json.JSONDecodeError) as exc:
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted malformed Responses SSE JSON",
                ) from exc
            if not isinstance(payload, dict):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted a non-object Responses event",
                )

            event_type = str(payload.get("type") or "")
            if event_type == "error" or event_type == "response.failed":
                raise _stream_error(payload)
            if event_type == "response.incomplete":
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider Responses stream ended incomplete",
                )
            if event_type == "response.output_text.delta":
                text = str(payload.get("delta") or "")
                if not text:
                    continue
                if first_token_at is None:
                    first_token_at = self._clock()
                parts.append(text)
                sequence += 1
                await _emit_delta(
                    on_delta,
                    CompletionDelta(
                        text=text,
                        sequence=sequence,
                        transport_mode=TransportMode.STREAMING,
                    ),
                )
                continue
            if event_type != "response.completed":
                continue

            completed = payload.get("response")
            completed = completed if isinstance(completed, dict) else payload
            response_id = _optional_string(completed.get("id"))
            served_model = _optional_string(completed.get("model"))
            finish_reason = responses_finish_reason(completed)
            raw_usage = responses_usage_to_chat(completed.get("usage"))
            if any(raw_usage.values()):
                usage = _parse_usage(raw_usage)
            done_seen = True
            break

        if not done_seen:
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=not parts,
                detail="provider Responses stream ended before completion",
            )
        if first_token_at is None:
            raise StreamingProviderError(
                ProviderErrorCategory.EMPTY_RESPONSE,
                retryable=True,
                detail="provider Responses stream contained no assistant text",
            )
        completed_at = self._clock()
        return ChatCompletionResult(
            content="".join(parts),
            transport_mode=TransportMode.STREAMING,
            timings=ProviderTimings(
                started_at=started_at,
                connected_at=connected_at,
                first_token_at=first_token_at,
                completed_at=completed_at,
            ),
            usage=usage,
            response_id=response_id,
            model=served_model,
            finish_reason=finish_reason,
        )

    async def _consume_sse(
        self,
        response: httpx.Response,
        *,
        on_delta: DeltaCallback | None,
        started_at: float,
        connected_at: float,
    ) -> ChatCompletionResult:
        parts: list[str] = []
        sequence = 0
        first_token_at: float | None = None
        usage: TokenUsage | None = None
        response_id: str | None = None
        served_model: str | None = None
        finish_reason: str | None = None
        done_seen = False

        async for raw_data in _iter_sse_data(response):
            data = raw_data.strip()
            if data == "[DONE]":
                done_seen = True
                break
            try:
                payload = json.loads(data)
            except (TypeError, json.JSONDecodeError) as exc:
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted malformed SSE JSON",
                ) from exc
            if not isinstance(payload, dict):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted a non-object SSE payload",
                )
            if isinstance(payload.get("error"), dict):
                raise _stream_error(payload)

            response_id = _first_non_empty(response_id, payload.get("id"))
            served_model = _first_non_empty(served_model, payload.get("model"))
            if "usage" in payload and payload["usage"] is not None:
                usage = _parse_usage(payload["usage"])

            choices = payload.get("choices", [])
            if not isinstance(choices, list):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted invalid choices",
                )
            choice = _primary_choice(choices)
            if choice is None:
                continue
            if not isinstance(choice, dict):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted an invalid choice",
                )
            if choice.get("finish_reason") is not None:
                finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta", {})
            if not isinstance(delta, dict):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="provider emitted an invalid delta",
                )
            text = _content_text(delta.get("content"))
            if not text:
                continue

            if first_token_at is None:
                first_token_at = self._clock()
            parts.append(text)
            sequence += 1
            await _emit_delta(
                on_delta,
                CompletionDelta(
                    text=text,
                    sequence=sequence,
                    transport_mode=TransportMode.STREAMING,
                ),
            )

        if not done_seen:
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=not parts,
                detail="provider stream ended before [DONE]",
            )
        if first_token_at is None:
            raise StreamingProviderError(
                ProviderErrorCategory.EMPTY_RESPONSE,
                retryable=True,
                detail="provider stream contained no assistant text",
            )
        completed_at = self._clock()
        return ChatCompletionResult(
            content="".join(parts),
            transport_mode=TransportMode.STREAMING,
            timings=ProviderTimings(
                started_at=started_at,
                connected_at=connected_at,
                first_token_at=first_token_at,
                completed_at=completed_at,
            ),
            usage=usage,
            response_id=response_id,
            model=served_model,
            finish_reason=finish_reason,
        )

    async def _retry_without_streaming(
        self,
        request_body: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
        on_delta: DeltaCallback | None,
        started_at: float,
        connected_at: float,
    ) -> ChatCompletionResult:
        fallback_body = dict(request_body)
        fallback_body["stream"] = False
        fallback_body.pop("stream_options", None)
        response = await self._client.post(
            self._completion_url,
            headers=headers,
            json=fallback_body,
            timeout=self._timeout_seconds,
        )
        payload = await _response_json(response)
        if response.status_code >= 400:
            raise _http_error(response.status_code, payload)
        if payload is None:
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=False,
                status_code=response.status_code,
                detail="non-streaming fallback returned invalid JSON",
            )
        return await self._consume_non_streaming(
            payload,
            on_delta=on_delta,
            started_at=started_at,
            connected_at=connected_at,
            fallback_reason="provider_rejected_streaming",
        )

    async def _consume_non_streaming(
        self,
        payload: Any,
        *,
        on_delta: DeltaCallback | None,
        started_at: float,
        connected_at: float,
        fallback_reason: str,
    ) -> ChatCompletionResult:
        if not isinstance(payload, dict):
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=False,
                detail="non-streaming fallback returned a non-object payload",
            )
        if isinstance(payload.get("error"), dict):
            raise _stream_error(payload)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise StreamingProviderError(
                ProviderErrorCategory.EMPTY_RESPONSE,
                retryable=True,
                detail="non-streaming fallback contained no choices",
            )
        choice = _primary_choice(choices)
        if not isinstance(choice, dict):
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=False,
                detail="non-streaming fallback returned an invalid choice",
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL,
                retryable=False,
                detail="non-streaming fallback returned an invalid message",
            )
        content = _content_text(message.get("content"))
        if not content:
            raise StreamingProviderError(
                ProviderErrorCategory.EMPTY_RESPONSE,
                retryable=True,
                detail="non-streaming fallback contained no assistant text",
            )

        usage = _parse_usage(payload["usage"]) if payload.get("usage") is not None else None
        first_token_at = self._clock()
        await _emit_delta(
            on_delta,
            CompletionDelta(
                text=content,
                sequence=1,
                transport_mode=TransportMode.NON_STREAMING_FALLBACK,
                fallback_reason=fallback_reason,
            ),
        )
        completed_at = self._clock()
        return ChatCompletionResult(
            content=content,
            transport_mode=TransportMode.NON_STREAMING_FALLBACK,
            timings=ProviderTimings(
                started_at=started_at,
                connected_at=connected_at,
                first_token_at=first_token_at,
                completed_at=completed_at,
            ),
            usage=usage,
            response_id=_optional_string(payload.get("id")),
            model=_optional_string(payload.get("model")),
            finish_reason=_optional_string(choice.get("finish_reason")),
            fallback_reason=fallback_reason,
        )


async def _iter_sse_data(response: httpx.Response):
    data_lines: list[str] = []
    first_line = True
    async for raw_line in response.aiter_lines():
        line = raw_line.lstrip("\ufeff") if first_line else raw_line
        first_line = False
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field != "data":
            continue
        if separator and value.startswith(" "):
            value = value[1:]
        data_lines.append(value)
    if data_lines:
        yield "\n".join(data_lines)


async def _emit_delta(callback: DeltaCallback | None, delta: CompletionDelta) -> None:
    if callback is None:
        return
    result = callback(delta)
    if inspect.isawaitable(result):
        await result


def _primary_choice(choices: list[Any]) -> Any | None:
    if not choices:
        return None
    for choice in choices:
        if isinstance(choice, dict) and choice.get("index") == 0:
            return choice
    return choices[0]


def _content_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    raise StreamingProviderError(
        ProviderErrorCategory.PROTOCOL,
        retryable=False,
        detail="provider returned invalid assistant content",
    )


def _parse_usage(raw_usage: Any) -> TokenUsage:
    if not isinstance(raw_usage, dict):
        raise StreamingProviderError(
            ProviderErrorCategory.PROTOCOL,
            retryable=False,
            detail="provider returned invalid usage metadata",
        )
    names = ("prompt_tokens", "completion_tokens", "total_tokens")
    values = tuple(raw_usage.get(name) for name in names)
    if any(type(value) is not int or value < 0 for value in values):
        raise StreamingProviderError(
            ProviderErrorCategory.PROTOCOL,
            retryable=False,
            detail="provider returned invalid token counts",
        )
    prompt_tokens, completion_tokens, total_tokens = values
    if total_tokens != prompt_tokens + completion_tokens:
        raise StreamingProviderError(
            ProviderErrorCategory.PROTOCOL,
            retryable=False,
            detail="provider returned inconsistent token counts",
        )
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


async def _response_json(response: httpx.Response) -> Any | None:
    try:
        body = await response.aread()
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _http_error(status_code: int, payload: Any) -> StreamingProviderError:
    provider_code = _provider_code(payload)
    if status_code in {401, 403}:
        category = ProviderErrorCategory.AUTHENTICATION
        retryable = False
    elif status_code == 429:
        category = ProviderErrorCategory.RATE_LIMIT
        retryable = True
    elif status_code in {408, 504}:
        category = ProviderErrorCategory.TIMEOUT
        retryable = True
    elif status_code >= 500:
        category = ProviderErrorCategory.PROVIDER_SERVER
        retryable = True
    else:
        category = ProviderErrorCategory.PROVIDER_CLIENT
        retryable = False
    return StreamingProviderError(
        category,
        retryable=retryable,
        status_code=status_code,
        provider_code=provider_code,
    )


def _stream_error(payload: Mapping[str, Any]) -> StreamingProviderError:
    error = payload.get("error")
    if not isinstance(error, dict):
        response = payload.get("response")
        error = response.get("error") if isinstance(response, dict) else error
    error = error if isinstance(error, dict) else {}
    provider_code = _optional_string(error.get("code"))
    classifier = " ".join(
        str(error.get(name) or "").lower() for name in ("type", "code", "message")
    )
    if "rate" in classifier or "quota" in classifier:
        category = ProviderErrorCategory.RATE_LIMIT
        retryable = True
    elif "auth" in classifier or "api_key" in classifier or "permission" in classifier:
        category = ProviderErrorCategory.AUTHENTICATION
        retryable = False
    else:
        category = ProviderErrorCategory.PROVIDER_SERVER
        retryable = True
    return StreamingProviderError(
        category,
        retryable=retryable,
        provider_code=provider_code,
    )


def _is_stream_unsupported(status_code: int, payload: Any) -> bool:
    if status_code not in {400, 404, 415, 422} or not isinstance(payload, dict):
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    param = str(error.get("param") or "").lower()
    code = str(error.get("code") or "").lower()
    message = str(error.get("message") or "").lower()
    mentions_stream = param == "stream" or "stream" in code or "stream" in message
    unsupported = (
        "unsupported" in code
        or "not_supported" in code
        or "not supported" in message
        or "does not support" in message
        or "unsupported" in message
    )
    return mentions_stream and unsupported


def _provider_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        return _optional_string(error.get("code") or error.get("type"))
    return _optional_string(payload.get("code") or payload.get("type"))


def _safe_provider_code(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in _SAFE_PROVIDER_CODES else None


def _first_non_empty(current: str | None, candidate: Any) -> str | None:
    return current or _optional_string(candidate)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _validated_base_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query parameters or a fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))

"""One-shot non-streaming transport for validated discussion-state JSON."""

from __future__ import annotations
import asyncio
import time
from collections.abc import Mapping

from meeting_copilot_web_mvp.llm_service import (
    AsyncHttpxLlmClient,
    LlmProviderHttpError,
    LlmProviderTransportError,
    ProviderAbortHandle,
    _provider_post_async,
    _require_success_payload,
    _response_payload,
)
from meeting_copilot_web_mvp.openai_protocol import (
    chat_body_to_responses,
    responses_payload_to_chat,
    responses_url_for_chat_url,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (
    ChatCompletionResult,
    NonStreamingProviderTimings,
    ProviderErrorCategory,
    StreamingProviderError,
    TransportMode,
    _content_text,
    _optional_string,
    _parse_usage,
    _primary_choice,
    _validated_base_url,
    provider_idempotency_header_value,
)


class _StateJsonClient(AsyncHttpxLlmClient):
    """Retain native Responses completion status before shared normalization."""

    async def post_json(self, url, headers, body, timeout, *, abort=None):
        if self.api_style != "responses":
            return await super().post_json(url, headers, body, timeout, abort=abort)
        handle = abort or ProviderAbortHandle()
        handle.bind_current_task()
        try:
            response = await _provider_post_async(
                self._client,
                responses_url_for_chat_url(url),
                headers,
                chat_body_to_responses(body, stream=False),
                timeout,
            )
            payload = _require_success_payload(
                response,
                _response_payload(response),
                api_style=self.api_style,
            )
            if payload.get("status") not in {None, "completed"} or payload.get("error"):
                raise StreamingProviderError(
                    ProviderErrorCategory.PROTOCOL,
                    retryable=False,
                    detail="state Responses completion was not completed",
                )
            normalized = responses_payload_to_chat(payload)
            native_usage = payload.get("usage")
            # Missing values remain unknown; never invent zero counts or totals.
            normalized["usage"] = (
                {
                    "prompt_tokens": native_usage.get("input_tokens"),
                    "completion_tokens": native_usage.get("output_tokens"),
                    "total_tokens": native_usage.get("total_tokens"),
                }
                if isinstance(native_usage, Mapping)
                else None
            )
            return normalized
        except asyncio.CancelledError:
            handle.acknowledge_abort()
            raise
        finally:
            if handle.abort_requested and not handle.abort_acknowledged:
                handle.acknowledge_abort()


class NonStreamingStateProvider:
    """A one-shot JSON-completion facade with unknown first-byte/token timings."""

    def __init__(self, *, base_url, api_key, model, client=None, timeout_seconds=25.0, api_style="responses"):
        api_style = str(api_style or "").strip().lower()
        if api_style not in {"responses", "chat_completions"}:
            raise ValueError("api_style must be chat_completions or responses")
        if not api_key or not str(model or "").strip():
            raise ValueError("api_key and model are required")
        if timeout_seconds <= 0:
            raise ValueError("timeout must be positive")
        self.model = str(model).strip()
        self.api_style = api_style
        self.url = _validated_base_url(base_url) + "/v1/chat/completions"
        self.headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
        self.client = _StateJsonClient(client=client, api_style=api_style)
        self.timeout_seconds = min(25.0, float(timeout_seconds))
        self.calls = 0

    async def aclose(self):
        await self.client.aclose()

    async def complete(self, messages, *, on_delta=None, idempotency_key=None, **parameters):
        if self.calls:
            raise RuntimeError("discussion state permits exactly one provider call")
        if on_delta is not None:
            raise ValueError("discussion state does not deliver streaming deltas")
        if {"model", "messages", "stream", "stream_options"}.intersection(parameters):
            raise ValueError("reserved completion parameters cannot be overridden")
        self.calls += 1
        body = {"model": self.model, "messages": [dict(message) for message in messages], "stream": False, **parameters}
        headers = dict(self.headers)
        identity = provider_idempotency_header_value(idempotency_key)
        if identity is not None:
            headers["Idempotency-Key"] = identity
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self.timeout_seconds):
                payload = await self.client.post_json(self.url, headers, body, self.timeout_seconds)
                choices = payload.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise StreamingProviderError(
                        ProviderErrorCategory.EMPTY_RESPONSE,
                        retryable=True,
                        detail="state completion contained no choices",
                    )
                choice = _primary_choice(choices)
                if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
                    raise StreamingProviderError(
                        ProviderErrorCategory.PROTOCOL,
                        retryable=False,
                        detail="state completion contained invalid message",
                    )
                if self.api_style == "responses" and choice.get("finish_reason") == "length":
                    raise StreamingProviderError(
                        ProviderErrorCategory.PROTOCOL,
                        retryable=False,
                        detail="state Responses completion was incomplete",
                    )
                content = _content_text(choice["message"].get("content"))
                if not content:
                    raise StreamingProviderError(
                        ProviderErrorCategory.EMPTY_RESPONSE,
                        retryable=True,
                        detail="state completion contained no assistant text",
                    )
                usage = _parse_usage(payload["usage"]) if payload.get("usage") is not None else None
                completed = time.perf_counter()
                return ChatCompletionResult(
                    content=content,
                    transport_mode=TransportMode.NON_STREAMING,
                    timings=NonStreamingProviderTimings(started_at=started, completed_at=completed),
                    usage=usage,
                    response_id=_optional_string(payload.get("id")),
                    model=_optional_string(payload.get("model")) or self.model,
                    finish_reason=_optional_string(choice.get("finish_reason")),
                    fallback_reason=None,
                )
        except asyncio.CancelledError:
            raise
        except (LlmProviderHttpError, LlmProviderTransportError) as error:
            category = ProviderErrorCategory(error.category)
            safe = StreamingProviderError(
                category,
                retryable=error.retryable,
                status_code=error.status_code,
                provider_code=error.provider_code,
                detail="state provider request failed",
            )
            origin = getattr(error, "timeout_origin", "unknown")
            safe.timeout_origin = origin if origin in {"pool", "connect", "read", "write"} else "unknown"
            raise safe from None
        except TimeoutError:
            safe = StreamingProviderError(
                ProviderErrorCategory.TIMEOUT, retryable=True, detail="state completion exceeded total deadline"
            )
            safe.timeout_origin = "unknown"
            safe.timeout_scope = "total_deadline"
            raise safe from None
        except StreamingProviderError:
            raise
        except (KeyError, TypeError, ValueError):
            raise StreamingProviderError(
                ProviderErrorCategory.PROTOCOL, retryable=False, detail="state provider returned invalid completion"
            ) from None

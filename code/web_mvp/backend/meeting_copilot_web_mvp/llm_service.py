"""LLM execution service — real suggestion card generation via OpenAI-compatible gateway.

Reads config from env (LLM_GATEWAY_*). Used by the
/live/asr/sessions/{id}/llm-execution-runs endpoint when mode='enabled'.
Default mode remains 'disabled' (safe); 'enabled' actually calls the LLM,
records usage, and creates a real suggestion card (card_status='new').
"""
from __future__ import annotations

import json
import ipaddress
import os
import re
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

from meeting_copilot_web_mvp.logging_config import get_logger
from meeting_copilot_web_mvp.openai_protocol import (
    chat_body_to_responses,
    responses_payload_to_chat,
    responses_url_for_chat_url,
)
from meeting_copilot_web_mvp.streaming_llm_provider import provider_idempotency_header_value

_log = get_logger("meeting_copilot_web_mvp.llm_service")

PROMPT_VERSION = "web_mvp.suggestion_card.v2"
REPO_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
_PROVIDER_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_RUNTIME_CONFIG_LOCK = threading.RLock()
_RUNTIME_CONFIG_PAYLOAD: dict[str, Any] | None = None
_RUNTIME_CONFIG_GENERATION = 0
_AUDIT_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_SAFE_PROVIDER_ERROR_CODES = frozenset(
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


class LlmClient(Protocol):
    """Minimal HTTP client protocol so tests can inject a fake."""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]: ...


class LlmProviderHttpError(RuntimeError):
    """A redacted provider HTTP failure safe for logs and API responses."""

    def __init__(
        self,
        status_code: int,
        *,
        provider_code: str | None = None,
        api_style: str | None = None,
    ) -> None:
        self.status_code = int(status_code)
        self.provider_code = _safe_provider_error_code(provider_code)
        self.api_style = (
            _validated_api_style(api_style)
            if api_style is not None
            else None
        )
        self.category = _provider_error_category(self.status_code)
        self.retryable = self.status_code in {408, 409, 425, 429, 504} or self.status_code >= 500
        suffix = f":{self.provider_code}" if self.provider_code else ""
        super().__init__(f"llm_provider_http_{self.category}:{self.status_code}{suffix}")


class LlmProviderTransportError(RuntimeError):
    def __init__(self, category: str) -> None:
        self.category = category if category in {"timeout", "transport"} else "transport"
        self.retryable = True
        self.status_code = None
        self.provider_code = None
        super().__init__(f"llm_provider_{self.category}")


class HttpxLlmClient:
    """OpenAI-compatible client using an explicitly selected API style."""

    def __init__(self, *, api_style: str = "chat_completions") -> None:
        self.api_style = _validated_api_style(api_style)

    def post_json(self, url, headers, body, timeout):
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            if self.api_style == "responses":
                responses_url = responses_url_for_chat_url(url)
                responses_body = chat_body_to_responses(body, stream=False)
                response = _provider_post(client, responses_url, headers, responses_body)
                payload = _response_payload(response)
                normalized = _require_success_payload(
                    response,
                    payload,
                    api_style=self.api_style,
                )
                return responses_payload_to_chat(normalized)

            response = _provider_post(client, url, headers, body)
            payload = _response_payload(response)
            return _require_success_payload(
                response,
                payload,
                api_style=self.api_style,
            )


def provider_failure_message(error: Exception) -> str:
    """Return an actionable message without reflecting provider response text."""

    if isinstance(error, LlmProviderHttpError):
        code = f"，错误码 {error.provider_code}" if error.provider_code else ""
        if error.category == "authentication":
            if error.api_style == "responses":
                return (
                    f"AI 中转站认证失败（HTTP {error.status_code}{code}）。"
                    "请确认 API Key 与当前应用保存的一致；该中转站的 Responses 认证路由也可能不兼容，"
                    "可切换到 Chat Completions 后重试"
                )
            return (
                f"AI 中转站认证失败（HTTP {error.status_code}{code}）。"
                "请确认 API Key 与当前应用保存的一致，并检查 Chat Completions 和模型访问权限"
            )
        if error.category == "rate_limit":
            return f"AI 中转站限流或额度不足（HTTP {error.status_code}{code}），请稍后重试"
        if error.category == "provider_client":
            return f"AI 中转站不支持当前模型或请求协议（HTTP {error.status_code}{code}）"
        if error.category == "provider_server":
            return f"AI 中转站服务暂时不可用（HTTP {error.status_code}{code}）"
        if error.category == "timeout":
            return f"AI 中转站请求超时（HTTP {error.status_code}{code}）"
    if isinstance(error, LlmProviderTransportError):
        if error.category == "timeout":
            return "AI 中转站请求超时，请稍后重试"
        return "无法连接 AI 中转站，请检查网络和中转站地址"
    if isinstance(error, httpx.TimeoutException):
        return "AI 中转站请求超时，请稍后重试"
    if isinstance(error, httpx.RequestError):
        return "无法连接 AI 中转站，请检查网络和中转站地址"
    return f"LLM 探测失败: {type(error).__name__}"


def _provider_post(
    client: httpx.Client,
    url: str,
    headers: Mapping[str, str],
    body: Mapping[str, Any],
) -> Any:
    try:
        return client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        raise LlmProviderTransportError("timeout") from None
    except httpx.RequestError:
        raise LlmProviderTransportError("transport") from None


def _response_status(response: Any) -> int:
    value = getattr(response, "status_code", 200)
    return int(value) if type(value) is int else 200


def _response_payload(response: Any) -> Any:
    try:
        return response.json()
    except (TypeError, ValueError):
        return None


def _require_success_payload(
    response: Any,
    payload: Any,
    *,
    api_style: str | None = None,
) -> dict[str, Any]:
    status_code = _response_status(response)
    if status_code >= 400:
        raise LlmProviderHttpError(
            status_code,
            provider_code=_provider_error_code(payload),
            api_style=api_style,
        )
    if not isinstance(payload, dict):
        raise ValueError("LLM provider returned invalid JSON")
    return payload


def _provider_error_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if status_code in {408, 504}:
        return "timeout"
    if status_code >= 500:
        return "provider_server"
    return "provider_client"


def _provider_error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("error")
    if isinstance(nested, dict):
        return _safe_provider_error_code(nested.get("code") or nested.get("type"))
    return _safe_provider_error_code(payload.get("code") or payload.get("type"))


def _safe_provider_error_code(value: Any) -> str | None:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in _SAFE_PROVIDER_ERROR_CODES else None


@dataclass(repr=False)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    realtime_model: str | None = None
    timeout_seconds: float = 60.0
    provider_label: str = "openai_compatible_gateway"
    is_mock: bool = False
    max_retries: int = 2
    api_style: str = "chat_completions"

    def __post_init__(self) -> None:
        self.base_url = _validated_base_url(self.base_url)
        self.api_style = _validated_api_style(self.api_style)
        self.model = _validated_model_name(self.model)
        self.realtime_model = _validated_model_name(self.realtime_model or self.model)

    def __repr__(self) -> str:
        return (
            "LlmConfig("
            f"gateway_host={_gateway_host(self.base_url)!r}, "
            "api_key='<redacted>', "
            f"model={self.model!r}, "
            f"realtime_model={self.realtime_model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"provider_label={self.provider_label!r}, "
            f"is_mock={self.is_mock!r}, "
            f"api_style={self.api_style!r}"
            ")"
        )

    @classmethod
    def from_env(cls) -> "LlmConfig | None":
        """Return the desktop runtime override, process env config, or None."""
        with _RUNTIME_CONFIG_LOCK:
            runtime_payload = dict(_RUNTIME_CONFIG_PAYLOAD) if _RUNTIME_CONFIG_PAYLOAD else None
        if runtime_payload is not None:
            return cls(**runtime_payload)
        if _env_bool(os.environ.get("MEETING_COPILOT_DESKTOP_RUNTIME"), default=False):
            return None
        base = os.environ.get("LLM_GATEWAY_BASE_URL")
        key = os.environ.get("LLM_GATEWAY_API_KEY")
        if not base or not key:
            load_dotenv(REPO_ENV_FILE, override=False)
            base = os.environ.get("LLM_GATEWAY_BASE_URL")
            key = os.environ.get("LLM_GATEWAY_API_KEY")
        if not base or not key:
            return None
        model = os.environ.get("LLM_GATEWAY_MODEL", "gpt-5.5")
        return cls(
            base_url=base.rstrip("/"),
            api_key=key,
            model=model,
            realtime_model=os.environ.get("LLM_GATEWAY_REALTIME_MODEL") or model,
            timeout_seconds=float(os.environ.get("LLM_GATEWAY_TIMEOUT_SECONDS", "60")),
            provider_label=os.environ.get("LLM_GATEWAY_PROVIDER_LABEL", "openai_compatible_gateway"),
            is_mock=_env_bool(os.environ.get("LLM_GATEWAY_IS_MOCK"), default=False),
            api_style=os.environ.get("LLM_GATEWAY_API_STYLE", "chat_completions"),
        )


def configure_runtime(
    *,
    base_url: str,
    api_key: str,
    model: str,
    realtime_model: str | None = None,
    provider_label: str = "openai_compatible_gateway",
    api_style: str = "chat_completions",
) -> dict[str, Any]:
    """Install a process-local provider config without persisting its secret."""
    config = LlmConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        realtime_model=realtime_model,
        provider_label=provider_label,
        is_mock=False,
        api_style=api_style,
    )
    payload = {
        "base_url": config.base_url,
        "api_key": config.api_key,
        "model": config.model,
        "realtime_model": config.realtime_model,
        "timeout_seconds": config.timeout_seconds,
        "provider_label": config.provider_label,
        "is_mock": False,
        "max_retries": config.max_retries,
        "api_style": config.api_style,
    }
    with _RUNTIME_CONFIG_LOCK:
        global _RUNTIME_CONFIG_GENERATION, _RUNTIME_CONFIG_PAYLOAD
        _RUNTIME_CONFIG_PAYLOAD = payload
        _RUNTIME_CONFIG_GENERATION += 1
    return provider_metadata(config)


def clear_runtime_config() -> None:
    """Remove only the process-local override; environment config remains intact."""
    with _RUNTIME_CONFIG_LOCK:
        global _RUNTIME_CONFIG_GENERATION, _RUNTIME_CONFIG_PAYLOAD
        _RUNTIME_CONFIG_PAYLOAD = None
        _RUNTIME_CONFIG_GENERATION += 1


def runtime_configured() -> bool:
    with _RUNTIME_CONFIG_LOCK:
        return _RUNTIME_CONFIG_PAYLOAD is not None


def runtime_config_generation() -> int:
    with _RUNTIME_CONFIG_LOCK:
        return _RUNTIME_CONFIG_GENERATION


def _env_bool(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def provider_metadata(config: LlmConfig | None) -> dict[str, Any]:
    """Return non-secret LLM provider metadata for API responses and acceptance reports."""
    if config is None:
        return {
            "provider": "not_configured",
            "model": "not_called",
            "realtime_model": "not_called",
            "is_mock": False,
            "configured_from_env": False,
            "api_style": "not_configured",
        }
    return {
        "provider": provider_identifier(config),
        "model": config.model,
        "realtime_model": config.realtime_model,
        "is_mock": bool(config.is_mock),
        "configured_from_env": True,
        "api_style": config.api_style,
    }


def provider_identifier(config: LlmConfig) -> str:
    label = str(config.provider_label or "").strip()
    if _PROVIDER_LABEL_RE.fullmatch(label) and not label.lower().startswith(("http:", "https:")):
        return label
    return _gateway_host(config.base_url)


def realtime_config(config: LlmConfig) -> LlmConfig:
    """Return the same Provider credentials routed to the low-latency model."""

    return replace(
        config,
        model=str(config.realtime_model or config.model),
        realtime_model=str(config.realtime_model or config.model),
    )


def gateway_base_url_kind(base_url: str | None) -> str:
    """Classify a configured gateway without exposing its URL or credentials."""
    parsed = urlsplit(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return "not_configured"
    if parsed.hostname.lower() in {"127.0.0.1", "localhost", "::1"}:
        return "local"
    return "remote"


def provider_audit_metadata(config: LlmConfig, *, purpose: str) -> dict[str, str]:
    return {
        "provider": _safe_audit_value(provider_identifier(config), fallback="redacted_provider"),
        "model": _safe_audit_value(config.model, fallback="redacted_model"),
        "purpose": _safe_audit_value(purpose, fallback="llm_request"),
    }


def provider_error_payload(*, error_code: str, message: str) -> dict[str, str]:
    return {
        "error_code": _safe_audit_value(error_code, fallback="llm_provider_failed"),
        "message": str(message or "LLM provider request failed")[:160],
    }


def probe_gateway(
    config: LlmConfig,
    client: LlmClient | None = None,
) -> dict[str, Any]:
    """Make one minimal production-shaped request to verify gateway operability."""
    if config.is_mock:
        raise ValueError("mock LLM provider cannot pass production verification")
    client = client or _configured_httpx_client(config)
    data = client.post_json(
        f"{config.base_url}/v1/chat/completions",
        {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        {
            "model": config.model,
            "messages": [{"role": "user", "content": "只回复 OK"}],
            "temperature": 0,
            "reasoning_effort": "low",
            "max_completion_tokens": 16,
        },
        min(float(config.timeout_seconds), 15.0),
    )
    choices = data.get("choices") if isinstance(data, dict) else None
    content = (
        ((choices or [{}])[0].get("message") or {}).get("content")
        if isinstance(choices, list) and choices
        else None
    )
    if not isinstance(content, str) or not content.strip():
        raise ValueError("gateway returned no assistant content")
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict) or not {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }.issubset(usage):
        raise ValueError("gateway response missing usage metadata")
    raw_usage = (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )
    if any(type(value) is not int or value < 0 for value in raw_usage):
        raise ValueError("gateway response reported invalid token usage")
    prompt_tokens, completion_tokens, total_tokens = raw_usage
    if total_tokens <= 0 or total_tokens != prompt_tokens + completion_tokens:
        raise ValueError("gateway response reported inconsistent token usage")
    return {
        "operational": True,
        "provider": provider_identifier(config),
        "model": config.model,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


def _safe_audit_value(value: Any, *, fallback: str) -> str:
    normalized = str(value or "").strip()
    lowered = normalized.lower()
    if (
        not _AUDIT_VALUE_RE.fullmatch(normalized)
        or "://" in normalized
        or "api_key" in lowered
        or "authorization" in lowered
        or re.search(r"(?:^|[^a-z0-9])sk-[a-z0-9_-]+", lowered)
    ):
        return fallback
    return normalized


def _validated_base_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("LLM gateway base_url must be an absolute http(s) URL")
    if parsed.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = False
        if not is_loopback:
            raise ValueError("remote LLM gateway base_url must use HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("LLM gateway base_url must not contain userinfo")
    if parsed.query:
        raise ValueError("LLM gateway base_url must not contain query parameters")
    if parsed.fragment:
        raise ValueError("LLM gateway base_url must not contain a fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("LLM gateway base_url contains an invalid port") from exc
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _validated_api_style(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"chat_completions", "responses"}:
        raise ValueError("LLM api_style must be chat_completions or responses")
    return normalized


def _validated_model_name(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise ValueError("LLM model must contain 1 to 128 printable characters")
    return normalized


def _configured_httpx_client(config: LlmConfig) -> LlmClient:
    """Create the production client while keeping injected test doubles simple."""

    client = HttpxLlmClient()
    try:
        client.api_style = config.api_style
    except (AttributeError, TypeError):
        pass
    return client


def _gateway_host(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = str(parsed.hostname or "invalid_gateway")
    try:
        port = parsed.port
    except ValueError:
        port = None
    return f"{host}:{port}" if port is not None else host


_SYSTEM_PROMPT = (
    "你是中文技术会议 Copilot 建议生成器。基于证据片段生成一张低打扰建议卡片。"
    "只返回可解析 JSON：{\"suggestion_text\": str, \"confidence\": 0-1, \"trigger_reason\": str, "
    "\"corrected_transcript\": str|null}。corrected_transcript 仅修正同一证据片段中的 ASR 术语和断句错误，"
    "不得新增、删除或改写原意；不需要修正时返回 null。"
    "措辞用'建议确认/是否考虑过'，禁止'必须/一定/不可上线'等裁判式表达。"
)


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text


def _call_with_retry(client, url, headers, body, timeout, retries=2):
    """Retry only failures that can plausibly recover without user action."""
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return client.post_json(url, headers, body, timeout)
        except Exception as exc:
            last_exc = exc
            retryable = bool(getattr(exc, "retryable", False))
            if attempt < retries and retryable:
                _log.warning("llm.call.retry", attempt=attempt + 1, error_code=type(exc).__name__)
                time.sleep(0.5 * (2 ** attempt))
            else:
                raise
    raise last_exc  # unreachable


def execute_candidate(
    preview: dict[str, Any],
    config: LlmConfig,
    client: LlmClient | None = None,
) -> dict[str, Any]:
    """Call LLM for one suggestion candidate preview; return a run with a real card."""
    client = client or _configured_httpx_client(config)
    evidence_context = str(
        preview.get("evidence_context")
        or preview.get("input_summary", "")
    )
    user_prompt = json.dumps(
        {
            "gap_rule_id": preview.get("gap_rule_id"),
            "target_type": preview.get("target_type"),
            "trigger_reason": preview.get("suggested_prompt")
            or preview.get("input_summary", ""),
            "evidence_context": evidence_context,
        },
        ensure_ascii=False,
    )
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 512,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    request_identity = provider_idempotency_header_value(
        preview.get("idempotency_key")
        or preview.get("request_id")
        or preview.get("execution_id")
    )
    if request_identity is not None:
        headers["Idempotency-Key"] = request_identity
    url = f"{config.base_url}/v1/chat/completions"
    candidate_id = str(preview.get("target_candidate_id", ""))
    _log.info("llm.call.start", candidate_id=candidate_id, model=config.model)
    data = _call_with_retry(
        client,
        url,
        headers,
        body,
        config.timeout_seconds,
        retries=max(0, int(config.max_retries)),
    )
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(_strip_json_fences(content))
    usage = data.get("usage", {})
    usage_record = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    provider = provider_identifier(config)
    card = {
        "card_id": f"suggestion_card_{candidate_id}",
        "card_status": "new",
        "schema_name": "SuggestionCardV1",
        "gap_rule_id": preview.get("gap_rule_id"),
        "target_type": preview.get("target_type"),
        "target_id": preview.get("target_id"),
        "suggestion_text": str(parsed.get("suggestion_text", "")),
        "confidence": float(
            parsed.get("confidence", preview.get("candidate_confidence", 0.5))
        ),
        "trigger_reason": str(
            parsed.get("trigger_reason", preview.get("gap_rule_id", ""))
        ),
        "evidence_span_ids": list(preview.get("evidence_span_ids") or []),
        "evidence_spans": list(preview.get("evidence_spans") or []),
        "evidence_context": evidence_context,
        "source_event_ids": list(preview.get("source_event_ids") or []),
        "llm_trace": {
            "provider": provider,
            "model": config.model,
            "prompt_version": PROMPT_VERSION,
            "call_count": 1,
            "retry_count": 0,
            "usage": usage_record,
        },
    }
    run = {
        **preview,
        "run_id": f"asr_llm_execution_run_enabled_{preview.get('execution_id', '')}",
        "run_status": "completed",
        "execution_status": "executed",
        "provider": provider,
        "model": config.model,
        "prompt_version": PROMPT_VERSION,
        "llm_call_status": "called",
        "schema_status": "generated",
        "card_status": "new",
        "cost_status": "estimated",
        "card": card,
        "llm_usage": usage_record,
    }
    transcript_correction = _single_segment_transcript_correction(
        preview,
        corrected_text=parsed.get("corrected_transcript"),
        usage=usage_record,
    )
    if transcript_correction is not None:
        run["transcript_correction"] = transcript_correction
    _log.info(
        "llm.call.end",
        candidate_id=candidate_id,
        tokens=usage_record["total_tokens"],
    )
    return run


def _single_segment_transcript_correction(
    preview: dict[str, Any],
    *,
    corrected_text: Any,
    usage: dict[str, int],
) -> dict[str, Any] | None:
    corrected = str(corrected_text or "").strip()
    evidence_spans = [
        dict(span)
        for span in list(preview.get("evidence_spans") or [])
        if isinstance(span, dict)
    ]
    if not corrected or not evidence_spans:
        return None
    segment_ids = {str(span.get("segment_id") or "").strip() for span in evidence_spans}
    segment_ids.discard("")
    evidence_span_ids = [str(value) for value in list(preview.get("evidence_span_ids") or []) if str(value)]
    segment_batch = {str(value).strip() for value in list(preview.get("segment_batch") or []) if str(value).strip()}
    if len(segment_ids) != 1 or len(evidence_spans) != 1 or len(evidence_span_ids) != 1:
        return None
    segment_id = next(iter(segment_ids))
    if segment_batch and segment_batch != {segment_id}:
        return None
    evidence = evidence_spans[0]
    evidence_span_id = str(evidence.get("id") or "")
    original_text = str(evidence.get("quote") or "").strip()
    if not evidence_span_id or evidence_span_id != evidence_span_ids[0] or not original_text:
        return None
    return {
        "segment_id": segment_id,
        "evidence_span_id": evidence_span_id,
        "original_text": original_text,
        "corrected_text": corrected,
        "source": "combined_suggestion",
        "usage": dict(usage),
    }


def build_enabled_execution_runs(
    previews: list[dict[str, Any]],
    config: LlmConfig,
    client: LlmClient | None = None,
) -> list[dict[str, Any]]:
    """Execute LLM for each preview; return real runs with cards.

    A failure on one candidate does not abort the batch; the run is marked
    'failed' with llm_call_status='error'.
    """
    runs: list[dict[str, Any]] = []
    for preview in previews:
        try:
            run = execute_candidate(preview, config, client)
        except Exception as exc:
            _log.error(
                "llm.call.error",
                candidate_id=preview.get("target_candidate_id"),
                error_code=type(exc).__name__,
                exc_info=True,
            )
            safe_error = provider_error_payload(
                error_code="llm_provider_failed",
                message="LLM provider request failed",
            )
            run = {
                **preview,
                "run_id": f"asr_llm_execution_run_enabled_{preview.get('execution_id', '')}",
                "run_status": "failed",
                "execution_status": "error",
                "llm_call_status": "error",
                "card_status": "not_created",
                **safe_error,
            }
        runs.append(run)
    return runs


# ---------- Approach-consideration cards (Phase 2.5) ----------

APPROACH_PROMPT_VERSION = "web_mvp.approach_card.v1"
APPROACH_MAX_PER_SESSION = 3
APPROACH_CONFIDENCE_THRESHOLD = 0.7  # stricter than gap cards (≈0.6) — approach advice hallucinates more

_APPROACH_SYSTEM_PROMPT = (
    "你是中文技术会议 Copilot 方案考量生成器。基于会议转写，输出最多 3 条方案考量卡片。"
    "只返回可解析 JSON 数组，每项：{\"card_type\": \"approach.alternative\" 或 \"approach.consideration\", "
    "\"suggestion_text\": str, \"confidence\": 0-1, \"trigger_reason\": str, \"evidence_quote\": str}。"
    "措辞用'是否考虑过/建议确认'，禁止'必须/一定/不可上线'等裁判式表达。"
    "evidence_quote 必须是转写中的原文片段。没有可考量的方案内容时返回空数组 []。"
)


def build_approach_cards(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], bool]:
    """Ask the LLM to produce approach-consideration cards from the transcript.

    Risk gates (stricter than gap cards): confidence >= 0.7, max 3 per session,
    every card must carry an evidence_quote. Returns (cards, usage_record, degraded).
    On persistent LLM failure (after retry), degrades gracefully: returns ([], zeros, True)
    instead of raising — the endpoint stays 200.
    """
    client = client or _configured_httpx_client(config)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _APPROACH_SYSTEM_PROMPT},
            {"role": "user", "content": transcript_text},
        ],
        "temperature": 0,
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.base_url}/v1/chat/completions"
    _log.info("approach.call.start", model=config.model)
    try:
        data = _call_with_retry(client, url, headers, body, config.timeout_seconds)
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(_strip_json_fences(content))
        usage = data.get("usage", {})
    except Exception as exc:
        _log.error("approach.call.failed", error=str(exc), exc_info=True)
        return [], {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, True
    usage_record = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    items = parsed if isinstance(parsed, list) else [parsed]
    cards: list[dict[str, Any]] = []
    provider = provider_identifier(config)
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence", 0.0))
        evidence_quote = str(item.get("evidence_quote", "")).strip()
        # risk gates: confidence threshold + evidence required
        if confidence < APPROACH_CONFIDENCE_THRESHOLD:
            continue
        if not evidence_quote:
            continue
        card_type = str(item.get("card_type", "approach.consideration"))
        if card_type not in {"approach.alternative", "approach.consideration"}:
            card_type = "approach.consideration"
        cards.append({
            "card_id": f"approach_card_{idx}",
            "card_type": card_type,
            "card_status": "new",
            "schema_name": "ApproachConsiderationV1",
            "suggestion_text": str(item.get("suggestion_text", "")),
            "confidence": confidence,
            "trigger_reason": str(item.get("trigger_reason", "")),
            "evidence_quote": evidence_quote,
            "llm_trace": {
                "provider": provider,
                "model": config.model,
                "prompt_version": APPROACH_PROMPT_VERSION,
                "call_count": 1,
                "retry_count": 0,
                "usage": usage_record,
            },
        })
    # max per session
    cards = cards[:APPROACH_MAX_PER_SESSION]
    _log.info("approach.call.end", cards=len(cards), tokens=usage_record["total_tokens"])
    return cards, usage_record, False


# ---------- Post-meeting minutes (Phase P1-3) ----------

MINUTES_PROMPT_VERSION = "web_mvp.minutes.v1"

_MINUTES_SYSTEM_PROMPT = (
    "你是中文技术会议纪要生成器。基于转写，输出结构化纪要 JSON："
    "{\"background\": str, \"decisions\": [str], \"action_items\": [{\"item\":str,\"owner\":str,\"deadline\":str}], "
    "\"risks\": [str], \"open_questions\": [str], \"evidence_quotes\": [str]}。"
    "evidence_quotes 必须来自转写原文。未确认的标'待确认'。禁止编造。无内容时返回空数组。"
)


def _plain_minutes_text(value: Any, field: str, *, max_length: int = 1000) -> str:
    if not isinstance(value, str):
        raise ValueError(f"minutes field {field} must be a string")
    normalized = " ".join(value.split())
    if len(normalized) > max_length:
        raise ValueError(f"minutes field {field} exceeds {max_length} characters")
    return normalized


def _minutes_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError(f"minutes field {field} must be a bounded array")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _plain_minutes_text(item, f"{field}[{index}]")
        if text and text not in result:
            result.append(text)
    return result


def _normalize_minutes_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("minutes response must be a JSON object")
    background = _plain_minutes_text(value.get("background", ""), "background", max_length=4000)
    decisions = _minutes_string_list(value.get("decisions", []), "decisions")
    risks = _minutes_string_list(value.get("risks", []), "risks")
    open_questions = _minutes_string_list(value.get("open_questions", []), "open_questions")
    evidence_quotes = _minutes_string_list(value.get("evidence_quotes", []), "evidence_quotes")
    raw_actions = value.get("action_items", [])
    if not isinstance(raw_actions, list) or len(raw_actions) > 100:
        raise ValueError("minutes field action_items must be a bounded array")
    action_items: list[dict[str, str]] = []
    for index, raw_action in enumerate(raw_actions):
        if not isinstance(raw_action, dict):
            raise ValueError(f"minutes field action_items[{index}] must be an object")
        item = _plain_minutes_text(raw_action.get("item", ""), f"action_items[{index}].item")
        if not item:
            raise ValueError(f"minutes field action_items[{index}].item must not be empty")
        owner = _plain_minutes_text(
            raw_action.get("owner") or "待确认",
            f"action_items[{index}].owner",
        )
        deadline = _plain_minutes_text(
            raw_action.get("deadline") or "待确认",
            f"action_items[{index}].deadline",
        )
        normalized_action = {"item": item, "owner": owner, "deadline": deadline}
        if normalized_action not in action_items:
            action_items.append(normalized_action)
    if not any((background, decisions, action_items, risks, open_questions, evidence_quotes)):
        raise ValueError("minutes response contains no usable content")
    return {
        "background": background,
        "decisions": decisions,
        "action_items": action_items,
        "risks": risks,
        "open_questions": open_questions,
        "evidence_quotes": evidence_quotes,
    }


def _escape_minutes_markdown(value: str) -> str:
    return re.sub(r"([\\`*_\[\]<>#])", r"\\\1", value)


def _minutes_to_markdown(m: dict[str, Any]) -> str:
    lines = ["# 会议纪要", "", f"## 背景\n{_escape_minutes_markdown(m['background'])}", ""]
    lines.append("## 已确认决策")
    for d in m["decisions"]:
        lines.append(f"- {_escape_minutes_markdown(d)}")
    lines.append("\n## 行动项")
    for a in m["action_items"]:
        lines.append(
            f"- {_escape_minutes_markdown(a['item'])} "
            f"(owner: {_escape_minutes_markdown(a['owner'])}, "
            f"deadline: {_escape_minutes_markdown(a['deadline'])})"
        )
    lines.append("\n## 风险")
    for risk in m["risks"]:
        lines.append(f"- {_escape_minutes_markdown(risk)}")
    lines.append("\n## 未闭环问题")
    for q in m["open_questions"]:
        lines.append(f"- {_escape_minutes_markdown(q)}")
    lines.append("\n## 证据片段")
    for e in m["evidence_quotes"]:
        lines.append(f"> {_escape_minutes_markdown(e)}")
    return "\n".join(lines)


def build_minutes_json(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[dict[str, Any], dict[str, int], bool]:
    """Generate structured minutes (parsed JSON dict) via LLM. Degrades gracefully.
    Returns (parsed_dict, usage_record, degraded)."""
    client = client or _configured_httpx_client(config)
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _MINUTES_SYSTEM_PROMPT},
            {"role": "user", "content": transcript_text},
        ],
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    url = f"{config.base_url}/v1/chat/completions"
    _log.info("minutes.call.start", model=config.model)
    try:
        data = _call_with_retry(client, url, headers, body, config.timeout_seconds)
        content = data["choices"][0]["message"]["content"]
        parsed = _normalize_minutes_payload(json.loads(_strip_json_fences(content)))
        usage = data.get("usage", {})
    except Exception as exc:
        _log.error("minutes.call.failed", error=str(exc), exc_info=True)
        return {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, True
    usage_record = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    _log.info("minutes.call.end", tokens=usage_record["total_tokens"])
    return parsed, usage_record, False


def build_minutes(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[str, dict[str, int], bool]:
    """Generate post-meeting minutes (Markdown) via LLM. Degrades gracefully."""
    markdown, _structured, usage, degraded = build_minutes_artifact(
        transcript_text,
        config,
        client,
    )
    return markdown, usage, degraded


def build_minutes_artifact(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[str, dict[str, Any], dict[str, int], bool]:
    """Generate one structured minutes artifact and its Markdown projection."""
    parsed, usage, degraded = build_minutes_json(transcript_text, config, client)
    if degraded:
        return "", {}, usage, True
    return _minutes_to_markdown(parsed), parsed, usage, False

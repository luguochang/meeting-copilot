"""LLM execution service — real suggestion card generation via OpenAI-compatible gateway.

Reads config from env (LLM_GATEWAY_*). Used by the
/live/asr/sessions/{id}/llm-execution-runs endpoint when mode='enabled'.
Default mode remains 'disabled' (safe); 'enabled' actually calls the LLM,
records usage, and creates a real suggestion card (card_status='new').
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import load_dotenv

from meeting_copilot_web_mvp.logging_config import get_logger
from meeting_copilot_web_mvp.streaming_llm_provider import provider_idempotency_header_value

_log = get_logger("meeting_copilot_web_mvp.llm_service")

PROMPT_VERSION = "web_mvp.suggestion_card.v2"
REPO_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
_PROVIDER_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_AUDIT_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class LlmClient(Protocol):
    """Minimal HTTP client protocol so tests can inject a fake."""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]: ...


class HttpxLlmClient:
    """Default LLM client using httpx."""

    def post_json(self, url, headers, body, timeout):
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()


@dataclass(repr=False)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0
    provider_label: str = "openai_compatible_gateway"
    is_mock: bool = False
    max_retries: int = 2

    def __post_init__(self) -> None:
        self.base_url = _validated_base_url(self.base_url)

    def __repr__(self) -> str:
        return (
            "LlmConfig("
            f"gateway_host={_gateway_host(self.base_url)!r}, "
            "api_key='<redacted>', "
            f"model={self.model!r}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"provider_label={self.provider_label!r}, "
            f"is_mock={self.is_mock!r}"
            ")"
        )

    @classmethod
    def from_env(cls) -> "LlmConfig | None":
        """Return config from env, or None if not configured."""
        base = os.environ.get("LLM_GATEWAY_BASE_URL")
        key = os.environ.get("LLM_GATEWAY_API_KEY")
        if not base or not key:
            load_dotenv(REPO_ENV_FILE, override=False)
            base = os.environ.get("LLM_GATEWAY_BASE_URL")
            key = os.environ.get("LLM_GATEWAY_API_KEY")
        if not base or not key:
            return None
        return cls(
            base_url=base.rstrip("/"),
            api_key=key,
            model=os.environ.get("LLM_GATEWAY_MODEL", "gpt-5.5"),
            timeout_seconds=float(os.environ.get("LLM_GATEWAY_TIMEOUT_SECONDS", "60")),
            provider_label=os.environ.get("LLM_GATEWAY_PROVIDER_LABEL", "openai_compatible_gateway"),
            is_mock=_env_bool(os.environ.get("LLM_GATEWAY_IS_MOCK"), default=False),
        )


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
            "is_mock": False,
            "configured_from_env": False,
        }
    return {
        "provider": provider_identifier(config),
        "model": config.model,
        "is_mock": bool(config.is_mock),
        "configured_from_env": True,
    }


def provider_identifier(config: LlmConfig) -> str:
    label = str(config.provider_label or "").strip()
    if _PROVIDER_LABEL_RE.fullmatch(label) and not label.lower().startswith(("http:", "https:")):
        return label
    return _gateway_host(config.base_url)


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
    client = client or HttpxLlmClient()
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
    """Call client.post_json with exponential backoff retry on any error.

    Gateway 5xx / timeouts / network errors are transient — retry before giving
    up so a flaky LLM gateway doesn't fail the whole batch.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            return client.post_json(url, headers, body, timeout)
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
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
    client = client or HttpxLlmClient()
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
    client = client or HttpxLlmClient()
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


def _minutes_to_markdown(m: dict[str, Any]) -> str:
    lines = ["# 会议纪要", "", f"## 背景\n{m.get('background', '')}", ""]
    lines.append("## 已确认决策")
    for d in m.get("decisions") or []:
        lines.append(f"- {d}")
    lines.append("\n## 行动项")
    for a in m.get("action_items") or []:
        lines.append(f"- {a.get('item', '')} (owner: {a.get('owner', '待确认')}, deadline: {a.get('deadline', '待确认')})")
    lines.append("\n## 风险")
    for r in m.get("risks") or []:
        lines.append(f"- {r}")
    lines.append("\n## 未闭环问题")
    for q in m.get("open_questions") or []:
        lines.append(f"- {q}")
    lines.append("\n## 证据片段")
    for e in m.get("evidence_quotes") or []:
        lines.append(f"> {e}")
    return "\n".join(lines)


def build_minutes_json(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[dict[str, Any], dict[str, int], bool]:
    """Generate structured minutes (parsed JSON dict) via LLM. Degrades gracefully.
    Returns (parsed_dict, usage_record, degraded)."""
    client = client or HttpxLlmClient()
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
        parsed = json.loads(_strip_json_fences(content))
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
    return (parsed if isinstance(parsed, dict) else {}), usage_record, False


def build_minutes(
    transcript_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[str, dict[str, int], bool]:
    """Generate post-meeting minutes (Markdown) via LLM. Degrades gracefully."""
    parsed, usage, degraded = build_minutes_json(transcript_text, config, client)
    if degraded:
        return "", usage, True
    return _minutes_to_markdown(parsed), usage, False

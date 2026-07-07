"""LLM execution service — real suggestion card generation via OpenAI-compatible gateway.

Reads config from env (LLM_GATEWAY_*). Used by the
/live/asr/sessions/{id}/llm-execution-runs endpoint when mode='enabled'.
Default mode remains 'disabled' (safe); 'enabled' actually calls the LLM,
records usage, and creates a real suggestion card (card_status='new').
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.llm_service")

PROMPT_VERSION = "web_mvp.suggestion_card.v1"


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
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            return resp.json()


@dataclass
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "LlmConfig | None":
        """Return config from env, or None if not configured."""
        base = os.environ.get("LLM_GATEWAY_BASE_URL")
        key = os.environ.get("LLM_GATEWAY_API_KEY")
        if not base or not key:
            return None
        return cls(
            base_url=base.rstrip("/"),
            api_key=key,
            model=os.environ.get("LLM_GATEWAY_MODEL", "gpt-5.5"),
            timeout_seconds=float(os.environ.get("LLM_GATEWAY_TIMEOUT_SECONDS", "60")),
        )


_SYSTEM_PROMPT = (
    "你是中文技术会议 Copilot 建议生成器。基于证据片段生成一张低打扰建议卡片。"
    "只返回可解析 JSON：{\"suggestion_text\": str, \"confidence\": 0-1, \"trigger_reason\": str}。"
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
                _log.warning("llm.call.retry", attempt=attempt + 1, error=str(exc))
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
    user_prompt = json.dumps(
        {
            "gap_rule_id": preview.get("gap_rule_id"),
            "target_type": preview.get("target_type"),
            "trigger_reason": preview.get("suggested_prompt")
            or preview.get("input_summary", ""),
            "evidence_context": preview.get("input_summary", ""),
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
    }
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    url = f"{config.base_url}/v1/chat/completions"
    candidate_id = str(preview.get("target_candidate_id", ""))
    _log.info("llm.call.start", candidate_id=candidate_id, model=config.model)
    data = _call_with_retry(client, url, headers, body, config.timeout_seconds)
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(_strip_json_fences(content))
    usage = data.get("usage", {})
    usage_record = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
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
        "source_event_ids": list(preview.get("source_event_ids") or []),
        "llm_trace": {
            "provider": config.base_url,
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
        "provider": config.base_url,
        "model": config.model,
        "prompt_version": PROMPT_VERSION,
        "llm_call_status": "called",
        "schema_status": "generated",
        "card_status": "new",
        "cost_status": "estimated",
        "card": card,
        "llm_usage": usage_record,
    }
    _log.info(
        "llm.call.end",
        candidate_id=candidate_id,
        tokens=usage_record["total_tokens"],
    )
    return run


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
                error=str(exc),
                exc_info=True,
            )
            run = {
                **preview,
                "run_id": f"asr_llm_execution_run_enabled_{preview.get('execution_id', '')}",
                "run_status": "failed",
                "execution_status": "error",
                "llm_call_status": "error",
                "card_status": "not_created",
                "error": str(exc),
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
                "provider": config.base_url,
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

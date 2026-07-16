"""L2: LLM-based correction of ASR technical-term misrecognitions.

Multi-level ASR accuracy pipeline:
  L1 ASR raw (local FunASR/sherpa)  ->  L2 LLM correction (this)  ->  L3 normalizer

The LLM sees the raw transcript + domain hint, fixes character/term errors
(t九九->P99, payment gate->checkout-service, 用力->用例, 先挥->灰度) while
preserving meaning. ASR stays local; LLM correction uses the remote LLM gateway
(already used for cards/minutes) — consistent with the product direction
(local ASR, no remote ASR cost; LLM is the existing remote step).

Real-time vs file: file conversion runs full L2 (accuracy); real-time may skip
L2 on partials (speed) and run L2 only on finals, or skip entirely for latency.
"""
from __future__ import annotations

from meeting_copilot_web_mvp.llm_service import (
    HttpxLlmClient,
    LlmClient,
    LlmConfig,
    _call_with_retry,
)
from meeting_copilot_web_mvp.logging_config import get_logger

_log = get_logger("meeting_copilot_web_mvp.asr_correct")

CORRECT_PROMPT_VERSION = "asr_correct.v2"

_SYSTEM = (
    "你是中文技术会议 ASR 转写修正器。输入是 ASR 原始转写，存在术语误识（发音对但字错，"
    "如 t九九=P99、payment gate=checkout-service、用力=用例、先挥=灰度、回軚=回滚）。"
    "根据上下文修正术语误识，保持原意和语气不变，不增删内容、不解释。"
    "不得新增或改变事实、实体、负责人、时间、数值、比例和会议结论。"
    "如果输入包含 <<<MC_SEGMENT:...>>> 和 <<<MC_END:...>>> 索引标记，必须逐字保留每一行标记及原顺序，"
    "只允许修改对应标记之间的转写正文。"
    "只输出修正后的转写纯文本，不要引号、不要 JSON、不要任何说明。"
)


def correct_transcript(
    raw_text: str,
    config: LlmConfig,
    client: LlmClient | None = None,
) -> tuple[str, dict[str, int], bool]:
    """L2 LLM correction. Returns (corrected_text, usage_record, degraded).

    On LLM failure, degrades to the raw text (caller can still normalize it).
    """
    if not raw_text:
        return raw_text, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, False
    client = client or HttpxLlmClient()
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0,
        "reasoning_effort": "low",
        "max_completion_tokens": 4096,
    }
    headers = {"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}
    url = f"{config.base_url}/v1/chat/completions"
    _log.info("asr_correct.start", chars=len(raw_text))
    try:
        data = _call_with_retry(
            client,
            url,
            headers,
            body,
            config.timeout_seconds,
            retries=max(0, int(config.max_retries)),
        )
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
    except Exception as exc:
        _log.error("asr_correct.failed", error=str(exc), exc_info=True)
        return raw_text, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, True
    usage_record = {
        "prompt_tokens": int(usage.get("prompt_tokens", 0)),
        "completion_tokens": int(usage.get("completion_tokens", 0)),
        "total_tokens": int(usage.get("total_tokens", 0)),
    }
    _log.info("asr_correct.end", tokens=usage_record["total_tokens"], chars_out=len(content))
    return content, usage_record, False

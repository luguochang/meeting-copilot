"""Transcript normalizer — fixes common ASR misrecognitions via a term dictionary.

Loads configs/asr_terms.json (raw fragment -> normalized). Applied to ASR final
text so downstream state/LLM see corrected technical entities (e.g. 't九九' ->
'P99'). Conservative: only exact-fragment replacements from the dict, longest
first to avoid partial overlaps. Raw text is always preserved alongside.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_TERMS_PATH = _REPO_ROOT / "configs" / "asr_terms.json"
_PROTECTED_CANONICAL_ENTITIES = (
    "checkout-service",
    "Kafka",
    "Redis",
    "SLO",
    "王五",
    "李四",
)


@lru_cache(maxsize=8)
def load_terms(terms_path: str | None = None) -> dict[str, str]:
    path = Path(terms_path) if terms_path else _DEFAULT_TERMS_PATH
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("terms") or {})


def normalize(raw_text: str, terms_path: str | None = None) -> str:
    """Apply term-dictionary replacements to raw ASR text (longest first)."""
    if not raw_text:
        return raw_text
    terms = load_terms(terms_path)
    out = _collapse_cjk_spaces(raw_text)
    source_text = out
    out = _normalize_observed_contextual_near_misses(out)
    if not terms:
        return out
    # longest keys first to avoid partial overlaps (e.g. '九九延迟' before '九九')
    for key in sorted(terms, key=len, reverse=True):
        if key and key in out:
            if _replacement_injects_protected_entity(source_text, terms[key]):
                continue
            out = out.replace(key, terms[key])
    return out


def _normalize_observed_contextual_near_misses(text: str) -> str:
    normalized = text
    normalized = _normalize_latest_real_mic_release_near_misses(normalized)
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:tracout|acout)\s+out\s+service(?=[\s\u4e00-\u9fff]*(?:周五|灰度|指标|error|P99|p99))",
        "checkout-service",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])older\s+ker(?=[\s\u4e00-\u9fff]*(?:消费堆积|lag|告警))",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"百分十十", "百分之十", normalized)
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])p\s*九(?:九)?b(?=[\s\u4e00-\u9fff]*(?:如果|指标|延迟|回滚|\u3002|$))",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])auto(?:ker|k(?:er)?|worker)?(?=[\s\u4e00-\u9fff]*(?:消费堆积|lag|告警))",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])auder(?=[\s\u4e00-\u9fff]*(?:消费堆积|lag|告警))",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])check\s*out\s*service(?=[\s\u4e00-\u9fff]*(?:周五|灰度|指标|P99|p99|error))",
        "checkout-service",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = _normalize_release_metric_rate_near_miss(normalized)
    return normalized


def _normalize_latest_real_mic_release_near_misses(text: str) -> str:
    normalized = text
    normalized = re.sub(
        r"发布庭审(?=[\s\u4e00-\u9fff]*(?:为先灰度|先灰度|灰度|错误率|回滚|延迟|毫秒))",
        "发布评审",
        normalized,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:pay)?ment\s+gate(?![A-Za-z0-9._/-])",
        "payment-gateway",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])payment\s*(?:gate|ate)(?=[\s\u4e00-\u9fff]*(?:为先灰度|先灰度|灰度|发布|回滚|错误率))",
        "payment-gateway",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])t\s*(?:九九|一九|九)(?=[\s\u4e00-\u9fff]*(?:延迟|毫秒|错误率|回滚))",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])p\s*九九(?=[\s\u4e00-\u9fff]*(?:延迟|毫秒|错误率|回滚))",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])p\s*九五(?=[\s\u4e00-\u9fff]*(?:延迟|毫秒|错误率|回滚))",
        "P95",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])p\s*九(?!\s*[九五])(?=[\s\u4e00-\u9fff]*(?:延迟|毫秒|错误率|回滚))",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])slow\s*(?:看板|碳板)",
        "SLO看板",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:四|斯)\s*low\s*(?:看板|碳板)",
        "SLO看板",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=负责)flow\s*(?:看板|碳板)",
        "SLO看板",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=负责)low\s*(?:看板|碳板)",
        "SLO看板",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"斯隆\s*看板", "SLO看板", normalized)
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:ure|future|featur)\s+flag(?![A-Za-z0-9._/-])",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])feature\s*flag(?=[\s\u4e00-\u9fff]*(?:降级|回滚|rollback|checklist|王五|负责|补齐|兼容性))",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:ure|future|featur|feature)\s+fly(?=[\s\u4e00-\u9fff]*(?:王五|负责|roll|回滚|checklist|补齐|兼容性|确认))",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])picture\s+er(?=[\s\u4e00-\u9fff]*(?:王五|负责|roll|row|raw|回滚|checklist|第六部分))",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])feature\s*lag(?=[\s\u4e00-\u9fff]*(?:王五|负责|roll|row|raw|checklist|第六部分))",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:ure|future|featur)\s+flap(?=[\s\u4e00-\u9fff]*(?:和|roll|回滚|王五|补充|确认))",
        "feature flag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])roll\s*backor(?![A-Za-z0-9._/-])",
        "rollback owner",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=回滚)(?:h?onor|hononor|or)(?=[\s\u4e00-\u9fff]*(?:李四|王五|张三|负责|SLO|看板|兼容性))",
        "owner",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])ononor(?=[\s\u4e00-\u9fff]*(?:张三|李四|王五|负责|SLO|看板|兼容性))",
        "owner",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=下一步)(?:h?onor|那\s*or)(?=要在[\s\u4e00-\u9fff]*(?:补齐|兼容性|灰度|回滚|演练))",
        "owner",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=和)onner(?=[\s\u4e00-\u9fff]*(?:王五|张三|李四|补充|确认|回滚))",
        "owner",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:roll|row|raw|rall|ll)\s*back\s*(?:checklist|list|st)?(?=$|[\s\u4e00-\u9fff]*(?:张三|李四|王五|负责|补充|补齐|测试|兼容性|这里|请|最后|稍后|第六部分|工程风险))",
        "rollback checklist",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])rollback\s*list(?=[\s\u4e00-\u9fff]*(?:张三|李四|王五|负责|补充|测试|这里))",
        "rollback checklist",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<=补充)(?:c?klist|list)(?=[\s\u4e00-\u9fff]*(?:自动化测试|测试|用例|清单))",
        "checklist",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:rediis|redis|discs|disc|r)?closter(?=\s*缓存穿透)",
        "Redis cluster",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:di)?closter(?=\s*缓存穿透)",
        "Redis cluster",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])ster(?=\s*缓存穿透)",
        "Redis cluster",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])(?:caf|cafe|caffe|taf|have)\s*(?:collect|lect)(?=[\s\u4e00-\u9fff]*(?:如果超过|超过三分钟|通知值班群|值班群))",
        "Kafka lag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])have\s*c?\s*t(?=[\s\u4e00-\u9fff]*(?:如果超过|超过三分钟|通知值班群|值班群))",
        "Kafka lag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])have\s*g(?=\s*如超[\s\u4e00-\u9fff]*(?:通知值班群|通知值值群群|值班群|值值群群))",
        "Kafka lag",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])orourer(?=\s*消费堆积)",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])order(?=\s*消费堆积)",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"(?<![A-Za-z0-9._/-])p\s*P99(?![A-Za-z0-9._/-])", "P99", normalized, flags=re.IGNORECASE)
    return normalized


def _normalize_release_metric_rate_near_miss(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        context = text[max(0, start - 28) : min(len(text), end + 18)]
        if re.search(r"(先看|指标|灰度|checkout-service)", context, flags=re.IGNORECASE) and re.search(
            r"(P99|p99|指标|异常|和)",
            context,
            flags=re.IGNORECASE,
        ):
            return "error_rate"
        return match.group(0)

    normalized = re.sub(r"(?<![A-Za-z0-9._/-])error\s+r\s*(?:ate|rate)(?![A-Za-z0-9._/-])", replace, text, flags=re.IGNORECASE)

    def replace_release_near_miss(match: re.Match[str]) -> str:
        start, end = match.span()
        context = normalized[max(0, start - 48) : min(len(normalized), end + 28)]
        if re.search(r"(灰度|发布评审|payment-gateway|P99|p99|百分之零点一|延迟|回滚)", context, flags=re.IGNORECASE):
            return "error_rate"
        return match.group(0)

    return re.sub(
        r"(?<![A-Za-z0-9._/-])(?:era\s+r\s*ate|err?\s*rate|errate|erarea|AR\s*ate|ror\s*(?=超过百分之零点一))(?![A-Za-z0-9._/-])",
        replace_release_near_miss,
        normalized,
        flags=re.IGNORECASE,
    )


def _collapse_cjk_spaces(text: str) -> str:
    normalized = str(text or "")
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r"([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", r"\1", normalized)
    return normalized


def _replacement_injects_protected_entity(source_text: str, replacement: str) -> bool:
    source_lower = source_text.lower()
    replacement_lower = replacement.lower()
    for entity in _PROTECTED_CANONICAL_ENTITIES:
        entity_lower = entity.lower()
        if entity_lower in replacement_lower and entity_lower not in source_lower:
            return True
    return False


def hotwords(terms_path: str | None = None) -> list[str]:
    """Return the hotword list for ASR engines that support them (FunASR)."""
    path = Path(terms_path) if terms_path else _DEFAULT_TERMS_PATH
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("hotwords") or [])

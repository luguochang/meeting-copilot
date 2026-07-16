from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GlossaryTerm:
    canonical: str
    aliases: list[str]


@dataclass(frozen=True)
class NormalizedTranscript:
    text: str
    changes: list[dict[str, str]]


DEFAULT_TERMS = [
    GlossaryTerm(canonical="10%", aliases=["百分之十", "百 分 之 十"]),
    GlossaryTerm(canonical="0.1%", aliases=["百分之零点一", "百 分 之 零 点 一"]),
    GlossaryTerm(canonical="P99", aliases=["九九"]),
]
_CHINESE_NUMBER_RE = r"[零〇一二两三四五六七八九十百千万]+"
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CHINESE_UNITS = {"十": 10, "百": 100, "千": 1000}


def load_glossary(path: Path) -> list[GlossaryTerm]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        GlossaryTerm(
            canonical=str(item["canonical"]),
            aliases=[str(alias) for alias in item.get("aliases", [])],
        )
        for item in data.get("terms", [])
    ]


def normalize_transcript_text(
    text: str,
    glossary_terms: list[GlossaryTerm] | None = None,
) -> NormalizedTranscript:
    normalized = _collapse_cjk_spaces(text)
    normalized, changes = _replace_glossary_aliases(normalized, [*DEFAULT_TERMS, *(glossary_terms or [])])
    normalized, observed_changes = _normalize_observed_technical_near_misses(normalized)
    changes.extend(observed_changes)
    normalized, numeric_changes = _normalize_spoken_error_codes(normalized)
    changes.extend(numeric_changes)
    normalized, metric_changes = _normalize_metric_context_terms(normalized)
    changes.extend(metric_changes)
    return NormalizedTranscript(text=_space_technical_tokens(normalized), changes=changes)


def _replace_glossary_aliases(
    text: str,
    terms: list[GlossaryTerm],
) -> tuple[str, list[dict[str, str]]]:
    normalized = text
    changes: list[dict[str, str]] = []
    alias_pairs = sorted(
        (
            (alias, term.canonical)
            for term in terms
            for alias in term.aliases
            if alias
        ),
        key=lambda pair: len(pair[0]),
        reverse=True,
    )
    for alias, canonical in alias_pairs:
        normalized, changed = _replace_alias(normalized, alias, canonical)
        if changed:
            changes.append({"alias": alias, "canonical": canonical})
    return normalized, changes


def _replace_alias(text: str, alias: str, canonical: str) -> tuple[str, bool]:
    if re.search(r"[A-Za-z0-9]", alias):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9._/-]){re.escape(alias)}(?![A-Za-z0-9._/-])",
            re.IGNORECASE,
        )
        replaced, count = pattern.subn(canonical, text)
        return replaced, count > 0
    if alias not in text:
        return text, False
    return text.replace(alias, canonical), True


def _normalize_spoken_error_codes(text: str) -> tuple[str, list[dict[str, str]]]:
    changes: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        spoken_number = match.group("number")
        parsed = _parse_chinese_integer(spoken_number)
        if parsed is None:
            return match.group(0)
        canonical = str(parsed)
        changes.append({"alias": spoken_number, "canonical": canonical})
        return f"{prefix}{canonical}"

    pattern = re.compile(
        rf"(?P<prefix>(?:错误码|状态码|error code|code)[\u4e00-\u9fffA-Za-z0-9_\s:：-]{{0,8}}?)(?P<number>{_CHINESE_NUMBER_RE})",
        re.IGNORECASE,
    )
    return pattern.sub(replace, text), changes


def _normalize_metric_context_terms(text: str) -> tuple[str, list[dict[str, str]]]:
    normalized = text
    changes: list[dict[str, str]] = []

    if "lag" not in normalized.lower() and "消费堆积" in normalized and "最高到了" in normalized:
        normalized = normalized.replace("消费堆积", "消费堆积 lag", 1)
        changes.append({"alias": "消费堆积最高到了", "canonical": "lag"})

    if (
        "qps" not in normalized.lower()
        and "峰值按" in normalized
        and any(marker in normalized for marker in ("缓存穿透", "压测", "扩容", "降级"))
    ):
        normalized = normalized.replace("峰值按", " QPS 峰值按", 1)
        changes.append({"alias": "峰值按", "canonical": "QPS"})

    return normalized, changes


def _normalize_observed_technical_near_misses(text: str) -> tuple[str, list[dict[str, str]]]:
    normalized = text
    changes: list[dict[str, str]] = []

    normalized, count = re.subn(
        r"(?P<prefix>字段\s*)request(?![A-Za-z0-9._/-])",
        r"\g<prefix>request_id",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "字段 request", "canonical": "request_id"})

    normalized, count = re.subn(
        r"(?P<prefix>字段\s*)quest(?![A-Za-z0-9._/-])",
        r"\g<prefix>request_id",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "字段 quest", "canonical": "request_id"})

    normalized, count = re.subn(
        r"(?<![A-Za-z0-9])redis\s*cost(?:b(?=qps))?",
        "redis cluster ",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "REDIScost", "canonical": "redis cluster"})

    normalized, count = re.subn(
        r"(?<![A-Za-z0-9])auder(?=[\s\u4e00-\u9fff]*(?:消费堆积|lag|告警))",
        "order-worker",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "auder + backlog context", "canonical": "order-worker"})

    normalized, count = re.subn(
        r"(?<![A-Za-z0-9])p{1,2}\s*九{1,2}(?=(?:[abc][\u4e00-\u9fff])|[^A-Za-z0-9]|$)",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "p/pp + 九", "canonical": "P99"})

    normalized, count = re.subn(
        r"(?<![A-Za-z0-9])p{1,2}\s*P99(?=(?:[abc][\u4e00-\u9fff])|[^A-Za-z0-9]|$)",
        "P99",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "p/pp + P99", "canonical": "P99"})

    normalized, count = re.subn(
        r"(?<![A-Za-z0-9._/-])check\s*out\s*service(?=[\s\u4e00-\u9fff]*(?:周五|灰度|指标|P99|p99|error))",
        "checkout-service",
        normalized,
        flags=re.IGNORECASE,
    )
    if count:
        changes.append({"alias": "check outservice + release context", "canonical": "checkout-service"})

    normalized, metric_rate_changes = _normalize_release_metric_rate_near_miss(normalized)
    changes.extend(metric_rate_changes)

    return normalized, changes


def _normalize_release_metric_rate_near_miss(text: str) -> tuple[str, list[dict[str, str]]]:
    changes: list[dict[str, str]] = []

    def replace(match: re.Match[str]) -> str:
        start, end = match.span()
        context = text[max(0, start - 28) : min(len(text), end + 18)]
        if re.search(r"(先看|指标|灰度|checkout-service)", context, flags=re.IGNORECASE) and re.search(
            r"(P99|p99|指标|异常|和)",
            context,
            flags=re.IGNORECASE,
        ):
            changes.append({"alias": match.group(0), "canonical": "error_rate"})
            return "error_rate"
        return match.group(0)

    normalized = re.sub(
        r"(?<![A-Za-z0-9._/-])error\s+r\s*(?:ate|rate)(?![A-Za-z0-9._/-])",
        replace,
        text,
        flags=re.IGNORECASE,
    )
    return normalized, changes


def _parse_chinese_integer(value: str) -> int | None:
    if not value or any(char not in _CHINESE_DIGITS and char not in _CHINESE_UNITS and char != "万" for char in value):
        return None
    if value == "十":
        return 10

    total = 0
    section = 0
    number = 0
    for char in value:
        if char in _CHINESE_DIGITS:
            number = _CHINESE_DIGITS[char]
        elif char in _CHINESE_UNITS:
            unit = _CHINESE_UNITS[char]
            section += (number or 1) * unit
            number = 0
        elif char == "万":
            section += number
            total += (section or 1) * 10000
            section = 0
            number = 0
    return total + section + number


def _collapse_cjk_spaces(text: str) -> str:
    normalized = text.strip()
    previous = None
    while previous != normalized:
        previous = normalized
        normalized = re.sub(r"([\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", r"\1", normalized)
    return normalized


def _space_technical_tokens(text: str) -> str:
    text = re.sub(r"(?<=[\u4e00-\u9fff])([A-Za-z0-9][A-Za-z0-9._/-]*%?)", r" \1", text)
    text = re.sub(r"([A-Za-z0-9][A-Za-z0-9._/-]*%?)(?=[\u4e00-\u9fff])", r"\1 ", text)
    return re.sub(r"\s+", " ", text).strip()

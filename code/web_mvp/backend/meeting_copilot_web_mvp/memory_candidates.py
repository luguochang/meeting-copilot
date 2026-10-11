"""Finite explicit declarations only. Ordinary business text is never a memory seed."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping

MAX_CANDIDATES = 6
_QUOTED = r'[“"「『](?P<{name}>[^”"」』\n]{{1,64}})[”"」』]'
_TERM_PATTERNS = (
    re.compile(r"^(?:术语纠正|我的用词纠正|我的术语纠正)[：:]\s*(?:将|把)" + _QUOTED.format(name="alias")
               + r"\s*(?:改为|统一写为|纠正为)\s*" + _QUOTED.format(name="canonical")),
    re.compile(r"^(?:术语纠正|我的用词纠正|我的术语纠正)[：:]\s*不是" + _QUOTED.format(name="alias")
               + r"[，,；;\s]*(?:正确名称是|正确写法是|而是)\s*" + _QUOTED.format(name="canonical")),
)
_PERSONAL = re.compile(r"^(?:我希望|我偏好|我的表达偏好[：:]|我的提醒偏好[：:]|用户本人偏好[：:]|本人表达偏好[：:])")
_OTHER_SPEAKER = re.compile(r"(?:他说|她说|他希望|她希望|客户表示|参会者表示|[A-Za-z\u3400-\u9fff]{1,12}(?:说|表示|提到)[：:])")
_SPEAKER_LABEL = re.compile(r"^[A-Za-z\u3400-\u9fff]{1,12}[：:]\s*$")


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def semantic_fingerprint(value: Mapping[str, Any]) -> str:
    meaning = {"scope": value["scope"], "scope_id": value["scope_id"], "scene": value.get("scene", ""), "kind": value["kind"],
               "value": normalized(value["value"]), "aliases": sorted({normalized(word) for word in value.get("aliases", [])})}
    if value["kind"] == "preference":
        meaning["memory_key"] = value["memory_key"]
    return digest(meaning)


def explicit_declarations(text: str) -> list[dict[str, Any]]:
    result = []
    previous = ""
    # One bounded new-version scan also retains speaker context across spans.
    for raw in text[:20_000].splitlines():
        line = raw.strip()
        if not line:
            continue
        if _OTHER_SPEAKER.search(line + previous) or _SPEAKER_LABEL.fullmatch(previous):
            # Do not reinterpret the next lines in an attributed quotation as
            # the author's own declarations. No inferred speaker reset.
            if not (_OTHER_SPEAKER.search(previous) or _SPEAKER_LABEL.fullmatch(previous)):
                previous = line
            continue
        if len(line) > 450:
            previous = line
            continue
        term = next((match for pattern in _TERM_PATTERNS if (match := pattern.search(line))), None)
        if term is not None and all(word.strip() and not any(ord(char) < 32 for char in word)
                                    for word in (term["alias"], term["canonical"])) and normalized(term["alias"]) != normalized(term["canonical"]):
            result.append({"kind": "term", "memory_key": normalized(term["canonical"]), "value": term["canonical"],
                           "aliases": [term["alias"]], "quote": line})
        elif _PERSONAL.search(line):
            short = bool(re.search(r"(?:回答|回复|答复).{0,8}(?:简短|简洁|短一点)", line)) and not re.search(r"(?:不|别|不要).{0,4}(?:简短|简洁|短一点)", line)
            standard = bool(re.search(r"(?:回答|回复|答复).{0,8}(?:标准长度|详细一些|详细一点)", line))
            if short != standard:
                result.append({"kind": "preference", "memory_key": "response_length", "value": "brief" if short else "standard", "aliases": [], "quote": line})
            policy = []
            if re.search(r"(?:关闭|不要|停止)主动提醒", line) and not re.search(r"不(?:关闭|停止)主动提醒", line):
                policy.append("off")
            if "低频提醒" in line and "不要低频提醒" not in line:
                policy.append("low_frequency")
            if "正常提醒" in line and "不要正常提醒" not in line:
                policy.append("standard")
            if len(policy) == 1:
                result.append({"kind": "preference", "memory_key": "suggestion_policy", "value": policy[0], "aliases": [], "quote": line})
        previous = line
        if len(result) >= MAX_CANDIDATES:
            break
    return result[:MAX_CANDIDATES]

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.meeting_events import build_state_events

ALLOWED_SUGGESTION_TYPES = {
    "owner_gap",
    "rollback_gap",
    "test_verification_gap",
    "metric_monitoring_gap",
}


def validate_analysis(analysis: dict[str, Any], evidence_span_ids: set[str]) -> None:
    required_top_level = {"summary", "meeting_context", "states", "suggestion_cards"}
    missing = required_top_level - set(analysis)
    if missing:
        raise ValueError(f"analysis missing required fields: {sorted(missing)}")
    meeting_context = analysis["meeting_context"]
    if not isinstance(meeting_context, dict):
        raise ValueError("meeting_context must be an object")
    if "is_engineering_meeting" not in meeting_context:
        raise ValueError("meeting_context.is_engineering_meeting is required")
    states = analysis["states"]
    for key in ["decision_candidates", "action_items", "risks", "open_questions"]:
        if key not in states or not isinstance(states[key], list):
            raise ValueError(f"states.{key} must be a list")
        for item in states[key]:
            item_evidence_ids = _evidence_ids(item)
            if not item_evidence_ids:
                raise ValueError(f"state missing evidence_span_id: states.{key}")
            for evidence_id in item_evidence_ids:
                if evidence_id not in evidence_span_ids:
                    raise ValueError(f"unknown evidence_span_id: {evidence_id}")
    if not isinstance(analysis["suggestion_cards"], list):
        raise ValueError("suggestion_cards must be a list")
    if not meeting_context["is_engineering_meeting"] and analysis["suggestion_cards"]:
        raise ValueError("non-engineering meeting must not produce engineering suggestion cards")
    for card in analysis["suggestion_cards"]:
        card_type = card.get("type")
        if card_type not in ALLOWED_SUGGESTION_TYPES:
            raise ValueError(f"unknown suggestion card type: {card_type}")
        card_evidence_ids = _evidence_ids(card)
        if not card_evidence_ids:
            raise ValueError("suggestion card missing evidence_span_id")
        for evidence_id in card_evidence_ids:
            if evidence_id not in evidence_span_ids:
                raise ValueError(f"unknown evidence_span_id: {evidence_id}")


def _evidence_ids(item: dict[str, Any]) -> list[str]:
    if "evidence_span_ids" in item:
        return [str(value) for value in item["evidence_span_ids"]]
    if "evidence_spans" in item:
        return [str(value) for value in item["evidence_spans"]]
    if "evidence_span_id" in item:
        return [str(item["evidence_span_id"])]
    return []


def build_prompt(transcript_report: dict[str, Any]) -> str:
    return (
        "请基于下面的会议转写报告输出严格 JSON，不要输出 Markdown。"
        "JSON 顶层字段必须是 summary, meeting_context, states, suggestion_cards。"
        "meeting_context 必须包含 is_engineering_meeting 和 reason。"
        "你必须先判断会议是否属于软件工程、研发协作、上线发布、接口评审、事故复盘、项目交付等工程语境。"
        "如果是非软件工程会议，仍可输出 summary 和空的 states，但 suggestion_cards 必须为空。"
        "states 包含 decision_candidates, action_items, risks, open_questions 四个数组。"
        "每个状态对象和建议卡片必须引用已有 evidence_span_id；没有证据就不要输出。"
        "建议卡片只允许 owner_gap, rollback_gap, test_verification_gap, metric_monitoring_gap。"
        "只有软件工程会议才允许输出建议卡片，不要把投资、宏观、学习、闲聊等非工程内容套成工程缺口。"
        "不要编造 owner、deadline、回滚阈值。"
        "\n\n转写报告：\n"
        + json.dumps(transcript_report, ensure_ascii=False)
    )


def _load_llm_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_ca_bundle_path() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    return str(certifi.where())


PROMPT_VERSION = "meeting_analysis.v1"


def call_llm(config: dict[str, Any], prompt: str) -> dict[str, Any]:
    return call_llm_with_usage(config, prompt)["analysis"]


def call_llm_with_usage(config: dict[str, Any], prompt: str) -> dict[str, Any]:
    base_url = str(config["base_url"]).rstrip("/")
    payload = {
        "model": config["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是中文技术会议 Copilot 分析器，只返回可解析 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    request = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    ca_bundle = config.get("ca_bundle_path") or _default_ca_bundle_path()
    context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
    try:
        with urllib.request.urlopen(
            request,
            timeout=float(config.get("timeout_seconds", 60)),
            context=context,
        ) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
    data = json.loads(body)
    content = data["choices"][0]["message"]["content"]
    return {
        "analysis": json.loads(_strip_json_fences(content)),
        "llm_usage": build_llm_usage(config, data.get("usage", {})),
    }


def build_llm_usage(config: dict[str, Any], response_usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": str(config["base_url"]).rstrip("/"),
        "model": str(config["model"]),
        "prompt_version": PROMPT_VERSION,
        "call_count": 1,
        "retry_count": 0,
        "usage": {
            key: int(response_usage.get(key, 0))
            for key in ["prompt_tokens", "completion_tokens", "total_tokens"]
        },
    }


def masked_llm_config(config: dict[str, Any]) -> dict[str, Any]:
    masked = dict(config)
    if "api_key" in masked:
        masked["api_key"] = _mask_secret(str(masked["api_key"]))
    return masked


def _mask_secret(secret: str) -> str:
    if not secret:
        return ""
    if secret.startswith("sk-"):
        return "sk-***"
    return "***"


def _strip_json_fences(content: str) -> str:
    text = content.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    if text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze transcript report with an LLM gateway.")
    parser.add_argument("--transcript-report", required=True, type=Path)
    parser.add_argument("--llm-config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--events-output", type=Path)
    parser.add_argument("--usage-output", type=Path)
    args = parser.parse_args()

    report = json.loads(args.transcript_report.read_text(encoding="utf-8"))
    evidence_ids = {item["id"] for item in report.get("evidence_spans", [])}
    llm_config = _load_llm_config(args.llm_config)
    llm_result = call_llm_with_usage(llm_config, build_prompt(report))
    analysis = llm_result["analysis"]
    validate_analysis(analysis, evidence_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    response = {"output": str(args.output), "cards": len(analysis["suggestion_cards"])}
    if args.usage_output:
        args.usage_output.parent.mkdir(parents=True, exist_ok=True)
        args.usage_output.write_text(
            json.dumps(llm_result["llm_usage"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        response["usage_output"] = str(args.usage_output)
    if args.events_output:
        events = build_state_events(analysis, created_at_ms=0)
        args.events_output.parent.mkdir(parents=True, exist_ok=True)
        args.events_output.write_text(
            json.dumps([asdict(event) for event in events], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        response["events_output"] = str(args.events_output)
        response["events"] = len(events)
    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

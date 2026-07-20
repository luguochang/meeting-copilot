#!/usr/bin/env python3
"""Compare real-time OpenAI-compatible models without retaining meeting content."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import platform
import statistics
import ssl
import time
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


CONFIG_SCHEMA = "meeting_copilot.local_provider_test.v1"
REPORT_SCHEMA = "meeting_copilot.provider_latency_bakeoff.v1"
REQUIRED_RESULT_KEYS = frozenset({"topic_update", "state_changes", "follow_up"})
SYSTEM_PROMPT = (
    "你是中文技术会议实时理解引擎。只输出紧凑 JSON，不要 Markdown。"
    "根字段必须是 topic_update、state_changes、follow_up。"
    "state_changes 只记录原文明确存在的 decision、action_item 或 risk，"
    "每项必须有 type、content、evidence_quote，不得补造事实。"
)
USER_PROMPT = (
    "我们决定把认证服务拆成独立进程，下周三由李明完成灰度方案。"
    "当前风险是缓存穿透会放大数据库压力，需要确认限流阈值和回滚负责人。"
)
PROMPT_HASH = hashlib.sha256(
    f"{SYSTEM_PROMPT}\n{USER_PROMPT}".encode("utf-8")
).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_base_url(value: Any) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base_url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query or fragment")
    if parsed.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            is_loopback = parsed.hostname.lower() == "localhost"
        if not is_loopback:
            raise ValueError("remote base_url must use HTTPS")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def load_config(path: Path) -> dict[str, str]:
    path = Path(path)
    if path.is_symlink():
        raise ValueError("provider config must not be a symlink")
    path = path.resolve(strict=True)
    if path.stat().st_mode & 0o777 != 0o600:
        raise ValueError("provider config must have 0600 permissions")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("provider config is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("provider config must be a JSON object")
    allowed = {"schema_version", "base_url", "api_key", "model", "api_style"}
    if set(payload) - allowed:
        raise ValueError("provider config contains unsupported keys")
    if payload.get("schema_version") != CONFIG_SCHEMA:
        raise ValueError("provider config schema_version is invalid")
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise ValueError("provider config api_key is required")
    api_style = str(payload.get("api_style") or "chat_completions").strip().lower()
    if api_style not in {"chat_completions", "responses"}:
        raise ValueError("api_style must be chat_completions or responses")
    model = str(payload.get("model") or "").strip()
    if not model or len(model) > 128:
        raise ValueError("provider config model is invalid")
    return {
        "base_url": _normalize_base_url(payload.get("base_url")),
        "api_key": api_key,
        "model": model,
        "api_style": api_style,
    }


@dataclass(frozen=True)
class StreamResult:
    status_code: int | None
    ttft_ms: float | None
    total_ms: float
    text: str
    usage: dict[str, int]
    error_type: str | None


def _request_shape(model: str, api_style: str) -> tuple[str, dict[str, Any]]:
    if api_style == "responses":
        return "/v1/responses", {
            "model": model,
            "instructions": SYSTEM_PROMPT,
            "input": [{"role": "user", "content": USER_PROMPT}],
            "max_output_tokens": 220,
            "store": False,
            "stream": True,
        }
    return "/v1/chat/completions", {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "max_completion_tokens": 220,
        "stream": True,
    }


def _event_text(event: dict[str, Any], api_style: str) -> str:
    if api_style == "responses":
        if event.get("type") == "response.output_text.delta":
            return str(event.get("delta") or "")
        return ""
    choices = event.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    return str(delta.get("content") or "") if isinstance(delta, dict) else ""


def _event_usage(event: dict[str, Any], api_style: str) -> dict[str, int]:
    raw: Any = event.get("usage")
    if api_style == "responses" and event.get("type") == "response.completed":
        response = event.get("response")
        raw = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        return {}
    result: dict[str, int] = {}
    aliases = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for target, names in aliases.items():
        value = next(
            (raw.get(name) for name in names if isinstance(raw.get(name), int)), None
        )
        if isinstance(value, int) and value >= 0:
            result[target] = value
    if "total_tokens" not in result and {"input_tokens", "output_tokens"} <= set(
        result
    ):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def stream_once(
    *,
    base_url: str,
    api_key: str,
    model: str,
    api_style: str,
    timeout_seconds: float,
) -> StreamResult:
    parsed = urlsplit(base_url)
    path, payload = _request_shape(model, api_style)
    endpoint_path = f"{parsed.path.rstrip('/')}{path}"
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port,
            timeout=timeout_seconds,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port,
            timeout=timeout_seconds,
        )
    started = time.monotonic()
    first_content_at: float | None = None
    text_parts: list[str] = []
    usage: dict[str, int] = {}
    status_code: int | None = None
    error_type: str | None = None
    try:
        connection.request(
            "POST",
            endpoint_path,
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
        )
        response = connection.getresponse()
        status_code = response.status
        if response.status != 200:
            response.read(256 * 1024)
        else:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeDecodeError:
                    error_type = "invalid_utf8_stream"
                    break
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    error_type = "invalid_json_sse_event"
                    break
                if not isinstance(event, dict):
                    continue
                content = _event_text(event, api_style)
                if content:
                    if first_content_at is None:
                        first_content_at = time.monotonic()
                    text_parts.append(content)
                observed_usage = _event_usage(event, api_style)
                if observed_usage:
                    usage = observed_usage
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        error_type = type(exc).__name__
    finally:
        connection.close()
    finished = time.monotonic()
    return StreamResult(
        status_code=status_code,
        ttft_ms=(
            round((first_content_at - started) * 1000, 3)
            if first_content_at is not None
            else None
        ),
        total_ms=round((finished - started) * 1000, 3),
        text="".join(text_parts),
        usage=usage,
        error_type=error_type,
    )


def validate_structured_text(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {"valid": False, "reason": "invalid_json"}
    if not isinstance(payload, dict) or not REQUIRED_RESULT_KEYS <= set(payload):
        return {"valid": False, "reason": "required_keys_missing"}
    changes = payload.get("state_changes")
    if not isinstance(changes, list) or not changes:
        return {"valid": False, "reason": "state_changes_missing"}
    allowed_types = {"decision", "action_item", "risk"}
    for item in changes:
        if not isinstance(item, dict):
            return {"valid": False, "reason": "state_change_not_object"}
        if item.get("type") not in allowed_types:
            return {"valid": False, "reason": "state_change_type_invalid"}
        if not str(item.get("content") or "").strip():
            return {"valid": False, "reason": "state_change_content_missing"}
        if not str(item.get("evidence_quote") or "").strip():
            return {"valid": False, "reason": "evidence_quote_missing"}
    return {"valid": True, "reason": None, "state_change_count": len(changes)}


def _percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, int((len(ordered) * fraction) - 1e-9)))
    return round(ordered[index], 3)


def run_bakeoff(
    *,
    config: dict[str, str],
    models: list[str],
    repeats: int,
    timeout_seconds: float,
    ttft_slo_ms: float,
    total_slo_ms: float,
) -> dict[str, Any]:
    if repeats < 1 or repeats > 20:
        raise ValueError("repeats must be between 1 and 20")
    unique_models = list(
        dict.fromkeys(model.strip() for model in models if model.strip())
    )
    if not unique_models:
        raise ValueError("at least one model is required")
    if any(len(model) > 128 for model in unique_models):
        raise ValueError("model name is invalid")
    runs: list[dict[str, Any]] = []
    for model in unique_models:
        for run_number in range(1, repeats + 1):
            result = stream_once(
                base_url=config["base_url"],
                api_key=config["api_key"],
                model=model,
                api_style=config["api_style"],
                timeout_seconds=timeout_seconds,
            )
            structured = (
                validate_structured_text(result.text)
                if result.text
                else {
                    "valid": False,
                    "reason": "visible_content_missing",
                }
            )
            runs.append(
                {
                    "model": model,
                    "run": run_number,
                    "status_code": result.status_code,
                    "ttft_ms": result.ttft_ms,
                    "total_ms": result.total_ms,
                    "structured": structured,
                    "content_length": len(result.text),
                    "content_sha256": hashlib.sha256(
                        result.text.encode("utf-8")
                    ).hexdigest()
                    if result.text
                    else None,
                    "usage": result.usage,
                    "error_type": result.error_type,
                }
            )
    summaries: list[dict[str, Any]] = []
    for model in unique_models:
        model_runs = [item for item in runs if item["model"] == model]
        successful = [
            item
            for item in model_runs
            if item["status_code"] == 200
            and item["ttft_ms"] is not None
            and item["error_type"] is None
        ]
        ttft_values = [float(item["ttft_ms"]) for item in successful]
        total_values = [float(item["total_ms"]) for item in successful]
        valid_count = sum(bool(item["structured"]["valid"]) for item in successful)
        median_ttft = round(statistics.median(ttft_values), 3) if ttft_values else None
        p95_total = _percentile(total_values, 0.95)
        blockers: list[str] = []
        if len(successful) != repeats:
            blockers.append("not_all_calls_succeeded")
        if valid_count != repeats:
            blockers.append("not_all_outputs_structurally_valid")
        if median_ttft is None or median_ttft > ttft_slo_ms:
            blockers.append("median_ttft_slo_missed")
        if p95_total is None or p95_total > total_slo_ms:
            blockers.append("p95_total_slo_missed")
        summaries.append(
            {
                "model": model,
                "run_count": len(model_runs),
                "successful_count": len(successful),
                "structured_valid_count": valid_count,
                "median_ttft_ms": median_ttft,
                "p95_ttft_ms": _percentile(ttft_values, 0.95),
                "median_total_ms": round(statistics.median(total_values), 3)
                if total_values
                else None,
                "p95_total_ms": p95_total,
                "realtime_candidate": not blockers,
                "blockers": blockers,
            }
        )
    candidates = sorted(
        (item for item in summaries if item["realtime_candidate"]),
        key=lambda item: (item["median_ttft_ms"], item["p95_total_ms"]),
    )
    parsed = urlsplit(config["base_url"])
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _now(),
        "scope": "direct_provider_streaming_bakeoff_not_packaged_acceptance",
        "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "runtime": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "provider_origin_sha256": hashlib.sha256(
            f"{parsed.scheme}://{parsed.netloc}".encode("utf-8")
        ).hexdigest(),
        "api_style": config["api_style"],
        "prompt_sha256": PROMPT_HASH,
        "prompt_or_response_retained": False,
        "provider_secret_retained": False,
        "thresholds": {
            "median_ttft_ms": ttft_slo_ms,
            "p95_total_ms": total_slo_ms,
            "structured_valid_rate": 1.0,
        },
        "runs": runs,
        "models": summaries,
        "recommended_realtime_model": candidates[0]["model"] if candidates else None,
        "verdict": "go_direct_realtime_candidate"
        if candidates
        else "no_go_no_realtime_candidate",
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--ttft-slo-ms", type=float, default=3_000.0)
    parser.add_argument("--total-slo-ms", type=float, default=8_000.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    models = args.model or [config["model"]]
    report = run_bakeoff(
        config=config,
        models=models,
        repeats=args.repeats,
        timeout_seconds=args.timeout_seconds,
        ttft_slo_ms=args.ttft_slo_ms,
        total_slo_ms=args.total_slo_ms,
    )
    _write_report(args.output, report)
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "recommended_realtime_model": report["recommended_realtime_model"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["recommended_realtime_model"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

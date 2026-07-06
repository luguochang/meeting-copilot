from __future__ import annotations

import argparse
import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from asr_bakeoff.llm_gateway import load_llm_gateway_config


def _default_ca_bundle_path() -> str | None:
    try:
        import certifi
    except ImportError:
        return None
    return str(certifi.where())


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test an OpenAI-compatible LLM gateway.")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--prompt", default="请用一句中文回复：LLM 中转站连通。")
    args = parser.parse_args()

    config = load_llm_gateway_config(args.config)
    payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": "你是一个连通性测试助手，只返回简短中文。"},
            {"role": "user", "content": args.prompt},
        ],
        "temperature": 0,
    }
    request = urllib.request.Request(
        config.chat_completions_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    ca_bundle = config.ca_bundle_path or _default_ca_bundle_path()
    context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else None
    try:
        with urllib.request.urlopen(request, timeout=config.timeout_seconds, context=context) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"request failed: {exc.reason}") from exc

    data: dict[str, Any] = json.loads(body)
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    print(json.dumps({"model": config.model, "reply": content}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

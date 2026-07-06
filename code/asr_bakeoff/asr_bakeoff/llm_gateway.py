from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LlmGatewayConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 30.0
    ca_bundle_path: str | None = None

    @property
    def chat_completions_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"

    @property
    def masked_api_key(self) -> str:
        if len(self.api_key) <= 8:
            return "***"
        return f"{self.api_key[:3]}...{self.api_key[-4:]}"


def load_llm_gateway_config(path: Path) -> LlmGatewayConfig:
    data = json.loads(path.read_text(encoding="utf-8"))
    return LlmGatewayConfig(
        base_url=str(data["base_url"]),
        api_key=str(data["api_key"]),
        model=str(data["model"]),
        timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        ca_bundle_path=data.get("ca_bundle_path"),
    )

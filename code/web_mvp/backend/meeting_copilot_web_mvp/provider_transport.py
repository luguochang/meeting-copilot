"""Explicit outbound Provider proxy; system proxy settings remain opt-in."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit


def provider_httpx_options() -> dict[str, Any]:
    options: dict[str, Any] = {"trust_env": False}
    proxy = str(os.environ.get("LLM_GATEWAY_PROXY_URL") or "").strip()
    if not proxy:
        return options
    try:
        parsed = urlsplit(proxy)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and parsed.port != 0
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and not any(ord(char) < 32 or char.isspace() for char in proxy)
        )
    except ValueError:
        valid = False
    if not valid:
        # Proxy URLs may contain credentials; never echo a rejected value.
        raise ValueError("LLM_GATEWAY_PROXY_URL must be a valid HTTP(S) proxy URL")
    options["proxy"] = proxy
    # An explicit HTTPX proxy does not consult NO_PROXY. Keep UI-selected
    # local Providers reachable without sending loopback requests to it.
    options["mounts"] = {
        "all://localhost": None,
        "all://127.0.0.1": None,
        "all://[::1]": None,
    }
    return options

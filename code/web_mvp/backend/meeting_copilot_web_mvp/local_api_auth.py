from __future__ import annotations

from http.cookies import SimpleCookie
import hashlib
import hmac
import json
from typing import Any
from urllib.parse import urlsplit

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


LOCAL_API_TOKEN_ENV = "MEETING_COPILOT_LOCAL_API_TOKEN"
SESSION_COOKIE_NAME = "meeting_copilot_session"
BOOTSTRAP_PATH = "/desktop/bootstrap"
HEALTH_PATH = "/health"
_SESSION_CONTEXT = b"meeting-copilot-session-v1"
_HEALTH_CONTEXT = b"meeting-copilot-health-v1"
STRICT_CSP = (
    "default-src 'self'; "
    "base-uri 'none'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "media-src 'self' blob:; "
    "worker-src 'self' blob:; "
    "connect-src 'self' ws://127.0.0.1:* ws://localhost:*"
)


def session_cookie_value(token: str) -> str:
    return hmac.new(token.encode("utf-8"), _SESSION_CONTEXT, hashlib.sha256).hexdigest()


def health_proof(token: str) -> str:
    return hmac.new(token.encode("utf-8"), _HEALTH_CONTEXT, hashlib.sha256).hexdigest()


def _headers(scope: Scope) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _cookie(headers: dict[str, str], name: str) -> str | None:
    raw = headers.get("cookie")
    if not raw:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return None
    morsel = cookie.get(name)
    return morsel.value if morsel is not None else None


def _origin_is_allowed(headers: dict[str, str]) -> bool:
    origin = headers.get("origin")
    if not origin:
        return True
    host = headers.get("host", "").lower()
    parsed = urlsplit(origin)
    return (
        parsed.scheme in {"http", "https"}
        and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
        and parsed.netloc.lower() == host
    )


class LocalApiAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str | None = None) -> None:
        self.app = app
        self.token = str(token or "").strip()
        self.cookie_value = session_cookie_value(self.token) if self.token else ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.token or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        headers = _headers(scope)
        public_path = path in {HEALTH_PATH, BOOTSTRAP_PATH}
        header_token = headers.get("x-meeting-copilot-token", "")
        cookie_token = _cookie(headers, SESSION_COOKIE_NAME) or ""
        authenticated = hmac.compare_digest(header_token, self.token) or hmac.compare_digest(
            cookie_token,
            self.cookie_value,
        )
        if not public_path and (not authenticated or not _origin_is_allowed(headers)):
            await self._reject(scope, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers["content-security-policy"] = STRICT_CSP
                response_headers["x-content-type-options"] = "nosniff"
                response_headers["referrer-policy"] = "no-referrer"
                response_headers["cache-control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)

    async def _reject(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4403, "reason": "local authentication required"})
            return
        body = json.dumps(
            {
                "detail": {
                    "error": "local_authentication_required",
                    "message": "本地客户端认证失败",
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def token_status(token: str | None) -> dict[str, Any]:
    configured = bool(str(token or "").strip())
    return {
        "enabled": configured,
        "transport": "http_only_same_site_strict_cookie" if configured else "development_unprotected",
    }

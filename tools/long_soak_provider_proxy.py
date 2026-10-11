#!/usr/bin/env python3
"""Loopback-only acceptance proxy with a hard bound on forwarded model POSTs.

This is a test boundary, not a production budget feature. Credentials live in
memory/environment only. The report never contains headers or request/response
bodies; failed upstream attempts consume a slot and are never retried here.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import sys
from threading import Lock
import time
from urllib.parse import urlsplit
from unittest.mock import patch

import httpx


ALLOWED_PATHS = frozenset({"/v1/responses", "/v1/chat/completions"})
ALLOWED_LANES = frozenset({"correction", "state", "automatic", "answer", "minutes", "approach", "unknown"})
MAX_BODY_BYTES = 1_048_576


@dataclass
class AttemptBudget:
    limit: int = 12
    report: Path | None = None
    reserved: int = 0
    rejected: int = 0
    events: list[dict] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self):
        if type(self.limit) is not int or not 1 <= self.limit <= 12:
            raise ValueError("test budget must be between 1 and 12 attempts")

    def reserve(self, *, lane: str, endpoint: str) -> int | None:
        with self.lock:
            if self.reserved >= self.limit:
                self.rejected += 1
                self._save_locked()
                return None
            self.reserved += 1
            sequence = self.reserved
            self.events.append({"sequence": sequence, "lane": lane, "endpoint": endpoint,
                                "started_at_ms": time.time_ns() // 1_000_000,
                                "status": "reserved", "http_status": None})
            self._save_locked()
            return sequence

    def finish(self, sequence: int, *, status: str, http_status: int | None) -> None:
        with self.lock:
            event = self.events[sequence - 1]
            event.update(status=status, http_status=http_status,
                         completed_at_ms=time.time_ns() // 1_000_000)
            self._save_locked()

    def snapshot(self) -> dict:
        with self.lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> dict:
        return {"schema_version": "long_soak_provider_boundary.v1", "scope": "isolated_test_runtime",
                "maximum_forward_attempts": self.limit, "reserved_forward_attempts": self.reserved,
                "budget_rejections": self.rejected, "remaining": self.limit - self.reserved,
                "upstream_retries": 0, "headers_or_bodies_logged": False,
                "lane_attribution": "optional_test_header_otherwise_unknown", "events": [dict(x) for x in self.events]}

    def _save_locked(self) -> None:
        if self.report is not None:
            self.report.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.report.with_suffix(self.report.suffix + ".tmp")
            temporary.write_text(json.dumps(self._snapshot_locked(), indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.report)


def native_provider_httpx_options(*, explicit_proxy_url: str = "") -> dict:
    """Use the native policy, with an explicit test-only proxy selection."""
    backend = str(Path(__file__).resolve().parents[1] / "code/web_mvp/backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from meeting_copilot_web_mvp.provider_transport import provider_httpx_options

    # Startup-only mapping; restore the caller's environment before any request.
    with patch.dict(os.environ, {"LLM_GATEWAY_PROXY_URL": explicit_proxy_url}):
        return provider_httpx_options()


def normalized_upstream(base: str, *, allow_local_upstream: bool = False) -> str:
    parsed = urlsplit(base)
    local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parsed.scheme != "https" and not (allow_local_upstream and parsed.scheme == "http" and local)) or (
        not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise ValueError("upstream must be an HTTPS base URL without embedded credentials")
    stripped = base.rstrip("/")
    return stripped if parsed.path.rstrip("/").endswith("/v1") else stripped + "/v1"


def make_server(*, port: int, upstream_base: str, upstream_key: str, proxy_token: str,
                budget: AttemptBudget, model: str = "gpt-6-sol", timeout_seconds: float = 45,
                allow_local_upstream: bool = False, upstream_httpx_options: dict | None = None) -> ThreadingHTTPServer:
    if not upstream_key or not proxy_token:
        raise ValueError("private upstream key and proxy token are required")
    base = normalized_upstream(upstream_base, allow_local_upstream=allow_local_upstream)
    options = dict(upstream_httpx_options) if upstream_httpx_options is not None else native_provider_httpx_options()
    if options.get("trust_env") is not False:
        raise ValueError("acceptance transport must explicitly disable system proxy inheritance")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            # BaseHTTPRequestHandler otherwise logs paths and exceptions.
            return

        def respond(self, status: int, payload: dict):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)
            self.close_connection = True

        def do_GET(self):
            if self.path not in {"/health", "/status"}:
                self.respond(404, {"error": {"code": "soak_proxy_route_not_allowed"}})
                return
            self.respond(200, {"status": "ok", "model": model, **budget.snapshot()})

        def do_POST(self):
            if self.path not in ALLOWED_PATHS:
                self.respond(404, {"error": {"code": "soak_proxy_route_not_allowed"}})
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + proxy_token):
                self.respond(401, {"error": {"code": "soak_proxy_auth_required"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY_BYTES:
                    raise ValueError
                body = self.rfile.read(length)
                payload = json.loads(body)
                if len(body) != length or not isinstance(payload, dict) or payload.get("model") != model:
                    raise ValueError
            except (ValueError, UnicodeError):
                self.respond(422, {"error": {"code": "soak_proxy_request_not_allowed"}})
                return
            lane = self.headers.get("X-Meeting-Soak-Lane", "unknown")
            lane = lane if lane in ALLOWED_LANES else "unknown"
            sequence = budget.reserve(lane=lane, endpoint=self.path)
            if sequence is None:
                self.respond(503, {"error": {"code": "soak_provider_budget_exhausted",
                                            "type": "soak_budget_exhausted",
                                            "message": "The isolated test forwarding budget is exhausted."}})
                return
            status = "transport_error"
            http_status = None
            headers_sent = False
            try:
                with httpx.Client(timeout=timeout_seconds, follow_redirects=False, **options) as client:
                    with client.stream("POST", base + self.path.removeprefix("/v1"), content=body,
                            headers={"Authorization": "Bearer " + upstream_key, "Content-Type": "application/json"}) as response:
                        http_status = response.status_code
                        if 300 <= http_status < 400:
                            status = "redirect_rejected"
                            self.respond(502, {"error": {"code": "soak_proxy_upstream_redirect_rejected"}})
                            return
                        self.send_response(http_status)
                        content_type = response.headers.get("Content-Type", "application/json")
                        # Forward only fixed media types, never upstream headers.
                        content_type = "text/event-stream" if content_type.startswith("text/event-stream") else "application/json"
                        self.send_header("Content-Type", content_type)
                        self.send_header("Transfer-Encoding", "chunked")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        headers_sent = True
                        # No chunk_size: do not buffer small SSE frames to 64 KiB.
                        for chunk in response.iter_bytes():
                            if chunk:
                                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n")
                                self.wfile.flush()
                        self.wfile.write(b"0\r\n\r\n")
                        self.wfile.flush()
                        status = "completed" if 200 <= http_status < 300 else "upstream_error"
            except (BrokenPipeError, ConnectionResetError):
                status = "client_disconnected"
            except (httpx.HTTPError, OSError, TimeoutError):
                if not headers_sent:
                    self.respond(502, {"error": {"code": "soak_proxy_upstream_transport_failed"}})
            finally:
                self.close_connection = True
                budget.finish(sequence, status=status, http_status=http_status)

    class SafeServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            # Unexpected server failures must not dump transport exceptions,
            # URLs, headers or request objects into the acceptance log.
            return

    server = SafeServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--max-forward-attempts", type=int, choices=range(1, 13), default=12)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--upstream-base-url-env", default="MEETING_SOAK_UPSTREAM_BASE_URL")
    parser.add_argument("--upstream-key-env", default="MEETING_SOAK_UPSTREAM_API_KEY")
    parser.add_argument("--proxy-token-env", default="MEETING_SOAK_PROXY_TOKEN")
    parser.add_argument("--upstream-proxy-env", default="MEETING_SOAK_UPSTREAM_PROXY_URL")
    args = parser.parse_args(argv)
    if args.report.exists():
        parser.error("report must be a fresh path; restarting must not silently reset an old budget")
    budget = AttemptBudget(limit=args.max_forward_attempts, report=args.report.resolve())
    server = make_server(port=args.port, upstream_base=os.environ.get(args.upstream_base_url_env, ""),
                         upstream_key=os.environ.get(args.upstream_key_env, ""),
                         proxy_token=os.environ.get(args.proxy_token_env, ""), budget=budget,
                         upstream_httpx_options=native_provider_httpx_options(
                             explicit_proxy_url=os.environ.get(args.upstream_proxy_env, "")))
    with budget.lock:
        budget._save_locked()
    print(json.dumps({"status": "ready", "port": server.server_port, "maximum_forward_attempts": budget.limit,
                      "headers_or_bodies_logged": False}), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

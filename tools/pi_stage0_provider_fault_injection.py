#!/usr/bin/env python3
"""Exercise the OpenAI-compatible realtime Provider over a real local socket.

The normal Provider unit tests use ``httpx.MockTransport``.  That is useful for
protocol contracts, but it cannot expose connection pooling, response-stream
closure, socket timeouts, or cancellation races.  This runner starts a
loopback-only ``ThreadingHTTPServer`` and injects bounded failures at the HTTP
boundary.  It never contacts a remote service and never accepts a credential
from the command line.

The output is a redacted, deterministic-shaped report suitable for attaching
to Stage 0A evidence.  The fixture key is generated in memory and is never
included in requests recorded by the report.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from meeting_copilot_web_mvp.streaming_llm_provider import (  # noqa: E402
    OpenAICompatibleStreamingProvider,
    ProviderErrorCategory,
    StreamingProviderError,
)


REPORT_SCHEMA_VERSION = "meeting_copilot.pi_stage0_provider_fault_injection.v1"
FIXTURE_API_KEY_BYTES = 24
DEFAULT_TIMEOUT_SECONDS = 0.15
DEFAULT_TIMEOUT_DELAY_SECONDS = 0.45
DEFAULT_CONCURRENCY = 4
DEFAULT_SCENARIOS = (
    "success",
    "rate_limit",
    "server_error",
    "timeout",
    "transport",
    "protocol",
    "empty",
    "cancel",
)
SCENARIOS = frozenset(DEFAULT_SCENARIOS)


@dataclass(frozen=True)
class FaultObservation:
    """One request outcome with no request content or credentials."""

    index: int
    scenario: str
    outcome: str
    provider_category: str | None
    status_code: int | None
    retry_after_ms: int | None
    elapsed_ms: float
    request_seen: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "scenario": self.scenario,
            "outcome": self.outcome,
            "provider_category": self.provider_category,
            "status_code": self.status_code,
            "retry_after_ms": self.retry_after_ms,
            "elapsed_ms": self.elapsed_ms,
            "request_seen": self.request_seen,
        }


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class FaultFixture:
    """A loopback HTTP fixture with per-request failure selection."""

    def __init__(self, *, api_key: str, timeout_delay_seconds: float) -> None:
        if timeout_delay_seconds <= 0:
            raise ValueError("timeout_delay_seconds must be positive")
        self.api_key = api_key
        self.timeout_delay_seconds = float(timeout_delay_seconds)
        self._lock = threading.Lock()
        self._requests: list[dict[str, Any]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self) -> None:  # noqa: N802
                if self.path != "/v1/chat/completions":
                    self._send_json(404, {"error": {"code": "ENDPOINT_NOT_FOUND"}})
                    return
                try:
                    length = int(self.headers.get("content-length") or 0)
                    body = json.loads(self.rfile.read(max(0, length)) or b"{}")
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    self._send_json(400, {"error": {"code": "INVALID_REQUEST_ERROR"}})
                    return

                expected = f"Bearer {owner.api_key}"
                authenticated = self.headers.get("authorization") == expected
                scenario = str(body.get("x_stage0_fault") or "success").strip().lower()
                if scenario not in SCENARIOS:
                    scenario = "protocol"
                with owner._lock:
                    owner._requests.append(
                        {
                            "scenario": scenario,
                            "authenticated": authenticated,
                            "at_monotonic": time.monotonic(),
                        }
                    )
                if not authenticated:
                    self._send_json(401, {"error": {"code": "AUTHENTICATION_ERROR"}})
                    return

                if scenario == "timeout":
                    # Delay before response headers so httpx raises a genuine
                    # timeout from the socket/connection boundary.
                    time.sleep(owner.timeout_delay_seconds)
                    self._send_sse("fixture-timeout-late")
                    return
                if scenario == "rate_limit":
                    self._send_json(
                        429,
                        {"error": {"code": "RATE_LIMIT_EXCEEDED"}},
                        headers={"Retry-After": "1"},
                    )
                    return
                if scenario == "server_error":
                    self._send_json(503, {"error": {"code": "SERVER_BUSY"}})
                    return
                if scenario == "transport":
                    self._send_truncated_stream()
                    return
                if scenario == "protocol":
                    self._send_raw_sse(b"data: not-json\n\n")
                    return
                if scenario == "empty":
                    self._send_raw_sse(b"data: [DONE]\n\n")
                    return
                if scenario == "cancel":
                    # The client task is cancelled while waiting for this
                    # response.  Keep the server side bounded and harmless.
                    time.sleep(owner.timeout_delay_seconds)
                    self._send_sse("fixture-cancel-late")
                    return
                self._send_sse("fixture-ok")

            def _send_json(
                self,
                status_code: int,
                payload: Mapping[str, Any],
                *,
                headers: Mapping[str, str] | None = None,
            ) -> None:
                encoded = _json_bytes(payload)
                try:
                    self.send_response(status_code)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(encoded)))
                    for key, value in (headers or {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                    self.wfile.write(encoded)
                    self.wfile.flush()
                except OSError:
                    # A timed-out/cancelled client is expected to close the
                    # socket before the delayed fixture writes its response.
                    return

            def _send_sse(self, text_value: str) -> None:
                event = {
                    "id": "fixture",
                    "choices": [
                        {
                            "delta": {"content": text_value},
                            "finish_reason": "stop",
                        }
                    ],
                }
                body = (
                    "data: "
                    + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n\ndata: [DONE]\n\n"
                ).encode("utf-8")
                self._send_raw_sse(body)

            def _send_raw_sse(self, body: bytes) -> None:
                try:
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("content-length", str(len(body)))
                    self.send_header("connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                except OSError:
                    return

            def _send_truncated_stream(self) -> None:
                body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                try:
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    # Deliberately advertise more bytes than sent, then close
                    # the socket.  httpx classifies this as a transport error.
                    self.send_header("content-length", str(len(body) + 32))
                    self.send_header("connection", "close")
                    self.end_headers()
                    self.wfile.write(body)
                    self.wfile.flush()
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    self.connection.close()
                    self.close_connection = True
                except OSError:
                    return

            def log_message(self, _format: str, *_args: object) -> None:
                return

        # The fixture intentionally drives more requests than the default
        # listen backlog.  A small backlog makes an otherwise immediate
        # truncated response wait behind another handler and get classified as
        # a socket timeout, which tests the scheduler rather than the declared
        # transport fault.  This must be a class attribute because
        # ``TCPServer.__init__`` calls ``listen(request_queue_size)``.
        class Stage0HTTPServer(ThreadingHTTPServer):
            request_queue_size = 64

        self.server = Stage0HTTPServer(("127.0.0.1", 0), Handler)
        # Let ``server_close`` join request handlers so delayed timeout/cancel
        # responses cannot overlap the next fixture instance.
        self.server.daemon_threads = False
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            name="stage0-provider-fault-fixture",
            daemon=True,
        )
        self._started = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def start(self) -> None:
        if self._started:
            return
        self.thread.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            self.server.server_close()
            return
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self._started = False

    def request_count(self, scenario: str) -> int:
        with self._lock:
            return sum(item["scenario"] == scenario for item in self._requests)

    def authenticated_request_count(self) -> int:
        with self._lock:
            return sum(bool(item["authenticated"]) for item in self._requests)


def _exception_observation(
    index: int,
    scenario: str,
    error: BaseException,
    *,
    elapsed_ms: float,
    request_seen: bool,
) -> FaultObservation:
    if isinstance(error, asyncio.CancelledError):
        return FaultObservation(
            index=index,
            scenario=scenario,
            outcome="cancelled",
            provider_category=None,
            status_code=None,
            retry_after_ms=None,
            elapsed_ms=round(elapsed_ms, 3),
            request_seen=request_seen,
        )
    if isinstance(error, StreamingProviderError):
        return FaultObservation(
            index=index,
            scenario=scenario,
            outcome="error",
            provider_category=error.category.value,
            status_code=error.status_code,
            retry_after_ms=error.retry_after_ms,
            elapsed_ms=round(elapsed_ms, 3),
            request_seen=request_seen,
        )
    return FaultObservation(
        index=index,
        scenario=scenario,
        outcome="unexpected_error",
        provider_category=type(error).__name__,
        status_code=None,
        retry_after_ms=None,
        elapsed_ms=round(elapsed_ms, 3),
        request_seen=request_seen,
    )


async def _run_one(
    provider: OpenAICompatibleStreamingProvider,
    fixture: FaultFixture,
    *,
    index: int,
    scenario: str,
) -> FaultObservation:
    started = time.perf_counter()
    task = asyncio.create_task(
        provider.complete(
            [{"role": "user", "content": "stage0 fixture"}],
            x_stage0_fault=scenario,
        )
    )
    try:
        if scenario == "cancel":
            await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError as exc:
                return _exception_observation(
                    index,
                    scenario,
                    exc,
                    elapsed_ms=(time.perf_counter() - started) * 1_000,
                    request_seen=fixture.request_count(scenario) > 0,
                )
            return FaultObservation(
                index=index,
                scenario=scenario,
                outcome="unexpected_success",
                provider_category=None,
                status_code=None,
                retry_after_ms=None,
                elapsed_ms=round((time.perf_counter() - started) * 1_000, 3),
                request_seen=fixture.request_count(scenario) > 0,
            )
        await task
        return FaultObservation(
            index=index,
            scenario=scenario,
            outcome="success",
            provider_category=None,
            status_code=None,
            retry_after_ms=None,
            elapsed_ms=round((time.perf_counter() - started) * 1_000, 3),
            request_seen=fixture.request_count(scenario) > 0,
        )
    except BaseException as exc:  # classify, then keep the runner alive
        # The loopback handler records receipt on a worker thread.  Give that
        # bookkeeping a bounded scheduling opportunity before evaluating the
        # request-seen invariant; otherwise full-suite CPU contention can make
        # a correctly sent request appear absent.
        await asyncio.sleep(0.005)
        return _exception_observation(
            index,
            scenario,
            exc,
            elapsed_ms=(time.perf_counter() - started) * 1_000,
            request_seen=fixture.request_count(scenario) > 0,
        )
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except BaseException:
                pass


def _expected(scenario: str) -> tuple[str, str | None, int | None]:
    return {
        "success": ("success", None, None),
        "rate_limit": ("error", ProviderErrorCategory.RATE_LIMIT.value, 429),
        "server_error": ("error", ProviderErrorCategory.PROVIDER_SERVER.value, 503),
        "timeout": ("error", ProviderErrorCategory.TIMEOUT.value, None),
        "transport": ("error", ProviderErrorCategory.TRANSPORT.value, None),
        "protocol": ("error", ProviderErrorCategory.PROTOCOL.value, None),
        "empty": ("error", ProviderErrorCategory.EMPTY_RESPONSE.value, None),
        "cancel": ("cancelled", None, None),
    }[scenario]


async def run_fault_injection(
    scenarios: Sequence[str] = DEFAULT_SCENARIOS,
    *,
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    timeout_delay_seconds: float = DEFAULT_TIMEOUT_DELAY_SECONDS,
) -> dict[str, Any]:
    normalized = [str(item).strip().lower() for item in scenarios]
    if not normalized or any(item not in SCENARIOS for item in normalized):
        raise ValueError(f"scenarios must be drawn from {sorted(SCENARIOS)}")
    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if timeout_delay_seconds <= timeout_seconds:
        raise ValueError("timeout_delay_seconds must exceed timeout_seconds")

    # The value is only an in-memory loopback fixture credential.  Do not put
    # it in the report, logs, command line, or child process environment.
    api_key = secrets.token_urlsafe(FIXTURE_API_KEY_BYTES)
    fixture = FaultFixture(
        api_key=api_key,
        timeout_delay_seconds=timeout_delay_seconds,
    )
    fixture.start()
    observations: list[FaultObservation] = []
    try:
        import httpx

        client = httpx.AsyncClient(
            timeout=timeout_seconds,
            trust_env=False,
            limits=httpx.Limits(
                max_connections=max(concurrency, 1),
                max_keepalive_connections=max(concurrency, 1),
            ),
        )
        provider = OpenAICompatibleStreamingProvider(
            base_url=fixture.base_url,
            api_key=api_key,
            model="stage0-fixture-model",
            client=client,
            timeout_seconds=timeout_seconds,
            allow_non_streaming_fallback=False,
        )
        try:
            semaphore = asyncio.Semaphore(concurrency)

            async def run_with_limit(index: int, scenario: str) -> FaultObservation:
                async with semaphore:
                    return await _run_one(
                        provider,
                        fixture,
                        index=index,
                        scenario=scenario,
                    )

            observations = list(
                await asyncio.gather(
                    *(run_with_limit(index, scenario) for index, scenario in enumerate(normalized))
                )
            )
        finally:
            await provider.aclose()
            await client.aclose()
    finally:
        fixture.stop()

    observations.sort(key=lambda item: item.index)
    checks: list[dict[str, Any]] = []
    for observation in observations:
        expected_outcome, expected_category, expected_status = _expected(observation.scenario)
        checks.append(
            {
                "index": observation.index,
                "scenario": observation.scenario,
                "passed": (
                    observation.outcome == expected_outcome
                    and observation.provider_category == expected_category
                    and (
                        expected_status is None
                        or observation.status_code == expected_status
                    )
                    and observation.request_seen
                ),
                "expected": {
                    "outcome": expected_outcome,
                    "provider_category": expected_category,
                    "status_code": expected_status,
                },
                "observed": observation.to_dict(),
            }
        )
    outcome_counts = Counter(item.outcome for item in observations)
    category_counts = Counter(
        item.provider_category
        for item in observations
        if item.provider_category is not None
    )
    report_without_hash: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "transport": "loopback_threading_http_server",
        "fixture": {
            "gateway": "127.0.0.1",
            "is_remote": False,
            "is_mock": False,
            "request_count": len(observations),
            "authenticated_request_count": fixture.authenticated_request_count(),
            "concurrency": concurrency,
            "timeout_seconds": timeout_seconds,
            "timeout_delay_seconds": timeout_delay_seconds,
        },
        "scenarios": normalized,
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "provider_category_counts": dict(sorted(category_counts.items())),
        "observations": [item.to_dict() for item in observations],
        "checks": checks,
        "passed": bool(checks) and all(bool(item["passed"]) for item in checks),
    }
    report_without_hash["report_sha256"] = hashlib.sha256(
        json.dumps(
            report_without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return report_without_hash


def _parse_scenarios(raw: str) -> list[str]:
    values = [item.strip().lower() for item in str(raw or "").split(",") if item.strip()]
    return values or list(DEFAULT_SCENARIOS)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        default=",".join(DEFAULT_SCENARIOS),
        help="comma-separated fixture scenarios",
    )
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--timeout-delay-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_DELAY_SECONDS,
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            run_fault_injection(
                _parse_scenarios(args.scenarios),
                concurrency=args.concurrency,
                timeout_seconds=args.timeout_seconds,
                timeout_delay_seconds=args.timeout_delay_seconds,
            )
        )
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        sys.stdout.write(encoded)
    else:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
        sys.stdout.write(json.dumps({"output": str(output), "passed": report["passed"]}) + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":  # pragma: no cover - exercised by the CLI smoke test
    raise SystemExit(main())

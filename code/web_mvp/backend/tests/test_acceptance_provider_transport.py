"""Real ephemeral loopback forwarding; no model, production runtime or audio."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import struct
import sys
from threading import Event, Thread
import time

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools"))
import long_soak_provider_proxy as proxy
import participation_continuous_acceptance as runner


@contextmanager
def serving(server):
    worker = Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@contextmanager
def upstream(*, mode="json", finished=None):
    requests = []
    gate = finished or Event()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_POST(self):
            requests.append({"path": self.path, "body": self.rfile.read(int(self.headers["Content-Length"])),
                             "authorization": self.headers.get("Authorization")})
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/redirected")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if mode in {"sse", "sse_abort"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                last_chunk = b"data: [DONE]\n\n" if mode == "sse" else b"data: " + b"x" * 262_144 + b"\n\n"
                for chunk in (b"data: first\n\n", last_chunk):
                    try:
                        self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    if chunk == b"data: first\n\n":
                        assert gate.wait(timeout=3)
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            body = b'{"error":{"code":"local_error"}}' if mode == "error" else b'{"output_text":"local-only"}'
            self.send_response(429 if mode == "error" else 200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    try:
        with serving(server) as url:
            yield url, requests, gate
    finally:
        gate.set()


def new_proxy(url, budget, **kwargs):
    return proxy.make_server(port=0, upstream_base=url, upstream_key="synthetic-upstream-key",
        proxy_token="synthetic-boundary-token", budget=budget, allow_local_upstream=True, **kwargs)


def post(client, url, *, token="synthetic-boundary-token", model="gpt-6-sol", path="/v1/responses"):
    return client.post(url + path, headers={"Authorization": "Bearer " + token},
                       json={"model": model, "stream": False, "input": "public synthetic only"})


def terminal(budget):
    deadline = time.monotonic() + 2
    while any(e["status"] == "reserved" for e in budget.snapshot()["events"]):
        assert time.monotonic() < deadline
        time.sleep(.005)
    return budget.snapshot()


def test_native_forwarding_ignores_implicit_system_proxy(monkeypatch, tmp_path):
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(key, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    budget = proxy.AttemptBudget(limit=1, report=tmp_path / "ledger.json")
    with upstream() as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = post(client, boundary)
    assert response.status_code == 200
    assert response.json() == {"output_text": "local-only"}
    assert len(requests) == 1
    assert terminal(budget)["events"][0]["status"] == "completed"


def test_explicit_native_options_reach_client_and_loopback_stays_direct(monkeypatch):
    options = runner.proxy.native_provider_httpx_options(explicit_proxy_url="http://127.0.0.1:1")
    assert options["trust_env"] is False and options["proxy"] == "http://127.0.0.1:1"
    assert options["mounts"] == {"all://localhost": None, "all://127.0.0.1": None, "all://[::1]": None}
    original = httpx.Client
    observed = []

    def capture(**kwargs):
        observed.append(kwargs)
        return original(**kwargs)

    budget = proxy.AttemptBudget(limit=1)
    with upstream() as (url, requests, _), serving(new_proxy(url, budget, upstream_httpx_options=options)) as boundary:
        with original(trust_env=False, timeout=2) as client:
            monkeypatch.setattr(proxy.httpx, "Client", capture)
            response = post(client, boundary)
    assert response.status_code == 200 and len(requests) == 1
    assert len(observed) == 1 and observed[0]["trust_env"] is False
    assert observed[0]["follow_redirects"] is False and "verify" not in observed[0]
    assert observed[0]["proxy"] == options["proxy"] and observed[0]["mounts"] == options["mounts"]


def test_continuous_cli_maps_only_explicit_child_proxy(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_CONTINUOUS_UPSTREAM_BASE_URL", "https://public-synthetic.invalid")
    monkeypatch.setenv("MEETING_CONTINUOUS_UPSTREAM_API_KEY", "synthetic-key")
    monkeypatch.setenv("MEETING_CONTINUOUS_PROXY_TOKEN", "synthetic-token")
    monkeypatch.setenv("MEETING_CONTINUOUS_UPSTREAM_PROXY_URL", "http://127.0.0.1:7999")
    monkeypatch.setenv("LLM_GATEWAY_PROXY_URL", "http://127.0.0.1:7998")
    captured = []

    class Server:
        def serve_forever(self, **_kwargs):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(runner.proxy, "make_server", lambda **kwargs: captured.append(kwargs) or Server())
    runner.serve_proxy(tmp_path)
    assert captured[0]["upstream_httpx_options"]["proxy"] == "http://127.0.0.1:7999"
    assert captured[0]["upstream_httpx_options"]["trust_env"] is False
    assert runner.os.environ["LLM_GATEWAY_PROXY_URL"] == "http://127.0.0.1:7998"
    ledger = json.loads((tmp_path / "proxy-ledger.json").read_text())
    assert ledger["maximum_forward_attempts"] == 14 and ledger["reserved_forward_attempts"] == 0


def test_sse_first_small_chunk_arrives_before_upstream_finishes():
    budget = proxy.AttemptBudget(limit=1)
    finish = Event()
    with upstream(mode="sse", finished=finish) as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        with httpx.Client(trust_env=False, timeout=2) as client:
            try:
                with client.stream("POST", boundary + "/v1/responses",
                        headers={"Authorization": "Bearer synthetic-boundary-token"}, json={"model": "gpt-6-sol", "stream": True}) as response:
                    chunks = response.iter_bytes()
                    assert next(chunks) == b"data: first\n\n"
                    assert not finish.is_set() and budget.snapshot()["events"][0]["status"] == "reserved"
                    finish.set()
                    assert b"".join(chunks) == b"data: [DONE]\n\n"
                    assert response.headers["content-type"] == "text/event-stream"
            finally:
                finish.set()
    assert len(requests) == 1 and terminal(budget)["events"][0]["status"] == "completed"


@pytest.mark.parametrize(("mode", "status", "outcome"), [("redirect", 502, "redirect_rejected"), ("error", 429, "upstream_error")])
def test_upstream_error_or_redirect_consumes_once_and_never_retries(mode, status, outcome, tmp_path):
    budget = proxy.AttemptBudget(limit=1, report=tmp_path / "ledger.json")
    with upstream(mode=mode) as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert post(client, boundary).status_code == status
            assert post(client, boundary).status_code == 503
    assert len(requests) == 1
    result = terminal(budget)
    assert result["events"][0]["status"] == outcome
    assert result["reserved_forward_attempts"] == result["budget_rejections"] == 1
    assert result["upstream_retries"] == 0 and result["headers_or_bodies_logged"] is False
    text = (tmp_path / "ledger.json").read_text()
    assert "synthetic-upstream-key" not in text and "synthetic-boundary-token" not in text
    assert "public synthetic only" not in text and "public-synthetic.invalid" not in text


@pytest.mark.parametrize(("kwargs", "status"), [({"token": "wrong"}, 401), ({"model": "other"}, 422), ({"path": "/forbidden"}, 404)])
def test_pre_forward_denial_spends_no_budget(kwargs, status):
    budget = proxy.AttemptBudget(limit=1)
    with upstream() as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        with httpx.Client(trust_env=False, timeout=2) as client:
            assert post(client, boundary, **kwargs).status_code == status
    assert requests == [] and budget.snapshot()["reserved_forward_attempts"] == 0


def test_native_httpx_timeout_is_terminal_redacted_and_not_retried(monkeypatch, tmp_path):
    budget = proxy.AttemptBudget(limit=1, report=tmp_path / "ledger.json")
    original = httpx.HTTPTransport.handle_request
    hits = []
    with upstream() as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        upstream_port = httpx.URL(url).port

        def fail_upstream(self, request):
            if request.url.port == upstream_port:
                hits.append(request.url.port)
                raise httpx.ReadTimeout("private-key/private-url must remain hidden")
            return original(self, request)

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fail_upstream)
        with httpx.Client(trust_env=False, timeout=2) as client:
            response = post(client, boundary)
            assert response.status_code == 502
            assert post(client, boundary).status_code == 503
    assert len(hits) == 1 and requests == []
    result = terminal(budget)
    assert result["events"][0]["status"] == "transport_error" and result["events"][0]["http_status"] is None
    assert result["reserved_forward_attempts"] == 1 and result["upstream_retries"] == 0
    assert "private-key" not in response.text and "private-url" not in (tmp_path / "ledger.json").read_text()


def test_invalid_explicit_proxy_rejects_before_server_without_private_values(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    with pytest.raises(ValueError) as error:
        proxy.native_provider_httpx_options(explicit_proxy_url="http://user:synthetic-private@local host:9")
    assert "synthetic-private" not in str(error.value) and "local host" not in str(error.value)


def test_real_client_abort_closes_upstream_and_finishes_consumed_slot():
    budget = proxy.AttemptBudget(limit=1)
    finish = Event()
    with upstream(mode="sse_abort", finished=finish) as (url, requests, _), serving(new_proxy(url, budget)) as boundary:
        body = b'{"model":"gpt-6-sol","stream":true}'
        with socket.create_connection(("127.0.0.1", httpx.URL(boundary).port), timeout=2) as client:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: localhost\r\n"
                b"Authorization: Bearer synthetic-boundary-token\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            received = b""
            while b"data: first\n\n" not in received:
                part = client.recv(4096)
                assert part
                received += part
            assert budget.snapshot()["events"][0]["status"] == "reserved"
            # Exact test-owned loopback socket: abort before the upstream tail.
            linger = struct.pack("hh" if os.name == "nt" else "ii", 1, 0)
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
        try:
            finish.set()
            result = terminal(budget)
        finally:
            finish.set()
    assert len(requests) == 1 and result["reserved_forward_attempts"] == 1
    assert result["upstream_retries"] == 0
    assert result["events"][0]["status"] in {"client_disconnected", "transport_error"}
    assert type(result["events"][0]["completed_at_ms"]) is int

import asyncio
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from meeting_copilot_web_mvp.provider_transport import provider_httpx_options


def test_system_proxy_does_not_implicitly_redirect_provider(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_PROXY_URL", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://unrelated.example:8080")
    assert provider_httpx_options() == {"trust_env": False}


def test_explicit_local_proxy_is_enabled_without_inheriting_environment(monkeypatch):
    monkeypatch.setenv("LLM_GATEWAY_PROXY_URL", " http://127.0.0.1:7892 ")
    assert provider_httpx_options() == {
        "trust_env": False,
        "proxy": "http://127.0.0.1:7892",
        "mounts": {
            "all://localhost": None,
            "all://127.0.0.1": None,
            "all://[::1]": None,
        },
    }


@contextmanager
def local_provider_server(host):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"local-provider-ok"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET

    try:
        server = Server((host, 0), Handler)
    except OSError:
        if host == "::1":
            pytest.skip("IPv6 loopback is unavailable")
        raise
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
@pytest.mark.parametrize("async_client", [False, True], ids=["sync", "async"])
def test_loopback_provider_bypasses_unreachable_explicit_proxy(monkeypatch, host, async_client):
    monkeypatch.setenv("LLM_GATEWAY_PROXY_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    bind_host = "::1" if host == "[::1]" else "127.0.0.1"
    with local_provider_server(bind_host) as port:
        url = f"http://{host}:{port}/v1/models"
        if async_client:
            async def request():
                async with httpx.AsyncClient(timeout=2, **provider_httpx_options()) as client:
                    return await client.get(url)

            response = asyncio.run(request())
        else:
            with httpx.Client(timeout=2, **provider_httpx_options()) as client:
                response = client.get(url)
    assert response.status_code == 200
    assert response.text == "local-provider-ok"


@pytest.mark.parametrize("proxy", [
    "socks5://user:private-password@localhost:1080",
    "http://user:private-password@localhost:wrong-port",
    "http://user:private-password@localhost:0",
    "http://user:private-password@localhost:8080/path",
    "http://user:private-password@localhost:8080?key=secret",
    "http://user:private-password@local host:8080",
])
def test_invalid_proxy_is_rejected_without_disclosing_credentials(monkeypatch, proxy):
    monkeypatch.setenv("LLM_GATEWAY_PROXY_URL", proxy)
    with pytest.raises(ValueError) as failure:
        provider_httpx_options()
    assert "private-password" not in str(failure.value)
    assert "secret" not in str(failure.value)

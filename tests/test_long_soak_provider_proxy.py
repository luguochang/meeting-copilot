from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock, Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.long_soak_provider_proxy import AttemptBudget, make_server, normalized_upstream


@contextmanager
def running(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def faux_upstream(*, status=200, sse=False):
    calls = []
    guard = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            with guard:
                calls.append(self.path)
            body = b'data: {"test":true}\n\ndata: [DONE]\n\n' if sse else b'{"test":true}'
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Private-Header", "do-not-forward-this-secret")
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler), calls


def post(base, *, path="/v1/responses", token="proxy-test-private", model="gpt-6-sol"):
    request = Request(base + path, data=json.dumps({"model": model, "input": "private-fixture-body"}).encode(),
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.code, response.read(), dict(response.headers)


def make_proxy(upstream, budget):
    return make_server(port=0, upstream_base=upstream, upstream_key="upstream-test-private",
                       proxy_token="proxy-test-private", budget=budget, allow_local_upstream=True)


def test_concurrent_real_local_posts_never_forward_the_thirteenth(tmp_path, capsys):
    upstream, calls = faux_upstream()
    budget = AttemptBudget(limit=12, report=tmp_path / "boundary.json")
    with running(upstream) as url:
        with running(make_proxy(url, budget)) as proxy:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: post(proxy)[0], range(24)))
    assert results.count(200) == 12
    assert results.count(503) == 12
    assert len(calls) == budget.reserved == 12
    assert budget.rejected == 12
    report = json.loads((tmp_path / "boundary.json").read_text())
    assert report["upstream_retries"] == 0
    assert all(e["status"] == "completed" for e in report["events"])
    captured = capsys.readouterr()
    output = json.dumps(report) + captured.out + captured.err
    for private in ("private-fixture-body", "proxy-test-private", "upstream-test-private", "do-not-forward-this-secret", "Authorization"):
        assert private not in output


def test_bad_auth_route_or_model_consumes_no_slot():
    upstream, calls = faux_upstream()
    budget = AttemptBudget(limit=2)
    with running(upstream) as url:
        with running(make_proxy(url, budget)) as proxy:
            assert post(proxy, token="wrong")[0] == 401
            assert post(proxy, path="/v1/files")[0] == 404
            assert post(proxy, model="different-model")[0] == 422
    assert budget.reserved == 0
    assert not calls


def test_two_pi_model_turns_are_two_posts_and_sse_is_preserved():
    upstream, calls = faux_upstream(sse=True)
    budget = AttemptBudget(limit=2)
    with running(upstream) as url:
        with running(make_proxy(url, budget)) as proxy:
            first = post(proxy)
            second = post(proxy)
            third = post(proxy)
    assert first[0] == second[0] == 200
    assert first[1] == second[1] == b'data: {"test":true}\n\ndata: [DONE]\n\n'
    assert first[2]["Content-Type"] == "text/event-stream"
    assert "X-Private-Header" not in first[2]
    assert third[0] == 503
    assert len(calls) == 2


def test_failed_upstream_post_consumes_slot_without_implicit_retry():
    upstream, calls = faux_upstream(status=500)
    budget = AttemptBudget(limit=1)
    with running(upstream) as url:
        with running(make_proxy(url, budget)) as proxy:
            assert post(proxy)[0] == 500
            assert post(proxy)[0] == 503
    assert len(calls) == 1
    assert budget.snapshot()["events"][0]["status"] == "upstream_error"


@pytest.mark.parametrize("base", ["http://example.com", "https://user:secret@example.com", "https://example.com?secret=x"])
def test_unsafe_upstream_is_rejected(base):
    with pytest.raises(ValueError):
        normalized_upstream(base)


def test_upstream_base_preserves_existing_v1_path():
    assert normalized_upstream("https://example.com/openai/v1/") == "https://example.com/openai/v1"
    assert normalized_upstream("https://example.com") == "https://example.com/v1"

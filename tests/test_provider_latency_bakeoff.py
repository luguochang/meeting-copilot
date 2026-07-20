from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools/provider_latency_bakeoff.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("provider_latency_bakeoff", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _StreamingHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("content-length", "0"))
        request = json.loads(self.rfile.read(size))
        assert self.headers.get("authorization") == "Bearer local-secret"
        assert request["stream"] is True
        if self.path == "/v1/chat/completions":
            events = [
                {"choices": [{"delta": {"role": "assistant"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "content": json.dumps(
                                    {
                                        "topic_update": {
                                            "title": "认证服务",
                                            "summary": "拆分与灰度",
                                        },
                                        "state_changes": [
                                            {
                                                "type": "decision",
                                                "content": "拆分认证服务",
                                                "evidence_quote": "决定把认证服务拆成独立进程",
                                            }
                                        ],
                                        "follow_up": {"question": "谁负责回滚？"},
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                },
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 30,
                        "completion_tokens": 20,
                        "total_tokens": 50,
                    },
                },
            ]
        elif self.path == "/v1/responses":
            content = json.dumps(
                {
                    "topic_update": {"title": "认证服务"},
                    "state_changes": [
                        {
                            "type": "risk",
                            "content": "缓存穿透",
                            "evidence_quote": "缓存穿透会放大数据库压力",
                        }
                    ],
                    "follow_up": None,
                },
                ensure_ascii=False,
            )
            events = [
                {"type": "response.output_text.delta", "delta": content},
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 30,
                            "output_tokens": 20,
                            "total_tokens": 50,
                        }
                    },
                },
            ]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for event in events:
            self.wfile.write(
                f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
            )
            self.wfile.flush()
            time.sleep(0.003)
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture
def gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def write_config(
    path: Path, base_url: str, api_style: str = "chat_completions"
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "meeting_copilot.local_provider_test.v1",
                "base_url": base_url,
                "api_key": "local-secret",
                "model": "fixture-model",
                "api_style": api_style,
            }
        )
    )
    path.chmod(0o600)


@pytest.mark.parametrize("api_style", ["chat_completions", "responses"])
def test_bakeoff_measures_visible_content_and_retains_no_secret_or_text(
    tmp_path, gateway, api_style
):
    tool = load_tool()
    config_path = tmp_path / "provider.json"
    write_config(config_path, gateway, api_style)
    config = tool.load_config(config_path)

    report = tool.run_bakeoff(
        config=config,
        models=["fixture-model"],
        repeats=2,
        timeout_seconds=2,
        ttft_slo_ms=1_000,
        total_slo_ms=1_000,
    )

    assert report["verdict"] == "go_direct_realtime_candidate"
    assert report["recommended_realtime_model"] == "fixture-model"
    assert report["prompt_or_response_retained"] is False
    assert report["provider_secret_retained"] is False
    assert all(item["structured"]["valid"] for item in report["runs"])
    assert all(item["ttft_ms"] is not None for item in report["runs"])
    assert all(item["usage"]["total_tokens"] == 50 for item in report["runs"])
    serialized = json.dumps(report, ensure_ascii=False)
    assert "local-secret" not in serialized
    assert gateway not in serialized
    assert "拆分认证服务" not in serialized
    assert "缓存穿透" not in serialized


def test_config_rejects_permissive_secret_file_and_remote_plain_http(tmp_path):
    tool = load_tool()
    config_path = tmp_path / "provider.json"
    write_config(config_path, "https://provider.example")
    config_path.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        tool.load_config(config_path)

    config_path.chmod(0o600)
    payload = json.loads(config_path.read_text())
    payload["base_url"] = "http://provider.example"
    config_path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="HTTPS"):
        tool.load_config(config_path)


def test_config_rejects_symlink_even_when_target_is_owner_only(tmp_path):
    tool = load_tool()
    target = tmp_path / "provider-target.json"
    write_config(target, "https://provider.example")
    link = tmp_path / "provider.json"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        tool.load_config(link)


def test_invalid_or_slow_outputs_cannot_be_recommended(monkeypatch):
    tool = load_tool()
    results = iter(
        [
            tool.StreamResult(200, 3_100.0, 4_000.0, "{}", {}, None),
            tool.StreamResult(500, None, 20.0, "", {}, None),
        ]
    )
    monkeypatch.setattr(tool, "stream_once", lambda **_kwargs: next(results))

    report = tool.run_bakeoff(
        config={
            "base_url": "https://provider.example",
            "api_key": "secret",
            "api_style": "chat_completions",
            "model": "slow",
        },
        models=["slow"],
        repeats=2,
        timeout_seconds=2,
        ttft_slo_ms=3_000,
        total_slo_ms=8_000,
    )

    summary = report["models"][0]
    assert report["verdict"] == "no_go_no_realtime_candidate"
    assert summary["realtime_candidate"] is False
    assert set(summary["blockers"]) == {
        "not_all_calls_succeeded",
        "not_all_outputs_structurally_valid",
        "median_ttft_slo_missed",
    }


def test_partial_stream_followed_by_timeout_is_not_a_success(monkeypatch):
    tool = load_tool()
    content = json.dumps(
        {
            "topic_update": {"title": "topic"},
            "state_changes": [
                {
                    "type": "decision",
                    "content": "decision",
                    "evidence_quote": "quote",
                }
            ],
            "follow_up": None,
        }
    )
    monkeypatch.setattr(
        tool,
        "stream_once",
        lambda **_kwargs: tool.StreamResult(
            200,
            100.0,
            60_000.0,
            content,
            {},
            "TimeoutError",
        ),
    )

    report = tool.run_bakeoff(
        config={
            "base_url": "https://provider.example",
            "api_key": "secret",
            "api_style": "chat_completions",
            "model": "partial",
        },
        models=["partial"],
        repeats=1,
        timeout_seconds=60,
        ttft_slo_ms=3_000,
        total_slo_ms=8_000,
    )

    assert report["models"][0]["successful_count"] == 0
    assert "not_all_calls_succeeded" in report["models"][0]["blockers"]

"""Tests for post-meeting minutes (P1-3)."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository


def _minutes_response():
    return {
        "choices": [{"message": {"content": json.dumps({
            "background": "支付服务灰度发布评审",
            "decisions": ["灰度 5% 起步"],
            "action_items": [{"item": "补兼容性测试用例", "owner": "张三", "deadline": "下周三"}],
            "risks": ["rollback 负责人未确认"],
            "open_questions": ["P99 阈值多少"],
            "evidence_quotes": ["先灰度 5%", "谁负责回滚"],
        }, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 150, "completion_tokens": 60, "total_tokens": 210},
    }


def test_build_minutes_to_markdown_with_evidence():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    md, usage, degraded = llm_service.build_minutes("转写", config, client=FakeClient())
    assert degraded is False
    assert "# 会议纪要" in md
    assert "## 已确认决策" in md
    assert "灰度 5% 起步" in md
    assert "## 行动项" in md and "张三" in md and "下周三" in md
    assert "## 风险" in md and "rollback 负责人未确认" in md
    assert "## 未闭环问题" in md and "P99 阈值多少" in md
    assert "## 证据片段" in md and "先灰度 5%" in md
    assert usage["total_tokens"] == 210


def test_build_minutes_artifact_keeps_structured_data_and_markdown_in_one_call():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    markdown, structured, usage, degraded = llm_service.build_minutes_artifact(
        "转写",
        config,
        client=FakeClient(),
    )

    assert degraded is False
    assert structured["decisions"] == ["灰度 5% 起步"]
    assert structured["action_items"][0]["owner"] == "张三"
    assert "灰度 5% 起步" in markdown
    assert usage["total_tokens"] == 210


def test_build_minutes_artifact_degrades_for_valid_json_with_invalid_schema(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *args: None)
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def __init__(self, content):
            self.content = content

        def post_json(self, url, headers, body, timeout):
            return {
                "choices": [{"message": {"content": json.dumps(self.content)}}],
                "usage": {},
            }

    for content in (
        [],
        {},
        {"background": "背景", "decisions": "不是数组"},
        {"background": "背景", "action_items": ["不是对象"]},
    ):
        markdown, structured, _usage, degraded = llm_service.build_minutes_artifact(
            "转写",
            config,
            client=FakeClient(content),
        )
        assert degraded is True
        assert markdown == ""
        assert structured == {}


def test_build_minutes_artifact_normalizes_multiline_fields_before_markdown_projection():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    response = _minutes_response()
    payload = json.loads(response["choices"][0]["message"]["content"])
    payload["decisions"] = ["确认发布\n## 伪造章节\n[外链](https://example.test)"]
    response["choices"][0]["message"]["content"] = json.dumps(payload, ensure_ascii=False)

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return response

    markdown, structured, _usage, degraded = llm_service.build_minutes_artifact(
        "转写",
        config,
        client=FakeClient(),
    )

    assert degraded is False
    assert structured["decisions"] == ["确认发布 ## 伪造章节 [外链](https://example.test)"]
    assert "\n## 伪造章节\n" not in markdown
    assert r"\#\# 伪造章节 \[外链\](https://example.test)" in markdown


def test_minutes_endpoint_returns_markdown(monkeypatch):
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_test", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    r = client.post("/live/asr/demo/sessions/minutes_test/minutes", json={"mode": "enabled"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is False
    assert "# 会议纪要" in body["minutes_md"]
    assert "证据片段" in body["minutes_md"]
    assert body["minutes"]["decisions"] == ["灰度 5% 起步"]
    assert body["minutes"]["action_items"][0]["owner"] == "张三"
    assert body["llm_usage"]["total_tokens"] == 210


def test_minutes_endpoint_persists_markdown_for_download(monkeypatch):
    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return _minutes_response()

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FakeClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_download", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。谁负责回滚？", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })

    created = client.post("/live/asr/demo/sessions/minutes_download/minutes", json={"mode": "enabled"})
    downloaded = client.get("/live/asr/sessions/minutes_download/minutes.md")
    history = client.get("/live/asr/sessions?include_demo=true")

    assert created.status_code == 200
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith("text/markdown")
    assert "# 会议纪要" in downloaded.text
    assert "先灰度 5%" in downloaded.text
    indexed = {item["session_id"]: item for item in history.json()["sessions"]}
    assert indexed["minutes_download"]["has_minutes"] is True


def test_minutes_endpoint_degrades_when_llm_fails(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            raise RuntimeError("502")

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FailingClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app())
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_deg", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "x", "start_ms": 0, "end_ms": 100, "received_at_ms": 110, "confidence": 0.9}]
    })
    r = client.post("/live/asr/demo/sessions/minutes_deg/minutes", json={"mode": "enabled"})
    assert r.status_code == 200  # degraded, not 500
    assert r.json()["degraded"] is True
    assert r.json()["error_code"] == "llm_minutes_generation_failed"
    assert "可解析纪要" in r.json()["message"]


def test_minutes_persistence_keeps_event_appended_while_llm_is_blocked(monkeypatch, tmp_path):
    provider_entered = Event()
    release_provider = Event()

    class BlockingClient:
        def post_json(self, url, headers, body, timeout):
            provider_entered.set()
            assert release_provider.wait(timeout=3)
            return _minutes_response()

    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: BlockingClient())
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")
    client = TestClient(create_app(data_dir=tmp_path))
    client.post("/live/asr/mock/sessions", json={
        "session_id": "minutes_concurrent_event", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post,
            "/live/asr/demo/sessions/minutes_concurrent_event/minutes",
            json={"mode": "enabled"},
        )
        assert provider_entered.wait(timeout=2)
        SqliteAsrLiveSessionRepository(tmp_path).update(
            "minutes_concurrent_event",
            lambda latest: {**latest, "events": [*latest["events"], {"id": "concurrent_event", "event_type": "state_event", "at_ms": 9_999, "payload": {}}]},
        )
        release_provider.set()
        assert future.result(timeout=5).status_code == 200

    persisted = SqliteAsrLiveSessionRepository(tmp_path).get("minutes_concurrent_event")
    assert any(event.get("id") == "concurrent_event" for event in persisted["events"])

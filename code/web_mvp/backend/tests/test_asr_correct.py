"""Tests for L2 LLM correction (asr_correct)."""
import pytest
import asyncio

from meeting_copilot_web_mvp import asr_correct, llm_service


def test_correct_transcript_fixes_misrecognitions():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post_json(self, url, headers, body, timeout):
            self.calls.append({"url": url, "headers": headers, "body": body, "timeout": timeout})
            return {"choices": [{"message": {"content": "这次讨论 P99 延迟和 checkout-service 的灰度。"}}], "usage": {"total_tokens": 50}}

    client = FakeClient()
    corrected, usage, degraded = asr_correct.correct_transcript(
        "这次讨论 t九九 延迟和 payment gate 的灰度。", config, client=client
    )
    assert degraded is False
    assert "P99" in corrected
    assert "checkout-service" in corrected
    assert usage["total_tokens"] == 50
    assert client.calls[0]["body"]["reasoning_effort"] == "low"
    assert client.calls[0]["body"]["max_completion_tokens"] == 4096


def test_correct_transcript_uses_deepseek_v4_compatibility_fields():
    config = llm_service.LlmConfig(
        base_url="https://api.deepseek.com",
        api_key="sk-test-deepseek",
        model="deepseek-v4-flash",
    )

    class FakeClient:
        def __init__(self):
            self.calls = []

        def post_json(self, url, headers, body, timeout):
            self.calls.append(body)
            return {
                "choices": [{"message": {"content": "修正后的文本"}}],
                "usage": {"total_tokens": 3},
            }

    client = FakeClient()
    corrected, _usage, degraded = asr_correct.correct_transcript(
        "原始转写 t九九", config, client=client
    )

    assert degraded is False
    assert corrected == "修正后的文本"
    assert client.calls[0]["thinking"] == {"type": "disabled"}
    assert client.calls[0]["max_tokens"] == 4096
    assert "max_completion_tokens" not in client.calls[0]


def test_correct_transcript_degrades_to_raw_on_failure(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            raise RuntimeError("502 Bad Gateway")

    raw = "原始转写 t九九"
    corrected, _usage, degraded = asr_correct.correct_transcript(raw, config, client=FailingClient())
    assert degraded is True
    assert corrected == raw  # degrade to raw on failure


def test_correct_transcript_strict_mode_raises_provider_failure(monkeypatch):
    monkeypatch.setattr(llm_service.time, "sleep", lambda *a: None)
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FailingClient:
        def post_json(self, url, headers, body, timeout):
            raise RuntimeError("502 Bad Gateway")

    with pytest.raises(RuntimeError, match="502 Bad Gateway"):
        asr_correct.correct_transcript(
            "原始转写 t九九",
            config,
            client=FailingClient(),
            raise_on_failure=True,
        )


def test_correct_transcript_strict_mode_raises_response_parse_failure():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class MalformedClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": []}

    with pytest.raises(IndexError):
        asr_correct.correct_transcript(
            "原始转写 t九九",
            config,
            client=MalformedClient(),
            raise_on_failure=True,
        )


def test_correct_transcript_empty_input():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    corrected, usage, degraded = asr_correct.correct_transcript("", config)
    assert corrected == ""
    assert degraded is False


@pytest.mark.parametrize("async_call", [False, True])
def test_correction_uses_one_call_with_bounded_readonly_context(async_call):
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-test", model="m1")
    calls = []
    raw = "<<<MC_SEGMENT:0001:target>>>\n待修正文。\n<<<MC_END:0001>>>"

    def response(body):
        calls.append(body)
        return {"choices": [{"message": {"content": raw}}], "usage": {"total_tokens": 3}}

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return response(body)

    class AsyncFakeClient:
        async def post_json(self, url, headers, body, timeout, *, abort=None):
            return response(body)

    if async_call:
        result = asyncio.run(asr_correct.async_correct_transcript(
            raw, config, client=AsyncFakeClient(), read_only_context="只读前文" * 300,
        ))
    else:
        result = asr_correct.correct_transcript(
            raw, config, client=FakeClient(), read_only_context="只读前文" * 300,
        )
    assert result[0] == raw
    assert len(calls) == 1
    messages = calls[0]["messages"]
    assert len(messages) == 3
    assert "MC_READONLY_CONTEXT" in messages[1]["content"]
    assert len(messages[1]["content"]) < 660
    assert messages[2]["content"] == raw
    assert "不预设会议属于技术场景" in messages[0]["content"]
    assert "不得把未完结的话补成事实或承诺" in messages[0]["content"]

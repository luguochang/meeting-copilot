"""Tests for L2 LLM correction (asr_correct)."""
from meeting_copilot_web_mvp import asr_correct, llm_service


def test_correct_transcript_fixes_misrecognitions():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            return {"choices": [{"message": {"content": "这次讨论 P99 延迟和 checkout-service 的灰度。"}}], "usage": {"total_tokens": 50}}

    corrected, usage, degraded = asr_correct.correct_transcript(
        "这次讨论 t九九 延迟和 payment gate 的灰度。", config, client=FakeClient()
    )
    assert degraded is False
    assert "P99" in corrected
    assert "checkout-service" in corrected
    assert usage["total_tokens"] == 50


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


def test_correct_transcript_empty_input():
    config = llm_service.LlmConfig(base_url="https://gw.example", api_key="sk-x", model="m1")
    corrected, usage, degraded = asr_correct.correct_transcript("", config)
    assert corrected == ""
    assert degraded is False

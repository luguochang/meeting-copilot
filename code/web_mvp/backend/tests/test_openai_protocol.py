from meeting_copilot_web_mvp.openai_protocol import (
    chat_body_to_responses,
    responses_payload_to_chat,
)


def test_chat_body_converts_to_bounded_responses_shape():
    body = chat_body_to_responses(
        {
            "model": "gpt-5.5",
            "messages": [
                {"role": "system", "content": "只修正术语"},
                {"role": "user", "content": "P 九九超过阈值"},
            ],
            "temperature": 0,
            "reasoning_effort": "low",
            "max_completion_tokens": 128,
            "stream": True,
            "stream_options": {"include_usage": True},
        },
        stream=False,
    )

    assert body == {
        "model": "gpt-5.5",
        "input": [{"role": "user", "content": "P 九九超过阈值"}],
        "instructions": "只修正术语",
        "store": False,
        "stream": False,
        "max_output_tokens": 128,
        "reasoning": {"effort": "low"},
    }
    assert "temperature" not in body
    assert "stream_options" not in body


def test_responses_payload_normalizes_to_chat_completion_contract():
    normalized = responses_payload_to_chat(
        {
            "id": "resp_1",
            "model": "gpt-5.5",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "P99 超过阈值"}],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        }
    )

    assert normalized["choices"][0]["message"]["content"] == "P99 超过阈值"
    assert normalized["choices"][0]["finish_reason"] == "stop"
    assert normalized["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
    }

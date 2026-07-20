"""Protocol adapters shared by synchronous and streaming LLM transports."""

from __future__ import annotations

from typing import Any, Mapping


def responses_url_for_chat_url(chat_url: str) -> str:
    suffix = "/v1/chat/completions"
    normalized = str(chat_url or "").rstrip("/")
    if not normalized.endswith(suffix):
        raise ValueError("chat completion URL does not end with /v1/chat/completions")
    return f"{normalized[:-len(suffix)]}/v1/responses"


def chat_body_to_responses(body: Mapping[str, Any], *, stream: bool | None = None) -> dict[str, Any]:
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("chat completion messages must be a non-empty array")

    instructions: list[str] = []
    inputs: list[dict[str, Any]] = []
    for raw_message in messages:
        if not isinstance(raw_message, Mapping):
            raise ValueError("chat completion message must be an object")
        role = str(raw_message.get("role") or "user").strip().lower()
        text = _message_text(raw_message.get("content"))
        if role in {"system", "developer"}:
            if text:
                instructions.append(text)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        inputs.append({"role": role, "content": text})

    if not inputs:
        inputs.append({"role": "user", "content": "请按要求完成任务。"})

    response_body: dict[str, Any] = {
        "model": str(body.get("model") or "").strip(),
        "input": inputs,
        "store": False,
        "stream": bool(body.get("stream")) if stream is None else bool(stream),
    }
    if instructions:
        response_body["instructions"] = "\n\n".join(instructions)

    max_output_tokens = body.get("max_completion_tokens", body.get("max_tokens"))
    if type(max_output_tokens) is int and max_output_tokens > 0:
        response_body["max_output_tokens"] = max_output_tokens

    reasoning_effort = str(body.get("reasoning_effort") or "").strip().lower()
    if reasoning_effort:
        response_body["reasoning"] = {"effort": reasoning_effort}

    return response_body


def responses_payload_to_chat(payload: Mapping[str, Any]) -> dict[str, Any]:
    content = responses_output_text(payload)
    if not content:
        raise ValueError("responses payload contained no assistant text")
    return {
        "id": payload.get("id"),
        "object": "chat.completion",
        "model": payload.get("model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": responses_finish_reason(payload),
            }
        ],
        "usage": responses_usage_to_chat(payload.get("usage")),
    }


def responses_output_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    parts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if block.get("type") not in {"output_text", "text"}:
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def responses_usage_to_chat(raw_usage: Any) -> dict[str, int]:
    if not isinstance(raw_usage, Mapping):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    prompt_tokens = _non_negative_int(raw_usage.get("input_tokens"))
    completion_tokens = _non_negative_int(raw_usage.get("output_tokens"))
    total_tokens = _non_negative_int(raw_usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def responses_finish_reason(payload: Mapping[str, Any]) -> str:
    status = str(payload.get("status") or "").strip().lower()
    return "length" if status == "incomplete" else "stop"


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    parts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def _non_negative_int(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0

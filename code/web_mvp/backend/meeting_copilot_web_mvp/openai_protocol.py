"""Protocol adapters shared by synchronous and streaming LLM transports."""

from __future__ import annotations

import json
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

    tools = _chat_tools_to_responses(body.get("tools"))
    if tools:
        response_body["tools"] = tools
        tool_choice = _chat_tool_choice_to_responses(body.get("tool_choice"))
        if tool_choice is not None:
            response_body["tool_choice"] = tool_choice

    return response_body


def responses_payload_to_chat(payload: Mapping[str, Any]) -> dict[str, Any]:
    content = responses_output_text(payload)
    tool_calls = responses_tool_calls(payload)
    if not content and not tool_calls:
        raise ValueError("responses payload contained no assistant output")
    message: dict[str, Any] = {
        "role": "assistant",
        "content": content or None,
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": payload.get("id"),
        "object": "chat.completion",
        "model": payload.get("model"),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": (
                    "tool_calls" if tool_calls else responses_finish_reason(payload)
                ),
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


def responses_tool_calls(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize completed Responses function calls to Chat Completions shape."""

    output = payload.get("output")
    if not isinstance(output, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for index, item in enumerate(output):
        if not isinstance(item, Mapping) or item.get("type") != "function_call":
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("responses function call omitted its name")
        raw_arguments = item.get("arguments")
        if isinstance(raw_arguments, str):
            arguments = raw_arguments
        elif isinstance(raw_arguments, Mapping):
            arguments = json.dumps(
                dict(raw_arguments),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        else:
            raise ValueError("responses function call omitted its arguments")
        call_id = str(item.get("call_id") or item.get("id") or f"call_{index}")
        tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return tool_calls


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


def _chat_tools_to_responses(raw_tools: Any) -> list[dict[str, Any]]:
    if raw_tools is None:
        return []
    if not isinstance(raw_tools, list):
        raise ValueError("chat completion tools must be an array")
    tools: list[dict[str, Any]] = []
    for raw_tool in raw_tools:
        if not isinstance(raw_tool, Mapping) or raw_tool.get("type") != "function":
            raise ValueError("only function tools can be converted to Responses")
        function = raw_tool.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("chat completion function tool is malformed")
        name = str(function.get("name") or "").strip()
        parameters = function.get("parameters")
        if not name or not isinstance(parameters, Mapping):
            raise ValueError("chat completion function tool is incomplete")
        tool: dict[str, Any] = {
            "type": "function",
            "name": name,
            "parameters": dict(parameters),
        }
        description = function.get("description")
        if isinstance(description, str) and description.strip():
            tool["description"] = description.strip()
        if isinstance(function.get("strict"), bool):
            tool["strict"] = function["strict"]
        tools.append(tool)
    return tools


def _chat_tool_choice_to_responses(raw_choice: Any) -> Any:
    if raw_choice is None:
        return None
    if isinstance(raw_choice, str):
        normalized = raw_choice.strip().lower()
        if normalized in {"auto", "none", "required"}:
            return normalized
        raise ValueError("chat completion tool_choice is unsupported")
    if not isinstance(raw_choice, Mapping) or raw_choice.get("type") != "function":
        raise ValueError("chat completion tool_choice is malformed")
    function = raw_choice.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("chat completion function tool_choice is malformed")
    name = str(function.get("name") or "").strip()
    if not name:
        raise ValueError("chat completion function tool_choice omitted its name")
    return {"type": "function", "name": name}


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

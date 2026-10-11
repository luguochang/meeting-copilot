"""Scripted positive state fixtures; no claim of actual model interpretation."""
from __future__ import annotations

import json


def with_attention(content, packet, *, status="present"):
    """Complete only new-contract fake replies from the actual frozen packet."""
    task = packet.get("state_task") or {}
    if task.get("attention_contract") != "discussion_attention.v1":
        return content
    try:
        body = json.loads(content) if isinstance(content, str) else dict(content)
    except (TypeError, ValueError):
        return content
    if not isinstance(body, dict) or "current_attention" in body:
        return content
    sources = {item["id"]: item for group in ("new_paragraphs", "context_paragraphs", "retrieval_paragraphs")
               for item in packet.get(group, [])}
    observations = [{"evidence_segment_ids": [item["id"]], "evidence_quote": sources[item["id"]]["text"][:180]}
                    for item in task["latest_focus_paragraphs"]]
    relation = "changed" if (body.get("topic_update") or {}).get("operation") == "add" else "same_topic"
    anchor = next((item for item in [body.get("topic_update"), *(body.get("state_changes") or [])]
                   if isinstance(item, dict) and item.get("evidence_segment_ids") and item.get("evidence_quote")
                   and any(item["evidence_quote"] in str(sources.get(ref, {}).get("text") or "")
                           for ref in item["evidence_segment_ids"])), observations[-1])
    body["current_attention"] = {"current_focus": {"observations": observations, "relation": relation,
        "object": None, "setting": None, "purpose": None}, "contribution_opportunity": {
            "status": status, "intent": "freeform" if status == "present" else None,
            "reason": "此正向运输测试已设置一个待核对条件。" if status == "present" else "此测试表示当前没有额外贡献。",
            "evidence_segment_ids": list(anchor["evidence_segment_ids"]) if status == "present" else [],
            "evidence_quote": anchor["evidence_quote"] if status == "present" else "", "opportunity_id": None}}
    return json.dumps(body, ensure_ascii=False) if isinstance(content, str) else body


def wire_packet(body):
    if "messages" in body:
        messages = body["messages"]
        return json.loads(next(message["content"] for message in messages if message["role"] == "user"))
    content = next(message["content"] for message in body["input"] if message.get("role") == "user")
    return json.loads(content if isinstance(content, str) else next(
        part["text"] for part in content if part["type"] == "input_text"))


def state_response(persistence, job, response, *, status="present"):
    metadata = job["request_context"]
    rows = {row["segment_id"]: dict(row) for row in persistence._conn.execute(
        "SELECT * FROM transcript_segments WHERE meeting_id=?", (job["meeting_id"],))}
    packet = {"state_task": {"attention_contract": metadata.get("attention_contract"),
        "latest_focus_paragraphs": [{"id": ref} for ref in metadata.get("attention_focus_segment_ids", [])]},
        "context_paragraphs": [{"id": item["segment_id"],
            "text": str(rows[item["segment_id"]]["normalized_text"] or rows[item["segment_id"]]["text"])}
            for item in metadata["evidence"]]}
    return with_attention(response, packet, status=status)

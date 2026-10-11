"""Stable semantic input identity, excluding clocks and generated advice."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


STATE_INPUT_VERSION = "discussion_state_input.v1"


def state_input_fingerprint(metadata: Mapping[str, Any]) -> str | None:
    """Support old persisted state jobs without adding a database migration."""
    if metadata.get("profile") != "participation" or metadata.get("task_mode") != "discussion_state":
        return None
    topic = metadata.get("topic_id")
    basis, anchor = metadata.get("analysis_basis_revision"), metadata.get("anchor_seq")
    cursor = metadata.get("semantic_from_seq", 0)
    if not isinstance(topic, str) or not topic or type(basis) is not int or basis < 1:
        return None
    if type(anchor) is not int or anchor < 1 or type(cursor) is not int or cursor < 0:
        return None
    binding = metadata.get("input_context") or {}
    evidence = metadata.get("evidence")
    if not isinstance(binding, Mapping) or not isinstance(evidence, list) or not 1 <= len(evidence) <= 16:
        return None
    sources = []
    for item in evidence:
        if not isinstance(item, Mapping):
            return None
        segment, revision, digest = item.get("segment_id"), item.get("revision"), item.get("evidence_hash")
        if not isinstance(segment, str) or not segment or type(revision) is not int or revision < 1 or not isinstance(digest, str) or not digest:
            return None
        sources.append({"segment_id": segment, "revision": revision, "evidence_hash": digest})
    identity = {"version": STATE_INPUT_VERSION, "topic_id": topic, "analysis_basis_revision": basis,
        "semantic_from_seq": cursor, "anchor_seq": anchor, "input_context": dict(binding),
        "evidence": sorted(sources, key=lambda source: (source["segment_id"], source["revision"], source["evidence_hash"]))}
    # Keep every historical fingerprint byte-for-byte compatible. A newly
    # registered state contract is a different semantic input, even if an old
    # four-field attempt failed on exactly these same transcript versions.
    if "attention_contract" in metadata:
        if metadata["attention_contract"] != "discussion_attention.v1":
            return None
        focus_ids = metadata.get("attention_focus_segment_ids")
        if (not isinstance(focus_ids, list) or not 1 <= len(focus_ids) <= 2
                or any(not isinstance(item, str) or item not in {source["segment_id"] for source in sources} for item in focus_ids)
                or len(set(focus_ids)) != len(focus_ids)):
            return None
        identity["attention_contract"] = metadata["attention_contract"]
        identity["attention_focus_segment_ids"] = focus_ids
    try:
        wire = json.dumps(identity, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()

"""One state-only envelope; quotes establish provenance, never scene truth."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from .realtime_intelligence import IntelligenceResponseValidationError, _decode_json_object

STATE_ATTENTION_CONTRACT = "discussion_attention.v1"
_RELATIONS = frozenset({"same_topic", "changed", "interlude", "uncertain", "unknown"})
_INTENTS = frozenset({"ask_question", "compare_options", "formulate_response", "freeform"})
_ATTENTION_FIELDS = frozenset({"envelope", "focus", "observation", "object", "setting", "purpose", "opportunity"})

CURRENT_ATTENTION_OUTPUT_CONTRACT = {
    "current_focus": {
        "observations": [{"evidence_segment_ids": "known frozen paragraph IDs", "evidence_quote": "verbatim quote; 1..2 observations"}],
        "relation": "same_topic|changed|interlude|uncertain|unknown",
        "object": 'null or {"evidence_segment_ids":["input paragraph id"],"evidence_quote":"one contiguous verbatim fragment from that paragraph"}; exactly these two keys; replace example values with the supplied source',
        "setting": 'null or {"evidence_segment_ids":["input paragraph id"],"evidence_quote":"one contiguous verbatim fragment from that paragraph"}; exactly these two keys; replace example values with the supplied source',
        "purpose": 'null or {"evidence_segment_ids":["input paragraph id"],"evidence_quote":"one contiguous verbatim fragment from that paragraph"}; exactly these two keys; replace example values with the supplied source',
    },
    "contribution_opportunity": {
        "status": "present|none|uncertain", "intent": "ask_question|compare_options|formulate_response|freeform|null",
        "reason": "short explanation, <=200 characters; not a fact",
        "evidence_segment_ids": "known frozen paragraph IDs; [] allowed unless present",
        "evidence_quote": "verbatim current need/condition; empty allowed unless present",
        "opportunity_id": "known prior host opportunity ID or null; never invent an ID",
    },
}


def split_state_attention_content(content: str) -> tuple[str, Any, bool]:
    """Best effort only: preserve paid usage before any strict validation."""
    try:
        payload = dict(_decode_json_object(content))
    except (TypeError, ValueError):
        return content, None, False
    if not isinstance(payload, dict) or "current_attention" not in payload:
        return content, None, False
    value = payload.pop("current_attention")
    return json.dumps(payload, ensure_ascii=False), value, True


def _invalid(message: str, rule: str, *, attention_field: str | None = None) -> None:
    error = IntelligenceResponseValidationError("current_attention " + message)
    error.validation_field_group = "current_attention"
    error.validation_rule = rule
    if rule == "invalid_fields" and isinstance(attention_field, str) and attention_field in _ATTENTION_FIELDS:
        error.validation_attention_field = attention_field
    raise error


def _object(value: Any, keys: set[str], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _invalid(field + " has invalid fields", "invalid_fields", attention_field=field)
    return dict(value)


def _text(source: Any) -> str:
    if isinstance(source, Mapping):
        return str(source.get("normalized_text") or source.get("text") or "")
    return str(getattr(source, "text", ""))


def validate_current_attention(value: Any, *, paragraphs_by_id: Mapping[str, Any],
                               metadata: Mapping[str, Any], required: bool = True) -> dict[str, Any] | None:
    if value is None:
        if required:
            _invalid("is required by the state contract", "required")
        return None
    top = _object(value, {"current_focus", "contribution_opportunity"}, "envelope")
    try:
        if len(json.dumps(top, ensure_ascii=False, allow_nan=False)) > 3_000:
            _invalid("exceeds its bounded size", "bounded_size")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, IntelligenceResponseValidationError):
            raise
        _invalid("must contain JSON data", "invalid_json_data")
    frozen_ids = {item.get("segment_id") for item in metadata.get("evidence", []) if isinstance(item, Mapping)}

    def quote(raw: Any, field: str, allow_empty: bool = False) -> dict[str, Any]:
        item = _object(raw, {"evidence_segment_ids", "evidence_quote"}, field)
        ids, text = item["evidence_segment_ids"], item["evidence_quote"]
        if (not isinstance(ids, list) or len(ids) > 4
                or any(not isinstance(ref, str) or not ref or len(ref) > 240 for ref in ids)
                or len(set(ids)) != len(ids)
                or not isinstance(text, str) or len(text) > 300):
            _invalid(field + " requires bounded quote references", "bounded_quote_references")
        if not ids and text == "" and allow_empty:
            return {"evidence_segment_ids": [], "evidence_quote": ""}
        if not ids or not text.strip() or any(ref not in paragraphs_by_id or ref not in frozen_ids for ref in ids):
            _invalid(field + " requires frozen transcript references", "frozen_reference_required")
        if not any(text in _text(paragraphs_by_id[ref]) for ref in ids):
            _invalid(field + " quote is not present in its source", "quote_not_verbatim")
        return {"evidence_segment_ids": list(ids), "evidence_quote": text}

    focus = _object(top["current_focus"], {"observations", "relation", "object", "setting", "purpose"}, "focus")
    observations, relation = focus["observations"], focus["relation"]
    if not isinstance(relation, str) or relation not in _RELATIONS:
        _invalid("focus relation is unsupported", "unsupported_relation")
    if not isinstance(observations, list) or not 1 <= len(observations) <= 2:
        _invalid("requires one or two literal observations", "invalid_observations")
    normalized_observations = [quote(item, "observation") for item in observations]
    if len({json.dumps(item, sort_keys=True) for item in normalized_observations}) != len(observations):
        _invalid("observations must not repeat", "duplicate_observations")
    if metadata.get("attention_contract") == STATE_ATTENTION_CONTRACT:
        latest_ids = metadata.get("attention_focus_segment_ids")
        if (not isinstance(latest_ids, list) or not 1 <= len(latest_ids) <= 2
                or any(not isinstance(ref, str) or ref not in frozen_ids or ref not in paragraphs_by_id for ref in latest_ids)
                or len(set(latest_ids)) != len(latest_ids)):
            _invalid("requires host-frozen current focus references", "host_focus_invalid")
        if not any(item["evidence_quote"] in _text(paragraphs_by_id[ref])
                   for item in normalized_observations for ref in item["evidence_segment_ids"] if ref in latest_ids):
            _invalid("observations do not cover the frozen current focus", "focus_not_current")
    normalized_focus = {"observations": normalized_observations, "relation": relation,
        **{key: None if focus[key] is None else quote(focus[key], key) for key in ("object", "setting", "purpose")}}
    opportunity = _object(top["contribution_opportunity"],
        {"status", "intent", "reason", "evidence_segment_ids", "evidence_quote", "opportunity_id"}, "opportunity")
    status, intent, reason, opportunity_id = (opportunity[key] for key in ("status", "intent", "reason", "opportunity_id"))
    if not isinstance(status, str) or status not in {"present", "none", "uncertain"}:
        _invalid("opportunity status is unsupported", "unsupported_status")
    if (status == "present" and (not isinstance(intent, str) or intent not in _INTENTS)
            or status != "present" and intent is not None):
        _invalid("opportunity intent does not match status", "status_intent_mismatch")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
        _invalid("opportunity reason must be bounded text", "reason_invalid")
    prior = (metadata.get("discussion_context") or {}).get("current_attention") or {}
    expected_id = prior.get("opportunity_id") if isinstance(prior, Mapping) else None
    if opportunity_id is not None and (not isinstance(opportunity_id, str) or not opportunity_id
            or len(opportunity_id) > 240 or opportunity_id != expected_id):
        _invalid("opportunity identity is not known to the host", "unknown_opportunity_id")
    support = quote({key: opportunity[key] for key in ("evidence_segment_ids", "evidence_quote")},
                    "opportunity", allow_empty=status != "present")
    return {"current_focus": normalized_focus, "contribution_opportunity": {
        "status": status, "intent": intent, "reason": reason, **support, "opportunity_id": opportunity_id}}


def parse_current_attention(value: Any, *, request: Any, metadata: Mapping[str, Any],
                            required: bool = True) -> dict[str, Any] | None:
    return validate_current_attention(value, paragraphs_by_id=request.paragraphs_by_id,
                                      metadata=metadata, required=required)

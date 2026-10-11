"""Bounded failure metadata for semantic state jobs; never retain model text."""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


SCHEMA = "v2_discussion_state_failure_output.v1"
CATEGORIES = frozenset({"structural", "truncated", "evidence", "stale", "semantic_safety", "unclassified"})
FIELDS = frozenset({"response", "paragraph_revisions", "topic_update", "state_changes", "follow_up", "current_attention", "unclassified"})
PROJECTION_RULE_MESSAGES = {
    "transcript_reference_missing": "discussion interpretation requires meeting transcript evidence",
    "topic_quote_unverifiable": "discussion interpretation requires a verifiable topic-bound quote",
    "cross_topic_old_question_operation_invalid": "cross-topic old question requires explicit open_question resolve",
    "cross_topic_response_owner_or_evidence_invalid": "cross-topic response requires an existing old question and current evidence",
}
RULES = frozenset({"unknown_reference", "empty_quote", "quote_not_verbatim", "unclassified", *PROJECTION_RULE_MESSAGES,
    "empty_json", "invalid_json", "invalid_fence", "non_object_json", "unsupported_fields", "array_required",
    "object_required", "text_required", "empty_text", "text_too_long", "unsupported_enum", "duplicate_items",
    "paragraph_revisions_forbidden", "required", "invalid_fields", "bounded_size", "invalid_json_data",
    "bounded_quote_references", "frozen_reference_required", "unsupported_relation", "invalid_observations",
    "duplicate_observations", "host_focus_invalid", "focus_not_current", "unsupported_status",
    "status_intent_mismatch", "reason_invalid", "unknown_opportunity_id"})
VALIDATION_PHASES = frozenset({"response_decode", "core_contract", "current_attention"})
ATTENTION_FIELDS = frozenset({"envelope", "focus", "observation", "object", "setting", "purpose", "opportunity"})
FAILURE_STAGES = frozenset({"request_prepare", "provider_call", "provider_result", "response_serialization",
    "formal_context", "host_projection", "result_projection"})
FAILURE_SOURCE = "discussion_state_handler"
USAGE_FIELDS = frozenset({"prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"})
_FIELD_PATTERN = r"(topic_update|state_changes\[[0-9]{1,3}\]|follow_up)"
_HOST_PATTERNS = (
    (re.compile(_FIELD_PATTERN + r" references unknown evidence paragraphs"), "unknown_reference"),
    (re.compile(_FIELD_PATTERN + r"\.evidence_quote must not be empty"), "empty_quote"),
    (re.compile(_FIELD_PATTERN + r"\.evidence_quote is not present in the referenced meeting evidence"), "quote_not_verbatim"),
)
_DECODE_RULES = {
    "intelligence response must be non-empty JSON text": "empty_json",
    "intelligence response is not valid JSON": "invalid_json",
    "malformed fenced JSON response": "invalid_fence",
    "intelligence response must be a JSON object": "non_object_json",
}
_CORE_FIELD_PATTERN = r"(paragraph_revisions|topic_update|state_changes|follow_up)(?:\[[0-9]{1,3}\])?"
_CORE_SUBFIELD_PATTERN = r"(?:\.(?:target_id|expected_revision|corrected_text|change_count|operation|title|summary|type|item_id|content|owner|deadline|status|evidence_segment_ids|evidence_quote|confidence|question|reason|urgency))?"
_CORE_PATTERNS = tuple((re.compile(_CORE_FIELD_PATTERN + _CORE_SUBFIELD_PATTERN + suffix), rule) for suffix, rule in (
    (r" must be an array", "array_required"), (r" must be an object", "object_required"),
    (r" must be text", "text_required"), (r" must not be empty", "empty_text"),
    (r" exceeds [0-9]{1,5} characters", "text_too_long"), (r" is unsupported", "unsupported_enum"),
    (r" contains duplicates", "duplicate_items"),
))


def _core_validation_diagnostic(error: Exception) -> tuple[str, str, str]:
    """Map host validator wording to constants; never retain model-supplied keys."""
    message = str(error)
    if message in _DECODE_RULES:
        return "response", _DECODE_RULES[message], "response_decode"
    if re.fullmatch(r"response contains unsupported fields: .+", message):
        return "response", "unsupported_fields", "core_contract"
    if message == "paragraph_revisions must be empty because transcript correction is handled independently":
        return "paragraph_revisions", "paragraph_revisions_forbidden", "core_contract"
    if message == "state changes must not be duplicated":
        return "state_changes", "duplicate_items", "core_contract"
    for pattern, rule in _CORE_PATTERNS:
        match = pattern.fullmatch(message)
        if match:
            return match.group(1), rule, "core_contract"
    return "unclassified", "unclassified", "core_contract"


class DiscussionProjectionRejection(ValueError):
    """A known host rejection with its original ValueError/retry semantics."""

    durable_error_class = "ValueError"

    def __init__(self, rule: str) -> None:
        if rule not in PROJECTION_RULE_MESSAGES:
            raise ValueError("unsupported discussion projection rule")
        super().__init__(PROJECTION_RULE_MESSAGES[rule])
        self.state_projection_rule = rule


def _bounded_integer(value: Any, maximum: int) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum else None


def sanitize_state_failure_output(value: Any) -> dict[str, Any] | None:
    """Rebuild only the fixed contract, including at the persistence boundary."""
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA or value.get("task_mode") != "discussion_state":
        return None
    raw = value.get("failure_diagnostics")
    if not isinstance(raw, Mapping):
        return None
    category = raw.get("category")
    field = raw.get("field_group")
    rule = raw.get("rule")
    if not all(isinstance(item, str) for item in (category, field, rule)) or category not in CATEGORIES or field not in FIELDS or rule not in RULES:
        return None
    attempts = _bounded_integer(value.get("provider_attempt_count"), 1)
    if attempts is None:
        return None
    counts = raw.get("paragraph_counts")
    if not isinstance(counts, Mapping):
        return None
    safe_counts = {key: _bounded_integer(counts.get(key), 10_000) for key in ("new", "context", "retrieval")}
    if any(count is None for count in safe_counts.values()):
        return None
    usage = value.get("usage")
    safe_usage = {key: count for key in USAGE_FIELDS
        if isinstance(usage, Mapping) and (count := _bounded_integer(usage.get(key), 10_000_000)) is not None}
    diagnostic = {"category": category, "field_group": field, "rule": rule, "paragraph_counts": safe_counts}
    if "failure_stage" in raw or "failure_source" in raw:
        if not isinstance(raw.get("failure_stage"), str) or raw["failure_stage"] not in FAILURE_STAGES or raw.get("failure_source") != FAILURE_SOURCE:
            return None
        diagnostic.update(failure_stage=raw["failure_stage"], failure_source=FAILURE_SOURCE)
    if "validation_phase" in raw:
        phase = raw["validation_phase"]
        if (not isinstance(phase, str) or phase not in VALIDATION_PHASES
                or diagnostic.get("failure_stage") != "provider_result"
                or diagnostic.get("failure_source") != FAILURE_SOURCE):
            return None
        diagnostic["validation_phase"] = phase
    attention_field = raw.get("attention_field")
    if (isinstance(attention_field, str) and attention_field in ATTENTION_FIELDS
            and field == "current_attention" and rule == "invalid_fields"
            and diagnostic.get("failure_stage") == "provider_result"
            and diagnostic.get("validation_phase") == "current_attention"):
        diagnostic["attention_field"] = attention_field
    if category == "unclassified" and "failure_stage" not in diagnostic:
        return None
    if rule in PROJECTION_RULE_MESSAGES and (category != "unclassified" or field != "state_changes"
            or diagnostic.get("failure_stage") != "host_projection"):
        return None
    return {"schema_version": SCHEMA, "task_mode": "discussion_state", "provider_attempt_count": attempts,
        "failure_diagnostics": diagnostic,
        "usage": safe_usage or None}


def state_validation_failure_output(error: Exception, *, provider_attempt_count: int,
    paragraph_counts: Mapping[str, int], usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """Only match anchored, known host-generated messages from the typed error."""
    field, rule = "unclassified", "unclassified"
    # Explicit structured attributes win when a future validator supplies them.
    structured_field = getattr(error, "validation_field_group", None)
    structured_rule = getattr(error, "validation_rule", None)
    if isinstance(structured_field, str) and isinstance(structured_rule, str) and structured_field in FIELDS and structured_rule in RULES:
        field, rule = structured_field, structured_rule
    elif type(error).__name__ == "IntelligenceResponseValidationError":
        for pattern, known_rule in _HOST_PATTERNS:
            match = pattern.fullmatch(str(error))
            if match:
                field, rule = match.group(1).split("[", 1)[0], known_rule
                break
    result = sanitize_state_failure_output({"schema_version": SCHEMA, "task_mode": "discussion_state",
        "provider_attempt_count": provider_attempt_count, "usage": usage,
        "failure_diagnostics": {"category": getattr(error, "category", "structural"),
            "field_group": field, "rule": rule, "paragraph_counts": paragraph_counts}})
    if result is None:
        raise ValueError("invalid bounded state diagnostics")
    return result


def state_failure_output(error: Exception, *, failure_stage: str, provider_attempt_count: int,
    paragraph_counts: Mapping[str, int], usage: Mapping[str, Any] | None,
    validation_phase: str | None = None) -> dict[str, Any]:
    """Unknown exceptions retain unknown cause; only this typed host rule is read."""
    if type(error).__name__ == "IntelligenceResponseValidationError":
        result = state_validation_failure_output(error, provider_attempt_count=provider_attempt_count,
            paragraph_counts=paragraph_counts, usage=usage)
    else:
        known_rule = error.state_projection_rule if isinstance(error, DiscussionProjectionRejection) else "unclassified"
        if not isinstance(known_rule, str) or known_rule not in PROJECTION_RULE_MESSAGES:
            known_rule = "unclassified"
        result = {"schema_version": SCHEMA, "task_mode": "discussion_state", "provider_attempt_count": provider_attempt_count,
            "usage": usage, "failure_diagnostics": {"category": "unclassified",
                "field_group": "state_changes" if known_rule != "unclassified" else "unclassified",
                "rule": known_rule, "paragraph_counts": paragraph_counts}}
    result["failure_diagnostics"].update(failure_stage=failure_stage, failure_source=FAILURE_SOURCE)
    if validation_phase is not None:
        if validation_phase == "core_contract" and type(error).__name__ == "IntelligenceResponseValidationError":
            field, rule, validation_phase = _core_validation_diagnostic(error)
            if result["failure_diagnostics"]["rule"] == "unclassified":
                result["failure_diagnostics"].update(field_group=field, rule=rule)
        result["failure_diagnostics"]["validation_phase"] = validation_phase
    attention_field = getattr(error, "validation_attention_field", None)
    if isinstance(attention_field, str):
        result["failure_diagnostics"]["attention_field"] = attention_field
    sanitized = sanitize_state_failure_output(result)
    if sanitized is None:
        raise ValueError("invalid bounded state diagnostics")
    return sanitized

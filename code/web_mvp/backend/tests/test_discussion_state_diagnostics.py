from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.discussion_state_diagnostics import (
    DiscussionProjectionRejection,
    PROJECTION_RULE_MESSAGES,
    sanitize_state_failure_output,
    state_failure_output,
    state_validation_failure_output,
)
from meeting_copilot_web_mvp.realtime_intelligence import IntelligenceResponseValidationError


def _output(error):
    return state_validation_failure_output(error, provider_attempt_count=1,
        paragraph_counts={"new": 1, "context": 2, "retrieval": 3},
        usage={"prompt_tokens": 1306, "completion_tokens": 814, "total_tokens": 2120, "raw": "private usage"})


@pytest.mark.parametrize("message,field,rule", [
    ("topic_update.evidence_quote is not present in the referenced meeting evidence", "topic_update", "quote_not_verbatim"),
    ("state_changes[12] references unknown evidence paragraphs", "state_changes", "unknown_reference"),
    ("follow_up.evidence_quote must not be empty", "follow_up", "empty_quote"),
    ("secret source text: topic_update.evidence_quote is not present in the referenced meeting evidence", "unclassified", "unclassified"),
    ("state_changes[1].content secret", "unclassified", "unclassified"),
])
def test_only_anchored_known_host_messages_become_safe_enums(message, field, rule):
    result = _output(IntelligenceResponseValidationError(message, category="evidence"))
    assert result["failure_diagnostics"] == {"category": "evidence", "field_group": field, "rule": rule,
        "paragraph_counts": {"new": 1, "context": 2, "retrieval": 3}}
    assert message not in json.dumps(result)
    assert "private usage" not in json.dumps(result)
    assert result["usage"]["total_tokens"] == 2120


def test_structured_known_diagnostics_win_but_unknown_provider_errors_are_not_parsed():
    error = IntelligenceResponseValidationError("private rejected response", category="evidence")
    error.validation_field_group, error.validation_rule = "state_changes", "unknown_reference"
    assert _output(error)["failure_diagnostics"]["field_group"] == "state_changes"
    provider = ValueError("topic_update.evidence_quote must not be empty")
    assert _output(provider)["failure_diagnostics"]["rule"] == "unclassified"


def test_persistence_boundary_rebuilds_only_the_contract_and_rejects_unbounded_values():
    result = _output(IntelligenceResponseValidationError("unknown", category="evidence"))
    contaminated = {**result, "raw_response": "private model", "source_ids": ["private-id"]}
    contaminated["failure_diagnostics"] = {**result["failure_diagnostics"], "quote": "private original"}
    assert sanitize_state_failure_output(contaminated) == result
    for value in [2, -1, True, "1"]:
        assert sanitize_state_failure_output({**result, "provider_attempt_count": value}) is None
    for field in ["private text", ["topic_update"]]:
        assert sanitize_state_failure_output({**result, "failure_diagnostics": {**result["failure_diagnostics"], "field_group": field}}) is None


@pytest.mark.parametrize("rule", list(PROJECTION_RULE_MESSAGES))
def test_known_host_rules_remain_valueerror_compatible_and_only_typed_rules_are_specific(rule):
    error = DiscussionProjectionRejection(rule)
    assert isinstance(error, ValueError) and error.durable_error_class == "ValueError"
    assert str(error) == PROJECTION_RULE_MESSAGES[rule]
    kwargs = {"failure_stage": "host_projection", "provider_attempt_count": 1,
        "paragraph_counts": {"new": 3, "context": 0, "retrieval": 0}, "usage": None}
    result = state_failure_output(error, **kwargs)
    assert result["failure_diagnostics"]["rule"] == rule
    assert result["failure_diagnostics"]["category"] == "unclassified"
    ordinary = ValueError(PROJECTION_RULE_MESSAGES[rule])
    ordinary.state_projection_rule = rule
    assert state_failure_output(ordinary, **kwargs)["failure_diagnostics"]["rule"] == "unclassified"
    assert PROJECTION_RULE_MESSAGES[rule] not in json.dumps(result)


@pytest.mark.parametrize("value", [[], {}, True, "private stage"])
def test_unknown_or_non_scalar_stage_does_not_enter_durable_diagnostics(value):
    result = _output(IntelligenceResponseValidationError("unknown", category="evidence"))
    result["failure_diagnostics"].update(failure_stage=value, failure_source="discussion_state_handler")
    assert sanitize_state_failure_output(result) is None

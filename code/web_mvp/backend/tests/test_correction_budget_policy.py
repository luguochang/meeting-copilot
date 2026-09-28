import pytest

from meeting_copilot_web_mvp.correction_budget_policy import (
    COMPLETION_RATE_ENV,
    PRICING_MODE_ENV,
    PRICING_MODE_UNKNOWN,
    PROMPT_RATE_ENV,
    correction_pricing_decision,
)


def test_missing_rates_keep_correction_enabled_with_unavailable_cost():
    decision = correction_pricing_decision(is_mock=False, environ={})

    assert decision.allowed is True
    assert decision.reason is None
    assert decision.pricing_mode == PRICING_MODE_UNKNOWN
    assert decision.rates is None


@pytest.mark.parametrize(
    "environment",
    [
        {PROMPT_RATE_ENV: "0", COMPLETION_RATE_ENV: "1"},
        {PROMPT_RATE_ENV: "1", COMPLETION_RATE_ENV: "0"},
        {PROMPT_RATE_ENV: "-1", COMPLETION_RATE_ENV: "1"},
        {PROMPT_RATE_ENV: "nan", COMPLETION_RATE_ENV: "1"},
        {PROMPT_RATE_ENV: "1", COMPLETION_RATE_ENV: "inf"},
        {PROMPT_RATE_ENV: "1"},
        {COMPLETION_RATE_ENV: "1"},
    ],
)
def test_explicit_invalid_or_incomplete_rates_fail_closed(environment):
    decision = correction_pricing_decision(is_mock=False, environ=environment)

    assert decision.allowed is False
    assert decision.reason == "correction_provider_rates_not_configured"
    assert decision.pricing_mode == "metered"
    assert decision.rates is None


def test_explicit_unmetered_correction_allows_zero_or_missing_rates():
    decision = correction_pricing_decision(
        is_mock=False,
        environ={
            PRICING_MODE_ENV: "unmetered",
            PROMPT_RATE_ENV: "0",
            COMPLETION_RATE_ENV: "0",
        },
    )

    assert decision.allowed is True
    assert decision.pricing_mode == "unmetered"
    assert decision.rates is None


def test_paid_correction_accepts_positive_finite_rates():
    decision = correction_pricing_decision(
        is_mock=False,
        environ={PROMPT_RATE_ENV: "1.2", COMPLETION_RATE_ENV: "3.4"},
    )

    assert decision.allowed is True
    assert decision.pricing_mode == "metered"
    assert decision.rates == (1.2, 3.4)


def test_mock_correction_does_not_need_paid_rates():
    decision = correction_pricing_decision(is_mock=True, environ={})

    assert decision.allowed is True
    assert decision.pricing_mode == "mock"

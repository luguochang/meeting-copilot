"""Pricing metadata policy for transcript-correction requests.

This module deliberately owns only the correction lane.  Other LLM features
retain their existing accounting behavior while correction has a strict
pre-attempt guard shared by manual and durable execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Mapping


PROMPT_RATE_ENV = "LLM_PROMPT_CNY_PER_1M_TOKENS"
COMPLETION_RATE_ENV = "LLM_COMPLETION_CNY_PER_1M_TOKENS"
PRICING_MODE_ENV = "LLM_CORRECTION_PRICING_MODE"
PRICING_MODE_METERED = "metered"
PRICING_MODE_UNMETERED = "unmetered"
PRICING_MODE_UNKNOWN = "unknown"
RATES_NOT_CONFIGURED = "correction_provider_rates_not_configured"


@dataclass(frozen=True)
class CorrectionPricingDecision:
    """Safe, secret-free result of the correction pre-attempt guard."""

    allowed: bool
    reason: str | None
    pricing_mode: str
    rates: tuple[float, float] | None


def _positive_finite_rate(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        rate = float(value.strip())
    except (AttributeError, ValueError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def correction_pricing_decision(
    *,
    is_mock: bool,
    environ: Mapping[str, str] | None = None,
) -> CorrectionPricingDecision:
    """Return whether one correction Provider request may start.

    Many OpenAI-compatible gateways do not publish model-specific rates.
    When both rate variables are absent, cost estimation is unavailable but an
    explicitly enabled correction feature remains usable. Once either rate is
    configured, both values must be positive and finite; explicit zero or an
    incomplete/invalid pair fails closed unless the gateway is declared
    ``unmetered``.
    """

    if is_mock:
        return CorrectionPricingDecision(True, None, "mock", None)

    source = os.environ if environ is None else environ
    raw_mode = str(source.get(PRICING_MODE_ENV) or "").strip().lower()
    if raw_mode == PRICING_MODE_UNMETERED:
        return CorrectionPricingDecision(True, None, PRICING_MODE_UNMETERED, None)

    raw_prompt_rate = source.get(PROMPT_RATE_ENV)
    raw_completion_rate = source.get(COMPLETION_RATE_ENV)
    if raw_prompt_rate is None and raw_completion_rate is None:
        return CorrectionPricingDecision(
            True,
            None,
            PRICING_MODE_UNKNOWN,
            None,
        )

    prompt_rate = _positive_finite_rate(raw_prompt_rate)
    completion_rate = _positive_finite_rate(raw_completion_rate)
    if prompt_rate is None or completion_rate is None:
        return CorrectionPricingDecision(
            False,
            RATES_NOT_CONFIGURED,
            PRICING_MODE_METERED,
            None,
        )
    return CorrectionPricingDecision(
        True,
        None,
        PRICING_MODE_METERED,
        (prompt_rate, completion_rate),
    )

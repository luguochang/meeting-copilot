"""Fail-closed pricing policy for paid transcript-correction requests.

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

    Paid correction requests require two strictly positive finite rates.  A
    gateway is allowed to be explicitly declared ``unmetered`` but a zero rate
    is never treated as that declaration.  The result contains no Provider
    endpoint or credential material so it is safe to expose through status
    plumbing later.
    """

    if is_mock:
        return CorrectionPricingDecision(True, None, "mock", None)

    source = os.environ if environ is None else environ
    raw_mode = str(source.get(PRICING_MODE_ENV) or "").strip().lower()
    if raw_mode == PRICING_MODE_UNMETERED:
        return CorrectionPricingDecision(True, None, PRICING_MODE_UNMETERED, None)

    prompt_rate = _positive_finite_rate(source.get(PROMPT_RATE_ENV))
    completion_rate = _positive_finite_rate(source.get(COMPLETION_RATE_ENV))
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

from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class EntityAccuracy:
    precision: float
    recall: float
    f1: float
    missing: list[str]
    extra: list[str]


@dataclass(frozen=True)
class LatencySummary:
    count: int
    p50_ms: int
    p95_ms: int
    max_ms: int


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Compute character error rate using Levenshtein distance."""
    reference = normalize_transcript_text(reference)
    hypothesis = normalize_transcript_text(hypothesis)
    if not reference:
        return 0.0 if not hypothesis else 1.0
    distance = _levenshtein(list(reference), list(hypothesis))
    return distance / len(reference)


def normalize_transcript_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def entity_accuracy(reference_entities: list[str], hypothesis_entities: list[str]) -> EntityAccuracy:
    reference = set(reference_entities)
    hypothesis = set(hypothesis_entities)
    matched = reference & hypothesis
    missing = sorted(reference - hypothesis)
    extra = sorted(hypothesis - reference)
    precision = len(matched) / len(hypothesis) if hypothesis else (1.0 if not reference else 0.0)
    recall = len(matched) / len(reference) if reference else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return EntityAccuracy(
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        missing=missing,
        extra=extra,
    )


def latency_summary(latencies_ms: list[int]) -> LatencySummary:
    if not latencies_ms:
        return LatencySummary(count=0, p50_ms=0, p95_ms=0, max_ms=0)
    values = sorted(latencies_ms)
    return LatencySummary(
        count=len(values),
        p50_ms=_percentile_nearest_rank(values, 50),
        p95_ms=_percentile_nearest_rank(values, 95),
        max_ms=max(values),
    )


def _levenshtein(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for i, ref_item in enumerate(reference, start=1):
        current = [i]
        for j, hyp_item in enumerate(hypothesis, start=1):
            substitution_cost = 0 if ref_item == hyp_item else 1
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + substitution_cost,
                )
            )
        previous = current
    return previous[-1]


def _percentile_nearest_rank(sorted_values: list[int], percentile: int) -> int:
    rank = ceil(percentile / 100 * len(sorted_values))
    return sorted_values[max(0, min(rank - 1, len(sorted_values) - 1))]

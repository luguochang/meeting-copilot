#!/usr/bin/env python3
"""Build a Chinese technical-meeting ASR mainline quality report.

The report separates two questions that are easy to conflate:
1. Did ASR output close into the Meeting Copilot mainline
   (EvidenceSpan -> state -> suggestion candidate) without paid calls?
2. Was the recognized Chinese technical content good enough to trust?
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "asr_mainline_quality_report.v1"
MIN_TERM_RECALL = 0.75
MAX_CHAR_ERROR_RATE = 0.45
TERM_ALIASES = {
    "checkout-service": ["checkout-service", "checkoutservice", "check outservice", "check out service"],
    "error_rate": ["error_rate", "errorrate", "error r ate", "error r rate", "error rate"],
    "错误率": ["error_rate", "errorrate", "error-rate", "错误率"],
    "P99": ["p99", "p九九", "p九"],
    "800ms": ["800ms", "800毫秒", "八百毫秒"],
}


class ProviderInput:
    def __init__(self, provider: str, transcript_report_path: Path, pipeline_replay_report_path: Path) -> None:
        self.provider = provider
        self.transcript_report_path = transcript_report_path
        self.pipeline_replay_report_path = pipeline_replay_report_path


def build_asr_mainline_quality_report(
    *,
    sample_id: str,
    reference_path: Path,
    annotation_path: Path,
    provider_reports: list[dict[str, Any] | ProviderInput],
    output_path: Path,
) -> dict[str, Any]:
    reference_text = reference_path.read_text(encoding="utf-8").strip()
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    expected_terms = _expected_terms(annotation)
    providers = [
        _provider_report(
            provider_input=_coerce_provider_input(item),
            reference_text=reference_text,
            expected_terms=expected_terms,
        )
        for item in provider_reports
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "sample_id": sample_id,
        "reference_path": str(reference_path),
        "annotation_path": str(annotation_path),
        "quality_gate": {
            "min_term_recall": MIN_TERM_RECALL,
            "max_char_error_rate": MAX_CHAR_ERROR_RATE,
        },
        "privacy_cost_flags": _privacy_cost_flags(providers),
        "summary": _summary(providers),
        "default_decision": _default_decision(providers),
        "providers": providers,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _coerce_provider_input(item: dict[str, Any] | ProviderInput) -> ProviderInput:
    if isinstance(item, ProviderInput):
        return item
    return ProviderInput(
        provider=str(item["provider"]),
        transcript_report_path=Path(item["transcript_report_path"]),
        pipeline_replay_report_path=Path(item["pipeline_replay_report_path"]),
    )


def _provider_report(
    *,
    provider_input: ProviderInput,
    reference_text: str,
    expected_terms: list[str],
) -> dict[str, Any]:
    transcript = json.loads(provider_input.transcript_report_path.read_text(encoding="utf-8"))
    replay = json.loads(provider_input.pipeline_replay_report_path.read_text(encoding="utf-8"))
    hypothesis = str(transcript.get("normalized_text") or transcript.get("text") or "").strip()
    quality = _quality(reference_text=reference_text, hypothesis=hypothesis, expected_terms=expected_terms)
    pipeline = _pipeline(replay)
    return {
        "provider": provider_input.provider,
        "transcript_report_path": str(provider_input.transcript_report_path),
        "pipeline_replay_report_path": str(provider_input.pipeline_replay_report_path),
        "transcript": {
            "provider": str(transcript.get("provider") or provider_input.provider),
            "duration_seconds": _optional_float(transcript.get("duration_seconds")),
            "latency_ms": _optional_int(transcript.get("latency_ms")),
            "rtf": _optional_float(transcript.get("rtf")),
            "segment_count": len(list(transcript.get("segments") or [])),
            "text": str(transcript.get("text") or ""),
            "normalized_text": hypothesis,
        },
        "quality": quality,
        "pipeline": pipeline,
        "mainline_status": _provider_mainline_status(quality=quality, pipeline=pipeline),
    }


def _quality(*, reference_text: str, hypothesis: str, expected_terms: list[str]) -> dict[str, Any]:
    matched_terms, missing_terms = _term_matches(expected_terms, hypothesis)
    char_error_rate = _char_error_rate(reference_text, hypothesis)
    term_recall = round(len(matched_terms) / len(expected_terms), 6) if expected_terms else 1.0
    return {
        "char_error_rate": char_error_rate,
        "term_recall": term_recall,
        "matched_terms": matched_terms,
        "missing_terms": missing_terms,
        "contains_unk": "<unk>" in hypothesis.lower(),
        "passed_minimum_quality_gate": (
            term_recall >= MIN_TERM_RECALL
            and char_error_rate <= MAX_CHAR_ERROR_RATE
            and "<unk>" not in hypothesis.lower()
        ),
    }


def _pipeline(replay: dict[str, Any]) -> dict[str, Any]:
    statuses = [str(item) for item in list(replay.get("all_llm_statuses") or [])]
    closed = str(replay.get("short_local_simulated_input_status") or "") == "closed_to_candidate_timeline"
    return {
        "replay_status": str(replay.get("replay_status") or ""),
        "closed_to_candidate_timeline": closed,
        "evidence_span_count": _optional_int(replay.get("evidence_span_count")) or 0,
        "state_event_count": _optional_int(replay.get("state_event_count")) or 0,
        "suggestion_candidate_count": _optional_int(replay.get("suggestion_candidate_count")) or 0,
        "llm_request_draft_count": _optional_int(replay.get("llm_request_draft_count")) or 0,
        "all_llm_statuses": statuses,
        "llm_called": any(status not in {"", "not_called"} for status in statuses),
        "remote_asr_called": bool(replay.get("safe_to_call_remote_asr_now")),
        "asr_metrics": dict(replay.get("asr_metrics") or {}),
        "safe_to_call_llm_now": bool(replay.get("safe_to_call_llm_now")),
        "safe_to_call_remote_asr_now": bool(replay.get("safe_to_call_remote_asr_now")),
    }


def _provider_mainline_status(*, quality: dict[str, Any], pipeline: dict[str, Any]) -> str:
    if not pipeline["closed_to_candidate_timeline"]:
        return "blocked_pipeline_not_closed"
    if not quality["passed_minimum_quality_gate"]:
        return "pipeline_closed_quality_insufficient"
    return "pipeline_closed_quality_candidate"


def _summary(providers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "provider_count": len(providers),
        "pipeline_closed_count": sum(1 for provider in providers if provider["pipeline"]["closed_to_candidate_timeline"]),
        "quality_pass_count": sum(1 for provider in providers if provider["quality"]["passed_minimum_quality_gate"]),
        "remote_asr_call_count": sum(1 for provider in providers if provider["pipeline"]["remote_asr_called"]),
        "llm_call_count": sum(1 for provider in providers if provider["pipeline"]["llm_called"]),
    }


def _default_decision(providers: list[dict[str, Any]]) -> dict[str, Any]:
    pipeline_closed = [provider for provider in providers if provider["pipeline"]["closed_to_candidate_timeline"]]
    quality_passed = [provider for provider in pipeline_closed if provider["quality"]["passed_minimum_quality_gate"]]
    blockers: list[str] = []
    if not pipeline_closed:
        blockers.append("no_provider_closed_mainline_pipeline")
    if pipeline_closed and not quality_passed:
        blockers.append("pipeline_closed_but_asr_quality_insufficient")
    if any(provider["pipeline"]["llm_called"] for provider in providers):
        blockers.append("unexpected_llm_call")
    if any(provider["pipeline"]["remote_asr_called"] for provider in providers):
        blockers.append("unexpected_remote_asr_call")
    if blockers:
        status = "no_go_quality_not_production"
    else:
        status = "candidate_for_next_real_audio_gate"
    return {
        "decision_status": status,
        "recommended_next_gate": (
            "real_or_public_audio_wall_clock_soak"
            if status == "candidate_for_next_real_audio_gate"
            else "improve_asr_terms_segmentation_or_provider_before_real_gate"
        ),
        "blockers": blockers,
        "best_quality_provider": _best_quality_provider(providers),
    }


def _best_quality_provider(providers: list[dict[str, Any]]) -> str | None:
    if not providers:
        return None
    ranked = sorted(
        providers,
        key=lambda provider: (
            provider["quality"]["passed_minimum_quality_gate"],
            provider["quality"]["term_recall"],
            -provider["quality"]["char_error_rate"],
        ),
        reverse=True,
    )
    return str(ranked[0]["provider"])


def _privacy_cost_flags(providers: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "remote_asr_called": any(provider["pipeline"]["remote_asr_called"] for provider in providers),
        "llm_called": any(provider["pipeline"]["llm_called"] for provider in providers),
        "raw_audio_uploaded": False,
        "user_audio_committed_to_repo": False,
    }


def _expected_terms(annotation: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for item in list(annotation.get("technical_entities") or []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("normalized") or item.get("text") or "").strip()
        if value and value not in terms:
            terms.append(value)
    return terms


def _term_matches(expected_terms: list[str], hypothesis: str) -> tuple[list[str], list[str]]:
    normalized_hypothesis = _normalize_for_match(hypothesis)
    matched: list[str] = []
    missing: list[str] = []
    for term in expected_terms:
        variants = TERM_ALIASES.get(term, [term])
        if any(_normalize_for_match(variant) in normalized_hypothesis for variant in variants):
            matched.append(term)
        else:
            missing.append(term)
    return sorted(matched), sorted(missing)


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())


def _normalize_for_cer(value: str) -> str:
    text = _normalize_for_match(value)
    return re.sub(r"[，。！？、,.!?%：:；;（）()\[\]{}\"'`~\-_/\\]", "", text)


def _char_error_rate(reference: str, hypothesis: str) -> float:
    reference_chars = list(_normalize_for_cer(reference))
    hypothesis_chars = list(_normalize_for_cer(hypothesis))
    if not reference_chars:
        return 0.0 if not hypothesis_chars else 1.0
    return round(_levenshtein(reference_chars, hypothesis_chars) / len(reference_chars), 6)


def _levenshtein(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for index, ref_item in enumerate(reference, start=1):
        current = [index]
        for hyp_index, hyp_item in enumerate(hypothesis, start=1):
            substitution = 0 if ref_item == hyp_item else 1
            current.append(
                min(
                    previous[hyp_index] + 1,
                    current[hyp_index - 1] + 1,
                    previous[hyp_index - 1] + substitution,
                )
            )
        previous = current
    return previous[-1]


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _provider_inputs_from_args(args: argparse.Namespace) -> list[ProviderInput]:
    if len(args.provider) != len(args.transcript_report) or len(args.provider) != len(args.pipeline_replay_report):
        raise SystemExit("--provider, --transcript-report and --pipeline-replay-report counts must match")
    return [
        ProviderInput(provider=provider, transcript_report_path=transcript, pipeline_replay_report_path=replay)
        for provider, transcript, replay in zip(args.provider, args.transcript_report, args.pipeline_replay_report)
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--annotation", required=True, type=Path)
    parser.add_argument("--provider", action="append", required=True)
    parser.add_argument("--transcript-report", action="append", required=True, type=Path)
    parser.add_argument("--pipeline-replay-report", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_asr_mainline_quality_report(
        sample_id=args.sample_id,
        reference_path=args.reference,
        annotation_path=args.annotation,
        provider_reports=_provider_inputs_from_args(args),
        output_path=args.output,
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

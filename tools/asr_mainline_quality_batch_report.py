#!/usr/bin/env python3
"""Build a batch quality gate for Chinese technical-meeting ASR mainline output.

This wrapper keeps single-sample scoring in asr_mainline_quality_report.py and
adds the release-style aggregation needed for provider bake-offs:

1. discover reference/annotation samples,
2. replay approved local ASR event JSON into the live Meeting Copilot pipeline,
3. score each provider against the sample reference and technical terms,
4. summarize whether the batch is ready for real-audio gates.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import asr_live_pipeline_replay as live_replay  # noqa: E402
from asr_mainline_quality_report import build_asr_mainline_quality_report  # noqa: E402


SCHEMA_VERSION = "asr_mainline_quality_batch_report.v1"
DEFAULT_PROVIDER_SPECS = (
    ("funasr_streaming", "funasr"),
    ("sherpa_onnx_streaming", "sherpa"),
)
COVERAGE_OK_THRESHOLD = 0.9
COVERAGE_MIN_SIGNAL_THRESHOLD = 0.35
COVERAGE_MIN_BLOCK_SIZE = 4


class ProviderSpec:
    def __init__(
        self,
        provider: str,
        file_slug: str,
        *,
        events_path: Path | None = None,
        transcript_report_path: Path | None = None,
    ) -> None:
        self.provider = provider
        self.file_slug = file_slug
        self.events_path = events_path
        self.transcript_report_path = transcript_report_path


class SampleSpec:
    def __init__(
        self,
        *,
        sample_id: str,
        sample_key: str,
        session_id: str,
        reference_path: Path,
        annotation_path: Path,
        provider_specs: list[ProviderSpec] | None = None,
    ) -> None:
        self.sample_id = sample_id
        self.sample_key = sample_key
        self.session_id = session_id
        self.reference_path = reference_path
        self.annotation_path = annotation_path
        self.provider_specs = provider_specs


def run_asr_mainline_quality_batch_report(
    *,
    repo_root: Path = REPO_ROOT,
    references_root: Path | None = None,
    annotations_root: Path | None = None,
    events_root: Path | None = None,
    reports_root: Path | None = None,
    matrix_path: Path | None = None,
    output_path: Path,
    replay_run_id: str | None = None,
    provider_specs: list[ProviderSpec] | None = None,
    sample_ids: list[str] | None = None,
) -> dict[str, Any]:
    repo_root = repo_root.resolve(strict=False)
    live_replay.REPO_ROOT = repo_root
    references_root = _resolve_root(repo_root, references_root, "data/asr_eval/references")
    annotations_root = _resolve_root(repo_root, annotations_root, "data/asr_eval/annotations")
    events_root = _resolve_root(repo_root, events_root, "artifacts/tmp/asr_events")
    reports_root = _resolve_root(repo_root, reports_root, "artifacts/tmp/asr_reports")
    replay_run_id = replay_run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    provider_specs = provider_specs or [ProviderSpec(provider, slug) for provider, slug in DEFAULT_PROVIDER_SPECS]

    if matrix_path:
        matrix_path = _resolve_path(repo_root, matrix_path)
        samples = _load_matrix_samples(repo_root=repo_root, matrix_path=matrix_path, sample_ids=sample_ids)
    else:
        matrix_path = None
        samples = _discover_samples(
            references_root=references_root,
            annotations_root=annotations_root,
            sample_ids=sample_ids,
        )
    sample_reports = [
        _evaluate_sample(
            sample=sample,
            provider_specs=provider_specs,
            events_root=events_root,
            reports_root=reports_root,
            replay_run_id=replay_run_id,
        )
        for sample in samples
    ]
    report = {
        "schema_version": SCHEMA_VERSION,
        "repo_root": str(repo_root),
        "references_root": str(references_root),
        "annotations_root": str(annotations_root),
        "events_root": str(events_root),
        "reports_root": str(reports_root),
        "matrix_path": str(matrix_path) if matrix_path else None,
        "replay_run_id": replay_run_id,
        "provider_specs": [
            {"provider": spec.provider, "file_slug": spec.file_slug}
            for spec in provider_specs
        ],
        "privacy_cost_flags": _privacy_cost_flags(sample_reports),
        "aggregate": _aggregate(sample_reports),
        "default_decision": _default_decision(sample_reports),
        "samples": sample_reports,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _resolve_root(repo_root: Path, root: Path | None, default_relative: str) -> Path:
    candidate = root or repo_root / default_relative
    return _resolve_path(repo_root, candidate)


def _resolve_path(repo_root: Path, candidate: Path) -> Path:
    if candidate.is_absolute():
        return candidate.resolve(strict=False)
    return (repo_root / candidate).resolve(strict=False)


def _load_matrix_samples(
    *,
    repo_root: Path,
    matrix_path: Path,
    sample_ids: list[str] | None,
) -> list[SampleSpec]:
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    wanted = set(sample_ids or [])
    samples: list[SampleSpec] = []
    for item in list(matrix.get("samples") or []):
        if not isinstance(item, dict):
            continue
        sample_id = str(item.get("sample_id") or item.get("id") or "").strip()
        if not sample_id:
            continue
        if wanted and sample_id not in wanted:
            continue
        samples.append(
            SampleSpec(
                sample_id=sample_id,
                sample_key=str(item.get("sample_key") or _sample_key_from_id(sample_id)),
                session_id=str(item.get("session_id") or sample_id),
                reference_path=_resolve_path(repo_root, Path(str(item["reference_path"]))),
                annotation_path=_resolve_path(repo_root, Path(str(item["annotation_path"]))),
                provider_specs=[
                    _matrix_provider_spec(repo_root=repo_root, item=provider_item)
                    for provider_item in list(item.get("providers") or [])
                    if isinstance(provider_item, dict)
                ],
            )
        )
    return samples


def _matrix_provider_spec(*, repo_root: Path, item: dict[str, Any]) -> ProviderSpec:
    provider = str(item["provider"])
    return ProviderSpec(
        provider=provider,
        file_slug=str(item.get("file_slug") or _provider_file_slug(provider)),
        events_path=_resolve_path(repo_root, Path(str(item["events_path"]))),
        transcript_report_path=_resolve_path(repo_root, Path(str(item["transcript_report_path"]))),
    )


def _provider_file_slug(provider: str) -> str:
    value = provider.strip().replace("_streaming", "")
    value = value.replace("_onnx", "")
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _discover_samples(
    *,
    references_root: Path,
    annotations_root: Path,
    sample_ids: list[str] | None,
) -> list[SampleSpec]:
    wanted = set(sample_ids or [])
    samples: list[SampleSpec] = []
    for reference_path in sorted(references_root.glob("S*.txt")):
        sample_id = reference_path.stem
        if wanted and sample_id not in wanted:
            continue
        annotation_path = annotations_root / f"{sample_id}.annotation.json"
        sample_key = _sample_key_from_id(sample_id)
        samples.append(
            SampleSpec(
                sample_id=sample_id,
                sample_key=sample_key,
                session_id=f"{sample_key}-001",
                reference_path=reference_path,
                annotation_path=annotation_path,
            )
        )
    return samples


def _sample_key_from_id(sample_id: str) -> str:
    parts = sample_id.split("-", 1)
    return parts[1] if len(parts) == 2 and parts[0].startswith("S") else sample_id


def _evaluate_sample(
    *,
    sample: SampleSpec,
    provider_specs: list[ProviderSpec],
    events_root: Path,
    reports_root: Path,
    replay_run_id: str,
) -> dict[str, Any]:
    sample_provider_specs = sample.provider_specs or provider_specs
    missing_inputs = _missing_inputs(
        sample=sample,
        provider_specs=sample_provider_specs,
        events_root=events_root,
        reports_root=reports_root,
    )
    base = {
        "sample_id": sample.sample_id,
        "sample_key": sample.sample_key,
        "session_id": sample.session_id,
        "reference_path": str(sample.reference_path),
        "annotation_path": str(sample.annotation_path),
    }
    if missing_inputs:
        return {
            **base,
            "status": "missing_inputs",
            "missing_inputs": missing_inputs,
            "quality_report_path": None,
            "summary": None,
            "default_decision": {
                "decision_status": "not_evaluated_missing_inputs",
                "blockers": ["missing_sample_inputs"],
            },
            "providers": [],
        }

    provider_inputs: list[dict[str, Any]] = []
    for spec in sample_provider_specs:
        events_path = _provider_events_path(sample=sample, spec=spec, events_root=events_root)
        replay_path = reports_root / f"{sample.session_id}.{spec.file_slug}.live-pipeline-replay-{replay_run_id}.json"
        replay_report = live_replay.build_asr_live_pipeline_replay_report(
            events_path=events_path,
            provider=spec.provider,
            session_id=f"{sample.session_id}-{spec.file_slug}-batch-replay",
        )
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        replay_path.write_text(
            json.dumps(replay_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        provider_inputs.append(
            {
                "provider": spec.provider,
                "transcript_report_path": _provider_transcript_report_path(
                    sample=sample,
                    spec=spec,
                    reports_root=reports_root,
                ),
                "pipeline_replay_report_path": replay_path,
            }
        )

    quality_report_path = reports_root / f"{sample.session_id}.asr-mainline-quality-{replay_run_id}.json"
    quality_report = build_asr_mainline_quality_report(
        sample_id=sample.sample_id,
        reference_path=sample.reference_path,
        annotation_path=sample.annotation_path,
        provider_reports=provider_inputs,
        output_path=quality_report_path,
    )
    reference_artifact_coverage = _reference_artifact_coverage(
        reference_text=sample.reference_path.read_text(encoding="utf-8").strip(),
        expected_terms=_expected_terms_from_annotation(sample.annotation_path),
        providers=quality_report["providers"],
    )
    return {
        **base,
        "status": "evaluated",
        "missing_inputs": [],
        "quality_report_path": str(quality_report_path),
        "summary": quality_report["summary"],
        "default_decision": quality_report["default_decision"],
        "reference_artifact_coverage": reference_artifact_coverage,
        "providers": [_compact_provider(provider) for provider in quality_report["providers"]],
    }


def _missing_inputs(
    *,
    sample: SampleSpec,
    provider_specs: list[ProviderSpec],
    events_root: Path,
    reports_root: Path,
) -> list[str]:
    missing: list[str] = []
    if not sample.reference_path.exists():
        missing.append(f"reference: {_display_path(sample.reference_path)}")
    if not sample.annotation_path.exists():
        missing.append(f"annotation: {_display_path(sample.annotation_path)}")
    for spec in provider_specs:
        events_path = _provider_events_path(sample=sample, spec=spec, events_root=events_root)
        transcript_report_path = _provider_transcript_report_path(sample=sample, spec=spec, reports_root=reports_root)
        if not events_path.exists():
            missing.append(f"events: {_display_path(events_path)}")
        if not transcript_report_path.exists():
            missing.append(f"transcript_report: {_display_path(transcript_report_path)}")
    return missing


def _provider_events_path(*, sample: SampleSpec, spec: ProviderSpec, events_root: Path) -> Path:
    return spec.events_path or events_root / f"{sample.session_id}.{spec.file_slug}.events.json"


def _provider_transcript_report_path(*, sample: SampleSpec, spec: ProviderSpec, reports_root: Path) -> Path:
    return spec.transcript_report_path or reports_root / f"{sample.session_id}.{spec.file_slug}.transcript-report.json"


def _display_path(path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(live_replay.REPO_ROOT.resolve(strict=False)).as_posix()
    except ValueError:
        return str(path)


def _compact_provider(provider: dict[str, Any]) -> dict[str, Any]:
    quality = provider["quality"]
    pipeline = provider["pipeline"]
    transcript = provider["transcript"]
    return {
        "provider": provider["provider"],
        "mainline_status": provider["mainline_status"],
        "pipeline_closed": bool(pipeline["closed_to_candidate_timeline"]),
        "quality_passed": bool(quality["passed_minimum_quality_gate"]),
        "term_recall": quality["term_recall"],
        "char_error_rate": quality["char_error_rate"],
        "contains_unk": bool(quality["contains_unk"]),
        "missing_terms": list(quality["missing_terms"]),
        "rtf": transcript["rtf"],
        "first_partial_latency_ms": pipeline["asr_metrics"].get("first_partial_latency_ms"),
        "first_final_latency_ms": pipeline["asr_metrics"].get("first_final_latency_ms"),
        "remote_asr_called": bool(pipeline["remote_asr_called"]),
        "llm_called": bool(pipeline["llm_called"]),
    }


def _privacy_cost_flags(sample_reports: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "remote_asr_called": any(
            provider.get("remote_asr_called") is True
            for sample in sample_reports
            for provider in sample.get("providers", [])
        ),
        "llm_called": any(
            provider.get("llm_called") is True
            for sample in sample_reports
            for provider in sample.get("providers", [])
        ),
        "raw_audio_uploaded": False,
        "user_audio_committed_to_repo": False,
    }


def _aggregate(sample_reports: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated = [sample for sample in sample_reports if sample["status"] == "evaluated"]
    missing = [sample for sample in sample_reports if sample["status"] == "missing_inputs"]
    providers = [
        provider
        for sample in evaluated
        for provider in sample.get("providers", [])
    ]
    return {
        "sample_count": len(sample_reports),
        "evaluated_sample_count": len(evaluated),
        "missing_input_sample_count": len(missing),
        "sample_provider_count": len(providers),
        "pipeline_closed_sample_provider_count": sum(1 for provider in providers if provider["pipeline_closed"]),
        "quality_pass_sample_provider_count": sum(1 for provider in providers if provider["quality_passed"]),
        "samples_with_quality_pass_count": sum(
            1
            for sample in evaluated
            if any(provider["quality_passed"] for provider in sample.get("providers", []))
        ),
        "remote_asr_call_count": sum(1 for provider in providers if provider["remote_asr_called"]),
        "llm_call_count": sum(1 for provider in providers if provider["llm_called"]),
        "best_provider_by_average_term_recall": _best_provider_by_average_term_recall(providers),
        "best_provider_by_quality_coverage": _best_provider_by_quality_coverage(providers),
        "samples_without_quality_pass": [
            str(sample["sample_id"])
            for sample in evaluated
            if not any(provider["quality_passed"] for provider in sample.get("providers", []))
        ],
        "suspected_reference_artifact_mismatch_sample_count": sum(
            1
            for sample in evaluated
            if dict(sample.get("reference_artifact_coverage") or {}).get(
                "suspected_reference_artifact_mismatch"
            )
        ),
        "samples_with_suspected_reference_artifact_mismatch": [
            str(sample["sample_id"])
            for sample in evaluated
            if dict(sample.get("reference_artifact_coverage") or {}).get(
                "suspected_reference_artifact_mismatch"
            )
        ],
        "provider_quality_summary": _provider_quality_summary(providers),
    }


def _best_provider_by_average_term_recall(providers: list[dict[str, Any]]) -> str | None:
    by_provider: dict[str, list[float]] = {}
    for provider in providers:
        by_provider.setdefault(str(provider["provider"]), []).append(float(provider["term_recall"]))
    if not by_provider:
        return None
    return max(
        by_provider,
        key=lambda provider_name: (
            sum(by_provider[provider_name]) / len(by_provider[provider_name]),
            provider_name,
        ),
    )


def _best_provider_by_quality_coverage(providers: list[dict[str, Any]]) -> str | None:
    summary = _provider_quality_summary(providers)
    if not summary:
        return None
    return str(summary[0]["provider"])


def _provider_quality_summary(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_provider: dict[str, list[dict[str, Any]]] = {}
    for provider in providers:
        by_provider.setdefault(str(provider["provider"]), []).append(provider)

    summaries: list[dict[str, Any]] = []
    for provider_name, items in by_provider.items():
        missing_terms = sorted(
            {
                str(term)
                for item in items
                for term in list(item.get("missing_terms") or [])
            }
        )
        summaries.append(
            {
                "provider": provider_name,
                "evaluated_sample_count": len(items),
                "pipeline_closed_count": sum(1 for item in items if item["pipeline_closed"]),
                "quality_pass_count": sum(1 for item in items if item["quality_passed"]),
                "usable_pass_count": sum(1 for item in items if _is_usable_provider_result(item)),
                "quality_pass_rate": _rounded_rate(
                    sum(1 for item in items if item["quality_passed"]),
                    len(items),
                ),
                "usable_pass_rate": _rounded_rate(
                    sum(1 for item in items if _is_usable_provider_result(item)),
                    len(items),
                ),
                "average_term_recall": _rounded_average(float(item["term_recall"]) for item in items),
                "average_char_error_rate": _rounded_average(float(item["char_error_rate"]) for item in items),
                "contains_unk_count": sum(1 for item in items if item["contains_unk"]),
                "average_rtf": _rounded_average(
                    float(item["rtf"])
                    for item in items
                    if item.get("rtf") is not None
                ),
                "missing_terms": missing_terms,
            }
        )
    return sorted(
        summaries,
        key=lambda item: (
            int(item["usable_pass_count"]),
            int(item["quality_pass_count"]),
            int(item["pipeline_closed_count"]),
            float(item["average_term_recall"] or 0.0),
            -float(item["average_char_error_rate"] or 0.0),
            -int(item["contains_unk_count"]),
            str(item["provider"]),
        ),
        reverse=True,
    )


def _is_usable_provider_result(provider: dict[str, Any]) -> bool:
    return bool(provider.get("quality_passed")) and bool(provider.get("pipeline_closed"))


def _rounded_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _rounded_average(values: Any) -> float | None:
    materialized = [float(value) for value in values]
    if not materialized:
        return None
    return round(sum(materialized) / len(materialized), 6)


def _default_decision(sample_reports: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate = _aggregate(sample_reports)
    blockers: list[str] = []
    if aggregate["missing_input_sample_count"]:
        blockers.append("missing_sample_inputs")
    if aggregate["evaluated_sample_count"] == 0:
        blockers.append("no_evaluated_samples")
    if aggregate["evaluated_sample_count"] and (
        aggregate["samples_with_quality_pass_count"] < aggregate["evaluated_sample_count"]
    ):
        blockers.append("some_samples_have_no_quality_passing_provider")
    if aggregate["pipeline_closed_sample_provider_count"] < aggregate["sample_provider_count"]:
        blockers.append("some_provider_pipelines_not_closed")
    if aggregate["suspected_reference_artifact_mismatch_sample_count"]:
        blockers.append("suspected_reference_artifact_mismatch")
    if aggregate["remote_asr_call_count"]:
        blockers.append("unexpected_remote_asr_call")
    if aggregate["llm_call_count"]:
        blockers.append("unexpected_llm_call")

    if aggregate["missing_input_sample_count"]:
        status = "no_go_batch_incomplete"
    elif blockers:
        status = "no_go_quality_not_production"
    else:
        status = "candidate_for_next_real_audio_gate"
    return {
        "decision_status": status,
        "blockers": blockers,
        "best_provider_candidate": aggregate["best_provider_by_quality_coverage"],
        "recommended_next_gate": (
            "fill_missing_asr_artifacts_then_improve_asr_quality"
            if status == "no_go_batch_incomplete"
            else "repair_or_regenerate_aligned_synthetic_audio_artifacts_before_provider_judgment"
            if "suspected_reference_artifact_mismatch" in blockers
            else "real_or_public_audio_wall_clock_soak"
            if status == "candidate_for_next_real_audio_gate"
            else "improve_asr_terms_segmentation_or_provider_before_real_gate"
        ),
    }


def _expected_terms_from_annotation(annotation_path: Path) -> list[str]:
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    terms: list[str] = []
    for item in list(annotation.get("technical_entities") or []):
        if not isinstance(item, dict):
            continue
        value = str(item.get("normalized") or item.get("text") or "").strip()
        if value and value not in terms:
            terms.append(value)
    return terms


def _reference_artifact_coverage(
    *,
    reference_text: str,
    expected_terms: list[str],
    providers: list[dict[str, Any]],
) -> dict[str, Any]:
    reference_normalized, reference_index_map = _normalize_for_coverage_with_map(reference_text)
    provider_coverages = [
        _provider_reference_coverage(provider=provider, reference_normalized=reference_normalized)
        for provider in providers
    ]
    best = max(
        provider_coverages,
        key=lambda item: (
            bool(item["quality_passed"]),
            float(item["term_recall"]),
            -float(item["char_error_rate"]),
            int(not item["contains_unk"]),
            -float(item["rtf"] or 0.0),
            float(item["covered_reference_ratio"]),
            int(item["transcript_normalized_length"]),
            int(item["covered_reference_normalized_index"]),
            float(item["duration_seconds"] or 0.0),
            str(item["provider"]),
        ),
        default={
            "provider": None,
            "quality_passed": False,
            "term_recall": 0.0,
            "char_error_rate": 1.0,
            "contains_unk": True,
            "rtf": None,
            "covered_reference_ratio": 0.0,
            "covered_reference_normalized_index": 0,
            "transcript_normalized_length": 0,
            "duration_seconds": None,
        },
    )
    covered_index = int(best["covered_reference_normalized_index"])
    covered_ratio = float(best["covered_reference_ratio"])
    uncovered_suffix = _uncovered_suffix(
        reference_text=reference_text,
        reference_index_map=reference_index_map,
        covered_normalized_index=covered_index,
    )
    all_provider_missing_terms = _all_provider_missing_terms(providers=providers, expected_terms=expected_terms)
    missing_terms_in_uncovered_suffix = [
        term
        for term in all_provider_missing_terms
        if _normalize_for_coverage(term) in _normalize_for_coverage(uncovered_suffix)
    ]
    suspected = (
        covered_ratio >= COVERAGE_MIN_SIGNAL_THRESHOLD
        and covered_ratio < COVERAGE_OK_THRESHOLD
        and bool(missing_terms_in_uncovered_suffix)
    )
    if suspected:
        status = "suspected_reference_artifact_mismatch"
    elif covered_ratio >= COVERAGE_OK_THRESHOLD:
        status = "coverage_ok"
    else:
        status = "coverage_inconclusive"
    return {
        "status": status,
        "suspected_reference_artifact_mismatch": suspected,
        "best_provider": best["provider"],
        "best_covered_reference_ratio": round(covered_ratio, 6),
        "best_covered_reference_normalized_index": covered_index,
        "best_transcript_duration_seconds": best["duration_seconds"],
        "all_provider_missing_terms": all_provider_missing_terms,
        "missing_terms_in_uncovered_suffix": missing_terms_in_uncovered_suffix,
        "uncovered_reference_suffix_preview": uncovered_suffix[:120],
        "provider_coverages": provider_coverages,
    }


def _provider_reference_coverage(
    *,
    provider: dict[str, Any],
    reference_normalized: str,
) -> dict[str, Any]:
    transcript = dict(provider.get("transcript") or {})
    hypothesis = str(transcript.get("normalized_text") or transcript.get("text") or "")
    hypothesis_normalized = _normalize_for_coverage(hypothesis)
    covered_index = _covered_reference_normalized_index(
        reference_normalized=reference_normalized,
        hypothesis_normalized=hypothesis_normalized,
    )
    ratio = covered_index / len(reference_normalized) if reference_normalized else 1.0
    return {
        "provider": str(provider.get("provider") or ""),
        "quality_passed": bool(provider.get("quality", {}).get("passed_minimum_quality_gate")),
        "term_recall": float(provider.get("quality", {}).get("term_recall") or 0.0),
        "char_error_rate": float(provider.get("quality", {}).get("char_error_rate") or 0.0),
        "contains_unk": bool(provider.get("quality", {}).get("contains_unk")),
        "rtf": _optional_float(transcript.get("rtf")),
        "covered_reference_ratio": round(ratio, 6),
        "covered_reference_normalized_index": covered_index,
        "transcript_normalized_length": len(hypothesis_normalized),
        "duration_seconds": _optional_float(transcript.get("duration_seconds")),
    }


def _covered_reference_normalized_index(*, reference_normalized: str, hypothesis_normalized: str) -> int:
    if not reference_normalized:
        return 0
    if not hypothesis_normalized:
        return 0
    matcher = SequenceMatcher(None, reference_normalized, hypothesis_normalized, autojunk=False)
    covered_index = 0
    for block in matcher.get_matching_blocks():
        if block.size >= COVERAGE_MIN_BLOCK_SIZE:
            covered_index = max(covered_index, block.a + block.size)
    return min(covered_index, len(reference_normalized))


def _all_provider_missing_terms(*, providers: list[dict[str, Any]], expected_terms: list[str]) -> list[str]:
    missing_sets: list[set[str]] = []
    expected = set(expected_terms)
    for provider in providers:
        quality = dict(provider.get("quality") or {})
        provider_missing = {str(term) for term in list(quality.get("missing_terms") or [])}
        if expected:
            provider_missing = provider_missing & expected
        missing_sets.append(provider_missing)
    if not missing_sets:
        return []
    return sorted(set.intersection(*missing_sets), key=lambda term: expected_terms.index(term) if term in expected_terms else term)


def _uncovered_suffix(
    *,
    reference_text: str,
    reference_index_map: list[int],
    covered_normalized_index: int,
) -> str:
    if not reference_index_map or covered_normalized_index >= len(reference_index_map):
        return ""
    original_index = reference_index_map[max(0, covered_normalized_index)]
    return reference_text[original_index:].strip()


def _normalize_for_coverage(value: str) -> str:
    normalized, _ = _normalize_for_coverage_with_map(value)
    return normalized


def _normalize_for_coverage_with_map(value: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    index_map: list[int] = []
    for original_index, char in enumerate(str(value or "")):
        for folded_char in char.casefold():
            if folded_char.isspace() or re.match(r"[，。！？、,.!?%：:；;（）()\[\]{}\"'`~]", folded_char):
                continue
            chars.append(folded_char)
            index_map.append(original_index)
    return "".join(chars), index_map


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_provider_specs(values: list[str] | None) -> list[ProviderSpec]:
    if not values:
        return [ProviderSpec(provider, slug) for provider, slug in DEFAULT_PROVIDER_SPECS]
    specs: list[ProviderSpec] = []
    for value in values:
        if "=" not in value:
            raise SystemExit("--provider must use provider_id=file_slug, for example funasr_streaming=funasr")
        provider, file_slug = value.split("=", 1)
        if not provider.strip() or not file_slug.strip():
            raise SystemExit("--provider must include non-empty provider_id and file_slug")
        specs.append(ProviderSpec(provider.strip(), file_slug.strip()))
    return specs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--references-root", type=Path)
    parser.add_argument("--annotations-root", type=Path)
    parser.add_argument("--events-root", type=Path)
    parser.add_argument("--reports-root", type=Path)
    parser.add_argument("--matrix", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-run-id")
    parser.add_argument("--provider", action="append")
    parser.add_argument("--sample-id", action="append")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = run_asr_mainline_quality_batch_report(
        repo_root=args.repo_root,
        references_root=args.references_root,
        annotations_root=args.annotations_root,
        events_root=args.events_root,
        reports_root=args.reports_root,
        matrix_path=args.matrix,
        output_path=args.output,
        replay_run_id=args.replay_run_id,
        provider_specs=_parse_provider_specs(args.provider),
        sample_ids=args.sample_id,
    )
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["default_decision"]["decision_status"] == "candidate_for_next_real_audio_gate" else 2


if __name__ == "__main__":
    raise SystemExit(main())

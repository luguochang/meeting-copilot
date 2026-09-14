#!/usr/bin/env python3
"""Evaluate the mandatory three-run Stage 0 realtime canary.

Each input is a redacted ``manifest.json`` emitted by
``pi_stage0_production_replay.py``.  The gate intentionally does not make
Provider requests itself.  It compares the manifests and fails closed when a
run is missing evidence, used different inputs, or recorded any realtime
reliability/correction failure.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "meeting_copilot.pi_stage0_canary_gate.v1"
REQUIRED_RUN_COUNT = 3
TERMINAL_JOB_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
FORBIDDEN_ERROR_TOKENS = frozenset(
    {
        "429",
        "502",
        "provider_429",
        "provider_502",
        "provider_timeout",
        "provider_transport",
        "timeout",
        "timed_out",
        "deadline_exceeded",
    }
)


class CanaryGateError(ValueError):
    """Raised when a manifest cannot be used as Stage 0 canary evidence."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized


def _fixture_key(manifest: Mapping[str, Any]) -> tuple[Any, ...]:
    fixture = _mapping(manifest.get("fixture"))
    replay = _mapping(manifest.get("replay"))
    runtime = _mapping(manifest.get("runtime_validation"))
    realtime_model = _mapping(runtime.get("realtime_model"))
    metrics = _mapping(manifest.get("metrics"))
    # Older replay manifests keep timing under metrics; current manifests keep
    # the durable deadline in the latest intelligence job.  Missing values are
    # deliberately retained as None and rejected by the caller.
    intelligence = _mapping(metrics.get("intelligence"))
    latest_job = _mapping(intelligence.get("latest_job"))
    if not latest_job:
        latest_job = _mapping(_mapping(manifest.get("acceptance")).get("latest_intelligence_job"))
    deadline = latest_job.get("deadline_at_ms")
    if deadline is None:
        deadline = latest_job.get("deadline_ms")
    # ``deadline_at_ms`` is a wall-clock timestamp and necessarily differs for
    # every run. Compare the budget relative to the final/job creation time.
    if deadline is not None and latest_job.get("deadline_ms") is None:
        anchor = latest_job.get("final_committed_at_ms")
        if anchor is None:
            anchor = latest_job.get("job_created_at_ms", latest_job.get("created_at_ms"))
        try:
            if anchor is not None:
                deadline = int(deadline) - int(anchor)
        except (TypeError, ValueError, OverflowError):
            deadline = None
    return (
        fixture.get("sha256"),
        fixture.get("frame_count"),
        fixture.get("sample_rate_hz"),
        fixture.get("channels"),
        fixture.get("sample_width_bytes"),
        realtime_model.get("selected_model"),
        replay.get("chunk_seconds"),
        replay.get("tail_silence_seconds"),
        replay.get("pace"),
        deadline,
    )


def _error_tokens(manifest: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    failure = _mapping(manifest.get("failure"))
    for value in (
        failure.get("error_class"),
        failure.get("message"),
        failure.get("layer"),
    ):
        text = str(value or "").strip().casefold()
        if text:
            tokens.add(text)
    acceptance = _mapping(manifest.get("acceptance"))
    for value in _list(acceptance.get("failed_checks")):
        text = str(value or "").strip().casefold()
        if text:
            tokens.add(text)
    metrics = _mapping(manifest.get("metrics"))
    intelligence = _mapping(metrics.get("intelligence"))
    latest_result = _mapping(intelligence.get("latest_result"))
    for value in (
        latest_result.get("error_class"),
        latest_result.get("drop_reason"),
        _mapping(metrics.get("post_end")).get("error_class"),
    ):
        text = str(value or "").strip().casefold()
        if text:
            tokens.add(text)
    for job in _list(_mapping(metrics.get("post_end")).get("jobs")):
        error_class = str(_mapping(job).get("error_class") or "").strip().casefold()
        if error_class:
            tokens.add(error_class)
    for value in _list(_mapping(metrics.get("http")).get("requests")):
        request = _mapping(value)
        status = request.get("status")
        if status is not None:
            tokens.add(str(status).strip().casefold())
        error_class = str(request.get("error_class") or "").strip().casefold()
        if error_class:
            tokens.add(error_class)
    return tokens


def _contains_forbidden_error(tokens: set[str]) -> set[str]:
    matches: set[str] = set()
    for token in tokens:
        if token in FORBIDDEN_ERROR_TOKENS:
            matches.add(token)
            continue
        if any(forbidden in token for forbidden in FORBIDDEN_ERROR_TOKENS):
            matches.add(token)
    return matches


def _correction_summary(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    acceptance = _mapping(manifest.get("acceptance"))
    fixture_quality = _mapping(acceptance.get("fixture_quality"))
    corrections = _mapping(fixture_quality.get("corrections"))
    if corrections:
        return corrections
    metrics = _mapping(manifest.get("metrics"))
    direct = _mapping(metrics.get("corrections"))
    if direct:
        return direct

    # Production replay keeps the authoritative durable job list under
    # metrics.post_end.jobs. Derive a correction-only summary when the compact
    # metrics.corrections block is absent; missing jobs remain fail-closed.
    jobs = [
        _mapping(job)
        for job in _list(_mapping(metrics.get("post_end")).get("jobs"))
        if str(_mapping(job).get("kind") or "").strip().casefold() == "correction"
    ]
    if not jobs:
        return {}
    failed = [
        job
        for job in jobs
        if str(job.get("status") or "").strip().casefold() == "failed"
    ]
    unsettled = [
        job
        for job in jobs
        if str(job.get("status") or "").strip().casefold()
        not in TERMINAL_JOB_STATUSES
    ]
    return {
        "job_count": len(jobs),
        "failed_job_count": len(failed),
        "failed_job_ids": [str(job.get("id") or "") for job in failed],
        "unsettled_job_count": len(unsettled),
        "unsettled_job_ids": [str(job.get("id") or "") for job in unsettled],
    }


def _job_statuses(manifest: Mapping[str, Any]) -> list[str]:
    metrics = _mapping(manifest.get("metrics"))
    post_end = _mapping(metrics.get("post_end"))
    raw_jobs = _list(post_end.get("jobs"))
    if not raw_jobs:
        # The replay manifest may only carry acceptance's unsettled IDs.  An
        # absent job list is not enough evidence to claim all jobs settled.
        return []
    return [str(_mapping(job).get("status") or "").strip().casefold() for job in raw_jobs]


def _intervention_count(manifest: Mapping[str, Any]) -> int | None:
    metrics = _mapping(manifest.get("metrics"))
    decisions = _mapping(metrics.get("decisions"))
    value = decisions.get("intervention_count")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    outcome_counts = _mapping(decisions.get("outcome_counts"))
    value = outcome_counts.get("intervention")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    pi = _mapping(_mapping(manifest.get("acceptance")).get("fixture_quality")).get("pi")
    if isinstance(pi, Mapping):
        value = pi.get("intervention_count")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    # Current replay stores the count in acceptance's latest outcome only when
    # the full metrics sidecar is not embedded.  Do not infer success from a
    # status string; missing evidence must fail closed.
    return None


def _run_checks(manifest: Mapping[str, Any], index: int) -> dict[str, Any]:
    acceptance = _mapping(manifest.get("acceptance"))
    metrics = _mapping(manifest.get("metrics"))
    provider_health = _mapping(_mapping(manifest.get("runtime_validation")).get("provider_health"))
    llm_health = _mapping(provider_health.get("llm"))
    checks: dict[str, bool] = {
        "manifest_status_passed": manifest.get("status") == "passed",
        "acceptance_passed": acceptance.get("passed") is True,
        "runtime_validated": _mapping(manifest.get("runtime_validation")).get("validated") is True,
        "provider_realtime_ready": llm_health.get("realtime_ready") is True,
        "post_end_settled": _mapping(acceptance.get("post_end")).get("settled") is True
        or _mapping(metrics.get("post_end")).get("settled") is True,
        "fixture_present": bool(_mapping(manifest.get("fixture")).get("sha256")),
        "realtime_model_present": bool(
            _mapping(_mapping(manifest.get("runtime_validation")).get("realtime_model")).get("selected_model")
        ),
    }
    errors = _contains_forbidden_error(_error_tokens(manifest))
    checks["no_provider_or_timeout_error"] = not errors
    correction = _correction_summary(manifest)
    failed_corrections = correction.get("failed_job_count")
    unsettled_corrections = correction.get("unsettled_job_count")
    checks["correction_zero_failed"] = failed_corrections == 0
    checks["correction_zero_unsettled"] = unsettled_corrections == 0
    statuses = _job_statuses(manifest)
    checks["durable_jobs_terminal"] = bool(statuses) and all(status in TERMINAL_JOB_STATUSES for status in statuses)
    intervention_count = _intervention_count(manifest)
    checks["pi_intervention_present"] = intervention_count is not None and intervention_count >= 1
    return {
        "run": index,
        "meeting_id": manifest.get("meeting_id"),
        "checks": checks,
        "passed": all(checks.values()),
        "failed_checks": [name for name, passed in checks.items() if not passed],
        "forbidden_error_tokens": sorted(errors),
        "intervention_count": intervention_count,
        "job_statuses": statuses,
        "correction": dict(correction),
        "fixture_key": list(_fixture_key(manifest)),
    }


def evaluate_manifests(manifests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return a fail-closed report for exactly three comparable runs."""

    if len(manifests) != REQUIRED_RUN_COUNT:
        raise CanaryGateError(f"expected exactly {REQUIRED_RUN_COUNT} manifests")
    run_reports = [_run_checks(manifest, index + 1) for index, manifest in enumerate(manifests)]
    fixture_keys = [tuple(report["fixture_key"]) for report in run_reports]
    same_input = len(set(fixture_keys)) == 1 and all(
        all(value is not None for value in key) for key in fixture_keys
    )
    run_checks = [bool(report["passed"]) for report in run_reports]
    meeting_ids = [str(manifest.get("meeting_id") or "").strip() for manifest in manifests]
    independent_runs = all(meeting_ids) and len(set(meeting_ids)) == REQUIRED_RUN_COUNT
    failed_checks = []
    if not same_input:
        failed_checks.append("same_fixture_model_deadline")
    if not independent_runs:
        failed_checks.append("independent_run_identity")
    failed_checks.extend(
        f"run_{report['run']}:{check}"
        for report in run_reports
        for check in report["failed_checks"]
    )
    status_counts = Counter(str(manifest.get("status") or "unknown") for manifest in manifests)
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": same_input and independent_runs and all(run_checks),
        "required_run_count": REQUIRED_RUN_COUNT,
        "observed_run_count": len(manifests),
        "same_fixture_model_deadline": same_input,
        "independent_run_identity": independent_runs,
        "distinct_meeting_id_count": len(set(meeting_ids)),
        "run_passed": run_checks,
        "failed_checks": failed_checks,
        "status_counts": dict(sorted(status_counts.items())),
        "runs": run_reports,
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryGateError(f"cannot read manifest {path}: {type(exc).__name__}") from exc
    if not isinstance(value, Mapping):
        raise CanaryGateError(f"manifest {path} must contain an object")
    manifest = dict(value)
    # Replay deliberately keeps large diagnostics in sidecar files. Load the
    # metrics sidecar when present so the gate can inspect HTTP error classes
    # and the complete durable job list without changing the redacted manifest
    # schema or requiring callers to pass a second path.
    if not isinstance(manifest.get("metrics"), Mapping):
        metrics_path = path.parent / "metrics.json"
        if metrics_path.is_file():
            try:
                metrics_value = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CanaryGateError(
                    f"cannot read metrics sidecar {metrics_path}: {type(exc).__name__}"
                ) from exc
            if not isinstance(metrics_value, Mapping):
                raise CanaryGateError(f"metrics sidecar {metrics_path} must contain an object")
            manifest["metrics"] = dict(metrics_value)
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        nargs="+",
        type=Path,
        help="Exactly three replay manifest.json files, in chronological order",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = evaluate_manifests([load_manifest(path) for path in args.manifest])
    except CanaryGateError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.expanduser().resolve().write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

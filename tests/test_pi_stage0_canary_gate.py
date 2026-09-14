from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pi_stage0_canary_gate as gate  # noqa: E402


def _manifest(*, run: int, fixture_sha: str = "a" * 64) -> dict:
    return {
        "status": "passed",
        "meeting_id": f"canary-{run}",
        "fixture": {
            "sha256": fixture_sha,
            "frame_count": 100,
            "sample_rate_hz": 16_000,
            "channels": 1,
            "sample_width_bytes": 2,
        },
        "replay": {
            "pace": 1.0,
            "chunk_seconds": 0.3,
            "tail_silence_seconds": 9.0,
        },
        "runtime_validation": {
            "validated": True,
            "provider_health": {"llm": {"realtime_ready": True}},
            "realtime_model": {"selected_model": "gpt-5.4-mini"},
        },
        "acceptance": {
            "passed": True,
            "post_end": {"settled": True},
            "fixture_quality": {
                "corrections": {"failed_job_count": 0, "unsettled_job_count": 0},
                "pi": {"intervention_count": 1},
            },
        },
        "metrics": {
            "post_end": {
                "settled": True,
                "jobs": [{"status": "succeeded"}, {"status": "cancelled"}],
            },
            "decisions": {"intervention_count": 1},
            "http": {"requests": [{"status": 200}]},
            "intelligence": {
                "latest_job": {"deadline_at_ms": 1_000},
            },
        },
    }


def test_three_identical_successful_manifests_pass():
    report = gate.evaluate_manifests([_manifest(run=1), _manifest(run=2), _manifest(run=3)])

    assert report["passed"] is True
    assert report["same_fixture_model_deadline"] is True
    assert report["failed_checks"] == []


def test_repeating_one_successful_manifest_is_not_three_independent_canaries():
    manifest = _manifest(run=1)

    report = gate.evaluate_manifests([manifest, deepcopy(manifest), deepcopy(manifest)])

    assert report["passed"] is False
    assert report["independent_run_identity"] is False
    assert report["distinct_meeting_id_count"] == 1
    assert "independent_run_identity" in report["failed_checks"]


def test_absolute_deadline_timestamps_are_normalized_to_the_same_budget():
    manifests = [_manifest(run=1), _manifest(run=2), _manifest(run=3)]
    for index, item in enumerate(manifests):
        item["acceptance"]["latest_intelligence_job"] = {
            "final_committed_at_ms": 10_000 * (index + 1),
            "deadline_at_ms": 10_000 * (index + 1) + 10_000,
        }

    report = gate.evaluate_manifests(manifests)

    assert report["passed"] is True
    assert report["same_fixture_model_deadline"] is True


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda item: item.update({"status": "failed"}), "run_2:manifest_status_passed"),
        (
            lambda item: item["metrics"]["http"]["requests"].append({"status": 429}),
            "run_2:no_provider_or_timeout_error",
        ),
        (
            lambda item: item["acceptance"]["fixture_quality"]["corrections"].update(
                {"failed_job_count": 1}
            ),
            "run_2:correction_zero_failed",
        ),
        (
            lambda item: item["metrics"]["decisions"].update({"intervention_count": 0}),
            "run_2:pi_intervention_present",
        ),
        (
            lambda item: item["metrics"]["post_end"].update({"jobs": [{"status": "running"}]}),
            "run_2:durable_jobs_terminal",
        ),
    ],
)
def test_each_run_gate_is_fail_closed(mutate, expected):
    manifests = [_manifest(run=1), _manifest(run=2), _manifest(run=3)]
    mutate(manifests[1])

    report = gate.evaluate_manifests(manifests)

    assert report["passed"] is False
    assert expected in report["failed_checks"]


def test_different_fixture_or_model_fails_comparability():
    manifests = [_manifest(run=1), _manifest(run=2), _manifest(run=3)]
    manifests[2]["fixture"]["sha256"] = "b" * 64

    report = gate.evaluate_manifests(manifests)

    assert report["passed"] is False
    assert report["same_fixture_model_deadline"] is False
    assert "same_fixture_model_deadline" in report["failed_checks"]


def test_cli_requires_exactly_three_manifests(tmp_path):
    paths = []
    for index in range(2):
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(_manifest(run=index + 1)), encoding="utf-8")
        paths.append(str(path))

    result = subprocess.run(
        [sys.executable, str(TOOLS_ROOT / "pi_stage0_canary_gate.py"), *paths],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "exactly 3" in result.stderr


def test_load_manifest_joins_metrics_sidecar(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(run=1)), encoding="utf-8")
    (tmp_path / "metrics.json").write_text(
        json.dumps({"post_end": {"jobs": [{"status": "succeeded"}]}}),
        encoding="utf-8",
    )

    loaded = gate.load_manifest(manifest_path)

    assert loaded["metrics"]["post_end"]["jobs"][0]["status"] == "succeeded"


def test_gate_derives_correction_summary_from_post_end_jobs():
    manifest = {
        "status": "passed",
        "meeting_id": "run-derived-corrections",
        "fixture": {"sha256": "fixture"},
        "runtime_validation": {
            "validated": True,
            "realtime_model": {"selected_model": "deepseek-v4-flash"},
            "provider_health": {"llm": {"realtime_ready": True}},
        },
        "acceptance": {
            "passed": True,
            "post_end": {"settled": True},
            "fixture_quality": {},
        },
        "metrics": {
            "decisions": {"intervention_count": 1},
            "post_end": {
                "settled": True,
                "jobs": [
                    {"id": "intelligence-1", "kind": "intelligence", "status": "succeeded"},
                    {"id": "correction-1", "kind": "correction", "status": "succeeded"},
                    {"id": "minutes-1", "kind": "minutes", "status": "succeeded"},
                ],
            },
        },
    }

    assert gate._correction_summary(manifest) == {
        "job_count": 1,
        "failed_job_count": 0,
        "failed_job_ids": [],
        "unsettled_job_count": 0,
        "unsettled_job_ids": [],
    }


def test_provider_errors_in_metrics_sidecar_fail_gate(tmp_path):
    manifest = _manifest(run=1)
    manifest.pop("metrics")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {
                "intelligence": {"latest_result": {"error_class": "deadline_exceeded"}},
                "post_end": {
                    "jobs": [
                        {"status": "failed", "error_class": "provider_timeout"},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    report = gate.evaluate_manifests([gate.load_manifest(manifest_path)] * 3)

    assert report["passed"] is False
    assert "run_1:no_provider_or_timeout_error" in report["failed_checks"]

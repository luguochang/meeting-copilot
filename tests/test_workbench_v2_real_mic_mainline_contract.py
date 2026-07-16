from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    REPO_ROOT
    / "code"
    / "web_mvp"
    / "e2e"
    / "workbench_v2_real_mic_mainline.mjs"
)


def _job(
    kind: str,
    *,
    status: str = "succeeded",
    error_class: str | None = None,
    job_id: str | None = None,
) -> dict:
    return {
        "id": job_id or f"job-{kind}",
        "kind": kind,
        "status": status,
        "attempts": 1,
        "max_attempts": 3,
        "error_class": error_class,
        "created_at_ms": 1_000,
        "updated_at_ms": 2_000,
        "completed_at_ms": 2_000 if status == "succeeded" else None,
    }


def _write_artifact(
    root: Path,
    *,
    jobs: list[dict] | None,
    segment_revision: int,
    revised_event: bool,
    trace_revision_count: int,
) -> None:
    snapshot = {
        "segments": [
            {
                "segment_id": "segment-1",
                "revision": segment_revision,
            }
        ],
    }
    if jobs is not None:
        snapshot["jobs"] = jobs
    events = {
        "events": (
            [
                {
                    "type": "transcript.segment.revised",
                    "aggregate_id": "segment-1",
                    "payload": {"segment_id": "segment-1", "revision": 2},
                }
            ]
            if revised_event
            else []
        )
    }
    traces = {
        "traces": [
            {
                "stages": {
                    "validated": {
                        "attributes": {"revision_count": trace_revision_count}
                    }
                }
            }
        ]
    }
    root.mkdir(parents=True)
    for name, payload in (
        ("snapshot.json", snapshot),
        ("events.json", events),
        ("traces.json", traces),
    ):
        (root / name).write_text(
            json.dumps(payload, ensure_ascii=True),
            encoding="utf-8",
        )


def _evaluate(root: Path) -> dict:
    completed = subprocess.run(
        ["node", str(SCRIPT), "--evaluate-report-contract", str(root)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_old_artifact_shape_fails_closed_on_missing_jobs_and_revision_projection(
    tmp_path,
):
    artifact = tmp_path / "old-artifact"
    _write_artifact(
        artifact,
        jobs=None,
        segment_revision=1,
        revised_event=False,
        trace_revision_count=1,
    )

    report = _evaluate(artifact)

    assert report["verdict"] == "no_go"
    assert report["jobs_contract_present"] is False
    assert {
        "jobs_contract_missing",
        "correction_jobs_incomplete",
        "suggestion_jobs_incomplete",
        "correction_projection_mismatch",
    } <= set(report["blockers"])
    assert report["trace_revision_count"] == 1
    assert report["transcript_revision_event_count"] == 0
    assert report["transcript_revision_count"] == 0


def test_complete_job_and_revision_observability_contract_is_go(tmp_path):
    artifact = tmp_path / "complete-artifact"
    _write_artifact(
        artifact,
        jobs=[_job("correction"), _job("suggestion")],
        segment_revision=2,
        revised_event=True,
        trace_revision_count=1,
    )

    report = _evaluate(artifact)

    assert report["verdict"] == "go"
    assert report["blockers"] == []
    assert report["jobs_contract_present"] is True
    assert report["correction_jobs"][0]["status"] == "succeeded"
    assert report["suggestion_jobs"][0]["status"] == "succeeded"


def test_expected_superseded_correction_is_go_with_successful_replacement(tmp_path):
    artifact = tmp_path / "superseded-artifact"
    _write_artifact(
        artifact,
        jobs=[
            _job(
                "correction",
                status="cancelled",
                error_class="evidence_superseded",
                job_id="job-correction-old",
            ),
            _job("correction", job_id="job-correction-replacement"),
            _job("suggestion"),
        ],
        segment_revision=2,
        revised_event=True,
        trace_revision_count=1,
    )

    report = _evaluate(artifact)

    assert report["verdict"] == "go"
    assert report["blockers"] == []


@pytest.mark.parametrize(
    ("lane", "status", "expected_blocker"),
    [
        ("correction", "cancelled", "correction_jobs_incomplete"),
        ("suggestion", "retry_wait", "suggestion_jobs_incomplete"),
    ],
)
def test_ai_job_lanes_require_succeeded_terminal_status(
    tmp_path,
    lane,
    status,
    expected_blocker,
):
    jobs = [_job("correction"), _job("suggestion")]
    next(job for job in jobs if job["kind"] == lane).update(
        status=status,
        completed_at_ms=None,
    )
    artifact = tmp_path / f"{lane}-{status}"
    _write_artifact(
        artifact,
        jobs=jobs,
        segment_revision=2,
        revised_event=True,
        trace_revision_count=1,
    )

    report = _evaluate(artifact)

    assert report["verdict"] == "no_go"
    assert expected_blocker in report["blockers"]

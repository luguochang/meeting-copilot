#!/usr/bin/env python3
"""Decide whether public-audio planned samples are ready without downloading audio."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TextIO


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import public_audio_sample_extraction_plan  # noqa: E402


DECISION_ID = "DRV-031"
DECISION_MODE = "public_audio_planned_sample_manifest_decision_only"
DECISION_VERSION = "public_audio_planned_sample_manifest_decision.v1"
SOURCE_SNAPSHOT_DATE = "2026-07-03"

CANDIDATE_SOURCE_ORDER = [
    "alimeeting_openslr_slr119",
    "aishell4_openslr_slr111",
]

CANDIDATE_SOURCE_REVIEW = {
    "alimeeting_openslr_slr119": {
        "source_url": "https://www.openslr.org/119/",
        "source_license": "CC BY-SA 4.0",
        "source_split": "eval",
        "archive_name": "Eval_Ali.tar.gz",
        "archive_size_note": "approximately 3.42G on OpenSLR SLR119",
        "review_priority": 1,
        "use_boundary": "meeting acoustics, near/far-field contrast, speaker turns, overlap and ASR event contract",
    },
    "aishell4_openslr_slr111": {
        "source_url": "https://www.openslr.org/111/",
        "source_license": "CC BY-SA 4.0",
        "source_split": "test",
        "archive_name": "test.tar.gz",
        "archive_size_note": "approximately 5.2G on OpenSLR SLR111",
        "review_priority": 2,
        "use_boundary": "meeting acoustics, far-field, multi-speaker overlap and ASR event contract",
    },
}

BLOCKED_REASONS = [
    "no_verified_archive_member_path",
    "no_expected_clip_sha256_after_extract",
    "no_user_approval_for_gb_archive_download",
]

REQUIRED_MANIFEST_EVIDENCE = public_audio_sample_extraction_plan.PLANNED_SAMPLE_REQUIRED_FIELDS


def _sync_imported_tool_roots() -> None:
    public_audio_sample_extraction_plan.REPO_ROOT = REPO_ROOT


def _base_report(
    *,
    source_id: str,
    decision_status: str,
    public_audio_stage_status: str,
    validation_errors: list[str] | None = None,
) -> dict[str, object]:
    selected_source_review = CANDIDATE_SOURCE_REVIEW.get(source_id)
    return {
        "decision_mode": DECISION_MODE,
        "decision_id": DECISION_ID,
        "decision_version": DECISION_VERSION,
        "source_snapshot_date": SOURCE_SNAPSHOT_DATE,
        "decision_status": decision_status,
        "public_audio_stage_status": public_audio_stage_status,
        "source_id": source_id,
        "candidate_source_order": CANDIDATE_SOURCE_ORDER,
        "selected_source_review": selected_source_review,
        "candidate_source_review": [
            CANDIDATE_SOURCE_REVIEW[source_id]
            | {
                "source_id": source_id,
                "verified_archive_member_path_available": False,
                "expected_clip_sha256_available": False,
                "safe_to_download_now": False,
            }
            for source_id in CANDIDATE_SOURCE_ORDER
        ],
        "required_manifest_evidence": REQUIRED_MANIFEST_EVIDENCE,
        "blocked_reasons": BLOCKED_REASONS if decision_status == "blocked_no_verified_public_sample_manifest" else [],
        "planned_samples": [],
        "planned_sample_count": 0,
        "planned_total_duration_seconds": 0,
        "planned_samples_status": "not_planned",
        "download_status": "not_started",
        "download_command": None,
        "extract_command": None,
        "transcode_command": None,
        "safe_to_download_now": False,
        "safe_to_extract_now": False,
        "safe_to_transcode_now": False,
        "safe_to_call_asr_now": False,
        "safe_to_read_user_audio": False,
        "safe_to_read_configs_local": False,
        "safe_to_call_llm": False,
        "safe_to_commit_raw_audio": False,
        "validation_errors": validation_errors or [],
        "next_action": (
            "obtain_verified_archive_index_or_keep_blocked"
            if decision_status == "blocked_no_verified_public_sample_manifest"
            else "fix_validation_errors"
        ),
    }


def _blocked_validation_report(*, source_id: str, errors: list[str]) -> dict[str, object]:
    return _base_report(
        source_id=source_id,
        decision_status="blocked_by_manifest_validation",
        public_audio_stage_status="blocked",
        validation_errors=errors,
    )


def build_public_audio_planned_sample_manifest_decision(
    *,
    source_id: str = "alimeeting_openslr_slr119",
    planned_samples_file: str | None = None,
    target_root: str = "artifacts/tmp/public_audio",
    sample_budget_count: int = 3,
    sample_budget_minutes: int = 9,
    max_clip_seconds: int = 180,
    max_duration_seconds: int = 180,
    max_download_bytes: int = 600_000_000,
) -> dict[str, object]:
    _sync_imported_tool_roots()

    if source_id not in CANDIDATE_SOURCE_ORDER:
        return _blocked_validation_report(
            source_id=source_id,
            errors=["source_id is not approved for executable public sample manifest decision"],
        )

    if planned_samples_file is None:
        return _base_report(
            source_id=source_id,
            decision_status="blocked_no_verified_public_sample_manifest",
            public_audio_stage_status="blocked_no_planned_samples",
        )

    planned_samples, planned_samples_errors = public_audio_sample_extraction_plan._load_planned_samples_file(
        planned_samples_file,
    )
    if planned_samples_errors:
        return _blocked_validation_report(source_id=source_id, errors=planned_samples_errors)

    sample_plan = public_audio_sample_extraction_plan.build_public_sample_extraction_plan(
        source_id=source_id,
        target_root=target_root,
        max_duration_seconds=max_duration_seconds,
        max_download_bytes=max_download_bytes,
        sample_budget_count=sample_budget_count,
        sample_budget_minutes=sample_budget_minutes,
        max_clip_seconds=max_clip_seconds,
        planned_samples=planned_samples,
    )
    if sample_plan["plan_status"] != "ready_for_manual_download_review":
        return _blocked_validation_report(
            source_id=source_id,
            errors=list(sample_plan["validation_errors"]),
        ) | {
            "planned_samples_status": sample_plan["planned_samples_status"],
            "planned_samples": sample_plan["planned_samples"],
            "planned_sample_count": sample_plan["planned_sample_count"],
            "planned_total_duration_seconds": sample_plan["planned_total_duration_seconds"],
        }

    return _base_report(
        source_id=source_id,
        decision_status="schema_validated_no_download",
        public_audio_stage_status="ready_for_manual_download_review",
    ) | {
        "planned_samples": sample_plan["planned_samples"],
        "planned_sample_count": sample_plan["planned_sample_count"],
        "planned_total_duration_seconds": sample_plan["planned_total_duration_seconds"],
        "planned_samples_status": sample_plan["planned_samples_status"],
        "next_action": "manual_review_before_any_download",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", default="alimeeting_openslr_slr119")
    parser.add_argument("--planned-samples-file")
    parser.add_argument("--target-root", default="artifacts/tmp/public_audio")
    parser.add_argument("--sample-budget-count", type=int, default=3)
    parser.add_argument("--sample-budget-minutes", type=int, default=9)
    parser.add_argument("--max-clip-seconds", type=int, default=180)
    parser.add_argument("--max-duration-seconds", type=int, default=180)
    parser.add_argument("--max-download-bytes", type=int, default=600_000_000)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    report = build_public_audio_planned_sample_manifest_decision(
        source_id=args.source_id,
        planned_samples_file=args.planned_samples_file,
        target_root=args.target_root,
        sample_budget_count=args.sample_budget_count,
        sample_budget_minutes=args.sample_budget_minutes,
        max_clip_seconds=args.max_clip_seconds,
        max_duration_seconds=args.max_duration_seconds,
        max_download_bytes=args.max_download_bytes,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["decision_status"] == "schema_validated_no_download" else 1


if __name__ == "__main__":
    raise SystemExit(main())

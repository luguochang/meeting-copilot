#!/usr/bin/env python3
"""Run a no-side-effect simulated ASR-to-shadow-report pipeline smoke test."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import asr_live_pipeline_replay  # noqa: E402
import replay_shadow_report_draft_adapter  # noqa: E402
import shadow_report_ingestion_export_feedback  # noqa: E402


RUNNER_ID = "DRV-041"
BATCH_RUNNER_ID = "DRV-042"
RUNNER_MODE = "simulated_shadow_pipeline_smoke"
RUNNER_VERSION = "simulated_shadow_pipeline_smoke.v1"
BATCH_RUNNER_MODE = "simulated_shadow_pipeline_batch_smoke"
BATCH_RUNNER_VERSION = "simulated_shadow_pipeline_batch_smoke.v1"
EXECUTION_BOUNDARY = (
    "no_mic_no_audio_download_no_model_download_no_remote_asr_no_llm_no_artifact_write"
)
EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True

DEFAULT_MOCK_SCENARIO_SPECS = [
    {
        "session_id": "api-review-001",
        "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        "expected_kind": "engineering",
    },
    {
        "session_id": "architecture-review-001",
        "events_path": "artifacts/tmp/asr_events/architecture-review-001.mock.events.json",
        "expected_kind": "engineering",
    },
    {
        "session_id": "incident-review-001",
        "events_path": "artifacts/tmp/asr_events/incident-review-001.mock.events.json",
        "expected_kind": "engineering",
    },
    {
        "session_id": "release-review-001",
        "events_path": "artifacts/tmp/asr_events/release-review-001.mock.events.json",
        "expected_kind": "engineering",
    },
    {
        "session_id": "non-engineering-control-001",
        "events_path": "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json",
        "expected_kind": "negative_control",
    },
]

FALSE_SAFETY_FLAGS = [
    "safe_to_access_microphone_now",
    "safe_to_enumerate_audio_devices_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_microphone_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_real_user_audio_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunk_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_mutate_web_session_now",
    "safe_to_download_public_audio_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
]


def _false_safety_flags() -> dict[str, bool]:
    return {flag: False for flag in FALSE_SAFETY_FLAGS}


def _base_report(
    *,
    events_path: Path,
    event_manifest_path: Path | None,
    provider: str,
    session_id: str,
) -> dict[str, Any]:
    return {
        "runner_id": RUNNER_ID,
        "runner_mode": RUNNER_MODE,
        "runner_version": RUNNER_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "events_path": events_path.as_posix(),
        "event_manifest_path": event_manifest_path.as_posix()
        if event_manifest_path is not None
        else None,
        "provider": provider,
        "session_id": session_id,
        "pipeline_status": "not_run",
        "replay_status": "not_run",
        "adapter_status": "not_run",
        "ingestion_status": "not_run",
        "candidate_report_validation_status": "not_run",
        "export_readiness_status": "not_run",
        "feedback_collection_status": "not_run",
        "final_decision_readiness_status": "not_run",
        "go_evidence_status": "not_go_evidence_not_run",
        "artifact_write_status": "not_written",
        "audio_chunk_write_status": "not_written",
        "public_audio_download_status": "not_downloaded",
        "remote_asr_call_status": "not_called",
        "llm_call_status": "not_called",
        "real_mic_validation_status": "not_started_user_final_validation_required",
        "input_source_kind": "unverified_event_file",
        "event_manifest_status": "not_run",
        "event_provenance": None,
        "input_event_counts": None,
        "live_event_counts": None,
        "short_local_simulated_input_status": None,
        "evidence_span_count": 0,
        "state_event_count": 0,
        "suggestion_candidate_count": 0,
        "timeline_counts": None,
        "feedback_analysis": None,
        "replay_report": None,
        "candidate_report": None,
        "json_export_preview": None,
        "markdown_export_preview": None,
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _base_batch_report(*, provider: str) -> dict[str, Any]:
    return {
        "batch_runner_id": BATCH_RUNNER_ID,
        "runner_mode": BATCH_RUNNER_MODE,
        "runner_version": BATCH_RUNNER_VERSION,
        "execution_boundary": EXECUTION_BOUNDARY,
        "provider": provider,
        "batch_status": "not_run",
        "scenario_count": 0,
        "engineering_scenario_count": 0,
        "negative_control_count": 0,
        "preview_created_count": 0,
        "engineering_preview_created_count": 0,
        "negative_control_blocked_count": 0,
        "negative_control_fake_candidate_count": 0,
        "scenario_results": [],
        "failed_scenarios": [],
        "go_evidence_status": "not_go_evidence_not_run",
        "artifact_write_status": "not_written",
        "public_audio_download_status": "not_downloaded",
        "remote_asr_call_status": "not_called",
        "llm_call_status": "not_called",
        "real_mic_validation_status": "not_started_user_final_validation_required",
        "validation_errors": [],
        **_false_safety_flags(),
    }


def _merge_replay_summary(report: dict[str, Any], replay_report: dict[str, Any]) -> None:
    report["replay_status"] = replay_report.get("replay_status")
    report["input_source_kind"] = replay_report.get("input_source_kind")
    report["event_manifest_status"] = replay_report.get("event_manifest_status")
    report["event_provenance"] = replay_report.get("event_provenance")
    report["input_event_counts"] = replay_report.get("input_event_counts")
    report["live_event_counts"] = replay_report.get("live_event_counts")
    report["short_local_simulated_input_status"] = replay_report.get(
        "short_local_simulated_input_status"
    )
    report["evidence_span_count"] = int(replay_report.get("evidence_span_count") or 0)
    report["state_event_count"] = int(replay_report.get("state_event_count") or 0)
    report["suggestion_candidate_count"] = int(
        replay_report.get("suggestion_candidate_count") or 0
    )
    report["replay_report"] = replay_report


def _merge_adapter_summary(report: dict[str, Any], adapter_report: dict[str, Any]) -> None:
    report["adapter_status"] = adapter_report.get("adapter_status")
    report["candidate_report_validation_status"] = adapter_report.get(
        "candidate_report_validation_status"
    )
    report["candidate_report"] = adapter_report.get("candidate_report")
    candidate_report = report["candidate_report"]
    if isinstance(candidate_report, dict):
        audio_retention = candidate_report.get("audio_retention")
        if isinstance(audio_retention, dict):
            report["audio_chunk_write_status"] = audio_retention.get(
                "audio_chunk_write_status",
                "not_written",
            )


def _merge_ingestion_summary(report: dict[str, Any], ingestion_report: dict[str, Any]) -> None:
    report["ingestion_status"] = ingestion_report.get("ingestion_status")
    report["candidate_report_validation_status"] = ingestion_report.get(
        "candidate_report_validation_status"
    )
    report["timeline_counts"] = ingestion_report.get("timeline_counts")
    report["feedback_analysis"] = ingestion_report.get("feedback_analysis")
    report["feedback_collection_status"] = ingestion_report.get("feedback_collection_status")
    report["final_decision_readiness_status"] = ingestion_report.get(
        "final_decision_readiness_status"
    )
    report["export_readiness_status"] = ingestion_report.get("export_readiness_status")
    report["json_export_preview"] = ingestion_report.get("json_export_preview")
    report["markdown_export_preview"] = ingestion_report.get("markdown_export_preview")


def _go_evidence_status(report: dict[str, Any]) -> str:
    if report.get("export_readiness_status") == "ready_for_shadow_test_export":
        return "candidate_export_ready_but_requires_real_shadow_test_review"
    if report.get("export_readiness_status") == "draft_export_preview_only":
        return "not_go_evidence_replay_or_feedback_missing"
    return "not_go_evidence_pipeline_blocked"


def _blocked_status_from_adapter(adapter_report: dict[str, Any]) -> str:
    errors = adapter_report.get("validation_errors") or []
    if any("no candidate/card timeline" in str(error) for error in errors):
        return "blocked_by_no_candidate_timeline"
    return "blocked_by_shadow_report_draft"


def build_simulated_shadow_pipeline_smoke(
    *,
    events_path: Path,
    provider: str,
    session_id: str,
    event_manifest_path: Path | None = None,
) -> dict[str, Any]:
    report = _base_report(
        events_path=events_path,
        event_manifest_path=event_manifest_path,
        provider=provider,
        session_id=session_id,
    )

    replay_report = asr_live_pipeline_replay.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=event_manifest_path,
        provider=provider,
        session_id=session_id,
    )
    _merge_replay_summary(report, replay_report)
    if str(replay_report.get("replay_status", "")).startswith("blocked_"):
        report["pipeline_status"] = "blocked_by_replay"
        report["validation_errors"] = list(replay_report.get("validation_errors") or [])
        report["go_evidence_status"] = _go_evidence_status(report)
        return report

    adapter_report = replay_shadow_report_draft_adapter.build_replay_shadow_report_draft(
        replay_report=replay_report,
    )
    _merge_adapter_summary(report, adapter_report)
    if adapter_report.get("adapter_status") != "shadow_report_draft_created":
        report["pipeline_status"] = _blocked_status_from_adapter(adapter_report)
        report["validation_errors"] = list(adapter_report.get("validation_errors") or [])
        report["go_evidence_status"] = _go_evidence_status(report)
        return report

    ingestion_report = (
        shadow_report_ingestion_export_feedback.build_shadow_report_ingestion_export_feedback(
            candidate_report=adapter_report.get("candidate_report"),
        )
    )
    _merge_ingestion_summary(report, ingestion_report)
    if ingestion_report.get("ingestion_status") != "shadow_report_ingested_for_export_feedback":
        report["pipeline_status"] = "blocked_by_shadow_report_ingestion"
        report["validation_errors"] = list(ingestion_report.get("validation_errors") or [])
        report["go_evidence_status"] = _go_evidence_status(report)
        return report

    report["pipeline_status"] = "simulated_shadow_pipeline_preview_created"
    report["validation_errors"] = []
    report["go_evidence_status"] = _go_evidence_status(report)
    return report


def _candidate_card_count(smoke_report: dict[str, Any]) -> int:
    timeline_counts = smoke_report.get("timeline_counts")
    if isinstance(timeline_counts, dict):
        return int(timeline_counts.get("candidate_cards") or 0)
    return 0


def _transcript_segment_count(smoke_report: dict[str, Any]) -> int | None:
    timeline_counts = smoke_report.get("timeline_counts")
    if isinstance(timeline_counts, dict):
        return int(timeline_counts.get("transcript_segments") or 0)
    return None


def _scenario_summary(
    *,
    spec: dict[str, Any],
    smoke_report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "session_id": str(spec.get("session_id") or ""),
        "expected_kind": str(spec.get("expected_kind") or ""),
        "pipeline_status": smoke_report.get("pipeline_status"),
        "short_local_simulated_input_status": smoke_report.get(
            "short_local_simulated_input_status"
        ),
        "transcript_segments": _transcript_segment_count(smoke_report),
        "candidate_cards": _candidate_card_count(smoke_report),
        "go_evidence_status": smoke_report.get("go_evidence_status"),
        "validation_errors": list(smoke_report.get("validation_errors") or []),
    }


def _scenario_failure(summary: dict[str, Any]) -> dict[str, Any] | None:
    expected_kind = summary["expected_kind"]
    pipeline_status = summary["pipeline_status"]
    candidate_cards = int(summary.get("candidate_cards") or 0)
    if expected_kind == "engineering":
        if (
            pipeline_status == "simulated_shadow_pipeline_preview_created"
            and candidate_cards > 0
        ):
            return None
        return {
            "session_id": summary["session_id"],
            "expected_kind": expected_kind,
            "pipeline_status": pipeline_status,
            "failure_reason": "engineering_scenario_did_not_create_preview",
        }
    if expected_kind == "negative_control":
        if pipeline_status == "blocked_by_no_candidate_timeline" and candidate_cards == 0:
            return None
        return {
            "session_id": summary["session_id"],
            "expected_kind": expected_kind,
            "pipeline_status": pipeline_status,
            "failure_reason": "negative_control_created_candidate_or_preview",
        }
    return {
        "session_id": summary["session_id"],
        "expected_kind": expected_kind,
        "pipeline_status": pipeline_status,
        "failure_reason": "unknown_expected_kind",
    }


def build_simulated_shadow_pipeline_batch_smoke(
    *,
    scenario_specs: list[dict[str, Any]],
    provider: str = "mock_streaming",
) -> dict[str, Any]:
    report = _base_batch_report(provider=provider)
    scenario_results: list[dict[str, Any]] = []
    failed_scenarios: list[dict[str, Any]] = []

    for spec in scenario_specs:
        session_id = str(spec.get("session_id") or "")
        events_path = Path(str(spec.get("events_path") or ""))
        event_manifest = spec.get("event_manifest_path")
        smoke_report = build_simulated_shadow_pipeline_smoke(
            events_path=events_path,
            event_manifest_path=Path(str(event_manifest)) if event_manifest else None,
            provider=provider,
            session_id=session_id,
        )
        summary = _scenario_summary(spec=spec, smoke_report=smoke_report)
        scenario_results.append(summary)
        failure = _scenario_failure(summary)
        if failure is not None:
            failed_scenarios.append(failure)

    engineering_results = [
        item for item in scenario_results if item.get("expected_kind") == "engineering"
    ]
    negative_results = [
        item for item in scenario_results if item.get("expected_kind") == "negative_control"
    ]
    report["scenario_count"] = len(scenario_results)
    report["engineering_scenario_count"] = len(engineering_results)
    report["negative_control_count"] = len(negative_results)
    report["preview_created_count"] = sum(
        1
        for item in scenario_results
        if item.get("pipeline_status") == "simulated_shadow_pipeline_preview_created"
    )
    report["engineering_preview_created_count"] = sum(
        1
        for item in engineering_results
        if item.get("pipeline_status") == "simulated_shadow_pipeline_preview_created"
        and int(item.get("candidate_cards") or 0) > 0
    )
    report["negative_control_blocked_count"] = sum(
        1
        for item in negative_results
        if item.get("pipeline_status") == "blocked_by_no_candidate_timeline"
        and int(item.get("candidate_cards") or 0) == 0
    )
    report["negative_control_fake_candidate_count"] = sum(
        1
        for item in negative_results
        if int(item.get("candidate_cards") or 0) > 0
        or item.get("pipeline_status") == "simulated_shadow_pipeline_preview_created"
    )
    report["scenario_results"] = scenario_results
    report["failed_scenarios"] = failed_scenarios
    if failed_scenarios:
        report["batch_status"] = "failed_engineering_preview_or_negative_control"
        report["go_evidence_status"] = "not_go_evidence_pipeline_blocked"
    else:
        report["batch_status"] = "simulated_shadow_pipeline_batch_passed"
        report["go_evidence_status"] = "not_go_evidence_batch_replay_or_feedback_missing"
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-default-mock-events", action="store_true")
    parser.add_argument("--events-path", type=Path)
    parser.add_argument("--event-manifest-path", type=Path)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--session-id")
    args = parser.parse_args(argv)
    if args.batch_default_mock_events:
        if args.events_path or args.event_manifest_path or args.session_id:
            parser.error("--batch-default-mock-events cannot be combined with single-run inputs")
        if args.provider is None:
            args.provider = "mock_streaming"
        return args
    missing = [
        name
        for name, value in (
            ("--events-path", args.events_path),
            ("--provider", args.provider),
            ("--session-id", args.session_id),
        )
        if value is None
    ]
    if missing:
        parser.error("the following arguments are required: " + ", ".join(missing))
    return args


def main(argv: list[str] | None = None, *, out: TextIO = sys.stdout) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.batch_default_mock_events:
        batch_report = build_simulated_shadow_pipeline_batch_smoke(
            scenario_specs=DEFAULT_MOCK_SCENARIO_SPECS,
            provider=args.provider,
        )
        json.dump(batch_report, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0 if batch_report["batch_status"] == "simulated_shadow_pipeline_batch_passed" else 1
    report = build_simulated_shadow_pipeline_smoke(
        events_path=args.events_path,
        event_manifest_path=args.event_manifest_path,
        provider=args.provider,
        session_id=args.session_id,
    )
    json.dump(report, out, ensure_ascii=False, indent=2)
    out.write("\n")
    return 0 if report["pipeline_status"] == "simulated_shadow_pipeline_preview_created" else 1


if __name__ == "__main__":
    raise SystemExit(main())

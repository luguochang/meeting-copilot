import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "shadow_report_ingestion_export_feedback.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "shadow_report_ingestion_export_feedback",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_report(*, decision: str = "go", audio_written: bool = True) -> dict:
    feedback_labels = {
        "useful": 1 if decision == "go" else 0,
        "would_have_asked": 1 if decision == "go" else 0,
        "wrong": 0,
        "too_late": 0,
        "too_intrusive": 0,
        "dismissed": 0,
    }
    useful_count = feedback_labels["useful"] + feedback_labels["would_have_asked"]
    return {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": "shadow-test-api-review-001",
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": 24,
            "participant_count": 4,
            "language": "zh-CN",
            "domain_tags": ["api", "release"],
        },
        "transcript": {
            "segment_count": 2,
            "segments": [
                {
                    "segment_id": "seg-001",
                    "speaker_label": "speaker_1",
                    "start_ms": 0,
                    "end_ms": 4200,
                    "text": "这个接口的 request_id 和 rollback owner 还没定。",
                    "source_event_id": "event-final-001",
                },
                {
                    "segment_id": "seg-002",
                    "speaker_label": "speaker_2",
                    "start_ms": 4300,
                    "end_ms": 7600,
                    "text": "P99 和 40012 的监控也要补。",
                    "source_event_id": "event-final-002",
                },
            ],
        },
        "asr_metrics": {
            "duration_seconds": 1440,
            "first_partial_latency_ms": 420,
            "final_latency_p95_ms": 1800,
            "rtf": 0.18,
            "raw_cer": 0.12,
            "normalized_cer": 0.08,
            "raw_technical_entity_recall": 0.72,
            "normalized_technical_entity_recall": 0.84,
            "technical_entity_precision": 0.9,
            "error_event_count": 0,
            "end_of_stream_event_count": 1,
        },
        "evidence_span_timeline": [
            {
                "evidence_id": "ev-001",
                "segment_id": "seg-001",
                "start_ms": 0,
                "end_ms": 4200,
                "text": "request_id 和 rollback owner 还没定",
                "supports_candidate_id": "cand-001",
            }
        ],
        "state_timeline": [
            {
                "state_id": "state-001",
                "state_type": "open_question",
                "at_ms": 4300,
                "evidence_id": "ev-001",
            }
        ],
        "candidate_card_timeline": [
            {
                "candidate_id": "cand-001",
                "card_type": "engineering_gap",
                "created_at_ms": 6200,
                "latency_ms": 2000,
                "evidence_ids": ["ev-001"],
                "text": "确认 rollback owner 和 request_id 监控负责人。",
            }
        ],
        "feedback_summary": {
            "labels": feedback_labels,
            "useful_or_would_have_asked_count": useful_count,
            "negative_feedback_count": 0,
        },
        "final_decision": {
            "decision": decision,
            "reason": "Two timely evidence-backed cards were useful."
            if decision == "go"
            else "Replay draft has no real meeting feedback yet.",
        },
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
        "audio_retention": {
            "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
            "audio_chunk_write_status": "written_by_user_approved_shadow_test"
            if audio_written
            else "not_written",
            "audio_delete_status": "deleted_after_review"
            if audio_written
            else "not_applicable_no_audio_written",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "single shadow test cannot prove product-market fit",
        ],
    }


def replay_draft_candidate_report() -> dict:
    report = candidate_report(decision="inconclusive_requires_more_shadow_tests", audio_written=False)
    report["session_id"] = "replay-draft-api-review-001"
    report["feedback_summary"] = {
        "labels": {
            "useful": 0,
            "would_have_asked": 0,
            "wrong": 0,
            "too_late": 0,
            "too_intrusive": 0,
            "dismissed": 0,
        },
        "useful_or_would_have_asked_count": 0,
        "negative_feedback_count": 0,
    }
    return report


def test_shadow_report_ingestion_turns_replay_draft_into_export_preview_not_go_evidence():
    tool = load_tool_module()

    report = tool.build_shadow_report_ingestion_export_feedback(
        candidate_report=replay_draft_candidate_report(),
    )

    assert report["drv_id"] == "DRV-036"
    assert report["ingestion_status"] == "shadow_report_ingested_for_export_feedback"
    assert report["candidate_report_validation_status"] == "passed"
    assert report["export_readiness_status"] == "draft_export_preview_only"
    assert report["feedback_collection_status"] == "feedback_required_before_decision"
    assert report["final_decision_readiness_status"] == "inconclusive_requires_more_shadow_tests"
    assert report["timeline_counts"] == {
        "transcript_segments": 2,
        "evidence_spans": 1,
        "state_events": 1,
        "candidate_cards": 1,
    }
    assert report["feedback_analysis"] == {
        "candidate_card_count": 1,
        "useful_or_would_have_asked_count": 0,
        "negative_feedback_count": 0,
        "usefulness_ratio": 0.0,
        "negative_ratio": 0.0,
    }
    assert report["json_export_preview"]["session_id"] == "replay-draft-api-review-001"
    assert report["json_export_preview"]["final_decision"]["decision"] == (
        "inconclusive_requires_more_shadow_tests"
    )
    assert "Draft only; not real mic validation." in report["markdown_export_preview"]
    assert "确认 rollback owner" in report["markdown_export_preview"]
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_shadow_report_ingestion_marks_real_feedback_report_ready_for_export():
    tool = load_tool_module()

    report = tool.build_shadow_report_ingestion_export_feedback(
        candidate_report=candidate_report(),
    )

    assert report["ingestion_status"] == "shadow_report_ingested_for_export_feedback"
    assert report["export_readiness_status"] == "ready_for_shadow_test_export"
    assert report["feedback_collection_status"] == "feedback_collected"
    assert report["final_decision_readiness_status"] == "go_supported_by_feedback"
    assert report["feedback_analysis"]["usefulness_ratio"] == 1.0
    assert report["feedback_analysis"]["negative_ratio"] == 0.0
    assert report["json_export_preview"]["go_pivot_stop"]["decision"] == "go"
    assert "## Feedback Summary" in report["markdown_export_preview"]


def test_shadow_report_ingestion_blocks_invalid_candidate_report_without_export():
    tool = load_tool_module()
    invalid_report = candidate_report()
    invalid_report["privacy_cost_flags"]["remote_asr_called"] = True

    report = tool.build_shadow_report_ingestion_export_feedback(candidate_report=invalid_report)

    assert report["ingestion_status"] == "blocked_by_candidate_report_schema"
    assert report["candidate_report_validation_status"] == "failed"
    assert "privacy_cost_flags.remote_asr_called must remain false" in report[
        "candidate_report_validation_errors"
    ]
    assert report["json_export_preview"] is None
    assert report["markdown_export_preview"] is None


def test_shadow_report_ingestion_rejects_forbidden_report_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("candidate report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_shadow_report_ingestion_export_feedback(
        candidate_report_path="configs/local/shadow-report.json",
    )

    assert report["ingestion_status"] == "blocked_by_path_guard"
    assert report["candidate_report_read_status"] == "blocked"
    assert "candidate_report_path is blocked: configs/local" in report["validation_errors"]


def test_shadow_report_ingestion_accepts_drv035_adapter_report_path(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    report_dir = repo_root / "artifacts" / "tmp" / "asr_reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "replay-shadow-draft.json"
    report_path.write_text(
        json.dumps(
            {
                "adapter_id": "DRV-035",
                "adapter_status": "shadow_report_draft_created",
                "candidate_report_validation_status": "passed",
                "candidate_report": replay_draft_candidate_report(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_ingestion_export_feedback(
        candidate_report_path="artifacts/tmp/asr_reports/replay-shadow-draft.json",
    )

    assert report["candidate_report_path"] == "artifacts/tmp/asr_reports/replay-shadow-draft.json"
    assert report["source_report_kind"] == "drv035_adapter_report"
    assert report["ingestion_status"] == "shadow_report_ingested_for_export_feedback"


def test_shadow_report_ingestion_source_does_not_access_audio_network_or_processes():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "ffmpeg",
        "afconvert",
        "sounddevice",
        "pyaudio",
        "wave.open",
        "requests.",
        "urllib.request",
        "modelscope",
        "AutoModel",
        "getUserMedia",
        "MediaRecorder",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source

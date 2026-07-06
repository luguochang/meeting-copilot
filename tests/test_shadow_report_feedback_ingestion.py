import copy
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "shadow_report_feedback_ingestion.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "shadow_report_feedback_ingestion",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_report(*, audio_written: bool = True) -> dict:
    return {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": "shadow-test-api-review-038",
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
            },
            {
                "evidence_id": "ev-002",
                "segment_id": "seg-002",
                "start_ms": 4300,
                "end_ms": 7600,
                "text": "P99 和 40012 的监控也要补",
                "supports_candidate_id": "cand-002",
            },
        ],
        "state_timeline": [
            {
                "state_id": "state-001",
                "state_type": "open_question",
                "at_ms": 4300,
                "evidence_id": "ev-001",
            },
            {
                "state_id": "state-002",
                "state_type": "risk",
                "at_ms": 7600,
                "evidence_id": "ev-002",
            },
        ],
        "candidate_card_timeline": [
            {
                "candidate_id": "cand-001",
                "card_type": "engineering_gap",
                "created_at_ms": 6200,
                "latency_ms": 2000,
                "evidence_ids": ["ev-001"],
                "text": "确认 rollback owner 和 request_id 监控负责人。",
            },
            {
                "candidate_id": "cand-002",
                "card_type": "engineering_gap",
                "created_at_ms": 9200,
                "latency_ms": 1600,
                "evidence_ids": ["ev-002"],
                "text": "补齐 P99 和 40012 的监控阈值。",
            },
        ],
        "feedback_summary": {
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
        },
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Feedback has not been collected yet.",
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


def test_feedback_ingestion_updates_candidate_feedback_and_go_readiness():
    tool = load_tool_module()
    original = candidate_report(audio_written=True)

    report = tool.build_shadow_report_feedback_ingestion(
        candidate_report=original,
        feedback_entries=[
            {"candidate_id": "cand-001", "label": "useful"},
            {"candidate_id": "cand-002", "label": "would_have_asked"},
        ],
    )

    assert report["drv_id"] == "DRV-038"
    assert report["feedback_ingestion_status"] == "shadow_report_feedback_ingested"
    assert report["source_report_kind"] == "drv033_candidate_report"
    assert report["updated_candidate_report"]["feedback_summary"] == {
        "labels": {
            "useful": 1,
            "would_have_asked": 1,
            "wrong": 0,
            "too_late": 0,
            "too_intrusive": 0,
            "dismissed": 0,
        },
        "useful_or_would_have_asked_count": 2,
        "negative_feedback_count": 0,
    }
    assert report["updated_candidate_report"]["candidate_card_timeline"][0]["feedback_label"] == "useful"
    assert report["updated_candidate_report"]["candidate_card_timeline"][1]["feedback_label"] == "would_have_asked"
    assert report["updated_candidate_report"]["final_decision"]["decision"] == "go"
    assert report["readiness_report"]["final_decision_readiness_status"] == "go_supported_by_feedback"
    assert report["readiness_report"]["export_readiness_status"] == "ready_for_shadow_test_export"
    assert original["feedback_summary"]["useful_or_would_have_asked_count"] == 0
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_feedback_ingestion_keeps_replay_draft_inconclusive_even_with_positive_feedback():
    tool = load_tool_module()

    report = tool.build_shadow_report_feedback_ingestion(
        candidate_report=candidate_report(audio_written=False),
        feedback_entries=[
            {"candidate_id": "cand-001", "label": "useful"},
            {"candidate_id": "cand-002", "label": "would_have_asked"},
        ],
    )

    assert report["feedback_ingestion_status"] == "shadow_report_feedback_ingested_preview_only"
    assert report["updated_candidate_report"]["final_decision"]["decision"] == (
        "inconclusive_requires_more_shadow_tests"
    )
    assert report["readiness_report"]["export_readiness_status"] == "draft_export_preview_only"
    assert report["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"


def test_feedback_ingestion_blocks_unknown_candidate_and_invalid_label():
    tool = load_tool_module()

    unknown_candidate = tool.build_shadow_report_feedback_ingestion(
        candidate_report=candidate_report(),
        feedback_entries=[{"candidate_id": "cand-999", "label": "useful"}],
    )
    invalid_label = tool.build_shadow_report_feedback_ingestion(
        candidate_report=candidate_report(),
        feedback_entries=[{"candidate_id": "cand-001", "label": "kept"}],
    )

    assert unknown_candidate["feedback_ingestion_status"] == "blocked_by_feedback_validation"
    assert "feedback_entries[0].candidate_id must reference candidate_card_timeline" in unknown_candidate[
        "validation_errors"
    ]
    assert invalid_label["feedback_ingestion_status"] == "blocked_by_feedback_validation"
    assert "feedback_entries[0].label must be one of dismissed, too_intrusive, too_late, useful, would_have_asked, wrong" in invalid_label[
        "validation_errors"
    ]


def test_feedback_ingestion_blocks_duplicate_candidate_feedback():
    tool = load_tool_module()

    report = tool.build_shadow_report_feedback_ingestion(
        candidate_report=candidate_report(),
        feedback_entries=[
            {"candidate_id": "cand-001", "label": "useful"},
            {"candidate_id": "cand-001", "label": "wrong"},
        ],
    )

    assert report["feedback_ingestion_status"] == "blocked_by_feedback_validation"
    assert "feedback_entries must contain at most one label per candidate_id" in report["validation_errors"]


def test_feedback_ingestion_accepts_drv035_adapter_report_path(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    report_dir = repo_root / "artifacts" / "tmp" / "asr_reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "replay-shadow-draft.json"
    adapter_report = {
        "adapter_id": "DRV-035",
        "adapter_status": "shadow_report_draft_created",
        "candidate_report_validation_status": "passed",
        "candidate_report": candidate_report(audio_written=False),
    }
    report_path.write_text(json.dumps(adapter_report), encoding="utf-8")
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback.real_mic_shadow_test_report_schema, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_feedback_ingestion(
        candidate_report_path="artifacts/tmp/asr_reports/replay-shadow-draft.json",
        feedback_entries=[{"candidate_id": "cand-001", "label": "dismissed"}],
    )

    assert report["candidate_report_read_status"] == "read"
    assert report["candidate_report_path"] == "artifacts/tmp/asr_reports/replay-shadow-draft.json"
    assert report["source_report_kind"] == "drv035_adapter_report"
    assert report["updated_candidate_report"]["feedback_summary"]["labels"]["dismissed"] == 1


def test_feedback_ingestion_rejects_forbidden_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("candidate report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_shadow_report_feedback_ingestion(
        candidate_report_path="configs/local/shadow-report.json",
        feedback_entries=[{"candidate_id": "cand-001", "label": "useful"}],
    )

    assert report["feedback_ingestion_status"] == "blocked_by_path_guard"
    assert report["candidate_report_read_status"] == "blocked"
    assert "candidate_report_path is blocked: configs/local" in report["validation_errors"]


def test_feedback_ingestion_does_not_mutate_input_report():
    tool = load_tool_module()
    original = candidate_report()
    before = copy.deepcopy(original)

    tool.build_shadow_report_feedback_ingestion(
        candidate_report=original,
        feedback_entries=[{"candidate_id": "cand-001", "label": "wrong"}],
    )

    assert original == before

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "shadow_report_export_file_writer.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "shadow_report_export_file_writer",
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


def test_shadow_report_export_writer_writes_json_and_markdown_to_ignored_root(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    output_root = repo_root / "artifacts" / "tmp" / "shadow_report_exports"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_export_file_write(
        candidate_report=candidate_report(),
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["drv_id"] == "DRV-037"
    assert report["export_file_write_status"] == "written_to_ignored_artifact_root"
    assert report["source_export_readiness_status"] == "ready_for_shadow_test_export"
    assert report["output_root"] == "artifacts/tmp/shadow_report_exports"
    assert report["written_file_count"] == 2
    json_path = output_root / "shadow-test-api-review-001.shadow-report.json"
    markdown_path = output_root / "shadow-test-api-review-001.shadow-report.md"
    assert json_path.exists()
    assert markdown_path.exists()
    exported_json = json.loads(json_path.read_text(encoding="utf-8"))
    assert exported_json["session_id"] == "shadow-test-api-review-001"
    assert exported_json["go_pivot_stop"]["decision"] == "go"
    assert "## Feedback Summary" in markdown_path.read_text(encoding="utf-8")
    assert {
        item["kind"]: item["path"]
        for item in report["written_files"]
    } == {
        "json": "artifacts/tmp/shadow_report_exports/shadow-test-api-review-001.shadow-report.json",
        "markdown": "artifacts/tmp/shadow_report_exports/shadow-test-api-review-001.shadow-report.md",
    }
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_shadow_report_export_writer_allows_replay_draft_preview_but_marks_it_not_go_evidence(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_export_file_write(
        candidate_report=replay_draft_candidate_report(),
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["export_file_write_status"] == "written_to_ignored_artifact_root"
    assert report["source_export_readiness_status"] == "draft_export_preview_only"
    assert report["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    markdown_path = repo_root / report["written_files"][1]["path"]
    assert "Draft only; not real mic validation." in markdown_path.read_text(encoding="utf-8")


def test_shadow_report_export_writer_is_idempotent_when_files_match(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    first = tool.build_shadow_report_export_file_write(
        candidate_report=candidate_report(),
        output_root="artifacts/tmp/shadow_report_exports",
    )
    second = tool.build_shadow_report_export_file_write(
        candidate_report=candidate_report(),
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert first["export_file_write_status"] == "written_to_ignored_artifact_root"
    assert second["export_file_write_status"] == "idempotent_existing_files_match"
    assert second["written_file_count"] == 2


def test_shadow_report_export_writer_blocks_existing_conflict_without_overwrite(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    output_root = repo_root / "artifacts" / "tmp" / "shadow_report_exports"
    output_root.mkdir(parents=True)
    existing_json = output_root / "shadow-test-api-review-001.shadow-report.json"
    existing_json.write_text('{"different": true}\n', encoding="utf-8")
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_export_file_write(
        candidate_report=candidate_report(),
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["export_file_write_status"] == "blocked_by_existing_export_conflict"
    assert "existing export file differs" in report["validation_errors"]
    assert existing_json.read_text(encoding="utf-8") == '{"different": true}\n'


def test_shadow_report_export_writer_blocks_forbidden_output_root_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_ingested(*args, **kwargs):
        raise AssertionError("candidate report was ingested before output root guard")

    monkeypatch.setattr(
        tool.shadow_report_ingestion_export_feedback,
        "build_shadow_report_ingestion_export_feedback",
        fail_if_ingested,
    )

    report = tool.build_shadow_report_export_file_write(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/report.json",
        output_root="outputs/shadow_report_exports",
    )

    assert report["export_file_write_status"] == "blocked_by_output_root_guard"
    assert "output_root is blocked: outputs" in report["validation_errors"]


def test_shadow_report_export_writer_blocks_unsafe_session_id_without_writing(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    unsafe_report = candidate_report()
    unsafe_report["session_id"] = "../escape"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_report_export_file_write(
        candidate_report=unsafe_report,
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["export_file_write_status"] == "blocked_by_export_filename_guard"
    assert "session_id is not safe for export filename" in report["validation_errors"]
    assert not (repo_root / "artifacts" / "tmp" / "shadow_report_exports").exists()


def test_shadow_report_export_writer_source_does_not_access_audio_network_or_processes():
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

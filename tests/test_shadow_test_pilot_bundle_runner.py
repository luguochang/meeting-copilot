import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "shadow_test_pilot_bundle_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "shadow_test_pilot_bundle_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidate_report(*, audio_written=True, session_id="shadow-test-pilot-039") -> dict:
    return {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": session_id,
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


def write_candidate_report(repo_root: Path, report: dict, *, root="real_mic_shadow_reports") -> Path:
    report_dir = repo_root / "artifacts" / "tmp" / root
    report_dir.mkdir(parents=True)
    report_path = report_dir / f"{report['session_id']}.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def test_pilot_bundle_runner_applies_feedback_and_writes_go_exports(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    write_candidate_report(repo_root, candidate_report())
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_feedback_ingestion, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_export_file_writer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_export_file_writer.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_test_pilot_bundle(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/shadow-test-pilot-039.json",
        feedback_entries=[
            {"candidate_id": "cand-001", "label": "useful"},
            {"candidate_id": "cand-002", "label": "would_have_asked"},
        ],
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["drv_id"] == "DRV-039"
    assert report["pilot_bundle_status"] == "pilot_bundle_written"
    assert report["feedback_ingestion_status"] == "shadow_report_feedback_ingested"
    assert report["export_file_write_status"] == "written_to_ignored_artifact_root"
    assert report["go_evidence_status"] == "go_evidence_supported_by_real_feedback_report"
    assert report["final_decision"] == "go"
    assert report["written_file_count"] == 2
    assert {
        item["kind"]: item["path"]
        for item in report["bundle_artifacts"]
    } == {
        "json": "artifacts/tmp/shadow_report_exports/shadow-test-pilot-039.shadow-report.json",
        "markdown": "artifacts/tmp/shadow_report_exports/shadow-test-pilot-039.shadow-report.md",
    }
    exported = json.loads(
        (repo_root / "artifacts/tmp/shadow_report_exports/shadow-test-pilot-039.shadow-report.json").read_text(
            encoding="utf-8",
        )
    )
    assert exported["go_pivot_stop"]["decision"] == "go"
    assert exported["feedback_analysis"]["useful_or_would_have_asked_count"] == 2
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_pilot_bundle_runner_keeps_replay_draft_as_preview_not_go_evidence(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    draft = candidate_report(audio_written=False, session_id="replay-draft-pilot-039")
    write_candidate_report(repo_root, draft)
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_feedback_ingestion, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_export_file_writer, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_export_file_writer.shadow_report_ingestion_export_feedback, "REPO_ROOT", repo_root)

    report = tool.build_shadow_test_pilot_bundle(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/replay-draft-pilot-039.json",
        feedback_entries=[
            {"candidate_id": "cand-001", "label": "useful"},
            {"candidate_id": "cand-002", "label": "would_have_asked"},
        ],
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["pilot_bundle_status"] == "pilot_bundle_preview_written_not_go_evidence"
    assert report["feedback_ingestion_status"] == "shadow_report_feedback_ingested_preview_only"
    assert report["export_file_write_status"] == "written_to_ignored_artifact_root"
    assert report["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert report["final_decision"] == "inconclusive_requires_more_shadow_tests"


def test_pilot_bundle_runner_blocks_feedback_errors_without_writing_exports(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    write_candidate_report(repo_root, candidate_report())
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_feedback_ingestion, "REPO_ROOT", repo_root)
    monkeypatch.setattr(tool.shadow_report_export_file_writer, "REPO_ROOT", repo_root)

    report = tool.build_shadow_test_pilot_bundle(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/shadow-test-pilot-039.json",
        feedback_entries=[{"candidate_id": "cand-404", "label": "useful"}],
        output_root="artifacts/tmp/shadow_report_exports",
    )

    assert report["pilot_bundle_status"] == "blocked_by_feedback_ingestion"
    assert report["export_file_write_status"] is None
    assert "feedback_entries[0].candidate_id must reference candidate_card_timeline" in report[
        "validation_errors"
    ]
    assert not (repo_root / "artifacts/tmp/shadow_report_exports").exists()


def test_pilot_bundle_runner_blocks_output_root_before_reading_candidate(monkeypatch):
    tool = load_tool_module()

    def fail_if_feedback_ingested(*args, **kwargs):
        raise AssertionError("feedback ingestion ran before output root guard")

    monkeypatch.setattr(
        tool.shadow_report_feedback_ingestion,
        "build_shadow_report_feedback_ingestion",
        fail_if_feedback_ingested,
    )

    report = tool.build_shadow_test_pilot_bundle(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/shadow-test-pilot-039.json",
        feedback_entries=[{"candidate_id": "cand-001", "label": "useful"}],
        output_root="outputs/shadow_report_exports",
    )

    assert report["pilot_bundle_status"] == "blocked_by_output_root_guard"
    assert report["feedback_ingestion_status"] is None
    assert "output_root is blocked: outputs" in report["validation_errors"]


def test_pilot_bundle_runner_source_does_not_access_audio_network_or_processes():
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

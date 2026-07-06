import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_report_schema.py"

EXPECTED_REQUIRED_SECTIONS = [
    "schema_version",
    "session_id",
    "meeting_profile",
    "transcript",
    "asr_metrics",
    "evidence_span_timeline",
    "state_timeline",
    "candidate_card_timeline",
    "feedback_summary",
    "final_decision",
    "privacy_cost_flags",
    "audio_retention",
    "known_limitations",
]

EXPECTED_FEEDBACK_LABELS = [
    "useful",
    "would_have_asked",
    "wrong",
    "too_late",
    "too_intrusive",
    "dismissed",
]

EXPECTED_FALSE_FLAGS = [
    "safe_to_access_microphone_now",
    "safe_to_enumerate_audio_devices_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_real_user_audio_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunk_now",
    "safe_to_read_configs_local_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
    "safe_to_mutate_web_session_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "real_mic_shadow_test_report_schema",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_shadow_report() -> dict:
    return {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": "shadow-test-2026-07-03-001",
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": 24,
            "participant_count": 4,
            "language": "zh-CN",
            "domain_tags": ["api", "release", "monitoring"],
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
        },
        "final_decision": {
            "decision": "go",
            "reason": "Two timely evidence-backed cards were useful.",
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
            "audio_chunk_write_status": "written_by_user_approved_shadow_test",
            "audio_delete_status": "deleted_after_review",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "single shadow test cannot prove product-market fit",
        ],
    }


def test_default_schema_report_specifies_real_mic_shadow_test_contract_without_audio_access():
    tool = load_tool_module()

    report = tool.build_real_mic_shadow_test_report_schema()

    assert report["drv_id"] == "DRV-033"
    assert report["report_mode"] == "real_mic_shadow_test_report_schema"
    assert report["schema_version"] == "real_mic_shadow_test_report.v1"
    assert report["schema_status"] == "specified_not_executable"
    assert report["candidate_report_status"] == "not_provided"
    assert report["required_sections"] == EXPECTED_REQUIRED_SECTIONS
    assert report["feedback_labels"] == EXPECTED_FEEDBACK_LABELS
    assert report["final_decision_allowed_values"] == [
        "go",
        "pivot",
        "stop",
        "inconclusive_requires_more_shadow_tests",
    ]
    assert report["approved_pre_pilot_audio_chunk_root"] == (
        "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
    )
    assert report["execution_boundary"] == "schema_only_no_mic_no_audio_no_remote_calls"
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_tool_source_does_not_access_microphone_network_models_or_processes():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "import subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "cpal",
        "rodio",
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


def test_valid_candidate_report_passes_schema_without_reading_audio(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    report_dir = repo_root / "artifacts" / "tmp" / "real_mic_shadow_reports"
    report_dir.mkdir(parents=True)
    report_path = report_dir / "shadow-report.json"
    report_path.write_text(json.dumps(valid_shadow_report()), encoding="utf-8")
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)

    report = tool.build_real_mic_shadow_test_report_schema(
        candidate_report_path="artifacts/tmp/real_mic_shadow_reports/shadow-report.json",
    )

    assert report["candidate_report_path"] == "artifacts/tmp/real_mic_shadow_reports/shadow-report.json"
    assert report["candidate_report_status"] == "schema_validated_no_audio_access"
    assert report["candidate_report_validation_status"] == "passed"
    assert report["candidate_report_validation_errors"] == []
    assert report["candidate_report_summary"] == {
        "session_id": "shadow-test-2026-07-03-001",
        "duration_minutes": 24,
        "segment_count": 2,
        "evidence_span_count": 1,
        "state_event_count": 1,
        "candidate_card_count": 1,
        "useful_or_would_have_asked_count": 2,
        "negative_feedback_count": 0,
        "final_decision": "go",
    }
    assert report["safe_to_access_microphone_now"] is False
    assert report["safe_to_read_real_user_audio_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_call_llm_now"] is False


def test_candidate_report_accepts_user_approved_audio_retained_for_review():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["audio_retention"]["audio_delete_status"] = (
        "retained_in_ignored_artifact_root_for_user_review"
    )

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_validation_status"] == "passed"


def test_candidate_report_rejects_empty_timeline_items_without_auditable_schema():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["evidence_span_timeline"] = [{}]
    candidate["state_timeline"] = [{}]
    candidate["candidate_card_timeline"] = [{}]

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert "evidence_span_timeline[0].evidence_id must be a non-empty string" in report[
        "candidate_report_validation_errors"
    ]
    assert "evidence_span_timeline[0].segment_id must be a non-empty string" in report[
        "candidate_report_validation_errors"
    ]
    assert "state_timeline[0].state_id must be a non-empty string" in report[
        "candidate_report_validation_errors"
    ]
    assert "candidate_card_timeline[0].candidate_id must be a non-empty string" in report[
        "candidate_report_validation_errors"
    ]
    assert "candidate_card_timeline[0].evidence_ids must be a non-empty list of strings" in report[
        "candidate_report_validation_errors"
    ]


def test_candidate_report_rejects_broken_timeline_cross_references():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["evidence_span_timeline"][0]["segment_id"] = "missing-segment"
    candidate["state_timeline"][0]["evidence_id"] = "missing-evidence-for-state"
    candidate["candidate_card_timeline"][0]["evidence_ids"] = ["missing-evidence-for-card"]

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert "evidence_span_timeline[0].segment_id must reference transcript.segments" in report[
        "candidate_report_validation_errors"
    ]
    assert "state_timeline[0].evidence_id must reference evidence_span_timeline" in report[
        "candidate_report_validation_errors"
    ]
    assert "candidate_card_timeline[0].evidence_ids must reference evidence_span_timeline" in report[
        "candidate_report_validation_errors"
    ]


def test_candidate_report_rejects_missing_sections_bad_feedback_and_bad_decision():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    del candidate["evidence_span_timeline"]
    candidate["feedback_summary"]["labels"]["wrong"] = True
    candidate["final_decision"]["decision"] = "ship_it"

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert report["candidate_report_validation_status"] == "failed"
    assert "missing required section: evidence_span_timeline" in report[
        "candidate_report_validation_errors"
    ]
    assert "feedback label wrong must be a non-negative integer" in report[
        "candidate_report_validation_errors"
    ]
    assert "final_decision.decision must be one of go, pivot, stop, inconclusive_requires_more_shadow_tests" in report[
        "candidate_report_validation_errors"
    ]


def test_candidate_report_rejects_inconsistent_feedback_aggregates():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["feedback_summary"]["useful_or_would_have_asked_count"] = 5
    candidate["feedback_summary"]["labels"]["wrong"] = 1
    candidate["feedback_summary"]["labels"]["too_late"] = 1
    candidate["feedback_summary"]["negative_feedback_count"] = 0

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert (
        "feedback_summary.useful_or_would_have_asked_count must equal useful + would_have_asked"
        in report["candidate_report_validation_errors"]
    )
    assert (
        "feedback_summary.negative_feedback_count must equal wrong + too_late + too_intrusive"
        in report["candidate_report_validation_errors"]
    )


def test_candidate_report_rejects_go_decision_without_minimum_useful_feedback():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["feedback_summary"]["labels"]["useful"] = 0
    candidate["feedback_summary"]["labels"]["would_have_asked"] = 0
    candidate["feedback_summary"]["useful_or_would_have_asked_count"] = 0
    candidate["final_decision"]["decision"] = "go"

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert "final_decision.go requires useful_or_would_have_asked_count >= 2" in report[
        "candidate_report_validation_errors"
    ]


def test_candidate_report_rejects_unsafe_audio_retention_statuses_and_empty_limitations():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["audio_retention"]["audio_chunk_write_status"] = "unexpected_write_status"
    candidate["audio_retention"]["audio_delete_status"] = "not_deleted"
    candidate["audio_retention"]["retention_policy"] = "keep_forever"
    candidate["known_limitations"] = []

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert "audio_retention.audio_chunk_write_status must be one of not_written, written_by_user_approved_shadow_test" in report[
        "candidate_report_validation_errors"
    ]
    assert "audio_retention.audio_delete_status must be one of not_applicable_no_audio_written, deleted_after_review, retained_in_ignored_artifact_root_for_user_review" in report[
        "candidate_report_validation_errors"
    ]
    assert "audio_retention.retention_policy must be delete_audio_chunks_before_session_discard" in report[
        "candidate_report_validation_errors"
    ]
    assert "known_limitations must contain at least one limitation" in report[
        "candidate_report_validation_errors"
    ]


def test_candidate_report_rejects_privacy_and_cost_flags_that_would_break_boundary():
    tool = load_tool_module()
    candidate = valid_shadow_report()
    candidate["privacy_cost_flags"]["raw_audio_uploaded"] = True
    candidate["privacy_cost_flags"]["remote_asr_called"] = True
    candidate["privacy_cost_flags"]["configs_local_read"] = True
    candidate["audio_retention"]["audio_chunk_root"] = "data/local_runtime/private-audio"

    report = tool.build_real_mic_shadow_test_report_schema(candidate_report=candidate)

    assert report["candidate_report_status"] == "blocked_by_schema_validation"
    assert "privacy_cost_flags.raw_audio_uploaded must remain false" in report[
        "candidate_report_validation_errors"
    ]
    assert "privacy_cost_flags.remote_asr_called must remain false" in report[
        "candidate_report_validation_errors"
    ]
    assert "privacy_cost_flags.configs_local_read must remain false" in report[
        "candidate_report_validation_errors"
    ]
    assert (
        "audio_retention.audio_chunk_root must be artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks"
        in report["candidate_report_validation_errors"]
    )
    assert report["safe_to_read_real_user_audio_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_candidate_report_path_rejects_forbidden_roots_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("candidate report file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_real_mic_shadow_test_report_schema(
        candidate_report_path="configs/local/shadow-report.json",
    )

    assert report["candidate_report_status"] == "blocked_by_path_guard"
    assert report["candidate_report_read_status"] == "blocked"
    assert "candidate_report_path is blocked: configs/local" in report[
        "candidate_report_validation_errors"
    ]
    assert report["safe_to_read_configs_local_now"] is False


def test_candidate_report_path_rejects_all_forbidden_roots_before_read(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("candidate report file was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    forbidden_paths = [
        ("configs/local/shadow-report.json", "configs/local"),
        ("data/asr_eval/local_samples/shadow-report.json", "data/asr_eval/local_samples"),
        ("data/local_runtime/shadow-report.json", "data/local_runtime"),
        ("outputs/shadow-report.json", "outputs"),
    ]
    for path, label in forbidden_paths:
        report = tool.build_real_mic_shadow_test_report_schema(candidate_report_path=path)
        assert report["candidate_report_status"] == "blocked_by_path_guard"
        assert f"candidate_report_path is blocked: {label}" in report[
            "candidate_report_validation_errors"
        ]


def test_cli_outputs_schema_report_without_audio_access():
    tool = load_tool_module()
    out = io.StringIO()

    exit_code = tool.main([], out=out)

    assert exit_code == 0
    report = json.loads(out.getvalue())
    assert report["drv_id"] == "DRV-033"
    assert report["schema_status"] == "specified_not_executable"
    assert report["candidate_report_status"] == "not_provided"
    assert report["safe_to_access_microphone_now"] is False
    assert report["safe_to_read_real_user_audio_now"] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "Voice" + "Memos" not in report_json
    assert "." + "m4a" not in report_json

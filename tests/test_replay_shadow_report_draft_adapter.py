import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "replay_shadow_report_draft_adapter.py"
SHADOW_SCHEMA_PATH = REPO_ROOT / "tools" / "real_mic_shadow_test_report_schema.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "replay_shadow_report_draft_adapter",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_shadow_schema_module():
    spec = importlib.util.spec_from_file_location(
        "real_mic_shadow_test_report_schema",
        SHADOW_SCHEMA_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay_report() -> dict:
    return {
        "report_mode": "asr_live_pipeline_replay",
        "replay_status": "asr_events_replayed_to_live_pipeline",
        "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        "provider": "mock_streaming",
        "session_id": "api-review-001",
        "input_event_counts": {
            "partial": 1,
            "final": 2,
            "revision": 0,
            "error": 0,
            "end_of_stream": 1,
        },
        "evidence_span_count": 2,
        "state_event_count": 1,
        "suggestion_candidate_count": 1,
        "short_local_simulated_input_status": "closed_to_candidate_timeline",
        "input_source_kind": "mock_streaming",
        "event_manifest_status": "not_provided",
        "event_provenance": {
            "input_source_kind": "mock_streaming",
            "source_id": "synthetic_meeting_scripts",
            "script_id": "api-review-001",
            "sample_id": None,
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "mock_streaming_fixture",
        },
        "timeline_window_ms": {
            "first_input_at_ms": 0,
            "last_live_event_at_ms": 6100,
            "duration_ms": 6100,
        },
        "asr_metrics": {
            "final_or_revision_count": 2,
            "first_partial_latency_ms": 1100,
            "first_final_latency_ms": 3500,
            "stream_duration_ms": 6100,
        },
        "evidence_span_timeline": [
            {
                "evidence_id": "asr_ev_seg_001",
                "segment_id": "seg_001",
                "source_event_type": "transcript_final",
                "at_ms": 3500,
                "start_ms": 0,
                "end_ms": 3200,
                "text": "payment-gateway 先灰度 10%。",
                "status": "active",
            },
            {
                "evidence_id": "asr_ev_seg_002",
                "segment_id": "seg_002",
                "source_event_type": "transcript_final",
                "at_ms": 5900,
                "start_ms": 3200,
                "end_ms": 5600,
                "text": "回滚负责人是谁？",
                "status": "active",
            },
        ],
        "state_timeline": [
            {
                "state_event_id": "state-event-001",
                "target_type": "OpenQuestion",
                "target_id": "state-001",
                "state_event_type": "created",
                "at_ms": 5900,
                "evidence_span_ids": ["asr_ev_seg_002"],
                "summary": "回滚负责人是谁？",
            }
        ],
        "candidate_card_timeline": [
            {
                "candidate_id": "cand-001",
                "target_type": "OpenQuestion",
                "target_id": "state-001",
                "gap_rule_id": "rollback_owner_gap",
                "created_at_ms": 6100,
                "evidence_span_ids": ["asr_ev_seg_002"],
                "segment_batch": ["seg_002"],
                "llm_call_status": "not_called",
                "card_status": "not_created",
                "confidence": 0.8,
                "confidence_level": "medium",
                "degradation_reasons": [],
            }
        ],
        "validation_errors": [],
        "safe_to_call_llm_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_capture_microphone_now": False,
    }


def test_replay_shadow_report_draft_maps_replay_timeline_to_drv033_candidate_report():
    tool = load_tool_module()
    shadow_schema = load_shadow_schema_module()

    report = tool.build_replay_shadow_report_draft(replay_report=replay_report())

    assert report["adapter_id"] == "DRV-035"
    assert report["adapter_status"] == "shadow_report_draft_created"
    assert report["source_replay_status"] == "asr_events_replayed_to_live_pipeline"
    assert report["candidate_report_validation_status"] == "passed"
    candidate = report["candidate_report"]
    assert candidate["schema_version"] == "real_mic_shadow_test_report.v1"
    assert candidate["session_id"] == "replay-draft-api-review-001"
    assert candidate["meeting_profile"]["meeting_type"] == "chinese_technical_review"
    assert candidate["meeting_profile"]["language"] == "zh-CN"
    assert candidate["transcript"]["segment_count"] == 2
    assert candidate["transcript"]["segments"][0]["text"] == "payment-gateway 先灰度 10%。"
    assert candidate["asr_metrics"]["duration_seconds"] == 6.1
    assert candidate["asr_metrics"]["first_partial_latency_ms"] == 1100
    assert candidate["asr_metrics"]["final_latency_p95_ms"] == 3500
    assert candidate["asr_metrics"]["error_event_count"] == 0
    assert candidate["asr_metrics"]["end_of_stream_event_count"] == 1
    assert candidate["evidence_span_timeline"][0]["supports_candidate_id"] == "cand-001"
    assert candidate["evidence_span_timeline"][1]["supports_candidate_id"] == "cand-001"
    assert candidate["state_timeline"] == [
        {
            "state_id": "state-001",
            "state_type": "open_question",
            "at_ms": 5900,
            "evidence_id": "asr_ev_seg_002",
        }
    ]
    assert candidate["candidate_card_timeline"] == [
        {
            "candidate_id": "cand-001",
            "card_type": "engineering_gap",
            "created_at_ms": 6100,
            "latency_ms": 200,
            "evidence_ids": ["asr_ev_seg_002"],
            "text": "Draft from replay candidate rollback_owner_gap for OpenQuestion.",
        }
    ]
    assert candidate["feedback_summary"]["useful_or_would_have_asked_count"] == 0
    assert candidate["feedback_summary"]["negative_feedback_count"] == 0
    assert candidate["final_decision"]["decision"] == "inconclusive_requires_more_shadow_tests"
    assert candidate["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "user_audio_committed_to_repo": False,
    }
    assert candidate["audio_retention"]["audio_chunk_write_status"] == "not_written"
    assert candidate["audio_retention"]["audio_delete_status"] == "not_applicable_no_audio_written"
    assert shadow_schema.validate_candidate_report(candidate) == []
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_replay_shadow_report_draft_blocks_non_candidate_replay_without_fake_value():
    tool = load_tool_module()
    blocked_replay = replay_report() | {
        "short_local_simulated_input_status": "no_engineering_candidate_detected",
        "candidate_card_timeline": [],
        "suggestion_candidate_count": 0,
    }

    report = tool.build_replay_shadow_report_draft(replay_report=blocked_replay)

    assert report["adapter_status"] == "blocked_by_replay_not_candidate_ready"
    assert report["candidate_report"] is None
    assert "replay report has no candidate/card timeline" in report["validation_errors"]
    assert report["safe_to_access_microphone_now"] is False
    assert report["safe_to_read_real_user_audio_now"] is False
    assert report["safe_to_call_llm_now"] is False


def test_replay_shadow_report_draft_blocks_replay_with_side_effect_flags():
    tool = load_tool_module()
    unsafe_replay = replay_report() | {
        "safe_to_call_remote_asr_now": True,
        "safe_to_read_user_audio_now": True,
    }

    report = tool.build_replay_shadow_report_draft(replay_report=unsafe_replay)

    assert report["adapter_status"] == "blocked_by_replay_not_candidate_ready"
    assert report["candidate_report"] is None
    assert "replay report safe_to_call_remote_asr_now must be false" in report["validation_errors"]
    assert "replay report safe_to_read_user_audio_now must be false" in report["validation_errors"]


def test_replay_shadow_report_draft_blocks_replay_with_validation_errors():
    tool = load_tool_module()
    unsafe_replay = replay_report() | {
        "validation_errors": ["upstream replay path validation failed"],
    }

    report = tool.build_replay_shadow_report_draft(replay_report=unsafe_replay)

    assert report["adapter_status"] == "blocked_by_replay_not_candidate_ready"
    assert report["candidate_report"] is None
    assert "replay report validation_errors must be empty" in report["validation_errors"]


def test_replay_shadow_report_draft_rejects_forbidden_report_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("replay report was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_replay_shadow_report_draft(
        replay_report_path="configs/local/replay-report.json",
    )

    assert report["adapter_status"] == "blocked_by_path_guard"
    assert report["replay_report_read_status"] == "blocked"
    assert "replay_report_path is blocked: configs/local" in report["validation_errors"]


def test_replay_shadow_report_draft_source_does_not_access_audio_network_or_processes():
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

import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = REPO_ROOT / "code" / "desktop_tauri"
POLICY_PATH = DESKTOP_ROOT / "asr-worker-handoff-local-dry-run.policy.json"
TOOL_PATH = REPO_ROOT / "tools" / "desktop_asr_worker_handoff_local_dry_run.py"

EXPECTED_FALSE_FLAGS = [
    "safe_to_spawn_worker_now",
    "safe_to_start_worker_now",
    "safe_to_capture_audio_now",
    "safe_to_request_audio_permission_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_write_runtime_audio_now",
    "safe_to_write_runtime_session_now",
]


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "desktop_asr_worker_handoff_local_dry_run",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_descriptor() -> dict:
    return {
        "descriptor_version": "desktop_asr_worker_handoff_preflight.v1",
        "session_id": "desktop_handoff_local_dry_run",
        "provider": "sherpa_onnx_streaming",
        "event_file_path": "artifacts/tmp/asr_events/desktop-handoff-dry-run.events.json",
        "source_kind": "synthetic",
        "chunk_lifecycle": [
            {
                "chunk_id": "chunk_0001",
                "chunk_index": 0,
                "chunk_start_ms": 0,
                "chunk_end_ms": 1600,
                "source_kind": "synthetic",
            }
        ],
    }


def minimal_asr_events() -> list[dict]:
    return [
        {
            "event_type": "final",
            "segment_id": "dry_run_seg_001",
            "text": "API 回滚负责人还没有确认，P99 监控也要补。",
            "start_ms": 0,
            "end_ms": 2400,
            "received_at_ms": 2500,
            "confidence": 0.91,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "dry_run_eos",
            "text": "",
            "start_ms": 2400,
            "end_ms": 2500,
            "received_at_ms": 2500,
        },
    ]


def test_local_dry_run_policy_exists_and_defaults_to_preview_only():
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))

    assert policy["pcweb_id"] == "PCWEB-096"
    assert policy["policy_name"] == "Desktop ASR Worker Handoff Local Dry Run"
    assert policy["policy_status"] == "desktop_asr_worker_handoff_local_dry_run_policy_only"
    assert policy["default_quality_gate_status"] == "included_in_root_pytest"
    assert policy["default_mode"] == "preview_only"
    assert policy["allowed_modes"] == ["preview_only", "synthetic_local_test"]
    assert policy["handoff_api_endpoint"] == "/live/asr/local-event-files/sessions"
    assert policy["required_preflight_source"] == "PCWEB-095"
    assert policy["approved_event_file_root"] == "artifacts/tmp/asr_events"
    assert policy["approved_data_dir_root"] == "artifacts/tmp/desktop_handoff_dry_run"
    assert policy["synthetic_local_test_status"] == "explicit_mode_only"
    for flag in EXPECTED_FALSE_FLAGS:
        assert policy[flag] is False


def test_tool_source_does_not_spawn_processes_or_access_audio():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_snippets = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "sounddevice",
        "pyaudio",
        "AVAudioEngine",
        "AudioQueue",
        "modelscope",
        "AutoModel",
    ]
    for snippet in forbidden_snippets:
        assert snippet not in source
    assert "EXTERNAL_COMMAND_EXECUTION_FORBIDDEN = True" in source


def test_preview_only_builds_web_handoff_preview_without_reading_event_file(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="preview_only",
        repo_root=tmp_path,
    )

    assert report["pcweb_id"] == "PCWEB-096"
    assert report["report_mode"] == "desktop_asr_worker_handoff_local_dry_run"
    assert report["policy_validation_status"] == "passed"
    assert report["preflight_status"] == "ready_for_web_handoff_contract_review"
    assert report["dry_run_status"] == "preview_ready_no_web_mutation"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["web_handoff_response_status_code"] is None
    assert report["future_web_handoff_request_preview"] == {
        "session_id": "desktop_handoff_local_dry_run",
        "provider": "sherpa_onnx_streaming",
        "events_path": "artifacts/tmp/asr_events/desktop-handoff-dry-run.events.json",
    }
    assert report["safe_to_read_approved_asr_event_file_now"] is False
    for flag in EXPECTED_FALSE_FLAGS:
        assert report[flag] is False


def test_synthetic_local_test_calls_web_handoff_api_in_temp_data_dir(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    event_file = tmp_path / descriptor["event_file_path"]
    event_file.parent.mkdir(parents=True)
    event_file.write_text(json.dumps(minimal_asr_events(), ensure_ascii=False), encoding="utf-8")

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "data",
    )

    assert report["dry_run_status"] == "synthetic_web_handoff_passed"
    assert report["event_file_read_status"] == "read_by_web_handoff_api"
    assert report["web_handoff_mutation_status"] == "mutated_temp_web_session"
    assert report["web_handoff_response_status_code"] == 201
    assert report["web_handoff_response_summary"] == {
        "session_id": "desktop_handoff_local_dry_run",
        "ingest_mode": "local_asr_event_file",
        "event_source_provider": "sherpa_onnx_streaming",
        "transcript_final_count": 1,
        "evidence_span_count": 1,
        "state_event_count": 1,
        "scheduler_event_count": 1,
        "suggestion_candidate_count": 1,
        "llm_request_draft_count": 1,
        "suggestion_card_count": 0,
        "all_llm_statuses": ["not_called"],
        "worker_to_web_live_session_closure_status": "closed_to_evidence_state_gap",
    }
    assert report["safe_to_read_approved_asr_event_file_now"] is True
    assert report["safe_to_mutate_temp_web_session_now"] is True
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_download_models_now"] is False
    assert tool._load_web_app_module().REPO_ROOT == REPO_ROOT


def test_synthetic_local_test_blocks_mic_descriptor_before_web_api_call(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    descriptor["source_kind"] = "mic"
    descriptor["chunk_lifecycle"][0]["source_kind"] = "mic"

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "data",
    )

    assert report["dry_run_status"] == "blocked_by_preflight"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert "source_kind requires later approval: mic" in report["preflight_errors"]
    assert report["safe_to_read_approved_asr_event_file_now"] is False
    assert report["safe_to_mutate_temp_web_session_now"] is False


def test_synthetic_local_test_blocks_data_dir_outside_approved_temp_root(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    event_file = tmp_path / descriptor["event_file_path"]
    event_file.parent.mkdir(parents=True)
    event_file.write_text(json.dumps(minimal_asr_events(), ensure_ascii=False), encoding="utf-8")

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "configs" / "local" / "dry-run-data",
    )

    assert report["dry_run_status"] == "blocked_by_data_dir_validation"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["data_dir"] == "<redacted_invalid_path>"
    assert report["data_dir_validation_errors"] == [
        "data_dir is blocked: configs/local",
    ]


def test_synthetic_local_test_reports_web_handoff_422_without_success_flags(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "data",
    )

    assert report["dry_run_status"] == "blocked_by_web_handoff_response"
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["web_handoff_response_status_code"] == 422
    assert report["web_handoff_response_summary"]["detail"]["ingest_status"] == (
        "blocked_by_invalid_events_file"
    )
    assert report["safe_to_read_approved_asr_event_file_now"] is False
    assert report["safe_to_mutate_temp_web_session_now"] is False


def test_custom_policy_cannot_enable_worker_audio_or_remote_side_effects(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["safe_to_start_worker_now"] = True
    policy["safe_to_capture_audio_now"] = True
    policy["safe_to_call_remote_asr_now"] = True
    policy_path = tmp_path / "asr-worker-handoff-local-dry-run.policy.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=policy_path,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "data",
    )

    assert report["policy_validation_status"] == "failed"
    assert report["dry_run_status"] == "blocked_by_policy_validation"
    assert "safe_to_start_worker_now must be false" in report["policy_validation_errors"]
    assert "safe_to_capture_audio_now must be false" in report["policy_validation_errors"]
    assert "safe_to_call_remote_asr_now must be false" in report["policy_validation_errors"]
    assert report["event_file_read_status"] == "not_read"
    assert report["web_handoff_mutation_status"] == "not_mutated"
    assert report["safe_to_start_worker_now"] is False
    assert report["safe_to_capture_audio_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False


def test_cli_returns_nonzero_for_blocked_web_handoff_response():
    tool = load_tool_module()
    descriptor = valid_descriptor()
    descriptor["session_id"] = "desktop_handoff_cli_blocked"
    descriptor["event_file_path"] = "artifacts/tmp/asr_events/missing-pcweb-096.events.json"
    output = io.StringIO()

    exit_code = tool.main(
        [
            "--descriptor-json",
            json.dumps(descriptor),
            "--mode",
            "synthetic_local_test",
            "--data-dir",
            "artifacts/tmp/desktop_handoff_dry_run/test-main-exit",
        ],
        out=output,
    )
    report = json.loads(output.getvalue())

    assert exit_code == 1
    assert report["dry_run_status"] == "blocked_by_web_handoff_response"


def test_synthetic_local_test_blocks_when_handoff_does_not_create_evidence_state_gap(tmp_path):
    tool = load_tool_module()
    descriptor = valid_descriptor()
    event_file = tmp_path / descriptor["event_file_path"]
    event_file.parent.mkdir(parents=True)
    event_file.write_text(
        json.dumps(
            [
                {
                    "event_type": "final",
                    "segment_id": "dry_run_non_engineering_seg_001",
                    "text": "今天午饭订餐名单已经确认。",
                    "start_ms": 0,
                    "end_ms": 1800,
                    "received_at_ms": 1900,
                    "confidence": 0.93,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "dry_run_eos",
                    "text": "",
                    "start_ms": 1800,
                    "end_ms": 1900,
                    "received_at_ms": 1900,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = tool.build_desktop_asr_worker_handoff_local_dry_run_report(
        policy_path=POLICY_PATH,
        descriptor=descriptor,
        mode="synthetic_local_test",
        repo_root=tmp_path,
        data_dir=tmp_path / "artifacts" / "tmp" / "desktop_handoff_dry_run" / "data",
    )

    assert report["dry_run_status"] == "blocked_by_live_session_closure"
    assert report["event_file_read_status"] == "read_by_web_handoff_api"
    assert report["web_handoff_mutation_status"] == "mutated_temp_web_session"
    assert report["web_handoff_response_summary"]["transcript_final_count"] == 1
    assert report["web_handoff_response_summary"]["evidence_span_count"] == 1
    assert report["web_handoff_response_summary"]["state_event_count"] == 0
    assert report["web_handoff_response_summary"]["suggestion_candidate_count"] == 0
    assert report["web_handoff_response_summary"]["worker_to_web_live_session_closure_status"] == (
        "blocked_no_state_or_gap_candidate"
    )
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_capture_audio_now"] is False


def test_closure_summary_blocks_when_scheduler_or_llm_request_draft_is_missing():
    tool = load_tool_module()
    payload = {
        "session_id": "closure_missing_scheduler_or_draft",
        "ingest_mode": "local_asr_event_file",
        "event_source": {"provider": "sherpa_onnx_streaming"},
        "live_event_counts": {
            "transcript_final": 1,
            "state_event": 1,
            "scheduler_event": 0,
            "suggestion_candidate_event": 1,
            "llm_request_draft_event": 1,
            "suggestion_card": 0,
        },
        "live_events": [
            {
                "event_type": "transcript_final",
                "payload": {"evidence_spans": [{"id": "asr_ev_seg_001"}]},
            }
        ],
        "all_llm_statuses": ["not_called"],
    }

    missing_scheduler_summary = tool._summarize_web_handoff_response(payload)

    assert missing_scheduler_summary["evidence_span_count"] == 1
    assert missing_scheduler_summary["state_event_count"] == 1
    assert missing_scheduler_summary["scheduler_event_count"] == 0
    assert missing_scheduler_summary["suggestion_candidate_count"] == 1
    assert missing_scheduler_summary["llm_request_draft_count"] == 1
    assert missing_scheduler_summary["worker_to_web_live_session_closure_status"] == (
        "blocked_no_scheduler_event"
    )

    payload["live_event_counts"]["scheduler_event"] = 1
    payload["live_event_counts"]["llm_request_draft_event"] = 0

    missing_draft_summary = tool._summarize_web_handoff_response(payload)

    assert missing_draft_summary["scheduler_event_count"] == 1
    assert missing_draft_summary["llm_request_draft_count"] == 0
    assert missing_draft_summary["worker_to_web_live_session_closure_status"] == (
        "blocked_no_llm_request_draft"
    )

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "asr_live_pipeline_replay.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("asr_live_pipeline_replay", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_events(root: Path, relative_path: str, events: list[dict]) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return Path(relative_path)


def _write_json(root: Path, relative_path: str, payload: dict) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return Path(relative_path)


def _streaming_events() -> list[dict]:
    return [
        {
            "event_type": "partial",
            "segment_id": "seg_001",
            "text": "先灰度",
            "start_ms": 0,
            "end_ms": 1000,
            "received_at_ms": 1100,
            "confidence": 0.72,
        },
        {
            "event_type": "final",
            "segment_id": "seg_001",
            "text": "payment-gateway 先灰度 10%。",
            "start_ms": 0,
            "end_ms": 3200,
            "received_at_ms": 3500,
            "confidence": 0.91,
        },
        {
            "event_type": "final",
            "segment_id": "seg_002",
            "text": "回滚负责人是谁？",
            "start_ms": 3200,
            "end_ms": 5600,
            "received_at_ms": 5900,
            "confidence": 0.89,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "eos",
            "text": "",
            "start_ms": 5600,
            "end_ms": 5600,
            "received_at_ms": 6100,
        },
    ]


def test_replay_report_converts_asr_events_to_live_pipeline_without_llm_calls(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        _streaming_events(),
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        provider="sherpa_onnx_streaming",
        session_id="api-review-001",
    )

    assert report["report_mode"] == "asr_live_pipeline_replay"
    assert report["replay_status"] == "asr_events_replayed_to_live_pipeline"
    assert report["events_path"] == "artifacts/tmp/asr_events/api-review-001.sherpa.events.json"
    assert report["provider"] == "sherpa_onnx_streaming"
    assert report["session_id"] == "api-review-001"
    assert report["source"] == "live_asr_stream"
    assert report["trace_kind"] == "live_event"
    assert report["input_event_counts"] == {
        "partial": 1,
        "final": 2,
        "revision": 0,
        "error": 0,
        "end_of_stream": 1,
    }
    assert report["live_event_counts"]["transcript_partial"] == 1
    assert report["live_event_counts"]["transcript_final"] == 2
    assert report["live_event_counts"]["state_event"] >= 2
    assert report["live_event_counts"]["scheduler_event"] >= 2
    assert report["live_event_counts"]["suggestion_candidate_event"] >= 2
    assert report["live_event_counts"]["llm_request_draft_event"] >= 2
    assert report["live_event_counts"]["suggestion_card"] == 0
    assert report["evidence_span_count"] == 2
    assert report["state_event_count"] >= 2
    assert report["scheduler_event_count"] >= 2
    assert report["suggestion_candidate_count"] >= 2
    assert report["llm_request_draft_count"] >= 2
    assert report["all_llm_statuses"] == ["not_called"]
    assert report["formal_card_creation_status"] == "not_created"
    assert report["short_local_simulated_input_status"] == "closed_to_candidate_timeline"
    assert report["input_source_kind"] == "approved_synthetic_event_file"
    assert report["timeline_window_ms"] == {
        "first_input_at_ms": 0,
        "last_live_event_at_ms": 6100,
        "duration_ms": 6100,
    }
    assert report["asr_metrics"]["final_or_revision_count"] == 2
    assert report["asr_metrics"]["first_partial_latency_ms"] == 1100
    assert report["asr_metrics"]["first_final_latency_ms"] == 3500
    assert report["asr_metrics"]["stream_duration_ms"] == 6100
    assert report["evidence_span_timeline"] == [
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
    ]
    assert [item["target_type"] for item in report["state_timeline"]] == [
        "DecisionCandidate",
        "OpenQuestion",
    ]
    assert all(item["evidence_span_ids"] for item in report["state_timeline"])
    assert [item["card_status"] for item in report["candidate_card_timeline"]] == [
        "not_created",
        "not_created",
    ]
    assert all(
        item["llm_call_status"] == "not_called"
        for item in report["candidate_card_timeline"]
    )
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_read_user_audio_now"] is False
    assert report["safe_to_read_configs_local_now"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["validation_errors"] == []

    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json


def test_replay_report_keeps_non_engineering_control_at_zero_candidates(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json",
        [
            {
                "event_type": "partial",
                "segment_id": "seg_001",
                "text": "午饭订餐",
                "start_ms": 0,
                "end_ms": 900,
                "received_at_ms": 1000,
                "confidence": 0.9,
            },
            {
                "event_type": "final",
                "segment_id": "seg_001",
                "text": "今天午饭订餐名单已经确认。",
                "start_ms": 0,
                "end_ms": 2400,
                "received_at_ms": 2600,
                "confidence": 0.95,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "eos",
                "text": "",
                "start_ms": 2400,
                "end_ms": 2400,
                "received_at_ms": 2800,
            },
        ],
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        provider="mock_streaming",
        session_id="non-engineering-control-001",
    )

    assert report["short_local_simulated_input_status"] == "no_engineering_candidate_detected"
    assert report["evidence_span_count"] == 1
    assert report["state_event_count"] == 0
    assert report["suggestion_candidate_count"] == 0
    assert report["evidence_span_timeline"][0]["text"] == "今天午饭订餐名单已经确认。"
    assert report["state_timeline"] == []
    assert report["candidate_card_timeline"] == []
    assert report["safe_to_call_llm_now"] is False


def test_replay_report_uses_event_manifest_provenance_for_public_audio_sample(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/alimeeting-eval-001.public.events.json",
        _streaming_events(),
    )
    manifest_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/alimeeting-eval-001.provenance.json",
        {
            "manifest_version": "asr_event_provenance.v1",
            "events_path": "artifacts/tmp/asr_events/alimeeting-eval-001.public.events.json",
            "input_source_kind": "public_audio_sample",
            "source_id": "alimeeting_openslr_slr119",
            "sample_id": "alimeeting-eval-001",
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "manual_transcript_sidecar_simulator",
            "safe_to_call_llm": False,
            "safe_to_call_remote_asr": False,
            "safe_to_capture_microphone": False,
            "safe_to_read_user_audio": False,
            "safe_to_download_public_audio": False,
        },
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="alimeeting-eval-001",
    )

    assert report["replay_status"] == "asr_events_replayed_to_live_pipeline"
    assert report["event_manifest_status"] == "loaded"
    assert report["event_manifest_path"] == (
        "artifacts/tmp/asr_events/alimeeting-eval-001.provenance.json"
    )
    assert report["input_source_kind"] == "public_audio_sample"
    assert report["event_provenance"] == {
        "manifest_version": "asr_event_provenance.v1",
        "events_path": "artifacts/tmp/asr_events/alimeeting-eval-001.public.events.json",
        "input_source_kind": "public_audio_sample",
        "source_id": "alimeeting_openslr_slr119",
        "script_id": None,
        "sample_id": "alimeeting-eval-001",
        "provider_candidate": "mock_streaming",
        "event_contract_version": "asr_streaming_events.v1",
        "generated_by": "manual_transcript_sidecar_simulator",
    }
    assert report["safe_to_call_llm_now"] is False
    assert report["safe_to_call_remote_asr_now"] is False
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_read_user_audio_now"] is False
    report_json = json.dumps(report, ensure_ascii=False)
    assert str(tmp_path) not in report_json
    assert "/Users/" not in report_json


def test_replay_report_blocks_event_manifest_with_side_effect_flags(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _streaming_events(),
    )
    manifest_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.provenance.json",
        {
            "manifest_version": "asr_event_provenance.v1",
            "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
            "input_source_kind": "mock_streaming",
            "source_id": "synthetic_meeting_scripts",
            "script_id": "api-review-001",
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "mock_streaming_fixture",
            "safe_to_call_llm": True,
            "safe_to_call_remote_asr": False,
            "safe_to_capture_microphone": False,
            "safe_to_read_user_audio": False,
            "safe_to_download_public_audio": False,
        },
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["replay_status"] == "blocked_by_event_manifest_validation"
    assert report["event_manifest_status"] == "blocked"
    assert report["input_source_kind"] == "unverified_event_file"
    assert "event_manifest.safe_to_call_llm must be false" in report["validation_errors"]
    assert report["input_event_counts"] == {
        "partial": 0,
        "final": 0,
        "revision": 0,
        "error": 0,
        "end_of_stream": 0,
    }
    assert report["safe_to_call_llm_now"] is False


def test_replay_report_blocks_event_manifest_with_local_path_provenance(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _streaming_events(),
    )
    manifest_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.provenance.json",
        {
            "manifest_version": "asr_event_provenance.v1",
            "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
            "input_source_kind": "mock_streaming",
            "source_id": "synthetic_meeting_scripts",
            "script_id": "api-review-001",
            "sample_id": "/Users/chase/private-audio.wav",
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "mock_streaming_fixture",
            "safe_to_call_llm": False,
            "safe_to_call_remote_asr": False,
            "safe_to_capture_microphone": False,
            "safe_to_read_user_audio": False,
            "safe_to_download_public_audio": False,
        },
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["replay_status"] == "blocked_by_event_manifest_validation"
    assert report["event_manifest_status"] == "blocked"
    assert "event_manifest.sample_id must not contain local path text" in report["validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "private-audio" not in report_json


def test_replay_report_blocks_event_manifest_with_forbidden_relative_path_provenance(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _streaming_events(),
    )
    manifest_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.provenance.json",
        {
            "manifest_version": "asr_event_provenance.v1",
            "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
            "input_source_kind": "mock_streaming",
            "source_id": "configs/local/asr-provider.json",
            "script_id": "api-review-001",
            "sample_id": "data/asr_eval/local_samples/private.m4a",
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "mock_streaming_fixture",
            "safe_to_call_llm": False,
            "safe_to_call_remote_asr": False,
            "safe_to_capture_microphone": False,
            "safe_to_read_user_audio": False,
            "safe_to_download_public_audio": False,
        },
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["replay_status"] == "blocked_by_event_manifest_validation"
    assert report["event_manifest_status"] == "blocked"
    assert "event_manifest.source_id must not contain local path text" in report["validation_errors"]
    assert "event_manifest.sample_id must not contain local path text" in report["validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "configs/local" not in report_json
    assert "local_samples" not in report_json
    assert ".m4a" not in report_json
    assert "private" not in report_json


def test_replay_report_blocks_event_manifest_with_backslash_path_provenance(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _streaming_events(),
    )
    manifest_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.provenance.json",
        {
            "manifest_version": "asr_event_provenance.v1",
            "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
            "input_source_kind": "mock_streaming",
            "source_id": "synthetic_meeting_scripts",
            "script_id": "api-review-001",
            "sample_id": r"data\asr_eval\local_samples\private.wav",
            "provider_candidate": "mock_streaming",
            "event_contract_version": "asr_streaming_events.v1",
            "generated_by": "mock_streaming_fixture",
            "safe_to_call_llm": False,
            "safe_to_call_remote_asr": False,
            "safe_to_capture_microphone": False,
            "safe_to_read_user_audio": False,
            "safe_to_download_public_audio": False,
        },
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["replay_status"] == "blocked_by_event_manifest_validation"
    assert report["event_manifest_status"] == "blocked"
    assert "event_manifest.sample_id must not contain local path text" in report["validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "data\\\\asr_eval" not in report_json
    assert "private" not in report_json


def test_replay_report_rejects_forbidden_event_manifest_paths_before_reading(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _streaming_events(),
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        event_manifest_path=Path("configs/local/event-provenance.json"),
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["replay_status"] == "blocked_by_event_manifest_path_validation"
    assert report["event_manifest_status"] == "blocked"
    assert report["event_manifest_path"] == "<redacted_invalid_path>"
    assert report["validation_errors"] == ["event_manifest path is blocked: configs/local"]
    assert report["safe_to_read_configs_local_now"] is False


def test_replay_report_blocks_stream_without_final_segments(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/partial-only.sherpa.events.json",
        [
            {
                "event_type": "partial",
                "segment_id": "seg_001",
                "text": "还在识别",
                "start_ms": 0,
                "end_ms": 1000,
                "received_at_ms": 1100,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "eos",
                "text": "",
                "start_ms": 1000,
                "end_ms": 1000,
                "received_at_ms": 1200,
            },
        ],
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        provider="sherpa_onnx_streaming",
        session_id="partial-only",
    )

    assert report["replay_status"] == "blocked_by_no_final_or_revision_events"
    assert "stream contains no final or revision events" in report["validation_errors"]
    assert report["evidence_span_count"] == 0
    assert report["safe_to_call_llm_now"] is False


def test_replay_report_returns_contract_blocker_for_unsupported_event(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    events_path = _write_events(
        tmp_path,
        "artifacts/tmp/asr_events/bad-provider.events.json",
        [
            {
                "event_type": "word",
                "segment_id": "seg_001",
                "text": "unsupported",
                "start_ms": 0,
                "end_ms": 1000,
                "received_at_ms": 1000,
            }
        ],
    )

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=events_path,
        provider="bad_provider",
        session_id="bad-provider",
    )

    assert report["replay_status"] == "blocked_by_event_contract"
    assert report["validation_errors"] == ["unsupported ASR streaming event_type: word"]
    assert report["live_event_counts"] == {}
    assert report["safe_to_call_llm_now"] is False


def test_replay_report_rejects_forbidden_event_paths_before_reading(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)

    forbidden_roots = {
        ("configs", "local"): "configs/local",
        ("data", "asr_eval", "local_samples"): "data/asr_eval/local_samples",
        ("data", "local_runtime"): "data/local_runtime",
        ("outputs",): "outputs",
    }

    for suffix_parts, label in forbidden_roots.items():
        forbidden_path = tmp_path.joinpath(*suffix_parts) / "events.json"

        report = tool.build_asr_live_pipeline_replay_report(
            events_path=forbidden_path,
            provider="sherpa_onnx_streaming",
            session_id="forbidden",
        )

        assert report["replay_status"] == "blocked_by_path_validation"
        assert report["events_path"] == "<redacted_invalid_path>"
        assert report["validation_errors"] == [f"events path is blocked: {label}"]
        assert report["safe_to_call_llm_now"] is False


def test_replay_report_rejects_allowed_path_symlink_to_forbidden_root(tmp_path, monkeypatch):
    tool = load_tool_module()
    monkeypatch.setattr(tool, "REPO_ROOT", tmp_path)
    visible_root = tmp_path / "artifacts" / "tmp" / "asr_events"
    forbidden_root = tmp_path / "outside" / "configs" / "local"
    visible_root.mkdir(parents=True)
    forbidden_root.mkdir(parents=True)
    target = forbidden_root / "events.json"
    target.write_text(json.dumps(_streaming_events(), ensure_ascii=False), encoding="utf-8")
    link = visible_root / "linked.events.json"
    link.symlink_to(target)

    report = tool.build_asr_live_pipeline_replay_report(
        events_path=Path("artifacts/tmp/asr_events/linked.events.json"),
        provider="sherpa_onnx_streaming",
        session_id="linked",
    )

    assert report["replay_status"] == "blocked_by_path_validation"
    assert report["validation_errors"] == ["events path is blocked: configs/local"]
    assert report["safe_to_call_llm_now"] is False


def test_replay_tool_source_has_no_remote_or_process_execution_entrypoints():
    source = TOOL_PATH.read_text(encoding="utf-8")

    forbidden_source_tokens = [
        "subprocess",
        "os.system",
        "Popen",
        "check_call",
        "check_output",
        "requests.",
        "httpx.",
        "urllib.",
        "run(",
        "exec(",
        "eval(",
    ]
    for token in forbidden_source_tokens:
        assert token not in source

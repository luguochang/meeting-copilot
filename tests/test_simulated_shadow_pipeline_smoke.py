import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "simulated_shadow_pipeline_smoke.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "simulated_shadow_pipeline_smoke",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(root: Path, relative_path: str, payload) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return Path(relative_path)


def _candidate_events() -> list[dict]:
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


def _non_engineering_events() -> list[dict]:
    return [
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
    ]


def _patch_repo_roots(tool, monkeypatch, root: Path) -> None:
    monkeypatch.setattr(tool.asr_live_pipeline_replay, "REPO_ROOT", root)
    monkeypatch.setattr(tool.replay_shadow_report_draft_adapter, "REPO_ROOT", root)
    monkeypatch.setattr(tool.shadow_report_ingestion_export_feedback, "REPO_ROOT", root)


def test_simulated_shadow_pipeline_smoke_creates_draft_export_preview_from_candidate_events(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _candidate_events(),
    )

    report = tool.build_simulated_shadow_pipeline_smoke(
        events_path=events_path,
        provider="mock_streaming",
        session_id="api-review-001",
    )

    assert report["runner_id"] == "DRV-041"
    assert report["pipeline_status"] == "simulated_shadow_pipeline_preview_created"
    assert report["replay_status"] == "asr_events_replayed_to_live_pipeline"
    assert report["adapter_status"] == "shadow_report_draft_created"
    assert report["ingestion_status"] == "shadow_report_ingested_for_export_feedback"
    assert report["candidate_report_validation_status"] == "passed"
    assert report["export_readiness_status"] == "draft_export_preview_only"
    assert report["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert report["artifact_write_status"] == "not_written"
    assert report["audio_chunk_write_status"] == "not_written"
    assert report["public_audio_download_status"] == "not_downloaded"
    assert report["remote_asr_call_status"] == "not_called"
    assert report["llm_call_status"] == "not_called"
    assert report["real_mic_validation_status"] == "not_started_user_final_validation_required"
    assert report["timeline_counts"]["transcript_segments"] == 2
    assert report["timeline_counts"]["candidate_cards"] >= 1
    assert report["json_export_preview"]["session_id"] == "replay-draft-api-review-001"
    assert "Draft only; not real mic validation." in report["markdown_export_preview"]
    assert report["candidate_report"]["audio_retention"]["audio_chunk_write_status"] == "not_written"
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_simulated_shadow_pipeline_smoke_blocks_non_engineering_control_without_fake_card(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json",
        _non_engineering_events(),
    )

    report = tool.build_simulated_shadow_pipeline_smoke(
        events_path=events_path,
        provider="mock_streaming",
        session_id="non-engineering-control-001",
    )

    assert report["pipeline_status"] == "blocked_by_no_candidate_timeline"
    assert report["replay_status"] == "asr_events_replayed_to_live_pipeline"
    assert report["short_local_simulated_input_status"] == "no_engineering_candidate_detected"
    assert report["adapter_status"] == "blocked_by_replay_not_candidate_ready"
    assert report["candidate_report"] is None
    assert report["json_export_preview"] is None
    assert report["markdown_export_preview"] is None
    assert "replay report has no candidate/card timeline" in report["validation_errors"]
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_simulated_shadow_pipeline_smoke_preserves_public_audio_provenance_without_download(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/alimeeting-eval-001.public.events.json",
        _candidate_events(),
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

    report = tool.build_simulated_shadow_pipeline_smoke(
        events_path=events_path,
        event_manifest_path=manifest_path,
        provider="mock_streaming",
        session_id="alimeeting-eval-001",
    )

    assert report["pipeline_status"] == "simulated_shadow_pipeline_preview_created"
    assert report["input_source_kind"] == "public_audio_sample"
    assert report["event_manifest_status"] == "loaded"
    assert report["event_provenance"]["source_id"] == "alimeeting_openslr_slr119"
    assert report["public_audio_download_status"] == "not_downloaded"
    assert report["safe_to_download_public_audio_now"] is False


def test_simulated_shadow_pipeline_smoke_rejects_forbidden_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("forbidden events path was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_simulated_shadow_pipeline_smoke(
        events_path=Path("configs/local/private.events.json"),
        provider="mock_streaming",
        session_id="blocked-session",
    )

    assert report["pipeline_status"] == "blocked_by_replay"
    assert report["replay_status"] == "blocked_by_path_validation"
    assert report["adapter_status"] == "not_run"
    assert report["ingestion_status"] == "not_run"
    assert report["candidate_report"] is None
    assert "events path is blocked: configs/local" in report["validation_errors"]
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json


def test_simulated_shadow_pipeline_smoke_main_exits_zero_only_for_preview(tmp_path, monkeypatch, capsys):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    events_path = _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _candidate_events(),
    )

    exit_code = tool.main(
        [
            "--events-path",
            str(events_path),
            "--provider",
            "mock_streaming",
            "--session-id",
            "api-review-001",
        ]
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["pipeline_status"] == "simulated_shadow_pipeline_preview_created"

    blocked_exit_code = tool.main(
        [
            "--events-path",
            "configs/local/private.events.json",
            "--provider",
            "mock_streaming",
            "--session-id",
            "blocked-session",
        ]
    )
    assert blocked_exit_code == 1


def test_simulated_shadow_pipeline_batch_smoke_passes_engineering_and_negative_control(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _candidate_events(),
    )
    _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/release-review-001.mock.events.json",
        _candidate_events(),
    )
    _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json",
        _non_engineering_events(),
    )

    report = tool.build_simulated_shadow_pipeline_batch_smoke(
        scenario_specs=[
            {
                "session_id": "api-review-001",
                "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
                "expected_kind": "engineering",
            },
            {
                "session_id": "release-review-001",
                "events_path": "artifacts/tmp/asr_events/release-review-001.mock.events.json",
                "expected_kind": "engineering",
            },
            {
                "session_id": "non-engineering-control-001",
                "events_path": (
                    "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json"
                ),
                "expected_kind": "negative_control",
            },
        ],
        provider="mock_streaming",
    )

    assert report["batch_runner_id"] == "DRV-042"
    assert report["batch_status"] == "simulated_shadow_pipeline_batch_passed"
    assert report["scenario_count"] == 3
    assert report["engineering_scenario_count"] == 2
    assert report["negative_control_count"] == 1
    assert report["engineering_preview_created_count"] == 2
    assert report["negative_control_blocked_count"] == 1
    assert report["negative_control_fake_candidate_count"] == 0
    assert report["go_evidence_status"] == "not_go_evidence_batch_replay_or_feedback_missing"
    assert report["artifact_write_status"] == "not_written"
    assert report["public_audio_download_status"] == "not_downloaded"
    assert report["remote_asr_call_status"] == "not_called"
    assert report["llm_call_status"] == "not_called"
    assert report["failed_scenarios"] == []
    assert [item["session_id"] for item in report["scenario_results"]] == [
        "api-review-001",
        "release-review-001",
        "non-engineering-control-001",
    ]
    assert [item["pipeline_status"] for item in report["scenario_results"]] == [
        "simulated_shadow_pipeline_preview_created",
        "simulated_shadow_pipeline_preview_created",
        "blocked_by_no_candidate_timeline",
    ]
    assert report["scenario_results"][0]["candidate_cards"] >= 1
    assert report["scenario_results"][2]["candidate_cards"] == 0
    for flag in tool.FALSE_SAFETY_FLAGS:
        assert report[flag] is False


def test_simulated_shadow_pipeline_batch_smoke_fails_when_engineering_does_not_preview(
    tmp_path,
    monkeypatch,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/api-review-001.mock.events.json",
        _non_engineering_events(),
    )

    report = tool.build_simulated_shadow_pipeline_batch_smoke(
        scenario_specs=[
            {
                "session_id": "api-review-001",
                "events_path": "artifacts/tmp/asr_events/api-review-001.mock.events.json",
                "expected_kind": "engineering",
            },
        ],
        provider="mock_streaming",
    )

    assert report["batch_status"] == "failed_engineering_preview_or_negative_control"
    assert report["engineering_preview_created_count"] == 0
    assert report["failed_scenarios"] == [
        {
            "session_id": "api-review-001",
            "expected_kind": "engineering",
            "pipeline_status": "blocked_by_no_candidate_timeline",
            "failure_reason": "engineering_scenario_did_not_create_preview",
        }
    ]
    assert report["go_evidence_status"] == "not_go_evidence_pipeline_blocked"
    assert report["safe_to_capture_microphone_now"] is False
    assert report["safe_to_call_llm_now"] is False


def test_simulated_shadow_pipeline_batch_smoke_blocks_forbidden_path_before_reading(monkeypatch):
    tool = load_tool_module()

    def fail_if_read(*args, **kwargs):
        raise AssertionError("forbidden batch events path was read before path guard")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    report = tool.build_simulated_shadow_pipeline_batch_smoke(
        scenario_specs=[
            {
                "session_id": "blocked-session",
                "events_path": "configs/local/private.events.json",
                "expected_kind": "engineering",
            }
        ],
        provider="mock_streaming",
    )

    assert report["batch_status"] == "failed_engineering_preview_or_negative_control"
    assert report["scenario_results"][0]["pipeline_status"] == "blocked_by_replay"
    assert report["failed_scenarios"][0]["session_id"] == "blocked-session"
    assert report["failed_scenarios"][0]["failure_reason"] == (
        "engineering_scenario_did_not_create_preview"
    )
    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json


def test_simulated_shadow_pipeline_batch_smoke_main_runs_default_mock_scenarios(
    tmp_path,
    monkeypatch,
    capsys,
):
    tool = load_tool_module()
    _patch_repo_roots(tool, monkeypatch, tmp_path)
    for session_id in [
        "api-review-001",
        "architecture-review-001",
        "incident-review-001",
        "release-review-001",
    ]:
        _write_json(
            tmp_path,
            f"artifacts/tmp/asr_events/{session_id}.mock.events.json",
            _candidate_events(),
        )
    _write_json(
        tmp_path,
        "artifacts/tmp/asr_events/non-engineering-control-001.mock.events.json",
        _non_engineering_events(),
    )

    exit_code = tool.main(["--batch-default-mock-events"])

    assert exit_code == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["batch_status"] == "simulated_shadow_pipeline_batch_passed"
    assert report["scenario_count"] == 5
    assert report["engineering_preview_created_count"] == 4
    assert report["negative_control_blocked_count"] == 1

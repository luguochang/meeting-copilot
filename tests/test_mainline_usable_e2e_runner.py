import importlib.util
import io
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "mainline_usable_e2e_runner.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "mainline_usable_e2e_runner",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_runner_executes_mainline_and_writes_traceable_reports(tmp_path):
    tool = load_tool_module()
    output_root = tool.REPO_ROOT / "artifacts/tmp/mainline_selftests"
    session_id = "m15_contract_review_retention"

    report = tool.run_mainline_usable_e2e_selftest(
        session_id=session_id,
        repo_root=tool.REPO_ROOT,
        output_root=output_root,
        run_browser_smoke=False,
    )

    assert report["report_mode"] == "mainline_usable_e2e_selftest"
    assert report["overall_status"] == "mainline_product_chain_exercised_with_expected_blockers"
    assert report["audio_health"]["health_status"] == "audio_capture_health_passed"
    assert report["mainline_trial"]["trial_status"] == "mainline_trial_session_created"
    assert report["mainline_trial"]["asr_quality_exit_status"] == "not_exited"
    assert report["live_asr"]["event_counts"]["transcript_final"] >= 1
    assert report["live_asr"]["event_counts"]["state_event"] >= 1
    assert report["live_asr"]["event_counts"]["suggestion_candidate_event"] >= 1
    assert report["live_asr"]["event_counts"]["llm_request_draft_event"] >= 1
    assert report["draft_review"]["draft_status"] == "draft_review_created"
    assert report["draft_review"]["contains_state_candidates"] is True
    assert report["draft_review"]["formal_report_status"] == "formal_report_preview_created"
    assert report["copilot_report_preview"]["preview_status"] == "copilot_report_preview_created"
    assert report["copilot_report_preview"]["is_formal_go_evidence"] is False
    assert report["copilot_report_preview"]["value_chain"] == [
        "transcript",
        "evidence_span",
        "meeting_state",
        "suggestion_candidate",
        "llm_request_draft",
        "feedback_export_preview",
    ]
    assert report["copilot_report_preview"]["suggestion_candidate_count"] >= 1
    assert report["copilot_report_preview"]["quality_blockers"] == [
        "blocked_by_funasr_smoke_assembly_input_guard",
        "not_real_meeting_go_evidence",
    ]
    assert report["closure"]["closure_status"] == "mainline_trial_feedback_export_preview_created"
    assert report["closure"]["final_decision"] == "inconclusive_requires_more_shadow_tests"
    assert report["closure"]["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert report["system_audio_capture"]["capture_adapter_status"] == "preflight_only_not_capturing"
    assert report["system_audio_capture"]["safe_to_capture_system_audio_now"] is False
    assert report["artifact_retention"]["retention_status"] == "local_artifacts_retained"
    assert report["artifact_retention"]["retained_artifact_count"] == 3
    assert report["gap_summary"]["implemented_and_verified"] >= 5
    assert report["gap_summary"]["blocked_by_asr_quality"] >= 1
    assert report["gap_summary"]["blocked_requires_m2_system_audio_capture"] >= 1
    assert report["gap_summary"]["blocked_requires_explicit_user_approval"] >= 1

    actual_json_path = output_root / f"{session_id}.mainline-usable-e2e.json"
    actual_markdown_path = output_root / f"{session_id}.mainline-usable-e2e.md"
    assert report["artifacts"]["json_report_path"] == (
        f"artifacts/tmp/mainline_selftests/{session_id}.mainline-usable-e2e.json"
    )
    assert report["artifacts"]["markdown_report_path"] == (
        f"artifacts/tmp/mainline_selftests/{session_id}.mainline-usable-e2e.md"
    )
    assert actual_json_path.exists()
    assert actual_markdown_path.exists()
    assert json.loads(actual_json_path.read_text(encoding="utf-8"))["session_id"] == session_id
    markdown = actual_markdown_path.read_text(encoding="utf-8")
    assert "Mainline Usable E2E Self-Test" in markdown
    assert "Copilot Report Preview" in markdown
    assert "formal_report_preview_created" in markdown
    assert "mainline_product_chain_exercised_with_expected_blockers" in markdown
    assert "blocked_by_asr_quality" in markdown
    assert "Artifact retention" in markdown


def test_runner_report_has_no_remote_or_private_side_effects(tmp_path):
    tool = load_tool_module()

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_safety_review",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
    )

    assert report["privacy_cost_flags"] == {
        "raw_audio_uploaded": False,
        "remote_asr_called": False,
        "llm_called": False,
        "configs_local_read": False,
        "private_user_audio_read": False,
        "paid_provider_used": False,
    }
    serialized = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "configs/local" not in serialized
    assert ("Voice" + "Memos") not in serialized
    assert ".m4a" not in serialized
    assert report["browser_smoke"]["browser_smoke_status"] == "not_requested"


def test_runner_can_ingest_blocked_asr_quality_decision_evidence(tmp_path):
    tool = load_tool_module()
    asr_quality_decision = {
        "decision_mode": "asr_quality_decision_gate",
        "decision_id": "DRV-032",
        "decision_version": "asr_quality_decision_gate.v1",
        "execution_mode": "decision_only_no_asr_execution_no_download",
        "decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
        "quality_exit_status": "not_exited",
        "funasr_smoke_assembly_status": "drv044_batch_evidence_blocked",
        "funasr_smoke_assembly_input_errors": [
            "engineering normalized_recall must be >= 0.8",
        ],
        "can_unblock_real_mic_shadow_test_quality_gate": False,
        "counts_as_asr_quality_go_evidence": False,
        "blocked_reasons": [
            "engineering normalized_recall must be >= 0.8",
        ],
        "safe_to_run_funasr_smoke_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_extract_public_audio_now": False,
        "safe_to_call_public_audio_asr_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_run_cargo_tauri_now": False,
    }

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_asr_quality_blocked_evidence",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        asr_quality_decision=asr_quality_decision,
    )

    production_asr_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "production_asr_quality"
    )
    assert report["asr_quality"]["source_status"] == "provided_asr_quality_decision_report"
    assert report["asr_quality"]["decision_status"] == "blocked_by_funasr_smoke_assembly_input_guard"
    assert report["asr_quality"]["quality_exit_status"] == "not_exited"
    assert report["asr_quality"]["blocked_reasons"] == [
        "engineering normalized_recall must be >= 0.8",
    ]
    assert production_asr_gap["status"] == "blocked_by_asr_quality"
    assert "engineering normalized_recall" in production_asr_gap["detail"]
    assert report["copilot_report_preview"]["quality_blockers"] == [
        "blocked_by_funasr_smoke_assembly_input_guard",
        "not_real_meeting_go_evidence",
    ]


def test_runner_can_handoff_approved_asr_event_artifact(tmp_path):
    tool = load_tool_module()
    events_path = tool.REPO_ROOT / "artifacts/tmp/asr_events/m15_runner_handoff.events.json"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            [
                {
                    "event_type": "final",
                    "segment_id": "handoff_001",
                    "text": "我们先确认 payment-gateway 的 rollback owner。",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "received_at_ms": 3500,
                    "confidence": 0.92,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "handoff_eos",
                    "text": "",
                    "start_ms": 3000,
                    "end_ms": 3000,
                    "received_at_ms": 3600,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_runner_handoff",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        asr_events_path=Path("artifacts/tmp/asr_events/m15_runner_handoff.events.json"),
        asr_events_provider="sherpa_onnx_streaming",
    )

    handoff_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "asr_event_artifact_handoff"
    )
    assert report["asr_event_handoff"]["handoff_status"] == (
        "local_asr_event_file_handoff_created"
    )
    assert report["asr_event_handoff"]["events_path"] == (
        "artifacts/tmp/asr_events/m15_runner_handoff.events.json"
    )
    assert report["asr_event_handoff"]["event_source"]["is_mock"] is False
    assert report["asr_event_handoff"]["live_event_counts"]["transcript_final"] >= 1
    assert report["asr_event_handoff"]["safe_to_call_remote_asr_now"] is False
    assert report["asr_event_handoff"]["safe_to_call_llm_now"] is False
    assert handoff_gap["status"] == "implemented_and_verified"


def test_runner_uses_asr_event_artifact_as_mainline_trial_source(tmp_path):
    tool = load_tool_module()
    events_path = tool.REPO_ROOT / "artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            [
                {
                    "event_type": "final",
                    "segment_id": "artifact_mainline_001",
                    "text": "我们先确认 payment-gateway 的 rollback owner。",
                    "start_ms": 0,
                    "end_ms": 3000,
                    "received_at_ms": 3500,
                    "confidence": 0.92,
                },
                {
                    "event_type": "final",
                    "segment_id": "artifact_mainline_002",
                    "text": "谁确认降级开关 owner？feature flag 半夜触发时值班群怎么通知？",
                    "start_ms": 4000,
                    "end_ms": 6500,
                    "received_at_ms": 7000,
                    "confidence": 0.91,
                },
                {
                    "event_type": "final",
                    "segment_id": "artifact_mainline_003",
                    "text": "如果 Redis cluster 缓存穿透打到 MySQL，P99 超过 900ms 就触发 rollback。",
                    "start_ms": 8000,
                    "end_ms": 12500,
                    "received_at_ms": 13000,
                    "confidence": 0.91,
                },
                {
                    "event_type": "final",
                    "segment_id": "artifact_mainline_004",
                    "text": "李四明天补充 idempotency-key 重试和 callback 失败的兼容测试。",
                    "start_ms": 14000,
                    "end_ms": 17500,
                    "received_at_ms": 18000,
                    "confidence": 0.91,
                },
                {
                    "event_type": "end_of_stream",
                    "segment_id": "artifact_mainline_eos",
                    "text": "",
                    "start_ms": 17500,
                    "end_ms": 17500,
                    "received_at_ms": 18100,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_runner_artifact_mainline",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        asr_events_path=Path("artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json"),
        asr_events_provider="local_artifact_asr",
    )

    artifact_closure_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "asr_event_artifact_closure"
    )
    assert report["mainline_trial"]["trial_id"] == "mainline_asr_event_artifact_trial"
    assert report["mainline_trial"]["trial_status"] == "mainline_artifact_trial_session_created"
    assert report["mainline_trial"]["mainline_decision_id"] == "DEC-214"
    assert report["mainline_trial"]["source_event_artifact_status"] == (
        "local_asr_event_file_handoff_created"
    )
    assert report["live_asr"]["event_counts"]["transcript_final"] == 4
    assert report["closure"]["source_trial_id"] == "mainline_asr_event_artifact_trial"
    assert report["closure"]["source_event_artifact_status"] == (
        "local_asr_event_file_handoff_created"
    )
    assert report["closure"]["closure_status"] == (
        "mainline_trial_feedback_export_preview_created"
    )
    assert artifact_closure_gap["status"] == "implemented_and_verified"
    assert report["privacy_cost_flags"]["remote_asr_called"] is False
    assert report["privacy_cost_flags"]["llm_called"] is False


def test_runner_reports_local_shadow_preview_release_readiness(tmp_path):
    tool = load_tool_module()
    asr_quality_decision_path = (
        tool.REPO_ROOT
        / "artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json"
    )
    asr_quality_decision = json.loads(asr_quality_decision_path.read_text(encoding="utf-8"))

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="local_shadow_preview_release_readiness_test",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        asr_quality_decision=asr_quality_decision,
        asr_events_path=Path("artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json"),
        asr_events_provider="local_artifact_asr",
    )

    readiness = report["local_shadow_preview_release_readiness"]
    assert readiness["release_tier"] == "local_shadow_preview"
    assert readiness["demo_preview_ready"] is True
    assert readiness["shadow_pilot_ready"] is False
    assert readiness["production_mvp_ready"] is False
    assert readiness["asr_quality_exit_status"] == "not_exited"
    assert readiness["asr_quality_decision_status"] == (
        "blocked_by_funasr_smoke_assembly_input_guard"
    )
    assert readiness["real_mic_readiness_status"] == (
        "blocked_not_ready_for_user_real_mic_shadow_test"
    )
    assert readiness["user_can_start_real_mic_shadow_test_now"] is False
    assert readiness["llm_execution_status"] == "disabled_not_called"
    assert readiness["formal_card_status"] == "not_created_in_current_mainline_preview"
    assert readiness["formal_report_status"] == "preview_only_not_real_meeting_go_evidence"
    assert readiness["go_evidence_status"] == "not_go_evidence_replay_or_feedback_missing"
    assert readiness["allowed_claim"] == "local synthetic/replay/artifact Copilot preview"
    assert readiness["forbidden_claims"] == [
        "real meeting ready",
        "production ASR ready",
        "production MVP ready",
        "background microphone capture ready",
    ]
    assert readiness["release_blockers"] == [
        "asr_quality_exit_not_passed",
        "real_mic_shadow_test_blocked",
        "desktop_real_audio_capture_not_enabled",
        "llm_execution_disabled",
        "formal_cards_not_created_in_current_mainline_preview",
    ]
    assert readiness["next_valid_actions"] == [
        "p0_local_shadow_preview_truthful_packaging",
        "p1_asr_quality_exit_or_pivot",
        "p2_user_authorized_shadow_pilot_after_p1",
    ]
    assert readiness["safety_flags"]["safe_to_capture_microphone_now"] is False
    assert readiness["safety_flags"]["safe_to_capture_system_audio_now"] is False
    assert readiness["safety_flags"]["safe_to_call_remote_asr_now"] is False
    assert readiness["safety_flags"]["safe_to_call_llm_now"] is False
    assert readiness["safety_flags"]["safe_to_read_configs_local_now"] is False


def test_cli_loads_asr_quality_decision_path_from_approved_artifact(tmp_path):
    tool = load_tool_module()
    out = io.StringIO()
    decision_path = tool.REPO_ROOT / "artifacts/tmp/asr_reports/m15_cli_asr_quality.json"
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text(
        json.dumps(
            {
                "decision_mode": "asr_quality_decision_gate",
                "decision_id": "DRV-032",
                "decision_version": "asr_quality_decision_gate.v1",
                "execution_mode": "decision_only_no_asr_execution_no_download",
                "decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
                "quality_exit_status": "not_exited",
                "can_unblock_real_mic_shadow_test_quality_gate": False,
                "counts_as_asr_quality_go_evidence": False,
                "blocked_reasons": ["engineering normalized_recall must be >= 0.8"],
                "safe_to_run_funasr_smoke_now": False,
                "safe_to_download_models_now": False,
                "safe_to_download_public_audio_now": False,
                "safe_to_extract_public_audio_now": False,
                "safe_to_call_public_audio_asr_now": False,
                "safe_to_capture_microphone_now": False,
                "safe_to_read_user_audio_now": False,
                "safe_to_read_configs_local_now": False,
                "safe_to_call_remote_asr_now": False,
                "safe_to_call_llm_now": False,
                "safe_to_run_cargo_tauri_now": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = tool.main(
        [
            "--session-id",
            "m15_cli_asr_quality",
            "--output-root",
            str(tmp_path / "artifacts/tmp/mainline_selftests"),
            "--asr-quality-decision-path",
            "artifacts/tmp/asr_reports/m15_cli_asr_quality.json",
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["asr_quality"]["source_path"] == (
        "artifacts/tmp/asr_reports/m15_cli_asr_quality.json"
    )
    assert payload["asr_quality"]["source_status"] == "provided_asr_quality_decision_report"
    assert payload["asr_quality"]["blocked_reasons"] == [
        "engineering normalized_recall must be >= 0.8",
    ]


def test_cli_blocks_asr_quality_decision_path_outside_approved_artifacts(tmp_path):
    tool = load_tool_module()
    out = io.StringIO()

    exit_code = tool.main(
        [
            "--session-id",
            "m15_cli_asr_quality_blocked_path",
            "--output-root",
            str(tmp_path / "artifacts/tmp/mainline_selftests"),
            "--asr-quality-decision-path",
            "configs/local/asr-quality.json",
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["asr_quality"]["source_status"] == "blocked_by_asr_quality_path_guard"
    assert payload["asr_quality"]["decision_status"] == "blocked_by_asr_quality_decision_input_guard"
    assert payload["asr_quality"]["validation_errors"] == [
        "asr_quality_decision_path is blocked: configs/local",
    ]
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "configs/local/asr-quality.json" not in serialized


def test_runner_redacts_outside_repo_artifact_paths(tmp_path):
    tool = load_tool_module()

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_outside_artifacts",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "outside-repo-artifacts",
        run_browser_smoke=False,
    )

    assert report["artifacts"]["json_report_path"] == "<redacted_outside_repo_path>"
    assert report["artifacts"]["markdown_report_path"] == "<redacted_outside_repo_path>"
    artifacts_serialized = json.dumps(report["artifacts"], ensure_ascii=False)
    assert "/private/var" not in artifacts_serialized
    assert "/var/folders" not in artifacts_serialized
    assert "/Users/" not in artifacts_serialized


def test_browser_smoke_output_redacts_local_absolute_paths(tmp_path):
    tool = load_tool_module()

    def fake_browser_smoke_runner():
        return {
            "browser_smoke_status": "passed",
            "stdout_tail": 'dataDir="/var/folders/fr/example/meeting-copilot"',
            "stderr_tail": "log=/Users/chase/tmp/meeting-copilot.log",
            "safe_to_start_browser_now": True,
        }

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_browser_redaction",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=True,
        browser_smoke_runner=fake_browser_smoke_runner,
    )

    browser_smoke_serialized = json.dumps(report["browser_smoke"], ensure_ascii=False)
    assert "/var/folders" not in browser_smoke_serialized
    assert "/Users/" not in browser_smoke_serialized
    assert "<redacted_local_path>" in report["browser_smoke"]["stdout_tail"]
    assert "<redacted_local_path>" in report["browser_smoke"]["stderr_tail"]


def test_runner_can_mark_system_audio_capture_verified_when_m2_health_gate_passes(tmp_path):
    tool = load_tool_module()
    system_audio_capture = {
        "report_mode": "mac_system_audio_capture_adapter",
        "schema_version": "mac_system_audio_capture_adapter.v1",
        "capture_adapter_status": "system_audio_capture_health_passed",
        "capture_backend": "ffmpeg_avfoundation_explicit_device",
        "recommended_route": "virtual_system_audio_device_first",
        "screen_capturekit_status": "future_native_path_not_implemented",
        "audio_path": "artifacts/tmp/audio_health/system.wav",
        "capture": {
            "capture_status": "recorded_from_system_audio_device",
            "audio_path": "artifacts/tmp/audio_health/system.wav",
            "record_seconds": 12,
        },
        "audio_health": {
            "health_status": "audio_capture_health_passed",
            "audio_path": "artifacts/tmp/audio_health/system.wav",
        },
        "m2_go_evidence_status": "not_real_meeting_go_evidence",
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "private_user_audio_read": False,
            "paid_provider_used": False,
        },
    }

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_m2_verified",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        system_audio_capture=system_audio_capture,
    )

    mac_capture_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "mac_system_audio_capture"
    )
    production_asr_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "production_asr_quality"
    )
    real_meeting_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "real_meeting_go_evidence"
    )
    assert mac_capture_gap["status"] == "implemented_and_verified"
    assert "system_audio_capture_health_passed" in mac_capture_gap["detail"]
    assert production_asr_gap["status"] == "blocked_by_asr_quality"
    assert real_meeting_gap["status"] == "blocked_requires_explicit_user_approval"
    assert report["system_audio_capture"] == system_audio_capture


def test_runner_requires_m1_audio_health_to_clear_system_audio_capture_gap(tmp_path):
    tool = load_tool_module()
    system_audio_capture = {
        "report_mode": "mac_system_audio_capture_adapter",
        "schema_version": "mac_system_audio_capture_adapter.v1",
        "capture_adapter_status": "system_audio_capture_health_passed",
        "capture_backend": "ffmpeg_avfoundation_explicit_device",
        "recommended_route": "virtual_system_audio_device_first",
        "screen_capturekit_status": "future_native_path_not_implemented",
        "audio_path": "artifacts/tmp/audio_health/system.wav",
        "capture": {
            "capture_status": "recorded_from_system_audio_device",
            "audio_path": "artifacts/tmp/audio_health/system.wav",
            "record_seconds": 12,
        },
        "audio_health": {
            "health_status": "blocked_audio_too_quiet",
            "audio_path": "artifacts/tmp/audio_health/system.wav",
        },
        "m2_go_evidence_status": "not_real_meeting_go_evidence",
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "private_user_audio_read": False,
            "paid_provider_used": False,
        },
    }

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_m2_health_required",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        system_audio_capture=system_audio_capture,
    )

    mac_capture_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "mac_system_audio_capture"
    )
    assert mac_capture_gap["status"] == "blocked_by_audio_capture_health"
    assert "blocked_audio_too_quiet" in mac_capture_gap["detail"]


def test_runner_explicit_system_audio_capture_uses_adapter_and_health_gate(monkeypatch, tmp_path):
    tool = load_tool_module()
    calls = []

    def fake_build_system_audio_capture_health_report(*, audio_path, repo_root, capture):
        calls.append(
            {
                "audio_path": audio_path,
                "repo_root": repo_root,
                "capture": capture,
            }
        )
        return {
            "report_mode": "mac_system_audio_capture_adapter",
            "schema_version": "mac_system_audio_capture_adapter.v1",
            "capture_adapter_status": "system_audio_capture_health_passed",
            "capture_backend": "ffmpeg_avfoundation_explicit_device",
            "recommended_route": "virtual_system_audio_device_first",
            "screen_capturekit_status": "future_native_path_not_implemented",
            "audio_path": "artifacts/tmp/audio_health/m15_system_audio_capture.system-audio-health.wav",
            "capture": capture,
            "audio_health": {
                "health_status": "audio_capture_health_passed",
                "audio_path": "artifacts/tmp/audio_health/m15_system_audio_capture.system-audio-health.wav",
            },
            "m2_go_evidence_status": "not_real_meeting_go_evidence",
            "privacy_cost_flags": {
                "raw_audio_uploaded": False,
                "remote_asr_called": False,
                "llm_called": False,
                "configs_local_read": False,
                "private_user_audio_read": False,
                "paid_provider_used": False,
            },
        }

    monkeypatch.setattr(
        tool.mac_system_audio_capture_adapter,
        "build_system_audio_capture_health_report",
        fake_build_system_audio_capture_health_report,
    )

    report = tool.run_mainline_usable_e2e_selftest(
        session_id="m15_system_audio_capture",
        repo_root=tool.REPO_ROOT,
        output_root=tmp_path / "artifacts/tmp/mainline_selftests",
        run_browser_smoke=False,
        system_audio_capture={
            "capture_status": "recorded_from_system_audio_device",
            "audio_path": "artifacts/tmp/audio_health/m15_system_audio_capture.system-audio-health.wav",
            "record_seconds": 12,
            "audio_device_index": 7,
            "privacy_cost_flags": {
                "raw_audio_uploaded": False,
                "remote_asr_called": False,
                "llm_called": False,
                "configs_local_read": False,
                "private_user_audio_read": False,
                "paid_provider_used": False,
            },
        },
    )

    mac_capture_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "mac_system_audio_capture"
    )
    production_asr_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "production_asr_quality"
    )
    real_meeting_gap = next(
        entry for entry in report["gap_entries"] if entry["gap_id"] == "real_meeting_go_evidence"
    )
    assert len(calls) == 1
    assert calls[0]["audio_path"] == (
        tool.REPO_ROOT
        / "artifacts/tmp/audio_health/m15_system_audio_capture.system-audio-health.wav"
    )
    assert calls[0]["capture"]["capture_status"] == "recorded_from_system_audio_device"
    assert report["system_audio_capture"]["audio_health"]["health_status"] == "audio_capture_health_passed"
    assert mac_capture_gap["status"] == "implemented_and_verified"
    assert production_asr_gap["status"] == "blocked_by_asr_quality"
    assert real_meeting_gap["status"] == "blocked_requires_explicit_user_approval"


def test_cli_explicit_system_audio_capture_calls_recorder_without_remote_services(monkeypatch, tmp_path):
    tool = load_tool_module()
    out = io.StringIO()
    calls = []

    def fake_record_system_audio_sample(*, audio_path, record_seconds, audio_device_index, repo_root):
        calls.append(
            {
                "audio_path": audio_path,
                "record_seconds": record_seconds,
                "audio_device_index": audio_device_index,
                "repo_root": repo_root,
            }
        )
        return {
            "capture_status": "recorded_from_system_audio_device",
            "audio_path": "artifacts/tmp/audio_health/m15_cli_system_audio.system-audio-health.wav",
            "record_seconds": record_seconds,
            "timeout_seconds": record_seconds + 10,
            "audio_device_index": audio_device_index,
            "audio_file_size_bytes": 4096,
            "validation_errors": [],
            "privacy_cost_flags": {
                "raw_audio_uploaded": False,
                "remote_asr_called": False,
                "llm_called": False,
                "configs_local_read": False,
                "private_user_audio_read": False,
                "paid_provider_used": False,
            },
        }

    def fake_build_system_audio_capture_health_report(*, audio_path, repo_root, capture):
        return {
            "report_mode": "mac_system_audio_capture_adapter",
            "schema_version": "mac_system_audio_capture_adapter.v1",
            "capture_adapter_status": "system_audio_capture_health_passed",
            "capture_backend": "ffmpeg_avfoundation_explicit_device",
            "recommended_route": "virtual_system_audio_device_first",
            "screen_capturekit_status": "future_native_path_not_implemented",
            "audio_path": "artifacts/tmp/audio_health/m15_cli_system_audio.system-audio-health.wav",
            "capture": capture,
            "audio_health": {
                "health_status": "audio_capture_health_passed",
                "audio_path": "artifacts/tmp/audio_health/m15_cli_system_audio.system-audio-health.wav",
            },
            "m2_go_evidence_status": "not_real_meeting_go_evidence",
            "privacy_cost_flags": {
                "raw_audio_uploaded": False,
                "remote_asr_called": False,
                "llm_called": False,
                "configs_local_read": False,
                "private_user_audio_read": False,
                "paid_provider_used": False,
            },
        }

    monkeypatch.setattr(
        tool.mac_system_audio_capture_adapter,
        "record_system_audio_sample",
        fake_record_system_audio_sample,
    )
    monkeypatch.setattr(
        tool.mac_system_audio_capture_adapter,
        "build_system_audio_capture_health_report",
        fake_build_system_audio_capture_health_report,
    )

    exit_code = tool.main(
        [
            "--session-id",
            "m15_cli_system_audio",
            "--output-root",
            str(tmp_path / "artifacts/tmp/mainline_selftests"),
            "--system-audio-record-seconds",
            "12",
            "--system-audio-device-index",
            "7",
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["record_seconds"] == 12
    assert calls[0]["audio_device_index"] == 7
    assert calls[0]["audio_path"] == (
        tool.REPO_ROOT / "artifacts/tmp/audio_health/m15_cli_system_audio.system-audio-health.wav"
    )
    assert payload["system_audio_capture"]["audio_health"]["health_status"] == "audio_capture_health_passed"
    assert payload["privacy_cost_flags"]["remote_asr_called"] is False
    assert payload["privacy_cost_flags"]["llm_called"] is False


def test_cli_writes_json_payload_and_artifacts(tmp_path):
    tool = load_tool_module()
    out = io.StringIO()
    output_root = tmp_path / "artifacts/tmp/mainline_selftests"

    exit_code = tool.main(
        [
            "--session-id",
            "m15_cli_review",
            "--output-root",
            str(output_root),
        ],
        out=out,
    )

    payload = json.loads(out.getvalue())
    assert exit_code == 0
    assert payload["overall_status"] == "mainline_product_chain_exercised_with_expected_blockers"
    assert payload["artifacts"]["json_report_path"] == "<redacted_outside_repo_path>"
    assert payload["artifacts"]["markdown_report_path"] == "<redacted_outside_repo_path>"
    assert (output_root / "m15_cli_review.mainline-usable-e2e.json").exists()
    assert (output_root / "m15_cli_review.mainline-usable-e2e.md").exists()

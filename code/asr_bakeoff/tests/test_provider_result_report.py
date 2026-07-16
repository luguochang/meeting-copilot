import json
from pathlib import Path

from asr_bakeoff.provider_result_report import main, run_provider_result_report


def test_provider_result_report_compares_live_session_and_funasr_provider_outputs(tmp_path: Path):
    live_session = tmp_path / "sherpa-session-events.json"
    funasr_provider = tmp_path / "funasr-provider.json"
    output = tmp_path / "provider-result-report.json"

    live_session.write_text(
        json.dumps(
            {
                "session_id": "rec_real",
                "event_source": {
                    "provider": "sherpa_onnx_realtime",
                    "provider_mode": "real",
                    "is_mock": False,
                    "asr_fallback_used": False,
                    "input_source": "real_browser_mic",
                    "acceptance_eligible": True,
                    "acceptance_blockers": [],
                    "asr_semantic_quality": {
                        "status": "passed",
                        "technical_entity_hit_count": 8,
                        "technical_group_hit_count": 4,
                    },
                },
                "audio": {"duration_ms": 60000, "saved": True},
                "events": [
                    {
                        "event_type": "transcript_final",
                        "payload": {
                            "segment_id": "s1",
                            "text": "checkout-service 接口先灰度 5%，错误率超过 0.1% 就回滚。",
                        },
                    },
                    {
                        "event_type": "transcript_final",
                        "payload": {
                            "segment_id": "s2",
                            "text": "张三确认负责人，P99 延迟和 SLO 看板一起观察。",
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    funasr_provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "checkout service 接口先灰度，如果错误率超过零点一就回滚，t一九延迟和LO板需要观察",
                "latency_ms": 42000,
                "audio_duration_seconds": 60.0,
                "rtf": 0.7,
                "raw": {
                    "provider": "funasr",
                    "mode": "file_replayed_streaming_events",
                    "model_download_status": "not_performed",
                    "partial_event_count": 69,
                    "final_event_count": 16,
                    "safe_to_download_models": False,
                    "hotword_status": "enabled",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_provider_result_report(
        input_paths=[live_session, funasr_provider],
        output_path=output,
        current_default_provider="sherpa_onnx_realtime",
    )

    assert output.exists()
    assert report["schema_version"] == "asr_provider_result_report.v1"
    assert report["summary"]["candidate_count"] == 2
    assert report["summary"]["remote_asr_call_count"] == 0
    assert report["summary"]["remote_llm_call_count"] == 0
    assert report["summary"]["saved_audio_candidate_count"] == 1

    sherpa = report["candidates"][0]
    assert sherpa["provider"] == "sherpa_onnx_realtime"
    assert sherpa["input_kind"] == "live_asr_session_events"
    assert sherpa["final_count"] == 2
    assert sherpa["partial_count"] == 0
    assert sherpa["semantic_quality"]["status"] == "passed"
    assert sherpa["text_quality"]["contains_unk"] is False
    assert sherpa["cost_privacy"] == {
        "remote_asr_called": False,
        "remote_llm_called": False,
        "model_download_performed": False,
        "raw_audio_uploaded": False,
    }

    funasr = report["candidates"][1]
    assert funasr["provider"] == "funasr"
    assert funasr["input_kind"] == "provider_json"
    assert funasr["partial_count"] == 69
    assert funasr["final_count"] == 16
    assert funasr["latency"]["rtf"] == 0.7
    assert funasr["cost_privacy"]["model_download_performed"] is False

    decision = report["default_provider_decision"]
    assert decision["current_default_provider"] == "sherpa_onnx_realtime"
    assert decision["recommended_action"] == "keep_current_default"
    assert decision["replacement_allowed"] is False
    assert "candidate_not_proven_on_natural_meeting" in decision["blockers"]
    assert "funasr_has_quality_or_latency_risk" in decision["blockers"]


def test_provider_result_report_cli_writes_report(tmp_path: Path, capsys):
    provider = tmp_path / "provider.json"
    output = tmp_path / "report.json"
    provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "接口灰度发布，错误率超过阈值就回滚。",
                "raw": {"provider": "local_candidate", "final_event_count": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = main(["--input", str(provider), "--output", str(output)])

    assert exit_code == 0
    assert output.exists()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "asr_provider_result_report.v1"
    assert payload["summary"]["candidate_count"] == 1
    assert "candidate_count" in capsys.readouterr().out


def test_provider_result_report_preserves_raw_and_normalized_unk_risk(tmp_path: Path):
    provider = tmp_path / "provider.json"
    output = tmp_path / "report.json"
    provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "P99 延迟和<unk> 看板需要观察，这个风险如果没有会上要马上追问",
                "raw": {"provider": "normalizable-provider", "final_event_count": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_provider_result_report(input_paths=[provider], output_path=output)

    text_quality = report["candidates"][0]["text_quality"]
    assert text_quality["raw_contains_unk"] is True
    assert text_quality["normalized_contains_unk"] is True
    assert text_quality["contains_unk"] is True
    assert "contains_unk" in report["candidates"][0]["risk_flags"]


def test_provider_result_report_records_realtime_metrics_from_live_and_sibling_event_files(tmp_path: Path):
    live_session = tmp_path / "sherpa-session-events.json"
    funasr_provider = tmp_path / "funasr-streaming-provider.json"
    funasr_events = tmp_path / "funasr-streaming-events.json"
    output = tmp_path / "provider-result-report.json"

    live_session.write_text(
        json.dumps(
            {
                "event_source": {"provider": "sherpa_onnx_realtime", "asr_semantic_quality": {"status": "passed"}},
                "events": [
                    {
                        "event_type": "transcript_final",
                        "at_ms": 27000,
                        "payload": {
                            "segment_id": "s1",
                            "start_ms": 0,
                            "end_ms": 27000,
                            "text": "接口先灰度，错误率超过阈值就回滚。",
                        },
                    },
                    {
                        "event_type": "transcript_final",
                        "at_ms": 54000,
                        "payload": {
                            "segment_id": "s2",
                            "start_ms": 27000,
                            "end_ms": 54000,
                            "text": "张三确认负责人和 P99 看板。",
                        },
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    funasr_provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "接口先灰度，错误率超过阈值就回滚。",
                "raw": {"provider": "funasr", "partial_event_count": 2, "final_event_count": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    funasr_events.write_text(
        json.dumps(
            [
                {"event_type": "partial", "start_ms": 0, "end_ms": 600, "received_at_ms": 800, "text": "接口"},
                {"event_type": "final", "start_ms": 0, "end_ms": 3000, "received_at_ms": 7600, "text": "接口先灰度"},
                {"event_type": "final", "start_ms": 3000, "end_ms": 6000, "received_at_ms": 9400, "text": "错误率就回滚"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_provider_result_report(input_paths=[live_session, funasr_provider], output_path=output)

    sherpa = report["candidates"][0]
    assert sherpa["realtime_metrics"]["first_final_received_at_ms"] == 27000
    assert sherpa["realtime_metrics"]["final_interval_ms"]["max_ms"] == 27000
    assert "first_final_after_10s" in sherpa["risk_flags"]
    assert "final_interval_above_15s" in sherpa["risk_flags"]

    funasr = report["candidates"][1]
    assert funasr["realtime_metrics"]["timing_source"] == str(funasr_events)
    assert funasr["realtime_metrics"]["first_partial_received_at_ms"] == 800
    assert funasr["realtime_metrics"]["first_final_received_at_ms"] == 7600
    assert funasr["realtime_metrics"]["final_latency_ms"]["max_ms"] == 4600
    assert "first_final_after_10s" not in funasr["risk_flags"]


def test_provider_result_report_records_resource_metrics_from_sibling_resource_file(tmp_path: Path):
    provider = tmp_path / "funasr-streaming-provider.json"
    resource = tmp_path / "funasr-streaming-resource.json"
    output = tmp_path / "provider-result-report.json"
    provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "接口先灰度，错误率超过阈值就回滚。",
                "raw": {"provider": "funasr", "final_event_count": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resource.write_text(
        json.dumps(
            {
                "schema_version": "asr_provider_resource_probe.v1",
                "resource_metrics": {
                    "wall_seconds": 42.5,
                    "user_cpu_seconds": 88.0,
                    "system_cpu_seconds": 4.0,
                    "max_rss_bytes": 3_221_225_472,
                    "peak_memory_footprint_bytes": 3_489_660_928,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_provider_result_report(input_paths=[provider], output_path=output)

    candidate = report["candidates"][0]
    assert candidate["resource_metrics"]["resource_source"] == str(resource)
    assert candidate["resource_metrics"]["max_rss_mb"] == 3072.0
    assert candidate["resource_metrics"]["peak_memory_footprint_mb"] == 3328.0
    assert candidate["resource_metrics"]["cpu_time_ratio"] == 2.164706
    assert "max_rss_above_2gb" in candidate["risk_flags"]
    assert "cpu_time_ratio_above_2" in candidate["risk_flags"]
    assert report["summary"]["resource_measured_count"] == 1

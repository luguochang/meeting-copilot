import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "synthetic_asr_smoke_report.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "synthetic_asr_smoke_report",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_synthetic_asr_smoke_report_scores_raw_and_normalized_entities(tmp_path):
    provider_json = tmp_path / "provider.json"
    transcript_report_json = tmp_path / "transcript-report.json"
    events_json = tmp_path / "events.json"
    script_json = tmp_path / "api-review.json"

    provider_json.write_text(
        json.dumps(
            {
                "status": "ok",
                "latency_ms": 500,
                "audio_duration_seconds": 10,
                "rtf": 0.05,
                "text": "我们先看 <unk> 接口 request id，需要看 九九 延迟",
                "segments": [{"id": "seg_001", "text": "我们先看 <unk> 接口"}],
                "raw": {"partial_event_count": 2, "final_event_count": 1},
            }
        ),
        encoding="utf-8",
    )
    transcript_report_json.write_text(
        json.dumps(
            {
                "text": "我们先看 <unk> 接口 request id，需要看 九九 延迟",
                "normalized_text": "我们先看 payment-gateway 接口 request_id，需要看 P99 延迟",
                "normalization_changes": [{"alias": "九九", "canonical": "P99"}],
            }
        ),
        encoding="utf-8",
    )
    events_json.write_text(
        json.dumps(
            [
                {"event_type": "partial"},
                {"event_type": "partial"},
                {"event_type": "final"},
                {"event_type": "end_of_stream"},
            ]
        ),
        encoding="utf-8",
    )
    script_json.write_text(
        json.dumps(
            {
                "script_id": "api-review-001",
                "technical_entities": ["payment-gateway", "request_id", "P99"],
            }
        ),
        encoding="utf-8",
    )
    tool = load_tool_module()

    report = tool.build_synthetic_asr_smoke_report(
        provider_json_path=provider_json,
        transcript_report_path=transcript_report_json,
        events_json_path=events_json,
        script_json_path=script_json,
    )

    assert report["report_mode"] == "synthetic_asr_smoke_report"
    assert report["report_version"] == "synthetic_asr_smoke_report.v1"
    assert report["status"] == "completed"
    assert report["script_id"] == "api-review-001"
    assert report["duration_seconds"] == 10
    assert report["latency_ms"] == 500
    assert report["rtf"] == 0.05
    assert report["event_counts"] == {
        "partial": 2,
        "final": 1,
        "revision": 0,
        "error": 0,
        "end_of_stream": 1,
    }
    assert report["unk_token_count"] == 1
    assert report["technical_entity_count"] == 3
    assert report["raw_technical_entity_recall"] == 0.0
    assert report["normalized_technical_entity_recall"] == 1.0
    assert report["normalized_missing_entities"] == []
    assert report["quality_gate"]["passes_first_pilot_entity_threshold"] is True
    assert report["safe_to_read_user_audio"] is False
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False


def test_synthetic_asr_smoke_report_rejects_forbidden_repo_paths():
    tool = load_tool_module()

    report = tool.build_synthetic_asr_smoke_report_from_relative_paths(
        provider_json_path="outputs/private.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/private.report.json",
        events_json_path="artifacts/tmp/asr_events/private.events.json",
        script_json_path="data/asr_eval/local_samples/private.json",
    )

    assert report["status"] == "blocked"
    assert "provider_json_path is not allowed" in report["validation_errors"]
    assert "script_json_path is not allowed" in report["validation_errors"]
    assert report["safe_to_read_user_audio"] is False


def test_synthetic_asr_smoke_report_rejects_allowed_symlink_to_forbidden_root_before_read(tmp_path, monkeypatch):
    tool = load_tool_module()
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(tool, "REPO_ROOT", repo_root)
    allowed_report_dir = repo_root / "artifacts" / "tmp" / "asr_reports"
    allowed_events_dir = repo_root / "artifacts" / "tmp" / "asr_events"
    allowed_script_dir = repo_root / "data" / "asr_eval" / "synthetic_meetings" / "scripts"
    forbidden_dir = repo_root / "configs" / "local"
    allowed_report_dir.mkdir(parents=True)
    allowed_events_dir.mkdir(parents=True)
    allowed_script_dir.mkdir(parents=True)
    forbidden_dir.mkdir(parents=True)

    forbidden_provider = forbidden_dir / "private-provider.json"
    forbidden_provider.write_text(
        json.dumps(
            {
                "status": "ok",
                "text": "payment-gateway P99",
                "segments": [{"id": "seg_001", "text": "payment-gateway P99"}],
            }
        ),
        encoding="utf-8",
    )
    provider_symlink = allowed_report_dir / "provider-link.json"
    provider_symlink.symlink_to(forbidden_provider)
    (allowed_report_dir / "transcript-report.json").write_text(
        json.dumps({"normalized_text": "payment-gateway P99"}),
        encoding="utf-8",
    )
    (allowed_events_dir / "events.json").write_text(
        json.dumps([{"event_type": "final"}, {"event_type": "end_of_stream"}]),
        encoding="utf-8",
    )
    (allowed_script_dir / "api-review.json").write_text(
        json.dumps(
            {
                "script_id": "api-review-001",
                "technical_entities": ["payment-gateway", "P99"],
            }
        ),
        encoding="utf-8",
    )

    report = tool.build_synthetic_asr_smoke_report_from_relative_paths(
        provider_json_path="artifacts/tmp/asr_reports/provider-link.json",
        transcript_report_path="artifacts/tmp/asr_reports/transcript-report.json",
        events_json_path="artifacts/tmp/asr_events/events.json",
        script_json_path="data/asr_eval/synthetic_meetings/scripts/api-review.json",
    )

    assert report["status"] == "blocked"
    assert "provider_json_path resolved path is forbidden: configs/local" in report["validation_errors"]
    assert report["safe_to_read_configs_local"] is False
    assert report["safe_to_read_user_audio"] is False

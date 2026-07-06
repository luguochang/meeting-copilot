import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_single_result_builder.py"
GATE_PATH = REPO_ROOT / "tools" / "funasr_synthetic_smoke_result_evidence.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(repo_root: Path, relative_path: str, payload: object) -> None:
    path = repo_root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def engineering_inputs() -> tuple[dict, dict, list[dict], dict]:
    provider = {
        "status": "ok",
        "text": "我们先看 payment-gateway 的创建订单接口，字段 request_id 要兼容旧客户端。错误码 40012，灰度看 P99。",
        "latency_ms": 1200,
        "audio_duration_seconds": 12.0,
        "rtf": 0.1,
        "segments": [
            {
                "id": "funasr_001",
                "start_ms": 0,
                "end_ms": 12000,
                "text": "我们先看 payment-gateway 的创建订单接口，字段 request_id 要兼容旧客户端。错误码 40012，灰度看 P99。",
            }
        ],
    }
    transcript_report = {
        "normalized_text": provider["text"],
        "rtf": 0.1,
        "evidence_spans": [
            {
                "id": "ev_001",
                "segment_id": "seg_001",
                "start_ms": 0,
                "end_ms": 12000,
                "quote": provider["text"],
            }
        ],
    }
    events = [
        {
            "event_type": "partial",
            "segment_id": "seg_001",
            "text": "我们先看 payment-gateway",
            "start_ms": 0,
            "end_ms": 1200,
            "received_at_ms": 900,
        },
        {
            "event_type": "final",
            "segment_id": "seg_001",
            "text": provider["text"],
            "start_ms": 0,
            "end_ms": 12000,
            "received_at_ms": 13200,
        },
        {"event_type": "end_of_stream", "received_at_ms": 13250},
    ]
    script = {
        "script_id": "api-review-001",
        "scenario": "api_review",
        "technical_entities": ["payment-gateway", "request_id", "40012", "P99"],
        "expected_state_events": [{"event_type": "risk.created"}],
        "expected_suggestion_cards": [
            {"card_id": "api-owner", "should_show": True, "evidence_span_required": True}
        ],
    }
    return provider, transcript_report, events, script


def test_builder_outputs_drv044_single_result_and_passes_single_gate(tmp_path, monkeypatch):
    builder = load_module(BUILDER_PATH, "funasr_synthetic_smoke_single_result_builder")
    gate = load_module(GATE_PATH, "funasr_synthetic_smoke_result_evidence")
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(builder, "REPO_ROOT", repo_root)
    monkeypatch.setattr(gate, "REPO_ROOT", repo_root)
    provider, transcript_report, events, script = engineering_inputs()
    write_json(repo_root, "artifacts/tmp/asr_reports/api-review-001.funasr.provider.json", provider)
    write_json(repo_root, "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json", transcript_report)
    write_json(repo_root, "artifacts/tmp/asr_events/api-review-001.funasr.events.json", events)
    write_json(repo_root, "data/asr_eval/synthetic_meetings/scripts/api-review.json", script)

    evidence = builder.build_funasr_synthetic_smoke_single_result_from_relative_paths(
        provider_json_path="artifacts/tmp/asr_reports/api-review-001.funasr.provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        events_json_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        script_json_path="data/asr_eval/synthetic_meetings/scripts/api-review.json",
    )

    assert evidence["manifest_version"] == "funasr_synthetic_smoke_result.v1"
    assert evidence["evidence_kind"] == "single_synthetic_smoke"
    assert evidence["provider"] == "funasr_streaming"
    scenario = evidence["scenario_results"][0]
    assert scenario["scenario_id"] == "api-review-001"
    assert scenario["scenario_kind"] == "engineering"
    assert scenario["event_contract"] == {
        "partial_count": 1,
        "final_count": 1,
        "revision_count": 0,
        "error_count": 0,
        "end_of_stream_count": 1,
        "has_required_event_sequence": True,
    }
    assert scenario["technical_entity_metrics"]["normalized_recall"] == 1.0
    assert scenario["closure"]["evidence_span_count"] == 1
    assert scenario["closure"]["state_event_count"] == 1
    assert scenario["closure"]["candidate_card_count"] == 1
    assert scenario["safety"]["used_microphone"] is False
    gate_report = gate.build_funasr_synthetic_smoke_result_evidence_gate(evidence_report=evidence)
    assert gate_report["quality_evidence_status"] == (
        "funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation"
    )


def test_builder_reports_entity_matches_and_missing_entities_for_raw_and_normalized_text():
    builder = load_module(BUILDER_PATH, "funasr_synthetic_smoke_single_result_builder")
    provider, transcript_report, events, script = engineering_inputs()
    provider["text"] = "paymentway 字段 quest 错误码 40012，需要看 P99"
    transcript_report["normalized_text"] = "payment-gateway 字段 request_id 错误码 40012，需要看 P99"

    evidence = builder.build_funasr_synthetic_smoke_single_result(
        provider=provider,
        transcript_report=transcript_report,
        events=events,
        script=script,
    )

    metrics = evidence["scenario_results"][0]["technical_entity_metrics"]
    assert metrics["expected_entities"] == ["payment-gateway", "request_id", "40012", "P99"]
    assert metrics["raw_recall"] == 0.5
    assert metrics["raw_matched_entities"] == ["40012", "P99"]
    assert metrics["raw_missing_entities"] == ["payment-gateway", "request_id"]
    assert metrics["normalized_recall"] == 1.0
    assert metrics["normalized_matched_entities"] == ["40012", "P99", "payment-gateway", "request_id"]
    assert metrics["normalized_missing_entities"] == []


def test_builder_blocks_forbidden_provider_path_before_reading(monkeypatch):
    builder = load_module(BUILDER_PATH, "funasr_synthetic_smoke_single_result_builder")

    def fail_if_read(*_args, **_kwargs):
        raise AssertionError("forbidden provider path was read")

    monkeypatch.setattr(Path, "read_text", fail_if_read)

    evidence = builder.build_funasr_synthetic_smoke_single_result_from_relative_paths(
        provider_json_path="configs/local/private-provider.json",
        transcript_report_path="artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        events_json_path="artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        script_json_path="data/asr_eval/synthetic_meetings/scripts/api-review.json",
    )

    assert evidence["evidence_status"] == "blocked_by_path_guard"
    assert "provider_json_path is not allowed" in evidence["validation_errors"]
    assert "provider_json_path is forbidden" in evidence["validation_errors"]
    assert evidence["safe_to_read_configs_local_now"] is False

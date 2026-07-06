import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "asr_event_generation_plan.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "asr_event_generation_plan",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_asr_event_generation_plan_is_local_only_for_public_audio():
    tool = load_tool_module()

    report = tool.build_asr_event_generation_plan(
        input_layer="public_audio_sample",
        audio_path="artifacts/tmp/public_audio/aishell4/sample-001.wav",
        provider_candidate="funasr_streaming",
        output_event_path="artifacts/tmp/asr_events/public-sample-001.events.jsonl",
    )

    assert report["plan_mode"] == "asr_event_generation_plan_only"
    assert report["plan_version"] == "asr_event_generation_plan.v1"
    assert report["plan_status"] == "ready_for_manual_local_asr_review"
    assert report["input_layer"] == "public_audio_sample"
    assert report["event_contract"] == "partial_final_revision_error_eos"
    assert report["provider_candidate"] == "funasr_streaming"
    assert report["safe_to_run_asr_now"] is False
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_call_llm"] is False
    assert report["safe_to_read_user_audio"] is False
    assert report["metrics_required"] == [
        "duration_seconds",
        "rtf",
        "first_partial_latency_ms",
        "final_latency_p95_ms",
        "segment_count",
        "raw_cer",
        "normalized_cer",
        "raw_technical_entity_recall",
        "raw_technical_entity_precision",
        "technical_entity_recall",
        "technical_entity_precision",
        "cpu_peak_percent",
        "memory_peak_mb",
    ]
    assert report["validation_errors"] == []


def test_asr_event_generation_plan_accepts_synthetic_audio_layer():
    tool = load_tool_module()

    report = tool.build_asr_event_generation_plan(
        input_layer="synthetic_audio",
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        provider_candidate="sherpa_onnx_streaming",
        output_event_path="artifacts/tmp/asr_events/api-review-001.events.jsonl",
    )

    assert report["plan_status"] == "ready_for_manual_local_asr_review"
    assert report["input_layer"] == "synthetic_audio"
    assert report["provider_candidate"] == "sherpa_onnx_streaming"
    assert report["next_action"] == "manual_local_asr_event_smoke"


def test_asr_event_generation_plan_rejects_private_audio_paths_and_remote_provider():
    tool = load_tool_module()

    report = tool.build_asr_event_generation_plan(
        input_layer="public_audio_sample",
        audio_path="data/asr_eval/local_samples/private.wav",
        provider_candidate="remote_asr",
        output_event_path="outputs/private.events.jsonl",
    )

    assert report["plan_status"] == "blocked"
    assert "audio_path is not under an approved input root" in report["validation_errors"]
    assert "output_event_path is not under an approved output root" in report["validation_errors"]
    assert "provider_candidate is not approved for local-only ASR" in report["validation_errors"]
    assert report["safe_to_call_remote_asr"] is False
    assert report["safe_to_run_asr_now"] is False


def test_asr_event_generation_report_is_path_safe():
    tool = load_tool_module()

    report = tool.build_asr_event_generation_plan(
        input_layer="synthetic_audio",
        audio_path="artifacts/tmp/synthetic_audio/api-review-001.wav",
        provider_candidate="funasr_streaming",
        output_event_path="artifacts/tmp/asr_events/api-review-001.events.jsonl",
    )

    report_json = json.dumps(report, ensure_ascii=False)
    assert "/Users/" not in report_json
    assert "configs/local" not in report_json

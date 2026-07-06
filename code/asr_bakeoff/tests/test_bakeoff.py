import json
from pathlib import Path

from asr_bakeoff.bakeoff import run_bakeoff
from asr_bakeoff.providers.mock import MockProvider
from asr_bakeoff.providers.base import AsrProvider, TranscriptResult


def test_run_bakeoff_scores_mock_provider_against_reference(tmp_path: Path):
    audio = tmp_path / "S01.wav"
    audio.write_bytes(b"fake wav")
    manifest = tmp_path / "manifest.json"
    reference = tmp_path / "S01.txt"
    annotation = tmp_path / "S01.annotation.json"
    output = tmp_path / "result.json"

    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(audio),
                        "reference_path": str(reference),
                        "annotation_path": str(annotation),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 10.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    reference.write_text("接口新增 trace_id 字段，需要兼容调用方。", encoding="utf-8")
    annotation.write_text(
        json.dumps(
            {
                "technical_entities": [
                    {"text": "trace_id", "normalized": "trace_id"},
                    {"text": "兼容", "normalized": "兼容"},
                    {"text": "调用方", "normalized": "调用方"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    provider = MockProvider({"S01": "接口新增 trace_id 字段，需要兼容调用方。"})
    report = run_bakeoff(manifest, provider, output)

    assert report["provider"] == "mock"
    assert report["summary"]["sample_count"] == 1
    assert report["summary"]["avg_cer"] == 0
    assert report["summary"]["avg_entity_f1"] == 1
    assert output.exists()


def test_run_bakeoff_excludes_samples_without_reference_or_annotation_from_metric_averages(tmp_path: Path):
    audio = tmp_path / "S01.wav"
    audio.write_bytes(b"fake wav")
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "result.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(audio),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 10.0,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_bakeoff(manifest, MockProvider({"S01": "这段没有人工参考文本。"}), output)

    assert report["summary"]["scored_cer_sample_count"] == 0
    assert report["summary"]["scored_entity_sample_count"] == 0
    assert report["summary"]["avg_cer"] is None
    assert report["summary"]["avg_entity_f1"] is None
    assert report["samples"][0]["cer"] is None
    assert report["samples"][0]["entity_accuracy"] is None
    assert report["samples"][0]["evaluation_status"] == {
        "cer": "not_evaluated",
        "entity_accuracy": "not_evaluated",
    }


def test_run_bakeoff_records_provider_failure_without_aborting_entire_run(tmp_path: Path):
    audio_ok = tmp_path / "S01.wav"
    audio_fail = tmp_path / "S02.wav"
    audio_ok.write_bytes(b"fake wav")
    audio_fail.write_bytes(b"fake wav")
    reference_ok = tmp_path / "S01.txt"
    reference_ok.write_text("接口新增 trace_id 字段。", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    output = tmp_path / "result.json"
    manifest.write_text(
        json.dumps(
            {
                "version": 1,
                "samples": [
                    {
                        "id": "S01",
                        "audio_path": str(audio_ok),
                        "reference_path": str(reference_ok),
                        "language": "zh-CN",
                        "scenario": "api_review",
                        "duration_seconds": 10.0,
                    },
                    {
                        "id": "S02",
                        "audio_path": str(audio_fail),
                        "language": "zh-CN",
                        "scenario": "incident_review",
                        "duration_seconds": 8.0,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_bakeoff(manifest, PartiallyFailingProvider(), output)

    assert report["summary"]["sample_count"] == 2
    assert report["summary"]["failed_sample_count"] == 1
    assert report["summary"]["scored_cer_sample_count"] == 1
    assert report["samples"][0]["status"] == "success"
    assert report["samples"][1]["status"] == "failed"
    assert report["samples"][1]["error"] == "simulated ASR outage"
    assert output.exists()


class PartiallyFailingProvider(AsrProvider):
    name = "partially-failing"

    def transcribe(self, sample_id: str, audio_path: Path) -> TranscriptResult:
        if sample_id == "S02":
            raise RuntimeError("simulated ASR outage")
        return TranscriptResult(text="接口新增 trace_id 字段。", latency_ms=100)

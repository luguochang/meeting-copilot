import json
import subprocess
import sys

from scripts.transcript_report import (
    EvidenceSpan,
    TranscriptReport,
    TranscriptSegment,
    build_report,
    load_provider_transcript,
)


def test_transcript_report_cli_runs_as_direct_script():
    result = subprocess.run(
        [sys.executable, "scripts/transcript_report.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Build a transcript report JSON" in result.stdout


def test_transcript_report_cli_accepts_glossary_argument():
    result = subprocess.run(
        [sys.executable, "scripts/transcript_report.py", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "--glossary" in result.stdout


def test_build_report_creates_evidence_span_from_transcript_text():
    report = build_report(
        audio_path="sample.wav",
        provider="local-test",
        text="我们先灰度 10%，如果错误率超过 0.1% 就回滚。",
        duration_seconds=10.0,
        latency_ms=5000,
    )

    assert isinstance(report, TranscriptReport)
    assert report.audio_path == "sample.wav"
    assert report.provider == "local-test"
    assert report.rtf == 0.5
    assert report.segments == [
        TranscriptSegment(
            id="seg_001",
            start_ms=0,
            end_ms=10000,
            text="我们先灰度 10%，如果错误率超过 0.1% 就回滚。",
            confidence=None,
        )
    ]
    assert report.evidence_spans == [
        EvidenceSpan(
            id="ev_001",
            segment_id="seg_001",
            start_ms=0,
            end_ms=10000,
            quote="我们先灰度 10%，如果错误率超过 0.1% 就回滚。",
        )
    ]


def test_build_report_preserves_raw_text_and_adds_normalized_text():
    report = build_report(
        audio_path="sample.wav",
        provider="funasr",
        text="我 们 这 次 payment gate 为 先 灰 度 百 分 之 十。",
        duration_seconds=10.0,
        latency_ms=5000,
        normalization_terms=[
            {"canonical": "payment-gateway", "aliases": ["payment gate 为"]}
        ],
    )

    assert report.text == "我 们 这 次 payment gate 为 先 灰 度 百 分 之 十。"
    assert report.normalized_text == "我们这次 payment-gateway 先灰度 10%。"
    assert report.normalization_changes == [
        {"alias": "payment gate 为", "canonical": "payment-gateway"},
        {"alias": "百分之十", "canonical": "10%"},
    ]


def test_load_provider_transcript_reads_text_latency_and_provider(tmp_path):
    provider_json = tmp_path / "asr.json"
    provider_json.write_text(
        '{"text":"我们确认一下回滚负责人","latency_ms":3210,"audio_duration_seconds":12.5,"raw":{"provider":"sherpa-onnx"}}',
        encoding="utf-8",
    )

    transcript = load_provider_transcript(provider_json)

    assert transcript.text == "我们确认一下回滚负责人"
    assert transcript.latency_ms == 3210
    assert transcript.duration_seconds == 12.5
    assert transcript.raw["provider"] == "sherpa-onnx"


def test_transcript_report_cli_uses_provider_duration_when_provider_json_has_it(tmp_path):
    provider_json = tmp_path / "asr.json"
    output_json = tmp_path / "transcript-report.json"
    provider_json.write_text(
        """
        {
          "text": "我们确认 payment-gateway 的 P99。",
          "latency_ms": 5000,
          "audio_duration_seconds": 20,
          "raw": {"provider": "funasr"},
          "segments": [
            {"id": "asr_001", "start_ms": 0, "end_ms": 20000, "text": "我们确认 payment-gateway 的 P99。"}
          ]
        }
        """,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/transcript_report.py",
            "--audio",
            "artifacts/tmp/synthetic_audio/api-review-001.wav",
            "--provider-json",
            str(provider_json),
            "--output",
            str(output_json),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert report["duration_seconds"] == 20
    assert report["rtf"] == 0.25
    assert report["provider"] == "funasr"


def test_build_report_uses_provider_segments_as_evidence_spans(tmp_path):
    provider_json = tmp_path / "asr.json"
    provider_json.write_text(
        """
        {
          "text": "先灰度百分之十。还没有回滚负责人。",
          "latency_ms": 1800,
          "raw": {"provider": "sherpa-onnx"},
          "segments": [
            {"id": "asr_001", "start_ms": 0, "end_ms": 1800, "text": "先灰度百分之十。"},
            {"id": "asr_002", "start_ms": 1800, "end_ms": 3600, "text": "还没有回滚负责人。"}
          ]
        }
        """,
        encoding="utf-8",
    )

    transcript = load_provider_transcript(provider_json)
    report = build_report(
        audio_path="sample.wav",
        provider="sherpa-onnx",
        text=transcript.text,
        duration_seconds=3.6,
        latency_ms=transcript.latency_ms,
        provider_segments=transcript.segments,
    )

    assert report.segments == [
        TranscriptSegment(
            id="seg_001",
            start_ms=0,
            end_ms=1800,
            text="先灰度百分之十。",
            confidence=None,
        ),
        TranscriptSegment(
            id="seg_002",
            start_ms=1800,
            end_ms=3600,
            text="还没有回滚负责人。",
            confidence=None,
        ),
    ]
    assert report.evidence_spans == [
        EvidenceSpan(
            id="ev_001",
            segment_id="seg_001",
            start_ms=0,
            end_ms=1800,
            quote="先灰度百分之十。",
        ),
        EvidenceSpan(
            id="ev_002",
            segment_id="seg_002",
            start_ms=1800,
            end_ms=3600,
            quote="还没有回滚负责人。",
        ),
    ]

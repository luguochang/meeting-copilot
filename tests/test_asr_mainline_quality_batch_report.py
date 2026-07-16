import importlib.util
import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "asr_mainline_quality_batch_report.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location("asr_mainline_quality_batch_report", TOOL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def seed_batch_fixture(root: Path) -> None:
    (root / "data/asr_eval/references").mkdir(parents=True)
    (root / "data/asr_eval/annotations").mkdir(parents=True)
    (root / "data/asr_eval/references/S01-api-review.txt").write_text(
        "接口先灰度，如果错误率超过 0.1% 就回滚。",
        encoding="utf-8",
    )
    write_json(
        root / "data/asr_eval/annotations/S01-api-review.annotation.json",
        {
            "technical_entities": [
                {"normalized": "接口"},
                {"normalized": "灰度"},
                {"normalized": "错误率"},
                {"normalized": "0.1%"},
                {"normalized": "回滚"},
            ]
        },
    )
    (root / "data/asr_eval/references/S04-mixed-terms.txt").write_text(
        "GET /api/v1/orders 增加 total_amount 字段。",
        encoding="utf-8",
    )
    write_json(
        root / "data/asr_eval/annotations/S04-mixed-terms.annotation.json",
        {
            "technical_entities": [
                {"normalized": "GET /api/v1/orders"},
                {"normalized": "total_amount"},
            ]
        },
    )

    write_json(
        root / "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
        [
            {
                "event_type": "final",
                "segment_id": "funasr_001",
                "text": "接口先灰度，如果错误率超过 0.1% 就回滚。",
                "start_ms": 0,
                "end_ms": 4200,
                "received_at_ms": 4300,
                "confidence": 0.91,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "eos",
                "text": "",
                "start_ms": 4300,
                "end_ms": 4300,
                "received_at_ms": 4300,
            },
        ],
    )
    write_json(
        root / "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
        [
            {
                "event_type": "final",
                "segment_id": "sherpa_001",
                "text": "<unk> 接口先灰度。",
                "start_ms": 0,
                "end_ms": 4200,
                "received_at_ms": 4300,
                "confidence": 0.7,
            },
            {
                "event_type": "end_of_stream",
                "segment_id": "eos",
                "text": "",
                "start_ms": 4300,
                "end_ms": 4300,
                "received_at_ms": 4300,
            },
        ],
    )
    write_json(
        root / "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
        {
            "provider": "funasr",
            "normalized_text": "接口先灰度，如果错误率超过 0.1% 就回滚。",
            "duration_seconds": 4.2,
            "rtf": 0.8,
            "segments": [{"id": "seg_001"}],
        },
    )
    write_json(
        root / "artifacts/tmp/asr_reports/api-review-001.sherpa.transcript-report.json",
        {
            "provider": "sherpa-onnx",
            "normalized_text": "<unk> 接口先灰度。",
            "duration_seconds": 4.2,
            "rtf": 0.03,
            "segments": [{"id": "seg_001"}],
        },
    )
    write_json(
        root / "data/asr_eval/manifests/asr-mainline-quality-batch.json",
        {
            "schema_version": "asr_mainline_quality_batch_matrix.v1",
            "samples": [
                {
                    "sample_id": "api-review-001",
                    "reference_path": "data/asr_eval/references/S01-api-review.txt",
                    "annotation_path": "data/asr_eval/annotations/S01-api-review.annotation.json",
                    "providers": [
                        {
                            "provider": "funasr_streaming",
                            "file_slug": "funasr",
                            "events_path": "artifacts/tmp/asr_events/api-review-001.funasr.events.json",
                            "transcript_report_path": "artifacts/tmp/asr_reports/api-review-001.funasr.transcript-report.json",
                        },
                        {
                            "provider": "sherpa_onnx_streaming",
                            "file_slug": "sherpa",
                            "events_path": "artifacts/tmp/asr_events/api-review-001.sherpa.events.json",
                            "transcript_report_path": "artifacts/tmp/asr_reports/api-review-001.sherpa.transcript-report.json",
                        },
                    ],
                },
                {
                    "sample_id": "mixed-terms-001",
                    "reference_path": "data/asr_eval/references/S04-mixed-terms.txt",
                    "annotation_path": "data/asr_eval/annotations/S04-mixed-terms.annotation.json",
                    "providers": [
                        {
                            "provider": "funasr_streaming",
                            "file_slug": "funasr",
                            "events_path": "artifacts/tmp/asr_events/mixed-terms-001.funasr.events.json",
                            "transcript_report_path": "artifacts/tmp/asr_reports/mixed-terms-001.funasr.transcript-report.json",
                        }
                    ],
                },
            ],
        },
    )


def test_batch_report_uses_explicit_matrix_generates_replay_and_marks_missing_inputs(tmp_path: Path):
    seed_batch_fixture(tmp_path)
    tool = load_tool_module()
    output = tmp_path / "artifacts/tmp/asr_reports/batch.json"

    report = tool.run_asr_mainline_quality_batch_report(
        repo_root=tmp_path,
        matrix_path=tmp_path / "data/asr_eval/manifests/asr-mainline-quality-batch.json",
        output_path=output,
        replay_run_id="unit",
    )

    assert output.exists()
    assert report["schema_version"] == "asr_mainline_quality_batch_report.v1"
    assert report["matrix_path"].endswith("data/asr_eval/manifests/asr-mainline-quality-batch.json")
    assert report["aggregate"]["sample_count"] == 2
    assert report["aggregate"]["evaluated_sample_count"] == 1
    assert report["aggregate"]["missing_input_sample_count"] == 1
    assert report["aggregate"]["quality_pass_sample_provider_count"] == 1
    assert report["aggregate"]["remote_asr_call_count"] == 0
    assert report["aggregate"]["llm_call_count"] == 0
    assert report["aggregate"]["best_provider_by_quality_coverage"] == "funasr_streaming"
    assert report["aggregate"]["samples_without_quality_pass"] == []
    assert report["aggregate"]["provider_quality_summary"] == [
        {
            "provider": "funasr_streaming",
            "evaluated_sample_count": 1,
            "pipeline_closed_count": 1,
            "quality_pass_count": 1,
            "usable_pass_count": 1,
            "quality_pass_rate": 1.0,
            "usable_pass_rate": 1.0,
            "average_term_recall": 1.0,
            "average_char_error_rate": 0.0,
            "contains_unk_count": 0,
            "average_rtf": 0.8,
            "missing_terms": [],
        },
        {
            "provider": "sherpa_onnx_streaming",
            "evaluated_sample_count": 1,
            "pipeline_closed_count": 1,
            "quality_pass_count": 0,
            "usable_pass_count": 0,
            "quality_pass_rate": 0.0,
            "usable_pass_rate": 0.0,
            "average_term_recall": 0.4,
            "average_char_error_rate": 1.0,
            "contains_unk_count": 1,
            "average_rtf": 0.03,
            "missing_terms": ["0.1%", "回滚", "错误率"],
        },
    ]
    assert report["default_decision"]["decision_status"] == "no_go_batch_incomplete"
    assert "missing_sample_inputs" in report["default_decision"]["blockers"]
    assert report["default_decision"]["best_provider_candidate"] == "funasr_streaming"

    evaluated = next(sample for sample in report["samples"] if sample["sample_id"] == "api-review-001")
    assert evaluated["status"] == "evaluated"
    assert evaluated["session_id"] == "api-review-001"
    assert Path(evaluated["quality_report_path"]).exists()
    assert [provider["provider"] for provider in evaluated["providers"]] == [
        "funasr_streaming",
        "sherpa_onnx_streaming",
    ]
    assert evaluated["providers"][0]["pipeline_closed"] is True
    assert evaluated["providers"][0]["quality_passed"] is True
    assert evaluated["providers"][1]["contains_unk"] is True

    missing = next(sample for sample in report["samples"] if sample["sample_id"] == "mixed-terms-001")
    assert missing["status"] == "missing_inputs"
    assert "events: artifacts/tmp/asr_events/mixed-terms-001.funasr.events.json" in missing["missing_inputs"]


def test_batch_report_cli_writes_machine_readable_report(tmp_path: Path):
    seed_batch_fixture(tmp_path)
    output = tmp_path / "batch-cli.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL_PATH),
            "--repo-root",
            str(tmp_path),
            "--matrix",
            str(tmp_path / "data/asr_eval/manifests/asr-mainline-quality-batch.json"),
            "--output",
            str(output),
            "--replay-run-id",
            "cli",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "asr_mainline_quality_batch_report.v1"
    assert payload["aggregate"]["evaluated_sample_count"] == 1
    assert payload["default_decision"]["decision_status"] == "no_go_batch_incomplete"
    assert "missing_input_sample_count" in completed.stdout


def test_batch_report_ranks_provider_by_usable_quality_and_pipeline_coverage():
    tool = load_tool_module()

    aggregate = tool._aggregate(
        [
            {
                "sample_id": "sample-1",
                "status": "evaluated",
                "providers": [
                    {
                        "provider": "fast_quality_only",
                        "pipeline_closed": False,
                        "quality_passed": True,
                        "term_recall": 1.0,
                        "char_error_rate": 0.01,
                        "contains_unk": False,
                        "rtf": 0.2,
                        "missing_terms": [],
                        "remote_asr_called": False,
                        "llm_called": False,
                    },
                    {
                        "provider": "stable_usable",
                        "pipeline_closed": True,
                        "quality_passed": True,
                        "term_recall": 0.8,
                        "char_error_rate": 0.2,
                        "contains_unk": False,
                        "rtf": 0.8,
                        "missing_terms": ["staging"],
                        "remote_asr_called": False,
                        "llm_called": False,
                    },
                ],
            }
        ]
    )

    assert aggregate["best_provider_by_quality_coverage"] == "stable_usable"
    assert aggregate["provider_quality_summary"][0]["provider"] == "stable_usable"
    assert aggregate["provider_quality_summary"][0]["usable_pass_count"] == 1
    assert aggregate["provider_quality_summary"][1]["provider"] == "fast_quality_only"
    assert aggregate["provider_quality_summary"][1]["usable_pass_count"] == 0


def test_reference_artifact_coverage_flags_suffix_terms_missing_after_truncated_transcript():
    tool = load_tool_module()

    audit = tool._reference_artifact_coverage(
        reference_text=(
            "凌晨 order-worker 消费堆积，lag 最高到了八万。"
            "临时扩容已经止血，根因可能是库存接口 timeout 增多。"
            "复盘报告我们下周一发，但监控阈值谁来改还没定。"
        ),
        expected_terms=["order-worker", "lag", "timeout", "监控阈值"],
        providers=[
            {
                "provider": "funasr_batch_chunk20_hotword",
                "transcript": {
                    "duration_seconds": 11.362,
                    "normalized_text": "凌晨 order-worker 消费堆积 lag 最高到了八万 临时扩容已经止血 根因可能是库存接口",
                },
                "quality": {
                    "missing_terms": ["timeout", "监控阈值"],
                },
            },
            {
                "provider": "sherpa_onnx_streaming",
                "transcript": {
                    "duration_seconds": 11.362,
                    "normalized_text": "<unk> 消费堆积 lag 库存接口",
                },
                "quality": {
                    "missing_terms": ["order-worker", "timeout", "监控阈值"],
                },
            },
        ],
    )

    assert audit["status"] == "suspected_reference_artifact_mismatch"
    assert audit["suspected_reference_artifact_mismatch"] is True
    assert audit["best_provider"] == "funasr_batch_chunk20_hotword"
    assert audit["best_covered_reference_ratio"] < 0.9
    assert audit["missing_terms_in_uncovered_suffix"] == ["timeout", "监控阈值"]
    assert "复盘报告" in audit["uncovered_reference_suffix_preview"]

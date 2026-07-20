import json
from pathlib import Path

from asr_bakeoff.semantic_quality_report import run_semantic_quality_report


def test_run_semantic_quality_report_scores_dataset_and_records_default_provider_decision(tmp_path: Path):
    dataset = tmp_path / "semantic-quality.json"
    output = tmp_path / "report.json"
    dataset.write_text(
        json.dumps(
            {
                "schema_version": "asr_semantic_quality_dataset.v1",
                "language": "zh-CN",
                "domain": "technical_meeting",
                "samples": [
                    {
                        "id": "positive",
                        "text": "接口先灰度 5%，错误率超过 0.1% 就回滚，owner 张三今天补 SLO 看板。",
                        "expected_status": "passed",
                        "expected_keywords": ["接口", "灰度", "错误率", "回滚", "owner", "张三", "SLO"],
                    },
                    {
                        "id": "negative",
                        "text": "今天天气不错，我们吃饭聊天，然后大家都很开心。",
                        "expected_status": "warning",
                        "expected_keywords": [],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = run_semantic_quality_report(dataset, output)

    assert output.exists()
    assert report["schema_version"] == "asr_semantic_quality_report.v1"
    assert report["provider_under_test"] == "meeting_copilot_asr_semantic_quality_gate"
    assert report["summary"] == {
        "sample_count": 2,
        "expected_passed_count": 1,
        "expected_blocked_count": 0,
        "expected_warning_count": 1,
        "actual_passed_count": 1,
        "actual_blocked_count": 0,
        "actual_warning_count": 1,
        "expected_status_match_count": 2,
        "unexpected_status_count": 0,
        "false_pass_count": 0,
        "false_block_count": 0,
        "keyword_recall_average": 1.0,
    }
    assert report["default_provider_decision"] == {
        "file_asr_default": "local_funasr_batch",
        "realtime_asr_default_order": ["sherpa_onnx_realtime", "funasr_realtime"],
        "remote_asr_default_enabled": False,
        "semantic_quality_gate_required": True,
        "decision_status": "accepted",
    }
    assert report["cost_status"] == "no_paid_remote_service"
    assert report["samples"][0]["actual_status"] == "passed"
    assert report["samples"][0]["keyword_recall"] == 1.0
    assert report["samples"][1]["actual_status"] == "warning"

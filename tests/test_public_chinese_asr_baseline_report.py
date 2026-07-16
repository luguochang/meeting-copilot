import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "public_chinese_asr_baseline_report.py"


def load_tool_module():
    spec = importlib.util.spec_from_file_location(
        "public_chinese_asr_baseline_report",
        TOOL_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_chinese_report_marks_downloaded_audio_as_baseline_not_product_feature():
    tool = load_tool_module()

    report = tool.build_report_from_provider_records(
        [
            {
                "audio_id": "OSR_cn_000_0072_8k",
                "source_id": "osr_mandarin",
                "provider_result": {
                    "status": "ok",
                    "text": "院子门口不远处就是一个地铁站这是一个美丽而神奇的景象树上长满了又大又甜的桃子海豚和鲸鱼的表演是很好看的节目尤局门前的人行道上有一个蓝色的油箱",
                    "latency_ms": 19318,
                    "audio_duration_seconds": 19.967,
                    "rtf": 0.967496,
                },
            },
            {
                "audio_id": "magichub_web_meeting_sample",
                "source_id": "magichub_web_meeting_sample",
                "provider_result": {
                    "status": "ok",
                    "text": "这个公司是应该现在要严厉的管一下这边的话你们有没有什么好的一个推荐的一个方案",
                    "latency_ms": 74654,
                    "audio_duration_seconds": 63.826,
                    "rtf": 1.169649,
                },
            },
        ]
    )

    assert report["report_kind"] == "public_chinese_asr_baseline"
    assert report["purpose"] == "reproducible_chinese_asr_quality_baseline_not_product_feature"
    assert report["remote_asr_call_count"] == 0
    assert report["llm_call_count"] == 0
    assert report["raw_audio_uploaded"] is False
    assert report["summary"]["item_count"] == 2
    assert report["summary"]["referenced_item_count"] == 1
    assert report["summary"]["qualitative_only_item_count"] == 1
    assert report["summary"]["max_rtf"] == 1.169649
    assert report["summary"]["release_gate_status"] == "needs_asr_optimization_before_release"
    assert report["items"][0]["reference_status"] == "available"
    assert report["items"][0]["cer"] > 0
    assert report["items"][0]["observed_near_misses"] == [
        {"observed": "尤局", "expected": "邮局", "risk": "common_mandarin_word_confusion"},
        {"observed": "油箱", "expected": "邮箱", "risk": "common_mandarin_word_confusion"},
    ]
    assert report["items"][1]["reference_status"] == "unavailable_qualitative_meeting_sample"
    assert report["recommendations"][0] == "stop_downloading_more_audio_until_current_baseline_has_a_fix_and_rerun"


def test_public_chinese_report_computes_zero_cer_for_reference_match_after_punctuation_normalization():
    tool = load_tool_module()

    report = tool.build_report_from_provider_records(
        [
            {
                "audio_id": "OSR_cn_000_0072_8k",
                "source_id": "osr_mandarin",
                "provider_result": {
                    "status": "ok",
                    "text": "院子门口不远处就是一个地铁站。 这是一个美丽而神奇的景象，树上长满了又大又甜的桃子。海豚和鲸鱼的表演是很好看的节目；邮局门前的人行道上有一个蓝色的邮箱。",
                    "latency_ms": 1000,
                    "audio_duration_seconds": 20,
                    "rtf": 0.05,
                },
            }
        ]
    )

    assert report["items"][0]["cer"] == 0
    assert report["summary"]["weighted_cer"] == 0
    assert report["summary"]["release_gate_status"] == "baseline_passed_for_current_public_samples"


def test_public_chinese_report_rejects_provider_records_without_status_or_text():
    tool = load_tool_module()

    report = tool.build_report_from_provider_records(
        [
            {
                "audio_id": "OSR_cn_000_0072_8k",
                "source_id": "osr_mandarin",
                "provider_result": {"status": "ok", "rtf": 0.5},
            },
            {
                "audio_id": "bad",
                "source_id": "osr_mandarin",
                "provider_result": {"text": "没有状态"},
            },
        ]
    )

    assert report["summary"]["item_count"] == 2
    assert report["items"][0]["status"] == "invalid_missing_text"
    assert report["items"][1]["status"] == "invalid_missing_status"
    assert report["summary"]["release_gate_status"] == "invalid_baseline_inputs"

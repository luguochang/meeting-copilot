from __future__ import annotations

import json

import pytest

from meeting_copilot_web_mvp.review_export import review_library_content


def test_decision_records_show_mixed_status_without_internal_ids_or_promotion():
    content = {"decisions": [
        {"id": "internal-confirmed", "text": "采用分层处理。", "status": "confirmed", "evidenceSegmentId": "internal-segment"},
        {"text": "整体重试待讨论。", "status": "candidate"},
        {"text": "放弃串行通知。", "status": "dismissed"},
        {"text": "旧状态待核对。", "status": "future_status"},
        {"text": "没有保存状态的条目。"},
    ]}
    result = review_library_content("decisions", content)
    assert "## 决策记录" in result
    for text, status in (("采用分层处理。", "已确认"), ("整体重试待讨论。", "待确认"),
                         ("放弃串行通知。", "已放弃"), ("旧状态待核对。", "待核对（原状态：future_status）"),
                         ("没有保存状态的条目。", "未标明")):
        assert f"- {text}\n  状态：{status}" in result
    assert "internal-confirmed" not in result and "internal-segment" not in result
    assert "```json" not in result


def test_action_and_risk_business_fields_are_readable_and_unknown_fields_are_retained():
    action = review_library_content("action_items", {"items": [{
        "item": "补充压测。", "status": "in_progress", "owner": "王工", "due": "周五",
        "estimate_hours": 0, "depends_on": ["预算批准"],
    }]})
    for text in ("补充压测。", "状态：进行中", "负责人：王工", "截止时间：周五",
                 '"estimate_hours": 0', '"depends_on"', "预算批准", "补充字段（原值保留）"):
        assert text in action
    risk = review_library_content("risks", [{
        "text": "缓存击穿。", "status": "open", "severity": "high", "mitigation": "互斥加载。",
        "owner": "后端组", "impact": "尾延迟升高", "probability": 0.25, "budget_cap": 50000,
    }])
    for text in ("状态：待处理", "级别：high", "缓解措施：互斥加载。", "负责人：后端组",
                 "影响：尾延迟升高", "概率：0.25", '"budget_cap": 50000'):
        assert text in risk


def test_transcript_reuses_timing_and_labels_while_retaining_alternate_original_text():
    content = {"segments": [{"segmentId": "hidden-id", "normalized_text": "人工校对文字。",
        "text": "原始识别文字。", "startedAtMs": 61000, "endedAtMs": 65000,
        "speakerLabel": "已有标签", "speakerId": "hidden-speaker", "speakerConfidence": 0.9}]}
    result = review_library_content("transcript", content)
    assert "[01:01-01:05] 已有标签：人工校对文字。" in result
    assert '"text": "原始识别文字。"' in result
    assert "hidden-id" not in result and "hidden-speaker" not in result
    missing_start = review_library_content("transcript", [{"text": "起点未保存。", "ended_at_ms": 5000}])
    assert "[结束 00:05] 起点未保存。" in missing_start and "[00:00-" not in missing_start
    no_timing = review_library_content("transcript", [{"text": "没有时间数据。"}])
    assert "没有时间数据。" in no_timing and "[00:00]" not in no_timing


def test_unconsumed_aliases_top_level_fields_and_unknown_rows_are_not_silently_dropped():
    content = {"decisions": [{"text": "主正文。", "item": "另一原始正文。", "status": "candidate",
        "evidence": {"segment_id": "hidden", "quote": "相关原话。"}}],
        "items": ["另一个未被选择的列表"], "business_policy": {"refunds": False}}
    rendered = review_library_content("decisions", content)
    for text in ("主正文。", '"item": "另一原始正文。"', "相关原话。", "另一个未被选择的列表", '"refunds": false'):
        assert text in rendered
    for item in (0, False, None, ["嵌套列表"], {"new_field": "业务约束"}, "  "):
        result = review_library_content("decisions", [item])
        assert "完整保留" in result
        assert json.dumps(item, ensure_ascii=False, indent=2) in result
    unknown = {"future_shape": {"important": [1, False, None]}}
    result = review_library_content("risks", unknown)
    assert "未识别结构，完整保留" in result
    assert json.dumps(unknown, ensure_ascii=False, indent=2) in result


@pytest.mark.parametrize("content", [[], {"decisions": []}])
def test_empty_saved_list_has_explicit_empty_body_and_no_invented_decisions(content):
    assert review_library_content("decisions", content) == "## 决策记录\n\n此终稿列表为空。"


@pytest.mark.parametrize("content", ["  # 人工纪要\n\n末尾原文  \n", {"markdown": "  # 人工纪要\n\n末尾原文  \n"}])
def test_minutes_markdown_is_kept_verbatim(content):
    expected = content if isinstance(content, str) else content["markdown"]
    assert review_library_content("minutes", content) == expected


@pytest.mark.parametrize("timing", [{"started_at_ms": -1}, {"started_at_ms": "bad"}, {"started_at_ms": True},
                                    {"started_at_ms": 1000, "ended_at_ms": 500}])
def test_invalid_transcript_timing_preserves_the_original_without_fabricating_zero(timing):
    item = {"text": "已有正文。", **timing}
    rendered = review_library_content("transcript", [item])
    assert "待核对，完整保留" in rendered
    assert json.dumps(item, ensure_ascii=False, indent=2) in rendered
    assert "[00:00]" not in rendered

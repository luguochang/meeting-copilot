from copy import deepcopy

from meeting_copilot_web_mvp.canonical_transcript import (
    SCHEMA_VERSION,
    project_canonical_transcript,
)


def _event(event_type, segment_id, text, *, at_ms, start_ms=0, end_ms=0, **payload):
    return {
        "id": f"{event_type}:{segment_id}:{at_ms}",
        "event_type": event_type,
        "at_ms": at_ms,
        "payload": {
            "segment_id": segment_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "normalized_text": text,
            **payload,
        },
    }


def test_project_canonical_transcript_builds_committed_text_and_one_active_tail():
    events = [
        _event("transcript_final", "seg_1", "第一段", at_ms=1000, end_ms=1000),
        _event("transcript_final", "seg_2", "第二段", at_ms=2000, start_ms=1000, end_ms=2000),
        _event("transcript_partial", "seg_3", "第三段正在说", at_ms=2500, start_ms=2000, end_ms=2500),
    ]

    snapshot = project_canonical_transcript(session_id="rec_test", events=events)

    assert snapshot["schema_version"] == SCHEMA_VERSION
    assert snapshot["session_id"] == "rec_test"
    assert [segment["segment_id"] for segment in snapshot["segments"]] == ["seg_1", "seg_2"]
    assert snapshot["committed_text"] == "第一段第二段"
    assert snapshot["full_text"] == "第一段第二段第三段正在说"
    assert snapshot["committed_char_count"] == len("第一段第二段")
    assert snapshot["full_char_count"] == len("第一段第二段第三段正在说")
    assert snapshot["active_tail"]["segment_id"] == "seg_3"
    assert snapshot["active_tail"]["status"] == "partial"


def test_project_canonical_transcript_applies_authority_and_revision_in_place():
    events = [
        _event("transcript_partial", "seg_1", "临时文字", at_ms=500),
        _event("transcript_final", "seg_1", "原始识别", at_ms=1000),
        _event(
            "transcript_revision",
            "revision_1",
            "AI 校正文字",
            at_ms=1500,
            revision_of="seg_1",
            supersedes_segment_id="seg_1",
            evidence_spans=[{"id": "evidence_revision_1"}],
        ),
        _event("transcript_partial", "seg_1", "迟到的临时文字", at_ms=2000),
    ]

    snapshot = project_canonical_transcript(session_id="rec_revision", events=events)

    assert len(snapshot["segments"]) == 1
    segment = snapshot["segments"][0]
    assert segment["segment_id"] == "seg_1"
    assert segment["status"] == "corrected"
    assert segment["display_text"] == "AI 校正文字"
    assert segment["original_text"] == "原始识别"
    assert segment["evidence_ids"] == ["evidence_revision_1"]
    assert snapshot["active_tail"] is None
    assert snapshot["full_text"] == "AI 校正文字"


def test_project_canonical_transcript_keeps_only_newest_unresolved_partial():
    events = [
        _event("transcript_partial", "seg_old", "旧活动尾部", at_ms=1000),
        _event("transcript_partial", "seg_new", "最新活动尾部", at_ms=2000),
    ]

    snapshot = project_canonical_transcript(session_id="rec_partials", events=events)

    assert snapshot["segments"] == []
    assert snapshot["active_tail"]["segment_id"] == "seg_new"
    assert snapshot["full_text"] == "最新活动尾部"


def test_project_canonical_transcript_keeps_unresolved_revision_as_auditable_supplement():
    events = [
        _event("transcript_final", "seg_1", "正文", at_ms=1000),
        _event("transcript_revision", "orphan_revision", "修正补充", at_ms=2000),
    ]

    snapshot = project_canonical_transcript(session_id="rec_supplement", events=events)

    assert [segment["display_text"] for segment in snapshot["segments"]] == ["正文", "修正补充"]
    assert snapshot["segments"][1]["revision_supplement"] is True
    assert snapshot["full_text"] == "正文修正补充"


def test_project_canonical_transcript_does_not_mutate_input_events():
    events = [_event("transcript_final", "seg_1", "正文", at_ms=1000)]
    before = deepcopy(events)

    project_canonical_transcript(session_id="rec_immutable", events=events)

    assert events == before


def test_project_canonical_transcript_reconciles_legacy_cumulative_partial_prefix():
    events = [
        _event("transcript_final", "seg_1", "第一段", at_ms=1000),
        _event("transcript_final", "seg_2", "第二段", at_ms=2000),
        _event("transcript_partial", "seg_3", "第一段第二段第三段", at_ms=3000),
    ]

    snapshot = project_canonical_transcript(session_id="rec_legacy", events=events)

    assert snapshot["committed_text"] == "第一段第二段"
    assert snapshot["active_tail"]["display_text"] == "第三段"
    assert snapshot["full_text"] == "第一段第二段第三段"


def test_project_canonical_transcript_reconciles_changed_committed_boundary_to_source_snapshot():
    events = [
        _event("transcript_final", "seg_1", "我们讨论产品目标", at_ms=1000),
        _event(
            "transcript_partial",
            "seg_2",
            "的和实现计划",
            at_ms=2000,
            source_snapshot_text="我们讨论产品目的和实现计划",
            projection_reconciled=True,
        ),
    ]

    snapshot = project_canonical_transcript(session_id="rec_reconciled", events=events)

    assert snapshot["full_text"] == "我们讨论产品目的和实现计划"
    assert snapshot["full_text"] == snapshot["committed_text"] + snapshot["active_tail"]["display_text"]
    assert snapshot["active_tail"]["projection_reconciled"] is True


def test_project_canonical_transcript_keeps_reconciled_boundary_after_tail_becomes_final():
    events = [
        _event("transcript_final", "seg_1", "我们讨论产品目标", at_ms=1000),
        _event(
            "transcript_final",
            "seg_2",
            "的和实现计划",
            at_ms=2000,
            source_snapshot_text="我们讨论产品目的和实现计划",
            projection_reconciled=True,
        ),
    ]

    snapshot = project_canonical_transcript(session_id="rec_reconciled_final", events=events)

    assert snapshot["active_tail"] is None
    assert snapshot["committed_text"] == "我们讨论产品目的和实现计划"
    assert snapshot["full_text"] == "我们讨论产品目的和实现计划"


def test_project_canonical_transcript_marks_revision_with_missing_target_as_supplement():
    events = [
        _event(
            "transcript_revision",
            "revision_missing",
            "无法定位的修正",
            at_ms=1000,
            revision_of="missing_segment",
            supersedes_segment_id="missing_segment",
        ),
    ]

    snapshot = project_canonical_transcript(session_id="rec_missing_revision", events=events)

    assert len(snapshot["segments"]) == 1
    assert snapshot["segments"][0]["revision_supplement"] is True
    assert snapshot["segments"][0]["segment_id"].startswith("revision-supplement:")


def test_project_canonical_transcript_keeps_projection_namespace_separate_from_segment_ids():
    events = [
        _event("transcript_final", "revision-supplement:r1", "合法普通段落", at_ms=1000),
        _event("transcript_revision", "r1", "无目标修正补充", at_ms=2000),
    ]

    snapshot = project_canonical_transcript(session_id="rec_namespace", events=events)

    assert [segment["display_text"] for segment in snapshot["segments"]] == [
        "合法普通段落",
        "无目标修正补充",
    ]
    assert [segment["projection_key"] for segment in snapshot["segments"]] == [
        "segment:revision-supplement:r1",
        "revision-supplement:transcript_revision:r1:2000",
    ]
    assert len({segment["projection_key"] for segment in snapshot["segments"]}) == 2

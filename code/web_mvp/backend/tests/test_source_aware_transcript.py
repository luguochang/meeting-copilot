from __future__ import annotations

import sqlite3

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import (
    V2Persistence,
    transcript_evidence_hash,
)


def _commit(
    persistence: V2Persistence,
    *,
    final_id: str,
    segment_id: str,
    text: str,
    source_track: str | None,
    started_at_ms: int,
    ended_at_ms: int,
) -> dict:
    return persistence.commit_final_and_enqueue(
        meeting_id="dual-source-meeting",
        final_id=final_id,
        segment_id=segment_id,
        text=text,
        normalized_text=text,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        evidence_hash=transcript_evidence_hash(segment_id, text),
        now_ms=ended_at_ms + 100,
        source_track=source_track,
    )


def test_cross_track_overlap_is_audited_but_not_shown_or_sent_to_ai(tmp_path):
    persistence = V2Persistence(tmp_path / "source-aware.db")
    try:
        microphone = _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="我们确认本周五完成数据库迁移并保留回滚方案",
            source_track="microphone",
            started_at_ms=1_000,
            ended_at_ms=4_000,
        )
        system_audio = _commit(
            persistence,
            final_id="system-final-1",
            segment_id="system_audio:segment-1",
            text="我们确认本周五完成数据库迁移，并保留回滚方案。",
            source_track="system_audio",
            started_at_ms=1_100,
            ended_at_ms=4_100,
        )

        assert microphone["source_duplicate"] is False
        assert set(microphone["job_ids"]) == {"correction", "suggestion"}
        assert system_audio["source_duplicate"] is True
        assert system_audio["duplicate_of_segment_id"] == "microphone:segment-1"
        assert system_audio["source_duplicate_similarity"] == 1.0
        assert system_audio["job_ids"] == {}

        canonical = persistence.list_transcript_segments("dual-source-meeting")
        assert [segment["segment_id"] for segment in canonical["segments"]] == [
            "microphone:segment-1"
        ]
        audit = persistence.list_transcript_segments(
            "dual-source-meeting",
            include_source_duplicates=True,
        )
        assert [segment["source_track"] for segment in audit["segments"]] == [
            "microphone",
            "system_audio",
        ]
        assert audit["segments"][1]["duplicate_of_segment_id"] == "microphone:segment-1"

        snapshot = persistence.get_snapshot("dual-source-meeting")
        assert snapshot["transcript_page"]["total"] == 1
        assert snapshot["transcript_page"]["source_duplicate_count"] == 1
        assert [segment["segment_id"] for segment in snapshot["segments"]] == [
            "microphone:segment-1"
        ]
        event_types = [event["type"] for event in persistence.list_events("dual-source-meeting")]
        assert event_types.count("transcript.segment.finalized") == 1
        assert event_types.count("transcript.segment.source_duplicate") == 1
    finally:
        persistence.close()


def test_repeated_words_are_not_suppressed_without_cross_track_time_overlap(tmp_path):
    persistence = V2Persistence(tmp_path / "source-aware-negative.db")
    try:
        _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="请确认回滚方案和负责人",
            source_track="microphone",
            started_at_ms=1_000,
            ended_at_ms=2_000,
        )
        overlapping_same_track = _commit(
            persistence,
            final_id="mic-final-2",
            segment_id="microphone:segment-2",
            text="请确认回滚方案和负责人",
            source_track="microphone",
            started_at_ms=1_100,
            ended_at_ms=2_100,
        )
        later_other_track = _commit(
            persistence,
            final_id="system-final-1",
            segment_id="system_audio:segment-1",
            text="请确认回滚方案和负责人",
            source_track="system_audio",
            started_at_ms=8_000,
            ended_at_ms=9_000,
        )
        assert later_other_track["source_duplicate"] is False
        assert overlapping_same_track["source_duplicate"] is False
        assert len(persistence.list_transcript_segments("dual-source-meeting")["segments"]) == 3
    finally:
        persistence.close()


def test_source_less_exact_timeline_replay_is_audited_but_not_projected(tmp_path):
    persistence = V2Persistence(tmp_path / "source-less-replay.db")
    try:
        _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="这些问题有人回答得上来吗？回答不上来，它就只是一个演示。",
            source_track="microphone",
            started_at_ms=150_000,
            ended_at_ms=161_632,
        )
        replay = _commit(
            persistence,
            final_id="replayed-final-1",
            segment_id="segment-1",
            text="这些问题有人回答上来吗？回答不上来，它就只是一个演示。",
            source_track=None,
            started_at_ms=150_000,
            ended_at_ms=161_632,
        )

        assert replay["source_duplicate"] is True
        assert replay["duplicate_of_segment_id"] == "microphone:segment-1"
        assert replay["job_ids"] == {}
        assert [
            segment["segment_id"]
            for segment in persistence.list_transcript_segments("dual-source-meeting")["segments"]
        ] == ["microphone:segment-1"]
        paragraphs = persistence.list_semantic_paragraphs("dual-source-meeting")["paragraphs"]
        assert [paragraph["checkpoint_ids"] for paragraph in paragraphs] == [
            ["microphone:segment-1"]
        ]
    finally:
        persistence.close()


def test_short_exact_source_less_replay_is_not_left_as_a_visible_tail(tmp_path):
    persistence = V2Persistence(tmp_path / "short-source-less-replay.db")
    try:
        _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="上线。",
            source_track="microphone",
            started_at_ms=1_000,
            ended_at_ms=2_000,
        )
        replay = _commit(
            persistence,
            final_id="replayed-final-1",
            segment_id="segment-1",
            text="上线。",
            source_track=None,
            started_at_ms=1_000,
            ended_at_ms=2_000,
        )

        assert replay["source_duplicate"] is True
        assert replay["source_duplicate_similarity"] == 1.0
        assert len(persistence.list_transcript_segments("dual-source-meeting")["segments"]) == 1
    finally:
        persistence.close()


def test_existing_source_less_replay_is_repaired_once_on_reopen(tmp_path):
    database_path = tmp_path / "legacy-source-less-replay.db"
    persistence = V2Persistence(database_path)
    _commit(
        persistence,
        final_id="mic-final-1",
        segment_id="microphone:segment-1",
        text="确认发布窗口、回滚负责人以及验证步骤。",
        source_track="microphone",
        started_at_ms=1_000,
        ended_at_ms=4_000,
    )
    _commit(
        persistence,
        final_id="replayed-final-1",
        segment_id="segment-1",
        text="确认发布窗口、回滚负责人和验证步骤。",
        source_track=None,
        started_at_ms=10_000,
        ended_at_ms=13_000,
    )
    persistence.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE transcript_segments SET started_at_ms = 1000, ended_at_ms = 4000 "
            "WHERE meeting_id = 'dual-source-meeting' AND segment_id = 'segment-1'"
        )

    migrated = V2Persistence(database_path)
    first_audit = migrated.list_transcript_segments(
        "dual-source-meeting", include_source_duplicates=True
    )
    first_paragraphs = migrated.list_semantic_paragraphs("dual-source-meeting")["paragraphs"]
    migrated.close()

    reopened = V2Persistence(database_path)
    second_audit = reopened.list_transcript_segments(
        "dual-source-meeting", include_source_duplicates=True
    )
    second_paragraphs = reopened.list_semantic_paragraphs("dual-source-meeting")["paragraphs"]
    reopened.close()

    assert first_audit["segments"][1]["duplicate_of_segment_id"] == "microphone:segment-1"
    assert first_audit == second_audit
    assert [paragraph["checkpoint_ids"] for paragraph in first_paragraphs] == [
        ["microphone:segment-1"]
    ]
    assert first_paragraphs == second_paragraphs


def test_distinct_overlapping_tracks_do_not_merge_into_one_semantic_paragraph(tmp_path):
    persistence = V2Persistence(tmp_path / "source-aware-semantic-boundary.db")
    try:
        first = _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="本地负责人确认回滚窗口和验证步骤。",
            source_track="microphone",
            started_at_ms=1_000,
            ended_at_ms=4_000,
        )
        second = _commit(
            persistence,
            final_id="system-final-1",
            segment_id="system_audio:segment-1",
            text="远端团队补充发布指标和观察时长。",
            source_track="system_audio",
            started_at_ms=1_100,
            ended_at_ms=4_100,
        )

        assert first["source_duplicate"] is False
        assert second["source_duplicate"] is False
        paragraphs = persistence.list_semantic_paragraphs("dual-source-meeting")["paragraphs"]
        assert len(paragraphs) == 2
        assert {paragraph["text"] for paragraph in paragraphs} == {
            "本地负责人确认回滚窗口和验证步骤。",
            "远端团队补充发布指标和观察时长。",
        }
    finally:
        persistence.close()


def test_source_duplicate_retry_is_idempotent_and_does_not_enqueue_jobs(tmp_path):
    persistence = V2Persistence(tmp_path / "source-aware-retry.db")
    try:
        _commit(
            persistence,
            final_id="mic-final-1",
            segment_id="microphone:segment-1",
            text="发布窗口确定为周五晚上十点",
            source_track="microphone",
            started_at_ms=1_000,
            ended_at_ms=2_000,
        )
        first = _commit(
            persistence,
            final_id="system-final-1",
            segment_id="system_audio:segment-1",
            text="发布窗口确定为周五晚上十点",
            source_track="system_audio",
            started_at_ms=1_050,
            ended_at_ms=2_050,
        )
        retried = _commit(
            persistence,
            final_id="system-final-1",
            segment_id="system_audio:segment-1",
            text="发布窗口确定为周五晚上十点",
            source_track="system_audio",
            started_at_ms=1_050,
            ended_at_ms=2_050,
        )

        assert first["created"] is True
        assert retried["created"] is False
        assert retried["source_duplicate"] is True
        assert retried["job_ids"] == {}
        assert len(
            persistence.list_transcript_segments(
                "dual-source-meeting",
                include_source_duplicates=True,
            )["segments"]
        ) == 2
    finally:
        persistence.close()


def test_v2_ingress_qualifies_same_recognizer_segment_id_by_track(tmp_path):
    app = create_app(data_dir=tmp_path)
    persistence = app.state.v2_persistence
    try:
        microphone = app.state.commit_v2_final(
            "ingress-meeting",
            {
                "segment_id": "stream-segment-1",
                "text": "确认今天完成接口联调并准备回滚方案",
                "start_ms": 1_000,
                "end_ms": 2_500,
                "source_track": "microphone",
            },
        )
        system_audio = app.state.commit_v2_final(
            "ingress-meeting",
            {
                "segment_id": "stream-segment-1",
                "text": "确认今天完成接口联调，并准备回滚方案。",
                "start_ms": 1_050,
                "end_ms": 2_550,
                "source_track": "system_audio",
            },
        )

        assert microphone["segment_id"] == "microphone:stream-segment-1"
        assert system_audio["segment_id"] == "system_audio:stream-segment-1"
        assert system_audio["source_duplicate"] is True
        audit = persistence.list_transcript_segments(
            "ingress-meeting",
            include_source_duplicates=True,
        )
        assert len(audit["segments"]) == 2
    finally:
        persistence.close()

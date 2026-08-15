from __future__ import annotations

from meeting_copilot_web_mvp.app import _v2_intelligence_semantic_windows
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _commit(
    persistence: V2Persistence,
    *,
    final_id: str,
    segment_id: str,
    text: str,
    source_track: str,
    started_at_ms: int,
    ended_at_ms: int,
) -> None:
    persistence.commit_final_and_enqueue(
        meeting_id="meeting-source-aware",
        final_id=final_id,
        segment_id=segment_id,
        text=text,
        normalized_text=text,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
        source_track=source_track,
        evidence_hash=f"hash-{final_id}",
        now_ms=ended_at_ms,
        enqueue_jobs=False,
    )


def test_sqlite_semantic_projection_reaches_realtime_intelligence_with_source_roles(tmp_path) -> None:
    persistence = V2Persistence(tmp_path / "source-aware.db")
    try:
        _commit(
            persistence,
            final_id="local-final",
            segment_id="local-segment",
            text="压测还没有完成。",
            source_track="microphone",
            started_at_ms=0,
            ended_at_ms=1_000,
        )
        _commit(
            persistence,
            final_id="remote-final",
            segment_id="remote-segment",
            text="你能承诺周五一定上线吗？",
            source_track="system_audio",
            started_at_ms=1_100,
            ended_at_ms=2_000,
        )
        segments = persistence.list_transcript_segments(
            "meeting-source-aware",
            limit=100,
        )["segments"]

        windows = _v2_intelligence_semantic_windows(
            persistence,
            meeting_id="meeting-source-aware",
            segments=segments,
        )

        assert [window["segment_ids"] for window in windows] == [
            ["local-segment"],
            ["remote-segment"],
        ]
        assert windows[0]["source_tracks"] == ["microphone"]
        assert windows[0]["role_hints"] == ["self_or_room"]
        assert windows[0]["text"] == "[self_or_room] 压测还没有完成。"
        assert windows[1]["source_tracks"] == ["system_audio"]
        assert windows[1]["role_hints"] == ["remote_mix"]
        assert windows[1]["text"] == "[remote_mix] 你能承诺周五一定上线吗？"
    finally:
        persistence.close()

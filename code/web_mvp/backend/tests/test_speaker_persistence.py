from __future__ import annotations

import sqlite3

import pytest

from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def _commit_speaker_final(
    persistence: V2Persistence,
    *,
    meeting_id: str = "meeting-1",
    number: int = 1,
    speaker_id: str | None = None,
    speaker_confidence: float | None = None,
    started_at_ms: int | None = None,
) -> dict[str, object]:
    start_ms = started_at_ms if started_at_ms is not None else number * 1_000
    text = f"第 {number} 位发言人的会议内容。"
    return persistence.commit_final_and_enqueue(
        meeting_id=meeting_id,
        final_id=f"{meeting_id}-final-{number}",
        segment_id=f"{meeting_id}-segment-{number}",
        text=text,
        normalized_text=text,
        started_at_ms=start_ms,
        ended_at_ms=start_ms + 600,
        evidence_hash=f"{meeting_id}-hash-{number}",
        speaker_id=speaker_id,
        speaker_confidence=speaker_confidence,
        now_ms=start_ms + 700,
        enqueue_jobs=False,
    )


def test_legacy_transcript_and_paragraph_tables_gain_nullable_speaker_columns(tmp_path):
    database_path = tmp_path / "legacy-speakers.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE transcript_segments (
                meeting_id TEXT NOT NULL,
                segment_id TEXT NOT NULL,
                final_id TEXT NOT NULL,
                transcript_seq INTEGER NOT NULL,
                text TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                started_at_ms INTEGER,
                ended_at_ms INTEGER,
                revision INTEGER NOT NULL DEFAULT 1,
                evidence_hash TEXT NOT NULL,
                correction_status TEXT NOT NULL DEFAULT 'pending',
                correction_before_text TEXT,
                correction_after_text TEXT,
                correction_error_class TEXT,
                correction_updated_at_ms INTEGER,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (meeting_id, segment_id),
                UNIQUE (meeting_id, final_id),
                UNIQUE (meeting_id, transcript_seq)
            );
            CREATE TABLE semantic_paragraphs (
                meeting_id TEXT NOT NULL,
                paragraph_id TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 1,
                text TEXT NOT NULL,
                start_ms INTEGER,
                end_ms INTEGER,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                PRIMARY KEY (meeting_id, paragraph_id)
            );
            INSERT INTO transcript_segments (
                meeting_id, segment_id, final_id, transcript_seq, text, normalized_text,
                started_at_ms, ended_at_ms, revision, evidence_hash, created_at_ms, updated_at_ms
            ) VALUES ('legacy-meeting', 'legacy-segment', 'legacy-final', 1, '旧文字', '旧文字',
                10, 20, 3, 'legacy-hash', 30, 40);
            INSERT INTO semantic_paragraphs VALUES (
                'legacy-meeting', 'legacy-paragraph', 4, '旧自然段', 10, 20, 'stable', 30, 40
            );
            """
        )

    persistence = V2Persistence(database_path)
    persistence.close()

    with sqlite3.connect(database_path) as connection:
        transcript_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(transcript_segments)")
        }
        paragraph_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(semantic_paragraphs)")
        }
        assert {"speaker_id", "speaker_label", "speaker_confidence"} <= transcript_columns
        assert {"speaker_id", "speaker_label", "speaker_confidence"} <= paragraph_columns
        assert connection.execute(
            "SELECT revision, started_at_ms, ended_at_ms, speaker_id, speaker_label, "
            "speaker_confidence FROM transcript_segments"
        ).fetchone() == (3, 10, 20, None, None, None)
        assert connection.execute(
            "SELECT revision, start_ms, end_ms, speaker_id, speaker_label, "
            "speaker_confidence FROM semantic_paragraphs"
        ).fetchone() == (4, 10, 20, None, None, None)


def test_first_seen_speakers_receive_stable_non_personal_labels_and_split_paragraphs(tmp_path):
    persistence = V2Persistence(tmp_path / "speakers.db")
    try:
        for number, speaker_id in enumerate(("cluster-a", "cluster-b", "cluster-c"), start=1):
            _commit_speaker_final(
                persistence,
                number=number,
                speaker_id=speaker_id,
                speaker_confidence=0.9,
                started_at_ms=number * 1_000,
            )

        assert persistence.list_meeting_speakers("meeting-1") == {
            "meeting_id": "meeting-1",
            "speakers": [
                {
                    "speaker_id": "cluster-a",
                    "speaker_label": "Speaker 1",
                    "ordinal": 1,
                    "created_at_ms": 1_700,
                    "updated_at_ms": 1_700,
                },
                {
                    "speaker_id": "cluster-b",
                    "speaker_label": "Speaker 2",
                    "ordinal": 2,
                    "created_at_ms": 2_700,
                    "updated_at_ms": 2_700,
                },
                {
                    "speaker_id": "cluster-c",
                    "speaker_label": "Speaker 3",
                    "ordinal": 3,
                    "created_at_ms": 3_700,
                    "updated_at_ms": 3_700,
                },
            ],
        }
        snapshot = persistence.get_snapshot("meeting-1")
        assert [segment["speaker_label"] for segment in snapshot["segments"]] == [
            "Speaker 1",
            "Speaker 2",
            "Speaker 3",
        ]
        assert [paragraph["speaker_id"] for paragraph in snapshot["semantic_paragraphs"]] == [
            "cluster-a",
            "cluster-b",
            "cluster-c",
        ]
        assert [paragraph["speaker_confidence"] for paragraph in snapshot["semantic_paragraphs"]] == [
            0.9,
            0.9,
            0.9,
        ]
    finally:
        persistence.close()


def test_unknown_or_low_confidence_speaker_can_remain_unattributed(tmp_path):
    persistence = V2Persistence(tmp_path / "nullable-speaker.db")
    try:
        _commit_speaker_final(persistence, speaker_id=None, speaker_confidence=None)
        _commit_speaker_final(
            persistence,
            number=2,
            speaker_id="low-confidence-cluster",
            speaker_confidence=None,
        )

        segments = persistence.list_transcript_segments("meeting-1")["segments"]
        paragraphs = persistence.list_semantic_paragraphs("meeting-1")["paragraphs"]
        segment = segments[0]
        paragraph = paragraphs[0]
        assert (segment["speaker_id"], segment["speaker_label"], segment["speaker_confidence"]) == (
            None,
            None,
            None,
        )
        assert (
            paragraph["speaker_id"],
            paragraph["speaker_label"],
            paragraph["speaker_confidence"],
        ) == (None, None, None)
        assert (
            segments[1]["speaker_id"],
            segments[1]["speaker_label"],
            segments[1]["speaker_confidence"],
        ) == ("low-confidence-cluster", "Speaker 1", None)
        assert (
            paragraphs[1]["speaker_id"],
            paragraphs[1]["speaker_label"],
            paragraphs[1]["speaker_confidence"],
        ) == ("low-confidence-cluster", "Speaker 1", None)
    finally:
        persistence.close()


def test_manual_rename_backfills_existing_rows_is_meeting_scoped_and_survives_reopen(tmp_path):
    database_path = tmp_path / "rename.db"
    persistence = V2Persistence(database_path)
    _commit_speaker_final(persistence, meeting_id="meeting-1", speaker_id="cluster-a")
    _commit_speaker_final(persistence, meeting_id="meeting-2", speaker_id="cluster-a")
    before = persistence.get_transcript_segment("meeting-1", "meeting-1-segment-1")

    renamed = persistence.rename_meeting_speaker(
        meeting_id="meeting-1",
        speaker_id="cluster-a",
        speaker_label="  张工  ",
        now_ms=5_000,
    )

    assert renamed["speaker_label"] == "张工"
    updated_segment = persistence.get_transcript_segment("meeting-1", "meeting-1-segment-1")
    updated_paragraph = persistence.list_semantic_paragraphs("meeting-1")["paragraphs"][0]
    assert updated_segment["speaker_label"] == "张工"
    assert updated_paragraph["speaker_label"] == "张工"
    assert updated_segment["revision"] == before["revision"]
    assert updated_segment["started_at_ms"] == before["started_at_ms"]
    assert updated_segment["ended_at_ms"] == before["ended_at_ms"]
    assert persistence.get_transcript_segment(
        "meeting-2", "meeting-2-segment-1"
    )["speaker_label"] == "Speaker 1"
    persistence.close()

    reopened = V2Persistence(database_path)
    try:
        assert reopened.list_meeting_speakers("meeting-1")["speakers"][0]["speaker_label"] == "张工"
        assert reopened.get_snapshot("meeting-1")["segments"][0]["speaker_label"] == "张工"
        assert reopened.get_snapshot("meeting-1")["semantic_paragraphs"][0]["speaker_label"] == "张工"
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("speaker_id", "speaker_confidence"),
    [
        ("", None),
        ("speaker/1", None),
        ("speaker-1", -0.01),
        ("speaker-1", 1.01),
        ("speaker-1", float("nan")),
        (None, 0.5),
    ],
)
def test_invalid_speaker_attribution_is_rejected(tmp_path, speaker_id, speaker_confidence):
    persistence = V2Persistence(tmp_path / "invalid-speaker.db")
    try:
        with pytest.raises(ValueError):
            _commit_speaker_final(
                persistence,
                speaker_id=speaker_id,
                speaker_confidence=speaker_confidence,
            )
    finally:
        persistence.close()


@pytest.mark.parametrize("speaker_label", ["", "\x00name", "x" * 81])
def test_invalid_manual_speaker_label_is_rejected(tmp_path, speaker_label):
    persistence = V2Persistence(tmp_path / "invalid-label.db")
    try:
        _commit_speaker_final(persistence, speaker_id="cluster-a")
        with pytest.raises(ValueError):
            persistence.rename_meeting_speaker(
                meeting_id="meeting-1",
                speaker_id="cluster-a",
                speaker_label=speaker_label,
                now_ms=2_000,
            )
        with pytest.raises(KeyError):
            persistence.rename_meeting_speaker(
                meeting_id="meeting-1",
                speaker_id="cluster-missing",
                speaker_label="Speaker 2",
                now_ms=2_000,
            )
    finally:
        persistence.close()

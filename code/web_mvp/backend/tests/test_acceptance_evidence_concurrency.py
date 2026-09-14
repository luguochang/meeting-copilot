from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from meeting_copilot_web_mvp.v2_persistence import (
    V2Persistence,
    transcript_evidence_hash,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
TOOLS_ROOT = REPO_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pi_stage0_acceptance_evidence_stress as evidence_stress  # noqa: E402


def _seed_capture(tmp_path: Path) -> tuple[V2Persistence, dict]:
    database_path = tmp_path / "meeting_copilot.db"
    persistence = V2Persistence(database_path)
    segment_id = "acceptance-segment"
    text = "Stage zero acceptance evidence."
    persistence.commit_final_and_enqueue(
        meeting_id="acceptance-meeting",
        final_id="acceptance-final",
        segment_id=segment_id,
        text=text,
        normalized_text=text,
        started_at_ms=100,
        ended_at_ms=900,
        evidence_hash=transcript_evidence_hash(segment_id, text),
        now_ms=1_000,
        source_track="microphone",
    )
    return persistence, persistence.capture_acceptance_evidence("acceptance-meeting")


def test_acceptance_capture_lineage_hashes_are_independently_verifiable(tmp_path) -> None:
    persistence, capture = _seed_capture(tmp_path)
    try:
        assert evidence_stress.verify_capture(capture) == []
        lineage = capture["lineage"]
        assert lineage["event_count"] == len(capture["events"])
        assert lineage["job_count"] == len(capture["snapshot"]["jobs"])
        assert lineage["usage_count"] == 0
        assert lineage["live_session_sha256"] is None
        assert lineage["event_sha256"] == hashlib.sha256(
            json.dumps(
                capture["events"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        tampered = json.loads(json.dumps(capture))
        tampered["events"][0]["type"] = "tampered"
        errors = evidence_stress.verify_capture(tampered)
        assert "capture_sha256_mismatch" in errors
        assert "event_sha256_mismatch" in errors
    finally:
        persistence.close()


def test_capture_fails_closed_when_live_authoritative_final_diverges(tmp_path) -> None:
    persistence, _capture = _seed_capture(tmp_path)
    try:
        with persistence._write_transaction():
            live_record = {
                "session_id": "acceptance-meeting",
                "events": [
                    {
                        "event_type": "transcript_final",
                        "payload": {
                            "authoritative": True,
                            "segment_id": "acceptance-segment",
                            "normalized_text": "Divergent live text.",
                        },
                    }
                ],
            }
            persistence._conn.execute(
                "INSERT INTO asr_live_sessions "
                "(session_id, record_json, created_at_ms, last_activity_ms, source, has_audio) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "acceptance-meeting",
                    json.dumps(live_record),
                    1_000,
                    1_100,
                    "browser_live_mic",
                    1,
                ),
            )

        capture = persistence.capture_acceptance_evidence("acceptance-meeting")
        assert capture["consistency"]["acceptance_eligible"] is False
        assert capture["consistency"]["errors"] == [
            "live_session_segment_text_mismatch:acceptance-segment"
        ]
        assert "capture_not_acceptance_eligible" in evidence_stress.verify_capture(
            capture
        )
    finally:
        persistence.close()


def test_spawned_writers_and_readers_preserve_atomic_capture(tmp_path) -> None:
    report = evidence_stress.run_stress(
        tmp_path / "stress",
        writer_processes=2,
        reader_processes=2,
        writes_per_process=4,
    )

    assert report["passed"] is True
    assert report["process_start_method"] == "spawn"
    assert report["writer_exit_codes"] == [0, 0]
    assert report["reader_exit_codes"] == [0, 0]
    assert report["concurrent_final_capture"]["segment_count"] == 8
    assert report["concurrent_final_capture"]["verification_errors"] == []
    assert report["concurrent_final_capture"]["live_session_present"] is True
    assert report["concurrent_final_capture"]["usage_count"] == 1
    assert all(
        item["invalid_capture_count"] == 0 for item in report["reader_results"]
    )
    assert all(
        item["distinct_transcript_high_water_count"] >= 2
        for item in report["reader_results"]
    )

    boundary = report["pinned_boundary"]
    assert boundary["passed"] is True
    assert (
        boundary["pinned_transcript_high_water"]
        == boundary["before_transcript_high_water"]
    )
    assert (
        boundary["after_transcript_high_water"]
        == boundary["before_transcript_high_water"] + 1
    )
    assert boundary["pinned_capture_sha256"] == boundary["before_capture_sha256"]
    assert boundary["after_capture_sha256"] != boundary["pinned_capture_sha256"]

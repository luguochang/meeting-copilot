from __future__ import annotations

import importlib.util
from pathlib import Path
import time

from meeting_copilot_web_mvp.sqlite_repository import SqliteAsrLiveSessionRepository
from meeting_copilot_web_mvp.v2_persistence import V2Persistence, transcript_evidence_hash


SCRIPT = Path(__file__).resolve().parents[4] / "tools/participation_acceptance_story.py"
spec = importlib.util.spec_from_file_location("participation_acceptance_story", SCRIPT)
story = importlib.util.module_from_spec(spec)
spec.loader.exec_module(story)


def test_story_seed_mirrors_disclosed_synthetic_text_and_canonical_hash(tmp_path):
    store = story.open_synthetic_store(tmp_path / "meeting_copilot.db")
    repository = SqliteAsrLiveSessionRepository(tmp_path)
    try:
        story.create_synthetic_live_record(repository, "story-seed")
        turn = {"segment_id": "synthetic-s1", "text": "通知可以稍后到达。", "started_at_ms": 0, "ended_at_ms": 15_000}
        seeded = story.seed_synthetic_final(store, repository, "story-seed", turn, 1)
        segment = store.list_transcript_segments("story-seed")["segments"][0]
        record = repository.get("story-seed")
        assert segment["evidence_hash"] == transcript_evidence_hash(turn["segment_id"], turn["text"]) == seeded["source_hash"]
        assert record["source"] == "synthetic_text_fixture"
        assert record["provider_mode"] == "synthetic" and record["is_mock"] is True
        assert record["events"][0]["normalized_text"] == segment["normalized_text"]
        assert record["events"][0]["synthetic_fixture"] is True
        assert record["events"][0]["start_ms"] == 0 and record["events"][0]["end_ms"] == 15_000
        assert seeded["live_record_mirrored"] is True
    finally:
        repository.close()
        store.close()


def test_story_seed_never_exposes_incidental_model_jobs_to_another_consumer(tmp_path, monkeypatch):
    database = tmp_path / "meeting_copilot.db"
    store = story.open_synthetic_store(database)
    observer = V2Persistence(database, semantic_projection_mode="llm_first")
    repository = SqliteAsrLiveSessionRepository(tmp_path)
    observed_uncommitted_counts = []
    original_commit = store.commit_final_and_enqueue

    def observed_commit(**kwargs):
        result = original_commit(**kwargs)
        # Observe between final+job insertion and cancellation on a separate
        # connection. The outer transaction must keep both invisible.
        observed_uncommitted_counts.append(observer._conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE meeting_id=? AND status='pending'", (kwargs["meeting_id"],)
        ).fetchone()[0])
        return result

    monkeypatch.setattr(store, "commit_final_and_enqueue", observed_commit)
    try:
        story.create_synthetic_live_record(repository, "atomic-seed")
        seeded = story.seed_synthetic_final(store, repository, "atomic-seed", {"segment_id": "s1", "text": "本轮讨论权益发放。"}, 1)
        assert observed_uncommitted_counts == [0]
        jobs = observer.list_jobs(meeting_id="atomic-seed")
        assert set(seeded["cancelled_job_ids"]) == {job["id"] for job in jobs}
        assert {job["status"] for job in jobs} == {"cancelled"}
        now_ms = time.time_ns() // 1_000_000
        for lane in ("intelligence", "pi_deep", "correction", "answer"):
            assert observer.claim_next_job(worker_id="no-paid-work", lane=lane, now_ms=now_ms, lease_ms=5_000) is None
    finally:
        repository.close()
        observer.close()
        store.close()

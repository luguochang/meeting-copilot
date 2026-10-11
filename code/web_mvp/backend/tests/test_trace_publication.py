"""Real commit/claim scheduling must not drop the producer's trace stages."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Event
import time

import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.pipeline_trace import PipelineTraceCollector


def _final(segment_id="segment-1"):
    return {
        "event_type": "final", "segment_id": segment_id,
        "text": "这次讨论先核对部署流程和验收范围。", "start_ms": 0, "end_ms": 1_000,
        "received_at_ms": 1_000, "source_track": "microphone",
    }


def _app(tmp_path, monkeypatch, *, mode="legacy"):
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "0")
    app = create_app(data_dir=tmp_path, semantic_projection_mode=mode)
    return app, app.state.v2_persistence, app.state.pipeline_traces


@pytest.mark.parametrize("mode", ["legacy", "llm_first"])
def test_polling_worker_claims_visible_sql_but_observes_trace_after_publication(
    tmp_path, monkeypatch, caplog, mode,
):
    app, persistence, traces = _app(tmp_path, monkeypatch, mode=mode)
    executor = app.state.v2_executor
    executor._poll_interval_s = 0.005
    sql_visible = Event()
    release_producer = Event()
    sql_claimed = Event()
    worker_finished = Event()
    real_commit = persistence.commit_final_and_enqueue
    real_observer = executor._lifecycle_observer
    claimed_events = []
    handler_started = {}

    def pause_after_real_sql_commit(**kwargs):
        committed = real_commit(**kwargs)
        sql_visible.set()
        assert release_producer.wait(5), "producer was not released"
        return committed

    def observe_real_claim(event):
        if event["event"] == "job_claimed":
            claimed_events.append(dict(event))
            sql_claimed.set()
        real_observer(event)

    def synthetic_handler(job):
        # This is the existing injectable provider/handler boundary. No model
        # implementation is entered, but the production wrappers must precede it.
        handler_started[job["id"]] = time.monotonic_ns()
        return {"called": False, "provider_attempt_count": 0,
                "transcript_revisions": [], "generated_card_count": 0, "runs": []}

    for lane in ("correction", "suggestion", "answer", "intelligence"):
        setattr(app.state, f"v2_{lane}_job_handler_impl", synthetic_handler)

    async def synthetic_intelligence_handler(job):
        return synthetic_handler(job)

    app.state.v2_intelligence_job_handler_impl = synthetic_intelligence_handler
    monkeypatch.setattr(persistence, "commit_final_and_enqueue", pause_after_real_sql_commit)
    executor._lifecycle_observer = observe_real_claim

    async def run_real_worker():
        await executor.start()
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                jobs = persistence.list_jobs(meeting_id="publication-race")
                if jobs and all(job["status"] == "succeeded" for job in jobs):
                    worker_finished.set()
                    return
                await asyncio.sleep(0.005)
            raise AssertionError(f"workers did not settle: {jobs}")
        finally:
            await executor.stop()

    with ThreadPoolExecutor(max_workers=2) as pool:
        producer = pool.submit(app.state.commit_v2_final, "publication-race", _final())
        worker = None
        try:
            assert sql_visible.wait(5)
            assert persistence.list_transcript_segments("publication-race")["segments"]
            worker = pool.submit(lambda: asyncio.run(run_real_worker()))
            assert sql_claimed.wait(5)
            jobs = persistence.list_jobs(meeting_id="publication-race")
            assert any(job["status"] == "running" for job in jobs)
            assert not handler_started
            # Publication uses its own lock; reading the collector is still free.
            assert traces.find(meeting_id="publication-race") == []
            assert not worker_finished.is_set()
        finally:
            release_producer.set()
        committed = producer.result(timeout=5)
        assert worker is not None
        worker.result(timeout=5)

    assert len(committed["job_ids"]) >= 2
    first_claim = claimed_events[0]
    first_trace = traces.export(first_claim["job_id"])
    # The actual executor clock precedes publication and remains unchanged.
    sql_at_ns = first_claim["lifecycle_at_monotonic_ns"]
    assert first_trace["execution"]["provenance"]["lifecycle_lookup"]["at_monotonic_ns"] == sql_at_ns
    assert sql_at_ns < first_trace["stages"]["final_committed"]["monotonic_ns"]
    for job_id in committed["job_ids"].values():
        trace = traces.export(job_id)
        stages = trace["stages"]
        assert stages["final_committed"]["monotonic_ns"] <= stages["job_queued"]["monotonic_ns"]
        assert stages["job_queued"]["monotonic_ns"] <= stages["job_claimed"]["monotonic_ns"]
        assert stages["job_claimed"]["monotonic_ns"] <= handler_started[job_id]
        assert trace["meeting_id"] == "publication-race"
        assert trace["job_id"] == job_id
        assert trace["execution"]["provenance"]["speech_endpoint"]["status"] == "observed"
        # These synthetic handlers have no actual Provider latency observations.
        assert trace["latency"]["complete"] is False
    assert "Pipeline trace observation dropped" not in caplog.text


def test_sql_rollback_publishes_nothing_and_releases_guard(tmp_path, monkeypatch):
    app, persistence, traces = _app(tmp_path, monkeypatch, mode="llm_first")
    with persistence._lock:
        persistence._conn.execute(
            "CREATE TEMP TRIGGER r02_abort_final BEFORE INSERT ON transcript_segments "
            "BEGIN SELECT RAISE(ABORT, 'synthetic trace rollback'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic trace rollback"):
        app.state.commit_v2_final("publication-rollback", _final())
    assert not persistence.list_jobs(meeting_id="publication-rollback")
    assert not traces.find(meeting_id="publication-rollback")
    with persistence._lock:
        persistence._conn.execute("DROP TRIGGER r02_abort_final")
    with ThreadPoolExecutor(max_workers=1) as pool:
        committed = pool.submit(app.state.commit_v2_final, "publication-rollback", _final()).result(timeout=5)
    assert committed["created"] is True
    assert all("job_queued" in traces.export(job_id)["stages"] for job_id in committed["job_ids"].values())


def test_post_commit_publication_exception_releases_guard_without_rewriting_sql(tmp_path, monkeypatch):
    app, persistence, traces = _app(tmp_path, monkeypatch)
    real_provenance = traces.record_provenance_stage

    def failed_publication(*args, **kwargs):
        raise RuntimeError("synthetic publisher failure")

    monkeypatch.setattr(traces, "record_provenance_stage", failed_publication)
    with pytest.raises(RuntimeError, match="synthetic publisher failure"):
        app.state.commit_v2_final("publication-error", _final())
    assert persistence.list_transcript_segments("publication-error")["segments"]
    assert persistence.list_jobs(meeting_id="publication-error")
    assert all(not trace.latency_breakdown()["complete"] for trace in traces.find(meeting_id="publication-error"))
    monkeypatch.setattr(traces, "record_provenance_stage", real_provenance)
    with ThreadPoolExecutor(max_workers=1) as pool:
        committed = pool.submit(app.state.commit_v2_final, "publication-error", _final("segment-2")).result(timeout=5)
    assert committed["created"] is True


def test_guard_is_reentrant_and_duplicate_commit_preserves_first_marks(tmp_path, monkeypatch):
    app, _persistence, traces = _app(tmp_path, monkeypatch)
    with traces.publication_guard():
        with traces.publication_guard():
            first = app.state.commit_v2_final("publication-duplicate", _final())
    snapshots = {job_id: traces.export(job_id) for job_id in first["job_ids"].values()}
    duplicate = app.state.commit_v2_final("publication-duplicate", _final())
    assert duplicate["created"] is False
    assert duplicate["job_ids"] == first["job_ids"]
    assert snapshots == {job_id: traces.export(job_id) for job_id in first["job_ids"].values()}


def test_guard_exception_unlocks_other_threads_and_does_not_hold_collector_data_lock():
    traces = PipelineTraceCollector()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="synthetic guard exit"):
            with traces.publication_guard():
                assert pool.submit(traces.find, meeting_id="guard-read").result(timeout=2) == []
                raise RuntimeError("synthetic guard exit")

        def publish_after_exception():
            with traces.publication_guard():
                with traces.publication_guard():
                    return traces.record("guard-trace", "job_queued", meeting_id="guard-read")

        mark = pool.submit(publish_after_exception).result(timeout=2)
    assert mark.monotonic_ns >= 0


def test_guard_keeps_strict_timestamp_rejection_and_first_observation():
    traces = PipelineTraceCollector()
    with traces.publication_guard():
        first = traces.record("strict-trace", "job_claimed", meeting_id="strict", monotonic_ns=100)
        with pytest.raises(ValueError, match="follows the earliest later stage"):
            traces.record("strict-trace", "final_committed", meeting_id="strict", monotonic_ns=200)
        assert traces.record("strict-trace", "job_claimed", meeting_id="strict", monotonic_ns=300) is first
    assert traces.export("strict-trace")["stages"] == {
        "job_claimed": {"stage": "job_claimed", "monotonic_ns": 100, "attributes": {}},
    }

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import IntelligenceDeadlineExceeded, JobLeaseLostError
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


USAGE = {"prompt_tokens": 70, "completion_tokens": 10, "total_tokens": 80}
STRUCTURED = {"background": "已核对接口方案", "decisions": [], "action_items": [],
              "risks": [], "open_questions": [], "evidence_quotes": []}
CARD = {"card_id": "candidate", "title": "核对发布条件", "body": "保留原始依据"}


@pytest.fixture
def review_app(tmp_path, monkeypatch):
    config = app_module.llm_service.LlmConfig(
        base_url="https://synthetic.example", api_key="synthetic-test-key", model="synthetic-model",
    )
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module.llm_service, "build_minutes_artifact",
                        lambda *_a: ("# 新纪要", STRUCTURED, USAGE, False))
    monkeypatch.setattr(app_module.llm_service, "build_approach_cards",
                        lambda *_a: ([CARD], USAGE, False))
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    p.commit_final_and_enqueue(
        meeting_id="review", final_id="f1", segment_id="s1", text="接口方案已经核对。",
        normalized_text="接口方案已经核对。", started_at_ms=0, ended_at_ms=1_000,
        evidence_hash="original", now_ms=now, enqueue_answer=False,
    )
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET status='cancelled' WHERE meeting_id='review'")
    app.state.asr_live_repository.create({
        "session_id": "review", "source": "live_asr_stream", "trace_kind": "live_event",
        "provider": "funasr_realtime", "provider_mode": "real", "is_mock": False,
        "input_source": "browser_live_mic", "ingest_mode": "live_asr_stream",
        "asr_fallback_used": False, "degradation_reasons": [],
        "minutes": {"minutes_md": "旧纪要"}, "approach_cards": [{"card_id": "old"}],
        "events": [{"id": "final:s1", "event_type": "transcript_final", "payload": {
            "segment_id": "s1", "text": "接口方案已经核对。", "normalized_text": "接口方案已经核对。",
        }}],
    })
    p.end_meeting(meeting_id="review", now_ms=now)
    with p._write_transaction():
        # END legitimately creates a replacement for the earlier cancelled correction.
        p._conn.execute("UPDATE jobs SET status='cancelled' WHERE meeting_id='review' AND kind='correction'")
    app.state.test_clock = [now]
    p._projection_clock_ms = lambda: app.state.test_clock[0]
    yield app
    p.close()


def claim(app, kind, *, deadline=True):
    p = app.state.v2_persistence
    now = app.state.test_clock[0]
    if not deadline:
        with p._write_transaction():
            p._conn.execute("UPDATE jobs SET deadline_at_ms=NULL WHERE meeting_id='review' AND kind=?", (kind,))
    job = p.claim_next_job(worker_id="review-worker", lane=kind, now_ms=now, lease_ms=180_000)
    assert job is not None
    return job


def complete_native_review_job(app, kind):
    job = claim(app, kind)
    output = app.state.v2_post_job_handler_impls[kind](job)
    assert app.state.v2_persistence.complete_job(
        job_id=job["id"], worker_id="review-worker", now_ms=app.state.test_clock[0], output=output,
    )
    return output


def products(app):
    p = app.state.v2_persistence
    tables = ("minutes", "approach_artifacts", "search_documents", "review_documents", "review_document_revisions")
    with p._lock:
        data = {table: [tuple(row) for row in p._conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                for table in tables}
        data["ready_events"] = [tuple(row) for row in p._conn.execute(
            "SELECT * FROM meeting_events WHERE type IN "
            "('meeting.minutes.ready','meeting.approach.ready','meeting.index.ready') ORDER BY seq")]
    record = app.state.asr_live_repository.get("review")
    data["legacy_products"] = {key: record.get(key) for key in ("minutes", "approach_cards")}
    return data


def usage_total(app):
    return app.state.v2_persistence._conn.execute(
        "SELECT COALESCE(SUM(total_tokens),0) FROM llm_usage_ledger WHERE session_id='review'"
    ).fetchone()[0]


@pytest.mark.parametrize("kind", ["minutes", "approach"])
@pytest.mark.parametrize("mutation", ["cancel", "lease", "attempt", "deadline", "source"])
def test_native_response_cannot_publish_after_claim_or_source_changes(review_app, monkeypatch, kind, mutation):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, kind)
    entered, release = Event(), Event()

    def blocked(*_args):
        entered.set()
        assert release.wait(3)
        return ("# 新纪要", STRUCTURED, USAGE, False) if kind == "minutes" else ([CARD], USAGE, False)

    monkeypatch.setattr(app_module.llm_service,
                        "build_minutes_artifact" if kind == "minutes" else "build_approach_cards", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(app.state.v2_post_job_handler_impls[kind], job)
        assert entered.wait(3)
        if mutation == "source":
            p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="original",
                corrected_text="接口方案尚未核对。", revision_id="changed", now_ms=app.state.test_clock[0] + 1,
                evidence_remap_reason="validated_meaning_preserved_correction")
        else:
            with p._write_transaction():
                if mutation == "cancel":
                    p._conn.execute("UPDATE jobs SET status='cancelled',lease_owner=NULL,lease_until_ms=NULL WHERE id=?", (job["id"],))
                elif mutation == "lease":
                    p._conn.execute("UPDATE jobs SET lease_until_ms=? WHERE id=?", (app.state.test_clock[0] - 1, job["id"]))
                elif mutation == "attempt":
                    p._conn.execute("UPDATE jobs SET attempts=attempts+1 WHERE id=?", (job["id"],))
                else:
                    app.state.test_clock[0] = job["deadline_at_ms"] + 1
        before = products(app)
        release.set()
        error = future.exception(timeout=3)
    assert products(app) == before
    assert error is not None
    assert usage_total(app) == 80


@pytest.mark.parametrize("kind", ["minutes", "approach"])
def test_executor_timeout_cannot_publish_from_late_sync_thread(review_app, monkeypatch, kind):
    app = review_app
    p = app.state.v2_persistence
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET deadline_at_ms=? WHERE meeting_id='review' AND kind=?",
                        (app.state.test_clock[0] + 120, kind))
    job = claim(app, kind)
    entered, release, finished = Event(), Event(), Event()

    def blocked(*_args):
        entered.set()
        assert release.wait(3)
        return ("# 超时新稿", STRUCTURED, USAGE, False) if kind == "minutes" else ([CARD], USAGE, False)

    monkeypatch.setattr(app_module.llm_service,
                        "build_minutes_artifact" if kind == "minutes" else "build_approach_cards", blocked)
    native = app.state.v2_post_job_handler_impls[kind]

    def handler(item):
        try:
            return native(item)
        finally:
            finished.set()

    async def run():
        executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {},
                                      additional_handlers={kind: handler}, now_ms=lambda: app.state.test_clock[0])
        before = products(app)
        task = asyncio.create_task(executor._execute_claimed_job(kind, "review-worker", job))
        assert await asyncio.to_thread(entered.wait, 3)
        app.state.test_clock[0] = job["deadline_at_ms"] + 1
        await asyncio.wait_for(task, 3)
        assert p.get_job(job["id"])["status"] == "cancelled"
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        assert products(app) == before

    asyncio.run(run())
    assert usage_total(app) == 80


@pytest.mark.parametrize("kind", ["minutes", "approach", "index"])
def test_transaction_reads_fresh_deadline_after_waiting_for_write_lock(review_app, kind):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, kind)
    before = products(app)
    with p._lock:
        with ThreadPoolExecutor(max_workers=1) as pool:
            # The handler's old now_ms cannot authorize publication after the lock opens.
            if kind == "index":
                future = pool.submit(app.state.v2_post_job_handler_impls[kind], job)
            else:
                source = p.capture_review_source("review")
                method = p.save_minutes if kind == "minutes" else p.save_approach_cards
                kwargs = {"markdown": "迟到稿", "structured": STRUCTURED} if kind == "minutes" else {"cards": [CARD]}
                future = pool.submit(method, meeting_id="review", job_id=job["id"], degraded=False,
                                     now_ms=app.state.test_clock[0], expected_transcript_hash=source["hash"],
                                     expected_claim=job, **kwargs)
            app.state.test_clock[0] = job["deadline_at_ms"] + 1
            p._lock.release()
            try:
                error = future.exception(timeout=3)
            finally:
                p._lock.acquire()
    assert isinstance(error, IntelligenceDeadlineExceeded)
    assert products(app) == before


@pytest.mark.parametrize("kind", ["minutes", "approach", "index"])
@pytest.mark.parametrize("deadline", [True, False])
def test_valid_claim_saves_products_once_and_keeps_manual_final(review_app, kind, deadline):
    app = review_app
    p = app.state.v2_persistence
    ai = p.save_ai_generated_document(meeting_id="review", document_kind="minutes",
                                     content={"markdown": "旧初稿"}, author="ai:old", now_ms=app.state.test_clock[0])
    manual = p.save_user_final_document(meeting_id="review", document_kind="minutes", expected_revision=ai["revision"],
                                       content={"markdown": "人工终稿"}, now_ms=app.state.test_clock[0])
    job = claim(app, kind, deadline=deadline)
    result = app.state.v2_post_job_handler_impls[kind](job)
    assert result["correction_degraded"] is True
    assert p.get_review_document("review", "minutes")["user_final"] == manual["user_final"]
    assert p.complete_job(job_id=job["id"], worker_id="review-worker", now_ms=app.state.test_clock[0], output=result)
    snapshot = p.get_snapshot("review")
    record = app.state.asr_live_repository.get("review")
    if kind == "minutes":
        assert snapshot["minutes"]["job_id"] == job["id"]
        assert record["minutes"]["minutes_md"] == "# 新纪要"
        assert result["degraded"] is False
        assert snapshot["review_jobs"][kind]["output"] == {
            "degraded": False, "correction_degraded": True,
            "correction_degraded_reason": "correction_has_no_successful_job",
        }
    elif kind == "approach":
        assert snapshot["approach_artifact"]["job_id"] == job["id"]
        assert any(card["card_id"] == CARD["card_id"] for card in record["approach_cards"])
        assert result["degraded"] is False
    else:
        assert snapshot["review"]["indexed"] is True


@pytest.mark.parametrize("projection_status", ["completed", "partially_completed"])
def test_partial_projection_keeps_generation_success_separate_from_input_quality(review_app, projection_status):
    app = review_app
    p = app.state.v2_persistence
    p.commit_final_and_enqueue(meeting_id="review", final_id="f2", segment_id="s2", text="第二项待核对。",
        normalized_text="第二项待核对。", started_at_ms=1_000, ended_at_ms=2_000,
        evidence_hash="second", now_ms=app.state.test_clock[0], enqueue_answer=False)
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET status='cancelled' WHERE meeting_id='review' AND kind='correction'")
    app.state.asr_live_repository.update("review", lambda old: {**old, "realtime_transcript_correction": {
        "status": projection_status, "processed_segment_ids": ["s1", "s2"], "terminal_failed_segment_ids": ["s2"],
    }})
    quality = app.state.wait_for_v2_correction_jobs("review", timeout_seconds=0.1)
    assert quality["correction_degraded"] is True
    assert quality["reason"] == "correction_partial_coverage"
    assert quality["quality_source"] == "realtime_correction_projection"


@pytest.mark.parametrize("kind", ["minutes", "approach"])
def test_provider_failure_after_five_of_nine_corrections_publishes_readable_degraded_review(
    review_app, kind,
):
    """Replay r04's coverage shape locally, without reading its audio or calling a provider."""
    app = review_app
    p = app.state.v2_persistence
    # Real r04/post-r01 processed all nine, but 004/005/008/009 remained
    # terminally failed. A succeeded job for 007 must not hide those four.
    for index in range(2, 10):
        text = f"第{index}项原文仍可核对。"
        p.commit_final_and_enqueue(
            meeting_id="review", final_id=f"f{index}", segment_id=f"s{index}",
            text=text, normalized_text=text, started_at_ms=(index - 1) * 1_000,
            ended_at_ms=index * 1_000, evidence_hash=f"hash-{index}",
            now_ms=app.state.test_clock[0], enqueue_answer=False,
        )
    with p._write_transaction():
        p._conn.execute(
            "UPDATE jobs SET status='cancelled',error_class='evidence_superseded' "
            "WHERE meeting_id='review' AND kind='correction'",
        )
        p._conn.execute(
            "UPDATE jobs SET status='succeeded',error_class=NULL "
            "WHERE meeting_id='review' AND kind='correction' AND evidence_segment_id='s7'",
        )
        p._conn.execute(
            "UPDATE jobs SET status='failed',error_class='correction_meeting_fact_changed' "
            "WHERE meeting_id='review' AND kind='correction' AND evidence_segment_id IN ('s4','s5')",
        )
        p._conn.execute(
            "UPDATE jobs SET status='failed',error_class='realtime_correction_provider_failed' "
            "WHERE meeting_id='review' AND kind='correction' AND evidence_segment_id IN ('s8','s9')",
        )
    projection = {
        "status": "provider_failed_terminal",
        "processed_segment_ids": [f"s{index}" for index in range(1, 10)],
        "revised_segment_ids": ["s1", "s6", "s7"],
        "rejected_segment_ids": ["s4", "s5"],
        "terminal_failed_segment_ids": ["s4", "s5", "s8", "s9"],
        "skipped_segment_ids": [],
    }
    app.state.asr_live_repository.update("review", lambda old: {
        **old, "realtime_transcript_correction": projection,
        "events": [*old["events"], *[
            {"id": f"final:s{index}", "event_type": "transcript_final", "payload": {
                "segment_id": f"s{index}", "text": f"第{index}项原文仍可核对。",
                "normalized_text": f"第{index}项原文仍可核对。",
            }}
            for index in range(2, 10)
        ]],
    })
    quality = app.state.wait_for_v2_correction_jobs("review", timeout_seconds=0.1)
    assert quality["correction_degraded"] is True
    assert quality["reason"] == "correction_partial_coverage"
    assert quality["quality_source"] == "realtime_correction_projection"

    job = claim(app, kind)
    result = app.state.v2_post_job_handler_impls[kind](job)
    assert result["degraded"] is False  # Generation succeeded; input coverage did not.
    assert result["correction_degraded"] is True
    assert result["correction_degraded_reason"] == "correction_partial_coverage"
    assert p.complete_job(job_id=job["id"], worker_id="review-worker",
                          now_ms=app.state.test_clock[0], output=result)

    client = TestClient(app)
    response = client.get("/v2/meetings/review/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    product = snapshot["minutes" if kind == "minutes" else "approach_artifact"]
    assert snapshot["review"][f"{kind}_status"] == "degraded"
    assert product["job_id"] == job["id"]
    if kind == "minutes":
        assert product["status"] == "degraded"
        assert product["markdown"] == "# 新纪要"
    else:
        assert snapshot["approach_cards"] == [CARD]
    public_job = snapshot["review_jobs"][kind]
    assert public_job["status"] == "succeeded"
    assert public_job["output"] == {
        "degraded": False, "correction_degraded": True,
        "correction_degraded_reason": "correction_partial_coverage",
    }
    assert snapshot["review"]["status"] == "processing"
    assert snapshot["runtime"]["ai"]["capabilities"]["review"] == {
        "state": "busy", "label": "会后整理中",
    }
    for remaining_kind in ("minutes", "approach", "index"):
        if remaining_kind != kind:
            complete_native_review_job(app, remaining_kind)
    completed = client.get("/v2/meetings/review/snapshot").json()
    assert completed["review"]["status"] == "degraded"
    assert completed["runtime"]["ai"]["capabilities"]["review"] == {
        "state": "idle", "label": "会后整理待核对",
    }
    assert completed["minutes"]["markdown"] == "# 新纪要"
    assert completed["approach_cards"] == [CARD]
    assert {item["status"] for item in completed["review_jobs"].values()} == {"succeeded"}
    assert all(p.get_transcript_segment("review", f"s{index}")["normalized_text"] ==
               f"第{index}项原文仍可核对。" for index in (4, 5, 8, 9))
    assert client.get("/v2/meetings/review/snapshot").json()[
        "minutes" if kind == "minutes" else "approach_artifact"
    ] == product
    assert usage_total(app) == 160  # GETs only read the two generated, saved products.


@pytest.mark.parametrize("kind", ["minutes", "approach"])
def test_completed_projection_publishes_ready_review_without_correction_degradation(review_app, kind):
    app = review_app
    p = app.state.v2_persistence
    app.state.asr_live_repository.update("review", lambda old: {
        **old, "realtime_transcript_correction": {
            "status": "completed", "processed_segment_ids": ["s1"],
            "terminal_failed_segment_ids": [], "rejected_segment_ids": [], "skipped_segment_ids": [],
        },
    })
    job = claim(app, kind)
    result = app.state.v2_post_job_handler_impls[kind](job)
    assert result["correction_degraded"] is False
    assert result["correction_degraded_reason"] is None
    assert p.complete_job(job_id=job["id"], worker_id="review-worker",
                          now_ms=app.state.test_clock[0], output=result)
    response = TestClient(app).get("/v2/meetings/review/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["review"][f"{kind}_status"] == "ready"
    assert snapshot["review_jobs"][kind]["output"]["correction_degraded"] is False
    for remaining_kind in ("minutes", "approach", "index"):
        if remaining_kind != kind:
            complete_native_review_job(app, remaining_kind)
    completed = TestClient(app).get("/v2/meetings/review/snapshot").json()
    assert completed["review"]["status"] == "ready"
    assert completed["runtime"]["ai"]["capabilities"]["review"] == {
        "state": "idle", "label": "会后整理就绪",
    }


def test_review_failure_has_priority_over_pending_job_and_degraded_saved_minutes(review_app):
    app = review_app
    p = app.state.v2_persistence
    complete_native_review_job(app, "minutes")
    failed = claim(app, "approach")
    assert p.fail_job(job_id=failed["id"], worker_id="review-worker",
                      now_ms=app.state.test_clock[0], error_class="ControlledReviewFailure")
    response = TestClient(app).get("/v2/meetings/review/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["review_jobs"]["approach"]["status"] == "failed"
    assert snapshot["review_jobs"]["index"]["status"] == "pending"
    assert snapshot["minutes"]["status"] == "degraded"
    assert snapshot["minutes"]["markdown"] == "# 新纪要"
    assert snapshot["review"]["status"] == "failed"
    assert snapshot["runtime"]["ai"]["capabilities"]["review"] == {
        "state": "error", "label": "会后整理失败",
    }


@pytest.mark.parametrize("active_status", ["running", "retry_wait"])
def test_review_processing_has_priority_over_degraded_product_while_a_job_is_active(review_app, active_status):
    app = review_app
    p = app.state.v2_persistence
    complete_native_review_job(app, "minutes")
    complete_native_review_job(app, "index")
    active = claim(app, "approach")
    if active_status == "retry_wait":
        assert p.retry_job(job_id=active["id"], worker_id="review-worker",
            now_ms=app.state.test_clock[0], next_attempt_at_ms=app.state.test_clock[0] + 60_000,
            error_class="ControlledTransientFailure")
    response = TestClient(app).get("/v2/meetings/review/snapshot")
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["review_jobs"]["approach"]["status"] == active_status
    assert snapshot["minutes"]["status"] == "degraded"
    assert snapshot["minutes"]["markdown"] == "# 新纪要"
    assert snapshot["review"]["status"] == "processing"
    assert snapshot["runtime"]["ai"]["capabilities"]["review"] == {
        "state": "busy", "label": "会后整理中",
    }


def test_disabled_noop_projection_does_not_claim_successful_correction_coverage(review_app):
    app = review_app
    app.state.asr_live_repository.update("review", lambda old: {
        **old, "realtime_transcript_correction": {
            "status": "disabled_by_setting", "processed_segment_ids": ["s1"],
            "terminal_failed_segment_ids": [],
        },
    })
    quality = app.state.wait_for_v2_correction_jobs("review", timeout_seconds=0.1)
    assert quality["correction_degraded"] is True
    assert quality["reason"] == "correction_has_no_successful_job"
    assert quality["quality_source"] == "durable_jobs"


@pytest.mark.parametrize("ineligible_field", ["rejected_segment_ids", "skipped_segment_ids"])
def test_successful_batch_does_not_cover_an_earlier_rejected_or_skipped_segment(
    review_app, ineligible_field,
):
    app = review_app
    p = app.state.v2_persistence
    p.commit_final_and_enqueue(meeting_id="review", final_id="f2", segment_id="s2", text="第二项待核对。",
        normalized_text="第二项待核对。", started_at_ms=1_000, ended_at_ms=2_000,
        evidence_hash="second", now_ms=app.state.test_clock[0], enqueue_answer=False)
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET status='cancelled' WHERE meeting_id='review' AND kind='correction'")
    app.state.asr_live_repository.update("review", lambda old: {
        **old, "realtime_transcript_correction": {
            "status": "completed", "processed_segment_ids": ["s1", "s2"],
            "terminal_failed_segment_ids": [], ineligible_field: ["s2"],
        },
    })
    quality = app.state.wait_for_v2_correction_jobs("review", timeout_seconds=0.1)
    assert quality["correction_degraded"] is True
    assert quality["reason"] == "correction_partial_coverage"


def test_manual_minutes_api_preserves_explicit_legacy_write(review_app):
    app = review_app
    client = TestClient(app)
    response = client.post("/live/asr/sessions/review/minutes", json={"mode": "enabled"})
    assert response.status_code == 200
    assert app.state.asr_live_repository.get("review")["minutes"]["minutes_md"] == "# 新纪要"
    assert usage_total(app) == 80


@pytest.mark.parametrize("kind", ["minutes", "approach"])
def test_ready_event_failure_rolls_back_legacy_products_and_ai_versions(review_app, monkeypatch, kind):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, kind)
    before = products(app)
    append = p._append_event_locked

    def reject_ready(**kwargs):
        if kwargs["event_type"] == f"meeting.{kind}.ready":
            raise RuntimeError("controlled publication failure")
        return append(**kwargs)

    monkeypatch.setattr(p, "_append_event_locked", reject_ready)
    with pytest.raises(RuntimeError, match="controlled publication failure"):
        app.state.v2_post_job_handler_impls[kind](job)
    assert products(app) == before
    assert usage_total(app) == 80


@pytest.mark.parametrize("field,value", [("id", "other-job"), ("meeting_id", "other-meeting"), ("kind", "index")])
def test_claim_identity_is_checked_in_addition_to_running_owner(review_app, field, value):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, "minutes")
    source = p.capture_review_source("review")
    actual_id = job["id"]
    before = products(app)
    job[field] = value
    with pytest.raises(JobLeaseLostError):
        p.save_minutes(meeting_id="review", job_id=actual_id, markdown="不应保存", structured=None,
                       degraded=False, now_ms=app.state.test_clock[0], expected_claim=job,
                       expected_transcript_hash=source["hash"])
    assert products(app) == before


def test_public_job_quality_never_exposes_body_or_guesses_legacy_flags(review_app):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, "minutes")
    assert "output" not in p.get_snapshot("review")["review_jobs"]["minutes"]
    p.complete_job(job_id=job["id"], worker_id="review-worker", now_ms=app.state.test_clock[0], output={
        "degraded": False, "correction_degraded": 1, "correction_degraded_reason": ["not-safe"],
        "minutes_md": "private model body", "llm_provider": {"base_url": "private route"},
    })
    public = p.get_snapshot("review")["review_jobs"]["minutes"]
    assert public["id"] == job["id"]
    assert public["output"] == {"degraded": False}
    assert "private" not in json.dumps(public)


def test_old_executor_attempt_cannot_fail_new_claim_by_same_worker(review_app, monkeypatch):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, "minutes")
    before = products(app)
    entered, release = Event(), Event()

    def blocked(*_args):
        entered.set()
        assert release.wait(3)
        return "# 不应发布", STRUCTURED, USAGE, False

    monkeypatch.setattr(app_module.llm_service, "build_minutes_artifact", blocked)

    async def run():
        executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {},
            additional_handlers={"minutes": app.state.v2_post_job_handler_impls["minutes"]},
            now_ms=lambda: app.state.test_clock[0])
        task = asyncio.create_task(executor._execute_claimed_job("minutes", "review-worker", job))
        assert await asyncio.to_thread(entered.wait, 3)
        with p._write_transaction():
            p._conn.execute("UPDATE jobs SET attempts=attempts+1 WHERE id=?", (job["id"],))
        release.set()
        await asyncio.wait_for(task, 3)
        current = p.get_job(job["id"])
        assert (current["status"], current["attempts"]) == ("running", 2)

    asyncio.run(run())
    assert products(app) == before
    assert usage_total(app) == 80


@pytest.mark.parametrize("kind", ["minutes", "approach"])
def test_deadline_crossed_during_publication_rolls_back_entire_transaction(review_app, monkeypatch, kind):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, kind)
    before = products(app)
    append = p._append_event_locked

    def cross_deadline(**kwargs):
        result = append(**kwargs)
        if kwargs["event_type"] == f"meeting.{kind}.ready":
            app.state.test_clock[0] = job["deadline_at_ms"] + 1
        return result

    monkeypatch.setattr(p, "_append_event_locked", cross_deadline)
    with pytest.raises(IntelligenceDeadlineExceeded):
        app.state.v2_post_job_handler_impls[kind](job)
    assert products(app) == before
    assert usage_total(app) == 80


@pytest.mark.parametrize("operation", ["complete_job", "cancel_job", "fail_job", "retry_job", "defer_job"])
def test_review_claim_attempt_compare_and_swap_is_atomic(review_app, operation):
    app = review_app
    p = app.state.v2_persistence
    job = claim(app, "minutes")
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET attempts=2 WHERE id=?", (job["id"],))
    before = p.get_job(job["id"])
    kwargs = {"output": {}} if operation == "complete_job" else {"error_class": "controlled_error"}
    if operation in {"retry_job", "defer_job"}:
        kwargs["next_attempt_at_ms"] = app.state.test_clock[0] + 1_000
    result = getattr(p, operation)(job_id=job["id"], worker_id="review-worker",
        now_ms=app.state.test_clock[0], expected_attempt=1, **kwargs)
    assert result is None
    assert p.get_job(job["id"]) == before

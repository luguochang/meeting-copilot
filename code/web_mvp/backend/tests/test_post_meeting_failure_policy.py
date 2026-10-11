from __future__ import annotations

import asyncio
import json
import time

from fastapi import HTTPException
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.post_meeting_failures import permanent_post_meeting_failure
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_transient_http_error_preserves_existing_retry_semantics(status):
    assert permanent_post_meeting_failure(HTTPException(status_code=status, detail="untrusted upstream body")) is None


@pytest.mark.parametrize("status,code", [(401, "post_meeting_provider_auth_invalid"), (403, "post_meeting_provider_auth_invalid"),
    (400, "post_meeting_request_invalid"), (404, "post_meeting_request_invalid"), (422, "post_meeting_request_invalid")])
def test_permanent_upstream_failure_never_retains_arbitrary_body(status, code):
    failure = permanent_post_meeting_failure(HTTPException(status_code=status, detail="secret credential and upstream body"))
    assert failure.retryable is False and failure.durable_error_class == code
    assert str(failure) == code and failure.status_code == status
    assert not hasattr(failure, "detail")


def setup_app(tmp_path, monkeypatch, *, source_exists):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    p.create_meeting(meeting_id="post-review", title=None, now_ms=now)
    committed = p.commit_final_and_enqueue(meeting_id="post-review", final_id="f1", segment_id="s1",
        text="通知失败不应影响已经发放的权益。", normalized_text="通知失败不应影响已经发放的权益。",
        started_at_ms=0, ended_at_ms=1_000, evidence_hash="h1", now_ms=now)
    with p._write_transaction():
        for job_id in committed["job_ids"].values():
            if job_id:
                p._conn.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (job_id,))
    if source_exists:
        app.state.asr_live_repository.create({"session_id": "post-review", "source": "synthetic_text_fixture",
            "provider": "synthetic_text_input", "is_mock": True, "input_source": "synthetic_text_fixture",
            "events": [{"event_type": "final", "segment_id": "s1", "text": "通知失败不应影响已经发放的权益。"}]})
    def forbidden(*_args, **_kwargs):
        raise AssertionError("source eligibility failure called provider")
    monkeypatch.setattr(app_module.llm_service, "build_minutes_artifact", forbidden)
    monkeypatch.setattr(app_module.llm_service, "build_approach_cards", forbidden)
    p.end_meeting(meeting_id="post-review", now_ms=now + 1)
    return app


async def run_post_jobs(app):
    p = app.state.v2_persistence
    executor = DurableJobExecutor(p, correction_handler=lambda _j: {}, suggestion_handler=lambda _j: {},
        additional_handlers={"minutes": app.state.v2_post_job_handler_impls["minutes"],
                             "approach": app.state.v2_post_job_handler_impls["approach"], "index": lambda _j: {}},
        poll_interval_ms=5, retry_initial_ms=5, retry_max_ms=10)
    try:
        await executor.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            jobs = [j for j in p.list_jobs(meeting_id="post-review") if j["kind"] in {"minutes", "approach"}]
            if all(j["status"] == "failed" for j in jobs) and len(jobs) == 2:
                return jobs
            await asyncio.sleep(0.01)
        raise AssertionError("post-meeting jobs did not terminate")
    finally:
        await executor.stop()


@pytest.mark.parametrize("source_exists,code", [(False, "post_meeting_source_missing"), (True, "post_meeting_source_ineligible")])
def test_real_post_handlers_reject_unusable_source_once_and_project_safe_reason(tmp_path, monkeypatch, source_exists, code):
    app = setup_app(tmp_path, monkeypatch, source_exists=source_exists)
    p = app.state.v2_persistence
    try:
        jobs = asyncio.run(run_post_jobs(app))
        assert [(j["attempts"], j["error_class"]) for j in jobs] == [(1, code), (1, code)]
        snapshot = p.get_snapshot("post-review")
        for kind in ("minutes", "approach"):
            assert snapshot["review_jobs"][kind]["error_class"] == code
            assert snapshot["review_jobs"][kind]["error_message"]
        assert p._conn.execute("SELECT COUNT(*) FROM llm_usage_ledger WHERE session_id='post-review'").fetchone()[0] == 0
        assert "untrusted" not in json.dumps(snapshot, ensure_ascii=False)
    finally:
        p.close()


@pytest.mark.parametrize("status,attempts,code", [(429, 3, "HTTPException"), (503, 3, "HTTPException"), (401, 1, "post_meeting_provider_auth_invalid")])
def test_real_post_handler_retains_transient_retries_but_not_auth_body(tmp_path, monkeypatch, status, attempts, code):
    app = setup_app(tmp_path, monkeypatch, source_exists=True)
    def reject(*_args, **_kwargs):
        raise HTTPException(status_code=status, detail="secret credential and upstream body")
    monkeypatch.setattr(app_module, "_ensure_enabled_llm_allowed", reject)
    p = app.state.v2_persistence
    try:
        jobs = asyncio.run(run_post_jobs(app))
        assert [(j["attempts"], j["error_class"]) for j in jobs] == [(attempts, code), (attempts, code)]
        public = p.get_snapshot("post-review")["review_jobs"]
        assert "secret" not in json.dumps(public) and "credential" not in json.dumps(public)
    finally:
        p.close()

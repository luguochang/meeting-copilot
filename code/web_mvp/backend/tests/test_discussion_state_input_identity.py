from __future__ import annotations

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.discussion_state_identity import state_input_fingerprint
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from test_discussion_state_background import append, prepare
import test_discussion_state_background as state_tests
from test_realtime_participation_persistence import _coverage_attachment, _project_coverage_card


@pytest.fixture
def state_app(tmp_path, monkeypatch):
    yield from state_tests.state_app.__wrapped__(tmp_path, monkeypatch)


def _metadata():
    return {"profile": "participation", "task_mode": "discussion_state", "topic_id": "discussion:synthetic",
        "analysis_basis_revision": 1, "semantic_from_seq": 0, "anchor_seq": 1,
        "input_context": {"material_set_id": "synthetic-materials", "material_context_revision": 1},
        "evidence": [{"segment_id": "s1", "revision": 1, "evidence_hash": "synthetic-hash"}]}


def test_state_identity_uses_authorized_input_and_revisions_not_clock_or_navigation():
    source = _metadata()
    key = state_input_fingerprint(source)
    noisy = {**deepcopy(source), "job_id": "another-job", "created_at_ms": 5000, "packet_hash": "another-full-packet",
        "material_context": {"attachment_catalog": [{"status": "converted"}]},
        "discussion_context": {"proposals": [{"text": "generated advice"}]}}
    assert state_input_fingerprint(noisy) == key
    for change in ["basis", "revision", "hash", "anchor", "scope", "cursor"]:
        revised = deepcopy(source)
        if change == "basis":
            revised["analysis_basis_revision"] += 1
        elif change == "revision":
            revised["evidence"][0]["revision"] += 1
        elif change == "hash":
            revised["evidence"][0]["evidence_hash"] = "new-hash"
        elif change == "anchor":
            revised["anchor_seq"] += 1
        elif change == "scope":
            revised["input_context"]["material_context_revision"] += 1
        else:
            revised["semantic_from_seq"] += 1
        assert state_input_fingerprint(revised) != key


def test_young_meeting_still_waits_for_stable_sufficient_input_and_manual_priority(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    with p._write_transaction():
        p._conn.execute("UPDATE meetings SET created_at_ms=? WHERE id='review'", (now - 10_000,))
    append(p, 1, "这是尚未形成明确议题的短句。", now=now - 4_000)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now) is None
    append(p, 2, "我们要判断支付后的权益与短信通知是否要在同一个流程，重点比较通知失败、重复打开页面和权益已生效之后的处理边界。" * 3, now=now - 2_000)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now) is None
    manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
        now_ms=now, idempotency_key="young-manual")
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 1_000) is None
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET status='succeeded' WHERE id=?", (manual["id"],))
    state = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 1_001)
    assert state is not None and state["created_at_ms"] < now + 50_000
    assert state["request_context"]["state_input_fingerprint"] == state_input_fingerprint(state["request_context"])


def test_network_failed_real_handler_does_not_restart_the_same_packet_after_cooldown(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    calls = []

    class TimedOutProvider:
        async def complete(self, *_args, **_kwargs):
            calls.append(1)
            raise httpx.ReadTimeout("private upstream response must not be persisted")

    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: TimedOutProvider())
    with pytest.raises(httpx.ReadTimeout) as caught:
        asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    executor = DurableJobExecutor(p, correction_handler=lambda _job: None, suggestion_handler=lambda _job: None,
        now_ms=lambda: now + 20)
    asyncio.run(executor._retry_after_handler_failure(job, "state", caught.value, resume_generation=0))
    final = p.get_job(job["id"])
    assert final["status"] == "failed" and final["attempts"] == 1
    assert final["error_class"] == "ReadTimeout"
    failure = final["output"]
    assert failure["schema_version"] == "v2_discussion_state_failure_output.v1"
    assert failure["task_mode"] == "discussion_state" and failure["provider_attempt_count"] == 1
    assert failure["failure_diagnostics"]["failure_stage"] == "provider_call"
    assert failure["failure_diagnostics"]["failure_source"] == "discussion_state_handler"
    assert failure["usage"] is None
    assert "private upstream response" not in json.dumps(failure)
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 60_000) is None
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    assert calls == [1]
    assert p.get_discussion_state_capability("review")["error_class"] == "provider_timeout"


@pytest.mark.parametrize("change", ["revision", "basis", "speech"])
def test_terminal_failed_packet_compatibility_allows_only_new_input_generations(state_app, change):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    assert p.fail_job(job_id=job["id"], worker_id="state", now_ms=now + 20, error_class="intelligence_validation_evidence")
    # An old persisted job without the new field still participates in dedupe.
    legacy = dict(job["request_context"])
    legacy.pop("state_input_fingerprint")
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET request_context_json=? WHERE id=?", (json.dumps(legacy), job["id"]))
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    if change == "revision":
        p.commit_transcript_revision(meeting_id="review", segment_id="s1", expected_evidence_hash="h1",
            corrected_text="通知不可以稍后发送，本轮先确认支付后的权益发放。", revision_id="new-revision", now_ms=now + 121_000)
    elif change == "basis":
        topic = p.get_current_discussion("review")
        p.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
            context_patch={"current_problem": "人工澄清本轮关注的失败边界"}, now_ms=now + 121_000)
    else:
        append(p, 4, "新的原话明确提出短信通知应单独重试，而付款页面要单独展示权益状态。", now=now + 121_000)
    successor = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 124_000)
    assert successor is not None
    assert successor["request_context"]["state_input_fingerprint"] != job["request_context"]["state_input_fingerprint"]
    assert successor["attempts"] == 0
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 124_001) is None


def test_deadline_terminal_is_bounded_but_user_preemption_can_resume_after_cooldown(state_app):
    app, now = state_app
    p = app.state.v2_persistence
    job = prepare(app, now)
    assert p.cancel_job(job_id=job["id"], worker_id="state", now_ms=now + 20, error_class="deadline_exceeded")
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    with p._write_transaction():
        p._conn.execute("UPDATE jobs SET error_class='preempted_by_user_request' WHERE id=?", (job["id"],))
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 59_999) is None
    successor = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 60_000)
    assert successor is not None
    assert successor["request_context"]["state_input_fingerprint"] == job["request_context"]["state_input_fingerprint"]


def test_manual_proposal_and_conversion_progress_do_not_unblock_failed_packet_but_authorization_does(state_app):
    from meeting_copilot_web_mvp.document_import import extract_document

    app, now = state_app
    p = app.state.v2_persistence
    repository, material_set, attachment, _evidence = _coverage_attachment(app)
    state = prepare(app, now)
    original_identity = state["request_context"]["state_input_fingerprint"]
    original_binding = state["request_context"]["input_context"]
    assert p.fail_job(job_id=state["id"], worker_id="state", now_ms=now + 20,
                      error_class="intelligence_validation_evidence")

    manual = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="compare_options",
        now_ms=now + 100, idempotency_key="same-input-manual")
    _project_coverage_card(p, manual, now + 101)
    assert p.get_current_discussion("review")["context"]["proposals"]
    assert p.get_current_discussion("review")["context"]["analysis_basis_revision"] == state["request_context"]["analysis_basis_revision"]
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 60_000) is None

    content = "这份尚未授权的新资料只用于验证目录转换进度。".encode("utf-8")
    pending = app.state.meeting_material_importer.stage(material_set["id"], "pending.txt", content, content_type="text/plain")
    assert not pending["enabled"]
    assert p.get_material_context("review")["attachment_catalog"]
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 90_000) is None
    conversion = app.state.meeting_material_jobs.claim()
    assert app.state.meeting_material_jobs.finish(conversion, extract_document("pending.txt", content, "text/plain"))
    assert p.enqueue_discussion_state(meeting_id="review", now_ms=now + 120_000) is None
    with p._write_transaction():
        frozen = p._freeze_participation_locked(meeting_id="review", now_ms=now + 120_001,
            intent="freeform", user_request="", topic_id=None, expected_basis_revision=None,
            selected_segment_ids=[], source_evidence=[])
    frozen.update(task_mode="discussion_state", semantic_from_seq=0,
        attention_contract=state["request_context"]["attention_contract"],
        attention_focus_segment_ids=state["request_context"]["attention_focus_segment_ids"])
    assert frozen["input_context"] == original_binding
    assert state_input_fingerprint(frozen) == original_identity

    # Only a real authorization revision, not catalog conversion, changes input.
    repository.usage(material_set["id"], attachment["id"], enabled=False,
        version_id=attachment["latest_version_id"], selected_span_ids=None, expected_revision=attachment["revision"])
    successor = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 124_000)
    assert successor is not None
    assert successor["request_context"]["input_context"] != original_binding
    assert successor["request_context"]["state_input_fingerprint"] != original_identity
    assert successor["attempts"] == 0

"""State-first compatibility and kill switches; no upstream or audio devices.

Only the named State-first fixture opts out of direct participation. The new
default is exercised independently in test_direct_realtime_participation.py.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from meeting_copilot_web_mvp import app as app_module
import test_discussion_state_background as state_tests
import test_live_analysis_profile as profile_tests


@pytest.fixture
def state_app(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_DIRECT_ENABLED", "0")
    yield from state_tests.state_app.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def profile_app(tmp_path, monkeypatch):
    yield from profile_tests.profile_app.__wrapped__(tmp_path, monkeypatch)


def _understood(app, now, monkeypatch):
    p = app.state.v2_persistence
    job = state_tests.prepare(app, now)
    provider = state_tests.Provider()
    monkeypatch.setattr(app_module, "NonStreamingStateProvider", lambda **_kwargs: provider)
    output = asyncio.run(app.state.v2_discussion_state_job_handler_impl(job))
    assert p.complete_job(job_id=job["id"], worker_id="state", now_ms=now + 1, output=output)
    assert len(provider.calls) == 1
    # Arrange settled, unchanged L2 so these fixtures isolate State-first
    # lease/switch boundaries rather than concurrent correction scheduling.
    for _ in p.list_jobs(meeting_id="review", lane="correction"):
        correction = p.claim_next_job(worker_id="fixture-l2", lane="correction",
            now_ms=now + 2, lease_ms=5_000)
        if correction is None or correction["meeting_id"] != "review":
            raise AssertionError("expected this fixture's correction job")
        segment_id = correction["evidence_segment_id"]
        p.mark_correction_segments_no_change(meeting_id="review", segment_ids=[segment_id],
            max_input_transcript_seq=correction["input_transcript_seq"], now_ms=now + 3,
            expected_evidence_hashes={segment_id: correction["evidence_hash"]})
        if p.complete_job(job_id=correction["id"], worker_id="fixture-l2", now_ms=now + 3,
                output={"fixture": "unchanged"}) is None:
            raise AssertionError("fixture correction did not complete")
    if p.has_pending_discussion_corrections("review"):
        raise AssertionError("fixture correction work remains active")
    return job


def _automatic_jobs(p):
    return [job for job in p.list_jobs(meeting_id="review")
        if job["trigger_type"] == "discussion_update" and not job["request_context"].get("task_mode")]


def test_legacy_state_first_automatic_enable_default_enqueues_after_local_state_success(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.delenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", raising=False)
    _understood(app, now, monkeypatch)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    jobs = _automatic_jobs(app.state.v2_persistence)
    assert len(jobs) == 1 and jobs[0]["status"] == "pending" and jobs[0]["attempts"] == 0
    assert jobs[0]["request_context"]["profile"] == "participation"
    assert jobs[0]["request_context"]["automatic_attention"] is True
    assert jobs[0]["request_context"].get("automatic_direct") is not True


def test_explicit_zero_stops_new_contributions_but_keeps_understanding_independent(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "1")
    _understood(app, now, monkeypatch)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert _automatic_jobs(p) == []
    assert p.get_current_discussion("review")["context"]["last_semantic_seq"] == 3
    assert len([j for j in p.list_jobs(meeting_id="review") if j["request_context"].get("task_mode") == "discussion_state"]) == 1


@pytest.mark.parametrize("profile", [None, "legacy"])
def test_legacy_or_no_preparation_does_not_gain_state_or_automatic_with_enable_flag(profile_app, monkeypatch, profile):
    app, client = profile_app
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-key")
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "1")
    if profile is not None:
        assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": profile}).status_code == 200
    for seq in range(1, 4):
        profile_tests._direct_final(app.state.v2_persistence, seq)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert not any(job["trigger_type"] == "discussion_update" for job in app.state.v2_persistence.list_jobs(meeting_id="review"))
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile"] == "legacy"


def test_meeting_policy_off_and_global_zero_do_not_block_manual_handler_and_typed_publication(profile_app, monkeypatch):
    app, client = profile_app
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation",
        "proactive_suggestion_policy": "off"}).status_code == 200
    original_put = client.put

    def fixture_preparation_with_off(path, **kwargs):
        # The reusable transport regression saves its own preparation. Supply
        # the intended full policy there too, rather than testing under its
        # low_frequency default after accidentally replacing this setup.
        if path == "/v2/meetings/review/preparation" and kwargs.get("json") == {"live_analysis_profile": "participation"}:
            kwargs["json"] = {"live_analysis_profile": "participation", "proactive_suggestion_policy": "off"}
        return original_put(path, **kwargs)

    monkeypatch.setattr(client, "put", fixture_preparation_with_off)
    # Reuse the existing complete request->fake Sidecar->typed apply->HTTP
    # regression, not merely a 202 enqueue assertion or an external model call.
    profile_tests.test_trusted_participation_handler_reaches_sidecar_and_persists_its_own_typed_card(
        profile_app, monkeypatch, "user_request")
    assert client.get("/v2/meetings/review/preparation").json()["proactive_suggestion_policy"] == "off"
    assert _automatic_jobs(app.state.v2_persistence) == []


def test_meeting_policy_off_stops_automatic_after_understanding_with_global_enable(state_app, monkeypatch):
    app, now = state_app
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    _understood(app, now, monkeypatch)
    app.state.meeting_preparation_store.save("review", proactive_suggestion_policy="off", updated_at_ms=now + 2)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert _automatic_jobs(app.state.v2_persistence) == []


@pytest.mark.parametrize("claimed", [False, True])
def test_zero_switch_does_not_cancel_or_steal_existing_queued_or_running_work(state_app, monkeypatch, claimed):
    app, now = state_app
    p = app.state.v2_persistence
    _understood(app, now, monkeypatch)
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 1
    before = _automatic_jobs(p)[0]
    if claimed:
        before = p.claim_next_job(worker_id="existing-worker", lane="pi_deep",
            now_ms=time.time_ns() // 1_000_000, lease_ms=30_000)
        assert before["status"] == "running"
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "0")
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    after = p.get_job(before["id"])
    assert after["status"] == before["status"]
    assert after["attempts"] == before["attempts"]
    assert after["lease_owner"] == before["lease_owner"]
    assert after["lease_until_ms"] == before["lease_until_ms"]
    assert len(_automatic_jobs(p)) == 1


def test_legacy_state_first_state_zero_prevents_admission_even_with_automatic_enabled(state_app, monkeypatch):
    app, now = state_app
    p = app.state.v2_persistence
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_STATE_ENABLED", "0")
    for seq in range(1, 4):
        state_tests.append(p, seq, now=now - 5_000)
    p._conn.execute("UPDATE jobs SET status='succeeded' WHERE meeting_id='review'")
    app.state.provider_lane_registry.sync_realtime_work(set())
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert not any(job["trigger_type"] == "discussion_update" for job in p.list_jobs(meeting_id="review"))

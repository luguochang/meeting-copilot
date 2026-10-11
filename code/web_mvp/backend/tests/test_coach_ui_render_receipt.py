from __future__ import annotations

import json
import time

import pytest

from meeting_copilot_web_mvp.coach_ui_render_receipt import current_publication_receipt, normalize_receipt, publication_marker
import test_live_analysis_profile as profile_tests
from test_realtime_participation_persistence import _coverage_attachment


@pytest.fixture
def published_app(tmp_path, monkeypatch):
    fixture = profile_tests.profile_app.__wrapped__(tmp_path, monkeypatch)
    app, client = next(fixture)
    try:
        profile_tests.test_trusted_participation_handler_reaches_sidecar_and_persists_its_own_typed_card(
            (app, client), monkeypatch, "user_request")
        snapshot = client.get("/v2/meetings/review/snapshot").json()
        card = snapshot["coach_intervention"]
        assert card.get("ui_render_receipt") is not None
        yield app, client, card["ui_render_receipt"]
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


def test_real_local_handler_publication_snapshot_event_and_duplicate_ack_keep_first_time(published_app):
    app, client, receipt = published_app
    assert normalize_receipt(receipt) == receipt
    assert receipt["input_version"] >= 1 and type(receipt["input_version"]) is int
    events = client.get("/v2/meetings/review/events", params={"once": True}).json()["events"]
    event = next(item for item in events if item["seq"] == receipt["event_seq"])
    assert event["payload"]["ui_render_measurement_version"] == "formal_coach_ui.v1"
    assert event["payload"]["ui_render_input_version"] == receipt["input_version"]
    assert event["payload"]["coach_intervention"]["ui_render_receipt"] == receipt
    assert event["payload"]["follow_up"]["ui_render_receipt"] == receipt
    assert all("ui_render_receipt" not in card for card in client.get("/v2/meetings/review/snapshot").json()["coach_history"])
    trace = app.state.pipeline_traces.get(receipt["job_id"])
    assert "ui_rendered" not in trace.stage_marks
    response = client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt)
    assert response.status_code == 200, response.json()
    first = trace.stage_marks["ui_rendered"].monotonic_ns
    assert client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt).status_code == 200
    assert trace.stage_marks["ui_rendered"].monotonic_ns == first


@pytest.mark.parametrize("field,value", [("meeting_id", "different-meeting"), ("job_id", "different-job"),
    ("decision_id", "different-decision"), ("event_seq", 999999), ("input_version", 99999), ("draft_seq", 1),
    ("input_version", True), ("event_seq", 1.0)])
def test_bad_receipts_are_rejected_without_hiding_readable_card_or_marking_trace(published_app, field, value):
    app, client, receipt = published_app
    rejected = client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json={**receipt, field: value})
    assert rejected.status_code == 422 and rejected.json()["detail"] == {"error": "ui_render_receipt_rejected"}
    assert "ui_rendered" not in app.state.pipeline_traces.get(receipt["job_id"]).stage_marks
    assert client.get("/v2/meetings/review/snapshot").json()["coach_intervention"]["decision_id"] == receipt["decision_id"]


@pytest.mark.parametrize("invalidation", ["old_marker", "expired", "binding_expired", "ended", "revised", "basis", "retracted", "new_replacement"])
def test_old_and_invalidated_publications_never_receive_ack_or_a_fresh_receipt(published_app, invalidation):
    app, client, receipt = published_app
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    if invalidation in {"old_marker", "expired"}:
        with p._write_transaction():
            row = p._conn.execute("SELECT payload_json FROM meeting_events WHERE meeting_id='review' AND seq=?", (receipt["event_seq"],)).fetchone()
            payload = json.loads(row["payload_json"])
            if invalidation == "old_marker":
                payload.pop("ui_render_measurement_version")
                payload["coach_intervention"]["ui_render_receipt"] = receipt  # Untrusted old hint cannot promote.
            else:
                payload["coach_intervention"]["valid_until_ms"] = now - 1
            p._conn.execute("UPDATE meeting_events SET payload_json=? WHERE meeting_id='review' AND seq=?", (json.dumps(payload), receipt["event_seq"]))
    elif invalidation == "binding_expired":
        row = p._conn.execute("SELECT payload_json FROM coach_source_bindings WHERE job_id=?", (receipt["job_id"],)).fetchone()
        bound = json.loads(row["payload_json"])
        bound["valid_until_ms"] = now - 1
        p._conn.execute("UPDATE coach_source_bindings SET payload_json=? WHERE job_id=?", (json.dumps(bound), receipt["job_id"]))
    elif invalidation == "ended":
        p._conn.execute("UPDATE meetings SET state='ended' WHERE id='review'")
    elif invalidation == "revised":
        p.commit_transcript_revision(meeting_id="review", segment_id="s1",
            expected_evidence_hash=p.get_job(receipt["job_id"])["evidence_hash"], corrected_text="通知不可以稍后发送。",
            revision_id="ui-new-source", now_ms=now)
    elif invalidation == "basis":
        topic = p.get_current_discussion("review")
        p.update_discussion_context(meeting_id="review", work_item_id=topic["work_item_id"], expected_version=topic["version"],
            context_patch={"current_problem": "现在讨论退款"}, now_ms=now)
    else:
        # Exact later replacement/retraction is a barrier without changing topic/basis.
        decision = {"decision_id": "later-decision", "status": "stale" if invalidation == "retracted" else "intervention",
            "lifecycle_action": "retract" if invalidation == "retracted" else "retain", "supersedes_decision_id": receipt["decision_id"]}
        with p._write_transaction():
            p._append_event_locked(meeting_id="review", event_type="meeting.intelligence.applied", aggregate_type="intelligence",
                aggregate_id="later", occurred_at_ms=now, idempotency_key="ui-later", payload={"coach_decision": decision,
                "coach_intervention": None if invalidation == "retracted" else {"recommendation": "新的建议", "reason": "新依据"}},
                correlation_id="review", causation_id="synthetic-ui-test")
    assert current_publication_receipt(p, meeting_id="review", job_id=receipt["job_id"], decision_id=receipt["decision_id"], now_ms=now) is None
    assert client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt).status_code == 422
    assert "ui_rendered" not in app.state.pipeline_traces.get(receipt["job_id"]).stage_marks
    events = client.get("/v2/meetings/review/events", params={"once": True}).json()["events"]
    old = next(item for item in events if item["seq"] == receipt["event_seq"])
    assert "ui_render_receipt" not in old["payload"]["coach_intervention"]


def test_marker_is_host_only_not_legacy_silence_or_model_receipt(published_app):
    app, client, receipt = published_app
    metadata = app.state.v2_persistence.get_job(receipt["job_id"])["request_context"]
    snapshot = client.get("/v2/meetings/review/snapshot").json()
    decision = snapshot["coach_decision"]
    card = snapshot["coach_intervention"]
    assert publication_marker({**metadata, "profile": "legacy"}, decision, card, input_version=1) == {}
    assert publication_marker(metadata, {**decision, "status": "protected_silent"}, {"ui_render_receipt": receipt}, input_version=1) == {}
    assert client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json={"event_seq": receipt["event_seq"], "draft_seq": 0}).status_code == 422


def test_source_binding_authorization_invalidation_prevents_measurement(published_app):
    app, client, receipt = published_app
    _coverage_attachment(app)  # Binding scope changes; old current sources no longer authorize measurement.
    assert client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt).status_code == 422


def test_missing_trace_is_not_created_by_receipt(published_app, monkeypatch):
    app, client, receipt = published_app
    def missing(_trace_id):
        raise KeyError("not-observed")
    monkeypatch.setattr(app.state.pipeline_traces, "get", missing)
    assert client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt).status_code == 422


def test_existing_answer_trace_endpoint_keeps_legacy_minimal_body(profile_app):
    app, client = profile_app
    trace = app.state.pipeline_traces.create(trace_id="answer-test", meeting_id="review", job_id="answer-test")
    assert client.post("/v2/traces/answer-test/ui-rendered", json={"event_seq": 2, "draft_seq": 3}).status_code == 200
    assert trace.stage_marks["ui_rendered"].attributes == {"event_seq": 2, "draft_seq": 3}


@pytest.mark.parametrize("condition", ["published_running", "no_lease", "expired_lease", "deadline", "changed_input_version"])
def test_published_running_window_requires_unrevoked_lease_deadline_and_frozen_version(published_app, condition):
    app, client, receipt = published_app
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    p._conn.execute("UPDATE jobs SET status='running',lease_owner='publisher',lease_until_ms=?,deadline_at_ms=? WHERE id=?",
                    (now + 30_000, now + 30_000, receipt["job_id"]))
    if condition == "no_lease":
        p._conn.execute("UPDATE jobs SET lease_owner=NULL WHERE id=?", (receipt["job_id"],))
    elif condition == "expired_lease":
        p._conn.execute("UPDATE jobs SET lease_until_ms=? WHERE id=?", (now - 1, receipt["job_id"]))
    elif condition == "deadline":
        p._conn.execute("UPDATE jobs SET deadline_at_ms=? WHERE id=?", (now - 1, receipt["job_id"]))
    elif condition == "changed_input_version":
        p._conn.execute("UPDATE jobs SET input_version=input_version+1 WHERE id=?", (receipt["job_id"],))
    response = client.post(f"/v2/traces/{receipt['job_id']}/ui-rendered", json=receipt)
    assert response.status_code == (200 if condition == "published_running" else 422)
    assert ("ui_rendered" in app.state.pipeline_traces.get(receipt["job_id"]).stage_marks) == (condition == "published_running")


@pytest.fixture
def profile_app(tmp_path, monkeypatch):
    yield from profile_tests.profile_app.__wrapped__(tmp_path, monkeypatch)

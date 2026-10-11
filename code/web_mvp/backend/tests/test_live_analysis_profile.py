from __future__ import annotations

import asyncio
import time

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.v2_persistence import transcript_evidence_hash
from discussion_attention_fixtures import state_response


@pytest.fixture
def profile_app(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    app.state.v2_persistence.create_meeting(meeting_id="review", title=None, now_ms=time.time_ns() // 1_000_000 - 100_000)
    client = TestClient(app)
    yield app, client
    client.close()
    app.state.v2_persistence.close()


def _direct_final(persistence, seq, *, enqueue_intelligence=True, enqueue_answer=False, text=None):
    text = text or f"用户付款后应立即看到权益，通知可以稍后发送，本轮先讨论方案边界和重试风险。第{seq}段。"
    return persistence.commit_final_and_enqueue(meeting_id="review", final_id=f"final{seq}", segment_id=f"s{seq}",
        text=text, normalized_text=text, started_at_ms=seq * 1000, ended_at_ms=seq * 1000 + 800,
        evidence_hash=transcript_evidence_hash(f"s{seq}", text), now_ms=time.time_ns() // 1_000_000 - 5_000,
        source_track="system_audio", enqueue_intelligence=enqueue_intelligence, enqueue_answer=enqueue_answer)


def test_prepare_profile_is_optional_persistent_and_changeable_before_real_input(profile_app):
    app, client = profile_app
    assert app.state.v2_persistence.get_meeting("review")["state"] == "live"
    assert client.get("/v2/meetings/review/preparation").json()["live_analysis_profile"] == "legacy"
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile"] == "legacy"
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"}).status_code == 200
    response = client.put("/v2/meetings/review/preparation", json={"hotwords": ["订单"]})
    assert response.json()["live_analysis_profile"] == "participation"
    assert response.json()["version"] == 2
    snapshot = client.get("/v2/meetings/review/snapshot").json()
    assert snapshot["live_analysis_profile"] == "participation" and snapshot["live_analysis_profile_locked"] is False
    for bad in (None, "", "automatic", 1):
        assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": bad}).status_code == 422
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "legacy"}).status_code == 200


def test_canonical_input_locks_changed_profile_but_not_same_or_omitted_edits(profile_app):
    app, client = profile_app
    client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"})
    _direct_final(app.state.v2_persistence, 1, enqueue_intelligence=False)
    version = client.get("/v2/meetings/review/preparation").json()["version"]
    rejected = client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "legacy"})
    assert rejected.status_code == 409 and rejected.json()["detail"]["error"] == "live_analysis_profile_locked"
    assert client.get("/v2/meetings/review/preparation").json()["version"] == version
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"}).status_code == 200
    assert client.put("/v2/meetings/review/preparation", json={"meeting_goal": "确认权益方案"}).json()["live_analysis_profile"] == "participation"
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile_locked"] is True


def test_first_binary_audio_locks_profile_before_chunk_or_final(profile_app, monkeypatch):
    app, client = profile_app
    client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation", "notice_acknowledged": True})

    async def fake_stream(websocket, _session_id, **_kwargs):
        await websocket.accept()
        message = await websocket.receive()
        assert message["bytes"] == b"synthetic-audio-boundary"
        await websocket.send_text("input-received")
        await websocket.receive()

    monkeypatch.setattr(app_module.asr_stream, "handle_stream", fake_stream)
    with client.websocket_connect("/live/asr/stream/ws/review") as websocket:
        websocket.send_bytes(b"synthetic-audio-boundary")
        assert websocket.receive_text() == "input-received"
        p = app.state.v2_persistence
        assert p._conn.execute("SELECT COUNT(*) FROM audio_chunks").fetchone()[0] == 0
        assert p._conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 0
        assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "legacy"}).status_code == 409
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile_locked"] is True


def test_persisted_audio_without_speech_also_locks_profile(profile_app):
    app, client = profile_app
    client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"})
    p = app.state.v2_persistence
    p._conn.execute("INSERT INTO audio_chunks(meeting_id,track,epoch,chunk_seq,relative_path,sha256,sample_rate_hz,sample_count,duration_ms,file_size_bytes,status,captured_at_ms,created_at_ms) VALUES('review','microphone',0,1,'synthetic.wav','hash',16000,1600,100,3200,'committed',1,1)")
    assert p._conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 0
    assert client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "legacy"}).status_code == 409
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile_locked"] is True


def test_producer_switch_preserves_correction_and_semantic_revision_without_legacy_jobs(profile_app):
    app, _client = profile_app
    p = app.state.v2_persistence
    first = _direct_final(p, 1, enqueue_intelligence=False)
    second = _direct_final(p, 2, enqueue_intelligence=False)
    assert set(first["job_ids"]) == set(second["job_ids"]) == {"correction"}
    assert all(job["kind"] == "correction" for job in p.list_jobs(meeting_id="review"))
    assert len(p.list_jobs(meeting_id="review")) == 2
    assert p._conn.execute("SELECT COUNT(*) FROM semantic_paragraph_checkpoints WHERE meeting_id='review'").fetchone()[0] == 2
    assert {item["failure_reason"] for item in p.list_intelligence_input_coverage("review")} == {"legacy_analysis_disabled"}
    replay = _direct_final(p, 2, enqueue_intelligence=False)
    assert replay["created"] is False and len(p.list_jobs(meeting_id="review")) == 2


@pytest.mark.parametrize("profile", ["participation", "legacy", None])
def test_official_final_producer_selects_one_engine_and_manual_does_not_switch_it(profile_app, profile, monkeypatch):
    app, client = profile_app
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    if profile is not None:
        client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": profile, "proactive_suggestion_policy": "standard"})
    committed = app.state.commit_v2_final("review", {"segment_id": "actual-final", "text": "支付失败时应该怎么恢复？", "source_track": "system_audio", "start_ms": 0, "end_ms": 1000})
    if profile == "participation":
        assert set(committed["job_ids"]) == {"correction"}
        assert app.state.provider_lane_registry.pending_realtime_count == 0
    else:
        assert "intelligence" in committed["job_ids"] and "answer" in committed["job_ids"]
        assert app.state.provider_lane_registry.pending_realtime_count > 0
    manual = client.post("/v2/meetings/review/coach/request", json={"intent": "ask_question"}, headers={"Idempotency-Key": "manual-review"})
    assert manual.status_code == 202, manual.text
    assert manual.json()["job"]["request_context"]["profile"] == "participation"
    assert client.get("/v2/meetings/review/snapshot").json()["live_analysis_profile"] == (profile or "legacy")


def test_participation_consumer_disables_old_jobs_and_supersession_fallback(profile_app):
    app, client = profile_app
    client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"})
    # Simulate a caller from before the additive producer argument existed.
    committed = _direct_final(app.state.v2_persistence, 1, enqueue_answer=True)
    p = app.state.v2_persistence
    intelligence = p.get_job(committed["job_ids"]["intelligence"])
    answer = p.get_job(committed["job_ids"]["answer"])
    result = asyncio.run(app.state.v2_intelligence_job_handler_impl(intelligence))
    assert result["provider_attempt_count"] == 0 and result["reason"] == "live_analysis_profile_disabled"
    result = asyncio.run(app.state.v2_answer_job_handler_impl(answer))
    assert result["provider_attempt_count"] == 0 and result["reason"] == "live_analysis_profile_disabled"
    assert app.state.sync_realtime_provider_reservations() == set()
    assert app.state.sync_deep_provider_reservations() == set()


def test_legacy_scheduler_never_parallelizes_new_discussion_lane(profile_app, monkeypatch):
    app, _client = profile_app
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "synthetic-key")
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://synthetic.example")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "synthetic-model")
    monkeypatch.setenv("MEETING_COPILOT_DISCUSSION_AUTOMATIC_ENABLED", "1")
    for seq in range(1, 4):
        _direct_final(app.state.v2_persistence, seq)
    assert asyncio.run(app.state.refresh_due_coach_items()) == 0
    assert not any(job["trigger_type"] == "discussion_update" for job in app.state.v2_persistence.list_jobs(meeting_id="review"))


@pytest.mark.parametrize("trigger", ["user_request", "discussion_update"])
def test_trusted_participation_handler_reaches_sidecar_and_persists_its_own_typed_card(profile_app, monkeypatch, trigger):
    app, client = profile_app
    config = app_module.llm_service.LlmConfig(base_url="https://synthetic.invalid", api_key="synthetic-test-key",
        model="synthetic-model", timeout_seconds=25, is_mock=True)
    monkeypatch.setattr(app_module.llm_service.LlmConfig, "from_env", classmethod(lambda _cls: config))
    monkeypatch.setattr(app_module, "_ensure_llm_provider_allowed_for_derivation", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_PI_LOCAL_REFLEX_FIRST", "0")
    client.put("/v2/meetings/review/preparation", json={"live_analysis_profile": "participation"})
    _direct_final(app.state.v2_persistence, 1, enqueue_intelligence=False)
    p = app.state.v2_persistence
    now = time.time_ns() // 1_000_000
    if trigger == "user_request":
        queued = p.enqueue_user_coach_request(meeting_id="review", user_request="", intent="ask_question",
            idempotency_key="typed-handler", now_ms=now)
    else:
        # Automatic transport assumes L2 has completed unchanged. The manual
        # branch deliberately keeps pending L2 and remains independently usable.
        correction = p.claim_next_job(worker_id="fixture-l2", lane="correction",
            now_ms=now, lease_ms=5_000)
        if correction is None or correction["meeting_id"] != "review":
            raise AssertionError("expected this fixture's correction job")
        segment_id = correction["evidence_segment_id"]
        unchanged = p.mark_correction_segments_no_change(meeting_id="review", segment_ids=[segment_id],
            max_input_transcript_seq=correction["input_transcript_seq"], now_ms=now,
            expected_evidence_hashes={segment_id: correction["evidence_hash"]})
        if unchanged != [segment_id]:
            raise AssertionError("fixture correction evidence did not match")
        if p.complete_job(job_id=correction["id"], worker_id="fixture-l2", now_ms=now,
                output={"fixture": "unchanged"}) is None:
            raise AssertionError("fixture correction did not complete")
        if p.has_pending_discussion_corrections("review"):
            raise AssertionError("fixture correction work remains active")
        # Script a positive state reply using its exact frozen sources. This
        # tests native admission, not the quality of a real interpretation.
        state = p.enqueue_discussion_state(meeting_id="review", now_ms=now, stable_ms=0,
            minimum_segments=1, minimum_characters=0)
        if state is None:
            raise AssertionError("expected the fixture's state request")
        claimed = p.claim_next_job(worker_id="fixture-state", lane="discussion_state", now_ms=now, lease_ms=5_000)
        if claimed is None or claimed["id"] != state["id"]:
            raise AssertionError("fixture state lease mismatch")
        response = state_response(p, claimed, {"paragraph_revisions": [], "topic_update": None,
            "state_changes": [], "follow_up": None})
        p.apply_intelligence_response(meeting_id="review", job_id=claimed["id"], response=response, now_ms=now)
        if not p.complete_job(job_id=claimed["id"], worker_id="fixture-state", now_ms=now, output={}):
            raise AssertionError("fixture state did not complete")
        queued = p.enqueue_discussion_update(meeting_id="review", now_ms=now)
    assert queued is not None
    running = p.claim_next_job(worker_id="typed-handler", lane="pi_deep", now_ms=now, lease_ms=30_000)
    assert running["id"] == queued["id"]
    packets = []

    class FakeSidecar:
        available = True

        async def evaluate(self, payload):
            packets.append(payload)
            context = payload["context"]
            assert context["coach_profile"] == "participation"
            assert context["request_metadata"]["topic_id"] == queued["request_context"]["topic_id"]
            assert context["trigger_type"] == trigger
            assert context["rolling_state"]["current_answer"] is None
            paragraph = context["context_paragraphs"][-1]
            result = {"action": "intervention", "metrics": {"turns": 1, "tool_calls": 1, "tool_names": ["submit_intervention"]},
                "intervention": {"event_type": "question_to_user", "title": "核对通知失败边界", "urgency": "low", "confidence": .95,
                    "recommendation": "建议先把权益与通知的失败处理边界分开，核对通知重试对权益发放结果的影响。",
                    "reason": "原话允许通知稍后发送，可以单独讨论通知失败路径。",
                    "evidence_segment_ids": [paragraph["id"]], "evidence_quote": paragraph["text"],
                    "supporting_facts": [{"text": "通知可以稍后发送", "source_ref": paragraph["id"]}], "assumptions": []}}
            if context["request_metadata"].get("current_need_contract") == "participation_need.v1":
                result["current_need"] = {
                    "status": "has_current_need",
                    "focus_evidence": [{"segment_id": paragraph["id"], "quote": paragraph["text"]}],
                    "reason": "最新原话仍有权益与通知失败边界值得确认。",
                }
            return result

    async def semantic_must_not_run(**_kwargs):
        raise AssertionError("participation must not invoke the legacy semantic suggestion branch")

    app.state.pi_coach_runtime = FakeSidecar()
    app.state.streaming_llm_client = object()
    monkeypatch.setattr(app_module, "run_realtime_intelligence", semantic_must_not_run)
    output = asyncio.run(app.state.v2_intelligence_job_handler_impl(running))
    assert len(packets) == 1
    assert output["coach"]["status"] == "intervention", output["coach"]
    assert output["coach"]["provider_lane"] == "pi_deep"
    assert output["applied"]["coach_intervention"]["analysis_kind"] == "participation"
    assert output["applied"]["coach_intervention"]["job_id"] == queued["id"]
    assert p.complete_job(job_id=queued["id"], worker_id="typed-handler", now_ms=now + 1, output=output)
    snapshot = client.get("/v2/meetings/review/snapshot").json()
    assert snapshot["coach_intervention"]["job_id"] == queued["id"]
    assert snapshot["coach_intervention"]["supporting_facts"] == [{"text": "通知可以稍后发送", "source_ref": "s1"}]
    # A later understanding failure is a separate lane; it cannot erase or
    # masquerade as failure of this already readable contribution.
    _direct_final(p, 2, enqueue_intelligence=False, text="接下来核对另一项通知恢复条件。")
    state = p.enqueue_discussion_state(meeting_id="review", now_ms=now + 2,
        cooldown_ms=0, stable_ms=0, minimum_segments=1, minimum_characters=0)
    assert state is not None
    running_state = p.claim_next_job(worker_id="state-failure", lane="discussion_state", now_ms=now + 2, lease_ms=30_000)
    assert p.fail_job(job_id=running_state["id"], worker_id="state-failure", now_ms=now + 3,
        error_class="intelligence_validation_evidence")
    failed_snapshot = client.get("/v2/meetings/review/snapshot").json()
    assert failed_snapshot["runtime"]["ai"]["capabilities"]["discussion_state"]["decision"] == "failed"
    assert failed_snapshot["coach_intervention"]["job_id"] == queued["id"]
    assert failed_snapshot["coach_decision"]["job_id"] == queued["id"]

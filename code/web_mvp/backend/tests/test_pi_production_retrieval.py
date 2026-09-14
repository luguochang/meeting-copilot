import asyncio
from pathlib import Path
import shutil
import time

import pytest

import meeting_copilot_web_mvp.app as app_module
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_production_handler_persists_pi_intervention_with_same_meeting_history(
    tmp_path,
    monkeypatch,
):
    """The durable handler must wire Pi host retrieval to the meeting database."""

    node = shutil.which("node")
    bridge = REPO_ROOT / "code/agent_runtime/pi_coach_bridge"
    fixture = bridge / "test/fixtures/host_evidence_production.mjs"
    if node is None or not (bridge / "node_modules/@earendil-works/pi-agent-core").exists():
        pytest.skip("Node and Pi bridge dependencies are required")

    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_ENABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    config = app_module.llm_service.LlmConfig(
        base_url="https://controlled.invalid/v1",
        api_key="controlled-test-only",
        model="controlled-model",
        realtime_model="controlled-realtime-model",
        timeout_seconds=20,
        is_mock=False,
    )
    monkeypatch.setattr(
        app_module.llm_service.LlmConfig,
        "from_env",
        classmethod(lambda _cls: config),
    )
    monkeypatch.setattr(app_module.llm_service, "realtime_config", lambda value: value)
    monkeypatch.setattr(
        app_module,
        "_ensure_llm_provider_allowed_for_derivation",
        lambda *_args, **_kwargs: None,
    )

    async def fake_semantic(**_kwargs):
        now = 1.0
        return {
            "response": app_module.RealtimeIntelligenceResponse((), None, (), None),
            "transport_mode": "controlled_semantic",
            "ttft_ms": 1,
            "timings": {
                "started_at": now,
                "connected_at": now,
                "first_token_at": now,
                "completed_at": now,
            },
            "usage": None,
            "model": "controlled-model",
        }

    monkeypatch.setattr(app_module, "run_realtime_intelligence", fake_semantic)
    app = create_app(data_dir=tmp_path, semantic_projection_mode="llm_first")
    sidecar = PiCoachSidecar(
        command=[node, str(fixture)],
        evidence_registry_factory=lambda meeting_id: app_module.PiEvidenceRegistry(
            app.state.v2_persistence,
            meeting_id=meeting_id,
        ),
    )
    app.state.pi_coach_runtime = sidecar

    meeting_id = "production-pi-history-meeting"
    store = app.state.v2_persistence
    try:
        base_now = time.time_ns() // 1_000_000
        store.commit_final_and_enqueue(
            meeting_id=meeting_id,
            final_id="old-final",
            segment_id="old-0",
            text="发布前需要法务批准。",
            normalized_text="发布前需要法务批准。",
            started_at_ms=0,
            ended_at_ms=900,
            evidence_hash="hash-0",
            source_track="system_audio",
            now_ms=base_now - 1_000,
            enqueue_jobs=False,
        )
        committed = store.commit_final_and_enqueue(
            meeting_id=meeting_id,
            final_id="new-final",
            segment_id="new-12",
            text="我们担心周五发布会影响监控，需要先确认监控阈值。",
            normalized_text="我们担心周五发布会影响监控，需要先确认监控阈值。",
            started_at_ms=12_000,
            ended_at_ms=12_900,
            evidence_hash="hash-12",
            source_track="microphone",
            now_ms=base_now,
        )
        store.mark_correction_segments_no_change(
            meeting_id=meeting_id,
            segment_ids=["old-0", "new-12"],
            max_input_transcript_seq=100,
            now_ms=base_now + 1,
        )
        job = store.get_job(committed["job_ids"]["intelligence"])
        output = asyncio.run(app.state.v2_intelligence_job_handler_impl(job))

        assert output["coach"]["runtime_requested"] == "pi"
        assert output["coach"]["runtime_used"] == "pi"
        assert output["coach"]["origin"] == "pi"
        assert output["coach"]["status"] == "intervention"
        intervention = output["coach"]["intervention"]
        assert intervention["evidence_segment_ids"] == ["old-0", "new-12"]
        assert "发布前需要法务批准" in intervention["evidence_quote"]
        assert "我们担心周五发布会影响监控" in intervention["evidence_quote"]
        assert output["coach"]["agent_metrics"]["history_searches"] == 1
        assert output["coach"]["agent_metrics"]["history_results"] == 1

        applied = [
            event
            for event in store.list_events(meeting_id, limit=1_000)
            if event["type"] == "meeting.intelligence.applied"
        ]
        assert len(applied) == 1
        payload = applied[0]["payload"]
        assert payload["coach_decision"]["runtime_used"] == "pi"
        assert payload["coach_decision"]["origin"] == "pi"
        assert payload["coach_intervention"]["evidence_segment_ids"] == [
            "old-0",
            "new-12",
        ]
        assert output["formal_event_context"]["evidence"]["evidence_hash"] == "hash-12"
    finally:
        sidecar.close()
        store.close()

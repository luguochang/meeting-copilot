"""Meeting-selected refinement, real native persistence and L2; no real models."""

import asyncio
import json
import os
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from meeting_copilot_web_mvp import asr_correct, asr_refiner, asr_stream
from meeting_copilot_web_mvp import asr_resource_guard, correction_capability, llm_service
from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.asr_live_repository import InMemoryAsrLiveSessionRepository
from meeting_copilot_web_mvp.meeting_preparation import MeetingPreparationStore
from test_asr_stream_resource_fallback import (
    _AcknowledgedOnlineRecognizer, _CapturedWebSocket, SPEECH, SILENCE,
)


RAW = "接口先开启 5% 灰度，如果错误率超过 0.1% 就回滚，张三今天补回滚策掠并确认监控阈值。"


def forbidden(*_args, **_kwargs):
    raise AssertionError("optional offline work must not be requested")


@pytest.fixture
def isolated_stream(monkeypatch):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_stream, "_maybe_sherpa_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "_maybe_funasr_sidecar", lambda _sid: None)
    monkeypatch.setattr(asr_stream, "DiarizationRuntime", lambda *_a, **_kw: SimpleNamespace(
        start=lambda: None, submit_pcm=lambda _pcm: None, observe_final=lambda _ev: None,
        finish=lambda: None, abort=lambda: None,
    ))


def _forbid_offline(monkeypatch):
    for name in ("retain_refiner_for_meeting", "get_refiner_readiness_waiter",
                 "refine_pcm_f32", "release_refiner_for_meeting"):
        monkeypatch.setattr(asr_stream, name, forbidden)


def _capability(monkeypatch, *, commit=12, physical=10, warm=False, probe_failure=False):
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", lambda: None)
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "refiner_worker_status", lambda: {
        "process_running": warm, "process_ready": warm,
    })
    if probe_failure:
        monkeypatch.setattr(asr_resource_guard, "read_memory_snapshot", lambda: (
            _ for _ in ()).throw(asr_resource_guard.AsrResourceError("asr_resource_probe_failed")))
    else:
        monkeypatch.setattr(asr_resource_guard, "read_memory_snapshot", lambda: asr_resource_guard.MemorySnapshot(
            commit * asr_resource_guard.GIB, physical * asr_resource_guard.GIB, 123,
            total_physical_bytes=32 * asr_resource_guard.GIB,
        ))
    for name in ("prewarm_refiner_worker", "_get_resident_refiner"):
        monkeypatch.setattr(asr_refiner, name, forbidden)


def test_choice_is_versioned_omission_preserves_it_and_legacy_defaults(tmp_path):
    store = MeetingPreparationStore(tmp_path)
    original = store.save("m", updated_at_ms=1)
    assert original.correction_mode == "local_and_llm"
    saved = store.save("m", correction_mode="llm_only", updated_at_ms=2)
    edited = store.save("m", hotwords=["灰度"], updated_at_ms=3)
    assert saved.correction_mode == edited.correction_mode == "llm_only"
    assert [p.correction_mode for p in store.list_versions("m")] == ["local_and_llm", "llm_only", "llm_only"]
    old_payload = original.to_dict()
    old_payload.pop("correction_mode")
    (tmp_path / "m.json").write_text(json.dumps(old_payload), encoding="utf-8")
    assert store.get("m").correction_mode == "local_and_llm"


@pytest.mark.parametrize("invalid", ["", "online_only", True, 1, " llm_only"])
def test_mode_unknown_values_are_rejected(tmp_path, invalid):
    with pytest.raises(ValueError, match="correction_mode"):
        MeetingPreparationStore(tmp_path).save("m", correction_mode=invalid, updated_at_ms=1)


@pytest.mark.parametrize("commit,physical,warm,expected,effective", [
    (12, 10, False, 8, "local_and_llm"),
    (7, 10, False, 8, "llm_only"),
    (12, 5, False, 8, "llm_only"),
    (3, 3, True, 2, "local_and_llm"),
])
def test_preflight_reads_current_resources_without_models(monkeypatch, tmp_path, commit, physical, warm, expected, effective):
    _capability(monkeypatch, commit=commit, physical=physical, warm=warm)
    app = create_app(data_dir=tmp_path, prewarm_funasr=False)
    client = TestClient(app)
    try:
        result = client.get("/providers/asr/correction-capability").json()
        assert result["schema_version"] == "meeting_correction_capability.v1"
        assert result["effective_mode"] == effective
        memory = result["memory"]
        assert memory["total_physical_bytes"] == 32 * asr_resource_guard.GIB
        assert memory["commit_available_bytes"] == commit * asr_resource_guard.GIB
        assert memory["physical_available_bytes"] == physical * asr_resource_guard.GIB
        assert memory["required_commit_bytes"] == expected * asr_resource_guard.GIB
        assert result["fallback_reason"] == ("asr_capacity_unavailable" if effective == "llm_only" else None)
        assert result["llm_correction"]["enabled"] is True
        assert result["llm_correction"]["configured"] is False
        assert client.put("/v2/meetings/m/preparation", json={"correction_mode": "llm_only"}).status_code == 200
        actual = client.get("/providers/asr/correction-capability?meeting_id=m&correction_mode=local_and_llm").json()
        assert actual["requested_mode"] == actual["effective_mode"] == "llm_only"
        assert actual["mode_source"] == "meeting_preparation"
        assert actual["fallback_reason"] is None
        assert client.get("/providers/asr/correction-capability?correction_mode=unknown").status_code == 422
    finally:
        for repo in app.state.sqlite_repositories:
            repo.close()


def test_probe_failure_does_not_claim_local_ready_or_capacity_refusal(monkeypatch):
    _capability(monkeypatch, probe_failure=True)
    result = correction_capability.correction_capability(correction_mode="local_and_llm", llm_enabled=True)
    assert result["local_refinement"] == {
        "available": False, "worker_ready": False, "reason": "asr_resource_probe_failed",
        "recovery_required": False, "last_error_code": None,
    }
    assert result["memory"]["checked"] is False
    assert result["effective_mode"] == "local_and_llm"
    assert result["fallback_reason"] is None
    assert result["recommended_mode"] == "llm_only"
    llm_only = correction_capability.correction_capability(correction_mode="llm_only", llm_enabled=True)
    assert llm_only["effective_mode"] == "llm_only"


def test_llm_configuration_uses_the_correction_model_resolver(monkeypatch):
    _capability(monkeypatch)
    base = object()
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", lambda: base)
    calls = []
    monkeypatch.setattr(llm_service, "correction_config", lambda value: calls.append(value) or object())
    result = correction_capability.correction_capability(correction_mode="llm_only", llm_enabled=False)
    assert calls == [base]
    assert result["llm_correction"] == {"enabled": False, "configured": True}


def test_recovered_memory_does_not_claim_a_latched_failed_worker_is_ready(monkeypatch):
    _capability(monkeypatch, warm=True)
    monkeypatch.setattr(asr_refiner, "refiner_worker_status", lambda: {
        "process_running": True, "process_ready": True, "recovery_required": True,
        "last_error_code": "asr_capacity_unavailable",
    })
    result = correction_capability.correction_capability(correction_mode="local_and_llm", llm_enabled=True)
    local = result["local_refinement"]
    assert local["available"] is True  # resources permit an explicit prepare/retry
    assert local["worker_ready"] is False and local["recovery_required"] is True
    assert local["last_error_code"] == "asr_capacity_unavailable"
    assert result["memory"]["required_commit_bytes"] == 8 * asr_resource_guard.GIB


def test_service_startup_prewarms_only_online_before_a_meeting_choice(monkeypatch, tmp_path):
    monkeypatch.setenv(asr_refiner.REALTIME_REFINER_POLICY_ENV, "prewarm")
    calls = []
    monkeypatch.setattr(asr_refiner, "refinement_capability", lambda: {"status": "ready"})
    monkeypatch.setattr(asr_refiner, "prewarm_refiner_worker", forbidden)
    monkeypatch.setattr(asr_stream, "prewarm_funasr_resident_manager", lambda: calls.append("online") or True)
    monkeypatch.setattr(asr_stream, "shutdown_funasr_resident_manager", lambda: None)
    monkeypatch.setattr(asr_refiner, "shutdown_refiner_worker", lambda: None)
    with TestClient(create_app(data_dir=tmp_path, prewarm_funasr=True)) as client:
        assert client.app.state.funasr_resident_prewarm_ready is True
        assert client.app.state.funasr_refiner_prewarm_ready is False
        assert client.app.state.funasr_refiner_policy["mode"] == "prewarm"
    assert calls == ["online"]


def test_native_llm_only_ws_saves_history_and_l2_corrects_without_offline_work(monkeypatch, tmp_path, isolated_stream):
    _forbid_offline(monkeypatch)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "gpt-test")
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "1")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "1")
    recognizer = _AcknowledgedOnlineRecognizer(texts=(RAW,))
    recognizer.wait_ready = lambda _timeout: True
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    app = create_app(data_dir=tmp_path, prewarm_funasr=False, semantic_projection_mode="llm_first")
    client = TestClient(app)
    mid = "native-llm-only"
    calls = []
    monkeypatch.setattr(asr_correct, "correct_transcript", lambda raw, *_a, **_kw: (
        calls.append(raw) or raw.replace("策掠", "策略"),
        {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}, False,
    ))
    try:
        assert client.put(f"/v2/meetings/{mid}/preparation", json={
            "notice_acknowledged": True, "correction_mode": "llm_only",
            "live_analysis_profile": "participation",
        }).status_code == 200
        with client.websocket_connect(f"/live/asr/stream/ws/{mid}?audio_source=browser_live_mic") as ws:
            for pcm in (SPEECH, SILENCE, SILENCE, SILENCE):
                ws.send_bytes(pcm)
            events = []
            for _ in range(12):
                events.append(json.loads(ws.receive_text()))
                if events[-1].get("event_type") == "final":
                    break
            ready = next(e for e in events if e.get("event_type") == "asr_ready")
            assert ready["requested_correction_mode"] == ready["effective_correction_mode"] == "llm_only"
            assert ready["offline_refinement_ready"] is False
            final = events[-1]
            assert final["event_type"] == "final" and final["text"] == RAW
            assert final["final_source"] == "local_realtime_online_final"
            assert final["refinement_status"] == "bypassed"
            snapshot = client.get(f"/v2/meetings/{mid}/snapshot").json()
            assert snapshot["correction_mode"] == "llm_only" and snapshot["correction_mode_locked"] is True
            assert snapshot["segments"][0]["text"] == RAW
            assert client.put(f"/v2/meetings/{mid}/preparation", json={"correction_mode": "local_and_llm"}).status_code == 409
            assert client.put(f"/v2/meetings/{mid}/preparation", json={"correction_mode": "llm_only", "notice_acknowledged": True}).status_code == 200
            assert client.put(f"/v2/meetings/{mid}/preparation", json={"hotwords": ["灰度"], "notice_acknowledged": True}).json()["correction_mode"] == "llm_only"
            ws.send_text("END")
            with pytest.raises(WebSocketDisconnect):
                while True:
                    ws.receive_text()
        p = app.state.v2_persistence
        now = time.time_ns() // 1_000_000
        p.end_meeting(meeting_id=mid, now_ms=now)
        job = p.claim_next_job(worker_id="mode-test", lane="correction", now_ms=now + 1, lease_ms=30_000)
        assert job is not None
        output = app.state.v2_correction_job_handler_impl(job)
        assert p.complete_job(job_id=job["id"], worker_id="mode-test", now_ms=time.time_ns() // 1_000_000, output=output)
        snapshot = client.get(f"/v2/meetings/{mid}/snapshot").json()
        segment = snapshot["segments"][0]
        assert len(calls) == 1
        assert segment["text"] == RAW and segment["normalized_text"] == RAW.replace("策掠", "策略")
        assert segment["correction_status"] == "changed"
        assert "策略" in " ".join(p["text"] for p in snapshot["semantic_paragraphs"])
        record = app.state.asr_live_repository.get(mid)
        assert record["settings_snapshot"]["asr"]["correction_mode"] == "llm_only"
        assert record["effective_correction_mode"] == "llm_only"
    finally:
        for repo in app.state.sqlite_repositories:
            repo.close()


@pytest.mark.parametrize("bad_source", ["no_ack", "no_audio", "short_text"])
def test_llm_only_does_not_promote_unclosed_or_empty_audio(monkeypatch, isolated_stream, bad_source):
    _forbid_offline(monkeypatch)
    recognizer = _AcknowledgedOnlineRecognizer(texts=(("嗯" if bad_source == "short_text" else RAW),))
    if bad_source == "no_ack":
        recognizer.flush_utterance = None
    monkeypatch.setattr(asr_stream, "get_recognizer", lambda _sid: recognizer)
    messages = ([{"bytes": SPEECH}, *({"bytes": SILENCE} for _ in range(3))]
                if bad_source != "no_audio" else [])
    ws = _CapturedWebSocket([*messages, {"text": "END"}])
    committed = []
    asyncio.run(asr_stream.handle_stream(ws, "bad-source", asr_live_repo=InMemoryAsrLiveSessionRepository(),
        correction_mode="llm_only", on_final_committed=committed.append))
    assert committed == []
    assert not any(e.get("event_type") == "final" for e in ws.sent)


def test_simultaneous_meetings_keep_their_own_mode_and_local_preparation(monkeypatch, isolated_stream):
    calls = []
    recognizers = {sid: _AcknowledgedOnlineRecognizer(texts=(RAW,)) for sid in ("local", "llm")}
    for recognizer in recognizers.values():
        recognizer.wait_ready = lambda _timeout: True
    monkeypatch.setattr(asr_stream, "get_recognizer", recognizers.__getitem__)
    monkeypatch.setattr(asr_stream, "retain_refiner_for_meeting", lambda sid: calls.append(("retain", sid)) or True)
    monkeypatch.setattr(asr_stream, "get_refiner_readiness_waiter", lambda sid: (
        calls.append(("waiter", sid)) or (lambda *_a: True)))
    monkeypatch.setattr(asr_stream, "release_refiner_for_meeting", lambda sid: calls.append(("release", sid)))
    monkeypatch.setattr(asr_stream, "refine_pcm_f32", lambda pcm: (
        calls.append(("refine", len(pcm))) or asr_refiner.RefinementResult(
            text=RAW.replace("策掠", "策略"), status="refined", model_id="local-test",
        )))
    repository = InMemoryAsrLiveSessionRepository()
    sockets = {sid: _CapturedWebSocket([
        {"bytes": SPEECH}, *({"bytes": SILENCE} for _ in range(3)), {"text": "END"},
    ]) for sid in recognizers}

    async def run():
        await asyncio.gather(*(
            asr_stream.handle_stream(sockets[sid], sid, asr_live_repo=repository,
                correction_mode="llm_only" if sid == "llm" else "local_and_llm")
            for sid in recognizers
        ))

    asyncio.run(run())
    assert os.environ[asr_refiner.REALTIME_REFINER_POLICY_ENV] == "prewarm"
    assert [(kind, sid) for kind, sid in calls if kind != "refine"] == [
        ("retain", "local"), ("waiter", "local"), ("release", "local"),
    ]
    assert len([call for call in calls if call[0] == "refine"]) == 1
    for sid, source in (("local", "local_offline_refinement"), ("llm", "local_realtime_online_final")):
        final = next(e for e in sockets[sid].sent if e.get("event_type") == "final")
        assert final["final_source"] == source
        assert final["text"] == (RAW.replace("策掠", "策略") if sid == "local" else RAW)
        assert repository.get(sid)["settings_snapshot"]["asr"]["correction_mode"] == (
            "local_and_llm" if sid == "local" else "llm_only")

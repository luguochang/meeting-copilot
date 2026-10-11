"""Correction availability at the real app boundary, with local injected I/O."""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp import app as app_module, llm_service
from meeting_copilot_web_mvp.correction_provider_availability import CorrectionProviderAvailabilityDeferred
from meeting_copilot_web_mvp.v2_persistence import _public_job_error_message
from test_correction_batch_backlog import _fixture


def _open(app):
    guard = app.state.correction_provider_availability
    config = llm_service.LlmConfig.from_env()
    for _ in range(2):
        guard.admit_or_defer(config).record_failure(llm_service.LlmProviderTransportError("timeout"))
    return guard, config


def _run(app, **kwargs):
    return asyncio.run(app.state.run_asr_live_session_realtime_corrections_once_async(
        "meeting-1", app_module.RunRealtimeCorrectionsRequest(force=True), **kwargs))


def test_paused_actual_app_skips_provider_and_does_not_claim_batch(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    calls = []
    try:
        guard, config = _open(app)
        with pytest.raises(CorrectionProviderAvailabilityDeferred) as deferred:
            _run(app, provider_runner=lambda *args, **kwargs: calls.append(True))
        assert deferred.value.preserve_attempt is True
        assert calls == []
        correction = app.state.asr_live_repository.get("meeting-1").get("realtime_transcript_correction", {})
        assert not correction.get("reservation")
        assert guard.snapshot(config).failure_count == 2
        assert app.state.provider_lane_registry.active_correction_count == 0
    finally:
        persistence.close()


def test_no_current_batch_and_disabled_setting_do_not_reset_outage(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    try:
        guard, config = _open(app)
        app.state.asr_live_repository.update("meeting-1", lambda record: {
            **record, "realtime_transcript_correction": {"processed_segment_ids": ["s1", "s2", "s3", "s4"]}})
        assert _run(app)["called"] is False
        client = TestClient(app)
        settings = client.get("/settings").json()
        settings["asr"]["l2_correction_enabled"] = False
        assert client.patch("/settings", json=settings).status_code == 200
        assert _run(app)["gate"]["reason"] == "disabled_by_setting"
        assert guard.snapshot(config).state == "open"
    finally:
        persistence.close()


def test_two_actual_transport_failures_open_only_correction(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    calls = []
    try:
        async def failing(*args, **kwargs):
            calls.append(True)
            raise llm_service.LlmProviderTransportError("timeout", timeout_origin="read")
        monkeypatch.setattr(app_module.asr_correct, "async_correct_transcript", failing)
        for _ in range(2):
            with pytest.raises(app_module.RealtimeCorrectionProviderError):
                _run(app)
        config = llm_service.LlmConfig.from_env()
        assert len(calls) == 2
        assert app.state.correction_provider_availability.snapshot(config).state == "open"
        assert app.state.realtime_provider_circuit.snapshot(app_module._realtime_provider_identity(config)).state == "closed"
    finally:
        persistence.close()


def test_half_open_actual_response_restores_availability_before_content_rejection(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    calls = []
    try:
        guard, config = _open(app)
        clock = SimpleNamespace(seconds=0.0)
        # Advance relative to the existing durable circuit's clock, preserving
        # persisted wall-clock coordinates rather than resetting its identity.
        clock.seconds = guard._circuit._clock() + 61
        guard._circuit._clock = lambda: clock.seconds
        def malformed(raw, cfg, **kwargs):
            calls.append(True)
            return "missing indexed markers", {"total_tokens": 2}, False
        result = _run(app, provider_runner=malformed)
        assert result["called"] is True
        assert result["revision_count"] == 0
        assert len(calls) == 1
        assert guard.snapshot(config).state == "closed"
        assert guard.snapshot(config).failure_count == 0
    finally:
        persistence.close()


def test_cancelled_actual_recovery_releases_trial_without_counting_failure(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    try:
        guard, config = _open(app)
        future = guard._circuit._clock() + 61
        guard._circuit._clock = lambda: future
        async def cancelled(*args, **kwargs):
            raise asyncio.CancelledError
        monkeypatch.setattr(app_module.asr_correct, "async_correct_transcript", cancelled)
        with pytest.raises(asyncio.CancelledError):
            _run(app)
        admission = guard.acquire(config)
        assert admission.admitted and admission.permit.half_open
        assert admission.snapshot.failure_count == 2
        admission.permit.release()
    finally:
        persistence.close()


def test_correction_snapshot_is_safe_and_does_not_mark_pi_unavailable(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    try:
        _open(app)
        snapshot = TestClient(app).get("/v2/meetings/meeting-1/snapshot").json()
        availability = snapshot["diagnostics"]["correction_availability"]
        assert availability["state"] == "open" and availability["scope"] == "correction"
        assert snapshot["runtime"]["ai"]["capabilities"]["transcript"]["label"] == "AI校对暂不可用，识别原文已保存"
        assert snapshot["runtime"]["ai"]["capabilities"]["provider"]["state"] == "active"
        assert "sk-test" not in json.dumps(availability)
        assert "gw.example" not in json.dumps(availability)
    finally:
        persistence.close()


def test_explicit_run_once_reports_paused_correction_without_generic_server_error(monkeypatch, tmp_path):
    app, persistence, _, _, _ = _fixture(monkeypatch, tmp_path)
    try:
        _open(app)
        response = TestClient(app, raise_server_exceptions=False).post(
            "/live/asr/sessions/meeting-1/realtime-corrections/run-once", json={"force": True})
        assert response.status_code == 503
        assert response.json()["detail"]["error"] == "CorrectionProviderAvailabilityDeferred"
        assert response.json()["detail"]["message"] == "AI校对暂不可用，识别原文已保存"
    finally:
        persistence.close()


def test_deferred_error_has_public_correction_message(monkeypatch, tmp_path):
    _, persistence, job, _, now = _fixture(monkeypatch, tmp_path)
    try:
        persistence.defer_job(job_id=job["id"], worker_id=job["lease_owner"],
            error_class="CorrectionProviderAvailabilityDeferred", next_attempt_at_ms=now + 60_000,
            now_ms=now + 1)
        snapshot = persistence.get_snapshot("meeting-1")
        deferred = next(item for item in snapshot["jobs"] if item["id"] == job["id"])
        assert deferred["error_class"] == "CorrectionProviderAvailabilityDeferred"
        segment = next(item for item in snapshot["segments"] if item["segment_id"] == job["evidence_segment_id"])
        assert segment["correction_status"] == "pending"
        assert _public_job_error_message(deferred["error_class"]) == "AI校对暂不可用，识别原文已保存，等待后续恢复检查。"
        assert deferred["attempts"] == 0
    finally:
        persistence.close()

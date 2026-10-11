from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import capability_status, llm_service, metrics
from meeting_copilot_web_mvp.llm_lane_locks import ProviderLaneRegistry


def create_app(**kwargs):
    # app.py creates its module-level app on import; defer until isolation runs.
    from meeting_copilot_web_mvp.app import create_app as factory
    return factory(**kwargs)


def _realtime_provider_identity(config, **kwargs):
    from meeting_copilot_web_mvp.app import _realtime_provider_identity as identity
    return identity(config, **kwargs)


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    import os
    for name in tuple(os.environ):
        if name.startswith(("LLM_", "MEETING_COPILOT_", "FUNASR_", "ASR_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_service, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(metrics, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("MEETING_COPILOT_REALTIME_COACH_RUNTIME", "pi")
    monkeypatch.setattr(capability_status.asr_stream, "funasr_realtime_available", lambda: True)
    monkeypatch.setattr(capability_status.asr_stream, "_funasr_resident_enabled", lambda: True)
    monkeypatch.setattr(capability_status.asr_stream, "funasr_resident_status", lambda: {
        "process_running": True, "process_ready": True,
        "pid": 999, "active_session_id": "private-session", "last_error": None,
    })
    monkeypatch.setattr(capability_status.batch_transcribe, "capability_status", lambda: {
        "available": True, "status": "ready", "path": "private-model-path",
        "formats": {"wav": {"available": True}, "mp3": {"available": False}},
    })
    monkeypatch.setattr(capability_status.batch_transcribe, "runtime_status", lambda: {
        "resource": {"last_error_code": None, "last_check": None},
    }, raising=False)
    monkeypatch.setattr(capability_status.asr_refiner, "refinement_capability", lambda: {
        "status": "ready", "realtime_policy": {"realtime_refinement_enabled": True},
    })
    monkeypatch.setattr(capability_status.asr_refiner, "refiner_worker_status", lambda: {
        "process_running": False, "process_ready": False, "last_stop_reason": "idle_timeout",
    })


def test_source_runtime_is_usable_without_imported_package(tmp_path):
    client = TestClient(create_app(data_dir=tmp_path / "runtime"))
    response = client.get("/v2/local-capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["installed"] is False
    assert body["deployment_mode"] == "source"
    assert body["runtime_capabilities"]["realtime_asr"]["state"] == "ready"
    assert body["runtime_capabilities"]["file_asr"]["formats"] == {"wav": True, "mp3": False}
    assert body["runtime_capabilities"]["offline_refinement"]["state"] == "idle"
    payload = json.dumps(body)
    assert "private-session" not in payload and "private-model-path" not in payload
    assert "pid" not in payload


@pytest.mark.parametrize("worker,expected", [
    ({"process_running": True, "process_ready": False}, "starting"),
    ({"process_running": False, "process_ready": False}, "idle"),
    ({"process_running": True, "process_ready": True}, "ready"),
    ({"process_running": True, "process_ready": True, "last_error": "old_slot_failed"}, "ready"),
    ({"process_running": False, "last_error": "secret-key-or-private-text"}, "failed"),
    ({"process_running": False, "last_exit_code": 1}, "failed"),
    ({"process_running": False, "last_stop_reason": "worker_timeout"}, "failed"),
    (None, "unknown"),
])
def test_worker_states_are_observed_without_loading_models(monkeypatch, worker, expected):
    monkeypatch.setattr(capability_status.asr_stream, "funasr_resident_status", lambda: worker)
    result = capability_status.local_runtime_status({"installed": False}, {})
    assert result["runtime_capabilities"]["realtime_asr"]["state"] == expected
    assert "secret-key-or-private-text" not in json.dumps(result)


def test_failed_status_reader_is_unknown_and_resource_policy_is_disabled(monkeypatch):
    def unavailable():
        raise RuntimeError("secret-or-private-path")
    monkeypatch.setattr(capability_status.asr_stream, "funasr_realtime_available", unavailable)
    monkeypatch.setattr(capability_status.asr_refiner, "refinement_capability", lambda: {
        "status": "ready", "realtime_policy": {"realtime_refinement_enabled": False},
    })
    result = capability_status.local_runtime_status({"installed": True, "restart_required": True}, {})
    assert result["installed"] is True and result["restart_required"] is True
    assert result["runtime_capabilities"]["realtime_asr"]["available"] is None
    assert result["runtime_capabilities"]["realtime_asr"]["state"] == "unknown"
    assert result["runtime_capabilities"]["offline_refinement"]["state"] == "disabled"
    assert "secret-or-private-path" not in json.dumps(result)


@pytest.mark.parametrize("code,state", [
    ("asr_capacity_unavailable", "failed"),
    ("asr_resource_probe_failed", "unknown"),
    ("asr_resource_guard_triggered", "failed"),
    ("asr_cold_start_busy", "failed"),
])
def test_refiner_resource_rejection_preserves_installation_and_overrides_policy(monkeypatch, code, state):
    monkeypatch.setattr(capability_status.asr_refiner, "refinement_capability", lambda: {
        "status": "ready", "realtime_policy": {"realtime_refinement_enabled": False},
    })
    monkeypatch.setattr(capability_status.asr_refiner, "refiner_worker_status", lambda: {
        "process_running": False, "process_ready": False, "recovery_required": True,
        "last_error_code": code,
        "resource": {"last_error_code": code, "last_check": {
            "commit_available_bytes": 123, "private_path": r"C:\private\model.pt",
        }},
    })
    result = capability_status.local_runtime_status({"installed": True}, {})
    runtime = result["runtime_capabilities"]["offline_refinement"]
    assert result["installed"] is True
    assert runtime == {
        "available": True, "state": state, "source": "source_configuration",
        "reason": code, "process_ready": False, "process_running": False,
    }
    assert "private" not in json.dumps(result) and "commit_available_bytes" not in json.dumps(result)


def test_refiner_recovery_lock_and_unknown_diagnostic_cannot_appear_idle_or_ready(monkeypatch):
    monkeypatch.setattr(capability_status.asr_refiner, "refiner_worker_status", lambda: {
        "process_running": True, "process_ready": True, "recovery_required": True,
        "last_error_code": r"C:\private\secret-error",
    })
    runtime = capability_status.local_runtime_status({"installed": True}, {})["runtime_capabilities"]["offline_refinement"]
    assert runtime["state"] == "failed" and runtime["reason"] == "worker_reported_failure"
    assert "private" not in json.dumps(runtime)


@pytest.mark.parametrize("code,state", [
    ("asr_capacity_unavailable", "failed"),
    ("asr_resource_probe_failed", "unknown"),
    ("asr_resource_guard_triggered", "failed"),
    ("asr_cold_start_busy", "failed"),
])
def test_file_asr_uses_cached_resource_rejection_without_claiming_missing_components(monkeypatch, code, state):
    monkeypatch.setattr(capability_status.batch_transcribe, "runtime_status", lambda: {
        "resource": {"last_error_code": code, "last_check": {"private": "secret", "input_bytes": 456}},
    })
    runtime = capability_status.local_runtime_status({"installed": True}, {})["runtime_capabilities"]["file_asr"]
    assert runtime["available"] is True and runtime["state"] == state and runtime["reason"] == code
    assert runtime["formats"] == {"wav": True, "mp3": False}
    assert "secret" not in json.dumps(runtime) and "input_bytes" not in json.dumps(runtime)


def test_cached_resource_success_clears_rejection_without_status_starting_work(monkeypatch):
    observed = {"resource": {"last_error_code": "asr_capacity_unavailable", "last_check": None}}
    reads = []

    def cached_status():
        reads.append(True)
        return observed

    def forbidden(*args, **kwargs):
        raise AssertionError("status must not start ASR work")

    monkeypatch.setattr(capability_status.batch_transcribe, "runtime_status", cached_status)
    monkeypatch.setattr(capability_status.batch_transcribe, "transcribe_file_report", forbidden)
    monkeypatch.setattr(capability_status.asr_refiner, "prewarm_refiner_worker", forbidden)
    assert capability_status.local_runtime_status({}, {})["runtime_capabilities"]["file_asr"]["state"] == "failed"
    observed["resource"]["last_error_code"] = None
    runtime = capability_status.local_runtime_status({}, {})["runtime_capabilities"]["file_asr"]
    assert runtime["state"] == "available" and runtime["reason"] is None
    assert len(reads) == 2


def test_file_runtime_reader_failure_is_unknown_and_private_details_are_not_reflected(monkeypatch):
    def unavailable():
        raise RuntimeError(r"C:\private\secret-error")

    monkeypatch.setattr(capability_status.batch_transcribe, "runtime_status", unavailable)
    runtime = capability_status.local_runtime_status({}, {})["runtime_capabilities"]["file_asr"]
    assert runtime["available"] is True and runtime["state"] == "unknown"
    assert runtime["reason"] == "runtime_status_check_failed"
    assert "private" not in json.dumps(runtime)


def test_local_capability_get_reads_cached_rejection_and_does_not_attempt_recovery(monkeypatch, tmp_path):
    observed = {"resource": {"last_error_code": "asr_resource_probe_failed", "last_check": {
        "commit_available_bytes": 123, "private_path": r"C:\private\model.pt",
    }}}
    reads = []

    def cached_status():
        reads.append(True)
        return observed

    def forbidden(*args, **kwargs):
        raise AssertionError("GET must not start a model or file task")

    monkeypatch.setattr(capability_status.batch_transcribe, "runtime_status", cached_status)
    monkeypatch.setattr(capability_status.batch_transcribe, "transcribe_file_report", forbidden)
    monkeypatch.setattr(capability_status.asr_refiner, "prewarm_refiner_worker", forbidden)
    client = TestClient(create_app(data_dir=tmp_path / "runtime"))
    for _ in range(2):
        response = client.get("/v2/local-capabilities")
        assert response.status_code == 200
        body = response.json()
        assert body["runtime_capabilities"]["file_asr"]["state"] == "unknown"
        assert body["runtime_capabilities"]["file_asr"]["reason"] == "asr_resource_probe_failed"
        assert "private" not in response.text and "commit_available_bytes" not in response.text
    assert len(reads) == 2
    assert observed["resource"]["last_error_code"] == "asr_resource_probe_failed"


def test_provider_lane_status_does_not_invent_circuit_probe_or_admission():
    config = llm_service.LlmConfig("https://fixture.invalid", "fixture-secret", "general", realtime_model="fast", correction_model="correct")
    registry = ProviderLaneRegistry()
    registry.reserve_deep("private-job")
    circuit = {"state": "open", "reason": "rate_limit", "failure_count": 1}
    lanes = capability_status.provider_lane_status(config, {"probe_status": "succeeded"}, {"pi_deep": circuit}, registry, pi_enabled=True, correction_enabled=False)
    assert lanes["pi_deep"]["circuit_status"] == "open"
    assert lanes["pi_deep"]["pending_count"] == 1
    assert lanes["answer"]["circuit_status"] == "not_applicable"
    assert lanes["answer"]["active_count"] is None
    assert lanes["correction"]["model"] == "correct"
    assert lanes["correction"]["enabled"] is False
    assert lanes["pi_realtime"]["probe_status"] == "succeeded"
    assert all(lanes[lane]["probe_status"] == "not_run" for lane in ("answer", "pi_deep", "direct_semantic", "correction"))
    assert "fixture-secret" not in json.dumps(lanes) and "private-job" not in json.dumps(lanes)


def test_provider_api_shows_independent_circuits_and_content_free_recent_result(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://fixture.invalid")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "fixture-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "general")
    monkeypatch.setenv("LLM_GATEWAY_REALTIME_MODEL", "fast")
    monkeypatch.setenv("LLM_GATEWAY_CORRECTION_MODEL", "correct")
    app = create_app(data_dir=tmp_path / "runtime")
    config = llm_service.LlmConfig.from_env()
    admission = app.state.realtime_provider_circuits["pi_deep"].acquire(
        _realtime_provider_identity(config, scope="pi_deep"),
    )
    admission.permit.record_failure("rate_limit")
    trace = app.state.pipeline_traces.create(trace_id="private-trace", meeting_id="private-meeting")
    trace.mark("job_claimed", attributes={"lane": "pi_deep"})
    trace.record_provider_attempt(1, branch="coach", runtime="pi")
    trace.record_provider_attempt_outcome(1, "success", branch="coach", runtime="pi")
    body = TestClient(app).get("/providers/status").json()
    assert body["realtime_circuit"]["state"] == "closed"
    lanes = body["provider_lanes"]
    assert lanes["pi_deep"]["circuit_status"] == "open"
    assert lanes["pi_realtime"]["circuit_status"] == "closed"
    assert lanes["pi_deep"]["last_result"]["outcome"] == "success"
    assert lanes["pi_deep"]["last_result"]["configuration_match"] == "unknown"
    assert lanes["correction"]["circuit_status"] == "not_applicable"
    assert "fixture-secret" not in json.dumps(body) and "private-meeting" not in json.dumps(body)


def test_unconfigured_provider_api_does_not_load_local_dotenv(tmp_path):
    body = TestClient(create_app(data_dir=tmp_path / "runtime")).get("/providers/status").json()
    assert body["configured"] is False
    assert all(lane["configured"] is False and lane["model"] is None for lane in body["provider_lanes"].values())

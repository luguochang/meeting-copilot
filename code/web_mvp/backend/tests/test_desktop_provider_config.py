from __future__ import annotations

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


TOKEN = "a" * 64


def _clear_provider_env(monkeypatch) -> None:
    for name in (
        "LLM_GATEWAY_BASE_URL",
        "LLM_GATEWAY_API_KEY",
        "LLM_GATEWAY_MODEL",
        "LLM_GATEWAY_REALTIME_MODEL",
        "LLM_GATEWAY_CORRECTION_MODEL",
        "LLM_GATEWAY_PROVIDER_LABEL",
        "LLM_GATEWAY_API_STYLE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_service, "load_dotenv", lambda *args, **kwargs: False)


def test_runtime_provider_config_precedes_environment_and_can_be_cleared(monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://env.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-env-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "env-model")
    llm_service.clear_runtime_config()

    metadata = llm_service.configure_runtime(
        base_url="https://relay.example/",
        api_key="sk-runtime-secret",
        model="runtime-model",
        realtime_model="runtime-fast-model",
    )
    configured = llm_service.LlmConfig.from_env()

    assert metadata == {
        "provider": "openai_compatible_gateway",
        "model": "runtime-model",
        "realtime_model": "runtime-fast-model",
        "realtime_model_source": "runtime_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "runtime-model",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "is_mock": False,
        "configured_from_env": True,
        "api_style": "chat_completions",
    }
    assert configured is not None
    assert configured.base_url == "https://relay.example"
    assert configured.api_key == "sk-runtime-secret"
    assert configured.realtime_model == "runtime-fast-model"
    assert llm_service.realtime_config(configured).model == "runtime-fast-model"
    assert "sk-runtime-secret" not in repr(configured)

    llm_service.clear_runtime_config()
    restored = llm_service.LlmConfig.from_env()
    assert restored is not None
    assert restored.model == "env-model"


def test_desktop_runtime_never_falls_back_to_inherited_provider_environment(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://env.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-env-secret")

    assert llm_service.LlmConfig.from_env() is None


def test_remote_plain_http_provider_is_rejected_but_loopback_http_is_allowed(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()

    try:
        llm_service.configure_runtime(
            base_url="http://relay.example",
            api_key="sk-test-secret",
            model="test-model",
        )
    except ValueError as error:
        assert "must use HTTPS" in str(error)
    else:
        raise AssertionError("remote plaintext HTTP provider must be rejected")

    metadata = llm_service.configure_runtime(
        base_url="http://127.0.0.1:8000",
        api_key="sk-test-secret",
        model="test-model",
    )
    assert metadata["model"] == "test-model"
    llm_service.clear_runtime_config()


def test_desktop_provider_config_requires_authenticated_desktop_runtime(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()
    monkeypatch.delenv("MEETING_COPILOT_LOCAL_API_TOKEN", raising=False)
    monkeypatch.delenv("MEETING_COPILOT_DESKTOP_RUNTIME", raising=False)

    client = TestClient(create_app())
    response = client.put(
        "/desktop/provider/config",
        json={
            "base_url": "https://relay.example",
            "api_key": "sk-test-secret",
            "model": "test-model",
        },
    )

    assert response.status_code == 403
    assert not llm_service.runtime_configured()


def test_desktop_provider_config_updates_running_backend_without_echoing_secret(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    app = create_app()
    client = TestClient(app)
    headers = {"x-meeting-copilot-token": TOKEN}

    class ResumeSpy:
        calls = 0

        def resume(self) -> None:
            self.calls += 1

    resume_spy = ResumeSpy()
    app.state.v2_executor = resume_spy

    response = client.put(
        "/desktop/provider/config",
        headers=headers,
        json={
            "base_url": "https://relay.example/v1-root",
            "api_key": "sk-never-echo-this",
            "model": "gpt-test",
            "realtime_model": "gpt-test-fast",
            "realtime_model_source": "runtime_realtime_model",
            "api_style": "responses",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["runtime_override"] is True
    assert body["model"] == "gpt-test"
    assert body["realtime_model"] == "gpt-test-fast"
    assert body["realtime_model_source"] == "runtime_realtime_model"
    assert body["realtime_model_explicit"] is True
    assert body["realtime_model_warning"] is None
    assert body["api_style"] == "responses"
    assert "never-echo" not in response.text
    assert resume_spy.calls == 1
    status = client.get("/desktop/provider/config", headers=headers)
    assert status.status_code == 200
    assert status.json()["runtime_override"] is True

    deleted = client.delete("/desktop/provider/config", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["configured"] is False
    assert not llm_service.runtime_configured()


def test_desktop_provider_config_preserves_general_model_fallback_source(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    headers = {"x-meeting-copilot-token": TOKEN}

    with TestClient(create_app()) as client:
        response = client.put(
            "/desktop/provider/config",
            headers=headers,
            json={
                "base_url": "https://relay.example",
                "api_key": "sk-test-secret",
                "model": "general-model",
                "realtime_model": "general-model",
                "realtime_model_source": "general_model_fallback",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["realtime_model"] == "general-model"
    assert body["realtime_model_source"] == "general_model_fallback"
    assert body["realtime_model_explicit"] is False
    assert body["realtime_model_warning"] == "realtime_model_inherits_general_model"
    llm_service.clear_runtime_config()


def test_provider_status_is_stable_across_refresh_probe_and_model_switch(monkeypatch):
    _clear_provider_env(monkeypatch)
    llm_service.clear_runtime_config()
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    headers = {"x-meeting-copilot-token": TOKEN}
    client = TestClient(create_app())

    def circuit(
        state="closed",
        reason="realtime_provider_circuit_closed",
        failure_count=0,
        last_failure_class=None,
        identity_generation=1,
    ):
        return {
            "state": state,
            "reason": reason,
            "failure_count": failure_count,
            "retry_after_ms": 0,
            "half_open": state == "half_open",
            "identity_generation": identity_generation,
            "last_failure_class": last_failure_class,
        }

    assert client.get("/providers/status", headers=headers).json() == {
        "configured": False,
        "runtime_synced": False,
        "probe_status": "not_run",
        "model": None,
        "realtime_model": None,
        "realtime_model_source": "not_configured",
        "realtime_model_explicit": False,
        "realtime_model_warning": None,
        "correction_model": None,
        "correction_model_source": "not_configured",
        "correction_model_explicit": False,
        "correction_model_warning": None,
        "operational": None,
        "realtime_ready": None,
        "realtime_probe_ready": None,
        "realtime_readiness_reason": "probe_not_ready",
        "probe_latency_ms": None,
        "probe_usage": None,
        "realtime_cutoff_ms": 2500,
        "realtime_circuit": circuit(identity_generation=1),
    }

    configured = client.put(
        "/desktop/provider/config",
        headers=headers,
        json={
            "base_url": "https://relay.example",
            "api_key": "sk-test-secret",
            "model": "gpt-first",
            "realtime_model": "gpt-first-fast",
        },
    )
    assert configured.status_code == 200
    assert client.get("/providers/status", headers=headers).json() == {
        "configured": True,
        "runtime_synced": True,
        "probe_status": "not_run",
        "model": "gpt-first",
        "realtime_model": "gpt-first-fast",
        "realtime_model_source": "runtime_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "gpt-first",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "operational": None,
        "realtime_ready": None,
        "realtime_probe_ready": None,
        "realtime_readiness_reason": "probe_not_ready",
        "probe_latency_ms": None,
        "probe_usage": None,
        "realtime_cutoff_ms": 2500,
        "realtime_circuit": circuit(identity_generation=2),
    }

    probed_models: list[str] = []

    def probe(config):
        probed_models.append(config.model)
        return {
            "operational": True,
            "provider": config.provider_label,
            "model": config.model,
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    monkeypatch.setattr(
        llm_service,
        "probe_gateway",
        probe,
    )
    probed = client.post(
        "/providers/llm/probe",
        headers={
            **headers,
            "X-Meeting-Copilot-Verification": "1",
        },
    )
    assert probed.status_code == 200
    assert probed.json()["model"] == "gpt-first-fast"
    assert probed_models == ["gpt-first-fast"]
    expected_connected = {
        "configured": True,
        "runtime_synced": True,
        "probe_status": "succeeded",
        "model": "gpt-first",
        "realtime_model": "gpt-first-fast",
        "realtime_model_source": "runtime_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "gpt-first",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "operational": True,
        "realtime_ready": True,
        "realtime_probe_ready": True,
        "realtime_readiness_reason": "probe_ready",
        "probe_latency_ms": 1,
        "probe_usage": {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        },
        "realtime_cutoff_ms": 2500,
        "realtime_circuit": circuit(identity_generation=3),
    }
    assert client.get("/providers/status", headers=headers).json() == expected_connected
    second_app_status = TestClient(create_app()).get("/providers/status", headers=headers).json()
    # The circuit generation is process-local in the in-memory test app; all
    # provider-facing fields remain stable across app refresh.
    assert second_app_status == {
        **expected_connected,
        "realtime_circuit": circuit(identity_generation=1),
    }

    switched = client.put(
        "/desktop/provider/config",
        headers=headers,
        json={
            "base_url": "https://relay.example",
            "api_key": "sk-test-secret",
            "model": "gpt-second",
            "realtime_model": "gpt-second-fast",
        },
    )
    assert switched.status_code == 200
    assert client.get("/providers/status", headers=headers).json() == {
        "configured": True,
        "runtime_synced": True,
        "probe_status": "not_run",
        "model": "gpt-second",
        "realtime_model": "gpt-second-fast",
        "realtime_model_source": "runtime_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "gpt-second",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "operational": None,
        "realtime_ready": None,
        "realtime_probe_ready": None,
        "realtime_readiness_reason": "probe_not_ready",
        "probe_latency_ms": None,
        "probe_usage": None,
        "realtime_cutoff_ms": 2500,
        "realtime_circuit": circuit(identity_generation=4),
    }

    def fail_probe(config):
        raise ValueError(f"provider rejected model {config.model}")

    monkeypatch.setattr(llm_service, "probe_gateway", fail_probe)
    failed_probe = client.post(
        "/providers/llm/probe",
        headers={
            **headers,
            "X-Meeting-Copilot-Verification": "1",
        },
    )
    assert failed_probe.status_code == 502
    assert client.get("/providers/status", headers=headers).json() == {
        "configured": True,
        "runtime_synced": True,
        "probe_status": "failed",
        "model": "gpt-second",
        "realtime_model": "gpt-second-fast",
        "realtime_model_source": "runtime_realtime_model",
        "realtime_model_explicit": True,
        "realtime_model_warning": None,
        "correction_model": "gpt-second",
        "correction_model_source": "general_model_fallback",
        "correction_model_explicit": False,
        "correction_model_warning": "correction_model_inherits_general_model",
        "operational": False,
        "realtime_ready": False,
        "realtime_probe_ready": False,
        "realtime_readiness_reason": "probe_not_ready",
        "probe_latency_ms": None,
        "probe_usage": None,
        "realtime_cutoff_ms": 2500,
        # A generic ValueError is classified as non-retryable and must
        # not poison the transport circuit.
        "realtime_circuit": circuit(identity_generation=4),
    }

    llm_service.clear_runtime_config()

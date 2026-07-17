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
        "LLM_GATEWAY_PROVIDER_LABEL",
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
    )
    configured = llm_service.LlmConfig.from_env()

    assert metadata == {
        "provider": "openai_compatible_gateway",
        "model": "runtime-model",
        "is_mock": False,
        "configured_from_env": True,
    }
    assert configured is not None
    assert configured.base_url == "https://relay.example"
    assert configured.api_key == "sk-runtime-secret"
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
    client = TestClient(create_app())
    headers = {"x-meeting-copilot-token": TOKEN}

    response = client.put(
        "/desktop/provider/config",
        headers=headers,
        json={
            "base_url": "https://relay.example/v1-root",
            "api_key": "sk-never-echo-this",
            "model": "gpt-test",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["runtime_override"] is True
    assert body["model"] == "gpt-test"
    assert "never-echo" not in response.text
    status = client.get("/desktop/provider/config", headers=headers)
    assert status.status_code == 200
    assert status.json()["runtime_override"] is True

    deleted = client.delete("/desktop/provider/config", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["configured"] is False
    assert not llm_service.runtime_configured()

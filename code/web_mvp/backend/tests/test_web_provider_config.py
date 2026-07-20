from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


def _clear_env(monkeypatch) -> None:
    for name in (
        "LLM_GATEWAY_BASE_URL",
        "LLM_GATEWAY_API_KEY",
        "LLM_GATEWAY_MODEL",
        "LLM_GATEWAY_REALTIME_MODEL",
        "LLM_GATEWAY_PROVIDER_LABEL",
        "LLM_GATEWAY_API_STYLE",
        "MEETING_COPILOT_DESKTOP_RUNTIME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_service, "load_dotenv", lambda *args, **kwargs: False)
    llm_service.clear_runtime_config()


def test_web_provider_config_is_local_persistent_and_secret_free_in_response(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    secret = "sk-web-provider-secret"

    with TestClient(create_app(data_dir=data_dir)) as client:
        initial = client.get("/providers/config")
        assert initial.status_code == 200
        assert initial.json()["configured"] is False
        assert initial.json()["api_key_present"] is False

        saved = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "api_key": secret,
                "model": "gpt-fast",
                "realtime_model": "gpt-fast-realtime",
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["command_status"] == "ok"
        assert body["configured"] is True
        assert body["api_key_present"] is True
        assert body["model"] == "gpt-fast"
        assert secret not in saved.text

        config_path = data_dir / "settings" / "provider.json"
        assert config_path.is_file()
        assert stat_mode(config_path) == 0o600
        assert json.loads(config_path.read_text(encoding="utf-8"))["api_key"] == secret

    llm_service.clear_runtime_config()
    with TestClient(create_app(data_dir=data_dir)) as client:
        restored = client.get("/providers/config")
        assert restored.status_code == 200
        assert restored.json()["configured"] is True
        assert restored.json()["base_url"] == "https://relay.example"
        assert secret not in restored.text


def test_web_provider_config_requires_a_key_for_first_save(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.put(
            "/providers/config",
            json={"base_url": "https://relay.example", "model": "gpt-test"},
        )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "api_key_required"


def test_web_provider_config_clear_removes_local_secret_and_runtime(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    with TestClient(create_app(data_dir=data_dir)) as client:
        saved = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "api_key": "sk-clear-me",
                "model": "gpt-test",
            },
        )
        assert saved.status_code == 200
        cleared = client.delete("/providers/config")
        assert cleared.status_code == 200
        assert cleared.json()["configured"] is False

    assert not (data_dir / "settings" / "provider.json").exists()
    assert not llm_service.runtime_configured()


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777

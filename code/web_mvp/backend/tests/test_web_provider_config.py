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
        "LLM_GATEWAY_CORRECTION_MODEL",
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
        if os.name != "nt":
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


def test_web_provider_config_repairs_metadata_only_record_when_key_is_supplied(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    settings = tmp_path / "data" / "settings"
    settings.mkdir(parents=True)
    (settings / "provider.json").write_text(
        json.dumps({
            "schema_version": "meeting_copilot.provider_config.v1",
            "base_url": "https://relay.example",
            "model": "gpt-old",
            "api_style": "chat_completions",
        }),
        encoding="utf-8",
    )

    with TestClient(create_app(data_dir=tmp_path / "data")) as client:
        response = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example/v1",
                "api_key": "sk-repair-test",
                "model": "gpt-repaired",
            },
        )

    assert response.status_code == 200
    assert response.json()["configured"] is True
    stored = json.loads((settings / "provider.json").read_text(encoding="utf-8"))
    assert stored["api_key"] == "sk-repair-test"


def test_web_provider_config_resumes_provider_paused_jobs(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    app = create_app(data_dir=tmp_path / "data")

    class ResumeSpy:
        calls = 0

        def resume(self) -> None:
            self.calls += 1

    resume_spy = ResumeSpy()
    with TestClient(app) as client:
        app.state.v2_executor = resume_spy
        response = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "api_key": "sk-provider-resume-test",
                "model": "gpt-test",
            },
        )

    assert response.status_code == 200
    assert resume_spy.calls == 1
    llm_service.clear_runtime_config()


def test_web_provider_config_persists_explicit_correction_model_and_provenance(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    secret = "sk-correction-config-test"

    with TestClient(create_app(data_dir=data_dir)) as client:
        saved = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "api_key": secret,
                "model": "gpt-5.5",
                "realtime_model": "gpt-5.4-mini",
                "correction_model": "gpt-5.4-mini",
            },
        )
        assert saved.status_code == 200
        body = saved.json()
        assert body["correction_model"] == "gpt-5.4-mini"
        assert body["correction_model_source"] == "runtime_correction_model"
        assert body["correction_model_explicit"] is True
        assert body["correction_model_warning"] is None
        assert secret not in saved.text

    stored = json.loads(
        (data_dir / "settings" / "provider.json").read_text(encoding="utf-8")
    )
    assert stored["correction_model"] == "gpt-5.4-mini"
    assert stored["correction_model_source"] == "runtime_correction_model"

    llm_service.clear_runtime_config()
    with TestClient(create_app(data_dir=data_dir)) as client:
        restored = client.get("/providers/config")
        health = client.get("/providers/health")
    assert restored.status_code == 200
    assert restored.json()["correction_model"] == "gpt-5.4-mini"
    assert restored.json()["correction_model_source"] == "runtime_correction_model"
    assert restored.json()["correction_model_explicit"] is True
    assert health.json()["llm"]["correction_model"] == "gpt-5.4-mini"
    assert health.json()["llm"]["correction_model_source"] == "runtime_correction_model"
    assert secret not in restored.text
    assert secret not in health.text
    llm_service.clear_runtime_config()


def test_web_provider_config_legacy_update_preserves_explicit_correction_model(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    with TestClient(create_app(data_dir=data_dir)) as client:
        first = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "api_key": "sk-legacy-preserve-test",
                "model": "gpt-5.5",
                "correction_model": "gpt-5.4-mini",
            },
        )
        assert first.status_code == 200
        # Simulate an older client that predates correction_model entirely.
        updated = client.put(
            "/providers/config",
            json={
                "base_url": "https://relay.example",
                "model": "gpt-5.5-updated",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["correction_model"] == "gpt-5.4-mini"
        assert updated.json()["correction_model_source"] == "runtime_correction_model"
    llm_service.clear_runtime_config()


def test_web_provider_config_preserves_realtime_model_fallback_provenance(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    try:
        with TestClient(create_app(data_dir=data_dir)) as client:
            saved = client.put(
                "/providers/config",
                json={
                    "base_url": "https://relay.example",
                    "api_key": "sk-fallback-test",
                    "model": "general-model",
                },
            )
            assert saved.status_code == 200
            assert saved.json()["realtime_model"] == "general-model"
            assert saved.json()["realtime_model_source"] == "general_model_fallback"
            assert saved.json()["realtime_model_explicit"] is False
            assert (
                saved.json()["realtime_model_warning"]
                == "realtime_model_inherits_general_model"
            )

        stored = json.loads(
            (data_dir / "settings" / "provider.json").read_text(encoding="utf-8")
        )
        assert stored["realtime_model"] is None
        assert stored["realtime_model_source"] == "general_model_fallback"

        llm_service.clear_runtime_config()
        with TestClient(create_app(data_dir=data_dir)) as client:
            restored = client.get("/providers/config")
            health = client.get("/providers/health")
            status = client.get("/providers/status")

        assert restored.status_code == 200
        assert restored.json()["realtime_model_source"] == "general_model_fallback"
        assert restored.json()["realtime_model_explicit"] is False
        assert health.status_code == 200
        assert health.json()["llm"]["realtime_model_source"] == "general_model_fallback"
        assert health.json()["llm"]["realtime_model_explicit"] is False
        assert status.status_code == 200
        assert status.json()["realtime_model_source"] == "general_model_fallback"
        assert status.json()["realtime_model_explicit"] is False
        assert (
            status.json()["realtime_model_warning"]
            == "realtime_model_inherits_general_model"
        )
    finally:
        llm_service.clear_runtime_config()


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

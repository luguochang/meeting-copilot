from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.app import create_app


PROBE_HEADERS = {"X-Meeting-Copilot-Verification": "1"}


def test_llm_provider_probe_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.delenv("LLM_GATEWAY_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_GATEWAY_API_KEY", raising=False)
    monkeypatch.setattr(llm_service, "REPO_ENV_FILE", "missing.env")

    response = TestClient(create_app()).post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "llm_not_configured"


def test_llm_provider_probe_uses_running_service_configuration(monkeypatch):
    calls = []

    class Client:
        def post_json(self, url, headers, body, timeout):
            calls.append((url, headers, body, timeout))
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: Client())

    client = TestClient(create_app())
    response = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "operational": True,
        "provider": "openai_compatible_gateway",
        "model": "probe-model",
        "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
    }
    assert calls[0][0] == "https://gateway.example/v1/chat/completions"
    assert calls[0][2]["reasoning_effort"] == "low"
    assert calls[0][2]["max_completion_tokens"] == 16
    assert "max_tokens" not in calls[0][2]
    assert b"sk-probe-secret" not in response.content
    stats = client.get("/settings/cost-stats").json()
    assert any(item["purpose"] == "provider_probe" for item in stats["breakdown"])


def test_llm_provider_probe_rejects_mock_without_calling_provider(monkeypatch):
    calls = 0

    class NeverCalledClient:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("mock provider must not be probed")

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.setenv("LLM_GATEWAY_IS_MOCK", "true")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: NeverCalledClient())

    response = TestClient(create_app()).post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "mock_llm_not_accepted"
    assert calls == 0


def test_llm_provider_probe_returns_safe_gateway_failure(monkeypatch):
    class FailingClient:
        def post_json(self, *_args, **_kwargs):
            raise RuntimeError("secret upstream response")

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: FailingClient())

    response = TestClient(create_app()).post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 502
    body = response.json()
    assert body["detail"]["error"] == "llm_probe_failed"
    assert body["detail"]["error_type"] == "RuntimeError"
    assert b"secret upstream response" not in response.content
    assert b"sk-probe-secret" not in response.content


def test_llm_provider_probe_fails_closed_when_usage_metadata_is_missing(monkeypatch):
    class MissingUsageClient:
        def post_json(self, *_args, **_kwargs):
            return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: MissingUsageClient())
    client = TestClient(create_app())

    response = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "llm_probe_failed"
    assert client.get("/settings/cost-stats").json()["breakdown"] == []


def test_llm_provider_probe_rejects_inconsistent_usage_metadata(monkeypatch):
    class InconsistentUsageClient:
        def post_json(self, *_args, **_kwargs):
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 1},
            }

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: InconsistentUsageClient())
    client = TestClient(create_app())

    response = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 502
    assert client.get("/settings/cost-stats").json()["breakdown"] == []


def test_llm_provider_probe_rejects_cross_site_and_missing_verification_header(monkeypatch):
    calls = 0

    class NeverCalledClient:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("unauthorized probe must not call provider")

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: NeverCalledClient())
    client = TestClient(create_app())

    missing_header = client.post("/providers/llm/probe")
    hostile_origin = client.post(
        "/providers/llm/probe",
        headers={**PROBE_HEADERS, "Origin": "https://attacker.example"},
    )

    assert missing_header.status_code == 403
    assert hostile_origin.status_code == 403
    assert calls == 0


def test_llm_provider_probe_reuses_short_lived_success_without_second_paid_call(monkeypatch):
    calls = 0

    class Client:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: Client())
    client = TestClient(create_app())

    first = client.post("/providers/llm/probe", headers=PROBE_HEADERS)
    second = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert calls == 1


def test_llm_provider_probe_checks_budget_before_warm_success_cache(monkeypatch):
    calls = 0

    class Client:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://warm-cache-budget.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "warm-cache-budget-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: Client())
    client = TestClient(create_app())

    settings = client.get("/settings").json()
    settings["budget"].update({"session_limit_cny": 10.0, "daily_limit_cny": 50.0})
    assert client.patch("/settings", json=settings).status_code == 200

    first = client.post("/providers/llm/probe", headers=PROBE_HEADERS)
    assert first.status_code == 200

    settings = client.get("/settings").json()
    settings["budget"].update({"session_limit_cny": 0.0, "daily_limit_cny": 50.0})
    assert client.patch("/settings", json=settings).status_code == 200

    second = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert second.status_code == 429
    assert second.json()["detail"]["error"] == "llm_budget_exceeded"
    assert second.json()["detail"]["scope"] == "session"
    assert calls == 1


def test_llm_provider_probe_route_single_flight_is_not_masked_by_success_cache(monkeypatch):
    provider_entered = Event()
    release_provider = Event()
    calls = 0

    class BlockingClient:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            provider_entered.set()
            assert release_provider.wait(timeout=3)
            return {
                "choices": [{"message": {"content": "OK"}}],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.delenv("LLM_PROMPT_CNY_PER_1M_TOKENS", raising=False)
    monkeypatch.delenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", raising=False)
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: BlockingClient())
    app = create_app()
    first_client = TestClient(app)
    second_client = TestClient(app)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(
            first_client.post,
            "/providers/llm/probe",
            headers=PROBE_HEADERS,
        )
        assert provider_entered.wait(timeout=2)
        second_future = pool.submit(
            second_client.post,
            "/providers/llm/probe",
            headers=PROBE_HEADERS,
        )
        second = second_future.result(timeout=2)
        assert not first_future.done()
        release_provider.set()
        first = first_future.result(timeout=3)

    assert first.status_code == 200
    assert first.json().get("cached") is not True
    assert second.status_code == 409
    assert second.json()["detail"]["error"] == "llm_probe_in_flight"
    assert calls == 1


@pytest.mark.parametrize(
    ("usage_session_id", "budget", "expected_scope"),
    [
        (
            "provider_probe",
            {"session_limit_cny": 1.0, "daily_limit_cny": 50.0},
            "session",
        ),
        (
            "another_session_today",
            {"session_limit_cny": 10.0, "daily_limit_cny": 1.0},
            "daily",
        ),
    ],
    ids=["session", "daily"],
)
def test_llm_provider_probe_budget_gate_blocks_before_provider_call(
    monkeypatch,
    usage_session_id,
    budget,
    expected_scope,
):
    calls = 0

    class NeverCalledClient:
        def post_json(self, *_args, **_kwargs):
            nonlocal calls
            calls += 1
            raise AssertionError("exhausted probe budget must block provider")

    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-probe-secret")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "probe-model")
    monkeypatch.delenv("LLM_GATEWAY_IS_MOCK", raising=False)
    monkeypatch.setenv("LLM_PROMPT_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setenv("LLM_COMPLETION_CNY_PER_1M_TOKENS", "100000")
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: NeverCalledClient())
    app = create_app()
    app.state.settings_usage_repository.record_usage(
        session_id=usage_session_id,
        purpose="existing_usage",
        provider="test_provider",
        model="test_model",
        prompt_tokens=10,
        completion_tokens=0,
        total_tokens=10,
        timestamp_ms=int(time.time() * 1_000),
    )
    client = TestClient(app)
    settings = client.get("/settings").json()
    settings["budget"].update(budget)
    assert client.patch("/settings", json=settings).status_code == 200

    response = client.post("/providers/llm/probe", headers=PROBE_HEADERS)

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "error": "llm_budget_exceeded",
        "scope": expected_scope,
        "purpose": "provider_probe",
        "currentEstimatedCostCny": pytest.approx(1.0),
        "limitCny": 1.0,
        "costStatus": "estimated",
    }
    assert calls == 0

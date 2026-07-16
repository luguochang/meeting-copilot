import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "验证测试.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("meeting_copilot_root_verify", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_verification_script_resolves_the_repository_root():
    script = _load_script()

    assert script.PROJECT_ROOT == REPO_ROOT
    assert script.WEB_BACKEND_ROOT == REPO_ROOT / "code" / "web_mvp" / "backend"
    assert script.CORE_ROOT == REPO_ROOT / "code" / "core"


def test_sqlite_repository_check_uses_a_data_directory():
    script = _load_script()

    assert script.test_sqlite_repository() is True


def test_api_check_fails_closed_when_service_is_unreachable(monkeypatch):
    script = _load_script()

    def connection_failed(*_args, **_kwargs):
        raise script.httpx.RequestError("offline")

    monkeypatch.setattr(script.httpx, "get", connection_failed)

    assert script.test_api_endpoints("http://127.0.0.1:65530") is False


def test_api_check_fails_closed_on_non_200(monkeypatch):
    script = _load_script()

    class Response:
        status_code = 503

        def json(self):
            return {"status": "degraded"}

    monkeypatch.setattr(script.httpx, "get", lambda *_args, **_kwargs: Response())

    assert script.test_api_endpoints("http://127.0.0.1:8765") is False


def test_api_check_fails_closed_when_required_providers_are_unavailable(monkeypatch):
    script = _load_script()

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    responses = {
        "/health": {"status": "ok"},
        "/degradation/status": {"level": 0},
        "/providers/health": {
            "llm": {"credential_configured": False, "is_mock": False},
            "asr": {"file_asr_available": False, "realtime_asr_available": False},
        },
    }

    def fake_get(url, **_kwargs):
        path = "/" + url.split("/", 3)[-1]
        return Response(responses[path])

    monkeypatch.setattr(script.httpx, "get", fake_get)

    assert script.test_api_endpoints("http://127.0.0.1:8765") is False


def test_api_check_rejects_malformed_provider_flags(monkeypatch):
    script = _load_script()

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    responses = {
        "/health": {"status": "ok"},
        "/degradation/status": {"level": 0},
        "/providers/health": {
            "llm": {"credential_configured": "false"},
            "asr": {
                "file_asr_available": "false",
                "realtime_asr_available": "false",
            },
        },
    }

    monkeypatch.setattr(
        script.httpx,
        "get",
        lambda url, **_kwargs: Response(responses["/" + url.split("/", 3)[-1]]),
    )

    assert script.test_api_endpoints("http://127.0.0.1:8765") is False


def test_llm_gateway_probe_requires_running_service_success(monkeypatch):
    script = _load_script()
    calls = []

    class Response:
        status_code = 200

        def json(self):
            return {"operational": True, "provider": "gateway", "model": "m", "usage": {}}

    def fake_post(url, headers, timeout):
        calls.append((url, headers, timeout))
        return Response()

    monkeypatch.setattr(script.httpx, "post", fake_post)

    assert script.test_llm_gateway_probe("http://127.0.0.1:8765") is True
    assert calls == [(
        "http://127.0.0.1:8765/providers/llm/probe",
        {"X-Meeting-Copilot-Verification": "1"},
        20,
    )]


def test_llm_gateway_probe_fails_closed_without_calling_mock_provider(monkeypatch):
    script = _load_script()

    class Response:
        status_code = 409

        def json(self):
            return {"detail": {"error": "mock_llm_not_accepted"}}

    monkeypatch.setattr(script.httpx, "post", lambda *_args, **_kwargs: Response())

    assert script.test_llm_gateway_probe("http://127.0.0.1:8765") is False


def test_verification_checks_do_not_depend_on_python_assert_statements():
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "assert " not in source


def test_api_check_rejects_non_integer_degradation_level(monkeypatch):
    script = _load_script()

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    responses = {
        "/health": {"status": "ok"},
        "/degradation/status": {"level": "0"},
        "/providers/health": {
            "llm": {"credential_configured": True, "is_mock": False},
            "asr": {"file_asr_available": True, "realtime_asr_available": True},
        },
    }
    monkeypatch.setattr(
        script.httpx,
        "get",
        lambda url, **_kwargs: Response(responses["/" + url.split("/", 3)[-1]]),
    )

    assert script.test_api_endpoints("http://127.0.0.1:8765") is False

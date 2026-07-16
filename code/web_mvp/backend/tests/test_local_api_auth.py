from __future__ import annotations

from fastapi import WebSocket
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.local_api_auth import health_proof


TOKEN_A = "a" * 64
TOKEN_B = "b" * 64


def _authenticated_app(monkeypatch, tmp_path, token: str = TOKEN_A):
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", token)
    return create_app(data_dir=tmp_path)


def test_packaged_health_exposes_only_expected_instance_proof(monkeypatch, tmp_path):
    client = TestClient(_authenticated_app(monkeypatch, tmp_path))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "meeting-copilot-web-mvp",
        "instance_proof": health_proof(TOKEN_A),
    }
    assert TOKEN_A not in response.text


def test_bootstrap_sets_http_only_strict_cookie_and_unlocks_local_api(monkeypatch, tmp_path):
    client = TestClient(_authenticated_app(monkeypatch, tmp_path))

    assert client.get("/v2/meetings").status_code == 403
    bootstrap = client.get(
        f"/desktop/bootstrap?token={TOKEN_A}",
        follow_redirects=False,
    )

    assert bootstrap.status_code == 303
    assert bootstrap.headers["location"] == "/workbench"
    cookie = bootstrap.headers["set-cookie"]
    assert "meeting_copilot_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert client.get("/v2/meetings").status_code == 200
    workbench = client.get("/workbench")
    assert workbench.status_code == 200
    assert "default-src 'self'" in workbench.headers["content-security-policy"]


def test_bootstrap_rejects_wrong_token_and_open_redirect(monkeypatch, tmp_path):
    client = TestClient(_authenticated_app(monkeypatch, tmp_path))

    assert client.get(f"/desktop/bootstrap?token={TOKEN_B}", follow_redirects=False).status_code == 403
    assert client.get(
        f"/desktop/bootstrap?token={TOKEN_A}&next=https://attacker.example",
        follow_redirects=False,
    ).status_code == 422


def test_authenticated_cookie_rejects_hostile_origin_and_expires_after_restart(monkeypatch, tmp_path):
    first = TestClient(_authenticated_app(monkeypatch, tmp_path / "first", TOKEN_A))
    assert first.get(f"/desktop/bootstrap?token={TOKEN_A}", follow_redirects=False).status_code == 303
    assert first.get("/v2/meetings", headers={"Origin": "https://attacker.example"}).status_code == 403

    old_cookie = first.cookies.get("meeting_copilot_session")
    second = TestClient(_authenticated_app(monkeypatch, tmp_path / "second", TOKEN_B))
    second.cookies.set("meeting_copilot_session", old_cookie)

    assert second.get("/v2/meetings").status_code == 403


def test_websocket_requires_the_authenticated_session_cookie(monkeypatch, tmp_path):
    app = _authenticated_app(monkeypatch, tmp_path)

    @app.websocket("/auth-test-ws")
    async def auth_test_ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    anonymous = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as denied:
        with anonymous.websocket_connect("/auth-test-ws"):
            pass
    assert denied.value.code == 4403

    authenticated = TestClient(app)
    assert authenticated.get(f"/desktop/bootstrap?token={TOKEN_A}", follow_redirects=False).status_code == 303
    with authenticated.websocket_connect("/auth-test-ws") as websocket:
        assert websocket.receive_text() == "ok"


def test_development_mode_without_token_preserves_existing_local_contract(monkeypatch):
    monkeypatch.delenv("MEETING_COPILOT_LOCAL_API_TOKEN", raising=False)
    client = TestClient(create_app())

    assert client.get("/health").json() == {
        "status": "ok",
        "service": "meeting-copilot-web-mvp",
    }
    assert client.get("/settings").status_code == 200

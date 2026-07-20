from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app


@pytest.fixture
def meeting_api(tmp_path) -> Iterator[tuple[TestClient, object]]:
    app = create_app(data_dir=tmp_path)
    try:
        yield TestClient(app), app
    finally:
        app.state.v2_persistence.close()


def _seed_meeting(app, meeting_id: str = "meeting-title") -> dict:
    return app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id,
        title=None,
        now_ms=1_721_257_200_000,
    )


def test_patch_v2_meeting_returns_latest_meeting_and_snapshot(meeting_api):
    client, app = meeting_api
    created = _seed_meeting(app)

    response = client.patch(
        "/v2/meetings/meeting-title",
        json={"title": "   支付服务发布评审   "},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meeting"]["title"] == "支付服务发布评审"
    assert body["meeting"]["title_source"] == "user"
    assert body["meeting"]["updated_at_ms"] >= created["updated_at_ms"]
    assert body["snapshot"]["meeting_id"] == "meeting-title"
    assert body["snapshot"]["title"] == "支付服务发布评审"
    assert body["snapshot"]["title_source"] == "user"
    assert body["snapshot"]["updated_at_ms"] == body["meeting"]["updated_at_ms"]

    refreshed = client.get("/v2/meetings/meeting-title/snapshot")
    assert refreshed.status_code == 200
    assert refreshed.json()["title"] == "支付服务发布评审"
    assert refreshed.json()["title_source"] == "user"
    assert refreshed.json()["updated_at_ms"] == body["meeting"]["updated_at_ms"]


def test_patch_v2_meeting_accepts_title_at_length_limit(meeting_api):
    client, app = meeting_api
    _seed_meeting(app)

    response = client.patch(
        "/v2/meetings/meeting-title",
        json={"title": f"  {'会' * 200}  "},
    )

    assert response.status_code == 200
    assert response.json()["meeting"]["title"] == "会" * 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": None},
        {"title": 42},
        {"title": "   "},
        {"title": "会" * 201},
        {"title": "季度/路径"},
        {"title": "季度\\路径"},
    ],
)
def test_patch_v2_meeting_rejects_invalid_titles(meeting_api, payload):
    client, app = meeting_api
    _seed_meeting(app)

    response = client.patch("/v2/meetings/meeting-title", json=payload)

    assert response.status_code == 422


def test_patch_v2_meeting_returns_not_found_for_unknown_meeting(meeting_api):
    client, _app = meeting_api

    response = client.patch(
        "/v2/meetings/missing-title",
        json={"title": "不存在"},
    )

    assert response.status_code == 404

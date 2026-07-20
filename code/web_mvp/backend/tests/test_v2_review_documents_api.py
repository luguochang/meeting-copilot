from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app


def _seed_meeting(app, meeting_id: str) -> None:
    app.state.v2_persistence.create_meeting(
        meeting_id=meeting_id,
        title="发布复盘",
        now_ms=1_000,
    )
    app.state.commit_v2_final(
        meeting_id,
        {
            "event_type": "final",
            "segment_id": "segment-1",
            "text": "确认发布窗口和回滚负责人。",
            "normalized_text": "确认发布窗口和回滚负责人。",
            "start_ms": 100,
            "end_ms": 900,
        },
    )


def test_review_document_save_snapshot_history_and_conflict(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_meeting(app, "meeting-docs")

    saved = client.patch(
        "/v2/meetings/meeting-docs/documents/minutes",
        json={"expected_revision": 0, "content_json": {"markdown": "# 用户最终复盘"}},
    )

    assert saved.status_code == 200
    document = saved.json()["document"]
    assert document["revision"] == 1
    assert document["user_final"]["content"] == {"markdown": "# 用户最终复盘"}
    snapshot = client.get("/v2/meetings/meeting-docs/snapshot").json()
    assert snapshot["documents"]["minutes"]["user_final"]["modified"] is True

    revisions = client.get(
        "/v2/meetings/meeting-docs/documents/minutes/revisions"
    )
    assert revisions.status_code == 200
    assert revisions.json()["revisions"][0]["version_kind"] == "user_final"

    conflict = client.patch(
        "/v2/meetings/meeting-docs/documents/minutes",
        json={"expected_revision": 0, "content_json": {"markdown": "覆盖用户稿"}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_revision"] == 1
    app.state.v2_persistence.close()


def test_meeting_title_patch_persists_user_source_and_validates_title(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_meeting(app, "meeting-title")

    updated = client.patch("/v2/meetings/meeting-title", json={"title": "  架构评审  "})

    assert updated.status_code == 200
    assert updated.json()["meeting"]["title"] == "架构评审"
    assert updated.json()["meeting"]["title_source"] == "user"
    assert client.get("/v2/meetings/meeting-title/snapshot").json()["title"] == "架构评审"

    invalid = client.patch("/v2/meetings/meeting-title", json={"title": "   "})
    assert invalid.status_code == 422
    missing = client.patch("/v2/meetings/missing-title", json={"title": "不存在"})
    assert missing.status_code == 404
    app.state.v2_persistence.close()


def test_review_jobs_can_retry_independently_without_overwriting_user_final(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    _seed_meeting(app, "meeting-retry")
    saved = client.patch(
        "/v2/meetings/meeting-retry/documents/minutes",
        json={"expected_revision": 0, "content_json": {"markdown": "# 人工版本"}},
    ).json()["document"]

    minutes = client.post("/v2/meetings/meeting-retry/jobs/minutes/retry", json={})
    approach = client.post("/v2/meetings/meeting-retry/jobs/approach/retry", json={})
    index = client.post("/v2/meetings/meeting-retry/jobs/index/retry", json={})

    assert minutes.status_code == 200
    assert approach.status_code == 200
    assert index.status_code == 200
    assert {minutes.json()["job"]["kind"], approach.json()["job"]["kind"], index.json()["job"]["kind"]} == {
        "minutes",
        "approach",
        "index",
    }
    regenerated = client.post(
        "/v2/meetings/meeting-retry/documents/minutes/regenerate",
        json={"preserve_user_final": True},
    )
    assert regenerated.status_code == 200
    current = client.get("/v2/meetings/meeting-retry/snapshot").json()["documents"]["minutes"]
    assert current["revision"] == saved["revision"]
    assert current["user_final"]["content"] == {"markdown": "# 人工版本"}
    app.state.v2_persistence.close()

from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import create_app


def _seed_governed_meeting(app, tmp_path, meeting_id: str) -> None:
    persistence = app.state.v2_persistence
    persistence.create_meeting(meeting_id=meeting_id, title="治理测试", now_ms=1_000)
    app.state.commit_v2_final(
        meeting_id,
        {
            "event_type": "final",
            "segment_id": "segment-1",
            "text": "确认发布负责人。",
            "normalized_text": "确认发布负责人。",
            "start_ms": 0,
            "end_ms": 1_000,
        },
    )
    persistence.end_meeting(meeting_id=meeting_id, now_ms=2_000)
    app.state.asr_live_repository.create(
        {
            "session_id": meeting_id,
            "source": "live_asr_stream",
            "trace_kind": "live_event",
            "provider": "funasr_realtime",
            "events": [
                {"event_type": "transcript_final", "payload": {"text": "确认发布负责人。"}},
                {"event_type": "transcript_revision", "payload": {"text": "确认发布负责人。"}},
                {"event_type": "suggestion_card", "payload": {"text": "明确负责人"}},
            ],
            "suggestion_cards": [{"id": "suggestion-1", "text": "明确负责人"}],
            "minutes": {"minutes_md": "# 纪要"},
            "audio": {
                "saved": True,
                "relative_path": f"audio_assets/{meeting_id}/audio.wav",
            },
        }
    )
    audio_dir = tmp_path / "audio_assets" / meeting_id
    audio_dir.mkdir(parents=True)
    (audio_dir / "audio.wav").write_bytes(b"RIFF-governance")
    app.state.meeting_preparation_store.save(
        meeting_id,
        hotwords=["P99"],
        notice_acknowledged=True,
        updated_at_ms=2_000,
    )


def test_data_governance_settings_are_strict_persisted_and_audited(tmp_path) -> None:
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    default = client.get("/v2/data-governance/settings")
    updated = client.patch(
        "/v2/data-governance/settings",
        json={"retention_policy": "90_days"},
    )
    reloaded = client.get("/v2/data-governance/settings")
    invalid = client.patch(
        "/v2/data-governance/settings",
        json={"retention_policy": "30_days", "unexpected": True},
    )
    audit = client.get("/v2/data-governance/audit")

    assert default.status_code == 200
    assert default.json()["retention_policy"] == "local_until_user_deletes"
    assert updated.status_code == 200
    assert updated.json()["retention_policy"] == "90_days"
    assert reloaded.json()["retention_policy"] == "90_days"
    assert invalid.status_code == 422
    assert [event["event_type"] for event in audit.json()["events"]] == [
        "retention_policy.updated"
    ]
    for repository in app.state.sqlite_repositories:
        repository.close()


@pytest.mark.parametrize("scope", ["recording", "derived", "transcript"])
def test_scoped_deletion_removes_only_the_requested_local_data(tmp_path, scope) -> None:
    meeting_id = f"meeting-{scope}"
    app = create_app(data_dir=tmp_path)
    _seed_governed_meeting(app, tmp_path, meeting_id)
    client = TestClient(app)

    response = client.delete(f"/v2/meetings/{meeting_id}?scope={scope}")
    job_response = client.get(
        f"/v2/data-governance/deletions/{response.json()['deletion_job']['id']}"
    )
    audit_response = client.get(
        "/v2/data-governance/audit",
        params={"meeting_id": meeting_id},
    )

    assert response.status_code == 200
    assert response.json()["deletion_scope"] == scope
    assert response.json()["meeting_deleted"] is False
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"
    assert [event["event_type"] for event in audit_response.json()["events"]] == [
        "data_deletion.requested",
        "data_deletion.started",
        "data_deletion.completed",
    ]

    persistence = app.state.v2_persistence
    assert persistence.meeting_exists(meeting_id) is True
    segments = persistence.list_transcript_segments(meeting_id)["segments"]
    audio_exists = (tmp_path / "audio_assets" / meeting_id / "audio.wav").exists()
    legacy = app.state.asr_live_repository.get(meeting_id)
    if scope == "recording":
        assert segments
        assert audio_exists is False
        assert "audio" not in legacy
        assert not (tmp_path / "meeting_preparation" / f"{meeting_id}.json").exists()
    elif scope == "derived":
        assert segments
        assert audio_exists is True
        assert "suggestion_cards" not in legacy
        assert "minutes" not in legacy
        assert [event["event_type"] for event in legacy["events"]] == [
            "transcript_final"
        ]
        slo = client.get(f"/v2/meetings/{meeting_id}/realtime-ai-slo").json()
        assert sum(lane["cancelled_count"] for lane in slo["lanes"].values()) >= 2
    else:
        assert segments == []
        assert audio_exists is True
        assert legacy["events"] == []
        assert "minutes" not in legacy

    for repository in app.state.sqlite_repositories:
        repository.close()


def test_data_governance_requires_persistent_local_storage() -> None:
    client = TestClient(create_app())

    assert client.get("/v2/data-governance/settings").status_code == 503
    assert client.patch(
        "/v2/data-governance/settings",
        json={"retention_policy": "30_days"},
    ).status_code == 503

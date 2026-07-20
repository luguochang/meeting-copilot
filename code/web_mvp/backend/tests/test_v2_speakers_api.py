from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.app import create_app


@pytest.fixture
def speaker_api(tmp_path) -> Iterator[tuple[TestClient, object]]:
    app = create_app(data_dir=tmp_path)
    try:
        yield TestClient(app), app
    finally:
        app.state.v2_persistence.close()


def _commit_speaker(
    app,
    *,
    meeting_id: str,
    number: int,
    speaker_id: str,
    confidence: float = 0.91,
) -> None:
    app.state.commit_v2_final(
        meeting_id,
        {
            "event_type": "final",
            "segment_id": f"segment-{number}",
            "text": f"第 {number} 位发言人的会议内容。",
            "normalized_text": f"第 {number} 位发言人的会议内容。",
            "start_ms": number * 1_000,
            "end_ms": number * 1_000 + 600,
            "speaker_id": speaker_id,
            "speaker_confidence": confidence,
        },
    )


def test_speaker_api_lists_and_renames_durable_meeting_scoped_labels(speaker_api):
    client, app = speaker_api
    _commit_speaker(app, meeting_id="meeting-one", number=1, speaker_id="cluster-a")
    _commit_speaker(app, meeting_id="meeting-one", number=2, speaker_id="cluster-b")
    _commit_speaker(app, meeting_id="meeting-two", number=1, speaker_id="cluster-a")

    listed = client.get("/v2/meetings/meeting-one/speakers")

    assert listed.status_code == 200
    assert [speaker["speaker_label"] for speaker in listed.json()["speakers"]] == [
        "Speaker 1",
        "Speaker 2",
    ]

    renamed = client.patch(
        "/v2/meetings/meeting-one/speakers/cluster-a",
        json={"speaker_label": "  张工  "},
    )

    assert renamed.status_code == 200
    assert renamed.json()["speaker"]["speaker_label"] == "张工"
    refreshed = client.get("/v2/meetings/meeting-one/snapshot").json()
    assert refreshed["segments"][0]["speaker_label"] == "张工"
    assert refreshed["semantic_paragraphs"][0]["speaker_label"] == "张工"
    other_meeting = client.get("/v2/meetings/meeting-two/speakers").json()
    assert other_meeting["speakers"][0]["speaker_label"] == "Speaker 1"


def test_speaker_api_reports_missing_resources_and_label_conflicts(speaker_api):
    client, app = speaker_api
    _commit_speaker(app, meeting_id="meeting-conflict", number=1, speaker_id="cluster-a")
    _commit_speaker(app, meeting_id="meeting-conflict", number=2, speaker_id="cluster-b")

    assert client.get("/v2/meetings/missing/speakers").status_code == 404
    assert client.patch(
        "/v2/meetings/meeting-conflict/speakers/missing",
        json={"speaker_label": "王工"},
    ).status_code == 404

    conflict = client.patch(
        "/v2/meetings/meeting-conflict/speakers/cluster-b",
        json={"speaker_label": "Speaker 1"},
    )

    assert conflict.status_code == 409
    assert "already used" in str(conflict.json()["detail"])


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"speaker_label": None},
        {"speaker_label": 42},
        {"speaker_label": "   "},
        {"speaker_label": "x" * 81},
        {"speaker_label": "姓名\x00内容"},
    ],
)
def test_speaker_api_rejects_invalid_manual_labels(speaker_api, payload):
    client, app = speaker_api
    _commit_speaker(app, meeting_id="meeting-invalid", number=1, speaker_id="cluster-a")

    response = client.patch(
        "/v2/meetings/meeting-invalid/speakers/cluster-a",
        json=payload,
    )

    assert response.status_code == 422

from __future__ import annotations

import errno
import struct

from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.app import create_app
from meeting_copilot_web_mvp.audio_assets import RealtimeWavAssetWriter
from meeting_copilot_web_mvp.next006_failpoints import (
    InjectedStorageWriteError,
    storage_write_failpoint,
)


TOKEN = "b" * 64


@pytest.fixture(autouse=True)
def reset_failpoint():
    storage_write_failpoint.reset()
    yield
    storage_write_failpoint.reset()


def _desktop_client(monkeypatch, tmp_path, *, enabled: bool = True) -> TestClient:
    monkeypatch.setenv("MEETING_COPILOT_LOCAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("MEETING_COPILOT_DESKTOP_RUNTIME", "1")
    if enabled:
        monkeypatch.setenv("MEETING_COPILOT_ENABLE_NEXT006_FAILPOINTS", "1")
    else:
        monkeypatch.delenv("MEETING_COPILOT_ENABLE_NEXT006_FAILPOINTS", raising=False)
    return TestClient(create_app(data_dir=tmp_path))


def test_authenticated_sqlite_failpoint_fails_real_write_then_recovers(monkeypatch, tmp_path):
    client = _desktop_client(monkeypatch, tmp_path)
    headers = {"x-meeting-copilot-token": TOKEN}
    created = client.post(
        "/v2/meetings",
        headers=headers,
        json={"meeting_id": "next006_disk", "expected_duration_seconds": 30},
    )
    assert created.status_code == 201

    armed = client.put(
        "/desktop/test/failpoints/storage-write",
        headers=headers,
        json={
            "scope": "meeting_title_transaction",
            "failure": "enospc",
            "count": 1,
        },
    )
    assert armed.status_code == 200
    unrelated_write = client.post(
        "/v2/meetings",
        headers=headers,
        json={"meeting_id": "next006_unrelated", "expected_duration_seconds": 30},
    )
    assert unrelated_write.status_code == 201
    assert (
        client.get("/desktop/test/failpoints/storage-write", headers=headers).json()[
            "remaining"
        ]
        == 1
    )
    failed = client.patch(
        "/v2/meetings/next006_disk",
        headers=headers,
        json={"title": "must not persist"},
    )
    assert failed.status_code == 507
    assert failed.json()["detail"] == {
        "error": "storage_write_failed",
        "failure": "enospc",
        "scope": "meeting_title_transaction",
        "retryable": True,
    }

    unchanged = client.get("/v2/meetings/next006_disk/snapshot", headers=headers)
    assert unchanged.status_code == 200
    assert unchanged.json()["title"] != "must not persist"

    recovered = client.patch(
        "/v2/meetings/next006_disk",
        headers=headers,
        json={"title": "persisted after recovery"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["snapshot"]["title"] == "persisted after recovery"
    status = client.get("/desktop/test/failpoints/storage-write", headers=headers).json()
    assert status["hit_count"] == 1
    assert status["remaining"] == 0


def test_failpoint_endpoint_is_disabled_outside_explicit_desktop_test_runtime(monkeypatch, tmp_path):
    client = _desktop_client(monkeypatch, tmp_path, enabled=False)
    response = client.put(
        "/desktop/test/failpoints/storage-write",
        headers={"x-meeting-copilot-token": TOKEN},
        json={"scope": "sqlite_transaction", "failure": "enospc", "count": 1},
    )

    assert response.status_code == 403
    assert storage_write_failpoint.snapshot()["remaining"] == 0


def test_audio_chunk_failpoint_runs_before_actual_pcm_file_write(tmp_path):
    writer = RealtimeWavAssetWriter(
        data_dir=tmp_path,
        session_id="next006_audio_disk",
        source_type="next006_real_sut",
        sample_rate_hz=100,
        chunk_duration_seconds=0.1,
    )
    storage_write_failpoint.arm(scope="audio_chunk", failure="eio", count=1)
    payload = b"".join(struct.pack("<f", 0.1) for _ in range(10))

    with pytest.raises(InjectedStorageWriteError) as raised:
        writer.write_float32_pcm(payload)

    assert raised.value.errno == errno.EIO
    chunks = tmp_path / "audio_assets/next006_audio_disk/chunks"
    assert list(chunks.glob("*.pcm")) == []

    writer.write_float32_pcm(payload)
    assert len(list(chunks.glob("*.pcm"))) == 1

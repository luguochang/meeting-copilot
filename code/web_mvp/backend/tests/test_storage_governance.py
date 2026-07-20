from __future__ import annotations

from collections import namedtuple
from io import StringIO
import os
import stat
import pytest

from meeting_copilot_web_mvp.logging_config import (
    ManagedRotatingLogStream,
    redact_sensitive_log_data,
)

from meeting_copilot_web_mvp.storage_governance import (
    GIB,
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    PRODUCT_SOFT_LIMIT_BYTES,
    RESERVED_FREE_BYTES,
    ManagedLogRotator,
    UnsafeManagedPathError,
    estimate_pcm16_storage_bytes,
    harden_managed_storage_permissions,
    preflight_meeting_storage,
    scan_managed_storage,
)


DiskUsage = namedtuple("DiskUsage", "total used free")


def _disk_with_free(free_bytes: int) -> DiskUsage:
    total = 100 * GIB
    return DiskUsage(total=total, used=total - free_bytes, free=free_bytes)


def test_pcm16_budget_is_exact_for_duration_and_tracks():
    assert estimate_pcm16_storage_bytes(duration_seconds=3_600, track_count=1) == 115_200_000
    assert estimate_pcm16_storage_bytes(duration_seconds=3_600, track_count=2) == 230_400_000


@pytest.mark.parametrize(
    ("duration_seconds", "track_count"),
    [(-1, 1), (60, 0), (60, -1), (True, 1), (60, True)],
)
def test_pcm16_budget_rejects_invalid_inputs(duration_seconds, track_count):
    with pytest.raises(ValueError):
        estimate_pcm16_storage_bytes(
            duration_seconds=duration_seconds,
            track_count=track_count,
        )


def test_scan_managed_storage_counts_only_regular_files(tmp_path):
    data_dir = tmp_path / "data"
    (data_dir / "audio_assets" / "meeting-1").mkdir(parents=True)
    (data_dir / "audio_assets" / "meeting-1" / "audio.wav").write_bytes(b"audio")
    (data_dir / "meeting_copilot.db").write_bytes(b"database")

    scan = scan_managed_storage(data_dir)

    assert scan.total_bytes == len(b"audio") + len(b"database")
    assert scan.file_count == 2
    assert scan.directory_count == 2


def test_scan_missing_managed_storage_is_empty(tmp_path):
    scan = scan_managed_storage(tmp_path / "not-created")

    assert scan.total_bytes == 0
    assert scan.file_count == 0
    assert scan.directory_count == 0


def test_scan_rejects_symlink_instead_of_following_it(tmp_path):
    data_dir = tmp_path / "data"
    outside = tmp_path / "outside"
    data_dir.mkdir()
    outside.mkdir()
    (outside / "meeting.wav").write_bytes(b"must-not-be-counted")
    (data_dir / "escaped").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeManagedPathError, match="symbolic link"):
        scan_managed_storage(data_dir)


def test_scan_rejects_symlink_as_managed_root(tmp_path):
    real_data = tmp_path / "real-data"
    real_data.mkdir()
    linked_data = tmp_path / "linked-data"
    linked_data.symlink_to(real_data, target_is_directory=True)

    with pytest.raises(UnsafeManagedPathError, match="root"):
        scan_managed_storage(linked_data)


@pytest.mark.skipif(os.name == "nt", reason="Windows uses inherited per-user ACLs")
def test_harden_managed_storage_tightens_existing_tree(tmp_path):
    data_dir = tmp_path / "data"
    nested = data_dir / "audio_assets" / "meeting-1"
    nested.mkdir(parents=True)
    database = data_dir / "meeting_copilot.db"
    database.write_bytes(b"database")
    audio = nested / "audio.wav"
    audio.write_bytes(b"audio")
    os.chmod(data_dir, 0o755)
    os.chmod(nested, 0o755)
    os.chmod(database, 0o644)
    os.chmod(audio, 0o644)

    report = harden_managed_storage_permissions(data_dir)

    assert report == {"directory_count": 2, "file_count": 2, "already_hardened": 0}
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(nested.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert stat.S_IMODE(audio.stat().st_mode) == 0o600
    second = harden_managed_storage_permissions(data_dir)
    assert second == {"directory_count": 0, "file_count": 0, "already_hardened": 1}
    assert stat.S_IMODE((data_dir / ".storage-permissions-v1").stat().st_mode) == 0o600


def test_preflight_allows_meeting_and_returns_structured_capacity(tmp_path):
    result = preflight_meeting_storage(
        data_dir=tmp_path / "data",
        expected_duration_seconds=3_600,
        track_count=2,
        disk_usage_provider=lambda _path: _disk_with_free(20 * GIB),
    )

    assert result.allowed is True
    assert result.decision == "allow"
    assert result.reason_code == "storage_available"
    assert result.estimated_meeting_bytes == 230_400_000
    assert result.reserved_free_bytes == RESERVED_FREE_BYTES
    assert result.product_quota_bytes == 4 * GIB
    assert result.available_after_reserve_bytes == 18 * GIB
    assert "可以开始会议" in result.user_message
    assert result.to_dict()["allowed"] is True


def test_preflight_blocks_when_two_gib_reserve_would_be_consumed(tmp_path):
    required = estimate_pcm16_storage_bytes(duration_seconds=60, track_count=1)
    result = preflight_meeting_storage(
        data_dir=tmp_path / "data",
        expected_duration_seconds=60,
        track_count=1,
        disk_usage_provider=lambda _path: _disk_with_free(
            RESERVED_FREE_BYTES + required - 1
        ),
    )

    assert result.allowed is False
    assert result.decision == "block"
    assert result.reason_code == "disk_reserve_required"
    assert result.available_after_reserve_bytes == required - 1
    assert "2 GiB" in result.user_message


def test_preflight_blocks_at_twenty_percent_quota_before_ten_gib(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    existing = 2 * GIB - 100
    with (data_dir / "managed.bin").open("wb") as managed_file:
        managed_file.truncate(existing)

    result = preflight_meeting_storage(
        data_dir=data_dir,
        expected_duration_seconds=1,
        track_count=1,
        disk_usage_provider=lambda _path: _disk_with_free(10 * GIB),
    )

    assert result.product_quota_bytes == 2 * GIB
    assert result.allowed is False
    assert result.reason_code == "product_quota_exceeded"
    assert "20%" in result.user_message


def test_preflight_caps_product_quota_at_ten_gib(tmp_path):
    result = preflight_meeting_storage(
        data_dir=tmp_path / "data",
        expected_duration_seconds=60,
        track_count=1,
        disk_usage_provider=lambda _path: _disk_with_free(80 * GIB),
    )

    assert result.product_quota_bytes == PRODUCT_SOFT_LIMIT_BYTES

def test_log_rotator_does_not_rotate_below_limit(tmp_path):
    rotator = ManagedLogRotator(
        data_dir=tmp_path,
        log_name="backend.log",
        max_bytes=10,
        backup_count=LOG_BACKUP_COUNT,
    )

    first = rotator.append("1234")
    second = rotator.append("56")

    assert first.rotated is False
    assert second.rotated is False
    assert rotator.path.read_text() == "123456"
    assert not rotator.backup_path(1).exists()


def test_runtime_log_stream_mirrors_and_uses_managed_rotation(tmp_path):
    mirror = StringIO()
    stream = ManagedRotatingLogStream(
        data_dir=tmp_path,
        mirror=mirror,
        max_bytes=8,
        backup_count=2,
    )

    stream.write("first\n")
    stream.write("second\n")
    stream.flush()

    assert mirror.getvalue() == "first\nsecond\n"
    assert stream.rotator.path.read_text() == "second\n"
    assert stream.rotator.backup_path(1).read_text() == "first\n"
    assert stream.last_error is None


def test_structured_log_redaction_hashes_ids_and_removes_content_and_secrets():
    session_id = "rec_sensitive_meeting_123"
    api_key = "sk-this-secret-must-not-appear"
    redacted = redact_sensitive_log_data(
        None,
        "info",
        {
            "event": "provider.failed",
            "session_id": session_id,
            "path": f"/v2/meetings/{session_id}/snapshot",
            "transcript_text": "这是用户的会议原文",
            "prompt": "请总结会议",
            "authorization": f"Bearer {api_key}",
            "error": f"provider rejected Bearer {api_key}",
            "total_tokens": 3048,
            "nested": {"meeting_id": session_id, "status": "failed"},
        },
    )
    encoded = str(redacted)

    assert redacted["meeting_id_hash"] == redacted["nested"]["meeting_id_hash"]
    assert redacted["path"] == "/v2/meetings/<meeting>/snapshot"
    assert redacted["transcript_text_redacted"] is True
    assert redacted["prompt_redacted"] is True
    assert redacted["authorization_redacted"] is True
    assert redacted["error_redacted"] is True
    assert redacted["total_tokens"] == 3048
    assert session_id not in encoded
    assert api_key not in encoded
    assert "用户的会议原文" not in encoded


def test_log_rotator_rotates_before_append_and_keeps_five_backups(tmp_path):
    rotator = ManagedLogRotator(
        data_dir=tmp_path,
        log_name="backend.log",
        max_bytes=4,
        backup_count=5,
    )

    for value in ("0000", "1111", "2222", "3333", "4444", "5555", "6666"):
        rotator.append(value)

    assert rotator.path.read_text() == "6666"
    assert [rotator.backup_path(index).read_text() for index in range(1, 6)] == [
        "5555",
        "4444",
        "3333",
        "2222",
        "1111",
    ]
    assert not rotator.backup_path(6).exists()


def test_log_rotation_never_deletes_meeting_data(tmp_path):
    meeting_audio = tmp_path / "audio_assets" / "meeting-1" / "audio.wav"
    meeting_audio.parent.mkdir(parents=True)
    meeting_audio.write_bytes(b"meeting-audio")
    rotator = ManagedLogRotator(data_dir=tmp_path, log_name="backend.log", max_bytes=1)

    rotator.append("a")
    rotator.append("b")

    assert meeting_audio.read_bytes() == b"meeting-audio"


@pytest.mark.parametrize(
    "log_name",
    ["../meeting.log", "nested/backend.log", "/tmp/backend.log", "backend.txt"],
)
def test_log_rotator_rejects_paths_outside_managed_logs(tmp_path, log_name):
    with pytest.raises(ValueError, match="log_name"):
        ManagedLogRotator(data_dir=tmp_path, log_name=log_name)


def test_log_rotator_rejects_symlinked_log_file(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    outside = tmp_path / "outside.log"
    outside.write_text("outside")
    (logs_dir / "backend.log").symlink_to(outside)
    rotator = ManagedLogRotator(data_dir=tmp_path, log_name="backend.log")

    with pytest.raises(UnsafeManagedPathError, match="symbolic link"):
        rotator.append("must-not-escape")

    assert outside.read_text() == "outside"


def test_log_defaults_are_ten_mib_and_five_backups():
    assert LOG_MAX_BYTES == 10 * 1024 * 1024
    assert LOG_BACKUP_COUNT == 5

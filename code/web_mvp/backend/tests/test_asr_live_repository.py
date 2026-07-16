from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest

from meeting_copilot_web_mvp import asr_live_repository as repository_module
from meeting_copilot_web_mvp.asr_live_repository import (
    InMemoryAsrLiveSessionRepository,
    JsonFileAsrLiveSessionRepository,
)


@pytest.mark.parametrize(
    "repo_factory",
    [
        lambda tmp_path: InMemoryAsrLiveSessionRepository(),
        lambda tmp_path: JsonFileAsrLiveSessionRepository(tmp_path),
    ],
)
def test_repository_update_reads_latest_record_and_persists_mutation(tmp_path, repo_factory):
    repo = repo_factory(tmp_path)
    repo.create({"session_id": "atomic_update", "counter": 0, "events": []})

    updated = repo.update(
        "atomic_update",
        lambda current: {**current, "counter": current["counter"] + 1},
    )

    assert updated["counter"] == 1
    assert repo.get("atomic_update")["counter"] == 1


@pytest.mark.parametrize(
    "repo_factory",
    [
        lambda tmp_path: InMemoryAsrLiveSessionRepository(),
        lambda tmp_path: JsonFileAsrLiveSessionRepository(tmp_path),
    ],
)
def test_repository_update_prevents_lost_updates_under_concurrency(tmp_path, repo_factory):
    repo = repo_factory(tmp_path)
    repo.create({"session_id": "atomic_concurrency", "counter": 0, "events": []})

    def increment() -> None:
        repo.update(
            "atomic_concurrency",
            lambda current: {**current, "counter": current["counter"] + 1},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: increment(), range(80)))

    assert repo.get("atomic_concurrency")["counter"] == 80


def test_json_repository_two_instances_serialize_atomic_updates(tmp_path):
    first_repo = JsonFileAsrLiveSessionRepository(tmp_path)
    second_repo = JsonFileAsrLiveSessionRepository(tmp_path)
    first_repo.create({"session_id": "shared_atomic_update", "counter": 0, "events": []})
    first_mutator_entered = Event()
    release_first_mutator = Event()

    def increment(repo: JsonFileAsrLiveSessionRepository, *, hold_first: bool = False) -> None:
        def mutate(current):
            if hold_first:
                first_mutator_entered.set()
                assert release_first_mutator.wait(timeout=2)
            return {**current, "counter": current["counter"] + 1}

        repo.update("shared_atomic_update", mutate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(increment, first_repo, hold_first=True)
        assert first_mutator_entered.wait(timeout=2)
        second_future = pool.submit(increment, second_repo)
        time.sleep(0.05)
        release_first_mutator.set()
        futures = [first_future, second_future]
        for future in futures:
            future.result(timeout=5)

    assert first_repo.get("shared_atomic_update")["counter"] == 2
    assert list((tmp_path / "live_asr_sessions").glob("*.tmp")) == []


def test_json_repository_falls_back_to_process_lock_when_fcntl_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "fcntl", None)
    monkeypatch.setattr(repository_module, "msvcrt", None)
    repo = JsonFileAsrLiveSessionRepository(tmp_path)

    repo.create({"session_id": "windows_process_lock", "counter": 0, "events": []})
    updated = repo.update(
        "windows_process_lock",
        lambda current: {**current, "counter": current["counter"] + 1},
    )

    assert updated["counter"] == 1
    assert repo.get("windows_process_lock")["counter"] == 1


def test_json_repository_uses_one_byte_msvcrt_lock_when_fcntl_is_unavailable(tmp_path, monkeypatch):
    class FakeMsvcrt:
        LK_LOCK = 1
        LK_UNLCK = 2

        def __init__(self):
            self.calls = []

        def locking(self, fd, mode, nbytes):
            self.calls.append((fd, mode, nbytes))

    fake_msvcrt = FakeMsvcrt()
    monkeypatch.setattr(repository_module, "fcntl", None)
    monkeypatch.setattr(repository_module, "msvcrt", fake_msvcrt)
    repo = JsonFileAsrLiveSessionRepository(tmp_path)

    repo.create({"session_id": "windows_file_lock", "counter": 0, "events": []})

    assert repository_module._repository_lock_capability() == "msvcrt"
    assert [mode for _fd, mode, nbytes in fake_msvcrt.calls if nbytes == 1] == [
        fake_msvcrt.LK_LOCK,
        fake_msvcrt.LK_UNLCK,
    ]


def test_repository_lock_capability_matches_available_platform_backend():
    capability = repository_module._repository_lock_capability()

    if repository_module.fcntl is not None:
        assert capability == "fcntl"
    elif repository_module.msvcrt is not None:
        assert capability == "msvcrt"
    else:
        assert capability == "process_only"


def test_repository_update_requires_existing_session():
    repo = InMemoryAsrLiveSessionRepository()

    with pytest.raises(KeyError, match="ASR live session not found"):
        repo.update("missing", lambda current: current)


@pytest.mark.parametrize(
    "repo_factory",
    [
        lambda tmp_path: InMemoryAsrLiveSessionRepository(),
        lambda tmp_path: JsonFileAsrLiveSessionRepository(tmp_path),
    ],
)
def test_repository_stamps_creation_and_only_advances_last_activity_when_caller_requests_it(
    tmp_path,
    repo_factory,
    monkeypatch,
):
    monkeypatch.setattr(repository_module, "_now_epoch_ms", lambda: 1_700_000_000_100)
    repo = repo_factory(tmp_path)

    created = repo.create({"session_id": "timestamped_session", "events": []})
    updated = repo.update(
        "timestamped_session",
        lambda current: {**current, "events": [{"event_type": "transcript_final"}]},
    )
    activity_updated = repo.update(
        "timestamped_session",
        lambda current: {**current, "last_activity_at_epoch_ms": 1_700_000_000_900},
    )

    assert created["created_at_epoch_ms"] == 1_700_000_000_100
    assert created["last_activity_at_epoch_ms"] == 1_700_000_000_100
    assert updated["created_at_epoch_ms"] == 1_700_000_000_100
    assert updated["last_activity_at_epoch_ms"] == 1_700_000_000_100
    assert activity_updated["created_at_epoch_ms"] == 1_700_000_000_100
    assert activity_updated["last_activity_at_epoch_ms"] == 1_700_000_000_900


def test_json_repository_uses_file_mtime_for_legacy_records_without_wall_clock_timestamps(tmp_path):
    records_dir = tmp_path / "live_asr_sessions"
    records_dir.mkdir()
    record_path = records_dir / "legacy_session.json"
    record_path.write_text('{"session_id":"legacy_session","events":[]}', encoding="utf-8")
    legacy_epoch_seconds = 1_690_000_000
    record_path.touch()
    import os

    os.utime(record_path, (legacy_epoch_seconds, legacy_epoch_seconds))

    record = JsonFileAsrLiveSessionRepository(tmp_path).get("legacy_session")

    assert record["created_at_epoch_ms"] == legacy_epoch_seconds * 1000
    assert record["last_activity_at_epoch_ms"] == legacy_epoch_seconds * 1000

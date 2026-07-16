from meeting_copilot_web_mvp.llm_lane_locks import LaneLockRegistry


def test_same_session_same_lane_is_single_flight():
    registry = LaneLockRegistry()
    first = registry.try_acquire("meeting_1", "suggestion")

    assert first is not None
    assert registry.try_acquire("meeting_1", "suggestion") is None

    first.release()
    second = registry.try_acquire("meeting_1", "suggestion")
    assert second is not None
    second.release()


def test_correction_and_suggestion_have_independent_locks():
    registry = LaneLockRegistry()
    suggestion = registry.try_acquire("meeting_1", "suggestion")
    correction = registry.try_acquire("meeting_1", "correction")

    assert suggestion is not None
    assert correction is not None

    suggestion.release()
    correction.release()


def test_release_is_idempotent_and_registry_does_not_leak_entries():
    registry = LaneLockRegistry()
    lease = registry.try_acquire("meeting_1", "suggestion")

    assert lease is not None
    lease.release()
    lease.release()

    assert registry.active_lock_count == 0

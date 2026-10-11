import pytest

from meeting_copilot_web_mvp.llm_lane_locks import (
    LaneLockRegistry,
    ProviderLaneRegistry,
    ProviderPriorityArbiter,
)


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


def test_is_active_observes_a_lane_without_changing_lock_behavior():
    registry = LaneLockRegistry()
    assert registry.is_active("meeting_1", "correction") is False
    assert registry.active_lock_count == 0

    correction = registry.try_acquire("meeting_1", "correction")
    assert correction is not None
    assert registry.is_active("meeting_1", "correction") is True
    assert registry.is_active("meeting_1", "suggestion") is False
    suggestion = registry.try_acquire("meeting_1", "suggestion")
    assert suggestion is not None

    correction.release()
    suggestion.release()
    assert registry.is_active("meeting_1", "correction") is False


def test_release_is_idempotent_and_registry_does_not_leak_entries():
    registry = LaneLockRegistry()
    lease = registry.try_acquire("meeting_1", "suggestion")

    assert lease is not None
    lease.release()
    lease.release()

    assert registry.active_lock_count == 0


def test_pending_or_active_realtime_never_blocks_correction_lane():
    registry = ProviderLaneRegistry()
    registry.reserve_unbound_realtime("intelligence-1")

    correction = registry.try_acquire_correction()
    assert correction is not None
    realtime = registry.try_acquire_realtime("intelligence-1")
    assert realtime is not None
    assert registry.pending_realtime_count == 0
    assert registry.active_realtime_count == 1
    assert registry.active_correction_count == 1

    realtime.release()
    correction.release()


def test_active_correction_never_blocks_realtime_lane():
    registry = ProviderLaneRegistry()
    correction = registry.try_acquire_correction()
    assert correction is not None

    registry.reserve_realtime("intelligence-2")
    realtime = registry.try_acquire_realtime("intelligence-2")
    assert realtime is not None
    assert registry.active_realtime_count == 1
    assert registry.active_correction_count == 1

    realtime.release()
    correction.release()


def test_each_provider_lane_enforces_only_its_own_capacity():
    registry = ProviderLaneRegistry(
        realtime_max_active=2,
        correction_max_active=2,
    )
    realtime_1 = registry.try_acquire_realtime("pi-1")
    realtime_2 = registry.try_acquire_realtime("pi-2")
    correction_1 = registry.try_acquire_correction()
    correction_2 = registry.try_acquire_correction()

    assert realtime_1 is not None
    assert realtime_2 is not None
    assert correction_1 is not None
    assert correction_2 is not None
    assert registry.try_acquire_realtime("pi-3") is None
    assert registry.try_acquire_correction() is None

    realtime_1.release()
    realtime_2.release()
    correction_1.release()
    correction_2.release()


@pytest.mark.parametrize(
    ("keyword", "value"),
    (("realtime_max_active", 0), ("correction_max_active", 0)),
)
def test_provider_lane_capacity_must_be_positive(keyword, value):
    with pytest.raises(ValueError, match="must be positive"):
        ProviderLaneRegistry(**{keyword: value})


def test_priority_arbiter_import_is_a_nonblocking_compatibility_alias():
    registry = ProviderPriorityArbiter()
    correction = registry.try_acquire_background()
    realtime = registry.try_acquire_realtime("pi")

    assert correction is not None
    assert realtime is not None

    realtime.release()
    correction.release()


def test_provider_lane_reconciles_stale_pending_work_but_keeps_active_lease():
    registry = ProviderLaneRegistry()
    registry.replace_realtime_reservation("", "stale")
    registry.replace_realtime_reservation("", "live")
    active = registry.try_acquire_realtime("live")
    assert active is not None

    registry.sync_realtime_work({"live"})
    assert registry.pending_realtime_count == 0
    assert registry.active_realtime_count == 1
    correction = registry.try_acquire_correction()
    assert correction is not None

    active.release()
    correction.release()


def test_discussion_state_admission_does_not_wait_for_continuous_correction():
    registry = ProviderLaneRegistry()
    correction = registry.try_acquire_correction()
    assert correction is not None
    try:
        # Generic project/memory reconciliation must retain its old policy.
        assert registry.has_priority_work
        assert registry.try_acquire_reconcile() is None
        assert registry.has_urgent_work is False
        state = registry.try_acquire_discussion_state()
        assert state is not None
        try:
            assert registry.active_correction_count == 1
            assert registry.try_acquire_correction() is None
            assert registry.try_acquire_discussion_state() is None
            assert registry.try_acquire_reconcile() is None
        finally:
            state.release()
    finally:
        correction.release()


def test_discussion_state_and_project_reconcile_share_one_background_lease():
    registry = ProviderLaneRegistry()
    project = registry.try_acquire_reconcile()
    assert project is not None
    try:
        assert registry.try_acquire_discussion_state() is None
    finally:
        project.release()
    state = registry.try_acquire_discussion_state()
    assert state is not None
    try:
        # New correction can progress without stealing the state lease.
        correction = registry.try_acquire_correction()
        assert correction is not None
        correction.release()
        assert registry.try_acquire_reconcile() is None
    finally:
        state.release()
    state.release()
    next_project = registry.try_acquire_reconcile()
    assert next_project is not None
    next_project.release()


@pytest.mark.parametrize("lane,active", [("realtime", False), ("realtime", True), ("deep", False), ("deep", True)])
def test_discussion_state_preserves_urgent_pending_and_active_priority(lane, active):
    registry = ProviderLaneRegistry()
    getattr(registry, f"reserve_{lane}")("urgent")
    lease = getattr(registry, f"try_acquire_{lane}")("urgent") if active else None
    try:
        assert registry.has_urgent_work
        assert registry.try_acquire_discussion_state() is None
    finally:
        if lease is not None:
            lease.release()
        if lane == "deep":
            registry.release_deep("urgent")
        else:
            registry.release_realtime_reservation("urgent")
    assert registry.has_urgent_work is False
    state = registry.try_acquire_discussion_state()
    assert state is not None
    state.release()

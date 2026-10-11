from __future__ import annotations

import asyncio
from fastapi import HTTPException
import pytest

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.llm_lane_locks import LaneLockRegistry, ProviderLaneRegistry
from meeting_copilot_web_mvp.project_reconcile_admission import execute_reconcile
from meeting_copilot_web_mvp.project_reconcile_jobs import BackgroundReconcileDeferred


@pytest.fixture
def gates(monkeypatch):
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: llm_service.LlmConfig(base_url="https://example.com", api_key="fake", model="fake")))
    lanes, registry, usage = LaneLockRegistry(), ProviderLaneRegistry(), []
    async def run(callback, **kwargs):
        return await execute_reconcile(callback, {}, {"meeting_id": "meeting"}, lanes=lanes, provider_lanes=registry,
                                       enforce_budget=kwargs.get("budget", lambda *a, **k: None),
                                       record_usage=lambda *a, **k: usage.append(k["usage"]), validate_config=lambda config: None)
    return lanes, registry, usage, run


def test_success_records_reported_usage_and_has_single_background_lease(gates):
    _, registry, usage, run = gates
    async def callback(payload, job, config):
        assert registry.try_acquire_reconcile() is None
        return {"metrics": {"usage": {"total_tokens": 7}}}
    assert asyncio.run(run(callback))["metrics"]["usage"]["total_tokens"] == 7
    assert usage == [{"total_tokens": 7}]
    assert registry.try_acquire_reconcile() is not None


def test_memory_background_uses_shared_admission_with_an_explicit_ledger_key(gates):
    lanes, registry, _, _ = gates
    budget, recorded = [], []
    async def callback(payload, job, config):
        assert registry.try_acquire_reconcile() is None
        return {"metrics": {"usage": {"total_tokens": 8}}}
    result = asyncio.run(execute_reconcile(callback, {}, {"meeting_id": "memory:synthetic-project"}, lanes=lanes, provider_lanes=registry,
        purpose="memory_reconcile", enforce_budget=lambda key, **kw: budget.append((key, kw["purpose"])),
        record_usage=lambda key, **kw: recorded.append((key, kw["purpose"], kw["usage"])), validate_config=lambda _: None))
    assert result["metrics"]["usage"]["total_tokens"] == 8
    assert budget == [("memory:synthetic-project", "memory_reconcile")]
    assert recorded == [("memory:synthetic-project", "memory_reconcile", {"total_tokens": 8})]


@pytest.mark.parametrize("lane", ["answer", "ask", "correction"])
def test_user_waiting_lane_defers_before_model_start(gates, lane):
    lanes, _, _, run = gates
    lease = lanes.try_acquire("different-meeting", lane)
    called = []
    async def callback(*args):
        called.append(True)
    try:
        with pytest.raises(BackgroundReconcileDeferred):
            asyncio.run(run(callback))
        assert called == []
    finally:
        lease.release()


def test_new_realtime_request_proceeds_and_background_awaits_cancel_ack(gates):
    _, registry, _, run = gates
    async def scenario():
        entered, ack = asyncio.Event(), asyncio.Event()
        async def callback(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.02)
                ack.set()
        task = asyncio.create_task(run(callback))
        await entered.wait()
        realtime = registry.try_acquire_realtime("urgent")
        assert realtime is not None
        with pytest.raises(BackgroundReconcileDeferred) as error:
            await task
        assert error.value.code == "preempted_by_realtime"
        assert ack.is_set()
        realtime.release()
        assert registry.try_acquire_reconcile() is not None
    asyncio.run(scenario())


def test_job_cancellation_waits_for_local_ack_and_releases_background(gates):
    _, registry, _, run = gates
    async def scenario():
        entered, ack = asyncio.Event(), asyncio.Event()
        async def callback(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.02)
                ack.set()
        task = asyncio.create_task(run(callback))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ack.is_set() and registry.try_acquire_reconcile() is not None
    asyncio.run(scenario())


def test_missing_configuration_and_budget_do_not_start_model(gates, monkeypatch):
    _, registry, _, run = gates
    calls = []
    async def callback(*args):
        calls.append(True)
    def budget(*args, **kwargs):
        raise HTTPException(429, "budget")
    with pytest.raises(BackgroundReconcileDeferred, match="budget_or_runtime_deferred"):
        asyncio.run(run(callback, budget=budget))
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: None))
    with pytest.raises(BackgroundReconcileDeferred, match="provider_not_configured"):
        asyncio.run(run(callback))
    assert calls == [] and registry.try_acquire_reconcile() is not None

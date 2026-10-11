"""No-network deadline races and failed-call accounting preserve real evidence."""
from __future__ import annotations

import asyncio
from io import StringIO
import threading

import pytest

from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp.llm_lane_locks import LaneLockRegistry, ProviderLaneRegistry
from meeting_copilot_web_mvp.memory_jobs import MemoryReconcilePiExecutor
from meeting_copilot_web_mvp.pi_coach_runtime import PROTOCOL, PiCoachRuntimeError, PiCoachSidecar
from meeting_copilot_web_mvp.preparation_jobs import PreparationPiExecutor
from meeting_copilot_web_mvp.project_reconcile_admission import execute_reconcile, safe_background_failure
from meeting_copilot_web_mvp.project_reconcile_jobs import BackgroundReconcileDeferred


def test_failure_metrics_keep_only_static_known_fields_and_unknown_cost_is_unknown():
    failure = PiCoachRuntimeError("provider private body fixture-secret", code="agent_deadline_exceeded", metrics={
        "turns": 1, "tool_calls": 0, "ttft_ms": None, "prompt_profile": "memory_reconcile",
        "tool_names": ["propose_memory_update", "fixture-secret"], "prompt": "fixture-secret",
        "usage": {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15, "key": "fixture-secret"},
        "timings": {"clock": "unix_epoch_ms", "started_at_ms": 1000, "key": "fixture-secret"},
        "provider_status_code": 200, "bridge_round_trip_ms": 25001,
    })
    code, metrics = safe_background_failure(failure)
    assert code == "agent_deadline_exceeded" and metrics["usage_known"] is True
    assert metrics["turns"] == 1 and metrics["tool_names"] == ["propose_memory_update"]
    assert metrics["usage"] == {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
    assert "fixture-secret" not in str((code, metrics))
    for usage in (None, {}, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}):
        _, unknown = safe_background_failure(PiCoachRuntimeError("private", metrics={"usage": usage}))
        assert unknown["usage_known"] is False and "usage" not in unknown
    assert safe_background_failure(PiCoachRuntimeError("private", code="fixture-secret"))[0] == "pi_runtime_error"


def test_only_background_executors_reserve_250ms_and_model_deadlines_stay_unchanged():
    assert MemoryReconcilePiExecutor(None).runtime.timeout_seconds == 25.25
    assert PreparationPiExecutor(None).runtime.timeout_seconds == 45.25
    assert PiCoachSidecar().timeout_seconds == 12.0


@pytest.mark.parametrize("executor_class,budget_ms", [(MemoryReconcilePiExecutor, 25000), (PreparationPiExecutor, 45000)])
def test_background_request_keeps_the_original_model_budget_while_host_can_receive_ack(executor_class, budget_ms):
    captured = []
    class Queue:
        def mark_started(self, job):
            pass
    class Runtime:
        available = True
        async def evaluate(self, request):
            captured.append(request["provider"])
            return {"metrics": {}}
    executor = executor_class(Queue())
    executor.runtime = Runtime()
    config = llm_service.LlmConfig(base_url="https://unused.invalid", api_key="fixture", model="fixture", timeout_seconds=60)
    asyncio.run(executor({}, {}, config))
    assert captured[0]["timeout_ms"] == captured[0]["decision_timeout_ms"] == budget_ms


def test_bounded_host_window_receives_node_deadline_metrics_after_model_deadline():
    class Process:
        stdin = StringIO()
        def poll(self):
            return None

    # Scale the fake model deadline to 40ms. Its error arrives 30ms later,
    # within the existing 250ms response window, without extending execution.
    sidecar = PiCoachSidecar(command=["never-executed"], timeout_seconds=0.29)
    sidecar._process = Process()
    sidecar._ready.set()
    response = {"protocol": PROTOCOL, "request_id": "deadline-fixture", "ok": False,
        "error": {"code": "agent_deadline_exceeded", "message": "private", "metrics": {
            "turns": 1, "tool_calls": 0, "decision_timeout_ms": 40,
            "usage": {"prompt_tokens": 12, "total_tokens": 12}}}}
    timer = threading.Timer(0.07, lambda: sidecar._responses.put(response))
    timer.start()
    try:
        with pytest.raises(PiCoachRuntimeError) as caught:
            sidecar._evaluate_sync({"request_id": "deadline-fixture", "provider": {"decision_timeout_ms": 40}})
        assert caught.value.code == "agent_deadline_exceeded"
        assert caught.value.metrics["turns"] == 1 and caught.value.metrics["usage"]["total_tokens"] == 12
        assert caught.value.metrics["bridge_round_trip_ms"] >= 40
    finally:
        timer.join()


@pytest.mark.parametrize("purpose", ["memory_reconcile", "meeting_preparation"])
def test_failed_reported_usage_is_recorded_exactly_once_then_same_error_propagates(monkeypatch, purpose):
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda _: llm_service.LlmConfig(base_url="https://unused.invalid", api_key="fixture", model="fixture")))
    recorded = []
    failure = PiCoachRuntimeError("private", code="agent_deadline_exceeded", metrics={"usage": {
        "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})
    async def callback(*args):
        raise failure
    key = ("memory:" if purpose == "memory_reconcile" else "preparation:") + "project_" + "a" * 32
    with pytest.raises(PiCoachRuntimeError) as caught:
        asyncio.run(execute_reconcile(callback, {}, {"meeting_id": key}, lanes=LaneLockRegistry(), provider_lanes=ProviderLaneRegistry(),
            enforce_budget=lambda *a, **k: None, record_usage=lambda account, **kw: recorded.append((account, kw["purpose"], kw["usage"])),
            validate_config=lambda _: None, purpose=purpose))
    assert caught.value is failure
    assert recorded == [(key, purpose, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})]


@pytest.mark.parametrize("case", ["unknown_usage", "budget", "cancel"])
def test_no_reported_usage_budget_or_cancel_never_fabricates_a_charge_or_repeats(monkeypatch, case):
    from fastapi import HTTPException
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda _: llm_service.LlmConfig(base_url="https://unused.invalid", api_key="fixture", model="fixture")))
    recorded, calls, ack = [], [], asyncio.Event()
    def budget(*args, **kwargs):
        if case == "budget":
            raise HTTPException(429, "fixture budget")
    async def callback(*args):
        calls.append(True)
        if case == "cancel":
            try:
                await asyncio.Event().wait()
            finally:
                ack.set()
        raise PiCoachRuntimeError("private", code="agent_deadline_exceeded")
    async def run():
        coroutine = execute_reconcile(callback, {}, {"meeting_id": "memory:project_" + "a" * 32},
            lanes=LaneLockRegistry(), provider_lanes=ProviderLaneRegistry(), enforce_budget=budget,
            record_usage=lambda *a, **kw: recorded.append(kw), validate_config=lambda _: None)
        if case == "cancel":
            task = asyncio.create_task(coroutine)
            while not calls:
                await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert ack.is_set()
        else:
            with pytest.raises(BackgroundReconcileDeferred if case == "budget" else PiCoachRuntimeError):
                await coroutine
    asyncio.run(run())
    assert recorded == [] and len(calls) == (0 if case == "budget" else 1)


@pytest.mark.parametrize("kind", ["memory", "preparation"])
@pytest.mark.parametrize("code", ["agent_deadline_exceeded", "pi_timeout"])
def test_real_queues_persist_deadline_classification_log_safe_metrics_and_explain_manual_retry(tmp_path, monkeypatch, kind, code):
    from meeting_copilot_web_mvp import project_reconcile_admission
    from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
    from meeting_copilot_web_mvp.memory_jobs import MemoryReconcileQueue, MemoryReconcileRunner
    from meeting_copilot_web_mvp.memory_jobs_api import public_memory_job
    from meeting_copilot_web_mvp.preparation_jobs import PreparationQueue, PreparationRunner
    from meeting_copilot_web_mvp.preparation_jobs_api import public_preparation_job
    logs = []
    class Logger:
        def warning(self, event, **fields):
            logs.append((event, fields))
    monkeypatch.setattr(project_reconcile_admission, "_log", Logger())
    library = KnowledgeRepository(tmp_path / "isolated.db")
    try:
        project = library.create_project("合成诊断项目")
        if kind == "memory":
            queue = MemoryReconcileQueue(library)
            library._memory_reconcile_queue = queue
            doc = library.create_document(project["id"], "本人声明")
            version = library.add_version(doc["id"], "我希望回答简短。", expected_revision=1)
            library.confirm_version(doc["id"], version["id"], expected_revision=2)
            job = queue.list(project["id"])["items"][0]
            runner_class, public = MemoryReconcileRunner, public_memory_job
        else:
            queue = PreparationQueue(library)
            job = queue.enqueue(project["id"], document_ids=[], goal="确认交付范围", expected_project_revision=project["revision"])
            runner_class, public = PreparationRunner, public_preparation_job
        async def callback(payload, claimed):
            queue.mark_started(claimed)
            raise PiCoachRuntimeError("fixture-private-provider-body", code=code, metrics={
                "turns": 1, "tool_calls": 0, "elapsed_ms": 25000, "provider_body": "fixture-private-provider-body"})
        runner = runner_class(queue, callback)
        async def run():
            await runner.start()
            try:
                for _ in range(200):
                    if queue.get(job["id"])["status"] == "failed":
                        return
                    await asyncio.sleep(0.01)
                pytest.fail("synthetic runner did not complete")
            finally:
                await runner.stop()
        asyncio.run(run())
        current = queue.get(job["id"])
        assert current["status"] == "failed" and current["error_code"] == code and current["attempts"] == 1
        assert queue.claim() is None
        assert "重试" in public(queue, current)["error_message"]
        assert len(logs) == 1 and logs[0][0] == "background.pi.failed"
        assert logs[0][1]["metrics"]["turns"] == 1
        assert logs[0][1]["metrics"]["usage_known"] is False
        assert "fixture-private-provider-body" not in str(logs)
    finally:
        library.close()

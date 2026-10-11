"""Safe job/settings API. Reading or restarting never authorizes old paid work."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import Field

from .knowledge_api import _Payload, _cursor, _guard, _page
from .memory_jobs import MemoryReconcilePiExecutor, MemoryReconcileQueue, MemoryReconcileRunner
from .project_reconcile_jobs import BackgroundReconcileDeferred


class MemoryJobRequest(_Payload):
    expected_version_id: str = Field(min_length=1, max_length=100)


class MemoryReconcileSettings(_Payload):
    mode: Literal["off", "explicit_only"]
    expected_revision: int = Field(ge=0)


def public_memory_job(queue, job):
    messages = {
        "input_superseded": "资料或记忆已变化，请刷新后手动重试。",
        "execution_interrupted": "执行已中断，候选没有写入；可手动重试。",
        "background_busy": "等待实时工作空闲。", "provider_not_configured": "等待模型配置就绪。",
        "budget_or_runtime_deferred": "等待调用预算或模型能力就绪。",
        "preempted_by_realtime": "实时工作优先，后台整理等待空闲。",
        "settings_disabled": "自动记忆整理已关闭，任务已取消。",
        "memory_reconciliation_failed": "记忆整理失败，可检查配置后手动重试。",
        "agent_deadline_exceeded": "模型在本次整理时限内没有完成；不会自动重试，可明确重试。",
        "pi_timeout": "等待记忆整理结果超时；不会自动重试，可明确重试。",
        "agent_provider_error": "模型服务没有正常完成本次整理，可检查配置后手动重试。",
        "background_deferred": "后台长时间未能获得执行机会，可手动重试。",
    }
    needs_resume = job["status"] == "pending" and job["id"] not in queue.eligible
    with queue.repository._lock:
        document = queue.repository._get("knowledge_documents", job["document_id"])
        retry_source = (document["status"] == "active" and document["active_version_id"] == job["source_version_id"]
            and document["source_status"] == "current" and queue.settings(job["project_id"])["mode"] != "off")
    return {"id": job["id"], "project_id": job["project_id"], "document_id": job["document_id"], "document_title": document["title"],
        "source_version_id": job["source_version_id"], "source_version": job["source_version"], "status": job["status"],
        "error_code": job["error_code"], "error_message": messages.get(job["error_code"],
            "记忆整理失败，可检查配置后手动重试。" if job["error_code"] else None),
        "created_at_ms": job["created_at_ms"], "updated_at_ms": job["updated_at_ms"], "attempts": job["attempts"],
        "result_memory_ids": json.loads(job["result_memory_ids_json"]), "requires_resume": needs_resume,
        "can_retry": retry_source and (job["status"] in {"failed", "cancelled"} or needs_resume)}


def mount_memory_jobs_api(app: FastAPI, repository) -> None:
    queue = MemoryReconcileQueue(repository) if repository is not None else None
    pi = MemoryReconcilePiExecutor(queue) if queue else None

    async def execute(payload, job):
        admission = getattr(app.state, "run_memory_reconcile_with_admission", None)
        if admission is None:
            raise BackgroundReconcileDeferred(code="provider_not_configured", retry_after_ms=5000)
        return await admission(pi, payload, job)

    runner = MemoryReconcileRunner(queue, execute) if queue else None
    app.state.memory_reconcile_queue, app.state.memory_reconcile_runner, app.state.memory_reconcile_pi = queue, runner, pi
    if repository is not None:
        repository._memory_reconcile_queue = queue
    router = APIRouter(prefix="/v2/knowledge", tags=["memory-reconcile"])

    def require():
        if queue is None:
            raise HTTPException(503, "自动记忆整理需要本地资料存储。")
        return queue

    @router.get("/projects/{project_id}/memory-reconcile-settings")
    @_guard
    def settings(project_id: str):
        return {"settings": require().settings(project_id)}

    @_guard
    def write_settings(project_id, payload):
        return require().set_settings(project_id, **payload.model_dump())

    @router.put("/projects/{project_id}/memory-reconcile-settings")
    async def settings_update(project_id: str, payload: MemoryReconcileSettings):
        setting, cancelled = await asyncio.to_thread(write_settings, project_id, payload)
        for job_id in cancelled:
            await runner.cancel(job_id)
        return {"settings": setting}

    @router.post("/documents/{document_id}/memory-reconcile", status_code=202)
    @_guard
    def enqueue(document_id: str, payload: MemoryJobRequest):
        return {"job": public_memory_job(require(), require().enqueue(document_id, **payload.model_dump()))}

    @router.get("/memory-reconcile/jobs")
    @_guard
    def jobs(project_id: str, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().list(project_id, limit=limit, cursor=_cursor(cursor)), lambda value: public_memory_job(queue, value))

    @router.get("/memory-reconcile/jobs/{job_id}")
    @_guard
    def get(job_id: str):
        return {"job": public_memory_job(require(), require().get(job_id))}

    @router.post("/memory-reconcile/jobs/{job_id}/retry")
    @_guard
    def retry(job_id: str, payload: MemoryJobRequest):
        return {"job": public_memory_job(require(), require().retry(job_id, **payload.model_dump()))}

    @_guard
    def checked(job_id):
        return require().get(job_id)

    @router.post("/memory-reconcile/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        await asyncio.to_thread(checked, job_id)
        return {"job": public_memory_job(queue, await runner.cancel(job_id))}

    app.include_router(router)
    if runner:
        app.router.add_event_handler("startup", runner.start)
        async def stop():
            await runner.stop()
            await pi.close()
        app.router.add_event_handler("shutdown", stop)

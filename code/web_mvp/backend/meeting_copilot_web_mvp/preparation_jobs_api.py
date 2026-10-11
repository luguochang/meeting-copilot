"""Explicit goal-based preparation; GET and restart never authorize a model call."""
from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field

from .knowledge_api import _Payload, _cursor, _guard, _page
from .knowledge_repository import KnowledgeConflict, KnowledgeScopeError
from .preparation_jobs import PreparationPiExecutor, PreparationQueue, PreparationRunner
from .project_reconcile_jobs import BackgroundReconcileDeferred


class PreparationCreate(_Payload):
    folder_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=3000)
    goal: str = Field(min_length=1, max_length=1000)
    role: str = Field(default="", max_length=160)
    focus: list[str] = Field(default_factory=list, max_length=8)
    scene: Literal["general", "decision", "project", "interview", "brainstorm"] = "project"
    expected_project_revision: int = Field(ge=1)


class PreparationRetry(_Payload):
    expected_revision: int = Field(ge=1)
    expected_input_hash: str = Field(min_length=64, max_length=64)
    expected_project_revision: int = Field(ge=1)


def public_preparation_job(queue, job):
    snapshot, payload = json.loads(job["snapshot_json"]), json.loads(job["source_payload_json"])
    messages = {"input_superseded": "项目状态、资料版本或偏好已变化，请核对范围后重新准备。",
        "execution_interrupted": "准备已中断；不会自动重复调用，可明确重试。", "background_busy": "等待实时任务空闲。",
        "preempted_by_realtime": "已让出资源给实时任务。", "provider_not_configured": "等待模型配置。",
        "budget_or_runtime_deferred": "等待预算或模型能力可用。", "background_deferred": "等待时间过长，可手动恢复。",
        "preparation_failed": "准备失败，检查配置后可手动重试。",
        "agent_deadline_exceeded": "模型在本次准备时限内没有完成；不会自动重试，可明确重试。",
        "pi_timeout": "等待会前准备结果超时；不会自动重试，可明确重试。",
        "agent_provider_error": "模型服务没有正常完成本次准备，可检查配置后手动重试。"}
    try:
        queue.validate(job)
        evidence_status = "valid"
    except (KnowledgeConflict, KnowledgeScopeError, KeyError):
        evidence_status = "stale"
    return {**{key: job[key] for key in ("id", "project_id", "status", "revision", "input_hash", "base_project_revision", "attempts",
        "defer_count", "next_attempt_at_ms", "error_code", "created_at_ms", "updated_at_ms", "completed_at_ms")},
        **{key: snapshot[key] for key in ("goal", "role", "focus", "scene")},
        "folder_id": snapshot["selection"]["folder_id"], "document_ids": snapshot["selection"]["document_ids"],
        "requires_resume": job["status"] == "pending" and job["id"] not in queue.eligible,
        "error_message": messages.get(job["error_code"], "准备失败，检查配置后可手动重试。" if job["error_code"] else None),
        "evidence_status": evidence_status,
        "context_truncated": payload["preparation"]["context_truncated"],
        "result": json.loads(job["result_json"]) if job["result_json"] else None}


def mount_preparation_jobs_api(app, repository, outcomes=None):
    queue = PreparationQueue(repository) if repository is not None else None
    pi = PreparationPiExecutor(queue) if queue else None

    async def execute(payload, job):
        admission = getattr(app.state, "run_preparation_with_admission", None)
        if admission is None:
            raise BackgroundReconcileDeferred(code="provider_not_configured", retry_after_ms=5000)
        return await admission(pi, payload, job)

    runner = PreparationRunner(queue, execute) if queue else None
    app.state.preparation_queue, app.state.preparation_runner, app.state.preparation_pi = queue, runner, pi
    router = APIRouter(prefix="/v2/knowledge", tags=["meeting-preparation"])

    def require():
        if queue is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "会前准备需要本地资料存储。"})
        return queue

    @router.post("/projects/{project_id}/preparation-jobs", status_code=202)
    @_guard
    def create(project_id: str, payload: PreparationCreate):
        return {"job": public_preparation_job(require(), require().enqueue(project_id, **payload.model_dump()))}

    @router.get("/preparation-jobs")
    @_guard
    def jobs(project_id: str, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().list(project_id, limit=limit, cursor=_cursor(cursor)), lambda job: public_preparation_job(queue, job))

    @router.get("/preparation-jobs/{job_id}")
    @_guard
    def get(job_id: str):
        return {"job": public_preparation_job(require(), require().get(job_id))}

    @router.post("/preparation-jobs/{job_id}/retry")
    @_guard
    def retry(job_id: str, payload: PreparationRetry):
        return {"job": public_preparation_job(require(), require().retry(job_id, **payload.model_dump()))}

    @_guard
    def checked(job_id):
        return require().get(job_id)

    @router.post("/preparation-jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        await asyncio.to_thread(checked, job_id)
        return {"job": public_preparation_job(queue, await runner.cancel(job_id))}

    app.include_router(router)
    if runner:
        app.router.add_event_handler("startup", runner.start)

        async def stop():
            await runner.stop()
            await pi.close()

        app.router.add_event_handler("shutdown", stop)

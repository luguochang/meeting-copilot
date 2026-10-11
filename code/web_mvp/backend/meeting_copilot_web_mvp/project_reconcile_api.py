"""Explicit reconciliation jobs. No paid work starts during startup or polling."""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import Field

from .knowledge_api import _Payload, _cursor, _guard, _page
from .knowledge_repository import KnowledgeRepository
from .project_outcomes import ProjectOutcomes
from .project_reconcile_jobs import BackgroundReconcileDeferred, ProjectReconcilePiExecutor, ProjectReconcileQueue, ProjectReconcileRunner
from .source_library_api import _source_guard


class ReconcileCreate(_Payload):
    document_kind: Literal["minutes", "decisions", "action_items", "risks", "transcript"] = "minutes"
    expected_project_revision: int = Field(ge=1)
    expected_source_version: int = Field(ge=1)


class ReconcileRetry(_Payload):
    expected_project_revision: int = Field(ge=1)
    expected_source_version: int = Field(ge=1)


def _job(job):
    messages = {"input_superseded": "原文、项目状态、资料范围或记忆已变化，请刷新后手动重试。",
                "execution_interrupted": "执行已中断，候选没有写入；可手动重试。",
                "background_busy": "等待实时工作空闲。", "provider_not_configured": "等待模型配置就绪。",
                "budget_or_runtime_deferred": "等待调用预算或模型能力就绪。",
                "reconciliation_failed": "对账失败，可检查配置后手动重试。"}
    payload = json.loads(job["source_payload_json"])
    return {**{key: value for key, value in job.items() if key not in {"input_context_json", "source_refs_json", "source_payload_json", "input_hash", "lease_owner", "lease_until_ms"}},
            "context_truncated": payload["knowledge_context"]["project_state"]["truncated"],
            "source_characters": len(payload["review_final_content"]), "source_version": json.loads(job["source_refs_json"])[0]["version"],
            "error_message": messages.get(job["error_class"]) if job["error_class"] else None}


def mount_project_reconcile_api(app: FastAPI, repository: KnowledgeRepository | None, outcomes: ProjectOutcomes | None) -> None:
    queue = ProjectReconcileQueue(repository, outcomes) if repository is not None and outcomes is not None else None
    pi = ProjectReconcilePiExecutor(queue) if queue is not None else None

    async def execute(payload, job):
        admission = getattr(app.state, "run_project_reconcile_with_admission", None)
        if admission is None:
            raise BackgroundReconcileDeferred(code="provider_not_configured", retry_after_ms=5000)
        return await admission(pi, payload, job)

    runner = ProjectReconcileRunner(queue, execute) if queue is not None else None
    app.state.project_reconcile_queue, app.state.project_reconcile_runner, app.state.project_reconcile_pi = queue, runner, pi
    router = APIRouter(prefix="/v2/knowledge", tags=["project-reconcile"])

    def require():
        if queue is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "项目对账需要启用本地 SQLite 存储。"})
        return queue

    @router.post("/meetings/{meeting_id}/project-reconcile", status_code=202)
    @_source_guard
    def enqueue(meeting_id: str, payload: ReconcileCreate):
        return {"job": _job(require().enqueue(meeting_id, **payload.model_dump()))}

    @router.get("/project-reconcile/jobs")
    @_guard
    def jobs(project_id: str, meeting_id: str | None = None, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().list(project_id, meeting_id=meeting_id, limit=limit, cursor=_cursor(cursor)), _job)

    @router.get("/project-reconcile/jobs/{job_id}")
    @_guard
    def get(job_id: str):
        return {"job": _job(require().get(job_id))}

    @router.post("/project-reconcile/jobs/{job_id}/retry")
    @_source_guard
    def retry(job_id: str, payload: ReconcileRetry):
        return {"job": _job(require().retry(job_id, **payload.model_dump()))}

    @_guard
    def checked(job_id):
        require().get(job_id)

    @router.post("/project-reconcile/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        await asyncio.to_thread(checked, job_id)
        return {"job": _job(await runner.cancel(job_id))}

    app.include_router(router)
    if runner is not None:
        app.router.add_event_handler("startup", runner.start)
        async def stop():
            await runner.stop()
            await pi.close()
        app.router.add_event_handler("shutdown", stop)

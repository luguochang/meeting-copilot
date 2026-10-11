"""No-project material uploads, bounded previews and explicit meeting-local use."""
from __future__ import annotations

import asyncio
from functools import wraps
from pathlib import Path
import re
import sqlite3
from typing import Callable

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .document_import import MAX_UPLOAD_BYTES
from .knowledge_api import ERROR_MESSAGES
from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError, MAX_CONTENT_CHARACTERS
from .meeting_material_jobs import MeetingMaterialJobQueue, MeetingMaterialJobRunner
from .meeting_materials import MeetingMaterialImportService, MeetingMaterialsRepository, read_attachment_source


def _attachment(value: dict) -> dict:
    result = {key: item for key, item in value.items() if key not in {"original_relative_path", "original_sha256"}}
    result["error_message"] = ERROR_MESSAGES.get(value.get("error_code"), "转换失败，请核对或重新导出文件。") if value.get("error_code") else None
    return result


def _job(value: dict | None) -> dict | None:
    if value is None:
        return None
    result = {key: item for key, item in value.items() if key not in {"lease_token", "lease_until_ms", "input_sha256"}}
    result["error_message"] = ERROR_MESSAGES.get(value.get("error_code"), "后台处理失败，可手动重试。") if value.get("error_code") else None
    return result


def _guard(function: Callable) -> Callable:
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KnowledgeConflict:
            raise HTTPException(409, detail={"code": "revision_conflict", "message": "本场资料已被更新，请刷新后重试。"}) from None
        except KnowledgeScopeError:
            raise HTTPException(409, detail={"code": "invalid_material_scope", "message": "资料不在本场有效范围，或已删除、停用、版本变化。"}) from None
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found", "message": "会议、资料集合、附件、版本或任务不存在。"}) from None
        except (ValueError, TypeError):
            raise HTTPException(400, detail={"code": "invalid_request", "message": "参数不正确，请核对文件格式、大小、名称及所选范围。"}) from None
        except sqlite3.Error:
            raise HTTPException(503, detail={"code": "storage_unavailable", "message": "本地资料存储暂时不可用，请稍后重试。"}) from None
        except OSError:
            raise HTTPException(503, detail={"code": "original_write_failed", "message": "原文件保存失败，请检查本地存储后重试。"}) from None
    return wrapped


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _VersionCreate(_Payload):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARACTERS)
    expected_revision: int = Field(ge=1)
    base_version_id: str | None = None


class _Usage(_Payload):
    version_id: str | None = None
    enabled: bool
    selected_span_ids: list[str] | None = Field(default=None, max_length=5000)
    expected_revision: int = Field(ge=1)


class _Bind(_Payload):
    material_set_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)


class _UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limited = (scope["type"] == "http" and scope.get("method") == "POST"
                   and re.fullmatch(r"/v2/material-sets/[^/]+/attachments", scope.get("path", "")))
        if not limited:
            return await self.app(scope, receive, send)
        maximum = MAX_UPLOAD_BYTES + 1024 * 1024
        try:
            length = int(dict(scope.get("headers", [])).get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if length < 0 or length > maximum:
            response = JSONResponse({"detail": {"code": "upload_too_large", "message": "文件超过 20 MiB 上传限制。"}}, status_code=413)
            return await response(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > maximum:
                raise HTTPException(413, detail={"code": "upload_too_large", "message": "文件超过 20 MiB 上传限制。"})
            return message
        return await self.app(scope, bounded_receive, send)


def mount_meeting_materials_api(app: FastAPI, library: KnowledgeRepository | None, data_dir: Path | None) -> None:
    repository = MeetingMaterialsRepository(library) if library is not None else None
    importer = MeetingMaterialImportService(repository, data_dir) if repository is not None and data_dir is not None else None
    queue = MeetingMaterialJobQueue(repository) if repository is not None else None
    runner = MeetingMaterialJobRunner(queue, importer) if queue is not None and importer is not None else None
    app.state.meeting_materials_repository = repository
    app.state.meeting_material_jobs = queue
    app.state.meeting_material_importer = importer
    app.state.meeting_material_job_runner = runner
    app.add_middleware(_UploadLimitMiddleware)
    router = APIRouter(prefix="/v2", tags=["meeting-materials"])

    def require() -> MeetingMaterialsRepository:
        if repository is None:
            raise HTTPException(503, detail={"code": "materials_storage_unavailable", "message": "本场资料需要启用本地 SQLite 存储。"})
        return repository

    @router.post("/material-sets", status_code=201)
    @_guard
    def create_set():
        return {"material_set": require().create_set()}

    @router.get("/material-sets")
    @_guard
    def list_sets(limit: int = Query(50, ge=1, le=200), cursor: str | None = None):
        return require().list_sets(limit=limit, before_id=cursor)

    @router.get("/material-sets/{set_id}")
    @_guard
    def get_set(set_id: str):
        repo = require()
        material_set = repo.get_set(set_id)
        page = repo.list_attachments(set_id)
        return {"material_set": material_set, "attachments": [_attachment(item) for item in page["items"]],
                "context_revision": material_set["context_revision"], "has_more": page["has_more"], "next_cursor": page["next_cursor"]}

    @router.post("/meetings/{meeting_id}/material-set")
    @_guard
    def meeting_set(meeting_id: str, payload: _Bind):
        return {"material_set": require().bind(meeting_id, payload.material_set_id, expected_revision=payload.expected_revision)}

    @router.get("/meetings/{meeting_id}/material-set")
    @_guard
    def existing_meeting_set(meeting_id: str):
        repo = require()
        with repo._lock:
            if repo._conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
                raise KeyError("meeting not found")
            row = repo._conn.execute("SELECT id FROM material_sets WHERE meeting_id=? AND state='bound'", (meeting_id,)).fetchone()
            return {"material_set": repo.get_set(row[0]) if row else None}

    def stage(set_id, filename, data, title, purpose, content_type):
        require()
        if importer is None:
            raise HTTPException(503, detail={"code": "materials_storage_unavailable", "message": "本地附件存储未就绪。"})
        attachment = importer.stage(set_id, filename, data, title=title, purpose=purpose, content_type=content_type)
        with repository._lock:
            row = repository._conn.execute("SELECT * FROM meeting_attachment_jobs WHERE attachment_id=? ORDER BY created_at_ms DESC LIMIT 1", (attachment["id"],)).fetchone()
        return {"attachment": _attachment(attachment), "job": _job(dict(row)), "material_set": repository.get_set(set_id)}
    stage = _guard(stage)

    @router.post("/material-sets/{set_id}/attachments", status_code=202)
    async def upload(set_id: str, file: UploadFile = File(...), title: str | None = Form(None), purpose: str = Form("")):
        require()
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail={"code": "upload_too_large", "message": "文件超过 20 MiB 上传限制。"})
            return await asyncio.to_thread(stage, set_id, file.filename or "", data, title, purpose, file.content_type)
        finally:
            await file.close()

    @router.get("/material-sets/{set_id}/attachments")
    @_guard
    def attachments(set_id: str, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        page = require().list_attachments(set_id, limit=limit, before_id=cursor)
        return {**page, "items": [_attachment(item) for item in page["items"]]}

    @router.get("/material-sets/{set_id}/attachments/{attachment_id}")
    @_guard
    def attachment_get(set_id: str, attachment_id: str, version_id: str | None = None):
        repo = require()
        attachment = repo.get_attachment(set_id, attachment_id)
        selected = version_id or attachment["latest_version_id"]
        version = repo.get_version(set_id, attachment_id, selected) if selected is not None else None
        with repo._lock:
            row = repo._conn.execute("SELECT * FROM meeting_attachment_jobs WHERE attachment_id=? ORDER BY created_at_ms DESC,id DESC LIMIT 1", (attachment_id,)).fetchone()
        return {"attachment": _attachment(attachment), "version": version,
                "latest_version_id": attachment["latest_version_id"], "job": _job(dict(row) if row else None)}

    @router.get("/material-sets/{set_id}/attachments/{attachment_id}/versions")
    @_guard
    def versions(set_id: str, attachment_id: str, before_number: int | None = Query(None, ge=1), limit: int = Query(50, ge=1, le=100)):
        repo = require()
        with repo._lock:
            repo.get_attachment(set_id, attachment_id)
            rows = repo._conn.execute("SELECT id FROM meeting_attachment_versions WHERE attachment_id=? AND number<? ORDER BY number DESC LIMIT ?",
                                     (attachment_id, before_number or 2**63 - 1, limit + 1)).fetchall()
            items = [repo.get_version(set_id, attachment_id, row[0]) for row in rows[:limit]]
        return {"items": items, "has_more": len(rows) > limit, "next_before_number": items[-1]["number"] if len(rows) > limit else None}

    @router.get("/material-sets/{set_id}/attachments/{attachment_id}/versions/{version_id}")
    @_guard
    def version_get(set_id: str, attachment_id: str, version_id: str):
        return {"version": require().get_version(set_id, attachment_id, version_id)}

    @router.get("/material-sets/{set_id}/attachments/{attachment_id}/versions/{version_id}/spans")
    @_guard
    def spans(set_id: str, attachment_id: str, version_id: str, offset: int = Query(0, ge=0, le=5000), limit: int = Query(50, ge=1, le=100)):
        return require().spans(set_id, attachment_id, version_id, offset=offset, limit=limit)

    @router.post("/material-sets/{set_id}/attachments/{attachment_id}/versions", status_code=201)
    @_guard
    def create_version(set_id: str, attachment_id: str, payload: _VersionCreate):
        repo = require()
        version = repo.add_version(set_id, attachment_id, **payload.model_dump())
        return {"version": version, "attachment": _attachment(repo.get_attachment(set_id, attachment_id))}

    @router.put("/material-sets/{set_id}/attachments/{attachment_id}/usage")
    @_guard
    def usage(set_id: str, attachment_id: str, payload: _Usage):
        repo = require()
        attachment = repo.usage(set_id, attachment_id, **payload.model_dump())
        return {"attachment": _attachment(attachment), "material_set": repo.get_set(set_id)}

    @router.delete("/material-sets/{set_id}/attachments/{attachment_id}", status_code=202)
    @_guard
    def delete_attachment(set_id: str, attachment_id: str, expected_revision: int = Query(..., ge=1)):
        attachment, job = require().delete_attachment(set_id, attachment_id, expected_revision=expected_revision)
        return {"attachment": _attachment(attachment), "job": _job(job)}

    @router.delete("/material-sets/{set_id}", status_code=202)
    @_guard
    def delete_set(set_id: str, expected_revision: int = Query(..., ge=1)):
        material_set, jobs = require().delete_draft(set_id, expected_revision=expected_revision)
        return {"material_set": material_set, "jobs": [_job(job) for job in jobs]}

    @router.get("/material-sets/{set_id}/jobs")
    @_guard
    def jobs(set_id: str, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        require()
        page = queue.list(set_id, limit=limit, before_id=cursor)
        return {**page, "items": [_job(item) for item in page["items"]]}

    @router.get("/material-sets/{set_id}/jobs/{job_id}")
    @_guard
    def job_get(set_id: str, job_id: str):
        require()
        return {"job": _job(queue.get(job_id, set_id=set_id))}

    @router.post("/material-sets/{set_id}/jobs/{job_id}/retry")
    @_guard
    def job_retry(set_id: str, job_id: str):
        require()
        return {"job": _job(queue.retry(set_id, job_id))}

    @router.post("/material-sets/{set_id}/jobs/{job_id}/cancel")
    @_guard
    def job_cancel(set_id: str, job_id: str):
        require()
        return {"job": _job(queue.cancel(set_id, job_id))}

    @router.get("/meetings/{meeting_id}/sources/{source_id}")
    @_guard
    def source_get(meeting_id: str, source_id: str, allow_historical: bool = False):
        repo = require()
        with repo._lock:
            if source_id.startswith("material_span_"):
                source = read_attachment_source(repo._conn, meeting_id, source_id, allow_historical=allow_historical)
            else:
                source = library.read_source(source_id, meeting_id=meeting_id)
        return {"source": source}

    app.include_router(router)
    if runner is not None:
        app.router.add_event_handler("startup", runner.start)
        app.router.add_event_handler("shutdown", runner.stop)

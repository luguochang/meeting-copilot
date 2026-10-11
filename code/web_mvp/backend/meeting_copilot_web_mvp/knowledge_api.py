"""Local project sources API. Public projections never disclose managed paths or leases."""

from __future__ import annotations

import asyncio
import base64
from functools import wraps
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Literal

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .document_import import DocumentImportService, MAX_UPLOAD_BYTES
from .knowledge_jobs import KnowledgeJobQueue, KnowledgeJobRunner
from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError, MAX_CONTENT_CHARACTERS


ERROR_MESSAGES = {
    "pdf_parser_unavailable": "PDF 解析组件未就绪，请安装组件后重试。",
    "pdf_ocr_required": "此 PDF 没有可提取文字，需要 OCR 或上传文字版。",
    "pdf_partial_ocr_required": "部分页面没有可提取文字，预览可能不完整，请核对或补充。",
    "encrypted_pdf_not_supported": "暂不支持加密 PDF，请上传解除加密的文字版。",
    "encrypted_docx_not_supported": "暂不支持加密 Word 文件，请上传解除加密的版本。",
    "docx_archive_too_large": "Word 文件解压大小、文件数量或压缩比例超过限制。",
    "unsafe_docx_archive_path": "Word 文件包含不安全的内部路径。",
    "unsafe_docx_xml": "Word 文件包含不支持的 XML 声明。",
    "invalid_docx_document": "无法读取此 Word 文件，请重新导出 DOCX 后上传。",
    "invalid_pdf_document": "无法读取此 PDF，请重新导出后上传。",
    "document_has_no_text": "文件中没有可提取的文字。",
    "invalid_text_encoding": "无法识别文字编码，或文件包含二进制内容。",
    "extracted_text_too_large": "提取文字超过 200 万字或片段数量限制。",
    "pdf_page_too_large": "PDF 页面内容超过资源限制。",
    "pdf_too_many_pages": "PDF 超过 500 页限制。",
    "conversion_timeout": "转换超过 60 秒，可重试或拆分文件后上传。",
    "conversion_process_failed": "转换进程失败或超过资源限制，可重试或重新导出文件。",
    "input_superseded": "转换期间资料已被修改，本次结果已作废，以保留你的修改。",
    "original_write_failed": "原文件保存失败，请检查本地存储后重试。",
    "user_cancelled": "已取消转换，可手动重试。",
    "document_deleted": "资料已删除，相关转换已取消。",
    "knowledge_worker_failed": "后台处理失败，可手动重试。",
    "unsafe_original_path": "原件路径不安全，已停止清理。",
}


def _document(value: dict[str, Any]) -> dict[str, Any]:
    return {**{key: item for key, item in value.items() if key not in {"original_relative_path", "original_sha256"}},
            "error_message": ERROR_MESSAGES.get(value.get("error_code"), "后台处理失败，可手动重试。") if value.get("error_code") else None}


def _job(value: dict[str, Any]) -> dict[str, Any]:
    return {**{key: item for key, item in value.items() if key not in {"lease_token", "lease_until_ms"}},
            "error_message": ERROR_MESSAGES.get(value.get("error_code"), "后台处理失败，可手动重试。") if value.get("error_code") else None}


def _cursor(value: str | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if len(value) > 1000:
        raise ValueError("invalid cursor")
    try:
        result = json.loads(base64.urlsafe_b64decode(value.encode("ascii")))
        if not isinstance(result, dict) or set(result) != {"id", "updated_at_ms"}:
            raise ValueError("invalid cursor")
        return result
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid cursor") from exc


def _page(value: dict[str, Any], project: Callable = lambda value: value) -> dict[str, Any]:
    cursor = value["next_cursor"]
    return {"items": [project(item) for item in value["items"]], "has_more": value["has_more"],
            "next_cursor": base64.urlsafe_b64encode(json.dumps(cursor).encode()).decode() if cursor else None}


def _guard(function: Callable) -> Callable:
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except KnowledgeConflict:
            raise HTTPException(409, detail={"code": "revision_conflict", "message": "资料已被更新，请刷新后再操作。"}) from None
        except KnowledgeScopeError:
            raise HTTPException(409, detail={"code": "invalid_scope", "message": "资料不属于所选范围，或已归档、禁用、删除、尚未确认。"}) from None
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found", "message": "所选项目、资料、版本或作业不存在。"}) from None
        except (ValueError, TypeError):
            raise HTTPException(400, detail={"code": "invalid_request", "message": "参数不正确，请检查文件格式、大小、名称或字段。"}) from None
        except sqlite3.Error:
            raise HTTPException(503, detail={"code": "storage_unavailable", "message": "本地存储暂时不可用，请稍后重试。"}) from None
    return wrapped


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(_Payload):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ProjectEdit(_Payload):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    archived: bool | None = None
    expected_revision: int = Field(ge=1)


class FolderCreate(_Payload):
    project_id: str
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class FolderEdit(_Payload):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None
    expected_revision: int = Field(ge=1)


class DocumentManual(_Payload):
    project_id: str
    folder_id: str | None = None
    kind: Literal["note", "meeting_document"] = "note"
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARACTERS)
    source_meeting_id: str | None = None


class DocumentEdit(_Payload):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    folder_id: str | None = None
    status: Literal["active", "disabled", "deleted"] | None = None
    expected_revision: int = Field(ge=1)


class VersionCreate(_Payload):
    content: str = Field(min_length=1, max_length=MAX_CONTENT_CHARACTERS)
    expected_revision: int = Field(ge=1)
    base_version_id: str | None = None


class VersionConfirm(_Payload):
    version_id: str
    expected_revision: int = Field(ge=1)


class MeetingContext(_Payload):
    project_id: str | None = None
    folder_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=3000)
    expected_revision: int | None = Field(default=None, ge=0)


class SearchRequest(_Payload):
    query: str = Field(min_length=1, max_length=200)
    meeting_id: str | None = None
    project_id: str | None = None
    folder_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=3000)
    limit: int = Field(default=6, ge=1, le=20)


class _UploadLimitMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        limited = scope["type"] == "http" and scope.get("method") == "POST" and scope.get("path") == "/v2/knowledge/documents"
        if not limited:
            return await self.app(scope, receive, send)
        maximum = MAX_UPLOAD_BYTES + 1024 * 1024  # bounded multipart metadata overhead
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if length > maximum:
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


def mount_knowledge_api(app: FastAPI, repository: KnowledgeRepository | None, data_dir: Path | None) -> None:
    queue = KnowledgeJobQueue(repository) if repository is not None else None
    importer = DocumentImportService(repository, data_dir) if repository is not None and data_dir is not None else None
    runner = KnowledgeJobRunner(queue, importer) if queue is not None and importer is not None else None
    app.state.knowledge_repository, app.state.knowledge_jobs, app.state.knowledge_importer = repository, queue, importer
    app.state.knowledge_job_runner = runner
    app.add_middleware(_UploadLimitMiddleware)
    router = APIRouter(prefix="/v2/knowledge", tags=["knowledge"])

    def require() -> KnowledgeRepository:
        if repository is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "资料库需要启用本地 SQLite 存储。"})
        return repository

    @router.get("/projects")
    @_guard
    def projects(limit: int = Query(100, ge=1, le=200), cursor: str | None = None, include_archived: bool = False):
        return _page(require().list_projects(limit=limit, cursor=_cursor(cursor), include_archived=include_archived))

    @router.post("/projects", status_code=201)
    @_guard
    def project_create(payload: ProjectCreate):
        return {"project": require().create_project(**payload.model_dump())}

    @router.patch("/projects/{project_id}")
    @_guard
    def project_edit(project_id: str, payload: ProjectEdit):
        return {"project": require().update_project(project_id, **payload.model_dump(exclude_unset=True))}

    @router.get("/folders")
    @_guard
    def folders(project_id: str, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().list_folders(project_id, limit=limit, cursor=_cursor(cursor)))

    @router.post("/folders", status_code=201)
    @_guard
    def folder_create(payload: FolderCreate):
        return {"folder": require().create_folder(**payload.model_dump())}

    @router.patch("/folders/{folder_id}")
    @_guard
    def folder_edit(folder_id: str, payload: FolderEdit):
        return {"folder": require().update_folder(folder_id, **payload.model_dump(exclude_unset=True))}

    @router.get("/documents")
    @_guard
    def documents(project_id: str, folder_id: str | None = None, status: str = "all", query: str = "",
                  limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().list_documents(project_id, folder_id=folder_id, status=status, query=query, limit=limit,
                                             cursor=_cursor(cursor)), _document)

    def stage(project_id, filename, data, folder_id, title, content_type):
        require()
        document = importer.stage_document(project_id, filename, data, folder_id=folder_id, title=title, content_type=content_type)
        if document["import_status"] == "failed":
            raise HTTPException(503, detail={"code": document["error_code"], "message": ERROR_MESSAGES["original_write_failed"]})
        job = queue.enqueue(document["id"])
        return {"document": _document(document), "job": _job(job)}

    stage = _guard(stage)

    @router.post("/documents", status_code=202)
    async def document_upload(file: UploadFile = File(...), project_id: str = Form(...), folder_id: str | None = Form(None),
                              title: str | None = Form(None)):
        require()
        try:
            data = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(data) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, detail={"code": "upload_too_large", "message": "文件超过 20 MiB 上传限制。"})
            return await asyncio.to_thread(stage, project_id, file.filename or "", data, folder_id or None, title, file.content_type)
        finally:
            await file.close()

    @router.post("/documents/manual", status_code=201)
    @_guard
    def document_manual(payload: DocumentManual):
        library = require()
        with library._write():
            document = library.create_document(payload.project_id, payload.title, folder_id=payload.folder_id, kind=payload.kind,
                                                source_meeting_id=payload.source_meeting_id)
            version = library.add_version(document["id"], payload.content, origin="meeting_user_final" if payload.kind == "meeting_document" else "user")
            document = library.set_import_status(document["id"], "extracted")
        return {"document": _document(document), "version": version}

    @router.get("/documents/{document_id}")
    @_guard
    def document_get(document_id: str, version_id: str | None = None):
        library = require()
        document = library.get_document(document_id)
        history = library.list_versions(document_id, limit=1)["items"]
        version = library.get_version(document_id, version_id) if history else None
        latest = history[0] if history else None
        return {"document": _document(document), "version": version,
                "latest_draft_version_id": latest["id"] if latest and latest["confirmed_at_ms"] is None else None}

    @router.patch("/documents/{document_id}")
    @_guard
    def document_edit(document_id: str, payload: DocumentEdit):
        library = require()
        fields = payload.model_dump(exclude_unset=True)
        if fields.get("status") == "deleted":
            if len(set(fields) - {"status", "expected_revision"}):
                raise ValueError("delete cannot combine edits")
            document, job = queue.delete_document(document_id, expected_revision=payload.expected_revision)
            return {"document": _document(document), "job": _job(job)}
        return {"document": _document(library.update_document(document_id, **fields))}

    @router.get("/documents/{document_id}/versions")
    @_guard
    def versions(document_id: str, before_number: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=200)):
        return require().list_versions(document_id, before_number=before_number, limit=limit)

    @router.get("/documents/{document_id}/versions/{version_id}")
    @_guard
    def version_get(document_id: str, version_id: str):
        return {"version": require().get_version(document_id, version_id)}

    @router.post("/documents/{document_id}/versions", status_code=201)
    @_guard
    def version_create(document_id: str, payload: VersionCreate):
        library = require()
        version = library.add_version(document_id, **payload.model_dump())
        return {"version": version, "document": _document(library.get_document(document_id))}

    @router.post("/documents/{document_id}/confirm")
    @_guard
    def version_confirm(document_id: str, payload: VersionConfirm):
        return {"document": _document(require().confirm_version(document_id, **payload.model_dump()))}

    @router.get("/meetings/{meeting_id}/context")
    @_guard
    def context_get(meeting_id: str):
        return {"context": require().get_meeting_context(meeting_id)}

    @router.put("/meetings/{meeting_id}/context")
    @_guard
    def context_set(meeting_id: str, payload: MeetingContext):
        return {"context": require().bind_meeting_context(meeting_id, **payload.model_dump())}

    @router.post("/search")
    @_guard
    def search(payload: SearchRequest):
        library = require()
        if payload.meeting_id is not None:
            if payload.project_id is not None or payload.folder_id is not None or payload.document_ids is not None:
                raise ValueError("meeting scope cannot be overridden")
            store = getattr(app.state, "meeting_preparation_store", None)
            preparation = store.get(payload.meeting_id) if store is not None else None
            items = library.search_for_meeting(payload.meeting_id, payload.query, limit=payload.limit,
                                              memory_scene=preparation.preset_id if preparation is not None else "general")
            from .meeting_materials import MeetingMaterialsRepository, material_input_context

            with library._lock:
                has_materials = bool(material_input_context(library._conn, payload.meeting_id))
            if has_materials:
                material_items = MeetingMaterialsRepository(library).search_for_meeting(payload.meeting_id, payload.query, limit=payload.limit)
                combined = []
                for index in range(max(len(items), len(material_items))):
                    if index < len(material_items):
                        combined.append(material_items[index])
                    if index < len(items):
                        combined.append(items[index])
                items = combined[:payload.limit]
        else:
            if payload.project_id is None:
                raise ValueError("search requires project or meeting scope")
            items = library.search(payload.query, project_id=payload.project_id, folder_id=payload.folder_id,
                                   document_ids=payload.document_ids, limit=payload.limit)
        return {"items": items}

    @router.get("/sources/{source_id}")
    @_guard
    def source_get(source_id: str, meeting_id: str | None = None, project_id: str | None = None):
        return {"source": require().read_source(source_id, meeting_id=meeting_id, project_id=project_id)}

    @router.get("/jobs")
    @_guard
    def jobs(project_id: str, status: str | None = None, document_id: str | None = None,
             limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        require()
        return _page(queue.list(project_id, status=status, document_id=document_id, limit=limit, cursor=_cursor(cursor)), _job)

    @router.get("/jobs/{job_id}")
    @_guard
    def job_get(job_id: str):
        require()
        return {"job": _job(queue.get(job_id))}

    @router.post("/jobs/{job_id}/retry")
    @_guard
    def job_retry(job_id: str):
        require()
        return {"job": _job(queue.retry(job_id))}

    @router.post("/jobs/{job_id}/cancel")
    @_guard
    def job_cancel(job_id: str):
        require()
        return {"job": _job(queue.cancel(job_id))}

    app.include_router(router)
    if runner is not None:
        app.router.add_event_handler("startup", runner.start)
        app.router.add_event_handler("shutdown", runner.stop)

"""Paged notes and explicit source-to-library operations, mounted before dynamic note routes."""

from __future__ import annotations

from functools import wraps
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import Field

from .knowledge_api import _Payload, _cursor, _document, _guard, _page
from .knowledge_repository import KnowledgeRepository
from .source_library_import import LibrarySourceError, SourceLibraryImporter


class FinalImport(_Payload):
    document_kind: Literal["minutes", "decisions", "action_items", "risks", "transcript"] = "minutes"
    expected_source_version: int = Field(ge=1)
    expected_document_revision: int | None = Field(default=None, ge=1)
    folder_id: str | None = None


class NoteImport(_Payload):
    project_id: str
    expected_source_version: int = Field(ge=1)
    expected_document_revision: int | None = Field(default=None, ge=1)
    folder_id: str | None = None


MESSAGES = {"human_final_required": "请先核对并保存人工终稿，再加入资料库。",
            "final_too_large": "终稿为空或超过两万字，请核对并精简后重试。",
            "project_required": "请先为会议选择项目，再加入资料库。",
            "source_deleted": "原文已删除，无法加入资料库。",
            "source_needs_review": "原文需要核对或已归档，请先处理原文。",
            "linked_document_unavailable": "此前入库资料已删除或禁用，请先处理现有资料。"}


def _source_guard(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except LibrarySourceError as exc:
            raise HTTPException(409, detail={"code": exc.code, "message": MESSAGES.get(exc.code, "原文无法加入资料库。")}) from None
    return _guard(wrapped)


def mount_source_library_api(app: FastAPI, repository: KnowledgeRepository | None, notes_repository) -> None:
    importer = SourceLibraryImporter(repository) if repository is not None else None
    app.state.source_library_importer = importer
    router = APIRouter(tags=["source-library"])

    def require():
        if importer is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "资料库需要启用本地 SQLite 存储。"})
        return importer

    def public(result):
        return {**result, "document": _document(result["document"])}

    @router.get("/v2/notes/page")
    @_guard
    def notes_page(meeting_id: str | None = None, status: str = "active", query: str = "",
                   limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        if notes_repository is None:
            raise HTTPException(503, detail={"code": "notes_storage_unavailable", "message": "笔记需要启用本地 SQLite 存储。"})
        return _page(notes_repository.list_notes_page(meeting_id=meeting_id, status=status, query=query,
                                                      limit=limit, cursor=_cursor(cursor)))

    @router.post("/v2/knowledge/meetings/{meeting_id}/import-final", status_code=201)
    @_source_guard
    def final_import(meeting_id: str, payload: FinalImport):
        return public(require().import_final(meeting_id, **payload.model_dump()))

    @router.post("/v2/knowledge/notes/{note_id}/import", status_code=201)
    @_source_guard
    def note_import(note_id: str, payload: NoteImport):
        return public(require().import_note(note_id, **payload.model_dump()))

    app.include_router(router)

"""Finite explicit memory API; confirmation and conflicting replacement are user actions."""
from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .knowledge_api import _guard
from .knowledge_repository import KnowledgeRepository
from .user_memory import MemoryTermConflict, UserMemory


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "project", "meeting"]
    scope_id: str = ""
    scene: Literal["", "general", "decision", "project", "interview", "brainstorm"] = ""
    kind: Literal["term", "preference"]
    memory_key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=64)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=6)
    origin: Literal["user", "ai"] = "user"


class MemoryEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    status: Literal["active", "disabled", "deleted"] | None = None
    value: str | None = Field(default=None, min_length=1, max_length=64)
    aliases: list[str] | None = Field(default=None, max_length=8)
    replace_active_id: str | None = None
    replace_active_revision: int | None = Field(default=None, ge=1)


def mount_memory_api(app: FastAPI, repository: KnowledgeRepository | None) -> None:
    memory = UserMemory(repository) if repository is not None else None
    app.state.user_memory = memory
    router = APIRouter(prefix="/v2/knowledge")

    def require() -> UserMemory:
        if memory is None:
            raise HTTPException(503, "本地资料存储未就绪")
        return memory

    @router.get("/memories")
    @_guard
    def memories(scope: Literal["global", "project", "meeting"], scope_id: str = "", limit: int = Query(50, ge=1, le=200), cursor: str | None = None):
        return require().list(scope=scope, scope_id=scope_id, limit=limit, cursor=cursor)

    @router.post("/memories", status_code=201)
    @_guard
    def create(payload: MemoryCreate):
        return require().create(**payload.model_dump())

    @router.patch("/memories/{memory_id}")
    @_guard
    def update(memory_id: str, payload: MemoryEdit):
        try:
            result = require().update(memory_id, **payload.model_dump())
        except MemoryTermConflict as exc:
            raise HTTPException(409, detail={"code": "term_spelling_conflict", "conflicting_memory_id": exc.memory_id,
                                            "message": "同范围已有术语使用相同拼写或错误写法，请先修改或禁用冲突术语。"}) from None
        asr = getattr(app.state, "asr_stream", None)
        preparation_store = getattr(app.state, "meeting_preparation_store", None)
        if asr is not None and preparation_store is not None:
            for row in repository._conn.execute("SELECT id FROM meetings WHERE state='live'").fetchall():
                preparation = preparation_store.get(row[0])
                resolved = require().resolve(row[0], preparation.preset_id if preparation else "general")
                explicit = list(preparation.hotwords) if preparation else []
                words = list(dict.fromkeys([*explicit, *resolved["hotwords"]]))
                accepted = []
                for word in words:
                    if len(accepted) >= 50 or sum(map(len, accepted)) + len(word) > 2000:
                        break
                    accepted.append(word)
                asr.set_session_hotwords(row[0], accepted)
        return result

    @router.get("/meetings/{meeting_id}/memory-context")
    @_guard
    def resolved(meeting_id: str, scene: str = "general"):
        return require().resolve(meeting_id, scene)

    @router.get("/memory-preview")
    @_guard
    def preview(project_id: str | None = None, scene: str = "general"):
        return require().preview_preferences(project_id, scene)

    @router.get("/memories/export")
    @_guard
    def export(scope: Literal["global", "project", "meeting"], scope_id: str = ""):
        pages, cursor = [], None
        for _ in range(50):
            page = require().list(scope=scope, scope_id=scope_id, limit=200, cursor=cursor)
            pages.extend(page["items"])
            if not page["has_more"]:
                return {"format": "talktrace.memory.v1", "scope": scope, "scope_id": scope_id, "items": pages}
            cursor = page["next_cursor"]
        raise HTTPException(413, "记忆导出超过10000条，请分范围导出")

    app.include_router(router)

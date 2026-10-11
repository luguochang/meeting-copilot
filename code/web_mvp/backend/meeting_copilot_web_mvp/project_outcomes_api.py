"""Explicit confirmation boundaries for project outcomes; no automatic model work."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .knowledge_api import _cursor, _guard, _page
from .knowledge_repository import KnowledgeRepository
from .project_outcomes import ProjectOutcomes


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StateCreate(_Payload):
    type: Literal["decision", "task", "question"]
    text: str = Field(min_length=1, max_length=2000)
    status: Literal["active", "done", "cancelled", "unknown", "discarded"] = "active"
    owner: str | None = Field(default=None, max_length=200)
    due: str | None = Field(default=None, max_length=200)
    source_meeting_id: str | None = None
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=12)
    expected_project_revision: int = Field(ge=1)


class StateEdit(_Payload):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    status: Literal["active", "done", "cancelled", "unknown", "discarded"] | None = None
    owner: str | None = Field(default=None, max_length=200)
    due: str | None = Field(default=None, max_length=200)
    source_refs: list[dict[str, Any]] | None = Field(default=None, max_length=12)
    expected_revision: int = Field(ge=1)
    expected_project_revision: int = Field(ge=1)


class Change(_Payload):
    action: Literal["create", "supersede", "update", "discard"] = "create"
    item_id: str | None = None
    type: Literal["decision", "task", "question"] | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    status: Literal["active", "done", "cancelled", "unknown", "discarded"] | None = None
    owner: str | None = Field(default=None, max_length=200)
    due: str | None = Field(default=None, max_length=200)
    source_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=12)


class ProposalCreate(_Payload):
    meeting_id: str | None = None
    origin: Literal["ai", "user"] = "ai"
    changes: list[Change] = Field(min_length=1, max_length=40)
    expected_project_revision: int = Field(ge=1)


class ProposalConfirm(_Payload):
    expected_revision: int = Field(ge=1)
    expected_project_revision: int = Field(ge=1)


class ProposalReject(_Payload):
    expected_revision: int = Field(ge=1)


class BriefPreview(_Payload):
    folder_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=3000)
    limit: int = Field(default=20, ge=1, le=20)


def mount_project_outcomes_api(app: FastAPI, repository: KnowledgeRepository | None) -> None:
    service = ProjectOutcomes(repository) if repository is not None else None
    app.state.project_outcomes = service
    router = APIRouter(prefix="/v2/knowledge", tags=["project-outcomes"])

    def require() -> ProjectOutcomes:
        if service is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "项目状态需要启用本地资料库。"})
        return service

    @router.get("/projects/{project_id}/state")
    @_guard
    def state_list(project_id: str, status: str | None = None, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        page = require().list(project_id, status=status, limit=limit, cursor=_cursor(cursor))
        return {**_page(page), "project_revision": page["project_revision"]}

    @router.post("/projects/{project_id}/state", status_code=201)
    @_guard
    def state_create(project_id: str, payload: StateCreate):
        fields = payload.model_dump()
        fields["kind"] = fields.pop("type")
        item = require().create(project_id, **fields)
        return {"item": item, "project_revision": repository.get_project(project_id)["revision"]}

    @router.patch("/state/{item_id}")
    @_guard
    def state_edit(item_id: str, payload: StateEdit):
        fields = payload.model_dump(exclude_unset=True)
        expected_revision = fields.pop("expected_revision")
        expected_project_revision = fields.pop("expected_project_revision")
        item = require().update(item_id, fields, expected_revision=expected_revision, expected_project_revision=expected_project_revision)
        return {"item": item, "project_revision": repository.get_project(item["project_id"])["revision"]}

    @router.get("/state/{item_id}/history")
    @_guard
    def state_history(item_id: str, before_revision: int | None = Query(None, ge=1), limit: int = Query(100, ge=1, le=200)):
        return require().history(item_id, before_revision=before_revision, limit=limit)

    @router.get("/projects/{project_id}/proposals")
    @_guard
    def proposal_list(project_id: str, status: str | None = None, limit: int = Query(100, ge=1, le=200), cursor: str | None = None):
        return _page(require().proposals(project_id, status=status, limit=limit, cursor=_cursor(cursor)))

    @router.post("/projects/{project_id}/proposals", status_code=201)
    @_guard
    def proposal_create(project_id: str, payload: ProposalCreate):
        changes = [change.model_dump(exclude_unset=True) for change in payload.changes]
        return {"proposal": require().propose(project_id, changes, origin=payload.origin, meeting_id=payload.meeting_id,
                                               expected_project_revision=payload.expected_project_revision)}

    @router.get("/proposals/{proposal_id}")
    @_guard
    def proposal_get(proposal_id: str):
        return {"proposal": require().get_proposal(proposal_id)}

    @router.post("/proposals/{proposal_id}/confirm")
    @_guard
    def proposal_confirm(proposal_id: str, payload: ProposalConfirm):
        return require().confirm(proposal_id, **payload.model_dump())

    @router.post("/proposals/{proposal_id}/reject")
    @_guard
    def proposal_reject(proposal_id: str, payload: ProposalReject):
        return {"proposal": require().reject(proposal_id, **payload.model_dump())}

    @router.get("/projects/{project_id}/wiki")
    @_guard
    def wiki(project_id: str):
        return require().wiki(project_id)

    @router.post("/projects/{project_id}/brief-preview")
    @_guard
    def brief_preview(project_id: str, payload: BriefPreview):
        return require().brief_preview(project_id, **payload.model_dump())

    @router.get("/meetings/{meeting_id}/project-state")
    @_guard
    def meeting_state(meeting_id: str, limit: int = Query(40, ge=1, le=100)):
        return require().for_meeting(meeting_id, limit=limit)

    @router.get("/outcome-sources/{source_id}")
    @_guard
    def outcome_source(source_id: str, project_id: str, source_type: Literal["document", "review_document", "meeting_entity"] = "document", meeting_id: str | None = None):
        return {"source": require().source(source_id, project_id, source_type=source_type, meeting_id=meeting_id)}

    app.include_router(router)

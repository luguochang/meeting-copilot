"""Explicit local ZIP download; no upload, model work or durable mutations."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, FastAPI, HTTPException
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from .knowledge_repository import KnowledgeRepository
from .project_export import ProjectExportError, build_project_export


def mount_project_export_api(app: FastAPI, repository: KnowledgeRepository | None) -> None:
    router = APIRouter(prefix="/v2/knowledge", tags=["project-export"])

    @router.get("/projects/{project_id}/export")
    def export_project(project_id: str):
        if repository is None:
            raise HTTPException(503, detail={"code": "knowledge_storage_unavailable", "message": "项目导出需要启用本地资料库。"})
        try:
            result = build_project_export(repository, project_id)
        except ProjectExportError as error:
            raise HTTPException(error.status_code, detail={"code": error.code, "message": error.message}) from None
        except KeyError:
            raise HTTPException(404, detail={"code": "not_found", "message": "项目不存在。"}) from None
        except (ValueError, TypeError):
            raise HTTPException(409, detail={"code": "project_export_invalid_source", "message": "项目资料或来源无法验证，请先核对资料。"}) from None
        except sqlite3.Error:
            raise HTTPException(503, detail={"code": "storage_unavailable", "message": "本地资料库暂时不可用，请稍后重试。"}) from None
        except OSError:
            raise HTTPException(503, detail={"code": "project_export_io_failed", "message": "导出文件生成失败，请检查本地存储后重试。"}) from None

        def chunks():
            try:
                while chunk := result.file.read(64 * 1024):
                    yield chunk
            finally:
                result.file.close()

        return StreamingResponse(chunks(), media_type="application/zip", background=BackgroundTask(result.file.close), headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"', "Content-Length": str(result.size_bytes),
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    app.include_router(router)

"""Path-free API for explicit local historical transcript recovery."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .asr_resource_guard import RESOURCE_ERROR_MESSAGES
from .transcript_recovery import TranscriptRecoveryService
from .transcript_recovery_audio import RecoveryAudioError
from .transcript_recovery_persistence import RecoveryError

RECOVERY_ERROR_MESSAGES = {
    "recovery_request_invalid": "补识别请求格式无效，请刷新后重新选择范围。",
    "recovery_meeting_not_found": "会议不存在或已删除。",
    "recovery_job_not_found": "补识别任务不存在。",
    "recovery_audio_not_ready": "该音频尚未封存并整理完成。",
    "recovery_audio_invalid": "保存音频的格式与来源记录不一致。",
    "recovery_audio_capacity_exceeded": "保存音频超出本次恢复的容量限制。",
    "recovery_audio_path_invalid": "音频不属于本场允许的保存来源。",
    "recovery_audio_unavailable": "保存音频暂时不可读取。",
    "recovery_audio_changed": "保存音频已变化，请重新选择来源。",
    "recovery_timeline_unknown": "无法确认该音频与原会议的时间映射。",
    "recovery_coverage_unknown": "现有文字的时间范围未知，无法安全补识别。",
    "recovery_range_invalid": "请选择本来源内不超过五分钟的有效采样范围。",
    "recovery_source_changed": "音频或该范围文字已变化，旧候选不能合并。",
    "recovery_range_covered": "该范围已有文字，补识别不会覆盖原文。",
    "recovery_range_overlap": "已有任务覆盖该范围，请先处理原任务。",
    "recovery_version_conflict": "任务已更新，请刷新后再操作。",
    "recovery_execution_superseded": "本次执行已取消或被更新。",
    "recovery_attempts_exhausted": "该任务已达到三次识别上限。",
    "recovery_capture_active": "正在录音，补识别等待采集结束。",
    "recovery_worker_interrupted": "上次执行被中断，可明确重试。",
    "recovery_retry_not_allowed": "当前任务状态不允许重试。",
    "recovery_already_merged": "候选已经合并，不能再次修改。",
    "recovery_candidate_not_ready": "尚无可确认的候选文字。",
    "recovery_merge_capture_active": "请先结束会议和录音，再确认合并。",
    "recovery_candidate_invalid": "补识别未返回有效的有界候选文字。",
    "recovery_components_missing": "本地文件识别组件尚不可用。",
    "recovery_no_speech": "该范围未返回文字，可能包含静音。",
    "recovery_asr_failed": "本地补识别未完成，可明确重试。",
    "recovery_asr_timeout": "本地补识别超过三分钟，已停止本次尝试。",
    "recovery_storage_unavailable": "补识别存储暂时不可用。",
    **RESOURCE_ERROR_MESSAGES,
}


class RecoveryRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def validated(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                # Do not echo submitted paths, candidate text, or arbitrary fields.
                return JSONResponse(status_code=422, content={"error": {
                    "code": "recovery_request_invalid", "message": RECOVERY_ERROR_MESSAGES["recovery_request_invalid"]}})
        return validated


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: Literal["microphone", "system_audio"]
    epoch: StrictInt = Field(ge=0)
    start_sample: StrictInt = Field(ge=0)
    end_sample: StrictInt = Field(gt=0)
    audio_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    coverage_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class RecoveryVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt | None = Field(default=None, gt=0)


class RecoveryMergeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_version: StrictInt = Field(gt=0)
    text: str | None = Field(default=None, max_length=20_000)


def mount_transcript_recovery_api(app: FastAPI, persistence: Any, data_dir: Path | None) -> None:
    service = TranscriptRecoveryService(persistence, data_dir) if persistence is not None and data_dir is not None else None
    app.state.transcript_recovery = service
    router = APIRouter(route_class=RecoveryRoute)

    def error_response(error: Exception) -> JSONResponse:
        code = getattr(error, "code", None)
        if code not in RECOVERY_ERROR_MESSAGES:
            code = "recovery_meeting_not_found" if type(error).__name__ == "MeetingDeletedError" else "recovery_storage_unavailable"
        status = 404 if code in {"recovery_meeting_not_found", "recovery_job_not_found"} else 503 if code == "recovery_storage_unavailable" else 409
        return JSONResponse(status_code=status, content={"error": {"code": code, "message": RECOVERY_ERROR_MESSAGES[code]}})

    async def call(method: str, *args: Any, status: int = 200) -> JSONResponse:
        if service is None:
            return error_response(RecoveryError("recovery_storage_unavailable"))
        try:
            result = await asyncio.to_thread(getattr(service, method), *args)
        except (RecoveryError, RecoveryAudioError, sqlite3.Error, OSError, RuntimeError, KeyError) as exc:
            return error_response(exc)
        return JSONResponse(status_code=status, content=result if method == "describe" else {"job": result})

    path = "/v2/meetings/{meeting_id}/transcript-recovery"

    @router.get(path)
    async def describe(meeting_id: str) -> JSONResponse:
        return await call("describe", meeting_id)

    @router.post(path)
    async def create(meeting_id: str, request: RecoveryRequest) -> JSONResponse:
        return await call("create", meeting_id, request.model_dump(), status=202)

    @router.post(path + "/{job_id}/cancel")
    async def cancel(meeting_id: str, job_id: str, request: RecoveryVersionRequest) -> JSONResponse:
        return await call("cancel", meeting_id, job_id, request.expected_version)

    @router.post(path + "/{job_id}/retry")
    async def retry(meeting_id: str, job_id: str, request: RecoveryVersionRequest) -> JSONResponse:
        return await call("retry", meeting_id, job_id, request.expected_version, status=202)

    @router.post(path + "/{job_id}/merge")
    async def merge(meeting_id: str, job_id: str, request: RecoveryMergeRequest) -> JSONResponse:
        return await call("merge", meeting_id, job_id, request.candidate_version, request.text)

    app.include_router(router)

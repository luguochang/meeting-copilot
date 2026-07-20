from __future__ import annotations

import importlib.util
import asyncio
import hmac
import json
import os
import platform
import structlog
import subprocess
import threading
import time
import uuid
import httpx
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal
import unicodedata
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response, UploadFile, File, Form, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from meeting_copilot_web_mvp.logging_config import (
    ManagedRotatingLogStream,
    configure_logging,
    get_logger,
)
from meeting_copilot_web_mvp.meeting_preparation import MeetingPreparationStore
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp import asr_stream
from meeting_copilot_web_mvp import batch_transcribe
from meeting_copilot_web_mvp import diagnostic_bundle
from meeting_copilot_web_mvp import provider_config_runtime
from meeting_copilot_web_mvp.provider_config_store import ProviderConfigStore
from meeting_copilot_web_mvp.data_governance import (
    DataGovernanceService,
    normalize_deletion_scope,
)
from meeting_copilot_web_mvp.docx_export import render_docx
from meeting_copilot_web_mvp import asr_correct
from meeting_copilot_web_mvp import auto_suggestion_orchestrator
from meeting_copilot_web_mvp import audio_assets
from meeting_copilot_web_mvp import desktop_parent_watchdog
from meeting_copilot_web_mvp import realtime_transcript_correction
from meeting_copilot_web_mvp.canonical_transcript import project_canonical_transcript
from meeting_copilot_web_mvp.llm_lane_locks import LaneLockRegistry
from meeting_copilot_web_mvp.pipeline_trace import PipelineTraceCollector
from meeting_copilot_web_mvp.realtime_slo import RealtimeSLOStore
from meeting_copilot_web_mvp.application_schema import (
    bootstrap_application_schema,
    safe_schema_migration_report,
)
from meeting_copilot_web_mvp.v2_persistence import (
    DEFAULT_EVENT_PAGE_LIMIT,
    MAX_EVENT_PAGE_LIMIT,
    ReviewDocumentConflict,
    REVIEW_DOCUMENT_KINDS,
    SpeakerLabelConflict,
    V2Persistence,
    transcript_evidence_hash,
)
from meeting_copilot_web_mvp.review_export import (
    build_transcript_versions,
    export_fact_items,
    export_minutes,
    export_minutes_markdown,
    export_transcript,
    format_meeting_datetime,
    format_meeting_duration,
    transcript_display_line,
    value_text,
)
from meeting_copilot_web_mvp.v2_migration import (
    MigrationPreflightError,
    MigrationExecutionError,
    migrate_v1_to_v2,
)
from meeting_copilot_web_mvp.v2_pipeline import DurableJobExecutor
from meeting_copilot_web_mvp.recording_export import RecordingExportExecutor
from meeting_copilot_web_mvp.recording_recovery import (
    reconcile_and_recover_abandoned_recordings,
    reconcile_and_recover_expired_recordings,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (
    OpenAICompatibleStreamingProvider,
)
from meeting_copilot_web_mvp.v2_streaming_suggestions import (
    build_realtime_suggestion_messages,
    generate_streaming_suggestion,
)
from meeting_copilot_web_mvp.realtime_intelligence import (
    RealtimeIntelligenceRequest,
    build_llm_first_event_context,
    realtime_intelligence_batch_id,
    run_realtime_intelligence,
)
from meeting_copilot_web_mvp.asr_semantic_quality import (
    BLOCKER as ASR_SEMANTIC_QUALITY_BLOCKER,
    evaluate_semantic_quality,
)
from meeting_copilot_web_mvp.transcript_normalizer import normalize as _normalize_text
from meeting_copilot_web_mvp import metrics as _metrics
from meeting_copilot_web_mvp.storage_governance import (
    UnsafeManagedPathError,
    ensure_private_directory,
    harden_managed_storage_permissions,
    preflight_meeting_storage,
)
from meeting_copilot_web_mvp.local_api_auth import (
    BOOTSTRAP_PATH,
    LOCAL_API_TOKEN_ENV,
    SESSION_COOKIE_NAME,
    LocalApiAuthMiddleware,
    health_proof,
    session_cookie_value,
    token_status,
)
from meeting_copilot_web_mvp.next006_failpoints import (
    InjectedStorageWriteError,
    storage_write_failpoint,
)

configure_logging()
_log = get_logger("meeting_copilot_web_mvp.app")
_metrics.log_config_status()


async def _cancel_active_capture_tasks(
    active_capture_tasks: dict[str, set[asyncio.Task[Any]]],
) -> int:
    """Cancel live capture routes so their own finally blocks seal recording."""
    current_task = asyncio.current_task()
    tasks = {
        task
        for task_set in active_capture_tasks.values()
        for task in task_set
        if task is not current_task and not task.done()
    }
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    active_capture_tasks.clear()
    return len(tasks)


from meeting_copilot_core.session_snapshot import build_markdown_report
from meeting_copilot_web_mvp.asr_live_events import (
    ASR_LIVE_SOURCE,
    ASR_LIVE_TRACE_KIND,
    asr_event_source_metadata,
    build_asr_live_events,
    render_asr_sse_events,
)
from meeting_copilot_web_mvp.asr_live_repository import (
    InMemoryAsrLiveSessionRepository,
)
from meeting_copilot_web_mvp.sqlite_repository import (
    InMemorySettingsUsageRepository,
    SqliteAsrLiveSessionRepository,
    SqlitePersistenceCoordinator,
    SqliteSessionRepository,
    SqliteSettingsUsageRepository,
    migrate_json_to_sqlite,
)
from meeting_copilot_web_mvp.degradation_controller import get_degradation_controller
from meeting_copilot_web_mvp.asr_live_report import (
    build_asr_live_draft_review,
    render_asr_live_draft_markdown,
)
from meeting_copilot_web_mvp.demo_evaluation import evaluate_demo_snapshot
from meeting_copilot_web_mvp.demo_fixtures import (
    list_demo_fixtures,
    session_payload_from_fixture,
)
from meeting_copilot_web_mvp.live_events import (
    build_mock_live_events,
    event_source_metadata,
    render_sse_events as render_live_sse_events,
)
from meeting_copilot_web_mvp.replay_events import build_replay_events, render_sse_events
from meeting_copilot_web_mvp.repository import (
    CardStatusTransitionError,
    InMemorySessionRepository,
    JsonFileSessionRepository,
    SESSION_ID_PATTERN,
)


STATIC_DIR = Path(__file__).resolve().parent / "frontend_static"
WORKBENCH_HTML = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
FRONTEND_V2_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend_v2" / "dist"
SOURCE_REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = SOURCE_REPO_ROOT
DEFAULT_RUNTIME_DATA_DIR = REPO_ROOT / "artifacts" / "tmp" / "web_mvp_data"
LOCAL_ASR_EVENTS_APPROVED_ROOT = "artifacts/tmp/asr_events"
LOCAL_ASR_EVENTS_FORBIDDEN_PATH_LABELS = (
    ("configs/local", ("configs", "local")),
    ("data/asr_eval/local_samples", ("data", "asr_eval", "local_samples")),
    ("data/asr_eval/samples", ("data", "asr_eval", "samples")),
    ("data/local_runtime", ("data", "local_runtime")),
    ("outputs", ("outputs",)),
)
LOCAL_ASR_EVENT_TYPES = ("partial", "final", "revision", "error", "end_of_stream")
LOCAL_ASR_LIVE_EVENT_TYPES = (
    "transcript_partial",
    "transcript_final",
    "transcript_revision",
    "state_event",
    "scheduler_event",
    "suggestion_candidate_event",
    "llm_request_draft_event",
    "provider_error",
    "evaluation_summary",
    "suggestion_card",
)


def _live_asr_record_is_finalized(record: dict[str, Any]) -> bool:
    """Accept both raw and normalized end-of-stream persistence events."""
    for event in record.get("events") or []:
        if event.get("event_type") == "end_of_stream":
            return True
        if event.get("event_type") != "evaluation_summary":
            continue
        payload = event.get("payload") or {}
        try:
            if int(payload.get("end_of_stream_event_count") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


LOCAL_ASR_FILE_SAFETY_FLAGS = {
    "safe_to_call_llm_now": False,
    "safe_to_call_remote_asr_now": False,
    "safe_to_read_user_audio_now": False,
    "safe_to_read_configs_local_now": False,
    "safe_to_capture_microphone_now": False,
    "safe_to_download_models_now": False,
}
OPENAI_REQUEST_BODY_REDACTION_POLICY = "local_sensitive_draft_value_guard.v1"
OPENAI_REQUEST_BODY_REDACTION_PLACEHOLDER = "[redacted:sensitive_draft_value]"
OPENAI_REQUEST_BODY_RELAY_DOMAIN_MARKER = "codexai" + ".club"
DETERMINISTIC_DEMO_DERIVATION_MODE = "deterministic_demo"
DETERMINISTIC_DEMO_PROVIDER = "deterministic_demo"
DETERMINISTIC_DEMO_MODEL = "meeting-copilot-no-cost-demo"
DETERMINISTIC_DEMO_PROMPT_VERSION = "packaged-same-chain-demo.v1"
ASR_SEMANTIC_QUALITY_FORMAL_INPUT_SOURCES = frozenset(
    {
        "real_mic",
        "browser_live_mic",
        "uploaded_file",
        "simulated_realtime_wav",
        "real_mic_recorded_wav",
    }
)
LLM_EXECUTION_CANDIDATE_SELECTION_POLICY_VERSION = "llm-execution-candidate-selection.v1"
LLM_EXECUTION_DEFAULT_MAX_CANDIDATES_PER_RUN = 5
LLM_EXECUTION_HARD_MAX_CANDIDATES_PER_RUN = 20
LLM_EXECUTION_GAP_RULE_PRIORITY = {
    "release.rollback.owner.required": 0,
    "risk.rollback.validation": 1,
    "action.owner.deadline.confirmation": 2,
    "open.question.followup": 3,
}

FORMAL_REALTIME_AI_EVENT_TYPES = frozenset(
    {
        "meeting.topic.updated",
        "meeting.open_question.updated",
        "meeting.decision.updated",
        "meeting.action_item.updated",
        "meeting.risk.updated",
        "meeting.intelligence.applied",
        "suggestion.draft.started",
        "suggestion.draft.delta",
        "suggestion.committed",
        "suggestion.superseded",
        "suggestion.evidence.remapped",
    }
)
FORMAL_REALTIME_AI_PROJECTION_KEYS = {
    "meeting.open_question.updated": "question",
    "meeting.decision.updated": "decision",
    "meeting.action_item.updated": "action_item",
    "meeting.risk.updated": "risk",
}

V2_MEETING_TITLE_MAX_LENGTH = 200
V2_MEETING_TITLE_FORBIDDEN_PATTERN = re.compile(r"[\x00-\x1f\x7f/\\]")
V2_EXPORT_FILENAME_UNSAFE_PATTERN = re.compile(r"[\x00-\x1f\x7f<>:\"/\\|?*]+")
V2_EXPORT_FILENAME_SEPARATOR_PATTERN = re.compile(r"[\s._-]+")
V2_EXPORT_TIMEZONE = timezone(timedelta(hours=8))


def _validated_v2_meeting_title(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("title must be a string")
    if V2_MEETING_TITLE_FORBIDDEN_PATTERN.search(value):
        raise ValueError("title contains path-unsafe or control characters")
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("title must not be empty")
    if len(normalized) > V2_MEETING_TITLE_MAX_LENGTH:
        raise ValueError(f"title must not exceed {V2_MEETING_TITLE_MAX_LENGTH} characters")
    return normalized


def _v2_snapshot_with_meeting_metadata(
    persistence: V2Persistence,
    meeting_id: str,
    *,
    segment_limit: int = 500,
) -> dict[str, Any]:
    snapshot = persistence.get_snapshot(meeting_id, segment_limit=segment_limit)
    try:
        meeting = persistence.get_meeting(meeting_id)
    except KeyError:
        return snapshot
    return {
        **snapshot,
        "title": meeting["title"],
        "title_source": meeting["title_source"],
        "updated_at_ms": meeting["updated_at_ms"],
    }


def _v2_export_filename_component(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(
        "-" if unicodedata.category(character).startswith("C") else character for character in normalized
    )
    normalized = V2_EXPORT_FILENAME_UNSAFE_PATTERN.sub("-", normalized)
    normalized = V2_EXPORT_FILENAME_SEPARATOR_PATTERN.sub("-", normalized).strip("-")
    return normalized[:96].rstrip("-") or "meeting"


def _v2_export_date(meeting: dict[str, Any]) -> str:
    for field in ("started_at_ms", "created_at_ms"):
        try:
            milliseconds = max(0, int(meeting.get(field)))
            return datetime.fromtimestamp(
                milliseconds / 1_000,
                tz=V2_EXPORT_TIMEZONE,
            ).strftime("%Y-%m-%d")
        except (OSError, OverflowError, TypeError, ValueError):
            continue
    return "1970-01-01"


def _v2_export_content_disposition(
    meeting: dict[str, Any],
    *,
    extension: str,
) -> str:
    title = _v2_export_filename_component(meeting.get("title"))
    date = _v2_export_date(meeting)
    filename = f"{title}-{date}.{extension}"
    ascii_title = (
        re.sub(
            r"[^A-Za-z0-9_-]+",
            "-",
            title.encode("ascii", "ignore").decode("ascii"),
        ).strip("-")
        or "meeting"
    )
    ascii_filename = f"{ascii_title}-{date}.{extension}"
    return f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _v2_export_problem(error: str, message: str, *, retryable: bool) -> dict[str, Any]:
    return {
        "error": error,
        "message": message,
        "retryable": retryable,
    }


def _v2_validate_export_download_contract(
    content_disposition: str,
    *,
    extension: str,
    content: str | bytes,
) -> None:
    if not isinstance(content, (str, bytes)) or not content:
        raise ValueError("export content must be non-empty text or bytes")
    if not isinstance(content_disposition, str) or "\r" in content_disposition or "\n" in content_disposition:
        raise ValueError("invalid Content-Disposition header")
    match = re.search(r"filename\*=UTF-8''([^;]+)", content_disposition)
    if match is None:
        raise ValueError("Content-Disposition is missing its UTF-8 filename")
    from urllib.parse import unquote

    filename = unquote(match.group(1))
    if (
        not filename
        or filename.startswith(".")
        or ".." in filename
        or "/" in filename
        or "\\" in filename
        or V2_EXPORT_FILENAME_UNSAFE_PATTERN.search(filename)
        or not filename.endswith(f".{extension}")
    ):
        raise ValueError("Content-Disposition filename is unsafe")


def _v2_export_download_response(
    content: str | bytes,
    *,
    media_type: str,
    content_disposition: str,
    extension: str,
) -> Response:
    _v2_validate_export_download_contract(
        content_disposition,
        extension=extension,
        content=content,
    )
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition},
    )


def _v2_complete_transcript(persistence: V2Persistence, meeting_id: str) -> list[dict[str, Any]]:
    transcript: list[dict[str, Any]] = []
    after_seq = 0
    while True:
        page = persistence.list_transcript_segments(
            meeting_id,
            after_transcript_seq=after_seq,
            limit=1_000,
        )
        transcript.extend(page["segments"])
        if not page["has_more"]:
            return transcript
        next_seq = int(page["next_after_transcript_seq"])
        if next_seq <= after_seq:
            raise RuntimeError("meeting export transcript cursor did not advance")
        after_seq = next_seq


def _v2_export_payload(persistence: V2Persistence, meeting_id: str) -> dict[str, Any]:
    meeting = persistence.get_meeting(meeting_id)
    snapshot = persistence.get_snapshot(meeting_id, segment_limit=1_000)
    source_transcript = _v2_complete_transcript(persistence, meeting_id)
    documents = snapshot.get("documents") or {}
    suggestions = [
        {
            "suggestion_id": item["suggestion_id"],
            "status": item["status"],
            "text": item.get("text") or item.get("draft_text"),
            "feedback": item.get("feedback"),
            "evidence_segment_id": item.get("evidence_segment_id"),
            "evidence_transcript_seq": item.get("evidence_transcript_seq"),
            "committed_at_ms": item.get("committed_at_ms"),
        }
        for item in snapshot["suggestions"]
        if item.get("status") == "committed"
    ]
    review_document_revisions = {
        kind: persistence.list_review_document_revisions(meeting_id, kind, limit=500)
        for kind in sorted(documents)
        if kind in REVIEW_DOCUMENT_KINDS
    }
    payload = {
        "schema_version": "meeting_copilot.meeting_export.v1",
        "exported_at_ms": time.time_ns() // 1_000_000,
        "meeting": meeting,
        "transcript": source_transcript,
        "source_transcript": source_transcript,
        "current_topic": snapshot["current_topic"],
        "open_questions": snapshot["open_questions"],
        "decision_candidates": snapshot["decision_candidates"],
        "action_items": snapshot["action_items"],
        "risks": snapshot["risks"],
        "suggestions": suggestions,
        "minutes": snapshot["minutes"],
        "approach_cards": snapshot["approach_cards"],
        "review_jobs": snapshot["review_jobs"],
        "documents": documents,
        "audio": snapshot["audio"],
        "audit": {
            "meeting_revision": meeting["revision"],
            "latest_event_seq": meeting["latest_seq"],
            "review_document_revisions": review_document_revisions,
        },
    }
    payload["minutes"] = export_minutes(payload)
    payload["decision_candidates"] = export_fact_items(
        payload,
        kind="decisions",
        key="decisions",
        fallback=snapshot["decision_candidates"],
    )
    payload["action_items"] = export_fact_items(
        payload,
        kind="action_items",
        key="action_items",
        fallback=snapshot["action_items"],
    )
    payload["risks"] = export_fact_items(
        payload,
        kind="risks",
        key="risks",
        fallback=snapshot["risks"],
    )
    payload["transcript"] = export_transcript(payload)
    payload["transcript_versions"] = build_transcript_versions(payload)
    return payload


def _single_line_export_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _export_offset(milliseconds: Any) -> str:
    try:
        total_seconds = max(0, int(milliseconds or 0) // 1_000)
    except (TypeError, ValueError):
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _render_v2_export_markdown(payload: dict[str, Any]) -> str:
    meeting = dict(payload["meeting"])
    meeting_id = str(meeting["id"])
    title = _single_line_export_text(meeting.get("title")) or "会议复盘"
    lines = [
        f"# {title}",
        "",
        f"- 会议 ID：`{meeting_id}`",
        f"- 状态：{_single_line_export_text(meeting.get('state')) or 'unknown'}",
        f"- 会议日期：{format_meeting_datetime(meeting)}",
        f"- 会议时长：{format_meeting_duration(meeting)}",
        "",
        "## 会议复盘",
        "",
    ]
    markdown = export_minutes_markdown(payload).strip()
    lines.append(markdown or "会议复盘尚未生成。")

    formal_facts = (
        (
            "决策候选",
            export_fact_items(
                payload, kind="decisions", key="decisions", fallback=payload.get("decision_candidates") or []
            ),
        ),
        (
            "行动项",
            export_fact_items(
                payload, kind="action_items", key="action_items", fallback=payload.get("action_items") or []
            ),
        ),
        ("风险", export_fact_items(payload, kind="risks", key="risks", fallback=payload.get("risks") or [])),
    )
    for heading, facts in formal_facts:
        if not facts:
            continue
        lines.extend(["", f"## {heading}", ""])
        for fact in facts:
            evidence = fact.get("evidence") or {} if isinstance(fact, dict) else {}
            evidence_id = _single_line_export_text(
                evidence.get("segment_id") if isinstance(evidence, dict) else None
            ) or _single_line_export_text(fact.get("evidence_segment_id") if isinstance(fact, dict) else None)
            details = (
                [f"状态：{_single_line_export_text(fact.get('status')) or 'candidate'}"]
                if isinstance(fact, dict)
                else ["状态：candidate"]
            )
            if isinstance(fact, dict) and fact.get("owner") is not None:
                details.append(f"负责人：{_single_line_export_text(fact.get('owner'))}")
            if isinstance(fact, dict) and fact.get("deadline") is not None:
                details.append(f"截止：{_single_line_export_text(fact.get('deadline'))}")
            if isinstance(fact, dict) and fact.get("mitigation") is not None:
                details.append(f"缓解：{_single_line_export_text(fact.get('mitigation'))}")
            if evidence_id:
                details.append(f"依据：`{evidence_id}`")
            lines.append(f"- {value_text(fact, 'text', 'item')}（{'；'.join(details)}）")

    kept = [item for item in payload["suggestions"] if item.get("feedback") == "kept"]
    if kept:
        lines.extend(["", "## 保留的会中建议", ""])
        for item in kept:
            evidence = _single_line_export_text(item.get("evidence_segment_id"))
            suffix = f"（依据：`{evidence}`）" if evidence else ""
            lines.append(f"- {_single_line_export_text(item.get('text'))}{suffix}")

    lines.extend(["", "## 完整会议文字", ""])
    transcript = export_transcript(payload)
    if transcript:
        for segment in transcript:
            lines.append(f"- {transcript_display_line(segment)}")
    else:
        lines.append("暂无会议文字。")
    return "\n".join(lines).rstrip() + "\n"


OPENAI_REQUEST_BODY_SENSITIVE_PATTERN = re.compile(
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"Bearer\s+\S+|"
    r"Authorization\s*:|"
    r"raw_config|"
    r"api_key|"
    r"base_url|"
    r"config_path|"
    r"configs/local",
    re.IGNORECASE,
)
SCHEMA_VALIDATION_REQUIRED_FIELDS = [
    "id",
    "type",
    "evidence_span_ids",
    "state_refs",
    "state_event_ids",
    "gap_rule_id",
    "trigger_reason",
    "trigger_source",
    "final_segment_at_ms",
    "state_event_at_ms",
    "card_created_at_ms",
    "latency_ms",
    "prompt_version",
    "model",
    "usage",
    "schema_result",
    "show_or_silence_decision",
    "segment_batch",
    "status",
]
SCHEMA_VALIDATION_OPTIONAL_FIELDS = [
    "title",
    "suggested_question",
]
SCHEMA_VALIDATION_VALID_SCHEMA_RESULTS = {"valid", "failed", "timeout", "invalid"}
SCHEMA_VALIDATION_BLOCKING_SCHEMA_RESULTS = {"failed", "timeout", "invalid"}
SCHEMA_VALIDATION_NON_STRONG_DECISIONS = {
    "silence",
    "too_late",
    "after_meeting_pending",
    "draft",
    "degraded",
}


class V2TranscriptRevisionConflict(RuntimeError):
    retryable = False


class CorrectionBatchDeferred(RuntimeError):
    def __init__(self, retry_after_ms: int) -> None:
        super().__init__("transcript correction batch is waiting for its realtime interval")
        self.retry_after_ms = max(250, int(retry_after_ms))


class RealtimeCorrectionProviderError(HTTPException):
    def __init__(self, *, retryable: bool) -> None:
        super().__init__(
            status_code=502,
            detail=llm_service.provider_error_payload(
                error_code="realtime_correction_provider_failed",
                message="Realtime correction provider request failed",
            ),
        )
        self.retryable = bool(retryable)


class ProviderRuntimeNotConfiguredDeferred(RuntimeError):
    preserve_attempt = True
    retry_after_ms = 10_000

    def __init__(self) -> None:
        super().__init__("AI provider is waiting for explicit desktop connection")


def _commit_v2_transcript_revisions(
    persistence: V2Persistence,
    *,
    meeting_id: str,
    causation_job_id: str,
    revisions: list[dict[str, Any]],
    max_input_transcript_seq: int,
    now_ms: int,
) -> dict[str, Any]:
    """Idempotently project validated legacy revisions into canonical V2 facts."""

    unique: dict[str, dict[str, Any]] = {}
    for revision in revisions:
        revision_id = str(revision.get("id") or "").strip()
        if revision_id:
            unique[revision_id] = dict(revision)

    event_count = 0
    segment_ids: list[str] = []
    for revision_id, revision in unique.items():
        payload = dict(revision.get("payload") or {})
        if str(revision.get("event_type") or "") != "transcript_revision":
            continue
        correction = dict(payload.get("correction") or {})
        if correction.get("policy_version") != realtime_transcript_correction.POLICY_VERSION:
            continue
        segment_id = str(payload.get("supersedes_segment_id") or payload.get("revision_of") or "").strip()
        original_text = str(payload.get("original_text") or "").strip()
        corrected_text = str(payload.get("normalized_text") or payload.get("text") or "").strip()
        if not segment_id or not original_text or not corrected_text:
            raise V2TranscriptRevisionConflict(f"validated correction is missing target identity: {revision_id}")

        try:
            before = persistence.get_transcript_segment(meeting_id, segment_id)
        except KeyError as exc:
            raise RuntimeError(f"correction target is not committed yet: {segment_id}") from exc
        if int(before["transcript_seq"]) > int(max_input_transcript_seq):
            continue

        committed = persistence.commit_transcript_revision(
            meeting_id=meeting_id,
            segment_id=segment_id,
            expected_evidence_hash=transcript_evidence_hash(
                segment_id,
                original_text,
            ),
            corrected_text=corrected_text,
            revision_id=revision_id,
            now_ms=now_ms,
            correlation_id=meeting_id,
            causation_id=causation_job_id,
            evidence_remap_reason="validated_meaning_preserved_correction",
        )
        if committed is None:
            raise V2TranscriptRevisionConflict(f"correction evidence changed before commit: {segment_id}")
        segment_ids.append(segment_id)
        if int(committed["revision"]) > int(before["revision"]):
            event_count += 1

    return {
        "revision_count": len(segment_ids),
        "event_count": event_count,
        "segment_ids": segment_ids,
    }


SCHEMA_VALIDATION_NON_STRONG_STATUSES = {
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}
V2_END_ASR_FINALIZATION_TIMEOUT_S = 15.0
V2_END_ASR_FINALIZATION_POLL_S = 0.05
CARD_CREATION_POLICY_CHECK_COUNT = 13
CARD_CREATION_POLICY_REALTIME_WINDOW_MS = 30_000
CARD_LIFECYCLE_PREVIEW_CHECK_COUNT = 5


class CreateSessionRequest(BaseModel):
    session_id: str
    transcript_report: dict[str, Any]
    analysis: dict[str, Any]
    state_events: list[dict[str, Any]] = []
    llm_usage: dict[str, Any] | None = None
    degradation_reasons: list[str] = []


class UpdateCardStatusRequest(BaseModel):
    status: str


class CreateFixtureSessionRequest(BaseModel):
    session_id: str | None = None


class CreateAsrLiveSessionRequest(BaseModel):
    session_id: str | None = None
    provider: str = "local_mock_asr"
    streaming_events: list[dict[str, Any]] | None = None
    fixture_id: str | None = None  # New: load streaming_events from fixture


class CreateAsrLiveSessionFromEventFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    provider: str = "local_asr_event_file"
    events_path: str


class CreateMacLocalShadowMvpDemoSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str


class CreateRealisticMeetingSimulationPackSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    profile: str = "standard"


class CreateMainlineAsrBlockedTrialSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str


class CreateMainlineTrialFeedbackExportClosureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    feedback_entries: list[dict[str, Any]] = Field(default_factory=list)


class CreateLlmExecutionRunsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str
    max_candidates: int | None = Field(default=None, ge=1, le=LLM_EXECUTION_HARD_MAX_CANDIDATES_PER_RUN)


class PatchAutoSuggestionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused: bool | None = None


class RunRealtimeCorrectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False


class AsrSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    l2_correction_enabled: bool = True
    l3_normalize_enabled: bool = True


class SuggestionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    window_seconds: int = Field(default=20, ge=1, le=3_600)
    cooldown_minutes: int = Field(default=5, ge=0, le=1_440)
    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)


class BudgetSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_limit_cny: float = Field(default=10.0, ge=0.0)
    daily_limit_cny: float = Field(default=50.0, ge=0.0)
    l3_value_policy: Literal["always", "when_needed", "never"] = "when_needed"


class SettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asr: AsrSettings = Field(default_factory=AsrSettings)
    suggestions: SuggestionSettings = Field(default_factory=SuggestionSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)


class DesktopProviderConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=8, max_length=2_048)
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=128)
    realtime_model: str | None = Field(default=None, min_length=1, max_length=128)
    api_style: Literal["chat_completions", "responses"] = "chat_completions"
    provider_label: str = Field(
        default="openai_compatible_gateway",
        min_length=1,
        max_length=128,
    )


class WebProviderConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=8, max_length=2_048)
    api_key: SecretStr | None = None
    model: str = Field(min_length=1, max_length=128)
    realtime_model: str | None = Field(default=None, min_length=1, max_length=128)
    api_style: Literal["chat_completions", "responses"] = "chat_completions"
    provider_label: str = Field(
        default="openai_compatible_gateway",
        min_length=1,
        max_length=128,
    )


class Next006StorageFailpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["sqlite_transaction", "meeting_title_transaction", "audio_chunk"]
    failure: Literal["enospc", "eio", "erofs"]
    count: int = Field(default=1, ge=1, le=10)


class ShadowReportFeedbackIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_report: dict[str, Any] | None = None
    candidate_report_path: str | None = None
    feedback_entries: list[dict[str, Any]]


class DesktopTauriNoopRunResultValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_result: dict[str, Any]


def create_app(
    repository: InMemorySessionRepository | JsonFileSessionRepository | SqliteSessionRepository | None = None,
    data_dir: str | Path | None = None,
    allow_fake_asr_fallback: bool = False,
    prewarm_funasr: bool = False,
    semantic_projection_mode: str = "legacy",
) -> FastAPI:
    configured_workers = max(
        int(os.environ.get("WEB_CONCURRENCY") or 1),
        int(os.environ.get("UVICORN_WORKERS") or 1),
    )
    if configured_workers != 1:
        raise RuntimeError("Meeting Copilot LLM single-flight requires a single worker runtime")
    # A sidecar process belongs to one app instance. A newly constructed app
    # must not inherit that older instance's crashed/restart-failed marker.
    degradation = get_degradation_controller()
    if degradation.reason.startswith(("asr_sidecar_crashed:", "asr_sidecar_restart_failed:")):
        degradation.reset()
    app = FastAPI(title="Meeting Copilot Local Web MVP")

    @app.exception_handler(InjectedStorageWriteError)
    async def next006_storage_write_failure_handler(
        _request: Request,
        error: InjectedStorageWriteError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=507,
            content={
                "detail": {
                    "error": "storage_write_failed",
                    "failure": error.failure,
                    "scope": error.scope,
                    "retryable": True,
                }
            },
        )

    local_api_token = os.environ.get(LOCAL_API_TOKEN_ENV, "").strip()
    app.state.local_api_auth = token_status(local_api_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8765",
            "http://localhost:8765",
            "tauri://localhost",
        ],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(LocalApiAuthMiddleware, token=local_api_token)
    if repository is not None and data_dir is not None:
        raise ValueError("repository and data_dir cannot both be provided")
    data_dir_path = Path(data_dir) if data_dir is not None else None
    meeting_preparation_store: MeetingPreparationStore | None = None
    sqlite_repositories: list[Any] = []
    persistence_coordinator: SqlitePersistenceCoordinator | None = None
    v2_persistence: V2Persistence | None = None
    v2_migration_report: dict[str, Any] | None = None
    application_schema_migration_report = safe_schema_migration_report(None)
    if repository is not None:
        repo = repository
        asr_live_repo = InMemoryAsrLiveSessionRepository()
    elif data_dir is not None:
        data_dir_path = ensure_private_directory(data_dir_path)
        harden_managed_storage_permissions(data_dir_path)
        db_path = data_dir_path / "meeting_copilot.db"
        if db_path.is_dir():
            raise RuntimeError(
                "Application SQLite schema bootstrap failed: database_path_is_directory"
            )
        try:
            schema_result = bootstrap_application_schema(db_path)
        except Exception as exc:
            error_class = type(exc).__name__
            _log.error(
                "meeting.application_schema.bootstrap_failed",
                error_class=error_class,
            )
            raise RuntimeError(
                f"Application SQLite schema bootstrap failed: {error_class}"
            ) from None
        application_schema_migration_report = safe_schema_migration_report(schema_result)
        try:
            migrate_json_to_sqlite(data_dir, str(db_path))
        except Exception as exc:
            raise RuntimeError(f"SQLite migration failed: {exc}") from exc
        try:
            v2_migration_report = migrate_v1_to_v2(db_path)
        except (MigrationPreflightError, MigrationExecutionError) as exc:
            v2_migration_report = getattr(exc, "report", None) or {
                "status": "failed",
                "error_class": type(exc).__name__,
            }
            _log.error(
                "meeting.v2.shadow_migration_failed",
                error_class=type(exc).__name__,
            )
        meeting_preparation_store = MeetingPreparationStore(data_dir_path / "meeting_preparation")
        repo = SqliteSessionRepository(data_dir_path)
        asr_live_repo = SqliteAsrLiveSessionRepository(data_dir_path)
        persistence_coordinator = SqlitePersistenceCoordinator(data_dir_path)
        v2_persistence = V2Persistence(
            db_path,
            semantic_projection_mode=semantic_projection_mode,
        )
        sqlite_repositories.extend([repo, asr_live_repo, persistence_coordinator, v2_persistence])
    else:
        repo = InMemorySessionRepository()
        asr_live_repo = InMemoryAsrLiveSessionRepository()
    default_settings = SettingsPayload().model_dump(mode="json")
    if data_dir_path is not None:
        settings_usage_repo = SqliteSettingsUsageRepository(data_dir_path, default_settings)
        sqlite_repositories.append(settings_usage_repo)
    else:
        settings_usage_repo = InMemorySettingsUsageRepository(default_settings)
    web_provider_config_store = (
        ProviderConfigStore(data_dir_path)
        if data_dir_path is not None and os.environ.get("MEETING_COPILOT_DESKTOP_RUNTIME") != "1"
        else None
    )
    web_provider_config_error: str | None = None
    if web_provider_config_store is not None:
        try:
            stored_provider = web_provider_config_store.load()
            if stored_provider is not None:
                llm_service.configure_runtime(
                    base_url=str(stored_provider["base_url"]),
                    api_key=str(stored_provider["api_key"]),
                    model=str(stored_provider["model"]),
                    realtime_model=(
                        str(stored_provider["realtime_model"])
                        if stored_provider.get("realtime_model")
                        else None
                    ),
                    provider_label=str(
                        stored_provider.get("provider_label") or "openai_compatible_gateway"
                    ),
                    api_style=str(stored_provider.get("api_style") or "chat_completions"),
                )
        except (OSError, TypeError, ValueError) as exc:
            web_provider_config_error = "本地 AI 配置无效，请重新保存"
            _log.warning(
                "provider.web_config.load_failed",
                error_type=type(exc).__name__,
            )
    app.state.asr_live_repository = asr_live_repo
    app.state.session_repository = repo
    app.state.settings_usage_repository = settings_usage_repo
    app.state.sqlite_repositories = sqlite_repositories
    app.state.persistence_coordinator = persistence_coordinator
    app.state.v2_persistence = v2_persistence
    app.state.v2_migration_report = v2_migration_report
    app.state.application_schema_migration_report = application_schema_migration_report
    app.state.meeting_preparation_store = meeting_preparation_store
    app.state.web_provider_config_store = web_provider_config_store
    app.state.web_provider_config_error = web_provider_config_error
    app.state.llm_first_event_contexts = {}

    def _valid_llm_first_event_context(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        required_text_fields = ("job_id", "batch_id", "provider", "model")
        if value.get("source") != "llm_first" or value.get("llm_called") is not True:
            return None
        if any(not str(value.get(field) or "").strip() for field in required_text_fields):
            return None
        evidence = value.get("evidence")
        if not isinstance(evidence, dict) or not isinstance(evidence.get("segment_ids"), list):
            return None
        if not any(str(item).strip() for item in evidence["segment_ids"]):
            return None
        return dict(value)

    def _llm_first_event_context_for_job(job_id: str) -> dict[str, Any] | None:
        normalized_job_id = str(job_id or "").strip()
        if not normalized_job_id:
            return None
        cached = _valid_llm_first_event_context(
            app.state.llm_first_event_contexts.get(normalized_job_id)
        )
        if cached is not None:
            return cached
        if v2_persistence is None:
            return None
        try:
            job = v2_persistence.get_job(normalized_job_id)
        except KeyError:
            return None
        output = job.get("output") if isinstance(job, dict) else None
        context = _valid_llm_first_event_context(
            output.get("formal_event_context") if isinstance(output, dict) else None
        )
        if context is not None:
            app.state.llm_first_event_contexts[normalized_job_id] = context
        return context

    def _evidence_from_value(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
        source = value if isinstance(value, dict) else {}
        raw_ids = source.get("segment_ids") or source.get("evidence_segment_ids") or []
        segment_ids = [str(item).strip() for item in raw_ids if str(item).strip()]
        if not segment_ids:
            segment_ids = [str(item).strip() for item in fallback.get("segment_ids") or [] if str(item).strip()]
        evidence = {
            "segment_ids": segment_ids,
            "quote": str(source.get("quote") or fallback.get("quote") or "").strip(),
        }
        for key in ("evidence_hash", "state_revision"):
            if source.get(key) is not None:
                evidence[key] = source[key]
            elif fallback.get(key) is not None:
                evidence[key] = fallback[key]
        return evidence

    def _formal_event_evidence(event: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        fallback = dict(context.get("evidence") or {})
        event_type = str(event.get("type") or "")
        if event_type == "meeting.topic.updated":
            return _evidence_from_value(context.get("topic_evidence"), fallback)
        if event_type == "meeting.intelligence.applied":
            follow_up = payload.get("follow_up")
            return _evidence_from_value(
                context.get("follow_up_evidence") if isinstance(follow_up, dict) else None,
                fallback,
            )
        entity = payload.get("entity")
        if isinstance(entity, dict):
            entity_evidence = entity.get("evidence") if isinstance(entity.get("evidence"), dict) else {}
            return _evidence_from_value(
                {
                    "segment_ids": entity.get("evidence_segment_ids") or entity_evidence.get("segment_ids"),
                    "quote": payload.get("evidence_quote") or entity_evidence.get("quote"),
                },
                fallback,
            )
        return _evidence_from_value(
            {
                "segment_ids": payload.get("evidence_segment_ids") or (
                    [payload.get("evidence_segment_id")] if payload.get("evidence_segment_id") else []
                ),
                "quote": payload.get("evidence_quote"),
            },
            fallback,
        )

    def _decorate_v2_formal_event(event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "")
        if event_type not in FORMAL_REALTIME_AI_EVENT_TYPES:
            return event
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if payload.get("source") != "llm_first":
            return event
        job_id = str(payload.get("job_id") or event.get("causation_id") or "").strip()
        context = _llm_first_event_context_for_job(job_id)
        if context is None:
            return event
        enriched_payload = {
            **payload,
            "source": "llm_first",
            "job_id": context["job_id"],
            "batch_id": context["batch_id"],
            "provider": context["provider"],
            "model": context["model"],
            "llm_called": True,
            "llm_call_status": "called",
            "evidence": _formal_event_evidence(event, context),
        }
        projection_key = FORMAL_REALTIME_AI_PROJECTION_KEYS.get(event_type)
        if projection_key and isinstance(payload.get("entity"), dict):
            enriched_payload[projection_key] = payload["entity"]
        return {**event, "payload": enriched_payload}

    def _all_v2_formal_events(meeting_id: str) -> list[dict[str, Any]]:
        if v2_persistence is None:
            return []
        cursor = 0
        formal_events: list[dict[str, Any]] = []
        while True:
            page = v2_persistence.list_event_page(
                meeting_id,
                after_seq=cursor,
                limit=MAX_EVENT_PAGE_LIMIT,
            )
            decorated = [_decorate_v2_formal_event(event) for event in page["events"]]
            formal_events.extend(
                event
                for event in decorated
                if event.get("type") in FORMAL_REALTIME_AI_EVENT_TYPES
                and (event.get("payload") or {}).get("llm_called") is True
                and (event.get("payload") or {}).get("source") == "llm_first"
            )
            if not page["has_more"]:
                break
            next_cursor = int(page["next_after_seq"])
            if next_cursor <= cursor:
                break
            cursor = next_cursor
        return formal_events

    def _decorate_v2_snapshot(snapshot: dict[str, Any], meeting_id: str) -> dict[str, Any]:
        if v2_persistence is None or v2_persistence.semantic_projection_mode != "llm_first":
            return snapshot
        formal_events = _all_v2_formal_events(meeting_id)
        by_projection: dict[tuple[str, str], dict[str, Any]] = {}
        intelligence_events: list[dict[str, Any]] = []
        for event in formal_events:
            event_type = str(event.get("type") or "")
            if event_type == "meeting.intelligence.applied":
                intelligence_events.append(event)
            by_projection[(event_type, str(event.get("aggregate_id") or ""))] = event

        def project_item(item: Any, event_type: str) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            item_id = str(item.get("id") or "")
            event = by_projection.get((event_type, item_id))
            if event is None:
                return None
            payload = dict(event.get("payload") or {})
            return {
                **item,
                "source": payload.get("source"),
                "job_id": payload.get("job_id"),
                "batch_id": payload.get("batch_id"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "llm_called": payload.get("llm_called"),
                "formal_evidence": payload.get("evidence"),
            }

        current_topic = snapshot.get("current_topic")
        topic_event = by_projection.get(("meeting.topic.updated", "current-topic"))
        if topic_event is None:
            current_topic = None
        elif isinstance(current_topic, dict):
            topic_payload = dict(topic_event.get("payload") or {})
            current_topic = {
                **current_topic,
                "source": topic_payload.get("source"),
                "job_id": topic_payload.get("job_id"),
                "batch_id": topic_payload.get("batch_id"),
                "provider": topic_payload.get("provider"),
                "model": topic_payload.get("model"),
                "llm_called": topic_payload.get("llm_called"),
                "formal_evidence": topic_payload.get("evidence"),
            }
        projected = {
            **snapshot,
            "suggestions": [],
            "current_topic": current_topic,
            "open_questions": [
                item for item in (
                    project_item(value, "meeting.open_question.updated")
                    for value in snapshot.get("open_questions") or []
                ) if item is not None
            ],
            "decision_candidates": [
                item for item in (
                    project_item(value, "meeting.decision.updated")
                    for value in snapshot.get("decision_candidates") or []
                ) if item is not None
            ],
            "action_items": [
                item for item in (
                    project_item(value, "meeting.action_item.updated")
                    for value in snapshot.get("action_items") or []
                ) if item is not None
            ],
            "risks": [
                item for item in (
                    project_item(value, "meeting.risk.updated")
                    for value in snapshot.get("risks") or []
                ) if item is not None
            ],
            "follow_up": snapshot.get("follow_up") if intelligence_events else None,
        }
        if projected["follow_up"] is not None and intelligence_events:
            latest_payload = dict(intelligence_events[-1].get("payload") or {})
            projected["follow_up"] = {
                **dict(projected["follow_up"]),
                "source": latest_payload.get("source"),
                "job_id": latest_payload.get("job_id"),
                "batch_id": latest_payload.get("batch_id"),
                "provider": latest_payload.get("provider"),
                "model": latest_payload.get("model"),
                "llm_called": latest_payload.get("llm_called"),
                "formal_evidence": latest_payload.get("evidence"),
            }

        intelligence_jobs = [
            job for job in v2_persistence.list_jobs(meeting_id=meeting_id)
            if job.get("kind") == "intelligence"
        ]
        active_jobs = [job for job in intelligence_jobs if job.get("status") in {"pending", "running", "retry_wait"}]
        deferred = next(
            (job for job in reversed(active_jobs) if job.get("error_class") == "ProviderRuntimeNotConfiguredDeferred"),
            None,
        )
        failed = next((job for job in reversed(intelligence_jobs) if job.get("status") == "failed"), None)
        runtime = dict(projected.get("runtime") or {})
        if deferred is not None:
            runtime["ai"] = {
                "state": "paused",
                "label": "AI 已暂停",
                "level": None,
                "detail": "LLM Provider 不可用，实时理解已暂停",
                "error_class": "ProviderRuntimeNotConfiguredDeferred",
            }
        elif failed is not None:
            runtime["ai"] = {
                "state": "error",
                "label": "AI 处理失败",
                "level": None,
                "detail": "实时理解未生成正式结果",
                "error_class": failed.get("error_class"),
            }
        elif active_jobs:
            runtime["ai"] = {"state": "busy", "label": "AI 正在处理", "level": None, "detail": None}
        projected["runtime"] = runtime
        diagnostics = dict(projected.get("diagnostics") or {})
        diagnostics["formal_ai_projection"] = "llm_first_only"
        projected["diagnostics"] = diagnostics
        return projected

    def _purge_legacy_meeting_projection(meeting_id: str, deletion_scope: str) -> None:
        """Keep compatibility repositories inside the same privacy boundary as V2."""

        if v2_persistence is not None and deletion_scope in {"derived", "transcript", "all"}:
            for job in v2_persistence.list_jobs(meeting_id=meeting_id):
                if str(job.get("status") or "") not in {
                    "pending",
                    "running",
                    "retry_wait",
                }:
                    continue
                try:
                    pipeline_traces.record_cancelled(str(job["id"]))
                except KeyError:
                    pass
        if deletion_scope == "all":
            try:
                asr_live_repo.delete(meeting_id)
            except KeyError:
                pass
            try:
                repo.delete(meeting_id)
            except KeyError:
                pass
        else:

            def scrub(existing: dict[str, Any]) -> dict[str, Any]:
                scrubbed = dict(existing)
                if deletion_scope in {"derived", "transcript"}:
                    for key in (
                        "approach_cards",
                        "auto_suggestion",
                        "llm_execution_runs",
                        "minutes",
                        "realtime_intelligence",
                        "realtime_transcript_correction",
                        "suggestion_cards",
                    ):
                        scrubbed.pop(key, None)
                    if deletion_scope == "derived":
                        scrubbed["events"] = [
                            event
                            for event in list(scrubbed.get("events") or [])
                            if str(event.get("event_type") or "")
                            not in {
                                "suggestion_card",
                                "transcript_revision",
                            }
                        ]
                if deletion_scope == "transcript":
                    scrubbed["events"] = []
                    for key in ("asr_semantic_quality", "post_meeting_asr_profile"):
                        scrubbed.pop(key, None)
                if deletion_scope == "recording":
                    scrubbed.pop("audio", None)
                return scrubbed

            try:
                asr_live_repo.update(meeting_id, scrub)
            except KeyError:
                pass
        if deletion_scope in {"recording", "all"}:
            asr_stream.clear_session_hotwords(meeting_id)

    data_governance_service = (
        DataGovernanceService(
            persistence=v2_persistence,
            data_dir=data_dir_path,
            meeting_preparation_store=meeting_preparation_store,
            pre_purge=_purge_legacy_meeting_projection,
        )
        if v2_persistence is not None and data_dir_path is not None
        else None
    )
    app.state.data_governance = data_governance_service
    realtime_slo = RealtimeSLOStore(
        state_path=(data_dir_path / "diagnostics" / "realtime-ai-slo.json" if data_dir_path is not None else None)
    )
    pipeline_traces = PipelineTraceCollector(
        max_traces=2_048,
        on_evict=realtime_slo.observe,
    )
    app.state.pipeline_traces = pipeline_traces
    app.state.realtime_slo = realtime_slo
    audio_active_marks: dict[str, int] = {}
    app.state.audio_active_marks = audio_active_marks
    recording_capture_lease_ms = 30_000
    recording_asset_locks: dict[str, threading.Lock] = {}
    recording_asset_locks_guard = threading.Lock()
    active_v2_capture_tasks: dict[str, set[asyncio.Task[Any]]] = {}
    app.state.active_v2_capture_tasks = active_v2_capture_tasks
    recording_import_wake_event = asyncio.Event()
    recording_import_stop_event = asyncio.Event()
    app.state.recording_import_worker_task = None
    app.state.desktop_parent_watchdog_task = None

    def _recording_asset_lock(meeting_id: str) -> threading.Lock:
        with recording_asset_locks_guard:
            return recording_asset_locks.setdefault(meeting_id, threading.Lock())

    def _delete_audio_asset_locked(
        meeting_id: str,
        audio: dict[str, Any],
    ) -> str:
        with _recording_asset_lock(meeting_id):
            return audio_assets.delete_audio_asset(data_dir_path, audio)

    def _delete_recording_directory_if_present(meeting_id: str) -> None:
        if data_dir_path is None:
            return
        with _recording_asset_lock(meeting_id):
            session_dir = audio_assets.safe_audio_path(
                data_dir_path,
                Path("audio_assets") / meeting_id,
            )
            if not session_dir.exists():
                return
            audio_assets.delete_audio_asset(
                data_dir_path,
                {"relative_path": f"audio_assets/{meeting_id}/audio.wav"},
            )

    async def _cancel_meeting_capture_tasks(meeting_id: str) -> int:
        capture_tasks = tuple(active_v2_capture_tasks.get(meeting_id, ()))
        current_task = asyncio.current_task()
        capture_tasks = tuple(task for task in capture_tasks if task is not current_task)
        for capture_task in capture_tasks:
            capture_task.cancel()
        if capture_tasks:
            await asyncio.gather(*capture_tasks, return_exceptions=True)
        return len(capture_tasks)

    async def _begin_deletion_fence(meeting_id: str) -> dict[str, Any] | None:
        deletion_job = (
            v2_persistence.create_deletion_job(
                meeting_id=meeting_id,
                managed_paths=[f"audio_assets/{meeting_id}"],
                now_ms=time.time_ns() // 1_000_000,
            )
            if v2_persistence is not None
            else None
        )
        await _cancel_meeting_capture_tasks(meeting_id)
        return deletion_job

    async def _complete_deletion_facts(
        deletion_job: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if deletion_job is None or v2_persistence is None:
            return None

        await asyncio.to_thread(
            _delete_recording_directory_if_present,
            str(deletion_job["meeting_id"]),
        )

        def complete() -> dict[str, Any]:
            running = v2_persistence.mark_deletion_running(
                job_id=str(deletion_job["id"]),
                now_ms=time.time_ns() // 1_000_000,
            )
            return v2_persistence.complete_deletion_and_purge(
                job_id=str(running["id"]),
                now_ms=time.time_ns() // 1_000_000,
            )

        return await asyncio.to_thread(complete)

    def _transcript_source_track(audio_source: Any) -> str | None:
        normalized = str(audio_source or "").strip().lower()
        if normalized in {
            "microphone",
            "browser_live_mic",
            "tauri_native_mic",
            "native_microphone_streaming",
        }:
            return "microphone"
        if normalized in {"system_audio", "tauri_system_audio", "macos_system_audio"}:
            return "system_audio"
        if normalized in {"uploaded_file", "imported_file"}:
            return "uploaded_file"
        return None

    def _uses_source_segment_namespace(audio_source: Any) -> bool:
        return str(audio_source or "").strip().lower() in {
            "tauri_native_mic",
            "native_microphone_streaming",
            "tauri_system_audio",
            "macos_system_audio",
        }

    def _commit_v2_final(session_id: str, event: dict[str, Any]) -> dict[str, Any] | None:
        if v2_persistence is None:
            return None
        raw_segment_id = str(event.get("segment_id") or "").strip()
        text = str(event.get("text") or "").strip()
        if not raw_segment_id or not text:
            raise ValueError("V2 final commit requires segment_id and text")
        source_track = _transcript_source_track(
            event.get("source_track") or event.get("audio_source") or event.get("input_source")
        )
        use_source_namespace = bool(
            event.get(
                "use_source_segment_namespace",
                source_track in {"microphone", "system_audio"},
            )
        )
        segment_id = (
            f"{source_track}:{raw_segment_id}"
            if use_source_namespace
            and source_track in {"microphone", "system_audio"}
            and not raw_segment_id.startswith(f"{source_track}:")
            else raw_segment_id
        )
        normalized_text = str(event.get("normalized_text") or text).strip()
        evidence_hash = transcript_evidence_hash(segment_id, normalized_text)
        committed = v2_persistence.commit_final_and_enqueue(
            meeting_id=session_id,
            final_id=f"final:{session_id}:{segment_id}",
            segment_id=segment_id,
            text=text,
            normalized_text=normalized_text,
            started_at_ms=int(event.get("start_ms") or 0),
            ended_at_ms=int(event.get("end_ms") or event.get("received_at_ms") or 0),
            evidence_hash=evidence_hash,
            now_ms=time.time_ns() // 1_000_000,
            speaker_id=(str(event["speaker_id"]) if event.get("speaker_id") is not None else None),
            speaker_confidence=event.get("speaker_confidence"),
            correlation_id=session_id,
            source_track=source_track,
        )
        _log.info(
            "meeting.v2.final_committed",
            session_id=session_id,
            segment_id=segment_id,
            event_seq=committed["event_seq"],
            created=committed["created"],
        )
        if committed["created"]:
            trace_at_ns = time.monotonic_ns()
            for lane, job_id in committed["job_ids"].items():
                generation_id = (
                    f"suggestion:{session_id}:final:{session_id}:{segment_id}" if lane == "suggestion" else None
                )
                audio_active_ns = audio_active_marks.get(session_id)
                if audio_active_ns is not None:
                    pipeline_traces.observe(
                        job_id,
                        "audio_active",
                        meeting_id=session_id,
                        job_id=job_id,
                        generation_id=generation_id,
                        monotonic_ns=audio_active_ns,
                    )
                pipeline_traces.observe(
                    job_id,
                    "final_committed",
                    meeting_id=session_id,
                    job_id=job_id,
                    generation_id=generation_id,
                    monotonic_ns=trace_at_ns,
                    attributes={"segment_id": segment_id},
                )
                pipeline_traces.observe(
                    job_id,
                    "job_queued",
                    meeting_id=session_id,
                    job_id=job_id,
                    generation_id=generation_id,
                    monotonic_ns=trace_at_ns,
                    attributes={"lane": lane},
                )
            executor = getattr(app.state, "v2_executor", None)
            if executor is not None:
                executor.wake()
        return committed

    app.state.commit_v2_final = _commit_v2_final

    import_runtime_methods = (
        "create_import_job",
        "get_import_job",
        "list_import_jobs",
        "claim_import_job",
        "update_import_job_stage",
        "complete_import_job",
        "fail_import_job",
        "recover_interrupted_import_jobs",
    )
    import_worker_id = f"recording-import-{uuid.uuid4().hex}"
    import_lease_ms = 30_000
    import_heartbeat_seconds = 5.0

    class _ImportLeaseLost(RuntimeError):
        pass

    class _FileAsrComponentMissing(RuntimeError):
        pass

    def _recording_import_runtime_ready() -> bool:
        return v2_persistence is not None and all(
            callable(getattr(v2_persistence, method, None)) for method in import_runtime_methods
        )

    async def _update_recording_import_stage(
        *,
        job_id: str,
        stage: str,
        progress: int,
    ) -> dict[str, Any]:
        if v2_persistence is None:
            raise _ImportLeaseLost("recording import persistence is unavailable")
        updated = await asyncio.to_thread(
            v2_persistence.update_import_job_stage,
            job_id=job_id,
            worker_id=import_worker_id,
            stage=stage,
            progress=progress,
            now_ms=time.time_ns() // 1_000_000,
            lease_ms=import_lease_ms,
        )
        if updated is None:
            raise _ImportLeaseLost(f"recording import lease was lost: {job_id}")
        return updated

    async def _run_import_blocking_stage(
        *,
        job_id: str,
        stage: str,
        progress: int,
        function: Any,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
    ) -> Any:
        await _update_recording_import_stage(
            job_id=job_id,
            stage=stage,
            progress=progress,
        )
        operation = asyncio.create_task(
            asyncio.to_thread(function, *args, **dict(kwargs or {})),
            name=f"recording-import-{stage}-{job_id}",
        )
        try:
            while True:
                done, _ = await asyncio.wait(
                    (operation,),
                    timeout=import_heartbeat_seconds,
                )
                if operation in done:
                    return operation.result()
                await _update_recording_import_stage(
                    job_id=job_id,
                    stage=stage,
                    progress=progress,
                )
        except asyncio.CancelledError:
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise

    def _upsert_import_asr_live_record(
        *,
        meeting_id: str,
        normalized_text: str,
        asr_report: dict[str, Any],
        audio_asset: dict[str, Any],
    ) -> None:
        streaming_events = (
            [
                {
                    "event_type": "final",
                    "segment_id": "import_seg_0001",
                    "text": normalized_text,
                    "start_ms": 0,
                    "end_ms": int(audio_asset.get("duration_ms") or 0),
                    "received_at_ms": 0,
                    "confidence": 0.9,
                }
            ]
            if normalized_text
            else []
        )
        live_events = build_asr_live_events(
            session_id=meeting_id,
            provider="local_funasr_batch",
            streaming_events=streaming_events,
            is_mock=False,
        )
        semantic_quality = (
            evaluate_semantic_quality(normalized_text)
            if normalized_text
            else {
                "status": "not_evaluated",
                "blocker": None,
                "quality_failure_reasons": ["transcript_empty"],
            }
        )
        record = {
            "session_id": meeting_id,
            "provider": "local_funasr_batch",
            "provider_mode": "real",
            "is_mock": False,
            "asr_fallback_used": False,
            "degradation_reasons": (
                [ASR_SEMANTIC_QUALITY_BLOCKER]
                if semantic_quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER
                else []
            ),
            "asr_semantic_quality": semantic_quality,
            "post_meeting_asr_profile": _post_meeting_asr_profile(asr_report),
            "audio_source": "uploaded_file",
            "input_source": "uploaded_file",
            "audio": audio_asset,
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "events": live_events,
        }
        try:
            existing = asr_live_repo.get(meeting_id)
        except KeyError:
            asr_live_repo.create(record)
        else:
            asr_live_repo.replace({**existing, **record})

    async def _execute_recording_import_job(job: dict[str, Any]) -> None:
        if v2_persistence is None or data_dir_path is None:
            raise RuntimeError("recording import runtime requires persistent storage")
        job_id = str(job["id"])
        meeting_id = str(job["meeting_id"])
        source_path = audio_assets.safe_audio_path(
            data_dir_path,
            str(job["source_relative_path"]),
        )
        canonical_path: Path | None = None
        try:
            await _update_recording_import_stage(
                job_id=job_id,
                stage="reading",
                progress=5,
            )
            source_size = await asyncio.to_thread(lambda: source_path.stat().st_size)
            if source_size <= 0 or source_size != int(job["file_size_bytes"]):
                raise ValueError("managed import source is missing, empty, or incomplete")
            if not batch_transcribe.is_available():
                raise _FileAsrComponentMissing("local file ASR component is unavailable")

            await _update_recording_import_stage(
                job_id=job_id,
                stage="normalizing",
                progress=20,
            )
            asr_report = await _run_import_blocking_stage(
                job_id=job_id,
                stage="transcribing",
                progress=40,
                function=batch_transcribe.transcribe_file_report,
                args=(source_path,),
                kwargs={"preserve_preprocessed": True},
            )
            raw_text = str(asr_report.get("text") or "").strip()
            normalized_text = _normalize_text(raw_text)
            canonical_path = Path(str(asr_report.get("normalized_audio_path") or source_path))
            if not canonical_path.exists():
                canonical_path = await _run_import_blocking_stage(
                    job_id=job_id,
                    stage="transcribing",
                    progress=55,
                    function=batch_transcribe.ensure_wav_16k_mono,
                    args=(source_path,),
                )
            managed_root = data_dir_path.resolve()
            canonical_path = canonical_path.resolve()
            if managed_root != canonical_path and managed_root not in canonical_path.parents:
                raise ValueError("normalized import audio escaped the managed data directory")
            if canonical_path.suffix.lower() != ".wav":
                raise ValueError("imported audio could not be normalized to WAV")

            audio_asset = await asyncio.to_thread(
                audio_assets.persist_imported_wav_asset_from_path,
                data_dir=data_dir_path,
                session_id=meeting_id,
                source_path=canonical_path,
                original_filename=str(job["original_filename"]),
            )
            meeting = v2_persistence.get_meeting(meeting_id)
            await asyncio.to_thread(
                v2_persistence.register_imported_recording,
                meeting_id=meeting_id,
                source_type="imported_file",
                relative_path=str(audio_asset["relative_path"]),
                sha256=str(audio_asset["sha256"]),
                sample_rate_hz=int(audio_asset["sample_rate_hz"] or 0),
                sample_count=int(
                    round(int(audio_asset["duration_ms"] or 0) * int(audio_asset["sample_rate_hz"] or 0) / 1_000)
                ),
                duration_ms=int(audio_asset["duration_ms"] or 0),
                file_size_bytes=int(audio_asset["file_size_bytes"] or 0),
                started_at_ms=int(meeting.get("started_at_ms") or time.time_ns() // 1_000_000),
                now_ms=time.time_ns() // 1_000_000,
            )
            await asyncio.to_thread(
                _upsert_import_asr_live_record,
                meeting_id=meeting_id,
                normalized_text=normalized_text,
                asr_report=asr_report,
                audio_asset=audio_asset,
            )

            await _update_recording_import_stage(
                job_id=job_id,
                stage="correcting",
                progress=70,
            )
            if normalized_text:
                _commit_v2_final(
                    meeting_id,
                    {
                        "segment_id": "import_seg_0001",
                        "text": raw_text,
                        "normalized_text": normalized_text,
                        "start_ms": 0,
                        "end_ms": int(audio_asset["duration_ms"] or 0),
                        "received_at_ms": time.time_ns() // 1_000_000,
                        "source_track": "uploaded_file",
                    },
                )
            await asyncio.to_thread(
                v2_persistence.end_meeting,
                meeting_id=meeting_id,
                now_ms=time.time_ns() // 1_000_000,
                correlation_id=meeting_id,
            )
            executor = getattr(app.state, "v2_executor", None)
            if executor is not None:
                executor.wake()
            await _update_recording_import_stage(
                job_id=job_id,
                stage="reviewing",
                progress=90,
            )
            completed = await asyncio.to_thread(
                v2_persistence.complete_import_job,
                job_id=job_id,
                worker_id=import_worker_id,
                now_ms=time.time_ns() // 1_000_000,
            )
            if completed is None:
                raise _ImportLeaseLost(f"recording import lease was lost: {job_id}")
        finally:
            playback_path = audio_assets.safe_audio_path(
                data_dir_path,
                f"audio_assets/{meeting_id}/audio.wav",
            )
            if canonical_path is not None and canonical_path not in {source_path, playback_path}:
                canonical_path.unlink(missing_ok=True)

    async def _fail_recording_import_job(
        job: dict[str, Any],
        error: Exception,
    ) -> None:
        if v2_persistence is None:
            return
        if isinstance(error, _FileAsrComponentMissing):
            error_class = "file_asr_component_missing"
            message = "本地文件转写组件未安装或尚未就绪，原始录音已保留。"
            retryable = False
        elif isinstance(error, subprocess.TimeoutExpired):
            error_class = "file_asr_timeout"
            message = "本地文件转写超时，原始录音已保留。"
            retryable = True
        elif isinstance(error, OSError):
            error_class = "file_import_io_error"
            message = "本地录音读取或写入失败，原始录音已保留。"
            retryable = True
        else:
            error_class = "file_import_processing_failed"
            message = "本地录音处理失败，原始录音已保留。"
            retryable = False
        now_ms = time.time_ns() // 1_000_000
        failed = await asyncio.to_thread(
            v2_persistence.fail_import_job,
            job_id=str(job["id"]),
            worker_id=import_worker_id,
            error_class=error_class,
            error_message=message,
            retryable=retryable,
            next_attempt_at_ms=now_ms + 1_000,
            now_ms=now_ms,
        )
        if failed is None:
            _log.warning(
                "meeting.recording_import.failure_not_persisted",
                import_job_id=job["id"],
                meeting_id=job["meeting_id"],
                error_class=error_class,
            )

    async def _recording_import_worker() -> None:
        if v2_persistence is None:
            return
        while not recording_import_stop_event.is_set():
            try:
                job = await asyncio.to_thread(
                    v2_persistence.claim_import_job,
                    worker_id=import_worker_id,
                    now_ms=time.time_ns() // 1_000_000,
                    lease_ms=import_lease_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.error(
                    "meeting.recording_import.claim_failed",
                    error_class=type(exc).__name__,
                )
                await asyncio.sleep(0.25)
                continue
            if job is None:
                recording_import_wake_event.clear()
                try:
                    async with asyncio.timeout(0.25):
                        await recording_import_wake_event.wait()
                except TimeoutError:
                    pass
                continue
            try:
                await _execute_recording_import_job(job)
            except asyncio.CancelledError:
                raise
            except _ImportLeaseLost as exc:
                _log.warning(
                    "meeting.recording_import.lease_lost",
                    import_job_id=job["id"],
                    meeting_id=job["meeting_id"],
                    error_class=type(exc).__name__,
                )
            except Exception as exc:
                _log.error(
                    "meeting.recording_import.failed",
                    import_job_id=job["id"],
                    meeting_id=job["meeting_id"],
                    error_class=type(exc).__name__,
                )
                try:
                    await _fail_recording_import_job(job, exc)
                except asyncio.CancelledError:
                    raise
                except Exception as persistence_exc:
                    _log.error(
                        "meeting.recording_import.failure_write_failed",
                        import_job_id=job["id"],
                        meeting_id=job["meeting_id"],
                        error_class=type(persistence_exc).__name__,
                    )

    def _record_audio_active(
        session_id: str,
        observation: dict[str, Any],
    ) -> None:
        audio_active_marks.setdefault(
            session_id,
            int(observation["monotonic_ns"]),
        )

    app.state.record_audio_active = _record_audio_active

    def _v2_recording_track_id(source_type: str, explicit_track_id: object = None) -> str:
        explicit = str(explicit_track_id or "").strip()
        if explicit in {"microphone", "system_audio"}:
            return explicit
        return "system_audio" if "system_audio" in source_type else "microphone"

    def _record_v2_audio_chunk(
        session_id: str,
        chunk: dict[str, Any],
        *,
        lease_owner: str | None = None,
    ) -> dict[str, Any] | None:
        if v2_persistence is None:
            return None
        source_type = str(chunk.get("source_type") or "microphone")
        track = _v2_recording_track_id(source_type, chunk.get("track_id"))
        return v2_persistence.record_audio_chunk(
            meeting_id=session_id,
            track=track,
            epoch=int(chunk.get("epoch") or 0),
            chunk_seq=int(chunk["sequence"] if chunk.get("sequence") is not None else chunk["chunk_index"]),
            relative_path=str(chunk["relative_path"]),
            sha256=str(chunk["sha256"]),
            sample_rate_hz=int(chunk["sample_rate_hz"]),
            sample_count=int(chunk["sample_count"]),
            duration_ms=int(chunk["duration_ms"]),
            file_size_bytes=int(chunk["file_size_bytes"]),
            captured_at_ms=(int(chunk["timestamp_ms"]) if chunk.get("timestamp_ms") is not None else None),
            source_sequence_start=chunk.get("source_sequence_start"),
            source_sequence_end=chunk.get("source_sequence_end"),
            source_timestamp_start_ms=chunk.get("source_timestamp_start_ms"),
            source_timestamp_end_ms=chunk.get("source_timestamp_end_ms"),
            now_ms=time.time_ns() // 1_000_000,
            lease_owner=lease_owner,
            lease_ms=recording_capture_lease_ms if lease_owner is not None else None,
        )

    app.state.record_v2_audio_chunk = _record_v2_audio_chunk

    def _begin_v2_recording(
        session_id: str,
        metadata: dict[str, Any],
        *,
        lease_owner: str,
    ) -> dict[str, Any] | None:
        if v2_persistence is None:
            return None
        source_type = str(metadata.get("source_type") or "browser_live_mic")
        track = _v2_recording_track_id(source_type, metadata.get("track_id"))
        return v2_persistence.begin_recording(
            meeting_id=session_id,
            track=track,
            epoch=int(metadata.get("epoch") or 0),
            source_type=source_type,
            sample_rate_hz=int(metadata.get("sample_rate_hz") or 16_000),
            lease_owner=lease_owner,
            lease_ms=recording_capture_lease_ms,
            now_ms=time.time_ns() // 1_000_000,
        )

    def _heartbeat_v2_recording(
        session_id: str,
        *,
        source_type: str,
        lease_owner: str,
        track_id: str | None = None,
        epoch: int = 0,
    ) -> bool:
        if v2_persistence is None:
            return True
        track = _v2_recording_track_id(source_type, track_id)
        return v2_persistence.heartbeat_recording(
            meeting_id=session_id,
            track=track,
            epoch=int(epoch),
            lease_owner=lease_owner,
            lease_ms=recording_capture_lease_ms,
            now_ms=time.time_ns() // 1_000_000,
        )

    def _authorize_v2_audio_chunk(
        session_id: str,
        chunk: dict[str, Any],
        *,
        lease_owner: str,
    ) -> bool:
        return _heartbeat_v2_recording(
            session_id,
            source_type=str(chunk.get("source_type") or "browser_live_mic"),
            lease_owner=lease_owner,
            track_id=(str(chunk["track_id"]) if chunk.get("track_id") else None),
            epoch=int(chunk.get("epoch") or 0),
        )

    def _seal_v2_recording(
        session_id: str,
        metadata: dict[str, Any],
        *,
        lease_owner: str,
    ) -> dict[str, Any] | None:
        if v2_persistence is None:
            return None
        source_type = str(metadata.get("source_type") or "browser_live_mic")
        track = _v2_recording_track_id(source_type, metadata.get("track_id"))
        epoch = int(metadata.get("epoch") or 0)
        sealed = v2_persistence.seal_recording_and_enqueue_export(
            meeting_id=session_id,
            track=track,
            epoch=epoch,
            lease_owner=lease_owner,
            output_relative_path=str(metadata.get("relative_path") or f"audio_assets/{session_id}/audio.wav"),
            expected_journal_sha256=(str(metadata["journal_sha256"]) if metadata.get("journal_sha256") else None),
            interrupted=bool(metadata.get("interrupted")),
            now_ms=time.time_ns() // 1_000_000,
        )
        exporter = getattr(app.state, "recording_export_executor", None)
        if exporter is not None:
            exporter.wake()
        return sealed

    app.state.begin_v2_recording = _begin_v2_recording
    app.state.heartbeat_v2_recording = _heartbeat_v2_recording
    app.state.seal_v2_recording = _seal_v2_recording

    def _close_created_sqlite_repositories() -> None:
        for sqlite_repository in reversed(sqlite_repositories):
            sqlite_repository.close()

    provider_probe_cache: dict[str, Any] = {
        "key": None,
        "expires_at_monotonic": 0.0,
        "result": None,
    }
    llm_lane_locks = LaneLockRegistry()
    app.state.llm_lane_locks = llm_lane_locks
    app.state.llm_session_locks = llm_lane_locks._locks

    def _current_settings() -> dict[str, Any]:
        return SettingsPayload.model_validate(settings_usage_repo.get_settings()).model_dump(mode="json")

    def _current_auto_suggestion_runtime_policy() -> dict[str, Any]:
        return auto_suggestion_orchestrator.build_runtime_policy(
            _current_settings()["suggestions"],
            degradation_level=get_degradation_controller().level,
        )

    def _persist_auto_suggestion_runtime_policy(
        session_id: str,
        runtime_policy: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:

        def persist_runtime_policy(latest: dict[str, Any]) -> dict[str, Any]:
            updated, _status = auto_suggestion_orchestrator.apply_runtime_policy(
                latest,
                runtime_policy,
            )
            return updated

        updated = asr_live_repo.update(session_id, persist_runtime_policy)
        return (
            updated,
            auto_suggestion_orchestrator.status_from_record(updated),
        )

    def _llm_cost_rates() -> tuple[float, float] | None:
        prompt_raw = os.environ.get("LLM_PROMPT_CNY_PER_1M_TOKENS")
        completion_raw = os.environ.get("LLM_COMPLETION_CNY_PER_1M_TOKENS")
        if prompt_raw is None or completion_raw is None:
            return None
        try:
            prompt_rate = float(prompt_raw)
            completion_rate = float(completion_raw)
        except ValueError:
            return None
        if prompt_rate < 0 or completion_rate < 0:
            return None
        return prompt_rate, completion_rate

    def _estimated_usage_cost(record: dict[str, Any], rates: tuple[float, float]) -> float:
        prompt_rate, completion_rate = rates
        return (
            int(record.get("prompt_tokens") or 0) * prompt_rate
            + int(record.get("completion_tokens") or 0) * completion_rate
        ) / 1_000_000

    def _period_start_ms(*, month: bool) -> int:
        now = datetime.now().astimezone()
        start = now.replace(
            day=1 if month else now.day,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return int(start.timestamp() * 1_000)

    def _usage_cost_total(records: list[dict[str, Any]], rates: tuple[float, float]) -> float:
        return sum(_estimated_usage_cost(record, rates) for record in records)

    def _usage_has_pricing_split(record: dict[str, Any]) -> bool:
        return int(record.get("total_tokens") or 0) == (
            int(record.get("prompt_tokens") or 0) + int(record.get("completion_tokens") or 0)
        )

    def _estimated_period_cost(
        records: list[dict[str, Any]],
        rates: tuple[float, float] | None,
    ) -> float | None:
        if rates is None or any(not _usage_has_pricing_split(record) for record in records):
            return None
        return _usage_cost_total(records, rates)

    def _cost_stats() -> dict[str, Any]:
        all_records = settings_usage_repo.list_usage()
        latest_session_id = str(all_records[-1]["session_id"]) if all_records else None
        session_records = (
            [record for record in all_records if record["session_id"] == latest_session_id]
            if latest_session_id is not None
            else []
        )
        today_records = [
            record for record in all_records if int(record["timestamp_ms"]) >= _period_start_ms(month=False)
        ]
        month_records = [
            record for record in all_records if int(record["timestamp_ms"]) >= _period_start_ms(month=True)
        ]
        rates = _llm_cost_rates()
        groups: dict[tuple[str, str, str], dict[str, Any]] = {}
        for record in month_records:
            key = (record["purpose"], record["provider"], record["model"])
            group = groups.setdefault(
                key,
                {
                    "name": record["purpose"],
                    "purpose": record["purpose"],
                    "provider": record["provider"],
                    "model": record["model"],
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            group["prompt_tokens"] += int(record["prompt_tokens"])
            group["completion_tokens"] += int(record["completion_tokens"])
            group["total_tokens"] += int(record["total_tokens"])
        breakdown: list[dict[str, Any]] = []
        for group in groups.values():
            group["tokens"] = group["total_tokens"]
            group_cost = _estimated_period_cost([group], rates)
            group["estimated_cost_cny"] = group_cost
            group["costStatus"] = "estimated" if group_cost is not None else "unavailable"
            breakdown.append(group)
        breakdown.sort(key=lambda item: (item["purpose"], item["provider"], item["model"]))
        session_cost = _estimated_period_cost(session_records, rates)
        today_cost = _estimated_period_cost(today_records, rates)
        month_cost = _estimated_period_cost(month_records, rates)
        cost_status = "estimated" if month_cost is not None else "unavailable"
        return {
            "currentSession": session_cost,
            "today": today_cost,
            "month": month_cost,
            "currentSessionTokens": sum(int(record.get("total_tokens") or 0) for record in session_records),
            "todayTokens": sum(int(record.get("total_tokens") or 0) for record in today_records),
            "monthTokens": sum(int(record.get("total_tokens") or 0) for record in month_records),
            "breakdown": breakdown,
            "currency": "CNY",
            "costStatus": cost_status,
            "estimated": cost_status == "estimated",
            "currentSessionId": latest_session_id,
            "rateEnvironmentVariables": {
                "prompt": "LLM_PROMPT_CNY_PER_1M_TOKENS",
                "completion": "LLM_COMPLETION_CNY_PER_1M_TOKENS",
            },
        }

    def _enforce_llm_budget(
        session_id: str,
        *,
        purpose: str,
        config: llm_service.LlmConfig | None,
    ) -> None:
        degradation = get_degradation_controller()
        if not degradation.can_call_llm():
            reason = f"degradation_level_{degradation.level}_paid_ai_disabled"
            try:
                asr_live_repo.update(
                    session_id,
                    lambda record: {
                        **record,
                        "degradation_reasons": list(
                            dict.fromkeys(
                                [
                                    *list(record.get("degradation_reasons") or []),
                                    reason,
                                ]
                            )
                        ),
                    },
                )
            except (KeyError, ValueError):
                pass
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "llm_disabled_by_degradation",
                    "purpose": purpose,
                    "degradationLevel": degradation.level,
                    "reason": degradation.reason,
                },
            )
        if config is None:
            return
        if config.is_mock:
            return
        rates = _llm_cost_rates()
        if rates is None:
            _log.warning(
                "llm.budget.unavailable",
                session_id=session_id,
                purpose=purpose,
                reason="rates_not_configured",
            )
            return
        settings = _current_settings()
        budget = settings["budget"]
        session_records = settings_usage_repo.list_usage(session_id=session_id)
        daily_records = settings_usage_repo.list_usage(since_ms=_period_start_ms(month=False))
        if any(not _usage_has_pricing_split(record) for record in [*session_records, *daily_records]):
            _log.warning(
                "llm.budget.unavailable",
                session_id=session_id,
                purpose=purpose,
                reason="usage_token_split_unavailable",
            )
            return
        session_cost = _usage_cost_total(session_records, rates)
        daily_cost = _usage_cost_total(daily_records, rates)
        checks = (
            ("session", session_cost, float(budget["session_limit_cny"])),
            ("daily", daily_cost, float(budget["daily_limit_cny"])),
        )
        for scope, current_cost, limit in checks:
            if current_cost >= limit:
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "llm_budget_exceeded",
                        "scope": scope,
                        "purpose": purpose,
                        "currentEstimatedCostCny": current_cost,
                        "limitCny": limit,
                        "costStatus": "estimated",
                    },
                )

    def _record_llm_usage(
        session_id: str,
        *,
        purpose: str,
        config: llm_service.LlmConfig,
        usage: dict[str, Any] | None,
    ) -> None:
        if config.is_mock:
            return
        usage = dict(usage or {})
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens") or 0))
        total_tokens = max(
            prompt_tokens + completion_tokens,
            int(usage.get("total_tokens") or 0),
        )
        if total_tokens <= 0:
            return
        audit = llm_service.provider_audit_metadata(config, purpose=purpose)
        settings_usage_repo.record_usage(
            session_id=session_id,
            purpose=purpose,
            provider=audit["provider"],
            model=audit["model"],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            timestamp_ms=int(time.time() * 1_000),
        )

    def _llm_session_evidence(session_id: str) -> dict[str, Any]:
        """Return safe, session-scoped evidence that paid LLM work really ran."""
        config = llm_service.LlmConfig.from_env()
        usage_records = [
            record
            for record in settings_usage_repo.list_usage(session_id=session_id)
            if int(record.get("total_tokens") or 0) > 0
        ]
        provider = (
            llm_service.provider_identifier(config)
            if config is not None
            else (str(usage_records[0].get("provider") or "not_configured") if usage_records else "not_configured")
        )
        model = (
            str(config.model)
            if config is not None
            else (str(usage_records[0].get("model") or "not_called") if usage_records else "not_called")
        )
        is_mock = bool(config.is_mock) if config is not None else False
        called = bool(usage_records)
        return {
            "schema_version": "llm-session-evidence.v1",
            "source": "runtime_config_and_usage_ledger",
            "configured": config is not None,
            "provider": provider,
            "model": model,
            "is_mock": is_mock,
            "gateway_base_url_kind": llm_service.gateway_base_url_kind(config.base_url if config is not None else None),
            "llm_called": called,
            "llm_call_count": len(usage_records),
            "llm_usage_total_tokens": sum(int(record.get("total_tokens") or 0) for record in usage_records),
        }

    def _meeting_llm_usage_summary(session_id: str) -> dict[str, Any]:
        records = settings_usage_repo.list_usage(session_id=session_id)
        rates = _llm_cost_rates()
        estimated_cost = _estimated_period_cost(records, rates)
        purposes: dict[str, dict[str, int]] = {}
        for record in records:
            purpose = str(record.get("purpose") or "unknown")
            summary = purposes.setdefault(
                purpose,
                {
                    "call_count": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            )
            summary["call_count"] += 1
            summary["prompt_tokens"] += int(record.get("prompt_tokens") or 0)
            summary["completion_tokens"] += int(record.get("completion_tokens") or 0)
            summary["total_tokens"] += int(record.get("total_tokens") or 0)
        return {
            "call_count": len(records),
            "prompt_tokens": sum(int(record.get("prompt_tokens") or 0) for record in records),
            "completion_tokens": sum(int(record.get("completion_tokens") or 0) for record in records),
            "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
            "estimated_cost_cny": estimated_cost,
            "cost_status": "estimated" if estimated_cost is not None else "unavailable",
            "purposes": purposes,
        }

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if FRONTEND_V2_DIST_DIR.joinpath("assets").is_dir():
        app.mount(
            "/workbench-assets",
            StaticFiles(directory=FRONTEND_V2_DIST_DIR),
            name="workbench-v2-assets",
        )

    @app.middleware("http")
    async def _structured_request_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        _log.info("request.start")
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            _log.error("request.error", duration_ms=duration_ms, exc_info=True)
            raise
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        _log.info("request.end", status_code=response.status_code, duration_ms=duration_ms)
        response.headers["x-request-id"] = request_id
        return response

    @app.get(BOOTSTRAP_PATH)
    def desktop_bootstrap(token: str, next: str = "/workbench") -> Response:
        if not local_api_token:
            raise HTTPException(status_code=404, detail="desktop bootstrap is disabled")
        if next not in {"/workbench", "/workbench-v2"}:
            raise HTTPException(status_code=422, detail="desktop bootstrap target is invalid")
        if not hmac.compare_digest(token, local_api_token):
            raise HTTPException(status_code=403, detail="desktop bootstrap token is invalid")
        response = RedirectResponse(next, status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_cookie_value(local_api_token),
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/health")
    def health() -> dict[str, str]:
        result = {"status": "ok", "service": "meeting-copilot-web-mvp"}
        if local_api_token:
            result["instance_proof"] = health_proof(local_api_token)
        return result

    @app.get("/v2/diagnostics/application-schema")
    def application_schema_diagnostics() -> dict[str, object]:
        return dict(application_schema_migration_report)

    def _require_v2_persistence() -> V2Persistence:
        if v2_persistence is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "v2_persistence_unavailable",
                    "message": "V2 meeting state requires a persistent data directory",
                },
            )
        return v2_persistence

    def _require_data_governance() -> DataGovernanceService:
        if data_governance_service is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "data_governance_unavailable",
                    "message": "Data governance requires a persistent local data directory",
                },
            )
        return data_governance_service

    def _require_meeting_preparation_store() -> MeetingPreparationStore:
        if meeting_preparation_store is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "meeting_preparation_unavailable",
                    "message": "Meeting preparation requires a persistent data directory",
                },
            )
        return meeting_preparation_store

    def _review_document_kind(value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in REVIEW_DOCUMENT_KINDS:
            raise HTTPException(status_code=422, detail="unsupported review document kind")
        return normalized

    @app.get("/v2/data-governance/settings")
    def get_v2_data_governance_settings() -> dict[str, Any]:
        return _require_data_governance().get_settings()

    @app.patch("/v2/data-governance/settings")
    def patch_v2_data_governance_settings(payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"retention_policy"}:
            raise HTTPException(
                status_code=422,
                detail="settings must contain only retention_policy",
            )
        try:
            return _require_data_governance().set_retention_policy(
                payload["retention_policy"],
                now_ms=time.time_ns() // 1_000_000,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/data-governance/deletions/{job_id}")
    def get_v2_data_deletion_job(job_id: str) -> dict[str, Any]:
        try:
            return _require_v2_persistence().get_deletion_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="deletion job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/data-governance/audit")
    def get_v2_data_governance_audit(
        meeting_id: str | None = Query(default=None),
        deletion_job_id: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1_000),
    ) -> dict[str, Any]:
        try:
            events = _require_v2_persistence().list_data_governance_audit_events(
                meeting_id=meeting_id,
                deletion_job_id=deletion_job_id,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"events": events}

    def _current_realtime_slo_reports() -> dict[str, Any]:
        return realtime_slo.report_all(active_traces=pipeline_traces.slo_snapshots())

    @app.get("/v2/diagnostics/realtime-ai-slo")
    def get_v2_realtime_ai_slo_reports() -> dict[str, Any]:
        return _current_realtime_slo_reports()

    @app.get("/v2/meetings/{meeting_id}/realtime-ai-slo")
    def get_v2_meeting_realtime_ai_slo(meeting_id: str) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        if not persistence.meeting_exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}")
        report = realtime_slo.report(
            meeting_id=meeting_id,
            active_traces=pipeline_traces.slo_snapshots(),
        )
        report["token_usage"] = _meeting_llm_usage_summary(meeting_id)
        return report

    @app.get("/v2/meetings/{meeting_id}/preparation")
    def get_v2_meeting_preparation(meeting_id: str) -> dict[str, Any]:
        store = _require_meeting_preparation_store()
        try:
            preparation = store.get(meeting_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if preparation is None:
            return {
                "meeting_id": meeting_id,
                "hotwords": [],
                "input_source": "microphone",
                "input_device_id": None,
                "input_device_name": None,
                "notice_acknowledged": False,
                "updated_at_ms": 0,
            }
        return preparation.to_dict()

    @app.put("/v2/meetings/{meeting_id}/preparation")
    def put_v2_meeting_preparation(
        meeting_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        store = _require_meeting_preparation_store()
        try:
            preparation = store.save(
                meeting_id,
                hotwords=payload.get("hotwords") or [],
                input_source=str(payload.get("input_source") or "microphone"),
                input_device_id=payload.get("input_device_id"),
                input_device_name=payload.get("input_device_name"),
                notice_acknowledged=bool(payload.get("notice_acknowledged", False)),
                updated_at_ms=time.time_ns() // 1_000_000,
            )
            asr_stream.set_session_hotwords(meeting_id, list(preparation.hotwords))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return preparation.to_dict()

    @app.post("/v2/meetings", status_code=201)
    def create_v2_meeting(payload: dict[str, Any]) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        meeting_id = str(payload.get("meeting_id") or f"meeting_{uuid.uuid4().hex}")
        if not SESSION_ID_PATTERN.fullmatch(meeting_id):
            raise HTTPException(status_code=422, detail="meeting_id contains unsafe characters")
        try:
            expected_duration_seconds = float(payload.get("expected_duration_seconds") or 3_600)
            if data_dir_path is None:
                raise RuntimeError("persistent data directory is unavailable")
            preflight = preflight_meeting_storage(
                data_dir=data_dir_path,
                expected_duration_seconds=expected_duration_seconds,
                track_count=max(1, int(payload.get("track_count") or 1)),
            )
        except (UnsafeManagedPathError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not preflight.allowed:
            raise HTTPException(status_code=507, detail=preflight.to_dict())
        try:
            meeting = persistence.create_meeting(
                meeting_id=meeting_id,
                title=str(payload.get("title") or "").strip() or None,
                now_ms=time.time_ns() // 1_000_000,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"meeting": meeting, "storage_preflight": preflight.to_dict()}

    @app.patch("/v2/meetings/{meeting_id}")
    def update_v2_meeting(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        try:
            title = _validated_v2_meeting_title(payload.get("title"))
            meeting = persistence.update_meeting_title(
                meeting_id=meeting_id,
                title=title,
                title_source="user",
                now_ms=time.time_ns() // 1_000_000,
            )
            snapshot = _v2_snapshot_with_meeting_metadata(persistence, meeting_id)
            snapshot = _decorate_v2_snapshot(snapshot, meeting_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="meeting not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"meeting": meeting, "snapshot": snapshot}

    @app.post("/v2/meetings/import-audio", status_code=202)
    async def import_v2_meeting_audio(
        response: Response,
        file: UploadFile = File(...),
        title: str = Form(""),
    ) -> dict[str, Any]:
        """Persist an upload and enqueue the durable local import pipeline."""

        persistence = _require_v2_persistence()
        if data_dir_path is None:
            raise HTTPException(status_code=503, detail="persistent data directory is unavailable")
        if not _recording_import_runtime_ready():
            raise HTTPException(
                status_code=503,
                detail="durable recording import persistence is unavailable",
            )

        raw_filename = str(file.filename or "recording")
        original_filename = Path(raw_filename.replace("\\", "/")).name[:255] or "recording"
        suffix = Path(original_filename).suffix.lower() or ".audio"
        if len(suffix) > 12 or "/" in suffix or "\\" in suffix:
            suffix = ".audio"
        meeting_id = "import_" + uuid.uuid4().hex[:20]
        session_dir = audio_assets.safe_audio_path(
            data_dir_path,
            Path("audio_assets") / meeting_id,
        )
        session_dir.mkdir(parents=True, exist_ok=True)
        staged_path = session_dir / f".uploading{suffix}"
        source_audio_asset: dict[str, Any] | None = None
        meeting_created = False

        async def rollback_unqueued_import() -> None:
            if meeting_created:
                deletion_job = await _begin_deletion_fence(meeting_id)
                await _complete_deletion_facts(deletion_job)
            elif source_audio_asset is not None:
                await asyncio.to_thread(
                    audio_assets.delete_audio_asset,
                    data_dir_path,
                    source_audio_asset,
                )

        explicit_title = bool(str(title or "").strip())
        fallback_title = Path(original_filename).stem or "录音导入"
        cleaned_title = re.sub(
            r"[/\\\x00-\x1f\x7f]+",
            " ",
            str(title if explicit_title else fallback_title),
        )
        cleaned_title = " ".join(cleaned_title.split())[:200] or "录音导入"

        try:
            uploaded_bytes = 0
            with staged_path.open("wb") as uploaded_file:
                while chunk := await file.read(1024 * 1024):
                    uploaded_bytes += len(chunk)
                    if uploaded_bytes > 500 * 1024 * 1024:
                        raise HTTPException(
                            status_code=413,
                            detail="文件超过 500MB 限制，请缩短录音或分段导入",
                        )
                    uploaded_file.write(chunk)
                uploaded_file.flush()
                os.fsync(uploaded_file.fileno())
            if uploaded_bytes <= 0:
                raise HTTPException(status_code=422, detail="录音文件为空")

            source_audio_asset = await asyncio.to_thread(
                audio_assets.persist_uploaded_audio_asset_from_path,
                data_dir=data_dir_path,
                session_id=meeting_id,
                source_type="imported_original",
                filename=original_filename,
                source_path=staged_path,
            )
            now_ms = time.time_ns() // 1_000_000
            title_source = "user" if explicit_title else "import"
            meeting = persistence.create_meeting(
                meeting_id=meeting_id,
                title=cleaned_title,
                title_source=title_source,
                now_ms=now_ms,
            )
            meeting = {**meeting, "title_source": title_source}
            meeting_created = True
            import_job = persistence.create_import_job(
                meeting_id=meeting_id,
                source_relative_path=str(source_audio_asset["relative_path"]),
                original_filename=original_filename,
                file_size_bytes=int(source_audio_asset["file_size_bytes"]),
                now_ms=now_ms,
            )
        except HTTPException:
            await rollback_unqueued_import()
            raise
        except (OSError, ValueError, KeyError) as exc:
            await rollback_unqueued_import()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            staged_path.unlink(missing_ok=True)
            try:
                session_dir.rmdir()
            except OSError:
                pass

        recording_import_wake_event.set()
        response.headers["Location"] = f"/v2/meetings/{meeting_id}/import-job"
        return {
            "meeting": meeting,
            "meeting_id": meeting_id,
            "source": "uploaded_file",
            "provider": "local_funasr_batch",
            "source_audio": source_audio_asset,
            "import_job": import_job,
        }

    @app.get("/v2/meetings/{meeting_id}/import-job")
    def get_v2_recording_import_job(meeting_id: str) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        jobs = persistence.list_import_jobs(meeting_id=meeting_id)
        if not jobs:
            raise HTTPException(status_code=404, detail="recording import job not found")
        return {"import_job": jobs[0]}

    @app.post("/v2/meetings/{meeting_id}/import-job/retry", status_code=202)
    async def retry_v2_recording_import_job(
        meeting_id: str,
        response: Response,
    ) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        jobs = await asyncio.to_thread(
            persistence.list_import_jobs,
            meeting_id=meeting_id,
        )
        if not jobs:
            raise HTTPException(status_code=404, detail="recording import job not found")
        current = jobs[0]
        if current.get("status") != "failed":
            raise HTTPException(
                status_code=409,
                detail="only a failed recording import job can be retried",
            )
        source_path = (
            audio_assets.safe_audio_path(
                data_dir_path,
                str(current["source_relative_path"]),
            )
            if data_dir_path is not None
            else None
        )
        if source_path is None or not source_path.is_file():
            raise HTTPException(
                status_code=409,
                detail="the original recording is missing and cannot be retried",
            )
        retry_import_job = getattr(persistence, "retry_import_job", None)
        if not callable(retry_import_job):
            raise HTTPException(
                status_code=503,
                detail="durable recording import retry persistence is unavailable",
            )
        try:
            retried = await asyncio.to_thread(
                retry_import_job,
                job_id=str(current["id"]),
                now_ms=time.time_ns() // 1_000_000,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if retried is None:
            raise HTTPException(
                status_code=409,
                detail="recording import job could not be retried",
            )
        recording_import_wake_event.set()
        response.headers["Location"] = f"/v2/meetings/{meeting_id}/import-job"
        return {"meeting_id": meeting_id, "import_job": retried}

    @app.get("/v2/meetings")
    def list_v2_meetings(
        limit: int = 100,
        query: str = "",
        status: str = "all",
        before_updated_at_ms: int | None = None,
        before_meeting_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return _require_v2_persistence().list_meetings_page(
                limit=limit,
                query=query,
                status=status,
                before_updated_at_ms=before_updated_at_ms,
                before_meeting_id=before_meeting_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/meetings/{meeting_id}/snapshot")
    def get_v2_meeting_snapshot(
        meeting_id: str,
        segment_limit: int = 500,
    ) -> dict[str, Any]:
        try:
            snapshot = _v2_snapshot_with_meeting_metadata(
                _require_v2_persistence(),
                meeting_id,
                segment_limit=segment_limit,
            )
            snapshot = _decorate_v2_snapshot(snapshot, meeting_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            live_record = asr_live_repo.get(meeting_id)
        except KeyError:
            return snapshot
        degradation_reasons = [
            str(reason)
            for reason in live_record.get("degradation_reasons") or []
            if str(reason)
            in {
                "asr_semantic_quality_blocked",
                "asr_no_final",
                "asr_final_empty",
                "stream_interrupted",
            }
        ]
        formal_status = str(live_record.get("formal_derivation_status") or "").strip()
        if not formal_status:
            formal_status = (
                "suppressed_by_asr_semantic_quality"
                if "asr_semantic_quality_blocked" in degradation_reasons
                else "available"
            )
        diagnostics = dict(snapshot.get("diagnostics") or {})
        diagnostics["formal_derivation_status"] = formal_status
        diagnostics["degradation_reasons"] = degradation_reasons
        return {**snapshot, "diagnostics": diagnostics}

    @app.get("/v2/meetings/{meeting_id}/transcript")
    def get_v2_meeting_transcript(
        meeting_id: str,
        after_transcript_seq: int = 0,
        limit: int = 200,
        include_source_duplicates: bool = False,
    ) -> dict[str, Any]:
        try:
            return _require_v2_persistence().list_transcript_segments(
                meeting_id,
                after_transcript_seq=after_transcript_seq,
                limit=limit,
                include_source_duplicates=include_source_duplicates,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/meetings/{meeting_id}/semantic-paragraphs")
    def get_v2_meeting_semantic_paragraphs(meeting_id: str) -> dict[str, Any]:
        try:
            return _require_v2_persistence().list_semantic_paragraphs(meeting_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="meeting not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v2/meetings/{meeting_id}/speakers")
    def get_v2_meeting_speakers(meeting_id: str) -> dict[str, Any]:
        try:
            return _require_v2_persistence().list_meeting_speakers(meeting_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="meeting not found") from None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.patch("/v2/meetings/{meeting_id}/speakers/{speaker_id}")
    def rename_v2_meeting_speaker(
        meeting_id: str,
        speaker_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        speaker_label = payload.get("speaker_label")
        if not isinstance(speaker_label, str):
            raise HTTPException(status_code=422, detail="speaker_label must be a string")
        try:
            speaker = _require_v2_persistence().rename_meeting_speaker(
                meeting_id=meeting_id,
                speaker_id=speaker_id,
                speaker_label=speaker_label,
                now_ms=time.time_ns() // 1_000_000,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="speaker not found") from None
        except SpeakerLabelConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"speaker": speaker}

    @app.get("/v2/meetings/{meeting_id}/documents/{document_kind}")
    def get_v2_review_document(meeting_id: str, document_kind: str) -> dict[str, Any]:
        kind = _review_document_kind(document_kind)
        try:
            document = _require_v2_persistence().get_review_document(meeting_id, kind)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"document": document}

    @app.patch("/v2/meetings/{meeting_id}/documents/{document_kind}")
    def save_v2_review_document(
        meeting_id: str,
        document_kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        kind = _review_document_kind(document_kind)
        try:
            document = _require_v2_persistence().save_user_final_document(
                meeting_id=meeting_id,
                document_kind=kind,
                expected_revision=int(payload.get("expected_revision") or 0),
                content=payload.get("content_json"),
                now_ms=time.time_ns() // 1_000_000,
            )
        except ReviewDocumentConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "review_document_revision_conflict",
                    "expected_revision": exc.expected_revision,
                    "current_revision": exc.current_revision,
                    "current_document": exc.current_document,
                },
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"document": document}

    @app.get("/v2/meetings/{meeting_id}/documents/{document_kind}/revisions")
    def list_v2_review_document_revisions(
        meeting_id: str,
        document_kind: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        kind = _review_document_kind(document_kind)
        try:
            revisions = _require_v2_persistence().list_review_document_revisions(
                meeting_id,
                kind,
                limit=limit,
            )
        except KeyError:
            revisions = []
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"meeting_id": meeting_id, "document_kind": kind, "revisions": revisions}

    @app.post("/v2/meetings/{meeting_id}/documents/{document_kind}/regenerate")
    def regenerate_v2_review_document(meeting_id: str, document_kind: str) -> dict[str, Any]:
        kind = _review_document_kind(document_kind)
        job_kind = {
            "minutes": "minutes",
            "decisions": "minutes",
            "action_items": "minutes",
            "risks": "minutes",
            "transcript": "index",
        }[kind]
        try:
            result = _require_v2_persistence().enqueue_review_job(
                meeting_id=meeting_id,
                kind=job_kind,
                now_ms=time.time_ns() // 1_000_000,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        executor = getattr(app.state, "v2_executor", None)
        if executor is not None:
            executor.wake()
        return {
            "document_kind": kind,
            "preserve_user_final": True,
            "job": result["job"],
            "created": result["created"],
        }

    @app.post("/v2/meetings/{meeting_id}/jobs/{job_kind}/retry")
    def retry_v2_review_job(meeting_id: str, job_kind: str) -> dict[str, Any]:
        kind = str(job_kind or "").strip().lower()
        if kind not in {"minutes", "approach", "index"}:
            raise HTTPException(status_code=422, detail="unsupported review job kind")
        try:
            result = _require_v2_persistence().enqueue_review_job(
                meeting_id=meeting_id,
                kind=kind,
                now_ms=time.time_ns() // 1_000_000,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        executor = getattr(app.state, "v2_executor", None)
        if executor is not None:
            executor.wake()
        return {"job": result["job"], "created": result["created"]}

    @app.get("/v2/meetings/{meeting_id}/export")
    def export_v2_meeting(
        meeting_id: str,
        export_format: str = Query("markdown", alias="format"),
    ) -> Response:
        if not SESSION_ID_PATTERN.fullmatch(meeting_id):
            raise HTTPException(status_code=422, detail="meeting_id contains unsafe characters")
        normalized_format = str(export_format or "").strip().lower()
        formats = {
            "markdown": ("md", "text/markdown"),
            "docx": (
                "docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
            "json": ("json", "application/json"),
        }
        if normalized_format not in formats:
            detail = _v2_export_problem(
                "unsupported_export_format",
                "The requested meeting export format is not supported.",
                retryable=False,
            )
            detail["supported_formats"] = sorted(formats)
            raise HTTPException(status_code=422, detail=detail)
        persistence = _require_v2_persistence()
        try:
            payload = _v2_export_payload(persistence, meeting_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="meeting not found") from None
        except Exception as exc:
            _log.warning(
                "meeting.export.storage_failed",
                export_format=normalized_format,
                error_class=type(exc).__name__,
            )
            raise HTTPException(
                status_code=503,
                detail=_v2_export_problem(
                    "export_storage_unavailable",
                    "The local meeting data could not be read for export.",
                    retryable=True,
                ),
            ) from None
        extension, media_type = formats[normalized_format]
        try:
            if normalized_format == "markdown":
                content: str | bytes = _render_v2_export_markdown(payload)
            elif normalized_format == "docx":
                content = render_docx(payload)
            else:
                content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        except Exception as exc:
            _log.warning(
                "meeting.export.generation_failed",
                export_format=normalized_format,
                error_class=type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail=_v2_export_problem(
                    "export_document_generation_failed",
                    "The meeting document could not be generated.",
                    retryable=True,
                ),
            ) from None
        try:
            content_disposition = _v2_export_content_disposition(
                payload["meeting"],
                extension=extension,
            )
            return _v2_export_download_response(
                content,
                media_type=media_type,
                content_disposition=content_disposition,
                extension=extension,
            )
        except Exception as exc:
            _log.warning(
                "meeting.export.download_contract_failed",
                export_format=normalized_format,
                error_class=type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail=_v2_export_problem(
                    "export_download_contract_failed",
                    "The generated meeting document could not be prepared for download.",
                    retryable=True,
                ),
            ) from None

    @app.post("/v2/meetings/{meeting_id}/end")
    def end_v2_meeting(meeting_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("action") != "end_and_review":
            raise HTTPException(
                status_code=422,
                detail="action must be end_and_review",
            )
        persistence = _require_v2_persistence()
        try:
            live_record = asr_live_repo.get(meeting_id)
        except KeyError:
            live_record = None
        if live_record is not None and live_record.get("source") == "live_asr_stream":
            deadline = time.monotonic() + V2_END_ASR_FINALIZATION_TIMEOUT_S
            while True:
                if _live_asr_record_is_finalized(live_record):
                    break
                if "stream_interrupted" in set(live_record.get("degradation_reasons") or []):
                    break
                if time.monotonic() >= deadline:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "error": "asr_finalization_pending",
                            "message": "Realtime ASR is still finalizing; retry ending the meeting.",
                        },
                    )
                time.sleep(V2_END_ASR_FINALIZATION_POLL_S)
                live_record = asr_live_repo.get(meeting_id)
        try:
            meeting = persistence.end_meeting(
                meeting_id=meeting_id,
                now_ms=time.time_ns() // 1_000_000,
                correlation_id=meeting_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        jobs = persistence.list_jobs(meeting_id=meeting_id)
        executor = getattr(app.state, "v2_executor", None)
        if executor is not None:
            executor.wake()
        return {
            "meeting": meeting,
            "snapshot": _decorate_v2_snapshot(persistence.get_snapshot(meeting_id), meeting_id),
            "jobs": [job for job in jobs if job["kind"] in {"minutes", "approach", "index"}],
        }

    @app.put("/v2/meetings/{meeting_id}/suggestions/{suggestion_id}/feedback")
    def save_v2_suggestion_feedback(
        meeting_id: str,
        suggestion_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        try:
            suggestion = persistence.save_suggestion_feedback(
                meeting_id=meeting_id,
                suggestion_id=suggestion_id,
                feedback=str(payload.get("feedback") or ""),
                now_ms=time.time_ns() // 1_000_000,
                correlation_id=meeting_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"suggestion": suggestion}

    def _set_v2_entity_status(
        meeting_id: str,
        entity_id: str,
        status: str,
    ) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        if status == "ignore":
            status = "dismissed"
        try:
            entity = persistence.set_entity_status(
                meeting_id=meeting_id,
                entity_id=entity_id,
                status=status,
                now_ms=time.time_ns() // 1_000_000,
                correlation_id=meeting_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"entity": entity}

    @app.post("/v2/meetings/{meeting_id}/entities/{entity_id}/confirm")
    def confirm_v2_entity(meeting_id: str, entity_id: str) -> dict[str, Any]:
        return _set_v2_entity_status(meeting_id, entity_id, "confirmed")

    @app.post("/v2/meetings/{meeting_id}/entities/{entity_id}/dismiss")
    def dismiss_v2_entity(meeting_id: str, entity_id: str) -> dict[str, Any]:
        return _set_v2_entity_status(meeting_id, entity_id, "dismissed")

    @app.post("/v2/meetings/{meeting_id}/entities/{entity_id}/ignore")
    def ignore_v2_entity(meeting_id: str, entity_id: str) -> dict[str, Any]:
        return _set_v2_entity_status(meeting_id, entity_id, "dismissed")

    @app.patch("/v2/meetings/{meeting_id}/entities/{entity_id}")
    def update_v2_entity_status(
        meeting_id: str,
        entity_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return _set_v2_entity_status(
            meeting_id,
            entity_id,
            str(payload.get("status") or payload.get("action") or "").strip().lower(),
        )

    @app.delete("/v2/meetings/{meeting_id}")
    async def delete_v2_meeting(
        meeting_id: str,
        request: Request,
        scope: Literal["recording", "derived", "transcript", "all"] = Query(default="all"),
    ) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        if not persistence.meeting_exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}")
        governance = _require_data_governance()
        try:
            normalized_scope = normalize_deletion_scope(scope)
            meeting = persistence.get_meeting(meeting_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}") from exc
        if normalized_scope == "derived" and str(meeting.get("state") or "") == "live":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "active_ai_derivation",
                    "message": "结束会议后才能单独删除 AI 整理，避免实时任务再次写入。",
                },
            )
        cancelled_capture_count = 0
        if normalized_scope in {"recording", "transcript", "all"}:
            cancelled_capture_count = await _cancel_meeting_capture_tasks(meeting_id)
        request_key = str(request.headers.get("idempotency-key") or "").strip()
        if len(request_key) > 200 or any(ord(character) < 32 for character in request_key):
            raise HTTPException(status_code=422, detail="Idempotency-Key is invalid")
        if request_key:
            idempotency_key = f"api.delete:{normalized_scope}:{meeting_id}:{request_key}"
        elif normalized_scope == "all":
            idempotency_key = f"meeting.delete:all:{meeting_id}"
        else:
            idempotency_key = f"api.delete:{normalized_scope}:{meeting_id}:{uuid.uuid4().hex}"
        job: dict[str, Any] | None = None
        try:
            job = await asyncio.to_thread(
                governance.request_deletion,
                meeting_id=meeting_id,
                deletion_scope=normalized_scope,
                now_ms=time.time_ns() // 1_000_000,
                idempotency_key=idempotency_key,
            )
            completed = await asyncio.to_thread(
                governance.execute_deletion_job,
                job_id=str(job["id"]),
                now_ms=time.time_ns() // 1_000_000,
            )
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "meeting_deletion_failed",
                    "job_id": str(job["id"]) if job is not None else None,
                    "error_class": type(exc).__name__,
                },
            ) from exc
        return {
            "deleted": True,
            "deletion_scope": normalized_scope,
            "cancelled_capture_count": cancelled_capture_count,
            "meeting_deleted": normalized_scope == "all",
            "deletion_job": completed,
        }

    @app.get("/v2/meetings/{meeting_id}/events")
    async def get_v2_meeting_events(
        meeting_id: str,
        request: Request,
        after_seq: int = 0,
        limit: int = DEFAULT_EVENT_PAGE_LIMIT,
        once: bool = False,
    ) -> Any:
        if after_seq < 0:
            raise HTTPException(status_code=422, detail="after_seq must be non-negative")
        if not 1 <= limit <= MAX_EVENT_PAGE_LIMIT:
            raise HTTPException(
                status_code=422,
                detail=f"limit must be between 1 and {MAX_EVENT_PAGE_LIMIT}",
            )
        persistence = _require_v2_persistence()
        if "text/event-stream" in request.headers.get("accept", ""):

            async def stream_events():
                cursor = after_seq
                while True:
                    page = await asyncio.to_thread(
                        persistence.list_event_page,
                        meeting_id,
                        after_seq=cursor,
                        limit=limit,
                    )
                    events = [_decorate_v2_formal_event(event) for event in page["events"]]
                    if events:
                        for event in events:
                            cursor = max(cursor, int(event["seq"]))
                            encoded = json.dumps(
                                event,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                            yield (f"id: {event['seq']}\nevent: {event['type']}\ndata: {encoded}\n\n")
                    if not events:
                        yield f": heartbeat after_seq={cursor}\n\n"
                    if once or await request.is_disconnected():
                        return
                    if page["has_more"]:
                        continue
                    await asyncio.sleep(0.25)

            return StreamingResponse(
                stream_events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                },
            )
        try:
            page = await asyncio.to_thread(
                persistence.list_event_page,
                meeting_id,
                after_seq=after_seq,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            **page,
            "events": [_decorate_v2_formal_event(event) for event in page["events"]],
        }

    @app.get("/v2/meetings/{meeting_id}/traces")
    def get_v2_meeting_traces(meeting_id: str) -> dict[str, Any]:
        traces = [trace.to_dict() for trace in pipeline_traces.find(meeting_id=meeting_id)]
        return {"meeting_id": meeting_id, "traces": traces}

    @app.get("/v2/meetings/{meeting_id}/audio")
    def get_v2_meeting_audio(meeting_id: str) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        if not persistence.meeting_exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}")
        chunks = persistence.list_audio_chunks(meeting_id)
        recordings = persistence.list_recording_sessions(meeting_id)
        exports = persistence.list_recording_exports(meeting_id=meeting_id)
        derivations = persistence.list_recording_derivations(meeting_id)
        track_states = []
        for recording in recordings:
            track_chunks = [
                chunk
                for chunk in chunks
                if chunk["track_id"] == recording["track_id"] and int(chunk["epoch"]) == int(recording["epoch"])
            ]
            track_states.append(
                {
                    **recording,
                    "first_sequence": (min(int(chunk["sequence"]) for chunk in track_chunks) if track_chunks else None),
                    "last_sequence": (max(int(chunk["sequence"]) for chunk in track_chunks) if track_chunks else None),
                    "first_timestamp_ms": (
                        min(int(chunk["timestamp_ms"]) for chunk in track_chunks) if track_chunks else None
                    ),
                    "last_timestamp_ms": (
                        max(int(chunk["timestamp_ms"]) for chunk in track_chunks) if track_chunks else None
                    ),
                    "playback_url": (
                        f"/v2/meetings/{meeting_id}/audio/tracks/"
                        f"{recording['track_id']}/content?epoch={recording['epoch']}"
                        if recording["status"] == "ready"
                        else None
                    ),
                }
            )
        ready_recordings = sorted(
            (recording for recording in recordings if recording["status"] == "ready"),
            key=lambda recording: (
                recording["track_id"] != "microphone",
                -int(recording["epoch"]),
            ),
        )
        default_recording = ready_recordings[0] if ready_recordings else None
        relative_path = (
            str(default_recording["output_relative_path"])
            if default_recording is not None and default_recording.get("output_relative_path")
            else f"audio_assets/{meeting_id}/audio.wav"
        )
        path = audio_assets.safe_audio_path(data_dir_path, relative_path) if data_dir_path is not None else None
        recording_statuses = {str(recording["status"]) for recording in recordings}
        assembled_file = bool(path is not None and path.is_file() and not path.is_symlink())
        assembled = assembled_file and (not recordings or default_recording is not None)
        status = (
            "partial_failure"
            if "failed" in recording_statuses and recording_statuses != {"failed"}
            else "failed"
            if recording_statuses == {"failed"}
            else "recording"
            if "active" in recording_statuses
            else "assembling"
            if recording_statuses & {"sealed", "exporting", "interrupted"}
            else "saved"
            if assembled
            else "unknown"
        )
        return {
            "meeting_id": meeting_id,
            "status": status,
            "assembled": assembled,
            "playback_url": (f"/v2/meetings/{meeting_id}/audio/content" if assembled else None),
            "format": "wav" if assembled else None,
            "file_size_bytes": path.stat().st_size if assembled and path is not None else 0,
            "chunk_count": len(chunks),
            "duration_ms": max(
                [int(recording["duration_ms"]) for recording in recordings]
                or [sum(int(chunk["duration_ms"]) for chunk in chunks)]
            ),
            "tracks": sorted(
                {str(chunk["track_id"]) for chunk in chunks} | {str(recording["track_id"]) for recording in recordings}
            ),
            "track_states": track_states,
            "chunks": chunks,
            "recordings": recordings,
            "exports": exports,
            "derived_assets": derivations,
            "mixed_create_url": f"/v2/meetings/{meeting_id}/audio/mixed",
        }

    @app.get("/v2/meetings/{meeting_id}/audio/content")
    def get_v2_meeting_audio_content(meeting_id: str) -> FileResponse:
        persistence = _require_v2_persistence()
        if not persistence.meeting_exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}")
        if data_dir_path is None:
            raise HTTPException(status_code=404, detail="meeting audio is unavailable")
        recordings = persistence.list_recording_sessions(meeting_id)
        ready_recordings = sorted(
            (recording for recording in recordings if recording["status"] == "ready"),
            key=lambda recording: (
                recording["track_id"] != "microphone",
                -int(recording["epoch"]),
            ),
        )
        if recordings and not ready_recordings:
            raise HTTPException(status_code=409, detail="meeting audio is still being assembled")
        relative_path = (
            str(ready_recordings[0]["output_relative_path"])
            if ready_recordings and ready_recordings[0].get("output_relative_path")
            else f"audio_assets/{meeting_id}/audio.wav"
        )
        path = audio_assets.safe_audio_path(data_dir_path, relative_path)
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="meeting audio is not assembled yet")
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=f"{meeting_id}.wav",
            content_disposition_type="inline",
        )

    @app.get("/v2/meetings/{meeting_id}/audio/tracks/{track_id}/content")
    def get_v2_meeting_track_audio_content(
        meeting_id: str,
        track_id: Literal["microphone", "system_audio"],
        epoch: int = Query(default=0, ge=0),
    ) -> FileResponse:
        persistence = _require_v2_persistence()
        if data_dir_path is None:
            raise HTTPException(status_code=404, detail="meeting audio is unavailable")
        try:
            recording = persistence.get_recording_session(
                meeting_id,
                track=track_id,
                epoch=epoch,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if recording["status"] != "ready":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "audio_track_not_ready",
                    "track_id": track_id,
                    "epoch": epoch,
                    "status": recording["status"],
                    "error_class": recording["error_class"],
                },
            )
        path = audio_assets.safe_audio_path(
            data_dir_path,
            str(recording["output_relative_path"]),
        )
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="track audio asset is missing")
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=f"{meeting_id}-{track_id}-epoch-{epoch}.wav",
            content_disposition_type="inline",
        )

    @app.post("/v2/meetings/{meeting_id}/audio/mixed", status_code=201)
    def create_v2_meeting_mixed_audio(meeting_id: str) -> dict[str, Any]:
        persistence = _require_v2_persistence()
        if not persistence.meeting_exists(meeting_id):
            raise HTTPException(status_code=404, detail=f"meeting not found: {meeting_id}")
        if data_dir_path is None:
            raise HTTPException(status_code=503, detail="meeting audio storage is unavailable")
        recordings = persistence.list_recording_sessions(meeting_id)
        latest = {
            track_id: max(
                (recording for recording in recordings if recording["track_id"] == track_id),
                key=lambda recording: int(recording["epoch"]),
                default=None,
            )
            for track_id in ("microphone", "system_audio")
        }
        not_ready = {
            track_id: (
                {
                    "status": recording["status"],
                    "epoch": recording["epoch"],
                    "error_class": recording["error_class"],
                }
                if recording is not None
                else {"status": "missing", "epoch": None, "error_class": None}
            )
            for track_id, recording in latest.items()
            if recording is None or recording["status"] != "ready"
        }
        if not_ready:
            raise HTTPException(
                status_code=409,
                detail={"code": "audio_tracks_not_ready", "tracks": not_ready},
            )
        sources = [latest["microphone"], latest["system_audio"]]
        identity = persistence.mixed_recording_identity(meeting_id, sources)
        existing = next(
            (
                asset
                for asset in persistence.list_recording_derivations(meeting_id)
                if asset["asset_id"] == identity["asset_id"]
            ),
            None,
        )
        if existing is not None:
            existing_path = audio_assets.safe_audio_path(
                data_dir_path,
                str(existing["output_relative_path"]),
            )
            if (
                existing_path.is_file()
                and not existing_path.is_symlink()
                and existing_path.stat().st_size == int(existing["file_size_bytes"])
            ):
                current = audio_assets.audio_metadata_for_file(
                    data_dir=data_dir_path,
                    session_id=meeting_id,
                    relative_path=str(existing["output_relative_path"]),
                    source_type="local_derived_mix",
                    sample_rate_hz=None,
                    sample_count=None,
                )
                if current["sha256"] == existing["output_sha256"]:
                    return {"asset": existing}
        try:
            with _recording_asset_lock(meeting_id):
                output = audio_assets.derive_local_mixed_wav_asset(
                    data_dir=data_dir_path,
                    meeting_id=meeting_id,
                    asset_id=str(identity["asset_id"]),
                    sources=identity["sources"],
                )
                asset = persistence.register_mixed_recording_derivation(
                    meeting_id=meeting_id,
                    sources=identity["sources"],
                    output=output,
                    now_ms=time.time_ns() // 1_000_000,
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "mixed_audio_derivation_failed"},
            ) from exc
        return {"asset": asset}

    @app.get("/v2/meetings/{meeting_id}/audio/mixed/{asset_id}/content")
    def get_v2_meeting_mixed_audio_content(meeting_id: str, asset_id: str) -> FileResponse:
        persistence = _require_v2_persistence()
        if data_dir_path is None:
            raise HTTPException(status_code=404, detail="meeting audio is unavailable")
        try:
            asset = persistence.get_recording_derivation(meeting_id, asset_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if asset["status"] != "ready":
            raise HTTPException(status_code=409, detail="mixed audio is not ready")
        path = audio_assets.safe_audio_path(
            data_dir_path,
            str(asset["output_relative_path"]),
        )
        if not path.is_file() or path.is_symlink():
            raise HTTPException(status_code=404, detail="mixed audio asset is missing")
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=f"{meeting_id}-mixed.wav",
            content_disposition_type="inline",
        )

    @app.post("/v2/traces/{trace_id}/ui-rendered")
    def mark_v2_trace_ui_rendered(
        trace_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            trace = pipeline_traces.get(trace_id)
            trace.mark(
                "ui_rendered",
                attributes={
                    "event_seq": int(payload.get("event_seq") or 0),
                    "draft_seq": int(payload.get("draft_seq") or 0),
                },
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return trace.to_dict()

    @app.get("/workbench")
    def workbench() -> Response:
        return _workbench_v2_response()

    @app.get("/workbench-legacy")
    def workbench_legacy() -> Response:
        return Response(WORKBENCH_HTML, media_type="text/html; charset=utf-8")

    @app.get("/workbench-v2")
    def workbench_v2() -> Response:
        return _workbench_v2_response()

    def _workbench_v2_response() -> Response:
        index_path = FRONTEND_V2_DIST_DIR / "index.html"
        if not index_path.is_file():
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "frontend_v2_not_built",
                    "message": "Run the frontend_v2 production build before serving it",
                },
            )
        return FileResponse(index_path, media_type="text/html; charset=utf-8")

    @app.get("/metrics")
    def get_metrics() -> dict[str, Any]:
        return _metrics.metrics.snapshot()

    @app.get("/audio/check")
    def audio_check() -> dict[str, Any]:
        """Pre-meeting device check (G3): mic devices + ASR sidecars + LLM config."""
        devices: list[dict[str, Any]] = []
        mic_available = False
        mic_error: str | None = None
        try:
            import sounddevice as sd

            for i, d in enumerate(sd.query_devices()):
                if d.get("max_input_channels", 0) > 0:
                    devices.append({"index": i, "name": d.get("name"), "channels": d.get("max_input_channels")})
                    mic_available = True
        except Exception as exc:
            mic_error = str(exc)
            _log.warning("audio.check.mic_failed", error=str(exc))
        file_asr_available = batch_transcribe.is_available()
        realtime_asr_providers = _realtime_asr_providers()
        realtime_asr_available = bool(realtime_asr_providers)
        degradation = get_degradation_controller()
        recording_only = degradation.can_record_audio() and not degradation.can_run_asr()
        available = degradation.can_record_audio() and (recording_only or realtime_asr_available)
        return {
            "available": available,
            "recording_only": recording_only,
            "asr_available": realtime_asr_available and degradation.can_run_asr(),
            "mic_available": mic_available,
            "mic_devices": devices,
            "mic_error": mic_error,
            "file_asr_available": file_asr_available,
            "realtime_asr_available": realtime_asr_available,
            "realtime_asr_providers": realtime_asr_providers,
            "asr_readiness_summary": "realtime_ready"
            if realtime_asr_available
            else ("file_only" if file_asr_available else "unavailable"),
            "funasr_available": file_asr_available,
            "sherpa_available": "sherpa_onnx_realtime" in realtime_asr_providers,
            "llm_configured": bool(llm_service.LlmConfig.from_env()),
            "degradation": degradation.to_status_dict(),
        }

    @app.get("/v2/storage/preflight")
    def v2_storage_preflight(
        expected_duration_seconds: float = 3_600,
        track_count: int = 1,
    ) -> dict[str, Any]:
        if data_dir_path is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "managed_storage_unavailable"},
            )
        try:
            return preflight_meeting_storage(
                data_dir=data_dir_path,
                expected_duration_seconds=expected_duration_seconds,
                track_count=track_count,
            ).to_dict()
        except (UnsafeManagedPathError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/providers/status")
    def provider_status() -> dict[str, Any]:
        return provider_config_runtime.get_status().to_dict()

    def _web_provider_config_response(*, errors: list[str] | None = None) -> dict[str, Any]:
        config = llm_service.LlmConfig.from_env()
        runtime_status = provider_config_runtime.get_status(config).to_dict()
        response_errors = list(errors or [])
        configured_error = getattr(app.state, "web_provider_config_error", None)
        if configured_error:
            response_errors.append(configured_error)
        return {
            "command_status": "error" if response_errors else "ok",
            "configured": config is not None,
            "api_key_present": config is not None,
            "base_url": config.base_url if config is not None else None,
            "model": config.model if config is not None else None,
            "realtime_model": config.realtime_model if config is not None else None,
            "api_style": config.api_style if config is not None else None,
            "provider_label": (
                llm_service.provider_identifier(config)
                if config is not None
                else "openai_compatible_gateway"
            ),
            "runtime_synced": bool(runtime_status.get("runtime_synced")),
            "probe_status": runtime_status.get("probe_status", "not_run"),
            "errors": response_errors,
        }

    @app.get("/providers/config")
    def get_web_provider_config() -> dict[str, Any]:
        """Return Web-local provider metadata without ever returning the API key."""

        return _web_provider_config_response()

    @app.put("/providers/config")
    def put_web_provider_config(payload: WebProviderConfigRequest) -> dict[str, Any]:
        store = getattr(app.state, "web_provider_config_store", None)
        if store is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "web_provider_storage_unavailable",
                    "message": "当前运行实例没有可用的本地 AI 配置目录",
                },
            )
        current = llm_service.LlmConfig.from_env()
        try:
            stored = store.load()
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_provider_config", "message": "本地 AI 配置无效，请重新保存"},
            ) from exc
        supplied_key = payload.api_key.get_secret_value().strip() if payload.api_key is not None else ""
        api_key = supplied_key or (
            str(stored["api_key"]) if stored is not None else (current.api_key if current is not None else "")
        )
        if not api_key:
            raise HTTPException(
                status_code=422,
                detail={"error": "api_key_required", "message": "请输入 API Key"},
            )
        try:
            metadata = llm_service.configure_runtime(
                base_url=payload.base_url,
                api_key=api_key,
                model=payload.model,
                realtime_model=payload.realtime_model,
                provider_label=payload.provider_label,
                api_style=payload.api_style,
            )
            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise ValueError("provider runtime was not configured")
            store.save(
                {
                    "base_url": config.base_url,
                    "api_key": config.api_key,
                    "model": config.model,
                    "realtime_model": config.realtime_model,
                    "provider_label": config.provider_label,
                    "api_style": config.api_style,
                }
            )
            app.state.web_provider_config_error = None
        except (OSError, TypeError, ValueError) as exc:
            _log.warning(
                "provider.web_config.save_failed",
                error_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_provider_config", "message": "AI 配置保存失败，请检查地址、模型和密钥"},
            ) from None
        return {**_web_provider_config_response(), **metadata, "command_status": "ok"}

    @app.delete("/providers/config")
    def delete_web_provider_config() -> dict[str, Any]:
        store = getattr(app.state, "web_provider_config_store", None)
        if store is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "web_provider_storage_unavailable",
                    "message": "当前运行实例没有可用的本地 AI 配置目录",
                },
            )
        try:
            store.clear()
            llm_service.clear_runtime_config()
            app.state.web_provider_config_error = None
        except (OSError, ValueError):
            raise HTTPException(
                status_code=500,
                detail={"error": "provider_config_clear_failed", "message": "AI 配置移除失败"},
            ) from None
        return _web_provider_config_response()

    @app.get("/providers/health")
    def providers_health() -> dict[str, Any]:
        llm_config = llm_service.LlmConfig.from_env()
        llm_meta = llm_service.provider_metadata(llm_config)
        realtime_asr_providers = _realtime_asr_providers()
        file_asr_available = batch_transcribe.is_available()
        return {
            "schema_version": "provider_health.v1",
            "llm": {
                "configured": bool(llm_meta.get("configured_from_env")),
                "provider": str(llm_meta.get("provider") or "not_configured"),
                "model": str(llm_meta.get("model") or "not_called"),
                "realtime_model": str(llm_meta.get("realtime_model") or "not_called"),
                "api_style": str(llm_meta.get("api_style") or "not_configured"),
                "is_mock": bool(llm_meta.get("is_mock", False)),
                "credential_configured": llm_config is not None,
            },
            "asr": {
                "file_provider": "local_funasr_batch",
                "file_asr_available": file_asr_available,
                "realtime_providers": realtime_asr_providers,
                "realtime_asr_available": bool(realtime_asr_providers),
            },
            "remote_asr": {
                "default_enabled": False,
                "enabled": False,
                "providers": [],
                "adapter_contract": "optional_openai_compatible_or_vendor_adapter_disabled_by_default",
            },
            "cost_policy": {
                "default_paid_services": ["llm_gateway_when_ai_analysis_enabled"],
                "remote_asr_default_enabled": False,
                "raw_audio_uploaded_by_default": False,
            },
            "degradation": get_degradation_controller().to_status_dict(),
        }

    @app.get("/v2/diagnostics/bundle")
    def export_diagnostic_bundle() -> Response:
        try:
            provider_status = providers_health()
        except (OSError, RuntimeError, ValueError):
            realtime_asr_providers = _realtime_asr_providers()
            provider_status = {
                "llm": {
                    "configured": False,
                    "provider": "invalid_config",
                    "model": "not_called",
                    "api_style": "invalid_config",
                },
                "asr": {
                    "file_asr_available": batch_transcribe.is_available(),
                    "realtime_asr_available": bool(realtime_asr_providers),
                },
            }
        counters = _metrics.metrics.snapshot()

        def latency_metrics(prefix: str) -> dict[str, Any]:
            values: dict[str, Any] = {}
            count = counters.get(f"{prefix}_count")
            average = counters.get(f"{prefix}_avg_ms")
            maximum = counters.get(f"{prefix}_max_ms")
            if count is not None:
                values["observation_count"] = count
            if average is not None:
                values["avg_latency_ms"] = average
            if maximum is not None:
                values["max_latency_ms"] = maximum
            return values

        slo_stage_metrics: list[dict[str, Any]] = []
        for meeting_report in _current_realtime_slo_reports()["meetings"].values():
            for lane_name, lane_report in meeting_report["lanes"].items():
                metrics = lane_report["metrics"]
                slo_stage_metrics.append(
                    {
                        "stage": f"realtime_ai_{lane_name}",
                        "sample_count": lane_report["count"],
                        "missing_trace_count": lane_report["missing_trace_count"],
                        "cancelled_count": lane_report["cancelled_count"],
                        "retry_count": lane_report["retry_count"],
                        "final_to_first_token_p95_ms": metrics["final_to_first_token_ms"]["p95_ms"],
                        "final_to_event_p95_ms": metrics["final_to_event_emitted_ms"]["p95_ms"],
                        "queue_wait_p95_ms": metrics["queue_wait_ms"]["p95_ms"],
                        "provider_ttft_p95_ms": metrics["provider_ttft_ms"]["p95_ms"],
                        "provider_total_p95_ms": metrics["provider_total_ms"]["p95_ms"],
                        "event_to_ui_p95_ms": metrics["event_to_ui_ms"]["p95_ms"],
                    }
                )

        llm_status = dict(provider_status.get("llm") or {})
        asr_status = dict(provider_status.get("asr") or {})
        snapshot = {
            "version": {
                "app_version": app.version,
                "architecture": platform.machine(),
                "os": platform.system(),
            },
            "config_summary": {
                "app_mode": "desktop_local" if local_api_token else "local_web",
                "language": "zh-CN",
                "network_mode": "local_audio_remote_llm_only",
                "recording_enabled": True,
                "provider_mode": "openai_compatible" if llm_status.get("configured") else "not_configured",
            },
            "provider_capabilities": {
                "llm": {
                    "kind": "llm",
                    "provider": llm_status.get("provider"),
                    "model": llm_status.get("model"),
                    "protocol": llm_status.get("api_style"),
                    "configured": bool(llm_status.get("configured")),
                    "supports_streaming": bool(llm_status.get("configured")),
                },
                "realtime_asr": {
                    "kind": "realtime_asr",
                    "provider": "local_funasr",
                    "available": bool(asr_status.get("realtime_asr_available")),
                    "supports_realtime": bool(asr_status.get("realtime_asr_available")),
                    "mode": "local",
                },
                "file_asr": {
                    "kind": "file_asr",
                    "provider": "local_funasr_batch",
                    "available": bool(asr_status.get("file_asr_available")),
                    "supports_file_asr": bool(asr_status.get("file_asr_available")),
                    "mode": "local",
                },
            },
            "stage_metrics": [
                {"stage": "asr", **latency_metrics("asr_latency")},
                {"stage": "llm", **latency_metrics("llm_latency")},
                *slo_stage_metrics,
            ],
            "errors": [],
        }
        payload = diagnostic_bundle.build_diagnostic_bundle_bytes(snapshot)
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": ('attachment; filename="meeting-copilot-diagnostics.zip"'),
                "Cache-Control": "no-store",
            },
        )

    @app.get("/providers/asr/runtime")
    def asr_runtime_status() -> dict[str, Any]:
        return {
            "schema_version": "asr_runtime_status.v1",
            "realtime_available": asr_stream.funasr_realtime_available(),
            "resident_enabled": asr_stream._funasr_resident_enabled(),
            "resident": asr_stream.funasr_resident_status(),
        }

    @app.get("/degradation/status")
    def degradation_status() -> dict[str, Any]:
        return get_degradation_controller().to_status_dict()

    @app.post("/providers/llm/probe")
    def probe_llm_provider(request: Request) -> dict[str, Any]:
        verification_header = request.headers.get("x-meeting-copilot-verification")
        origin = request.headers.get("origin")
        origin_host = str(urlsplit(origin).hostname or "") if origin else ""

        is_local_origin = origin_host in {"127.0.0.1", "localhost", "::1", ""}
        if verification_header != "1" or not is_local_origin:
            raise HTTPException(
                status_code=403,
                detail={
                    "ok": False,
                    "error": "local_verification_required",
                    "message": "需要本地验证头",
                },
            )
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "ok": False,
                    "error": "llm_not_configured",
                    "message": "LLM未配置，请设置环境变量",
                },
            )
        if config.is_mock:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error": "mock_llm_not_accepted",
                    "message": "Mock LLM不被接受，请使用真实LLM配置",
                },
            )
        cache_key = (
            config.base_url,
            config.model,
            config.realtime_model,
            llm_service.provider_identifier(config),
            llm_service.runtime_config_generation(),
        )
        lane_lease = llm_lane_locks.try_acquire("provider_probe", "probe")
        if lane_lease is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "error": "llm_probe_in_flight",
                    "message": "LLM探测正在进行中，请稍后重试",
                },
            )
        provider_config_runtime.mark_probe_started(config)
        try:
            now = time.monotonic()
            _enforce_llm_budget("provider_probe", purpose="provider_probe", config=config)
            if (
                provider_probe_cache.get("key") == cache_key
                and float(provider_probe_cache.get("expires_at_monotonic") or 0.0) > now
                and isinstance(provider_probe_cache.get("result"), dict)
            ):
                cached_result = dict(provider_probe_cache["result"])
                cached_result["ok"] = True
                cached_result["cached"] = True
                provider_config_runtime.mark_probe_succeeded(config)
                return cached_result
            probe_config = llm_service.realtime_config(config)
            result = llm_service.probe_gateway(probe_config)
            _record_llm_usage(
                "provider_probe",
                purpose="provider_probe",
                config=probe_config,
                usage=dict(result.get("usage") or {}),
            )
            provider_probe_cache.update(
                {
                    "key": cache_key,
                    "expires_at_monotonic": now + 60.0,
                    "result": dict(result),
                }
            )
            provider_config_runtime.mark_probe_succeeded(config)
            return {
                "ok": True,
                **result,
            }
        except Exception as exc:
            provider_config_runtime.mark_probe_failed(config)
            if isinstance(exc, HTTPException):
                raise
            status_code = getattr(exc, "status_code", None)
            provider_code = getattr(exc, "provider_code", None)
            _log.warning(
                "llm.probe.failed",
                error_type=type(exc).__name__,
                status_code=status_code if isinstance(status_code, int) else None,
                provider_code=provider_code if isinstance(provider_code, str) else None,
            )
            raise HTTPException(
                status_code=502,
                detail={
                    "ok": False,
                    "error": "llm_probe_failed",
                    "error_type": type(exc).__name__,
                    "message": llm_service.provider_failure_message(exc),
                },
            ) from None
        finally:
            lane_lease.release()

    def _require_authenticated_desktop_runtime() -> None:
        if (
            not bool(app.state.local_api_auth.get("enabled"))
            or os.environ.get("MEETING_COPILOT_DESKTOP_RUNTIME") != "1"
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "desktop_runtime_required",
                    "message": "AI 配置只能由已认证的桌面客户端修改",
                },
            )

    @app.get("/desktop/provider/config")
    def get_desktop_provider_config() -> dict[str, Any]:
        _require_authenticated_desktop_runtime()
        config = llm_service.LlmConfig.from_env()
        runtime_status = provider_config_runtime.get_status(config).to_dict()
        return {
            **runtime_status,
            "runtime_override": llm_service.runtime_configured(),
            **llm_service.provider_metadata(config),
        }

    @app.put("/desktop/provider/config")
    def put_desktop_provider_config(
        payload: DesktopProviderConfigRequest,
    ) -> dict[str, Any]:
        _require_authenticated_desktop_runtime()
        try:
            metadata = llm_service.configure_runtime(
                base_url=payload.base_url,
                api_key=payload.api_key.get_secret_value(),
                model=payload.model,
                realtime_model=payload.realtime_model,
                provider_label=payload.provider_label,
                api_style=payload.api_style,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_provider_config", "message": str(exc)},
            ) from None
        runtime_status = provider_config_runtime.get_status().to_dict()
        return {
            **runtime_status,
            "runtime_override": True,
            **metadata,
        }

    @app.delete("/desktop/provider/config")
    def delete_desktop_provider_config() -> dict[str, Any]:
        _require_authenticated_desktop_runtime()
        llm_service.clear_runtime_config()
        config = llm_service.LlmConfig.from_env()
        runtime_status = provider_config_runtime.get_status(config).to_dict()
        return {
            **runtime_status,
            "runtime_override": False,
            **llm_service.provider_metadata(config),
        }

    def _require_next006_failpoints_enabled() -> None:
        _require_authenticated_desktop_runtime()
        if os.environ.get("MEETING_COPILOT_ENABLE_NEXT006_FAILPOINTS") != "1":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "next006_failpoints_disabled",
                    "message": "NEXT-006 test failpoints are disabled",
                },
            )

    @app.get("/desktop/test/failpoints/storage-write")
    def get_next006_storage_failpoint() -> dict[str, Any]:
        _require_next006_failpoints_enabled()
        return storage_write_failpoint.snapshot()

    @app.put("/desktop/test/failpoints/storage-write")
    def put_next006_storage_failpoint(
        payload: Next006StorageFailpointRequest,
    ) -> dict[str, Any]:
        _require_next006_failpoints_enabled()
        return storage_write_failpoint.arm(
            scope=payload.scope,
            failure=payload.failure,
            count=payload.count,
        )

    @app.delete("/desktop/test/failpoints/storage-write")
    def delete_next006_storage_failpoint() -> dict[str, Any]:
        _require_next006_failpoints_enabled()
        return storage_write_failpoint.reset()

    @app.post("/degradation/reset")
    def degradation_reset() -> dict[str, Any]:
        get_degradation_controller().reset()
        return get_degradation_controller().to_status_dict()

    @app.get("/settings/cost-stats")
    def get_cost_stats() -> dict[str, Any]:
        return _cost_stats()

    @app.get("/settings")
    def get_settings() -> dict[str, Any]:
        return _current_settings()

    @app.patch("/settings")
    def patch_settings(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            validated = SettingsPayload.model_validate(payload)
        except ValidationError:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_settings",
                    "message": "Settings payload does not match the strict non-sensitive schema",
                },
            ) from None
        settings = validated.model_dump(mode="json")
        settings_usage_repo.replace_settings(settings)
        return settings

    @app.post("/live/asr/sessions/{session_id}/minutes.json")
    def create_asr_live_session_minutes_json(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        """Post-meeting minutes as structured JSON (G5, for Jira/Linear/GitHub)."""
        return _asr_live_session_minutes_json_response(
            session_id,
            payload,
            allow_non_acceptance_execution=False,
            execution_boundary="production_acceptance_execution",
        )

    @app.post("/live/asr/demo/sessions/{session_id}/minutes.json")
    def create_demo_asr_live_session_minutes_json(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        """Demo-only structured minutes. Never counts as production acceptance."""
        return _asr_live_session_minutes_json_response(
            session_id,
            payload,
            allow_non_acceptance_execution=True,
            execution_boundary="demo_non_acceptance_execution",
        )

    @app.websocket("/live/asr/stream/ws/{session_id}")
    async def asr_stream_ws(websocket: WebSocket, session_id: str):
        if v2_persistence is not None and v2_persistence.meeting_exists(session_id):
            preparation = meeting_preparation_store.get(session_id) if meeting_preparation_store is not None else None
            if preparation is None or not preparation.notice_acknowledged:
                await websocket.accept()
                await websocket.send_text(
                    json.dumps(
                        {
                            "event_type": "provider_error",
                            "error_code": "recording_notice_required",
                            "message": "开始录音前需要确认录音告知。",
                            "recoverable": True,
                            "recording_saved": False,
                        },
                        ensure_ascii=False,
                    )
                )
                await websocket.close(
                    code=1008,
                    reason="recording notice acknowledgement required",
                )
                return
        route_task = asyncio.current_task()
        if route_task is not None:
            active_v2_capture_tasks.setdefault(session_id, set()).add(route_task)

            def unregister_capture_task(completed_task: asyncio.Task[Any]) -> None:
                tasks = active_v2_capture_tasks.get(session_id)
                if tasks is None:
                    return
                tasks.discard(completed_task)
                if not tasks:
                    active_v2_capture_tasks.pop(session_id, None)

            route_task.add_done_callback(unregister_capture_task)
        audio_source = str(websocket.query_params.get("audio_source") or "").strip() or None
        pcm_protocol = str(websocket.query_params.get("pcm_protocol") or "").strip() or None
        native_track_id = _transcript_source_track(audio_source)
        native_capture_epoch: int | None = None
        native_source = str(audio_source or "").strip().lower() in {
            "tauri_native_mic",
            "native_microphone_streaming",
            "tauri_system_audio",
            "macos_system_audio",
        }
        if native_source:
            try:
                native_capture_epoch = int(websocket.query_params.get("capture_epoch") or 0)
            except (TypeError, ValueError):
                native_capture_epoch = 0
            if (
                pcm_protocol != asr_stream.NATIVE_PCM_PROTOCOL_NAME
                or native_track_id not in {"microphone", "system_audio"}
                or native_capture_epoch <= 0
            ):
                await websocket.accept()
                await websocket.send_text(
                    json.dumps(
                        {
                            "event_type": "provider_error",
                            "error_code": "native_pcm_identity_invalid",
                            "message": "本地音频协议或采集批次无效，请重新启动客户端后开始会议。",
                            "recording_saved": False,
                            "recoverable": True,
                        },
                        ensure_ascii=False,
                    )
                )
                await websocket.close(code=1008, reason="native PCM identity required")
                return
        elif pcm_protocol is not None:
            await websocket.accept()
            await websocket.send_text(
                json.dumps(
                    {
                        "event_type": "provider_error",
                        "error_code": "native_pcm_source_invalid",
                        "message": "本地音频协议只能用于桌面原生音频轨道。",
                        "recording_saved": False,
                        "recoverable": True,
                    },
                    ensure_ascii=False,
                )
            )
            await websocket.close(code=1008, reason="native PCM source required")
            return
        if data_dir_path is not None:
            try:
                storage_preflight = preflight_meeting_storage(
                    data_dir=data_dir_path,
                    expected_duration_seconds=float(websocket.query_params.get("expected_duration_seconds") or 3_600),
                    track_count=1,
                )
            except (UnsafeManagedPathError, ValueError) as exc:
                await asr_stream.reject_recording_stream(
                    websocket,
                    degradation_level=3,
                    reason=f"storage_preflight_failed:{type(exc).__name__}",
                )
                return
            if not storage_preflight.allowed:
                await asr_stream.reject_recording_stream(
                    websocket,
                    degradation_level=3,
                    reason=storage_preflight.reason_code,
                )
                return
        l3_normalize_enabled = bool(_current_settings()["asr"]["l3_normalize_enabled"])
        degradation = get_degradation_controller()
        if not degradation.can_record_audio():
            await asr_stream.reject_recording_stream(
                websocket,
                degradation_level=degradation.level,
                reason=degradation.reason,
            )
            return
        capture_lease_owner = f"capture:{uuid.uuid4().hex}"
        capture_source_type = audio_source or "live_asr_stream"
        capture_started = False
        capture_recording: dict[str, Any] | None = None

        def begin_capture(metadata: dict[str, Any]) -> None:
            nonlocal capture_source_type, capture_started, capture_recording
            capture_source_type = str(metadata.get("source_type") or capture_source_type)
            capture_recording = _begin_v2_recording(
                session_id,
                metadata,
                lease_owner=capture_lease_owner,
            )
            capture_started = True

        def abort_capture_setup() -> None:
            nonlocal capture_started, capture_recording
            recording = capture_recording
            if v2_persistence is None or recording is None:
                capture_started = False
                capture_recording = None
                return
            v2_persistence.abort_recording_setup(
                meeting_id=session_id,
                track=str(recording["track"]),
                epoch=int(recording["epoch"]),
                lease_owner=capture_lease_owner,
                capture_generation=int(recording["capture_generation"]),
                now_ms=time.time_ns() // 1_000_000,
            )
            capture_started = False
            capture_recording = None

        async def heartbeat_capture() -> None:
            while True:
                await asyncio.sleep(recording_capture_lease_ms / 3 / 1_000)
                if not capture_started:
                    continue
                renewed = await asyncio.to_thread(
                    _heartbeat_v2_recording,
                    session_id,
                    source_type=capture_source_type,
                    lease_owner=capture_lease_owner,
                    track_id=(str(capture_recording.get("track")) if capture_recording else native_track_id),
                    epoch=(int(capture_recording.get("epoch") or 0) if capture_recording else int(native_capture_epoch or 0)),
                )
                if not renewed:
                    _log.error(
                        "meeting.recording.capture_lease_lost",
                        session_id=session_id,
                    )
                    try:
                        await websocket.close(
                            code=1011,
                            reason="recording capture lease lost",
                        )
                    except Exception:
                        pass
                    return

        heartbeat_task = asyncio.create_task(
            heartbeat_capture(),
            name=f"recording-capture-heartbeat-{session_id}",
        )
        try:
            common_recording_callbacks = {
                "on_audio_chunk_committed": lambda chunk: _record_v2_audio_chunk(
                    session_id,
                    chunk,
                    lease_owner=capture_lease_owner,
                ),
                "authorize_audio_chunk_commit": lambda chunk: _authorize_v2_audio_chunk(
                    session_id,
                    chunk,
                    lease_owner=capture_lease_owner,
                ),
                "on_audio_recording_started": begin_capture,
                "on_audio_recording_setup_failed": abort_capture_setup,
                "on_audio_recording_sealed": lambda metadata: _seal_v2_recording(
                    session_id,
                    metadata,
                    lease_owner=capture_lease_owner,
                ),
                "audio_asset_lock": _recording_asset_lock(session_id),
            }
            if not degradation.can_run_asr():
                await asr_stream.handle_recording_only_stream(
                    websocket,
                    session_id,
                    asr_live_repo=asr_live_repo,
                    audio_source=audio_source,
                    audio_asset_data_dir=data_dir_path,
                    degradation_reason="degradation_level_3_recording_only",
                    pcm_protocol=pcm_protocol,
                    native_track_id=native_track_id if native_source else None,
                    native_capture_epoch=native_capture_epoch,
                    **common_recording_callbacks,
                )
                return
            await asr_stream.handle_stream(
                websocket,
                session_id,
                asr_live_repo=asr_live_repo,
                allow_fake_fallback=allow_fake_asr_fallback,
                audio_source=audio_source,
                audio_asset_data_dir=data_dir_path,
                l3_normalize_enabled=l3_normalize_enabled,
                on_final_committed=lambda event: _commit_v2_final(
                    session_id,
                    {
                        **event,
                        "source_track": _transcript_source_track(audio_source),
                        "use_source_segment_namespace": _uses_source_segment_namespace(audio_source),
                    },
                ),
                on_audio_active=lambda observation: _record_audio_active(
                    session_id,
                    observation,
                ),
                diarization_persistence=v2_persistence,
                diarization_enabled=v2_persistence is not None,
                pcm_protocol=pcm_protocol,
                native_track_id=native_track_id if native_source else None,
                native_capture_epoch=native_capture_epoch,
                **common_recording_callbacks,
            )
        finally:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    @app.post("/live/asr/transcribe-file/sessions", status_code=201)
    async def transcribe_file_session(file: UploadFile = File(...)) -> dict[str, Any]:
        """Meeting recording file conversion: upload audio -> FunASR batch transcribe
        (more accurate, 0.86 recall) -> session -> cards/minutes."""
        if not batch_transcribe.is_available():
            raise HTTPException(status_code=422, detail="FunASR venv not found — file conversion unavailable")
        import tempfile

        suffix = Path(file.filename or "").suffix or ".wav"
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        import os as _os

        _os.close(fd)
        tmp = Path(tmp_path)
        session_id = "file_" + uuid.uuid4().hex[:12]
        audio_asset: dict[str, Any] | None = None
        try:
            uploaded_bytes = 0
            with tmp.open("wb") as uploaded_file:
                while chunk := await file.read(1024 * 1024):
                    uploaded_bytes += len(chunk)
                    if uploaded_bytes > 500 * 1024 * 1024:
                        raise HTTPException(
                            status_code=413,
                            detail="文件超过 500MB 限制，请缩短录音或分段导入",
                        )
                    uploaded_file.write(chunk)
                uploaded_file.flush()
                _os.fsync(uploaded_file.fileno())
            asr_report = batch_transcribe.transcribe_file_report(tmp)  # L1 high-quality offline ASR raw
            raw_text = str(asr_report.get("text") or "")
            if data_dir_path is not None:
                audio_asset = audio_assets.persist_uploaded_audio_asset_from_path(
                    data_dir=data_dir_path,
                    session_id=session_id,
                    source_type="uploaded_file",
                    filename=file.filename or "recording",
                    source_path=tmp,
                )
        except HTTPException:
            raise
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except subprocess.TimeoutExpired:
            raise HTTPException(
                status_code=408,
                detail="录音文件识别超时（超过180秒），请尝试较短的录音或分段导入",
            )
        finally:
            tmp.unlink(missing_ok=True)
        # Multi-level accuracy: L2 LLM correction (if configured) -> L3 normalizer
        llm_cfg = llm_service.LlmConfig.from_env()
        corrected_text = raw_text
        correction_degraded = False
        budget_error: HTTPException | None = None
        if llm_cfg is not None:
            try:
                _enforce_llm_budget(
                    session_id,
                    purpose="file_transcript_correction",
                    config=llm_cfg,
                )
            except HTTPException as exc:
                budget_error = exc
            else:
                corrected_text, usage, correction_degraded = asr_correct.correct_transcript(raw_text, llm_cfg)
                _record_llm_usage(
                    session_id,
                    purpose="file_transcript_correction",
                    config=llm_cfg,
                    usage=usage,
                )
        text = _normalize_text(corrected_text)
        post_meeting_asr_profile = _post_meeting_asr_profile(asr_report)
        live_events = build_asr_live_events(
            session_id=session_id,
            provider="local_funasr_batch",
            streaming_events=[
                {
                    "event_type": "final",
                    "segment_id": "file_seg_001",
                    "text": text,
                    "start_ms": 0,
                    "end_ms": 0,
                    "received_at_ms": 0,
                    "confidence": 0.9,
                }
            ],
            is_mock=False,
        )
        try:
            semantic_quality = (
                evaluate_semantic_quality(text)
                if text.strip()
                else {
                    "schema_version": "asr_semantic_quality.v1",
                    "policy_version": "general_chinese_technical_meeting.v3",
                    "status": "not_evaluated",
                    "blocker": None,
                    "matched_entities": [],
                    "matched_entity_groups": [],
                    "missing_entity_groups": [],
                    "technical_entity_hit_count": 0,
                    "technical_group_hit_count": 0,
                    "gibberish_score": 0.0,
                    "latin_token_count": 0,
                    "unknown_latin_token_count": 0,
                    "unknown_latin_tokens": [],
                    "mixed_language_fragmentation_score": 0.0,
                    "quality_failure_reasons": [],
                    "reason": "transcript_empty",
                }
            )
            degradation_reasons = []
            if semantic_quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER:
                degradation_reasons.append(ASR_SEMANTIC_QUALITY_BLOCKER)
            asr_live_repo.create(
                {
                    "session_id": session_id,
                    "provider": "local_funasr_batch",
                    "provider_mode": "real",
                    "is_mock": False,
                    "asr_fallback_used": False,
                    "degradation_reasons": degradation_reasons,
                    "asr_semantic_quality": semantic_quality,
                    "post_meeting_asr_profile": post_meeting_asr_profile,
                    "audio_source": "uploaded_file",
                    "input_source": "uploaded_file",
                    **({"audio": audio_asset} if audio_asset is not None else {}),
                    "source": ASR_LIVE_SOURCE,
                    "trace_kind": ASR_LIVE_TRACE_KIND,
                    "events": live_events,
                }
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _log.info("transcribe.file.session", session_id=session_id, chars=len(text), events=len(live_events))
        if budget_error is not None:
            detail = (
                dict(budget_error.detail)
                if isinstance(budget_error.detail, dict)
                else {
                    "error": "llm_budget_exceeded",
                    "message": str(budget_error.detail),
                }
            )
            detail["preservedSessionId"] = session_id
            detail["asrPreserved"] = True
            raise HTTPException(status_code=budget_error.status_code, detail=detail)
        return {
            "session_id": session_id,
            "provider": "local_funasr_batch",
            "transcript": text,
            "raw_transcript": raw_text,
            "corrected": corrected_text != raw_text,
            "correction_degraded": correction_degraded,
            "post_meeting_asr_profile": post_meeting_asr_profile,
            "event_count": len(live_events),
        }

    @app.post("/shadow-reports/feedback-ingestions")
    def create_shadow_report_feedback_ingestion(
        payload: ShadowReportFeedbackIngestionRequest,
    ) -> dict[str, Any]:
        feedback_tool = _load_shadow_report_feedback_ingestion_module()
        report = feedback_tool.build_shadow_report_feedback_ingestion(
            candidate_report=payload.candidate_report,
            candidate_report_path=payload.candidate_report_path,
            feedback_entries=payload.feedback_entries,
        )
        if str(report.get("feedback_ingestion_status", "")).startswith("blocked_"):
            raise HTTPException(status_code=422, detail=report)
        return report

    @app.get("/")
    def root_index() -> Response:
        # Redirect to V2 workbench by default
        return Response(
            status_code=302,
            headers={"Location": "/workbench"},
        )

    @app.get("/v1")
    def v1_workbench() -> Response:
        """Legacy V1 workbench (vanilla JS)"""
        return Response(WORKBENCH_HTML, media_type="text/html; charset=utf-8")

    @app.get("/demo/fixtures")
    def demo_fixtures() -> dict[str, Any]:
        return {"fixtures": list_demo_fixtures()}

    @app.post("/demo/fixtures/{fixture_id}/sessions", status_code=201)
    def create_demo_fixture_session(
        fixture_id: str,
        payload: CreateFixtureSessionRequest,
    ) -> dict[str, Any]:
        try:
            session_payload, metadata = session_payload_from_fixture(
                fixture_id,
                session_id=payload.session_id,
            )
            snapshot = repo.create(
                session_id=session_payload["session_id"],
                transcript_report=session_payload["transcript_report"],
                analysis=session_payload["analysis"],
                state_events=session_payload["state_events"],
                llm_usage=session_payload.get("llm_usage"),
                degradation_reasons=session_payload.get("degradation_reasons"),
                metadata={"expected_gap_rule_count": int(metadata["expected_gap_rule_count"])},
            )
            evaluation_summary = evaluate_demo_snapshot(
                snapshot,
                expected_gap_rule_count=int(metadata["expected_gap_rule_count"]),
                source=str(metadata["source"]),
            )
            replay_events = build_replay_events(
                snapshot,
                evaluation_summary=evaluation_summary,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        metadata.pop("expected_gap_rule_count", None)
        return {
            "metadata": metadata,
            "snapshot": snapshot,
            "evaluation_summary": evaluation_summary,
            "replay_events": replay_events,
        }

    @app.post("/live/mock/fixtures/{fixture_id}/sessions", status_code=201)
    def create_mock_live_fixture_session(
        fixture_id: str,
        payload: CreateFixtureSessionRequest,
    ) -> dict[str, Any]:
        try:
            session_payload, metadata = session_payload_from_fixture(
                fixture_id,
                session_id=payload.session_id,
            )
            snapshot = repo.create(
                session_id=session_payload["session_id"],
                transcript_report=session_payload["transcript_report"],
                analysis=session_payload["analysis"],
                state_events=session_payload["state_events"],
                llm_usage=session_payload.get("llm_usage"),
                degradation_reasons=session_payload.get("degradation_reasons"),
                metadata={
                    "expected_gap_rule_count": int(metadata["expected_gap_rule_count"]),
                    "live_mode": "mock_fixture_stream",
                },
            )
            evaluation_summary = evaluate_demo_snapshot(
                snapshot,
                expected_gap_rule_count=int(metadata["expected_gap_rule_count"]),
                source="live_mock_stream",
            )
            live_events = build_mock_live_events(
                snapshot,
                evaluation_summary=evaluation_summary,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        metadata.pop("expected_gap_rule_count", None)
        metadata["live_mode"] = "mock_fixture_stream"
        return {
            "metadata": metadata,
            "snapshot": snapshot,
            "evaluation_summary": evaluation_summary,
            "event_source": event_source_metadata(),
            "live_events": live_events,
        }

    @app.post("/live/asr/mock/sessions", status_code=201)
    def create_asr_live_mock_session(payload: CreateAsrLiveSessionRequest) -> dict[str, Any]:
        # Generate session_id if not provided
        session_id = payload.session_id or f"mock-{uuid.uuid4().hex[:12]}"

        # Load streaming_events from fixture if fixture_id provided
        streaming_events = payload.streaming_events
        if payload.fixture_id:
            fixture_file = REPO_ROOT / "configs" / "asr_providers" / f"mock-streaming-events.{payload.fixture_id}.json"
            if not fixture_file.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Fixture not found: {payload.fixture_id}. Available fixtures: release-review",
                )
            try:
                with open(fixture_file, "r", encoding="utf-8") as f:
                    streaming_events = json.load(f)
            except (IOError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=500, detail=f"Failed to load fixture: {exc}") from exc

        if not streaming_events:
            raise HTTPException(status_code=422, detail="Either 'streaming_events' or 'fixture_id' must be provided")

        try:
            live_events = build_asr_live_events(
                session_id=session_id,
                provider=payload.provider,
                streaming_events=streaming_events,
                is_mock=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = {
            "session_id": session_id,
            "provider": payload.provider,
            "is_mock": True,
            "provider_mode": "mock",
            "ingest_mode": "mock_asr_session",
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "events": live_events,
        }
        try:
            asr_live_repo.create(record)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "session_id": session_id,
            "event_source": _asr_live_event_source_metadata(record),
            "live_events": live_events,
        }

    @app.post("/live/asr/local-event-files/sessions", status_code=201)
    def create_asr_live_session_from_local_event_file(
        payload: CreateAsrLiveSessionFromEventFileRequest,
    ) -> dict[str, Any]:
        events_path = Path(payload.events_path)
        path_errors = _validate_local_asr_events_path(events_path)
        if path_errors:
            raise HTTPException(
                status_code=422,
                detail=_local_asr_event_file_error_detail(
                    ingest_status="blocked_by_path_validation",
                    events_path=events_path,
                    provider=payload.provider,
                    session_id=payload.session_id,
                    validation_errors=path_errors,
                ),
            )
        try:
            streaming_events = _load_local_asr_events(events_path)
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=_local_asr_event_file_error_detail(
                    ingest_status="blocked_by_invalid_events_file",
                    events_path=events_path,
                    provider=payload.provider,
                    session_id=payload.session_id,
                    validation_errors=[str(exc)],
                ),
            ) from exc
        event_contract_errors = _validate_local_asr_event_contract(streaming_events)
        if event_contract_errors:
            raise HTTPException(
                status_code=422,
                detail=_local_asr_event_file_error_detail(
                    ingest_status="blocked_by_event_contract",
                    events_path=events_path,
                    provider=payload.provider,
                    session_id=payload.session_id,
                    validation_errors=event_contract_errors,
                    input_event_counts=_local_asr_input_event_counts(streaming_events),
                ),
            )
        try:
            live_events = build_asr_live_events(
                session_id=payload.session_id,
                provider=payload.provider,
                streaming_events=streaming_events,
                is_mock=False,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail=_local_asr_event_file_error_detail(
                    ingest_status="blocked_by_event_contract",
                    events_path=events_path,
                    provider=payload.provider,
                    session_id=payload.session_id,
                    validation_errors=[str(exc)],
                    input_event_counts=_local_asr_input_event_counts(streaming_events),
                ),
            ) from exc

        display_path = _display_local_asr_events_path(events_path)
        record = {
            "session_id": payload.session_id,
            "provider": payload.provider,
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "ingest_mode": "local_asr_event_file",
            "events_path": display_path,
            "events": live_events,
        }
        try:
            asr_live_repo.create(record)
        except ValueError as exc:
            detail = str(exc)
            if detail.startswith("ASR live session already exists:"):
                raise HTTPException(
                    status_code=422,
                    detail=_local_asr_event_file_error_detail(
                        ingest_status="blocked_by_duplicate_session",
                        events_path=events_path,
                        provider=payload.provider,
                        session_id=payload.session_id,
                        validation_errors=[detail],
                        input_event_counts=_local_asr_input_event_counts(streaming_events),
                    ),
                ) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        live_event_counts = _local_asr_live_event_counts(live_events)
        return {
            "session_id": payload.session_id,
            "ingest_mode": "local_asr_event_file",
            "events_path": display_path,
            "event_source": _asr_live_event_source_metadata(record),
            "input_event_counts": _local_asr_input_event_counts(streaming_events),
            "live_event_counts": live_event_counts,
            "all_llm_statuses": _local_asr_llm_statuses(live_events),
            "formal_card_creation_status": "not_created"
            if live_event_counts.get("suggestion_card", 0) == 0
            else "unexpected_card_created",
            "live_events": live_events,
            **LOCAL_ASR_FILE_SAFETY_FLAGS,
        }

    @app.post("/sessions", status_code=201)
    def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
        try:
            return repo.create(
                session_id=payload.session_id,
                transcript_report=payload.transcript_report,
                analysis=payload.analysis,
                state_events=payload.state_events,
                llm_usage=payload.llm_usage,
                degradation_reasons=payload.degradation_reasons,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        try:
            return repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/events")
    def get_session_events(session_id: str) -> dict[str, Any]:
        try:
            snapshot = repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        evaluation_summary = _replay_evaluation(snapshot, repo.metadata(session_id))
        return {
            "session_id": session_id,
            "source": "replay_snapshot",
            "events": build_replay_events(snapshot, evaluation_summary=evaluation_summary),
        }

    @app.get("/sessions/{session_id}/events.sse")
    def get_session_events_sse(session_id: str) -> Response:
        try:
            snapshot = repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        evaluation_summary = _replay_evaluation(snapshot, repo.metadata(session_id))
        return Response(
            content=render_sse_events(build_replay_events(snapshot, evaluation_summary=evaluation_summary)),
            media_type="text/event-stream; charset=utf-8",
        )

    @app.get("/live/sessions/{session_id}/events")
    def get_live_session_events(session_id: str) -> dict[str, Any]:
        try:
            snapshot = repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        evaluation_summary = _live_evaluation(snapshot, repo.metadata(session_id))
        return {
            "session_id": session_id,
            "source": "live_mock_stream",
            "trace_kind": "live_event",
            "events": build_mock_live_events(snapshot, evaluation_summary=evaluation_summary),
        }

    @app.get("/live/sessions/{session_id}/events.sse")
    def get_live_session_events_sse(session_id: str) -> Response:
        try:
            snapshot = repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        evaluation_summary = _live_evaluation(snapshot, repo.metadata(session_id))
        return Response(
            content=render_live_sse_events(build_mock_live_events(snapshot, evaluation_summary=evaluation_summary)),
            media_type="text/event-stream; charset=utf-8",
        )

    @app.get("/live/asr/sessions")
    def list_asr_live_sessions(include_demo: bool = False) -> dict[str, Any]:
        try:
            records = asr_live_repo.list()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        records = [_migrate_asr_live_quality_record(record) for record in records]
        if not include_demo:
            records = [record for record in records if not _asr_live_session_hidden_from_default_history(record)]
        sessions = sorted(
            (_asr_live_session_summary(record) for record in records),
            key=lambda session: (
                int(session.get("last_activity_at_ms") or 0),
                int(session.get("created_at_ms") or 0),
                str(session.get("session_id") or ""),
            ),
            reverse=True,
        )
        return {"session_count": len(sessions), "sessions": sessions}

    @app.get("/live/asr/sessions/{session_id}/events")
    def get_asr_live_session_events(session_id: str) -> dict[str, Any]:
        try:
            record = _get_asr_live_record_for_derivation(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        summary = _asr_live_session_summary(record)
        event_source = _asr_live_event_source_metadata(record)
        derivation_projection = _asr_live_formal_derivation_projection(
            record,
            event_source,
        )
        audio = dict(record.get("audio") or {})
        # The V2 recording/export ledger is the durable source of truth. A
        # legacy live-session projection may lag briefly when export completes
        # on its worker, so expose the committed WAV metadata here as well.
        if v2_persistence is not None:
            try:
                exports = v2_persistence.list_recording_exports(meeting_id=session_id)
                completed_export = next(
                    (
                        export
                        for export in exports
                        if str(export.get("status") or "") == "succeeded" and isinstance(export.get("output"), dict)
                    ),
                    None,
                )
                if completed_export is not None:
                    audio = {**audio, **dict(completed_export["output"])}
            except (KeyError, ValueError):
                # The legacy session can exist before V2 recording setup. Keep
                # its projection intact rather than turning history into 5xx.
                pass
        return {
            "session_id": session_id,
            "provider": str(record.get("provider", "")),
            "event_source": event_source,
            "is_mock": bool(event_source.get("is_mock")),
            "provider_mode": str(record.get("provider_mode") or ("mock" if event_source.get("is_mock") else "real")),
            "asr_fallback_used": bool(record.get("asr_fallback_used", False)),
            "degradation_reasons": list(event_source.get("degradation_reasons") or []),
            "final_count": summary["final_count"],
            "non_empty_final_count": event_source["non_empty_final_count"],
            "non_empty_transcript": event_source["non_empty_transcript"],
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "events": record["events"],
            "canonical_transcript": project_canonical_transcript(
                session_id=session_id,
                events=list(record.get("events") or []),
            ),
            **derivation_projection,
            "auto_suggestion": auto_suggestion_orchestrator.status_from_record(record),
            "realtime_transcript_correction": dict(record.get("realtime_transcript_correction") or {}),
            "settings_snapshot": dict(record.get("settings_snapshot") or {}),
            "llm_evidence": _llm_session_evidence(session_id),
            "audio": audio,
        }

    @app.get("/live/asr/sessions/{session_id}/events.sse")
    def get_asr_live_session_events_sse(session_id: str) -> Response:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=render_asr_sse_events(record["events"]),
            media_type="text/event-stream; charset=utf-8",
        )

    @app.get("/live/asr/sessions/{session_id}/suggestion-candidates")
    def get_asr_live_session_suggestion_candidates(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        candidates = _candidate_events_from_record(record)
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "candidate_count": len(candidates),
            "candidates": candidates,
        }

    @app.get("/live/asr/sessions/{session_id}/llm-request-drafts")
    def get_asr_live_session_llm_request_drafts(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        request_drafts = _request_draft_events_from_record(record)
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "request_draft_count": len(request_drafts),
            "request_drafts": request_drafts,
        }

    @app.get("/live/asr/sessions/{session_id}/llm-execution-previews")
    def get_asr_live_session_llm_execution_previews(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        execution_previews = _execution_previews_from_record(record)
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "execution_preview_count": len(execution_previews),
            "execution_previews": execution_previews,
        }

    @app.get("/live/asr/sessions/{session_id}/auto-suggestions/status")
    def get_asr_live_session_auto_suggestion_status(session_id: str) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        _overlay, status = auto_suggestion_orchestrator.apply_runtime_policy(
            record,
            _current_auto_suggestion_runtime_policy(),
        )
        return {
            "session_id": session_id,
            "status": status,
        }

    @app.patch("/live/asr/sessions/{session_id}/auto-suggestions/status")
    def patch_asr_live_session_auto_suggestion_status(
        session_id: str,
        payload: PatchAutoSuggestionStatusRequest,
    ) -> dict[str, Any]:
        _get_asr_live_record_for_derivation(session_id)

        def patch_latest(latest: dict[str, Any]) -> dict[str, Any]:
            updated, _status = auto_suggestion_orchestrator.patch_status(
                latest,
                paused=payload.paused,
            )
            return updated

        updated = asr_live_repo.update(session_id, patch_latest)
        status = auto_suggestion_orchestrator.status_from_record(updated)
        return {
            "session_id": session_id,
            "status": status,
        }

    @app.post("/live/asr/sessions/{session_id}/auto-suggestions/run-once")
    def run_asr_live_session_auto_suggestions_once(session_id: str) -> dict[str, Any]:
        lane_lease = llm_lane_locks.try_acquire(session_id, "suggestion")
        if lane_lease is None:
            record = _get_asr_live_record_for_derivation(session_id)
            return {
                "session_id": session_id,
                "status": auto_suggestion_orchestrator.status_from_record(record),
                "generated_card_count": 0,
                "suppressed_count": 0,
                "runs": [],
                "suggestion_cards": list(record.get("suggestion_cards") or []),
                "transcript_revisions": [],
                "reason": "in_flight",
            }
        try:
            _get_asr_live_record_for_derivation(session_id)
            runtime_policy = _current_auto_suggestion_runtime_policy()
            correction_enabled = bool(_current_settings()["asr"]["l2_correction_enabled"])
            record, _status = _persist_auto_suggestion_runtime_policy(
                session_id,
                runtime_policy,
            )
            if not runtime_policy["enabled"]:

                def suppress_disabled(latest: dict[str, Any]) -> dict[str, Any]:
                    updated, _status = auto_suggestion_orchestrator.suppress(
                        latest,
                        reason="disabled_by_setting",
                        now_ms=int(time.time() * 1_000),
                    )
                    return updated

                updated = asr_live_repo.update(session_id, suppress_disabled)
                status = auto_suggestion_orchestrator.status_from_record(updated)
                return {
                    "session_id": session_id,
                    "status": status,
                    "generated_card_count": 0,
                    "suppressed_count": len(status.get("suppressed") or []),
                    "runs": [],
                    "suggestion_cards": list(updated.get("suggestion_cards") or []),
                    "transcript_revisions": [],
                    "reason": "disabled_by_setting",
                }
            try:
                _enforce_llm_budget(
                    session_id,
                    purpose="auto_suggestion",
                    config=None,
                )
            except HTTPException:

                def suppress_degradation(latest: dict[str, Any]) -> dict[str, Any]:
                    updated, _status = auto_suggestion_orchestrator.suppress(
                        latest,
                        reason="disabled_by_degradation",
                        now_ms=int(time.time() * 1_000),
                    )
                    return updated

                asr_live_repo.update(session_id, suppress_degradation)
                raise
            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "llm_provider_not_configured",
                        "purpose": "auto_suggestion",
                    },
                )
            config = llm_service.realtime_config(config)
            _ensure_llm_provider_allowed_for_derivation(
                config,
                allow_non_acceptance_execution=False,
            )
            try:
                _enforce_llm_budget(
                    session_id,
                    purpose="auto_suggestion",
                    config=config,
                )
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                if detail.get("error") == "llm_budget_exceeded":

                    def suppress_budget(latest: dict[str, Any]) -> dict[str, Any]:
                        updated, _status = auto_suggestion_orchestrator.suppress(
                            latest,
                            reason="budget_blocked",
                            now_ms=int(time.time() * 1_000),
                        )
                        return updated

                    asr_live_repo.update(session_id, suppress_budget)
                raise
            claim_id = f"auto_claim_{uuid.uuid4().hex}"
            claimed: dict[str, Any] = {}

            def claim_latest(latest: dict[str, Any]) -> dict[str, Any]:
                claimed_record, _claimed_status, claim = auto_suggestion_orchestrator.claim_candidate(
                    latest,
                    previews=_execution_previews_from_record(latest),
                    config=config,
                    acceptance_blockers=_enabled_llm_execution_blockers(latest),
                    claim_id=claim_id,
                    now_ms=int(time.time() * 1_000),
                )
                if claim is not None:
                    claimed.update(claim)
                return claimed_record

            record = asr_live_repo.update(session_id, claim_latest)
            if not claimed:
                status = auto_suggestion_orchestrator.status_from_record(record)
                suppression_reason = str(status.get("last_suppression_reason") or "")
                return {
                    "session_id": session_id,
                    "status": status,
                    "generated_card_count": 0,
                    "suppressed_count": len(status.get("suppressed") or []),
                    "runs": [],
                    "suggestion_cards": list(record.get("suggestion_cards") or []),
                    "transcript_revisions": [],
                    "reason": (
                        "in_flight" if status.get("status") == "in_flight" else suppression_reason or "no_candidate"
                    ),
                }
            computed, _computed_status, runs = auto_suggestion_orchestrator.run_once(
                record,
                previews=[dict(claimed["preview"])],
                config=config,
                acceptance_blockers=_enabled_llm_execution_blockers(record),
                correction_enabled=correction_enabled,
                claimed_candidate_id=str(claimed["candidate_id"]),
                claim_id=claim_id,
            )
            for run in runs:
                _record_llm_usage(
                    session_id,
                    purpose="auto_suggestion",
                    config=config,
                    usage=run.get("llm_usage"),
                )
            transcript_revisions = [
                dict(run["transcript_revision"]) for run in runs if isinstance(run.get("transcript_revision"), dict)
            ]

            def merge_latest(latest: dict[str, Any]) -> dict[str, Any]:
                candidate_id = str(claimed["candidate_id"])
                latest_status = auto_suggestion_orchestrator.status_from_record(latest)
                latest_reservation = dict((latest_status.get("candidate_reservations") or {}).get(candidate_id) or {})
                if (
                    str(latest_reservation.get("claim_id") or "") != claim_id
                    or latest_reservation.get("status") != "reserved"
                ):
                    return latest
                latest_cards = list(latest.get("suggestion_cards") or [])
                latest_card_ids = {str(card.get("card_id") or "") for card in latest_cards}
                for card in list(computed.get("suggestion_cards") or []):
                    card_id = str(card.get("card_id") or "")
                    if card_id and card_id not in latest_card_ids:
                        latest_cards.append(dict(card))
                        latest_card_ids.add(card_id)
                computed_status = auto_suggestion_orchestrator.status_from_record(computed)
                merged_status = {
                    **latest_status,
                    **computed_status,
                    "processed_candidate_ids": auto_suggestion_orchestrator.order_preserving_union(
                        list(latest_status.get("processed_candidate_ids") or []),
                        list(computed_status.get("processed_candidate_ids") or []),
                    ),
                    "call_timestamps_ms": sorted(
                        set(latest_status.get("call_timestamps_ms") or []).union(
                            computed_status.get("call_timestamps_ms") or []
                        )
                    ),
                    "suppressed": [
                        *list(latest_status.get("suppressed") or []),
                        *[
                            item
                            for item in list(computed_status.get("suppressed") or [])
                            if item not in list(latest_status.get("suppressed") or [])
                        ],
                    ],
                    "terminal_failed_candidate_ids": auto_suggestion_orchestrator.order_preserving_union(
                        list(latest_status.get("terminal_failed_candidate_ids") or []),
                        list(computed_status.get("terminal_failed_candidate_ids") or []),
                    ),
                    "candidate_attempt_counts": {
                        **dict(latest_status.get("candidate_attempt_counts") or {}),
                        **dict(computed_status.get("candidate_attempt_counts") or {}),
                    },
                    "candidate_reservations": {
                        **dict(latest_status.get("candidate_reservations") or {}),
                        candidate_id: dict(
                            (computed_status.get("candidate_reservations") or {}).get(candidate_id) or {}
                        ),
                    },
                }
                merged_status["paused"] = bool(latest_status.get("paused"))
                if merged_status["paused"]:
                    merged_status["status"] = "paused"
                merged = {
                    **latest,
                    "suggestion_cards": latest_cards,
                    "auto_suggestion": auto_suggestion_orchestrator.persistable_status(merged_status),
                }
                computed_correction_status = dict(computed.get("realtime_transcript_correction") or {})
                if transcript_revisions or computed_correction_status:
                    latest_correction_status = dict(latest.get("realtime_transcript_correction") or {})
                    merged_correction_status = {
                        **latest_correction_status,
                        **computed_correction_status,
                    }
                    for field in (
                        "processed_segment_ids",
                        "revised_segment_ids",
                        "combined_attempted_segment_ids",
                        "combined_no_revision_needed_segment_ids",
                        "combined_rejected_segment_ids",
                    ):
                        merged_correction_status[field] = sorted(
                            set(latest_correction_status.get(field) or []).union(
                                computed_correction_status.get(field) or []
                            )
                        )
                    merged = realtime_transcript_correction.apply_revision_events(
                        merged,
                        transcript_revisions,
                        status=merged_correction_status,
                    )
                return merged

            updated = asr_live_repo.update(session_id, merge_latest)
            status = auto_suggestion_orchestrator.status_from_record(updated)
            cards = [
                dict(run["card"])
                for run in runs
                if run.get("run_status") == "completed" and isinstance(run.get("card"), dict)
            ]
            _metrics.metrics.inc("llm_calls", len(runs))
            _metrics.metrics.inc("cards_created", len(cards))
            return {
                "session_id": session_id,
                "status": status,
                "generated_card_count": len(cards),
                "suppressed_count": len(status.get("suppressed") or []),
                "runs": runs,
                "suggestion_cards": list(updated.get("suggestion_cards") or []),
                "transcript_revisions": transcript_revisions,
            }
        finally:
            lane_lease.release()

    @app.post("/live/asr/sessions/{session_id}/realtime-corrections/run-once")
    def run_asr_live_session_realtime_corrections_once(
        session_id: str,
        payload: RunRealtimeCorrectionsRequest,
    ) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        if not _current_settings()["asr"]["l2_correction_enabled"]:
            return {
                "session_id": session_id,
                "called": False,
                "gate": {
                    "eligible": False,
                    "reason": "disabled_by_setting",
                    "policy_version": realtime_transcript_correction.POLICY_VERSION,
                },
                "status": dict(record.get("realtime_transcript_correction") or {}),
                "revision_count": 0,
                "transcript_revisions": [],
                "no_revision_segment_ids": [],
            }
        lane_lease = llm_lane_locks.try_acquire(session_id, "correction")
        if lane_lease is None:
            record = _get_asr_live_record_for_derivation(session_id)
            return {
                "session_id": session_id,
                "called": False,
                "gate": {
                    "eligible": False,
                    "reason": "in_flight",
                    "policy_version": realtime_transcript_correction.POLICY_VERSION,
                },
                "status": dict(record.get("realtime_transcript_correction") or {}),
                "revision_count": 0,
                "transcript_revisions": [],
                "no_revision_segment_ids": [],
            }
        try:
            record = _get_asr_live_record_for_derivation(session_id)
            blockers = _realtime_correction_blockers(record)
            if blockers:
                raise HTTPException(
                    status_code=409,
                    detail=f"ASR live session {session_id} is not eligible for realtime correction; blockers: {', '.join(blockers)}",
                )
            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise HTTPException(
                    status_code=422,
                    detail="Realtime correction requires LLM_GATEWAY_BASE_URL / LLM_GATEWAY_API_KEY",
                )
            config = llm_service.realtime_config(config)
            _ensure_llm_provider_allowed_for_derivation(config, allow_non_acceptance_execution=False)
            audit_metadata = llm_service.provider_audit_metadata(
                config,
                purpose="realtime_transcript_correction",
            )
            _enforce_llm_budget(
                session_id,
                purpose="realtime_transcript_correction",
                config=config,
            )
            now_wall_clock_ms = int(time.time() * 1_000)
            reservation_decision = realtime_transcript_correction.reservation_action(
                record,
                now_ms=now_wall_clock_ms,
            )
            reservation = dict(reservation_decision.get("reservation") or {})
            reservation_action = str(reservation_decision.get("action") or "none")
            if reservation_action == "in_flight":
                return {
                    "session_id": session_id,
                    "called": False,
                    "gate": {
                        "eligible": False,
                        "reason": "reservation_in_flight",
                        "segment_ids": list(reservation.get("segment_ids") or []),
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                    },
                    "status": dict(record.get("realtime_transcript_correction") or {}),
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }
            if reservation_action == "expired_terminal":
                expired_batch_id = str(reservation.get("batch_id") or "")
                expired_segment_ids = list(reservation.get("segment_ids") or [])

                def commit_expired_terminal(latest: dict[str, Any]) -> dict[str, Any]:
                    existing_status = dict(latest.get("realtime_transcript_correction") or {})
                    terminal = realtime_transcript_correction.apply_revision_events(
                        latest,
                        [],
                        status={
                            "status": "reservation_expired_terminal",
                            "processed_segment_ids": sorted(
                                set(existing_status.get("processed_segment_ids") or []).union(expired_segment_ids)
                            ),
                            "terminal_failed_segment_ids": sorted(
                                set(existing_status.get("terminal_failed_segment_ids") or []).union(expired_segment_ids)
                            ),
                        },
                    )
                    return realtime_transcript_correction.commit_batch_audit(
                        terminal,
                        batch_id=expired_batch_id,
                        status="expired_terminal",
                        completed_at_ms=now_wall_clock_ms,
                        usage={},
                        **audit_metadata,
                        degraded=True,
                        fallback=True,
                        retry=max(0, int(reservation.get("retry_count") or 0)) > 0,
                    )

                updated = asr_live_repo.update(session_id, commit_expired_terminal)
                return {
                    "session_id": session_id,
                    "called": False,
                    "gate": {
                        "eligible": False,
                        "reason": "reservation_expired_terminal",
                        "segment_ids": expired_segment_ids,
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                    },
                    "status": dict(updated.get("realtime_transcript_correction") or {}),
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }

            if reservation_action == "retry":
                batch_id = str(reservation.get("batch_id") or "")
                segment_ids = list(reservation.get("segment_ids") or [])
                final_events = realtime_transcript_correction.final_events_for_segment_ids(record, segment_ids)
                gate = {
                    "eligible": True,
                    "reason": "reservation_retry",
                    "final_events": final_events,
                    "segment_ids": segment_ids,
                    "total_chars": sum(
                        len(
                            str(
                                (event.get("payload") or {}).get("normalized_text")
                                or (event.get("payload") or {}).get("text")
                                or ""
                            )
                        )
                        for event in final_events
                    ),
                    "elapsed_ms": 0,
                    "retry_after_ms": 0,
                    "oversized_segment_ids": [],
                    "policy_version": realtime_transcript_correction.POLICY_VERSION,
                }
                retry_count = max(0, int(reservation.get("retry_count") or 0)) + 1
            else:
                gate = realtime_transcript_correction.eligible_final_batch(
                    record,
                    force=payload.force,
                    now_ms=now_wall_clock_ms,
                )
                batch_id = f"rtc_batch_{uuid.uuid4().hex}"
                retry_count = 0
                segment_ids = list(gate.get("segment_ids") or [])
                final_events = list(gate.get("final_events") or [])

            if not gate["eligible"]:
                current_record = record
                if gate.get("reason") == "batch_gate_closed" and gate.get("segment_ids"):
                    current_record = asr_live_repo.update(
                        session_id,
                        lambda latest: realtime_transcript_correction.apply_revision_events(
                            latest,
                            [],
                            status={
                                "status": "waiting",
                                "waiting_since_epoch_ms": int(
                                    (latest.get("realtime_transcript_correction") or {}).get("waiting_since_epoch_ms")
                                    or now_wall_clock_ms
                                ),
                                "skipped_segment_ids": sorted(
                                    set(
                                        (latest.get("realtime_transcript_correction") or {}).get("skipped_segment_ids")
                                        or []
                                    ).union(gate.get("oversized_segment_ids") or [])
                                ),
                            },
                        ),
                    )
                    gate = realtime_transcript_correction.eligible_final_batch(
                        current_record,
                        force=payload.force,
                        now_ms=now_wall_clock_ms,
                    )
                elif gate.get("oversized_segment_ids"):
                    current_record = asr_live_repo.update(
                        session_id,
                        lambda latest: realtime_transcript_correction.apply_revision_events(
                            latest,
                            [],
                            status={
                                "status": "oversized_skipped",
                                "skipped_segment_ids": sorted(
                                    set(
                                        (latest.get("realtime_transcript_correction") or {}).get("skipped_segment_ids")
                                        or []
                                    ).union(gate.get("oversized_segment_ids") or [])
                                ),
                            },
                        ),
                    )
                return {
                    "session_id": session_id,
                    "called": False,
                    "gate": gate,
                    "status": dict(
                        current_record.get("realtime_transcript_correction")
                        or {"policy_version": realtime_transcript_correction.POLICY_VERSION, "status": "waiting"}
                    ),
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }

            if not final_events or len(final_events) != len(segment_ids):
                raise HTTPException(
                    status_code=409,
                    detail="Realtime correction reservation no longer matches persisted final segments",
                )
            claim_result: dict[str, Any] = {}

            def claim_latest_reservation(latest: dict[str, Any]) -> dict[str, Any]:
                latest_decision = realtime_transcript_correction.reservation_action(
                    latest,
                    now_ms=now_wall_clock_ms,
                )
                latest_action = str(latest_decision.get("action") or "none")
                latest_reservation = dict(latest_decision.get("reservation") or {})
                if reservation_action == "retry":
                    if latest_action != "retry" or str(latest_reservation.get("batch_id") or "") != batch_id:
                        claim_result.update(
                            {
                                "claimed": False,
                                "reason": "reservation_in_flight"
                                if latest_action == "in_flight"
                                else "reservation_changed",
                                "reservation": latest_reservation,
                            }
                        )
                        return latest
                    claimed_segment_ids = list(latest_reservation.get("segment_ids") or [])
                    claimed_final_events = realtime_transcript_correction.final_events_for_segment_ids(
                        latest,
                        claimed_segment_ids,
                    )
                    claimed_retry_count = max(0, int(latest_reservation.get("retry_count") or 0)) + 1
                    claimed_gate = {
                        "eligible": True,
                        "reason": "reservation_retry",
                        "final_events": claimed_final_events,
                        "segment_ids": claimed_segment_ids,
                        "total_chars": sum(
                            len(
                                str(
                                    (event.get("payload") or {}).get("normalized_text")
                                    or (event.get("payload") or {}).get("text")
                                    or ""
                                )
                            )
                            for event in claimed_final_events
                        ),
                        "elapsed_ms": 0,
                        "retry_after_ms": 0,
                        "oversized_segment_ids": [],
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                    }
                else:
                    if latest_action in {"in_flight", "retry", "expired_terminal"}:
                        claim_result.update(
                            {
                                "claimed": False,
                                "reason": "reservation_in_flight"
                                if latest_action == "in_flight"
                                else "reservation_changed",
                                "reservation": latest_reservation,
                            }
                        )
                        return latest
                    claimed_gate = realtime_transcript_correction.eligible_final_batch(
                        latest,
                        force=payload.force,
                        now_ms=now_wall_clock_ms,
                    )
                    if not claimed_gate.get("eligible"):
                        claim_result.update(
                            {
                                "claimed": False,
                                "reason": str(claimed_gate.get("reason") or "no_unrevised_final"),
                                "gate": claimed_gate,
                            }
                        )
                        return latest
                    claimed_segment_ids = list(claimed_gate.get("segment_ids") or [])
                    claimed_final_events = list(claimed_gate.get("final_events") or [])
                    failure_counts = {
                        str(segment_id): max(0, int(count or 0))
                        for segment_id, count in dict(
                            (latest.get("realtime_transcript_correction") or {}).get("failure_counts") or {}
                        ).items()
                    }
                    claimed_retry_count = max(
                        (failure_counts.get(segment_id, 0) for segment_id in claimed_segment_ids),
                        default=0,
                    )
                if not claimed_final_events or len(claimed_final_events) != len(claimed_segment_ids):
                    claim_result.update(
                        {
                            "claimed": False,
                            "reason": "reservation_segments_changed",
                        }
                    )
                    return latest
                claim_result.update(
                    {
                        "claimed": True,
                        "gate": claimed_gate,
                        "segment_ids": claimed_segment_ids,
                        "final_events": claimed_final_events,
                        "retry_count": claimed_retry_count,
                    }
                )
                return realtime_transcript_correction.begin_reservation(
                    latest,
                    batch_id=batch_id,
                    segment_ids=claimed_segment_ids,
                    now_ms=now_wall_clock_ms,
                    retry_count=claimed_retry_count,
                )

            record = asr_live_repo.update(session_id, claim_latest_reservation)
            owned_reservation = dict((record.get("realtime_transcript_correction") or {}).get("reservation") or {})
            if (
                not claim_result.get("claimed")
                or str(owned_reservation.get("batch_id") or "") != batch_id
                or owned_reservation.get("status") != "reserved"
            ):
                current_gate = dict(claim_result.get("gate") or {})
                if not current_gate:
                    current_gate = {
                        "eligible": False,
                        "reason": str(claim_result.get("reason") or "reservation_in_flight"),
                        "segment_ids": list(owned_reservation.get("segment_ids") or []),
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                    }
                return {
                    "session_id": session_id,
                    "called": False,
                    "gate": current_gate,
                    "status": dict(record.get("realtime_transcript_correction") or {}),
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }
            gate = dict(claim_result["gate"])
            segment_ids = list(claim_result["segment_ids"])
            final_events = list(claim_result["final_events"])
            retry_count = max(0, int(claim_result["retry_count"] or 0))
            final_events = realtime_transcript_correction.final_events_for_segment_ids(record, segment_ids)
            indexed_batch = realtime_transcript_correction.encode_indexed_batch(final_events)
            _metrics.metrics.inc("llm_calls", 1)
            try:
                corrected_batch, usage, degraded = asr_correct.correct_transcript(
                    indexed_batch,
                    replace(config, timeout_seconds=min(config.timeout_seconds, 25.0), max_retries=0),
                    raise_on_failure=True,
                )
                if degraded:
                    raise RuntimeError("strict realtime correction returned a degraded fallback")
                _record_llm_usage(
                    session_id,
                    purpose="realtime_transcript_correction",
                    config=config,
                    usage=usage,
                )
            except Exception as exc:
                completed_at_ms = int(time.time() * 1_000)
                provider_retryable = bool(getattr(exc, "retryable", False))
                terminal_failure_threshold = 2 if provider_retryable else 1

                def commit_provider_failure(latest: dict[str, Any]) -> dict[str, Any]:
                    existing = dict(latest.get("realtime_transcript_correction") or {})
                    failure_counts = {
                        str(segment_id): max(0, int(count or 0))
                        for segment_id, count in dict(existing.get("failure_counts") or {}).items()
                    }
                    for segment_id in segment_ids:
                        failure_counts[segment_id] = failure_counts.get(segment_id, 0) + 1
                    terminal_failed_segment_ids = sorted(
                        segment_id
                        for segment_id, count in failure_counts.items()
                        if count >= terminal_failure_threshold
                    )
                    failed = realtime_transcript_correction.apply_revision_events(
                        latest,
                        [],
                        status={
                            "status": "provider_failed_terminal" if terminal_failed_segment_ids else "provider_error",
                            "last_source": "fallback_batch",
                            "failure_counts": failure_counts,
                            "failed_segment_ids": sorted(failure_counts),
                            "terminal_failed_segment_ids": terminal_failed_segment_ids,
                            "processed_segment_ids": sorted(
                                set(existing.get("processed_segment_ids") or []).union(terminal_failed_segment_ids)
                            ),
                        },
                    )
                    return realtime_transcript_correction.commit_batch_audit(
                        failed,
                        batch_id=batch_id,
                        status="provider_failed",
                        completed_at_ms=completed_at_ms,
                        usage={},
                        error_code="realtime_correction_provider_failed",
                        **audit_metadata,
                        degraded=True,
                        fallback=True,
                        retry=retry_count > 0,
                    )

                failed_record = asr_live_repo.update(session_id, commit_provider_failure)
                failed_status = dict(failed_record.get("realtime_transcript_correction") or {})
                terminal_segment_ids = set(failed_status.get("terminal_failed_segment_ids") or [])
                raise RealtimeCorrectionProviderError(
                    retryable=(provider_retryable and not set(segment_ids).issubset(terminal_segment_ids)),
                ) from exc
            decoded = realtime_transcript_correction.decode_indexed_batch(corrected_batch, final_events)
            batch_at_ms = max((int(event.get("at_ms") or 0) for event in final_events), default=0)

            def correction_status(
                latest: dict[str, Any],
                *,
                state: str,
                rejected_segment_ids: list[str],
                accepted_segment_ids: list[str],
            ) -> dict[str, Any]:
                existing = dict(latest.get("realtime_transcript_correction") or {})
                existing_revised_segment_ids = set(existing.get("revised_segment_ids") or [])
                failure_counts = {
                    str(segment_id): max(0, int(count or 0))
                    for segment_id, count in dict(existing.get("failure_counts") or {}).items()
                }
                for segment_id in accepted_segment_ids:
                    failure_counts.pop(segment_id, None)
                for segment_id in rejected_segment_ids:
                    failure_counts[segment_id] = failure_counts.get(segment_id, 0) + 1
                rejected_ids = set(existing.get("rejected_segment_ids") or [])
                rejected_ids.difference_update(accepted_segment_ids)
                rejected_ids.update(rejected_segment_ids)
                effective_state = state
                if state == "correction_rejected" and existing_revised_segment_ids:
                    effective_state = "partially_completed"
                return {
                    "status": effective_state,
                    "last_batch_at_ms": batch_at_ms,
                    "last_batch_wall_clock_ms": now_wall_clock_ms,
                    "waiting_since_epoch_ms": now_wall_clock_ms,
                    "last_source": "fallback_batch",
                    "failure_counts": failure_counts,
                    "failed_segment_ids": sorted(failure_counts),
                    "rejected_segment_ids": sorted(rejected_ids),
                    "terminal_failed_segment_ids": sorted(
                        segment_id for segment_id, count in failure_counts.items() if count >= 2
                    ),
                    "skipped_segment_ids": sorted(
                        set(existing.get("skipped_segment_ids") or []).union(gate.get("oversized_segment_ids") or [])
                    ),
                }

            if decoded is None:
                completed_at_ms = int(time.time() * 1_000)

                def commit_mapping_rejection(latest: dict[str, Any]) -> dict[str, Any]:
                    rejected = realtime_transcript_correction.apply_revision_events(
                        latest,
                        [],
                        status=correction_status(
                            latest,
                            state="mapping_rejected",
                            rejected_segment_ids=segment_ids,
                            accepted_segment_ids=[],
                        ),
                    )
                    return realtime_transcript_correction.commit_batch_audit(
                        rejected,
                        batch_id=batch_id,
                        status="rejected",
                        completed_at_ms=completed_at_ms,
                        usage=usage,
                        **audit_metadata,
                        degraded=bool(degraded),
                        fallback=True,
                        retry=retry_count > 0,
                    )

                updated = asr_live_repo.update(
                    session_id,
                    commit_mapping_rejection,
                )
                return {
                    "session_id": session_id,
                    "called": True,
                    "gate": gate,
                    "status": dict(updated.get("realtime_transcript_correction") or {}),
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }

            revisions: list[dict[str, Any]] = []
            no_revision_segment_ids: list[str] = []
            for final_event, corrected_text in zip(final_events, decoded, strict=True):
                final_payload = final_event.get("payload") or {}
                original_text = str(
                    final_payload.get("normalized_text")
                    or final_payload.get("text")
                    or final_event.get("normalized_text")
                    or final_event.get("text")
                    or ""
                ).strip()
                if not degraded and str(corrected_text or "").strip() == original_text:
                    no_revision_segment_ids.append(
                        str(final_payload.get("segment_id") or final_event.get("segment_id") or "")
                    )
                    continue
                revision = realtime_transcript_correction.build_revision_event(
                    session_id=session_id,
                    final_event=final_event,
                    corrected_text=corrected_text,
                    source="fallback_batch",
                    usage=usage,
                    batch_id=batch_id,
                )
                if revision is not None:
                    revisions.append(revision)
            accepted_segment_ids = [
                str((revision.get("payload") or {}).get("supersedes_segment_id") or "") for revision in revisions
            ]
            rejected_segment_ids = [
                segment_id
                for segment_id in segment_ids
                if segment_id not in set(accepted_segment_ids).union(no_revision_segment_ids)
            ]
            if rejected_segment_ids and revisions:
                correction_state = "partially_completed"
            elif rejected_segment_ids:
                correction_state = "correction_rejected"
            elif no_revision_segment_ids and not revisions:
                correction_state = "no_revision_needed"
            elif degraded:
                correction_state = "degraded"
            else:
                correction_state = "completed"
            completed_at_ms = int(time.time() * 1_000)

            def commit_correction_batch(latest: dict[str, Any]) -> dict[str, Any]:
                completed = realtime_transcript_correction.apply_revision_events(
                    latest,
                    revisions,
                    status={
                        **correction_status(
                            latest,
                            state=correction_state,
                            rejected_segment_ids=rejected_segment_ids,
                            accepted_segment_ids=[*accepted_segment_ids, *no_revision_segment_ids],
                        ),
                        "processed_segment_ids": sorted(
                            set(
                                (latest.get("realtime_transcript_correction") or {}).get("processed_segment_ids") or []
                            ).union(no_revision_segment_ids)
                        ),
                    },
                )
                completed = _recompute_asr_semantic_quality(completed)
                committed_status = str(
                    (completed.get("realtime_transcript_correction") or {}).get("status") or correction_state
                )
                return realtime_transcript_correction.commit_batch_audit(
                    completed,
                    batch_id=batch_id,
                    status=(
                        "partially_completed"
                        if committed_status == "partially_completed"
                        else "rejected"
                        if committed_status == "correction_rejected"
                        else "completed"
                    ),
                    completed_at_ms=completed_at_ms,
                    usage=usage,
                    **audit_metadata,
                    degraded=bool(degraded),
                    fallback=True,
                    retry=retry_count > 0,
                )

            try:
                updated = asr_live_repo.update(
                    session_id,
                    commit_correction_batch,
                )
            except KeyError:
                # Stop/cleanup may intentionally delete a session while a remote
                # correction is still in flight. The result is no longer usable.
                return {
                    "session_id": session_id,
                    "called": False,
                    "gate": {
                        "eligible": False,
                        "reason": "session_deleted",
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                    },
                    "status": {
                        "policy_version": realtime_transcript_correction.POLICY_VERSION,
                        "status": "session_deleted",
                    },
                    "revision_count": 0,
                    "transcript_revisions": [],
                    "no_revision_segment_ids": [],
                }
            return {
                "session_id": session_id,
                "called": True,
                "gate": gate,
                "status": dict(updated.get("realtime_transcript_correction") or {}),
                "revision_count": len(revisions),
                "transcript_revisions": revisions,
                "no_revision_segment_ids": no_revision_segment_ids,
            }
        finally:
            lane_lease.release()

    app.state.run_asr_live_session_realtime_corrections_once = run_asr_live_session_realtime_corrections_once

    def _get_asr_live_record_for_derivation(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _migrate_asr_live_quality_record(record)

    def _migrate_asr_live_quality_record(record: dict[str, Any]) -> dict[str, Any]:
        """Persist the current quality policy so old sessions cannot stay gated.

        Quality policy changes are intentionally data migrations: the same
        canonical transcript must drive the history list, gates, and derived
        results. A stale semantic-only degradation is removed when the new
        policy classifies the transcript as readable.
        """
        provider = str(record.get("provider") or "")
        is_mock = bool(record.get("is_mock")) or provider in {"local_mock_asr", "fake"}
        ingest_mode = str(record.get("ingest_mode") or "")
        input_source = _asr_live_input_source(
            record,
            provider=provider,
            is_mock=is_mock,
            ingest_mode=ingest_mode,
        )
        if input_source not in ASR_SEMANTIC_QUALITY_FORMAL_INPUT_SOURCES:
            return record
        normalized = _recompute_asr_semantic_quality(record)
        if normalized.get("asr_semantic_quality") == record.get("asr_semantic_quality") and normalized.get(
            "degradation_reasons"
        ) == record.get("degradation_reasons"):
            return record
        session_id = str(record.get("session_id") or "")
        if not session_id:
            return normalized
        try:
            return asr_live_repo.update(
                session_id,
                lambda latest: _recompute_asr_semantic_quality(latest),
            )
        except (KeyError, ValueError):
            return normalized

    def _deterministic_demo_provider_metadata() -> dict[str, Any]:
        return {
            "provider": DETERMINISTIC_DEMO_PROVIDER,
            "model": DETERMINISTIC_DEMO_MODEL,
            "is_mock": True,
            "configured_from_env": False,
        }

    def _zero_llm_usage() -> dict[str, int]:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def _first_transcript_quote(record: dict[str, Any]) -> str:
        for event in realtime_transcript_correction.effective_final_events(record):
            payload = event.get("payload") or {}
            text = str(payload.get("normalized_text") or payload.get("text") or event.get("text") or "").strip()
            if text:
                return text[:80]
        return "会议原话"

    def _deterministic_demo_transcript_text(record: dict[str, Any]) -> str:
        text = " ".join(
            str((event.get("payload") or {}).get("normalized_text") or (event.get("payload") or {}).get("text") or "")
            for event in realtime_transcript_correction.effective_final_events(record)
        )
        return re.sub(r"\s+", " ", text).strip()

    def _deterministic_demo_is_release_context(text: str) -> bool:
        return any(marker in text for marker in ("灰度", "回滚", "P99", "错误率", "发布评审", "发布门禁"))

    def _deterministic_demo_is_product_feedback_context(text: str) -> bool:
        return any(
            marker in text
            for marker in ("用户访谈反馈", "首页", "持续看到", "最后一句话", "录音", "文字稿", "会议纪要")
        )

    def _sentence_bullet(text: str, fallback: str, limit: int = 80) -> str:
        cleaned = re.sub(r"\s+", "", text).strip("，。；、 ")
        if not cleaned:
            cleaned = fallback
        if len(cleaned) > limit:
            cleaned = cleaned[:limit].rstrip("，。；、 ") + "..."
        return cleaned

    def _deterministic_demo_key_points(text: str) -> list[str]:
        points: list[str] = []
        if "用户访谈反馈" in text:
            points.append("本次讨论围绕用户访谈反馈。")
        if "首页" in text or "下一步" in text:
            points.append("首页需要更清楚地告诉新用户下一步该做什么。")
        if "持续看到" in text or "最后一句话" in text or "前面的讨论内容" in text:
            points.append("会议中需要持续看到前面的讨论内容，而不是只显示最后一句话。")
        if "录音" in text and ("文字稿" in text or "会议纪要" in text):
            points.append("结束后需要马上看到录音、文字稿和会议纪要。")
        if _deterministic_demo_is_release_context(text):
            points.extend(
                [
                    "先按小流量灰度推进，并以错误率阈值作为回滚条件。",
                    "明确回滚负责人、观察窗口和兼容性测试补充项。",
                ]
            )
        if points:
            return points[:6]
        return [f"本次讨论重点：{_sentence_bullet(text, '会议内容', 90)}。"]

    def _deterministic_demo_action_items(text: str) -> list[str]:
        actions: list[str] = []
        pattern = re.compile(
            r"([\u4e00-\u9fff]{2})(今天|明天|后天|本周|下周|周[一二三四五六日天])?"
            r"(负责|确认|补充)(.{1,24}?)(?=[，。；、\s]|[\u4e00-\u9fff]{2}(?:今天|明天|后天|本周|下周|周[一二三四五六日天])?(?:负责|确认|补充)|$)"
        )
        for match in pattern.finditer(text):
            owner, due, verb, item = match.groups()
            item = _sentence_bullet(item, "相关事项", 24)
            action = f"{owner}{due or ''}{verb}{item}。"
            if action not in actions:
                actions.append(action)
            if len(actions) >= 4:
                break
        if actions:
            return actions
        if any(marker in text for marker in ("负责", "确认", "补充")):
            return ["把已提到的负责人、截止时间和验收口径整理成行动清单。"]
        return ["补充本次讨论的负责人、截止时间和验收口径。"]

    def _deterministic_demo_suggestion_text(record: dict[str, Any]) -> str:
        text = _deterministic_demo_transcript_text(record)
        has_product = _deterministic_demo_is_product_feedback_context(text)
        has_release = _deterministic_demo_is_release_context(text)
        if has_product and has_release:
            return "建议把用户访谈反馈和发布评审拆成两组待办：一组验证首页引导、实时上下文和导出复盘，一组确认灰度阈值、回滚 owner 和监控口径。"
        if has_release:
            return "建议确认回滚负责人、错误率阈值和观察窗口，避免灰度异常时无人决策。"
        if has_product:
            return "建议把用户访谈反馈中的首页引导、实时上下文和导出复盘诉求拆成可验收清单。"
        return "建议把本次讨论中的结论、负责人、截止时间和未闭环问题整理成可执行清单。"

    def _deterministic_demo_approach_text(record: dict[str, Any]) -> tuple[str, str]:
        text = _deterministic_demo_transcript_text(record)
        has_product = _deterministic_demo_is_product_feedback_context(text)
        has_release = _deterministic_demo_is_release_context(text)
        if has_product and has_release:
            return (
                "可以把用户访谈反馈先收敛为首页引导、实时上下文展示、录音、文字稿和会议纪要三类体验问题，同时把灰度比例、P99、错误率、回滚 owner 和 SLO 看板写成发布门禁。",
                "混合会议需要同时保留产品体验和发布风险两条主线",
            )
        if has_release:
            return (
                "可以把灰度推进、回滚阈值、负责人和值班窗口写成发布检查表。",
                "灰度发布方案需要可执行边界",
            )
        if has_product:
            return (
                "可以把用户访谈反馈先收敛为首页引导、实时上下文展示、录音、文字稿和会议纪要三类体验问题，再排优先级。",
                "用户反馈需要转成可验收的产品改进项",
            )
        return (
            "可以先把讨论内容分成已确认结论、待办事项和未闭环问题，再决定下一步优先级。",
            "普通会议需要沉淀为可执行复盘",
        )

    def _deterministic_demo_minutes(record: dict[str, Any]) -> str:
        text = _deterministic_demo_transcript_text(record)
        quote = _first_transcript_quote(record)
        has_product = _deterministic_demo_is_product_feedback_context(text)
        has_release = _deterministic_demo_is_release_context(text)
        if has_release and not has_product:
            return "\n".join(
                [
                    "# 会议纪要",
                    "",
                    "## 已确认决策",
                    "- 先按小流量灰度推进，并以错误率阈值作为回滚条件。",
                    "",
                    "## 行动项",
                    "- 明确回滚负责人、观察窗口和兼容性测试补充项。",
                    "",
                    "## 风险",
                    "- 如果负责人和阈值没有写入发布检查表，异常时容易延迟决策。",
                    "",
                    "## 未闭环问题",
                    "- 错误率阈值、P99 观察窗口和值班 owner 需要在发布前确认。",
                    "",
                    "## 证据片段",
                    f"- {quote}",
                ]
            )

        key_points = _deterministic_demo_key_points(text)
        actions = _deterministic_demo_action_items(text)
        risks: list[str] = []
        if "最后一句话" in text or "持续看到" in text:
            risks.append("如果页面只显示最后一句话，会议中用户会丢失前文上下文。")
        if "导出" in text or "录音" in text:
            risks.append("录音、文字稿和会议纪要入口如果不清晰，会影响会后复盘效率。")
        if not risks:
            risks.append("需要把讨论内容转成可验收项，否则会后容易遗漏。")
        questions: list[str] = []
        if "首页" in text or "下一步" in text:
            questions.append("首页下一步引导的验收标准是什么？")
        if "持续看到" in text or "最后一句话" in text:
            questions.append("实时文字展示需要保留多少上下文、如何处理 ASR 修正？")
        if not questions:
            questions.append("下一步优先级、负责人和截止时间需要确认。")

        return "\n".join(
            [
                "# 会议纪要",
                "",
                "## 讨论主题",
                f"- {_sentence_bullet(text, '本次会议', 48)}。",
                "",
                "## 已确认重点",
                *[f"- {point}" for point in key_points],
                "",
                "## 行动项",
                *[f"- {action}" for action in actions],
                "",
                "## 风险",
                *[f"- {risk}" for risk in risks[:3]],
                "",
                "## 未闭环问题",
                *[f"- {question}" for question in questions[:3]],
                "",
                "## 证据片段",
                f"- {quote}",
            ]
        )

    def _deterministic_demo_execution_runs_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
        session_id = str(record.get("session_id", ""))
        previews = _execution_previews_from_record(record)
        if not previews:
            previews = [
                {
                    "execution_id": f"deterministic_demo_preview_{session_id}",
                    "execution_status": "preview_only",
                    "request_id": "deterministic_demo_request",
                    "request_draft_event_id": "",
                    "request_draft_sequence": 0,
                    "request_type": "suggestion_card",
                    "target_candidate_id": "deterministic_demo_candidate",
                    "target_type": "Risk",
                    "target_id": "deterministic_demo_target",
                    "gap_rule_id": "deterministic_demo_packaged_probe",
                    "prompt_version": "suggestion-card-execution-preview.v1",
                    "provider": "not_configured",
                    "model": "not_called",
                    "llm_call_status": "not_called",
                    "schema_name": "SuggestionCardV1",
                    "schema_status": "not_generated",
                    "card_status": "not_created",
                    "cost_status": "not_estimated",
                    "idempotency_key": f"live_asr_execution_preview:{session_id}:deterministic_demo_request",
                    "source_event_ids": [],
                    "evidence_span_ids": [],
                    "evidence_spans": [],
                    "evidence_context": _first_transcript_quote(record),
                    "segment_batch": [],
                    "candidate_confidence": 0.86,
                    "candidate_confidence_level": "medium",
                    "candidate_degradation_reasons": [],
                    "input_summary": _first_transcript_quote(record),
                    "suggested_prompt": _deterministic_demo_suggestion_text(record),
                }
            ]
        runs: list[dict[str, Any]] = []
        suggestion_text = _deterministic_demo_suggestion_text(record)
        for index, preview in enumerate(previews[:3]):
            request_id = str(preview.get("request_id") or f"request_{index}")
            card = {
                "card_id": f"deterministic_demo_suggestion_card_{request_id}",
                "card_status": "new",
                "schema_name": "SuggestionCardV1",
                "gap_rule_id": preview.get("gap_rule_id"),
                "target_type": preview.get("target_type") or "Risk",
                "target_id": preview.get("target_id"),
                "suggestion_text": suggestion_text,
                "confidence": 0.86,
                "trigger_reason": preview.get("suggested_prompt") or preview.get("input_summary") or suggestion_text,
                "evidence_span_ids": list(preview.get("evidence_span_ids") or []),
                "evidence_spans": list(preview.get("evidence_spans") or []),
                "evidence_context": str(preview.get("evidence_context") or _first_transcript_quote(record)),
                "source_event_ids": list(preview.get("source_event_ids") or []),
                "llm_trace": {
                    "provider": DETERMINISTIC_DEMO_PROVIDER,
                    "model": DETERMINISTIC_DEMO_MODEL,
                    "prompt_version": DETERMINISTIC_DEMO_PROMPT_VERSION,
                    "call_count": 0,
                    "retry_count": 0,
                    "usage": _zero_llm_usage(),
                },
            }
            runs.append(
                {
                    **preview,
                    "run_id": f"asr_llm_execution_run_deterministic_demo_{request_id}",
                    "run_status": "completed",
                    "execution_status": "executed_demo_no_cost",
                    "provider": DETERMINISTIC_DEMO_PROVIDER,
                    "model": DETERMINISTIC_DEMO_MODEL,
                    "prompt_version": DETERMINISTIC_DEMO_PROMPT_VERSION,
                    "llm_call_status": "not_called",
                    "schema_status": "generated",
                    "card_status": "new",
                    "cost_status": "no_cost",
                    "idempotency_key": f"live_asr_execution_run:deterministic_demo:{session_id}:{request_id}",
                    "card": card,
                    "llm_usage": _zero_llm_usage(),
                }
            )
        return runs

    def _deterministic_demo_approach_cards(record: dict[str, Any]) -> list[dict[str, Any]]:
        quote = _first_transcript_quote(record)
        suggestion_text, trigger_reason = _deterministic_demo_approach_text(record)
        return [
            {
                "card_id": "deterministic_demo_approach_context_summary",
                "card_type": "approach.consideration",
                "card_status": "new",
                "suggestion_text": suggestion_text,
                "confidence": 0.88,
                "trigger_reason": trigger_reason,
                "evidence_quote": quote,
                "llm_trace": {
                    "provider": DETERMINISTIC_DEMO_PROVIDER,
                    "model": DETERMINISTIC_DEMO_MODEL,
                    "prompt_version": DETERMINISTIC_DEMO_PROMPT_VERSION,
                    "call_count": 0,
                    "usage": _zero_llm_usage(),
                },
            }
        ]

    def _quality_blocked_demo_derivation(record: dict[str, Any]) -> dict[str, Any] | None:
        metadata = _asr_live_event_source_metadata(record)
        blockers = list(metadata.get("acceptance_blockers") or [])
        if not _formal_derivation_quality_blocked(metadata):
            return None
        return {
            "derivation_blocked": True,
            "execution_boundary": "demo_no_cost_quality_blocked",
            "degraded": True,
            "degradation_reasons": list(metadata.get("degradation_reasons") or []),
            "acceptance_blockers": blockers,
            "asr_semantic_quality": dict(metadata.get("asr_semantic_quality") or {}),
            "message": "识别语义质量不足：声音可用，但没有听清关键业务内容，先不生成正式建议。",
        }

    def _asr_live_session_llm_execution_runs_response(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
        *,
        allow_non_acceptance_execution: bool,
        execution_boundary: str,
    ) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        candidate_selection: dict[str, Any] | None = None
        if payload.mode == "disabled":
            config = None
            llm_provider = llm_service.provider_metadata(config)
            runs = _disabled_execution_runs_from_record(record)
        elif payload.mode == DETERMINISTIC_DEMO_DERIVATION_MODE and allow_non_acceptance_execution:
            llm_provider = _deterministic_demo_provider_metadata()
            quality_block = _quality_blocked_demo_derivation(record)
            if quality_block:
                return {
                    "session_id": session_id,
                    "source": record["source"],
                    "trace_kind": record["trace_kind"],
                    "executor_mode": payload.mode,
                    "llm_provider": llm_provider,
                    "run_count": 0,
                    "runs": [],
                    **quality_block,
                }
            execution_boundary = "demo_no_cost_execution"
            runs = _deterministic_demo_execution_runs_from_record(record)
            cards = [
                dict(run["card"])
                for run in runs
                if run.get("run_status") == "completed" and isinstance(run.get("card"), dict)
            ]
            if cards:
                record = _persist_asr_live_record_fields(
                    asr_live_repo,
                    record,
                    suggestion_cards=_merge_records_by_id(
                        list(record.get("suggestion_cards") or []),
                        cards,
                        key="card_id",
                    ),
                )
        elif payload.mode == "enabled":
            _ensure_enabled_llm_allowed(
                record,
                allow_non_acceptance_execution=allow_non_acceptance_execution,
            )
            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "LLM execution enabled but LLM_GATEWAY_BASE_URL / "
                        "LLM_GATEWAY_API_KEY not configured in environment"
                    ),
                )
            config = llm_service.realtime_config(config)
            _ensure_llm_provider_allowed_for_derivation(
                config,
                allow_non_acceptance_execution=allow_non_acceptance_execution,
            )
            llm_provider = llm_service.provider_metadata(config)
            max_candidates, selection_reason = _enabled_execution_candidate_limit(payload.max_candidates)
            previews, candidate_selection = _select_enabled_execution_previews(
                _execution_previews_from_record(record),
                max_candidates=max_candidates,
                requested_max_candidates=payload.max_candidates,
                selection_reason=selection_reason,
            )
            if candidate_selection["selection_applied"]:
                _log.info(
                    "llm.execution.candidate_selection_applied",
                    session_id=session_id,
                    total_candidates=candidate_selection["total_candidates"],
                    selected_count=candidate_selection["selected_count"],
                    skipped_count=candidate_selection["skipped_count"],
                    policy_version=candidate_selection["policy_version"],
                )
            runs = []
            for preview in previews:
                _enforce_llm_budget(session_id, purpose="formal_suggestion", config=config)
                candidate_runs = llm_service.build_enabled_execution_runs([preview], config)
                runs.extend(candidate_runs)
                for run in candidate_runs:
                    _record_llm_usage(
                        session_id,
                        purpose="formal_suggestion",
                        config=config,
                        usage=run.get("llm_usage"),
                    )
            _metrics.metrics.inc("llm_calls", len(runs))
            _metrics.metrics.inc("cards_created", sum(1 for r in runs if r.get("card_status") == "new"))
            cards = [
                dict(run["card"])
                for run in runs
                if run.get("run_status") == "completed" and isinstance(run.get("card"), dict)
            ]
            if cards:
                record = _persist_asr_live_record_fields(
                    asr_live_repo,
                    record,
                    suggestion_cards=_merge_records_by_id(
                        list(record.get("suggestion_cards") or []),
                        cards,
                        key="card_id",
                    ),
                )
        else:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported llm execution mode: {payload.mode}",
            )
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "executor_mode": payload.mode,
            "execution_boundary": execution_boundary,
            "llm_provider": llm_provider,
            "run_count": len(runs),
            "runs": runs,
            **({"candidate_selection": candidate_selection} if candidate_selection else {}),
        }

    def _asr_live_session_approach_cards_response(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
        *,
        allow_non_acceptance_execution: bool,
        execution_boundary: str,
    ) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        if payload.mode == "disabled":
            # Disabled mode: return existing approach cards from record without calling LLM
            existing_cards = list(record.get("approach_cards") or [])
            config = None
            llm_provider = llm_service.provider_metadata(config)
            return {
                "session_id": session_id,
                "source": record["source"],
                "trace_kind": record["trace_kind"],
                "execution_boundary": execution_boundary,
                "approach_mode": "disabled",
                "approach_cards": existing_cards,
                "count": len(existing_cards),
                "llm_provider": llm_provider,
                "llm_usage": _zero_llm_usage(),
                "degraded": False,
            }
        if payload.mode == DETERMINISTIC_DEMO_DERIVATION_MODE and allow_non_acceptance_execution:
            quality_block = _quality_blocked_demo_derivation(record)
            if quality_block:
                return {
                    "session_id": session_id,
                    "source": record["source"],
                    "trace_kind": record["trace_kind"],
                    "approach_cards": [],
                    "count": 0,
                    "llm_provider": _deterministic_demo_provider_metadata(),
                    "llm_usage": _zero_llm_usage(),
                    **quality_block,
                }
            cards = _deterministic_demo_approach_cards(record)
            record = _persist_asr_live_record_fields(
                asr_live_repo,
                record,
                approach_cards=_merge_records_by_id(
                    list(record.get("approach_cards") or []),
                    cards,
                    key="card_id",
                ),
            )
            return {
                "session_id": session_id,
                "source": record["source"],
                "trace_kind": record["trace_kind"],
                "execution_boundary": "demo_no_cost_execution",
                "approach_cards": cards,
                "count": len(cards),
                "llm_provider": _deterministic_demo_provider_metadata(),
                "llm_usage": _zero_llm_usage(),
                "degraded": False,
            }
        if payload.mode != "enabled":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unsupported_approach_mode",
                    "message": f"不支持的方案模式: {payload.mode}，请使用 'enabled'、'disabled' 或 'deterministic_demo'",
                },
            )
        _ensure_enabled_llm_allowed(
            record,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "LLM execution enabled but LLM_GATEWAY_BASE_URL / LLM_GATEWAY_API_KEY not configured in environment"
                ),
            )
        llm_provider = llm_service.provider_metadata(config)
        _ensure_llm_provider_allowed_for_derivation(
            config,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        transcript_text = " ".join(
            str((e.get("payload") or {}).get("text", ""))
            for e in record.get("events") or []
            if e.get("event_type") == "transcript_final"
        )
        _enforce_llm_budget(session_id, purpose="approach_cards", config=config)
        cards, usage, degraded = llm_service.build_approach_cards(transcript_text, config)
        _record_llm_usage(
            session_id,
            purpose="approach_cards",
            config=config,
            usage=usage,
        )
        if cards:
            record = _persist_asr_live_record_fields(
                asr_live_repo,
                record,
                approach_cards=_merge_records_by_id(
                    list(record.get("approach_cards") or []),
                    cards,
                    key="card_id",
                ),
            )
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "execution_boundary": execution_boundary,
            "approach_cards": cards,
            "count": len(cards),
            "llm_provider": llm_provider,
            "llm_usage": usage,
            "degraded": degraded,
        }

    def _transcript_text_from_record(record: dict[str, Any]) -> str:
        return " ".join(
            str((e.get("payload") or {}).get("normalized_text") or (e.get("payload") or {}).get("text", ""))
            for e in realtime_transcript_correction.effective_final_events(record)
        )

    def _transcript_export_text_from_record(record: dict[str, Any]) -> str:
        lines: list[str] = []
        for event in realtime_transcript_correction.effective_final_events(record):
            payload = event.get("payload") or {}
            text = str(payload.get("normalized_text") or payload.get("text") or "").strip()
            if not text:
                continue
            start_ms = int(payload.get("start_ms") or event.get("start_ms") or 0)
            lines.append(f"[{_format_mmss(start_ms)}] {text}")
        return "\n".join(lines)

    def _attachment_headers(session_id: str, suffix: str) -> dict[str, str]:
        safe_session_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", session_id).strip("._") or "meeting"
        return {"Content-Disposition": f'attachment; filename="{safe_session_id}.{suffix}"'}

    def _asr_live_session_minutes_response(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
        *,
        allow_non_acceptance_execution: bool,
        execution_boundary: str,
    ) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        if payload.mode == "disabled":
            # Disabled mode: return existing minutes from record without calling LLM
            existing_minutes = record.get("minutes") or {}
            minutes_md = str(existing_minutes.get("minutes_md") or "")
            config = None
            llm_provider = llm_service.provider_metadata(config)
            return {
                "session_id": session_id,
                "execution_boundary": execution_boundary,
                "minutes_mode": "disabled",
                "minutes_md": minutes_md,
                "llm_provider": llm_provider,
                "llm_usage": _zero_llm_usage(),
                "degraded": False,
            }
        if payload.mode == DETERMINISTIC_DEMO_DERIVATION_MODE and allow_non_acceptance_execution:
            quality_block = _quality_blocked_demo_derivation(record)
            if quality_block:
                return {
                    "session_id": session_id,
                    "minutes_md": "",
                    "llm_provider": _deterministic_demo_provider_metadata(),
                    "llm_usage": _zero_llm_usage(),
                    **quality_block,
                }
            markdown = _deterministic_demo_minutes(record)
            minutes_payload = {
                "minutes_md": markdown,
                "llm_usage": _zero_llm_usage(),
                "degraded": False,
                "llm_provider": _deterministic_demo_provider_metadata(),
            }
            record = _persist_asr_live_record_fields(
                asr_live_repo,
                record,
                minutes=minutes_payload,
            )
            return {
                "session_id": session_id,
                "execution_boundary": "demo_no_cost_execution",
                "minutes_md": markdown,
                "llm_provider": _deterministic_demo_provider_metadata(),
                "llm_usage": _zero_llm_usage(),
                "degraded": False,
            }
        if payload.mode != "enabled":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "unsupported_minutes_mode",
                    "message": f"不支持的纪要模式: {payload.mode}，请使用 'enabled'、'disabled' 或 'deterministic_demo'",
                },
            )
        _ensure_enabled_llm_allowed(
            record,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(
                status_code=422,
                detail="LLM execution enabled but LLM_GATEWAY_BASE_URL / LLM_GATEWAY_API_KEY not configured in environment",
            )
        _ensure_llm_provider_allowed_for_derivation(
            config,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        _enforce_llm_budget(session_id, purpose="minutes_markdown", config=config)
        markdown, structured, usage, degraded = llm_service.build_minutes_artifact(
            _transcript_text_from_record(record),
            config,
        )
        _record_llm_usage(
            session_id,
            purpose="minutes_markdown",
            config=config,
            usage=usage,
        )
        minutes_payload = {
            "minutes_md": markdown,
            "minutes_json": structured,
            "llm_usage": usage,
            "degraded": degraded,
        }
        record = _persist_asr_live_record_fields(
            asr_live_repo,
            record,
            minutes=minutes_payload,
        )
        return {
            "session_id": session_id,
            "execution_boundary": execution_boundary,
            "minutes_md": markdown,
            "minutes": structured,
            "llm_usage": usage,
            "degraded": degraded,
            **(
                {
                    "error_code": "llm_minutes_generation_failed",
                    "message": "模型没有返回可解析纪要，文字稿和录音已保留，请稍后重试。",
                }
                if degraded
                else {}
            ),
        }

    def _asr_live_session_minutes_json_response(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
        *,
        allow_non_acceptance_execution: bool,
        execution_boundary: str,
    ) -> dict[str, Any]:
        record = _get_asr_live_record_for_derivation(session_id)
        if payload.mode != "enabled":
            raise HTTPException(status_code=422, detail=f"unsupported minutes mode: {payload.mode}")
        _ensure_enabled_llm_allowed(
            record,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(status_code=422, detail="LLM execution enabled but LLM_GATEWAY_* not configured")
        _ensure_llm_provider_allowed_for_derivation(
            config,
            allow_non_acceptance_execution=allow_non_acceptance_execution,
        )
        llm_provider = llm_service.provider_metadata(config)
        _enforce_llm_budget(session_id, purpose="minutes_json", config=config)
        markdown, parsed, usage, degraded = llm_service.build_minutes_artifact(
            _transcript_text_from_record(record),
            config,
        )
        _record_llm_usage(
            session_id,
            purpose="minutes_json",
            config=config,
            usage=usage,
        )
        minutes = dict(record.get("minutes") or {})
        minutes.update(
            {
                "minutes_md": markdown,
                "minutes_json": parsed,
                "llm_usage": usage,
                "degraded": degraded,
                "minutes_json_llm_usage": usage,
                "minutes_json_degraded": degraded,
            }
        )
        _persist_asr_live_record_fields(
            asr_live_repo,
            record,
            minutes=minutes,
        )
        return {
            "session_id": session_id,
            "execution_boundary": execution_boundary,
            "minutes_md": markdown,
            "minutes": parsed,
            "llm_provider": llm_provider,
            "llm_usage": usage,
            "degraded": degraded,
        }

    @app.post("/live/asr/sessions/{session_id}/llm-execution-runs")
    def create_asr_live_session_llm_execution_runs(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_llm_execution_runs_response(
            session_id,
            payload,
            allow_non_acceptance_execution=False,
            execution_boundary="production_acceptance_execution",
        )

    @app.post("/live/asr/demo/sessions/{session_id}/llm-execution-runs")
    def create_demo_asr_live_session_llm_execution_runs(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_llm_execution_runs_response(
            session_id,
            payload,
            allow_non_acceptance_execution=True,
            execution_boundary="demo_non_acceptance_execution",
        )

    @app.post("/live/asr/sessions/{session_id}/approach-cards")
    def create_asr_live_session_approach_cards(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_approach_cards_response(
            session_id,
            payload,
            allow_non_acceptance_execution=False,
            execution_boundary="production_acceptance_execution",
        )

    @app.post("/live/asr/demo/sessions/{session_id}/approach-cards")
    def create_demo_asr_live_session_approach_cards(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_approach_cards_response(
            session_id,
            payload,
            allow_non_acceptance_execution=True,
            execution_boundary="demo_non_acceptance_execution",
        )

    @app.post("/live/asr/sessions/{session_id}/minutes")
    def create_asr_live_session_minutes(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_minutes_response(
            session_id,
            payload,
            allow_non_acceptance_execution=False,
            execution_boundary="production_acceptance_execution",
        )

    @app.post("/live/asr/demo/sessions/{session_id}/minutes")
    def create_demo_asr_live_session_minutes(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        return _asr_live_session_minutes_response(
            session_id,
            payload,
            allow_non_acceptance_execution=True,
            execution_boundary="demo_non_acceptance_execution",
        )

    @app.get("/live/asr/sessions/{session_id}/minutes.md")
    def get_asr_live_session_minutes_markdown(session_id: str) -> Response:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        minutes = dict(record.get("minutes") or {})
        markdown = str(minutes.get("minutes_md") or "")
        if not markdown:
            raise HTTPException(status_code=404, detail=f"minutes not found for ASR live session: {session_id}")
        return Response(
            content=markdown,
            media_type="text/markdown; charset=utf-8",
            headers=_attachment_headers(session_id, "minutes.md"),
        )

    @app.get("/live/asr/sessions/{session_id}/audio.wav")
    def get_asr_live_session_audio(session_id: str) -> FileResponse:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if data_dir_path is None:
            raise HTTPException(status_code=404, detail=f"audio not found for ASR live session: {session_id}")
        audio = dict(record.get("audio") or {})
        if not audio.get("saved") or not audio.get("relative_path"):
            raise HTTPException(status_code=404, detail=f"audio not found for ASR live session: {session_id}")
        try:
            path = audio_assets.safe_audio_path(data_dir_path, str(audio["relative_path"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"audio file not found for ASR live session: {session_id}")
        audio_format = str(audio.get("format") or "wav").strip(".").lower() or "wav"
        return FileResponse(
            path,
            media_type=_audio_media_type(audio_format),
            filename=f"{session_id}.audio.{audio_format}",
        )

    @app.get("/live/asr/sessions/{session_id}/transcript.txt")
    def get_asr_live_session_transcript_text(session_id: str) -> Response:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        transcript = _transcript_export_text_from_record(record)
        if not transcript:
            raise HTTPException(status_code=404, detail=f"transcript not found for ASR live session: {session_id}")
        return Response(
            content=transcript + "\n",
            media_type="text/plain; charset=utf-8",
            headers=_attachment_headers(session_id, "transcript.txt"),
        )

    @app.delete("/live/asr/sessions/{session_id}")
    async def delete_asr_live_session(session_id: str) -> Any:
        record: dict[str, Any] | None
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            if persistence_coordinator is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"ASR live session not found: {session_id}",
                ) from exc
            try:
                pending_cleanup = persistence_coordinator.pending_audio_cleanup(session_id)
            except ValueError as pending_exc:
                raise HTTPException(status_code=422, detail=str(pending_exc)) from pending_exc
            except (OSError, sqlite3.Error) as pending_exc:
                _log.error(
                    "asr.session.read_cleanup_before_delete_failed",
                    session_id=session_id,
                    error_type=type(pending_exc).__name__,
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "session_id": session_id,
                        "deleted": False,
                        "session_record_deleted": False,
                        "delete_scope": {
                            "session_record": "read_failed",
                            "transcript_events": "retained_unknown",
                            "suggestion_cards": "retained_unknown",
                            "approach_cards": "retained_unknown",
                            "minutes": "retained_unknown",
                            "audio": "cleanup_state_read_failed",
                            "exports": "not_tracked_by_live_session_repo",
                            "evidence_bundle": "not_tracked_by_live_session_repo",
                        },
                        "errors": [
                            {
                                "scope": "audio_cleanup_job",
                                "code": "read_failed",
                                "error_type": type(pending_exc).__name__,
                            }
                        ],
                    },
                )
            if pending_cleanup is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"ASR live session not found: {session_id}",
                ) from exc
            record = None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            _log.error(
                "asr.session.read_before_delete_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": False,
                    "delete_scope": {
                        "session_record": "read_failed",
                        "transcript_events": "retained_unknown",
                        "suggestion_cards": "retained_unknown",
                        "approach_cards": "retained_unknown",
                        "minutes": "retained_unknown",
                        "audio": "retained_not_attempted",
                        "exports": "not_tracked_by_live_session_repo",
                        "evidence_bundle": "not_tracked_by_live_session_repo",
                    },
                    "errors": [
                        {
                            "scope": "session_record",
                            "code": "read_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )

        v2_deletion_job = await _begin_deletion_fence(session_id)
        if persistence_coordinator is not None:
            try:
                outcome = persistence_coordinator.delete_live_session(session_id)
            except KeyError as exc:
                raise HTTPException(
                    status_code=404,
                    detail=f"ASR live session not found: {session_id}",
                ) from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except (OSError, sqlite3.Error) as exc:
                _log.error(
                    "asr.session.atomic_delete_failed",
                    session_id=session_id,
                    error_type=type(exc).__name__,
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "session_id": session_id,
                        "deleted": False,
                        "session_record_deleted": False,
                        "delete_scope": {
                            "session_record": "retained_after_rollback",
                            "transcript_events": "retained_after_rollback",
                            "suggestion_cards": "retained_after_rollback",
                            "approach_cards": "retained_after_rollback",
                            "minutes": "retained_after_rollback",
                            "audio": "retained_not_attempted",
                            "exports": "not_tracked_by_live_session_repo",
                            "evidence_bundle": "not_tracked_by_live_session_repo",
                        },
                        "errors": [
                            {
                                "scope": "session_record",
                                "code": "delete_failed",
                                "error_type": type(exc).__name__,
                            }
                        ],
                    },
                )

            audio_cleanup = outcome.get("audio_cleanup")
            audio_delete_status = "not_present"
            if audio_cleanup is not None:
                try:
                    audio_delete_status = _delete_audio_asset_locked(
                        session_id,
                        dict(audio_cleanup),
                    )
                except (OSError, ValueError) as exc:
                    _log.error(
                        "asr.session.delete_audio_pending_retry",
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    return JSONResponse(
                        status_code=207,
                        content={
                            "session_id": session_id,
                            "deleted": False,
                            "session_record_deleted": True,
                            "audio_cleanup_pending": True,
                            "delete_scope": {
                                "session_record": "deleted",
                                "transcript_events": "deleted_with_session_record",
                                "suggestion_cards": "deleted_with_session_record",
                                "approach_cards": "deleted_with_session_record",
                                "minutes": "deleted_with_session_record",
                                "audio": "cleanup_pending",
                                "exports": "not_tracked_by_live_session_repo",
                                "evidence_bundle": "not_tracked_by_live_session_repo",
                            },
                            "errors": [
                                {
                                    "scope": "audio",
                                    "code": "cleanup_pending",
                                    "error_type": type(exc).__name__,
                                }
                            ],
                        },
                    )
                try:
                    persistence_coordinator.clear_pending_audio_cleanup(session_id)
                except (OSError, sqlite3.Error) as exc:
                    _log.error(
                        "asr.session.clear_audio_cleanup_failed",
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    return JSONResponse(
                        status_code=207,
                        content={
                            "session_id": session_id,
                            "deleted": False,
                            "session_record_deleted": True,
                            "audio_cleanup_pending": True,
                            "delete_scope": {
                                "session_record": "deleted",
                                "transcript_events": "deleted_with_session_record",
                                "suggestion_cards": "deleted_with_session_record",
                                "approach_cards": "deleted_with_session_record",
                                "minutes": "deleted_with_session_record",
                                "audio": "cleanup_pending",
                                "exports": "not_tracked_by_live_session_repo",
                                "evidence_bundle": "not_tracked_by_live_session_repo",
                            },
                            "errors": [
                                {
                                    "scope": "audio_cleanup_job",
                                    "code": "clear_failed",
                                    "error_type": type(exc).__name__,
                                }
                            ],
                        },
                    )
            await _complete_deletion_facts(v2_deletion_job)
            _log.info("asr.session.deleted", session_id=session_id)
            return {
                "session_id": session_id,
                "deleted": True,
                "session_record_deleted": True,
                "delete_scope": {
                    "session_record": "deleted",
                    "transcript_events": "deleted_with_session_record",
                    "suggestion_cards": "deleted_with_session_record",
                    "approach_cards": "deleted_with_session_record",
                    "minutes": "deleted_with_session_record",
                    "audio": audio_delete_status,
                    "exports": "not_tracked_by_live_session_repo",
                    "evidence_bundle": "not_tracked_by_live_session_repo",
                },
            }

        assert record is not None
        try:
            removed = asr_live_repo.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            _log.error(
                "asr.session.delete_record_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": False,
                    "delete_scope": {
                        "session_record": "delete_failed",
                        "transcript_events": "retained_with_session_record",
                        "suggestion_cards": "retained_with_session_record",
                        "approach_cards": "retained_with_session_record",
                        "minutes": "retained_with_session_record",
                        "audio": "retained_not_attempted",
                        "exports": "not_tracked_by_live_session_repo",
                        "evidence_bundle": "not_tracked_by_live_session_repo",
                    },
                    "errors": [
                        {
                            "scope": "session_record",
                            "code": "delete_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )
        if not removed:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}")
        try:
            audio_delete_status = _delete_audio_asset_locked(
                session_id,
                dict(record.get("audio") or {}),
            )
        except (OSError, ValueError) as exc:
            _log.error(
                "asr.session.delete_audio_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=207,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": True,
                    "delete_scope": {
                        "session_record": "deleted",
                        "transcript_events": "deleted_with_session_record",
                        "suggestion_cards": "deleted_with_session_record",
                        "approach_cards": "deleted_with_session_record",
                        "minutes": "deleted_with_session_record",
                        "audio": "delete_failed",
                        "exports": "not_tracked_by_live_session_repo",
                        "evidence_bundle": "not_tracked_by_live_session_repo",
                    },
                    "errors": [
                        {
                            "scope": "audio",
                            "code": "delete_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )
        await _complete_deletion_facts(v2_deletion_job)
        _log.info("asr.session.deleted", session_id=session_id)
        return {
            "session_id": session_id,
            "deleted": True,
            "session_record_deleted": True,
            "delete_scope": {
                "session_record": "deleted",
                "transcript_events": "deleted_with_session_record",
                "suggestion_cards": "deleted_with_session_record",
                "approach_cards": "deleted_with_session_record",
                "minutes": "deleted_with_session_record",
                "audio": audio_delete_status,
                "exports": "not_tracked_by_live_session_repo",
                "evidence_bundle": "not_tracked_by_live_session_repo",
            },
        }

    @app.get("/live/asr/sessions/{session_id}/draft")
    def get_asr_live_session_draft(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return build_asr_live_draft_review(record)

    @app.get("/live/asr/sessions/{session_id}/draft.md")
    def get_asr_live_session_draft_markdown(session_id: str) -> Response:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        review = build_asr_live_draft_review(record)
        return Response(
            content=render_asr_live_draft_markdown(review),
            media_type="text/markdown; charset=utf-8",
        )

    @app.patch("/sessions/{session_id}/cards/{card_id}/status")
    def update_card_status(
        session_id: str,
        card_id: str,
        payload: UpdateCardStatusRequest,
    ) -> dict[str, Any]:
        try:
            return repo.set_card_status(session_id, card_id, payload.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except CardStatusTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}/report.md")
    def get_markdown_report(session_id: str) -> Response:
        try:
            snapshot = repo.snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return Response(
            content=build_markdown_report(snapshot),
            media_type="text/markdown; charset=utf-8",
        )

    @app.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        try:
            try:
                asr_live_record = asr_live_repo.get(session_id)
            except KeyError:
                asr_live_record = None
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            _log.error(
                "session.read_live_record_before_delete_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": False,
                    "live_session_record_deleted": False,
                    "delete_scope": {
                        "session_record": "retained_not_attempted",
                        "live_session_record": "read_failed",
                        "audio": "retained_not_attempted",
                    },
                    "errors": [
                        {
                            "scope": "live_session_record",
                            "code": "read_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )

        try:
            session_known = bool(asr_live_record) or repo.exists(session_id)
            if persistence_coordinator is not None:
                session_known = session_known or bool(persistence_coordinator.pending_audio_cleanup(session_id))
            if v2_persistence is not None:
                session_known = session_known or v2_persistence.meeting_exists(session_id)
            session_known = session_known or bool(active_v2_capture_tasks.get(session_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "session_delete_preflight_failed",
                    "error_class": type(exc).__name__,
                },
            ) from exc
        v2_deletion_job = await _begin_deletion_fence(session_id) if session_known else None

        if persistence_coordinator is not None:
            try:
                outcome = persistence_coordinator.delete_session_bundle(session_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=f"session not found: {session_id}") from exc
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except (OSError, sqlite3.Error) as exc:
                _log.error(
                    "session.atomic_bundle_delete_failed",
                    session_id=session_id,
                    error_type=type(exc).__name__,
                )
                return JSONResponse(
                    status_code=500,
                    content={
                        "session_id": session_id,
                        "deleted": False,
                        "session_record_deleted": False,
                        "live_session_record_deleted": False,
                        "delete_scope": {
                            "session_record": "retained_after_rollback",
                            "live_session_record": "retained_after_rollback",
                            "audio": "retained_not_attempted",
                        },
                        "errors": [
                            {
                                "scope": "session_bundle",
                                "code": "delete_failed",
                                "error_type": type(exc).__name__,
                            }
                        ],
                    },
                )

            audio_cleanup = outcome.get("audio_cleanup")
            if audio_cleanup is not None:
                try:
                    _delete_audio_asset_locked(
                        session_id,
                        dict(audio_cleanup),
                    )
                except (OSError, ValueError) as exc:
                    _log.error(
                        "session.delete_audio_pending_retry",
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    return JSONResponse(
                        status_code=207,
                        content={
                            "session_id": session_id,
                            "deleted": False,
                            "session_record_deleted": bool(outcome.get("session_record_deleted")),
                            "live_session_record_deleted": bool(outcome.get("live_session_record_deleted")),
                            "audio_cleanup_pending": True,
                            "delete_scope": {
                                "session_record": "deleted",
                                "live_session_record": "deleted",
                                "audio": "cleanup_pending",
                            },
                            "errors": [
                                {
                                    "scope": "audio",
                                    "code": "cleanup_pending",
                                    "error_type": type(exc).__name__,
                                }
                            ],
                        },
                    )
                try:
                    persistence_coordinator.clear_pending_audio_cleanup(session_id)
                except (OSError, sqlite3.Error) as exc:
                    _log.error(
                        "session.clear_audio_cleanup_failed",
                        session_id=session_id,
                        error_type=type(exc).__name__,
                    )
                    return JSONResponse(
                        status_code=207,
                        content={
                            "session_id": session_id,
                            "deleted": False,
                            "session_record_deleted": bool(outcome.get("session_record_deleted")),
                            "live_session_record_deleted": bool(outcome.get("live_session_record_deleted")),
                            "audio_cleanup_pending": True,
                            "delete_scope": {
                                "session_record": "deleted",
                                "live_session_record": "deleted",
                                "audio": "cleanup_pending",
                            },
                            "errors": [
                                {
                                    "scope": "audio_cleanup_job",
                                    "code": "clear_failed",
                                    "error_type": type(exc).__name__,
                                }
                            ],
                        },
                    )
            await _complete_deletion_facts(v2_deletion_job)
            return Response(status_code=204)

        try:
            session_deleted = repo.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            _log.error(
                "session.delete_record_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": False,
                    "live_session_record_deleted": False,
                    "delete_scope": {
                        "session_record": "delete_failed",
                        "live_session_record": "retained_not_attempted",
                        "audio": "retained_not_attempted",
                    },
                    "errors": [
                        {
                            "scope": "session_record",
                            "code": "delete_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )
        try:
            asr_live_deleted = asr_live_repo.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            _log.error(
                "session.delete_live_record_failed",
                session_id=session_id,
                error_type=type(exc).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "session_id": session_id,
                    "deleted": False,
                    "session_record_deleted": session_deleted,
                    "live_session_record_deleted": False,
                    "delete_scope": {
                        "session_record": "deleted" if session_deleted else "not_present",
                        "live_session_record": "delete_failed",
                        "audio": "retained_not_attempted",
                    },
                    "errors": [
                        {
                            "scope": "live_session_record",
                            "code": "delete_failed",
                            "error_type": type(exc).__name__,
                        }
                    ],
                },
            )
        deleted = session_deleted or asr_live_deleted
        if not deleted:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        if asr_live_record is not None:
            try:
                _delete_audio_asset_locked(
                    session_id,
                    dict(asr_live_record.get("audio") or {}),
                )
            except (OSError, ValueError) as exc:
                _log.error(
                    "session.delete_audio_failed",
                    session_id=session_id,
                    error_type=type(exc).__name__,
                )
                return JSONResponse(
                    status_code=207,
                    content={
                        "session_id": session_id,
                        "deleted": False,
                        "session_record_deleted": session_deleted,
                        "live_session_record_deleted": asr_live_deleted,
                        "delete_scope": {
                            "session_record": "deleted" if session_deleted else "not_present",
                            "live_session_record": "deleted" if asr_live_deleted else "not_present",
                            "audio": "delete_failed",
                        },
                        "errors": [
                            {
                                "scope": "audio",
                                "code": "delete_failed",
                                "error_type": type(exc).__name__,
                            }
                        ],
                    },
                )
        await _complete_deletion_facts(v2_deletion_job)
        return Response(status_code=204)

    def _mark_v2_job_stage(
        job: dict[str, Any],
        stage: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        pipeline_traces.observe(
            str(job["id"]),
            stage,
            meeting_id=str(job["meeting_id"]),
            job_id=str(job["id"]),
            generation_id=str(job.get("generation_id") or "") or None,
            attributes=attributes,
        )

    def _bounded_v2_correction_job_output(
        job: dict[str, Any],
        result: dict[str, Any],
        reconciliation: dict[str, Any],
    ) -> dict[str, Any]:
        gate = dict(result.get("gate") or {})
        status = dict(result.get("status") or {})
        gate_segment_count = len(gate.get("segment_ids") or gate.get("final_events") or [])
        return {
            "schema_version": "v2_correction_job_output.v1",
            "session_id": str(result.get("session_id") or job["meeting_id"]),
            "called": bool(result.get("called")),
            "revision_count": int(result.get("revision_count") or 0),
            "provider_revision_count": len(result.get("transcript_revisions") or []),
            "no_revision_segment_count": len(result.get("no_revision_segment_ids") or []),
            "gate": {
                "eligible": bool(gate.get("eligible")),
                "reason": str(gate.get("reason") or "unknown"),
                "segment_count": gate_segment_count,
                "total_chars": int(gate.get("total_chars") or 0),
                "elapsed_ms": int(gate.get("elapsed_ms") or 0),
                "retry_after_ms": int(gate.get("retry_after_ms") or 0),
                "oversized_segment_count": len(gate.get("oversized_segment_ids") or []),
                "policy_version": str(gate.get("policy_version") or realtime_transcript_correction.POLICY_VERSION),
            },
            "status": {
                "status": str(status.get("status") or "unknown"),
                "policy_version": str(status.get("policy_version") or realtime_transcript_correction.POLICY_VERSION),
                "processed_segment_count": len(status.get("processed_segment_ids") or []),
                "revised_segment_count": len(status.get("revised_segment_ids") or []),
                "skipped_segment_count": len(status.get("skipped_segment_ids") or []),
                "terminal_failed_segment_count": len(status.get("terminal_failed_segment_ids") or []),
            },
            "v2_reconciliation": reconciliation,
        }

    async def _default_v2_intelligence_job_handler(job: dict[str, Any]) -> dict[str, Any]:
        """Run the single LLM-first semantic lane for the packaged runtime."""

        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise ProviderRuntimeNotConfiguredDeferred()
        config = llm_service.realtime_config(config)
        _ensure_llm_provider_allowed_for_derivation(
            config,
            allow_non_acceptance_execution=False,
        )
        meeting_id = str(job["meeting_id"])
        all_segments = _v2_complete_transcript(v2_persistence, meeting_id)
        target_id = str(job.get("evidence_segment_id") or "")
        target = next(
            (segment for segment in all_segments if str(segment.get("segment_id") or "") == target_id),
            None,
        )
        if target is None:
            raise RuntimeError("intelligence evidence segment no longer exists")
        semantic_paragraphs = v2_persistence.list_semantic_paragraphs(meeting_id).get("paragraphs") or []
        target_paragraph = next(
            (paragraph for paragraph in semantic_paragraphs if target_id in set(paragraph.get("checkpoint_ids") or [])),
            None,
        )
        if target_paragraph is not None:
            paragraph_ids = set(target_paragraph.get("checkpoint_ids") or [])
            new_segments = [
                segment for segment in all_segments if str(segment.get("segment_id") or "") in paragraph_ids
            ]
            first_paragraph_seq = (
                min(int(segment.get("transcript_seq") or 0) for segment in new_segments)
                if new_segments
                else int(target.get("transcript_seq") or 0)
            )
            context = [
                segment for segment in all_segments if int(segment.get("transcript_seq") or 0) < first_paragraph_seq
            ][-3:]
        else:
            new_segments = [target]
            context = [
                segment
                for segment in all_segments
                if int(segment.get("transcript_seq") or 0) < int(target.get("transcript_seq") or 0)
            ][-3:]
        snapshot = v2_persistence.get_snapshot(meeting_id, segment_limit=100)
        topic = snapshot.get("current_topic") or {}
        rolling_state = {
            "topic": {
                "title": str(topic.get("text") or ""),
                "summary": str((topic.get("evidence") or {}).get("summary") or ""),
            }
            if topic
            else None,
            "open_items": [
                {
                    "id": str(item.get("id") or ""),
                    "type": kind,
                    "text": str(item.get("text") or ""),
                    "status": str(item.get("status") or ""),
                }
                for kind, items in (
                    ("decision", snapshot.get("decision_candidates") or []),
                    ("action_item", snapshot.get("action_items") or []),
                    ("risk", snapshot.get("risks") or []),
                    ("open_question", snapshot.get("open_questions") or []),
                )
                for item in items[-24:]
            ],
            "version": int(job.get("input_version") or 1),
        }
        preparation = meeting_preparation_store.get(meeting_id) if meeting_preparation_store is not None else None
        request = RealtimeIntelligenceRequest.from_payload(
            meeting_id=meeting_id,
            state_revision=int(job.get("input_version") or 1),
            new_paragraphs=[
                {
                    "id": str(segment.get("segment_id") or ""),
                    "text": str(segment.get("normalized_text") or segment.get("text") or ""),
                    "revision": int(segment.get("revision") or 1),
                    "start_ms": segment.get("started_at_ms"),
                    "end_ms": segment.get("ended_at_ms"),
                    "speaker": segment.get("speaker"),
                }
                for segment in new_segments
            ],
            context_paragraphs=[
                {
                    "id": str(segment.get("segment_id") or ""),
                    "text": str(segment.get("normalized_text") or segment.get("text") or ""),
                    "revision": int(segment.get("revision") or 1),
                    "start_ms": segment.get("started_at_ms"),
                    "end_ms": segment.get("ended_at_ms"),
                    "speaker": segment.get("speaker"),
                }
                for segment in context
            ],
            rolling_state=rolling_state,
            glossary=list(preparation.hotwords) if preparation is not None else [],
        )
        provider = OpenAICompatibleStreamingProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            client=app.state.streaming_llm_client,
            timeout_seconds=min(config.timeout_seconds, 25.0),
            api_style=config.api_style,
        )

        def before_intelligence_attempt(_attempt: int) -> None:
            _enforce_llm_budget(
                meeting_id,
                purpose="realtime_intelligence",
                config=config,
            )

        def record_intelligence_attempt_usage(usage: dict[str, Any] | None, _attempt: int) -> None:
            _record_llm_usage(
                meeting_id,
                purpose="realtime_intelligence",
                config=config,
                usage=usage,
            )

        result = await run_realtime_intelligence(
            request=request,
            provider=provider,
            before_attempt=before_intelligence_attempt,
            on_usage=record_intelligence_attempt_usage,
        )
        formal_event_context = build_llm_first_event_context(
            request=request,
            response=result["response"],
            job_id=str(job["id"]),
            batch_id=realtime_intelligence_batch_id(request),
            provider=llm_service.provider_identifier(config),
            model=str(result.get("model") or config.model),
            evidence_hash=str(target.get("evidence_hash") or "") or None,
        )
        app.state.llm_first_event_contexts[str(job["id"])] = formal_event_context
        for stage, field in (
            ("provider_connected", "connected_at"),
            ("first_token", "first_token_at"),
            ("provider_completed", "completed_at"),
        ):
            timestamp = (result.get("timings") or {}).get(field)
            if isinstance(timestamp, (int, float)):
                pipeline_traces.observe(
                    str(job["id"]),
                    stage,
                    meeting_id=meeting_id,
                    job_id=str(job["id"]),
                    generation_id=str(job.get("generation_id") or "") or None,
                    monotonic_ns=int(float(timestamp) * 1_000_000_000),
                )
        try:
            applied = await asyncio.to_thread(
                v2_persistence.apply_intelligence_response,
                meeting_id=meeting_id,
                job_id=str(job["id"]),
                response=result["response"].to_dict(),
                now_ms=time.time_ns() // 1_000_000,
            )
        except Exception as exc:
            _log.error(
                "meeting.v2.intelligence_projection_failed",
                meeting_id=meeting_id,
                job_id=str(job["id"]),
                error_class=type(exc).__name__,
                error_detail=str(exc),
            )
            raise
        _mark_v2_job_stage(
            job,
            "validated",
            attributes={
                "revision_count": applied["revision_count"],
                "state_change_count": applied["state_change_count"],
                "ttft_ms": result["ttft_ms"],
            },
        )
        _mark_v2_job_stage(
            job,
            "event_emitted",
            attributes={"state_change_count": applied["state_change_count"]},
        )
        return {
            "schema_version": "v2_realtime_intelligence_job_output.v1",
            "applied": applied,
            "formal_event_context": formal_event_context,
            "transport_mode": result["transport_mode"],
            "ttft_ms": result["ttft_ms"],
            "repair_ttft_ms": result.get("repair_ttft_ms"),
            "provider_attempt_count": result.get("provider_attempt_count", 1),
            "repair_attempted": result.get("repair_attempted", False),
            "usage": result.get("usage"),
            "model": result.get("model"),
        }

    def _default_v2_correction_job_handler(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is not None and v2_persistence.semantic_projection_mode == "llm_first":
            return {
                "schema_version": "v2_correction_job_output.v2",
                "skipped": True,
                "reason": "llm_first_intelligence_lane",
            }
        if llm_service.LlmConfig.from_env() is None:
            raise ProviderRuntimeNotConfiguredDeferred()
        force = str(job.get("idempotency_key") or "").endswith("meeting.ended") or int(job.get("attempts") or 0) > 1
        try:
            result = app.state.run_asr_live_session_realtime_corrections_once(
                str(job["meeting_id"]),
                RunRealtimeCorrectionsRequest(force=force),
            )
        except HTTPException as exc:
            if exc.status_code in {422, 503} and llm_service.LlmConfig.from_env() is None:
                raise ProviderRuntimeNotConfiguredDeferred() from None
            raise
        persisted_revisions = [
            dict(event)
            for event in (_get_asr_live_record_for_derivation(str(job["meeting_id"])).get("events") or [])
            if event.get("event_type") == "transcript_revision"
        ]
        reconciliation = (
            _commit_v2_transcript_revisions(
                v2_persistence,
                meeting_id=str(job["meeting_id"]),
                causation_job_id=str(job["id"]),
                revisions=[
                    *persisted_revisions,
                    *[dict(item) for item in result.get("transcript_revisions") or []],
                ],
                max_input_transcript_seq=int(job["input_transcript_seq"]),
                now_ms=time.time_ns() // 1_000_000,
            )
            if v2_persistence is not None
            else {"revision_count": 0, "event_count": 0, "segment_ids": []}
        )
        gate = dict(result.get("gate") or {})
        satisfied_segment_ids = sorted(
            set(reconciliation["segment_ids"]).union(
                str(segment_id).strip()
                for segment_id in result.get("no_revision_segment_ids") or []
                if str(segment_id).strip()
            )
        )
        superseded_job_count = (
            v2_persistence.supersede_correction_jobs(
                meeting_id=str(job["meeting_id"]),
                segment_ids=satisfied_segment_ids,
                except_job_id=str(job["id"]),
                now_ms=time.time_ns() // 1_000_000,
            )
            if v2_persistence is not None
            else 0
        )
        reconciliation = {
            **reconciliation,
            "superseded_job_count": superseded_job_count,
        }
        pending_gate_segment_ids = set(gate.get("segment_ids") or []).difference(satisfied_segment_ids)
        if not result.get("called") and gate.get("reason") == "batch_gate_closed" and pending_gate_segment_ids:
            elapsed_ms = max(0, int(gate.get("elapsed_ms") or 0))
            retry_after_ms = max(
                250,
                realtime_transcript_correction.MIN_INTERVAL_MS - elapsed_ms,
            )
            _log.info(
                "meeting.v2.correction_deferred",
                session_id=str(job["meeting_id"]),
                job_id=str(job["id"]),
                retry_after_ms=retry_after_ms,
            )
            raise CorrectionBatchDeferred(retry_after_ms)
        _mark_v2_job_stage(
            job,
            "validated",
            attributes={
                "provider_revision_count": len(result.get("transcript_revisions") or []),
                "reconciled_revision_count": reconciliation["revision_count"],
            },
        )
        if reconciliation["event_count"]:
            _mark_v2_job_stage(
                job,
                "event_emitted",
                attributes={"event_count": reconciliation["event_count"]},
            )
        return _bounded_v2_correction_job_output(job, result, reconciliation)

    def _preview_segment_ids(preview: dict[str, Any]) -> set[str]:
        segment_ids = {
            str(span.get("segment_id") or "").strip()
            for span in preview.get("evidence_spans") or []
            if isinstance(span, dict)
        }
        segment_ids.update(str(segment_id).strip() for segment_id in preview.get("segment_batch") or [])
        segment_ids.discard("")
        return segment_ids

    async def _wait_for_blocked_quality_correction(
        job: dict[str, Any],
        *,
        timeout_seconds: float = 35.0,
    ) -> str:
        if v2_persistence is None:
            return "ready"
        record = _get_asr_live_record_for_derivation(str(job["meeting_id"]))
        if not _has_recoverable_semantic_quality_failure(record):
            return "ready"

        deadline = time.monotonic() + timeout_seconds
        while True:
            current_job = v2_persistence.get_job(str(job["id"]))
            if current_job["status"] != "running":
                return "evidence_superseded_before_generation"
            correction_jobs = v2_persistence.list_jobs(
                meeting_id=str(job["meeting_id"]),
                lane="correction",
            )
            dependency_active = any(
                correction_job["evidence_segment_id"] == job["evidence_segment_id"]
                and correction_job["input_transcript_seq"] == job["input_transcript_seq"]
                and correction_job["status"] in {"pending", "running", "retry_wait"}
                for correction_job in correction_jobs
            )
            if not dependency_active:
                return "ready"
            if time.monotonic() >= deadline:
                return "correction_dependency_timeout"
            await asyncio.sleep(0.05)

    async def _default_v2_suggestion_job_handler(
        job: dict[str, Any],
    ) -> dict[str, Any]:
        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        if v2_persistence.semantic_projection_mode == "llm_first":
            return {
                "generated_card_count": 0,
                "reason": "llm_first_intelligence_lane",
            }
        dependency_status = await _wait_for_blocked_quality_correction(job)
        if dependency_status != "ready":
            return {
                "generated_card_count": 0,
                "reason": dependency_status,
            }
        if llm_service.LlmConfig.from_env() is None:
            raise ProviderRuntimeNotConfiguredDeferred()
        session_id = str(job["meeting_id"])
        lane_lease = llm_lane_locks.try_acquire(session_id, "suggestion")
        if lane_lease is None:
            raise RuntimeError("suggestion lane is already in flight")
        claim_id = f"durable_claim_{job['id']}"
        claimed: dict[str, Any] = {}
        try:
            record = _get_asr_live_record_for_derivation(session_id)
            runtime_policy = _current_auto_suggestion_runtime_policy()
            record, _status = _persist_auto_suggestion_runtime_policy(
                session_id,
                runtime_policy,
            )
            if not runtime_policy["enabled"]:
                return {"generated_card_count": 0, "reason": "disabled_by_setting"}

            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise ProviderRuntimeNotConfiguredDeferred()
            config = llm_service.realtime_config(config)
            _ensure_llm_provider_allowed_for_derivation(
                config,
                allow_non_acceptance_execution=False,
            )
            _enforce_llm_budget(session_id, purpose="auto_suggestion", config=config)
            evidence_segment_id = str(job["evidence_segment_id"])
            previews = [
                preview
                for preview in _execution_previews_from_record(record)
                if evidence_segment_id in _preview_segment_ids(preview)
            ]
            if not previews:
                return {"generated_card_count": 0, "reason": "no_candidate_for_final"}

            def claim_latest(latest: dict[str, Any]) -> dict[str, Any]:
                claimed_record, _claimed_status, claim = auto_suggestion_orchestrator.claim_candidate(
                    latest,
                    previews=previews,
                    config=config,
                    acceptance_blockers=_enabled_llm_execution_blockers(latest),
                    claim_id=claim_id,
                    now_ms=time.time_ns() // 1_000_000,
                )
                if claim is not None:
                    claimed.update(claim)
                return claimed_record

            record = asr_live_repo.update(session_id, claim_latest)
            if not claimed:
                status = auto_suggestion_orchestrator.status_from_record(record)
                return {
                    "generated_card_count": 0,
                    "reason": str(status.get("last_suppression_reason") or "no_candidate"),
                }

            preview = dict(claimed["preview"])
            evidence_context = str(preview.get("evidence_context") or preview.get("input_summary") or "").strip()
            messages = build_realtime_suggestion_messages(
                gap=preview.get("suggested_prompt") or preview.get("input_summary"),
                evidence=evidence_context,
            )
            streaming_client = getattr(app.state, "streaming_llm_client", None)
            if streaming_client is None:
                raise RuntimeError("streaming LLM client is not started")
            provider = OpenAICompatibleStreamingProvider(
                base_url=config.base_url,
                api_key=config.api_key,
                model=config.model,
                client=streaming_client,
                timeout_seconds=min(config.timeout_seconds, 25.0),
                api_style=config.api_style,
            )
            try:
                result = await generate_streaming_suggestion(
                    job=job,
                    messages=messages,
                    provider=provider,
                    persistence=v2_persistence,
                )
            except BaseException as exc:
                error_class = type(exc).__name__

                def release_failed_claim(latest: dict[str, Any]) -> dict[str, Any]:
                    status = auto_suggestion_orchestrator.status_from_record(latest)
                    reservations = dict(status.get("candidate_reservations") or {})
                    candidate_id = str(claimed.get("candidate_id") or "")
                    reservation = dict(reservations.get(candidate_id) or {})
                    if str(reservation.get("claim_id") or "") == claim_id:
                        reservation.update(
                            {
                                "status": "retry_pending",
                                "completed_at_ms": time.time_ns() // 1_000_000,
                                "error_class": error_class,
                            }
                        )
                        reservations[candidate_id] = reservation
                        status["candidate_reservations"] = reservations
                        status["status"] = "provider_error"
                    return {
                        **latest,
                        "auto_suggestion": auto_suggestion_orchestrator.persistable_status(status),
                    }

                asr_live_repo.update(session_id, release_failed_claim)
                raise

            timings = dict(result.get("timings") or {})
            for stage, field in (
                ("provider_connected", "connected_at"),
                ("first_token", "first_token_at"),
                ("provider_completed", "completed_at"),
            ):
                timestamp = timings.get(field)
                if isinstance(timestamp, (int, float)):
                    pipeline_traces.observe(
                        str(job["id"]),
                        stage,
                        meeting_id=session_id,
                        job_id=str(job["id"]),
                        generation_id=str(job.get("generation_id") or "") or None,
                        monotonic_ns=int(float(timestamp) * 1_000_000_000),
                    )

            suggestion = dict(result["suggestion"])
            suggestion_text = str(suggestion["text"])
            candidate_id = str(claimed["candidate_id"])
            card = {
                "card_id": suggestion["suggestion_id"],
                "card_status": "new",
                "schema_name": "SuggestionCardV1",
                "gap_rule_id": preview.get("gap_rule_id"),
                "target_type": preview.get("target_type"),
                "target_id": preview.get("target_id"),
                "suggestion_text": suggestion_text,
                "confidence": float(preview.get("candidate_confidence") or 0.8),
                "trigger_reason": str(preview.get("suggested_prompt") or ""),
                "evidence_span_ids": list(preview.get("evidence_span_ids") or []),
                "evidence_spans": list(preview.get("evidence_spans") or []),
                "evidence_context": evidence_context,
                "source_event_ids": list(preview.get("source_event_ids") or []),
                "llm_trace": {
                    "provider": llm_service.provider_identifier(config),
                    "model": config.model,
                    "transport_mode": result["transport_mode"],
                    "ttft_ms": result["ttft_ms"],
                    "usage": dict(result.get("usage") or {}),
                },
            }

            def commit_legacy_projection(latest: dict[str, Any]) -> dict[str, Any]:
                status = auto_suggestion_orchestrator.status_from_record(latest)
                reservations = dict(status.get("candidate_reservations") or {})
                reservation = dict(reservations.get(candidate_id) or {})
                if str(reservation.get("claim_id") or "") != claim_id:
                    raise RuntimeError("suggestion reservation changed before projection commit")
                now_ms = time.time_ns() // 1_000_000
                reservation.update({"status": "completed", "completed_at_ms": now_ms})
                reservations[candidate_id] = reservation
                status["candidate_reservations"] = reservations
                status["processed_candidate_ids"] = auto_suggestion_orchestrator.order_preserving_union(
                    list(status.get("processed_candidate_ids") or []),
                    [candidate_id],
                )
                status["call_timestamps_ms"] = [
                    *list(status.get("call_timestamps_ms") or []),
                    now_ms,
                ]
                status["status"] = "running"
                status["last_triggered_at_ms"] = now_ms
                status["last_successful_card_at_ms"] = now_ms
                cards = list(latest.get("suggestion_cards") or [])
                if suggestion["suggestion_id"] not in {str(existing.get("card_id") or "") for existing in cards}:
                    cards.append(card)
                return {
                    **latest,
                    "suggestion_cards": cards,
                    "auto_suggestion": auto_suggestion_orchestrator.persistable_status(status),
                }

            asr_live_repo.update(session_id, commit_legacy_projection)
            _record_llm_usage(
                session_id,
                purpose="auto_suggestion",
                config=config,
                usage=dict(result.get("usage") or {}),
            )
            _mark_v2_job_stage(job, "validated", attributes={"card_count": 1})
            _mark_v2_job_stage(
                job,
                "event_emitted",
                attributes={"suggestion_id": suggestion["suggestion_id"]},
            )
            return {**result, "generated_card_count": 1, "card": card}
        finally:
            lane_lease.release()

    app.state.v2_intelligence_job_handler_impl = _default_v2_intelligence_job_handler
    app.state.v2_correction_job_handler_impl = _default_v2_correction_job_handler
    app.state.v2_suggestion_job_handler_impl = _default_v2_suggestion_job_handler

    def _wait_for_v2_correction_jobs(
        meeting_id: str,
        *,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        if v2_persistence is None:
            return {"correction_degraded": True, "reason": "v2_persistence_unavailable"}
        lane = "intelligence" if v2_persistence.semantic_projection_mode == "llm_first" else "correction"
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            correction_jobs = v2_persistence.list_jobs(
                meeting_id=meeting_id,
                lane=lane,
            )
            active = [item for item in correction_jobs if item["status"] in {"pending", "running", "retry_wait"}]
            if active and all(
                item["status"] == "retry_wait" and item.get("error_class") == "ProviderRuntimeNotConfiguredDeferred"
                for item in active
            ):
                return {
                    "correction_degraded": True,
                    "reason": f"{lane}_provider_not_configured",
                    "lane": lane,
                    "job_count": len(correction_jobs),
                }
            if not active:
                succeeded = [item for item in correction_jobs if item["status"] == "succeeded"]
                degraded = not correction_jobs or not succeeded
                reason = (
                    f"{lane}_jobs_missing"
                    if not correction_jobs
                    else f"{lane}_has_no_successful_job"
                    if not succeeded
                    else None
                )
                if degraded:
                    _log.warning(
                        "meeting.post_job.realtime_quality_degraded",
                        meeting_id=meeting_id,
                        lane=lane,
                        reason=reason,
                    )
                return {
                    "correction_degraded": degraded,
                    "reason": reason,
                    "lane": lane,
                    "job_count": len(correction_jobs),
                }
            time.sleep(0.1)
        _log.warning(
            "meeting.post_job.realtime_quality_wait_timeout",
            meeting_id=meeting_id,
            lane=lane,
            timeout_seconds=timeout_seconds,
        )
        return {
            "correction_degraded": True,
            "reason": f"{lane}_wait_timeout",
            "lane": lane,
        }

    app.state.wait_for_v2_correction_jobs = _wait_for_v2_correction_jobs

    def _run_v2_minutes_job(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        quality_gate = _wait_for_v2_correction_jobs(str(job["meeting_id"]))
        try:
            result = _asr_live_session_minutes_response(
                str(job["meeting_id"]),
                CreateLlmExecutionRunsRequest(mode="enabled"),
                allow_non_acceptance_execution=False,
                execution_boundary="durable_post_meeting_job",
            )
        except HTTPException as exc:
            if exc.status_code in {422, 503} and llm_service.LlmConfig.from_env() is None:
                raise ProviderRuntimeNotConfiguredDeferred() from None
            raise
        markdown = str(result.get("minutes_md") or "").strip()
        if not markdown:
            raise RuntimeError("minutes job returned empty markdown")
        artifact = v2_persistence.save_minutes(
            meeting_id=str(job["meeting_id"]),
            job_id=str(job["id"]),
            markdown=markdown,
            structured=(dict(result["minutes"]) if isinstance(result.get("minutes"), dict) else None),
            degraded=bool(result.get("degraded")) or bool(quality_gate["correction_degraded"]),
            now_ms=time.time_ns() // 1_000_000,
        )
        return {
            **result,
            "correction_degraded": bool(quality_gate["correction_degraded"]),
            "correction_degraded_reason": quality_gate.get("reason"),
            "v2_minutes": artifact,
        }

    def _run_v2_approach_job(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        quality_gate = _wait_for_v2_correction_jobs(str(job["meeting_id"]))
        try:
            result = _asr_live_session_approach_cards_response(
                str(job["meeting_id"]),
                CreateLlmExecutionRunsRequest(mode="enabled"),
                allow_non_acceptance_execution=False,
                execution_boundary="durable_post_meeting_job",
            )
        except HTTPException as exc:
            if exc.status_code in {422, 503} and llm_service.LlmConfig.from_env() is None:
                raise ProviderRuntimeNotConfiguredDeferred() from None
            raise
        artifact = v2_persistence.save_approach_cards(
            meeting_id=str(job["meeting_id"]),
            job_id=str(job["id"]),
            cards=[dict(card) for card in result.get("approach_cards") or []],
            degraded=bool(result.get("degraded")) or bool(quality_gate["correction_degraded"]),
            now_ms=time.time_ns() // 1_000_000,
        )
        return {
            **result,
            "correction_degraded": bool(quality_gate["correction_degraded"]),
            "correction_degraded_reason": quality_gate.get("reason"),
            "v2_approach": artifact,
        }

    def _run_v2_index_job(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        quality_gate = _wait_for_v2_correction_jobs(str(job["meeting_id"]))
        indexed = v2_persistence.rebuild_search_document(
            meeting_id=str(job["meeting_id"]),
            job_id=str(job["id"]),
            now_ms=time.time_ns() // 1_000_000,
        )
        return {
            **indexed,
            "correction_degraded": bool(quality_gate["correction_degraded"]),
            "correction_degraded_reason": quality_gate.get("reason"),
        }

    app.state.v2_post_job_handler_impls = {
        "minutes": _run_v2_minutes_job,
        "approach": _run_v2_approach_job,
        "index": _run_v2_index_job,
    }

    def _dispatch_v2_post_job(lane: str, job: dict[str, Any]) -> Any:
        return app.state.v2_post_job_handler_impls[lane](job)

    def _execute_v2_deletion_job(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is None:
            raise RuntimeError("V2 persistence is unavailable")
        running = v2_persistence.mark_deletion_running(
            job_id=str(job["id"]),
            now_ms=time.time_ns() // 1_000_000,
        )
        try:
            meeting_id = str(running["meeting_id"])
            with _recording_asset_lock(meeting_id):
                if data_dir_path is not None:
                    audio_assets.delete_audio_asset(
                        data_dir_path,
                        {"relative_path": f"audio_assets/{meeting_id}/audio.wav"},
                    )
                try:
                    asr_live_repo.delete(meeting_id)
                except KeyError:
                    pass
                try:
                    repo.delete(meeting_id)
                except KeyError:
                    pass
                if meeting_preparation_store is not None:
                    meeting_preparation_store.delete(meeting_id)
                asr_stream.clear_session_hotwords(meeting_id)
                return v2_persistence.complete_deletion_and_purge(
                    job_id=str(job["id"]),
                    now_ms=time.time_ns() // 1_000_000,
                )
        except BaseException as exc:
            v2_persistence.fail_deletion_job(
                job_id=str(job["id"]),
                error_class=type(exc).__name__,
                now_ms=time.time_ns() // 1_000_000,
            )
            raise

    app.state.execute_v2_deletion_job = _execute_v2_deletion_job

    def _run_recording_export(job: dict[str, Any]) -> dict[str, Any]:
        if v2_persistence is None or data_dir_path is None:
            raise RuntimeError("recording export storage is unavailable")
        recording = v2_persistence.get_recording_session(
            str(job["meeting_id"]),
            track=str(job["track"]),
            epoch=int(job["epoch"]),
        )
        output_relative_path = str(job["output_relative_path"])
        uses_track_layout = "/tracks/" in f"/{output_relative_path}"
        with _recording_asset_lock(str(job["meeting_id"])):
            metadata = audio_assets.assemble_realtime_wav_asset(
                data_dir=data_dir_path,
                session_id=str(job["meeting_id"]),
                source_type=str(recording["source_type"]),
                sample_rate_hz=int(recording["sample_rate_hz"]),
                expected_chunk_count=int(job["input_chunk_count"]),
                expected_sample_count=int(job["input_sample_count"]),
                expected_journal_sha256=str(job["input_journal_sha256"]),
                track_id=str(job["track"]) if uses_track_layout else None,
                epoch=int(job["epoch"]),
                started_at_ms=int(recording["started_at_ms"]),
            )
        try:
            asr_live_repo.update(
                str(job["meeting_id"]),
                lambda existing: {**existing, "audio": metadata},
            )
        except KeyError:
            pass
        return metadata

    recording_export_executor: RecordingExportExecutor | None = None
    if v2_persistence is not None and data_dir_path is not None:
        recording_export_executor = RecordingExportExecutor(
            v2_persistence,
            export_handler=_run_recording_export,
            capture_recovery_handler=lambda now_ms: reconcile_and_recover_expired_recordings(
                v2_persistence,
                data_dir=data_dir_path,
                now_ms=now_ms,
            ),
        )
    app.state.recording_export_executor = recording_export_executor

    def _run_v2_correction_job(job: dict[str, Any]) -> dict[str, Any]:
        _mark_v2_job_stage(job, "job_claimed", attributes={"lane": "correction"})
        return app.state.v2_correction_job_handler_impl(job)

    async def _run_v2_intelligence_job(job: dict[str, Any]) -> dict[str, Any]:
        _mark_v2_job_stage(job, "job_claimed", attributes={"lane": "intelligence"})
        return await app.state.v2_intelligence_job_handler_impl(job)

    def _run_v2_suggestion_job(job: dict[str, Any]) -> Any:
        _mark_v2_job_stage(job, "job_claimed", attributes={"lane": "suggestion"})
        return app.state.v2_suggestion_job_handler_impl(job)

    v2_executor: DurableJobExecutor | None = None
    if v2_persistence is not None:
        v2_executor = DurableJobExecutor(
            v2_persistence,
            correction_handler=_run_v2_correction_job,
            suggestion_handler=_run_v2_suggestion_job,
            additional_handlers={
                "intelligence": _run_v2_intelligence_job,
                "minutes": lambda job: _dispatch_v2_post_job("minutes", job),
                "approach": lambda job: _dispatch_v2_post_job("approach", job),
                "index": lambda job: _dispatch_v2_post_job("index", job),
            },
            retry_observer=pipeline_traces.record_retry,
            cancellation_observer=pipeline_traces.record_cancelled,
        )
    app.state.v2_executor = v2_executor
    app.state.streaming_llm_client = None

    async def _start_v2_executor() -> None:
        if app.state.streaming_llm_client is None:
            app.state.streaming_llm_client = httpx.AsyncClient(
                timeout=60.0,
                trust_env=False,
            )
        if v2_executor is not None:
            if recording_export_executor is not None:
                await recording_export_executor.start()
            await v2_executor.start()

    async def _start_data_governance() -> None:
        if data_governance_service is None or v2_persistence is None:
            return
        for deletion_job in v2_persistence.list_deletion_jobs(
            statuses=("pending", "running", "failed"),
        ):
            try:
                await asyncio.to_thread(
                    data_governance_service.execute_deletion_job,
                    job_id=str(deletion_job["id"]),
                    now_ms=time.time_ns() // 1_000_000,
                )
            except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
                _log.error(
                    "v2_deletion_recovery_failed",
                    deletion_job_id=deletion_job["id"],
                    meeting_id=deletion_job["meeting_id"],
                    error_class=type(exc).__name__,
                )
        try:
            retention_result = await asyncio.to_thread(
                data_governance_service.run_retention_if_due,
                now_ms=time.time_ns() // 1_000_000,
            )
            if retention_result.get("claimed"):
                _log.info(
                    "meeting.data_governance.retention_completed",
                    deletion_job_count=len(retention_result.get("deletion_jobs") or []),
                    error_count=len(retention_result.get("errors") or []),
                )
        except (OSError, sqlite3.Error, ValueError, KeyError) as exc:
            _log.error(
                "meeting.data_governance.retention_failed",
                error_class=type(exc).__name__,
            )

    async def _start_recording_import_runtime() -> None:
        if not _recording_import_runtime_ready() or v2_persistence is None:
            _log.warning("meeting.recording_import.runtime_unavailable")
            return
        recovered = await asyncio.to_thread(
            v2_persistence.recover_interrupted_import_jobs,
            now_ms=time.time_ns() // 1_000_000,
        )
        if recovered:
            _log.info("meeting.recording_import.recovered", count=recovered)
        existing = app.state.recording_import_worker_task
        if existing is not None and not existing.done():
            return
        recording_import_stop_event.clear()
        recording_import_wake_event.set()
        app.state.recording_import_worker_task = asyncio.create_task(
            _recording_import_worker(),
            name="recording-import-worker",
        )

    async def _start_desktop_parent_watchdog() -> None:
        parent_pid = desktop_parent_watchdog.configured_parent_pid()
        if parent_pid is None:
            return
        app.state.desktop_parent_watchdog_task = asyncio.create_task(
            desktop_parent_watchdog.monitor_parent(parent_pid),
            name="desktop-parent-watchdog",
        )

    async def _start_funasr_resident_worker() -> None:
        if prewarm_funasr:
            ready = await asyncio.to_thread(asr_stream.prewarm_funasr_resident_manager)
            app.state.funasr_resident_prewarm_ready = ready
            if os.environ.get("MEETING_COPILOT_DESKTOP_RUNTIME") == "1" and not ready:
                raise RuntimeError("packaged desktop FunASR resident worker failed to become ready")

    async def _recover_abandoned_desktop_recordings() -> None:
        if os.environ.get("MEETING_COPILOT_DESKTOP_RUNTIME") != "1" or v2_persistence is None or data_dir_path is None:
            return
        recovered = await asyncio.to_thread(
            reconcile_and_recover_abandoned_recordings,
            v2_persistence,
            data_dir=data_dir_path,
            now_ms=time.time_ns() // 1_000_000,
        )
        if recovered:
            _log.warning(
                "meeting.recording.runtime_restart_recovered",
                count=len(recovered),
            )

    async def _stop_desktop_parent_watchdog() -> None:
        task = app.state.desktop_parent_watchdog_task
        app.state.desktop_parent_watchdog_task = None
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _stop_active_v2_capture_tasks() -> None:
        cancelled_count = await _cancel_active_capture_tasks(active_v2_capture_tasks)
        if cancelled_count:
            _log.info("meeting.capture.shutdown_cancelled", count=cancelled_count)

    async def _stop_recording_import_runtime() -> None:
        task = app.state.recording_import_worker_task
        app.state.recording_import_worker_task = None
        if task is None:
            return
        recording_import_stop_event.set()
        recording_import_wake_event.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _stop_v2_executor() -> None:
        if v2_executor is not None:
            await v2_executor.stop()

    async def _stop_recording_export_executor() -> None:
        if recording_export_executor is not None:
            await recording_export_executor.stop()

    async def _close_streaming_llm_client() -> None:
        client = app.state.streaming_llm_client
        app.state.streaming_llm_client = None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def _checkpoint_realtime_slo() -> None:
        await asyncio.to_thread(
            realtime_slo.checkpoint,
            active_traces=pipeline_traces.slo_snapshots(),
        )

    async def _stop_funasr_resident_worker() -> None:
        await asyncio.to_thread(asr_stream.shutdown_funasr_resident_manager)

    app.router.add_event_handler("startup", _recover_abandoned_desktop_recordings)
    app.router.add_event_handler("startup", _start_funasr_resident_worker)
    app.router.add_event_handler("startup", _start_desktop_parent_watchdog)
    app.router.add_event_handler("startup", _start_data_governance)
    app.router.add_event_handler("startup", _start_v2_executor)
    app.router.add_event_handler("startup", _start_recording_import_runtime)
    app.router.add_event_handler("shutdown", _stop_desktop_parent_watchdog)
    app.router.add_event_handler("shutdown", _stop_active_v2_capture_tasks)
    app.router.add_event_handler("shutdown", _stop_recording_import_runtime)
    app.router.add_event_handler("shutdown", _stop_v2_executor)
    app.router.add_event_handler("shutdown", _stop_recording_export_executor)
    app.router.add_event_handler("shutdown", _checkpoint_realtime_slo)
    app.router.add_event_handler("shutdown", _close_streaming_llm_client)
    app.router.add_event_handler("shutdown", _stop_funasr_resident_worker)
    app.router.add_event_handler("shutdown", _close_created_sqlite_repositories)

    return app


def _replay_evaluation(
    snapshot: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_gap_rule_count = int(
        (metadata or {}).get(
            "expected_gap_rule_count",
            2 if snapshot.get("quality", {}).get("is_engineering_meeting", False) else 0,
        )
    )
    return evaluate_demo_snapshot(
        snapshot,
        expected_gap_rule_count=expected_gap_rule_count,
        source="replay_snapshot",
    )


def _live_evaluation(
    snapshot: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected_gap_rule_count = int(
        (metadata or {}).get(
            "expected_gap_rule_count",
            2 if snapshot.get("quality", {}).get("is_engineering_meeting", False) else 0,
        )
    )
    return evaluate_demo_snapshot(
        snapshot,
        expected_gap_rule_count=expected_gap_rule_count,
        source="live_mock_stream",
    )


def _candidate_events_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": int(event.get("sequence", 0)),
            "event_id": str(event.get("id", "")),
            "event_type": str(event.get("event_type", "")),
            "at_ms": int(event.get("at_ms", 0)),
            "payload": dict(event.get("payload") or {}),
        }
        for event in record.get("events") or []
        if event.get("event_type") == "suggestion_candidate_event"
    ]


def _request_draft_events_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": int(event.get("sequence", 0)),
            "event_id": str(event.get("id", "")),
            "event_type": str(event.get("event_type", "")),
            "at_ms": int(event.get("at_ms", 0)),
            "payload": dict(event.get("payload") or {}),
        }
        for event in record.get("events") or []
        if event.get("event_type") == "llm_request_draft_event"
    ]


def _execution_previews_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    session_id = str(record.get("session_id", ""))
    previews: list[dict[str, Any]] = []
    evidence_by_id = _evidence_spans_by_id_from_record(record)
    for event in record.get("events") or []:
        if event.get("event_type") != "llm_request_draft_event":
            continue
        payload = dict(event.get("payload") or {})
        request_id = str(payload.get("request_id", ""))
        evidence_span_ids = list(payload.get("evidence_span_ids") or [])
        evidence_spans = [
            evidence_by_id[evidence_id] for evidence_id in evidence_span_ids if evidence_id in evidence_by_id
        ]
        previews.append(
            {
                "execution_id": f"asr_llm_execution_preview_{request_id}",
                "execution_status": "preview_only",
                "request_id": request_id,
                "request_draft_event_id": str(event.get("id", "")),
                "request_draft_sequence": int(event.get("sequence", 0)),
                "request_type": str(payload.get("request_type", "")),
                "target_candidate_id": str(payload.get("target_candidate_id", "")),
                "target_type": str(payload.get("target_type", "")),
                "target_id": str(payload.get("target_id", "")),
                "gap_rule_id": str(payload.get("gap_rule_id", "")),
                "prompt_version": "suggestion-card-execution-preview.v1",
                "provider": "not_configured",
                "model": "not_called",
                "llm_call_status": "not_called",
                "schema_name": "SuggestionCardV1",
                "schema_status": "not_generated",
                "card_status": "not_created",
                "cost_status": "not_estimated",
                "idempotency_key": (f"live_asr_execution_preview:{session_id}:{request_id}"),
                "source_event_ids": list(payload.get("source_event_ids") or []),
                "evidence_span_ids": evidence_span_ids,
                "evidence_spans": evidence_spans,
                "evidence_context": _render_evidence_context(evidence_spans),
                "segment_batch": list(payload.get("segment_batch") or []),
                "candidate_confidence": payload.get("candidate_confidence"),
                "candidate_confidence_level": payload.get("candidate_confidence_level"),
                "candidate_degradation_reasons": list(payload.get("candidate_degradation_reasons") or []),
                "input_summary": str(payload.get("input_summary", "")),
                "suggested_prompt": str(payload.get("suggested_prompt", "")),
            }
        )
    return previews


def _enabled_execution_candidate_limit(
    requested_max_candidates: int | None = None,
) -> tuple[int, str]:
    if requested_max_candidates is not None:
        return (
            min(
                max(int(requested_max_candidates), 1),
                LLM_EXECUTION_HARD_MAX_CANDIDATES_PER_RUN,
            ),
            "request_max_candidates",
        )
    raw_value = os.environ.get("LLM_EXECUTION_MAX_CANDIDATES_PER_RUN")
    try:
        value = int(raw_value) if raw_value else LLM_EXECUTION_DEFAULT_MAX_CANDIDATES_PER_RUN
    except (TypeError, ValueError):
        value = LLM_EXECUTION_DEFAULT_MAX_CANDIDATES_PER_RUN
    reason = "env_max_candidates" if raw_value else "default_max_candidates"
    return min(max(value, 1), LLM_EXECUTION_HARD_MAX_CANDIDATES_PER_RUN), reason


def _execution_preview_candidate_id(preview: dict[str, Any]) -> str:
    return str(preview.get("target_candidate_id") or preview.get("request_id") or preview.get("execution_id") or "")


def _execution_preview_confidence(preview: dict[str, Any]) -> float:
    try:
        return float(preview.get("candidate_confidence"))
    except (TypeError, ValueError):
        return 0.0


def _execution_preview_rank(
    indexed_preview: tuple[int, dict[str, Any]],
) -> tuple[float, int, int, int]:
    index, preview = indexed_preview
    gap_rule_id = str(preview.get("gap_rule_id") or "")
    try:
        sequence = int(preview.get("request_draft_sequence") or 0)
    except (TypeError, ValueError):
        sequence = 0
    return (
        -_execution_preview_confidence(preview),
        LLM_EXECUTION_GAP_RULE_PRIORITY.get(gap_rule_id, 99),
        sequence,
        index,
    )


def _select_enabled_execution_previews(
    previews: list[dict[str, Any]],
    *,
    max_candidates: int,
    requested_max_candidates: int | None = None,
    selection_reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked = sorted(enumerate(previews), key=_execution_preview_rank)
    selected_indexes: list[int] = []
    selected_set: set[int] = set()
    seen_gap_rules: set[str] = set()

    for index, preview in ranked:
        if len(selected_indexes) >= max_candidates:
            break
        gap_rule_id = str(preview.get("gap_rule_id") or "")
        if gap_rule_id in seen_gap_rules:
            continue
        selected_indexes.append(index)
        selected_set.add(index)
        seen_gap_rules.add(gap_rule_id)

    for index, _preview in ranked:
        if len(selected_indexes) >= max_candidates:
            break
        if index in selected_set:
            continue
        selected_indexes.append(index)
        selected_set.add(index)

    selected = [previews[index] for index in selected_indexes]
    skipped = [preview for index, preview in enumerate(previews) if index not in selected_set]
    selection = {
        "policy_version": LLM_EXECUTION_CANDIDATE_SELECTION_POLICY_VERSION,
        "max_candidates": max_candidates,
        "requested_max_candidates": requested_max_candidates,
        "selection_reason": selection_reason,
        "total_candidates": len(previews),
        "selected_count": len(selected),
        "skipped_count": len(skipped),
        "selection_applied": len(skipped) > 0,
        "selected_candidate_ids": [_execution_preview_candidate_id(preview) for preview in selected],
        "skipped_candidate_ids": [_execution_preview_candidate_id(preview) for preview in skipped],
    }
    return selected, selection


def _evidence_spans_by_id_from_record(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for event in record.get("events") or []:
        payload = dict(event.get("payload") or {})
        event_type = str(event.get("event_type") or "")
        normalized_event_text = str(payload.get("normalized_text") or "").strip()
        event_segment_id = str(payload.get("segment_id") or "")
        for raw_span in payload.get("evidence_spans") or []:
            if not isinstance(raw_span, dict):
                continue
            evidence_id = str(raw_span.get("id") or "")
            if not evidence_id:
                continue
            span_segment_id = str(raw_span.get("segment_id") or "")
            quote = str(raw_span.get("quote") or "")
            if (
                event_type in {"final", "transcript_final", "transcript_revision"}
                and normalized_event_text
                and span_segment_id == event_segment_id
            ):
                quote = normalized_event_text
            evidence_by_id[evidence_id] = {
                "id": evidence_id,
                "segment_id": span_segment_id,
                "start_ms": int(raw_span.get("start_ms") or 0),
                "end_ms": int(raw_span.get("end_ms") or 0),
                "quote": quote,
                "status": str(raw_span.get("status") or "active"),
            }
    return evidence_by_id


def _render_evidence_context(evidence_spans: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for span in evidence_spans:
        quote = str(span.get("quote") or "").strip()
        if not quote:
            continue
        parts.append(
            f"[{_format_mmss(int(span.get('start_ms') or 0))}-{_format_mmss(int(span.get('end_ms') or 0))}] {quote}"
        )
    return "\n".join(parts)


def _format_mmss(ms: int) -> str:
    total_seconds = max(0, ms) // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def _disabled_execution_runs_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    session_id = str(record.get("session_id", ""))
    runs: list[dict[str, Any]] = []
    for preview in _execution_previews_from_record(record):
        request_id = str(preview["request_id"])
        execution_id = str(preview["execution_id"])
        run = {
            **preview,
            "run_id": f"asr_llm_execution_run_disabled_{execution_id}",
            "run_status": "skipped",
            "skip_reason": "llm_executor_disabled",
            "idempotency_key": (f"live_asr_execution_run:disabled:{session_id}:{request_id}"),
        }
        runs.append(run)
    return runs


def _asr_live_session_summary(record: dict[str, Any]) -> dict[str, Any]:
    events = list(record.get("events") or [])
    final_events = [event for event in events if event.get("event_type") == "transcript_final"]
    candidate_events = [event for event in events if event.get("event_type") == "suggestion_candidate_event"]
    at_values: list[int] = []
    for event in events:
        value = event.get("at_ms", 0)
        if isinstance(value, int):
            at_values.append(value)
        elif str(value).isdigit():
            at_values.append(int(value))
    event_source = _asr_live_event_source_metadata(record)
    derivation_projection = _asr_live_formal_derivation_projection(
        record,
        event_source,
    )
    canonical_transcript = project_canonical_transcript(
        session_id=str(record.get("session_id", "")),
        events=events,
    )
    has_transcript = bool(str(canonical_transcript.get("full_text") or "").strip())
    has_audio = bool((record.get("audio") or {}).get("saved"))
    is_mock = bool(event_source.get("is_mock"))
    created_at_ms = int(record.get("created_at_epoch_ms") or 0)
    last_activity_at_ms = int(record.get("last_activity_at_epoch_ms") or created_at_ms or 0)
    return {
        "session_id": str(record.get("session_id", "")),
        "provider": str(record.get("provider", "")),
        "event_source": event_source,
        "is_mock": is_mock,
        "provider_mode": str(record.get("provider_mode") or ("mock" if event_source.get("is_mock") else "real")),
        "asr_fallback_used": bool(record.get("asr_fallback_used", False)),
        "degradation_reasons": list(event_source.get("degradation_reasons") or []),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "event_count": len(events),
        "final_count": len(final_events),
        "suggestion_candidate_count": len(candidate_events),
        "suggestion_card_count": len(derivation_projection["suggestion_cards"]),
        "approach_card_count": len(derivation_projection["approach_cards"]),
        "stored_suggestion_card_count": derivation_projection["stored_formal_derivation_counts"]["suggestion_cards"],
        "stored_approach_card_count": derivation_projection["stored_formal_derivation_counts"]["approach_cards"],
        "stored_has_minutes": bool(derivation_projection["stored_formal_derivation_counts"]["minutes"]),
        "has_minutes": _minutes_record_has_content(derivation_projection["minutes"]),
        "formal_derivation_status": derivation_projection["formal_derivation_status"],
        "has_transcript": has_transcript,
        "has_audio": has_audio,
        "recoverable": not is_mock and (has_transcript or has_audio),
        "created_at_ms": created_at_ms,
        "last_activity_at_ms": last_activity_at_ms,
        "duration_ms": max(at_values) if at_values else 0,
    }


def _asr_live_formal_derivation_projection(
    record: dict[str, Any],
    event_source: dict[str, Any],
) -> dict[str, Any]:
    suggestion_cards = list(record.get("suggestion_cards") or [])
    approach_cards = list(record.get("approach_cards") or [])
    minutes = dict(record.get("minutes") or {})
    quality_blocked = _formal_derivation_quality_blocked(event_source)
    return {
        "formal_derivation_status": ("suppressed_by_asr_semantic_quality" if quality_blocked else "available"),
        "suggestion_cards": [] if quality_blocked else suggestion_cards,
        "approach_cards": [] if quality_blocked else approach_cards,
        "minutes": {} if quality_blocked else minutes,
        "stored_formal_derivation_counts": {
            "suggestion_cards": len(suggestion_cards),
            "approach_cards": len(approach_cards),
            "minutes": 1 if _minutes_record_has_content(minutes) else 0,
        },
    }


def _minutes_record_has_content(minutes: dict[str, Any]) -> bool:
    return bool(minutes.get("minutes_md") or minutes.get("minutes_json"))


def _formal_derivation_quality_blocked(event_source: dict[str, Any]) -> bool:
    return (
        str(event_source.get("input_source") or "") in ASR_SEMANTIC_QUALITY_FORMAL_INPUT_SOURCES
        and (event_source.get("asr_semantic_quality") or {}).get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER
    )


def _asr_live_session_hidden_from_default_history(record: dict[str, Any]) -> bool:
    event_source = _asr_live_event_source_metadata(record)
    input_source = str(event_source.get("input_source") or "")
    ingest_mode = str(event_source.get("ingest_mode") or record.get("ingest_mode") or "")
    provider = str(record.get("provider") or "")
    acceptance_blockers = set(event_source.get("acceptance_blockers") or [])
    if bool(event_source.get("is_mock")):
        return True
    if event_source.get("provider_mode") == "mock":
        return True
    if ingest_mode in {"mock_asr_session", "local_asr_event_file"}:
        return True
    if input_source in {"mock", "local_event_file", "simulated_realtime_wav"}:
        return True
    if provider in {"local_mock_asr", "fake"}:
        return True
    return bool(
        {
            "mock_or_demo_session",
            "local_event_file_not_real_input",
        }
        & acceptance_blockers
    )


def _merge_records_by_id(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    key: str,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {str(item.get(key, index)): dict(item) for index, item in enumerate(existing)}
    for index, item in enumerate(incoming):
        merged[str(item.get(key, f"incoming_{index}"))] = dict(item)
    return list(merged.values())


def _audio_media_type(audio_format: str) -> str:
    if audio_format == "wav":
        return "audio/wav"
    if audio_format == "mp3":
        return "audio/mpeg"
    if audio_format in {"m4a", "mp4"}:
        return "audio/mp4"
    return "application/octet-stream"


def _persist_asr_live_record_fields(
    asr_live_repo: Any,
    record: dict[str, Any],
    **fields: Any,
) -> dict[str, Any]:
    session_id = str(record.get("session_id") or "")
    return asr_live_repo.update(
        session_id,
        lambda latest: {**latest, **fields},
    )


def _realtime_asr_providers() -> list[str]:
    providers: list[str] = []
    if asr_stream.funasr_realtime_available():
        providers.append("funasr_realtime")
    if (
        asr_stream._SHERPA_VENV_PY.is_file()
        and asr_stream._SHERPA_WORKER.is_file()
        and asr_stream._SHERPA_MODEL.is_dir()
        and any(child.is_dir() and any(child.glob("*.onnx")) for child in asr_stream._SHERPA_MODEL.iterdir())
    ):
        providers.append("sherpa_onnx_realtime")
    return providers


def _asr_live_event_source_metadata(record: dict[str, Any]) -> dict[str, Any]:
    provider = str(record.get("provider") or "")
    is_mock_value = record.get("is_mock")
    is_mock = (
        bool(is_mock_value)
        if isinstance(is_mock_value, bool)
        else provider in {"local_mock_asr", "fake"} or str(record.get("ingest_mode") or "") == "mock_asr_session"
    )
    metadata = asr_event_source_metadata(provider, is_mock=is_mock)
    metadata["provider_mode"] = str(record.get("provider_mode") or ("mock" if is_mock else "real"))
    ingest_mode = str(record.get("ingest_mode") or "")
    if ingest_mode:
        metadata["ingest_mode"] = ingest_mode
    metadata["asr_fallback_used"] = bool(record.get("asr_fallback_used", False))
    degradation_reasons = [
        reason for reason in list(record.get("degradation_reasons") or []) if reason != ASR_SEMANTIC_QUALITY_BLOCKER
    ]
    stored_quality = dict(
        record.get("asr_semantic_quality")
        or {
            "schema_version": "asr_semantic_quality.v1",
            "policy_version": "general_chinese_technical_meeting.v3",
            "status": "not_evaluated",
            "blocker": None,
            "matched_entities": [],
            "matched_entity_groups": [],
            "missing_entity_groups": [],
            "technical_entity_hit_count": 0,
            "technical_group_hit_count": 0,
            "gibberish_score": 0.0,
            "latin_token_count": 0,
            "unknown_latin_token_count": 0,
            "unknown_latin_tokens": [],
            "mixed_language_fragmentation_score": 0.0,
            "quality_failure_reasons": [],
            "reason": "transcript_empty",
        }
    )
    canonical_transcript = project_canonical_transcript(
        session_id=str(record.get("session_id") or ""),
        events=list(record.get("events") or []),
    )
    canonical_text = str(canonical_transcript.get("full_text") or "").strip()
    semantic_quality = evaluate_semantic_quality(canonical_text) if canonical_text else stored_quality
    metadata["asr_semantic_quality"] = semantic_quality
    if semantic_quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER:
        degradation_reasons.append(ASR_SEMANTIC_QUALITY_BLOCKER)
    metadata["degradation_reasons"] = list(dict.fromkeys(degradation_reasons))
    if record.get("post_meeting_asr_profile"):
        metadata["post_meeting_asr_profile"] = dict(record.get("post_meeting_asr_profile") or {})
    if record.get("asr_runtime_profile"):
        metadata["asr_runtime_profile"] = dict(record.get("asr_runtime_profile") or {})
    input_source = _asr_live_input_source(record, provider=provider, is_mock=is_mock, ingest_mode=ingest_mode)
    metadata["input_source"] = input_source
    transcript_stats = _asr_live_transcript_stats(record)
    metadata.update(transcript_stats)
    acceptance_blockers = _asr_live_acceptance_blockers(
        provider_mode=str(metadata["provider_mode"]),
        is_mock=is_mock,
        input_source=input_source,
        ingest_mode=ingest_mode,
        asr_fallback_used=bool(metadata["asr_fallback_used"]),
        degradation_reasons=list(metadata["degradation_reasons"]),
        final_count=int(transcript_stats["final_count"]),
        non_empty_final_count=int(transcript_stats["non_empty_final_count"]),
        non_empty_transcript=bool(transcript_stats["non_empty_transcript"]),
    )
    metadata["acceptance_eligible"] = not acceptance_blockers
    metadata["acceptance_blockers"] = acceptance_blockers
    return metadata


def _post_meeting_asr_profile(asr_report: dict[str, Any]) -> dict[str, Any]:
    raw = dict(asr_report.get("raw") or {})
    batch = dict(asr_report.get("batch") or {})
    profile = {
        "provider": raw.get("provider"),
        "mode": raw.get("mode"),
        "model_id": raw.get("model_id") or batch.get("model_id"),
        "model_resolution": raw.get("model_resolution") or batch.get("model_resolution"),
        "model_download_status": raw.get("model_download_status") or batch.get("model_download_status"),
        "vad_model_status": raw.get("vad_model_status") or batch.get("vad_model_status"),
        "punc_model_status": raw.get("punc_model_status") or batch.get("punc_model_status"),
        "batch_mode": batch.get("batch_mode"),
        "audio_duration_seconds": asr_report.get("audio_duration_seconds"),
        "rtf": asr_report.get("rtf"),
        "transcribe_only_rtf": batch.get("transcribe_only_rtf"),
        "safe_to_download_models": batch.get("safe_to_download_models"),
        "safe_to_call_remote_asr": batch.get("safe_to_call_remote_asr"),
        "safe_to_call_llm": batch.get("safe_to_call_llm"),
    }
    return {key: value for key, value in profile.items() if value is not None and value != "" and value != []}


def _asr_live_transcript_stats(record: dict[str, Any]) -> dict[str, Any]:
    partial_count = 0
    final_count = 0
    non_empty_final_count = 0
    fragments: list[str] = []
    for event in record.get("events") or []:
        if event.get("event_type") == "transcript_partial":
            partial_count += 1
            continue
        if event.get("event_type") != "transcript_final":
            continue
        final_count += 1
        payload = event.get("payload") or {}
        text = str(payload.get("normalized_text") or payload.get("text") or event.get("text") or "").strip()
        if text:
            non_empty_final_count += 1
            fragments.append(text)
    return {
        "partial_count": partial_count,
        "final_count": final_count,
        "non_empty_final_count": non_empty_final_count,
        "non_empty_transcript": bool(fragments),
        "transcript_chars": sum(len(fragment) for fragment in fragments),
    }


def _asr_live_input_source(
    record: dict[str, Any],
    *,
    provider: str,
    is_mock: bool,
    ingest_mode: str,
) -> str:
    explicit_source = str(record.get("audio_source") or record.get("input_source") or "")
    if explicit_source:
        return explicit_source
    if is_mock or ingest_mode == "mock_asr_session":
        return "mock"
    if ingest_mode == "local_asr_event_file":
        return "local_event_file"
    if provider == "local_funasr_batch":
        return "uploaded_file"
    if provider in {"sherpa_onnx_realtime", "funasr_realtime"}:
        return "real_mic"
    return "unknown"


def _asr_live_acceptance_blockers(
    *,
    provider_mode: str,
    is_mock: bool,
    input_source: str,
    ingest_mode: str,
    asr_fallback_used: bool,
    degradation_reasons: list[str],
    final_count: int,
    non_empty_final_count: int,
    non_empty_transcript: bool,
) -> list[str]:
    blockers: list[str] = []
    if is_mock or ingest_mode == "mock_asr_session":
        blockers.append("mock_or_demo_session")
    if ingest_mode == "local_asr_event_file" or input_source == "local_event_file":
        blockers.append("local_event_file_not_real_input")
    if provider_mode != "real":
        blockers.append("asr_provider_not_real")
    if asr_fallback_used:
        blockers.append("asr_fallback_used")
    if "asr_semantic_quality_blocked" in degradation_reasons:
        blockers.append("asr_semantic_quality_blocked")
    if degradation_reasons:
        blockers.append("degraded_asr_session")
    if input_source not in {
        "real_mic",
        "browser_live_mic",
        "uploaded_file",
        "simulated_realtime_wav",
        "real_mic_recorded_wav",
    }:
        blockers.append("input_source_not_acceptance_lane")
    if final_count < 1:
        blockers.append("asr_final_missing")
    if non_empty_final_count < 1 or not non_empty_transcript:
        blockers.append("asr_transcript_empty")
    return blockers


def _enabled_llm_execution_blockers(record: dict[str, Any]) -> list[str]:
    metadata = _asr_live_event_source_metadata(record)
    blockers = list(metadata.get("acceptance_blockers") or [])
    return [blocker for blocker in blockers if blocker != "input_source_not_acceptance_lane"]


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _realtime_correction_blockers(record: dict[str, Any]) -> list[str]:
    """Allow correction of persisted finals across recoverable quality tails."""
    blockers = _enabled_llm_execution_blockers(record)
    degradation_reasons = {str(reason) for reason in record.get("degradation_reasons") or []}
    persisted_final_available = any(
        event.get("event_type") in {"final", "transcript_final"}
        and str((event.get("payload") or {}).get("text") or event.get("text") or "").strip()
        for event in record.get("events") or []
    )
    allowed: set[str] = set()
    if _has_recoverable_semantic_quality_failure(record):
        allowed.update({ASR_SEMANTIC_QUALITY_BLOCKER, "degraded_asr_session"})
    if persisted_final_available and degradation_reasons and degradation_reasons <= {"stream_interrupted"}:
        allowed.add("degraded_asr_session")
    return [blocker for blocker in blockers if blocker not in allowed]


def _has_recoverable_semantic_quality_failure(record: dict[str, Any]) -> bool:
    quality = dict(record.get("asr_semantic_quality") or {})
    reasons = set(str(reason) for reason in list(record.get("degradation_reasons") or []))
    return (
        quality.get("status") == "blocked"
        and quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER
        and reasons <= {ASR_SEMANTIC_QUALITY_BLOCKER}
    )


def _recompute_asr_semantic_quality(record: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the effective transcript projection, including accepted revisions."""
    texts: list[str] = []
    for event in realtime_transcript_correction.effective_final_events(record):
        payload = event.get("payload") or {}
        text = str(
            payload.get("normalized_text")
            or payload.get("text")
            or event.get("normalized_text")
            or event.get("text")
            or ""
        ).strip()
        if text:
            texts.append(text)
    if not texts:
        return record
    quality = evaluate_semantic_quality(" ".join(texts))
    raw_reasons = list(record.get("degradation_reasons") or [])
    non_quality_reasons = [
        reason for reason in raw_reasons if reason not in {ASR_SEMANTIC_QUALITY_BLOCKER, "degraded_asr_session"}
    ]
    reasons = list(non_quality_reasons)
    if "degraded_asr_session" in raw_reasons and non_quality_reasons:
        reasons.insert(0, "degraded_asr_session")
    if quality.get("blocker") == ASR_SEMANTIC_QUALITY_BLOCKER:
        reasons.append(ASR_SEMANTIC_QUALITY_BLOCKER)
    return {
        **record,
        "asr_semantic_quality": quality,
        "degradation_reasons": _dedupe_strings(reasons),
    }


def _ensure_enabled_llm_allowed(
    record: dict[str, Any],
    *,
    allow_non_acceptance_execution: bool,
) -> None:
    if allow_non_acceptance_execution:
        return
    blockers = _enabled_llm_execution_blockers(record)
    if blockers:
        session_id = str(record.get("session_id") or "")
        raise HTTPException(
            status_code=409,
            detail=(
                f"ASR live session {session_id} is not eligible for enabled LLM execution; "
                f"blockers: {', '.join(blockers)}"
            ),
        )


def _ensure_llm_provider_allowed_for_derivation(
    config: llm_service.LlmConfig,
    *,
    allow_non_acceptance_execution: bool,
) -> None:
    if allow_non_acceptance_execution:
        return
    if config.is_mock:
        raise HTTPException(
            status_code=409,
            detail="mock LLM provider cannot create production derivations",
        )


def _load_shadow_report_feedback_ingestion_module() -> Any:
    tool_path = SOURCE_REPO_ROOT / "tools" / "shadow_report_feedback_ingestion.py"
    spec = importlib.util.spec_from_file_location(
        "meeting_copilot_shadow_report_feedback_ingestion",
        tool_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("shadow report feedback ingestion tool is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _path_has_suffix_parts(path: Path, suffix_parts: tuple[str, ...]) -> bool:
    parts = path.parts
    width = len(suffix_parts)
    return any(parts[index : index + width] == suffix_parts for index in range(len(parts) - width + 1))


def _local_asr_events_read_path(events_path: Path) -> Path:
    return events_path if events_path.is_absolute() else REPO_ROOT / events_path


def _repo_relative_path(path: Path) -> Path | None:
    if not path.is_absolute():
        return path
    try:
        return path.resolve(strict=False).relative_to(REPO_ROOT.resolve(strict=False))
    except ValueError:
        return None


def _is_under_local_asr_events_root(path: Path) -> bool:
    relative = _repo_relative_path(path)
    if relative is None:
        return False
    path_text = relative.as_posix()
    return path_text == LOCAL_ASR_EVENTS_APPROVED_ROOT or path_text.startswith(f"{LOCAL_ASR_EVENTS_APPROVED_ROOT}/")


def _forbidden_local_asr_events_path_errors_for(path: Path) -> list[str]:
    errors: list[str] = []
    for label, suffix_parts in LOCAL_ASR_EVENTS_FORBIDDEN_PATH_LABELS:
        if _path_has_suffix_parts(path, suffix_parts):
            errors.append(f"events path is blocked: {label}")
    return errors


def _validate_local_asr_events_path(events_path: Path) -> list[str]:
    errors = _forbidden_local_asr_events_path_errors_for(events_path)
    resolved = _local_asr_events_read_path(events_path).resolve(strict=False)
    for error in _forbidden_local_asr_events_path_errors_for(resolved):
        if error not in errors:
            errors.append(error)
    if errors:
        return errors
    if not _is_under_local_asr_events_root(events_path) or not _is_under_local_asr_events_root(resolved):
        return ["events path is not under approved ASR events root"]
    return []


def _display_local_asr_events_path(events_path: Path) -> str:
    if _validate_local_asr_events_path(events_path):
        return "<redacted_invalid_path>"
    relative = _repo_relative_path(events_path)
    return relative.as_posix() if relative is not None else "<redacted_invalid_path>"


def _load_local_asr_events(events_path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(_local_asr_events_read_path(events_path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("ASR events file must contain valid JSON") from exc
    except OSError as exc:
        raise OSError("ASR events file could not be read") from exc
    if not isinstance(data, list):
        raise ValueError("ASR events JSON must be a list")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("ASR events JSON items must be objects")
    return data


def _validate_local_asr_event_contract(streaming_events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for event in streaming_events:
        event_type = str(event.get("event_type", ""))
        if event_type not in LOCAL_ASR_EVENT_TYPES:
            errors.append(f"unsupported ASR streaming event_type: {event_type}")
            continue
        event_label = f"ASR {event_type} event"
        if event_type in {"partial", "final", "revision", "error"}:
            segment_id = event.get("segment_id")
            if not isinstance(segment_id, str) or not segment_id.strip():
                errors.append(f"{event_label} missing segment_id")
                continue
        if event_type in {"partial", "final", "revision"}:
            text = event.get("text")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{event_label} text must be non-empty")
                continue
        if event_type == "revision":
            revision_of = event.get("revision_of")
            if not isinstance(revision_of, str) or not revision_of.strip():
                errors.append("ASR revision event missing revision_of")
                continue
        time_errors = _validate_local_asr_event_timestamps(event, event_label)
        if time_errors:
            errors.extend(time_errors)
            continue
        confidence_error = _validate_local_asr_event_confidence(event, event_label)
        if confidence_error:
            errors.append(confidence_error)
    return errors


def _is_local_asr_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_local_asr_event_timestamps(
    event: dict[str, Any],
    event_label: str,
) -> list[str]:
    errors: list[str] = []
    for field_name in ("start_ms", "end_ms", "received_at_ms"):
        if field_name not in event:
            continue
        value = event.get(field_name)
        if not _is_local_asr_number(value) or float(value) < 0:
            errors.append(f"{event_label} {field_name} must be a non-negative number")
    if errors:
        return errors
    start_ms = event.get("start_ms")
    end_ms = event.get("end_ms")
    received_at_ms = event.get("received_at_ms")
    if _is_local_asr_number(start_ms) and _is_local_asr_number(end_ms):
        if float(end_ms) < float(start_ms):
            return [f"{event_label} end_ms must be greater than or equal to start_ms"]
    if _is_local_asr_number(end_ms) and _is_local_asr_number(received_at_ms):
        if float(received_at_ms) < float(end_ms):
            return [f"{event_label} received_at_ms must be greater than or equal to end_ms"]
    return []


def _validate_local_asr_event_confidence(
    event: dict[str, Any],
    event_label: str,
) -> str | None:
    if "confidence" not in event or event.get("confidence") is None:
        return None
    confidence = event.get("confidence")
    if not _is_local_asr_number(confidence):
        return f"{event_label} confidence must be between 0 and 1"
    if not 0 <= float(confidence) <= 1:
        return f"{event_label} confidence must be between 0 and 1"
    return None


def _local_asr_input_event_counts(streaming_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in LOCAL_ASR_EVENT_TYPES}
    for event in streaming_events:
        event_type = str(event.get("event_type", ""))
        if event_type in counts:
            counts[event_type] += 1
    return counts


def _local_asr_live_event_counts(live_events: list[dict[str, Any]]) -> dict[str, int]:
    counts = {event_type: 0 for event_type in LOCAL_ASR_LIVE_EVENT_TYPES}
    for event in live_events:
        event_type = str(event.get("event_type", ""))
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _local_asr_llm_statuses(live_events: list[dict[str, Any]]) -> list[str]:
    statuses: set[str] = set()
    for event in live_events:
        payload = event.get("payload", {})
        if isinstance(payload, dict) and isinstance(payload.get("llm_call_status"), str):
            statuses.add(payload["llm_call_status"])
    return sorted(statuses)


def _local_asr_event_file_error_detail(
    *,
    ingest_status: str,
    events_path: Path,
    provider: str,
    session_id: str,
    validation_errors: list[str],
    input_event_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "ingest_mode": "local_asr_event_file",
        "ingest_status": ingest_status,
        "events_path": _display_local_asr_events_path(events_path),
        "provider": provider,
        "session_id": session_id,
        "input_event_counts": input_event_counts or {event_type: 0 for event_type in LOCAL_ASR_EVENT_TYPES},
        "validation_errors": validation_errors,
        **LOCAL_ASR_FILE_SAFETY_FLAGS,
    }


def _runtime_data_dir() -> Path:
    configured = os.environ.get("MEETING_COPILOT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_RUNTIME_DATA_DIR


def create_runtime_app() -> FastAPI:
    """Build the process-level app with durable local session storage."""
    runtime_data_dir = _runtime_data_dir()
    managed_log_stream = ManagedRotatingLogStream(data_dir=runtime_data_dir)
    configure_logging(stream=managed_log_stream)
    runtime_app = create_app(
        data_dir=runtime_data_dir,
        prewarm_funasr=True,
        semantic_projection_mode="llm_first",
    )
    runtime_app.state.managed_log_path = str(managed_log_stream.rotator.path)
    runtime_app.state.managed_log_stream = managed_log_stream
    return runtime_app


app = create_runtime_app()

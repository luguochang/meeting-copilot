from __future__ import annotations

import importlib.util
import json
import os
import structlog
import time
import uuid
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

from fastapi import Body, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from meeting_copilot_web_mvp.logging_config import configure_logging, get_logger
from meeting_copilot_web_mvp import llm_service
from meeting_copilot_web_mvp import asr_stream

configure_logging()
_log = get_logger("meeting_copilot_web_mvp.app")

from meeting_copilot_core.contracts import SuggestionCardV1
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
    JsonFileAsrLiveSessionRepository,
)
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
)


STATIC_DIR = Path(__file__).resolve().parent / "frontend_static"
WORKBENCH_HTML = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
SOURCE_REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_ROOT = SOURCE_REPO_ROOT
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
SCHEMA_VALIDATION_NON_STRONG_STATUSES = {
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}
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
    session_id: str
    provider: str = "local_mock_asr"
    streaming_events: list[dict[str, Any]]


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


class ShadowReportFeedbackIngestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_report: dict[str, Any] | None = None
    candidate_report_path: str | None = None
    feedback_entries: list[dict[str, Any]]


class DesktopTauriNoopRunResultValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_result: dict[str, Any]


def create_app(
    repository: InMemorySessionRepository | JsonFileSessionRepository | None = None,
    data_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Meeting Copilot Local Web MVP")
    if repository is not None and data_dir is not None:
        raise ValueError("repository and data_dir cannot both be provided")
    repo = repository or (
        JsonFileSessionRepository(data_dir)
        if data_dir is not None
        else InMemorySessionRepository()
    )
    asr_live_repo = (
        JsonFileAsrLiveSessionRepository(data_dir)
        if data_dir is not None
        else InMemoryAsrLiveSessionRepository()
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "meeting-copilot-web-mvp"}

    @app.get("/workbench")
    def workbench() -> Response:
        return Response(WORKBENCH_HTML, media_type="text/html; charset=utf-8")

    @app.websocket("/live/asr/stream/ws/{session_id}")
    async def asr_stream_ws(websocket: WebSocket, session_id: str):
        await asr_stream.handle_stream(websocket, session_id)

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
    def workbench() -> Response:
        return Response(
            content=(STATIC_DIR / "index.html").read_text(encoding="utf-8"),
            media_type="text/html; charset=utf-8",
        )

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
        try:
            live_events = build_asr_live_events(
                session_id=payload.session_id,
                provider=payload.provider,
                streaming_events=payload.streaming_events,
                is_mock=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        record = {
            "session_id": payload.session_id,
            "provider": payload.provider,
            "source": ASR_LIVE_SOURCE,
            "trace_kind": ASR_LIVE_TRACE_KIND,
            "events": live_events,
        }
        try:
            asr_live_repo.create(record)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "session_id": payload.session_id,
            "event_source": asr_event_source_metadata(payload.provider, is_mock=True),
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
            "event_source": asr_event_source_metadata(payload.provider, is_mock=False),
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
            content=render_sse_events(
                build_replay_events(snapshot, evaluation_summary=evaluation_summary)
            ),
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
            content=render_live_sse_events(
                build_mock_live_events(snapshot, evaluation_summary=evaluation_summary)
            ),
            media_type="text/event-stream; charset=utf-8",
        )

    @app.get("/live/asr/sessions/{session_id}/events")
    def get_asr_live_session_events(session_id: str) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "events": record["events"],
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

    @app.post("/live/asr/sessions/{session_id}/llm-execution-runs")
    def create_asr_live_session_llm_execution_runs(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.mode == "disabled":
            runs = _disabled_execution_runs_from_record(record)
        elif payload.mode == "enabled":
            config = llm_service.LlmConfig.from_env()
            if config is None:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "LLM execution enabled but LLM_GATEWAY_BASE_URL / "
                        "LLM_GATEWAY_API_KEY not configured in environment"
                    ),
                )
            previews = _execution_previews_from_record(record)
            runs = llm_service.build_enabled_execution_runs(previews, config)
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
            "run_count": len(runs),
            "runs": runs,
        }

    @app.post("/live/asr/sessions/{session_id}/approach-cards")
    def create_asr_live_session_approach_cards(
        session_id: str,
        payload: CreateLlmExecutionRunsRequest,
    ) -> dict[str, Any]:
        try:
            record = asr_live_repo.get(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if payload.mode != "enabled":
            raise HTTPException(status_code=422, detail=f"unsupported approach mode: {payload.mode}")
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "LLM execution enabled but LLM_GATEWAY_BASE_URL / "
                    "LLM_GATEWAY_API_KEY not configured in environment"
                ),
            )
        transcript_text = " ".join(
            str((e.get("payload") or {}).get("text", ""))
            for e in record.get("events") or []
            if e.get("event_type") == "transcript_final"
        )
        cards, usage = llm_service.build_approach_cards(transcript_text, config)
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "approach_cards": cards,
            "count": len(cards),
            "llm_usage": usage,
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
    def delete_session(session_id: str) -> Response:
        try:
            session_deleted = repo.delete(session_id)
            asr_live_deleted = asr_live_repo.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        deleted = session_deleted or asr_live_deleted
        if not deleted:
            raise HTTPException(status_code=404, detail=f"session not found: {session_id}")
        return Response(status_code=204)

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


def _openai_request_forbidden_fields() -> list[str]:
    return [
        "api_key",
        "authorization",
        "bearer_token",
        "base_url",
        "raw_config",
        "config_path",
    ]


def _openai_request_body_previews_from_record(
    record: dict[str, Any],
) -> list[dict[str, Any]]:
    session_id = str(record.get("session_id", ""))
    source = str(record.get("source", ""))
    trace_kind = str(record.get("trace_kind", ""))
    previews: list[dict[str, Any]] = []
    for event in record.get("events") or []:
        if event.get("event_type") != "llm_request_draft_event":
            continue
        payload = dict(event.get("payload") or {})
        request_id = str(payload.get("request_id", ""))
        target_type = str(payload.get("target_type", ""))
        target_id = str(payload.get("target_id", ""))
        gap_rule_id = str(payload.get("gap_rule_id", ""))
        evidence_span_ids = [
            str(item) for item in payload.get("evidence_span_ids") or []
        ]
        segment_batch = [str(item) for item in payload.get("segment_batch") or []]
        candidate_confidence = payload.get("candidate_confidence")
        candidate_confidence_level = payload.get("candidate_confidence_level")
        candidate_degradation_reasons = list(
            payload.get("candidate_degradation_reasons") or []
        )
        safe_suggested_prompt, suggested_prompt_redacted = (
            _redact_sensitive_openai_request_body_value(
                str(payload.get("suggested_prompt", ""))
            )
        )
        safe_input_summary, input_summary_redacted = (
            _redact_sensitive_openai_request_body_value(
                str(payload.get("input_summary", ""))
            )
        )
        safe_request_origin, request_origin_redacted = (
            _redact_sensitive_openai_request_body_value(
                str(payload.get("request_origin", ""))
            )
        )
        safe_source_event_ids, source_event_ids_redacted = (
            _redact_sensitive_openai_request_body_values(
                payload.get("source_event_ids") or []
            )
        )
        safe_evidence_span_ids, evidence_span_ids_redacted = (
            _redact_sensitive_openai_request_body_values(evidence_span_ids)
        )
        safe_segment_batch, segment_batch_redacted = (
            _redact_sensitive_openai_request_body_values(segment_batch)
        )
        redacted_fields = []
        if suggested_prompt_redacted:
            redacted_fields.append("suggested_prompt")
        if input_summary_redacted:
            redacted_fields.append("input_summary")
        if request_origin_redacted:
            redacted_fields.append("request_origin")
        if source_event_ids_redacted:
            redacted_fields.append("source_event_ids")
        if evidence_span_ids_redacted:
            redacted_fields.append("evidence_span_ids")
        if segment_batch_redacted:
            redacted_fields.append("segment_batch")
        previews.append(
            {
                "request_body_preview_id": f"asr_openai_request_body_preview_{request_id}",
                "request_body_status": "preview_only",
                "redaction_status": "applied" if redacted_fields else "not_needed",
                "redacted_fields": redacted_fields,
                "request_id": request_id,
                "request_draft_event_id": str(event.get("id", "")),
                "request_draft_sequence": int(event.get("sequence", 0)),
                "request_type": str(payload.get("request_type", "")),
                "target_candidate_id": str(payload.get("target_candidate_id", "")),
                "target_type": target_type,
                "target_id": target_id,
                "gap_rule_id": gap_rule_id,
                "idempotency_key": (
                    f"live_asr_openai_request_body_preview:{session_id}:{request_id}"
                ),
                "provider_protocol": "openai_compatible_chat_completions",
                "endpoint_family": "chat_completions",
                "http_method": "POST",
                "request_path": "/v1/chat/completions",
                "model": "not_configured",
                "temperature": 0.2,
                "max_output_tokens": 600,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are Meeting Copilot. Generate one concise suggestion card "
                            "for an engineering meeting. Use only the provided evidence."
                        ),
                    },
                    {
                        "role": "user",
                        "content": _openai_request_user_message_content(
                            target_type=target_type,
                            target_id=target_id,
                            gap_rule_id=gap_rule_id,
                            evidence_span_ids=safe_evidence_span_ids,
                            segment_batch=safe_segment_batch,
                            candidate_confidence=candidate_confidence,
                            candidate_confidence_level=candidate_confidence_level,
                            suggested_prompt=safe_suggested_prompt,
                            input_summary=safe_input_summary,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": _suggestion_card_schema_outline_preview(),
                },
                "metadata": {
                    "source": source,
                    "trace_kind": trace_kind,
                    "request_origin": safe_request_origin,
                    "source_event_ids": safe_source_event_ids,
                    "evidence_span_ids": safe_evidence_span_ids,
                    "segment_batch": safe_segment_batch,
                    "candidate_confidence": candidate_confidence,
                    "candidate_confidence_level": candidate_confidence_level,
                    "candidate_degradation_reasons": candidate_degradation_reasons,
                },
                "forbidden_request_fields": _openai_request_forbidden_fields(),
                "llm_call_status": "not_called",
                "schema_status": "not_generated",
                "card_status": "not_created",
                "cost_status": "not_estimated",
            }
        )
    return previews


def _suggestion_card_schema_outline_preview() -> dict[str, Any]:
    return {
        "name": "SuggestionCardV1",
        "strict": True,
        "schema_outline_status": "outline_only",
        "schema_outline_source": "local_contract_preview",
        "schema_outline": {
            "type": "object",
            "required": [
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
            ],
            "optional": [
                "title",
                "suggested_question",
            ],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string"},
                "evidence_span_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "state_event_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "gap_rule_id": {"type": "string"},
                "trigger_reason": {"type": "string"},
                "trigger_source": {"type": "string"},
                "final_segment_at_ms": {"type": "integer", "minimum": 0},
                "state_event_at_ms": {"type": "integer", "minimum": 0},
                "card_created_at_ms": {"type": "integer", "minimum": 0},
                "latency_ms": {"type": "integer", "minimum": 0},
                "prompt_version": {"type": "string"},
                "model": {"type": "string"},
                "usage": {"type": "object"},
                "schema_result": {"type": "string"},
                "show_or_silence_decision": {"type": "string"},
                "segment_batch": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "status": {"type": "string", "default": "new"},
                "title": {"type": ["string", "null"]},
                "suggested_question": {"type": ["string", "null"]},
            },
            "additional_properties_status": "allowed_by_local_contract_extra",
        },
    }


def _redact_sensitive_openai_request_body_value(value: Any) -> tuple[str, bool]:
    text = str(value)
    if OPENAI_REQUEST_BODY_SENSITIVE_PATTERN.search(
        text
    ) or OPENAI_REQUEST_BODY_RELAY_DOMAIN_MARKER.lower() in text.lower():
        return OPENAI_REQUEST_BODY_REDACTION_PLACEHOLDER, True
    return text, False


def _redact_sensitive_openai_request_body_values(
    values: list[Any],
) -> tuple[list[str], bool]:
    redacted_values: list[str] = []
    any_redacted = False
    for value in values:
        redacted_value, was_redacted = _redact_sensitive_openai_request_body_value(
            value
        )
        redacted_values.append(redacted_value)
        any_redacted = any_redacted or was_redacted
    return redacted_values, any_redacted


def _openai_request_user_message_content(
    *,
    target_type: str,
    target_id: str,
    gap_rule_id: str,
    evidence_span_ids: list[str],
    segment_batch: list[str],
    candidate_confidence: Any,
    candidate_confidence_level: Any,
    suggested_prompt: str,
    input_summary: str,
) -> str:
    confidence_level = str(candidate_confidence_level)
    return "\n".join(
        [
            f"Target: {target_type} {target_id}",
            f"Gap rule: {gap_rule_id}",
            f"Evidence spans: {', '.join(evidence_span_ids)}",
            f"Segment batch: {', '.join(segment_batch)}",
            f"Candidate quality: {confidence_level} ({candidate_confidence})",
            f"Suggested prompt: {suggested_prompt}",
            f"Input summary: {input_summary}",
        ]
    )


def _execution_previews_from_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    session_id = str(record.get("session_id", ""))
    previews: list[dict[str, Any]] = []
    for event in record.get("events") or []:
        if event.get("event_type") != "llm_request_draft_event":
            continue
        payload = dict(event.get("payload") or {})
        request_id = str(payload.get("request_id", ""))
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
                "idempotency_key": (
                    f"live_asr_execution_preview:{session_id}:{request_id}"
                ),
                "source_event_ids": list(payload.get("source_event_ids") or []),
                "evidence_span_ids": list(payload.get("evidence_span_ids") or []),
                "segment_batch": list(payload.get("segment_batch") or []),
                "candidate_confidence": payload.get("candidate_confidence"),
                "candidate_confidence_level": payload.get(
                    "candidate_confidence_level"
                ),
                "candidate_degradation_reasons": list(
                    payload.get("candidate_degradation_reasons") or []
                ),
                "input_summary": str(payload.get("input_summary", "")),
                "suggested_prompt": str(payload.get("suggested_prompt", "")),
            }
        )
    return previews


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
            "idempotency_key": (
                f"live_asr_execution_run:disabled:{session_id}:{request_id}"
            ),
        }
        runs.append(run)
    return runs


def _validate_llm_schema_validation_dry_run_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    mode = raw_mode.strip()
    if not mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if mode != "dry_run_only":
        raise HTTPException(
            status_code=422,
            detail=f"unsupported schema validation mode: {mode}",
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_run_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "disabled":
        raise HTTPException(
            status_code=422,
            detail=f"unsupported card lifecycle append run mode: {raw_mode}",
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_repository_dry_run_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "dry_run_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append repository dry-run mode: "
                f"{raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_transaction_run_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "disabled":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append transaction run mode: "
                f"{raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_result_audit_preview_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preview_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append result audit preview mode: "
                f"{raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_retry_replay_preflight_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle retry replay preflight mode: "
                f"{raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_event_serializer_dry_run_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "dry_run_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append event serializer dry-run "
                f"mode: {raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_mutation_preflight_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append mutation preflight "
                f"mode: {raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_transaction_commit_preflight_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append transaction commit "
                f"preflight mode: {raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_idempotency_store_write_preflight_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append idempotency store write "
                f"preflight mode: {raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_append_result_audit_event_persistence_preflight_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported card lifecycle append result audit event "
                f"persistence preflight mode: {raw_mode}"
            ),
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _validate_llm_card_lifecycle_readiness_summary_payload(
    payload: Any,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="request body must be an object")
    allowed_fields = {"mode", "request_id", "candidate_response"}
    extra_fields = sorted(set(payload) - allowed_fields)
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail=f"extra fields are not permitted: {', '.join(extra_fields)}",
        )
    raw_mode = payload.get("mode")
    if raw_mode is None:
        raise HTTPException(status_code=422, detail="missing mode")
    if not isinstance(raw_mode, str):
        raise HTTPException(status_code=422, detail="mode must be a string")
    if not raw_mode:
        raise HTTPException(status_code=422, detail="missing mode")
    if raw_mode != "summary_only":
        raise HTTPException(
            status_code=422,
            detail=f"unsupported card lifecycle readiness summary mode: {raw_mode}",
        )
    raw_request_id = payload.get("request_id")
    if raw_request_id is None:
        raise HTTPException(status_code=422, detail="missing request_id")
    if not isinstance(raw_request_id, str):
        raise HTTPException(status_code=422, detail="request_id must be a string")
    request_id = raw_request_id.strip()
    if not request_id:
        raise HTTPException(status_code=422, detail="missing request_id")
    if "candidate_response" not in payload:
        raise HTTPException(status_code=422, detail="missing candidate_response")
    candidate_response = payload.get("candidate_response")
    if not isinstance(candidate_response, dict):
        raise HTTPException(
            status_code=422,
            detail="candidate_response must be an object",
        )
    return {
        "mode": raw_mode,
        "request_id": request_id,
        "candidate_response": dict(candidate_response),
    }


def _llm_schema_validation_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    request_draft = _request_draft_for_schema_validation(record, request_id)
    candidate_response = dict(payload["candidate_response"])
    validation_errors = _validate_suggestion_card_dry_run_candidate(
        candidate_response
    )
    validation_passed = not validation_errors
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "validation_mode": "dry_run_only",
        "validation_status": "passed" if validation_passed else "failed",
        "schema_name": "SuggestionCardV1",
        "schema_validation_status": (
            "dry_run_passed" if validation_passed else "dry_run_failed"
        ),
        "schema_result_status": "not_generated",
        "card_status": "not_created",
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "cost_status": "not_estimated",
        "safe_to_create_card": False,
        "request_id": request_id,
        "request_draft_event_id": request_draft["event_id"],
        "request_draft_sequence": request_draft["sequence"],
        "target_candidate_id": str(
            request_draft["payload"].get("target_candidate_id", "")
        ),
        "target_type": str(request_draft["payload"].get("target_type", "")),
        "target_id": str(request_draft["payload"].get("target_id", "")),
        "gap_rule_id": str(request_draft["payload"].get("gap_rule_id", "")),
        "source_event_ids": list(request_draft["payload"].get("source_event_ids") or []),
        "evidence_span_ids": list(request_draft["payload"].get("evidence_span_ids") or []),
        "segment_batch": list(request_draft["payload"].get("segment_batch") or []),
        "validation_errors": validation_errors,
        "validated_field_count": len(
            SCHEMA_VALIDATION_REQUIRED_FIELDS
            + SCHEMA_VALIDATION_OPTIONAL_FIELDS
        ),
        "candidate_response_preview": _candidate_response_preview(
            candidate_response
        ),
        "block_reasons": [
            "schema_validation_dry_run_only",
            "llm_executor_disabled",
            "card_lifecycle_disabled",
        ],
        "next_required_decisions": [
            "enabled_executor_mode_contract",
            "real_llm_response_parser",
            "schema_validation_failure_lifecycle",
            "card_creation_policy",
            "token_cost_accounting",
        ],
    }


def _request_draft_for_schema_validation(
    record: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    for draft in _request_draft_events_from_record(record):
        if str(draft["payload"].get("request_id", "")) == request_id:
            return draft
    raise HTTPException(
        status_code=404,
        detail=(
            "LLM request draft not found for schema validation dry-run: "
            f"{request_id}"
        ),
    )


def _request_draft_for_card_creation_policy(
    record: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    for draft in _request_draft_events_from_record(record):
        if str(draft["payload"].get("request_id", "")) == request_id:
            return draft
    raise HTTPException(
        status_code=404,
        detail=(
            "LLM request draft not found for card creation policy dry-run: "
            f"{request_id}"
        ),
    )


def _request_draft_for_card_lifecycle_preview(
    record: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    for draft in _request_draft_events_from_record(record):
        if str(draft["payload"].get("request_id", "")) == request_id:
            return draft
    raise HTTPException(
        status_code=404,
        detail=(
            "LLM request draft not found for card lifecycle preview dry-run: "
            f"{request_id}"
        ),
    )


def _llm_card_creation_policy_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    request_draft = _request_draft_for_card_creation_policy(record, request_id)
    candidate_response = dict(payload["candidate_response"])
    validation_errors = _validate_suggestion_card_dry_run_candidate(
        candidate_response
    )
    scheduler_gate = _card_policy_scheduler_gate(record, request_draft)
    policy_errors = _card_creation_policy_errors(
        record,
        request_draft,
        candidate_response,
        validation_errors,
    )
    policy_allowed = not policy_errors
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "policy_mode": "dry_run_only",
        "policy_status": "allowed" if policy_allowed else "blocked",
        "card_creation_policy_status": (
            "dry_run_allowed" if policy_allowed else "dry_run_blocked"
        ),
        "schema_name": "SuggestionCardV1",
        "schema_validation_status": (
            "dry_run_passed" if not validation_errors else "dry_run_failed"
        ),
        "schema_result_status": "not_generated",
        "card_status": "not_created",
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "cost_status": "not_estimated",
        "safe_to_create_card": False,
        "would_create_card_if_enabled": policy_allowed,
        "would_silence_candidate_if_enabled": not policy_allowed,
        "request_id": request_id,
        "request_draft_event_id": request_draft["event_id"],
        "request_draft_sequence": request_draft["sequence"],
        "target_candidate_id": str(
            request_draft["payload"].get("target_candidate_id", "")
        ),
        "target_type": str(request_draft["payload"].get("target_type", "")),
        "target_id": str(request_draft["payload"].get("target_id", "")),
        "target_state_ref": _card_policy_state_ref(
            str(request_draft["payload"].get("target_type", "")),
            str(request_draft["payload"].get("target_id", "")),
        ),
        "gap_rule_id": str(request_draft["payload"].get("gap_rule_id", "")),
        "source_event_ids": list(request_draft["payload"].get("source_event_ids") or []),
        "evidence_span_ids": list(request_draft["payload"].get("evidence_span_ids") or []),
        "segment_batch": list(request_draft["payload"].get("segment_batch") or []),
        **scheduler_gate,
        "validation_errors": validation_errors,
        "policy_errors": policy_errors,
        "policy_check_count": CARD_CREATION_POLICY_CHECK_COUNT,
        "candidate_response_preview": _candidate_response_preview(
            candidate_response
        ),
        "block_reasons": [
            "card_creation_policy_dry_run_only",
            "card_lifecycle_disabled",
        ],
        "next_required_decisions": [
            "real_llm_response_parser",
            "llm_schema_result_event_lifecycle",
            "suggestion_card_persistence",
            "suggestion_silenced_lifecycle",
            "feedback_idempotency",
        ],
    }


def _llm_card_lifecycle_preview_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    request_id = str(payload["request_id"])
    request_draft = _request_draft_for_card_lifecycle_preview(record, request_id)
    candidate_response = dict(payload["candidate_response"])
    validation_errors = _validate_suggestion_card_dry_run_candidate(
        candidate_response
    )
    scheduler_gate = _card_policy_scheduler_gate(record, request_draft)
    policy_errors = _card_creation_policy_errors(
        record,
        request_draft,
        candidate_response,
        validation_errors,
    )
    policy_allowed = not policy_errors
    preview_events = _card_lifecycle_preview_events(
        request_id,
        request_draft,
        candidate_response,
        policy_allowed,
        validation_errors,
        policy_errors,
    )
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "lifecycle_preview_mode": "dry_run_only",
        "lifecycle_preview_status": "previewed",
        "schema_name": "SuggestionCardV1",
        "schema_validation_status": (
            "dry_run_passed" if not validation_errors else "dry_run_failed"
        ),
        "card_creation_policy_status": (
            "dry_run_allowed" if policy_allowed else "dry_run_blocked"
        ),
        "future_lifecycle_status": (
            "would_create_card" if policy_allowed else "would_silence_candidate"
        ),
        "schema_result_status": "preview_only",
        "card_status": "preview_only" if policy_allowed else "not_created",
        "silenced_status": "not_previewed" if policy_allowed else "preview_only",
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "config_source_status": "not_read",
        "cost_status": "not_estimated",
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "would_append_event_types_if_enabled": [
            event["event_type"] for event in preview_events
        ],
        "request_id": request_id,
        "request_draft_event_id": request_draft["event_id"],
        "request_draft_sequence": request_draft["sequence"],
        "target_candidate_id": str(
            request_draft["payload"].get("target_candidate_id", "")
        ),
        "target_type": str(request_draft["payload"].get("target_type", "")),
        "target_id": str(request_draft["payload"].get("target_id", "")),
        "target_state_ref": _card_policy_state_ref(
            str(request_draft["payload"].get("target_type", "")),
            str(request_draft["payload"].get("target_id", "")),
        ),
        "gap_rule_id": str(request_draft["payload"].get("gap_rule_id", "")),
        "source_event_ids": list(request_draft["payload"].get("source_event_ids") or []),
        "evidence_span_ids": list(request_draft["payload"].get("evidence_span_ids") or []),
        "segment_batch": list(request_draft["payload"].get("segment_batch") or []),
        **scheduler_gate,
        "validation_errors": validation_errors,
        "policy_errors": policy_errors,
        "lifecycle_preview_check_count": CARD_LIFECYCLE_PREVIEW_CHECK_COUNT,
        "candidate_response_preview": _candidate_response_preview(
            candidate_response
        ),
        "preview_events": preview_events,
        "block_reasons": [
            "card_lifecycle_preview_dry_run_only",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "real_llm_response_parser",
            "llm_schema_result_event_persistence",
            "suggestion_card_repository_lifecycle",
            "suggestion_silenced_repository_lifecycle",
            "feedback_idempotency",
        ],
    }


def _llm_card_lifecycle_append_preflight_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    preview = _llm_card_lifecycle_preview_dry_run_from_record(record, payload)
    append_plan, append_errors = _card_lifecycle_append_preflight_plan(
        record,
        preview,
    )
    append_allowed = not append_errors
    return {
        **preview,
        "append_preflight_mode": "dry_run_only",
        "append_preflight_status": "allowed" if append_allowed else "blocked",
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "existing_event_count": len(record.get("events") or []),
        "last_existing_sequence": _last_existing_event_sequence(record),
        "append_plan_count": len(append_plan),
        "append_errors": append_errors,
        "append_plan": append_plan,
        "block_reasons": [
            "append_preflight_dry_run_only",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "event_append_repository_api",
            "idempotency_store",
            "enabled_card_lifecycle_mutation",
            "feedback_idempotency",
            "retry_and_replay_conflict_resolution",
        ],
    }


def _llm_card_lifecycle_append_disabled_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    preflight = _llm_card_lifecycle_append_preflight_dry_run_from_record(
        record,
        {
            "mode": "dry_run_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    append_runs = [
        _card_lifecycle_disabled_append_run_from_plan_item(
            preflight,
            append_plan_item,
        )
        for append_plan_item in preflight.get("append_plan") or []
    ]
    return {
        **preflight,
        "append_run_mode": "disabled",
        "append_run_status": "skipped",
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "append_run_count": len(append_runs),
        "append_runs": append_runs,
        "block_reasons": [
            "append_run_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "event_append_repository_api",
            "idempotency_store",
            "enabled_card_lifecycle_mutation",
            "retry_and_replay_conflict_resolution",
            "append_result_audit_event",
        ],
    }


def _llm_card_lifecycle_append_repository_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    disabled_run = _llm_card_lifecycle_append_disabled_run_from_record(
        record,
        {
            "mode": "disabled",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    append_runs_by_future_event_id = {
        str(run.get("future_event_id", "")): run
        for run in disabled_run.get("append_runs") or []
    }
    repository_results = [
        _card_lifecycle_repository_dry_run_result_from_plan_item(
            disabled_run,
            append_plan_item,
            append_runs_by_future_event_id.get(
                str(append_plan_item.get("future_event_id", "")),
                {},
            ),
        )
        for append_plan_item in disabled_run.get("append_plan") or []
    ]
    repository_blocked = any(
        result["repository_result_status"] == "blocked_by_preflight"
        for result in repository_results
    )
    return {
        **disabled_run,
        "repository_dry_run_mode": "dry_run_only",
        "repository_dry_run_status": (
            "blocked_by_preflight"
            if repository_blocked
            else "would_append_if_enabled"
        ),
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "repository_append_count": len(repository_results),
        "repository_results": repository_results,
        "block_reasons": [
            "repository_append_dry_run_only",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "repository_append_transaction",
            "idempotency_store_write_contract",
            "append_result_audit_event",
            "retry_and_replay_conflict_resolution",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_transaction_disabled_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    repository_dry_run = _llm_card_lifecycle_append_repository_dry_run_from_record(
        record,
        {
            "mode": "dry_run_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    transaction_runs = [
        _card_lifecycle_append_transaction_disabled_run_from_repository_result(
            repository_dry_run,
            repository_result,
        )
        for repository_result in repository_dry_run.get("repository_results") or []
    ]
    return {
        **repository_dry_run,
        "transaction_run_mode": "disabled",
        "transaction_run_status": "skipped",
        "repository_transaction_status": "disabled",
        "idempotency_store_write_status": "not_written",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "safe_to_commit_transaction": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "transaction_run_count": len(transaction_runs),
        "transaction_runs": transaction_runs,
        "block_reasons": [
            "repository_transaction_disabled",
            "idempotency_store_write_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "repository_transaction_commit_contract",
            "idempotency_store_write_contract",
            "append_result_audit_event",
            "retry_and_replay_conflict_resolution",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_result_audit_preview_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    transaction_run = _llm_card_lifecycle_append_transaction_disabled_run_from_record(
        record,
        {
            "mode": "disabled",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    audit_events = [
        _card_lifecycle_append_result_audit_event_preview_from_transaction_run(
            transaction_run,
            transaction_run_item,
        )
        for transaction_run_item in transaction_run.get("transaction_runs") or []
    ]
    return {
        **transaction_run,
        "append_result_audit_mode": "preview_only",
        "append_result_audit_status": "previewed",
        "append_result_audit_event_status": "preview_only",
        "audit_event_append_status": "not_appended",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_write_audit_events": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "append_result_audit_event_count": len(audit_events),
        "append_result_audit_events": audit_events,
        "block_reasons": [
            "append_result_audit_preview_only",
            "repository_transaction_disabled",
            "idempotency_store_write_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "append_result_audit_event_persistence_contract",
            "retry_and_replay_conflict_resolution",
            "enabled_repository_transaction_commit",
            "idempotency_store_write_contract",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_retry_replay_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    audit_preview = _llm_card_lifecycle_append_result_audit_preview_from_record(
        record,
        {
            "mode": "preview_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    event_index = _existing_events_by_id(record)
    idempotency_index = _existing_events_by_idempotency_key(record)
    checks = [
        _card_lifecycle_retry_replay_check_from_audit_preview(
            audit_event,
            event_index,
            idempotency_index,
            str(audit_preview.get("request_id", "")),
            str(audit_preview.get("request_draft_event_id", "")),
        )
        for audit_event in audit_preview.get("append_result_audit_events") or []
    ]
    _mark_partial_replay_checks(checks)
    resolution_status = _retry_replay_resolution_status(checks)
    return {
        **audit_preview,
        "retry_replay_preflight_mode": "preflight_only",
        "retry_replay_preflight_status": "analyzed",
        "retry_replay_resolution_status": resolution_status,
        "retry_replay_check_count": len(checks),
        "retry_replay_checks": checks,
        "safe_to_replay_existing_events": bool(checks)
        and all(
            check["resolution_status"] == "safe_replay_same_event"
            for check in checks
        ),
        "safe_to_mutate_events": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "block_reasons": [
            "retry_replay_preflight_only",
            "repository_transaction_disabled",
            "idempotency_store_write_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "enabled_retry_replay_resolution_policy",
            "repository_transaction_commit_contract",
            "idempotency_store_write_contract",
            "append_result_audit_event_persistence_contract",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_event_serializer_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    preflight = _llm_card_lifecycle_append_preflight_dry_run_from_record(
        record,
        {
            "mode": "dry_run_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    preview_events_by_id = {
        str(event.get("event_id", "")): event
        for event in preflight.get("preview_events") or []
    }
    serialized_events = [
        _card_lifecycle_serialized_append_event_from_plan_item(
            preflight,
            append_plan_item,
            preview_events_by_id.get(
                str(append_plan_item.get("preview_event_id", "")),
                {},
            ),
        )
        for append_plan_item in preflight.get("append_plan") or []
    ]
    blocked = any(
        event["serialization_status"] == "blocked_by_preflight"
        for event in serialized_events
    )
    return {
        **preflight,
        "append_event_serializer_mode": "dry_run_only",
        "append_event_serializer_status": "serialized",
        "append_event_serialization_status": (
            "blocked_by_preflight" if blocked else "would_serialize_if_enabled"
        ),
        "append_event_count": len(serialized_events),
        "serialized_append_events": serialized_events,
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "block_reasons": [
            "append_event_serializer_dry_run_only",
            "event_mutation_disabled",
            "idempotency_store_write_disabled",
        ],
        "next_required_decisions": [
            "repository_append_transaction",
            "idempotency_store_write_contract",
            "append_result_audit_event",
            "retry_and_replay_conflict_resolution",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_mutation_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    serializer_run = _llm_card_lifecycle_append_event_serializer_dry_run_from_record(
        record,
        {
            "mode": "dry_run_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    checks = [
        _card_lifecycle_append_mutation_preflight_check_from_serialized_event(
            serialized_event
        )
        for serialized_event in serializer_run.get("serialized_append_events") or []
    ]
    blocked = any(
        check["mutation_preflight_check_status"]
        == "blocked_by_serializer_preflight"
        for check in checks
    )
    return {
        **serializer_run,
        "append_mutation_preflight_mode": "preflight_only",
        "append_mutation_preflight_status": "analyzed",
        "append_mutation_readiness_status": (
            "blocked_by_serializer_preflight"
            if blocked
            else "blocked_until_enabled"
        ),
        "mutation_preflight_check_count": len(checks),
        "mutation_preflight_checks": checks,
        "repository_transaction_status": "not_started",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_mutate_events": False,
        "safe_to_commit_transaction": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "block_reasons": [
            "append_mutation_preflight_only",
            "repository_transaction_disabled",
            "idempotency_store_write_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "enabled_repository_transaction_commit",
            "idempotency_store_write_contract",
            "append_result_audit_event_persistence_contract",
            "enabled_retry_replay_resolution_policy",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_transaction_commit_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    mutation_preflight = _llm_card_lifecycle_append_mutation_preflight_from_record(
        record,
        {
            "mode": "preflight_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    retry_replay_preflight = _llm_card_lifecycle_retry_replay_preflight_from_record(
        record,
        {
            "mode": "preflight_only",
            "request_id": str(payload["request_id"]),
            "candidate_response": dict(payload["candidate_response"]),
        },
    )
    retry_checks_by_future_event_id = {
        str(check.get("future_event_id", "")): check
        for check in retry_replay_preflight.get("retry_replay_checks") or []
    }
    checks = [
        _card_lifecycle_append_transaction_commit_preflight_check(
            mutation_check,
            retry_checks_by_future_event_id.get(
                str(mutation_check.get("future_event_id", "")),
                {},
            ),
        )
        for mutation_check in mutation_preflight.get("mutation_preflight_checks") or []
    ]
    return {
        **retry_replay_preflight,
        **mutation_preflight,
        "retry_replay_preflight_mode": retry_replay_preflight.get(
            "retry_replay_preflight_mode"
        ),
        "retry_replay_preflight_status": retry_replay_preflight.get(
            "retry_replay_preflight_status"
        ),
        "retry_replay_resolution_status": retry_replay_preflight.get(
            "retry_replay_resolution_status"
        ),
        "retry_replay_check_count": retry_replay_preflight.get(
            "retry_replay_check_count"
        ),
        "retry_replay_checks": retry_replay_preflight.get(
            "retry_replay_checks",
            [],
        ),
        "safe_to_replay_existing_events": retry_replay_preflight.get(
            "safe_to_replay_existing_events",
            False,
        ),
        "append_transaction_commit_preflight_mode": "preflight_only",
        "append_transaction_commit_preflight_status": "analyzed",
        "transaction_commit_readiness_status": (
            _transaction_commit_readiness_status(
                checks,
                mutation_preflight,
                retry_replay_preflight,
            )
        ),
        "transaction_commit_preflight_check_count": len(checks),
        "transaction_commit_preflight_checks": checks,
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_mutate_events": False,
        "safe_to_append_events": False,
        "safe_to_write_idempotency_store": False,
        "safe_to_write_audit_events": False,
        "safe_to_create_card": False,
        "block_reasons": [
            "append_transaction_commit_preflight_only",
            "repository_transaction_commit_disabled",
            "idempotency_store_write_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "enabled_repository_transaction_commit",
            "idempotency_store_write_contract",
            "append_result_audit_event_persistence_contract",
            "enabled_retry_replay_resolution_policy",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_idempotency_store_write_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    transaction_commit_preflight = (
        _llm_card_lifecycle_append_transaction_commit_preflight_from_record(
            record,
            {
                "mode": "preflight_only",
                "request_id": str(payload["request_id"]),
                "candidate_response": dict(payload["candidate_response"]),
            },
        )
    )
    transaction_commit_readiness_status = str(
        transaction_commit_preflight.get(
            "transaction_commit_readiness_status",
            "",
        )
    )
    readiness_status = _idempotency_store_write_readiness_status(
        transaction_commit_readiness_status
    )
    checks = [
        _card_lifecycle_append_idempotency_store_write_preflight_check(
            transaction_check,
            transaction_commit_readiness_status,
            readiness_status,
        )
        for transaction_check in transaction_commit_preflight.get(
            "transaction_commit_preflight_checks"
        )
        or []
    ]
    return {
        **transaction_commit_preflight,
        "idempotency_store_write_preflight_mode": "preflight_only",
        "idempotency_store_write_preflight_status": "analyzed",
        "idempotency_store_write_readiness_status": readiness_status,
        "idempotency_store_write_preflight_check_count": len(checks),
        "idempotency_store_write_preflight_checks": checks,
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "safe_to_write_idempotency_store": False,
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_mutate_events": False,
        "safe_to_append_events": False,
        "safe_to_write_audit_events": False,
        "safe_to_create_card": False,
        "block_reasons": [
            "idempotency_store_write_preflight_only",
            "idempotency_store_write_disabled",
            "repository_transaction_commit_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "enabled_idempotency_store_write",
            "enabled_repository_transaction_commit",
            "append_result_audit_event_persistence_contract",
            "enabled_retry_replay_resolution_policy",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_append_result_audit_event_persistence_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    idempotency_store_write_preflight = (
        _llm_card_lifecycle_append_idempotency_store_write_preflight_from_record(
            record,
            {
                "mode": "preflight_only",
                "request_id": str(payload["request_id"]),
                "candidate_response": dict(payload["candidate_response"]),
            },
        )
    )
    idempotency_store_write_readiness_status = str(
        idempotency_store_write_preflight.get(
            "idempotency_store_write_readiness_status",
            "",
        )
    )
    readiness_status = _append_result_audit_event_persistence_readiness_status(
        idempotency_store_write_readiness_status
    )
    audit_events_by_future_event_id = {
        str(audit_event.get("future_event_id", "")): audit_event
        for audit_event in idempotency_store_write_preflight.get(
            "append_result_audit_events"
        )
        or []
    }
    checks = [
        _card_lifecycle_append_result_audit_event_persistence_preflight_check(
            idempotency_check,
            audit_events_by_future_event_id.get(
                str(idempotency_check.get("future_event_id", "")),
                {},
            ),
            idempotency_store_write_readiness_status,
            readiness_status,
        )
        for idempotency_check in idempotency_store_write_preflight.get(
            "idempotency_store_write_preflight_checks"
        )
        or []
    ]
    return {
        **idempotency_store_write_preflight,
        "append_result_audit_event_persistence_preflight_mode": "preflight_only",
        "append_result_audit_event_persistence_preflight_status": "analyzed",
        "append_result_audit_event_persistence_readiness_status": readiness_status,
        "append_result_audit_event_persistence_preflight_check_count": len(checks),
        "append_result_audit_event_persistence_preflight_checks": checks,
        "audit_event_append_status": "not_appended",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "safe_to_persist_append_result_audit_event": False,
        "safe_to_write_audit_events": False,
        "safe_to_write_idempotency_store": False,
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_mutate_events": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
        "block_reasons": [
            "append_result_audit_event_persistence_preflight_only",
            "append_result_audit_event_persistence_disabled",
            "idempotency_store_write_disabled",
            "repository_transaction_commit_disabled",
            "event_mutation_disabled",
        ],
        "next_required_decisions": [
            "enabled_append_result_audit_event_persistence",
            "enabled_idempotency_store_write",
            "enabled_repository_transaction_commit",
            "enabled_retry_replay_resolution_policy",
            "enabled_card_lifecycle_mutation",
        ],
    }


def _llm_card_lifecycle_readiness_summary_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    source_preflight = (
        _llm_card_lifecycle_append_result_audit_event_persistence_preflight_from_record(
            record,
            {
                "mode": "preflight_only",
                "request_id": str(payload["request_id"]),
                "candidate_response": dict(payload["candidate_response"]),
            },
        )
    )
    readiness_status = str(
        source_preflight.get(
            "append_result_audit_event_persistence_readiness_status",
            "",
        )
    )
    phases = _card_lifecycle_readiness_summary_phases(source_preflight)
    block_reasons = _card_lifecycle_readiness_summary_block_reasons(
        readiness_status,
        source_preflight,
    )
    next_required_decisions = _card_lifecycle_readiness_summary_next_decisions(
        readiness_status,
        source_preflight,
    )
    return {
        "session_id": str(source_preflight.get("session_id", "")),
        "source": str(source_preflight.get("source", "")),
        "trace_kind": str(source_preflight.get("trace_kind", "")),
        "request_id": str(source_preflight.get("request_id", payload["request_id"])),
        "request_draft_event_id": str(
            source_preflight.get("request_draft_event_id", "")
        ),
        "target_candidate_id": str(
            source_preflight.get("target_candidate_id", "")
        ),
        "future_lifecycle_status": str(
            source_preflight.get("future_lifecycle_status", "")
        ),
        "would_append_event_types_if_enabled": list(
            source_preflight.get("would_append_event_types_if_enabled") or []
        ),
        "card_lifecycle_readiness_summary_mode": "summary_only",
        "card_lifecycle_readiness_summary_status": "summarized",
        "card_lifecycle_overall_readiness_status": readiness_status,
        "source_preflight_kind": (
            "append_result_audit_event_persistence_preflight"
        ),
        "source_preflight_endpoint": (
            "POST /live/asr/sessions/{session_id}/"
            "llm-card-lifecycle-append-result-audit-event-persistence-preflights"
        ),
        "source_preflight_mode": "preflight_only",
        "source_preflight_status": str(
            source_preflight.get(
                "append_result_audit_event_persistence_preflight_status",
                "",
            )
        ),
        "source_readiness_status": readiness_status,
        "source_check_count": int(
            source_preflight.get(
                "append_result_audit_event_persistence_preflight_check_count",
                0,
            )
            or 0
        ),
        "card_lifecycle_summary_phase_count": len(phases),
        "card_lifecycle_summary_phases": phases,
        "card_lifecycle_block_reasons": block_reasons,
        "card_lifecycle_next_required_decisions": next_required_decisions,
        "llm_call_status": str(source_preflight.get("llm_call_status", "not_called")),
        "credentials_status": str(
            source_preflight.get("credentials_status", "not_read")
        ),
        "config_source_status": str(
            source_preflight.get("config_source_status", "not_read")
        ),
        "cost_status": str(source_preflight.get("cost_status", "not_estimated")),
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "card_lifecycle_safe_to_execute_llm": False,
        "card_lifecycle_safe_to_create_card": False,
        "card_lifecycle_safe_to_append_events": False,
        "card_lifecycle_safe_to_mutate_events": False,
        "card_lifecycle_safe_to_begin_transaction": False,
        "card_lifecycle_safe_to_commit_transaction": False,
        "card_lifecycle_safe_to_write_idempotency_store": False,
        "card_lifecycle_safe_to_persist_append_result_audit_event": False,
    }


def _card_lifecycle_readiness_summary_phases(
    source_preflight: dict[str, Any],
) -> list[dict[str, Any]]:
    phase_specs = [
        (
            "card_lifecycle_preview",
            "lifecycle_preview_status",
            "lifecycle_preview_mode",
            "dry_run",
            "dry_run_only",
            "lifecycle_preview_check_count",
        ),
        (
            "append_preflight",
            "append_preflight_status",
            "append_preflight_mode",
            "preflight",
            "dry_run_only",
            "append_plan_count",
        ),
        (
            "append_disabled_run",
            "append_run_status",
            "append_run_mode",
            "disabled_run",
            "disabled",
            "append_run_count",
        ),
        (
            "append_repository_dry_run",
            "repository_dry_run_status",
            "repository_dry_run_mode",
            "dry_run",
            "dry_run_only",
            "repository_append_count",
        ),
        (
            "append_transaction_disabled_run",
            "transaction_run_status",
            "transaction_run_mode",
            "disabled_run",
            "disabled",
            "transaction_run_count",
        ),
        (
            "append_result_audit_preview",
            "append_result_audit_status",
            "append_result_audit_mode",
            "preview",
            "preview_only",
            "append_result_audit_event_count",
        ),
        (
            "retry_replay_preflight",
            "retry_replay_resolution_status",
            "retry_replay_preflight_mode",
            "preflight",
            "preflight_only",
            "retry_replay_check_count",
        ),
        (
            "append_event_serializer_dry_run",
            "append_event_serialization_status",
            "append_event_serializer_mode",
            "dry_run",
            "dry_run_only",
            "append_event_count",
        ),
        (
            "append_mutation_preflight",
            "append_mutation_readiness_status",
            "append_mutation_preflight_mode",
            "preflight",
            "preflight_only",
            "mutation_preflight_check_count",
        ),
        (
            "append_transaction_commit_preflight",
            "transaction_commit_readiness_status",
            "append_transaction_commit_preflight_mode",
            "preflight",
            "preflight_only",
            "transaction_commit_preflight_check_count",
        ),
        (
            "append_idempotency_store_write_preflight",
            "idempotency_store_write_readiness_status",
            "idempotency_store_write_preflight_mode",
            "preflight",
            "preflight_only",
            "idempotency_store_write_preflight_check_count",
        ),
        (
            "append_result_audit_event_persistence_preflight",
            "append_result_audit_event_persistence_readiness_status",
            "append_result_audit_event_persistence_preflight_mode",
            "preflight",
            "preflight_only",
            "append_result_audit_event_persistence_preflight_check_count",
        ),
    ]
    phases = []
    for (
        phase_id,
        source_status_field,
        source_mode_field,
        phase_kind,
        write_boundary_status,
        count_field,
    ) in phase_specs:
        source_status_value = str(source_preflight.get(source_status_field, ""))
        phases.append(
            {
                "phase_id": phase_id,
                "phase_status": source_status_value,
                "phase_mode": str(source_preflight.get(source_mode_field, "")),
                "phase_kind": phase_kind,
                "write_boundary_status": write_boundary_status,
                "item_count": int(source_preflight.get(count_field, 0) or 0),
                "safe_to_write": False,
                "source_status_field": source_status_field,
                "source_status_value": source_status_value,
            }
        )
    return phases


def _card_lifecycle_readiness_summary_block_reasons(
    readiness_status: str,
    source_preflight: dict[str, Any],
) -> list[str]:
    if readiness_status == "safe_replay_existing_events":
        return [
            "safe_replay_existing_events_requires_no_new_writes",
            "card_lifecycle_summary_response_only",
            "event_mutation_disabled",
        ]
    if readiness_status == "blocked_by_partial_replay":
        return [
            "partial_replay_blocks_card_lifecycle_summary",
            "missing_tail_lifecycle_events_must_not_be_written",
            "event_mutation_disabled",
        ]
    if readiness_status == "blocked_by_retry_replay_conflict":
        return [
            "retry_replay_conflict_blocks_card_lifecycle_summary",
            "event_mutation_disabled",
        ]
    if readiness_status == "blocked_by_idempotency_store_write_preflight":
        return [
            "idempotency_store_write_preflight_blocks_card_lifecycle_summary",
            "event_mutation_disabled",
        ]
    reasons = list(source_preflight.get("block_reasons") or [])
    if reasons:
        return reasons
    return [
        "card_lifecycle_summary_response_only",
        "event_mutation_disabled",
    ]


def _card_lifecycle_readiness_summary_next_decisions(
    readiness_status: str,
    source_preflight: dict[str, Any],
) -> list[str]:
    decisions = list(source_preflight.get("next_required_decisions") or [])
    if readiness_status == "safe_replay_existing_events":
        decisions = [
            "safe_replay_existing_events_requires_no_new_writes",
            *decisions,
        ]
    for decision in [
        "enabled_append_result_audit_event_persistence",
        "enabled_idempotency_store_write",
        "enabled_repository_transaction_commit",
        "enabled_retry_replay_resolution_policy",
        "enabled_card_lifecycle_mutation",
    ]:
        if decision not in decisions:
            decisions.append(decision)
    return list(dict.fromkeys(str(decision) for decision in decisions))


def _card_lifecycle_serialized_append_event_from_plan_item(
    serializer_run: dict[str, Any],
    append_plan_item: dict[str, Any],
    preview_event: dict[str, Any],
) -> dict[str, Any]:
    future_event_id = str(append_plan_item.get("future_event_id", ""))
    event_type = str(append_plan_item.get("event_type", ""))
    idempotency_key = str(append_plan_item.get("idempotency_key", ""))
    append_status = str(append_plan_item.get("append_status", ""))
    conflict_status = str(append_plan_item.get("conflict_status", ""))
    payload = dict(preview_event.get("payload") or {})
    card_id = str(payload.get("card_id", "")).strip()
    if not card_id:
        card_id = "suggestion_card_preview"
    payload["idempotency_key"] = idempotency_key
    serialization_status = (
        "would_serialize_if_enabled"
        if append_status == "would_append_once_if_enabled"
        and conflict_status == "none"
        else "blocked_by_preflight"
    )
    return {
        "serializer_result_id": (
            "asr_card_lifecycle_append_event_serializer_"
            f"{_run_id_token(future_event_id)}"
        ),
        "serialization_status": serialization_status,
        "id": future_event_id,
        "event_id": future_event_id,
        "event_type": event_type,
        "sequence": append_plan_item.get("would_append_sequence"),
        "at_ms": preview_event.get("at_ms", append_plan_item.get("at_ms")),
        "source": str(serializer_run.get("source", "")),
        "trace_kind": "live_event",
        "idempotency_key": idempotency_key,
        "payload": payload,
        "preview_event_id": str(append_plan_item.get("preview_event_id", "")),
        "future_event_id": future_event_id,
        "append_status": append_status,
        "conflict_status": conflict_status,
        "would_append_sequence": append_plan_item.get("would_append_sequence"),
        "would_append_after_sequence": append_plan_item.get(
            "would_append_after_sequence"
        ),
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_append_event": False,
        "safe_to_create_card": False,
        "card_id": card_id,
    }


def _card_lifecycle_append_mutation_preflight_check_from_serialized_event(
    serialized_event: dict[str, Any],
) -> dict[str, Any]:
    future_event_id = str(serialized_event.get("future_event_id", ""))
    serialization_status = str(serialized_event.get("serialization_status", ""))
    check_status = (
        "blocked_until_enabled"
        if serialization_status == "would_serialize_if_enabled"
        else "blocked_by_serializer_preflight"
    )
    return {
        "mutation_preflight_check_id": (
            "asr_card_lifecycle_append_mutation_preflight_"
            f"{_run_id_token(future_event_id)}"
        ),
        "mutation_preflight_check_status": check_status,
        "serializer_result_id": str(serialized_event.get("serializer_result_id", "")),
        "serialization_status": serialization_status,
        "event_type": str(serialized_event.get("event_type", "")),
        "future_event_id": future_event_id,
        "serialized_event_id": str(serialized_event.get("event_id", "")),
        "preview_event_id": str(serialized_event.get("preview_event_id", "")),
        "idempotency_key": str(serialized_event.get("idempotency_key", "")),
        "would_append_sequence": serialized_event.get("would_append_sequence"),
        "would_append_after_sequence": serialized_event.get(
            "would_append_after_sequence"
        ),
        "append_status": str(serialized_event.get("append_status", "")),
        "conflict_status": str(serialized_event.get("conflict_status", "")),
        "repository_transaction_status": "not_started",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_mutate_event": False,
        "safe_to_commit_transaction": False,
        "safe_to_append_event": False,
        "safe_to_create_card": False,
    }


def _card_lifecycle_append_transaction_commit_preflight_check(
    mutation_check: dict[str, Any],
    retry_replay_check: dict[str, Any],
) -> dict[str, Any]:
    future_event_id = str(mutation_check.get("future_event_id", ""))
    retry_resolution_status = str(
        retry_replay_check.get("resolution_status")
        or retry_replay_check.get("retry_replay_check_status", "")
    )
    return {
        "transaction_commit_preflight_check_id": (
            "asr_card_lifecycle_append_transaction_commit_preflight_"
            f"{_run_id_token(future_event_id)}"
        ),
        "transaction_commit_preflight_check_status": (
            _transaction_commit_preflight_check_status(
                str(mutation_check.get("mutation_preflight_check_status", "")),
                retry_resolution_status,
            )
        ),
        "mutation_preflight_check_id": str(
            mutation_check.get("mutation_preflight_check_id", "")
        ),
        "mutation_preflight_check_status": str(
            mutation_check.get("mutation_preflight_check_status", "")
        ),
        "retry_replay_check_id": str(
            retry_replay_check.get("retry_replay_check_id", "")
        ),
        "retry_replay_check_status": str(
            retry_replay_check.get("retry_replay_check_status", "")
        ),
        "retry_replay_resolution_status": retry_resolution_status,
        "serializer_result_id": str(mutation_check.get("serializer_result_id", "")),
        "serialization_status": str(mutation_check.get("serialization_status", "")),
        "event_type": str(mutation_check.get("event_type", "")),
        "future_event_id": future_event_id,
        "serialized_event_id": str(mutation_check.get("serialized_event_id", "")),
        "preview_event_id": str(mutation_check.get("preview_event_id", "")),
        "idempotency_key": str(mutation_check.get("idempotency_key", "")),
        "transaction_idempotency_key": str(
            retry_replay_check.get("transaction_idempotency_key", "")
        ),
        "would_append_sequence": mutation_check.get("would_append_sequence"),
        "would_append_after_sequence": mutation_check.get(
            "would_append_after_sequence"
        ),
        "append_status": str(mutation_check.get("append_status", "")),
        "conflict_status": str(mutation_check.get("conflict_status", "")),
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_append_event": False,
        "safe_to_write_idempotency_store": False,
        "safe_to_write_audit_event": False,
        "safe_to_create_card": False,
    }


def _card_lifecycle_append_idempotency_store_write_preflight_check(
    transaction_check: dict[str, Any],
    transaction_commit_readiness_status: str,
    idempotency_store_write_readiness_status: str,
) -> dict[str, Any]:
    future_event_id = str(transaction_check.get("future_event_id", ""))
    event_token = _run_id_token(future_event_id)
    check_status = _idempotency_store_write_preflight_check_status(
        str(transaction_check.get("transaction_commit_preflight_check_status", "")),
        idempotency_store_write_readiness_status,
    )
    future_record_status = _future_idempotency_record_status(check_status)
    idempotency_key = str(transaction_check.get("idempotency_key", ""))
    return {
        "idempotency_store_write_preflight_check_id": (
            "asr_card_lifecycle_append_idempotency_store_write_preflight_"
            f"{event_token}"
        ),
        "idempotency_store_write_preflight_check_status": check_status,
        "future_idempotency_record_id": (
            "asr_card_lifecycle_append_idempotency_record_"
            f"{event_token}"
        ),
        "future_idempotency_record_key": idempotency_key,
        "future_idempotency_record_status": future_record_status,
        "idempotency_store_write_reason": (
            _idempotency_store_write_reason(check_status)
        ),
        "transaction_commit_readiness_status": transaction_commit_readiness_status,
        "idempotency_store_write_readiness_status": (
            idempotency_store_write_readiness_status
        ),
        "transaction_commit_preflight_check_id": str(
            transaction_check.get("transaction_commit_preflight_check_id", "")
        ),
        "transaction_commit_preflight_check_status": str(
            transaction_check.get("transaction_commit_preflight_check_status", "")
        ),
        "mutation_preflight_check_id": str(
            transaction_check.get("mutation_preflight_check_id", "")
        ),
        "mutation_preflight_check_status": str(
            transaction_check.get("mutation_preflight_check_status", "")
        ),
        "retry_replay_check_id": str(
            transaction_check.get("retry_replay_check_id", "")
        ),
        "retry_replay_check_status": str(
            transaction_check.get("retry_replay_check_status", "")
        ),
        "retry_replay_resolution_status": str(
            transaction_check.get("retry_replay_resolution_status", "")
        ),
        "serializer_result_id": str(transaction_check.get("serializer_result_id", "")),
        "serialization_status": str(transaction_check.get("serialization_status", "")),
        "event_type": str(transaction_check.get("event_type", "")),
        "future_event_id": future_event_id,
        "serialized_event_id": str(transaction_check.get("serialized_event_id", "")),
        "preview_event_id": str(transaction_check.get("preview_event_id", "")),
        "idempotency_key": idempotency_key,
        "transaction_idempotency_key": str(
            transaction_check.get("transaction_idempotency_key", "")
        ),
        "would_append_sequence": transaction_check.get("would_append_sequence"),
        "would_append_after_sequence": transaction_check.get(
            "would_append_after_sequence"
        ),
        "append_status": str(transaction_check.get("append_status", "")),
        "conflict_status": str(transaction_check.get("conflict_status", "")),
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_write_idempotency_store": False,
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_append_event": False,
        "safe_to_write_audit_event": False,
        "safe_to_create_card": False,
    }


def _card_lifecycle_append_result_audit_event_persistence_preflight_check(
    idempotency_check: dict[str, Any],
    audit_event: dict[str, Any],
    idempotency_store_write_readiness_status: str,
    append_result_audit_event_persistence_readiness_status: str,
) -> dict[str, Any]:
    future_event_id = str(idempotency_check.get("future_event_id", ""))
    event_token = _run_id_token(future_event_id)
    check_status = _append_result_audit_event_persistence_preflight_check_status(
        str(
            idempotency_check.get(
                "idempotency_store_write_preflight_check_status",
                "",
            )
        ),
        append_result_audit_event_persistence_readiness_status,
    )
    future_audit_event_status = _future_append_result_audit_event_status(
        check_status
    )
    audit_event_id = str(audit_event.get("audit_event_id", ""))
    audit_event_type = str(
        audit_event.get("audit_event_type", "card_lifecycle_append_result")
    )
    return {
        "append_result_audit_event_persistence_preflight_check_id": (
            "asr_card_lifecycle_append_result_audit_event_persistence_preflight_"
            f"{event_token}"
        ),
        "append_result_audit_event_persistence_preflight_check_status": check_status,
        "future_append_result_audit_event_id": audit_event_id,
        "future_append_result_audit_event_type": audit_event_type,
        "future_append_result_audit_event_status": future_audit_event_status,
        "append_result_audit_event_persistence_reason": (
            _append_result_audit_event_persistence_reason(check_status)
        ),
        "audit_event_id": audit_event_id,
        "audit_idempotency_key": str(audit_event.get("audit_idempotency_key", "")),
        "append_result_audit_event_status": str(
            audit_event.get("audit_event_status", "")
        ),
        "audit_result_status": str(audit_event.get("audit_result_status", "")),
        "transaction_run_id": str(audit_event.get("transaction_run_id", "")),
        "transaction_run_status": str(
            audit_event.get("transaction_run_status", "")
        ),
        "append_run_id": str(audit_event.get("append_run_id", "")),
        "repository_result_id": str(audit_event.get("repository_result_id", "")),
        "repository_result_status": str(
            audit_event.get("repository_result_status", "")
        ),
        "repository_idempotency_key": str(
            audit_event.get("repository_idempotency_key", "")
        ),
        "preflight_append_status": str(
            audit_event.get("preflight_append_status", "")
        ),
        "preflight_conflict_status": str(
            audit_event.get("preflight_conflict_status", "")
        ),
        "audit_repository_transaction_status": str(
            audit_event.get("repository_transaction_status", "")
        ),
        "repository_write_status": str(
            audit_event.get("repository_write_status", "")
        ),
        "transaction_write_status": str(
            audit_event.get("transaction_write_status", "")
        ),
        "idempotency_store_write_preflight_check_id": str(
            idempotency_check.get("idempotency_store_write_preflight_check_id", "")
        ),
        "idempotency_store_write_preflight_check_status": str(
            idempotency_check.get(
                "idempotency_store_write_preflight_check_status",
                "",
            )
        ),
        "idempotency_store_write_readiness_status": (
            idempotency_store_write_readiness_status
        ),
        "future_idempotency_record_status": str(
            idempotency_check.get("future_idempotency_record_status", "")
        ),
        "transaction_commit_readiness_status": str(
            idempotency_check.get("transaction_commit_readiness_status", "")
        ),
        "transaction_commit_preflight_check_id": str(
            idempotency_check.get("transaction_commit_preflight_check_id", "")
        ),
        "transaction_commit_preflight_check_status": str(
            idempotency_check.get("transaction_commit_preflight_check_status", "")
        ),
        "mutation_preflight_check_id": str(
            idempotency_check.get("mutation_preflight_check_id", "")
        ),
        "mutation_preflight_check_status": str(
            idempotency_check.get("mutation_preflight_check_status", "")
        ),
        "retry_replay_check_id": str(
            idempotency_check.get("retry_replay_check_id", "")
        ),
        "retry_replay_check_status": str(
            idempotency_check.get("retry_replay_check_status", "")
        ),
        "retry_replay_resolution_status": str(
            idempotency_check.get("retry_replay_resolution_status", "")
        ),
        "serializer_result_id": str(
            idempotency_check.get("serializer_result_id", "")
        ),
        "serialization_status": str(
            idempotency_check.get("serialization_status", "")
        ),
        "event_type": str(idempotency_check.get("event_type", "")),
        "future_event_id": future_event_id,
        "serialized_event_id": str(
            idempotency_check.get("serialized_event_id", "")
        ),
        "preview_event_id": str(idempotency_check.get("preview_event_id", "")),
        "idempotency_key": str(idempotency_check.get("idempotency_key", "")),
        "transaction_idempotency_key": str(
            idempotency_check.get("transaction_idempotency_key", "")
        ),
        "would_append_sequence": idempotency_check.get("would_append_sequence"),
        "would_append_after_sequence": idempotency_check.get(
            "would_append_after_sequence"
        ),
        "append_status": str(idempotency_check.get("append_status", "")),
        "conflict_status": str(idempotency_check.get("conflict_status", "")),
        "repository_transaction_status": "not_started",
        "repository_transaction_commit_status": "not_committed",
        "repository_transaction_rollback_status": "not_started",
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_persist_append_result_audit_event": False,
        "safe_to_write_audit_event": False,
        "safe_to_write_audit_events": False,
        "safe_to_append_event": False,
        "safe_to_write_idempotency_store": False,
        "safe_to_begin_transaction": False,
        "safe_to_commit_transaction": False,
        "safe_to_rollback_transaction": False,
        "safe_to_mutate_events": False,
        "safe_to_append_events": False,
        "safe_to_create_card": False,
    }


def _transaction_commit_preflight_check_status(
    mutation_preflight_status: str,
    retry_resolution_status: str,
) -> str:
    if retry_resolution_status == "safe_replay_same_event":
        return "safe_replay_existing_event"
    if retry_resolution_status == "blocked_partial_replay":
        return "blocked_by_partial_replay"
    if retry_resolution_status in {
        "blocked_mismatched_replay",
        "blocked_existing_idempotency_key",
    }:
        return "blocked_by_retry_replay_conflict"
    if mutation_preflight_status == "blocked_by_serializer_preflight":
        return "blocked_by_mutation_preflight"
    return "blocked_until_enabled"


def _idempotency_store_write_readiness_status(
    transaction_commit_readiness_status: str,
) -> str:
    if transaction_commit_readiness_status == "safe_replay_existing_events":
        return "safe_replay_existing_events"
    if transaction_commit_readiness_status == "blocked_by_partial_replay":
        return "blocked_by_partial_replay"
    if transaction_commit_readiness_status == "blocked_by_retry_replay_conflict":
        return "blocked_by_retry_replay_conflict"
    if transaction_commit_readiness_status == "blocked_until_enabled":
        return "blocked_until_enabled"
    return "blocked_by_transaction_commit_preflight"


def _append_result_audit_event_persistence_readiness_status(
    idempotency_store_write_readiness_status: str,
) -> str:
    if idempotency_store_write_readiness_status == "safe_replay_existing_events":
        return "safe_replay_existing_events"
    if idempotency_store_write_readiness_status == "blocked_by_partial_replay":
        return "blocked_by_partial_replay"
    if (
        idempotency_store_write_readiness_status
        == "blocked_by_retry_replay_conflict"
    ):
        return "blocked_by_retry_replay_conflict"
    if idempotency_store_write_readiness_status == "blocked_until_enabled":
        return "blocked_until_enabled"
    return "blocked_by_idempotency_store_write_preflight"


def _idempotency_store_write_preflight_check_status(
    transaction_commit_preflight_check_status: str,
    top_level_readiness_status: str,
) -> str:
    if transaction_commit_preflight_check_status == "safe_replay_existing_event":
        return "write_not_required_for_safe_replay"
    if transaction_commit_preflight_check_status == "blocked_by_partial_replay":
        return "blocked_by_partial_replay"
    if (
        transaction_commit_preflight_check_status
        == "blocked_by_retry_replay_conflict"
    ):
        return "blocked_by_retry_replay_conflict"
    if (
        transaction_commit_preflight_check_status == "blocked_until_enabled"
        and top_level_readiness_status == "blocked_until_enabled"
    ):
        return "blocked_until_enabled"
    return "blocked_by_transaction_commit_preflight"


def _append_result_audit_event_persistence_preflight_check_status(
    idempotency_store_write_preflight_check_status: str,
    top_level_readiness_status: str,
) -> str:
    if (
        idempotency_store_write_preflight_check_status
        == "write_not_required_for_safe_replay"
    ):
        return "persistence_not_required_for_safe_replay"
    if idempotency_store_write_preflight_check_status == "blocked_by_partial_replay":
        return "blocked_by_partial_replay"
    if (
        idempotency_store_write_preflight_check_status
        == "blocked_by_retry_replay_conflict"
    ):
        return "blocked_by_retry_replay_conflict"
    if (
        idempotency_store_write_preflight_check_status == "blocked_until_enabled"
        and top_level_readiness_status == "blocked_until_enabled"
    ):
        return "blocked_until_enabled"
    return "blocked_by_idempotency_store_write_preflight"


def _future_idempotency_record_status(check_status: str) -> str:
    if check_status == "blocked_until_enabled":
        return "would_write_if_enabled"
    if check_status == "write_not_required_for_safe_replay":
        return "not_required_existing_replay"
    return "blocked"


def _future_append_result_audit_event_status(check_status: str) -> str:
    if check_status == "blocked_until_enabled":
        return "would_persist_if_enabled"
    if check_status == "persistence_not_required_for_safe_replay":
        return "not_required_existing_replay"
    return "blocked"


def _idempotency_store_write_reason(check_status: str) -> str:
    if check_status == "blocked_until_enabled":
        return "fresh_append_requires_idempotency_record"
    if check_status == "write_not_required_for_safe_replay":
        return "safe_replay_existing_event_requires_no_new_record"
    if check_status == "blocked_by_partial_replay":
        return "partial_replay_blocks_idempotency_record_write"
    if check_status == "blocked_by_retry_replay_conflict":
        return "retry_replay_conflict_blocks_idempotency_record_write"
    return "transaction_commit_preflight_blocks_idempotency_record_write"


def _append_result_audit_event_persistence_reason(check_status: str) -> str:
    if check_status == "blocked_until_enabled":
        return "fresh_append_requires_append_result_audit_event"
    if check_status == "persistence_not_required_for_safe_replay":
        return "safe_replay_existing_event_requires_no_new_audit_event"
    if check_status == "blocked_by_partial_replay":
        return "partial_replay_blocks_append_result_audit_event_persistence"
    if check_status == "blocked_by_retry_replay_conflict":
        return "retry_replay_conflict_blocks_append_result_audit_event_persistence"
    return "idempotency_store_write_preflight_blocks_append_result_audit_event_persistence"


def _transaction_commit_readiness_status(
    checks: list[dict[str, Any]],
    mutation_preflight: dict[str, Any],
    retry_replay_preflight: dict[str, Any],
) -> str:
    retry_resolution_status = str(
        retry_replay_preflight.get("retry_replay_resolution_status", "")
    )
    if retry_resolution_status == "safe_to_replay":
        return "safe_replay_existing_events"
    if retry_resolution_status == "blocked_by_partial_replay":
        return "blocked_by_partial_replay"
    if retry_resolution_status == "blocked_by_conflict":
        return "blocked_by_retry_replay_conflict"
    if (
        str(mutation_preflight.get("append_mutation_readiness_status", ""))
        == "blocked_by_serializer_preflight"
        or any(
            check.get("transaction_commit_preflight_check_status")
            == "blocked_by_mutation_preflight"
            for check in checks
        )
    ):
        return "blocked_by_mutation_preflight"
    return "blocked_until_enabled"


def _card_lifecycle_retry_replay_check_from_audit_preview(
    audit_event: dict[str, Any],
    event_index: dict[str, dict[str, Any]],
    idempotency_index: dict[str, list[dict[str, Any]]],
    expected_request_id: str,
    expected_request_draft_event_id: str,
) -> dict[str, Any]:
    event_type = str(audit_event.get("event_type", ""))
    future_event_id = str(audit_event.get("future_event_id", ""))
    idempotency_key = str(audit_event.get("idempotency_key", ""))
    card_id = (
        future_event_id.split(":", 1)[1]
        if ":" in future_event_id
        else future_event_id
    )
    if not card_id:
        card_id = "suggestion_card_preview"
    event_token = _repository_component_token(event_type)
    card_token = _repository_component_token(card_id)
    existing_event = event_index.get(future_event_id)
    existing_idempotency_events = idempotency_index.get(idempotency_key, [])
    duplicate_idempotency_events = [
        event
        for event in existing_idempotency_events
        if str(event.get("id", "")) != future_event_id
    ]
    existing_idempotency_event = (
        existing_idempotency_events[0] if existing_idempotency_events else None
    )
    resolution_status = "no_existing_append"
    existing_event_match_status = "not_found"
    existing_idempotency_match_status = "not_found"

    if existing_event is not None:
        existing_event_key = _event_idempotency_key(existing_event)
        existing_event_card_id = _event_card_id(existing_event)
        if duplicate_idempotency_events:
            existing_event_match_status = (
                "same_event_id"
                if _event_matches_lifecycle_retry_replay_expectation(
                    existing_event,
                    event_type,
                    idempotency_key,
                    card_id,
                    expected_request_id,
                    expected_request_draft_event_id,
                )
                else "mismatched_event"
            )
            existing_idempotency_match_status = "duplicate_idempotency_key"
            resolution_status = "blocked_existing_idempotency_key"
        elif _event_matches_lifecycle_retry_replay_expectation(
            existing_event,
            event_type,
            idempotency_key,
            card_id,
            expected_request_id,
            expected_request_draft_event_id,
        ):
            existing_event_match_status = "same_event_id"
            existing_idempotency_match_status = "same_idempotency_key"
            resolution_status = "safe_replay_same_event"
        else:
            existing_event_match_status = "mismatched_event"
            existing_idempotency_match_status = (
                "mismatched_idempotency_key"
                if not _event_idempotency_keys_match_expected(
                    existing_event,
                    idempotency_key,
                )
                else "same_idempotency_key"
            )
            resolution_status = "blocked_mismatched_replay"
    elif existing_idempotency_event is not None:
        existing_event_match_status = "not_found"
        existing_idempotency_match_status = "same_idempotency_key_different_event"
        resolution_status = "blocked_existing_idempotency_key"

    existing_match = (
        duplicate_idempotency_events[0]
        if existing_event is None and duplicate_idempotency_events
        else existing_event
        or existing_idempotency_event
        or {}
    )
    return {
        "retry_replay_check_id": (
            "asr_card_lifecycle_retry_replay_preflight_"
            f"{event_token}_{card_token}"
        ),
        "retry_replay_check_status": resolution_status,
        "resolution_status": resolution_status,
        "event_type": event_type,
        "future_event_id": future_event_id,
        "preview_event_id": str(audit_event.get("preview_event_id", "")),
        "append_run_id": str(audit_event.get("append_run_id", "")),
        "transaction_run_id": str(audit_event.get("transaction_run_id", "")),
        "repository_result_id": str(audit_event.get("repository_result_id", "")),
        "audit_event_id": str(audit_event.get("audit_event_id", "")),
        "idempotency_key": idempotency_key,
        "transaction_idempotency_key": str(
            audit_event.get("transaction_idempotency_key", "")
        ),
        "repository_idempotency_key": str(
            audit_event.get("repository_idempotency_key", "")
        ),
        "audit_idempotency_key": str(audit_event.get("audit_idempotency_key", "")),
        "existing_event_match_status": existing_event_match_status,
        "existing_idempotency_match_status": existing_idempotency_match_status,
        "existing_event_id": str(existing_match.get("id", "")),
        "existing_event_type": str(existing_match.get("event_type", "")),
        "existing_idempotency_key": _event_idempotency_key(existing_match),
        "existing_idempotency_conflict_count": len(duplicate_idempotency_events),
        "existing_idempotency_conflict_event_ids": [
            str(event.get("id", "")) for event in duplicate_idempotency_events
        ],
        "existing_card_id": _event_card_id(existing_match),
        "preflight_append_status": str(
            audit_event.get("preflight_append_status", "")
        ),
        "preflight_conflict_status": str(
            audit_event.get("preflight_conflict_status", "")
        ),
        "append_result_audit_event_status": str(
            audit_event.get("audit_event_status", "")
        ),
        "audit_result_status": str(audit_event.get("audit_result_status", "")),
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_replay_event": resolution_status == "safe_replay_same_event",
        "safe_to_append_event": False,
        "safe_to_write_audit_event": False,
    }


def _mark_partial_replay_checks(checks: list[dict[str, Any]]) -> None:
    has_safe_replay = any(
        check["resolution_status"] == "safe_replay_same_event" for check in checks
    )
    has_missing = any(
        check["resolution_status"] == "no_existing_append" for check in checks
    )
    has_conflict = any(
        check["resolution_status"]
        in {"blocked_mismatched_replay", "blocked_existing_idempotency_key"}
        for check in checks
    )
    if not has_safe_replay or not has_missing or has_conflict:
        return
    for check in checks:
        if check["resolution_status"] == "no_existing_append":
            check["resolution_status"] = "blocked_partial_replay"
            check["retry_replay_check_status"] = "blocked_partial_replay"


def _retry_replay_resolution_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("resolution_status", "")) for check in checks}
    if statuses == {"safe_replay_same_event"}:
        return "safe_to_replay"
    if "blocked_partial_replay" in statuses:
        return "blocked_by_partial_replay"
    if statuses & {"blocked_mismatched_replay", "blocked_existing_idempotency_key"}:
        return "blocked_by_conflict"
    return "no_existing_append"


def _existing_events_by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(event.get("id", "")): event
        for event in record.get("events") or []
        if str(event.get("id", ""))
    }


def _existing_events_by_idempotency_key(
    record: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    events_by_key: dict[str, list[dict[str, Any]]] = {}
    for event in record.get("events") or []:
        for key in _event_idempotency_keys(event):
            events_by_key.setdefault(key, []).append(event)
    return events_by_key


def _event_idempotency_keys(event: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    top_level_key = str(event.get("idempotency_key", "")).strip()
    if top_level_key:
        keys.append(top_level_key)
    payload = event.get("payload")
    if isinstance(payload, dict):
        payload_key = str(payload.get("idempotency_key", "")).strip()
        if payload_key:
            keys.append(payload_key)
    return list(dict.fromkeys(keys))


def _event_idempotency_key(event: dict[str, Any]) -> str:
    keys = _event_idempotency_keys(event)
    return keys[0] if keys else ""


def _event_card_id(event: dict[str, Any]) -> str:
    payload = event.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("card_id", "")).strip()
    return ""


def _event_matches_lifecycle_retry_replay_expectation(
    event: dict[str, Any],
    expected_event_type: str,
    expected_idempotency_key: str,
    expected_card_id: str,
    expected_request_id: str,
    expected_request_draft_event_id: str,
) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    if str(event.get("event_type", "")) != expected_event_type:
        return False
    if not _event_idempotency_keys_match_expected(
        event,
        expected_idempotency_key,
    ):
        return False
    if str(payload.get("card_id", "")).strip() != expected_card_id:
        return False
    if str(payload.get("request_id", "")).strip() != expected_request_id:
        return False
    if (
        str(payload.get("request_draft_event_id", "")).strip()
        != expected_request_draft_event_id
    ):
        return False
    if expected_event_type == "suggestion_card":
        card_payload = payload.get("card")
        if not isinstance(card_payload, dict):
            return False
        if str(card_payload.get("id", "")).strip() != expected_card_id:
            return False
    return True


def _event_idempotency_keys_match_expected(
    event: dict[str, Any],
    expected_idempotency_key: str,
) -> bool:
    keys = _event_idempotency_keys(event)
    return bool(keys) and all(key == expected_idempotency_key for key in keys)


def _card_lifecycle_repository_dry_run_result_from_plan_item(
    disabled_run: dict[str, Any],
    append_plan_item: dict[str, Any],
    append_run_item: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(disabled_run.get("session_id", ""))
    request_id = str(disabled_run.get("request_id", ""))
    event_type = str(append_plan_item.get("event_type", ""))
    future_event_id = str(append_plan_item.get("future_event_id", ""))
    conflict_status = str(append_plan_item.get("conflict_status", ""))
    card_id = future_event_id.split(":", 1)[1] if ":" in future_event_id else future_event_id
    if not card_id:
        card_id = "suggestion_card_preview"
    repository_event_token = _repository_component_token(event_type)
    repository_card_token = _repository_component_token(card_id)
    return {
        "repository_result_id": (
            "asr_card_lifecycle_repository_dry_run_"
            f"{repository_event_token}_{repository_card_token}"
        ),
        "repository_result_status": (
            "would_append_if_enabled"
            if conflict_status == "none"
            else "blocked_by_preflight"
        ),
        "event_type": event_type,
        "future_event_id": future_event_id,
        "preview_event_id": str(append_plan_item.get("preview_event_id", "")),
        "append_run_id": str(append_run_item.get("run_id", "")),
        "preflight_append_status": str(append_plan_item.get("append_status", "")),
        "preflight_conflict_status": conflict_status,
        "would_append_sequence": append_plan_item.get("would_append_sequence"),
        "would_append_after_sequence": append_plan_item.get(
            "would_append_after_sequence"
        ),
        "idempotency_key": str(append_plan_item.get("idempotency_key", "")),
        "repository_idempotency_key": (
            "live_asr_card_lifecycle_repository_dry_run:"
            f"{_repository_component_token(session_id)}:"
            f"{_repository_component_token(request_id)}:"
            f"{repository_event_token}:"
            f"{repository_card_token}"
        ),
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "repository_write_status": "dry_run_only",
        "safe_to_append_event": False,
    }


def _card_lifecycle_append_transaction_disabled_run_from_repository_result(
    repository_dry_run: dict[str, Any],
    repository_result: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(repository_dry_run.get("session_id", ""))
    request_id = str(repository_dry_run.get("request_id", ""))
    event_type = str(repository_result.get("event_type", ""))
    future_event_id = str(repository_result.get("future_event_id", ""))
    card_id = future_event_id.split(":", 1)[1] if ":" in future_event_id else future_event_id
    if not card_id:
        card_id = "suggestion_card_preview"
    event_token = _repository_component_token(event_type)
    card_token = _repository_component_token(card_id)
    repository_result_status = str(
        repository_result.get("repository_result_status", "")
    )
    return {
        "transaction_run_id": (
            "asr_card_lifecycle_append_transaction_run_disabled_"
            f"{event_token}_{card_token}"
        ),
        "transaction_run_status": "skipped",
        "skip_reason": (
            "repository_preflight_blocked"
            if repository_result_status == "blocked_by_preflight"
            else "repository_transaction_disabled"
        ),
        "repository_result_id": str(repository_result.get("repository_result_id", "")),
        "repository_result_status": repository_result_status,
        "event_type": event_type,
        "future_event_id": future_event_id,
        "preview_event_id": str(repository_result.get("preview_event_id", "")),
        "append_run_id": str(repository_result.get("append_run_id", "")),
        "idempotency_key": str(repository_result.get("idempotency_key", "")),
        "repository_idempotency_key": str(
            repository_result.get("repository_idempotency_key", "")
        ),
        "transaction_idempotency_key": (
            "live_asr_card_lifecycle_append_transaction_run:disabled:"
            f"{_repository_component_token(session_id)}:"
            f"{_repository_component_token(request_id)}:"
            f"{event_token}:"
            f"{card_token}"
        ),
        "preflight_append_status": str(
            repository_result.get("preflight_append_status", "")
        ),
        "preflight_conflict_status": str(
            repository_result.get("preflight_conflict_status", "")
        ),
        "would_append_sequence": repository_result.get("would_append_sequence"),
        "would_append_after_sequence": repository_result.get(
            "would_append_after_sequence"
        ),
        "repository_write_status": str(
            repository_result.get("repository_write_status", "")
        ),
        "transaction_write_status": "disabled",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_commit_transaction": False,
        "safe_to_append_event": False,
    }


def _card_lifecycle_append_result_audit_event_preview_from_transaction_run(
    transaction_run: dict[str, Any],
    transaction_run_item: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(transaction_run.get("session_id", ""))
    request_id = str(transaction_run.get("request_id", ""))
    event_type = str(transaction_run_item.get("event_type", ""))
    future_event_id = str(transaction_run_item.get("future_event_id", ""))
    card_id = future_event_id.split(":", 1)[1] if ":" in future_event_id else future_event_id
    if not card_id:
        card_id = "suggestion_card_preview"
    event_token = _repository_component_token(event_type)
    card_token = _repository_component_token(card_id)
    skip_reason = str(transaction_run_item.get("skip_reason", ""))
    return {
        "audit_event_id": (
            "asr_card_lifecycle_append_result_audit_preview_"
            f"{event_token}_{card_token}"
        ),
        "audit_event_type": "card_lifecycle_append_result",
        "audit_event_status": "preview_only",
        "audit_result_status": (
            "blocked_by_preflight"
            if skip_reason == "repository_preflight_blocked"
            else "skipped_transaction_disabled"
        ),
        "transaction_run_id": str(transaction_run_item.get("transaction_run_id", "")),
        "transaction_run_status": str(
            transaction_run_item.get("transaction_run_status", "")
        ),
        "transaction_idempotency_key": str(
            transaction_run_item.get("transaction_idempotency_key", "")
        ),
        "repository_result_id": str(
            transaction_run_item.get("repository_result_id", "")
        ),
        "repository_result_status": str(
            transaction_run_item.get("repository_result_status", "")
        ),
        "repository_idempotency_key": str(
            transaction_run_item.get("repository_idempotency_key", "")
        ),
        "event_type": event_type,
        "future_event_id": future_event_id,
        "preview_event_id": str(transaction_run_item.get("preview_event_id", "")),
        "append_run_id": str(transaction_run_item.get("append_run_id", "")),
        "idempotency_key": str(transaction_run_item.get("idempotency_key", "")),
        "audit_idempotency_key": (
            "live_asr_card_lifecycle_append_result_audit_preview:"
            f"{_repository_component_token(session_id)}:"
            f"{_repository_component_token(request_id)}:"
            f"{event_token}:"
            f"{card_token}"
        ),
        "preflight_append_status": str(
            transaction_run_item.get("preflight_append_status", "")
        ),
        "preflight_conflict_status": str(
            transaction_run_item.get("preflight_conflict_status", "")
        ),
        "would_append_sequence": transaction_run_item.get("would_append_sequence"),
        "would_append_after_sequence": transaction_run_item.get(
            "would_append_after_sequence"
        ),
        "repository_transaction_status": "disabled",
        "repository_write_status": str(
            transaction_run_item.get("repository_write_status", "")
        ),
        "transaction_write_status": str(
            transaction_run_item.get("transaction_write_status", "")
        ),
        "event_append_status": "not_appended",
        "audit_event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "idempotency_store_write_status": "not_written",
        "safe_to_write_audit_event": False,
        "safe_to_append_event": False,
    }


def _repository_component_token(value: str) -> str:
    token = quote(
        str(value),
        safe="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.~",
    )
    return token or "unknown"


def _card_lifecycle_disabled_append_run_from_plan_item(
    preflight: dict[str, Any],
    append_plan_item: dict[str, Any],
) -> dict[str, Any]:
    session_id = str(preflight.get("session_id", ""))
    request_id = str(preflight.get("request_id", ""))
    event_type = str(append_plan_item.get("event_type", ""))
    future_event_id = str(append_plan_item.get("future_event_id", ""))
    conflict_status = str(append_plan_item.get("conflict_status", ""))
    card_id = future_event_id.split(":", 1)[1] if ":" in future_event_id else future_event_id
    if not card_id:
        card_id = "suggestion_card_preview"
    return {
        "run_id": f"asr_card_lifecycle_append_run_disabled_{_run_id_token(future_event_id)}",
        "run_status": "skipped",
        "skip_reason": (
            "event_append_disabled"
            if conflict_status == "none"
            else "append_preflight_blocked"
        ),
        "event_type": event_type,
        "future_event_id": future_event_id,
        "preview_event_id": str(append_plan_item.get("preview_event_id", "")),
        "idempotency_key": (
            "live_asr_card_lifecycle_append_run:"
            f"disabled:{session_id}:{request_id}:{event_type}:{card_id}"
        ),
        "preflight_idempotency_key": str(
            append_plan_item.get("idempotency_key", "")
        ),
        "preflight_append_status": str(
            append_plan_item.get("append_status", "")
        ),
        "preflight_conflict_status": conflict_status,
        "would_append_sequence": append_plan_item.get("would_append_sequence"),
        "would_append_after_sequence": append_plan_item.get(
            "would_append_after_sequence"
        ),
        "llm_call_status": "not_called",
        "credentials_status": "not_read",
        "cost_status": "not_estimated",
        "event_append_status": "not_appended",
        "idempotency_store_status": "not_written",
        "safe_to_append_event": False,
    }


def _run_id_token(value: str) -> str:
    raw_token = str(value)
    token_parts: list[str] = []
    for character in raw_token:
        if character.isascii() and character.isalnum():
            token_parts.append(character.lower())
        elif character in {"_", ":"}:
            token_parts.append("_")
        else:
            token_parts.append(f"_{ord(character):x}_")
    token = re.sub(r"_+", "_", "".join(token_parts)).strip("_")
    return token or "unknown"


def _card_lifecycle_append_preflight_plan(
    record: dict[str, Any],
    lifecycle_preview: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    session_id = str(lifecycle_preview.get("session_id", ""))
    request_id = str(lifecycle_preview.get("request_id", ""))
    last_sequence = _last_existing_event_sequence(record)
    existing_event_ids = _existing_event_ids(record)
    existing_idempotency_keys = _existing_idempotency_keys(record)
    append_plan: list[dict[str, Any]] = []
    append_errors: list[dict[str, str]] = []

    for index, preview_event in enumerate(
        list(lifecycle_preview.get("preview_events") or []),
        start=1,
    ):
        event_type = str(preview_event.get("event_type", ""))
        future_event_id = _future_event_id_from_preview_event(preview_event)
        card_id = str(dict(preview_event.get("payload") or {}).get("card_id", "")).strip()
        if not card_id:
            card_id = "suggestion_card_preview"
        idempotency_key = (
            "live_asr_card_lifecycle_append:"
            f"{session_id}:{request_id}:{event_type}:{card_id}"
        )
        append_status = "would_append_once_if_enabled"
        conflict_status = "none"
        if future_event_id in existing_event_ids:
            append_status = "blocked_existing_event"
            conflict_status = "existing_event_id"
            append_errors.append(
                _schema_validation_error(
                    "future_event_id",
                    "existing_event_id",
                    f"future event already exists: {future_event_id}",
                )
            )
        elif idempotency_key in existing_idempotency_keys:
            append_status = "blocked_existing_idempotency_key"
            conflict_status = "existing_idempotency_key"
            append_errors.append(
                _schema_validation_error(
                    "idempotency_key",
                    "existing_idempotency_key",
                    (
                        "future idempotency key already exists for event: "
                        f"{future_event_id}"
                    ),
                )
            )

        would_append_sequence = last_sequence + index
        append_plan.append(
            {
                "event_type": event_type,
                "preview_event_id": str(preview_event.get("event_id", "")),
                "future_event_id": future_event_id,
                "idempotency_key": idempotency_key,
                "would_append_sequence": would_append_sequence,
                "would_append_after_sequence": would_append_sequence - 1,
                "at_ms": preview_event.get("at_ms"),
                "append_status": append_status,
                "conflict_status": conflict_status,
                "preview_only": True,
                "would_append_if_enabled": bool(
                    preview_event.get("would_append_if_enabled", False)
                ),
            }
        )
    return append_plan, append_errors


def _last_existing_event_sequence(record: dict[str, Any]) -> int:
    return max(
        (int(event.get("sequence", 0)) for event in record.get("events") or []),
        default=0,
    )


def _existing_event_ids(record: dict[str, Any]) -> set[str]:
    return {
        str(event.get("id", ""))
        for event in record.get("events") or []
        if str(event.get("id", ""))
    }


def _existing_idempotency_keys(record: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for event in record.get("events") or []:
        top_level_key = str(event.get("idempotency_key", "")).strip()
        if top_level_key:
            keys.add(top_level_key)
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_key = str(payload.get("idempotency_key", "")).strip()
            if payload_key:
                keys.add(payload_key)
    return keys


def _future_event_id_from_preview_event(preview_event: dict[str, Any]) -> str:
    preview_event_id = str(preview_event.get("event_id", ""))
    if preview_event_id.startswith("preview:"):
        return preview_event_id.removeprefix("preview:")
    event_type = str(preview_event.get("event_type", ""))
    payload = dict(preview_event.get("payload") or {})
    card_id = str(payload.get("card_id", "suggestion_card_preview")).strip()
    if not card_id:
        card_id = "suggestion_card_preview"
    return f"{event_type}:{card_id}"


def _card_lifecycle_preview_events(
    request_id: str,
    request_draft: dict[str, Any],
    candidate_response: dict[str, Any],
    policy_allowed: bool,
    validation_errors: list[dict[str, str]],
    policy_errors: list[dict[str, str]],
) -> list[dict[str, Any]]:
    card_id = str(candidate_response.get("id", "suggestion_card_preview")).strip()
    if not card_id:
        card_id = "suggestion_card_preview"
    at_ms = candidate_response.get("card_created_at_ms")
    if type(at_ms) is not int:
        at_ms = 0
    preview_events = [
        {
            "event_type": "llm_schema_result",
            "preview_only": True,
            "would_append_if_enabled": True,
            "event_id": f"preview:llm_schema_result:{card_id}",
            "at_ms": at_ms,
            "payload": {
                "request_id": request_id,
                "request_draft_event_id": request_draft["event_id"],
                "card_id": card_id,
                "schema_result": str(candidate_response.get("schema_result", "")),
                "show_or_silence_decision": str(
                    candidate_response.get("show_or_silence_decision", "")
                ),
                "usage": dict(candidate_response.get("usage") or {}),
                "latency_ms": candidate_response.get("latency_ms"),
                "validation_errors": validation_errors,
            },
        }
    ]
    if policy_allowed:
        preview_events.append(
            {
                "event_type": "suggestion_card",
                "preview_only": True,
                "would_append_if_enabled": True,
                "event_id": f"preview:suggestion_card:{card_id}",
                "at_ms": at_ms,
                "payload": {
                    "request_id": request_id,
                    "request_draft_event_id": request_draft["event_id"],
                    "card_id": card_id,
                    "card": _card_lifecycle_preview_card(candidate_response),
                },
            }
        )
    else:
        silence_reason = (
            "schema_validation_failed"
            if validation_errors
            else "card_creation_policy_blocked"
        )
        preview_events.append(
            {
                "event_type": "suggestion_silenced",
                "preview_only": True,
                "would_append_if_enabled": True,
                "event_id": f"preview:suggestion_silenced:{card_id}",
                "at_ms": at_ms,
                "payload": {
                    "request_id": request_id,
                    "request_draft_event_id": request_draft["event_id"],
                    "card_id": card_id,
                    "silence_reason": silence_reason,
                    "validation_errors": validation_errors,
                    "policy_errors": policy_errors,
                },
            }
        )
    return preview_events


def _card_lifecycle_preview_card(
    candidate_response: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": str(candidate_response.get("id", "")),
        "type": str(candidate_response.get("type", "")),
        "title": str(candidate_response.get("title", "")),
        "suggested_question": str(candidate_response.get("suggested_question", "")),
        "evidence_span_ids": _string_list_for_card_policy(
            candidate_response.get("evidence_span_ids")
        ),
        "state_refs": _string_list_for_card_policy(
            candidate_response.get("state_refs")
        ),
        "state_event_ids": _string_list_for_card_policy(
            candidate_response.get("state_event_ids")
        ),
        "gap_rule_id": str(candidate_response.get("gap_rule_id", "")),
        "trigger_reason": str(candidate_response.get("trigger_reason", "")),
        "trigger_source": str(candidate_response.get("trigger_source", "")),
        "final_segment_at_ms": candidate_response.get("final_segment_at_ms"),
        "state_event_at_ms": candidate_response.get("state_event_at_ms"),
        "card_created_at_ms": candidate_response.get("card_created_at_ms"),
        "latency_ms": candidate_response.get("latency_ms"),
        "prompt_version": str(candidate_response.get("prompt_version", "")),
        "model": str(candidate_response.get("model", "")),
        "usage": dict(candidate_response.get("usage") or {}),
        "schema_result": str(candidate_response.get("schema_result", "")),
        "show_or_silence_decision": str(
            candidate_response.get("show_or_silence_decision", "")
        ),
        "segment_batch": _string_list_for_card_policy(
            candidate_response.get("segment_batch")
        ),
        "status": str(candidate_response.get("status", "")),
    }


def _card_creation_policy_errors(
    record: dict[str, Any],
    request_draft: dict[str, Any],
    candidate_response: dict[str, Any],
    validation_errors: list[dict[str, str]],
) -> list[dict[str, str]]:
    card_id = str(candidate_response.get("id", "suggestion card")).strip()
    if validation_errors:
        return [
            _schema_validation_error(
                "schema_validation",
                "schema_validation_failed",
                f"{card_id} schema validation must pass before card creation policy",
            )
        ]

    errors: list[dict[str, str]] = []
    draft_payload = dict(request_draft.get("payload") or {})
    target_type = str(draft_payload.get("target_type", ""))
    target_id = str(draft_payload.get("target_id", ""))
    target_state_ref = _card_policy_state_ref(target_type, target_id)
    candidate_payload = _card_policy_candidate_payload_by_id(record).get(
        str(draft_payload.get("target_candidate_id", ""))
    )
    if candidate_payload is not None:
        scheduler_event_type = str(candidate_payload.get("scheduler_event_type", ""))
        if scheduler_event_type != "llm_candidate_queued":
            decision_reason = str(candidate_payload.get("decision_reason", ""))
            errors.append(
                _schema_validation_error(
                    "scheduler_event_type",
                    "scheduler_candidate_not_queued",
                    (
                        f"{card_id} request draft candidate was not queued by scheduler"
                        f": {decision_reason or scheduler_event_type}"
                    ),
                )
            )

    _append_policy_linkage_error_if_mismatch(
        errors,
        candidate_response,
        draft_payload,
        field_name="gap_rule_id",
        card_id=card_id,
    )
    _append_policy_linkage_error_if_mismatch(
        errors,
        candidate_response,
        draft_payload,
        field_name="evidence_span_ids",
        card_id=card_id,
    )
    _append_policy_linkage_error_if_mismatch(
        errors,
        candidate_response,
        draft_payload,
        field_name="segment_batch",
        card_id=card_id,
    )
    _append_policy_list_linkage_error_if_mismatch(
        errors,
        _string_list_for_card_policy(candidate_response.get("state_event_ids")),
        _string_list_for_card_policy(draft_payload.get("source_event_ids")),
        "state_event_ids",
        card_id,
    )
    if target_state_ref not in _string_list_for_card_policy(
        candidate_response.get("state_refs")
    ):
        errors.append(
            _schema_validation_error(
                "state_refs",
                "request_linkage_mismatch",
                f"{card_id} state_refs must include request draft target state ref",
            )
        )

    evidence_by_id = _card_policy_evidence_by_id(record)
    segment_final_times = _card_policy_segment_final_times(record)
    state_events_by_id = _card_policy_state_events_by_id(record)

    for evidence_id in _string_list_for_card_policy(
        candidate_response.get("evidence_span_ids")
    ):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            errors.append(
                _schema_validation_error(
                    "evidence_span_ids",
                    "unknown_evidence",
                    f"{card_id} references unknown evidence_span_id: {evidence_id}",
                )
            )
            continue
        if (
            str(evidence.get("status", "active")) != "active"
            and _schema_validation_is_strong_card(candidate_response)
        ):
            errors.append(
                _schema_validation_error(
                    "evidence_span_ids",
                    "stale_evidence",
                    f"{card_id} references stale evidence_span_id: {evidence_id}",
                )
            )

    for event_id in _string_list_for_card_policy(
        candidate_response.get("state_event_ids")
    ):
        state_event = state_events_by_id.get(event_id)
        if state_event is None:
            errors.append(
                _schema_validation_error(
                    "state_event_ids",
                    "unknown_state_event",
                    f"{card_id} references unknown state_event_id: {event_id}",
                )
            )
            continue
        event_state_ref = _card_policy_state_ref(
            str(state_event.get("target_type", "")),
            str(state_event.get("target_id", "")),
        )
        if event_state_ref != target_state_ref:
            errors.append(
                _schema_validation_error(
                    "state_event_ids",
                    "state_event_target_mismatch",
                    f"{card_id} state_event_id {event_id} target must match request draft target",
                )
            )

    segment_batch = _string_list_for_card_policy(
        candidate_response.get("segment_batch")
    )
    segment_times = []
    for segment_id in segment_batch:
        if segment_id not in segment_final_times:
            errors.append(
                _schema_validation_error(
                    "segment_batch",
                    "unknown_segment",
                    f"{card_id} references unknown segment_id: {segment_id}",
                )
            )
            continue
        segment_times.append(segment_final_times[segment_id])
    if segment_times and candidate_response.get("final_segment_at_ms") != max(segment_times):
        errors.append(
            _schema_validation_error(
                "final_segment_at_ms",
                "segment_time_mismatch",
                f"{card_id} final_segment_at_ms must match segment batch",
            )
        )

    state_event_times = [
        int(state_events_by_id[event_id]["at_ms"])
        for event_id in _string_list_for_card_policy(
            candidate_response.get("state_event_ids")
        )
        if event_id in state_events_by_id
    ]
    if (
        state_event_times
        and candidate_response.get("state_event_at_ms") != max(state_event_times)
    ):
        errors.append(
            _schema_validation_error(
                "state_event_at_ms",
                "state_event_time_mismatch",
                f"{card_id} state_event_at_ms must match referenced state events",
            )
        )

    latency_ms = candidate_response.get("latency_ms")
    if (
        type(latency_ms) is int
        and latency_ms > CARD_CREATION_POLICY_REALTIME_WINDOW_MS
        and _schema_validation_is_strong_card(candidate_response)
    ):
        errors.append(
            _schema_validation_error(
                "latency_ms",
                "strong_card_too_late",
                f"{card_id} latency_ms exceeds realtime card window",
            )
        )

    if str(candidate_response.get("schema_result", "")) != "valid":
        errors.append(
            _schema_validation_error(
                "schema_result",
                "schema_result_not_creatable",
                f"{card_id} schema_result must be valid for card creation",
            )
        )
    if str(candidate_response.get("show_or_silence_decision", "")) != "show":
        errors.append(
            _schema_validation_error(
                "show_or_silence_decision",
                "non_show_decision",
                f"{card_id} show_or_silence_decision must be show for card creation",
            )
        )
    if str(candidate_response.get("status", "")) != "new":
        errors.append(
            _schema_validation_error(
                "status",
                "non_new_status",
                f"{card_id} status must be new for card creation",
            )
        )

    degradation_reasons = list(
        draft_payload.get("candidate_degradation_reasons") or []
    )
    if degradation_reasons and _schema_validation_is_strong_card(candidate_response):
        errors.append(
            _schema_validation_error(
                "candidate_degradation_reasons",
                "candidate_quality_degraded",
                f"{card_id} request draft candidate quality blocks strong card creation",
            )
        )

    return _dedupe_schema_validation_errors(errors)


def _append_policy_linkage_error_if_mismatch(
    errors: list[dict[str, str]],
    candidate_response: dict[str, Any],
    draft_payload: dict[str, Any],
    *,
    field_name: str,
    card_id: str,
) -> None:
    candidate_value = candidate_response.get(field_name)
    draft_value = draft_payload.get(field_name)
    if isinstance(draft_value, list):
        _append_policy_list_linkage_error_if_mismatch(
            errors,
            _string_list_for_card_policy(candidate_value),
            _string_list_for_card_policy(draft_value),
            field_name,
            card_id,
        )
        return
    if str(candidate_value) != str(draft_value):
        errors.append(
            _schema_validation_error(
                field_name,
                "request_linkage_mismatch",
                f"{card_id} {field_name} must match request draft",
            )
        )


def _append_policy_list_linkage_error_if_mismatch(
    errors: list[dict[str, str]],
    candidate_values: list[str],
    draft_values: list[str],
    field_name: str,
    card_id: str,
) -> None:
    if candidate_values != draft_values:
        errors.append(
            _schema_validation_error(
                field_name,
                "request_linkage_mismatch",
                f"{card_id} {field_name} must match request draft",
            )
        )


def _card_policy_evidence_by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for event in record.get("events") or []:
        payload = dict(event.get("payload") or {})
        for evidence in payload.get("evidence_spans") or []:
            evidence_by_id[str(evidence.get("id", ""))] = dict(evidence)
        for evidence in payload.get("superseded_evidence_spans") or []:
            evidence_by_id[str(evidence.get("id", ""))] = dict(evidence)
    return evidence_by_id


def _card_policy_segment_final_times(record: dict[str, Any]) -> dict[str, int]:
    segment_final_times: dict[str, int] = {}
    for event in record.get("events") or []:
        if event.get("event_type") not in {"transcript_final", "transcript_revision"}:
            continue
        payload = dict(event.get("payload") or {})
        segment_id = str(payload.get("segment_id", ""))
        if segment_id:
            segment_final_times[segment_id] = int(event.get("at_ms", 0))
    return segment_final_times


def _card_policy_state_events_by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    state_events_by_id: dict[str, dict[str, Any]] = {}
    for event in record.get("events") or []:
        if event.get("event_type") != "state_event":
            continue
        payload = dict(event.get("payload") or {})
        event_id = str(payload.get("event_id", ""))
        if event_id:
            state_events_by_id[event_id] = {
                "at_ms": int(event.get("at_ms", 0)),
                "target_type": str(payload.get("target_type", "")),
                "target_id": str(payload.get("target_id", "")),
            }
    return state_events_by_id


def _card_policy_candidate_payload_by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidate_by_id: dict[str, dict[str, Any]] = {}
    for event in record.get("events") or []:
        if event.get("event_type") != "suggestion_candidate_event":
            continue
        payload = dict(event.get("payload") or {})
        candidate_id = str(payload.get("candidate_id", ""))
        if candidate_id:
            candidate_by_id[candidate_id] = payload
    return candidate_by_id


def _card_policy_scheduler_gate(
    record: dict[str, Any],
    request_draft: dict[str, Any],
) -> dict[str, str]:
    draft_payload = dict(request_draft.get("payload") or {})
    candidate_payload = _card_policy_candidate_payload_by_id(record).get(
        str(draft_payload.get("target_candidate_id", ""))
    )
    if candidate_payload is None:
        return {
            "scheduler_policy_status": "not_found",
            "scheduler_decision_reason": "",
            "scheduler_candidate_event_type": "",
        }

    scheduler_event_type = str(candidate_payload.get("scheduler_event_type", ""))
    decision_reason = str(candidate_payload.get("decision_reason", ""))
    scheduler_policy_status = "blocked_by_scheduler"
    if scheduler_event_type == "llm_candidate_queued":
        scheduler_policy_status = "queued"
    elif scheduler_event_type == "llm_candidate_skipped" and decision_reason:
        scheduler_policy_status = f"blocked_by_{decision_reason}"

    return {
        "scheduler_policy_status": scheduler_policy_status,
        "scheduler_decision_reason": decision_reason,
        "scheduler_candidate_event_type": scheduler_event_type,
    }


def _card_policy_state_ref(target_type: str, target_id: str) -> str:
    return f"{target_type}:{target_id}"


def _string_list_for_card_policy(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = [value]
    else:
        raw_values = list(value)
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _validate_suggestion_card_dry_run_candidate(
    candidate_response: dict[str, Any],
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    card_id = str(candidate_response.get("id", "suggestion card")).strip()
    for field_name in SCHEMA_VALIDATION_REQUIRED_FIELDS:
        if field_name not in candidate_response:
            errors.append(
                _schema_validation_error(
                    field_name,
                    "missing_required_field",
                    f"suggestion card {card_id} missing {field_name}",
                )
            )
    errors.extend(_suggestion_card_contract_errors(candidate_response, card_id))
    errors.extend(_suggestion_card_trace_errors(candidate_response, card_id))
    return _dedupe_schema_validation_errors(errors)


def _suggestion_card_contract_errors(
    candidate_response: dict[str, Any],
    card_id: str,
) -> list[dict[str, str]]:
    try:
        SuggestionCardV1.from_dict(candidate_response)
    except ValueError as exc:
        field_name = _schema_validation_field_from_message(str(exc))
        return [
            _schema_validation_error(
                field_name,
                _schema_validation_code_from_message(str(exc)),
                str(exc),
            )
        ]
    return []


def _suggestion_card_trace_errors(
    candidate_response: dict[str, Any],
    card_id: str,
) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    usage = candidate_response.get("usage")
    if isinstance(usage, dict):
        if "total_tokens" not in usage:
            errors.append(
                _schema_validation_error(
                    "usage.total_tokens",
                    "missing_required_field",
                    f"suggestion card {card_id} missing usage.total_tokens",
                )
            )
        else:
            total_tokens = _strict_schema_validation_int(
                usage["total_tokens"]
            )
            if total_tokens is None:
                errors.append(
                    _schema_validation_error(
                        "usage.total_tokens",
                        "invalid_type",
                        f"{card_id} usage.total_tokens must be an integer",
                    )
                )
            else:
                if total_tokens < 0:
                    errors.append(
                        _schema_validation_error(
                            "usage.total_tokens",
                            "invalid_value",
                            f"{card_id} usage.total_tokens must be non-negative",
                        )
                    )
    schema_result = str(candidate_response.get("schema_result", "")).strip()
    if schema_result and schema_result not in SCHEMA_VALIDATION_VALID_SCHEMA_RESULTS:
        errors.append(
            _schema_validation_error(
                "schema_result",
                "unsupported_schema_result",
                f"{card_id} unsupported schema_result: {schema_result}",
            )
        )
    if (
        schema_result in SCHEMA_VALIDATION_BLOCKING_SCHEMA_RESULTS
        and _schema_validation_is_strong_card(candidate_response)
    ):
        errors.append(
            _schema_validation_error(
                "schema_result",
                "blocking_schema_result",
                f"{card_id} schema_result {schema_result} blocks strong suggestion",
            )
        )
    time_values = _schema_validation_time_values(candidate_response, card_id)
    errors.extend(time_values["errors"])
    if not time_values["ready"]:
        return errors
    final_segment_at_ms = int(time_values["final_segment_at_ms"])
    state_event_at_ms = int(time_values["state_event_at_ms"])
    card_created_at_ms = int(time_values["card_created_at_ms"])
    latency_ms = int(time_values["latency_ms"])
    if state_event_at_ms < final_segment_at_ms:
        errors.append(
            _schema_validation_error(
                "state_event_at_ms",
                "invalid_time_order",
                f"{card_id} state_event_at_ms must be >= final_segment_at_ms",
            )
        )
    if card_created_at_ms < state_event_at_ms:
        errors.append(
            _schema_validation_error(
                "card_created_at_ms",
                "invalid_time_order",
                f"{card_id} card_created_at_ms must be >= state_event_at_ms",
            )
        )
    if card_created_at_ms - final_segment_at_ms != latency_ms:
        errors.append(
            _schema_validation_error(
                "latency_ms",
                "inconsistent_latency",
                f"{card_id} latency_ms must equal card_created_at_ms - final_segment_at_ms",
            )
        )
    return errors


def _schema_validation_time_values(
    candidate_response: dict[str, Any],
    card_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {"ready": True, "errors": []}
    for field_name in (
        "final_segment_at_ms",
        "state_event_at_ms",
        "card_created_at_ms",
        "latency_ms",
    ):
        if field_name not in candidate_response:
            result["ready"] = False
            continue
        value = _strict_schema_validation_int(
            candidate_response[field_name]
        )
        if value is None:
            result["ready"] = False
            result["errors"].append(
                _schema_validation_error(
                    field_name,
                    "invalid_type",
                    f"{card_id} {field_name} must be an integer",
                )
            )
            continue
        if value < 0:
            result["ready"] = False
            result["errors"].append(
                _schema_validation_error(
                    field_name,
                    "invalid_value",
                    f"{card_id} {field_name} must be non-negative",
                )
            )
        result[field_name] = value
    return result


def _strict_schema_validation_int(value: Any) -> int | None:
    if type(value) is not int:
        return None
    return value


def _schema_validation_is_strong_card(candidate_response: dict[str, Any]) -> bool:
    decision = str(candidate_response.get("show_or_silence_decision", "show"))
    status = str(candidate_response.get("status", "new"))
    return (
        decision not in SCHEMA_VALIDATION_NON_STRONG_DECISIONS
        and status not in SCHEMA_VALIDATION_NON_STRONG_STATUSES
    )


def _candidate_response_preview(candidate_response: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(candidate_response.get("id", "")),
        "type": str(candidate_response.get("type", "")),
        "schema_result": str(candidate_response.get("schema_result", "")),
        "show_or_silence_decision": str(
            candidate_response.get("show_or_silence_decision", "")
        ),
        "status": str(candidate_response.get("status", "")),
    }


def _schema_validation_error(
    field: str,
    code: str,
    message: str,
) -> dict[str, str]:
    return {
        "field": field,
        "code": code,
        "message": message,
    }


def _schema_validation_field_from_message(message: str) -> str:
    if "usage.total_tokens" in message:
        return "usage.total_tokens"
    known_fields = [
        "usage",
        "schema_result",
        "status",
        *SCHEMA_VALIDATION_REQUIRED_FIELDS,
    ]
    for field_name in known_fields:
        if f" {field_name}" in message or message.endswith(field_name):
            return field_name
    return "candidate_response"


def _schema_validation_code_from_message(message: str) -> str:
    if "missing" in message:
        return "missing_required_field"
    if "unsupported" in message:
        return "unsupported_value"
    if "non-negative" in message:
        return "invalid_value"
    if "must be an integer" in message:
        return "invalid_type"
    return "invalid_contract"


def _dedupe_schema_validation_errors(
    errors: list[dict[str, str]],
) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for error in errors:
        key = (error["field"], error["code"], error["message"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(error)
    return deduped


def _llm_provider_readiness_from_record(record: dict[str, Any]) -> dict[str, Any]:
    request_draft_count = len(_request_draft_events_from_record(record))
    execution_preview_count = len(_execution_previews_from_record(record))
    disabled_run_count = len(_disabled_execution_runs_from_record(record))
    queue_status = "has_request_drafts" if request_draft_count else "empty"
    block_reasons = [
        "llm_executor_disabled",
        "provider_config_not_loaded",
        "credentials_not_read",
        "enabled_mode_not_designed",
    ]
    if request_draft_count == 0:
        block_reasons.append("no_request_drafts")
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "readiness_status": "not_ready",
        "executor_mode": "disabled",
        "enabled_mode_status": "blocked",
        "provider_protocol": "openai_compatible_chat_completions",
        "provider_config_status": "not_loaded",
        "provider_config_source": "not_read",
        "credentials_status": "not_read",
        "base_url_status": "not_configured",
        "model_status": "not_configured",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "request_draft_count": request_draft_count,
        "execution_preview_count": execution_preview_count,
        "disabled_run_count": disabled_run_count,
        "queue_status": queue_status,
        "can_execute_llm": False,
        "block_reasons": block_reasons,
        "required_config_fields": ["base_url", "api_key", "model"],
        "next_required_decisions": [
            "provider_config_secret_boundary",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
            "token_cost_accounting",
            "timeout_retry_and_degradation_policy",
        ],
    }


def _llm_provider_config_boundary_from_record(record: dict[str, Any]) -> dict[str, Any]:
    fields = [
        {
            "name": "base_url",
            "classification": "public_endpoint",
            "display_policy": "origin_only_or_masked",
            "required": True,
            "response_value_policy": "never_return_raw_value",
        },
        {
            "name": "api_key",
            "classification": "secret",
            "display_policy": "never_display",
            "required": True,
            "response_value_policy": "never_return_value",
        },
        {
            "name": "model",
            "classification": "public_model_id",
            "display_policy": "display_allowed",
            "required": True,
            "response_value_policy": (
                "return_configured_value_only_after_loader_mask_review"
            ),
        },
        {
            "name": "timeout_seconds",
            "classification": "non_secret_runtime",
            "display_policy": "display_allowed",
            "required": False,
            "response_value_policy": "return_configured_value_after_validation",
        },
        {
            "name": "ca_bundle_path",
            "classification": "local_path_sensitive",
            "display_policy": "basename_only_or_not_displayed",
            "required": False,
            "response_value_policy": "never_return_absolute_path",
        },
    ]
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "boundary_status": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_load_status": "not_loaded",
        "config_source_status": "not_read",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "field_count": len(fields),
        "required_field_names": [
            str(field["name"]) for field in fields if field["required"] is True
        ],
        "fields": fields,
        "allowed_response_fields": [
            "provider_protocol",
            "model",
            "base_url_origin",
            "timeout_seconds",
            "config_status",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
        ],
        "secret_storage_policy": "configs/local_only_or_os_keychain_future",
        "next_required_decisions": [
            "provider_config_loader_contract",
            "secret_storage_adapter",
            "masked_provider_status_response",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }


def _llm_provider_masked_status_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "status_kind": "masked_provider_status",
        "status_mode": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "provider_status": "not_configured",
        "config_load_status": "not_loaded",
        "config_source_status": "not_read",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "display_values": {
            "base_url_origin": None,
            "model": None,
            "timeout_seconds": None,
            "ca_bundle_name": None,
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "not_read",
            "model": "not_read",
            "timeout_seconds": "not_read",
            "ca_bundle_name": "not_read",
            "api_key": "never_display",
        },
        "masked_value_policy": {
            "api_key": "never_return_value_or_mask",
            "base_url": "origin_only_after_loader_review",
            "model": "display_allowed_after_loader_review",
            "timeout_seconds": "display_allowed_after_validation",
            "ca_bundle_path": "basename_only_after_loader_review",
        },
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
            "authorization",
            "bearer_token",
            "raw_config",
        ],
        "block_reasons": [
            "template_only_status",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "provider_config_loader_contract",
            "secret_storage_adapter",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }


def _validate_llm_provider_masked_status_loader_dry_run_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="provider masked status loader dry run request body must be an object",
        )
    required_fields = {
        "loader_mode",
        "provider_protocol",
        "config_path",
        "secret_reference",
        "requested_display_fields",
        "authorization",
    }
    if set(payload) != required_fields:
        raise HTTPException(
            status_code=422,
            detail="provider masked status loader dry run fields must match contract",
        )
    if payload["loader_mode"] != "masked_status_dry_run_only":
        raise HTTPException(
            status_code=422,
            detail="loader_mode must be masked_status_dry_run_only",
        )
    if payload["provider_protocol"] != "openai_compatible_chat_completions":
        raise HTTPException(status_code=422, detail="unsupported provider_protocol")
    config_path = payload["config_path"]
    if not isinstance(config_path, str) or config_path.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="config_path must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in config_path):
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain control characters",
        )
    parsed_config_path = urlparse(config_path)
    if parsed_config_path.netloc or (
        parsed_config_path.scheme and len(parsed_config_path.scheme) != 1
    ):
        raise HTTPException(
            status_code=422,
            detail="config_path must be a local filesystem path",
        )
    if ".." in Path(config_path.replace("\\", "/")).parts:
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain path traversal",
        )
    secret_reference = payload["secret_reference"]
    if not isinstance(secret_reference, dict):
        raise HTTPException(
            status_code=422,
            detail="secret_reference must be an object",
        )
    if set(secret_reference) != {"reference_type", "reference_id"}:
        raise HTTPException(
            status_code=422,
            detail="secret_reference fields must match contract",
        )
    allowed_reference_types = {
        "keychain_item_reference",
        "enterprise_secret_reference",
        "env_var_name_reference",
    }
    reference_type = secret_reference["reference_type"]
    if not isinstance(reference_type, str):
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_type must be a string",
        )
    if reference_type not in allowed_reference_types:
        raise HTTPException(
            status_code=422,
            detail="unsupported secret_reference reference_type",
        )
    reference_id = secret_reference["reference_id"]
    if not isinstance(reference_id, str) or reference_id.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_id must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in reference_id):
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_id must not contain control characters",
        )
    requested_display_fields = payload["requested_display_fields"]
    if not isinstance(requested_display_fields, list) or not requested_display_fields:
        raise HTTPException(
            status_code=422,
            detail="requested_display_fields must be a non-empty list",
        )
    allowed_display_fields = {
        "base_url_origin",
        "model",
        "timeout_seconds",
        "ca_bundle_name",
        "api_key",
    }
    if any(not isinstance(field_name, str) for field_name in requested_display_fields):
        raise HTTPException(
            status_code=422,
            detail="requested_display_fields values must be strings",
        )
    if any(field_name not in allowed_display_fields for field_name in requested_display_fields):
        raise HTTPException(
            status_code=422,
            detail="unsupported requested_display_fields value",
        )
    if len(set(requested_display_fields)) != len(requested_display_fields):
        raise HTTPException(
            status_code=422,
            detail="requested_display_fields must not contain duplicates",
        )
    authorization = payload["authorization"]
    if not isinstance(authorization, dict):
        raise HTTPException(status_code=422, detail="authorization must be an object")
    authorization_fields = {
        "user_confirmed_local_config_access",
        "acknowledged_secret_storage_policy",
        "allow_config_file_read",
        "allow_secret_read",
        "allow_llm_call",
        "allow_event_mutation",
        "allow_status_value_inference",
    }
    if set(authorization) != authorization_fields:
        raise HTTPException(
            status_code=422,
            detail="authorization fields must match dry run contract",
        )
    if authorization["user_confirmed_local_config_access"] is not True:
        raise HTTPException(
            status_code=422,
            detail="user_confirmed_local_config_access must be true",
        )
    if authorization["acknowledged_secret_storage_policy"] is not True:
        raise HTTPException(
            status_code=422,
            detail="acknowledged_secret_storage_policy must be true",
        )
    if authorization["allow_config_file_read"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_config_file_read must be false during dry run",
        )
    if authorization["allow_secret_read"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_secret_read must be false during dry run",
        )
    if authorization["allow_llm_call"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_llm_call must be false during dry run",
        )
    if authorization["allow_event_mutation"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_event_mutation must be false during dry run",
        )
    if authorization["allow_status_value_inference"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_status_value_inference must be false during dry run",
        )


def _llm_provider_masked_status_loader_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "dry_run_kind": "authorized_masked_status_loader",
        "dry_run_status": "blocked",
        "dry_run_mode": "masked_status_dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_reference",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "secret_reference_status": "provided_not_resolved",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "status_value_status": "not_inferred",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_read_config": False,
        "safe_to_read_secret": False,
        "safe_to_infer_status": False,
        "safe_to_execute": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "secret_reference_display": {
            "reference_type": str(payload["secret_reference"]["reference_type"]),
            "reference_id": None,
        },
        "requested_display_fields": list(payload["requested_display_fields"]),
        "display_values": {
            "base_url_origin": None,
            "model": None,
            "timeout_seconds": None,
            "ca_bundle_name": None,
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "not_read",
            "model": "not_read",
            "timeout_seconds": "not_read",
            "ca_bundle_name": "not_read",
            "api_key": "never_display",
        },
        "masked_value_policy": {
            "api_key": "never_return_value_or_mask",
            "base_url": "origin_only_after_authorized_loader",
            "model": "display_allowed_after_authorized_loader",
            "timeout_seconds": "display_allowed_after_authorized_loader",
            "ca_bundle_path": "basename_only_after_authorized_loader",
        },
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
            "allow_status_value_inference": False,
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
            "secret_reference_id",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "config_file_readable",
            "config_file_size",
            "config_file_mtime",
            "config_file_hash",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "dry_run_only",
            "config_file_read_not_authorized",
            "secret_value_read_not_authorized",
            "status_value_inference_not_authorized",
            "secret_storage_adapter_not_connected",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "authorized_config_file_reader",
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }


def _validate_llm_provider_config_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="provider config request body must be an object",
        )
    allowed_fields = {
        "provider_protocol",
        "base_url",
        "api_key",
        "model",
        "timeout_seconds",
        "ca_bundle_path",
    }
    required_fields = {"provider_protocol", "base_url", "api_key", "model"}
    for field_name in required_fields:
        if field_name not in payload:
            raise HTTPException(
                status_code=422,
                detail=f"missing required field: {field_name}",
            )
    extra_fields = set(payload) - allowed_fields
    if extra_fields:
        raise HTTPException(
            status_code=422,
            detail="unsupported provider config field",
        )
    if payload["provider_protocol"] != "openai_compatible_chat_completions":
        raise HTTPException(status_code=422, detail="unsupported provider_protocol")
    base_url = payload["base_url"]
    if not isinstance(base_url, str):
        raise HTTPException(status_code=422, detail="base_url must be an https URL")
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        raise HTTPException(status_code=422, detail="base_url must be an https URL")
    if parsed_base_url.username or parsed_base_url.password:
        raise HTTPException(
            status_code=422,
            detail="base_url must not include credentials",
        )
    api_key = payload["api_key"]
    if not isinstance(api_key, str) or api_key.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="api_key must be a non-empty string",
        )
    model = payload["model"]
    if not isinstance(model, str) or model.strip() == "":
        raise HTTPException(status_code=422, detail="model must be a non-empty string")
    if "timeout_seconds" in payload and payload["timeout_seconds"] is not None:
        timeout_seconds = payload["timeout_seconds"]
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or timeout_seconds < 1
            or timeout_seconds > 120
        ):
            raise HTTPException(
                status_code=422,
                detail="timeout_seconds must be between 1 and 120",
            )
    if "ca_bundle_path" in payload and payload["ca_bundle_path"] is not None:
        ca_bundle_path = payload["ca_bundle_path"]
        if not isinstance(ca_bundle_path, str):
            raise HTTPException(
                status_code=422,
                detail="ca_bundle_path must be a relative basename or subpath",
            )
        ca_path = Path(ca_bundle_path)
        if ca_path.is_absolute():
            raise HTTPException(
                status_code=422,
                detail="ca_bundle_path must be a relative basename or subpath",
            )
        if ".." in ca_path.parts:
            raise HTTPException(
                status_code=422,
                detail="ca_bundle_path must not contain path traversal",
            )


def _llm_provider_config_validation_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    parsed_base_url = urlparse(str(payload["base_url"]))
    base_url_host = parsed_base_url.hostname or ""
    base_url_origin = f"{parsed_base_url.scheme}://{base_url_host}"
    if parsed_base_url.port is not None:
        base_url_origin = f"{base_url_origin}:{parsed_base_url.port}"
    timeout_seconds = payload.get("timeout_seconds")
    ca_bundle_path = payload.get("ca_bundle_path")
    ca_bundle_name = Path(ca_bundle_path).name if ca_bundle_path else None
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "validation_kind": "provider_config_request_body",
        "validation_status": "valid",
        "validation_mode": "request_body_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "request_body_only",
        "config_file_status": "not_read",
        "credentials_status": "provided_but_not_returned",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "validated_fields": [
            "provider_protocol",
            "base_url",
            "api_key",
            "model",
            "timeout_seconds",
            "ca_bundle_path",
        ],
        "display_values": {
            "base_url_origin": base_url_origin,
            "model": str(payload["model"]),
            "timeout_seconds": timeout_seconds,
            "ca_bundle_name": ca_bundle_name,
            "api_key": None,
        },
        "display_value_status": {
            "base_url_origin": "derived_from_request_body",
            "model": "provided_non_secret",
            "timeout_seconds": (
                "provided_non_secret"
                if timeout_seconds is not None
                else "not_provided"
            ),
            "ca_bundle_name": "basename_only" if ca_bundle_name else "not_provided",
            "api_key": "never_display",
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
        ],
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "next_required_decisions": [
            "secret_storage_adapter",
            "authorized_config_file_loader",
            "enabled_executor_mode_contract",
            "schema_validation_and_card_lifecycle",
        ],
    }


def _validate_llm_provider_config_loader_preflight_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="provider config loader preflight request body must be an object",
        )
    allowed_fields = {
        "loader_mode",
        "provider_protocol",
        "config_path",
        "requested_fields",
        "authorization",
    }
    required_fields = {
        "loader_mode",
        "provider_protocol",
        "config_path",
        "requested_fields",
        "authorization",
    }
    for field_name in required_fields:
        if field_name not in payload:
            raise HTTPException(
                status_code=422,
                detail=f"missing required field: {field_name}",
            )
    if set(payload) - allowed_fields:
        raise HTTPException(
            status_code=422,
            detail="unsupported provider config loader preflight field",
        )
    if payload["loader_mode"] != "preflight_only":
        raise HTTPException(
            status_code=422,
            detail="loader_mode must be preflight_only",
        )
    if payload["provider_protocol"] != "openai_compatible_chat_completions":
        raise HTTPException(status_code=422, detail="unsupported provider_protocol")
    config_path = payload["config_path"]
    if not isinstance(config_path, str) or config_path.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="config_path must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in config_path):
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain control characters",
        )
    parsed_config_path = urlparse(config_path)
    if parsed_config_path.netloc or (
        parsed_config_path.scheme and len(parsed_config_path.scheme) != 1
    ):
        raise HTTPException(
            status_code=422,
            detail="config_path must be a local filesystem path",
        )
    config_path_parts = Path(config_path.replace("\\", "/")).parts
    if ".." in config_path_parts:
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain path traversal",
        )
    requested_fields = payload["requested_fields"]
    if not isinstance(requested_fields, list) or not requested_fields:
        raise HTTPException(
            status_code=422,
            detail="requested_fields must be a non-empty list",
        )
    allowed_requested_fields = {
        "base_url",
        "api_key",
        "model",
        "timeout_seconds",
        "ca_bundle_path",
    }
    if any(
        not isinstance(field_name, str)
        or field_name not in allowed_requested_fields
        for field_name in requested_fields
    ):
        raise HTTPException(status_code=422, detail="unsupported requested field")
    if len(set(requested_fields)) != len(requested_fields):
        raise HTTPException(
            status_code=422,
            detail="requested_fields must not contain duplicates",
        )
    authorization = payload["authorization"]
    if not isinstance(authorization, dict):
        raise HTTPException(
            status_code=422,
            detail="authorization must be an object",
        )
    authorization_fields = {
        "user_confirmed_local_config_access",
        "allow_secret_read",
        "allow_llm_call",
    }
    if set(authorization) != authorization_fields:
        raise HTTPException(
            status_code=422,
            detail="authorization fields must match preflight contract",
        )
    if authorization["user_confirmed_local_config_access"] is not True:
        raise HTTPException(
            status_code=422,
            detail="user_confirmed_local_config_access must be true",
        )
    if authorization["allow_secret_read"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_secret_read must be false during preflight",
        )
    if authorization["allow_llm_call"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_llm_call must be false during preflight",
        )


def _llm_provider_config_loader_preflight_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "preflight_kind": "provider_config_loader",
        "preflight_status": "accepted",
        "preflight_mode": "metadata_only",
        "loader_mode": "preflight_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_metadata",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "safe_to_load_config": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "requested_fields": list(payload["requested_fields"]),
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "allow_secret_read": False,
            "allow_llm_call": False,
        },
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "preflight_only",
            "config_file_not_read",
            "secret_read_not_authorized",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "secret_storage_adapter",
            "authorized_config_file_reader",
            "masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }


def _llm_provider_secret_storage_policy_from_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "policy_kind": "provider_secret_storage",
        "policy_status": "template_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "not_read",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_execute": False,
        "safe_to_read_secret": False,
        "recommended_storage_order": [
            "os_keychain",
            "enterprise_secret_provider",
            "environment_variable_for_development_only",
        ],
        "allowed_secret_references": [
            "keychain_item_reference",
            "enterprise_secret_reference",
            "env_var_name_reference",
        ],
        "forbidden_storage_locations": [
            "repository_files",
            "configs_local_plaintext_api_key",
            "session_json",
            "live_asr_audit_events",
            "logs",
            "reports",
            "browser_local_storage",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "required_loader_guards": [
            "explicit_user_authorization",
            "path_privacy_redaction",
            "secret_value_redaction",
            "no_secret_in_error_response",
            "no_secret_in_audit_event",
            "no_secret_in_logs",
            "no_secret_in_browser_storage",
        ],
        "block_reasons": [
            "template_only_policy",
            "secret_storage_adapter_not_connected",
            "provider_config_not_loaded",
            "credentials_not_read",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "authorized_config_file_reader",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }


def _validate_llm_provider_config_reader_dry_run_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="provider config reader dry run request body must be an object",
        )
    required_fields = {
        "reader_mode",
        "provider_protocol",
        "config_path",
        "secret_reference",
        "authorization",
    }
    if set(payload) != required_fields:
        raise HTTPException(
            status_code=422,
            detail="provider config reader dry run fields must match contract",
        )
    if payload["reader_mode"] != "dry_run_only":
        raise HTTPException(status_code=422, detail="reader_mode must be dry_run_only")
    if payload["provider_protocol"] != "openai_compatible_chat_completions":
        raise HTTPException(status_code=422, detail="unsupported provider_protocol")
    config_path = payload["config_path"]
    if not isinstance(config_path, str) or config_path.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="config_path must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in config_path):
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain control characters",
        )
    parsed_config_path = urlparse(config_path)
    if parsed_config_path.netloc or (
        parsed_config_path.scheme and len(parsed_config_path.scheme) != 1
    ):
        raise HTTPException(
            status_code=422,
            detail="config_path must be a local filesystem path",
        )
    if ".." in Path(config_path.replace("\\", "/")).parts:
        raise HTTPException(
            status_code=422,
            detail="config_path must not contain path traversal",
        )
    secret_reference = payload["secret_reference"]
    if not isinstance(secret_reference, dict):
        raise HTTPException(
            status_code=422,
            detail="secret_reference must be an object",
        )
    if set(secret_reference) != {"reference_type", "reference_id"}:
        raise HTTPException(
            status_code=422,
            detail="secret_reference fields must match contract",
        )
    allowed_reference_types = {
        "keychain_item_reference",
        "enterprise_secret_reference",
        "env_var_name_reference",
    }
    reference_type = secret_reference["reference_type"]
    if not isinstance(reference_type, str):
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_type must be a string",
        )
    if reference_type not in allowed_reference_types:
        raise HTTPException(
            status_code=422,
            detail="unsupported secret_reference reference_type",
        )
    reference_id = secret_reference["reference_id"]
    if not isinstance(reference_id, str) or reference_id.strip() == "":
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_id must be a non-empty string",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in reference_id):
        raise HTTPException(
            status_code=422,
            detail="secret_reference reference_id must not contain control characters",
        )
    authorization = payload["authorization"]
    if not isinstance(authorization, dict):
        raise HTTPException(status_code=422, detail="authorization must be an object")
    authorization_fields = {
        "user_confirmed_local_config_access",
        "acknowledged_secret_storage_policy",
        "allow_config_file_read",
        "allow_secret_read",
        "allow_llm_call",
        "allow_event_mutation",
    }
    if set(authorization) != authorization_fields:
        raise HTTPException(
            status_code=422,
            detail="authorization fields must match dry run contract",
        )
    if authorization["user_confirmed_local_config_access"] is not True:
        raise HTTPException(
            status_code=422,
            detail="user_confirmed_local_config_access must be true",
        )
    if authorization["acknowledged_secret_storage_policy"] is not True:
        raise HTTPException(
            status_code=422,
            detail="acknowledged_secret_storage_policy must be true",
        )
    if authorization["allow_config_file_read"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_config_file_read must be false during dry run",
        )
    if authorization["allow_secret_read"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_secret_read must be false during dry run",
        )
    if authorization["allow_llm_call"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_llm_call must be false during dry run",
        )
    if authorization["allow_event_mutation"] is not False:
        raise HTTPException(
            status_code=422,
            detail="allow_event_mutation must be false during dry run",
        )


def _llm_provider_config_reader_dry_run_from_record(
    record: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "session_id": str(record.get("session_id", "")),
        "source": str(record.get("source", "")),
        "trace_kind": str(record.get("trace_kind", "")),
        "dry_run_kind": "authorized_config_file_reader",
        "dry_run_status": "blocked",
        "dry_run_mode": "dry_run_only",
        "provider_protocol": "openai_compatible_chat_completions",
        "config_source_status": "caller_supplied_path_reference",
        "config_file_status": "not_read",
        "config_existence_status": "not_checked",
        "secret_reference_status": "provided_not_resolved",
        "secret_storage_status": "not_connected",
        "credentials_status": "not_read",
        "llm_call_status": "not_called",
        "schema_status": "not_generated",
        "card_status": "not_created",
        "cost_status": "not_estimated",
        "safe_to_read_config": False,
        "safe_to_read_secret": False,
        "safe_to_execute": False,
        "path_display": {
            "config_path_label": None,
            "config_path_parent_name": None,
            "config_path": None,
        },
        "secret_reference_display": {
            "reference_type": str(payload["secret_reference"]["reference_type"]),
            "reference_id": None,
        },
        "authorization_summary": {
            "user_confirmed_local_config_access": True,
            "acknowledged_secret_storage_policy": True,
            "allow_config_file_read": False,
            "allow_secret_read": False,
            "allow_llm_call": False,
            "allow_event_mutation": False,
        },
        "required_loader_guards": [
            "explicit_user_authorization",
            "path_privacy_redaction",
            "secret_reference_only",
            "secret_value_redaction",
            "no_secret_in_error_response",
            "no_secret_in_audit_event",
            "no_secret_in_logs",
            "no_secret_in_browser_storage",
        ],
        "forbidden_response_fields": [
            "api_key",
            "authorization",
            "bearer_token",
            "raw_config",
            "config_path",
            "absolute_config_path",
            "secret_reference_id",
            "masked_api_key",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_length",
            "api_key_fingerprint",
        ],
        "forbidden_status_signals": [
            "config_file_exists",
            "config_file_readable",
            "config_file_size",
            "config_file_mtime",
            "config_file_hash",
            "api_key_present",
            "api_key_valid",
            "api_key_length",
            "api_key_hash",
            "api_key_prefix",
            "api_key_suffix",
            "api_key_fingerprint",
        ],
        "block_reasons": [
            "dry_run_only",
            "config_file_read_not_authorized",
            "secret_value_read_not_authorized",
            "secret_storage_adapter_not_connected",
            "llm_executor_disabled",
        ],
        "next_required_decisions": [
            "authorized_config_file_reader",
            "os_keychain_adapter",
            "enterprise_secret_provider_adapter",
            "authorized_masked_status_loader",
            "enabled_executor_mode_contract",
        ],
    }


def _mac_local_shadow_mvp_streaming_events() -> list[dict[str, Any]]:
    return [
        {
            "event_type": "partial",
            "segment_id": "asr_seg_001",
            "text": "先灰度",
            "start_ms": 0,
            "end_ms": 1200,
            "received_at_ms": 1300,
            "confidence": 0.72,
        },
        {
            "event_type": "final",
            "segment_id": "asr_seg_001",
            "text": "先灰度 10%。",
            "start_ms": 0,
            "end_ms": 3200,
            "received_at_ms": 3500,
            "confidence": 0.91,
        },
        {
            "event_type": "revision",
            "segment_id": "asr_seg_001_rev1",
            "revision_of": "asr_seg_001",
            "text": "先灰度 5%，不是 10%。",
            "start_ms": 0,
            "end_ms": 3400,
            "received_at_ms": 5200,
            "confidence": 0.94,
        },
        {
            "event_type": "final",
            "segment_id": "asr_seg_002",
            "text": "谁负责回滚？",
            "start_ms": 3400,
            "end_ms": 6100,
            "received_at_ms": 7000,
            "confidence": 0.9,
        },
        {
            "event_type": "final",
            "segment_id": "asr_seg_003",
            "text": "如果错误率超过 0.1% 就回滚。",
            "start_ms": 6100,
            "end_ms": 8200,
            "received_at_ms": 8800,
            "confidence": 0.9,
        },
        {
            "event_type": "final",
            "segment_id": "asr_seg_004",
            "text": "张三下周三补充兼容性测试用例。",
            "start_ms": 8200,
            "end_ms": 10400,
            "received_at_ms": 11200,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "asr_eos",
            "text": "",
            "start_ms": 10400,
            "end_ms": 10400,
            "received_at_ms": 11400,
        },
    ]


def _mac_local_shadow_mvp_demo_response(
    *,
    session_id: str,
    live_events: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness = _desktop_real_mic_shadow_test_readiness()
    live_event_counts = _local_asr_live_event_counts(live_events)
    return {
        "demo_id": "mac_local_shadow_mvp",
        "demo_status": "synthetic_demo_session_created",
        "session_id": session_id,
        "provider": "local_mock_asr",
        "execution_boundary": "synthetic_events_only_no_mic_no_audio_file_no_remote_calls",
        "closure_status": "closed_to_no_llm_request_draft_and_readiness_blockers",
        "product_chain": [
            "synthetic_streaming_events",
            "transcript_partial_preview",
            "transcript_final_revision",
            "evidence_span",
            "meeting_state",
            "suggestion_candidate",
            "llm_request_draft_no_call",
            "real_mic_readiness_blocked",
        ],
        "event_source": asr_event_source_metadata("local_mock_asr", is_mock=True),
        "live_event_counts": live_event_counts,
        "all_llm_statuses": _local_asr_llm_statuses(live_events),
        "formal_card_creation_status": "not_created"
        if live_event_counts.get("suggestion_card", 0) == 0
        else "unexpected_card_created",
        "llm_execution_status": "not_called",
        "remote_asr_call_status": "not_called",
        "real_mic_shadow_readiness_status": readiness.get("readiness_status"),
        "user_can_start_real_mic_shadow_test_now": readiness.get(
            "user_can_start_real_mic_shadow_test_now",
            False,
        ),
        "readiness_blockers": _mac_local_shadow_mvp_readiness_blockers(readiness),
        "readiness_summary": readiness.get("readiness_summary", {}),
        "next_required_decision": readiness.get("next_required_decision"),
        "live_events": live_events,
        "safe_to_access_microphone_now": False,
        "safe_to_enumerate_audio_devices_now": False,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_real_user_audio_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
    }


def _mac_local_shadow_mvp_readiness_blockers(
    readiness: dict[str, Any],
) -> list[str]:
    blockers = [str(blocker) for blocker in readiness.get("blockers", [])]
    if readiness.get("asr_quality_exit_status") != "exited":
        blockers.insert(0, "asr_quality_gate_not_exited")
    return list(dict.fromkeys(blockers))


def _realistic_meeting_simulation_pack_streaming_events(
    profile_id: str = "standard",
) -> list[dict[str, Any]]:
    if profile_id == "long_shadow":
        return _long_shadow_realistic_meeting_simulation_events()
    return [
        {
            "event_type": "partial",
            "segment_id": "realistic_seg_001",
            "speaker": "主持人",
            "text": "我们今天看 payment gateway",
            "start_ms": 0,
            "end_ms": 1600,
            "received_at_ms": 1750,
            "confidence": 0.74,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_001",
            "speaker": "主持人",
            "text": "我们今天看 payment-gateway 发布，先灰度 20%。",
            "start_ms": 0,
            "end_ms": 4200,
            "received_at_ms": 4700,
            "confidence": 0.88,
        },
        {
            "event_type": "revision",
            "segment_id": "realistic_seg_001_rev1",
            "revision_of": "realistic_seg_001",
            "speaker": "主持人",
            "text": "我们今天看 payment-gateway 发布，先灰度 5%，不是 20%。",
            "start_ms": 0,
            "end_ms": 4400,
            "received_at_ms": 6100,
            "confidence": 0.93,
        },
        {
            "event_type": "partial",
            "segment_id": "realistic_seg_002",
            "speaker": "后端",
            "text": "如果 P99 超过八百",
            "start_ms": 6400,
            "end_ms": 8200,
            "received_at_ms": 8400,
            "confidence": 0.77,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_002",
            "speaker": "后端",
            "text": "如果 P99 延迟超过 800ms，错误率超过 0.1% 就回滚。",
            "start_ms": 6400,
            "end_ms": 11200,
            "received_at_ms": 11900,
            "confidence": 0.9,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_003",
            "speaker": "SRE",
            "text": "Kafka lag 如果超过 5000，告警可能延迟，需要先看监控指标。",
            "start_ms": 12400,
            "end_ms": 16800,
            "received_at_ms": 17600,
            "confidence": 0.87,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_004",
            "speaker": "测试",
            "text": "李四明天补充 feature flag 关闭后的兼容测试。",
            "start_ms": 18200,
            "end_ms": 21600,
            "received_at_ms": 22400,
            "confidence": 0.91,
        },
        {
            "event_type": "partial",
            "segment_id": "realistic_seg_005",
            "speaker": "产品",
            "text": "谁确认回滚",
            "start_ms": 23200,
            "end_ms": 24600,
            "received_at_ms": 24800,
            "confidence": 0.8,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_005",
            "speaker": "产品",
            "text": "谁确认回滚 owner？如果半夜触发降级，值班群怎么通知？",
            "start_ms": 23200,
            "end_ms": 28600,
            "received_at_ms": 29600,
            "confidence": 0.89,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_006",
            "speaker": "后端",
            "text": "张三下周三整理 request_id 和 trace_id 的排查脚本。",
            "start_ms": 30200,
            "end_ms": 34200,
            "received_at_ms": 35100,
            "confidence": 0.92,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_007",
            "speaker": "SRE",
            "text": "这里有重叠发言，Redis 缓存如果穿透会打到 MySQL，峰值 QPS 可能增多。",
            "start_ms": 34800,
            "end_ms": 39200,
            "received_at_ms": 40200,
            "confidence": 0.84,
        },
        {
            "event_type": "revision",
            "segment_id": "realistic_seg_007_rev1",
            "revision_of": "realistic_seg_007",
            "speaker": "SRE",
            "text": "这里有重叠发言，Redis 缓存如果穿透会打到 MySQL，峰值 QPS 可能增加，不是已经压测过。",
            "start_ms": 34800,
            "end_ms": 40200,
            "received_at_ms": 42400,
            "confidence": 0.86,
        },
        {
            "event_type": "final",
            "segment_id": "realistic_seg_008",
            "speaker": "主持人",
            "text": "王五今天确认发布窗口和 rollback checklist。",
            "start_ms": 42800,
            "end_ms": 47200,
            "received_at_ms": 48000,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "realistic_eos",
            "text": "",
            "start_ms": 47200,
            "end_ms": 47200,
            "received_at_ms": 48200,
        },
    ]


def _long_shadow_realistic_meeting_simulation_events() -> list[dict[str, Any]]:
    return [
        {
            "event_type": "partial",
            "segment_id": "long_shadow_seg_001",
            "speaker": "主持人",
            "text": "我们先看 architecture",
            "start_ms": 0,
            "end_ms": 1800,
            "received_at_ms": 2000,
            "confidence": 0.74,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_001",
            "speaker": "主持人",
            "text": "我们先看 architecture review，recommendation-service 先灰度 10%。",
            "start_ms": 0,
            "end_ms": 26000,
            "received_at_ms": 27600,
            "confidence": 0.88,
        },
        {
            "event_type": "revision",
            "segment_id": "long_shadow_seg_001_rev1",
            "revision_of": "long_shadow_seg_001",
            "speaker": "主持人",
            "text": "我们先看 architecture review，recommendation-service 先灰度 5%，不是 10%。",
            "start_ms": 0,
            "end_ms": 27000,
            "received_at_ms": 33800,
            "confidence": 0.93,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_002",
            "speaker": "后端",
            "text": "谁确认降级开关 owner？feature flag 半夜触发时值班群怎么通知？",
            "start_ms": 36000,
            "end_ms": 65000,
            "received_at_ms": 67600,
            "confidence": 0.89,
        },
        {
            "event_type": "partial",
            "segment_id": "long_shadow_seg_003",
            "speaker": "SRE",
            "text": "如果 Redis cluster",
            "start_ms": 72000,
            "end_ms": 74200,
            "received_at_ms": 76000,
            "confidence": 0.77,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_003",
            "speaker": "SRE",
            "text": "如果 Redis cluster 缓存穿透打到 MySQL，P99 超过 900ms 就触发 rollback。",
            "start_ms": 72000,
            "end_ms": 104000,
            "received_at_ms": 106500,
            "confidence": 0.87,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_004",
            "speaker": "测试",
            "text": "李四明天补充 idempotency-key 重试和 callback 失败的兼容测试。",
            "start_ms": 116000,
            "end_ms": 148000,
            "received_at_ms": 150000,
            "confidence": 0.91,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_005",
            "speaker": "产品",
            "text": "API 幂等和 idempotency-key 是否兼容老客户端？这个还没有确认。",
            "start_ms": 160000,
            "end_ms": 190000,
            "received_at_ms": 192500,
            "confidence": 0.88,
        },
        {
            "event_type": "partial",
            "segment_id": "long_shadow_seg_006",
            "speaker": "后端",
            "text": "如果 callback 失败超过",
            "start_ms": 205000,
            "end_ms": 207500,
            "received_at_ms": 209000,
            "confidence": 0.78,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_006",
            "speaker": "后端",
            "text": "如果 callback 失败超过 0.5%，订单服务会降级，监控指标要提前看。",
            "start_ms": 205000,
            "end_ms": 238000,
            "received_at_ms": 240500,
            "confidence": 0.9,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_007",
            "speaker": "SRE",
            "text": "张三下周三整理 request_id、trace_id 和 Kafka lag 的排查脚本。",
            "start_ms": 252000,
            "end_ms": 284000,
            "received_at_ms": 286200,
            "confidence": 0.91,
        },
        {
            "event_type": "revision",
            "segment_id": "long_shadow_seg_007_rev1",
            "revision_of": "long_shadow_seg_007",
            "speaker": "SRE",
            "text": "张三下周三整理 request_id、trace_id、Kafka lag 和 dead letter queue 的排查脚本。",
            "start_ms": 252000,
            "end_ms": 286000,
            "received_at_ms": 294000,
            "confidence": 0.92,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_008",
            "speaker": "主持人",
            "text": "SLO 99.9% 谁负责确认？如果 error budget 烧完是否暂停发布？",
            "start_ms": 304000,
            "end_ms": 335000,
            "received_at_ms": 337500,
            "confidence": 0.89,
        },
        {
            "event_type": "partial",
            "segment_id": "long_shadow_seg_009",
            "speaker": "SRE",
            "text": "Kafka lag 如果",
            "start_ms": 350000,
            "end_ms": 352200,
            "received_at_ms": 354000,
            "confidence": 0.78,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_009",
            "speaker": "SRE",
            "text": "Kafka lag 如果超过 8000，告警延迟会影响 rollback 判断。",
            "start_ms": 350000,
            "end_ms": 378000,
            "received_at_ms": 380500,
            "confidence": 0.88,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_010",
            "speaker": "测试",
            "text": "王五今天确认压测脚本和 P99 监控 dashboard。",
            "start_ms": 392000,
            "end_ms": 418000,
            "received_at_ms": 420500,
            "confidence": 0.91,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_011",
            "speaker": "产品",
            "text": "这里有重叠发言，老版本客户端如果没有 idempotency-key 会不会重复扣款？",
            "start_ms": 430000,
            "end_ms": 465000,
            "received_at_ms": 468000,
            "confidence": 0.84,
        },
        {
            "event_type": "partial",
            "segment_id": "long_shadow_seg_012",
            "speaker": "后端",
            "text": "发布窗口先定周四",
            "start_ms": 482000,
            "end_ms": 485000,
            "received_at_ms": 486800,
            "confidence": 0.79,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_012",
            "speaker": "后端",
            "text": "发布窗口先定周四，灰度 20%，rollback checklist 由赵六负责。",
            "start_ms": 482000,
            "end_ms": 515000,
            "received_at_ms": 518000,
            "confidence": 0.88,
        },
        {
            "event_type": "revision",
            "segment_id": "long_shadow_seg_012_rev1",
            "revision_of": "long_shadow_seg_012",
            "speaker": "后端",
            "text": "发布窗口先定周四，灰度 10%，rollback checklist 由赵六负责。",
            "start_ms": 482000,
            "end_ms": 516000,
            "received_at_ms": 528000,
            "confidence": 0.92,
        },
        {
            "event_type": "final",
            "segment_id": "long_shadow_seg_013",
            "speaker": "主持人",
            "text": "如果上线后错误率超过 0.1%，先降级 recommendation-service，再回滚 payment-gateway。",
            "start_ms": 548000,
            "end_ms": 615000,
            "received_at_ms": 618000,
            "confidence": 0.9,
        },
        {
            "event_type": "end_of_stream",
            "segment_id": "long_shadow_eos",
            "text": "",
            "start_ms": 615000,
            "end_ms": 615000,
            "received_at_ms": 619000,
        },
    ]


def _realistic_meeting_simulation_pack_response(
    *,
    session_id: str,
    live_events: list[dict[str, Any]],
    profile_id: str = "standard",
) -> dict[str, Any]:
    readiness = _desktop_real_mic_shadow_test_readiness()
    live_event_counts = _local_asr_live_event_counts(live_events)
    profile = _realistic_meeting_simulation_pack_profile(profile_id)
    return {
        "simulation_id": "realistic_meeting_simulation_pack",
        "simulation_status": "realistic_synthetic_session_created",
        "profile_id": profile_id,
        "scenario_id": profile["scenario_id"],
        "session_id": session_id,
        "provider": "local_mock_asr",
        "execution_boundary": "synthetic_realistic_events_only_no_mic_no_audio_file_no_remote_calls",
        "meeting_shape": profile["meeting_shape"],
        "realism_features": profile["realism_features"],
        "technical_terms": profile["technical_terms"],
        "shadow_report_preview_status": "draft_preview_available_after_sse_end",
        "product_chain": [
            "realistic_synthetic_meeting_turns",
            "partial_final_revision_events",
            "evidence_span",
            "meeting_state",
            "suggestion_candidate",
            "llm_request_draft_no_call",
            "real_mic_readiness_blocked",
        ],
        "event_source": asr_event_source_metadata("local_mock_asr", is_mock=True),
        "live_event_counts": live_event_counts,
        "all_llm_statuses": _local_asr_llm_statuses(live_events),
        "formal_card_creation_status": "not_created"
        if live_event_counts.get("suggestion_card", 0) == 0
        else "unexpected_card_created",
        "llm_execution_status": "not_called",
        "remote_asr_call_status": "not_called",
        "public_audio_download_status": "not_downloaded",
        "real_mic_shadow_readiness_status": readiness.get("readiness_status"),
        "user_can_start_real_mic_shadow_test_now": readiness.get(
            "user_can_start_real_mic_shadow_test_now",
            False,
        ),
        "readiness_blockers": _mac_local_shadow_mvp_readiness_blockers(readiness),
        "readiness_summary": readiness.get("readiness_summary", {}),
        "next_required_decision": readiness.get("next_required_decision"),
        "live_events": live_events,
        "safe_to_access_microphone_now": False,
        "safe_to_enumerate_audio_devices_now": False,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_real_user_audio_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
    }


def _mainline_asr_blocked_trial_response(
    *,
    session_id: str,
    live_events: list[dict[str, Any]],
) -> dict[str, Any]:
    readiness = _desktop_real_mic_shadow_test_readiness()
    live_event_counts = _local_asr_live_event_counts(live_events)
    return {
        "trial_id": "mainline_asr_blocked_trial",
        "trial_status": "mainline_trial_session_created",
        "session_id": session_id,
        "provider": "local_mock_asr",
        "execution_boundary": "synthetic_live_events_only_no_mic_no_audio_file_no_remote_calls",
        "mainline_decision_id": "DEC-201",
        "asr_quality_exit_status": "not_exited",
        "asr_quality_decision_status": "blocked_by_funasr_smoke_assembly_input_guard",
        "selected_product_route": "pc_product_flow_with_asr_quality_blocked_visible",
        "recommended_next_action": "continue_pc_product_flow_keep_real_mic_blocked",
        "product_chain": [
            "synthetic_live_event_replay",
            "transcript_final_revision",
            "evidence_span",
            "meeting_state",
            "suggestion_candidate",
            "llm_request_draft_no_call",
            "asr_quality_blocked_visible",
            "real_mic_shadow_test_blocked",
        ],
        "product_replay_summary": {
            "funasr_engineering_preview_created_count": 3,
            "funasr_engineering_scenario_count": 4,
            "mock_engineering_preview_created_count": 4,
            "negative_control_fake_candidate_count": 0,
            "failed_funasr_scenario_id": "incident-review-001",
        },
        "blocked_asr_candidates": [
            {
                "candidate_id": "chunk10_hotword",
                "rtf_range": "0.668-0.694",
                "engineering_normalized_recall": "1.00 / 0.80 / 0.25 / 0.50",
                "gate_status": "blocked",
                "quality_tradeoff": "quality_partial_speed_fails",
            },
            {
                "candidate_id": "chunk20_hotword",
                "rtf_range": "0.355-0.363",
                "engineering_normalized_recall": "0.50 / 0.60 / 0.25 / 0.50",
                "gate_status": "blocked",
                "quality_tradeoff": "speed_passes_quality_fails",
            },
        ],
        "event_source": asr_event_source_metadata("local_mock_asr", is_mock=True),
        "live_event_counts": live_event_counts,
        "all_llm_statuses": _local_asr_llm_statuses(live_events),
        "formal_card_creation_status": "not_created"
        if live_event_counts.get("suggestion_card", 0) == 0
        else "unexpected_card_created",
        "llm_execution_status": "not_called",
        "remote_asr_call_status": "not_called",
        "real_mic_shadow_readiness_status": readiness.get("readiness_status"),
        "user_can_start_real_mic_shadow_test_now": readiness.get(
            "user_can_start_real_mic_shadow_test_now",
            False,
        ),
        "readiness_blockers": _mac_local_shadow_mvp_readiness_blockers(readiness),
        "readiness_summary": readiness.get("readiness_summary", {}),
        "next_required_decision": readiness.get("next_required_decision"),
        "live_events": live_events,
        "safe_to_access_microphone_now": False,
        "safe_to_enumerate_audio_devices_now": False,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_real_user_audio_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
    }


def _mainline_asr_event_artifact_trial_response(
    *,
    session_id: str,
    provider: str,
    events_path: str,
    live_events: list[dict[str, Any]],
    streaming_events: list[dict[str, Any]],
) -> dict[str, Any]:
    live_event_counts = _local_asr_live_event_counts(live_events)
    return {
        "trial_id": "mainline_asr_event_artifact_trial",
        "trial_status": "mainline_artifact_trial_session_created",
        "session_id": session_id,
        "provider": provider,
        "ingest_mode": "mainline_asr_event_artifact_trial",
        "events_path": events_path,
        "execution_boundary": "approved_asr_event_artifact_handoff_no_audio_read_no_remote_calls",
        "mainline_decision_id": "DEC-214",
        "asr_quality_exit_status": "not_exited",
        "asr_quality_decision_status": "artifact_handoff_only_quality_not_proven",
        "selected_product_route": "pc_product_flow_with_asr_event_artifact_handoff_visible",
        "recommended_next_action": "run_feedback_export_preview_keep_real_mic_blocked",
        "product_chain": [
            "approved_asr_event_artifact",
            "web_live_asr_handoff",
            "transcript_final_revision",
            "evidence_span",
            "meeting_state",
            "suggestion_candidate",
            "llm_request_draft_no_call",
            "feedback_export_preview",
        ],
        "event_source": asr_event_source_metadata(provider, is_mock=False),
        "input_event_counts": _local_asr_input_event_counts(streaming_events),
        "live_event_counts": live_event_counts,
        "all_llm_statuses": _local_asr_llm_statuses(live_events),
        "formal_card_creation_status": "not_created"
        if live_event_counts.get("suggestion_card", 0) == 0
        else "unexpected_card_created",
        "source_event_artifact_status": "local_asr_event_file_handoff_created",
        "llm_execution_status": "not_called",
        "remote_asr_call_status": "not_called",
        "real_mic_shadow_readiness_status": "blocked_not_ready_for_user_real_mic_shadow_test",
        "user_can_start_real_mic_shadow_test_now": False,
        "readiness_blockers": [
            "asr_quality_not_exited",
            "explicit_user_start_required",
        ],
        "readiness_summary": {
            "artifact_handoff_verified": True,
            "real_meeting_go_evidence": False,
        },
        "next_required_decision": "fix_asr_quality_or_accept_explicit_degraded_pilot_before_real_mic",
        "live_events": live_events,
        "safe_to_read_asr_event_file_now": True,
        "safe_to_access_microphone_now": False,
        "safe_to_enumerate_audio_devices_now": False,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_real_user_audio_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "safe_to_download_public_audio_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
    }


def _mainline_trial_feedback_export_closure_from_record(
    record: dict[str, Any],
    *,
    feedback_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    source_trial_id = _mainline_trial_source_trial_id(record)
    if source_trial_id is None:
        return {
            "pcweb_id": "PCWEB-129",
            "closure_id": "mainline_trial_feedback_export_closure",
            "closure_status": "blocked_by_source_trial",
            "session_id": record.get("session_id"),
            "validation_errors": [
                "session must be created by mainline_asr_blocked_trial or mainline_asr_event_artifact_trial",
            ],
            **_mainline_trial_feedback_export_closure_safety_flags(),
        }

    candidate_report_result = _mainline_trial_candidate_report_from_record(record)
    candidate_report = candidate_report_result.get("candidate_report")
    if candidate_report_result.get("candidate_report_status") != "created":
        return {
            "pcweb_id": "PCWEB-129",
            "closure_id": "mainline_trial_feedback_export_closure",
            "closure_status": "blocked_by_candidate_report",
            "session_id": record.get("session_id"),
            "validation_errors": candidate_report_result.get("validation_errors") or [],
            **_mainline_trial_feedback_export_closure_safety_flags(),
        }

    feedback_tool = _load_shadow_report_feedback_ingestion_module()
    resolved_feedback_entries = (
        feedback_entries
        if feedback_entries
        else _default_mainline_trial_feedback_entries(
            candidate_report_result["selected_candidate_ids"]
        )
    )
    feedback_report = feedback_tool.build_shadow_report_feedback_ingestion(
        candidate_report=candidate_report,
        feedback_entries=resolved_feedback_entries,
    )
    if str(feedback_report.get("feedback_ingestion_status", "")).startswith("blocked_"):
        return {
            "pcweb_id": "PCWEB-129",
            "closure_id": "mainline_trial_feedback_export_closure",
            "closure_status": "blocked_by_feedback_ingestion",
            "session_id": record.get("session_id"),
            "source_trial_id": source_trial_id,
            "candidate_report_validation_status": feedback_report.get(
                "candidate_report_validation_status"
            ),
            "candidate_report_validation_errors": feedback_report.get(
                "candidate_report_validation_errors",
                [],
            ),
            "validation_errors": feedback_report.get("validation_errors", []),
            "feedback_ingestion_status": feedback_report.get("feedback_ingestion_status"),
            **_mainline_trial_feedback_export_closure_safety_flags(),
        }

    readiness_report = feedback_report.get("readiness_report") or {}
    updated_candidate_report = feedback_report.get("updated_candidate_report") or candidate_report
    return {
        "pcweb_id": "PCWEB-129",
        "closure_id": "mainline_trial_feedback_export_closure",
        "closure_status": "mainline_trial_feedback_export_preview_created",
        "session_id": record.get("session_id"),
        "source_trial_id": source_trial_id,
        "source_event_artifact_status": "local_asr_event_file_handoff_created"
        if source_trial_id == "mainline_asr_event_artifact_trial"
        else "not_applicable",
        "source_review_type": "asr_live_draft",
        "candidate_report_validation_status": feedback_report.get(
            "candidate_report_validation_status"
        ),
        "candidate_report_validation_errors": feedback_report.get(
            "candidate_report_validation_errors",
            [],
        ),
        "feedback_ingestion_status": feedback_report.get("feedback_ingestion_status"),
        "feedback_entry_count": feedback_report.get("feedback_entry_count", 0),
        "feedback_summary_delta": feedback_report.get("feedback_summary_delta"),
        "feedback_analysis": readiness_report.get("feedback_analysis"),
        "feedback_collection_status": readiness_report.get("feedback_collection_status"),
        "final_decision_readiness_status": readiness_report.get(
            "final_decision_readiness_status"
        ),
        "export_readiness_status": readiness_report.get("export_readiness_status"),
        "go_evidence_status": feedback_report.get("go_evidence_status"),
        "final_decision": updated_candidate_report.get("final_decision"),
        "selected_candidate_ids": candidate_report_result["selected_candidate_ids"],
        "timeline_counts": readiness_report.get("timeline_counts") or {},
        "json_export_preview": readiness_report.get("json_export_preview"),
        "markdown_export_preview": readiness_report.get("markdown_export_preview"),
        "not_go_reason": "synthetic mainline replay feedback cannot be used as real mic Go evidence",
        **_mainline_trial_feedback_export_closure_safety_flags(),
    }


def _mainline_trial_source_trial_id(record: dict[str, Any]) -> str | None:
    ingest_mode = record.get("ingest_mode")
    if ingest_mode == "mainline_asr_blocked_trial":
        return "mainline_asr_blocked_trial"
    if ingest_mode == "mainline_asr_event_artifact_trial":
        return "mainline_asr_event_artifact_trial"
    return None


def _mainline_trial_candidate_report_from_record(
    record: dict[str, Any],
) -> dict[str, Any]:
    review = build_asr_live_draft_review(record)
    candidates = [
        candidate
        for candidate in review.get("suggestion_candidates") or []
        if candidate.get("candidate_id") and candidate.get("evidence_span_ids")
    ]
    selected_candidates = candidates[:2]
    if len(selected_candidates) < 2:
        return {
            "candidate_report_status": "blocked",
            "validation_errors": [
                "mainline trial needs at least two suggestion candidates with evidence",
            ],
        }

    transcript_by_id = {
        str(segment.get("id")): segment
        for segment in review.get("transcript_segments") or []
        if segment.get("id")
    }
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for evidence in review.get("evidence_spans") or []:
        evidence_id = str(evidence.get("id", ""))
        if evidence_id and evidence_id not in evidence_by_id:
            evidence_by_id[evidence_id] = evidence

    candidate_cards: list[dict[str, Any]] = []
    evidence_timeline: list[dict[str, Any]] = []
    transcript_segments: list[dict[str, Any]] = []
    state_timeline: list[dict[str, Any]] = []
    added_evidence_ids: set[str] = set()
    added_segment_ids: set[str] = set()
    selected_candidate_ids: list[str] = []

    for index, candidate in enumerate(selected_candidates):
        candidate_id = str(candidate["candidate_id"])
        selected_candidate_ids.append(candidate_id)
        evidence_ids = [
            str(evidence_id)
            for evidence_id in candidate.get("evidence_span_ids") or []
            if str(evidence_id) in evidence_by_id
        ]
        if not evidence_ids:
            return {
                "candidate_report_status": "blocked",
                "validation_errors": [
                    f"candidate {candidate_id} does not reference available evidence",
                ],
            }
        for evidence_id in evidence_ids:
            evidence = evidence_by_id[evidence_id]
            segment_id = str(evidence.get("segment_id", ""))
            if evidence_id not in added_evidence_ids:
                evidence_timeline.append(
                    {
                        "evidence_id": evidence_id,
                        "segment_id": segment_id,
                        "start_ms": int(evidence.get("start_ms", 0)),
                        "end_ms": int(evidence.get("end_ms", 0)),
                        "text": str(evidence.get("quote", "")),
                        "supports_candidate_id": candidate_id,
                    }
                )
                added_evidence_ids.add(evidence_id)
            if segment_id and segment_id not in added_segment_ids:
                transcript_segments.append(
                    _mainline_trial_transcript_segment(
                        transcript_by_id.get(segment_id),
                        evidence=evidence,
                    )
                )
                added_segment_ids.add(segment_id)
        candidate_cards.append(
            {
                "candidate_id": candidate_id,
                "card_type": str(candidate.get("candidate_type") or "state_gap_review"),
                "created_at_ms": _mainline_trial_candidate_created_at_ms(evidence_ids, evidence_by_id),
                "latency_ms": 0,
                "evidence_ids": evidence_ids,
                "text": str(candidate.get("suggested_prompt", "")),
            }
        )
        state_timeline.append(
            _mainline_trial_state_item(
                review=review,
                candidate=candidate,
                evidence_id=evidence_ids[0],
                fallback_at_ms=index,
                evidence_by_id=evidence_by_id,
            )
        )

    max_end_ms = max(
        [
            int(segment.get("end_ms", 0))
            for segment in review.get("transcript_segments") or []
            if isinstance(segment, dict)
        ]
        or [0]
    )
    candidate_report = {
        "schema_version": "real_mic_shadow_test_report.v1",
        "session_id": "mainline-closure-" + str(review.get("session_id", "")),
        "meeting_profile": {
            "meeting_type": "chinese_technical_review",
            "duration_minutes": max(1, (max_end_ms + 59_999) // 60_000),
            "participant_count": 5,
            "language": "zh-CN",
            "domain_tags": [
                "architecture",
                "release",
                "incident",
                "asr_blocked",
                "synthetic",
            ],
        },
        "transcript": {
            "segment_count": len(transcript_segments),
            "segments": transcript_segments,
        },
        "asr_metrics": _mainline_trial_asr_metrics(review, max_end_ms),
        "evidence_span_timeline": evidence_timeline,
        "state_timeline": state_timeline,
        "candidate_card_timeline": candidate_cards,
        "feedback_summary": _mainline_trial_empty_feedback_summary(),
        "final_decision": {
            "decision": "inconclusive_requires_more_shadow_tests",
            "reason": "Synthetic mainline replay cannot be used as Go evidence.",
        },
        "privacy_cost_flags": {
            "raw_audio_uploaded": False,
            "remote_asr_called": False,
            "llm_called": False,
            "configs_local_read": False,
            "user_audio_committed_to_repo": False,
        },
        "audio_retention": {
            "audio_chunk_root": "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
            "audio_chunk_write_status": "not_written",
            "audio_delete_status": "not_applicable_no_audio_written",
            "retention_policy": "delete_audio_chunks_before_session_discard",
        },
        "known_limitations": [
            "Synthetic replay preview only; not real microphone evidence.",
            "No remote ASR or LLM call was made during closure generation.",
            "Feedback labels are deterministic local defaults unless explicitly provided.",
        ],
    }
    return {
        "candidate_report_status": "created",
        "candidate_report": candidate_report,
        "selected_candidate_ids": selected_candidate_ids,
    }


def _mainline_trial_transcript_segment(
    segment: dict[str, Any] | None,
    *,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    segment_id = str((segment or {}).get("id") or evidence.get("segment_id", ""))
    start_ms = int((segment or {}).get("start_ms", evidence.get("start_ms", 0)))
    end_ms = int((segment or {}).get("end_ms", evidence.get("end_ms", start_ms)))
    return {
        "segment_id": segment_id,
        "speaker_label": "speaker_unknown",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "text": str((segment or {}).get("text") or evidence.get("quote", "")),
        "source_event_id": str((segment or {}).get("event_type") or "evidence") + ":" + segment_id,
    }


def _mainline_trial_candidate_created_at_ms(
    evidence_ids: list[str],
    evidence_by_id: dict[str, dict[str, Any]],
) -> int:
    return max(
        [
            int(evidence_by_id[evidence_id].get("end_ms", 0))
            for evidence_id in evidence_ids
            if evidence_id in evidence_by_id
        ]
        or [0]
    )


def _mainline_trial_state_item(
    *,
    review: dict[str, Any],
    candidate: dict[str, Any],
    evidence_id: str,
    fallback_at_ms: int,
    evidence_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target_id = str(candidate.get("target_id", ""))
    matching_state = next(
        (
            state
            for state in review.get("state_candidates") or []
            if str(state.get("target_id", "")) == target_id
        ),
        None,
    )
    evidence = evidence_by_id.get(evidence_id, {})
    return {
        "state_id": str(
            (matching_state or {}).get("target_id")
            or target_id
            or candidate.get("candidate_id", "")
        ),
        "state_type": str(
            (matching_state or {}).get("target_type")
            or candidate.get("target_type")
            or "state_gap_review"
        ),
        "at_ms": int(evidence.get("start_ms", fallback_at_ms)),
        "evidence_id": evidence_id,
    }


def _mainline_trial_asr_metrics(
    review: dict[str, Any],
    max_end_ms: int,
) -> dict[str, int | float]:
    summary = review.get("evaluation_summary") or {}
    return {
        "duration_seconds": round(max_end_ms / 1000, 3),
        "first_partial_latency_ms": 0,
        "final_latency_p95_ms": 0,
        "rtf": 0,
        "raw_cer": 0,
        "normalized_cer": 0,
        "raw_technical_entity_recall": 0,
        "normalized_technical_entity_recall": 0,
        "technical_entity_precision": 0,
        "error_event_count": int(summary.get("error_event_count", 0)),
        "end_of_stream_event_count": int(summary.get("end_of_stream_event_count", 1)),
    }


def _mainline_trial_empty_feedback_summary() -> dict[str, Any]:
    labels = {
        "useful": 0,
        "would_have_asked": 0,
        "wrong": 0,
        "too_late": 0,
        "too_intrusive": 0,
        "dismissed": 0,
    }
    return {
        "labels": labels,
        "useful_or_would_have_asked_count": 0,
        "negative_feedback_count": 0,
    }


def _default_mainline_trial_feedback_entries(
    selected_candidate_ids: list[str],
) -> list[dict[str, str]]:
    labels = ["useful", "would_have_asked"]
    return [
        {"candidate_id": candidate_id, "label": labels[index]}
        for index, candidate_id in enumerate(selected_candidate_ids[:2])
    ]


def _mainline_trial_feedback_export_closure_safety_flags() -> dict[str, bool]:
    return {
        "safe_to_access_microphone_now": False,
        "safe_to_enumerate_audio_devices_now": False,
        "safe_to_request_audio_permission_now": False,
        "safe_to_capture_microphone_now": False,
        "safe_to_read_user_audio_now": False,
        "safe_to_read_real_user_audio_now": False,
        "safe_to_write_audio_chunk_now": False,
        "safe_to_delete_audio_chunk_now": False,
        "safe_to_read_configs_local_now": False,
        "safe_to_call_remote_asr_now": False,
        "safe_to_call_llm_now": False,
        "safe_to_download_models_now": False,
        "safe_to_run_tauri_or_cargo_now": False,
        "safe_to_mutate_web_session_now": False,
        "safe_to_write_candidate_report_now": False,
    }


def _realistic_meeting_simulation_pack_profile(profile_id: str) -> dict[str, Any]:
    if profile_id == "long_shadow":
        return {
            "scenario_id": "pcweb_127_long_architecture_release_review",
            "meeting_shape": {
                "speaker_count": 5,
                "speaker_turn_count": 16,
                "duration_seconds": 615.0,
                "overlap_marker_count": 2,
                "pause_marker_count": 5,
                "revision_count": 3,
            },
            "realism_features": [
                "multi_speaker_turns",
                "long_meeting_timeline",
                "architecture_review",
                "incident_followup",
                "partial_corrections",
                "revision_after_misheard_number",
                "pause_gap_markers",
                "overlap_marker",
                "technical_term_dense_release_incident_review",
                "no_remote_provider_no_audio_file",
            ],
            "technical_terms": [
                "recommendation-service",
                "payment-gateway",
                "idempotency-key",
                "Redis cluster",
                "MySQL",
                "P99",
                "SLO",
                "Kafka lag",
                "rollback",
                "feature flag",
            ],
        }
    return {
        "scenario_id": "pcweb_126_release_incident_review",
        "meeting_shape": {
            "speaker_count": 4,
            "speaker_turn_count": 8,
            "duration_seconds": 47.2,
            "overlap_marker_count": 1,
            "pause_marker_count": 2,
            "revision_count": 2,
        },
        "realism_features": [
            "multi_speaker_turns",
            "partial_corrections",
            "revision_after_misheard_number",
            "pause_gap_markers",
            "overlap_marker",
            "technical_term_dense_release_incident_review",
            "no_remote_provider_no_audio_file",
        ],
        "technical_terms": [
            "payment-gateway",
            "P99",
            "0.1%",
            "Kafka lag",
            "rollback",
            "feature flag",
        ],
    }


def _desktop_shell_readiness() -> dict[str, Any]:
    phases = [
        {
            "phase_id": "desktop_shell_runtime",
            "phase_status": "blocked",
            "phase_mode": "not_started",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "desktop_shell_status",
            "source_status_value": "not_started",
        },
        {
            "phase_id": "target_platform",
            "phase_status": "planned",
            "phase_mode": "macos_first",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "target_platform_status",
            "source_status_value": "macos_first_windows_deferred",
        },
        {
            "phase_id": "microphone_permission",
            "phase_status": "blocked",
            "phase_mode": "not_requested",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "microphone_permission_status",
            "source_status_value": "not_requested",
        },
        {
            "phase_id": "system_audio_permission",
            "phase_status": "blocked",
            "phase_mode": "not_requested",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "system_audio_permission_status",
            "source_status_value": "not_requested",
        },
        {
            "phase_id": "audio_source_separation",
            "phase_status": "blocked",
            "phase_mode": "not_connected",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "audio_capture_status",
            "source_status_value": "not_connected",
        },
        {
            "phase_id": "asr_worker_lifecycle",
            "phase_status": "blocked",
            "phase_mode": "not_started",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "asr_worker_status",
            "source_status_value": "not_started",
        },
        {
            "phase_id": "local_data_lifecycle",
            "phase_status": "needs_decision",
            "phase_mode": "policy_only",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "local_data_dir_status",
            "source_status_value": "not_created",
        },
        {
            "phase_id": "packaging_distribution",
            "phase_status": "blocked",
            "phase_mode": "not_started",
            "item_count": 3,
            "safe_to_proceed": False,
            "source_status_field": "packaging_status",
            "source_status_value": "not_started",
        },
    ]
    return {
        "desktop_readiness_mode": "preflight_only",
        "desktop_readiness_status": "blocked_before_desktop_shell",
        "desktop_shell_status": "not_started",
        "target_platform_status": "macos_first_windows_deferred",
        "audio_capture_status": "not_connected",
        "microphone_permission_status": "not_requested",
        "system_audio_permission_status": "not_requested",
        "asr_worker_status": "not_started",
        "llm_provider_status": "not_connected",
        "local_data_dir_status": "not_created",
        "packaging_status": "not_started",
        "desktop_readiness_phase_count": len(phases),
        "desktop_readiness_phases": phases,
        "desktop_readiness_blockers": [
            "desktop_shell_not_selected",
            "audio_capture_not_connected",
            "permissions_not_requested",
            "asr_worker_not_started",
            "packaging_not_started",
        ],
        "desktop_readiness_next_decisions": [
            "choose_desktop_shell_runtime",
            "define_macos_audio_permission_ux",
            "define_mic_and_system_audio_source_separation",
            "define_asr_worker_lifecycle_and_resource_limits",
            "define_local_data_directory_and_retention_policy",
            "define_packaging_signing_notarization_path",
        ],
        "desktop_safe_to_capture_audio": False,
        "desktop_safe_to_request_permissions": False,
        "desktop_safe_to_start_asr_worker": False,
        "desktop_safe_to_call_remote_asr": False,
        "desktop_safe_to_call_llm": False,
        "desktop_safe_to_write_audio_chunks": False,
    }


def _desktop_asr_worker_handoff_dry_run_readiness() -> dict[str, Any]:
    phases = [
        {
            "phase_id": "pcweb_096_policy",
            "phase_status": "available",
            "phase_mode": "policy_static_report",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "pcweb_096_default_dry_run_status",
            "source_status_value": "preview_ready_no_web_mutation",
        },
        {
            "phase_id": "descriptor_preflight",
            "phase_status": "ready_for_preview",
            "phase_mode": "pcweb_095_reused",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "descriptor_preflight_status",
            "source_status_value": "required_before_synthetic_local_test",
        },
        {
            "phase_id": "approved_event_file_root",
            "phase_status": "scoped",
            "phase_mode": "artifacts_tmp_only",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "approved_event_file_root",
            "source_status_value": LOCAL_ASR_EVENTS_APPROVED_ROOT,
        },
        {
            "phase_id": "synthetic_local_test",
            "phase_status": "explicit_only",
            "phase_mode": "fastapi_testclient_only",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "synthetic_local_test_status",
            "source_status_value": "explicit_mode_only",
        },
        {
            "phase_id": "web_handoff_mutation",
            "phase_status": "not_started",
            "phase_mode": "preview_only_default",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "web_handoff_mutation_status",
            "source_status_value": "not_mutated",
        },
        {
            "phase_id": "desktop_runtime_binding",
            "phase_status": "planned",
            "phase_mode": "web_tauri_noop_ui",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "next_pcweb_id",
            "source_status_value": "PCWEB-106",
        },
        {
            "phase_id": "mic_adapter_contract",
            "phase_status": "specified",
            "phase_mode": "contract_only_no_audio_permission_noop_ui",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "mic_adapter_contract_status",
            "source_status_value": "specified_not_executable",
        },
    ]
    return {
        "pcweb_id": "PCWEB-096",
        "next_pcweb_id": "PCWEB-106",
        "desktop_asr_worker_handoff_dry_run_mode": "readiness_only",
        "desktop_asr_worker_handoff_dry_run_status": "preview_only_ready",
        "pcweb_096_default_dry_run_status": "preview_ready_no_web_mutation",
        "synthetic_local_test_status": "explicit_mode_only",
        "descriptor_preflight_status": "required_before_synthetic_local_test",
        "worker_execution_status": "not_started",
        "event_file_read_status": "not_read",
        "web_handoff_mutation_status": "not_mutated",
        "handoff_api_endpoint": "/live/asr/local-event-files/sessions",
        "approved_event_file_root": LOCAL_ASR_EVENTS_APPROVED_ROOT,
        "approved_temp_web_data_dir_root": "artifacts/tmp/desktop_handoff_dry_run",
        "desktop_asr_handoff_phase_count": len(phases),
        "desktop_asr_handoff_phases": phases,
        "desktop_asr_handoff_blockers": [
            "asr_worker_not_started",
            "command_runner_binding_not_approved",
            "command_runner_implementation_skeleton_not_approved",
            "mic_adapter_not_bound_to_desktop_runtime",
            "synthetic_local_test_requires_explicit_mode",
            "real_audio_source_forbidden_before_mic_adapter",
            "funasr_model_dir_or_approval_missing",
            "tauri_cargo_run_not_authorized",
        ],
        "desktop_asr_handoff_next_decisions": [
            "surface_mic_adapter_contract_readiness_ui",
            "provide_funasr_local_model_dir_or_model_approval",
            "implement_worker_process_contract_after_desktop_runtime",
            "run_tauri_noop_after_explicit_cargo_tauri_approval",
        ],
        "desktop_asr_handoff_safe_to_start_worker": False,
        "command_runner_binding_status": "not_bound",
        "command_runner_implementation_skeleton_status": "not_bound_no_dispatch",
        "command_runner_execution_status": "not_executed",
        "mic_adapter_contract_status": "specified_not_executable",
        "desktop_asr_handoff_safe_to_bind_command_runner": False,
        "desktop_asr_handoff_safe_to_accept_worker_command": False,
        "desktop_asr_handoff_safe_to_dispatch_worker_command": False,
        "desktop_asr_handoff_safe_to_run_subprocess": False,
        "desktop_asr_handoff_safe_to_invoke_tauri_ipc": False,
        "desktop_asr_handoff_safe_to_request_audio_permission": False,
        "desktop_asr_handoff_safe_to_capture_audio": False,
        "desktop_asr_handoff_safe_to_read_real_audio": False,
        "desktop_asr_handoff_safe_to_read_configs_local": False,
        "desktop_asr_handoff_safe_to_call_remote_asr": False,
        "desktop_asr_handoff_safe_to_call_llm": False,
        "desktop_asr_handoff_safe_to_download_models": False,
        "desktop_asr_handoff_safe_to_run_tauri_or_cargo": False,
        "desktop_asr_handoff_safe_to_mutate_web_session_now": False,
    }


def _load_desktop_mic_adapter_contract_module() -> Any:
    tool_path = SOURCE_REPO_ROOT / "tools" / "desktop_mic_adapter_contract.py"
    spec = importlib.util.spec_from_file_location(
        "meeting_copilot_desktop_mic_adapter_contract",
        tool_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("desktop mic adapter contract tool is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_desktop_tauri_noop_run_result_intake_module() -> Any:
    tool_path = SOURCE_REPO_ROOT / "tools" / "desktop_tauri_noop_run_result_intake.py"
    spec = importlib.util.spec_from_file_location(
        "meeting_copilot_desktop_tauri_noop_run_result_intake",
        tool_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("desktop Tauri no-op run result intake tool is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _load_real_mic_shadow_test_readiness_module() -> Any:
    tool_path = SOURCE_REPO_ROOT / "tools" / "real_mic_shadow_test_readiness_gate.py"
    spec = importlib.util.spec_from_file_location(
        "meeting_copilot_real_mic_shadow_test_readiness_gate",
        tool_path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("real mic shadow test readiness tool is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _desktop_real_mic_shadow_test_readiness() -> dict[str, Any]:
    readiness_tool = _load_real_mic_shadow_test_readiness_module()
    return readiness_tool.build_real_mic_shadow_test_readiness_report()


def _desktop_mic_adapter_contract_readiness() -> dict[str, Any]:
    mic_contract = _load_desktop_mic_adapter_contract_module()
    contract_report = mic_contract.build_desktop_mic_adapter_contract_report()
    command_catalog = contract_report.get("command_transition_catalog", [])
    safe_flags = {
        flag: contract_report.get(flag, False)
        for flag in getattr(mic_contract, "FALSE_SAFETY_FLAGS", ())
    }
    return {
        "pcweb_id": "PCWEB-106",
        "source_pcweb_id": contract_report.get("pcweb_id", "PCWEB-105"),
        "readiness_mode": "readiness_only_no_mic_permission",
        "mic_adapter_ui_status": "ready_noop_contract_visible",
        "mic_adapter_contract_status": contract_report.get("mic_adapter_contract_status"),
        "contract_version": getattr(
            mic_contract,
            "CONTRACT_VERSION",
            "desktop_mic_adapter_contract.v1",
        ),
        "adapter_execution_status": contract_report.get("adapter_execution_status"),
        "permission_request_status": contract_report.get("permission_request_status"),
        "audio_capture_status": contract_report.get("audio_capture_status"),
        "audio_chunk_write_status": contract_report.get("audio_chunk_write_status"),
        "audio_chunk_delete_status": contract_report.get("audio_chunk_delete_status"),
        "user_start_boundary": contract_report.get("user_start_boundary"),
        "approved_runtime_audio_root": getattr(
            mic_contract,
            "APPROVED_RUNTIME_AUDIO_ROOT",
            "artifacts/tmp/desktop_mic_adapter_runtime",
        ),
        "approved_audio_chunk_root": getattr(
            mic_contract,
            "APPROVED_AUDIO_CHUNK_ROOT",
            "artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks",
        ),
        "delete_semantics": contract_report.get("delete_semantics"),
        "mic_adapter_command_count": len(command_catalog),
        "mic_adapter_command_catalog": command_catalog,
        "mic_adapter_readiness_blockers": [
            "mic_adapter_not_bound_to_desktop_runtime",
            "audio_permission_not_requested",
            "real_capture_requires_future_explicit_user_start",
            "asr_worker_not_connected",
            "tauri_cargo_run_not_authorized",
        ],
        "mic_adapter_readiness_next_decisions": [
            "bind_mic_adapter_noop_ipc_after_tauri_approval",
            "connect_mic_adapter_output_to_asr_worker_after_noop_smoke",
            "run_user_real_mic_shadow_test_after_start_pause_resume_stop_delete",
        ],
        **safe_flags,
    }


def _desktop_runtime_boundary() -> dict[str, Any]:
    phases = [
        {
            "phase_id": "runtime_recommendation",
            "phase_status": "recommended",
            "phase_mode": "tauri_first",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "recommended_desktop_runtime",
            "source_status_value": "tauri_first_electron_fallback",
        },
        {
            "phase_id": "process_model",
            "phase_status": "planned",
            "phase_mode": "ui_bridge_worker_split",
            "item_count": 4,
            "safe_to_proceed": False,
            "source_status_field": "desktop_process_model_status",
            "source_status_value": "planned_not_started",
        },
        {
            "phase_id": "ui_reuse",
            "phase_status": "planned",
            "phase_mode": "static_assets",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "ui_reuse_status",
            "source_status_value": "web_mvp_static_assets_reusable",
        },
        {
            "phase_id": "core_isolation",
            "phase_status": "satisfied",
            "phase_mode": "platform_independent",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "core_isolation_status",
            "source_status_value": "platform_independent",
        },
        {
            "phase_id": "native_bridge",
            "phase_status": "blocked",
            "phase_mode": "not_created",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "native_bridge_status",
            "source_status_value": "not_created",
        },
        {
            "phase_id": "asr_sidecar_worker",
            "phase_status": "planned",
            "phase_mode": "sidecar_worker",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "asr_worker_process_model",
            "source_status_value": "sidecar_worker_planned",
        },
        {
            "phase_id": "platform_targets",
            "phase_status": "planned",
            "phase_mode": "macos_first_windows_deferred",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "macos_target_status",
            "source_status_value": "apple_silicon_first",
        },
        {
            "phase_id": "packaging_pipeline",
            "phase_status": "blocked",
            "phase_mode": "not_started",
            "item_count": 3,
            "safe_to_proceed": False,
            "source_status_field": "packaging_pipeline_status",
            "source_status_value": "not_started",
        },
    ]
    return {
        "desktop_runtime_mode": "decision_preflight_only",
        "desktop_runtime_boundary_status": "blocked_before_runtime_creation",
        "recommended_desktop_runtime": "tauri_first_electron_fallback",
        "desktop_runtime_decision_status": "recommended_not_created",
        "desktop_process_model_status": "planned_not_started",
        "ui_reuse_status": "web_mvp_static_assets_reusable",
        "core_isolation_status": "platform_independent",
        "native_bridge_status": "not_created",
        "asr_worker_process_model": "sidecar_worker_planned",
        "packaging_pipeline_status": "not_started",
        "macos_target_status": "apple_silicon_first",
        "windows_target_status": "deferred_adapter",
        "desktop_runtime_phase_count": len(phases),
        "desktop_runtime_phases": phases,
        "desktop_runtime_blockers": [
            "desktop_runtime_not_created",
            "native_bridge_not_created",
            "asr_sidecar_not_spawned",
            "packaging_pipeline_not_started",
            "permissions_not_designed",
        ],
        "desktop_runtime_next_decisions": [
            "create_tauri_shell_spike",
            "define_native_bridge_command_contract",
            "define_asr_sidecar_worker_packaging",
            "define_macos_permission_preflight_copy",
            "define_desktop_update_and_distribution_policy",
            "define_windows_adapter_followup",
        ],
        "desktop_runtime_safe_to_create_shell": False,
        "desktop_runtime_safe_to_start_native_bridge": False,
        "desktop_runtime_safe_to_spawn_worker": False,
        "desktop_runtime_safe_to_package_installer": False,
        "desktop_runtime_safe_to_request_permissions": False,
        "desktop_runtime_safe_to_capture_audio": False,
        "desktop_runtime_safe_to_call_remote_asr": False,
        "desktop_runtime_safe_to_call_llm": False,
    }


def _desktop_native_bridge_contract() -> dict[str, Any]:
    commands = [
        {
            "command_id": "runtime.get_status",
            "command_group": "runtime",
            "effect_class": "read_only_status",
            "requires_explicit_user_action": False,
            "read_set": ["desktop_shell_runtime_state", "native_bridge_state"],
            "write_set": [],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "tauri_runtime_status_adapter",
            "failure_mode": "runtime_unavailable",
            "security_classification": "local_status_only",
        },
        {
            "command_id": "session.prepare",
            "command_group": "session",
            "effect_class": "future_local_metadata_write",
            "requires_explicit_user_action": True,
            "read_set": ["selected_meeting_profile", "local_data_policy"],
            "write_set": ["future_session_directory_manifest"],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "local_session_storage_adapter",
            "failure_mode": "local_storage_unavailable",
            "security_classification": "local_session_metadata",
        },
        {
            "command_id": "audio.permissions_status",
            "command_group": "audio",
            "effect_class": "permission_status_read",
            "requires_explicit_user_action": False,
            "read_set": ["macos_microphone_permission_state", "macos_system_audio_permission_state"],
            "write_set": [],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "macos_permission_status_adapter",
            "failure_mode": "permission_status_unavailable",
            "security_classification": "local_permission_metadata",
        },
        {
            "command_id": "audio.devices_list",
            "command_group": "audio",
            "effect_class": "device_metadata_read",
            "requires_explicit_user_action": True,
            "read_set": ["future_audio_device_catalog"],
            "write_set": [],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "platform_audio_device_adapter",
            "failure_mode": "device_enumeration_unavailable",
            "security_classification": "local_device_metadata",
        },
        {
            "command_id": "audio.capture_start",
            "command_group": "audio",
            "effect_class": "future_audio_capture_start",
            "requires_explicit_user_action": True,
            "read_set": ["selected_audio_source", "permission_state", "buffer_policy"],
            "write_set": ["future_ephemeral_audio_buffer"],
            "spawns_process": False,
            "captures_audio": True,
            "calls_remote_provider": False,
            "future_adapter": "platform_audio_capture_adapter",
            "failure_mode": "permission_denied_or_device_busy",
            "security_classification": "local_audio_sensitive",
        },
        {
            "command_id": "audio.capture_stop",
            "command_group": "audio",
            "effect_class": "future_audio_capture_stop",
            "requires_explicit_user_action": True,
            "read_set": ["capture_session_state"],
            "write_set": ["future_capture_stop_marker"],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "platform_audio_capture_adapter",
            "failure_mode": "capture_session_missing",
            "security_classification": "local_audio_sensitive",
        },
        {
            "command_id": "asr_worker.start",
            "command_group": "asr_worker",
            "effect_class": "future_sidecar_process_spawn",
            "requires_explicit_user_action": True,
            "read_set": ["local_asr_model_path", "worker_resource_policy"],
            "write_set": ["future_worker_pid_file", "future_worker_health_state"],
            "spawns_process": True,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "asr_sidecar_process_adapter",
            "failure_mode": "worker_binary_or_model_unavailable",
            "security_classification": "local_process_control",
        },
        {
            "command_id": "asr_worker.health",
            "command_group": "asr_worker",
            "effect_class": "worker_status_read",
            "requires_explicit_user_action": False,
            "read_set": ["future_worker_health_state"],
            "write_set": [],
            "spawns_process": False,
            "captures_audio": False,
            "calls_remote_provider": False,
            "future_adapter": "asr_sidecar_process_adapter",
            "failure_mode": "worker_not_started",
            "security_classification": "local_process_metadata",
        },
    ]
    commands = [
        {
            **command,
            "command_status": "contract_only",
            "implementation_status": "not_bound",
            "transport_status": "not_created",
            "side_effect_status": "forbidden",
            "safe_to_execute_now": False,
            "safe_to_invoke": False,
            "request_schema_status": "outline_only",
            "response_schema_status": "outline_only",
        }
        for command in commands
    ]
    phases = [
        {
            "phase_id": "contract_catalog",
            "phase_status": "specified",
            "phase_mode": "response_only",
            "item_count": len(commands),
            "safe_to_proceed": False,
            "source_status_field": "bridge_command_contract_status",
            "source_status_value": "specified_not_bound",
        },
        {
            "phase_id": "transport_binding",
            "phase_status": "not_created",
            "phase_mode": "no_ipc",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "bridge_transport_status",
            "source_status_value": "not_created",
        },
        {
            "phase_id": "runtime_adapter",
            "phase_status": "not_created",
            "phase_mode": "tauri_planned",
            "item_count": 1,
            "safe_to_proceed": False,
            "source_status_field": "bridge_platform_adapter_status",
            "source_status_value": "not_created",
        },
        {
            "phase_id": "permission_adapter",
            "phase_status": "not_requested",
            "phase_mode": "status_contract_only",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "desktop_bridge_safe_to_request_permissions",
            "source_status_value": False,
        },
        {
            "phase_id": "audio_adapter",
            "phase_status": "not_connected",
            "phase_mode": "capture_contract_only",
            "item_count": 3,
            "safe_to_proceed": False,
            "source_status_field": "desktop_bridge_safe_to_capture_audio",
            "source_status_value": False,
        },
        {
            "phase_id": "asr_worker_lifecycle",
            "phase_status": "specified_not_started",
            "phase_mode": "sidecar_contract_only",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "bridge_process_lifecycle_status",
            "source_status_value": "specified_not_started",
        },
        {
            "phase_id": "resource_policy",
            "phase_status": "specified_not_enforced",
            "phase_mode": "limits_contract_only",
            "item_count": 8,
            "safe_to_proceed": False,
            "source_status_field": "bridge_resource_policy_status",
            "source_status_value": "specified_not_enforced",
        },
        {
            "phase_id": "error_audit_contract",
            "phase_status": "specified",
            "phase_mode": "response_only",
            "item_count": 2,
            "safe_to_proceed": False,
            "source_status_field": "bridge_audit_contract_status",
            "source_status_value": "response_only",
        },
    ]
    return {
        "desktop_bridge_contract_mode": "contract_preflight_only",
        "desktop_bridge_contract_status": "specified_not_bound",
        "native_bridge_status": "not_created",
        "desktop_shell_runtime_status": "not_created",
        "bridge_transport_status": "not_created",
        "bridge_command_contract_status": "specified_not_bound",
        "bridge_process_lifecycle_status": "specified_not_started",
        "bridge_resource_policy_status": "specified_not_enforced",
        "bridge_error_contract_status": "specified",
        "bridge_audit_contract_status": "response_only",
        "bridge_platform_adapter_status": "not_created",
        "desktop_bridge_command_count": len(commands),
        "desktop_bridge_commands": commands,
        "desktop_bridge_phase_count": len(phases),
        "desktop_bridge_phases": phases,
        "desktop_bridge_error_contract": {
            "error_code": "required_stable_string",
            "error_kind": "permission|device|worker|transport|storage|remote_provider",
            "user_recoverable": "required_boolean",
            "safe_message_policy": "user_visible_no_stack_no_secret",
            "secret_redaction_policy": "no_secret_values",
            "retry_policy": "explicit_user_action_required",
        },
        "desktop_bridge_resource_policy": {
            "worker_spawn_status": "not_started",
            "worker_memory_limit_mb": 2048,
            "worker_health_check_status": "planned_not_started",
            "audio_buffer_retention": "ephemeral_by_default",
            "audio_chunk_persistence_status": "not_created",
            "event_queue_backpressure_status": "specified_not_enforced",
            "payload_size_limit_kb": 256,
            "heartbeat_interval_ms": 5000,
            "log_line_limit": 200,
            "crash_restart_policy": "planned_manual_restart_first",
        },
        "desktop_bridge_blockers": [
            "native_bridge_not_created",
            "ipc_transport_not_bound",
            "desktop_shell_runtime_not_created",
            "platform_adapters_not_created",
            "permission_flow_not_designed",
            "audio_capture_not_connected",
            "asr_sidecar_worker_not_started",
            "resource_limits_not_enforced",
        ],
        "desktop_bridge_next_decisions": [
            "create_tauri_shell_scaffold_against_bridge_contract",
            "wire_noop_ipc_for_runtime_status_session_prepare_and_worker_health",
            "define_macos_permission_copy_and_manual_preflight",
            "define_audio_device_adapter_without_starting_capture",
            "define_asr_sidecar_packaging_and_health_probe",
            "define_local_data_lifecycle_before_audio_chunk_persistence",
        ],
        "desktop_bridge_safe_to_create_native_bridge": False,
        "desktop_bridge_safe_to_bind_ipc": False,
        "desktop_bridge_safe_to_invoke_commands": False,
        "desktop_bridge_safe_to_request_permissions": False,
        "desktop_bridge_safe_to_enumerate_devices": False,
        "desktop_bridge_safe_to_capture_audio": False,
        "desktop_bridge_safe_to_spawn_worker": False,
        "desktop_bridge_safe_to_write_local_files": False,
        "desktop_bridge_safe_to_call_remote_asr": False,
        "desktop_bridge_safe_to_call_llm": False,
    }


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
    return (
        path_text == LOCAL_ASR_EVENTS_APPROVED_ROOT
        or path_text.startswith(f"{LOCAL_ASR_EVENTS_APPROVED_ROOT}/")
    )


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
    if (
        not _is_under_local_asr_events_root(events_path)
        or not _is_under_local_asr_events_root(resolved)
    ):
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
        "input_event_counts": input_event_counts
        or {event_type: 0 for event_type in LOCAL_ASR_EVENT_TYPES},
        "validation_errors": validation_errors,
        **LOCAL_ASR_FILE_SAFETY_FLAGS,
    }


app = create_app(data_dir=os.environ.get("MEETING_COPILOT_DATA_DIR"))

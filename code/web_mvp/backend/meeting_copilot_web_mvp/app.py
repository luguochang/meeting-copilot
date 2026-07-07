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
        cards, usage, degraded = llm_service.build_approach_cards(transcript_text, config)
        return {
            "session_id": session_id,
            "source": record["source"],
            "trace_kind": record["trace_kind"],
            "approach_cards": cards,
            "count": len(cards),
            "llm_usage": usage,
            "degraded": degraded,
        }

    @app.post("/live/asr/sessions/{session_id}/minutes")
    def create_asr_live_session_minutes(
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
            raise HTTPException(status_code=422, detail=f"unsupported minutes mode: {payload.mode}")
        config = llm_service.LlmConfig.from_env()
        if config is None:
            raise HTTPException(
                status_code=422,
                detail="LLM execution enabled but LLM_GATEWAY_BASE_URL / LLM_GATEWAY_API_KEY not configured in environment",
            )
        transcript_text = " ".join(
            str((e.get("payload") or {}).get("normalized_text") or (e.get("payload") or {}).get("text", ""))
            for e in record.get("events") or []
            if e.get("event_type") == "transcript_final"
        )
        markdown, usage, degraded = llm_service.build_minutes(transcript_text, config)
        return {
            "session_id": session_id,
            "minutes_md": markdown,
            "llm_usage": usage,
            "degraded": degraded,
        }

    @app.delete("/live/asr/sessions/{session_id}")
    def delete_asr_live_session(session_id: str) -> dict[str, Any]:
        try:
            removed = asr_live_repo.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail=f"ASR live session not found: {session_id}")
        _log.info("asr.session.deleted", session_id=session_id)
        return {"session_id": session_id, "deleted": True, "cascade": "transcript, events, candidates, cards"}

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

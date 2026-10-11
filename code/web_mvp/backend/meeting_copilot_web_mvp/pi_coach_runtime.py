"""Bounded JSONL bridge to the optional Pi realtime-coach runtime."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Mapping, Sequence

from meeting_copilot_web_mvp.coach_skills import coach_skill_payload
from meeting_copilot_web_mvp.coach_contract import (
    COACH_ROLE_CONTRACT,
    COACH_ROLE_CONTRACT_VERSION,
    extract_role_context,
)
from meeting_copilot_web_mvp.participation_contract import (
    PARTICIPATION_PROFILE,
    PARTICIPATION_ROLE_CONTRACT,
    PARTICIPATION_CONTRACT_VERSION,
)


PROTOCOL = "talktrace-pi-coach-jsonl.v1"
PI_RUNTIME_ENV = "MEETING_COPILOT_REALTIME_COACH_RUNTIME"
PI_PREWARM_ENV = "MEETING_COPILOT_PI_BRIDGE_PREWARM"
PI_BRIDGE_ENTRY_ENV = "MEETING_COPILOT_PI_BRIDGE_ENTRY"
PI_NODE_EXECUTABLE_ENV = "MEETING_COPILOT_NODE_EXECUTABLE"
DEFAULT_TIMEOUT_SECONDS = 12.0
REALTIME_PROVIDER_TIMEOUT_SECONDS = 10.0
DEEP_PROVIDER_TIMEOUT_SECONDS = 25.0
BRIDGE_RESPONSE_GRACE_SECONDS = 0.25
MAX_REQUEST_BYTES = 200_000
MAX_RESPONSE_BYTES = 200_000

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_BRIDGE_ENTRY = _REPO_ROOT / "code/agent_runtime/pi_coach_bridge/src/bridge.mjs"


class PiCoachRuntimeError(RuntimeError):
    """The optional Pi process, provider, or protocol could not produce a result."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "pi_runtime_error",
        metrics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.metrics = dict(metrics) if isinstance(metrics, Mapping) else {}


def configured_coach_runtime(value: Any = None) -> str:
    normalized = str(value if value is not None else os.environ.get(PI_RUNTIME_ENV, "pi")).strip().lower()
    return normalized if normalized in {"direct", "pi"} else "direct"


def pi_bridge_prewarm_enabled(value: Any = None) -> bool:
    """Return whether startup may eagerly launch the local Pi bridge.

    Prewarming only starts the Node process and waits for its local ``ready``
    handshake; it never contacts the configured Provider. Keep it opt-in so
    provider-free/demo and unit-test runtimes do not spawn an optional process.
    """

    raw = value if value is not None else os.environ.get(PI_PREWARM_ENV)
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def default_pi_bridge_command() -> list[str] | None:
    configured_entry = str(os.environ.get(PI_BRIDGE_ENTRY_ENV) or "").strip()
    entry = Path(configured_entry) if configured_entry else _DEFAULT_BRIDGE_ENTRY
    if not entry.is_absolute() or not entry.is_file():
        return None
    configured_node = str(os.environ.get(PI_NODE_EXECUTABLE_ENV) or "").strip()
    node = configured_node or shutil.which("node")
    if not node:
        return None
    node_path = Path(node)
    if configured_node and (not node_path.is_absolute() or not node_path.is_file()):
        return None
    return [str(node), str(entry)]


def build_pi_coach_request(
    request: Any,
    *,
    request_id: str,
    base_url: str,
    api_key: str,
    model: str,
    api_style: str,
    timeout_seconds: float,
    candidate_events: Sequence[Mapping[str, Any]] | None = None,
    priority_mode: str = "realtime",
) -> dict[str, Any]:
    """Serialize the already-validated intelligence request for the Pi bridge."""

    from .user_memory import memory_model_projection

    normalized_priority_mode = str(priority_mode or "realtime").strip().lower()
    direct_participation = getattr(request, "request_metadata", {}).get("automatic_direct") is True
    if normalized_priority_mode not in {"realtime", "deep"}:
        raise ValueError("priority_mode must be realtime or deep")

    # Give the agent the deterministic routing result so it can spend its
    # bounded turn on choosing and wording, rather than rediscovering triggers.
    candidate_events = [dict(item) for item in (candidate_events or ())][:8]
    provider_timeout_cap = (
        DEEP_PROVIDER_TIMEOUT_SECONDS
        if normalized_priority_mode == "deep"
        else REALTIME_PROVIDER_TIMEOUT_SECONDS
    )
    candidate_evidence_ids: list[str] = []
    for candidate in candidate_events:
        raw_ids = candidate.get("evidence_segment_ids")
        if not isinstance(raw_ids, Sequence) or isinstance(raw_ids, (str, bytes)):
            continue
        for raw_id in raw_ids:
            paragraph_id = str(raw_id)
            if paragraph_id not in candidate_evidence_ids:
                candidate_evidence_ids.append(paragraph_id)
    paragraphs_by_id = request.paragraphs_by_id
    already_visible_ids = {
        item.id for item in (*request.context_paragraphs[-2:], *request.new_paragraphs)
    }
    candidate_evidence_paragraphs = [
        asdict(paragraph)
        for paragraph_id in candidate_evidence_ids
        if paragraph_id not in already_visible_ids
        if (paragraph := paragraphs_by_id.get(paragraph_id)) is not None
    ][:3]
    role_context = extract_role_context(
        request.meeting_goal,
        participant_role=getattr(request, "participant_role", None),
        focus_points=getattr(request, "focus_points", ()),
    )
    return {
        "request_id": str(request_id),
        "session_id": str(request.meeting_id),
        "provider": {
            "base_url": str(base_url),
            "api_key": str(api_key),
            "model": str(model),
            "api_style": str(api_style),
            "timeout_ms": int(
                max(1.0, min(float(timeout_seconds), provider_timeout_cap)) * 1_000
            ),
            "decision_timeout_ms": int(
                max(1.0, min(float(timeout_seconds), provider_timeout_cap)) * 1_000
            ),
        },
        "context": {
            "coach_profile": getattr(request, "coach_profile", "legacy"),
            "request_metadata": dict(getattr(request, "request_metadata", {})),
            "role_contract": (PARTICIPATION_ROLE_CONTRACT
                              if getattr(request, "coach_profile", "legacy") == PARTICIPATION_PROFILE
                              else COACH_ROLE_CONTRACT),
            "role_contract_version": (PARTICIPATION_CONTRACT_VERSION
                                      if getattr(request, "coach_profile", "legacy") == PARTICIPATION_PROFILE
                                      else COACH_ROLE_CONTRACT_VERSION),
            "state_revision": int(request.state_revision),
            "trigger_type": str(request.trigger_type),
            "work_item_id": request.work_item_id,
            "user_request": request.user_request,
            "new_paragraphs": [asdict(item) for item in request.new_paragraphs],
            # Pi is a decision lane, not the transcript index. Keep its
            # prompt small and let the bounded search tool retrieve older
            # evidence only when the current decision needs it.
            "context_paragraphs": [asdict(item) for item in (request.context_paragraphs if direct_participation
                else request.context_paragraphs[-3 if normalized_priority_mode == "deep" else -2:])],
            # Historical evidence is available through Pi's bounded search
            # tool. Keep the initial prompt small; replaying a long meeting
            # here made later coach turns pay for the same text repeatedly.
            "retrieval_paragraphs": [asdict(item) for item in request.retrieval_paragraphs[-12 if direct_participation else -8:]],
            # Cross-batch clarity candidates may cite one older paragraph.
            # Send only the exact referenced evidence so the fast Pi prompt
            # can inspect it without replaying the whole retrieval window.
            "candidate_evidence_paragraphs": candidate_evidence_paragraphs,
            "semantic_windows": [asdict(item) for item in request.semantic_windows[-2:]],
            "candidate_events": candidate_events,
            "priority_mode": normalized_priority_mode,
            # Candidate evidence is already host-generated and validated. The
            # bridge can reconstruct quote/ids/reason while the model emits
            # only its decision and user-facing sentence.
            "compact_terminal_contract": bool(candidate_events),
            "rolling_state": dict(request.rolling_state),
            "meeting_goal": request.meeting_goal,
            "participant_role": role_context["participant_role"],
            "focus_points": role_context["focus_points"],
            "document_evidence": list(getattr(request, "document_evidence", ())),
            "knowledge_context": dict(getattr(request, "knowledge_context", {})),
            "user_memory": memory_model_projection(getattr(request, "user_memory", {})),
            # The host request already validates glossary size and term length.
            "glossary": list(request.glossary),
            "coach_skill": coach_skill_payload(request.coach_skill_id),
        },
    }


class PiCoachSidecar:
    """Lazy, persistent Pi process with one bounded in-flight JSONL request."""

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        process_factory: Any = subprocess.Popen,
        evidence_registry_factory: Any = None,
    ) -> None:
        self.command = list(command) if command is not None else default_pi_bridge_command()
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        self._process_factory = process_factory
        self._evidence_registry_factory = evidence_registry_factory
        self._process: Any | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._responses: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=8)
        self._ready = threading.Event()
        self._cancel_requested = threading.Event()
        self._lock = threading.Lock()
        self._inflight_condition = threading.Condition()
        self._active_evaluations = 0
        self._capabilities: frozenset[str] = frozenset()
        self.stderr_bytes_drained = 0

    def prewarm(self) -> dict[str, Any]:
        """Start the local bridge without sending a Provider request.

        Startup prewarming removes the Node/SDK readiness cost from the first
        live coach turn. It is deliberately best-effort: a missing Node
        runtime or a failed bridge must remain an ordinary runtime-unavailable
        state so application startup cannot be blocked by an optional coach.
        The return value contains only local phase metadata and a stable error
        code; it never includes provider configuration.
        """

        started = time.perf_counter()
        with self._lock:
            reused = bool(
                self._process is not None
                and self._process.poll() is None
                and self._ready.is_set()
            )
            try:
                self._ensure_started_unlocked()
            except PiCoachRuntimeError as exc:
                return {
                    "ready": False,
                    "bridge_process_reused": reused,
                    "bridge_startup_ms": round(
                        max(0.0, (time.perf_counter() - started) * 1_000),
                        3,
                    ),
                    "error_code": str(exc.code)[:80],
                }
            except Exception as exc:  # pragma: no cover - defensive boundary
                return {
                    "ready": False,
                    "bridge_process_reused": reused,
                    "bridge_startup_ms": round(
                        max(0.0, (time.perf_counter() - started) * 1_000),
                        3,
                    ),
                    "error_code": type(exc).__name__[:80],
                }
            return {
                "ready": bool(self._process is not None and self._ready.is_set()),
                "bridge_process_reused": reused,
                "bridge_startup_ms": round(
                    max(0.0, (time.perf_counter() - started) * 1_000),
                    3,
                ),
            }

    @property
    def available(self) -> bool:
        return bool(self.command)

    def wait_until_idle(self, timeout: float = 0.75) -> bool:
        """Wait for cancellation to release this channel, without starting work."""
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._inflight_condition:
            while self._active_evaluations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._inflight_condition.wait(timeout=remaining)
        return True

    async def evaluate(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(self._evaluate_sync, dict(payload))
        except asyncio.CancelledError:
            # Cancelling ``asyncio.to_thread`` does not stop the worker thread.
            # Signal the synchronous loop so it releases the serialized Pi
            # channel and terminates the obsolete Node/provider request.
            self._cancel_requested.set()
            raise

    def _evaluate_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._inflight_condition:
            self._active_evaluations += 1
        try:
            return self._evaluate_sync_impl(payload)
        finally:
            with self._inflight_condition:
                self._active_evaluations -= 1
                self._inflight_condition.notify_all()

    def _evaluate_sync_impl(self, payload: dict[str, Any]) -> dict[str, Any]:
        reconcile_mode = (payload.get("context") or {}).get("task_mode") == "project_reconcile"
        memory_mode = (payload.get("context") or {}).get("task_mode") == "memory_reconcile"
        preparation_mode = (payload.get("context") or {}).get("task_mode") == "meeting_preparation"
        registry = (
            self._evidence_registry_factory(str(payload.get("session_id") or ""))
            if self._evidence_registry_factory is not None else None
        )
        payload = {**payload, "host_evidence_enabled": registry is not None}
        documents = (payload.get("context") or {}).get("document_evidence") or []
        if documents:
            if registry is None or not hasattr(registry, "register_documents"):
                raise PiCoachRuntimeError("Pi document registry unavailable", code="pi_invalid_request")
            if (payload.get("context") or {}).get("coach_profile") == "participation":
                registry.register_documents(documents, max_sources=12, max_characters=8000)
            else:
                registry.register_documents(documents)
        host_calls: set[str] = set()
        retrieved: dict[str, dict[str, Any]] = {}
        project_changes = None
        memory_update = None
        preparation_brief = None
        project_state = None
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_REQUEST_BYTES:
            raise PiCoachRuntimeError("Pi request exceeds the byte budget", code="pi_request_too_large")
        request_id = str(payload.get("request_id") or "")
        if not request_id:
            raise PiCoachRuntimeError("Pi request_id is required", code="pi_invalid_request")
        queue_started = time.perf_counter()
        self._lock.acquire()
        sidecar_queue_ms = max(0.0, (time.perf_counter() - queue_started) * 1_000)
        try:
            try:
                self._cancel_requested.clear()
                bridge_process_reused = bool(
                    self._process is not None
                    and self._process.poll() is None
                    and self._ready.is_set()
                )
                startup_started = time.perf_counter()
                self._ensure_started_unlocked()
                if ((payload.get("context") or {}).get("coach_profile") == "participation"
                        and "participation.v1" not in self._capabilities):
                    raise PiCoachRuntimeError("Pi bridge does not support participation.v1",
                                              code="pi_unsupported_profile")
                bridge_startup_ms = max(0.0, (time.perf_counter() - startup_started) * 1_000)
                process = self._process
                if process is None or process.stdin is None or process.poll() is not None:
                    raise PiCoachRuntimeError("Pi bridge is not running", code="pi_unavailable")
                try:
                    process.stdin.write(encoded + "\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError, ValueError) as exc:
                    self._stop_unlocked()
                    raise PiCoachRuntimeError("Pi bridge input closed", code="pi_transport_error") from exc
                round_trip_started = time.perf_counter()
                provider = payload.get("provider") if isinstance(payload.get("provider"), Mapping) else {}
                decision_timeout_ms = max(
                    1,
                    int(provider.get("decision_timeout_ms") or provider.get("timeout_ms") or self.timeout_seconds * 1_000),
                )
                deadline = time.monotonic() + min(
                    self.timeout_seconds,
                    decision_timeout_ms / 1_000 + BRIDGE_RESPONSE_GRACE_SECONDS,
                )

                def failure_metrics() -> dict[str, Any]:
                    """Keep local phase clocks when a sidecar response fails."""

                    return {
                        "sidecar_queue_ms": round(sidecar_queue_ms, 3),
                        "bridge_startup_ms": round(bridge_startup_ms, 3),
                        "bridge_round_trip_ms": round(
                            max(0.0, (time.perf_counter() - round_trip_started) * 1_000),
                            3,
                        ),
                        "bridge_process_reused": bridge_process_reused,
                    }

                while True:
                    if self._cancel_requested.is_set():
                        self._stop_unlocked()
                        raise PiCoachRuntimeError(
                            "Pi bridge request was superseded",
                            code="pi_cancelled",
                        )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._stop_unlocked()
                        raise PiCoachRuntimeError(
                            "Pi bridge response timed out",
                            code="pi_timeout",
                            metrics=failure_metrics(),
                        )
                    try:
                        response = dict(self._responses.get(timeout=min(remaining, 0.1)))
                    except queue.Empty:
                        continue
                    bridge_error = response.get("_bridge_error")
                    if bridge_error:
                        self._stop_unlocked()
                        raise PiCoachRuntimeError(
                            str(bridge_error),
                            code="pi_transport_error",
                            metrics=failure_metrics(),
                        )
                    if str(response.get("request_id") or "") != request_id:
                        self._stop_unlocked()
                        raise PiCoachRuntimeError(
                            "Pi response request_id mismatch",
                            code="pi_protocol_error",
                            metrics=failure_metrics(),
                        )
                    if response.get("event") == "host_tool_request":
                        call_id = response.get("call_id")
                        tool_name = response.get("tool")
                        if (registry is None or call_id != f"evidence-{len(host_calls) + 1}"
                                or call_id in host_calls or len(host_calls) >= (5 if preparation_mode else 3 if memory_mode else 6 if reconcile_mode else 4)
                                or tool_name not in ({"search_project_documents", "read_project_state", "submit_preparation_brief"} if preparation_mode else {"propose_memory_update"} if memory_mode else {"search_project_documents", "read_project_state", "propose_project_changes"} if reconcile_mode else
                                                     {"search_prior_evidence", "read_transcript_span", "search_project_documents", "read_project_state"})):
                            self._stop_unlocked()
                            raise PiCoachRuntimeError("Invalid host tool request", code="pi_protocol_error")
                        host_calls.add(call_id)
                        reply = {"protocol": PROTOCOL, "event": "host_tool_response",
                                 "request_id": request_id, "call_id": call_id, "ok": False}
                        try:
                            arguments = response.get("arguments")
                            if not isinstance(arguments, Mapping):
                                raise ValueError("invalid tool arguments")
                            if tool_name == "submit_preparation_brief":
                                if preparation_brief is not None or not hasattr(registry, "validate_preparation_brief"):
                                    raise ValueError("preparation terminal already captured or unavailable")
                                preparation_brief = registry.validate_preparation_brief(arguments)
                                rows = preparation_brief
                            elif tool_name == "propose_memory_update":
                                if memory_update is not None or not hasattr(registry, "validate_memory_update"):
                                    raise ValueError("memory terminal already captured or unavailable")
                                memory_update = registry.validate_memory_update(arguments)
                                rows = memory_update
                            elif tool_name == "propose_project_changes":
                                if project_changes is not None or not hasattr(registry, "validate_project_changes"):
                                    raise ValueError("project terminal already captured or unavailable")
                                project_changes = registry.validate_project_changes(arguments)
                                rows = project_changes
                            elif tool_name == "search_prior_evidence":
                                if set(arguments) - {"query", "max_results", "include_neighbors"}:
                                    raise ValueError("invalid search arguments")
                                search_kwargs = {"limit": arguments.get("max_results", 4)}
                                # Keep compatibility with injected registries while only opting into
                                # the expanded, explicitly requested neighborhood contract.
                                if arguments.get("include_neighbors") is True:
                                    search_kwargs["include_neighbors"] = True
                                rows = registry.search(arguments.get("query"), **search_kwargs)
                            elif tool_name == "read_transcript_span":
                                if set(arguments) - {"segment_id", "before", "after"}:
                                    raise ValueError("invalid span arguments")
                                rows = registry.read_span(
                                    arguments.get("segment_id"),
                                    before=arguments.get("before", 1),
                                    after=arguments.get("after", 1),
                                )
                            elif tool_name == "search_project_documents":
                                if set(arguments) - {"query", "max_results"}:
                                    raise ValueError("invalid document search arguments")
                                rows = registry.search_documents(arguments.get("query"), limit=arguments.get("max_results", 6))
                            else:
                                if arguments:
                                    raise ValueError("project state tool accepts no model-supplied scope")
                                rows = registry.read_project_state()
                                if not reconcile_mode and not preparation_mode and isinstance(rows, list) and len(rows) == 1:
                                    project_state = dict(rows[0])
                            paragraphs = rows if tool_name in {"search_project_documents", "read_project_state", "propose_project_changes", "propose_memory_update", "submit_preparation_brief"} else [self._host_evidence_paragraph(row) for row in rows]
                            reply.update(ok=True, results=paragraphs)
                            reply_text = json.dumps(reply, ensure_ascii=False, separators=(",", ":"))
                            if len(reply_text.encode("utf-8")) > 48_000:
                                raise ValueError("host evidence exceeds byte budget")
                            if tool_name not in {"search_project_documents", "read_project_state", "propose_project_changes", "propose_memory_update", "submit_preparation_brief"}:
                                retrieved.update({row["segment_id"]: row for row in rows})
                        except Exception:
                            # Database/error text must never enter model context.
                            reply = {"protocol": PROTOCOL, "event": "host_tool_response",
                                     "request_id": request_id, "call_id": call_id, "ok": False}
                        if self._cancel_requested.is_set() or time.monotonic() >= deadline:
                            self._stop_unlocked()
                            raise PiCoachRuntimeError("Host evidence request expired", code="pi_timeout",
                                                      metrics=failure_metrics())
                        process.stdin.write(json.dumps(reply, ensure_ascii=False, separators=(",", ":")) + "\n")
                        process.stdin.flush()
                        continue
                    validation_started = time.perf_counter()
                    try:
                        validated = self._validate_response(response, task_mode="meeting_preparation" if preparation_mode else "memory_reconcile" if memory_mode else "project_reconcile" if reconcile_mode else "realtime")
                        # Never accept extra evidence supplied by the bridge itself.
                        validated.pop("host_evidence", None)
                        validated.pop("host_source_evidence", None)
                        validated.pop("host_project_changes", None)
                        validated.pop("host_memory_update", None)
                        validated.pop("host_preparation_brief", None)
                        validated.pop("host_project_state", None)
                        if not reconcile_mode and not preparation_mode and project_state is not None:
                            validated["host_project_state"] = registry.validate_project_state(project_state)
                        if reconcile_mode:
                            if project_changes is None or validated.get("project_changes") != project_changes:
                                raise PiCoachRuntimeError("Project result was not captured by the host", code="pi_protocol_error")
                            registry.validate_project_changes(project_changes)
                            validated["host_project_changes"] = project_changes
                        if memory_mode:
                            if memory_update is None or validated.get("memory_update") != memory_update:
                                raise PiCoachRuntimeError("Memory result was not captured by the host", code="pi_protocol_error")
                            registry.validate_memory_update(memory_update)
                            validated["host_memory_update"] = memory_update
                        if preparation_mode:
                            if preparation_brief is None or validated.get("preparation_brief") != preparation_brief:
                                raise PiCoachRuntimeError("Preparation was not captured by the host", code="pi_protocol_error")
                            registry.validate_preparation_brief(preparation_brief)
                            validated["host_preparation_brief"] = preparation_brief
                        intervention = validated.get("intervention")
                        if registry is not None and isinstance(intervention, Mapping):
                            refs = intervention.get("document_evidence_refs") or []
                            if refs:
                                validated["host_source_evidence"] = registry.validate_documents(refs)
                                intervention = {**intervention, "source_evidence": validated["host_source_evidence"]}
                                validated["intervention"] = intervention
                            ids = intervention.get("evidence_segment_ids") or []
                            quotes = {key: retrieved[key]["normalized_text"] for key in ids if key in retrieved}
                            if quotes:
                                try:
                                    validated["host_evidence"] = registry.validate(quotes)
                                except Exception as exc:
                                    raise PiCoachRuntimeError("Host evidence no longer valid",
                                                              code="pi_evidence_superseded") from exc
                        if registry is not None and (
                            self._cancel_requested.is_set() or time.monotonic() >= deadline
                        ):
                            self._stop_unlocked()
                            raise PiCoachRuntimeError("Host evidence validation expired", code="pi_timeout")
                    except PiCoachRuntimeError as exc:
                        # Bridge failures still consumed host queue/startup time.
                        # Host clocks override any same-named remote metrics.
                        exc.metrics.update(failure_metrics())
                        exc.metrics["response_validation_ms"] = round(
                            max(0.0, (time.perf_counter() - validation_started) * 1_000),
                            3,
                        )
                        raise
                    response_validation_ms = max(
                        0.0, (time.perf_counter() - validation_started) * 1_000
                    )
                    metrics = (
                        dict(validated.get("metrics"))
                        if isinstance(validated.get("metrics"), Mapping)
                        else {}
                    )
                    metrics.update(
                        {
                            "sidecar_queue_ms": round(sidecar_queue_ms, 3),
                            "bridge_startup_ms": round(bridge_startup_ms, 3),
                            "bridge_round_trip_ms": round(
                                max(0.0, (time.perf_counter() - round_trip_started) * 1_000),
                                3,
                            ),
                            "bridge_process_reused": bridge_process_reused,
                            "response_validation_ms": round(response_validation_ms, 3),
                        }
                    )
                    validated["metrics"] = metrics
                    return validated
            finally:
                self._cancel_requested.clear()
        finally:
            self._lock.release()

    @staticmethod
    def _host_evidence_paragraph(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": row["segment_id"], "text": row["normalized_text"],
            "revision": row["revision"], "source_track": row["source_track"],
            "correction_status": row["correction_status"], "role_hint": "unknown",
            "evidence_quality": "reviewed" if row["correction_status"] in {"changed", "no_change"} else "provisional",
        }

    def _ensure_started_unlocked(self) -> None:
        if self._process is not None and self._process.poll() is None and self._ready.is_set():
            return
        self._stop_unlocked()
        if not self.command:
            raise PiCoachRuntimeError(
                "Pi bridge or Node.js is not installed",
                code="pi_unavailable",
            )
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._process = self._process_factory(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            self._process = None
            raise PiCoachRuntimeError("Pi bridge failed to start", code="pi_spawn_failed") from exc
        process = self._process
        if process is None:
            raise PiCoachRuntimeError("Pi bridge is not running", code="pi_unavailable")
        # Bind reader state to this exact process generation. A timed-out
        # process can finish draining its pipes after the next request has
        # already spawned a replacement; sharing ``self._responses`` would
        # let that stale reader inject an old bridge error into the new turn.
        response_queue: queue.Queue[Mapping[str, Any]] = queue.Queue(maxsize=8)
        ready_event = threading.Event()
        self._responses = response_queue
        self._ready = ready_event
        self._reader = threading.Thread(
            target=self._read_stdout,
            args=(process, response_queue, ready_event),
            name="pi-coach-stdout",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="pi-coach-stderr",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()
        if not ready_event.wait(timeout=min(8.0, self.timeout_seconds)):
            self._stop_unlocked()
            raise PiCoachRuntimeError("Pi bridge readiness timed out", code="pi_startup_timeout")

    def _read_stdout(
        self,
        process: Any | None = None,
        response_queue: queue.Queue[Mapping[str, Any]] | None = None,
        ready_event: threading.Event | None = None,
    ) -> None:
        process = process if process is not None else self._process
        if process is None or process.stdout is None:
            return
        response_queue = response_queue if response_queue is not None else self._responses
        ready_event = ready_event if ready_event is not None else self._ready
        try:
            try:
                for line in process.stdout:
                    if len(line.encode("utf-8", errors="replace")) > MAX_RESPONSE_BYTES:
                        self._put_response(
                            {"_bridge_error": "Pi bridge response exceeded the byte budget"},
                            response_queue,
                        )
                        return
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        self._put_response(
                            {"_bridge_error": "Pi bridge returned invalid JSON"},
                            response_queue,
                        )
                        return
                    if not isinstance(payload, Mapping) or payload.get("protocol") != PROTOCOL:
                        self._put_response(
                            {"_bridge_error": "Pi bridge protocol mismatch"},
                            response_queue,
                        )
                        return
                    if payload.get("event") == "ready":
                        if self._process is process:
                            self._capabilities = frozenset(
                                str(item) for item in payload.get("capabilities", [])
                                if isinstance(item, str)
                            )
                        ready_event.set()
                        continue
                    self._put_response(payload, response_queue)
            except (OSError, ValueError):
                # Cancellation deliberately closes the pipe to stop an
                # obsolete provider request. Treat that as normal shutdown.
                pass
        finally:
            if not ready_event.is_set():
                ready_event.set()
            self._put_response({"_bridge_error": "Pi bridge output closed"}, response_queue)

    def _read_stderr(self, process: Any | None = None) -> None:
        process = process if process is not None else self._process
        if process is None or process.stderr is None:
            return
        try:
            for chunk in iter(lambda: process.stderr.read(4096), ""):
                self.stderr_bytes_drained += len(chunk.encode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass

    def _put_response(
        self,
        payload: Mapping[str, Any],
        response_queue: queue.Queue[Mapping[str, Any]] | None = None,
    ) -> None:
        target_queue = response_queue if response_queue is not None else self._responses
        try:
            target_queue.put_nowait(payload)
        except queue.Full:
            pass

    @staticmethod
    def _validate_response(response: dict[str, Any], *, task_mode: str = "realtime") -> dict[str, Any]:
        if response.get("protocol") != PROTOCOL:
            raise PiCoachRuntimeError("Pi response protocol mismatch", code="pi_protocol_error")
        if response.get("ok") is not True:
            error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
            code = str(error.get("code") or "pi_agent_error")[:80]
            message = str(error.get("message") or "Pi agent failed")[:500]
            metrics = error.get("metrics") if isinstance(error.get("metrics"), Mapping) else None
            raise PiCoachRuntimeError(message, code=code, metrics=metrics)
        if task_mode == "meeting_preparation":
            if response.get("action") != "preparation_brief" or not isinstance(response.get("preparation_brief"), Mapping):
                raise PiCoachRuntimeError("Invalid preparation response", code="pi_protocol_error")
            return response
        if task_mode == "project_reconcile":
            if response.get("action") != "project_changes" or not isinstance(response.get("project_changes"), Mapping):
                raise PiCoachRuntimeError("Project changes response is invalid", code="pi_protocol_error")
            return response
        if task_mode == "memory_reconcile":
            if response.get("action") != "memory_update" or not isinstance(response.get("memory_update"), Mapping):
                raise PiCoachRuntimeError("Memory update response is invalid", code="pi_protocol_error")
            return response
        if response.get("action") not in {"intervention", "silent"}:
            raise PiCoachRuntimeError("Pi response action is invalid", code="pi_protocol_error")
        if response.get("action") == "intervention" and not isinstance(response.get("intervention"), Mapping):
            raise PiCoachRuntimeError("Pi intervention is missing", code="pi_protocol_error")
        return response

    def close(self) -> None:
        self._cancel_requested.set()
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        process = self._process
        self._ready.clear()
        self._capabilities = frozenset()
        if process is None:
            return
        # TextIOWrapper.close() can wait for a reader's buffered-pipe lock.
        # Stop the producer first so blocking stdout/stderr reads receive EOF;
        # never place an unbounded pipe close before terminate/kill.
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=2.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            # Retain the process for a later cleanup attempt and do not close
            # its live pipes, which could block the cancelling caller again.
            raise PiCoachRuntimeError("Pi process exit was not acknowledged", code="pi_stop_unconfirmed") from None
        self._process = None
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

from __future__ import annotations

import argparse
import asyncio
import hashlib
import itertools
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "code/web_mvp/backend"
CORE_ROOT = REPO_ROOT / "code/core"
for import_root in (str(BACKEND_ROOT), str(CORE_ROOT), str(REPO_ROOT)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from meeting_copilot_web_mvp.llm_service import LlmConfig, realtime_config  # noqa: E402
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar  # noqa: E402
from meeting_copilot_web_mvp.realtime_intelligence import (  # noqa: E402
    RealtimeIntelligenceRequest,
    build_local_reflex_intervention,
    build_realtime_coach_provenance_decision,
    realtime_coach_candidate_events,
    run_realtime_coach_routed,
    should_run_realtime_coach,
)
from meeting_copilot_web_mvp.streaming_llm_provider import (  # noqa: E402
    OpenAICompatibleStreamingProvider,
)
from tools.realtime_coach_eval.score import score_predictions  # noqa: E402


DEFAULT_ACCEPTANCE_THRESHOLDS = {
    "minimum_precision": 0.70,
    "minimum_recall": 0.80,
    "minimum_silent_accuracy": 0.80,
    "minimum_evidence_accuracy": 1.0,
    "minimum_production_trigger_recall": 0.80,
    "maximum_latency_p50_ms": 2_500.0,
    "maximum_latency_p95_ms": 5_000.0,
    "maximum_latency_ms": 10_000.0,
    "maximum_reliability_errors": 0,
    "maximum_fallbacks": 0,
}

REPLAY_SAFE_AGENT_METRIC_KEYS = (
    "elapsed_ms",
    "decision_latency_budget_ms",
    "decision_timeout_ms",
    "within_latency_budget",
    "prompt_profile",
    "prompt_characters",
    "system_prompt_characters",
    "tool_schema_characters",
    "available_tool_names",
    "compact_terminal_tools",
    "ttft_ms",
    "decision_latency_ms",
    "sidecar_queue_ms",
    "bridge_startup_ms",
    "bridge_round_trip_ms",
    "provider_connect_ms",
    "bridge_process_reused",
    "response_validation_ms",
    "context_reads",
    "tool_errors",
    "session_message_count_before",
    "session_reused",
    "history_searches",
    "history_results",
)

ORDERED_LIFECYCLE_FIELDS = ("sequence_id", "turn_index", "sequence_stage")
ORDERED_LIFECYCLE_STAGES = ("open", "intervention", "resolving_evidence")
SHARED_REPLAY_DEADLINE_POLICY = "shared_total_timeout.v1"
PAIRED_RUNTIME_ORDER_POLICY = "case_adjacent_alternating_first_arm.v1"
THREE_ARM_RUNTIME_ORDER_POLICY = "case_adjacent_balanced_six_permutations.v1"
REPLAY_REQUEST_SCHEMA_VERSION = "talktrace.realtime_coach_eval_request.v1"
REPLAY_OUTPUT_SCHEMA_VERSION = "talktrace.realtime_coach_display_decision.v1"
REPLAY_PROVIDER_GENERATION_CONTRACT = {
    "temperature": 0.1,
    "output_mode": "bounded_json_decision",
}
LOCAL_BASELINE_MODEL = "deterministic_local_reflex.v1"
LOCAL_BASELINE_API_STYLE = "local"


def classify_replay_failure(
    result: Mapping[str, Any] | None = None,
    *,
    status: str | None = None,
    error: BaseException | None = None,
) -> str | None:
    """Return a stable failure class without treating safe silence as failure.

    Replay output may contain a successful-looking ``protected_silent`` status
    together with a Provider/runtime failure code.  Keep that failure visible
    to scoring while leaving normal ``protected_silent`` and ``not_triggered``
    decisions clean.
    """

    value = result if isinstance(result, Mapping) else {}
    normalized_status = str(status or value.get("status") or "").strip().lower()
    # A production gate that deliberately did not start an evaluation is a
    # valid outcome, not a runtime fallback. Likewise, the explicitly
    # requested local baseline may safely return a protected silence when its
    # narrow reflex has no matching intervention. Keep both out of the
    # reliability-error denominator; actual Provider/sidecar failures still
    # carry a fallback code and remain classified below.
    requested_runtime = str(value.get("runtime_requested") or "").strip().lower()
    attempted = value.get("decision_attempted", True)
    code = str(
        value.get("fallback_error_code")
        or value.get("fallback_reason")
        or value.get("status_reason")
        or ""
    ).strip().lower()
    if (
        normalized_status == "not_triggered"
        and not value.get("fallback_error_code")
        and (attempted is False or code in {"trigger_gate", "coach_disabled", "candidate_cooldown"})
    ):
        return None
    if normalized_status == "protected_silent" and code == "local_reflex_no_intervention":
        return None
    if (
        requested_runtime == "local"
        and normalized_status == "protected_silent"
        and not value.get("fallback_error_code")
        and not error
    ):
        return None
    if error is not None:
        return "timeout" if isinstance(error, TimeoutError) else "execution_error"
    timeout_codes = {
        "provider_timeout",
        "soft_deadline_exceeded",
        "agent_deadline_exceeded",
        "deadline_budget_exhausted",
        "evaluation_deadline_exceeded",
    }
    if normalized_status == "timed_out" or code in timeout_codes:
        return "timeout"
    if "429" in code or "rate_limit" in code or "rate_limited" in code:
        return "rate_limit"
    if "5xx" in code or "provider_http_5" in code or code.startswith("provider_5"):
        return "provider_5xx"
    if "transport" in code or "connection" in code:
        return "transport_error"
    if "validation" in code or "invalid_response" in code:
        return "validation_error"
    if normalized_status in {"failed", "error"}:
        return "execution_error"
    if code:
        return "runtime_fallback"
    return None


def bounded_agent_metrics(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Keep replay diagnostics useful without copying arbitrary runtime data."""

    return {
        key: metrics.get(key)
        for key in REPLAY_SAFE_AGENT_METRIC_KEYS
        if key in metrics
    }


def acceptance_from_score(score: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the checked-in Stage 0 gate without hiding unavailable metrics."""

    checks = {
        "precision": (
            score.get("precision") is not None
            and score["precision"] >= DEFAULT_ACCEPTANCE_THRESHOLDS["minimum_precision"]
        ),
        "recall": (
            score.get("recall") is not None
            and score["recall"] >= DEFAULT_ACCEPTANCE_THRESHOLDS["minimum_recall"]
        ),
        "silent_accuracy": (
            score.get("silent_accuracy") is not None
            and score["silent_accuracy"] >= DEFAULT_ACCEPTANCE_THRESHOLDS["minimum_silent_accuracy"]
        ),
        "required_evidence_accuracy": (
            score.get("required_evidence_accuracy") is not None
            and score["required_evidence_accuracy"]
            >= DEFAULT_ACCEPTANCE_THRESHOLDS["minimum_evidence_accuracy"]
        ),
        "production_trigger_recall": (
            score.get("production_trigger_recall") is not None
            and score["production_trigger_recall"]
            >= DEFAULT_ACCEPTANCE_THRESHOLDS["minimum_production_trigger_recall"]
        ),
        "latency_p50_ms": (
            score.get("latency_p50_ms") is not None
            and score["latency_p50_ms"] <= DEFAULT_ACCEPTANCE_THRESHOLDS["maximum_latency_p50_ms"]
        ),
        "latency_p95_ms": (
            score.get("latency_p95_ms") is not None
            and score["latency_p95_ms"] <= DEFAULT_ACCEPTANCE_THRESHOLDS["maximum_latency_p95_ms"]
        ),
        "latency_max_ms": (
            score.get("latency_max_ms") is not None
            and score["latency_max_ms"] <= DEFAULT_ACCEPTANCE_THRESHOLDS["maximum_latency_ms"]
        ),
        "reliability_error_count": (
            score.get("reliability_error_count") is not None
            and int(score["reliability_error_count"])
            <= DEFAULT_ACCEPTANCE_THRESHOLDS["maximum_reliability_errors"]
        ),
        "fallback_count": (
            score.get("fallback_count") is not None
            and int(score["fallback_count"])
            <= DEFAULT_ACCEPTANCE_THRESHOLDS["maximum_fallbacks"]
        ),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "passed": not failed_checks,
        "checks": checks,
        "failed_checks": failed_checks,
        "thresholds": dict(DEFAULT_ACCEPTANCE_THRESHOLDS),
    }


def load_dataset(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, Mapping):
            raise ValueError(f"dataset line {line_number} must be an object")
        case = dict(value)
        if not str(case.get("case_id") or "").strip():
            raise ValueError(f"dataset line {line_number} is missing case_id")
        if not isinstance(case.get("expected"), Mapping):
            raise ValueError(f"dataset line {line_number} is missing expected")
        cases.append(case)
    if not cases:
        raise ValueError("dataset must contain at least one case")
    validate_ordered_lifecycle_cases(cases)
    return cases


def _paragraphs_by_id(case: Mapping[str, Any], field: str) -> dict[str, Mapping[str, Any]]:
    values = case.get(field)
    if not isinstance(values, list):
        return {}
    return {
        str(value.get("id") or ""): value
        for value in values
        if isinstance(value, Mapping) and str(value.get("id") or "")
    }


def validate_ordered_lifecycle_cases(cases: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed for explicitly declared ordered lifecycle sequences."""

    declared: list[tuple[int, Mapping[str, Any]]] = []
    for position, case in enumerate(cases):
        present = [field for field in ORDERED_LIFECYCLE_FIELDS if field in case]
        if not present:
            continue
        missing = [field for field in ORDERED_LIFECYCLE_FIELDS if field not in case]
        if missing:
            raise ValueError(
                f"case {case.get('case_id')!r} has a partial ordered lifecycle declaration: "
                f"missing {', '.join(missing)}"
            )
        sequence_id = str(case.get("sequence_id") or "").strip()
        turn_index = case.get("turn_index")
        if not sequence_id:
            raise ValueError(f"case {case.get('case_id')!r} must have a non-empty sequence_id")
        if not isinstance(turn_index, int) or isinstance(turn_index, bool) or turn_index < 1:
            raise ValueError(f"case {case.get('case_id')!r} must have a positive integer turn_index")
        declared.append((position, case))

    grouped: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for position, case in declared:
        grouped.setdefault(str(case["sequence_id"]), []).append((position, case))

    session_owners: dict[str, str] = {}
    for sequence_id, positioned_cases in grouped.items():
        positions = [position for position, _case in positioned_cases]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise ValueError(f"ordered lifecycle sequence {sequence_id!r} must not be interleaved")
        sequence_cases = [case for _position, case in positioned_cases]
        case_ids = [str(case.get("case_id") or "").strip() for case in sequence_cases]
        if not all(case_ids) or len(case_ids) != len(set(case_ids)):
            raise ValueError(f"ordered lifecycle sequence {sequence_id!r} must have unique case_id values")
        turn_indexes = [case.get("turn_index") for case in sequence_cases]
        if turn_indexes != list(range(1, len(ORDERED_LIFECYCLE_STAGES) + 1)):
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} turn_index must be exactly [1, 2, 3]"
            )
        stages = [str(case.get("sequence_stage") or "") for case in sequence_cases]
        if stages != list(ORDERED_LIFECYCLE_STAGES):
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} stages must be "
                "open, intervention, resolving_evidence"
            )
        session_ids = [str(case.get("session_id") or "").strip() for case in sequence_cases]
        if not session_ids[0] or len(set(session_ids)) != 1:
            raise ValueError(f"ordered lifecycle sequence {sequence_id!r} must use one session_id")
        previous_owner = session_owners.setdefault(session_ids[0], sequence_id)
        if previous_owner != sequence_id:
            raise ValueError(
                f"ordered lifecycle session {session_ids[0]!r} belongs to multiple sequences"
            )
        revisions = [case.get("state_revision") for case in sequence_cases]
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in revisions
        ) or any(current <= previous for previous, current in zip(revisions, revisions[1:])):
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} state_revision must strictly increase"
            )

        expected_values = [
            case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
            for case in sequence_cases
        ]
        if [expected.get("action") for expected in expected_values] != [
            "silent",
            "intervention",
            "silent",
        ]:
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} expected actions must be "
                "silent, intervention, silent"
            )
        if expected_values[1].get("lifecycle_action") != "retain":
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} intervention must retain"
            )
        if expected_values[2].get("lifecycle_action") not in {"retract", "deprioritize"}:
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} resolving evidence must retract "
                "or deprioritize"
            )
        if expected_values[2].get("supersedes_case_id") != case_ids[1]:
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} resolving evidence must supersede "
                "the intervention case"
            )

        prior_new: dict[str, Mapping[str, Any]] = {}
        prior_texts: set[str] = set()
        for turn_offset, case in enumerate(sequence_cases):
            new_paragraphs = _paragraphs_by_id(case, "new_paragraphs")
            if not new_paragraphs:
                raise ValueError(
                    f"ordered lifecycle sequence {sequence_id!r} turn {turn_offset + 1} "
                    "must contain fresh new_paragraphs"
                )
            new_texts = {
                str(paragraph.get("text") or "").strip()
                for paragraph in new_paragraphs.values()
            }
            if set(new_paragraphs) & set(prior_new) or not all(new_texts) or new_texts & prior_texts:
                raise ValueError(
                    f"ordered lifecycle sequence {sequence_id!r} turn {turn_offset + 1} "
                    "must contain fresh evidence"
                )
            if turn_offset:
                context = _paragraphs_by_id(case, "context_paragraphs")
                referenced_ids = set(context) & set(prior_new)
                if not referenced_ids or any(
                    str(context[paragraph_id].get("text") or "").strip()
                    != str(prior_new[paragraph_id].get("text") or "").strip()
                    for paragraph_id in referenced_ids
                ):
                    raise ValueError(
                        f"ordered lifecycle sequence {sequence_id!r} turn {turn_offset + 1} "
                        "must reference prior evidence"
                    )
            prior_new.update(new_paragraphs)
            prior_texts.update(new_texts)

        resolving_context = _paragraphs_by_id(sequence_cases[2], "context_paragraphs")
        intervention_new = _paragraphs_by_id(sequence_cases[1], "new_paragraphs")
        if not set(resolving_context) & set(intervention_new):
            raise ValueError(
                f"ordered lifecycle sequence {sequence_id!r} resolving evidence must reference "
                "the intervention turn"
            )


def ordered_lifecycle_summary(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    validate_ordered_lifecycle_cases(cases)
    encoded = json.dumps(
        [dict(case) for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    sequence_ids = {
        str(case["sequence_id"])
        for case in cases
        if all(field in case for field in ORDERED_LIFECYCLE_FIELDS)
    }
    turn_count = sum(
        all(field in case for field in ORDERED_LIFECYCLE_FIELDS) for case in cases
    )
    return {
        "case_count": len(cases),
        "single_turn_case_count": len(cases) - turn_count,
        "ordered_lifecycle_sequence_count": len(sequence_ids),
        "ordered_lifecycle_turn_count": turn_count,
        "dataset_contract_fingerprint": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "provider_canary_run_count": 0,
        "provider_canary_evidence": False,
    }


def case_input_fingerprint(case: Mapping[str, Any]) -> str:
    """Hash the exact realtime stimulus and deadline used for a replay case."""

    expected = case.get("expected") if isinstance(case.get("expected"), Mapping) else {}
    deadline = expected.get("deadline_ms")
    if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= 0:
        raise ValueError(f"case {case.get('case_id')!r} must have a positive expected.deadline_ms")
    stimulus = {
        "state_revision": case.get("state_revision") or 1,
        "trigger_type": case.get("trigger_type") or "delta",
        "work_item_id": case.get("work_item_id"),
        "user_request": case.get("user_request"),
        "new_paragraphs": case.get("new_paragraphs") or [],
        "context_paragraphs": case.get("context_paragraphs") or [],
        "semantic_windows": case.get("semantic_windows") or [],
        "rolling_state": case.get("rolling_state") or {},
        "glossary": case.get("glossary") or [],
        "meeting_goal": case.get("meeting_goal"),
        "coach_skill_id": case.get("coach_skill_id") or "general",
        "deadline_ms": deadline,
    }
    encoded = json.dumps(stimulus, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def provider_identity_fingerprint(config: LlmConfig) -> str:
    """Identify the comparable Provider route without exposing its credential."""

    identity = {
        "base_url": str(config.base_url).rstrip("/"),
        "model": str(config.model),
        "api_style": str(config.api_style),
    }
    encoded = json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_replay_config() -> LlmConfig:
    """Use the same low-latency model selection as the production realtime lane."""

    config = LlmConfig.from_env()
    if config is None:
        raise RuntimeError(
            "LLM provider is not configured; set LLM_GATEWAY_BASE_URL, "
            "LLM_GATEWAY_API_KEY, and LLM_GATEWAY_MODEL"
        )
    return realtime_config(config)


def request_from_case(case: Mapping[str, Any]) -> RealtimeIntelligenceRequest:
    return RealtimeIntelligenceRequest.from_payload(
        meeting_id=str(case.get("session_id") or case["case_id"]),
        state_revision=case.get("state_revision") or 1,
        trigger_type=case.get("trigger_type") or "delta",
        work_item_id=case.get("work_item_id"),
        user_request=case.get("user_request"),
        new_paragraphs=case.get("new_paragraphs") or [],
        context_paragraphs=case.get("context_paragraphs") or [],
        retrieval_paragraphs=case.get("retrieval_paragraphs") or [],
        semantic_windows=case.get("semantic_windows") or [],
        rolling_state=case.get("rolling_state") or {},
        glossary=case.get("glossary") or [],
        meeting_goal=case.get("meeting_goal"),
        coach_skill_id=case.get("coach_skill_id") or "general",
        allow_paragraph_revisions=False,
    )


def prediction_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    intervention = result.get("intervention")
    status = str(result.get("status") or ("intervention" if intervention is not None else "protected_silent"))
    if intervention is None:
        prediction = {
            "action": "silent",
            "status": status,
            "status_reason": str(result.get("status_reason") or "").strip() or None,
            "decision_reason": str(result.get("decision_reason") or "").strip() or None,
        }
    else:
        prediction = {
            "action": "intervention",
            "status": status,
            "status_reason": str(result.get("status_reason") or "").strip() or None,
            "event_type": intervention.event_type,
            "title": intervention.title,
            "recommendation": intervention.recommendation,
            "say_this": intervention.recommendation,
            "why_now": intervention.reason,
            "confidence": intervention.confidence,
            "evidence_segment_ids": list(intervention.evidence_segment_ids),
            "evidence_quote": intervention.evidence_quote,
            "decision_reason": str(result.get("decision_reason") or "").strip() or None,
        }
    for field in (
        "decision_id",
        "lifecycle_action",
        "supersedes_decision_id",
        "supersedes_case_id",
    ):
        if field in result:
            prediction[field] = result.get(field)
    return prediction


async def replay_mode(
    cases: list[dict[str, Any]],
    *,
    runtime_name: str,
    config: LlmConfig | None,
    client: httpx.AsyncClient,
    pi_runtime: PiCoachSidecar,
    decision_case_ids: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if runtime_name not in {"local", "direct", "pi"}:
        raise ValueError(f"unsupported replay runtime: {runtime_name}")
    if runtime_name != "local" and config is None:
        raise RuntimeError(f"{runtime_name} replay requires Provider configuration")
    provider = (
        OpenAICompatibleStreamingProvider(
            base_url=config.base_url,
            api_key=config.api_key,
            model=config.model,
            client=client,
            timeout_seconds=min(config.timeout_seconds, 30.0),
            api_style=config.api_style,
        )
        if config is not None
        else None
    )
    records: list[dict[str, Any]] = []
    if decision_case_ids is None:
        decision_case_ids = {}
    for case in cases:
        started_at = time.perf_counter()
        request = request_from_case(case)
        production_candidates = realtime_coach_candidate_events(request)
        production_triggered = should_run_realtime_coach(request, requested_runtime="pi")
        decision_attempted = not (runtime_name == "pi" and not production_triggered)
        deadline_ms = float(case["expected"]["deadline_ms"])
        provider_timeout_ms = max(1, int(deadline_ms))
        base_record = {
            "case_id": case["case_id"],
            "session_id": str(case.get("session_id") or case["case_id"]),
            "coach_skill_id": str(case.get("coach_skill_id") or "general"),
            "trigger_type": request.trigger_type,
            "work_item_id": request.work_item_id,
            "user_request": request.user_request,
            "difficulty": list(case.get("difficulty") or []),
            "expected": dict(case["expected"]),
            "runtime_requested": runtime_name,
            "model_requested": (
                LOCAL_BASELINE_MODEL if runtime_name == "local" else config.model
            ),
            "api_style_requested": (
                LOCAL_BASELINE_API_STYLE if runtime_name == "local" else config.api_style
            ),
            "provider_identity_fingerprint": (
                None if runtime_name == "local" else provider_identity_fingerprint(config)
            ),
            "request_schema_version": REPLAY_REQUEST_SCHEMA_VERSION,
            "output_schema_version": REPLAY_OUTPUT_SCHEMA_VERSION,
            "generation_contract": (
                {"mode": "deterministic_local_reflex", "provider_called": False}
                if runtime_name == "local"
                else dict(REPLAY_PROVIDER_GENERATION_CONTRACT)
            ),
            "production_triggered": production_triggered,
            "production_candidate_count": len(production_candidates),
            "production_candidate_event_types": [item.event_type for item in production_candidates],
            "decision_attempted": decision_attempted,
            "deadline_ms": deadline_ms,
            "decision_deadline_policy": SHARED_REPLAY_DEADLINE_POLICY,
            "input_fingerprint": case_input_fingerprint(case),
        }
        for field in ("state_revision", *ORDERED_LIFECYCLE_FIELDS):
            if field in case:
                base_record[field] = case[field]
        try:
            if runtime_name == "local":
                local_result = build_local_reflex_intervention(
                    request,
                    production_candidates,
                    now_ms=0,
                )
                result = local_result or {
                    "runtime_requested": "local_reflex",
                    "runtime_used": "local_reflex",
                    "status": "protected_silent",
                    "status_reason": "local_reflex_no_intervention",
                    "decision_reason": (
                        "The deterministic local reflex did not emit an intervention."
                    ),
                    "intervention": None,
                    "pi_provider_attempted": False,
                    "llm_called": False,
                }
            elif runtime_name == "pi" and not production_triggered:
                result = build_realtime_coach_provenance_decision(
                    request=request,
                    origin="pi",
                    status="not_triggered",
                    status_reason="trigger_gate",
                )
            else:
                result = await asyncio.wait_for(
                    run_realtime_coach_routed(
                        request=request,
                        provider=provider,
                        requested_runtime=runtime_name,
                        pi_runtime=pi_runtime,
                        pi_provider_config={
                            "base_url": config.base_url,
                            "api_key": config.api_key,
                            "model": config.model,
                            "api_style": config.api_style,
                            "timeout_seconds": min(
                                config.timeout_seconds,
                                deadline_ms / 1_000,
                            ),
                        },
                        candidate_events=(
                            [item.to_dict() for item in production_candidates]
                            if runtime_name == "pi"
                            else None
                        ),
                        max_provider_timeout_ms=provider_timeout_ms,
                    ),
                    timeout=deadline_ms / 1_000,
                )
            metrics = result.get("agent_metrics") if isinstance(result.get("agent_metrics"), Mapping) else {}
            prediction = prediction_from_result(result)
            supersedes_decision_id = str(
                prediction.get("supersedes_decision_id") or ""
            ).strip()
            if supersedes_decision_id in decision_case_ids and "supersedes_case_id" not in prediction:
                prediction["supersedes_case_id"] = decision_case_ids[supersedes_decision_id]
            decision_id = str(prediction.get("decision_id") or "").strip()
            if decision_id:
                decision_case_ids[decision_id] = str(case["case_id"])
            records.append(
                {
                    **base_record,
                    "runtime_used": (
                        "local_reflex"
                        if runtime_name == "local"
                        else result.get("runtime_used")
                    ),
                    "fallback_error_code": result.get("fallback_error_code"),
                    "failure_class": classify_replay_failure(result),
                    "status": result.get("status"),
                    "prediction": prediction,
                    "latency_ms": round((time.perf_counter() - started_at) * 1_000, 2),
                    "agent_turns": metrics.get("turns"),
                    "agent_tool_calls": metrics.get("tool_calls"),
                    "agent_tool_names": metrics.get("tool_names"),
                    # Preserve bounded runtime diagnostics so a replay can
                    # distinguish prompt/profile cost from provider latency
                    # without exposing provider credentials or transcript
                    # content beyond the existing prediction fields.
                    "agent_metrics": bounded_agent_metrics(metrics),
                    "usage": result.get("usage"),
                }
            )
        except TimeoutError:
            records.append(
                {
                    **base_record,
                    "runtime_used": runtime_name,
                    "fallback_error_code": None,
                    "failure_class": "timeout",
                    "status": "timed_out",
                    "prediction": {
                        "action": "silent",
                        "status": "timed_out",
                        "status_reason": "evaluation_deadline_exceeded",
                        "decision_reason": "The shared replay decision deadline expired.",
                    },
                    "latency_ms": round((time.perf_counter() - started_at) * 1_000, 2),
                }
            )
        except Exception as exc:
            records.append(
                {
                    **base_record,
                    "runtime_used": None,
                    "status": "error",
                    "failure_class": classify_replay_failure(error=exc),
                    "prediction": {"action": "error", "status": "error"},
                    "latency_ms": round((time.perf_counter() - started_at) * 1_000, 2),
                    "error": {"class": type(exc).__name__, "message": str(exc)[:300]},
                }
            )
    return records


def paired_runtime_schedule(
    cases: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], tuple[str, str]]]:
    """Keep each A/B pair adjacent while balancing which runtime runs first."""

    return [
        (
            case,
            ("direct", "pi") if index % 2 == 0 else ("pi", "direct"),
        )
        for index, case in enumerate(cases)
    ]


def three_arm_runtime_schedule(
    cases: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], tuple[str, str, str]]]:
    """Keep each three-arm trial adjacent and balance every order position."""

    permutations = tuple(itertools.permutations(("local", "direct", "pi")))
    return [
        (case, permutations[index % len(permutations)])
        for index, case in enumerate(cases)
    ]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    needs_provider = args.runtime != "local"
    config = load_replay_config() if needs_provider else None
    cases = load_dataset(args.dataset)
    modes = (
        ["local", "direct", "pi"]
        if args.runtime == "all"
        else ["direct", "pi"]
        if args.runtime == "both"
        else [args.runtime]
    )
    pi_runtime = PiCoachSidecar()
    results: dict[str, Any] = {}
    paired_execution: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            records_by_mode: dict[str, list[dict[str, Any]]] = {
                mode: [] for mode in modes
            }
            decision_case_ids_by_mode: dict[str, dict[str, str]] = {
                mode: {} for mode in modes
            }
            if args.runtime in {"both", "all"}:
                schedule = (
                    three_arm_runtime_schedule(cases)
                    if args.runtime == "all"
                    else paired_runtime_schedule(cases)
                )
                first_arm_counts = {mode: 0 for mode in modes}
                schedule_rows: list[dict[str, Any]] = []
                for case, arm_order in schedule:
                    first_arm_counts[arm_order[0]] += 1
                    schedule_rows.append(
                        {
                            "case_id": str(case["case_id"]),
                            "arm_order": list(arm_order),
                        }
                    )
                    for mode in arm_order:
                        records_by_mode[mode].extend(
                            await replay_mode(
                                [dict(case)],
                                runtime_name=mode,
                                config=config,
                                client=client,
                                pi_runtime=pi_runtime,
                                decision_case_ids=decision_case_ids_by_mode[mode],
                            )
                        )
                paired_execution = {
                    "policy": (
                        THREE_ARM_RUNTIME_ORDER_POLICY
                        if args.runtime == "all"
                        else PAIRED_RUNTIME_ORDER_POLICY
                    ),
                    "pair_adjacency": True,
                    "first_arm_counts": first_arm_counts,
                    "schedule": schedule_rows,
                }
                if args.runtime == "all":
                    paired_execution["runtimes"] = modes
            else:
                mode = modes[0]
                records_by_mode[mode] = await replay_mode(
                    cases,
                    runtime_name=mode,
                    config=config,
                    client=client,
                    pi_runtime=pi_runtime,
                    decision_case_ids=decision_case_ids_by_mode[mode],
                )

            for mode in modes:
                records = records_by_mode[mode]
                results[mode] = {
                    "score": score_predictions(records),
                    "score_by_skill": {
                        skill_id: score_predictions(
                            record for record in records if record.get("coach_skill_id") == skill_id
                        )
                        for skill_id in sorted({str(record["coach_skill_id"]) for record in records})
                    },
                    "records": records,
                }
    finally:
        await asyncio.to_thread(pi_runtime.close)
    report = {
        "schema_version": "talktrace.realtime_coach_eval.v1",
        "dataset": str(args.dataset),
        "model": config.model if config is not None else LOCAL_BASELINE_MODEL,
        "api_style": config.api_style if config is not None else LOCAL_BASELINE_API_STYLE,
        "evaluation_contract": {
            "request_schema_version": REPLAY_REQUEST_SCHEMA_VERSION,
            "output_schema_version": REPLAY_OUTPUT_SCHEMA_VERSION,
            "shared_deadline_policy": SHARED_REPLAY_DEADLINE_POLICY,
            "provider_comparable_runtimes": [
                mode for mode in modes if mode in {"direct", "pi"}
            ],
            "provider_identity_fingerprint": (
                provider_identity_fingerprint(config) if config is not None else None
            ),
            "provider_generation_contract": dict(REPLAY_PROVIDER_GENERATION_CONTRACT),
            "local_baseline": {
                "model": LOCAL_BASELINE_MODEL,
                "api_style": LOCAL_BASELINE_API_STYLE,
                "provider_called": False,
            },
        },
        "dataset_summary": ordered_lifecycle_summary(cases),
        "results": results,
    }
    if paired_execution is not None:
        report["paired_execution"] = paired_execution
    if "pi" in results:
        report["acceptance"] = acceptance_from_score(results["pi"]["score"])
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay source-aware transcripts through direct and/or Pi coach runtimes.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument(
        "--runtime",
        choices=("local", "direct", "pi", "both", "all"),
        default="both",
        help="use 'all' for the Stage 0C local/direct/Pi comparison",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--enforce-gates",
        action="store_true",
        help="return exit code 2 when the Pi Stage 0 acceptance thresholds fail",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = asyncio.run(run(args))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    acceptance = report.get("acceptance")
    if args.enforce_gates and isinstance(acceptance, Mapping) and not acceptance.get("passed"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run a two-phase Provider-backed Pi lifecycle probe against a live service.

This probe intentionally does not use the production replay acceptance helper:
an ordered meeting may contain terminal ``deadline_exceeded`` jobs for older
evidence while a later ``meeting.intelligence.applied`` event is valid. The
probe therefore records every applied decision and treats durable job status as
audit evidence instead of selecting only the newest job.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.pi_stage0_production_replay import (  # noqa: E402
    JsonHttpClient,
    _expect,
    end_meeting_with_retry,
    inspect_wav,
    meeting_preparation_payload,
    sanitize,
    snapshot_jobs_settled,
    stream_wav,
    wait_after_end,
)


def _get(client: JsonHttpClient, path: str) -> dict[str, Any]:
    result = client.request("GET", path)
    return _expect(result, {200}, layer="evidence", action=path)


def _formal_events(client: JsonHttpClient, meeting_id: str) -> list[dict[str, Any]]:
    payload = _get(client, f"/v2/meetings/{meeting_id}/events?after_seq=0&limit=1000")
    return [event for event in payload.get("events", []) if isinstance(event, dict)]


def _snapshot(client: JsonHttpClient, meeting_id: str) -> dict[str, Any]:
    return _get(client, f"/v2/meetings/{meeting_id}/snapshot?segment_limit=500")


def _compact_text(value: Any) -> str:
    """Normalize quote/text comparisons without changing the recorded evidence."""

    return "".join(str(value or "").split()).casefold()


def _formal_final_segments(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return authoritative transcript finals in formal event order."""

    finals: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "transcript.segment.finalized":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        segment_id = str(payload.get("segment_id") or "").strip()
        text = str(payload.get("normalized_text") or payload.get("text") or "").strip()
        if not segment_id or not text:
            continue
        finals.append(
            {
                "seq": event.get("seq"),
                "occurred_at_ms": event.get("occurred_at_ms"),
                "segment_id": segment_id,
                "text": text,
                "normalized_text": str(payload.get("normalized_text") or text).strip(),
                "transcript_seq": payload.get("transcript_seq"),
                "revision": payload.get("revision"),
            }
        )
    return finals


def _ws_final_segment_ids(events: list[Mapping[str, Any]]) -> list[str]:
    """Return non-empty ASR final IDs in the order observed on the socket."""

    result: list[str] = []
    for event in events:
        if str(event.get("event_type") or "") != "final":
            continue
        if event.get("authoritative") is False:
            continue
        segment_id = str(event.get("segment_id") or "").strip()
        text = str(event.get("normalized_text") or event.get("text") or "").strip()
        if segment_id and text:
            result.append(segment_id)
    return result


def _looks_like_resolution_text(text: Any) -> bool:
    """Require substantive owner/deadline evidence before closing a card.

    A repeated question or a generic "已解决" sentence is not enough to prove
    that the missing commitment was answered.  The probe intentionally uses a
    conservative lexical gate here: the application owns semantic detection,
    while the acceptance tool verifies that the resulting decision was tied to
    a fresh, auditable answer.
    """

    compact = _compact_text(text)
    if not compact:
        return False
    owner_markers = ("负责人", "我负责", "由", "owner", "值班")
    deadline_markers = (
        "今天",
        "明天",
        "下午",
        "上午",
        "晚上",
        "周一",
        "周二",
        "周三",
        "周四",
        "周五",
        "截止",
        "之前",
        "前",
        "点",
        "时",
    )
    resolution_markers = ("已", "通过", "完成", "确认", "解决", "不受影响", "复核")
    return (
        any(marker in compact for marker in owner_markers)
        and any(marker in compact for marker in deadline_markers)
        and any(marker in compact for marker in resolution_markers)
    )


def _event_seq(event: Mapping[str, Any]) -> int:
    value = event.get("seq")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _decision_id(row: Mapping[str, Any]) -> str:
    decision = row.get("decision")
    if not isinstance(decision, Mapping):
        return ""
    return str(decision.get("decision_id") or "").strip()


def _is_pi_provider_decision(
    row: Mapping[str, Any],
    *,
    status: str | None = None,
) -> bool:
    """Validate Pi provenance without coupling the probe to a gateway label.

    OpenAI-compatible providers are persisted under the transport identity
    (for example ``openai_compatible_gateway``), not the vendor/model name.
    The Pi decision envelope is the authoritative proof that the provider was
    attempted and the Pi runtime produced the result.
    """

    decision = row.get("decision")
    if not isinstance(decision, Mapping):
        return False
    return bool(
        decision.get("origin") == "pi"
        and decision.get("runtime_used") == "pi"
        and decision.get("pi_provider_attempted") is True
        and (status is None or decision.get("status") == status)
        and str(row.get("provider") or "").strip()
        and str(row.get("model") or "").strip()
    )


def _decision_links_to(
    row: Mapping[str, Any],
    *,
    ancestor_decision_id: str | None,
    all_rows: list[dict[str, Any]],
) -> bool:
    """Return whether a decision's supersession chain reaches an ancestor."""

    if not ancestor_decision_id:
        return False
    by_id = {
        _decision_id(candidate): candidate
        for candidate in all_rows
        if _decision_id(candidate)
    }
    decision = row.get("decision")
    parent_id = (
        str(decision.get("supersedes_decision_id") or "").strip()
        if isinstance(decision, Mapping)
        else ""
    )
    visited: set[str] = set()
    while parent_id and parent_id not in visited:
        if parent_id == ancestor_decision_id:
            return True
        visited.add(parent_id)
        parent = by_id.get(parent_id)
        parent_decision = parent.get("decision") if isinstance(parent, Mapping) else None
        parent_id = (
            str(parent_decision.get("supersedes_decision_id") or "").strip()
            if isinstance(parent_decision, Mapping)
            else ""
        )
    return False


def _rows_after_baseline(
    rows: list[dict[str, Any]],
    *,
    baseline_decision_ids: set[str],
    minimum_seq: int = 0,
) -> list[dict[str, Any]]:
    """Return new applied rows without relying on a success-only row count."""

    return [
        row
        for row in rows
        if _decision_id(row) not in baseline_decision_ids
        and int(row.get("seq") or 0) > minimum_seq
    ]


def _row_evidence(row: Mapping[str, Any]) -> tuple[set[str], str]:
    """Extract evidence IDs and quote from all supported decision envelopes."""

    decision = row.get("decision") if isinstance(row.get("decision"), Mapping) else {}
    intervention = row.get("intervention") if isinstance(row.get("intervention"), Mapping) else {}
    evidence = row.get("evidence") if isinstance(row.get("evidence"), Mapping) else {}
    evidence_ids: set[str] = set()
    for source, keys in (
        (evidence, ("segment_ids", "evidence_segment_ids")),
        (intervention, ("evidence_segment_ids", "segment_ids")),
        (decision, ("evidence_segment_ids", "lifecycle_refresh_evidence_segment_ids")),
    ):
        for key in keys:
            raw_ids = source.get(key)
            if isinstance(raw_ids, (list, tuple, set)):
                evidence_ids.update(str(item).strip() for item in raw_ids if str(item).strip())
            elif isinstance(raw_ids, str) and raw_ids.strip():
                evidence_ids.add(raw_ids.strip())
    quote = ""
    for source, keys in (
        (evidence, ("quote", "evidence_quote")),
        (intervention, ("evidence_quote", "quote")),
        (decision, ("evidence_quote", "lifecycle_refresh_evidence_quote")),
    ):
        for key in keys:
            candidate = str(source.get(key) or "").strip()
            if candidate:
                quote = candidate
                break
        if quote:
            break
    return evidence_ids, quote


def _decision_rows(events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "meeting.intelligence.applied":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        decision = payload.get("coach_decision") if isinstance(payload.get("coach_decision"), Mapping) else {}
        intervention = payload.get("coach_intervention")
        rows.append(
            {
                "seq": event.get("seq"),
                "occurred_at_ms": event.get("occurred_at_ms"),
                "job_id": payload.get("job_id"),
                "source": payload.get("source"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "decision": {
                    key: decision.get(key)
                    for key in (
                        "decision_id",
                        "status",
                        "status_reason",
                        "origin",
                        "runtime_requested",
                        "runtime_used",
                        "pi_provider_attempted",
                        "lifecycle_action",
                        "supersedes_decision_id",
                        "superseded_by",
                        "candidate_event",
                        "evidence_revision",
                        "evidence_segment_ids",
                        "evidence_quote",
                        "lifecycle_refresh",
                        "lifecycle_refresh_evidence_segment_ids",
                        "lifecycle_refresh_evidence_quote",
                        "final_committed_at_ms",
                        "decision_completed_at_ms",
                        "projected_at_ms",
                        "valid_until_ms",
                        "decision_latency_ms",
                        "ttft_ms",
                    )
                    if key in decision
                },
                "intervention": (
                    {
                        key: intervention.get(key)
                        for key in (
                            "decision_id",
                            "status",
                            "status_reason",
                            "origin",
                            "runtime_used",
                            "lifecycle_action",
                            "supersedes_decision_id",
                            "superseded_by",
                            "event_type",
                            "evidence_segment_ids",
                            "evidence_quote",
                            "say_this",
                            "why_now",
                            "valid_until_ms",
                        )
                        if key in intervention
                    }
                    if isinstance(intervention, Mapping)
                    else None
                ),
                "evidence": {
                    key: evidence.get(key)
                    for key in ("segment_ids", "evidence_segment_ids", "quote", "evidence_quote", "state_revision", "evidence_hash")
                    if key in evidence
                }
                if isinstance(evidence := payload.get("evidence"), Mapping)
                else {},
            }
        )
    return rows


def _wait_for_applied(
    client: JsonHttpClient,
    meeting_id: str,
    *,
    previous_count: int,
    timeout_seconds: float,
    require_pi_intervention: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Wait for a new applied event, ignoring superseded terminal jobs.

    The first phase asks for a successful Pi intervention specifically. This
    avoids returning early when an older coalesced job emits a timeout audit
    immediately before the valid intervention is projected.
    """

    deadline = time.monotonic() + timeout_seconds
    last_events: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    latest_rows: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last_events = _formal_events(client, meeting_id)
        rows = _decision_rows(last_events)
        # ``rows`` is the full meeting history. Return only the suffix after
        # the caller's observed count; otherwise phase 2 can mistake the old
        # Pi intervention for a fresh lifecycle decision.
        latest_rows = rows[previous_count:]
        last_snapshot = _snapshot(client, meeting_id)
        if latest_rows and (
            not require_pi_intervention
            or any(_is_pi_provider_decision(row, status="intervention") for row in latest_rows)
        ):
            return latest_rows, last_events, last_snapshot
        time.sleep(0.25)
    return latest_rows, last_events, last_snapshot


def _wait_for_lifecycle_refresh(
    client: JsonHttpClient,
    meeting_id: str,
    *,
    baseline_decision_ids: set[str],
    baseline_segment_ids: set[str],
    baseline_seq: int,
    old_decision_id: str | None,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Wait for a lifecycle decision grounded in the resolving final.

    The old probe returned as soon as any second ``meeting.intelligence.applied``
    event appeared.  That is insufficient for an ordered replay: the second
    event can be caused by a duplicate final containing the old question while
    the actual owner/deadline answer arrives later.  This helper keeps polling
    until the applied event is causally after a fresh resolving final and its
    evidence quote/IDs point to that exact final.
    """

    deadline = time.monotonic() + timeout_seconds
    last_events: list[dict[str, Any]] = []
    last_snapshot: dict[str, Any] = {}
    validation: dict[str, Any] = {
        "fresh_resolving_final": False,
        "fresh_resolving_final_ids": [],
        "fresh_resolving_final_texts": [],
        "fresh_resolving_final_seq": None,
        "fresh_resolving_final_at_ms": None,
        "lifecycle_decision_after_final": False,
        "lifecycle_evidence_segment_exact": False,
        "lifecycle_evidence_quote_exact": False,
        "lifecycle_status_protected_silent": False,
        "lifecycle_provider_pi": False,
        "lifecycle_linked_old_decision": False,
        "no_early_lifecycle_close": True,
        "lifecycle_valid": False,
        "failure_reasons": [],
    }

    while time.monotonic() < deadline:
        last_events = _formal_events(client, meeting_id)
        all_rows = _decision_rows(last_events)
        new_rows = _rows_after_baseline(
            all_rows,
            baseline_decision_ids=baseline_decision_ids,
            minimum_seq=baseline_seq,
        )
        finals = [
            final
            for final in _formal_final_segments(last_events)
            if _event_seq(final) > baseline_seq
            and final["segment_id"] not in baseline_segment_ids
            and _looks_like_resolution_text(final["text"])
        ]
        # Keep the newest valid resolving final as the causal anchor.  A
        # repeated old question is intentionally excluded by the lexical gate.
        fresh_final = finals[-1] if finals else None
        validation.update(
            {
                "fresh_resolving_final": fresh_final is not None,
                "fresh_resolving_final_ids": [item["segment_id"] for item in finals],
                "fresh_resolving_final_texts": [item["text"] for item in finals],
                "fresh_resolving_final_seq": fresh_final.get("seq") if fresh_final else None,
                "fresh_resolving_final_at_ms": (
                    fresh_final.get("occurred_at_ms") if fresh_final else None
                ),
            }
        )
        if fresh_final is not None:
            fresh_id = fresh_final["segment_id"]
            fresh_quote = _compact_text(fresh_final["text"])
            lifecycle_rows = [
                row
                for row in new_rows
                if int(row.get("seq") or 0) > _event_seq(fresh_final)
            ]
            for row in lifecycle_rows:
                decision = row.get("decision") if isinstance(row.get("decision"), Mapping) else {}
                evidence_ids, quote = _row_evidence(row)
                status_ok = decision.get("status") == "protected_silent"
                provider_ok = _is_pi_provider_decision(row, status="protected_silent")
                linked_ok = _decision_links_to(
                    row,
                    ancestor_decision_id=old_decision_id,
                    all_rows=all_rows,
                )
                ids_ok = evidence_ids == {fresh_id}
                quote_ok = _compact_text(quote) == fresh_quote
                decision_time = decision.get("final_committed_at_ms")
                if decision_time is None:
                    decision_time = row.get("occurred_at_ms")
                try:
                    decision_after_final = int(decision_time) >= int(fresh_final["occurred_at_ms"])
                except (TypeError, ValueError):
                    decision_after_final = False
                if decision_after_final and status_ok and provider_ok and linked_ok and ids_ok and quote_ok:
                    early_rows = [
                        prior_row
                        for prior_row in new_rows
                        if (prior_row.get("decision") or {}).get("lifecycle_refresh") is True
                        and int(prior_row.get("seq") or 0) <= _event_seq(fresh_final)
                    ]
                    no_early_close = not early_rows
                    validation.update(
                        {
                            "lifecycle_decision_after_final": True,
                            "lifecycle_evidence_segment_exact": True,
                            "lifecycle_evidence_quote_exact": True,
                            "lifecycle_status_protected_silent": True,
                            "lifecycle_provider_pi": True,
                            "lifecycle_linked_old_decision": True,
                            "lifecycle_decision_id": _decision_id(row),
                            "lifecycle_decision_seq": row.get("seq"),
                            "lifecycle_decision_at_ms": row.get("occurred_at_ms"),
                            "no_early_lifecycle_close": no_early_close,
                            "lifecycle_valid": no_early_close,
                        }
                    )
                    if early_rows:
                        validation["failure_reasons"].append("lifecycle_closed_before_resolving_final")
                    return new_rows, last_events, _snapshot(client, meeting_id), validation
            # A refresh that occurred before the fresh final is an explicit
            # failure, not a successful lifecycle transition.
            early_rows = [
                row
                for row in new_rows
                if (row.get("decision") or {}).get("lifecycle_refresh") is True
                and int(row.get("seq") or 0) <= _event_seq(fresh_final)
            ]
            if early_rows:
                validation["no_early_lifecycle_close"] = False
        time.sleep(0.25)

    if not validation["fresh_resolving_final"]:
        validation["failure_reasons"].append("missing_fresh_resolving_final")
    if validation["fresh_resolving_final"] and not validation["lifecycle_decision_after_final"]:
        validation["failure_reasons"].append("missing_valid_lifecycle_decision_after_final")
    if not validation["no_early_lifecycle_close"]:
        validation["failure_reasons"].append("lifecycle_closed_before_resolving_final")
    return (
        _rows_after_baseline(
            _decision_rows(last_events),
            baseline_decision_ids=baseline_decision_ids,
            minimum_seq=baseline_seq,
        ),
        last_events,
        last_snapshot,
        validation,
    )


def _compact_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "transcript": [
            {
                key: segment.get(key)
                for key in (
                    "segment_id",
                    "normalized_text",
                    "source_track",
                    "transcript_seq",
                    "started_at_ms",
                    "ended_at_ms",
                    "revision",
                    "evidence_hash",
                )
            }
            for segment in snapshot.get("segments", [])
            if isinstance(segment, Mapping)
        ],
        "coach_history": snapshot.get("coach_history"),
        "suggestions": snapshot.get("suggestions"),
        "jobs": [
            {
                key: job.get(key)
                for key in (
                    "id",
                    "kind",
                    "status",
                    "error_class",
                    "attempts",
                    "evidence_segment_id",
                    "created_at_ms",
                    "completed_at_ms",
                )
            }
            for job in snapshot.get("jobs", [])
            if isinstance(job, Mapping)
        ],
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    question_path = args.question_wav.expanduser().resolve()
    resolving_path = args.resolving_wav.expanduser().resolve()
    question = inspect_wav(question_path)
    resolving = inspect_wav(resolving_path)
    client = JsonHttpClient(args.base_url, timeout_seconds=args.http_timeout)
    provider_health = _expect(
        client.request("GET", "/providers/health"),
        {200},
        layer="provider",
        action="provider health",
    )
    _expect(
        client.request(
            "POST",
            "/v2/meetings",
            payload={
                "meeting_id": args.meeting_id,
                "title": "Pi Provider lifecycle probe",
                "expected_duration_seconds": 180,
                "track_count": 1,
            },
        ),
        {201},
        layer="create",
        action="create meeting",
    )
    _expect(
        client.request(
            "PUT",
            f"/v2/meetings/{args.meeting_id}/preparation",
            payload=meeting_preparation_payload(question),
        ),
        {200},
        layer="preparation",
        action="save preparation",
    )

    question_ws_events: list[dict[str, Any]] = []
    question_stats = stream_wav(
        client,
        meeting_id=args.meeting_id,
        wav_path=question_path,
        wav_info=question,
        pace=args.pace,
        chunk_seconds=args.chunk_seconds,
        tail_silence_seconds=args.question_tail_seconds,
        ready_timeout_seconds=args.ready_timeout,
        finalize_timeout_seconds=args.finalize_timeout,
        event_sink=question_ws_events,
        audio_source="simulated_realtime_wav",
    )
    question_rows, question_events, question_snapshot = _wait_for_applied(
        client,
        args.meeting_id,
        previous_count=0,
        timeout_seconds=args.intelligence_timeout,
        require_pi_intervention=True,
    )
    question_all_rows = _decision_rows(question_events)
    baseline_decision_ids = {
        _decision_id(row) for row in question_all_rows if _decision_id(row)
    }
    baseline_final_segments = _formal_final_segments(question_events)
    baseline_segment_ids = {item["segment_id"] for item in baseline_final_segments}
    baseline_seq = max((_event_seq(event) for event in question_events), default=0)
    old_decision_id = next(
        (
            _decision_id(row)
            for row in question_all_rows
            if row.get("decision", {}).get("origin") == "pi"
            and row.get("decision", {}).get("status") == "intervention"
        ),
        None,
    )

    resolving_ws_events: list[dict[str, Any]] = []
    resolving_stats = stream_wav(
        client,
        meeting_id=args.meeting_id,
        wav_path=resolving_path,
        wav_info=resolving,
        pace=args.pace,
        chunk_seconds=args.chunk_seconds,
        tail_silence_seconds=args.resolving_tail_seconds,
        ready_timeout_seconds=args.ready_timeout,
        finalize_timeout_seconds=args.finalize_timeout,
        event_sink=resolving_ws_events,
        audio_source="simulated_realtime_wav",
    )
    resolving_rows, resolving_events, resolving_snapshot, lifecycle_validation = _wait_for_lifecycle_refresh(
        client,
        args.meeting_id,
        baseline_decision_ids=baseline_decision_ids,
        baseline_segment_ids=baseline_segment_ids,
        baseline_seq=baseline_seq,
        old_decision_id=old_decision_id,
        timeout_seconds=args.intelligence_timeout,
    )

    end_result = end_meeting_with_retry(
        client,
        meeting_id=args.meeting_id,
        timeout_seconds=max(45.0, args.http_timeout),
        poll_interval_seconds=0.25,
    )
    final_snapshot, post_end_settled = wait_after_end(
        client,
        meeting_id=args.meeting_id,
        timeout_seconds=args.post_end_timeout,
        poll_interval_seconds=0.25,
    )
    final_events = _formal_events(client, args.meeting_id)
    rows = _decision_rows(final_events)
    final_applied_rows = [row for row in rows if _decision_id(row)]
    resolving_rows = [
        row
        for row in final_applied_rows
        if _decision_id(row) not in baseline_decision_ids
        and _event_seq(row) > baseline_seq
    ]
    assertions = {
        "phase_1_pi_intervention": any(
            _is_pi_provider_decision(row, status="intervention") for row in question_rows
        ),
        # _wait_for_applied returns only the phase-2 suffix, not the complete
        # history.  A single returned row is therefore a valid new decision.
        "phase_2_new_applied_decision": bool(resolving_rows),
        "phase_2_provider_pi": lifecycle_validation["lifecycle_provider_pi"],
        "phase_2_silent_or_stale": lifecycle_validation["lifecycle_status_protected_silent"],
        "old_decision_linked": lifecycle_validation["lifecycle_linked_old_decision"],
        "fresh_resolving_final": lifecycle_validation["fresh_resolving_final"],
        "lifecycle_decision_after_final": lifecycle_validation["lifecycle_decision_after_final"],
        "lifecycle_evidence_segment_exact": lifecycle_validation["lifecycle_evidence_segment_exact"],
        "lifecycle_evidence_quote_exact": lifecycle_validation["lifecycle_evidence_quote_exact"],
        "no_early_lifecycle_close": lifecycle_validation["no_early_lifecycle_close"],
        "post_end_jobs_settled": bool(post_end_settled and snapshot_jobs_settled(final_snapshot)),
        "history_retained": len(final_snapshot.get("coach_history") or []) >= 1,
        "old_history_terminal_lifecycle": any(
            row.get("decision_id") == old_decision_id
            and (row.get("lifecycle_action") in {"retract", "deprioritize"} or row.get("superseded_by"))
            for row in final_snapshot.get("coach_history") or []
        ),
    }
    output = {
        "schema_version": "pi_provider_lifecycle_validation.v1",
        "meeting_id": args.meeting_id,
        "service_port": int(args.base_url.rsplit(":", 1)[-1]),
        "provider": {
            key: provider_health.get("llm", {}).get(key)
            for key in (
                "configured",
                "provider",
                "model",
                "realtime_model",
                "realtime_model_source",
                "realtime_ready",
            )
            if isinstance(provider_health.get("llm"), Mapping)
        },
        "fixtures": {
            "question": {
                "filename": question.filename,
                "sha256": question.sha256,
                "duration_seconds": question.duration_seconds,
            },
            "resolving": {
                "filename": resolving.filename,
                "sha256": resolving.sha256,
                "duration_seconds": resolving.duration_seconds,
            },
        },
        "phase_1": {
            "ws": {
                key: question_stats.get(key)
                for key in (
                    "audio_source",
                    "audio_provenance",
                    "ready",
                    "non_empty_final_count",
                    "event_counts",
                    "stream_wall_ms",
                    "asr_shutdown_diagnostics",
                )
            },
            "decisions": question_rows,
            "snapshot": _compact_snapshot(question_snapshot),
        },
        "phase_2": {
            "ws": {
                key: resolving_stats.get(key)
                for key in (
                    "audio_source",
                    "audio_provenance",
                    "ready",
                    "non_empty_final_count",
                    "event_counts",
                    "stream_wall_ms",
                    "asr_shutdown_diagnostics",
                )
            },
            "decisions": resolving_rows,
            "snapshot": _compact_snapshot(resolving_snapshot),
            "lifecycle_validation": lifecycle_validation,
        },
        "assertions": assertions,
        "end": {
            "http_status": end_result.status,
            "runtime_phase": final_snapshot.get("runtime", {}).get("phase"),
            "post_end_settled": bool(post_end_settled),
            "jobs_settled": bool(snapshot_jobs_settled(final_snapshot)),
        },
        "final_snapshot": _compact_snapshot(final_snapshot),
        "formal_event_count": len(final_events),
    }
    safe_output = sanitize(output)
    # The managed launcher may pre-create the probe directory.  Allow that
    # empty directory, but refuse to overwrite a previous report in place.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    existing_outputs = list(args.output_dir.iterdir())
    if existing_outputs:
        raise RuntimeError(
            "probe output directory must be empty: " + str(args.output_dir)
        )
    (args.output_dir / "lifecycle-summary.json").write_text(
        json.dumps(safe_output, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "phase-1-ws-events.jsonl").write_text(
        "".join(json.dumps(sanitize(event), ensure_ascii=False, sort_keys=True) + "\n" for event in question_ws_events),
        encoding="utf-8",
    )
    (args.output_dir / "phase-2-ws-events.jsonl").write_text(
        "".join(json.dumps(sanitize(event), ensure_ascii=False, sort_keys=True) + "\n" for event in resolving_ws_events),
        encoding="utf-8",
    )
    (args.output_dir / "formal-events.jsonl").write_text(
        "".join(json.dumps(sanitize(event), ensure_ascii=False, sort_keys=True) + "\n" for event in final_events),
        encoding="utf-8",
    )
    return safe_output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--question-wav", required=True, type=Path)
    parser.add_argument("--resolving-wav", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--pace", type=float, default=1.0)
    parser.add_argument("--chunk-seconds", type=float, default=0.3)
    parser.add_argument("--question-tail-seconds", type=float, default=3.0)
    parser.add_argument("--resolving-tail-seconds", type=float, default=3.0)
    parser.add_argument("--ready-timeout", type=float, default=60.0)
    parser.add_argument("--finalize-timeout", type=float, default=120.0)
    parser.add_argument("--intelligence-timeout", type=float, default=90.0)
    parser.add_argument("--post-end-timeout", type=float, default=120.0)
    parser.add_argument("--http-timeout", type=float, default=45.0)
    return parser.parse_args(argv)


if __name__ == "__main__":
    arguments = parse_args()
    result = run(arguments)
    print(json.dumps({"output_dir": str(arguments.output_dir), "assertions": result["assertions"]}, ensure_ascii=False))

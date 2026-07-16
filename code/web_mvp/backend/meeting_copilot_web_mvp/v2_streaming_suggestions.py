"""Streaming suggestion generation with durable drafts and a commit barrier."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
import json
import time
from typing import Any

from .streaming_llm_provider import (
    CompletionDelta,
    OpenAICompatibleStreamingProvider,
    TokenUsage,
)
from .v2_persistence import V2Persistence


class StreamingSuggestionError(RuntimeError):
    """Base error for a suggestion that must not become a formal result."""

    retryable = False


class SuggestionValidationError(StreamingSuggestionError):
    """The job identity or generated suggestion is not safe to persist."""


class StaleSuggestionEvidenceError(StreamingSuggestionError):
    """The transcript evidence changed before the suggestion commit barrier."""


def _wall_clock_ms() -> int:
    return time.time_ns() // 1_000_000


def build_realtime_suggestion_messages(*, gap: Any, evidence: Any) -> list[dict[str, str]]:
    """Build the single production prompt used by live suggestions and value review."""

    gap_text = str(gap or "").strip()
    evidence_text = str(evidence or "").strip()
    if not gap_text or not evidence_text:
        raise SuggestionValidationError("suggestion gap and evidence must not be empty")
    return [
        {
            "role": "system",
            "content": (
                "你是中文技术会议实时副驾驶。基于会议原话，只输出一句现在仍来得及追问的中文问题；"
                "不得输出 JSON、解释、标题或虚构事实，措辞以建议确认或是否考虑开头。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"gap": gap_text, "evidence": evidence_text},
                ensure_ascii=False,
            ),
        },
    ]


async def generate_streaming_suggestion(
    *,
    job: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
    provider: OpenAICompatibleStreamingProvider,
    persistence: V2Persistence,
    suggestion_id: str | None = None,
    checkpoint_interval_seconds: float = 0.250,
    checkpoint_characters: int = 64,
    max_characters: int = 240,
    completion_parameters: Mapping[str, Any] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    now_ms: Callable[[], int] = _wall_clock_ms,
) -> dict[str, Any]:
    """Generate one short suggestion for an already-claimed durable job.

    Draft checkpoints are visible to the UI, but they are never formal output.
    Only ``commit_suggestion`` can cross the evidence and generation barrier.
    Provider failures and task cancellation therefore leave, at most, a draft.
    """

    identity = _SuggestionIdentity.from_claimed_job(job, suggestion_id=suggestion_id)
    _validate_options(
        messages=messages,
        checkpoint_interval_seconds=checkpoint_interval_seconds,
        checkpoint_characters=checkpoint_characters,
        max_characters=max_characters,
    )

    parts: list[str] = []
    existing = await asyncio.to_thread(persistence.get_suggestion, identity.suggestion_id)
    if existing is not None and existing.get("status") == "committed":
        raise StaleSuggestionEvidenceError("suggestion generation has already been committed")
    draft_seq = int(existing.get("draft_seq") or 0) if existing is not None else 0
    checkpoint_text = _normalized_text(existing.get("draft_text") or "") if existing is not None else ""
    checkpoint_at: float | None = None

    async def checkpoint(text: str, observed_at: float) -> None:
        nonlocal checkpoint_at, checkpoint_text, draft_seq
        draft_seq += 1
        row = await asyncio.to_thread(
            persistence.upsert_suggestion_draft,
            suggestion_id=identity.suggestion_id,
            meeting_id=identity.meeting_id,
            job_id=identity.job_id,
            generation_id=identity.generation_id,
            evidence_segment_id=identity.evidence_segment_id,
            evidence_transcript_seq=identity.evidence_transcript_seq,
            evidence_hash=identity.evidence_hash,
            state_revision=identity.state_revision,
            draft_text=text,
            draft_seq=draft_seq,
            now_ms=now_ms(),
            lease_owner=identity.lease_owner,
        )
        _validate_persisted_identity(row, identity, expected_draft_seq=draft_seq)
        checkpoint_text = text
        checkpoint_at = observed_at

    async def on_delta(delta: CompletionDelta) -> None:
        if not isinstance(delta.text, str):
            raise SuggestionValidationError("suggestion delta must be plain text")
        parts.append(delta.text)
        candidate = _normalized_text("".join(parts))
        if not candidate:
            return
        _validate_length(candidate, max_characters=max_characters)

        observed_at = monotonic()
        first_readable_delta = draft_seq == 0
        elapsed = (
            observed_at - checkpoint_at
            if checkpoint_at is not None
            else checkpoint_interval_seconds
        )
        added_characters = max(0, len(candidate) - len(checkpoint_text))
        if (
            first_readable_delta
            or elapsed >= checkpoint_interval_seconds
            or added_characters >= checkpoint_characters
        ):
            await checkpoint(candidate, observed_at)

    parameters: dict[str, Any] = {
        "temperature": 0.2,
        "max_completion_tokens": 128,
    }
    if completion_parameters is not None:
        parameters.update(dict(completion_parameters))

    result = await provider.complete(
        messages,
        on_delta=on_delta,
        idempotency_key=_required_job_text(job, "idempotency_key"),
        **parameters,
    )

    final_text = _normalized_text(result.content)
    if not final_text:
        raise SuggestionValidationError("suggestion is empty")
    _validate_length(final_text, max_characters=max_characters)

    accumulated_text = _normalized_text("".join(parts))
    if accumulated_text and accumulated_text != final_text:
        raise SuggestionValidationError(
            "provider final suggestion does not match its streamed deltas"
        )

    if checkpoint_text != final_text:
        await checkpoint(final_text, monotonic())
    if draft_seq <= 0:
        raise SuggestionValidationError("suggestion produced no durable draft")

    # Keep the final SQLite barrier synchronous: cancellation cannot detach a
    # background commit that succeeds after the coroutine reports cancellation.
    committed = persistence.commit_suggestion(
        suggestion_id=identity.suggestion_id,
        generation_id=identity.generation_id,
        expected_evidence_hash=identity.evidence_hash,
        final_draft_seq=draft_seq,
        text=final_text,
        now_ms=now_ms(),
        expected_job_id=identity.job_id if identity.lease_owner is not None else None,
        expected_lease_owner=identity.lease_owner,
    )
    if committed is None:
        raise StaleSuggestionEvidenceError(
            "suggestion commit barrier rejected stale evidence or generation"
        )
    _validate_persisted_identity(committed, identity, expected_draft_seq=draft_seq)
    if committed.get("status") != "committed" or committed.get("text") != final_text:
        raise StreamingSuggestionError("suggestion commit returned an invalid result")

    return {
        "transport_mode": result.transport_mode.value,
        "fallback_reason": result.fallback_reason,
        "ttft_ms": result.timings.time_to_first_token_seconds * 1_000,
        "timings": {
            "started_at": result.timings.started_at,
            "connected_at": result.timings.connected_at,
            "first_token_at": result.timings.first_token_at,
            "completed_at": result.timings.completed_at,
        },
        "usage": _usage_dict(result.usage),
        "suggestion": committed,
    }


class _SuggestionIdentity:
    def __init__(
        self,
        *,
        suggestion_id: str,
        job_id: str,
        meeting_id: str,
        generation_id: str,
        evidence_segment_id: str,
        evidence_transcript_seq: int,
        evidence_hash: str,
        state_revision: int,
        lease_owner: str | None,
    ) -> None:
        self.suggestion_id = suggestion_id
        self.job_id = job_id
        self.meeting_id = meeting_id
        self.generation_id = generation_id
        self.evidence_segment_id = evidence_segment_id
        self.evidence_transcript_seq = evidence_transcript_seq
        self.evidence_hash = evidence_hash
        self.state_revision = state_revision
        self.lease_owner = lease_owner

    @classmethod
    def from_claimed_job(
        cls,
        job: Mapping[str, Any],
        *,
        suggestion_id: str | None,
    ) -> _SuggestionIdentity:
        if not isinstance(job, Mapping):
            raise SuggestionValidationError("suggestion job must be a mapping")
        if str(job.get("kind") or "") != "suggestion":
            raise SuggestionValidationError("durable job is not in the suggestion lane")
        if str(job.get("status") or "") != "running":
            raise SuggestionValidationError("suggestion job must already be claimed")

        job_id = _required_job_text(job, "id")
        evidence_transcript_seq = _positive_job_integer(job, "input_transcript_seq")
        state_revision = _positive_job_integer(job, "input_version")
        resolved_suggestion_id = str(suggestion_id or f"suggestion:{job_id}").strip()
        if not resolved_suggestion_id:
            raise SuggestionValidationError("suggestion_id must not be empty")
        return cls(
            suggestion_id=resolved_suggestion_id,
            job_id=job_id,
            meeting_id=_required_job_text(job, "meeting_id"),
            generation_id=_required_job_text(job, "generation_id"),
            evidence_segment_id=_required_job_text(job, "evidence_segment_id"),
            evidence_transcript_seq=evidence_transcript_seq,
            evidence_hash=_required_job_text(job, "evidence_hash"),
            state_revision=state_revision,
            lease_owner=(str(job.get("lease_owner") or "").strip() or None),
        )


def _validate_options(
    *,
    messages: Sequence[Mapping[str, Any]],
    checkpoint_interval_seconds: float,
    checkpoint_characters: int,
    max_characters: int,
) -> None:
    if not messages or any(not isinstance(message, Mapping) for message in messages):
        raise SuggestionValidationError("messages must contain at least one message mapping")
    if not 0 < checkpoint_interval_seconds <= 0.250:
        raise ValueError("checkpoint_interval_seconds must be in (0, 0.250]")
    if not 0 < checkpoint_characters <= 64:
        raise ValueError("checkpoint_characters must be in [1, 64]")
    if max_characters <= 0:
        raise ValueError("max_characters must be positive")


def _required_job_text(job: Mapping[str, Any], field: str) -> str:
    value = str(job.get(field) or "").strip()
    if not value:
        raise SuggestionValidationError(f"suggestion job is missing {field}")
    return value


def _positive_job_integer(job: Mapping[str, Any], field: str) -> int:
    value = job.get(field)
    if isinstance(value, bool):
        raise SuggestionValidationError(f"suggestion job has invalid {field}")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SuggestionValidationError(f"suggestion job has invalid {field}") from exc
    if number <= 0:
        raise SuggestionValidationError(f"suggestion job has invalid {field}")
    return number


def _normalized_text(value: Any) -> str:
    if not isinstance(value, str):
        raise SuggestionValidationError("suggestion must be plain text")
    return " ".join(value.split())


def _validate_length(text: str, *, max_characters: int) -> None:
    if len(text) > max_characters:
        raise SuggestionValidationError(
            f"suggestion exceeds maximum length of {max_characters} characters"
        )


def _validate_persisted_identity(
    row: Mapping[str, Any],
    identity: _SuggestionIdentity,
    *,
    expected_draft_seq: int,
) -> None:
    expected = {
        "suggestion_id": identity.suggestion_id,
        "job_id": identity.job_id,
        "meeting_id": identity.meeting_id,
        "generation_id": identity.generation_id,
        "evidence_segment_id": identity.evidence_segment_id,
        "evidence_transcript_seq": identity.evidence_transcript_seq,
        "evidence_hash": identity.evidence_hash,
        "state_revision": identity.state_revision,
        "draft_seq": expected_draft_seq,
    }
    conflicts = {
        field: (row.get(field), expected_value)
        for field, expected_value in expected.items()
        if row.get(field) != expected_value
    }
    if conflicts:
        raise StaleSuggestionEvidenceError(
            f"persisted suggestion identity changed before commit: {conflicts}"
        )


def _usage_dict(usage: TokenUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
    }

"""Stable semantic paragraph assembly and realtime intelligence batching."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class _Checkpoint:
    checkpoint_id: str
    text: str
    start_ms: int
    end_ms: int
    speaker: str | None

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "_Checkpoint":
        if not isinstance(value, Mapping):
            raise ValueError("checkpoint must be an object")
        checkpoint_id = _required_text(value.get("checkpoint_id"), "checkpoint_id", maximum=200)
        text = _required_text(value.get("text"), "text", maximum=20_000)
        start_ms = _non_negative_integer(value.get("start_ms"), "start_ms")
        end_ms = _non_negative_integer(value.get("end_ms"), "end_ms")
        if end_ms < start_ms:
            raise ValueError("checkpoint end_ms must not precede start_ms")
        raw_speaker = value.get("speaker")
        speaker = None if raw_speaker is None else _required_text(raw_speaker, "speaker", maximum=120)
        return cls(
            checkpoint_id=checkpoint_id,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
            speaker=speaker,
        )


class ParagraphAssembler:
    """Turn append/update ASR checkpoints into stable user-facing paragraphs.

    ASR checkpoints remain the recovery units. The active paragraph is updated
    in place and becomes immutable only after a speaker boundary, a meaningful
    silence, an explicit flush, or the configured paragraph duration ceiling.
    """

    def __init__(
        self,
        *,
        stabilize_silence_ms: int = 1_800,
        maximum_paragraph_ms: int = 60_000,
    ) -> None:
        self._stabilize_silence_ms = _positive_integer(
            stabilize_silence_ms,
            "stabilize_silence_ms",
        )
        self._maximum_paragraph_ms = _positive_integer(
            maximum_paragraph_ms,
            "maximum_paragraph_ms",
        )
        if self._maximum_paragraph_ms <= self._stabilize_silence_ms:
            raise ValueError("maximum_paragraph_ms must exceed stabilize_silence_ms")
        self._active: OrderedDict[str, _Checkpoint] = OrderedDict()
        self._stable: list[dict[str, Any]] = []
        self._stable_checkpoint_ids: set[str] = set()

    def add(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        checkpoint = _Checkpoint.from_payload(payload)
        if checkpoint.checkpoint_id in self._stable_checkpoint_ids:
            return []

        existing = self._active.get(checkpoint.checkpoint_id)
        if existing is not None:
            self._active[checkpoint.checkpoint_id] = checkpoint
            self._validate_active_order()
            return []

        emitted: list[dict[str, Any]] = []
        if self._active and self._starts_new_paragraph(checkpoint):
            stable = self._stabilize_active()
            if stable is not None:
                emitted.append(stable)
        self._active[checkpoint.checkpoint_id] = checkpoint
        self._validate_active_order()
        return emitted

    def flush(self, *, now_ms: int | None = None, force: bool = False) -> dict[str, Any] | None:
        if not self._active:
            return None
        if not force:
            if now_ms is None:
                raise ValueError("now_ms is required unless force is true")
            normalized_now = _non_negative_integer(now_ms, "now_ms")
            if normalized_now - self._active_end_ms < self._stabilize_silence_ms:
                return None
        return self._stabilize_active()

    def snapshot(self) -> dict[str, Any]:
        return {
            "stable_paragraphs": deepcopy(self._stable),
            "active_paragraph": deepcopy(self._active_paragraph()),
        }

    @property
    def active_checkpoint_count(self) -> int:
        return len(self._active)

    def _starts_new_paragraph(self, checkpoint: _Checkpoint) -> bool:
        first = next(iter(self._active.values()))
        previous = next(reversed(self._active.values()))
        if checkpoint.start_ms < previous.start_ms:
            raise ValueError("checkpoints must be added in chronological order")
        if checkpoint.speaker != previous.speaker:
            return True
        if checkpoint.start_ms - previous.end_ms >= self._stabilize_silence_ms:
            return True
        return checkpoint.end_ms - first.start_ms > self._maximum_paragraph_ms

    def _stabilize_active(self) -> dict[str, Any] | None:
        paragraph = self._active_paragraph()
        if paragraph is None:
            return None
        paragraph["status"] = "stable"
        self._stable.append(paragraph)
        self._stable_checkpoint_ids.update(paragraph["checkpoint_ids"])
        self._active.clear()
        return deepcopy(paragraph)

    def _active_paragraph(self) -> dict[str, Any] | None:
        if not self._active:
            return None
        checkpoints = list(self._active.values())
        text = ""
        for checkpoint in checkpoints:
            text = _merge_checkpoint_text(text, checkpoint.text)
        first = checkpoints[0]
        return {
            "id": f"paragraph:{first.checkpoint_id}",
            "text": text,
            "revision": 1,
            "start_ms": first.start_ms,
            "end_ms": max(item.end_ms for item in checkpoints),
            "speaker": first.speaker,
            "checkpoint_ids": [item.checkpoint_id for item in checkpoints],
            "status": "active",
        }

    @property
    def _active_end_ms(self) -> int:
        return max(item.end_ms for item in self._active.values())

    def _validate_active_order(self) -> None:
        checkpoints = list(self._active.values())
        for previous, current in zip(checkpoints, checkpoints[1:]):
            if current.start_ms < previous.start_ms:
                raise ValueError("checkpoint update would invalidate chronological order")


class RealtimeIntelligenceBatcher:
    """Coalesce stable paragraphs behind one in-flight intelligence request."""

    def __init__(self, *, debounce_ms: int = 2_000, maximum_wait_ms: int = 7_000) -> None:
        self._debounce_ms = _positive_integer(debounce_ms, "debounce_ms")
        self._maximum_wait_ms = _positive_integer(maximum_wait_ms, "maximum_wait_ms")
        if self._maximum_wait_ms < self._debounce_ms:
            raise ValueError("maximum_wait_ms must be at least debounce_ms")
        self._pending: OrderedDict[str, int] = OrderedDict()
        self._first_pending_at_ms: int | None = None
        self._last_pending_at_ms: int | None = None
        self._in_flight = False

    def offer(self, paragraph_id: Any, *, stable_at_ms: int) -> None:
        normalized_id = _required_text(paragraph_id, "paragraph_id", maximum=200)
        timestamp = _non_negative_integer(stable_at_ms, "stable_at_ms")
        if normalized_id not in self._pending:
            self._pending[normalized_id] = timestamp
            if self._first_pending_at_ms is None:
                self._first_pending_at_ms = timestamp
        else:
            self._pending[normalized_id] = timestamp
        self._last_pending_at_ms = (
            timestamp
            if self._last_pending_at_ms is None
            else max(self._last_pending_at_ms, timestamp)
        )

    def claim_ready(self, *, now_ms: int) -> tuple[str, ...] | None:
        timestamp = _non_negative_integer(now_ms, "now_ms")
        if self._in_flight or not self._pending:
            return None
        if self._first_pending_at_ms is None or self._last_pending_at_ms is None:
            raise RuntimeError("pending batch timestamps are inconsistent")
        ready_at_ms = min(
            self._last_pending_at_ms + self._debounce_ms,
            self._first_pending_at_ms + self._maximum_wait_ms,
        )
        if timestamp < ready_at_ms:
            return None
        claimed = tuple(self._pending)
        self._pending.clear()
        self._first_pending_at_ms = None
        self._last_pending_at_ms = None
        self._in_flight = True
        return claimed

    def complete(self) -> None:
        self._in_flight = False

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def in_flight(self) -> bool:
        return self._in_flight


def _merge_checkpoint_text(existing: str, incoming: str) -> str:
    left = str(existing or "")
    right = str(incoming or "")
    if not left:
        return right
    if not right:
        return left
    maximum_overlap = min(len(left), len(right), 80)
    for size in range(maximum_overlap, 1, -1):
        if left[-size:] == right[:size]:
            return f"{left}{right[size:]}"
    separator = " " if left[-1].isascii() and right[0].isascii() and left[-1].isalnum() and right[0].isalnum() else ""
    return f"{left}{separator}{right}"


def _required_text(value: Any, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    if len(normalized) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return normalized


def _positive_integer(value: Any, field: str) -> int:
    number = _non_negative_integer(value, field)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if number < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return number

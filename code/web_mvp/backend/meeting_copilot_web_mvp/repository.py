from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any

from meeting_copilot_core.session_snapshot import build_session_snapshot


SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
NEGATIVE_CARD_FEEDBACK_STATUSES = {
    "dismissed",
    "marked_wrong",
    "too_late",
    "too_intrusive",
}


class CardStatusTransitionError(Exception):
    pass


@dataclass
class SessionRecord:
    session_id: str
    transcript_report: dict[str, Any]
    analysis: dict[str, Any]
    state_events: list[dict[str, Any]]
    llm_usage: dict[str, Any] | None = None
    degradation_reasons: list[str] = field(default_factory=list)
    card_statuses: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemorySessionRepository:
    def __init__(self) -> None:
        self._records: dict[str, SessionRecord] = {}

    def create(
        self,
        *,
        session_id: str,
        transcript_report: dict[str, Any],
        analysis: dict[str, Any],
        state_events: list[dict[str, Any]],
        llm_usage: dict[str, Any] | None = None,
        degradation_reasons: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = SessionRecord(
            session_id=session_id,
            transcript_report=deepcopy(transcript_report),
            analysis=deepcopy(analysis),
            state_events=deepcopy(state_events),
            llm_usage=deepcopy(llm_usage),
            degradation_reasons=list(degradation_reasons or []),
            metadata=deepcopy(metadata or {}),
        )
        snapshot = self._snapshot_from_record(record)
        self._records[session_id] = record
        return snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        record = self._record(session_id)
        return self._snapshot_from_record(record)

    def metadata(self, session_id: str) -> dict[str, Any]:
        return deepcopy(self._record(session_id).metadata)

    def _snapshot_from_record(self, record: SessionRecord) -> dict[str, Any]:
        return build_session_snapshot(
            session_id=record.session_id,
            transcript_report=record.transcript_report,
            analysis=record.analysis,
            state_events=record.state_events,
            llm_usage=record.llm_usage,
            card_statuses=record.card_statuses,
            degradation_reasons=record.degradation_reasons,
        )

    def set_card_status(self, session_id: str, card_id: str, status: str) -> dict[str, Any]:
        record = self._record(session_id)
        current_snapshot = self._snapshot_from_record(record)
        card_ids = {
            str(card.get("id", ""))
            for card in current_snapshot.get("suggestion_cards", [])
        }
        if card_id not in card_ids:
            raise KeyError(f"card not found: {card_id}")
        card = next(
            card
            for card in current_snapshot.get("suggestion_cards", [])
            if str(card.get("id", "")) == card_id
        )
        if str(card.get("show_or_silence_decision", "show")) != "show":
            raise ValueError(f"silenced suggestion card cannot be updated: {card_id}")
        candidate = SessionRecord(
            session_id=record.session_id,
            transcript_report=deepcopy(record.transcript_report),
            analysis=deepcopy(record.analysis),
            state_events=deepcopy(record.state_events),
            llm_usage=deepcopy(record.llm_usage),
            degradation_reasons=list(record.degradation_reasons),
            card_statuses={**record.card_statuses, card_id: status},
            metadata=deepcopy(record.metadata),
        )
        snapshot = self._snapshot_from_record(candidate)
        _validate_card_status_transition(
            card_id=card_id,
            current_status=str(card.get("status", "new")),
            next_status=status,
        )
        record.card_statuses[card_id] = status
        return snapshot

    def delete(self, session_id: str) -> bool:
        return self._records.pop(session_id, None) is not None

    def exists(self, session_id: str) -> bool:
        return session_id in self._records

    def _record(self, session_id: str) -> SessionRecord:
        try:
            return self._records[session_id]
        except KeyError as exc:
            raise KeyError(f"session not found: {session_id}") from exc


class JsonFileSessionRepository:
    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._sessions_dir = self._data_dir / "sessions"

    def create(
        self,
        *,
        session_id: str,
        transcript_report: dict[str, Any],
        analysis: dict[str, Any],
        state_events: list[dict[str, Any]],
        llm_usage: dict[str, Any] | None = None,
        degradation_reasons: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = SessionRecord(
            session_id=session_id,
            transcript_report=deepcopy(transcript_report),
            analysis=deepcopy(analysis),
            state_events=deepcopy(state_events),
            llm_usage=deepcopy(llm_usage),
            degradation_reasons=list(degradation_reasons or []),
            metadata=deepcopy(metadata or {}),
        )
        snapshot = self._snapshot_from_record(record)
        self._write_record(record)
        return snapshot

    def snapshot(self, session_id: str) -> dict[str, Any]:
        return self._snapshot_from_record(self._record(session_id))

    def metadata(self, session_id: str) -> dict[str, Any]:
        return deepcopy(self._record(session_id).metadata)

    def set_card_status(self, session_id: str, card_id: str, status: str) -> dict[str, Any]:
        record = self._record(session_id)
        current_snapshot = self._snapshot_from_record(record)
        card_ids = {
            str(card.get("id", ""))
            for card in current_snapshot.get("suggestion_cards", [])
        }
        if card_id not in card_ids:
            raise KeyError(f"card not found: {card_id}")
        card = next(
            card
            for card in current_snapshot.get("suggestion_cards", [])
            if str(card.get("id", "")) == card_id
        )
        if str(card.get("show_or_silence_decision", "show")) != "show":
            raise ValueError(f"silenced suggestion card cannot be updated: {card_id}")
        candidate = SessionRecord(
            session_id=record.session_id,
            transcript_report=deepcopy(record.transcript_report),
            analysis=deepcopy(record.analysis),
            state_events=deepcopy(record.state_events),
            llm_usage=deepcopy(record.llm_usage),
            degradation_reasons=list(record.degradation_reasons),
            card_statuses={**record.card_statuses, card_id: status},
            metadata=deepcopy(record.metadata),
        )
        snapshot = self._snapshot_from_record(candidate)
        _validate_card_status_transition(
            card_id=card_id,
            current_status=str(card.get("status", "new")),
            next_status=status,
        )
        self._write_record(candidate)
        return snapshot

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def exists(self, session_id: str) -> bool:
        return self._session_path(session_id).exists()

    def _snapshot_from_record(self, record: SessionRecord) -> dict[str, Any]:
        return build_session_snapshot(
            session_id=record.session_id,
            transcript_report=record.transcript_report,
            analysis=record.analysis,
            state_events=record.state_events,
            llm_usage=record.llm_usage,
            card_statuses=record.card_statuses,
            degradation_reasons=record.degradation_reasons,
        )

    def _record(self, session_id: str) -> SessionRecord:
        path = self._session_path(session_id)
        if not path.exists():
            raise KeyError(f"session not found: {session_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionRecord(
            session_id=str(data["session_id"]),
            transcript_report=deepcopy(data["transcript_report"]),
            analysis=deepcopy(data["analysis"]),
            state_events=deepcopy(data["state_events"]),
            llm_usage=deepcopy(data.get("llm_usage")),
            degradation_reasons=list(data.get("degradation_reasons") or []),
            card_statuses=deepcopy(data.get("card_statuses") or {}),
            metadata=deepcopy(data.get("metadata") or {}),
        )

    def _write_record(self, record: SessionRecord) -> None:
        payload = {
            "session_id": record.session_id,
            "transcript_report": record.transcript_report,
            "analysis": record.analysis,
            "state_events": record.state_events,
            "llm_usage": record.llm_usage,
            "degradation_reasons": record.degradation_reasons,
            "card_statuses": record.card_statuses,
            "metadata": record.metadata,
        }
        path = self._session_path(record.session_id)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _session_path(self, session_id: str) -> Path:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError(f"unsafe session_id: {session_id}")
        return self._sessions_dir / f"{session_id}.json"


def _validate_card_status_transition(
    *,
    card_id: str,
    current_status: str,
    next_status: str,
) -> None:
    if current_status in NEGATIVE_CARD_FEEDBACK_STATUSES and next_status != current_status:
        raise CardStatusTransitionError(
            f"card status transition not allowed: {card_id} {current_status} -> {next_status}"
        )

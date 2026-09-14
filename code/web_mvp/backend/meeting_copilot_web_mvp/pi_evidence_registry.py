from __future__ import annotations

from typing import Any, Mapping

from .v2_persistence import V2Persistence


class PiEvidenceRegistry:
    """One host-owned evaluation's bounded evidence, never a global cache."""

    def __init__(self, persistence: V2Persistence, *, meeting_id: str) -> None:
        self._persistence = persistence
        self._meeting_id = meeting_id
        self._registered: dict[str, dict[str, Any]] = {}
        self._searches = 0

    @staticmethod
    def _evidence(row: Mapping[str, Any]) -> dict[str, Any]:
        evidence = {key: row[key] for key in (
            "segment_id", "revision", "evidence_hash", "normalized_text",
            "source_track", "correction_status", "transcript_seq",
            "started_at_ms", "ended_at_ms",
        )}
        if row.get("evidence_relation") == "neighbor":
            evidence["evidence_relation"] = "neighbor"
        return evidence

    def search(self, query: str, *, limit: int = 6, include_neighbors: bool = False) -> list[dict[str, Any]]:
        if self._searches >= 4:
            raise ValueError("Pi evidence search budget exhausted")
        self._searches += 1
        rows = self._persistence.query_live_coach_evidence(
            self._meeting_id, query=query, limit=limit, include_neighbors=include_neighbors,
        )
        evidence = [self._evidence(row) for row in rows]
        for item in evidence:
            self._registered[item["segment_id"]] = dict(item)
        return evidence

    def read_span(self, segment_id: str, *, before: int = 1, after: int = 1) -> list[dict[str, Any]]:
        """Register and return an exact current segment plus bounded neighbors."""
        if self._searches >= 4:
            raise ValueError("Pi evidence search budget exhausted")
        self._searches += 1
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("segment_id is required")
        rows = self._persistence.read_live_coach_evidence_span(
            self._meeting_id, segment_id=segment_id.strip(), before=before, after=after,
        )
        evidence = [self._evidence(row) for row in rows]
        for item in evidence:
            self._registered[item["segment_id"]] = dict(item)
        return evidence

    def validate(self, quotes: Mapping[str, str]) -> list[dict[str, Any]]:
        """Re-read registered evidence; publication still needs its transaction barrier."""
        if not quotes or len(quotes) > 6:
            raise ValueError("Pi citations require 1 to 6 registered segments")
        for segment_id, quote in quotes.items():
            registered = self._registered.get(segment_id)
            if registered is None or not isinstance(quote, str) or not quote.strip():
                raise ValueError("unregistered or empty Pi evidence")
            if quote not in registered["normalized_text"]:
                raise ValueError("Pi quote is not verbatim")
        rows = self._persistence.query_live_coach_evidence(
            self._meeting_id, segment_ids=tuple(quotes), limit=6,
        )
        current = {row["segment_id"]: self._evidence(row) for row in rows}
        for segment_id in quotes:
            registered = self._registered[segment_id]
            current_item = current.get(segment_id)
            if current_item is not None and registered.get("evidence_relation") == "neighbor":
                current_item["evidence_relation"] = "neighbor"
            if current_item != registered:
                raise ValueError("Pi evidence changed or was removed")
        return [dict(current[segment_id]) for segment_id in quotes]

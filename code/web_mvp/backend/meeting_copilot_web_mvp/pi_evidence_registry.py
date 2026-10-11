from __future__ import annotations

from typing import Any, Mapping
import hashlib
import json

from .v2_persistence import V2Persistence
from .knowledge_context import (
    MAX_COMPLETE_MATERIAL_CHARACTERS, MAX_COMPLETE_MATERIAL_SOURCES, retrieve_meeting_knowledge,
)


class PiEvidenceRegistry:
    """One host-owned evaluation's bounded evidence, never a global cache."""

    def __init__(self, persistence: V2Persistence, *, meeting_id: str, knowledge_repository: Any = None,
                 project_outcomes: Any = None, memory_scene: str = "general") -> None:
        self._persistence = persistence
        self._meeting_id = meeting_id
        self._registered: dict[str, dict[str, Any]] = {}
        self._searches = 0
        self._knowledge = knowledge_repository
        self._documents: dict[str, dict[str, Any]] = {}
        self._document_union_budget: tuple[int, int] | None = None
        self._project_outcomes = project_outcomes
        self._memory_scene = memory_scene

    def read_project_state(self) -> list[dict[str, Any]]:
        if self._searches >= 4:
            raise ValueError("Pi project state tool budget exhausted")
        self._searches += 1
        return self._project_state_snapshot()

    def _project_state_snapshot(self) -> list[dict[str, Any]]:
        if self._persistence.get_meeting(self._meeting_id)["state"] != "live":
            raise ValueError("Pi project state requires a live meeting")
        if self._project_outcomes is None:
            return []
        state = self._project_outcomes.context_for_meeting(self._meeting_id)
        if state.get("project_id") is None:
            return []
        canonical = json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return [{"source_type": "project_state", **state, "snapshot_hash": hashlib.sha256(canonical.encode()).hexdigest()}]

    def validate_project_state(self, state: Mapping[str, Any]) -> dict[str, Any]:
        current = self._project_state_snapshot()
        if len(current) != 1 or dict(state) != current[0]:
            raise ValueError("Pi project state changed before publication")
        return current[0]

    def register_documents(self, evidence, *, max_sources: int = 6, max_characters: int = 3_600) -> list[dict[str, Any]]:
        if self._persistence.get_meeting(self._meeting_id)["state"] != "live":
            raise ValueError("Pi document evidence requires a live meeting")
        if (type(max_sources) is not int or not 1 <= max_sources <= MAX_COMPLETE_MATERIAL_SOURCES
                or type(max_characters) is not int or not 1 <= max_characters <= MAX_COMPLETE_MATERIAL_CHARACTERS):
            raise ValueError("invalid Pi document evidence budget")
        if len(evidence) > max_sources or sum(len(str(item.get("quote") or "")) for item in evidence) > max_characters:
            raise ValueError("Pi document evidence exceeds its bounded context")
        if evidence and self._knowledge is None:
            raise ValueError("Pi document sources are unavailable")
        if max_sources > 6 or max_characters > 3_600:
            # Only the host's explicit participation initial registration can
            # opt in. Subsequent local tool calls retain their smaller batches.
            self._document_union_budget = (max_sources, max_characters)
        accepted = []
        for item in evidence:
            self._knowledge.validate_evidence(item, meeting_id=self._meeting_id)
            prior = self._documents.get(item["source_id"])
            replacement = dict(item)
            if prior is not None and item["quote"] in prior["quote"]:
                self._knowledge.validate_evidence(prior, meeting_id=self._meeting_id)
                replacement = prior  # Searching must not erase an already supplied full quote.
            candidate = {**self._documents, item["source_id"]: replacement}
            if self._document_union_budget is not None:
                source_budget, character_budget = self._document_union_budget
                if len(candidate) > source_budget or sum(len(row["quote"]) for row in candidate.values()) > character_budget:
                    continue  # No undisclosed eviction of previously supplied source evidence.
            self._documents[item["source_id"]] = replacement
            accepted.append(dict(item))
        return accepted

    def search_documents(self, query: str, *, limit: int = 6) -> list[dict[str, Any]]:
        if self._searches >= 4 or type(limit) is not int or not 1 <= limit <= 6:
            raise ValueError("Pi document search budget exhausted")
        self._searches += 1
        if self._knowledge is None:
            return []
        rows = retrieve_meeting_knowledge(self._knowledge, self._meeting_id, query, memory_scene=self._memory_scene,
                                          include_attachments=True)[:limit]
        return self.register_documents(rows)

    def validate_documents(self, refs) -> list[dict[str, Any]]:
        if self._persistence.get_meeting(self._meeting_id)["state"] != "live":
            raise ValueError("Pi document citations require a live meeting")
        if not isinstance(refs, list) or len(refs) > 6:
            raise ValueError("Pi document citations must be bounded")
        result, seen = [], set()
        for ref in refs:
            if not isinstance(ref, Mapping) or set(ref) != {"source_id", "quote"}:
                raise ValueError("Pi document citation must have typed source and quote")
            item = self._documents.get(ref["source_id"])
            quote = ref["quote"]
            if item is None or ref["source_id"] in seen or not isinstance(quote, str) or not quote.strip() or quote not in item["quote"]:
                raise ValueError("Pi document citation is not registered verbatim evidence")
            evidence = {**item, "quote": quote}
            self._knowledge.validate_evidence(evidence, meeting_id=self._meeting_id)
            seen.add(ref["source_id"])
            result.append(evidence)
        return result

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

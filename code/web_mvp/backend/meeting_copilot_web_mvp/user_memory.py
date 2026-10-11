"""Explicit, reversible terminology/style memory. No inference from silence or model calls."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import unicodedata
import uuid
from typing import Any, Mapping

from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from .knowledge_context import source_evidence_state
from .memory_candidates import digest as memory_digest, normalized, semantic_fingerprint

PREFERENCES = {"response_length": {"brief", "standard"}, "suggestion_policy": {"off", "low_frequency", "standard"}}
SCENES = {"", "general", "decision", "project", "interview", "brainstorm"}


class MemoryTermConflict(KnowledgeConflict):
    """Two confirmed corrections cannot give the same spelling different meanings."""

    def __init__(self, memory_id: str) -> None:
        self.memory_id = memory_id
        super().__init__("another confirmed term already uses this spelling")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _public(row) -> dict[str, Any]:
    result = dict(row)
    result["aliases"] = json.loads(result.pop("aliases_json"))
    result["source_refs"] = json.loads(result.pop("source_refs_json"))
    result.pop("fingerprint")
    result["conflicts_with_ids"] = []
    return result


def memory_model_projection(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Bound actual model JSON, preserving all canonical terms before adding aliases.

    Runtime fingerprints and provenance stay with the host and never consume the
    Pi wire budget. An omitted alias is not a reason to omit its canonical term.
    """
    value = value or {}
    preferences = {key: item for key, item in value.get("preferences", {}).items()
                   if key in PREFERENCES and item in PREFERENCES[key]}
    terms = [{"canonical": item["canonical"], "aliases": []} for item in value.get("terms", [])[:40]
             if isinstance(item, Mapping) and isinstance(item.get("canonical"), str) and 0 < len(item["canonical"]) <= 64]
    result = {"terms": terms, "preferences": preferences}

    def size() -> int:
        return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-16-le")) // 2

    while terms and size() > 5500:
        terms.pop()
    source_terms = {item.get("canonical"): item for item in value.get("terms", []) if isinstance(item, Mapping)}
    for term in terms:
        for alias in source_terms[term["canonical"]].get("aliases", [])[:8]:
            if not isinstance(alias, str) or not alias or len(alias) > 64:
                continue
            term["aliases"].append(alias)
            if size() > 5500:
                term["aliases"].pop()
                break
    return result


def memory_context(connection: sqlite3.Connection, meeting_id: str, scene: str = "general") -> dict[str, Any]:
    context = connection.execute("SELECT project_id,context_revision FROM meeting_knowledge_context WHERE meeting_id=?", (meeting_id,)).fetchone()
    project_id = context[0] if context else None
    project = connection.execute("SELECT archived_at_ms FROM knowledge_projects WHERE id=?", (project_id,)).fetchone() if project_id else None
    if project is None or project[0] is not None:
        project_id = None
    rows = connection.execute(
        "SELECT * FROM user_memories WHERE status='active' AND scene IN ('',?) AND "
        "(scope='global' OR (scope='project' AND scope_id=?) OR (scope='meeting' AND scope_id=?)) "
        "ORDER BY CASE scope WHEN 'meeting' THEN 0 WHEN 'project' THEN 1 ELSE 2 END, CASE WHEN scene=? THEN 0 ELSE 1 END,updated_at_ms DESC,id",
        (scene, project_id, meeting_id, scene),
    ).fetchall()
    selected = {}
    for row in rows:
        slot = (row["kind"], row["memory_key"])
        if slot in selected:
            continue
        refs = json.loads(row["source_refs_json"])
        scoped_refs = [{**ref, "context_revision": context[1] if context else 0} if ref.get("source_type") == "document" else ref for ref in refs]
        if refs and source_evidence_state(connection, meeting_id, scoped_refs) != "valid":
            continue
        selected[slot] = row
    hotwords, terms, preferences, revisions = [], [], {}, []
    for row in selected.values():
        revisions.append({"id": row["id"], "revision": row["revision"]})
        if row["kind"] == "preference":
            preferences[row["memory_key"]] = row["value"]
        elif len(terms) < 40:
            aliases = json.loads(row["aliases_json"])
            words = [row["value"]]  # Incorrect spellings are hints, never ASR boost words.
            if sum(map(len, hotwords)) + sum(map(len, words)) > 1600:
                continue
            hotwords.extend(word for word in words if word not in hotwords)
            terms.append({"canonical": row["value"], "aliases": aliases})
    result = {"scene": scene, "terms": terms, "hotwords": hotwords[:50], "preferences": preferences}
    return {**result, "fingerprint": _digest({**result, "revisions": revisions}), "memory_ids": [item["id"] for item in revisions]}


def capture_input_context(connection: sqlite3.Connection, meeting_id: str, scene: str = "general") -> dict[str, Any]:
    from .meeting_materials import material_input_context

    selection = connection.execute("SELECT project_id,context_revision FROM meeting_knowledge_context WHERE meeting_id=?", (meeting_id,)).fetchone()
    project = connection.execute("SELECT revision FROM knowledge_projects WHERE id=?", (selection[0],)).fetchone() if selection and selection[0] else None
    return {"project_id": selection[0] if selection else None, "context_revision": selection[1] if selection else 0,
            "project_revision": project[0] if project else None, "memory_scene": scene,
            "memory_fingerprint": memory_context(connection, meeting_id, scene)["fingerprint"],
            **material_input_context(connection, meeting_id)}


def input_context_state(connection: sqlite3.Connection, meeting_id: str, binding: Mapping[str, Any]) -> str:
    if not binding:
        return "unknown"
    current = capture_input_context(connection, meeting_id, str(binding.get("memory_scene") or "general"))
    return "valid" if dict(binding) == current else "stale"


class UserMemory:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def _scope(self, scope: str, scope_id: str, scene: str) -> None:
        if scene not in SCENES or scope not in {"global", "project", "meeting"}:
            raise ValueError("invalid memory scope")
        if scope == "global" and scope_id != "":
            raise ValueError("global memory has no scope ID")
        if scope == "project":
            self.repository._project(scope_id)
        if scope == "meeting" and self.repository._conn.execute("SELECT 1 FROM meetings WHERE id=?", (scope_id,)).fetchone() is None:
            raise KeyError("meeting not found")

    def _get(self, memory_id: str) -> dict[str, Any]:
        return self.repository._get("user_memories", memory_id)

    def _refs(self, refs: list[dict[str, Any]], scope: str, scope_id: str) -> None:
        if not isinstance(refs, list) or len(refs) > 6:
            raise ValueError("too many memory sources")
        if refs and scope == "global":
            raise ValueError("global explicit styles/terms cannot inherit project sources")
        for ref in refs:
            if isinstance(ref, Mapping) and ref.get("source_type") == "meeting_attachment":
                if scope != "meeting":
                    raise ValueError("meeting attachments cannot become global or project memory")
                from .meeting_materials import validate_attachment_evidence

                validate_attachment_evidence(self.repository._conn, scope_id, ref)
                continue
            if not isinstance(ref, Mapping) or ref.get("source_type") != "document":
                raise ValueError("memory candidates require typed document sources")
            meeting = scope_id if scope == "meeting" else None
            self.repository.validate_evidence(ref, meeting_id=meeting, project_id=scope_id if scope == "project" else None)

    def _notify(self) -> None:
        # Same SQLite transaction; the host's input binding detects changes without caches.
        from . import knowledge_invalidation
        invalidate = getattr(knowledge_invalidation, "invalidate_input_context", None)
        if invalidate is not None:
            now = int(time.time() * 1000)
            for row in self.repository._conn.execute("SELECT id FROM meetings").fetchall():
                invalidate(self.repository._conn, row[0], now)

    def _conflicts(self, item: Mapping[str, Any]) -> list[str]:
        if item["status"] not in {"candidate", "needs_review"}:
            return []
        rows = self.repository._conn.execute(
            "SELECT * FROM user_memories WHERE scope=? AND scope_id=? AND scene=? AND kind=? AND status='active' AND id!=?",
            (item["scope"], item["scope_id"], item["scene"], item["kind"], item["id"]),
        ).fetchall()
        spellings = {normalized(word) for word in [item["value"], *item["aliases"]]}
        return [row["id"] for row in rows if row["memory_key"] == item["memory_key"] or
                item["kind"] == "term" and spellings & {normalized(word) for word in [row["value"], *json.loads(row["aliases_json"])]}]

    def source_excluded(self, value: Mapping[str, Any], refs: list[dict[str, Any]]) -> bool:
        semantic = semantic_fingerprint(value)
        for ref in refs:
            if self.repository._conn.execute(
                "SELECT 1 FROM memory_source_exclusions WHERE semantic_fingerprint=? AND "
                "((document_id=? AND quote_hash=?) OR (document_id='' AND quote_hash=''))",
                (semantic, ref.get("document_id") or "attachment:" + str(ref.get("attachment_id") or ""), memory_digest(normalized(ref["quote"]))),
            ).fetchone():
                return True
        return False

    def create_ai_candidate(self, *, project_id: str, candidate: Mapping[str, Any], job_id: str) -> dict[str, Any] | None:
        """Only a validated background job uses this helper; no active row is edited."""
        fields = {"scope": "project", "scope_id": project_id, "scene": "", **{key: candidate[key] for key in ("kind", "memory_key", "value", "aliases")}}
        refs = list(candidate["source_refs"])
        with self.repository._write():
            self._refs(refs, "project", project_id)
            if self.source_excluded(fields, refs):
                return None
            semantic = semantic_fingerprint(fields)
            rows = self.repository._conn.execute("SELECT * FROM user_memories WHERE scope='project' AND scope_id=? AND scene='' AND kind=? AND status!='deleted'",
                                                 (project_id, fields["kind"])).fetchall()
            for row in rows:
                previous = _public(row)
                if semantic_fingerprint(previous) == semantic:
                    try:
                        self._refs(previous["source_refs"], "project", project_id)
                    except (KeyError, ValueError, KnowledgeScopeError):
                        fields["memory_key"] = previous["memory_key"]
                        continue
                    return {**previous, "conflicts_with_ids": self._conflicts(previous)}
                if fields["kind"] == "term" and normalized(previous["value"]) == normalized(fields["value"]):
                    fields["memory_key"] = previous["memory_key"]
            item = self.create(**fields, source_refs=refs, origin="ai")
            self.repository._conn.execute("UPDATE user_memories SET reconciliation_job_id=? WHERE id=? AND status='candidate' AND origin='ai'",
                                          (job_id, item["id"]))
            item = _public(self._get(item["id"]))
            return {**item, "conflicts_with_ids": self._conflicts(item)}

    def semantic_already_recorded(self, value: Mapping[str, Any]) -> bool:
        """Deterministic duplicate/disabled gate before any model execution."""
        with self.repository._lock:
            rows = self.repository._conn.execute("SELECT * FROM user_memories WHERE scope=? AND scope_id=? AND scene=? AND kind=? AND status!='deleted'",
                (value["scope"], value["scope_id"], value.get("scene", ""), value["kind"])).fetchall()
            semantic = semantic_fingerprint(value)
            for row in rows:
                previous = _public(row)
                if semantic_fingerprint(previous) != semantic:
                    continue
                if previous["status"] == "disabled":
                    return True
                try:
                    self._refs(previous["source_refs"], value["scope"], value["scope_id"])
                except (KeyError, ValueError, KnowledgeScopeError):
                    continue
                return True
            return False

    def list(self, *, scope: str, scope_id: str = "", limit: int = 50, cursor: str | None = None, include_disabled: bool = True) -> dict[str, Any]:
        with self.repository._lock:
            self._scope(scope, scope_id, "")
            if not 1 <= limit <= 200 or (cursor is not None and len(cursor) > 100):
                raise ValueError("invalid page")
            rows = self.repository._conn.execute(
                "SELECT * FROM user_memories WHERE scope=? AND scope_id=? AND status!='deleted' "
                "AND (? OR status IN ('active','candidate','needs_review')) AND (? IS NULL OR id>?) ORDER BY id LIMIT ?",
                (scope, scope_id, include_disabled, cursor, cursor, limit + 1),
            ).fetchall()
            items = []
            for row in rows[:limit]:
                item = _public(row)
                if item["source_refs"]:
                    try:
                        self._refs(item["source_refs"], scope, scope_id)
                    except (KeyError, ValueError, KnowledgeScopeError):
                        item["status"] = "needs_review"
                item["conflicts_with_ids"] = self._conflicts(item)
                items.append(item)
            return {"items": items, "has_more": len(rows) > limit, "next_cursor": rows[limit-1]["id"] if len(rows) > limit else None}

    def create(self, *, scope: str, scope_id: str = "", scene: str = "", kind: str, memory_key: str, value: str,
               aliases: list[str] | None = None, source_refs: list[dict[str, Any]] | None = None, origin: str = "user") -> dict[str, Any]:
        aliases, refs = aliases or [], source_refs or []
        refs = [{key: value for key, value in ref.items() if key != "context_revision"}
                if isinstance(ref, Mapping) and ref.get("source_type") == "document" else ref for ref in refs]
        if kind not in {"term", "preference"} or origin not in {"user", "ai"}:
            raise ValueError("invalid memory kind")
        if not isinstance(value, str) or not value.strip() or len(value) > 64:
            raise ValueError("memory value exceeds limit")
        value, memory_key = value.strip(), _key(memory_key)
        if not memory_key or len(memory_key) > 64 or any(ord(char) < 32 for char in value + memory_key):
            raise ValueError("invalid term")
        if kind == "preference" and (memory_key not in PREFERENCES or value not in PREFERENCES[memory_key] or aliases):
            raise ValueError("unsupported explicit preference")
        if not isinstance(aliases, list) or len(aliases) > 8 or any(not isinstance(word, str) or not word.strip() or len(word) > 64 for word in aliases):
            raise ValueError("invalid aliases")
        aliases = list(dict.fromkeys(word.strip() for word in aliases))
        if origin == "ai" and not refs:
            raise ValueError("AI memory needs verifiable sources")
        with self.repository._write():
            self._scope(scope, scope_id, scene)
            self._refs(refs, scope, scope_id)
            fingerprint = _digest({"scope": scope, "scope_id": scope_id, "scene": scene, "kind": kind, "key": memory_key, "value": value,
                                   "aliases": aliases, "sources": refs})
            if self.repository._conn.execute("SELECT 1 FROM memory_exclusions WHERE fingerprint=?", (fingerprint,)).fetchone():
                raise KnowledgeScopeError("this deleted memory source is excluded; explicitly write a revised value")
            old = self.repository._conn.execute("SELECT * FROM user_memories WHERE fingerprint=? AND status!='deleted'", (fingerprint,)).fetchone()
            if old:
                return _public(old)
            now, memory_id = int(time.time() * 1000), "mem_" + uuid.uuid4().hex
            self.repository._conn.execute(
                "INSERT INTO user_memories(id,scope,scope_id,scene,kind,memory_key,value,aliases_json,status,origin,source_refs_json,fingerprint,created_at_ms,updated_at_ms) "
                "VALUES(?,?,?,?,?,?,?,?,'candidate',?,?,?,?,?)",
                (memory_id, scope, scope_id, scene, kind, memory_key, value, _json(aliases), origin, _json(refs), fingerprint, now, now),
            )
            return _public(self._get(memory_id))

    def update(self, memory_id: str, *, expected_revision: int, status: str | None = None, value: str | None = None,
               aliases: list[str] | None = None, replace_active_id: str | None = None, replace_active_revision: int | None = None) -> dict[str, Any]:
        with self.repository._write():
            row = self._get(memory_id)
            if row["revision"] != expected_revision or row["status"] == "deleted":
                raise KnowledgeConflict("memory changed")
            if status not in {None, "active", "disabled", "deleted"}:
                raise ValueError("invalid memory status")
            if value is not None or aliases is not None:
                candidate = self.create(scope=row["scope"], scope_id=row["scope_id"], scene=row["scene"], kind=row["kind"],
                                        memory_key=row["memory_key"], value=value if value is not None else row["value"],
                                        aliases=aliases if aliases is not None else json.loads(row["aliases_json"]),
                                        source_refs=json.loads(row["source_refs_json"]), origin="user")
                if candidate["id"] != memory_id:
                    return candidate  # Edited values are reviewed before replacing the active slot.
            if status == "active":
                self._scope(row["scope"], row["scope_id"], row["scene"])
                self._refs(json.loads(row["source_refs_json"]), row["scope"], row["scope_id"])
                if row["kind"] == "term":
                    spellings = {_key(word) for word in [row["value"], *json.loads(row["aliases_json"])]}
                    other_terms = self.repository._conn.execute(
                        "SELECT * FROM user_memories WHERE scope=? AND scope_id=? AND scene=? "
                        "AND kind='term' AND status='active' AND memory_key!=? AND id!=?",
                        (row["scope"], row["scope_id"], row["scene"], row["memory_key"], memory_id),
                    ).fetchall()
                    for other in other_terms:
                        if spellings & {_key(word) for word in [other["value"], *json.loads(other["aliases_json"])]}:
                            raise MemoryTermConflict(other["id"])
                current = self.repository._conn.execute("SELECT * FROM user_memories WHERE scope=? AND scope_id=? AND scene=? AND kind=? AND memory_key=? AND status='active' AND id!=?",
                                                       (row["scope"], row["scope_id"], row["scene"], row["kind"], row["memory_key"], memory_id)).fetchone()
                if current:
                    if replace_active_id != current["id"] or replace_active_revision != current["revision"]:
                        raise KnowledgeConflict("another confirmed memory occupies this slot")
                    self.repository._conn.execute("UPDATE user_memories SET status='disabled',revision=revision+1 WHERE id=?", (current["id"],))
            now = int(time.time() * 1000)
            if status == "deleted":
                previous = _public(row)
                semantic = semantic_fingerprint(previous)
                refs = previous["source_refs"]
                for ref in refs or [{"document_id": "", "quote": ""}]:
                    self.repository._conn.execute("INSERT OR IGNORE INTO memory_source_exclusions VALUES(?,?,?,?)",
                        (semantic, ref.get("document_id", "") or ("attachment:" + str(ref["attachment_id"]) if ref.get("attachment_id") else ""), memory_digest(normalized(ref["quote"])) if refs else "", now))
                self.repository._conn.execute("INSERT OR IGNORE INTO memory_exclusions VALUES(?,?)", (row["fingerprint"], now))
                self.repository._conn.execute("UPDATE user_memories SET value='',aliases_json='[]',source_refs_json='[]' WHERE id=?", (memory_id,))
            self.repository._conn.execute("UPDATE user_memories SET status=?,revision=revision+1,updated_at_ms=? WHERE id=?", (status or row["status"], now, memory_id))
            self._notify()
            return _public(self._get(memory_id))

    def resolve(self, meeting_id: str, scene: str = "general") -> dict[str, Any]:
        with self.repository._lock:
            if scene not in SCENES:
                raise ValueError("invalid memory scene")
            if self.repository._conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
                raise KeyError("meeting not found")
            return memory_context(self.repository._conn, meeting_id, scene)

    def preview_preferences(self, project_id: str | None = None, scene: str = "general") -> dict[str, Any]:
        with self.repository._lock:
            if scene not in SCENES:
                raise ValueError("invalid memory scene")
            if project_id:
                self.repository._project(project_id)
            rows = self.repository._conn.execute(
                "SELECT * FROM user_memories WHERE status='active' AND kind='preference' AND scene IN ('',?) "
                "AND (scope='global' OR (scope='project' AND scope_id=?)) "
                "ORDER BY CASE scope WHEN 'project' THEN 0 ELSE 1 END,CASE WHEN scene=? THEN 0 ELSE 1 END,updated_at_ms DESC,id",
                (scene, project_id, scene),
            ).fetchall()
            preferences, sources = {}, []
            for row in rows:
                if row["memory_key"] in preferences:
                    continue
                try:
                    self._refs(json.loads(row["source_refs_json"]), row["scope"], row["scope_id"])
                except (ValueError, KeyError, KnowledgeScopeError):
                    continue
                preferences[row["memory_key"]] = row["value"]
                sources.append({"id": row["id"], "revision": row["revision"], "scope": row["scope"], "scene": row["scene"]})
            return {"project_id": project_id, "scene": scene, "preferences": preferences, "sources": sources}

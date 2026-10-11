"""Version checks for copied local sources. Pure SQL helpers, independent of repositories."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any
import uuid


_REVIEW_SOURCE_LABELS = {
    "minutes": "会议纪要",
    "decisions": "决策",
    "action_items": "待办",
    "risks": "风险",
    "transcript": "会议文字",
}


def _review_source_title(document_kind: str) -> str:
    label = _REVIEW_SOURCE_LABELS.get(document_kind)
    return f"会议终稿·{label}" if label else "会议终稿"


def _hash(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def source_snapshot(connection: sqlite3.Connection, source_kind: str, source_id: str) -> dict[str, Any]:
    if source_kind == "review_document":
        row = connection.execute("SELECT meeting_id,document_kind,user_version,user_content_json,user_modified,dirty_state "
                                 "FROM review_documents WHERE document_id=?", (source_id,)).fetchone()
        if row is None:
            raise ValueError("source_deleted")
        if row[4] != 1 or row[3] is None or row[5] != "saved":
            raise ValueError("source_needs_review")
        content = json.loads(row[3])
        text = content if isinstance(content, str) else content.get("markdown") if isinstance(content, dict) else None
        if not isinstance(text, str):
            text = json.dumps(content, ensure_ascii=False, indent=2)
        return {"source_kind": source_kind, "source_id": source_id, "source_meeting_id": row[0], "source_version": row[2],
                "source_hash": _hash(content), "title": _review_source_title(row[1]), "content": text,
                "document_kind": row[1], "source_content": content}
    if source_kind == "note":
        row = connection.execute("SELECT meeting_id,title,body,version,status,source_kind,source_message_id FROM meeting_notes WHERE note_id=?",
                                 (source_id,)).fetchone()
        if row is None or row[4] == "deleted":
            raise ValueError("source_deleted")
        if row[4] != "active":
            raise ValueError("source_needs_review")
        return {"source_kind": source_kind, "source_id": source_id, "source_meeting_id": row[0], "source_version": row[3],
                "source_hash": _hash({"title": row[1], "body": row[2], "source_kind": row[5], "source_message_id": row[6]}),
                "title": row[1], "content": row[2], "note_source_kind": row[5]}
    raise ValueError("unsupported_source")


def library_source_status(connection: sqlite3.Connection, document_id: str, version_id: str | None) -> str:
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_source_bindings'").fetchone() is None:
        return "current"
    binding = connection.execute("SELECT source_kind,source_id FROM knowledge_source_bindings WHERE document_id=?", (document_id,)).fetchone()
    if binding is None:
        return "current"
    version = connection.execute("SELECT source_kind,source_id,source_version,source_hash FROM knowledge_source_versions "
                                 "WHERE library_version_id=? AND document_id=?", (version_id, document_id)).fetchone()
    if version is None or tuple(version[:2]) != tuple(binding):
        return "needs_review"
    try:
        current = source_snapshot(connection, binding[0], binding[1])
    except (ValueError, TypeError, json.JSONDecodeError):
        return "deleted" if connection.execute(
            "SELECT 1 FROM review_documents WHERE document_id=?" if binding[0] == "review_document" else
            "SELECT 1 FROM meeting_notes WHERE note_id=? AND status!='deleted'", (binding[1],),
        ).fetchone() is None else "needs_review"
    return "current" if version[2] == current["source_version"] and version[3] == current["source_hash"] else "needs_review"


def library_source_is_current(connection: sqlite3.Connection, document_id: str, version_id: str | None) -> bool:
    return library_source_status(connection, document_id, version_id) == "current"


def inherit_library_source(connection: sqlite3.Connection, document_id: str, version_id: str, base_version_id: str | None) -> None:
    """User edits inherit the chosen source snapshot; never stamp an old draft with a new source."""
    if base_version_id is None or connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_source_versions'").fetchone() is None:
        return
    connection.execute("INSERT INTO knowledge_source_versions(library_version_id,document_id,source_kind,source_id,source_version,source_hash,created_at_ms) "
                       "SELECT ?,document_id,source_kind,source_id,source_version,source_hash,created_at_ms FROM knowledge_source_versions "
                       "WHERE library_version_id=? AND document_id=?", (version_id, base_version_id, document_id))


def invalidate_bound_sources(connection: sqlite3.Connection, source_kind: str, source_id: str, now_ms: int,
                             *, source_meeting_id: str | None = None) -> None:
    """Withdraw current output inside the original source's mutation transaction."""
    from .knowledge_invalidation import invalidate_meeting_sources

    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_source_bindings'").fetchone() is None:
        return
    condition = "((json_extract(ref.value,'$.source_type')=? AND json_extract(ref.value,'$.source_id')=? " \
                "AND (? IS NULL OR json_extract(ref.value,'$.meeting_id')=?)) OR " \
                "(json_extract(ref.value,'$.source_type')='document' AND json_extract(ref.value,'$.document_id') IN " \
                "(SELECT document_id FROM knowledge_source_bindings WHERE source_kind=? AND source_id=?)))"
    parameters = (source_kind, source_id, source_meeting_id, source_meeting_id, source_kind, source_id)
    changed = connection.execute("SELECT DISTINCT i.* FROM project_state_items i,json_each(i.source_refs_json) ref "
                                 "WHERE i.status IN ('active','unknown') AND " + condition, parameters).fetchall()
    projects = {row["project_id"] for row in changed}
    for row in changed:
        snapshot = dict(row)
        snapshot["source_refs"] = json.loads(snapshot.pop("source_refs_json"))
        snapshot.update(status="needs_review", revision=row["revision"] + 1, updated_at_ms=now_ms)
        connection.execute("UPDATE project_state_items SET status='needs_review',revision=revision+1,updated_at_ms=? WHERE id=?", (now_ms, row["id"]))
        connection.execute("INSERT INTO project_state_history(id,project_id,item_id,revision,snapshot_json,reason,created_at_ms) VALUES(?,?,?,?,?,'source_invalidated',?)",
                           (f"state_history_{uuid.uuid4().hex}", row["project_id"], row["id"], snapshot["revision"],
                            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")), now_ms))
    proposals = connection.execute("SELECT DISTINCT p.id FROM project_change_proposals p,json_each(p.changes_json) change,"
                                   "json_each(change.value,'$.source_refs') ref WHERE p.status='pending' AND " + condition, parameters).fetchall()
    for row in proposals:
        connection.execute("UPDATE project_change_proposals SET status='needs_review',revision=revision+1,updated_at_ms=? WHERE id=?", (now_ms, row[0]))
    for project_id in projects:
        connection.execute("UPDATE knowledge_projects SET revision=revision+1,updated_at_ms=? WHERE id=?", (now_ms, project_id))
    projects.update(row[0] for row in connection.execute("SELECT project_id FROM knowledge_source_bindings WHERE source_kind=? AND source_id=?",
                                                        (source_kind, source_id)).fetchall())
    for project_id in projects:
        for row in connection.execute("SELECT meeting_id FROM meeting_knowledge_context WHERE project_id=?", (project_id,)).fetchall():
            invalidate_meeting_sources(connection, row[0], now_ms)

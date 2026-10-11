"""Persistent, versioned material sets with explicit meeting-local activation.

The caller's SQLite transaction owns binding, invalidation and deletion. Import
jobs depend on content revisions, never the set's meeting binding revision.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Mapping
import uuid

from .document_import import MAX_UPLOAD_BYTES, document_structure, validate_upload
from .knowledge_repository import (
    KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError, MAX_CONTENT_CHARACTERS,
    MAX_SPANS, MAX_SPAN_CHARACTERS, _tokens, content_spans,
)
from .storage_governance import ensure_private_directory, harden_private_file


def _now() -> int:
    return time.time_ns() // 1_000_000


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _linked(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _set(connection: sqlite3.Connection, set_id: str, *, allow_deleted: bool = False) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM material_sets WHERE id=?", (set_id,)).fetchone()
    if row is None:
        raise KeyError("material set not found")
    result = dict(row)
    if result["state"] == "deleted" and not allow_deleted:
        raise KnowledgeScopeError("material set was deleted")
    if result["state"] == "bound":
        if connection.execute("SELECT 1 FROM meetings WHERE id=?", (result["meeting_id"],)).fetchone() is None:
            raise KnowledgeScopeError("bound meeting is unavailable")
        if connection.execute("SELECT 1 FROM meeting_tombstones WHERE meeting_id=?", (result["meeting_id"],)).fetchone():
            raise KnowledgeScopeError("bound meeting was deleted")
    return result


def _scope_event(connection: sqlite3.Connection, material_set: Mapping[str, Any], now_ms: int) -> None:
    meeting_id = material_set.get("meeting_id")
    if not meeting_id:
        return
    from .knowledge_invalidation import _append, invalidate_meeting_sources

    _append(connection, meeting_id, "meeting.materials.updated", material_set["id"], now_ms,
            f"materials.scope:{material_set['id']}:{material_set['context_revision']}",
            {"material_set_id": material_set["id"], "context_revision": material_set["context_revision"],
             "state": material_set["state"]})
    invalidate_meeting_sources(connection, meeting_id, now_ms)


def bind_material_set(connection: sqlite3.Connection, set_id: str, meeting_id: str,
                      expected_revision: int | None = None, now_ms: int | None = None) -> dict[str, Any]:
    """Atomic create+bind primitive: caller MUST hold the meeting writer transaction."""
    if not connection.in_transaction:
        raise RuntimeError("binding materials requires the caller's write transaction")
    material_set = _set(connection, set_id)
    if connection.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
        raise KeyError("meeting not found")
    if connection.execute("SELECT 1 FROM meeting_tombstones WHERE meeting_id=?", (meeting_id,)).fetchone():
        raise KnowledgeScopeError("meeting was deleted")
    if material_set["state"] == "bound":
        if material_set["meeting_id"] != meeting_id:
            raise KnowledgeScopeError("material set is already bound to another meeting")
        return material_set  # Lost-response replay precedes old expected revision.
    other = connection.execute("SELECT id FROM material_sets WHERE meeting_id=? AND state='bound'", (meeting_id,)).fetchone()
    if other is not None and other[0] != set_id:
        raise KnowledgeScopeError("meeting already has another material set")
    if expected_revision is not None and material_set["revision"] != expected_revision:
        raise KnowledgeConflict("material set revision changed")
    timestamp = _now() if now_ms is None else now_ms
    connection.execute("UPDATE material_sets SET state='bound',meeting_id=?,revision=revision+1,"
                       "context_revision=context_revision+1,updated_at_ms=? WHERE id=?", (meeting_id, timestamp, set_id))
    material_set = _set(connection, set_id)
    _scope_event(connection, material_set, timestamp)
    return material_set


def material_input_context(connection: sqlite3.Connection, meeting_id: str) -> dict[str, Any]:
    # During older-schema bootstrap there is intentionally no attachment context.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_sets'").fetchone():
        return {}
    row = connection.execute("SELECT id,context_revision FROM material_sets WHERE meeting_id=? AND state='bound'", (meeting_id,)).fetchone()
    return {"material_set_id": row["id"], "material_context_revision": row["context_revision"]} if row else {}


def material_context_snapshot(connection: sqlite3.Connection, meeting_id: str, *,
                              provided_evidence=()) -> dict[str, Any]:
    """Small source catalog and actual coverage, never an invented document summary."""
    binding = material_input_context(connection, meeting_id)
    if not binding:
        return {}
    _set(connection, binding["material_set_id"])
    rows = connection.execute("SELECT a.*,v.content_hash,v.structure_json,v.warnings_json FROM meeting_attachments a "
                              "JOIN meeting_attachment_versions v ON v.id=a.active_version_id WHERE a.set_id=? "
                              "AND a.enabled=1 AND a.status='active' ORDER BY a.created_at_ms DESC,a.id LIMIT 7",
                              (binding["material_set_id"],)).fetchall()
    catalog = []
    for row in rows[:6]:
        authorized = connection.execute("SELECT s.id,LENGTH(s.text) AS characters FROM meeting_attachment_spans s WHERE s.version_id=? "
                                        "AND ( ? IS NULL OR EXISTS(SELECT 1 FROM json_each(?) WHERE value=s.id))",
                                        (row["active_version_id"], row["selected_span_ids_json"], row["selected_span_ids_json"])).fetchall()
        authorized_ids = {span["id"] for span in authorized}
        provided = {}
        for evidence in list(provided_evidence)[:12]:
            if evidence.get("attachment_id") == row["id"] and evidence.get("source_id") in authorized_ids:
                if attachment_source_state(connection, meeting_id, evidence) == "valid":
                    provided[evidence["source_id"]] = evidence
        outline = json.loads(row["structure_json"])
        sections = []
        relevant_sections = [section for section in outline.get("sections", [])
                             if authorized_ids.intersection(section.get("span_ids") or [])]
        for section in relevant_sections[:3]:
            sections.append({"id": section["id"], "title": section["title"][:100], "locator": section.get("locator") or {},
                             "authorized_span_count": len(authorized_ids.intersection(section.get("span_ids") or []))})
        authorized_characters = sum(span["characters"] for span in authorized)
        provided_characters = sum(len(item["quote"]) for item in provided.values())
        catalog.append({"attachment_id": row["id"], "title": row["title"][:100], "purpose": row["purpose"],
                        "version_id": row["active_version_id"], "content_hash": row["content_hash"],
                        "format": outline.get("format"), "sections": sections,
                        "omitted_section_count": max(0, len(relevant_sections) - len(sections)),
                        "warnings": json.loads(row["warnings_json"]),
                        "coverage": {"authorized_span_count": len(authorized), "authorized_characters": authorized_characters,
                                     "provided_span_count": len(provided), "provided_characters": provided_characters,
                                     "complete_read": len(provided) == len(authorized) and provided_characters == authorized_characters,
                                     "selection": "selected_spans" if row["selected_span_ids_json"] is not None else "whole_document"}})
    return {**binding, "attachment_catalog": catalog, "catalog_truncated": len(rows) > 6,
            "catalog_is_evidence": False}


def _source(connection: sqlite3.Connection, meeting_id: str, source_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT s.id AS source_id,s.text AS quote,s.locator_json,s.ordinal,v.id AS version_id,v.number AS version,"
        "v.content_hash,a.id AS attachment_id,a.title,a.set_id,a.enabled,a.status,a.active_version_id,"
        "a.selected_span_ids_json,m.meeting_id,m.state AS set_state,m.context_revision "
        "FROM meeting_attachment_spans s JOIN meeting_attachment_versions v ON v.id=s.version_id "
        "JOIN meeting_attachments a ON a.id=v.attachment_id JOIN material_sets m ON m.id=a.set_id "
        "WHERE s.id=? AND m.meeting_id=?", (source_id, meeting_id),
    ).fetchone()
    if row is None:
        raise KeyError("attachment source not found in meeting")
    return dict(row)


def validate_attachment_evidence(connection: sqlite3.Connection, meeting_id: str,
                                 evidence: Mapping[str, Any]) -> dict[str, Any]:
    if evidence.get("source_type") != "meeting_attachment" or evidence.get("meeting_id") != meeting_id:
        raise KnowledgeScopeError("attachment evidence requires its bound meeting")
    row = _source(connection, meeting_id, str(evidence.get("source_id") or ""))
    material_set = _set(connection, row["set_id"])
    selected = json.loads(row["selected_span_ids_json"]) if row["selected_span_ids_json"] is not None else None
    quote = evidence.get("quote")
    if (material_set["state"] != "bound" or row["status"] != "active" or not row["enabled"]
            or row["active_version_id"] != row["version_id"]
            or (selected is not None and row["source_id"] not in selected)
            or evidence.get("set_id") != row["set_id"] or evidence.get("attachment_id") != row["attachment_id"]
            or evidence.get("version_id") != row["version_id"] or evidence.get("version") != row["version"]
            or evidence.get("content_hash") != row["content_hash"]
            or evidence.get("context_revision") != row["context_revision"]
            or evidence.get("locator") != json.loads(row["locator_json"])
            or not isinstance(quote, str) or not quote.strip() or quote not in row["quote"]):
        raise KnowledgeScopeError("attachment evidence is outside current version or scope")
    return {**dict(evidence), "title": row["title"], "ordinal": row["ordinal"]}


def attachment_source_state(connection: sqlite3.Connection, meeting_id: str, evidence: Mapping[str, Any]) -> str:
    try:
        validate_attachment_evidence(connection, meeting_id, evidence)
        return "valid"
    except (KeyError, KnowledgeScopeError):
        return "stale"


def read_attachment_source(connection: sqlite3.Connection, meeting_id: str, source_id: str, *,
                           allow_historical: bool = False) -> dict[str, Any]:
    # A deleted meeting never reveals its old set, even for historical reads.
    if connection.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
        raise KeyError("meeting not found")
    if connection.execute("SELECT 1 FROM meeting_tombstones WHERE meeting_id=?", (meeting_id,)).fetchone():
        raise KnowledgeScopeError("meeting was deleted")
    row = _source(connection, meeting_id, source_id)
    evidence = {key: row[key] for key in ("source_id", "set_id", "meeting_id", "attachment_id", "title", "version_id",
                                         "version", "content_hash", "quote", "context_revision", "ordinal")}
    evidence.update(source_type="meeting_attachment", locator=json.loads(row["locator_json"]))
    state = attachment_source_state(connection, meeting_id, evidence)
    if not allow_historical:
        return validate_attachment_evidence(connection, meeting_id, evidence)
    if row["status"] == "deleted" or row["set_state"] == "deleted":
        evidence.update(quote="", title="已删除的本场资料", source_state="deleted", stale_reason="attachment_deleted")
    else:
        evidence.update(source_state=state, stale_reason=None if state == "valid" else "attachment_scope_or_version_changed")
    return evidence


def delete_meeting_materials(connection: sqlite3.Connection, meeting_id: str, now_ms: int) -> None:
    """Meeting all-purge hook; owner stays tombstoned, never becomes a draft."""
    if not connection.in_transaction:
        raise RuntimeError("material deletion requires a write transaction")
    for row in connection.execute("SELECT id FROM material_sets WHERE meeting_id=? AND state='bound'", (meeting_id,)).fetchall():
        _delete_set(connection, row[0], now_ms)


def _enqueue(connection: sqlite3.Connection, attachment_id: str, job_type: str, now_ms: int) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM meeting_attachment_jobs WHERE attachment_id=? AND type=? "
                             "AND status IN ('queued','running')", (attachment_id, job_type)).fetchone()
    if row is not None:
        return dict(row)
    attachment = connection.execute("SELECT * FROM meeting_attachments WHERE id=?", (attachment_id,)).fetchone()
    if attachment is None:
        raise KeyError("attachment not found")
    job_id = _id("material_job")
    connection.execute("INSERT INTO meeting_attachment_jobs(id,set_id,attachment_id,type,input_revision,input_sha256,"
                       "created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,?,?)",
                       (job_id, attachment["set_id"], attachment_id, job_type, attachment["content_revision"],
                        attachment["original_sha256"], now_ms, now_ms))
    return dict(connection.execute("SELECT * FROM meeting_attachment_jobs WHERE id=?", (job_id,)).fetchone())


def _delete_attachment(connection: sqlite3.Connection, attachment_id: str, now_ms: int) -> dict[str, Any]:
    connection.execute("UPDATE meeting_attachments SET status='deleted',enabled=0,revision=revision+1,"
                       "content_revision=content_revision+1,deleted_at_ms=?,updated_at_ms=? WHERE id=? AND status!='deleted'",
                       (now_ms, now_ms, attachment_id))
    connection.execute("DELETE FROM meeting_attachment_search WHERE attachment_id=?", (attachment_id,))
    connection.execute("UPDATE meeting_attachment_jobs SET status='cancelled',progress='cancelled',error_code='document_deleted',"
                       "lease_token=NULL,lease_until_ms=NULL,updated_at_ms=? WHERE attachment_id=? AND type='import' "
                       "AND status IN ('queued','running')", (now_ms, attachment_id))
    previous = connection.execute("SELECT * FROM meeting_attachment_jobs WHERE attachment_id=? AND type='cleanup' "
                                  "ORDER BY created_at_ms DESC,id DESC LIMIT 1", (attachment_id,)).fetchone()
    return dict(previous) if previous is not None else _enqueue(connection, attachment_id, "cleanup", now_ms)


def _delete_set(connection: sqlite3.Connection, set_id: str, now_ms: int) -> list[dict[str, Any]]:
    jobs = [_delete_attachment(connection, row[0], now_ms) for row in connection.execute(
        "SELECT id FROM meeting_attachments WHERE set_id=?", (set_id,)).fetchall()]
    connection.execute("UPDATE material_sets SET state='deleted',revision=revision+1,context_revision=context_revision+1,"
                       "deleted_at_ms=?,updated_at_ms=? WHERE id=? AND state!='deleted'", (now_ms, now_ms, set_id))
    return jobs


class MeetingMaterialsRepository:
    """Reuse the knowledge repository's connection/lock/transactions, never migrate here."""
    def __init__(self, library: KnowledgeRepository) -> None:
        self.library = library
        self._conn, self._lock = library._conn, library._lock
        self._write = library._write

    def _attachment(self, set_id: str, attachment_id: str, *, allow_deleted_set: bool = False) -> dict[str, Any]:
        _set(self._conn, set_id, allow_deleted=allow_deleted_set)
        row = self._conn.execute("SELECT * FROM meeting_attachments WHERE id=? AND set_id=?", (attachment_id, set_id)).fetchone()
        if row is None:
            raise KeyError("attachment not found in material set")
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        selected_json = result.pop("selected_span_ids_json")
        result["selected_span_ids"] = json.loads(selected_json) if selected_json is not None else None
        latest = self._conn.execute("SELECT id FROM meeting_attachment_versions WHERE attachment_id=? ORDER BY number DESC LIMIT 1", (attachment_id,)).fetchone()
        result["latest_version_id"] = latest[0] if latest else None
        return result

    def get_document(self, attachment_id: str) -> dict[str, Any]:
        """Private adapter for the existing isolated import runner."""
        with self._lock:
            row = self._conn.execute("SELECT set_id FROM meeting_attachments WHERE id=?", (attachment_id,)).fetchone()
            if row is None:
                raise KeyError("attachment not found")
            return self._attachment(row[0], attachment_id, allow_deleted_set=True)

    def create_set(self) -> dict[str, Any]:
        with self._write():
            now, set_id = _now(), _id("material_set")
            self._conn.execute("INSERT INTO material_sets(id,created_at_ms,updated_at_ms) VALUES(?,?,?)", (set_id, now, now))
            return _set(self._conn, set_id)

    def list_sets(self, *, limit: int = 50, before_id: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("invalid page limit")
        with self._lock:
            clauses, params = ["state='draft'"], []
            if before_id:
                old = _set(self._conn, before_id)
                clauses.append("(created_at_ms<? OR (created_at_ms=? AND id<?))")
                params.extend((old["created_at_ms"], old["created_at_ms"], old["id"]))
            rows = self._conn.execute("SELECT * FROM material_sets WHERE " + " AND ".join(clauses) +
                                     " ORDER BY created_at_ms DESC,id DESC LIMIT ?", (*params, limit + 1)).fetchall()
            items = [dict(row) for row in rows[:limit]]
            return {"items": items, "has_more": len(rows) > limit, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def get_set(self, set_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        with self._lock:
            return _set(self._conn, set_id, allow_deleted=include_deleted)

    def bind(self, meeting_id: str, set_id: str | None = None, *, expected_revision: int | None = None) -> dict[str, Any]:
        with self._write():
            row = self._conn.execute("SELECT id FROM material_sets WHERE meeting_id=? AND state='bound'", (meeting_id,)).fetchone()
            if set_id is None:
                set_id = row[0] if row else self.create_set()["id"]
            return bind_material_set(self._conn, set_id, meeting_id, expected_revision)

    def list_attachments(self, set_id: str, *, limit: int = 100, before_id: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("invalid page limit")
        with self._lock:
            _set(self._conn, set_id)
            clauses, params = ["set_id=?", "status!='deleted'"], [set_id]
            if before_id:
                old = self._attachment(set_id, before_id)
                clauses.append("(created_at_ms<? OR (created_at_ms=? AND id<?))")
                params.extend((old["created_at_ms"], old["created_at_ms"], old["id"]))
            rows = self._conn.execute("SELECT id FROM meeting_attachments WHERE " + " AND ".join(clauses) +
                                     " ORDER BY created_at_ms DESC,id DESC LIMIT ?", (*params, limit + 1)).fetchall()
            items = [self._attachment(set_id, row[0]) for row in rows[:limit]]
            return {"items": items, "has_more": len(rows) > limit, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def get_attachment(self, set_id: str, attachment_id: str) -> dict[str, Any]:
        with self._lock:
            return self._attachment(set_id, attachment_id)

    def _version(self, set_id: str, attachment_id: str, version_id: str) -> dict[str, Any]:
        self._attachment(set_id, attachment_id)
        row = self._conn.execute("SELECT * FROM meeting_attachment_versions WHERE id=? AND attachment_id=?", (version_id, attachment_id)).fetchone()
        if row is None:
            raise KeyError("attachment version not found")
        version = dict(row)
        version["structure"] = json.loads(version.pop("structure_json"))
        version["warnings"] = json.loads(version.pop("warnings_json"))
        return version

    def get_version(self, set_id: str, attachment_id: str, version_id: str) -> dict[str, Any]:
        with self._lock:
            result = self._version(set_id, attachment_id, version_id)
            result.pop("content")
            return result

    def spans(self, set_id: str, attachment_id: str, version_id: str, *, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        if not 0 <= offset <= MAX_SPANS or not 1 <= limit <= 100:
            raise ValueError("invalid span page")
        with self._lock:
            version = self.get_version(set_id, attachment_id, version_id)
            rows = self._conn.execute("SELECT * FROM meeting_attachment_spans WHERE version_id=? AND ordinal>=? "
                                     "ORDER BY ordinal LIMIT ?", (version_id, offset, limit + 1)).fetchall()
            items = [{**dict(row), "locator": json.loads(row["locator_json"])} for row in rows[:limit]]
            for item in items:
                item.pop("locator_json")
            return {"items": items, "has_more": len(rows) > limit,
                    "next_offset": items[-1]["ordinal"] + 1 if len(rows) > limit else None, "version": version}

    def add_version(self, set_id: str, attachment_id: str, content: str, *, expected_revision: int | None = None,
                    base_version_id: str | None = None, spans=None, origin: str = "user", structure=None, warnings=()) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT_CHARACTERS or origin not in {"user", "extracted"}:
            raise ValueError("invalid attachment content")
        spans = list(content_spans(content) if spans is None else spans)
        if not 0 < len(spans) <= MAX_SPANS:
            raise ValueError("invalid attachment spans")
        for span in spans:
            if (not isinstance(span, Mapping) or not isinstance(span.get("text"), str) or not span["text"].strip()
                    or len(span["text"]) > MAX_SPAN_CHARACTERS or span["text"] not in content
                    or not isinstance(span.get("locator"), dict) or len(_json(span["locator"])) > 800):
                raise ValueError("invalid source span")
        with self._write():
            attachment = self._attachment(set_id, attachment_id)
            if attachment["status"] == "deleted":
                raise KnowledgeScopeError("attachment was deleted")
            if expected_revision is not None and expected_revision != attachment["revision"]:
                raise KnowledgeConflict("attachment revision changed")
            if base_version_id is not None and base_version_id != attachment["latest_version_id"]:
                raise KnowledgeConflict("base version changed")
            now, version_id = _now(), _id("attachment_version")
            number = self._conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM meeting_attachment_versions WHERE attachment_id=?", (attachment_id,)).fetchone()[0]
            span_ids = [_id("material_span") for _ in spans]
            outline = dict(structure or document_structure(spans, "txt" if origin == "user" else Path(attachment["filename"]).suffix[1:]))
            outline["span_count"] = len(spans)
            sections = []
            for index, section in enumerate(outline.get("sections", [])[:MAX_SPANS]):
                ordinals = section.get("span_ordinals", [])
                sections.append({"id": f"section:{version_id}:{index}", "title": str(section.get("title") or "正文")[:200],
                                 "span_ids": [span_ids[o] for o in ordinals if isinstance(o, int) and 0 <= o < len(span_ids)],
                                 "locator": section.get("locator") or {}})
            outline["sections"] = sections
            self._conn.execute("INSERT INTO meeting_attachment_versions(id,attachment_id,number,content,content_hash,"
                               "content_characters,origin,structure_json,warnings_json,created_at_ms) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (version_id, attachment_id, number, content, hashlib.sha256(content.encode()).hexdigest(), len(content),
                                origin, _json(outline), _json(list(warnings)), now))
            for ordinal, span in enumerate(spans):
                self._conn.execute("INSERT INTO meeting_attachment_spans(id,version_id,ordinal,text,locator_json) VALUES(?,?,?,?,?)",
                                   (span_ids[ordinal], version_id, ordinal, span["text"], _json(span["locator"])))
                self._conn.execute("INSERT INTO meeting_attachment_search(span_id,attachment_id,version_id,tokens) VALUES(?,?,?,?)",
                                   (span_ids[ordinal], attachment_id, version_id, " ".join(_tokens(span["text"]))))
            self._conn.execute("UPDATE meeting_attachments SET revision=revision+1,content_revision=content_revision+1,updated_at_ms=? WHERE id=?", (now, attachment_id))
            # Saving a new version deliberately leaves the enabled v1 unchanged.
            return self.get_version(set_id, attachment_id, version_id)

    def usage(self, set_id: str, attachment_id: str, *, enabled: bool, expected_revision: int,
              version_id: str | None = None, selected_span_ids: list[str] | None = None) -> dict[str, Any]:
        if type(enabled) is not bool or (selected_span_ids is not None and (
            not isinstance(selected_span_ids, list) or len(selected_span_ids) > MAX_SPANS or len(set(selected_span_ids)) != len(selected_span_ids))):
            raise ValueError("invalid attachment usage")
        with self._write():
            attachment = self._attachment(set_id, attachment_id)
            if attachment["status"] == "deleted":
                raise KnowledgeScopeError("attachment was deleted")
            if expected_revision != attachment["revision"]:
                raise KnowledgeConflict("attachment revision changed")
            selected_version = version_id or attachment["active_version_id"]
            if enabled:
                if selected_version is None:
                    raise KnowledgeScopeError("attachment has no readable version")
                self._version(set_id, attachment_id, selected_version)
                all_ids = {row[0] for row in self._conn.execute("SELECT id FROM meeting_attachment_spans WHERE version_id=? AND text!=''", (selected_version,))}
                if not all_ids or (selected_span_ids is not None and (not selected_span_ids or not set(selected_span_ids) <= all_ids)):
                    raise KnowledgeScopeError("selected spans are outside attachment version")
            else:
                selected_version = attachment["active_version_id"]
                selected_span_ids = attachment["selected_span_ids"]
            if attachment["enabled"] == enabled and attachment["active_version_id"] == selected_version and attachment["selected_span_ids"] == selected_span_ids:
                return attachment
            now = _now()
            self._conn.execute("UPDATE meeting_attachments SET enabled=?,active_version_id=?,selected_span_ids_json=?,"
                               "revision=revision+1,updated_at_ms=? WHERE id=?", (int(enabled), selected_version,
                               _json(selected_span_ids) if selected_span_ids is not None else None, now, attachment_id))
            self._conn.execute("UPDATE material_sets SET revision=revision+1,context_revision=context_revision+1,updated_at_ms=? WHERE id=?", (now, set_id))
            _scope_event(self._conn, _set(self._conn, set_id), now)
            return self._attachment(set_id, attachment_id)

    def delete_attachment(self, set_id: str, attachment_id: str, *, expected_revision: int) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._write():
            attachment = self._attachment(set_id, attachment_id)
            if attachment["status"] != "deleted" and expected_revision != attachment["revision"]:
                raise KnowledgeConflict("attachment revision changed")
            already = attachment["status"] == "deleted"
            now = _now()
            job = _delete_attachment(self._conn, attachment_id, now)
            if not already:
                self._conn.execute("UPDATE material_sets SET revision=revision+1,context_revision=context_revision+1,updated_at_ms=? WHERE id=?", (now, set_id))
                _scope_event(self._conn, _set(self._conn, set_id), now)
            return self._attachment(set_id, attachment_id), job

    def delete_draft(self, set_id: str, *, expected_revision: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        with self._write():
            material_set = _set(self._conn, set_id, allow_deleted=True)
            if material_set["state"] == "bound":
                raise KnowledgeScopeError("bound sets require meeting data governance")
            if material_set["state"] != "deleted" and expected_revision != material_set["revision"]:
                raise KnowledgeConflict("material set revision changed")
            jobs = _delete_set(self._conn, set_id, _now())
            return _set(self._conn, set_id, allow_deleted=True), jobs

    def search_for_meeting(self, meeting_id: str, query: str, *, limit: int = 6, match_mode: str = "any") -> list[dict[str, Any]]:
        if not isinstance(query, str) or len(query) > 200 or not 1 <= limit <= 20 or match_mode not in {"all", "any"}:
            raise ValueError("invalid material search")
        terms = _tokens(query, query=True)
        if not terms:
            return []
        expression = (" AND " if match_mode == "all" else " OR ").join('"' + token + '"' for token in terms)
        with self._lock:
            rows = self._conn.execute("SELECT s.id FROM meeting_attachment_search f JOIN meeting_attachment_spans s ON s.id=f.span_id "
                                     "JOIN meeting_attachment_versions v ON v.id=s.version_id JOIN meeting_attachments a ON a.id=v.attachment_id "
                                     "JOIN material_sets m ON m.id=a.set_id WHERE meeting_attachment_search MATCH ? "
                                     "AND m.meeting_id=? AND m.state='bound' AND a.status='active' AND a.enabled=1 AND a.active_version_id=v.id "
                                     "AND (a.selected_span_ids_json IS NULL OR EXISTS(SELECT 1 FROM json_each(a.selected_span_ids_json) WHERE value=s.id)) "
                                     "ORDER BY bm25(meeting_attachment_search),a.created_at_ms DESC,s.ordinal LIMIT ?",
                                     (expression, meeting_id, limit)).fetchall()
            return [read_attachment_source(self._conn, meeting_id, row[0]) for row in rows]

    def preview_background(self, meeting_id: str, *, max_characters: int = 3600, limit: int = 6,
                           max_files: int = 6, max_quote_characters: int | None = 900) -> list[dict[str, Any]]:
        """Small selected material can be complete; large material is never silently full."""
        if not 1 <= limit <= 12 or not 1 <= max_characters <= 8000 or not 1 <= max_files <= 6:
            raise ValueError("invalid material preview budget")
        if max_quote_characters is not None and not 1 <= max_quote_characters <= max_characters:
            raise ValueError("invalid material preview quote budget")
        with self._lock:
            rows = self._conn.execute("SELECT s.id,s.text,a.id AS attachment_id FROM meeting_attachment_spans s "
                                     "JOIN meeting_attachment_versions v ON v.id=s.version_id JOIN meeting_attachments a ON a.id=v.attachment_id "
                                     "JOIN material_sets m ON m.id=a.set_id WHERE m.meeting_id=? AND m.state='bound' AND a.status='active' "
                                     "AND a.enabled=1 AND a.active_version_id=v.id AND "
                                     "(a.selected_span_ids_json IS NULL OR EXISTS(SELECT 1 FROM json_each(a.selected_span_ids_json) WHERE value=s.id)) "
                                     "ORDER BY a.created_at_ms DESC,a.id,s.ordinal LIMIT ?", (meeting_id, limit + 1)).fetchall()
            if (len(rows) > limit or len({row["attachment_id"] for row in rows}) > max_files
                    or sum(len(row["text"]) for row in rows) > max_characters
                    or (max_quote_characters is not None and any(len(row["text"]) > max_quote_characters for row in rows))):
                return []  # Longer files use query matching or explicit smaller section selection.
            return [read_attachment_source(self._conn, meeting_id, row[0]) for row in rows]


class MeetingMaterialImportService:
    def __init__(self, repository: MeetingMaterialsRepository, data_dir: str | Path) -> None:
        self.repository = repository
        self.data_dir = ensure_private_directory(data_dir)
        if _linked(self.data_dir / "meeting_material_assets"):
            raise ValueError("material asset directory must not be linked")
        self.assets_dir = ensure_private_directory(self.data_dir / "meeting_material_assets")
        if self.assets_dir.parent != self.data_dir:
            raise ValueError("material asset directory escapes managed storage")

    def stage(self, set_id: str, filename: str, data: bytes, *, title: str | None = None,
              purpose: str = "", content_type: str | None = None) -> dict[str, Any]:
        extension = validate_upload(filename, data, content_type)
        title = title.strip() if isinstance(title, str) else Path(filename).stem
        if not title or len(title) > 200 or len(purpose) > 100 or any(ord(c) < 32 for c in title + purpose):
            raise ValueError("invalid material metadata")
        attachment_id, now = _id("attachment"), _now()
        relative = f"meeting_material_assets/{set_id}/{attachment_id}/original{extension}"
        # Original save is bounded and occurs before the atomic row+job publication.
        with self.repository._write():
            _set(self.repository._conn, set_id)
            count = self.repository._conn.execute("SELECT COUNT(*) FROM meeting_attachments WHERE set_id=? AND status!='deleted'", (set_id,)).fetchone()[0]
            if count >= 3000:
                raise ValueError("material set exceeds 3000 active attachments")
            if any(_linked(path) for path in (self.assets_dir, self.assets_dir / set_id, self.assets_dir / set_id / attachment_id)):
                raise ValueError("material original escapes its permanent owner")
            directory = ensure_private_directory(self.assets_dir / set_id / attachment_id)
            temporary = directory / "original.tmp"
            try:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                harden_private_file(temporary)
                os.replace(temporary, self.data_dir / relative)
                harden_private_file(self.data_dir / relative)
                self.repository._conn.execute("INSERT INTO meeting_attachments(id,set_id,title,filename,content_type,purpose,"
                                              "original_relative_path,original_sha256,file_size_bytes,created_at_ms,updated_at_ms) "
                                              "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (attachment_id, set_id, title, filename, content_type, purpose,
                                              relative, hashlib.sha256(data).hexdigest(), len(data), now, now))
                _enqueue(self.repository._conn, attachment_id, "import", now)
                self.repository._conn.execute("UPDATE material_sets SET revision=revision+1,updated_at_ms=? WHERE id=?", (now, set_id))
            except BaseException:
                temporary.unlink(missing_ok=True)
                (self.data_dir / relative).unlink(missing_ok=True)
                raise
            return self.repository._attachment(set_id, attachment_id)

    def original_path(self, attachment_id: str, *, require_exists: bool = True) -> Path:
        attachment = self.repository.get_document(attachment_id)
        relative = attachment["original_relative_path"]
        if not isinstance(relative, str) or not re.fullmatch(
            r"meeting_material_assets/material_set_[0-9a-f]{32}/attachment_[0-9a-f]{32}/original\.(txt|md|docx|pdf)", relative):
            raise ValueError("material original has no safe path")
        expected_parent = self.assets_dir / attachment["set_id"] / attachment_id
        candidate = self.data_dir / relative
        if candidate.parent != expected_parent or any(_linked(path) for path in (self.assets_dir, expected_parent.parent, expected_parent, candidate)):
            raise ValueError("material original escapes its permanent owner")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.assets_dir) or (require_exists and not resolved.is_file()):
            raise ValueError("material original is missing or unsafe")
        if resolved.exists() and resolved.stat().st_nlink != 1:
            raise ValueError("material original must not be hardlinked")
        if require_exists and (resolved.stat().st_size > MAX_UPLOAD_BYTES or hashlib.sha256(resolved.read_bytes()).hexdigest() != attachment["original_sha256"]):
            raise ValueError("material original hash changed")
        return resolved

    def recover_unpublished_assets(self, *, now_seconds: float | None = None, limit: int = 1000) -> int:
        """Reap only unindexed crash remnants older than three days.

        Publication holds the same writer lock while saving originals, so a
        concurrent upload cannot be mistaken for an orphan. Never traverse a
        symlink/hardlink, unknown filename or a published attachment directory.
        """
        timestamp = time.time() if now_seconds is None else float(now_seconds)
        removed, inspected = 0, 0
        if not 1 <= limit <= 1000:
            raise ValueError("invalid orphan scan limit")
        if _linked(self.assets_dir) or not self.assets_dir.resolve().is_relative_to(self.data_dir):
            return 0
        for set_directory in self.assets_dir.iterdir():
            if (_linked(set_directory) or not set_directory.is_dir()
                    or not re.fullmatch(r"material_set_[0-9a-f]{32}", set_directory.name)):
                continue
            for directory in set_directory.iterdir():
                inspected += 1
                if inspected > limit:
                    return removed
                if (_linked(directory) or not directory.is_dir()
                        or not re.fullmatch(r"attachment_[0-9a-f]{32}", directory.name)):
                    continue
                with self.repository._write():
                    if self.repository._conn.execute("SELECT 1 FROM meeting_attachments WHERE id=?", (directory.name,)).fetchone():
                        continue
                    if self.repository._conn.execute("SELECT 1 FROM meeting_attachment_jobs WHERE attachment_id=? AND status IN ('queued','running')",
                                                     (directory.name,)).fetchone():
                        continue
                    if not directory.resolve().is_relative_to(self.assets_dir):
                        continue
                    files = list(directory.iterdir())
                    if any(_linked(path) or not path.is_file() or not re.fullmatch(r"original\.(txt|md|docx|pdf|tmp)", path.name)
                           or path.stat().st_nlink != 1 or timestamp - path.stat().st_mtime < 3 * 24 * 3600 for path in files):
                        continue
                    for path in files:
                        if not path.resolve().is_relative_to(self.assets_dir):
                            raise ValueError("orphan original escapes managed storage")
                        path.unlink()
                    if not any(directory.iterdir()):
                        directory.rmdir()
                        removed += 1
            if set_directory.exists() and not any(set_directory.iterdir()):
                set_directory.rmdir()
        return removed

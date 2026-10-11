"""Explicit local source imports. A copied version retains the actual original version."""

from __future__ import annotations

import hashlib
from typing import Any

from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError, _now, content_spans
from .review_export import review_library_content
from .source_library_provenance import source_snapshot


class LibrarySourceError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class SourceLibraryImporter:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def import_final(self, meeting_id: str, document_kind: str, *, expected_source_version: int,
                     folder_id: str | None = None, expected_document_revision: int | None = None,
                     now_ms: int | None = None) -> dict[str, Any]:
        library = self.repository
        with library._write():
            context = library.get_meeting_context(meeting_id)
            if context["project_id"] is None:
                raise LibrarySourceError("project_required")
            row = library._conn.execute("SELECT document_id FROM review_documents WHERE meeting_id=? AND document_kind=?",
                                        (meeting_id, document_kind)).fetchone()
            if row is None:
                raise LibrarySourceError("human_final_required")
            return self._import("review_document", row[0], context["project_id"], expected_source_version,
                                folder_id if folder_id is not None else context["folder_id"], expected_document_revision, _now(now_ms))

    def import_note(self, note_id: str, project_id: str, *, expected_source_version: int,
                    folder_id: str | None = None, expected_document_revision: int | None = None,
                    now_ms: int | None = None) -> dict[str, Any]:
        with self.repository._write():
            return self._import("note", note_id, project_id, expected_source_version,
                                folder_id, expected_document_revision, _now(now_ms))

    def _import(self, kind: str, source_id: str, project_id: str, expected_source_version: int,
                folder_id: str | None, expected_document_revision: int | None, now_ms: int) -> dict[str, Any]:
        library = self.repository
        library._project(project_id)
        library._folder(folder_id, project_id)
        try:
            snapshot = source_snapshot(library._conn, kind, source_id)
        except ValueError as exc:
            raise LibrarySourceError("human_final_required" if kind == "review_document" and str(exc) == "source_needs_review"
                                     else str(exc)) from None
        if isinstance(expected_source_version, bool) or expected_source_version != snapshot["source_version"]:
            raise KnowledgeConflict("original source version conflict")
        source_meeting = snapshot["source_meeting_id"]
        if source_meeting is not None:
            context = library._conn.execute("SELECT project_id FROM meeting_knowledge_context WHERE meeting_id=?", (source_meeting,)).fetchone()
            if context is not None and context[0] is not None and context[0] != project_id:
                raise KnowledgeScopeError("source meeting belongs to another project")
        binding = library._conn.execute("SELECT document_id FROM knowledge_source_bindings WHERE source_kind=? AND source_id=? AND project_id=?",
                                        (kind, source_id, project_id)).fetchone()
        created = binding is None
        if binding is not None:
            document = library.get_document(binding[0])
            if document["status"] in {"deleted", "disabled"}:
                raise LibrarySourceError("linked_document_unavailable")
            existing = library._conn.execute("SELECT library_version_id FROM knowledge_source_versions WHERE document_id=? "
                                             "AND source_kind=? AND source_id=? AND source_version=? AND source_hash=? "
                                             "ORDER BY created_at_ms,library_version_id LIMIT 1",
                                             (document["id"], kind, source_id, snapshot["source_version"], snapshot["source_hash"])).fetchone()
            if existing is not None:
                return {"document": document, "version": library.get_version(document["id"], existing[0]),
                        "created": False, "already_imported": True}
            if expected_document_revision is None:
                raise KnowledgeConflict("read current document before importing a newer source")
            library._expected(document, expected_document_revision)
        else:
            if expected_document_revision is not None:
                raise KnowledgeConflict("no previous linked document")
            document = library.create_document(project_id, self._new_document_title(snapshot), folder_id=folder_id,
                                               kind="meeting_document" if kind == "review_document" else "note",
                                               source_meeting_id=source_meeting, now_ms=now_ms)
            library._conn.execute("INSERT INTO knowledge_source_bindings(source_kind,source_id,project_id,document_id,created_at_ms) VALUES(?,?,?,?,?)",
                                  (kind, source_id, project_id, document["id"], now_ms))
        content = review_library_content(snapshot["document_kind"], snapshot["source_content"]) if kind == "review_document" else snapshot["content"]
        spans = content_spans(content)
        for span in spans:
            span["locator"] = {**span["locator"], "type": kind, "source_id": source_id,
                               "source_version": snapshot["source_version"], "source_hash": snapshot["source_hash"]}
        version = library.add_version(document["id"], content, spans=spans,
                                      origin="meeting_user_final" if kind == "review_document" else "user",
                                      expected_revision=document["revision"], now_ms=now_ms)
        library._conn.execute("INSERT OR REPLACE INTO knowledge_source_versions(library_version_id,document_id,source_kind,source_id,source_version,source_hash,created_at_ms) "
                              "VALUES(?,?,?,?,?,?,?)", (version["id"], document["id"], kind, source_id, snapshot["source_version"], snapshot["source_hash"], now_ms))
        document = library.set_import_status(document["id"], "extracted", now_ms=now_ms)
        return {"document": document, "version": library.get_version(document["id"], version["id"]),
                "created": created, "already_imported": False}

    def _new_document_title(self, snapshot: dict[str, Any]) -> str:
        if snapshot["source_kind"] != "review_document":
            return snapshot["title"][:200]
        meeting_id = snapshot["source_meeting_id"]
        row = self.repository._conn.execute("SELECT title FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        meeting_title = str(row[0] or "").strip() if row is not None else ""
        if not meeting_title:
            short_id = hashlib.sha256(meeting_id.encode("utf-8")).hexdigest()[:8]
            meeting_title = f"未命名会议（{short_id}）"
        suffix = f"·{snapshot['title']}"
        return f"{meeting_title[:200 - len(suffix)]}{suffix}"

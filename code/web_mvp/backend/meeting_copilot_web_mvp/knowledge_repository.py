"""Local, versioned project sources; no model calls and no implicit global scope."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
from threading import RLock
import time
from typing import Any, Iterator, Mapping, Sequence
import uuid

from .application_schema import bootstrap_application_schema
from .knowledge_aliases import confirmed_search_terms, expanded_fts_expression
from .knowledge_invalidation import invalidate_meeting_sources
from .storage_governance import ensure_private_directory, harden_sqlite_files


MAX_CONTENT_CHARACTERS = 2_000_000
MAX_SPANS = 5_000
MAX_SPAN_CHARACTERS = 4_000
_UNSET = object()
_KINDS = frozenset({"upload", "note", "meeting_document"})
_ORIGINS = frozenset({"extracted", "user", "ai", "meeting_user_final"})


class KnowledgeConflict(RuntimeError):
    """The user or a job edited an object after the caller read it."""


class KnowledgeScopeError(ValueError):
    """A source is outside the explicitly selected project or no longer valid."""


def _now(value: int | None = None) -> int:
    return max(0, int(value)) if value is not None else time.time_ns() // 1_000_000


def _label(value: Any, field: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not (value := value.strip()) or len(value) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field} contains control characters")
    return value


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _tokens(value: str, *, query: bool = False) -> list[str]:
    """Space-delimited Unicode grams let FTS5 match one/two-character Chinese terms."""
    result: list[str] = []
    seen: set[str] = set()
    for item in re.findall(r"[\u3400-\u9fff]+|[A-Za-z0-9_]+", value.casefold()):
        if re.fullmatch(r"[\u3400-\u9fff]+", item):
            parts = ([item] if len(item) == 1 else [item[index:index + 2] for index in range(len(item) - 1)])
            if not query:
                parts = [*item, *parts]
        else:
            parts = [item]
        for part in parts:
            if part not in seen:
                result.append(part)
                seen.add(part)
    return result


def content_spans(content: str) -> list[dict[str, Any]]:
    """Locators describe the corrected text, never invent original PDF page numbers."""
    spans: list[dict[str, Any]] = []
    for paragraph, block in enumerate(re.split(r"\n\s*\n", content), 1):
        block = block.strip()
        for offset in range(0, len(block), MAX_SPAN_CHARACTERS):
            spans.append({"text": block[offset:offset + MAX_SPAN_CHARACTERS],
                          "locator": {"type": "converted_text", "paragraph": paragraph, "offset": offset}})
    return spans


class KnowledgeRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        ensure_private_directory(self.database_path.parent)
        bootstrap_application_schema(self.database_path)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(self.database_path), isolation_level=None, timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        harden_sqlite_files(self.database_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _write(self) -> Iterator[None]:
        with self._lock:
            nested = self._conn.in_transaction
            savepoint = f"knowledge_{uuid.uuid4().hex}"
            self._conn.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.execute(f"ROLLBACK TO {savepoint}" if nested else "ROLLBACK")
                if nested:
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            else:
                self._conn.execute(f"RELEASE {savepoint}" if nested else "COMMIT")

    def _get(self, table: str, object_id: str) -> dict[str, Any]:
        # The table argument is an internal constant, never caller input.
        row = self._conn.execute(f"SELECT * FROM {table} WHERE id = ?", (object_id,)).fetchone()
        if row is None:
            raise KeyError(f"{table} object not found")
        result = dict(row)
        return self._document_source(result) if table == "knowledge_documents" else result

    def _document_source(self, document: dict[str, Any]) -> dict[str, Any]:
        from .source_library_provenance import library_source_status

        binding = self._conn.execute("SELECT source_kind,source_id FROM knowledge_source_bindings WHERE document_id=?",
                                     (document["id"],)).fetchone()
        latest = self._conn.execute("SELECT id FROM knowledge_versions WHERE document_id=? ORDER BY number DESC LIMIT 1",
                                    (document["id"],)).fetchone()
        selected = document["active_version_id"] or (latest[0] if latest is not None else None)
        return {**document, "source_status": library_source_status(self._conn, document["id"], selected),
                "source_binding": dict(binding) if binding is not None else None}

    def _project(self, project_id: str, *, active: bool = True) -> dict[str, Any]:
        project = self._get("knowledge_projects", project_id)
        if active and project["archived_at_ms"] is not None:
            raise KnowledgeScopeError("project is archived")
        return project

    def _folder(self, folder_id: str | None, project_id: str) -> None:
        if folder_id is not None and self._get("knowledge_folders", folder_id)["project_id"] != project_id:
            raise KnowledgeScopeError("folder belongs to another project")

    def _touch(self, project_id: str, now_ms: int) -> None:
        self._conn.execute("UPDATE knowledge_projects SET revision=revision+1, updated_at_ms=? WHERE id=?", (now_ms, project_id))
        self._conn.execute("UPDATE meeting_knowledge_context SET context_revision=context_revision+1, updated_at_ms=? "
                           "WHERE project_id=?", (now_ms, project_id))
        for row in self._conn.execute("SELECT meeting_id FROM meeting_knowledge_context WHERE project_id=?", (project_id,)).fetchall():
            invalidate_meeting_sources(self._conn, row["meeting_id"], now_ms)

    @staticmethod
    def _expected(item: Mapping[str, Any], expected_revision: int | None) -> None:
        if expected_revision is not None and int(expected_revision) != item["revision"]:
            raise KnowledgeConflict("knowledge revision conflict")

    def create_project(self, name: str, description: str = "", *, now_ms: int | None = None) -> dict[str, Any]:
        name = _label(name, "name")
        if not isinstance(description, str) or len(description) > 2_000:
            raise ValueError("description exceeds 2000 characters")
        project_id, timestamp = _id("project"), _now(now_ms)
        with self._write():
            self._conn.execute("INSERT INTO knowledge_projects(id,name,description,created_at_ms,updated_at_ms) VALUES(?,?,?,?,?)",
                               (project_id, name, description.strip(), timestamp, timestamp))
            return self._get("knowledge_projects", project_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            return self._project(project_id, active=False)

    def update_project(self, project_id: str, *, name: Any = _UNSET, description: Any = _UNSET,
                       archived: bool | None = None, expected_revision: int | None = None,
                       now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now(now_ms)
        with self._write():
            current = self._project(project_id, active=False)
            self._expected(current, expected_revision)
            next_name = current["name"] if name is _UNSET else _label(name, "name")
            next_description = current["description"] if description is _UNSET else description
            if not isinstance(next_description, str) or len(next_description) > 2_000:
                raise ValueError("description exceeds 2000 characters")
            archive_time = current["archived_at_ms"] if archived is None else (timestamp if archived else None)
            self._conn.execute("UPDATE knowledge_projects SET name=?,description=?,archived_at_ms=? WHERE id=?",
                               (next_name, next_description, archive_time, project_id))
            self._touch(project_id, timestamp)
            return self._get("knowledge_projects", project_id)

    def _page(self, table: str, *, clauses: list[str], parameters: list[Any], limit: int,
              cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(limit, bool) or not 1 <= int(limit) <= 200:
            raise ValueError("limit must be between 1 and 200")
        if cursor is not None:
            if not isinstance(cursor, Mapping) or not isinstance(cursor.get("id"), str):
                raise ValueError("invalid knowledge cursor")
            try:
                timestamp = int(cursor["updated_at_ms"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("invalid knowledge cursor") from exc
            clauses.append("(updated_at_ms < ? OR (updated_at_ms=? AND id<?))")
            parameters.extend((timestamp, timestamp, cursor["id"]))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._conn.execute(f"SELECT * FROM {table}{where} ORDER BY updated_at_ms DESC,id DESC LIMIT ?",
                                  (*parameters, int(limit) + 1)).fetchall()
        visible = [dict(row) for row in rows[:int(limit)]]
        next_cursor = ({"id": visible[-1]["id"], "updated_at_ms": visible[-1]["updated_at_ms"]}
                       if len(rows) > int(limit) else None)
        return {"items": visible, "next_cursor": next_cursor, "has_more": next_cursor is not None}

    def list_projects(self, *, include_archived: bool = False, limit: int = 100,
                      cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return self._page("knowledge_projects", clauses=[] if include_archived else ["archived_at_ms IS NULL"],
                              parameters=[], limit=limit, cursor=cursor)

    def create_folder(self, project_id: str, name: str, *, parent_id: str | None = None,
                      now_ms: int | None = None) -> dict[str, Any]:
        folder_id, timestamp, name = _id("folder"), _now(now_ms), _label(name, "name")
        with self._write():
            self._project(project_id)
            self._folder(parent_id, project_id)
            self._conn.execute("INSERT INTO knowledge_folders(id,project_id,parent_id,name,created_at_ms,updated_at_ms) "
                               "VALUES(?,?,?,?,?,?)", (folder_id, project_id, parent_id, name, timestamp, timestamp))
            self._touch(project_id, timestamp)
            return self._get("knowledge_folders", folder_id)

    def list_folders(self, project_id: str, *, limit: int = 100, cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._project(project_id, active=False)
            return self._page("knowledge_folders", clauses=["project_id=?"], parameters=[project_id], limit=limit, cursor=cursor)

    def update_folder(self, folder_id: str, *, name: Any = _UNSET, parent_id: Any = _UNSET,
                      expected_revision: int | None = None, now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now(now_ms)
        with self._write():
            current = self._get("knowledge_folders", folder_id)
            self._project(current["project_id"])
            self._expected(current, expected_revision)
            parent = current["parent_id"] if parent_id is _UNSET else parent_id
            self._folder(parent, current["project_id"])
            ancestor = parent
            seen: set[str] = set()
            while ancestor is not None:
                if ancestor == folder_id or ancestor in seen:
                    raise KnowledgeScopeError("folder move would create a cycle")
                seen.add(ancestor)
                ancestor = self._get("knowledge_folders", ancestor)["parent_id"]
            next_name = current["name"] if name is _UNSET else _label(name, "name")
            self._conn.execute("UPDATE knowledge_folders SET name=?,parent_id=?,revision=revision+1,updated_at_ms=? WHERE id=?",
                               (next_name, parent, timestamp, folder_id))
            self._touch(current["project_id"], timestamp)
            return self._get("knowledge_folders", folder_id)

    def _folder_ids(self, project_id: str, folder_id: str) -> list[str]:
        self._folder(folder_id, project_id)
        rows = self._conn.execute("WITH RECURSIVE tree(id) AS (SELECT id FROM knowledge_folders WHERE id=? "
                                  "UNION ALL SELECT f.id FROM knowledge_folders f JOIN tree t ON f.parent_id=t.id "
                                  "WHERE f.project_id=?) SELECT id FROM tree", (folder_id, project_id)).fetchall()
        return [row[0] for row in rows]

    def create_document(self, project_id: str, title: str, *, folder_id: str | None = None, kind: str = "upload",
                        source_meeting_id: str | None = None, filename: str | None = None,
                        content_type: str | None = None, file_size_bytes: int = 0, original_sha256: str | None = None,
                        original_relative_path: str | None = None, now_ms: int | None = None) -> dict[str, Any]:
        title = _label(title, "title")
        if kind not in _KINDS:
            raise ValueError("unsupported document kind")
        if filename is not None and (not isinstance(filename, str) or len(filename) > 240 or not filename.strip()
                                     or any(character in filename for character in ("/", "\\", ":", "\x00"))
                                     or any(ord(character) < 32 for character in filename)
                                     or filename in {".", ".."}):
            raise ValueError("unsafe document filename")
        if original_relative_path is not None:
            path = PurePosixPath(original_relative_path)
            if (path.is_absolute() or ".." in path.parts or "\\" in original_relative_path or ":" in original_relative_path
                    or not path.parts or path.parts[0] != "knowledge_assets"):
                raise ValueError("unsafe original document path")
        if not 0 <= int(file_size_bytes) <= 20 * 1024 * 1024:
            raise ValueError("document file exceeds 20 MiB")
        if content_type is not None and (not isinstance(content_type, str) or len(content_type) > 200
                                        or any(ord(character) < 32 for character in content_type)):
            raise ValueError("invalid document content type")
        if original_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", original_sha256):
            raise ValueError("invalid original SHA256")
        document_id, timestamp = _id("document"), _now(now_ms)
        with self._write():
            self._project(project_id)
            self._folder(folder_id, project_id)
            if source_meeting_id is not None and self._conn.execute("SELECT 1 FROM meetings WHERE id=?", (source_meeting_id,)).fetchone() is None:
                raise KeyError("source meeting not found")
            self._conn.execute("INSERT INTO knowledge_documents(id,project_id,folder_id,kind,title,source_meeting_id,filename,"
                               "content_type,file_size_bytes,original_sha256,original_relative_path,created_at_ms,updated_at_ms) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (document_id, project_id, folder_id, kind, title, source_meeting_id,
                               filename, content_type, int(file_size_bytes), original_sha256, original_relative_path, timestamp, timestamp))
            return self._get("knowledge_documents", document_id)

    def get_document(self, document_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get("knowledge_documents", document_id)

    def list_documents(self, project_id: str, *, folder_id: str | None = None, status: str = "all",
                       query: str = "", limit: int = 100, cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._project(project_id, active=False)
            clauses, parameters = ["project_id=?", "status!='deleted'"], [project_id]
            if status not in {"all", "draft", "active", "disabled", "deleted"}:
                raise ValueError("invalid document status")
            if status == "deleted":
                clauses.remove("status!='deleted'")
            if status != "all":
                clauses.append("status=?")
                parameters.append(status)
            if folder_id is not None:
                ids = self._folder_ids(project_id, folder_id)
                clauses.append("folder_id IN (" + ",".join("?" for _ in ids) + ")")
                parameters.extend(ids)
            if query.strip():
                clauses.append("instr(lower(title),lower(?))>0")
                parameters.append(query.strip()[:200])
            page = self._page("knowledge_documents", clauses=clauses, parameters=parameters, limit=limit, cursor=cursor)
            page["items"] = [self._document_source(item) for item in page["items"]]
            return page

    def set_import_status(self, document_id: str, status: str, *, error_code: str | None = None,
                          now_ms: int | None = None) -> dict[str, Any]:
        if status not in {"pending", "extracted", "needs_ocr", "unavailable", "failed"}:
            raise ValueError("invalid import status")
        with self._write():
            current = self._get("knowledge_documents", document_id)
            if current["status"] == "deleted":
                raise KnowledgeScopeError("document is deleted")
            self._conn.execute("UPDATE knowledge_documents SET import_status=?,error_code=?,updated_at_ms=? WHERE id=?",
                               (status, error_code, _now(now_ms), document_id))
            return self._get("knowledge_documents", document_id)

    def add_version(self, document_id: str, content: str, *, spans: Sequence[Mapping[str, Any]] | None = None,
                    origin: str = "user", expected_revision: int | None = None, base_version_id: str | None = None,
                    now_ms: int | None = None) -> dict[str, Any]:
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_CONTENT_CHARACTERS:
            raise ValueError("document content is empty or exceeds the character limit")
        if origin not in _ORIGINS:
            raise ValueError("unsupported version origin")
        normalized_spans = content_spans(content) if spans is None else list(spans)
        if not normalized_spans or len(normalized_spans) > MAX_SPANS:
            raise ValueError("document spans are empty or exceed the limit")
        for span in normalized_spans:
            if (not isinstance(span, Mapping) or not isinstance(span.get("text"), str) or not span["text"].strip()
                    or len(span["text"]) > MAX_SPAN_CHARACTERS or span["text"] not in content
                    or not isinstance(span.get("locator"), Mapping)
                    or len(json.dumps(dict(span["locator"]), ensure_ascii=False)) > 2_000):
                raise ValueError("invalid source span")
        version_id, timestamp = _id("version"), _now(now_ms)
        with self._write():
            document = self._get("knowledge_documents", document_id)
            self._project(document["project_id"])
            self._expected(document, expected_revision)
            if document["status"] == "deleted":
                raise KnowledgeScopeError("document is deleted")
            base = base_version_id or document["active_version_id"]
            if base is None:
                previous = self._conn.execute("SELECT id FROM knowledge_versions WHERE document_id=? ORDER BY number DESC LIMIT 1", (document_id,)).fetchone()
                base = previous[0] if previous is not None else None
            if base is not None and self._get("knowledge_versions", base)["document_id"] != document_id:
                raise KnowledgeScopeError("base version belongs to another document")
            number = self._conn.execute("SELECT COALESCE(MAX(number),0)+1 FROM knowledge_versions WHERE document_id=?", (document_id,)).fetchone()[0]
            self._conn.execute("INSERT INTO knowledge_versions(id,document_id,number,content,content_hash,origin,created_at_ms) "
                               "VALUES(?,?,?,?,?,?,?)", (version_id, document_id, number, content,
                               hashlib.sha256(content.encode("utf-8")).hexdigest(), origin, timestamp))
            self._conn.executemany("INSERT INTO knowledge_spans(id,version_id,ordinal,text,locator_json) VALUES(?,?,?,?,?)",
                                   [(_id("span"), version_id, index, span["text"], json.dumps(dict(span["locator"]), ensure_ascii=False))
                                    for index, span in enumerate(normalized_spans)])
            self._conn.execute("UPDATE knowledge_documents SET revision=revision+1,updated_at_ms=? WHERE id=?", (timestamp, document_id))
            from .source_library_provenance import inherit_library_source

            inherit_library_source(self._conn, document_id, version_id, base)
            return self._version(version_id)

    def _version(self, version_id: str) -> dict[str, Any]:
        version = self._get("knowledge_versions", version_id)
        rows = self._conn.execute("SELECT * FROM knowledge_spans WHERE version_id=? ORDER BY ordinal", (version_id,)).fetchall()
        version["spans"] = [{**dict(row), "locator": json.loads(row["locator_json"])} for row in rows]
        for span in version["spans"]:
            span.pop("locator_json", None)
        return version

    def get_version(self, document_id: str, version_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            document = self._get("knowledge_documents", document_id)
            selected = version_id or document["active_version_id"]
            if selected is None:
                row = self._conn.execute("SELECT id FROM knowledge_versions WHERE document_id=? ORDER BY number DESC LIMIT 1", (document_id,)).fetchone()
                if row is None:
                    raise KeyError("document has no version")
                selected = row[0]
            version = self._version(selected)
            if version["document_id"] != document_id:
                raise KnowledgeScopeError("version belongs to another document")
            from .source_library_provenance import library_source_status

            version["source_status"] = library_source_status(self._conn, document_id, selected)
            return version

    def list_versions(self, document_id: str, *, before_number: int | None = None, limit: int = 100) -> dict[str, Any]:
        if not 1 <= int(limit) <= 200:
            raise ValueError("limit must be between 1 and 200")
        with self._lock:
            self._get("knowledge_documents", document_id)
            rows = self._conn.execute("SELECT id,document_id,number,content_hash,origin,confirmed_at_ms,created_at_ms "
                                      "FROM knowledge_versions WHERE document_id=? AND number<? ORDER BY number DESC LIMIT ?",
                                      (document_id, before_number if before_number is not None else 2**63 - 1, int(limit) + 1)).fetchall()
            items = [dict(row) for row in rows[:int(limit)]]
            return {"items": items, "next_before_number": items[-1]["number"] if len(rows) > int(limit) else None}

    def _index(self, document: Mapping[str, Any], version: Mapping[str, Any]) -> None:
        self._conn.execute("DELETE FROM knowledge_search WHERE document_id=?", (document["id"],))
        title = " ".join(_tokens(document["title"]))
        self._conn.executemany("INSERT INTO knowledge_search(document_id,version_id,span_id,title_tokens,body_tokens) VALUES(?,?,?,?,?)",
                               [(document["id"], version["id"], span["id"], title, " ".join(_tokens(span["text"])))
                                for span in version["spans"]])

    def confirm_version(self, document_id: str, version_id: str, *, expected_revision: int,
                        now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now(now_ms)
        with self._write():
            document = self._get("knowledge_documents", document_id)
            self._expected(document, expected_revision)
            self._project(document["project_id"])
            if document["status"] in {"disabled", "deleted"}:
                raise KnowledgeScopeError("document is disabled or deleted")
            version = self._version(version_id)
            if version["document_id"] != document_id:
                raise KnowledgeScopeError("version belongs to another document")
            from .source_library_provenance import library_source_is_current

            if not library_source_is_current(self._conn, document_id, version_id):
                raise KnowledgeScopeError("document source has changed and needs review")
            self._conn.execute("UPDATE knowledge_versions SET confirmed_at_ms=COALESCE(confirmed_at_ms,?) WHERE id=?", (timestamp, version_id))
            self._conn.execute("UPDATE knowledge_documents SET active_version_id=?,status='active',revision=revision+1,updated_at_ms=? WHERE id=?",
                               (version_id, timestamp, document_id))
            self._index(document, version)
            self._touch(document["project_id"], timestamp)
            memory_queue = getattr(self, "_memory_reconcile_queue", None)
            if memory_queue is not None:
                memory_queue.enqueue(document_id, expected_version_id=version_id, automatic=True)
            return self._get("knowledge_documents", document_id)

    def update_document(self, document_id: str, *, title: Any = _UNSET, folder_id: Any = _UNSET,
                        status: Any = _UNSET, expected_revision: int | None = None,
                        now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now(now_ms)
        with self._write():
            current = self._get("knowledge_documents", document_id)
            self._project(current["project_id"])
            self._expected(current, expected_revision)
            if current["status"] == "deleted":
                raise KnowledgeScopeError("deleted document cannot be restored implicitly")
            next_title = current["title"] if title is _UNSET else _label(title, "title")
            folder = current["folder_id"] if folder_id is _UNSET else folder_id
            self._folder(folder, current["project_id"])
            next_status = current["status"] if status is _UNSET else status
            if next_status not in {"draft", "active", "disabled", "deleted"}:
                raise ValueError("invalid document status")
            if next_status == "active" and (current["active_version_id"] is None
                                           or self._version(current["active_version_id"])["confirmed_at_ms"] is None):
                raise KnowledgeScopeError("document has no confirmed version")
            self._conn.execute("UPDATE knowledge_documents SET title=?,folder_id=?,status=?,revision=revision+1,updated_at_ms=? WHERE id=?",
                               (next_title, folder, next_status, timestamp, document_id))
            updated = self._get("knowledge_documents", document_id)
            if next_status == "active":
                self._index(updated, self._version(updated["active_version_id"]))
            else:
                self._conn.execute("DELETE FROM knowledge_search WHERE document_id=?", (document_id,))
            self._touch(current["project_id"], timestamp)
            return updated

    def move_document(self, document_id: str, folder_id: str | None, **kwargs: Any) -> dict[str, Any]:
        return self.update_document(document_id, folder_id=folder_id, **kwargs)

    def set_document_status(self, document_id: str, status: str, **kwargs: Any) -> dict[str, Any]:
        return self.update_document(document_id, status=status, **kwargs)

    def _valid_document(self, document_id: str, project_id: str, folders: list[str] | None = None) -> dict[str, Any]:
        document = self._get("knowledge_documents", document_id)
        if document["project_id"] != project_id or document["status"] != "active" or document["active_version_id"] is None:
            raise KnowledgeScopeError("document is outside the project or is not confirmed and active")
        if self._get("knowledge_versions", document["active_version_id"])["confirmed_at_ms"] is None:
            raise KnowledgeScopeError("document version is not confirmed")
        from .source_library_provenance import library_source_is_current

        if not library_source_is_current(self._conn, document_id, document["active_version_id"]):
            raise KnowledgeScopeError("document source has changed and needs review")
        if folders is not None and document["folder_id"] not in folders:
            raise KnowledgeScopeError("document is outside the selected folder")
        return document

    def bind_meeting_context(self, meeting_id: str, project_id: str | None = None, *, folder_id: str | None = None,
                             document_ids: Sequence[str] | None = None, expected_revision: int | None = None,
                             now_ms: int | None = None) -> dict[str, Any]:
        timestamp = _now(now_ms)
        if document_ids is not None and (isinstance(document_ids, (str, bytes)) or not isinstance(document_ids, Sequence)
                                          or len(document_ids) > 3_000 or any(not isinstance(item, str) for item in document_ids)):
            raise ValueError("selected documents must be an array with at most 3000 IDs")
        selected = list(dict.fromkeys(document_ids or []))
        mode = "none" if project_id is None else "project" if document_ids is None else "documents"
        if project_id is None and (selected or folder_id is not None):
            raise KnowledgeScopeError("documents and folder require a selected project")
        with self._write():
            if self._conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
                raise KeyError("meeting not found")
            previous = self._conn.execute("SELECT * FROM meeting_knowledge_context WHERE meeting_id=?", (meeting_id,)).fetchone()
            revision = previous["context_revision"] if previous is not None else 0
            if expected_revision is not None and int(expected_revision) != revision:
                raise KnowledgeConflict("meeting context revision conflict")
            if project_id is not None:
                self._project(project_id)
                folders = self._folder_ids(project_id, folder_id) if folder_id is not None else None
                for document_id in selected:
                    self._valid_document(document_id, project_id, folders)
            self._conn.execute("INSERT INTO meeting_knowledge_context(meeting_id,project_id,folder_id,selection_mode,"
                               "selected_document_ids_json,context_revision,updated_at_ms) VALUES(?,?,?,?,?,?,?) "
                               "ON CONFLICT(meeting_id) DO UPDATE SET project_id=excluded.project_id,folder_id=excluded.folder_id,"
                               "selection_mode=excluded.selection_mode,selected_document_ids_json=excluded.selected_document_ids_json,"
                               "context_revision=excluded.context_revision,updated_at_ms=excluded.updated_at_ms",
                               (meeting_id, project_id, folder_id, mode, json.dumps(selected), revision + 1, timestamp))
            invalidate_meeting_sources(self._conn, meeting_id, timestamp)
            return self._context(meeting_id)

    def _context(self, meeting_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM meeting_knowledge_context WHERE meeting_id=?", (meeting_id,)).fetchone()
        if row is None:
            return {"meeting_id": meeting_id, "project_id": None, "folder_id": None, "selection_mode": "none",
                    "selected_document_ids": [], "context_revision": 0}
        context = dict(row)
        context["selected_document_ids"] = json.loads(context.pop("selected_document_ids_json"))
        return context

    def get_meeting_context(self, meeting_id: str) -> dict[str, Any]:
        with self._lock:
            return self._context(meeting_id)

    def search(self, query: str, *, project_id: str, folder_id: str | None = None,
               document_ids: Sequence[str] | None = None, limit: int = 6, match_mode: str = "all",
               memory_scene: str = "general", _meeting_id: str | None = None) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
            raise ValueError("query must contain 1 to 200 characters")
        if isinstance(limit, bool) or not 1 <= int(limit) <= 20:
            raise ValueError("search limit must be between 1 and 20")
        if match_mode not in {"all", "any"}:
            raise ValueError("unsupported keyword match mode")
        terms = _tokens(query, query=True)[:24]
        if document_ids == [] or document_ids == ():
            return []
        if document_ids is not None and (isinstance(document_ids, (str, bytes)) or len(document_ids) > 3_000):
            raise ValueError("invalid selected document IDs")
        with self._lock:
            self._project(project_id)
            clauses, parameters = ["knowledge_search MATCH ?", "d.project_id=?", "d.status='active'",
                                   "d.active_version_id=v.id", "v.confirmed_at_ms IS NOT NULL"], [None, project_id]
            folders = None
            if folder_id is not None:
                folders = self._folder_ids(project_id, folder_id)
                clauses.append("d.folder_id IN (" + ",".join("?" for _ in folders) + ")")
                parameters.extend(folders)
            if document_ids is not None:
                # Never silently widen scope if one of the caller's IDs is unrelated.
                for document_id in document_ids:
                    if self._get("knowledge_documents", document_id)["project_id"] != project_id:
                        raise KnowledgeScopeError("selected document belongs to another project")
                clauses.append("d.id IN (" + ",".join("?" for _ in document_ids) + ")")
                parameters.extend(document_ids)
            aliases = confirmed_search_terms(self, project_id=project_id, folder_ids=folders,
                                             document_ids=document_ids, meeting_id=_meeting_id, scene=memory_scene)
            parameters[0] = expanded_fts_expression(query, aliases, tokenize=lambda value: _tokens(value, query=True),
                                                  match_mode=match_mode)
            if not parameters[0]:
                return []
            literal = (" AND " if match_mode == "all" else " OR ").join('"' + token + '"' for token in terms)
            expanded = parameters[0] != literal
            rows = self._conn.execute("SELECT d.id AS document_id,d.project_id,d.title,v.id AS version_id,v.number AS version,"
                                      "v.content_hash,s.id AS source_id,s.ordinal,s.text AS quote,s.locator_json,"
                                      "bm25(knowledge_search,0,0,0,4,1) AS score FROM knowledge_search "
                                      "JOIN knowledge_documents d ON d.id=knowledge_search.document_id "
                                      "JOIN knowledge_versions v ON v.id=knowledge_search.version_id "
                                      "JOIN knowledge_spans s ON s.id=knowledge_search.span_id WHERE " + " AND ".join(clauses) +
                                      " ORDER BY score,d.updated_at_ms DESC,s.ordinal LIMIT ?", (*parameters, int(limit) * 4)).fetchall()
            from .source_library_provenance import library_source_is_current

            result = []
            for row in rows:
                if not library_source_is_current(self._conn, row["document_id"], row["version_id"]):
                    continue
                item = dict(row)
                item["source_type"] = "document"
                item["locator"] = json.loads(item.pop("locator_json"))
                result.append(item)
                if len(result) == int(limit):
                    break
            if expanded and aliases != confirmed_search_terms(self, project_id=project_id, folder_ids=folders,
                                                               document_ids=document_ids, meeting_id=_meeting_id,
                                                               scene=memory_scene):
                # A concurrent confirmation/disable/source change must not let
                # an already revoked mapping supply the next model's evidence.
                return []
            return result

    def search_for_meeting(self, meeting_id: str, query: str, *, limit: int = 6, match_mode: str = "all",
                           memory_scene: str = "general") -> list[dict[str, Any]]:
        with self._lock:
            context = self._context(meeting_id)
            if context["project_id"] is None or context["selection_mode"] == "none":
                return []
            return self.search(query, project_id=context["project_id"], folder_id=context["folder_id"],
                               document_ids=context["selected_document_ids"] if context["selection_mode"] == "documents" else None,
                               limit=limit, match_mode=match_mode, memory_scene=memory_scene, _meeting_id=meeting_id)

    def validate_evidence(self, evidence: Mapping[str, Any], *, meeting_id: str | None = None,
                          project_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if evidence.get("source_type") == "meeting_attachment":
                if meeting_id is None or project_id is not None:
                    raise KnowledgeScopeError("attachments require explicit meeting scope")
                from .meeting_materials import validate_attachment_evidence

                return validate_attachment_evidence(self._conn, meeting_id, evidence)
            if meeting_id is not None:
                context = self._context(meeting_id)
                if context["project_id"] is None or context["selection_mode"] == "none":
                    raise KnowledgeScopeError("meeting has no selected knowledge scope")
                if project_id is not None and project_id != context["project_id"]:
                    raise KnowledgeScopeError("evidence project differs from meeting scope")
                project_id = context["project_id"]
                if context["selection_mode"] == "documents" and evidence.get("document_id") not in context["selected_document_ids"]:
                    raise KnowledgeScopeError("evidence document is no longer selected")
                folders = self._folder_ids(project_id, context["folder_id"]) if context["folder_id"] is not None else None
            else:
                folders = None
            if project_id is None:
                raise KnowledgeScopeError("evidence validation requires an explicit project")
            self._project(project_id)
            document = self._valid_document(str(evidence.get("document_id") or ""), project_id, folders)
            if document["active_version_id"] != evidence.get("version_id"):
                raise KnowledgeScopeError("evidence version was superseded")
            version = self._version(document["active_version_id"])
            span = next((item for item in version["spans"] if item["id"] == evidence.get("source_id")), None)
            quote = evidence.get("quote")
            if (evidence.get("source_type") != "document" or span is None or not isinstance(quote, str) or not quote.strip()
                    or quote not in span["text"] or evidence.get("version") != version["number"]
                    or evidence.get("project_id") != document["project_id"]
                    or evidence.get("content_hash") != version["content_hash"] or evidence.get("locator") != span["locator"]):
                raise KnowledgeScopeError("evidence source, quote or version metadata does not match")
            return {**dict(evidence), "title": document["title"], "ordinal": span["ordinal"]}

    def read_source(self, source_id: str, *, meeting_id: str | None = None,
                    project_id: str | None = None) -> dict[str, Any]:
        """Resolve a citation only while its source is still confirmed, active and selected."""
        with self._lock:
            if isinstance(source_id, str) and source_id.startswith("material_span_"):
                if meeting_id is None or project_id is not None:
                    raise KnowledgeScopeError("attachments require explicit meeting scope")
                from .meeting_materials import read_attachment_source

                return read_attachment_source(self._conn, meeting_id, source_id)
            span = self._get("knowledge_spans", source_id)
            version = self._get("knowledge_versions", span["version_id"])
            document = self._get("knowledge_documents", version["document_id"])
            evidence = {"source_type": "document", "source_id": span["id"], "document_id": document["id"],
                        "project_id": document["project_id"], "title": document["title"], "version_id": version["id"],
                        "version": version["number"], "content_hash": version["content_hash"], "ordinal": span["ordinal"],
                        "quote": span["text"], "locator": json.loads(span["locator_json"])}
            return self.validate_evidence(evidence, meeting_id=meeting_id, project_id=project_id)

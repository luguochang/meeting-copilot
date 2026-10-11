"""Human-confirmed project state and reproducible Wiki views; AI changes remain proposals."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Mapping, Sequence
from urllib.parse import quote
import uuid

from .knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError


MAX_CHANGES = 40
MAX_SOURCE_REFS = 12
_PUBLIC_STATUSES = {"active", "done", "cancelled", "unknown", "discarded"}
_TERMINAL_STATUSES = {"done", "cancelled", "discarded", "superseded"}
_WIKI_STATUS_LABELS = {"active": "有效", "done": "已完成", "cancelled": "已取消", "unknown": "待确认",
                       "discarded": "已作废", "superseded": "已被替代", "needs_review": "待核对"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text(value: Any, field: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"invalid {field}")
    return value.strip()


def review_content_text(value: Any) -> str:
    parts = []
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 20:
            raise ValueError("review document nesting exceeds limit")
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, (bool, int, float)):
            parts.append(json.dumps(item, ensure_ascii=False))
        elif isinstance(item, Mapping):
            pending.extend((child, depth + 1) for child in reversed(list(item.values())))
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in reversed(item))
    return "\n".join(parts)


class ProjectOutcomes:
    def __init__(self, repository: KnowledgeRepository) -> None:
        self.repository = repository

    def _touch_state(self, project_id: str, timestamp: int) -> None:
        # Outcome revisions are distinct from document selection/version revisions.
        self.repository._conn.execute("UPDATE knowledge_projects SET revision=revision+1,updated_at_ms=? WHERE id=?", (timestamp, project_id))
        from .knowledge_invalidation import invalidate_input_context

        for row in self.repository._conn.execute("SELECT meeting_id FROM meeting_knowledge_context WHERE project_id=?", (project_id,)).fetchall():
            invalidate_input_context(self.repository._conn, row[0], timestamp)

    def _item(self, item_id: str) -> dict[str, Any]:
        item = self.repository._get("project_state_items", item_id)
        item["source_refs"] = json.loads(item.pop("source_refs_json"))
        return item

    def _proposal(self, proposal_id: str) -> dict[str, Any]:
        proposal = self.repository._get("project_change_proposals", proposal_id)
        proposal["changes"] = json.loads(proposal.pop("changes_json"))
        proposal["result_item_ids"] = json.loads(proposal.pop("result_item_ids_json"))
        proposal["is_stale"] = proposal["base_revision"] != self.repository._project(proposal["project_id"], active=False)["revision"]
        return proposal

    def _meeting(self, meeting_id: str | None, project_id: str) -> None:
        if meeting_id is not None:
            if self.repository._conn.execute("SELECT 1 FROM meetings WHERE id=?", (meeting_id,)).fetchone() is None:
                raise KeyError("meeting not found")
            if self.repository._context(meeting_id)["project_id"] != project_id:
                raise KnowledgeScopeError("meeting is not bound to this project")

    def _review_source(self, source_id: str, project_id: str) -> tuple[dict[str, Any], str]:
        row = self.repository._conn.execute("SELECT * FROM review_documents WHERE document_id=?", (source_id,)).fetchone()
        if row is None or row["user_modified"] != 1 or row["user_content_json"] is None or row["dirty_state"] != "saved":
            raise KnowledgeScopeError("review source is not a current human final document")
        self._meeting(row["meeting_id"], project_id)
        content = json.loads(row["user_content_json"])
        return {"source_type": "review_document", "source_id": row["document_id"], "project_id": project_id,
                "meeting_id": row["meeting_id"], "document_kind": row["document_kind"], "version_kind": "user_final",
                "version": row["user_version"], "content_hash": hashlib.sha256(_json(content).encode()).hexdigest(),
                "locator": {"type": "review_document", "document_kind": row["document_kind"]}}, review_content_text(content)

    def _entity_source(self, source_id: str, project_id: str, meeting_id: str | None) -> tuple[dict[str, Any], str]:
        if meeting_id is None:
            raise ValueError("meeting entity requires its meeting ID")
        self._meeting(meeting_id, project_id)
        row = self.repository._conn.execute("SELECT * FROM meeting_entities WHERE meeting_id=? AND entity_id=?", (meeting_id, source_id)).fetchone()
        if row is None or row["status"] != "confirmed":
            raise KnowledgeScopeError("meeting entity is not human confirmed")
        version = row["version"]
        human = self.repository._conn.execute("SELECT 1 FROM meeting_events WHERE meeting_id=? AND aggregate_id=? AND idempotency_key IN (?,?)",
                                              (meeting_id, source_id, f"meeting.entity.status:{source_id}:{version}",
                                               f"meeting.entity.user_edit:{source_id}:{version}")).fetchone()
        if human is None:
            raise KnowledgeScopeError("current meeting entity version has no human confirmation")
        snapshot = {key: row[key] for key in ("kind", "status", "text", "owner", "deadline", "mitigation")}
        content = row["text"] + (f"\n负责人：{row['owner']}" if row["owner"] else "") + (f"\n期限：{row['deadline']}" if row["deadline"] else "")
        return {"source_type": "meeting_entity", "source_id": source_id, "meeting_id": meeting_id, "project_id": project_id,
                "version": version, "content_hash": hashlib.sha256(_json(snapshot).encode()).hexdigest(),
                "locator": {"type": "meeting_entity", "kind": row["kind"]}}, content

    def source(self, source_id: str, project_id: str, *, source_type: str = "document", meeting_id: str | None = None) -> dict[str, Any]:
        with self.repository._lock:
            self.repository._project(project_id)
            if source_type == "document":
                return self.repository.read_source(source_id, project_id=project_id)
            if source_type == "meeting_entity":
                reference, content = self._entity_source(source_id, project_id, meeting_id)
                return {**reference, "quote": content[:4000]}
            if source_type != "review_document":
                raise ValueError("unsupported outcome source type")
            reference, content = self._review_source(source_id, project_id)
            return {**reference, "quote": content[:4000]}

    def _refs(self, values: Sequence[Mapping[str, Any]], project_id: str) -> list[dict[str, Any]]:
        if not isinstance(values, (list, tuple)) or len(values) > MAX_SOURCE_REFS:
            raise ValueError("too many outcome sources")
        result = []
        for ref in values:
            if not isinstance(ref, Mapping) or not isinstance(ref.get("quote"), str) or not 1 <= len(ref["quote"]) <= 4000:
                raise ValueError("invalid outcome source")
            if ref.get("source_type") == "document":
                canonical = self.repository.validate_evidence(ref, project_id=project_id)
                canonical = {key: canonical[key] for key in ("source_type", "source_id", "document_id", "project_id", "title", "version_id",
                                                             "version", "content_hash", "ordinal", "quote", "locator")}
            elif ref.get("source_type") == "review_document":
                canonical, content = self._review_source(str(ref.get("source_id") or ""), project_id)
                if (ref.get("project_id") != project_id or ref.get("version_kind") != "user_final"
                        or ref.get("meeting_id") != canonical["meeting_id"] or ref.get("version") != canonical["version"]
                        or ref.get("content_hash") != canonical["content_hash"] or ref.get("locator") != canonical["locator"]
                        or not ref["quote"].strip() or ref["quote"] not in content):
                    raise KnowledgeScopeError("review source version or quote has changed")
                canonical["quote"] = ref["quote"]
            elif ref.get("source_type") == "meeting_entity":
                canonical, content = self._entity_source(str(ref.get("source_id") or ""), project_id, ref.get("meeting_id"))
                if (ref.get("project_id") != project_id or ref.get("version") != canonical["version"]
                        or ref.get("content_hash") != canonical["content_hash"] or ref.get("locator") != canonical["locator"]
                        or not ref["quote"].strip() or ref["quote"] not in content):
                    raise KnowledgeScopeError("meeting fact version or quote has changed")
                canonical["quote"] = ref["quote"]
            else:
                raise ValueError("unsupported outcome source type")
            result.append(canonical)
        return result

    def _history(self, item_id: str, reason: str, timestamp: int) -> None:
        item = self._item(item_id)
        self.repository._conn.execute("INSERT INTO project_state_history(id,project_id,item_id,revision,snapshot_json,reason,created_at_ms) "
                                      "VALUES(?,?,?,?,?,?,?)", (f"state_history_{uuid.uuid4().hex}", item["project_id"], item_id,
                                      item["revision"], _json(item), reason, timestamp))

    def reconcile(self, project_id: str) -> int:
        with self.repository._write():
            self.repository._project(project_id)
            rows = self.repository._conn.execute("SELECT id FROM project_state_items WHERE project_id=? AND status IN ('active','unknown')", (project_id,)).fetchall()
            timestamp, affected = time.time_ns() // 1_000_000, 0
            for row in rows:
                item = self._item(row["id"])
                try:
                    self._refs(item["source_refs"], project_id)
                except (KnowledgeScopeError, KeyError, ValueError):
                    self.repository._conn.execute("UPDATE project_state_items SET status='needs_review',revision=revision+1,updated_at_ms=? WHERE id=?",
                                                  (timestamp, item["id"]))
                    self._history(item["id"], "source_invalidated", timestamp)
                    affected += 1
            proposals = self.repository._conn.execute("SELECT id FROM project_change_proposals WHERE project_id=? AND status='pending'", (project_id,)).fetchall()
            for row in proposals:
                proposal = self._proposal(row["id"])
                try:
                    for change in proposal["changes"]:
                        self._refs(change.get("source_refs", []), project_id)
                except (KnowledgeScopeError, KeyError, ValueError):
                    self.repository._conn.execute("UPDATE project_change_proposals SET status='needs_review',revision=revision+1,updated_at_ms=? WHERE id=?",
                                                  (timestamp, proposal["id"]))
            if affected:
                self._touch_state(project_id, timestamp)
            return affected

    @staticmethod
    def _kind_status(kind: str, status: str) -> None:
        if kind not in {"decision", "task", "question"} or status not in _PUBLIC_STATUSES:
            raise ValueError("invalid outcome type or status")
        if kind == "decision" and status not in {"active", "discarded"}:
            raise ValueError("decisions may be active or discarded")

    def _apply(self, project_id: str, change: Mapping[str, Any], *, meeting_id: str | None,
               origin: str, timestamp: int) -> dict[str, Any]:
        if not isinstance(change, Mapping) or set(change) - {"action", "item_id", "type", "text", "status", "owner", "due", "source_refs"}:
            raise ValueError("invalid project change")
        action = change.get("action", "create")
        if action not in {"create", "supersede", "update", "discard"}:
            raise ValueError("invalid project change action")
        old = self._item(change["item_id"]) if action != "create" else None
        if old is not None and old["project_id"] != project_id:
            raise KnowledgeScopeError("outcome belongs to another project")
        if old is not None and old["status"] in _TERMINAL_STATUSES and action != "discard":
            raise KnowledgeScopeError("terminal outcomes cannot be revived by an old proposal")
        kind = change.get("type", old["type"] if old else None)
        if old is not None and kind != old["type"]:
            raise ValueError("outcome type cannot change")
        status = "discarded" if action == "discard" else change.get("status", "active" if action in {"create", "supersede"} else old["status"])
        self._kind_status(kind, status)
        text = _text(change.get("text", old["text"] if old else None), "text")
        refs = old["source_refs"] if action == "discard" and "source_refs" not in change else self._refs(change.get("source_refs", old["source_refs"] if old else []), project_id)
        if origin == "confirmed_proposal" and (not refs or (action == "discard" and "source_refs" not in change)):
            raise KnowledgeScopeError("AI candidates require verifiable sources before confirmation")
        if origin == "confirmed_proposal" and action == "create" and self.repository._conn.execute(
            "SELECT 1 FROM project_state_items WHERE project_id=? AND type=? AND text=? AND status IN ('done','cancelled','discarded','superseded')",
            (project_id, kind, text),
        ).fetchone():
            raise KnowledgeScopeError("AI candidates cannot recreate a terminal outcome from old sources")
        owner = change.get("owner", old["owner"] if old else None)
        due = change.get("due", old["due"] if old else None)
        for field, value in (("owner", owner), ("due", due)):
            if value is not None:
                _text(value, field, 200)
                if kind != "task":
                    raise ValueError("only tasks have owner or due")
                if origin == "confirmed_proposal" and not any(value in ref["quote"] for ref in refs):
                    raise KnowledgeScopeError("candidate owner or deadline is not present in its sources")
        if action in {"update", "discard"}:
            item_id = old["id"]
            self.repository._conn.execute("UPDATE project_state_items SET text=?,status=?,owner=?,due=?,source_refs_json=?,"
                                          "source_meeting_id=COALESCE(?,source_meeting_id),revision=revision+1,effective_at_ms=?,updated_at_ms=? WHERE id=?",
                                          (text, status, owner, due, _json(refs), meeting_id, timestamp, timestamp, item_id))
        else:
            item_id = f"project_item_{uuid.uuid4().hex}"
            if old is not None:
                self.repository._conn.execute("UPDATE project_state_items SET status='superseded',revision=revision+1,updated_at_ms=? WHERE id=?", (timestamp, old["id"]))
                self._history(old["id"], "superseded", timestamp)
            self.repository._conn.execute("INSERT INTO project_state_items(id,project_id,type,text,status,owner,due,source_meeting_id,"
                                          "source_refs_json,origin,supersedes_id,effective_at_ms,confirmed_at_ms,created_at_ms,updated_at_ms) "
                                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (item_id, project_id, kind, text, status, owner, due, meeting_id,
                                          _json(refs), origin, old["id"] if old else None, timestamp, timestamp, timestamp, timestamp))
        self._history(item_id, action, timestamp)
        return self._item(item_id)

    def create(self, project_id: str, *, kind: str, text: str, status: str = "active", owner: str | None = None,
               due: str | None = None, source_meeting_id: str | None = None, source_refs: Sequence[Mapping[str, Any]] = (),
               expected_project_revision: int) -> dict[str, Any]:
        with self.repository._write():
            project = self.repository._project(project_id)
            self.repository._expected(project, expected_project_revision)
            self._meeting(source_meeting_id, project_id)
            timestamp = time.time_ns() // 1_000_000
            item = self._apply(project_id, {"action": "create", "type": kind, "text": text, "status": status,
                                            "owner": owner, "due": due, "source_refs": list(source_refs)},
                               meeting_id=source_meeting_id, origin="user", timestamp=timestamp)
            self._touch_state(project_id, timestamp)
            return item

    def update(self, item_id: str, changes: Mapping[str, Any], *, expected_revision: int,
               expected_project_revision: int) -> dict[str, Any]:
        with self.repository._write():
            current = self._item(item_id)
            self.repository._expected(current, expected_revision)
            self.repository._expected(self.repository._project(current["project_id"]), expected_project_revision)
            timestamp = time.time_ns() // 1_000_000
            updated = self._apply(current["project_id"], {**dict(changes), "action": "discard" if changes.get("status") == "discarded" else "update", "item_id": item_id},
                                  meeting_id=None, origin="user", timestamp=timestamp)
            self._touch_state(current["project_id"], timestamp)
            return updated

    def list(self, project_id: str, *, status: str | None = None, limit: int = 100, cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.reconcile(project_id)
        with self.repository._lock:
            clauses, parameters = ["project_id=?"], [project_id]
            if status is not None:
                if status not in {*_PUBLIC_STATUSES, "superseded", "needs_review"}:
                    raise ValueError("invalid outcome status")
                clauses.append("status=?")
                parameters.append(status)
            page = self.repository._page("project_state_items", clauses=clauses, parameters=parameters, limit=limit, cursor=cursor)
            page["items"] = [self._item(item["id"]) for item in page["items"]]
            page["project_revision"] = self.repository._project(project_id)["revision"]
            return page

    def propose(self, project_id: str, changes: Sequence[Mapping[str, Any]], *, origin: str = "ai", meeting_id: str | None = None,
                expected_project_revision: int) -> dict[str, Any]:
        if origin not in {"ai", "user"} or not isinstance(changes, (list, tuple)) or not 1 <= len(changes) <= MAX_CHANGES:
            raise ValueError("invalid proposal")
        with self.repository._write():
            self.repository._expected(self.repository._project(project_id), expected_project_revision)
            self._meeting(meeting_id, project_id)
            # Validate by rolling back a savepoint: this checks the final transition without changing current state.
            self.repository._conn.execute("SAVEPOINT proposal_validation")
            try:
                for change in changes:
                    self._apply(project_id, change, meeting_id=meeting_id, origin="confirmed_proposal" if origin == "ai" else "user",
                                timestamp=time.time_ns() // 1_000_000)
            finally:
                self.repository._conn.execute("ROLLBACK TO proposal_validation")
                self.repository._conn.execute("RELEASE proposal_validation")
            proposal_id, timestamp = f"project_proposal_{uuid.uuid4().hex}", time.time_ns() // 1_000_000
            self.repository._conn.execute("INSERT INTO project_change_proposals(id,project_id,meeting_id,origin,base_revision,changes_json,status,"
                                          "created_at_ms,updated_at_ms) VALUES(?,?,?,?,?,?,'pending',?,?)",
                                          (proposal_id, project_id, meeting_id, origin, expected_project_revision, _json(changes), timestamp, timestamp))
            return self._proposal(proposal_id)

    def confirm(self, proposal_id: str, *, expected_revision: int, expected_project_revision: int) -> dict[str, Any]:
        with self.repository._lock:
            project_id = self._proposal(proposal_id)["project_id"]
        self.reconcile(project_id)
        with self.repository._write():
            proposal = self._proposal(proposal_id)
            self.repository._expected(proposal, expected_revision)
            project = self.repository._project(proposal["project_id"])
            self.repository._expected(project, expected_project_revision)
            if proposal["status"] != "pending" or proposal["base_revision"] != expected_project_revision:
                raise KnowledgeConflict("proposal no longer matches current project state")
            self._meeting(proposal["meeting_id"], project["id"])
            timestamp = time.time_ns() // 1_000_000
            items = [self._apply(project["id"], change, meeting_id=proposal["meeting_id"],
                                 origin="confirmed_proposal" if proposal["origin"] == "ai" else "user", timestamp=timestamp)
                     for change in proposal["changes"]]
            self.repository._conn.execute("UPDATE project_change_proposals SET status='confirmed',revision=revision+1,result_item_ids_json=?,updated_at_ms=? WHERE id=?",
                                          (_json([item["id"] for item in items]), timestamp, proposal_id))
            self._touch_state(project["id"], timestamp)
            return {"proposal": self._proposal(proposal_id), "items": items, "project_revision": self.repository._project(project["id"])["revision"]}

    def reject(self, proposal_id: str, *, expected_revision: int) -> dict[str, Any]:
        with self.repository._write():
            proposal = self._proposal(proposal_id)
            self.repository._expected(proposal, expected_revision)
            if proposal["status"] not in {"pending", "needs_review"}:
                raise KnowledgeConflict("proposal has already been resolved")
            self.repository._conn.execute("UPDATE project_change_proposals SET status='rejected',revision=revision+1,updated_at_ms=? WHERE id=?",
                                          (time.time_ns() // 1_000_000, proposal_id))
            return self._proposal(proposal_id)

    def proposals(self, project_id: str, *, status: str | None = None, limit: int = 100, cursor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self.reconcile(project_id)
        with self.repository._lock:
            self.repository._project(project_id)
            clauses, parameters = ["project_id=?"], [project_id]
            if status is not None:
                if status not in {"pending", "confirmed", "rejected", "needs_review"}:
                    raise ValueError("invalid proposal status")
                clauses.append("status=?")
                parameters.append(status)
            page = self.repository._page("project_change_proposals", clauses=clauses, parameters=parameters, limit=limit, cursor=cursor)
            page["items"] = [self._proposal(item["id"]) for item in page["items"]]
            return page

    def get_proposal(self, proposal_id: str) -> dict[str, Any]:
        with self.repository._lock:
            project_id = self._proposal(proposal_id)["project_id"]
        self.reconcile(project_id)
        with self.repository._lock:
            return self._proposal(proposal_id)

    def history(self, item_id: str, *, before_revision: int | None = None, limit: int = 100) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise ValueError("invalid history limit")
        with self.repository._lock:
            self._item(item_id)
            rows = self.repository._conn.execute("SELECT snapshot_json,reason,created_at_ms,revision FROM project_state_history "
                                                  "WHERE item_id=? AND revision<? ORDER BY revision DESC LIMIT ?",
                                                  (item_id, before_revision if before_revision is not None else 2**63 - 1, limit + 1)).fetchall()
            return {"items": [{"snapshot": json.loads(row["snapshot_json"]), "reason": row["reason"], "created_at_ms": row["created_at_ms"]}
                               for row in rows[:limit]], "next_before_revision": rows[limit - 1]["revision"] if len(rows) > limit else None}

    def for_meeting(self, meeting_id: str, *, limit: int = 40) -> dict[str, Any]:
        context = self.repository.get_meeting_context(meeting_id)
        if context["project_id"] is None or context["selection_mode"] == "none":
            return {"project_id": None, "decisions": [], "unfinished_tasks": [], "open_questions": [], "truncated": False}
        project_id = context["project_id"]
        self.reconcile(project_id)
        with self.repository._lock:
            rows = self.repository._conn.execute("SELECT id FROM project_state_items WHERE project_id=? AND status IN ('active','unknown') "
                                                  "ORDER BY effective_at_ms DESC,id DESC", (project_id,)).fetchall()
            items = []
            for row in rows:
                item = self._item(row["id"])
                allowed = bool(item["source_refs"]) or (context["folder_id"] is None and context["selection_mode"] == "project")
                for ref in item["source_refs"]:
                    if ref["source_type"] == "document":
                        try:
                            self.repository.validate_evidence(ref, meeting_id=meeting_id)
                        except (KnowledgeScopeError, KeyError):
                            allowed = False
                    elif context["folder_id"] is not None or context["selection_mode"] == "documents":
                        allowed = False  # Direct review sources have no document-folder mapping.
                if allowed:
                    items.append(item)
            visible = items[:max(1, min(limit, 100))]
            return {"project_id": project_id, "project_revision": self.repository._project(project_id)["revision"],
                    "decisions": [item for item in visible if item["type"] == "decision"],
                    "unfinished_tasks": [item for item in visible if item["type"] == "task"],
                    "open_questions": [item for item in visible if item["type"] == "question"], "truncated": len(items) > len(visible)}

    def brief_preview(self, project_id: str, *, folder_id: str | None = None,
                      document_ids: Sequence[str] | None = None, limit: int = 20) -> dict[str, Any]:
        """A bounded, confirmed snapshot for a real selection before a meeting exists."""
        if isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("invalid brief limit")
        if document_ids is not None and (not isinstance(document_ids, (list, tuple)) or len(document_ids) > 3000
                                         or any(not isinstance(value, str) or not value for value in document_ids)):
            raise ValueError("invalid brief selection")
        selected = None if document_ids is None else list(dict.fromkeys(document_ids))
        with self.repository._write():
            self.repository._project(project_id)
            folders = self.repository._folder_ids(project_id, folder_id) if folder_id is not None else None
            for document_id in selected or []:
                document = self.repository._get("knowledge_documents", document_id)
                if document["project_id"] != project_id or folders is not None and document["folder_id"] not in folders:
                    raise KnowledgeScopeError("selected document is outside the brief scope")
            self.reconcile(project_id)
            project = self.repository._project(project_id)
            result = {"project_id": project_id, "project_name": project["name"], "project_revision": project["revision"],
                      "folder_id": folder_id, "document_ids": selected, "generated_at_ms": time.time_ns() // 1_000_000,
                      "decisions": [], "unfinished_tasks": [], "open_questions": [], "needs_review": [],
                      "needs_review_documents": [], "truncated": False}
            if selected == []:
                return result

            def in_scope(refs: Sequence[Mapping[str, Any]]) -> bool:
                if not refs:
                    return folders is None and selected is None
                for ref in refs:
                    if ref.get("project_id") != project_id:
                        return False
                    if ref.get("source_type") != "document":
                        if folders is not None or selected is not None:
                            return False  # Raw meeting sources have no document-folder membership.
                        continue
                    try:
                        document = self.repository._get("knowledge_documents", str(ref.get("document_id") or ""))
                    except KeyError:
                        return False
                    if document["project_id"] != project_id or folders is not None and document["folder_id"] not in folders:
                        return False
                    if selected is not None and document["id"] not in selected:
                        return False
                return True

            rows = self.repository._conn.execute("SELECT id FROM project_state_items WHERE project_id=? "
                "AND status IN ('active','unknown','needs_review') ORDER BY effective_at_ms DESC,id DESC LIMIT 501",
                (project_id,)).fetchall()
            groups: list[list[dict[str, Any]]] = [[], [], [], []]
            for row in rows[:500]:
                item = self._item(row["id"])
                if not in_scope(item["source_refs"]):
                    continue
                index = 3 if item["status"] == "needs_review" else {"decision": 0, "task": 1, "question": 2}[item["type"]]
                groups[index].append(item)
            # Keep conclusions, open work and warnings represented even if one type dominates.
            ordered = [(index, group[offset]) for offset in range(max((len(group) for group in groups), default=0))
                       for index, group in enumerate(groups) if offset < len(group)]
            names = ("decisions", "unfinished_tasks", "open_questions", "needs_review")
            for index, item in ordered[:limit]:
                result[names[index]].append(item)
            result["truncated"] = len(rows) > 500 or len(ordered) > limit

            clauses, parameters = ["d.project_id=?", "d.active_version_id IS NOT NULL", "d.status!='deleted'"], [project_id]
            if folders is not None:
                clauses.append("d.folder_id IN (" + ",".join("?" for _ in folders) + ")")
                parameters.extend(folders)
            if selected is not None:
                clauses.append("d.id IN (" + ",".join("?" for _ in selected) + ")")
                parameters.extend(selected)
            document_rows = self.repository._conn.execute("SELECT d.id,v.number FROM knowledge_documents d "
                "JOIN knowledge_versions v ON v.id=d.active_version_id WHERE " + " AND ".join(clauses)
                + " ORDER BY d.updated_at_ms DESC,d.id DESC LIMIT 501", parameters).fetchall()
            for row in document_rows[:500]:
                document = self.repository._get("knowledge_documents", row["id"])
                if document["source_status"] not in {"needs_review", "deleted"}:
                    continue
                if len(result["needs_review_documents"]) >= limit:
                    result["truncated"] = True
                    continue
                result["needs_review_documents"].append({"id": document["id"], "title": document["title"],
                    "revision": document["revision"], "source_status": document["source_status"],
                    "active_version": row["number"]})
            result["truncated"] = result["truncated"] or len(document_rows) > 500
            return result

    def context_for_meeting(self, meeting_id: str, *, max_items: int = 8, max_characters: int = 2400) -> dict[str, Any]:
        """One bounded view for model callers; raw history and pending proposals never enter it."""
        maximum = max(400, min(int(max_characters), 6000))
        try:
            state = self.for_meeting(meeting_id, limit=100)
        except (KnowledgeScopeError, KeyError):
            return {"project_id": None, "project_revision": None, "items": [], "truncated": False, "error_code": "project_scope_unavailable"}
        result = {"project_id": state["project_id"], "project_revision": state.get("project_revision"), "items": [], "truncated": state["truncated"]}
        groups = [state["decisions"], state["unfinished_tasks"], state["open_questions"]]
        ordered = [group[index] for index in range(max((len(group) for group in groups), default=0)) for group in groups if index < len(group)]
        for item in ordered:
            summary = {"id": item["id"], "type": item["type"], "text": item["text"][:300], "status": item["status"],
                       "owner": item["owner"][:80] if item["owner"] else None, "due": item["due"][:80] if item["due"] else None,
                       "revision": item["revision"], "origin": item["origin"], "source_refs": [{**ref, "quote": ref["quote"][:200]}
                                                                                               for ref in item["source_refs"][:2]]}
            if len(result["items"]) >= max(1, min(max_items, 12)) or len(_json({**result, "items": [*result["items"], summary]})) > maximum:
                result["truncated"] = True
                continue
            result["items"].append(summary)
        return result

    def wiki(self, project_id: str) -> dict[str, Any]:
        self.reconcile(project_id)
        with self.repository._lock:
            project = self.repository._project(project_id)
            rows = self.repository._conn.execute("SELECT id FROM project_state_items WHERE project_id=? AND status IN ('active','unknown','needs_review') "
                                                  "ORDER BY effective_at_ms DESC,id DESC LIMIT 201", (project_id,)).fetchall()
            items = [self._item(row["id"]) for row in rows[:200]]
            sections = [("当前决定", [item for item in items if item["type"] == "decision" and item["status"] == "active"]),
                        ("未完成事项", [item for item in items if item["type"] == "task" and item["status"] in {"active", "unknown"}]),
                        ("开放问题", [item for item in items if item["type"] == "question" and item["status"] in {"active", "unknown"}]),
                        ("来源变化，待核对", [item for item in items if item["status"] == "needs_review"])]
            lines = [f"# {project['name']}\n", "此页由用户确认的项目结果生成，可从当前状态重新构建。\n"]
            for heading, grouped in sections:
                lines.append(f"\n## {heading}\n")
                if not grouped:
                    lines.append("暂无。\n")
                for item in grouped:
                    details = (f"（负责人：{item['owner'] or '未指定'}；期限：{item['due'] or '未知'}；状态：{_WIKI_STATUS_LABELS.get(item['status'], '待确认')}）"
                               if item["type"] == "task" else "")
                    lines.append(f"- {item['text']} {details}")
                    for ref in item["source_refs"]:
                        path = "/v2/knowledge/sources/" if ref["source_type"] == "document" else "/v2/knowledge/outcome-sources/"
                        query = f"?project_id={quote(project_id, safe='')}"
                        if ref["source_type"] != "document":
                            query += f"&source_type={ref['source_type']}"
                            if ref["source_type"] == "meeting_entity":
                                query += f"&meeting_id={quote(ref['meeting_id'], safe='')}"
                        lines.append(f"  [来源·版本{ref['version']}]({path}{quote(ref['source_id'], safe='')}{query})")
            return {"project_id": project_id, "project_revision": project["revision"], "markdown": "\n".join(lines),
                    "items": items, "truncated": len(rows) > 200, "rebuildable": True}

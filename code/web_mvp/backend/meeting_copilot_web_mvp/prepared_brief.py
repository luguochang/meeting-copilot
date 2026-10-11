"""Bounded, source-verified preparation for a real project selection, before a meeting exists."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .coach_contract import COACH_ROLE_CONTRACT, COACH_ROLE_CONTRACT_VERSION
from .knowledge_context import knowledge_query
from .knowledge_repository import KnowledgeConflict, KnowledgeScopeError
from .project_outcomes import ProjectOutcomes
from .realtime_answer_copilot import ground_realtime_answer
from .user_memory import SCENES, UserMemory, memory_model_projection


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class PreparedBriefSources:
    def __init__(self, repository):
        self.repository = repository
        self.outcomes = ProjectOutcomes(repository)
        self.memory = UserMemory(repository)

    def validate_selection(self, project_id, folder_id, document_ids):
        self.repository._project(project_id)
        folders = self.repository._folder_ids(project_id, folder_id) if folder_id is not None else None
        if document_ids is not None and (not isinstance(document_ids, list) or len(document_ids) > 3000
                                        or any(not isinstance(item, str) or not item for item in document_ids)):
            raise ValueError("invalid preparation document selection")
        for document_id in document_ids or []:
            self.repository._valid_document(document_id, project_id, folders)
        return folders

    def scope_fingerprint(self, selection):
        folders = self.validate_selection(**selection)
        clauses, params = ["d.project_id=?"], [selection["project_id"]]
        if selection["document_ids"] == []:
            return digest([])
        if folders is not None:
            clauses.append("d.folder_id IN (" + ",".join("?" for _ in folders) + ")")
            params.extend(sorted(folders))
        if selection["document_ids"] is not None:
            clauses.append("d.id IN (" + ",".join("?" for _ in selection["document_ids"]) + ")")
            params.extend(selection["document_ids"])
        rows = self.repository._conn.execute("SELECT d.id,d.folder_id,d.status,d.active_version_id,d.revision,v.content_hash "
            "FROM knowledge_documents d LEFT JOIN knowledge_versions v ON v.id=d.active_version_id WHERE "
            + " AND ".join(clauses) + " ORDER BY d.id LIMIT 3001", params).fetchall()
        if len(rows) > 3000:
            raise KnowledgeScopeError("preparation scope exceeds 3000 documents")
        return digest([dict(row) | {"source_status": self.repository._get("knowledge_documents", row["id"])["source_status"]} for row in rows])

    def memories(self, project_id, scene, selection):
        rows = self.repository._conn.execute("SELECT * FROM user_memories WHERE status='active' AND scene IN ('',?) "
            "AND (scope='global' OR (scope='project' AND scope_id=?)) ORDER BY CASE scope WHEN 'project' THEN 0 ELSE 1 END,"
            "CASE WHEN scene=? THEN 0 ELSE 1 END,updated_at_ms DESC,id LIMIT 201", (scene, project_id, scene)).fetchall()
        selected, slots = [], set()
        for row in rows[:200]:
            refs = json.loads(row["source_refs_json"])
            try:
                self.memory._refs(refs, row["scope"], row["scope_id"])
                for ref in refs:
                    self.validate_ref(ref, selection)
            except (ValueError, KeyError, KnowledgeScopeError):
                continue
            slot = (row["kind"], row["memory_key"])
            if slot in slots:
                continue
            slots.add(slot)
            selected.append({"source_type": "user_memory", "source_id": row["id"], "revision": row["revision"],
                "scope": row["scope"], "scope_id": row["scope_id"], "scene": row["scene"], "kind": row["kind"],
                "memory_key": row["memory_key"], "value": row["value"], "aliases": json.loads(row["aliases_json"]), "source_refs": refs})
        projection = memory_model_projection({"terms": [{"canonical": item["value"], "aliases": item["aliases"]}
            for item in selected if item["kind"] == "term"], "preferences": {item["memory_key"]: item["value"]
            for item in selected if item["kind"] == "preference"}})
        return selected, projection, len(rows) > 200

    def documents(self, selection, query, *, limit=6, scene="general"):
        if type(limit) is not int or not 1 <= limit <= 6 or not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("invalid preparation search")
        self.validate_selection(**selection)
        rows = self.repository.search(knowledge_query(query), **selection, limit=limit, memory_scene=scene)
        if not rows:
            rows = self.repository.search(knowledge_query(query), **selection, limit=limit, match_mode="any", memory_scene=scene)
        result, remaining = [], 3600
        for row in rows:
            ref = {key: row[key] for key in ("source_type", "source_id", "document_id", "project_id", "title", "version_id", "version", "content_hash", "locator")}
            ref.update(quote=row["quote"][:min(900, remaining)], context_revision=0)
            if not ref["quote"].strip():
                break
            self.validate_ref(ref, selection)
            result.append(ref)
            remaining -= len(ref["quote"])
        return result

    def validate_ref(self, ref, selection):
        if not isinstance(ref, Mapping) or ref.get("project_id") != selection["project_id"]:
            raise KnowledgeScopeError("preparation source project mismatch")
        if selection["document_ids"] == []:
            raise KnowledgeScopeError("preparation has no background scope")
        if ref.get("source_type") == "document":
            folders = self.validate_selection(**selection)
            if selection["document_ids"] is not None and ref.get("document_id") not in selection["document_ids"]:
                raise KnowledgeScopeError("preparation source outside selection")
            self.repository._valid_document(ref.get("document_id"), selection["project_id"], folders)
            return self.repository.validate_evidence(ref, project_id=selection["project_id"])
        if ref.get("source_type") != "project_state":
            raise KnowledgeScopeError("unsupported preparation source")
        item = self.outcomes._item(str(ref.get("source_id") or ""))
        brief = self.outcomes.brief_preview(**selection, limit=20)
        allowed = {row["id"] for name in ("decisions", "unfinished_tasks", "open_questions") for row in brief[name]}
        if (item["id"] not in allowed or item["revision"] != ref.get("revision") or item["project_id"] != selection["project_id"]
                or not isinstance(ref.get("quote"), str) or not ref["quote"].strip() or ref["quote"] not in item["text"]
                or any(ref.get(key) != item[key] for key in ("status", "owner", "due"))
                or ref.get("source_refs") != [{**source, "quote": source["quote"][:200]} for source in item["source_refs"][:2]]):
            raise KnowledgeScopeError("preparation state source is stale or outside scope")
        self.outcomes._refs(item["source_refs"], selection["project_id"])
        return dict(ref)

    def capture(self, project_id, *, folder_id=None, document_ids=None, goal, role="", focus=None,
                scene="project", expected_project_revision):
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 1000 or not isinstance(role, str) or len(role) > 160:
            raise ValueError("invalid preparation goal or role")
        focus = [] if focus is None else focus
        if scene not in SCENES - {""} or not isinstance(focus, list) or len(focus) > 8 or any(not isinstance(x, str) or not x.strip() or len(x) > 160 for x in focus):
            raise ValueError("invalid preparation scene or focus")
        selection = {"project_id": project_id, "folder_id": folder_id, "document_ids": None if document_ids is None else list(dict.fromkeys(document_ids))}
        self.validate_selection(**selection)
        brief = self.outcomes.brief_preview(**selection, limit=8)
        self.repository._expected(self.repository._project(project_id), expected_project_revision)
        state = [{"source_type": "project_state", "source_id": item["id"], "project_id": project_id,
            "revision": item["revision"], "quote": item["text"][:240], "type": item["type"],
            "status": item["status"], "owner": item["owner"], "due": item["due"],
            "source_refs": [{**source, "quote": source["quote"][:200]} for source in item["source_refs"][:2]]}
            for name in ("decisions", "unfinished_tasks", "open_questions") for item in brief[name]][:8]
        documents = self.documents(selection, goal[:200], scene=scene)
        memories, projection, memory_truncated = self.memories(project_id, scene, selection)
        used_memories = [item for item in memories if item["kind"] == "preference" and projection["preferences"].get(item["memory_key"]) == item["value"]
                         or item["kind"] == "term" and any(term["canonical"] == item["value"] for term in projection["terms"])]
        snapshot = {"selection": selection, "goal": goal.strip(), "role": role.strip(), "focus": focus, "scene": scene,
            "project_revision": brief["project_revision"], "scope_hash": self.scope_fingerprint(selection), "memories_hash": digest(memories),
            "memory_sources": used_memories}
        payload = {"task_mode": "meeting_preparation", "role_contract": COACH_ROLE_CONTRACT,
            "role_contract_version": COACH_ROLE_CONTRACT_VERSION, "meeting_goal": goal.strip(), "participant_role": role.strip() or None,
            "focus_points": focus, "user_memory": projection, "document_evidence": documents,
            "knowledge_context": {"project_id": project_id, "project_revision": brief["project_revision"]},
            "preparation": {"input_hash": digest(snapshot), "goal": goal.strip(), "role": role.strip(), "focus": focus,
                "scene": scene, "sources": [*documents, *state], "project_state": state,
                "context_truncated": brief["truncated"] or memory_truncated or len(used_memories) < len(memories)}}
        return snapshot, payload

    def validate_snapshot(self, snapshot):
        project = self.repository._project(snapshot["selection"]["project_id"])
        if project["revision"] != snapshot["project_revision"] or self.scope_fingerprint(snapshot["selection"]) != snapshot["scope_hash"]:
            raise KnowledgeConflict("preparation project or source scope has changed")
        memories, _, _ = self.memories(project["id"], snapshot["scene"], snapshot["selection"])
        if digest(memories) != snapshot["memories_hash"]:
            raise KnowledgeConflict("preparation memory input has changed")

    def validate_result(self, terminal, snapshot, known):
        if not isinstance(terminal, Mapping) or set(terminal) != {"input_hash", "agenda", "questions", "conditions"} or terminal["input_hash"] != digest(snapshot):
            raise ValueError("preparation terminal snapshot mismatch")
        self.validate_snapshot(snapshot)
        count = 0
        for group in ("agenda", "questions", "conditions"):
            items = terminal[group]
            if not isinstance(items, list) or len(items) > 4:
                raise ValueError("preparation item budget exceeded")
            for item in items:
                if not isinstance(item, Mapping) or set(item) != {"text", "basis", "source_refs"} or not isinstance(item["text"], str) or not item["text"].strip() or len(item["text"]) > 240:
                    raise ValueError("invalid preparation item")
                refs = item["source_refs"]
                if not isinstance(refs, list):
                    raise ValueError("invalid preparation citations")
                count += sum(1 + len(ref.get("source_refs", [])) for ref in refs if isinstance(ref, Mapping))
                if count > 12 or item["basis"] not in {"user_goal", "source"} or (item["basis"] == "user_goal" and (group == "conditions" or refs)) or item["basis"] == "source" and not refs:
                    raise ValueError("preparation basis requires verified sources")
                for ref in refs:
                    if not any(isinstance(ref, Mapping) and isinstance(ref.get("quote"), str) and ref["quote"] in json.loads(seed)["quote"]
                               and canonical({key: value for key, value in ref.items() if key != "quote"})
                               == canonical({key: value for key, value in json.loads(seed).items() if key != "quote"}) for seed in known):
                        raise ValueError("preparation source was not registered by host")
                    self.validate_ref(ref, snapshot["selection"])
                if item["basis"] == "user_goal" and (group == "questions" and not item["text"].rstrip().endswith(("?", "？"))
                    or group == "agenda" and not re.match(r"^(确认|明确|核对|讨论|梳理|对齐|评估|了解|准备|列出|比较|选择|制定|Discuss\b|Clarify\b|Review\b|Verify\b|Plan\b)", item["text"].strip(), re.IGNORECASE)):
                    raise ValueError("goal-only items must be plans or explicit questions, not assertions")
                evidence = [quote for ref in refs for quote in [ref["quote"], *[source["quote"] for source in ref.get("source_refs", [])]]] if refs else [snapshot["goal"], snapshot["role"], *snapshot["focus"]]
                # These are confirmed state properties, never fabricated transcript/document quotations.
                evidence.extend((f"负责人为{ref['owner']}；" if ref.get("owner") else "") + (f"期限{ref['due']}" if ref.get("due") else "")
                                for ref in refs if ref["source_type"] == "project_state")
                if ground_realtime_answer(item["text"], evidence_texts=evidence).removed_claim_types:
                    raise ValueError("preparation contains unsupported material facts")
        if not terminal["agenda"] or not terminal["questions"]:
            raise ValueError("preparation requires an agenda and a question")
        return json.loads(canonical(terminal))

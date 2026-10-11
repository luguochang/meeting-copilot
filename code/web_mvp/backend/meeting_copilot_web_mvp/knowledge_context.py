"""Bounded local sources shared by answer lanes; no provider calls or global search."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Mapping, Sequence

from .knowledge_repository import KnowledgeRepository, KnowledgeScopeError
from .source_library_provenance import library_source_is_current


MAX_KNOWLEDGE_SOURCES = 6
MAX_KNOWLEDGE_CHARACTERS = 3_600
MAX_QUOTE_CHARACTERS = 900
MAX_COMPLETE_MATERIAL_SOURCES = 12
MAX_COMPLETE_MATERIAL_CHARACTERS = 8_000
_QUESTION_WORDS = re.compile(r"(?:请问|帮我|能不能|是否|为什么|怎么样|怎么|什么|多少|哪些|哪个|何时|时候|目前|现在|一下|是多少|是什么)")


def knowledge_query(question: str) -> str:
    return _QUESTION_WORDS.sub(" ", question).strip()[:200] or question.strip()[:200]


def retrieve_meeting_knowledge(repository: KnowledgeRepository, meeting_id: str, question: str, *,
                               memory_scene: str = "general", include_attachments: bool = False,
                               prefer_complete_attachments: bool = False) -> list[dict[str, Any]]:
    query = knowledge_query(question)
    if not query and not (include_attachments and prefer_complete_attachments):
        return []
    context = repository.get_meeting_context(meeting_id)
    project_items = []
    try:
        if query:
            project_items = repository.search_for_meeting(meeting_id, query, limit=MAX_KNOWLEDGE_SOURCES, memory_scene=memory_scene)
        if query and not project_items:
            # Keyword OR is only a bounded candidate recall inside the already
            # selected scope. It never treats a retrieved passage as a conclusion.
            project_items = repository.search_for_meeting(meeting_id, query, limit=MAX_KNOWLEDGE_SOURCES, match_mode="any",
                                                          memory_scene=memory_scene)
    except (KeyError, KnowledgeScopeError):
        pass
    from .meeting_materials import MeetingMaterialsRepository, material_input_context, validate_attachment_evidence

    with repository._lock:
        materials_binding = material_input_context(repository._conn, meeting_id) if include_attachments else {}
    material_items = []
    complete_materials = False
    if materials_binding:
        materials = MeetingMaterialsRepository(repository)
        try:
            if prefer_complete_attachments:
                material_items = materials.preview_background(
                    meeting_id, max_characters=MAX_COMPLETE_MATERIAL_CHARACTERS,
                    limit=MAX_COMPLETE_MATERIAL_SOURCES, max_files=6, max_quote_characters=None,
                )
                complete_materials = bool(material_items)
            if query and not material_items:
                material_items = materials.search_for_meeting(meeting_id, query, limit=MAX_KNOWLEDGE_SOURCES)
            if not material_items:
                material_items = materials.preview_background(meeting_id)
        except (KeyError, KnowledgeScopeError):
            return []
    items = []
    if complete_materials:
        # Reserve the bounded whole selection first: interleaving project hits
        # must not turn a purported full attachment read into a partial read.
        items = material_items + project_items
    else:
        # Relevance retrieval keeps the existing shared budget and interleaving.
        for index in range(max(len(project_items), len(material_items))):
            if index < len(material_items):
                items.append(material_items[index])
            if index < len(project_items):
                items.append(project_items[index])
    result: list[dict[str, Any]] = []
    remaining = MAX_COMPLETE_MATERIAL_CHARACTERS if complete_materials else MAX_KNOWLEDGE_CHARACTERS
    source_limit = MAX_COMPLETE_MATERIAL_SOURCES if complete_materials else MAX_KNOWLEDGE_SOURCES
    for item in items[:source_limit]:
        quote_limit = remaining if complete_materials and item["source_type"] == "meeting_attachment" else min(MAX_QUOTE_CHARACTERS, remaining)
        quote = item["quote"][:quote_limit]
        if not (complete_materials and item["source_type"] == "meeting_attachment"):
            quote = quote.strip()
        if not quote.strip():
            break
        keys = ("source_type", "source_id", "title", "version_id", "version", "content_hash", "locator")
        keys += ("set_id", "meeting_id", "attachment_id", "context_revision") if item["source_type"] == "meeting_attachment" else ("document_id", "project_id")
        evidence = {key: item[key] for key in keys}
        evidence.update(quote=quote)
        if item["source_type"] == "document":
            evidence["context_revision"] = context["context_revision"]
        # Catch a scope/version replacement during retrieval before use.
        try:
            if evidence["source_type"] == "meeting_attachment":
                with repository._lock:
                    validate_attachment_evidence(repository._conn, meeting_id, evidence)
            else:
                repository.validate_evidence(evidence, meeting_id=meeting_id)
        except (KeyError, KnowledgeScopeError):
            return []
        result.append(evidence)
        remaining -= len(quote)
    if repository.get_meeting_context(meeting_id)["context_revision"] != context["context_revision"]:
        return []
    with repository._lock:
        if include_attachments and material_input_context(repository._conn, meeting_id) != materials_binding:
            return []
    return result


def format_source_evidence(evidence: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for item in evidence:
        if item.get("source_type") in {"document", "meeting_attachment"}:
            locator = json.dumps(item.get("locator") or {}, ensure_ascii=False, separators=(",", ":"))
            lines.append(f"[资料:{item['source_id']}] {item['title']} · 版本{item['version']} · {locator}\n{item['quote']}")
        else:
            lines.append(f"[{item['segment_id']} {int(item.get('start_ms') or 0) // 1000}s] {item['quote']}")
    return "\n".join(lines)


def source_evidence_state(connection: sqlite3.Connection, meeting_id: str, evidence: Sequence[Mapping[str, Any]]) -> str:
    """Validate on the caller's SQLite transaction, avoiding cross-repository lock races."""
    unknown = not evidence
    for item in evidence:
        if item.get("source_type") == "meeting_attachment":
            from .meeting_materials import attachment_source_state

            if attachment_source_state(connection, meeting_id, item) != "valid":
                return "stale"
            continue
        if item.get("source_type") != "document":
            if not item.get("evidence_hash") or item.get("revision") is None:
                unknown = True  # Legacy history has no verifiable version.
                continue
            row = connection.execute(
                "SELECT evidence_hash,revision,normalized_text FROM transcript_segments WHERE meeting_id=? AND segment_id=?",
                (meeting_id, item.get("segment_id")),
            ).fetchone()
            quote = item.get("quote")
            if row is None or row["evidence_hash"] != item["evidence_hash"] or row["revision"] != item["revision"] or not isinstance(quote, str) or not quote.strip() or quote not in row["normalized_text"]:
                return "stale"
            continue
        context = connection.execute("SELECT * FROM meeting_knowledge_context WHERE meeting_id=?", (meeting_id,)).fetchone()
        if context is None or context["context_revision"] != item.get("context_revision") or context["project_id"] != item.get("project_id") or context["selection_mode"] == "none":
            return "stale"
        if context["selection_mode"] == "documents" and item.get("document_id") not in json.loads(context["selected_document_ids_json"]):
            return "stale"
        row = connection.execute(
            "SELECT d.status,d.active_version_id,d.folder_id,v.document_id,v.number,v.content_hash,v.confirmed_at_ms,"
            "s.text,s.locator_json,p.archived_at_ms FROM knowledge_spans s "
            "JOIN knowledge_versions v ON v.id=s.version_id JOIN knowledge_documents d ON d.id=v.document_id "
            "JOIN knowledge_projects p ON p.id=d.project_id WHERE s.id=? AND d.id=? AND d.project_id=? AND v.id=?",
            (item.get("source_id"), item.get("document_id"), item.get("project_id"), item.get("version_id")),
        ).fetchone()
        if row is None or row["status"] != "active" or row["active_version_id"] != item.get("version_id") or row["confirmed_at_ms"] is None or row["archived_at_ms"] is not None:
            return "stale"
        if not library_source_is_current(connection, str(item.get("document_id") or ""), item.get("version_id")):
            return "stale"
        quote = item.get("quote")
        if not isinstance(quote, str) or not quote.strip() or quote not in row["text"] or row["number"] != item.get("version") or row["content_hash"] != item.get("content_hash") or json.loads(row["locator_json"]) != item.get("locator"):
            return "stale"
        if context["folder_id"] is not None:
            in_folder = connection.execute(
                "WITH RECURSIVE folders(id) AS (SELECT id FROM knowledge_folders WHERE id=? AND project_id=? "
                "UNION ALL SELECT f.id FROM knowledge_folders f JOIN folders parent ON f.parent_id=parent.id WHERE f.project_id=?) "
                "SELECT 1 FROM folders WHERE id=?",
                (context["folder_id"], context["project_id"], context["project_id"], row["folder_id"]),
            ).fetchone()
            if in_folder is None:
                return "stale"
    return "unknown" if unknown else "valid"

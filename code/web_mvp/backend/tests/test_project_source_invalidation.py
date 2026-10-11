from __future__ import annotations

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.user_memory import capture_input_context, input_context_state
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.mark.parametrize("mutation", ["final", "entity_edit", "entity_status", "entity_model", "entity_delete"])
def test_original_source_mutation_atomically_marks_project_item_and_input_stale(tmp_path, mutation):
    persistence = V2Persistence(tmp_path / "project.db")
    library = None
    try:
        with persistence._write_transaction():
            persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('source','live',100,100)")
            persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('current','live',100,100)")
        library = KnowledgeRepository(persistence.database_path)
        project = library.create_project("项目")
        library.bind_meeting_context("source", project["id"])
        library.bind_meeting_context("current", project["id"])
        service = ProjectOutcomes(library)
        if mutation == "final":
            final = persistence.save_user_final_document(meeting_id="source", document_kind="minutes", expected_revision=0,
                content={"markdown": "预算五万元。"}, now_ms=200)
            ref = service.source(final["document_id"], project["id"], source_type="review_document")
        else:
            persistence.create_user_entity(meeting_id="source", entity_id="fact", kind="decision_candidate", text="预算五万元。", evidence=[], now_ms=200)
            entity = persistence.set_entity_status(meeting_id="source", entity_id="fact", status="confirmed", now_ms=250)
            ref = service.source("fact", project["id"], source_type="meeting_entity", meeting_id="source")
        item = service.create(project["id"], kind="decision", text="预算五万元。", source_refs=[ref], expected_project_revision=library.get_project(project["id"])["revision"])
        revision = library.get_project(project["id"])["revision"]
        proposal = service.propose(project["id"], [{"action": "create", "type": "question", "text": "预算是否足够？", "source_refs": [ref]}], expected_project_revision=revision, meeting_id="source")
        binding = capture_input_context(persistence._conn, "current")
        context_revision = library.get_meeting_context("current")["context_revision"]
        if mutation == "final":
            persistence.save_user_final_document(meeting_id="source", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "预算八万元。"}, now_ms=400)
        elif mutation == "entity_edit":
            persistence.update_entity_fields(meeting_id="source", entity_id="fact", fields={"text": "预算八万元。"}, expected_version=entity["version"], now_ms=400)
        elif mutation == "entity_status":
            persistence.set_entity_status(meeting_id="source", entity_id="fact", status="dismissed", now_ms=400)
        elif mutation == "entity_delete":
            with persistence._write_transaction():
                persistence._purge_all_meeting_data_locked("source", now_ms=400)
        else:
            with persistence._write_transaction():
                persistence._upsert_entity_locked("source", "decision_candidate", {"id": "fact", "text": "模型改为预算八万元。", "status": "confirmed", "updated_at_ms": 400}, current_transcript_seq=0)
        # Read raw rows before any lazy projection getter can reconcile them.
        stored = persistence._conn.execute("SELECT status,revision FROM project_state_items WHERE id=?", (item["id"],)).fetchone()
        assert tuple(stored) == ("needs_review", item["revision"] + 1)
        assert library.get_project(project["id"])["revision"] == revision + 1
        assert library.get_meeting_context("current")["context_revision"] == context_revision
        assert input_context_state(persistence._conn, "current", binding) == "stale"
        stored_proposal = persistence._conn.execute("SELECT status FROM project_change_proposals WHERE id=?", (proposal["id"],)).fetchone()
        assert stored_proposal is None if mutation == "entity_delete" else stored_proposal[0] == "needs_review"
        assert len(service.history(item["id"])["items"]) == 2
    finally:
        if library is not None:
            library.close()
        persistence.close()


def test_full_purge_removes_only_meeting_memory_and_unconfirmed_candidates_and_keeps_confirmed_knowledge(tmp_path):
    from meeting_copilot_web_mvp.user_memory import UserMemory

    persistence = V2Persistence(tmp_path / "scopes.db")
    library = None
    try:
        with persistence._write_transaction():
            persistence._conn.executemany("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'ended',100,100)", [("source",), ("other",)])
        library = KnowledgeRepository(persistence.database_path)
        project = library.create_project("项目")
        library.bind_meeting_context("source", project["id"])
        library.bind_meeting_context("other", project["id"])
        memory = UserMemory(library)
        removed = memory.create(scope="meeting", scope_id="source", kind="term", memory_key="本场术语", value="本场敏感资料")
        excluded_fingerprint = persistence._conn.execute("SELECT fingerprint FROM user_memories WHERE id=?", (removed["id"],)).fetchone()[0]
        retained = [memory.create(scope=scope, scope_id=scope_id, kind="term", memory_key=key, value=key)
                    for scope, scope_id, key in [("global", "", "全局术语"), ("project", project["id"], "项目术语"), ("meeting", "other", "另一场术语")]]
        persistence.create_note(note_id="note", meeting_id="source", title="独立笔记", body="用户独立保存的素材", source_kind="manual", source_message_id=None, evidence=[], now_ms=200)
        persistence.create_user_entity(meeting_id="source", entity_id="same-id", kind="decision_candidate", text="原会议决定", evidence=[], now_ms=200)
        persistence.set_entity_status(meeting_id="source", entity_id="same-id", status="confirmed", now_ms=220)
        persistence.create_user_entity(meeting_id="other", entity_id="same-id", kind="decision_candidate", text="另一会议决定", evidence=[], now_ms=200)
        persistence.set_entity_status(meeting_id="other", entity_id="same-id", status="confirmed", now_ms=220)
        outcomes = ProjectOutcomes(library)
        ref = outcomes.source("same-id", project["id"], source_type="meeting_entity", meeting_id="source")
        other_ref = outcomes.source("same-id", project["id"], source_type="meeting_entity", meeting_id="other")
        confirmed = outcomes.propose(project["id"], [{"type": "decision", "text": "原会议决定", "source_refs": [ref]}], meeting_id="source", expected_project_revision=library.get_project(project["id"])["revision"])
        item = outcomes.confirm(confirmed["id"], expected_revision=1, expected_project_revision=confirmed["base_revision"])["items"][0]
        other_item = outcomes.create(project["id"], kind="decision", text="另一会议决定", source_refs=[other_ref], expected_project_revision=library.get_project(project["id"])["revision"])
        pending = outcomes.propose(project["id"], [{"type": "question", "text": "原来源是否有效", "source_refs": [ref]}], meeting_id="source", expected_project_revision=library.get_project(project["id"])["revision"])
        with persistence._write_transaction():
            counts = persistence._purge_all_meeting_data_locked("source", now_ms=400)
        assert counts["user_memories"] == 1 and counts["project_change_proposals"] == 1
        rows = persistence._conn.execute("SELECT id FROM user_memories").fetchall()
        assert {row[0] for row in rows} == {value["id"] for value in retained}
        assert persistence._conn.execute("SELECT 1 FROM memory_exclusions WHERE fingerprint=?", (excluded_fingerprint,)).fetchone()
        assert persistence.get_note("note")["body"] == "用户独立保存的素材"
        assert persistence._conn.execute("SELECT status FROM project_change_proposals WHERE id=?", (confirmed["id"],)).fetchone()[0] == "confirmed"
        assert persistence._conn.execute("SELECT 1 FROM project_change_proposals WHERE id=?", (pending["id"],)).fetchone() is None
        assert persistence._conn.execute("SELECT status FROM project_state_items WHERE id=?", (item["id"],)).fetchone()[0] == "needs_review"
        assert persistence._conn.execute("SELECT status FROM project_state_items WHERE id=?", (other_item["id"],)).fetchone()[0] == "active"
    finally:
        if library is not None:
            library.close()
        persistence.close()


def test_numeric_values_in_structured_human_final_remain_citable(tmp_path):
    persistence = V2Persistence(tmp_path / "numeric.db")
    library = None
    try:
        with persistence._write_transaction():
            persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','ended',100,100)")
        final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0, content={"amount": 50000}, now_ms=200)
        library = KnowledgeRepository(persistence.database_path)
        project = library.create_project("项目")
        library.bind_meeting_context("meeting", project["id"])
        assert ProjectOutcomes(library).source(final["document_id"], project["id"], source_type="review_document")["quote"] == "50000"
    finally:
        if library is not None:
            library.close()
        persistence.close()

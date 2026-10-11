from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from meeting_copilot_web_mvp import application_schema
from meeting_copilot_web_mvp.application_schema import bootstrap_application_schema
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError


@pytest.fixture
def library(tmp_path: Path):
    repository = KnowledgeRepository(tmp_path / "knowledge.db")
    yield repository
    repository.close()


def _meeting(library: KnowledgeRepository, meeting_id: str = "meeting-1") -> str:
    with sqlite3.connect(library.database_path) as connection:
        connection.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'live',1,1)", (meeting_id,))
    return meeting_id


def _active(library: KnowledgeRepository, project_id: str, content: str = "预算已经批准，交付日期是十月十五日。", **kwargs):
    document = library.create_document(project_id, "产品资料", **kwargs)
    version = library.add_version(document["id"], content)
    document = library.confirm_version(document["id"], version["id"], expected_revision=document["revision"] + 1)
    return document, version


def test_confirmed_only_and_versioned_evidence_invalidates_after_edit(library):
    project = library.create_project("客户项目")
    document = library.create_document(project["id"], "预算")
    draft = library.add_version(document["id"], "预算是三万元。")
    assert library.search("预算", project_id=project["id"]) == []
    with pytest.raises(KnowledgeConflict):
        library.confirm_version(document["id"], draft["id"], expected_revision=document["revision"])
    document = library.confirm_version(document["id"], draft["id"], expected_revision=document["revision"] + 1)
    evidence = library.search("三万", project_id=project["id"])[0]
    assert evidence["source_type"] == "document"
    assert evidence["version"] == 1
    assert evidence["locator"]["type"] == "converted_text"
    assert library.validate_evidence(evidence, project_id=project["id"]) == evidence
    assert library.read_source(evidence["source_id"], project_id=project["id"])["quote"] == evidence["quote"]
    second = library.add_version(document["id"], "预算修正为五万元。")
    assert library.get_version(document["id"])["id"] == draft["id"]
    assert library.search("五万", project_id=project["id"]) == []
    assert library.search("三万", project_id=project["id"])
    library.confirm_version(document["id"], second["id"], expected_revision=document["revision"] + 1)
    assert library.search("三万", project_id=project["id"]) == []
    assert library.search("五万", project_id=project["id"])
    with pytest.raises(KnowledgeScopeError, match="superseded"):
        library.validate_evidence(evidence, project_id=project["id"])
    with pytest.raises(KnowledgeScopeError, match="superseded"):
        library.read_source(evidence["source_id"], project_id=project["id"])
    history = library.list_versions(document["id"], limit=1)
    assert history["items"][0]["number"] == 2
    assert library.list_versions(document["id"], before_number=history["next_before_number"])["items"][0]["number"] == 1


def test_folder_tree_scope_cross_project_cycle_and_stable_move(library):
    project = library.create_project("研发")
    other = library.create_project("销售")
    parent = library.create_folder(project["id"], "规划")
    child = library.create_folder(project["id"], "十月", parent_id=parent["id"])
    other_folder = library.create_folder(other["id"], "规划")
    document, version = _active(library, project["id"], folder_id=child["id"])
    assert library.search("交付", project_id=project["id"], folder_id=parent["id"])
    with pytest.raises(KnowledgeScopeError):
        library.create_document(project["id"], "不应跨项目", folder_id=other_folder["id"])
    with pytest.raises(KnowledgeScopeError):
        library.update_folder(parent["id"], parent_id=child["id"])
    with pytest.raises(KnowledgeScopeError):
        library.move_document(document["id"], other_folder["id"])
    moved = library.move_document(document["id"], None, title="新的资料名称")
    assert moved["id"] == document["id"] and moved["active_version_id"] == version["id"]
    assert library.search("交付", project_id=project["id"], folder_id=parent["id"]) == []
    assert library.search("资料名称", project_id=project["id"])[0]["document_id"] == moved["id"]


def test_meeting_scope_empty_selection_revision_and_stale_evidence(library):
    meeting = _meeting(library)
    project = library.create_project("项目一")
    document, _ = _active(library, project["id"])
    second, _ = _active(library, project["id"], "预算不是项目二的数据。")
    other = library.create_project("项目二")
    unrelated, _ = _active(library, other["id"])
    assert library.search_for_meeting(meeting, "预算") == []
    context = library.bind_meeting_context(meeting, project["id"], document_ids=[])
    assert context["selection_mode"] == "documents" and library.search_for_meeting(meeting, "预算") == []
    with pytest.raises(KnowledgeScopeError):
        library.bind_meeting_context(meeting, project["id"], document_ids=[unrelated["id"]])
    with pytest.raises(KnowledgeScopeError):
        library.search("预算", project_id=project["id"], document_ids=[unrelated["id"]])
    context = library.bind_meeting_context(meeting, project["id"], document_ids=[document["id"]])
    evidence = library.search_for_meeting(meeting, "预算")[0]
    assert library.validate_evidence(evidence, meeting_id=meeting)
    context = library.bind_meeting_context(meeting, project["id"], document_ids=[second["id"]], expected_revision=context["context_revision"])
    with pytest.raises(KnowledgeScopeError, match="no longer selected"):
        library.validate_evidence(evidence, meeting_id=meeting)
    library.move_document(second["id"], None)
    assert library.get_meeting_context(meeting)["context_revision"] > context["context_revision"]
    with pytest.raises(KnowledgeConflict):
        library.bind_meeting_context(meeting, project["id"], expected_revision=context["context_revision"])
    assert library.bind_meeting_context(meeting, project["id"])["selection_mode"] == "project"
    assert library.bind_meeting_context(meeting)["selection_mode"] == "none"
    assert library.search_for_meeting(meeting, "预算") == []


def test_folder_context_move_and_disable_delete_archive_remove_retrieval(library):
    meeting = _meeting(library)
    project = library.create_project("分类项目")
    folder = library.create_folder(project["id"], "合同")
    document, _ = _active(library, project["id"], folder_id=folder["id"])
    library.bind_meeting_context(meeting, project["id"], folder_id=folder["id"])
    evidence = library.search_for_meeting(meeting, "预算")[0]
    library.move_document(document["id"], None)
    with pytest.raises(KnowledgeScopeError, match="selected folder"):
        library.validate_evidence(evidence, meeting_id=meeting)
    library.set_document_status(document["id"], "disabled")
    assert library.search("预算", project_id=project["id"]) == []
    with pytest.raises(KnowledgeScopeError):
        library.validate_evidence(evidence, project_id=project["id"])
    library.set_document_status(document["id"], "active")
    assert library.search("预算", project_id=project["id"])
    library.set_document_status(document["id"], "deleted")
    assert library.list_documents(project["id"])["items"] == []
    assert len(library.list_documents(project["id"], status="deleted")["items"]) == 1
    with pytest.raises(KnowledgeScopeError):
        library.set_document_status(document["id"], "active")
    _active(library, project["id"])
    library.update_project(project["id"], archived=True)
    with pytest.raises(KnowledgeScopeError, match="archived"):
        library.search("预算", project_id=project["id"])
    assert library.list_projects()["items"] == []


def test_evidence_rejects_fabricated_quote_locator_hash_and_unconfirmed(library):
    project = library.create_project("证据项目")
    document, _ = _active(library, project["id"])
    evidence = library.search("预算", project_id=project["id"])[0]
    for replacement in ({"quote": "预算批准了100万元"}, {"locator": {"type": "pdf_page", "page": 3}},
                        {"content_hash": "fake"}, {"version": 99}, {"source_type": "transcript"}, {"project_id": "unrelated"}):
        with pytest.raises(KnowledgeScopeError):
            library.validate_evidence({**evidence, **replacement}, project_id=project["id"])
    draft = library.create_document(project["id"], "未确认")
    library.add_version(draft["id"], "预算")
    with pytest.raises(KnowledgeScopeError):
        library.bind_meeting_context(_meeting(library), project["id"], document_ids=[draft["id"]])
    assert library.get_document(document["id"])["status"] == "active"
    library.update_document(document["id"], title="重新命名")
    assert library.validate_evidence(evidence, project_id=project["id"])["title"] == "重新命名"


@pytest.mark.parametrize("metadata", [
    {"filename": "../outside.txt"}, {"filename": "C:\\outside.txt"}, {"filename": "bad\nname.txt"},
    {"original_relative_path": "knowledge_assets/../../bad"}, {"original_relative_path": "C:/bad"},
    {"original_sha256": "not-a-hash"}, {"content_type": "text/plain\nInjected"}, {"file_size_bytes": 21 * 1024 * 1024},
])
def test_document_metadata_is_bounded_and_safe(library, metadata):
    project = library.create_project("安全")
    with pytest.raises(ValueError):
        library.create_document(project["id"], "上传", **metadata)


def test_3000_document_pagination_and_chinese_short_term_search(library):
    project = library.create_project("规模测试")
    start = time.perf_counter()
    for index in range(3000):
        content = f"批次{index}常规资料，预算明确。" if index != 1777 else "冰川客户合同，交付范围最终确认。"
        _active(library, project["id"], content, now_ms=100)
    indexed_seconds = time.perf_counter() - start
    ids, cursor = [], None
    while True:
        page = library.list_documents(project["id"], limit=200, cursor=cursor)
        ids.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(ids) == len(set(ids)) == 3000
    start = time.perf_counter()
    assert len(library.search("冰", project_id=project["id"])) == 1
    assert len(library.search("冰川", project_id=project["id"])) == 1
    assert len(library.search("冰川客户", project_id=project["id"])) == 1
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"synthetic_3000_docs index_seconds={indexed_seconds:.3f} three_queries_ms={elapsed_ms:.3f}")


def test_migration_13_to_14_preserves_existing_meeting_and_has_foreign_key_integrity(tmp_path, monkeypatch):
    path = tmp_path / "upgrade.db"
    with monkeypatch.context() as scoped:
        scoped.setattr(application_schema, "APPLICATION_SCHEMA_VERSION", 13)
        scoped.setattr(application_schema, "APPLICATION_MAX_SUPPORTED_SCHEMA_VERSION", 13)
        scoped.setattr(application_schema, "APPLICATION_SCHEMA_MIGRATIONS", application_schema.APPLICATION_SCHEMA_MIGRATIONS[:13])
        bootstrap_application_schema(path)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO meetings(id,state,title,created_at_ms,updated_at_ms) VALUES('kept','ended','保留标题',1,1)")
    result = bootstrap_application_schema(path)
    assert result.applied_versions == tuple(range(14, application_schema.APPLICATION_SCHEMA_VERSION + 1)) and result.backup_path is not None
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT title FROM meetings WHERE id='kept'").fetchone() == ("保留标题",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]

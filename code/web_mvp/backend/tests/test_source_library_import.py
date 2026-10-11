from __future__ import annotations

import hashlib
import json

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.source_library_import import LibrarySourceError, SourceLibraryImporter
from meeting_copilot_web_mvp.source_library_provenance import _review_source_title, library_source_is_current, source_snapshot
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def sources(tmp_path):
    persistence = V2Persistence(tmp_path / "sources.db")
    persistence.commit_final_and_enqueue(meeting_id="meeting", final_id="final", segment_id="segment", text="待确认方案",
        normalized_text="待确认方案", source_track="microphone", started_at_ms=0, ended_at_ms=100,
        evidence_hash="hash", now_ms=100, enqueue_answer=False)
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("客户甲", now_ms=200)
    library.bind_meeting_context("meeting", project["id"], now_ms=300)
    yield persistence, library, project, SourceLibraryImporter(library)
    library.close()
    persistence.close()


def note(persistence, source_kind="manual"):
    return persistence.create_note(note_id="note", meeting_id="meeting", title="会议素材", body="预算五万元，待法务批准。",
        source_kind=source_kind, source_message_id=None, evidence=[], now_ms=500)


def confirm(library, result):
    return library.confirm_version(result["document"]["id"], result["version"]["id"], expected_revision=result["document"]["revision"], now_ms=700)


@pytest.mark.parametrize(("kind", "label"), [
    ("minutes", "会议纪要"), ("decisions", "决策"), ("action_items", "待办"),
    ("risks", "风险"), ("transcript", "会议文字"),
])
def test_new_final_import_defaults_to_chinese_document_type(sources, kind, label):
    persistence, _library, _project, importer = sources
    persistence.update_meeting_title(meeting_id="meeting", title="客户需求评审", title_source="user", now_ms=450)
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind=kind,
        expected_revision=0, content={"markdown": "用户已核对的合成记录。"}, now_ms=500)
    result = importer.import_final("meeting", kind, expected_source_version=final["user_final"]["version"])
    assert result["created"] is True
    assert result["document"]["title"] == f"客户需求评审·会议终稿·{label}"
    assert result["document"]["status"] == "draft"


@pytest.mark.parametrize("saved_title", ["会议终稿·minutes", "客户预算评审结论"])
def test_reimport_preserves_legacy_or_user_named_title(sources, saved_title):
    persistence, library, _project, importer = sources
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes",
        expected_revision=0, content={"markdown": "原终稿。"}, now_ms=500)
    original = importer.import_final("meeting", "minutes", expected_source_version=1)
    existing = library.update_document(original["document"]["id"], title=saved_title,
        expected_revision=original["document"]["revision"], now_ms=600)
    persistence.update_meeting_title(meeting_id="meeting", title="后来调整的会议名", title_source="user", now_ms=650)
    replay = importer.import_final("meeting", "minutes", expected_source_version=1)
    assert replay["already_imported"] is True
    assert replay["document"]["title"] == saved_title
    persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes",
        expected_revision=final["revision"], content={"markdown": "用户补充后的终稿。"}, now_ms=800)
    newer = importer.import_final("meeting", "minutes", expected_source_version=2,
        expected_document_revision=existing["revision"])
    assert newer["created"] is False
    assert newer["document"]["title"] == saved_title
    assert newer["version"]["number"] == 2


def test_unknown_final_kind_uses_neutral_title():
    assert _review_source_title("future_kind") == "会议终稿"


def test_new_final_import_distinguishes_meetings_and_preserves_source_provenance(sources):
    persistence, library, project, importer = sources
    titles = []
    for index, title in enumerate(("需求澄清", "架构评审")):
        meeting_id = f"named-meeting-{index}"
        persistence.commit_final_and_enqueue(meeting_id=meeting_id, final_id=f"final-{index}",
            segment_id=f"segment-{index}", text="公开合成方案", normalized_text="公开合成方案",
            source_track="microphone", started_at_ms=0, ended_at_ms=100,
            evidence_hash=f"hash-{index}", now_ms=400, enqueue_answer=False)
        persistence.update_meeting_title(meeting_id=meeting_id, title=title, title_source="user", now_ms=450)
        library.bind_meeting_context(meeting_id, project["id"], now_ms=460)
        final = persistence.save_user_final_document(meeting_id=meeting_id, document_kind="minutes",
            expected_revision=0, content={"markdown": "公开合成终稿，仅等待人工入库确认。"}, now_ms=500)
        before = source_snapshot(library._conn, "review_document", final["document_id"])
        result = importer.import_final(meeting_id, "minutes", expected_source_version=1, now_ms=550)
        titles.append(result["document"]["title"])
        assert source_snapshot(library._conn, "review_document", final["document_id"]) == before
        assert result["version"]["content"] == before["content"]
        assert result["version"]["source_status"] == "current"
        locator = result["version"]["spans"][0]["locator"]
        assert locator["source_hash"] == before["source_hash"]
        assert locator["source_version"] == before["source_version"]
        assert result["document"]["source_meeting_id"] == meeting_id
        assert result["document"]["active_version_id"] is None
        assert result["document"]["status"] == "draft"
    assert titles == ["需求澄清·会议终稿·会议纪要", "架构评审·会议终稿·会议纪要"]


@pytest.mark.parametrize("meeting_title", [None, "", "  \t  "])
def test_new_untitled_final_uses_stable_id_not_import_or_updated_time(sources, meeting_title):
    persistence, _library, _project, importer = sources
    # Legacy nullable/blank titles are supported even though the current title editor rejects them.
    with persistence._write_transaction():
        persistence._conn.execute("UPDATE meetings SET title=?, updated_at_ms=1000 WHERE id='meeting'", (meeting_title,))
    for kind in ("minutes", "decisions"):
        persistence.save_user_final_document(meeting_id="meeting", document_kind=kind,
            expected_revision=0, content={"markdown": "公开合成已保存终稿。"}, now_ms=500)
    first = importer.import_final("meeting", "minutes", expected_source_version=1, now_ms=1000)
    with persistence._write_transaction():
        persistence._conn.execute("UPDATE meetings SET updated_at_ms=900000 WHERE id='meeting'")
    second = importer.import_final("meeting", "decisions", expected_source_version=1, now_ms=900000)
    prefix = first["document"]["title"].removesuffix("·会议终稿·会议纪要")
    assert prefix.startswith("未命名会议（") and len(prefix.removeprefix("未命名会议（").removesuffix("）")) == 8
    assert second["document"]["title"] == f"{prefix}·会议终稿·决策"


def test_new_final_long_meeting_title_keeps_type_suffix_within_limit(sources):
    persistence, _library, _project, importer = sources
    persistence.update_meeting_title(meeting_id="meeting", title="长" * 200, title_source="user", now_ms=450)
    persistence.save_user_final_document(meeting_id="meeting", document_kind="action_items",
        expected_revision=0, content={"markdown": "人工已确认待办。"}, now_ms=500)
    result = importer.import_final("meeting", "action_items", expected_source_version=1)
    assert result["document"]["title"] == "长" * (200 - len("·会议终稿·待办")) + "·会议终稿·待办"
    assert len(result["document"]["title"]) == 200


@pytest.mark.parametrize(("kind", "content", "expected"), [
    ("decisions", {"decisions": [{"id": "decision-1", "text": "先压测再确认。", "status": "candidate",
                                "evidenceSegmentId": "segment"}]}, "先压测再确认。\n  状态：待确认"),
    ("action_items", {"action_items": [{"text": "补充压测。", "status": "open", "owner": "王工", "deadline": "周五"}]},
     "负责人：王工\n  截止时间：周五"),
    ("risks", {"risks": [{"text": "缓存击穿。", "status": "open", "severity": "high", "business_impact": "延迟升高"}]},
     '"business_impact": "延迟升高"'),
    ("transcript", {"segments": [{"segment_id": "segment", "text": "已校对的公开合成原话。", "started_at_ms": 1000,
                                  "ended_at_ms": 3000, "speaker_label": "已有标签"}]},
     "[00:01-00:03] 已有标签：已校对的公开合成原话。"),
])
def test_new_structured_library_body_keeps_raw_source_and_pending_confirmation(sources, kind, content, expected):
    persistence, library, project, importer = sources
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind=kind,
        expected_revision=0, content=content, now_ms=500)
    before = source_snapshot(library._conn, "review_document", final["document_id"])
    result = importer.import_final("meeting", kind, expected_source_version=1)
    assert expected in result["version"]["content"]
    assert result["version"]["content"] != before["content"]
    assert source_snapshot(library._conn, "review_document", final["document_id"]) == before
    assert before["source_content"] == content
    raw_hash = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert before["source_hash"] == raw_hash
    for span in result["version"]["spans"]:
        assert span["locator"]["source_hash"] == raw_hash
        assert span["locator"]["source_version"] == 1
        assert span["locator"]["source_id"] == final["document_id"]
    assert result["document"]["status"] == "draft"
    assert result["document"]["active_version_id"] is None
    assert result["version"]["confirmed_at_ms"] is None
    assert library.search("压测", project_id=project["id"]) == []


def test_old_json_same_source_replay_is_unchanged_and_new_source_uses_readable_body_with_cas(sources, monkeypatch):
    from meeting_copilot_web_mvp import source_library_import

    persistence, library, _project, importer = sources
    original_content = {"decisions": [{"id": "old-id", "text": "原先候选方案。", "status": "candidate"}]}
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="decisions",
        expected_revision=0, content=original_content, now_ms=500)
    with monkeypatch.context() as legacy:
        legacy.setattr(source_library_import, "review_library_content", lambda _kind, content: json.dumps(content, ensure_ascii=False, indent=2))
        old = importer.import_final("meeting", "decisions", expected_source_version=1)
    confirmed = confirm(library, old)
    renamed = library.update_document(confirmed["id"], title="用户核对后的项目决定",
        expected_revision=confirmed["revision"], now_ms=800)
    saved_library_version = library.get_version(confirmed["id"], old["version"]["id"])
    replay = importer.import_final("meeting", "decisions", expected_source_version=1)
    assert replay["already_imported"] is True and replay["created"] is False
    assert replay["version"] == saved_library_version
    assert replay["version"]["content"] == old["version"]["content"]
    assert json.loads(replay["version"]["content"]) == original_content
    assert replay["document"]["revision"] == renamed["revision"]
    newer_content = {"decisions": [{"text": "人工确认的新方案。", "status": "confirmed"}]}
    persistence.save_user_final_document(meeting_id="meeting", document_kind="decisions",
        expected_revision=final["revision"], content=newer_content, now_ms=1000)
    with pytest.raises(KnowledgeConflict):
        importer.import_final("meeting", "decisions", expected_source_version=2)
    with pytest.raises(KnowledgeConflict):
        importer.import_final("meeting", "decisions", expected_source_version=2,
            expected_document_revision=renamed["revision"] - 1)
    new = importer.import_final("meeting", "decisions", expected_source_version=2,
        expected_document_revision=renamed["revision"])
    assert new["created"] is False and new["already_imported"] is False
    assert new["version"]["number"] == 2 and new["version"]["confirmed_at_ms"] is None
    assert "人工确认的新方案。\n  状态：已确认" in new["version"]["content"]
    assert new["document"]["title"] == "用户核对后的项目决定"
    assert new["document"]["active_version_id"] == old["version"]["id"]
    assert library.get_version(confirmed["id"], old["version"]["id"])["content"] == old["version"]["content"]


def test_actual_human_final_import_keeps_structure_folder_and_no_automatic_confirmation(sources):
    persistence, library, project, importer = sources
    folder = library.create_folder(project["id"], "客户访谈")
    library.bind_meeting_context("meeting", project["id"], folder_id=folder["id"])
    with pytest.raises(LibrarySourceError, match="human_final_required"):
        importer.import_final("meeting", "minutes", expected_source_version=1)
    initial = persistence.save_ai_generated_document(meeting_id="meeting", document_kind="minutes", content={"markdown": "机器候选"}, author="fake-test", now_ms=400)
    with pytest.raises(LibrarySourceError, match="human_final_required"):
        importer.import_final("meeting", "minutes", expected_source_version=1)
    human = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=initial["revision"],
        content={"conclusion": "需法务审核", "amount": 50000}, now_ms=500)
    result = importer.import_final("meeting", "minutes", expected_source_version=human["user_final"]["version"])
    assert result["document"]["folder_id"] == folder["id"]
    assert result["document"]["status"] == "draft" and result["version"]["confirmed_at_ms"] is None
    assert '"amount": 50000' in result["version"]["content"]
    locator = result["version"]["spans"][0]["locator"]
    assert locator["source_id"] == human["document_id"] and locator["source_version"] == human["user_final"]["version"]
    assert result["document"]["source_binding"] == {"source_kind": "review_document", "source_id": human["document_id"]}
    assert library.search("法务", project_id=project["id"]) == []
    confirm(library, result)
    assert library.search_for_meeting("meeting", "法务")


def test_repeat_import_does_not_overwrite_user_corrected_active_version(sources):
    persistence, library, project, importer = sources
    note(persistence)
    result = importer.import_note("note", project["id"], expected_source_version=1)
    assert result["document"]["title"] == "会议素材"
    assert result["version"]["content"] == persistence.get_note("note")["body"]
    document = confirm(library, result)
    corrected = library.add_version(document["id"], "预算五万元，仍需法务批准；这句由用户补充。", expected_revision=document["revision"], base_version_id=result["version"]["id"])
    document = library.confirm_version(document["id"], corrected["id"], expected_revision=document["revision"] + 1)
    replay = importer.import_note("note", project["id"], expected_source_version=1)
    assert replay["already_imported"] is True and replay["created"] is False
    assert replay["document"]["active_version_id"] == corrected["id"]
    assert replay["document"]["revision"] == document["revision"]
    assert len(library.list_versions(document["id"])["items"]) == 2


def test_changed_source_is_not_retrievable_and_new_import_remains_a_separate_draft(sources):
    persistence, library, project, importer = sources
    note(persistence)
    original = importer.import_note("note", project["id"], expected_source_version=1)
    old_document = confirm(library, original)
    persistence.update_note("note", expected_version=1, body="新预算八万元，需董事会批准。", now_ms=1000)
    assert library.get_document(old_document["id"])["source_status"] == "needs_review"
    assert library.search_for_meeting("meeting", "法务") == []
    with pytest.raises(KnowledgeScopeError):
        library.confirm_version(old_document["id"], original["version"]["id"], expected_revision=old_document["revision"])
    assert library.get_version(old_document["id"], original["version"]["id"])["source_status"] == "needs_review"
    with pytest.raises(KnowledgeConflict):
        importer.import_note("note", project["id"], expected_source_version=1)
    with pytest.raises(KnowledgeConflict):
        importer.import_note("note", project["id"], expected_source_version=2)
    new = importer.import_note("note", project["id"], expected_source_version=2, expected_document_revision=old_document["revision"])
    assert new["document"]["active_version_id"] == original["version"]["id"]
    assert new["version"]["number"] == 2 and new["version"]["source_status"] == "current"
    edited = library.add_version(old_document["id"], "新预算八万元，需董事会批准。用户核对。", base_version_id=new["version"]["id"], expected_revision=new["document"]["revision"])
    document = library.confirm_version(old_document["id"], edited["id"], expected_revision=new["document"]["revision"] + 1)
    assert document["source_status"] == "current"
    assert library.search_for_meeting("meeting", "董事会")
    assert "五万元" in library.get_version(document["id"], original["version"]["id"])["content"]
    reopened = KnowledgeRepository(library.database_path)
    try:
        assert reopened.get_document(document["id"])["active_version_id"] == edited["id"]
        assert library_source_is_current(reopened._conn, document["id"], edited["id"])
    finally:
        reopened.close()


@pytest.mark.parametrize("mutation", ["source_deleted", "source_archived", "library_deleted", "library_disabled"])
def test_source_or_library_deletion_cannot_resurrect_a_copy(sources, mutation):
    persistence, library, project, importer = sources
    note(persistence)
    result = importer.import_note("note", project["id"], expected_source_version=1)
    document = confirm(library, result)
    if mutation.startswith("source"):
        persistence.update_note("note", expected_version=1, status="deleted" if mutation == "source_deleted" else "archived", now_ms=1000)
    else:
        library.set_document_status(document["id"], "deleted" if mutation == "library_deleted" else "disabled")
    with pytest.raises(LibrarySourceError):
        importer.import_note("note", project["id"], expected_source_version=1)
    assert library.search_for_meeting("meeting", "法务") == []
    if mutation.startswith("source"):
        assert library.get_document(document["id"])["source_status"] in {"deleted", "needs_review"}


def test_newer_human_final_and_cross_project_checks_preserve_old_manual_content(sources):
    persistence, library, project, importer = sources
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0, content={"markdown": "确认预算五万元。"}, now_ms=500)
    old = importer.import_final("meeting", "minutes", expected_source_version=1)
    document = confirm(library, old)
    persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "确认预算八万元。"}, now_ms=1000)
    assert library.search_for_meeting("meeting", "五万元") == []
    new = importer.import_final("meeting", "minutes", expected_source_version=2, expected_document_revision=document["revision"])
    assert new["document"]["active_version_id"] == old["version"]["id"]
    assert new["version"]["content"] == "确认预算八万元。"
    other = library.create_project("客户乙")
    note(persistence)
    with pytest.raises(KnowledgeScopeError):
        importer.import_note("note", other["id"], expected_source_version=1)


def test_ask_ai_excerpt_is_only_a_draft_until_user_confirms(sources):
    persistence, library, project, importer = sources
    note(persistence, "ask_ai")
    result = importer.import_note("note", project["id"], expected_source_version=1)
    assert result["document"]["status"] == "draft"
    assert library.search_for_meeting("meeting", "预算") == []
    confirm(library, result)
    assert library.search_for_meeting("meeting", "预算")


def test_transcript_change_requires_human_final_review_and_source_purge_expires_other_meeting(sources):
    persistence, library, project, importer = sources
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0,
        content={"markdown": "确认预算五万元。"}, now_ms=500)
    result = importer.import_final("meeting", "minutes", expected_source_version=1)
    confirm(library, result)
    persistence.commit_final_and_enqueue(meeting_id="next", final_id="next-final", segment_id="next-segment", text="继续讨论",
        normalized_text="继续讨论", source_track="microphone", started_at_ms=0, ended_at_ms=100,
        evidence_hash="next-hash", now_ms=800, enqueue_answer=False)
    library.bind_meeting_context("next", project["id"])
    assert library.search_for_meeting("next", "预算")
    persistence.commit_transcript_revision(meeting_id="meeting", segment_id="segment", expected_evidence_hash="hash",
        corrected_text="暂不批准方案", revision_id="rev", now_ms=1000, evidence_remap_reason="validated_meaning_preserved_correction")
    assert library.get_document(result["document"]["id"])["source_status"] == "needs_review"
    assert library.search_for_meeting("next", "预算") == []
    current = persistence.get_review_document("meeting", "minutes")
    assert current["user_final"]["content"] == final["user_final"]["content"]
    human = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=current["revision"],
        content=current["user_final"]["content"], now_ms=1100)
    fresh = importer.import_final("meeting", "minutes", expected_source_version=human["user_final"]["version"],
        expected_document_revision=library.get_document(result["document"]["id"])["revision"])
    confirm(library, fresh)
    assert library.search_for_meeting("next", "预算")
    with persistence._write_transaction():
        persistence._purge_all_meeting_data_locked("meeting", now_ms=1200)
    assert library.get_document(result["document"]["id"])["source_status"] == "deleted"
    assert library.search_for_meeting("next", "预算") == []


def test_migration_twenty_preserves_existing_note_content_and_version(tmp_path):
    import sqlite3
    from meeting_copilot_web_mvp import application_schema
    from meeting_copilot_web_mvp.sqlite_schema import migrate_sqlite_schema

    database = tmp_path / "prior.db"
    migrate_sqlite_schema(database, application_schema.APPLICATION_SCHEMA_MIGRATIONS[:19], current_version=19, max_supported_version=19)
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO meeting_notes(note_id,title,body,source_kind,version,status,created_at_ms,updated_at_ms) "
                           "VALUES('note','用户资料','预算五万元','manual',7,'active',100,200)")
    application_schema.bootstrap_application_schema(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT title,body,version FROM meeting_notes WHERE note_id='note'").fetchone() == ("用户资料", "预算五万元", 7)
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_source_versions'").fetchone()

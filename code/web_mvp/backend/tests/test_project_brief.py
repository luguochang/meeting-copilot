from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.project_outcomes_api import mount_project_outcomes_api
from meeting_copilot_web_mvp.source_library_import import SourceLibraryImporter
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def brief(tmp_path):
    persistence = V2Persistence(tmp_path / "brief.db")
    persistence.commit_final_and_enqueue(meeting_id="previous", final_id="final", segment_id="segment", text="合成原话",
        normalized_text="合成原话", source_track="microphone", started_at_ms=0, ended_at_ms=100,
        evidence_hash="hash", now_ms=100, enqueue_answer=False)
    repository = KnowledgeRepository(persistence.database_path)
    project = repository.create_project("客户甲")
    repository.bind_meeting_context("previous", project["id"])
    service = ProjectOutcomes(repository)
    yield persistence, repository, service, project["id"]
    repository.close()
    persistence.close()


def revision(repository, project_id):
    return repository.get_project(project_id)["revision"]


def source(repository, project_id, *, folder_id=None):
    document = repository.create_document(project_id, "已核对资料", folder_id=folder_id)
    version = repository.add_version(document["id"], "预算五万元，仍需法务审核。负责人李明，周五核对。")
    document = repository.confirm_version(document["id"], version["id"], expected_revision=document["revision"] + 1)
    return document, repository.read_source(version["spans"][0]["id"], project_id=project_id)


def add(service, repository, project_id, kind, text, refs=(), **kwargs):
    return service.create(project_id, kind=kind, text=text, source_refs=list(refs),
        expected_project_revision=revision(repository, project_id), **kwargs)


def test_automatic_brief_is_confirmed_bounded_snapshot_without_creating_a_meeting_or_ai_job(brief):
    _, repository, service, project_id = brief
    _, ref = source(repository, project_id)
    add(service, repository, project_id, "decision", "预算五万元", [ref])
    add(service, repository, project_id, "task", "法务审核", [ref], owner="李明", due="周五")
    add(service, repository, project_id, "question", "法务审核是否已完成？", [ref])
    done = add(service, repository, project_id, "task", "已完成的核对", [ref], status="done")
    service.propose(project_id, [{"type": "decision", "text": "未确认预算八万元", "source_refs": [ref]}],
        expected_project_revision=revision(repository, project_id))
    before_meetings = repository._conn.execute("SELECT count(*) FROM meetings").fetchone()[0]
    before_jobs = repository._conn.execute("SELECT count(*) FROM project_reconcile_jobs").fetchone()[0]
    result = service.brief_preview(project_id)
    assert result["project_revision"] == revision(repository, project_id)
    assert result["project_name"] == "客户甲" and result["generated_at_ms"] > 0
    assert [item["text"] for item in result["decisions"]] == ["预算五万元"]
    assert result["unfinished_tasks"][0]["owner"] == "李明"
    assert result["open_questions"][0]["source_refs"][0]["content_hash"] == ref["content_hash"]
    assert all(item["id"] != done["id"] for item in result["unfinished_tasks"])
    assert "未确认预算八万元" not in str(result)
    assert repository._conn.execute("SELECT count(*) FROM meetings").fetchone()[0] == before_meetings
    assert repository._conn.execute("SELECT count(*) FROM project_reconcile_jobs").fetchone()[0] == before_jobs


def test_brief_folder_and_document_intersection_matches_next_meeting_inheritance(brief):
    _, repository, service, project_id = brief
    folder = repository.create_folder(project_id, "本次目录")
    child = repository.create_folder(project_id, "子目录", parent_id=folder["id"])
    allowed_document, allowed = source(repository, project_id, folder_id=child["id"])
    _, omitted = source(repository, project_id)
    add(service, repository, project_id, "decision", "目录内决定", [allowed])
    add(service, repository, project_id, "decision", "目录外决定", [omitted])
    add(service, repository, project_id, "decision", "项目级无附加来源决定")
    limited = service.brief_preview(project_id, folder_id=folder["id"])
    assert [item["text"] for item in limited["decisions"]] == ["目录内决定"]
    selected = service.brief_preview(project_id, document_ids=[allowed_document["id"]])
    assert [item["text"] for item in selected["decisions"]] == ["目录内决定"]
    repository.bind_meeting_context("previous", project_id, document_ids=[allowed_document["id"]])
    assert service.for_meeting("previous")["decisions"] == selected["decisions"]
    empty = service.brief_preview(project_id, document_ids=[])
    assert all(empty[key] == [] for key in ("decisions", "unfinished_tasks", "open_questions", "needs_review", "needs_review_documents"))
    repository.bind_meeting_context("previous", project_id, document_ids=[])
    assert service.for_meeting("previous")["decisions"] == []


def test_changed_or_deleted_linked_source_is_warning_never_current_conclusion(brief):
    persistence, repository, service, project_id = brief
    folder = repository.create_folder(project_id, "本次目录")
    persistence.create_note(note_id="note", meeting_id="previous", title="关联预算", body="预算五万元。",
        source_kind="manual", source_message_id=None, evidence=[], now_ms=500)
    imported = SourceLibraryImporter(repository).import_note("note", project_id, folder_id=folder["id"], expected_source_version=1)
    document = repository.confirm_version(imported["document"]["id"], imported["version"]["id"], expected_revision=imported["document"]["revision"])
    ref = repository.read_source(imported["version"]["spans"][0]["id"], project_id=project_id)
    old = add(service, repository, project_id, "decision", "预算五万元", [ref])
    persistence.update_note("note", expected_version=1, body="预算八万元。", now_ms=1000)
    result = service.brief_preview(project_id, folder_id=folder["id"], document_ids=[document["id"]])
    assert result["decisions"] == [] and result["needs_review"][0]["id"] == old["id"]
    assert result["needs_review"][0]["source_refs"][0]["version_id"] == ref["version_id"]
    assert result["needs_review_documents"][0]["source_status"] == "needs_review"
    assert result["project_revision"] == revision(repository, project_id)
    assert repository.search_for_meeting("previous", "预算") == []
    with pytest.raises(KnowledgeScopeError):
        repository.bind_meeting_context("previous", project_id, document_ids=[document["id"]])
    other_folder = repository.create_folder(project_id, "别的目录")
    assert service.brief_preview(project_id, folder_id=other_folder["id"])["needs_review"] == []
    persistence.update_note("note", expected_version=2, status="deleted", now_ms=1200)
    assert service.brief_preview(project_id, document_ids=[document["id"]])["needs_review_documents"][0]["source_status"] == "deleted"


def test_brief_rejects_cross_project_and_archived_project_and_oversized_limit(brief):
    _, repository, service, project_id = brief
    other = repository.create_project("客户乙")
    document, _ = source(repository, other["id"])
    with pytest.raises(KnowledgeScopeError):
        service.brief_preview(project_id, document_ids=[document["id"]])
    with pytest.raises(ValueError):
        service.brief_preview(project_id, limit=21)
    repository.update_project(project_id, archived=True, expected_revision=revision(repository, project_id))
    with pytest.raises(KnowledgeScopeError):
        service.brief_preview(project_id)


def test_raw_human_final_and_mixed_folder_sources_do_not_leak_into_a_limited_brief(brief):
    persistence, repository, service, project_id = brief
    folder = repository.create_folder(project_id, "本次范围")
    _, allowed = source(repository, project_id, folder_id=folder["id"])
    _, omitted = source(repository, project_id)
    add(service, repository, project_id, "decision", "含目录外依据的决定", [allowed, omitted])
    final = persistence.save_user_final_document(meeting_id="previous", document_kind="minutes", expected_revision=0,
        content={"markdown": "人工保存：法务核对后签署。"}, now_ms=500)
    raw = service.source(final["document_id"], project_id, source_type="review_document")
    add(service, repository, project_id, "decision", "法务核对后签署", [raw])
    assert len(service.brief_preview(project_id)["decisions"]) == 2
    assert service.brief_preview(project_id, folder_id=folder["id"])["decisions"] == []


def test_brief_fair_budget_keeps_open_question_when_many_decisions_dominate(brief):
    _, repository, service, project_id = brief
    for index in range(25):
        add(service, repository, project_id, "decision", f"人工决定{index}")
    add(service, repository, project_id, "task", "人工未完事项")
    add(service, repository, project_id, "question", "人工开放问题")
    result = service.brief_preview(project_id, limit=3)
    assert len(result["decisions"]) == len(result["unfinished_tasks"]) == len(result["open_questions"]) == 1
    assert result["truncated"] is True


def test_brief_api_accepts_real_precreation_scope_and_preserves_null_vs_empty(brief):
    _, repository, service, project_id = brief
    add(service, repository, project_id, "decision", "项目人工确认结论")
    app = FastAPI()
    mount_project_outcomes_api(app, repository)
    with TestClient(app) as client:
        url = f"/v2/knowledge/projects/{project_id}/brief-preview"
        full = client.post(url, json={"folder_id": None, "document_ids": None})
        assert full.status_code == 200, full.text
        assert full.json()["document_ids"] is None and full.json()["decisions"]
        empty = client.post(url, json={"document_ids": []}).json()
        assert empty["document_ids"] == [] and empty["decisions"] == []
        assert client.post(url, json={"meeting_id": "fake"}).status_code == 422
        assert client.post(url, json={"limit": 21}).status_code == 422
        assert client.post(url, json={"document_ids": ["missing"]}).status_code == 404

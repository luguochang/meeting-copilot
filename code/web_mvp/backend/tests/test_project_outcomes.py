from __future__ import annotations

import sqlite3
import json

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes


@pytest.fixture
def state(tmp_path):
    repository = KnowledgeRepository(tmp_path / "project.db")
    service = ProjectOutcomes(repository)
    project = repository.create_project("客户 A")
    with sqlite3.connect(repository.database_path) as connection:
        connection.executemany("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'ended',1,1)", [("previous",), ("current",)])
    repository.bind_meeting_context("previous", project["id"])
    repository.bind_meeting_context("current", project["id"])
    yield repository, service, project["id"]
    repository.close()


def _revision(repository, project_id):
    return repository.get_project(project_id)["revision"]


def _source(repository, project_id, content, **kwargs):
    document = repository.create_document(project_id, "会后人工文稿", kind="meeting_document", source_meeting_id="previous", **kwargs)
    version = repository.add_version(document["id"], content, origin="meeting_user_final")
    document = repository.confirm_version(document["id"], version["id"], expected_revision=document["revision"] + 1)
    return document, repository.read_source(version["spans"][0]["id"], project_id=project_id)


def test_ai_candidate_never_enters_wiki_until_explicit_confirmation(state):
    repository, service, project_id = state
    _, ref = _source(repository, project_id, "压测后上线。负责人李明，周五完成压测。")
    base = _revision(repository, project_id)
    proposal = service.propose(project_id, [
        {"type": "decision", "text": "压测后上线", "source_refs": [ref]},
        {"type": "task", "text": "完成压测", "owner": "李明", "due": "周五", "source_refs": [ref]},
    ], meeting_id="previous", expected_project_revision=base)
    assert proposal["status"] == "pending"
    assert service.wiki(project_id)["items"] == []
    assert _revision(repository, project_id) == base
    result = service.confirm(proposal["id"], expected_revision=proposal["revision"], expected_project_revision=base)
    assert result["proposal"]["status"] == "confirmed"
    assert len(result["items"]) == 2
    inherited = service.for_meeting("current")
    assert inherited["decisions"][0]["text"] == "压测后上线"
    assert inherited["unfinished_tasks"][0]["owner"] == "李明"
    wiki = service.wiki(project_id)
    assert "压测后上线" in wiki["markdown"] and "/v2/knowledge/sources/" in wiki["markdown"]
    assert "状态：有效" in wiki["markdown"] and "状态：active" not in wiki["markdown"]
    assert wiki == service.wiki(project_id) and wiki["rebuildable"]


def test_cross_meeting_new_confirmed_snapshot_supersedes_old_manual_condition(state):
    repository, service, project_id = state
    _, old_ref = _source(repository, project_id, "必须压测后上线。")
    old = service.create(project_id, kind="decision", text="压测后上线", source_refs=[old_ref], source_meeting_id="previous",
                         expected_project_revision=_revision(repository, project_id))
    question = service.create(project_id, kind="question", text="压测是否已通过？", source_refs=[old_ref], source_meeting_id="previous",
                              expected_project_revision=_revision(repository, project_id))
    _, current_ref = _source(repository, project_id, "本次压测已通过，可以周五上线。")
    proposal = service.propose(project_id, [{"action": "supersede", "item_id": old["id"], "text": "压测已通过，周五上线",
                                             "source_refs": [current_ref]},
                                            {"action": "update", "item_id": question["id"], "status": "done", "source_refs": [current_ref]}],
                               meeting_id="current", expected_project_revision=_revision(repository, project_id))
    service.confirm(proposal["id"], expected_revision=1, expected_project_revision=proposal["base_revision"])
    wiki = service.wiki(project_id)
    assert "必须压测后上线" not in wiki["markdown"]
    assert len(wiki["items"]) == 1 and wiki["items"][0]["supersedes_id"] == old["id"]
    assert service.history(old["id"])["items"][0]["snapshot"]["status"] == "superseded"
    assert service.for_meeting("current")["decisions"][0]["text"] == "压测已通过，周五上线"
    assert service.for_meeting("current")["open_questions"] == []


def test_cancelled_and_completed_tasks_are_not_revived_or_inherited(state):
    repository, service, project_id = state
    task = service.create(project_id, kind="task", text="用户确认取消旧压测", status="unknown", owner=None, due=None,
                          expected_project_revision=_revision(repository, project_id))
    old_context_revision = repository.get_meeting_context("current")["context_revision"]
    cancelled = service.update(task["id"], {"status": "cancelled"}, expected_revision=task["revision"],
                                expected_project_revision=_revision(repository, project_id))
    assert repository.get_meeting_context("current")["context_revision"] == old_context_revision
    assert service.for_meeting("current")["unfinished_tasks"] == []
    with pytest.raises(KnowledgeScopeError, match="revived"):
        service.update(task["id"], {"status": "active"}, expected_revision=cancelled["revision"],
                        expected_project_revision=_revision(repository, project_id))
    with pytest.raises(KnowledgeScopeError):
        service.propose(project_id, [{"action": "update", "item_id": task["id"], "status": "active"}], origin="user",
                        expected_project_revision=_revision(repository, project_id))
    done = service.create(project_id, kind="task", text="完成资料核对", expected_project_revision=_revision(repository, project_id))
    service.update(done["id"], {"status": "done"}, expected_revision=done["revision"], expected_project_revision=_revision(repository, project_id))
    assert service.for_meeting("current")["unfinished_tasks"] == []


def test_source_revision_or_deletion_marks_current_result_needs_review_and_never_reactivates(state):
    repository, service, project_id = state
    document, ref = _source(repository, project_id, "预算已批准3万元。")
    item = service.create(project_id, kind="decision", text="预算3万元", source_refs=[ref], expected_project_revision=_revision(repository, project_id))
    new_version = repository.add_version(document["id"], "预算仍未批准。")
    repository.confirm_version(document["id"], new_version["id"], expected_revision=document["revision"] + 1)
    assert service.for_meeting("current")["decisions"] == []
    assert service.list(project_id, status="needs_review")["items"][0]["id"] == item["id"]
    # Restoring an older document version cannot automatically restore an invalidated outcome.
    current_document = repository.get_document(document["id"])
    repository.confirm_version(document["id"], ref["version_id"], expected_revision=current_document["revision"])
    assert service.for_meeting("current")["decisions"] == []
    assert "待核对" in service.wiki(project_id)["markdown"]
    current_document = repository.get_document(document["id"])
    repository.set_document_status(document["id"], "deleted", expected_revision=current_document["revision"])
    assert service.for_meeting("current")["decisions"] == []


def test_stale_candidate_cannot_overwrite_new_confirmation_and_partial_changes_rollback(state):
    repository, service, project_id = state
    _, ref = _source(repository, project_id, "预算3万，负责人李明。")
    proposal = service.propose(project_id, [{"type": "decision", "text": "预算3万", "source_refs": [ref]}],
                               expected_project_revision=_revision(repository, project_id))
    service.create(project_id, kind="decision", text="用户确认预算4万", expected_project_revision=_revision(repository, project_id))
    with pytest.raises(KnowledgeConflict):
        service.confirm(proposal["id"], expected_revision=1, expected_project_revision=_revision(repository, project_id))
    assert [item["text"] for item in service.wiki(project_id)["items"]] == ["用户确认预算4万"]
    with pytest.raises(KnowledgeScopeError):
        service.propose(project_id, [{"type": "decision", "text": "候选一", "source_refs": [ref]},
                                     {"type": "task", "text": "候选二", "owner": "无来源人物", "source_refs": [ref]}],
                        expected_project_revision=_revision(repository, project_id))
    assert len(service.wiki(project_id)["items"]) == 1


def test_current_selection_intersection_uses_project_sources_without_previous_scope_revision(state):
    repository, service, project_id = state
    folder = repository.create_folder(project_id, "允许的目录")
    _, allowed = _source(repository, project_id, "客户A决定", folder_id=folder["id"])
    _, omitted = _source(repository, project_id, "客户A其他决定")
    allowed["context_revision"] = 999999  # prior meeting context is not a source truth revision
    service.create(project_id, kind="decision", text="允许决定", source_refs=[allowed], expected_project_revision=_revision(repository, project_id))
    service.create(project_id, kind="decision", text="未选决定", source_refs=[omitted], expected_project_revision=_revision(repository, project_id))
    repository.bind_meeting_context("current", project_id, folder_id=folder["id"])
    inherited = service.for_meeting("current")
    assert [item["text"] for item in inherited["decisions"]] == ["允许决定"]
    assert "context_revision" not in inherited["decisions"][0]["source_refs"][0]
    repository.bind_meeting_context("current", project_id, document_ids=[])
    assert service.for_meeting("current")["decisions"] == []


def test_human_review_final_source_is_checked_without_mutating_it(state):
    repository, service, project_id = state
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("INSERT INTO review_documents(document_id,meeting_id,document_kind,revision,ai_version,user_version,ai_content_json,"
                           "user_content_json,user_modified,created_at_ms,updated_at_ms) VALUES('review','previous','minutes',1,1,1,?, ?,0,1,1)",
                           ('"AI草稿"', '"用户尚未编辑的AI复制版"'))
    with pytest.raises(KnowledgeScopeError, match="human final"):
        service.source("review", project_id, source_type="review_document")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE review_documents SET user_modified=1,user_version=2,user_content_json=? WHERE document_id='review'", ('"用户终稿：预算3万"',))
    ref = service.source("review", project_id, source_type="review_document")
    service.create(project_id, kind="decision", text="预算3万", source_refs=[ref], source_meeting_id="previous",
                   expected_project_revision=_revision(repository, project_id))
    assert service.for_meeting("current")["decisions"][0]["source_refs"][0]["version"] == 2
    before = ref["content_hash"]
    assert service.wiki(project_id)["items"]
    assert service.source("review", project_id, source_type="review_document")["content_hash"] == before
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE review_documents SET user_version=3,user_content_json=? WHERE document_id='review'", ('"用户终稿：预算未批准"',))
    assert service.for_meeting("current")["decisions"] == []


def test_cross_project_outcomes_and_wrong_source_are_rejected(state):
    repository, service, project_id = state
    other = repository.create_project("客户 B")
    _, ref = _source(repository, project_id, "A资料")
    with pytest.raises(KnowledgeScopeError):
        service.create(other["id"], kind="decision", text="泄漏", source_refs=[ref], expected_project_revision=other["revision"])
    with pytest.raises(KnowledgeScopeError):
        service.create(other["id"], kind="decision", text="错误会议", source_meeting_id="current", expected_project_revision=other["revision"])
    with pytest.raises(KnowledgeScopeError):
        service.propose(project_id, [{"type": "decision", "text": "无依据AI候选"}], expected_project_revision=_revision(repository, project_id))


def test_ai_cannot_recreate_cancelled_task_as_new_id(state):
    repository, service, project_id = state
    _, ref = _source(repository, project_id, "取消测试任务，负责人李明。")
    service.create(project_id, kind="task", text="测试任务", status="cancelled", source_refs=[ref],
                   expected_project_revision=_revision(repository, project_id))
    with pytest.raises(KnowledgeScopeError, match="recreate"):
        service.propose(project_id, [{"type": "task", "text": "测试任务", "source_refs": [ref]}],
                        expected_project_revision=_revision(repository, project_id))


def test_pending_candidate_source_change_is_explicit_needs_review(state):
    repository, service, project_id = state
    document, ref = _source(repository, project_id, "预算已批准。")
    proposal = service.propose(project_id, [{"type": "decision", "text": "预算已批准", "source_refs": [ref]}],
                               expected_project_revision=_revision(repository, project_id))
    repository.set_document_status(document["id"], "disabled")
    pending = service.proposals(project_id)["items"][0]
    assert pending["status"] == "needs_review" and pending["is_stale"]
    with pytest.raises(KnowledgeConflict):
        service.confirm(proposal["id"], expected_revision=proposal["revision"], expected_project_revision=_revision(repository, project_id))
    assert service.wiki(project_id)["items"] == []


def test_prompt_context_json_budget_and_three_categories(state):
    repository, service, project_id = state
    for kind in ("decision", "task", "question"):
        service.create(project_id, kind=kind, text=kind + "内容" * 150,
                       expected_project_revision=_revision(repository, project_id))
    context = service.context_for_meeting("current")
    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 2400
    assert {item["type"] for item in context["items"]} == {"decision", "task", "question"}
    assert all(len(item["text"]) <= 300 for item in context["items"])
    tight = service.context_for_meeting("current", max_characters=1500)
    assert len(json.dumps(tight, ensure_ascii=False, sort_keys=True, separators=(",", ":"))) <= 1500 and tight["truncated"]
    repository.update_project(project_id, archived=True)
    unavailable = service.context_for_meeting("current")
    assert unavailable["items"] == [] and unavailable["error_code"] == "project_scope_unavailable"


def test_existing_meeting_fact_needs_current_human_event_not_model_confirmed_flag(state):
    repository, service, project_id = state
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("INSERT INTO meeting_entities(meeting_id,entity_id,kind,status,text,evidence_segment_ids_json,version) "
                           "VALUES('previous','fact','decision_candidate','confirmed','预算三万','[]',1)")
    with pytest.raises(KnowledgeScopeError, match="human confirmation"):
        service.source("fact", project_id, source_type="meeting_entity", meeting_id="previous")
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("INSERT INTO meeting_events(meeting_id,seq,event_id,type,aggregate_type,aggregate_id,occurred_at_ms,idempotency_key,payload_json) "
                           "VALUES('previous',1,'human-event','meeting.decision.updated','meeting_entity','fact',1,'meeting.entity.status:fact:1','{}')")
    ref = service.source("fact", project_id, source_type="meeting_entity", meeting_id="previous")
    service.create(project_id, kind="decision", text="预算三万", source_refs=[ref], expected_project_revision=_revision(repository, project_id))
    assert service.for_meeting("current")["decisions"][0]["source_refs"][0]["source_type"] == "meeting_entity"
    assert "source_type=meeting_entity" in service.wiki(project_id)["markdown"]
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE meeting_entities SET text='模型后续改为四万',version=2 WHERE meeting_id='previous' AND entity_id='fact'")
    assert service.for_meeting("current")["decisions"] == []


def test_outcomes_survive_repository_restart(tmp_path):
    path = tmp_path / "persistent.db"
    first = KnowledgeRepository(path)
    project = first.create_project("持续项目")
    item = ProjectOutcomes(first).create(project["id"], kind="task", text="用户明确的待办", status="unknown", expected_project_revision=project["revision"])
    first.close()
    second = KnowledgeRepository(path)
    try:
        service = ProjectOutcomes(second)
        assert service.list(project["id"])["items"][0]["id"] == item["id"]
        assert "用户明确的待办" in service.wiki(project["id"])["markdown"]
    finally:
        second.close()

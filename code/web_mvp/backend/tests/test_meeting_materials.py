from __future__ import annotations

import asyncio
import json
import os
import subprocess
from threading import Lock
import time

import pytest

from meeting_copilot_web_mvp.document_import import DocumentImportService, extract_document
from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge, source_evidence_state
from meeting_copilot_web_mvp.knowledge_jobs import JOB_LEASE_MS, KnowledgeJobQueue, KnowledgeJobRunner
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.meeting_material_jobs import MeetingMaterialJobQueue, MeetingMaterialJobRunner
from meeting_copilot_web_mvp.meeting_materials import (
    MeetingMaterialImportService, MeetingMaterialsRepository, attachment_source_state, bind_material_set,
    delete_meeting_materials, material_context_snapshot, read_attachment_source,
)
from meeting_copilot_web_mvp.user_memory import UserMemory, capture_input_context, input_context_state
from meeting_copilot_web_mvp.pi_evidence_registry import PiEvidenceRegistry
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def materials(tmp_path):
    library = KnowledgeRepository(tmp_path / "app.sqlite3")
    repository = MeetingMaterialsRepository(library)
    importer = MeetingMaterialImportService(repository, tmp_path / "private")
    queue = MeetingMaterialJobQueue(repository)
    yield repository, importer, queue
    library.close()


def meeting(repository, meeting_id="meeting"):
    with repository._write():
        repository._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES(?,'live',1,1)", (meeting_id,))
    return meeting_id


def imported(materials, content="预算 30 万元，接口延迟上限 100 毫秒。", filename="需求.txt", *, set_id=None):
    repository, importer, queue = materials
    material_set = repository.get_set(set_id) if set_id else repository.create_set()
    attachment = importer.stage(material_set["id"], filename, content.encode(), content_type="text/plain")
    job = queue.claim()
    result = extract_document(filename, content.encode(), "text/plain")
    assert queue.finish(job, result)
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    version = repository.get_version(material_set["id"], attachment["id"], attachment["latest_version_id"])
    return repository.get_set(material_set["id"]), attachment, version


def use(repository, material_set, attachment, version, *, selected_span_ids=None):
    return repository.usage(material_set["id"], attachment["id"], enabled=True, version_id=version["id"],
                            selected_span_ids=selected_span_ids, expected_revision=attachment["revision"])


def test_draft_upload_enable_bind_preview_use_without_project_or_fake_meeting(materials):
    repository, importer, _ = materials
    material_set, attachment, version = imported(materials)
    assert repository._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    assert repository._conn.execute("SELECT COUNT(*) FROM meeting_events").fetchone()[0] == 0
    assert material_set["state"] == "draft" and attachment["enabled"] is False
    use(repository, material_set, attachment, version)
    meeting_id = meeting(repository)
    assert retrieve_meeting_knowledge(repository.library, meeting_id, "预算", include_attachments=True) == []
    path = importer.original_path(attachment["id"])
    bound = repository.bind(meeting_id, material_set["id"], expected_revision=repository.get_set(material_set["id"])["revision"])
    assert bound["state"] == "bound" and importer.original_path(attachment["id"]) == path
    assert retrieve_meeting_knowledge(repository.library, meeting_id, "预算") == []
    evidence = retrieve_meeting_knowledge(repository.library, meeting_id, "预算", include_attachments=True)[0]
    assert evidence["source_type"] == "meeting_attachment" and evidence["meeting_id"] == meeting_id
    assert evidence["quote"] == "预算 30 万元，接口延迟上限 100 毫秒。"
    assert repository.library.validate_evidence(evidence, meeting_id=meeting_id)["source_id"] == evidence["source_id"]
    assert source_evidence_state(repository._conn, meeting_id, [evidence]) == "valid"
    assert repository.library.read_source(evidence["source_id"], meeting_id=meeting_id)["quote"] == evidence["quote"]


def test_create_and_bind_use_same_transaction_and_idempotent_replay(materials):
    repository, _, _ = materials
    material_set = repository.create_set()
    with pytest.raises(RuntimeError, match="transaction"):
        bind_material_set(repository._conn, material_set["id"], "not-created")
    with pytest.raises(RuntimeError, match="rollback"):
        with repository._write():
            repository._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','live',1,1)")
            bind_material_set(repository._conn, material_set["id"], "meeting", material_set["revision"])
            raise RuntimeError("rollback")
    assert repository.get_set(material_set["id"])["state"] == "draft"
    assert not repository._conn.execute("SELECT 1 FROM meetings WHERE id='meeting'").fetchone()
    meeting(repository)
    bound = repository.bind("meeting", material_set["id"], expected_revision=1)
    assert repository.bind("meeting", material_set["id"], expected_revision=1) == bound
    meeting(repository, "other")
    with pytest.raises(KnowledgeScopeError):
        repository.bind("other", material_set["id"])
    assert repository.bind("meeting")["id"] == material_set["id"]


def test_binding_during_conversion_does_not_supersede_content_input(materials):
    repository, importer, queue = materials
    material_set = repository.create_set()
    attachment = importer.stage(material_set["id"], "draft.txt", b"budget 30000", content_type="text/plain")
    job = queue.claim()
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    assert queue.finish(job, extract_document("draft.txt", b"budget 30000"))
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    assert attachment["latest_version_id"] and not attachment["enabled"]
    assert retrieve_meeting_knowledge(repository.library, "meeting", "budget", include_attachments=True) == []


def test_saved_v2_and_failed_conversion_never_replace_enabled_v1(materials):
    repository, _, queue = materials
    material_set, attachment, version = imported(materials)
    attachment = use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    original = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)[0]
    binding = capture_input_context(repository._conn, "meeting")
    version2 = repository.add_version(material_set["id"], attachment["id"], "预算 3 万元。", expected_revision=attachment["revision"], base_version_id=version["id"])
    attachment2 = repository.get_attachment(material_set["id"], attachment["id"])
    assert attachment2["active_version_id"] == version["id"] and input_context_state(repository._conn, "meeting", binding) == "valid"
    assert attachment_source_state(repository._conn, "meeting", original) == "valid"
    attachment2 = use(repository, material_set, attachment2, version2)
    assert attachment_source_state(repository._conn, "meeting", original) == "stale"
    assert input_context_state(repository._conn, "meeting", binding) == "stale"
    old = read_attachment_source(repository._conn, "meeting", original["source_id"], allow_historical=True)
    assert old["quote"] == original["quote"] and old["source_state"] == "stale"
    repository.usage(material_set["id"], attachment["id"], enabled=False, expected_revision=attachment2["revision"])
    assert retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True) == []
    assert not queue.recover_orphans()


def test_edit_during_conversion_discards_late_output_and_refuses_stale_retry(materials):
    repository, importer, queue = materials
    material_set = repository.create_set()
    attachment = importer.stage(material_set["id"], "draft.txt", b"old", content_type="text/plain")
    job = queue.claim()
    corrected = repository.add_version(material_set["id"], attachment["id"], "human correction", expected_revision=attachment["revision"])
    assert queue.finish(job, extract_document("draft.txt", b"old"))
    assert queue.get(job["id"])["error_code"] == "input_superseded"
    assert repository.get_attachment(material_set["id"], attachment["id"])["latest_version_id"] == corrected["id"]
    with pytest.raises(KnowledgeConflict):
        queue.retry(material_set["id"], job["id"])


def test_scope_select_pages_and_cross_meeting_forgery_rejected(materials):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, "# public\n接口预算 30 万元。\n\n# secret\n密码区域不得进入本场。", "scope.md")
    page = repository.spans(material_set["id"], attachment["id"], version["id"], limit=1)
    assert page["has_more"] and page["next_offset"] == 1
    assert len(version["structure"]["sections"]) == 2
    use(repository, material_set, attachment, version, selected_span_ids=[page["items"][0]["id"]])
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)
    assert evidence and all("密码" not in item["quote"] for item in evidence)
    meeting(repository, "other")
    assert retrieve_meeting_knowledge(repository.library, "other", "预算", include_attachments=True) == []
    for changes in ({"meeting_id": "other"}, {"content_hash": "0" * 64}, {"context_revision": 0}, {"locator": {}}, {"quote": "invented"}):
        assert attachment_source_state(repository._conn, "meeting", {**evidence[0], **changes}) == "stale"
    with pytest.raises(KeyError):
        read_attachment_source(repository._conn, "other", evidence[0]["source_id"], allow_historical=True)
    with pytest.raises(KnowledgeScopeError):
        repository.library.validate_evidence(evidence[0], project_id="invented")


def test_project_and_material_sources_share_six_items_and_3600_chars(materials):
    repository, _, _ = materials
    project = repository.library.create_project("test")
    document = repository.library.create_document(project["id"], "project")
    version = repository.library.add_version(document["id"], ("budget " * 200) + "\n\n" + ("budget " * 200))
    repository.library.confirm_version(document["id"], version["id"], expected_revision=repository.library.get_document(document["id"])["revision"])
    material_set, attachment, version = imported(materials, ("budget " * 200) + "\n\n" + ("budget " * 200))
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.library.bind_meeting_context("meeting", project_id=project["id"])
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "budget", include_attachments=True)
    assert {item["source_type"] for item in evidence} == {"document", "meeting_attachment"}
    assert len(evidence) <= 6 and sum(len(item["quote"]) for item in evidence) <= 3600
    assert max(len(item["quote"]) for item in evidence) <= 900


def test_expired_lease_cancel_and_retry_keep_active_version(materials):
    repository, importer, queue = materials
    material_set = repository.create_set()
    attachment = importer.stage(material_set["id"], "retry.txt", b"budget", content_type="text/plain")
    first = queue.claim(now_ms=100)
    assert queue.recover_expired(now_ms=100 + JOB_LEASE_MS) == 1
    second = queue.claim(now_ms=100 + JOB_LEASE_MS)
    assert second["lease_token"] != first["lease_token"]
    assert queue.finish(first, extract_document("retry.txt", b"budget")) is False
    queue.cancel(material_set["id"], second["id"])
    assert queue.finish(second, extract_document("retry.txt", b"budget")) is False
    assert queue.retry(material_set["id"], second["id"])["status"] == "queued"
    assert queue.finish(queue.claim(), extract_document("retry.txt", b"budget"))
    ready = repository.get_attachment(material_set["id"], attachment["id"])
    assert ready["latest_version_id"] and not ready["enabled"]


def test_delete_bound_attachment_cleans_managed_original_and_preserves_source_tombstone(materials):
    repository, importer, queue = materials
    material_set, attachment, version = imported(materials)
    attachment = use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)[0]
    path = importer.original_path(attachment["id"])
    deleted, cleanup = repository.delete_attachment(material_set["id"], attachment["id"], expected_revision=attachment["revision"])
    assert not deleted["enabled"] and attachment_source_state(repository._conn, "meeting", evidence) == "stale"
    runner = MeetingMaterialJobRunner(queue, importer)
    job = queue.claim()
    assert queue.finish(job, runner._cleanup(job))
    assert not path.exists() and queue.get(cleanup["id"])["status"] == "succeeded"
    historical = read_attachment_source(repository._conn, "meeting", evidence["source_id"], allow_historical=True)
    assert historical["source_state"] == "deleted" and historical["quote"] == ""
    assert repository.delete_attachment(material_set["id"], attachment["id"], expected_revision=attachment["revision"])[1]["id"] == cleanup["id"]


def test_all_meeting_delete_tombstones_bound_set_never_reappears_as_draft(materials):
    repository, _, queue = materials
    material_set, attachment, version = imported(materials)
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    with pytest.raises(KnowledgeScopeError):
        repository.delete_draft(material_set["id"], expected_revision=repository.get_set(material_set["id"])["revision"])
    with repository._write():
        delete_meeting_materials(repository._conn, "meeting", 10)
        repository._conn.execute("DELETE FROM meetings WHERE id='meeting'")
    assert repository.list_sets()["items"] == []
    assert repository.get_set(material_set["id"], include_deleted=True)["state"] == "deleted"
    assert queue.claim()["type"] == "cleanup"


def test_attachment_memory_requires_explicit_meeting_scope_and_is_revoked_with_source(materials):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, "术语 PaperPilot 指现场助手。")
    attachment = use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "PaperPilot", include_attachments=True)[0]
    memory = UserMemory(repository.library)
    candidate = memory.create(scope="meeting", scope_id="meeting", kind="term", memory_key="PaperPilot", value="PaperPilot", source_refs=[evidence])
    active = memory.update(candidate["id"], status="active", expected_revision=candidate["revision"])
    assert memory.resolve("meeting")["terms"][0]["canonical"] == "PaperPilot"
    with pytest.raises(ValueError):
        memory.create(scope="global", kind="term", memory_key="PaperPilot", value="PaperPilot", source_refs=[evidence])
    repository.usage(material_set["id"], attachment["id"], enabled=False, expected_revision=attachment["revision"])
    assert memory.resolve("meeting")["terms"] == []
    deleted = memory.update(active["id"], status="deleted", expected_revision=active["revision"])
    assert deleted["source_refs"] == []


@pytest.mark.parametrize("status", ["pending", "running"])
def test_usage_changes_cancel_unpublished_participation_on_source_transaction(materials, status):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials)
    attachment = use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)
    binding = capture_input_context(repository._conn, "meeting")
    with repository._write():
        repository._conn.execute("INSERT INTO transcript_segments(meeting_id,segment_id,final_id,transcript_seq,text,normalized_text,evidence_hash,created_at_ms,updated_at_ms) "
                                 "VALUES('meeting','segment','final',1,'synthetic','synthetic','hash',1,1)")
        repository._conn.execute("INSERT INTO jobs(id,meeting_id,kind,status,input_transcript_seq,input_version,evidence_segment_id,evidence_hash,"
                                 "idempotency_key,next_attempt_at_ms,created_at_ms,updated_at_ms,request_context_json) "
                                 "VALUES('participation','meeting','intelligence',?,1,1,'segment','hash','participation',0,1,1,?)",
                                 (status, json.dumps({"profile": "participation", "input_context": binding, "source_evidence": evidence})))
    repository.usage(material_set["id"], attachment["id"], enabled=False, expected_revision=attachment["revision"])
    job = repository._conn.execute("SELECT status,error_class FROM jobs WHERE id='participation'").fetchone()
    assert tuple(job) == ("cancelled", "evidence_superseded")


def test_catalog_reports_actual_complete_short_read_and_long_omissions(materials):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, "已确认预算 30000 元。")
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "请介绍一下", include_attachments=True)
    snapshot = material_context_snapshot(repository._conn, "meeting", provided_evidence=evidence)
    assert snapshot["material_set_id"] == material_set["id"] and snapshot["catalog_is_evidence"] is False
    assert snapshot["attachment_catalog"][0]["coverage"]["complete_read"] is True
    material_set, long_attachment, long_version = imported(materials, "预算" * 1200, set_id=material_set["id"])
    use(repository, material_set, long_attachment, long_version)
    evidence = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)
    snapshot = material_context_snapshot(repository._conn, "meeting", provided_evidence=evidence)
    long_catalog = next(item for item in snapshot["attachment_catalog"] if item["attachment_id"] == long_attachment["id"])
    assert long_catalog["coverage"]["authorized_characters"] == 2400
    assert long_catalog["coverage"]["provided_characters"] == 900
    assert long_catalog["coverage"]["complete_read"] is False


def test_complete_prefetch_includes_unmatched_constraints_and_empty_question(materials):
    repository, _, _ = materials
    content = "# 问题\n支付预算 3 万元。\n\n# 限制\n不引复杂设施。\n\n# 付款\n不能重复扣款。"
    material_set, attachment, version = imported(materials, content, "requirements.md")
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    ordinary = retrieve_meeting_knowledge(repository.library, "meeting", "支付预算", include_attachments=True)
    assert "不能重复扣款" not in "\n".join(item["quote"] for item in ordinary)
    complete = retrieve_meeting_knowledge(repository.library, "meeting", "支付预算", include_attachments=True,
                                         prefer_complete_attachments=True)
    assert len(complete) == 3
    assert all(phrase in "\n".join(item["quote"] for item in complete) for phrase in ("不引复杂设施", "不能重复扣款"))
    assert retrieve_meeting_knowledge(repository.library, "meeting", "", include_attachments=True,
                                     prefer_complete_attachments=True) == complete
    assert retrieve_meeting_knowledge(repository.library, "meeting", "支付预算", prefer_complete_attachments=True) == []
    snapshot = material_context_snapshot(repository._conn, "meeting", provided_evidence=complete)
    assert snapshot["attachment_catalog"][0]["coverage"]["complete_read"] is True
    partial = material_context_snapshot(repository._conn, "meeting", provided_evidence=ordinary)
    assert partial["attachment_catalog"][0]["coverage"]["complete_read"] is False


@pytest.mark.parametrize("file_count,spans_per_file,body_characters", [(6, 2, 620), (2, 1, 3900)])
def test_complete_prefetch_aggregate_budget_and_untruncated_quotes(materials, file_count, spans_per_file, body_characters):
    repository, _, _ = materials
    material_set = repository.create_set()
    for index in range(file_count):
        content = "\n\n".join(f"预算{index}/{part}：" + "甲" * body_characters for part in range(spans_per_file))
        material_set, attachment, version = imported(materials, content, set_id=material_set["id"])
        use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    project = repository.library.create_project("正式项目")
    document = repository.library.create_document(project["id"], "正式预算")
    project_version = repository.library.add_version(document["id"], "项目预算有界背景。", origin="user", expected_revision=document["revision"])
    repository.library.confirm_version(document["id"], project_version["id"], expected_revision=repository.library.get_document(document["id"])["revision"])
    repository.library.bind_meeting_context("meeting", project["id"])
    complete = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True,
                                         prefer_complete_attachments=True)
    material_sources = [item for item in complete if item["source_type"] == "meeting_attachment"]
    assert len(material_sources) == file_count * spans_per_file
    assert sum(len(item["quote"]) for item in complete) <= 8000
    assert all(item["quote"] == read_attachment_source(repository._conn, "meeting", item["source_id"])["quote"] for item in material_sources)
    catalog = material_context_snapshot(repository._conn, "meeting", provided_evidence=complete)
    assert all(item["coverage"]["complete_read"] for item in catalog["attachment_catalog"])


@pytest.mark.parametrize("file_count,spans_per_file,body_characters", [(7, 1, 30), (1, 13, 30), (3, 1, 2800)])
def test_complete_prefetch_over_aggregate_budget_falls_back_to_bounded_relevance(materials, file_count, spans_per_file, body_characters):
    repository, _, _ = materials
    material_set = repository.create_set()
    for index in range(file_count):
        content = "\n\n".join(f"预算{index}/{part}：" + "乙" * body_characters for part in range(spans_per_file))
        material_set, attachment, version = imported(materials, content, set_id=material_set["id"])
        use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    ordinary = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True)
    preferred = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True,
                                          prefer_complete_attachments=True)
    assert preferred == ordinary
    assert len(preferred) <= 6 and sum(len(item["quote"]) for item in preferred) <= 3600
    assert all(len(item["quote"]) <= 900 for item in preferred)


def test_complete_prefetch_respects_selected_spans_active_version_disabled_and_meeting_scope(materials):
    repository, _, _ = materials
    material_set, attachment, version = imported(materials, "公开预算 30000。\n\n未授权秘密条款。", "scope.md")
    selected = repository.spans(material_set["id"], attachment["id"], version["id"])["items"][0]["id"]
    attachment = use(repository, material_set, attachment, version, selected_span_ids=[selected])
    version2 = repository.add_version(material_set["id"], attachment["id"], "尚未启用的新预算 90000。", expected_revision=attachment["revision"])
    _, disabled, _ = imported(materials, "停用秘密附件。", set_id=material_set["id"])
    other_set, other_attachment, other_version = imported(materials, "其他会议秘密附件。")
    use(repository, other_set, other_attachment, other_version)
    meeting(repository)
    meeting(repository, "other")
    repository.bind("meeting", material_set["id"])
    repository.bind("other", other_set["id"])
    selected_sources = retrieve_meeting_knowledge(repository.library, "meeting", "", include_attachments=True,
                                                 prefer_complete_attachments=True)
    assert len(selected_sources) == 1 and selected_sources[0]["source_id"] == selected
    assert selected_sources[0]["version_id"] == version["id"]
    assert all(item["attachment_id"] != disabled["id"] for item in selected_sources)
    catalog = material_context_snapshot(repository._conn, "meeting", provided_evidence=selected_sources)
    assert catalog["attachment_catalog"][0]["coverage"]["complete_read"] is True
    attachment = repository.get_attachment(material_set["id"], attachment["id"])
    use(repository, material_set, attachment, version2)
    replaced = retrieve_meeting_knowledge(repository.library, "meeting", "", include_attachments=True,
                                          prefer_complete_attachments=True)
    assert len(replaced) == 1 and replaced[0]["version_id"] == version2["id"]
    assert source_evidence_state(repository._conn, "meeting", selected_sources) == "stale"


def test_participation_registry_explicit_budget_preserves_full_quotes_after_search(materials):
    repository, _, _ = materials
    content = "\n\n".join(f"预算{index}：" + "长" * 950 for index in range(7))
    material_set, attachment, version = imported(materials, content)
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    complete = retrieve_meeting_knowledge(repository.library, "meeting", "预算", include_attachments=True,
                                         prefer_complete_attachments=True)
    assert len(complete) == 7
    persistence = V2Persistence(repository.library.database_path)
    try:
        registry = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=repository.library)
        with pytest.raises(ValueError, match="bounded"):
            registry.register_documents(complete)
        assert registry.register_documents(complete, max_sources=12, max_characters=8000) == complete
        search = registry.search_documents("预算")
        assert len(search) <= 6 and sum(len(item["quote"]) for item in search) <= 3600
        assert len(registry._documents) == 7
        for item in complete:
            assert registry.validate_documents([{"source_id": item["source_id"], "quote": item["quote"]}])[0]["quote"] == item["quote"]
    finally:
        persistence.close()


def test_participation_registry_search_registration_never_evicts_or_exceeds_union_budget(materials):
    repository, _, _ = materials
    content = "\n\n".join([*(f"范围{index}：" + "长" * 950 for index in range(7)), "剩余空间：" + "新" * 1200, "超出空间：" + "外" * 1000])
    material_set, attachment, version = imported(materials, content)
    use(repository, material_set, attachment, version)
    meeting(repository)
    repository.bind("meeting", material_set["id"])
    source_ids = [item["id"] for item in repository.spans(material_set["id"], attachment["id"], version["id"])["items"]]
    sources = [read_attachment_source(repository._conn, "meeting", source_id) for source_id in source_ids]
    persistence = V2Persistence(repository.library.database_path)
    try:
        registry = PiEvidenceRegistry(persistence, meeting_id="meeting", knowledge_repository=repository.library)
        initial = sources[:7]
        registry.register_documents(initial, max_sources=12, max_characters=8000)
        assert registry.register_documents([sources[7]]) == [sources[7]]
        assert registry.register_documents([sources[8]]) == []
        assert len(registry._documents) == 8
        assert sum(len(item["quote"]) for item in registry._documents.values()) <= 8000
        for item in initial:
            assert registry.validate_documents([{"source_id": item["source_id"], "quote": item["quote"]}])[0]["quote"] == item["quote"]
    finally:
        persistence.close()


def test_crash_orphan_cleanup_waits_three_days_and_never_removes_published_or_linked_assets(materials):
    repository, importer, _ = materials
    material_set, attachment, _ = imported(materials)
    published = importer.original_path(attachment["id"])
    now = time.time()
    os.utime(published, (now - 4 * 24 * 3600, now - 4 * 24 * 3600))
    orphan_directory = importer.assets_dir / material_set["id"] / ("attachment_" + "f" * 32)
    orphan_directory.mkdir()
    orphan = orphan_directory / "original.txt"
    orphan.write_bytes(b"unpublished synthetic original")
    assert importer.recover_unpublished_assets(now_seconds=now) == 0
    os.utime(orphan, (now - 4 * 24 * 3600, now - 4 * 24 * 3600))
    linked_directory = importer.assets_dir / material_set["id"] / ("attachment_" + "a" * 32)
    linked_directory.mkdir()
    linked = linked_directory / "original.txt"
    os.link(published, linked)
    assert importer.recover_unpublished_assets(now_seconds=now) == 1
    assert published.exists() and linked.exists() and not orphan_directory.exists()
    with pytest.raises(ValueError, match="hardlinked"):
        importer.original_path(attachment["id"])


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction boundary")
def test_orphan_junction_cannot_delete_a_published_attachment(materials):
    _, importer, _ = materials
    material_set, attachment, _ = imported(materials)
    published = importer.original_path(attachment["id"])
    now = time.time()
    os.utime(published, (now - 4 * 24 * 3600, now - 4 * 24 * 3600))
    junction = importer.assets_dir / material_set["id"] / ("attachment_" + "f" * 32)
    created = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(published.parent)],
                             capture_output=True, check=False)
    assert created.returncode == 0, "synthetic junction setup failed"
    try:
        assert junction.is_junction()
        assert importer.recover_unpublished_assets(now_seconds=now) == 0
        assert published.read_bytes()
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction boundary")
def test_upload_cannot_write_through_another_material_sets_junction(materials):
    repository, importer, _ = materials
    published_set, attachment, _ = imported(materials)
    published = importer.original_path(attachment["id"])
    draft = repository.create_set()
    junction = importer.assets_dir / draft["id"]
    created = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(published.parent.parent)],
                             capture_output=True, check=False)
    assert created.returncode == 0, "synthetic junction setup failed"
    try:
        with pytest.raises(ValueError, match="permanent owner"):
            importer.stage(draft["id"], "new.txt", b"new synthetic material")
        assert repository.list_attachments(draft["id"])["items"] == []
        assert repository.list_attachments(published_set["id"])["items"][0]["id"] == attachment["id"]
        assert published.read_bytes()
    finally:
        junction.rmdir()


def test_draft_and_queued_conversion_survive_repository_restart(materials):
    repository, importer, queue = materials
    material_set = repository.create_set()
    attachment = importer.stage(material_set["id"], "restart.txt", b"budget 30000")
    database_path = repository.library.database_path
    with repository._lock:
        job_id = repository._conn.execute("SELECT id FROM meeting_attachment_jobs WHERE attachment_id=?", (attachment["id"],)).fetchone()[0]
    repository.library.close()
    reopened_library = KnowledgeRepository(database_path)
    try:
        reopened = MeetingMaterialsRepository(reopened_library)
        reopened_queue = MeetingMaterialJobQueue(reopened)
        assert reopened.list_sets()["items"][0]["id"] == material_set["id"]
        assert reopened_queue.get(job_id)["status"] == "queued"
        assert reopened_queue.finish(reopened_queue.claim(), extract_document("restart.txt", b"budget 30000"))
        assert reopened.get_attachment(material_set["id"], attachment["id"])["latest_version_id"]
    finally:
        reopened_library.close()


def test_project_and_material_workers_never_parse_in_parallel(materials, tmp_path):
    repository, importer, queue = materials
    project = repository.library.create_project("project")
    project_importer = DocumentImportService(repository.library, tmp_path / "private")
    document = project_importer.stage_document(project["id"], "project.txt", b"hello")
    project_queue = KnowledgeJobQueue(repository.library)
    project_queue.enqueue(document["id"])
    material_set = repository.create_set()
    importer.stage(material_set["id"], "meeting.txt", b"hello")
    project_runner = KnowledgeJobRunner(project_queue, project_importer)
    material_runner = MeetingMaterialJobRunner(queue, importer)
    active, peak, lock = 0, 0, Lock()

    def convert(job):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return extract_document("hello.txt", b"hello")

    project_runner._convert = material_runner._convert = convert

    async def run():
        await project_runner.start()
        await material_runner.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with repository._lock:
                counts = repository._conn.execute("SELECT (SELECT COUNT(*) FROM knowledge_jobs WHERE status='succeeded') + "
                                                  "(SELECT COUNT(*) FROM meeting_attachment_jobs WHERE status='succeeded')").fetchone()[0]
            if counts == 2:
                break
            await asyncio.sleep(0.02)
        await project_runner.stop()
        await material_runner.stop()
        assert counts == 2

    asyncio.run(run())
    assert peak == 1

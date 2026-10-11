from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import PurePosixPath
import re
import sqlite3
from zipfile import ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.project_export_api import mount_project_export_api
from meeting_copilot_web_mvp import project_export
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    repository = KnowledgeRepository(tmp_path / "export.db")
    app = FastAPI()
    mount_project_export_api(app, repository)
    with TestClient(app) as client:
        yield client, repository
    repository.close()


def _confirmed(repository, project_id, title="已确认文稿", content="压测后上线。", **kwargs):
    document = repository.create_document(project_id, title, **kwargs)
    version = repository.add_version(document["id"], content, origin="user")
    document = repository.confirm_version(document["id"], version["id"], expected_revision=repository.get_document(document["id"])["revision"])
    return document, version


def _download(client, project_id):
    response = client.get(f"/v2/knowledge/projects/{project_id}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert re.fullmatch(r'attachment; filename="project-[0-9a-f]{16}\.zip"', response.headers["content-disposition"])
    assert int(response.headers["content-length"]) == len(response.content)
    assert response.headers["cache-control"] == "no-store"
    archive = ZipFile(BytesIO(response.content))
    return archive, json.loads(archive.read("manifest.json"))


def test_download_complete_confirmed_text_state_wiki_and_nested_sources_without_mutation(api):
    client, repository = api
    project = repository.create_project("客户 A")
    root = repository.create_folder(project["id"], "合同")
    folder = repository.create_folder(project["id"], "发布", parent_id=root["id"])
    content = "压测后上线。负责人李明，周五完成压测。\n\n原文完整尾部。"
    document, version = _confirmed(repository, project["id"], content=content, folder_id=folder["id"])
    source = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    outcomes = ProjectOutcomes(repository)
    outcomes.create(project["id"], kind="decision", text="压测后上线", source_refs=[source], expected_project_revision=repository.get_project(project["id"])["revision"])
    outcomes.create(project["id"], kind="task", text="完成压测", status="done", owner="李明", due="周五", source_refs=[source], expected_project_revision=repository.get_project(project["id"])["revision"])
    outcomes.create(project["id"], kind="question", text="何时复核？", expected_project_revision=repository.get_project(project["id"])["revision"])
    before = list(repository._conn.iterdump())
    changes = repository._conn.total_changes
    archive, manifest = _download(client, project["id"])
    assert manifest["document_count"] == 1 and manifest["state_item_count"] == 3 and not manifest["truncated"]
    entry = manifest["documents"][0]
    assert entry["id"] == document["id"] and entry["version_id"] == version["id"]
    assert len(PurePosixPath(entry["text_path"]).parts) == 4
    assert archive.read(entry["text_path"]).decode() == content
    metadata = json.loads(archive.read(entry["metadata_path"]))
    assert metadata["spans"][0]["id"] == source["source_id"]
    assert metadata["spans"][0]["locator"] == source["locator"]
    state = json.loads(archive.read("state.json"))
    assert {item["type"] for item in state["items"]} == {"decision", "task", "question"}
    assert next(item for item in state["items"] if item["type"] == "task")["status"] == "done"
    wiki = archive.read("wiki.md").decode()
    assert "李明" in wiki and "已关闭或被替代" in wiki and "何时复核" in wiki
    for entry in manifest["files"]:
        body = archive.read(entry["path"])
        assert entry["sha256"] == hashlib.sha256(body).hexdigest() and len(body) == entry["size_bytes"]
    assert list(repository._conn.iterdump()) == before and repository._conn.total_changes == changes
    assert repository._conn.in_transaction is False


def test_export_excludes_other_project_drafts_deleted_private_stores_and_pending_proposals(api, tmp_path):
    client, repository = api
    project = repository.create_project("客户 A")
    other = repository.create_project("客户 B", "OTHER-PROJECT-PRIVATE")
    document, version = _confirmed(repository, project["id"], content="CONFIRMED-ONLY")
    repository.add_version(document["id"], "NEW-DRAFT-PRIVATE", origin="user")
    _confirmed(repository, other["id"], content="OTHER-DOCUMENT-PRIVATE")
    draft = repository.create_document(project["id"], "草稿")
    repository.add_version(draft["id"], "UNCONFIRMED-PRIVATE", origin="ai")
    deleted, _ = _confirmed(repository, project["id"], content="DELETED-PRIVATE")
    repository.update_document(deleted["id"], status="deleted")
    source = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    outcomes = ProjectOutcomes(repository)
    outcomes.propose(project["id"], [{"type": "decision", "text": "UNCONFIRMED-AI-PROPOSAL", "source_refs": [source]}], expected_project_revision=repository.get_project(project["id"])["revision"])
    from meeting_copilot_web_mvp.user_memory import UserMemory
    UserMemory(repository).create(scope="project", scope_id=project["id"], scene="general", kind="term",
                                  memory_key="private-candidate", value="PRIVATE-CANDIDATE", aliases=["PRIVATE-ALIAS"], origin="ai", source_refs=[source])
    for name in (".env", "private_settings.json", "user-private.log"):
        (tmp_path / name).write_text("PRIVATE-FILE-SECRET", encoding="utf-8")
    repository._conn.execute("UPDATE knowledge_documents SET original_relative_path='../private_settings.json' WHERE id=?", (document["id"],))
    archive, manifest = _download(client, project["id"])
    exported = "\n".join(archive.read(name).decode() for name in archive.namelist())
    assert "CONFIRMED-ONLY" in exported
    for forbidden in ("OTHER-PROJECT-PRIVATE", "OTHER-DOCUMENT-PRIVATE", "UNCONFIRMED-PRIVATE", "NEW-DRAFT-PRIVATE",
                      "DELETED-PRIVATE", "UNCONFIRMED-AI-PROPOSAL", "PRIVATE-CANDIDATE", "PRIVATE-ALIAS", "PRIVATE-FILE-SECRET",
                      "original_relative_path", "api_key", "lease_token"):
        assert forbidden not in exported
    assert manifest["document_count"] == 1 and manifest["excluded_document_count"] == 2


def test_unsafe_names_produce_short_safe_unique_zip_paths_and_no_header_injection(api):
    client, repository = api
    project = repository.create_project("../CON:evil.zip")
    folder = repository.create_folder(project["id"], "../../..\\private:C:\\env")
    _confirmed(repository, project["id"], title="../same\\CON:bad", folder_id=folder["id"])
    _confirmed(repository, project["id"], title="../same\\CON:bad", folder_id=folder["id"])
    archive, manifest = _download(client, project["id"])
    paths = archive.namelist()
    assert len(paths) == len(set(paths))
    for name in paths:
        assert not PurePosixPath(name).is_absolute() and ".." not in PurePosixPath(name).parts
        assert "\\" not in name and ":" not in name
        assert all(len(part) <= 70 for part in PurePosixPath(name).parts)
    assert manifest["document_count"] == 2


def test_source_revision_is_exported_as_stale_without_write_reconcile(api):
    client, repository = api
    project = repository.create_project("项目")
    document, version = _confirmed(repository, project["id"], content="旧决定")
    source = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    outcomes = ProjectOutcomes(repository)
    item = outcomes.create(project["id"], kind="decision", text="旧决定", source_refs=[source], expected_project_revision=repository.get_project(project["id"])["revision"])
    new = repository.add_version(document["id"], "新决定", origin="user")
    repository.confirm_version(document["id"], new["id"], expected_revision=repository.get_document(document["id"])["revision"])
    before = list(repository._conn.iterdump())
    archive, _ = _download(client, project["id"])
    state = json.loads(archive.read("state.json"))["items"][0]
    assert state["id"] == item["id"] and state["status"] == "active"
    assert state["export_effective_status"] == "needs_review" and state["evidence_status"] == "stale"
    assert "来源变化，待核对" in archive.read("wiki.md").decode()
    assert list(repository._conn.iterdump()) == before


def test_deleted_source_quotes_are_explicitly_omitted_from_retained_confirmed_state(api):
    client, repository = api
    project = repository.create_project("项目")
    document, version = _confirmed(repository, project["id"], content="REMOVED-QUOTE-PRIVATE")
    source = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    ProjectOutcomes(repository).create(project["id"], kind="decision", text="保留人工决定", source_refs=[source], expected_project_revision=repository.get_project(project["id"])["revision"])
    repository.update_document(document["id"], status="deleted")
    archive, manifest = _download(client, project["id"])
    assert manifest["document_count"] == 0
    refs = json.loads(archive.read("state.json"))["items"][0]["source_refs"]
    assert "quote" not in refs[0] and refs[0]["quote_omitted_reason"] == "source_deleted"
    assert "REMOVED-QUOTE-PRIVATE" not in "".join(archive.read(name).decode() for name in archive.namelist())


@pytest.mark.parametrize("limit", ["documents", "bytes", "spans", "state", "depth", "folders"])
def test_export_limits_fail_explicitly_instead_of_silent_truncation(api, monkeypatch, limit):
    client, repository = api
    project = repository.create_project("项目")
    folder = repository.create_folder(project["id"], "一")
    child = repository.create_folder(project["id"], "二", parent_id=folder["id"])
    _confirmed(repository, project["id"], content="首段\n\n次段", folder_id=child["id"])
    _confirmed(repository, project["id"], content="另一份")
    ProjectOutcomes(repository).create(project["id"], kind="task", text="用户确认事项", expected_project_revision=repository.get_project(project["id"])["revision"])
    fields = {"documents": ("MAX_EXPORT_DOCUMENTS", 1), "bytes": ("MAX_EXPORT_BYTES", 10), "spans": ("MAX_EXPORT_SPANS", 1),
              "state": ("MAX_EXPORT_STATE_ITEMS", 0), "depth": ("MAX_FOLDER_DEPTH", 1), "folders": ("MAX_EXPORT_FOLDERS", 1)}
    monkeypatch.setattr(project_export, *fields[limit])
    before = list(repository._conn.iterdump())
    response = client.get(f"/v2/knowledge/projects/{project['id']}/export")
    assert response.status_code == 413 and response.json()["detail"]["code"] == "project_export_too_large"
    assert response.headers["content-type"].startswith("application/json")
    assert list(repository._conn.iterdump()) == before and not repository._conn.in_transaction


def test_invalid_cross_project_reference_fails_without_disclosing_quote(api):
    client, repository = api
    project, other = repository.create_project("A"), repository.create_project("B")
    _, version = _confirmed(repository, other["id"], content="B-SOURCE-PRIVATE")
    source = repository.read_source(version["spans"][0]["id"], project_id=other["id"])
    item = ProjectOutcomes(repository).create(project["id"], kind="task", text="A事项", expected_project_revision=repository.get_project(project["id"])["revision"])
    repository._conn.execute("UPDATE project_state_items SET source_refs_json=? WHERE id=?", (json.dumps([source]), item["id"]))
    response = client.get(f"/v2/knowledge/projects/{project['id']}/export")
    assert response.status_code == 409 and "B-SOURCE-PRIVATE" not in response.text
    assert response.json()["detail"]["code"] == "project_export_invalid_source"


@pytest.mark.parametrize("forgery", ["foreign_span", "foreign_version", "same_project_wrong_version", "wrong_version_number"])
def test_document_source_span_and_version_must_belong_to_the_declared_document(api, forgery):
    client, repository = api
    project, other = repository.create_project("A"), repository.create_project("B")
    document, version = _confirmed(repository, project["id"], content="A approved")
    _, foreign = _confirmed(repository, other["id"], content="FOREIGN-PRIVATE")
    _, sibling = _confirmed(repository, project["id"], content="同项目其他资料")
    ref = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    item = ProjectOutcomes(repository).create(project["id"], kind="task", text="A事项", source_refs=[ref], expected_project_revision=repository.get_project(project["id"])["revision"])
    if forgery == "foreign_span":
        ref.update(source_id=foreign["spans"][0]["id"], quote="FOREIGN-PRIVATE")
    elif forgery == "foreign_version":
        ref.update(version_id=foreign["id"], content_hash=foreign["content_hash"], quote="FOREIGN-PRIVATE")
    elif forgery == "same_project_wrong_version":
        ref.update(version_id=sibling["id"], content_hash=sibling["content_hash"])
    else:
        ref["version"] = 99
    repository._conn.execute("UPDATE project_state_items SET source_refs_json=? WHERE id=?", (json.dumps([ref]), item["id"]))
    response = client.get(f"/v2/knowledge/projects/{project['id']}/export")
    assert response.status_code == 409 and response.json()["detail"]["code"] == "project_export_invalid_source"
    assert "FOREIGN-PRIVATE" not in response.text


def test_actual_human_final_provenance_locators_and_old_review_versions_are_retained_as_stale(api):
    client, repository = api
    from meeting_copilot_web_mvp.source_library_import import SourceLibraryImporter
    from meeting_copilot_web_mvp.v2_persistence import V2Persistence
    persistence = V2Persistence(repository.database_path)
    try:
        project = repository.create_project("真实来源链合成项目")
        persistence.create_meeting(meeting_id="synthetic-review", title="合成会议", now_ms=1)
        repository.bind_meeting_context("synthetic-review", project["id"])
        first = persistence.save_user_final_document(meeting_id="synthetic-review", document_kind="minutes", expected_revision=0,
                                                    content={"markdown": "人工旧结论：压测后上线。"}, now_ms=2)
        outcomes = ProjectOutcomes(repository)
        reference = outcomes.source(first["document_id"], project["id"], source_type="review_document")
        item = outcomes.create(project["id"], kind="decision", text="压测后上线", source_refs=[reference], expected_project_revision=repository.get_project(project["id"])["revision"])
        imported = SourceLibraryImporter(repository).import_final("synthetic-review", "minutes", expected_source_version=1)
        document = repository.confirm_version(imported["document"]["id"], imported["version"]["id"], expected_revision=imported["document"]["revision"])
        persistence.save_user_final_document(meeting_id="synthetic-review", document_kind="minutes", expected_revision=first["revision"],
                                             content={"markdown": "人工新结论：等待法务复核。"}, now_ms=3)
        before = list(repository._conn.iterdump())
        archive, manifest = _download(client, project["id"])
        exported_document = next(entry for entry in manifest["documents"] if entry["id"] == document["id"])
        metadata = json.loads(archive.read(exported_document["metadata_path"]))
        assert metadata["spans"][0]["locator"] == imported["version"]["spans"][0]["locator"]
        assert metadata["provenance"]["source_version"] == 1 and metadata["source_status"] == "needs_review"
        exported_item = next(entry for entry in json.loads(archive.read("state.json"))["items"] if entry["id"] == item["id"])
        assert exported_item["evidence_status"] == "stale"
        assert exported_item["source_refs"][0]["quote"] == reference["quote"]
        assert list(repository._conn.iterdump()) == before
        forged = {**reference, "version": 2}
        repository._conn.execute("UPDATE project_state_items SET source_refs_json=? WHERE id=?", (json.dumps([forged]), item["id"]))
        response = client.get(f"/v2/knowledge/projects/{project['id']}/export")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "project_export_invalid_source"
    finally:
        persistence.close()


def test_export_includes_more_than_300_documents_without_list_page_truncation(api):
    client, repository = api
    project = repository.create_project("完整项目")
    for index in range(301):
        _confirmed(repository, project["id"], title=f"文档{index}", content=f"完整内容{index}")
    archive, manifest = _download(client, project["id"])
    assert manifest["document_count"] == 301 and not manifest["truncated"]
    assert len([name for name in archive.namelist() if name.startswith("sources/") and name.endswith(".txt")]) == 301


def test_twelve_long_named_folder_levels_fit_windows_paths_and_wiki_labels_escape(api):
    client, repository = api
    project = repository.create_project("中文项目")
    parent = None
    for index in range(12):
        parent = repository.create_folder(project["id"], "目录" + "long-folder-name" * 10 + str(index), parent_id=parent)["id"]
    _confirmed(repository, project["id"], title="材料[一](最终)\\文稿", folder_id=parent)
    archive, manifest = _download(client, project["id"])
    path = manifest["documents"][0]["text_path"]
    assert len(path) <= 180 and len(PurePosixPath(path).parts) == 14
    folder_metadata = json.loads(archive.read("project.json"))["folders"]
    assert len(folder_metadata) == 12 and all("long-folder-name" in folder["name"] for folder in folder_metadata)
    assert "材料\\[一\\]\\(最终\\)\\\\文稿" in archive.read("wiki.md").decode()


def test_missing_historical_span_omits_quote_with_explicit_reason_and_storage_failure_is_public(api, monkeypatch):
    client, repository = api
    project = repository.create_project("项目")
    document, version = _confirmed(repository, project["id"], content="OLD-SPAN-QUOTE")
    ref = repository.read_source(version["spans"][0]["id"], project_id=project["id"])
    ProjectOutcomes(repository).create(project["id"], kind="task", text="已确认事项", source_refs=[ref], expected_project_revision=repository.get_project(project["id"])["revision"])
    new = repository.add_version(document["id"], "新版资料", origin="user")
    repository.confirm_version(document["id"], new["id"], expected_revision=repository.get_document(document["id"])["revision"])
    repository._conn.execute("DELETE FROM knowledge_spans WHERE id=?", (ref["source_id"],))
    archive, _ = _download(client, project["id"])
    state = json.loads(archive.read("state.json"))["items"][0]
    assert state["evidence_status"] == "stale" and state["source_refs"][0]["quote_omitted_reason"] == "source_version_unavailable"
    assert "quote" not in state["source_refs"][0]
    def disk_full(*_args, **_kwargs):
        raise OSError("private-device-path/SECRET")
    monkeypatch.setattr(project_export._ArchiveWriter, "text", disk_full)
    response = client.get(f"/v2/knowledge/projects/{project['id']}/export")
    assert response.status_code == 503 and response.json()["detail"]["code"] == "project_export_io_failed"
    assert "SECRET" not in response.text and not repository._conn.in_transaction


def test_same_read_snapshot_survives_concurrent_source_commit(api, monkeypatch):
    client, repository = api
    project = repository.create_project("项目")
    first, _ = _confirmed(repository, project["id"], content="FIRST-OLD")
    second, version = _confirmed(repository, project["id"], content="SECOND-OLD")
    original = project_export._ArchiveWriter.text
    changed = False
    def concurrent_write(writer, path, value):
        nonlocal changed
        if path.endswith(".txt") and path.startswith("sources/") and not changed:
            changed = True
            with sqlite3.connect(repository.database_path) as connection:
                connection.execute("UPDATE knowledge_versions SET content=?,content_hash=? WHERE id=?",
                                   ("SECOND-NEW", hashlib.sha256(b"SECOND-NEW").hexdigest(), version["id"]))
        return original(writer, path, value)
    monkeypatch.setattr(project_export._ArchiveWriter, "text", concurrent_write)
    archive, manifest = _download(client, project["id"])
    files = {entry["id"]: archive.read(entry["text_path"]).decode() for entry in manifest["documents"]}
    assert files[first["id"]] == "FIRST-OLD" and files[second["id"]] == "SECOND-OLD" and changed
    assert repository.get_version(second["id"])["content"] == "SECOND-NEW"


def test_missing_storage_unknown_project_and_archived_project_contract(api):
    client, repository = api
    assert client.get("/v2/knowledge/projects/no-such-project/export").status_code == 404
    project = repository.create_project("归档项目")
    _confirmed(repository, project["id"])
    repository.update_project(project["id"], archived=True)
    _, manifest = _download(client, project["id"])
    assert manifest["document_count"] == 1
    app = FastAPI()
    mount_project_export_api(app, None)
    with TestClient(app) as unavailable:
        assert unavailable.get("/v2/knowledge/projects/any/export").status_code == 503

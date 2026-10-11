from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.memory_candidates import explicit_declarations
from meeting_copilot_web_mvp.memory_jobs import MemoryReconcileQueue, MemoryReconcileRunner
from meeting_copilot_web_mvp.memory_jobs_api import mount_memory_jobs_api
from meeting_copilot_web_mvp.source_library_import import SourceLibraryImporter
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


TEXT = '术语纠正：将“托克吹斯”改为“Talktrace”。\n我希望回答简短。'


@pytest.fixture
def jobs(tmp_path):
    persistence = V2Persistence(tmp_path / "memory.db")
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("合成项目")
    persistence.create_meeting(meeting_id="next", title="下场", now_ms=1000)
    library.bind_meeting_context("next", project["id"])
    queue = MemoryReconcileQueue(library)
    library._memory_reconcile_queue = queue
    yield persistence, library, queue, project
    library.close()
    persistence.close()


def document(jobs, text=TEXT, origin="user"):
    _, library, _, project = jobs
    doc = library.create_document(project["id"], "人工声明")
    version = library.add_version(doc["id"], text, origin=origin, expected_revision=doc["revision"])
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    return doc, version


def terminal(job, *, empty=False):
    data = json.loads(job["source_payload_json"])["memory_reconciliation"]
    return {"input_hash": job["input_hash"], "candidates": [] if empty else data["clues"]}


@pytest.mark.parametrize("text", ["预算五万元，王五下周提交。", "他说：我希望回答简短。", "张三：\n我希望回答简短。", "张三：\n\n我希望回答简短。",
    "张三：\n我希望回答简短。\n我偏好低频提醒。", '客户表示：\n术语纠正：将“错名”改为“Tool”。\n术语纠正：将“另一个错名”改为“OtherTool”。',
    "我希望回答不要简短。", "术语纠正：将“   ”改为“Tool”。", "我希望关闭主动提醒，并保持正常提醒。"])
def test_ordinary_business_other_speakers_or_ambiguous_preferences_have_no_clue(text):
    assert explicit_declarations(text) == []


def test_explicit_term_and_finite_personal_preferences_are_bounded():
    clues = explicit_declarations(TEXT + "\n我偏好低频提醒。")
    assert [(item["kind"], item["value"]) for item in clues] == [("term", "Talktrace"), ("preference", "brief"), ("preference", "low_frequency")]
    assert len(explicit_declarations("\n".join(f'术语纠正：将“错{i}”改为“Tool{i}”。' for i in range(20)))) == 6


@pytest.mark.parametrize("origin,text", [("ai", TEXT), ("extracted", TEXT), ("user", "会议决定下周发版。"), ("meeting_user_final", TEXT)])
def test_no_eligible_source_or_clue_creates_no_job_and_no_paid_permission(jobs, origin, text):
    doc, version = document(jobs, text, origin)
    queue = jobs[2]
    assert queue.list(jobs[3]["id"])["items"] == []
    assert queue.claim() is None
    with pytest.raises(KnowledgeScopeError):
        queue.enqueue(doc["id"], expected_version_id=version["id"])


def test_confirm_hook_creates_once_and_candidates_require_user_activation(jobs):
    persistence, library, queue, project = jobs
    doc, version = document(jobs)
    initial = queue.list(project["id"])["items"][0]
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    assert len(queue.list(project["id"])["items"]) == 1
    claimed = queue.claim()
    queue.mark_started(claimed)
    ids = queue.finish(claimed, terminal(claimed))
    assert len(ids) == 2 and queue.get(initial["id"])["status"] == "succeeded"
    assert persistence._conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 0
    assert queue.memory.resolve("next")["terms"] == []
    assert queue.memory.resolve("next")["preferences"] == {}
    candidates = queue.memory.list(scope="project", scope_id=project["id"])["items"]
    assert all(item["origin"] == "ai" and item["status"] == "candidate" and item["reconciliation_job_id"] == claimed["id"] for item in candidates)
    for item in candidates:
        queue.memory.update(item["id"], expected_revision=item["revision"], status="active")
    assert queue.memory.resolve("next")["hotwords"] == ["Talktrace"]
    assert queue.memory.resolve("next")["preferences"] == {"response_length": "brief"}
    assert queue.claim() is None


@pytest.mark.parametrize("mutation", ["new_version", "disable", "settings", "memory", "cancel"])
def test_commit_barrier_rejects_late_candidate_atomically(jobs, mutation):
    _, library, queue, project = jobs
    doc, _ = document(jobs)
    claimed = queue.claim()
    output = terminal(claimed)
    if mutation == "new_version":
        version = library.add_version(doc["id"], TEXT + "\n新增原文。", origin="user", expected_revision=library.get_document(doc["id"])["revision"])
        library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    elif mutation == "disable":
        library.set_document_status(doc["id"], "disabled", expected_revision=library.get_document(doc["id"])["revision"])
    elif mutation == "settings":
        queue.set_settings(project["id"], mode="off", expected_revision=0)
    elif mutation == "memory":
        queue.memory.create(scope="global", kind="term", memory_key="other", value="Other")
    else:
        queue.cancel(claimed["id"])
    with pytest.raises((KnowledgeConflict, KnowledgeScopeError)):
        queue.finish(claimed, output)
    assert queue.memory.list(scope="project", scope_id=project["id"])["items"] == []


def test_deleted_source_exclusion_prevents_recreation_on_next_version(jobs):
    _, library, queue, project = jobs
    doc, _ = document(jobs, '术语纠正：将“托克吹斯”改为“Talktrace”。')
    claimed = queue.claim()
    ids = queue.finish(claimed, terminal(claimed))
    queue.memory.update(ids[0], expected_revision=1, status="deleted")
    version = library.add_version(doc["id"], '术语纠正：将“托克吹斯”改为“Talktrace”。\n其他业务更新。', origin="user", expected_revision=library.get_document(doc["id"])["revision"])
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    assert queue.claim() is None
    assert len(queue.list(project["id"])["items"]) == 1
    assert queue.memory.list(scope="project", scope_id=project["id"])["items"] == []


def test_semantic_duplicates_do_not_create_another_candidate_and_conflicts_are_host_owned(jobs):
    _, _, queue, project = jobs
    active = queue.memory.create(scope="project", scope_id=project["id"], kind="preference", memory_key="response_length", value="standard")
    queue.memory.update(active["id"], expected_revision=1, status="active")
    document(jobs, "我希望回答简短。")
    claimed = queue.claim()
    ids = queue.finish(claimed, terminal(claimed))
    candidate = queue.memory.list(scope="project", scope_id=project["id"])["items"]
    assert next(item for item in candidate if item["id"] == ids[0])["conflicts_with_ids"] == [active["id"]]
    assert queue.memory.resolve("next")["preferences"] == {"response_length": "standard"}
    document(jobs, "我希望回答简短。")
    second = queue.claim()
    assert second is None
    assert len(queue.list(project["id"])["items"]) == 1
    assert len(queue.memory.list(scope="project", scope_id=project["id"])["items"]) == 2


@pytest.mark.parametrize("mutation", ["hash", "value", "quote", "conflict_ids", "too_many"])
def test_model_cannot_invent_values_refs_snapshot_or_conflicts(jobs, mutation):
    document(jobs)
    queue = jobs[2]
    claimed = queue.claim()
    value = terminal(claimed)
    if mutation == "hash":
        value["input_hash"] = "bad"
    elif mutation == "value":
        value["candidates"][0]["value"] = "Invented"
    elif mutation == "quote":
        value["candidates"][0]["source_refs"][0]["quote"] = "已完成业务任务。"
    elif mutation == "conflict_ids":
        value["candidates"][0]["conflicts_with_ids"] = ["model-override"]
    else:
        value["candidates"] *= 4
    with pytest.raises(ValueError):
        queue.finish(claimed, value)
    assert queue.memory.list(scope="project", scope_id=jobs[3]["id"])["items"] == []


def test_restart_polling_and_repeat_confirmation_never_rearm_old_work(jobs):
    _, library, queue, project = jobs
    doc, version = document(jobs)
    job = queue.list(project["id"])["items"][0]
    reopened = MemoryReconcileQueue(library)
    assert reopened.claim() is None
    library._memory_reconcile_queue = reopened
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    assert reopened.claim() is None
    reopened.retry(job["id"], expected_version_id=version["id"])
    claimed = reopened.claim()
    reopened.mark_started(claimed)
    with library._write():
        library._conn.execute("UPDATE memory_reconcile_jobs SET lease_until_ms=1 WHERE id=?", (claimed["id"],))
    reopened.recover_expired()
    assert reopened.get(job["id"])["status"] == "failed"
    assert reopened.claim() is None
    reopened.retry(job["id"], expected_version_id=version["id"])
    assert reopened.claim()["attempts"] == 1


def test_settings_off_cancels_same_transaction_and_runner_waits_for_ack(jobs):
    async def run():
        _, _, queue, project = jobs
        document(jobs)
        job = queue.list(project["id"])["items"][0]
        entered, ack = asyncio.Event(), asyncio.Event()
        async def executor(payload, claimed):
            queue.mark_started(claimed)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                ack.set()
        runner = MemoryReconcileRunner(queue, executor)
        await runner.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            settings, ids = queue.set_settings(project["id"], mode="off", expected_revision=0)
            assert ids == [job["id"]] and settings["mode"] == "off"
            assert queue.get(job["id"])["status"] == "cancelled"
            await runner.cancel(job["id"])
            assert ack.is_set()
            assert queue.claim() is None
        finally:
            await runner.stop()
    asyncio.run(run())


def test_real_human_final_requires_saved_provenance_and_own_preference_marker(jobs):
    persistence, library, queue, project = jobs
    persistence.save_user_final_document(meeting_id="next", document_kind="minutes", expected_revision=0,
        content={"markdown": "我希望回答简短。\n用户本人偏好：回答简短。"}, now_ms=2000)
    imported = SourceLibraryImporter(library).import_final("next", "minutes", expected_source_version=1)
    doc, version = imported["document"], imported["version"]
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    job = queue.list(project["id"])["items"][0]
    clues = terminal(job)["candidates"]
    assert len(clues) == 1 and clues[0]["source_refs"][0]["quote"] == "用户本人偏好：回答简短。"


def test_job_api_public_settings_pagination_safe_fields_and_explicit_resume(jobs):
    _, library, _, project = jobs
    app = FastAPI()
    mount_memory_jobs_api(app, library)
    queue = app.state.memory_reconcile_queue
    doc, version = document(jobs)
    document(jobs, "我偏好正常提醒。")
    # No lifespan: API reads are independently tested without starting any worker.
    client = TestClient(app)
    base = "/v2/knowledge"
    settings_path = f"{base}/projects/{project['id']}/memory-reconcile-settings"
    assert client.get(settings_path).json()["settings"]["revision"] == 0
    page = client.get(f"{base}/memory-reconcile/jobs", params={"project_id": project["id"], "limit": 1}).json()
    assert page["has_more"] and page["next_cursor"]
    following = client.get(f"{base}/memory-reconcile/jobs", params={"project_id": project["id"], "limit": 1, "cursor": page["next_cursor"]}).json()
    assert page["items"][0]["id"] != following["items"][0]["id"]
    assert not {"input_hash", "snapshot_json", "source_payload_json", "source_hash", "lease_owner"}.intersection(page["items"][0])
    queue.eligible.clear()
    resumable = client.get(f"{base}/memory-reconcile/jobs", params={"project_id": project["id"]}).json()["items"]
    assert all(item["requires_resume"] and item["can_retry"] for item in resumable)
    job = client.post(f"{base}/documents/{doc['id']}/memory-reconcile", json={"expected_version_id": version["id"]}).json()["job"]
    assert not job["requires_resume"] and job["attempts"] == 0
    off = client.put(settings_path, json={"mode": "off", "expected_revision": 0})
    assert off.status_code == 200
    stopped = client.get(f"{base}/memory-reconcile/jobs", params={"project_id": project["id"]}).json()["items"]
    assert all(item["status"] == "cancelled" and not item["can_retry"] for item in stopped)
    assert client.post(f"{base}/memory-reconcile/jobs/{job['id']}/retry", json={"expected_version_id": version["id"]}).status_code == 409
    assert client.put(settings_path, json={"mode": "explicit_only", "expected_revision": 0}).status_code == 409
    assert client.get(f"{base}/memory-reconcile/jobs", params={"project_id": project["id"], "cursor": "bad"}).status_code == 400
    assert all(item["attempts"] == 0 for item in queue.list(project["id"])["items"])


def test_off_on_and_new_worker_do_not_scan_or_restart_old_versions(jobs):
    _, library, queue, project = jobs
    doc, version = document(jobs)
    queue.set_settings(project["id"], mode="off", expected_revision=0)
    queue.set_settings(project["id"], mode="explicit_only", expected_revision=1)
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    assert queue.claim() is None
    reopened = MemoryReconcileQueue(library)
    assert reopened.claim() is None
    assert all(item["attempts"] == 0 for item in reopened.list(project["id"])["items"])


def test_bounded_evidence_is_anchored_to_a_later_explicit_declaration(jobs):
    document(jobs, "普通业务说明。" * 120 + "\n我希望回答简短。")
    claimed = jobs[2].claim()
    payload = json.loads(claimed["source_payload_json"])
    assert payload["document_evidence"][0]["quote"] == "我希望回答简短。"
    assert len(terminal(claimed)["candidates"]) == 1


@pytest.mark.parametrize("status", ["pending", "running", "succeeded"])
def test_source_physical_cleanup_cascades_snapshot_and_blocks_late_write(jobs, status):
    _, library, queue, project = jobs
    doc, _ = document(jobs)
    claimed = queue.list(project["id"])["items"][0] if status == "pending" else queue.claim()
    output = terminal(claimed)
    if status == "succeeded":
        ids = queue.finish(claimed, output)
        for memory_id in ids:
            queue.memory.update(memory_id, expected_revision=1, status="active")
    library.set_document_status(doc["id"], "deleted", expected_revision=library.get_document(doc["id"])["revision"])
    with library._write():
        library._conn.execute("DELETE FROM knowledge_spans WHERE version_id IN (SELECT id FROM knowledge_versions WHERE document_id=?)", (doc["id"],))
        library._conn.execute("DELETE FROM knowledge_versions WHERE document_id=?", (doc["id"],))
        library._conn.execute("UPDATE knowledge_documents SET active_version_id=NULL WHERE id=?", (doc["id"],))
    assert queue.list(project["id"])["items"] == []
    assert queue.memory.resolve("next")["terms"] == [] and queue.memory.resolve("next")["preferences"] == {}
    assert all(item["status"] == "needs_review" for item in queue.memory.list(scope="project", scope_id=project["id"])["items"])
    with pytest.raises(KeyError):
        queue.finish(claimed, output)


@pytest.mark.parametrize("source_kind", ["ask_ai", "manual"])
def test_unmodified_ai_notes_are_not_own_memory(jobs, source_kind):
    persistence, library, queue, project = jobs
    persistence.create_note(note_id="own-note", meeting_id="next", title="声明", body="我希望回答简短。", source_kind=source_kind,
        source_message_id=None, evidence=[], now_ms=2000)
    imported = SourceLibraryImporter(library).import_note("own-note", project["id"], expected_source_version=1)
    doc, version = imported["document"], imported["version"]
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    assert len(queue.list(project["id"])["items"]) == (1 if source_kind == "manual" else 0)

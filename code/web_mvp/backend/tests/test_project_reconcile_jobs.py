from __future__ import annotations

import asyncio
import json

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.project_reconcile_jobs import BackgroundReconcileDeferred, ProjectReconcileQueue, ProjectReconcileRegistry, ProjectReconcileRunner
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


@pytest.fixture
def jobs(tmp_path):
    persistence = V2Persistence(tmp_path / "reconcile.db")
    with persistence._write_transaction():
        persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','ended',100,100)")
    final = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0,
        content={"markdown": "张三将在下周三提交方案。预算由五万元改为八万元。"}, now_ms=200)
    library = KnowledgeRepository(persistence.database_path)
    project = library.create_project("项目")
    library.bind_meeting_context("meeting", project["id"])
    outcomes = ProjectOutcomes(library)
    queue = ProjectReconcileQueue(library, outcomes)
    yield persistence, library, outcomes, queue, project, final
    library.close()
    persistence.close()


def enqueue(jobs):
    _, library, _, queue, project, _ = jobs
    return queue.enqueue("meeting", expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=1)


def terminal(job, *, changes=True):
    ref = json.loads(job["source_refs_json"])[0]
    ref["quote"] = "张三将在下周三提交方案。"
    return {"base_project_revision": job["base_project_revision"], "changes": [{"action": "create", "type": "task", "text": "提交方案",
        "status": "active", "owner": "张三", "due": "下周三", "source_refs": [ref]}] if changes else []}


def test_no_audio_real_final_can_enqueue_validate_and_persist_only_pending_candidate(jobs):
    persistence, library, outcomes, queue, project, _ = jobs
    job = enqueue(jobs)
    assert enqueue(jobs)["id"] == job["id"]
    assert persistence._conn.execute("SELECT COUNT(*) FROM transcript_segments").fetchone()[0] == 0
    claimed = queue.claim()
    registry = ProjectReconcileRegistry(queue, claimed)
    changes = registry.validate_project_changes(terminal(claimed))
    assert outcomes.proposals(project["id"])["items"] == []
    assert outcomes.list(project["id"])["items"] == []
    assert registry.read_project_state()[0]["project_revision"] == job["base_project_revision"]
    queue.mark_started(claimed)
    proposal = queue.finish(claimed, changes)
    assert proposal["status"] == "pending" and proposal["origin"] == "ai"
    assert outcomes.list(project["id"])["items"] == []
    assert queue.get(job["id"])["result_proposal_id"] == proposal["id"]
    confirmed = outcomes.confirm(proposal["id"], expected_revision=1, expected_project_revision=job["base_project_revision"])
    assert confirmed["items"][0]["owner"] == "张三" and confirmed["items"][0]["due"] == "下周三"
    reopened_library = KnowledgeRepository(library.database_path)
    try:
        reopened = ProjectReconcileQueue(reopened_library, ProjectOutcomes(reopened_library))
        assert reopened.get(job["id"])["status"] == "succeeded"
    finally:
        reopened_library.close()


def test_meeting_attachment_is_usable_live_but_cannot_become_confirmed_project_evidence(jobs, tmp_path):
    from meeting_copilot_web_mvp.document_import import extract_document
    from meeting_copilot_web_mvp.knowledge_context import retrieve_meeting_knowledge
    from meeting_copilot_web_mvp.meeting_material_jobs import MeetingMaterialJobQueue
    from meeting_copilot_web_mvp.meeting_materials import MeetingMaterialImportService, MeetingMaterialsRepository

    _, library, outcomes, queue, project, _ = jobs
    materials = MeetingMaterialsRepository(library)
    draft = materials.create_set()
    raw = "本场临时预算是三十万元。".encode()
    attachment = MeetingMaterialImportService(materials, tmp_path / "materials").stage(draft["id"], "budget.txt", raw)
    imports = MeetingMaterialJobQueue(materials)
    assert imports.finish(imports.claim(), extract_document("budget.txt", raw, "text/plain"))
    attachment = materials.get_attachment(draft["id"], attachment["id"])
    materials.usage(draft["id"], attachment["id"], enabled=True, version_id=attachment["latest_version_id"],
                    expected_revision=attachment["revision"])
    materials.bind("meeting", draft["id"])
    live_evidence = retrieve_meeting_knowledge(library, "meeting", "预算", include_attachments=True)
    assert live_evidence[0]["source_type"] == "meeting_attachment"
    enqueue(jobs)
    registry = ProjectReconcileRegistry(queue, queue.claim())
    assert registry.search_documents("预算") == []
    with pytest.raises(ValueError, match="confirmed project documents"):
        registry.register_documents(live_evidence)
    assert outcomes.list(project["id"])["items"] == []
    assert outcomes.proposals(project["id"])["items"] == []


@pytest.mark.parametrize("mutation", ["final", "project", "scope", "memory"])
def test_input_revision_barrier_rejects_late_candidate_without_overwrite(jobs, mutation):
    persistence, library, outcomes, queue, project, final = jobs
    enqueue(jobs)
    claimed = queue.claim()
    changes = terminal(claimed)
    if mutation == "final":
        persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "取消旧安排。"}, now_ms=400)
    elif mutation == "project":
        outcomes.create(project["id"], kind="decision", text="用户已改为新的安排", expected_project_revision=library.get_project(project["id"])["revision"])
    elif mutation == "scope":
        library.bind_meeting_context("meeting", document_ids=[], project_id=project["id"])
    else:
        from meeting_copilot_web_mvp.user_memory import UserMemory
        memory = UserMemory(library)
        candidate = memory.create(scope="global", scope_id="", kind="term", memory_key="法务", value="法律事务部", origin="user")
        memory.update(candidate["id"], expected_revision=candidate["revision"], status="active")
    with pytest.raises((KnowledgeConflict, KnowledgeScopeError)):
        queue.finish(claimed, changes)
    assert outcomes.proposals(project["id"])["items"] == []


def test_cancel_rejects_late_completion_and_explicit_retry_recaptures_current_source(jobs):
    persistence, library, outcomes, queue, project, final = jobs
    original = enqueue(jobs)
    claimed = queue.claim()
    queue.cancel(original["id"])
    with pytest.raises(KnowledgeConflict):
        queue.finish(claimed, terminal(claimed))
    new = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "李四将在下周四提交方案。"}, now_ms=500)
    retried = queue.retry(original["id"], expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=new["user_final"]["version"])
    assert retried["id"] == original["id"] and retried["status"] == "pending" and retried["attempts"] == 0
    assert "李四" in retried["source_payload_json"]
    assert outcomes.proposals(project["id"])["items"] == []


def test_stale_queued_snapshot_fails_without_starting_paid_work(jobs):
    persistence, _, _, queue, _, final = jobs
    job = enqueue(jobs)
    persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "来源改变。"}, now_ms=500)
    assert queue.claim() is None
    assert queue.get(job["id"])["status"] == "failed" and queue.get(job["id"])["attempts"] == 0


def test_unpaid_lease_recovers_and_paid_lease_requires_manual_retry(jobs):
    _, _, _, queue, _, _ = jobs
    job = enqueue(jobs)
    claimed = queue.claim(now_ms=1000)
    assert queue.recover_expired(now_ms=80_000) == 1
    assert queue.get(job["id"])["status"] == "pending"
    claimed = queue.claim(now_ms=80_100)
    queue.mark_started(claimed)
    assert queue.recover_expired(now_ms=200_000) == 1
    assert queue.get(job["id"])["status"] == "failed" and queue.get(job["id"])["error_class"] == "execution_interrupted"


def test_defer_has_finite_unpaid_budget_and_paid_preemption_does_not_rerun(jobs):
    _, library, _, queue, _, _ = jobs
    job = enqueue(jobs)
    claimed = queue.claim()
    queue.defer(claimed, BackgroundReconcileDeferred())
    assert queue.get(job["id"])["status"] == "pending" and queue.get(job["id"])["attempts"] == 0
    with library._write():
        library._conn.execute("UPDATE project_reconcile_jobs SET next_attempt_at_ms=0,defer_count=30 WHERE id=?", (job["id"],))
    claimed = queue.claim()
    queue.defer(claimed, BackgroundReconcileDeferred())
    assert queue.get(job["id"])["status"] == "failed"
    queue.retry(job["id"], expected_project_revision=job["base_project_revision"], expected_source_version=1)
    claimed = queue.claim()
    queue.mark_started(claimed)
    queue.defer(claimed, BackgroundReconcileDeferred(code="preempted_by_realtime"))
    assert queue.get(job["id"])["status"] == "failed" and queue.get(job["id"])["attempts"] == 1


@pytest.mark.parametrize("forgery", ["base", "owner", "quote", "hash", "source", "too_many"])
def test_host_rejects_forged_changes_and_never_writes_candidates(jobs, forgery):
    _, _, outcomes, queue, project, _ = jobs
    enqueue(jobs)
    claimed = queue.claim()
    changes = terminal(claimed)
    if forgery == "base":
        changes["base_project_revision"] += 1
    elif forgery == "owner":
        changes["changes"][0]["owner"] = "王五"
    elif forgery == "quote":
        changes["changes"][0]["source_refs"][0]["quote"] = "这是假的已批准安排。"
    elif forgery == "hash":
        changes["changes"][0]["source_refs"][0]["content_hash"] = "a" * 64
    elif forgery == "source":
        changes["changes"][0]["source_refs"][0]["source_id"] = "unknown"
    else:
        changes["changes"] *= 13
    with pytest.raises((ValueError, KnowledgeScopeError)):
        ProjectReconcileRegistry(queue, claimed).validate_project_changes(changes)
    assert outcomes.proposals(project["id"])["items"] == []


def test_zero_changes_succeeds_without_creating_an_empty_proposal(jobs):
    _, _, outcomes, queue, project, _ = jobs
    original = enqueue(jobs)
    claimed = queue.claim()
    queue.mark_started(claimed)
    empty = ProjectReconcileRegistry(queue, claimed).validate_project_changes(terminal(claimed, changes=False))
    assert queue.finish(claimed, empty) is None
    assert queue.get(original["id"])["status"] == "succeeded"
    assert queue.get(original["id"])["result_proposal_id"] is None
    assert outcomes.proposals(project["id"])["items"] == []


def test_single_background_runner_closes_with_no_leaking_task_and_stops_cancelled_result(jobs):
    async def run():
        _, _, _, queue, _, _ = jobs
        job = enqueue(jobs)
        started, released = asyncio.Event(), asyncio.Event()
        active, maximum = 0, 0
        async def execute(payload, claimed):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            started.set()
            try:
                await released.wait()
                return {"host_project_changes": terminal(claimed)}
            finally:
                active -= 1
        runner = ProjectReconcileRunner(queue, execute)
        await runner.start()
        await asyncio.wait_for(started.wait(), timeout=2)
        await runner.cancel(job["id"])
        released.set()
        await runner.stop()
        assert maximum == 1 and active == 0 and runner._task is None
        assert queue.get(job["id"])["status"] == "cancelled"
    asyncio.run(run())


def test_sqlite_claim_is_single_flight_across_repository_connections(jobs):
    persistence, library, _, queue, project, final = jobs
    enqueue(jobs)
    first = queue.claim()
    updated = persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=final["revision"], content={"markdown": "新的人工安排。"}, now_ms=700)
    next_job = queue.enqueue("meeting", expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=updated["user_final"]["version"])
    other = KnowledgeRepository(library.database_path)
    try:
        other_queue = ProjectReconcileQueue(other, ProjectOutcomes(other))
        assert other_queue.claim() is None
        queue.cancel(first["id"])
        assert other_queue.claim()["id"] == next_job["id"]
    finally:
        other.close()


@pytest.mark.parametrize("completion", ["success", "cancel"])
def test_deleted_meeting_job_is_fenced_and_runner_continues_with_another_meeting(jobs, completion):
    async def run():
        persistence, library, outcomes, queue, project, _ = jobs
        removed = enqueue(jobs)
        started, release = asyncio.Event(), asyncio.Event()
        async def execute(payload, claimed):
            queue.mark_started(claimed)
            if claimed["id"] == removed["id"]:
                started.set()
                await release.wait()
                if completion == "cancel":
                    raise asyncio.CancelledError
            return {"host_project_changes": terminal(claimed)}
        runner = ProjectReconcileRunner(queue, execute)
        await runner.start()
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            captured = queue.get(removed["id"])
            with persistence._write_transaction():
                counts = persistence._purge_all_meeting_data_locked("meeting", now_ms=400)
            assert counts["project_reconcile_jobs"] == 1
            with pytest.raises(KeyError):
                queue.get(removed["id"])
            with pytest.raises(KnowledgeConflict):
                queue.finish(captured, terminal(captured))
            release.set()
            with persistence._write_transaction():
                persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('next','ended',500,500)")
            persistence.save_user_final_document(meeting_id="next", document_kind="minutes", expected_revision=0,
                content={"markdown": "张三将在下周三提交方案。"}, now_ms=600)
            library.bind_meeting_context("next", project["id"])
            following = queue.enqueue("next", expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=1)
            async def completed():
                while queue.get(following["id"])["status"] in {"pending", "running"}:
                    assert not runner._task.done()
                    await asyncio.sleep(0.02)
            await asyncio.wait_for(completed(), timeout=3)
            assert queue.get(following["id"])["status"] == "succeeded"
            assert len(outcomes.proposals(project["id"])["items"]) == 1
        finally:
            release.set()
            await runner.stop()
    asyncio.run(run())


def test_ai_discard_persists_new_evidence_rather_than_the_empty_old_refs(jobs):
    _, library, outcomes, queue, project, _ = jobs
    old = outcomes.create(project["id"], kind="decision", text="旧预算规则", expected_project_revision=library.get_project(project["id"])["revision"])
    enqueue(jobs)
    job = queue.claim()
    ref = json.loads(job["source_refs_json"])[0]
    changes = {"base_project_revision": job["base_project_revision"], "changes": [{"action": "discard", "type": "decision", "item_id": old["id"], "source_refs": [ref]}]}
    ProjectReconcileRegistry(queue, job).validate_project_changes(changes)
    proposal = queue.finish(job, changes)
    confirmed = outcomes.confirm(proposal["id"], expected_revision=1, expected_project_revision=job["base_project_revision"])
    assert confirmed["items"][0]["status"] == "discarded"
    assert confirmed["items"][0]["source_refs"][0]["source_id"] == ref["source_id"]

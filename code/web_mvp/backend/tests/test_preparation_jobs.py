from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from meeting_copilot_web_mvp.application_schema import (
    APPLICATION_SCHEMA_MIGRATIONS,
    APPLICATION_SCHEMA_VERSION,
    bootstrap_application_schema,
)
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.preparation_jobs import PreparationQueue, PreparationRunner
from meeting_copilot_web_mvp.preparation_jobs_api import mount_preparation_jobs_api, public_preparation_job
from meeting_copilot_web_mvp.project_reconcile_jobs import BackgroundReconcileDeferred
from meeting_copilot_web_mvp.sqlite_schema import migrate_sqlite_schema


@pytest.fixture
def prepared(tmp_path):
    library = KnowledgeRepository(tmp_path / "synthetic.db")
    project = library.create_project("客户A")
    folder = library.create_folder(project["id"], "发布资料")
    other = library.create_folder(project["id"], "其他资料")
    docs = []
    for title, target, content in [("本次条件", folder, "客户A预算120元；需要压测通过。"), ("不允许资料", other, "客户A预算900元。")]:
        doc = library.create_document(project["id"], title, folder_id=target["id"])
        version = library.add_version(doc["id"], content, expected_revision=doc["revision"])
        library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
        docs.append((doc, version))
    queue = PreparationQueue(library)
    yield library, queue, project, folder, other, docs
    library.close()


def enqueue(prepared, **kwargs):
    library, queue, project, folder, _, _ = prepared
    return queue.enqueue(project["id"], **({"folder_id": folder["id"], "goal": "预算 压测", "role": "项目负责人", "focus": ["验收条件"],
        "expected_project_revision": library.get_project(project["id"])["revision"]} | kwargs))


def terminal(job, *, source=True):
    payload = json.loads(job["source_payload_json"])["preparation"]
    ref = payload["sources"][0] if source else None
    return {"input_hash": job["input_hash"], "agenda": [{"text": "明确预算和压测条件。", "basis": "user_goal", "source_refs": []}],
        "questions": [{"text": "压测需要满足哪些条件？", "basis": "user_goal", "source_refs": []}],
        "conditions": [{"text": "需要核对预算120元的适用范围。", "basis": "source", "source_refs": [ref]}] if ref else []}


def test_scope_snapshot_dedup_success_and_no_meeting_or_project_mutation(prepared):
    library, queue, project, folder, _, docs = prepared
    revision = library.get_project(project["id"])["revision"]
    job = enqueue(prepared)
    assert enqueue(prepared)["id"] == job["id"]
    sources = json.loads(job["source_payload_json"])["preparation"]["sources"]
    assert sources and {ref["document_id"] for ref in sources} == {docs[0][0]["id"]}
    running = queue.claim()
    queue.mark_started(running)
    result = queue.finish(running, terminal(running))
    assert result["conditions"][0]["source_refs"] == [sources[0]]
    assert queue.get(job["id"])["attempts"] == 1 and queue.get(job["id"])["status"] == "succeeded"
    assert enqueue(prepared)["id"] == job["id"] and queue.claim() is None
    assert library.get_project(project["id"])["revision"] == revision
    assert library._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    assert library._conn.execute("SELECT COUNT(*) FROM project_change_proposals").fetchone()[0] == 0
    assert library._conn.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0] == 0


def test_empty_scope_has_only_explicit_goal_planning_and_no_source_less_state(prepared):
    library, queue, project, _, _, _ = prepared
    queue.sources.outcomes.create(project["id"], kind="decision", text="全项目人工结论", expected_project_revision=library.get_project(project["id"])["revision"])
    job = enqueue(prepared, folder_id=None, document_ids=[], goal="确认交付范围")
    payload = json.loads(job["source_payload_json"])
    assert payload["document_evidence"] == [] and payload["preparation"]["sources"] == []
    running = queue.claim()
    assert queue.finish(running, terminal(running, source=False))["conditions"] == []


def test_selected_scope_filters_source_bound_memory_but_keeps_manual_preferences(prepared):
    library, queue, project, _, other, docs = prepared
    ref = library.read_source(docs[0][1]["spans"][0]["id"], project_id=project["id"])
    memory = queue.sources.memory.create(scope="project", scope_id=project["id"], kind="term", memory_key="term", value="Talktrace", aliases=["错字"], source_refs=[ref], origin="ai")
    queue.sources.memory.update(memory["id"], expected_revision=memory["revision"], status="active")
    preference = queue.sources.memory.create(scope="global", kind="preference", memory_key="response_length", value="brief")
    queue.sources.memory.update(preference["id"], expected_revision=preference["revision"], status="active")
    job = enqueue(prepared, folder_id=other["id"], document_ids=[docs[1][0]["id"]])
    projection = json.loads(job["source_payload_json"])["user_memory"]
    assert projection == {"terms": [], "preferences": {"response_length": "brief"}}
    empty = enqueue(prepared, document_ids=[])
    assert json.loads(empty["source_payload_json"])["user_memory"] == projection


def test_state_nested_actual_quote_supports_owner_and_deadline_without_metadata_as_fact(prepared):
    library, queue, project, _, _, docs = prepared
    doc = library.get_document(docs[0][0]["id"])
    version = library.add_version(doc["id"], "王工负责提交方案，期限下周三。", expected_revision=doc["revision"])
    library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    ref = library.read_source(version["spans"][0]["id"], project_id=project["id"])
    queue.sources.outcomes.create(project["id"], kind="task", text="提交方案", owner="王工", due="下周三", source_refs=[ref], expected_project_revision=library.get_project(project["id"])["revision"])
    enqueue(prepared, goal="提交方案")
    running = queue.claim()
    state = next(item for item in json.loads(running["source_payload_json"])["preparation"]["sources"] if item["source_type"] == "project_state")
    value = terminal(running, source=False)
    value["conditions"] = [{"text": "王工负责提交方案，期限下周三。", "basis": "source", "source_refs": [state]}]
    assert queue.finish(running, value)["conditions"] == value["conditions"]


def test_manual_confirmed_state_properties_are_visible_and_revision_verified(prepared):
    library, queue, project, _, _, _ = prepared
    queue.sources.outcomes.create(project["id"], kind="task", text="提交方案", owner="王工", due="下周三", expected_project_revision=library.get_project(project["id"])["revision"])
    enqueue(prepared, folder_id=None, goal="提交方案")
    running = queue.claim()
    state = next(item for item in json.loads(running["source_payload_json"])["preparation"]["sources"] if item["source_type"] == "project_state")
    assert state["quote"] == "提交方案" and state["owner"] == "王工" and state["due"] == "下周三" and state["status"] == "active"
    value = terminal(running, source=False)
    value["conditions"] = [{"text": "负责人为王工，期限下周三。", "basis": "source", "source_refs": [state]}]
    assert queue.finish(running, value)["conditions"] == value["conditions"]


def test_goal_only_qualitative_assertions_are_not_planning(prepared):
    _, queue, _, _, _, _ = prepared
    enqueue(prepared, document_ids=[], goal="希望完成签约")
    running = queue.claim()
    value = terminal(running, source=False)
    value["agenda"][0]["text"] = "客户已经信任我们的方案。"
    with pytest.raises(ValueError, match="plans or explicit questions"):
        queue.finish(running, value)


@pytest.mark.parametrize("mutation", ["hash", "outside_source", "invented_number", "goal_condition", "empty", "reference_budget"])
def test_host_rejects_fabricated_or_unbounded_results(prepared, mutation):
    _, queue, _, _, _, docs = prepared
    job = enqueue(prepared)
    running = queue.claim()
    value = terminal(running)
    if mutation == "hash":
        value["input_hash"] = "0" * 64
    elif mutation == "outside_source":
        value["conditions"][0]["source_refs"][0]["document_id"] = docs[1][0]["id"]
    elif mutation == "invented_number":
        value["conditions"][0]["text"] = "预算已调整到900元。"
    elif mutation == "goal_condition":
        value["conditions"] = [{"text": "已批准发布。", "basis": "user_goal", "source_refs": []}]
    elif mutation == "empty":
        value.update(agenda=[], questions=[], conditions=[])
    else:
        value["conditions"][0]["source_refs"] *= 13
    with pytest.raises((ValueError, KnowledgeScopeError)):
        queue.finish(running, value)
    assert queue.get(job["id"])["result_json"] is None


@pytest.mark.parametrize("mutation", ["version", "disable", "memory", "project", "cancel"])
def test_late_completion_cannot_overwrite_new_inputs(prepared, mutation):
    library, queue, project, _, _, docs = prepared
    enqueue(prepared)
    running = queue.claim()
    value = terminal(running)
    if mutation == "version":
        doc = library.get_document(docs[0][0]["id"])
        version = library.add_version(doc["id"], "预算已改。", expected_revision=doc["revision"])
        library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
    elif mutation == "disable":
        doc = library.get_document(docs[0][0]["id"])
        library.set_document_status(doc["id"], "disabled", expected_revision=doc["revision"])
    elif mutation == "memory":
        item = queue.sources.memory.create(scope="project", scope_id=project["id"], kind="preference", memory_key="response_length", value="brief")
        queue.sources.memory.update(item["id"], expected_revision=item["revision"], status="active")
    elif mutation == "project":
        queue.sources.outcomes.create(project["id"], kind="task", text="新待办", expected_project_revision=library.get_project(project["id"])["revision"])
    else:
        queue.cancel(running["id"])
    with pytest.raises((KnowledgeConflict, KnowledgeScopeError)):
        queue.finish(running, value)
    assert queue.get(running["id"])["result_json"] is None
    assert public_preparation_job(queue, queue.get(running["id"]))["evidence_status"] == ("valid" if mutation == "cancel" else "stale")


def test_restart_polling_no_paid_permission_started_failure_and_explicit_retry(prepared):
    library, queue, project, _, _, _ = prepared
    job = enqueue(prepared)
    reopened = PreparationQueue(library)
    assert reopened.list(project["id"])["items"] and reopened.claim() is None
    old = reopened.get(job["id"])
    with pytest.raises(KnowledgeConflict):
        reopened.retry(job["id"], expected_revision=old["revision"]+1, expected_input_hash=old["input_hash"], expected_project_revision=library.get_project(project["id"])["revision"])
    reopened.retry(job["id"], expected_revision=old["revision"], expected_input_hash=old["input_hash"], expected_project_revision=library.get_project(project["id"])["revision"])
    running = reopened.claim()
    reopened.mark_started(running)
    reopened.release(running, BackgroundReconcileDeferred())
    assert reopened.get(job["id"])["status"] == "failed" and reopened.claim() is None
    with pytest.raises(KnowledgeConflict):
        reopened.finish(running, terminal(running))
    with library._write():
        library._conn.execute("UPDATE project_preparation_jobs SET status='running',lease_until_ms=0 WHERE id=?", (job["id"],))
    reopened.recover_expired()
    assert reopened.get(job["id"])["error_code"] == "execution_interrupted"


@pytest.mark.parametrize("status", ["pending", "running", "succeeded"])
def test_deleted_source_physically_clears_snapshot_and_late_result(prepared, status):
    library, queue, _, _, _, docs = prepared
    job = enqueue(prepared)
    running = job if status == "pending" else queue.claim()
    result = terminal(running)
    if status == "succeeded":
        queue.finish(running, result)
    with library._write():
        library._conn.execute("DELETE FROM knowledge_spans WHERE version_id=?", (docs[0][1]["id"],))
        library._conn.execute("DELETE FROM knowledge_versions WHERE id=?", (docs[0][1]["id"],))
    with pytest.raises(KeyError):
        queue.get(job["id"])
    with pytest.raises(KeyError):
        queue.finish(running, result)
    assert library._conn.execute("SELECT COUNT(*) FROM preparation_source_versions").fetchone()[0] == 0


@pytest.mark.parametrize("status", ["pending", "running", "succeeded"])
def test_memory_only_source_deletion_clears_job_and_never_becomes_business_evidence(prepared, status):
    library, queue, project, _, _, docs = prepared
    ref = library.read_source(docs[0][1]["spans"][0]["id"], project_id=project["id"])
    memory = queue.sources.memory.create(scope="project", scope_id=project["id"], kind="term", memory_key="term", value="Aster", aliases=["Alpha"], source_refs=[ref], origin="ai")
    queue.sources.memory.update(memory["id"], expected_revision=memory["revision"], status="active")
    job = enqueue(prepared, document_ids=[docs[0][0]["id"]], goal="Review legal approval")
    payload = json.loads(job["source_payload_json"])
    assert payload["preparation"]["sources"] == [] and payload["user_memory"]["terms"][0]["canonical"] == "Aster"
    assert library._conn.execute("SELECT COUNT(*) FROM preparation_source_versions WHERE job_id=?", (job["id"],)).fetchone()[0] == 1
    running = job if status == "pending" else queue.claim()
    value = terminal(running, source=False)
    if status == "succeeded":
        queue.finish(running, value)
    with library._write():
        library._conn.execute("DELETE FROM knowledge_spans WHERE version_id=?", (docs[0][1]["id"],))
        library._conn.execute("DELETE FROM knowledge_versions WHERE id=?", (docs[0][1]["id"],))
    with pytest.raises(KeyError):
        queue.get(job["id"])
    with pytest.raises(KeyError):
        queue.finish(running, value)


def test_goal_only_api_safe_projection_pagination_and_revision_guard(prepared):
    library, _, project, _, _, _ = prepared
    app = FastAPI()
    mount_preparation_jobs_api(app, library)
    client = TestClient(app)
    base = "/v2/knowledge"
    for goal in ["确认范围", "明确验收"]:
        response = client.post(f"{base}/projects/{project['id']}/preparation-jobs", json={"document_ids": [], "goal": goal, "expected_project_revision": library.get_project(project["id"])["revision"]})
        assert response.status_code == 202, response.text
    page = client.get(f"{base}/preparation-jobs", params={"project_id": project["id"], "limit": 1}).json()
    assert page["has_more"] and page["next_cursor"]
    job = page["items"][0]
    assert job["document_ids"] == [] and job["result"] is None and not job["requires_resume"]
    assert not {"snapshot_json", "source_payload_json", "lease_owner", "api_key"}.intersection(job)
    assert set(("created_at_ms", "updated_at_ms", "completed_at_ms")).issubset(job)
    app.state.preparation_queue.eligible.clear()
    assert client.get(f"{base}/preparation-jobs/{job['id']}").json()["job"]["requires_resume"]
    assert client.post(f"{base}/preparation-jobs/{job['id']}/retry", json={"expected_revision": job["revision"]+1, "expected_input_hash": job["input_hash"], "expected_project_revision": job["base_project_revision"]}).status_code == 409
    assert client.post(f"{base}/preparation-jobs/{job['id']}/cancel").json()["job"]["status"] == "cancelled"
    assert all(row["attempts"] == 0 for row in app.state.preparation_queue.list(project["id"])["items"])


def test_runner_cancel_ack_and_no_automatic_repeat_after_paid_interruption(prepared):
    _, queue, _, _, _, _ = prepared
    job = enqueue(prepared)
    async def run():
        entered, ack = asyncio.Event(), asyncio.Event()
        async def execute(payload, claimed):
            queue.mark_started(claimed)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                ack.set()
        runner = PreparationRunner(queue, execute)
        await runner.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await runner.cancel(job["id"])
            assert ack.is_set() and queue.get(job["id"])["status"] == "cancelled" and queue.claim() is None
        finally:
            await runner.stop()
    asyncio.run(run())


def test_migration_22_to_23_preserves_all_prior_fingerprints(tmp_path):
    path = tmp_path / "upgrade.db"
    migrate_sqlite_schema(path, APPLICATION_SCHEMA_MIGRATIONS[:22], current_version=22, max_supported_version=22)
    with sqlite3.connect(path) as connection:
        fingerprints = connection.execute("SELECT version,fingerprint FROM meeting_copilot_schema_migrations ORDER BY version").fetchall()
    result = bootstrap_application_schema(path)
    assert result.applied_versions == tuple(range(23, APPLICATION_SCHEMA_VERSION + 1))
    assert result.final_version == APPLICATION_SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version,fingerprint FROM meeting_copilot_schema_migrations WHERE version<=22 ORDER BY version").fetchall() == fingerprints
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_actual_app_mounts_preparation_and_uses_independent_account_key_without_meeting(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("MEETING_COPILOT_DATA_DIR", str(tmp_path / "isolated-runtime"))
    from meeting_copilot_web_mvp import llm_service
    from meeting_copilot_web_mvp.app import create_app
    config = llm_service.LlmConfig(base_url="https://unused.invalid", api_key="fixture-never-sent", model="gpt-6-sol", is_mock=False)
    monkeypatch.setattr(llm_service.LlmConfig, "from_env", classmethod(lambda cls: config))
    app = create_app(data_dir=tmp_path, prewarm_funasr=False)
    client = TestClient(app)
    project = client.post("/v2/knowledge/projects", json={"name": "独立会前项目"}).json()["project"]
    response = client.post(f"/v2/knowledge/projects/{project['id']}/preparation-jobs", json={"goal": "确认交付范围", "document_ids": [], "expected_project_revision": project["revision"]})
    assert response.status_code == 202, response.text
    assert app.state.preparation_queue.claim()["id"] == response.json()["job"]["id"]
    observed = []
    async def callback(payload, job, config):
        observed.append(job["meeting_id"])
        return {"metrics": {"usage": {"total_tokens": 1}}}
    asyncio.run(app.state.run_preparation_with_admission(callback, {}, {"meeting_id": "preparation:"+project["id"]}))
    assert observed == ["preparation:"+project["id"]]
    assert app.state.preparation_queue.repository._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from pathlib import Path

import pytest

from meeting_copilot_web_mvp.document_import import DocumentExtraction, DocumentImportService
from meeting_copilot_web_mvp.knowledge_jobs import KnowledgeJobQueue, KnowledgeJobRunner, JOB_LEASE_MS
from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp import knowledge_jobs


@pytest.fixture
def services(tmp_path):
    repository = KnowledgeRepository(tmp_path / "library.db")
    importer = DocumentImportService(repository, tmp_path / "assets")
    queue = KnowledgeJobQueue(repository)
    project = repository.create_project("客户 A")
    yield repository, importer, queue, project
    repository.close()


def test_claim_deduplication_concurrent_lease_recovery_and_stale_result(services):
    repository, importer, queue, project = services
    document = importer.stage_document(project["id"], "材料.txt", "预算三万".encode())
    job = queue.enqueue(document["id"], now_ms=100)
    assert queue.enqueue(document["id"])["id"] == job["id"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        claims = list(executor.map(lambda _: queue.claim(now_ms=101), range(4)))
    first = next(claim for claim in claims if claim is not None)
    assert len([claim for claim in claims if claim is not None]) == 1
    assert queue.recover_expired(now_ms=101 + JOB_LEASE_MS - 1) == 0
    assert queue.recover_expired(now_ms=101 + JOB_LEASE_MS) == 1
    current = queue.claim(now_ms=102 + JOB_LEASE_MS)
    assert current["attempt"] == 2 and current["lease_token"] != first["lease_token"]
    assert queue.finish(first, DocumentExtraction("extracted", "旧结果", ({"text": "旧结果", "locator": {"type": "converted_text"}},))) is False
    assert repository.list_versions(document["id"])["items"] == []
    assert queue.finish(current, DocumentExtraction("extracted", "预算三万", ({"text": "预算三万", "locator": {"type": "converted_text"}},)))
    assert queue.get(job["id"])["status"] == "succeeded"
    assert repository.get_document(document["id"])["status"] == "draft"


def test_manual_revision_wins_cancel_and_retry(services):
    repository, importer, queue, project = services
    document = importer.stage_document(project["id"], "材料.txt", b"extracted")
    job = queue.enqueue(document["id"])
    running = queue.claim()
    corrected = repository.add_version(document["id"], "人工校对内容")
    assert queue.finish(running, DocumentExtraction("extracted", "old", ({"text": "old", "locator": {}},)))
    assert queue.get(job["id"])["error_code"] == "input_superseded"
    assert repository.get_version(document["id"])["id"] == corrected["id"]
    assert len(repository.list_versions(document["id"])["items"]) == 1
    assert queue.retry(job["id"])["status"] == "queued"
    assert queue.cancel(job["id"])["status"] == "cancelled"
    assert queue.cancel(job["id"])["status"] == "cancelled"
    assert queue.claim() is None
    assert queue.retry(job["id"])["status"] == "queued"


def test_restart_recovers_original_without_job_and_preserves_confirmed_version(services):
    repository, importer, queue, project = services
    document = importer.stage_document(project["id"], "材料.txt", "文稿".encode())
    assert queue.recover_orphans() == 1
    assert queue.recover_orphans() == 0
    job = queue.claim()
    assert job["document_id"] == document["id"]
    queue.release(job)
    assert queue.get(job["id"])["status"] == "queued"


def test_real_windows_subprocess_isolated_conversion_and_idempotent_physical_cleanup(services):
    repository, importer, queue, project = services
    document = importer.stage_document(project["id"], "中文文稿.md", "# 预算\n\n金额3万".encode())
    original = importer.original_path(document["id"])
    job = queue.enqueue(document["id"])
    running = queue.claim()
    runner = KnowledgeJobRunner(queue, importer)
    result = runner._convert(running)
    assert result.status == "extracted", result.error_code
    assert "金额3万" in result.content
    assert queue.finish(running, result)
    assert list(runner.results_dir.iterdir()) == []
    version_id = queue.get(job["id"])["result_version_id"]
    document = repository.confirm_version(document["id"], version_id, expected_revision=document["revision"] + 1)
    deleted, cleanup = queue.delete_document(document["id"], expected_revision=document["revision"])
    assert deleted["status"] == "deleted" and repository.search("预算", project_id=project["id"]) == []
    running = queue.claim()
    assert running["id"] == cleanup["id"]
    result = runner._cleanup(running)
    assert result.status == "extracted" and not original.exists()
    assert repository.list_versions(document["id"])["items"] == []
    assert runner._cleanup(running).status == "extracted"
    assert queue.finish(running, result)


@pytest.mark.parametrize("parent_bytecode_setting", [None, "0"])
def test_real_extract_child_forces_readonly_bytecode_and_no_user_site_without_forwarding_secrets(
    services, monkeypatch, parent_bytecode_setting,
):
    repository, importer, queue, project = services
    for name in ("PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE"):
        if parent_bytecode_setting is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, parent_bytecode_setting)
    secret_names = ("LLM_GATEWAY_API_KEY", "OPENAI_API_KEY", "AUTHORIZATION", "MEETING_COPILOT_LOCAL_API_TOKEN")
    for name in secret_names:
        monkeypatch.setenv(name, "synthetic-parent-secret-marker")
    monkeypatch.setenv("PYTHONHOME", "synthetic-untrusted-python-home")
    monkeypatch.setenv("PYTHONPATH", str(Path(knowledge_jobs.__file__).resolve().parent.parent))
    calls = []
    original_popen = knowledge_jobs.subprocess.Popen
    def spy_popen(command, *args, **kwargs):
        calls.append((list(command), dict(kwargs["env"])))
        return original_popen(command, *args, **kwargs)
    monkeypatch.setattr(knowledge_jobs.subprocess, "Popen", spy_popen)
    document = importer.stage_document(project["id"], "合成隔离资料.md", "# 合成资料\n真实转换预算3万元。".encode())
    job = queue.enqueue(document["id"])
    running = queue.claim()
    runner = KnowledgeJobRunner(queue, importer)
    result = runner._convert(running)
    assert result.status == "extracted", result.error_code
    assert "真实转换预算3万元。" in result.content
    assert queue.finish(running, result) and queue.get(job["id"])["status"] == "succeeded"
    assert repository.get_version(document["id"])["content"] == "# 合成资料\n真实转换预算3万元。"
    assert len(calls) == 1
    command, environment = calls[0]
    assert command[1:4] == ["-B", "-m", "meeting_copilot_web_mvp.knowledge_jobs"]
    assert "-S" not in command  # Source virtualenv dependencies need normal site initialization.
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHON_DOTENV_DISABLED"] == "1"
    assert "PYTHONHOME" not in environment and not any(name in environment for name in secret_names)
    assert not list(runner.results_dir.iterdir())


def test_conversion_timeout_and_stop_releases_job(services):
    _, importer, queue, project = services
    document = importer.stage_document(project["id"], "材料.txt", b"simple")
    job = queue.enqueue(document["id"])
    running = queue.claim()
    runner = KnowledgeJobRunner(queue, importer, timeout_seconds=0.05)
    result = runner._convert(running)
    assert result.status == "failed" and result.error_code == "conversion_timeout"
    queue.finish(running, result)
    assert queue.get(job["id"])["status"] == "failed"
    assert not list(runner.results_dir.iterdir())


def test_background_runner_completes_without_blocking_loop_and_stops(services):
    _, importer, queue, project = services
    document = importer.stage_document(project["id"], "材料.txt", b"hello")
    job = queue.enqueue(document["id"])
    runner = KnowledgeJobRunner(queue, importer)

    async def exercise():
        await runner.start()
        ticked = 0
        deadline = time.monotonic() + 5
        while queue.get(job["id"])["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
            ticked += 1
        await runner.stop()
        assert ticked > 1
    asyncio.run(exercise())
    assert runner._task is None and queue.get(job["id"])["status"] == "succeeded"


def test_new_process_connection_recovers_expired_persistent_import(tmp_path):
    path, data_dir = tmp_path / "persistent.db", tmp_path / "private"
    old = KnowledgeRepository(path)
    project = old.create_project("重启项目")
    importer = DocumentImportService(old, data_dir)
    document = importer.stage_document(project["id"], "资料.txt", "重启后的资料".encode())
    queue = KnowledgeJobQueue(old)
    job = queue.enqueue(document["id"], now_ms=1)
    queue.claim(now_ms=1)  # The former worker died holding a lease which is now expired.
    old.close()
    current = KnowledgeRepository(path)
    queue = KnowledgeJobQueue(current)
    runner = KnowledgeJobRunner(queue, DocumentImportService(current, data_dir))

    async def recover():
        await runner.start()
        deadline = time.monotonic() + 5
        while queue.get(job["id"])["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await runner.stop()
    try:
        asyncio.run(recover())
        assert queue.get(job["id"])["status"] == "succeeded"
        assert queue.get(job["id"])["attempt"] == 2
        assert current.get_version(document["id"])["content"] == "重启后的资料"
    finally:
        current.close()

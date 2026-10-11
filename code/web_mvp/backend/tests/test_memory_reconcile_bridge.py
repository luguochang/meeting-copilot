from __future__ import annotations

import asyncio
from io import StringIO
import json
from pathlib import Path
import threading

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeConflict, KnowledgeRepository, KnowledgeScopeError
from meeting_copilot_web_mvp.memory_jobs import MemoryReconcilePiExecutor, MemoryReconcileQueue, MemoryReconcileRegistry
from meeting_copilot_web_mvp.pi_coach_runtime import PROTOCOL, PiCoachRuntimeError, PiCoachSidecar, default_pi_bridge_command


@pytest.mark.parametrize("mutation", ["none", "late_source", "cross_project"])
def test_actual_windows_node_sdk_jsonl_host_capture_persists_candidates_without_network(tmp_path, mutation):
    command = default_pi_bridge_command()
    bridge = Path(__file__).resolve().parents[4] / "code/agent_runtime/pi_coach_bridge"
    ai = bridge / "node_modules/@earendil-works/pi-ai/dist/index.js"
    if not command or not ai.is_file():
        pytest.skip("local Node/Pi SDK is not installed")
    script = tmp_path / "memory-sdk-fake.mjs"
    script.write_text(
        f"import {{runStdio}} from {json.dumps((bridge/'src/bridge.mjs').as_uri())};\n"
        f"import {{PiCoachRuntime}} from {json.dumps((bridge/'src/runtime.mjs').as_uri())};\n"
        f"import {{createModels,fauxProvider,fauxAssistantMessage,fauxToolCall}} from {json.dumps(ai.as_uri())};\n"
        "const fake=fauxProvider({provider:'memory-fixture-no-network'});const models=createModels();models.setProvider(fake.provider);\n"
        "const runtime=new PiCoachRuntime({backendFactory:()=>({identity:'faux',model:fake.getModel(),streamFn:models.streamSimple.bind(models)})});\n"
        "await runStdio({evaluate:(request,hooks)=>{\n"
        "const input=request.context.memory_reconciliation;const clue_indices=input.clues.map((_,index)=>index);\n"
        "fake.setResponses(Array.from({length:2},()=>fauxAssistantMessage(fauxToolCall('propose_memory_update',{input_hash:input.input_hash,clue_indices}),{stopReason:'toolUse'})));return runtime.evaluate(request,hooks);}});\n",
        encoding="utf-8")
    library = KnowledgeRepository(tmp_path / "memory.db")
    sidecar = None
    try:
        project = library.create_project("合成项目")
        queue = MemoryReconcileQueue(library)
        library._memory_reconcile_queue = queue
        doc = library.create_document(project["id"], "术语声明")
        version = library.add_version(doc["id"], '术语纠正：将“托克吹斯”改为“Talktrace”。\n我希望回答简短。', expected_revision=1, origin="user")
        library.confirm_version(doc["id"], version["id"], expected_revision=library.get_document(doc["id"])["revision"])
        job = queue.claim()
        class Registry(MemoryReconcileRegistry):
            revised = False
            def validate_memory_update(self, terminal):
                if mutation == "late_source" and not self.revised:
                    self.revised = True
                    updated = library.add_version(doc["id"], "我希望保持正常回答长度。", origin="user", expected_revision=library.get_document(doc["id"])["revision"])
                    library.confirm_version(doc["id"], updated["id"], expected_revision=library.get_document(doc["id"])["revision"])
                return super().validate_memory_update(terminal)
        registry = Registry(queue, job)
        sidecar = PiCoachSidecar(command=[command[0], str(script)], timeout_seconds=8, evidence_registry_factory=lambda _: registry)
        payload = queue.payload(job)
        payload["provider"] = {"base_url": "https://unused.invalid", "api_key": "fixture-never-sent", "model": "faux", "api_style": "chat_completions", "timeout_ms": 8000}
        if mutation == "cross_project":
            payload["context"]["knowledge_context"]["project_id"] = library.create_project("另一个合成项目")["id"]
        queue.mark_started(job)
        if mutation != "none":
            with pytest.raises(PiCoachRuntimeError) as caught:
                sidecar._evaluate_sync(payload)
            assert caught.value.code == ("invalid_request" if mutation == "cross_project" else "missing_terminal_action")
            assert library._conn.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0] == 0
            if mutation == "late_source":
                old = json.loads(job["source_payload_json"])["memory_reconciliation"]
                with pytest.raises((KnowledgeConflict, KnowledgeScopeError)):
                    queue.finish(job, {"input_hash": old["input_hash"], "candidates": old["clues"]})
            return
        result = sidecar._evaluate_sync(payload)
        assert result["host_memory_update"] == result["memory_update"]
        assert set(result["host_memory_update"]) == {"input_hash", "candidates"}
        assert all(candidate["source_refs"][0]["version_id"] == version["id"] for candidate in result["host_memory_update"]["candidates"])
        assert result["metrics"]["turns"] == 1 and result["metrics"]["tool_calls"] == 1
        assert result["metrics"]["available_tool_names"] == ["propose_memory_update"]
        ids = queue.finish(job, result["host_memory_update"])
        assert len(ids) == 2 and queue.get(job["id"])["status"] == "succeeded"
        assert all(item["origin"] == "ai" and item["status"] == "candidate" for item in queue.memory.list(scope="project", scope_id=project["id"])["items"])
        assert library._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    finally:
        if sidecar:
            sidecar.close()
        library.close()


class Process:
    def __init__(self):
        self.stdin, self.alive = StringIO(), True
    def poll(self):
        return None if self.alive else 0
    def terminate(self):
        self.alive = False
    def wait(self, timeout=None):
        self.alive = False


class Registry:
    def validate_memory_update(self, terminal):
        return dict(terminal)


@pytest.mark.parametrize("mode,forged", [("memory_reconcile", True), ("realtime_coach", False), ("project_reconcile", False)])
def test_memory_terminal_requires_capture_and_is_forbidden_in_other_modes(mode, forged):
    sidecar = PiCoachSidecar(command=["unused-fake"], evidence_registry_factory=lambda _: Registry())
    sidecar._process, sidecar._ready = Process(), threading.Event()
    sidecar._ready.set()
    terminal = {"input_hash": "a" * 64, "candidates": []}
    sidecar._responses.put_nowait({"protocol": PROTOCOL, "request_id": "r", **(
        {"ok": True, "action": "memory_update", "memory_update": terminal, "host_memory_update": terminal} if forged else
        {"event": "host_tool_request", "call_id": "evidence-1", "tool": "propose_memory_update", "arguments": terminal})})
    try:
        with pytest.raises(PiCoachRuntimeError) as caught:
            sidecar._evaluate_sync({"request_id": "r", "context": {"task_mode": mode}})
        assert caught.value.code == "pi_protocol_error"
    finally:
        sidecar.close()


def test_memory_executor_cancel_waits_for_actual_local_close_ack(tmp_path):
    async def run():
        library = KnowledgeRepository(tmp_path / "ack.db")
        try:
            project = library.create_project("项目")
            queue = MemoryReconcileQueue(library)
            doc = library.create_document(project["id"], "偏好")
            version = library.add_version(doc["id"], "我希望回答简短。", origin="user", expected_revision=1)
            library.confirm_version(doc["id"], version["id"], expected_revision=2)
            queue.enqueue(doc["id"], expected_version_id=version["id"])
            job = queue.claim()
            executor = MemoryReconcilePiExecutor(queue)
            entered, close_started, ack = asyncio.Event(), threading.Event(), threading.Event()
            class FakeRuntime:
                available = True
                async def evaluate(self, payload):
                    entered.set()
                    await asyncio.Future()
                def close(self):
                    close_started.set()
                    assert ack.wait(2)
            executor.runtime = FakeRuntime()
            class Config:
                base_url, api_key, model, api_style, timeout_seconds = "https://unused.invalid", "unused", "fake", "chat_completions", 25
            task = asyncio.create_task(executor(queue.payload(job), job, Config()))
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            assert await asyncio.to_thread(close_started.wait, 1)
            assert not task.done()
            ack.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            library.close()
    asyncio.run(run())

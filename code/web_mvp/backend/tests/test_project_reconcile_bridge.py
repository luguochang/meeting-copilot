from __future__ import annotations

import json
import asyncio
from io import StringIO
from pathlib import Path
import threading
import subprocess

import pytest

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.pi_coach_runtime import PROTOCOL, PiCoachRuntimeError, PiCoachSidecar, default_pi_bridge_command
from meeting_copilot_web_mvp.project_outcomes import ProjectOutcomes
from meeting_copilot_web_mvp.project_reconcile_jobs import ProjectReconcilePiExecutor, ProjectReconcileQueue, ProjectReconcileRegistry
from meeting_copilot_web_mvp.v2_persistence import V2Persistence


def test_real_node_pi_sdk_jsonl_host_terminal_produces_only_pending_proposal(tmp_path):
    command = default_pi_bridge_command()
    root = Path(__file__).resolve().parents[4]
    bridge_dir = root / "code/agent_runtime/pi_coach_bridge"
    ai = bridge_dir / "node_modules/@earendil-works/pi-ai/dist/index.js"
    if not command or not ai.is_file():
        pytest.skip("local Node/Pi dependencies are not installed")
    script = tmp_path / "faux-reconcile.mjs"
    script.write_text(
        f"import {{runStdio}} from {json.dumps((bridge_dir / 'src/bridge.mjs').as_uri())};\n"
        f"import {{PiCoachRuntime}} from {json.dumps((bridge_dir / 'src/runtime.mjs').as_uri())};\n"
        f"import {{createModels,fauxProvider,fauxAssistantMessage,fauxToolCall}} from {json.dumps(ai.as_uri())};\n"
        "const faux=fauxProvider({provider:'local-reconcile-fixture'});\n"
        "const models=createModels();models.setProvider(faux.provider);\n"
        "const runtime=new PiCoachRuntime({backendFactory:()=>({identity:'local-faux',model:faux.getModel(),streamFn:models.streamSimple.bind(models)})});\n"
        "await runStdio({evaluate:(request,hooks)=>{\n"
        " const ref={...request.context.reconciliation_evidence[0],quote:'张三将在下周三提交方案。'};\n"
        " const changes={base_project_revision:request.context.knowledge_context.project_revision,changes:[{action:'create',type:'task',text:'提交方案',status:'active',owner:'张三',due:'下周三',source_refs:[ref]}]};\n"
        " faux.setResponses([fauxAssistantMessage(fauxToolCall('read_project_state',{}),{stopReason:'toolUse'}),fauxAssistantMessage(fauxToolCall('propose_project_changes',changes),{stopReason:'toolUse'})]);\n"
        " return runtime.evaluate(request,hooks);}});\n", encoding="utf-8")
    persistence = V2Persistence(tmp_path / "reconcile.db")
    library = None
    sidecar = None
    try:
        with persistence._write_transaction():
            persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','ended',100,100)")
        persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0,
            content={"markdown": "张三将在下周三提交方案。"}, now_ms=200)
        library = KnowledgeRepository(persistence.database_path)
        project = library.create_project("项目")
        library.bind_meeting_context("meeting", project["id"])
        outcomes = ProjectOutcomes(library)
        jobs = ProjectReconcileQueue(library, outcomes)
        job = jobs.enqueue("meeting", expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=1)
        claimed = jobs.claim()
        registry = ProjectReconcileRegistry(jobs, claimed)
        sidecar = PiCoachSidecar(command=[command[0], str(script)], timeout_seconds=8, evidence_registry_factory=lambda meeting_id: registry)
        payload = jobs.payload(claimed)
        payload["provider"] = {"base_url": "https://example.test", "api_key": "local-faux-no-network", "model": "faux", "api_style": "chat_completions", "timeout_ms": 8000}
        jobs.mark_started(claimed)
        result = sidecar._evaluate_sync(payload)
        assert result["action"] == "project_changes"
        assert result["host_project_changes"] == result["project_changes"]
        assert result["metrics"]["turns"] == 2 and result["metrics"]["tool_calls"] == 2
        assert outcomes.proposals(project["id"])["items"] == []
        proposal = jobs.finish(claimed, result["host_project_changes"])
        assert proposal["status"] == "pending" and jobs.get(job["id"])["status"] == "succeeded"
        assert outcomes.list(project["id"])["items"] == []
    except PiCoachRuntimeError as exc:
        pytest.fail(f"local fixture Pi protocol failed: {exc.code}")
    finally:
        if sidecar is not None:
            sidecar.close()
        if library is not None:
            library.close()
        persistence.close()


class LocalProcess:
    def __init__(self):
        self.stdin = StringIO()
        self.alive = True
    def poll(self):
        return None if self.alive else 0
    def terminate(self):
        self.alive = False
    def wait(self, timeout=None):
        self.alive = False
    def kill(self):
        self.alive = False


class LocalRegistry:
    def read_project_state(self):
        return []
    def validate_project_changes(self, terminal):
        return dict(terminal)


def fake_sidecar():
    sidecar = PiCoachSidecar(command=["no-network-fixture"], evidence_registry_factory=lambda meeting_id: LocalRegistry())
    sidecar._process = LocalProcess()
    sidecar._ready.set()
    return sidecar


def test_background_result_cannot_forge_host_terminal_capture():
    sidecar = fake_sidecar()
    terminal = {"base_project_revision": 1, "changes": []}
    sidecar._responses.put_nowait({"protocol": PROTOCOL, "request_id": "request", "ok": True, "action": "project_changes",
        "project_changes": terminal, "host_project_changes": terminal})
    try:
        with pytest.raises(PiCoachRuntimeError, match="captured"):
            sidecar._evaluate_sync({"request_id": "request", "context": {"task_mode": "project_reconcile"}})
    finally:
        sidecar.close()


def test_background_mode_accepts_exactly_six_host_calls_then_matching_terminal():
    sidecar = fake_sidecar()
    terminal = {"base_project_revision": 1, "changes": []}
    for index in range(1, 7):
        sidecar._responses.put_nowait({"protocol": PROTOCOL, "event": "host_tool_request", "request_id": "request", "call_id": f"evidence-{index}",
            "tool": "read_project_state" if index < 6 else "propose_project_changes", "arguments": {} if index < 6 else terminal})
    sidecar._responses.put_nowait({"protocol": PROTOCOL, "request_id": "request", "ok": True, "action": "project_changes", "project_changes": terminal})
    try:
        result = sidecar._evaluate_sync({"request_id": "request", "context": {"task_mode": "project_reconcile"}})
        assert result["host_project_changes"] == terminal
    finally:
        sidecar.close()


def test_realtime_mode_never_accepts_the_background_terminal_tool():
    sidecar = fake_sidecar()
    sidecar._responses.put_nowait({"protocol": PROTOCOL, "event": "host_tool_request", "request_id": "request", "call_id": "evidence-1",
        "tool": "propose_project_changes", "arguments": {"base_project_revision": 1, "changes": []}})
    try:
        with pytest.raises(PiCoachRuntimeError, match="Invalid host tool"):
            sidecar._evaluate_sync({"request_id": "request", "context": {}})
    finally:
        sidecar.close()


def test_background_cancellation_waits_for_local_sidecar_close_ack(tmp_path):
    async def run():
        persistence = V2Persistence(tmp_path / "ack.db")
        library = None
        try:
            with persistence._write_transaction():
                persistence._conn.execute("INSERT INTO meetings(id,state,created_at_ms,updated_at_ms) VALUES('meeting','ended',100,100)")
            persistence.save_user_final_document(meeting_id="meeting", document_kind="minutes", expected_revision=0, content={"markdown": "真实人工稿"}, now_ms=200)
            library = KnowledgeRepository(persistence.database_path)
            project = library.create_project("项目")
            library.bind_meeting_context("meeting", project["id"])
            queue = ProjectReconcileQueue(library, ProjectOutcomes(library))
            queue.enqueue("meeting", expected_project_revision=library.get_project(project["id"])["revision"], expected_source_version=1)
            claimed = queue.claim()
            executor = ProjectReconcilePiExecutor(queue)
            entered = asyncio.Event()
            close_started, ack = threading.Event(), threading.Event()
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
                base_url, api_key, model, api_style, timeout_seconds = "https://example.test", "fake-unused", "faux", "chat_completions", 10
            task = asyncio.create_task(executor(queue.payload(claimed), claimed, Config()))
            await asyncio.wait_for(entered.wait(), timeout=2)
            task.cancel()
            assert await asyncio.to_thread(close_started.wait, 1)
            assert not task.done()
            ack.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if library is not None:
                library.close()
            persistence.close()
    asyncio.run(run())


def test_killed_sidecar_requires_exit_ack_before_close_returns():
    class SlowExit(LocalProcess):
        def __init__(self):
            super().__init__()
            self.waits = 0
            self.killed = False
        def terminate(self):
            pass
        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("fixture", timeout)
            assert self.killed
            self.alive = False
        def kill(self):
            self.killed = True
    sidecar = fake_sidecar()
    process = SlowExit()
    sidecar._process = process
    sidecar.close()
    assert process.waits == 2 and process.poll() == 0

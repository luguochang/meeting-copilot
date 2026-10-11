from __future__ import annotations

import json
from pathlib import Path

from meeting_copilot_web_mvp.knowledge_repository import KnowledgeRepository
from meeting_copilot_web_mvp.pi_coach_runtime import PiCoachSidecar, default_pi_bridge_command
from meeting_copilot_web_mvp.preparation_jobs import PreparationQueue, PreparationRegistry


def test_real_windows_sdk_jsonl_capture_persists_preparation_without_network(tmp_path):
    command = default_pi_bridge_command()
    bridge = Path(__file__).resolve().parents[4] / "code/agent_runtime/pi_coach_bridge"
    ai = bridge / "node_modules/@earendil-works/pi-ai/dist/index.js"
    assert command and ai.is_file(), "the acceptance workspace must have the actual Node/Pi SDK installed"
    script = tmp_path / "preparation-fake.mjs"
    script.write_text(
        f"import {{runStdio}} from {json.dumps((bridge/'src/bridge.mjs').as_uri())};\n"
        f"import {{PiCoachRuntime}} from {json.dumps((bridge/'src/runtime.mjs').as_uri())};\n"
        f"import {{createModels,fauxProvider,fauxAssistantMessage,fauxToolCall}} from {json.dumps(ai.as_uri())};\n"
        "const fake=fauxProvider({provider:'preparation-fixture-no-network'});const models=createModels();models.setProvider(fake.provider);\n"
        "const runtime=new PiCoachRuntime({backendFactory:()=>({identity:'faux',model:fake.getModel(),streamFn:models.streamSimple.bind(models)})});\n"
        "await runStdio({evaluate:(request,hooks)=>{const input=request.context.preparation;const source=input.sources.find(ref=>ref.source_type==='document');\n"
        "const value={input_hash:input.input_hash,agenda:[{text:'确认预算和压测条件。',basis:'user_goal',source_refs:[]}],questions:[{text:'压测需要满足什么条件？',basis:'user_goal',source_refs:[]}],conditions:[{text:'需要核对预算120元的适用范围。',basis:'source',source_refs:[{source_type:source.source_type,source_id:source.source_id,quote:'预算120元'}]}]};\n"
        "fake.setResponses([fauxAssistantMessage(fauxToolCall('read_project_state',{}),{stopReason:'toolUse'}),fauxAssistantMessage(fauxToolCall('search_project_documents',{query:'预算'}),{stopReason:'toolUse'}),fauxAssistantMessage(fauxToolCall('submit_preparation_brief',value),{stopReason:'toolUse'})]);return runtime.evaluate(request,hooks);}});\n",
        encoding="utf-8")
    library = KnowledgeRepository(tmp_path / "preparation.db")
    sidecar = None
    try:
        project = library.create_project("合成会前项目")
        doc = library.create_document(project["id"], "发布条件")
        version = library.add_version(doc["id"], "预算120元；需要压测通过。", origin="user", expected_revision=1)
        library.confirm_version(doc["id"], version["id"], expected_revision=2)
        queue = PreparationQueue(library)
        queue.enqueue(project["id"], goal="预算 压测", expected_project_revision=library.get_project(project["id"])["revision"])
        job = queue.claim()
        registry = PreparationRegistry(queue, job)
        sidecar = PiCoachSidecar(command=[command[0], str(script)], timeout_seconds=8, evidence_registry_factory=lambda _: registry)
        payload = queue.payload(job)
        payload["provider"] = {"base_url": "https://unused.invalid", "api_key": "fixture-never-sent", "model": "faux", "api_style": "chat_completions", "timeout_ms": 8000}
        queue.mark_started(job)
        result = sidecar._evaluate_sync(payload)
        assert result["host_preparation_brief"] == result["preparation_brief"]
        assert result["metrics"]["turns"] == 3 and result["metrics"]["tool_calls"] == 3
        assert result["metrics"]["available_tool_names"] == ["submit_preparation_brief", "read_project_state", "search_project_documents"]
        output = queue.finish(job, result["host_preparation_brief"])
        ref = output["conditions"][0]["source_refs"][0]
        assert ref["version_id"] == version["id"] and ref["quote"] == "预算120元"
        assert queue.get(job["id"])["status"] == "succeeded" and queue.get(job["id"])["attempts"] == 1
        assert library._conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
        assert library._conn.execute("SELECT COUNT(*) FROM project_change_proposals").fetchone()[0] == 0
        assert library._conn.execute("SELECT COUNT(*) FROM user_memories").fetchone()[0] == 0
    finally:
        if sidecar:
            sidecar.close()
        library.close()

import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../src/runtime.mjs";

const doc = (index = 1) => ({ source_type: "document", source_id: `span${index}`, document_id: `doc${index}`,
  project_id: "project1", title: "发布条件", version_id: `version${index}`, version: 1, content_hash: "b".repeat(64),
  locator: { type: "converted_text" }, quote: "预算120元；需要压测通过。", context_revision: 0 });
const item = (text, basis = "user_goal", refs = []) => ({ text, basis, source_refs: refs });
const terminal = (refs = []) => ({ input_hash: "a".repeat(64), agenda: [item("确认预算范围。")], questions: [item("压测需要满足什么条件？")],
  conditions: refs.length ? [item("核对预算120元。", "source", refs)] : [] });
const call = (name, params) => fauxAssistantMessage(fauxToolCall(name, params), { stopReason: "toolUse" });
function harness(responses, sources = [doc()]) {
  const fake = fauxProvider({ provider: `prep-test-${Math.random()}` });
  const models = createModels(); models.setProvider(fake.provider); fake.setResponses(responses);
  return { runtime: new PiCoachRuntime({ backendFactory: () => ({ identity: "prep-fake", model: fake.getModel(), streamFn: models.streamSimple.bind(models), decisionTimeoutMs: 45000 }) }),
    request: { request_id: "prepare1", session_id: "preparation:project1", provider: {}, context: { task_mode: "meeting_preparation",
      role_contract: "你是用户的私人中文会议副驾。", meeting_goal: "确认预算范围", participant_role: "项目负责人", focus_points: ["压测条件"],
      document_evidence: sources.filter((source) => source.source_type === "document"), knowledge_context: { project_id: "project1", project_revision: 3 },
      preparation: { input_hash: "a".repeat(64), goal: "确认预算范围", role: "项目负责人", focus: ["压测条件"], scene: "project", sources, project_state: sources.filter((source) => source.source_type === "project_state"), context_truncated: false } } } };
}

test("goal-only preparation uses only its terminal and requires an agenda and question", async () => {
  const value = terminal(); const { runtime, request } = harness([call("submit_preparation_brief", value)], []);
  const result = await runtime.evaluate(request, { submitPreparationBrief: async (brief) => brief });
  assert.equal(result.action, "preparation_brief");
  assert.deepEqual(result.preparation_brief, value);
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.available_tool_names, ["submit_preparation_brief"]);
});

test("three real SDK turns can read state, search and submit minimal references with host metadata", async () => {
  const state = { source_type: "project_state", source_id: "state1", project_id: "project1", revision: 2, type: "task", status: "active", owner: null, due: null, quote: "核对压测条件", source_refs: [] };
  const minimal = { source_type: "document", source_id: "span1", quote: "预算120元" };
  const { runtime, request } = harness([call("read_project_state", {}), call("search_project_documents", { query: "预算" }), call("submit_preparation_brief", terminal([minimal]))], [doc(), state]);
  const result = await runtime.evaluate(request, { readProjectState: async () => [state], searchDocuments: async () => [doc()], submitPreparationBrief: async (brief) => brief });
  assert.equal(result.metrics.turns, 3);
  assert.equal(result.metrics.tool_calls, 3);
  assert.deepEqual(result.preparation_brief.conditions[0].source_refs[0], { ...doc(), quote: "预算120元" });
  assert.deepEqual(result.metrics.available_tool_names, ["submit_preparation_brief", "read_project_state", "search_project_documents"]);
});

test("two distinct searches preserve the first result beyond the realtime six-source slice", async () => {
  const minimal = { source_type: "document", source_id: "span1", quote: "预算120元" };
  const { runtime, request } = harness([call("search_project_documents", { query: "第一次" }), call("search_project_documents", { query: "第二次" }), call("submit_preparation_brief", terminal([minimal]))], []);
  let searches = 0;
  const result = await runtime.evaluate(request, { searchDocuments: async () => { searches += 1; return Array.from({ length: 6 }, (_, i) => doc(searches === 1 ? i + 1 : i + 7)); }, submitPreparationBrief: async (brief) => brief });
  assert.equal(result.metrics.turns, 3);
  assert.equal(result.preparation_brief.conditions[0].source_refs[0].source_id, "span1");
});

for (const mutation of ["snapshot", "source", "metadata", "goal_condition", "missing_question", "refs_budget"]) {
  test(`preparation rejects ${mutation} before accepting its terminal`, async () => {
    const value = terminal([{ source_type: "document", source_id: "span1", quote: "预算120元" }]);
    if (mutation === "snapshot") value.input_hash = "c".repeat(64);
    if (mutation === "source") value.conditions[0].source_refs[0].source_id = "foreign";
    if (mutation === "metadata") value.conditions[0].source_refs[0].version_id = "fabricated";
    if (mutation === "goal_condition") value.conditions = [item("已批准上线。")];
    if (mutation === "missing_question") value.questions = [];
    if (mutation === "refs_budget") value.conditions[0].source_refs = Array.from({ length: 13 }, () => ({ source_type: "document", source_id: "span1", quote: "预算120元" }));
    const { runtime, request } = harness([call("submit_preparation_brief", value)]);
    let accepted = false;
    await assert.rejects(runtime.evaluate(request, { submitPreparationBrief: async (brief) => { accepted = true; return brief; } }));
    assert.equal(accepted, false);
  });
}

test("preparation stops after three model turns without a terminal", async () => {
  const { runtime, request } = harness(Array.from({ length: 4 }, () => call("search_project_documents", { query: "预算" })));
  await assert.rejects(runtime.evaluate(request, { searchDocuments: async () => [doc()], submitPreparationBrief: async (brief) => brief }), (error) => error.metrics?.turns === 3);
});

test("five tool executions are a shared ceiling even when one SDK turn proposes six lookups", async () => {
  const message = call("search_project_documents", { query: "预算" });
  message.content = Array.from({ length: 6 }, () => fauxToolCall("search_project_documents", { query: "预算" }));
  const { runtime, request } = harness([message]);
  let searches = 0;
  await assert.rejects(runtime.evaluate(request, { searchDocuments: async () => { searches += 1; return [doc()]; }, submitPreparationBrief: async (brief) => brief }), (error) => error.metrics?.tool_calls === 5);
  assert.equal(searches, 5);
});

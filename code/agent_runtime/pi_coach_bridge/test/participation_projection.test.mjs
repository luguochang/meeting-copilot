import assert from "node:assert/strict";
import test from "node:test";
import { modelDiscussionProjection, participationPrompt } from "../src/participation.mjs";
import { createParticipationQuoteCatalog, hydrateParticipationQuotes } from "../src/participation_quotes.mjs";

function point(extra = {}) {
  return { id: "candidate-upload", text: "接口接收不等于所有记录处理完成。", source_refs: ["meeting-source"],
    entity_type: "decision", kind: "decision_candidate", status: "candidate", is_current_solution: true,
    evidence: [{ segment_id: "meeting-source", revision: 2, evidence_hash: "a".repeat(64) }], ...extra };
}

function discussion() {
  return { topic_id: "discussion-import", current_problem: "大文件导入", current_solution: "候选方案：后台处理后另行确认完成。",
    alternatives: ["用户重选文件后明确继续"], constraints: [point()], open_points: [point({ id: "file-permission",
      text: "刷新后原文件权限需要核对。", entity_type: "open_question", kind: "unknown_condition", status: "open", is_current_solution: false })],
    responded_points: [point({ id: "receive-only", text: "已确认接收与处理完成分开展示。", status: "resolved",
      kind: "decision_fact", is_current_solution: false })], inferred: false, phase: "discussing", analysis_basis_revision: 3,
    last_checked_seq: 4, last_semantic_seq: 4, opportunity_formed_at_ms: 1000,
    proposals: [{ decision_id: "previous-card", say_this: "建议先核对文件身份。", kind: "ai_proposal", source_refs: ["meeting-source"] }],
    future_semantic_field: { must_preserve: "用户明确继续以前不恢复执行。" } };
}

function freeze(value) {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

test("discussion model projection removes only redundant snapshots and preserves all semantic fields", () => {
  const value = discussion();
  const expected = structuredClone(value);
  for (const field of ["constraints", "open_points", "responded_points"]) delete expected[field][0].evidence;
  assert.deepEqual(modelDiscussionProjection(value), expected);
  for (const field of ["constraints", "open_points", "responded_points"]) {
    assert.deepEqual(modelDiscussionProjection(value)[field][0].source_refs, value[field][0].source_refs);
    assert.equal(modelDiscussionProjection(value)[field][0].id, value[field][0].id);
  }
});

test("projection does not mutate the frozen host discussion or its version/hash fences", () => {
  const value = freeze(discussion());
  const before = JSON.stringify(value);
  const result = modelDiscussionProjection(value);
  assert.equal(JSON.stringify(value), before);
  assert.notEqual(result, value);
  assert.notEqual(result.constraints[0], value.constraints[0]);
  assert.equal(value.constraints[0].evidence[0].revision, 2);
  assert.equal(value.constraints[0].evidence[0].evidence_hash, "a".repeat(64));
});

for (const [label, evidence] of [
  ["foreign source", [{ segment_id: "other-source", revision: 2, evidence_hash: "a".repeat(64) }]],
  ["additional semantic evidence", [{ segment_id: "meeting-source", revision: 2, evidence_hash: "a".repeat(64), quote: "原文仍有语义" }]],
  ["invalid revision", [{ segment_id: "meeting-source", revision: 0, evidence_hash: "a".repeat(64) }]],
  ["missing hash", [{ segment_id: "meeting-source", revision: 2 }]],
  ["mixed snapshots", [{ segment_id: "meeting-source", revision: 2, evidence_hash: "a".repeat(64) }, "语义证据"]],
  ["non-array evidence", { explanation: "用户上传说明" }],
]) {
  test(`unknown or incomplete evidence is preserved: ${label}`, () => {
    const value = { constraints: [point({ evidence })] };
    assert.deepEqual(modelDiscussionProjection(value), value);
  });
}

test("projection keeps non-host fields and evidence outside the three formal point lists", () => {
  const value = discussion();
  value.constraints.push("人工录入条件", null, ["未识别结构"]);
  value.proposals[0].evidence = [{ segment_id: "meeting-source", revision: 2, evidence_hash: "a".repeat(64) }];
  value.constraints[0].owner = "待用户确认";
  value.constraints[0].deadline = null;
  value.constraints[0].future_meaning = { evidence_hash: "这是未知语义字段，不应递归移除" };
  const projected = modelDiscussionProjection(value);
  assert.deepEqual(projected.constraints.slice(1), value.constraints.slice(1));
  assert.deepEqual(projected.proposals, value.proposals);
  assert.equal(projected.constraints[0].owner, "待用户确认");
  assert.equal(projected.constraints[0].deadline, null);
  assert.deepEqual(projected.constraints[0].future_meaning, value.constraints[0].future_meaning);
  for (const input of [null, undefined, "unknown", []]) assert.equal(modelDiscussionProjection(input), input);
});

test("participation prompt projection preserves exact citation catalog, intent, basis and full host metadata", () => {
  const meaning = discussion();
  const context = freeze({ request_metadata: { profile: "participation", intent: "ask_question", topic_id: meaning.topic_id,
    analysis_basis_revision: 3, discussion_context: meaning, input_context: { context_revision: 4 },
    evidence: [{ segment_id: "meeting-source", revision: 2, evidence_hash: "a".repeat(64) }] },
    rolling_state: { discussion: meaning }, user_request: "帮我提出一个值得澄清的问题。",
    context_paragraphs: [{ id: "meeting-source", text: "接口接收不等于所有记录处理完成。", revision: 2 }],
    new_paragraphs: [], retrieval_paragraphs: [], candidate_evidence_paragraphs: [], document_evidence: [],
    knowledge_context: {}, participant_role: null, focus_points: [], glossary: [] });
  const before = JSON.stringify(context);
  const catalog = createParticipationQuoteCatalog(context, "public-fixture");
  const payload = JSON.parse(participationPrompt(context, "zh", catalog));
  assert.equal(payload.intent, "ask_question");
  assert.equal(payload.topic_id, meaning.topic_id);
  assert.equal(payload.analysis_basis_revision, 3);
  assert.deepEqual(payload.discussion, modelDiscussionProjection(meaning));
  assert.equal(payload.recent_context[0].id, "meeting-source");
  assert.equal(payload.recent_context[0].revision, 2);
  assert.equal(payload.recent_context[0].quotes[0].text, context.context_paragraphs[0].text);
  const hydrated = hydrateParticipationQuotes({ citation_quote_ids: [payload.recent_context[0].quotes[0].quote_id] }, context, catalog);
  assert.deepEqual(hydrated.evidence_segment_ids, ["meeting-source"]);
  assert.equal(hydrated.evidence_quote, context.context_paragraphs[0].text);
  assert.equal(JSON.stringify(context), before);
  assert.ok(context.request_metadata.discussion_context.constraints[0].evidence);
});

import assert from "node:assert/strict";
import test from "node:test";
import { createModels, fauxAssistantMessage, fauxProvider, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime, validateEvaluationRequest } from "../src/runtime.mjs";
import {
  createParticipationQuoteCatalog, exactQuoteSlices, hydrateParticipationQuotes,
  registerTranscriptQuotes, registerDocumentQuotes, renderTranscriptQuotes, renderDocumentQuotes,
} from "../src/participation_quotes.mjs";

function request(id = "quoted-selection") {
  return { request_id: id, session_id: "public-meeting", provider: {}, context: {
    coach_profile: "participation", trigger_type: "user_request", priority_mode: "deep", state_revision: 1,
    user_request: "帮我提出有价值的问题", request_metadata: { profile: "participation", intent: "ask_question",
      topic_id: "topic", analysis_basis_revision: 1, input_context: { material_set_id: "set", material_context_revision: 1 } },
    new_paragraphs: [{ id: "current", revision: 1, text: "现在计划失败后直接重发请求。" }],
    context_paragraphs: [], rolling_state: {}, knowledge_context: { material_set_id: "set" },
    document_evidence: [document()],
  } };
}

function document(overrides = {}) {
  return { source_type: "meeting_attachment", source_id: "material-span", set_id: "set", meeting_id: "public-meeting",
    attachment_id: "attachment", version_id: "version", version: 1, context_revision: 1,
    title: "公开超时约束", content_hash: "hash", quote: "超时不能判断为失败，必须先查询处理结果。", locator: { paragraph: 1 }, ...overrides };
}

function setup(value = request()) {
  const context = validateEvaluationRequest(value).context;
  const catalog = createParticipationQuoteCatalog(context, value.session_id);
  const transcript = renderTranscriptQuotes(catalog, context.new_paragraphs)[0].quotes.map((quote) => quote.quote_id);
  const documents = renderDocumentQuotes(catalog, context.document_evidence)[0].quotes.map((quote) => quote.quote_id);
  const params = { event_type: "question_to_user", title: "核对重复请求的处理", urgency: "low", confidence: .9,
    recommendation: "是否可以先明确重复请求的识别与处理结果复用方式，避免异常重试产生重复订单？",
    reason: "当前在讨论失败后重发，资料要求先查询结果。", assumptions: ["需要核对查询不可用时的处理边界。"],
    citation_quote_ids: [transcript[0], documents[0]] };
  return { value, context, catalog, transcript, documents, params };
}

test("quote slices cover exact supplied sentences, whitespace and Unicode without duplicate prompt text", () => {
  const text = "  条件未确认。\n" + "😀".repeat(205) + "不能直接重试！尾部\n";
  const slices = exactQuoteSlices(text);
  assert.equal(slices.join(""), text);
  assert.ok(slices.every((slice) => slice.length <= 300));
  assert.ok(slices.every((slice) => !/[\uD800-\uDBFF]$/u.test(slice) && !/^[\uDC00-\uDFFF]/u.test(slice)));
  const { context, catalog } = setup();
  const rendered = renderTranscriptQuotes(catalog, context.new_paragraphs)[0];
  assert.equal(rendered.text, undefined);
  assert.equal(rendered.quotes.map((item) => item.text).join(""), context.new_paragraphs[0].text);
  const material = renderDocumentQuotes(catalog, context.document_evidence)[0];
  assert.equal(material.quote, undefined);
  assert.equal(material.quotes.map((item) => item.text).join(""), context.document_evidence[0].quote);
});

test("host selection restores only selected exact old card fields, with no default evidence", () => {
  const { context, catalog, params } = setup();
  const card = hydrateParticipationQuotes(params, context, catalog);
  assert.deepEqual(card.evidence_segment_ids, ["current"]);
  assert.equal(card.evidence_quote, context.new_paragraphs[0].text);
  assert.deepEqual(card.document_evidence_refs, [{ source_id: "material-span", quote: document().quote }]);
  assert.deepEqual(card.supporting_facts, [{ source_ref: "current", text: context.new_paragraphs[0].text },
    { source_ref: "material-span", text: document().quote }]);
  assert.equal(card.citation_quote_ids, undefined);
});

test("individual short sentences permit focused current-topic facts without copying an already responded topic", () => {
  const value = request();
  value.context.new_paragraphs[0].text = "通知失败已确认不阻塞。付款超时后用户可能重新付款。查询不可用的处理边界尚待核对。";
  value.context.document_evidence[0].quote = "通知失败只展示提示。付款超时不能判断为失败。必须先查询处理结果。";
  const { context, catalog, params } = setup(value);
  const originals = renderTranscriptQuotes(catalog, context.new_paragraphs)[0].quotes;
  const material = renderDocumentQuotes(catalog, context.document_evidence)[0].quotes;
  assert.equal(originals.length, 3); assert.equal(material.length, 3);
  const chosenOriginal = originals.find((quote) => quote.text === "查询不可用的处理边界尚待核对。");
  const chosenMaterial = material.find((quote) => quote.text === "付款超时不能判断为失败。");
  const card = hydrateParticipationQuotes({ ...params, citation_quote_ids: [chosenOriginal.quote_id, chosenMaterial.quote_id] }, context, catalog);
  assert.equal(card.evidence_quote, chosenOriginal.text);
  assert.deepEqual(card.supporting_facts, [{ source_ref: "current", text: chosenOriginal.text },
    { source_ref: "material-span", text: chosenMaterial.text }]);
  assert.ok(!JSON.stringify(card.supporting_facts).includes("通知"));
});

test("adjacent document choices merge in source order without changing selected facts or Unicode whitespace", () => {
  const value = request();
  value.context.document_evidence[0].quote = "未选择的条件。  超时😀不能判断失败。\n必须先查询结果！尾部未选择。";
  const { context, catalog, params, transcript, documents } = setup(value);
  const quotes = renderDocumentQuotes(catalog, context.document_evidence)[0].quotes;
  const card = hydrateParticipationQuotes({ ...params,
    citation_quote_ids: [transcript[0], documents[2], documents[1]] }, context, catalog);
  assert.deepEqual(card.document_evidence_refs, [{ source_id: "material-span", quote: "  超时😀不能判断失败。\n必须先查询结果！" }]);
  assert.deepEqual(card.supporting_facts, [{ source_ref: "current", text: context.new_paragraphs[0].text },
    { source_ref: "material-span", text: quotes[2].text }, { source_ref: "material-span", text: quotes[1].text }]);
  assert.equal(card.supporting_facts.length, 3);
  assert.ok(!JSON.stringify(card.document_evidence_refs).includes("未选择"));
});

test("a gap between same-source document choices is rejected rather than filled with unselected text", () => {
  const value = request(); value.context.document_evidence[0].quote = "超时不能判断失败。中间条件不能擅自补入。必须先查询结果。";
  const { context, catalog, params, transcript, documents } = setup(value);
  assert.throws(() => hydrateParticipationQuotes({ ...params,
    citation_quote_ids: [transcript[0], documents[2], documents[0]] }, context, catalog),
  (error) => error.quote_selection_code === "quote_selection_same_doc_source");
});

test("adjacent document quote above 300 UTF16 units is rejected without truncating selected Unicode", () => {
  const value = request(); value.context.document_evidence[0].quote = "😀".repeat(75) + "。\n" + "界".repeat(148) + "。";
  assert.equal(value.context.document_evidence[0].quote.length, 301);
  const { context, catalog, params, transcript, documents } = setup(value);
  assert.equal(documents.length, 2);
  assert.throws(() => hydrateParticipationQuotes({ ...params,
    citation_quote_ids: [transcript[0], ...documents] }, context, catalog),
  (error) => error.quote_selection_code === "quote_selection_budget");
});

test("adjacent document choices cannot bypass current source revision or authorization checks", () => {
  for (const mutation of ["version", "disabled", "input_binding"]) {
    const value = request(); value.context.document_evidence[0].quote = "超时不能判断失败。必须先查询结果。";
    const { context, catalog, params, transcript, documents } = setup(value);
    if (mutation === "version") context.document_evidence[0].version_id = "new-version";
    if (mutation === "disabled") context.document_evidence = [];
    if (mutation === "input_binding") context.request_metadata.input_context.material_context_revision += 1;
    assert.throws(() => hydrateParticipationQuotes({ ...params,
      citation_quote_ids: [transcript[0], ...documents] }, context, catalog),
    (error) => error.quote_selection_code === "quote_selection_source_changed");
  }
});

test("different document sources retain distinct references even when their text looks adjacent", () => {
  const value = request(); value.context.document_evidence = [document({ quote: "超时不能判断失败。" }),
    document({ source_id: "second-span", quote: "必须先查询结果。", locator: { paragraph: 2 } })];
  const { context, catalog, params, transcript } = setup(value);
  const sources = renderDocumentQuotes(catalog, context.document_evidence);
  const card = hydrateParticipationQuotes({ ...params,
    citation_quote_ids: [transcript[0], ...sources.map((source) => source.quotes[0].quote_id)] }, context, catalog);
  assert.deepEqual(card.document_evidence_refs, value.context.document_evidence.map((source) => ({ source_id: source.source_id, quote: source.quote })));
  assert.equal(card.supporting_facts.length, 3);
});

for (const [mutation, reason] of [["unknown", "unknown_id"], ["only_documents", "no_transcript"],
  ["duplicate", "duplicate"], ["missing", "no_transcript"], ["three_list_contract", "obsolete_contract"],
  ["free_quote", "obsolete_contract"], ["too_many_citations", "budget"], ["stale_request", "unknown_id"]]) {
  test(`quote selection rejects ${mutation} atomically without fallback source`, () => {
    const { context, catalog, params, transcript, documents } = setup();
    if (mutation === "unknown") params.citation_quote_ids = ["unknown"];
    if (mutation === "only_documents") params.citation_quote_ids = documents;
    if (mutation === "duplicate") params.citation_quote_ids = [transcript[0], transcript[0]];
    if (mutation === "missing") params.citation_quote_ids = [];
    if (mutation === "three_list_contract") Object.assign(params, { transcript_quote_ids: transcript,
      document_quote_ids: documents, supporting_fact_quote_ids: documents });
    if (mutation === "free_quote") params.evidence_quote = "自称逐字但不是原文";
    if (mutation === "too_many_citations") params.citation_quote_ids = Array(5).fill(transcript[0]);
    if (mutation === "stale_request") params.citation_quote_ids = renderTranscriptQuotes(createParticipationQuoteCatalog(context), context.new_paragraphs)[0].quotes.map((item) => item.quote_id);
    assert.throws(() => hydrateParticipationQuotes(params, context, catalog), (error) => error.quote_selection_code === `quote_selection_${reason}`);
  });
}

for (const mutation of ["transcript_revision", "transcript_text", "material_hash", "material_version", "scope_revision", "material_disabled", "topic_basis", "input_binding"]) {
  test(`already selected quote rejects changed ${mutation} before card acceptance`, () => {
    const { context, catalog, params } = setup();
    if (mutation === "transcript_revision") context.new_paragraphs[0].revision += 1;
    if (mutation === "transcript_text") context.new_paragraphs[0].text += "已经采用。";
    if (mutation === "material_hash") context.document_evidence[0].content_hash = "new-hash";
    if (mutation === "material_version") context.document_evidence[0].version_id = "new-version";
    if (mutation === "scope_revision") context.document_evidence[0].context_revision += 1;
    if (mutation === "material_disabled") context.document_evidence = [];
    if (mutation === "topic_basis") context.request_metadata.analysis_basis_revision += 1;
    if (mutation === "input_binding") context.request_metadata.input_context.material_context_revision += 1;
    assert.throws(() => hydrateParticipationQuotes(params, context, catalog), /changed/);
  });
}

test("catalog never pre-reads hidden retrieval, summaries, material catalog or another meeting", () => {
  const value = request();
  value.context.retrieval_paragraphs = [{ id: "hidden", text: "未返回的历史内容", revision: 1 }];
  value.context.rolling_state.summary = "摘要不是证据";
  value.context.knowledge_context.attachment_catalog = [{ title: "目录不是证据" }];
  const { catalog, context } = setup(value);
  assert.ok([...catalog.entries.values()].every((entry) => !entry.quote.includes("未返回") && !entry.quote.includes("不是证据")));
  assert.throws(() => registerDocumentQuotes(catalog, [document({ source_id: "foreign", meeting_id: "other-meeting" })]), /scope/);
  assert.ok(!catalog.sources.has("document:foreign"));
  assert.throws(() => hydrateParticipationQuotes({ citation_quote_ids: ["hidden"] }, context, catalog));
});

test("catalog and combined-document budgets fail without partial registration", () => {
  const value = request(); value.context.document_evidence[0].quote = "字".repeat(600);
  value.context.new_paragraphs[0].text = "原".repeat(1500);
  const { catalog, context, params, transcript, documents } = setup(value);
  assert.throws(() => hydrateParticipationQuotes({ ...params, citation_quote_ids: transcript.slice(0, 4) }, context, catalog), /budget/);
  assert.throws(() => hydrateParticipationQuotes({ ...params, citation_quote_ids: [transcript[0], ...documents] }, context, catalog), /budget/);
  const before = catalog.entries.size;
  assert.throws(() => registerTranscriptQuotes(catalog, [{ id: "new", text: "允许范围", revision: 1 },
    { id: "over", text: "字".repeat(160001), revision: 1 }]), /budget/);
  assert.equal(catalog.entries.size, before);
  assert.ok(!catalog.sources.has("transcript:new"));
});

function harness(id) {
  const faux = fauxProvider({ provider: `quote-choice-${id}` });
  const models = createModels(); models.setProvider(faux.provider);
  const runtime = new PiCoachRuntime({ backendFactory: () => ({ identity: id,
    model: faux.getModel(), streamFn: models.streamSimple.bind(models) }) });
  return { faux, runtime, value: request(id) };
}

function payload(context, role = "user") {
  return JSON.parse(context.messages.findLast((message) => message.role === role).content.find((block) => block.type === "text").text);
}

function selected(payloadValue, extra = {}) {
  const { params } = setup();
  const transcript = payloadValue.recent_context.flatMap((source) => source.quotes.map((quote) => quote.quote_id));
  const documents = payloadValue.document_evidence.flatMap((source) => source.quotes.slice(0, 1).map((quote) => quote.quote_id));
  return { ...params, citation_quote_ids: [...transcript.slice(0, 1), ...documents], ...extra };
}

test("real Pi SDK first-turn choice is hydrated and accepted by unchanged typed evidence validator", async () => {
  const { faux, runtime, value } = harness("first-turn");
  faux.setResponses([(context) => fauxAssistantMessage(fauxToolCall("submit_intervention", selected(payload(context))), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention");
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.equal(result.intervention.evidence_quote, value.context.new_paragraphs[0].text);
  assert.deepEqual(result.intervention.supporting_facts, [{ source_ref: "current", text: value.context.new_paragraphs[0].text },
    { source_ref: "material-span", text: document().quote }]);
  assert.equal(result.intervention.citation_quote_ids, undefined);
});

test("real Pi SDK accepts two adjacent same-document choices in one submit and restores a single exact source ref", async () => {
  const { faux, runtime, value } = harness("adjacent-document");
  value.context.document_evidence[0].quote = "超时不能判断为失败。\n必须先查询处理结果。";
  faux.setResponses([(context) => {
    const initial = payload(context); const choices = initial.document_evidence[0].quotes;
    assert.equal(choices.length, 2);
    return fauxAssistantMessage(fauxToolCall("submit_intervention", selected(initial, {
      citation_quote_ids: [initial.recent_context[0].quotes[0].quote_id, choices[1].quote_id, choices[0].quote_id],
    })), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "intervention"); assert.equal(result.metrics.turns, 1);
  assert.equal(result.metrics.tool_calls, 1);
  assert.deepEqual(result.metrics.tool_names, ["submit_intervention"]);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.deepEqual(result.intervention.document_evidence_refs, [{ source_id: "material-span", quote: value.context.document_evidence[0].quote }]);
  assert.equal(result.intervention.source_evidence.length, 1);
  assert.equal(result.intervention.source_evidence[0].quote, value.context.document_evidence[0].quote);
  assert.equal(result.intervention.source_evidence[0].version_id, "version");
  assert.deepEqual(result.intervention.supporting_facts, [{ source_ref: "current", text: value.context.new_paragraphs[0].text },
    { source_ref: "material-span", text: "必须先查询处理结果。" }, { source_ref: "material-span", text: "超时不能判断为失败。\n" }]);
});

test("one selected meeting citation is one fact and never silently cites a provided document", async () => {
  const { faux, runtime, value } = harness("one-meeting-citation");
  faux.setResponses([(context) => {
    const current = payload(context).recent_context[0].quotes[0].quote_id;
    return fauxAssistantMessage(fauxToolCall("submit_intervention", selected(payload(context), {
      citation_quote_ids: [current], reason: "当前在讨论异常重试，先明确处理边界有助于评估方案。",
    })), { stopReason: "toolUse" });
  }]);
  const result = await runtime.evaluate(value);
  assert.equal(result.metrics.turns, 1);
  assert.deepEqual(result.metrics.tool_errors, []);
  assert.deepEqual(result.intervention.document_evidence_refs, []);
  assert.deepEqual(result.intervention.source_evidence, []);
  assert.deepEqual(result.intervention.supporting_facts, [{ source_ref: "current", text: value.context.new_paragraphs[0].text }]);
});

for (const tool of ["search_prior_evidence", "read_transcript_span", "search_project_documents"]) {
  test(`real Pi SDK ${tool} registers returned authorized quotes before second-turn choice`, async () => {
    const { faux, runtime, value } = harness(tool);
    let initial;
    faux.setResponses([(context) => {
      initial = payload(context);
      const args = tool === "read_transcript_span" ? { segment_id: "prior" } : { query: "查询边界" };
      return fauxAssistantMessage(fauxToolCall(tool, args), { stopReason: "toolUse" });
    }, (context) => {
      const result = payload(context, "toolResult");
      assert.equal(result.results[0].text, undefined);
      assert.equal(result.results[0].quote, undefined);
      const quote = result.results[0].quotes[0];
      const choice = selected(initial);
      if (tool === "search_project_documents") {
        choice.citation_quote_ids = [initial.recent_context[0].quotes[0].quote_id, quote.quote_id];
      } else {
        choice.citation_quote_ids = [quote.quote_id, ...initial.document_evidence.map((source) => source.quotes[0].quote_id)];
      }
      return fauxAssistantMessage(fauxToolCall("submit_intervention", choice), { stopReason: "toolUse" });
    }]);
    const options = { searchEvidence: async () => [{ id: "prior", text: "之前要求先查询处理结果。", revision: 2 }],
      readEvidenceSpan: async () => [{ id: "prior", text: "之前要求先查询处理结果。", revision: 2 }],
      searchDocuments: async () => [document({ source_id: "lookup-span", quote: "查询不可用时不能直接重发。" })] };
    let result;
    try { result = await runtime.evaluate(value, options); }
    catch (error) { assert.fail(JSON.stringify(error.metrics?.tool_errors ?? [], null, 2)); }
    assert.equal(result.action, "intervention");
    assert.equal(result.metrics.turns, 2);
    assert.deepEqual(result.metrics.tool_names, [tool, "submit_intervention"]);
    assert.deepEqual(result.metrics.tool_errors, []);
    assert.ok(result.intervention.supporting_facts.some((fact) => fact.text.includes(tool === "search_project_documents" ? "查询不可用" : "之前要求")));
  });
}

test("real Pi SDK rejects a bad quote choice and may choose silence without inventing fallback evidence", async () => {
  const { faux, runtime, value } = harness("reject-choice");
  faux.setResponses([(context) => fauxAssistantMessage(fauxToolCall("submit_intervention", selected(payload(context), {
    citation_quote_ids: ["not-issued"],
  })), { stopReason: "toolUse" }), fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "本轮未形成可靠贡献。" }), { stopReason: "toolUse" })]);
  const result = await runtime.evaluate(value);
  assert.equal(result.action, "silent");
  assert.equal(result.intervention, null);
  assert.deepEqual(result.metrics.tool_errors, [{ tool: "submit_intervention", code: "quote_selection_unknown_id" }]);
});

for (const reason of ["duplicate", "unknown_id", "no_transcript", "same_doc_source", "obsolete_contract", "budget", "source_changed", "tool_schema_validation"]) {
  test(`real Pi SDK reports fixed ${reason} without echoing rejected arguments`, async () => {
    const { faux, runtime, value } = harness(`classified-${reason}`);
    if (reason === "same_doc_source") value.context.document_evidence[0].quote = "超时不能当作失败。中间句不能擅自加入。必须先查询结果。";
    const expected = reason === "tool_schema_validation" ? reason : `quote_selection_${reason}`;
    faux.setResponses([(context) => {
      const initial = payload(context); const choice = selected(initial);
      const transcript = initial.recent_context[0].quotes[0].quote_id;
      const doc = initial.document_evidence[0].quotes[0].quote_id;
      if (reason === "duplicate") choice.citation_quote_ids = [transcript, transcript];
      if (reason === "unknown_id") choice.citation_quote_ids = ["not-issued-private-fixture-marker"];
      if (reason === "no_transcript") choice.citation_quote_ids = [doc];
      if (reason === "same_doc_source") choice.citation_quote_ids = [transcript, initial.document_evidence[0].quotes[2].quote_id, doc];
      if (reason === "obsolete_contract") Object.assign(choice, { transcript_quote_ids: [transcript],
        document_quote_ids: [doc], supporting_fact_quote_ids: [doc], evidence_quote: "private-fixture-marker" });
      if (reason === "budget") choice.citation_quote_ids = Array(5).fill(transcript);
      if (reason === "source_changed") runtime.sessions.get(value.session_id).activeContext.document_evidence[0].content_hash = "new-private-fixture-marker";
      if (reason === "tool_schema_validation") { delete choice.title; choice.reason = "private-fixture-marker"; }
      return fauxAssistantMessage(fauxToolCall("submit_intervention", choice), { stopReason: "toolUse" });
    }, (context) => {
      const tool = context.messages.findLast((message) => message.role === "toolResult");
      const text = tool.content.find((block) => block.type === "text").text;
      assert.match(text, new RegExp(`^Intervention rejected: ${expected}\\.`));
      assert.ok(!text.includes("Received arguments") && !text.includes("private-fixture-marker"));
      assert.deepEqual(tool.details, { code: expected });
      return fauxAssistantMessage(fauxToolCall("keep_silent", { reason: "本轮不冒险生成无依据建议。" }), { stopReason: "toolUse" });
    }]);
    const result = await runtime.evaluate(value);
    assert.equal(result.action, "silent");
    assert.equal(result.metrics.turns, 2);
    assert.equal(result.metrics.tool_calls, 2);
    assert.deepEqual(result.metrics.tool_names, ["submit_intervention", "keep_silent"]);
    assert.deepEqual(result.metrics.tool_errors, [{ tool: "submit_intervention", code: expected }]);
  });
}

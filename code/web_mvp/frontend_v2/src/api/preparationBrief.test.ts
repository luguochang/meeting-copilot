import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpPreparationBriefApi, matchesPreparation, parsePreparationJob, type PreparationInput } from "./preparationBrief";

import { rawPreparationJob } from "./preparationBrief.fixtures";
const input: PreparationInput = { selection: { projectId: "project", folderId: null, documentIds: null }, goal: "确认上线条件", role: "项目负责人", focus: ["风险"], scene: "project" };
const respond = (value: unknown) => vi.fn().mockResolvedValue(new Response(JSON.stringify(value), { status: 200 }));
afterEach(() => vi.unstubAllGlobals());

describe("preparation job boundaries", () => {
  it("reads history without authorizing a job", async () => {
    const fetcher = respond({ items: [rawPreparationJob()], has_more: false, next_cursor: null }); vi.stubGlobal("fetch", fetcher);
    const page = await new HttpPreparationBriefApi("http://candidate/").list("project");
    expect(page.items[0].result?.questions[0].basis).toBe("user_goal");
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toContain("/preparation-jobs?project_id=project");
    expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  });
  it("preserves empty selected document scope", async () => {
    const fetcher = respond({ job: rawPreparationJob({ document_ids: [] }) }); vi.stubGlobal("fetch", fetcher);
    await new HttpPreparationBriefApi().start({ ...input, selection: { ...input.selection, documentIds: [] } }, 4);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ folder_id: null, document_ids: [], goal: input.goal, role: input.role, focus: input.focus, scene: input.scene, expected_project_revision: 4 });
  });
  it("retries only the captured job revision and input hash", async () => {
    const fetcher = respond({ job: rawPreparationJob({ status: "pending", result: null, completed_at_ms: null }) }); vi.stubGlobal("fetch", fetcher);
    await new HttpPreparationBriefApi().retry(parsePreparationJob(rawPreparationJob()), 9);
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_revision: 2, expected_input_hash: "captured-hash", expected_project_revision: 9 });
  });
  it("rejects another project's job", async () => {
    vi.stubGlobal("fetch", respond({ items: [rawPreparationJob({ project_id: "other" })], has_more: false, next_cursor: null }));
    await expect(new HttpPreparationBriefApi().list("project")).rejects.toThrow("其他项目");
  });
  it("does not treat goal planning as factual conditions", () => {
    expect(() => parsePreparationJob(rawPreparationJob({ result: { agenda: [], questions: [], conditions: [{ text: "预算已经批准", basis: "user_goal", source_refs: [] }] } }))).toThrow("条件必须");
  });
  it("requires sources for asserted material facts", () => {
    expect(() => parsePreparationJob(rawPreparationJob({ result: { agenda: [{ text: "预算120元", basis: "source", source_refs: [] }], questions: [], conditions: [] } }))).toThrow("依据");
  });
  it("recognizes project-state provenance and rejects a forged scope", () => {
    const ref = { source_type: "project_state", source_id: "state", project_id: "project", revision: 3, quote: "预算120元", source_refs: [] };
    const result = { agenda: [], questions: [], conditions: [{ text: "预算120元", basis: "source", source_refs: [ref] }] };
    expect(parsePreparationJob(rawPreparationJob({ result })).result?.conditions[0].sourceRefs[0].sourceType).toBe("project_state");
    expect(() => parsePreparationJob(rawPreparationJob({ result: { ...result, conditions: [{ ...result.conditions[0], source_refs: [{ ...ref, project_id: "other" }] }] } }))).toThrow("不一致");
  });
  it("reads the registered document evidence returned by the real preparation host without inventing an ordinal", () => {
    const ref = { source_type: "document", source_id: "span", document_id: "doc", project_id: "project", title: "发布条件", version_id: "v1", version: 1, content_hash: "hash", context_revision: 0, quote: "预算120元", locator: { type: "converted_text", paragraph: 1, offset: 0 } };
    const result = { agenda: [], questions: [], conditions: [{ text: "预算120元", basis: "source", source_refs: [ref] }] };
    const parsed = parsePreparationJob(rawPreparationJob({ result })).result?.conditions[0].sourceRefs[0];
    expect(parsed).toMatchObject({ sourceType: "document", sourceId: "span", documentId: "doc", versionId: "v1", version: 1, quote: "预算120元", meetingId: null });
    expect(parsed).not.toHaveProperty("ordinal");
    expect(() => parsePreparationJob(rawPreparationJob({ result: { ...result, conditions: [{ ...result.conditions[0], source_refs: [{ ...ref, project_id: "other" }] }] } }))).toThrow("不一致");
  });
  it("still rejects incomplete or invalid registered document evidence", () => {
    const ref = { source_type: "document", source_id: "span", document_id: "doc", project_id: "project", title: "发布条件", version_id: "v1", version: 1, content_hash: "hash", context_revision: 0, quote: "预算120元", locator: { paragraph: 1 } };
    for (const invalid of [{ ...ref, version_id: "" }, { ...ref, context_revision: -1 }, { ...ref, version: 0 }]) {
      expect(() => parsePreparationJob(rawPreparationJob({ result: { agenda: [], questions: [], conditions: [{ text: "预算120元", basis: "source", source_refs: [invalid] }] } }))).toThrow();
    }
  });
  it("distinguishes all documents, empty scope and a changed target", () => {
    const job = parsePreparationJob(rawPreparationJob());
    expect(matchesPreparation(job, input)).toBe(true);
    expect(matchesPreparation(job, { ...input, selection: { ...input.selection, documentIds: [] } })).toBe(false);
    expect(matchesPreparation(job, { ...input, goal: "讨论招聘" })).toBe(false);
  });
});

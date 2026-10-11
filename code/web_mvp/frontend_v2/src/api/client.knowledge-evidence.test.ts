import { afterEach, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "./client";
import { HttpKnowledgeApi } from "./knowledge";
import { parseDocumentSourceEvidence } from "./sourceEvidence";

const reference = { source_type: "document", source_id: "span-1", document_id: "doc-1", project_id: "project-1", title: "预算", version_id: "version-2", version: 2, content_hash: "hash", locator: { type: "pdf_page", page: 2 }, quote: "预算为三万元", context_revision: 3 };
const message = { message_id: "answer", thread_id: "thread", meeting_id: "meeting", role: "assistant", content: "依据旧预算得到的回答", scope: "project", status: "failed", error_class: "EvidenceSuperseded", evidence_status: "stale", evidence: [reference, { segment_id: "segment", transcript_seq: 2, start_ms: 0, end_ms: 1000, quote: "会议里的原话" }], created_at_ms: 1, updated_at_ms: 2 };
afterEach(() => vi.unstubAllGlobals());
it("consumes mixed project Ask citations and keeps failed stale content readable", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response([{ type: "started", thread_id: "thread" }, { type: "delta", text: "依据" }, { type: "done", thread_id: "thread", message }].map((x) => JSON.stringify(x)).join("\n"), { headers: { "Content-Type": "application/x-ndjson" } })); vi.stubGlobal("fetch", fetcher);
  const delta = vi.fn(); const result = await new HttpMeetingApi().askMeeting("meeting", { question: "预算如何？", scope: "project", includeKnowledge: true }, delta);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ scope: "project", include_knowledge: true }); expect(result.message.content).toBe(message.content); expect(result.message.evidenceStatus).toBe("stale");
  expect(result.message.evidence[0]).toEqual(parseDocumentSourceEvidence(reference)); expect(result.message.evidence[0]).not.toHaveProperty("segmentId"); expect(result.message.evidence[1]).toMatchObject({ sourceType: "transcript", segmentId: "segment" });
});
it("sends typed document evidence separately when converting a valid Ask into a candidate", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("{}", { headers: { "Content-Type": "application/json" } })); vi.stubGlobal("fetch", fetcher); const evidence = parseDocumentSourceEvidence(reference);
  await new HttpMeetingApi().createMeetingEntity("meeting", { kind: "decision_candidate", text: "待确认预算", sourceMessageId: "real-answer", evidence: [], sourceEvidence: [evidence] });
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toMatchObject({ source_message_id: "real-answer", evidence: [], source_evidence: [reference] });
});
it("distinguishes a full project scope from explicit zero-document selection on persistence", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ context: { meeting_id: "meeting", project_id: "project", folder_id: null, selection_mode: "documents", selected_document_ids: [], context_revision: 4 } }), { headers: { "Content-Type": "application/json" } })); vi.stubGlobal("fetch", fetcher);
  const result = await new HttpKnowledgeApi().saveMeetingContext("meeting", { projectId: "project", folderId: null, documentIds: [] }, 3);
  expect(result.documentIds).toEqual([]); expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ project_id: "project", folder_id: null, document_ids: [], expected_revision: 3 });
});
it("rejects document citations missing their actual source/versions instead of fabricating transcript IDs", () => {
  expect(() => parseDocumentSourceEvidence({ ...reference, source_id: null })).toThrow("source_id"); expect(() => parseDocumentSourceEvidence({ ...reference, context_revision: -1 })).toThrow("context_revision");
});

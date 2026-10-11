import { parseMeetingSnapshot } from "./schema";

const answer = {
  suggestion_id: "answer-updated", meeting_id: "meeting", generation_id: "generation-updated", kind: "answer",
  evidence_segment_id: "question", evidence_transcript_seq: 2, evidence_hash: "question-hash", state_revision: 1,
  status: "committed", text: "依据为未验收，应先完成验收。", draft_text: "", draft_seq: 1,
  context_evidence: [
    { segment_id: "context", evidence_hash: "corrected-hash", revision: 2 },
    { segment_id: "question", evidence_hash: "question-hash", revision: 1 },
  ],
  supersedes_suggestion_id: "answer-original",
};

function snapshot(suggestion: Record<string, unknown>) {
  return parseMeetingSnapshot({ meeting_id: "meeting", last_seq: 2, segments: [], suggestions: [suggestion] });
}

it("retains answer source versions and the original answer link in snapshots", () => {
  expect(snapshot(answer).suggestions[0]).toMatchObject({
    contextEvidence: [{ segmentId: "context", evidenceHash: "corrected-hash", revision: 2 },
      { segmentId: "question", evidenceHash: "question-hash", revision: 1 }],
    supersedesSuggestionId: "answer-original",
  });
});

it("accepts old snapshots without revision bindings and both field conventions", () => {
  const { context_evidence, supersedes_suggestion_id, ...legacy } = answer;
  expect(snapshot(legacy).suggestions[0]).toMatchObject({ contextEvidence: [], supersedesSuggestionId: null });
  expect(snapshot({ ...legacy, contextEvidence: context_evidence.map(({ segment_id, evidence_hash, revision }) =>
    ({ segmentId: segment_id, evidenceHash: evidence_hash, revision })), supersedesSuggestionId: supersedes_suggestion_id })
    .suggestions[0].supersedesSuggestionId).toBe("answer-original");
});

it("rejects invalid and duplicate answer source versions", () => {
  for (const context of [
    [{ segment_id: "context", evidence_hash: "hash", revision: 0 }],
    [{ segment_id: "context", evidence_hash: "hash", revision: 1.2 }],
    [answer.context_evidence[0], answer.context_evidence[0]],
    Array(17).fill(answer.context_evidence[0]),
  ]) expect(() => snapshot({ ...answer, context_evidence: context })).toThrow();
});

const documentReference = { source_type: "document", source_id: "span-1", document_id: "doc", project_id: "project", title: "预算依据", version_id: "version-2", version: 2, content_hash: "doc-hash", locator: { type: "pdf_page", page: 2 }, quote: "预算三万元", context_revision: 3 };
it("retains independent typed document bindings on fast answers and Pi decisions", () => {
  const parsed = parseMeetingSnapshot({ meeting_id: "meeting", last_seq: 2, segments: [], suggestions: [{ ...answer, source_evidence: [documentReference] }], coach_decision: { origin: "pi", status: "intervention", source_evidence: [documentReference] } });
  expect(parsed.suggestions[0].sourceEvidence?.[0]).toMatchObject({ sourceType: "document", sourceId: "span-1", versionId: "version-2", contextRevision: 3 });
  expect(parsed.suggestions[0].contextEvidence).toHaveLength(2); expect(parsed.coachDecision?.sourceEvidence?.[0].contentHash).toBe("doc-hash"); expect(parsed.suggestions[0].sourceEvidence?.[0]).not.toHaveProperty("segmentId");
});
it("rejects malformed document source versions in answer snapshots", () => {
  expect(() => snapshot({ ...answer, source_evidence: [{ ...documentReference, version_id: null }] })).toThrow("version_id");
});

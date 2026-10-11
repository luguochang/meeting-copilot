import { parseReviewDocument } from "./schema";

describe("review source selection", () => {
  const raw = {
    document_id: "document", meeting_id: "meeting", document_kind: "minutes", revision: 4,
    source_transcript_revision: 2,
    ai_generated: { version: 2, source_transcript_revision: 2, content: { markdown: "新的 AI 初稿" } },
    user_final: { version: 1, source_transcript_revision: 1, content: { markdown: "旧的自动副本" }, modified: false },
    dirty_state: "saved",
  };

  it("uses the fresh AI version instead of an unmodified initial copy", () => {
    expect(parseReviewDocument(raw)).toMatchObject({
      source: "ai_generated", sourceRevision: 2, contentJson: { markdown: "新的 AI 初稿" },
    });
  });

  it("keeps a human final with its own source version after AI regeneration", () => {
    expect(parseReviewDocument({ ...raw, dirty_state: "stale", user_final: { ...raw.user_final, modified: true } })).toMatchObject({
      source: "user_final", sourceRevision: 1, dirtyState: "stale", contentJson: { markdown: "旧的自动副本" },
    });
  });
});

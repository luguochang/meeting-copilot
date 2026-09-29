import { describe, expect, it } from "vitest";
import type { CoachHistoryEntry, Suggestion } from "../../domain/events";
import { buildAnswerThreads } from "./answerThreads";

function answer(id: string, seq: number): Suggestion {
  return {
    suggestionId: id,
    meetingId: "meeting-1",
    jobId: `job-${id}`,
    generationId: `generation-${id}`,
    kind: "answer",
    questionText: `question-${id}`,
    evidenceSegmentId: `segment-${id}`,
    evidenceTranscriptSeq: seq,
    evidenceHash: `hash-${id}`,
    stateRevision: seq,
    status: "committed",
    draftText: "",
    draftSeq: 1,
    text: `answer-${id}`,
    finalDraftSeq: 1,
    feedback: null,
    createdAtMs: seq,
    updatedAtMs: seq,
    committedAtMs: seq,
  };
}

function revision(answerId: string, id: string, createdAtMs: number): CoachHistoryEntry {
  return {
    historyId: id,
    answerId,
    promptProfile: "deep_answer",
    question: `pi-${id}`,
    reason: "missing detail",
    evidenceSegmentIds: [`segment-${answerId}`],
    evidenceQuote: "evidence",
    urgency: "medium",
    status: "intervention",
    createdAtMs,
  };
}

describe("buildAnswerThreads", () => {
  it("keeps answers independent and orders Pi revisions within their bound answer", () => {
    const threads = buildAnswerThreads(
      [answer("older", 1), answer("newer", 2)],
      [revision("older", "v1", 10), revision("newer", "only", 15), revision("older", "v2", 20)],
    );

    expect(threads.map((thread) => thread.answerId)).toEqual(["newer", "older"]);
    expect(threads[0].piRevisions.map((item) => item.historyId)).toEqual(["only"]);
    expect(threads[1].piRevisions.map((item) => item.historyId)).toEqual(["v2", "v1"]);
  });

  it("keeps ten consecutive questions as ten independently traceable threads", () => {
    const answers = Array.from({ length: 10 }, (_, index) => answer(`question-${index + 1}`, index + 1));
    const revisions = answers.flatMap((item, index) => [
      revision(item.suggestionId, `${item.suggestionId}-v1`, index * 10 + 1),
      revision(item.suggestionId, `${item.suggestionId}-v2`, index * 10 + 2),
    ]);

    const threads = buildAnswerThreads(answers, revisions);

    expect(threads).toHaveLength(10);
    expect(new Set(threads.map((thread) => thread.answerId)).size).toBe(10);
    for (const thread of threads) {
      expect(thread.piRevisions).toHaveLength(2);
      expect(thread.piRevisions.every((item) => item.answerId === thread.answerId)).toBe(true);
      expect(thread.piRevisions[0].createdAtMs).toBeGreaterThan(thread.piRevisions[1].createdAtMs);
    }
  });
});

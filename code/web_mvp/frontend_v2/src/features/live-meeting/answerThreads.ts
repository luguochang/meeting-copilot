import type { CoachHistoryEntry, Suggestion } from "../../domain/events";

export interface AnswerThread {
  answerId: string;
  answer: Suggestion;
  piRevisions: CoachHistoryEntry[];
}

export function buildAnswerThreads(
  suggestions: Suggestion[],
  coachHistory: CoachHistoryEntry[],
): AnswerThread[] {
  const piByAnswer = new Map<string, CoachHistoryEntry[]>();
  for (const item of coachHistory) {
    if (item.promptProfile !== "deep_answer" || !item.answerId) continue;
    const revisions = piByAnswer.get(item.answerId) ?? [];
    revisions.push(item);
    piByAnswer.set(item.answerId, revisions);
  }

  return suggestions
    .filter((item) => item.kind === "answer")
    .filter((item) => item.feedback !== "ignored" && item.feedback !== "false_positive" && item.feedback !== "too_late")
    .sort((left, right) => (
      right.evidenceTranscriptSeq - left.evidenceTranscriptSeq
      || right.updatedAtMs - left.updatedAtMs
    ))
    .map((answer) => ({
      answerId: answer.suggestionId,
      answer,
      piRevisions: [...(piByAnswer.get(answer.suggestionId) ?? [])]
        .sort((left, right) => right.createdAtMs - left.createdAtMs),
    }));
}

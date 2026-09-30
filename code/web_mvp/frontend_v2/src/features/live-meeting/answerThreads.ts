import type { CoachHistoryEntry, FollowUpProjection, Suggestion } from "../../domain/events";

export interface AnswerThread {
  answerId: string;
  answer: Suggestion;
  piRevisions: CoachHistoryEntry[];
}

export function piRevisionIdentity(item: FollowUpProjection): string {
  return item.decisionId ?? `${item.answerId}:${item.revision ?? 0}:${item.question}`;
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

export function completeAnswerText(answer: Suggestion, revision?: FollowUpProjection | null): string {
  const lines = [answer.questionText ? `问题：${answer.questionText}` : "", answer.text || answer.draftText || ""];
  const value = revision?.coachingPackage;
  if (value) {
    lines.push("Pi 补充", value.headline, value.sayThisAddition, `核心判断：${value.coreJudgement}`, `为什么重要：${value.whyItMatters}`);
    for (const [label, items] of [["遗漏重点", value.missingPoints], ["适用约束", value.constraints], ["关键风险", value.risks], ["下一步", value.nextActions]] as const) {
      if (items.length) lines.push(`${label}：`, ...items.map((item) => `- ${item}`));
    }
    for (const item of value.likelyFollowUps) lines.push(`可能追问：${item.question}`, item.answerAngle);
    for (const item of value.evidenceRefs) lines.push(`依据原话：${item.quote}`);
  } else if (revision) lines.push("Pi 补充", revision.sayThis || revision.question);
  return lines.filter(Boolean).join("\n\n");
}

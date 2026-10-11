import type { CoachDecisionProjection, CoachHistoryEntry, FollowUpProjection, Suggestion } from "../../domain/events";

export interface AnswerThread {
  answerId: string;
  answer: Suggestion;
  piRevisions: CoachHistoryEntry[];
}

export function isCurrentAnswer(answer: Suggestion): boolean {
  return answer.kind === "answer" && ["committed", "draft", "validating"].includes(answer.status)
    && !answer.errorClass;
}

export function currentAnswerThread(threads: AnswerThread[]): AnswerThread | undefined {
  const current = threads.filter((thread) => isCurrentAnswer(thread.answer));
  return current.find((thread) => thread.answer.status === "committed") ?? current[0];
}

export function piRevisionIsValid(value: FollowUpProjection | CoachDecisionProjection, nowMs: number): boolean {
  return !(Boolean(value.status && value.status !== "intervention")
    || Boolean(value.lifecycleStatus)
    || value.lifecycleAction === "retract" || value.lifecycleAction === "deprioritize"
    || Boolean(value.supersededBy)
    || (value.validUntil !== undefined && value.validUntil <= nowMs));
}

export function isParticipationProjection(value: FollowUpProjection): boolean {
  return value.analysisKind === "participation" || value.analysisKind === "discussion_update" || value.promptProfile === "participation";
}

export function piRevisionIsCurrent(revision: FollowUpProjection, answerId: string,
  decision: CoachDecisionProjection | null | undefined, nowMs: number): boolean {
  if (revision.answerId !== answerId || !piRevisionIsValid(revision, nowMs)) return false;
  if (!decision) return true;
  if (decision.supersedesDecisionId && decision.supersedesDecisionId === revision.decisionId) return false;
  // A quiet decision for another coach lane must not invalidate a direct answer
  // or an unrelated question's supplement.
  return !((decision.answerId === answerId || Boolean(decision.decisionId
    && decision.decisionId === revision.decisionId)) && !piRevisionIsValid(decision, nowMs));
}

export function piRevisionIdentity(item: FollowUpProjection): string {
  return item.decisionId ?? item.formalAi?.jobId ?? `${item.answerId}:${item.revision ?? 0}:${item.question}`;
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

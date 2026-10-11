import type { CoachRequestOutcome, ContributionIntent } from "../../api/client";

export interface ContributionTerminal { schemaVersion: 1; intent: ContributionIntent; outcome: CoachRequestOutcome; }
const INTENTS = ["ask_question", "compare_options", "formulate_response", "freeform"];
const STATUSES = ["pending", "succeeded", "failed", "timed_out", "no_change"];
const CATEGORIES = ["evidence_rejected", "validation_rejected", "timeout", "provider_unavailable", "execution_failed", "cancelled", "unknown"];
const identity = (value: unknown): value is string => typeof value === "string" && value.length > 0 && value.length <= 200 && !Array.from(value).some((character) => character.charCodeAt(0) < 32);

export function normalizeContributionOutcome(value: unknown, meetingId: string, jobId: string): CoachRequestOutcome | null {
  if (!value || typeof value !== "object") return null;
  const o = value as CoachRequestOutcome;
  if (!identity(o.meetingId) || o.meetingId !== meetingId || !identity(o.jobId) || o.jobId !== jobId || !STATUSES.includes(o.status)
    || !(o.failureCategory === null || CATEGORIES.includes(o.failureCategory)) || typeof o.canRetry !== "boolean"
    || !(o.topicId === null || identity(o.topicId)) || !(o.analysisBasisRevision === null || Number.isSafeInteger(o.analysisBasisRevision) && o.analysisBasisRevision >= 0)
    || (o.topicId === null) !== (o.analysisBasisRevision === null)
    || (["pending", "succeeded", "no_change"].includes(o.status) && (o.failureCategory !== null || o.canRetry))
    || ((o.status === "failed" || o.status === "timed_out") && o.failureCategory === null)
    || o.canRetry !== ["timeout", "provider_unavailable", "execution_failed"].includes(o.failureCategory ?? "")) return null;
  return { meetingId: o.meetingId, jobId: o.jobId, status: o.status, failureCategory: o.failureCategory, canRetry: o.canRetry, topicId: o.topicId, analysisBasisRevision: o.analysisBasisRevision };
}

/** A bounded enum-only record; no raw reasons, material content, model output or retry instructions are persisted. */
export function restoreContributionTerminal(key: string, meetingId: string): ContributionTerminal | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw || raw.length > 1600) return null;
    const value = JSON.parse(raw) as ContributionTerminal;
    const o = normalizeContributionOutcome(value?.outcome, meetingId, value?.outcome?.jobId);
    if (value?.schemaVersion !== 1 || !INTENTS.includes(value.intent) || !o || o.status === "pending") return null;
    return { schemaVersion: 1, intent: value.intent, outcome: o };
  } catch { return null; }
}

export function contributionTerminalMessage(outcome: CoachRequestOutcome): string {
  if (outcome.status === "succeeded") return "本次现场建议已生成，请查看当前建议或本场历史。";
  if (outcome.status === "no_change") return "当前没有新的有用贡献；可以等讨论推进后再试。";
  if (outcome.failureCategory === "evidence_rejected") return "生成建议未通过依据核对，未发布。可以核对当前原文与资料，或等讨论推进后再请求；不会自动重试。";
  if (outcome.failureCategory === "validation_rejected") return "生成建议未通过格式或依据核对，未发布。可以核对当前原文与资料，或等讨论推进后再请求；不会自动重试。";
  if (outcome.status === "timed_out" || outcome.failureCategory === "timeout") return "本次建议未及时生成，可重试；会议原文已保留。";
  if (outcome.failureCategory === "provider_unavailable") return "本次建议未完成，AI 服务暂时不可用。可检查连接或稍后明确重新请求；不会自动重试。";
  if (outcome.failureCategory === "cancelled") return "本次现场分析已取消，未发布新的建议。需要时可基于当前讨论重新请求。";
  if (outcome.failureCategory === "execution_failed") return "本次现场分析未完成。原文与资料已保留，可以稍后明确重新请求；不会自动重试。";
  return "本次现场分析未完成，尚无法确认具体原因。未当作建议发布成功；可以稍后检查状态或基于当前讨论重新请求。";
}

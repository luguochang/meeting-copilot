import type { FollowUpProjection } from "../../domain/events";

interface CoachLifecycleView {
  state: "not_triggered" | "protected_silent" | "timed_out" | "failed" | "stale" | "expired";
  label: string;
  detail: string;
}

export function coachLifecycleView(
  followUp: Pick<FollowUpProjection, "validUntil" | "status" | "decisionReason" | "lifecycleAction">,
  nowMs: number,
): CoachLifecycleView | null {
  if (followUp.lifecycleAction === "retract") {
    return {
      state: "stale",
      label: "建议已撤回",
      detail: followUp.decisionReason ?? "新证据已使上一条建议失效。",
    };
  }
  if (followUp.lifecycleAction === "deprioritize") {
    return {
      state: "stale",
      label: "建议已替换",
      detail: followUp.decisionReason ?? "这条建议已不再是当前最高优先级。",
    };
  }
  if (followUp.validUntil !== undefined && followUp.validUntil <= nowMs) {
    return {
      state: "expired",
      label: "建议已过期",
      detail: followUp.decisionReason ?? "这条建议已超过当前对话的有效时间。",
    };
  }
  if (!followUp.status || followUp.status === "intervention") return null;
  const labels = {
    not_triggered: "本轮未触发",
    protected_silent: "本轮保持静默",
    timed_out: "本轮已超时",
    failed: "本轮分析失败",
    stale: "建议已撤回",
  } as const;
  const details = {
    not_triggered: "当前输入未达到实时教练的介入门槛。",
    protected_silent: "当前没有高置信度且可立即执行的建议。",
    timed_out: "本轮超出实时响应预算，迟到结果不会显示。",
    failed: "本轮未能形成可靠建议，已保持静默。",
    stale: "新证据已使这轮判断失效。",
  } as const;
  return {
    state: followUp.status,
    label: labels[followUp.status],
    detail: followUp.decisionReason ?? details[followUp.status],
  };
}

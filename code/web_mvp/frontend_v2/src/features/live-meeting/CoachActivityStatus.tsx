import type { CoachDecisionProjection, CoachDueWorkItem, RuntimeIndicator } from "../../domain/events";

/** Meeting-level progress, never a claim that the selected answer is still being generated. */
export function CoachActivityStatus({ decision, runtime, dueItems = [], hasAnswer = true }: {
  decision?: CoachDecisionProjection | null;
  runtime?: RuntimeIndicator | null;
  dueItems?: CoachDueWorkItem[];
  hasAnswer?: boolean;
}) {
  const dueCount = new Set(dueItems.filter((item) => item.status === "due").map((item) => item.itemId)).size;
  const status = decision?.status;
  const outcome = status === "timed_out" ? (hasAnswer ? "本轮未及时完成，已有回答保留。" : "本次建议未及时生成，可重试；会议原文已保留。")
    : status === "failed" ? (hasAnswer ? "本轮分析未完成，已有回答保留。" : "本次建议未完成，可重试；会议原文已保留。")
      : status === "protected_silent" ? "本轮保持静默，未形成新的可靠建议。"
        : status === "not_triggered" ? "本轮未达到主动建议的条件。"
          : status === "stale" || decision?.lifecycleAction === "retract" ? "上一轮建议已撤回，请使用仍有效的内容。"
            : runtime?.state === "error" ? (hasAnswer ? "Pi 暂不可用，已有回答保留。" : "现场建议暂时不可用；会议原文仍可查看，可以稍后重试。") : null;
  const busy = runtime?.state === "busy";
  if (!outcome && !busy && !dueCount) return null;
  return <section className="coach-activity-status" aria-label="当前会议的 Pi 状态" role="status" aria-live="polite">
    <strong>当前会议 · Pi 状态</strong>
    {outcome ? <p>{outcome}</p> : null}
    {busy ? <p>{hasAnswer ? "服务端正在处理本场会议的分析；这不表示正在阅读的这条回答仍在生成。" : "正在结合本场讨论分析；文字转写和资料预览继续可用。"}</p> : null}
    {dueCount ? <p>有 {dueCount} 项待后续检查，尚不代表已经开始生成补充。</p> : null}
    {!busy && outcome ? <small>当前没有已报告的执行中任务；不会因这条提示自动重试。</small> : null}
  </section>;
}

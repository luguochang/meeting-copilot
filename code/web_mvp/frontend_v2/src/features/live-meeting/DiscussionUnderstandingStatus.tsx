import type { RuntimeIndicator } from "../../domain/events";
import { hasUnprocessedDiscussion } from "./discussionPresentation";

/** The host's current understanding lane, independent of published suggestions. */
export function DiscussionUnderstandingStatus({ runtime }: { runtime?: RuntimeIndicator | null }) {
  if (!runtime) return null;
  let message: string;
  switch (runtime.decision) {
    case "not_started":
      message = "等待下一轮讨论理解；原文已保留，可随时主动提问。";
      break;
    case "pending":
      message = "本轮理解等待处理，尚未开始执行；原文已保留，继续讨论或主动提问仍可用。";
      break;
    case "running":
      message = "正在理解本轮讨论，尚未形成可靠状态；原文继续保留，主动提问仍可用。";
      break;
    case "retry_wait":
      message = "本轮理解等待后台重试，当前未在执行；原文已保留，继续讨论或主动提问仍可用。";
      break;
    case "succeeded":
      message = "当前讨论状态已更新；这不代表已生成新的建议，仍可主动提问。";
      break;
    case "failed":
      message = runtime.errorClass === "evidence_rejected"
        ? "本轮理解未通过依据核对，结果未采用；原文已保留，继续讨论或主动提问仍可用。"
        : runtime.errorClass === "invalid_output"
          ? "本轮理解结果未通过格式校验，结果未采用；原文已保留，继续讨论或主动提问仍可用。"
          : runtime.errorClass === "provider_timeout"
            ? "本轮理解请求超时；原文已保留，继续讨论或主动提问仍可用。"
            : runtime.errorClass === "provider_unavailable"
              ? "本轮理解的模型服务暂不可用；原文已保留，仍可继续讨论并在服务恢复后主动提问。"
              : "本轮理解未完成；原文已保留，继续讨论或主动提问仍可用。";
      break;
    case "cancelled":
      message = "本轮理解已取消，结果未采用；原文已保留，继续讨论或主动提问仍可用。";
      break;
    default:
      message = "讨论理解状态待确认；原文已保留，继续讨论或主动提问仍可用。";
  }
  // A bounded hint explains this version's lifecycle; it cannot replace the
  // durable job decision or hide a failure returned by the current server.
  switch (runtime.understandingStatus) {
    case "waiting_reanalysis":
      if (runtime.decision === "not_started") message = "校对后等待重新理解；旧结论不再代表当前原文，仍可主动提问。";
      break;
    case "reanalysis_queued":
      if (runtime.decision === "pending" || runtime.decision === "retry_wait") message = "校对后重新理解已排队，尚未开始执行；原文已保留。";
      break;
    case "reanalysis_running":
      if (runtime.decision === "running") message = "正在重新理解校对后的原文，尚未形成可靠的新结论；仍可主动提问。";
      break;
    case "revision_deferred":
      if (runtime.decision === "not_started") message = "原文仍在校对更新，重新理解暂缓；待输入稳定后继续，仍可主动提问。";
      break;
    case "reanalysis_deferred":
      if (runtime.decision === "not_started") message = "重新理解暂缓，等待后台条件；原文已保留，仍可主动提问。";
      break;
    case "partial_review":
      if (runtime.decision === "succeeded") message = "已保存有效话题和事实，部分问题闭环待核对；尚未确认这些问题已回应。";
      break;
  }
  // Labels/details are deliberately not a fallback: future/raw errors must not
  // masquerade as a successful result or leak diagnostic/provider text.
  return <section className="coach-activity-status discussion-understanding-status" aria-label="当前讨论的理解状态" role="status" aria-live="polite">
    <strong>当前讨论 · 理解状态</strong>
    <p>{message}</p>
    {hasUnprocessedDiscussion(runtime) ? <p>有新的讨论尚未理解；请核对已生成的建议是否仍适用。</p> : null}
  </section>;
}

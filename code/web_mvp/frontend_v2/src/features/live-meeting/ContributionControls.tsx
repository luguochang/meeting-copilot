import { useEffect, useRef, useState } from "react";
import { Lightbulb, LoaderCircle } from "lucide-react";
import type { CoachRequestOutcome, ContributionIntent, DiscussionWorkItem } from "../../api/client";
import { ApiError } from "../../api/client";
import type { NowRailProps } from "./nowRailTypes";
import { hasUnprocessedDiscussion, hasUnverifiedHistoricalTopic } from "./discussionPresentation";
import { currentAttentionPresentation } from "./currentAttention";
import { contributionTerminalMessage, normalizeContributionOutcome, restoreContributionTerminal, type ContributionTerminal } from "./contributionRequestState";

const ACTIONS: { intent: ContributionIntent; label: string }[] = [
  { intent: "ask_question", label: "我可以问什么" },
  { intent: "compare_options", label: "还有什么方案" },
  { intent: "formulate_response", label: "帮我组织回应" },
];
type PendingContribution = { jobId: string; label: string; intent?: ContributionIntent; topicId?: string | null; analysisBasisRevision?: number | null };
function validIdentity(value: unknown): value is string {
  return typeof value === "string" && value.length > 0 && value.length <= 200 && !Array.from(value).some((character) => character.charCodeAt(0) < 32);
}
function restore(key: string): PendingContribution | null {
  try {
    const raw = sessionStorage.getItem(key);
    if (raw && raw.length > 1600) return null;
    const value = JSON.parse(raw ?? "null");
    const validTopic = validIdentity(value?.topicId);
    const validBasis = Number.isSafeInteger(value?.analysisBasisRevision) && value.analysisBasisRevision >= 0;
    return value && validIdentity(value.jobId) && ACTIONS.some((action) => action.label === value.label)
      ? { jobId: value.jobId, label: value.label, intent: ACTIONS.find((action) => action.label === value.label)?.intent,
        topicId: validTopic && validBasis ? value.topicId : null,
        analysisBasisRevision: validTopic && validBasis ? value.analysisBasisRevision : null } : null;
  } catch { return null; }
}

/** Deliberate help is available before an automatic answer ever exists. */
export function ContributionControls({ viewStateKey, currentTopic, discussionRuntime, onContribute, onOpenAsk, onCoachRequestStatus, onCoachRequestOutcome, onReadDiscussion, onUpdateDiscussion, followUp }: Pick<NowRailProps,
  "viewStateKey" | "currentTopic" | "discussionRuntime" | "onContribute" | "onOpenAsk" | "onCoachRequestStatus" | "onCoachRequestOutcome" | "onReadDiscussion" | "onUpdateDiscussion"> & { followUp?: NowRailProps["followUp"] }) {
  const meetingId = viewStateKey ?? "current";
  const hasDiscussionReader = Boolean(onReadDiscussion);
  const key = `talktrace-contribution:${viewStateKey ?? "current"}`;
  const terminalKey = `${key}:terminal-v1`;
  const [pending, setPending] = useState(() => restore(key));
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [discussion, setDiscussion] = useState<DiscussionWorkItem | null>(null);
  const discussionKey = useRef(key);
  const [discussionReady, setDiscussionReady] = useState(!onReadDiscussion);
  const [terminal, setTerminal] = useState(() => restoreContributionTerminal(terminalKey, meetingId));
  const [terminalVerified, setTerminalVerified] = useState(!onCoachRequestOutcome);
  const [editingTopic, setEditingTopic] = useState(false); const [topicDraft, setTopicDraft] = useState(""); const [updatingTopic, setUpdatingTopic] = useState(false);
  const scope = useRef<object>({});
  const submitting = useRef(false);
  const rawBasis = discussion?.analysis_basis_revision ?? discussion?.context.analysis_basis_revision;
  const knownBasis = typeof rawBasis === "number" && Number.isSafeInteger(rawBasis) && rawBasis >= 0 ? rawBasis : null;
  const currentBinding = useRef<{ topicId: string | null; analysisBasisRevision: number | null }>({ topicId: null, analysisBasisRevision: null });
  currentBinding.current = { topicId: knownBasis !== null ? discussion?.id ?? null : null, analysisBasisRevision: knownBasis };
  useEffect(() => {
    scope.current = {};
    setPending(restore(key)); setSending(false); setNotice(""); setError(""); setDiscussion(null); setDiscussionReady(!hasDiscussionReader); setTerminal(restoreContributionTerminal(terminalKey, meetingId)); setTerminalVerified(false); setEditingTopic(false); submitting.current = false;
    return () => { scope.current = {}; };
  }, [key, terminalKey, meetingId, hasDiscussionReader]);
  useEffect(() => {
    if (!onReadDiscussion) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const read = async () => {
      try { const value = await onReadDiscussion(controller.signal); if (!controller.signal.aborted) { discussionKey.current = key; setDiscussion(value); setDiscussionReady(true); } }
      catch { /* The inference preview remains useful during a read outage. */ }
      if (!controller.signal.aborted) timer = setTimeout(() => void read(), 10_000);
    };
    void read(); return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [onReadDiscussion, key, pending?.jobId, followUp?.decisionId]);
  useEffect(() => {
    if (!terminal || !discussionReady) return;
    const o = terminal.outcome;
    if (o.topicId !== currentBinding.current.topicId || o.analysisBasisRevision !== currentBinding.current.analysisBasisRevision) {
      try { sessionStorage.removeItem(terminalKey); } catch { /* Optional UI persistence. */ }
      setTerminal(null);
    }
  }, [terminal, discussionReady, discussion?.id, knownBasis, terminalKey]);
  useEffect(() => {
    if (!terminal || terminalVerified || !onCoachRequestOutcome || !discussionReady || terminal.outcome.topicId !== currentBinding.current.topicId || terminal.outcome.analysisBasisRevision !== currentBinding.current.analysisBasisRevision) return;
    const controller = new AbortController();
    const verify = async () => {
      try {
        const outcome = normalizeContributionOutcome(await onCoachRequestOutcome(terminal.outcome.jobId, controller.signal), meetingId, terminal.outcome.jobId);
        if (controller.signal.aborted) return;
        if (!outcome) {
          setTerminal(null); try { sessionStorage.removeItem(terminalKey); } catch { /* Optional UI persistence. */ }
          setNotice("未能确认本次请求的结果身份；可以基于当前讨论重新请求，不会自动重试。"); return;
        }
        if (outcome.status === "pending") {
          setTerminal(null); try { sessionStorage.removeItem(terminalKey); } catch { /* Optional UI persistence. */ }
          const entry = { jobId: outcome.jobId, label: ACTIONS.find((action) => action.intent === terminal.intent)?.label ?? ACTIONS[0].label, intent: terminal.intent, topicId: outcome.topicId, analysisBasisRevision: outcome.analysisBasisRevision };
          setPending(entry); try { sessionStorage.setItem(key, JSON.stringify(entry)); } catch { /* Optional UI persistence. */ }
          return;
        }
        const value: ContributionTerminal = { ...terminal, outcome };
        setTerminal(value); setTerminalVerified(true); setNotice("");
        try { sessionStorage.setItem(terminalKey, JSON.stringify(value)); } catch { /* Optional UI persistence. */ }
      } catch {
        if (!controller.signal.aborted) setNotice("暂时无法恢复本次请求结果，可稍后刷新查看；不会重复提交分析。");
      }
    };
    void verify(); return () => controller.abort();
  }, [terminal, terminalVerified, onCoachRequestOutcome, discussionReady, discussion?.id, knownBasis, meetingId, key, terminalKey]);
  useEffect(() => {
    if (!pending || !onCoachRequestStatus && !onCoachRequestOutcome) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        let rawOutcome: CoachRequestOutcome;
        if (onCoachRequestOutcome) rawOutcome = await onCoachRequestOutcome(pending.jobId, controller.signal);
        else {
          const status = await onCoachRequestStatus!(pending.jobId, controller.signal);
          rawOutcome = { meetingId, jobId: pending.jobId, status, failureCategory: status === "timed_out" ? "timeout" : status === "failed" ? "unknown" : null, canRetry: status === "timed_out", topicId: pending.topicId ?? null, analysisBasisRevision: pending.analysisBasisRevision ?? null };
        }
        if (controller.signal.aborted) return;
        const outcome = normalizeContributionOutcome(rawOutcome, meetingId, pending.jobId);
        if (!outcome) throw new Error("请求结果身份未确认");
        const status = outcome.status;
        if (status !== "pending") {
          try { sessionStorage.removeItem(key); } catch { /* Optional UI persistence. */ }
          setPending(null);
          const safeOutcome = outcome;
          const value: ContributionTerminal = { schemaVersion: 1, intent: pending.intent ?? "ask_question", outcome: safeOutcome };
          if (discussionReady && (safeOutcome.topicId !== currentBinding.current.topicId || safeOutcome.analysisBasisRevision !== currentBinding.current.analysisBasisRevision)) {
            setNotice("讨论已更新，本次请求已结束；可以基于当前讨论重新请求。");
          } else {
            setTerminal(value); setTerminalVerified(true);
            try { sessionStorage.setItem(terminalKey, JSON.stringify(value)); } catch { /* Optional UI persistence. */ }
          }
          return;
        }
        setNotice("");
      } catch {
        if (controller.signal.aborted) return;
        setNotice("暂时无法读取进度，正在重新连接；不会重复提交分析。");
      }
      timer = setTimeout(() => void poll(), 1500);
    };
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [pending, onCoachRequestStatus, onCoachRequestOutcome, key, terminalKey, meetingId, discussionReady]);
  const submit = async (action: typeof ACTIONS[number]) => {
    if (!onContribute || submitting.current || pending) return;
    submitting.current = true; setSending(true); setError(""); setNotice("");
    setTerminal(null); setTerminalVerified(true); try { sessionStorage.removeItem(terminalKey); } catch { /* Optional UI persistence. */ }
    const owner = scope.current;
    const binding = { ...currentBinding.current };
    try {
      const jobId = await onContribute(action.intent);
      if (!jobId) throw new Error("服务未返回任务编号，无法确认建议进度。");
      const entry = { jobId, label: action.label, intent: action.intent, ...binding };
      try { sessionStorage.setItem(key, JSON.stringify(entry)); } catch { /* Optional UI persistence. */ }
      if (scope.current === owner) setPending(entry);
    } catch (reason) {
      if (scope.current === owner) setError(reason instanceof Error ? reason.message : "提交失败，请重试。");
    } finally {
      if (scope.current === owner) { submitting.current = false; setSending(false); }
    }
  };
  const update = async (resolved: boolean) => {
    if (!discussion || !onUpdateDiscussion || updatingTopic) return;
    const owner = scope.current; setUpdatingTopic(true); setError("");
    try {
      const point = followUp?.sayThis ?? followUp?.question ?? discussion.title;
      const previous = Array.isArray(discussion.context.responded_points) ? discussion.context.responded_points : [];
      const changes = resolved ? { context: { responded_points: [...previous, point].slice(-8) } } : { title: topicDraft.trim(), context: { current_problem: topicDraft.trim(), inferred: false } };
      if (!resolved && !topicDraft.trim()) throw new Error("请填写当前真正讨论的事项。");
      const value = await onUpdateDiscussion(discussion, changes);
      if (scope.current !== owner) return;
      setDiscussion(value); setEditingTopic(false); setNotice(resolved ? "已记录这个点已经讨论过，后续分析会据此收束。" : "当前议题已纠正，后续分析使用新的理解。");
    } catch (reason) {
      if (scope.current !== owner) return;
      if (reason instanceof ApiError && reason.status === 409 && onReadDiscussion) {
        try { const latest = await onReadDiscussion(); if (scope.current === owner) { setDiscussion(latest); setError("讨论已有新进展，已刷新当前议题。请核对后重新提交。"); } }
        catch { if (scope.current === owner) setError("讨论已有新进展，请稍后重新读取议题再提交。"); }
      } else setError(reason instanceof Error ? reason.message : "议题更新失败，请重试。");
    }
    finally { if (scope.current === owner) setUpdatingTopic(false); }
  };
  const visibleDiscussion = discussionKey.current === key ? discussion : null;
  const topicText = visibleDiscussion?.title ?? currentTopic?.text;
  const visibleTerminal = !pending && !sending && discussionReady && (terminalVerified || !onCoachRequestOutcome)
    && terminal?.outcome.meetingId === meetingId && terminal.outcome.topicId === currentBinding.current.topicId && terminal.outcome.analysisBasisRevision === currentBinding.current.analysisBasisRevision ? terminal : null;
  const terminalError = visibleTerminal?.outcome.status === "failed" || visibleTerminal?.outcome.status === "timed_out";
  // Stored and projected titles can differ while referring to the same retained
  // discussion. Text equality cannot certify that withdrawn understanding is current.
  const historicalTopic = visibleDiscussion?.context.inferred !== false
    && (!visibleDiscussion || visibleDiscussion.id === (discussionRuntime?.topicId ?? currentTopic?.id))
    && hasUnverifiedHistoricalTopic(discussionRuntime, currentTopic?.lastUpdatedSeq);
  const { attention, hasSnapshot } = currentAttentionPresentation(visibleDiscussion, meetingId, discussionRuntime);
  const retainedTopic = hasSnapshot && (!attention || ["interlude", "unknown", "uncertain"].includes(attention.relation));
  const topicLabel = retainedTopic ? "已记录的议题" : historicalTopic ? "上次记录的话题" : hasUnprocessedDiscussion(discussionRuntime) ? "最近已理解的话题" : "正在讨论";
  return <section className="contribution-controls" aria-label="现场贡献">
    <div className="contribution-controls-heading"><strong>当场用得上的建议</strong>{onOpenAsk ? <button type="button" className="coach-text-button" onClick={onOpenAsk}>自己提问</button> : null}</div>
    <div className="contribution-actions" role="group" aria-label="请求现场建议">{ACTIONS.map((action) => <button key={action.intent} type="button" className="secondary-button" disabled={!onContribute || sending || Boolean(pending)} onClick={() => void submit(action)}>{action.label}</button>)}</div>
    <details className="contribution-topic-details">
    <summary className="contribution-topic" title={topicText ? `${topicLabel}：${topicText}` : undefined}><Lightbulb size={15} /><span>{topicText ? `${topicLabel}：${topicText}` : "听到讨论后，随时帮你找到一个值得提出的观点。"}</span><small>议题与判断</small></summary>
    {attention ? <div role="region" aria-label="最近交流与参与判断">
      <p><strong>最近交流</strong><small>转写原话</small></p>
      {attention.observations.map((observation, index) => <blockquote key={index}>{observation}</blockquote>)}
      <p>{attention.status === "present" ? "有可参与的讨论点，可主动请求建议。" : attention.status === "none" ? "暂时无需补充，继续聆听。" : "参与时机尚不确定，继续聆听。"}</p>
      <p><small>助手判断</small><span>{attention.reason}</span></p>
    </div> : hasSnapshot ? <p role="status">当前交流尚未确认，等待重新理解；仍可主动请求建议。</p> : null}
    {discussion && onUpdateDiscussion ? <div className="contribution-topic-actions">{editingTopic ? <><input aria-label="纠正当前议题" maxLength={200} value={topicDraft} onChange={(event) => setTopicDraft(event.target.value)} /><button type="button" disabled={updatingTopic} onClick={() => void update(false)}>保存议题</button><button type="button" disabled={updatingTopic} onClick={() => setEditingTopic(false)}>取消</button></> : <><button type="button" onClick={() => { setTopicDraft(topicText ?? ""); setEditingTopic(true); }}>不是当前话题？纠正</button>{followUp?.question && followUp.topicId === discussion.id ? <button type="button" disabled={updatingTopic} onClick={() => void update(true)}>这个已经讨论过</button> : null}</>}</div> : null}
    </details>
    {sending || pending ? <p role="status"><LoaderCircle size={14} className="spin" />{sending ? "正在提交现场分析…" : notice || `${pending?.label} · 正在结合当前讨论分析`}</p> : notice ? <p role="status">{notice}</p> : null}
    {terminal && !visibleTerminal && !pending && !sending && !notice && onCoachRequestOutcome ? <p role="status">正在核对上次现场请求结果；不会重新提交分析。</p> : null}
    {error ? <p role="alert" className="inline-error">{error}</p> : null}
    {visibleTerminal ? <p role={terminalError ? "alert" : "status"} aria-label="本次现场请求结果" className={terminalError ? "inline-error" : undefined}>{ACTIONS.find((action) => action.intent === visibleTerminal.intent)?.label ?? "现场分析"}：{contributionTerminalMessage(visibleTerminal.outcome)}</p> : null}
  </section>;
}

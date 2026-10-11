import { SourceEvidenceList } from "../knowledge-library/SourceEvidenceList";
import { ArrowDown, ArrowLeft, ArrowUp, Copy, History, LoaderCircle, Quote, RotateCcw, Sparkles, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { CoachHistoryEntry, FollowUpProjection, Suggestion } from "../../domain/events";
import { buildAnswerThreads, completeAnswerText, currentAnswerThread, isCurrentAnswer, piRevisionIdentity, piRevisionIsCurrent } from "./answerThreads";
import { PiSupplement } from "./PiSupplement";
import type { NowRailProps } from "./nowRailTypes";
import { useCoachRequests, type CoachRequest } from "./useCoachRequests";
import "./coachReader.css";

interface ReaderState {
  answerId: string | null;
  positions: Record<string, number>;
  drafts: Record<string, string>;
}

function readState(key: string): ReaderState {
  try {
    const value = JSON.parse(sessionStorage.getItem(key) ?? "null");
    return { answerId: typeof value?.answerId === "string" ? value.answerId : null,
      positions: Object.fromEntries(Object.entries(value?.positions ?? {}).filter(([, position]) => typeof position === "number" && position >= 0)),
      drafts: Object.fromEntries(Object.entries(value?.drafts ?? {}).filter(([, draft]) => typeof draft === "string")) } as ReaderState;
  } catch { return { answerId: null, positions: {}, drafts: {} }; }
}

const EMPTY_HISTORY: CoachHistoryEntry[] = [];
const QUICK_ADJUSTMENTS = ["更具体，结合当前会议给出可执行建议", "补充关键风险与适用边界", "给我一句可以直接说出口的回答"];

function timeLabel(time: number) {
  return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(time));
}

function answerState(answer: Suggestion): string {
  if (expiredAnswer(answer)) return answer.errorClass === "transcript_revised" ? "已过期 · 原文修订" : "已过期";
  if (answer.status === "rejected") return "生成失败";
  if (answer.status === "draft" || answer.status === "validating") return "生成中";
  return "已保存";
}

function expiredAnswer(answer: Suggestion): boolean {
  return answer.status === "superseded" || answer.errorClass === "transcript_revised";
}

function replacementThreads(answerId: string, threads: ReturnType<typeof buildAnswerThreads>) {
  const related = new Set([answerId]);
  // Interrupted refreshes can form a chain through another expired draft.
  // Follow stable answer IDs rather than guessing from the question wording.
  for (let pass = 0; pass < threads.length; pass += 1) {
    let changed = false;
    for (const thread of threads) {
      if (thread.answer.supersedesSuggestionId && related.has(thread.answer.supersedesSuggestionId)
        && !related.has(thread.answerId)) {
        related.add(thread.answerId);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return threads.filter((thread) => thread.answerId !== answerId && related.has(thread.answerId));
}

function answerError(answer: Suggestion): string {
  if (answer.errorClass === "provider_not_configured") return "回答模型尚未连接，请检查 AI 设置。";
  return "本次回答未完成，已有内容已保留；左侧转写和录音不受影响。";
}

type Revision = FollowUpProjection & { createdAtMs?: number };
type ThreadItem = { id: string; time: number; revision?: Revision; request?: CoachRequest };

export function AnswerCoachReader({ viewStateKey, suggestions, followUp, coachHistory = EMPTY_HISTORY,
  historyOnly = false, coachRuntime, coachDecision, knowledgeApi, sourceMeetingId, onKnowledgeSource, onEvidence, onMessage, onCoachRefine, onCoachRequestStatus, children,
  startInHistory = false, onHistoryReadingChange,
}: NowRailProps & { children?: ReactNode; startInHistory?: boolean; onHistoryReadingChange?(reading: boolean): void }) {
  const stateKey = `meeting-copilot-coach-reader:${viewStateKey ?? "current"}`;
  const [initial] = useState(() => readState(stateKey));
  const threads = useMemo(() => buildAnswerThreads(suggestions, coachHistory), [suggestions, coachHistory]);
  const latest = currentAnswerThread(threads);
  const [selectedId, setSelectedId] = useState<string | null>((historyOnly ? initial.answerId : null) ?? latest?.answerId ?? threads[0]?.answerId ?? null);
  const [readingHistory, setReadingHistory] = useState(historyOnly || startInHistory);
  const [holdSelection, setHoldSelection] = useState(false);
  const requested = threads.find((item) => item.answerId === selectedId);
  const selected = (readingHistory || (holdSelection && requested && isCurrentAnswer(requested.answer)) ? requested : latest) ?? latest ?? threads[0];
  const answer = selected.answer;
  const answerId = selected.answerId;
  const expired = expiredAnswer(answer);
  const replacements = useMemo(() => replacementThreads(answerId, threads), [answerId, threads]);
  const replacement = replacements.find((item) => item.answer.status === "committed" && isCurrentAnswer(item.answer));
  const replacementBusy = replacements.some((item) => ["draft", "validating"].includes(item.answer.status));
  const replacementFailed = replacements.some((item) => item.answer.status === "rejected");
  const [historyOpen, setHistoryOpen] = useState(startInHistory);
  const [historyQuery, setHistoryQuery] = useState("");
  const [drafts, setDrafts] = useState(initial.drafts);
  const positions = useRef(initial.positions);
  const reader = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const historySearch = useRef<HTMLInputElement>(null);
  const [quickOpen, setQuickOpen] = useState(false);
  const [newSupplement, setNewSupplement] = useState<string | null>(null);
  const [acknowledgedRequests, setAcknowledgedRequests] = useState<Set<string>>(new Set());
  const followRequest = useRef<string | null>(null);
  const itemNodes = useRef(new Map<string, HTMLElement>());
  const previousRevisions = useRef<{ answerId: string; ids: string[] }>({ answerId: "", ids: [] });
  const { requests, pending, connectionNotice, submit } = useCoachRequests({ viewStateKey, onCoachRefine, onCoachRequestStatus });
  const backgroundResult = requests.find((item) => item.answerId !== answerId && !["sending", "pending"].includes(item.status) && !acknowledgedRequests.has(item.id));
  const draft = drafts[answerId] ?? "";
  const answerText = answer.text || answer.draftText || "";
  const selectedIndex = threads.findIndex((item) => item.answerId === answerId);
  const previous = threads[selectedIndex + 1];
  const newerAnswers = threads.slice(0, selectedIndex).filter((item) => item.answer.status === "committed" && isCurrentAnswer(item.answer)
    && !(expired && replacements.some((revision) => revision.answerId === item.answerId)));
  const incoming = threads[0]?.answer;
  const incomingBusy = incoming.suggestionId !== answerId && ["draft", "validating"].includes(incoming.status);
  const historical = historyOnly || readingHistory || selected.answerId !== latest?.answerId;
  const [, refreshExpiration] = useState(0);
  const nowMs = Date.now();

  useEffect(() => {
    const deadlines = [...selected.piRevisions, followUp, coachDecision]
      .flatMap((item) => item?.validUntil !== undefined && item.validUntil > Date.now() ? [item.validUntil] : []);
    if (!deadlines.length) return;
    const timer = window.setTimeout(() => refreshExpiration((value) => value + 1), Math.min(Math.min(...deadlines) - Date.now() + 25, 2_147_483_647));
    return () => window.clearTimeout(timer);
  }, [selected.piRevisions, followUp, coachDecision, nowMs]);

  const revisions = useMemo(() => {
    const items: Revision[] = [...selected.piRevisions];
    if (followUp?.answerId === answerId && followUp.promptProfile === "deep_answer") items.push(followUp);
    return items.filter((item, index) => item.answerId === answerId && item.promptProfile === "deep_answer"
      && item.formalAi?.source === "llm_first" && item.formalAi.llmCalled
      && (historical || piRevisionIsCurrent(item, answerId, coachDecision, nowMs))
      && items.findIndex((other) => piRevisionIdentity(other) === piRevisionIdentity(item)) === index)
      .sort((left, right) => (left.revision ?? 0) - (right.revision ?? 0) || (left.createdAtMs ?? 0) - (right.createdAtMs ?? 0));
  }, [answerId, selected.piRevisions, followUp, historical, coachDecision, nowMs]);
  const threadItems = useMemo(() => {
    const unpaired = new Set(revisions);
    const items: ThreadItem[] = requests.filter((item) => item.answerId === answerId).map((request) => {
      const revision = revisions.find((item) => unpaired.has(item) && (
        Boolean(request.jobId) && item.formalAi?.jobId === request.jobId
        || item.triggerType === "user_request" && item.userRequest === request.request
          && (item.createdAtMs ?? Infinity) >= request.createdAtMs
      ));
      if (revision) unpaired.delete(revision);
      return { id: request.id, time: request.createdAtMs, request, revision };
    });
    const latestKnownTime = Math.max(answer.createdAtMs, ...revisions.map((item) => item.createdAtMs ?? 0));
    for (const revision of unpaired) items.push({ id: piRevisionIdentity(revision),
      time: revision.createdAtMs ?? latestKnownTime + (revision.revision ?? 1), revision });
    return items.sort((left, right) => left.time - right.time);
  }, [requests, revisions, answerId, answer.createdAtMs]);

  useEffect(() => {
    if (selectedId !== answerId) {
      setSelectedId(answerId);
      if (!readingHistory) setHoldSelection(false);
    }
  }, [answerId, selectedId, readingHistory]);
  useEffect(() => {
    const persist = () => {
      try { sessionStorage.setItem(stateKey, JSON.stringify({ answerId, drafts, positions: positions.current })); } catch { /* Optional UI persistence. */ }
    };
    persist();
    return persist;
  }, [answerId, drafts, stateKey]);
  useLayoutEffect(() => {
    if (!historyOpen && reader.current) reader.current.scrollTop = positions.current[answerId] ?? 0;
  }, [answerId, historyOpen]);

  const scrollToItem = (id: string) => {
    const node = itemNodes.current.get(id);
    const container = reader.current;
    if (!node || !container) return;
    const top = node.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - 16;
    container.scrollTo?.({ top: Math.max(0, top), behavior: "auto" });
  };
  useLayoutEffect(() => {
    if (historyOpen || !followRequest.current) return;
    const item = threadItems.find((entry) => entry.id === followRequest.current);
    if (item) scrollToItem(item.id);
  }, [threadItems, historyOpen]);
  useEffect(() => {
    const ids = revisions.map(piRevisionIdentity);
    const previousValue = previousRevisions.current;
    if (previousValue.answerId === answerId) {
      const fresh = ids.filter((id) => !previousValue.ids.includes(id));
      if (fresh.length && !followRequest.current) setNewSupplement(fresh[0]);
    } else setNewSupplement(null);
    if (newSupplement && !ids.includes(newSupplement)) setNewSupplement(null);
    previousRevisions.current = { answerId, ids };
  }, [answerId, revisions, newSupplement]);
  useEffect(() => {
    if (historyOpen) historySearch.current?.focus();
  }, [historyOpen]);
  useEffect(() => {
    if (historyOpen) return;
    const visibleResults = requests.filter((item) => item.answerId === answerId && !["sending", "pending"].includes(item.status));
    if (visibleResults.length) setAcknowledgedRequests((current) => {
      if (visibleResults.every((item) => current.has(item.id))) return current;
      return new Set([...current, ...visibleResults.map((item) => item.id)]);
    });
  }, [answerId, requests, historyOpen]);

  const selectAnswer = (id: string) => {
    if (reader.current && !historyOpen) positions.current[answerId] = reader.current.scrollTop;
    followRequest.current = null;
    setNewSupplement(null);
    setSelectedId(id);
    const history = id !== latest?.answerId;
    setReadingHistory(history);
    setHoldSelection(false);
    onHistoryReadingChange?.(history);
    setHistoryOpen(false);
    setQuickOpen(false);
    requestAnimationFrame(() => reader.current?.focus({ preventScroll: true }));
  };
  const closeHistory = () => {
    if (!latest && !historyOnly) { onHistoryReadingChange?.(false); return; }
    setHistoryOpen(false); historyButton.current?.focus();
  };
  const openHistory = () => {
    if (reader.current) positions.current[answerId] = reader.current.scrollTop;
    followRequest.current = null;
    setHoldSelection(true);
    setReadingHistory(true);
    onHistoryReadingChange?.(true);
    setHistoryOpen(true);
  };
  const requestRefinement = (request: string, retry?: CoachRequest) => {
    const targetId = retry?.answerId ?? answerId;
    const target = threads.find((thread) => thread.answerId === targetId)?.answer;
    if (historyOnly || !target || target.status !== "committed" || !isCurrentAnswer(target)) {
      onMessage("此回答已过期或尚未完成，请先查看有效回答再调整。");
      return;
    }
    const id = submit(targetId, request, retry?.id);
    if (!id) return;
    followRequest.current = id;
    setHoldSelection(true);
    setQuickOpen(false);
    if (!retry) setDrafts((current) => ({ ...current, [answerId]: "" }));
  };
  const copy = async () => {
    const base = completeAnswerText(answer);
    const additions = revisions.map((item) => [expired || !piRevisionIsCurrent(item, answerId, coachDecision, nowMs)
      ? "【此 Pi 补充已失效，仅供历史核对】" : "", completeAnswerText(answer, item).slice(base.length).trim()].filter(Boolean).join("\n"));
    const warning = expired ? "【此回答及其补充已过期，仅供修订前的历史核对】" : historical ? "【历史问题的回答及补充，请核对当前语境】" : "";
    try { await navigator.clipboard.writeText([warning, base, ...additions].filter(Boolean).join("\n\n")); onMessage(expired ? "修订前记录已复制（包含过期标记）" : "完整建议已复制（包含本题全部补充）"); }
    catch { onMessage("复制失败，请重试或选择文字复制"); }
  };
  const matching = threads.filter((thread) => [thread.answer.questionText, thread.answer.text, thread.answer.draftText,
    ...thread.piRevisions.map((item) => JSON.stringify(item.coachingPackage ?? item.question))].join(" ").toLocaleLowerCase().includes(historyQuery.trim().toLocaleLowerCase()));
  const canRefine = onCoachRefine && !historyOnly && answer.status === "committed" && isCurrentAnswer(answer);

  return <aside className="coach-reader" aria-label="当前会议重点">
    <header className="coach-reader-heading">
      <Sparkles size={17} aria-hidden="true" />
      <h2>{historyOnly ? "会中问答与 Pi 补充" : "AI 实时教练"}</h2>
      <span className="coach-reader-mode">{historyOpen ? "本场记录" : expired ? "过期记录" : historical ? "历史记录" : "当前建议"}</span>
      <button ref={historyButton} type="button" className="coach-text-button" onClick={historyOpen ? closeHistory : openHistory} aria-expanded={historyOpen}>
        {historyOpen ? <X size={15} /> : <History size={15} />}{historyOpen ? "返回回答" : `历史 ${threads.length}`}
      </button>
      {historical && !historyOnly ? <button type="button" className="coach-text-button" onClick={() => {
        if (latest) selectAnswer(latest.answerId); else onHistoryReadingChange?.(false);
      }}>返回当前状态</button> : null}
    </header>
    <div className="coach-reader-body" hidden={historyOpen}>
      {expired ? <section className="coach-new-answer" aria-label="回答修订状态">
        <span><strong role="status">{answer.errorClass === "transcript_revised" ? "原文已修订，此回答已过期。" : "此回答已过期。"}</strong><span>旧正文及 Pi 补充保留供核对，请以最新依据为准。</span></span>
        {replacement ? <button type="button" className="coach-text-button" onClick={() => selectAnswer(replacement.answerId)}>查看更新回答 <ArrowDown size={14} /></button>
          : <span role={replacementFailed && !replacementBusy ? "alert" : "status"}>{replacementBusy ? "更新回答正在生成" : replacementFailed ? "更新回答未完成，请核对最新文字稿" : "暂无可用的更新回答"}</span>}
      </section> : null}
      {backgroundResult ? <button type="button" className="coach-background-result" onClick={() => {
        selectAnswer(backgroundResult.answerId);
        followRequest.current = backgroundResult.id;
      }}>{["failed", "timed_out"].includes(backgroundResult.status) ? "另一问题的补充未完成" : backgroundResult.status === "no_change" ? "另一问题的分析已结束，无新增补充" : "另一问题的补充已完成"} · 查看 <ArrowDown size={13} /></button> : null}
      {newerAnswers.length ? <button type="button" className="coach-new-answer" onClick={() => selectAnswer(newerAnswers[0].answerId)}>
        <span><strong>{newerAnswers.length} 条新建议</strong><span>{newerAnswers[0].answer.questionText ?? "最新会议建议"}</span></span><span>查看最新 <ArrowDown size={14} /></span>
      </button> : incomingBusy && !expired ? <p className="coach-inline-status" role="status"><LoaderCircle size={13} className="spin" />下一条回应正在生成，当前建议保留。</p> : null}
      <div ref={reader} className="coach-reader-scroll" role="region" aria-label="建议阅读区" tabIndex={0}
        onScroll={() => { if (reader.current) { const top = reader.current.scrollTop; if (top < (positions.current[answerId] ?? 0) - 3) followRequest.current = null; positions.current[answerId] = top; } }}
        onWheel={(event) => { if (event.deltaY < 0) followRequest.current = null; }}
        onTouchMove={() => { followRequest.current = null; }}
        onKeyDown={(event) => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) followRequest.current = null; }}
        onMouseUp={() => { if (window.getSelection()?.toString()) followRequest.current = null; }}>
        {previous ? <button className="coach-previous" type="button" onClick={() => selectAnswer(previous.answerId)}><span>上一条 · {timeLabel(previous.answer.createdAtMs)}</span><span>{previous.answer.questionText ?? "会议建议"}</span><ArrowLeft size={13} /></button> : null}
        <article className="coach-answer" data-testid="answer-copilot-card" data-ui-render-card={!historical && !expired ? "answer" : undefined} data-ui-render-job-id={!historical && !expired ? answer.jobId ?? undefined : undefined}>
          <div className="coach-answer-meta"><time>{timeLabel(answer.createdAtMs)}</time><span>{historical ? "历史问题" : answer.questionText === "当前讨论重点" ? "讨论重点" : "当前问题"}</span>{["draft", "validating"].includes(answer.status) ? <span role="status">生成中</span> : null}
            {expired ? <span>已过期</span> : answer.supersedesSuggestionId ? <span>修订后的回答</span> : null}
            {revisions.length ? <button type="button" className="coach-text-button" onClick={() => { const item = threadItems.find((entry) => entry.revision); if (item) scrollToItem(item.id); }}>Pi 补充 {revisions.length} <ArrowDown size={12} /></button> : null}
          </div>
          <h3 className="coach-question">{answer.questionText ?? "正在确认对方的问题…"}</h3>
          {answerText ? <div className="coach-answer-text"><ReactMarkdown>{answerText}</ReactMarkdown></div> : expired ? <p className="coach-inline-status">修订前的回答尚未生成完整内容。</p> : !answer.errorClass && answer.status !== "rejected" ? <p className="coach-inline-status" role="status">正在生成可直接说出口的回答…</p> : null}
          {!expired && (answer.errorClass || answer.status === "rejected") ? <p className="coach-inline-error" role="alert">{answerError(answer)}</p> : null}
          <div className="coach-answer-actions">
            {answerText ? <button className="coach-text-button" type="button" onClick={() => void copy()}><Copy size={14} />复制完整建议</button> : null}
            {answer.sourceEvidence?.length ? <SourceEvidenceList evidence={answer.sourceEvidence} api={knowledgeApi} meetingId={sourceMeetingId ?? answer.meetingId} onTranscript={onEvidence} onDocument={onKnowledgeSource} stale={expired} /> : null}
            <button className="coach-text-button" type="button" onClick={() => onEvidence(answer.evidenceSegmentId)}><Quote size={14} />查看原话</button>
          </div>
          {threadItems.map((item) => <section key={item.id} ref={(node) => { if (node) itemNodes.current.set(item.id, node); else itemNodes.current.delete(item.id); }} className="coach-thread-item" aria-label={item.request ? "调整记录" : "Pi 补充"}
            data-ui-render-card={item.revision && !historical ? "coach" : undefined} data-ui-render-job-id={!historical ? item.revision?.formalAi?.jobId ?? undefined : undefined}>
            <div className="coach-thread-meta"><Sparkles size={14} /><span>{item.request || item.revision?.triggerType === "user_request" ? "根据你的调整补充" : "Pi 补充"}</span>{item.revision?.revision ? <small>第 {item.revision.revision} 次</small> : null}</div>
            {item.revision && (expired || !piRevisionIsCurrent(item.revision, answerId, coachDecision, nowMs)) ? <p role="status">此 Pi 补充已失效，仅供历史核对。</p> : historical && item.revision ? <p>历史问题的 Pi 补充。</p> : null}
            {item.request || item.revision?.userRequest ? <p className="coach-adjustment-quote">你的调整：{item.request?.request ?? item.revision?.userRequest}</p> : null}
            {item.revision ? <PiSupplement value={item.revision} stale={expired || !piRevisionIsCurrent(item.revision, answerId, coachDecision, nowMs)} onEvidence={onEvidence} knowledgeApi={knowledgeApi} meetingId={sourceMeetingId ?? answer.meetingId} onKnowledgeSource={onKnowledgeSource} /> : item.request ? <div className="coach-request-state">
              {expired ? <p>这次调整针对修订前的回答，请在有效的更新回答中重新提交。</p> : ["failed", "timed_out"].includes(item.request.status) ? <>
                <p role="alert">{item.request.status === "timed_out" ? "这次补充未及时完成，可重试；原回答已保留。" : "这次补充未完成，原回答已保留。"}</p>
                <button type="button" className="coach-text-button" disabled={!canRefine || Boolean(pending)} onClick={() => requestRefinement(item.request!.request, item.request)}><RotateCcw size={14} />重试这次补充</button>
                {item.request.error ? <details><summary>错误详情</summary><p>{item.request.error}</p></details> : null}
              </> : <p role="status">{item.request.status === "no_change" ? "本次分析没有新的补充，原回答已保留。" : item.request.status === "succeeded" ? "补充已生成，正在同步内容…" : <><LoaderCircle className="spin" size={14} />正在补充这条回答…</>}</p>}
            </div> : null}
          </section>)}
          {coachRuntime?.state === "error" && !revisions.length ? <details className="coach-evidence-details"><summary>Pi 补充暂不可用，当前回答已保留</summary><p>{coachRuntime.detail}</p></details> : null}
        </article>
      </div>
      {newSupplement ? <button className="coach-new-supplement" type="button" onClick={() => { const item = threadItems.find((entry) => entry.revision && piRevisionIdentity(entry.revision) === newSupplement); if (item) scrollToItem(item.id); setNewSupplement(null); }}>本题有新补充 · 查看 <ArrowDown size={13} /></button> : null}
      {canRefine ? <form className="coach-composer" onSubmit={(event) => { event.preventDefault(); requestRefinement(draft); }}>
        <div className="coach-composer-label"><label htmlFor="coach-refinement-input">调整当前回答</label><button className="coach-text-button" type="button" onClick={() => setQuickOpen(!quickOpen)} aria-expanded={quickOpen}>常用调整</button></div>
        {quickOpen ? <div className="coach-quick-adjustments">{QUICK_ADJUSTMENTS.map((text) => <button className="coach-text-button" type="button" key={text} onClick={() => { setHoldSelection(true); setDrafts((current) => ({ ...current, [answerId]: text })); setQuickOpen(false); input.current?.focus(); }}>{text}</button>)}</div> : null}
        <div className="coach-composer-input"><textarea id="coach-refinement-input" ref={input} rows={1} value={draft} maxLength={1000} placeholder="更具体一点，或给我一个可以直接问的问题…"
          onChange={(event) => { setHoldSelection(true); setDrafts((current) => ({ ...current, [answerId]: event.target.value })); }}
          onFocus={() => { followRequest.current = null; }}
          onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); requestRefinement(draft); } }} />
          <button className="primary-button" type="submit" disabled={!draft.trim() || Boolean(pending)} aria-label="提交补充">{pending ? <LoaderCircle className="spin" size={15} /> : <ArrowUp size={16} />}补充</button>
        </div>
        <div className="coach-composer-hint">{pending && pending.answerId !== answerId ? <button className="coach-text-button" type="button" onClick={() => selectAnswer(pending.answerId)}>另一问题正在补充 · 查看进度</button> : connectionNotice || "仅针对当前问题 · 原回答保留 · Shift+Enter 换行"}</div>
      </form> : null}
    </div>
    {historyOpen ? <section className="coach-history-panel" aria-label="回答历史" onKeyDown={(event) => { if (event.key === "Escape") { event.stopPropagation(); closeHistory(); } }}>
      <label className="sr-only" htmlFor="coach-history-search">搜索问答记录</label>
      <input id="coach-history-search" ref={historySearch} placeholder="搜索问题、回答或 Pi 补充" value={historyQuery} onChange={(event) => setHistoryQuery(event.target.value)} />
      <p className="coach-history-hint">每个问题保留完整回答与后续补充</p>
      <ol className="coach-thread-list" aria-label="回答与 Pi 历史">{matching.map((thread) => <li key={thread.answerId}><button type="button" aria-current={thread.answerId === answerId ? "true" : undefined} onClick={() => selectAnswer(thread.answerId)}>
        <span><time>{timeLabel(thread.answer.createdAtMs)}</time> · {answerState(thread.answer)}</span><strong>{thread.answer.questionText ?? "会议建议"}</strong><span>{thread.piRevisions.length} 次 Pi 补充</span>
      </button></li>)}</ol>
      {!matching.length ? <p>没有匹配的记录，请尝试其他关键词。</p> : null}
      {children}
    </section> : null}
  </aside>;
}

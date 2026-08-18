import { Check, ChevronDown, ChevronUp, Clock3, FileCheck2, ListTodo, LoaderCircle, MessageSquareText, NotebookPen, Plus, Quote, Save, Send, Target } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import type { MeetingApi } from "../../api/client";
import type { AskAiMessage, AskAiScope, AskAiThread, MeetingChapter, MeetingPreparationSnapshot } from "../../domain/events";
import { NowRail } from "./NowRail";
import type { TranscriptSelection, TranscriptSelectionAction } from "./TranscriptPane";

interface AiWorkspaceProps extends ComponentProps<typeof NowRail> {
  meetingId: string;
  api: MeetingApi;
  selection: TranscriptSelection | null;
  askSelectionNonce: number;
  selectionAction?: TranscriptSelectionAction;
}

const SCOPE_LABELS: Record<AskAiScope, string> = {
  selection: "选中文字",
  recent: "最近内容",
  chapter: "当前章节",
  meeting: "整场会议",
};

const SELECTION_ACTION_PROMPTS: Partial<Record<TranscriptSelectionAction, string>> = {
  explain: "请解释选中内容的含义、上下文和影响，不要补充原文没有依据的信息。",
  extract_action_items: "请从选中内容提炼行动项，列出事项、负责人和截止时间；原文未说明的字段标记为待确认。",
  mark_pending: "请把选中内容整理为一条待确认事项，说明需要确认的问题、当前已知信息和原文依据。",
};

const CATCH_UP_PROMPT = "我刚错过了什么？请用 3 至 6 条简洁要点说明刚才讨论的主题、已确认结论和仍待确认的问题，并给出原文时间依据。";

function assistantEvidence(message: AskAiMessage): string | null {
  return message.evidence[0]?.segmentId ?? null;
}

function recentContextKindLabel(kind: "topic" | "decision" | "question"): string {
  if (kind === "decision") return "结论";
  if (kind === "question") return "待确认";
  return "主题";
}

function recentContextTime(updatedAtMs: number): string {
  if (!updatedAtMs) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(updatedAtMs));
}

export function AiWorkspace({
  meetingId,
  api,
  selection,
  askSelectionNonce,
  selectionAction = "ask",
  onEvidence,
  onMessage,
  ...railProps
}: AiWorkspaceProps) {
  // Realtime coaching is the primary meeting workflow; Ask AI remains available as a deliberate follow-up tool.
  const [tab, setTab] = useState<"ask" | "insights" | "context">("insights");
  const [scope, setScope] = useState<AskAiScope>("recent");
  const [recentMinutes, setRecentMinutes] = useState<1 | 3 | 5 | 10>(3);
  const [recentContextOpen, setRecentContextOpen] = useState(true);
  const [recentContextExpanded, setRecentContextExpanded] = useState(false);
  const [chapters, setChapters] = useState<MeetingChapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [threads, setThreads] = useState<AskAiThread[]>([]);
  const [savedNoteMessageIds, setSavedNoteMessageIds] = useState<Set<string>>(new Set());
  const [savedEntityKeys, setSavedEntityKeys] = useState<Set<string>>(new Set());
  const [threadId, setThreadId] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [streamText, setStreamText] = useState("");
  const [asking, setAsking] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [preparation, setPreparation] = useState<MeetingPreparationSnapshot | null>(null);
  const [preparationVersionCount, setPreparationVersionCount] = useState(0);
  const [contextGoal, setContextGoal] = useState("");
  const [contextRole, setContextRole] = useState("");
  const [contextFocus, setContextFocus] = useState("");
  const [contextSaving, setContextSaving] = useState(false);
  const questionRef = useRef<HTMLTextAreaElement | null>(null);

  const loadWorkspace = useCallback(async (preferredThreadId?: string | null) => {
    const [nextThreads, nextChapters, nextNotes] = await Promise.all([
      api.getAskThreads?.(meetingId) ?? Promise.resolve([]),
      api.getChapters?.(meetingId) ?? Promise.resolve([]),
      api.listNotes?.({ meetingId, status: "all" }) ?? Promise.resolve([]),
    ]);
    setThreads(nextThreads);
    setChapters(nextChapters);
    setSavedNoteMessageIds(new Set(nextNotes.flatMap((note) => note.sourceMessageId ? [note.sourceMessageId] : [])));
    setChapterId((current) => current || nextChapters.at(-1)?.chapterId || "");
    setThreadId((current) => {
      const preferred = preferredThreadId || current;
      return nextThreads.some((thread) => thread.threadId === preferred)
        ? preferred
        : nextThreads.at(-1)?.threadId ?? null;
    });
  }, [api, meetingId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void loadWorkspace().catch((loadError) => {
      if (!cancelled) setError(loadError instanceof Error ? loadError.message : "AI 工作区加载失败");
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [loadWorkspace]);

  useEffect(() => {
    if (!api.getMeetingPreparation) return;
    let cancelled = false;
    void Promise.all([
      api.getMeetingPreparation(meetingId),
      api.getMeetingPreparationVersions?.(meetingId) ?? Promise.resolve([]),
    ]).then(([nextPreparation, versions]) => {
      if (cancelled) return;
      setPreparation(nextPreparation);
      setPreparationVersionCount(versions.length);
      setContextGoal(nextPreparation.meetingGoal ?? "");
      setContextRole(nextPreparation.participantRole ?? "");
      setContextFocus(nextPreparation.focusPoints.join("、"));
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [api, meetingId]);

  useEffect(() => {
    if (!askSelectionNonce || !selection) return;
    setTab("ask");
    setScope("selection");
    const prompt = SELECTION_ACTION_PROMPTS[selectionAction];
    if (prompt) setQuestion(prompt);
    window.requestAnimationFrame(() => questionRef.current?.focus());
  }, [askSelectionNonce, selection, selectionAction]);

  useEffect(() => {
    if (scope === "selection" && !selection) setScope("recent");
  }, [scope, selection]);

  const currentThread = useMemo(
    () => threads.find((thread) => thread.threadId === threadId) ?? null,
    [threadId, threads],
  );

  const ask = async (questionOverride?: string) => {
    const normalizedQuestion = (questionOverride ?? question).trim();
    const effectiveScope: AskAiScope = questionOverride === CATCH_UP_PROMPT ? "recent" : scope;
    if (!normalizedQuestion || asking) return;
    if (!api.askMeeting) {
      setError("当前版本未启用 Ask AI");
      return;
    }
    if (effectiveScope === "selection" && !selection?.segmentIds.length) {
      setError("请先在会议文字中选中内容");
      return;
    }
    if (effectiveScope === "chapter" && !chapterId) {
      setError("当前还没有可用章节");
      return;
    }
    setAsking(true);
    setError(null);
    setPendingQuestion(normalizedQuestion);
    setQuestion("");
    setStreamText("");
    try {
      const result = await api.askMeeting(
        meetingId,
        {
          question: normalizedQuestion,
          scope: effectiveScope,
          threadId,
          segmentIds: effectiveScope === "selection" ? selection?.segmentIds : undefined,
          chapterId: effectiveScope === "chapter" ? chapterId : undefined,
          recentMinutes: effectiveScope === "recent" ? recentMinutes : undefined,
          intent: questionOverride === CATCH_UP_PROMPT ? "catch_up" : undefined,
        },
        (delta) => setStreamText((current) => current + delta),
      );
      await loadWorkspace(result.threadId);
      onMessage("AI 回答已保存");
    } catch (askError) {
      setError(askError instanceof Error ? askError.message : "AI 回答失败");
      setQuestion(normalizedQuestion);
    } finally {
      setAsking(false);
      setPendingQuestion("");
      setStreamText("");
    }
  };

  const saveMessageAsNote = async (message: AskAiMessage) => {
    if (!api.createNote || savedNoteMessageIds.has(message.messageId)) return;
    try {
      await api.createNote(meetingId, {
        body: message.content,
        sourceKind: "ask_ai",
        sourceMessageId: message.messageId,
        evidence: message.evidence.map((item) => ({
          segmentId: item.segmentId,
          transcriptSeq: item.transcriptSeq,
          startMs: item.startMs,
          endMs: item.endMs,
          quote: item.quote,
        })),
      });
      setSavedNoteMessageIds((current) => new Set(current).add(message.messageId));
      onMessage("已保存到笔记");
    } catch (saveError) {
      onMessage(saveError instanceof Error ? saveError.message : "笔记保存失败");
    }
  };

  const saveMessageAsEntity = async (message: AskAiMessage, kind: "decision_candidate" | "action_item") => {
    if (!api.createMeetingEntity) return;
    const key = `${message.messageId}:${kind}`;
    if (savedEntityKeys.has(key)) return;
    try {
      await api.createMeetingEntity(meetingId, {
        kind,
        text: message.content,
        sourceMessageId: message.messageId,
        evidence: message.evidence,
      });
      setSavedEntityKeys((current) => new Set(current).add(key));
      onMessage(kind === "decision_candidate" ? "已保存为候选事实" : "已保存为候选行动项");
    } catch (saveError) {
      onMessage(saveError instanceof Error ? saveError.message : "保存失败");
    }
  };

  const saveMeetingContext = async () => {
    if (!preparation || contextSaving) return;
    setContextSaving(true);
    try {
      await api.saveMeetingPreparation(meetingId, {
        hotwords: preparation.hotwords,
        inputSource: preparation.inputSource,
        inputDeviceId: preparation.inputDeviceId,
        inputDeviceName: preparation.inputDeviceName,
        noticeAcknowledged: true,
        presetId: preparation.presetId,
        meetingGoal: contextGoal.trim() || null,
        participantRole: contextRole.trim() || null,
        focusPoints: contextFocus.split(/[，,、;；\n]/).map((item) => item.trim()).filter(Boolean),
        outputFormat: preparation.outputFormat,
        proactiveSuggestionPolicy: preparation.proactiveSuggestionPolicy,
      });
      const [nextPreparation, versions] = await Promise.all([
        api.getMeetingPreparation?.(meetingId),
        api.getMeetingPreparationVersions?.(meetingId) ?? Promise.resolve([]),
      ]);
      if (nextPreparation) setPreparation(nextPreparation);
      setPreparationVersionCount(versions.length);
      onMessage("会议关注点已保存，新版本将用于后续 AI 理解");
    } catch (saveError) {
      onMessage(saveError instanceof Error ? saveError.message : "会议关注点保存失败");
    } finally {
      setContextSaving(false);
    }
  };

  const historyContext = railProps.recentContextHistory ?? [];
  const fallbackContext = historyContext.length ? [] : [
    railProps.currentTopic ? {
      contextId: `topic:${railProps.currentTopic.id}`,
      kind: "topic" as const,
      title: railProps.currentTopic.text,
      summary: null,
      updatedAtMs: railProps.currentTopic.updatedAtMs ?? 0,
      evidenceSegmentIds: railProps.currentTopic.evidenceSegmentIds,
    } : null,
    ...railProps.decisionCandidates.filter((item) => item.status === "confirmed").map((item) => ({
      contextId: `decision:${item.id}`,
      kind: "decision" as const,
      title: item.text,
      summary: null,
      updatedAtMs: item.updatedAtMs,
      evidenceSegmentIds: item.evidenceSegmentIds,
    })),
    ...railProps.openQuestions.filter((item) => ["open", "carried_over", "unknown"].includes(item.status)).map((item) => ({
      contextId: `question:${item.id}`,
      kind: "question" as const,
      title: item.text,
      summary: null,
      updatedAtMs: item.updatedAtMs ?? 0,
      evidenceSegmentIds: item.evidenceSegmentIds,
    })),
  ].filter((item): item is NonNullable<typeof item> => item !== null);
  const recentContextItems = [...historyContext, ...fallbackContext]
    .sort((left, right) => right.updatedAtMs - left.updatedAtMs)
    .slice(0, 10);
  const visibleRecentContext = recentContextExpanded ? recentContextItems : recentContextItems.slice(0, 5);

  return (
    <aside className="ai-workspace" aria-label="会议 AI 工作区">
      <div className="ai-workspace-tabs" role="tablist" aria-label="AI 工作区视图">
        <button type="button" role="tab" aria-selected={tab === "ask"} className={tab === "ask" ? "is-selected" : ""} onClick={() => setTab("ask")}>
          <MessageSquareText size={15} />Ask AI
        </button>
        <button type="button" role="tab" aria-selected={tab === "insights"} className={tab === "insights" ? "is-selected" : ""} onClick={() => setTab("insights")}>
          会议重点
        </button>
        {api.getMeetingPreparation ? (
          <button type="button" role="tab" aria-selected={tab === "context"} className={tab === "context" ? "is-selected" : ""} onClick={() => setTab("context")}>
            <Target size={15} />会议目标
          </button>
        ) : null}
      </div>

      {tab === "insights" ? (
        <NowRail
          {...railProps}
          activeCoachSkillId={preparation?.presetId ?? "general"}
          onEvidence={onEvidence}
          onMessage={onMessage}
        />
      ) : tab === "context" ? (
        <section className="meeting-context-panel" aria-label="会议目标和关注点">
          <header>
            <div><span>会中上下文</span><strong>会议目标</strong></div>
            <small>{preparation ? `版本 ${preparation.version} · 共 ${Math.max(preparationVersionCount, preparation.version)} 个版本` : "正在加载"}</small>
          </header>
          <label>
            <span>本次会议目标</span>
            <textarea value={contextGoal} onChange={(event) => setContextGoal(event.target.value)} maxLength={2_000} placeholder="明确本次会议需要达成的结果" />
          </label>
          <label>
            <span>我的角色</span>
            <input value={contextRole} onChange={(event) => setContextRole(event.target.value)} maxLength={200} placeholder="例如：产品负责人、技术评审人" />
          </label>
          <label>
            <span>重点关注</span>
            <textarea value={contextFocus} onChange={(event) => setContextFocus(event.target.value)} maxLength={1_200} placeholder="用逗号分隔关注点" />
          </label>
          <p>修改只影响后续 AI 理解，已生成内容和历史版本保持不变。</p>
          <button className="primary-button" type="button" onClick={() => void saveMeetingContext()} disabled={!preparation || contextSaving}>
            {contextSaving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
            {contextSaving ? "正在保存" : "保存新版本"}
          </button>
        </section>
      ) : (
        <div className="ask-ai-panel" role="tabpanel">
          <div className="ask-ai-toolbar">
            <select value={threadId ?? ""} onChange={(event) => setThreadId(event.target.value || null)} aria-label="AI 对话">
              <option value="">新对话</option>
              {threads.map((thread) => <option key={thread.threadId} value={thread.threadId}>{thread.title}</option>)}
            </select>
            <button className="icon-button icon-button--small" type="button" onClick={() => setThreadId(null)} title="新建对话" aria-label="新建对话">
              <Plus size={16} />
            </button>
          </div>

          <section className="recent-context-strip" aria-label="刚刚讨论">
            <button type="button" onClick={() => setRecentContextOpen((current) => !current)} aria-expanded={recentContextOpen}>
              <span><Clock3 size={14} />刚刚讨论</span>
              {recentContextOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {recentContextOpen ? (
              <div className={recentContextItems.length ? undefined : "recent-context-strip__empty"}>
                {recentContextItems.length ? (
                  <ol className="recent-context-timeline" aria-label="最近讨论时间线">
                    {visibleRecentContext.map((item) => {
                      const evidenceId = item.evidenceSegmentIds[0];
                      return (
                        <li key={item.contextId} data-kind={item.kind}>
                          <button type="button" disabled={!evidenceId} onClick={() => evidenceId && onEvidence(evidenceId)}>
                            <span className="recent-context-kind">{recentContextKindLabel(item.kind)}</span>
                            <span className="recent-context-copy">
                              <strong>{item.title}</strong>
                              {item.summary && item.summary !== item.title ? <small>{item.summary}</small> : null}
                            </span>
                            <time dateTime={item.updatedAtMs ? new Date(item.updatedAtMs).toISOString() : undefined}>{recentContextTime(item.updatedAtMs)}</time>
                          </button>
                        </li>
                      );
                    })}
                  </ol>
                ) : <span>暂无可用上下文</span>}
                {recentContextItems.length > 5 ? (
                  <button
                    className="recent-context-more"
                    type="button"
                    onClick={() => setRecentContextExpanded((current) => !current)}
                    aria-expanded={recentContextExpanded}
                  >
                    {recentContextExpanded ? <><ChevronUp size={13} />收起</> : <><ChevronDown size={13} />查看更早的 {recentContextItems.length - 5} 条</>}
                  </button>
                ) : null}
              </div>
            ) : null}
          </section>

          <div className="ask-ai-conversation" aria-live="polite">
            {loading ? <p className="ask-ai-empty"><LoaderCircle className="spin" size={18} />正在加载对话</p> : null}
            {currentThread?.messages.map((message) => (
              <article className={`ask-ai-message ask-ai-message--${message.role}`} key={message.messageId}>
                <span>{message.role === "user" ? "你" : "AI"}</span>
                <div><ReactMarkdown>{message.content || (message.status === "failed" ? "本次回答失败，请重试。" : "")}</ReactMarkdown></div>
                {message.role === "assistant" && message.status === "completed" ? (
                  <footer>
                    <button type="button" onClick={() => assistantEvidence(message) && onEvidence(assistantEvidence(message)!)} disabled={!assistantEvidence(message)}>
                      <Quote size={13} />查看依据
                    </button>
                    <button
                      type="button"
                      onClick={() => void saveMessageAsNote(message)}
                      aria-pressed={savedNoteMessageIds.has(message.messageId) || message.pinnedKind === "note"}
                      disabled={savedNoteMessageIds.has(message.messageId) || message.pinnedKind === "note"}
                    >
                      {savedNoteMessageIds.has(message.messageId) || message.pinnedKind === "note" ? <Check size={13} /> : <NotebookPen size={13} />}
                      {savedNoteMessageIds.has(message.messageId) || message.pinnedKind === "note" ? "已存笔记" : "存为笔记"}
                    </button>
                    <button type="button" onClick={() => void saveMessageAsEntity(message, "decision_candidate")} aria-pressed={savedEntityKeys.has(`${message.messageId}:decision_candidate`) || message.pinnedKind === "fact"} disabled={savedEntityKeys.has(`${message.messageId}:decision_candidate`) || message.pinnedKind === "fact"}>
                      {savedEntityKeys.has(`${message.messageId}:decision_candidate`) || message.pinnedKind === "fact" ? <Check size={13} /> : <FileCheck2 size={13} />}
                      {savedEntityKeys.has(`${message.messageId}:decision_candidate`) || message.pinnedKind === "fact" ? "已存事实" : "存为事实"}
                    </button>
                    <button type="button" onClick={() => void saveMessageAsEntity(message, "action_item")} aria-pressed={savedEntityKeys.has(`${message.messageId}:action_item`) || message.pinnedKind === "action_item"} disabled={savedEntityKeys.has(`${message.messageId}:action_item`) || message.pinnedKind === "action_item"}>
                      {savedEntityKeys.has(`${message.messageId}:action_item`) || message.pinnedKind === "action_item" ? <Check size={13} /> : <ListTodo size={13} />}
                      {savedEntityKeys.has(`${message.messageId}:action_item`) || message.pinnedKind === "action_item" ? "已存行动项" : "存为行动项"}
                    </button>
                  </footer>
                ) : null}
              </article>
            ))}
            {asking ? (
              <>
                <article className="ask-ai-message ask-ai-message--user"><span>你</span><p>{pendingQuestion}</p></article>
                <article className="ask-ai-message ask-ai-message--assistant ask-ai-message--streaming">
                  <span>AI</span>
                  {streamText ? <div><ReactMarkdown>{streamText}</ReactMarkdown></div> : <p><LoaderCircle className="spin" size={15} />正在读取会议上下文</p>}
                </article>
              </>
            ) : null}
          </div>

          <div className="ask-ai-composer">
            <div className="ask-ai-scope-row">
              <select value={scope} onChange={(event) => setScope(event.target.value as AskAiScope)} aria-label="提问范围">
                {(Object.keys(SCOPE_LABELS) as AskAiScope[]).map((value) => (
                  <option key={value} value={value} disabled={value === "selection" && !selection}>{SCOPE_LABELS[value]}</option>
                ))}
              </select>
              {scope === "chapter" ? (
                <select value={chapterId} onChange={(event) => setChapterId(event.target.value)} aria-label="选择章节">
                  {chapters.map((chapter) => <option key={chapter.chapterId} value={chapter.chapterId}>{chapter.index}. {chapter.title}</option>)}
                </select>
              ) : null}
              {scope === "recent" ? (
                <select value={recentMinutes} onChange={(event) => setRecentMinutes(Number(event.target.value) as 1 | 3 | 5 | 10)} aria-label="最近内容时间范围">
                  {[1, 3, 5, 10].map((minutes) => <option value={minutes} key={minutes}>最近 {minutes} 分钟</option>)}
                </select>
              ) : null}
              <button className="catch-up-button" type="button" onClick={() => { setScope("recent"); void ask(CATCH_UP_PROMPT); }} disabled={asking}>
                我刚错过了什么
              </button>
            </div>
            {scope === "selection" && selection ? <blockquote>{selection.text}</blockquote> : null}
            <div className="ask-ai-input-row">
              <textarea
                ref={questionRef}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void ask();
                  }
                }}
                placeholder="询问决策、风险、上下文或下一步"
                rows={3}
                maxLength={4_000}
                disabled={asking}
              />
              <button className="ask-ai-send" type="button" onClick={() => void ask()} disabled={asking || !question.trim()} title="发送" aria-label="发送问题">
                {asking ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}
              </button>
            </div>
            {error ? <p className="inline-error" role="alert">{error}</p> : null}
          </div>
        </div>
      )}
    </aside>
  );
}

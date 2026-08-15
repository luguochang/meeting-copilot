import { Bookmark, Check, CircleAlert, CircleHelp, Copy, EyeOff, Flag, GitMerge, ListChecks, MessageCircleQuestion, MoreHorizontal, Pencil, Quote, Save, ShieldAlert, TimerOff, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  ActionItemProjection,
  DecisionCandidate,
  FollowUpProjection,
  MeetingFactKind,
  MeetingFactStatus,
  OpenQuestionProjection,
  RiskProjection,
  Suggestion,
  SuggestionFeedback,
  TopicProjection,
} from "../../domain/events";

interface NowRailProps {
  currentTopic: TopicProjection | null;
  followUp: FollowUpProjection | null | undefined;
  openQuestions: OpenQuestionProjection[];
  suggestions: Suggestion[];
  decisionCandidates: DecisionCandidate[];
  actionItems: ActionItemProjection[];
  risks: RiskProjection[];
  onEvidence(segmentId: string): void;
  onFeedback(suggestionId: string, feedback: SuggestionFeedback): Promise<void>;
  onFactStatus(factType: MeetingFactKind, factId: string, status: Extract<MeetingFactStatus, "confirmed" | "dismissed">): Promise<void>;
  onFactEdit?(
    factType: MeetingFactKind,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
  ): Promise<void>;
  onFactMerge?(
    factType: MeetingFactKind,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
  ): Promise<void>;
  onMessage(message: string): void;
}

function currentSuggestion(suggestions: Suggestion[]): Suggestion | null {
  const visible = suggestions.filter(
    (item) =>
      item.status !== "rejected" &&
      item.status !== "superseded" &&
      item.feedback !== "ignored" &&
      item.feedback !== "false_positive" &&
      item.feedback !== "too_late",
  );
  return visible.sort((a, b) => b.evidenceTranscriptSeq - a.evidenceTranscriptSeq || b.updatedAtMs - a.updatedAtMs)[0] ?? null;
}

function suggestionText(suggestion: Suggestion): string {
  return suggestion.status === "committed" ? suggestion.text ?? suggestion.draftText : suggestion.draftText;
}

function questionIsOpen(question: OpenQuestionProjection): boolean {
  return question.status === "open" || question.status === "carried_over" || question.status === "unknown";
}

type RailFact = DecisionCandidate | ActionItemProjection | RiskProjection;
type FactView = "active" | "changed" | "resolved";

function isFormalAi(value: { formalAi?: { source: "llm_first"; llmCalled: true } | null }): boolean {
  return value.formalAi?.source === "llm_first" && value.formalAi.llmCalled === true;
}

function factStatusLabel(status: MeetingFactStatus): string {
  if (status === "candidate") return "候选";
  if (status === "confirmed") return "已确认";
  if (status === "dismissed") return "已忽略";
  if (status === "in_progress") return "进行中";
  if (status === "done") return "已完成";
  return "待确认";
}

function factKindLabel(kind: MeetingFactKind, status: MeetingFactStatus): string {
  const state = factStatusLabel(status);
  if (kind === "decision") return state === "已确认" ? "已确认决策" : state === "候选" ? "候选决策" : `${state}决策`;
  if (kind === "action_item") return state === "候选" ? "候选待办" : state === "已确认" ? "已确认待办" : `${state}待办`;
  return state === "候选" ? "候选风险" : state === "已确认" ? "已确认风险" : `${state}风险`;
}

function factEvidenceId(fact: RailFact): string | null {
  return fact.evidenceSpans[0]?.segmentId ?? fact.evidenceSegmentIds[0] ?? null;
}

function factEvidenceQuote(fact: RailFact): string {
  return fact.evidenceSpans[0]?.quote || "查看依据";
}

function coachLabel(followUp: FollowUpProjection): string {
  if (followUp.title) return followUp.title;
  if (followUp.coachEventType === "question_to_user") return "对方正在等你回答";
  if (followUp.coachEventType === "commitment_risk") return "先限定承诺条件";
  if (followUp.coachEventType === "goal_at_risk") return "目标可能被跳过";
  if (followUp.coachEventType === "contradiction") return "前后口径需要确认";
  return "建议追问";
}

function factIsResolved(fact: RailFact): boolean {
  return ["done", "answered", "resolved", "dismissed"].includes(String(fact.status));
}

function FactRow({
  fact,
  factType,
  onEvidence,
  onStatus,
  mergeCandidates,
  onEdit,
  onMerge,
}: {
  fact: RailFact;
  factType: MeetingFactKind;
  onEvidence(segmentId: string): void;
  onStatus(factType: MeetingFactKind, factId: string, status: Extract<MeetingFactStatus, "confirmed" | "dismissed">): Promise<void>;
  mergeCandidates: RailFact[];
  onEdit?: NowRailProps["onFactEdit"];
  onMerge?: NowRailProps["onFactMerge"];
}) {
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [merging, setMerging] = useState(false);
  const [textDraft, setTextDraft] = useState(fact.text);
  const [ownerDraft, setOwnerDraft] = useState(factType === "action_item" ? (fact as ActionItemProjection).owner ?? "" : "");
  const [deadlineDraft, setDeadlineDraft] = useState(factType === "action_item" ? (fact as ActionItemProjection).deadline ?? "" : "");
  const [mitigationDraft, setMitigationDraft] = useState(factType === "risk" ? (fact as RiskProjection).mitigation ?? "" : "");
  const [mergeSourceId, setMergeSourceId] = useState("");
  const evidenceId = factEvidenceId(fact);
  const save = async (status: Extract<MeetingFactStatus, "confirmed" | "dismissed">) => {
    if (saving) return;
    setSaving(true);
    try {
      await onStatus(factType, fact.id, status);
    } finally {
      setSaving(false);
    }
  };

  const saveEdit = async () => {
    if (!onEdit || saving || !textDraft.trim()) return;
    setSaving(true);
    try {
      await onEdit(factType, fact.id, {
        text: textDraft.trim(),
        ...(factType === "action_item" ? { owner: ownerDraft.trim() || null, deadline: deadlineDraft.trim() || null } : {}),
        ...(factType === "risk" ? { mitigation: mitigationDraft.trim() || null } : {}),
      }, fact.version ?? 1);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const saveMerge = async () => {
    const source = mergeCandidates.find((candidate) => candidate.id === mergeSourceId);
    if (!source || !onMerge || saving) return;
    setSaving(true);
    try {
      await onMerge(factType, fact.id, source.id, fact.version ?? 1, source.version ?? 1);
      setMerging(false);
      setMergeSourceId("");
    } finally {
      setSaving(false);
    }
  };

  return (
    <li className={`fact-row fact-row--${fact.status}`}>
      <div className="fact-row-main">
        <span className="fact-status-label">{factKindLabel(factType, fact.status)}</span>
        {editing ? (
          <div className="fact-inline-editor">
            <textarea value={textDraft} onChange={(event) => setTextDraft(event.target.value)} aria-label={`编辑${factKindLabel(factType, fact.status)}内容`} />
            {factType === "action_item" ? (
              <div><input value={ownerDraft} onChange={(event) => setOwnerDraft(event.target.value)} aria-label="编辑负责人" placeholder="负责人" /><input value={deadlineDraft} onChange={(event) => setDeadlineDraft(event.target.value)} aria-label="编辑截止时间" placeholder="截止时间" /></div>
            ) : null}
            {factType === "risk" ? <input value={mitigationDraft} onChange={(event) => setMitigationDraft(event.target.value)} aria-label="编辑风险应对" placeholder="风险应对" /> : null}
            <div><button type="button" onClick={() => void saveEdit()} disabled={saving || !textDraft.trim()}><Save size={13} />保存</button><button type="button" onClick={() => setEditing(false)} disabled={saving}><X size={13} />取消</button></div>
          </div>
        ) : <p>{fact.text}</p>}
        {!editing && factType === "action_item" ? (
          <span className="fact-detail">
            负责人：{(fact as ActionItemProjection).owner ?? "待定"} · 截止：{(fact as ActionItemProjection).deadline ?? "待定"}
          </span>
        ) : null}
        {!editing && factType === "risk" && (fact as RiskProjection).mitigation ? <span className="fact-detail">应对：{(fact as RiskProjection).mitigation}</span> : null}
        {merging ? (
          <div className="fact-merge-control">
            <select value={mergeSourceId} onChange={(event) => setMergeSourceId(event.target.value)} aria-label="选择要并入的同类事项">
              <option value="">选择同类事项</option>
              {mergeCandidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.text}</option>)}
            </select>
            <button type="button" onClick={() => void saveMerge()} disabled={!mergeSourceId || saving}><GitMerge size={13} />合并</button>
            <button type="button" onClick={() => setMerging(false)} disabled={saving}><X size={13} />取消</button>
          </div>
        ) : null}
      </div>
      <div className="fact-row-footer">
        <button
          className="fact-evidence-link"
          type="button"
          onClick={() => evidenceId && onEvidence(evidenceId)}
          disabled={!evidenceId}
          aria-label={`查看“${fact.text}”的依据`}
        >
          <Quote size={12} />
          <span>{factEvidenceQuote(fact)}</span>
        </button>
        <div className="fact-actions" aria-label={`${fact.text}操作`}>
          {onEdit ? <button className="icon-button icon-button--small" type="button" onClick={() => setEditing(true)} disabled={saving || merging} title="编辑" aria-label={`编辑“${fact.text}”`}><Pencil size={14} /></button> : null}
          {onMerge && mergeCandidates.length ? <button className="icon-button icon-button--small" type="button" onClick={() => setMerging(true)} disabled={saving || editing} title="合并同类事项" aria-label={`合并“${fact.text}”`}><GitMerge size={14} /></button> : null}
          {fact.status !== "confirmed" ? (
            <button
              className="icon-button icon-button--small"
              type="button"
              onClick={() => void save("confirmed")}
              disabled={saving}
              title="确认事实"
              aria-label={`确认${factKindLabel(factType, fact.status)}“${fact.text}”`}
            >
              <Check size={14} />
            </button>
          ) : null}
          <button
            className="icon-button icon-button--small"
            type="button"
            onClick={() => void save("dismissed")}
            disabled={saving}
            title="忽略事实"
            aria-label={`忽略${factKindLabel(factType, fact.status)}“${fact.text}”`}
          >
            <EyeOff size={14} />
          </button>
        </div>
      </div>
    </li>
  );
}

function FactGroup({
  icon,
  label,
  facts,
  factType,
  dismissedFactIds,
  onEvidence,
  onStatus,
  view,
  onEdit,
  onMerge,
}: {
  icon: LucideIcon;
  label: string;
  facts: RailFact[];
  factType: MeetingFactKind;
  dismissedFactIds: Set<string>;
  onEvidence(segmentId: string): void;
  onStatus(factType: MeetingFactKind, factId: string, status: Extract<MeetingFactStatus, "confirmed" | "dismissed">): Promise<void>;
  view: FactView;
  onEdit?: NowRailProps["onFactEdit"];
  onMerge?: NowRailProps["onFactMerge"];
}) {
  const Icon = icon;
  const visible = facts
    .filter((fact) => isFormalAi(fact))
    .filter((fact) => view === "resolved"
      ? factIsResolved(fact)
      : view === "changed"
        ? true
        : !factIsResolved(fact) && !dismissedFactIds.has(`${factType}:${fact.id}`))
    .sort((left, right) => view === "changed"
      ? right.updatedAtMs - left.updatedAtMs
      : (left.firstSeenSeq ?? 0) - (right.firstSeenSeq ?? 0))
    .slice(0, 4);
  return (
    <div className="fact-group">
      <div className="fact-group-heading">
        <span className="fact-group-label"><Icon size={13} />{label}</span>
        {visible.length ? <span className="fact-group-count">{visible.length}</span> : null}
      </div>
      {visible.length ? (
        <ul className="fact-list">
          {visible.map((fact) => (
            <FactRow
              key={fact.id}
              fact={fact}
              factType={factType}
              onEvidence={onEvidence}
              onStatus={onStatus}
              mergeCandidates={facts.filter((candidate) => candidate.id !== fact.id && !factIsResolved(candidate))}
              onEdit={onEdit}
              onMerge={onMerge}
            />
          ))}
        </ul>
      ) : <p className="fact-empty">暂无记录</p>}
    </div>
  );
}

export function NowRail({
  currentTopic,
  followUp,
  openQuestions,
  suggestions,
  decisionCandidates,
  actionItems,
  risks,
  onEvidence,
  onFeedback,
  onFactStatus,
  onFactEdit,
  onFactMerge,
  onMessage,
}: NowRailProps) {
  const suggestion = useMemo(
    () => currentSuggestion(suggestions.filter((item) => isFormalAi(item))),
    [suggestions],
  );
  const questions = openQuestions.filter((question) => isFormalAi(question) && questionIsOpen(question)).slice(0, 3);
  const formalTopic = currentTopic && isFormalAi(currentTopic) ? currentTopic : null;
  const formalFollowUp = followUp && isFormalAi(followUp) ? followUp : null;
  const [menuOpen, setMenuOpen] = useState(false);
  const [saving, setSaving] = useState<SuggestionFeedback | null>(null);
  const [dismissedFactIds, setDismissedFactIds] = useState<Set<string>>(new Set());
  const [factStatusOverrides, setFactStatusOverrides] = useState<Record<string, MeetingFactStatus>>({});
  const [savingQuestionId, setSavingQuestionId] = useState<string | null>(null);
  const [factView, setFactView] = useState<FactView>("active");
  const text = suggestion ? suggestionText(suggestion) : "";

  const withFactStatusOverrides = <T extends RailFact>(factType: MeetingFactKind, facts: T[]): T[] => facts.map((fact) => {
    const status = factStatusOverrides[`${factType}:${fact.id}`];
    return status ? { ...fact, status } : fact;
  });

  const saveFactStatus = async (
    factType: MeetingFactKind,
    factId: string,
    status: Extract<MeetingFactStatus, "confirmed" | "dismissed">,
  ) => {
    try {
      await onFactStatus(factType, factId, status);
      setFactStatusOverrides((current) => ({ ...current, [`${factType}:${factId}`]: status }));
      if (status === "dismissed") {
        setDismissedFactIds((current) => new Set(current).add(`${factType}:${factId}`));
      }
      onMessage(status === "confirmed" ? "事实已确认" : "事实已忽略");
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "事实状态保存失败");
      throw error;
    }
  };

  const saveFeedback = async (feedback: SuggestionFeedback) => {
    if (!suggestion || saving) return;
    setSaving(feedback);
    setMenuOpen(false);
    try {
      await onFeedback(suggestion.suggestionId, feedback);
      const labels: Record<SuggestionFeedback, string> = {
        kept: "建议已保留",
        ignored: "建议已忽略",
        false_positive: "已标记为误报",
        too_late: "已标记为太晚",
      };
      onMessage(labels[feedback]);
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "反馈保存失败");
    } finally {
      setSaving(null);
    }
  };

  const copySuggestion = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      onMessage("追问已复制");
    } catch {
      onMessage("复制失败，请检查剪贴板权限");
    }
  };

  return (
    <aside className="now-rail" aria-label="当前会议重点">
      <section className="rail-section topic-section" aria-labelledby="topic-title">
        <header className="rail-heading">
          <Flag size={15} />
          <h2 id="topic-title">当前议题</h2>
        </header>
        {formalTopic ? (
          <button
            className="topic-content evidence-content"
            type="button"
            onClick={() => formalTopic.evidenceSegmentIds[0] && onEvidence(formalTopic.evidenceSegmentIds[0])}
            disabled={!formalTopic.evidenceSegmentIds.length}
          >
            {formalTopic.text}
          </button>
        ) : (
          <p className="rail-empty">尚未形成明确议题</p>
        )}
      </section>

      <section className="rail-section suggestion-section" aria-labelledby="suggestion-title">
        <header className="rail-heading">
          <MessageCircleQuestion size={16} />
          <h2 id="suggestion-title">AI 实时教练</h2>
          {suggestion?.status === "draft" || suggestion?.status === "validating" ? (
            <span className="draft-badge">生成中</span>
          ) : null}
        </header>

        {formalFollowUp ? (
          <div className="follow-up-card" data-testid="follow-up-card">
            <div className="follow-up-heading">
              <strong>{coachLabel(formalFollowUp)}</strong>
              <span
                className="follow-up-reason"
                title={`为什么现在提示：${formalFollowUp.reason}${formalFollowUp.evidenceQuote ? `；依据：${formalFollowUp.evidenceQuote}` : ""}`}
              >
                <CircleHelp size={15} aria-hidden="true" />
                <span className="sr-only">{formalFollowUp.reason}</span>
              </span>
            </div>
            <blockquote>{formalFollowUp.question}</blockquote>
            <p className="follow-up-reason-text">{formalFollowUp.reason}</p>
            <div className="suggestion-footer">
              {formalFollowUp.evidenceSegmentIds[0] ? (
                <button
                  className="evidence-link"
                  type="button"
                  onClick={() => onEvidence(formalFollowUp.evidenceSegmentIds[0])}
                >
                  <Quote size={13} />查看依据
                </button>
              ) : <span className="evidence-link evidence-link--disabled">暂无可定位依据</span>}
              <span className="follow-up-urgency">{formalFollowUp.urgency === "high" ? "紧急" : formalFollowUp.urgency === "low" ? "低优先" : "适时确认"}</span>
            </div>
          </div>
        ) : suggestion && text ? (
          <div className={`suggestion-card suggestion-card--${suggestion.status}`}>
            <blockquote>{text}</blockquote>
            <div className="suggestion-footer">
              <button
                className="evidence-link"
                type="button"
                onClick={() => onEvidence(suggestion.evidenceSegmentId)}
              >
                <Quote size={13} />查看依据
              </button>
              <div className="suggestion-actions" aria-label="建议操作">
                <button className="icon-button icon-button--small" type="button" onClick={copySuggestion} title="复制追问" aria-label="复制追问">
                  <Copy size={15} />
                </button>
                <button
                  className={`icon-button icon-button--small ${suggestion.feedback === "kept" ? "is-selected" : ""}`}
                  type="button"
                  onClick={() => void saveFeedback("kept")}
                  title="保留建议"
                  aria-label="保留建议"
                  disabled={Boolean(saving)}
                >
                  {suggestion.feedback === "kept" ? <Check size={15} /> : <Bookmark size={15} />}
                </button>
                <button className="icon-button icon-button--small" type="button" onClick={() => void saveFeedback("ignored")} title="忽略建议" aria-label="忽略建议" disabled={Boolean(saving)}>
                  <EyeOff size={15} />
                </button>
                <div className="feedback-menu-wrap">
                  <button className="icon-button icon-button--small" type="button" onClick={() => setMenuOpen((value) => !value)} title="更多反馈" aria-label="更多反馈" aria-expanded={menuOpen}>
                    <MoreHorizontal size={16} />
                  </button>
                  {menuOpen ? (
                    <div className="feedback-menu" role="menu">
                      <button type="button" role="menuitem" onClick={() => void saveFeedback("false_positive")}><Flag size={14} />误报</button>
                      <button type="button" role="menuitem" onClick={() => void saveFeedback("too_late")}><TimerOff size={14} />太晚</button>
                    </div>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <p className="rail-empty">暂无实时建议</p>
        )}
      </section>

      <section className="rail-section questions-section" aria-labelledby="questions-title">
        <header className="rail-heading">
          <span className="question-mark" aria-hidden="true">?</span>
          <h2 id="questions-title">未闭环问题</h2>
          {questions.length ? <span className="count-badge">{questions.length}</span> : null}
        </header>
        {questions.length ? (
          <ol className="question-list">
            {questions.map((question) => (
              <li key={question.id}>
                <button
                  className="question-evidence"
                  type="button"
                  onClick={() => question.evidenceSegmentIds[0] && onEvidence(question.evidenceSegmentIds[0])}
                  disabled={!question.evidenceSegmentIds.length}
                >
                  {question.text}
                </button>
                <div className="question-actions">
                  <button
                    className="icon-button icon-button--small"
                    type="button"
                    title="标记已闭环"
                    aria-label={`标记问题“${question.text}”已闭环`}
                    disabled={savingQuestionId === question.id}
                    onClick={() => {
                      setSavingQuestionId(question.id);
                      void onFactStatus("open_question", question.id, "confirmed")
                        .then(() => onMessage("问题已标记为闭环"))
                        .catch((error) => onMessage(error instanceof Error ? error.message : "问题状态保存失败"))
                        .finally(() => setSavingQuestionId(null));
                    }}
                  >
                    <Check size={13} />
                  </button>
                  <button
                    className="icon-button icon-button--small"
                    type="button"
                    title="忽略问题"
                    aria-label={`忽略问题“${question.text}”`}
                    disabled={savingQuestionId === question.id}
                    onClick={() => {
                      setSavingQuestionId(question.id);
                      void onFactStatus("open_question", question.id, "dismissed")
                        .then(() => onMessage("问题已忽略"))
                        .catch((error) => onMessage(error instanceof Error ? error.message : "问题状态保存失败"))
                        .finally(() => setSavingQuestionId(null));
                    }}
                  >
                    <EyeOff size={13} />
                  </button>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <p className="rail-empty">暂无已识别的未闭环问题</p>
        )}
      </section>

      <section className="rail-section facts-section" aria-labelledby="facts-title" aria-label="会议事实">
        <header className="rail-heading">
          <CircleAlert size={15} />
          <h2 id="facts-title">会议事实</h2>
        </header>
        <div className="fact-view-tabs" role="tablist" aria-label="会议事实视图">
          <button type="button" role="tab" aria-selected={factView === "active"} className={factView === "active" ? "is-selected" : ""} onClick={() => setFactView("active")}>当前活跃</button>
          <button type="button" role="tab" aria-selected={factView === "changed"} className={factView === "changed" ? "is-selected" : ""} onClick={() => setFactView("changed")}>刚刚变化</button>
          <button type="button" role="tab" aria-selected={factView === "resolved"} className={factView === "resolved" ? "is-selected" : ""} onClick={() => setFactView("resolved")}>已解决</button>
        </div>
        <div className="fact-groups">
          <FactGroup
            icon={ListChecks}
            label="决策"
            facts={withFactStatusOverrides("decision", decisionCandidates)}
            factType="decision"
            dismissedFactIds={dismissedFactIds}
            onEvidence={onEvidence}
            onStatus={saveFactStatus}
            view={factView}
            onEdit={onFactEdit}
            onMerge={onFactMerge}
          />
          <FactGroup
            icon={ListChecks}
            label="待办"
            facts={withFactStatusOverrides("action_item", actionItems)}
            factType="action_item"
            dismissedFactIds={dismissedFactIds}
            onEvidence={onEvidence}
            onStatus={saveFactStatus}
            view={factView}
            onEdit={onFactEdit}
            onMerge={onFactMerge}
          />
          <FactGroup
            icon={ShieldAlert}
            label="风险"
            facts={withFactStatusOverrides("risk", risks)}
            factType="risk"
            dismissedFactIds={dismissedFactIds}
            onEvidence={onEvidence}
            onStatus={saveFactStatus}
            view={factView}
            onEdit={onFactEdit}
            onMerge={onFactMerge}
          />
        </div>
      </section>
    </aside>
  );
}

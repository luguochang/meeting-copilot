import { Check, CircleAlert, EyeOff, Flag, GitMerge, ListChecks, Pencil, Quote, Save, ShieldAlert, X, type LucideIcon } from "lucide-react";
import { useState } from "react";
import type { ActionItemProjection, DecisionCandidate, MeetingFactKind, MeetingFactStatus, RiskProjection, OpenQuestionProjection } from "../../domain/events";
import type { NowRailProps } from "./nowRailTypes";

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

export function MeetingFactsPanel({ currentTopic, openQuestions, decisionCandidates, actionItems, risks, onEvidence, onFactStatus, onFactEdit, onFactMerge, onMessage }: NowRailProps) {
  const questions = openQuestions.filter((question) => isFormalAi(question) && questionIsOpen(question));
  const formalTopic = currentTopic && isFormalAi(currentTopic) ? currentTopic : null;
  const [dismissedFactIds, setDismissedFactIds] = useState<Set<string>>(new Set());
  const [factStatusOverrides, setFactStatusOverrides] = useState<Record<string, MeetingFactStatus>>({});
  const [savingQuestionId, setSavingQuestionId] = useState<string | null>(null);
  const [factView, setFactView] = useState<FactView>("active");
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

  return <div className="meeting-facts-panel">
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
  </div>;
}

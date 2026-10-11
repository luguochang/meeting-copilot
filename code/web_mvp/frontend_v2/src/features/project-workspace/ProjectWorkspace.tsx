import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { ProjectItemInput, ProjectItemStatus, ProjectOutcomesApi, ProjectProposal, ProjectSourceRef, ProjectStateItem, ProjectWiki } from "../../api/projectOutcomes";
import { ProjectSources } from "./ProjectSources";
import "./projectWorkspace.css";
import type { ProjectReconcileApi } from "../../api/projectReconcile";
import { ProjectReconcileJobs } from "./ProjectReconcileJobs";

const typeLabels = { decision: "决策", task: "待办", question: "待确认问题" };
const statusLabels: Record<ProjectItemStatus, string> = { active: "有效", done: "已完成", cancelled: "已取消", unknown: "待确认", discarded: "已作废", superseded: "已被替代", needs_review: "来源变化，待复核" };
const proposalLabels = { pending: "待你审核", confirmed: "已确认", rejected: "已拒绝", needs_review: "来源变化，待复核" };
const reasonLabels: Record<string, string> = { created: "新建", updated: "人工修改", source_changed: "来源变化", proposal_confirmed: "候选确认", superseded: "被新事项替代" };
function errorText(e: unknown) { return e instanceof Error ? e.message : "操作未完成，请重试。"; }
interface Editor { item: ProjectStateItem | null; input: ProjectItemInput; manualReconfirm: boolean }

export function ProjectWorkspace({ api, reconcileApi, initialView, projectId, archived = false, onDocument, onMeeting, onEditingChange }: {
  reconcileApi?: ProjectReconcileApi; initialView?: "state" | "proposals" | "wiki" | "jobs";
  api: ProjectOutcomesApi; projectId: string; archived?: boolean;
  onDocument?: (source: ProjectSourceRef) => void; onMeeting?: (id: string) => void; onEditingChange?: (editing: boolean) => void;
}) {
  const [view, setView] = useState<"state" | "proposals" | "wiki" | "jobs">(initialView ?? "state");
  const [items, setItems] = useState<ProjectStateItem[]>([]); const [proposals, setProposals] = useState<ProjectProposal[]>([]); const [wiki, setWiki] = useState<ProjectWiki | null>(null);
  const [revision, setRevision] = useState<number | null>(null); const [status, setStatus] = useState(""); const [cursor, setCursor] = useState<string | null>(null); const [next, setNext] = useState<string | null>(null); const [previous, setPrevious] = useState<(string | null)[]>([]);
  const [proposalCursor, setProposalCursor] = useState<string | null>(null); const [proposalNext, setProposalNext] = useState<string | null>(null); const [proposalPrevious, setProposalPrevious] = useState<(string | null)[]>([]);
  const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null); const [history, setHistory] = useState<{ item: ProjectStateItem; rows: { snapshot: ProjectStateItem; reason: string }[]; before: number | null } | null>(null);
  const reload = useCallback(() => { setError(null); setRefresh((x) => x + 1); }, []);
  useEffect(() => {
    onEditingChange?.(editor !== null); return () => onEditingChange?.(false);
  }, [editor, onEditingChange]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    void (async () => {
      const [state, candidatePage, currentWiki] = await Promise.all([api.listState(projectId, status || null, cursor, controller.signal), api.listProposals(projectId, proposalCursor, controller.signal), api.getWiki(projectId, controller.signal)]);
      if (controller.signal.aborted) return;
      setItems(state.items); setRevision(state.revision); setNext(state.hasMore ? state.nextCursor : null); setProposals(candidatePage.items); setProposalNext(candidatePage.hasMore ? candidatePage.nextCursor : null); setWiki(currentWiki);
    })().catch((e) => { if (!controller.signal.aborted) setError(errorText(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, cursor, projectId, proposalCursor, refresh, status]);
  useEffect(() => {
    if (!editor) return;
    const listener = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", listener); return () => window.removeEventListener("beforeunload", listener);
  }, [editor]);
  useEffect(() => {
    if (!editor && !history) return;
    const keydown = (event: KeyboardEvent) => {
      const dialog = document.querySelector<HTMLElement>(".project-workspace [role=dialog]");
      if (event.key !== "Tab") return;
      const elements = dialog?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)");
      const first = elements?.[0]; const last = elements?.[elements.length - 1];
      if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", keydown); return () => window.removeEventListener("keydown", keydown);
  }, [editor, history]);
  const act = async (operation: () => Promise<void>) => { setBusy(true); setError(null); try { await operation(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } };
  const edit = (item: ProjectStateItem | null) => {
    setError(null); setEditor({ item, manualReconfirm: false, input: item ? { type: item.type, text: item.text, status: ["superseded", "needs_review"].includes(item.status) ? "active" : item.status as ProjectItemInput["status"], owner: item.owner, due: item.due } : { type: "decision", text: "", status: "active", owner: null, due: null } });
  };
  const save = async () => act(async () => {
    if (!editor || revision === null) return;
    const input = { ...editor.input, text: editor.input.text.trim(), owner: editor.input.owner?.trim() || null, due: editor.input.due?.trim() || null };
    if (editor.item) {
      const changes = { text: input.text, status: input.status, owner: input.owner, due: input.due };
      await api.updateState(editor.item.id, { ...changes, ...(editor.manualReconfirm ? { sourceRefs: [] } : {}) }, editor.item.revision, revision);
    } else await api.createState(projectId, input, revision);
    setEditor(null); setNotice("已按你的明确输入确认项目事项。"); reload();
  });
  const showHistory = (item: ProjectStateItem) => void act(async () => { const value = await api.history(item.id); setHistory({ item, rows: value.items, before: value.nextBeforeRevision }); });
  const refreshEditor = () => void act(async () => {
    if (!editor?.item) return;
    const latestPage = await api.listState(projectId); const latestHistory = await api.history(editor.item.id);
    const latest = latestHistory.items[0]?.snapshot;
    if (!latest) throw new Error("事项最新版本不可用，请重新打开事项。");
    setRevision(latestPage.revision); setEditor({ ...editor, item: latest, manualReconfirm: false }); setNotice("已读取最新事项；你的草稿保留。请比较后再明确保存。");
  });
  const confirm = (proposal: ProjectProposal) => void act(async () => {
    if (revision === null) return;
    await api.confirmProposal(proposal.id, proposal.revision, revision); setNotice("候选已确认，项目状态与 Wiki 已更新。"); reload();
  });
  const reject = (proposal: ProjectProposal) => void act(async () => { await api.rejectProposal(proposal.id, proposal.revision); setNotice("候选已拒绝，不会进入项目状态。"); reload(); });
  const sourceProps = { api, onDocument, onMeeting };
  return <section className="project-workspace" aria-label="项目状态与 Wiki">
    <header><div><h2>项目状态与 Wiki</h2><p>明确确认的决策、待办与未解决问题，供后续会议继续使用。</p></div><button type="button" disabled={busy || loading} onClick={reload}>刷新项目状态</button></header>
    <nav aria-label="项目工作视图"><button type="button" aria-pressed={view === "state"} onClick={() => setView("state")}>已确认事项</button><button type="button" aria-pressed={view === "proposals"} onClick={() => setView("proposals")}>待审核候选</button><button type="button" aria-pressed={view === "wiki"} onClick={() => setView("wiki")}>项目 Wiki</button>{reconcileApi ? <button type="button" aria-pressed={view === "jobs"} onClick={() => setView("jobs")}>后台核对任务</button> : null}</nav>
    {error ? <div className="knowledge-alert" role="alert">{error}<p>内容或来源可能已更新。请刷新后核对再提交；未保存的编辑保留。</p></div> : null}{notice ? <p className="knowledge-notice" role="status">{notice}</p> : null}{archived ? <p>项目已归档，恢复后可修改或审核。</p> : null}
    {loading ? <p role="status">正在读取项目状态…</p> : error && revision === null ? <p>项目内容未读取成功，请刷新项目状态；不能据此判断没有候选。</p> : view === "jobs" && reconcileApi ? <ProjectReconcileJobs api={reconcileApi} projectApi={api} projectId={projectId} onOpenProposals={() => { setView("proposals"); reload(); }} /> : view === "state" ? <>
      <div className="knowledge-actions"><label>事项状态<select aria-label="筛选项目事项状态" value={status} onChange={(event) => { setStatus(event.target.value); setCursor(null); setPrevious([]); }}><option value="">全部</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="button" disabled={archived || busy || revision === null} onClick={() => edit(null)}>添加已确认事项</button></div>
      {!items.length ? <p>尚无项目事项。可以明确输入一项决策、待办或问题，也可以审核会议产生的候选。</p> : items.map((item) => <article className="project-item" key={item.id}><strong>{typeLabels[item.type]} · {statusLabels[item.status]}</strong><p>{item.text}</p>{item.type === "task" ? <p>负责人：{item.owner ?? "未指定"} · 截止：{item.due ?? "未指定"}</p> : null}<ProjectSources {...sourceProps} sources={item.sourceRefs} stale={item.status === "needs_review"} /><div className="knowledge-actions"><button type="button" disabled={archived || busy || item.status === "superseded"} onClick={() => edit(item)}>修改事项</button><button type="button" disabled={busy} onClick={() => showHistory(item)}>修改历史</button></div></article>)}
      <div className="knowledge-pagination"><button type="button" disabled={busy || !previous.length} onClick={() => { setCursor(previous.at(-1) ?? null); setPrevious((x) => x.slice(0, -1)); }}>上一页事项</button><span>第 {previous.length + 1} 页</span><button type="button" disabled={busy || !next} onClick={() => { setPrevious((x) => [...x, cursor]); setCursor(next); }}>下一页事项</button></div>
    </> : view === "proposals" ? <>
      <p>候选不会直接进入项目状态。核对内容、负责人、时间与来源，再明确确认或拒绝。</p>
      {!proposals.length ? <p>暂无候选。会中已确认的会议事项可以加入项目候选，或直接输入项目事项。</p> : proposals.map((proposal) => <article className="project-item" key={proposal.id}><strong>{proposalLabels[proposal.status]}{proposal.isStale && proposal.status === "pending" ? " · 项目已更新" : ""}</strong>{proposal.changes.map((change, index) => <section key={index}><p>{({ create: "新增", update: "修改", supersede: "替代旧事项", discard: "作废" })[change.action]} · {change.type ? typeLabels[change.type] : "项目事项"}</p>{change.itemId ? <p className="knowledge-muted">原事项：{items.find((x) => x.id === change.itemId)?.text ?? "需在事项列表或历史中核对"}</p> : null}<p>{change.text ?? "保持原内容"}</p>{change.owner || change.due ? <p>负责人：{change.owner ?? "未指定"} · 截止：{change.due ?? "未指定"}</p> : null}<ProjectSources {...sourceProps} sources={change.sourceRefs} stale={proposal.status === "needs_review"} /></section>)}{(proposal.isStale && proposal.status === "pending") || proposal.status === "needs_review" ? <p className="knowledge-alert">项目或来源已变化，此候选不能直接确认。请拒绝旧候选并按当前来源重新整理。</p> : null}{proposal.status === "pending" || proposal.status === "needs_review" ? <div className="knowledge-actions"><button type="button" className="knowledge-primary" disabled={archived || busy || proposal.isStale || proposal.status !== "pending" || revision === null} onClick={() => confirm(proposal)}>确认这些变更</button><button type="button" disabled={archived || busy} onClick={() => reject(proposal)}>拒绝候选</button></div> : null}</article>)}
      <div className="knowledge-pagination"><button type="button" disabled={busy || !proposalPrevious.length} onClick={() => { setProposalCursor(proposalPrevious.at(-1) ?? null); setProposalPrevious((x) => x.slice(0, -1)); }}>上一页候选</button><span>第 {proposalPrevious.length + 1} 页</span><button type="button" disabled={busy || !proposalNext} onClick={() => { setProposalPrevious((x) => [...x, proposalCursor]); setProposalCursor(proposalNext); }}>下一页候选</button></div>
    </> : <><p>由已确认项目状态整理，修改事项后可刷新。候选不会进入这里。</p>{wiki ? <div className="project-wiki"><ReactMarkdown components={{ a: ({ children }) => <span>{children}</span> }}>{wiki.markdown}</ReactMarkdown>{wiki.truncated ? <p>仅显示部分事项，完整内容请查看事项列表。</p> : null}{wiki.items.filter((x) => x.sourceRefs.length).map((item) => <section key={item.id}><strong>{item.text}</strong><ProjectSources {...sourceProps} sources={item.sourceRefs} stale={item.status === "needs_review"} /></section>)}</div> : <p>暂无可整理的项目状态。</p>}</>}
    {editor ? <div className="knowledge-modal-backdrop"><section className="knowledge-modal" role="dialog" aria-modal="true" aria-label={editor.item ? "修改项目事项" : "添加项目事项"}><h2>{editor.item ? "修改项目事项" : "添加项目事项"}</h2>{error ? <p className="knowledge-alert" role="alert">{error}</p> : null}{editor.item ? <><p>最新已保存内容：{editor.item.text}</p><button type="button" disabled={busy} onClick={refreshEditor}>重新载入最新版本（保留我的草稿）</button></> : null}<form onSubmit={(event) => { event.preventDefault(); void save(); }}><label>事项类型<select aria-label="项目事项类型" disabled={!!editor.item || busy} value={editor.input.type} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, type: event.target.value as ProjectItemInput["type"], status: "active" } })}>{Object.entries(typeLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>事项内容<textarea autoFocus aria-label="项目事项内容" maxLength={2000} value={editor.input.text} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, text: event.target.value } })} /></label><label>状态<select aria-label="项目事项状态" value={editor.input.status} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, status: event.target.value as ProjectItemInput["status"] } })}>{(editor.input.type === "decision" ? ["active", "discarded"] : ["active", "done", "cancelled", "unknown", "discarded"]).map((value) => <option key={value} value={value}>{statusLabels[value as ProjectItemStatus]}</option>)}</select></label>{editor.input.type === "task" ? <><label>负责人（明确输入）<input aria-label="项目事项负责人" maxLength={200} value={editor.input.owner ?? ""} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, owner: event.target.value } })} /></label><label>截止时间（明确输入）<input aria-label="项目事项截止" maxLength={200} value={editor.input.due ?? ""} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, due: event.target.value } })} /></label></> : null}{editor.item?.status === "needs_review" ? <label><input type="checkbox" checked={editor.manualReconfirm} onChange={(event) => setEditor({ ...editor, manualReconfirm: event.target.checked })} />我已独立核实，按我的明确输入确认，不沿用旧引用</label> : null}<p>保存即表示你明确确认这些内容。其他人修改后会提示冲突，不覆盖他们的版本。</p><div className="knowledge-actions"><button type="button" disabled={busy} onClick={() => setEditor(null)}>取消编辑</button><button className="knowledge-primary" type="submit" disabled={busy || !editor.input.text.trim() || (editor.item?.status === "needs_review" && !editor.manualReconfirm)}>确认并保存事项</button></div></form></section></div> : null}
    {history ? <div className="knowledge-modal-backdrop"><section className="knowledge-modal" role="dialog" aria-modal="true" aria-label="项目事项修改历史"><h2>修改历史</h2>{history.rows.map(({ snapshot, reason }) => <article className="project-item" key={snapshot.revision}><strong>第 {snapshot.revision} 次修改 · {statusLabels[snapshot.status]}</strong><p>{snapshot.text}</p><p>{reasonLabels[reason] ?? "事项更新"}</p><ProjectSources {...sourceProps} sources={snapshot.sourceRefs} /></article>)}{history.before ? <button type="button" disabled={busy} onClick={() => void act(async () => { const value = await api.history(history.item.id, history.before); setHistory({ ...history, rows: [...history.rows, ...value.items], before: value.nextBeforeRevision }); })}>更早修改</button> : null}<button type="button" onClick={() => setHistory(null)}>关闭历史</button></section></div> : null}
  </section>;
}

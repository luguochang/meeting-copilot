import { useCallback, useEffect, useState } from "react";
import type { MemoryApi, MemoryInput, MemoryScene, MemoryScope, UserMemory } from "../../api/memory";
import type { ProjectOutcomesApi, ProjectSourceRef } from "../../api/projectOutcomes";
import { ProjectSources } from "../project-workspace/ProjectSources";
import type { MemoryReconcileApi } from "../../api/memoryReconcile";
import { MemoryReconcilePanel } from "./MemoryReconcilePanel";
import "./memoryPanel.css";

const scopeLabels = { global: "全局", project: "当前项目", meeting: "本场会议" };
const sceneLabels: Record<MemoryScene, string> = { "": "全部场景", general: "一般讨论", decision: "决策评审", project: "项目推进", interview: "用户访谈", brainstorm: "头脑风暴" };
const statusLabels = { candidate: "待确认", active: "已确认生效", disabled: "已禁用", deleted: "已删除", needs_review: "来源变化，待复核" };
const preferenceLabels: Record<string, string> = { response_length: "回答长度", suggestion_policy: "主动建议频率" };
const valueLabels: Record<string, string> = { brief: "简短", standard: "标准", off: "关闭", low_frequency: "低频" };
function describe(memory: UserMemory) {
  const value = memory.kind === "term" ? memory.value : `${preferenceLabels[memory.key] ?? "偏好"}：${valueLabels[memory.value] ?? memory.value}`;
  const origin = memory.origin === "ai" && memory.status === "candidate" ? "Pi 提议：" : "";
  const conflicts = memory.conflictingMemoryIds?.length ? `（与 ${memory.conflictingMemoryIds.length} 条已确认记忆冲突，需先核对）` : "";
  return `${origin}${value}${conflicts}`;
}
function aliases(value: string) { return [...new Set(value.split(/[，,\n]/).map((x) => x.trim()).filter(Boolean))]; }

export function MemoryPanel({ api, reconcileApi, projectApi, projectId, meetingId, initialScope, onDocument, onEditingChange, onChanged }: {
  api: MemoryApi; projectApi?: ProjectOutcomesApi; projectId?: string | null; meetingId?: string;
  initialScope?: MemoryScope; onDocument?: (source: ProjectSourceRef) => void; reconcileApi?: MemoryReconcileApi;
  onEditingChange?: (value: boolean) => void; onChanged?: () => void;
}) {
  const [scope, setScope] = useState<MemoryScope>(initialScope ?? (meetingId ? "meeting" : projectId ? "project" : "global"));
  const scopeId = scope === "global" ? "" : scope === "project" ? projectId ?? "" : meetingId ?? "";
  const [items, setItems] = useState<UserMemory[]>([]); const [cursor, setCursor] = useState<string | null>(null); const [next, setNext] = useState<string | null>(null); const [previous, setPrevious] = useState<(string | null)[]>([]); const [refresh, setRefresh] = useState(0);
  const refreshCandidates = useCallback(() => setRefresh((value) => value + 1), []);
  const [loading, setLoading] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ original: UserMemory | null; input: MemoryInput; aliasText: string } | null>(null);
  const [confirmation, setConfirmation] = useState<{ memory: UserMemory; action: "active" | "deleted"; replace?: UserMemory } | null>(null);
  useEffect(() => { onEditingChange?.(editor !== null || busy); return () => onEditingChange?.(false); }, [busy, editor, onEditingChange]);
  useEffect(() => {
    if (!editor) return;
    const listener = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", listener); return () => window.removeEventListener("beforeunload", listener);
  }, [editor]);
  useEffect(() => {
    const controller = new AbortController(); setError(null); setItems([]);
    if (scope !== "global" && !scopeId) { setLoading(false); return () => controller.abort(); }
    setLoading(true); void api.list(scope, scopeId, cursor, controller.signal).then((page) => { if (!controller.signal.aborted) { setItems(page.items); setNext(page.hasMore ? page.nextCursor : null); } }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "记忆读取失败。"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort();
  }, [api, cursor, refresh, scope, scopeId]);
  const act = async (operation: () => Promise<void>) => { setBusy(true); setError(null); try { await operation(); } catch (e) { setError(e instanceof Error ? e.message : "操作未完成，请重试。"); } finally { setBusy(false); } };
  const edit = (memory: UserMemory | null) => { setError(null); setEditor(memory ? { original: memory, input: { scope: memory.scope, scopeId: memory.scopeId, scene: memory.scene, kind: memory.kind, key: memory.key, value: memory.value, aliases: memory.aliases }, aliasText: memory.aliases.join("，") } : { original: null, input: { scope, scopeId, scene: "", kind: "term", key: "", value: "", aliases: [] }, aliasText: "" }); };
  const save = () => void act(async () => {
    if (!editor) return;
    const input = { ...editor.input, value: editor.input.value.trim(), key: editor.input.kind === "term" ? editor.input.value.trim() : editor.input.key, aliases: editor.input.kind === "term" ? aliases(editor.aliasText) : [] };
    if (input.aliases.length > 8 || input.aliases.some((x) => x.length > 64)) throw new Error("错误拼写最多 8 条，每条不超过 64 字。");
    if (editor.original) await api.update(editor.original.id, editor.original.revision, { value: input.value, aliases: input.aliases });
    else await api.create(input);
    setEditor(null); setNotice("已保存为待确认候选。原来已确认的值继续生效，请检查后明确确认。"); setRefresh((x) => x + 1);
  });
  const prepareConfirmation = (memory: UserMemory) => void act(async () => {
    let nextCursor: string | null = null; const seen = new Set<string>(); let current: UserMemory | undefined;
    do { const page = await api.list(memory.scope, memory.scopeId, nextCursor); current = page.items.find((x) => x.id !== memory.id && x.status === "active" && x.kind === memory.kind && x.key === memory.key && x.scene === memory.scene); nextCursor = page.hasMore ? page.nextCursor : null; if (nextCursor && seen.has(nextCursor)) throw new Error("分页无法继续，请刷新后重试。"); if (nextCursor) seen.add(nextCursor); } while (!current && nextCursor);
    setConfirmation({ memory, action: "active", ...(current ? { replace: current } : {}) });
  });
  const confirm = () => void act(async () => {
    if (!confirmation) return; const { memory, action, replace } = confirmation;
    await api.update(memory.id, memory.revision, { status: action, ...(replace ? { replaceActiveId: replace.id, replaceActiveRevision: replace.revision } : {}) });
    setConfirmation(null); setNotice(action === "deleted" ? "已删除。不会从同一旧来源自动重新创建。" : "已确认生效。术语标准写法用于后续识别与理解，错误拼写只用于修正参考。"); setRefresh((x) => x + 1); onChanged?.();
  });
  const exportMemories = () => void act(async () => { const data = await api.export(scope, scopeId); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const link = document.createElement("a"); link.href = url; link.download = `术语与偏好-${scope}.json`; link.click(); URL.revokeObjectURL(url); setNotice("已导出当前范围的术语与偏好。"); });
  const unavailable = scope !== "global" && !scopeId;
  const validEditor = editor && editor.input.value.trim() && (editor.input.kind === "term" || editor.input.key);
  return <section className="memory-panel" aria-label="术语与偏好">
    {reconcileApi && scope === "project" && projectId ? <MemoryReconcilePanel key={projectId} api={reconcileApi} projectId={projectId} onChanged={refreshCandidates} /> : null}
    <header><h2>术语与偏好</h2><p>只记录你明确确认的标准词和有限偏好。未确认候选、禁用项和过期来源不会应用。</p></header>
    <div className="memory-controls"><label>使用范围<select aria-label="记忆使用范围" disabled={busy || !!editor} value={scope} onChange={(event) => { setScope(event.target.value as MemoryScope); setCursor(null); setPrevious([]); setConfirmation(null); }}><option value="global">全局</option><option value="project" disabled={!projectId}>当前项目</option>{meetingId ? <option value="meeting">本场会议</option> : null}</select></label><button type="button" disabled={busy || loading || !!editor} onClick={() => setRefresh((x) => x + 1)}>刷新术语与偏好</button><button type="button" disabled={unavailable || busy || !!editor} onClick={() => edit(null)}>新增候选</button><button type="button" disabled={unavailable || busy} onClick={exportMemories}>导出本范围</button></div>
    <p className="knowledge-muted">本场设置优先于项目，项目优先于全局。同范围内当前场景优先于全部场景。本场主动建议频率以会前或会议目标页面的显式设置为准。</p>
    {error ? <p className="knowledge-alert" role="alert">{error} 内容已保留，请刷新并核对最新版本。</p> : null}{notice ? <p className="knowledge-notice" role="status">{notice}</p> : null}
    {unavailable ? <p>选择项目后，可管理该项目的术语与偏好。</p> : loading ? <p role="status">正在读取术语与偏好…</p> : !items.length ? <p>当前范围没有记忆。可以添加标准术语及常见错误拼写，或回答长度、主动建议频率偏好。</p> : items.map((memory) => <article className="memory-item" key={memory.id}><strong>{describe(memory)}</strong><p>{scopeLabels[memory.scope]} · {sceneLabels[memory.scene]} · {statusLabels[memory.status]}</p>{memory.kind === "term" && memory.aliases.length ? <p>常见错误拼写：{memory.aliases.join("、")}</p> : null}{memory.sourceRefs.length && projectApi ? <ProjectSources api={projectApi} sources={memory.sourceRefs} stale={memory.status === "needs_review"} onDocument={onDocument} /> : <p className="knowledge-muted">{memory.sourceRefs.length ? "关联资料来源，可在资料库核对。" : "由用户明确输入，无附加资料来源。"}</p>}{memory.status === "needs_review" ? <p className="knowledge-alert">原来源已变化，请核实新资料后重新添加候选；不会自动恢复旧值。</p> : null}<div className="memory-controls">{memory.status !== "active" ? <button type="button" disabled={busy || !!editor || memory.status === "needs_review"} onClick={() => prepareConfirmation(memory)}>确认使用</button> : <button type="button" disabled={busy || !!editor} onClick={() => void act(async () => { await api.update(memory.id, memory.revision, { status: "disabled" }); setRefresh((x) => x + 1); onChanged?.(); })}>禁用</button>}<button type="button" disabled={busy || !!editor || memory.status === "needs_review"} onClick={() => edit(memory)}>编辑为新候选</button><button type="button" disabled={busy || !!editor} onClick={() => setConfirmation({ memory, action: "deleted" })}>删除</button></div></article>)}
    <div className="knowledge-pagination"><button type="button" disabled={busy || !!editor || !previous.length} onClick={() => { setCursor(previous.at(-1) ?? null); setPrevious((x) => x.slice(0, -1)); }}>上一页记忆</button><span>第 {previous.length + 1} 页</span><button type="button" disabled={busy || !!editor || !next} onClick={() => { setPrevious((x) => [...x, cursor]); setCursor(next); }}>下一页记忆</button></div>
    {editor ? <section className="memory-editor" aria-label="编辑记忆候选"><h3>{editor.original ? "编辑后另存候选" : "新增记忆候选"}</h3><form onSubmit={(event) => { event.preventDefault(); save(); }}><label>类型<select aria-label="记忆候选类型" disabled={busy || !!editor.original} value={editor.input.kind} onChange={(event) => setEditor({ ...editor, aliasText: "", input: { ...editor.input, kind: event.target.value as MemoryInput["kind"], key: event.target.value === "term" ? "" : "response_length", value: event.target.value === "term" ? "" : "brief" } })}><option value="term">标准术语</option><option value="preference">回答与建议偏好</option></select></label><label>适用场景<select aria-label="记忆适用场景" disabled={busy || !!editor.original} value={editor.input.scene} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, scene: event.target.value as MemoryScene } })}>{Object.entries(sceneLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{editor.input.kind === "term" ? <><label>标准写法<input autoFocus maxLength={64} aria-label="术语标准写法" value={editor.input.value} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, value: event.target.value } })} /></label><label>常见错误拼写<textarea aria-label="术语错误拼写" placeholder="用逗号或换行分隔，最多 8 条" value={editor.aliasText} onChange={(event) => setEditor({ ...editor, aliasText: event.target.value })} /></label><p>标准写法用于识别提示；错误写法不会加入语音识别热词。</p></> : <><label>偏好项目<select aria-label="偏好项目" disabled={!!editor.original} value={editor.input.key} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, key: event.target.value, value: event.target.value === "response_length" ? "brief" : "low_frequency" } })}>{Object.entries(preferenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>偏好值<select aria-label="偏好值" value={editor.input.value} onChange={(event) => setEditor({ ...editor, input: { ...editor.input, value: event.target.value } })}>{(editor.input.key === "response_length" ? ["brief", "standard"] : ["off", "low_frequency", "standard"]).map((value) => <option key={value} value={value}>{valueLabels[value]}</option>)}</select></label></>}<div className="memory-controls"><button type="button" disabled={busy} onClick={() => setEditor(null)}>取消候选编辑</button><button type="submit" disabled={busy || !validEditor}>保存为待确认候选</button></div></form></section> : null}
    {confirmation ? <section className="memory-confirmation" role="dialog" aria-modal="false" aria-label="确认记忆变更"><h3>{confirmation.action === "deleted" ? "删除这条记忆？" : confirmation.replace ? "替换已确认的记忆？" : "确认使用这条记忆？"}</h3>{confirmation.replace ? <p>原值：{describe(confirmation.replace)}。新值：{describe(confirmation.memory)}。确认后原值禁用，新值生效。</p> : <p>{describe(confirmation.memory)}</p>}<div className="memory-controls"><button type="button" disabled={busy} onClick={() => setConfirmation(null)}>取消变更</button><button type="button" disabled={busy} onClick={confirm}>{confirmation.action === "deleted" ? "确定删除记忆" : confirmation.replace ? "确认替换原值" : "确认这条记忆生效"}</button></div></section> : null}
  </section>;
}

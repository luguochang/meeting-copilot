import { useEffect, useState } from "react";
import type { KnowledgeApi, KnowledgeDocument, KnowledgeFolder, KnowledgeProject, MeetingKnowledgeSelection } from "../../api/knowledge";
import "./knowledgeLibrary.css";

function documentAvailable(document: KnowledgeDocument) {
  return document.status === "active" && Boolean(document.activeVersionId)
    && document.sourceStatus !== "needs_review" && document.sourceStatus !== "deleted";
}
function documentStatusLabel(document: KnowledgeDocument) {
  if (document.sourceStatus === "needs_review") return "来源已修改，待复核";
  if (document.sourceStatus === "deleted") return "来源已删除，暂不可用";
  return documentAvailable(document) ? "已确认" : "暂不可用";
}

export function KnowledgeScopePicker({ api, value, onChange, disabled = false }: { api: KnowledgeApi; value: MeetingKnowledgeSelection; onChange: (value: MeetingKnowledgeSelection) => void; disabled?: boolean }) {
  const [projects, setProjects] = useState<KnowledgeProject[]>([]); const [folders, setFolders] = useState<KnowledgeFolder[]>([]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]); const [cursor, setCursor] = useState<string | null>(null); const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [previous, setPrevious] = useState<(string | null)[]>([]); const [error, setError] = useState<string | null>(null); const [loading, setLoading] = useState(false); const [refresh, setRefresh] = useState(0);
  const [knownDocuments, setKnownDocuments] = useState<Record<string, KnowledgeDocument>>({});
  const mode = value.documentIds === null ? "all" : value.documentIds.length ? "selected" : "none";
  const [explicit, setExplicit] = useState(value.documentIds !== null && value.documentIds.length > 0);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => { const items: KnowledgeProject[] = []; let next: string | null = null; const seen = new Set<string>(); do { const page = await api.listProjects(next, controller.signal); items.push(...page.items.filter((x) => x.archivedAtMs === null)); next = page.hasMore ? page.nextCursor : null; if (next && seen.has(next)) throw new Error("项目分页未能继续。"); if (next) seen.add(next); } while (next && !controller.signal.aborted); if (!controller.signal.aborted) setProjects(items); })().catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "项目加载失败"); });
    return () => controller.abort();
  }, [api, refresh]);
  useEffect(() => {
    setCursor(null); setPrevious([]); setFolders([]);
    if (!value.projectId) return;
    const controller = new AbortController();
    void (async () => { const items: KnowledgeFolder[] = []; let next: string | null = null; const seen = new Set<string>(); do { const page = await api.listFolders(value.projectId!, next, controller.signal); items.push(...page.items); next = page.hasMore ? page.nextCursor : null; if (next && seen.has(next)) throw new Error("文件夹分页未能继续。"); if (next) seen.add(next); } while (next && !controller.signal.aborted); if (!controller.signal.aborted) setFolders(items); })().catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "文件夹加载失败"); });
    return () => controller.abort();
  }, [api, value.projectId, refresh]);
  useEffect(() => {
    if (!value.projectId || !explicit) { setDocuments([]); return; }
    const controller = new AbortController(); setLoading(true); setDocuments([]); setNextCursor(null);
    void api.listDocuments({ projectId: value.projectId, folderId: value.folderId, cursor, limit: 25 }, controller.signal).then((page) => { if (controller.signal.aborted) return; setDocuments(page.items); setKnownDocuments((previous) => ({ ...previous, ...Object.fromEntries(page.items.map((document) => [document.id, document])) })); setNextCursor(page.hasMore ? page.nextCursor : null); }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "资料加载失败"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, cursor, explicit, refresh, value.folderId, value.projectId]);
  const setMode = (next: string) => { setExplicit(next === "selected"); onChange({ ...value, documentIds: next === "all" ? null : [] }); setCursor(null); setPrevious([]); };
  const outsidePage = (value.documentIds ?? []).filter((id) => !documents.some((document) => document.id === id));
  const remove = (id: string) => onChange({ ...value, documentIds: (value.documentIds ?? []).filter((selected) => selected !== id) });
  return <fieldset className="knowledge-scope-picker" disabled={disabled} aria-label="本场会议资料范围"><legend>本场会议资料</legend>
    <label>项目<select aria-label="会议资料项目" value={value.projectId ?? ""} onChange={(e) => { setExplicit(false); setError(null); onChange({ projectId: e.target.value || null, folderId: null, documentIds: null }); }}><option value="">不关联项目</option>{value.projectId && !projects.some((p) => p.id === value.projectId) ? <option value={value.projectId}>已选项目（请检查是否仍可用）</option> : null}{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
    {value.projectId ? <><label>文件夹范围<select aria-label="会议资料文件夹" value={value.folderId ?? ""} onChange={(e) => { onChange({ ...value, folderId: e.target.value || null, documentIds: explicit ? [] : value.documentIds === null ? null : [] }); setCursor(null); setPrevious([]); }}><option value="">整个项目</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label><label>使用资料<select aria-label="会议资料选择方式" value={explicit ? "selected" : mode} onChange={(e) => setMode(e.target.value)}><option value="all">范围内全部已确认资料</option><option value="selected">只用所选资料</option><option value="none">不使用资料</option></select></label><p>只使用已确认、启用且来源仍有效的版本。文件夹范围包含下级目录；待确认、待复核、禁用或删除的资料不会带入。</p>
      {explicit ? <><p role="status">已选择 {value.documentIds?.length ?? 0} 份{loading ? " · 正在加载" : ""}。失效的已选资料可取消，复核前不能重新加入。</p><div className="knowledge-scope-documents">{documents.map((document) => {
        const selected = value.documentIds?.includes(document.id) ?? false;
        const available = documentAvailable(document);
        return <label key={document.id}><input type="checkbox" checked={selected} disabled={loading || !available && !selected} onChange={(e) => { const ids = new Set(value.documentIds ?? []); if (e.target.checked) { if (!available || loading) return; ids.add(document.id); } else ids.delete(document.id); onChange({ ...value, documentIds: [...ids] }); }} /><span>{document.title}<small>{documentStatusLabel(document)}{selected && !available ? " · 请取消或先复核" : ""}</small></span></label>;
      })}</div>{outsidePage.length ? <div aria-label="当前页之外的所选资料"><p>另有 {outsidePage.length} 份所选资料不在当前页，或已从当前范围移除。可以逐项取消；此处不表示它们仍可用。</p>{outsidePage.slice(0, 25).map((id, index) => <p key={id}>{knownDocuments[id]?.title ?? `所选资料 ${index + 1}`}<button type="button" onClick={() => remove(id)}>取消这份资料</button></p>)}{outsidePage.length > 25 ? <p>先取消上述资料后会继续显示其余选择。</p> : null}</div> : null}<div className="knowledge-actions"><button type="button" disabled={!previous.length || loading} onClick={() => { setCursor(previous.at(-1) ?? null); setPrevious((x) => x.slice(0, -1)); }}>上一页资料</button><button type="button" disabled={!nextCursor || loading} onClick={() => { setPrevious((x) => [...x, cursor]); setCursor(nextCursor); }}>下一页资料</button><button type="button" disabled={loading} onClick={() => setRefresh((x) => x + 1)}>刷新资料状态</button><button type="button" disabled={!value.documentIds?.length} onClick={() => onChange({ ...value, documentIds: [] })}>清空所选资料</button></div></> : null}
    </> : <p>本次只使用会议文字，不带入项目资料。</p>}
    {error ? <p className="inline-error" role="alert">{error}<button type="button" onClick={() => { setError(null); setRefresh((x) => x + 1); }}>重新加载资料</button></p> : null}
  </fieldset>;
}

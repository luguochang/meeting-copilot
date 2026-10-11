import { useEffect, useState } from "react";
import type { KnowledgeApi, KnowledgeDocument, KnowledgeFolder, KnowledgeImportResult, KnowledgeProject } from "../../api/knowledge";
import { useModalDialog } from "../../components/useModalDialog";

type Source = { kind: "note"; id: string; version: number } | { kind: "final"; id: string; meetingId: string; version: number; documentKind: "minutes" | "decisions" | "action_items" | "risks" | "transcript" };
export function SourceLibraryImport({ api, source, onClose, onOpenDocument }: { api: KnowledgeApi; source: Source; onClose: () => void; onOpenDocument?: (target: { documentId: string; projectId: string }) => void }) {
  const [projectId, setProjectId] = useState<string | null>(null); const [folderId, setFolderId] = useState("");
  const [projects, setProjects] = useState<KnowledgeProject[]>([]); const [folders, setFolders] = useState<KnowledgeFolder[]>([]); const [linked, setLinked] = useState<KnowledgeDocument | null>(null);
  const [loading, setLoading] = useState(true); const [scopeLoading, setScopeLoading] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [reload, setReload] = useState(0); const [result, setResult] = useState<KnowledgeImportResult | null>(null);
  const dialogRef = useModalDialog(true, onClose, busy);
  const sourceKind = source.kind; const sourceId = source.id;
  const sourceMeetingId = source.kind === "final" ? source.meetingId : null;
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    void (async () => {
      const all: KnowledgeProject[] = []; let cursor: string | null = null; const seen = new Set<string>();
      do { const page = await api.listProjects(cursor, controller.signal); all.push(...page.items); cursor = page.hasMore ? page.nextCursor : null; if (cursor && seen.has(cursor)) throw new Error("项目分页无法继续，请重试。"); if (cursor) seen.add(cursor); } while (cursor && !controller.signal.aborted);
      if (controller.signal.aborted) return; setProjects(all.filter((p) => p.archivedAtMs === null));
      if (sourceKind === "final" && sourceMeetingId) { const context = await api.getMeetingContext(sourceMeetingId, controller.signal); if (!context.projectId) throw new Error("请先为本场会议保存项目资料范围，再加入资料库。"); if (!controller.signal.aborted) { setProjectId(context.projectId); setFolderId(context.folderId ?? ""); } }
    })().catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "入库项目读取失败。"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort();
  }, [api, reload, sourceKind, sourceMeetingId]);
  useEffect(() => {
    if (!projectId) { setFolders([]); setLinked(null); return; }
    const controller = new AbortController(); setScopeLoading(true); setError(null); setLinked(null); setResult(null);
    void (async () => {
      const directory: KnowledgeFolder[] = []; let cursor: string | null = null; const seen = new Set<string>();
      do { const page = await api.listFolders(projectId, cursor, controller.signal); directory.push(...page.items); cursor = page.hasMore ? page.nextCursor : null; if (cursor && seen.has(cursor)) throw new Error("文件夹分页无法继续。"); if (cursor) seen.add(cursor); } while (cursor && !controller.signal.aborted);
      if (controller.signal.aborted) return; setFolders(directory);
      cursor = null; seen.clear(); let found: KnowledgeDocument | null = null;
      do { const page = await api.listDocuments({ projectId, cursor, limit: 100 }, controller.signal); found = page.items.find((d) => d.sourceBinding?.sourceKind === (sourceKind === "note" ? "note" : "review_document") && d.sourceBinding.sourceId === sourceId) ?? null; cursor = page.hasMore ? page.nextCursor : null; if (cursor && seen.has(cursor)) throw new Error("资料分页无法继续。"); if (cursor) seen.add(cursor); } while (!found && cursor && !controller.signal.aborted);
      if (!controller.signal.aborted) setLinked(found);
    })().catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "资料范围读取失败。"); }).finally(() => { if (!controller.signal.aborted) setScopeLoading(false); }); return () => controller.abort();
  }, [api, projectId, reload, sourceId, sourceKind]);
  const submit = async () => {
    if (!projectId || loading || scopeLoading || busy) return; setBusy(true); setError(null);
    try {
      const input = { expectedSourceVersion: source.version, folderId: folderId || null, ...(linked ? { expectedDocumentRevision: linked.revision } : {}) };
      const value = source.kind === "note" ? await api.importNote?.(source.id, { ...input, projectId }) : await api.importFinal?.(source.meetingId, { ...input, documentKind: source.documentKind });
      if (!value) throw new Error("当前未提供来源入库功能。"); setResult(value);
    } catch (e) { setError(e instanceof Error ? e.message : "来源入库失败，请刷新版本后重试。"); }
    finally { setBusy(false); }
  };
  return <div className="knowledge-modal-backdrop"><section ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label="加入项目资料库" className="knowledge-modal"><h2>加入项目资料库</h2><p>导入当前已保存的第 {source.version} 版原文。先生成资料草稿，检查确认后才用于后续会议。</p>{error ? <p className="knowledge-alert" role="alert">{error}<button type="button" disabled={busy} onClick={() => setReload((x) => x + 1)}>刷新入库范围</button></p> : null}{loading || scopeLoading ? <p role="status">正在读取项目、文件夹与已有入库版本…</p> : null}<label>入库项目<select aria-label="入库项目" disabled={busy || loading || source.kind === "final" || !!result} value={projectId ?? ""} onChange={(event) => { setProjectId(event.target.value || null); setFolderId(""); }}><option value="">请选择项目</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><label>存放文件夹<select aria-label="入库文件夹" disabled={busy || scopeLoading || !!result} value={folderId} onChange={(event) => setFolderId(event.target.value)}><option value="">项目根目录</option>{folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}</select></label>{linked ? <p>已有资料「{linked.title}」。同一来源版重复导入不会覆盖修正文字；来源更新后会另存草稿，保留原确认版。</p> : null}{result ? <><p>下一步：到资料库核对文本并确认。项目事项与 Wiki 仍需独立审核，不会因入库自动更新。</p><p role="status">{result.alreadyImported ? "这一来源版已经入库，保留已有修正内容。" : "已保存为资料草稿，尚未用于会议。"}</p>{onOpenDocument ? <button type="button" onClick={() => onOpenDocument({ documentId: result.document.id, projectId: result.document.projectId })}>到资料库检查并确认</button> : null}</> : <button type="button" className="knowledge-primary" disabled={busy || loading || scopeLoading || !projectId || !!error || linked?.status === "disabled" || linked?.status === "deleted"} onClick={() => void submit()}>导入当前原文为草稿</button>}<button type="button" disabled={busy} onClick={onClose}>关闭入库窗口</button></section></div>;
}

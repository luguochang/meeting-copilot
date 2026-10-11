import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, FileText, Folder, FolderPlus, Library, LoaderCircle, Plus, RefreshCw, Upload } from "lucide-react";
import type { KnowledgeApi, KnowledgeDetail, KnowledgeDocument, KnowledgeFolder, KnowledgeJob, KnowledgeLocator, KnowledgeProject, KnowledgeSource, KnowledgeVersion, KnowledgeVersionMetadata } from "../../api/knowledge";
import { ProductNavigation, type ActiveMeetingNavigation } from "../../components/ProductNavigation";
import "./knowledgeLibrary.css";
import type { ProjectOutcomesApi, ProjectSourceRef } from "../../api/projectOutcomes";
import { ProjectWorkspace } from "../project-workspace/ProjectWorkspace";
import type { MemoryApi } from "../../api/memory";
import type { MemoryReconcileApi } from "../../api/memoryReconcile";
import { MemoryPanel } from "../memory/MemoryPanel";
import type { ProjectReconcileApi } from "../../api/projectReconcile";

interface Props {
  reconcileApi?: ProjectReconcileApi; initialProjectTab?: "state" | "proposals" | "wiki" | "jobs";
  initialDocumentId?: string | null;
  memoryApi?: MemoryApi;
  memoryReconcileApi?: MemoryReconcileApi;
  projectApi?: ProjectOutcomesApi;
  api: KnowledgeApi; onOpenMeetings: () => void; onOpenNotes: () => void; onOpenCapabilities: () => void;
  onOpenMeeting: (id: string) => void; activeMeeting?: ActiveMeetingNavigation | null; onOpenActiveMeeting?: () => void;
  initialSourceId?: string | null; initialProjectId?: string | null; initialMeetingId?: string | null; initialContextRevision?: number | null; initialVersionId?: string | null; initialContentHash?: string | null;
}
type FormKind = "project" | "project-edit" | "folder" | "folder-edit" | "file" | "text" | "document-edit";
function message(error: unknown): string { return error instanceof Error ? error.message : "操作未完成，请重试。"; }
function statusLabel(document: KnowledgeDocument): string {
  if (document.status === "deleted") return "已删除";
  if (document.status === "disabled") return "已禁用";
  if (document.sourceStatus === "needs_review") return "原来源已修改，待复核";
  if (document.sourceStatus === "deleted") return "原来源已删除，待复核";
  if (document.importStatus === "pending") return "正在转换";
  if (document.importStatus === "needs_ocr") return "扫描件需要文字识别";
  if (document.importStatus === "unavailable") return "转换能力暂不可用";
  if (document.importStatus === "failed") return "转换失败";
  return { draft: "待检查确认", active: "已确认可用", disabled: "已禁用", deleted: "已删除" }[document.status];
}
function sourceLocation(locator: KnowledgeLocator): string {
  const page = locator.page ?? locator.page_number;
  const paragraph = locator.paragraph ?? locator.paragraph_number;
  if (typeof page === "number") return `第 ${page} 页`;
  if (typeof paragraph === "number") return `第 ${paragraph} 段`;
  return "转换文本";
}
function progressLabel(progress: string): string {
  return ({ waiting: "等待转换", waiting_recovery: "等待恢复处理", extracting: "正在提取文字", cleaning: "正在清理原文件", cancelled: "已取消", extracted: "文字已提取", completed: "已完成", failed: "处理未完成", needs_ocr: "需要文字识别", unavailable: "转换能力暂不可用" } as Record<string, string>)[progress] ?? progress;
}
function inactiveJob(job: KnowledgeJob): boolean { return ["succeeded", "failed", "cancelled"].includes(job.status); }
function descendants(folders: KnowledgeFolder[], id: string): Set<string> {
  const ids = new Set([id]);
  for (let i = 0; i < folders.length; i += 1) folders.forEach((folder) => { if (folder.parentId && ids.has(folder.parentId)) ids.add(folder.id); });
  return ids;
}
function folderOptions(folders: KnowledgeFolder[]): { folder: KnowledgeFolder; depth: number }[] {
  const results: { folder: KnowledgeFolder; depth: number }[] = []; const visited = new Set<string>();
  const walk = (parent: string | null, depth: number) => folders.filter((x) => x.parentId === parent).forEach((folder) => { if (visited.has(folder.id)) return; visited.add(folder.id); results.push({ folder, depth }); walk(folder.id, depth + 1); });
  walk(null, 0); folders.forEach((folder) => { if (!visited.has(folder.id)) results.push({ folder, depth: 0 }); }); return results;
}

export function KnowledgeLibrary({ reconcileApi, initialProjectTab, initialDocumentId, api, projectApi, memoryApi, memoryReconcileApi, onOpenMeetings, onOpenNotes, onOpenCapabilities, onOpenMeeting, activeMeeting, onOpenActiveMeeting, initialSourceId, initialProjectId, initialMeetingId, initialContextRevision, initialVersionId, initialContentHash }: Props) {
  const [libraryView, setLibraryView] = useState<"documents" | "project" | "memories">(initialProjectTab ? "project" : "documents");
  const [projectView, setProjectView] = useState<"state" | "proposals" | "wiki" | "jobs">(initialProjectTab ?? "state");
  const [projectEditing, setProjectEditing] = useState(false);
  const [projects, setProjects] = useState<KnowledgeProject[]>([]); const [projectReload, setProjectReload] = useState(0); const [projectCursor, setProjectCursor] = useState<string | null>(null);
  const [projectId, setProjectId] = useState<string | null>(initialProjectId ?? null); const [showArchived, setShowArchived] = useState(false);
  const [folders, setFolders] = useState<KnowledgeFolder[]>([]); const [folderId, setFolderId] = useState<string | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]); const [cursor, setCursor] = useState<string | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null); const [previousCursors, setPreviousCursors] = useState<(string | null)[]>([]);
  const [query, setQuery] = useState(""); const [search, setSearch] = useState(""); const [refresh, setRefresh] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(initialDocumentId ?? null); const [detail, setDetail] = useState<KnowledgeDetail | null>(null);
  const [version, setVersion] = useState<KnowledgeVersion | null>(null); const [versions, setVersions] = useState<KnowledgeVersionMetadata[]>([]); const [moreVersions, setMoreVersions] = useState(false);
  const [content, setContent] = useState(""); const [dirty, setDirty] = useState(false); const [job, setJob] = useState<KnowledgeJob | null>(null);
  const [loading, setLoading] = useState(true); const [detailLoading, setDetailLoading] = useState(false); const [detailReload, setDetailReload] = useState(0); const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null); const [notice, setNotice] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [form, setForm] = useState<FormKind | null>(null); const [name, setName] = useState(""); const [description, setDescription] = useState("");
  const [targetFolder, setTargetFolder] = useState(""); const [file, setFile] = useState<File | null>(null); const [text, setText] = useState("");
  const [manualKind, setManualKind] = useState<"note" | "meeting_document">("note"); const [sourceMeetingId, setSourceMeetingId] = useState("");
  const [confirmation, setConfirmation] = useState<{ text: string; action: () => void } | null>(null);
  const [source, setSource] = useState<KnowledgeSource | null>(null);
  const pendingSourceRef = useRef<KnowledgeSource | null>(null);
  const selectedRef = useRef(selectedId); selectedRef.current = selectedId; const dirtyRef = useRef(dirty); dirtyRef.current = dirty;
  const project = projects.find((x) => x.id === projectId); const folder = folders.find((x) => x.id === folderId); const orderedFolders = useMemo(() => folderOptions(folders), [folders]);
  const editFolderExcluded = form === "folder-edit" && folderId ? descendants(folders, folderId) : new Set<string>();
  const previewSourceStale = (version?.sourceStatus ?? detail?.document.sourceStatus ?? "current") !== "current";
  const guard = (action: () => void) => { if (projectEditing) { setNotice("请先保存或取消项目事项编辑，再切换页面。"); return; } if (busy) { setNotice("当前操作完成后再切换。"); return; } if (dirty) setConfirmation({ text: "还有未保存的资料修改。离开会丢弃这些修改。", action: () => { setDirty(false); setContent(version?.content ?? ""); action(); } }); else action(); };
  const openProjectDocument = (value: ProjectSourceRef) => guard(() => {
    if (value.sourceType !== "document" || !value.documentId || !value.versionId || value.ordinal === undefined) return;
    pendingSourceRef.current = { sourceType: "document", sourceId: value.sourceId, documentId: value.documentId, projectId: value.projectId, title: value.title ?? "项目资料", versionId: value.versionId, version: value.version, contentHash: value.contentHash, ordinal: value.ordinal, quote: value.quote, locator: { ...value.locator, type: typeof value.locator.type === "string" ? value.locator.type : "converted_text" } };
    setLibraryView("documents"); setProjectId(value.projectId); setSelectedId(value.documentId); setDetailReload((x) => x + 1);
  });
  const resetPage = () => { setCursor(null); setPreviousCursors([]); };
  const refreshList = () => setRefresh((x) => x + 1);
  const installDetail = useCallback((value: KnowledgeDetail, currentVersion: KnowledgeVersion | null) => { setDetail(value); setVersion(currentVersion); setContent(currentVersion?.content ?? ""); setDirty(false); setSource(null); }, []);

  useEffect(() => {
    const controller = new AbortController();
    void api.listProjects(null, controller.signal).then((page) => { if (controller.signal.aborted) return; setProjects(page.items); setProjectCursor(page.hasMore ? page.nextCursor : null); setProjectId((id) => id ?? page.items.find((x) => x.archivedAtMs === null)?.id ?? null); }).catch((e) => { if (!controller.signal.aborted) setError(message(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, projectReload]);
  useEffect(() => {
    if (!form && !confirmation) return;
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) { setForm(null); setConfirmation(null); }
      if (event.key !== "Tab") return;
      const dialog = document.querySelector<HTMLElement>(".knowledge-modal-backdrop [role=dialog]");
      const elements = dialog?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled)");
      const first = elements?.[0]; const last = elements?.[elements.length - 1];
      if (event.shiftKey && document.activeElement === first && last) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last && first) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [busy, confirmation, form]);
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);
  useEffect(() => { const timer = window.setTimeout(() => setSearch(query.trim()), 200); return () => window.clearTimeout(timer); }, [query]);
  useEffect(() => {
    if (!projectId) { setFolders([]); return; }
    const controller = new AbortController();
    void (async () => { const all: KnowledgeFolder[] = []; let next: string | null = null; const cursors = new Set<string>(); do { const page = await api.listFolders(projectId, next, controller.signal); all.push(...page.items); next = page.hasMore ? page.nextCursor : null; if (next && cursors.has(next)) throw new Error("目录分页未能继续，请刷新资料库。"); if (next) cursors.add(next); } while (next && !controller.signal.aborted); if (!controller.signal.aborted) setFolders(all); })().catch((e) => { if (!controller.signal.aborted) setError(message(e)); });
    return () => controller.abort();
  }, [api, projectId, refresh]);
  useEffect(() => {
    if (!projectId) { setDocuments([]); return; }
    const controller = new AbortController(); setLoading(true);
    void api.listDocuments({ projectId, folderId, query: search, cursor, limit: 25 }, controller.signal).then((page) => { if (controller.signal.aborted) return; setDocuments(page.items); setNextCursor(page.hasMore ? page.nextCursor : null); }).catch((e) => { if (!controller.signal.aborted) setError(message(e)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, cursor, folderId, projectId, refresh, search]);
  useEffect(() => {
    if (!selectedId) { setDetail(null); setVersion(null); setContent(""); setVersions([]); setJob(null); return; }
    const controller = new AbortController(); setDetailLoading(true); setError(null); setJob(null); setDetail(null); setVersion(null); setVersions([]); setMoreVersions(false); setSource(null);
    void (async () => {
      const value = await api.getDocument(selectedId, controller.signal);
      const linkedSource = pendingSourceRef.current?.documentId === selectedId ? pendingSourceRef.current : null;
      const targetVersionId = linkedSource?.versionId ?? value.latestDraftVersionId;
      const currentVersion = targetVersionId && targetVersionId !== value.version?.id ? await api.getVersion(selectedId, targetVersionId, controller.signal) : value.version;
      if (controller.signal.aborted) return; installDetail(value, currentVersion); if (linkedSource) { setSource(linkedSource); pendingSourceRef.current = null; }
      const history = await api.listVersions(selectedId); if (controller.signal.aborted) return; setVersions(history.items); setMoreVersions(history.hasMore);
      if (value.document.importStatus === "pending" || ["failed", "unavailable", "needs_ocr"].includes(value.document.importStatus)) {
        const jobs = await api.listJobs(value.document.projectId, selectedId, controller.signal); if (!controller.signal.aborted) setJob(jobs.items.find((x) => x.documentId === selectedId && x.type === "import") ?? null);
      }
    })().catch((e) => { if (!controller.signal.aborted) setError(message(e)); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [api, detailReload, installDetail, selectedId]);
  useEffect(() => {
    if (!job || inactiveJob(job)) return;
    const controller = new AbortController(); let running = false;
    const timer = window.setInterval(() => {
      if (running) return; running = true;
      void api.getJob(job.id, controller.signal).then(async (next) => {
        if (controller.signal.aborted) return; setJob(next);
        if (inactiveJob(next)) {
          refreshList();
          if (selectedRef.current === next.documentId && !dirtyRef.current) { const value = await api.getDocument(next.documentId, controller.signal); const nextVersion = value.latestDraftVersionId && value.latestDraftVersionId !== value.version?.id ? await api.getVersion(next.documentId, value.latestDraftVersionId, controller.signal) : value.version; if (!controller.signal.aborted && selectedRef.current === next.documentId && !dirtyRef.current) installDetail(value, nextVersion); }
        }
      }).catch((e) => { if (!controller.signal.aborted) setError(message(e)); }).finally(() => { running = false; });
    }, 2_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [api, installDetail, job]);
  useEffect(() => {
    if (!initialSourceId || !initialProjectId) return;
    const controller = new AbortController();
    void (async () => {
      const value = initialMeetingId ? await api.getMeetingSource(initialSourceId, initialMeetingId, controller.signal) : await api.getSource(initialSourceId, initialProjectId, controller.signal);
      if (initialMeetingId && initialContextRevision !== null && initialContextRevision !== undefined) {
        const context = await api.getMeetingContext(initialMeetingId, controller.signal);
        if (context.revision !== initialContextRevision) throw new Error("这条历史引用已过期：本场资料范围已变化。请重新提问，或单独核对资料库。");
      }
      if ((initialVersionId && value.versionId !== initialVersionId) || (initialContentHash && value.contentHash !== initialContentHash)) throw new Error("资料版本已经变化，旧引用已过期，未替换为新版本。");
      if (controller.signal.aborted) return; pendingSourceRef.current = value; setProjectId(value.projectId); setSelectedId(value.documentId);
    })().catch((e) => { if (!controller.signal.aborted) setError(`无法打开引用的原版本：${message(e)}`); });
    return () => controller.abort();
  }, [api, initialContentHash, initialContextRevision, initialMeetingId, initialProjectId, initialSourceId, initialVersionId]);

  const act = async (action: () => Promise<void>) => { setBusy(true); setError(null); try { await action(); } catch (e) { setError(message(e)); } finally { setBusy(false); } };
  const exportProject = () => {
    if (!projectId || !api.exportProject || busy || dirty || projectEditing) return;
    setExporting(true);
    void act(async () => {
      try { await api.exportProject!(projectId); setNotice("已交给浏览器下载。ZIP 包含已确认文稿、项目事项、Wiki 和来源目录；请到下载文件夹查看。"); }
      finally { setExporting(false); }
    });
  };
  const openForm = (kind: FormKind) => {
    setName(kind === "project-edit" ? project?.name ?? "" : kind === "folder-edit" ? folder?.name ?? "" : kind === "document-edit" ? detail?.document.title ?? "" : "");
    setDescription(kind === "project-edit" ? project?.description ?? "" : ""); setTargetFolder(kind === "folder-edit" ? folder?.parentId ?? "" : kind === "document-edit" ? detail?.document.folderId ?? "" : folderId ?? ""); setFile(null); setText(""); setSourceMeetingId(""); setError(null); setForm(kind);
  };
  const submitForm = async () => {
    await act(async () => {
      if (form === "project") { const value = await api.createProject(name.trim(), description.trim()); setProjects((x) => [...x, value]); setProjectId(value.id); setFolderId(null); setSelectedId(null); resetPage(); }
      else if (form === "project-edit" && project) { const value = await api.updateProject(project.id, project.revision, { name: name.trim(), description: description.trim() }); setProjects((x) => x.map((p) => p.id === value.id ? value : p)); }
      else if (form === "folder" && projectId) { const value = await api.createFolder(projectId, name.trim(), targetFolder || null); setFolders((x) => [...x, value]); setFolderId(value.id); resetPage(); }
      else if (form === "folder-edit" && folder) { const value = await api.updateFolder(folder.id, folder.revision, { name: name.trim(), parentId: targetFolder || null }); setFolders((x) => x.map((f) => f.id === value.id ? value : f)); }
      else if (form === "file" && projectId && file) { const value = await api.importDocument(file, projectId, targetFolder || null, name.trim() || undefined); setSelectedId(value.document.id); setJob(value.job); setNotice("已加入转换队列。完成后请检查文本并确认。"); resetPage(); refreshList(); }
      else if (form === "text" && projectId) { const value = await api.createDocument({ projectId, folderId: targetFolder || null, title: name.trim(), content: text, kind: manualKind, sourceMeetingId: manualKind === "meeting_document" ? sourceMeetingId.trim() || null : null }); setSelectedId(value.document.id); setNotice("文本已保存为待确认版本。"); resetPage(); refreshList(); }
      else if (form === "document-edit" && detail) { const value = await api.updateDocument(detail.document.id, detail.document.revision, { title: name.trim(), folderId: targetFolder || null }); setDetail({ ...detail, document: value }); refreshList(); }
      else throw new Error("请先选择项目与资料。");
      setForm(null);
    });
  };
  const save = async (confirm: boolean) => act(async () => {
    if (!detail || !version || !content.trim()) throw new Error("请先补全资料文字。");
    let currentDocument = detail.document; let currentVersion = version;
    if (dirty) { const saved = await api.saveVersion(currentDocument.id, content, currentDocument.revision, currentVersion.id); currentDocument = saved.document; currentVersion = saved.version; setDetail({ ...detail, document: currentDocument, latestDraftVersionId: currentVersion.id }); setVersion(currentVersion); setDirty(false); }
    if (confirm) { currentDocument = await api.confirmVersion(currentDocument.id, currentVersion.id, currentDocument.revision); currentVersion = await api.getVersion(currentDocument.id, currentVersion.id); setDetail({ ...detail, document: currentDocument, version: currentVersion, latestDraftVersionId: null }); setVersion(currentVersion); setNotice("已确认。这一版可用于所选项目的后续会议。"); }
    else setNotice("修改已保存为待确认版本，原确认版继续保留。");
    const history = await api.listVersions(currentDocument.id); setVersions(history.items); setMoreVersions(history.hasMore); refreshList();
  });
  const updateStatus = (status: "active" | "disabled" | "deleted") => act(async () => {
    if (!detail) return; const value = await api.updateDocument(detail.document.id, detail.document.revision, { status });
    if (status === "deleted") { setSelectedId(null); setNotice("资料已删除，后续不会再使用它。原文件清理由后台执行。"); }
    else { setDetail({ ...detail, document: value }); setNotice(status === "disabled" ? "资料已禁用，后续不会再使用它。" : "资料已重新启用。"); }
    refreshList();
  });
  const selectVersion = (id: string) => guard(() => void act(async () => { if (!detail) return; const value = await api.getVersion(detail.document.id, id); setVersion(value); setContent(value.content); setDirty(false); setSource(null); }));
  const locate = (spanId: string) => void act(async () => {
    if (!detail || !version) return;
    if (version.id === detail.document.activeVersionId && detail.document.status === "active" && project?.archivedAtMs === null) {
      setSource(await api.getSource(spanId, detail.document.projectId));
    } else {
      const span = version.spans.find((x) => x.id === spanId); if (!span) throw new Error("当前版本中未找到这一段原文。");
      setSource({ sourceType: "document", sourceId: span.id, documentId: detail.document.id, projectId: detail.document.projectId, title: detail.document.title, versionId: version.id, version: version.number, contentHash: version.contentHash, ordinal: span.ordinal, quote: span.text, locator: span.locator });
    }
  });
  const titleMap: Record<FormKind, string> = { project: "新建项目", "project-edit": "编辑项目", folder: "新建文件夹", "folder-edit": "编辑文件夹", file: "导入文件", text: "添加文本资料", "document-edit": "编辑资料信息" };
  const formValid = form === "file" ? Boolean(file) : Boolean(name.trim()) && (form !== "text" || Boolean(text.trim()));
  const historical = Boolean(version && detail?.document.activeVersionId && version.id !== detail.document.activeVersionId && version.confirmedAtMs !== null);

  return <div className="product-app product-app--knowledge">
    <ProductNavigation active="knowledge" onOpenMeetings={() => guard(onOpenMeetings)} onOpenNotes={() => guard(onOpenNotes)} onOpenCapabilities={() => guard(onOpenCapabilities)} activeMeeting={activeMeeting} onOpenActiveMeeting={onOpenActiveMeeting ? () => guard(onOpenActiveMeeting) : undefined} />
    <div className="knowledge-shell">
      <header className="knowledge-header"><div><span className="eyebrow">本地资料</span><h1><Library size={25} />资料库</h1><p>按项目整理资料。转换内容经你检查确认后，才能作为后续会议的背景。</p></div><button type="button" onClick={() => openForm("project")}><Plus size={16} />新建项目</button></header>
      {error && !form ? <div className="knowledge-alert" role="alert">{error}<button type="button" onClick={() => guard(() => { setError(null); setProjectReload((x) => x + 1); setDetailReload((x) => x + 1); refreshList(); })}>刷新重试</button></div> : null}
      {notice ? <div className="knowledge-notice" role="status">{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button></div> : null}
      {projectApi || memoryApi ? <nav className="knowledge-view-tabs" aria-label="资料库工作视图"><button type="button" aria-pressed={libraryView === "documents"} onClick={() => guard(() => setLibraryView("documents"))}>资料</button>{projectApi ? <button type="button" aria-pressed={libraryView === "project"} onClick={() => guard(() => setLibraryView("project"))}>项目状态与 Wiki</button> : null}{memoryApi ? <button type="button" aria-pressed={libraryView === "memories"} onClick={() => guard(() => setLibraryView("memories"))}>术语与偏好</button> : null}</nav> : null}
      <main className="knowledge-layout">
        <aside className="knowledge-tree" aria-label="项目与文件夹">
          <label>项目<select aria-label="选择项目" value={projectId ?? ""} onChange={(e) => guard(() => { setProjectId(e.target.value || null); setFolderId(null); setSelectedId(null); setFolders([]); setDocuments([]); resetPage(); })}><option value="">选择项目</option>{projects.filter((x) => showArchived || x.archivedAtMs === null).map((x) => <option key={x.id} value={x.id}>{x.name}{x.archivedAtMs !== null ? "（已归档）" : ""}</option>)}</select></label>
          <label className="knowledge-check"><input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />显示归档项目</label>
          {projectCursor ? <button type="button" disabled={busy} onClick={() => void act(async () => { const page = await api.listProjects(projectCursor); setProjects((x) => [...x, ...page.items]); setProjectCursor(page.hasMore ? page.nextCursor : null); })}>加载更多项目</button> : null}
          {project ? <><div className="knowledge-actions"><button type="button" onClick={() => openForm("project-edit")}>编辑项目</button><button type="button" disabled={busy} onClick={() => setConfirmation({ text: project.archivedAtMs === null ? "归档此项目？资料会保留。" : "恢复此项目？", action: () => void act(async () => { const value = await api.updateProject(project.id, project.revision, { archived: project.archivedAtMs === null }); setProjects((x) => x.map((p) => p.id === value.id ? value : p)); setShowArchived(true); }) })}>{project.archivedAtMs === null ? "归档" : "恢复"}</button></div>{project.description ? <p className="knowledge-muted">{project.description}</p> : null}
            {api.exportProject ? <><button type="button" disabled={busy || dirty || projectEditing} onClick={exportProject}><Download size={15} />{exporting ? "正在导出…" : "导出项目资料"}</button><p className="knowledge-muted">导出整个项目的已确认文本、事项、Wiki 与来源目录（ZIP）。草稿和未确认提议不在其中。{dirty || projectEditing ? "请先保存或取消当前编辑，再导出。" : ""}</p></> : null}
            <div className="knowledge-tree-heading"><strong>文件夹</strong><button type="button" disabled={project.archivedAtMs !== null} onClick={() => openForm("folder")} aria-label="新建文件夹"><FolderPlus size={17} /></button></div>
            <button type="button" className={`knowledge-folder${folderId === null ? " is-selected" : ""}`} onClick={() => guard(() => { setFolderId(null); resetPage(); })}><Folder size={16} />项目全部资料</button>
            {orderedFolders.map(({ folder: item, depth }) => <button key={item.id} type="button" className={`knowledge-folder${folderId === item.id ? " is-selected" : ""}`} style={{ paddingLeft: `${12 + depth * 14}px` }} onClick={() => guard(() => { setFolderId(item.id); resetPage(); })}><Folder size={16} />{item.name}</button>)}
            {folder ? <button type="button" onClick={() => openForm("folder-edit")}>改名或移动当前文件夹</button> : null}</> : <p className="knowledge-muted">新建或选择项目后，可以整理不同主题的资料。</p>}
        </aside>
        {libraryView === "memories" && memoryApi ? <MemoryPanel key={projectId ?? "global"} api={memoryApi} reconcileApi={memoryReconcileApi} projectApi={projectApi} projectId={projectId} onDocument={openProjectDocument} onEditingChange={setProjectEditing} /> : libraryView === "project" && projectApi ? projectId ? <ProjectWorkspace key={projectId} api={projectApi} reconcileApi={reconcileApi} initialView={projectView} projectId={projectId} archived={project?.archivedAtMs !== null} onDocument={openProjectDocument} onMeeting={(id) => guard(() => onOpenMeeting(id))} onEditingChange={setProjectEditing} /> : <section className="project-workspace"><p>选择项目后查看已确认事项、候选和 Wiki。</p></section> : <><section className="knowledge-index" aria-label="资料列表">
          <div className="knowledge-list-controls"><h2>{folder?.name ?? "项目资料"}</h2><div className="knowledge-actions"><button type="button" disabled={!projectId || project?.archivedAtMs !== null} onClick={() => guard(() => openForm("file"))}><Upload size={15} />导入文件</button><button type="button" disabled={!projectId || project?.archivedAtMs !== null} onClick={() => guard(() => openForm("text"))}><Plus size={15} />添加文本</button><button type="button" aria-label="刷新资料" onClick={refreshList}><RefreshCw size={15} /></button></div><input aria-label="搜索资料" placeholder="搜索资料标题" value={query} onChange={(e) => { setQuery(e.target.value); resetPage(); }} /></div>
          {loading ? <p className="knowledge-muted" role="status"><LoaderCircle className="spin" size={16} />正在加载资料</p> : !documents.length ? <p className="knowledge-muted">没有匹配资料。可导入 TXT、MD、DOCX 或文本 PDF，也可粘贴已修正的会议文稿。</p> : null}
          <div className="knowledge-document-list">{documents.map((item) => <button type="button" key={item.id} className={`knowledge-document${selectedId === item.id ? " is-selected" : ""}`} onClick={() => guard(() => { setSelectedId(item.id); setSource(null); })}><FileText size={18} /><span><strong>{item.title}</strong><small>{statusLabel(item)}{item.filename ? ` · ${item.filename}` : ""}</small></span></button>)}</div>
          <div className="knowledge-pagination"><button type="button" disabled={!previousCursors.length || loading} onClick={() => { setCursor(previousCursors.at(-1) ?? null); setPreviousCursors((x) => x.slice(0, -1)); }}>上一页</button><span>第 {previousCursors.length + 1} 页</span><button type="button" disabled={!nextCursor || loading} onClick={() => { setPreviousCursors((x) => [...x, cursor]); setCursor(nextCursor); }}>下一页</button></div>
        </section>
        <section className="knowledge-editor" aria-label="资料预览与确认">
          {detailLoading ? <p role="status">正在读取资料…</p> : !detail ? <div className="knowledge-empty"><FileText size={28} /><p>选择一份资料，检查转换文本与版本。</p></div> : <>
            <header className="knowledge-editor-heading"><div><h2>{detail.document.title}</h2><p>{statusLabel(detail.document)}{detail.document.filename ? ` · ${detail.document.filename}` : ""}</p></div><button type="button" disabled={busy || project?.archivedAtMs !== null} onClick={() => openForm("document-edit")}>改名 / 移动</button></header>
            <div className="knowledge-actions"><button type="button" disabled={busy || project?.archivedAtMs !== null || detail.document.status === "deleted" || !detail.document.activeVersionId} onClick={() => guard(() => void updateStatus(detail.document.status === "disabled" ? "active" : "disabled"))}>{detail.document.status === "disabled" ? "重新启用" : "禁用资料"}</button><button type="button" disabled={busy || project?.archivedAtMs !== null || detail.document.status === "deleted"} onClick={() => setConfirmation({ text: "删除这份资料及其所有版本？删除后不再用于后续会议。", action: () => { setDirty(false); void updateStatus("deleted"); } })}>删除资料</button>{detail.document.sourceMeetingId ? <button type="button" onClick={() => guard(() => onOpenMeeting(detail.document.sourceMeetingId!))}>打开来源会议</button> : null}</div>
            {job ? <div className="knowledge-job" role="status"><strong>{job.status === "queued" ? "等待转换" : job.status === "running" ? "正在转换" : job.status === "succeeded" ? "转换已完成" : job.status === "cancelled" ? "转换已取消" : "转换未完成"}</strong><span>{progressLabel(job.progress)}</span>{job.errorMessage ? <span>{job.errorMessage}</span> : null}{job.status === "failed" || job.status === "cancelled" ? <button type="button" disabled={busy} onClick={() => void act(async () => { setJob(await api.retryJob(job.id)); setDetailReload((x) => x + 1); refreshList(); })}>重试转换</button> : !inactiveJob(job) ? <button type="button" disabled={busy} onClick={() => void act(async () => { setJob(await api.cancelJob(job.id)); setDetailReload((x) => x + 1); refreshList(); })}>取消转换</button> : null}</div> : null}
            {["needs_ocr", "unavailable", "failed"].includes(detail.document.importStatus) ? <p className="knowledge-alert">{detail.document.importStatus === "needs_ocr" ? "这份 PDF 没有可提取的文字，可能是扫描件。请提供文字版文件，或将识别出的内容粘贴为文本资料。" : detail.document.importStatus === "unavailable" ? "当前未能使用该格式的转换能力。请稍后重试，或改用 TXT / MD 文本。" : "文件转换失败。可以重试，或改用文字版文件。"}</p> : null}
            {version ? <>{previewSourceStale ? <p className="knowledge-alert" role="alert">这版资料的原来源已修改或删除，不能确认使用。请回到来源笔记或会议核对并重新入库，旧版保留供比较。</p> : null}
              <label className="knowledge-version-select">查看版本<select aria-label="查看资料版本" value={version.id} onChange={(e) => selectVersion(e.target.value)}>{!versions.some((x) => x.id === version.id) ? <option value={version.id}>第 {version.number} 版</option> : null}{versions.map((x) => <option key={x.id} value={x.id}>第 {x.number} 版 · {x.id === detail.document.activeVersionId ? "当前确认版" : x.confirmedAtMs !== null ? "历史确认版" : "待确认"}</option>)}</select>{moreVersions ? <button type="button" disabled={busy} onClick={() => void act(async () => { const page = await api.listVersions(detail.document.id, Math.min(...versions.map((x) => x.number))); setVersions((x) => [...x, ...page.items]); setMoreVersions(page.hasMore); })}>更早版本</button> : null}</label>
              <p className="knowledge-muted">{project?.archivedAtMs !== null ? "此项目已归档，恢复后可修改或确认资料。" : detail.document.status === "disabled" ? "资料已禁用，重新启用后才能确认新版本。" : ""}{historical ? "正在查看历史确认版。修改会另存为新版本；原版保留。" : version.id === detail.document.activeVersionId ? "当前确认版。修改会保存为新草稿，不覆盖这一版。" : "待确认版本。检查人名、数字和遗漏，再确认使用。"}{dirty ? " 有未保存修改。" : ""}</p>
              <textarea aria-label="转换文本" value={content} disabled={busy || project?.archivedAtMs !== null || detail.document.status === "deleted"} onChange={(e) => { setContent(e.target.value); setDirty(e.target.value !== version.content); }} />
              {detail.document.status === "active" && version.id === detail.document.activeVersionId && !dirty && !previewSourceStale ? <section className="knowledge-reuse-next" aria-label="已确认资料的下一步">
                <p>这版资料已确认可用。它是会议背景，项目决策与待办仍需单独确认后才进入 Wiki。</p>
                <p>下次开会时选择同一项目和需要的文件夹或资料；会前可查看上次决定与未完成待办。</p>
                {projectApi ? <button type="button" onClick={() => guard(() => { setProjectView("wiki"); setLibraryView("project"); })}>查看本项目 Wiki</button> : null}
              </section> : null}
              <div className="knowledge-actions"><button type="button" disabled={busy || project?.archivedAtMs !== null || !dirty || !content.trim() || detail.document.status === "deleted"} onClick={() => void save(false)}>保存修改为草稿</button><button type="button" className="knowledge-primary" disabled={busy || project?.archivedAtMs !== null || detail.document.status === "disabled" || !content.trim() || detail.document.status === "deleted" || previewSourceStale || (!dirty && version.id === detail.document.activeVersionId)} onClick={() => void save(true)}>{busy ? "正在保存…" : "确认这一版可用"}</button></div>
              {version.spans.length ? <details className="knowledge-sources"><summary>原文定位 · {version.spans.length} 段</summary>{version.spans.map((span) => <button type="button" key={span.id} disabled={busy} onClick={() => locate(span.id)}><strong>{sourceLocation(span.locator)} · 段落 {span.ordinal + 1}</strong><span>{span.text}</span></button>)}</details> : null}
              {source ? <section className="knowledge-source" aria-label="原文来源"><h3>{source.title} · 第 {source.version} 版 · {sourceLocation(source.locator)}</h3><blockquote>{source.quote}</blockquote><p>{detail.document.status === "disabled" ? "资料已禁用，后续不会使用这一来源。" : source.versionId === detail.document.activeVersionId && project?.archivedAtMs === null ? "当前确认版来源" : "历史或待确认来源，请核对有效版本。"}</p></section> : null}
            </> : detail.document.importStatus !== "pending" ? <p className="knowledge-muted">尚无可检查的转换文字。</p> : null}
          </>}
        </section></>}
      </main>
    </div>
    {form ? <div className="knowledge-modal-backdrop"><section role="dialog" aria-modal="true" aria-label={titleMap[form]} className="knowledge-modal"><h2>{titleMap[form]}</h2>{error ? <p className="knowledge-alert" role="alert">{error}</p> : null}<form onSubmit={(e) => { e.preventDefault(); void submitForm(); }}>
      <label>{form === "file" ? "资料标题（可选）" : "名称"}<input autoFocus aria-label={form === "file" ? "资料标题（可选）" : "名称"} maxLength={200} value={name} onChange={(e) => setName(e.target.value)} /></label>
      {form === "project" || form === "project-edit" ? <label>项目说明<textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={2000} /></label> : <label>{form === "folder" || form === "folder-edit" ? "上级文件夹" : "存放文件夹"}<select aria-label={form === "folder" || form === "folder-edit" ? "上级文件夹" : "存放文件夹"} value={targetFolder} onChange={(e) => setTargetFolder(e.target.value)}><option value="">项目根目录</option>{orderedFolders.filter((x) => !editFolderExcluded.has(x.folder.id)).map(({ folder: item, depth }) => <option key={item.id} value={item.id}>{"　".repeat(depth)}{item.name}</option>)}</select></label>}
      {form === "file" ? <><label>选择文件<input type="file" aria-label="选择资料文件" accept=".txt,.md,.docx,.pdf" onChange={(e) => { const candidate = e.target.files?.[0] ?? null; if (candidate && candidate.size > 20 * 1024 * 1024) { setFile(null); setError("文件超过 20 MiB，请拆分后导入。"); } else { setFile(candidate); setError(null); } }} /></label><p>支持 TXT、MD、DOCX 和含文字的 PDF，每份不超过 20 MiB。扫描件暂不自动识别；转换完成后请检查确认。</p></> : null}
      {form === "text" ? <><label>资料类型<select aria-label="文本资料类型" value={manualKind} onChange={(e) => setManualKind(e.target.value as "note" | "meeting_document")}><option value="note">文本资料</option><option value="meeting_document">已修正会议文稿</option></select></label>{manualKind === "meeting_document" ? <label>来源会议编号（可选）<input aria-label="来源会议编号（可选）" value={sourceMeetingId} onChange={(e) => setSourceMeetingId(e.target.value)} /></label> : null}<label>资料正文<textarea aria-label="资料正文" value={text} onChange={(e) => setText(e.target.value)} /></label></> : null}
      <div className="knowledge-actions"><button type="button" disabled={busy} onClick={() => setForm(null)}>取消</button><button type="submit" className="knowledge-primary" disabled={busy || !formValid}>{busy ? "正在保存…" : form === "file" ? "开始导入" : "保存"}</button></div>
    </form></section></div> : null}
    {confirmation ? <div className="knowledge-modal-backdrop"><section role="dialog" aria-modal="true" aria-label="确认操作" className="knowledge-modal"><h2>确认操作</h2><p>{confirmation.text}</p><div className="knowledge-actions"><button type="button" onClick={() => setConfirmation(null)}>取消</button><button type="button" className="knowledge-primary" onClick={() => { const action = confirmation.action; setConfirmation(null); action(); }}>继续</button></div></section></div> : null}
  </div>;
}

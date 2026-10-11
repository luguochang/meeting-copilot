import { useEffect, useState } from "react";
import type { KnowledgeApi } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import type { ProjectReconcileApi } from "../../api/projectReconcile";
import type { ReviewDocumentKind, ReviewDocuments } from "../../domain/events";
import type { DocumentSaveState } from "./useReviewDocumentDraft";
import { SourceLibraryImport } from "../knowledge-library/SourceLibraryImport";
import { ProjectReconcileJobs } from "../project-workspace/ProjectReconcileJobs";
import { MeetingReuseGuide } from "./MeetingReuseGuide";
import { MeetingKnowledgeScope } from "../knowledge-library/MeetingKnowledgeScope";
import "../project-workspace/projectWorkspace.css";
const names: Record<ReviewDocumentKind, string> = { minutes: "会议纪要", decisions: "决策", action_items: "待办", risks: "风险", transcript: "会议文字" };
export function ReviewProjectActions({ knowledgeApi, projectApi, reconcileApi, meetingId, documents, saveStates, onOpenDocument, onOpenProposals }: {
  knowledgeApi: KnowledgeApi; projectApi?: ProjectOutcomesApi; reconcileApi?: ProjectReconcileApi;
  meetingId: string; documents: ReviewDocuments; saveStates: Partial<Record<ReviewDocumentKind, DocumentSaveState>>;
  onOpenDocument?: (target: { documentId: string; projectId: string }) => void; onOpenProposals?: (projectId: string) => void;
}) {
  const [kind, setKind] = useState<ReviewDocumentKind>("minutes"); const [importing, setImporting] = useState(false); const [projectId, setProjectId] = useState<string | null>(null); const [reload, setReload] = useState(0); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null); const [jobId, setJobId] = useState<string | null>(null);
  useEffect(() => { const controller = new AbortController(); setLoading(true); void knowledgeApi.getMeetingContext(meetingId, controller.signal).then((context) => { if (!controller.signal.aborted) setProjectId(context.projectId); }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "项目资料范围读取失败。"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [knowledgeApi, meetingId, reload]);
  const document = documents[kind]; const ready = !!document?.documentId && document.source === "user_final" && document.userVersion > 0 && document.dirtyState === "saved" && (saveStates[kind] ?? "saved") === "saved";
  const reconcile = async () => {
    if (!ready || !document || !projectId || !reconcileApi || !projectApi || busy) return; setBusy(true); setError(null);
    try {
      const context = await knowledgeApi.getMeetingContext(meetingId);
      if (context.projectId !== projectId) throw new Error("会议所选项目已变化，请刷新项目范围后重新发起。");
      const current = await reconcileApi.getCurrentFinal(meetingId, kind);
      if (current.userVersion !== document.userVersion || current.documentId !== document.documentId) throw new Error("人工终稿已更新，请刷新会议文稿后再发起核对。");
      const state = await projectApi.listState(projectId); const job = await reconcileApi.create(meetingId, kind, state.revision, current.userVersion); setJobId(job.id);
    } catch (e) { setError(e instanceof Error ? e.message : "项目核对未发起，请刷新后重试。"); }
    finally { setBusy(false); }
  };
  return <section className="review-project-actions" aria-label="终稿与项目资料">
    <h3>让人工终稿继续用于后续会议</h3>
    <p>先核对并保存人工终稿。入库会生成待确认资料；项目核对会使用已配置模型生成候选，确认后才进入项目状态。</p>
    <MeetingReuseGuide saved={ready} />
    <div className="knowledge-actions">
      <label>原文类型<select aria-label="使用哪份人工终稿" disabled={busy || importing} value={kind} onChange={(event) => setKind(event.target.value as ReviewDocumentKind)}>{Object.entries(names).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <button type="button" disabled={busy || loading} onClick={() => { setError(null); setReload((x) => x + 1); }}>刷新本场项目范围</button>
      {knowledgeApi.importFinal ? <button type="button" disabled={!ready || !projectId || loading || busy} onClick={() => setImporting(true)}>把已保存人工终稿加入资料库</button> : null}
      {projectApi && reconcileApi ? <button type="button" disabled={!ready || !projectId || loading || busy} onClick={() => void reconcile()}>让 Pi 核对项目变化</button> : null}
    </div>
    {loading ? <p role="status">正在读取本场项目范围…</p> : !projectId ? <>
      <p>会前无需选择项目。想把终稿留作后续会议资料时，可以在这里选择归档项目，再保存人工终稿并入库。</p>
      <details className="review-project-binding"><summary>选择会后归档项目</summary>
        <p>只在你点击保存后关联项目；保存也会更新本场的项目资料范围。入库仍需单独确认，不会自动发起 AI 核对。</p>
        <MeetingKnowledgeScope api={knowledgeApi} meetingId={meetingId} onSaved={() => { setError(null); setReload((value) => value + 1); }} />
      </details>
    </> : !ready ? <p>所选文稿尚未形成可用的已保存人工终稿，或还有未保存编辑。请完成核对并保存后再使用。</p> : <p>使用已保存的第 {document?.userVersion} 版人工终稿；不覆盖资料修正版。</p>}
    {error ? <p className="knowledge-alert" role="alert">{error}</p> : null}
    {importing && document?.documentId ? <SourceLibraryImport api={knowledgeApi} source={{ kind: "final", id: document.documentId, meetingId, documentKind: kind, version: document.userVersion }} onClose={() => setImporting(false)} onOpenDocument={onOpenDocument} /> : null}
    {reconcileApi && projectApi && projectId ? <ProjectReconcileJobs api={reconcileApi} projectApi={projectApi} projectId={projectId} meetingId={meetingId} initialJobId={jobId} onOpenProposals={onOpenProposals} /> : null}
  </section>;
}

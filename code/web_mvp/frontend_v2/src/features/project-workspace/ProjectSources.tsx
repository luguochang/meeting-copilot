import { useState } from "react";
import type { ProjectOutcomesApi, ProjectSourceRef } from "../../api/projectOutcomes";

function sameProjectSource(recorded: ProjectSourceRef, current: ProjectSourceRef): boolean {
  return recorded.sourceType === current.sourceType && recorded.sourceId === current.sourceId && recorded.projectId === current.projectId && recorded.meetingId === current.meetingId && recorded.version === current.version && recorded.contentHash === current.contentHash && recorded.versionId === current.versionId;
}
export function ProjectSources({ api, sources, stale = false, onDocument, onMeeting, beforeInspect }: {
  api: ProjectOutcomesApi; sources: ProjectSourceRef[]; stale?: boolean;
  onDocument?: (source: ProjectSourceRef) => void; onMeeting?: (id: string) => void;
  beforeInspect?: () => Promise<void>;
}) {
  const [opened, setOpened] = useState<ProjectSourceRef | null>(null);
  const [busy, setBusy] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  if (!sources.length) return <p className="knowledge-muted">由用户明确输入，无附加来源。</p>;
  const inspect = async (source: ProjectSourceRef) => {
    setOpened(null); setError(null); setBusy(source.sourceId);
    try {
      if (stale) throw new Error("该事项来源已变化，需要重新核实；旧引用未替换为新版。");
      await beforeInspect?.();
      const current = await api.getSource(source);
      if (!sameProjectSource(source, current)) throw new Error("来源版本已变化，旧引用已过期；未替换为新版。");
      setOpened(current);
    } catch (e) { setError(`无法核对原来源：${e instanceof Error ? e.message : "请稍后重试。"}`); }
    finally { setBusy(null); }
  };
  return <div className="project-sources">
    {sources.map((source, index) => <button type="button" key={`${source.sourceType}:${source.sourceId}:${index}`} disabled={busy !== null} onClick={() => void inspect(source)}>查看来源 {index + 1} · {source.sourceType === "document" ? source.title ?? "项目资料" : source.sourceType === "review_document" ? "人工修正会议文稿" : "人工确认会议事项"} · 第 {source.version} 版</button>)}
    {busy ? <p role="status">正在核对来源版本…</p> : null}
    {error ? <p className="knowledge-alert" role="alert">{error}</p> : null}
    {opened ? <section className="knowledge-source" aria-label="项目事项原来源"><blockquote>{opened.quote}</blockquote>{opened.sourceType === "document" && onDocument ? <button type="button" onClick={() => onDocument(opened)}>在资料库定位原文</button> : opened.meetingId && onMeeting ? <button type="button" onClick={() => onMeeting(opened.meetingId!)}>打开来源会议</button> : null}<button type="button" onClick={() => setOpened(null)}>收起来源</button></section> : null}
  </div>;
}

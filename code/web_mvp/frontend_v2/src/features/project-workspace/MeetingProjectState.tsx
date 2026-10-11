import { useEffect, useState } from "react";
import type { KnowledgeApi } from "../../api/knowledge";
import type { MeetingProjectState as Projection, ProjectOutcomesApi, ProjectSourceRef, ProjectStateItem } from "../../api/projectOutcomes";
import type { DocumentSourceEvidence } from "../../domain/events";
import { ProjectSources } from "./ProjectSources";
import "./projectWorkspace.css";

export function MeetingProjectState({ api, knowledgeApi, meetingId, refreshKey = 0, onDocument }: {
  api: ProjectOutcomesApi; knowledgeApi?: KnowledgeApi; meetingId: string; refreshKey?: number;
  onDocument?: (source: DocumentSourceEvidence, meetingId?: string) => void;
}) {
  const [state, setState] = useState<Projection | null>(null); const [loading, setLoading] = useState(true); const [error, setError] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setState(null); setError(null);
    void api.getMeetingState(meetingId, controller.signal).then((value) => { if (!controller.signal.aborted) setState(value); }).catch((e) => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "项目状态读取失败。"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [api, meetingId, refresh, refreshKey]);
  const check = async (item: ProjectStateItem) => {
    const latest = await api.getMeetingState(meetingId);
    if (![...latest.decisions, ...latest.unfinishedTasks, ...latest.openQuestions].some((x) => x.id === item.id && x.revision === item.revision)) throw new Error("本场范围或项目事项已变化，请刷新后重新核对。");
  };
  const openDocument = async (item: ProjectStateItem, source: ProjectSourceRef) => {
    try {
      if (!knowledgeApi || !onDocument || !source.documentId || !source.versionId) return;
      await check(item); const context = await knowledgeApi.getMeetingContext(meetingId);
      onDocument({ sourceType: "document", sourceId: source.sourceId, documentId: source.documentId, projectId: source.projectId, title: source.title ?? "项目资料", versionId: source.versionId, version: source.version, contentHash: source.contentHash, quote: source.quote, locator: source.locator, contextRevision: context.revision }, meetingId);
    } catch (e) { setError(e instanceof Error ? e.message : "原来源无法打开。"); }
  };
  const groups: [string, ProjectStateItem[]][] = state ? [["已确认决策", state.decisions], ["未完成待办", state.unfinishedTasks], ["未解决问题", state.openQuestions]] : [];
  return <section className="meeting-project-state" aria-label="本场继承项目状态"><header><h3>本场继承的项目状态</h3><button type="button" disabled={loading} onClick={() => setRefresh((x) => x + 1)}>刷新继承事项</button></header><p>按照本场已保存的项目与资料范围读取。已完成、已取消和来源待复核的事项不会作为当前上下文。</p>{error ? <p className="knowledge-alert" role="alert">{error}</p> : loading ? <p role="status">正在读取继承事项…</p> : !state?.projectId ? <p>先保存项目资料范围，再查看可继承事项。</p> : !groups.some(([, items]) => items.length) ? <p>当前范围没有可继承事项。可在资料库的项目状态中确认决策、待办和问题。</p> : groups.map(([label, items]) => items.length ? <div key={label}><h4>{label}</h4>{items.map((item) => <article className="project-item" key={item.id}><p>{item.text}</p>{item.type === "task" ? <p>负责人：{item.owner ?? "未指定"} · 截止：{item.due ?? "未指定"}</p> : null}<ProjectSources api={api} sources={item.sourceRefs} beforeInspect={() => check(item)} onDocument={(source) => void openDocument(item, source)} /></article>)}</div> : null)}{state?.truncated ? <p>还有更多事项，请在资料库中查看完整项目状态。</p> : null}</section>;
}

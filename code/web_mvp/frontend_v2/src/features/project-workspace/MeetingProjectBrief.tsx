import { useEffect, useRef, useState } from "react";
import type { MeetingKnowledgeSelection } from "../../api/knowledge";
import type { ProjectBriefPreview, ProjectOutcomesApi, ProjectStateItem } from "../../api/projectOutcomes";
import { ProjectSources } from "./ProjectSources";
import "./meetingProjectBrief.css";

export function MeetingProjectBrief({ api, selection, open }: { api: ProjectOutcomesApi; selection: MeetingKnowledgeSelection; open: boolean }) {
  const key = JSON.stringify([selection.projectId, selection.folderId, selection.documentIds]);
  const [snapshot, setSnapshot] = useState<{ key: string; value: ProjectBriefPreview } | null>(null);
  const [error, setError] = useState<{ key: string; message: string } | null>(null);
  const [pending, setPending] = useState<string | null>(null); const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  useEffect(() => {
    const current = ++generation.current;
    if (!open || !selection.projectId) { setSnapshot(null); setError(null); setPending(null); return; }
    if (!api.previewBrief) { setError({ key, message: "当前服务暂不支持会前项目简报，请更新服务后重试。" }); return; }
    const controller = new AbortController(); setSnapshot(null); setError(null); setPending(key);
    const timer = window.setTimeout(() => { if (current === generation.current && !controller.signal.aborted) { controller.abort(); setPending(null); setError({ key, message: "会前简报读取超时，可刷新重试；会议资料范围尚未改变。" }); } }, 6_000);
    void api.previewBrief(selection, controller.signal).then((value) => {
      if (controller.signal.aborted || current !== generation.current) return;
      setSnapshot({ key, value });
    }).catch((failure) => { if (!controller.signal.aborted && current === generation.current) setError({ key, message: failure instanceof Error ? failure.message : "会前简报读取失败。" }); }).finally(() => { window.clearTimeout(timer); if (!controller.signal.aborted && current === generation.current) setPending(null); });
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api, key, open, refresh, selection]);
  if (!open || !selection.projectId) return null;
  const brief = snapshot?.key === key ? snapshot.value : null;
  const showItems = (items: ProjectStateItem[], empty: string, stale = false) => items.length ? <ul>{items.map((item) => <li key={`${key}:${item.id}:${item.revision}`}><p>{item.text}{item.owner ? ` · 负责人：${item.owner}` : ""}{item.due ? ` · 期限：${item.due}` : ""}</p>{stale ? <p>这条旧事项的来源已变化，复核前不会带入本次会议。</p> : null}<ProjectSources api={api} sources={item.sourceRefs} stale={stale} /></li>)}</ul> : <p>{empty}</p>;
  return <section className="meeting-project-brief" aria-label="会前项目简报"><h3>会前项目简报</h3><p>按当前选择整理已确认的内容，不新增推测。改选项目、文件夹或资料后会重新读取。</p>
    {pending === key ? <p role="status">正在读取所选范围的结论与未完事项…</p> : null}
    {error?.key === key ? <p className="knowledge-alert" role="alert">{error.message}</p> : null}
    {brief ? <><p>{brief.projectName} · 项目第 {brief.revision} 版 · 读取于 {new Date(brief.generatedAtMs).toLocaleTimeString()}</p>
      {brief.documentIds?.length === 0 ? <p>本次明确不使用项目资料，项目背景不会带入。</p> : null}
      <h4>上次结论</h4>{showItems(brief.decisions, "所选范围暂无已确认结论。")}
      <h4>这次仍待解决</h4>{showItems([...brief.unfinishedTasks, ...brief.openQuestions], "所选范围暂无未完事项或开放问题。")}
      <h4>需复核</h4>{showItems(brief.needsReview, "暂无来源已变化的项目事项。", true)}
      {brief.needsReviewDocuments.length ? <ul>{brief.needsReviewDocuments.map((document) => <li key={document.id}>{document.title} · 第 {document.activeVersion} 版 · {document.sourceStatus === "deleted" ? "原来源已删除" : "原来源已修改，待复核"}。复核前不会作为本次会议背景。</li>)}</ul> : null}
      {brief.truncated ? <p>这里只展示有限的项目事项；更多内容请在项目状态与资料库中查看。</p> : null}
    </> : null}
    <button type="button" disabled={pending === key} onClick={() => setRefresh((value) => value + 1)}>刷新会前简报</button>
  </section>;
}

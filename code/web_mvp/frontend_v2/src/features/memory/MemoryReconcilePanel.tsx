import { useEffect, useRef, useState } from "react";
import type { MemoryReconcileApi, MemoryReconcileJob, MemoryReconcileMode, MemoryReconcileSettings } from "../../api/memoryReconcile";

const labels: Record<MemoryReconcileJob["status"], string> = { pending: "等待整理", running: "正在核对来源", succeeded: "已完成", failed: "未完成", cancelled: "已取消" };
function message(error: unknown): string { return error instanceof Error ? error.message : "记忆整理读取失败，请重试。"; }

export function MemoryReconcilePanel({ api, projectId, onChanged }: { api: MemoryReconcileApi; projectId: string; onChanged: () => void }) {
  const [settings, setSettings] = useState<MemoryReconcileSettings | null>(null);
  const [jobs, setJobs] = useState<MemoryReconcileJob[]>([]);
  const [cursor, setCursor] = useState<string | null>(null); const [next, setNext] = useState<string | null>(null); const [previous, setPrevious] = useState<(string | null)[]>([]);
  const [refresh, setRefresh] = useState(0); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const notified = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const results = await Promise.allSettled([api.settings(projectId, controller.signal), api.list(projectId, cursor, controller.signal)]);
      if (controller.signal.aborted) return;
      if (results[0].status === "fulfilled" && results[1].status === "fulfilled") {
        const config = results[0].value; const page = results[1].value;
        if (config.projectId !== projectId || page.items.some((job) => job.projectId !== projectId)) { setError("返回的项目范围不一致，请刷新。"); setLoading(false); return; }
        setSettings(config); setJobs(page.items); setNext(page.hasMore ? page.nextCursor : null); setError(null);
        for (const job of page.items) if (job.status === "succeeded" && job.resultMemoryIds.length && !notified.current.has(job.id)) { notified.current.add(job.id); onChanged(); }
        if (page.items.some((job) => job.status === "running" || job.status === "pending" && !job.requiresResume)) timer = setTimeout(() => void load(), 2000);
      } else { const rejected = results.find((result) => result.status === "rejected"); setError(message(rejected?.status === "rejected" ? rejected.reason : null)); }
      setLoading(false);
    };
    setLoading(true); setSettings(null); setJobs([]); void load(); return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [api, cursor, onChanged, projectId, refresh]);
  const act = async (operation: () => Promise<void>) => { setBusy(true); setError(null); try { await operation(); setRefresh((value) => value + 1); } catch (failure) { setError(message(failure)); } finally { setBusy(false); } };
  const configure = (mode: MemoryReconcileMode) => void act(async () => { if (!settings) return; await api.configure(projectId, mode, settings.revision); });
  return <section className="memory-reconcile" aria-label="后台记忆整理">
    <h3>从明确纠正中积累记忆</h3>
    <p>确认资料中的术语纠正或“我的偏好”后，Pi 在后台核对来源并提出候选。只在你确认候选后生效，普通业务内容不会自动变成个人偏好。</p>
    <label>后台记忆整理<select aria-label="后台记忆整理模式" value={settings?.mode ?? "off"} disabled={loading || busy || !settings} onChange={(event) => configure(event.target.value as MemoryReconcileMode)}><option value="explicit_only">仅整理明确纠正与本人偏好</option><option value="off">关闭</option></select></label>
    <p>{!settings ? "尚未读取整理模式，暂不能操作。" : settings.mode === "explicit_only" ? "仅新确认的明确增量会调用模型；查看页面不会启动任务。失败或中断后由你决定是否重试。" : "关闭后不启动新的模型任务，已确认的记忆继续可查看、禁用或删除。"}</p>
    <button type="button" disabled={loading || busy} onClick={() => setRefresh((value) => value + 1)}>刷新整理记录</button>
    {error ? <p role="alert">{error}</p> : null}
    {loading && !jobs.length ? <p>正在读取整理记录…</p> : !jobs.length ? <p>还没有整理记录。下一次确认含明确纠正的资料后，候选会出现在下方。</p> : jobs.map((job) => <article className="memory-item" key={job.id}>
      <strong>{job.documentTitle}</strong><p>资料第 {job.sourceVersion} 版 · {job.requiresResume ? "等待你恢复" : labels[job.status]} · 已执行 {job.attempts} 次</p>
      {job.errorMessage ? <p>{job.errorMessage}</p> : null}
      {job.status === "succeeded" ? <p>{job.resultMemoryIds.length ? `已提出 ${job.resultMemoryIds.length} 条候选，仍待你确认。` : "核对完成，没有新增候选。"}</p> : null}
      <div className="memory-controls">
        {(job.status === "pending" || job.status === "running") ? <button type="button" disabled={busy} onClick={() => void act(async () => { await api.cancel(job.id); })}>取消本次整理</button> : null}
        {job.canRetry ? <button type="button" disabled={busy || settings?.mode !== "explicit_only"} onClick={() => void act(async () => { await api.retry(job.id, job.sourceVersionId); })}>{job.requiresResume ? "继续核对此版本" : "重试核对此版本"}</button> : null}
      </div>
    </article>)}
    <nav className="knowledge-pagination" aria-label="整理记录分页"><button type="button" disabled={busy || loading || !previous.length} onClick={() => { setCursor(previous.at(-1) ?? null); setPrevious((items) => items.slice(0, -1)); }}>上一页整理记录</button><button type="button" disabled={busy || loading || !next} onClick={() => { setPrevious((items) => [...items, cursor]); setCursor(next); }}>下一页整理记录</button></nav>
  </section>;
}

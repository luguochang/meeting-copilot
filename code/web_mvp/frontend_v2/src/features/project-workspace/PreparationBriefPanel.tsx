import { useEffect, useRef, useState } from "react";
import { matchesPreparation, type PreparationBriefApi, type PreparationInput, type PreparationJob, type PreparationLine } from "../../api/preparationBrief";
import type { KnowledgePage } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import { ProjectSources } from "./ProjectSources";
import "./meetingProjectBrief.css";

function failureMessage(error: unknown): string {
  if (error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name)) return "操作未确认完成，请刷新任务列表核对；不会自动重复提交。";
  return error instanceof Error ? error.message : "会前准备读取失败，请手动刷新。";
}
const STATUS: Record<PreparationJob["status"], string> = { pending: "等待后台处理", running: "正在按目标准备", succeeded: "准备草案已生成", failed: "准备失败", cancelled: "已取消" };

function PreparationLines({ title, lines, job, api, projectApi }: { title: string; lines: PreparationLine[]; job: PreparationJob; api: PreparationBriefApi; projectApi: ProjectOutcomesApi }) {
  const [opened, setOpened] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const verify = async () => {
    const current = await api.get(job.id);
    if (current.status !== "succeeded" || current.evidenceStatus !== "valid" || current.inputHash !== job.inputHash) throw new Error("准备资料已变化，旧结果需要重新生成。请选择当前范围后重新准备。");
  };
  if (!lines.length) return null;
  return <section><h4>{title}</h4><ul>{lines.map((line, index) => <li key={index}><p>{line.text}</p>
    {line.basis === "user_goal" ? <small>按本次目标建议，待会议核实。</small> : <>
      <ProjectSources api={projectApi} sources={line.sourceRefs.filter((source) => source.sourceType !== "project_state")} beforeInspect={verify} />
      {line.sourceRefs.filter((source) => source.sourceType === "project_state").map((source) => <div key={source.sourceId}>
        <button type="button" disabled={busy} onClick={() => { setBusy(true); setError(null); setOpened(null); void verify().then(() => setOpened(source.sourceId)).catch((e) => setError(failureMessage(e))).finally(() => setBusy(false)); }}>查看项目事项来源 · 第 {source.revision} 版</button>
        {opened === source.sourceId ? <div><blockquote>{source.quote}</blockquote>{source.sourceRefs.length ? <ProjectSources api={projectApi} sources={source.sourceRefs} beforeInspect={verify} /> : <p>由用户确认的项目事项。</p>}<button type="button" onClick={() => setOpened(null)}>收起来源</button></div> : null}
      </div>)}
    </>}
  </li>)}</ul>{error ? <p role="alert" className="knowledge-alert">{error}</p> : null}</section>;
}

export function PreparationBriefPanel({ api, projectApi, input, open }: { api: PreparationBriefApi; projectApi: ProjectOutcomesApi; input: PreparationInput; open: boolean }) {
  const projectId = input.selection.projectId;
  const inputKey = JSON.stringify([projectId, input.selection.folderId, input.selection.documentIds, input.goal.trim(), input.role.trim(), input.focus, input.scene]);
  const generation = useRef(0); const inputKeyRef = useRef(inputKey);
  const [page, setPage] = useState<{ projectId: string; page: KnowledgePage<PreparationJob> } | null>(null);
  const [error, setError] = useState<{ key: string; text: string } | null>(null);
  const [reload, setReload] = useState(0); const [loading, setLoading] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => { inputKeyRef.current = inputKey; }, [inputKey]);
  useEffect(() => {
    const current = ++generation.current;
    if (!open || !projectId) { setPage(null); setLoading(null); return; }
    const controller = new AbortController(); let timer: number | undefined;
    setLoading(projectId);
    const load = async () => {
      try {
        const result = await api.list(projectId, null, controller.signal);
        if (controller.signal.aborted || current !== generation.current) return;
        setPage({ projectId, page: result }); setLoading(null);
        if (result.items.some((job) => job.status === "running" || (job.status === "pending" && !job.requiresResume))) timer = window.setTimeout(() => void load(), 2_000);
      } catch (e) { if (!controller.signal.aborted && current === generation.current) { setError({ key: projectId, text: failureMessage(e) }); setLoading(null); } }
    };
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [api, projectId, open, reload]);
  if (!open || !projectId) return null;
  const currentPage = page?.projectId === projectId ? page.page : null;
  const jobs = currentPage?.items ?? [];
  const matching = jobs.filter((job) => matchesPreparation(job, input));
  const running = matching.some((job) => job.status === "running" || (job.status === "pending" && !job.requiresResume));
  const validInput = input.goal.trim().length > 0 && input.goal.length <= 1000 && input.role.length <= 160 && input.focus.length <= 8 && input.focus.every((point) => point.length <= 160);
  const mutate = async (operation: "start" | "retry" | "cancel", job?: PreparationJob) => {
    const key = inputKey; const current = generation.current;
    setBusy(key); setError(null);
    try {
      if (operation === "cancel" && job) await api.cancel(job.id);
      else {
        const state = await projectApi.getWiki(projectId);
        if (state.projectId !== projectId) throw new Error("项目状态与本次选择不一致，请刷新。");
        if (inputKeyRef.current !== key || current !== generation.current) return;
        if (operation === "retry" && job) await api.retry(job, state.revision);
        else await api.start(input, state.revision);
      }
      if (current === generation.current) setReload((value) => value + 1);
    } catch (e) { if (current === generation.current && inputKeyRef.current === key) setError({ key, text: failureMessage(e) }); }
    finally { if (current === generation.current) setBusy(null); }
  };
  const loadMore = async () => {
    if (!currentPage?.hasMore || !currentPage.nextCursor) return;
    const current = generation.current; setBusy(inputKey);
    try {
      const next = await api.list(projectId, currentPage.nextCursor);
      if (current !== generation.current) return;
      setPage({ projectId, page: { ...next, items: [...jobs, ...next.items.filter((job) => !jobs.some((old) => old.id === job.id))] } });
    } catch (e) { if (current === generation.current) setError({ key: projectId, text: failureMessage(e) }); }
    finally { if (current === generation.current) setBusy(null); }
  };
  return <section className="meeting-project-brief meeting-preparation-brief" aria-label="按本次目标准备"><h3>按本次目标准备</h3>
    <p>Pi 会核对所选资料，整理本次议程、可直接问的问题和已有条件。点击后在后台处理；你可以先开始会议。</p>
    {!input.goal.trim() ? <p>请在下方“会议设置”填写本次目标，再生成准备草案。</p> : <p>本次目标：{input.goal.trim()}{input.role.trim() ? ` · 我的角色：${input.role.trim()}` : ""}</p>}
    <button type="button" disabled={!validInput || running || busy === inputKey} onClick={() => void mutate("start")}>{matching.some((job) => job.status === "succeeded") ? "重新按本次目标准备" : "让 Pi 按本次目标准备"}</button>
    <button type="button" disabled={busy === inputKey || loading === projectId} onClick={() => { setError(null); setReload((value) => value + 1); }}>刷新准备任务</button>
    {loading === projectId ? <p role="status">正在读取准备任务…</p> : null}
    {error && (error.key === inputKey || error.key === projectId) ? <p role="alert" className="knowledge-alert">{error.text}</p> : null}
    {!loading && !jobs.length ? <p>暂无准备任务。读取页面不会自动调用模型。</p> : null}
    {jobs.map((job) => {
      const matches = matchesPreparation(job, input); const ready = matches && job.status === "succeeded" && job.evidenceStatus === "valid" && job.result;
      return <article key={`${inputKey}:${job.id}:${job.revision}`}><h4>{STATUS[job.status]} · {job.goal}</h4><p>项目第 {job.baseProjectRevision} 版 · {new Date(job.createdAtMs).toLocaleString()}</p>
        {!matches ? <p>这份任务使用其他目标或资料范围，结果未带入当前准备。</p> : null}
        {job.requiresResume ? <p>这是此前尚未执行的任务，需要你明确恢复，不会自动调用模型。</p> : null}
        {job.errorMessage ? <p role="status">{job.errorMessage}</p> : null}
        {job.evidenceStatus === "stale" ? <p className="knowledge-alert">来源或项目状态已变化，旧草案不再作为当前依据。请重新准备。</p> : null}
        {job.status === "running" || job.status === "pending" ? <button type="button" disabled={busy === inputKey} onClick={() => void mutate("cancel", job)}>取消准备</button> : null}
        {matches && (job.status === "failed" || job.status === "cancelled" || job.requiresResume) ? <button type="button" disabled={busy === inputKey || !validInput} onClick={() => void mutate("retry", job)}>{job.requiresResume ? "明确恢复准备" : "手动重试准备"}</button> : null}
        {ready ? <><p>准备草案，供本次讨论使用；没有改写项目结论。</p><PreparationLines title="建议议程" lines={job.result!.agenda} job={job} api={api} projectApi={projectApi} /><PreparationLines title="可以直接问" lines={job.result!.questions} job={job} api={api} projectApi={projectApi} /><PreparationLines title="已有条件" lines={job.result!.conditions} job={job} api={api} projectApi={projectApi} />{job.contextTruncated ? <p>本次只使用有限资料，请检查是否还需补充关键背景。</p> : null}</> : null}
      </article>;
    })}
    {currentPage?.hasMore ? <button type="button" disabled={busy === inputKey} onClick={() => void loadMore()}>更多准备任务</button> : null}
  </section>;
}

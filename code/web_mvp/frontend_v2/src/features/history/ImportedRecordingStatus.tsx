import { useState } from "react";
import type { ImportJob, ImportJobStage } from "../../domain/events";

const stages: Record<ImportJobStage, string> = {
  reading: "读取文件", normalizing: "标准化转换", transcribing: "本地中文转写",
  correcting: "文字校正", reviewing: "会后整理", completed: "处理完成", unknown: "处理阶段未知",
};

export function ImportedRecordingStatus({ job, onRetry, onRefresh }: {
  job: ImportJob; onRetry(): Promise<void>; onRefresh(): Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const terminalFailure = job.status === "failed" || job.status === "cancelled";
  const operation = async (action: () => Promise<void>, retry = false) => {
    if (busy) return;
    setBusy(true); setRetrying(retry); setError(null); setNotice(null);
    try { await action(); if (retry) setNotice("重试请求已提交，处理状态以服务端返回为准；无需重复导入原文件。"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "导入状态操作失败，请保留原文件后重试。"); }
    finally { setBusy(false); setRetrying(false); }
  };
  return <section className="imported-recording-status" aria-label="本地录音导入状态">
    <header><strong>{terminalFailure ? "录音导入未完成" : job.status === "succeeded" ? "本地录音导入完成" : "正在处理导入录音"}</strong>
      <span>{stages[job.stage]}{job.progress !== null ? ` · ${Math.round(job.progress)}%` : ""}</span></header>
    <p>来源是本地录音文件，此页面不会启动麦克风或系统声音采集。下方可查看已生成的文字与结果；导入未完成不代表整场已整理。</p>
    {terminalFailure ? <p role="alert">{job.errorMessage || "本次导入未能完成，请核对本地转写组件和原文件。"} 原始录音已保留。</p> : null}
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
    {retrying || notice ? <p role="status">{retrying ? "正在提交本次导入重试…" : notice}</p> : null}
    <div>{terminalFailure && job.retryable ? <button type="button" className="primary-button" disabled={busy} onClick={() => void operation(onRetry, true)}>{retrying ? "正在提交重试…" : "重试本次录音导入"}</button> : null}
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void operation(onRefresh)}>{busy ? "正在读取…" : "刷新导入状态"}</button></div>
  </section>;
}

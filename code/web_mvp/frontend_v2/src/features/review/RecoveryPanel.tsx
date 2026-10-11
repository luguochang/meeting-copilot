import { useEffect, useRef, useState } from "react";
import { recoveryErrorMessage, recoveryReason, type RecoveryJob, type RecoveryRange, type RecoverySnapshot, type TranscriptRecoveryApi } from "../../api/transcriptRecovery";
import "./recoveryPanel.css";

const statusLabels: Record<RecoveryJob["status"], string> = {
  queued: "等待本地补识别", waiting_resources: "等待识别资源", running: "本地补识别中",
  candidate_ready: "候选已生成，待核对", failed: "补识别未完成", cancelled: "已取消",
  merged: "已合并到识别原文", stale: "来源已变化，候选已失效",
};
function time(milliseconds: number) {
  const seconds = Math.floor(milliseconds / 1_000);
  return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}
function rangeLabel(range: RecoveryRange) { return `${time(range.startMs)}–${time(range.endMs)}`; }
function trackLabel(track: RecoveryJob["track"]) { return track === "microphone" ? "麦克风" : "会议声音"; }
function rangeKey(range: RecoveryRange) { return `${range.startSample}:${range.endSample}:${range.coverageDigest}`; }
function pending(job: RecoveryJob) { return ["queued", "waiting_resources", "running"].includes(job.status) || job.executionActive; }
interface CandidateDraft { text: string; candidateVersion: number }

export function RecoveryPanel({ api, meetingId, onMerged }: {
  api: TranscriptRecoveryApi; meetingId: string; onMerged?(): Promise<void> | void;
}) {
  return <MeetingRecoveryPanel key={meetingId} api={api} meetingId={meetingId} onMerged={onMerged} />;
}

function MeetingRecoveryPanel({ api, meetingId, onMerged }: {
  api: TranscriptRecoveryApi; meetingId: string; onMerged?(): Promise<void> | void;
}) {
  const [open, setOpen] = useState(false);
  const [snapshot, setSnapshot] = useState<RecoverySnapshot | null>(null);
  const [sourceKey, setSourceKey] = useState("");
  const [selectedRangeKey, setSelectedRangeKey] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, CandidateDraft>>({});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const generation = useRef(0);
  const alive = useRef(true);
  const actionController = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; generation.current += 1; actionController.current?.abort(); };
  }, []);

  useEffect(() => {
    if (!open || busy) return;
    const controller = new AbortController();
    const request = ++generation.current;
    setLoading(true);
    void api.get(meetingId, controller.signal).then((value) => {
      if (controller.signal.aborted || request !== generation.current) return;
      setSnapshot((current) => ({ ...value, jobs: value.jobs.map((job) => {
        const previous = current?.jobs.find((item) => item.id === job.id);
        return previous && previous.version > job.version ? previous : job;
      }) }));
    }).catch((reason) => { if (!controller.signal.aborted && request === generation.current) setError(recoveryErrorMessage(reason)); })
      .finally(() => { if (!controller.signal.aborted && request === generation.current) setLoading(false); });
    return () => controller.abort();
  }, [api, busy, meetingId, open, refresh]);

  const needsPolling = Boolean(snapshot?.jobs.some(pending));
  useEffect(() => {
    if (!open || busy || !needsPolling || loading) return;
    const timer = window.setTimeout(() => setRefresh((value) => value + 1), 2_000);
    return () => window.clearTimeout(timer);
  }, [busy, loading, needsPolling, open, refresh]);

  const sources = snapshot?.sources ?? [];
  const source = sourceKey ? sources.find((item) => `${item.track}:${item.epoch}` === sourceKey) : sources[0];
  const range = selectedRangeKey ? source?.uncoveredRanges.find((item) => rangeKey(item) === selectedRangeKey) : source?.uncoveredRanges[0];
  const selected = snapshot?.jobs.find((job) => job.id === selectedId) ?? null;
  const draft = selected ? drafts[selected.id] ?? { text: selected.candidateText ?? "", candidateVersion: selected.candidateVersion } : null;
  const candidateChanged = Boolean(selected && draft && draft.candidateVersion !== selected.candidateVersion);

  const act = async (operation: (signal: AbortSignal) => Promise<RecoveryJob>, merging = false) => {
    if (busy) return;
    const request = ++generation.current;
    const controller = new AbortController();
    actionController.current = controller;
    setBusy(true); setLoading(false); setError(null); setNotice(null);
    try {
      const job = await operation(controller.signal);
      if (!alive.current || request !== generation.current) return;
      setSnapshot((current) => current ? { ...current, jobs: [job, ...current.jobs.filter((item) => item.id !== job.id)] } : current);
      setSelectedId(job.id);
      if (merging) {
        if (job.status !== "merged") throw new Error("merge_not_confirmed");
        setNotice("补识别原文已合并，未做 AI 校对。已保存人工稿和你的未保存编辑仍保留，请重新核对新增来源。");
        try { await onMerged?.(); } catch { setError("原文已合并，但页面刷新未完成。请手动刷新文字；不要重复合并。"); }
      }
    } catch (reason) { if (alive.current && request === generation.current) setError(recoveryErrorMessage(reason)); }
    finally { if (alive.current && request === generation.current) { setBusy(false); setRefresh((value) => value + 1); } }
  };

  const canCreate = snapshot?.capability.status === "ready" && Boolean(source && range) && !busy && !loading;
  const canMerge = Boolean(selected?.status === "candidate_ready" && snapshot?.mergeAllowed && !selected.executionActive && draft?.text.trim() && !candidateChanged && !busy);
  const overLength = Boolean(draft && (draft.text.length > 20_000 || new TextEncoder().encode(draft.text).length > 65_536));

  return <section className="recovery-panel" aria-label="从保存录音恢复文字">
    <header><div><h3>从保存录音恢复文字</h3><p>补识别没有文字覆盖的音频范围，其中可能包含静音。只使用本地识别，不调用大模型。</p></div>
      <button type="button" aria-expanded={open} disabled={busy} onClick={() => setOpen((value) => !value)}>{open ? "收起文字恢复" : "查看文字恢复范围"}</button>
    </header>
    {open ? <>
      <p>流程：选择范围 → 手动补识别 → 核对候选 → 确认合并。合并不代表 AI 校对或人工终稿完成。</p>
      <button type="button" disabled={loading || busy} onClick={() => { setError(null); setRefresh((value) => value + 1); }}>刷新恢复状态</button>
      {error ? <p className="recovery-panel__error" role="alert">{error}</p> : null}
      {notice ? <p className="recovery-panel__notice" role="status">{notice}</p> : null}
      {loading && !snapshot ? <p role="status">正在读取已保存音频与文字覆盖范围…</p> : null}
      {snapshot ? <>
        {!snapshot.mergeAllowed ? <p className="recovery-panel__notice">当前不能合并：请等待会议结束且没有活跃录音。候选不会自动写入会议文字。</p> : null}
        {snapshot.capability.status !== "ready" ? <p className="recovery-panel__notice">{recoveryReason(snapshot.capability.reason)}</p> : null}
        {snapshot.unavailableSources.map((item) => <p key={`${item.track}:${item.epoch}`}>{trackLabel(item.track)} · 录音批次 {item.epoch}：{recoveryReason(item.errorCode)}</p>)}
        {sources.length ? <fieldset disabled={busy}>
          <legend>选择准确的音频来源和范围</legend>
          <label>音轨与录音批次<select aria-label="补识别音轨与录音批次" value={source ? `${source.track}:${source.epoch}` : ""} onChange={(event) => { setSourceKey(event.target.value); setSelectedRangeKey(""); }}>
            {sources.map((item) => <option value={`${item.track}:${item.epoch}`} key={`${item.track}:${item.epoch}`}>{trackLabel(item.track)} · 录音批次 {item.epoch}</option>)}
          </select></label>
          <label>未覆盖范围<select aria-label="补识别未覆盖范围" value={range ? rangeKey(range) : ""} onChange={(event) => setSelectedRangeKey(event.target.value)}>
            <option value="" disabled>请选择当前有效范围</option>
            {source?.uncoveredRanges.map((item) => <option key={rangeKey(item)} value={rangeKey(item)}>{rangeLabel(item)}</option>)}
          </select></label>
          {range ? <p>本音轨累计录音时间 {rangeLabel(range)}；采样范围 {range.startSample}–{range.endSample}。时间不是墙钟，也不表示说话者身份。最多 {snapshot.capability.maxRangeSeconds} 秒。</p> : <p>当前没有可选的未覆盖范围，或此前选择的来源已变化。请刷新后重新核对。</p>}
          {source?.rangesTruncated ? <p>这里只显示部分有界范围；处理后刷新，不代表已覆盖整场音频。</p> : null}
          <button type="button" disabled={!canCreate} onClick={() => { if (source && range) void act((signal) => api.create(meetingId, { ...range, track: source.track, epoch: source.epoch }, signal)); }}>开始本地补识别</button>
        </fieldset> : <p>暂无可恢复的已保存音频范围。请到录音页核对文件状态；没有范围不证明整场文字已经完整。</p>}
        {snapshot.jobs.length ? <div className="recovery-panel__jobs" role="group" aria-label="本场文字恢复任务">
          {snapshot.jobs.map((job) => <button type="button" key={job.id} aria-pressed={selectedId === job.id} disabled={busy} onClick={() => { setSelectedId(job.id); setError(null); setNotice(null); }}>{trackLabel(job.track)} · 批次 {job.epoch} · {rangeLabel(job)} · {statusLabels[job.status]}</button>)}
        </div> : <p>还没有补识别任务。读取此页面不会开始识别。</p>}
        {selected ? <article className="recovery-panel__candidate" aria-label="文字恢复候选">
          <h4>{trackLabel(selected.track)} · 录音批次 {selected.epoch} · {rangeLabel(selected)}</h4>
          <p role="status">{selected.status === "cancelled" && selected.executionActive ? "已请求取消，仍在等待本次执行结束；不会合并迟到结果。" : statusLabels[selected.status]}</p>
          <p>识别尝试 {selected.attempts}/{selected.maxAttempts}；任务版本 {selected.version}。</p>
          {selected.errorCode ? <p className="recovery-panel__error">{recoveryReason(selected.errorCode)}</p> : null}
          {selected.originalCandidateText !== null ? <details><summary>查看原始 ASR 候选</summary><pre>{selected.originalCandidateText || "没有得到可用语音文字。"}</pre></details> : null}
          {selected.status === "candidate_ready" && draft ? <>
            <label>核对或修正本次候选<textarea aria-label="补识别候选文字" value={draft.text} disabled={busy} onChange={(event) => setDrafts((current) => ({ ...current, [selected.id]: { text: event.target.value, candidateVersion: draft.candidateVersion } }))} /></label>
            {candidateChanged ? <p className="recovery-panel__notice">候选版本已更新，你的修改仍保留。请对照原始候选，再明确选择是否使用新版本。<button type="button" disabled={busy} onClick={() => setDrafts((current) => ({ ...current, [selected.id]: { text: selected.candidateText ?? "", candidateVersion: selected.candidateVersion } }))}>使用最新候选并放弃本任务草稿</button></p> : null}
            {overLength ? <p role="alert">候选最多 20000 个字符和 64 KiB，请缩短后再合并。</p> : null}
            <button type="button" disabled={!canMerge || overLength} onClick={() => void act((signal) => api.merge(meetingId, selected.id, draft.candidateVersion, draft.text, signal), true)}>确认并合并到识别原文</button>
          </> : null}
          {["queued", "waiting_resources", "running"].includes(selected.status) ? <button type="button" disabled={busy} onClick={() => void act((signal) => api.cancel(meetingId, selected.id, selected.version, signal))}>取消本次补识别</button> : null}
          {["failed", "cancelled"].includes(selected.status) && !selected.executionActive && selected.attempts < selected.maxAttempts ? <button type="button" disabled={busy} onClick={() => void act((signal) => api.retry(meetingId, selected.id, selected.version, signal))}>手动重试本次补识别</button> : null}
          {selected.status === "stale" ? <p>旧候选不可合并。请刷新音频与文字覆盖范围，明确选择新的有效范围。</p> : null}
          {selected.status === "merged" ? <p>补识别原文，未做 AI 校对。人工稿与资料是否需要更新，请你继续核对。</p> : null}
        </article> : null}
      </> : null}
    </> : null}
  </section>;
}

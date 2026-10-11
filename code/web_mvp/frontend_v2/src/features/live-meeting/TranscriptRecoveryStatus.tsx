import { readTranscriptRecovery } from "./transcriptRecovery";

function time(ms: number) { const seconds = Math.floor(ms / 1000); return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`; }

export function TranscriptRecoveryStatus({ diagnostic, audioAvailable = false, onOpenAudio }: {
  diagnostic: unknown; audioAvailable?: boolean; onOpenAudio?: () => void;
}) {
  const value = readTranscriptRecovery(diagnostic);
  if (!value) return null;
  const range = value.range ? `${time(value.range.startMs)}–${time(value.range.endMs)}` : "补齐范围待读取";
  return <section className={`transcript-recovery-status transcript-recovery-status--${value.status}`} role={value.status === "failed" ? "alert" : "status"} aria-label="会议文字补齐状态">
    <strong>{value.status === "running" ? "正在补齐中断处文字" : value.status === "completed" && value.range ? "这段中断处文字已补齐" : value.status === "completed" ? "补齐任务已报告完成，范围需核对" : "中断处文字暂未补齐"}</strong>
    <p>{range} · {value.status === "running" ? "已有正文仍可查看。" : value.status === "completed" ? "仅代表本次补齐范围，不代表全场文字已经人工确认。" : value.reason ?? "请核对录音与正文，这段文字可能仍有缺口。"}</p>
    <p>{audioAvailable ? "已有可播放的录音；文字补齐与录音保存是两项独立状态。" : "录音保存状态请到录音页核对；本提示不表示录音已完整保存。"}</p>
    {onOpenAudio ? <button type="button" className="coach-text-button" onClick={onOpenAudio}>查看录音与保存状态</button> : null}
  </section>;
}

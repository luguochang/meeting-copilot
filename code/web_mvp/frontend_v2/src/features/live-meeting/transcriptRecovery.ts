const reasonMessages: Record<string, string> = {
  asr_capacity_unavailable: "当前本机可用内存不足，补齐任务未能完成。",
  asr_resource_probe_failed: "暂时无法确认本机资源，补齐任务未能开始。",
  asr_resource_guard_triggered: "补齐任务触发了资源保护，未能完成。",
  asr_cold_start_busy: "本机离线识别正在加载或清理，补齐任务暂未完成。",
};

export function readTranscriptRecovery(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  const status = source.status;
  if (status !== "running" && status !== "completed" && status !== "failed") return null;
  const start = source.start_ms ?? source.startMs;
  const end = source.end_ms ?? source.endMs;
  const range = typeof start === "number" && Number.isFinite(start) && start >= 0
    && typeof end === "number" && Number.isFinite(end) && end >= start ? { startMs: start, endMs: end } : null;
  const code = source.error_code ?? source.errorCode ?? source.error_class ?? source.errorClass;
  return { status, range, reason: typeof code === "string" ? reasonMessages[code] : undefined };
}

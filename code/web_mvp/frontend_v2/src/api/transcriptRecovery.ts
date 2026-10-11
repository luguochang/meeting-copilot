import { ContractError } from "./schema";

export type RecoveryTrack = "microphone" | "system_audio";
export type RecoveryStatus = "queued" | "waiting_resources" | "running" | "candidate_ready" | "failed" | "cancelled" | "merged" | "stale";
export interface RecoveryRange {
  startSample: number; endSample: number; startMs: number; endMs: number;
  audioDigest: string; coverageDigest: string;
}
export interface RecoverySource {
  track: RecoveryTrack; epoch: number; sampleRateHz: number; sampleCount: number;
  timelineOffsetMs: number; audioDigest: string; uncoveredRanges: RecoveryRange[]; rangesTruncated: boolean;
}
export interface RecoveryJob extends RecoveryRange {
  id: string; meetingId: string; track: RecoveryTrack; epoch: number; status: RecoveryStatus;
  version: number; candidateVersion: number; originalCandidateText: string | null; candidateText: string | null;
  attempts: number; maxAttempts: number; errorCode: string | null; executionActive: boolean;
  mergedSegmentIds: string[]; createdAtMs: number; updatedAtMs: number;
}
export interface RecoverySnapshot {
  meetingId: string; mergeAllowed: boolean;
  capability: { status: string; reason: string | null; maxRangeSeconds: number; maxAttempts: number; timeoutSeconds: number };
  sources: RecoverySource[]; jobs: RecoveryJob[];
  unavailableSources: { track: RecoveryTrack; epoch: number; errorCode: string }[];
}
export interface RecoveryInput extends Pick<RecoveryRange, "startSample" | "endSample" | "audioDigest" | "coverageDigest"> {
  track: RecoveryTrack; epoch: number;
}
export interface TranscriptRecoveryApi {
  get(meetingId: string, signal?: AbortSignal): Promise<RecoverySnapshot>;
  create(meetingId: string, range: RecoveryInput, signal?: AbortSignal): Promise<RecoveryJob>;
  retry(meetingId: string, jobId: string, expectedVersion: number, signal?: AbortSignal): Promise<RecoveryJob>;
  cancel(meetingId: string, jobId: string, expectedVersion: number, signal?: AbortSignal): Promise<RecoveryJob>;
  merge(meetingId: string, jobId: string, candidateVersion: number, text?: string, signal?: AbortSignal): Promise<RecoveryJob>;
}

const messages: Record<string, string> = {
  asr_capacity_unavailable: "当前本机可用内存资源不足，尚不能完成本次补识别；资源恢复后可手动重试。",
  asr_resource_probe_failed: "暂时无法确认本机可用内存资源，不能开始补识别；可稍后手动重试。",
  asr_resource_guard_triggered: "本次补识别触发资源保护，未能完成；可稍后手动重试。",
  asr_cold_start_busy: "其他离线识别正在启动或清理，请稍后再试。",
  recovery_audio_not_ready: "本场录音尚未形成可用的已保存音频，请先核对录音状态。",
  recovery_audio_invalid: "本场保存音频的格式或来源信息不可用，当前不能补识别。",
  recovery_audio_capacity_exceeded: "保存音频超出本地补识别的容量限制。",
  recovery_audio_path_invalid: "保存音频的来源未通过检查，当前不能补识别。",
  recovery_timeline_unknown: "录音时间映射尚不明确，不能推测或恢复该范围。",
  recovery_coverage_unknown: "原文字的覆盖范围尚不明确，请先核对会议来源。",
  recovery_source_changed: "音频或文字来源已变化，请刷新范围后重新核对。",
  recovery_coverage_changed: "这个范围的文字覆盖已变化，请刷新后重新核对。",
  recovery_range_invalid: "补识别范围无效，请重新选择已列出的范围。",
  recovery_range_overlap: "这个范围已有恢复任务，请先查看该任务。",
  recovery_version_conflict: "任务版本已变化，你的编辑仍保留。请刷新并核对最新候选。",
  recovery_candidate_changed: "候选版本已变化，你的编辑仍保留。请核对最新候选。",
  recovery_candidate_not_ready: "当前尚未形成可合并的识别候选。",
  recovery_capture_active: "当前录音仍在进行，请等待录音结束后再合并。",
  recovery_attempts_exhausted: "本范围已达到重试次数上限。",
  recovery_timeout: "本次本地补识别超时，录音与原文字保持原样。",
  recovery_cancelled: "已请求取消，本次结果不会自动合并。",
  recovery_not_available: "当前本地识别组件不可用，请到本地能力页核对。",
  recovery_components_missing: "本地离线识别组件尚未就绪，请到本地能力页核对。",
  recovery_worker_busy: "本地识别正在处理其他任务，请等待资源空闲。",
  recovery_audio_unavailable: "本场保存音频不可用，当前不能补识别。",
  recovery_audio_changed: "保存音频来源已变化，请刷新范围后重新核对。",
  recovery_range_covered: "这个范围已有识别文字，不能重复补识别。",
  recovery_merge_capture_active: "当前仍有录音进行，请等待录音结束后再合并。",
  recovery_no_speech: "这段音频未得到可用语音文字，可能包含静音；原录音保持原样。",
  recovery_asr_failed: "本次本地识别未完成，原录音与原文字保持原样。",
  recovery_asr_timeout: "本次本地识别超时，可稍后手动重试。",
  recovery_worker_interrupted: "上次本地识别被中断，请核对后手动重试。",
  recovery_execution_superseded: "这项任务已被较新的操作替代，请刷新当前状态。",
  recovery_retry_not_allowed: "当前任务状态不能重试，请刷新并选择有效范围。",
  recovery_candidate_invalid: "候选文字无效，未进行合并；请核对文字长度和内容。",
  recovery_already_merged: "这个范围的候选已合并，不会重复添加文字。",
  recovery_storage_unavailable: "当前本地保存不可用，未完成此次文字恢复。",
  recovery_meeting_not_found: "这场会议已不存在，不能继续文字恢复。",
  recovery_job_not_found: "这项恢复任务已不存在，请刷新本场状态。",
};
export function recoveryReason(code: string | null | undefined): string {
  return code && messages[code] ? messages[code] : "当前无法完成文字恢复，请刷新状态并核对本地识别与保存音频。";
}
export class RecoveryApiError extends Error {
  constructor(public readonly status: number, public readonly code: string) {
    super(recoveryReason(code)); this.name = "RecoveryApiError";
  }
}
export function recoveryErrorMessage(error: unknown): string {
  if (error instanceof RecoveryApiError) return error.message;
  if (error instanceof ContractError) return "恢复状态返回格式无效，未进行确认或合并。请刷新后再试。";
  return "文字恢复请求未完成，请检查本地服务并刷新；你的编辑仍保留。";
}
function record(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new ContractError("文字恢复数据格式错误");
  return raw as Record<string, unknown>;
}
function string(raw: unknown): string { if (typeof raw !== "string") throw new ContractError("文字恢复文本字段无效"); return raw; }
function nullable(raw: unknown): string | null { if (raw === null) return null; return string(raw); }
function integer(raw: unknown, minimum = 0): number {
  if (typeof raw !== "number" || !Number.isSafeInteger(raw) || raw < minimum) throw new ContractError("文字恢复数值字段无效");
  return raw;
}
function boolean(raw: unknown): boolean { if (typeof raw !== "boolean") throw new ContractError("文字恢复状态字段无效"); return raw; }
function digest(raw: unknown): string { const value = string(raw); if (!/^[a-f0-9]{64}$/i.test(value)) throw new ContractError("文字恢复来源摘要无效"); return value; }
function track(raw: unknown): RecoveryTrack { if (raw !== "microphone" && raw !== "system_audio") throw new ContractError("文字恢复音轨无效"); return raw; }
function list<T>(raw: unknown, parse: (item: unknown) => T): T[] { if (!Array.isArray(raw)) throw new ContractError("文字恢复列表无效"); return raw.map(parse); }
function parseRange(raw: unknown): RecoveryRange {
  const x = record(raw); const startSample = integer(x.start_sample); const endSample = integer(x.end_sample, 1);
  const startMs = integer(x.start_ms); const endMs = integer(x.end_ms, 1);
  if (endSample <= startSample || endMs <= startMs) throw new ContractError("文字恢复范围无效");
  return { startSample, endSample, startMs, endMs, audioDigest: digest(x.audio_digest), coverageDigest: digest(x.coverage_digest) };
}
export function parseRecoveryJob(raw: unknown, meetingId: string): RecoveryJob {
  const x = record(raw); const status = string(x.status);
  if (!["queued", "waiting_resources", "running", "candidate_ready", "failed", "cancelled", "merged", "stale"].includes(status)) throw new ContractError("文字恢复任务状态无效");
  if (x.meeting_id !== meetingId) throw new ContractError("文字恢复任务归属不匹配");
  if (["candidate_ready", "merged"].includes(status) && integer(x.candidate_version) < 1) throw new ContractError("文字恢复候选版本无效");
  return { ...parseRange(x), id: string(x.id), meetingId, track: track(x.track), epoch: integer(x.epoch), status: status as RecoveryStatus,
    version: integer(x.version, 1), candidateVersion: integer(x.candidate_version), originalCandidateText: nullable(x.original_candidate_text), candidateText: nullable(x.candidate_text),
    attempts: integer(x.attempts), maxAttempts: integer(x.max_attempts, 1), errorCode: nullable(x.error_code), executionActive: boolean(x.execution_active),
    mergedSegmentIds: list(x.merged_segment_ids, string), createdAtMs: integer(x.created_at_ms), updatedAtMs: integer(x.updated_at_ms) };
}
export function parseRecoverySnapshot(raw: unknown, meetingId: string): RecoverySnapshot {
  const x = record(raw); const c = record(x.capability);
  if (x.schema_version !== "transcript_recovery.v1" || x.meeting_id !== meetingId) throw new ContractError("文字恢复接口版本或归属不匹配");
  if (!["ready", "waiting_resources", "unavailable"].includes(String(c.status))) throw new ContractError("文字恢复能力状态无效");
  const maxRangeSeconds = integer(c.max_range_seconds, 1), maxAttempts = integer(c.max_attempts, 1), timeoutSeconds = integer(c.timeout_seconds, 1);
  if (maxRangeSeconds > 300 || maxAttempts > 3 || timeoutSeconds > 180) throw new ContractError("文字恢复限额无效");
  return { meetingId, mergeAllowed: boolean(x.merge_allowed), capability: { status: string(c.status), reason: nullable(c.reason), maxRangeSeconds, maxAttempts, timeoutSeconds },
    sources: list(x.sources, (rawSource) => { const s = record(rawSource); const sampleCount = integer(s.sample_count, 1); const audioDigest = digest(s.audio_digest); const ranges = list(s.uncovered_ranges, parseRange);
      const sourceTrack = track(s.track), epoch = integer(s.epoch), rate = integer(s.sample_rate_hz, 1);
      if (s.meeting_id !== meetingId || rate < 8_000 || rate > 48_000 || ranges.some((range) => range.endSample > sampleCount || range.audioDigest !== audioDigest || range.endSample - range.startSample > maxRangeSeconds * rate)) throw new ContractError("补识别范围与音频来源不匹配");
      list(s.uncovered_ranges, (rawRange) => { const r = record(rawRange); if (r.track !== sourceTrack || r.epoch !== epoch) throw new ContractError("补识别范围的音轨或批次不匹配"); return r; });
      return { track: sourceTrack, epoch, sampleRateHz: rate, sampleCount, timelineOffsetMs: integer(s.timeline_offset_ms), audioDigest, uncoveredRanges: ranges, rangesTruncated: boolean(s.ranges_truncated) };
    }), jobs: list(x.jobs, (job) => parseRecoveryJob(job, meetingId)), unavailableSources: list(x.unavailable_sources ?? [], (rawSource) => { const s = record(rawSource); return { track: track(s.track), epoch: integer(s.epoch), errorCode: string(s.error_code) }; }) };
}
export class HttpTranscriptRecoveryApi implements TranscriptRecoveryApi {
  private readonly base: string;
  constructor(base = "") { this.base = base.trim().replace(/\/+$/, ""); }
  private path(meetingId: string, suffix = "") { return `${this.base}/v2/meetings/${encodeURIComponent(meetingId)}/transcript-recovery${suffix}`; }
  private async request(path: string, signal?: AbortSignal, body?: unknown): Promise<unknown> {
    const response = await fetch(path, { signal, headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) }, ...(body === undefined ? {} : { method: "POST", body: JSON.stringify(body) }) });
    const raw: unknown = await response.json().catch(() => null);
    if (!response.ok) { const payload = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}; const error = payload.error && typeof payload.error === "object" ? payload.error as Record<string, unknown> : {}; throw new RecoveryApiError(response.status, typeof error.code === "string" ? error.code : "recovery_request_failed"); }
    return raw;
  }
  async get(meetingId: string, signal?: AbortSignal) { return parseRecoverySnapshot(await this.request(this.path(meetingId), signal), meetingId); }
  private async job(meetingId: string, suffix: string, body: unknown, signal?: AbortSignal, expectedJobId?: string) {
    const job = parseRecoveryJob(record(await this.request(this.path(meetingId, suffix), signal, body)).job, meetingId);
    if (expectedJobId && job.id !== expectedJobId) throw new ContractError("文字恢复响应任务归属不匹配");
    return job;
  }
  async create(meetingId: string, range: RecoveryInput, signal?: AbortSignal) { return this.job(meetingId, "", { track: range.track, epoch: range.epoch, start_sample: range.startSample, end_sample: range.endSample, audio_digest: range.audioDigest, coverage_digest: range.coverageDigest }, signal); }
  async retry(meetingId: string, jobId: string, expectedVersion: number, signal?: AbortSignal) { return this.job(meetingId, `/${encodeURIComponent(jobId)}/retry`, { expected_version: expectedVersion }, signal, jobId); }
  async cancel(meetingId: string, jobId: string, expectedVersion: number, signal?: AbortSignal) { return this.job(meetingId, `/${encodeURIComponent(jobId)}/cancel`, { expected_version: expectedVersion }, signal, jobId); }
  async merge(meetingId: string, jobId: string, candidateVersion: number, text?: string, signal?: AbortSignal) { return this.job(meetingId, `/${encodeURIComponent(jobId)}/merge`, { candidate_version: candidateVersion, ...(text === undefined ? {} : { text }) }, signal, jobId); }
}

import type { RecoveryJob, RecoverySnapshot, TranscriptRecoveryApi } from "../api/transcriptRecovery";

export function recoveryJob(changes: Partial<RecoveryJob> = {}): RecoveryJob {
  return { id: "job-1", meetingId: "meeting-recovery", track: "system_audio", epoch: 2,
    startSample: 16_000, endSample: 48_000, startMs: 6_000, endMs: 8_000,
    audioDigest: "a".repeat(64), coverageDigest: "c".repeat(64), status: "candidate_ready", version: 3, candidateVersion: 1,
    originalCandidateText: "预算仍待核对。", candidateText: "预算仍待核对。", attempts: 1, maxAttempts: 3,
    errorCode: null, executionActive: false, mergedSegmentIds: [], createdAtMs: 1, updatedAtMs: 3, ...changes };
}
export function recoverySnapshot(changes: Partial<RecoverySnapshot> = {}): RecoverySnapshot {
  const job = recoveryJob();
  return { meetingId: job.meetingId, mergeAllowed: true, capability: { status: "ready", reason: null, maxRangeSeconds: 300, maxAttempts: 3, timeoutSeconds: 180 },
    sources: [{ track: job.track, epoch: job.epoch, sampleRateHz: 16_000, sampleCount: 48_000, timelineOffsetMs: 5_000,
      audioDigest: job.audioDigest, uncoveredRanges: [{ startSample: job.startSample, endSample: job.endSample, startMs: job.startMs, endMs: job.endMs, audioDigest: job.audioDigest, coverageDigest: job.coverageDigest }], rangesTruncated: false }], jobs: [job], unavailableSources: [], ...changes };
}
export function recoveryApi(value = recoverySnapshot()): TranscriptRecoveryApi {
  return { get: vi.fn().mockResolvedValue(value), create: vi.fn().mockResolvedValue(recoveryJob({ status: "queued", candidateVersion: 0, candidateText: null, originalCandidateText: null })), retry: vi.fn().mockResolvedValue(recoveryJob({ status: "queued", version: 4 })), cancel: vi.fn().mockResolvedValue(recoveryJob({ status: "cancelled", version: 4 })), merge: vi.fn().mockResolvedValue(recoveryJob({ status: "merged", version: 4, mergedSegmentIds: ["recovered-segment"] })) };
}
export function recoveryWireJob(job = recoveryJob()) {
  return { id: job.id, meeting_id: job.meetingId, track: job.track, epoch: job.epoch,
    start_sample: job.startSample, end_sample: job.endSample, start_ms: job.startMs, end_ms: job.endMs,
    audio_digest: job.audioDigest, coverage_digest: job.coverageDigest, status: job.status, version: job.version,
    candidate_version: job.candidateVersion, original_candidate_text: job.originalCandidateText, candidate_text: job.candidateText,
    attempts: job.attempts, max_attempts: job.maxAttempts, error_code: job.errorCode, execution_active: job.executionActive,
    merged_segment_ids: job.mergedSegmentIds, created_at_ms: job.createdAtMs, updated_at_ms: job.updatedAtMs };
}
export function recoveryWireSnapshot(value = recoverySnapshot()) {
  return { schema_version: "transcript_recovery.v1", meeting_id: value.meetingId, merge_allowed: value.mergeAllowed,
    capability: { status: value.capability.status, reason: value.capability.reason, max_range_seconds: 300, max_attempts: 3, timeout_seconds: 180 },
    sources: value.sources.map((source) => ({ meeting_id: value.meetingId, track: source.track, epoch: source.epoch, sample_rate_hz: source.sampleRateHz, sample_count: source.sampleCount, timeline_offset_ms: source.timelineOffsetMs, audio_digest: source.audioDigest, ranges_truncated: source.rangesTruncated, uncovered_ranges: source.uncoveredRanges.map((range) => ({ track: source.track, epoch: source.epoch, start_sample: range.startSample, end_sample: range.endSample, start_ms: range.startMs, end_ms: range.endMs, audio_digest: range.audioDigest, coverage_digest: range.coverageDigest })) })), jobs: value.jobs.map(recoveryWireJob), unavailable_sources: value.unavailableSources.map((source) => ({ track: source.track, epoch: source.epoch, error_code: source.errorCode })) };
}

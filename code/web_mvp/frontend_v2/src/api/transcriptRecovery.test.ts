import { afterEach, expect, it, vi } from "vitest";
import { HttpTranscriptRecoveryApi, parseRecoverySnapshot, recoveryErrorMessage } from "./transcriptRecovery";
import { recoveryJob, recoverySnapshot, recoveryWireJob, recoveryWireSnapshot } from "../test/transcriptRecoveryFixtures";
import { parseTranscriptPage } from "./schema";

function response(body: unknown, status = 200) { return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }); }
afterEach(() => vi.unstubAllGlobals());
it("reads without starting recognition, and sends exact source and versions only on explicit calls", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response(recoveryWireSnapshot())).mockImplementation((url: string) => Promise.resolve(response({ job: recoveryWireJob(recoveryJob({ id: url.includes("/job%2Fa/") ? "job/a" : "job-1" })) })));
  vi.stubGlobal("fetch", fetcher); const api = new HttpTranscriptRecoveryApi("http://localhost:9000/");
  await api.get("meeting-recovery"); expect(fetcher.mock.calls[0][1].method).toBeUndefined();
  const range = recoverySnapshot().sources[0].uncoveredRanges[0];
  await api.create("meeting-recovery", { ...range, track: "system_audio", epoch: 2 });
  await api.retry("meeting-recovery", "job/a", 3); await api.cancel("meeting-recovery", "job/a", 4); await api.merge("meeting-recovery", "job/a", 1, "修正后的候选。");
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ track: "system_audio", epoch: 2, start_sample: 16_000, end_sample: 48_000, audio_digest: "a".repeat(64), coverage_digest: "c".repeat(64) });
  expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ expected_version: 3 });
  expect(JSON.parse(fetcher.mock.calls[3][1].body)).toEqual({ expected_version: 4 });
  expect(JSON.parse(fetcher.mock.calls[4][1].body)).toEqual({ candidate_version: 1, text: "修正后的候选。" });
  expect(fetcher.mock.calls[4][0]).toContain("/job%2Fa/merge");
});
it.each(["meeting", "job-meeting", "source-meeting", "range-track", "range-epoch", "range", "digest", "execution", "status", "candidate-version", "capability"])("rejects invalid recovery response: %s", (damage) => {
  const raw = recoveryWireSnapshot();
  if (damage === "meeting") raw.meeting_id = "other";
  if (damage === "job-meeting") raw.jobs[0].meeting_id = "other";
  if (damage === "source-meeting") raw.sources[0].meeting_id = "other";
  if (damage === "range-track") raw.sources[0].uncovered_ranges[0].track = "microphone";
  if (damage === "range-epoch") raw.sources[0].uncovered_ranges[0].epoch = 99;
  if (damage === "range") raw.sources[0].uncovered_ranges[0].end_sample = 999_999;
  if (damage === "digest") raw.sources[0].audio_digest = "invalid";
  if (damage === "execution") Object.assign(raw.jobs[0], { execution_active: "true" });
  if (damage === "status") raw.jobs[0].status = "finished" as typeof raw.jobs[0]["status"];
  if (damage === "capability") raw.capability.status = "unexpected";
  if (damage === "candidate-version") raw.jobs[0].candidate_version = 0;
  expect(() => parseRecoverySnapshot(raw, "meeting-recovery")).toThrow();
});
it("rejects a different response job instead of adopting an unrelated merge result", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ job: recoveryWireJob(recoveryJob({ id: "different-job" })) })));
  await expect(new HttpTranscriptRecoveryApi().merge("meeting-recovery", "job-1", 1)).rejects.toThrow("任务归属不匹配");
});
it.each(["recovery_source_changed", "asr_capacity_unavailable", "recovery_asr_timeout", "unknown-private-code"])("shows fixed error text and never arbitrary server message: %s", async (code) => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ error: { code, message: "private-path-and-secret" } }, 409)));
  const api = new HttpTranscriptRecoveryApi();
  try { await api.get("meeting-recovery"); throw new Error("expected_error"); } catch (error) {
    expect(recoveryErrorMessage(error)).not.toContain("private-path-and-secret");
    expect(recoveryErrorMessage(error)).not.toContain("unknown-private-code");
  }
});
it("preserves real recovery provenance despite compatible pending correction status", () => {
  const job = recoveryJob(); const raw = { meeting_id: job.meetingId, after_transcript_seq: 0, next_after_transcript_seq: 1, has_more: false,
    segments: [{ meeting_id: job.meetingId, segment_id: "recovered", final_id: "recovered-final", transcript_seq: 1, text: "补回原文", correction_status: "pending", recovery_job_id: job.id,
      recovery_provenance: { job_id: job.id, track: job.track, epoch: job.epoch, start_sample: job.startSample, end_sample: job.endSample, audio_digest: job.audioDigest, coverage_digest: job.coverageDigest, candidate_version: job.candidateVersion, timing: "whole_audio_range", recognition: "local_batch_asr", correction: "not_requested", user_edited: true } }] };
  const parsed = parseTranscriptPage(raw);
  expect(parsed.segments[0].recoveryProvenance).toMatchObject({ jobId: job.id, correction: "not_requested", userEdited: true });
  expect(parsed.segments[0].correctionStatus).toBe("pending");
});

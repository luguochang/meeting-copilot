import { describe, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "./client";

describe("contribution request transport", () => {
  it("sends intent without fabricating an answer and preserves legacy refinement", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ job: { id: "job" } }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi("http://localhost:9000");
    await api.requestRealtimeCoach("meeting", "", null, undefined, { intent: "compare_options", expectedBasisRevision: 3 });
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ request: "", answer_id: null, intent: "compare_options", expected_basis_revision: 3 });
    expect(fetcher.mock.calls[0][1].headers["Idempotency-Key"]).toBeTruthy();
    fetcher.mockResolvedValue(new Response(JSON.stringify({ job: { id: "legacy" } }), { headers: { "content-type": "application/json" } }));
    await api.requestRealtimeCoach("meeting", "更具体", "answer");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ request: "更具体", answer_id: "answer" });
  });
  it("uses the persisted discussion ID and version for feedback, never the display topic ID", async () => {
    const item = { work_item_id: "discussion-real", title: "订单与通知的失败边界", state: "investigating", version: 7, context: { analysis_basis_revision: 11 } };
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ items: [item] }), { headers: { "Content-Type": "application/json" } })).mockResolvedValueOnce(new Response(JSON.stringify({ work_item: { ...item, version: 8 } }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi("http://localhost:9000");
    const discussion = await api.getDiscussion("meeting");
    expect(discussion?.id).toBe("discussion-real");
    expect(discussion?.context.analysis_basis_revision).toBe(11);
    await api.updateDiscussion("meeting", discussion!, { context: { responded_points: ["通知采用独立重试"] } });
    expect(fetcher.mock.calls[1][0]).toContain("/work-items/discussion-real");
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ expected_version: 7, context: { responded_points: ["通知采用独立重试"] } });
  });
  it("distinguishes durable job success from a timed-out coach decision", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ jobs: [{ id: "job", status: "succeeded" }], coach_decision: { job_id: "job", status: "timed_out" } }), { headers: { "Content-Type": "application/json" } })));
    await expect(new HttpMeetingApi().getCoachRequestStatus("meeting", "job")).resolves.toBe("timed_out");
  });
});

describe("safe own-job contribution outcome", () => {
  const ownJob = { id: "own", status: "succeeded", request_context: { topic_id: "orders", analysis_basis_revision: 3 } };
  const reply = (snapshot: unknown) => vi.stubGlobal("fetch", vi.fn().mockImplementation(async () => new Response(JSON.stringify(snapshot), { headers: { "Content-Type": "application/json" } })));
  it("preserves a failed own decision despite durable success and another job's old card", async () => {
    reply({ jobs: [ownJob], coach_decision: { job_id: "old", status: "stale" }, coach_history: [{ job_id: "old", status: "intervention" }], diagnostics: { coach_runtime_history: [{ job_id: "own", status: "failed", outcome: "failure", status_reason: "IntelligenceResponseValidationError" }] } });
    const result = await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own");
    expect(result).toEqual({ meetingId: "meeting", jobId: "own", status: "failed", failureCategory: "validation_rejected", canRetry: false, topicId: "orders", analysisBasisRevision: 3 });
    expect(JSON.stringify(result)).not.toContain("IntelligenceResponseValidationError");
  });
  it("ignores a foreign job failure while this request is pending", async () => {
    reply({ jobs: [{ ...ownJob, status: "running" }], coach_decision: { job_id: "foreign", status: "failed", status_reason: "evidence_rejected" } });
    expect(await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own")).toMatchObject({ status: "pending", failureCategory: null });
  });
  it("accepts only this job's published current intervention", async () => {
    reply({ jobs: [ownJob], coach_decision: { job_id: "own", status: "intervention", topic_id: "orders", analysis_basis_revision: 3 }, coach_history: [] });
    expect(await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own")).toMatchObject({ status: "succeeded", failureCategory: null, canRetry: false });
  });
  it.each([
    ["protected_silent", undefined, "no_change", null, false],
    ["timed_out", "pi_timeout", "timed_out", "timeout", true],
    ["failed", "evidence_rejected", "failed", "evidence_rejected", false],
    ["failed", "provider_unavailable", "failed", "provider_unavailable", true],
    ["failed", "private arbitrary reason", "failed", "unknown", false],
  ])("classifies only public allowlisted own codes (%s)", async (decisionStatus, reason, status, category, canRetry) => {
    reply({ jobs: [ownJob], coach_decision: { job_id: "own", status: decisionStatus, status_reason: reason, rejected_output: "must never enter outcome" } });
    const result = await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own");
    expect(result).toMatchObject({ status, failureCategory: category, canRetry });
    expect(JSON.stringify(result)).not.toMatch(/private arbitrary|must never enter|rejected_output/);
  });
  it("requires own published terminal proof while preserving the old five-string API", async () => {
    reply({ jobs: [ownJob] });
    const api = new HttpMeetingApi();
    expect(await api.getCoachRequestOutcome("meeting", "own")).toMatchObject({ status: "failed", failureCategory: "unknown" });
    expect(await api.getCoachRequestStatus("meeting", "own")).toBe("no_change");
  });
  it("does not accept another meeting's matching job ID", async () => {
    reply({ meeting_id: "foreign", jobs: [ownJob], coach_history: [{ job_id: "own", status: "intervention" }] });
    expect(await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own")).toMatchObject({ status: "failed", failureCategory: "unknown" });
  });
  it("uses the latest own terminal and never borrows another request's scope", async () => {
    reply({ jobs: [ownJob], coach_decision: { job_id: "foreign", status: "failed", topic_id: "foreign", analysis_basis_revision: 900 }, diagnostics: { coach_runtime_history: [{ job_id: "own", status: "intervention" }, { job_id: "own", status: "failed", status_reason: "evidence_rejected" }] } });
    expect(await new HttpMeetingApi().getCoachRequestOutcome("meeting", "own")).toMatchObject({ status: "failed", topicId: "orders", analysisBasisRevision: 3 });
  });
});

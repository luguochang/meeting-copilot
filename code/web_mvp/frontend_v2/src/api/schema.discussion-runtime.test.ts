import { HttpMeetingApi } from "./client";
import { parseMeetingSnapshot } from "./schema";

const snapshot = { meeting_id: "understanding", last_seq: 4, segments: [], suggestions: [], live_analysis_profile: "participation" };

it("preserves only the supplied optional historical topic cursor in a snapshot", () => {
  const topic = { id: "historical", text: "上次话题", status: "active", last_updated_seq: 8 };
  expect(parseMeetingSnapshot({ ...snapshot, current_topic: topic }).currentTopic?.lastUpdatedSeq).toBe(8);
  expect(parseMeetingSnapshot({ ...snapshot, current_topic: "用户初始话题" }).currentTopic?.lastUpdatedSeq).toBeUndefined();
  expect(parseMeetingSnapshot({ ...snapshot, current_topic: { ...topic, last_updated_seq: undefined } }).currentTopic?.lastUpdatedSeq).toBeUndefined();
});

it("reads the dedicated current discussion capability through the real HTTP client without conflating coach output or bounded jobs", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...snapshot,
    jobs: [], coach_decision: { status: "protected_silent", decision_reason: "正常静默" },
    runtime: { phase: "live", ai: { state: "paused", capabilities: {
      proactive_suggestions: { state: "idle", label: "教练监听中" },
      discussion_state: { state: "paused", label: "本轮理解未完成", decision: "failed", error_class: "evidence_rejected", job_id: "state-new", topic_id: "topic-current", analysis_basis_revision: 3, input_transcript_seq: 12, last_semantic_seq: 8 },
    } } },
  }), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const parsed = await new HttpMeetingApi().getSnapshot("understanding");
  expect(parsed.runtime.ai.capabilities?.discussion_state).toMatchObject({ state: "paused", decision: "failed", errorClass: "evidence_rejected", jobId: "state-new", topicId: "topic-current", analysisBasisRevision: 3, inputTranscriptSeq: 12, lastSemanticSeq: 8 });
  expect(parsed.runtime.ai.capabilities?.proactive_suggestions.state).toBe("idle");
  expect(parsed.coachDecision?.status).toBe("protected_silent");
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher.mock.calls[0][0]).toBe("/v2/meetings/understanding/snapshot");
  expect(fetcher.mock.calls[0][1]).not.toHaveProperty("method", "POST");
});

it("keeps a future phase and reason untrusted so the UI can show an unknown state, while old capabilities remain optional", () => {
  expect(parseMeetingSnapshot(snapshot).runtime.ai.capabilities?.discussion_state).toBeUndefined();
  const parsed = parseMeetingSnapshot({ ...snapshot, runtime: { ai: { capabilities: { discussion_state: { state: "active", decision: "future_success", error_class: "unknown_provider_text" } } } } });
  expect(parsed.runtime.ai.capabilities?.discussion_state).toMatchObject({ decision: "future_success", errorClass: "unknown_provider_text" });
});

it("parses bounded reanalysis hints without replacing the durable job decision", () => {
  const capability = (understanding_status: unknown) => parseMeetingSnapshot({ ...snapshot,
    runtime: { ai: { capabilities: { discussion_state: { state: "idle", decision: "pending", understanding_status } } } },
  }).runtime.ai.capabilities?.discussion_state;
  expect(capability("reanalysis_queued")).toMatchObject({ decision: "pending", understandingStatus: "reanalysis_queued" });
  for (const value of ["private exception body", ["reanalysis_queued"], {}, true]) {
    expect(capability(value)?.understandingStatus).toBeUndefined();
    expect(capability(value)?.decision).toBe("pending");
  }
});

import { parseMeetingSnapshot } from "./schema";

function formalMetadata(segmentId: string) {
  return {
    source: "llm_first",
    job_id: `job-${segmentId}`,
    batch_id: "batch-1",
    provider: "test-provider",
    model: "test-model",
    llm_called: true,
    formal_evidence: {
      segment_ids: [segmentId],
      quote: `quote-${segmentId}`,
    },
  };
}

it("parses bounded coach and recent-context history from a snapshot", () => {
  const snapshot = parseMeetingSnapshot({
    meeting_id: "meeting-1",
    last_seq: 4,
    segments: [],
    suggestions: [],
    coach_history: [{
      history_id: "coach-event-1",
      created_at_ms: 1_000,
      question: "请先确认回滚负责人。",
      reason: "负责人还没有明确。",
      evidence_segment_ids: ["segment-1"],
      evidence_quote: "负责人还没有定",
      urgency: "high",
      coach_event_type: "commitment_risk",
      ...formalMetadata("segment-1"),
    }],
    recent_context_history: [{
      context_id: "topic-event-1",
      kind: "topic",
      title: "发布方案",
      summary: "正在确认发布窗口和回滚安排。",
      updated_at_ms: 2_000,
      evidence_segment_ids: ["segment-2"],
      ...formalMetadata("segment-2"),
    }],
  });

  expect(snapshot.coachHistory[0]).toMatchObject({
    historyId: "coach-event-1",
    createdAtMs: 1_000,
    coachEventType: "commitment_risk",
    formalAi: { source: "llm_first", jobId: "job-segment-1" },
  });
  expect(snapshot.recentContextHistory[0]).toMatchObject({
    contextId: "topic-event-1",
    kind: "topic",
    title: "发布方案",
    summary: "正在确认发布窗口和回滚安排。",
    evidenceSegmentIds: ["segment-2"],
  });
});

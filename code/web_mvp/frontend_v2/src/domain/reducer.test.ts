import { createInitialMeetingState, meetingReducer } from "./reducer";
import type { MeetingEvent, MeetingSnapshot, Suggestion } from "./events";

function suggestion(overrides: Partial<Suggestion> = {}): Suggestion {
  return {
    suggestionId: "suggestion-1",
    meetingId: "meeting-1",
    jobId: "job-1",
    generationId: "generation-1",
    evidenceSegmentId: "segment-1",
    evidenceTranscriptSeq: 1,
    evidenceHash: "hash-1",
    stateRevision: 1,
    status: "draft",
    draftText: "是否需要确认负责人",
    draftSeq: 1,
    text: null,
    finalDraftSeq: null,
    feedback: null,
    createdAtMs: 100,
    updatedAtMs: 100,
    committedAtMs: null,
    ...overrides,
  };
}

function snapshot(overrides: Partial<MeetingSnapshot> = {}): MeetingSnapshot {
  return {
    meetingId: "meeting-1",
    title: "发布评审",
    lastSeq: 1,
    segments: [],
    activePartial: null,
    suggestions: [],
    decisionCandidates: [],
    actionItems: [],
    risks: [],
    currentTopic: null,
    openQuestions: [],
    minutes: null,
    approach: { cards: [], degraded: null, updatedAtMs: null },
    reviewJobs: {},
    audio: { status: "unknown", chunkCount: 0, durationMs: 0, fileSizeBytes: 0, tracks: [] },
    runtime: {
      phase: "live",
      recording: { state: "active", label: "录音中", level: null, detail: null },
      input: { state: "active", label: "有声音", level: 0.5, detail: null },
      ai: { state: "active", label: "在线", level: null, detail: null },
      elapsedMs: 18_000,
    },
    diagnostics: {},
    ...overrides,
  } as MeetingSnapshot;
}

function event(overrides: Partial<MeetingEvent> = {}): MeetingEvent {
  return {
    meetingId: "meeting-1",
    seq: 2,
    eventId: "event-2",
    type: "transcript.segment.finalized",
    aggregateType: "transcript_segment",
    aggregateId: "segment-2",
    occurredAtMs: 2_000,
    correlationId: null,
    causationId: null,
    idempotencyKey: "final-2",
    payload: {
      meeting_id: "meeting-1",
      segment_id: "segment-2",
      final_id: "final-2",
      transcript_seq: 2,
      text: "需要明确回滚负责人。",
      normalized_text: "需要明确回滚负责人。",
      revision: 1,
    },
    publishedAtMs: null,
    ...overrides,
  };
}

function formalAiPayload(segmentId = "segment-1") {
  return {
    source: "llm_first",
    job_id: "job-1",
    batch_id: "batch-1",
    provider: "openai_compatible_gateway",
    model: "fast-model",
    llm_called: true,
    llm_call_status: "called",
    evidence: {
      segment_ids: [segmentId],
      quote: "需要明确回滚负责人。",
    },
  };
}

describe("meetingReducer", () => {
  it("projects sealed and ready recording events without waiting for a full snapshot", () => {
    const initial = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ audio: { status: "recording", chunkCount: 1, durationMs: 5_000, fileSizeBytes: 160_000, tracks: ["microphone"] } }),
      receivedAtMs: 500,
    });
    const sealed = meetingReducer(initial, {
      type: "events.received",
      events: [event({ seq: 2, type: "recording.export.queued", payload: {} })],
      receivedAtMs: 600,
    });
    const ready = meetingReducer(sealed, {
      type: "events.received",
      events: [event({ seq: 3, type: "recording.export.ready", payload: {} })],
      receivedAtMs: 700,
    });

    expect(sealed.audio.status).toBe("assembling");
    expect(sealed.runtime.recording.label).toBe("正在整理录音");
    expect(ready.audio.status).toBe("saved");
    expect(ready.runtime.recording.label).toBe("录音已保存");
  });

  it("hydrates authoritative snapshot and keeps a committed suggestion over an older draft", () => {
    const initial = createInitialMeetingState("meeting-1");
    const committed = suggestion({
      status: "committed",
      draftSeq: 3,
      finalDraftSeq: 3,
      text: "请确认上线负责人和回滚时限。",
      committedAtMs: 400,
    });
    const hydrated = meetingReducer(initial, {
      type: "snapshot.received",
      snapshot: snapshot({ suggestions: [committed] }),
      receivedAtMs: 500,
    });
    const stale = meetingReducer(hydrated, {
      type: "snapshot.received",
      snapshot: snapshot({
        suggestions: [suggestion({ draftSeq: 2, draftText: "旧草稿" })],
      }),
      receivedAtMs: 600,
    });

    expect(stale.suggestions[0]).toMatchObject({
      status: "committed",
      text: "请确认上线负责人和回滚时限。",
      draftSeq: 3,
    });
  });

  it("keeps terminal content sealed while accepting persisted feedback", () => {
    const committed = suggestion({
      status: "committed",
      draftSeq: 3,
      finalDraftSeq: 3,
      text: "请确认上线负责人。",
    });
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ suggestions: [committed] }),
      receivedAtMs: 500,
    });
    const refreshed = meetingReducer(current, {
      type: "snapshot.received",
      snapshot: snapshot({ suggestions: [{ ...committed, feedback: "kept", text: "不应替换的正文" }] }),
      receivedAtMs: 600,
    });

    expect(refreshed.suggestions[0]).toMatchObject({
      text: "请确认上线负责人。",
      feedback: "kept",
    });
  });

  it("applies an authoritative superseded event to a committed suggestion", () => {
    const committed = suggestion({
      status: "committed",
      draftSeq: 3,
      finalDraftSeq: 3,
      text: "请确认上线负责人。",
      committedAtMs: 400,
    });
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ lastSeq: 1, suggestions: [committed] }),
      receivedAtMs: 500,
    });
    const superseded = event({
      seq: 2,
      type: "suggestion.superseded",
      aggregateType: "suggestion",
      aggregateId: committed.suggestionId,
      correlationId: committed.generationId,
      causationId: "revision-2",
      payload: {
        ...formalAiPayload(),
        ...committed,
        suggestion_id: committed.suggestionId,
        meeting_id: committed.meetingId,
        job_id: committed.jobId,
        generation_id: committed.generationId,
        evidence_segment_id: committed.evidenceSegmentId,
        evidence_transcript_seq: committed.evidenceTranscriptSeq,
        evidence_hash: committed.evidenceHash,
        state_revision: committed.stateRevision,
        draft_text: committed.draftText,
        draft_seq: committed.draftSeq,
        final_draft_seq: committed.finalDraftSeq,
        committed_at_ms: committed.committedAtMs,
        status: "superseded",
        updated_at_ms: 2_000,
      },
    });

    const state = meetingReducer(current, {
      type: "events.received",
      events: [superseded],
      receivedAtMs: 2_100,
    });

    expect(state.suggestions[0]).toMatchObject({
      status: "superseded",
      stateRevision: 1,
      updatedAtMs: 2_000,
      text: "请确认上线负责人。",
    });
  });

  it("applies an authoritative evidence remap to a committed suggestion", () => {
    const committed = suggestion({
      status: "committed",
      evidenceHash: "hash-before-correction",
      stateRevision: 1,
      draftSeq: 3,
      finalDraftSeq: 3,
      text: "请确认上线负责人。",
      committedAtMs: 400,
    });
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ lastSeq: 1, suggestions: [committed] }),
      receivedAtMs: 500,
    });
    const remapped = event({
      seq: 2,
      type: "suggestion.evidence.remapped",
      aggregateType: "suggestion",
      aggregateId: committed.suggestionId,
      correlationId: committed.generationId,
      causationId: "revision-2",
      payload: {
        ...formalAiPayload(),
        ...committed,
        suggestion_id: committed.suggestionId,
        meeting_id: committed.meetingId,
        job_id: committed.jobId,
        generation_id: committed.generationId,
        evidence_segment_id: committed.evidenceSegmentId,
        evidence_transcript_seq: committed.evidenceTranscriptSeq,
        evidence_hash: "hash-after-correction",
        state_revision: 2,
        draft_text: committed.draftText,
        draft_seq: committed.draftSeq,
        final_draft_seq: committed.finalDraftSeq,
        committed_at_ms: committed.committedAtMs,
        status: "committed",
        updated_at_ms: 2_000,
        previous_evidence_hash: committed.evidenceHash,
        evidence_remap_reason: "validated_meaning_preserved_correction",
      },
    });

    const state = meetingReducer(current, {
      type: "events.received",
      events: [remapped],
      receivedAtMs: 2_100,
    });

    expect(state.suggestions[0]).toMatchObject({
      status: "committed",
      evidenceHash: "hash-after-correction",
      stateRevision: 2,
      updatedAtMs: 2_000,
      text: "请确认上线负责人。",
    });
  });

  it("applies event sequence once and appends the final in transcript order", () => {
    const initial = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot(),
      receivedAtMs: 1_000,
    });
    const first = meetingReducer(initial, {
      type: "events.received",
      events: [event()],
      receivedAtMs: 2_100,
    });
    const duplicate = meetingReducer(first, {
      type: "events.received",
      events: [event()],
      receivedAtMs: 2_200,
    });

    expect(first.lastSeq).toBe(2);
    expect(first.segments).toHaveLength(1);
    expect(first.segments[0].normalizedText).toBe("需要明确回滚负责人。");
    expect(duplicate.segments).toHaveLength(1);
  });

  it("does not regress to a snapshot older than the event cursor", () => {
    const state = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({ seq: 4 })],
      receivedAtMs: 2_100,
    });
    const regressed = meetingReducer(state, {
      type: "snapshot.received",
      snapshot: snapshot({ lastSeq: 3, title: "旧标题" }),
      receivedAtMs: 2_200,
    });
    expect(regressed.lastSeq).toBe(4);
    expect(regressed.title).toBeNull();
  });

  it("keeps the current segment when an equal-revision snapshot has the same timestamp", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 2,
        segments: [{
          meetingId: "meeting-1",
          segmentId: "segment-1",
          finalId: "final-1",
          transcriptSeq: 1,
          text: "修正后的文字",
          normalizedText: "修正后的文字",
          startedAtMs: 100,
          endedAtMs: 900,
          revision: 2,
          evidenceHash: "hash-2",
          createdAtMs: 1_000,
          updatedAtMs: 2_000,
        }],
      }),
      receivedAtMs: 2_100,
    });
    const stale = meetingReducer(current, {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 2,
        segments: [{
          meetingId: "meeting-1",
          segmentId: "segment-1",
          finalId: "final-1",
          transcriptSeq: 1,
          text: "旧文字",
          normalizedText: "旧文字",
          startedAtMs: 100,
          endedAtMs: 900,
          revision: 2,
          evidenceHash: "hash-1",
          createdAtMs: 1_000,
          updatedAtMs: 2_000,
        }],
      }),
      receivedAtMs: 2_200,
    });

    expect(stale.segments[0].normalizedText).toBe("修正后的文字");
    expect(stale.segments[0].evidenceHash).toBe("hash-2");
  });

  it("projects real suggestion draft events and seals a committed generation", () => {
    const initial = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ lastSeq: 0 }),
      receivedAtMs: 1_000,
    });
    const suggestionEvent = (seq: number, type: string, payload: Record<string, unknown>) => event({
      seq,
      eventId: `event-${seq}`,
      type,
      aggregateType: "suggestion",
      aggregateId: "suggestion-1",
      correlationId: "generation-1",
      causationId: "job-1",
      payload: {
        ...formalAiPayload(),
        suggestion_id: "suggestion-1",
        meeting_id: "meeting-1",
        job_id: "job-1",
        generation_id: "generation-1",
        evidence_segment_id: "segment-1",
        evidence_transcript_seq: 1,
        evidence_hash: "hash-1",
        state_revision: 1,
        created_at_ms: 1_100,
        updated_at_ms: 1_100 + seq,
        ...payload,
      },
    });
    const started = suggestionEvent(1, "suggestion.draft.started", {
      status: "draft",
      draft_text: "请确认负责人",
      draft_seq: 0,
    });
    const delta = suggestionEvent(2, "suggestion.draft.delta", {
      status: "draft",
      draft_text: "请确认负责人和回滚窗口",
      draft_seq: 2,
    });
    const committed = suggestionEvent(3, "suggestion.committed", {
      status: "committed",
      draft_text: "请确认负责人和回滚窗口",
      draft_seq: 2,
      text: "谁负责本次上线，回滚窗口是什么？",
      final_draft_seq: 2,
      committed_at_ms: 1_300,
    });
    const staleDelta = suggestionEvent(4, "suggestion.draft.delta", {
      status: "draft",
      draft_text: "迟到的旧草稿",
      draft_seq: 3,
    });

    const state = meetingReducer(initial, {
      type: "events.received",
      events: [staleDelta, committed, delta, started],
      receivedAtMs: 2_000,
    });

    expect(state.suggestions).toHaveLength(1);
    expect(state.suggestions[0]).toMatchObject({
      generationId: "generation-1",
      status: "committed",
      draftSeq: 2,
      finalDraftSeq: 2,
      text: "谁负责本次上线，回滚窗口是什么？",
    });
  });

  it("projects the LLM follow-up with its reason and evidence", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.intelligence.applied",
        aggregateType: "meeting_intelligence",
        aggregateId: "intelligence-1",
        payload: {
          ...formalAiPayload("segment-2"),
          follow_up: {
            question: "请确认回滚负责人。",
            reason: "会议已经讨论发布方案，但尚未明确回滚负责人。",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "需要明确回滚负责人。",
            urgency: "high",
          },
        },
      })],
      receivedAtMs: 2_000,
    });

    expect(current.followUp).toEqual({
      question: "请确认回滚负责人。",
      reason: "会议已经讨论发布方案，但尚未明确回滚负责人。",
      evidenceSegmentIds: ["segment-2"],
      evidenceQuote: "需要明确回滚负责人。",
      urgency: "high",
      formalAi: {
        source: "llm_first",
        jobId: "job-1",
        batchId: "batch-1",
        provider: "openai_compatible_gateway",
        model: "fast-model",
        llmCalled: true,
        evidence: {
          segmentIds: ["segment-2"],
          quote: "需要明确回滚负责人。",
          evidenceHash: null,
          stateRevision: null,
        },
      },
    });
  });

  it("does not project an intelligence event without a called LLM envelope", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.intelligence.applied",
        aggregateType: "meeting_intelligence",
        aggregateId: "intelligence-not-called",
        payload: {
          source: "deterministic_candidate",
          llm_call_status: "not_called",
          llm_called: false,
          job_id: "job-not-called",
          batch_id: "batch-not-called",
          provider: "not_configured",
          model: "not_called",
          evidence: { segment_ids: ["segment-2"], quote: "需要明确回滚负责人。" },
          follow_up: {
            question: "不应进入正式 UI",
            reason: "这是 deterministic candidate",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "需要明确回滚负责人。",
            urgency: "high",
          },
        },
      })],
      receivedAtMs: 2_000,
    });

    expect(current.followUp).toBeNull();
  });

  it("does not project a deterministic fact candidate as a formal AI fact", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.decision.updated",
        aggregateType: "meeting_entity",
        aggregateId: "decision-draft",
        payload: {
          source: "deterministic_candidate",
          llm_call_status: "not_called",
          llm_called: false,
          job_id: "job-draft",
          batch_id: "batch-draft",
          provider: "not_configured",
          model: "not_called",
          evidence: { segment_ids: ["segment-2"], quote: "需要明确回滚负责人。" },
          decision: {
            id: "decision-draft",
            text: "不应渲染为正式决策",
            status: "candidate",
            evidence_segment_ids: ["segment-2"],
            evidence_spans: [],
            updated_at_ms: 2_000,
          },
        },
      })],
      receivedAtMs: 2_000,
    });

    expect(current.decisionCandidates).toEqual([]);
  });

  it("rejects a conflicting generation at the same state revision", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ suggestions: [suggestion({ generationId: "generation-new", draftSeq: 4 })] }),
      receivedAtMs: 1_000,
    });
    const staleGeneration = event({
      seq: 2,
      type: "suggestion.draft.delta",
      aggregateType: "suggestion",
      aggregateId: "suggestion-1",
      correlationId: "generation-old",
      causationId: "job-old",
      payload: {
        suggestion_id: "suggestion-1",
        meeting_id: "meeting-1",
        job_id: "job-old",
        generation_id: "generation-old",
        evidence_segment_id: "segment-1",
        evidence_transcript_seq: 1,
        state_revision: 1,
        status: "draft",
        draft_text: "旧 generation 的迟到内容",
        draft_seq: 99,
      },
    });

    const state = meetingReducer(current, {
      type: "events.received",
      events: [staleGeneration],
      receivedAtMs: 2_000,
    });
    expect(state.suggestions[0]).toMatchObject({ generationId: "generation-new", draftSeq: 4 });
  });

  it("applies transcript.segment.revised without clearing the active partial", () => {
    const snapshotState = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 1,
        segments: [{
          meetingId: "meeting-1",
          segmentId: "segment-1",
          finalId: "final-1",
          transcriptSeq: 1,
          text: "原始文字",
          normalizedText: "原始文字",
          startedAtMs: 100,
          endedAtMs: 900,
          revision: 1,
          evidenceHash: "hash-1",
          createdAtMs: 1_000,
          updatedAtMs: 1_000,
        }],
        activePartial: { segmentId: "segment-2", text: "下一句话正在识别", startedAtMs: 1_100, updatedAtMs: 1_200 },
      }),
      receivedAtMs: 1_300,
    });
    const current = meetingReducer(snapshotState, {
      type: "transcript.received",
      segments: snapshotState.segments,
    });
    const revised = event({
      seq: 2,
      type: "transcript.segment.revised",
      aggregateId: "segment-1",
      causationId: "correction-job-1",
      payload: {
        meeting_id: "meeting-1",
        segment_id: "segment-1",
        final_id: "final-1",
        transcript_seq: 1,
        text: "原始文字",
        normalized_text: "AI 修正后的文字。",
        revision: 2,
        evidence_hash: "hash-1",
      },
    });

    const state = meetingReducer(current, {
      type: "events.received",
      events: [revised],
      receivedAtMs: 2_000,
    });
    expect(state.segments[0]).toMatchObject({ normalizedText: "AI 修正后的文字。", revision: 2 });
    expect(state.fullTranscript[0]).toMatchObject({ normalizedText: "AI 修正后的文字。", revision: 2 });
    expect(state.activePartial?.text).toBe("下一句话正在识别");

    const afterStaleTranscriptPage = meetingReducer(state, {
      type: "transcript.received",
      segments: snapshotState.segments,
    });
    expect(afterStaleTranscriptPage.fullTranscript[0]).toMatchObject({
      normalizedText: "AI 修正后的文字。",
      revision: 2,
    });
  });

  it("hydrates meeting facts and applies the three typed realtime updates", () => {
    const initial = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 1,
        decisionCandidates: [{
          id: "decision-1",
          text: "先灰度 5%",
          status: "candidate",
          confidence: 0.82,
          evidenceSegmentIds: ["segment-1"],
          evidenceSpans: [],
          updatedAtMs: 1_000,
        }],
        actionItems: [],
        risks: [],
      }),
      receivedAtMs: 1_100,
    });

    const updated = meetingReducer(initial, {
      type: "events.received",
      events: [
        event({
          seq: 2,
          eventId: "decision-event",
          type: "meeting.decision.updated",
          aggregateType: "decision",
          aggregateId: "decision-1",
          payload: {
            ...formalAiPayload(),
            decision: {
              id: "decision-1",
              text: "先灰度 10%",
              status: "confirmed",
              confidence: 0.91,
              evidence_segment_ids: ["segment-1"],
              evidence_spans: [{
                segment_id: "segment-1",
                transcript_seq: 1,
                start_ms: 100,
                end_ms: 900,
                quote: "支付服务先灰度百分之十",
              }],
              updated_at_ms: 2_000,
            },
          },
        }),
        event({
          seq: 3,
          eventId: "action-event",
          type: "meeting.action_item.updated",
          aggregateType: "action_item",
          aggregateId: "action-1",
          payload: {
            ...formalAiPayload(),
            action_item: {
              id: "action-1",
              text: "张三补充回滚演练",
              status: "candidate",
              confidence: 0.77,
              evidence_segment_ids: ["segment-1"],
              evidence_spans: [],
              owner: "张三",
              deadline: "周五",
              updated_at_ms: 2_100,
            },
          },
        }),
        event({
          seq: 4,
          eventId: "risk-event",
          type: "meeting.risk.updated",
          aggregateType: "risk",
          aggregateId: "risk-1",
          payload: {
            ...formalAiPayload(),
            risk: {
              id: "risk-1",
              text: "P99 延迟可能超标",
              status: "candidate",
              confidence: 0.74,
              evidence_segment_ids: ["segment-1"],
              evidence_spans: [],
              mitigation: "超过 900ms 立即回滚",
              updated_at_ms: 2_200,
            },
          },
        }),
      ],
      receivedAtMs: 2_300,
    });

    expect(updated.decisionCandidates).toEqual([
      expect.objectContaining({ id: "decision-1", text: "先灰度 10%", status: "confirmed" }),
    ]);
    expect(updated.decisionCandidates[0].evidenceSpans[0]).toMatchObject({
      segmentId: "segment-1",
      quote: "支付服务先灰度百分之十",
    });
    expect(updated.actionItems[0]).toMatchObject({ owner: "张三", deadline: "周五" });
    expect(updated.risks[0]).toMatchObject({ mitigation: "超过 900ms 立即回滚" });
  });

  it("does not let an older snapshot overwrite a newer fact event", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.decision.updated",
        aggregateType: "decision",
        aggregateId: "decision-1",
        payload: {
          ...formalAiPayload(),
          decision: {
            id: "decision-1",
            text: "已确认灰度 10%",
            status: "confirmed",
            confidence: 0.9,
            evidence_segment_ids: ["segment-1"],
            evidence_spans: [],
            updated_at_ms: 2_000,
          },
        },
      })],
      receivedAtMs: 2_100,
    });

    const refreshed = meetingReducer(current, {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 2,
        decisionCandidates: [{
          id: "decision-1",
          text: "旧候选",
          status: "candidate",
          confidence: 0.5,
          evidenceSegmentIds: ["segment-1"],
          evidenceSpans: [],
          updatedAtMs: 1_500,
        }],
      }),
      receivedAtMs: 2_200,
    });

    expect(refreshed.decisionCandidates[0]).toMatchObject({
      text: "已确认灰度 10%",
      status: "confirmed",
      updatedAtMs: 2_000,
    });
  });

  it("projects speaker attribution from events and backfills a durable manual rename", () => {
    const withAttributedEvent = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        payload: {
          meeting_id: "meeting-1",
          segment_id: "segment-2",
          final_id: "final-2",
          transcript_seq: 2,
          text: "先确认发布范围。",
          normalized_text: "先确认发布范围。",
          revision: 1,
          speaker_id: "cluster-a",
          speaker_label: "Speaker 1",
          speaker_confidence: 0.86,
        },
      })],
      receivedAtMs: 2_100,
    });
    expect(withAttributedEvent.segments[0]).toMatchObject({
      speakerId: "cluster-a",
      speakerLabel: "Speaker 1",
      speakerConfidence: 0.86,
    });

    const semanticParagraph = {
      meetingId: "meeting-1",
      paragraphId: "paragraph-1",
      revision: 1,
      text: "先确认发布范围。",
      startMs: 1_000,
      endMs: 2_000,
      status: "stable" as const,
      checkpointIds: ["segment-2"],
      speakerId: "cluster-a",
      speakerLabel: "Speaker 1",
      speakerConfidence: 0.86,
      createdAtMs: 2_000,
      updatedAtMs: 2_000,
    };
    const beforeRename = {
      ...withAttributedEvent,
      fullTranscript: withAttributedEvent.segments,
      semanticParagraphs: [semanticParagraph],
      activeParagraph: semanticParagraph,
    };
    const renamed = meetingReducer(beforeRename, {
      type: "speaker.renamed",
      speaker: {
        meetingId: "meeting-1",
        speakerId: "cluster-a",
        speakerLabel: "张工",
        ordinal: 1,
        createdAtMs: 2_000,
        updatedAtMs: 3_000,
      },
    });

    expect(renamed.speakers).toEqual([expect.objectContaining({ speakerLabel: "张工" })]);
    expect(renamed.segments[0].speakerLabel).toBe("张工");
    expect(renamed.fullTranscript[0].speakerLabel).toBe("张工");
    expect(renamed.semanticParagraphs?.[0].speakerLabel).toBe("张工");
    expect(renamed.activeParagraph?.speakerLabel).toBe("张工");
  });
});

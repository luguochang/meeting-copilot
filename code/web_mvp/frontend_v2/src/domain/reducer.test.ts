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

function localReflexPayload(overrides: Record<string, unknown> = {}) {
  return {
    source: "local_reflex",
    llm_called: false,
    llm_call_status: "not_called",
    runtime_used: "local_reflex",
    pi_provider_attempted: false,
    evidence: {
      segment_ids: ["segment-local-reflex"],
      quote: "这个目标我再重复一下，我们先把目标说清楚",
    },
    coach_intervention: {
      origin: "local_reflex",
      runtime_used: "local_reflex",
      pi_provider_attempted: false,
      local_reflex_kind: "communication_clarity",
      status: "intervention",
      coach_event_type: "communication_clarity",
      say_this: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
      why_now: "同一观点已经连续重复，需要先收束表达。",
      evidence_segment_ids: ["segment-local-reflex"],
      evidence_quote: "这个目标我再重复一下，我们先把目标说清楚",
      urgency: "medium",
      valid_until_ms: 40_000,
      lifecycle_action: "retain",
      decision_id: "local-reflex-decision-1",
    },
    coach_decision: {
      origin: "local_reflex",
      runtime_used: "local_reflex",
      pi_provider_attempted: false,
      status: "intervention",
      decision_id: "local-reflex-decision-1",
      valid_until_ms: 40_000,
      lifecycle_action: "retain",
    },
    ...overrides,
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
    // A plain semantic follow-up is retained as the current follow-up for
    // compatibility, but it is not allowed to masquerade as coach history.
    expect(current.coachHistory).toHaveLength(0);
  });

  it("moves prior coach advice into history when a later Pi loop stays silent", () => {
    const advised = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-2"),
          follow_up: {
            question: "先确认回滚负责人。",
            reason: "负责人尚未明确。",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "需要明确回滚负责人。",
            urgency: "high",
            coach_event_type: "commitment_risk",
            origin: "pi",
            status: "intervention",
            run_id: "coach-run-2",
            decision_id: "coach-decision-2",
          },
        },
      })],
      receivedAtMs: 2_000,
    });
    const silent = meetingReducer(advised, {
      type: "events.received",
      events: [event({
        seq: 3,
        eventId: "event-3",
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-3"),
          follow_up: null,
          coach_decision: {
            origin: "pi",
            status: "protected_silent",
            decision_id: "coach-decision-3",
            decision_reason: "新证据没有形成值得打断用户的建议。",
            lifecycle_action: "deprioritize",
            agent_metrics: {
              decision_latency_ms: 1_250,
              bridge_process_reused: true,
              tool_errors: [{ tool: "submit_intervention", code: "evidence_quote_not_verbatim" }],
              provider_response: "must-not-enter-typed-state",
            },
          },
        },
      })],
      receivedAtMs: 3_000,
    });

    expect(silent.followUp).toBeNull();
    expect(silent.coachHistory).toHaveLength(1);
    expect(silent.coachHistory[0]).toMatchObject({
      decisionId: "coach-decision-2",
      question: "先确认回滚负责人。",
    });
    expect(silent.coachDecision).toMatchObject({
      decisionId: "coach-decision-3",
      status: "protected_silent",
      lifecycleAction: "deprioritize",
      agentMetrics: {
        decisionLatencyMs: 1_250,
        bridgeProcessReused: true,
        toolErrors: [{ tool: "submit_intervention", code: "evidence_quote_not_verbatim" }],
      },
    });
    expect(silent.coachDecision?.agentMetrics).not.toHaveProperty("providerResponse");
  });

  it("retracts the current coach card without deleting the prior intervention history", () => {
    const advised = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-2"),
          coach_intervention: {
            question: "先确认回滚负责人。",
            reason: "负责人尚未明确。",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "需要明确回滚负责人。",
            urgency: "high",
            coach_event_type: "commitment_risk",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-2",
            lifecycle_action: "retain",
          },
        },
      })],
      receivedAtMs: 2_000,
    });
    const retracted = meetingReducer(advised, {
      type: "events.received",
      events: [event({
        seq: 3,
        eventId: "event-3",
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-3"),
          coach_intervention: null,
          coach_decision: {
            origin: "pi",
            status: "stale",
            decision_id: "coach-decision-3",
            decision_reason: "负责人已经确认，旧建议不再成立。",
            lifecycle_action: "retract",
            supersedes_decision_id: "coach-decision-2",
            superseded_by: null,
          },
        },
      })],
      receivedAtMs: 3_000,
    });

    expect(retracted.followUp).toBeNull();
    expect(retracted.coachDecision).toMatchObject({
      status: "stale",
      lifecycleAction: "retract",
    });
    expect(retracted.coachHistory).toHaveLength(1);
    expect(retracted.coachHistory[0]).toMatchObject({
      decisionId: "coach-decision-2",
      question: "先确认回滚负责人。",
      lifecycleAction: "retract",
      supersededBy: "coach-decision-3",
    });
  });

  it("deprioritizes the previous card when a newer intervention supersedes it", () => {
    const advised = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 2,
        eventId: "coach-event-2",
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-2"),
          coach_intervention: {
            question: "先确认回滚负责人。",
            reason: "负责人尚未明确。",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "周五上线",
            urgency: "high",
            coach_event_type: "commitment_risk",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-2",
            lifecycle_action: "retain",
          },
        },
      })],
      receivedAtMs: 2_000,
    });
    const replaced = meetingReducer(advised, {
      type: "events.received",
      events: [event({
        seq: 3,
        eventId: "coach-event-3",
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-3"),
          coach_intervention: {
            question: "再确认验收条件。",
            reason: "负责人已明确，但验收条件还不完整。",
            evidence_segment_ids: ["segment-3"],
            evidence_quote: "李明负责回滚",
            urgency: "medium",
            coach_event_type: "decision_readiness",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-3",
            lifecycle_action: "retain",
            supersedes_decision_id: "coach-decision-2",
          },
        },
      })],
      receivedAtMs: 3_000,
    });

    expect(replaced.followUp).toMatchObject({
      decisionId: "coach-decision-3",
      question: "再确认验收条件。",
      supersedesDecisionId: "coach-decision-2",
    });
    expect(replaced.coachHistory).toHaveLength(2);
    expect(replaced.coachHistory[0]).toMatchObject({
      decisionId: "coach-decision-2",
      lifecycleAction: "deprioritize",
      supersededBy: "coach-decision-3",
    });
    expect(replaced.coachHistory[1]).toMatchObject({
      decisionId: "coach-decision-3",
      lifecycleAction: "retain",
    });
  });

  it("ends capture authoritatively and ignores a late coach result while keeping history", () => {
    const advised = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ lastSeq: 1 }),
      receivedAtMs: 1_000,
    });
    const withAdvice = meetingReducer(advised, {
      type: "events.received",
      events: [event({
        seq: 2,
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-2"),
          coach_intervention: {
            question: "请先确认上线负责人。",
            reason: "当前承诺没有负责人。",
            evidence_segment_ids: ["segment-2"],
            evidence_quote: "周五上线",
            urgency: "high",
            coach_event_type: "commitment_risk",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-before-end",
            lifecycle_action: "retain",
          },
        },
      })],
      receivedAtMs: 2_000,
    });
    const ended = meetingReducer(withAdvice, {
      type: "events.received",
      events: [event({ seq: 3, eventId: "meeting-ended", type: "meeting.ended", payload: {} })],
      receivedAtMs: 3_000,
    });
    const afterLateCoach = meetingReducer(ended, {
      type: "events.received",
      events: [event({
        seq: 4,
        eventId: "late-coach",
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-4"),
          coach_intervention: {
            question: "这条迟到建议不应重新出现。",
            reason: "会议已经结束。",
            evidence_segment_ids: ["segment-4"],
            evidence_quote: "会议结束后的结果",
            urgency: "high",
            coach_event_type: "goal_at_risk",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-too-late",
            lifecycle_action: "retain",
          },
        },
      })],
      receivedAtMs: 4_000,
    });

    expect(ended.runtime.phase).toBe("ended");
    expect(ended.runtime.recording.state).not.toBe("active");
    expect(ended.runtime.input.state).not.toBe("active");
    expect(ended.audio.status).not.toBe("recording");
    expect(ended.followUp).toBeNull();
    expect(ended.coachHistory).toHaveLength(1);
    expect(afterLateCoach.lastSeq).toBe(4);
    expect(afterLateCoach.followUp).toBeNull();
    expect(afterLateCoach.coachDecision).toBeNull();
    expect(afterLateCoach.coachHistory).toEqual(ended.coachHistory);

    const afterStaleLiveSnapshot = meetingReducer(afterLateCoach, {
      type: "snapshot.received",
      snapshot: snapshot({
        lastSeq: 4,
        followUp: {
          question: "快照中的迟到建议也不应重新出现。",
          reason: "会议已经结束。",
          evidenceSegmentIds: ["segment-4"],
          evidenceQuote: "会议结束后的结果",
          urgency: "high",
          coachEventType: "goal_at_risk",
          origin: "pi",
          status: "intervention",
          decisionId: "coach-decision-too-late",
          lifecycleAction: "retain",
        },
      }),
      receivedAtMs: 4_100,
    });
    expect(afterStaleLiveSnapshot.runtime.phase).toBe("ended");
    expect(afterStaleLiveSnapshot.followUp).toBeNull();
  });

  it("normalizes stale active capture fields from an ended snapshot", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({
        runtime: { ...snapshot().runtime, phase: "ended" },
        audio: { ...snapshot().audio, status: "recording" },
      }),
      receivedAtMs: 2_000,
    });

    expect(current.runtime.phase).toBe("ended");
    expect(current.runtime.recording).toMatchObject({ state: "busy", label: "正在整理录音" });
    expect(current.runtime.input).toMatchObject({ state: "idle", label: "输入已结束", level: 0 });
    expect(current.audio.status).toBe("assembling");
  });

  it("builds a bounded recent-context timeline from live formal events", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [
        event({
          seq: 2,
          eventId: "topic-event",
          type: "meeting.topic.updated",
          payload: {
            ...formalAiPayload("segment-2"),
            summary: "确认上线与回滚安排。",
            topic: { id: "current-topic", text: "发布安排", status: "active", evidence_segment_ids: ["segment-2"] },
          },
        }),
        event({
          seq: 3,
          eventId: "decision-event",
          occurredAtMs: 3_000,
          type: "meeting.decision.updated",
          payload: {
            ...formalAiPayload("segment-3"),
            decision: { id: "decision-1", text: "采用蓝绿发布", status: "confirmed", evidence_segment_ids: ["segment-3"] },
          },
        }),
      ],
      receivedAtMs: 3_000,
    });

    expect(current.recentContextHistory.map((item) => [item.kind, item.title])).toEqual([
      ["topic", "发布安排"],
      ["decision", "采用蓝绿发布"],
    ]);
  });

  it("preserves realtime coach event metadata for the private coach card", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 3,
        type: "meeting.intelligence.applied",
        aggregateType: "meeting_intelligence",
        aggregateId: "intelligence-coach-1",
        payload: {
          ...formalAiPayload("remote-segment"),
          follow_up: {
            question: "可以把周五作为目标，但需要以周四压测达标为上线条件。",
            say_this: "可以把周五作为目标，但需要以周四压测达标为上线条件。",
            reason: "对方要求确定日期，但压测尚未完成。",
            why_now: "对方要求确定日期，但压测尚未完成。",
            evidence_segment_ids: ["local-segment", "remote-segment"],
            evidence_quote: "压测还没有完成",
            urgency: "high",
            coach_event_type: "decision_readiness",
            title: "先限定承诺条件",
            confidence: 0.91,
            provenance_version: "realtime_coach_provenance.v1",
            origin: "pi",
            status: "intervention",
            status_reason: "intervention_submitted",
            decision_reason: "压测条件还未说明。",
            run_id: "coach-run-3",
            decision_id: "coach-decision-3",
            evidence_revision: "coach-evidence:3:abc",
            valid_until_ms: 12_000,
            lifecycle_action: "retain",
            superseded_by: null,
          },
        },
      })],
      receivedAtMs: 3_000,
    });

    expect(current.followUp).toMatchObject({
      coachEventType: "decision_readiness",
      sayThis: "可以把周五作为目标，但需要以周四压测达标为上线条件。",
      whyNow: "对方要求确定日期，但压测尚未完成。",
      title: "先限定承诺条件",
      confidence: 0.91,
      urgency: "high",
      origin: "pi",
      status: "intervention",
      statusReason: "intervention_submitted",
      decisionReason: "压测条件还未说明。",
      runId: "coach-run-3",
      decisionId: "coach-decision-3",
      evidenceRevision: "coach-evidence:3:abc",
      validUntil: 12_000,
      lifecycleAction: "retain",
      supersededBy: null,
    });
  });

  it("accepts canonical coach aliases when an event omits legacy names", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-canonical-card"), {
      type: "events.received",
      events: [event({
        meetingId: "meeting-canonical-card",
        seq: 4,
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-4"),
          coach_intervention: {
            say_this: "我先确认验收条件，再给出日期。",
            why_now: "现在需要避免在条件不清时作出承诺。",
            evidence_segment_ids: ["segment-4"],
            evidence_quote: "验收条件还没定",
            urgency: "high",
            coach_event_type: "commitment_risk",
          },
          coach_decision: {
            origin: "pi",
            status: "intervention",
            decision_id: "coach-decision-canonical",
          },
        },
      })],
      receivedAtMs: 4_000,
    });

    expect(current.followUp).toMatchObject({
      question: "我先确认验收条件，再给出日期。",
      reason: "现在需要避免在条件不清时作出承诺。",
      sayThis: "我先确认验收条件，再给出日期。",
      whyNow: "现在需要避免在条件不清时作出承诺。",
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

  it("projects a strictly attributed local clarity reflex without formal-AI provenance", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 5,
        eventId: "local-reflex-event-5",
        type: "meeting.intelligence.applied",
        aggregateType: "meeting_intelligence",
        aggregateId: "local-reflex-intelligence-5",
        payload: localReflexPayload(),
      })],
      receivedAtMs: 5_000,
    });

    expect(current.followUp).toMatchObject({
      origin: "local_reflex",
      localReflexKind: "communication_clarity",
      status: "intervention",
      coachEventType: "communication_clarity",
      sayThis: "我先收束一下：当前只确认目标，细节稍后逐项核对。",
      whyNow: "同一观点已经连续重复，需要先收束表达。",
      validUntil: 40_000,
      decisionId: "local-reflex-decision-1",
    });
    expect(current.followUp?.formalAi).toBeNull();
    expect(current.coachDecision).toMatchObject({
      origin: "local_reflex",
      status: "intervention",
      decisionId: "local-reflex-decision-1",
    });
    expect(current.coachHistory).toHaveLength(1);
    expect(current.coachHistory[0]).toMatchObject({
      historyId: "local-reflex-event-5",
      origin: "local_reflex",
    });
    expect(current.coachHistory[0].formalAi).toBeNull();
  });

  it.each([
    ["missing_next_step", "execution_gap"],
    ["communication_clarity", "communication_clarity"],
    ["strong_objection", "discovery_gap"],
  ] as const)("projects the valid %s/%s local-reflex event", (localReflexKind, coachEventType) => {
    const base = localReflexPayload();
    const intervention = {
      ...(base.coach_intervention as Record<string, unknown>),
      local_reflex_kind: localReflexKind,
      coach_event_type: coachEventType,
    };
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 7,
        eventId: `local-reflex-${localReflexKind}`,
        type: "meeting.intelligence.applied",
        payload: localReflexPayload({ coach_intervention: intervention }),
      })],
      receivedAtMs: 7_000,
    });

    expect(current.followUp).toMatchObject({
      origin: "local_reflex",
      localReflexKind,
      coachEventType,
    });
    expect(current.coachHistory).toHaveLength(1);
  });

  it.each([
    ["missing_next_step", "communication_clarity"],
    ["communication_clarity", "discovery_gap"],
    ["strong_objection", "execution_gap"],
    ["unknown_reflex", "execution_gap"],
  ])("rejects the mismatched local-reflex event %s/%s", (localReflexKind, coachEventType) => {
    const base = localReflexPayload();
    const intervention = {
      ...(base.coach_intervention as Record<string, unknown>),
      local_reflex_kind: localReflexKind,
      coach_event_type: coachEventType,
    };
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 8,
        type: "meeting.intelligence.applied",
        payload: localReflexPayload({ coach_intervention: intervention }),
      })],
      receivedAtMs: 8_000,
    });

    expect(current.followUp).toBeNull();
    expect(current.coachDecision).toBeNull();
    expect(current.coachHistory).toEqual([]);
  });

  it("rejects local-reflex provenance that claims a Pi provider attempt", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 9,
        type: "meeting.intelligence.applied",
        payload: localReflexPayload({ pi_provider_attempted: true }),
      })],
      receivedAtMs: 9_000,
    });

    expect(current.followUp).toBeNull();
    expect(current.coachDecision).toBeNull();
    expect(current.coachHistory).toEqual([]);
  });

  it("rejects a local-reflex envelope that tries to generate non-clarity advice", () => {
    const unsafeIntervention = {
      ...(localReflexPayload().coach_intervention as Record<string, unknown>),
      coach_event_type: "commitment_risk",
    };
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 6,
        type: "meeting.intelligence.applied",
        payload: localReflexPayload({ coach_intervention: unsafeIntervention }),
      })],
      receivedAtMs: 6_000,
    });

    expect(current.followUp).toBeNull();
    expect(current.coachDecision).toBeNull();
    expect(current.coachHistory).toEqual([]);
  });

  it("drops an invalid local-reflex entry from snapshot coach history", () => {
    const invalidHistory = {
      question: "直接承诺周五上线。",
      reason: "本地规则不允许生成承诺。",
      evidenceSegmentIds: ["segment-unsafe"],
      evidenceQuote: "周五上线",
      urgency: "high" as const,
      coachEventType: "commitment_risk" as const,
      origin: "local_reflex" as const,
      status: "intervention" as const,
      validUntil: 40_000,
      historyId: "unsafe-local-reflex-history",
      createdAtMs: 5_000,
    };
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({ coachHistory: [invalidHistory] }),
      receivedAtMs: 5_000,
    });

    expect(current.coachHistory).toEqual([]);
  });

  it("keeps a retained local-reflex snapshot card current and preserves its history identity", () => {
    const retained = {
      question: "先确认一下：下一步是什么、谁来负责、什么时候回看？",
      sayThis: "先确认一下：下一步是什么、谁来负责、什么时候回看？",
      reason: "对话正在收尾，但还没有明确下一步。",
      whyNow: "对话正在收尾，但还没有明确下一步。",
      evidenceSegmentIds: ["segment-close"],
      evidenceQuote: "我们已经把方案讨论完了今天先到这",
      urgency: "high" as const,
      coachEventType: "execution_gap" as const,
      origin: "local_reflex" as const,
      localReflexKind: "missing_next_step" as const,
      status: "intervention" as const,
      lifecycleAction: "retain" as const,
      decisionId: "local-reflex-decision-close",
      validUntil: 100_000,
      formalAi: null,
    };
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "snapshot.received",
      snapshot: snapshot({
        followUp: retained,
        coachDecision: {
          origin: "local_reflex",
          status: "intervention",
          lifecycleAction: "retain",
          decisionId: "local-reflex-decision-close",
          validUntil: 100_000,
        },
        coachHistory: [{
          ...retained,
          historyId: "local-reflex-history-close",
          createdAtMs: 10_000,
        }],
      }),
      receivedAtMs: 10_100,
    });

    expect(current.followUp).toMatchObject({
      origin: "local_reflex",
      status: "intervention",
      lifecycleAction: "retain",
      decisionId: "local-reflex-decision-close",
    });
    expect(current.coachHistory).toHaveLength(1);
    expect(current.coachHistory[0].historyId).toBe("local-reflex-history-close");
    expect(current.coachHistory[0].lifecycleAction).toBe("retain");
  });

  it("keeps a direct semantic follow-up out of Pi coach history", () => {
    const current = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [event({
        seq: 4,
        type: "meeting.intelligence.applied",
        payload: {
          ...formalAiPayload("segment-4"),
          semantic_follow_up: {
            question: "可以再补充一个真实使用场景吗？",
            reason: "这是普通语义追问，不是教练介入决策。",
            evidence_segment_ids: ["segment-4"],
            evidence_quote: "想了解更多场景",
            urgency: "medium",
          },
          coach_decision: {
            origin: "direct_intelligence",
            status: "protected_silent",
            decision_id: "coach-decision-silent-4",
            decision_reason: "本轮没有达到教练介入门槛。",
            valid_until_ms: 94_000,
            lifecycle_action: "deprioritize",
          },
        },
      })],
      receivedAtMs: 4_000,
    });

    expect(current.followUp).toBeNull();
    expect(current.semanticFollowUp).toMatchObject({
      question: "可以再补充一个真实使用场景吗？",
    });
    expect(current.coachDecision).toMatchObject({
      origin: "direct_intelligence",
      status: "protected_silent",
      decisionId: "coach-decision-silent-4",
      validUntil: 94_000,
      lifecycleAction: "deprioritize",
    });
    expect(current.coachHistory).toEqual([]);
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

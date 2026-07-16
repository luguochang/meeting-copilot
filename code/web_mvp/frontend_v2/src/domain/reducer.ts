import type {
  ActivePartial,
  ApproachCard,
  MeetingAction,
  MeetingEvent,
  MeetingSnapshot,
  MeetingViewState,
  OpenQuestionProjection,
  ReviewJobKind,
  Suggestion,
  TopicProjection,
  TranscriptSegment,
} from "./events";

const MAX_LIVE_SEGMENTS = 500;

function unknownIndicator(label: string) {
  return { state: "unknown" as const, label, level: null, detail: null };
}

export function createInitialMeetingState(meetingId: string): MeetingViewState {
  return {
    meetingId,
    title: null,
    lastSeq: 0,
    segments: [],
    archivedTranscript: "",
    archivedSegmentCount: 0,
    activePartial: null,
    suggestions: [],
    currentTopic: null,
    openQuestions: [],
    minutes: null,
    approach: { cards: [], degraded: null, updatedAtMs: null },
    reviewJobs: {},
    audio: { status: "unknown", chunkCount: 0, durationMs: 0, fileSizeBytes: 0, tracks: [] },
    runtime: {
      phase: "unknown",
      recording: unknownIndicator("录音状态待同步"),
      input: unknownIndicator("输入状态待同步"),
      ai: unknownIndicator("AI 状态待同步"),
      elapsedMs: null,
    },
    diagnostics: {},
    connection: "idle",
    lastSyncedAtMs: null,
    transportError: null,
    ending: false,
    endError: null,
    fullTranscript: [],
    fullTranscriptState: "idle",
    fullTranscriptError: null,
    audioDetail: null,
    audioLoadState: "idle",
    audioError: null,
  };
}

function segmentDisplayText(segment: TranscriptSegment): string {
  return segment.normalizedText.trim() || segment.text.trim();
}

function mergeTranscriptSegments(...collections: TranscriptSegment[][]): TranscriptSegment[] {
  const byId = new Map<string, TranscriptSegment>();
  for (const segments of collections) {
    for (const segment of segments) {
      const current = byId.get(segment.segmentId);
      if (!current || segment.revision > current.revision ||
          (segment.revision === current.revision && segment.updatedAtMs > current.updatedAtMs)) {
        byId.set(segment.segmentId, segment);
      }
    }
  }
  return [...byId.values()].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
}

function compactSegments(segments: TranscriptSegment[]) {
  const ordered = [...segments].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
  if (ordered.length <= MAX_LIVE_SEGMENTS) {
    return { segments: ordered, archivedTranscript: "", archivedSegmentCount: 0 };
  }
  const splitAt = ordered.length - MAX_LIVE_SEGMENTS;
  const archived = ordered.slice(0, splitAt);
  return {
    segments: ordered.slice(splitAt),
    archivedTranscript: archived.map(segmentDisplayText).filter(Boolean).join("\n"),
    archivedSegmentCount: archived.length,
  };
}

function mergeSuggestion(
  existing: Suggestion | undefined,
  incoming: Suggestion,
  authoritative = false,
): Suggestion {
  if (!existing) return incoming;
  if (authoritative) {
    return { ...existing, ...incoming, feedback: incoming.feedback ?? existing.feedback };
  }

  if (incoming.generationId !== existing.generationId) {
    if (incoming.stateRevision <= existing.stateRevision) return existing;
    return incoming;
  }

  if (incoming.stateRevision < existing.stateRevision) return existing;
  const existingTerminal = ["committed", "rejected", "superseded"].includes(existing.status);
  const incomingTerminal = ["committed", "rejected", "superseded"].includes(incoming.status);
  if (existingTerminal) {
    return incomingTerminal && incoming.status === existing.status
      ? { ...existing, feedback: incoming.feedback ?? existing.feedback }
      : existing;
  }
  if (!incomingTerminal && incoming.draftSeq <= existing.draftSeq) return existing;
  if (incomingTerminal && (incoming.finalDraftSeq ?? incoming.draftSeq) < existing.draftSeq) return existing;
  return { ...existing, ...incoming, feedback: incoming.feedback ?? existing.feedback };
}

function mergeSuggestions(
  current: Suggestion[],
  incoming: Suggestion[],
  authoritative = false,
): Suggestion[] {
  const byId = new Map(current.map((item) => [item.suggestionId, item]));
  for (const item of incoming) {
    byId.set(item.suggestionId, mergeSuggestion(byId.get(item.suggestionId), item, authoritative));
  }
  return [...byId.values()].sort((a, b) => {
    if (a.evidenceTranscriptSeq !== b.evidenceTranscriptSeq) {
      return a.evidenceTranscriptSeq - b.evidenceTranscriptSeq;
    }
    return a.createdAtMs - b.createdAtMs;
  });
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(record: Record<string, unknown>, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function numberValue(record: Record<string, unknown>, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function eventSegment(event: MeetingEvent): TranscriptSegment | null {
  const wrapper = asRecord(event.payload.segment) ?? event.payload;
  const segmentId = stringValue(wrapper, "segment_id", "segmentId") ?? event.aggregateId;
  const text = stringValue(wrapper, "text");
  const normalizedText = stringValue(wrapper, "normalized_text", "normalizedText") ?? text;
  const transcriptSeq = numberValue(wrapper, "transcript_seq", "transcriptSeq");
  if (!segmentId || !text || !normalizedText || transcriptSeq === null) return null;
  return {
    meetingId: event.meetingId,
    segmentId,
    finalId: stringValue(wrapper, "final_id", "finalId") ?? segmentId,
    transcriptSeq,
    text,
    normalizedText,
    startedAtMs: numberValue(wrapper, "started_at_ms", "startedAtMs"),
    endedAtMs: numberValue(wrapper, "ended_at_ms", "endedAtMs"),
    revision: numberValue(wrapper, "revision") ?? 1,
    evidenceHash: stringValue(wrapper, "evidence_hash", "evidenceHash") ?? "",
    createdAtMs: numberValue(wrapper, "created_at_ms", "createdAtMs") ?? event.occurredAtMs,
    updatedAtMs: numberValue(wrapper, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

function eventPartial(event: MeetingEvent): ActivePartial | null {
  const text = stringValue(event.payload, "text", "partial_text", "partialText");
  if (!text) return null;
  return {
    segmentId: stringValue(event.payload, "segment_id", "segmentId") ?? event.aggregateId,
    text,
    startedAtMs: numberValue(event.payload, "started_at_ms", "startedAtMs"),
    updatedAtMs: event.occurredAtMs,
  };
}

function suggestionStatus(event: MeetingEvent, value: Record<string, unknown>): Suggestion["status"] {
  if (event.type === "suggestion.committed" || event.type === "suggestion.evidence.remapped") {
    return "committed";
  }
  if (event.type === "suggestion.superseded") return "superseded";
  if (event.type === "suggestion.draft.started" || event.type === "suggestion.draft.delta") return "draft";
  const status = value.status;
  return status === "draft" || status === "validating" || status === "committed" ||
    status === "rejected" || status === "superseded"
    ? status
    : "draft";
}

function eventSuggestion(event: MeetingEvent): Suggestion | null {
  const value = asRecord(event.payload.suggestion) ?? event.payload;
  const suggestionId = stringValue(value, "suggestion_id", "suggestionId") ?? event.aggregateId;
  const generationId = stringValue(value, "generation_id", "generationId") ?? event.correlationId;
  const evidenceSegmentId = stringValue(value, "evidence_segment_id", "evidenceSegmentId");
  const evidenceTranscriptSeq = numberValue(value, "evidence_transcript_seq", "evidenceTranscriptSeq");
  if (!suggestionId || !generationId || !evidenceSegmentId || evidenceTranscriptSeq === null) return null;

  const status = suggestionStatus(event, value);
  const draftSeq = numberValue(value, "draft_seq", "draftSeq") ?? 0;
  const text = stringValue(value, "text");
  if (status === "committed" && !text) return null;
  const feedback = value.feedback;
  return {
    suggestionId,
    meetingId: stringValue(value, "meeting_id", "meetingId") ?? event.meetingId,
    jobId: stringValue(value, "job_id", "jobId") ?? event.causationId,
    generationId,
    evidenceSegmentId,
    evidenceTranscriptSeq,
    evidenceHash: stringValue(value, "evidence_hash", "evidenceHash") ?? "",
    stateRevision: numberValue(value, "state_revision", "stateRevision") ?? 1,
    status,
    draftText: stringValue(value, "draft_text", "draftText") ?? "",
    draftSeq,
    text,
    finalDraftSeq: numberValue(value, "final_draft_seq", "finalDraftSeq"),
    feedback: feedback === "kept" || feedback === "ignored" || feedback === "false_positive" || feedback === "too_late"
      ? feedback
      : null,
    createdAtMs: numberValue(value, "created_at_ms", "createdAtMs") ?? event.occurredAtMs,
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
    committedAtMs: numberValue(value, "committed_at_ms", "committedAtMs"),
  };
}

function eventTopic(event: MeetingEvent): TopicProjection | null {
  const value = asRecord(event.payload.topic) ?? event.payload;
  const text = stringValue(value, "text", "title", "topic");
  if (!text) return null;
  const evidence = value.evidence_segment_ids ?? value.evidenceSegmentIds;
  return {
    id: stringValue(value, "id", "topic_id", "topicId") ?? event.aggregateId,
    text,
    status: value.status === "active" || value.status === "changed" || value.status === "expired"
      ? value.status
      : "unknown",
    evidenceSegmentIds: Array.isArray(evidence) ? evidence.filter((item): item is string => typeof item === "string") : [],
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

function eventQuestion(event: MeetingEvent): OpenQuestionProjection | null {
  const value = asRecord(event.payload.question) ?? event.payload;
  const text = stringValue(value, "text", "question");
  if (!text) return null;
  const status = value.status;
  const evidence = value.evidence_segment_ids ?? value.evidenceSegmentIds;
  return {
    id: stringValue(value, "id", "question_id", "questionId") ?? event.aggregateId,
    text,
    status:
      status === "open" || status === "carried_over" || status === "answered" || status === "expired"
        ? status
        : "unknown",
    evidenceSegmentIds: Array.isArray(evidence) ? evidence.filter((item): item is string => typeof item === "string") : [],
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

function booleanValue(record: Record<string, unknown>, ...keys: string[]): boolean | null {
  for (const key of keys) {
    if (typeof record[key] === "boolean") return record[key];
  }
  return null;
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
}

function eventMinutes(event: MeetingEvent): MeetingSnapshot["minutes"] {
  const value = asRecord(event.payload.minutes) ?? event.payload;
  const markdown = stringValue(value, "markdown", "minutes_md", "minutesMd");
  if (!markdown) return null;
  const status = value.status;
  return {
    meetingId: stringValue(value, "meeting_id", "meetingId") ?? event.meetingId,
    jobId: stringValue(value, "job_id", "jobId") ?? event.causationId ?? "",
    version: numberValue(value, "version") ?? 1,
    status: status === "ready" || status === "degraded" ? status : "unknown",
    markdown,
    structured: asRecord(value.structured),
    createdAtMs: numberValue(value, "created_at_ms", "createdAtMs") ?? event.occurredAtMs,
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

function approachEvidenceIds(value: Record<string, unknown>): string[] {
  const direct = stringArray(value.evidence_segment_ids ?? value.evidenceSegmentIds);
  if (direct.length) return direct;
  const spans = value.evidence_spans ?? value.evidenceSpans;
  if (!Array.isArray(spans)) return [];
  return spans.flatMap((span) => {
    const item = asRecord(span);
    const segmentId = item ? stringValue(item, "segment_id", "segmentId") : null;
    return segmentId ? [segmentId] : [];
  });
}

function eventApproach(event: MeetingEvent): MeetingSnapshot["approach"] | null {
  const value = asRecord(event.payload.approach) ?? event.payload;
  const cardValues = Array.isArray(value.cards)
    ? value.cards
    : Array.isArray(event.payload.approach_cards)
      ? event.payload.approach_cards
      : null;
  if (!cardValues) return null;
  const cards = cardValues.flatMap((card): ApproachCard[] => {
    const item = asRecord(card);
    const suggestionText = item ? stringValue(item, "suggestion_text", "suggestionText", "text") : null;
    if (!item || !suggestionText) return [];
    return [{
      cardId: stringValue(item, "card_id", "cardId", "id"),
      cardType: stringValue(item, "card_type", "cardType") ?? "approach.consideration",
      suggestionText,
      triggerReason: stringValue(item, "trigger_reason", "triggerReason"),
      evidenceQuote: stringValue(item, "evidence_quote", "evidenceQuote"),
      evidenceSegmentIds: approachEvidenceIds(item),
      confidence: numberValue(item, "confidence"),
    }];
  });
  return {
    cards,
    degraded: booleanValue(value, "degraded"),
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

function completeReviewJob(state: MeetingViewState, kind: ReviewJobKind, event: MeetingEvent): MeetingViewState {
  const current = state.reviewJobs[kind];
  return {
    ...state,
    reviewJobs: {
      ...state.reviewJobs,
      [kind]: {
        id: current?.id ?? event.causationId,
        meetingId: event.meetingId,
        kind,
        status: "succeeded",
        attempts: current?.attempts ?? 0,
        maxAttempts: current?.maxAttempts ?? null,
        errorClass: null,
        output: current?.output ?? event.payload,
        updatedAtMs: event.occurredAtMs,
        completedAtMs: event.occurredAtMs,
      },
    },
  };
}

function feedbackFromEvent(event: MeetingEvent): { suggestionId: string; feedback: Suggestion["feedback"] } | null {
  const value = asRecord(event.payload.suggestion) ?? event.payload;
  const suggestionId = stringValue(value, "suggestion_id", "suggestionId") ?? event.aggregateId;
  const feedback = value.feedback;
  if (!suggestionId || (feedback !== "kept" && feedback !== "ignored" &&
      feedback !== "false_positive" && feedback !== "too_late")) return null;
  return { suggestionId, feedback };
}

function applyEvent(state: MeetingViewState, event: MeetingEvent): MeetingViewState {
  if (event.seq <= state.lastSeq || event.meetingId !== state.meetingId) return state;
  let next: MeetingViewState = { ...state, lastSeq: event.seq };

  if (event.type === "transcript.segment.finalized" || event.type === "transcript.segment.corrected" ||
      event.type === "transcript.segment.revised") {
    const segment = eventSegment(event);
    if (segment) {
      const segments = mergeTranscriptSegments(next.segments, [segment]);
      const fullTranscript = next.fullTranscript.length
        ? mergeTranscriptSegments(next.fullTranscript, [segment])
        : next.fullTranscript;
      next = {
        ...next,
        ...compactSegments(segments),
        fullTranscript,
        activePartial: event.type === "transcript.segment.finalized" ? null : next.activePartial,
      };
    }
  } else if (event.type === "suggestion.draft.started" || event.type === "suggestion.draft.delta" ||
      event.type === "suggestion.committed" || event.type === "suggestion.superseded" ||
      event.type === "suggestion.evidence.remapped") {
    const suggestion = eventSuggestion(event);
    if (suggestion) {
      const authoritative = event.type === "suggestion.superseded" ||
        event.type === "suggestion.evidence.remapped";
      next = {
        ...next,
        suggestions: mergeSuggestions(next.suggestions, [suggestion], authoritative),
      };
    }
  } else if (event.type === "transcript.segment.partial") {
    next = { ...next, activePartial: eventPartial(event) };
  } else if (event.type === "meeting.topic.updated") {
    next = { ...next, currentTopic: eventTopic(event) };
  } else if (event.type === "meeting.open_question.updated") {
    const question = eventQuestion(event);
    if (question) {
      const questions = new Map(next.openQuestions.map((item) => [item.id, item]));
      questions.set(question.id, question);
      next = { ...next, openQuestions: [...questions.values()] };
    }
  } else if (event.type === "meeting.ended") {
    next = {
      ...next,
      activePartial: null,
      ending: false,
      runtime: { ...next.runtime, phase: "ended" },
    };
  } else if (event.type === "meeting.minutes.ready") {
    const minutes = eventMinutes(event);
    next = completeReviewJob(minutes ? { ...next, minutes } : next, "minutes", event);
  } else if (event.type === "meeting.approach.ready") {
    const approach = eventApproach(event);
    next = completeReviewJob(approach ? { ...next, approach } : next, "approach", event);
  } else if (event.type === "meeting.index.ready") {
    next = completeReviewJob(next, "index", event);
  } else if (event.type === "recording.chunk.committed") {
    const durationMs = numberValue(event.payload, "duration_ms", "durationMs") ?? 0;
    const fileSizeBytes = numberValue(event.payload, "file_size_bytes", "fileSizeBytes") ?? 0;
    const track = stringValue(event.payload, "track");
    next = {
      ...next,
      audio: {
        ...next.audio,
        chunkCount: next.audio.chunkCount + 1,
        durationMs: next.audio.durationMs + durationMs,
        fileSizeBytes: next.audio.fileSizeBytes + fileSizeBytes,
        tracks: track && !next.audio.tracks.includes(track) ? [...next.audio.tracks, track] : next.audio.tracks,
      },
    };
  } else if (event.type === "recording.sealed" || event.type === "recording.interrupted" ||
      event.type === "recording.export.queued") {
    next = {
      ...next,
      audio: { ...next.audio, status: "assembling" },
      runtime: {
        ...next.runtime,
        recording: { state: "busy", label: "正在整理录音", level: null, detail: null },
      },
    };
  } else if (event.type === "recording.export.ready") {
    next = {
      ...next,
      audio: { ...next.audio, status: "saved" },
      runtime: {
        ...next.runtime,
        recording: { state: "idle", label: "录音已保存", level: null, detail: null },
      },
    };
  } else if (event.type === "recording.failed") {
    next = {
      ...next,
      audio: { ...next.audio, status: "failed" },
      runtime: {
        ...next.runtime,
        recording: { state: "error", label: "录音整理失败", level: null, detail: null },
      },
    };
  } else if (event.type === "suggestion.feedback.updated") {
    const feedback = feedbackFromEvent(event);
    if (feedback) {
      next = {
        ...next,
        suggestions: next.suggestions.map((item) => item.suggestionId === feedback.suggestionId
          ? { ...item, feedback: feedback.feedback }
          : item),
      };
    }
  }

  return next;
}

function applySnapshot(state: MeetingViewState, snapshot: MeetingSnapshot, receivedAtMs: number): MeetingViewState {
  if (snapshot.meetingId !== state.meetingId || snapshot.lastSeq < state.lastSeq) return state;
  const snapshotSegments = mergeTranscriptSegments(state.segments, snapshot.segments);
  const compacted = compactSegments(snapshotSegments);
  return {
    ...state,
    ...snapshot,
    ...compacted,
    fullTranscript: state.fullTranscript.length
      ? mergeTranscriptSegments(state.fullTranscript, snapshotSegments)
      : state.fullTranscript,
    suggestions: mergeSuggestions(state.suggestions, snapshot.suggestions),
    connection: "live",
    lastSyncedAtMs: receivedAtMs,
    transportError: null,
    ending: snapshot.runtime.phase === "ending",
    endError: null,
  };
}

export function meetingReducer(state: MeetingViewState, action: MeetingAction): MeetingViewState {
  switch (action.type) {
    case "meeting.bound":
      return action.meetingId === state.meetingId
        ? state
        : createInitialMeetingState(action.meetingId);
    case "snapshot.received":
      return applySnapshot(state, action.snapshot, action.receivedAtMs);
    case "events.received": {
      const next = [...action.events]
        .sort((a, b) => a.seq - b.seq)
        .reduce(applyEvent, state);
      return { ...next, lastSyncedAtMs: action.receivedAtMs, transportError: null };
    }
    case "connection.changed":
      return {
        ...state,
        connection: action.connection,
        transportError: action.error === undefined ? state.transportError : action.error,
      };
    case "meeting.ending":
      return {
        ...state,
        ending: true,
        endError: null,
        runtime: { ...state.runtime, phase: "ending" },
      };
    case "meeting.end_failed":
      return { ...state, ending: false, endError: action.error };
    case "suggestion.feedback_saved":
      return {
        ...state,
        suggestions: state.suggestions.map((item) =>
          item.suggestionId === action.suggestionId ? { ...item, feedback: action.feedback } : item,
        ),
      };
    case "transcript.loading":
      return { ...state, fullTranscriptState: "loading", fullTranscriptError: null };
    case "transcript.received":
      return {
        ...state,
        fullTranscript: mergeTranscriptSegments(
          action.segments,
          state.fullTranscript,
          state.segments,
        ),
        fullTranscriptState: "ready",
        fullTranscriptError: null,
      };
    case "transcript.failed":
      return { ...state, fullTranscriptState: "error", fullTranscriptError: action.error };
    case "audio.loading":
      return { ...state, audioLoadState: "loading", audioError: null };
    case "audio.received":
      return {
        ...state,
        audio: action.audio,
        audioDetail: action.audio,
        audioLoadState: "ready",
        audioError: null,
      };
    case "audio.failed":
      return { ...state, audioLoadState: "error", audioError: action.error };
    default:
      return state;
  }
}

export function createSnapshotForTest(overrides: Partial<MeetingSnapshot> = {}): MeetingSnapshot {
  const meetingId = overrides.meetingId ?? "meeting-test";
  return {
    ...createInitialMeetingState(meetingId),
    ...overrides,
    meetingId,
  };
}

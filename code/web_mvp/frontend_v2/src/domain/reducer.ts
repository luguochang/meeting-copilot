import {
  isFormalLlmFirstPayload,
} from "./events";
import type {
  ActivePartial,
  ApproachCard,
  ActionItemProjection,
  DecisionCandidate,
  EvidenceSpan,
  FollowUpProjection,
  MeetingAction,
  MeetingEvent,
  MeetingFact,
  MeetingFactKind,
  MeetingFactStatus,
  MeetingSnapshot,
  MeetingSpeaker,
  MeetingViewState,
  OpenQuestionProjection,
  RiskProjection,
  ReviewJobKind,
  Suggestion,
  TopicProjection,
  TranscriptSegment,
} from "./events";

function unknownIndicator(label: string) {
  return { state: "unknown" as const, label, level: null, detail: null };
}

export function createInitialMeetingState(meetingId: string): MeetingViewState {
  return {
    meetingId,
    title: null,
    titleSource: "unknown",
    updatedAtMs: 0,
    lastSeq: 0,
    segments: [],
    semanticParagraphs: [],
    activeParagraph: null,
    archivedTranscript: "",
    archivedSegmentCount: 0,
    activePartial: null,
    suggestions: [],
    decisionCandidates: [],
    actionItems: [],
    risks: [],
    currentTopic: null,
    openQuestions: [],
    followUp: null,
    minutes: null,
    approach: { cards: [], degraded: null, updatedAtMs: null },
    reviewJobs: {},
    documents: {},
    importJob: null,
    audio: { status: "unknown", chunkCount: 0, durationMs: 0, fileSizeBytes: 0, tracks: [] },
    runtime: {
      phase: "unknown",
      recording: unknownIndicator("录音状态待读取"),
      input: unknownIndicator("输入状态待读取"),
      ai: unknownIndicator("AI 状态待读取"),
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
    speakers: [],
    speakerLoadState: "idle",
    speakerError: null,
  };
}

interface SpeakerAttribution {
  speakerId?: string | null;
  speakerLabel?: string | null;
}

function applySpeakerLabels<T extends SpeakerAttribution>(
  items: T[],
  speakers: MeetingSpeaker[],
): T[] {
  if (!items.length || !speakers.length) return items;
  const labels = new Map(speakers.map((speaker) => [speaker.speakerId, speaker.speakerLabel]));
  return items.map((item) => {
    const label = item.speakerId ? labels.get(item.speakerId) : null;
    return label && label !== item.speakerLabel ? { ...item, speakerLabel: label } : item;
  });
}

function mergeMeetingSpeakers(current: MeetingSpeaker[], incoming: MeetingSpeaker[]): MeetingSpeaker[] {
  const byId = new Map(current.map((speaker) => [speaker.speakerId, speaker]));
  for (const speaker of incoming) byId.set(speaker.speakerId, speaker);
  return [...byId.values()].sort((left, right) => left.ordinal - right.ordinal);
}

function applySpeakers(state: MeetingViewState, speakers: MeetingSpeaker[]): MeetingViewState {
  const mergedSpeakers = mergeMeetingSpeakers(state.speakers, speakers);
  return {
    ...state,
    speakers: mergedSpeakers,
    segments: applySpeakerLabels(state.segments, mergedSpeakers),
    fullTranscript: applySpeakerLabels(state.fullTranscript, mergedSpeakers),
    semanticParagraphs: applySpeakerLabels(state.semanticParagraphs ?? [], mergedSpeakers),
    activeParagraph: state.activeParagraph
      ? applySpeakerLabels([state.activeParagraph], mergedSpeakers)[0]
      : null,
    speakerLoadState: "ready",
    speakerError: null,
  };
}

function mergeTranscriptSegments(...collections: TranscriptSegment[][]): TranscriptSegment[] {
  const byId = new Map<string, TranscriptSegment>();
  for (const segments of collections) {
    for (const segment of segments) {
      const current = byId.get(segment.segmentId);
      if (!current) {
        byId.set(segment.segmentId, segment);
        continue;
      }
      const textWinner = segment.revision > current.revision ||
          (segment.revision === current.revision && segment.updatedAtMs > current.updatedAtMs)
        ? segment
        : current;
      const speakerWinner = selectSpeakerProjection(current, segment, textWinner);
      byId.set(segment.segmentId, {
        ...textWinner,
        speakerId: speakerWinner.speakerId,
        speakerLabel: speakerWinner.speakerLabel,
        speakerConfidence: speakerWinner.speakerConfidence,
        speakerAttributionRevision: speakerWinner.speakerAttributionRevision,
        speakerAttributionSource: speakerWinner.speakerAttributionSource,
        speakerAttributionReason: speakerWinner.speakerAttributionReason,
      });
    }
  }
  return [...byId.values()].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
}

function selectSpeakerProjection(
  current: TranscriptSegment,
  incoming: TranscriptSegment,
  fallback: TranscriptSegment,
): TranscriptSegment {
  const currentRevision = current.speakerAttributionRevision ?? 0;
  const incomingRevision = incoming.speakerAttributionRevision ?? 0;
  if (incomingRevision > currentRevision) return incoming;
  if (currentRevision > incomingRevision) return current;
  if (incomingRevision > 0) return incoming.updatedAtMs > current.updatedAtMs ? incoming : current;
  if (incoming.speakerId && !current.speakerId) return incoming;
  if (current.speakerId && !incoming.speakerId) return current;
  return fallback;
}

function compactSegments(segments: TranscriptSegment[]) {
  const ordered = [...segments].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
  return {
    segments: ordered,
    archivedTranscript: "",
    archivedSegmentCount: 0,
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
    speakerId: stringValue(wrapper, "speaker_id", "speakerId"),
    speakerLabel: stringValue(wrapper, "speaker_label", "speakerLabel"),
    speakerConfidence: numberValue(wrapper, "speaker_confidence", "speakerConfidence"),
    speakerAttributionRevision: numberValue(
      wrapper,
      "speaker_attribution_revision",
      "speakerAttributionRevision",
    ) ?? 0,
    speakerAttributionSource: stringValue(
      wrapper,
      "speaker_attribution_source",
      "speakerAttributionSource",
    ),
    speakerAttributionReason: stringValue(
      wrapper,
      "speaker_attribution_reason",
      "speakerAttributionReason",
    ),
    correctionStatus: stringValue(wrapper, "correction_status", "correctionStatus") ?? "pending",
    correctionBeforeText: stringValue(wrapper, "correction_before_text", "correctionBeforeText"),
    correctionAfterText: stringValue(wrapper, "correction_after_text", "correctionAfterText"),
    correctionErrorClass: stringValue(wrapper, "correction_error_class", "correctionErrorClass"),
    correctionUpdatedAtMs: numberValue(wrapper, "correction_updated_at_ms", "correctionUpdatedAtMs"),
    createdAtMs: numberValue(wrapper, "created_at_ms", "createdAtMs") ?? event.occurredAtMs,
    updatedAtMs: numberValue(wrapper, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
  };
}

interface SpeakerRevisionProjection {
  segmentId: string;
  attributionRevision: number;
  speakerId: string | null;
  speakerLabel: string | null;
  speakerConfidence: number | null;
  source: string;
  reason: string;
}

function eventSpeakerRevision(event: MeetingEvent): SpeakerRevisionProjection | null {
  const payload = event.payload;
  const segmentId = stringValue(payload, "segment_id");
  const meetingId = stringValue(payload, "meeting_id");
  const attributionRevision = numberValue(payload, "attribution_revision");
  const runId = stringValue(payload, "run_id");
  const source = stringValue(payload, "source");
  const reason = stringValue(payload, "reason");
  const rawSpeakerId = payload.speaker_id;
  const rawSpeakerLabel = payload.speaker_label;
  const rawConfidence = payload.speaker_confidence;
  if (event.aggregateType !== "transcript_segment" || segmentId !== event.aggregateId ||
      meetingId !== event.meetingId || !runId || !source || !reason ||
      attributionRevision === null || !Number.isInteger(attributionRevision) || attributionRevision < 1 ||
      (rawSpeakerId !== null && (typeof rawSpeakerId !== "string" || !rawSpeakerId.trim())) ||
      (rawSpeakerLabel !== null && (typeof rawSpeakerLabel !== "string" || !rawSpeakerLabel.trim())) ||
      (rawConfidence !== null && (typeof rawConfidence !== "number" || !Number.isFinite(rawConfidence) ||
        rawConfidence < 0 || rawConfidence > 1))) {
    return null;
  }
  const speakerId = rawSpeakerId === null ? null : rawSpeakerId.trim();
  const speakerLabel = rawSpeakerLabel === null ? null : rawSpeakerLabel.trim();
  if ((speakerId === null && (speakerLabel !== null || rawConfidence !== null)) ||
      (speakerId !== null && speakerLabel === null)) return null;
  return {
    segmentId,
    attributionRevision,
    speakerId,
    speakerLabel,
    speakerConfidence: rawConfidence,
    source,
    reason,
  };
}

function applySpeakerRevision(
  segments: TranscriptSegment[],
  revision: SpeakerRevisionProjection,
): TranscriptSegment[] {
  return segments.map((segment) => {
    if (segment.segmentId !== revision.segmentId ||
        revision.attributionRevision <= (segment.speakerAttributionRevision ?? 0)) return segment;
    return {
      ...segment,
      speakerId: revision.speakerId,
      speakerLabel: revision.speakerLabel,
      speakerConfidence: revision.speakerConfidence,
      speakerAttributionRevision: revision.attributionRevision,
      speakerAttributionSource: revision.source,
      speakerAttributionReason: revision.reason,
    };
  });
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
    formalAi: formalAiFromPayload(event.payload),
  };
}

function eventTopic(event: MeetingEvent): TopicProjection | null {
  const value = asRecord(event.payload.topic) ?? event.payload;
  const provenance = isFormalLlmFirstPayload(event.payload) ? event.payload : value;
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
    formalAi: formalAiFromPayload(provenance),
  };
}

function eventQuestion(event: MeetingEvent): OpenQuestionProjection | null {
  const value = asRecord(event.payload.question) ?? event.payload;
  const provenance = isFormalLlmFirstPayload(event.payload) ? event.payload : value;
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
    formalAi: formalAiFromPayload(provenance),
  };
}

function eventFollowUp(event: MeetingEvent): FollowUpProjection | null {
  const value = asRecord(event.payload.follow_up ?? event.payload.followUp);
  if (!value) return null;
  const question = stringValue(value, "question");
  const reason = stringValue(value, "reason");
  if (!question || !reason) return null;
  const urgency = stringValue(value, "urgency");
  return {
    question,
    reason,
    evidenceSegmentIds: stringArray(value.evidence_segment_ids ?? value.evidenceSegmentIds),
    evidenceQuote: stringValue(value, "evidence_quote", "evidenceQuote") ?? "",
    urgency: urgency === "low" || urgency === "high" ? urgency : "medium",
    formalAi: formalAiFromPayload(event.payload),
  };
}

function formalAiFromPayload(value: Record<string, unknown>): MeetingFact["formalAi"] {
  if (!isFormalLlmFirstPayload(value)) return null;
  const evidence = value.evidence as Record<string, unknown>;
  return {
    source: "llm_first",
    jobId: value.job_id as string,
    batchId: value.batch_id as string,
    provider: value.provider as string,
    model: value.model as string,
    llmCalled: true,
    evidence: {
      segmentIds: (evidence.segment_ids as unknown[]).filter((item): item is string => typeof item === "string"),
      quote: typeof evidence.quote === "string" ? evidence.quote : "",
      evidenceHash: typeof evidence.evidence_hash === "string" ? evidence.evidence_hash : null,
      stateRevision: typeof evidence.state_revision === "number" ? evidence.state_revision : null,
    },
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

function factStatus(value: unknown): MeetingFactStatus {
  return typeof value === "string" && value.trim() ? value.trim() as MeetingFactStatus : "unknown";
}

function eventEvidenceSpans(value: Record<string, unknown>): EvidenceSpan[] {
  const spans = value.evidence_spans ?? value.evidenceSpans;
  if (!Array.isArray(spans)) return [];
  return spans.flatMap((span) => {
    const item = asRecord(span);
    const segmentId = item ? stringValue(item, "segment_id", "segmentId") : null;
    const transcriptSeq = item ? numberValue(item, "transcript_seq", "transcriptSeq") : null;
    if (!segmentId || transcriptSeq === null) return [];
    return [{
      segmentId,
      transcriptSeq,
      startMs: item ? numberValue(item, "start_ms", "startMs") : null,
      endMs: item ? numberValue(item, "end_ms", "endMs") : null,
      quote: item ? stringValue(item, "quote") ?? "" : "",
    }];
  });
}

function eventFact(event: MeetingEvent, kind: MeetingFactKind): MeetingFact | null {
  const key = kind === "decision" ? "decision" : kind === "action_item" ? "action_item" : "risk";
  const value = asRecord(event.payload[key]) ?? event.payload;
  const id = stringValue(value, "id", "fact_id", "factId") ?? event.aggregateId;
  const text = stringValue(value, "text", "statement", "description", "title");
  if (!id || !text) return null;
  const evidenceSpans = eventEvidenceSpans(value);
  const directEvidence = stringArray(value.evidence_segment_ids ?? value.evidenceSegmentIds);
  const base = {
    id,
    text,
    status: factStatus(value.status),
    confidence: numberValue(value, "confidence"),
    evidenceSegmentIds: directEvidence.length
      ? directEvidence
      : evidenceSpans.map((span) => span.segmentId),
    evidenceSpans,
    updatedAtMs: numberValue(value, "updated_at_ms", "updatedAtMs") ?? event.occurredAtMs,
    formalAi: formalAiFromPayload(event.payload),
  };
  if (kind === "decision") return base as DecisionCandidate;
  if (kind === "action_item") {
    return {
      ...base,
      owner: stringValue(value, "owner", "owner_name", "ownerName"),
      deadline: stringValue(value, "deadline", "due", "due_at", "dueAt"),
    } as ActionItemProjection;
  }
  return {
    ...base,
    mitigation: stringValue(value, "mitigation", "mitigation_text", "mitigationText"),
  } as RiskProjection;
}

function mergeFact<T extends MeetingFact>(existing: T | undefined, incoming: T): T {
  if (!existing || incoming.updatedAtMs >= existing.updatedAtMs) return incoming;
  return existing;
}

function mergeFacts<T extends MeetingFact>(current: T[], incoming: T[]): T[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, mergeFact(byId.get(item.id) as T | undefined, item));
  return [...byId.values()].sort((a, b) => a.updatedAtMs - b.updatedAtMs || a.id.localeCompare(b.id));
}

function updateFactStatus(state: MeetingViewState, factType: MeetingFactKind, factId: string, status: MeetingFactStatus): MeetingViewState {
  const update = <T extends MeetingFact>(items: T[]) => items.map((item) => item.id === factId ? { ...item, status } : item);
  if (factType === "decision") return { ...state, decisionCandidates: update(state.decisionCandidates) };
  if (factType === "action_item") return { ...state, actionItems: update(state.actionItems) };
  return { ...state, risks: update(state.risks) };
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

  const formalAiEvent = event.type === "meeting.topic.updated" || event.type === "meeting.open_question.updated" ||
    event.type === "meeting.intelligence.applied" || event.type === "meeting.decision.updated" ||
    event.type === "meeting.action_item.updated" || event.type === "meeting.risk.updated" ||
    event.type === "suggestion.draft.started" || event.type === "suggestion.draft.delta" ||
    event.type === "suggestion.committed" || event.type === "suggestion.superseded" ||
    event.type === "suggestion.evidence.remapped";
  if (formalAiEvent && !isFormalLlmFirstPayload(event.payload)) return next;

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
  } else if (event.type === "transcript.segment.speaker_revised") {
    const revision = eventSpeakerRevision(event);
    if (revision) {
      next = {
        ...next,
        segments: applySpeakerRevision(next.segments, revision),
        fullTranscript: applySpeakerRevision(next.fullTranscript, revision),
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
  } else if (event.type === "meeting.intelligence.applied") {
    next = { ...next, followUp: eventFollowUp(event) };
  } else if (event.type === "meeting.decision.updated" || event.type === "meeting.action_item.updated" || event.type === "meeting.risk.updated") {
    const kind = event.type === "meeting.decision.updated"
      ? "decision"
      : event.type === "meeting.action_item.updated"
        ? "action_item"
        : "risk";
    const fact = eventFact(event, kind);
    if (fact) {
      next = kind === "decision"
        ? { ...next, decisionCandidates: mergeFacts(next.decisionCandidates, [fact as DecisionCandidate]) }
        : kind === "action_item"
          ? { ...next, actionItems: mergeFacts(next.actionItems, [fact as ActionItemProjection]) }
          : { ...next, risks: mergeFacts(next.risks, [fact as RiskProjection]) };
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
    semanticParagraphs: snapshot.semanticParagraphs ?? [],
    activeParagraph: snapshot.activeParagraph ?? null,
    ...compacted,
    fullTranscript: state.fullTranscript.length
      ? mergeTranscriptSegments(state.fullTranscript, snapshotSegments)
      : state.fullTranscript,
    suggestions: mergeSuggestions(state.suggestions, snapshot.suggestions),
    decisionCandidates: mergeFacts(state.decisionCandidates, snapshot.decisionCandidates),
    actionItems: mergeFacts(state.actionItems, snapshot.actionItems),
    risks: mergeFacts(state.risks, snapshot.risks),
    followUp: snapshot.followUp ?? null,
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
    case "fact.status_saved":
      return updateFactStatus(state, action.factType, action.factId, action.status);
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
    case "speakers.loading":
      return { ...state, speakerLoadState: "loading", speakerError: null };
    case "speakers.received":
      return applySpeakers(state, action.speakers);
    case "speakers.failed":
      return { ...state, speakerLoadState: "error", speakerError: action.error };
    case "speaker.renamed":
      return applySpeakers(state, [action.speaker]);
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

import type {
  ActivePartial,
  ApproachCard,
  ApproachReview,
  AudioChunk,
  EventsPage,
  MeetingAudio,
  MeetingAudioSummary,
  MeetingEvent,
  MeetingHistory,
  MeetingHistoryItem,
  MeetingRuntime,
  MeetingSnapshot,
  MinutesArtifact,
  OpenQuestionProjection,
  ReviewJob,
  ReviewJobKind,
  ReviewJobs,
  RuntimeIndicator,
  RuntimeState,
  Suggestion,
  SuggestionFeedback,
  SuggestionStatus,
  TopicProjection,
  TranscriptPage,
  TranscriptSegment,
} from "../domain/events";

type JsonRecord = Record<string, unknown>;

export class ContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContractError";
  }
}

function record(value: unknown, field = "response"): JsonRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new ContractError(`${field} must be an object`);
  }
  return value as JsonRecord;
}

function optionalRecord(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonRecord)
    : null;
}

function requiredString(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new ContractError(`${field} must be a non-empty string`);
  return value.trim();
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function requiredNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) throw new ContractError(`${field} must be a number`);
  return value;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function optionalBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && Boolean(item.trim())) : [];
}

function first(source: JsonRecord, ...keys: string[]): unknown {
  for (const key of keys) if (key in source) return source[key];
  return undefined;
}

function parseSegment(value: unknown, index: number): TranscriptSegment {
  const item = record(value, `segments[${index}]`);
  const text = requiredString(item.text, `segments[${index}].text`);
  return {
    meetingId: requiredString(first(item, "meeting_id", "meetingId"), `segments[${index}].meeting_id`),
    segmentId: requiredString(first(item, "segment_id", "segmentId"), `segments[${index}].segment_id`),
    finalId: requiredString(first(item, "final_id", "finalId"), `segments[${index}].final_id`),
    transcriptSeq: requiredNumber(first(item, "transcript_seq", "transcriptSeq"), `segments[${index}].transcript_seq`),
    text,
    normalizedText: optionalString(first(item, "normalized_text", "normalizedText")) ?? text,
    startedAtMs: optionalNumber(first(item, "started_at_ms", "startedAtMs")),
    endedAtMs: optionalNumber(first(item, "ended_at_ms", "endedAtMs")),
    revision: optionalNumber(item.revision) ?? 1,
    evidenceHash: optionalString(first(item, "evidence_hash", "evidenceHash")) ?? "",
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

function parseReviewJobStatus(value: unknown): ReviewJob["status"] {
  return value === "pending" || value === "running" || value === "retry_wait" ||
    value === "succeeded" || value === "failed" || value === "cancelled"
    ? value
    : "unknown";
}

function parseReviewJob(value: unknown, kind: ReviewJobKind, meetingId: string): ReviewJob | null {
  const item = optionalRecord(value);
  if (!item) return null;
  return {
    id: optionalString(first(item, "id", "job_id", "jobId")),
    meetingId: optionalString(first(item, "meeting_id", "meetingId")) ?? meetingId,
    kind,
    status: parseReviewJobStatus(item.status),
    attempts: optionalNumber(item.attempts) ?? 0,
    maxAttempts: optionalNumber(first(item, "max_attempts", "maxAttempts")),
    errorClass: optionalString(first(item, "error_class", "errorClass", "error")),
    output: optionalRecord(item.output),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")),
    completedAtMs: optionalNumber(first(item, "completed_at_ms", "completedAtMs")),
  };
}

function parseReviewJobs(value: unknown, meetingId: string): ReviewJobs {
  const source = optionalRecord(value);
  if (!source) return {};
  const jobs: ReviewJobs = {};
  for (const kind of ["minutes", "approach", "index"] as const) {
    const parsed = parseReviewJob(source[kind], kind, meetingId);
    if (parsed) jobs[kind] = parsed;
  }
  return jobs;
}

function parseMinutes(value: unknown): MinutesArtifact | null {
  const item = optionalRecord(value);
  if (!item) return null;
  const markdown = optionalString(first(item, "markdown", "minutes_md", "minutesMd"));
  if (!markdown) return null;
  const status = item.status;
  return {
    meetingId: optionalString(first(item, "meeting_id", "meetingId")) ?? "",
    jobId: optionalString(first(item, "job_id", "jobId")) ?? "",
    version: optionalNumber(item.version) ?? 1,
    status: status === "ready" || status === "degraded" ? status : "unknown",
    markdown,
    structured: optionalRecord(item.structured),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

function evidenceSegmentIds(item: JsonRecord): string[] {
  const direct = strings(first(item, "evidence_segment_ids", "evidenceSegmentIds"));
  if (direct.length) return direct;
  const spans = first(item, "evidence_spans", "evidenceSpans");
  if (!Array.isArray(spans)) return [];
  return spans.flatMap((span) => {
    const value = optionalRecord(span);
    const segmentId = value ? optionalString(first(value, "segment_id", "segmentId")) : null;
    return segmentId ? [segmentId] : [];
  });
}

function parseApproachCard(value: unknown): ApproachCard | null {
  const item = optionalRecord(value);
  if (!item) return null;
  const suggestionText = optionalString(first(item, "suggestion_text", "suggestionText", "text"));
  if (!suggestionText) return null;
  return {
    cardId: optionalString(first(item, "card_id", "cardId", "id")),
    cardType: optionalString(first(item, "card_type", "cardType")) ?? "approach.consideration",
    suggestionText,
    triggerReason: optionalString(first(item, "trigger_reason", "triggerReason")),
    evidenceQuote: optionalString(first(item, "evidence_quote", "evidenceQuote")),
    evidenceSegmentIds: evidenceSegmentIds(item),
    confidence: optionalNumber(item.confidence),
  };
}

function degradedFromReviewJob(job: ReviewJob | undefined): boolean | null {
  return job?.output ? optionalBoolean(job.output.degraded) : null;
}

function parseApproach(value: unknown, job: ReviewJob | undefined): ApproachReview {
  const artifact = optionalRecord(value);
  const cardValues = Array.isArray(value)
    ? value
    : artifact && Array.isArray(artifact.cards)
      ? artifact.cards
      : [];
  const cards = cardValues
    .map(parseApproachCard)
    .filter((card): card is ApproachCard => card !== null);
  return {
    cards,
    degraded: artifact ? optionalBoolean(artifact.degraded) ?? degradedFromReviewJob(job) : degradedFromReviewJob(job),
    updatedAtMs: artifact
      ? optionalNumber(first(artifact, "updated_at_ms", "updatedAtMs")) ?? job?.updatedAtMs ?? null
      : job?.updatedAtMs ?? null,
  };
}

function parseAudioStatus(value: unknown): MeetingAudioSummary["status"] {
  return value === "recording" || value === "saved" || value === "assembling" || value === "failed"
    ? value
    : "unknown";
}

function parseAudioSummary(value: unknown): MeetingAudioSummary {
  const item = optionalRecord(value) ?? {};
  return {
    status: parseAudioStatus(item.status),
    chunkCount: optionalNumber(first(item, "chunk_count", "chunkCount")) ?? 0,
    durationMs: optionalNumber(first(item, "duration_ms", "durationMs")) ?? 0,
    fileSizeBytes: optionalNumber(first(item, "file_size_bytes", "fileSizeBytes")) ?? 0,
    tracks: strings(item.tracks),
  };
}

function parseAudioChunk(value: unknown, index: number): AudioChunk {
  const item = record(value, `chunks[${index}]`);
  const status = item.status;
  return {
    track: optionalString(item.track) ?? "",
    epoch: optionalNumber(item.epoch) ?? 0,
    chunkSeq: optionalNumber(first(item, "chunk_seq", "chunkSeq")) ?? 0,
    sampleRateHz: optionalNumber(first(item, "sample_rate_hz", "sampleRateHz")) ?? 0,
    sampleCount: optionalNumber(first(item, "sample_count", "sampleCount")) ?? 0,
    durationMs: optionalNumber(first(item, "duration_ms", "durationMs")) ?? 0,
    fileSizeBytes: optionalNumber(first(item, "file_size_bytes", "fileSizeBytes")) ?? 0,
    status: status === "committed" || status === "missing" || status === "corrupted" ? status : "unknown",
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")),
  };
}

function parseSuggestionStatus(value: unknown): SuggestionStatus {
  return value === "draft" || value === "validating" || value === "committed" || value === "rejected" || value === "superseded"
    ? value
    : "draft";
}

function parseFeedback(value: unknown): SuggestionFeedback | null {
  return value === "kept" || value === "ignored" || value === "false_positive" || value === "too_late"
    ? value
    : null;
}

function parseSuggestion(value: unknown, index: number): Suggestion {
  const item = record(value, `suggestions[${index}]`);
  return {
    suggestionId: requiredString(first(item, "suggestion_id", "suggestionId"), `suggestions[${index}].suggestion_id`),
    meetingId: requiredString(first(item, "meeting_id", "meetingId"), `suggestions[${index}].meeting_id`),
    jobId: optionalString(first(item, "job_id", "jobId")),
    generationId: requiredString(first(item, "generation_id", "generationId"), `suggestions[${index}].generation_id`),
    evidenceSegmentId: requiredString(first(item, "evidence_segment_id", "evidenceSegmentId"), `suggestions[${index}].evidence_segment_id`),
    evidenceTranscriptSeq: requiredNumber(first(item, "evidence_transcript_seq", "evidenceTranscriptSeq"), `suggestions[${index}].evidence_transcript_seq`),
    evidenceHash: optionalString(first(item, "evidence_hash", "evidenceHash")) ?? "",
    stateRevision: optionalNumber(first(item, "state_revision", "stateRevision")) ?? 1,
    status: parseSuggestionStatus(item.status),
    draftText: optionalString(first(item, "draft_text", "draftText")) ?? "",
    draftSeq: optionalNumber(first(item, "draft_seq", "draftSeq")) ?? 0,
    text: optionalString(item.text),
    finalDraftSeq: optionalNumber(first(item, "final_draft_seq", "finalDraftSeq")),
    feedback: parseFeedback(item.feedback),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
    committedAtMs: optionalNumber(first(item, "committed_at_ms", "committedAtMs")),
  };
}

function parseTopic(value: unknown): TopicProjection | null {
  if (typeof value === "string" && value.trim()) {
    return { id: "current-topic", text: value.trim(), status: "active", evidenceSegmentIds: [], updatedAtMs: null };
  }
  const item = optionalRecord(value);
  if (!item) return null;
  const text = optionalString(first(item, "text", "title", "topic"));
  if (!text) return null;
  const status = item.status;
  return {
    id: optionalString(first(item, "id", "topic_id", "topicId")) ?? "current-topic",
    text,
    status: status === "active" || status === "changed" || status === "expired" ? status : "unknown",
    evidenceSegmentIds: strings(first(item, "evidence_segment_ids", "evidenceSegmentIds")),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")),
  };
}

function parseQuestion(value: unknown, index: number): OpenQuestionProjection | null {
  if (typeof value === "string" && value.trim()) {
    return { id: `question-${index}`, text: value.trim(), status: "open", evidenceSegmentIds: [], updatedAtMs: null };
  }
  const item = optionalRecord(value);
  if (!item) return null;
  const text = optionalString(first(item, "text", "question"));
  if (!text) return null;
  const status = item.status;
  return {
    id: optionalString(first(item, "id", "question_id", "questionId")) ?? `question-${index}`,
    text,
    status:
      status === "open" || status === "carried_over" || status === "answered" || status === "expired"
        ? status
        : "unknown",
    evidenceSegmentIds: strings(first(item, "evidence_segment_ids", "evidenceSegmentIds")),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")),
  };
}

function parsePartial(value: unknown): ActivePartial | null {
  const item = optionalRecord(value);
  if (!item) return null;
  const text = optionalString(first(item, "text", "partial_text", "partialText"));
  if (!text) return null;
  return {
    segmentId: optionalString(first(item, "segment_id", "segmentId")) ?? "active-partial",
    text,
    startedAtMs: optionalNumber(first(item, "started_at_ms", "startedAtMs")),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? Date.now(),
  };
}

function runtimeState(value: unknown): RuntimeState {
  return value === "active" || value === "busy" || value === "idle" || value === "paused" || value === "offline" || value === "error"
    ? value
    : "unknown";
}

function parseIndicator(value: unknown, fallbackLabel: string): RuntimeIndicator {
  if (typeof value === "string") {
    return { state: runtimeState(value), label: fallbackLabel, level: null, detail: null };
  }
  const item = optionalRecord(value);
  if (!item) return { state: "unknown", label: fallbackLabel, level: null, detail: null };
  return {
    state: runtimeState(first(item, "state", "status")),
    label: optionalString(item.label) ?? fallbackLabel,
    level: optionalNumber(first(item, "level", "input_level", "inputLevel")),
    detail: optionalString(item.detail),
  };
}

function parseRuntime(source: JsonRecord): MeetingRuntime {
  const runtime = optionalRecord(first(source, "runtime", "meeting_status", "meetingStatus", "status")) ?? {};
  const phase = first(runtime, "phase", "meeting_phase", "meetingPhase");
  return {
    phase: phase === "live" || phase === "ending" || phase === "ended" ? phase : "unknown",
    recording: parseIndicator(first(runtime, "recording", "recording_status", "recordingStatus"), "录音状态待同步"),
    input: parseIndicator(first(runtime, "input", "input_status", "inputStatus"), "输入状态待同步"),
    ai: parseIndicator(first(runtime, "ai", "ai_status", "aiStatus"), "AI 状态待同步"),
    elapsedMs: optionalNumber(first(runtime, "elapsed_ms", "elapsedMs")),
  };
}

export function parseMeetingSnapshot(value: unknown): MeetingSnapshot {
  const source = record(value);
  const meetingId = requiredString(first(source, "meeting_id", "meetingId"), "meeting_id");
  const segmentValues = source.segments;
  const suggestionValues = source.suggestions;
  if (!Array.isArray(segmentValues)) throw new ContractError("segments must be an array");
  if (!Array.isArray(suggestionValues)) throw new ContractError("suggestions must be an array");

  const questionsValue = first(source, "open_questions", "openQuestions", "open_question", "openQuestion");
  const questionValues = Array.isArray(questionsValue) ? questionsValue : questionsValue === undefined || questionsValue === null ? [] : [questionsValue];
  const reviewJobs = parseReviewJobs(first(source, "review_jobs", "reviewJobs"), meetingId);
  const knownKeys = new Set([
    "meeting_id", "meetingId", "title", "last_seq", "lastSeq", "segments", "suggestions",
    "current_topic", "currentTopic", "open_questions", "openQuestions", "open_question", "openQuestion",
    "active_partial", "activePartial", "minutes", "approach_cards", "approachCards", "review_jobs", "reviewJobs",
    "audio", "runtime", "meeting_status", "meetingStatus", "status", "diagnostics", "transcript_page", "jobs",
  ]);
  const unknownDiagnostics = Object.fromEntries(Object.entries(source).filter(([key]) => !knownKeys.has(key)));

  return {
    meetingId,
    title: optionalString(source.title),
    lastSeq: requiredNumber(first(source, "last_seq", "lastSeq"), "last_seq"),
    segments: segmentValues.map(parseSegment),
    activePartial: parsePartial(first(source, "active_partial", "activePartial")),
    suggestions: suggestionValues.map(parseSuggestion),
    currentTopic: parseTopic(first(source, "current_topic", "currentTopic")),
    openQuestions: questionValues.map(parseQuestion).filter((item): item is OpenQuestionProjection => item !== null),
    minutes: parseMinutes(source.minutes),
    approach: parseApproach(first(source, "approach_cards", "approachCards"), reviewJobs.approach),
    reviewJobs,
    audio: parseAudioSummary(source.audio),
    runtime: parseRuntime(source),
    diagnostics: {
      ...unknownDiagnostics,
      ...(optionalRecord(source.diagnostics) ?? {}),
    },
  };
}

export function parseMeetingEvent(value: unknown, index = 0): MeetingEvent {
  const item = record(value, `events[${index}]`);
  return {
    meetingId: requiredString(first(item, "meeting_id", "meetingId"), `events[${index}].meeting_id`),
    seq: requiredNumber(item.seq, `events[${index}].seq`),
    eventId: requiredString(first(item, "event_id", "eventId"), `events[${index}].event_id`),
    type: requiredString(item.type, `events[${index}].type`),
    aggregateType: requiredString(first(item, "aggregate_type", "aggregateType"), `events[${index}].aggregate_type`),
    aggregateId: requiredString(first(item, "aggregate_id", "aggregateId"), `events[${index}].aggregate_id`),
    occurredAtMs: requiredNumber(first(item, "occurred_at_ms", "occurredAtMs"), `events[${index}].occurred_at_ms`),
    correlationId: optionalString(first(item, "correlation_id", "correlationId")),
    causationId: optionalString(first(item, "causation_id", "causationId")),
    idempotencyKey: requiredString(first(item, "idempotency_key", "idempotencyKey"), `events[${index}].idempotency_key`),
    payload: optionalRecord(item.payload) ?? {},
    publishedAtMs: optionalNumber(first(item, "published_at_ms", "publishedAtMs")),
  };
}

export function parseEventsPage(value: unknown): EventsPage {
  const source = record(value);
  if (!Array.isArray(source.events)) throw new ContractError("events must be an array");
  const lastSeq = requiredNumber(first(source, "last_seq", "lastSeq"), "last_seq");
  return {
    meetingId: requiredString(first(source, "meeting_id", "meetingId"), "meeting_id"),
    afterSeq: requiredNumber(first(source, "after_seq", "afterSeq"), "after_seq"),
    lastSeq,
    events: source.events.map(parseMeetingEvent),
    hasMore: optionalBoolean(first(source, "has_more", "hasMore")) ?? false,
    nextAfterSeq: optionalNumber(first(source, "next_after_seq", "nextAfterSeq")) ?? lastSeq,
  };
}

export function parseTranscriptPage(value: unknown): TranscriptPage {
  const source = record(value);
  if (!Array.isArray(source.segments)) throw new ContractError("segments must be an array");
  return {
    meetingId: requiredString(first(source, "meeting_id", "meetingId"), "meeting_id"),
    afterTranscriptSeq: requiredNumber(
      first(source, "after_transcript_seq", "afterTranscriptSeq"),
      "after_transcript_seq",
    ),
    segments: source.segments.map(parseSegment),
    hasMore: optionalBoolean(first(source, "has_more", "hasMore")) ?? false,
    nextAfterTranscriptSeq: requiredNumber(
      first(source, "next_after_transcript_seq", "nextAfterTranscriptSeq"),
      "next_after_transcript_seq",
    ),
  };
}

function parseMeetingPhase(value: unknown): MeetingHistoryItem["phase"] {
  return value === "live" || value === "ended" ? value : "unknown";
}

function parseHistoryItem(value: unknown, index: number): MeetingHistoryItem {
  const item = record(value, `meetings[${index}]`);
  return {
    meetingId: requiredString(first(item, "id", "meeting_id", "meetingId"), `meetings[${index}].id`),
    title: optionalString(item.title),
    phase: parseMeetingPhase(first(item, "state", "phase")),
    startedAtMs: optionalNumber(first(item, "started_at_ms", "startedAtMs")),
    endedAtMs: optionalNumber(first(item, "ended_at_ms", "endedAtMs")),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
    segmentCount: optionalNumber(first(item, "segment_count", "segmentCount")) ?? 0,
    suggestionCount: optionalNumber(first(item, "suggestion_count", "suggestionCount")) ?? 0,
    audioDurationMs: optionalNumber(first(item, "audio_duration_ms", "audioDurationMs")) ?? 0,
    hasMinutes: optionalBoolean(first(item, "has_minutes", "hasMinutes")) ?? false,
  };
}

export function parseMeetingHistory(value: unknown): MeetingHistory {
  const source = record(value);
  if (!Array.isArray(source.meetings)) throw new ContractError("meetings must be an array");
  return { meetings: source.meetings.map(parseHistoryItem) };
}

export function parseMeetingAudio(value: unknown): MeetingAudio {
  const source = record(value);
  if (!Array.isArray(source.chunks)) throw new ContractError("chunks must be an array");
  return {
    ...parseAudioSummary(source),
    meetingId: requiredString(first(source, "meeting_id", "meetingId"), "meeting_id"),
    assembled: optionalBoolean(source.assembled) ?? false,
    playbackUrl: optionalString(first(source, "playback_url", "playbackUrl")),
    format: optionalString(source.format),
    chunks: source.chunks.map(parseAudioChunk),
  };
}

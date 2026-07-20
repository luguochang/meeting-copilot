import type {
  ActivePartial,
  ActionItemProjection,
  ApproachCard,
  ApproachReview,
  AudioChunk,
  DataGovernanceSettings,
  DataRetentionPolicy,
  DecisionCandidate,
  EvidenceSpan,
  EventsPage,
  FollowUpProjection,
  FormalAiProvenance,
  MeetingAudio,
  MeetingAudioSummary,
  MeetingFactStatus,
  MeetingEvent,
  MeetingHistory,
  MeetingHistoryPage,
  MeetingHistoryItem,
  ImportJob,
  ImportJobStage,
  MeetingRuntime,
  MeetingSpeaker,
  MeetingSnapshot,
  MinutesArtifact,
  OpenQuestionProjection,
  RiskProjection,
  ReviewJob,
  ReviewJobKind,
  ReviewJobs,
  ReviewDocument,
  ReviewDocumentKind,
  ReviewDocumentRevision,
  ReviewDocuments,
  ReviewDocumentSource,
  MeetingTitleSource,
  RuntimeIndicator,
  RuntimeState,
  Suggestion,
  SuggestionFeedback,
  SuggestionStatus,
  SemanticParagraph,
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

export type AudioTrackId = "microphone" | "system_audio";

export type ProviderProbeStatus = "not_run" | "probing" | "succeeded" | "failed";

export interface ProviderStatus {
  configured: boolean;
  runtime_synced: boolean;
  probe_status: ProviderProbeStatus;
  model: string | null;
  realtime_model?: string | null;
}

export interface DesktopProviderStatusLike {
  configured?: boolean;
  runtime_synced?: boolean;
  probe_status?: ProviderProbeStatus;
  model?: string | null;
  realtime_model?: string | null;
}

export type AudioTrackStatus =
  | "active"
  | "ready"
  | "failed"
  | "sealed"
  | "exporting"
  | "interrupted"
  | "missing"
  | "unknown"
  | (string & {});

export type MeetingAudioOverallStatus =
  | "recording"
  | "saved"
  | "assembling"
  | "failed"
  | "partial_failure"
  | "unknown";

export interface MeetingAudioTrackState {
  trackId: AudioTrackId;
  source: AudioTrackId;
  epoch: number;
  status: AudioTrackStatus;
  durationMs: number;
  chunkCount: number;
  fileSizeBytes: number;
  playbackUrl: string | null;
  errorClass: string | null;
  firstSequence: number | null;
  lastSequence: number | null;
  firstTimestampMs: number | null;
  lastTimestampMs: number | null;
}

export interface MeetingAudioDerivedSource {
  trackId: AudioTrackId;
  epoch: number;
  outputSha256: string | null;
}

export interface MeetingAudioDerivedAsset {
  assetId: string;
  meetingId: string;
  kind: string;
  derivation: string;
  status: AudioTrackStatus;
  durationMs: number;
  fileSizeBytes: number;
  playbackUrl: string | null;
  sources: MeetingAudioDerivedSource[];
  remoteUploadUsed: boolean;
  retentionPolicy: string | null;
}

export interface MeetingAudioWithTracks extends MeetingAudio {
  overallStatus: MeetingAudioOverallStatus;
  trackStates: MeetingAudioTrackState[];
  derivedAssets: MeetingAudioDerivedAsset[];
  mixedCreateUrl: string | null;
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

function nullableString(value: unknown, field: string): string | null {
  if (value === null) return null;
  if (typeof value !== "string" || !value.trim()) {
    throw new ContractError(`${field} must be null or a non-empty string`);
  }
  return value.trim();
}

function nullableConfidence(value: unknown, field: string): number | null {
  if (value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new ContractError(`${field} must be null or a number between 0 and 1`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, field: string, fallback = 0): number {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ContractError(`${field} must be a non-negative integer`);
  }
  return value;
}

function optionalBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function parseProviderStatus(value: unknown): ProviderStatus {
  const source = record(value, "provider status");
  const probeStatus = source.probe_status;
  if (typeof source.configured !== "boolean") {
    throw new ContractError("provider status.configured must be a boolean");
  }
  if (typeof source.runtime_synced !== "boolean") {
    throw new ContractError("provider status.runtime_synced must be a boolean");
  }
  if (!["not_run", "probing", "succeeded", "failed"].includes(String(probeStatus))) {
    throw new ContractError("provider status.probe_status is invalid");
  }
  return {
    configured: source.configured,
    runtime_synced: source.runtime_synced,
    probe_status: probeStatus as ProviderProbeStatus,
    model: optionalString(source.model),
    realtime_model: optionalString(source.realtime_model) ?? optionalString(source.model),
  };
}

export function reconcileProviderStatus(
  desktop: DesktopProviderStatusLike | null,
  runtime: ProviderStatus | null,
): ProviderStatus {
  if (!desktop) {
    return runtime ?? {
      configured: false,
      runtime_synced: false,
      probe_status: "not_run",
      model: null,
      realtime_model: null,
    };
  }
  const configured = desktop.configured === true;
  const model = typeof desktop.model === "string" && desktop.model.trim()
    ? desktop.model.trim()
    : runtime?.model ?? null;
  const realtimeModel = typeof desktop.realtime_model === "string" && desktop.realtime_model.trim()
    ? desktop.realtime_model.trim()
    : model ?? null;
  const sameModel = (!runtime?.model || !model || runtime.model === model)
    && (!runtime?.realtime_model || !realtimeModel || runtime.realtime_model === realtimeModel);
  const runtimeSynced = configured
    && desktop.runtime_synced === true
    && (runtime === null || runtime.runtime_synced === true)
    && sameModel;
  return {
    configured,
    runtime_synced: runtimeSynced,
    probe_status: runtimeSynced
      ? runtime?.probe_status ?? desktop.probe_status ?? "not_run"
      : "not_run",
    model,
    realtime_model: realtimeModel,
  };
}

function boundedProgress(value: unknown): number | null {
  const parsed = optionalNumber(value);
  if (parsed === null) return null;
  return Math.min(100, Math.max(0, parsed <= 1 ? parsed * 100 : parsed));
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
    speakerId: optionalString(first(item, "speaker_id", "speakerId")),
    speakerLabel: optionalString(first(item, "speaker_label", "speakerLabel")),
    speakerConfidence: optionalNumber(first(item, "speaker_confidence", "speakerConfidence")),
    speakerAttributionRevision: nonNegativeInteger(
      first(item, "speaker_attribution_revision", "speakerAttributionRevision"),
      `segments[${index}].speaker_attribution_revision`,
    ),
    speakerAttributionSource: optionalString(
      first(item, "speaker_attribution_source", "speakerAttributionSource"),
    ),
    speakerAttributionReason: optionalString(
      first(item, "speaker_attribution_reason", "speakerAttributionReason"),
    ),
    correctionStatus: optionalString(first(item, "correction_status", "correctionStatus")) ?? "pending",
    correctionBeforeText: optionalString(first(item, "correction_before_text", "correctionBeforeText")),
    correctionAfterText: optionalString(first(item, "correction_after_text", "correctionAfterText")),
    correctionErrorClass: optionalString(first(item, "correction_error_class", "correctionErrorClass")),
    correctionUpdatedAtMs: optionalNumber(first(item, "correction_updated_at_ms", "correctionUpdatedAtMs")),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

function parseSemanticParagraph(value: unknown, index: number): SemanticParagraph {
  const item = record(value, `semantic_paragraphs[${index}]`);
  const status = item.status === "stable" ? "stable" : item.status === "active" ? "active" : null;
  if (!status) throw new ContractError(`semantic_paragraphs[${index}].status is unsupported`);
  return {
    meetingId: requiredString(first(item, "meeting_id", "meetingId"), `semantic_paragraphs[${index}].meeting_id`),
    paragraphId: requiredString(first(item, "paragraph_id", "paragraphId"), `semantic_paragraphs[${index}].paragraph_id`),
    revision: optionalNumber(item.revision) ?? 1,
    text: requiredString(item.text, `semantic_paragraphs[${index}].text`),
    startMs: optionalNumber(first(item, "start_ms", "startMs")),
    endMs: optionalNumber(first(item, "end_ms", "endMs")),
    status,
    checkpointIds: strings(first(item, "checkpoint_ids", "checkpointIds")),
    speakerId: optionalString(first(item, "speaker_id", "speakerId")),
    speakerLabel: optionalString(first(item, "speaker_label", "speakerLabel")),
    speakerConfidence: optionalNumber(first(item, "speaker_confidence", "speakerConfidence")),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

export function parseMeetingSpeaker(
  value: unknown,
  fallbackMeetingId = "",
  field = "speaker",
): MeetingSpeaker {
  const item = record(value, field);
  return {
    meetingId: optionalString(first(item, "meeting_id", "meetingId")) ?? fallbackMeetingId,
    speakerId: requiredString(first(item, "speaker_id", "speakerId"), `${field}.speaker_id`),
    speakerLabel: requiredString(first(item, "speaker_label", "speakerLabel"), `${field}.speaker_label`),
    ordinal: requiredNumber(item.ordinal, `${field}.ordinal`),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

export function parseMeetingSpeakers(value: unknown): MeetingSpeaker[] {
  const source = record(value);
  const meetingId = requiredString(first(source, "meeting_id", "meetingId"), "meeting_id");
  if (!Array.isArray(source.speakers)) throw new ContractError("speakers must be an array");
  return source.speakers.map((speaker, index) =>
    parseMeetingSpeaker(speaker, meetingId, `speakers[${index}]`));
}

function parseOptionalSemanticParagraph(value: unknown): SemanticParagraph | null {
  return value === null || value === undefined ? null : parseSemanticParagraph(value, 0);
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
    errorMessage: optionalString(first(item, "error_message", "errorMessage", "message")),
    retryable: optionalBoolean(item.retryable) ?? ["failed", "cancelled"].includes(String(item.status ?? "")),
    output: optionalRecord(item.output),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")),
    completedAtMs: optionalNumber(first(item, "completed_at_ms", "completedAtMs")),
  };
}

function parseDocumentKind(value: unknown): ReviewDocumentKind | null {
  if (value === "review" || value === "review_document") return "minutes";
  if (value === "actions") return "action_items";
  return value === "minutes" || value === "decisions" || value === "action_items" ||
    value === "risks" || value === "transcript"
    ? value
    : null;
}

function parseDocumentSource(value: unknown, userVersion = 0): ReviewDocumentSource {
  if (value === "ai_generated" || value === "user_final") return value;
  return userVersion > 0 ? "user_final" : "unknown";
}

export function parseReviewDocument(
  value: unknown,
  fallbackKind?: ReviewDocumentKind,
  fallbackMeetingId = "",
): ReviewDocument {
  const item = record(value, "document");
  const kind = parseDocumentKind(first(item, "document_kind", "documentKind", "kind")) ?? fallbackKind;
  if (!kind) throw new ContractError("document_kind is unsupported");
  const aiGenerated = optionalRecord(first(item, "ai_generated", "aiGenerated"));
  const userFinal = optionalRecord(first(item, "user_final", "userFinal"));
  const aiVersion = optionalNumber(first(item, "ai_version", "aiVersion"))
    ?? optionalNumber(aiGenerated?.version)
    ?? 0;
  const userVersion = optionalNumber(first(item, "user_version", "userVersion"))
    ?? optionalNumber(userFinal?.version)
    ?? 0;
  const userModified = optionalBoolean(first(userFinal ?? {}, "modified")) ?? false;
  const userContent = userFinal ? first(userFinal, "content", "content_json", "contentJson") : undefined;
  const aiContent = aiGenerated ? first(aiGenerated, "content", "content_json", "contentJson") : undefined;
  return {
    documentId: optionalString(first(item, "id", "document_id", "documentId")),
    meetingId: optionalString(first(item, "meeting_id", "meetingId")) ?? fallbackMeetingId,
    kind,
    revision: optionalNumber(item.revision) ?? 0,
    sourceRevision: optionalNumber(first(item, "source_transcript_revision", "sourceTranscriptRevision", "source_revision", "sourceRevision")),
    contentJson: userContent ?? aiContent ?? first(item, "content_json", "contentJson", "content") ?? {},
    aiVersion,
    userVersion,
    source: userModified || (userVersion > 0 && aiVersion === 0)
      ? "user_final"
      : aiVersion > 0
        ? "ai_generated"
        : parseDocumentSource(first(item, "version_source", "versionSource", "source", "author"), userVersion),
    dirtyState: optionalString(first(item, "dirty_state", "dirtyState")),
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
  };
}

function parseReviewDocuments(value: unknown, meetingId: string): ReviewDocuments {
  const documents: ReviewDocuments = {};
  if (Array.isArray(value)) {
    for (const entry of value) {
      try {
        const document = parseReviewDocument(entry, undefined, meetingId);
        documents[document.kind] = document;
      } catch (error) {
        if (!(error instanceof ContractError)) throw error;
      }
    }
    return documents;
  }
  const source = optionalRecord(value);
  if (!source) return documents;
  for (const [rawKind, entry] of Object.entries(source)) {
    const kind = parseDocumentKind(rawKind);
    if (!kind || !optionalRecord(entry)) continue;
    documents[kind] = parseReviewDocument(entry, kind, meetingId);
  }
  return documents;
}

function parseImportStage(value: unknown): ImportJobStage {
  const aliases: Record<string, ImportJobStage> = {
    upload: "reading",
    uploading: "reading",
    converting: "normalizing",
    conversion: "normalizing",
    asr: "transcribing",
    transcription: "transcribing",
    correction: "correcting",
    minutes: "reviewing",
    approach: "reviewing",
    done: "completed",
    succeeded: "completed",
  };
  const raw = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (raw === "reading" || raw === "normalizing" || raw === "transcribing" ||
      raw === "correcting" || raw === "reviewing" || raw === "completed") return raw;
  return aliases[raw] ?? "unknown";
}

export function parseImportJob(value: unknown): ImportJob | null {
  const item = optionalRecord(value);
  if (!item) return null;
  return {
    id: optionalString(first(item, "id", "job_id", "jobId")),
    meetingId: optionalString(first(item, "meeting_id", "meetingId")),
    status: parseReviewJobStatus(item.status),
    stage: parseImportStage(first(item, "stage", "current_stage", "currentStage", "kind")),
    progress: boundedProgress(first(item, "progress", "progress_percent", "progressPercent")),
    errorClass: optionalString(first(item, "error_class", "errorClass")),
    errorMessage: optionalString(first(item, "error_message", "errorMessage", "message")),
    retryable: optionalBoolean(item.retryable) ?? item.status === "failed",
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")),
  };
}

export function parseReviewDocumentRevisions(value: unknown): ReviewDocumentRevision[] {
  const source = Array.isArray(value) ? value : optionalRecord(value)?.revisions;
  if (!Array.isArray(source)) throw new ContractError("revisions must be an array");
  return source.flatMap((entry) => {
    const item = optionalRecord(entry);
    if (!item) return [];
    const revision = optionalNumber(item.revision);
    if (revision === null) return [];
    const author = optionalString(item.author) ?? "unknown";
    return [{
      revision,
      author,
      source: parseDocumentSource(first(item, "version_kind", "versionKind", "version_source", "versionSource", "source", "author")),
      contentJson: first(item, "content_json", "contentJson", "content") ?? null,
      patch: item.patch ?? null,
      createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    }];
  });
}

function parseTitleSource(value: unknown): MeetingTitleSource {
  return value === "user" || value === "ai" || value === "import" || value === "fallback"
    ? value
    : "unknown";
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

function parseEvidenceSpans(value: unknown): EvidenceSpan[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((span) => {
    const item = optionalRecord(span);
    if (!item) return [];
    const segmentId = optionalString(first(item, "segment_id", "segmentId"));
    const transcriptSeq = optionalNumber(first(item, "transcript_seq", "transcriptSeq"));
    if (!segmentId || transcriptSeq === null) return [];
    return [{
      segmentId,
      transcriptSeq,
      startMs: optionalNumber(first(item, "start_ms", "startMs")),
      endMs: optionalNumber(first(item, "end_ms", "endMs")),
      quote: optionalString(item.quote) ?? "",
    }];
  });
}

function parseMeetingFactStatus(value: unknown): MeetingFactStatus {
  return typeof value === "string" && value.trim() ? value.trim() as MeetingFactStatus : "unknown";
}

function parseFormalAi(value: unknown): FormalAiProvenance | null {
  const item = optionalRecord(value);
  if (!item || item.source !== "llm_first" || item.llm_called !== true) return null;
  const jobId = optionalString(first(item, "job_id", "jobId"));
  const batchId = optionalString(first(item, "batch_id", "batchId"));
  const provider = optionalString(item.provider);
  const model = optionalString(item.model);
  const evidenceValue = optionalRecord(first(item, "formal_evidence", "evidence"));
  const segmentIds = evidenceValue ? strings(first(evidenceValue, "segment_ids", "segmentIds")) : [];
  if (!jobId || !batchId || !provider || !model || !segmentIds.length) return null;
  return {
    source: "llm_first",
    jobId,
    batchId,
    provider,
    model,
    llmCalled: true,
    evidence: {
      segmentIds,
      quote: optionalString(evidenceValue?.quote) ?? "",
      evidenceHash: optionalString(first(evidenceValue ?? {}, "evidence_hash", "evidenceHash")),
      stateRevision: optionalNumber(first(evidenceValue ?? {}, "state_revision", "stateRevision")),
    },
  };
}

function parseFactBase(value: unknown): {
  id: string;
  text: string;
  status: MeetingFactStatus;
  confidence: number | null;
  evidenceSegmentIds: string[];
  evidenceSpans: EvidenceSpan[];
  updatedAtMs: number;
  formalAi: FormalAiProvenance | null;
} | null {
  const item = optionalRecord(value);
  if (!item) return null;
  const id = optionalString(first(item, "id", "fact_id", "factId"));
  const text = optionalString(first(item, "text", "statement", "description", "title"));
  if (!id || !text) return null;
  const evidenceSpans = parseEvidenceSpans(first(item, "evidence_spans", "evidenceSpans"));
  return {
    id,
    text,
    status: parseMeetingFactStatus(item.status),
    confidence: optionalNumber(item.confidence),
    evidenceSegmentIds: evidenceSegmentIds(item),
    evidenceSpans,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
    formalAi: parseFormalAi(item),
  };
}

function parseDecisionCandidate(value: unknown): DecisionCandidate | null {
  return parseFactBase(value);
}

function parseActionItem(value: unknown): ActionItemProjection | null {
  const item = optionalRecord(value);
  const base = parseFactBase(value);
  if (!item || !base) return null;
  return {
    ...base,
    owner: optionalString(first(item, "owner", "owner_name", "ownerName")),
    deadline: optionalString(first(item, "deadline", "due", "due_at", "dueAt")),
  };
}

function parseRisk(value: unknown): RiskProjection | null {
  const item = optionalRecord(value);
  const base = parseFactBase(value);
  if (!item || !base) return null;
  return { ...base, mitigation: optionalString(first(item, "mitigation", "mitigation_text", "mitigationText")) };
}

function factValues(source: JsonRecord, snakeKey: string, camelKey: string): unknown[] {
  const value = first(source, snakeKey, camelKey);
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value)) throw new ContractError(`${snakeKey} must be an array`);
  return value;
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

function parseMeetingAudioOverallStatus(value: unknown): MeetingAudioOverallStatus {
  return value === "recording" || value === "saved" || value === "assembling"
    || value === "failed" || value === "partial_failure"
    ? value
    : "unknown";
}

function parseAudioTrackId(value: unknown, field: string): AudioTrackId {
  if (value === "microphone" || value === "system_audio") return value;
  throw new ContractError(`${field} must be microphone or system_audio`);
}

function parseAudioTrackStatus(value: unknown): AudioTrackStatus {
  return typeof value === "string" && value.trim() ? value.trim() : "unknown";
}

function parseMeetingAudioTrack(value: unknown, index: number): MeetingAudioTrackState {
  const item = record(value, `track_states[${index}]`);
  return {
    trackId: parseAudioTrackId(first(item, "track_id", "trackId", "track"), `track_states[${index}].track_id`),
    source: parseAudioTrackId(first(item, "source", "track_id", "trackId", "track"), `track_states[${index}].source`),
    epoch: optionalNumber(item.epoch) ?? 0,
    status: parseAudioTrackStatus(item.status),
    durationMs: optionalNumber(first(item, "duration_ms", "durationMs")) ?? 0,
    chunkCount: optionalNumber(first(item, "chunk_count", "chunkCount")) ?? 0,
    fileSizeBytes: optionalNumber(first(item, "file_size_bytes", "fileSizeBytes")) ?? 0,
    playbackUrl: optionalString(first(item, "playback_url", "playbackUrl")),
    errorClass: optionalString(first(item, "error_class", "errorClass")),
    firstSequence: optionalNumber(first(item, "first_sequence", "firstSequence")),
    lastSequence: optionalNumber(first(item, "last_sequence", "lastSequence")),
    firstTimestampMs: optionalNumber(first(item, "first_timestamp_ms", "firstTimestampMs")),
    lastTimestampMs: optionalNumber(first(item, "last_timestamp_ms", "lastTimestampMs")),
  };
}

function parseMeetingAudioDerivedSource(value: unknown, index: number): MeetingAudioDerivedSource {
  const item = record(value, `derived_assets.sources[${index}]`);
  return {
    trackId: parseAudioTrackId(first(item, "track_id", "trackId", "track"), `derived_assets.sources[${index}].track_id`),
    epoch: optionalNumber(item.epoch) ?? 0,
    outputSha256: optionalString(first(item, "output_sha256", "outputSha256")),
  };
}

export function parseMeetingAudioDerivedAsset(value: unknown): MeetingAudioDerivedAsset {
  const item = record(value, "asset");
  const rawSources = first(item, "sources");
  return {
    assetId: requiredString(first(item, "asset_id", "assetId"), "asset.asset_id"),
    meetingId: requiredString(first(item, "meeting_id", "meetingId"), "asset.meeting_id"),
    kind: optionalString(item.kind) ?? "mixed",
    derivation: optionalString(item.derivation) ?? "unknown",
    status: parseAudioTrackStatus(item.status),
    durationMs: optionalNumber(first(item, "duration_ms", "durationMs")) ?? 0,
    fileSizeBytes: optionalNumber(first(item, "file_size_bytes", "fileSizeBytes")) ?? 0,
    playbackUrl: optionalString(first(item, "playback_url", "playbackUrl")),
    sources: Array.isArray(rawSources) ? rawSources.map(parseMeetingAudioDerivedSource) : [],
    remoteUploadUsed: optionalBoolean(first(item, "remote_upload_used", "remoteUploadUsed")) ?? false,
    retentionPolicy: optionalString(first(item, "retention_policy", "retentionPolicy")),
  };
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
    formalAi: parseFormalAi(item),
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
    formalAi: parseFormalAi(item),
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
    formalAi: parseFormalAi(item),
  };
}

function parseFollowUp(value: unknown): FollowUpProjection | null {
  const item = optionalRecord(value);
  if (!item) return null;
  const question = optionalString(first(item, "question"));
  const reason = optionalString(first(item, "reason"));
  if (!question || !reason) return null;
  const urgencyValue = optionalString(first(item, "urgency"));
  const urgency = urgencyValue === "low" || urgencyValue === "medium" || urgencyValue === "high"
    ? urgencyValue
    : "medium";
  return {
    question,
    reason,
    evidenceSegmentIds: strings(first(item, "evidence_segment_ids", "evidenceSegmentIds")),
    evidenceQuote: optionalString(first(item, "evidence_quote", "evidenceQuote")) ?? "",
    urgency,
    formalAi: parseFormalAi(item),
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
    recording: parseIndicator(first(runtime, "recording", "recording_status", "recordingStatus"), "录音状态待读取"),
    input: parseIndicator(first(runtime, "input", "input_status", "inputStatus"), "输入状态待读取"),
    ai: parseIndicator(first(runtime, "ai", "ai_status", "aiStatus"), "AI 状态待读取"),
    elapsedMs: optionalNumber(first(runtime, "elapsed_ms", "elapsedMs")),
  };
}

export function parseMeetingSnapshot(value: unknown): MeetingSnapshot {
  const source = record(value);
  const meetingId = requiredString(first(source, "meeting_id", "meetingId"), "meeting_id");
  const segmentValues = source.segments;
  const semanticParagraphValues = first(source, "semantic_paragraphs", "semanticParagraphs");
  const suggestionValues = source.suggestions;
  if (!Array.isArray(segmentValues)) throw new ContractError("segments must be an array");
  if (!Array.isArray(suggestionValues)) throw new ContractError("suggestions must be an array");

  const questionsValue = first(source, "open_questions", "openQuestions", "open_question", "openQuestion");
  const questionValues = Array.isArray(questionsValue) ? questionsValue : questionsValue === undefined || questionsValue === null ? [] : [questionsValue];
  const reviewJobs = parseReviewJobs(first(source, "review_jobs", "reviewJobs"), meetingId);
  const decisionValues = factValues(source, "decision_candidates", "decisionCandidates");
  const actionValues = factValues(source, "action_items", "actionItems");
  const riskValues = factValues(source, "risks", "riskItems");
  const knownKeys = new Set([
    "meeting_id", "meetingId", "title", "last_seq", "lastSeq", "segments", "suggestions",
    "semantic_paragraphs", "semanticParagraphs", "active_paragraph", "activeParagraph",
    "decision_candidates", "decisionCandidates", "action_items", "actionItems", "risks", "riskItems",
    "current_topic", "currentTopic", "open_questions", "openQuestions", "open_question", "openQuestion",
    "active_partial", "activePartial", "minutes", "approach_cards", "approachCards", "review_jobs", "reviewJobs",
    "audio", "runtime", "meeting_status", "meetingStatus", "status", "diagnostics", "transcript_page", "jobs",
    "title_source", "titleSource", "updated_at_ms", "updatedAtMs", "documents", "review_documents", "reviewDocuments",
    "import_job", "importJob",
  ]);
  const unknownDiagnostics = Object.fromEntries(Object.entries(source).filter(([key]) => !knownKeys.has(key)));

  return {
    meetingId,
    title: optionalString(source.title),
    titleSource: parseTitleSource(first(source, "title_source", "titleSource")),
    updatedAtMs: optionalNumber(first(source, "updated_at_ms", "updatedAtMs")) ?? 0,
    lastSeq: requiredNumber(first(source, "last_seq", "lastSeq"), "last_seq"),
    segments: segmentValues.map(parseSegment),
    semanticParagraphs: Array.isArray(semanticParagraphValues)
      ? semanticParagraphValues.map(parseSemanticParagraph)
      : [],
    activeParagraph: parseOptionalSemanticParagraph(
      first(source, "active_paragraph", "activeParagraph"),
    ),
    activePartial: parsePartial(first(source, "active_partial", "activePartial")),
    suggestions: suggestionValues.map(parseSuggestion),
    decisionCandidates: decisionValues
      .map(parseDecisionCandidate)
      .filter((item): item is DecisionCandidate => item !== null),
    actionItems: actionValues
      .map(parseActionItem)
      .filter((item): item is ActionItemProjection => item !== null),
    risks: riskValues
      .map(parseRisk)
      .filter((item): item is RiskProjection => item !== null),
    currentTopic: parseTopic(first(source, "current_topic", "currentTopic")),
    openQuestions: questionValues.map(parseQuestion).filter((item): item is OpenQuestionProjection => item !== null),
    followUp: parseFollowUp(first(source, "follow_up", "followUp")),
    minutes: parseMinutes(source.minutes),
    approach: parseApproach(first(source, "approach_cards", "approachCards"), reviewJobs.approach),
    reviewJobs,
    documents: parseReviewDocuments(first(source, "documents", "review_documents", "reviewDocuments"), meetingId),
    importJob: parseImportJob(first(source, "import_job", "importJob")),
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
  const meetingId = requiredString(
    first(item, "meeting_id", "meetingId"),
    `events[${index}].meeting_id`,
  );
  const type = requiredString(item.type, `events[${index}].type`);
  const aggregateType = requiredString(
    first(item, "aggregate_type", "aggregateType"),
    `events[${index}].aggregate_type`,
  );
  const aggregateId = requiredString(
    first(item, "aggregate_id", "aggregateId"),
    `events[${index}].aggregate_id`,
  );
  const payload = type === "transcript.segment.speaker_revised"
    ? parseSpeakerRevisionPayload(
      item.payload,
      `events[${index}].payload`,
      meetingId,
      aggregateType,
      aggregateId,
    )
    : optionalRecord(item.payload) ?? {};
  return {
    meetingId,
    seq: requiredNumber(item.seq, `events[${index}].seq`),
    eventId: requiredString(first(item, "event_id", "eventId"), `events[${index}].event_id`),
    type,
    aggregateType,
    aggregateId,
    occurredAtMs: requiredNumber(first(item, "occurred_at_ms", "occurredAtMs"), `events[${index}].occurred_at_ms`),
    correlationId: optionalString(first(item, "correlation_id", "correlationId")),
    causationId: optionalString(first(item, "causation_id", "causationId")),
    idempotencyKey: requiredString(first(item, "idempotency_key", "idempotencyKey"), `events[${index}].idempotency_key`),
    payload,
    publishedAtMs: optionalNumber(first(item, "published_at_ms", "publishedAtMs")),
  };
}

function parseSpeakerRevisionPayload(
  value: unknown,
  field: string,
  meetingId: string,
  aggregateType: string,
  aggregateId: string,
): JsonRecord {
  const payload = record(value, field);
  if (aggregateType !== "transcript_segment") {
    throw new ContractError(`${field} requires aggregate_type transcript_segment`);
  }
  const payloadMeetingId = requiredString(payload.meeting_id, `${field}.meeting_id`);
  const segmentId = requiredString(payload.segment_id, `${field}.segment_id`);
  if (payloadMeetingId !== meetingId) throw new ContractError(`${field}.meeting_id must match the event`);
  if (segmentId !== aggregateId) throw new ContractError(`${field}.segment_id must match aggregate_id`);

  const attributionRevision = nonNegativeInteger(
    payload.attribution_revision,
    `${field}.attribution_revision`,
    -1,
  );
  if (attributionRevision < 1) {
    throw new ContractError(`${field}.attribution_revision must be a positive integer`);
  }
  const runId = requiredString(payload.run_id, `${field}.run_id`);
  const speakerId = nullableString(payload.speaker_id, `${field}.speaker_id`);
  const speakerLabel = nullableString(payload.speaker_label, `${field}.speaker_label`);
  const speakerConfidence = nullableConfidence(
    payload.speaker_confidence,
    `${field}.speaker_confidence`,
  );
  if (speakerId === null && (speakerLabel !== null || speakerConfidence !== null)) {
    throw new ContractError(`${field} cannot identify or score an unknown speaker`);
  }
  if (speakerId !== null && speakerLabel === null) {
    throw new ContractError(`${field}.speaker_label is required when speaker_id is set`);
  }
  if (speakerId !== null && !/^[A-Za-z0-9._:-]{1,128}$/.test(speakerId)) {
    throw new ContractError(`${field}.speaker_id has an unsupported format`);
  }

  return {
    ...payload,
    meeting_id: payloadMeetingId,
    segment_id: segmentId,
    attribution_revision: attributionRevision,
    run_id: runId,
    speaker_id: speakerId,
    speaker_label: speakerLabel,
    speaker_confidence: speakerConfidence,
    source: requiredString(payload.source, `${field}.source`),
    reason: requiredString(payload.reason, `${field}.reason`),
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
    titleSource: parseTitleSource(first(item, "title_source", "titleSource")),
    phase: parseMeetingPhase(first(item, "state", "phase")),
    startedAtMs: optionalNumber(first(item, "started_at_ms", "startedAtMs")),
    endedAtMs: optionalNumber(first(item, "ended_at_ms", "endedAtMs")),
    createdAtMs: optionalNumber(first(item, "created_at_ms", "createdAtMs")) ?? 0,
    updatedAtMs: optionalNumber(first(item, "updated_at_ms", "updatedAtMs")) ?? 0,
    segmentCount: optionalNumber(first(item, "segment_count", "segmentCount")) ?? 0,
    suggestionCount: optionalNumber(first(item, "suggestion_count", "suggestionCount")) ?? 0,
    audioDurationMs: optionalNumber(first(item, "audio_duration_ms", "audioDurationMs")) ?? 0,
    hasMinutes: optionalBoolean(first(item, "has_minutes", "hasMinutes")) ?? false,
    reviewJobs: parseReviewJobs(first(item, "review_jobs", "reviewJobs"), requiredString(first(item, "id", "meeting_id", "meetingId"), `meetings[${index}].id`)),
    importJob: parseImportJob(first(item, "import_job", "importJob")),
    audioStatus: parseAudioStatus(first(item, "audio_status", "audioStatus")),
  };
}

export function parseMeetingHistory(value: unknown): MeetingHistory {
  const source = record(value);
  if (!Array.isArray(source.meetings)) throw new ContractError("meetings must be an array");
  return { meetings: source.meetings.map(parseHistoryItem) };
}

export function parseMeetingHistoryPage(value: unknown): MeetingHistoryPage {
  const source = record(value);
  const history = parseMeetingHistory(source);
  const rawCursor = first(source, "next_cursor", "nextCursor");
  let nextCursor: MeetingHistoryPage["nextCursor"] = null;
  if (rawCursor !== null && rawCursor !== undefined) {
    const cursor = record(rawCursor, "next_cursor");
    nextCursor = {
      beforeUpdatedAtMs: requiredNumber(
        first(cursor, "before_updated_at_ms", "beforeUpdatedAtMs"),
        "next_cursor.before_updated_at_ms",
      ),
      beforeMeetingId: requiredString(
        first(cursor, "before_meeting_id", "beforeMeetingId"),
        "next_cursor.before_meeting_id",
      ),
    };
  }
  const hasMore = optionalBoolean(first(source, "has_more", "hasMore")) ?? false;
  if (hasMore && !nextCursor) throw new ContractError("next_cursor is required when has_more is true");
  return { ...history, hasMore, nextCursor };
}

export function parseDataGovernanceSettings(value: unknown): DataGovernanceSettings {
  const source = record(value);
  const rawPolicy = first(source, "retention_policy", "retentionPolicy");
  const policy = rawPolicy === "manual_only" ? "local_until_user_deletes" : rawPolicy;
  if (!(policy === "local_until_user_deletes" || policy === "30_days" ||
    policy === "90_days" || policy === "365_days")) {
    throw new ContractError("retention_policy is unsupported");
  }
  return {
    retentionPolicy: policy as DataRetentionPolicy,
    updatedAtMs: requiredNumber(first(source, "updated_at_ms", "updatedAtMs"), "updated_at_ms"),
  };
}

export function parseMeetingAudio(value: unknown): MeetingAudioWithTracks {
  const source = record(value);
  if (!Array.isArray(source.chunks)) throw new ContractError("chunks must be an array");
  const overallStatus = parseMeetingAudioOverallStatus(source.status);
  const trackValues = first(source, "track_states", "trackStates");
  const derivedValues = first(source, "derived_assets", "derivedAssets");
  return {
    ...parseAudioSummary(source),
    meetingId: requiredString(first(source, "meeting_id", "meetingId"), "meeting_id"),
    assembled: optionalBoolean(source.assembled) ?? false,
    playbackUrl: optionalString(first(source, "playback_url", "playbackUrl")),
    format: optionalString(source.format),
    chunks: source.chunks.map(parseAudioChunk),
    status: overallStatus === "partial_failure" ? "failed" : overallStatus,
    overallStatus,
    trackStates: Array.isArray(trackValues) ? trackValues.map(parseMeetingAudioTrack) : [],
    derivedAssets: Array.isArray(derivedValues) ? derivedValues.map(parseMeetingAudioDerivedAsset) : [],
    mixedCreateUrl: optionalString(first(source, "mixed_create_url", "mixedCreateUrl")),
  };
}

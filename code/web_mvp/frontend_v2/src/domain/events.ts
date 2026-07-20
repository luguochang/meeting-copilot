export type SuggestionStatus =
  | "draft"
  | "validating"
  | "committed"
  | "rejected"
  | "superseded";

export type CorrectionStatus =
  | "pending"
  | "processing"
  | "no_change"
  | "changed"
  | "failed_preserved_original"
  | (string & {});

export type SuggestionFeedback =
  | "kept"
  | "ignored"
  | "false_positive"
  | "too_late";

export type RuntimeState =
  | "active"
  | "busy"
  | "idle"
  | "paused"
  | "offline"
  | "error"
  | "unknown";

export type ReviewJobKind = "minutes" | "approach" | "index";

export type ReviewDocumentKind =
  | "minutes"
  | "decisions"
  | "action_items"
  | "risks"
  | "transcript";

export type ReviewDocumentSource = "ai_generated" | "user_final" | "unknown";

export type MeetingTitleSource = "user" | "ai" | "import" | "fallback" | "unknown";

export type ReviewJobStatus =
  | "pending"
  | "running"
  | "retry_wait"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export type LoadState = "idle" | "loading" | "ready" | "error";

export type DataDeletionScope = "recording" | "derived" | "transcript" | "all";

export type DataRetentionPolicy =
  | "local_until_user_deletes"
  | "30_days"
  | "90_days"
  | "365_days";

export interface DataGovernanceSettings {
  retentionPolicy: DataRetentionPolicy;
  updatedAtMs: number;
}

export interface TranscriptSegment {
  meetingId: string;
  segmentId: string;
  finalId: string;
  transcriptSeq: number;
  text: string;
  normalizedText: string;
  startedAtMs: number | null;
  endedAtMs: number | null;
  revision: number;
  evidenceHash: string;
  speakerId?: string | null;
  speakerLabel?: string | null;
  speakerConfidence?: number | null;
  speakerAttributionRevision?: number;
  speakerAttributionSource?: string | null;
  speakerAttributionReason?: string | null;
  correctionStatus?: CorrectionStatus;
  correctionBeforeText?: string | null;
  correctionAfterText?: string | null;
  correctionErrorClass?: string | null;
  correctionUpdatedAtMs?: number | null;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface SemanticParagraph {
  meetingId: string;
  paragraphId: string;
  revision: number;
  text: string;
  startMs: number | null;
  endMs: number | null;
  status: "active" | "stable";
  checkpointIds: string[];
  speakerId?: string | null;
  speakerLabel?: string | null;
  speakerConfidence?: number | null;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface MeetingSpeaker {
  meetingId: string;
  speakerId: string;
  speakerLabel: string;
  ordinal: number;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface ActivePartial {
  segmentId: string;
  text: string;
  startedAtMs: number | null;
  updatedAtMs: number;
}

export interface Suggestion {
  suggestionId: string;
  meetingId: string;
  jobId: string | null;
  generationId: string;
  evidenceSegmentId: string;
  evidenceTranscriptSeq: number;
  evidenceHash: string;
  stateRevision: number;
  status: SuggestionStatus;
  draftText: string;
  draftSeq: number;
  text: string | null;
  finalDraftSeq: number | null;
  feedback: SuggestionFeedback | null;
  createdAtMs: number;
  updatedAtMs: number;
  committedAtMs: number | null;
  formalAi?: FormalAiProvenance | null;
}

export interface FormalAiEvidence {
  segmentIds: string[];
  quote: string;
  evidenceHash?: string | null;
  stateRevision?: number | null;
}

export interface FormalAiProvenance {
  source: "llm_first";
  jobId: string;
  batchId: string;
  provider: string;
  model: string;
  llmCalled: true;
  evidence: FormalAiEvidence;
}

export interface TopicProjection {
  id: string;
  text: string;
  status: "active" | "changed" | "expired" | "unknown";
  evidenceSegmentIds: string[];
  updatedAtMs: number | null;
  formalAi?: FormalAiProvenance | null;
}

export interface OpenQuestionProjection {
  id: string;
  text: string;
  status: "open" | "carried_over" | "answered" | "expired" | "unknown";
  evidenceSegmentIds: string[];
  updatedAtMs: number | null;
  formalAi?: FormalAiProvenance | null;
}

export interface FollowUpProjection {
  question: string;
  reason: string;
  evidenceSegmentIds: string[];
  evidenceQuote: string;
  urgency: "low" | "medium" | "high";
  formalAi?: FormalAiProvenance | null;
}

export type MeetingFactKind = "decision" | "action_item" | "risk";

export type MeetingFactStatus =
  | "candidate"
  | "confirmed"
  | "dismissed"
  | "open"
  | "in_progress"
  | "done"
  | "unknown"
  | (string & {});

export interface EvidenceSpan {
  segmentId: string;
  transcriptSeq: number;
  startMs: number | null;
  endMs: number | null;
  quote: string;
}

export interface MeetingFactBase {
  id: string;
  text: string;
  status: MeetingFactStatus;
  confidence: number | null;
  evidenceSegmentIds: string[];
  evidenceSpans: EvidenceSpan[];
  updatedAtMs: number;
  formalAi?: FormalAiProvenance | null;
}

export type DecisionCandidate = MeetingFactBase;

export interface ActionItemProjection extends MeetingFactBase {
  owner: string | null;
  deadline: string | null;
}

export interface RiskProjection extends MeetingFactBase {
  mitigation: string | null;
}

export type MeetingFact = DecisionCandidate | ActionItemProjection | RiskProjection;

export interface RuntimeIndicator {
  state: RuntimeState;
  label: string;
  level: number | null;
  detail: string | null;
}

export interface MeetingRuntime {
  phase: "live" | "ending" | "ended" | "unknown";
  recording: RuntimeIndicator;
  input: RuntimeIndicator;
  ai: RuntimeIndicator;
  elapsedMs: number | null;
}

export interface MinutesArtifact {
  meetingId: string;
  jobId: string;
  version: number;
  status: "ready" | "degraded" | "unknown";
  markdown: string;
  structured: Record<string, unknown> | null;
  createdAtMs: number;
  updatedAtMs: number;
}

export interface ApproachCard {
  cardId: string | null;
  cardType: string;
  suggestionText: string;
  triggerReason: string | null;
  evidenceQuote: string | null;
  evidenceSegmentIds: string[];
  confidence: number | null;
}

export interface ApproachReview {
  cards: ApproachCard[];
  degraded: boolean | null;
  updatedAtMs: number | null;
}

export interface ReviewJob {
  id: string | null;
  meetingId: string;
  kind: ReviewJobKind;
  status: ReviewJobStatus;
  attempts: number;
  maxAttempts: number | null;
  errorClass: string | null;
  errorMessage?: string | null;
  retryable?: boolean;
  output: Record<string, unknown> | null;
  updatedAtMs: number | null;
  completedAtMs: number | null;
}

export type ReviewJobs = Partial<Record<ReviewJobKind, ReviewJob>>;

export interface ReviewDocument {
  documentId: string | null;
  meetingId: string;
  kind: ReviewDocumentKind;
  revision: number;
  sourceRevision: number | null;
  contentJson: unknown;
  aiVersion: number;
  userVersion: number;
  source: ReviewDocumentSource;
  dirtyState: string | null;
  updatedAtMs: number;
}

export interface ReviewDocumentRevision {
  revision: number;
  author: string;
  source: ReviewDocumentSource;
  contentJson: unknown;
  patch: unknown;
  createdAtMs: number;
}

export type ReviewDocuments = Partial<Record<ReviewDocumentKind, ReviewDocument>>;

export type ImportJobStage =
  | "reading"
  | "normalizing"
  | "transcribing"
  | "correcting"
  | "reviewing"
  | "completed"
  | "unknown";

export interface ImportJob {
  id: string | null;
  meetingId: string | null;
  status: ReviewJobStatus;
  stage: ImportJobStage;
  progress: number | null;
  errorClass: string | null;
  errorMessage: string | null;
  retryable: boolean;
  updatedAtMs: number | null;
}

export interface MeetingAudioSummary {
  status: "recording" | "saved" | "assembling" | "failed" | "unknown";
  chunkCount: number;
  durationMs: number;
  fileSizeBytes: number;
  tracks: string[];
}

export type MeetingInputSource = "microphone" | "system_audio" | "dual_track";

export interface MeetingPreparationInput {
  title?: string | null;
  hotwords: string[];
  inputSource: MeetingInputSource;
  inputDeviceId: string | null;
  inputDeviceName: string | null;
  noticeAcknowledged: true;
}

export interface AudioChunk {
  track: string;
  epoch: number;
  chunkSeq: number;
  sampleRateHz: number;
  sampleCount: number;
  durationMs: number;
  fileSizeBytes: number;
  status: "committed" | "missing" | "corrupted" | "unknown";
  createdAtMs: number | null;
}

export interface MeetingAudio extends MeetingAudioSummary {
  meetingId: string;
  assembled: boolean;
  playbackUrl: string | null;
  format: string | null;
  chunks: AudioChunk[];
}

export interface MeetingHistoryItem {
  meetingId: string;
  title: string | null;
  titleSource?: MeetingTitleSource;
  phase: "live" | "ended" | "unknown";
  startedAtMs: number | null;
  endedAtMs: number | null;
  createdAtMs: number;
  updatedAtMs: number;
  segmentCount: number;
  suggestionCount: number;
  audioDurationMs: number;
  hasMinutes: boolean;
  reviewJobs?: ReviewJobs;
  importJob?: ImportJob | null;
  audioStatus?: MeetingAudioSummary["status"];
}

export interface MeetingHistory {
  meetings: MeetingHistoryItem[];
}

export interface MeetingHistoryCursor {
  beforeUpdatedAtMs: number;
  beforeMeetingId: string;
}

export interface MeetingHistoryPage extends MeetingHistory {
  hasMore: boolean;
  nextCursor: MeetingHistoryCursor | null;
}

export interface TranscriptPage {
  meetingId: string;
  afterTranscriptSeq: number;
  segments: TranscriptSegment[];
  hasMore: boolean;
  nextAfterTranscriptSeq: number;
}

export interface MeetingSnapshot {
  meetingId: string;
  title: string | null;
  titleSource?: MeetingTitleSource;
  updatedAtMs?: number;
  lastSeq: number;
  segments: TranscriptSegment[];
  semanticParagraphs?: SemanticParagraph[];
  activeParagraph?: SemanticParagraph | null;
  activePartial: ActivePartial | null;
  suggestions: Suggestion[];
  decisionCandidates: DecisionCandidate[];
  actionItems: ActionItemProjection[];
  risks: RiskProjection[];
  currentTopic: TopicProjection | null;
  openQuestions: OpenQuestionProjection[];
  followUp?: FollowUpProjection | null;
  minutes: MinutesArtifact | null;
  approach: ApproachReview;
  reviewJobs: ReviewJobs;
  documents?: ReviewDocuments;
  importJob?: ImportJob | null;
  audio: MeetingAudioSummary;
  runtime: MeetingRuntime;
  diagnostics: Record<string, unknown>;
}

export interface MeetingEvent {
  meetingId: string;
  seq: number;
  eventId: string;
  type: string;
  aggregateType: string;
  aggregateId: string;
  occurredAtMs: number;
  correlationId: string | null;
  causationId: string | null;
  idempotencyKey: string;
  payload: Record<string, unknown>;
  publishedAtMs: number | null;
}

const REALTIME_AI_PROJECTION_EVENT_TYPES = new Set([
  "meeting.topic.updated",
  "meeting.open_question.updated",
  "meeting.intelligence.applied",
  "meeting.decision.updated",
  "meeting.action_item.updated",
  "meeting.risk.updated",
  "suggestion.draft.started",
  "suggestion.draft.delta",
  "suggestion.committed",
  "suggestion.superseded",
  "suggestion.evidence.remapped",
]);

export function isRealtimeAiProjectionEventType(type: string): boolean {
  return REALTIME_AI_PROJECTION_EVENT_TYPES.has(type);
}

export function isFormalLlmFirstPayload(value: unknown): value is Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const payload = value as Record<string, unknown>;
  if (payload.source !== "llm_first" || payload.llm_called !== true) return false;
  if (!["job_id", "batch_id", "provider", "model"].every((key) =>
    typeof payload[key] === "string" && Boolean((payload[key] as string).trim()))) return false;
  const evidence = payload.evidence;
  if (evidence === null || typeof evidence !== "object" || Array.isArray(evidence)) return false;
  const segmentIds = (evidence as Record<string, unknown>).segment_ids;
  return Array.isArray(segmentIds) && segmentIds.some((item) => typeof item === "string" && Boolean(item.trim()));
}

export interface EventsPage {
  meetingId: string;
  afterSeq: number;
  lastSeq: number;
  events: MeetingEvent[];
  hasMore: boolean;
  nextAfterSeq: number;
}

export type ConnectionState = "idle" | "connecting" | "live" | "reconnecting" | "offline";

export interface MeetingViewState extends MeetingSnapshot {
  archivedTranscript: string;
  archivedSegmentCount: number;
  connection: ConnectionState;
  lastSyncedAtMs: number | null;
  transportError: string | null;
  ending: boolean;
  endError: string | null;
  fullTranscript: TranscriptSegment[];
  fullTranscriptState: LoadState;
  fullTranscriptError: string | null;
  audioDetail: MeetingAudio | null;
  audioLoadState: LoadState;
  audioError: string | null;
  speakers: MeetingSpeaker[];
  speakerLoadState: LoadState;
  speakerError: string | null;
}

export type MeetingAction =
  | { type: "meeting.bound"; meetingId: string }
  | { type: "snapshot.received"; snapshot: MeetingSnapshot; receivedAtMs: number }
  | { type: "events.received"; events: MeetingEvent[]; receivedAtMs: number }
  | { type: "connection.changed"; connection: ConnectionState; error?: string | null }
  | { type: "meeting.ending" }
  | { type: "meeting.end_failed"; error: string }
  | { type: "suggestion.feedback_saved"; suggestionId: string; feedback: SuggestionFeedback }
  | { type: "fact.status_saved"; factType: MeetingFactKind; factId: string; status: MeetingFactStatus }
  | { type: "transcript.loading" }
  | { type: "transcript.received"; segments: TranscriptSegment[] }
  | { type: "transcript.failed"; error: string }
  | { type: "speakers.loading" }
  | { type: "speakers.received"; speakers: MeetingSpeaker[] }
  | { type: "speakers.failed"; error: string }
  | { type: "speaker.renamed"; speaker: MeetingSpeaker }
  | { type: "audio.loading" }
  | { type: "audio.received"; audio: MeetingAudio }
  | { type: "audio.failed"; error: string };

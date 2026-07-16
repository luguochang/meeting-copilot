export type SuggestionStatus =
  | "draft"
  | "validating"
  | "committed"
  | "rejected"
  | "superseded";

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

export type ReviewJobStatus =
  | "pending"
  | "running"
  | "retry_wait"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export type LoadState = "idle" | "loading" | "ready" | "error";

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
}

export interface TopicProjection {
  id: string;
  text: string;
  status: "active" | "changed" | "expired" | "unknown";
  evidenceSegmentIds: string[];
  updatedAtMs: number | null;
}

export interface OpenQuestionProjection {
  id: string;
  text: string;
  status: "open" | "carried_over" | "answered" | "expired" | "unknown";
  evidenceSegmentIds: string[];
  updatedAtMs: number | null;
}

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
  output: Record<string, unknown> | null;
  updatedAtMs: number | null;
  completedAtMs: number | null;
}

export type ReviewJobs = Partial<Record<ReviewJobKind, ReviewJob>>;

export interface MeetingAudioSummary {
  status: "recording" | "saved" | "assembling" | "failed" | "unknown";
  chunkCount: number;
  durationMs: number;
  fileSizeBytes: number;
  tracks: string[];
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
  phase: "live" | "ended" | "unknown";
  startedAtMs: number | null;
  endedAtMs: number | null;
  createdAtMs: number;
  updatedAtMs: number;
  segmentCount: number;
  suggestionCount: number;
  audioDurationMs: number;
  hasMinutes: boolean;
}

export interface MeetingHistory {
  meetings: MeetingHistoryItem[];
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
  lastSeq: number;
  segments: TranscriptSegment[];
  activePartial: ActivePartial | null;
  suggestions: Suggestion[];
  currentTopic: TopicProjection | null;
  openQuestions: OpenQuestionProjection[];
  minutes: MinutesArtifact | null;
  approach: ApproachReview;
  reviewJobs: ReviewJobs;
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
}

export type MeetingAction =
  | { type: "meeting.bound"; meetingId: string }
  | { type: "snapshot.received"; snapshot: MeetingSnapshot; receivedAtMs: number }
  | { type: "events.received"; events: MeetingEvent[]; receivedAtMs: number }
  | { type: "connection.changed"; connection: ConnectionState; error?: string | null }
  | { type: "meeting.ending" }
  | { type: "meeting.end_failed"; error: string }
  | { type: "suggestion.feedback_saved"; suggestionId: string; feedback: SuggestionFeedback }
  | { type: "transcript.loading" }
  | { type: "transcript.received"; segments: TranscriptSegment[] }
  | { type: "transcript.failed"; error: string }
  | { type: "audio.loading" }
  | { type: "audio.received"; audio: MeetingAudio }
  | { type: "audio.failed"; error: string };

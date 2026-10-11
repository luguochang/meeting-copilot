import { isTranscriptEvidence, parseDocumentSourceEvidence, parseDocumentSources, serializeDocumentSource } from "./sourceEvidence";
import { coachUiRenderReceiptWire, parseCoachUiRenderReceipt } from "../domain/coachUiRenderReceipt";
import type {
  CoachUiRenderReceipt,
  DataDeletionScope,
  DataGovernanceSettings,
  DataRetentionPolicy,
  EventsPage,
  MeetingFactKind,
  MeetingFactStatus,
  AskAiMessage,
  DocumentSourceEvidence,
  AskAiScope,
  AskAiThread,
  MeetingChapter,
  MeetingHistory,
  MeetingHistoryCursor,
  MeetingHistoryPage,
  ImportJob,
  MeetingInputSource,
  MeetingCorrectionMode,
  MeetingPreparationInput,
  MeetingPreparationSnapshot,
  MeetingNote,
  MeetingNoteEvidence,
  MeetingNoteSourceKind,
  MeetingNoteStatus,
  MeetingSpeaker,
  MeetingSnapshot,
  ReviewDocument,
  ReviewDocumentKind,
  ReviewDocumentRevision,
  ReviewJobKind,
  SuggestionFeedback,
  TranscriptSegment,
} from "../domain/events";
import {
  ContractError,
  type MeetingAudioDerivedAsset,
  type MeetingAudioWithTracks,
  parseDataGovernanceSettings,
  parseEventsPage,
  parseMeetingAudio,
  parseMeetingAudioDerivedAsset,
  parseMeetingHistory,
  parseMeetingHistoryPage,
  parseMeetingSpeaker,
  parseMeetingSpeakers,
  parseMeetingSnapshot,
  parseLiveAnalysisProfile,
  parseImportJob,
  parseProviderStatus,
  parseAsrRuntimeStatus,
  parseMeetingCorrectionMode,
  parseMeetingCorrectionCapability,
  type MeetingCorrectionCapability,
  asrPreparationBlocker,
  type AsrRuntimeStatus,
  parseReviewDocument,
  parseReviewDocumentRevisions,
  parseTranscriptPage,
  type ProviderStatus,
} from "./schema";

import { coachRequestOutcomeFromSnapshot, type CoachRequestOutcome } from "./coachRequestOutcome";
export type { CoachRequestOutcome, CoachRequestStatus, CoachRequestFailureCategory } from "./coachRequestOutcome";
export type ContributionIntent = "ask_question" | "compare_options" | "formulate_response" | "freeform";
export interface ContributionRequestOptions {
  intent: ContributionIntent;
  topicId?: string;
  expectedBasisRevision?: number;
  selectedSegmentIds?: string[];
}
export interface MeetingCreateOptions { materialSetId?: string; expectedMaterialSetRevision?: number; idempotencyKey?: string }
export interface DiscussionWorkItem { id: string; title: string; state: string; version: number; context: Record<string, unknown>; analysis_basis_revision?: number }

export interface MeetingApi {
  getDiscussion?(meetingId: string, signal?: AbortSignal): Promise<DiscussionWorkItem | null>;
  updateDiscussion?(meetingId: string, item: DiscussionWorkItem, changes: { title?: string; context?: Record<string, unknown>; state?: string }): Promise<DiscussionWorkItem>;
  createMeeting(
    meetingId: string,
    title?: string | null,
    inputSourceOrSignal?: MeetingInputSource | AbortSignal,
    signal?: AbortSignal,
    options?: MeetingCreateOptions,
  ): Promise<void>;
  saveMeetingPreparation(
    meetingId: string,
    preparation: MeetingPreparationInput,
    signal?: AbortSignal,
  ): Promise<void>;
  importRecording(file: File, title?: string, signal?: AbortSignal): Promise<ImportRecordingResult>;
  retryImportJob(meetingId: string, signal?: AbortSignal): Promise<ImportJob>;
  updateMeetingTitle(meetingId: string, title: string, signal?: AbortSignal): Promise<void>;
  deleteMeeting(
    meetingId: string,
    scopeOrSignal?: DataDeletionScope | AbortSignal,
    signal?: AbortSignal,
  ): Promise<void>;
  getDataGovernanceSettings?(signal?: AbortSignal): Promise<DataGovernanceSettings>;
  updateDataGovernanceSettings?(
    retentionPolicy: DataRetentionPolicy,
    signal?: AbortSignal,
  ): Promise<DataGovernanceSettings>;
  listMeetings(signal?: AbortSignal): Promise<MeetingHistory>;
  listMeetingsPage?(query: MeetingHistoryQuery, signal?: AbortSignal): Promise<MeetingHistoryPage>;
  getSnapshot(meetingId: string, signal?: AbortSignal): Promise<MeetingSnapshot>;
  getTranscript(meetingId: string, signal?: AbortSignal): Promise<TranscriptSegment[]>;
  getSpeakers(meetingId: string, signal?: AbortSignal): Promise<MeetingSpeaker[]>;
  renameSpeaker(
    meetingId: string,
    speakerId: string,
    speakerLabel: string,
    signal?: AbortSignal,
  ): Promise<MeetingSpeaker>;
  getEvents(meetingId: string, afterSeq: number, signal?: AbortSignal): Promise<EventsPage>;
  getAudio(meetingId: string, signal?: AbortSignal): Promise<MeetingAudioWithTracks>;
  createMixedAudio?(meetingId: string, signal?: AbortSignal): Promise<MeetingAudioDerivedAsset>;
  exportMeeting(meetingId: string, format: MeetingExportFormat, signal?: AbortSignal): Promise<void>;
  exportDiagnosticBundle(signal?: AbortSignal): Promise<void>;
  saveReviewDocument(
    meetingId: string,
    kind: ReviewDocumentKind,
    expectedRevision: number,
    contentJson: unknown,
    signal?: AbortSignal,
  ): Promise<ReviewDocument>;
  getDocumentRevisions(
    meetingId: string,
    kind: ReviewDocumentKind,
    signal?: AbortSignal,
  ): Promise<ReviewDocumentRevision[]>;
  regenerateDocument(meetingId: string, kind: ReviewDocumentKind, signal?: AbortSignal): Promise<void>;
  retryReviewJob(meetingId: string, kind: ReviewJobKind, signal?: AbortSignal): Promise<void>;
  retryTranscriptCorrection(meetingId: string, signal?: AbortSignal): Promise<void>;
  endMeeting(meetingId: string, signal?: AbortSignal): Promise<void>;
  saveSuggestionFeedback(
    meetingId: string,
    suggestionId: string,
    feedback: SuggestionFeedback,
    signal?: AbortSignal,
  ): Promise<void>;
  getCoachRequestStatus?(meetingId: string, jobId: string, signal?: AbortSignal): Promise<"pending" | "succeeded" | "failed" | "timed_out" | "no_change">;
  getCoachRequestOutcome?(meetingId: string, jobId: string, signal?: AbortSignal): Promise<CoachRequestOutcome>;
  requestRealtimeCoach?(
    meetingId: string,
    request: string,
    answerId?: string | null,
    signal?: AbortSignal,
    options?: ContributionRequestOptions,
  ): Promise<string | void>;
  saveFactStatus(
    meetingId: string,
    factType: MeetingFactKind,
    factId: string,
    status: MeetingFactStatus,
    signal?: AbortSignal,
  ): Promise<void>;
  updateFact?(
    meetingId: string,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
    signal?: AbortSignal,
  ): Promise<void>;
  mergeFacts?(
    meetingId: string,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
    signal?: AbortSignal,
  ): Promise<void>;
  markUiRendered(
    jobId: string,
    eventSeq: number,
    draftSeq: number,
    signal?: AbortSignal,
  ): Promise<void>;
  markCoachUiRendered?(receipt: CoachUiRenderReceipt, signal?: AbortSignal): Promise<void>;
  getMeetingPreparation?(meetingId: string, signal?: AbortSignal): Promise<MeetingPreparationSnapshot>;
  getMeetingPreparationVersions?(meetingId: string, signal?: AbortSignal): Promise<MeetingPreparationSnapshot[]>;
  getChapters?(meetingId: string, query?: string, signal?: AbortSignal): Promise<MeetingChapter[]>;
  getAskThreads?(meetingId: string, signal?: AbortSignal): Promise<AskAiThread[]>;
  askMeeting?(
    meetingId: string,
    input: {
      question: string;
      scope: AskAiScope;
      threadId?: string | null;
      segmentIds?: string[];
      chapterId?: string | null;
      recentMinutes?: 1 | 3 | 5 | 10;
      intent?: "catch_up";
      includeKnowledge?: boolean;
    },
    onDelta: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<{ threadId: string; message: AskAiMessage }>;
  pinAskMessage?(
    meetingId: string,
    messageId: string,
    pinnedKind: AskAiMessage["pinnedKind"],
    signal?: AbortSignal,
  ): Promise<AskAiMessage>;
  listNotes?(
    query?: { meetingId?: string | null; status?: MeetingNoteStatus | "all"; query?: string; limit?: number },
    signal?: AbortSignal,
  ): Promise<MeetingNote[]>;
  listNotesPage?(query?: { meetingId?: string | null; status?: MeetingNoteStatus | "all"; query?: string; limit?: number; cursor?: string | null }, signal?: AbortSignal): Promise<{ items: MeetingNote[]; nextCursor: string | null; hasMore: boolean }>;
  getNote?(noteId: string, signal?: AbortSignal): Promise<MeetingNote>;
  createNote?(
    meetingId: string,
    input: {
      title?: string;
      body: string;
      sourceKind: MeetingNoteSourceKind;
      sourceMessageId?: string | null;
      evidence?: Array<Omit<MeetingNoteEvidence, "ordinal" | "meetingId"> & { meetingId?: string | null }>;
    },
    signal?: AbortSignal,
  ): Promise<MeetingNote>;
  updateNote?(
    noteId: string,
    expectedVersion: number,
    changes: { title?: string; body?: string; status?: MeetingNoteStatus },
    signal?: AbortSignal,
  ): Promise<MeetingNote>;
  deleteNote?(noteId: string, expectedVersion: number, signal?: AbortSignal): Promise<MeetingNote>;
  addNoteEvidence?(
    noteId: string,
    evidence: Array<Omit<MeetingNoteEvidence, "ordinal" | "meetingId"> & { meetingId?: string | null }>,
    signal?: AbortSignal,
  ): Promise<MeetingNote>;
  deleteNoteEvidence?(noteId: string, ordinal: number, signal?: AbortSignal): Promise<MeetingNote>;
  createMeetingEntity?(
    meetingId: string,
    input: {
      kind: "decision_candidate" | "action_item" | "risk" | "open_question";
      text: string;
      sourceMessageId?: string | null;
      evidence?: AskAiMessage["evidence"];
      sourceEvidence?: DocumentSourceEvidence[];
    },
    signal?: AbortSignal,
  ): Promise<void>;
  getLocalCapabilities?(signal?: AbortSignal): Promise<LocalCapabilityStatus>;
  importLocalCapabilityPackage?(
    file: File,
    signal?: AbortSignal,
  ): Promise<LocalCapabilityStatus>;
}

export type MeetingExportFormat = "markdown" | "docx" | "json";

export interface MeetingHistoryQuery {
  query?: string;
  status?: "all" | "live" | "processing" | "ready" | "failed";
  limit?: number;
  cursor?: MeetingHistoryCursor | null;
}

export interface ImportRecordingResult {
  meetingId: string | null;
  job: ImportJob | null;
}

export type LocalRuntimeState = "ready" | "starting" | "idle" | "available" | "disabled" | "unavailable" | "failed" | "unknown";

export interface LocalRuntimeCapability {
  available: boolean | null;
  state: LocalRuntimeState;
  source: string;
  reason: string | null;
  processReady: boolean | null;
  processRunning: boolean | null;
  formats?: Record<string, boolean>;
}

export interface LocalCapabilityStatus {
  schemaVersion: string;
  platform: string;
  baseAppReady: boolean;
  installed: boolean;
  packageId: string | null;
  packageVersion: string | null;
  installedAt: string | null;
  realtimeAsrReady: boolean;
  fileAsrReady: boolean;
  restartRequired: boolean;
  signatureStatus: string | null;
  releaseScope: string | null;
  downloadPageUrl: string | null;
  importAvailable: boolean;
  errors: string[];
  deploymentMode?: "source" | "packaged" | "desktop" | "unknown";
  runtimeCapabilities?: {
    realtimeAsr: LocalRuntimeCapability;
    fileAsr: LocalRuntimeCapability;
    offlineRefinement: LocalRuntimeCapability;
  };
}

function apiRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ContractError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function apiString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) throw new ContractError(`${label} must be a string`);
  return value;
}

function parseLocalCapabilityStatus(value: unknown): LocalCapabilityStatus {
  const item = apiRecord(value, "local capability status");
  const schemaVersion = apiString(item.schema_version, "local capability schema_version");
  if (schemaVersion !== "meeting_copilot.local_capability_status.v1") {
    throw new ContractError("local capability schema_version is unsupported");
  }
  const runtime = item.runtime_capabilities === undefined ? null : apiRecord(item.runtime_capabilities, "runtime capabilities");
  const parseRuntime = (value: unknown): LocalRuntimeCapability => {
    const capability = apiRecord(value, "runtime capability");
    const states: LocalRuntimeState[] = ["ready", "starting", "idle", "available", "disabled", "unavailable", "failed", "unknown"];
    if (!states.includes(capability.state as LocalRuntimeState)) throw new ContractError("runtime capability state is unsupported");
    return {
      available: typeof capability.available === "boolean" ? capability.available : null,
      state: capability.state as LocalRuntimeState,
      source: typeof capability.source === "string" ? capability.source : "unknown",
      reason: typeof capability.reason === "string" ? capability.reason : null,
      processReady: typeof capability.process_ready === "boolean" ? capability.process_ready : null,
      processRunning: typeof capability.process_running === "boolean" ? capability.process_running : null,
      ...(capability.formats && typeof capability.formats === "object" && !Array.isArray(capability.formats)
        ? { formats: Object.fromEntries(Object.entries(capability.formats).filter((entry): entry is [string, boolean] => typeof entry[1] === "boolean")) }
        : {}),
    };
  };
  return {
    schemaVersion,
    platform: apiString(item.platform, "local capability platform"),
    baseAppReady: item.base_app_ready === true,
    installed: item.installed === true,
    packageId: typeof item.package_id === "string" ? item.package_id : null,
    packageVersion: typeof item.package_version === "string" ? item.package_version : null,
    installedAt: typeof item.installed_at === "string" ? item.installed_at : null,
    realtimeAsrReady: item.realtime_asr_ready === true,
    fileAsrReady: item.file_asr_ready === true,
    restartRequired: item.restart_required === true,
    signatureStatus: typeof item.signature_status === "string" ? item.signature_status : null,
    releaseScope: typeof item.release_scope === "string" ? item.release_scope : null,
    downloadPageUrl: typeof item.download_page_url === "string" ? item.download_page_url : null,
    importAvailable: item.import_available === true,
    errors: Array.isArray(item.errors)
      ? item.errors.filter((error): error is string => typeof error === "string")
      : [],
    ...(item.deployment_mode === undefined ? {} : {
      deploymentMode: ["source", "packaged", "desktop"].includes(String(item.deployment_mode))
        ? item.deployment_mode as "source" | "packaged" | "desktop" : "unknown",
    }),
    ...(runtime ? { runtimeCapabilities: {
      realtimeAsr: parseRuntime(runtime.realtime_asr),
      fileAsr: parseRuntime(runtime.file_asr),
      offlineRefinement: parseRuntime(runtime.offline_refinement),
    } } : {}),
  };
}

function parseMeetingPreparation(value: unknown): MeetingPreparationSnapshot {
  const item = apiRecord(value, "meeting preparation");
  const inputSource = ["microphone", "system_audio", "dual_track"].includes(String(item.input_source))
    ? item.input_source as MeetingInputSource
    : "microphone";
  const presetId = ["general", "decision", "project", "interview", "brainstorm"].includes(String(item.preset_id))
    ? item.preset_id as MeetingPreparationSnapshot["presetId"]
    : "general";
  const outputFormat = ["standard", "decision_log", "action_plan", "brief"].includes(String(item.output_format))
    ? item.output_format as MeetingPreparationSnapshot["outputFormat"]
    : "standard";
  const suggestionPolicy = ["off", "low_frequency", "standard"].includes(String(item.proactive_suggestion_policy))
    ? item.proactive_suggestion_policy as MeetingPreparationSnapshot["proactiveSuggestionPolicy"]
    : "low_frequency";
  return {
    meetingId: typeof item.meeting_id === "string" ? item.meeting_id : "",
    liveAnalysisProfile: parseLiveAnalysisProfile(item.live_analysis_profile),
    correctionMode: parseMeetingCorrectionMode(item.correction_mode),
    hotwords: Array.isArray(item.hotwords) ? item.hotwords.filter((entry): entry is string => typeof entry === "string") : [],
    inputSource,
    inputDeviceId: typeof item.input_device_id === "string" ? item.input_device_id : null,
    inputDeviceName: typeof item.input_device_name === "string" ? item.input_device_name : null,
    noticeAcknowledged: item.notice_acknowledged === true,
    presetId,
    meetingGoal: typeof item.meeting_goal === "string" ? item.meeting_goal : null,
    participantRole: typeof item.participant_role === "string" ? item.participant_role : null,
    focusPoints: Array.isArray(item.focus_points) ? item.focus_points.filter((entry): entry is string => typeof entry === "string") : [],
    outputFormat,
    proactiveSuggestionPolicy: suggestionPolicy,
    version: typeof item.version === "number" ? item.version : 0,
    updatedAtMs: typeof item.updated_at_ms === "number" ? item.updated_at_ms : 0,
  };
}

function parseAskEvidence(value: unknown): AskAiMessage["evidence"] {
  if (!Array.isArray(value)) return [];
  return value.flatMap<AskAiMessage["evidence"][number]>((raw) => {
    const item = apiRecord(raw, "Ask AI evidence");
    if (item.source_type === "document" || item.source_type === "meeting_attachment") return [parseDocumentSourceEvidence(item)];
    const segmentId = typeof item.segment_id === "string" ? item.segment_id : "";
    if (!segmentId) return [];
    return [{
      sourceType: "transcript" as const,
      ...(typeof item.revision === "number" ? { revision: item.revision } : {}),
      ...(typeof item.evidence_hash === "string" ? { evidenceHash: item.evidence_hash } : {}),
      segmentId,
      transcriptSeq: typeof item.transcript_seq === "number" ? item.transcript_seq : 0,
      startMs: typeof item.start_ms === "number" ? item.start_ms : null,
      endMs: typeof item.end_ms === "number" ? item.end_ms : null,
      quote: typeof item.quote === "string" ? item.quote : "",
    }];
  });
}

function parseAskMessage(value: unknown): AskAiMessage {
  const item = apiRecord(value, "Ask AI message");
  const role = item.role === "assistant" ? "assistant" : "user";
  const scope = ["selection", "recent", "chapter", "meeting", "project"].includes(String(item.scope))
    ? item.scope as AskAiScope
    : "recent";
  const status = ["pending", "completed", "failed"].includes(String(item.status))
    ? item.status as AskAiMessage["status"]
    : "failed";
  const pinnedKind = ["note", "fact", "action_item"].includes(String(item.pinned_kind))
    ? item.pinned_kind as AskAiMessage["pinnedKind"]
    : null;
  return {
    messageId: apiString(item.message_id, "message_id"),
    threadId: apiString(item.thread_id, "thread_id"),
    meetingId: apiString(item.meeting_id, "meeting_id"),
    role,
    content: typeof item.content === "string" ? item.content : "",
    scope,
    evidence: parseAskEvidence(item.evidence),
    evidenceStatus: item.evidence_status === "valid" || item.evidence_status === "stale" ? item.evidence_status : "unknown",
    status,
    errorClass: typeof item.error_class === "string" ? item.error_class : null,
    pinnedKind,
    createdAtMs: typeof item.created_at_ms === "number" ? item.created_at_ms : 0,
    updatedAtMs: typeof item.updated_at_ms === "number" ? item.updated_at_ms : 0,
  };
}

function parseNoteEvidence(value: unknown): MeetingNoteEvidence[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw) => {
    const item = apiRecord(raw, "note evidence");
    return {
      ordinal: typeof item.ordinal === "number" ? item.ordinal : 0,
      meetingId: typeof item.meeting_id === "string" ? item.meeting_id : null,
      segmentId: apiString(item.segment_id, "note evidence segment_id"),
      transcriptSeq: typeof item.transcript_seq === "number" ? item.transcript_seq : null,
      startMs: typeof item.start_ms === "number" ? item.start_ms : null,
      endMs: typeof item.end_ms === "number" ? item.end_ms : null,
      quote: typeof item.quote === "string" ? item.quote : "",
    };
  });
}

function parseMeetingNote(value: unknown): MeetingNote {
  const item = apiRecord(value, "meeting note");
  const sourceKind = ["selection", "ask_ai", "manual"].includes(String(item.source_kind))
    ? item.source_kind as MeetingNoteSourceKind
    : "manual";
  const status = ["active", "archived", "deleted"].includes(String(item.status))
    ? item.status as MeetingNoteStatus
    : "active";
  return {
    noteId: apiString(item.note_id, "note_id"),
    meetingId: typeof item.meeting_id === "string" ? item.meeting_id : null,
    title: apiString(item.title, "note title"),
    body: typeof item.body === "string" ? item.body : "",
    sourceKind,
    sourceMessageId: typeof item.source_message_id === "string" ? item.source_message_id : null,
    version: typeof item.version === "number" ? item.version : 1,
    status,
    createdAtMs: typeof item.created_at_ms === "number" ? item.created_at_ms : 0,
    updatedAtMs: typeof item.updated_at_ms === "number" ? item.updated_at_ms : 0,
    evidence: parseNoteEvidence(item.evidence),
    sourceEvidence: parseDocumentSources(item.source_evidence),
    evidenceStatus: item.evidence_status === "valid" || item.evidence_status === "stale" ? item.evidence_status : "unknown",
  };
}

function noteEvidencePayload(evidence: Array<Omit<MeetingNoteEvidence, "ordinal" | "meetingId"> & { meetingId?: string | null }>) {
  return evidence.map((item) => ({
    meeting_id: item.meetingId || undefined,
    segment_id: item.segmentId,
    transcript_seq: item.transcriptSeq,
    start_ms: item.startMs,
    end_ms: item.endMs,
    quote: item.quote,
  }));
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

function trimBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

function endpoint(baseUrl: string, path: string): string {
  return `${baseUrl}${path}`;
}

async function responseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return text || null;
}

function errorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
  }
  return `请求失败（${status}）`;
}

export async function fetchProviderStatus(signal?: AbortSignal): Promise<ProviderStatus> {
  const response = await fetch("/providers/status", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, body), body);
  return parseProviderStatus(body);
}

// These two local operations do not acquire audio, a meeting lease, or an LLM lane.
async function asrPreparationRequest(path: string, method: "GET" | "POST", signal?: AbortSignal): Promise<{ ok: boolean; body: unknown }> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal?.aborted) controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(abort, method === "POST" ? 65_000 : 6_000);
  try {
    const response = await fetch(path, { method, signal: controller.signal, cache: "no-store", headers: {
      Accept: "application/json",
      ...(method === "POST" ? { "x-meeting-copilot-verification": "1" } : {}),
    } });
    return { ok: response.ok, body: await response.json().catch(() => null) };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export async function fetchAsrRuntimeStatus(signal?: AbortSignal): Promise<AsrRuntimeStatus | null> {
  try {
    const response = await asrPreparationRequest("/providers/asr/runtime", "GET", signal);
    return response.ok ? parseAsrRuntimeStatus(response.body) : null;
  } catch {
    // The older legitimate capture paths retain their existing native BEGIN checks.
    return null;
  }
}

export async function fetchMeetingCorrectionCapability(mode: MeetingCorrectionMode, signal?: AbortSignal, meetingId?: string): Promise<MeetingCorrectionCapability | null> {
  const query = new URLSearchParams({ correction_mode: mode });
  if (meetingId) query.set("meeting_id", meetingId);
  try {
    const response = await asrPreparationRequest(`/providers/asr/correction-capability?${query}`, "GET", signal);
    return response.ok ? parseMeetingCorrectionCapability(response.body) : null;
  } catch { return null; }
}

export async function prepareLocalAsrRefiner(signal?: AbortSignal): Promise<void> {
  let message = "离线识别准备失败，请先重新检查识别状态，再决定是否重新准备。";
  try {
    const response = await asrPreparationRequest("/providers/asr/prewarm", "POST", signal);
    const body = response.body;
    if (!response.ok || !body || typeof body !== "object" || !("ok" in body) || body.ok !== true) {
      const detail = body && typeof body === "object" && "detail" in body ? body.detail : null;
      const code = detail && typeof detail === "object" && "error_code" in detail ? detail.error_code : null;
      const reasons: Record<string, string> = {
        asr_capacity_unavailable: "本机内存资源不足，请释放内存后重新检查识别状态。",
        asr_resource_probe_failed: "暂时无法确认可用内存，请先重新检查识别状态。",
        asr_resource_guard_triggered: "本次准备触发资源保护，请稍后重新检查识别状态。",
        asr_cold_start_busy: "另一个识别模型正在加载，请等待后重新检查识别状态。",
        worker_out_of_memory: "本机内存不足，请释放内存后重新检查识别状态。",
        worker_dependency_load_failed: "本地识别组件加载失败，请修复组件后重新准备。",
        worker_dependency_unavailable: "本地识别组件缺失，请补齐组件后重新准备。",
        worker_model_file_missing: "本地模型文件缺失，请补齐模型后重新准备。",
        worker_model_file_unreadable: "本地模型文件不可读，请检查文件权限后重新准备。",
        worker_checkpoint_load_failed: "本地模型加载失败，请核对模型完整性后重新准备。",
        worker_model_load_failed: "本地模型或参数不兼容，请核对配置后重新准备。",
        worker_start_timeout: "等待启动超时，请先重新检查识别状态，再决定是否重新准备。",
        worker_spawn_failed: "本地识别进程无法启动，请核对运行环境后重新准备。",
        worker_exited_before_ready: "本地识别在准备完成前退出，请核对运行环境后重新准备。",
        worker_exited: "本地识别已退出，请先重新检查识别状态。",
        worker_write_failed: "本地识别连接中断，请先重新检查识别状态。",
        worker_timeout: "本地识别等待超时，请先重新检查识别状态，再决定是否重新准备。",
      };
      if (typeof code === "string" && Object.hasOwn(reasons, code)) message = `离线识别准备失败：${reasons[code]}`;
      throw new Error(message);
    }
  } catch {
    throw new Error(message);
  }
}

export async function checkAsrCapturePreparation(mode?: MeetingCorrectionMode, meetingId?: string): Promise<void> {
  const [status, capability] = await Promise.all([
    fetchAsrRuntimeStatus(),
    mode ? fetchMeetingCorrectionCapability(mode, undefined, meetingId) : Promise.resolve(undefined),
  ]);
  if (mode && !capability) throw new Error("未取得本场校对模式和资源检查结果，请重新检查识别状态后开始会议。");
  if (mode && capability?.requestedMode !== mode) throw new Error("本场校对模式已变化，请重新读取会议准备后开始。");
  const blocker = asrPreparationBlocker(status, capability);
  if (blocker) throw new Error(blocker);
}

export class HttpMeetingApi implements MeetingApi {
  async getDiscussion(meetingId: string, signal?: AbortSignal): Promise<DiscussionWorkItem | null> {
    const body = apiRecord(await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/work-items?kind=discussion&limit=20`, { signal }), "议题工作状态");
    if (!Array.isArray(body.items)) throw new ContractError("议题列表格式错误");
    const found = body.items.find((value) => value && typeof value === "object" && ["investigating", "waiting_for_evidence", "ready"].includes((value as { state?: string }).state ?? ""));
    if (!found) return null;
    const x = apiRecord(found, "当前议题");
    const id = typeof x.work_item_id === "string" ? x.work_item_id : x.id;
    if (typeof id !== "string" || typeof x.title !== "string" || typeof x.version !== "number") throw new ContractError("当前议题格式错误");
    return { ...(x as unknown as DiscussionWorkItem), id, context: x.context && typeof x.context === "object" ? x.context as Record<string, unknown> : {} };
  }
  async updateDiscussion(meetingId: string, item: DiscussionWorkItem, changes: { title?: string; context?: Record<string, unknown>; state?: string }): Promise<DiscussionWorkItem> {
    const body = apiRecord(await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/work-items/${encodeURIComponent(item.id)}`, { method: "PATCH", body: JSON.stringify({ ...changes, expected_version: item.version }) }), "议题更新");
    const x = apiRecord(body.work_item, "当前议题");
    const id = typeof x.work_item_id === "string" ? x.work_item_id : x.id;
    if (typeof id !== "string" || typeof x.version !== "number") throw new ContractError("议题更新未返回版本");
    return { ...(x as unknown as DiscussionWorkItem), id };
  }
  readonly baseUrl: string;

  constructor(baseUrl = import.meta.env.VITE_API_BASE_URL ?? "") {
    this.baseUrl = trimBaseUrl(baseUrl);
  }

  private async request(path: string, init: RequestInit = {}): Promise<unknown> {
    const isMultipart = typeof FormData !== "undefined" && init.body instanceof FormData;
    const response = await fetch(endpoint(this.baseUrl, path), {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body && !isMultipart ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    const body = await responseBody(response);
    if (!response.ok) throw new ApiError(response.status, errorMessage(response.status, body), body);
    return body;
  }

  async createMeeting(
    meetingId: string,
    title?: string | null,
    inputSourceOrSignal: MeetingInputSource | AbortSignal = "microphone",
    signal?: AbortSignal,
    options?: MeetingCreateOptions,
  ): Promise<void> {
    const inputSource = typeof inputSourceOrSignal === "string" ? inputSourceOrSignal : "microphone";
    const requestSignal = typeof inputSourceOrSignal === "string" ? signal : inputSourceOrSignal;
    await this.request("/v2/meetings", {
      method: "POST",
      ...(options?.idempotencyKey ? { headers: { "Idempotency-Key": options.idempotencyKey } } : {}),
      body: JSON.stringify({
        meeting_id: meetingId,
        expected_duration_seconds: 3_600,
        track_count: inputSource === "dual_track" ? 2 : 1,
        ...(options?.materialSetId ? { material_set_id: options.materialSetId, expected_material_set_revision: options.expectedMaterialSetRevision } : {}),
        ...(title?.trim() ? { title: title.trim() } : {}),
      }),
      signal: requestSignal,
    });
  }

  async saveMeetingPreparation(
    meetingId: string,
    preparation: MeetingPreparationInput,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/preparation`,
      {
        method: "PUT",
        body: JSON.stringify({
          hotwords: preparation.hotwords,
          input_source: preparation.inputSource,
          input_device_id: preparation.inputDeviceId,
          input_device_name: preparation.inputDeviceName,
          notice_acknowledged: preparation.noticeAcknowledged,
          ...(preparation.liveAnalysisProfile !== undefined ? { live_analysis_profile: preparation.liveAnalysisProfile } : {}),
          ...(preparation.correctionMode !== undefined ? { correction_mode: preparation.correctionMode } : {}),
          ...(preparation.presetId ? { preset_id: preparation.presetId } : {}),
          ...(preparation.meetingGoal !== undefined ? { meeting_goal: preparation.meetingGoal } : {}),
          ...(preparation.participantRole !== undefined ? { participant_role: preparation.participantRole } : {}),
          ...(preparation.focusPoints !== undefined ? { focus_points: preparation.focusPoints } : {}),
          ...(preparation.outputFormat ? { output_format: preparation.outputFormat } : {}),
          ...(preparation.proactiveSuggestionPolicy
            ? { proactive_suggestion_policy: preparation.proactiveSuggestionPolicy }
            : {}),
        }),
        signal,
      },
    );
  }

  async importRecording(file: File, title?: string, signal?: AbortSignal): Promise<ImportRecordingResult> {
    const form = new FormData();
    form.append("file", file, file.name);
    if (title?.trim()) form.append("title", title.trim());
    const body = await this.request("/v2/meetings/import-audio", {
      method: "POST",
      body: form,
      signal,
    });
    if (!body || typeof body !== "object") throw new ContractError("import response must be an object");
    const rawMeetingId = (body as { meeting_id?: unknown; meeting?: { id?: unknown } }).meeting_id
      ?? (body as { meeting?: { id?: unknown } }).meeting?.id;
    const job = parseImportJob((body as { import_job?: unknown; job?: unknown }).import_job
      ?? (body as { job?: unknown }).job);
    const meetingId = typeof rawMeetingId === "string" && rawMeetingId.trim()
      ? rawMeetingId.trim()
      : job?.meetingId ?? null;
    if (!meetingId && !job?.id) throw new ContractError("import response is missing meeting_id and job_id");
    return { meetingId, job };
  }

  async retryImportJob(meetingId: string, signal?: AbortSignal): Promise<ImportJob> {
    const body = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/import-job/retry`,
      { method: "POST", body: JSON.stringify({}), signal },
    );
    const job = parseImportJob(
      (body as { import_job?: unknown; job?: unknown }).import_job
        ?? (body as { job?: unknown }).job,
    );
    if (!job) throw new ContractError("retry import response is missing import_job");
    return job;
  }

  async updateMeetingTitle(meetingId: string, title: string, signal?: AbortSignal): Promise<void> {
    await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title: title.trim() }),
      signal,
    });
  }

  async deleteMeeting(
    meetingId: string,
    scopeOrSignal: DataDeletionScope | AbortSignal = "all",
    signal?: AbortSignal,
  ): Promise<void> {
    const scope = typeof scopeOrSignal === "string" ? scopeOrSignal : "all";
    const requestSignal = typeof scopeOrSignal === "string" ? signal : scopeOrSignal;
    const query = new URLSearchParams({ scope });
    await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}?${query}`, {
      method: "DELETE",
      signal: requestSignal,
    });
  }

  async getDataGovernanceSettings(signal?: AbortSignal): Promise<DataGovernanceSettings> {
    return parseDataGovernanceSettings(await this.request("/v2/data-governance/settings", { signal }));
  }

  async updateDataGovernanceSettings(
    retentionPolicy: DataRetentionPolicy,
    signal?: AbortSignal,
  ): Promise<DataGovernanceSettings> {
    const body = await this.request("/v2/data-governance/settings", {
      method: "PATCH",
      body: JSON.stringify({ retention_policy: retentionPolicy }),
      signal,
    });
    return parseDataGovernanceSettings(body);
  }

  async listMeetings(signal?: AbortSignal): Promise<MeetingHistory> {
    const meetings = new Map<string, MeetingHistory["meetings"][number]>();
    let beforeUpdatedAtMs: number | null = null;
    let beforeMeetingId: string | null = null;
    for (;;) {
      const query = new URLSearchParams({ limit: "100", status: "all" });
      if (beforeUpdatedAtMs !== null && beforeMeetingId) {
        query.set("before_updated_at_ms", String(beforeUpdatedAtMs));
        query.set("before_meeting_id", beforeMeetingId);
      }
      const body = await this.request(`/v2/meetings?${query}`, { signal });
      const page = parseMeetingHistory(body);
      for (const meeting of page.meetings) meetings.set(meeting.meetingId, meeting);
      const source = body && typeof body === "object" && !Array.isArray(body)
        ? body as { has_more?: unknown; next_cursor?: unknown }
        : {};
      if (source.has_more !== true) break;
      const cursor = source.next_cursor && typeof source.next_cursor === "object" && !Array.isArray(source.next_cursor)
        ? source.next_cursor as { before_updated_at_ms?: unknown; before_meeting_id?: unknown }
        : null;
      const nextTimestamp = typeof cursor?.before_updated_at_ms === "number" ? cursor.before_updated_at_ms : null;
      const nextMeetingId = typeof cursor?.before_meeting_id === "string" ? cursor.before_meeting_id : null;
      if (nextTimestamp === null || !nextMeetingId ||
          (nextTimestamp === beforeUpdatedAtMs && nextMeetingId === beforeMeetingId)) {
        throw new ContractError("meeting history cursor did not advance");
      }
      beforeUpdatedAtMs = nextTimestamp;
      beforeMeetingId = nextMeetingId;
    }
    return { meetings: [...meetings.values()] };
  }

  async listMeetingsPage(
    options: MeetingHistoryQuery = {},
    signal?: AbortSignal,
  ): Promise<MeetingHistoryPage> {
    const query = new URLSearchParams({
      limit: String(Math.max(1, Math.min(100, Math.trunc(options.limit ?? 12)))),
      status: options.status ?? "all",
    });
    const normalizedQuery = options.query?.trim();
    if (normalizedQuery) query.set("query", normalizedQuery);
    if (options.cursor) {
      query.set("before_updated_at_ms", String(Math.max(0, Math.trunc(options.cursor.beforeUpdatedAtMs))));
      query.set("before_meeting_id", options.cursor.beforeMeetingId);
    }
    return parseMeetingHistoryPage(await this.request(`/v2/meetings?${query}`, { signal }));
  }

  async getSnapshot(meetingId: string, signal?: AbortSignal): Promise<MeetingSnapshot> {
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`, { signal });
    return parseMeetingSnapshot(body);
  }

  async getTranscript(meetingId: string, signal?: AbortSignal): Promise<TranscriptSegment[]> {
    const byId = new Map<string, TranscriptSegment>();
    let cursor = 0;
    for (;;) {
      const query = new URLSearchParams({
        after_transcript_seq: String(cursor),
        limit: "500",
      });
      const body = await this.request(
        `/v2/meetings/${encodeURIComponent(meetingId)}/transcript?${query}`,
        { signal },
      );
      const page = parseTranscriptPage(body);
      for (const segment of page.segments) byId.set(segment.segmentId, segment);
      if (!page.hasMore) break;
      if (page.nextAfterTranscriptSeq <= cursor) {
        throw new ContractError("transcript cursor did not advance");
      }
      cursor = page.nextAfterTranscriptSeq;
    }
    return [...byId.values()].sort((a, b) => a.transcriptSeq - b.transcriptSeq);
  }

  async getSpeakers(meetingId: string, signal?: AbortSignal): Promise<MeetingSpeaker[]> {
    const body = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/speakers`,
      { signal },
    );
    return parseMeetingSpeakers(body);
  }

  async renameSpeaker(
    meetingId: string,
    speakerId: string,
    speakerLabel: string,
    signal?: AbortSignal,
  ): Promise<MeetingSpeaker> {
    const body = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/speakers/${encodeURIComponent(speakerId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ speaker_label: speakerLabel.trim() }),
        signal,
      },
    );
    if (!body || typeof body !== "object" || Array.isArray(body) || !("speaker" in body)) {
      throw new ContractError("rename speaker response is missing speaker");
    }
    return parseMeetingSpeaker((body as { speaker: unknown }).speaker, meetingId);
  }

  async getEvents(meetingId: string, afterSeq: number, signal?: AbortSignal): Promise<EventsPage> {
    const query = new URLSearchParams({ after_seq: String(Math.max(0, Math.trunc(afterSeq))) });
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/events?${query}`, { signal });
    return parseEventsPage(body);
  }

  async getAudio(meetingId: string, signal?: AbortSignal): Promise<MeetingAudioWithTracks> {
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/audio`, { signal });
    const audio = parseMeetingAudio(body);
    return {
      ...audio,
      playbackUrl: audio.playbackUrl ? endpoint(this.baseUrl, audio.playbackUrl) : null,
      trackStates: audio.trackStates.map((track) => ({
        ...track,
        playbackUrl: track.playbackUrl ? endpoint(this.baseUrl, track.playbackUrl) : null,
      })),
      derivedAssets: audio.derivedAssets.map((asset) => ({
        ...asset,
        playbackUrl: asset.playbackUrl ? endpoint(this.baseUrl, asset.playbackUrl) : null,
      })),
      mixedCreateUrl: audio.mixedCreateUrl ? endpoint(this.baseUrl, audio.mixedCreateUrl) : null,
    };
  }

  async createMixedAudio(meetingId: string, signal?: AbortSignal): Promise<MeetingAudioDerivedAsset> {
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/audio/mixed`, {
      method: "POST",
      body: JSON.stringify({}),
      signal,
    });
    if (!body || typeof body !== "object" || Array.isArray(body) || !("asset" in body)) {
      throw new ContractError("mixed audio response is missing asset");
    }
    const asset = parseMeetingAudioDerivedAsset((body as { asset: unknown }).asset);
    return {
      ...asset,
      playbackUrl: asset.playbackUrl ? endpoint(this.baseUrl, asset.playbackUrl) : null,
    };
  }

  async exportMeeting(
    meetingId: string,
    format: MeetingExportFormat,
    signal?: AbortSignal,
  ): Promise<void> {
    const query = new URLSearchParams({ format });
    const response = await fetch(
      endpoint(this.baseUrl, `/v2/meetings/${encodeURIComponent(meetingId)}/export?${query}`),
      {
        headers: {
          Accept: format === "json"
            ? "application/json"
            : format === "docx"
              ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              : "text/markdown",
        },
        signal,
      },
    );
    if (!response.ok) {
      const body = await responseBody(response);
      throw new ApiError(response.status, errorMessage(response.status, body), body);
    }
    const fallback = `${meetingId}.meeting.${format === "markdown" ? "md" : format}`;
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? fallback;
    const objectUrl = URL.createObjectURL(await response.blob());
    try {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  async exportDiagnosticBundle(signal?: AbortSignal): Promise<void> {
    const response = await fetch(endpoint(this.baseUrl, "/v2/diagnostics/bundle"), {
      headers: { Accept: "application/zip" },
      signal,
    });
    if (!response.ok) {
      const body = await responseBody(response);
      throw new ApiError(response.status, errorMessage(response.status, body), body);
    }
    const disposition = response.headers.get("content-disposition") ?? "";
    const filename = disposition.match(/filename="([^"]+)"/)?.[1]
      ?? "meeting-copilot-diagnostics.zip";
    const objectUrl = URL.createObjectURL(await response.blob());
    try {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  }

  async saveReviewDocument(
    meetingId: string,
    kind: ReviewDocumentKind,
    expectedRevision: number,
    contentJson: unknown,
    signal?: AbortSignal,
  ): Promise<ReviewDocument> {
    const body = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/documents/${encodeURIComponent(kind)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: Math.max(0, Math.trunc(expectedRevision)),
          content_json: contentJson,
          version_source: "user_final",
        }),
        signal,
      },
    );
    const source = body && typeof body === "object" && "document" in body
      ? (body as { document: unknown }).document
      : body;
    return parseReviewDocument(source, kind, meetingId);
  }

  async getDocumentRevisions(
    meetingId: string,
    kind: ReviewDocumentKind,
    signal?: AbortSignal,
  ): Promise<ReviewDocumentRevision[]> {
    const body = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/documents/${encodeURIComponent(kind)}/revisions`,
      { signal },
    );
    return parseReviewDocumentRevisions(body);
  }

  async regenerateDocument(meetingId: string, kind: ReviewDocumentKind, signal?: AbortSignal): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/documents/${encodeURIComponent(kind)}/regenerate`,
      { method: "POST", body: JSON.stringify({ preserve_user_final: true }), signal },
    );
  }

  async retryReviewJob(meetingId: string, kind: ReviewJobKind, signal?: AbortSignal): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/jobs/${encodeURIComponent(kind)}/retry`,
      { method: "POST", body: JSON.stringify({ use_current_transcript_revision: true }), signal },
    );
  }

  async retryTranscriptCorrection(meetingId: string, signal?: AbortSignal): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/transcript/correction/retry`,
      { method: "POST", body: JSON.stringify({}), signal },
    );
  }

  async endMeeting(meetingId: string, signal?: AbortSignal): Promise<void> {
    await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/end`, {
      method: "POST",
      body: JSON.stringify({ action: "end_and_review" }),
      signal,
    });
  }

  async saveSuggestionFeedback(
    meetingId: string,
    suggestionId: string,
    feedback: SuggestionFeedback,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/suggestions/${encodeURIComponent(suggestionId)}/feedback`,
      {
        method: "PUT",
        body: JSON.stringify({ feedback }),
        signal,
      },
    );
  }

  async requestRealtimeCoach(
    meetingId: string,
    request: string,
    answerId: string | null = null,
    signal?: AbortSignal,
    options?: ContributionRequestOptions,
  ): Promise<string> {
    const idempotencyKey = typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const response = await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/coach/request`,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify({ request, answer_id: answerId, ...(options ? {
          intent: options.intent,
          ...(options.topicId ? { topic_id: options.topicId } : {}),
          ...(options.expectedBasisRevision !== undefined ? { expected_basis_revision: options.expectedBasisRevision } : {}),
          ...(options.selectedSegmentIds?.length ? { selected_segment_ids: options.selectedSegmentIds } : {}),
        } : {}) }),
        signal,
      },
    );
    const jobId = (response as { job?: { id?: string } }).job?.id;
    if (!jobId) throw new Error("请求已提交，但未返回任务编号，请刷新会议查看结果");
    return jobId;
  }

  async getCoachRequestOutcome(meetingId: string, jobId: string, signal?: AbortSignal): Promise<CoachRequestOutcome> {
    const snapshot = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`, { signal });
    return coachRequestOutcomeFromSnapshot(meetingId, jobId, snapshot);
  }

  async getCoachRequestStatus(meetingId: string, jobId: string, signal?: AbortSignal): Promise<"pending" | "succeeded" | "failed" | "timed_out" | "no_change"> {
    const snapshot = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`, { signal }) as {
      jobs?: Array<{ id: string; status: string }>;
      coach_history?: Array<{ job_id?: string; status?: string }>;
      coach_decision?: { job_id?: string; status?: string };
      diagnostics?: { coach_runtime_history?: Array<{ job_id?: string; status?: string; outcome?: string }> };
    };
    const decision = snapshot.coach_decision?.job_id === jobId ? snapshot.coach_decision : null;
    if (decision?.status === "timed_out") return "timed_out";
    if (decision?.status === "failed") return "failed";
    if (snapshot.coach_history?.some((item) => item.job_id === jobId && item.status === "intervention")) return "succeeded";
    const result = snapshot.diagnostics?.coach_runtime_history?.find((item) => item.job_id === jobId);
    if (result?.status === "timed_out") return "timed_out";
    if (result?.outcome === "failure" || result?.status === "failed") return "failed";
    const job = snapshot.jobs?.find((item) => item.id === jobId);
    if (!job) return "failed";
    if (job?.status === "succeeded") return "no_change";
    if (["failed", "cancelled"].includes(job?.status ?? "")) return "failed";
    return "pending";
  }

  async saveFactStatus(
    meetingId: string,
    factType: MeetingFactKind,
    factId: string,
    status: MeetingFactStatus,
    signal?: AbortSignal,
  ): Promise<void> {
    if (!(["decision", "action_item", "risk", "open_question"] as MeetingFactKind[]).includes(factType)) {
      throw new ContractError("unsupported meeting fact type");
    }
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/entities/${encodeURIComponent(factId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ status }),
        signal,
      },
    );
  }

  async getLocalCapabilities(signal?: AbortSignal): Promise<LocalCapabilityStatus> {
    return parseLocalCapabilityStatus(await this.request("/v2/local-capabilities", { signal }));
  }

  async importLocalCapabilityPackage(
    file: File,
    signal?: AbortSignal,
  ): Promise<LocalCapabilityStatus> {
    const form = new FormData();
    form.append("file", file, file.name);
    return parseLocalCapabilityStatus(await this.request("/v2/local-capabilities/import", {
      method: "POST",
      body: form,
      signal,
    }));
  }

  async updateFact(
    meetingId: string,
    factId: string,
    changes: { text: string; owner?: string | null; deadline?: string | null; mitigation?: string | null },
    expectedVersion: number,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/entities/${encodeURIComponent(factId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ ...changes, expected_version: expectedVersion }),
        signal,
      },
    );
  }

  async mergeFacts(
    meetingId: string,
    targetFactId: string,
    sourceFactId: string,
    expectedTargetVersion: number,
    expectedSourceVersion: number,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/entities/${encodeURIComponent(targetFactId)}/merge`,
      {
        method: "POST",
        body: JSON.stringify({
          source_entity_id: sourceFactId,
          expected_target_version: expectedTargetVersion,
          expected_source_version: expectedSourceVersion,
        }),
        signal,
      },
    );
  }

  async markCoachUiRendered(receipt: CoachUiRenderReceipt, signal?: AbortSignal): Promise<void> {
    const body = coachUiRenderReceiptWire(receipt);
    if (!parseCoachUiRenderReceipt(body)) throw new ContractError("建议渲染回执身份无效");
    await this.request(`/v2/traces/${encodeURIComponent(receipt.jobId)}/ui-rendered`, {
      method: "POST", body: JSON.stringify(body), signal,
    });
  }

  async markUiRendered(
    jobId: string,
    eventSeq: number,
    draftSeq: number,
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(`/v2/traces/${encodeURIComponent(jobId)}/ui-rendered`, {
      method: "POST",
      body: JSON.stringify({
        event_seq: Math.max(0, Math.trunc(eventSeq)),
        draft_seq: Math.max(0, Math.trunc(draftSeq)),
      }),
      signal,
    });
  }

  async getMeetingPreparation(meetingId: string, signal?: AbortSignal): Promise<MeetingPreparationSnapshot> {
    return parseMeetingPreparation(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/preparation`,
      { signal },
    ));
  }

  async getMeetingPreparationVersions(meetingId: string, signal?: AbortSignal): Promise<MeetingPreparationSnapshot[]> {
    const body = apiRecord(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/preparation/versions`,
      { signal },
    ), "meeting preparation versions");
    if (!Array.isArray(body.versions)) throw new ContractError("meeting preparation versions must be an array");
    return body.versions.map(parseMeetingPreparation);
  }

  async getChapters(meetingId: string, query = "", signal?: AbortSignal): Promise<MeetingChapter[]> {
    const params = new URLSearchParams();
    if (query.trim()) params.set("query", query.trim());
    const body = apiRecord(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/chapters${params.size ? `?${params}` : ""}`,
      { signal },
    ), "chapters response");
    if (!Array.isArray(body.chapters)) throw new ContractError("chapters must be an array");
    return body.chapters.map((raw) => {
      const item = apiRecord(raw, "chapter");
      return {
        chapterId: apiString(item.chapter_id, "chapter_id"),
        index: typeof item.index === "number" ? item.index : 0,
        title: apiString(item.title, "chapter title"),
        text: typeof item.text === "string" ? item.text : "",
        startMs: typeof item.start_ms === "number" ? item.start_ms : null,
        endMs: typeof item.end_ms === "number" ? item.end_ms : null,
        paragraphIds: Array.isArray(item.paragraph_ids) ? item.paragraph_ids.map(String) : [],
        evidenceSegmentIds: Array.isArray(item.evidence_segment_ids) ? item.evidence_segment_ids.map(String) : [],
      };
    });
  }

  async getAskThreads(meetingId: string, signal?: AbortSignal): Promise<AskAiThread[]> {
    const body = apiRecord(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/ask/threads`,
      { signal },
    ), "Ask AI threads response");
    if (!Array.isArray(body.threads)) throw new ContractError("threads must be an array");
    return body.threads.map((raw) => {
      const item = apiRecord(raw, "Ask AI thread");
      return {
        threadId: apiString(item.thread_id, "thread_id"),
        title: apiString(item.title, "thread title"),
        createdAtMs: typeof item.created_at_ms === "number" ? item.created_at_ms : 0,
        updatedAtMs: typeof item.updated_at_ms === "number" ? item.updated_at_ms : 0,
        messages: Array.isArray(item.messages) ? item.messages.map(parseAskMessage) : [],
      };
    });
  }

  async listNotes(
    query: { meetingId?: string | null; status?: MeetingNoteStatus | "all"; query?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<MeetingNote[]> {
    const params = new URLSearchParams();
    if (query.meetingId) params.set("meeting_id", query.meetingId);
    if (query.status) params.set("status", query.status);
    if (query.query?.trim()) params.set("query", query.query.trim());
    if (query.limit) params.set("limit", String(query.limit));
    const body = apiRecord(await this.request(`/v2/notes${params.size ? `?${params}` : ""}`, { signal }), "notes response");
    if (!Array.isArray(body.notes)) throw new ContractError("notes must be an array");
    return body.notes.map(parseMeetingNote);
  }

  async listNotesPage(query: { meetingId?: string | null; status?: MeetingNoteStatus | "all"; query?: string; limit?: number; cursor?: string | null } = {}, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: String(query.limit ?? 50) });
    if (query.meetingId) params.set("meeting_id", query.meetingId);
    if (query.status) params.set("status", query.status);
    if (query.query?.trim()) params.set("query", query.query.trim());
    if (query.cursor) params.set("cursor", query.cursor);
    const body = apiRecord(await this.request(`/v2/notes/page?${params}`, { signal }), "notes page");
    if (!Array.isArray(body.items) || typeof body.has_more !== "boolean") throw new ContractError("笔记分页格式无效");
    return { items: body.items.map(parseMeetingNote), nextCursor: typeof body.next_cursor === "string" ? body.next_cursor : null, hasMore: body.has_more };
  }

  async getNote(noteId: string, signal?: AbortSignal) { return parseMeetingNote(apiRecord(await this.request(`/v2/notes/${encodeURIComponent(noteId)}`, { signal }), "note").note); }

  async createNote(
    meetingId: string,
    input: {
      title?: string;
      body: string;
      sourceKind: MeetingNoteSourceKind;
      sourceMessageId?: string | null;
      evidence?: Array<Omit<MeetingNoteEvidence, "ordinal" | "meetingId"> & { meetingId?: string | null }>;
    },
    signal?: AbortSignal,
  ): Promise<MeetingNote> {
    const body = apiRecord(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/notes`,
      {
        method: "POST",
        body: JSON.stringify({
          title: input.title ?? "",
          body: input.body,
          source_kind: input.sourceKind,
          source_message_id: input.sourceMessageId || undefined,
          evidence: noteEvidencePayload(input.evidence ?? []),
        }),
        signal,
      },
    ), "create note response");
    return parseMeetingNote(body.note);
  }

  async updateNote(
    noteId: string,
    expectedVersion: number,
    changes: { title?: string; body?: string; status?: MeetingNoteStatus },
    signal?: AbortSignal,
  ): Promise<MeetingNote> {
    const body = apiRecord(await this.request(
      `/v2/notes/${encodeURIComponent(noteId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ expected_version: expectedVersion, ...changes }),
        signal,
      },
    ), "update note response");
    return parseMeetingNote(body.note);
  }

  async deleteNote(noteId: string, expectedVersion: number, signal?: AbortSignal): Promise<MeetingNote> {
    const params = new URLSearchParams({ expected_version: String(expectedVersion) });
    const body = apiRecord(await this.request(
      `/v2/notes/${encodeURIComponent(noteId)}?${params}`,
      { method: "DELETE", signal },
    ), "delete note response");
    return parseMeetingNote(body.note);
  }

  async addNoteEvidence(
    noteId: string,
    evidence: Array<Omit<MeetingNoteEvidence, "ordinal" | "meetingId"> & { meetingId?: string | null }>,
    signal?: AbortSignal,
  ): Promise<MeetingNote> {
    const body = apiRecord(await this.request(
      `/v2/notes/${encodeURIComponent(noteId)}/evidence`,
      { method: "POST", body: JSON.stringify({ evidence: noteEvidencePayload(evidence) }), signal },
    ), "add note evidence response");
    return parseMeetingNote(body.note);
  }

  async deleteNoteEvidence(noteId: string, ordinal: number, signal?: AbortSignal): Promise<MeetingNote> {
    const body = apiRecord(await this.request(
      `/v2/notes/${encodeURIComponent(noteId)}/evidence/${ordinal}`,
      { method: "DELETE", signal },
    ), "delete note evidence response");
    return parseMeetingNote(body.note);
  }

  async createMeetingEntity(
    meetingId: string,
    input: {
      kind: "decision_candidate" | "action_item" | "risk" | "open_question";
      text: string;
      sourceMessageId?: string | null;
      evidence?: AskAiMessage["evidence"];
      sourceEvidence?: DocumentSourceEvidence[];
    },
    signal?: AbortSignal,
  ): Promise<void> {
    await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/entities`,
      {
        method: "POST",
        body: JSON.stringify({
          kind: input.kind,
          text: input.text,
          source_message_id: input.sourceMessageId || undefined,
          source_evidence: (input.sourceEvidence ?? []).map(serializeDocumentSource),
          evidence: (input.evidence ?? []).filter(isTranscriptEvidence).map((item) => ({
            segment_id: item.segmentId,
            transcript_seq: item.transcriptSeq,
            start_ms: item.startMs,
            end_ms: item.endMs,
            quote: item.quote,
          })),
        }),
        signal,
      },
    );
  }

  async askMeeting(
    meetingId: string,
    input: {
      question: string;
      scope: AskAiScope;
      threadId?: string | null;
      segmentIds?: string[];
      chapterId?: string | null;
      recentMinutes?: 1 | 3 | 5 | 10;
      intent?: "catch_up";
      includeKnowledge?: boolean;
    },
    onDelta: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<{ threadId: string; message: AskAiMessage }> {
    const response = await fetch(endpoint(
      this.baseUrl,
      `/v2/meetings/${encodeURIComponent(meetingId)}/ask/stream`,
    ), {
      method: "POST",
      headers: { Accept: "application/x-ndjson", "Content-Type": "application/json" },
      body: JSON.stringify({
        question: input.question,
        scope: input.scope,
        thread_id: input.threadId || undefined,
        segment_ids: input.segmentIds,
        chapter_id: input.chapterId || undefined,
        recent_minutes: input.recentMinutes,
        intent: input.intent,
        include_knowledge: input.includeKnowledge,
      }),
      signal,
    });
    if (!response.ok) {
      const body = await responseBody(response);
      throw new ApiError(response.status, errorMessage(response.status, body), body);
    }
    if (!response.body) throw new ContractError("Ask AI stream is unavailable");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let threadId = input.threadId ?? "";
    let completed: AskAiMessage | null = null;
    let streamError: Error | null = null;
    const consumeLine = (line: string) => {
      if (!line.trim()) return;
      const event = apiRecord(JSON.parse(line), "Ask AI event");
      if (event.type === "started") threadId = apiString(event.thread_id, "thread_id");
      if (event.type === "delta" && typeof event.text === "string") onDelta(event.text);
      if (event.type === "done") {
        threadId = apiString(event.thread_id, "thread_id");
        completed = parseAskMessage(event.message);
      }
      if (event.type === "error") {
        streamError = new Error(typeof event.error === "string" ? event.error : "AI 回答失败");
      }
    };
    for (;;) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      lines.forEach(consumeLine);
      if (done) break;
    }
    consumeLine(buffer);
    if (streamError) throw streamError;
    if (!completed || !threadId) throw new ContractError("Ask AI stream ended without a completed message");
    return { threadId, message: completed };
  }

  async pinAskMessage(
    meetingId: string,
    messageId: string,
    pinnedKind: AskAiMessage["pinnedKind"],
    signal?: AbortSignal,
  ): Promise<AskAiMessage> {
    const body = apiRecord(await this.request(
      `/v2/meetings/${encodeURIComponent(meetingId)}/ask/messages/${encodeURIComponent(messageId)}`,
      { method: "PATCH", body: JSON.stringify({ pinned_kind: pinnedKind }), signal },
    ), "Ask AI message response");
    return parseAskMessage(body.message);
  }
}

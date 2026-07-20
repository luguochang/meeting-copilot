import type {
  DataDeletionScope,
  DataGovernanceSettings,
  DataRetentionPolicy,
  EventsPage,
  MeetingFactKind,
  MeetingFactStatus,
  MeetingHistory,
  MeetingHistoryCursor,
  MeetingHistoryPage,
  ImportJob,
  MeetingInputSource,
  MeetingPreparationInput,
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
  parseImportJob,
  parseProviderStatus,
  parseReviewDocument,
  parseReviewDocumentRevisions,
  parseTranscriptPage,
  type ProviderStatus,
} from "./schema";

export interface MeetingApi {
  createMeeting(
    meetingId: string,
    title?: string | null,
    inputSourceOrSignal?: MeetingInputSource | AbortSignal,
    signal?: AbortSignal,
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
  endMeeting(meetingId: string, signal?: AbortSignal): Promise<void>;
  saveSuggestionFeedback(
    meetingId: string,
    suggestionId: string,
    feedback: SuggestionFeedback,
    signal?: AbortSignal,
  ): Promise<void>;
  saveFactStatus(
    meetingId: string,
    factType: MeetingFactKind,
    factId: string,
    status: MeetingFactStatus,
    signal?: AbortSignal,
  ): Promise<void>;
  markUiRendered(
    jobId: string,
    eventSeq: number,
    draftSeq: number,
    signal?: AbortSignal,
  ): Promise<void>;
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

export class HttpMeetingApi implements MeetingApi {
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
  ): Promise<void> {
    const inputSource = typeof inputSourceOrSignal === "string" ? inputSourceOrSignal : "microphone";
    const requestSignal = typeof inputSourceOrSignal === "string" ? signal : inputSourceOrSignal;
    await this.request("/v2/meetings", {
      method: "POST",
      body: JSON.stringify({
        meeting_id: meetingId,
        expected_duration_seconds: 3_600,
        track_count: inputSource === "dual_track" ? 2 : 1,
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

  async saveFactStatus(
    meetingId: string,
    factType: MeetingFactKind,
    factId: string,
    status: MeetingFactStatus,
    signal?: AbortSignal,
  ): Promise<void> {
    if (!(["decision", "action_item", "risk"] as MeetingFactKind[]).includes(factType)) {
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
}

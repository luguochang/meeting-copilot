import type {
  EventsPage,
  MeetingAudio,
  MeetingHistory,
  MeetingSnapshot,
  SuggestionFeedback,
  TranscriptSegment,
} from "../domain/events";
import {
  ContractError,
  parseEventsPage,
  parseMeetingAudio,
  parseMeetingHistory,
  parseMeetingSnapshot,
  parseTranscriptPage,
} from "./schema";

export interface MeetingApi {
  createMeeting(meetingId: string, signal?: AbortSignal): Promise<void>;
  importRecording(file: File, signal?: AbortSignal): Promise<{ meetingId: string }>;
  deleteMeeting(meetingId: string, signal?: AbortSignal): Promise<void>;
  listMeetings(signal?: AbortSignal): Promise<MeetingHistory>;
  getSnapshot(meetingId: string, signal?: AbortSignal): Promise<MeetingSnapshot>;
  getTranscript(meetingId: string, signal?: AbortSignal): Promise<TranscriptSegment[]>;
  getEvents(meetingId: string, afterSeq: number, signal?: AbortSignal): Promise<EventsPage>;
  getAudio(meetingId: string, signal?: AbortSignal): Promise<MeetingAudio>;
  endMeeting(meetingId: string, signal?: AbortSignal): Promise<void>;
  saveSuggestionFeedback(
    meetingId: string,
    suggestionId: string,
    feedback: SuggestionFeedback,
    signal?: AbortSignal,
  ): Promise<void>;
  markUiRendered(
    jobId: string,
    eventSeq: number,
    draftSeq: number,
    signal?: AbortSignal,
  ): Promise<void>;
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

  async createMeeting(meetingId: string, signal?: AbortSignal): Promise<void> {
    await this.request("/v2/meetings", {
      method: "POST",
      body: JSON.stringify({
        meeting_id: meetingId,
        expected_duration_seconds: 3_600,
        track_count: 1,
      }),
      signal,
    });
  }

  async importRecording(file: File, signal?: AbortSignal): Promise<{ meetingId: string }> {
    const form = new FormData();
    form.append("file", file, file.name);
    const body = await this.request("/v2/meetings/import-audio", {
      method: "POST",
      body: form,
      signal,
    });
    if (!body || typeof body !== "object") throw new ContractError("import response must be an object");
    const rawMeetingId = (body as { meeting_id?: unknown; meeting?: { id?: unknown } }).meeting_id
      ?? (body as { meeting?: { id?: unknown } }).meeting?.id;
    if (typeof rawMeetingId !== "string" || !rawMeetingId.trim()) {
      throw new ContractError("import response is missing meeting_id");
    }
    return { meetingId: rawMeetingId.trim() };
  }

  async deleteMeeting(meetingId: string, signal?: AbortSignal): Promise<void> {
    await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}`, {
      method: "DELETE",
      signal,
    });
  }

  async listMeetings(signal?: AbortSignal): Promise<MeetingHistory> {
    return parseMeetingHistory(await this.request("/v2/meetings", { signal }));
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

  async getEvents(meetingId: string, afterSeq: number, signal?: AbortSignal): Promise<EventsPage> {
    const query = new URLSearchParams({ after_seq: String(Math.max(0, Math.trunc(afterSeq))) });
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/events?${query}`, { signal });
    return parseEventsPage(body);
  }

  async getAudio(meetingId: string, signal?: AbortSignal): Promise<MeetingAudio> {
    const body = await this.request(`/v2/meetings/${encodeURIComponent(meetingId)}/audio`, { signal });
    const audio = parseMeetingAudio(body);
    return {
      ...audio,
      playbackUrl: audio.playbackUrl ? endpoint(this.baseUrl, audio.playbackUrl) : null,
    };
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

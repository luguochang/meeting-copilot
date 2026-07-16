import type { EventsPage, MeetingEvent } from "../domain/events";
import { parseMeetingEvent } from "./schema";
import type { MeetingApi } from "./client";

export interface EventSubscription {
  meetingId: string;
  afterSeq: number;
  signal: AbortSignal;
  onEvents(events: MeetingEvent[]): void;
  onConnection(state: "connecting" | "live" | "reconnecting" | "offline", error?: string): void;
}

export interface MeetingEventTransport {
  readonly kind: "poll" | "sse";
  subscribe(subscription: EventSubscription): () => void;
}

const V2_NAMED_EVENT_TYPES = [
  "transcript.segment.finalized",
  "transcript.segment.revised",
  "transcript.segment.corrected",
  "transcript.segment.partial",
  "suggestion.draft.started",
  "suggestion.draft.delta",
  "suggestion.committed",
  "suggestion.superseded",
  "suggestion.evidence.remapped",
  "suggestion.feedback.updated",
  "meeting.topic.updated",
  "meeting.open_question.updated",
  "meeting.ended",
  "meeting.minutes.ready",
  "meeting.approach.ready",
  "meeting.index.ready",
  "recording.chunk.committed",
  "recording.sealed",
  "recording.interrupted",
  "recording.failed",
  "recording.export.queued",
  "recording.export.ready",
] as const;

function sseUrl(baseUrl: string, meetingId: string, afterSeq: number): string {
  const origin = window.location.origin;
  const normalizedBase = baseUrl.trim();
  const base = normalizedBase
    ? new URL(`${normalizedBase.replace(/\/+$/, "")}/`, origin)
    : new URL("/", origin);
  const url = new URL(`v2/meetings/${encodeURIComponent(meetingId)}/events`, base);
  url.searchParams.set("after_seq", String(Math.max(0, Math.trunc(afterSeq))));
  return url.toString();
}

export class PollingEventTransport implements MeetingEventTransport {
  readonly kind = "poll" as const;

  constructor(
    private readonly api: MeetingApi,
    private readonly intervalMs = 1_000,
  ) {}

  subscribe(subscription: EventSubscription): () => void {
    let stopped = false;
    let timer: number | undefined;
    let afterSeq = subscription.afterSeq;
    let failures = 0;

    const poll = async () => {
      if (stopped || subscription.signal.aborted) return;
      subscription.onConnection(failures > 0 ? "reconnecting" : "connecting");
      try {
        let page: EventsPage;
        do {
          page = await this.api.getEvents(subscription.meetingId, afterSeq, subscription.signal);
          if (stopped) return;
          const nextAfterSeq = Math.max(
            afterSeq,
            page.nextAfterSeq,
            page.lastSeq,
            ...page.events.map((event) => event.seq),
          );
          if (page.hasMore && nextAfterSeq <= afterSeq) {
            throw new Error("事件分页游标未前进");
          }
          afterSeq = nextAfterSeq;
          if (page.events.length) subscription.onEvents(page.events);
        } while (page.hasMore);
        failures = 0;
        subscription.onConnection("live");
      } catch (error) {
        if (subscription.signal.aborted || stopped) return;
        failures += 1;
        const message = error instanceof Error ? error.message : "事件连接失败";
        subscription.onConnection(failures >= 3 ? "offline" : "reconnecting", message);
      } finally {
        if (!stopped && !subscription.signal.aborted) {
          const retryDelay = failures ? Math.min(5_000, this.intervalMs * 2 ** failures) : this.intervalMs;
          timer = window.setTimeout(poll, retryDelay);
        }
      }
    };

    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }
}

/** Retained adapter for the continuous SSE endpoint introduced by Phase 1B. */
export class SseEventTransport implements MeetingEventTransport {
  readonly kind = "sse" as const;

  constructor(private readonly baseUrl = import.meta.env.VITE_API_BASE_URL ?? "") {}

  subscribe(subscription: EventSubscription): () => void {
    let stopped = false;
    let cursor = Math.max(0, Math.trunc(subscription.afterSeq));
    let eventSource: EventSource | null = null;
    let reconnectTimer: number | undefined;
    let failures = 0;

    const receive = (message: MessageEvent<string>) => {
      try {
        const parsed = JSON.parse(message.data) as unknown;
        const values = Array.isArray(parsed)
          ? parsed
          : parsed && typeof parsed === "object" && "events" in parsed && Array.isArray((parsed as { events: unknown }).events)
            ? (parsed as { events: unknown[] }).events
            : [parsed];
        const events = values.map(parseMeetingEvent).filter((event) => event.seq > cursor);
        if (!events.length) return;
        cursor = Math.max(cursor, ...events.map((event) => event.seq));
        subscription.onEvents(events);
      } catch (error) {
        subscription.onConnection("reconnecting", error instanceof Error ? error.message : "事件格式错误");
      }
    };

    const connect = () => {
      if (stopped || subscription.signal.aborted) return;
      subscription.onConnection(failures > 0 ? "reconnecting" : "connecting");
      const source = new EventSource(sseUrl(this.baseUrl, subscription.meetingId, cursor));
      eventSource = source;
      source.onopen = () => {
        failures = 0;
        subscription.onConnection("live");
      };
      source.onmessage = receive;
      for (const eventType of V2_NAMED_EVENT_TYPES) {
        source.addEventListener(eventType, receive as EventListener);
      }
      source.onerror = () => {
        if (stopped || subscription.signal.aborted || eventSource !== source) return;
        failures += 1;
        source.close();
        eventSource = null;
        subscription.onConnection("reconnecting", "实时事件连接正在恢复");
        reconnectTimer = window.setTimeout(connect, Math.min(5_000, 500 * 2 ** failures));
      };
    };

    const abort = () => {
      stopped = true;
      eventSource?.close();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    };
    subscription.signal.addEventListener("abort", abort, { once: true });
    connect();
    return () => {
      stopped = true;
      subscription.signal.removeEventListener("abort", abort);
      eventSource?.close();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    };
  }
}

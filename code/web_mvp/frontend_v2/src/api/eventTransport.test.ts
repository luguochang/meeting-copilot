import { PollingEventTransport, SseEventTransport } from "./eventTransport";

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(url: string | URL) {
    super();
    this.url = String(url);
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  emit(type: string, data: unknown) {
    this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) }));
  }

  fail() {
    this.onerror?.(new Event("error"));
  }
}

function suggestionEvent(seq: number) {
  return {
    meeting_id: "meeting/1",
    seq,
    event_id: `event-${seq}`,
    type: "suggestion.committed",
    aggregate_type: "suggestion",
    aggregate_id: "suggestion-1",
    occurred_at_ms: 1_000,
    correlation_id: "generation-1",
    causation_id: "job-1",
    idempotency_key: `suggestion.committed:suggestion-1:${seq}`,
    payload: {},
    published_at_ms: null,
  };
}

function eventPage(afterSeq: number, seqs: number[], hasMore = false) {
  return {
    meetingId: "meeting-1",
    afterSeq,
    lastSeq: seqs.at(-1) ?? afterSeq,
    events: seqs.map(suggestionEvent),
    hasMore,
    nextAfterSeq: seqs.at(-1) ?? afterSeq,
  };
}

describe("PollingEventTransport", () => {
  it("drains bounded event pages before returning to the polling interval", async () => {
    vi.useFakeTimers();
    const getEvents = vi.fn()
      .mockResolvedValueOnce(eventPage(0, [1, 2], true))
      .mockResolvedValueOnce(eventPage(2, [3], false));
    const events = vi.fn();
    const transport = new PollingEventTransport({ getEvents } as never, 1_000);
    const stop = transport.subscribe({
      meetingId: "meeting-1",
      afterSeq: 0,
      signal: new AbortController().signal,
      onEvents: events,
      onConnection: vi.fn(),
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(getEvents).toHaveBeenCalledTimes(2);
    expect(events).toHaveBeenNthCalledWith(1, expect.arrayContaining([
      expect.objectContaining({ seq: 1 }),
      expect.objectContaining({ seq: 2 }),
    ]));
    expect(events).toHaveBeenNthCalledWith(2, [expect.objectContaining({ seq: 3 })]);
    stop();
    vi.useRealTimers();
  });
});

describe("SseEventTransport", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  it("consumes backend named events and encodes the initial after_seq URL", () => {
    const events = vi.fn();
    const controller = new AbortController();
    const transport = new SseEventTransport("http://127.0.0.1:8767/");

    const stop = transport.subscribe({
      meetingId: "meeting/1",
      afterSeq: 7.9,
      signal: controller.signal,
      onEvents: events,
      onConnection: vi.fn(),
    });

    expect(FakeEventSource.instances[0].url).toBe(
      "http://127.0.0.1:8767/v2/meetings/meeting%2F1/events?after_seq=7",
    );
    FakeEventSource.instances[0].emit("suggestion.committed", suggestionEvent(8));
    expect(events).toHaveBeenCalledWith([expect.objectContaining({ seq: 8, type: "suggestion.committed" })]);
    FakeEventSource.instances[0].emit("suggestion.superseded", {
      ...suggestionEvent(9),
      type: "suggestion.superseded",
    });
    expect(events).toHaveBeenLastCalledWith([
      expect.objectContaining({ seq: 9, type: "suggestion.superseded" }),
    ]);
    FakeEventSource.instances[0].emit("suggestion.evidence.remapped", {
      ...suggestionEvent(10),
      type: "suggestion.evidence.remapped",
    });
    expect(events).toHaveBeenLastCalledWith([
      expect.objectContaining({ seq: 10, type: "suggestion.evidence.remapped" }),
    ]);
    FakeEventSource.instances[0].emit("recording.export.ready", {
      ...suggestionEvent(11),
      type: "recording.export.ready",
      aggregate_type: "recording_export",
      aggregate_id: "export-1",
    });
    expect(events).toHaveBeenLastCalledWith([
      expect.objectContaining({ seq: 11, type: "recording.export.ready" }),
    ]);
    stop();
    expect(FakeEventSource.instances[0].closed).toBe(true);
  });

  it("reconnects with the latest consumed sequence and deduplicates replay", async () => {
    vi.useFakeTimers();
    const events = vi.fn();
    const transport = new SseEventTransport();
    const stop = transport.subscribe({
      meetingId: "meeting-1",
      afterSeq: 2,
      signal: new AbortController().signal,
      onEvents: events,
      onConnection: vi.fn(),
    });

    const first = FakeEventSource.instances[0];
    first.emit("suggestion.committed", suggestionEvent(5));
    first.emit("suggestion.committed", suggestionEvent(5));
    expect(events).toHaveBeenCalledTimes(1);
    first.fail();
    await vi.advanceTimersByTimeAsync(1_000);

    expect(FakeEventSource.instances).toHaveLength(2);
    expect(new URL(FakeEventSource.instances[1].url).searchParams.get("after_seq")).toBe("5");
    stop();
    vi.useRealTimers();
  });
});

import { describe, expect, it } from "vitest";
import type { MeetingEvent, TranscriptSegment } from "./events";
import { createInitialMeetingState, meetingReducer } from "./reducer";

function segment(overrides: Partial<TranscriptSegment> = {}): TranscriptSegment {
  return {
    meetingId: "meeting-1",
    segmentId: "segment-1",
    finalId: "final-1",
    transcriptSeq: 1,
    text: "原始文字事实。",
    normalizedText: "AI 修正后的文字事实。",
    startedAtMs: 100,
    endedAtMs: 900,
    revision: 3,
    evidenceHash: "evidence-3",
    speakerId: "speaker-a",
    speakerLabel: "发言人 1",
    speakerConfidence: 0.82,
    speakerAttributionRevision: 1,
    speakerAttributionSource: "diarization",
    speakerAttributionReason: "initial",
    createdAtMs: 1_000,
    updatedAtMs: 1_100,
    ...overrides,
  };
}

function speakerRevision(
  seq: number,
  attributionRevision: number,
  overrides: Record<string, unknown> = {},
): MeetingEvent {
  return {
    meetingId: "meeting-1",
    seq,
    eventId: `speaker-event-${seq}`,
    type: "transcript.segment.speaker_revised",
    aggregateType: "transcript_segment",
    aggregateId: "segment-1",
    occurredAtMs: 2_000 + seq,
    correlationId: "run-1",
    causationId: null,
    idempotencyKey: `speaker:segment-1:${attributionRevision}:${seq}`,
    payload: {
      meeting_id: "meeting-1",
      segment_id: "segment-1",
      attribution_revision: attributionRevision,
      run_id: "run-1",
      speaker_id: "speaker-b",
      speaker_label: "发言人 2",
      speaker_confidence: 0.94,
      source: "diarization",
      reason: "reclustered",
      ...overrides,
    },
    publishedAtMs: null,
  };
}

function stateWithSegment() {
  const initial = createInitialMeetingState("meeting-1");
  const original = segment();
  return {
    ...initial,
    lastSeq: 1,
    segments: [original],
    fullTranscript: [original],
  };
}

describe("speaker revision projection", () => {
  it("patches only speaker projection fields without adding transcript entries", () => {
    const original = stateWithSegment();
    const updated = meetingReducer(original, {
      type: "events.received",
      events: [speakerRevision(2, 2)],
      receivedAtMs: 3_000,
    });

    expect(updated.segments).toHaveLength(1);
    expect(updated.fullTranscript).toHaveLength(1);
    expect(updated.segments[0]).toMatchObject({
      speakerId: "speaker-b",
      speakerLabel: "发言人 2",
      speakerConfidence: 0.94,
      speakerAttributionRevision: 2,
      speakerAttributionSource: "diarization",
      speakerAttributionReason: "reclustered",
    });
    expect(updated.segments[0]).toMatchObject({
      text: original.segments[0].text,
      normalizedText: original.segments[0].normalizedText,
      revision: original.segments[0].revision,
      evidenceHash: original.segments[0].evidenceHash,
      updatedAtMs: original.segments[0].updatedAtMs,
    });
    expect(updated.fullTranscript[0]).toEqual(updated.segments[0]);
  });

  it("ignores replayed, stale, and out-of-order attribution revisions", () => {
    const updated = meetingReducer(stateWithSegment(), {
      type: "events.received",
      events: [
        speakerRevision(2, 3, { speaker_label: "最终说话人" }),
        speakerRevision(3, 3, { speaker_label: "冲突重放" }),
        speakerRevision(4, 2, { speaker_label: "迟到旧结果" }),
      ],
      receivedAtMs: 3_000,
    });

    expect(updated.segments).toHaveLength(1);
    expect(updated.segments[0]).toMatchObject({
      speakerLabel: "最终说话人",
      speakerAttributionRevision: 3,
    });
    expect(updated.segments[0].text).toBe("原始文字事实。");
  });

  it("does not synthesize a transcript segment when the target is absent", () => {
    const updated = meetingReducer(createInitialMeetingState("meeting-1"), {
      type: "events.received",
      events: [speakerRevision(2, 1)],
      receivedAtMs: 3_000,
    });
    expect(updated.segments).toEqual([]);
    expect(updated.fullTranscript).toEqual([]);
  });

  it("keeps a newer event attribution when a later snapshot carries an older attribution", () => {
    const eventUpdated = meetingReducer(stateWithSegment(), {
      type: "events.received",
      events: [speakerRevision(2, 4, { speaker_label: "最新说话人" })],
      receivedAtMs: 3_000,
    });
    const snapshotUpdated = meetingReducer(eventUpdated, {
      type: "snapshot.received",
      snapshot: {
        ...eventUpdated,
        lastSeq: 3,
        segments: [segment({
          normalizedText: "较新的文字修正。",
          revision: 4,
          evidenceHash: "evidence-4",
          speakerId: "speaker-old",
          speakerLabel: "旧归因",
          speakerConfidence: 0.5,
          speakerAttributionRevision: 2,
          updatedAtMs: 4_000,
        })],
      },
      receivedAtMs: 4_100,
    });

    expect(snapshotUpdated.segments[0]).toMatchObject({
      normalizedText: "较新的文字修正。",
      revision: 4,
      evidenceHash: "evidence-4",
      speakerLabel: "最新说话人",
      speakerAttributionRevision: 4,
    });
  });
});

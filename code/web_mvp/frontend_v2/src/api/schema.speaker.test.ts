import { describe, expect, it } from "vitest";
import { ContractError, parseMeetingEvent, parseMeetingSnapshot } from "./schema";

function speakerRevisionEvent(payload: Record<string, unknown> = {}) {
  return {
    meeting_id: "meeting-1",
    seq: 8,
    event_id: "event-speaker-2",
    type: "transcript.segment.speaker_revised",
    aggregate_type: "transcript_segment",
    aggregate_id: "segment-1",
    occurred_at_ms: 2_000,
    correlation_id: "run-1",
    causation_id: null,
    idempotency_key: "speaker:segment-1:2",
    payload: {
      meeting_id: "meeting-1",
      segment_id: "segment-1",
      attribution_revision: 2,
      run_id: "run-1",
      speaker_id: "speaker-a",
      speaker_label: "发言人 1",
      speaker_confidence: 0.91,
      source: "diarization",
      reason: "reclustered",
      ...payload,
    },
    published_at_ms: null,
  };
}

describe("speaker attribution contracts", () => {
  it("strictly parses a speaker revision event", () => {
    expect(parseMeetingEvent(speakerRevisionEvent()).payload).toEqual(expect.objectContaining({
      segment_id: "segment-1",
      attribution_revision: 2,
      speaker_id: "speaker-a",
      speaker_label: "发言人 1",
      speaker_confidence: 0.91,
      source: "diarization",
      reason: "reclustered",
    }));
  });

  it.each([
    ["zero revision", { attribution_revision: 0 }],
    ["fractional revision", { attribution_revision: 1.5 }],
    ["out of range confidence", { speaker_confidence: 1.1 }],
    ["missing source", { source: null }],
    ["mismatched segment", { segment_id: "segment-other" }],
    ["scored unknown speaker", { speaker_id: null, speaker_label: null }],
  ])("rejects %s", (_name, payload) => {
    const value = speakerRevisionEvent(payload);
    if (_name === "scored unknown speaker") value.payload.speaker_confidence = 0.4;
    expect(() => parseMeetingEvent(value)).toThrow(ContractError);
  });

  it("hydrates independent speaker attribution fields from a snapshot", () => {
    const snapshot = parseMeetingSnapshot({
      meeting_id: "meeting-1",
      last_seq: 8,
      segments: [{
        meeting_id: "meeting-1",
        segment_id: "segment-1",
        final_id: "final-1",
        transcript_seq: 1,
        text: "原始会议文字。",
        normalized_text: "原始会议文字。",
        revision: 3,
        evidence_hash: "evidence-3",
        speaker_id: "speaker-a",
        speaker_label: "张工",
        speaker_confidence: 0.93,
        speaker_attribution_revision: 4,
        speaker_attribution_source: "diarization",
        speaker_attribution_reason: "reclustered",
      }],
      suggestions: [],
    });

    expect(snapshot.segments[0]).toMatchObject({
      text: "原始会议文字。",
      revision: 3,
      evidenceHash: "evidence-3",
      speakerAttributionRevision: 4,
      speakerAttributionSource: "diarization",
      speakerAttributionReason: "reclustered",
    });
  });
});

import { render, screen } from "@testing-library/react";
import type { TranscriptSegment } from "../../domain/events";
import { TranscriptPane } from "./TranscriptPane";

function recovery(): TranscriptSegment {
  return { meetingId: "meeting-recovery", segmentId: "recovered", finalId: "recovered-final", transcriptSeq: 2,
    text: "补回的早期识别原文。", normalizedText: "补回的早期识别原文。", revision: 1, evidenceHash: "hash", startedAtMs: 6_000, endedAtMs: 8_000, correctionStatus: "pending", createdAtMs: 20_000, updatedAtMs: 20_000,
    recoveryProvenance: { jobId: "job", track: "system_audio", epoch: 2, startSample: 16_000, endSample: 48_000, audioDigest: "a".repeat(64), coverageDigest: "c".repeat(64), candidateVersion: 1, recognition: "local_batch_asr", timing: "whole_audio_range", correction: "not_requested", userEdited: true } };
}
const props = { archivedTranscript: "", archivedSegmentCount: 0, activePartial: null, connection: "live", liveMode: false };
it.each([true, false])("shows recovered raw status and original time order in merged=%s reading", (mergeSegments) => {
  const early = recovery(); const later = { ...early, segmentId: "later", transcriptSeq: 1, text: "原先已有的稍晚文字。", normalizedText: "原先已有的稍晚文字。", startedAtMs: 10_000, endedAtMs: 12_000, recoveryProvenance: undefined, correctionStatus: "no_change" };
  render(<TranscriptPane {...props} segments={[later, early]} mergeSegments={mergeSegments} />);
  expect(screen.getByText("补识别原文，未做AI校对")).toBeVisible(); expect(screen.queryByText("等待校对")).not.toBeInTheDocument();
  const first = screen.getByText(early.text); const second = screen.getByText(later.text);
  expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
it("uses recovery provenance for semantic paragraphs without implying all original segments are uncorrected", () => {
  const segment = recovery();
  render(<TranscriptPane {...props} segments={[segment]} semanticParagraphs={[{ meetingId: segment.meetingId, paragraphId: "recovery-paragraph", revision: 1, text: segment.text, startMs: segment.startedAtMs, endMs: segment.endedAtMs, status: "stable", checkpointIds: [segment.segmentId], createdAtMs: 20_000, updatedAtMs: 20_000 }]} />);
  expect(screen.getByText("补识别原文，未做AI校对")).toBeVisible();
});
it("honors a real later AI correction instead of claiming the recovered segment is always raw", () => {
  const segment = { ...recovery(), correctionStatus: "changed", normalizedText: "后来真实校对的文字。", correctionBeforeText: "补回的早期识别原文。", correctionAfterText: "后来真实校对的文字。" };
  render(<TranscriptPane {...props} segments={[segment]} />);
  expect(screen.getByText("已校对")).toBeVisible(); expect(screen.queryByText("补识别原文，未做AI校对")).not.toBeInTheDocument();
});

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInitialMeetingState } from "../../domain/reducer";
import type { TranscriptSegment } from "../../domain/events";
import { ReviewWorkspace } from "./ReviewWorkspace";

function segment(sequence: number): TranscriptSegment {
  return {
    meetingId: "meeting-review",
    segmentId: `segment-${sequence}`,
    finalId: `final-${sequence}`,
    transcriptSeq: sequence,
    text: `原始会议内容 ${sequence}`,
    normalizedText: `已确认会议内容 ${sequence}`,
    startedAtMs: sequence * 1_000,
    endedAtMs: sequence * 1_000 + 800,
    revision: 2,
    evidenceHash: `hash-${sequence}`,
    createdAtMs: sequence * 1_000,
    updatedAtMs: sequence * 1_000 + 900,
  };
}

describe("ReviewWorkspace", () => {
  it("uses the single-scroll transcript layout and keeps every confirmed segment in the document", async () => {
    const user = userEvent.setup();
    const state = {
      ...createInitialMeetingState("meeting-review"),
      fullTranscript: [segment(1), segment(2), segment(3)],
      fullTranscriptState: "ready" as const,
    };

    render(
      <ReviewWorkspace
        state={state}
        onReloadTranscript={vi.fn()}
        onReloadAudio={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("tab", { name: "会议文字" }));

    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveClass("review-tab-panel--transcript");
    expect(screen.getAllByText("3 段已确认")).not.toHaveLength(0);
    expect(panel.querySelectorAll(".transcript-segment")).toHaveLength(3);
    expect(screen.getByText("已确认会议内容 1")).toBeVisible();
    expect(screen.getByText("已确认会议内容 2")).toBeVisible();
    expect(screen.getByText("已确认会议内容 3")).toBeVisible();
  });
});

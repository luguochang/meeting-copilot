import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MeetingApi } from "../../api/client";
import type { MeetingNote } from "../../domain/events";
import { NotesCenter } from "./NotesCenter";

vi.mock("../settings/ProviderSettingsControl", () => ({ ProviderSettingsControl: () => null }));

function note(): MeetingNote {
  return {
    noteId: "note-1",
    meetingId: "meeting-1",
    title: "发布风险",
    body: "周五前关闭风险项。",
    sourceKind: "selection",
    sourceMessageId: null,
    version: 1,
    status: "active",
    createdAtMs: 1,
    updatedAtMs: 2,
    evidence: [{
      ordinal: 0,
      meetingId: "meeting-1",
      segmentId: "segment-1",
      transcriptSeq: 3,
      startMs: 65_000,
      endMs: 67_000,
      quote: "风险项需要在周五前关闭。",
    }],
  };
}

it("searches, autosaves and jumps from a note to transcript evidence without playback", async () => {
  let current = note();
  const api = {
    listMeetings: vi.fn(async () => ({ meetings: [{
      meetingId: "meeting-1",
      title: "发布周会",
      phase: "ended" as const,
      startedAtMs: 1,
      endedAtMs: 2,
      createdAtMs: 1,
      updatedAtMs: 2,
      segmentCount: 3,
      suggestionCount: 0,
      audioDurationMs: 0,
      hasMinutes: false,
    }] })),
    listNotes: vi.fn(async () => [current]),
    updateNote: vi.fn(async (_noteId, _version, changes) => {
      current = { ...current, ...changes, version: current.version + 1 };
      return current;
    }),
    deleteNote: vi.fn(async () => ({ ...current, status: "deleted" as const })),
  } as unknown as MeetingApi;
  const onOpenEvidence = vi.fn();

  render(
    <NotesCenter
      api={api}
      onOpenMeetings={vi.fn()}
      onOpenMeeting={vi.fn()}
      onOpenEvidence={onOpenEvidence}
    />,
  );

  expect(await screen.findByRole("button", { name: /发布风险/ })).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "笔记正文" }), {
    target: { value: "周五前关闭风险项，负责人待确认。" },
  });
  await waitFor(() => expect(api.updateNote).toHaveBeenCalledWith(
    "note-1",
    1,
    expect.objectContaining({ body: "周五前关闭风险项，负责人待确认。" }),
  ), { timeout: 2_000 });
  expect(await screen.findByText("已保存")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: /风险项需要在周五前关闭/ }));
  expect(onOpenEvidence).toHaveBeenCalledWith("meeting-1", "segment-1");
});

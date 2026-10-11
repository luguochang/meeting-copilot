import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { MeetingViewState } from "../../domain/events";
import { createInitialMeetingState } from "../../domain/reducer";
import { recoveryApi } from "../../test/transcriptRecoveryFixtures";
import { ReviewWorkspace } from "./ReviewWorkspace";

it("refreshes recovered source only after explicit merge and preserves an unsaved human final draft", async () => {
  window.localStorage.clear(); const api = recoveryApi(); const reload = vi.fn(); const refresh = vi.fn(); const save = vi.fn();
  const initial = createInitialMeetingState("meeting-recovery");
  initial.runtime = { ...initial.runtime, phase: "ended" };
  initial.fullTranscript = [{ meetingId: initial.meetingId, segmentId: "original", finalId: "original-final", transcriptSeq: 1, text: "已有识别原文", normalizedText: "已有识别原文", startedAtMs: 0, endedAtMs: 2_000, evidenceHash: "h", revision: 1, createdAtMs: 1, updatedAtMs: 1 }];
  initial.documents = { transcript: { documentId: "manual", meetingId: initial.meetingId, kind: "transcript", revision: 3, sourceRevision: 1, aiVersion: 1, userVersion: 2, source: "user_final", dirtyState: "saved", updatedAtMs: 2, contentJson: { segments: [{ segment_id: "original", text: "已保存人工文字" }] } } };
  function Fixture() {
    const [state, setState] = useState<MeetingViewState>(initial);
    return <ReviewWorkspace state={state} recoveryApi={api} onReloadTranscript={reload} onReloadAudio={vi.fn()} onExport={vi.fn()} onSaveDocument={save} onRefresh={async () => {
      refresh(); setState((current) => ({ ...current, fullTranscript: [...current.fullTranscript, { ...current.fullTranscript[0], segmentId: "recovered-segment", transcriptSeq: 2, text: "新增来源原文", normalizedText: "新增来源原文" }], documents: { ...current.documents, transcript: { ...current.documents!.transcript!, dirtyState: "stale" } } }));
    }} />;
  }
  render(<Fixture />);
  await userEvent.click(screen.getByRole("tab", { name: "会议文字" }));
  await userEvent.click(screen.getByRole("button", { name: "编辑最终文字" }));
  fireEvent.change(screen.getByRole("textbox", { name: "编辑会议文字第 1 段" }), { target: { value: "尚未保存的人工修正" } });
  await userEvent.click(screen.getByRole("button", { name: "查看文字恢复范围" }));
  await userEvent.click(await screen.findByRole("button", { name: /候选已生成/ }));
  expect(reload).not.toHaveBeenCalled(); expect(refresh).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "确认并合并到识别原文" }));
  await waitFor(() => expect(refresh).toHaveBeenCalledOnce()); expect(reload).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "编辑会议文字第 1 段" })).toHaveValue("尚未保存的人工修正");
  expect(screen.queryByText("人工文字已保存")).not.toBeInTheDocument();
  expect(save).not.toHaveBeenCalled();
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { MeetingApi } from "../../api/client";
import type { AskAiMessage, AskAiThread } from "../../domain/events";
import { AiWorkspace } from "./AiWorkspace";

function assistantMessage(): AskAiMessage {
  return {
    messageId: "assistant-1",
    threadId: "thread-1",
    meetingId: "meeting-1",
    role: "assistant",
    content: "当前需要先确认发布窗口和负责人。",
    scope: "selection",
    evidence: [{ segmentId: "segment-1", transcriptSeq: 1, startMs: 1_000, endMs: 2_000, quote: "先确认发布窗口。" }],
    status: "completed",
    errorClass: null,
    pinnedKind: null,
    createdAtMs: 2,
    updatedAtMs: 2,
  };
}

it("shows the active Pi checklist loop when no intervention is needed", async () => {
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
  } as unknown as MeetingApi;

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={null}
      askSelectionNonce={0}
      currentTopic={null}
      followUp={null}
      coachRuntime={{
        state: "active",
        label: "Pi 教练监听中",
        level: null,
        detail: "本轮完成 5 项检查 · 检索历史 1 次 · 已延续会议上下文",
      }}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn()}
      onFactStatus={vi.fn()}
      onMessage={vi.fn()}
    />,
  );

  expect(await screen.findByText("Pi 教练监听中")).toBeVisible();
  expect(screen.getByText("本轮完成 5 项检查 · 检索历史 1 次 · 已延续会议上下文")).toBeVisible();
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("问题回应");
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("承诺条件");
});

it("asks against a transcript selection, streams the answer, persists the thread, and saves a formal note", async () => {
  let savedThread: AskAiThread | null = null;
  const completed = assistantMessage();
  const api = {
    getAskThreads: vi.fn(async () => savedThread ? [savedThread] : []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
    askMeeting: vi.fn(async (_meetingId, input, onDelta) => {
      onDelta("当前需要先确认");
      savedThread = {
        threadId: "thread-1",
        title: input.question,
        createdAtMs: 1,
        updatedAtMs: 2,
        messages: [
          { ...completed, messageId: "user-1", role: "user", content: input.question, createdAtMs: 1, updatedAtMs: 1 },
          completed,
        ],
      };
      return { threadId: "thread-1", message: completed };
    }),
    createNote: vi.fn(async () => ({
      noteId: "note-1",
      meetingId: "meeting-1",
      title: "当前需要先确认发布窗口和负责人",
      body: completed.content,
      sourceKind: "ask_ai" as const,
      sourceMessageId: completed.messageId,
      version: 1,
      status: "active" as const,
      createdAtMs: 3,
      updatedAtMs: 3,
      evidence: [],
    })),
    createMeetingEntity: vi.fn(async () => undefined),
  } as unknown as MeetingApi;
  const onEvidence = vi.fn();

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={{ text: "先确认发布窗口。", segmentIds: ["segment-1"] }}
      askSelectionNonce={1}
      currentTopic={null}
      followUp={null}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={onEvidence}
      onFeedback={vi.fn()}
      onFactStatus={vi.fn()}
      onMessage={vi.fn()}
    />,
  );

  expect(await screen.findByText("先确认发布窗口。", { selector: "blockquote" })).toBeVisible();
  expect(screen.getByLabelText("提问范围")).toHaveValue("selection");
  fireEvent.change(screen.getByPlaceholderText("询问决策、风险、上下文或下一步"), {
    target: { value: "现在最需要确认什么？" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));

  expect(await screen.findByText("当前需要先确认发布窗口和负责人。")).toBeVisible();
  expect(api.askMeeting).toHaveBeenCalledWith(
    "meeting-1",
    expect.objectContaining({ scope: "selection", segmentIds: ["segment-1"] }),
    expect.any(Function),
  );
  fireEvent.click(screen.getByRole("button", { name: "查看依据" }));
  expect(onEvidence).toHaveBeenCalledWith("segment-1");
  fireEvent.click(screen.getByRole("button", { name: "存为笔记" }));
  await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(
    "meeting-1",
    expect.objectContaining({
      body: completed.content,
      sourceKind: "ask_ai",
      sourceMessageId: "assistant-1",
      evidence: [expect.objectContaining({ segmentId: "segment-1" })],
    }),
  ));
  fireEvent.click(screen.getByRole("button", { name: "存为事实" }));
  fireEvent.click(screen.getByRole("button", { name: "存为行动项" }));
  await waitFor(() => {
    expect(api.createMeetingEntity).toHaveBeenCalledWith(
      "meeting-1",
      expect.objectContaining({ kind: "decision_candidate", sourceMessageId: "assistant-1" }),
    );
    expect(api.createMeetingEntity).toHaveBeenCalledWith(
      "meeting-1",
      expect.objectContaining({ kind: "action_item", sourceMessageId: "assistant-1" }),
    );
  });
});

it("edits meeting focus points as a new version without replacing prior context", async () => {
  let version = 1;
  let focusPoints = ["断句", "术语"];
  const snapshot = () => ({
    meetingId: "meeting-1",
    hotwords: ["FunASR"],
    inputSource: "microphone" as const,
    inputDeviceId: null,
    inputDeviceName: null,
    noticeAcknowledged: true,
    presetId: "project" as const,
    meetingGoal: "验收中文实时会议",
    participantRole: "产品负责人",
    focusPoints,
    outputFormat: "action_plan" as const,
    proactiveSuggestionPolicy: "low_frequency" as const,
    version,
    updatedAtMs: version,
  });
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    getMeetingPreparation: vi.fn(async () => snapshot()),
    getMeetingPreparationVersions: vi.fn(async () => [snapshot()]),
    saveMeetingPreparation: vi.fn(async (_meetingId, preparation) => {
      version += 1;
      focusPoints = preparation.focusPoints ?? [];
    }),
  } as unknown as MeetingApi;

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={null}
      askSelectionNonce={0}
      currentTopic={null}
      followUp={null}
      openQuestions={[]}
      suggestions={[]}
      decisionCandidates={[]}
      actionItems={[]}
      risks={[]}
      onEvidence={vi.fn()}
      onFeedback={vi.fn()}
      onFactStatus={vi.fn()}
      onMessage={vi.fn()}
    />,
  );

  fireEvent.click(await screen.findByRole("tab", { name: "会议目标" }));
  expect(await screen.findByDisplayValue("验收中文实时会议")).toBeVisible();
  fireEvent.change(screen.getByPlaceholderText("用逗号分隔关注点"), {
    target: { value: "断句、断线恢复、纪要隔离" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));

  await waitFor(() => expect(api.saveMeetingPreparation).toHaveBeenCalledWith(
    "meeting-1",
    expect.objectContaining({ focusPoints: ["断句", "断线恢复", "纪要隔离"] }),
  ));
  await waitFor(() => expect(screen.getByText(/版本 2/)).toBeVisible());
});

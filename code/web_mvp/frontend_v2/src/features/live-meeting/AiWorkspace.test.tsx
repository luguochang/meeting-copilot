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

function formalAi(segmentId: string) {
  return {
    source: "llm_first" as const,
    jobId: `job-${segmentId}`,
    batchId: "batch-1",
    provider: "test-provider",
    model: "test-model",
    llmCalled: true as const,
    evidence: {
      segmentIds: [segmentId],
      quote: `quote-${segmentId}`,
      evidenceHash: null,
      stateRevision: null,
    },
  };
}

it("shows the active Pi checklist loop when no intervention is needed", async () => {
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
    getMeetingPreparation: vi.fn(async () => ({
      meetingId: "meeting-1",
      hotwords: [],
      inputSource: "microphone" as const,
      inputDeviceId: null,
      inputDeviceName: null,
      noticeAcknowledged: true,
      presetId: "interview" as const,
      meetingGoal: "理解用户的真实行为",
      participantRole: "访谈者",
      focusPoints: ["具体场景"],
      outputFormat: "brief" as const,
      proactiveSuggestionPolicy: "low_frequency" as const,
      version: 1,
      updatedAtMs: 1,
    })),
  } as unknown as MeetingApi;

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={null}
      askSelectionNonce={0}
      currentTopic={null}
      followUp={null}
      coachHistory={[{
        historyId: "prior-coach-1",
        createdAtMs: 1_000,
        question: "先确认负责人，再承诺时间。",
        reason: "此前负责人尚未确认。",
        evidenceSegmentIds: ["segment-1"],
        evidenceQuote: "负责人还没定",
        urgency: "high",
        coachEventType: "commitment_risk",
        formalAi: formalAi("segment-1"),
      }]}
      coachRuntime={{
        state: "active",
        label: "Pi 教练监听中",
        level: null,
        detail: "本轮完成 6 项检查 · 检索历史 1 次 · 已延续会议上下文",
        decision: "本轮结论：暂不打断，没有发现需要立刻介入的表达问题",
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
  expect(await screen.findByText("用户访谈")).toBeVisible();
  expect(screen.getByText("本轮结论：暂不打断，没有发现需要立刻介入的表达问题")).toBeVisible();
  expect(screen.getByText("本轮完成 6 项检查 · 检索历史 1 次 · 已延续会议上下文")).toBeVisible();
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("问题回应");
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("承诺条件");
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("表达清晰");
  expect(screen.getByRole("list", { name: "教练检查项" })).toHaveTextContent("访谈证据深度");
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("先确认负责人，再承诺时间。");
});

it("keeps the latest coach intervention prominent and exposes prior advice", async () => {
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
  } as unknown as MeetingApi;
  const oldAdvice = {
    historyId: "coach-1",
    createdAtMs: 1_000,
    question: "先说明这次调整解决什么问题。",
    reason: "当前表达缺少问题定义。",
    evidenceSegmentIds: ["segment-1"],
    evidenceQuote: "我们准备调整方案",
    urgency: "medium" as const,
    coachEventType: "communication_clarity" as const,
    formalAi: formalAi("segment-1"),
  };
  const latestAdvice = {
    historyId: "coach-2",
    createdAtMs: 2_000,
    question: "可以承诺周五，但先补充压测达标条件。",
    reason: "上线日期已承诺，压测条件还未说明。",
    evidenceSegmentIds: ["segment-2"],
    evidenceQuote: "周五上线",
    urgency: "high" as const,
    coachEventType: "commitment_risk" as const,
    formalAi: formalAi("segment-2"),
  };

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={null}
      askSelectionNonce={0}
      currentTopic={null}
      followUp={latestAdvice}
      coachHistory={[oldAdvice, latestAdvice]}
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

  expect(await screen.findByText(latestAdvice.question, { selector: "blockquote" })).toBeVisible();
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent(oldAdvice.question);
  expect(screen.getByText("过去建议").parentElement).toHaveTextContent("1");
});

it("shows recent discussion as a bounded timeline that can reveal earlier items", async () => {
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
    askMeeting: vi.fn(),
  } as unknown as MeetingApi;
  const recentContextHistory = Array.from({ length: 6 }, (_, index) => ({
    contextId: `context-${index + 1}`,
    kind: index % 3 === 0 ? "decision" as const : index % 3 === 1 ? "question" as const : "topic" as const,
    title: `讨论内容 ${index + 1}`,
    summary: index % 3 === 2 ? `内容摘要 ${index + 1}` : null,
    updatedAtMs: (index + 1) * 1_000,
    evidenceSegmentIds: [`segment-${index + 1}`],
    formalAi: formalAi(`segment-${index + 1}`),
  }));

  render(
    <AiWorkspace
      meetingId="meeting-1"
      api={api}
      selection={null}
      askSelectionNonce={0}
      currentTopic={null}
      followUp={null}
      recentContextHistory={recentContextHistory}
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

  const timeline = await screen.findByRole("list", { name: "最近讨论时间线" });
  expect(timeline).toHaveTextContent("讨论内容 6");
  expect(timeline).not.toHaveTextContent("讨论内容 1");
  fireEvent.click(screen.getByRole("button", { name: "查看更早的 1 条" }));
  expect(timeline).toHaveTextContent("讨论内容 1");
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

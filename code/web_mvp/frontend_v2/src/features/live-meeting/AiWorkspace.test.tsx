import type { KnowledgeApi } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import type { DocumentSourceEvidence } from "../../domain/events";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach } from "vitest";
import { HttpMeetingApi, type MeetingApi } from "../../api/client";
import type { AskAiMessage, AskAiThread } from "../../domain/events";
import { AiWorkspace } from "./AiWorkspace";

afterEach(() => {
  window.sessionStorage.clear();
});

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

it("restores the selected AI workspace tab after leaving and returning to a meeting", async () => {
  const api = {
    getAskThreads: vi.fn(async () => []),
    getChapters: vi.fn(async () => []),
    listNotes: vi.fn(async () => []),
  } as unknown as MeetingApi;
  const props = {
    meetingId: "meeting-view-state",
    api,
    selection: null,
    askSelectionNonce: 0,
    currentTopic: null,
    followUp: null,
    openQuestions: [],
    suggestions: [],
    decisionCandidates: [],
    actionItems: [],
    risks: [],
    onEvidence: vi.fn(),
    onFeedback: vi.fn(),
    onFactStatus: vi.fn(),
    onMessage: vi.fn(),
  };

  const first = render(<AiWorkspace {...props} />);
  fireEvent.click(await screen.findByRole("tab", { name: "询问 AI" }));
  await waitFor(() => expect(window.sessionStorage.getItem("meeting-copilot-ai-view:meeting-view-state")).toContain('"tab":"ask"'));
  first.unmount();

  render(<AiWorkspace {...props} />);
  expect(await screen.findByRole("tab", { name: "询问 AI" })).toHaveAttribute("aria-selected", "true");
});

it("keeps deliberate participation and history available when no intervention is needed", async () => {
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
  expect(screen.getByText("Pi 教练监听中")).toHaveAttribute("title", "本轮完成 6 项检查 · 检索历史 1 次 · 已延续会议上下文");
  expect(screen.getByRole("button", { name: "我可以问什么" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "还有什么方案" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "帮我组织回应" })).toBeEnabled();
  fireEvent.click(screen.getByText("过去建议"));
  expect(screen.getByRole("list", { name: "过去的教练建议" })).toHaveTextContent("先确认负责人，再承诺时间。");
  const coachTab = screen.getByRole("tab", { name: "实时教练" });
  const askTab = screen.getByRole("tab", { name: "询问 AI" });
  expect(coachTab).toHaveAttribute("aria-controls", screen.getByRole("tabpanel").id);
  fireEvent.click(screen.getByRole("button", { name: "自己提问" }));
  expect(askTab).toHaveAttribute("aria-selected", "true");
  coachTab.focus();
  fireEvent.keyDown(coachTab, { key: "ArrowRight" });
  await waitFor(() => expect(askTab).toHaveFocus());
  expect(askTab).toHaveAttribute("aria-selected", "true");
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
      coachRuntime={{
        state: "active",
        label: "Pi 教练已分析正文",
        level: null,
        detail: "本轮完成 7 项检查 · 已延续会议上下文",
        decision: "本轮结论：暂不打断，新内容与当前议题无关",
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

  expect(await screen.findByText(latestAdvice.question, { selector: "blockquote" })).toBeVisible();
  expect(screen.getByText("Pi 教练已分析正文")).toHaveAttribute("title", "本轮完成 7 项检查 · 已延续会议上下文");
  expect(screen.queryByText("本轮结论：暂不打断，新内容与当前议题无关")).not.toBeInTheDocument();
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

  await screen.findByRole("tab", { name: "实时教练" });
  fireEvent.click(screen.getByRole("tab", { name: "询问 AI" }));
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
    liveAnalysisProfile: "participation" as const,
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
    expect.objectContaining({ focusPoints: ["断句", "断线恢复", "纪要隔离"], liveAnalysisProfile: "participation" }),
  ));
  await waitFor(() => expect(screen.getByText(/版本 2/)).toBeVisible());
});

const knowledgeRef: DocumentSourceEvidence = { sourceType: "document", sourceId: "span", documentId: "doc", projectId: "project", title: "预算", versionId: "version", version: 2, contentHash: "hash", locator: { type: "pdf_page", page: 3 }, quote: "预算三万元", contextRevision: 3 };
function knowledgeProps(api: MeetingApi) {
  return { meetingId: "meeting-1", api, selection: null, askSelectionNonce: 0, currentTopic: null, followUp: null, openQuestions: [], suggestions: [], decisionCandidates: [], actionItems: [], risks: [], onEvidence: vi.fn(), onFeedback: vi.fn(), onFactStatus: vi.fn(), onMessage: vi.fn() };
}
it.each([
  ["我可以问什么", "ask_question"],
  ["还有什么方案", "compare_options"],
  ["帮我组织回应", "formulate_response"],
])("submits %s without Fast and without changing the persisted meeting mode", async (label, intent) => {
  const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
  const fetcher = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/preparation")) return Promise.resolve(response({ meeting_id: "meeting-1", live_analysis_profile: "legacy" }));
    if (url.endsWith("/coach/request")) return Promise.resolve(response({ job: { id: "manual-job" } }));
    if (url.endsWith("/snapshot")) return Promise.resolve(response({ jobs: [{ id: "manual-job", status: "succeeded" }] }));
    return Promise.reject(new Error(`Unexpected route ${url}`));
  });
  vi.stubGlobal("fetch", fetcher); const http = new HttpMeetingApi();
  const api = { getAskThreads: vi.fn().mockResolvedValue([]), getChapters: vi.fn().mockResolvedValue([]), getMeetingPreparation: http.getMeetingPreparation.bind(http), saveMeetingPreparation: http.saveMeetingPreparation.bind(http), requestRealtimeCoach: http.requestRealtimeCoach.bind(http), getCoachRequestStatus: http.getCoachRequestStatus.bind(http) } as unknown as MeetingApi;
  render(<AiWorkspace {...knowledgeProps(api)} />);
  fireEvent.click(await screen.findByRole("button", { name: label }));
  expect(await screen.findByRole("status", { name: "本次现场请求结果" })).toHaveTextContent("当前没有新的有用贡献；可以等讨论推进后再试。");
  const requests = fetcher.mock.calls as unknown as [RequestInfo | URL, RequestInit][];
  const posts = requests.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1); expect(posts[0][0]).toBe("/v2/meetings/meeting-1/coach/request");
  expect(JSON.parse(String(posts[0][1].body))).toEqual({ request: "", answer_id: null, intent });
  expect(requests.some(([, init]) => init?.method === "PUT")).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "会议目标" }));
  expect(await screen.findByText(/本场主要帮助：优先回答听到的问题/)).toBeVisible();
});
it("routes a contribution through the structured own-job result without using the legacy status result", async () => {
  const request = vi.fn().mockResolvedValue("own-job");
  const getOutcome = vi.fn().mockResolvedValue({ meetingId: "meeting-1", jobId: "own-job", status: "failed", failureCategory: "validation_rejected", canRetry: false, topicId: null, analysisBasisRevision: null });
  const legacy = vi.fn().mockResolvedValue("succeeded");
  const api = { getAskThreads: vi.fn().mockResolvedValue([]), getChapters: vi.fn().mockResolvedValue([]), requestRealtimeCoach: request, getCoachRequestOutcome: getOutcome, getCoachRequestStatus: legacy } as unknown as MeetingApi;
  render(<AiWorkspace {...knowledgeProps(api)} />);
  fireEvent.click(await screen.findByRole("button", { name: "我可以问什么" }));
  await screen.findByRole("alert", { name: "本次现场请求结果" });
  expect(screen.getByText(/生成建议未通过格式或依据核对，未发布/)).toBeVisible();
  expect(request).toHaveBeenCalledWith("meeting-1", "", null, undefined, { intent: "ask_question" });
  expect(getOutcome).toHaveBeenCalledWith("meeting-1", "own-job", expect.any(AbortSignal));
  expect(legacy).not.toHaveBeenCalled(); expect(request).toHaveBeenCalledTimes(1);
  expect(screen.queryByText(/现场建议已生成/)).not.toBeInTheDocument();
});
it("does not call a failed Ask result saved and restores the question for deliberate retry", async () => {
  const answer = { ...assistantMessage(), status: "failed" as const, content: "", errorClass: "ProviderTimeout" };
  const api = { getAskThreads: vi.fn().mockResolvedValue([{ threadId: "thread-1", title: "现场提问", messages: [answer] }]), getChapters: vi.fn().mockResolvedValue([]), listNotes: vi.fn().mockResolvedValue([]), askMeeting: vi.fn().mockResolvedValue({ threadId: "thread-1", message: answer }) } as unknown as MeetingApi;
  const onMessage = vi.fn(); render(<AiWorkspace {...knowledgeProps(api)} onMessage={onMessage} />);
  fireEvent.click(screen.getByRole("tab", { name: "询问 AI" }));
  await screen.findByText("本次回答失败，请重试。");
  const input = screen.getByPlaceholderText("询问决策、风险、上下文或下一步");
  fireEvent.change(input, { target: { value: "这场会议的验收条件是什么？" } });
  fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  expect(await screen.findByText("本次回答未完成，历史内容已保留。请重试提问。")).toBeVisible();
  expect(input).toHaveValue("这场会议的验收条件是什么？");
  expect(onMessage).not.toHaveBeenCalledWith("AI 回答已保存");
  expect(screen.queryByRole("button", { name: "存为事实" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "存为笔记" })).not.toBeInTheDocument();
  expect(api.askMeeting).toHaveBeenCalledTimes(1);
});
it("asks confirmed project sources without transcript and preserves mixed references when saving", async () => {
  const answer = { ...assistantMessage(), scope: "project" as const, evidence: [knowledgeRef, ...assistantMessage().evidence], evidenceStatus: "valid" as const };
  const api = { getAskThreads: vi.fn().mockResolvedValue([{ threadId: "thread-1", title: "项目提问", messages: [answer] }]), getChapters: vi.fn().mockResolvedValue([]), listNotes: vi.fn().mockResolvedValue([]), askMeeting: vi.fn().mockResolvedValue({ threadId: "thread-1", message: answer }), createNote: vi.fn().mockResolvedValue({ noteId: "note" }), createMeetingEntity: vi.fn().mockResolvedValue(undefined) } as unknown as MeetingApi;
  const knowledgeApi = { getMeetingContext: vi.fn(), getMeetingSource: vi.fn() } as unknown as KnowledgeApi;
  render(<AiWorkspace {...knowledgeProps(api)} knowledgeApi={knowledgeApi} />);
  fireEvent.click(screen.getByRole("tab", { name: "询问 AI" })); await screen.findByText(answer.content);
  expect(screen.getByRole("checkbox", { name: "同时参考本场已启用附件与所选项目的已确认资料" })).toBeChecked();
  expect(screen.getByRole("option", { name: "本场资料（无需会议文字）" })).toHaveValue("project");
  fireEvent.change(screen.getByLabelText("提问范围"), { target: { value: "project" } }); fireEvent.change(screen.getByPlaceholderText("询问决策、风险、上下文或下一步"), { target: { value: "预算多少？" } }); fireEvent.click(screen.getByRole("button", { name: "发送问题" }));
  await waitFor(() => expect(api.askMeeting).toHaveBeenCalledWith("meeting-1", expect.objectContaining({ scope: "project", includeKnowledge: true, question: "预算多少？", segmentIds: undefined }), expect.any(Function)));
  expect(screen.getByText("只询问本场已启用附件及所选项目范围内的已确认资料；不要求已有语音文字。")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "存为笔记" })); await waitFor(() => expect(api.createNote).toHaveBeenCalledWith("meeting-1", expect.objectContaining({ sourceMessageId: "assistant-1", evidence: [expect.objectContaining({ segmentId: "segment-1" })] })));
  fireEvent.click(screen.getByRole("button", { name: "存为事实" })); await waitFor(() => expect(api.createMeetingEntity).toHaveBeenCalledWith("meeting-1", expect.objectContaining({ sourceMessageId: "assistant-1", sourceEvidence: [knowledgeRef], evidence: [expect.objectContaining({ segmentId: "segment-1" })] })));
});
it("retains stale Ask content and citations while blocking save as current facts", async () => {
  const answer = { ...assistantMessage(), status: "failed" as const, content: "修订前的回答仍然保留", evidence: [knowledgeRef], evidenceStatus: "stale" as const, errorClass: "EvidenceSuperseded" };
  const api = { getAskThreads: vi.fn().mockResolvedValue([{ threadId: "thread-1", title: "旧提问", messages: [answer] }]), getChapters: vi.fn().mockResolvedValue([]), listNotes: vi.fn().mockResolvedValue([]) } as unknown as MeetingApi;
  render(<AiWorkspace {...knowledgeProps(api)} />); fireEvent.click(screen.getByRole("tab", { name: "询问 AI" })); await screen.findByText(answer.content); expect(screen.getByText(/此回答已过期/)).toBeInTheDocument(); expect(screen.queryByRole("button", { name: "存为事实" })).not.toBeInTheDocument(); expect(screen.queryByRole("button", { name: "存为笔记" })).not.toBeInTheDocument(); expect(screen.getByText("查看全部依据 · 1 条")).toBeInTheDocument();
});
it("shows a real Ask-sourced user candidate with typed evidence without fabricating formal AI provenance", async () => {
  const api = { getAskThreads: vi.fn().mockResolvedValue([]), getChapters: vi.fn().mockResolvedValue([]), listNotes: vi.fn().mockResolvedValue([]) } as unknown as MeetingApi;
  const candidate = { id: "candidate", text: "用户保存的预算候选", status: "candidate" as const, confidence: null, evidenceSegmentIds: [], evidenceSpans: [], updatedAtMs: 1, formalAi: null, sourceMessageId: "assistant-real", sourceEvidence: [knowledgeRef], evidenceStatus: "valid" as const };
  render(<AiWorkspace {...knowledgeProps(api)} decisionCandidates={[candidate]} />); fireEvent.click(screen.getByRole("tab", { name: "会议目标" })); await screen.findByText(candidate.text); expect(screen.getByText("来自保存的 AI 回答，仍需你确认。")).toBeInTheDocument(); expect(screen.getByText("查看全部依据 · 1 条")).toBeInTheDocument();
});
it.each([2, 3])("creates pending project candidates only from the exact current human meeting fact version %s", async (sourceVersion) => {
  const api = { getAskThreads: vi.fn().mockResolvedValue([]), getChapters: vi.fn().mockResolvedValue([]), listNotes: vi.fn().mockResolvedValue([]) } as unknown as MeetingApi;
  const candidate = { id: "human-fact", text: "人工确认预算", status: "confirmed" as const, confidence: null, evidenceSegmentIds: [], evidenceSpans: [], updatedAtMs: 1, formalAi: null, version: 2, sourceMessageId: "assistant-real", sourceEvidence: [knowledgeRef], evidenceStatus: "valid" as const };
  const source = { sourceType: "meeting_entity" as const, sourceId: candidate.id, projectId: "project", meetingId: "meeting-1", version: sourceVersion, contentHash: "human-hash", quote: candidate.text, locator: { type: "meeting_entity", kind: "decision" } };
  const projectApi = { getMeetingState: vi.fn().mockResolvedValue({ projectId: "project", revision: 7, decisions: [], unfinishedTasks: [], openQuestions: [], truncated: false }), getSource: vi.fn().mockResolvedValue(source), createProposal: vi.fn().mockResolvedValue({ id: "pending" }) } as unknown as ProjectOutcomesApi;
  const props = knowledgeProps(api); render(<AiWorkspace {...props} projectApi={projectApi} decisionCandidates={[candidate]} />); fireEvent.click(screen.getByRole("tab", { name: "会议目标" })); fireEvent.click(await screen.findByRole("button", { name: "加入项目候选" }));
  if (sourceVersion === 2) await waitFor(() => expect(projectApi.createProposal).toHaveBeenCalledWith("project", [{ action: "create", type: "decision", text: candidate.text, status: "active", sourceRefs: [source] }], 7, "meeting-1"));
  else { await waitFor(() => expect(props.onMessage).toHaveBeenCalledWith(expect.stringContaining("会议事项已更新"))); expect(projectApi.createProposal).not.toHaveBeenCalled(); }
});

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import type { MeetingSnapshot } from "../../domain/events";
import { LiveMeetingWorkbench } from "./LiveMeetingWorkbench";
import type { BrowserMicrophoneController } from "./useBrowserMicrophone";

function realSnapshot(): MeetingSnapshot {
  return {
    meetingId: "meeting-1",
    title: "支付服务发布评审",
    lastSeq: 4,
    segments: [
      {
        meetingId: "meeting-1",
        segmentId: "segment-1",
        finalId: "final-1",
        transcriptSeq: 1,
        text: "支付服务周五上线但是负责人还没定",
        normalizedText: "支付服务周五上线，但是负责人还没确定。",
        startedAtMs: 2_000,
        endedAtMs: 6_000,
        revision: 2,
        evidenceHash: "hash-1",
        createdAtMs: 6_100,
        updatedAtMs: 7_000,
      },
    ],
    activePartial: { segmentId: "partial-1", text: "回滚窗口我们还需要再确认", startedAtMs: 7_000, updatedAtMs: 8_000 },
    suggestions: [
      {
        suggestionId: "suggestion-1",
        meetingId: "meeting-1",
        jobId: "job-1",
        generationId: "generation-1",
        evidenceSegmentId: "segment-1",
        evidenceTranscriptSeq: 1,
        evidenceHash: "hash-1",
        stateRevision: 1,
        status: "committed",
        draftText: "请确认发布负责人。",
        draftSeq: 2,
        text: "谁负责本次上线，并在什么条件下执行回滚？",
        finalDraftSeq: 2,
        feedback: null,
        createdAtMs: 7_100,
        updatedAtMs: 7_800,
        committedAtMs: 7_800,
      },
    ],
    currentTopic: {
      id: "topic-1",
      text: "支付服务上线安排",
      status: "active",
      evidenceSegmentIds: ["segment-1"],
      updatedAtMs: 7_000,
    },
    openQuestions: [
      {
        id: "question-1",
        text: "上线负责人是谁？",
        status: "open",
        evidenceSegmentIds: ["segment-1"],
        updatedAtMs: 7_000,
      },
    ],
    minutes: null,
    approach: { cards: [], degraded: null, updatedAtMs: null },
    reviewJobs: {},
    audio: { status: "recording", chunkCount: 1, durationMs: 5_000, fileSizeBytes: 160_000, tracks: ["microphone"] },
    runtime: {
      phase: "live",
      recording: { state: "active", label: "录音中", level: null, detail: "WAV 正在保存" },
      input: { state: "active", label: "有声音", level: 0.72, detail: null },
      ai: { state: "busy", label: "整理中", level: null, detail: null },
      elapsedMs: 18_000,
    },
    diagnostics: { provider_mode: "remote", acceptance_gate: "open", mock: false },
  };
}

function dependencies() {
  const api: MeetingApi = {
    createMeeting: vi.fn().mockResolvedValue(undefined),
    importRecording: vi.fn().mockResolvedValue({ meetingId: "imported-meeting" }),
    deleteMeeting: vi.fn().mockResolvedValue(undefined),
    listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
    getSnapshot: vi.fn().mockResolvedValue(realSnapshot()),
    getTranscript: vi.fn().mockResolvedValue(realSnapshot().segments),
    getEvents: vi.fn().mockResolvedValue({
      meetingId: "meeting-1",
      afterSeq: 4,
      lastSeq: 4,
      events: [],
      hasMore: false,
      nextAfterSeq: 4,
    }),
    getAudio: vi.fn().mockResolvedValue({
      meetingId: "meeting-1",
      status: "saved",
      chunkCount: 1,
      durationMs: 5_000,
      fileSizeBytes: 160_000,
      tracks: ["microphone"],
      assembled: true,
      playbackUrl: "/audio.wav",
      format: "wav",
      chunks: [],
    }),
    endMeeting: vi.fn().mockResolvedValue(undefined),
    saveSuggestionFeedback: vi.fn().mockResolvedValue(undefined),
    markUiRendered: vi.fn().mockResolvedValue(undefined),
  };
  const transport: MeetingEventTransport = {
    kind: "poll",
    subscribe(subscription) {
      subscription.onConnection("live");
      return () => undefined;
    },
  };
  return { api, transport };
}

function microphoneController(
  overrides: Partial<BrowserMicrophoneController["state"]> = {},
): BrowserMicrophoneController {
  return {
    state: {
      phase: "idle",
      asrReady: false,
      inputLevel: 0,
      elapsedMs: null,
      activePartial: null,
      error: null,
      statusMessage: "尚未开始录音",
      droppedFrames: 0,
      ...overrides,
    },
    start: vi.fn().mockResolvedValue(undefined),
    togglePause: vi.fn(),
    end: vi.fn().mockResolvedValue(undefined),
    acknowledgeCommitted: vi.fn(),
  };
}

describe("LiveMeetingWorkbench", () => {
  it("exposes recording import from the meeting list and opens the imported review", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onOpenMeeting = vi.fn();
    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        onOpenMeeting={onOpenMeeting}
      />,
    );

    const input = screen.getByLabelText("选择要导入的录音文件") as HTMLInputElement;
    const file = new File(["audio"], "review.wav", { type: "audio/wav" });
    await user.upload(input, file);

    expect(api.importRecording).toHaveBeenCalledWith(file);
    expect(onOpenMeeting).toHaveBeenCalledWith("imported-meeting");
  });

  it("shows an import failure on the meeting list instead of hiding it in a toast", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    vi.mocked(api.importRecording).mockRejectedValue(new Error("本地转写不可用"));
    render(<LiveMeetingWorkbench meetingId={null} api={api} transport={transport} />);

    const input = screen.getByLabelText("选择要导入的录音文件") as HTMLInputElement;
    await user.upload(input, new File(["audio"], "broken.wav", { type: "audio/wav" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地转写不可用");
  });

  it("rejects an empty import before making a network request", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId={null} api={api} transport={transport} />);

    const input = screen.getByLabelText("选择要导入的录音文件") as HTMLInputElement;
    await user.upload(input, new File([], "empty.wav", { type: "audio/wav" }));

    expect(screen.getByRole("alert")).toHaveTextContent("录音文件为空");
    expect(api.importRecording).not.toHaveBeenCalled();
  });

  it("creates a meeting and starts the real microphone from the empty state", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const microphone = microphoneController();
    const onCreateMeeting = vi.fn(() => "rec_new_meeting");
    const order: string[] = [];
    vi.mocked(api.createMeeting).mockImplementation(async () => {
      order.push("meeting-created");
    });
    vi.mocked(microphone.start).mockImplementation(async () => {
      order.push("microphone-started");
    });

    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        microphoneController={microphone}
        onCreateMeeting={onCreateMeeting}
      />,
    );

    await user.click(screen.getByRole("button", { name: "开始会议" }));

    expect(onCreateMeeting).toHaveBeenCalledOnce();
    expect(api.createMeeting).toHaveBeenCalledWith("rec_new_meeting");
    expect(microphone.start).toHaveBeenCalledWith("rec_new_meeting");
    expect(order).toEqual(["meeting-created", "microphone-started"]);
  });

  it("returns to the meeting list when creating a new meeting fails", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onBackToMeetings = vi.fn();
    vi.mocked(api.createMeeting).mockRejectedValue(new Error("会议存储不可用"));
    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        onCreateMeeting={() => "failed-meeting"}
        onBackToMeetings={onBackToMeetings}
      />,
    );

    await user.click(screen.getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(onBackToMeetings).toHaveBeenCalledOnce());
  });

  it("keeps capture controls hidden until the initial meeting snapshot arrives", async () => {
    const { api, transport } = dependencies();
    let resolveSnapshot: ((snapshot: MeetingSnapshot) => void) | undefined;
    vi.mocked(api.getSnapshot).mockImplementation(
      () => new Promise((resolve) => {
        resolveSnapshot = resolve;
      }),
    );
    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={microphoneController({ phase: "recording" })}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("正在加载会议状态");
    expect(screen.queryByRole("button", { name: "开始录音" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "暂停录音" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "结束并整理" })).not.toBeInTheDocument();

    resolveSnapshot?.(realSnapshot());
    expect(await screen.findByText("支付服务上线安排")).toBeVisible();
  });

  it("shows the complete live projection and exactly one end-meeting command", async () => {
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    expect(await screen.findByText("支付服务周五上线，但是负责人还没确定。")).toBeVisible();
    expect(screen.getByText("回滚窗口我们还需要再确认")).toBeVisible();
    expect(screen.getByText("支付服务上线安排")).toBeVisible();
    expect(screen.getByText("谁负责本次上线，并在什么条件下执行回滚？")).toBeVisible();
    expect(screen.getByText("上线负责人是谁？")).toBeVisible();
    expect(screen.getByText("AI 已校正")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "结束并整理" })).toHaveLength(1);
  });

  it("does not render a microphone partial after its segment is committed", async () => {
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: {
        segmentId: "segment-1",
        text: "同一段 final 的暂存副本",
        startedAtMs: 2_000,
        updatedAtMs: 8_100,
      },
    });

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    await screen.findByText("支付服务周五上线，但是负责人还没确定。");
    expect(screen.queryByText("同一段 final 的暂存副本")).not.toBeInTheDocument();
  });

  it("does not label deterministic text normalization as an AI correction", async () => {
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      segments: realSnapshot().segments.map((segment) => ({
        ...segment,
        revision: 1,
      })),
    });

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    expect(await screen.findByText("支付服务周五上线，但是负责人还没确定。")).toBeVisible();
    expect(screen.queryByText("AI 已校正")).not.toBeInTheDocument();
  });

  it("keeps internal runtime terminology inside the diagnostics drawer", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);
    await screen.findByText("支付服务上线安排");

    expect(screen.queryByText("provider_mode")).not.toBeInTheDocument();
    expect(screen.queryByText("acceptance_gate")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开运行诊断" }));
    const drawer = screen.getByRole("dialog", { name: "会议连接详情" });
    expect(within(drawer).getByText(/provider_mode/)).toBeVisible();
    expect(within(drawer).getByText(/acceptance_gate/)).toBeVisible();
  });

  it("saves suggestion feedback only after the typed command succeeds", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);
    await screen.findByText("谁负责本次上线，并在什么条件下执行回滚？");

    await user.click(screen.getByRole("button", { name: "保留建议" }));
    await waitFor(() =>
      expect(api.saveSuggestionFeedback).toHaveBeenCalledWith("meeting-1", "suggestion-1", "kept"),
    );
    expect(await screen.findByRole("status")).toHaveTextContent("建议已保留");
  });

  it("reports a committed suggestion after render and ignores receipt failure", async () => {
    const { api } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      lastSeq: 0,
      suggestions: [],
    });
    vi.mocked(api.markUiRendered).mockRejectedValue(new Error("trace unavailable"));
    const committedEvent = {
      meetingId: "meeting-1",
      seq: 1,
      eventId: "event-1",
      type: "suggestion.committed",
      aggregateType: "suggestion",
      aggregateId: "suggestion-2",
      occurredAtMs: 9_000,
      correlationId: "generation-2",
      causationId: "job-2",
      idempotencyKey: "suggestion.committed:suggestion-2:4",
      payload: {
        suggestion_id: "suggestion-2",
        meeting_id: "meeting-1",
        job_id: "job-2",
        generation_id: "generation-2",
        evidence_segment_id: "segment-1",
        evidence_transcript_seq: 1,
        evidence_hash: "hash-1",
        state_revision: 1,
        status: "committed",
        draft_text: "请确认回滚窗口",
        draft_seq: 4,
        text: "回滚窗口和触发条件是否已经确认？",
        final_draft_seq: 4,
        created_at_ms: 8_000,
        updated_at_ms: 9_000,
        committed_at_ms: 9_000,
      },
      publishedAtMs: null,
    };
    const transport: MeetingEventTransport = {
      kind: "sse",
      subscribe(subscription) {
        subscription.onConnection("live");
        subscription.onEvents([committedEvent]);
        return () => undefined;
      },
    };

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    expect(await screen.findByText("回滚窗口和触发条件是否已经确认？")).toBeVisible();
    await waitFor(() => expect(api.markUiRendered).toHaveBeenCalledWith("job-2", 1, 4));
    expect(screen.getByText("回滚窗口和触发条件是否已经确认？")).toBeVisible();
    expect(screen.queryByText("trace unavailable")).not.toBeInTheDocument();
  });

  it("flushes and ends microphone capture before requesting post-meeting processing", async () => {
    const user = userEvent.setup();
    const order: string[] = [];
    const { api, transport } = dependencies();
    vi.mocked(api.endMeeting).mockImplementation(async () => {
      order.push("api-end");
    });
    const microphone = microphoneController({
      phase: "recording",
      asrReady: true,
      elapsedMs: 12_000,
      statusMessage: "实时识别已就绪",
    });
    vi.mocked(microphone.end).mockImplementation(async () => {
      order.push("audio-end");
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={microphone}
      />,
    );
    await screen.findByText("支付服务上线安排");
    await user.click(screen.getByRole("button", { name: "结束并整理" }));

    await waitFor(() => expect(api.endMeeting).toHaveBeenCalledWith("meeting-1"));
    expect(order).toEqual(["audio-end", "api-end"]);
  });

  it("opens recent meetings from the start screen", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onOpenMeeting = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.listMeetings).mockResolvedValue({
      meetings: [{
        meetingId: "meeting-history",
        title: "网关改造评审",
        phase: "ended",
        startedAtMs: 1_700_000_000_000,
        endedAtMs: 1_700_000_300_000,
        createdAtMs: 1_700_000_000_000,
        updatedAtMs: 1_700_000_300_000,
        segmentCount: 12,
        suggestionCount: 2,
        audioDurationMs: 300_000,
        hasMinutes: true,
      }],
    });

    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        microphoneController={microphoneController()}
        onCreateMeeting={() => "rec-new"}
        onOpenMeeting={onOpenMeeting}
      />,
    );

    expect(screen.queryByRole("button", { name: "试用示例" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: "打开会议：网关改造评审" }));
    expect(onOpenMeeting).toHaveBeenCalledWith("meeting-history");

    await user.click(screen.getByRole("button", { name: "删除会议：网关改造评审" }));
    await waitFor(() => expect(api.deleteMeeting).toHaveBeenCalledWith("meeting-history"));
    expect(screen.queryByRole("button", { name: "打开会议：网关改造评审" })).not.toBeInTheDocument();
  });

  it("shows the four-tab review and saved recording after meeting end", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onBackToMeetings = vi.fn();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: null,
      runtime: { ...realSnapshot().runtime, phase: "ended" },
      minutes: {
        meetingId: "meeting-1",
        jobId: "job-minutes",
        version: 1,
        status: "ready",
        markdown: "# 会议结论\n\n确认灰度发布。\n\n## 行动项\n\n- 张三跟进",
        structured: null,
        createdAtMs: 9_000,
        updatedAtMs: 9_000,
      },
      reviewJobs: {
        minutes: { id: "job-minutes", meetingId: "meeting-1", kind: "minutes", status: "succeeded", attempts: 1, maxAttempts: 3, errorClass: null, output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
        approach: { id: "job-approach", meetingId: "meeting-1", kind: "approach", status: "succeeded", attempts: 1, maxAttempts: 3, errorClass: null, output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
        index: { id: "job-index", meetingId: "meeting-1", kind: "index", status: "succeeded", attempts: 1, maxAttempts: 3, errorClass: null, output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
      },
    });

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        onBackToMeetings={onBackToMeetings}
      />,
    );

    expect(await screen.findByRole("tab", { name: "复盘" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 1, name: "会议复盘" })).toBeVisible();
    expect(screen.queryByRole("heading", { level: 1, name: "实时会议" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "复盘", "决策与待办", "会议文字", "录音",
    ]);
    expect(screen.getByRole("heading", { level: 3, name: "会议结论" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 3, name: "行动项" })).toBeVisible();
    expect(screen.getByText(/确认灰度发布/)).toBeVisible();
    expect(screen.getByRole("list")).toHaveTextContent("张三跟进");
    expect(screen.queryByRole("button", { name: "结束并整理" })).not.toBeInTheDocument();

    await screen.findByRole("button", { name: "返回会议列表" });
    await user.click(screen.getByRole("button", { name: "返回会议列表" }));
    expect(onBackToMeetings).toHaveBeenCalledOnce();

    await user.click(screen.getByRole("tab", { name: "会议文字" }));
    await user.click(screen.getByRole("button", { name: /在录音中定位到/ }));
    expect(screen.getByRole("tab", { name: "录音" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(api.getAudio).toHaveBeenCalledWith("meeting-1", expect.any(AbortSignal)));
    expect(document.querySelector("audio")).toHaveAttribute("src", "/audio.wav");
    expect(screen.getByText("录音分片")).toBeVisible();
  });

  it("shows semantic-quality pause instead of provider failure after meeting end", async () => {
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: null,
      runtime: { ...realSnapshot().runtime, phase: "ended" },
      diagnostics: {
        formal_derivation_status: "suppressed_by_asr_semantic_quality",
        degradation_reasons: ["asr_semantic_quality_blocked"],
      },
      reviewJobs: {
        minutes: { id: "job-minutes", meetingId: "meeting-1", kind: "minutes", status: "failed", attempts: 3, maxAttempts: 3, errorClass: "HTTPException", output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
        approach: { id: "job-approach", meetingId: "meeting-1", kind: "approach", status: "failed", attempts: 3, maxAttempts: 3, errorClass: "HTTPException", output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
        index: { id: "job-index", meetingId: "meeting-1", kind: "index", status: "succeeded", attempts: 1, maxAttempts: 3, errorClass: null, output: null, updatedAtMs: 9_000, completedAtMs: 9_000 },
      },
    });

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    expect(await screen.findByText("会议纪要：识别质量不足，已暂停")).toBeVisible();
    expect(screen.getByText("方案与风险：识别质量不足，已暂停")).toBeVisible();
    expect(screen.getByText(/正式会议纪要已暂停/)).toBeVisible();
    expect(screen.getByText(/方案与风险推断已暂停/)).toBeVisible();
    expect(screen.queryByText("会议纪要：生成失败")).not.toBeInTheDocument();
  });

  it("keeps ended input status authoritative over local microphone detection", async () => {
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: null,
      runtime: {
        ...realSnapshot().runtime,
        phase: "ended",
        recording: { state: "idle", label: "录音已保存", level: null, detail: null },
        input: { state: "idle", label: "输入已结束", level: 0, detail: null },
      },
    });
    const microphone = microphoneController({
      phase: "connecting",
      inputLevel: 0.8,
      elapsedMs: 99_000,
      statusMessage: "正在检测麦克风",
    });

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={microphone}
      />,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "会议复盘" })).toBeVisible();
    const statuses = screen.getByLabelText("会议运行状态");
    expect(within(statuses).getByText("输入已结束")).toBeVisible();
    expect(within(statuses).queryByText("检测中")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "结束并整理" })).not.toBeInTheDocument();
  });
});

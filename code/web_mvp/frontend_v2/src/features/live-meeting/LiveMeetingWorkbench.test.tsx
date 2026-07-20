import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MeetingApi } from "../../api/client";
import type { MeetingEventTransport } from "../../api/eventTransport";
import type { FormalAiProvenance, MeetingSnapshot } from "../../domain/events";
import { LiveMeetingWorkbench } from "./LiveMeetingWorkbench";
import type { BrowserMicrophoneController } from "./useBrowserMicrophone";

beforeEach(() => {
  delete window.__TAURI__;
  delete window.__TAURI_INTERNALS__;
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
    if (String(input).endsWith("/v2/storage/preflight")) {
      return Promise.resolve(new Response(JSON.stringify({
        allowed: true,
        writable_capacity_bytes: 4 * 1024 ** 3,
        estimated_meeting_bytes: 110 * 1024 ** 2,
      }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }));
    }
    return Promise.resolve(new Response(JSON.stringify({
      llm: { configured: true, model: "gpt-test" },
      asr: { realtime_asr_available: true, realtime_providers: ["funasr_realtime"] },
    }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }));
  }));
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: {
      enumerateDevices: vi.fn().mockResolvedValue([
        {
          kind: "audioinput",
          deviceId: "mic-1",
          label: "MacBook Microphone",
          groupId: "group-1",
          toJSON: () => ({}),
        },
      ]),
      getUserMedia: vi.fn(),
    },
  });
});

async function confirmMeetingPreflight(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "开始会议" }));
  const dialog = await screen.findByRole("dialog", { name: "准备开始会议" });
  await within(dialog).findByText("本地中文实时识别可用");
  await user.click(within(dialog).getByLabelText("我已告知参会者并确认可以录音"));
  await user.click(within(dialog).getByRole("button", { name: "开始会议" }));
}

function formalAi(
  suffix: string,
  quote = "支付服务周五上线但是负责人还没定",
): FormalAiProvenance {
  return {
    source: "llm_first",
    jobId: `job-${suffix}`,
    batchId: "batch-1",
    provider: "openai_compatible_gateway",
    model: "fixture-model",
    llmCalled: true,
    evidence: {
      segmentIds: ["segment-1"],
      quote,
      evidenceHash: "hash-1",
      stateRevision: 1,
    },
  };
}

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
        formalAi: formalAi("suggestion-1"),
      },
    ],
    decisionCandidates: [
      {
        id: "decision-1",
        text: "先灰度 5%",
        status: "candidate",
        confidence: 0.86,
        evidenceSegmentIds: ["segment-1"],
        evidenceSpans: [{
          segmentId: "segment-1",
          transcriptSeq: 1,
          startMs: 2_000,
          endMs: 6_000,
          quote: "支付服务周五上线但是负责人还没定",
        }],
        updatedAtMs: 7_200,
        formalAi: formalAi("decision-1"),
      },
      {
        id: "decision-2",
        text: "错误率超过 1% 就回滚",
        status: "confirmed",
        confidence: 0.94,
        evidenceSegmentIds: ["segment-1"],
        evidenceSpans: [],
        updatedAtMs: 7_300,
        formalAi: formalAi("decision-2"),
      },
    ],
    actionItems: [{
      id: "action-1",
      text: "补充回滚演练",
      status: "candidate",
      confidence: 0.81,
      evidenceSegmentIds: ["segment-1"],
      evidenceSpans: [],
      owner: "张三",
      deadline: "周五",
      updatedAtMs: 7_400,
      formalAi: formalAi("action-1"),
    }],
    risks: [{
      id: "risk-1",
      text: "P99 延迟可能超标",
      status: "candidate",
      confidence: 0.75,
      evidenceSegmentIds: ["segment-1"],
      evidenceSpans: [],
      mitigation: "超过 900ms 立即回滚",
      updatedAtMs: 7_500,
      formalAi: formalAi("risk-1"),
    }],
    currentTopic: {
      id: "topic-1",
      text: "支付服务上线安排",
      status: "active",
      evidenceSegmentIds: ["segment-1"],
      updatedAtMs: 7_000,
      formalAi: formalAi("topic-1"),
    },
    openQuestions: [
      {
        id: "question-1",
        text: "上线负责人是谁？",
        status: "open",
        evidenceSegmentIds: ["segment-1"],
        updatedAtMs: 7_000,
        formalAi: formalAi("question-1"),
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
    saveMeetingPreparation: vi.fn().mockResolvedValue(undefined),
    importRecording: vi.fn().mockResolvedValue({ meetingId: "imported-meeting" }),
    retryImportJob: vi.fn().mockResolvedValue({
      id: "import-job-1",
      meetingId: "imported-meeting",
      status: "pending",
      stage: "reading",
      progress: 0,
      errorClass: null,
      errorMessage: null,
      retryable: false,
      updatedAtMs: Date.now(),
    }),
    updateMeetingTitle: vi.fn().mockResolvedValue(undefined),
    deleteMeeting: vi.fn().mockResolvedValue(undefined),
    listMeetings: vi.fn().mockResolvedValue({ meetings: [] }),
    getSnapshot: vi.fn().mockResolvedValue(realSnapshot()),
    getTranscript: vi.fn().mockResolvedValue(realSnapshot().segments),
    getSpeakers: vi.fn().mockResolvedValue([]),
    renameSpeaker: vi.fn().mockImplementation(async (meetingId, speakerId, speakerLabel) => ({
      meetingId,
      speakerId,
      speakerLabel,
      ordinal: 1,
      createdAtMs: 1_000,
      updatedAtMs: 2_000,
    })),
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
    exportMeeting: vi.fn().mockResolvedValue(undefined),
    exportDiagnosticBundle: vi.fn().mockResolvedValue(undefined),
    saveReviewDocument: vi.fn().mockImplementation(async (meetingId, kind, expectedRevision, contentJson) => ({
      documentId: `${meetingId}-${kind}`,
      meetingId,
      kind,
      revision: expectedRevision + 1,
      sourceRevision: null,
      contentJson,
      aiVersion: 1,
      userVersion: 1,
      source: "user_final",
      dirtyState: null,
      updatedAtMs: Date.now(),
    })),
    getDocumentRevisions: vi.fn().mockResolvedValue([]),
    regenerateDocument: vi.fn().mockResolvedValue(undefined),
    retryReviewJob: vi.fn().mockResolvedValue(undefined),
    endMeeting: vi.fn().mockResolvedValue(undefined),
    saveSuggestionFeedback: vi.fn().mockResolvedValue(undefined),
    saveFactStatus: vi.fn().mockResolvedValue(undefined),
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
  it("loads, renames, and refreshes a stable speaker through the shared live transcript", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const attributedSnapshot = {
      ...realSnapshot(),
      segments: realSnapshot().segments.map((segment) => ({
        ...segment,
        speakerId: "cluster-a",
        speakerLabel: "Speaker 1",
        speakerConfidence: 0.91,
      })),
    };
    vi.mocked(api.getSnapshot).mockResolvedValue(attributedSnapshot);
    vi.mocked(api.getTranscript).mockResolvedValue(attributedSnapshot.segments);
    vi.mocked(api.getSpeakers)
      .mockResolvedValueOnce([{
        meetingId: "meeting-1",
        speakerId: "cluster-a",
        speakerLabel: "Speaker 1",
        ordinal: 1,
        createdAtMs: 1_000,
        updatedAtMs: 1_000,
      }])
      .mockResolvedValue([{
        meetingId: "meeting-1",
        speakerId: "cluster-a",
        speakerLabel: "张工",
        ordinal: 1,
        createdAtMs: 1_000,
        updatedAtMs: 2_000,
      }]);

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    await user.click(await screen.findByRole("button", { name: "Speaker 1" }));
    const input = screen.getByRole("textbox", { name: "重命名 Speaker 1" });
    await user.clear(input);
    await user.type(input, "张工");
    await user.click(screen.getByRole("button", { name: "保存 Speaker 1 的名称" }));

    await waitFor(() => expect(api.renameSpeaker).toHaveBeenCalledWith("meeting-1", "cluster-a", "张工"));
    expect(await screen.findByRole("button", { name: "张工" })).toBeVisible();
    expect(api.getSpeakers).toHaveBeenCalledWith("meeting-1", expect.any(AbortSignal));
    await waitFor(() => expect(api.getSpeakers).toHaveBeenCalledTimes(2));
  });

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

    await user.click(screen.getByRole("button", { name: "导入录音" }));
    expect(await screen.findByRole("dialog", { name: "导入录音" })).toHaveTextContent("WAV、MP3、M4A、AAC、FLAC、MP4、MOV");
    const input = screen.getByLabelText("选择要导入的录音文件") as HTMLInputElement;
    const file = new File(["audio"], "review.wav", { type: "audio/wav" });
    await user.upload(input, file);
    await user.click(screen.getByRole("button", { name: "开始导入" }));

    await waitFor(() => expect(api.importRecording).toHaveBeenCalledWith(file, "review"));
    await waitFor(() => expect(onOpenMeeting).toHaveBeenCalledWith("imported-meeting"));
  });

  it("shows an import failure on the meeting list instead of hiding it in a toast", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    vi.mocked(api.importRecording).mockRejectedValue(new Error("本地转写不可用"));
    render(<LiveMeetingWorkbench meetingId={null} api={api} transport={transport} />);

    await user.click(screen.getByRole("button", { name: "导入录音" }));
    const input = screen.getByLabelText("选择要导入的录音文件") as HTMLInputElement;
    await user.upload(input, new File(["audio"], "broken.wav", { type: "audio/wav" }));
    await user.click(screen.getByRole("button", { name: "开始导入" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("本地转写不可用");
  });

  it("rejects an empty import before making a network request", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId={null} api={api} transport={transport} />);

    await user.click(screen.getByRole("button", { name: "导入录音" }));
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

    await confirmMeetingPreflight(user);

    await waitFor(() => expect(microphone.start).toHaveBeenCalled());
    expect(onCreateMeeting).toHaveBeenCalledOnce();
    expect(api.createMeeting).toHaveBeenCalledWith("rec_new_meeting", null, "microphone");
    expect(api.saveMeetingPreparation).toHaveBeenCalledWith(
      "rec_new_meeting",
      expect.objectContaining({
        inputDeviceId: "mic-1",
        noticeAcknowledged: true,
      }),
    );
    expect(microphone.start).toHaveBeenCalledWith("rec_new_meeting", {
      inputDeviceId: "mic-1",
      inputSource: "microphone",
    });
    expect(order).toEqual(["meeting-created", "microphone-started"]);
  });

  it("passes the packaged system-audio selection to the single capture owner", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const capture = microphoneController();
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "system_audio_adapter_prepare") {
        return {
          command_status: "ok",
          status: "not_started",
          source: "system_audio",
          helper_present: true,
          captures_audio: false,
          fallback_source: null,
          errors: [],
        };
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        microphoneController={capture}
        onCreateMeeting={() => "rec_system_audio"}
      />,
    );

    await user.click(screen.getByRole("button", { name: "开始会议" }));
    const dialog = await screen.findByRole("dialog", { name: "准备开始会议" });
    await within(dialog).findByText("本地中文实时识别可用");
    await user.click(within(dialog).getByRole("radio", { name: "系统音频" }));
    await waitFor(() => expect(within(dialog).getByRole("radio", { name: "系统音频" }))
      .toHaveAttribute("aria-checked", "true"));
    await user.click(within(dialog).getByLabelText("我已告知参会者并确认可以录音"));
    await user.click(within(dialog).getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(capture.start).toHaveBeenCalledWith("rec_system_audio", {
      inputDeviceId: null,
      inputSource: "system_audio",
    }));
    expect(api.saveMeetingPreparation).toHaveBeenCalledWith(
      "rec_system_audio",
      expect.objectContaining({ inputSource: "system_audio", inputDeviceName: "系统音频" }),
    );
    expect(invokeMock.mock.calls.some(([command]) => String(command).startsWith("mic_adapter_"))).toBe(false);
  });

  it("creates a two-track meeting intent and starts the dual-track capture owner once", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const capture = microphoneController();
    const invokeMock = vi.fn(async (command: string, args?: Record<string, unknown>) => {
      void args;
      if (command === "dual_track_adapter_status") {
        return {
          command_status: "ok",
          status: "not_recording",
          requested_mode: "dual_track",
          active_mode: "none",
          active_track_count: 0,
          microphone: { command_status: "ok", status: "not_started", helper_present: true, captures_audio: false, errors: [] },
          system_audio: { command_status: "ok", status: "not_started", helper_present: true, captures_audio: false, fallback_source: null, errors: [] },
        };
      }
      throw new Error(`unexpected command: ${command}`);
    });
    window.__TAURI__ = {
      core: {
        invoke: <T,>(command: string, args?: Record<string, unknown>) => (
          invokeMock(command, args) as Promise<T>
        ),
      },
    };

    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        microphoneController={capture}
        onCreateMeeting={() => "rec_dual_track"}
      />,
    );

    await user.click(screen.getByRole("button", { name: "开始会议" }));
    const dialog = await screen.findByRole("dialog", { name: "准备开始会议" });
    const dualTrack = await within(dialog).findByRole("radio", { name: "双轨" });
    await user.click(dualTrack);
    await user.click(within(dialog).getByLabelText("我已告知参会者并确认可以录音"));
    await user.click(within(dialog).getByRole("button", { name: "开始会议" }));

    await waitFor(() => expect(api.createMeeting).toHaveBeenCalledWith(
      "rec_dual_track",
      null,
      "dual_track",
    ));
    expect(api.saveMeetingPreparation).toHaveBeenCalledWith(
      "rec_dual_track",
      expect.objectContaining({
        inputSource: "dual_track",
        inputDeviceId: null,
        inputDeviceName: "麦克风 + 系统音频",
      }),
    );
    expect(capture.start).toHaveBeenCalledOnce();
    expect(capture.start).toHaveBeenCalledWith("rec_dual_track", {
      inputDeviceId: null,
      inputSource: "dual_track",
    });
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

    await confirmMeetingPreflight(user);

    await waitFor(() => expect(onBackToMeetings).toHaveBeenCalledOnce());
  });

  it("rolls back a newly created meeting when microphone startup fails", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const microphone = microphoneController();
    const onBackToMeetings = vi.fn();
    vi.mocked(microphone.start).mockRejectedValue(new Error("麦克风权限请求超时"));

    render(
      <LiveMeetingWorkbench
        meetingId={null}
        api={api}
        transport={transport}
        microphoneController={microphone}
        onCreateMeeting={() => "rec_mic_timeout"}
        onBackToMeetings={onBackToMeetings}
      />,
    );

    await confirmMeetingPreflight(user);

    await waitFor(() => {
      expect(api.deleteMeeting).toHaveBeenCalledWith("rec_mic_timeout");
      expect(onBackToMeetings).toHaveBeenCalledOnce();
    });
    expect(screen.getByRole("alert")).toHaveTextContent("麦克风权限请求超时");
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

  it("uses correction status instead of semantic paragraph revision for AI labels", async () => {
    const { api, transport } = dependencies();
    const snapshot = realSnapshot();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...snapshot,
      segments: snapshot.segments.map((segment) => ({
        ...segment,
        revision: 2,
        correctionStatus: "no_change",
      })),
      semanticParagraphs: [{
        meetingId: "meeting-1",
        paragraphId: "paragraph-1",
        revision: 3,
        text: snapshot.segments[0].normalizedText,
        startMs: 2_000,
        endMs: 6_000,
        status: "active",
        checkpointIds: ["segment-1"],
        createdAtMs: 6_100,
        updatedAtMs: 7_000,
      }],
    });

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    expect(await screen.findByText("已检查，无需修改")).toBeVisible();
    expect(screen.queryByText("AI 已校正")).not.toBeInTheDocument();
  });

  it("shows a real correction before/after comparison", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const snapshot = realSnapshot();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...snapshot,
      segments: snapshot.segments.map((segment) => ({
        ...segment,
        correctionStatus: "changed",
        correctionBeforeText: segment.text,
        correctionAfterText: segment.normalizedText,
      })),
    });

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    await user.click(await screen.findByText("查看修正对照"));
    const details = screen.getByText("查看修正对照").closest("details");
    expect(details).not.toBeNull();
    if (details) {
      expect(within(details).getByText("识别")).toBeVisible();
      expect(within(details).getByText("AI")).toBeVisible();
      expect(within(details).getByText("支付服务周五上线但是负责人还没定")).toBeVisible();
    }
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

  it("re-reads diagnostics without creating, uploading, saving, exporting, or invoking AI work", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);
    await screen.findByText("支付服务上线安排");
    const readsBefore = vi.mocked(api.getSnapshot).mock.calls.length;

    await user.click(screen.getByRole("button", { name: "打开运行诊断" }));
    const drawer = screen.getByRole("dialog", { name: "会议连接详情" });
    await user.click(within(drawer).getByRole("button", { name: "重新读取状态" }));

    await waitFor(() => expect(api.getSnapshot).toHaveBeenCalledTimes(readsBefore + 1));
    expect(api.createMeeting).not.toHaveBeenCalled();
    expect(api.saveMeetingPreparation).not.toHaveBeenCalled();
    expect(api.importRecording).not.toHaveBeenCalled();
    expect(api.retryImportJob).not.toHaveBeenCalled();
    expect(api.updateMeetingTitle).not.toHaveBeenCalled();
    expect(api.deleteMeeting).not.toHaveBeenCalled();
    expect(api.exportMeeting).not.toHaveBeenCalled();
    expect(api.exportDiagnosticBundle).not.toHaveBeenCalled();
    expect(api.saveReviewDocument).not.toHaveBeenCalled();
    expect(api.regenerateDocument).not.toHaveBeenCalled();
    expect(api.retryReviewJob).not.toHaveBeenCalled();
    expect(api.endMeeting).not.toHaveBeenCalled();
    expect(api.saveSuggestionFeedback).not.toHaveBeenCalled();
    expect(api.saveFactStatus).not.toHaveBeenCalled();
    expect(api.markUiRendered).not.toHaveBeenCalled();
  });

  it("exports one redacted runtime bundle from the diagnostics drawer", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);
    await screen.findByText("支付服务上线安排");

    await user.click(screen.getByRole("button", { name: "打开运行诊断" }));
    const drawer = screen.getByRole("dialog", { name: "会议连接详情" });
    await user.click(within(drawer).getByRole("button", { name: "导出脱敏诊断包" }));

    await waitFor(() => expect(api.exportDiagnosticBundle).toHaveBeenCalledOnce());
    expect(api.createMeeting).not.toHaveBeenCalled();
    expect(api.importRecording).not.toHaveBeenCalled();
    expect(api.exportMeeting).not.toHaveBeenCalled();
    expect(api.endMeeting).not.toHaveBeenCalled();
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
        source: "llm_first",
        llm_called: true,
        batch_id: "batch-2",
        provider: "openai_compatible_gateway",
        model: "fixture-model",
        evidence: {
          segment_ids: ["segment-1"],
          quote: "请确认回滚窗口",
          evidence_hash: "hash-1",
          state_revision: 1,
        },
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

  it("does not show an unsupported pause command for system-audio capture", async () => {
    const { api, transport } = dependencies();
    const systemAudio = microphoneController({
      phase: "recording",
      asrReady: false,
      inputLevelAvailable: true,
      statusMessage: "已连接但当前无系统声音",
      systemAudioHealth: {
        transportReady: true,
        pcmSeen: true,
        audiblePcmSeen: false,
        asrReady: false,
      },
    });
    systemAudio.inputSource = "system_audio";
    systemAudio.supportsPause = false;

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={systemAudio}
      />,
    );

    await screen.findByText("支付服务上线安排");
    expect(screen.queryByRole("button", { name: "暂停录音" })).not.toBeInTheDocument();
    expect(within(screen.getByLabelText("会议运行状态")).getAllByTitle("已连接但当前无系统声音")).toHaveLength(2);
    expect(screen.getByLabelText("系统音频分层健康状态")).toHaveTextContent("传输已连接PCM已接收声音当前静音识别准备中");
    expect(screen.getByText("已连接但当前无系统声音")).toBeVisible();
    expect(screen.getByRole("button", { name: "结束并整理" })).toBeVisible();
  });

  it("opens recent meetings from the start screen", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onOpenMeeting = vi.fn();
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

    await user.click(screen.getByRole("button", { name: "管理本地数据：网关改造评审" }));
    await user.click(screen.getByRole("radio", { name: /整场会议/ }));
    await user.click(screen.getByRole("button", { name: "删除整场会议" }));
    await waitFor(() => expect(api.deleteMeeting).toHaveBeenCalledWith("meeting-history", "all"));
    expect(screen.queryByRole("button", { name: "打开会议：网关改造评审" })).not.toBeInTheDocument();
  });

  it("shows the four-tab review and saved recording after meeting end", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    const onBackToMeetings = vi.fn();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: null,
      decisionCandidates: [],
      actionItems: [],
      risks: [],
      runtime: { ...realSnapshot().runtime, phase: "ended" },
      minutes: {
        meetingId: "meeting-1",
        jobId: "job-minutes",
        version: 1,
        status: "ready",
        markdown: "# 会议结论\n\n确认灰度发布。\n\n## 行动项\n\n- 张三跟进",
        structured: {
          decisions: ["确认灰度发布"],
          action_items: [{ item: "张三跟进", owner: "张三", deadline: "周五" }],
          risks: ["回滚负责人尚未确认"],
          open_questions: ["P99 阈值是多少"],
        },
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
    expect(screen.getByRole("heading", { level: 1, name: "支付服务发布评审" })).toBeVisible();
    expect(screen.queryByRole("heading", { level: 1, name: "实时会议" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "复盘", "决策与待办", "会议文字", "录音",
    ]);
    expect(screen.getByRole("heading", { level: 3, name: "会议结论" })).toBeVisible();
    expect(screen.getByRole("heading", { level: 3, name: "行动项" })).toBeVisible();
    expect(screen.getByText(/确认灰度发布/)).toBeVisible();
    expect(screen.getByRole("list")).toHaveTextContent("张三跟进");
    expect(screen.queryByRole("button", { name: "结束并整理" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "决策与待办" }));
    expect(screen.getByRole("heading", { level: 2, name: "决策与待办" })).toBeVisible();
    expect(screen.getByText("确认灰度发布")).toBeVisible();
    expect(screen.getByText("张三跟进")).toBeVisible();
    expect(screen.getByText("负责人：张三 · 截止：周五")).toBeVisible();
    expect(screen.getByText("回滚负责人尚未确认")).toBeVisible();
    expect(screen.getByText("P99 阈值是多少")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "导出会议" }));
    await user.click(screen.getByRole("menuitem", { name: "JSON" }));
    await waitFor(() => expect(api.exportMeeting).toHaveBeenCalledWith("meeting-1", "json"));
    expect(screen.getByText("已导出 JSON")).toBeVisible();

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

  it("recovers decisions and actions from markdown-only legacy minutes", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      activePartial: null,
      decisionCandidates: [],
      actionItems: [],
      risks: [],
      runtime: { ...realSnapshot().runtime, phase: "ended" },
      minutes: {
        meetingId: "meeting-1",
        jobId: "legacy-minutes",
        version: 1,
        status: "ready",
        markdown: [
          "# 会议纪要",
          "## 已确认决策",
          "- 先灰度 5%",
          "## 行动项",
          "- 确认回滚负责人 (owner: 李四, deadline: 上线前)",
          "## 风险",
          "- P99 延迟超标",
          "## 未闭环问题",
          "- 监控 owner 是谁",
        ].join("\n"),
        structured: null,
        createdAtMs: 9_000,
        updatedAtMs: 9_000,
      },
    });

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
      />,
    );

    await user.click(await screen.findByRole("tab", { name: "决策与待办" }));
    expect(screen.getByText("先灰度 5%")).toBeVisible();
    expect(screen.getByText("确认回滚负责人")).toBeVisible();
    expect(screen.getByText("负责人：李四 · 截止：上线前")).toBeVisible();
    expect(screen.getByText("P99 延迟超标")).toBeVisible();
    expect(screen.getByText("监控 owner 是谁")).toBeVisible();
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
    expect(screen.getByText("分析建议：识别质量不足，已暂停")).toBeVisible();
    expect(screen.getByText(/正式会议纪要已暂停/)).toBeVisible();
    expect(screen.getByText(/分析建议生成已暂停/)).toBeVisible();
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

    expect(await screen.findByRole("heading", { level: 1, name: "支付服务发布评审" })).toBeVisible();
    const statuses = screen.getByLabelText("会议运行状态");
    expect(within(statuses).getByText("输入已结束")).toBeVisible();
    expect(within(statuses).queryByText("检测中")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "结束并整理" })).not.toBeInTheDocument();
  });

  it("does not enter review until the backend confirms that the meeting ended", async () => {
    const { api, transport } = dependencies();
    const microphone = microphoneController({
      phase: "ended",
      asrReady: false,
      statusMessage: "录音已安全封存，正在整理",
    });

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={microphone}
      />,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "支付服务发布评审" })).toBeVisible();
    expect(screen.queryByRole("heading", { level: 1, name: "会议复盘" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "结束并整理" })).toBeVisible();
  });

  it("keeps the meeting end command available after microphone interruption", async () => {
    const { api, transport } = dependencies();
    vi.mocked(api.getSnapshot).mockResolvedValue({
      ...realSnapshot(),
      runtime: {
        ...realSnapshot().runtime,
        phase: "unknown",
        recording: { state: "error", label: "录音中断", level: null, detail: "连接已断开" },
        input: { state: "error", label: "不可用", level: 0, detail: "连接已断开" },
      },
    });
    const microphone = microphoneController({
      phase: "error",
      error: "系统麦克风已停止，会议文字可能不再更新",
      statusMessage: "系统麦克风已停止，会议文字可能不再更新",
    });

    render(
      <LiveMeetingWorkbench
        meetingId="meeting-1"
        api={api}
        transport={transport}
        microphoneController={microphone}
      />,
    );

    expect(await screen.findByRole("button", { name: "重新开始录音" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: "结束并整理" })).toHaveLength(1);
  });

  it("shows realtime meeting facts with strict candidate labels and persists confirm or dismiss actions", async () => {
    const user = userEvent.setup();
    const { api, transport } = dependencies();

    render(<LiveMeetingWorkbench meetingId="meeting-1" api={api} transport={transport} />);

    const facts = await screen.findByRole("region", { name: "会议事实" });
    expect(within(facts).getByText("候选决策")).toBeVisible();
    expect(within(facts).getByText("已确认决策")).toBeVisible();
    expect(within(facts).getByText("先灰度 5%")).toBeVisible();
    expect(within(facts).getByText("错误率超过 1% 就回滚")).toBeVisible();
    expect(within(facts).getByText("负责人：张三 · 截止：周五")).toBeVisible();
    expect(within(facts).getByText("应对：超过 900ms 立即回滚")).toBeVisible();

    await user.click(within(facts).getByRole("button", { name: "查看“先灰度 5%”的依据" }));
    const segment = screen.getByText("支付服务周五上线，但是负责人还没确定。").closest(".transcript-segment");
    await waitFor(() => expect(segment).toHaveClass("is-evidence-target"));

    await user.click(within(facts).getByRole("button", { name: "确认候选决策“先灰度 5%”" }));
    expect(api.saveFactStatus).toHaveBeenCalledWith("meeting-1", "decision", "decision-1", "confirmed");

    await user.click(within(facts).getByRole("button", { name: "忽略候选风险“P99 延迟可能超标”" }));
    expect(api.saveFactStatus).toHaveBeenCalledWith("meeting-1", "risk", "risk-1", "dismissed");
    await waitFor(() => expect(within(facts).queryByText("P99 延迟可能超标")).not.toBeInTheDocument());

    expect(screen.getByRole("heading", { name: "AI 实时建议" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "未闭环问题" })).toBeVisible();
  });
});

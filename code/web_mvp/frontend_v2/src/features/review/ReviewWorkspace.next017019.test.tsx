import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInitialMeetingState } from "../../domain/reducer";
import type {
  MeetingViewState,
  ReviewDocument,
  ReviewDocumentKind,
  ReviewDocumentSource,
  ReviewJob,
  ReviewJobKind,
  TranscriptSegment,
} from "../../domain/events";
import { ReviewWorkspace } from "./ReviewWorkspace";

function transcriptSegment(text = "原始会议文字"): TranscriptSegment {
  return {
    meetingId: "meeting-next-017",
    segmentId: "segment-1",
    finalId: "final-1",
    transcriptSeq: 1,
    text,
    normalizedText: text,
    startedAtMs: 1_000,
    endedAtMs: 2_000,
    revision: 1,
    evidenceHash: "hash-1",
    speakerId: "cluster-a",
    speakerLabel: "Speaker 1",
    speakerConfidence: 0.92,
    createdAtMs: 1_000,
    updatedAtMs: 2_000,
  };
}

function reviewDocument(
  kind: ReviewDocumentKind,
  contentJson: unknown,
  revision = 3,
  source: ReviewDocumentSource = "user_final",
): ReviewDocument {
  return {
    documentId: `document-${kind}`,
    meetingId: "meeting-next-017",
    kind,
    revision,
    sourceRevision: 1,
    contentJson,
    aiVersion: 1,
    userVersion: source === "user_final" ? 1 : 0,
    source,
    dirtyState: null,
    updatedAtMs: 2_000,
  };
}

function reviewJob(kind: ReviewJobKind, status: ReviewJob["status"] = "failed"): ReviewJob {
  return {
    id: `job-${kind}`,
    meetingId: "meeting-next-017",
    kind,
    status,
    attempts: status === "failed" ? 2 : 1,
    maxAttempts: 3,
    errorClass: status === "failed" ? "ProviderTimeout" : null,
    errorMessage: null,
    retryable: status === "failed",
    output: status === "succeeded" ? {} : null,
    updatedAtMs: 2_000,
    completedAtMs: status === "running" ? null : 2_000,
  };
}

function endedState(): MeetingViewState {
  const state = createInitialMeetingState("meeting-next-017");
  return {
    ...state,
    runtime: { ...state.runtime, phase: "ended" },
  };
}

function workspaceProps(state: MeetingViewState, overrides: Partial<React.ComponentProps<typeof ReviewWorkspace>> = {}) {
  return {
    state,
    onReloadTranscript: vi.fn(),
    onReloadAudio: vi.fn(),
    onExport: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

beforeEach(() => {
  const values = new Map<string, string>();
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      get length() { return values.size; },
      clear: () => values.clear(),
      getItem: (key: string) => values.get(key) ?? null,
      key: (index: number) => [...values.keys()][index] ?? null,
      removeItem: (key: string) => values.delete(key),
      setItem: (key: string, value: string) => values.set(key, value),
    } satisfies Storage,
  });
});

describe("ReviewWorkspace NEXT-017 document editing", () => {
  it("auto-saves minutes, decisions, action items, risks, and transcript to their own document kinds", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.fullTranscript = [transcriptSegment()];
    state.fullTranscriptState = "ready";
    state.documents = {
      minutes: reviewDocument("minutes", { markdown: "# AI 复盘" }),
      decisions: reviewDocument("decisions", { decisions: [{ id: "decision-1", text: "原决策", status: "confirmed" }] }),
      action_items: reviewDocument("action_items", { action_items: [{ id: "action-1", text: "原待办", status: "open" }] }),
      risks: reviewDocument("risks", { risks: [{ id: "risk-1", text: "原风险", status: "open" }] }),
      transcript: reviewDocument("transcript", { segments: [{ segment_id: "segment-1", text: "原始会议文字", started_at_ms: 1_000, ended_at_ms: 2_000 }] }),
    };
    const onSaveDocument = vi.fn(async (kind: ReviewDocumentKind, expectedRevision: number, content: unknown) =>
      reviewDocument(kind, content, expectedRevision + 1),
    );
    render(<ReviewWorkspace {...workspaceProps(state, { onSaveDocument })} />);

    await user.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议复盘" }), { target: { value: "# 人工复盘" } });
    await waitFor(() => expect(onSaveDocument).toHaveBeenCalledWith("minutes", 3, { markdown: "# 人工复盘" }), { timeout: 2_500 });

    await user.click(screen.getByRole("tab", { name: "决策与待办" }));
    await user.click(screen.getByRole("button", { name: "编辑最终稿" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑决策 1" }), { target: { value: "人工决策" } });
    fireEvent.change(screen.getByRole("textbox", { name: "编辑行动项 1" }), { target: { value: "人工待办" } });
    fireEvent.change(screen.getByRole("textbox", { name: "编辑风险 1" }), { target: { value: "人工风险" } });
    await waitFor(() => {
      expect(onSaveDocument).toHaveBeenCalledWith("decisions", 3, expect.objectContaining({ decisions: [expect.objectContaining({ text: "人工决策" })] }));
      expect(onSaveDocument).toHaveBeenCalledWith("action_items", 3, expect.objectContaining({ action_items: [expect.objectContaining({ text: "人工待办" })] }));
      expect(onSaveDocument).toHaveBeenCalledWith("risks", 3, expect.objectContaining({ risks: [expect.objectContaining({ text: "人工风险" })] }));
    }, { timeout: 2_500 });

    await user.click(screen.getByRole("tab", { name: "会议文字" }));
    await user.click(screen.getByRole("button", { name: "编辑最终文字" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议文字第 1 段" }), { target: { value: "人工最终文字" } });
    await waitFor(() => expect(onSaveDocument).toHaveBeenCalledWith(
      "transcript",
      3,
      expect.objectContaining({
        segments: [expect.objectContaining({
          text: "人工最终文字",
          started_at_ms: 1_000,
          ended_at_ms: 2_000,
          speaker_id: "cluster-a",
          speaker_label: "Speaker 1",
          speaker_confidence: 0.92,
        })],
      }),
    ), { timeout: 2_500 });
  }, 10_000);

  it("recovers a locally persisted draft after refresh and saves it with its base revision", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.documents = {
      ...state.documents,
      decisions: reviewDocument("decisions", { decisions: [{ id: "decision-server", text: "服务端决策", status: "confirmed" }] }, 4),
    };
    window.localStorage.setItem(
      "meeting-copilot:review-draft:meeting-next-017:decisions",
      JSON.stringify({
        baseRevision: 4,
        value: [{ id: "decision-local", text: "刷新后恢复的决策", status: "confirmed", evidenceSegmentId: null }],
      }),
    );
    const onSaveDocument = vi.fn(async (kind: ReviewDocumentKind, expectedRevision: number, content: unknown) =>
      reviewDocument(kind, content, expectedRevision + 1),
    );
    render(<ReviewWorkspace {...workspaceProps(state, { onSaveDocument })} />);

    await user.click(screen.getByRole("tab", { name: "决策与待办" }));
    await user.click(screen.getByRole("button", { name: "编辑最终稿" }));

    expect(screen.getByText("已恢复上次未保存的决策草稿。")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "编辑决策 1" })).toHaveValue("刷新后恢复的决策");
    await user.click(screen.getByRole("button", { name: "完成编辑" }));
    await waitFor(() => expect(onSaveDocument).toHaveBeenCalledWith(
      "decisions",
      4,
      expect.objectContaining({ decisions: [expect.objectContaining({ text: "刷新后恢复的决策" })] }),
    ));
  });

  it("keeps the local draft and advances to the server revision after a 409 conflict", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.documents = {
      ...state.documents,
      minutes: reviewDocument("minutes", { markdown: "# 服务端复盘" }, 6),
    };
    const conflict = Object.assign(new Error("文档版本冲突"), {
      status: 409,
      body: { detail: { current_revision: 7 } },
    });
    const onSaveDocument = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockImplementationOnce(async (kind: ReviewDocumentKind, expectedRevision: number, content: unknown) =>
        reviewDocument(kind, content, expectedRevision + 1),
      );
    render(<ReviewWorkspace {...workspaceProps(state, { onSaveDocument })} />);

    await user.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议复盘" }), { target: { value: "# 本地未保存复盘" } });
    await waitFor(() => expect(screen.getByText(/服务端已有新版本/)).toBeVisible(), { timeout: 2_500 });
    expect(screen.getByRole("textbox", { name: "编辑会议复盘" })).toHaveValue("# 本地未保存复盘");

    await user.click(screen.getByRole("button", { name: "重试保存" }));

    await waitFor(() => expect(onSaveDocument).toHaveBeenNthCalledWith(2, "minutes", 7, { markdown: "# 本地未保存复盘" }));
    await waitFor(() => expect(screen.getByText(/已保存/)).toBeVisible());
  });

  it("shows the content of a selected historical revision", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.documents = {
      ...state.documents,
      minutes: reviewDocument("minutes", { markdown: "# 当前版本" }, 3),
    };
    const onLoadDocumentRevisions = vi.fn().mockResolvedValue([{
      revision: 2,
      author: "user",
      source: "user_final",
      contentJson: { markdown: "# 历史人工版本\n\n保留的结论" },
      patch: null,
      createdAtMs: 1_000,
    }]);
    render(<ReviewWorkspace {...workspaceProps(state, { onLoadDocumentRevisions })} />);

    await user.click(screen.getByRole("button", { name: "查看复盘版本历史" }));
    await user.click(await screen.findByRole("button", { name: "查看版本 2 内容" }));

    expect(screen.getByText("历史人工版本")).toBeVisible();
    expect(screen.getByText("保留的结论")).toBeVisible();
  });

  it.each([
    ["decisions", "决策", "决策与待办", "历史决策"],
    ["action_items", "行动项", "决策与待办", "历史待办"],
    ["risks", "风险", "决策与待办", "历史风险"],
    ["transcript", "完整文字", "会议文字", "历史文字"],
  ] as Array<[ReviewDocumentKind, string, string, string]>) (
    "shows version content for the %s document",
    async (kind, label, tab, historicalText) => {
      const user = userEvent.setup();
      const state = endedState();
      state.fullTranscript = [transcriptSegment()];
      state.documents = {
        ...state.documents,
        [kind]: reviewDocument(kind, { items: [{ text: `当前${label}` }] }),
      };
      const onLoadDocumentRevisions = vi.fn().mockResolvedValue([{
        revision: 2,
        author: "user",
        source: "user_final",
        contentJson: { items: [{ text: historicalText }] },
        patch: null,
        createdAtMs: 1_000,
      }]);
      render(<ReviewWorkspace {...workspaceProps(state, { onLoadDocumentRevisions })} />);

      await user.click(screen.getByRole("tab", { name: tab }));
      await user.click(screen.getByRole("button", { name: `查看${label}版本历史` }));
      await user.click(await screen.findByRole("button", { name: "查看版本 2 内容" }));

      expect(onLoadDocumentRevisions).toHaveBeenCalledWith(kind);
      expect(screen.getByLabelText(`${label}版本 2 内容`)).toHaveTextContent(historicalText);
    },
  );

  it("does not replace a visible user_final when regeneration returns a newer AI draft", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.documents = {
      ...state.documents,
      minutes: reviewDocument("minutes", { markdown: "# 人工最终稿" }, 4, "user_final"),
    };
    const onRegenerateDocument = vi.fn().mockResolvedValue(undefined);
    const props = workspaceProps(state, { onRegenerateDocument });
    const view = render(<ReviewWorkspace {...props} />);

    await user.click(screen.getByRole("button", { name: "重新生成会议纪要" }));
    expect(onRegenerateDocument).toHaveBeenCalledWith("minutes");

    const regeneratedState = { ...state, documents: { ...state.documents } };
    regeneratedState.documents.minutes = reviewDocument("minutes", { markdown: "# 新的 AI 初稿" }, 5, "ai_generated");
    view.rerender(<ReviewWorkspace {...workspaceProps(regeneratedState, { onRegenerateDocument })} />);

    expect(screen.getByText("人工最终稿")).toBeVisible();
    expect(screen.queryByText("新的 AI 初稿")).not.toBeInTheDocument();
  });

  it("scopes user_final protection and local draft state to one meeting", () => {
    const firstState = endedState();
    firstState.documents = {
      ...firstState.documents,
      minutes: reviewDocument("minutes", { markdown: "# 第一场人工稿" }, 4, "user_final"),
    };
    const view = render(<ReviewWorkspace {...workspaceProps(firstState)} />);

    const secondState = createInitialMeetingState("meeting-next-017-second");
    secondState.runtime = { ...secondState.runtime, phase: "ended" };
    secondState.documents = {
      minutes: {
        ...reviewDocument("minutes", { markdown: "# 第二场 AI 初稿" }, 1, "ai_generated"),
        documentId: "document-second-minutes",
        meetingId: secondState.meetingId,
      },
    };
    view.rerender(<ReviewWorkspace {...workspaceProps(secondState)} />);

    expect(screen.getByText("第二场 AI 初稿")).toBeVisible();
    expect(screen.queryByText("第一场人工稿")).not.toBeInTheDocument();
  });
});

describe("ReviewWorkspace NEXT-019 independent retries", () => {
  it.each([
    ["minutes", "会议纪要"],
    ["approach", "分析建议"],
    ["index", "内容索引"],
  ] as Array<[ReviewJobKind, string]>) (
    "retries only the failed %s artifact and refreshes its state",
    async (kind, label) => {
      const user = userEvent.setup();
      const state = endedState();
      state.reviewJobs = {
        minutes: reviewJob("minutes", kind === "minutes" ? "failed" : "succeeded"),
        approach: reviewJob("approach", kind === "approach" ? "failed" : "succeeded"),
        index: reviewJob("index", kind === "index" ? "failed" : "succeeded"),
      };
      const onRetryReviewJob = vi.fn().mockResolvedValue(undefined);
      const onRefresh = vi.fn().mockResolvedValue(undefined);
      render(<ReviewWorkspace {...workspaceProps(state, { onRetryReviewJob, onRefresh })} />);

      await user.click(screen.getByRole("button", { name: `重试${label}任务` }));

      expect(onRetryReviewJob).toHaveBeenCalledTimes(1);
      expect(onRetryReviewJob).toHaveBeenCalledWith(kind);
      await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
      const progress = screen.getByRole("region", { name: "会议整理进度" });
      for (const completedLabel of ["会议纪要", "分析建议", "内容索引"].filter((item) => item !== label)) {
        expect(progress).toHaveTextContent(`${completedLabel}：已完成`);
      }
    },
  );

  it("keeps retry failures isolated and leaves the other artifact controls available", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.reviewJobs = {
      minutes: reviewJob("minutes"),
      approach: reviewJob("approach"),
      index: reviewJob("index"),
    };
    const onRetryReviewJob = vi.fn().mockRejectedValue(new Error("Provider 仍不可用"));
    render(<ReviewWorkspace {...workspaceProps(state, { onRetryReviewJob })} />);

    await user.click(screen.getByRole("button", { name: "重试分析建议任务" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Provider 仍不可用");
    expect(screen.getByRole("button", { name: "重试会议纪要任务" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "重试内容索引任务" })).toBeEnabled();
  });

  it("still allows an index retry when semantic quality pauses minutes and analysis", () => {
    const state = endedState();
    state.diagnostics = {
      formal_derivation_status: "suppressed_by_asr_semantic_quality",
      degradation_reasons: ["asr_semantic_quality_blocked"],
    };
    state.reviewJobs.index = reviewJob("index");

    render(<ReviewWorkspace {...workspaceProps(state, { onRetryReviewJob: vi.fn().mockResolvedValue(undefined) })} />);

    expect(screen.getByRole("button", { name: "重试内容索引任务" })).toBeEnabled();
  });

  it("keeps realtime facts visible when minutes fail", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.reviewJobs.minutes = reviewJob("minutes");
    state.decisionCandidates = [{
      id: "decision-realtime",
      text: "实时已确认的决策",
      status: "confirmed",
      confidence: 0.9,
      evidenceSegmentIds: ["segment-1"],
      evidenceSpans: [],
      updatedAtMs: 2_000,
    }];
    render(<ReviewWorkspace {...workspaceProps(state, { onRetryReviewJob: vi.fn().mockResolvedValue(undefined) })} />);

    await user.click(screen.getByRole("tab", { name: "决策与待办" }));

    expect(screen.getByText("实时已确认的决策")).toBeVisible();
    expect(screen.queryByText("会议纪要生成失败，未能提取决策与行动项。")).not.toBeInTheDocument();
  });
});

describe("ReviewWorkspace approach artifact semantics", () => {
  it("names approach cards as analysis suggestions and identifies each actual card type", () => {
    const state = endedState();
    state.approach.cards = [
      { cardId: "alternative", cardType: "approach.alternative", suggestionText: "考虑增加 50% 灰度档", triggerReason: "当前只有 5%", evidenceQuote: null, evidenceSegmentIds: [], confidence: 0.9 },
      { cardId: "risk", cardType: "approach.risk", suggestionText: "补充回滚阈值", triggerReason: "回滚条件未明确", evidenceQuote: null, evidenceSegmentIds: [], confidence: 0.9 },
      { cardId: "consideration", cardType: "approach.consideration", suggestionText: "确认监控窗口", triggerReason: null, evidenceQuote: null, evidenceSegmentIds: [], confidence: 0.9 },
    ];

    render(<ReviewWorkspace {...workspaceProps(state)} />);

    expect(screen.getByRole("heading", { name: "分析建议" })).toBeVisible();
    expect(screen.getByText("备选方案")).toBeVisible();
    expect(screen.getByText("风险提示")).toBeVisible();
    expect(screen.getByText("考虑事项")).toBeVisible();
    expect(screen.queryByRole("heading", { name: "方案与风险" })).not.toBeInTheDocument();
  });
});

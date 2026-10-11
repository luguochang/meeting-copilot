import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { KnowledgeApi, MeetingKnowledgeContext } from "../../api/knowledge";
import type { ProjectOutcomesApi } from "../../api/projectOutcomes";
import type { ProjectReconcileApi } from "../../api/projectReconcile";
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

describe("ReviewWorkspace document editing", () => {
  it("shows each stored decision confirmation status in read mode without treating candidates or unknown states as confirmed", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.documents = { ...state.documents, decisions: reviewDocument("decisions", { decisions: [
      { id: "accepted", text: "已明确采用异步处理", status: "confirmed" },
      { id: "proposed", text: "整体重试仍是候选方案", status: "candidate" },
      { id: "discarded", text: "已放弃原有串行方案", status: "dismissed" },
      { id: "unknown", text: "历史导入状态无法识别", status: "future-status" },
    ] }) };
    const onSaveDocument = vi.fn();
    render(<ReviewWorkspace {...workspaceProps(state, { onSaveDocument })} />);

    await user.click(screen.getByRole("tab", { name: "决策与待办" }));

    expect(screen.getByRole("heading", { name: "决策记录" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "已确认决策" })).not.toBeInTheDocument();
    for (const [index, text, label] of [
      [1, "已明确采用异步处理", "已确认"], [2, "整体重试仍是候选方案", "待确认"],
      [3, "已放弃原有串行方案", "已放弃"], [4, "历史导入状态无法识别", "状态待核对"],
    ] as const) {
      const article = screen.getByText(text).closest("article")!;
      expect(within(article).getByLabelText(`决策 ${index} 状态：${label}`)).toBeVisible();
      expect(within(article).getByText(label)).toBeVisible();
      if (index !== 1) expect(within(article).queryByText("已确认")).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("textbox", { name: "编辑决策 1" })).not.toBeInTheDocument();
    expect(onSaveDocument).not.toHaveBeenCalled();
  });

  it("archives a meeting with no preselected project only after explicit scope binding and a saved human final", async () => {
    const user = userEvent.setup();
    let context: MeetingKnowledgeContext = { meetingId: "meeting-next-017", projectId: null, folderId: null, documentIds: null, selectionMode: "none", revision: 0 };
    const emptyPage = { items: [], hasMore: false, nextCursor: null };
    const knowledgeApi = {
      getMeetingContext: vi.fn(async () => context),
      saveMeetingContext: vi.fn(async (_meeting: string, selection: { projectId: string | null; folderId: string | null; documentIds: string[] | null }) => { context = { ...context, ...selection, selectionMode: "documents", revision: 1 }; return context; }),
      listProjects: vi.fn().mockResolvedValue({ ...emptyPage, items: [{ id: "project", name: "产品项目", description: "", revision: 1, archivedAtMs: null }] }),
      listFolders: vi.fn().mockResolvedValue(emptyPage), listDocuments: vi.fn().mockResolvedValue(emptyPage),
      importFinal: vi.fn().mockResolvedValue({ document: { id: "imported-final", projectId: "project" }, version: {}, created: true, alreadyImported: false }),
    } as unknown as KnowledgeApi;
    const projectApi = { listState: vi.fn().mockResolvedValue({ ...emptyPage, revision: 1 }) } as unknown as ProjectOutcomesApi;
    const reconcileApi = { list: vi.fn().mockResolvedValue(emptyPage), create: vi.fn() } as unknown as ProjectReconcileApi;
    const onSave = vi.fn(); const openDocument = vi.fn();
    function Host() {
      const [state, setState] = useState(() => ({ ...endedState(), documents: { minutes: reviewDocument("minutes", { markdown: "待人工确认的会议初稿" }, 1, "ai_generated") } }));
      return <ReviewWorkspace {...workspaceProps(state)} knowledgeApi={knowledgeApi} projectApi={projectApi} reconcileApi={reconcileApi} onOpenKnowledgeDocument={openDocument} onSaveDocument={async (kind, revision, content) => { const saved = { ...reviewDocument(kind, content, revision + 1), dirtyState: "saved" as const }; onSave(kind, revision, content); setState((value) => ({ ...value, documents: { ...value.documents, [kind]: saved } })); return saved; }} />;
    }
    render(<Host />);
    expect(await screen.findByText(/会前无需选择项目/)).toBeVisible();
    expect(knowledgeApi.saveMeetingContext).not.toHaveBeenCalled(); expect(knowledgeApi.importFinal).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "把已保存人工终稿加入资料库" })).toBeDisabled();
    await user.click(screen.getByText("选择会后归档项目"));
    await screen.findByRole("option", { name: "产品项目" });
    await user.selectOptions(screen.getByRole("combobox", { name: "会议资料项目" }), "project");
    await user.selectOptions(screen.getByRole("combobox", { name: "会议资料选择方式" }), "none");
    expect(knowledgeApi.saveMeetingContext).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "保存资料范围" }));
    await waitFor(() => expect(knowledgeApi.saveMeetingContext).toHaveBeenCalledWith("meeting-next-017", expect.objectContaining({ projectId: "project", folderId: null, documentIds: [] }), 0));
    await waitFor(() => expect(screen.queryByText("选择会后归档项目")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "把已保存人工终稿加入资料库" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议复盘" }), { target: { value: "已确认下单与通知失败隔离，待办由后端负责。" } });
    await user.click(screen.getByRole("button", { name: "退出编辑" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "把已保存人工终稿加入资料库" })).toBeEnabled());
    expect(onSave).toHaveBeenCalledWith("minutes", 1, { markdown: "已确认下单与通知失败隔离，待办由后端负责。" });
    await user.click(screen.getByRole("button", { name: "把已保存人工终稿加入资料库" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "导入当前原文为草稿" })).toBeEnabled());
    expect(screen.getByRole("combobox", { name: "入库项目" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "入库项目" })).toHaveValue("project");
    await user.click(screen.getByRole("button", { name: "导入当前原文为草稿" }));
    expect(await screen.findByText("已保存为资料草稿，尚未用于会议。")).toBeVisible();
    expect(knowledgeApi.importFinal).toHaveBeenCalledWith("meeting-next-017", { documentKind: "minutes", expectedSourceVersion: 1, folderId: null });
    expect(knowledgeApi.saveMeetingContext).toHaveBeenCalledOnce(); expect(reconcileApi.create).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "到资料库检查并确认" }));
    expect(openDocument).toHaveBeenCalledWith({ documentId: "imported-final", projectId: "project" });
  });
  it("keeps failed edits open, blocks exporting an old version, then exports after explicit save retry", async () => {
    const user = userEvent.setup(); const state = endedState();
    state.documents = { minutes: reviewDocument("minutes", { markdown: "服务端旧版本" }) };
    const onSaveDocument = vi.fn().mockRejectedValueOnce(new Error("磁盘写入失败")).mockImplementationOnce(async (kind: ReviewDocumentKind, revision: number, content: unknown) => reviewDocument(kind, content, revision + 1));
    const onExport = vi.fn().mockResolvedValue(undefined);
    render(<ReviewWorkspace {...workspaceProps(state, { onSaveDocument, onExport })} />);
    await user.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议复盘" }), { target: { value: "人工确认的新版本" } });
    await user.click(screen.getByRole("button", { name: "退出编辑" }));
    expect(await screen.findByText("磁盘写入失败")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "编辑会议复盘" })).toHaveValue("人工确认的新版本");
    await user.click(screen.getByRole("button", { name: "导出会议" }));
    await user.click(screen.getByRole("menuitem", { name: "Markdown" }));
    expect(onExport).not.toHaveBeenCalled();
    expect(screen.getByText(/还有修改尚未保存/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "重试保存" }));
    await waitFor(() => expect(screen.queryByText("磁盘写入失败")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "退出编辑" }));
    expect(screen.queryByRole("textbox", { name: "编辑会议复盘" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "导出会议" }));
    await user.click(screen.getByRole("menuitem", { name: "Markdown" }));
    await waitFor(() => expect(onExport).toHaveBeenCalledWith("markdown"));
    expect(onSaveDocument).toHaveBeenNthCalledWith(2, "minutes", 3, { markdown: "人工确认的新版本" });
  });
  it("does not label raw text or an unsaved manual draft as confirmed", async () => {
    const user = userEvent.setup(); const state = endedState(); state.fullTranscript = [transcriptSegment()];
    const view = render(<ReviewWorkspace {...workspaceProps(state)} />);
    expect(screen.getByText("已有文字，待人工核对")).toBeVisible();
    expect(screen.queryByText("文字已确认")).not.toBeInTheDocument();
    const manual = { ...reviewDocument("transcript", { segments: [{ segment_id: "segment-1", text: "人工保存文字" }] }), dirtyState: "saved" };
    view.rerender(<ReviewWorkspace {...workspaceProps({ ...state, documents: { transcript: manual } })} />);
    expect(screen.getByText("人工文字已保存")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "会议文字" }));
    await user.click(screen.getByRole("button", { name: "编辑最终文字" }));
    fireEvent.change(screen.getByRole("textbox", { name: "编辑会议文字第 1 段" }), { target: { value: "尚未保存的修改" } });
    expect(screen.getByText("文字修改尚未保存")).toBeVisible();
    expect(screen.queryByText("人工文字已保存")).not.toBeInTheDocument();
  });
  it("shows durable source invalidation even when a later AI generation updated the timestamp", () => {
    const state = endedState();
    state.documents = { minutes: { ...reviewDocument("minutes", { markdown: "保留人工最终稿" }), dirtyState: "stale", updatedAtMs: 99_000 } };
    render(<ReviewWorkspace {...workspaceProps(state)} />);
    expect(screen.getByText(/会议文字已更新，当前复盘可能基于旧版本/)).toBeVisible();
    expect(screen.getByText("保留人工最终稿")).toBeVisible();
  });
  it("shows corrected canonical text instead of a stale AI document and warns about older minutes", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.fullTranscriptState = "ready";
    state.fullTranscript = [{ ...transcriptSegment("支持一百病发"), normalizedText: "支持一百并发", correctionStatus: "changed", correctionUpdatedAtMs: 5_000 }];
    state.documents = {
      transcript: reviewDocument("transcript", { segments: [{ segment_id: "segment-1", text: "支持一百病发" }] }, 1, "ai_generated"),
      minutes: reviewDocument("minutes", { markdown: "旧会议纪要" }, 1, "ai_generated"),
    };
    state.semanticParagraphs = [{
      meetingId: state.meetingId, paragraphId: "paragraph-1", revision: 1, text: "支持一百病发",
      startMs: 1_000, endMs: 2_000, status: "stable", checkpointIds: ["segment-1"], createdAtMs: 1_000, updatedAtMs: 2_000,
    }];
    render(<ReviewWorkspace {...workspaceProps(state)} />);
    expect(screen.getByText(/会议文字已更新，当前复盘可能基于旧版本/)).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "会议文字" }));
    expect(screen.getByText("支持一百并发")).toBeVisible();
    expect(screen.getByText("支持一百病发")).not.toBeVisible();
    await user.click(screen.getByText("查看识别原文"));
    expect(screen.getByText("支持一百病发")).toBeVisible();
    await user.click(screen.getByRole("tab", { name: "会中建议与问答" }));
    expect(screen.getByText("本场会议还没有问答与建议记录。")).toBeVisible();
  });

  it("auto-saves minutes, decisions, action items, risks, and transcript to their own document kinds", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.fullTranscript = [transcriptSegment()];
    state.fullTranscriptState = "ready";
    state.semanticParagraphs = [{
      meetingId: state.meetingId,
      paragraphId: "stale-paragraph",
      revision: 1,
      text: "不应覆盖用户最终稿的旧语义段落",
      startMs: 1_000,
      endMs: 2_000,
      status: "stable",
      checkpointIds: ["segment-1"],
      createdAtMs: 1_000,
      updatedAtMs: 2_000,
    }];
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
    expect(screen.getByText("原始会议文字")).toBeVisible();
    expect(screen.queryByText("不应覆盖用户最终稿的旧语义段落")).not.toBeInTheDocument();
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

describe("ReviewWorkspace independent retries", () => {
  it("labels failed transcript correction as raw ASR and retries it explicitly", async () => {
    const user = userEvent.setup();
    const state = endedState();
    state.segments = [{
      ...transcriptSegment(),
      correctionStatus: "failed_preserved_original",
      correctionErrorClass: "correction_provider_rates_not_configured",
    }];
    const onRetryTranscriptCorrection = vi.fn().mockResolvedValue(undefined);
    const onReloadTranscript = vi.fn();
    const onRefresh = vi.fn().mockResolvedValue(undefined);

    render(<ReviewWorkspace {...workspaceProps(state, {
      onRetryTranscriptCorrection,
      onReloadTranscript,
      onRefresh,
    })} />);

    expect(screen.getByRole("alert")).toHaveTextContent("当前文字是原始 ASR");
    expect(screen.getByRole("alert")).toHaveTextContent("Provider 未配置价格费率");
    expect(screen.getByRole("button", { name: "重试 AI 校对" })).toBeEnabled();
    await user.click(screen.getByRole("tab", { name: "会议文字" }));
    expect(screen.getByText("原始 ASR（校对失败）")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "重试 AI 校对" }));
    expect(onRetryTranscriptCorrection).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(onRefresh).toHaveBeenCalledTimes(1));
    expect(onReloadTranscript).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["minutes", "会议纪要"],
    ["approach", "分析建议"],
    ["index", "内容索引"],
  ] as Array<[ReviewJobKind, string]>) (
    "retries only the failed %s artifact and refreshes its state",
    async (kind, label) => {
      const user = userEvent.setup();
      const state = endedState();
      state.segments = [transcriptSegment()];
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
    state.segments = [transcriptSegment()];
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

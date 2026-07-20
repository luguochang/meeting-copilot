import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createInitialMeetingState } from "../../domain/reducer";
import type { MeetingViewState, ReviewJobStatus, TranscriptSegment } from "../../domain/events";
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

function endedState(): MeetingViewState {
  const state = createInitialMeetingState("meeting-review");
  return {
    ...state,
    runtime: { ...state.runtime, phase: "ended" },
  };
}

async function openActions(state: MeetingViewState) {
  const user = userEvent.setup();
  render(
    <ReviewWorkspace
      state={state}
      onReloadTranscript={vi.fn()}
      onReloadAudio={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)}
    />,
  );
  await user.click(screen.getByRole("tab", { name: "决策与待办" }));
}

describe("ReviewWorkspace", () => {
  it("falls back to an authenticated blob when direct audio metadata cannot load", async () => {
    const state = endedState();
    state.audioDetail = {
      meetingId: state.meetingId,
      status: "saved",
      assembled: true,
      playbackUrl: "/v2/meetings/meeting-review/audio/content",
      format: "wav",
      fileSizeBytes: 64_044,
      chunkCount: 1,
      durationMs: 2_000,
      tracks: ["microphone"],
      chunks: [],
    };
    state.audioLoadState = "ready";
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      blob: async () => new Blob([new Uint8Array(64)], { type: "audio/wav" }),
    });
    const createObjectURL = vi.fn().mockReturnValue("blob:meeting-audio");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubGlobal("URL", { ...URL, createObjectURL, revokeObjectURL });

    const user = userEvent.setup();
    const view = render(
      <ReviewWorkspace
        state={state}
        onReloadTranscript={vi.fn()}
        onReloadAudio={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "录音" }));
    const audio = view.container.querySelector("audio");
    expect(audio).not.toBeNull();

    fireEvent.error(audio as HTMLAudioElement);

    await waitFor(() => expect(fetchSpy).toHaveBeenCalledWith(
      "/v2/meetings/meeting-review/audio/content",
      expect.objectContaining({
        credentials: "same-origin",
        headers: { Accept: "audio/wav" },
      }),
    ));
    await waitFor(() => expect(audio).toHaveAttribute("src", "blob:meeting-audio"));
    view.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:meeting-audio");
    vi.unstubAllGlobals();
  });

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
        onExport={vi.fn().mockResolvedValue(undefined)}
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

  it("keeps saved live suggestions beside structured minutes and de-duplicates open questions", async () => {
    const state = endedState();
    state.minutes = {
      meetingId: state.meetingId,
      jobId: "minutes-1",
      version: 1,
      status: "ready",
      markdown: "# 会议纪要",
      structured: {
        decisions: ["先灰度 5%"],
        action_items: [],
        risks: [],
        open_questions: ["谁负责回滚？"],
      },
      createdAtMs: 1,
      updatedAtMs: 1,
    };
    state.suggestions = [{
      suggestionId: "suggestion-1",
      meetingId: state.meetingId,
      jobId: "suggestion-job-1",
      generationId: "generation-1",
      evidenceSegmentId: "segment-1",
      evidenceTranscriptSeq: 1,
      evidenceHash: "hash-1",
      stateRevision: 1,
      status: "committed",
      draftText: "确认回滚负责人",
      draftSeq: 1,
      text: "上线前确认回滚负责人",
      finalDraftSeq: 1,
      feedback: "kept",
      createdAtMs: 1,
      updatedAtMs: 1,
      committedAtMs: 1,
    }];
    state.openQuestions = [{
      id: "question-1",
      text: "谁负责回滚?",
      status: "open",
      evidenceSegmentIds: ["segment-1"],
      updatedAtMs: 1,
    }];

    await openActions(state);

    expect(screen.getByText("先灰度 5%")).toBeVisible();
    expect(screen.getByText("上线前确认回滚负责人")).toBeVisible();
    expect(screen.getAllByText(/谁负责回滚/)).toHaveLength(1);
    expect(screen.getByRole("button", { name: "查看上下文" })).toBeVisible();
  });

  it("treats explicit structured empty arrays as authoritative", async () => {
    const state = endedState();
    state.minutes = {
      meetingId: state.meetingId,
      jobId: "minutes-empty",
      version: 1,
      status: "ready",
      markdown: "## 已确认决策\n- 已过期的旧决策",
      structured: { decisions: [], action_items: [], risks: [], open_questions: [] },
      createdAtMs: 1,
      updatedAtMs: 1,
    };

    await openActions(state);

    expect(screen.queryByText("已过期的旧决策")).not.toBeInTheDocument();
    expect(screen.getByText("本次会议尚未形成明确决策或行动项。")).toBeVisible();
    expect(screen.getByText("没有未闭环风险或待确认问题。")).toBeVisible();
  });

  it("recovers real legacy headings without treating code or nested lists as decisions", async () => {
    const state = endedState();
    state.minutes = {
      meetingId: state.meetingId,
      jobId: "minutes-legacy",
      version: 1,
      status: "ready",
      markdown: [
        "## 已确认重点",
        "- 先灰度 5%",
        "  - 嵌套说明不是决策",
        "```md",
        "## 已确认重点",
        "- 代码示例不是决策",
        "```",
        "## 行动项",
        "- 确认回滚负责人 (owner: 李四, deadline: 上线前)",
      ].join("\n"),
      structured: null,
      createdAtMs: 1,
      updatedAtMs: 1,
    };

    await openActions(state);

    expect(screen.getByText("先灰度 5%")).toBeVisible();
    expect(screen.getByText("确认回滚负责人")).toBeVisible();
    expect(screen.queryByText("嵌套说明不是决策")).not.toBeInTheDocument();
    expect(screen.queryByText("代码示例不是决策")).not.toBeInTheDocument();
  });

  it.each([
    ["running", false, "会议纪要正在生成，完成后显示决策与行动项。"],
    ["failed", false, "会议纪要生成失败，未能提取决策与行动项。"],
    ["failed", true, "识别语义质量不足，决策与行动项提取已暂停。"],
  ] as Array<[ReviewJobStatus, boolean, string]>) (
    "shows the actual %s minutes state in the actions tab",
    async (status, qualityPaused, expected) => {
      const state = endedState();
      state.reviewJobs.minutes = {
        id: "minutes-job",
        meetingId: state.meetingId,
        kind: "minutes",
        status,
        attempts: 1,
        maxAttempts: 3,
        errorClass: status === "failed" ? "ProviderError" : null,
        output: null,
        updatedAtMs: 1,
        completedAtMs: status === "failed" ? 1 : null,
      };
      state.diagnostics = qualityPaused
        ? {
            formal_derivation_status: "suppressed_by_asr_semantic_quality",
            degradation_reasons: ["asr_semantic_quality_blocked"],
          }
        : {};

      await openActions(state);

      expect(screen.getByText(expected)).toBeVisible();
      expect(screen.queryByText("本次会议尚未形成明确决策或行动项。")).not.toBeInTheDocument();
    },
  );

  it("distinguishes an unconfigured AI provider from an active retry", async () => {
    const state = endedState();
    state.fullTranscript = [segment(1)];
    state.fullTranscriptState = "ready";
    state.reviewJobs = {
      minutes: {
        id: "minutes-deferred",
        meetingId: state.meetingId,
        kind: "minutes",
        status: "retry_wait",
        attempts: 0,
        maxAttempts: 3,
        errorClass: "ProviderRuntimeNotConfiguredDeferred",
        output: null,
        updatedAtMs: 1,
        completedAtMs: null,
      },
      approach: {
        id: "approach-deferred",
        meetingId: state.meetingId,
        kind: "approach",
        status: "retry_wait",
        attempts: 0,
        maxAttempts: 3,
        errorClass: "ProviderRuntimeNotConfiguredDeferred",
        output: null,
        updatedAtMs: 1,
        completedAtMs: null,
      },
    };

    await openActions(state);
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "复盘" }));

    expect(screen.getByText("会议纪要：等待配置 AI")).toBeVisible();
    expect(screen.getByText("AI 尚未配置，会议文字和录音已保存；配置 AI 后会自动继续生成会后产物。")).toBeVisible();
    expect(screen.getByText("AI 尚未配置，会议文字和录音已保存；配置 AI 后可重新生成会议纪要。")).toBeVisible();
    expect(screen.getByText("AI 尚未配置，会议文字和录音已保存；配置 AI 后可重新生成分析建议。")).toBeVisible();
    expect(screen.queryByText("会议纪要：正在重试")).not.toBeInTheDocument();
  });

  it("does not claim review jobs are generating when no final transcript exists", () => {
    const state = endedState();
    state.audioDetail = {
      meetingId: state.meetingId,
      status: "saved",
      assembled: true,
      playbackUrl: "/v2/meetings/meeting-review/audio/content",
      format: "wav",
      fileSizeBytes: 128,
      chunkCount: 1,
      durationMs: 60_000,
      tracks: ["microphone"],
      chunks: [],
    };

    render(
      <ReviewWorkspace
        state={state}
        onReloadTranscript={vi.fn()}
        onReloadAudio={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
      />,
    );

    expect(screen.getByText("本次没有形成可确认的会议文字，录音已保存；补充有效音频后可重新整理。")).toBeVisible();
    expect(screen.getByText("本次没有形成可确认的会议文字，暂时没有可生成的分析建议。")).toBeVisible();
    expect(screen.queryByText("会议纪要正在生成，完成后会自动出现在这里。")).not.toBeInTheDocument();
  });
});

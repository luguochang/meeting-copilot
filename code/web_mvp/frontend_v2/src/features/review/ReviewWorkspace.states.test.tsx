import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpMeetingApi } from "../../api/client";
import type { MeetingViewState, ReviewJob, ReviewJobStatus } from "../../domain/events";
import { createInitialMeetingState, meetingReducer } from "../../domain/reducer";
import { ReviewWorkspace } from "./ReviewWorkspace";

function endedState(): MeetingViewState {
  const state = createInitialMeetingState("review-state-fixture");
  return {
    ...state,
    runtime: { ...state.runtime, phase: "ended" },
    fullTranscriptState: "ready",
    fullTranscript: [{
      meetingId: state.meetingId, segmentId: "fixture-1", finalId: "final-1", transcriptSeq: 1,
      text: "模拟验收：确认重复请求的业务边界。", normalizedText: "模拟验收：确认重复请求的业务边界。",
      startedAtMs: 0, endedAtMs: 900, revision: 1, evidenceHash: "fixture-hash",
      createdAtMs: 1_000, updatedAtMs: 1_000, correctionStatus: "no_change",
    }],
  };
}

function job(kind: "minutes" | "approach", status: ReviewJobStatus): ReviewJob {
  return {
    id: `${kind}-fixture`, meetingId: "review-state-fixture", kind, status,
    attempts: 1, maxAttempts: 3, errorClass: null, output: null, updatedAtMs: 1_000,
    completedAtMs: ["failed", "cancelled", "succeeded"].includes(status) ? 1_000 : null,
  };
}

function props(state: MeetingViewState) {
  return { state, onReloadTranscript: vi.fn(), onReloadAudio: vi.fn(), onExport: vi.fn().mockResolvedValue(undefined) };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => { vi.unstubAllGlobals(); });

describe("ReviewWorkspace truthful task states", () => {
  it.each([
    ["pending", "已排队，等待处理。", "等待处理"],
    ["retry_wait", "等待重试，尚未生成。", "等待重试"],
    ["cancelled", "任务已取消，不会自动继续；可按需手动重新生成。", "已取消"],
    ["failed", "生成失败，可按需重试；会议文字仍保留。", "生成失败"],
    ["succeeded", "任务已结束，但没有可显示的结果；请刷新状态后核对，或手动重新生成。", "已完成"],
    ["unknown", "任务状态暂不可用，请先刷新状态。", "状态未知"],
  ] as Array<[ReviewJobStatus, string, string]>) (
    "does not claim the %s product is still generating",
    (status, text, chip) => {
      const state = endedState();
      state.reviewJobs = { minutes: job("minutes", status), approach: job("approach", status) };
      if (status === "cancelled") state.reviewJobs.minutes = { ...job("minutes", status), errorClass: "ProviderRuntimeNotConfiguredDeferred" };
      render(<ReviewWorkspace {...props(state)} />);
      expect(screen.getByText(`会议纪要${text}`)).toBeVisible();
      expect(screen.getByText(`分析建议${text}`)).toBeVisible();
      expect(screen.getByText(new RegExp(`^会议纪要：${chip}`))).toBeVisible();
      expect(screen.queryByText("会议纪要正在生成，完成后会自动出现在这里。")).toBeNull();
      expect(screen.queryByText("分析建议正在整理。")).toBeNull();
      expect(screen.queryByText("AI 尚未配置，会议文字和录音已保存；配置 AI 后会自动继续生成会后产物。")).toBeNull();
    },
  );

  it("only a running job promises active generation", () => {
    const state = endedState();
    state.reviewJobs = { minutes: job("minutes", "running"), approach: job("approach", "running") };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByText("会议纪要正在生成，完成后会自动出现在这里。")).toBeVisible();
    expect(screen.getByText("分析建议正在整理。")).toBeVisible();
    expect(screen.queryByRole("button", { name: "生成会议纪要" })).toBeNull();
  });

  it("creates missing products only after explicit clicks through a real HttpMeetingApi", async () => {
    const state = endedState();
    const fetcher = vi.fn().mockImplementation(async () => jsonResponse({ job: { status: "pending" } }, 202));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi("http://localhost:8768");
    const refresh = vi.fn();
    const user = userEvent.setup();
    const view = render(<ReviewWorkspace {...props(state)} onRetryReviewJob={(kind) => api.retryReviewJob(state.meetingId, kind)} onRefresh={refresh} />);
    expect(screen.getByText("会议纪要尚未开始生成，可按需手动生成。")).toBeVisible();
    expect(screen.getByText("分析建议尚未开始生成，可按需手动生成。")).toBeVisible();
    expect(screen.getByText("尚无 AI 初稿")).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "生成会议纪要" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(fetcher).toHaveBeenNthCalledWith(1, "http://localhost:8768/v2/meetings/review-state-fixture/jobs/minutes/retry", expect.objectContaining({ method: "POST", body: JSON.stringify({ use_current_transcript_revision: true }) }));
    await user.click(screen.getByRole("button", { name: "生成分析建议" }));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(2));
    expect(fetcher).toHaveBeenNthCalledWith(2, "http://localhost:8768/v2/meetings/review-state-fixture/jobs/approach/retry", expect.objectContaining({ method: "POST", body: JSON.stringify({ use_current_transcript_revision: true }) }));
    expect(fetcher).toHaveBeenCalledTimes(2);
    const pendingState = { ...state, reviewJobs: { minutes: job("minutes", "pending"), approach: job("approach", "pending") } };
    view.rerender(<ReviewWorkspace {...props(pendingState)} onRetryReviewJob={(kind) => api.retryReviewJob(state.meetingId, kind)} onRefresh={refresh} />);
    expect(screen.getByText("会议纪要已排队，等待处理。")).toBeVisible();
    expect(screen.getByText("分析建议已排队，等待处理。")).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("makes cancelled work explicitly restartable without automatically submitting it", async () => {
    const state = endedState();
    state.reviewJobs = { minutes: job("minutes", "cancelled"), approach: job("approach", "cancelled") };
    const request = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(<ReviewWorkspace {...props(state)} onRetryReviewJob={request} />);
    expect(request).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "手动重新生成会议纪要" }));
    expect(request).toHaveBeenCalledExactlyOnceWith("minutes");
  });

  it("shows a completed empty approach as a result rather than an infinite wait", () => {
    const state = endedState();
    state.reviewJobs.approach = job("approach", "succeeded");
    state.approach = { cards: [], degraded: false, updatedAtMs: 1_000 };
    render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByText("本次没有生成补充分析建议。")).toBeVisible();
    expect(screen.queryByText("分析建议正在整理。")).toBeNull();
  });

  it.each([false, true])("does not request a product when transcript prerequisites fail (quality paused %s)", async (qualityPaused) => {
    const state = endedState();
    if (qualityPaused) state.diagnostics = { formal_derivation_status: "suppressed_by_asr_semantic_quality", degradation_reasons: ["asr_semantic_quality_blocked"] };
    else state.fullTranscript = [];
    state.reviewJobs.minutes = job("minutes", "cancelled");
    const request = vi.fn();
    const regenerate = vi.fn();
    const user = userEvent.setup();
    render(<ReviewWorkspace {...props(state)} onRetryReviewJob={request} onRegenerateDocument={regenerate} />);
    expect(screen.queryByRole("button", { name: "手动重新生成会议纪要" })).toBeNull();
    expect(screen.queryByRole("button", { name: "生成分析建议" })).toBeNull();
    expect(screen.getByRole("button", { name: "重新生成会议纪要" })).toBeDisabled();
    if (!qualityPaused) {
      await user.click(screen.getByRole("button", { name: "重试会议纪要任务" }));
      expect(screen.getByRole("alert")).toHaveTextContent("尚无可确认的会议文字，暂不能生成会后产物。");
    }
    expect(request).not.toHaveBeenCalled();
    expect(regenerate).not.toHaveBeenCalled();
  });

  it("preserves the original text and shows an explicit HTTP refusal without claiming generation", async () => {
    const state = endedState();
    const fetcher = vi.fn().mockResolvedValue(jsonResponse({ detail: "会议文字暂不可用，请刷新后重试。" }, 409));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi();
    const user = userEvent.setup();
    render(<ReviewWorkspace {...props(state)} onRetryReviewJob={(kind) => api.retryReviewJob(state.meetingId, kind)} />);
    await user.click(screen.getByRole("button", { name: "生成会议纪要" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("会议文字暂不可用，请刷新后重试。");
    expect(screen.getByText("会议纪要尚未开始生成，可按需手动生成。")).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("ReviewWorkspace consistent correction hydration contract", () => {
  it("keeps the same terminal projection from snapshot and transcript, while allowing a genuine newer retry", async () => {
    const wireSegment = {
      meeting_id: "review-state-fixture", segment_id: "fixture-1", final_id: "final-1", transcript_seq: 1,
      text: "模拟验收文字", normalized_text: "模拟验收文字", revision: 1, evidence_hash: "fixture-hash",
      created_at_ms: 1_000, updated_at_ms: 1_000, correction_status: "failed_preserved_original",
      correction_error_class: "synthetic_harness_other_lanes_disabled", correction_updated_at_ms: 1_200,
    };
    const page = (segments: unknown[]) => ({ meeting_id: "review-state-fixture", segments, after_transcript_seq: 0, has_more: false, next_after_transcript_seq: 1 });
    const fetcher = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ meeting_id: "review-state-fixture", last_seq: 1, segments: [wireSegment], suggestions: [], runtime: { phase: "ended" } }))
      .mockResolvedValueOnce(jsonResponse(page([wireSegment])))
      .mockResolvedValueOnce(jsonResponse(page([{ ...wireSegment, updated_at_ms: 1_500, correction_updated_at_ms: 1_500, correction_status: "processing", correction_error_class: null }])));
    vi.stubGlobal("fetch", fetcher);
    const api = new HttpMeetingApi();
    const snapshot = await api.getSnapshot("review-state-fixture");
    let state = meetingReducer(createInitialMeetingState("review-state-fixture"), { type: "snapshot.received", snapshot, receivedAtMs: 1_300 });
    state = meetingReducer(state, { type: "transcript.received", segments: await api.getTranscript(state.meetingId) });
    expect(state.fullTranscript[0]).toMatchObject({ revision: 1, updatedAtMs: 1_000, correctionStatus: "failed_preserved_original", correctionUpdatedAtMs: 1_200 });
    const view = render(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByText("当前文字是原始 ASR")).toBeVisible();
    expect(screen.queryByText("AI 正在校对会议文字")).toBeNull();
    state = meetingReducer(state, { type: "transcript.received", segments: await api.getTranscript(state.meetingId) });
    expect(state.fullTranscript[0]).toMatchObject({ revision: 1, correctionStatus: "processing", correctionUpdatedAtMs: 1_500 });
    view.rerender(<ReviewWorkspace {...props(state)} />);
    expect(screen.getByText("AI 正在校对会议文字")).toBeVisible();
    expect(fetcher.mock.calls.every(([, init]) => init.method === undefined)).toBe(true);
  });
});

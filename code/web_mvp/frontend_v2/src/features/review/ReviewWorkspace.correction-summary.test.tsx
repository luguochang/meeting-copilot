import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { TranscriptSegment } from "../../domain/events";
import { createInitialMeetingState } from "../../domain/reducer";
import { ReviewWorkspace } from "./ReviewWorkspace";

function segment(sequence: number, status: string, error: string | null = null): TranscriptSegment {
  const raw = `保留的识别原文 ${sequence}`;
  return {
    meetingId: "correction-review", segmentId: `s${sequence}`, finalId: `f${sequence}`, transcriptSeq: sequence,
    text: raw, normalizedText: status === "changed" ? `已校对内容 ${sequence}` : raw,
    revision: status === "changed" ? 2 : 1, evidenceHash: `hash-${sequence}`,
    startedAtMs: sequence * 1000, endedAtMs: sequence * 1000 + 900,
    createdAtMs: sequence * 1000, updatedAtMs: sequence * 1000 + 950,
    correctionStatus: status, correctionErrorClass: error,
  };
}
function show(segments: TranscriptSegment[]) {
  const initial = createInitialMeetingState("correction-review");
  const state = { ...initial, runtime: { ...initial.runtime, phase: "ended" as const },
    fullTranscriptState: "ready" as const, fullTranscript: segments };
  const retry = vi.fn().mockResolvedValue(undefined);
  const reload = vi.fn(); const refresh = vi.fn().mockResolvedValue(undefined);
  const view = render(<ReviewWorkspace state={state} onReloadTranscript={reload} onReloadAudio={vi.fn()}
    onExport={vi.fn().mockResolvedValue(undefined)} onRetryTranscriptCorrection={retry} onRefresh={refresh} />);
  return { ...view, retry, reload, refresh, state };
}

describe("review correction coverage and final segment reasons", () => {
  it("shows actual 14/437 valid coverage and mixed final reasons without treating 423 segments as timeout batches", async () => {
    const segments = Array.from({ length: 437 }, (_, index) => segment(index + 1,
      index < 7 ? "changed" : index < 14 ? "no_change" : "failed_preserved_original",
      index < 14 ? null : index < 432 ? "correction_not_evaluated" : index < 436 ? "provider_timeout" : "correction_speech_mode_changed"));
    const before = JSON.stringify(segments);
    const { retry, reload, refresh, state } = show(segments);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("已校对 14/437 段");
    expect(alert).toHaveTextContent("418 段未取得有效校对结果");
    expect(alert).toHaveTextContent("4 段响应超时");
    expect(alert).toHaveTextContent("1 段未通过原意校验");
    expect(alert).toHaveTextContent("原始识别文字已保留");
    expect(alert).toHaveTextContent("部分文字仍为原始 ASR");
    expect(alert).not.toHaveTextContent("有 423 段校对失败。远程语义校对响应超时");
    expect(alert).not.toHaveTextContent(/423.*超时|73|批次|未执行|没有调用|未调用/);
    expect(retry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重试 AI 校对" }));
    await waitFor(() => expect(retry).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(state.fullTranscript)).toBe(before);
    fireEvent.click(screen.getByRole("tab", { name: "会议文字" }));
    expect(screen.getByText("部分已校对（14/437）")).toBeVisible();
    expect(screen.queryByText("原始 ASR（校对失败）")).not.toBeInTheDocument();
    expect(screen.getByText(/^已校对内容 1已校对内容 2/)).toBeVisible();
    expect(screen.getByText(/保留的识别原文 437/)).toBeVisible();
  });

  it.each([
    ["correction_provider_rates_not_configured", "2 段因 Provider 未配置价格费率未执行"],
    ["correction_provider_budget_exhausted", "2 段因本轮校对预算已用尽未执行"],
    ["provider_timeout", "2 段响应超时"],
    ["TimeoutError", "2 段响应超时"],
    ["deadline_exceeded", "2 段响应超时"],
    ["correction_speech_mode_changed", "2 段未通过原意校验"],
    ["correction_meeting_fact_changed", "2 段未通过原意校验"],
  ])("keeps a homogeneous known reason scoped to the affected segments (%s)", (error, message) => {
    show([segment(1, "failed_preserved_original", error), segment(2, "failed_preserved_original", error)]);
    expect(screen.getByRole("alert")).toHaveTextContent("已校对 0/2 段");
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("alert")).toHaveTextContent("当前文字是原始 ASR");
    expect(screen.getByRole("button", { name: "重试 AI 校对" })).toBeEnabled();
  });

  it.each([null, "unrecognized_internal_detail"])("does not let one timeout hide an unknown affected segment (%s)", (error) => {
    show([segment(1, "failed_preserved_original", "provider_timeout"), segment(2, "failed_preserved_original", error)]);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("1 段响应超时");
    expect(alert).toHaveTextContent("1 段失败原因未明确");
    expect(alert).not.toHaveTextContent("远程语义校对响应超时");
    expect(alert).not.toHaveTextContent("unrecognized_internal_detail");
  });

  it("does not use a rate or budget refusal as the explanation for generic final results", () => {
    show([segment(1, "no_change"), segment(2, "failed_preserved_original", "correction_not_evaluated"),
      segment(3, "failed_preserved_original", "correction_provider_rates_not_configured"),
      segment(4, "failed_preserved_original", "correction_provider_budget_exhausted")]);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("已校对 1/4 段");
    expect(alert).toHaveTextContent("1 段未取得有效校对结果");
    expect(alert).toHaveTextContent("1 段因 Provider 未配置价格费率未执行");
    expect(alert).toHaveTextContent("1 段因本轮校对预算已用尽未执行");
    expect(alert).not.toHaveTextContent("远程语义校对未执行：");
  });

  it("does not hide failed final reasons while other segments are still processing", () => {
    const { retry } = show([segment(1, "changed"), segment(2, "processing"),
      segment(3, "failed_preserved_original", "provider_timeout")]);
    expect(screen.getByText(/已校对 1\/3 段，剩余 1 段处理中/)).toHaveTextContent("1 段响应超时");
    expect(screen.getByRole("button", { name: "重试 AI 校对" })).toBeDisabled();
    expect(retry).not.toHaveBeenCalled();
  });
});

import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { TranscriptRecoveryStatus } from "./TranscriptRecoveryStatus";

it("keeps the failed text range separate from playable audio without exposing raw error content", () => {
  const open = vi.fn(); render(<TranscriptRecoveryStatus diagnostic={{ status: "failed", start_ms: 60_000, end_ms: 90_000,
    error_code: "asr_capacity_unavailable", error_message: "private raw internal error" }} audioAvailable onOpenAudio={open} />);
  expect(screen.getByRole("alert", { name: "会议文字补齐状态" })).toHaveTextContent("1:00–1:30");
  expect(screen.getByText(/当前本机可用内存不足/)).toBeVisible();
  expect(screen.getByText(/已有可播放的录音/)).toBeVisible();
  expect(screen.queryByText(/private raw/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看录音与保存状态" })); expect(open).toHaveBeenCalledTimes(1);
});

it("does not promise saved audio or whole-meeting confirmation from a completed range", () => {
  render(<TranscriptRecoveryStatus diagnostic={{ status: "completed", start_ms: 1_000, end_ms: 4_000 }} />);
  expect(screen.getByRole("status")).toHaveTextContent("0:01–0:04");
  expect(screen.getByText(/不代表全场文字已经人工确认/)).toBeVisible();
  expect(screen.getByText(/不表示录音已完整保存/)).toBeVisible();
});

it.each([Infinity, NaN, -1])("does not invent a range from invalid timestamp %s", (start) => {
  render(<TranscriptRecoveryStatus diagnostic={{ status: "completed", start_ms: start, end_ms: 1000 }} />);
  expect(screen.getByRole("status")).toHaveTextContent("范围需核对");
  expect(screen.queryByText("这段中断处文字已补齐")).not.toBeInTheDocument();
});

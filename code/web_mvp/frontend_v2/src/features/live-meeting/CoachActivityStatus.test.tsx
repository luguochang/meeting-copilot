import { render, screen } from "@testing-library/react";
import { CoachActivityStatus } from "./CoachActivityStatus";
import type { CoachDueWorkItem } from "../../domain/events";

const due: CoachDueWorkItem = { itemId: "check-1", status: "due", nextCheckAtMs: 123,
  evidenceSegmentIds: ["segment-1"], evidenceQuote: "合成例子", createdAtMs: 100 };

it("does not present a timed-out check as an executing background job", () => {
  render(<CoachActivityStatus decision={{ status: "timed_out" }} />);
  expect(screen.getByRole("status", { name: "当前会议的 Pi 状态" })).toHaveTextContent("本轮未及时完成");
  expect(screen.queryByText(/正在处理/)).not.toBeInTheDocument();
  expect(screen.getByText(/不会因这条提示自动重试/)).toBeVisible();
});

it("reports deduplicated due work as waiting, independently from an actual busy indicator", () => {
  const view = render(<CoachActivityStatus dueItems={[due, due]} />);
  expect(screen.getByText(/有 1 项待后续检查/)).toHaveTextContent("尚不代表已经开始生成");
  expect(screen.queryByText(/正在处理/)).not.toBeInTheDocument();
  view.rerender(<CoachActivityStatus dueItems={[due]} runtime={{ state: "busy", label: "分析中", detail: null, level: null }} />);
  expect(screen.getByText(/服务端正在处理/)).toHaveTextContent("不表示正在阅读的这条回答仍在生成");
});

it("clears completed progress without inventing new work or keeping a stale spinner", () => {
  const view = render(<CoachActivityStatus runtime={{ state: "busy", label: "分析中", detail: null, level: null }} />);
  expect(screen.getByRole("status")).toBeVisible();
  view.rerender(<CoachActivityStatus decision={{ status: "intervention" }} runtime={{ state: "active", label: "已就绪", detail: null, level: null }} />);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("shows an honest timeout before any answer exists without duplicated Pi errors", () => {
  render(<CoachActivityStatus decision={{ status: "timed_out" }} runtime={{ state: "error", label: "Pi补充未完成", detail: "Pi暂不可用", level: null }} hasAnswer={false} />);
  expect(screen.getByText(/本次建议未及时生成，可重试/)).toBeVisible();
  expect(screen.queryByText(/已有回答/)).not.toBeInTheDocument();
});

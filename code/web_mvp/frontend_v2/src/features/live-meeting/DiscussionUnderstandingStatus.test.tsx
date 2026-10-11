import { render, screen } from "@testing-library/react";
import type { RuntimeIndicator } from "../../domain/events";
import { DiscussionUnderstandingStatus } from "./DiscussionUnderstandingStatus";

function runtime(decision: string | null, errorClass?: string): RuntimeIndicator {
  return { state: "active", label: "不可信诊断标签", detail: "敏感异常正文", level: null, decision, errorClass };
}

it.each([
  ["not_started", "等待下一轮讨论理解"],
  ["pending", "尚未开始执行"],
  ["running", "正在理解本轮讨论"],
  ["retry_wait", "当前未在执行"],
  ["succeeded", "这不代表已生成新的建议"],
  ["cancelled", "本轮理解已取消，结果未采用"],
] as const)("uses host decision %s, independently of a misleading active state", (decision, text) => {
  render(<DiscussionUnderstandingStatus runtime={runtime(decision)} />);
  expect(screen.getByRole("status", { name: "当前讨论的理解状态" })).toHaveTextContent(text);
  expect(screen.queryByText(/不可信诊断|敏感异常/)).not.toBeInTheDocument();
});

it.each([
  ["evidence_rejected", "未通过依据核对，结果未采用"],
  ["invalid_output", "未通过格式校验，结果未采用"],
  ["provider_timeout", "请求超时"],
  ["provider_unavailable", "模型服务暂不可用"],
  ["unknown_failure", "本轮理解未完成"],
  ["raw_provider_secret", "本轮理解未完成"],
])("explains rejected understanding with safe error class %s and preserved transcript", (reason, text) => {
  render(<DiscussionUnderstandingStatus runtime={runtime("failed", reason)} />);
  const status = screen.getByRole("status", { name: "当前讨论的理解状态" });
  expect(status).toHaveTextContent(text);
  expect(status).toHaveTextContent("原文已保留");
  expect(status).toHaveTextContent("主动提问");
  expect(status).not.toHaveTextContent("正在理解");
  expect(status).not.toHaveTextContent("已更新");
  expect(status).not.toHaveTextContent("raw_provider_secret");
  expect(screen.queryByText(/不可信诊断|敏感异常/)).not.toBeInTheDocument();
});

it("treats an unknown phase as unconfirmed rather than trusting optimistic labels", () => {
  render(<DiscussionUnderstandingStatus runtime={runtime("future_success")} />);
  expect(screen.getByRole("status")).toHaveTextContent("理解状态待确认");
  expect(screen.getByRole("status")).not.toHaveTextContent("已更新");
});

it("does not fabricate understanding state for an older server without this capability", () => {
  render(<DiscussionUnderstandingStatus />);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("replaces a failure only when the authoritative current phase changes", () => {
  const view = render(<DiscussionUnderstandingStatus runtime={runtime("failed", "evidence_rejected")} />);
  expect(screen.getByRole("status")).toHaveTextContent("未通过依据核对");
  view.rerender(<DiscussionUnderstandingStatus runtime={runtime("pending")} />);
  expect(screen.getByRole("status")).toHaveTextContent("尚未开始执行");
  expect(screen.getByRole("status")).not.toHaveTextContent("未通过依据核对");
  view.rerender(<DiscussionUnderstandingStatus runtime={runtime("succeeded")} />);
  expect(screen.getByRole("status")).toHaveTextContent("讨论状态已更新");
});

it("shows corrected evidence waiting, queued and running without claiming a Provider failure", () => {
  const view = render(<DiscussionUnderstandingStatus runtime={{ ...runtime("not_started"), understandingStatus: "waiting_reanalysis" }} />);
  const status = screen.getByRole("status");
  expect(status).toHaveTextContent("校对后等待重新理解");
  expect(status).not.toHaveTextContent("失败");
  view.rerender(<DiscussionUnderstandingStatus runtime={{ ...runtime("pending"), understandingStatus: "reanalysis_queued" }} />);
  expect(status).toHaveTextContent("重新理解已排队");
  expect(status).not.toHaveTextContent("正在重新理解");
  view.rerender(<DiscussionUnderstandingStatus runtime={{ ...runtime("running"), understandingStatus: "reanalysis_running" }} />);
  expect(status).toHaveTextContent("正在重新理解");
  expect(status).not.toHaveTextContent("讨论状态已更新");
  expect(screen.queryByText(/不可信诊断|敏感异常/)).not.toBeInTheDocument();
});

it.each([
  ["revision_deferred", "not_started", "原文仍在校对更新，重新理解暂缓"],
  ["reanalysis_deferred", "not_started", "重新理解暂缓，等待后台条件"],
  ["partial_review", "succeeded", "部分问题闭环待核对"],
] as const)("explains %s without treating it as full understanding", (understandingStatus, decision, text) => {
  render(<DiscussionUnderstandingStatus runtime={{ ...runtime(decision), understandingStatus }} />);
  const status = screen.getByRole("status");
  expect(status).toHaveTextContent(text);
  expect(status).not.toHaveTextContent("讨论状态已更新");
  expect(status).not.toHaveTextContent("模型服务暂不可用");
  expect(screen.queryByText(/不可信诊断|敏感异常/)).not.toBeInTheDocument();
});

it("does not let a reanalysis hint hide a real failed job", () => {
  render(<DiscussionUnderstandingStatus runtime={{ ...runtime("failed", "provider_timeout"), understandingStatus: "reanalysis_queued" }} />);
  expect(screen.getByRole("status")).toHaveTextContent("本轮理解请求超时");
  expect(screen.getByRole("status")).not.toHaveTextContent("已排队");
});

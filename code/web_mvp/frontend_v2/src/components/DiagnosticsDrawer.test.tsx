import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { createInitialMeetingState } from "../domain/reducer";
import { DiagnosticsDrawer } from "./DiagnosticsDrawer";

describe("DiagnosticsDrawer", () => {
  it("shows blocked transcription even when the connection and model are available", () => {
    const base = createInitialMeetingState("meeting-asr-blocked");
    render(<DiagnosticsDrawer open onClose={vi.fn()} onRefresh={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)} transportKind="sse" state={{
        ...base, connection: "live", runtime: { ...base.runtime, phase: "live", ai: {
          ...base.runtime.ai, state: "active", label: "AI 已连接", capabilities: {
            transcript: { state: "paused", label: "精修暂不可用", level: null,
              detail: "录音继续保存，识别草稿尚未确认。", errorClass: "asr_capacity_unavailable" },
          },
        } },
      }} />);
    expect(screen.getByText("会议文字待恢复")).toBeVisible();
    expect(screen.getByText("录音继续保存，识别草稿尚未确认。")).toBeVisible();
    expect(screen.queryByText("运行正常")).not.toBeInTheDocument();
    expect(screen.getByText("尚无已确认文字，教练等待可分析的内容")).toBeVisible();
  });

  it("does not claim the realtime pipeline is healthy before any confirmed words arrive", () => {
    const base = createInitialMeetingState("meeting-asr-waiting");
    render(<DiagnosticsDrawer open onClose={vi.fn()} onRefresh={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)} transportKind="sse" state={{
        ...base, connection: "live", runtime: { ...base.runtime, phase: "live" },
      }} />);
    expect(screen.getByText("等待会议文字")).toBeVisible();
    expect(screen.queryByText("本地服务和 AI 实时能力正常")).not.toBeInTheDocument();
  });

  it("supports Escape, traps keyboard focus, and restores focus to its trigger", async () => {
    const state = {
      ...createInitialMeetingState("meeting-dialog-contract"),
      connection: "live" as const,
    };
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>打开运行诊断</button>
          <DiagnosticsDrawer
            open={open}
            onClose={() => setOpen(false)}
            onRefresh={vi.fn()}
            onExport={vi.fn().mockResolvedValue(undefined)}
            state={state}
            transportKind="sse"
          />
        </>
      );
    }
    const user = userEvent.setup();
    render(<Harness />);

    const trigger = screen.getByRole("button", { name: "打开运行诊断" });
    await user.click(trigger);
    const close = within(screen.getByRole("dialog", { name: "会议连接详情" }))
      .getByRole("button", { name: "关闭运行诊断" });
    await waitFor(() => expect(close).toHaveFocus());

    const refresh = screen.getByRole("button", { name: "重新读取状态" });
    refresh.focus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "会议连接详情" })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("does not report normal operation when the Pi realtime lane timed out", () => {
    const state = {
      ...createInitialMeetingState("meeting-pi-timeout"),
      connection: "live" as const,
      coachDecision: {
        origin: "pi" as const,
        status: "timed_out" as const,
        statusReason: "provider_timeout",
      },
    };

    render(
      <DiagnosticsDrawer
        open
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
        state={state}
        transportKind="sse"
      />,
    );

    expect(screen.getByText("AI 实时能力异常")).toBeVisible();
    expect(screen.getAllByText("Pi 实时教练超时，本轮未生成建议")).toHaveLength(2);
    expect(screen.queryByText("运行正常")).not.toBeInTheDocument();
  });

  it("distinguishes provider recovery from the local event connection", () => {
    const state = {
      ...createInitialMeetingState("meeting-pi-circuit"),
      connection: "live" as const,
      coachDecision: {
        origin: "pi" as const,
        status: "protected_silent" as const,
        statusReason: "realtime_provider_recovery_probe_required",
      },
    };

    render(
      <DiagnosticsDrawer
        open
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
        state={state}
        transportKind="sse"
      />,
    );

    expect(screen.getByText("AI 实时能力异常")).toBeVisible();
    expect(screen.getAllByText("AI 实时通道连续失败，需要连接测试恢复")).toHaveLength(2);
    expect(screen.getByText("本地服务已连接")).toBeVisible();
  });

  it("keeps historical Pi timeout visible after the ended meeting clears the live card", () => {
    const state = {
      ...createInitialMeetingState("meeting-ended-pi-timeout"),
      connection: "live" as const,
      runtime: {
        ...createInitialMeetingState("meeting-ended-pi-timeout").runtime,
        phase: "ended" as const,
      },
      coachDecision: null,
      diagnostics: {
        coach_runtime_history: [{
          status: "timed_out",
          status_reason: "provider_timeout",
          origin: "pi",
          pi_provider_attempted: true,
          outcome: "failure",
        }],
      },
    };

    render(
      <DiagnosticsDrawer
        open
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
        state={state}
        transportKind="poll"
      />,
    );

    expect(screen.getAllByText("本场记录过 Pi Provider 超时；当前结果请查看实时教练")).toHaveLength(2);
    expect(screen.queryByText("AI 实时能力异常")).not.toBeInTheDocument();
  });

  it("reports local reflex provenance without counting it as a Pi intervention", () => {
    const state = {
      ...createInitialMeetingState("meeting-local-reflex"),
      connection: "live" as const,
      diagnostics: {
        coach_runtime_history: [{
          status: "intervention",
          origin: "local_reflex",
          pi_provider_attempted: false,
          outcome: "local_reflex_fallback",
        }],
      },
    };

    render(
      <DiagnosticsDrawer
        open
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
        state={state}
        transportKind="sse"
      />,
    );

    expect(screen.getByText("运行正常")).toBeVisible();
    expect(screen.getAllByText("本场有 1 轮使用本地实时提示，未调用 Pi Provider")).toHaveLength(2);
    expect(screen.queryByText("Pi 实时教练执行失败，本轮未生成建议")).not.toBeInTheDocument();
  });

  it("does not claim a meeting never produced advice when a produced intervention was withdrawn", () => {
    const base = createInitialMeetingState("meeting-produced-then-withdrawn");
    render(<DiagnosticsDrawer open onClose={vi.fn()} onRefresh={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)} transportKind="sse" state={{
        ...base, connection: "live", coachDecision: { origin: "pi", status: "stale" },
        diagnostics: { coach_runtime_history: [
          { origin: "pi", status: "intervention", outcome: "success", pi_provider_attempted: true },
          { origin: "pi", status: "failed", outcome: "failure", pi_provider_attempted: true },
        ] },
      }} />);
    expect(screen.queryAllByText(/本场未生成 Pi 建议/)).toHaveLength(0);
    expect(screen.getAllByText("本场记录过 Pi 实时教练执行失败；当前结果请查看实时教练")).toHaveLength(2);
    expect(screen.queryByText("AI 实时能力异常")).not.toBeInTheDocument();
  });

  it("does not classify an evidence withdrawal followed by a current intervention as execution failure", () => {
    const base = createInitialMeetingState("meeting-real-withdrawal-history");
    render(<DiagnosticsDrawer open onClose={vi.fn()} onRefresh={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)} transportKind="sse" state={{
        ...base, connection: "live", coachDecision: { origin: "pi", status: "intervention" },
        diagnostics: { coach_runtime_history: [
          { origin: "pi", status: "intervention", outcome: "intervention", pi_provider_attempted: true },
          { origin: "pi", status: "stale", status_reason: "discussion_evidence_revised", outcome: "failure", pi_provider_attempted: true },
          { origin: "pi", status: "intervention", outcome: "intervention", pi_provider_attempted: true },
        ] },
      }} />);
    expect(screen.queryByText("AI 实时能力异常")).not.toBeInTheDocument();
    expect(screen.queryAllByText(/本场记录过 .*失败/)).toHaveLength(0);
  });

  it("keeps a true historical timeout informational after the current request succeeds", () => {
    const base = createInitialMeetingState("meeting-recovered-pi");
    render(<DiagnosticsDrawer open onClose={vi.fn()} onRefresh={vi.fn()}
      onExport={vi.fn().mockResolvedValue(undefined)} transportKind="sse" state={{
        ...base, connection: "live", coachDecision: { origin: "pi", status: "intervention" },
        diagnostics: { coach_runtime_history: [
          { origin: "pi", status: "timed_out", status_reason: "provider_timeout", outcome: "failure", pi_provider_attempted: true },
        ] },
      }} />);
    expect(screen.queryByText("AI 实时能力异常")).not.toBeInTheDocument();
    expect(screen.getAllByText("本场记录过 Pi Provider 超时；当前结果请查看实时教练")).toHaveLength(2);
  });

  it("distinguishes a Pi attempt that fell back from a local-only reflex", () => {
    const state = {
      ...createInitialMeetingState("meeting-pi-fallback"),
      connection: "live" as const,
      diagnostics: {
        coach_runtime_history: [{
          status: "intervention",
          origin: "local_reflex",
          runtime_requested: "pi",
          runtime_used: "local_reflex",
          pi_provider_attempted: true,
          llm_called: true,
          outcome: "local_reflex_fallback",
        }],
      },
    };

    render(
      <DiagnosticsDrawer
        open
        onClose={vi.fn()}
        onRefresh={vi.fn()}
        onExport={vi.fn().mockResolvedValue(undefined)}
        state={state}
        transportKind="sse"
      />,
    );

    expect(screen.getByText("运行正常")).toBeVisible();
    expect(screen.getAllByText("本场有 1 轮使用本地实时提示，Pi Provider 已调用但未在实时窗口内完成")).toHaveLength(2);
    expect(screen.queryByText("未调用 Pi Provider")).not.toBeInTheDocument();
  });
});

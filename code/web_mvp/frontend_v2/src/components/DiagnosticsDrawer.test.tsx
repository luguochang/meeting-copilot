import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createInitialMeetingState } from "../domain/reducer";
import { DiagnosticsDrawer } from "./DiagnosticsDrawer";

describe("DiagnosticsDrawer", () => {
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

    expect(screen.getByText("AI 实时能力异常")).toBeVisible();
    expect(screen.getAllByText("本场曾发生 Pi Provider 超时，本场未生成 Pi 建议")).toHaveLength(2);
    expect(screen.queryByText("运行正常")).not.toBeInTheDocument();
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

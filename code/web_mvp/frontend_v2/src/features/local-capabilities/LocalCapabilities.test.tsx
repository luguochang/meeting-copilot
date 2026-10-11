import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { LocalCapabilityStatus, MeetingApi } from "../../api/client";
import { LocalCapabilities } from "./LocalCapabilities";

function status(overrides: Partial<LocalCapabilityStatus> = {}): LocalCapabilityStatus {
  return {
    schemaVersion: "meeting_copilot.local_capability_status.v1",
    platform: "windows-x86_64",
    baseAppReady: true,
    installed: false,
    packageId: null,
    packageVersion: null,
    installedAt: null,
    realtimeAsrReady: false,
    fileAsrReady: false,
    restartRequired: false,
    signatureStatus: null,
    releaseScope: null,
    downloadPageUrl: "https://pan.example.invalid/meeting-copilot",
    importAvailable: true,
    errors: [],
    ...overrides,
  };
}

it("shows the base state and the configured offline download page", async () => {
  const api = {
    getLocalCapabilities: vi.fn(async () => status()),
    importLocalCapabilityPackage: vi.fn(),
  } as unknown as MeetingApi;

  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);

  expect(await screen.findByText("基础版可用")).toBeVisible();
  expect(screen.getByRole("link", { name: "打开下载页" })).toHaveAttribute(
    "href",
    "https://pan.example.invalid/meeting-copilot",
  );
  expect(screen.getAllByText("未安装")).toHaveLength(2);
});

it("imports a selected mcpkg and reports that restart is required", async () => {
  const user = userEvent.setup();
  const installed = status({
    installed: true,
    packageId: "meeting-copilot-asr-full",
    packageVersion: "0.1.0",
    realtimeAsrReady: true,
    fileAsrReady: true,
    restartRequired: true,
  });
  const api = {
    getLocalCapabilities: vi.fn().mockResolvedValueOnce(status()).mockResolvedValue(installed),
    importLocalCapabilityPackage: vi.fn(async () => installed),
  } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  await screen.findByText("基础版可用");
  const packageFile = new File(["fixture"], "Talktrace-ASR-Full.mcpkg", {
    type: "application/octet-stream",
  });

  await user.upload(screen.getByLabelText("选择离线能力包"), packageFile);
  await user.click(screen.getByRole("button", { name: "校验并导入" }));

  expect(api.importLocalCapabilityPackage).toHaveBeenCalledWith(packageFile);
  expect(await screen.findByText("完整能力已安装")).toBeVisible();
  expect(screen.getByText("重启后启用完整能力")).toBeVisible();
  expect(screen.getAllByText("重启后启用")).toHaveLength(2);
});

function runtime(state: "ready" | "starting" | "idle" | "failed" | "unknown") {
  return { available: state === "unknown" ? null : true, state, source: "source_configuration", reason: null, processReady: state === "ready", processRunning: state === "ready" || state === "starting" };
}

it("shows configured source capabilities without claiming an imported package", async () => {
  const api = { getLocalCapabilities: vi.fn(async () => status({
    deploymentMode: "source", importAvailable: false,
    runtimeCapabilities: { realtimeAsr: runtime("ready"), fileAsr: { ...runtime("ready"), state: "available", formats: { wav: true, mp3: false } }, offlineRefinement: runtime("idle") },
  })) } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("本地能力可用")).toBeVisible();
  expect(screen.getByText("按需启动")).toBeVisible();
  expect(screen.getByText("支持格式：WAV")).toBeVisible();
  expect(screen.getByText("当前使用源码配置的本地运行时")).toBeVisible();
  expect(screen.queryByText("完整能力已安装")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "选择文件" })).toBeDisabled();
});

it("distinguishes loading, worker failure and unknown observations", async () => {
  const api = { getLocalCapabilities: vi.fn(async () => status({
    runtimeCapabilities: { realtimeAsr: runtime("starting"), fileAsr: runtime("unknown"), offlineRefinement: runtime("failed") },
  })) } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("正在加载")).toBeVisible();
  expect(screen.getByText("运行失败")).toBeVisible();
  expect(screen.getByText("状态未知")).toBeVisible();
  expect(screen.queryByText("本地能力可用")).not.toBeInTheDocument();
});

it.each([
  ["asr_capacity_unavailable", "failed", "当前可用资源不足，本次未启动；释放内存后请手动重试。"],
  ["asr_resource_probe_failed", "unknown", "无法读取本机可用资源，本次离线处理无法完成；请检查运行环境后手动重试。"],
  ["asr_resource_guard_triggered", "failed", "本机资源保护触发，本次任务未完成；请稍后手动重试。"],
  ["asr_cold_start_busy", "failed", "本机仍有本地处理任务未结束，本次未启动；请稍后手动重试。"],
] as const)("shows safe resource guidance for %s without claiming ready", async (reason, state, message) => {
  const api = { getLocalCapabilities: vi.fn(async () => status({
    installed: true, packageVersion: "0.1.0", realtimeAsrReady: true, fileAsrReady: true,
    runtimeCapabilities: {
      realtimeAsr: runtime("idle"),
      fileAsr: { ...runtime(state), available: true, reason, formats: { wav: true } },
      offlineRefinement: runtime("idle"),
    },
  })) } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText(message)).toBeVisible();
  expect(screen.getByText("完整能力包已安装 · 0.1.0")).toBeVisible();
  expect(screen.getByText("本地能力未就绪")).toBeVisible();
  expect(screen.queryByText("本地能力可用")).not.toBeInTheDocument();
  expect(screen.queryByText("已就绪")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument();
  expect(api.getLocalCapabilities).toHaveBeenCalledTimes(1);
});

it("does not treat installed but idle or unknown runtime as usable", async () => {
  const api = { getLocalCapabilities: vi.fn(async () => status({
    installed: true,
    runtimeCapabilities: { realtimeAsr: runtime("idle"), fileAsr: { ...runtime("unknown"), available: true }, offlineRefinement: runtime("idle") },
  })) } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("本地能力未就绪")).toBeVisible();
  expect(screen.getByText("完整能力包已安装")).toBeVisible();
  expect(screen.queryByText("本地能力可用")).not.toBeInTheDocument();
});

it("does not render unknown diagnostic contents or raw status exceptions", async () => {
  const api = { getLocalCapabilities: vi.fn(async () => status({
    runtimeCapabilities: {
      realtimeAsr: runtime("ready"), fileAsr: runtime("ready"),
      offlineRefinement: { ...runtime("failed"), reason: "C:\\private\\model.pt secret-fixture" },
    },
  })) } as unknown as MeetingApi;
  const view = render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("运行原因暂未识别，请检查本地运行状态。")).toBeVisible();
  expect(screen.getByText("部分本地能力可用")).toBeVisible();
  expect(view.container.textContent).not.toContain("private");
  expect(view.container.textContent).not.toContain("secret-fixture");
  view.unmount();
  const failure = { getLocalCapabilities: vi.fn().mockRejectedValue(new Error("C:\\private\\model.pt secret-fixture")) } as unknown as MeetingApi;
  const failedView = render(<LocalCapabilities api={failure} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("离线能力状态加载失败，请稍后重新检查")).toBeVisible();
  expect(failedView.container.textContent).not.toContain("private");
  expect(failedView.container.textContent).not.toContain("secret-fixture");
});

it("never claims base availability when the status request fails", async () => {
  const api = { getLocalCapabilities: vi.fn().mockRejectedValue(new Error("检查失败")) } as unknown as MeetingApi;
  render(<LocalCapabilities api={api} onOpenMeetings={vi.fn()} onOpenNotes={vi.fn()} />);
  expect(await screen.findByText("状态检查失败")).toBeVisible();
  expect(screen.queryByText("基础版可用")).not.toBeInTheDocument();
  expect(screen.getAllByText("状态未知")).toHaveLength(2);
});

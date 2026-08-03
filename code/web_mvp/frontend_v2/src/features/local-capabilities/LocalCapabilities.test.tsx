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
    getLocalCapabilities: vi.fn(async () => status()),
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
  expect(screen.getAllByText("已就绪")).toHaveLength(2);
});

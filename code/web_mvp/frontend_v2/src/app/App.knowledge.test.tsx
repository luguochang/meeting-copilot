import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "../api/client";
import { App } from "./App";

const capture = vi.hoisted(() => ({ stop: vi.fn(), state: { phase: "recording", elapsedMs: 1000 }, controllers: [] as unknown[] }));
vi.mock("../features/live-meeting/useMeetingMicrophone", () => ({ useMeetingMicrophone: () => capture }));
vi.mock("../features/live-meeting/LiveMeetingWorkbench", () => ({ LiveMeetingWorkbench: ({ onOpenKnowledge, onMeetingStarted, microphoneController, meetingId }: { onOpenKnowledge: () => void; onMeetingStarted: (id: string) => void; microphoneController: unknown; meetingId: string | null }) => { capture.controllers.push(microphoneController); return <main><output>{meetingId ?? "会议列表"}</output><button onClick={() => onMeetingStarted("live-1")}>开始会议</button><button onClick={onOpenKnowledge}>资料库</button></main>; } }));
vi.mock("../features/knowledge-library/KnowledgeLibrary", () => ({ KnowledgeLibrary: ({ activeMeeting, onOpenActiveMeeting, initialSourceId, initialProjectId }: { activeMeeting: { meetingId: string }; onOpenActiveMeeting: () => void; initialSourceId: string; initialProjectId: string }) => <main><h1>资料库</h1><output>{activeMeeting?.meetingId ?? "无正在会议"}</output><output>{initialSourceId} / {initialProjectId}</output><button onClick={onOpenActiveMeeting}>返回正在会议</button></main> }));
beforeEach(() => { window.history.replaceState(null, "", "/?meeting_id=live-1"); capture.controllers.length = 0; capture.stop.mockClear(); vi.spyOn(HttpMeetingApi.prototype, "listMeetingsPage").mockResolvedValue({ meetings: [], nextCursor: null, hasMore: false }); });
afterEach(() => { window.history.replaceState(null, "", "/"); vi.restoreAllMocks(); });
it("keeps the App capture controller and active meeting across the library round trip", async () => {
  const user = userEvent.setup(); render(<App />); await user.click(screen.getByRole("button", { name: "开始会议" })); await user.click(screen.getByRole("button", { name: "资料库" }));
  expect(window.location.search).toBe("?view=knowledge"); expect(screen.getByText("live-1")).toBeInTheDocument(); expect(capture.stop).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "返回正在会议" })); expect(window.location.search).toBe("?meeting_id=live-1"); expect(capture.controllers.every((controller) => controller === capture)).toBe(true); expect(capture.stop).not.toHaveBeenCalled();
});
it("resolves a project/source deep link without treating it as a meeting ID", () => {
  window.history.replaceState(null, "", "/?view=knowledge&source=span-3&project_id=project-1"); render(<App />); expect(screen.getByRole("heading", { name: "资料库" })).toBeInTheDocument(); expect(screen.getByText("span-3 / project-1")).toBeInTheDocument(); expect(capture.controllers).toHaveLength(0);
});

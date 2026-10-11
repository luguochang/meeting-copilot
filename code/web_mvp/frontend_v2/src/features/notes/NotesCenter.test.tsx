import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpMeetingApi, type MeetingApi } from "../../api/client";
import type { MeetingNote } from "../../domain/events";
import { NotesCenter } from "./NotesCenter";

vi.mock("../settings/ProviderSettingsControl", () => ({ ProviderSettingsControl: () => null }));
afterEach(() => vi.unstubAllGlobals());

function note(): MeetingNote {
  return {
    noteId: "note-1",
    meetingId: "meeting-1",
    title: "发布风险",
    body: "周五前关闭风险项。",
    sourceKind: "selection",
    sourceMessageId: null,
    version: 1,
    status: "active",
    createdAtMs: 1,
    updatedAtMs: 2,
    evidence: [{
      ordinal: 0,
      meetingId: "meeting-1",
      segmentId: "segment-1",
      transcriptSeq: 3,
      startMs: 65_000,
      endMs: 67_000,
      quote: "风险项需要在周五前关闭。",
    }],
  };
}

it("autosaves title and body through the actual HttpMeetingApi instance", async () => {
  let current = { note_id: "note-1", meeting_id: "meeting-1", title: "发布风险", body: "周五前关闭风险项。", source_kind: "ask_ai", version: 1, status: "active", evidence: [] };
  const jsonResponse = (value: unknown) => new Response(JSON.stringify(value), { status: 200, headers: { "content-type": "application/json" } });
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input)).pathname;
    if (path === "/v2/meetings") return jsonResponse({ meetings: [] });
    if (path === "/v2/notes/page") return jsonResponse({ items: [current], next_cursor: null, has_more: false });
    if (path === "/v2/notes/note-1" && init?.method === "PATCH") {
      const changes = JSON.parse(String(init.body));
      expect(changes.expected_version).toBe(current.version);
      current = { ...current, title: changes.title, body: changes.body, version: current.version + 1 };
      return jsonResponse({ note: current });
    }
    throw new Error(`Unexpected note request: ${path}`);
  });
  vi.stubGlobal("fetch", fetcher);
  const api = new HttpMeetingApi("http://localhost:8768");
  render(<NotesCenter api={api} onOpenMeetings={vi.fn()} onOpenMeeting={vi.fn()} onOpenEvidence={vi.fn()} />);
  const title = await screen.findByRole("textbox", { name: "笔记标题" });
  const body = screen.getByRole("textbox", { name: "笔记正文" });
  await waitFor(() => {
    expect(title).toHaveValue("发布风险");
    expect(body).toHaveValue("周五前关闭风险项。");
  });
  fireEvent.change(title, { target: { value: "权益与通知重试边界（模拟验收）" } });
  fireEvent.change(body, { target: { value: "需要明确通知重试是否影响订单成功。" } });
  expect(await screen.findByText("已保存", {}, { timeout: 1800 })).toBeVisible();
  expect(current).toMatchObject({ title: "权益与通知重试边界（模拟验收）", body: "需要明确通知重试是否影响订单成功。", version: 2 });
  expect(screen.queryByText(/reading 'request'/)).not.toBeInTheDocument();
});

it("explains how first-time users save meeting answers and provides the existing meeting navigation", async () => {
  const api = { listMeetings: vi.fn().mockResolvedValue({ meetings: [] }), listNotes: vi.fn().mockResolvedValue([]), createNote: vi.fn() } as unknown as MeetingApi;
  const openMeetings = vi.fn();
  render(<NotesCenter api={api} onOpenMeetings={openMeetings} onOpenMeeting={vi.fn()} onOpenEvidence={vi.fn()} />);
  expect(await screen.findByText("尚未保存会议笔记")).toBeVisible();
  expect(screen.getByRole("heading", { name: "把重要回答留成会议笔记" })).toBeVisible();
  expect(screen.getByText(/在「询问 AI」中提问，再点击回答下的「存为笔记」/)).toBeVisible();
  expect(screen.getByText(/笔记保存后不会自动成为会议背景/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "查看会议" }));
  expect(openMeetings).toHaveBeenCalledOnce();
  expect(api.createNote).not.toHaveBeenCalled();
});

it("searches, autosaves and jumps from a note to transcript evidence without playback", async () => {
  let current = note();
  const api = {
    listMeetings: vi.fn(async () => ({ meetings: [{
      meetingId: "meeting-1",
      title: "发布周会",
      phase: "ended" as const,
      startedAtMs: 1,
      endedAtMs: 2,
      createdAtMs: 1,
      updatedAtMs: 2,
      segmentCount: 3,
      suggestionCount: 0,
      audioDurationMs: 0,
      hasMinutes: false,
    }] })),
    listNotes: vi.fn(async () => [current]),
    updateNote: vi.fn(async (_noteId, _version, changes) => {
      current = { ...current, ...changes, version: current.version + 1 };
      return current;
    }),
    deleteNote: vi.fn(async () => ({ ...current, status: "deleted" as const })),
  } as unknown as MeetingApi;
  const onOpenEvidence = vi.fn();

  render(
    <NotesCenter
      api={api}
      onOpenMeetings={vi.fn()}
      onOpenMeeting={vi.fn()}
      onOpenEvidence={onOpenEvidence}
    />,
  );

  expect(await screen.findByRole("button", { name: /发布风险/ })).toBeVisible();
  const bodyInput = screen.getByRole("textbox", { name: "笔记正文" });
  await waitFor(() => expect(bodyInput).toHaveValue("周五前关闭风险项。"));
  fireEvent.change(bodyInput, {
    target: { value: "周五前关闭风险项，负责人待确认。" },
  });
  await waitFor(() => expect(api.updateNote).toHaveBeenCalledWith(
    "note-1",
    1,
    expect.objectContaining({ body: "周五前关闭风险项，负责人待确认。" }),
  ), { timeout: 2_000 });
  expect(await screen.findByText("已保存")).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: /风险项需要在周五前关闭/ }));
  expect(onOpenEvidence).toHaveBeenCalledWith("meeting-1", "segment-1");
});
it("uses opaque note pagination beyond the former 300-row limit and resets it on search", async () => {
  const api = { listMeetings: vi.fn().mockResolvedValue({ meetings: [] }), listNotesPage: vi.fn().mockResolvedValueOnce({ items: [note()], nextCursor: "opaque+/=", hasMore: true }).mockResolvedValue({ items: [{ ...note(), noteId: "note-501", title: "第501条笔记" }], nextCursor: null, hasMore: false }) } as unknown as MeetingApi;
  render(<NotesCenter api={api} onOpenMeetings={vi.fn()} onOpenMeeting={vi.fn()} onOpenEvidence={vi.fn()} />); await waitFor(() => expect(screen.getByRole("button", { name: "下一页笔记" })).toBeEnabled()); fireEvent.click(screen.getByRole("button", { name: "下一页笔记" })); await screen.findByRole("button", { name: /第501条笔记/ }); expect(api.listNotesPage).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 50, cursor: "opaque+/=" }), expect.any(AbortSignal)); fireEvent.change(screen.getByRole("textbox", { name: "搜索笔记" }), { target: { value: "新的检索" } }); await waitFor(() => expect(api.listNotesPage).toHaveBeenLastCalledWith(expect.objectContaining({ query: "新的检索", cursor: null }), expect.any(AbortSignal)));
});
it("serializes autosaves and preserves typing made while an older save is in flight", async () => {
  let release: ((value: MeetingNote) => void) | null = null;
  const api = { listMeetings: vi.fn().mockResolvedValue({ meetings: [] }), listNotes: vi.fn().mockResolvedValue([note()]), updateNote: vi.fn().mockImplementationOnce(() => new Promise<MeetingNote>((resolve) => { release = resolve; })).mockResolvedValue({ ...note(), version: 3, body: "后续编辑" }) } as unknown as MeetingApi;
  render(<NotesCenter api={api} onOpenMeetings={vi.fn()} onOpenMeeting={vi.fn()} onOpenEvidence={vi.fn()} />); const field = await screen.findByRole("textbox", { name: "笔记正文" }); fireEvent.change(field, { target: { value: "上一轮保存" } }); await waitFor(() => expect(api.updateNote).toHaveBeenCalledTimes(1), { timeout: 1500 }); fireEvent.change(field, { target: { value: "后续编辑" } }); await act(async () => { release!({ ...note(), version: 2, body: "上一轮保存" }); }); expect(field).toHaveValue("后续编辑"); await waitFor(() => expect(api.updateNote).toHaveBeenLastCalledWith("note-1", 2, expect.objectContaining({ body: "后续编辑" })), { timeout: 1500 }); expect(field).toHaveValue("后续编辑");
});
it("preserves a conflicted note and explicitly compares the latest revision before retry", async () => {
  const api = { listMeetings: vi.fn().mockResolvedValue({ meetings: [] }), listNotes: vi.fn().mockResolvedValue([note()]), getNote: vi.fn().mockResolvedValue({ ...note(), version: 2, body: "别人最新保存" }), updateNote: vi.fn().mockRejectedValueOnce(new Error("笔记已更新" )).mockResolvedValue({ ...note(), version: 3, body: "我的内容" }) } as unknown as MeetingApi;
  render(<NotesCenter api={api} onOpenMeetings={vi.fn()} onOpenMeeting={vi.fn()} onOpenEvidence={vi.fn()} />); const field = await screen.findByRole("textbox", { name: "笔记正文" }); fireEvent.change(field, { target: { value: "我的内容" } }); fireEvent.click(await screen.findByRole("button", { name: "读取最新版本，保留我的编辑" }, { timeout: 1500 })); await screen.findByText(/最新已保存内容：发布风险.*别人最新保存/); expect(field).toHaveValue("我的内容"); expect(api.updateNote).toHaveBeenCalledTimes(1); fireEvent.click(screen.getByRole("button", { name: "确认按我的编辑继续保存" })); await waitFor(() => expect(api.updateNote).toHaveBeenLastCalledWith("note-1", 2, expect.objectContaining({ body: "我的内容" })), { timeout: 1500 });
});

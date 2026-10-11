import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpMaterialsApi, type MaterialsApi, type MaterialSet, type MeetingAttachment } from "../../api/materials";
import { MeetingAttachments } from "./MeetingAttachments";
import { materialDraftPointerKey, rememberMaterialDraft } from "./materialDraftPointer";

const draft: MaterialSet = { id: "set-1", state: "draft", meeting_id: null, revision: 1, context_revision: 0 };
const file: MeetingAttachment = { id: "attachment-1", set_id: draft.id, title: "需求文档", filename: "需求.docx", purpose: "requirement", status: "active", import_status: "succeeded", active_version_id: null, latest_version_id: "v1", enabled: false, selected_span_ids: null, revision: 1, content_revision: 1, file_size_bytes: 100, error_message: null };
const version = { id: "v1", attachment_id: file.id, number: 1, content_hash: "hash", content_characters: 12, origin: "import", warnings: ["第 2 页没有可提取的文字"], structure: {} };
function fixture(): MaterialsApi {
  return { create: vi.fn(async () => draft), get: vi.fn(async () => ({ material_set: draft, attachments: [file] })), getForMeeting: vi.fn(async () => null), forMeeting: vi.fn(async () => ({ ...draft, state: "bound" as const, meeting_id: "meeting" })), upload: vi.fn(async () => ({ attachment: file, job: { id: "j", status: "pending", error_message: null } })), detail: vi.fn(async () => ({ attachment: file, version, latest_version_id: "v1", job: null })), spans: vi.fn(async () => ({ items: [{ id: "span", version_id: "v1", ordinal: 0, text: "通知必须同步成功。", locator: { page: 1 } }], has_more: false, next_offset: null })), save: vi.fn(async () => ({ attachment: { ...file, revision: 2, latest_version_id: "v2" }, version: { ...version, id: "v2", number: 2 } })), use: vi.fn(async (_set, _file, _version, enabled) => ({ attachment: { ...file, enabled, active_version_id: enabled ? "v1" : null, revision: 2 }, material_set: { ...draft, revision: 2, context_revision: 1 } })), retry: vi.fn(async () => ({ id: "j", status: "queued", error_message: null })), cancel: vi.fn(async () => ({ id: "j", status: "cancelled", error_message: null })), remove: vi.fn(async () => ({ attachment: { ...file, status: "deleted" }, job: { id: "cleanup", status: "queued", error_message: null } })), removeDraft: vi.fn(), source: vi.fn() };
}
afterEach(() => { sessionStorage.clear(); window.localStorage.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });
describe("meeting-only materials", () => {
  it("opens above a transformed preflight dialog, owns Escape, and returns focus without closing preflight", async () => {
    const api = fixture();
    const { container } = render(<section role="dialog" aria-modal="true" aria-label="会前检查" style={{ transform: "translate(-50%, -50%)" }}><MeetingAttachments api={api} meetingId={null} materialSet={draft} /></section>);
    const trigger = screen.getByRole("button", { name: /本场资料/ }); trigger.focus();
    fireEvent.click(trigger);
    const drawer = screen.getByRole("dialog", { name: "本场资料" });
    expect(container).not.toContainElement(drawer);
    expect(drawer.closest(".meeting-attachments-layer")?.parentElement).toBe(document.body);
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "本场资料" })).not.toBeInTheDocument());
    expect(screen.getByRole("dialog", { name: "会前检查" })).toBeVisible();
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(document.body.style.overflow).not.toBe("hidden");
    expect(api.create).not.toHaveBeenCalled(); expect(api.use).not.toHaveBeenCalled();
  });
  it("uploads before a meeting without creating a project, previews warnings, then explicitly enables", async () => {
    const api = fixture(); const changed = vi.fn();
    function Host() { const [set, update] = useState<MaterialSet | null>(null); return <MeetingAttachments api={api} meetingId={null} materialSet={set} onSetChange={update} onChanged={changed} />; }
    render(<Host />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(api.create).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText(/添加 DOCX/), { target: { files: [new File(["sample"], "需求.docx")] } });
    await screen.findByText("通知必须同步成功。");
    expect(api.create).toHaveBeenCalledTimes(1);
    expect(api.forMeeting).not.toHaveBeenCalled();
    expect(screen.getByText("第 2 页没有可提取的文字")).toBeVisible();
    expect(api.use).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "用于本场背景" }));
    await waitFor(() => expect(api.use).toHaveBeenCalledWith("set-1", "attachment-1", "v1", true, 1, null));
    expect(changed).toHaveBeenCalledTimes(1);
  });
  it("opening an empty existing meeting reads without creating material sets", async () => {
    const api = fixture(); render(<MeetingAttachments api={api} meetingId="meeting" />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    await waitFor(() => expect(api.getForMeeting).toHaveBeenCalledWith("meeting", expect.any(AbortSignal)));
    expect(api.create).not.toHaveBeenCalled(); expect(api.forMeeting).not.toHaveBeenCalled();
  });
  it("keeps the attachment entry available while its read-only initial lookup is loading", async () => {
    const api = fixture(); let finish!: (value: MaterialSet | null) => void;
    vi.mocked(api.getForMeeting).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<MeetingAttachments api={api} meetingId="meeting" />);
    const trigger = screen.getByRole("button", { name: "本场资料（可选）" });
    expect(trigger).toBeEnabled(); fireEvent.click(trigger);
    expect(await screen.findByText("正在读取资料…")).toBeVisible();
    expect(trigger).toBeEnabled(); expect(api.create).not.toHaveBeenCalled(); expect(api.forMeeting).not.toHaveBeenCalled();
    finish(null); await waitFor(() => expect(screen.queryByText("正在读取资料…")).not.toBeInTheDocument());
  });
  it("saves a correction as v2 without silently changing the enabled version", async () => {
    const api = fixture(); render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: /需求文档/ }));
    await screen.findByText("通知必须同步成功。");
    fireEvent.click(screen.getByRole("button", { name: "修正正文" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修正附件正文" }), { target: { value: "通知允许延后。" } });
    fireEvent.click(screen.getByRole("button", { name: "保存新版本" }));
    await waitFor(() => expect(api.save).toHaveBeenCalledWith("set-1", "attachment-1", "通知允许延后。", 1, "v1"));
    expect(api.use).not.toHaveBeenCalled();
    expect(screen.getByText(/新版本已保存/)).toBeVisible();
  });
  it("opens a historical citation at its real ordinal and prevents partial-body editing", async () => {
    const api = fixture(); const bound = { ...draft, state: "bound" as const, meeting_id: "meeting" };
    vi.mocked(api.get).mockResolvedValue({ material_set: bound, attachments: [file] });
    render(<MeetingAttachments api={api} meetingId="meeting" source={{ sourceType: "meeting_attachment", sourceId: "old-span", setId: draft.id, meetingId: "meeting", attachmentId: file.id, title: file.title, versionId: "old", version: 1, contentHash: "old-hash", locator: { page: 20 }, quote: "通知必须同步成功。", contextRevision: 0, ordinal: 150 }} />);
    await waitFor(() => expect(api.spans).toHaveBeenCalledWith("set-1", "attachment-1", "old", 150, expect.any(AbortSignal)));
    expect(screen.queryByRole("button", { name: "修正正文" })).not.toBeInTheDocument();
    expect(screen.getByText(/历史版本，仅供核对/)).toBeVisible();
  });
  it("anchors a historical reference by its exact version and ordinal rather than matching repeated quote text", async () => {
    const api = fixture(); const bound = { ...draft, state: "bound" as const, meeting_id: "meeting" };
    vi.mocked(api.get).mockResolvedValue({ material_set: bound, attachments: [file] });
    vi.mocked(api.spans).mockResolvedValue({ items: [{ id: "old-span", version_id: "old-version", ordinal: 150, text: "反复出现的原话", locator: { page: 20 } }, { id: "repeat-span", version_id: "old-version", ordinal: 151, text: "反复出现的原话", locator: { page: 21 } }], has_more: false, next_offset: null });
    render(<MeetingAttachments api={api} meetingId="meeting" source={{ sourceType: "meeting_attachment", sourceId: "material_span_old-span", setId: draft.id, meetingId: "meeting", attachmentId: file.id, title: file.title, versionId: "old-version", version: 1, contentHash: "old-hash", locator: { page: 20 }, quote: "反复出现的原话", contextRevision: 0, ordinal: 150 }} />);
    await screen.findAllByText("反复出现的原话");
    const anchors = document.querySelectorAll(".attachment-span-highlight");
    expect(anchors).toHaveLength(1); expect(anchors[0]).toHaveTextContent("第 20 页");
    expect(api.spans).toHaveBeenCalledWith(draft.id, file.id, "old-version", 150, expect.any(AbortSignal));
  });
  it("restores prepared materials before opening the drawer so start can bind their latest revision", async () => {
    sessionStorage.setItem("talktrace-material-draft", draft.id);
    const api = fixture(); const restored = vi.fn();
    vi.mocked(api.get).mockResolvedValue({ material_set: { ...draft, revision: 4 }, attachments: [file] });
    render(<MeetingAttachments api={api} meetingId={null} onSetChange={restored} />);
    await waitFor(() => expect(restored).toHaveBeenCalledWith({ ...draft, revision: 4 }));
    expect(api.create).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "本场资料 · 1" })).toBeVisible();
  });
  it("recovers a failed import in the same set and removes it only after an explicit local confirmation", async () => {
    const api = fixture(); const failed = { ...file, import_status: "failed", latest_version_id: null, error_message: "文件转换失败" };
    vi.mocked(api.get).mockResolvedValue({ material_set: draft, attachments: [failed] });
    vi.mocked(api.detail).mockResolvedValue({ attachment: failed, version: null, latest_version_id: null, job: { id: "failed-job", status: "failed", error_message: "文件转换失败" } });
    render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: /需求文档/ }));
    fireEvent.click(await screen.findByRole("button", { name: "重试转换" }));
    await waitFor(() => expect(api.retry).toHaveBeenCalledWith(draft.id, "failed-job"));
    fireEvent.click(await screen.findByRole("button", { name: "移除此附件" }));
    expect(api.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认移除" }));
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith(draft.id, file.id, 1));
    expect(api.create).not.toHaveBeenCalled(); expect(api.use).not.toHaveBeenCalled();
  });
  it("cancels a queued conversion without cancelling the meeting", async () => {
    const api = fixture();
    vi.mocked(api.detail).mockResolvedValue({ attachment: file, version: null, latest_version_id: null, job: { id: "queued-job", status: "queued", error_message: null } });
    render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: /需求文档/ }));
    fireEvent.click(await screen.findByRole("button", { name: "取消转换" }));
    await waitFor(() => expect(api.cancel).toHaveBeenCalledWith(draft.id, "queued-job"));
    expect(api.remove).not.toHaveBeenCalled();
  });
  it("retains selected spans across pages and never enables an empty selected range", async () => {
    const api = fixture();
    vi.mocked(api.spans).mockResolvedValueOnce({ items: [{ id: "span-1", version_id: "v1", ordinal: 0, text: "第一段", locator: { page: 1 } }], has_more: true, next_offset: 1 }).mockResolvedValue({ items: [{ id: "span-2", version_id: "v1", ordinal: 1, text: "第二段", locator: { page: 2 } }], has_more: false, next_offset: null });
    render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: /需求文档/ }));
    await screen.findByText("第一段");
    fireEvent.click(screen.getByRole("radio", { name: "仅使用选中的正文片段" }));
    expect(screen.getByRole("button", { name: "用于本场背景" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "选用第 1 段" }));
    fireEvent.click(screen.getByRole("button", { name: "加载后续正文" }));
    await screen.findByText("第二段");
    expect(screen.getByRole("checkbox", { name: "选用第 1 段" })).toBeChecked();
    fireEvent.click(screen.getByRole("checkbox", { name: "选用第 2 段" }));
    fireEvent.click(screen.getByRole("button", { name: "用于本场背景" }));
    await waitFor(() => expect(api.use).toHaveBeenCalledWith(draft.id, file.id, "v1", true, 1, ["span-1", "span-2"]));
    expect(screen.getByText(/不代表 AI 已通读全文/)).toBeVisible();
  });
  it("shows the active version and avoids repeated scope invalidation when it is already in use", async () => {
    const api = fixture(); const active = { ...file, enabled: true, active_version_id: "v1" };
    vi.mocked(api.get).mockResolvedValue({ material_set: draft, attachments: [active] });
    vi.mocked(api.detail).mockResolvedValue({ attachment: active, version, latest_version_id: "v1", job: null });
    render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: /需求文档/ }));
    await screen.findByText("通知必须同步成功。");
    expect(screen.getByRole("button", { name: "正在使用第 1 版" })).toBeDisabled();
    expect(api.use).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "停用本场资料" })).toBeEnabled();
  });
  it("clears a draft with explicit confirmation without creating or deleting any meeting", async () => {
    const api = fixture(); const changed = vi.fn(); sessionStorage.setItem("talktrace-material-draft", draft.id);
    render(<MeetingAttachments api={api} meetingId={null} materialSet={draft} onSetChange={changed} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    fireEvent.click(await screen.findByRole("button", { name: "删除会前草稿资料" }));
    expect(api.removeDraft).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除草稿资料" }));
    await waitFor(() => expect(api.removeDraft).toHaveBeenCalledWith(draft.id, 1));
    expect(changed).toHaveBeenLastCalledWith(null); expect(sessionStorage.getItem("talktrace-material-draft")).toBeNull();
    expect(api.forMeeting).not.toHaveBeenCalled();
  });
  it("recovers a cancelled preflight draft with a new real HttpMaterialsApi instance after session storage is lost", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ material_set: { ...draft, revision: 7 }, attachments: [file] }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher);
    const first = render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} materialSet={draft} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    await screen.findByRole("button", { name: /需求文档/ });
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "本场资料" })).not.toBeInTheDocument());
    const pointerKey = materialDraftPointerKey(window.location.origin);
    const pointer = JSON.parse(window.localStorage.getItem(pointerKey)!);
    expect(Object.keys(pointer).sort()).toEqual(["setId", "updatedAtMs"]);
    expect(pointer.setId).toBe(draft.id);
    first.unmount(); sessionStorage.clear(); fetcher.mockClear();
    const restored = vi.fn();
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} onSetChange={restored} />);
    await waitFor(() => expect(restored).toHaveBeenCalledWith({ ...draft, revision: 7 }));
    expect(screen.getByRole("button", { name: "本场资料 · 1" })).toBeVisible();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`${window.location.origin}/v2/material-sets/set-1`);
    expect(fetcher.mock.calls[0][1]).toMatchObject({ signal: expect.any(AbortSignal) });
    expect(fetcher.mock.calls[0][1]?.method).toBeUndefined();
  });
  it.each([404, 409, 410])("shows a %s stale draft receipt without creating, binding or overwriting it", async (status) => {
    rememberMaterialDraft(window.location.origin, draft.id); sessionStorage.clear();
    const key = materialDraftPointerKey(window.location.origin); const original = window.localStorage.getItem(key);
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ detail: "原草稿已过期或不可读取" }), { status, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); const restored = vi.fn();
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} onSetChange={restored} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(await screen.findByText(/原草稿已过期或不可读取/)).toBeVisible();
    expect(restored).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(key)).toBe(original);
    expect(fetcher.mock.calls.every((call) => call[1]?.method === undefined)).toBe(true);
  });
  it.each([
    { ...draft, state: "bound" as const, meeting_id: "old-meeting" },
    { ...draft, state: "deleted" as const },
    { ...draft, meeting_id: "unexpected-meeting" },
  ])("rejects a receipt that is not an unbound draft (%o)", async (returned) => {
    rememberMaterialDraft(window.location.origin, draft.id); sessionStorage.clear();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ material_set: returned, attachments: [file] }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); const restored = vi.fn();
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} onSetChange={restored} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(await screen.findByText(/未创建或绑定任何会议/)).toBeVisible();
    expect(restored).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(materialDraftPointerKey(window.location.origin))).toBeNull();
    expect(fetcher.mock.calls.every((call) => call[1]?.method === undefined)).toBe(true);
  });
  it("does not reuse another service's draft or guess an identity from a malformed receipt", async () => {
    rememberMaterialDraft("http://another-service:9000", draft.id); sessionStorage.clear();
    window.localStorage.setItem(materialDraftPointerKey(window.location.origin), JSON.stringify({ setId: draft.id, updatedAtMs: Date.now() + 600_000 }));
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(await screen.findByText("无需资料也能使用现场建议。有相关文件时再添加。")).toBeVisible();
    expect(fetcher).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(materialDraftPointerKey("http://another-service:9000"))).not.toBeNull();
  });
  it("retains an old receipt for read-only validation rather than silently expiring stored files", async () => {
    window.localStorage.setItem(materialDraftPointerKey(window.location.origin), JSON.stringify({ setId: draft.id, updatedAtMs: Date.now() - 365 * 24 * 3600 * 1000 }));
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ material_set: draft, attachments: [file] }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); const restored = vi.fn();
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} onSetChange={restored} />);
    await waitFor(() => expect(restored).toHaveBeenCalledWith(draft));
    expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][1]?.method).toBeUndefined();
  });
  it("rejects a different returned identity without changing the existing receipt", async () => {
    rememberMaterialDraft(window.location.origin, draft.id); sessionStorage.clear();
    const key = materialDraftPointerKey(window.location.origin); const original = window.localStorage.getItem(key);
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ material_set: { ...draft, id: "other-set" }, attachments: [file] }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetcher); const restored = vi.fn();
    render(<MeetingAttachments api={new HttpMaterialsApi(window.location.origin)} meetingId={null} onSetChange={restored} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(await screen.findByText(/未创建或绑定任何会议/)).toBeVisible();
    expect(restored).not.toHaveBeenCalled(); expect(window.localStorage.getItem(key)).toBe(original);
    expect(fetcher.mock.calls.every((call) => call[1]?.method === undefined)).toBe(true);
  });
  it("clears a successfully bound draft receipt without clearing a different prepared set", () => {
    rememberMaterialDraft(window.location.origin, draft.id);
    const api = fixture();
    const rendered = render(<MeetingAttachments api={api} meetingId="meeting" materialSet={{ ...draft, state: "bound", meeting_id: "meeting" }} />);
    expect(window.localStorage.getItem(materialDraftPointerKey(window.location.origin))).toBeNull();
    expect(sessionStorage.getItem("talktrace-material-draft")).toBeNull();
    rememberMaterialDraft(window.location.origin, "next-draft");
    rendered.rerender(<MeetingAttachments api={api} meetingId="meeting" materialSet={{ ...draft, state: "bound", meeting_id: "meeting", revision: 2 }} />);
    expect(window.localStorage.getItem(materialDraftPointerKey(window.location.origin))).toContain("next-draft");
    expect(api.create).not.toHaveBeenCalled(); expect(api.forMeeting).not.toHaveBeenCalled();
  });
  it("bounds a slow read, preserves the draft receipt and ignores its late response", async () => {
    rememberMaterialDraft(window.location.origin, draft.id); sessionStorage.clear(); vi.useFakeTimers();
    const api = fixture(); let finish!: (value: { material_set: MaterialSet; attachments: MeetingAttachment[] }) => void;
    vi.mocked(api.get).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const restored = vi.fn(); const pending = vi.fn();
    render(<MeetingAttachments api={api} meetingId={null} onSetChange={restored} onRecoveryPending={pending} />);
    fireEvent.click(screen.getByRole("button", { name: /本场资料/ }));
    expect(pending).toHaveBeenLastCalledWith(true); expect(api.get).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(6_001); });
    expect(screen.getByText(/读取会前资料超时/)).toBeVisible(); expect(pending).toHaveBeenLastCalledWith(false);
    expect(window.localStorage.getItem(materialDraftPointerKey(window.location.origin))).toContain(draft.id);
    await act(async () => { finish({ material_set: draft, attachments: [file] }); });
    expect(restored).not.toHaveBeenCalled(); expect(api.create).not.toHaveBeenCalled(); expect(api.forMeeting).not.toHaveBeenCalled();
    expect(api.get).toHaveBeenCalledTimes(1);
  });
});

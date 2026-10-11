import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReviewDocument } from "../../domain/events";
import { useReviewDocumentDraft } from "./useReviewDocumentDraft";

const fromContent = (content: unknown, fallback: string) => typeof content === "string" ? content : fallback;
const toContent = (value: string) => value;
function document(content: string, revision: number): ReviewDocument {
  return { documentId: "minutes", meetingId: "meeting", kind: "minutes", revision, sourceRevision: 1, contentJson: content, aiVersion: 1, userVersion: 1, source: "user_final", dirtyState: "saved", updatedAtMs: revision * 1000 };
}
beforeEach(() => localStorage.clear());
describe("review draft durability", () => {
  it("keeps edits made during a pending save, then saves them against the returned revision", async () => {
    let finish!: (value: ReviewDocument) => void;
    const onSave = vi.fn().mockImplementationOnce(() => new Promise<ReviewDocument>((resolve) => { finish = resolve; })).mockImplementationOnce(async (revision: number, content: string) => document(content, revision + 1));
    const { result } = renderHook(() => useReviewDocumentDraft({ meetingId: "meeting", kind: "minutes", document: document("原稿", 3), fallback: "", enabled: false, fromContent, toContent, onSave }));
    act(() => result.current.setDraft("第一次修改"));
    let first!: Promise<boolean>;
    act(() => { first = result.current.saveNow(); });
    act(() => result.current.setDraft("保存等待期间的新修改"));
    expect(result.current.saveState).toBe("saving");
    await act(async () => expect(await result.current.saveNow()).toBe(false));
    expect(onSave).toHaveBeenCalledTimes(1);
    await act(async () => { finish(document("第一次修改", 4)); expect(await first).toBe(false); });
    expect(result.current.saveState).toBe("unsaved");
    expect(result.current.draft).toBe("保存等待期间的新修改");
    expect(JSON.parse(localStorage.getItem("meeting-copilot:review-draft:meeting:minutes")!)).toEqual({ baseRevision: 4, value: "保存等待期间的新修改" });
    await act(async () => expect(await result.current.saveNow()).toBe(true));
    expect(onSave).toHaveBeenNthCalledWith(2, 4, "保存等待期间的新修改");
    expect(result.current.saveState).toBe("saved");
    expect(localStorage.getItem("meeting-copilot:review-draft:meeting:minutes")).toBeNull();
  });
  it("keeps the latest local edit after a failed in-flight save and only removes it after retry succeeds", async () => {
    let fail!: (error: Error) => void;
    const onSave = vi.fn().mockImplementationOnce(() => new Promise<ReviewDocument>((_resolve, reject) => { fail = reject; })).mockImplementationOnce(async (revision: number, content: string) => document(content, revision + 1));
    const { result } = renderHook(() => useReviewDocumentDraft({ meetingId: "meeting", kind: "minutes", document: document("原稿", 3), fallback: "", enabled: false, fromContent, toContent, onSave }));
    act(() => result.current.setDraft("提交的版本"));
    let first!: Promise<boolean>; act(() => { first = result.current.saveNow(); });
    act(() => result.current.setDraft("最新本地修改"));
    await act(async () => { fail(new Error("写入失败")); expect(await first).toBe(false); });
    expect(result.current.saveState).toBe("error");
    expect(result.current.error).toBe("写入失败");
    expect(JSON.parse(localStorage.getItem("meeting-copilot:review-draft:meeting:minutes")!).value).toBe("最新本地修改");
    await act(async () => expect(await result.current.saveNow()).toBe(true));
    expect(onSave).toHaveBeenNthCalledWith(2, 3, "最新本地修改");
    expect(localStorage.getItem("meeting-copilot:review-draft:meeting:minutes")).toBeNull();
  });
});

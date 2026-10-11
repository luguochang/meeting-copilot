import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { KnowledgeApi } from "../../api/knowledge";
import type { DocumentSourceEvidence, Suggestion } from "../../domain/events";
import { createInitialMeetingState } from "../../domain/reducer";
import { ReviewWorkspace } from "./ReviewWorkspace";

it("checks historical answer document references against the original meeting without replacing old evidence", async () => {
  const ref: DocumentSourceEvidence = { sourceType: "document", sourceId: "span", documentId: "doc", projectId: "project", title: "已核对方案", versionId: "version-1", version: 1, contentHash: "hash-1", locator: { type: "pdf_page", page: 2 }, quote: "预算待确认", contextRevision: 3 };
  const answer: Suggestion = { suggestionId: "answer", meetingId: "review-sources", jobId: "job", generationId: "generation", kind: "answer", questionText: "预算确定了吗？", evidenceSegmentId: "segment", evidenceTranscriptSeq: 1, evidenceHash: "hash", stateRevision: 1, status: "committed", draftText: "", draftSeq: 1, text: "资料显示预算仍待确认。", finalDraftSeq: 1, feedback: null, createdAtMs: 1, updatedAtMs: 1, committedAtMs: 1, sourceEvidence: [ref] };
  const initial = createInitialMeetingState("review-sources"); const state = { ...initial, runtime: { ...initial.runtime, phase: "ended" as const }, suggestions: [answer] };
  const api = { getMeetingContext: vi.fn().mockResolvedValue({ projectId: "project", revision: 3 }), getMeetingSource: vi.fn().mockResolvedValue(ref) } as unknown as KnowledgeApi; const open = vi.fn();
  render(<ReviewWorkspace state={state} knowledgeApi={api} onKnowledgeSource={open} onReloadTranscript={vi.fn()} onReloadAudio={vi.fn()} onExport={vi.fn()} />);
  fireEvent.click(screen.getByRole("tab", { name: "会中建议与问答" })); fireEvent.click(screen.getByText(/查看全部依据/)); fireEvent.click(screen.getByRole("button", { name: /资料 · 已核对方案/ }));
  await waitFor(() => expect(open).toHaveBeenCalledWith(ref, "review-sources")); expect(api.getMeetingSource).toHaveBeenCalledWith("span", "review-sources");
  vi.mocked(api.getMeetingSource).mockResolvedValue({ ...ref, ordinal: 0, locator: { type: "pdf_page", page: 2 }, versionId: "version-2", contentHash: "hash-2" }); fireEvent.click(screen.getByRole("button", { name: /资料 · 已核对方案/ }));
  await screen.findByText(/此引用已过期/); expect(open).toHaveBeenCalledTimes(1); expect(screen.getByText("资料显示预算仍待确认。")).toBeVisible();
});

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "../../api/client";
import { ContributionControls } from "./ContributionControls";

afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

function revokedDiscussion() {
  return {
    work_item_id: "topic-current", meeting_id: "material-meeting", kind: "discussion",
    title: "IOI店铺经营与评价情况", state: "waiting_for_evidence", version: 5,
    context: {
      analysis_basis_revision: 9, last_semantic_seq: 10, last_checked_seq: 10, phase: "needs_review",
      current_attention: {
        contract: "discussion_attention.v1", control_revision: 2, covered_seq: 10,
        opportunity_id: null, source_bindings: [],
        current_focus: { observations: [], relation: "unknown", object: null, setting: null, purpose: null },
        contribution_opportunity: { status: "uncertain", intent: null,
          reason: "依据已更新，等待重新理解。", evidence_segment_ids: [], evidence_quote: "", opportunity_id: null },
      },
    },
  };
}
function mockItems(items: unknown[]) {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ items }),
    { status: 200, headers: { "Content-Type": "application/json" } })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("current attention through the actual discussion HTTP reader", () => {
  it("keeps a revoked waiting discussion historical when material changes preserve the semantic cursor", async () => {
    const fetchMock = mockItems([revokedDiscussion()]);
    const api = new HttpMeetingApi(); const contribute = vi.fn().mockResolvedValue("manual-job");
    render(<ContributionControls viewStateKey="material-meeting"
      currentTopic={{ id: "topic-current", text: "IOI店铺经营与评价情况", status: "active", updatedAtMs: null,
        lastUpdatedSeq: 10, evidenceSegmentIds: [] }}
      discussionRuntime={{ state: "idle", label: "宿主状态", detail: null, level: null,
        decision: "not_started", understandingStatus: "waiting_reanalysis", topicId: "topic-current",
        analysisBasisRevision: 9, inputTranscriptSeq: 10, lastSemanticSeq: 10 }}
      onReadDiscussion={(signal) => api.getDiscussion("material-meeting", signal)} onContribute={contribute} />);
    await screen.findByText("已记录的议题：IOI店铺经营与评价情况");
    fireEvent.click(screen.getByText("议题与判断"));
    expect(screen.getByText(/当前交流尚未确认，等待重新理解/)).toBeVisible();
    expect(screen.queryByText("正在讨论：IOI店铺经营与评价情况")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    expect(screen.queryByText(/有可参与的讨论点/)).not.toBeInTheDocument();
    expect(contribute).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/v2/meetings/material-meeting/work-items?kind=discussion&limit=20");
    expect(fetchMock.mock.calls[0][1].method).toBeUndefined();
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledWith("ask_question"));
  });

  it("preserves the waiting item identity, version and withdrawn snapshot for deliberate correction", async () => {
    const item = revokedDiscussion(); mockItems([item]);
    const value = await new HttpMeetingApi().getDiscussion("material-meeting");
    expect(value).toMatchObject({ id: "topic-current", state: "waiting_for_evidence", version: 5 });
    expect(value?.context.last_semantic_seq).toBe(10);
    expect(value?.context.current_attention).toEqual(item.context.current_attention);
  });

  it.each(["resolved", "cancelled", "expired", "completed"])("continues to exclude terminal discussion states (%s)", async (state) => {
    mockItems([{ ...revokedDiscussion(), state }]);
    expect(await new HttpMeetingApi().getDiscussion("material-meeting")).toBeNull();
  });

  it("reads the active waiting discussion after a terminal history item", async () => {
    mockItems([{ ...revokedDiscussion(), state: "resolved", work_item_id: "historical" }, revokedDiscussion()]);
    expect(await new HttpMeetingApi().getDiscussion("material-meeting")).toMatchObject({ id: "topic-current", state: "waiting_for_evidence" });
  });
});

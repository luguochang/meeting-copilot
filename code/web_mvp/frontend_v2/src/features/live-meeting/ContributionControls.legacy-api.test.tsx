import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HttpMeetingApi } from "../../api/client";
import type { RuntimeIndicator } from "../../domain/events";
import { ContributionControls } from "./ContributionControls";

afterEach(() => { sessionStorage.clear(); vi.unstubAllGlobals(); });

const title = "店铺团购配置";
function show(runtimeChanges: Partial<RuntimeIndicator> = {}, manual = false) {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [{
    work_item_id: "topic-legacy", meeting_id: "legacy-meeting", kind: "discussion",
    title, state: "investigating", version: 4,
    context: { analysis_basis_revision: 8, last_semantic_seq: 0, last_checked_seq: 7,
      phase: "discussing", inferred: !manual },
  }] }), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  const api = new HttpMeetingApi(); const contribute = vi.fn().mockResolvedValue("manual-job");
  const runtime: RuntimeIndicator = {
    state: "active", label: "正在重新理解", detail: null, level: null,
    topicId: "topic-legacy", analysisBasisRevision: 8,
    inputTranscriptSeq: 10, lastSemanticSeq: 0, decision: "not_started",
    understandingStatus: "waiting_reanalysis", ...runtimeChanges,
  };
  render(<ContributionControls viewStateKey="legacy-meeting"
    currentTopic={{ id: "topic-legacy", text: "店铺团购配置及最近的评价情况", status: "active",
      updatedAtMs: null, lastUpdatedSeq: 7, evidenceSegmentIds: [] }}
    discussionRuntime={runtime}
    onReadDiscussion={(signal) => api.getDiscussion("legacy-meeting", signal)}
    onContribute={contribute} />);
  return { fetchMock, contribute };
}

describe("legacy topic authority through the HTTP reader", () => {
  it.each(["not_started", "pending", "running"] as const)("marks the retained title historical when reanalysis %s has a different projected title", async (decision) => {
    const { fetchMock, contribute } = show({ decision });
    await screen.findByText(`上次记录的话题：${title}`);
    expect(screen.queryByText(`正在讨论：${title}`)).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "最近交流与参与判断" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/v2/meetings/legacy-meeting/work-items?kind=discussion&limit=20");
    expect(contribute).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "我可以问什么" }));
    await waitFor(() => expect(contribute).toHaveBeenCalledWith("ask_question"));
  });

  it("keeps an explicitly corrected topic authoritative", async () => {
    show({}, true);
    await screen.findByText(`正在讨论：${title}`);
    expect(screen.queryByText(`上次记录的话题：${title}`)).not.toBeInTheDocument();
  });

  it("distinguishes understood prior context from wholly withdrawn understanding", async () => {
    show({ decision: "running", lastSemanticSeq: 8, understandingStatus: "reanalysis_running" });
    await screen.findByText(`最近已理解的话题：${title}`);
    expect(screen.queryByText(`正在讨论：${title}`)).not.toBeInTheDocument();
  });

  it("does not mark a naturally caught-up topic historical merely because its title differs", async () => {
    show({ decision: "succeeded", lastSemanticSeq: 10, understandingStatus: undefined });
    await screen.findByText(`正在讨论：${title}`);
    expect(screen.queryByText(`上次记录的话题：${title}`)).not.toBeInTheDocument();
  });
});

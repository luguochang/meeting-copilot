import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseMeetingSnapshot } from "../../api/schema";
import { parseMaterialContext } from "../../domain/contribution";
import type { FollowUpProjection, MeetingEvent } from "../../domain/events";
import { createInitialMeetingState, meetingReducer } from "../../domain/reducer";
import { ContributionBasis } from "./ContributionBasis";

function coverage(provided = 1, characters = 400, selection = "whole_document", complete = false) {
  return { material_set_id: "materials", material_context_revision: 4, catalog_is_evidence: false, catalog_truncated: false,
    attachment_catalog: [{ attachment_id: "requirements", title: "下单需求", version_id: "v1", content_hash: "hash", sections: [], warnings: [],
      coverage: { authorized_span_count: 3, authorized_characters: 1200, provided_span_count: provided, provided_characters: characters, complete_read: complete, selection } }] };
}
function contribution(): FollowUpProjection {
  return { question: "通知失败是否影响订单成功？", reason: "需要明确事务边界", evidenceSegmentIds: ["segment"], evidenceQuote: "先同步通知", urgency: "medium", analysisKind: "participation", proposalType: "professional_proposal" };
}
it.each([
  [1, 400, "whole_document", false, "本次仅提供相关片段"],
  [3, 1200, "selected_spans", true, "所选范围已完整提供"],
  [3, 1200, "whole_document", true, "授权全文已完整提供"],
  [0, 0, "whole_document", false, "本次未提供正文片段"],
])("shows frozen provided coverage %s/%s/%s without claiming a model read", async (spans, chars, selection, complete, label) => {
  render(<ContributionBasis value={{ ...contribution(), materialContext: parseMaterialContext(coverage(spans, chars, selection, complete)) }} />);
  await userEvent.setup().click(screen.getByText("本次资料覆盖"));
  expect(screen.getByText(`已提供 ${spans} 段 / ${chars} 字；授权 3 段 / 1200 字`)).toBeVisible();
  expect(screen.getByText(label)).toBeVisible();
  expect(screen.queryByText(/已通读|AI 已读/)).not.toBeInTheDocument();
});

it("does not infer missing historical coverage or accept inconsistent provided counts", async () => {
  expect(parseMaterialContext(coverage(4, 1400))).toBeUndefined();
  expect(parseMaterialContext(coverage(1, 400, "whole_document", true))?.attachmentCatalog[0].coverage.completeRead).toBe(false);
  render(<ContributionBasis value={contribution()} />);
  await userEvent.setup().click(screen.getByText("本次资料覆盖"));
  expect(screen.getByText("未记录本次资料覆盖，不能据此判断是否提供全文。")).toBeVisible();
});

it("preserves the same request coverage through snapshot, SSE, history and retraction", () => {
  const materialContext = coverage();
  const raw = { ...contribution(), question: "通知失败是否影响订单成功？", reason: "需要明确事务边界", origin: "pi", status: "intervention", analysis_kind: "participation", decision_id: "decision", source: "llm_first", job_id: "job", batch_id: "batch", provider: "test", model: "test", llm_called: true, formal_evidence: { segment_ids: ["segment"], quote: "先同步通知" }, material_context: materialContext };
  const snapshot = parseMeetingSnapshot({ meeting_id: "meeting", last_seq: 1, segments: [], suggestions: [], coach_intervention: raw,
    coach_decision: { origin: "pi", status: "intervention", decision_id: "decision", material_context: materialContext },
    coach_history: [{ ...raw, history_id: "history", created_at_ms: 100 }], runtime: { phase: "live" } });
  const expected = parseMaterialContext(materialContext);
  expect(snapshot.followUp?.materialContext).toEqual(expected);
  expect(snapshot.coachDecision?.materialContext).toEqual(expected);
  expect(snapshot.coachHistory?.[0].materialContext).toEqual(expected);
  const event: MeetingEvent = { meetingId: "meeting", seq: 1, eventId: "event", type: "meeting.intelligence.applied", aggregateType: "intelligence", aggregateId: "meeting", occurredAtMs: 200, publishedAtMs: 200, correlationId: null, causationId: null, idempotencyKey: "event", payload: { source: "llm_first", llm_called: true, job_id: "job", batch_id: "batch", provider: "test", model: "test", evidence: { segment_ids: ["segment"], quote: "先同步通知" }, coach_intervention: raw, coach_decision: { origin: "pi", status: "intervention", decision_id: "decision", material_context: materialContext } } };
  const state = meetingReducer(createInitialMeetingState("meeting"), { type: "events.received", events: [event], receivedAtMs: 200 });
  expect(state.followUp?.materialContext).toEqual(expected);
  expect(state.coachHistory[0].materialContext).toEqual(expected);
  expect(state.coachDecision?.materialContext).toEqual(expected);
  const retracted = meetingReducer(state, { type: "events.received", events: [{ ...event, seq: 2, eventId: "retract", payload: { source: "host_input_invalidation", llm_called: false, coach_intervention: null, follow_up: null, coach_decision: { origin: "pi", status: "stale", lifecycle_action: "retract", supersedes_decision_id: "decision", decision_id: "retract" } } }], receivedAtMs: 300 });
  expect(retracted.followUp).toBeNull();
  expect(retracted.coachHistory[0].materialContext).toEqual(expected);
  const forged = meetingReducer(state, { type: "events.received", events: [{ ...event, seq: 2, eventId: "forged", payload: { source: "host_input_invalidation", llm_called: false, coach_intervention: raw, coach_decision: { origin: "pi", status: "intervention", decision_id: "forged" } } }], receivedAtMs: 300 });
  expect(forged.followUp?.decisionId).toBe("decision");
  expect(forged.coachHistory).toHaveLength(1);
});

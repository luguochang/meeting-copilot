import { HttpMeetingApi } from "./client";
import { parseMeetingSnapshot, parseMeetingEvent } from "./schema";
import { createInitialMeetingState, meetingReducer } from "../domain/reducer";

const receipt = { meeting_id: "synthetic-meeting", job_id: "synthetic/job", decision_id: "synthetic-decision", event_seq: 14, input_version: 2, draft_seq: 0 };
const provenance = { source: "llm_first", job_id: receipt.job_id, batch_id: "batch", provider: "openai_compatible_gateway", model: "fixture-model", llm_called: true, evidence: { segment_ids: ["synthetic-segment"] } };
const card = { ...provenance, origin: "pi", status: "intervention", decision_id: receipt.decision_id, say_this: "合成方案供讨论。", why_now: "合成方案边界尚待核对。", evidence_segment_ids: ["synthetic-segment"], evidence_quote: "尚待核对", urgency: "medium", analysis_kind: "participation", ui_render_receipt: receipt };
const snapshot = { meeting_id: receipt.meeting_id, last_seq: 18, segments: [], suggestions: [], coach_intervention: card, coach_decision: { ...card, meeting_id: receipt.meeting_id }, coach_history: [{ ...card, history_id: "history", created_at_ms: 1 }] };

it("reads the exact optional receipt through HttpMeetingApi and posts all six fields without replacing event identity by latest snapshot sequence", async () => {
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => new Response(JSON.stringify(init?.method === "POST" ? {} : snapshot), { status: 200, headers: { "content-type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  const api = new HttpMeetingApi();
  const value = await api.getSnapshot(receipt.meeting_id);
  expect(value.followUp?.uiRenderReceipt).toEqual({ meetingId: receipt.meeting_id, jobId: receipt.job_id, decisionId: receipt.decision_id, eventSeq: 14, inputVersion: 2, draftSeq: 0 });
  expect(value.coachHistory[0].uiRenderReceipt).toBeUndefined();
  const signal = new AbortController().signal;
  await api.markCoachUiRendered(value.followUp!.uiRenderReceipt!, signal);
  expect(fetcher).toHaveBeenLastCalledWith("/v2/traces/synthetic%2Fjob/ui-rendered", expect.objectContaining({ method: "POST", body: JSON.stringify(receipt), signal }));
});

it.each([null, { ...receipt, event_seq: 0 }, { ...receipt, event_seq: Number.MAX_SAFE_INTEGER + 1 }, { ...receipt, input_version: "2" }, { ...receipt, draft_seq: 1 }, { ...receipt, meeting_id: " synthetic-meeting" }, { ...receipt, future_field: "unknown" }])("ignores malformed optional receipt %j while retaining the original readable card", (untrusted) => {
  const value = parseMeetingSnapshot({ ...snapshot, coach_intervention: { ...card, ui_render_receipt: untrusted } });
  expect(value.followUp?.sayThis).toBe(card.say_this);
  expect(value.followUp?.uiRenderReceipt).toBeUndefined();
});

it("preserves a formal event receipt on its current card and removes it from retained history", () => {
  const event = parseMeetingEvent({ meeting_id: receipt.meeting_id, seq: 14, event_id: "event", type: "meeting.intelligence.applied", aggregate_type: "meeting_intelligence", aggregate_id: receipt.job_id, occurred_at_ms: 1, idempotency_key: "event", payload: { ...provenance, coach_intervention: card, coach_decision: { ...card, meeting_id: receipt.meeting_id } } });
  const state = meetingReducer(createInitialMeetingState(receipt.meeting_id), { type: "events.received", events: [event], receivedAtMs: 2 });
  expect(state.followUp?.uiRenderReceipt?.eventSeq).toBe(14);
  expect(state.coachHistory[0].uiRenderReceipt).toBeUndefined();
});

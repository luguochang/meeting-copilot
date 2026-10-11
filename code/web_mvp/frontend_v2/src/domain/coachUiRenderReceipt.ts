import type { CoachUiRenderReceipt } from "./events";

/** Invalid optional measurement data must never hide an otherwise readable card. */
export function parseCoachUiRenderReceipt(value: unknown): CoachUiRenderReceipt | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  const keys = ["meeting_id", "job_id", "decision_id", "event_seq", "input_version", "draft_seq"];
  if (Object.keys(raw).length !== keys.length || Object.keys(raw).some((key) => !keys.includes(key))) return undefined;
  const text = (item: unknown): item is string => typeof item === "string" && Boolean(item.trim()) && item === item.trim();
  const positive = (item: unknown): item is number => typeof item === "number" && Number.isSafeInteger(item) && item >= 1;
  if (!text(raw.meeting_id) || !text(raw.job_id) || !text(raw.decision_id)
    || !positive(raw.event_seq) || !positive(raw.input_version) || raw.draft_seq !== 0) return undefined;
  return { meetingId: raw.meeting_id, jobId: raw.job_id, decisionId: raw.decision_id,
    eventSeq: raw.event_seq, inputVersion: raw.input_version, draftSeq: 0 };
}

export function coachUiRenderReceiptWire(receipt: CoachUiRenderReceipt) {
  return { meeting_id: receipt.meetingId, job_id: receipt.jobId, decision_id: receipt.decisionId,
    event_seq: receipt.eventSeq, input_version: receipt.inputVersion, draft_seq: receipt.draftSeq };
}

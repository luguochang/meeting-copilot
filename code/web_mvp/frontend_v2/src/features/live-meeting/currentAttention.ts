import type { DiscussionWorkItem } from "../../api/client";
import type { RuntimeIndicator } from "../../domain/events";

type FocusRelation = "same_topic" | "changed" | "interlude" | "uncertain" | "unknown";
export interface CurrentAttentionView {
  observations: string[];
  relation: FocusRelation;
  status: "present" | "none" | "uncertain";
  reason: string;
  coveredSeq: number;
}
type RecordValue = Record<string, unknown>;
function record(value: unknown): value is RecordValue {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function fields(value: unknown, keys: string[]): value is RecordValue {
  return record(value) && Object.keys(value).length === keys.length
    && keys.every((key) => Object.hasOwn(value, key));
}
function integer(value: unknown, minimum = 0): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= minimum;
}
function text(value: unknown, limit: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && Array.from(value).length <= limit
    && !Array.from(value).some((character) => character.charCodeAt(0) < 32 && ![9, 10, 13].includes(character.charCodeAt(0)));
}
function identity(value: unknown): value is string {
  return text(value, 240) && !/[\r\n\t]/.test(value);
}
function quote(value: unknown, bindings: Set<string>, allowEmpty = false): value is RecordValue {
  if (!fields(value, ["evidence_segment_ids", "evidence_quote"])) return false;
  const ids = value.evidence_segment_ids;
  if (!Array.isArray(ids) || ids.length > 4 || new Set(ids).size !== ids.length
    || ids.some((id) => !identity(id) || !bindings.has(id))) return false;
  return allowEmpty && ids.length === 0 && value.evidence_quote === ""
    || ids.length > 0 && text(value.evidence_quote, 300);
}

/** Display only the host snapshot; citation identity is not a claim of scene truth. */
export function parseCurrentAttention(value: unknown): CurrentAttentionView | null {
  try {
    if (Array.from(JSON.stringify(value) ?? "").length > 6000) return null;
  } catch { return null; }
  if (!fields(value, ["current_focus", "contribution_opportunity", "contract", "control_revision", "opportunity_id", "covered_seq", "source_bindings"])
    || value.contract !== "discussion_attention.v1" || !integer(value.control_revision, 1)
    || !integer(value.covered_seq, 1) || !Array.isArray(value.source_bindings)
    || value.source_bindings.length < 1 || value.source_bindings.length > 12) return null;
  const bindings = new Set<string>();
  for (const binding of value.source_bindings) {
    if (!fields(binding, ["segment_id", "revision", "evidence_hash"]) || !identity(binding.segment_id)
      || !integer(binding.revision, 1) || typeof binding.evidence_hash !== "string"
      || !/^[a-f0-9]{64}$/.test(binding.evidence_hash) || bindings.has(binding.segment_id)) return null;
    bindings.add(binding.segment_id);
  }
  const focus = value.current_focus;
  if (!fields(focus, ["observations", "relation", "object", "setting", "purpose"])
    || typeof focus.relation !== "string" || !["same_topic", "changed", "interlude", "uncertain", "unknown"].includes(focus.relation)
    || !Array.isArray(focus.observations) || focus.observations.length < 1 || focus.observations.length > 2
    || focus.observations.some((item) => !quote(item, bindings))
    || new Set(focus.observations.map((item) => JSON.stringify(item))).size !== focus.observations.length
    || ["object", "setting", "purpose"].some((key) => focus[key] !== null && !quote(focus[key], bindings))) return null;
  const opportunity = value.contribution_opportunity;
  if (!fields(opportunity, ["status", "intent", "reason", "evidence_segment_ids", "evidence_quote", "opportunity_id"])
    || typeof opportunity.status !== "string" || !["present", "none", "uncertain"].includes(opportunity.status)
    || !text(opportunity.reason, 200)
    || opportunity.opportunity_id !== null && !identity(opportunity.opportunity_id)
    || !quote({ evidence_segment_ids: opportunity.evidence_segment_ids, evidence_quote: opportunity.evidence_quote }, bindings, opportunity.status !== "present")) return null;
  if (opportunity.status === "present") {
    if (!identity(value.opportunity_id) || typeof opportunity.intent !== "string"
      || !["ask_question", "compare_options", "formulate_response", "freeform"].includes(opportunity.intent)
      || opportunity.opportunity_id !== null && opportunity.opportunity_id !== value.opportunity_id) return null;
  } else if (opportunity.intent !== null || value.opportunity_id !== null) return null;
  return {
    observations: focus.observations.map((item) => item.evidence_quote as string),
    relation: focus.relation as FocusRelation, status: opportunity.status as CurrentAttentionView["status"],
    reason: opportunity.reason, coveredSeq: value.covered_seq,
  };
}

export function currentAttentionPresentation(discussion: DiscussionWorkItem | null, meetingId: string,
  runtime?: RuntimeIndicator | null): { attention: CurrentAttentionView | null; hasSnapshot: boolean } {
  if (!discussion || !Object.hasOwn(discussion.context, "current_attention")) return { attention: null, hasSnapshot: false };
  const attention = parseCurrentAttention(discussion.context.current_attention);
  const meeting = (discussion as unknown as RecordValue).meeting_id;
  const basis = discussion.analysis_basis_revision ?? discussion.context.analysis_basis_revision;
  const semantic = discussion.context.last_semantic_seq;
  const checked = discussion.context.last_checked_seq;
  const stale = !attention || !integer(semantic, 1) || semantic !== attention.coveredSeq
    || discussion.context.phase === "needs_review"
    || meeting !== undefined && meeting !== meetingId
    || integer(checked) && checked > attention.coveredSeq
    || runtime?.topicId != null && runtime.topicId !== discussion.id
    || runtime?.analysisBasisRevision != null && runtime.analysisBasisRevision !== basis
    || runtime?.inputTranscriptSeq != null && (!integer(runtime.inputTranscriptSeq) || runtime.inputTranscriptSeq !== attention.coveredSeq)
    || runtime?.lastSemanticSeq != null && (!integer(runtime.lastSemanticSeq) || runtime.lastSemanticSeq < attention.coveredSeq);
  return { attention: stale ? null : attention, hasSnapshot: true };
}

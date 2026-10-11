export type CoachRequestStatus = "pending" | "succeeded" | "failed" | "timed_out" | "no_change";
export type CoachRequestFailureCategory = "evidence_rejected" | "validation_rejected" | "timeout" | "provider_unavailable" | "execution_failed" | "cancelled" | "unknown";
export interface CoachRequestOutcome {
  meetingId: string;
  jobId: string;
  status: CoachRequestStatus;
  failureCategory: CoachRequestFailureCategory | null;
  canRetry: boolean;
  topicId: string | null;
  analysisBasisRevision: number | null;
}

type RecordValue = Record<string, unknown>;
function record(value: unknown): RecordValue { return value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {}; }
function rows(value: unknown): RecordValue[] { return Array.isArray(value) ? value.map(record) : []; }
function boundedIdentity(value: unknown): string | null { return typeof value === "string" && value.length > 0 && value.length <= 200 && !Array.from(value).some((character) => character.charCodeAt(0) < 32) ? value : null; }
const FAILURE_CODES: Record<string, CoachRequestFailureCategory> = {
  evidence_rejected: "evidence_rejected", intelligence_validation_evidence: "evidence_rejected", pi_evidence_validation_failed: "evidence_rejected",
  IntelligenceResponseValidationError: "validation_rejected", response_validation_error: "validation_rejected", validation_error: "validation_rejected", invalid_output: "validation_rejected", pi_output_invalid: "validation_rejected",
  pi_timeout: "timeout", provider_timeout: "timeout", request_timeout: "timeout", analysis_timeout: "timeout", timed_out: "timeout",
  pi_unavailable: "provider_unavailable", pi_spawn_failed: "provider_unavailable", provider_unavailable: "provider_unavailable", provider_config_missing: "provider_unavailable", provider_network_error: "provider_unavailable", provider_rate_limited: "provider_unavailable",
  execution_interrupted: "execution_failed", worker_exit: "execution_failed", worker_error: "execution_failed", provider_error: "execution_failed", PiCoachRuntimeError: "execution_failed",
  preempted_by_user_request: "cancelled", cancelled: "cancelled",
};
function failureCategory(...values: RecordValue[]): CoachRequestFailureCategory {
  for (const value of values) for (const key of ["failure_code", "error_class", "fallback_error_code", "status_reason", "llm_call_status"]) {
    const code = value[key];
    if (typeof code === "string" && Object.hasOwn(FAILURE_CODES, code)) return FAILURE_CODES[code];
  }
  return "unknown";
}

/** Only published, exact-job metadata is interpreted; rejected output and free-form reasons never leave this boundary. */
export function coachRequestOutcomeFromSnapshot(meetingId: string, jobId: string, value: unknown): CoachRequestOutcome {
  const snapshot = record(value);
  const job = rows(snapshot.jobs).find((item) => item.id === jobId) ?? {};
  const decision = record(snapshot.coach_decision);
  const ownDecision = decision.job_id === jobId ? decision : {};
  const diagnostic = rows(record(snapshot.diagnostics).coach_runtime_history).filter((item) => item.job_id === jobId).at(-1) ?? {};
  const context = record(job.request_context);
  const topicId = boundedIdentity(context.topic_id ?? ownDecision.topic_id ?? diagnostic.topic_id);
  const basis = context.analysis_basis_revision ?? ownDecision.analysis_basis_revision ?? diagnostic.analysis_basis_revision;
  const validBasis = typeof basis === "number" && Number.isSafeInteger(basis) && basis >= 0 ? basis : null;
  const base = { meetingId, jobId, topicId: validBasis !== null ? topicId : null, analysisBasisRevision: topicId !== null ? validBasis : null };
  const failed = (category: CoachRequestFailureCategory, status: "failed" | "timed_out" = "failed"): CoachRequestOutcome => ({ ...base, status, failureCategory: category, canRetry: ["timeout", "provider_unavailable", "execution_failed"].includes(category) });
  if (snapshot.meeting_id !== undefined && snapshot.meeting_id !== meetingId || job.meeting_id !== undefined && job.meeting_id !== meetingId) return failed("unknown");
  if (ownDecision.status === "timed_out" || diagnostic.status === "timed_out") return failed("timeout", "timed_out");
  if (ownDecision.status === "failed" || diagnostic.status === "failed" || diagnostic.outcome === "failure") return failed(failureCategory(ownDecision, diagnostic, job));
  if (job.status === "cancelled") return failed("cancelled");
  if (job.status === "failed") return failed(failureCategory(job, ownDecision, diagnostic));
  if ([ownDecision.status, diagnostic.status].some((status) => status === "stale")) return failed("unknown");
  if (ownDecision.status === "intervention" || rows(snapshot.coach_history).some((item) => item.job_id === jobId && item.status === "intervention") || diagnostic.status === "intervention") return { ...base, status: "succeeded", failureCategory: null, canRetry: false };
  if ([ownDecision.status, diagnostic.status].some((status) => status === "protected_silent" || status === "not_triggered") || diagnostic.outcome === "silent") return { ...base, status: "no_change", failureCategory: null, canRetry: false };
  if (job.status === "succeeded" || !job.id) return failed("unknown");
  return { ...base, status: "pending", failureCategory: null, canRetry: false };
}

import type { CoachAgentMetrics, CoachAgentToolError } from "./events";

type JsonRecord = Record<string, unknown>;

function record(value: unknown): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonRecord
    : null;
}

function first(value: JsonRecord, snake: string, camel: string): unknown {
  return value[snake] ?? value[camel];
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function boundedText(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim().slice(0, 128) : undefined;
}

function boundedStrings(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const result = value
    .slice(0, 24)
    .map(boundedText)
    .filter((item): item is string => item !== undefined);
  return result.length ? result : undefined;
}

function toolErrors(value: unknown): CoachAgentToolError[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const result = value.slice(0, 24).flatMap((item) => {
    const error = record(item);
    if (!error) return [];
    const tool = boundedText(error.tool ?? error.name);
    const code = boundedText(error.code);
    return tool || code ? [{ ...(tool ? { tool } : {}), ...(code ? { code } : {}) }] : [];
  });
  return result.length ? result : undefined;
}

const NUMBER_FIELDS = {
  elapsedMs: ["elapsed_ms", "elapsedMs"],
  decisionLatencyMs: ["decision_latency_ms", "decisionLatencyMs"],
  ttftMs: ["ttft_ms", "ttftMs"],
  turns: ["turns", "turns"],
  toolCalls: ["tool_calls", "toolCalls"],
  contextReads: ["context_reads", "contextReads"],
  checklistReviews: ["checklist_reviews", "checklistReviews"],
  historySearches: ["history_searches", "historySearches"],
  historyResults: ["history_results", "historyResults"],
  decisionLatencyBudgetMs: ["decision_latency_budget_ms", "decisionLatencyBudgetMs"],
  decisionTimeoutMs: ["decision_timeout_ms", "decisionTimeoutMs"],
  promptCharacters: ["prompt_characters", "promptCharacters"],
  sessionMessageCountBefore: ["session_message_count_before", "sessionMessageCountBefore"],
  sidecarQueueMs: ["sidecar_queue_ms", "sidecarQueueMs"],
  bridgeStartupMs: ["bridge_startup_ms", "bridgeStartupMs"],
  bridgeRoundTripMs: ["bridge_round_trip_ms", "bridgeRoundTripMs"],
  jobQueueLatencyMs: ["job_queue_latency_ms", "jobQueueLatencyMs"],
  jobBudgetRemainingAtCoachStartMs: ["job_budget_remaining_at_coach_start_ms", "jobBudgetRemainingAtCoachStartMs"],
  softBudgetRemainingAtCoachStartMs: ["soft_budget_remaining_at_coach_start_ms", "softBudgetRemainingAtCoachStartMs"],
  softProviderBudgetMs: ["soft_provider_budget_ms", "softProviderBudgetMs"],
  softDeadlineAtMs: ["soft_deadline_at_ms", "softDeadlineAtMs"],
  softCutoffElapsedMs: ["soft_cutoff_elapsed_ms", "softCutoffElapsedMs"],
  softTimeoutProjectionAtMs: ["soft_timeout_projection_at_ms", "softTimeoutProjectionAtMs"],
  lateResultCompletedAtMs: ["late_result_completed_at_ms", "lateResultCompletedAtMs"],
  providerTimeoutMs: ["provider_timeout_ms", "providerTimeoutMs"],
  coachSkillVersion: ["coach_skill_version", "coachSkillVersion"],
} as const;

const BOOLEAN_FIELDS = {
  checklistReviewed: ["checklist_reviewed", "checklistReviewed"],
  withinLatencyBudget: ["within_latency_budget", "withinLatencyBudget"],
  sessionReused: ["session_reused", "sessionReused"],
  bridgeProcessReused: ["bridge_process_reused", "bridgeProcessReused"],
  correctionLaneActiveAtCoachStart: ["correction_lane_active_at_coach_start", "correctionLaneActiveAtCoachStart"],
  interventionSuppressed: ["intervention_suppressed", "interventionSuppressed"],
  fallbackSuppressed: ["fallback_suppressed", "fallbackSuppressed"],
  softCutoffTriggered: ["soft_cutoff_triggered", "softCutoffTriggered"],
  lateResultDiscarded: ["late_result_discarded", "lateResultDiscarded"],
} as const;

export function parseCoachAgentMetrics(value: unknown): CoachAgentMetrics | undefined {
  const source = record(value);
  if (!source) return undefined;
  const result: CoachAgentMetrics = {};
  for (const [key, [snake, camel]] of Object.entries(NUMBER_FIELDS)) {
    const parsed = finiteNumber(first(source, snake, camel));
    if (parsed !== undefined) Object.assign(result, { [key]: parsed });
  }
  for (const [key, [snake, camel]] of Object.entries(BOOLEAN_FIELDS)) {
    const parsed = first(source, snake, camel);
    if (typeof parsed === "boolean") Object.assign(result, { [key]: parsed });
  }
  const coachSkillId = boundedText(first(source, "coach_skill_id", "coachSkillId"));
  const suppressionReason = boundedText(first(source, "suppression_reason", "suppressionReason"));
  const checklistItemIds = boundedStrings(first(source, "checklist_item_ids", "checklistItemIds"));
  const toolNames = boundedStrings(first(source, "tool_names", "toolNames"));
  const parsedToolErrors = toolErrors(first(source, "tool_errors", "toolErrors"));
  Object.assign(result, {
    ...(coachSkillId ? { coachSkillId } : {}),
    ...(suppressionReason ? { suppressionReason } : {}),
    ...(checklistItemIds ? { checklistItemIds } : {}),
    ...(toolNames ? { toolNames } : {}),
    ...(parsedToolErrors ? { toolErrors: parsedToolErrors } : {}),
  });

  const rawTimings = record(source.timings);
  if (rawTimings) {
    const clock = rawTimings.clock === "unix_epoch_ms" || rawTimings.clock === "monotonic_ms"
      ? rawTimings.clock
      : undefined;
    const startedAtMs = finiteNumber(first(rawTimings, "started_at_ms", "startedAtMs"));
    const firstTokenAtMs = finiteNumber(first(rawTimings, "first_token_at_ms", "firstTokenAtMs"));
    const completedAtMs = finiteNumber(first(rawTimings, "completed_at_ms", "completedAtMs"));
    if (clock || startedAtMs !== undefined || firstTokenAtMs !== undefined || completedAtMs !== undefined) {
      result.timings = {
        ...(clock ? { clock } : {}),
        ...(startedAtMs !== undefined ? { startedAtMs } : {}),
        ...(firstTokenAtMs !== undefined ? { firstTokenAtMs } : {}),
        ...(completedAtMs !== undefined ? { completedAtMs } : {}),
      };
    }
  }

  const rawUsage = record(source.usage);
  if (rawUsage) {
    const promptTokens = finiteNumber(first(rawUsage, "prompt_tokens", "promptTokens"));
    const completionTokens = finiteNumber(first(rawUsage, "completion_tokens", "completionTokens"));
    const totalTokens = finiteNumber(first(rawUsage, "total_tokens", "totalTokens"));
    if (promptTokens !== undefined || completionTokens !== undefined || totalTokens !== undefined) {
      result.usage = {
        ...(promptTokens !== undefined ? { promptTokens } : {}),
        ...(completionTokens !== undefined ? { completionTokens } : {}),
        ...(totalTokens !== undefined ? { totalTokens } : {}),
      };
    }
  }
  return Object.keys(result).length ? result : undefined;
}

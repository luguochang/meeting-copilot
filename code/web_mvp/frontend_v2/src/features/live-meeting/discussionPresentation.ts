import type { RuntimeIndicator } from "../../domain/events";

/** A semantic cursor gap is a caution about understanding, not card invalidity. */
export function hasUnprocessedDiscussion(runtime?: RuntimeIndicator | null): boolean {
  if (!runtime || !["not_started", "pending", "running", "retry_wait", "failed", "cancelled"].includes(runtime.decision ?? "")) return false;
  const input = runtime.inputTranscriptSeq;
  const understood = runtime.lastSemanticSeq;
  return typeof input === "number" && Number.isSafeInteger(input)
    && typeof understood === "number" && Number.isSafeInteger(understood)
    && understood > 0 && input > understood;
}

/** A withdrawn semantic cursor cannot certify an older stored topic as current. */
export function hasUnverifiedHistoricalTopic(runtime: RuntimeIndicator | null | undefined, topicSeq: number | undefined): boolean {
  const correctedEvidencePending = runtime
    && ["not_started", "pending", "running", "retry_wait"].includes(runtime.decision ?? "")
    && ["waiting_reanalysis", "reanalysis_queued", "reanalysis_running", "revision_deferred", "reanalysis_deferred"].includes(runtime.understandingStatus ?? "");
  return Boolean(runtime && ["not_started", "pending", "running", "retry_wait", "failed", "cancelled"].includes(runtime.decision ?? "")
    && runtime.lastSemanticSeq === 0 && Number.isSafeInteger(runtime.inputTranscriptSeq)
    && typeof topicSeq === "number" && Number.isSafeInteger(topicSeq) && topicSeq > 0
    && ((runtime.inputTranscriptSeq as number) > topicSeq
      || (correctedEvidencePending && runtime.inputTranscriptSeq === topicSeq)));
}

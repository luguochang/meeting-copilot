import type { TranscriptSegment } from "./events";

export function isUncorrectedRecovery(segment: TranscriptSegment): boolean {
  return segment.recoveryProvenance?.recognition === "local_batch_asr"
    && segment.recoveryProvenance.correction === "not_requested"
    && (segment.correctionStatus === undefined || ["pending", "not_requested"].includes(segment.correctionStatus));
}

const NO_REVISION_STATUSES = new Set([
  "no_revision_needed",
  "combined_no_revision_needed",
]);

const FAILURE_STATUSES = new Set([
  "correction_rejected",
  "combined_rejected",
  "provider_failed",
  "provider_failed_terminal",
  "provider_error",
  "mapping_rejected",
  "degraded",
  "failed",
]);

function uniqueStringValues(value) {
  const values = Array.isArray(value) ? value : [];
  return new Set(values.map((item) => String(item || "").trim()).filter(Boolean));
}

function correctionFailureReason(status) {
  if (status === "partially_completed") return "correction_processing_incomplete";
  if (status === "mapping_rejected") return "correction_mapping_rejected";
  if (status === "provider_error" || status === "provider_failed" || status === "provider_failed_terminal") {
    return "correction_provider_failed";
  }
  if (status === "degraded") return "correction_degraded";
  return "correction_rejected";
}

export function buildRealtimeTranscriptCompactionReport({
  derivationMode: mode = "production_enabled",
  correctionEnabled = null,
  correctionStatus = {},
  recordingPhaseUiSamples: samples = [],
  recordingStartedAtEpochMs = null,
} = {}) {
  const safeSamples = Array.isArray(samples) ? samples : [];
  const maxActiveLivePartialCount = Math.max(
    0,
    ...safeSamples.map((sample) => Number(sample.active_live_partial_count || 0)),
  );
  const maxCommittedTranscriptRowCount = Math.max(
    0,
    ...safeSamples.map((sample) => Number(sample.committed_transcript_row_count || 0)),
  );
  const maxCorrectedTranscriptRowCount = Math.max(
    0,
    ...safeSamples.map((sample) => Number(sample.corrected_transcript_row_count || 0)),
  );
  const visibleCorrectedSegmentIds = new Set(
    safeSamples.flatMap((sample) => Array.isArray(sample.corrected_transcript_segment_ids)
      ? sample.corrected_transcript_segment_ids
      : []),
  );
  const visibleCorrectedSourceSegmentIds = new Set(
    safeSamples.flatMap((sample) => Array.isArray(sample.corrected_transcript_source_segment_ids)
      ? sample.corrected_transcript_source_segment_ids
      : []),
  );
  const maxRowsForSingleActiveSegment = Math.max(
    0,
    ...safeSamples.map((sample) => Number(sample.max_rows_for_single_active_segment || 0)),
  );
  const firstCorrectionSample = safeSamples.find(
    (sample) => Number(sample.corrected_transcript_row_count || 0) > 0,
  ) || null;
  const firstCorrectionVisibleLatencyMs = firstCorrectionSample
    && Number.isFinite(Number(recordingStartedAtEpochMs))
    ? Math.max(0, Number(firstCorrectionSample.at_ms || 0) - Number(recordingStartedAtEpochMs))
    : null;
  const statusFromBackend = String(correctionStatus.status || "");
  const rejectedSegmentIds = uniqueStringValues([
    ...(correctionStatus.combined_rejected_segment_ids || []),
    ...(correctionStatus.rejected_segment_ids || []),
  ]);
  const failedSegmentIds = uniqueStringValues([
    ...(correctionStatus.failed_segment_ids || []),
    ...(correctionStatus.terminal_failed_segment_ids || []),
  ]);
  const revisedSegmentIds = uniqueStringValues(correctionStatus.revised_segment_ids);
  const attemptedSegmentIds = uniqueStringValues([
    ...(correctionStatus.combined_attempted_segment_ids || []),
    ...(correctionStatus.processed_segment_ids || []),
  ]);
  const attemptedSegmentCount = attemptedSegmentIds.size
    + (Array.isArray(correctionStatus.batch_audits) ? correctionStatus.batch_audits.length : 0);
  const rejectedSegmentCount = rejectedSegmentIds.size;
  const failedSegmentCount = failedSegmentIds.size;
  const correctedTargetEvidenceCount = [...revisedSegmentIds]
    .filter((segmentId) => visibleCorrectedSegmentIds.has(segmentId))
    .length;
  const hasVisibleCorrectionEvidence = maxCorrectedTranscriptRowCount > 0
    || visibleCorrectedSegmentIds.size > 0
    || visibleCorrectedSourceSegmentIds.size > 0;
  const effectiveCorrectionEnabled = correctionEnabled === null || correctionEnabled === undefined
    ? statusFromBackend !== "correction_disabled_by_setting" && (
      Boolean(statusFromBackend)
      || revisedSegmentIds.size > 0
      || attemptedSegmentCount > 0
      || rejectedSegmentCount > 0
      || failedSegmentCount > 0
    )
      ? true
      : null
    : Boolean(correctionEnabled);

  let status = "failed_realtime_correction_not_visible";
  let classificationReason = null;
  if (maxRowsForSingleActiveSegment > 1) {
    status = "failed_duplicate_active_segment_rows";
    classificationReason = "duplicate_active_segment_rows";
  } else if (effectiveCorrectionEnabled === false || statusFromBackend === "correction_disabled_by_setting") {
    status = "correction_disabled_by_setting";
    classificationReason = "correction_disabled_by_setting";
  } else if (
    statusFromBackend === "partially_completed"
    && revisedSegmentIds.size > 0
    && correctedTargetEvidenceCount > 0
  ) {
    status = "passed_partial_correction_visible";
    classificationReason = "partial_correction_with_rejected_segments";
  } else if (rejectedSegmentCount > 0 || failedSegmentCount > 0 || FAILURE_STATUSES.has(statusFromBackend)) {
    status = "failed_realtime_correction_not_visible";
    classificationReason = correctionFailureReason(statusFromBackend);
  } else if (revisedSegmentIds.size > 0) {
    if (correctedTargetEvidenceCount > 0) {
      status = "passed_compacted_realtime_correction_visible";
    } else {
      classificationReason = "revised_evidence_missing";
    }
  } else if (hasVisibleCorrectionEvidence) {
    classificationReason = "revised_evidence_missing";
  } else if (NO_REVISION_STATUSES.has(statusFromBackend)) {
    status = "no_revision_needed";
    classificationReason = "no_revision_needed";
  } else if (
    statusFromBackend === "completed"
    || statusFromBackend === "partially_completed"
    || (effectiveCorrectionEnabled === true && attemptedSegmentCount > 0)
    || (mode === "production_enabled" && maxCommittedTranscriptRowCount > 0)
  ) {
    classificationReason = statusFromBackend === "partially_completed"
      ? "correction_processing_incomplete"
      : "correction_evidence_missing";
  }

  return {
    report_type: "realtime_transcript_compaction_report",
    status,
    derivation_mode: mode,
    correction_enabled: effectiveCorrectionEnabled,
    correction_status: statusFromBackend || null,
    classification_reason: classificationReason,
    correction_observed: correctedTargetEvidenceCount > 0,
    correction_attempted: attemptedSegmentCount > 0 || revisedSegmentIds.size > 0,
    rejected_segment_count: rejectedSegmentCount,
    failed_segment_count: failedSegmentCount,
    max_active_live_partial_count: maxActiveLivePartialCount,
    max_committed_transcript_row_count: maxCommittedTranscriptRowCount,
    max_corrected_transcript_row_count: maxCorrectedTranscriptRowCount,
    corrected_target_evidence_count: correctedTargetEvidenceCount,
    visible_corrected_segment_ids: [...visibleCorrectedSegmentIds],
    visible_corrected_source_segment_ids: [...visibleCorrectedSourceSegmentIds],
    revised_segment_ids: [...revisedSegmentIds],
    max_rows_for_single_active_segment: maxRowsForSingleActiveSegment,
    first_correction_visible_latency_ms: firstCorrectionVisibleLatencyMs,
    sample_count: safeSamples.length,
  };
}

export function realtimeTranscriptCompactionStatusFails(status = "") {
  return String(status).startsWith("failed_");
}

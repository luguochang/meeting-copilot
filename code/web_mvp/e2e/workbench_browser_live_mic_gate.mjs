const REALTIME_PASS_STATUSES = new Set([
  "passed_realtime_full",
  "passed_realtime_partial_final_missing",
  "passed_realtime_partial_final_slow",
]);

const LIVE_REMINDER_NON_FAILURE_STATUSES = new Set([
  "passed",
  "not_evaluated_no_recording_backend_candidates",
]);

const REALTIME_AI_SUGGESTION_NON_FAILURE_STATUSES = new Set([
  "not_required_no_cost",
  "passed_realtime_ai_suggestion_visible",
]);

const MAINLINE_PASS_STATUSES = new Set([
  "passed_no_cost_mainline",
  "passed_production_mainline",
]);

export function buildMainlineCompletionReport({
  derivationMode = "production_enabled",
  asrProbe = {},
  audioExportProbe = {},
  uiReport = {},
} = {}) {
  const blockers = [];
  const acceptanceBlockers = Array.isArray(asrProbe.acceptance_blockers)
    ? asrProbe.acceptance_blockers.map(String)
    : [];
  const suggestionCardCount = nonNegativeNumber(asrProbe.suggestion_card_count) ?? 0;
  const approachCardCount = nonNegativeNumber(asrProbe.approach_card_count) ?? 0;
  const minutesCharCount = nonNegativeNumber(asrProbe.minutes_char_count) ?? 0;
  const frontendCardCount = nonNegativeNumber(uiReport.frontend_card_count) ?? 0;
  const audioHttpStatus = nonNegativeNumber(audioExportProbe.audio_export_http_status);
  const audioFileSize = nonNegativeNumber(audioExportProbe.audio_file_size_bytes) ?? 0;

  if (asrProbe.acceptance_eligible !== true || acceptanceBlockers.length > 0) {
    blockers.push("asr_acceptance_blocked");
  }
  if (asrProbe.derivations_generated !== true) blockers.push("derivations_not_generated");
  if (suggestionCardCount < 1) blockers.push("suggestion_cards_missing");
  if (approachCardCount < 1) blockers.push("approach_cards_missing");
  if (minutesCharCount < 20) blockers.push("minutes_missing");
  if ((suggestionCardCount + approachCardCount) > 0 && asrProbe.all_cards_have_evidence !== true) {
    blockers.push("card_evidence_missing");
  }
  if (audioHttpStatus !== 200 || audioFileSize <= 0 || audioExportProbe.audio_sha256_matches_session !== true) {
    blockers.push("audio_export_missing_or_mismatched");
  }
  if (uiReport.workbench_same_session_visible !== true) blockers.push("workbench_same_session_not_visible");
  if (frontendCardCount < 2) blockers.push("frontend_cards_missing");
  if (uiReport.frontend_minutes_visible !== true || String(uiReport?.meeting_cockpit_stage?.state || "") !== "reviewed") {
    blockers.push("frontend_review_not_complete");
  }
  if ((nonNegativeNumber(uiReport.browser_console_error_count) ?? 0) > 0) blockers.push("browser_console_errors");
  if ((nonNegativeNumber(uiReport.network_error_count) ?? 0) > 0) blockers.push("browser_network_errors");

  if (derivationMode === "production_enabled") {
    const productionEvidence = asrProbe.counts_as_production_llm_evidence === true
      && asrProbe.llm_called === true
      && (nonNegativeNumber(asrProbe.llm_call_count) ?? 0) > 0
      && (nonNegativeNumber(asrProbe.llm_usage_total_tokens) ?? 0) > 0
      && asrProbe.is_mock === false
      && String(asrProbe.gateway_base_url_kind || "") === "remote";
    if (!productionEvidence) blockers.push("production_llm_evidence_missing");
  }

  return {
    status: blockers.length
      ? "failed_mainline_completion"
      : derivationMode === "production_enabled"
        ? "passed_production_mainline"
        : "passed_no_cost_mainline",
    derivation_mode: derivationMode,
    acceptance_eligible: asrProbe.acceptance_eligible === true,
    acceptance_blockers: acceptanceBlockers,
    suggestion_card_count: suggestionCardCount,
    approach_card_count: approachCardCount,
    minutes_char_count: minutesCharCount,
    frontend_card_count: frontendCardCount,
    audio_export_http_status: audioHttpStatus,
    audio_file_size_bytes: audioFileSize,
    audio_sha256_matches_session: audioExportProbe.audio_sha256_matches_session === true,
    blockers,
  };
}

export function buildRealtimeExperienceReport({
  uiReport = {},
  healthStatus = "",
  recordingPhaseUiSamples = [],
  textLatencySloMs = 15_000,
  finalLatencySloMs = 60_000,
} = {}) {
  const textSlo = positiveFiniteNumber(textLatencySloMs);
  const finalSlo = positiveFiniteNumber(finalLatencySloMs);
  const firstTextLatency = finiteNumber(uiReport.first_text_after_audio_active_latency_ms);
  const firstFinalLatency = finiteNumber(uiReport.first_final_after_audio_active_latency_ms);
  const partialVisibleCount = nonNegativeNumber(uiReport.partial_visible_count) ?? 0;
  const finalVisibleCount = nonNegativeNumber(uiReport.final_visible_count) ?? 0;
  const samples = Array.isArray(recordingPhaseUiSamples) ? recordingPhaseUiSamples : [];
  const recordingTextVisible = samples.some(recordingSampleHasText);
  const blockers = [];
  const warnings = [];
  let status = "passed_realtime_full";

  if (textSlo === null || finalSlo === null) {
    status = "failed_invalid_slo_configuration";
    if (textSlo === null) blockers.push("text_latency_slo_invalid");
    if (finalSlo === null) blockers.push("final_latency_slo_invalid");
  } else if (healthStatus !== "audio_capture_health_passed") {
    status = "failed_audio_capture_health";
    blockers.push("audio_capture_health_not_passed");
  } else if (samples.length === 0) {
    status = "failed_realtime_recording_samples_missing";
    blockers.push("recording_phase_ui_samples_missing");
  } else if (!recordingTextVisible) {
    status = "failed_realtime_text_not_visible_during_recording";
    blockers.push("realtime_text_not_visible_during_recording");
  } else if (firstTextLatency === null || (partialVisibleCount + finalVisibleCount) <= 0) {
    status = "failed_realtime_text_not_visible";
    blockers.push("realtime_text_not_visible");
  } else if (firstTextLatency > textSlo) {
    status = "failed_realtime_text_slow";
    blockers.push("realtime_text_latency_slo_exceeded");
  } else if (firstFinalLatency === null || finalVisibleCount <= 0) {
    status = "passed_realtime_partial_final_missing";
    warnings.push("final_text_not_visible_during_measurement");
  } else if (firstFinalLatency > finalSlo) {
    status = "passed_realtime_partial_final_slow";
    warnings.push("final_latency_slo_exceeded");
  }

  return {
    status,
    health_status: healthStatus || "unknown",
    text_latency_slo_ms: textSlo,
    final_latency_slo_ms: finalSlo,
    first_text_after_audio_active_latency_ms: firstTextLatency,
    first_final_after_audio_active_latency_ms: firstFinalLatency,
    partial_visible_count: partialVisibleCount,
    final_visible_count: finalVisibleCount,
    recording_phase_sample_count: samples.length,
    recording_text_visible: recordingTextVisible,
    blockers,
    warnings,
  };
}

export function buildRealtimeAiSuggestionReport({
  derivationMode = "production_enabled",
  recordingPhaseUiSamples = [],
  expectedSessionId = "",
  recordingStartedAtEpochMs = null,
} = {}) {
  const samples = Array.isArray(recordingPhaseUiSamples)
    ? recordingPhaseUiSamples.filter((sample) => sample && typeof sample === "object")
    : [];
  const expectedSession = String(expectedSessionId || "").trim();
  const recordingStartedAt = finiteNumber(recordingStartedAtEpochMs);

  if (derivationMode !== "production_enabled") {
    return {
      status: "not_required_no_cost",
      derivation_mode: derivationMode,
      recording_phase_sample_count: samples.length,
      max_recording_ai_suggestions: 0,
      max_recording_cockpit_ai_suggestions: 0,
      max_visible_evidence_backed_suggestion_cards: 0,
      max_new_visible_evidence_backed_suggestion_cards: 0,
      initial_visible_suggestion_card_count: 0,
      first_ai_suggestion_visible_at_ms: null,
      first_ai_suggestion_visible_latency_ms: null,
      blockers: [],
    };
  }

  const blockers = [];
  if (!expectedSession) blockers.push("recording_session_id_missing");
  if (recordingStartedAt === null) blockers.push("recording_started_at_missing");

  const eligibleSamples = samples.filter((sample) => {
    const atMs = finiteNumber(sample.at_ms);
    return Boolean(expectedSession)
      && recordingStartedAt !== null
      && String(sample.session_id || "").trim() === expectedSession
      && String(sample?.cockpit_stage?.state || "") === "recording"
      && atMs !== null
      && atMs >= recordingStartedAt;
  });
  if (eligibleSamples.length === 0) blockers.push("recording_phase_session_samples_missing");

  const initialVisibleCount = nonNegativeInteger(eligibleSamples[0]?.visible_suggestion_card_count) ?? 0;
  const initialEvidenceBackedCount = nonNegativeInteger(
    eligibleSamples[0]?.visible_evidence_backed_suggestion_card_count,
  ) ?? 0;
  if (initialVisibleCount > 0) blockers.push("recording_started_with_existing_suggestion_cards");

  let maxRecordingAiSuggestions = 0;
  let maxRecordingCockpitAiSuggestions = 0;
  let maxEvidenceBackedSuggestionCards = 0;
  let maxNewEvidenceBackedSuggestionCards = 0;
  let firstVisibleAtMs = null;
  for (const sample of eligibleSamples) {
    const cockpitCount = nonNegativeInteger(sample?.cockpit_counts?.ai_suggestions) ?? 0;
    const visibleCount = nonNegativeInteger(sample.visible_suggestion_card_count) ?? 0;
    const evidenceBackedCount = Math.min(
      visibleCount,
      nonNegativeInteger(sample.visible_evidence_backed_suggestion_card_count) ?? 0,
    );
    const newEvidenceBackedCount = Math.max(0, evidenceBackedCount - initialEvidenceBackedCount);
    maxRecordingCockpitAiSuggestions = Math.max(maxRecordingCockpitAiSuggestions, cockpitCount);
    maxRecordingAiSuggestions = Math.max(maxRecordingAiSuggestions, visibleCount);
    maxEvidenceBackedSuggestionCards = Math.max(maxEvidenceBackedSuggestionCards, evidenceBackedCount);
    maxNewEvidenceBackedSuggestionCards = Math.max(maxNewEvidenceBackedSuggestionCards, newEvidenceBackedCount);
    if (firstVisibleAtMs === null && newEvidenceBackedCount > 0) {
      firstVisibleAtMs = finiteNumber(sample.at_ms);
    }
  }

  if (maxRecordingAiSuggestions > initialVisibleCount && maxEvidenceBackedSuggestionCards <= initialEvidenceBackedCount) {
    blockers.push("realtime_ai_suggestion_evidence_missing");
  }
  const suggestionVisible = maxNewEvidenceBackedSuggestionCards > 0 && initialVisibleCount === 0;
  if (!suggestionVisible) blockers.push("realtime_ai_suggestion_not_visible_during_recording");
  return {
    status: blockers.length === 0 && suggestionVisible
      ? "passed_realtime_ai_suggestion_visible"
      : "failed_realtime_ai_suggestion_not_visible_during_recording",
    derivation_mode: derivationMode,
    recording_phase_sample_count: samples.length,
    eligible_recording_phase_sample_count: eligibleSamples.length,
    expected_session_id: expectedSession || null,
    recording_started_at_epoch_ms: recordingStartedAt,
    max_recording_ai_suggestions: maxRecordingAiSuggestions,
    max_recording_cockpit_ai_suggestions: maxRecordingCockpitAiSuggestions,
    max_visible_evidence_backed_suggestion_cards: maxEvidenceBackedSuggestionCards,
    max_new_visible_evidence_backed_suggestion_cards: maxNewEvidenceBackedSuggestionCards,
    initial_visible_suggestion_card_count: initialVisibleCount,
    first_ai_suggestion_visible_at_ms: firstVisibleAtMs,
    first_ai_suggestion_visible_latency_ms: recordingStartedAt !== null && firstVisibleAtMs !== null
      ? firstVisibleAtMs - recordingStartedAt
      : null,
    blockers,
  };
}

export function realtimeExperienceStatusFails(status) {
  return !REALTIME_PASS_STATUSES.has(String(status || ""));
}

export function liveReminderDriftStatusFails(status) {
  return !LIVE_REMINDER_NON_FAILURE_STATUSES.has(String(status || ""));
}

export function realtimeAiSuggestionStatusFails(status) {
  return !REALTIME_AI_SUGGESTION_NON_FAILURE_STATUSES.has(String(status || ""));
}

export function mainlineCompletionStatusFails(status) {
  return !MAINLINE_PASS_STATUSES.has(String(status || ""));
}

function recordingSampleHasText(sample = {}) {
  const cockpitTranscript = nonNegativeNumber(sample?.cockpit_counts?.transcript) ?? 0;
  const utteranceCount = nonNegativeNumber(sample.utterance_count) ?? 0;
  const partialDraftCount = nonNegativeNumber(sample.partial_draft_count) ?? 0;
  return Boolean(sample.live_partial_exists)
    || cockpitTranscript > 0
    || utteranceCount > 0
    || partialDraftCount > 0;
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(String(value).trim());
  return Number.isFinite(numeric) ? numeric : null;
}

function nonNegativeNumber(value) {
  const numeric = finiteNumber(value);
  return numeric !== null && numeric >= 0 ? numeric : null;
}

function nonNegativeInteger(value) {
  const numeric = nonNegativeNumber(value);
  return numeric !== null && Number.isInteger(numeric) ? numeric : null;
}

function positiveFiniteNumber(value) {
  const numeric = finiteNumber(value);
  return numeric !== null && numeric > 0 ? numeric : null;
}

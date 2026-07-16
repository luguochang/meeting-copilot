import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMainlineCompletionReport,
  buildRealtimeAiSuggestionReport,
  buildRealtimeExperienceReport,
  liveReminderDriftStatusFails,
  realtimeAiSuggestionStatusFails,
} from "./workbench_browser_live_mic_gate.mjs";
import {
  hasCanonicalTranscript,
  hasHistorySession,
  isMeetingStopped,
  isMinutesReady,
  isOrganizeTerminal,
} from "./workbench_ui_contract.mjs";

function recordingSample(overrides = {}) {
  return {
    label: "recording_in_progress",
    utterance_count: 1,
    partial_draft_count: 1,
    live_partial_exists: true,
    cockpit_counts: { transcript: "1" },
    backend_probe_status: "ok",
    ...overrides,
  };
}

function realtimeInput(overrides = {}) {
  return {
    healthStatus: "audio_capture_health_passed",
    recordingPhaseUiSamples: [recordingSample()],
    uiReport: {
      first_text_after_audio_active_latency_ms: 8_000,
      first_final_after_audio_active_latency_ms: 45_000,
      partial_visible_count: 2,
      final_visible_count: 1,
    },
    textLatencySloMs: 15_000,
    finalLatencySloMs: 60_000,
    ...overrides,
  };
}

test("passes when healthy audio produces recording-time text and timely final", () => {
  const report = buildRealtimeExperienceReport(realtimeInput());

  assert.equal(report.status, "passed_realtime_full");
  assert.equal(report.recording_text_visible, true);
  assert.deepEqual(report.blockers, []);
});

test("warns when recording-time text is timely but final is slow", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    uiReport: {
      first_text_after_audio_active_latency_ms: 6_694,
      first_final_after_audio_active_latency_ms: 288_627,
      partial_visible_count: 252,
      final_visible_count: 3,
    },
  }));

  assert.equal(report.status, "passed_realtime_partial_final_slow");
  assert.deepEqual(report.warnings, ["final_latency_slo_exceeded"]);
});

test("fails when text appears only after recording stops", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    recordingPhaseUiSamples: [recordingSample({
      utterance_count: 0,
      partial_draft_count: 0,
      live_partial_exists: false,
      cockpit_counts: { transcript: "0" },
    })],
    uiReport: {
      first_text_after_audio_active_latency_ms: 9_000,
      first_final_after_audio_active_latency_ms: 9_000,
      partial_visible_count: 0,
      final_visible_count: 1,
    },
  }));

  assert.equal(report.status, "failed_realtime_text_not_visible_during_recording");
  assert.ok(report.blockers.includes("realtime_text_not_visible_during_recording"));
});

test("fails closed when audio capture health does not pass", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    healthStatus: "blocked_audio_too_quiet",
  }));

  assert.equal(report.status, "failed_audio_capture_health");
  assert.ok(report.blockers.includes("audio_capture_health_not_passed"));
});

test("fails closed when recording samples are missing", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    recordingPhaseUiSamples: [],
  }));

  assert.equal(report.status, "failed_realtime_recording_samples_missing");
  assert.ok(report.blockers.includes("recording_phase_ui_samples_missing"));
});

test("rejects invalid latency SLO values instead of silently passing", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    textLatencySloMs: "abc",
    finalLatencySloMs: -1,
  }));

  assert.equal(report.status, "failed_invalid_slo_configuration");
  assert.ok(report.blockers.includes("text_latency_slo_invalid"));
  assert.ok(report.blockers.includes("final_latency_slo_invalid"));
});

test("fails when visible recording text exceeds the text latency SLO", () => {
  const report = buildRealtimeExperienceReport(realtimeInput({
    uiReport: {
      first_text_after_audio_active_latency_ms: 15_001,
      first_final_after_audio_active_latency_ms: 30_000,
      partial_visible_count: 1,
      final_visible_count: 1,
    },
  }));

  assert.equal(report.status, "failed_realtime_text_slow");
});

test("live reminder infrastructure gaps fail closed while no candidates are waived", () => {
  assert.equal(liveReminderDriftStatusFails("not_evaluated_no_recording_samples"), true);
  assert.equal(liveReminderDriftStatusFails("not_evaluated_missing_recording_backend_probe"), true);
  assert.equal(liveReminderDriftStatusFails("failed_backend_candidates_not_visible"), true);
  assert.equal(liveReminderDriftStatusFails("not_evaluated_no_recording_backend_candidates"), false);
  assert.equal(liveReminderDriftStatusFails("passed"), false);
  assert.equal(liveReminderDriftStatusFails("unexpected_status"), true);
});

function mainlineInput(overrides = {}) {
  return {
    derivationMode: "no_cost_deterministic",
    asrProbe: {
      acceptance_eligible: true,
      acceptance_blockers: [],
      derivations_generated: true,
      counts_as_production_llm_evidence: false,
      suggestion_card_count: 3,
      approach_card_count: 1,
      minutes_char_count: 252,
      all_cards_have_evidence: true,
    },
    audioExportProbe: {
      audio_export_http_status: 200,
      audio_file_size_bytes: 1_000,
      audio_sha256_matches_session: true,
    },
    uiReport: {
      workbench_same_session_visible: true,
      frontend_card_count: 4,
      frontend_minutes_visible: true,
      browser_console_error_count: 0,
      network_error_count: 0,
      meeting_cockpit_stage: { state: "reviewed" },
    },
    ...overrides,
  };
}

test("passes the no-cost mainline only when derivations, UI, and audio all close", () => {
  const report = buildMainlineCompletionReport(mainlineInput());

  assert.equal(report.status, "passed_no_cost_mainline");
  assert.deepEqual(report.blockers, []);
});

test("fails the mainline when ASR semantic quality blocks derivations", () => {
  const report = buildMainlineCompletionReport(mainlineInput({
    asrProbe: {
      acceptance_eligible: false,
      acceptance_blockers: ["asr_semantic_quality_blocked", "degraded_asr_session"],
      derivations_generated: false,
      counts_as_production_llm_evidence: false,
      suggestion_card_count: 0,
      approach_card_count: 0,
      minutes_char_count: 0,
      all_cards_have_evidence: false,
    },
  }));

  assert.equal(report.status, "failed_mainline_completion");
  assert.ok(report.blockers.includes("asr_acceptance_blocked"));
  assert.ok(report.blockers.includes("suggestion_cards_missing"));
  assert.ok(report.blockers.includes("minutes_missing"));
});

test("production mainline requires non-mock LLM usage evidence", () => {
  const report = buildMainlineCompletionReport(mainlineInput({
    derivationMode: "production_enabled",
    asrProbe: {
      ...mainlineInput().asrProbe,
      counts_as_production_llm_evidence: false,
      llm_called: false,
      llm_call_count: 0,
      llm_usage_total_tokens: 0,
    },
  }));

  assert.equal(report.status, "failed_mainline_completion");
  assert.ok(report.blockers.includes("production_llm_evidence_missing"));
});

test("mainline fails when exported audio is missing or not the session asset", () => {
  const report = buildMainlineCompletionReport(mainlineInput({
    audioExportProbe: {
      audio_export_http_status: 404,
      audio_file_size_bytes: 0,
      audio_sha256_matches_session: false,
    },
  }));

  assert.equal(report.status, "failed_mainline_completion");
  assert.ok(report.blockers.includes("audio_export_missing_or_mismatched"));
});

test("no-cost mode waives formal realtime AI suggestion evidence", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "no_cost_deterministic",
    recordingPhaseUiSamples: [],
  });

  assert.equal(report.status, "not_required_no_cost");
  assert.equal(realtimeAiSuggestionStatusFails(report.status), false);
});

test("production mode fails when formal suggestions appear only after recording", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "production_enabled",
    expectedSessionId: "rec_current",
    recordingStartedAtEpochMs: 500,
    recordingPhaseUiSamples: [
      {
        at_ms: 1_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "0" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
      {
        at_ms: 31_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "0" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
    ],
  });

  assert.equal(report.status, "failed_realtime_ai_suggestion_not_visible_during_recording");
  assert.equal(report.max_recording_ai_suggestions, 0);
  assert.equal(realtimeAiSuggestionStatusFails(report.status), true);
});

test("production mode records first formal suggestion visibility latency", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "production_enabled",
    expectedSessionId: "rec_current",
    recordingStartedAtEpochMs: 500,
    recordingPhaseUiSamples: [
      {
        at_ms: 1_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "0" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
      {
        at_ms: 11_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "0" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
      {
        at_ms: 26_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "1" },
        visible_suggestion_card_count: 1,
        visible_evidence_backed_suggestion_card_count: 1,
      },
      {
        at_ms: 31_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "2" },
        visible_suggestion_card_count: 2,
        visible_evidence_backed_suggestion_card_count: 2,
      },
    ],
  });

  assert.equal(report.status, "passed_realtime_ai_suggestion_visible");
  assert.equal(report.max_recording_ai_suggestions, 2);
  assert.equal(report.first_ai_suggestion_visible_latency_ms, 25_500);
  assert.equal(realtimeAiSuggestionStatusFails(report.status), false);
});

test("production mode rejects a positive cockpit counter without a visible evidence-backed card", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "production_enabled",
    expectedSessionId: "rec_current",
    recordingStartedAtEpochMs: 500,
    recordingPhaseUiSamples: [
      {
        at_ms: 1_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "0" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
      {
        at_ms: 12_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        cockpit_counts: { ai_suggestions: "1" },
        visible_suggestion_card_count: 0,
        visible_evidence_backed_suggestion_card_count: 0,
      },
    ],
  });

  assert.equal(report.status, "failed_realtime_ai_suggestion_not_visible_during_recording");
  assert.equal(report.max_recording_cockpit_ai_suggestions, 1);
  assert.equal(report.max_recording_ai_suggestions, 0);
});

test("production mode rejects stale, wrong-session, and non-recording cards", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "production_enabled",
    expectedSessionId: "rec_current",
    recordingStartedAtEpochMs: 500,
    recordingPhaseUiSamples: [
      {
        at_ms: 1_000,
        session_id: "rec_current",
        cockpit_stage: { state: "recording" },
        visible_suggestion_card_count: 1,
        visible_evidence_backed_suggestion_card_count: 1,
      },
      {
        at_ms: 12_000,
        session_id: "rec_previous",
        cockpit_stage: { state: "recording" },
        visible_suggestion_card_count: 2,
        visible_evidence_backed_suggestion_card_count: 2,
      },
      {
        at_ms: 18_000,
        session_id: "rec_current",
        cockpit_stage: { state: "reviewed" },
        visible_suggestion_card_count: 2,
        visible_evidence_backed_suggestion_card_count: 2,
      },
    ],
  });

  assert.equal(report.status, "failed_realtime_ai_suggestion_not_visible_during_recording");
  assert.equal(report.initial_visible_suggestion_card_count, 1);
  assert.equal(report.max_new_visible_evidence_backed_suggestion_cards, 0);
  assert.ok(report.blockers.includes("recording_started_with_existing_suggestion_cards"));
});

test("production mode fails closed when recording identity or timing is unauditable", () => {
  const report = buildRealtimeAiSuggestionReport({
    derivationMode: "production_enabled",
    expectedSessionId: "",
    recordingStartedAtEpochMs: null,
    recordingPhaseUiSamples: [
      {
        at_ms: "invalid",
        session_id: "",
        cockpit_stage: { state: "recording" },
        visible_suggestion_card_count: 1,
        visible_evidence_backed_suggestion_card_count: 1,
      },
    ],
  });

  assert.equal(report.status, "failed_realtime_ai_suggestion_not_visible_during_recording");
  assert.ok(report.blockers.includes("recording_session_id_missing"));
  assert.ok(report.blockers.includes("recording_started_at_missing"));
  assert.equal(report.first_ai_suggestion_visible_latency_ms, null);
});

test("canonical transcript contract ignores the legacy generic utterance class", () => {
  assert.equal(hasCanonicalTranscript({ canonicalCount: 0, activeTailVisible: false }), false);
  assert.equal(hasCanonicalTranscript({ canonicalCount: 1, activeTailVisible: false }), true);
  assert.equal(hasCanonicalTranscript({ canonicalCount: 0, activeTailVisible: true }), true);
});

test("history contract accepts real and demo session ids without a prefix assumption", () => {
  assert.equal(hasHistorySession(["rec_mrj436k1"], "rec_mrj436k1"), true);
  assert.equal(hasHistorySession(["workbench_demo"], "workbench_demo"), true);
  assert.equal(hasHistorySession(["rec_other"], "rec_current"), false);
});

test("minutes contract uses generated state and content instead of requiring a pre element", () => {
  const minutesText = "背景：灰度发布评审\n决定：先灰度 5%\n待办事项：补充兼容性测试和回滚负责人。";
  assert.equal(isMinutesReady({ minutesCountText: "已生成", panelText: minutesText }), true);
  assert.equal(isMinutesReady({ minutesCountText: "未生成", panelText: minutesText }), false);
  assert.equal(isMinutesReady({ minutesCountText: "已生成", panelText: "暂时没有生成可用复盘。" }), false);
});

test("organize contract accepts a completed quality-blocked result", () => {
  assert.equal(isOrganizeTerminal({
    organizeButtonDisabled: false,
    statusText: "会议整理完成，但识别质量不足：正式建议暂不生成。",
  }), true);
  assert.equal(isOrganizeTerminal({
    organizeButtonDisabled: false,
    statusText: "正在整理会议：生成正式建议、方案分析和会后复盘。",
  }), false);
});

test("meeting stop contract requires the post-recording control state", () => {
  assert.equal(isMeetingStopped({ recordButtonHidden: false, stopButtonHidden: true, cockpitState: "recorded" }), true);
  assert.equal(isMeetingStopped({ recordButtonHidden: false, stopButtonHidden: false, cockpitState: "recording" }), false);
  assert.equal(isMeetingStopped({ recordButtonHidden: false, stopButtonHidden: true, cockpitState: "processing" }), false);
});

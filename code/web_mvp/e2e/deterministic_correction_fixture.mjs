const ORIGINAL_SEGMENT_ID = "det_corr_seg_1";
const ORIGINAL_EVIDENCE_ID = `asr_ev_${ORIGINAL_SEGMENT_ID}`;
const ORIGINAL_TEXT = "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒";

export function expectedDeterministicCorrection() {
  return {
    target_segment_id: ORIGINAL_SEGMENT_ID,
    revision_source_segment_id: `${ORIGINAL_SEGMENT_ID}:rtc-v1`,
    revision_event_id: `transcript_revision:${ORIGINAL_SEGMENT_ID}:rtc-v1`,
    original_evidence_id: ORIGINAL_EVIDENCE_ID,
    original_text: ORIGINAL_TEXT,
    corrected_text: "接口先灰度百分之五，如果 P99延迟超过九百毫秒",
  };
}

export function buildDeterministicCorrectionRecord(sessionId = "deterministic_correction_fixture") {
  const originalEvidence = {
    id: ORIGINAL_EVIDENCE_ID,
    segment_id: ORIGINAL_SEGMENT_ID,
    start_ms: 0,
    end_ms: 2200,
    quote: ORIGINAL_TEXT,
    status: "active",
  };
  return {
    session_id: String(sessionId),
    provider: "funasr_realtime",
    provider_mode: "real",
    is_mock: false,
    asr_fallback_used: false,
    degradation_reasons: [],
    audio_source: "simulated_realtime_wav",
    input_source: "simulated_realtime_wav",
    source: "live_asr_stream",
    trace_kind: "live_event",
    asr_semantic_quality: {
      schema_version: "asr_semantic_quality.v1",
      status: "passed",
      blocker: null,
      matched_entities: ["灰度", "P99"],
      matched_entity_groups: ["release", "latency"],
      missing_entity_groups: [],
      technical_entity_hit_count: 2,
      technical_group_hit_count: 2,
      gibberish_score: 0,
      reason: "deterministic_correction_fixture",
    },
    auto_suggestion: {
      paused: true,
      updated_at_ms: 2200,
      pause_reason: "deterministic_correction_fixture",
    },
    events: [
      {
        id: `transcript_final:${ORIGINAL_SEGMENT_ID}`,
        event_type: "transcript_final",
        at_ms: 2200,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 1,
        payload: {
          segment_id: ORIGINAL_SEGMENT_ID,
          start_ms: 0,
          end_ms: 2200,
          text: ORIGINAL_TEXT,
          normalized_text: ORIGINAL_TEXT,
          confidence: 0.88,
          is_final: true,
          evidence_spans: [originalEvidence],
        },
      },
      {
        id: "evaluation:asr_stream_summary",
        event_type: "evaluation_summary",
        at_ms: 2200,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 2,
        payload: {
          source: "live_asr_stream",
          provider: "funasr_realtime",
          provider_mode: "real",
          is_mock: false,
          passes_minimum_gate: true,
          partial_event_count: 0,
          final_event_count: 1,
          revision_event_count: 0,
          error_event_count: 0,
          end_of_stream_event_count: 1,
        },
      },
    ],
    suggestion_cards: [
      {
        card_id: "deterministic_correction_original_evidence",
        suggestion_text: "建议确认灰度比例、P99 阈值和回滚条件。",
        confidence: 0.86,
        trigger_reason: "确定性 correction 证据回跳 fixture",
        evidence_span_ids: [ORIGINAL_EVIDENCE_ID],
        evidence_spans: [originalEvidence],
        source_event_ids: [`transcript_final:${ORIGINAL_SEGMENT_ID}`],
      },
    ],
    approach_cards: [],
    minutes: {},
  };
}

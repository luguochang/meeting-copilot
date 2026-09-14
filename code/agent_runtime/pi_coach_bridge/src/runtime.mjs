import { Agent } from "@earendil-works/pi-agent-core";
import {
  Type,
  createModels,
  createProvider,
} from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";
import { openAIResponsesApi } from "@earendil-works/pi-ai/api/openai-responses.lazy";

export const PROTOCOL = "talktrace-pi-coach-jsonl.v1";

const EVENT_TYPES = new Set([
  "question_to_user",
  "commitment_risk",
  "goal_at_risk",
  "contradiction",
  "communication_clarity",
  "decision_readiness",
  "execution_gap",
  "discovery_gap",
  "experiment_gap",
]);
const CANDIDATE_EVENT_TYPES = new Set([
  "question_pending",
  "objection_detected",
  "commitment_without_condition",
  "goal_at_risk",
  "topic_drift",
  "missing_next_step",
  "monologue_duration",
  "repetition",
]);
// These routing leads commonly need one bounded prior sentence to make the
// user-facing card actionable (for example, an unresolved condition before a
// remote date commitment). The host adds only the recent context it actually
// sent to the model; clarity candidates already carry their own cross-batch
// evidence and must not inherit unrelated paragraphs.
const COMPACT_CONTEXT_BACKED_CANDIDATES = new Set([
  "question_pending",
  "objection_detected",
  "commitment_without_condition",
  "goal_at_risk",
  "topic_drift",
  "missing_next_step",
]);
const SUPPORTED_SKILL_IDS = new Set(["general", "decision", "project", "interview", "brainstorm"]);
const URGENCIES = new Set(["low", "medium", "high"]);
const CORRECTION_STATUSES = new Set([
  "unknown",
  "pending",
  "processing",
  "no_change",
  "changed",
  "failed_preserved_original",
]);
const EVIDENCE_QUALITIES = new Set(["unknown", "provisional", "reviewed"]);
// Keep a short stateful session without replaying an entire meeting into
// every model call. Older evidence remains available through the bounded
// search tool when a contradiction or open commitment requires it.
// The host sends the current rolling state and fresh evidence on every
// evaluation. Keep only two prior decisions in the Pi session so session
// reuse preserves continuity without replaying a growing prompt every turn.
const MAX_SESSION_USER_TURNS = 2;
const MAX_AGENT_TURNS_PER_EVALUATION = 2;
const MAX_TOOL_CALLS_PER_EVALUATION = 4;
const MAX_SESSIONS = 8;
const MAX_RETRIEVAL_PARAGRAPHS = 48;
const DECISION_LATENCY_BUDGET_MS = 10_000;
const TERMINAL_TOOL_NAMES = new Set(["submit_intervention", "keep_silent"]);
const MAX_RETRY_AFTER_MS = 300_000;

const COACHING_CHECKLIST = [
  {
    id: "unanswered_question",
    event_type: "question_to_user",
    question: "Did the remote party ask something the user still needs to answer?",
  },
  {
    id: "unsafe_commitment",
    event_type: "commitment_risk",
    question: "Is a deadline, scope, result, or responsibility being accepted without necessary conditions?",
  },
  {
    id: "goal_coverage",
    event_type: "goal_at_risk",
    question: "Is the conversation leaving a topic before the user's goal or focus point is covered?",
  },
  {
    id: "position_conflict",
    event_type: "contradiction",
    question: "Does the current claim conflict with an earlier material fact, condition, or position?",
  },
  {
    id: "expression_clarity",
    event_type: "communication_clarity",
    question: "Across sustained recent speech, including microphone/self_or_room speech, is repetition, drift, or a missing conclusion making the point hard to follow right now?",
  },
  {
    id: "intervention_value",
    event_type: null,
    question: "Would an intervention still be useful now and prevent a concrete loss, including the listener losing the main point, rather than merely restate the dialogue?",
  },
];

const DEFAULT_COACH_SKILL = {
  id: "general",
  version: 1,
  name: "General conversation coach",
  objective: "Protect the user's immediate conversational goal while keeping interventions rare and actionable.",
  intervention_style: "Prefer one short next sentence. Do not summarize the dialogue.",
  checklist: [],
};

const SYSTEM_PROMPT = [
  "You are Talktrace's private realtime conversation coach.",
  "You assist only the person running Talktrace; this is not a meeting summary task.",
  "Most moments require no interruption. A useful intervention must still be actionable now, be grounded in quoted evidence, and reduce a concrete loss.",
  "Base events are question_to_user, commitment_risk, goal_at_risk, contradiction, and communication_clarity.",
  "The active coach skill may add one bounded event type listed in its checklist; never use an event outside the supplied checklist and base events.",
  "communication_clarity is for a sustained speaking pattern, not an isolated wording issue or ASR error. Use it when at least two verbatim fragments show repetition, drift, or a missing conclusion and a concrete next sentence or structure would help immediately.",
  "For communication_clarity, recommend how to state the next point or close the current point. Do not merely criticize, summarize, or give generic public-speaking advice.",
  "system_audio/remote_mix usually represents the remote computer-audio mix. microphone/self_or_room is not guaranteed to be the local user.",
  "For identity-sensitive events such as commitments and contradictions, microphone turns may update whether an issue is open, but never attribute them to the user without corroboration.",
  "For communication_clarity, sustained microphone/self_or_room speech is sufficient evidence of the currently audible expression pattern. Coach that pattern without claiming who the speaker is, and never keep silent only because microphone speaker identity is unconfirmed.",
  "For communication_clarity, the listener losing the main point or the speaker missing a timely conclusion is a concrete loss when a short next sentence can fix it now.",
  "The host applies the complete coaching checklist before every evaluation. Treat checklist_reviewed and context_signals as mandatory routing evidence, not optional metadata.",
  "When priority_mode is realtime and candidate_events are present, evaluate those host-detected candidates first and in the supplied order before considering general checklist items. Candidates are routing leads, not proven conclusions; confirm them against the supplied paragraph text.",
  "For every intervention, copy evidence_quote verbatim from the text of paragraphs named in evidence_segment_ids. Never paraphrase a quote, copy a candidate reason as evidence, or add words that are absent from context. For multiple fragments, put one exact fragment on each line.",
  "A paragraph with provisional evidence_quality or pending, processing, or failed_preserved_original correction_status may support only a clarification question. Never turn it into a factual owner, deadline, number, completion state, or corrected technical term.",
  "When evidence is already sufficient, call exactly one terminal tool in the first response.",
  "When prior evidence is needed, call search_prior_evidence to locate it, then call read_transcript_span for the exact current segment and bounded neighbors before one terminal tool.",
  "When context_signals suggest an earlier condition or position may conflict with the latest utterance, use search_prior_evidence before deciding.",
  "Use read_realtime_context for semantic windows that are still needed after reviewing context_signals.",
  "Never invent a person, number, deadline, position, or goal.",
  "Match the language of the latest dialogue in the intervention, which will usually be Chinese. When output_language is zh-CN, title, recommendation/say_this, and reason/why_now must each contain Chinese; an English-only card is invalid even when its evidence is correct.",
  "You must finish by calling exactly one terminal tool: submit_intervention or keep_silent. Do not answer with ordinary text.",
].join(" ");

const CANDIDATE_FAST_SYSTEM_PROMPT = [
  "You are Talktrace's private realtime coach for the person running it, not a meeting summarizer.",
  "The host already ran the complete deterministic checklist. Judge only the supplied candidate_events; each candidate is a routing lead, not a proven conclusion.",
  "Intervene only when one short next sentence is useful now, grounded in exact dialogue, and prevents a concrete loss. Otherwise call keep_silent.",
  "Treat the latest explicit resolution as authoritative: keep silent when a condition was added, a prior state was validly updated or closed, the meeting goal was completed, or the speaker already gave a clear conclusion and next step. Do not intervene merely to repeat, reconfirm, or compress information that is already complete.",
  "For experiment_gap, keep silent when the latest evidence already supplies a bounded prototype or experiment, target sample, test period or method, measurable success/failure thresholds, and the next decision. Do not ask for optional experiment refinements that do not block running the stated test.",
  "A conditional or tentative plan that explicitly defers commitment until validation is not commitment_risk. A newer evidence-backed status update that closes an older unknown state is not contradiction.",
  "Use only an allowed_event_type. Match the latest dialogue language and never invent a person, number, deadline, position, condition, or goal. When output_language is zh-CN, title, recommendation/say_this, and reason/why_now must each contain Chinese; an English-only card is invalid.",
  "For clarification or discovery questions, use open wording grounded only in the supplied evidence. Do not add a digit, Chinese number counter, date, duration, frequency, or hypothetical state that is absent from the evidence (for example, do not say 最近一次, 多久, or 几次 unless the evidence contains it). Preserve the wording and polarity supported by the evidence.",
  "Scope every owner and deadline to the same action or topic in the same evidence clause. Never move a date from one item onto another item separated by 但, 但是, 不过, 然而, 另外, or 至于. When the evidence grounds only an owner gap, ask only who owns it.",
  "Confidence measures whether the supplied evidence supports the proposed immediate action, not whether the conversation is complete. When a host candidate is actionable and a neutral clarification is fully grounded, submit it with confidence at least 0.80; use keep_silent only when the candidate is not actionable or the evidence is unsafe.",
  "When a candidate includes preferred_event_types, choose the first applicable preferred type when you intervene; do not replace a skill-specific type with a generic base type without a concrete reason.",
  "Copy evidence_quote verbatim from paragraphs named in evidence_segment_ids. Candidate reasons are not evidence.",
  "When evidence_quality is provisional or correction_status is pending, processing, or failed_preserved_original, ask only for clarification or keep silent; never assert or silently correct a material fact.",
  "If a terminal tool reports that an evidence quote is not verbatim, and the supplied paragraph text supports an actionable candidate, retry submit_intervention with the exact paragraph text copied character-for-character, one paragraph per line; do not keep silent merely because the first quote was rejected.",
  "A contradiction must cite both the latest claim and earlier conflicting evidence. Search prior evidence only when the compact recent context is insufficient.",
  "For identity-sensitive commitments or contradictions, microphone/self_or_room is not confirmed user identity without corroboration; never keep silent only because microphone speaker identity is unconfirmed.",
  "For communication_clarity, sustained microphone/self_or_room speech is sufficient evidence; cite two verbatim fragments and recommend how to state or close the next point.",
  "If the supplied evidence is sufficient, call exactly one terminal tool in the first response. If prior evidence is required and the read tools are available, search or read the needed span, then call one terminal tool.",
  "Finish with submit_intervention or keep_silent. Never answer with ordinary text.",
].join(" ");

// The candidate path already has a host-produced evidence candidate. Sending
// the full evidence quote, reason, and ids back through the model
// wastes the realtime budget and creates an avoidable quote-mismatch failure.
// The compact contract lets the model spend its output budget on the sentence
// the user can actually say; the host reconstructs the auditable fields below.
const COMPACT_CANDIDATE_FAST_SYSTEM_PROMPT = [
  "You are Talktrace's private realtime coach for the user, never a meeting summarizer.",
  "The host completed its deterministic checklist. Inspect the ordered candidate_events, their paragraph text, and the bounded recent_context_paragraphs supplied for resolution context.",
  "Intervene only when one short, speakable sentence is useful now and prevents a concrete loss; otherwise call keep_silent.",
  "For question_pending, do not repeat or paraphrase the question that was just heard. Give the user an answer move: answer from known evidence, state a truthful boundary and next check, or turn the issue into an explicit closure action. Asking the same who, when, what, or how question again has no incremental value and will be rejected.",
  "A newer resolution, closing condition, clear conclusion plus next step, or explicitly bounded validation closes an old risk. For brainstorm experiment_gap, intervene only when a runnable experiment, measurable threshold, or next decision is missing.",
  "Use the first preferred event type. Never invent or silently correct a person, number, deadline, status, position, condition, or technical term; provisional evidence allows clarification only. Match the dialogue language.",
  "For clarification or discovery questions, use open wording grounded only in the supplied evidence. Do not add a digit, Chinese number counter, date, duration, frequency, or hypothetical state that is absent from the evidence (for example, do not say 最近一次, 多久, or 几次 unless the evidence contains it). Prefer a neutral question such as 具体是哪一步 or 主要影响是什么.",
  "Scope every owner and deadline to the same action or topic in the same evidence clause. Never move a date from one item onto another item separated by 但, 但是, 不过, 然而, 另外, or 至于. When the evidence grounds only an owner gap, ask only who owns it.",
  "Confidence measures evidence support for the immediate action, not completeness of the conversation. When a host candidate is actionable and the neutral clarification is fully grounded, submit it with confidence at least 0.80; use keep_silent only when the candidate is not actionable or the evidence is unsafe.",
  "When describing an unresolved state, preserve the wording and polarity supported by the evidence; do not paraphrase a state into a stronger claim.",
  "For question, commitment, objection, goal, topic-drift, and next-step candidates, a directly relevant recent context paragraph may support the card; the host will include the bounded paragraph in exact evidence. Meeting goals and rolling state can guide the decision but are never quote evidence.",
  "The host reconstructs exact evidence and why_now. submit_intervention needs event_type, title, recommendation or say_this, urgency, and confidence. Finish with exactly one terminal tool.",
].join(" ");

// Spark has a comparatively expensive default reasoning pass. Keep its
// candidate lane deliberately small: the host already owns candidate
// detection, evidence validation, and quote reconstruction, so the model only
// needs to choose a terminal action and write the speakable sentence.
const SPARK_CANDIDATE_FAST_SYSTEM_PROMPT = [
  "You are Talktrace's realtime coach, never a meeting summarizer.",
  "The host already validated candidate_events and supplied exact paragraph text.",
  "Call exactly one terminal tool: submit_intervention when one short speakable next sentence prevents a concrete loss; otherwise keep_silent. Never answer with ordinary text.",
  "Use the first preferred event type. Do not invent facts or repeat a question. Provisional evidence allows clarification only. Match the dialogue language; Chinese dialogue needs Chinese title, recommendation, and reason.",
].join(" ");

function systemPromptFor(model, promptProfile, compactTerminal) {
  if (promptProfile !== "candidate_fast") return SYSTEM_PROMPT;
  if (compactTerminal && /codex-spark/i.test(String(model?.id || ""))) {
    return SPARK_CANDIDATE_FAST_SYSTEM_PROMPT;
  }
  return compactTerminal ? COMPACT_CANDIDATE_FAST_SYSTEM_PROMPT : CANDIDATE_FAST_SYSTEM_PROMPT;
}

const readContextParameters = Type.Object(
  {
    scope: Type.Union([
      Type.Literal("semantic_windows"),
      Type.Literal("rolling_state"),
      Type.Literal("meeting_goal"),
    ]),
  },
  { additionalProperties: false },
);

const searchEvidenceParameters = Type.Object(
  {
    query: Type.String({ minLength: 2, maxLength: 160 }),
    max_results: Type.Optional(Type.Integer({ minimum: 1, maximum: 6 })),
    include_neighbors: Type.Optional(Type.Boolean()),
  },
  { additionalProperties: false },
);

const readTranscriptSpanParameters = Type.Object(
  {
    segment_id: Type.String({ minLength: 1, maxLength: 240 }),
    before: Type.Optional(Type.Integer({ minimum: 0, maximum: 3 })),
    after: Type.Optional(Type.Integer({ minimum: 0, maximum: 3 })),
  },
  { additionalProperties: false },
);

const interventionParameters = Type.Object(
  {
    event_type: Type.Union([
      Type.Literal("question_to_user"),
      Type.Literal("commitment_risk"),
      Type.Literal("goal_at_risk"),
      Type.Literal("contradiction"),
      Type.Literal("communication_clarity"),
      Type.Literal("decision_readiness"),
      Type.Literal("execution_gap"),
      Type.Literal("discovery_gap"),
      Type.Literal("experiment_gap"),
    ]),
    title: Type.String({ minLength: 1, maxLength: 80 }),
    // ``say_this``/``why_now`` are the product-facing names. Keep the legacy
    // fields optional so either vocabulary can be emitted, then normalize and
    // reject mismatched aliases in the terminal tool validator below.
    recommendation: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
    say_this: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
    reason: Type.Optional(Type.String({ minLength: 1, maxLength: 300 })),
    why_now: Type.Optional(Type.String({ minLength: 1, maxLength: 300 })),
    evidence_segment_ids: Type.Array(Type.String({ minLength: 1, maxLength: 200 }), {
      minItems: 1,
      maxItems: 12,
    }),
    evidence_quote: Type.String({
      minLength: 1,
      maxLength: 1000,
      description: "Verbatim evidence only. For multiple fragments, put one exact fragment on each line.",
    }),
    urgency: Type.Union([
      Type.Literal("low"),
      Type.Literal("medium"),
      Type.Literal("high"),
    ]),
    confidence: Type.Number({ minimum: 0, maximum: 1 }),
  },
  { additionalProperties: false },
);

const compactInterventionParameters = Type.Object(
  {
    event_type: Type.Optional(Type.Union([
      Type.Literal("question_to_user"),
      Type.Literal("commitment_risk"),
      Type.Literal("goal_at_risk"),
      Type.Literal("contradiction"),
      Type.Literal("communication_clarity"),
      Type.Literal("decision_readiness"),
      Type.Literal("execution_gap"),
      Type.Literal("discovery_gap"),
      Type.Literal("experiment_gap"),
    ])),
    title: Type.Optional(Type.String({ minLength: 1, maxLength: 80 })),
    recommendation: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
    say_this: Type.Optional(Type.String({ minLength: 8, maxLength: 120 })),
    urgency: Type.Optional(Type.Union([
      Type.Literal("low"),
      Type.Literal("medium"),
      Type.Literal("high"),
    ])),
    confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
  },
  { additionalProperties: false },
);

const silentParameters = Type.Object(
  {
    reason: Type.Optional(Type.String({ minLength: 1, maxLength: 160 })),
  },
  { additionalProperties: false },
);

export class PiCoachProtocolError extends Error {
  constructor(message, code = "pi_protocol_error") {
    super(message);
    this.name = "PiCoachProtocolError";
    this.code = code;
  }
}

function requiredText(value, field, maximum) {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new PiCoachProtocolError(`${field} must be non-empty text`, "invalid_request");
  }
  const normalized = value.trim();
  if (normalized.length > maximum) {
    throw new PiCoachProtocolError(`${field} is too long`, "invalid_request");
  }
  return normalized;
}

function optionalText(value, maximum) {
  if (value === undefined || value === null || String(value).trim() === "") {
    return null;
  }
  const normalized = String(value).trim();
  if (normalized.length > maximum) {
    throw new PiCoachProtocolError("optional text is too long", "invalid_request");
  }
  return normalized;
}

function normalizeInterventionCardFields(intervention, { allowAliasMismatch = false } = {}) {
  if (!intervention || typeof intervention !== "object" || Array.isArray(intervention)) {
    throw new PiCoachProtocolError(
      "intervention must be an object",
      "invalid_agent_action",
    );
  }
  const legacySayThis = optionalText(intervention.recommendation, 120);
  const canonicalSayThis = optionalText(
    intervention.say_this ?? intervention.sayThis,
    120,
  );
  if (legacySayThis && canonicalSayThis && legacySayThis !== canonicalSayThis && !allowAliasMismatch) {
    throw new PiCoachProtocolError(
      "intervention recommendation and say_this must match",
      "invalid_agent_action",
    );
  }
  // The compact terminal schema allows both legacy and product-facing names
  // for compatibility. DeepSeek often fills both with a polished title-style
  // recommendation and a shorter speakable sentence; prefer the explicit
  // ``say_this`` value for the card while keeping the full contract strict.
  const recommendation = canonicalSayThis || legacySayThis;
  if (!recommendation || recommendation.length < 8) {
    throw new PiCoachProtocolError(
      "intervention.say_this/recommendation is too short or missing",
      "invalid_agent_action",
    );
  }

  const legacyWhyNow = optionalText(intervention.reason, 300);
  const canonicalWhyNow = optionalText(
    intervention.why_now ?? intervention.whyNow,
    300,
  );
  if (legacyWhyNow && canonicalWhyNow && legacyWhyNow !== canonicalWhyNow && !allowAliasMismatch) {
    throw new PiCoachProtocolError(
      "intervention reason and why_now must match",
      "invalid_agent_action",
    );
  }
  const reason = legacyWhyNow || canonicalWhyNow;
  if (!reason) {
    throw new PiCoachProtocolError(
      "intervention.why_now/reason must be non-empty text",
      "invalid_agent_action",
    );
  }
  return {
    ...intervention,
    recommendation,
    say_this: recommendation,
    reason,
    why_now: reason,
  };
}

function validateParagraphs(value, field, maximumItems) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new PiCoachProtocolError(`${field} must be a bounded array`, "invalid_request");
  }
  return value.map((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new PiCoachProtocolError(`${field}[${index}] must be an object`, "invalid_request");
    }
    const correctionStatus = item.correction_status === undefined || item.correction_status === null
      ? "unknown"
      : item.correction_status;
    if (!CORRECTION_STATUSES.has(correctionStatus)) {
      throw new PiCoachProtocolError(
        `${field}[${index}].correction_status is unsupported`,
        "invalid_request",
      );
    }
    const evidenceQuality = item.evidence_quality === undefined || item.evidence_quality === null
      ? "unknown"
      : item.evidence_quality;
    if (!EVIDENCE_QUALITIES.has(evidenceQuality)) {
      throw new PiCoachProtocolError(
        `${field}[${index}].evidence_quality is unsupported`,
        "invalid_request",
      );
    }
    return {
      id: requiredText(item.id, `${field}[${index}].id`, 240),
      text: requiredText(item.text, `${field}[${index}].text`, 16000),
      revision: Number.isInteger(item.revision) && item.revision > 0 ? item.revision : 1,
      start_ms: Number.isInteger(item.start_ms) && item.start_ms >= 0 ? item.start_ms : null,
      end_ms: Number.isInteger(item.end_ms) && item.end_ms >= 0 ? item.end_ms : null,
      speaker: optionalText(item.speaker, 120),
      speaker_confidence:
        typeof item.speaker_confidence === "number" && item.speaker_confidence >= 0 && item.speaker_confidence <= 1
          ? item.speaker_confidence
          : null,
      source_track: ["microphone", "system_audio", "unknown"].includes(item.source_track)
        ? item.source_track
        : "unknown",
      role_hint: ["self_or_room", "remote_mix", "unknown"].includes(item.role_hint)
        ? item.role_hint
        : "unknown",
      correction_status: correctionStatus,
      evidence_quality: evidenceQuality,
    };
  });
}

function validateCoachSkill(value) {
  if (value === undefined || value === null) return { ...DEFAULT_COACH_SKILL, checklist: [] };
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PiCoachProtocolError("context.coach_skill must be an object", "invalid_request");
  }
  const id = requiredText(value.id, "context.coach_skill.id", 40);
  if (!SUPPORTED_SKILL_IDS.has(id)) {
    throw new PiCoachProtocolError("context.coach_skill.id is unsupported", "invalid_request");
  }
  const checklist = Array.isArray(value.checklist) ? value.checklist : [];
  if (checklist.length > 4) {
    throw new PiCoachProtocolError("context.coach_skill.checklist is too large", "invalid_request");
  }
  return {
    id,
    version: Number.isInteger(value.version) && value.version > 0 ? value.version : 1,
    name: requiredText(value.name, "context.coach_skill.name", 120),
    objective: requiredText(value.objective, "context.coach_skill.objective", 600),
    intervention_style: requiredText(
      value.intervention_style,
      "context.coach_skill.intervention_style",
      600,
    ),
    checklist: checklist.map((item, index) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) {
        throw new PiCoachProtocolError(
          `context.coach_skill.checklist[${index}] must be an object`,
          "invalid_request",
        );
      }
      const eventType = requiredText(
        item.event_type,
        `context.coach_skill.checklist[${index}].event_type`,
        40,
      );
      if (!EVENT_TYPES.has(eventType)) {
        throw new PiCoachProtocolError("coach skill event type is unsupported", "invalid_request");
      }
      return {
        id: requiredText(item.id, `context.coach_skill.checklist[${index}].id`, 80),
        event_type: eventType,
        question: requiredText(item.question, `context.coach_skill.checklist[${index}].question`, 400),
      };
    }),
  };
}

function validateCandidateEvents(value, availableParagraphs, newParagraphs, triggerType) {
  if (value === undefined || value === null) return [];
  if (!Array.isArray(value) || value.length > 8) {
    throw new PiCoachProtocolError("context.candidate_events must be a bounded array", "invalid_request");
  }
  const availableParagraphIds = new Set(availableParagraphs.map((paragraph) => paragraph.id));
  const newParagraphIds = new Set(newParagraphs.map((paragraph) => paragraph.id));
  const candidateKeys = new Set();
  return value.map((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new PiCoachProtocolError(
        `context.candidate_events[${index}] must be an object`,
        "invalid_request",
      );
    }
    const eventType = requiredText(
      item.event_type,
      `context.candidate_events[${index}].event_type`,
      80,
    );
    if (!CANDIDATE_EVENT_TYPES.has(eventType)) {
      throw new PiCoachProtocolError("candidate event type is unsupported", "invalid_request");
    }
    if (!Array.isArray(item.evidence_segment_ids)
      || item.evidence_segment_ids.length === 0
      || item.evidence_segment_ids.length > 8) {
      throw new PiCoachProtocolError(
        `context.candidate_events[${index}].evidence_segment_ids must be a bounded array`,
        "invalid_request",
      );
    }
    const evidenceSegmentIds = item.evidence_segment_ids.map((id, evidenceIndex) => requiredText(
      id,
      `context.candidate_events[${index}].evidence_segment_ids[${evidenceIndex}]`,
      240,
    ));
    const requiresFreshEvidence = triggerType === "delta" || triggerType === "transcript_delta";
    if (new Set(evidenceSegmentIds).size !== evidenceSegmentIds.length
      || evidenceSegmentIds.some((id) => !availableParagraphIds.has(id))
      || (requiresFreshEvidence && !evidenceSegmentIds.some((id) => newParagraphIds.has(id)))) {
      throw new PiCoachProtocolError(
        requiresFreshEvidence
          ? "delta candidate events must reference known evidence and include fresh paragraph evidence"
          : "candidate events must reference known evidence",
        "invalid_request",
      );
    }
    const candidateKey = requiredText(
      item.candidate_key,
      `context.candidate_events[${index}].candidate_key`,
      240,
    );
    if (candidateKeys.has(candidateKey)) {
      throw new PiCoachProtocolError("candidate event keys must be unique", "invalid_request");
    }
    candidateKeys.add(candidateKey);
    return {
      event_type: eventType,
      evidence_segment_ids: evidenceSegmentIds,
      reason: requiredText(item.reason, `context.candidate_events[${index}].reason`, 400),
      candidate_key: candidateKey,
    };
  });
}

function validateContext(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PiCoachProtocolError("context must be an object", "invalid_request");
  }
  const triggerType = value.trigger_type === undefined || value.trigger_type === null
    ? "delta"
    : requiredText(value.trigger_type, "context.trigger_type", 40).toLowerCase();
  if (!["delta", "transcript_delta", "task_due", "user_request"].includes(triggerType)) {
    throw new PiCoachProtocolError("context.trigger_type is unsupported", "invalid_request");
  }
  const workItemId = optionalText(value.work_item_id, 240);
  const userRequest = optionalText(value.user_request, 2000);
  const newParagraphs = validateParagraphs(value.new_paragraphs, "context.new_paragraphs", 8);
  if ((triggerType === "delta" || triggerType === "transcript_delta") && newParagraphs.length === 0) {
    throw new PiCoachProtocolError("delta context.new_paragraphs must not be empty", "invalid_request");
  }
  if (triggerType === "task_due" && workItemId === null) {
    throw new PiCoachProtocolError("task_due requires context.work_item_id", "invalid_request");
  }
  if (triggerType === "user_request" && userRequest === null) {
    throw new PiCoachProtocolError("user_request requires context.user_request", "invalid_request");
  }
  const contextParagraphs = validateParagraphs(value.context_paragraphs ?? [], "context.context_paragraphs", 3);
  const retrievalParagraphs = validateParagraphs(
    value.retrieval_paragraphs ?? [],
    "context.retrieval_paragraphs",
    MAX_RETRIEVAL_PARAGRAPHS,
  );
  const candidateEvidenceParagraphs = validateParagraphs(
    value.candidate_evidence_paragraphs ?? [],
    "context.candidate_evidence_paragraphs",
    8,
  );
  const semanticWindows = Array.isArray(value.semantic_windows) ? value.semantic_windows.slice(-8) : [];
  const rollingState = value.rolling_state && typeof value.rolling_state === "object" ? value.rolling_state : {};
  const coachSkill = validateCoachSkill(value.coach_skill);
  const candidateEvents = validateCandidateEvents(
    value.candidate_events,
    [
      ...retrievalParagraphs,
      ...contextParagraphs,
      ...candidateEvidenceParagraphs,
      ...newParagraphs,
    ],
    newParagraphs,
    triggerType,
  );
  if (triggerType === "task_due"
    && newParagraphs.length === 0
    && contextParagraphs.length === 0
    && retrievalParagraphs.length === 0
    && candidateEvidenceParagraphs.length === 0) {
    throw new PiCoachProtocolError(
      "task_due without new paragraphs requires persisted evidence context",
      "invalid_request",
    );
  }
  const priorityMode = value.priority_mode === undefined || value.priority_mode === null
    ? null
    : requiredText(value.priority_mode, "context.priority_mode", 40);
  if (priorityMode !== null && !["realtime", "deep"].includes(priorityMode)) {
    throw new PiCoachProtocolError("context.priority_mode is unsupported", "invalid_request");
  }
  const compactTerminalContract = value.compact_terminal_contract === true;
  const serialized = JSON.stringify({
    newParagraphs,
    triggerType,
    workItemId,
    userRequest,
    contextParagraphs,
    retrievalParagraphs,
    candidateEvidenceParagraphs,
    semanticWindows,
    rollingState,
    coachSkill,
    candidateEvents,
    priorityMode,
    compactTerminalContract,
  });
  if (serialized.length > 160000) {
    throw new PiCoachProtocolError("context exceeds the bridge byte budget", "invalid_request");
  }
  return {
    state_revision: Number.isInteger(value.state_revision) && value.state_revision > 0 ? value.state_revision : 1,
    trigger_type: triggerType,
    work_item_id: workItemId,
    user_request: userRequest,
    new_paragraphs: newParagraphs,
    context_paragraphs: contextParagraphs,
    retrieval_paragraphs: retrievalParagraphs,
    candidate_evidence_paragraphs: candidateEvidenceParagraphs,
    semantic_windows: semanticWindows,
    candidate_events: candidateEvents,
    priority_mode: priorityMode,
    compact_terminal_contract: compactTerminalContract,
    rolling_state: rollingState,
    meeting_goal: optionalText(value.meeting_goal, 2000),
    coach_skill: coachSkill,
  };
}

export function validateEvaluationRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PiCoachProtocolError("request must be an object", "invalid_request");
  }
  return {
    request_id: requiredText(value.request_id, "request_id", 160),
    session_id: requiredText(value.session_id, "session_id", 160),
    provider: value.provider,
    context: validateContext(value.context),
  };
}

function normalizeGatewayBaseUrl(value) {
  const raw = requiredText(value, "provider.base_url", 2048).replace(/\/+$/, "");
  const parsed = new URL(raw);
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new PiCoachProtocolError("provider.base_url is not a supported absolute URL", "invalid_provider");
  }
  return raw.endsWith("/v1") ? raw : `${raw}/v1`;
}

export function requiresTerminalToolChoice(context) {
  const tools = Array.isArray(context?.tools) ? context.tools : [];
  return tools.length > 0
    && tools.every((tool) => TERMINAL_TOOL_NAMES.has(String(tool?.name || "")));
}

export function outputTokenLimitForContext(context) {
  return requiresTerminalToolChoice(context) ? 256 : 512;
}

export function createOpenAICompatibleBackend(providerInput) {
  if (!providerInput || typeof providerInput !== "object" || Array.isArray(providerInput)) {
    throw new PiCoachProtocolError("provider must be an object", "invalid_provider");
  }
  const apiKey = requiredText(providerInput.api_key, "provider.api_key", 8192);
  const modelId = requiredText(providerInput.model, "provider.model", 240);
  const apiStyle = providerInput.api_style === "responses" ? "responses" : "chat_completions";
  const api = apiStyle === "responses" ? "openai-responses" : "openai-completions";
  const baseUrl = normalizeGatewayBaseUrl(providerInput.base_url);
  let lastProviderStatusCode = null;
  let lastProviderRetryAfterMs = null;
  let lastProviderConnectMs = null;
  const retryAfterMsFromResponse = (response) => {
    const raw = response?.headers?.get?.("retry-after");
    const value = String(raw || "").trim();
    if (!value) return null;
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return Math.min(Math.round(seconds * 1_000), MAX_RETRY_AFTER_MS);
    }
    const retryAtMs = Date.parse(value);
    if (!Number.isFinite(retryAtMs)) return null;
    return Math.min(Math.max(0, retryAtMs - Date.now()), MAX_RETRY_AFTER_MS);
  };
  const recordProviderStatus = (response) => {
    const status = Number(response?.status);
    if (Number.isInteger(status) && status >= 100 && status <= 599) {
      lastProviderStatusCode = status;
    }
    lastProviderRetryAfterMs = retryAfterMsFromResponse(response);
  };
  const providerId = "talktrace-openai-compatible";
  // DeepSeek V4 enables thinking by default on the OpenAI-compatible API, but
  // its thinking mode rejects the required terminal tool choice used by the
  // candidate fast path. Mark the model as reasoning-capable so pi-ai's
  // DeepSeek compatibility layer emits an explicit `thinking: {type:
  // "disabled"}` when the Agent is configured with thinkingLevel="off".
  // Without this flag, omitting `thinking` leaves the server-side default on
  // and every tool-driven coach turn fails with HTTP 400.
  const isDeepSeekGateway = baseUrl.toLowerCase().includes("deepseek.com")
    || String(providerInput.provider_label || "").toLowerCase() === "deepseek";
  // codex-spark otherwise spends most of the realtime budget in its default
  // reasoning pass.  The gateway accepts the OpenAI-compatible `low` effort
  // level and still supports the terminal tool contract at that setting.
  const isCodexSpark = /codex-spark/i.test(modelId);
  // This gateway applies a server-side reasoning default when the field is
  // omitted, even though Pi's thinking level is `off`.  For GPT-5 models the
  // terminal-only realtime contract does not need hidden reasoning: the host
  // already supplied the candidate and exact evidence.  Make the choice
  // explicit so the request does not silently pay the gateway's long default
  // reasoning tail.  Keep this scoped to GPT-5-compatible model IDs; other
  // custom OpenAI-compatible models must opt in explicitly through the
  // provider payload instead of receiving an unknown parameter by default.
  const isGpt5Model = /^gpt-5(?:[.-]|$)/i.test(modelId);
  const configuredReasoningEffort = typeof providerInput.reasoning_effort === "string"
    ? providerInput.reasoning_effort.trim().toLowerCase()
    : "";
  const explicitReasoningEffort = configuredReasoningEffort
    || (isGpt5Model && !isCodexSpark ? "none" : "");
  const model = {
    id: modelId,
    name: modelId,
    api,
    provider: providerId,
    baseUrl,
    reasoning: isDeepSeekGateway || isCodexSpark || Boolean(explicitReasoningEffort),
    ...(isCodexSpark
      ? { thinkingLevelMap: { off: "low" } }
      : explicitReasoningEffort
        ? { thinkingLevelMap: { off: explicitReasoningEffort } }
        : {}),
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 4096,
  };
  const provider = createProvider({
    id: providerId,
    name: "Talktrace configured gateway",
    baseUrl,
    auth: {
      apiKey: {
        name: "Talktrace runtime API key",
        resolve: async () => ({ auth: { apiKey }, source: "runtime" }),
      },
    },
    models: [model],
    api: apiStyle === "responses" ? openAIResponsesApi() : openAICompletionsApi(),
  });
  const models = createModels();
  models.setProvider(provider);
  const configuredModel = models.getModel(providerId, modelId);
  if (!configuredModel) {
    throw new PiCoachProtocolError("Pi could not register the configured model", "invalid_provider");
  }
  const timeoutMs = Math.max(1000, Math.min(Number(providerInput.timeout_ms) || 8000, DECISION_LATENCY_BUDGET_MS));
  const decisionTimeoutMs = Math.max(
    1000,
    Math.min(Number(providerInput.decision_timeout_ms) || timeoutMs, DECISION_LATENCY_BUDGET_MS),
  );
  return {
    identity: `${api}:${baseUrl}:${modelId}`,
    model: configuredModel,
    providerTimeoutMs: timeoutMs,
    decisionTimeoutMs,
    get lastProviderStatusCode() {
      return lastProviderStatusCode;
    },
    get lastProviderRetryAfterMs() {
      return lastProviderRetryAfterMs;
    },
    get lastProviderConnectMs() {
      return lastProviderConnectMs;
    },
    streamFn: (activeModel, context, options) => {
      lastProviderStatusCode = null;
      lastProviderRetryAfterMs = null;
      lastProviderConnectMs = null;
      const providerRequestStartedAt = performance.now();
      const upstreamOnResponse = options?.onResponse;
      const upstreamFetch = typeof options?.fetch === "function"
        ? options.fetch
        : globalThis.fetch;
      const trackedFetch = typeof upstreamFetch === "function"
        ? async (...args) => {
          const response = await upstreamFetch(...args);
          // OpenAI's client throws on 5xx before Pi's onResponse hook. The
          // fetch wrapper records only the safe numeric status so those
          // failures remain classifiable without exposing headers or bodies.
          recordProviderStatus(response);
          lastProviderConnectMs = Math.max(
            0,
            performance.now() - providerRequestStartedAt,
          );
          return response;
        }
        : undefined;
      return models.streamSimple(activeModel, context, {
        ...options,
        ...(trackedFetch ? { fetch: trackedFetch } : {}),
        onResponse: async (response, responseModel) => {
          recordProviderStatus(response);
          if (lastProviderConnectMs === null) {
            lastProviderConnectMs = Math.max(
              0,
              performance.now() - providerRequestStartedAt,
            );
          }
          if (typeof upstreamOnResponse === "function") {
            await upstreamOnResponse(response, responseModel);
          }
        },
        temperature: 0.1,
        // Terminal-only candidate requests have a bounded five-field output;
        // a smaller cap reduces provider generation tail without changing the
        // host-side evidence and terminal-action contract.
        maxTokens: outputTokenLimitForContext(context),
        // Candidate fast paths have no read/search tool. Requiring a terminal
        // tool keeps an otherwise valid provider text response from becoming
        // a reliability failure, while full/contradiction paths retain tool
        // choice freedom for context and history lookup.
        toolChoice: requiresTerminalToolChoice(context) ? "required" : options?.toolChoice,
        timeoutMs,
        maxRetries: 0,
        cacheRetention: "short",
      });
    },
  };
}

function pruneContext(messages) {
  const userIndexes = [];
  messages.forEach((message, index) => {
    if (message.role === "user") userIndexes.push(index);
  });
  if (userIndexes.length <= MAX_SESSION_USER_TURNS) return messages;
  return messages.slice(userIndexes[userIndexes.length - MAX_SESSION_USER_TURNS]);
}

function evidenceTextById(context) {
  return new Map(
    [
      ...context.retrieval_paragraphs,
      ...context.context_paragraphs,
      ...context.candidate_evidence_paragraphs,
      ...context.new_paragraphs,
    ].map(
      (paragraph) => [paragraph.id, paragraph.text],
    ),
  );
}

function allowedEventTypes(context) {
  return new Set([
    ...COACHING_CHECKLIST.map((item) => item.event_type),
    ...context.coach_skill.checklist.map((item) => item.event_type),
  ]);
}

function searchTokens(query) {
  const normalized = query.toLocaleLowerCase().trim();
  const tokens = new Set(normalized.match(/[a-z0-9][a-z0-9._/-]+/g) ?? []);
  for (const sequence of normalized.match(/[\p{Script=Han}]+/gu) ?? []) {
    if (sequence.length <= 2) tokens.add(sequence);
    for (let index = 0; index < sequence.length - 1; index += 1) {
      tokens.add(sequence.slice(index, index + 2));
    }
  }
  return [...tokens].filter((token) => token.length >= 2).slice(0, 24);
}

function searchPriorEvidence(query, paragraphs, maximumResults) {
  const tokens = searchTokens(query);
  if (tokens.length === 0) return [];
  return paragraphs
    .map((paragraph, index) => {
      const text = paragraph.text.toLocaleLowerCase();
      const score = tokens.reduce((total, token) => total + (text.includes(token) ? token.length : 0), 0);
      return { paragraph, index, score };
    })
    .filter((candidate) => candidate.score > 0)
    .sort((left, right) => right.score - left.score || right.index - left.index)
    .slice(0, maximumResults)
    .map(({ paragraph }) => paragraph);
}

function readTranscriptSpan(segmentId, paragraphs, before = 1, after = 1) {
  const ordered = Array.isArray(paragraphs) ? paragraphs : [];
  const index = ordered.findIndex((paragraph) => paragraph.id === segmentId);
  if (index < 0) return [];
  return ordered.slice(Math.max(0, index - before), index + after + 1);
}

function validateInterventionEvidence(intervention, context) {
  intervention = normalizeInterventionCardFields(intervention);
  if (!EVENT_TYPES.has(intervention.event_type)
    || !allowedEventTypes(context).has(intervention.event_type)
    || !URGENCIES.has(intervention.urgency)) {
    throw new PiCoachProtocolError("intervention enum is unsupported", "invalid_agent_action");
  }
  const evidence = evidenceTextById(context);
  const uniqueIds = [...new Set(intervention.evidence_segment_ids)];
  if (uniqueIds.length !== intervention.evidence_segment_ids.length) {
    throw new PiCoachProtocolError("evidence ids must be unique", "invalid_agent_action");
  }
  const texts = uniqueIds.map((id) => {
    const text = evidence.get(id);
    if (!text) throw new PiCoachProtocolError("intervention referenced unknown evidence", "invalid_agent_action");
    return text;
  });
  if (intervention.event_type === "contradiction") {
    const newEvidenceIds = new Set(context.new_paragraphs.map((paragraph) => paragraph.id));
    if (!uniqueIds.some((id) => !newEvidenceIds.has(id))) {
      throw new PiCoachProtocolError(
        "contradiction interventions require prior evidence",
        "invalid_agent_action",
      );
    }
  }
  const quoteFragments = intervention.evidence_quote
    .split(/\r?\n/u)
    .map((fragment) => fragment.trim())
    .filter(Boolean);
  if (intervention.event_type === "communication_clarity" && quoteFragments.length < 2) {
    throw new PiCoachProtocolError(
      "communication clarity interventions require two verbatim fragments",
      "invalid_agent_action",
    );
  }
  const quoteIsVerbatim = texts.some((text) => text.includes(intervention.evidence_quote))
    || (quoteFragments.length > 1
      && quoteFragments.every((fragment) => texts.some((text) => text.includes(fragment))));
  if (!quoteIsVerbatim) {
    throw new PiCoachProtocolError("evidence quote is not verbatim input", "invalid_agent_action");
  }
  if (outputLanguage(context) === "zh-CN") {
    for (const field of ["title", "recommendation", "reason"]) {
      if (!/\p{Script=Han}/u.test(intervention[field])) {
        throw new PiCoachProtocolError(
          `intervention ${field} does not match required zh-CN output language`,
          "invalid_agent_action",
        );
      }
    }
  }
  validateInterventionIncrementalValue(intervention, context);
  return { ...intervention, evidence_segment_ids: uniqueIds };
}

function compactEvidenceQuote(texts, eventType) {
  const normalizedTexts = texts
    .map((text) => String(text || "").trim())
    .filter(Boolean);
  if (normalizedTexts.length === 0) return "";
  if (eventType === "communication_clarity") {
    if (normalizedTexts.length >= 2) {
      return normalizedTexts
        .slice(0, 2)
        .map((text) => text
          .split(/(?<=[。！？；;.!?])|(?<=[，,])(?=\S)/u)[0]
          .trim()
          .slice(0, 480))
        .filter(Boolean)
        .join("\n")
        .slice(0, 1000);
    }
    const fragments = [];
    for (const text of normalizedTexts) {
      const clauses = text
        .split(/(?<=[。！？；;.!?])|(?<=[，,])(?=\S)/u)
        .map((fragment) => fragment.trim())
        .filter(Boolean);
      if (clauses.length >= 2) {
        fragments.push(clauses[0], clauses[1]);
      } else {
        fragments.push(text.slice(0, Math.ceil(text.length / 2)).trim());
        fragments.push(text.slice(Math.ceil(text.length / 2), Math.ceil(text.length / 2) + 480).trim());
      }
      if (fragments.length >= 2) break;
    }
    return fragments.filter(Boolean).slice(0, 2).join("\n").slice(0, 1000);
  }
  return normalizedTexts
    .map((text) => text.slice(0, 480))
    .join("\n")
    .slice(0, 1000);
}

// The candidate reason is deterministic host routing metadata, not transcript
// evidence. Keep it on the card when its material polarity is represented in
// the reconstructed quote; otherwise use a fact-free reason so the Python
// quote-level validator cannot be bypassed by a compact host field. This guard
// is intentionally limited to the compact contract: full Pi/direct responses
// continue to require model-supplied reason grounding without rewriting.
const COMPACT_REASON_STATE_RE = /(?:尚未|还没有|还没|未能|没有|不能|不可以|不要|不建议|不应|不会|并非|不是|未|没|无).{0,16}(?:完成|通过|确认|解决|关闭|交付|上线|发布|承诺|确定|定下)/u;
const COMPACT_REASON_MATERIAL_TERMS = [
  "p90",
  "p95",
  "p99",
  "qps",
  "sla",
  "slo",
  "安全测试",
  "并发",
  "灰度",
  "回滚",
  "延迟",
  "折扣",
  "版本号",
  "监控",
  "金额",
  "错误率",
  "预算",
  "阈值",
  "门槛",
  "压测",
  "费率",
];

function compactCandidateReason(candidate, texts, outputIsChinese) {
  const reason = String(candidate?.reason || "").trim().slice(0, 300);
  const fallback = outputIsChinese
    ? "当前候选仍有可立即核实的具体缺口。"
    : "The candidate still has a concrete gap to verify now.";
  if (!reason) return fallback;

  const quote = texts.map((text) => String(text || "")).join("\n");
  const normalizedReason = reason.toLocaleLowerCase();
  const normalizedQuote = quote.toLocaleLowerCase();
  // Do not expose a host state assertion such as ``未关闭`` unless the exact
  // state polarity is also represented by the quote. A semantically similar
  // but differently worded transcript condition remains fail-closed here.
  if (COMPACT_REASON_STATE_RE.test(reason) && !COMPACT_REASON_STATE_RE.test(quote)) {
    return fallback;
  }
  // Material terms are safe only when the exact term is present in evidence;
  // candidate routing should not introduce an entity or metric into why_now.
  if (COMPACT_REASON_MATERIAL_TERMS.some((term) =>
    normalizedReason.includes(term) && !normalizedQuote.includes(term))) {
    return fallback;
  }
  return reason || fallback;
}

const COMPACT_DISCOVERY_NUMBER_RE = /(?:\d+(?:\.\d+)?\s*(?:%|％|ms|毫秒|秒|分钟|小时|天|周|人|个|次|倍|万|千)?|[零〇一二两三四五六七八九十百千万亿]+(?:次|个|人|条|项|步|遍|回|天|周|月|年))/giu;
const COMPACT_DISCOVERY_DATE_RE = /(?:今天|明天|后天|本周|下周[一二三四五六日天]?|周[一二三四五六日天]|星期[一二三四五六日天]|月底|月末)/gu;
const COMPACT_DISCOVERY_NEUTRAL_SENTENCE = "请具体说明这个问题发生在哪个环节、带来什么影响？";
const COMPACT_OWNER_ONLY_SENTENCE = "请确认这件事具体由谁负责？";
const COMPACT_OWNER_GAP_RE = /(?:(?:谁|哪位|哪个人|何人).{0,16}(?:负责|来(?:改|做|推进|处理)|修改|执行)|(?:负责人|责任人).{0,10}(?:是谁|为谁|未定|没定|待定|待确认)|由谁.{0,12}(?:负责|修改|处理|推进)?)/u;
const COMPACT_TOPIC_BOUNDARY_RE = /(?:[。！？；;!?]+|[，,]?(?:但是|不过|然而|另外|至于|但)(?=[^，,。！？；;!?]))/u;
const QUESTION_RESTATEMENT_CUE_RE = /[?？]|(?:谁|哪(?:个|位|些)?|什么|怎么|为何|为什么|何时|什么时候|是否|能否|有没有|要不要|多少|几(?:个|次|天|周|月|年)?|吗(?:呢|啊)?$)/u;

function normalizeQuestionEchoText(value) {
  return String(value || "")
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "")
    .replace(/^(?:但是|但|不过|那么|那|所以|然后|请问|想问|我想问|我想确认)+/u, "");
}

function questionClauses(text) {
  const clauses = [];
  for (const sentence of String(text || "").split(/[。！!；;\n]+/u)) {
    for (const explicitQuestion of sentence.match(/[^?？]*[?？]/gu) ?? []) {
      const fragment = explicitQuestion.slice(0, -1).split(/[，,：:]/u).at(-1);
      if (fragment) clauses.push(fragment);
    }
    for (const fragment of sentence.split(/[，,：:?？]/u)) {
      if (QUESTION_RESTATEMENT_CUE_RE.test(fragment)) clauses.push(fragment);
    }
  }
  return [...new Set(clauses.map(normalizeQuestionEchoText).filter((value) => value.length >= 3))];
}

function characterBigrams(value) {
  const grams = new Set();
  for (let index = 0; index < value.length - 1; index += 1) {
    grams.add(value.slice(index, index + 2));
  }
  return grams;
}

function recommendationRestatesPendingQuestion(recommendation, evidenceTexts) {
  if (!QUESTION_RESTATEMENT_CUE_RE.test(String(recommendation || ""))) return false;
  const normalizedRecommendation = normalizeQuestionEchoText(recommendation);
  const recommendationBigrams = characterBigrams(normalizedRecommendation);
  if (normalizedRecommendation.length < 3 || recommendationBigrams.size === 0) return false;

  return evidenceTexts.some((text) => questionClauses(text).some((question) => {
    if (normalizedRecommendation === question
      || (question.length >= 4 && normalizedRecommendation.includes(question))) {
      return true;
    }
    const questionBigrams = characterBigrams(question);
    if (questionBigrams.size < 2) return false;
    let overlap = 0;
    for (const gram of questionBigrams) {
      if (recommendationBigrams.has(gram)) overlap += 1;
    }
    return overlap >= 2 && overlap / questionBigrams.size >= 0.75;
  }));
}

function validateInterventionIncrementalValue(intervention, context) {
  const referencedIds = new Set(intervention.evidence_segment_ids);
  const evidence = evidenceTextById(context);
  for (const candidate of context.candidate_events) {
    if (candidate.event_type !== "question_pending"
      || !candidate.evidence_segment_ids.some((id) => referencedIds.has(id))) {
      continue;
    }
    const texts = candidate.evidence_segment_ids.map((id) => evidence.get(id)).filter(Boolean);
    if (recommendationRestatesPendingQuestion(intervention.recommendation, texts)) {
      throw new PiCoachProtocolError(
        "intervention recommendation restates a pending question without an answer or closure move",
        "recommendation_restates_evidence",
      );
    }
  }
}

function compactDateTokens(value) {
  return [...String(value || "").matchAll(COMPACT_DISCOVERY_DATE_RE)]
    .map((match) => String(match[0]).toLocaleLowerCase());
}

function compactTopicClauses(texts) {
  return texts
    .flatMap((text) => String(text || "").split(COMPACT_TOPIC_BOUNDARY_RE))
    .map((clause) => clause.trim())
    .filter(Boolean);
}

function compactDiscoveryRecommendation(recommendation, candidate, texts) {
  // Discovery candidates should elicit missing context, not smuggle in a
  // frequency/date assumption. If the provider adds an unquoted counter or
  // date, replace only that sentence with a neutral, speakable question. This
  // is a safety repair (removing an unsupported claim), and applies only to
  // compact question_pending candidates; other event types remain fail-closed
  // under the Python semantic validator.
  if (candidate?.event_type !== "question_pending") return recommendation;
  const quote = texts.map((text) => String(text || "")).join("\n").toLocaleLowerCase();
  const hasUnquotedNumber = [...String(recommendation || "").matchAll(COMPACT_DISCOVERY_NUMBER_RE)]
    .some((match) => {
      const token = String(match[0]).toLocaleLowerCase();
      const before = String(recommendation || "").slice(Math.max(0, Number(match.index) - 1), match.index);
      const after = String(recommendation || "").slice(
        Number(match.index) + String(match[0]).length,
        Number(match.index) + String(match[0]).length + 2,
      );
      // "下一个议题" is a navigation cue, not a factual quantity. Keeping it
      // does not invent a meeting fact, while concrete counts such as "最近一次"
      // still take the fact-free discovery fallback below.
      if (token === "一个" && before === "下" && after === "议题") return false;
      return !quote.includes(token);
    });
  const hasUnquotedDate = [...String(recommendation || "").matchAll(COMPACT_DISCOVERY_DATE_RE)]
    .some((match) => !quote.includes(String(match[0]).toLocaleLowerCase()));
  return hasUnquotedNumber || hasUnquotedDate
    ? COMPACT_DISCOVERY_NEUTRAL_SENTENCE
    : recommendation;
}

function compactScopedOwnerRecommendation(recommendation, title, candidate, texts) {
  if (candidate?.event_type !== "question_pending") return recommendation;
  const cardText = `${String(title || "")}\n${String(recommendation || "")}`;
  const dates = compactDateTokens(cardText);
  if (dates.length === 0 || !COMPACT_OWNER_GAP_RE.test(cardText)) return recommendation;

  const normalizedCard = cardText.toLocaleLowerCase();
  const materialTerms = COMPACT_REASON_MATERIAL_TERMS.filter((term) =>
    normalizedCard.includes(term));
  if (materialTerms.length === 0) return recommendation;

  const clauses = compactTopicClauses(texts);
  const scopeIsGrounded = dates.every((date) => clauses.some((clause) => {
    const normalizedClause = clause.toLocaleLowerCase();
    return compactDateTokens(clause).includes(date)
      && COMPACT_OWNER_GAP_RE.test(clause)
      && materialTerms.some((term) => normalizedClause.includes(term));
  }));
  if (scopeIsGrounded) return recommendation;

  // Preserve a speakable owner question when the provider already emitted one
  // in a separate sentence. If the deadline and owner were fused together,
  // fall back to a fact-free owner question instead of inventing a new scope.
  const ownerOnly = String(recommendation || "")
    .split(/(?<=[。！？!?])/u)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence && compactDateTokens(sentence).length === 0)
    .join("")
    .trim();
  return ownerOnly.length >= 8 && COMPACT_OWNER_GAP_RE.test(ownerOnly)
    ? ownerOnly
    : COMPACT_OWNER_ONLY_SENTENCE;
}

function compactProviderDefaults(params, context) {
  const candidate = context.candidate_events[0];
  if (!candidate) return params;
  const preferred = preferredEventTypesForCandidate(candidate, context);
  const defaults = {
    event_type: preferred[0] || "question_to_user",
    title: "确认下一步",
    recommendation: candidate.event_type === "missing_next_step"
      ? "请确认下一步、负责人和回看时间。"
      : candidate.event_type === "commitment_without_condition"
        ? "先确认必要前提，再承诺具体时间。"
        : candidate.event_type === "goal_at_risk" || candidate.event_type === "topic_drift"
          ? "先回到本次目标，确认下一步。"
          : candidate.event_type === "objection_detected"
            ? "先回应这个异议，再确认是否继续。"
            : "请确认这件事具体由谁负责？",
    urgency: "high",
    confidence: 0.8,
  };
  return { ...defaults, ...(params || {}) };
}

function compactInterventionFromCandidate(params, context) {
  let normalizedParams = normalizeInterventionCardFields({
    ...compactProviderDefaults(params, context),
    // The compact contract intentionally omits why_now/reason; the host owns
    // the deterministic candidate reason and restores it below.
    reason: params.reason ?? "候选事件仍有可立即修正的具体缺口。",
  }, { allowAliasMismatch: true });
  // The model may choose a generic alias even when the selected scene skill
  // supplied a more specific event. Prefer a matching candidate, but fall
  // back to the first host candidate so one malformed enum cannot consume a
  // second turn; the host then enforces the candidate's first preferred type.
  const candidate = context.candidate_events.find((item) =>
    preferredEventTypesForCandidate(item, context).includes(normalizedParams.event_type))
    ?? context.candidate_events.find((item) => preferredEventTypesForCandidate(item, context).length > 0);
  if (!candidate) {
    throw new PiCoachProtocolError(
      "intervention event type does not match a supplied candidate",
      "invalid_agent_action",
    );
  }
  const preferredEventTypes = preferredEventTypesForCandidate(candidate, context);
  if (preferredEventTypes.length > 0) {
    normalizedParams = {
      ...normalizedParams,
      event_type: preferredEventTypes[0],
    };
  }
  const evidenceIds = [...candidate.evidence_segment_ids];
  if (COMPACT_CONTEXT_BACKED_CANDIDATES.has(candidate.event_type)) {
    const knownIds = new Set(evidenceIds);
    // ``buildRealtimeCandidatePrompt`` exposes at most two recent context
    // paragraphs. Keep the reconstructed evidence set in that same bound so
    // the model can only borrow facts from text it actually saw this turn.
    for (const paragraph of context.context_paragraphs.slice(-2)) {
      if (evidenceIds.length >= 8) break;
      if (knownIds.has(paragraph.id)) continue;
      evidenceIds.push(paragraph.id);
      knownIds.add(paragraph.id);
    }
  }
  const newEvidenceIds = new Set(context.new_paragraphs.map((paragraph) => paragraph.id));
  if (normalizedParams.event_type === "contradiction" && !evidenceIds.some((id) => !newEvidenceIds.has(id))) {
    const prior = context.context_paragraphs.at(-1) ?? context.retrieval_paragraphs.at(-1);
    if (prior) evidenceIds.push(prior.id);
  }
  const evidence = evidenceTextById(context);
  const texts = evidenceIds.map((id) => evidence.get(id)).filter(Boolean);
  const outputIsChinese = outputLanguage(context) === "zh-CN";
  const reason = compactCandidateReason(candidate, texts, outputIsChinese);
  const scopedRecommendation = compactScopedOwnerRecommendation(
    normalizedParams.recommendation,
    normalizedParams.title,
    candidate,
    texts,
  );
  const recommendation = compactDiscoveryRecommendation(
    scopedRecommendation,
    candidate,
    texts,
  );
  const provisionalEvidence = evidenceIds.some((id) => {
    const paragraph = [
      ...context.retrieval_paragraphs,
      ...context.context_paragraphs,
      ...context.candidate_evidence_paragraphs,
      ...context.new_paragraphs,
    ].find((item) => item.id === id);
    return ["unknown", "pending", "processing", "failed_preserved_original"].includes(
      paragraph?.correction_status,
    );
  });
  if (provisionalEvidence && !/请先确认|先确认|请确认|核实|询问|是否|能否|\?|？/u.test(recommendation)) {
    const prefix = outputIsChinese ? "请先确认：" : "Please confirm first: ";
    normalizedParams = {
      ...normalizedParams,
      recommendation: `${prefix}${recommendation}`.slice(0, 120),
      say_this: `${prefix}${recommendation}`.slice(0, 120),
    };
  } else {
    normalizedParams = {
      ...normalizedParams,
      recommendation,
      say_this: recommendation,
    };
  }
  return {
    ...normalizedParams,
    reason,
    why_now: reason,
    evidence_segment_ids: evidenceIds,
    evidence_quote: compactEvidenceQuote(texts, normalizedParams.event_type),
  };
}

function selectContext(scope, context) {
  if (scope === "semantic_windows") return { semantic_windows: context.semantic_windows };
  if (scope === "rolling_state") return { rolling_state: context.rolling_state };
  return { meeting_goal: context.meeting_goal };
}

function contextSignals(context) {
  const state = context.rolling_state && typeof context.rolling_state === "object"
    ? context.rolling_state
    : {};
  const openItems = Array.isArray(state.open_items)
    ? state.open_items.slice(-6)
    : [];
  const compactState = {};
  for (const key of ["topic", "summary", "version"]) {
    if (state[key] !== undefined) compactState[key] = state[key];
  }
  if (openItems.length > 0) compactState.open_items = openItems;
  return {
    trigger_type: context.trigger_type,
    work_item_id: context.work_item_id,
    user_request: context.user_request,
    meeting_goal: context.meeting_goal,
    rolling_state: compactState,
    recent_context_paragraphs: context.context_paragraphs.slice(-2),
    coach_skill_id: context.coach_skill.id,
  };
}

function outputLanguage(context) {
  const latestDialogue = [
    context.user_request ?? "",
    ...context.new_paragraphs.map((paragraph) => paragraph.text),
  ]
    .join("\n");
  const hanCharacters = latestDialogue.match(/\p{Script=Han}/gu)?.length ?? 0;
  return hanCharacters >= 2 ? "zh-CN" : "match-latest-dialogue";
}

function coachingChecklist(context) {
  return [...COACHING_CHECKLIST, ...context.coach_skill.checklist];
}

function usesCandidateFastPath(context) {
  return context.priority_mode === "realtime" && context.candidate_events.length > 0;
}

function candidateFastPathNeedsHistorySearch(context, hostEvidenceAvailable = false) {
  return (context.retrieval_paragraphs.length > 0 || hostEvidenceAvailable)
    && context.candidate_events.some((candidate) => candidate.event_type === "objection_detected");
}

function preferredEventTypesForCandidate(candidate, context) {
  const skillId = context.coach_skill.id;
  const preferences = {
    question_pending: skillId === "interview"
      ? ["discovery_gap", "question_to_user"]
      : ["question_to_user"],
    objection_detected: skillId === "decision"
      ? ["decision_readiness", "contradiction"]
      : ["contradiction"],
    commitment_without_condition: skillId === "decision"
      ? ["decision_readiness", "commitment_risk"]
      : skillId === "project"
        ? ["execution_gap", "commitment_risk"]
        : skillId === "brainstorm"
          ? ["experiment_gap", "commitment_risk"]
          : ["commitment_risk"],
    goal_at_risk: ["goal_at_risk"],
    topic_drift: ["goal_at_risk"],
    missing_next_step: skillId === "project"
      ? ["execution_gap", "commitment_risk"]
      : ["question_to_user", "commitment_risk"],
    monologue_duration: ["communication_clarity"],
    repetition: ["communication_clarity"],
  }[candidate.event_type] || [];
  return preferences.filter((eventType) => allowedEventTypes(context).has(eventType));
}

function setTerminalAction(entry, action) {
  const run = activeRunOrThrow(entry);
  if (run.terminalAction) {
    throw new PiCoachProtocolError("the agent selected more than one terminal action", "invalid_agent_action");
  }
  run.terminalAction = action;
}

function activeRunOrThrow(entry) {
  if (!entry.run || entry.run.cancelled) {
    // A provider may ignore AbortSignal and finish a tool callback after the
    // host deadline. Keep that callback on the stable deadline path instead
    // of dereferencing the cleared run state and crashing the sidecar.
    throw new PiCoachProtocolError(
      "Pi agent evaluation is no longer active",
      "agent_deadline_exceeded",
    );
  }
  return entry.run;
}

function activeContextOrThrow(entry) {
  activeRunOrThrow(entry);
  if (!entry.activeContext) {
    throw new PiCoachProtocolError(
      "Pi agent evaluation context is no longer active",
      "agent_deadline_exceeded",
    );
  }
  return entry.activeContext;
}

function toolErrorCode(result) {
  const text = (result?.content ?? [])
    .filter((block) => block?.type === "text")
    .map((block) => String(block.text || ""))
    .join(" ")
    .toLocaleLowerCase();
  if (text.includes("unknown evidence")) return "unknown_evidence";
  if (text.includes("evidence quote is not verbatim")) return "evidence_quote_not_verbatim";
  if (text.includes("required zh-cn output language")) return "output_language_mismatch";
  if (text.includes("restates a pending question")) return "recommendation_restates_evidence";
  if (text.includes("require prior evidence")) return "prior_evidence_required";
  if (text.includes("more than one terminal action")) return "multiple_terminal_actions";
  if (text.includes("checklist")) return "checklist_not_reviewed";
  return "tool_execution_error";
}

function createRestrictedTools(
  entry,
  { includeContextRead = true, includeHistorySearch = true, compactTerminal = false } = {},
) {
  const tools = [
    {
      name: "read_realtime_context",
      label: "Read realtime context",
      description: "Read one bounded part of the current Talktrace context when necessary for the decision.",
      parameters: readContextParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        const run = activeRunOrThrow(entry);
        const context = activeContextOrThrow(entry);
        run.contextReads += 1;
        return {
          content: [{ type: "text", text: JSON.stringify(selectContext(params.scope, context)) }],
          details: { scope: params.scope },
        };
      },
    },
    {
      name: "search_prior_evidence",
      label: "Search prior evidence",
      description: "Search bounded earlier transcript evidence for a prior condition, position, question, or commitment.",
      parameters: searchEvidenceParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        const run = activeRunOrThrow(entry);
        const context = activeContextOrThrow(entry);
        const results = run.searchEvidence
          ? validateParagraphs(
            await run.searchEvidence({
              query: params.query,
              max_results: params.max_results ?? 4,
              include_neighbors: params.include_neighbors === true,
            }),
            "host_evidence", 6,
          )
          : searchPriorEvidence(params.query, context.retrieval_paragraphs, params.max_results ?? 4);
        // An obsolete callback must not register evidence into a newer run.
        if (activeRunOrThrow(entry) !== run) {
          throw new PiCoachProtocolError("obsolete evidence response", "pi_cancelled");
        }
        if (run.searchEvidence) {
          const known = new Map(context.retrieval_paragraphs.map((item) => [item.id, item]));
          for (const item of results) {
            const current = [...context.new_paragraphs, ...context.context_paragraphs,
              ...context.candidate_evidence_paragraphs, ...known.values()].find((p) => p.id === item.id);
            if (current && (current.text !== item.text || current.revision !== item.revision)) {
              throw new PiCoachProtocolError("evidence revision changed", "invalid_agent_action");
            }
            known.set(item.id, item);
          }
          context.retrieval_paragraphs = [...known.values()];
        }
        run.historySearches += 1;
        run.historyResults += results.length;
        return {
          content: [{ type: "text", text: JSON.stringify({ query: params.query, results }) }],
          details: { result_count: results.length },
        };
      },
    },
    {
      name: "read_transcript_span",
      label: "Read transcript span",
      description: "Read the exact current transcript segment and a small bounded neighborhood after locating it.",
      parameters: readTranscriptSpanParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        const run = activeRunOrThrow(entry);
        const context = activeContextOrThrow(entry);
        const segmentId = params.segment_id;
        const before = params.before ?? 1;
        const after = params.after ?? 1;
        const results = run.readEvidenceSpan
          ? validateParagraphs(
            await run.readEvidenceSpan({ segment_id: segmentId, before, after }),
            "host_evidence_span", 7,
          )
          : readTranscriptSpan(
            segmentId,
            [...context.retrieval_paragraphs, ...context.context_paragraphs, ...context.new_paragraphs],
            before,
            after,
          );
        if (activeRunOrThrow(entry) !== run) {
          throw new PiCoachProtocolError("obsolete evidence response", "pi_cancelled");
        }
        if (run.readEvidenceSpan) {
          const known = new Map(context.retrieval_paragraphs.map((item) => [item.id, item]));
          for (const item of results) {
            const current = [...context.new_paragraphs, ...context.context_paragraphs,
              ...context.candidate_evidence_paragraphs, ...known.values()].find((p) => p.id === item.id);
            if (current && (current.text !== item.text || current.revision !== item.revision)) {
              throw new PiCoachProtocolError("evidence revision changed", "invalid_agent_action");
            }
            known.set(item.id, item);
          }
          context.retrieval_paragraphs = [...known.values()];
        }
        run.historySearches += 1;
        run.historyResults += results.length;
        return {
          content: [{ type: "text", text: JSON.stringify({ segment_id: segmentId, results }) }],
          details: { result_count: results.length, tool: "read_transcript_span" },
        };
      },
    },
    {
      name: "submit_intervention",
      label: "Submit intervention",
      description: compactTerminal
        ? "Submit one grounded intervention and stop."
        : "Submit one evidence-grounded intervention that is still useful right now, then stop.",
      parameters: compactTerminal ? compactInterventionParameters : interventionParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        const run = activeRunOrThrow(entry);
        const context = activeContextOrThrow(entry);
        if (!run.checklistReviewed) {
          throw new PiCoachProtocolError("host coaching checklist must be complete", "checklist_not_reviewed");
        }
        const candidateParams = compactTerminal
          ? compactInterventionFromCandidate(params, context)
          : params;
        const intervention = validateInterventionEvidence(candidateParams, context);
        setTerminalAction(entry, { action: "intervention", intervention });
        return {
          content: [{ type: "text", text: "Intervention accepted." }],
          details: { action: "intervention", event_type: intervention.event_type },
          terminate: true,
        };
      },
    },
    {
      name: "keep_silent",
      label: "Keep silent",
      description: compactTerminal
        ? "Choose no intervention and stop."
        : "Choose no interruption because there is no sufficiently valuable realtime action, then stop.",
      parameters: silentParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        const run = activeRunOrThrow(entry);
        if (!run.checklistReviewed) {
          throw new PiCoachProtocolError("host coaching checklist must be complete", "checklist_not_reviewed");
        }
        setTerminalAction(entry, {
          action: "silent",
          reason: params?.reason || "当前证据不足以形成可靠的即时建议。",
        });
        return {
          content: [{ type: "text", text: "Silence accepted." }],
          details: { action: "silent" },
          terminate: true,
        };
      },
    },
  ];
  return tools.filter((tool) => {
    if (tool.name === "read_realtime_context") return includeContextRead;
    if (tool.name === "search_prior_evidence" || tool.name === "read_transcript_span") return includeHistorySearch;
    return true;
  });
}

function compactPromptParagraph(paragraph) {
  return {
    id: paragraph.id,
    text: paragraph.text,
    source_track: paragraph.source_track,
    role_hint: paragraph.role_hint,
    correction_status: paragraph.correction_status,
    evidence_quality: paragraph.evidence_quality,
  };
}

function buildRealtimeCandidatePrompt(context) {
  const signals = contextSignals(context);
  const allowedTypes = [...allowedEventTypes(context)].filter(Boolean).sort();
  const evidenceById = new Map(
    [
      ...context.retrieval_paragraphs,
      ...context.context_paragraphs,
      ...context.candidate_evidence_paragraphs,
      ...context.new_paragraphs,
    ].map((paragraph) => [paragraph.id, paragraph]),
  );
  const candidateEvidenceIds = [...new Set(
    context.candidate_events.flatMap((candidate) => candidate.evidence_segment_ids),
  )];
  const alreadyVisibleEvidenceIds = new Set([
    ...context.new_paragraphs.map((paragraph) => paragraph.id),
    ...context.context_paragraphs.slice(-2).map((paragraph) => paragraph.id),
  ]);
  const supplementalCandidateEvidence = candidateEvidenceIds
    .filter((paragraphId) => !alreadyVisibleEvidenceIds.has(paragraphId))
    .map((paragraphId) => evidenceById.get(paragraphId))
    .filter(Boolean)
    .map(compactPromptParagraph);
  const payload = {
    task: "decide_realtime_candidate",
    trigger_type: context.trigger_type,
    output_language: outputLanguage(context),
    output_language_contract: "title,recommendation,reason use output_language; say_this and why_now are accepted aliases",
    host_checklist_reviewed: true,
    candidate_events: context.candidate_events.map((candidate) => ({
      event_type: candidate.event_type,
      evidence_segment_ids: candidate.evidence_segment_ids,
      reason: candidate.reason,
      preferred_event_types: preferredEventTypesForCandidate(candidate, context),
    })),
    new_paragraphs: context.new_paragraphs.map(compactPromptParagraph),
    recent_context_paragraphs: context.context_paragraphs.slice(-2).map(compactPromptParagraph),
    rolling_state: signals.rolling_state,
    skill: {
      id: context.coach_skill.id,
      objective: context.coach_skill.objective,
      intervention_style: context.coach_skill.intervention_style,
      allowed_event_types: allowedTypes,
    },
  };
  if (context.work_item_id) payload.work_item_id = context.work_item_id;
  if (context.user_request) payload.user_request = context.user_request;
  if (supplementalCandidateEvidence.length > 0) {
    payload.candidate_evidence_paragraphs = supplementalCandidateEvidence;
  }
  if (context.meeting_goal) payload.meeting_goal = context.meeting_goal;
  if (context.retrieval_paragraphs.length > 0) {
    payload.searchable_prior_paragraph_count = context.retrieval_paragraphs.length;
  }
  return JSON.stringify(payload);
}

function buildSparkCandidatePrompt(context) {
  const evidence = [
    ...context.new_paragraphs,
    ...context.context_paragraphs.slice(-2),
    ...context.candidate_evidence_paragraphs,
  ];
  const seen = new Set();
  const paragraphs = evidence
    .filter((paragraph) => !seen.has(paragraph.id) && seen.add(paragraph.id))
    .map((paragraph) => ({
      id: paragraph.id,
      text: paragraph.text,
      correction_status: paragraph.correction_status,
      evidence_quality: paragraph.evidence_quality,
    }));
  return JSON.stringify({
    task: "realtime_coach",
    language: outputLanguage(context),
    candidates: context.candidate_events.map((candidate) => ({
      event_type: candidate.event_type,
      evidence_segment_ids: candidate.evidence_segment_ids,
      preferred_event_types: preferredEventTypesForCandidate(candidate, context),
    })),
    paragraphs,
    goal: context.meeting_goal,
  });
}

function buildPrompt(context, promptProfile = "full", model = null) {
  if (promptProfile === "candidate_fast") {
    return /codex-spark/i.test(String(model?.id || ""))
      ? buildSparkCandidatePrompt(context)
      : buildRealtimeCandidatePrompt(context);
  }
  const checklist = coachingChecklist(context);
  const hasRealtimeCandidates = context.priority_mode === "realtime"
    && context.candidate_events.length > 0;
  const payload = {
    task: "decide_realtime_coaching_intervention",
    output_language: outputLanguage(context),
    output_language_contract: "title, recommendation, and reason must each use output_language; say_this and why_now are accepted aliases",
    state_revision: context.state_revision,
    trigger_type: context.trigger_type,
    priority_mode: context.priority_mode,
    candidate_events: context.candidate_events,
    new_paragraphs: context.new_paragraphs,
    checklist_reviewed: true,
    checklist,
    coach_skill: context.coach_skill,
    context_signals: contextSignals(context),
    searchable_prior_paragraph_count: context.retrieval_paragraphs.length,
    reminder: hasRealtimeCandidates
      ? "The host checklist is complete. Evaluate candidate_events first in the supplied priority order. Confirm each candidate against candidate_evidence_paragraphs; candidate reasons are not evidence. Follow output_language for title, recommendation, and reason. Copy evidence_quote verbatim from the text of paragraphs named in evidence_segment_ids. If a candidate is actionable and evidence is sufficient, use exactly one terminal tool in this first response."
      : "The host checklist is complete. Follow output_language for title, recommendation, and reason. Copy evidence_quote verbatim from the text of paragraphs named in evidence_segment_ids. Use exactly one terminal tool now when evidence is sufficient; otherwise use one context tool, then decide next turn.",
  };
  if (context.work_item_id) payload.work_item_id = context.work_item_id;
  if (context.user_request) payload.user_request = context.user_request;
  return JSON.stringify(payload);
}

function isFirstTokenEvent(event) {
  return event.type === "message_update"
    && ["text_delta", "thinking_delta", "toolcall_delta"].includes(
      event.assistantMessageEvent?.type,
    );
}

function toolSchemaCharacters(tools) {
  return JSON.stringify(
    tools.map((tool) => ({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
    })),
  ).length;
}

function createSessionEntry(
  sessionId,
  backend,
  clock,
  wallClock,
  { promptProfile = "full", includeHistorySearch = true, compactTerminal = false } = {},
) {
  const entry = {
    sessionId,
    backendIdentity: backend.identity,
    promptProfile,
    historySearchEnabled: includeHistorySearch,
    compactTerminal,
    activeContext: null,
    run: null,
    agent: null,
  };
  const tools = createRestrictedTools(entry, {
    includeContextRead: promptProfile === "full",
    includeHistorySearch: promptProfile === "full" || includeHistorySearch,
    compactTerminal,
  });
  entry.agent = new Agent({
    initialState: {
      systemPrompt: systemPromptFor(backend.model, promptProfile, compactTerminal),
      model: backend.model,
      thinkingLevel: "off",
      tools,
      messages: [],
    },
    streamFn: backend.streamFn,
    transformContext: async (messages) => pruneContext(messages),
    toolExecution: "sequential",
    sessionId,
    shouldStopAfterTurn: () => !entry.run
      || Boolean(entry.run.terminalAction)
      || entry.run.turns >= MAX_AGENT_TURNS_PER_EVALUATION,
    beforeToolCall: async ({ toolCall }) => {
      if (!entry.run || entry.run.cancelled) {
        return { block: true, reason: "Agent evaluation deadline exceeded.", terminate: true };
      }
      entry.run.toolCalls += 1;
      entry.run.toolNames.push(toolCall.name);
      if (entry.run.toolCalls > MAX_TOOL_CALLS_PER_EVALUATION) {
        return { block: true, reason: "Tool-call budget exhausted.", terminate: true };
      }
      return undefined;
    },
    afterToolCall: async ({ toolCall, result, isError }) => {
      if (!entry.run || entry.run.cancelled) return undefined;
      if (isError) {
        entry.run.toolErrors.push({ tool: toolCall.name, code: toolErrorCode(result) });
      }
      return undefined;
    },
  });
  entry.agent.subscribe((event) => {
    if (event.type === "turn_start" && entry.run) entry.run.turns += 1;
    if (isFirstTokenEvent(event) && entry.run && entry.run.firstTokenAtMs === null) {
      entry.run.firstTokenAtMs = clock();
      entry.run.firstTokenAtWallMs = wallClock();
    }
  });
  return entry;
}

function usageFromMessages(messages) {
  return messages.reduce(
    (total, message) => {
      if (message.role !== "assistant" || !message.usage) return total;
      const inputTokens = Number(message.usage.input || 0);
      const cacheReadTokens = Number(message.usage.cacheRead || 0);
      const cacheWriteTokens = Number(message.usage.cacheWrite || 0);
      const outputTokens = Number(message.usage.output || 0);
      total.input_tokens += inputTokens;
      total.cache_read_tokens += cacheReadTokens;
      total.cache_write_tokens += cacheWriteTokens;
      total.reasoning_tokens += Number(message.usage.reasoning || 0);
      total.prompt_tokens += inputTokens + cacheReadTokens + cacheWriteTokens;
      total.completion_tokens += outputTokens;
      total.total_tokens = total.prompt_tokens + total.completion_tokens;
      return total;
    },
    {
      prompt_tokens: 0,
      input_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      reasoning_tokens: 0,
      completion_tokens: 0,
      total_tokens: 0,
    },
  );
}

function failureMetrics({
  entry,
  priorMessageCount,
  startedAt,
  clock,
  promptText,
  promptProfile,
  sessionReused,
  decisionTimeoutMs,
  providerStatusCode,
  providerRetryAfterMs,
  providerConnectMs,
}) {
  const run = entry.run || {};
  const elapsedMs = Math.max(0, clock() - startedAt);
  const ttftMs = run.firstTokenAtMs === null || run.firstTokenAtMs === undefined
    ? null
    : Math.min(elapsedMs, Math.max(0, run.firstTokenAtMs - startedAt));
  const metrics = {
    elapsed_ms: elapsedMs,
    decision_latency_ms: elapsedMs,
    ttft_ms: ttftMs,
    turns: Number(run.turns || 0),
    tool_calls: Number(run.toolCalls || 0),
    tool_names: [...(run.toolNames || [])].slice(0, MAX_TOOL_CALLS_PER_EVALUATION),
    tool_errors: [...(run.toolErrors || [])].slice(0, MAX_TOOL_CALLS_PER_EVALUATION),
    context_reads: Number(run.contextReads || 0),
    prompt_profile: promptProfile,
    prompt_characters: promptText.length,
    system_prompt_characters: entry.agent.state.systemPrompt.length,
    tool_schema_characters: toolSchemaCharacters(entry.agent.state.tools),
    request_characters: promptText.length
      + entry.agent.state.systemPrompt.length
      + toolSchemaCharacters(entry.agent.state.tools),
    available_tool_names: entry.agent.state.tools.map((tool) => tool.name),
    session_message_count_before: priorMessageCount,
    session_reused: sessionReused,
    decision_latency_budget_ms: DECISION_LATENCY_BUDGET_MS,
    decision_timeout_ms: decisionTimeoutMs,
    usage: usageFromMessages(entry.agent.state.messages.slice(priorMessageCount)),
  };
  const normalizedProviderStatusCode = Number(providerStatusCode);
  if (
    Number.isInteger(normalizedProviderStatusCode)
    && normalizedProviderStatusCode >= 100
    && normalizedProviderStatusCode <= 599
  ) {
    metrics.provider_status_code = normalizedProviderStatusCode;
  }
  const normalizedProviderRetryAfterMs = Number(providerRetryAfterMs);
  if (
    Number.isInteger(normalizedProviderRetryAfterMs)
    && normalizedProviderRetryAfterMs >= 0
    && normalizedProviderRetryAfterMs <= MAX_RETRY_AFTER_MS
  ) {
    metrics.provider_retry_after_ms = normalizedProviderRetryAfterMs;
  }
  const normalizedProviderConnectMs = Number(providerConnectMs);
  if (Number.isFinite(normalizedProviderConnectMs) && normalizedProviderConnectMs >= 0) {
    metrics.provider_connect_ms = normalizedProviderConnectMs;
  }
  return metrics;
}

export class PiCoachRuntime {
  constructor({
    backendFactory = createOpenAICompatibleBackend,
    clock = () => performance.now(),
    wallClock = () => Date.now(),
  } = {}) {
    this.backendFactory = backendFactory;
    this.clock = clock;
    this.wallClock = wallClock;
    this.sessions = new Map();
  }

  get sessionCount() {
    return this.sessions.size;
  }

  async evaluate(rawRequest, { searchEvidence = null, readEvidenceSpan = null } = {}) {
    const request = validateEvaluationRequest(rawRequest);
    const backend = this.backendFactory(request.provider);
    let entry = this.sessions.get(request.session_id);
    const promptProfile = usesCandidateFastPath(request.context) ? "candidate_fast" : "full";
    // A host evidence channel being available does not mean every realtime
    // candidate needs the full retrieval tool schema. Candidate events already
    // carry bounded, validated evidence in the compact fast path; exposing
    // search/read tools on all of those turns adds prompt/schema overhead and
    // can push the provider's first token past the realtime cutoff. Keep tools
    // for deep/full requests and the narrow candidate classes that genuinely
    // need prior-history lookup.
    const hostEvidenceAvailable = typeof searchEvidence === "function"
      || typeof readEvidenceSpan === "function";
    const needsHistorySearch = promptProfile !== "candidate_fast"
      || candidateFastPathNeedsHistorySearch(request.context, hostEvidenceAvailable);
    // A history-search candidate may need model-selected prior evidence, so it
    // retains the full terminal schema. The compact contract is only safe when
    // the host can reconstruct all evidence from the bounded candidate.
    const compactTerminal = promptProfile === "candidate_fast"
      && request.context.compact_terminal_contract
      && !needsHistorySearch;
    const skillIdentity = `${request.context.coach_skill.id}:${request.context.coach_skill.version}`;
    const sessionReused = Boolean(
      entry
      && entry.backendIdentity === backend.identity
      && entry.skillIdentity === skillIdentity
      && entry.promptProfile === promptProfile
      && entry.compactTerminal === compactTerminal
      && entry.historySearchEnabled === needsHistorySearch
    );
    if (!sessionReused) {
      const canCarryHistory = Boolean(
        entry
        && entry.promptProfile === promptProfile
        && entry.skillIdentity === skillIdentity
        && entry.agent?.state?.messages?.length,
      );
      const priorMessages = canCarryHistory ? pruneContext(entry.agent.state.messages) : [];
      entry = createSessionEntry(
        request.session_id,
        backend,
        this.clock,
        this.wallClock,
        { promptProfile, includeHistorySearch: needsHistorySearch, compactTerminal },
      );
      entry.skillIdentity = skillIdentity;
      if (priorMessages.length > 0) entry.agent.state.messages = priorMessages;
      this.sessions.set(request.session_id, entry);
      while (this.sessions.size > MAX_SESSIONS) {
        const oldestSessionId = this.sessions.keys().next().value;
        const oldest = this.sessions.get(oldestSessionId);
        oldest?.agent.reset();
        this.sessions.delete(oldestSessionId);
      }
    } else {
      this.sessions.delete(request.session_id);
      this.sessions.set(request.session_id, entry);
      entry.agent.state.model = backend.model;
      entry.agent.streamFunction = backend.streamFn;
    }
    entry.historySearchEnabled = needsHistorySearch;
    entry.compactTerminal = compactTerminal;
    entry.agent.state.systemPrompt = systemPromptFor(backend.model, promptProfile, compactTerminal);
    entry.agent.state.tools = createRestrictedTools(entry, {
      includeContextRead: promptProfile === "full",
      includeHistorySearch: promptProfile === "full" || entry.historySearchEnabled,
      compactTerminal,
    });
    entry.activeContext = request.context;
    entry.run = {
      searchEvidence: typeof searchEvidence === "function" ? searchEvidence : null,
      readEvidenceSpan: typeof readEvidenceSpan === "function" ? readEvidenceSpan : null,
      cancelled: false,
      terminalAction: null,
      turns: 0,
      toolCalls: 0,
      toolNames: [],
      toolErrors: [],
      contextReads: 0,
      checklistReviewed: true,
      checklistReviews: 1,
      historySearches: 0,
      historyResults: 0,
      firstTokenAtMs: null,
      firstTokenAtWallMs: null,
    };
    const priorMessageCount = entry.agent.state.messages.length;
    const startedAtWallMs = this.wallClock();
    const startedAt = this.clock();
    const decisionTimeoutMs = Math.max(
      1,
      Math.min(Number(backend.decisionTimeoutMs) || DECISION_LATENCY_BUDGET_MS, DECISION_LATENCY_BUDGET_MS),
    );
    const promptText = buildPrompt(request.context, promptProfile, backend.model);
    let deadlineExceeded = false;
    let deadlineTimer;
    try {
      const promptPromise = entry.agent.prompt(promptText);
      // Agent.prompt normally propagates provider cancellation. Attach a
      // handler even when the hard deadline wins the race so a late SDK
      // rejection cannot become an unhandled process error.
      promptPromise.catch(() => undefined);
      const deadlinePromise = new Promise((_, reject) => {
        deadlineTimer = setTimeout(() => {
          deadlineExceeded = true;
          if (entry.run) entry.run.cancelled = true;
          entry.agent.abort();
          reject(new PiCoachProtocolError("Pi agent exceeded its total decision deadline", "agent_deadline_exceeded"));
        }, decisionTimeoutMs);
      });
      await Promise.race([promptPromise, deadlinePromise]);
      if (deadlineExceeded) {
        throw new PiCoachProtocolError("Pi agent exceeded its total decision deadline", "agent_deadline_exceeded");
      }
      if (entry.agent.state.errorMessage) {
        throw new PiCoachProtocolError(entry.agent.state.errorMessage, "agent_provider_error");
      }
      if (!entry.run.terminalAction) {
        throw new PiCoachProtocolError("Pi agent did not choose a terminal action", "missing_terminal_action");
      }
      const elapsedMs = Math.max(0, this.clock() - startedAt);
      const completedAtWallMs = Math.max(startedAtWallMs, this.wallClock());
      const ttftMs = entry.run.firstTokenAtMs === null
        ? null
        : Math.min(elapsedMs, Math.max(0, entry.run.firstTokenAtMs - startedAt));
      const firstTokenAtWallMs = entry.run.firstTokenAtWallMs === null
        ? null
        : Math.max(
          startedAtWallMs,
          Math.min(entry.run.firstTokenAtWallMs, completedAtWallMs),
        );
      const runUsage = usageFromMessages(entry.agent.state.messages.slice(priorMessageCount));
      const checklist = coachingChecklist(request.context);
      entry.agent.state.messages = pruneContext(entry.agent.state.messages);
      return {
        protocol: PROTOCOL,
        request_id: request.request_id,
        ok: true,
        runtime: "pi-agent-core",
        action: entry.run.terminalAction.action,
        intervention: entry.run.terminalAction.intervention ?? null,
        decision_reason: entry.run.terminalAction.reason ?? null,
        metrics: {
          elapsed_ms: elapsedMs,
          decision_latency_ms: elapsedMs,
          ttft_ms: ttftMs,
          timings: {
            clock: "unix_epoch_ms",
            started_at_ms: startedAtWallMs,
            first_token_at_ms: firstTokenAtWallMs,
            completed_at_ms: completedAtWallMs,
          },
          turns: entry.run.turns,
          tool_calls: entry.run.toolCalls,
          tool_names: [...entry.run.toolNames],
          tool_errors: [...entry.run.toolErrors],
          context_reads: entry.run.contextReads,
          checklist_reviewed: entry.run.checklistReviewed,
          checklist_reviews: entry.run.checklistReviews,
          checklist_item_ids: checklist.map((item) => item.id),
          coach_skill_id: request.context.coach_skill.id,
          coach_skill_version: request.context.coach_skill.version,
          decision_latency_budget_ms: DECISION_LATENCY_BUDGET_MS,
          within_latency_budget: elapsedMs <= DECISION_LATENCY_BUDGET_MS,
          decision_timeout_ms: decisionTimeoutMs,
          prompt_profile: promptProfile,
          compact_terminal_tools: compactTerminal,
          prompt_characters: promptText.length,
          system_prompt_characters: entry.agent.state.systemPrompt.length,
          tool_schema_characters: toolSchemaCharacters(entry.agent.state.tools),
          request_characters: promptText.length
            + entry.agent.state.systemPrompt.length
            + toolSchemaCharacters(entry.agent.state.tools),
          available_tool_names: entry.agent.state.tools.map((tool) => tool.name),
          session_message_count_before: priorMessageCount,
          history_searches: entry.run.historySearches,
          history_results: entry.run.historyResults,
          session_reused: sessionReused,
          provider_connect_ms: backend.lastProviderConnectMs,
          usage: runUsage,
        },
      };
    } catch (error) {
      if (error && typeof error === "object") {
        error.metrics = failureMetrics({
          entry,
          priorMessageCount,
          startedAt,
          clock: this.clock,
          promptText,
          promptProfile,
          sessionReused,
          decisionTimeoutMs,
          providerStatusCode: backend.lastProviderStatusCode,
          providerRetryAfterMs: backend.lastProviderRetryAfterMs,
          providerConnectMs: backend.lastProviderConnectMs,
        });
      }
      this.sessions.delete(request.session_id);
      // An SDK/provider that ignores AbortSignal may still be winding down
      // when the hard deadline is reported. The sidecar will discard this
      // session; resetting an active Agent would mask the stable deadline code.
      if (!entry.agent.state.isStreaming) entry.agent.reset();
      throw error;
    } finally {
      if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
      entry.activeContext = null;
      entry.run = null;
    }
  }
}

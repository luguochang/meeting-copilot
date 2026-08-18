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
const SUPPORTED_SKILL_IDS = new Set(["general", "decision", "project", "interview", "brainstorm"]);
const URGENCIES = new Set(["low", "medium", "high"]);
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
  "When evidence is already sufficient, call exactly one terminal tool in the first response.",
  "When prior evidence is needed, call search_prior_evidence in the first response, then use one terminal tool in the next response.",
  "When context_signals suggest an earlier condition or position may conflict with the latest utterance, use search_prior_evidence before deciding.",
  "Use read_realtime_context for semantic windows that are still needed after reviewing context_signals.",
  "Never invent a person, number, deadline, position, or goal.",
  "Match the language of the latest dialogue in the intervention, which will usually be Chinese.",
  "You must finish by calling exactly one terminal tool: submit_intervention or keep_silent. Do not answer with ordinary text.",
].join(" ");

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
    recommendation: Type.String({ minLength: 8, maxLength: 120 }),
    reason: Type.String({ minLength: 1, maxLength: 300 }),
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

const silentParameters = Type.Object(
  {
    reason: Type.String({ minLength: 1, maxLength: 160 }),
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

function validateParagraphs(value, field, maximumItems) {
  if (!Array.isArray(value) || value.length > maximumItems) {
    throw new PiCoachProtocolError(`${field} must be a bounded array`, "invalid_request");
  }
  return value.map((item, index) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) {
      throw new PiCoachProtocolError(`${field}[${index}] must be an object`, "invalid_request");
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

function validateContext(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PiCoachProtocolError("context must be an object", "invalid_request");
  }
  const newParagraphs = validateParagraphs(value.new_paragraphs, "context.new_paragraphs", 8);
  if (newParagraphs.length === 0) {
    throw new PiCoachProtocolError("context.new_paragraphs must not be empty", "invalid_request");
  }
  const contextParagraphs = validateParagraphs(value.context_paragraphs ?? [], "context.context_paragraphs", 3);
  const retrievalParagraphs = validateParagraphs(
    value.retrieval_paragraphs ?? [],
    "context.retrieval_paragraphs",
    MAX_RETRIEVAL_PARAGRAPHS,
  );
  const semanticWindows = Array.isArray(value.semantic_windows) ? value.semantic_windows.slice(-8) : [];
  const rollingState = value.rolling_state && typeof value.rolling_state === "object" ? value.rolling_state : {};
  const coachSkill = validateCoachSkill(value.coach_skill);
  const serialized = JSON.stringify({
    newParagraphs,
    contextParagraphs,
    retrievalParagraphs,
    semanticWindows,
    rollingState,
    coachSkill,
  });
  if (serialized.length > 160000) {
    throw new PiCoachProtocolError("context exceeds the bridge byte budget", "invalid_request");
  }
  return {
    state_revision: Number.isInteger(value.state_revision) && value.state_revision > 0 ? value.state_revision : 1,
    new_paragraphs: newParagraphs,
    context_paragraphs: contextParagraphs,
    retrieval_paragraphs: retrievalParagraphs,
    semantic_windows: semanticWindows,
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

export function createOpenAICompatibleBackend(providerInput) {
  if (!providerInput || typeof providerInput !== "object" || Array.isArray(providerInput)) {
    throw new PiCoachProtocolError("provider must be an object", "invalid_provider");
  }
  const apiKey = requiredText(providerInput.api_key, "provider.api_key", 8192);
  const modelId = requiredText(providerInput.model, "provider.model", 240);
  const apiStyle = providerInput.api_style === "responses" ? "responses" : "chat_completions";
  const api = apiStyle === "responses" ? "openai-responses" : "openai-completions";
  const baseUrl = normalizeGatewayBaseUrl(providerInput.base_url);
  const providerId = "talktrace-openai-compatible";
  const model = {
    id: modelId,
    name: modelId,
    api,
    provider: providerId,
    baseUrl,
    reasoning: false,
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
  return {
    identity: `${api}:${baseUrl}:${modelId}`,
    model: configuredModel,
    streamFn: (activeModel, context, options) =>
      models.streamSimple(activeModel, context, {
        ...options,
        temperature: 0.1,
        maxTokens: 512,
        timeoutMs,
        maxRetries: 0,
        cacheRetention: "short",
      }),
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
    [...context.retrieval_paragraphs, ...context.context_paragraphs, ...context.new_paragraphs].map(
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

function validateInterventionEvidence(intervention, context) {
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
  return { ...intervention, evidence_segment_ids: uniqueIds };
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
    meeting_goal: context.meeting_goal,
    rolling_state: compactState,
    recent_context_paragraphs: context.context_paragraphs.slice(-2),
    coach_skill_id: context.coach_skill.id,
  };
}

function coachingChecklist(context) {
  return [...COACHING_CHECKLIST, ...context.coach_skill.checklist];
}

function setTerminalAction(entry, action) {
  if (entry.run.terminalAction) {
    throw new PiCoachProtocolError("the agent selected more than one terminal action", "invalid_agent_action");
  }
  entry.run.terminalAction = action;
}

function toolErrorCode(result) {
  const text = (result?.content ?? [])
    .filter((block) => block?.type === "text")
    .map((block) => String(block.text || ""))
    .join(" ")
    .toLocaleLowerCase();
  if (text.includes("unknown evidence")) return "unknown_evidence";
  if (text.includes("evidence quote is not verbatim")) return "evidence_quote_not_verbatim";
  if (text.includes("require prior evidence")) return "prior_evidence_required";
  if (text.includes("more than one terminal action")) return "multiple_terminal_actions";
  if (text.includes("checklist")) return "checklist_not_reviewed";
  return "tool_execution_error";
}

function createRestrictedTools(entry) {
  return [
    {
      name: "read_realtime_context",
      label: "Read realtime context",
      description: "Read one bounded part of the current Talktrace context when necessary for the decision.",
      parameters: readContextParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        entry.run.contextReads += 1;
        return {
          content: [{ type: "text", text: JSON.stringify(selectContext(params.scope, entry.activeContext)) }],
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
        const results = searchPriorEvidence(
          params.query,
          entry.activeContext.retrieval_paragraphs,
          params.max_results ?? 4,
        );
        entry.run.historySearches += 1;
        entry.run.historyResults += results.length;
        return {
          content: [{ type: "text", text: JSON.stringify({ query: params.query, results }) }],
          details: { result_count: results.length },
        };
      },
    },
    {
      name: "submit_intervention",
      label: "Submit intervention",
      description: "Submit one evidence-grounded intervention that is still useful right now, then stop.",
      parameters: interventionParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        if (!entry.run.checklistReviewed) {
          throw new PiCoachProtocolError("host coaching checklist must be complete", "checklist_not_reviewed");
        }
        const intervention = validateInterventionEvidence(params, entry.activeContext);
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
      description: "Choose no interruption because there is no sufficiently valuable realtime action, then stop.",
      parameters: silentParameters,
      executionMode: "sequential",
      execute: async (_toolCallId, params) => {
        if (!entry.run.checklistReviewed) {
          throw new PiCoachProtocolError("host coaching checklist must be complete", "checklist_not_reviewed");
        }
        setTerminalAction(entry, { action: "silent", reason: params.reason });
        return {
          content: [{ type: "text", text: "Silence accepted." }],
          details: { action: "silent" },
          terminate: true,
        };
      },
    },
  ];
}

function buildPrompt(context) {
  const checklist = coachingChecklist(context);
  return JSON.stringify({
    task: "decide_realtime_coaching_intervention",
    state_revision: context.state_revision,
    new_paragraphs: context.new_paragraphs,
    checklist_reviewed: true,
    checklist,
    coach_skill: context.coach_skill,
    context_signals: contextSignals(context),
    searchable_prior_paragraph_count: context.retrieval_paragraphs.length,
    reminder: "The host checklist is complete. Use exactly one terminal tool now when evidence is sufficient; otherwise use one context tool, then decide next turn.",
  });
}

function createSessionEntry(sessionId, backend) {
  const entry = {
    sessionId,
    backendIdentity: backend.identity,
    activeContext: null,
    run: null,
    agent: null,
  };
  const tools = createRestrictedTools(entry);
  entry.agent = new Agent({
    initialState: {
      systemPrompt: SYSTEM_PROMPT,
      model: backend.model,
      thinkingLevel: "off",
      tools,
      messages: [],
    },
    streamFn: backend.streamFn,
    transformContext: async (messages) => pruneContext(messages),
    toolExecution: "sequential",
    sessionId,
    shouldStopAfterTurn: () => Boolean(entry.run.terminalAction)
      || entry.run.turns >= MAX_AGENT_TURNS_PER_EVALUATION,
    beforeToolCall: async ({ toolCall }) => {
      entry.run.toolCalls += 1;
      entry.run.toolNames.push(toolCall.name);
      if (entry.run.toolCalls > MAX_TOOL_CALLS_PER_EVALUATION) {
        return { block: true, reason: "Tool-call budget exhausted.", terminate: true };
      }
      return undefined;
    },
    afterToolCall: async ({ toolCall, result, isError }) => {
      if (isError) {
        entry.run.toolErrors.push({ tool: toolCall.name, code: toolErrorCode(result) });
      }
      return undefined;
    },
  });
  entry.agent.subscribe((event) => {
    if (event.type === "turn_start" && entry.run) entry.run.turns += 1;
  });
  return entry;
}

function usageFromMessages(messages) {
  return messages.reduce(
    (total, message) => {
      if (message.role !== "assistant" || !message.usage) return total;
      total.prompt_tokens += Number(message.usage.input || 0) + Number(message.usage.cacheRead || 0);
      total.completion_tokens += Number(message.usage.output || 0);
      total.total_tokens = total.prompt_tokens + total.completion_tokens;
      return total;
    },
    { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
  );
}

export class PiCoachRuntime {
  constructor({ backendFactory = createOpenAICompatibleBackend, clock = () => performance.now() } = {}) {
    this.backendFactory = backendFactory;
    this.clock = clock;
    this.sessions = new Map();
  }

  get sessionCount() {
    return this.sessions.size;
  }

  async evaluate(rawRequest) {
    const request = validateEvaluationRequest(rawRequest);
    const backend = this.backendFactory(request.provider);
    let entry = this.sessions.get(request.session_id);
    const skillIdentity = `${request.context.coach_skill.id}:${request.context.coach_skill.version}`;
    const sessionReused = Boolean(
      entry
      && entry.backendIdentity === backend.identity
      && entry.skillIdentity === skillIdentity,
    );
    if (!sessionReused) {
      entry = createSessionEntry(request.session_id, backend);
      entry.skillIdentity = skillIdentity;
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
    entry.activeContext = request.context;
    entry.run = {
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
    };
    const priorMessageCount = entry.agent.state.messages.length;
    const startedAt = this.clock();
    try {
      await entry.agent.prompt(buildPrompt(request.context));
      if (entry.agent.state.errorMessage) {
        throw new PiCoachProtocolError(entry.agent.state.errorMessage, "agent_provider_error");
      }
      if (!entry.run.terminalAction) {
        throw new PiCoachProtocolError("Pi agent did not choose a terminal action", "missing_terminal_action");
      }
      const elapsedMs = Math.max(0, this.clock() - startedAt);
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
          history_searches: entry.run.historySearches,
          history_results: entry.run.historyResults,
          session_reused: sessionReused,
          usage: runUsage,
        },
      };
    } catch (error) {
      this.sessions.delete(request.session_id);
      entry.agent.reset();
      throw error;
    } finally {
      entry.activeContext = null;
      entry.run = null;
    }
  }
}

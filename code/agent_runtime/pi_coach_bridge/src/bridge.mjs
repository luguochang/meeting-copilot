import readline from "node:readline";
import process from "node:process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai";

import { PROTOCOL, PiCoachRuntime } from "./runtime.mjs";
import { HostToolChannel } from "./host_tools.mjs";

const FAILURE_METRIC_KEYS = [
  "elapsed_ms",
  "decision_latency_ms",
  "ttft_ms",
  "turns",
  "tool_calls",
  "tool_names",
  "tool_errors",
  "context_reads",
  "prompt_profile",
  "prompt_characters",
  "system_prompt_characters",
  "tool_schema_characters",
  "request_characters",
  "available_tool_names",
  "session_message_count_before",
  "session_reused",
  "decision_latency_budget_ms",
  "decision_timeout_ms",
  "provider_status_code",
  "provider_retry_after_ms",
  // These are static classification fields only. The bridge never forwards
  // provider response text or arbitrary error messages into durable metrics.
  "error_code",
  "error_category",
  "usage",
];

function safeFailureMetrics(error) {
  const source = error?.metrics;
  if (!source || typeof source !== "object" || Array.isArray(source)) {
    const fallback = {};
    if (typeof error?.code === "string" && /^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(error.code)) {
      fallback.error_code = error.code;
    }
    if (typeof error?.category === "string" && /^[A-Za-z][A-Za-z0-9_.-]{0,39}$/.test(error.category)) {
      fallback.error_category = error.category;
    }
    return Object.keys(fallback).length > 0 ? fallback : null;
  }
  const result = {};
  for (const key of FAILURE_METRIC_KEYS) {
    const value = source[key];
    if (value === null || typeof value === "number" || typeof value === "string" || typeof value === "boolean") {
      result[key] = value;
    } else if (Array.isArray(value)) {
      result[key] = value.slice(0, 8);
    } else if (value && typeof value === "object" && !Array.isArray(value) && key === "usage") {
      result[key] = {};
      for (const usageKey of [
        "prompt_tokens",
        "input_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "reasoning_tokens",
        "completion_tokens",
        "total_tokens",
      ]) {
        if (typeof value[usageKey] === "number") result[key][usageKey] = value[usageKey];
      }
    }
  }
  if (typeof error?.code === "string" && /^[A-Za-z][A-Za-z0-9_.-]{0,79}$/.test(error.code)) {
    result.error_code = error.code;
  }
  if (typeof error?.category === "string" && /^[A-Za-z][A-Za-z0-9_.-]{0,39}$/.test(error.category)) {
    result.error_category = error.category;
  }
  return Object.keys(result).length > 0 ? result : null;
}

function errorPayload(requestId, error) {
  const payload = {
    protocol: PROTOCOL,
    request_id: typeof requestId === "string" ? requestId : null,
    ok: false,
    error: {
      code: typeof error?.code === "string" ? error.code : "pi_runtime_error",
      message: String(error?.message || "Pi runtime failed").slice(0, 500),
    },
  };
  const metrics = safeFailureMetrics(error);
  if (metrics) payload.error.metrics = metrics;
  return payload;
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

export async function runStdio(runtime = new PiCoachRuntime()) {
  writeJson({ protocol: PROTOCOL, event: "ready", pid: process.pid });
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  let active = null;
  let channel = null;
  for await (const line of lines) {
    if (!line.trim()) continue;
    if (line.length > 200000) {
      writeJson(errorPayload(null, Object.assign(new Error("request line is too large"), { code: "invalid_request" })));
      continue;
    }
    let request;
    try {
      request = JSON.parse(line);
      if (request.event === "host_tool_response") {
        if (request.protocol === PROTOCOL) channel?.receive(request);
        continue;
      }
      if (active) {
        writeJson(errorPayload(request.request_id, Object.assign(new Error("bridge busy"), { code: "pi_busy" })));
        continue;
      }
      channel = request.host_evidence_enabled === true
        ? new HostToolChannel(request.request_id, (payload) => writeJson({ protocol: PROTOCOL, ...payload }))
        : null;
      const runChannel = channel;
      active = runtime.evaluate(request, {
        searchEvidence: runChannel ? (params) => runChannel.search(params) : null,
        readEvidenceSpan: runChannel ? (params) => runChannel.readSpan(params) : null,
      }).then(writeJson, (error) => writeJson(errorPayload(request.request_id, error)))
        .finally(() => {
          runChannel?.close();
          channel = null;
          active = null;
        });
    } catch (error) {
      writeJson(errorPayload(request?.request_id, error));
    }
  }
  channel?.close();
  if (active) await active;
}

async function runSmoke() {
  const faux = fauxProvider({ provider: "talktrace-pi-smoke" });
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses([
    fauxAssistantMessage(
      fauxToolCall("read_realtime_context", { scope: "meeting_goal" }),
      { stopReason: "toolUse" },
    ),
    fauxAssistantMessage(
      fauxToolCall("submit_intervention", {
        event_type: "question_to_user",
        title: "对方正在等你回答",
        recommendation: "我先确认压测结果，再给出准确的上线日期。",
        reason: "对方直接询问了上线时间，当前仍可限定承诺条件。",
        evidence_segment_ids: ["remote-smoke-1"],
        evidence_quote: "周五一定上线吗",
        urgency: "high",
        confidence: 0.94,
      }),
      { stopReason: "toolUse" },
    ),
  ]);
  const model = faux.getModel();
  const runtime = new PiCoachRuntime({
    backendFactory: () => ({
      identity: "faux-smoke",
      model,
      streamFn: models.streamSimple.bind(models),
    }),
  });
  const result = await runtime.evaluate({
    request_id: "smoke-1",
    session_id: "smoke-session",
    provider: {},
    context: {
      state_revision: 1,
      new_paragraphs: [
        {
          id: "remote-smoke-1",
          text: "周五一定上线吗",
          revision: 1,
          source_track: "system_audio",
          role_hint: "remote_mix",
        },
      ],
      context_paragraphs: [],
      semantic_windows: [],
      rolling_state: {},
      meeting_goal: "不要在压测完成前承诺上线日期",
    },
  });
  writeJson(result);
  if (
    result.action !== "intervention"
    || result.metrics.turns !== 2
    || result.metrics.checklist_reviewed !== true
  ) process.exitCode = 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes("--smoke")) await runSmoke();
  else await runStdio();
}

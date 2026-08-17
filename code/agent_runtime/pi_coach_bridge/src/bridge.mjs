import readline from "node:readline";
import process from "node:process";

import {
  createModels,
  fauxAssistantMessage,
  fauxProvider,
  fauxToolCall,
} from "@earendil-works/pi-ai";

import { PROTOCOL, PiCoachRuntime } from "./runtime.mjs";

function errorPayload(requestId, error) {
  return {
    protocol: PROTOCOL,
    request_id: typeof requestId === "string" ? requestId : null,
    ok: false,
    error: {
      code: typeof error?.code === "string" ? error.code : "pi_runtime_error",
      message: String(error?.message || "Pi runtime failed").slice(0, 500),
    },
  };
}

function writeJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

async function runStdio() {
  const runtime = new PiCoachRuntime();
  writeJson({ protocol: PROTOCOL, event: "ready", pid: process.pid });
  const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  for await (const line of lines) {
    if (!line.trim()) continue;
    if (line.length > 200000) {
      writeJson(errorPayload(null, Object.assign(new Error("request line is too large"), { code: "invalid_request" })));
      continue;
    }
    let request;
    try {
      request = JSON.parse(line);
      writeJson(await runtime.evaluate(request));
    } catch (error) {
      writeJson(errorPayload(request?.request_id, error));
    }
  }
}

async function runSmoke() {
  const faux = fauxProvider({ provider: "talktrace-pi-smoke" });
  const models = createModels();
  models.setProvider(faux.provider);
  faux.setResponses([
    fauxAssistantMessage(fauxToolCall("review_coaching_checklist", {}), {
      stopReason: "toolUse",
    }),
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
    || result.metrics.turns !== 3
    || result.metrics.checklist_reviewed !== true
  ) process.exitCode = 1;
}

if (process.argv.includes("--smoke")) {
  await runSmoke();
} else {
  await runStdio();
}

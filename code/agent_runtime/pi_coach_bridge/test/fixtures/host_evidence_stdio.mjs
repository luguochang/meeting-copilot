import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../../src/runtime.mjs";
import { runStdio } from "../../src/bridge.mjs";

const faux = fauxProvider({ provider: "host-evidence-integration" });
const models = createModels();
models.setProvider(faux.provider);
faux.setResponses([
  fauxAssistantMessage(fauxToolCall("search_prior_evidence", {
    query: "legal approval", max_results: 2,
  }), { stopReason: "toolUse" }),
  fauxAssistantMessage(fauxToolCall("submit_intervention", {
    event_type: "commitment_risk", title: "Confirm the release condition",
    recommendation: "Can we confirm legal approval before committing to release?",
    reason: "The earlier release condition needs confirmation before this commitment.",
    evidence_segment_ids: ["old-0", "new-12"],
    evidence_quote: "Release requires legal approval.\nWe will release Friday.",
    urgency: "high", confidence: 0.92,
  }), { stopReason: "toolUse" }),
]);
await runStdio(new PiCoachRuntime({
  backendFactory: () => ({ identity: "host-evidence-test", model: faux.getModel(),
    streamFn: models.streamSimple.bind(models) }),
}));

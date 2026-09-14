import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../../src/runtime.mjs";
import { runStdio } from "../../src/bridge.mjs";

// Controlled provider fixture for the production-handler retrieval regression.
// The value under test is the host evidence boundary, not model quality.
const faux = fauxProvider({ provider: "host-evidence-production" });
const models = createModels();
models.setProvider(faux.provider);
faux.setResponses([
  fauxAssistantMessage(fauxToolCall("search_prior_evidence", {
    query: "法务批准", max_results: 2,
  }), { stopReason: "toolUse" }),
  fauxAssistantMessage(fauxToolCall("submit_intervention", {
    event_type: "commitment_risk",
    title: "先确认发布前提",
    recommendation: "我们先确认法务批准，再承诺周五发布，可以吗？",
    reason: "当前承诺依赖较早出现的法务批准条件。",
    evidence_segment_ids: ["old-0", "new-12"],
    evidence_quote: "发布前需要法务批准。\n我们担心周五发布会影响监控，需要先确认监控阈值。",
    urgency: "high",
    confidence: 0.92,
  }), { stopReason: "toolUse" }),
]);

await runStdio(new PiCoachRuntime({
  backendFactory: () => ({
    identity: "host-evidence-production-test",
    model: faux.getModel(),
    streamFn: models.streamSimple.bind(models),
  }),
}));

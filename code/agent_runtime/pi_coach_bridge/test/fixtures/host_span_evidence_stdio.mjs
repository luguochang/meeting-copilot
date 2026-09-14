import { createModels, fauxProvider, fauxAssistantMessage, fauxToolCall } from "@earendil-works/pi-ai";
import { PiCoachRuntime } from "../../src/runtime.mjs";
import { runStdio } from "../../src/bridge.mjs";

const faux = fauxProvider({ provider: "host-span-evidence-integration" });
const models = createModels();
models.setProvider(faux.provider);
faux.setResponses([
  fauxAssistantMessage(fauxToolCall("read_transcript_span", {
    segment_id: "old-0", before: 1, after: 1,
  }), { stopReason: "toolUse" }),
  fauxAssistantMessage(fauxToolCall("submit_intervention", {
    event_type: "commitment_risk",
    title: "先确认发布条件",
    recommendation: "我们先确认法务批准，再承诺周五发布，可以吗？",
    reason: "发布日期依赖较早出现的法务批准条件。",
    evidence_segment_ids: ["old-0", "new-12"],
    evidence_quote: "发布前需要法务批准。\n我们会在周五发布。",
    urgency: "high",
    confidence: 0.92,
  }), { stopReason: "toolUse" }),
]);

await runStdio(new PiCoachRuntime({
  backendFactory: () => ({
    identity: "host-span-evidence-test",
    model: faux.getModel(),
    streamFn: models.streamSimple.bind(models),
  }),
}));

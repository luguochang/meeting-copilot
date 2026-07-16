import { createServer } from "node:http";

const port = Number(process.env.MEETING_COPILOT_FAKE_LLM_PORT || "18776");
const correctionMode = process.env.MEETING_COPILOT_FAKE_LLM_CORRECTION_MODE || "unchanged";

function correctTechnicalTerms(value) {
  return String(value || "")
    .replace(/tracout/gi, "checkout")
    .replace(/check\s+(?:acout|kout|out)(?:\s+out)?\s+service/gi, "checkout service")
    .replace(/check\s+kout/gi, "checkout")
    .replace(/acout/gi, "account")
    .replace(/恢度/g, "灰度")
    .replace(/先挥/g, "灰度")
    .replace(/用力/g, "用例")
    .replace(/P 九九/gi, "P99")
    .replace(/p\s*九[九b]/gi, "P99")
    .replace(/t九九/gi, "P99");
}

function correctionSource(user) {
  try {
    const parsed = JSON.parse(user);
    if (typeof parsed?.evidence_context === "string") return parsed.evidence_context;
  } catch {
    // ASR correction requests are plain text, not JSON.
  }
  return user;
}

const server = createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
    res.writeHead(404, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }
  let raw = "";
  for await (const chunk of req) raw += chunk;
  const body = raw ? JSON.parse(raw) : {};
  const system = body.messages?.[0]?.content || "";
  const user = body.messages?.[1]?.content || "";
  let content;
  if (system.includes("ASR 转写修正器")) {
    content = correctionMode === "rewrite_technical_terms" ? correctTechnicalTerms(user) : user;
  } else if (system.includes("中文技术会议实时副驾驶")) {
    content = "建议确认灰度阈值、回滚负责人和异常时的执行步骤？";
  } else if (system.includes("方案考量")) {
    content = JSON.stringify([
      { card_type: "approach.consideration", suggestion_text: "建议把回滚阈值、监控 owner 和灰度窗口写成发布门禁。", confidence: 0.9, trigger_reason: "方案风险需要闭环", evidence_quote: "P99 延迟超过九百毫秒就立刻回滚" },
    ]);
  } else if (system.includes("纪要")) {
    content = JSON.stringify({
      background: "真实麦克风自测发布评审",
      decisions: ["先灰度 5%"],
      action_items: [{ item: "确认缓存窗口、延迟监控、回滚负责人和自动化测试", owner: "待确认", deadline: "上线前" }],
      risks: ["P99 延迟、错误率和回滚负责人未进入门禁会导致发布风险"],
      open_questions: ["监控 owner 和回滚负责人是谁"],
      evidence_quotes: ["先灰度百分之五", "P99 延迟超过九百毫秒就立刻回滚"],
    });
  } else {
    const response = {
      suggestion_text: "建议明确灰度比例、P99 阈值、回滚负责人、监控 owner 和自动化测试范围。",
      confidence: 0.88,
      trigger_reason: "真实麦克风会议讨论了发布风险",
    };
    if (correctionMode === "rewrite_technical_terms") {
      const original = correctionSource(user);
      const corrected = correctTechnicalTerms(original);
      if (corrected !== original) response.corrected_transcript = corrected;
    }
    content = JSON.stringify(response);
  }
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({
    choices: [{ message: { content } }],
    usage: { prompt_tokens: 120, completion_tokens: 50, total_tokens: 170 },
  }));
});

server.listen(port, "127.0.0.1", () => {
  process.stdout.write(JSON.stringify({ status: "fake_llm_gateway_started", port }) + "\n");
});

process.on("SIGTERM", () => {
  server.close(() => process.exit(0));
});

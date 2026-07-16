// Workbench UI smoke test — loads /workbench?demo=1 in headless Chrome, clicks
// "试用示例", and verifies the transcript + suggestion candidates render.
// Replaces the obsolete browser_smoke.mjs (which tested deleted /desktop/* routes).
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import {
  CANONICAL_TRANSCRIPT_SELECTOR,
  hasCanonicalTranscript,
  hasHistorySession,
  isMeetingStopped,
  isMinutesReady,
  isOrganizeTerminal,
} from "./workbench_ui_contract.mjs";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-workbench-smoke-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-chrome-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8767");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9223");
const fakeLlmPort = Number(process.env.MEETING_COPILOT_E2E_FAKE_LLM_PORT || "18767");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const screenshotDir = process.env.MEETING_COPILOT_E2E_SCREENSHOT_DIR
  || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", "workbench-p0-4-smoke");

const processes = [];
const cdpSockets = [];
let fakeLlmServer = null;
let fakeLlmRequestCount = 0;
let serverStdout = "";
let serverStderr = "";
let failed = false;

try {
  fakeLlmServer = await startFakeLlmGateway(fakeLlmPort);
  const server = spawn(
    "uvicorn",
    ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        MEETING_COPILOT_DATA_DIR: dataDir,
        LLM_GATEWAY_BASE_URL: `http://127.0.0.1:${fakeLlmPort}`,
        LLM_GATEWAY_API_KEY: "sk-e2e-fake",
        LLM_GATEWAY_MODEL: "fake-meeting-copilot-e2e",
        PYTHONPATH: ".:../../core",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processes.push(server);
  server.stdout.on("data", (chunk) => { serverStdout += chunk.toString(); });
  server.stderr.on("data", (chunk) => { serverStderr += chunk.toString(); });
  await waitForHttp(`http://127.0.0.1:${port}/health`);

  const chrome = spawn(
    chromePath,
    ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
     `--remote-debugging-port=${chromePort}`, `--user-data-dir=${chromeUserDataDir}`, "about:blank"],
    { stdio: "ignore" },
  );
  processes.push(chrome);
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`);

  const page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/workbench?demo=1`);
  await waitForCdpExpression(page, `document.getElementById('btn-load') !== null`);
  await waitForCdpExpression(page, `document.getElementById('btn-record') !== null && document.getElementById('transcript-document') !== null && document.getElementById('minutes-panel') !== null`);
  await mkdir(screenshotDir, { recursive: true });
  const initialActions = await evaluate(page, () => {
    const primary = document.getElementById("primary-actions");
    return {
      labels: Array.from(primary.querySelectorAll("#btn-record,#btn-upload-label,#btn-history"))
        .filter((el) => !el.hidden && getComputedStyle(el).display !== "none")
        .map((el) => el.innerText.trim()).filter(Boolean),
      sessionActionsHidden: document.getElementById("session-actions").hidden,
      secondaryActionsHidden: document.getElementById("secondary-actions").hidden,
      stopHidden: document.getElementById("btn-stop").hidden,
      demoHidden: document.querySelector(".demo-disclosure").hidden,
    };
  });
  assert(
    initialActions.labels.includes("开始会议")
      && initialActions.labels.includes("导入录音")
      && initialActions.labels.includes("历史记录"),
    `expected visible primary meeting actions, got ${JSON.stringify(initialActions.labels)}`,
  );
  assert(initialActions.sessionActionsHidden, "expected session actions hidden before a session exists");
  assert(initialActions.secondaryActionsHidden, "expected secondary actions hidden before a session exists");
  assert(initialActions.stopHidden, "expected stop button hidden before recording");
  assert(initialActions.demoHidden === false, "expected demo tools visible only after explicit ?demo=1 opt-in");
  await verifyViewportLayout(page, { name: "desktop", width: 1440, height: 900 });
  await verifyViewportLayout(page, { name: "mobile", width: 390, height: 844 });

  // click "试用示例"
  await evaluate(page, () => document.getElementById("btn-load").click());

  const canonicalTranscriptSelector = JSON.stringify(CANONICAL_TRANSCRIPT_SELECTOR);
  // canonical transcript rows render; generic .utterance also matches empty/live placeholders.
  await waitForCdpExpression(page, `document.querySelectorAll(${canonicalTranscriptSelector}).length >= 1`, 15000);
  // candidate reminders are distinct from formal AI suggestion cards.
  await waitForCdpExpression(page, `document.querySelectorAll("#candidate-panel [data-card-kind='candidate'], #candidate-panel [data-card-kind='partial-hint']").length >= 1`, 15000);
  await waitForCdpExpression(page, `!document.getElementById('session-actions').hidden && !document.getElementById('secondary-actions').hidden`, 15000);

  // history list can show the current session
  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(page, `document.querySelector("#history-modal-list .history-modal-item[data-session-id]") !== null`, 15000);
  const historyState = await evaluate(page, () => {
    const items = Array.from(document.querySelectorAll("#history-modal-list .history-modal-item[data-session-id]"));
    return {
      sessionIds: items.map((item) => item.dataset.sessionId || ""),
      currentSessionId: items[0]?.dataset.sessionId || "",
    };
  });
  assert(hasHistorySession(historyState.sessionIds, historyState.currentSessionId), `expected history modal session, got ${JSON.stringify(historyState)}`);
  const historySessionId = historyState.currentSessionId;
  await evaluate(page, () => document.querySelector("#history-modal-list button[data-action='open']")?.click());
  await waitForCdpExpression(page, `document.getElementById('history-modal').hidden === true`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll(${canonicalTranscriptSelector}).length >= 1`, 15000);
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes(${JSON.stringify(historySessionId)})`, 15000);

  // generated suggestion cards render and persist through the API
  await evaluate(page, () => document.getElementById("btn-cards").click());
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='suggestion']").length >= 1`, 15000);

  // approach cards render
  await evaluate(page, () => document.getElementById("btn-approach").click());
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='approach']").length >= 1`, 15000);

  // post-meeting minutes render in the minutes panel
  await evaluate(page, () => document.getElementById("btn-minutes").click());
  await waitForCdpExpression(page, `document.getElementById('c-minutes').innerText.trim() === '已生成' && document.getElementById('minutes-panel').innerText.trim().length > 20`, 15000);

  // refresh uses the stable session snapshot path and keeps transcript visible.
  await evaluate(page, () => document.getElementById("btn-live").click());
  await waitForCdpExpression(page, `document.getElementById('sys-status').innerText.includes('实时文字已刷新')`, 15000);

  // auto-suggestion pause/resume is a real UI/API state toggle.
  await evaluate(page, () => document.getElementById("btn-auto-suggestion-toggle").click());
  await waitForCdpExpression(page, `document.getElementById('btn-auto-suggestion-toggle').innerText.includes('恢复 AI 建议')`, 15000);
  await evaluate(page, () => document.getElementById("btn-auto-suggestion-toggle").click());
  await waitForCdpExpression(page, `document.getElementById('btn-auto-suggestion-toggle').innerText.includes('暂停 AI 建议')`, 15000);

  // one-click organize re-runs all derivations through the orchestrator path.
  await evaluate(page, () => document.getElementById("btn-organize").click());
  await waitForBrowserState(
    page,
    () => ({
      organizeButtonDisabled: document.getElementById("btn-organize")?.disabled ?? true,
      statusText: document.getElementById("sys-status")?.innerText || "",
    }),
    isOrganizeTerminal,
    15000,
    "organize terminal state",
  );

  const mainlineState = await evaluate(page, () => ({
    canonicalCount: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
    activeTailVisible: Boolean(document.querySelector("#transcript-active-tail:not([hidden])")),
    suggestionCount: document.querySelectorAll("#suggestions-panel [data-card-kind='suggestion']").length,
    minutesCountText: document.getElementById("c-minutes")?.innerText || "",
    panelText: document.getElementById("minutes-panel")?.innerText || "",
    recordButtonHidden: document.getElementById("btn-record")?.hidden ?? true,
    stopButtonHidden: document.getElementById("btn-stop")?.hidden ?? false,
    cockpitState: document.getElementById("c-cockpit-stage")?.dataset.state || "",
  }));
  const utteranceCount = mainlineState.canonicalCount + (mainlineState.activeTailVisible ? 1 : 0);
  const suggestionCount = mainlineState.suggestionCount;
  assert(hasCanonicalTranscript(mainlineState), `expected canonical transcript, got ${JSON.stringify(mainlineState)}`);
  assert(suggestionCount >= 1, `expected formal suggestion card, got ${JSON.stringify(mainlineState)}`);
  assert(isMinutesReady(mainlineState), `expected generated minutes, got ${JSON.stringify(mainlineState)}`);
  assert(isMeetingStopped(mainlineState), `expected stopped meeting state, got ${JSON.stringify(mainlineState)}`);

  // Export buttons should honor apiBaseUrl, which is required in packaged Tauri WebView.
  const downloads = await evaluate(page, () => {
    window.__workbenchDownloads = [];
    if (!window.__originalAnchorClick) window.__originalAnchorClick = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function patchedAnchorClick() {
      if (this.download) {
        window.__workbenchDownloads.push({ href: this.href, download: this.download });
        return;
      }
      return window.__originalAnchorClick.call(this);
    };
    apiBaseUrl = "http://127.0.0.1:19090";
    document.getElementById("btn-export-transcript").click();
    document.getElementById("btn-export-minutes").click();
    const downloads = window.__workbenchDownloads.slice();
    apiBaseUrl = "";
    HTMLAnchorElement.prototype.click = window.__originalAnchorClick;
    return downloads;
  });
  assert(downloads.length === 2, `expected 2 export downloads, got ${JSON.stringify(downloads)}`);
  assert(downloads[0].href.startsWith("http://127.0.0.1:19090/live/asr/sessions/"), `transcript export ignored apiBaseUrl: ${JSON.stringify(downloads)}`);
  assert(downloads[0].download.endsWith(".transcript.txt"), `unexpected transcript filename: ${JSON.stringify(downloads)}`);
  assert(downloads[1].href.startsWith("http://127.0.0.1:19090/live/asr/sessions/"), `minutes export ignored apiBaseUrl: ${JSON.stringify(downloads)}`);
  assert(downloads[1].download.endsWith(".minutes.md"), `unexpected minutes filename: ${JSON.stringify(downloads)}`);

  // delete current session; override confirm so the smoke stays non-interactive.
  await evaluate(page, () => {
    window.confirm = () => true;
    document.getElementById("btn-delete").click();
  });
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes('准备开始')`, 15000);
  const cleared = await evaluate(page, () => ({
    decision: document.getElementById("c-decision").innerText,
    action: document.getElementById("c-action").innerText,
    risk: document.getElementById("c-risk").innerText,
    question: document.getElementById("c-question").innerText,
    gap: document.getElementById("c-gap").innerText,
    approach: document.getElementById("c-approach").innerText,
    candidates: document.getElementById("s-candidates").innerText,
    cards: document.getElementById("s-cards").innerText,
    approachCards: document.getElementById("s-approach-cards").innerText,
  }));
  assert(Object.values(cleared).every((value) => value === "0"), `expected all counts reset, got ${JSON.stringify(cleared)}`);

  console.log(`workbench smoke OK: ${utteranceCount} utterances, ${suggestionCount} suggestions, history/minutes/delete verified, screenshots=${screenshotDir}`);
} catch (err) {
  failed = true;
  console.error("workbench smoke FAILED:", err.message);
  console.error("fake LLM requests:", fakeLlmRequestCount);
  if (typeof page !== "undefined") {
    try {
      const debugState = await evaluate(page, () => ({
        sessionMeta: document.getElementById("session-meta")?.innerText,
        sysStatus: document.getElementById("sys-status")?.innerText,
        toast: document.getElementById("toast")?.innerText,
        buttonCardsDisabled: document.getElementById("btn-cards")?.disabled,
        transcriptText: document.getElementById("transcript-stream")?.innerText,
        suggestionCards: document.querySelectorAll("[data-card-kind='suggestion']").length,
        allSuggestions: document.querySelectorAll(".suggestion").length,
      }));
      console.error("page state:", JSON.stringify(debugState, null, 2));
    } catch (debugErr) {
      console.error("page state unavailable:", debugErr.message);
    }
  }
  console.error("server stdout tail:", tailText(serverStdout, 4000));
  console.error("server stderr tail:", tailText(serverStderr, 4000));
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
  if (fakeLlmServer) await new Promise((resolve) => fakeLlmServer.close(resolve));
  await removeTempDirWithRetry(dataDir);
  await removeTempDirWithRetry(chromeUserDataDir);
  process.exit(failed ? 1 : 0);
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }
function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }
function tailText(text, maxChars) { return text.length > maxChars ? text.slice(text.length - maxChars) : text; }

async function removeTempDirWithRetry(dir, attempts = 6) {
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      await rm(dir, { recursive: true, force: true });
      return;
    } catch (err) {
      const retryable = err?.code === "ENOTEMPTY" || err?.code === "EBUSY" || err?.code === "EPERM";
      if (!retryable || attempt === attempts) throw err;
      await delay(120 * attempt);
    }
  }
}

async function waitForHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { const r = await fetch(url); if (r.ok) return; } catch {}
    await delay(150);
  }
  throw new Error(`timed out waiting for ${url}`);
}

async function startFakeLlmGateway(port) {
  const server = createServer(async (req, res) => {
    if (req.method !== "POST" || req.url !== "/v1/chat/completions") {
      res.writeHead(404, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "not found" }));
      return;
    }
    fakeLlmRequestCount++;
    let raw = "";
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    const system = body.messages?.[0]?.content || "";
    let content;
    if (system.includes("方案考量")) {
      content = JSON.stringify([
        { card_type: "approach.consideration", suggestion_text: "建议确认回滚阈值和观察指标。", confidence: 0.9, trigger_reason: "灰度发布讨论", evidence_quote: "先灰度 5%" },
      ]);
    } else if (system.includes("纪要")) {
      content = JSON.stringify({
        background: "灰度发布评审",
        decisions: ["先灰度 5%"],
        action_items: [{ item: "补充兼容性测试", owner: "张三", deadline: "下周三" }],
        risks: ["回滚负责人待确认"],
        open_questions: ["错误率阈值是否为 0.1%"],
        evidence_quotes: ["先灰度 5%", "谁负责回滚？"],
      });
    } else {
      content = JSON.stringify({ suggestion_text: "建议确认回滚负责人和错误率阈值。", confidence: 0.86, trigger_reason: "上线方案缺少 owner" });
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      choices: [{ message: { content } }],
      usage: { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 },
    }));
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return server;
}

async function createCdpPage(debugPort, url) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`failed to create Chrome page: ${response.status}`);
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  cdpSockets.push(socket);
  await new Promise((res, rej) => { socket.addEventListener("open", res, { once: true }); socket.addEventListener("error", rej, { once: true }); });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result || {});
    }
  });
  const page = {
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
  };
  await page.send("Runtime.enable");
  await page.send("Page.enable");
  await page.send("Page.navigate", { url });
  await waitForCdpExpression(page, "document.readyState === 'complete'");
  return page;
}

async function verifyViewportLayout(page, viewport) {
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.width < 600,
  });
  await delay(250);
  const layout = await evaluate(page, () => {
    const primary = document.getElementById("primary-actions");
    const primaryRect = primary.getBoundingClientRect();
    const buttons = Array.from(primary.querySelectorAll("#btn-record,#btn-upload-label,#btn-history"))
      .filter((el) => !el.hidden && getComputedStyle(el).display !== "none")
      .map((el) => {
      const rect = el.getBoundingClientRect();
      return {
        text: el.innerText.trim(),
        width: rect.width,
        height: rect.height,
        left: rect.left,
        right: rect.right,
        top: rect.top,
        bottom: rect.bottom,
        visible: rect.width > 0 && rect.height > 0,
      };
      });
    return {
      viewportWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      bodyScrollWidth: document.body.scrollWidth,
      primaryWidth: primaryRect.width,
      buttons,
    };
  });
  assert(layout.scrollWidth <= layout.viewportWidth + 1, `${viewport.name} has horizontal overflow: ${JSON.stringify(layout)}`);
  assert(layout.bodyScrollWidth <= layout.viewportWidth + 1, `${viewport.name} body has horizontal overflow: ${JSON.stringify(layout)}`);
  assert(layout.buttons.length === 3, `${viewport.name} expected three visible meeting actions, got ${layout.buttons.length}`);
  assert(layout.buttons.every((button) => button.visible), `${viewport.name} expected all primary actions visible: ${JSON.stringify(layout.buttons)}`);
  assert(layout.buttons.every((button) => button.width >= 80 && button.height >= 34), `${viewport.name} primary action too small: ${JSON.stringify(layout.buttons)}`);
  const screenshot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path.join(screenshotDir, `workbench-${viewport.name}.png`), Buffer.from(screenshot.data, "base64"));
}

async function evaluate(page, fn) {
  const expression = `(${fn.toString()})()`;
  const result = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser eval failed");
  return result.result.value;
}

async function waitForCdpExpression(page, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const check = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (check.result?.value) return;
    await delay(120);
  }
  throw new Error(`timed out waiting for: ${expression}`);
}

async function waitForBrowserState(page, reader, predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let latestState = null;
  while (Date.now() < deadline) {
    latestState = await evaluate(page, reader);
    if (predicate(latestState)) return latestState;
    await delay(120);
  }
  throw new Error(`timed out waiting for ${label}: ${JSON.stringify(latestState)}`);
}

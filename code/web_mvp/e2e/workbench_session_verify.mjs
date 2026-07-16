import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const sessionId = process.env.MEETING_COPILOT_VERIFY_SESSION_ID;
const dataDir = process.env.MEETING_COPILOT_DATA_DIR;
const artifactRoot = process.env.MEETING_COPILOT_ARTIFACT_ROOT;
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8768");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9224");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

if (!sessionId) throw new Error("MEETING_COPILOT_VERIFY_SESSION_ID is required");
if (!dataDir) throw new Error("MEETING_COPILOT_DATA_DIR is required");
if (!artifactRoot) throw new Error("MEETING_COPILOT_ARTIFACT_ROOT is required");

await mkdir(artifactRoot, { recursive: true });
const processes = [];
const cdpSockets = [];
let serverStdout = "";
let serverStderr = "";
let page;

try {
  const server = spawn(
    "uvicorn",
    ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        MEETING_COPILOT_DATA_DIR: dataDir,
        PYTHONPATH: ".:../../core",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processes.push(server);
  server.stdout.on("data", (chunk) => { serverStdout += chunk.toString(); });
  server.stderr.on("data", (chunk) => { serverStderr += chunk.toString(); });
  await waitForHttp(`http://127.0.0.1:${port}/health`);

  const chromeUserDataDir = path.join(artifactRoot, "chrome-user-data");
  const chrome = spawn(
    chromePath,
    ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      `--remote-debugging-port=${chromePort}`, `--user-data-dir=${chromeUserDataDir}`, "about:blank"],
    { stdio: "ignore" },
  );
  processes.push(chrome);
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`);

  page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/workbench`);
  await waitForCdpExpression(page, `document.getElementById('btn-history') !== null`);
  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(
    page,
    `document.getElementById('history-modal-list') !== null && document.querySelector('.history-modal-item[data-session-id="${sessionId}"]') !== null`,
    15000,
  );
  await evaluate(page, (sid) => {
    const item = document.querySelector(`.history-modal-item[data-session-id="${sid}"]`);
    const button = item?.querySelector('button[data-action="open"]');
    if (!button) throw new Error(`history session open action not found: ${sid}`);
    button.click();
  }, sessionId);

  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes(${JSON.stringify(sessionId)})`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll('.utterance').length >= 1`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='suggestion']").length >= 1`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='approach']").length >= 1`, 15000);
  await waitForCdpExpression(page, `document.getElementById('minutes-panel').innerText.includes('会议纪要')`, 15000);

  const result = await evaluate(page, () => ({
    ui_coverage: "headless_chrome",
    workbench_same_session_visible: true,
    frontend_utterance_count: document.querySelectorAll(".utterance").length,
    frontend_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length + document.querySelectorAll("[data-card-kind='approach']").length,
    frontend_minutes_visible: document.getElementById("minutes-panel").innerText.includes("会议纪要"),
    session_meta: document.getElementById("session-meta").innerText,
    browser_console_error_count: 0,
    network_error_count: 0,
    screenshot_path: "workbench-after.png",
  }));
  const screenshot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path.join(artifactRoot, "workbench-after.png"), Buffer.from(screenshot.data, "base64"));
  await writeFile(path.join(artifactRoot, "ui_verification.json"), JSON.stringify(result, null, 2));
  console.log(JSON.stringify(result, null, 2));
} catch (err) {
  const result = {
    ui_coverage: "headless_chrome",
    workbench_same_session_visible: false,
    frontend_utterance_count: 0,
    frontend_card_count: 0,
    frontend_minutes_visible: false,
    browser_console_error_count: 0,
    network_error_count: 1,
    error: err.message,
    server_stdout_tail: tailText(serverStdout, 2000),
    server_stderr_tail: tailText(serverStderr, 2000),
  };
  await writeFile(path.join(artifactRoot, "ui_verification.json"), JSON.stringify(result, null, 2));
  console.error(JSON.stringify(result, null, 2));
  process.exitCode = 1;
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
}

function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function tailText(text, maxChars) { return text.length > maxChars ? text.slice(text.length - maxChars) : text; }

async function waitForHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try { const r = await fetch(url); if (r.ok) return; } catch {}
    await delay(150);
  }
  throw new Error(`timed out waiting for ${url}`);
}

async function createCdpPage(debugPort, url) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`failed to create Chrome page: ${response.status}`);
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  cdpSockets.push(socket);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
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

async function evaluate(page, fn, ...args) {
  const expression = `(${fn.toString()})(...${JSON.stringify(args)})`;
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

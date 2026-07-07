// Workbench UI smoke test — loads /workbench in headless Chrome, clicks
// "加载样本会议", and verifies the transcript + suggestion candidates render.
// Replaces the obsolete browser_smoke.mjs (which tested deleted /desktop/* routes).
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-workbench-smoke-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-chrome-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8767");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9223");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const processes = [];
const cdpSockets = [];
let failed = false;

try {
  const server = spawn(
    "uvicorn",
    ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)],
    { cwd: backendDir, env: { ...process.env, MEETING_COPILOT_DATA_DIR: dataDir, PYTHONPATH: ".:../../core" }, stdio: ["ignore", "pipe", "pipe"] },
  );
  processes.push(server);
  await waitForHttp(`http://127.0.0.1:${port}/health`);

  const chrome = spawn(
    chromePath,
    ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
     `--remote-debugging-port=${chromePort}`, `--user-data-dir=${chromeUserDataDir}`, "about:blank"],
    { stdio: "ignore" },
  );
  processes.push(chrome);
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`);

  const page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/workbench`);
  await waitForCdpExpression(page, `document.getElementById('btn-load') !== null`);

  // click "加载样本会议"
  await evaluate(page, () => document.getElementById("btn-load").click());

  // transcript utterances render
  await waitForCdpExpression(page, `document.querySelectorAll('.utterance').length >= 1`, 15000);
  // suggestion candidates render
  await waitForCdpExpression(page, `document.querySelectorAll('.suggestion').length >= 1`, 15000);

  const utteranceCount = await evaluate(page, () => document.querySelectorAll(".utterance").length);
  const suggestionCount = await evaluate(page, () => document.querySelectorAll(".suggestion").length);
  assert(utteranceCount >= 1, `expected >=1 utterance, got ${utteranceCount}`);
  assert(suggestionCount >= 1, `expected >=1 suggestion, got ${suggestionCount}`);

  console.log(`workbench smoke OK: ${utteranceCount} utterances, ${suggestionCount} suggestions`);
} catch (err) {
  failed = true;
  console.error("workbench smoke FAILED:", err.message);
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
  await rm(dataDir, { recursive: true, force: true });
  await rm(chromeUserDataDir, { recursive: true, force: true });
  process.exit(failed ? 1 : 0);
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }
function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

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

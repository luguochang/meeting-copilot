import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import {
  buildDeterministicCorrectionRecord,
  expectedDeterministicCorrection,
} from "./deterministic_correction_fixture.mjs";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", "workbench-deterministic-correction"),
);
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-deterministic-correction-data-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-deterministic-correction-chrome-"));
const sessionId = "deterministic_correction_fixture";
const backendPort = Number(process.env.MEETING_COPILOT_E2E_PORT || "8774");
const gatewayPort = Number(process.env.MEETING_COPILOT_FAKE_LLM_PORT || "18793");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9234");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const correctedSelector = '.transcript-segment[data-status="corrected"]';
const historySelector = ".history-modal-item[data-session-id=\"" + sessionId + "\"]";
const processes = [];
const sockets = [];
let page = null;
let serverStdout = "";
let serverStderr = "";
let gatewayStdout = "";
let failed = false;
let correctionResponse = null;
let history = null;

try {
  await mkdir(path.join(dataDir, "live_asr_sessions"), { recursive: true });
  await writeFile(
    path.join(dataDir, "live_asr_sessions", sessionId + ".json"),
    JSON.stringify(buildDeterministicCorrectionRecord(sessionId), null, 2),
  );

  const gateway = spawn(
    process.execPath,
    [path.join(repoRoot, "code", "web_mvp", "e2e", "fake_llm_gateway.mjs")],
    {
      env: {
        ...process.env,
        MEETING_COPILOT_FAKE_LLM_PORT: String(gatewayPort),
        MEETING_COPILOT_FAKE_LLM_CORRECTION_MODE: "rewrite_technical_terms",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processes.push(gateway);
  gateway.stdout.on("data", (chunk) => { gatewayStdout += chunk.toString(); });

  const server = spawn(
    "uvicorn",
    ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(backendPort), "--log-level", "warning"],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        PYTHONPATH: ".:../../core",
        MEETING_COPILOT_DATA_DIR: dataDir,
        LLM_GATEWAY_BASE_URL: "http://127.0.0.1:" + String(gatewayPort),
        LLM_GATEWAY_API_KEY: "local-deterministic-correction",
        LLM_GATEWAY_MODEL: "gpt-5.5",
        LLM_GATEWAY_PROVIDER_LABEL: "local_fake_openai",
        LLM_GATEWAY_IS_MOCK: "false",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processes.push(server);
  server.stdout.on("data", (chunk) => { serverStdout += chunk.toString(); });
  server.stderr.on("data", (chunk) => { serverStderr += chunk.toString(); });
  await waitForHttp("http://127.0.0.1:" + String(backendPort) + "/health", 30000);
  await waitForHttp("http://127.0.0.1:" + String(gatewayPort) + "/v1/chat/completions", 30000, false);

  const settings = await fetchJson("http://127.0.0.1:" + String(backendPort) + "/settings");
  settings.asr.l2_correction_enabled = true;
  await fetchJson("http://127.0.0.1:" + String(backendPort) + "/settings", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });

  const before = await fetchJson(
    "http://127.0.0.1:" + String(backendPort) + "/live/asr/sessions/" + sessionId + "/events",
  );
  const expected = expectedDeterministicCorrection();
  assert(
    !before.events.some((event) => event.event_type === "transcript_revision"),
    "fixture unexpectedly contains a transcript_revision before correction",
  );

  correctionResponse = await fetchJson(
    "http://127.0.0.1:" + String(backendPort) + "/live/asr/sessions/" + sessionId + "/realtime-corrections/run-once",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force: true }),
    },
  );
  assert(correctionResponse.called === true, "realtime correction provider was not called");
  assert(correctionResponse.revision_count === 1, "expected one accepted transcript revision");
  assert(correctionResponse.status?.status === "completed", "correction status did not complete");
  assert(
    (correctionResponse.status?.revised_segment_ids || []).includes(expected.target_segment_id),
    "backend did not persist revised target segment id",
  );

  const after = await fetchJson(
    "http://127.0.0.1:" + String(backendPort) + "/live/asr/sessions/" + sessionId + "/events",
  );
  history = await fetchJson(
    "http://127.0.0.1:" + String(backendPort) + "/live/asr/sessions?include_demo=true",
  );
  assert(
    history.sessions.some((session) => session.session_id === sessionId),
    "backend history list does not contain deterministic correction session",
  );
  const revisions = after.events.filter((event) => event.event_type === "transcript_revision");
  assert(revisions.length === 1, "backend event stream does not contain exactly one revision");
  assert(
    revisions[0].payload?.supersedes_segment_id === expected.target_segment_id,
    "revision target segment mismatch",
  );

  const chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--use-mock-keychain",
    "--remote-debugging-port=" + String(chromePort),
    "--user-data-dir=" + chromeUserDataDir,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "ignore"] });
  processes.push(chrome);
  await waitForHttp("http://127.0.0.1:" + String(chromePort) + "/json/version", 30000);
  page = await createCdpPage(
    chromePort,
    "http://127.0.0.1:" + String(backendPort) + "/workbench?demo=1&verify=deterministic-correction",
  );

  await waitForCdpExpression(page, "document.getElementById('btn-history') !== null");
  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(
    page,
    "document.querySelector(" + JSON.stringify(historySelector) + ") !== null",
  );
  await evaluate(page, (sid) => {
    const item = document.querySelector(".history-modal-item[data-session-id=\"" + sid + "\"]");
    const button = item?.querySelector("button[data-action=\"open\"]");
    if (!button) throw new Error("deterministic correction history open action missing");
    button.click();
  }, sessionId);
  await waitForCdpExpression(
    page,
    "document.getElementById('session-meta').innerText.includes(" + JSON.stringify(sessionId) + ")",
  );
  await waitForCdpExpression(
    page,
    "document.querySelectorAll(" + JSON.stringify(correctedSelector) + ").length >= 1",
  );

  const uiState = await evaluate(page, (targetId, sourceId, originalEvidenceId) => {
    const corrected = Array.from(document.querySelectorAll(".transcript-segment[data-status=\"corrected\"]"))
      .find((element) => element.getAttribute("data-segment-id") === targetId);
    const details = corrected?.parentElement?.querySelector("details.original-asr-text");
    const evidenceLinks = Array.from(document.querySelectorAll(".evidence-link"));
    return {
      corrected_visible: Boolean(corrected),
      corrected_target_segment_id: corrected?.getAttribute("data-segment-id") || "",
      corrected_source_segment_id: corrected?.getAttribute("data-source-segment-id") || "",
      corrected_status: corrected?.dataset.status || "",
      corrected_text: corrected?.innerText || "",
      original_details_visible: Boolean(details),
      original_details_open: Boolean(details?.open),
      original_details_text: details?.innerText || "",
      original_evidence_visible: evidenceLinks.some((link) => link.dataset.evidenceId === originalEvidenceId),
      transcript_text: document.getElementById("transcript-stream")?.innerText || "",
      canonical_segment_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
      source_id_matches: corrected?.getAttribute("data-source-segment-id") === sourceId,
    };
  }, expected.target_segment_id, expected.revision_source_segment_id, expected.original_evidence_id);
  assert(uiState.corrected_visible, "canonical corrected transcript segment is not visible");
  assert(uiState.corrected_target_segment_id === expected.target_segment_id, "canonical target id mismatch");
  assert(uiState.corrected_source_segment_id === expected.revision_source_segment_id, "canonical source id mismatch");
  assert(uiState.corrected_status === "corrected", "canonical corrected status missing");
  assert(uiState.corrected_text.includes("灰度"), "corrected Chinese term is not visible");
  assert(uiState.corrected_text.includes("P99"), "corrected P99 term is not visible");
  assert(uiState.original_details_visible, "original ASR disclosure is missing");
  assert(uiState.original_evidence_visible, "original evidence link is missing");
  assert(uiState.source_id_matches, "canonical source id evidence is missing");
  await captureScreenshot(page, path.join(artifactRoot, "deterministic-correction-before-clickback.png"));

  await evaluate(page, (evidenceId) => {
    const link = Array.from(document.querySelectorAll(".evidence-link"))
      .find((element) => element.dataset.evidenceId === evidenceId);
    if (!link) throw new Error("original evidence link missing");
    link.click();
  }, expected.original_evidence_id);
  await waitForCdpExpression(page, "document.querySelector('details.original-asr-text')?.open === true", 5000);
  const clickbackState = await evaluate(page, () => ({
    raw_details_open: Boolean(document.querySelector("details.original-asr-text")?.open),
    focus_count: document.querySelectorAll(".transcript-segment.evidence-focus").length,
    raw_text: document.querySelector("details.original-asr-text")?.innerText || "",
  }));
  assert(clickbackState.raw_details_open, "original ASR disclosure did not open from evidence clickback");
  assert(clickbackState.focus_count >= 1, "evidence clickback did not focus canonical corrected segment");
  assert(clickbackState.raw_text.includes("先恢度"), "clickback raw ASR evidence is missing");
  await captureScreenshot(page, path.join(artifactRoot, "deterministic-correction-original-clickback.png"));

  const report = {
    status: "go_deterministic_correction_e2e",
    session_id: sessionId,
    backend: {
      provider: after.provider,
      provider_mode: after.provider_mode,
      is_mock: after.is_mock,
      correction_response: correctionResponse,
      revision_count: revisions.length,
      history_contains_session: history.sessions.some((session) => session.session_id === sessionId),
    },
    ui: uiState,
    clickback: clickbackState,
    screenshots: [
      "deterministic-correction-before-clickback.png",
      "deterministic-correction-original-clickback.png",
    ],
    counts_as_production_llm_evidence: false,
    remote_asr_called: false,
    local_gateway_called: true,
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "deterministic_correction_report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  failed = true;
  const report = {
    status: "blocked_deterministic_correction_e2e",
    error: error.message,
    backend_correction_response: correctionResponse,
    backend_history: history,
    server_stdout_tail: tailText(serverStdout, 5000),
    server_stderr_tail: tailText(serverStderr, 5000),
    gateway_stdout_tail: tailText(gatewayStdout, 2000),
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "deterministic_correction_error.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
} finally {
  for (const socket of sockets) {
    try { socket.close(); } catch {}
  }
  for (const process of processes) {
    try { process.kill("SIGTERM"); } catch {}
  }
  await removeTempDir(dataDir);
  await removeTempDir(chromeUserDataDir);
  process.exitCode = failed ? 1 : 0;
}

async function fetchJson(url, init = {}) {
  const response = await fetch(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error("HTTP " + String(response.status) + " " + url + ": " + JSON.stringify(body));
  }
  return body;
}

async function waitForHttp(url, timeoutMs = 20000, requireOk = true) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if ((requireOk && response.ok) || (!requireOk && response.status >= 100)) return;
    } catch {}
    await delay(150);
  }
  if (!requireOk) return;
  throw new Error("timed out waiting for " + url);
}

async function createCdpPage(debugPort, url) {
  const response = await fetch(
    "http://127.0.0.1:" + String(debugPort) + "/json/new?" + encodeURIComponent(url),
    { method: "PUT" },
  );
  if (!response.ok) throw new Error("failed to create Chrome page: " + String(response.status));
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  sockets.push(socket);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const item = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) item.reject(new Error(message.error.message));
    else item.resolve(message.result || {});
  });
  const cdpPage = {
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
  };
  await cdpPage.send("Runtime.enable");
  await cdpPage.send("Page.enable");
  await cdpPage.send("Page.navigate", { url });
  await waitForCdpExpression(cdpPage, "document.readyState === 'complete'");
  return cdpPage;
}

async function evaluate(cdpPage, fn, ...args) {
  const expression = "(" + fn.toString() + ")(..." + JSON.stringify(args) + ")";
  const result = await cdpPage.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser eval failed");
  return result.result?.value;
}

async function waitForCdpExpression(cdpPage, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await cdpPage.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.result?.value) return;
    await delay(180);
  }
  throw new Error("timed out waiting for browser expression: " + expression);
}

async function captureScreenshot(cdpPage, outputPath) {
  await mkdir(path.dirname(outputPath), { recursive: true });
  const result = await cdpPage.send("Page.captureScreenshot", { format: "png" });
  await writeFile(outputPath, Buffer.from(result.data, "base64"));
}

async function removeTempDir(directory) {
  try {
    await rm(directory, { recursive: true, force: true });
  } catch {}
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function tailText(value, maxChars) {
  const text = String(value || "");
  return text.length > maxChars ? text.slice(text.length - maxChars) : text;
}

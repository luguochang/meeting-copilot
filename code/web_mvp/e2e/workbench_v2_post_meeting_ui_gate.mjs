import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";

const baseUrl = String(process.env.MEETING_COPILOT_BASE_URL || "http://127.0.0.1:8767").replace(/\/+$/, "");
const meetingId = String(process.env.MEETING_COPILOT_MEETING_ID || "").trim();
const confirmation = String(process.env.MEETING_COPILOT_UI_GATE_CONFIRM || "").trim();
if (!meetingId) throw new Error("MEETING_COPILOT_MEETING_ID is required");
if (confirmation !== "isolated-test-meeting") {
  throw new Error("This gate mutates meeting documents; set MEETING_COPILOT_UI_GATE_CONFIRM=isolated-test-meeting");
}

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", `workbench-v2-post-meeting-${meetingId}`),
);
const downloadDir = path.join(artifactRoot, "downloads");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9267");
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-post-ui-chrome-"));
const sockets = [];
const requestLog = [];
const downloadEvents = [];
const diagnostics = { runtime_exceptions: [], console_errors: [], network_failures: [], http_5xx: [] };
const screenshots = [];
let chrome;

try {
  await mkdir(downloadDir, { recursive: true });
  chrome = spawn(chromePath, [
    "--disable-gpu",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${chromePort}`,
    `--user-data-dir=${chromeUserDataDir}`,
    "about:blank",
  ], { stdio: "ignore" });
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`, 60_000);

  const page = await createCdpPage(chromePort, `${baseUrl}/workbench?meeting_id=${encodeURIComponent(meetingId)}`);
  await page.send("Browser.setDownloadBehavior", {
    behavior: "allow",
    downloadPath: downloadDir,
    eventsEnabled: true,
  });
  await setViewport(page, { width: 1440, height: 900 });
  await waitFor(page, `document.querySelectorAll('[role="tab"]').length === 4`, 20_000);

  const initialSnapshot = await fetchSnapshot();
  assert(String(initialSnapshot.title || "").includes("E2E"), "refusing to mutate a meeting without an E2E title marker");
  await capture(page, "01-before-edit.png");

  const title = `E2E 会后闭环 ${Date.now()}`;
  await clickByAria(page, "编辑会议名称");
  await waitFor(page, `document.querySelector('#meeting-title-input') !== null`);
  await setValue(page, "#meeting-title-input", title);
  await clickByAria(page, "保存会议名称");
  await waitFor(page, `document.querySelector('.meeting-title-display h1')?.textContent?.trim() === ${JSON.stringify(title)}`);
  const titleSnapshot = await waitForSnapshot((snapshot) => snapshot.title === title);

  await clickTab(page, "复盘");
  await clickText(page, ".review-document button", "编辑");
  await waitFor(page, `document.querySelector('[aria-label="编辑会议复盘"]') !== null`);
  await appendValue(page, '[aria-label="编辑会议复盘"]', "\n\n<!-- E2E_MINUTES_FINAL -->");
  await waitForSnapshot((snapshot) => documentMarker(snapshot, "minutes", "E2E_MINUTES_FINAL"));
  await clickText(page, ".review-document button", "退出编辑");

  await clickTab(page, "决策与待办");
  await clickText(page, "button", "编辑最终稿");
  await waitFor(page, `document.querySelector('[aria-label="编辑决策 1"]') !== null`);
  await appendValue(page, '[aria-label="编辑决策 1"]', " · E2E_DECISION_FINAL");
  await appendValue(page, '[aria-label="编辑行动项 1"]', " · E2E_ACTION_FINAL");
  await appendValue(page, '[aria-label="编辑风险 1"]', " · E2E_RISK_FINAL");
  const actionsSnapshot = await waitForSnapshot((snapshot) => (
    documentMarker(snapshot, "decisions", "E2E_DECISION_FINAL")
      && documentMarker(snapshot, "action_items", "E2E_ACTION_FINAL")
      && documentMarker(snapshot, "risks", "E2E_RISK_FINAL")
  ));
  await clickText(page, "button", "完成编辑");

  await clickTab(page, "会议文字");
  await clickText(page, "button", "编辑最终文字");
  await waitFor(page, `document.querySelector('[aria-label="编辑会议文字第 1 段"]') !== null`);
  await appendValue(page, '[aria-label="编辑会议文字第 1 段"]', " E2E_TRANSCRIPT_FINAL");
  const transcriptSnapshot = await waitForSnapshot((snapshot) => documentMarker(snapshot, "transcript", "E2E_TRANSCRIPT_FINAL"));
  await clickText(page, "button", "完成编辑");
  await capture(page, "02-user-final-edits.png");

  const userFinal = {
    title: titleSnapshot.title === title,
    minutes: documentMarker(transcriptSnapshot, "minutes", "E2E_MINUTES_FINAL"),
    decisions: documentMarker(actionsSnapshot, "decisions", "E2E_DECISION_FINAL"),
    action_items: documentMarker(actionsSnapshot, "action_items", "E2E_ACTION_FINAL"),
    risks: documentMarker(actionsSnapshot, "risks", "E2E_RISK_FINAL"),
    transcript: documentMarker(transcriptSnapshot, "transcript", "E2E_TRANSCRIPT_FINAL"),
    all_documents_protected: ["minutes", "decisions", "action_items", "risks", "transcript"]
      .every((kind) => snapshotUserFinalModified(transcriptSnapshot, kind) || snapshotUserFinalModified(actionsSnapshot, kind)),
  };
  assert(Object.values(userFinal).every(Boolean), `user_final edit contract failed: ${JSON.stringify(userFinal)}`);

  const downloads = [];
  for (const format of ["Markdown", "Word 文档", "JSON"]) {
    const before = new Set(await completedDownloads());
    await clickByAria(page, "导出会议");
    await waitFor(page, `document.querySelector('[role="menu"]') !== null`);
    await clickText(page, '[role="menuitem"]', format);
    const file = await waitForNewDownload(before, 20_000);
    downloads.push(await inspectDownload(file));
  }
  assert(downloads.length === 3, `expected three UI downloads: ${JSON.stringify(downloads)}`);
  assert(downloads.some((item) => item.extension === ".md" && item.contains_minutes_marker), "Markdown export missed user_final marker");
  assert(downloads.some((item) => item.extension === ".json" && item.contains_transcript_marker), "JSON export missed user_final marker");
  assert(downloads.some((item) => item.extension === ".docx" && item.valid_zip), "DOCX export is not a valid OOXML zip");

  const requestCountBeforeDiagnostics = requestLog.length;
  await clickByAria(page, "打开运行诊断");
  await waitFor(page, `document.querySelector('[aria-labelledby="diagnostics-title"]') !== null`);
  await clickText(page, ".diagnostics-drawer button", "重新读取状态");
  await clickText(page, ".diagnostics-drawer button", "重新读取状态");
  await waitFor(page, `document.querySelector('[aria-labelledby="diagnostics-title"]') !== null`);
  const diagnosticRequests = requestLog.slice(requestCountBeforeDiagnostics);
  assert(diagnosticRequests.some((url) => url.includes(`/v2/meetings/${meetingId}/snapshot`)), "diagnostic refresh did not reread local snapshot");
  assert(!diagnosticRequests.some((url) => url.includes("/providers/llm/probe")), "diagnostic refresh unexpectedly probed or billed the LLM");
  assert(diagnosticRequests.every((url) => url.startsWith(baseUrl)), `diagnostic refresh escaped local origin: ${JSON.stringify(diagnosticRequests)}`);
  const diagnosticBefore = new Set(await completedDownloads());
  await clickText(page, ".diagnostics-drawer button", "导出脱敏诊断包");
  const diagnosticFile = await waitForNewDownload(diagnosticBefore, 20_000);
  const diagnostic = await inspectDiagnosticDownload(diagnosticFile);
  await clickBySelector(page, '.diagnostics-drawer [aria-label="关闭运行诊断"]');
  await capture(page, "03-diagnostics-and-exports.png");

  await clickText(page, "button", "返回会议列表");
  await waitFor(page, `document.querySelector('[placeholder="搜索会议名称"]') !== null`, 20_000);
  await setValue(page, '[placeholder="搜索会议名称"]', title);
  await waitFor(page, `document.querySelectorAll('.history-row').length === 1`);
  await setSelectValue(page, ".history-filter select", "ready");
  await waitFor(page, `document.querySelectorAll('.history-row').length === 1`);
  const history = await evaluate(page, () => ({
    rows: document.querySelectorAll(".history-row").length,
    title: document.querySelector(".history-row-open strong")?.textContent?.trim() || "",
    overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  assert(history.rows === 1 && history.title === title && !history.overflow, `history search/filter failed: ${JSON.stringify(history)}`);
  await clickBySelector(page, ".history-row-open");
  await waitFor(page, `new URL(location.href).searchParams.get('meeting_id') === ${JSON.stringify(meetingId)}`);
  await waitFor(page, `document.querySelector('.meeting-title-display h1')?.textContent?.trim() === ${JSON.stringify(title)}`);
  await capture(page, "04-history-reopened.png");

  const report = {
    schema_version: "workbench-v2-post-meeting-ui-gate.v1",
    verdict: diagnostics.runtime_exceptions.length === 0
      && diagnostics.console_errors.length === 0
      && diagnostics.network_failures.length === 0
      && diagnostics.http_5xx.length === 0
      ? "go_shared_ui_non_acceptance"
      : "no_go",
    acceptance_scope: "isolated_test_meeting_shared_ui",
    acceptance_eligible: false,
    counts_as_packaged_client_go: false,
    meeting_id_hash: shortHash(meetingId),
    user_final: userFinal,
    downloads,
    diagnostic,
    history,
    download_events: downloadEvents,
    diagnostics,
    screenshots,
  };
  await writeFile(path.join(artifactRoot, "report.json"), JSON.stringify(report, null, 2));
  if (report.verdict !== "go_shared_ui_non_acceptance") throw new Error(`UI diagnostics failed: ${JSON.stringify(diagnostics)}`);
  console.log(JSON.stringify(report, null, 2));
} finally {
  for (const socket of sockets) socket.close();
  await terminateChild(chrome);
  await rm(chromeUserDataDir, { recursive: true, force: true });
}

async function createCdpPage(debugPort, url) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`failed to create Chrome page: ${response.status}`);
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
  sockets.push(socket);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });
  let nextId = 1;
  const pending = new Map();
  const handlers = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const current = pending.get(message.id);
      pending.delete(message.id);
      message.error ? current.reject(new Error(message.error.message)) : current.resolve(message.result || {});
      return;
    }
    for (const handler of handlers.get(message.method) || []) handler(message.params || {});
  });
  socket.addEventListener("close", () => {
    for (const request of pending.values()) request.reject(new Error("Chrome DevTools connection closed"));
    pending.clear();
  });
  const page = {
    send(method, params = {}, timeoutMs = 15_000) {
      const id = nextId++;
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`Chrome DevTools command timed out: ${method}`));
        }, timeoutMs);
        pending.set(id, {
          resolve(value) { clearTimeout(timeout); resolve(value); },
          reject(error) { clearTimeout(timeout); reject(error); },
        });
        try { socket.send(JSON.stringify({ id, method, params })); } catch (error) {
          pending.delete(id);
          clearTimeout(timeout);
          reject(error);
        }
      });
    },
    on(method, handler) { handlers.set(method, [...(handlers.get(method) || []), handler]); },
  };
  page.on("Runtime.exceptionThrown", (params) => diagnostics.runtime_exceptions.push(params.exceptionDetails?.text || "runtime exception"));
  page.on("Runtime.consoleAPICalled", (params) => {
    if (params.type === "error") diagnostics.console_errors.push((params.args || []).map((item) => item.value || item.description || "").join(" "));
  });
  page.on("Network.requestWillBeSent", (params) => {
    if (params.request?.url) requestLog.push(params.request.url);
  });
  page.on("Network.loadingFailed", (params) => {
    if (params.canceled === true) return;
    diagnostics.network_failures.push({ error: params.errorText, type: params.type || null });
  });
  page.on("Network.responseReceived", (params) => {
    if (Number(params.response?.status || 0) >= 500) diagnostics.http_5xx.push({ url: params.response?.url, status: params.response.status });
  });
  page.on("Browser.downloadWillBegin", (params) => downloadEvents.push({ guid: params.guid, file: params.suggestedFilename }));
  await page.send("Runtime.enable");
  await page.send("Network.enable");
  await page.send("Page.enable");
  await page.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: downloadDir, eventsEnabled: true });
  await page.send("Page.navigate", { url });
  await waitFor(page, `document.readyState === 'complete'`);
  return page;
}

async function evaluate(page, fn, ...args) {
  const expression = `(${fn.toString()})(...${JSON.stringify(args)})`;
  const result = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "browser evaluation failed");
  return result.result.value;
}

async function waitFor(page, expression, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await page.send("Runtime.evaluate", { expression: `Boolean(${expression})`, returnByValue: true });
    if (result.result?.value) return;
    await delay(100);
  }
  throw new Error(`timed out waiting for ${expression}`);
}

async function clickByAria(page, label) {
  const clicked = await evaluate(page, (target) => {
    const matches = [...document.querySelectorAll("button")].filter((button) => button.getAttribute("aria-label") === target);
    if (matches.length !== 1) return { count: matches.length };
    matches[0].click();
    return { count: 1 };
  }, label);
  assert(clicked.count === 1, `expected one button aria-label=${label}, got ${clicked.count}`);
}

async function clickBySelector(page, selector) {
  const clicked = await evaluate(page, (target) => {
    const matches = [...document.querySelectorAll(target)].filter((element) => {
      const style = getComputedStyle(element);
      return style.display !== "none" && style.visibility !== "hidden";
    });
    if (matches.length !== 1) return { count: matches.length };
    matches[0].click();
    return { count: 1 };
  }, selector);
  assert(clicked.count === 1, `expected one visible ${selector}, got ${clicked.count}`);
}

async function clickText(page, selector, text) {
  const clicked = await evaluate(page, (args) => {
    const matches = [...document.querySelectorAll(args.selector)].filter((element) => element.textContent?.trim() === args.text);
    if (matches.length !== 1) return { count: matches.length };
    matches[0].click();
    return { count: 1 };
  }, { selector, text });
  assert(clicked.count === 1, `expected one ${selector} with text ${text}, got ${clicked.count}`);
}

async function setValue(page, selector, value) {
  const updated = await evaluate(page, (args) => {
    const element = document.querySelector(args.selector);
    if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) return false;
    const prototype = element instanceof HTMLInputElement ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (!setter) return false;
    setter.call(element, args.value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }, { selector, value });
  assert(updated, `could not set ${selector}`);
}

async function appendValue(page, selector, suffix) {
  const updated = await evaluate(page, (args) => {
    const element = document.querySelector(args.selector);
    if (!(element instanceof HTMLTextAreaElement)) return false;
    const prototype = HTMLTextAreaElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
    if (!setter) return false;
    setter.call(element, `${element.value}${args.suffix}`);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }, { selector, suffix });
  assert(updated, `could not append ${selector}`);
}

async function setSelectValue(page, selector, value) {
  const updated = await evaluate(page, (args) => {
    const element = document.querySelector(args.selector);
    if (!(element instanceof HTMLSelectElement)) return false;
    element.value = args.value;
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return element.value === args.value;
  }, { selector, value });
  assert(updated, `could not set select ${selector}`);
}

async function clickTab(page, label) {
  await clickText(page, '[role="tab"]', label);
}

async function setViewport(page, viewport) {
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: false,
  });
}

async function capture(page, fileName) {
  const result = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, fromSurface: true });
  await writeFile(path.join(artifactRoot, fileName), Buffer.from(result.data, "base64"));
  screenshots.push(fileName);
}

async function fetchSnapshot() {
  return fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
}

async function waitForSnapshot(predicate, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  let snapshot = await fetchSnapshot();
  while (Date.now() < deadline) {
    if (predicate(snapshot)) return snapshot;
    await delay(200);
    snapshot = await fetchSnapshot();
  }
  throw new Error(`snapshot predicate timed out: ${JSON.stringify(snapshot)}`);
}

function documentContent(snapshot, kind) {
  return snapshot.documents?.[kind]?.user_final?.content ?? snapshot.documents?.[kind]?.userFinal?.content ?? null;
}

function documentMarker(snapshot, kind, marker) {
  return JSON.stringify(documentContent(snapshot, kind) ?? "").includes(marker);
}

function snapshotUserFinalModified(snapshot, kind) {
  return snapshot.documents?.[kind]?.user_final?.modified === true
    || snapshot.documents?.[kind]?.userFinal?.modified === true;
}

async function completedDownloads() {
  const entries = await readdir(downloadDir, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    if (!entry.isFile() || entry.name.endsWith(".crdownload")) continue;
    files.push(path.join(downloadDir, entry.name));
  }
  return files;
}

async function waitForNewDownload(before, timeoutMs) {
  const beforeSet = new Set(before);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const current = await completedDownloads();
    const newFile = current.find((file) => !beforeSet.has(file));
    if (newFile && (await stat(newFile)).size > 0) return newFile;
    await delay(100);
  }
  throw new Error(`download did not complete; events=${JSON.stringify(downloadEvents)}`);
}

async function inspectDownload(file) {
  const name = path.basename(file);
  const bytes = await readFile(file);
  const extension = path.extname(name).toLowerCase();
  let validZip = false;
  let containsMinutesMarker = false;
  let containsTranscriptMarker = false;
  if (extension === ".docx") {
    validZip = bytes.subarray(0, 4).toString("hex") === "504b0304" && unzipTest(file);
  }
  if (extension === ".md") containsMinutesMarker = bytes.toString("utf8").includes("E2E_MINUTES_FINAL");
  if (extension === ".json") containsTranscriptMarker = bytes.toString("utf8").includes("E2E_TRANSCRIPT_FINAL");
  return { name, extension, bytes: bytes.byteLength, valid_zip: validZip, contains_minutes_marker: containsMinutesMarker, contains_transcript_marker: containsTranscriptMarker };
}

async function inspectDiagnosticDownload(file) {
  const name = path.basename(file);
  assert(name.endsWith(".zip"), `diagnostic export is not zip: ${name}`);
  const entries = spawnSync("unzip", ["-Z1", file], { encoding: "utf8" });
  assert(entries.status === 0, `diagnostic zip cannot be listed: ${entries.stderr}`);
  const content = spawnSync("unzip", ["-p", file, "diagnostics.json"], { encoding: "utf8" });
  const manifest = spawnSync("unzip", ["-p", file, "manifest.json"], { encoding: "utf8" });
  const serialized = `${entries.stdout}\n${content.stdout}\n${manifest.stdout}`;
  const forbidden = ["Authorization", "Bearer ", "api_key", "transcript", "audio.wav", "prompt", "response", "/Users/"];
  assert(forbidden.every((value) => !serialized.includes(value)), `diagnostic bundle leaked forbidden content: ${valueFrom(forbidden, serialized)}`);
  return { name, bytes: (await stat(file)).size, has_diagnostics: serialized.includes("diagnostics"), has_manifest: serialized.includes("manifest"), forbidden_terms_absent: true };
}

function valueFrom(values, serialized) {
  return values.find((value) => serialized.includes(value)) || null;
}

function unzipTest(file) {
  return spawnSync("unzip", ["-t", file], { encoding: "utf8" }).status === 0;
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} returned ${response.status}`);
  return response.json();
}

async function waitForHttp(url, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {}
    await delay(150);
  }
  throw new Error(`timed out waiting for ${url}`);
}

async function terminateChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  const exited = new Promise((resolve) => child.once("exit", resolve));
  try { child.kill("SIGTERM"); } catch { return; }
  await Promise.race([exited, delay(3_000)]);
  if (child.exitCode === null && child.signalCode === null) {
    try { child.kill("SIGKILL"); } catch { return; }
    await Promise.race([exited, delay(1_000)]);
  }
}

function shortHash(value) {
  return createHash("sha256").update(String(value)).digest("hex").slice(0, 16);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const baseUrl = String(process.env.MEETING_COPILOT_BASE_URL || "http://127.0.0.1:8767").replace(/\/+$/, "");
const meetingId = String(process.env.MEETING_COPILOT_MEETING_ID || "").trim();
if (!meetingId) throw new Error("MEETING_COPILOT_MEETING_ID is required");

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT ||
    path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", `workbench-v2-${meetingId}`),
);
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9247");
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-v2-chrome-"));
const sockets = [];
const diagnostics = { runtime_exceptions: [], console_errors: [], network_failures: [], http_5xx: [], allowlisted_network_cancellations: [] };
const screenshots = [];
let chrome;

try {
  await mkdir(artifactRoot, { recursive: true });
  chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${chromePort}`,
    `--user-data-dir=${chromeUserDataDir}`,
    "about:blank",
  ], { stdio: "ignore" });
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`, 30_000);

  const page = await createCdpPage(chromePort, `${baseUrl}/workbench`);
  await setViewport(page, { width: 1440, height: 900, mobile: false });
  await waitFor(page, `document.querySelector('.start-meeting-button') !== null`);
  const home = await evaluate(page, () => ({
    start_button_count: [...document.querySelectorAll("button")].filter((button) => button.textContent?.includes("开始会议")).length,
    history_visible: document.body.innerText.includes("会议记录"),
    history_row_count: document.querySelectorAll(".history-row").length,
    horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  assert(home.start_button_count === 1, `expected one start command: ${JSON.stringify(home)}`);
  assert(home.history_visible && home.history_row_count > 0, `history did not load: ${JSON.stringify(home)}`);
  assert(!home.horizontal_overflow, `home has horizontal overflow: ${JSON.stringify(home)}`);
  await capture(page, "01-start-and-history-desktop.png", { fullPage: false, width: 1440, height: 900 });

  await page.send("Page.navigate", { url: `${baseUrl}/workbench?meeting_id=${encodeURIComponent(meetingId)}` });
  await waitFor(page, `document.querySelectorAll('[role="tab"]').length === 4`, 20_000);
  await waitFor(page, `document.body.innerText.includes('会议复盘')`, 20_000);
  const review = await evaluate(page, () => ({
    tabs: [...document.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent?.trim()),
    end_button_count: [...document.querySelectorAll("button")].filter((button) => button.textContent?.includes("结束并整理")).length,
    minutes_visible: document.body.innerText.includes("会议纪要") && document.body.innerText.includes("灰度"),
    progress_text: document.querySelector(".review-progress")?.textContent?.trim(),
    horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  }));
  assert(JSON.stringify(review.tabs) === JSON.stringify(["复盘", "决策与待办", "会议文字", "录音"]), `review tabs mismatch: ${JSON.stringify(review)}`);
  assert(review.end_button_count === 0, `ended meeting still shows end command: ${JSON.stringify(review)}`);
  assert(review.minutes_visible, `minutes are not visible: ${JSON.stringify(review)}`);
  assert(!review.horizontal_overflow, `review has horizontal overflow: ${JSON.stringify(review)}`);
  await capture(page, "02-review-desktop.png", { fullPage: false, width: 1440, height: 900 });

  await clickTab(page, "决策与待办");
  await waitFor(page, `document.body.innerText.includes('待确认问题')`);
  await capture(page, "03-actions-desktop.png", { fullPage: false, width: 1440, height: 900 });

  await clickTab(page, "会议文字");
  await waitFor(page, `document.querySelectorAll('.transcript-segment').length >= 2`);
  const transcript = await evaluate(page, () => ({
    segment_ids: [...document.querySelectorAll(".transcript-segment")].map((item) => item.id),
    active_partial_count: document.querySelectorAll(".active-partial").length,
    texts: [...document.querySelectorAll(".transcript-segment p")].map((item) => item.textContent?.trim()),
  }));
  assert(new Set(transcript.segment_ids).size === transcript.segment_ids.length, `duplicate transcript rows: ${JSON.stringify(transcript)}`);
  assert(transcript.active_partial_count === 0, `ended transcript retained active partial: ${JSON.stringify(transcript)}`);
  await capture(page, "04-transcript-desktop.png", { fullPage: false, width: 1440, height: 900 });

  await clickTab(page, "录音");
  await waitFor(page, `document.querySelector('audio')?.getAttribute('src')?.includes('/audio/content') === true`);
  const audio = await evaluate(page, () => ({
    source: document.querySelector("audio")?.getAttribute("src"),
    controls: document.querySelector("audio")?.controls === true,
    facts: document.querySelector(".audio-facts")?.textContent?.trim(),
  }));
  assert(audio.controls && audio.source?.includes("/audio/content"), `audio player unavailable: ${JSON.stringify(audio)}`);
  await capture(page, "05-audio-desktop.png", { fullPage: false, width: 1440, height: 900 });

  await setViewport(page, { width: 375, height: 812, mobile: true });
  await page.send("Page.reload", { ignoreCache: true });
  await waitFor(
    page,
    `document.readyState === 'complete' && ` +
      `JSON.stringify([...document.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent.trim())) === ` +
      `'["复盘","决策与待办","会议文字","录音"]'`,
    20_000,
  );
  const mobile = await evaluate(page, () => {
    const visibleButtons = [...document.querySelectorAll("button")].filter((button) => {
      const rect = button.getBoundingClientRect();
      const style = getComputedStyle(button);
      return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    });
    const overlaps = [];
    for (let left = 0; left < visibleButtons.length; left += 1) {
      for (let right = left + 1; right < visibleButtons.length; right += 1) {
        const a = visibleButtons[left].getBoundingClientRect();
        const b = visibleButtons[right].getBoundingClientRect();
        if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2) {
          overlaps.push([visibleButtons[left].textContent?.trim(), visibleButtons[right].textContent?.trim()]);
        }
      }
    }
    return {
      inner_width: window.innerWidth,
      scroll_width: document.documentElement.scrollWidth,
      horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      overlapping_buttons: overlaps,
      tabs: [...document.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent?.trim()),
    };
  });
  assert(!mobile.horizontal_overflow, `mobile horizontal overflow: ${JSON.stringify(mobile)}`);
  assert(mobile.overlapping_buttons.length === 0, `mobile buttons overlap: ${JSON.stringify(mobile)}`);
  assert(mobile.tabs.length === 4, `mobile review tabs disappeared: ${JSON.stringify(mobile)}`);
  await capture(page, "06-review-mobile.png", { fullPage: false, width: 375, height: 812 });

  const report = {
    schema_version: "workbench-v2-product-smoke.v1",
    verdict: [
      diagnostics.runtime_exceptions,
      diagnostics.console_errors,
      diagnostics.network_failures,
      diagnostics.http_5xx,
    ].every((items) => items.length === 0) ? "go" : "no_go",
    meeting_id: meetingId,
    base_url: baseUrl,
    home,
    review,
    transcript,
    audio,
    mobile,
    diagnostics,
    screenshots,
  };
  await writeFile(path.join(artifactRoot, "report.json"), JSON.stringify(report, null, 2));
  if (report.verdict !== "go") throw new Error(`browser diagnostics failed: ${JSON.stringify(diagnostics)}`);
  console.log(JSON.stringify({ verdict: report.verdict, artifact_root: artifactRoot, screenshots, home, review, transcript, audio, mobile }, null, 2));
} finally {
  for (const socket of sockets) socket.close();
  chrome?.kill("SIGTERM");
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
  const page = {
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    on(method, handler) {
      handlers.set(method, [...(handlers.get(method) || []), handler]);
    },
  };
  page.on("Runtime.exceptionThrown", (params) => diagnostics.runtime_exceptions.push(params.exceptionDetails?.text || "runtime exception"));
  page.on("Runtime.consoleAPICalled", (params) => {
    if (params.type === "error") diagnostics.console_errors.push((params.args || []).map((item) => item.value || item.description || "").join(" "));
  });
  const urls = new Map();
  page.on("Network.requestWillBeSent", (params) => urls.set(params.requestId, params.request?.url || ""));
  page.on("Network.loadingFailed", (params) => {
    const url = urls.get(params.requestId) || "";
    if (url.endsWith("/favicon.ico")) return;
    if (url.includes("/audio/content") && params.errorText === "net::ERR_ABORTED") {
      diagnostics.allowlisted_network_cancellations.push({ url, error: params.errorText, reason: "media_element_unloaded" });
      return;
    }
    diagnostics.network_failures.push({ url, error: params.errorText });
  });
  page.on("Network.responseReceived", (params) => {
    if (Number(params.response?.status || 0) >= 500) diagnostics.http_5xx.push({ url: params.response?.url, status: params.response?.status });
  });
  await page.send("Runtime.enable");
  await page.send("Network.enable");
  await page.send("Page.enable");
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

async function clickTab(page, label) {
  const clicked = await evaluate(page, (target) => {
    const tab = [...document.querySelectorAll('[role="tab"]')].find((item) => item.textContent?.trim() === target);
    if (!tab) return false;
    tab.click();
    return true;
  }, label);
  assert(clicked, `tab not found: ${label}`);
}

async function setViewport(page, viewport) {
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.mobile,
    screenWidth: viewport.width,
    screenHeight: viewport.height,
  });
}

async function capture(page, fileName, options) {
  const result = await page.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: options.fullPage,
    clip: { x: 0, y: 0, width: options.width, height: options.height, scale: 1 },
  });
  const output = path.join(artifactRoot, fileName);
  await writeFile(output, Buffer.from(result.data, "base64"));
  screenshots.push(output);
}

async function waitFor(page, expression, timeoutMs = 15_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.result?.value) return;
    await delay(150);
  }
  throw new Error(`timed out waiting for: ${expression}`);
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

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

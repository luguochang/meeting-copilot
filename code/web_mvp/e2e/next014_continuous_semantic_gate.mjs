#!/usr/bin/env node
/* eslint-disable no-console */

import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

export const NEXT014_SCOPE = "non_acceptance_synthetic_next014_continuous_asr";
export const CONTINUOUS_WINDOW_MS = 45_000;
export const SYNTHETIC_TIMELINE_MS = 60_000;

const checkpointFixture = [
  { id: "next014-history-1", start_ms: 0, end_ms: 1_000 },
  { id: "next014-history-2", start_ms: 3_000, end_ms: 4_000 },
  { id: "next014-history-3", start_ms: 6_000, end_ms: 7_000 },
  { id: "next014-continuous-00", start_ms: 9_000, end_ms: 24_000, continuous: true },
  { id: "next014-continuous-15", start_ms: 24_000, end_ms: 39_000, continuous: true },
  { id: "next014-continuous-30", start_ms: 39_000, end_ms: 54_000, continuous: true },
  { id: "next014-history-tail", start_ms: 56_000, end_ms: 60_000 },
];

export function buildNext014FixtureContract() {
  const continuous = checkpointFixture.filter((item) => item.continuous);
  return {
    scope: NEXT014_SCOPE,
    synthetic: true,
    acceptance_eligible: false,
    natural_multi_speaker_replacement: false,
    timeline_ms: SYNTHETIC_TIMELINE_MS,
    continuous_window_ms: continuous.at(-1).end_ms - continuous[0].start_ms,
    checkpoint_count: checkpointFixture.length,
    continuous_checkpoint_ids: continuous.map((item) => item.id),
    checkpoints: checkpointFixture,
  };
}

function asText(value) {
  return String(value ?? "").trim();
}

function numberOrNull(value) {
  if (value === null || value === undefined || value === "") return null;
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

export function evaluateNext014Report(candidate = {}) {
  const blockers = [];
  const add = (blocker) => {
    if (!blockers.includes(blocker)) blockers.push(blocker);
  };
  const fixture = buildNext014FixtureContract();
  const uiBefore = candidate.ui_before_end || {};
  const uiAfter = candidate.ui_after_end || {};
  const continuous = candidate.continuous_projection || {};
  const scroll = candidate.history_scroll || {};
  const paragraphsBefore = Array.isArray(candidate.semantic_paragraphs_before_end)
    ? candidate.semantic_paragraphs_before_end
    : [];
  const paragraphsAfter = Array.isArray(candidate.semantic_paragraphs_after_end)
    ? candidate.semantic_paragraphs_after_end
    : [];
  const beforeRows = Array.isArray(uiBefore.rows) ? uiBefore.rows : [];
  const afterRows = Array.isArray(uiAfter.rows) ? uiAfter.rows : [];

  if (candidate.acceptance_scope !== NEXT014_SCOPE) add("acceptance_scope_invalid");
  if (candidate.synthetic !== true) add("synthetic_marker_missing");
  if (candidate.acceptance_eligible !== false) add("synthetic_scope_must_not_be_acceptance_eligible");
  if (candidate.natural_multi_speaker_replacement !== false) add("natural_multi_speaker_boundary_missing");
  if (Number(candidate.timeline_ms) !== SYNTHETIC_TIMELINE_MS) add("synthetic_timeline_not_30_to_60_seconds");
  if (Number(candidate.continuous_window_ms) !== CONTINUOUS_WINDOW_MS) add("continuous_window_not_45_seconds");
  if (Number(candidate.checkpoint_count) < fixture.checkpoint_count) add("controlled_checkpoints_missing");

  const beforeParagraphTexts = paragraphsBefore.map((item) => asText(item.text));
  const afterParagraphTexts = paragraphsAfter.map((item) => asText(item.text));
  const beforeRowTexts = beforeRows.map((item) => asText(item.text));
  const afterRowTexts = afterRows.map((item) => asText(item.text));
  if (
    beforeRows.length !== paragraphsBefore.length
    || beforeRowTexts.some((text, index) => text !== beforeParagraphTexts[index])
  ) add("live_ui_is_not_durable_semantic_projection");
  if (Number(uiBefore.raw_checkpoint_row_count) !== Number(candidate.raw_checkpoint_count_before_end)) {
    add("raw_checkpoint_observation_missing");
  }
  if (Number(uiBefore.row_count) === Number(candidate.raw_checkpoint_count_before_end)) {
    add("mechanical_checkpoint_fragments_visible");
  }

  const continuousIds = fixture.continuous_checkpoint_ids;
  if (
    Number(continuous.paragraph_count) !== 1
    || Number(continuous.checkpoint_count) !== continuousIds.length
    || asText(continuous.paragraph_id) === ""
    || continuousIds.some((id) => !Array.isArray(continuous.checkpoint_ids) || !continuous.checkpoint_ids.includes(id))
    || Number(continuous.duration_ms) !== CONTINUOUS_WINDOW_MS
  ) add("continuous_checkpoints_split_into_mechanical_fragments");
  if (Number(continuous.paragraph_count) >= Number(continuous.checkpoint_count)) {
    add("mechanical_checkpoint_fragments_visible");
  }

  if (Number(candidate.active_partial_duplicate_count) !== 0) add("active_partial_repeated_durable_text");
  if (scroll.locked !== true || numberOrNull(scroll.before_top) !== numberOrNull(scroll.after_top)) {
    add("history_scroll_position_changed");
  }
  if (Number(scroll.new_paragraph_count) < 1 || !asText(scroll.notice).includes("有 ")) {
    add("new_paragraph_notice_missing");
  }

  const beforeFullText = asText(candidate.full_text_before_end);
  const afterFullText = asText(candidate.full_text_after_end);
  if (!beforeFullText || beforeFullText !== afterFullText) add("canonical_full_text_changed_at_end");
  if (afterRows.length !== paragraphsAfter.length && candidate.after_end_projection_is_semantic === true) {
    add("ended_ui_projection_mismatch");
  }
  const diagnostics = candidate.diagnostics || {};
  if ([
    diagnostics.runtime_exceptions,
    diagnostics.console_errors,
    diagnostics.network_failures,
    diagnostics.http_5xx,
  ].some((items) => Array.isArray(items) && items.length > 0)) add("browser_runtime_errors");

  return {
    schema_version: "next014-continuous-semantic-scroll-gate.v1",
    verdict: blockers.length ? "failed_non_acceptance" : "passed_non_acceptance",
    acceptance_scope: NEXT014_SCOPE,
    synthetic: true,
    acceptance_eligible: false,
    counts_as_real_release_go: false,
    natural_multi_speaker_replacement: false,
    blockers,
    checkpoint_contract: fixture,
    continuous_projection: continuous,
    history_scroll: scroll,
    active_partial_duplicate_count: Number(candidate.active_partial_duplicate_count || 0),
    full_text_equal_before_after_end: Boolean(beforeFullText && beforeFullText === afterFullText),
  };
}

async function run() {
  const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
  const artifactRoot = path.resolve(
    process.env.MEETING_COPILOT_ARTIFACT_ROOT
      || path.join(repoRoot, "artifacts", "tmp", `next014-continuous-semantic-${Date.now()}`),
  );
  const dataDir = path.join(artifactRoot, "runtime-data");
  const fixture = path.join(import.meta.dirname, "next014_continuous_semantic_backend.py");
  const frontendDir = path.join(repoRoot, "code", "web_mvp", "frontend_v2");
  const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
  const coreDir = path.join(repoRoot, "code", "core");
  const basePort = 8_700 + (process.pid % 500);
  const backendPort = Number(process.env.MEETING_COPILOT_NEXT014_BACKEND_PORT || basePort);
  const frontendPort = Number(process.env.MEETING_COPILOT_NEXT014_FRONTEND_PORT || backendPort + 1);
  const chromePort = Number(process.env.MEETING_COPILOT_NEXT014_CHROME_PORT || backendPort + 2);
  const baseUrl = `http://127.0.0.1:${backendPort}`;
  const frontendUrl = `${baseUrl}/workbench`;
  const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-next014-chrome-"));
  const wavPath = path.join(chromeUserDataDir, "next014-synthetic-60s.wav");
  const diagnostics = {
    runtime_exceptions: [],
    console_errors: [],
    network_failures: [],
    http_5xx: [],
  };
  let backend;
  let frontend;
  let chrome;
  let page;
  let meetingId = null;
  let scrollBefore = null;

  try {
    await mkdir(artifactRoot, { recursive: true });
    await writeSyntheticWav(wavPath, 55);
    const python = process.env.PYTHON_BIN || "python3";
    const pythonPath = [backendDir, coreDir, process.env.PYTHONPATH || ""].filter(Boolean).join(path.delimiter);
    backend = spawn(python, [fixture], {
      cwd: backendDir,
      env: {
        ...process.env,
        PYTHONPATH: pythonPath,
        MEETING_COPILOT_DATA_DIR: dataDir,
        MEETING_COPILOT_E2E_PORT: String(backendPort),
        MEETING_COPILOT_E2E_HOST: "127.0.0.1",
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    frontend = spawn(process.env.NPM_BIN || "npm", ["run", "build"], {
      cwd: frontendDir,
      env: { ...process.env, VITE_DEV_API_TARGET: baseUrl },
      stdio: ["ignore", "pipe", "pipe"],
    });
    const backendLog = collectChildOutput(backend);
    const frontendLog = collectChildOutput(frontend);
    await waitForChild(frontend, "frontend build", frontendLog);
    frontend = null;
    await waitForHttp(`${baseUrl}/health`, 30_000);
    await waitForHttp(frontendUrl, 30_000);

    chrome = spawn(chromePath, [
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${wavPath}`,
      "--autoplay-policy=no-user-gesture-required",
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${chromeUserDataDir}`,
      "about:blank",
    ], { stdio: "ignore" });
    await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`, 30_000);
    page = await createCdpPage(chromePort, frontendUrl, diagnostics, baseUrl);
    await setViewport(page, { width: 980, height: 650 });
    await waitFor(page, `document.querySelector('.start-meeting-button') !== null`, 20_000);
    await evaluate(page, () => document.querySelector(".start-meeting-button")?.click());
    await waitFor(page, `document.querySelector('.meeting-preflight-dialog') !== null`, 10_000);
    await waitFor(page, `document.querySelector('.preflight-consent input') !== null`, 10_000);
    await evaluate(page, () => document.querySelector(".preflight-consent input")?.click());
    await waitForPreflightReady(page, 20_000);
    await evaluate(page, () => document.querySelector(".meeting-preflight-actions .primary-button")?.click());
    await waitFor(page, `document.querySelector('.end-meeting-button') !== null`, 30_000);
    meetingId = await evaluate(page, () => new URL(location.href).searchParams.get("meeting_id"));
    if (!meetingId) throw new Error("NEXT-014 runner did not bind meeting_id");

    const samples = [];
    let beforeEnd = null;
    const captureDeadline = Date.now() + 60_000;
    while (Date.now() < captureDeadline) {
      const [snapshot, ui] = await Promise.all([
        fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`),
        evaluate(page, captureLiveUi),
      ]);
      const activePartial = asText(ui.active_partial_text);
      const duplicate = activePartial !== "" && ui.rows.some((row) => asText(row.text) === activePartial);
      samples.push({
        at_ms: Date.now(),
        ui,
        semantic_paragraph_count: Array.isArray(snapshot.semantic_paragraphs) ? snapshot.semantic_paragraphs.length : 0,
        semantic_checkpoint_count: Array.isArray(snapshot.semantic_paragraphs)
          ? snapshot.semantic_paragraphs.reduce((sum, paragraph) => sum + (paragraph.checkpoint_ids?.length || 0), 0)
          : 0,
        active_partial_duplicate: duplicate,
      });
      const continuousParagraph = findContinuousParagraph(snapshot.semantic_paragraphs);
      const paragraphTexts = (snapshot.semantic_paragraphs || []).map((paragraph) => asText(paragraph.text));
      const uiMatchesDurableProjection = ui.rows.length === paragraphTexts.length
        && ui.rows.every((row, index) => asText(row.text) === paragraphTexts[index]);
      if (
        continuousParagraph
        && Number(continuousParagraph.checkpoint_ids?.length) === 3
        && uiMatchesDurableProjection
        && !beforeEnd
      ) {
        beforeEnd = { snapshot, ui };
        scrollBefore = await scrollHistoricalPosition(page);
        break;
      }
      await delay(500);
    }
    if (!beforeEnd) throw new Error("continuous semantic paragraph did not stabilize before end");

    await waitFor(page, `document.querySelector('.transcript-scroll') !== null`, 5_000);
    const tailDeadline = Date.now() + 30_000;
    let afterAppend = null;
    while (Date.now() < tailDeadline) {
      const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
      const ui = await evaluate(page, captureLiveUi);
      const activePartial = asText(ui.active_partial_text);
      samples.push({
        at_ms: Date.now(),
        ui,
        semantic_paragraph_count: Array.isArray(snapshot.semantic_paragraphs) ? snapshot.semantic_paragraphs.length : 0,
        semantic_checkpoint_count: Array.isArray(snapshot.semantic_paragraphs)
          ? snapshot.semantic_paragraphs.reduce((sum, paragraph) => sum + (paragraph.checkpoint_ids?.length || 0), 0)
          : 0,
        active_partial_duplicate: activePartial !== ""
          && ui.rows.some((row) => asText(row.text) === activePartial),
      });
      const notice = await evaluate(page, () => document.querySelector('[data-testid="transcript-new-content"]')?.textContent?.trim() || "");
      if (snapshot.semantic_paragraphs?.length >= beforeEnd.snapshot.semantic_paragraphs.length + 1 && notice) {
        afterAppend = { snapshot, ui, notice };
        break;
      }
      await delay(500);
    }
    if (!afterAppend) throw new Error("new semantic paragraph notice did not appear after historical scroll");
    const scrollAfter = await evaluate(page, () => document.querySelector(".transcript-scroll")?.scrollTop ?? null);

    const beforeFullText = afterAppend.snapshot.semantic_paragraphs.map((paragraph) => asText(paragraph.text)).join("");
    await evaluate(page, () => document.querySelector(".end-meeting-button")?.click());
    await waitForMeetingEnded(baseUrl, meetingId, 30_000);
    await waitFor(page, `document.querySelectorAll('[role="tab"]').length === 4`, 30_000);
    await clickTab(page, "会议文字");
    const endedSnapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    await waitFor(page, `document.querySelectorAll('.review-transcript .transcript-segment').length === ${endedSnapshot.segments.length}`, 20_000);
    const endedUi = await evaluate(page, captureReviewUi);
    const afterFullText = endedSnapshot.segments.map((segment) => asText(segment.normalized_text || segment.text)).join("");
    const afterParagraphs = endedSnapshot.semantic_paragraphs || [];
    const continuousParagraph = findContinuousParagraph(beforeEnd.snapshot.semantic_paragraphs);
    const report = evaluateNext014Report({
      acceptance_scope: NEXT014_SCOPE,
      synthetic: true,
      acceptance_eligible: false,
      natural_multi_speaker_replacement: false,
      timeline_ms: SYNTHETIC_TIMELINE_MS,
      continuous_window_ms: CONTINUOUS_WINDOW_MS,
      checkpoint_count: endedSnapshot.segments.length,
      raw_checkpoint_count_before_end: beforeEnd.snapshot.segments.length,
      semantic_paragraphs_before_end: beforeEnd.snapshot.semantic_paragraphs,
      semantic_paragraphs_after_end: afterParagraphs,
      continuous_projection: {
        paragraph_count: beforeEnd.snapshot.semantic_paragraphs.filter((paragraph) =>
          paragraph.checkpoint_ids?.some((id) => id.startsWith("next014-continuous-")),
        ).length,
        paragraph_id: continuousParagraph?.paragraph_id || "",
        checkpoint_count: continuousParagraph?.checkpoint_ids?.length || 0,
        checkpoint_ids: continuousParagraph?.checkpoint_ids || [],
        duration_ms: Number(continuousParagraph?.end_ms || 0) - Number(continuousParagraph?.start_ms || 0),
      },
      active_partial_duplicate_count: samples.filter((sample) => sample.active_partial_duplicate).length,
      history_scroll: {
        before_top: scrollBefore?.scroll_top ?? null,
        after_top: scrollAfter,
        locked: scrollAfter === scrollBefore?.scroll_top,
        new_paragraph_count: parseNewParagraphCount(afterAppend.notice),
        notice: afterAppend.notice,
      },
      full_text_before_end: beforeFullText,
      full_text_after_end: afterFullText,
      ui_before_end: {
        row_count: beforeEnd.ui.rows.length,
        raw_checkpoint_row_count: beforeEnd.snapshot.segments.length,
        rows: beforeEnd.ui.rows,
      },
      ui_after_end: endedUi,
      after_end_projection_is_semantic: false,
      diagnostics,
    });
    const artifact = {
      ...report,
      runner: {
        command: "next014_continuous_semantic_gate.mjs",
        input_mode: "synthetic_fake_browser_microphone",
        natural_multi_speaker_replacement: false,
        checkpoint_fixture: buildNext014FixtureContract(),
        meeting_id_hash: shortHash(meetingId),
      },
      samples: samples.map((sample) => ({
        at_ms: sample.at_ms,
        semantic_paragraph_count: sample.semantic_paragraph_count,
        semantic_checkpoint_count: sample.semantic_checkpoint_count,
        ui_row_count: sample.ui.rows.length,
        active_partial_visible: Boolean(sample.ui.active_partial_text),
        active_partial_duplicate: sample.active_partial_duplicate,
      })),
      diagnostics,
      server_logs: { backend: backendLog(), frontend: frontendLog() },
    };
    await writeFile(path.join(artifactRoot, "report.json"), JSON.stringify(artifact, null, 2));
    await writeFile(path.join(artifactRoot, "snapshot-before-end.json"), JSON.stringify(beforeEnd.snapshot, null, 2));
    await writeFile(path.join(artifactRoot, "snapshot-after-end.json"), JSON.stringify(endedSnapshot, null, 2));
    console.log(JSON.stringify({
      verdict: report.verdict,
      acceptance_scope: report.acceptance_scope,
      acceptance_eligible: report.acceptance_eligible,
      artifact_root: artifactRoot,
      blockers: report.blockers,
    }, null, 2));
    assert.deepEqual(report.blockers, [], `NEXT-014 gate blockers: ${report.blockers.join(",")}`);
  } catch (error) {
    await mkdir(artifactRoot, { recursive: true });
    await writeFile(path.join(artifactRoot, "error-report.json"), JSON.stringify({
      schema_version: "next014-continuous-semantic-scroll-gate.error.v1",
      acceptance_scope: NEXT014_SCOPE,
      synthetic: true,
      acceptance_eligible: false,
      error: error instanceof Error ? error.message : String(error),
      diagnostics,
    }, null, 2));
    throw error;
  } finally {
    if (meetingId) await endMeetingIfStillLive(baseUrl, meetingId);
    await terminateChild(chrome);
    await terminateChild(frontend);
    await terminateChild(backend);
    await rm(chromeUserDataDir, { recursive: true, force: true });
  }
}

function findContinuousParagraph(paragraphs) {
  return (Array.isArray(paragraphs) ? paragraphs : []).find((paragraph) =>
    (paragraph.checkpoint_ids || []).some((id) => id === "next014-continuous-00"),
  ) || null;
}

function captureLiveUi() {
  const rows = [...document.querySelectorAll(".transcript-segment")].map((row) => ({
    id: row.getAttribute("data-segment-id") || "",
    text: row.querySelector(".segment-content p")?.textContent?.trim() || row.querySelector("p")?.textContent?.trim() || "",
  }));
  const scroll = document.querySelector(".transcript-scroll");
  return {
    rows,
    raw_checkpoint_row_count: rows.length,
    active_partial_text: document.querySelector(".active-partial p")?.textContent?.trim() || "",
    scroll_top: scroll?.scrollTop ?? null,
    scroll_height: scroll?.scrollHeight ?? null,
    client_height: scroll?.clientHeight ?? null,
  };
}

function captureReviewUi() {
  return {
    row_count: document.querySelectorAll(".review-transcript .transcript-segment").length,
    rows: [...document.querySelectorAll(".review-transcript .transcript-segment")].map((row) => ({
      id: row.getAttribute("data-segment-id") || "",
      text: row.querySelector(".segment-content p")?.textContent?.trim() || row.querySelector("p")?.textContent?.trim() || "",
    })),
  };
}

async function scrollHistoricalPosition(page) {
  return evaluate(page, () => {
    const node = document.querySelector(".transcript-scroll");
    if (!node) return null;
    if (node.scrollHeight - node.clientHeight < 120) {
      node.style.height = "140px";
      node.style.maxHeight = "140px";
      node.style.scrollBehavior = "auto";
    }
    node.scrollTop = 0;
    node.dispatchEvent(new Event("scroll", { bubbles: true }));
    return {
      scroll_top: node.scrollTop,
      scroll_height: node.scrollHeight,
      client_height: node.clientHeight,
    };
  });
}

function parseNewParagraphCount(notice) {
  const match = asText(notice).match(/有\s*(\d+)\s*段新内容/);
  return match ? Number(match[1]) : 0;
}

async function writeSyntheticWav(filePath, seconds) {
  const sampleRate = 16_000;
  const frameCount = sampleRate * seconds;
  const data = Buffer.alloc(frameCount * 2);
  for (let index = 0; index < frameCount; index += 1) {
    const sample = Math.round(Math.sin((2 * Math.PI * 440 * index) / sampleRate) * 8_000);
    data.writeInt16LE(sample, index * 2);
  }
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  await writeFile(filePath, Buffer.concat([header, data]));
}

function collectChildOutput(child) {
  let output = "";
  child.stdout?.on("data", (chunk) => { output += chunk.toString(); });
  child.stderr?.on("data", (chunk) => { output += chunk.toString(); });
  return () => output.slice(-4_000);
}

async function waitForChild(child, label, getOutput) {
  if (!child) return;
  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
  if (exitCode.code !== 0) {
    throw new Error(`${label} failed (${exitCode.signal || exitCode.code}): ${getOutput()}`);
  }
}

async function createCdpPage(debugPort, url, diagnostics, baseUrl) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, { method: "PUT" });
  if (!response.ok) throw new Error(`failed to create Chrome page: ${response.status}`);
  const target = await response.json();
  const socket = new WebSocket(target.webSocketDebuggerUrl);
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
      const request = pending.get(message.id);
      pending.delete(message.id);
      message.error ? request.reject(new Error(message.error.message)) : request.resolve(message.result || {});
      return;
    }
    for (const handler of handlers.get(message.method) || []) handler(message.params || {});
  });
  const urls = new Map();
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
        socket.send(JSON.stringify({ id, method, params }));
      });
    },
    on(method, handler) { handlers.set(method, [...(handlers.get(method) || []), handler]); },
  };
  page.on("Runtime.exceptionThrown", (params) => diagnostics.runtime_exceptions.push(params.exceptionDetails?.text || "runtime exception"));
  page.on("Runtime.consoleAPICalled", (params) => {
    if (params.type === "error") diagnostics.console_errors.push(
      (params.args || []).map((item) => item.value || item.description || "").join(" "),
    );
  });
  page.on("Network.requestWillBeSent", (params) => urls.set(params.requestId, params.request?.url || ""));
  page.on("Network.loadingFailed", (params) => {
    const failedUrl = urls.get(params.requestId) || "";
    if (failedUrl.endsWith("/favicon.ico") || (params.canceled === true && !failedUrl)) return;
    diagnostics.network_failures.push({ url: failedUrl, error: params.errorText });
  });
  page.on("Network.responseReceived", (params) => {
    if (Number(params.response?.status || 0) >= 500) diagnostics.http_5xx.push({ url: params.response.url, status: params.response.status });
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
  return result.result?.value;
}

async function waitFor(page, expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await page.send("Runtime.evaluate", { expression: `Boolean(${expression})`, returnByValue: true });
    if (result.result?.value) return;
    await delay(100);
  }
  throw new Error(`timed out waiting for ${expression}`);
}

async function waitForPreflightReady(page, timeoutMs) {
  try {
    await waitFor(page, `document.querySelector('.meeting-preflight-actions .primary-button')?.disabled === false`, timeoutMs);
  } catch (error) {
    const state = await evaluate(page, async () => {
      const [health, storage] = await Promise.all([
        fetch("/providers/health").then(async (response) => ({ status: response.status, body: await response.text() })),
        fetch("/v2/storage/preflight").then(async (response) => ({ status: response.status, body: await response.text() })),
      ]);
      const button = document.querySelector('.meeting-preflight-actions .primary-button');
      return {
        body: document.querySelector('.meeting-preflight-dialog')?.innerText || "",
        button_disabled: button?.disabled ?? null,
        consent_checked: document.querySelector('.preflight-consent input')?.checked ?? null,
        health,
        storage,
      };
    });
    throw new Error(`${error.message}; preflight_state=${JSON.stringify(state)}`);
  }
}

async function setViewport(page, { width, height }) {
  await page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
}

async function clickTab(page, label) {
  const clicked = await evaluate(page, (target) => {
    const tab = [...document.querySelectorAll('[role="tab"]')].find((item) => item.textContent?.trim() === target);
    if (!tab) return false;
    tab.click();
    return true;
  }, label);
  if (!clicked) throw new Error(`review tab not found: ${label}`);
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
    } catch {
      // Process startup is expected to race the first probe.
    }
    await delay(100);
  }
  throw new Error(`timed out waiting for ${url}`);
}

async function waitForMeetingEnded(baseUrl, meetingId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    if (snapshot.runtime?.phase === "ended") return;
    await delay(300);
  }
  throw new Error("meeting did not reach ended state");
}

async function endMeetingIfStillLive(baseUrl, meetingId) {
  try {
    const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    if (snapshot.runtime?.phase === "ended") return;
    await fetch(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/end`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "end_and_review" }),
    });
  } catch {
    // Preserve the original runner failure; cleanup is best effort.
  }
}

async function terminateChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill("SIGTERM");
  await new Promise((resolve) => {
    const timer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      resolve();
    }, 2_000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
  });
}

function shortHash(value) {
  let hash = 2166136261;
  for (const character of String(value)) hash = Math.imul(hash ^ character.charCodeAt(0), 16777619);
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await run();

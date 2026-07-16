// Workbench revision/evidence clickback smoke: verifies END-time transcript revisions
// do not break original evidence links in the UI. No remote ASR or LLM calls.
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-revision-clickback-data-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-revision-clickback-chrome-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8773");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9233");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", "workbench-revision-evidence-clickback"),
);

const sessionId = "revision_evidence_clickback_20260710";
const originalSegmentId = "raw_release_revision_clickback";
const revisionSegmentId = `${originalSegmentId}_rev1`;
const original_evidence_id = `asr_ev_${originalSegmentId}`;
const revision_evidence_id = `asr_ev_${revisionSegmentId}`;
const processes = [];
const cdpSockets = [];
let serverStdout = "";
let serverStderr = "";
let page;
let failed = false;
let stepIndex = 0;
const screenshots = [];
const checklist = [];

try {
  await mkdir(artifactRoot, { recursive: true });
  await writeFixtureSession(dataDir);

  const server = spawn("uvicorn", ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: backendDir,
    env: {
      ...process.env,
      MEETING_COPILOT_DATA_DIR: dataDir,
      LLM_GATEWAY_BASE_URL: "",
      LLM_GATEWAY_API_KEY: "",
      LLM_GATEWAY_MODEL: "",
      PYTHONPATH: ".:../../core",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  processes.push(server);
  server.stdout.on("data", (chunk) => { serverStdout += chunk.toString(); });
  server.stderr.on("data", (chunk) => { serverStderr += chunk.toString(); });
  await waitForHttp(`http://127.0.0.1:${port}/health`, 30000);

  const chrome = spawn(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${chromePort}`,
    `--user-data-dir=${chromeUserDataDir}`,
    "about:blank",
  ], { stdio: "ignore" });
  processes.push(chrome);
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`, 30000);

  page = await createCdpPage(
    chromePort,
    `http://127.0.0.1:${port}/workbench?demo=1&verify=revision-evidence-clickback`,
  );
  await waitForCdpExpression(page, `document.getElementById('btn-history') !== null`, 15000);
  await captureStep(page, "initial_page", "Workbench 已加载，准备打开历史会话");

  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(
    page,
    `document.querySelector('.history-modal-item[data-session-id=${JSON.stringify(sessionId)}]') !== null`,
    15000,
  );
  await evaluate(page, (sid) => {
    const button = document.querySelector(
      `.history-modal-item[data-session-id="${sid}"] button[data-action="open"]`,
    );
    if (!button) throw new Error(`history modal open action missing: ${sid}`);
    button.click();
  }, sessionId);

  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes(${JSON.stringify(sessionId)})`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll('.transcript-segment[data-status="corrected"]').length >= 1`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll('.evidence-link').length >= 1`, 15000);
  await captureStep(page, "revision_loaded", "含 transcript_revision 的会话已加载，建议证据链接可见");

  const initialUiState = await evaluate(page, (originalEvidenceId, revisionEvidenceId, expectedOriginalSegmentId, expectedRevisionSegmentId) => {
    const correctedSegment = Array.from(document.querySelectorAll(".transcript-segment[data-status='corrected']"))
      .find((el) => el.dataset.segmentId === expectedOriginalSegmentId);
    const originalDetails = document.querySelector("details.original-asr-text");
    return {
    canonical_utterance_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
    utterance_count: document.querySelectorAll(".utterance").length,
    corrected_segment_visible: Boolean(correctedSegment),
    corrected_segment_id: correctedSegment?.dataset.segmentId || "",
    corrected_source_segment_id: correctedSegment?.dataset.sourceSegmentId || "",
    corrected_status: correctedSegment?.dataset.status || "",
    original_raw_toggle_visible: Boolean(originalDetails),
    original_raw_open_initially: Boolean(originalDetails?.open),
    suggestion_count: document.querySelectorAll("[data-card-kind='suggestion']").length,
    evidence_link_count: document.querySelectorAll(".evidence-link").length,
    original_evidence_link_visible: Array.from(document.querySelectorAll(".evidence-link"))
      .some((el) => el.dataset.evidenceId === originalEvidenceId),
    revision_evidence_link_visible: Array.from(document.querySelectorAll(".evidence-link"))
      .some((el) => el.dataset.evidenceId === revisionEvidenceId),
    transcript_text: document.getElementById("transcript-stream")?.innerText || "",
    suggestions_text: document.getElementById("suggestions-panel")?.innerText || "",
    };
  }, original_evidence_id, revision_evidence_id, originalSegmentId, revisionSegmentId);
  assert(initialUiState.utterance_count >= 1, `missing canonical transcript row: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.corrected_segment_visible, `corrected canonical transcript segment missing: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.corrected_segment_id === originalSegmentId, `canonical target segment mismatch: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.corrected_source_segment_id === revisionSegmentId, `canonical source segment mismatch: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.corrected_status === "corrected", `canonical correction status missing: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.original_raw_toggle_visible, `original ASR disclosure missing: ${JSON.stringify(initialUiState)}`);
  assert(!initialUiState.original_raw_open_initially, `original ASR disclosure unexpectedly open: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.transcript_text.includes("先灰度"), `revision transcript not visible: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.original_evidence_link_visible, `missing original evidence link: ${JSON.stringify(initialUiState)}`);
  assert(initialUiState.revision_evidence_link_visible, `missing revision evidence link: ${JSON.stringify(initialUiState)}`);

  await evaluate(page, (evidenceId) => {
    const button = Array.from(document.querySelectorAll(".evidence-link"))
      .find((el) => el.dataset.evidenceId === evidenceId);
    if (!button) throw new Error(`evidence link missing: ${evidenceId}`);
    button.click();
  }, original_evidence_id);
  await waitForCdpExpression(page, `document.querySelectorAll(".transcript-segment.evidence-focus").length >= 1`, 5000);
  const originalClickback = await focusedEvidenceState(page, originalSegmentId);
  assert(originalClickback.focused_segment_id === originalSegmentId, `wrong original focus: ${JSON.stringify(originalClickback)}`);
  assert(originalClickback.focused_source_segment_id === revisionSegmentId, `wrong original source focus: ${JSON.stringify(originalClickback)}`);
  assert(originalClickback.raw_details_open, `original ASR disclosure did not open: ${JSON.stringify(originalClickback)}`);
  assert(originalClickback.raw_details_text.includes("先恢度"), `original raw ASR text not visible: ${JSON.stringify(originalClickback)}`);
  await captureStep(page, "original_evidence_clickback", "点击原始 evidence 后，修正文稿定位并展开原始 ASR");

  await evaluate(page, (evidenceId) => {
    const button = Array.from(document.querySelectorAll(".evidence-link"))
      .find((el) => el.dataset.evidenceId === evidenceId);
    if (!button) throw new Error(`revision evidence link missing: ${evidenceId}`);
    button.click();
  }, revision_evidence_id);
  await waitForCdpExpression(
    page,
    `Array.from(document.querySelectorAll(".transcript-segment.evidence-focus")).some((el) => el.dataset.sourceSegmentId === ${JSON.stringify(revisionSegmentId)})`,
    5000,
  );
  const revisionClickback = await focusedEvidenceState(page, revisionSegmentId);
  assert(revisionClickback.focused_source_segment_id === revisionSegmentId, `wrong revision focus: ${JSON.stringify(revisionClickback)}`);
  assert(revisionClickback.focused_text.includes("灰度"), `revision corrected text not visible: ${JSON.stringify(revisionClickback)}`);
  await captureStep(page, "revision_evidence_clickback", "点击修正 evidence 后，修正文稿行高亮");

  const revisionRelationship = await evaluate(page, (revisionEvidenceId, expectedOriginalSegmentId, expectedRevisionSegmentId) => {
    const revision = Array.from(document.querySelectorAll(".evidence-link"))
      .find((el) => el.dataset.evidenceId === revisionEvidenceId);
    const revisionRow = Array.from(document.querySelectorAll(".transcript-segment[data-status='corrected']"))
      .find((el) => el.dataset.sourceSegmentId === expectedRevisionSegmentId);
    return {
      revision_evidence_id: revisionEvidenceId,
      revision_link_visible: Boolean(revision),
      revision_link_text: revision?.innerText || "",
      revision_row_visible: Boolean(revisionRow),
      revision_row_text: revisionRow?.closest(".transcript-paragraph")?.innerText || revisionRow?.innerText || "",
      revision_relationship_visible: Boolean(
        revisionRow?.dataset.revisionOf === expectedOriginalSegmentId
        && revisionRow?.dataset.sourceSegmentId === expectedRevisionSegmentId,
      ),
    };
  }, revision_evidence_id, originalSegmentId, revisionSegmentId);
  assert(revisionRelationship.revision_link_visible, `revision evidence link not visible: ${JSON.stringify(revisionRelationship)}`);
  assert(revisionRelationship.revision_link_text.includes("灰度"), `revision text not visible: ${JSON.stringify(revisionRelationship)}`);
  assert(revisionRelationship.revision_row_visible, `revision transcript row not visible: ${JSON.stringify(revisionRelationship)}`);
  assert(revisionRelationship.revision_relationship_visible, `revision relationship not visible: ${JSON.stringify(revisionRelationship)}`);
  await captureStep(page, "revision_relationship_visible", "revision evidence 仍可见，页面保留修正文案关系");

  const apiEvidence = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/llm-execution-previews`).then((r) => r.json());
  const originalPreview = (apiEvidence.execution_previews || [])
    .find((preview) => (preview.evidence_span_ids || []).includes(original_evidence_id));
  assert(originalPreview, `original evidence preview missing: ${JSON.stringify(apiEvidence)}`);
  assert((originalPreview.evidence_context || "").includes("先恢度"), `original evidence context lost: ${JSON.stringify(originalPreview)}`);

  const report = {
    status: "go_revision_evidence_clickback",
    session_id: sessionId,
    original_segment_id: originalSegmentId,
    revision_segment_id: revisionSegmentId,
    original_evidence_id,
    revision_evidence_id,
    ui_state: initialUiState,
    original_clickback: originalClickback,
    revision_clickback: revisionClickback,
    revision_relationship: revisionRelationship,
    api_execution_preview_count: apiEvidence.execution_preview_count,
    api_original_evidence_context: originalPreview.evidence_context,
    screenshots,
    checklist,
    screenshot_count: screenshots.length,
    remote_llm_called: false,
    remote_asr_called: false,
  };
  await writeFile(path.join(artifactRoot, "revision_evidence_clickback_report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (err) {
  failed = true;
  if (page) {
    try { await captureStep(page, "failure_state", "失败现场截图"); } catch {}
  }
  const state = page ? await safeReadPageState(page).catch((stateErr) => ({ state_error: stateErr.message })) : {};
  const report = {
    status: "blocked_revision_evidence_clickback",
    error: err.message,
    page_state: state,
    screenshots,
    checklist,
    server_stdout_tail: tailText(serverStdout, 4000),
    server_stderr_tail: tailText(serverStderr, 4000),
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "revision_evidence_clickback_error.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
  await removeTempDirWithRetry(dataDir);
  await removeTempDirWithRetry(chromeUserDataDir);
  process.exit(failed ? 1 : 0);
}

async function writeFixtureSession(rootDir) {
  const recordDir = path.join(rootDir, "live_asr_sessions");
  await mkdir(recordDir, { recursive: true });
  const record = fixtureRecord();
  await writeFile(path.join(recordDir, `${sessionId}.json`), JSON.stringify(record, null, 2));
}

function fixtureRecord() {
  const originalQuote = "接口先恢度百分之五，如果 P 九九延迟超过九百毫秒";
  const revisionQuote = "接口先灰度百分之五，如果 P99延迟超过九百毫秒就回滚";
  const originalEvidence = {
    id: original_evidence_id,
    segment_id: originalSegmentId,
    start_ms: 0,
    end_ms: 2400,
    quote: originalQuote,
    status: "active",
  };
  const revisionEvidence = {
    id: revision_evidence_id,
    segment_id: revisionSegmentId,
    revision_of: original_evidence_id,
    start_ms: 0,
    end_ms: 2600,
    quote: revisionQuote,
    status: "active",
  };
  const supersededEvidence = {
    ...originalEvidence,
    status: "superseded",
    replaced_by: revision_evidence_id,
  };
  return {
    session_id: sessionId,
    provider: "fixture_revision_evidence",
    provider_mode: "real",
    is_mock: false,
    asr_fallback_used: false,
    degradation_reasons: [],
    audio_source: "simulated_realtime_wav",
    input_source: "simulated_realtime_wav",
    source: "live_asr_stream",
    trace_kind: "live_event",
    asr_semantic_quality: {
      schema_version: "asr_semantic_quality.v1",
      status: "passed",
      blocker: null,
      matched_entities: ["P99", "灰度"],
      matched_entity_groups: ["latency", "release"],
      missing_entity_groups: [],
      technical_entity_hit_count: 2,
      technical_group_hit_count: 2,
      gibberish_score: 0,
      reason: "fixture_revision_clickback",
    },
    auto_suggestion: { paused: true, updated_at_ms: 2600 },
    events: [
      {
        id: `transcript_final:${originalSegmentId}`,
        event_type: "transcript_final",
        at_ms: 2500,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 1,
        payload: {
          segment_id: originalSegmentId,
          start_ms: 0,
          end_ms: 2400,
          text: originalQuote,
          normalized_text: originalQuote,
          confidence: 0.82,
          is_final: true,
          evidence_spans: [originalEvidence],
        },
      },
      {
        id: `state:asr_state_event_${originalSegmentId}`,
        event_type: "state_event",
        at_ms: 2500,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 2,
        payload: {
          event_id: `asr_state_event_${originalSegmentId}`,
          target_type: "Risk",
          target_id: `asr_risk_${originalSegmentId}`,
          state_event_type: "created",
          evidence_span_ids: [original_evidence_id],
          state_item: {
            id: `asr_risk_${originalSegmentId}`,
            description: originalQuote,
            status: "open",
            evidence_span_ids: [original_evidence_id],
            source: "live_asr_stream",
            state_origin: "fixture_revision_clickback",
          },
        },
      },
      {
        id: `suggestion_candidate:asr_state_event_${originalSegmentId}`,
        event_type: "suggestion_candidate_event",
        at_ms: 2500,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 3,
        payload: {
          candidate_id: `asr_suggestion_candidate_asr_state_event_${originalSegmentId}`,
          target_type: "Risk",
          target_id: `asr_risk_${originalSegmentId}`,
          gap_rule_id: "risk.rollback.validation",
          suggested_prompt: "确认风险触发条件、回滚动作和监控指标是否明确。",
          source_event_ids: [`asr_state_event_${originalSegmentId}`],
          evidence_span_ids: [original_evidence_id],
          segment_batch: [originalSegmentId],
          llm_call_status: "not_called",
          card_status: "not_created",
          confidence: 0.72,
          confidence_level: "medium",
          degradation_reasons: ["asr_needs_review"],
          source: "live_asr_stream",
        },
      },
      {
        id: `llm_request_draft:asr_state_event_${originalSegmentId}`,
        event_type: "llm_request_draft_event",
        at_ms: 2500,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 4,
        payload: {
          request_id: `asr_llm_request_draft_asr_state_event_${originalSegmentId}`,
          request_type: "llm_suggestion_card_draft",
          target_candidate_id: `asr_suggestion_candidate_asr_state_event_${originalSegmentId}`,
          target_type: "Risk",
          target_id: `asr_risk_${originalSegmentId}`,
          gap_rule_id: "risk.rollback.validation",
          prompt_version: "not-called",
          model: "not-called",
          llm_call_status: "not_called",
          card_status: "not_created",
          schema_status: "not_generated",
          suggested_prompt: "确认风险触发条件、回滚动作和监控指标是否明确。",
          input_summary: `Risk asr_risk_${originalSegmentId} from ${originalSegmentId} using ${original_evidence_id}`,
          source_event_ids: [`asr_state_event_${originalSegmentId}`],
          evidence_span_ids: [original_evidence_id],
          segment_batch: [originalSegmentId],
          candidate_confidence: 0.72,
          candidate_confidence_level: "medium",
          candidate_degradation_reasons: ["asr_needs_review"],
          request_origin: "fixture_revision_clickback",
          source: "live_asr_stream",
        },
      },
      {
        id: `transcript_revision:${revisionSegmentId}`,
        event_type: "transcript_revision",
        at_ms: 2600,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 5,
        payload: {
          segment_id: revisionSegmentId,
          start_ms: 0,
          end_ms: 2600,
          text: revisionQuote,
          normalized_text: revisionQuote,
          confidence: 0.88,
          is_final: true,
          evidence_spans: [revisionEvidence],
          revision_of: originalSegmentId,
          supersedes_segment_id: originalSegmentId,
          superseded_evidence_spans: [supersededEvidence],
        },
      },
      {
        id: "evaluation:asr_stream_summary",
        event_type: "evaluation_summary",
        at_ms: 2600,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence: 6,
        payload: {
          source: "live_asr_stream",
          provider: "fixture_revision_evidence",
          is_mock: false,
          passes_minimum_gate: true,
          partial_event_count: 0,
          final_event_count: 1,
          revision_event_count: 1,
          error_event_count: 0,
          end_of_stream_event_count: 1,
        },
      },
    ],
    suggestion_cards: [
      {
        card_id: "card_original_evidence_clickback",
        suggestion_text: "建议现场确认 P99 阈值、回滚动作和负责人。",
        confidence: 0.86,
        trigger_reason: "实时识别到风险阈值但回滚动作需要确认",
        evidence_span_ids: [original_evidence_id],
        evidence_spans: [originalEvidence],
        source_event_ids: [`asr_state_event_${originalSegmentId}`],
      },
      {
        card_id: "card_revision_context_visible",
        suggestion_text: "修正文本显示需要补充回滚条件。",
        confidence: 0.8,
        trigger_reason: "会后修正补齐回滚动作",
        evidence_span_ids: [revision_evidence_id],
        evidence_spans: [revisionEvidence],
        source_event_ids: [`transcript_revision:${revisionSegmentId}`],
      },
    ],
    approach_cards: [],
    minutes: {
      minutes_md: "## 会议纪要\n- 原始识别保留，修正文案以 revision 追加。\n",
    },
  };
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }
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
  const expression = `(${fn.toString()})(...${JSON.stringify(args)})`;
  const result = await cdpPage.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "browser eval failed");
  return result.result.value;
}

async function waitForCdpExpression(cdpPage, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const check = await cdpPage.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (check.result?.value) return;
    await delay(180);
  }
  throw new Error(`timed out waiting for: ${expression}`);
}

async function focusedEvidenceState(cdpPage, preferredSegmentId = "") {
  return evaluate(cdpPage, (segmentId) => {
    const focusedItems = Array.from(document.querySelectorAll(".transcript-segment.evidence-focus, .utterance.evidence-focus"));
    const focused = focusedItems.find((item) => (
      item.dataset.segmentId === segmentId || item.dataset.sourceSegmentId === segmentId
    )) || focusedItems[0] || null;
    const originalDetails = document.querySelector("details.original-asr-text");
    return {
      focused_segment_id: focused?.dataset.segmentId || "",
      focused_source_segment_id: focused?.dataset.sourceSegmentId || "",
      focused_evidence_ids: (focused?.dataset.evidenceIds || "").split(" ").filter(Boolean),
      focused_text: focused?.innerText || "",
      raw_details_open: Boolean(originalDetails?.open),
      raw_details_text: originalDetails?.innerText || "",
    };
  }, preferredSegmentId);
}

async function safeReadPageState(cdpPage) {
  return evaluate(cdpPage, () => ({
    title: document.title,
    sessionMeta: document.getElementById("session-meta")?.innerText,
    sysStatus: document.getElementById("sys-status")?.innerText,
    transcript: document.getElementById("transcript-stream")?.innerText,
    suggestions: document.getElementById("suggestions-panel")?.innerText,
    utterances: document.querySelectorAll(".utterance").length,
    suggestionCards: document.querySelectorAll("[data-card-kind='suggestion']").length,
    evidenceLinks: document.querySelectorAll(".evidence-link").length,
  }));
}

async function captureStep(cdpPage, step, description) {
  const fileName = `${String(++stepIndex).padStart(2, "0")}-${step}.png`;
  const screenshotPath = path.join(artifactRoot, fileName);
  const screenshot = await cdpPage.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(screenshotPath, Buffer.from(screenshot.data, "base64"));
  const state = await safeReadPageState(cdpPage).catch((err) => ({ state_error: err.message }));
  const entry = {
    order: stepIndex,
    step,
    description,
    status: "passed",
    screenshot_path: screenshotPath,
    state,
  };
  screenshots.push({ step, screenshot_path: screenshotPath });
  checklist.push(entry);
  return entry;
}

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

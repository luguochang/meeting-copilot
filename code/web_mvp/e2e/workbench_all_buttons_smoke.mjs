// Workbench all-buttons smoke: no-cost browser E2E for import/export/clickback/history/delete.
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { createServer } from "node:http";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-workbench-all-buttons-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-chrome-all-buttons-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8771");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9231");
const fakeLlmPort = Number(process.env.MEETING_COPILOT_E2E_FAKE_LLM_PORT || "18771");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const uploadAudioPath = path.resolve(
  process.env.MEETING_COPILOT_UPLOAD_AUDIO || path.join(repoRoot, "code", "asr_runtime", "outputs", "simulated-release-review.16k.wav"),
);
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", "workbench-all-buttons-smoke"),
);
const DESKTOP_VIEWPORT = { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false };
const MOBILE_VIEWPORT = { width: 375, height: 812, deviceScaleFactor: 1, mobile: true };

const processes = [];
const cdpSockets = [];
let fakeLlmServer = null;
let fakeLlmRequestCount = 0;
let serverStdout = "";
let serverStderr = "";
let page;
let failed = false;
let stepIndex = 0;
let transcriptProjectionProbe = null;
let reconciledFinalProbe = null;
let providerErrorPreservationProbe = null;
let recordingDraftFirstEventProbe = null;
let snapshotRevisionOrderProbe = null;
let revisionSupplementProbe = null;
let historySelectionRaceProbe = null;
let historyWorkspaceProbe = null;
let historySessionOperationProbe = null;
let transcriptNamespaceCollisionProbe = null;
let recordingFailureRestoreProbe = null;
let suggestionSemanticDedupeProbe = null;
let mobileLayoutProbe = null;
let transcriptScrollFollowProbe = null;
let reloadRecoveryProbe = null;
const networkRequestUrls = new Map();
const browserDiagnostics = {
  runtime_exceptions: [],
  error_console: [],
  network_loading_failed: [],
  http_5xx: [],
  allowlisted_network_failures: [],
};
const expectedNetworkFailureAllowlist = [
  {
    name: "chrome_favicon_probe_aborted",
    matches: (entry) => entry.url.endsWith("/favicon.ico") && entry.error_text === "net::ERR_ABORTED",
  },
  {
    name: "intentional_session_operation_probe_abort",
    matches: (entry) => entry.canceled === true
      && entry.error_text === "net::ERR_ABORTED"
      && new URL(entry.url).pathname === "/live/asr/sessions",
  },
];
const screenshots = [];
const checklist = [];
const focusFilterCoverage = [
  { focus_type: "DecisionCandidate", label: "决定了什么", coverage: "pending", evidence: null },
  { focus_type: "ActionItem", label: "待办事项", coverage: "pending", evidence: null },
  { focus_type: "Risk", label: "风险提醒", coverage: "pending", evidence: null },
  { focus_type: "OpenQuestion", label: "待确认问题", coverage: "pending", evidence: null },
  { focus_type: "all", label: "显示全部", coverage: "pending", evidence: null },
];
const overviewJumpCoverage = [
  { overview_target: "transcript", label: "文字记录", coverage: "pending", evidence: null },
  { overview_target: "reminders", label: "实时提醒", coverage: "pending", evidence: null },
  { overview_target: "suggestions", label: "AI 建议", coverage: "pending", evidence: null },
  { overview_target: "approach", label: "方案分析", coverage: "pending", evidence: null },
  { overview_target: "audio", label: "录音保存", coverage: "pending", evidence: null },
  { overview_target: "minutes", label: "会后复盘", coverage: "pending", evidence: null },
];
const overviewJumpFocusStates = [];
const buttonCoverage = [
  { button_id: "btn-record", label: "开始会议", coverage: "covered_by_real_browser_mic_e2e", evidence: "workbench_browser_live_mic_verify.mjs" },
  { button_id: "btn-stop", label: "结束会议", coverage: "covered_by_real_browser_mic_e2e", evidence: "workbench_browser_live_mic_verify.mjs" },
  { button_id: "btn-load", label: "试用示例", coverage: "pending", evidence: null },
  { button_id: "btn-upload", label: "导入录音", coverage: "pending", evidence: null },
  { button_id: "btn-history", label: "历史记录", coverage: "pending", evidence: null },
  { button_id: "btn-settings", label: "设置", coverage: "pending", evidence: null },
  { button_id: "btn-organize", label: "整理会议", coverage: "pending", evidence: null },
  { button_id: "btn-live", label: "刷新实时文字", coverage: "pending", evidence: null },
  { button_id: "btn-delete", label: "删除本次会议", coverage: "pending", evidence: null },
  { button_id: "btn-cards", label: "生成会议建议", coverage: "pending", evidence: null },
  { button_id: "btn-approach", label: "生成方案分析", coverage: "pending", evidence: null },
  { button_id: "btn-minutes", label: "生成会议纪要", coverage: "pending", evidence: null },
  { button_id: "btn-export-transcript", label: "导出文字稿", coverage: "pending", evidence: null },
  { button_id: "btn-export-minutes", label: "导出纪要", coverage: "pending", evidence: null },
  { button_id: "btn-export-audio", label: "导出录音", coverage: "pending", evidence: null },
  { button_id: "btn-auto-suggestion-toggle", label: "暂停/恢复 AI 建议", coverage: "pending", evidence: null },
];

try {
  await mkdir(artifactRoot, { recursive: true });
  fakeLlmServer = await startFakeLlmGateway(fakeLlmPort);
  const server = spawn("uvicorn", ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)], {
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

  page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/workbench`);
  await waitForCdpExpression(page, `document.getElementById('btn-upload') !== null`, 15000);
  const demoLoadState = await evaluate(page, () => ({
    disclosureHidden: document.querySelector(".demo-disclosure")?.hidden === true,
    buttonVisible: document.getElementById("btn-load")?.offsetParent !== null,
  }));
  assert(demoLoadState.disclosureHidden && !demoLoadState.buttonVisible, `btn-load must remain hidden outside demo mode: ${JSON.stringify(demoLoadState)}`);
  markButtonCovered("btn-load", "hidden_demo_only", "initial_page_demo_disclosure_hidden");
  await captureStep(page, "initial_page", "Workbench 页面加载，开始会议、导入录音、历史记录等主按钮可见");

  await evaluate(page, () => document.getElementById("btn-history").click());
  markButtonCovered("btn-history", "clicked", "history_open_without_current_session");
  await waitForCdpExpression(page, `document.getElementById("history-modal").hidden === false`, 5000);
  historyWorkspaceProbe = await evaluate(page, () => ({
    current_session: currentSession,
    modal_hidden: document.getElementById("history-modal")?.hidden === true,
    modal_visible: window.getComputedStyle(document.getElementById("history-modal")).display !== "none",
    history_visible: document.getElementById("history-modal-list")?.offsetParent !== null,
  }));
  assert(historyWorkspaceProbe.current_session === null, `history probe requires no current session: ${JSON.stringify(historyWorkspaceProbe)}`);
  assert(!historyWorkspaceProbe.modal_hidden && historyWorkspaceProbe.modal_visible && historyWorkspaceProbe.history_visible, `history button did not reveal history modal: ${JSON.stringify(historyWorkspaceProbe)}`);
  await evaluate(page, () => document.getElementById("btn-close-history").click());
  await waitForCdpExpression(page, `document.getElementById("history-modal").hidden === true`, 5000);

  await waitForCdpExpression(page, `document.getElementById("btn-settings") !== null`, 5000);
  await evaluate(page, () => document.getElementById("btn-settings").click());
  await waitForCdpExpression(page, `document.getElementById("settings-modal").hidden === false`, 5000);
  const settingsProbe = await evaluate(page, () => ({
    modal_visible: window.getComputedStyle(document.getElementById("settings-modal")).display !== "none",
    api_key_field_present: document.getElementById("setting-llm-api-key") !== null,
  }));
  assert(settingsProbe.modal_visible && !settingsProbe.api_key_field_present, `settings modal is unsafe or hidden: ${JSON.stringify(settingsProbe)}`);
  markButtonCovered("btn-settings", "clicked_open_and_close", "settings_modal_non_secret_preferences");
  await evaluate(page, () => document.getElementById("btn-close-settings").click());
  await waitForCdpExpression(page, `document.getElementById("settings-modal").hidden === true`, 5000);

  recordingDraftFirstEventProbe = await evaluate(page, () => {
    const eventCases = [
      { name: "partial", event: { event_type: "transcript_partial", segment_id: "new_partial", text: "这是新录音第一条足够完整的临时识别文字", confidence: 0.95 } },
      { name: "final", event: { event_type: "transcript_final", segment_id: "new_final", text: "这是新录音第一条最终识别文字" } },
      { name: "revision", event: { event_type: "transcript_revision", id: "new_revision", revision_of: "new_revision_target", text: "这是新录音第一条修正文字" } },
    ];
    return eventCases.map(({ name, event }) => {
      prepareNewSession();
      currentSession = `preserved_${name}`;
      currentEvents = [{ event_type: "transcript_final", segment_id: `old_${name}`, text: "上一场会议保留文字" }];
      renderTranscriptAndCandidates(currentEvents);
      preserveSessionBeforeRecording();
      startRecordingDraftSession(`recording_${name}`);
      currentAutoSuggestionStatus = { paused: true };
      currentEvents.push(event);
      const result = appendLiveEvent(event);
      const report = {
        name,
        visible_text_changed: result.visibleTextChanged,
        visible_text: document.getElementById("transcript-stream")?.innerText || "",
        claimed: recordingDraftHasClaimedView,
      };
      resetSessionView();
      preservedSessionBeforeRecording = null;
      return report;
    });
  });
  recordingDraftFirstEventProbe.forEach((entry) => {
    assert(entry.visible_text_changed, `first ${entry.name} should report visible text: ${JSON.stringify(recordingDraftFirstEventProbe)}`);
    assert(entry.claimed, `first ${entry.name} should claim draft view: ${JSON.stringify(recordingDraftFirstEventProbe)}`);
    assert(entry.visible_text.includes("新录音第一条"), `first ${entry.name} was cleared from draft view: ${JSON.stringify(recordingDraftFirstEventProbe)}`);
    assert(!entry.visible_text.includes("上一场会议保留文字"), `preserved text remained after first ${entry.name}: ${JSON.stringify(recordingDraftFirstEventProbe)}`);
  });

  snapshotRevisionOrderProbe = await evaluate(page, () => {
    prepareNewSession();
    const events = [
      { event_type: "transcript_final", segment_id: "segment_a", text: "A 原文" },
      { event_type: "transcript_final", segment_id: "segment_b", text: "B" },
      { event_type: "transcript_revision", id: "revision_a", revision_of: "segment_a", text: "A 修正" },
    ];
    renderTranscriptAndCandidates(events);
    const texts = Array.from(document.querySelectorAll("#transcript-stream .transcript-text")).map((element) => element.textContent);
    resetSessionView();
    return texts;
  });
  assert(JSON.stringify(snapshotRevisionOrderProbe) === JSON.stringify(["A 修正", "B"]), `snapshot revision order drifted from realtime replacement: ${JSON.stringify(snapshotRevisionOrderProbe)}`);

  revisionSupplementProbe = await evaluate(page, () => {
    const revision = { event_type: "transcript_revision", id: "unresolved_revision_probe", text: "无法定位原段落，但这条修正仍需显示" };
    prepareNewSession();
    currentSession = "revision_supplement_live";
    currentAutoSuggestionStatus = { paused: true };
    currentEvents.push(revision);
    appendLiveEvent(revision);
    const live = {
      text: document.getElementById("transcript-stream")?.innerText || "",
      key: document.querySelector(".transcript-segment[data-revision-supplement='true']")?.dataset.transcriptSegmentId || "",
    };
    prepareNewSession();
    renderTranscriptAndCandidates([revision]);
    const snapshot = {
      text: document.getElementById("transcript-stream")?.innerText || "",
      key: document.querySelector(".transcript-segment[data-revision-supplement='true']")?.dataset.transcriptSegmentId || "",
    };
    resetSessionView();
    return { live, snapshot };
  });
  assert(revisionSupplementProbe.live.text.includes("修正补充") && revisionSupplementProbe.live.text.includes("无法定位原段落"), `live unresolved revision disappeared: ${JSON.stringify(revisionSupplementProbe)}`);
  assert(revisionSupplementProbe.snapshot.text.includes("修正补充") && revisionSupplementProbe.snapshot.text.includes("无法定位原段落"), `snapshot unresolved revision disappeared: ${JSON.stringify(revisionSupplementProbe)}`);
  assert(revisionSupplementProbe.live.key === revisionSupplementProbe.snapshot.key && revisionSupplementProbe.live.key.startsWith("revision-supplement:"), `revision supplement key is not stable: ${JSON.stringify(revisionSupplementProbe)}`);

  transcriptNamespaceCollisionProbe = await evaluate(page, () => {
    prepareNewSession();
    const events = [
      { event_type: "transcript_final", segment_id: "revision-supplement:r1", text: "合法普通段落" },
      { event_type: "transcript_revision", id: "r1", text: "无目标修正补充" },
    ];
    renderTranscriptAndCandidates(events);
    const rows = Array.from(document.querySelectorAll("#transcript-stream .transcript-segment[data-transcript-segment-id]")).map((row) => ({
      projection_key: row.dataset.transcriptSegmentId || "",
      source_segment_id: row.dataset.segmentId || "",
      revision_supplement: row.dataset.revisionSupplement === "true",
      text: row.querySelector(".transcript-text")?.textContent || "",
    }));
    resetSessionView();
    return { rows };
  });
  assert(transcriptNamespaceCollisionProbe.rows.length === 2, `segment and revision supplement collided: ${JSON.stringify(transcriptNamespaceCollisionProbe)}`);
  assert(transcriptNamespaceCollisionProbe.rows.some((row) => row.text === "合法普通段落" && row.source_segment_id === "revision-supplement:r1"), `legal segment id lost traceability: ${JSON.stringify(transcriptNamespaceCollisionProbe)}`);
  assert(transcriptNamespaceCollisionProbe.rows.some((row) => row.text === "无目标修正补充" && row.source_segment_id === "r1" && row.revision_supplement), `revision supplement lost traceability: ${JSON.stringify(transcriptNamespaceCollisionProbe)}`);
  assert(new Set(transcriptNamespaceCollisionProbe.rows.map((row) => row.projection_key)).size === 2, `DOM projection keys still collide: ${JSON.stringify(transcriptNamespaceCollisionProbe)}`);

  historySelectionRaceProbe = await evaluate(page, async () => {
    const originalFetch = window.fetch;
    const pending = new Map();
    window.fetch = (url, options = {}) => {
      const path = String(url);
      const match = path.match(/\/live\/asr\/sessions\/([^/]+)\/events$/);
      if (!match) {
        return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => ({ status: null }) });
      }
      const sid = decodeURIComponent(match[1]);
      return new Promise((resolve) => {
        pending.set(sid, { resolve, signal: options.signal || null });
      });
    };
    const responseFor = (sid) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        events: [{ event_type: "transcript_final", segment_id: `${sid}_segment`, text: `${sid} text` }],
        event_source: { provider: "local_mock_asr", provider_mode: "mock", is_mock: true },
      }),
    });
    try {
      const slowOpen = openHistorySession("slow_history");
      await Promise.resolve();
      const fastOpen = openHistorySession("fast_history");
      await Promise.resolve();
      pending.get("fast_history").resolve(responseFor("fast_history"));
      await fastOpen;
      const slowSignalAborted = pending.get("slow_history").signal?.aborted === true;
      pending.get("slow_history").resolve(responseFor("slow_history"));
      await slowOpen;
      return {
        slow_signal_aborted: slowSignalAborted,
        current_session: currentSession,
        visible_text: document.getElementById("transcript-stream")?.innerText || "",
      };
    } finally {
      window.fetch = originalFetch;
      resetSessionView();
    }
  });
  assert(historySelectionRaceProbe.slow_signal_aborted, `older history request was not aborted: ${JSON.stringify(historySelectionRaceProbe)}`);
  assert(historySelectionRaceProbe.current_session === "fast_history", `older history response overwrote latest selection: ${JSON.stringify(historySelectionRaceProbe)}`);
  assert(historySelectionRaceProbe.visible_text.includes("fast_history text"), `latest history target is not visible: ${JSON.stringify(historySelectionRaceProbe)}`);

  historySessionOperationProbe = await evaluate(page, async () => {
    const originalFetch = window.fetch;
    const originalGetUserMedia = navigator.mediaDevices.getUserMedia;
    const pendingHistory = new Map();
    const abortError = () => new DOMException("Aborted", "AbortError");
    window.fetch = (url, options = {}) => {
      const requestUrl = String(url);
      const historyMatch = requestUrl.match(/\/live\/asr\/sessions\/([^/]+)\/events$/);
      if (historyMatch) {
        const sid = decodeURIComponent(historyMatch[1]);
        return new Promise((resolve, reject) => {
          const signal = options.signal || null;
          if (signal?.aborted) return reject(abortError());
          signal?.addEventListener("abort", () => reject(abortError()), { once: true });
          pendingHistory.set(sid, { resolve, signal });
        });
      }
      if (requestUrl.includes("/live/asr/transcribe-file/sessions")) {
        return Promise.resolve({ ok: false, status: 499, statusText: "probe stopped", json: async () => ({ detail: "probe stopped" }) });
      }
      return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: async () => ({ sessions: [] }) });
    };
    navigator.mediaDevices.getUserMedia = async () => { throw new Error("recording cancellation probe"); };
    const historyResponse = (sid) => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        events: [{ event_type: "transcript_final", segment_id: `${sid}_segment`, text: `${sid} text` }],
        event_source: { provider: "local_mock_asr", provider_mode: "mock", is_mock: true },
      }),
    });
    try {
      const slowRecordHistory = openHistorySession("slow_before_record");
      await Promise.resolve();
      const slowRecord = pendingHistory.get("slow_before_record");
      const recordButton = document.getElementById("btn-record");
      recordButton.disabled = false;
      currentReadiness = { ...(currentReadiness || {}), realtime_asr_available: true };
      recordButton.click();
      await Promise.resolve();
      const recordHistorySignalAborted = slowRecord?.signal?.aborted === true;
      if (!recordHistorySignalAborted) slowRecord?.resolve(historyResponse("slow_before_record"));
      await slowRecordHistory;

      const slowUploadHistory = openHistorySession("slow_before_upload");
      await Promise.resolve();
      const slowUpload = pendingHistory.get("slow_before_upload");
      beginSessionOperation();
      await Promise.resolve();
      const uploadHistorySignalAborted = slowUpload?.signal?.aborted === true;
      if (!uploadHistorySignalAborted) slowUpload?.resolve(historyResponse("slow_before_upload"));
      await slowUploadHistory;
      return {
        record_history_signal_aborted: recordHistorySignalAborted,
        upload_history_signal_aborted: uploadHistorySignalAborted,
        stale_history_visible: (document.getElementById("transcript-stream")?.innerText || "").includes("slow_before_"),
      };
    } finally {
      window.fetch = originalFetch;
      navigator.mediaDevices.getUserMedia = originalGetUserMedia;
      resetSessionView();
    }
  });
  assert(historySessionOperationProbe.record_history_signal_aborted, `starting recording did not abort slow history: ${JSON.stringify(historySessionOperationProbe)}`);
  assert(historySessionOperationProbe.upload_history_signal_aborted, `starting import did not abort slow history: ${JSON.stringify(historySessionOperationProbe)}`);
  assert(!historySessionOperationProbe.stale_history_visible, `stale history response overwrote session action: ${JSON.stringify(historySessionOperationProbe)}`);

  transcriptProjectionProbe = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "transcript_projection_probe";
    currentAutoSuggestionStatus = { paused: true };
    resetRealtimeUiMetrics();
    const events = [
      { event_type: "transcript_partial", segment_id: "seg_probe", text: "这是用于验证最终状态转换的完整识别文字", confidence: 0.95, start_ms: 0 },
      { event_type: "final", segment_id: "seg_probe", text: "这是用于验证最终状态转换的完整识别文字", confidence: 0.95, start_ms: 0 },
      { event_type: "transcript_final", segment_id: "seg_probe", normalized_text: "这是用于验证最终状态转换的完整识别文字", confidence: 0.95, start_ms: 0 },
      { event_type: "transcript_revision", segment_id: "seg_probe_rev1", revision_of: "seg_probe", text: "这是 AI 校正后的完整识别文字", confidence: 0.95, start_ms: 0 },
    ];
    events.forEach((event) => {
      currentEvents.push(event);
      const result = appendLiveEvent(event);
      recordRealtimeUiEventMetric(event, result);
    });
    const row = document.querySelector('.transcript-segment[data-transcript-segment-id="segment:seg_probe"]');
    const report = {
      transcript_row_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
      active_partial_count: document.querySelectorAll("#transcript-active-tail:not([hidden])").length,
      display_text: row?.querySelector(".transcript-text")?.textContent || "",
      original_text: row?.closest(".transcript-paragraph")?.querySelector(".original-asr-text div")?.textContent || "",
      correction_badge_count: row?.querySelectorAll(".correction-badge").length || 0,
      partial_visible_count: realtimeUiMetricsSnapshot().partial_visible_count,
      final_visible_count: realtimeUiMetricsSnapshot().final_visible_count,
    };
    resetSessionView();
    return report;
  });
  assert(transcriptProjectionProbe.transcript_row_count === 1, `projection should keep one row: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.active_partial_count === 0, `projection should remove partial: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.display_text === "这是 AI 校正后的完整识别文字", `revision should replace display text: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.original_text === "这是用于验证最终状态转换的完整识别文字", `revision should preserve committed original: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.correction_badge_count === 1, `correction badge missing: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.partial_visible_count === 1, `partial metric should count one visible update: ${JSON.stringify(transcriptProjectionProbe)}`);
  assert(transcriptProjectionProbe.final_visible_count === 1, `identical normalized final must not double count: ${JSON.stringify(transcriptProjectionProbe)}`);

  reconciledFinalProbe = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "reconciled_final_probe";
    currentAutoSuggestionStatus = { paused: true };
    const events = [
      { event_type: "transcript_final", segment_id: "reconcile_old", text: "我们讨论产品目标", start_ms: 0, end_ms: 1000 },
      {
        event_type: "transcript_partial",
        segment_id: "reconcile_new",
        text: "的和实现计划",
        source_snapshot_text: "我们讨论产品目的和实现计划",
        projection_reconciled: true,
        start_ms: 1000,
        end_ms: 2000,
      },
      {
        event_type: "transcript_final",
        segment_id: "reconcile_new",
        text: "的和实现计划",
        source_snapshot_text: "我们讨论产品目的和实现计划",
        projection_reconciled: true,
        start_ms: 1000,
        end_ms: 2000,
      },
    ];
    events.forEach((event) => {
      currentEvents.push(event);
      appendLiveEvent(event);
    });
    const report = {
      full_text: canonicalTranscriptFullText(),
      visible_text: document.getElementById("transcript-document")?.innerText || "",
      segment_count: document.querySelectorAll(".transcript-segment").length,
      active_tail_count: document.querySelectorAll("#transcript-active-tail:not([hidden])").length,
    };
    resetSessionView();
    return report;
  });
  assert(reconciledFinalProbe.full_text === "我们讨论产品目的和实现计划", `reconciled final corrupted canonical text: ${JSON.stringify(reconciledFinalProbe)}`);
  assert(reconciledFinalProbe.visible_text.includes("我们讨论产品目的和实现计划"), `reconciled final was not visible: ${JSON.stringify(reconciledFinalProbe)}`);
  assert(reconciledFinalProbe.active_tail_count === 0, `reconciled final left an active tail: ${JSON.stringify(reconciledFinalProbe)}`);

  providerErrorPreservationProbe = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "provider_error_preservation_probe";
    currentAutoSuggestionStatus = { paused: true };
    const finalEvent = {
      event_type: "transcript_final",
      segment_id: "provider_error_segment",
      text: "识别错误发生前已经确认的会议正文",
      start_ms: 0,
      end_ms: 1000,
    };
    currentEvents.push(finalEvent);
    appendLiveEvent(finalEvent);
    const before = {
      full_text: canonicalTranscriptFullText(),
      segment_count: document.querySelectorAll(".transcript-segment").length,
    };
    appendLiveEvent({
      event_type: "provider_error",
      error_code: "probe_provider_error",
      message: "用于验证正文保留的识别错误",
      payload: {},
    });
    const after = {
      full_text: canonicalTranscriptFullText(),
      visible_text: document.getElementById("transcript-document")?.innerText || "",
      segment_count: document.querySelectorAll(".transcript-segment").length,
      document_exists: Boolean(document.getElementById("transcript-document")),
      tail_exists: Boolean(document.getElementById("transcript-active-tail")),
      status_text: document.getElementById("sys-status")?.innerText || "",
    };
    resetSessionView();
    return { before, after };
  });
  assert(providerErrorPreservationProbe.after.full_text === providerErrorPreservationProbe.before.full_text, `provider error changed canonical text: ${JSON.stringify(providerErrorPreservationProbe)}`);
  assert(providerErrorPreservationProbe.after.segment_count === providerErrorPreservationProbe.before.segment_count, `provider error removed committed segments: ${JSON.stringify(providerErrorPreservationProbe)}`);
  assert(providerErrorPreservationProbe.after.document_exists && providerErrorPreservationProbe.after.tail_exists, `provider error removed canonical containers: ${JSON.stringify(providerErrorPreservationProbe)}`);
  assert(providerErrorPreservationProbe.after.visible_text.includes("已经确认的会议正文"), `provider error hid committed text: ${JSON.stringify(providerErrorPreservationProbe)}`);
  assert(providerErrorPreservationProbe.after.status_text.includes("实时识别不可用"), `provider error status missing: ${JSON.stringify(providerErrorPreservationProbe)}`);

  const scrollSetup = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "scroll_follow_probe";
    currentAutoSuggestionStatus = { paused: true };
    const events = Array.from({ length: 80 }, (_, index) => ({
      event_type: "transcript_final",
      segment_id: `scroll_segment_${index}`,
      text: `第 ${index + 1} 段会议正文，用于验证用户阅读旧内容时不会被实时文字强制拉回底部。`,
      start_ms: index * 4000,
      end_ms: index * 4000 + 2500,
    }));
    currentEvents = [...events];
    renderTranscriptAndCandidates(events);
    const target = getTranscriptScrollTarget();
    target.scrollTop = Math.max(0, target.scrollHeight - target.clientHeight - 320);
    target.dispatchEvent(new Event("scroll"));
    window.__scrollFollowBefore = target.scrollTop;
    const partial = {
      event_type: "transcript_partial",
      segment_id: "scroll_live_tail",
      text: "这是用户阅读旧内容期间到达的新实时文字。",
      start_ms: 400000,
      end_ms: 401000,
    };
    currentEvents.push(partial);
    appendLiveEvent(partial);
    return {
      overflow: target.scrollHeight > target.clientHeight,
      before_scroll_top: window.__scrollFollowBefore,
    };
  });
  await delay(100);
  const scrollPaused = await evaluate(page, () => {
    const target = getTranscriptScrollTarget();
    const button = document.getElementById("btn-new-transcript-content");
    const buttonRect = button.getBoundingClientRect();
    const viewportRect = document.querySelector(".center").getBoundingClientRect();
    return {
      after_scroll_top: target.scrollTop,
      button_visible: button.hidden === false && buttonRect.width > 0 && buttonRect.height > 0,
      button_in_transcript_viewport: buttonRect.left >= viewportRect.left
        && buttonRect.right <= viewportRect.right
        && buttonRect.top >= viewportRect.top
        && buttonRect.bottom <= viewportRect.bottom,
      active_tail_count: document.querySelectorAll("#transcript-active-tail:not([hidden])").length,
    };
  });
  assert(scrollSetup.overflow, `scroll probe did not create overflow: ${JSON.stringify(scrollSetup)}`);
  assert(Math.abs(scrollPaused.after_scroll_top - scrollSetup.before_scroll_top) <= 1, `new transcript forced reader to bottom: ${JSON.stringify({ scrollSetup, scrollPaused })}`);
  assert(scrollPaused.button_visible && scrollPaused.button_in_transcript_viewport, `new-content control is not visible in transcript viewport: ${JSON.stringify(scrollPaused)}`);
  assert(scrollPaused.active_tail_count === 1, `scroll probe must keep one active tail: ${JSON.stringify(scrollPaused)}`);
  await evaluate(page, () => document.getElementById("btn-new-transcript-content").click());
  await waitForCdpExpression(page, `isTranscriptNearBottom(getTranscriptScrollTarget()) && document.getElementById("btn-new-transcript-content").hidden`, 5000);
  transcriptScrollFollowProbe = await evaluate(page, () => ({
    scroll_position_preserved: Math.abs(window.__scrollFollowBefore - getTranscriptScrollTarget().scrollTop) > 1,
    resumed_at_bottom: isTranscriptNearBottom(getTranscriptScrollTarget()),
    button_hidden_after_resume: document.getElementById("btn-new-transcript-content").hidden,
  }));
  assert(transcriptScrollFollowProbe.resumed_at_bottom && transcriptScrollFollowProbe.button_hidden_after_resume, `new-content control did not resume following: ${JSON.stringify(transcriptScrollFollowProbe)}`);
  await evaluate(page, () => {
    delete window.__scrollFollowBefore;
    resetSessionView();
  });

  recordingFailureRestoreProbe = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "preserved_session_probe";
    currentEvents = [
      { event_type: "transcript_final", segment_id: "seg_restore", normalized_text: "这是一段已保存的会议文字", payload: { normalized_text: "这是一段已保存的会议文字" } },
      { event_type: "suggestion_candidate_event", payload: { candidate_id: "cand_processed", target_type: "DecisionCandidate", suggested_prompt: "已处理决定不应复活" } },
      { event_type: "suggestion_candidate_event", payload: { candidate_id: "cand_executed", target_type: "ActionItem", suggested_prompt: "已执行待办不应复活" } },
      { event_type: "suggestion_candidate_event", payload: { candidate_id: "cand_remaining", target_type: "Risk", suggested_prompt: "仍未处理的风险提醒" } },
    ];
    currentAutoSuggestionStatus = { paused: true, status: "paused", processed_candidate_ids: ["cand_processed"] };
    executedSuggestionCandidateIds = new Set(["cand_executed"]);
    currentCandidateFocusType = "Risk";
    replaceCandidateReminderEvents(currentEvents);
    renderTranscriptAndCandidates(currentEvents);
    preserveSessionBeforeRecording();
    startRecordingDraftSession("failed_recording_probe");
    const draftState = {
      auto_status_cleared: currentAutoSuggestionStatus === null,
      executed_ids_cleared: executedSuggestionCandidateIds.size === 0,
      focus_cleared: currentCandidateFocusType === "",
    };
    restorePreservedSessionAfterRecordingFailure("测试恢复", "failed_recording_probe");
    const reminderText = document.getElementById("candidate-panel")?.innerText || "";
    const report = {
      draft_state: draftState,
      restored_session_id: currentSession,
      restored_paused: currentAutoSuggestionStatus?.paused === true,
      restored_processed_candidate_ids: currentAutoSuggestionStatus?.processed_candidate_ids || [],
      restored_executed_candidate_ids: Array.from(executedSuggestionCandidateIds),
      restored_focus_type: currentCandidateFocusType,
      processed_reminder_revived: reminderText.includes("已处理决定不应复活"),
      executed_reminder_revived: reminderText.includes("已执行待办不应复活"),
      remaining_reminder_visible: reminderText.includes("仍未处理的风险提醒"),
    };
    resetSessionView();
    return report;
  });
  assert(recordingFailureRestoreProbe.draft_state.auto_status_cleared, `draft auto status must reset: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.draft_state.executed_ids_cleared, `draft executed IDs must reset: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.draft_state.focus_cleared, `draft focus must reset: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.restored_paused, `paused status must restore: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.restored_processed_candidate_ids.includes("cand_processed"), `processed IDs must restore: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.restored_executed_candidate_ids.includes("cand_executed"), `executed IDs must restore: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.restored_focus_type === "Risk", `focus must restore: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(!recordingFailureRestoreProbe.processed_reminder_revived, `processed reminder revived: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(!recordingFailureRestoreProbe.executed_reminder_revived, `executed reminder revived: ${JSON.stringify(recordingFailureRestoreProbe)}`);
  assert(recordingFailureRestoreProbe.remaining_reminder_visible, `remaining reminder missing: ${JSON.stringify(recordingFailureRestoreProbe)}`);

  suggestionSemanticDedupeProbe = await evaluate(page, () => {
    prepareNewSession();
    currentSession = "suggestion_semantic_probe";
    const shared = {
      suggestion_text: "建议明确回滚负责人",
      target_type: "Risk",
      target_id: "risk_1",
      evidence_span_ids: ["ev_1"],
      evidence_spans: [{ id: "ev_1", segment_id: "seg_1", quote: "谁负责回滚" }],
    };
    const incoming = [
      { ...shared, card_id: "semantic_card_old" },
      { ...shared, card_id: "semantic_card_new", target_id: "risk_same_evidence_different_target" },
      ...Array.from({ length: 6 }, (_, index) => ({
        card_id: `unique_card_${index}`,
        suggestion_text: `不同建议 ${index}`,
        target_type: "ActionItem",
        target_id: `action_${index}`,
        evidence_span_ids: [`ev_${index + 2}`],
      })),
    ];
    const merged = mergeSuggestionCards([], incoming);
    currentSuggestionCards = merged.cards;
    renderSuggestionCards(currentSuggestionCards);
    const panel = document.getElementById("suggestions-panel");
    const semanticTextCount = Array.from(panel.querySelectorAll(".sug-body"))
      .filter((element) => element.textContent === "建议明确回滚负责人").length;
    const report = {
      scenario: "different_card_ids_same_semantics",
      semantic_unique_count: currentSuggestionCards.length,
      duplicate_semantic_text_count: semanticTextCount,
      visible_suggestion_count: panel.querySelectorAll(":scope > [data-card-kind='suggestion']").length,
      folded_suggestion_count: panel.querySelectorAll(".suggestion-fold-content [data-card-kind='suggestion']").length,
      fold_open: panel.querySelector(".suggestion-fold")?.open === true,
    };
    resetSessionView();
    return report;
  });
  assert(suggestionSemanticDedupeProbe.semantic_unique_count === 7, `semantic card merge should retain 7 unique cards: ${JSON.stringify(suggestionSemanticDedupeProbe)}`);
  assert(suggestionSemanticDedupeProbe.duplicate_semantic_text_count === 1, `same semantic suggestion rendered more than once: ${JSON.stringify(suggestionSemanticDedupeProbe)}`);
  assert(suggestionSemanticDedupeProbe.visible_suggestion_count === 5, `right rail visible suggestion cap failed: ${JSON.stringify(suggestionSemanticDedupeProbe)}`);
  assert(suggestionSemanticDedupeProbe.folded_suggestion_count === 2 && !suggestionSemanticDedupeProbe.fold_open, `older suggestions must be folded: ${JSON.stringify(suggestionSemanticDedupeProbe)}`);

  // Activate the visible native button before assigning the test file to the compatible file input.
  await evaluate(page, () => {
    const button = document.getElementById("btn-upload-label");
    const input = document.getElementById("btn-upload");
    window.__uploadActivationProbe = { inputClickCount: 0 };
    window.__uploadOriginalInputClick = input.click;
    input.click = () => { window.__uploadActivationProbe.inputClickCount += 1; };
    document.getElementById("btn-upload-label").focus();
  });
  await page.send("Input.dispatchKeyEvent", {
    type: "rawKeyDown",
    key: " ",
    code: "Space",
    windowsVirtualKeyCode: 32,
    nativeVirtualKeyCode: 32,
  });
  await page.send("Input.dispatchKeyEvent", {
    type: "keyUp",
    key: " ",
    code: "Space",
    windowsVirtualKeyCode: 32,
    nativeVirtualKeyCode: 32,
  });
  const uploadActivationProbe = await evaluate(page, () => {
    const button = document.getElementById("btn-upload-label");
    const input = document.getElementById("btn-upload");
    input.click = window.__uploadOriginalInputClick;
    const report = {
      tag_name: button.tagName,
      focused: document.activeElement === button,
      input_click_count: window.__uploadActivationProbe.inputClickCount,
    };
    delete window.__uploadActivationProbe;
    delete window.__uploadOriginalInputClick;
    return report;
  });
  assert(uploadActivationProbe.tag_name === "BUTTON" && uploadActivationProbe.focused, `import control must be a focusable native button: ${JSON.stringify(uploadActivationProbe)}`);
  assert(uploadActivationProbe.input_click_count === 1, `visible import button did not activate file input: ${JSON.stringify(uploadActivationProbe)}`);
  const inputNodeId = await nodeIdForSelector(page, "#btn-upload");
  await page.send("DOM.setFileInputFiles", { nodeId: inputNodeId, files: [uploadAudioPath] });
  markButtonCovered("btn-upload", "visible_button_triggered_file_input", "import_recording");
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes('simulated-release-review')`, 120000);
  await waitForCdpExpression(
    page,
    `document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length >= 1`,
    15000,
  );
  await waitForCdpExpression(page, `document.getElementById('source-badge').innerText.includes('导入录音')`, 15000);
  await captureStep(page, "import_recording", "导入录音完成，实时文字和来源标识已显示");

  // History list and reopen same session.
  const importedSessionId = await evaluate(page, () => currentSession);
  assert(importedSessionId && importedSessionId.startsWith("file_"), `expected file_ session, got ${importedSessionId}`);
  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(page, `document.getElementById("history-modal").hidden === false`, 5000);
  const historyItemSelector = `.history-modal-item[data-session-id="${importedSessionId}"] button[data-action="open"]`;
  await waitForCdpExpression(page, `document.querySelector(${JSON.stringify(historyItemSelector)}) !== null`, 15000);
  await captureStep(page, "history_open", "历史记录打开，并包含刚导入的会话");
  await evaluate(page, (selector) => {
    const button = document.querySelector(selector);
    if (!button) throw new Error(`history item missing: ${selector}`);
    button.click();
  }, historyItemSelector);
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes(${JSON.stringify(importedSessionId)})`, 15000);
  await captureStep(page, "history_reopen", "从历史记录重新打开同一会话");

  const beforeReloadRecovery = await evaluate(page, () => ({
    session_id: currentSession,
    full_text: canonicalTranscriptFullText(),
    visible_text: document.getElementById("transcript-stream")?.innerText || "",
  }));
  await page.send("Page.reload", { ignoreCache: true });
  await waitForCdpExpression(page, `document.getElementById("btn-upload") !== null`, 15000);
  await waitForCdpExpression(page, `currentSession === ${JSON.stringify(importedSessionId)} && document.getElementById("sys-status").innerText.includes("已恢复最近会议")`, 15000);
  reloadRecoveryProbe = await evaluate(page, () => ({
    session_id: currentSession,
    full_text: canonicalTranscriptFullText(),
    visible_text: document.getElementById("transcript-stream")?.innerText || "",
    status_text: document.getElementById("sys-status")?.innerText || "",
    active_tail_count: document.querySelectorAll("#transcript-active-tail:not([hidden])").length,
  }));
  assert(reloadRecoveryProbe.session_id === beforeReloadRecovery.session_id, `reload restored wrong session: ${JSON.stringify({ beforeReloadRecovery, reloadRecoveryProbe })}`);
  assert(reloadRecoveryProbe.full_text === beforeReloadRecovery.full_text, `reload changed canonical full text: ${JSON.stringify({ beforeReloadRecovery, reloadRecoveryProbe })}`);
  assert(reloadRecoveryProbe.visible_text === beforeReloadRecovery.visible_text, `reload changed visible transcript: ${JSON.stringify({ beforeReloadRecovery, reloadRecoveryProbe })}`);
  assert(reloadRecoveryProbe.active_tail_count <= 1, `reload restored multiple active tails: ${JSON.stringify(reloadRecoveryProbe)}`);
  await captureStep(page, "reload_recovery", "刷新页面后自动恢复最近真实会议，完整文字保持一致");
  await evaluate(page, () => showHistoryWorkspace());
  await waitForCdpExpression(page, `document.getElementById("review-workspace").open === true`, 5000);

  // Left meeting-focus filters should act on the realtime reminder queue.
  const focusBefore = await evaluate(page, () => ({
    focusTypes: Array.from(document.querySelectorAll("[data-focus-type]")).map((el) => el.dataset.focusType),
    candidateTypes: Array.from(document.querySelectorAll("#candidate-panel [data-card-kind]")).map((el) => el.dataset.candidateType),
    counts: {
      decision: document.getElementById("c-decision")?.innerText,
      action: document.getElementById("c-action")?.innerText,
      risk: document.getElementById("c-risk")?.innerText,
      question: document.getElementById("c-question")?.innerText,
      gap: document.getElementById("c-gap")?.innerText,
    },
  }));
  for (const focusType of ["DecisionCandidate", "ActionItem", "Risk", "OpenQuestion"]) {
    assert(focusBefore.focusTypes.includes(focusType), `missing left focus filter ${focusType}: ${JSON.stringify(focusBefore)}`);
    const countByType = {
      DecisionCandidate: focusBefore.counts.decision,
      ActionItem: focusBefore.counts.action,
      Risk: focusBefore.counts.risk,
      OpenQuestion: focusBefore.counts.question,
    };
    const hasReminderForType = Number(countByType[focusType] || 0) > 0;
    const disabled = await evaluate(page, (type) => document.querySelector(`[data-focus-type="${type}"]`)?.disabled === true, focusType);
    if (!hasReminderForType) {
      assert(disabled, `zero-count focus filter should be disabled: ${focusType} ${JSON.stringify(focusBefore)}`);
      markFocusFilterCovered(focusType, "disabled_zero_count_filter", `candidate_filter_${focusType}`);
      continue;
    }
    assert(!disabled, `non-empty focus filter should be enabled: ${focusType} ${JSON.stringify(focusBefore)}`);
    await evaluate(page, (type) => document.querySelector(`[data-focus-type="${type}"]`).click(), focusType);
    markFocusFilterCovered(focusType, "clicked_filter", `candidate_filter_${focusType}`);
    await waitForCdpExpression(page, `document.querySelector('[data-focus-type="${focusType}"]').getAttribute('aria-pressed') === 'true'`, 5000);
    await waitForCdpExpression(page, `
      document.querySelectorAll('#candidate-panel [data-candidate-type="${focusType}"]').length >= 1 &&
      document.querySelectorAll('#candidate-panel [data-card-kind]').length === document.querySelectorAll('#candidate-panel [data-candidate-type="${focusType}"]').length
    `, 5000);
    if (focusType === "Risk") {
      await captureStep(page, "candidate_filter_risk", "点击左侧风险提醒后，右侧只显示风险类实时提醒");
    }
  }
  await evaluate(page, () => document.getElementById("btn-clear-candidate-focus").click());
  markFocusFilterCovered("all", "clicked_clear_filter", "candidate_filter_all");
  await waitForCdpExpression(page, `!document.getElementById('btn-clear-candidate-focus')`, 5000);
  await waitForCdpExpression(page, `document.querySelectorAll('#candidate-panel [data-card-kind]').length >= ${focusBefore.candidateTypes.length}`, 5000);
  await captureStep(page, "candidate_filter_all", "点击显示全部后，实时提醒恢复默认优先级队列");

  // Generate cards, click evidence quote, generate approach and minutes.
  await evaluate(page, () => document.getElementById("btn-cards").click());
  markButtonCovered("btn-cards", "clicked", "suggestions_generated");
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='suggestion']").length === 1`, 30000);
  await waitForCdpExpression(page, `document.querySelectorAll(".evidence-link").length >= 1`, 15000);
  await waitForCdpExpression(page, `
    document.querySelectorAll('#candidate-panel [data-card-kind="candidate"], #candidate-panel [data-card-kind="partial-hint"]').length === 0 &&
    document.getElementById("c-gap")?.innerText === "0" &&
    document.getElementById("s-candidates")?.innerText === "0"
  `, 5000);
  await captureStep(page, "suggestions_generated", "生成会议建议后，正式建议可见，正式建议处理后实时提醒应为空且计数为 0");
  await evaluate(page, () => document.querySelector(".evidence-link").click());
  await waitForCdpExpression(page, `document.querySelectorAll(".transcript-segment.evidence-focus").length >= 1`, 5000);
  await captureStep(page, "evidence_clickback", "点击建议卡证据，文字区对应原话高亮");

  await evaluate(page, () => document.getElementById("btn-approach").click());
  markButtonCovered("btn-approach", "clicked", "approach_generated");
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='approach']").length >= 1`, 30000);
  await captureStep(page, "approach_generated", "生成方案分析卡片");
  await evaluate(page, () => document.getElementById("btn-minutes").click());
  markButtonCovered("btn-minutes", "clicked", "minutes_generated");
  await waitForCdpExpression(page, `document.getElementById('minutes-panel').innerText.includes('会议纪要')`, 30000);
  await captureStep(page, "minutes_generated", "生成会议纪要");

  // Left meeting overview rows should navigate to the corresponding business area.
  await clickOverviewJump(page, "transcript", "overview_jump_transcript", "#transcript-stream", "已跳到实时文字");
  await clickOverviewJump(page, "reminders", "overview_jump_reminders", "#candidate-panel", "还没有实时提醒");
  await clickOverviewJump(page, "suggestions", "overview_jump_suggestions", "#suggestions-panel", "已跳到 AI 建议");
  await clickOverviewJump(page, "approach", "overview_jump_approach", "#approach-panel", "已跳到方案分析");
  await clickOverviewJump(page, "audio", "overview_jump_audio", "#btn-export-audio", "录音已保存");
  await clickOverviewJump(page, "minutes", "overview_jump_minutes", "#minutes-panel", "已跳到会后复盘");

  await setViewport(page, MOBILE_VIEWPORT);
  await evaluate(page, () => window.scrollTo(0, 0));
  await delay(250);
  mobileLayoutProbe = await readViewportLayoutProbe(page);
  assert(mobileLayoutProbe.inner_width === MOBILE_VIEWPORT.width, `unexpected mobile viewport width: ${JSON.stringify(mobileLayoutProbe)}`);
  assert(mobileLayoutProbe.inner_height === MOBILE_VIEWPORT.height, `unexpected mobile viewport height: ${JSON.stringify(mobileLayoutProbe)}`);
  assert(mobileLayoutProbe.horizontal_overflow === false, `mobile page has horizontal overflow: ${JSON.stringify(mobileLayoutProbe)}`);
  assert(mobileLayoutProbe.overlapping_button_pairs.length === 0, `mobile buttons overlap: ${JSON.stringify(mobileLayoutProbe)}`);
  assert(mobileLayoutProbe.clipped_major_text.length === 0, `mobile major text is clipped: ${JSON.stringify(mobileLayoutProbe)}`);
  await captureViewportStep(page, "mobile_375x812", "375x812 移动视口无横向溢出、主要文字和按钮无明显重叠", MOBILE_VIEWPORT, mobileLayoutProbe);
  await setViewport(page, DESKTOP_VIEWPORT);
  await delay(250);

  await evaluate(page, () => document.getElementById("btn-live").click());
  markButtonCovered("btn-live", "clicked", "transcript_refreshed");
  await waitForCdpExpression(page, `document.getElementById('sys-status').innerText.includes('实时文字已刷新')`, 15000);
  await captureStep(page, "transcript_refreshed", "刷新实时文字完成");

  // Export all user-facing session artifacts and verify the download targets.
  const downloads = await evaluate(page, () => {
    window.__workbenchAllButtonDownloads = [];
    if (!window.__originalAnchorClickForAllButtons) window.__originalAnchorClickForAllButtons = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function patchedAnchorClick() {
      if (this.download) {
        window.__workbenchAllButtonDownloads.push({ href: this.href, download: this.download });
        return;
      }
      return window.__originalAnchorClickForAllButtons.call(this);
    };
    document.getElementById("btn-export-transcript").click();
    document.getElementById("btn-export-minutes").click();
    document.getElementById("btn-export-audio").click();
    const result = window.__workbenchAllButtonDownloads.slice();
    HTMLAnchorElement.prototype.click = window.__originalAnchorClickForAllButtons;
    return result;
  });
  markButtonCovered("btn-export-transcript", "clicked", "exports_verified");
  markButtonCovered("btn-export-minutes", "clicked", "exports_verified");
  markButtonCovered("btn-export-audio", "clicked", "audio_export_verified");
  assert(downloads.length === 3, `expected 3 downloads, got ${JSON.stringify(downloads)}`);
  assert(downloads[0].download.endsWith(".transcript.txt"), `bad transcript download: ${JSON.stringify(downloads)}`);
  assert(downloads[1].download.endsWith(".minutes.md"), `bad minutes download: ${JSON.stringify(downloads)}`);
  assert(downloads[2].download.endsWith(".audio.wav"), `bad audio download: ${JSON.stringify(downloads)}`);
  await captureStep(page, "exports_verified", "导出文字稿和导出纪要的下载目标已验证");
  await captureStep(page, "audio_export_verified", "导出录音按钮的下载目标已验证");

  // Auto suggestion toggle and one-click organizer on imported session.
  await evaluate(page, () => document.getElementById("btn-auto-suggestion-toggle").click());
  markButtonCovered("btn-auto-suggestion-toggle", "clicked_pause", "auto_suggestion_paused");
  await waitForCdpExpression(page, `document.getElementById('btn-auto-suggestion-toggle').innerText.includes('恢复 AI 建议')`, 15000);
  await captureStep(page, "auto_suggestion_paused", "暂停 AI 建议后，按钮切换为恢复 AI 建议");
  await evaluate(page, () => document.getElementById("btn-auto-suggestion-toggle").click());
  markButtonCovered("btn-auto-suggestion-toggle", "clicked_pause_and_resume", "auto_suggestion_resumed");
  await waitForCdpExpression(page, `document.getElementById('btn-auto-suggestion-toggle').innerText.includes('暂停 AI 建议')`, 15000);
  await captureStep(page, "auto_suggestion_resumed", "恢复 AI 建议后，按钮切回暂停 AI 建议");
  await evaluate(page, () => document.getElementById("btn-organize").click());
  markButtonCovered("btn-organize", "clicked", "organize_completed");
  await waitForCdpExpression(page, `document.getElementById('sys-status').innerText.includes('会议整理完成')`, 30000);
  await captureStep(page, "organize_completed", "一键整理会议完成");

  // Delete with confirmation override and verify reset.
  await evaluate(page, () => {
    window.confirm = () => true;
    document.getElementById("btn-delete").click();
  });
  markButtonCovered("btn-delete", "clicked_confirmed", "delete_reset");
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes('准备开始')`, 15000);
  await captureStep(page, "delete_reset", "删除本次会议后页面回到准备开始状态");
  const finalState = await evaluate(page, () => ({
    currentSession,
    utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length,
    suggestions: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approaches: document.querySelectorAll("[data-card-kind='approach']").length,
    sessionMeta: document.getElementById("session-meta").innerText,
  }));
  assert(finalState.currentSession === null, `expected currentSession reset, got ${JSON.stringify(finalState)}`);
  assert(finalState.utterances === 0, `expected no utterances after delete, got ${JSON.stringify(finalState)}`);

  const screenshot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
  await writeFile(path.join(artifactRoot, "workbench-all-buttons-after.png"), Buffer.from(screenshot.data, "base64"));
  const finalScreenshotPath = path.join(artifactRoot, "workbench-all-buttons-after.png");
  assertCoverageComplete();
  assertBrowserDiagnosticsClean();
  const report = {
    status: "go_workbench_all_buttons_smoke",
    imported_session_id: importedSessionId,
    upload_audio_path: uploadAudioPath,
    fake_llm_request_count: fakeLlmRequestCount,
    transcript_projection_probe: transcriptProjectionProbe,
    reconciled_final_probe: reconciledFinalProbe,
    provider_error_preservation_probe: providerErrorPreservationProbe,
    recording_failure_restore_probe: recordingFailureRestoreProbe,
    recording_draft_first_event_probe: recordingDraftFirstEventProbe,
    snapshot_revision_order_probe: snapshotRevisionOrderProbe,
    revision_supplement_probe: revisionSupplementProbe,
    history_selection_race_probe: historySelectionRaceProbe,
    history_workspace_probe: historyWorkspaceProbe,
    history_session_operation_probe: historySessionOperationProbe,
    transcript_namespace_collision_probe: transcriptNamespaceCollisionProbe,
    suggestion_semantic_dedupe_probe: suggestionSemanticDedupeProbe,
    mobile_layout_probe: mobileLayoutProbe,
    transcript_scroll_follow_probe: transcriptScrollFollowProbe,
    reload_recovery_probe: reloadRecoveryProbe,
    browser_diagnostics: browserDiagnostics,
    downloads,
    final_state: finalState,
    button_coverage: buttonCoverage,
    focus_filter_coverage: focusFilterCoverage,
    overview_jump_coverage: overviewJumpCoverage,
    overview_jump_focus_state: overviewJumpFocusStates,
    checklist,
    screenshots,
    screenshot_count: screenshots.length,
    screenshot_path: finalScreenshotPath,
  };
  await writeFile(path.join(artifactRoot, "workbench_all_buttons_report.json"), JSON.stringify(report, null, 2));
  await writeFile(path.join(artifactRoot, "workbench_visual_acceptance_report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (err) {
  failed = true;
  if (page) {
    try { await captureStep(page, "failure_state", "失败现场截图"); } catch {}
  }
  const state = page ? await safeReadPageState(page).catch((stateErr) => ({ state_error: stateErr.message })) : {};
  const report = {
    status: "blocked_workbench_all_buttons_smoke",
    error: err.message,
    upload_audio_path: uploadAudioPath,
    fake_llm_request_count: fakeLlmRequestCount,
    button_coverage: buttonCoverage,
    focus_filter_coverage: focusFilterCoverage,
    overview_jump_coverage: overviewJumpCoverage,
    overview_jump_focus_state: overviewJumpFocusStates,
    checklist,
    screenshots,
    page_state: state,
    browser_diagnostics: browserDiagnostics,
    server_stdout_tail: tailText(serverStdout, 4000),
    server_stderr_tail: tailText(serverStderr, 4000),
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "workbench_all_buttons_error.json"), JSON.stringify(report, null, 2));
  await writeFile(path.join(artifactRoot, "workbench_visual_acceptance_report.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
  if (fakeLlmServer) await new Promise((resolve) => fakeLlmServer.close(resolve));
  await removeTempDirWithRetry(dataDir);
  await removeTempDirWithRetry(chromeUserDataDir);
  process.exit(failed ? 1 : 0);
}

function assert(cond, msg) { if (!cond) throw new Error(msg); }
function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function tailText(text, maxChars) { return text.length > maxChars ? text.slice(text.length - maxChars) : text; }
function markButtonCovered(buttonId, coverage, evidence) {
  const item = buttonCoverage.find((entry) => entry.button_id === buttonId);
  if (!item) throw new Error(`button coverage entry missing: ${buttonId}`);
  item.coverage = coverage;
  item.evidence = evidence;
}

function assertCoverageComplete() {
  const pendingButtons = buttonCoverage.filter((entry) => entry.coverage === "pending");
  const pendingFocusFilters = focusFilterCoverage.filter((entry) => entry.coverage === "pending");
  const pendingOverviewJumps = overviewJumpCoverage.filter((entry) => entry.coverage === "pending");
  assert(pendingButtons.length === 0, `pending button coverage: ${JSON.stringify(pendingButtons)}`);
  assert(pendingFocusFilters.length === 0, `pending focus filter coverage: ${JSON.stringify(pendingFocusFilters)}`);
  assert(pendingOverviewJumps.length === 0, `pending overview jump coverage: ${JSON.stringify(pendingOverviewJumps)}`);
}

function assertBrowserDiagnosticsClean() {
  const failures = {
    runtime_exceptions: browserDiagnostics.runtime_exceptions,
    error_console: browserDiagnostics.error_console,
    network_loading_failed: browserDiagnostics.network_loading_failed,
    http_5xx: browserDiagnostics.http_5xx,
  };
  const failureCount = Object.values(failures).reduce((total, entries) => total + entries.length, 0);
  assert(failureCount === 0, `browser runtime/network diagnostics failed closed: ${JSON.stringify(failures)}`);
}

function runtimeRemoteObjectText(remoteObject = {}) {
  if (remoteObject.value !== undefined) return String(remoteObject.value);
  return String(remoteObject.description || remoteObject.unserializableValue || remoteObject.type || "");
}

function allowlistedNetworkFailure(entry) {
  const match = expectedNetworkFailureAllowlist.find((rule) => rule.matches(entry));
  if (!match) return false;
  browserDiagnostics.allowlisted_network_failures.push({ ...entry, allowlist_rule: match.name });
  return true;
}

function markFocusFilterCovered(focusType, coverage, evidence) {
  const item = focusFilterCoverage.find((entry) => entry.focus_type === focusType);
  if (!item) throw new Error(`focus filter coverage entry missing: ${focusType}`);
  item.coverage = coverage;
  item.evidence = evidence;
}

function markOverviewJumpCovered(target, coverage, evidence) {
  const item = overviewJumpCoverage.find((entry) => entry.overview_target === target);
  if (!item) throw new Error(`overview jump coverage entry missing: ${target}`);
  item.coverage = coverage;
  item.evidence = evidence;
}

async function clickOverviewJump(cdpPage, target, step, expectedFocusedSelector, expectedToastSnippet) {
  const clickState = await evaluate(cdpPage, (overviewTarget) => {
    const button = document.querySelector(`[data-overview-target="${overviewTarget}"]`);
    const previousToastText = document.getElementById("toast")?.innerText || "";
    if (!button) return { clicked: false, previousToastText };
    button.click();
    return { clicked: true, previousToastText };
  }, target);
  assert(clickState.clicked, `missing overview jump button: ${target}`);
  await waitForCdpExpression(cdpPage, `
    (() => {
      const targetElement = document.querySelector(${JSON.stringify(expectedFocusedSelector)});
      if (!targetElement) return false;
      const rect = targetElement.getBoundingClientRect();
      const targetInViewport = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
      const toastText = document.getElementById("toast")?.innerText || "";
      const toastAfterClickMatches = toastText.includes(${JSON.stringify(expectedToastSnippet)})
        && toastText !== ${JSON.stringify(clickState.previousToastText)};
      return targetInViewport && toastAfterClickMatches;
    })()
  `, 5000);
  const overviewJumpFocusState = await evaluate(cdpPage, ({ selector, expectedToast, previousToastText }) => {
    function isElementInViewport(element) {
      if (!element) return false;
      const rect = element.getBoundingClientRect();
      return rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
    }
    const targetElement = document.querySelector(selector);
    const toastText = document.getElementById("toast")?.innerText || "";
    return {
      selector,
      active_element_tag: document.activeElement?.tagName || "",
      active_element_id: document.activeElement?.id || "",
      active_element_matches: document.activeElement === targetElement || Boolean(document.activeElement?.closest?.(selector)),
      target_in_viewport: isElementInViewport(targetElement),
      toast_text: toastText,
      previous_toast_text: previousToastText,
      toast_after_click_matches: toastText.includes(expectedToast) && toastText !== previousToastText,
    };
  }, { selector: expectedFocusedSelector, expectedToast: expectedToastSnippet, previousToastText: clickState.previousToastText });
  assert(overviewJumpFocusState.active_element_matches, `overview jump focus target did not receive focus: ${JSON.stringify(overviewJumpFocusState)}`);
  assert(overviewJumpFocusState.target_in_viewport, `overview jump target not in viewport: ${JSON.stringify(overviewJumpFocusState)}`);
  assert(overviewJumpFocusState.toast_after_click_matches, `overview jump toast did not update for this click: ${JSON.stringify(overviewJumpFocusState)}`);
  overviewJumpFocusState.overview_target = target;
  overviewJumpFocusState.evidence = step;
  overviewJumpFocusStates.push(overviewJumpFocusState);
  markOverviewJumpCovered(target, "clicked_navigation", step);
  await captureStep(cdpPage, step, `点击左侧本场会议 ${target} 入口后定位到对应业务区`);
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
    const user = body.messages?.[1]?.content || "";
    let content;
    if (system.includes("ASR 转写修正器")) {
      content = user;
    } else if (system.includes("方案考量")) {
      content = JSON.stringify([
        { card_type: "approach.consideration", suggestion_text: "建议补齐灰度观察窗口和回滚触发阈值。", confidence: 0.9, trigger_reason: "方案风险未闭环", evidence_quote: "错误率超过 0.1% 就回滚" },
      ]);
    } else if (system.includes("纪要")) {
      content = JSON.stringify({
        background: "导入录音后的发布评审",
        decisions: ["先灰度 5%"],
        action_items: [{ item: "补齐 SLO 看板和自动化测试", owner: "张三", deadline: "今天" }],
        risks: ["回滚负责人和监控 owner 需要确认"],
        open_questions: ["P99 和错误率阈值是否进入发布门禁"],
        evidence_quotes: ["先灰度", "错误率超过 0.1% 就回滚"],
      });
    } else {
      content = JSON.stringify({ suggestion_text: "建议明确 owner、P99 阈值、回滚负责人和自动化测试范围。", confidence: 0.88, trigger_reason: "发布评审缺少闭环字段" });
    }
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({
      choices: [{ message: { content } }],
      usage: { prompt_tokens: 120, completion_tokens: 50, total_tokens: 170 },
    }));
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return server;
}

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
  const eventHandlers = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const { resolve, reject } = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(message.error.message)) : resolve(message.result || {});
      return;
    }
    if (!message.method) return;
    (eventHandlers.get(message.method) || []).forEach((handler) => handler(message.params || {}));
  });
  const cdpPage = {
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    on(method, handler) {
      const handlers = eventHandlers.get(method) || [];
      handlers.push(handler);
      eventHandlers.set(method, handlers);
    },
  };
  cdpPage.on("Runtime.exceptionThrown", (params) => {
    const details = params.exceptionDetails || {};
    browserDiagnostics.runtime_exceptions.push({
      text: details.exception?.description || details.text || "uncaught runtime exception",
      url: details.url || "",
      line_number: details.lineNumber ?? null,
      column_number: details.columnNumber ?? null,
    });
  });
  cdpPage.on("Runtime.consoleAPICalled", (params) => {
    if (params.type !== "error") return;
    browserDiagnostics.error_console.push({
      text: (params.args || []).map(runtimeRemoteObjectText).join(" "),
      timestamp: params.timestamp || null,
    });
  });
  cdpPage.on("Network.requestWillBeSent", (params) => {
    networkRequestUrls.set(params.requestId, params.request?.url || "");
  });
  cdpPage.on("Network.loadingFailed", (params) => {
    const entry = {
      request_id: params.requestId,
      url: networkRequestUrls.get(params.requestId) || "",
      error_text: params.errorText || "network loading failed",
      canceled: Boolean(params.canceled),
      resource_type: params.type || "",
    };
    if (!allowlistedNetworkFailure(entry)) browserDiagnostics.network_loading_failed.push(entry);
  });
  cdpPage.on("Network.responseReceived", (params) => {
    const status = Number(params.response?.status || 0);
    if (status < 500) return;
    browserDiagnostics.http_5xx.push({
      request_id: params.requestId,
      url: params.response?.url || networkRequestUrls.get(params.requestId) || "",
      status,
      status_text: params.response?.statusText || "",
      resource_type: params.type || "",
    });
  });
  await cdpPage.send("Runtime.enable");
  await cdpPage.send("Network.enable");
  await cdpPage.send("Page.enable");
  await cdpPage.send("DOM.enable");
  await setViewport(cdpPage, DESKTOP_VIEWPORT);
  await cdpPage.send("Page.navigate", { url });
  await waitForCdpExpression(cdpPage, "document.readyState === 'complete'");
  return cdpPage;
}

async function setViewport(cdpPage, viewport) {
  await cdpPage.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: viewport.deviceScaleFactor,
    mobile: viewport.mobile,
    screenWidth: viewport.width,
    screenHeight: viewport.height,
  });
}

async function readViewportLayoutProbe(cdpPage) {
  return evaluate(cdpPage, () => {
    const isVisible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
    };
    const rectFor = (element) => {
      const rect = element.getBoundingClientRect();
      return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom, width: rect.width, height: rect.height };
    };
    const buttons = Array.from(document.querySelectorAll("button, label.btn")).filter(isVisible);
    const overlappingButtonPairs = [];
    for (let leftIndex = 0; leftIndex < buttons.length; leftIndex++) {
      for (let rightIndex = leftIndex + 1; rightIndex < buttons.length; rightIndex++) {
        const left = buttons[leftIndex];
        const right = buttons[rightIndex];
        const leftRect = left.getBoundingClientRect();
        const rightRect = right.getBoundingClientRect();
        const overlapWidth = Math.min(leftRect.right, rightRect.right) - Math.max(leftRect.left, rightRect.left);
        const overlapHeight = Math.min(leftRect.bottom, rightRect.bottom) - Math.max(leftRect.top, rightRect.top);
        if (overlapWidth > 2 && overlapHeight > 2) {
          overlappingButtonPairs.push([left.id || left.textContent.trim(), right.id || right.textContent.trim()]);
        }
      }
    }
    const majorTextSelector = ".brand-name,#rec-state,.live-status-field,#source-badge,#session-meta,.status-chip,.panel-title,.sug-body,.sug-meta,.utterance .text,button,summary";
    const clippedMajorText = Array.from(document.querySelectorAll(majorTextSelector))
      .filter(isVisible)
      .filter((element) => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1)
      .map((element) => element.id || element.textContent.trim().slice(0, 80));
    return {
      inner_width: window.innerWidth,
      inner_height: window.innerHeight,
      document_scroll_width: document.documentElement.scrollWidth,
      document_client_width: document.documentElement.clientWidth,
      horizontal_overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      overlapping_button_pairs: overlappingButtonPairs,
      clipped_major_text: clippedMajorText,
      primary_actions_rect: rectFor(document.getElementById("primary-actions")),
      transcript_rect: rectFor(document.getElementById("transcript-stream")),
      guidance_rect: rectFor(document.getElementById("realtime-guidance-panel")),
    };
  });
}

async function nodeIdForSelector(cdpPage, selector) {
  const root = await cdpPage.send("DOM.getDocument", { depth: 1 });
  const found = await cdpPage.send("DOM.querySelector", { nodeId: root.root.nodeId, selector });
  if (!found.nodeId) throw new Error(`selector not found: ${selector}`);
  return found.nodeId;
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

async function safeReadPageState(cdpPage) {
  return evaluate(cdpPage, () => ({
    title: document.title,
    sessionMeta: document.getElementById("session-meta")?.innerText,
    sysStatus: document.getElementById("sys-status")?.innerText,
    toast: document.getElementById("toast")?.innerText,
    sourceBadge: document.getElementById("source-badge")?.innerText,
    cockpitStage: document.getElementById("c-cockpit-stage")?.innerText,
    transcript: document.getElementById("transcript-stream")?.innerText,
    suggestions: document.getElementById("suggestions-panel")?.innerText,
    minutes: document.getElementById("minutes-panel")?.innerText,
    utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length,
    suggestionCards: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approachCards: document.querySelectorAll("[data-card-kind='approach']").length,
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

async function captureViewportStep(cdpPage, step, description, viewport, layoutProbe) {
  const fileName = `${String(++stepIndex).padStart(2, "0")}-${step}.png`;
  const screenshotPath = path.join(artifactRoot, fileName);
  const screenshot = await cdpPage.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
    clip: { x: 0, y: 0, width: viewport.width, height: viewport.height, scale: 1 },
  });
  await writeFile(screenshotPath, Buffer.from(screenshot.data, "base64"));
  const state = await safeReadPageState(cdpPage).catch((err) => ({ state_error: err.message }));
  const entry = {
    order: stepIndex,
    step,
    description,
    status: "passed",
    screenshot_path: screenshotPath,
    viewport,
    layout_probe: layoutProbe,
    state,
  };
  screenshots.push({ step, screenshot_path: screenshotPath, viewport });
  checklist.push(entry);
  return entry;
}

// Meeting Copilot client: start/import a meeting, show live transcript, surface
// AI reminders, generate post-meeting review, and keep local session history.
const $ = (id) => document.getElementById(id);
let currentSession = null;
let currentEvents = [];
let currentSuggestionCards = [];
let currentApproachCards = [];
let currentMinutes = null;
let currentAudioAsset = null;
let currentSessionSource = null;
let currentSessionCleanupPending = false;
let currentReadiness = null;
let currentLlmReady = null;
let currentMeetingPhase = "idle";
let currentAutoSuggestionStatus = null;
let currentRealtimeCorrectionStatus = null;
let currentCandidateFocusType = "";
let executedSuggestionCandidateIds = new Set();
let autoSuggestionInFlight = false;
let autoSuggestionPending = false;
let autoSuggestionIdlePromise = Promise.resolve();
let autoSuggestionIdleResolve = null;
let realtimeCorrectionInFlight = false;
let realtimeCorrectionPending = false;
let realtimeCorrectionPendingForce = false;
let realtimeCorrectionRetryTimer = null;
let realtimeCorrectionOwnerSessionId = null;
let realtimeCorrectionGeneration = 0;
let realtimeCorrectionRevisionIds = new Set();
let realtimeCorrectionNoRevisionIds = new Set();
let realtimeCorrectionRevisionCount = 0;
let desktopRuntimeStatus = null;
let preservedSessionBeforeRecording = null;
let restoredRecordingFailureSessionId = null;
let recordingDraftHasClaimedView = false;
let sessionOperationGeneration = 0;
let sessionOperationController = new AbortController();
let recordingStartedAtMs = null;
let recordingStatusTimer = null;
let recordingPausedAtMs = null;
let _recordingPaused = false;
let recordingReminderTimer = null;
const RECORDING_REMINDER_INTERVAL_MS = 30 * 60 * 1000;
let _originalDocumentTitle = document.title;
let _historySessions = [];
let apiBaseUrl = "";
let latestPartialTextBySegment = new Map();
let transcriptRenderStateBySegment = new Map();
let canonicalTranscriptState = createCanonicalTranscriptState();
let candidateReminderEvents = [];
let candidateReminderProjectionCache = null;
let candidateReminderProjectionVersion = 0;
let politeAnnouncementTokens = new Map();
let transcriptFollowEnabled = true;

const MOCK_PAYLOAD = {
  provider: "local_mock_asr",
  streaming_events: [
    { event_type: "final", segment_id: "asr_seg_001", text: "先灰度 5%。", start_ms: 0, end_ms: 3200, received_at_ms: 3500, confidence: 0.91 },
    { event_type: "final", segment_id: "asr_seg_002", text: "谁负责回滚？", start_ms: 3400, end_ms: 6100, received_at_ms: 7000, confidence: 0.9 },
    { event_type: "final", segment_id: "asr_seg_003", text: "如果错误率超过 0.1% 就回滚。", start_ms: 6100, end_ms: 8200, received_at_ms: 8800, confidence: 0.9 },
    { event_type: "final", segment_id: "asr_seg_004", text: "张三下周三补充兼容性测试用例。", start_ms: 8200, end_ms: 10400, received_at_ms: 11200, confidence: 0.9 },
    { event_type: "end_of_stream", end_ms: 11200, received_at_ms: 11300 },
  ],
};

const TRANSCRIPT_EMPTY_MESSAGE = "开始会议后，我会实时记录发言，并提醒决策、待办、风险和待确认问题。";
const CANDIDATE_EMPTY_MESSAGE = "会议中我会实时提醒风险、待办和待确认问题。";
const SUGGESTIONS_EMPTY_MESSAGE = "会议开始并确认发言后，AI 建议会自动出现。";
const APPROACH_EMPTY_MESSAGE = "讨论到方案取舍后，可分析利弊与风险。";
const MINUTES_EMPTY_MESSAGE = "会议结束后，可生成会议纪要、决策、待办、风险和待确认问题。";
const ASR_SEMANTIC_QUALITY_MESSAGE = "识别语义质量不足：声音可用，但没有听清关键业务内容，先不生成正式建议。";
const MIC_ACTIVE_SAMPLE_THRESHOLD = 0.005;
const MIC_MIN_RMS = 0.01;
const MIC_MIN_PEAK = 0.05;
const MIC_MIN_ACTIVE_SAMPLE_RATIO = 0.08;
const PARTIAL_DRAFT_MIN_CHARS = 12;
const PARTIAL_DRAFT_MIN_CONFIDENCE = 0.8;
const MAX_CANDIDATE_REMINDERS_VISIBLE = 3;
const MAX_FORMAL_SUGGESTIONS_VISIBLE = 5;
const ORGANIZE_FAST_SUGGESTION_BUDGET = 1;
const MAX_REALTIME_CORRECTION_DRAIN_BATCHES = 16;
const REALTIME_CORRECTION_DRAIN_TIMEOUT_MS = 20_000;
const STOP_WAIT_FOR_ASR_READY_MS = 35_000;
const TRANSCRIPT_NEAR_BOTTOM_PX = 96;
const CANDIDATE_REMINDER_PRIORITY = {
  DecisionCandidate: 0,
  ActionItem: 1,
  Risk: 2,
  OpenQuestion: 3,
};
const CANDIDATE_FOCUS_LABELS = {
  DecisionCandidate: "决定了什么",
  ActionItem: "待办事项",
  Risk: "风险提醒",
  OpenQuestion: "待确认问题",
};

function shouldShowDemoTools() {
  const params = new URLSearchParams(window.location.search || "");
  return params.get("demo") === "1" || window.localStorage?.getItem("meetingCopilotDemo") === "1";
}

function isNoCostDerivationSelfTest() {
  const params = new URLSearchParams(window.location.search || "");
  return params.get("noCostDerivationSelfTest") === "1"
    || window.localStorage?.getItem("meetingCopilotNoCostDerivationSelfTest") === "1";
}

function initDemoTools() {
  const demoTools = document.querySelector(".demo-disclosure");
  if (!demoTools) return;
  const shouldShow = shouldShowDemoTools();
  demoTools.hidden = !shouldShow;
  // 默认展开details元素，确保按钮可见
  if (shouldShow) {
    demoTools.open = true;
  }
}

function getTauriInvoke() {
  const tauriGlobal = window.__TAURI__;
  return tauriGlobal?.core?.invoke || tauriGlobal?.tauri?.invoke || tauriGlobal?.invoke || null;
}

async function initDesktopRuntimeProbe() {
  const slot = $("s-desktop");
  if (!slot) return;
  const invoke = getTauriInvoke();
  if (!invoke) {
    slot.textContent = "浏览器模式";
    slot.className = "v";
    return;
  }
  slot.textContent = "连接中";
  try {
    const status = await invoke("runtime_get_status");
    desktopRuntimeStatus = status || null;
    const ok = status?.command_status === "ok" || status?.implementation_status === "real";
    if (status?.desktop_api_base_url) apiBaseUrl = String(status.desktop_api_base_url).replace(/\/$/, "");
    slot.textContent = ok ? "桌面壳已连接" : "桌面壳未就绪";
    slot.className = ok ? "v ok" : "v warn";
    writeDesktopFrontendProbe(invoke, status);
  } catch (err) {
    desktopRuntimeStatus = null;
    slot.textContent = "桌面壳未连接";
    slot.className = "v warn";
  }
}

async function writeDesktopFrontendProbe(invoke, runtimeStatus) {
  try {
    const selectorStatus = {};
    ["history-list", "session-meta", "transcript-stream", "suggestions-panel", "approach-panel", "minutes-panel", "s-desktop"].forEach((id) => {
      selectorStatus[id] = Boolean($(id));
    });
    await invoke("runtime_write_frontend_probe", {
      payload: {
        ready_state: document.readyState,
        title: document.title,
        location_path: window.location.pathname,
        user_agent: navigator.userAgent,
        api_base_url: apiBaseUrl,
        desktop_status_text: $("s-desktop")?.textContent || "",
        runtime_status: runtimeStatus || {},
        selectors: selectorStatus,
      },
    });
  } catch (err) {
    console.warn("[workbench] 桌面壳探针写入失败:", err);
  }
}

async function writePackagedBackendApiProbe() {
  const invoke = getTauriInvoke();
  if (!invoke) return;
  const payload = {
    packaged_api_probe: true,
    api_base_url: apiBaseUrl,
    health_ok: false,
    sessions_loaded: false,
    session_count: 0,
    errors: [],
  };
  try {
    const health = await api("/health");
    payload.health_ok = health?.status === "ok";
    payload.health_service = health?.service || "";
  } catch (err) {
    payload.errors.push(`health:${err.message}`);
  }
  try {
    const sessions = await api("/live/asr/sessions");
    payload.sessions_loaded = Array.isArray(sessions?.sessions);
    payload.session_count = Array.isArray(sessions?.sessions) ? sessions.sessions.length : 0;
  } catch (err) {
    payload.errors.push(`sessions:${err.message}`);
  }
  try {
    await invoke("runtime_write_frontend_probe", { payload });
  } catch (err) {
    console.warn("[workbench] 桌面壳后端探针写入失败:", err);
  }
}

async function runPackagedSameChainProbe() {
  const invoke = getTauriInvoke();
  if (!invoke || !desktopRuntimeStatus?.packaged_same_chain_probe_enabled) return;
  const sid = "packaged_probe_" + Date.now().toString(36);
  const payload = {
    packaged_same_chain_probe: true,
    chain_mode: "no_cost_controlled",
    api_base_url: apiBaseUrl,
    session_id: sid,
    uses_mock_asr_session: true,
    uses_deterministic_demo_derivation: true,
    session_created: false,
    events_ingested: false,
    events_visible_in_api: false,
    events_visible_in_workbench: false,
    same_session_id_observed: false,
    transcript_visible: false,
    suggestion_card_count: 0,
    approach_card_count: 0,
    minutes_visible: false,
    history_visible: false,
    delete_verified: false,
    history_removed_after_delete: false,
    captures_audio: false,
    spawns_process: false,
    calls_remote_provider: false,
    raw_audio_uploaded: false,
    remote_asr_called: false,
    remote_llm_called: false,
    paid_provider_called: false,
    errors: [],
  };
  let deleteAttempted = false;
  try {
    const created = await api("/live/asr/mock/sessions", {
      method: "POST",
      body: JSON.stringify({ ...MOCK_PAYLOAD, session_id: sid }),
    });
    payload.session_created = created?.session_id === sid;
    payload.events_ingested = Array.isArray(created?.live_events) && created.live_events.length > 0;

    const snapshot = await api(`/live/asr/sessions/${sid}/events`);
    payload.events_visible_in_api = Array.isArray(snapshot?.events) && sessionHasTranscript(snapshot);

    prepareNewSession();
    currentSession = sid;
    applySessionEvents(sid, snapshot, "桌面打包主链路自检正在运行。");
    setMeetingPhase("ready");
    payload.events_visible_in_workbench = document.querySelectorAll(".utterance").length > 0;
    payload.transcript_visible = payload.events_visible_in_workbench && $("transcript-stream")?.innerText.trim().length > 0;

    const suggestionRuns = await api(`/live/asr/demo/sessions/${sid}/llm-execution-runs`, {
      method: "POST",
      body: JSON.stringify({ mode: "deterministic_demo" }),
    });
    renderRealCards(suggestionRuns?.runs || []);
    payload.suggestion_card_count = document.querySelectorAll("[data-card-kind='suggestion']").length;

    const approach = await api(`/live/asr/demo/sessions/${sid}/approach-cards`, {
      method: "POST",
      body: JSON.stringify({ mode: "deterministic_demo" }),
    });
    renderApproachCards(approach?.approach_cards || []);
    payload.approach_card_count = document.querySelectorAll("[data-card-kind='approach']").length;

    const minutes = await api(`/live/asr/demo/sessions/${sid}/minutes`, {
      method: "POST",
      body: JSON.stringify({ mode: "deterministic_demo" }),
    });
    renderMinutes(minutes?.minutes_md || "");
    payload.minutes_visible = $("minutes-panel")?.innerText.includes("会议纪要") || false;

    await loadSessionHistory();
    payload.history_visible = $("history-list")?.innerText.includes(sid) || false;
    payload.same_session_id_observed = currentSession === sid;

    const deleted = await api(`/live/asr/sessions/${sid}`, { method: "DELETE" });
    deleteAttempted = true;
    payload.delete_verified = deleted?.deleted === true && deleted?.session_record_deleted === true;
    await loadSessionHistory();
    payload.history_removed_after_delete = !($("history-list")?.innerText.includes(sid));
  } catch (err) {
    payload.errors.push(err.message || String(err));
  } finally {
    if (!deleteAttempted) {
      try {
        const deleted = await api(`/live/asr/sessions/${sid}`, { method: "DELETE" });
        payload.delete_verified = deleted?.deleted === true || payload.delete_verified;
      } catch (cleanupErr) {
        if (!String(cleanupErr.message || "").includes("not found")) {
          payload.errors.push(`cleanup:${cleanupErr.message || String(cleanupErr)}`);
        }
      }
    }
    resetSessionView();
    setMeetingPhase("idle");
    try {
      await invoke("runtime_write_frontend_probe", { payload });
    } catch (probeErr) {
      console.warn("[workbench] 桌面壳同链路探针写入失败:", probeErr);
    }
  }
}

function pendingMicSource() {
  return {
    audio_source: "real_mic",
    provider: "browser_microphone",
    provider_mode: "pending",
    input_source: "real_mic",
    pending_verification: true,
  };
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(window._tt);
  window._tt = setTimeout(() => t.classList.remove("show"), 2200);
}

function announcePoliteStatus(elementId, message) {
  const region = $(elementId);
  if (!region || !message) return;
  const token = (politeAnnouncementTokens.get(elementId) || 0) + 1;
  politeAnnouncementTokens.set(elementId, token);
  region.textContent = "";
  window.requestAnimationFrame(() => {
    if (politeAnnouncementTokens.get(elementId) !== token) return;
    region.textContent = message;
  });
}

function clearPoliteAnnouncements() {
  ["transcript-live-region", "reminder-live-region"].forEach((elementId) => {
    politeAnnouncementTokens.set(elementId, (politeAnnouncementTokens.get(elementId) || 0) + 1);
    const region = $(elementId);
    if (region) region.textContent = "";
  });
}

function announceCommittedTranscript(text, isRevision = false) {
  const prefix = isRevision ? "AI 已校正发言" : "已确认发言";
  announcePoliteStatus("transcript-live-region", `${prefix}：${text}`);
}

function announceRealtimeReminder(event = {}) {
  const text = candidateReminderText(event);
  if (text) announcePoliteStatus("reminder-live-region", `实时提醒：${text}`);
}

function setTranscriptModeLabel(label) {
  const slot = $("transcript-mode-label");
  if (slot) slot.textContent = label;
}

function getTranscriptScrollTarget() {
  if (window.matchMedia("(max-width: 900px)").matches) {
    return document.scrollingElement || document.documentElement;
  }
  return document.querySelector(".center");
}

function isTranscriptNearBottom(target = getTranscriptScrollTarget()) {
  if (!target) return true;
  return target.scrollHeight - target.clientHeight - target.scrollTop <= TRANSCRIPT_NEAR_BOTTOM_PX;
}

function setNewTranscriptContentVisible(visible) {
  const button = $("btn-new-transcript-content");
  if (!button) return;
  if (visible) {
    $("btn-new-transcript-content").hidden = false;
  } else {
    $("btn-new-transcript-content").hidden = true;
  }
}

function captureTranscriptFollowState() {
  const target = getTranscriptScrollTarget();
  return {
    target,
    previousScrollTop: target?.scrollTop || 0,
    shouldFollow: transcriptFollowEnabled && isTranscriptNearBottom(target),
  };
}

function scrollTranscriptToBottom(target = getTranscriptScrollTarget(), { smooth = false } = {}) {
  if (!target) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const behavior = smooth && !reducedMotion ? "smooth" : "auto";
  if (target === document.scrollingElement || target === document.documentElement || target === document.body) {
    window.scrollTo({ top: target.scrollHeight, behavior });
    return;
  }
  target.scrollTo({ top: target.scrollHeight, behavior });
}

function syncTranscriptAfterRender(snapshot, { contentChanged = true } = {}) {
  if (!snapshot?.target) return;
  if (snapshot.shouldFollow) {
    transcriptFollowEnabled = true;
    setNewTranscriptContentVisible(false);
    scrollTranscriptToBottom(snapshot.target);
    return;
  }
  snapshot.target.scrollTop = snapshot.previousScrollTop;
  if (contentChanged) setNewTranscriptContentVisible(true);
}

function pauseTranscriptFollowing() {
  transcriptFollowEnabled = false;
}

function resumeTranscriptFollowing({ scroll = true } = {}) {
  transcriptFollowEnabled = true;
  setNewTranscriptContentVisible(false);
  if (scroll) scrollTranscriptToBottom(getTranscriptScrollTarget(), { smooth: true });
}

function bindTranscriptScrollFollow() {
  const updateFollowState = () => {
    const target = getTranscriptScrollTarget();
    if (!target) return;
    transcriptFollowEnabled = isTranscriptNearBottom(target);
    if (transcriptFollowEnabled) setNewTranscriptContentVisible(false);
  };
  document.querySelector(".center")?.addEventListener("scroll", updateFollowState, { passive: true });
  window.addEventListener("scroll", updateFollowState, { passive: true });
  $("btn-new-transcript-content")?.addEventListener("click", () => resumeTranscriptFollowing());
}

function formatRecordingDuration(elapsedMs = 0) {
  const totalSeconds = Math.max(0, Math.floor(Number(elapsedMs || 0) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const minuteText = String(minutes).padStart(2, "0");
  const secondText = String(seconds).padStart(2, "0");
  return hours > 0 ? `${String(hours).padStart(2, "0")}:${minuteText}:${secondText}` : `${minuteText}:${secondText}`;
}

function recordingElapsedMs() {
  if (recordingStartedAtMs === null) return 0;
  if (_recordingPaused && recordingPausedAtMs !== null) {
    return recordingPausedAtMs - recordingStartedAtMs;
  }
  return Date.now() - recordingStartedAtMs;
}

function updateRecordingDuration() {
  const slot = $("recording-duration");
  if (!slot || recordingStartedAtMs === null) return;
  const elapsed = recordingElapsedMs();
  slot.textContent = formatRecordingDuration(elapsed);
  if (currentMeetingPhase === "recording") {
    if (_recordingPaused) {
      document.title = "⏸ 已暂停 " + formatRecordingDuration(elapsed);
    } else {
      document.title = "● 录音中 " + formatRecordingDuration(elapsed);
    }
  }
}

function setMicInputStatus(label) {
  const slot = $("mic-input-status");
  if (slot) slot.textContent = label;
}

let selectedMicrophoneDeviceId = "";

function setMicLevelMeter(level = 0) {
  const meter = $("mic-level-meter");
  if (!meter) return;
  const safeLevel = Math.min(1, Math.max(0, Number(level) || 0));
  meter.value = safeLevel;
  meter.dataset.state = safeLevel < 0.01 ? "silent" : safeLevel < 0.04 ? "quiet" : "active";
}

async function refreshMicrophoneDevices() {
  const select = $("mic-device-select");
  const mediaDevices = navigator.mediaDevices;
  if (!select || !mediaDevices || typeof mediaDevices.enumerateDevices !== "function") return [];
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inputs = devices.filter((device) => device.kind === "audioinput");
    const current = selectedMicrophoneDeviceId;
    select.innerHTML = "";
    const defaultOption = document.createElement("option");
    defaultOption.value = "";
    defaultOption.textContent = "系统默认麦克风";
    select.appendChild(defaultOption);
    inputs.forEach((device, index) => {
      if (!device.deviceId || device.deviceId === "default") return;
      const option = document.createElement("option");
      option.value = device.deviceId;
      option.textContent = device.label || `麦克风 ${index + 1}`;
      select.appendChild(option);
    });
    const hasCurrent = [...select.options].some((option) => option.value === current);
    select.value = hasCurrent ? current : "";
    selectedMicrophoneDeviceId = select.value;
    select.disabled = inputs.length === 0;
    return inputs;
  } catch (error) {
    select.disabled = true;
    console.warn("[workbench] 无法枚举麦克风设备", error);
    return [];
  }
}

$("mic-device-select")?.addEventListener("change", (event) => {
  selectedMicrophoneDeviceId = String(event.target.value || "");
});

function stopRecordingStatusTimer({ reset = false } = {}) {
  if (recordingStatusTimer !== null) clearInterval(recordingStatusTimer);
  recordingStatusTimer = null;
  if (recordingReminderTimer !== null) {
    clearInterval(recordingReminderTimer);
    recordingReminderTimer = null;
  }
  document.title = _originalDocumentTitle;
  if (reset) {
    recordingStartedAtMs = null;
    recordingPausedAtMs = null;
    _recordingPaused = false;
    const duration = $("recording-duration");
    if (duration) duration.textContent = "00:00";
    setMicInputStatus("未连接");
    setMicLevelMeter(0);
    return;
  }
  updateRecordingDuration();
}

function clearRealtimeCorrectionRetryTimer() {
  if (realtimeCorrectionRetryTimer !== null) window.clearTimeout(realtimeCorrectionRetryTimer);
  realtimeCorrectionRetryTimer = null;
}

function resetRealtimeCorrectionState() {
  realtimeCorrectionGeneration++;
  realtimeCorrectionInFlight = false;
  realtimeCorrectionPending = false;
  realtimeCorrectionPendingForce = false;
  realtimeCorrectionOwnerSessionId = null;
  clearRealtimeCorrectionRetryTimer();
  realtimeCorrectionRevisionIds = new Set();
  realtimeCorrectionNoRevisionIds = new Set();
  realtimeCorrectionRevisionCount = 0;
}

function scheduleRealtimeCorrectionRetry(delayMs, sessionId = currentSession) {
  const sid = String(sessionId || "");
  const delay = Math.max(250, Number(delayMs) || 0);
  if (!sid || currentMeetingPhase !== "recording") return;
  clearRealtimeCorrectionRetryTimer();
  realtimeCorrectionRetryTimer = window.setTimeout(() => {
    realtimeCorrectionRetryTimer = null;
    if (currentSession === sid && currentMeetingPhase === "recording") {
      runRealtimeCorrectionsOnce({ force: false, sessionId: sid });
    }
  }, delay);
}

function startRecordingStatusTimer() {
  stopRecordingStatusTimer();
  recordingStartedAtMs = Date.now();
  recordingPausedAtMs = null;
  _recordingPaused = false;
  updateRecordingDuration();
  setMicInputStatus("已连接");
  recordingStatusTimer = setInterval(updateRecordingDuration, 1000);
  recordingReminderTimer = setInterval(() => {
    if (!_recordingPaused) {
      const elapsedMin = Math.floor(recordingElapsedMs() / 60000);
      if (elapsedMin > 0 && elapsedMin % 30 === 0) {
        toast(`已录音 ${elapsedMin} 分钟`);
      }
    }
  }, 60 * 1000);
}

function setMeetingPhase(phase) {
  const previousPhase = currentMeetingPhase;
  currentMeetingPhase = phase;
  if (phase === "recording" && previousPhase !== "recording") {
    startRecordingStatusTimer();
  } else if (phase !== "recording" && previousPhase === "recording") {
    stopRecordingStatusTimer();
  }
  if (phase === "idle") {
    stopRecordingStatusTimer({ reset: true });
  } else if (phase === "processing" || phase === "ready") {
    setMicInputStatus(recordingStartedAtMs === null ? "未使用" : "已停止");
  }
  const state = $("rec-state");
  const recordButton = $("btn-record");
  const stopButton = $("btn-stop");
  const minutesButton = $("btn-minutes");
  if (phase === "recording") {
    state.textContent = "● 录音中";
    state.style.color = "var(--risk)";
    setTranscriptModeLabel("已记录 + 正在听");
    recordButton.textContent = "开始会议";
    recordButton.disabled = true;
    stopButton.textContent = "结束会议";
    stopButton.disabled = false;
    minutesButton.disabled = true;
  } else if (phase === "processing") {
    state.textContent = "● 正在整理";
    state.style.color = "var(--warn)";
    setTranscriptModeLabel("整理中");
    recordButton.textContent = "正在整理...";
    recordButton.disabled = true;
    stopButton.disabled = true;
    minutesButton.disabled = true;
  } else if (phase === "ready") {
    state.textContent = "● 已生成文字";
    state.style.color = "var(--ok)";
    setTranscriptModeLabel("已记录");
    recordButton.textContent = "开始会议";
    recordButton.disabled = false;
    stopButton.disabled = true;
    minutesButton.disabled = false;
  } else {
    state.textContent = "● 还没有开始会议";
    state.style.color = "var(--fg-muted)";
    setTranscriptModeLabel("待开始");
    recordButton.textContent = "开始会议";
    recordButton.disabled = false;
    stopButton.disabled = true;
    minutesButton.disabled = !currentSession;
  }
  syncActionAvailability(phase);
  syncMeetingOverview();
}

function syncActionAvailability(phase) {
  const recording = phase === "recording" || phase === "processing";
  const hasTranscript = Boolean(currentSession) && currentSessionHasTranscript();
  const cleanupPending = currentSessionCleanupPending;
  const llmUnavailable = currentLlmReady === false;
  syncSessionToolVisibility(phase);
  $("btn-upload").disabled = recording;
  $("btn-load").disabled = recording;
  $("btn-delete").disabled = recording || !currentSession;
  $("btn-delete").textContent = cleanupPending ? "重试清理录音" : "删除本次会议";
  $("btn-cards").disabled = recording || cleanupPending || !hasTranscript || llmUnavailable;
  $("btn-approach").disabled = recording || cleanupPending || !hasTranscript || llmUnavailable;
  $("btn-organize").disabled = recording || cleanupPending || !hasTranscript || llmUnavailable;
  $("btn-live").disabled = recording || cleanupPending || !currentSession;
  $("btn-minutes").disabled = recording || cleanupPending || !hasTranscript || llmUnavailable;
  $("btn-export-transcript").disabled = recording || cleanupPending || !hasTranscript;
  $("btn-export-minutes").disabled = recording || cleanupPending || !currentMinutes;
  $("btn-export-audio").disabled = recording || cleanupPending || !currentSessionHasAudio();
  const btnPause = $("btn-pause");
  if (btnPause) {
    btnPause.hidden = phase !== "recording";
    btnPause.textContent = _recordingPaused ? "继续" : "暂停";
  }
  syncAutoSuggestionControl();
  const uploadLabel = $("btn-upload-label");
  if (uploadLabel) uploadLabel.disabled = recording;
  updateRecordButtonReadiness(phase);
}

function syncSessionToolVisibility(phase = currentMeetingPhase) {
  const hasSession = Boolean(currentSession);
  const meetingInProgress = phase === "recording" || phase === "processing";
  $("session-actions").hidden = !hasSession;
  $("secondary-actions").hidden = !hasSession;
  const reviewWorkspace = $("review-workspace");
  if (reviewWorkspace) {
    reviewWorkspace.hidden = !hasSession || meetingInProgress;
    reviewWorkspace.open = hasSession && !meetingInProgress;
  }
  $("btn-stop").hidden = phase !== "recording" && phase !== "processing";
  $("btn-record").hidden = phase === "recording" || phase === "processing";
}

function updateRecordButtonReadiness(phase = currentMeetingPhase) {
  const recordButton = $("btn-record");
  if (phase === "recording" || phase === "processing") return;
  if (currentReadiness && !currentReadiness.realtime_asr_available) {
    recordButton.disabled = true;
    recordButton.textContent = "导入录音继续";
    recordButton.title = "当前不能实时识别，请先导入录音。";
    return;
  }
  recordButton.disabled = false;
  recordButton.textContent = "开始会议";
  recordButton.title = "";
}

function cloneAutoSuggestionStatus(status) {
  if (!status) return null;
  return {
    ...status,
    processed_candidate_ids: [...(status.processed_candidate_ids || [])],
    suppressed: (status.suppressed || []).map((item) => ({ ...item })),
  };
}

function beginSessionOperation() {
  sessionOperationController.abort();
  sessionOperationController = new AbortController();
  sessionOperationGeneration += 1;
  return {
    generation: sessionOperationGeneration,
    signal: sessionOperationController.signal,
  };
}

function currentSessionOperation() {
  return {
    generation: sessionOperationGeneration,
    signal: sessionOperationController.signal,
  };
}

function isCurrentSessionOperation(operation) {
  return Boolean(
    operation
    && operation.generation === sessionOperationGeneration
    && operation.signal === sessionOperationController.signal
    && !operation.signal.aborted
  );
}

function resetSessionView(message = TRANSCRIPT_EMPTY_MESSAGE, options = {}) {
  const sessionOperation = options.sessionOperation || beginSessionOperation();
  if (!isCurrentSessionOperation(sessionOperation)) return false;
  resetRealtimeCorrectionState();
  clearPoliteAnnouncements();
  if (_micWs) { try { _manualStop = true; _micWs.close(); } catch {} _micWs = null; }
  stopAudioCapture();
  currentSession = null;
  currentSessionCleanupPending = false;
  _recSid = null;
  recordingDraftHasClaimedView = false;
  currentEvents = [];
  replaceCandidateReminderEvents([]);
  latestPartialTextBySegment.clear();
  transcriptRenderStateBySegment.clear();
  canonicalTranscriptState = createCanonicalTranscriptState();
  currentSuggestionCards = [];
  currentApproachCards = [];
  currentMinutes = null;
  currentAudioAsset = null;
  currentSessionSource = null;
  currentAutoSuggestionStatus = null;
  currentCandidateFocusType = "";
  resumeTranscriptFollowing({ scroll: false });
  executedSuggestionCandidateIds = new Set();
  renderAutoSuggestionStatus(null);
  renderRealtimeCorrectionStatus(null);
  syncCandidateFocusButtons();
  renderCanonicalTranscriptEmptyState(message);
  $("session-meta").textContent = "准备开始";
  $("source-badge").className = "source-badge neutral";
  $("source-badge").textContent = "未开始";
  $("source-badge").title = "";
  $("c-decision").textContent = "0";
  $("c-action").textContent = "0";
  $("c-risk").textContent = "0";
  $("c-question").textContent = "0";
  $("c-gap").textContent = "0";
  $("c-approach").textContent = "0";
  $("s-candidates").textContent = "0";
  $("s-cards").textContent = "0";
  $("s-approach-cards").textContent = "0";
  $("s-asr").textContent = "—";
  $("s-llm").textContent = "—";
  $("sys-status").innerHTML = `<div class="empty">还没有开始。</div>`;
  $("candidate-panel").innerHTML = `<div class="empty">${CANDIDATE_EMPTY_MESSAGE}</div>`;
  $("suggestions-panel").innerHTML = `<div class="empty">${SUGGESTIONS_EMPTY_MESSAGE}</div>`;
  $("approach-panel").innerHTML = `<div class="empty">${APPROACH_EMPTY_MESSAGE}</div>`;
  $("minutes-panel").innerHTML = `<div class="empty">${MINUTES_EMPTY_MESSAGE}</div>`;
  syncMeetingOverview();
  setMeetingPhase("idle");
  return true;
}

function prepareNewSession(message = "", options = {}) {
  const sessionOperation = options.sessionOperation || beginSessionOperation();
  if (!isCurrentSessionOperation(sessionOperation)) return false;
  resetRealtimeCorrectionState();
  clearPoliteAnnouncements();
  stopRecordingStatusTimer({ reset: true });
  recordingDraftHasClaimedView = true;
  currentSessionCleanupPending = false;
  currentEvents = [];
  replaceCandidateReminderEvents([]);
  latestPartialTextBySegment.clear();
  transcriptRenderStateBySegment.clear();
  canonicalTranscriptState = createCanonicalTranscriptState();
  currentSuggestionCards = [];
  currentApproachCards = [];
  currentMinutes = null;
  currentAudioAsset = null;
  currentSessionSource = null;
  currentAutoSuggestionStatus = null;
  currentCandidateFocusType = "";
  resumeTranscriptFollowing({ scroll: false });
  executedSuggestionCandidateIds = new Set();
  renderAutoSuggestionStatus(null);
  renderRealtimeCorrectionStatus(null);
  syncCandidateFocusButtons();
  renderCanonicalTranscriptEmptyState(message || TRANSCRIPT_EMPTY_MESSAGE);
  ["c-decision", "c-action", "c-risk", "c-question", "c-gap", "c-approach", "c-transcript", "c-cards", "s-candidates", "s-cards", "s-approach-cards"].forEach((id) => {
    $(id).textContent = "0";
  });
  $("s-llm").textContent = "—";
  $("source-badge").className = "source-badge neutral";
  $("source-badge").textContent = "未开始";
  $("source-badge").title = "";
  $("candidate-panel").innerHTML = `<div class="empty">${CANDIDATE_EMPTY_MESSAGE}</div>`;
  $("suggestions-panel").innerHTML = `<div class="empty">${SUGGESTIONS_EMPTY_MESSAGE}</div>`;
  $("approach-panel").innerHTML = `<div class="empty">${APPROACH_EMPTY_MESSAGE}</div>`;
  $("minutes-panel").innerHTML = `<div class="empty">${MINUTES_EMPTY_MESSAGE}</div>`;
  syncMeetingOverview();
  return true;
}

function preserveSessionBeforeRecording() {
  if (!currentSession || !currentSessionHasTranscript()) {
    preservedSessionBeforeRecording = null;
    restoredRecordingFailureSessionId = null;
    return;
  }
  preservedSessionBeforeRecording = {
    session: currentSession,
    events: [...currentEvents],
    suggestionCards: [...currentSuggestionCards],
    approachCards: [...currentApproachCards],
    minutes: currentMinutes,
    audio: currentAudioAsset ? { ...currentAudioAsset } : null,
    source: currentSessionSource ? { ...currentSessionSource } : null,
    autoSuggestionStatus: cloneAutoSuggestionStatus(currentAutoSuggestionStatus),
    executedSuggestionCandidateIds: [...executedSuggestionCandidateIds],
    candidateFocusType: currentCandidateFocusType,
  };
  restoredRecordingFailureSessionId = null;
}

function restorePreservedSessionAfterRecordingFailure(message, failedSessionId = null) {
  if (!preservedSessionBeforeRecording) return false;
  resetRealtimeCorrectionState();
  recordingDraftHasClaimedView = false;
  const preserved = preservedSessionBeforeRecording;
  currentSession = preserved.session;
  currentEvents = [...preserved.events];
  replaceCandidateReminderEvents(currentEvents);
  currentSuggestionCards = [...preserved.suggestionCards];
  currentApproachCards = [...preserved.approachCards];
  currentMinutes = preserved.minutes;
  currentAudioAsset = preserved.audio;
  currentSessionSource = preserved.source;
  currentAutoSuggestionStatus = cloneAutoSuggestionStatus(preserved.autoSuggestionStatus);
  executedSuggestionCandidateIds = new Set(preserved.executedSuggestionCandidateIds);
  currentCandidateFocusType = preserved.candidateFocusType;
  renderAutoSuggestionStatus(currentAutoSuggestionStatus);
  syncCandidateFocusButtons();
  renderSourceBadge(currentSessionSource || {});
  renderTranscriptAndCandidates(currentEvents);
  renderSuggestionCards(currentSuggestionCards);
  renderApproachCardList(currentApproachCards);
  if (currentMinutes) renderMinutes(currentMinutes);
  else $("minutes-panel").innerHTML = `<div class="empty">${MINUTES_EMPTY_MESSAGE}</div>`;
  $("session-meta").textContent = `已保留上一场会议 · ${currentSession}`;
  $("sys-status").innerHTML = `<div class="empty">${escapeHtml(message)} 已保留上一场会议，失败的麦克风会话可在历史记录中查看。</div>`;
  setMeetingPhase("ready");
  if (failedSessionId) restoredRecordingFailureSessionId = failedSessionId;
  preservedSessionBeforeRecording = null;
  return true;
}

function startRecordingDraftSession(sessionId, options = {}) {
  const sessionOperation = options.sessionOperation || beginSessionOperation();
  if (!isCurrentSessionOperation(sessionOperation)) return false;
  resetRealtimeCorrectionState();
  clearPoliteAnnouncements();
  currentSession = sessionId;
  currentSessionCleanupPending = false;
  currentEvents = [];
  replaceCandidateReminderEvents([]);
  latestPartialTextBySegment.clear();
  transcriptRenderStateBySegment.clear();
  canonicalTranscriptState = createCanonicalTranscriptState();
  currentSuggestionCards = [];
  currentApproachCards = [];
  currentMinutes = null;
  currentAudioAsset = null;
  currentAutoSuggestionStatus = null;
  currentCandidateFocusType = "";
  executedSuggestionCandidateIds = new Set();
  renderAutoSuggestionStatus(null);
  renderRealtimeCorrectionStatus(null);
  syncCandidateFocusButtons();
  recordingDraftHasClaimedView = false;
  // The previous session remains in memory for failure recovery, but the
  // visible workbench must represent the new meeting from its first frame.
  prepareNewSession("正在听，会实时显示文字。请保持麦克风有声音，结束后会整理成完整文字。", { sessionOperation });
  if (preservedSessionBeforeRecording) {
    $("session-meta").textContent = `录音中 · ${sessionId}`;
    $("sys-status").innerHTML = `<div class="empty">正在听新会议。上一场会议已安全保留，遇到麦克风失败时会恢复。</div>`;
    $("s-asr").textContent = "待确认";
    $("s-llm").textContent = "—";
  }
  currentSessionSource = pendingMicSource();
  renderSourceBadge(currentSessionSource);
  return true;
}

function claimRecordingDraftView() {
  if (recordingDraftHasClaimedView) return;
  recordingDraftHasClaimedView = true;
  canonicalTranscriptState = createCanonicalTranscriptState();
  renderCanonicalTranscriptEmptyState("正在识别新的会议内容。");
  ["c-decision", "c-action", "c-risk", "c-question", "c-gap", "c-approach", "c-transcript", "c-cards", "s-candidates", "s-cards", "s-approach-cards"].forEach((id) => {
    $(id).textContent = "0";
  });
  currentSuggestionCards = [];
  currentApproachCards = [];
  currentMinutes = null;
  currentAutoSuggestionStatus = null;
  currentCandidateFocusType = "";
  renderAutoSuggestionStatus(null);
  renderRealtimeCorrectionStatus(null);
  syncCandidateFocusButtons();
  $("candidate-panel").innerHTML = `<div class="empty">${CANDIDATE_EMPTY_MESSAGE}</div>`;
  $("suggestions-panel").innerHTML = `<div class="empty">${SUGGESTIONS_EMPTY_MESSAGE}</div>`;
  $("approach-panel").innerHTML = `<div class="empty">${APPROACH_EMPTY_MESSAGE}</div>`;
  $("minutes-panel").innerHTML = `<div class="empty">${MINUTES_EMPTY_MESSAGE}</div>`;
  syncMeetingOverview();
}

function fmtMs(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const mm = String(minutes).padStart(2, "0");
  const ss = String(seconds).padStart(2, "0");
  return hours > 0 ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

function apiErrorMessage(body = {}, fallback = "请求失败") {
  const detail = body?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
    if (typeof detail.reason === "string" && detail.reason.trim()) return detail.reason;
    if (typeof detail.error === "string" && detail.error.trim()) return detail.error;
    try { return JSON.stringify(detail); } catch {}
  }
  if (typeof body?.message === "string" && body.message.trim()) return body.message;
  return fallback || "请求失败";
}

function operationErrorMessage(error, fallback = "请求失败") {
  const bodyMessage = apiErrorMessage(error?.body || {}, "");
  const rawMessage = String(bodyMessage || error?.message || "").trim();
  if (!rawMessage || ["Failed to fetch", "Load failed", "NetworkError"].includes(rawMessage)) {
    return `${fallback}，请检查会议服务是否仍在运行。`;
  }
  return rawMessage;
}

async function api(path, opts = {}) {
  const { timeoutMs = 0, ...fetchOptions } = opts;
  const controller = timeoutMs > 0 && !fetchOptions.signal ? new AbortController() : null;
  const timeoutId = controller
    ? window.setTimeout(() => controller.abort(), Math.max(1, Number(timeoutMs)))
    : null;
  try {
    const r = await fetch(apiUrl(path), {
      headers: { "Content-Type": "application/json" },
      ...fetchOptions,
      ...(controller ? { signal: controller.signal } : {}),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(apiErrorMessage(body, r.statusText)), { status: r.status, body });
    return body;
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
  }
}

function apiUrl(path) {
  if (!apiBaseUrl || /^https?:\/\//.test(path)) return path;
  return `${apiBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
}

function apiWsUrl(path) {
  const base = apiBaseUrl
    ? apiBaseUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

function sessionSourceInfo(source = {}, body = {}) {
  const provider = source.provider || "";
  const providerMode = source.provider_mode || body.provider_mode || (source.is_mock || body.is_mock ? "mock" : "real");
  const reasons = [...new Set([
    ...(body.degradation_reasons || []),
    ...(source.degradation_reasons || []),
  ])];
  const blockers = [...new Set([
    ...(body.acceptance_blockers || []),
    ...(source.acceptance_blockers || []),
  ])];
  const acceptanceKnown = typeof source.acceptance_eligible === "boolean" || typeof body.acceptance_eligible === "boolean";
  const acceptanceEligible = Boolean(source.acceptance_eligible || body.acceptance_eligible);
  const fallbackUsed = Boolean(source.asr_fallback_used || body.asr_fallback_used);
  const isMock = Boolean(source.is_mock || body.is_mock || providerMode === "mock" || provider.includes("mock") || provider.includes("fake"));
  const emptyAsr = reasons.includes("asr_final_empty") || reasons.includes("asr_no_final");
  const semanticQualityBlocked = reasons.includes("asr_semantic_quality_blocked") || blockers.includes("asr_semantic_quality_blocked");
  const realMicSource = source.input_source === "real_mic" || source.audio_source === "real_mic" || body.input_source === "real_mic";
  if (source.input_source === "simulated_realtime_wav" || source.audio_source === "simulated_realtime_wav" || body.input_source === "simulated_realtime_wav") {
    return {
      kind: "simulated_realtime",
      label: "模拟实时",
      shortLabel: "模拟实时",
      warning: "使用音频文件模拟实时流，不计入麦克风验收。",
      counts_as_real_mic_go_evidence: false,
      className: "history-source simulated_realtime",
    };
  }
  if (source.input_source === "real_mic_recorded_wav" || source.audio_source === "real_mic_recorded_wav" || body.input_source === "real_mic_recorded_wav") {
    return {
      kind: "real_mic_recorded",
      label: "真实麦克风录音",
      shortLabel: "麦克风录音",
      warning: "真实麦克风采集后的录音回放实时流，可计入麦克风录音链路；不等同于浏览器实时采集。",
      counts_as_real_mic_go_evidence: true,
      browser_live_mic_go_evidence: false,
      className: "history-source real_mic_recorded",
    };
  }
  const missingRealMicTranscript = realMicSource && acceptanceKnown && !acceptanceEligible && (
    blockers.includes("asr_final_missing") || blockers.includes("asr_transcript_empty")
  );
  if (source.pending_verification || providerMode === "pending") {
    return {
      kind: "pending",
      label: "麦克风待确认",
      shortLabel: "待确认",
      warning: "服务端识别确认前，本次只表示浏览器已开始采集麦克风，不能作为真实验收。",
      className: "history-source pending",
    };
  }
  if (semanticQualityBlocked || fallbackUsed || emptyAsr || reasons.length) {
    return {
      kind: "degraded",
      label: "降级",
      shortLabel: "降级",
      warning: fallbackUsed
        ? "非真实识别：本地实时识别不可用，本次不能作为真实验收。"
        : (semanticQualityBlocked ? ASR_SEMANTIC_QUALITY_MESSAGE : (emptyAsr ? "未识别到有效语音" : `识别降级：${reasons.join(", ") || "真实识别不可用"}`)),
      className: "history-source degraded",
    };
  }
  if (missingRealMicTranscript) {
    return {
      kind: "degraded",
      label: "真实麦克风未通过",
      shortLabel: "未通过",
      warning: "真实麦克风未产生可用文字，本次不能作为真实会议证据。",
      className: "history-source degraded",
    };
  }
  if (isMock) {
    return {
      kind: "demo",
      label: "演示会议",
      shortLabel: "演示",
      warning: "示例内容，不计入真实验收。",
      className: "history-source demo",
    };
  }
  if (provider === "local_funasr_batch") {
    return { kind: "file", label: "导入录音", shortLabel: "导入录音", warning: "", className: "history-source file" };
  }
  if (source.audio_source === "real_mic" || provider.includes("microphone") || provider.includes("sherpa") || provider.includes("funasr_realtime")) {
    return { kind: "real_mic", label: "真实麦克风", shortLabel: "麦克风", warning: "", className: "history-source real" };
  }
  if (provider) {
    return { kind: "provider", label: provider, shortLabel: provider, warning: "", className: "history-source provider" };
  }
  return { kind: "local", label: "本机识别", shortLabel: "本机识别", warning: "", className: "history-source local" };
}

function sessionSourceLabel(source = {}, body = {}) {
  return sessionSourceInfo(source, body).shortLabel;
}

function renderSourceBadge(source = {}, body = {}) {
  const badge = $("source-badge");
  if (!badge) return;
  if (!currentSession) {
    badge.className = "source-badge neutral";
    badge.textContent = "未开始";
    badge.title = "";
    return;
  }
  const info = sessionSourceInfo(source, body);
  badge.className = `source-badge ${info.kind}`;
  badge.textContent = info.shortLabel;
  badge.title = info.warning || info.label;
}

function isSemanticQualityBlocked(body = {}) {
  const source = body.event_source || {};
  const blockers = new Set([
    ...(body.acceptance_blockers || []),
    ...(source.acceptance_blockers || []),
  ]);
  const reasons = new Set([
    ...(body.degradation_reasons || []),
    ...(source.degradation_reasons || []),
  ]);
  return Boolean(
    body.asr_semantic_quality?.blocker === "asr_semantic_quality_blocked"
    || source.asr_semantic_quality?.blocker === "asr_semantic_quality_blocked"
    || blockers.has("asr_semantic_quality_blocked")
    || reasons.has("asr_semantic_quality_blocked")
  );
}

function sessionHasTranscript(body = {}) {
  if (String(body.canonical_transcript?.full_text || "").trim()) return true;
  return (body.events || []).some((e) => {
    const p = e.payload || {};
    return (e.event_type === "transcript_final" || e.event_type === "final" || e.event_type === "transcript_revision")
      && Boolean(p.normalized_text || p.text || e.normalized_text || e.text || "");
  });
}

function currentSessionHasTranscript() {
  return sessionHasTranscript({ events: currentEvents });
}

function currentSessionHasAudio() {
  return Boolean(currentAudioAsset && currentAudioAsset.saved);
}

function visibleTranscriptCount() {
  return document.querySelectorAll("#transcript-stream .utterance:not(.live-partial)").length;
}

function committedTranscriptCount() {
  const canonicalCount = canonicalTranscriptState.segments.length
    + (canonicalTranscriptState.activeTail ? 1 : 0);
  return canonicalCount || visibleTranscriptCount();
}

function currentReminderCount() {
  return projectedUnprocessedCandidateReminders().length;
}

function numericCountOverride(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function meetingCockpitStage() {
  if (currentMeetingPhase === "recording") return { label: "录音中", state: "recording" };
  if (currentMeetingPhase === "processing") return { label: "整理中", state: "processing" };
  if (currentMinutes) return { label: "已复盘", state: "reviewed" };
  if (currentSession && (currentSessionHasTranscript() || currentSessionHasAudio() || currentSuggestionCards.length || currentApproachCards.length)) {
    return { label: "已记录", state: "recorded" };
  }
  return { label: "待开始", state: "idle" };
}

function syncMeetingOverview({ transcriptCount = null, reminderCount = null } = {}) {
  const transcriptOverride = numericCountOverride(transcriptCount);
  const reminderOverride = numericCountOverride(reminderCount);
  const transcriptValue = transcriptOverride !== null ? transcriptOverride : visibleTranscriptCount();
  const reminderValue = reminderOverride !== null ? reminderOverride : currentReminderCount();
  const stage = meetingCockpitStage();
  if ($("c-cockpit-stage")) {
    $("c-cockpit-stage").textContent = stage.label;
    $("c-cockpit-stage").dataset.state = stage.state;
  }
  if ($("c-transcript")) $("c-transcript").textContent = String(transcriptValue);
  if ($("c-gap")) $("c-gap").textContent = String(reminderValue);
  if ($("c-cards")) $("c-cards").textContent = String(currentSuggestionCards.length);
  if ($("c-approach")) $("c-approach").textContent = String(currentApproachCards.length);
  if ($("c-audio")) $("c-audio").textContent = currentSessionHasAudio() ? "已保存" : "未保存";
  if ($("c-minutes")) $("c-minutes").textContent = currentMinutes ? "已生成" : "未生成";
  syncOverviewJumpButtons();
}

function overviewTargetState(target = "") {
  const transcriptCount = visibleTranscriptCount();
  const reminderCount = currentReminderCount();
  const states = {
    transcript: {
      selector: "#transcript-stream",
      focusSelector: "#transcript-stream .utterance, #transcript-stream",
      available: transcriptCount > 0,
      ariaLabel: "查看实时文字",
      readyMessage: `已跳到实时文字，当前 ${transcriptCount} 条。`,
      emptyMessage: "还没有文字记录，开始会议或导入录音后这里会实时追加。",
    },
    reminders: {
      selector: "#candidate-panel",
      focusSelector: "#candidate-panel",
      available: reminderCount > 0,
      ariaLabel: "查看实时提醒",
      readyMessage: `已跳到实时提醒，当前 ${reminderCount} 条。`,
      emptyMessage: "还没有实时提醒，会议中出现决定、待办、风险或问题后会自动出现。",
    },
    suggestions: {
      selector: "#suggestions-panel",
      focusSelector: "#suggestions-panel",
      available: currentSuggestionCards.length > 0,
      ariaLabel: "查看 AI 建议",
      readyMessage: `已跳到 AI 建议，当前 ${currentSuggestionCards.length} 条。`,
      emptyMessage: "还没有 AI 建议，有文字后可自动或手动生成带依据的建议。",
    },
    approach: {
      selector: "#approach-panel",
      focusSelector: "#approach-panel",
      available: currentApproachCards.length > 0,
      ariaLabel: "查看方案分析",
      readyMessage: `已跳到方案分析，当前 ${currentApproachCards.length} 条。`,
      emptyMessage: "还没有方案分析，讨论到方案取舍后可生成利弊与风险。",
    },
    audio: {
      selector: "#secondary-actions",
      focusSelector: currentSessionHasAudio() ? "#btn-export-audio" : "#sys-status",
      available: currentSessionHasAudio(),
      ariaLabel: "查看录音保存",
      readyMessage: "录音已保存，可在会议操作里导出录音。",
      emptyMessage: "录音还未保存，开始会议后结束录音会自动保存到本地会话。",
    },
    minutes: {
      selector: "#minutes-panel",
      focusSelector: "#minutes-panel",
      available: Boolean(currentMinutes),
      ariaLabel: "查看会后复盘",
      readyMessage: "已跳到会后复盘。",
      emptyMessage: "还没有会后复盘，会议结束或导入录音后可生成纪要。",
    },
  };
  return states[target] || {
    selector: "#sys-status",
    focusSelector: "#sys-status",
    available: false,
    ariaLabel: "查看会议状态",
    readyMessage: "已跳到当前状态。",
    emptyMessage: "这个会议状态还没有可展示的内容。",
  };
}

function syncOverviewJumpButtons() {
  document.querySelectorAll(".overview-jump[data-overview-target]").forEach((button) => {
    const state = overviewTargetState(button.dataset.overviewTarget || "");
    button.setAttribute("aria-label", state.ariaLabel);
    button.title = state.available ? state.readyMessage : state.emptyMessage;
    button.dataset.hasContent = state.available ? "true" : "false";
  });
}

function isNativeFocusableOverviewTarget(targetElement) {
  return Boolean(targetElement?.matches?.("a[href],button,input,select,textarea,summary,[tabindex]:not([tabindex='-1'])"));
}

function focusOverviewTargetElement(targetElement) {
  if (!targetElement) return false;
  targetElement.scrollIntoView({ behavior: "smooth", block: "start" });
  const needsTemporaryTabIndex = !isNativeFocusableOverviewTarget(targetElement) && !targetElement.hasAttribute("tabindex");
  if (needsTemporaryTabIndex) targetElement.setAttribute("tabindex", "-1");
  targetElement.focus({ preventScroll: true });
  return true;
}

function jumpToOverviewTarget(target = "") {
  if (["approach", "audio", "minutes"].includes(target)) {
    const reviewWorkspace = $("review-workspace");
    if (reviewWorkspace && !reviewWorkspace.hidden) reviewWorkspace.open = true;
  }
  const state = overviewTargetState(target);
  const targetElement = document.querySelector(state.focusSelector) || document.querySelector(state.selector);
  const focused = focusOverviewTargetElement(targetElement);
  if (!focused) {
    toast(state.emptyMessage);
    return;
  }
  toast(state.available ? state.readyMessage : state.emptyMessage);
}

function bindMeetingOverviewJumps() {
  document.querySelectorAll(".overview-jump[data-overview-target]").forEach((button) => {
    button.addEventListener("click", () => jumpToOverviewTarget(button.dataset.overviewTarget || ""));
  });
  syncOverviewJumpButtons();
}

function enabledLlmRequestBody({ maxCandidates = null } = {}) {
  if (isNoCostDerivationSelfTest()) return { mode: "deterministic_demo" };
  if (!Number.isFinite(Number(maxCandidates)) || Number(maxCandidates) <= 0) return { mode: "enabled" };
  return { mode: "enabled", max_candidates: Number(maxCandidates) };
}

function derivationBasePath(sessionId) {
  const sourceInfo = sessionSourceInfo(currentSessionSource || {});
  const useDemoDerivationBoundary = sourceInfo.kind === "demo" || isNoCostDerivationSelfTest();
  const prefix = useDemoDerivationBoundary ? "/live/asr/demo/sessions" : "/live/asr/sessions";
  return `${prefix}/${sessionId}`;
}

function sessionHistoryPath() {
  return shouldShowDemoTools() ? "/live/asr/sessions?include_demo=true" : "/live/asr/sessions";
}

function syncAutoSuggestionControl() {
  const button = $("btn-auto-suggestion-toggle");
  if (!button) return;
  const hasSession = Boolean(currentSession);
  const paused = Boolean(currentAutoSuggestionStatus?.paused);
  button.disabled = !hasSession || autoSuggestionInFlight;
  button.textContent = paused ? "恢复 AI 建议" : "暂停 AI 建议";
}

function waitForAutoSuggestionIdle() {
  return autoSuggestionIdlePromise.then(() => new Promise((resolve) => queueMicrotask(resolve)));
}

function beginAutoSuggestionFlight() {
  autoSuggestionIdlePromise = new Promise((resolve) => {
    autoSuggestionIdleResolve = resolve;
  });
}

function finishAutoSuggestionFlight() {
  const resolve = autoSuggestionIdleResolve;
  autoSuggestionIdleResolve = null;
  autoSuggestionIdlePromise = Promise.resolve();
  if (resolve) resolve();
}

function renderAutoSuggestionStatus(status) {
  currentAutoSuggestionStatus = status || null;
  invalidateCandidateReminderProjection("auto_suggestion_status_changed");
  const panel = $("auto-suggestion-status");
  if (!panel) return;
  if (!currentSession) {
    panel.textContent = "AI 建议会在会议开始后运行。";
    syncAutoSuggestionControl();
    return;
  }
  if (isSemanticQualityBlocked({ event_source: currentSessionSource || {} })) {
    panel.textContent = "低质量已抑制 · 已保留文字稿，正式建议暂停。";
    $("suggestions-panel").innerHTML = `<div class="empty">${ASR_SEMANTIC_QUALITY_MESSAGE} 文字稿已保留，暂不生成正式建议。</div>`;
    syncAutoSuggestionControl();
    return;
  }
  if (!status) {
    panel.textContent = "有已确认文字后，AI 建议会自动运行。";
    syncAutoSuggestionControl();
    return;
  }
  const state = status.status || "running";
  const labels = {
    running: "AI 建议运行中",
    paused: "已暂停",
    cooldown: "冷却中",
    low_quality_suppressed: "低质量已抑制",
    blocked: "AI 分析暂不可用",
    rate_limited: "已达到本小时上限",
  };
  const suppressed = status.suppressed || [];
  const latestReason = suppressed.length ? suppressed[suppressed.length - 1].reason : "";
  const reasonText = {
    low_confidence: "识别内容还不够稳定，先不生成正式建议。",
    cooldown: "刚刚已经生成过建议，稍后再继续。",
    paused: "你已暂停 AI 建议。",
    acceptance_blocked: "这段文字还不够完整，先保留文字稿。",
    rate_limited: "已达到频率上限，避免刷屏和额外花费。",
    duplicate: "这段内容已经分析过。",
    provider_error: "AI 服务请求失败，未生成新的正式卡片。",
  }[latestReason] || "";
  const processed = status.processed_candidate_count || 0;
  const calls = status.call_count_last_hour || 0;
  const activeMessage = status.message ? ` · ${status.message}` : "";
  panel.textContent = `${labels[state] || "AI 建议运行中"} · 已生成 ${processed} 条 · 本小时 ${calls} 次${activeMessage}${reasonText ? ` · ${reasonText}` : ""}`;
  if (state === "blocked") {
    renderSuggestionFailureState(status.message || "AI 建议暂时不可用，请稍后重试。");
  }
  syncAutoSuggestionControl();
  refreshCandidateReminderProjection();
}

function renderRealtimeCorrectionStatus(result = null) {
  const panel = $("realtime-correction-status");
  if (!panel) return;
  currentRealtimeCorrectionStatus = result || null;
  if (!currentSession) {
    panel.textContent = "完整发言确认后，AI 会检查文字并保留原始识别。";
    return;
  }
  if (!result) {
    panel.textContent = "有已确认文字后，AI 会自动检查并修正识别结果。";
    return;
  }

  const status = result.status && typeof result.status === "object" ? result.status : result;
  const state = String(status.status || "").trim();
  const gateReason = String(result.gate?.reason || status.gate?.reason || "").trim();
  const revisedIds = [
    ...(result.revised_segment_ids || []),
    ...(status.revised_segment_ids || []),
  ].map((id) => String(id || "").trim()).filter(Boolean);
  const noRevisionIds = [
    ...(result.no_revision_segment_ids || []),
    ...(status.no_revision_segment_ids || []),
  ].map((id) => String(id || "").trim()).filter(Boolean);
  revisedIds.forEach((id) => realtimeCorrectionRevisionIds.add(id));
  noRevisionIds.forEach((id) => realtimeCorrectionNoRevisionIds.add(id));
  const revisionCount = Number(
    result.revision_count
      ?? status.revision_count
      ?? status.revised_segment_ids?.length
      ?? 0,
  );
  realtimeCorrectionRevisionCount = Math.max(
    realtimeCorrectionRevisionCount,
    Number.isFinite(revisionCount) ? revisionCount : 0,
    realtimeCorrectionRevisionIds.size,
  );
  const noRevisionCount = Number(
    result.no_revision_segment_ids?.length
      ?? status.no_revision_segment_ids?.length
      ?? 0,
  );
  const accumulatedRevisionCount = Math.max(
    realtimeCorrectionRevisionCount,
    realtimeCorrectionRevisionIds.size,
  );
  const accumulatedNoRevisionCount = Math.max(
    realtimeCorrectionNoRevisionIds.size,
    Number.isFinite(noRevisionCount) ? noRevisionCount : 0,
  );
  const hasAppliedRevision = accumulatedRevisionCount > 0;

  if (state === "running" || gateReason === "in_flight" || result.pending) {
    panel.textContent = "AI 校正中 · 正在结合上下文检查已确认发言。";
  } else if (gateReason === "disabled_by_setting" || state === "disabled_by_setting") {
    panel.textContent = "AI 校正已关闭 · 当前仅保留原始识别文字。";
  } else if (["provider_failed", "provider_error"].includes(state) || gateReason === "provider_error") {
    panel.textContent = `校正失败 · ${String(status.message || result.message || "模型服务未返回有效结果")}`;
  } else if (["correction_rejected", "mapping_rejected", "rejected"].includes(state)) {
    panel.textContent = "校正未应用 · 模型已调用，但输出未通过安全校验，已保留原始识别。";
  } else if (state === "partially_completed") {
    panel.textContent = `部分文字已校正 · 已应用 ${accumulatedRevisionCount} 段，未通过校验的片段保留原文。`;
  } else if (state === "combined_rejected" && hasAppliedRevision) {
    panel.textContent = `AI 已校正 · 已应用 ${accumulatedRevisionCount} 段，本批未通过校验的片段保留原文。`;
  } else if (hasAppliedRevision || state === "completed" && result.called) {
    panel.textContent = `AI 已校正 · 已应用 ${accumulatedRevisionCount || 1} 段文字${accumulatedNoRevisionCount ? "，本批无需修改" : ""}。`;
  } else if (state === "no_revision_needed" || noRevisionCount > 0 || gateReason === "no_unrevised_final") {
    panel.textContent = "无需修改 · 模型检查完成，原始识别文字已保留。";
  } else if (gateReason === "batch_gate_closed") {
    panel.textContent = "等待下一批文字 · 当前发言已确认，AI 校正稍后继续。";
  } else if (status.message || result.message) {
    panel.textContent = `文字校正 · ${String(status.message || result.message)}`;
  } else {
    panel.textContent = "有已确认文字后，AI 会自动检查并修正识别结果。";
  }
}

async function loadAutoSuggestionStatus(sid) {
  if (!sid) return null;
  try {
    const body = await api(`/live/asr/sessions/${sid}/auto-suggestions/status`);
    if (currentSession !== sid) return null;
    renderAutoSuggestionStatus(body.status || null);
    return body.status || null;
  } catch (err) {
    if (currentSession === sid) {
      renderAutoSuggestionStatus({
        status: "blocked",
        message: "AI 建议状态加载失败，请稍后重试。",
        suppressed: [{ reason: "status_load_failed" }],
      });
    }
    return null;
  }
}

async function runAutoSuggestionsOnce({ reason = "live_final" } = {}) {
  const sid = currentSession;
  if (!sid) return { ok: false, skipped: true };
  if (autoSuggestionInFlight) {
    autoSuggestionPending = true;
    return { ok: false, pending: true };
  }
  if (currentAutoSuggestionStatus?.paused) return { ok: false, paused: true };
  autoSuggestionInFlight = true;
  beginAutoSuggestionFlight();
  renderAutoSuggestionStatus({
    ...(currentAutoSuggestionStatus || {}),
    status: "running",
    message: "正在分析这段已确认文字",
  });
  syncAutoSuggestionControl();
  try {
    const body = await api(`/live/asr/sessions/${sid}/auto-suggestions/run-once`, { method: "POST" });
    if (currentSession !== sid) return { ok: false, stale: true };
    renderAutoSuggestionStatus(body.status || null);
    if (body.reason === "acceptance_blocked") {
      currentSuggestionCards = [];
      currentApproachCards = [];
      currentMinutes = null;
      replaceCandidateReminderEvents([]);
      renderCandidateReminders();
      renderSuggestionCards([]);
      renderApproachCardList([]);
      renderMinutes("");
      $("s-llm").textContent = "已抑制";
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(ASR_SEMANTIC_QUALITY_MESSAGE)} 文字稿和录音已保留，正式建议暂不生成。</div>`;
      return { ok: false, blocked: true, reason: body.reason, count: 0 };
    }
    applyRealtimeTranscriptRevisions(body.transcript_revisions || []);
    const { cards, newCardIds } = mergeSuggestionCards(currentSuggestionCards, body.suggestion_cards || []);
    currentSuggestionCards = cards;
    renderSuggestionCards(currentSuggestionCards, { highlightCardIds: newCardIds });
    if ((body.generated_card_count || 0) > 0) {
      $("s-llm").textContent = "自动建议";
      await loadSessionHistory();
    }
    return { ok: true, reason, count: body.generated_card_count || 0 };
  } catch (err) {
    if (currentSession === sid) {
      const message = operationErrorMessage(err, "AI 建议请求失败");
      $("s-llm").textContent = "暂不可用";
      renderAutoSuggestionStatus({
        ...(currentAutoSuggestionStatus || {}),
        status: "blocked",
        message,
        suppressed: [{ reason: "provider_error", message }],
      });
      renderSuggestionFailureState(`AI 建议请求失败：${message}`);
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(`AI 建议请求失败：${message}`)}</div>`;
    }
    return { ok: false, error: operationErrorMessage(err, "AI 建议请求失败") };
  } finally {
    autoSuggestionInFlight = false;
    finishAutoSuggestionFlight();
    syncAutoSuggestionControl();
    if (autoSuggestionPending && currentSession === sid && !currentAutoSuggestionStatus?.paused) {
      autoSuggestionPending = false;
      queueMicrotask(() => runAutoSuggestionsOnce({ reason: "pending_trigger" }));
    } else {
      autoSuggestionPending = false;
    }
  }
}

function applyRealtimeTranscriptRevisions(revisions = []) {
  let appliedCount = 0;
  revisions.forEach((revision) => {
    if (!revision || revision.event_type !== "transcript_revision") return;
    const eventId = String(revision.id || "");
    const existingIndex = eventId
      ? currentEvents.findIndex((event) => String(event.id || "") === eventId)
      : -1;
    if (existingIndex >= 0) currentEvents[existingIndex] = revision;
    else currentEvents.push(revision);
    const renderResult = appendLiveEvent(revision);
    if (renderResult.visibleTextChanged) appliedCount++;
  });
  return appliedCount;
}

async function runRealtimeCorrectionsOnce({ force = false, sessionId = null, requestTimeoutMs = 0 } = {}) {
  const sid = sessionId || currentSession;
  if (!sid) return { ok: false, skipped: true };
  if (realtimeCorrectionInFlight) {
    if (realtimeCorrectionOwnerSessionId !== sid) {
      return { ok: false, stale: true, reason: "different_session_in_flight" };
    }
    realtimeCorrectionPending = true;
    realtimeCorrectionPendingForce = realtimeCorrectionPendingForce || force;
    if (currentSession === sid) renderRealtimeCorrectionStatus({ status: { status: "running" }, pending: true });
    return { ok: false, pending: true };
  }
  const generation = realtimeCorrectionGeneration;
  realtimeCorrectionInFlight = true;
  realtimeCorrectionOwnerSessionId = sid;
  if (currentSession === sid) {
    renderRealtimeCorrectionStatus({
      status: { status: "running", message: "正在结合上下文检查这段已确认文字" },
    });
  }
  try {
    const body = await api(`/live/asr/sessions/${sid}/realtime-corrections/run-once`, {
      method: "POST",
      body: JSON.stringify({ force: Boolean(force) }),
      timeoutMs: requestTimeoutMs,
    });
    if (currentSession !== sid || generation !== realtimeCorrectionGeneration) return { ok: false, stale: true };
    renderRealtimeCorrectionStatus(body);
    const appliedCount = applyRealtimeTranscriptRevisions(body.transcript_revisions || []);
    if (body.gate?.reason === "in_flight" && !force) {
      realtimeCorrectionPending = true;
      realtimeCorrectionPendingForce = realtimeCorrectionPendingForce || force;
    } else if (!force && body.gate?.reason === "batch_gate_closed") {
      scheduleRealtimeCorrectionRetry(body.gate.retry_after_ms, sid);
    } else if (!force && body.called && currentMeetingPhase === "recording") {
      realtimeCorrectionPending = true;
    } else if (body.gate?.reason === "no_unrevised_final") {
      clearRealtimeCorrectionRetryTimer();
    }
    return {
      ok: true,
      called: Boolean(body.called),
      count: appliedCount,
      gate: body.gate || null,
    };
  } catch (err) {
    const message = operationErrorMessage(err, "实时文字校正失败");
    if (currentSession === sid) {
      $("s-llm").textContent = "校正失败";
      renderRealtimeCorrectionStatus({
        status: { status: "provider_failed", message },
        gate: { reason: "provider_error" },
      });
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(`实时文字校正失败：${message}`)}</div>`;
    }
    return { ok: false, error: message };
  } finally {
    if (generation === realtimeCorrectionGeneration && realtimeCorrectionOwnerSessionId === sid) {
      realtimeCorrectionInFlight = false;
      realtimeCorrectionOwnerSessionId = null;
      if (realtimeCorrectionPending && currentSession === sid) {
        const pendingForce = realtimeCorrectionPendingForce;
        realtimeCorrectionPending = false;
        realtimeCorrectionPendingForce = false;
        window.setTimeout(
          () => runRealtimeCorrectionsOnce({ force: pendingForce, sessionId: sid }),
          250,
        );
      }
    }
  }
}

async function toggleAutoSuggestion() {
  if (!currentSession) return toast("请先开始会议或导入录音");
  const sid = currentSession;
  const paused = !Boolean(currentAutoSuggestionStatus?.paused);
  try {
    const body = await api(`/live/asr/sessions/${sid}/auto-suggestions/status`, {
      method: "PATCH",
      body: JSON.stringify({ paused }),
    });
    if (currentSession !== sid) return;
    renderAutoSuggestionStatus(body.status || null);
    toast(paused ? "AI 建议已暂停" : "AI 建议已恢复");
  } catch (err) {
    toast("自动建议状态更新失败: " + err.message);
  }
}

function sessionDegradationText(body = {}) {
  const reasons = [...new Set([
    ...(body.degradation_reasons || []),
    ...(body.event_source?.degradation_reasons || []),
  ])];
  const blockers = [...new Set([
    ...(body.acceptance_blockers || []),
    ...(body.event_source?.acceptance_blockers || []),
  ])];
  if (isSemanticQualityBlocked(body)) {
    return ASR_SEMANTIC_QUALITY_MESSAGE;
  }
  if (reasons.includes("asr_final_empty") || reasons.includes("asr_no_final")) {
    return "未识别到有效语音。请检查麦克风输入、系统输入设备或音频文件。";
  }
  if (reasons.length) {
    return `识别结果不可用：${reasons.join(", ")}`;
  }
  return "未识别到有效语音。请检查麦克风或音频文件。";
}

function derivationBlockedMessage(body = {}) {
  const blockers = body.acceptance_blockers || [];
  const reasons = body.degradation_reasons || [];
  if (body.derivation_blocked && (blockers.includes("asr_semantic_quality_blocked") || reasons.includes("asr_semantic_quality_blocked"))) {
    return body.message || ASR_SEMANTIC_QUALITY_MESSAGE;
  }
  return body.message || "当前会议文字还不够完整，先不生成正式建议。";
}

async function loadAudioCheck() {
  try {
    const body = await api("/audio/check");
    const providerHealth = await api("/providers/health").catch(() => null);
    currentReadiness = body;
    const micText = body.mic_available ? "麦克风可用" : "麦克风暂不可用";
    const realtimeReady = Boolean(body.realtime_asr_available);
    const fileReady = Boolean(body.file_asr_available ?? body.funasr_available);
    const asrText = realtimeReady
      ? "实时识别可用"
      : (fileReady ? "实时识别暂不可用，可导入录音" : "语音识别暂不可用");
    const llmReady = Boolean(providerHealth?.llm?.configured || body.llm_configured);
    currentLlmReady = llmReady;
    const llmText = llmReady ? "AI 分析可用" : "AI 分析暂不可用";
    $("sys-status").innerHTML = `<div class="empty">${escapeHtml(micText)}<br>${escapeHtml(asrText)}<br>${escapeHtml(llmText)}</div>`;
    $("s-asr").textContent = realtimeReady ? "实时可用" : (fileReady ? "仅导入" : "不可用");
    $("s-llm").textContent = llmReady ? "可用" : "不可用";
    if (!llmReady) {
      renderSuggestionFailureState("AI 建议不可用：当前未连接模型服务。会议文字和录音仍可正常保存。");
    }
    updateRecordButtonReadiness(currentMeetingPhase);
  } catch {
    $("sys-status").innerHTML = `<div class="empty">启动检查暂时失败。仍可尝试导入录音或稍后重试。</div>`;
  }
}

function clearStreamEmptyState() {
  const stream = $("transcript-stream");
  if (stream.childElementCount === 1 && stream.firstElementChild?.classList.contains("empty")) {
    stream.innerHTML = "";
  }
}

function candidateReminderMetaLabel(event = {}) {
  return event.event_type === "partial_hint_event" ? "来自实时文字" : "来自会议原话";
}

function candidateReminderText(event = {}) {
  const payload = event.payload || {};
  return String(payload.suggested_prompt || payload.trigger_reason || payload.message || "").trim();
}

function candidateReminderFocusType(event = {}) {
  const payload = event.payload || {};
  if (payload.target_type) return String(payload.target_type);
  if (payload.state_type) return String(payload.state_type);
  const hint = String(`${payload.hint_type || ""} ${payload.gap_rule_id || ""} ${candidateReminderText(event)}`).toLowerCase();
  if (!hint.trim()) return "";
  if (hint.includes("risk") || hint.includes("rollback") || hint.includes("风险") || hint.includes("回滚")) return "Risk";
  if (hint.includes("action") || hint.includes("owner") || hint.includes("todo") || hint.includes("负责") || hint.includes("待办")) return "ActionItem";
  if (hint.includes("question") || hint.includes("open") || hint.includes("谁") || hint.includes("是否") || hint.includes("待确认")) return "OpenQuestion";
  if (hint.includes("decision") || hint.includes("决定") || hint.includes("决策")) return "DecisionCandidate";
  return "";
}

function filteredCandidateReminders(candidates = []) {
  if (!currentCandidateFocusType) return candidates;
  return candidates.filter((event) => candidateReminderFocusType(event) === currentCandidateFocusType);
}

function syncCandidateFocusButtons() {
  document.querySelectorAll(".focus-filter[data-focus-type]").forEach((button) => {
    const active = button.dataset.focusType === currentCandidateFocusType;
    const count = focusTypeCountFromDom(button.dataset.focusType || "");
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.disabled = !active && count <= 0;
    button.title = button.disabled ? "暂无这类提醒" : "点击筛选这类提醒";
  });
}

function focusTypeCountFromDom(focusType = "") {
  const ids = {
    DecisionCandidate: "c-decision",
    ActionItem: "c-action",
    Risk: "c-risk",
    OpenQuestion: "c-question",
  };
  const value = Number($(ids[focusType])?.textContent || 0);
  return Number.isFinite(value) ? value : 0;
}

function rerenderCandidateFocus() {
  renderCandidateReminders();
}

function refreshCandidateReminderProjection() {
  syncCandidateFocusCounts();
  renderCandidateReminders();
}

function setCandidateFocusFilter(focusType) {
  const active = currentCandidateFocusType === focusType;
  if (!active && focusTypeCountFromDom(focusType) <= 0) return;
  currentCandidateFocusType = currentCandidateFocusType === focusType ? "" : focusType;
  invalidateCandidateReminderProjection("focus_changed");
  syncCandidateFocusButtons();
  rerenderCandidateFocus();
}

function clearCandidateFocusFilter() {
  currentCandidateFocusType = "";
  invalidateCandidateReminderProjection("focus_cleared");
  syncCandidateFocusButtons();
  rerenderCandidateFocus();
}

function bindCandidateFocusFilters() {
  document.querySelectorAll(".focus-filter[data-focus-type]").forEach((button) => {
    button.addEventListener("click", () => setCandidateFocusFilter(button.dataset.focusType || ""));
  });
  syncCandidateFocusButtons();
}

function unprocessedCandidateReminders(events = []) {
  const processedCandidateIds = new Set(currentAutoSuggestionStatus?.processed_candidate_ids || []);
  executedSuggestionCandidateIds.forEach((candidateId) => processedCandidateIds.add(candidateId));
  return (events || []).filter((event) => {
    if (event.event_type !== "suggestion_candidate_event") return true;
    const payload = event.payload || {};
    const candidateId = String(payload.candidate_id || event.candidate_id || "");
    return !candidateId || !processedCandidateIds.has(candidateId);
  });
}

function invalidateCandidateReminderProjection(reason = "changed") {
  candidateReminderProjectionVersion++;
  candidateReminderProjectionCache = null;
  document.body.dataset.reminderProjectionInvalidation = reason;
}

function isCandidateReminderEvent(event = {}) {
  return event.event_type === "suggestion_candidate_event" || event.event_type === "partial_hint_event";
}

function replaceCandidateReminderEvents(events = []) {
  candidateReminderEvents = (events || []).filter(isCandidateReminderEvent);
  invalidateCandidateReminderProjection("candidate_events_replaced");
}

function appendCandidateReminderEvent(event = {}) {
  if (!isCandidateReminderEvent(event)) return false;
  candidateReminderEvents.push(event);
  invalidateCandidateReminderProjection("candidate_event_appended");
  return true;
}

function candidateFocusCounts(events = []) {
  const counts = { DecisionCandidate: 0, ActionItem: 0, Risk: 0, OpenQuestion: 0 };
  (events || []).forEach((event) => {
    const focusType = candidateReminderFocusType(event);
    if (counts[focusType] !== undefined) counts[focusType]++;
  });
  return counts;
}

function syncCandidateFocusCounts(events = null) {
  const candidates = projectedUnprocessedCandidateReminders(events);
  const counts = candidateFocusCounts(candidates);
  $("c-decision").textContent = counts.DecisionCandidate;
  $("c-action").textContent = counts.ActionItem;
  $("c-risk").textContent = counts.Risk;
  $("c-question").textContent = counts.OpenQuestion;
  $("c-gap").textContent = String(candidates.length);
  $("s-candidates").textContent = String(candidates.length);
  syncCandidateFocusButtons(counts);
}

function candidateReminderSemanticKey(event = {}) {
  const payload = event.payload || {};
  const segmentBatch = Array.isArray(payload.segment_batch) ? payload.segment_batch : [];
  const rawSegment = String(segmentBatch[0] || "").trim();
  const segment = rawSegment.replace(/_final\d*$/, "");
  const rule = String(payload.gap_rule_id || payload.hint_type || payload.target_type || "").trim();
  const text = candidateReminderText(event);
  if (!segment && !rule && !text) return "";
  return [segment, rule, text].join("|");
}

function candidateReminderKey(event = {}) {
  const payload = event.payload || {};
  const dedupeKey = payload.dedupe_key || event.dedupe_key;
  if (dedupeKey) return `dedupe|${dedupeKey}`;
  const semanticKey = candidateReminderSemanticKey(event);
  if (semanticKey) return `semantic|${semanticKey}`;
  return [
    payload.gap_rule_id || payload.hint_type || payload.target_type || "",
    candidateReminderText(event),
  ].join("|");
}

function dedupedCandidateReminderProjection(events = []) {
  const projectedByKey = new Map();
  (events || [])
    .filter((event) => event.event_type === "suggestion_candidate_event" || event.event_type === "partial_hint_event")
    .forEach((event, index) => {
      const key = candidateReminderKey(event);
      const existing = projectedByKey.get(key);
      projectedByKey.set(key, {
        event,
        firstIndex: existing ? existing.firstIndex : index,
      });
    });
  return Array.from(projectedByKey.values())
    .sort((left, right) => left.firstIndex - right.firstIndex)
    .map((item) => item.event);
}

function projectedUnprocessedCandidateReminders(events = null) {
  if (Array.isArray(events)) {
    return unprocessedCandidateReminders(dedupedCandidateReminderProjection(events));
  }
  if (candidateReminderProjectionCache?.version === candidateReminderProjectionVersion) {
    return candidateReminderProjectionCache.events;
  }
  const projected = unprocessedCandidateReminders(dedupedCandidateReminderProjection(candidateReminderEvents));
  candidateReminderProjectionCache = {
    version: candidateReminderProjectionVersion,
    events: projected,
  };
  return projected;
}

function visibleCandidateReminders(candidates = []) {
  const reminderByKey = new Map();
  candidates.forEach((event, index) => {
    const key = candidateReminderKey(event);
    const existing = reminderByKey.get(key);
    reminderByKey.set(key, {
      event,
      firstIndex: existing ? existing.firstIndex : index,
    });
  });
  return Array.from(reminderByKey.values())
    .sort((left, right) => {
      const leftPriority = CANDIDATE_REMINDER_PRIORITY[candidateReminderFocusType(left.event)] ?? Number.MAX_SAFE_INTEGER;
      const rightPriority = CANDIDATE_REMINDER_PRIORITY[candidateReminderFocusType(right.event)] ?? Number.MAX_SAFE_INTEGER;
      return leftPriority - rightPriority || left.firstIndex - right.firstIndex;
    })
    .slice(0, MAX_CANDIDATE_REMINDERS_VISIBLE)
    .map((item) => item.event);
}

function renderCandidateReminders(events = null) {
  const panel = $("candidate-panel");
  panel.innerHTML = "";
  const candidates = projectedUnprocessedCandidateReminders(events);
  if (!candidates.length) {
    panel.innerHTML = `<div class="empty">${CANDIDATE_EMPTY_MESSAGE}</div>`;
    syncMeetingOverview({ reminderCount: 0 });
    return;
  }
  const filteredCandidates = filteredCandidateReminders(candidates);
  if (currentCandidateFocusType) {
    const header = document.createElement("div");
    header.className = "empty";
    header.innerHTML = `正在查看：${escapeHtml(CANDIDATE_FOCUS_LABELS[currentCandidateFocusType] || currentCandidateFocusType)}
      <button type="button" class="btn" id="btn-clear-candidate-focus" style="margin-left:8px;min-height:28px;height:28px;padding:0 10px">显示全部</button>`;
    panel.appendChild(header);
    header.querySelector("#btn-clear-candidate-focus").addEventListener("click", () => clearCandidateFocusFilter());
  }
  if (!filteredCandidates.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = `当前没有${CANDIDATE_FOCUS_LABELS[currentCandidateFocusType] || "这类"}提醒。`;
    panel.appendChild(empty);
    syncMeetingOverview({ reminderCount: candidates.length });
    return;
  }
  const visibleCandidates = visibleCandidateReminders(filteredCandidates);
  const hiddenCount = Math.max(filteredCandidates.length - visibleCandidates.length, 0);
  if (hiddenCount > 0) {
    const folded = document.createElement("div");
    folded.className = "empty";
    folded.textContent = `${hiddenCount} 条较早或重复提醒已收起`;
    panel.appendChild(folded);
  }
  visibleCandidates.forEach((e) => {
    const p = e.payload || {};
    const isPartialHint = e.event_type === "partial_hint_event";
    const div = document.createElement("div");
    div.className = "suggestion";
    div.setAttribute("role", "article");
    div.setAttribute("aria-label", isPartialHint ? "来自实时文字的提醒" : "来自会议原话的提醒");
    div.dataset.cardKind = isPartialHint ? "partial-hint" : "candidate";
    div.dataset.candidateType = candidateReminderFocusType(e) || (isPartialHint ? "PartialHint" : "Candidate");
    div.innerHTML = `<div class="sug-head gap">实时提醒</div>
      <div class="sug-body">${escapeHtml(candidateReminderText(e))}</div>
      <div class="sug-meta">${escapeHtml(candidateReminderMetaLabel(e))}</div>`;
    panel.appendChild(div);
  });
  syncMeetingOverview({ reminderCount: candidates.length });
}

function renderTranscriptAndCandidates(events, options = {}) {
  const preserveExistingTranscript = options.preserveExistingTranscript === true;
  const existingTranscriptCount = canonicalTranscriptState.segments.length;
  const existingLivePartial = canonicalTranscriptState.activeTail;
  const counts = { DecisionCandidate: 0, ActionItem: 0, Risk: 0, OpenQuestion: 0 };
  const candidateEvents = [];
  let gapCount = 0;
  events.forEach((e) => {
    const p = e.payload || {};
    if (e.event_type === "suggestion_candidate_event") {
      gapCount++;
      candidateEvents.push(e);
      const t = p.target_type;
      if (counts[t] !== undefined) counts[t]++;
    } else if (e.event_type === "partial_hint_event") {
      gapCount++;
      candidateEvents.push(e);
    }
  });
  if (options.canonicalSnapshot || !preserveExistingTranscript || (!existingTranscriptCount && !existingLivePartial)) {
    replaceCanonicalTranscriptSnapshot(options.canonicalSnapshot || null, events);
  } else {
    renderCanonicalTranscriptView({ contentChanged: false });
  }
  replaceCandidateReminderEvents(candidateEvents);
  const activeCandidateEvents = projectedUnprocessedCandidateReminders();
  gapCount = activeCandidateEvents.length;
  syncCandidateFocusCounts();
  renderCandidateReminders();
  const visibleItemCount = canonicalTranscriptState.segments.length + (canonicalTranscriptState.activeTail ? 1 : 0);
  if (visibleItemCount) {
    syncMeetingOverview({ transcriptCount: canonicalTranscriptState.segments.length, reminderCount: gapCount });
  } else if (preserveExistingTranscript && (existingTranscriptCount > 0 || existingLivePartial)) {
    const keepMessage = existingLivePartial && existingTranscriptCount === 0
      ? "尚未收到完整整理结果，已保留临时实时文字。"
      : "尚未收到完整整理结果，已保留实时文字。";
    $("sys-status").innerHTML = `<div class="empty">${keepMessage}</div>`;
    syncMeetingOverview({ reminderCount: gapCount });
  } else {
    renderCanonicalTranscriptEmptyState("本次没有识别到有效语音，请检查麦克风或音频文件。");
    syncMeetingOverview({ transcriptCount: 0, reminderCount: gapCount });
  }
}

function applySessionEvents(sid, body, message = "实时文字已刷新。", options = {}) {
  const qualityBlocked = isSemanticQualityBlocked(body);
  currentEvents = body.events || [];
  currentSuggestionCards = qualityBlocked
    ? []
    : uniqueSuggestionCardsNewestFirst(body.suggestion_cards || []);
  currentApproachCards = qualityBlocked ? [] : (body.approach_cards || []);
  currentMinutes = qualityBlocked ? "" : (body.minutes?.minutes_md || "");
  currentAudioAsset = body.audio || null;
  currentSessionSource = body.event_source || { provider: body.provider, provider_mode: "real" };
  currentAutoSuggestionStatus = body.auto_suggestion || null;
  const sourceInfo = sessionSourceInfo(currentSessionSource, body);
  const sourceLabel = sourceInfo.shortLabel;
  renderSourceBadge(currentSessionSource, body);
  renderTranscriptAndCandidates(currentEvents, {
    preserveExistingTranscript: true,
    canonicalSnapshot: body.canonical_transcript || null,
  });
  renderSuggestionCards(currentSuggestionCards);
  renderApproachCardList(currentApproachCards);
  renderMinutes(currentMinutes);
  resetRealtimeCorrectionState();
  renderRealtimeCorrectionStatus(body.realtime_transcript_correction || null);
  if (qualityBlocked) {
    replaceCandidateReminderEvents([]);
    syncCandidateFocusCounts();
    renderCandidateReminders();
  }
  const transcriptCount = committedTranscriptCount();
  $("session-meta").textContent = `${sourceInfo.label} · ${sid} · ${transcriptCount} 段文字${sourceInfo.warning ? ` · ${sourceInfo.warning}` : ""}`;
  $("s-asr").textContent = sourceLabel;
  $("sys-status").innerHTML = `<div class="empty">${escapeHtml(qualityBlocked ? `${ASR_SEMANTIC_QUALITY_MESSAGE} 文字稿和录音已保留，正式建议暂不生成。` : message)}</div>`;
  loadAutoSuggestionStatus(sid);
}

function normalizedSuggestionSemanticValue(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLocaleLowerCase();
}

function suggestionEvidenceTargetKey(card = {}) {
  const target = [card.target_type, card.target_id, card.gap_rule_id]
    .map(normalizedSuggestionSemanticValue)
    .filter(Boolean);
  const evidenceIds = (card.evidence_span_ids || [])
    .map(normalizedSuggestionSemanticValue)
    .filter(Boolean)
    .sort();
  const evidenceSpans = (card.evidence_spans || [])
    .map((span) => [span.id, span.segment_id, span.quote]
      .map(normalizedSuggestionSemanticValue)
      .filter(Boolean)
      .join("|"))
    .filter(Boolean)
    .sort();
  if (evidenceSpans.length) return JSON.stringify({ evidenceSpans });
  if (evidenceIds.length) return JSON.stringify({ evidenceIds });
  return JSON.stringify({ target });
}

function suggestionSemanticKey(card = {}, index = 0) {
  const suggestionText = normalizedSuggestionSemanticValue(card.suggestion_text);
  const evidenceTarget = suggestionEvidenceTargetKey(card);
  if (suggestionText) return `${suggestionText}|${evidenceTarget}`;
  return `empty|${card.card_id || index}|${evidenceTarget}`;
}

function suggestionCandidateKey(card = {}, index = 0) {
  const candidate = [card.target_type, card.target_id, card.gap_rule_id]
    .map(normalizedSuggestionSemanticValue)
    .filter(Boolean);
  return candidate.length
    ? `candidate|${candidate.join("|")}`
    : `semantic|${suggestionSemanticKey(card, index)}`;
}

function uniqueSuggestionCardsNewestFirst(cards = [], inputNewestFirst = false) {
  const ordered = inputNewestFirst ? cards : [...cards].reverse();
  const seenSemanticKeys = new Set();
  return ordered.filter((card, index) => {
    const semanticKey = suggestionSemanticKey(card, index);
    if (seenSemanticKeys.has(semanticKey)) return false;
    seenSemanticKeys.add(semanticKey);
    return true;
  });
}

function mergeSuggestionCards(existing = [], incoming = []) {
  const existingCards = uniqueSuggestionCardsNewestFirst(existing, true);
  const incomingCards = uniqueSuggestionCardsNewestFirst(incoming);
  const existingSemanticKeys = new Set(existingCards.map((card, index) => suggestionSemanticKey(card, index)));
  const newCardIds = new Set(incomingCards
    .filter((card, index) => !existingSemanticKeys.has(suggestionSemanticKey(card, index)))
    .map((card) => card.card_id)
    .filter(Boolean));
  const incomingSemanticKeys = new Set(incomingCards.map((card, index) => suggestionSemanticKey(card, index)));
  const incomingCandidateKeys = new Set(incomingCards.map((card, index) => suggestionCandidateKey(card, index)));
  return {
    cards: [...incomingCards, ...existingCards.filter((card, index) => (
      !incomingSemanticKeys.has(suggestionSemanticKey(card, index))
      && !incomingCandidateKeys.has(suggestionCandidateKey(card, index))
    ))],
    newCardIds,
  };
}

function renderRealCards(runs) {
  const cards = [];
  let processedCandidateChanged = false;
  runs.forEach((run) => {
    if (run.run_status !== "completed" || !run.card) return;
    if (run.target_candidate_id) {
      const previousSize = executedSuggestionCandidateIds.size;
      executedSuggestionCandidateIds.add(String(run.target_candidate_id));
      processedCandidateChanged = processedCandidateChanged || executedSuggestionCandidateIds.size !== previousSize;
    }
    cards.push(run.card);
  });
  if (processedCandidateChanged) invalidateCandidateReminderProjection("candidate_processed");
  const { cards: mergedCards, newCardIds } = mergeSuggestionCards(currentSuggestionCards, cards);
  currentSuggestionCards = mergedCards;
  renderSuggestionCards(currentSuggestionCards, { highlightCardIds: newCardIds });
  refreshCandidateReminderProjection();
  $("s-llm").textContent = `${runs.length} 调用`;
  return {
    visibleCount: currentSuggestionCards.length,
    newCount: newCardIds.size,
  };
}

function renderApproachCards(cards) {
  currentApproachCards = mergeById(currentApproachCards, cards || [], "card_id");
  renderApproachCardList(currentApproachCards);
}

function mergeById(existing, incoming, key) {
  const map = new Map();
  existing.forEach((item, index) => map.set(item[key] || `existing_${index}`, item));
  incoming.forEach((item, index) => map.set(item[key] || `incoming_${index}`, item));
  return Array.from(map.values());
}

function renderSuggestionFailureState(message = "AI 建议暂时生成失败，请稍后重试。") {
  const panel = $("suggestions-panel");
  if (!panel) return;
  let failure = panel.querySelector(".suggestion-error");
  if (!failure) {
    failure = document.createElement("div");
    failure.className = "empty suggestion-error";
    failure.setAttribute("role", "status");
    failure.setAttribute("aria-live", "polite");
    panel.prepend(failure);
  }
  failure.textContent = message;
}

function buildSuggestionCardElement(card, highlightCardIds = new Set()) {
  const div = document.createElement("div");
  div.className = "suggestion";
  div.setAttribute("role", "article");
  div.setAttribute("aria-label", "AI 建议");
  if (card.card_id && highlightCardIds.has(card.card_id)) {
    div.classList.add("suggestion-new");
    div.setAttribute("aria-label", "本次新到的 AI 建议");
  }
  div.dataset.cardKind = "suggestion";
  div.style.borderLeftColor = "var(--info)";
  div.innerHTML = `<div class="sug-head" style="color:var(--info)">AI 建议</div>
    <div class="sug-body">${escapeHtml(card.suggestion_text || "")}</div>
    <div class="sug-meta">触发原因：${escapeHtml(card.trigger_reason || "会议上下文")} · 置信度：${escapeHtml(formatConfidence(card.confidence))}</div>
    <div class="evidence">${renderEvidenceSpans(card)}</div>`;
  div.querySelectorAll(".evidence-link").forEach((button) => {
    button.addEventListener("click", () => {
      focusEvidenceSpan(button.dataset.segmentId || "", button.dataset.evidenceId || "");
    });
  });
  return div;
}

function renderSuggestionCards(cards, { highlightCardIds = new Set() } = {}) {
  const panel = $("suggestions-panel");
  panel.innerHTML = "";
  const uniqueCards = uniqueSuggestionCardsNewestFirst(cards, true);
  if (!uniqueCards.length) {
    panel.innerHTML = `<div class="empty">${SUGGESTIONS_EMPTY_MESSAGE}</div>`;
    $("s-cards").textContent = "0";
    syncMeetingOverview();
    return;
  }
  uniqueCards.slice(0, MAX_FORMAL_SUGGESTIONS_VISIBLE)
    .forEach((card) => panel.appendChild(buildSuggestionCardElement(card, highlightCardIds)));
  const foldedCards = uniqueCards.slice(MAX_FORMAL_SUGGESTIONS_VISIBLE);
  if (foldedCards.length) {
    const folded = document.createElement("details");
    folded.className = "suggestion-fold";
    const summary = document.createElement("summary");
    summary.textContent = `查看其余 ${foldedCards.length} 条建议`;
    folded.appendChild(summary);
    const content = document.createElement("div");
    content.className = "suggestion-fold-content";
    foldedCards.forEach((card) => content.appendChild(buildSuggestionCardElement(card, highlightCardIds)));
    folded.appendChild(content);
    panel.appendChild(folded);
  }
  $("s-cards").textContent = String(uniqueCards.length);
  syncMeetingOverview();
}

function formatConfidence(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "未标注";
  return `${Math.round(numeric * 100)}%`;
}

function renderEvidenceSpans(card) {
  const spans = card.evidence_spans || [];
  if (!spans.length) {
    const ids = (card.evidence_span_ids || []).join(", ");
    return `<span class="quote">依据：${escapeHtml(ids || "会议原话")}</span>`;
  }
  return spans.map((span) => {
    const start = fmtMs(span.start_ms || 0);
    const end = fmtMs(span.end_ms || 0);
    const quote = span.quote || "会议原话";
    return `<button type="button" class="evidence-link" data-segment-id="${escapeHtml(span.segment_id || "")}" data-evidence-id="${escapeHtml(span.id || "")}">
      <span class="evidence-time">${escapeHtml(`${start}-${end}`)}</span>${escapeHtml(quote)}
    </button>`;
  }).join("");
}

function focusEvidenceSpan(segmentId, evidenceId) {
  const utterances = Array.from(document.querySelectorAll(".transcript-segment, .utterance"));
  const target = utterances.find((item) => {
    const evidenceIds = (item.dataset.evidenceIds || "").split(/[,\s]+/).filter(Boolean);
    return (segmentId && (
      item.dataset.segmentId === segmentId
      || item.dataset.sourceSegmentId === segmentId
    ))
      || (evidenceId && evidenceIds.includes(evidenceId));
  });
  if (!target) {
    toast("未找到对应原话");
    return;
  }
  pauseTranscriptFollowing();
  target.scrollIntoView({ behavior: "smooth", block: "center" });
  target.classList.add("evidence-focus");
  const originalDetails = target.querySelector("details.original-asr-text")
    || target.parentElement?.querySelector("details.original-asr-text");
  const evidenceIds = (target.dataset.evidenceIds || "").split(/[\s,]+/).filter(Boolean);
  const targetMatchesEvidence = Boolean(
    (segmentId && (target.dataset.segmentId === segmentId || target.dataset.sourceSegmentId === segmentId))
    || (evidenceId && evidenceIds.includes(evidenceId))
  );
  if (
    originalDetails
    && targetMatchesEvidence
    && target.dataset.status === "corrected"
    && target.dataset.sourceSegmentId
    && target.dataset.sourceSegmentId !== target.dataset.segmentId
  ) {
    originalDetails.open = true;
  }
  window.setTimeout(() => target.classList.remove("evidence-focus"), 2200);
}

function renderApproachCardList(cards) {
  const panel = $("approach-panel");
  panel.innerHTML = "";
  if (!cards.length) {
    panel.innerHTML = `<div class="empty">${APPROACH_EMPTY_MESSAGE}</div>`;
    $("c-approach").textContent = "0";
    $("s-approach-cards").textContent = "0";
    syncMeetingOverview();
    return;
  }
  cards.forEach((card) => {
    const div = document.createElement("div");
    div.className = "suggestion approach";
    div.dataset.cardKind = "approach";
    div.innerHTML = `<div class="sug-head approach">方案分析</div>
      <div class="sug-body">${escapeHtml(card.suggestion_text || "")}</div>
      <div class="evidence"><span class="quote">依据：${escapeHtml(card.evidence_quote || "会议原话")}</span></div>`;
    panel.appendChild(div);
  });
  $("c-approach").textContent = cards.length;
  $("s-approach-cards").textContent = String(cards.length);
  syncMeetingOverview();
}

function renderMinutes(markdown) {
  currentMinutes = markdown || "";
  if (!currentMinutes) {
    $("minutes-panel").innerHTML = `<div class="empty">暂时没有生成可用复盘。</div>`;
    syncMeetingOverview();
    syncActionAvailability();
    return;
  }
  $("minutes-panel").innerHTML = `<pre style="white-space:pre-wrap;color:var(--fg-primary);font-size:12px;line-height:1.55;max-height:280px;overflow:auto">${escapeHtml(currentMinutes)}</pre>`;
  syncMeetingOverview();
  syncActionAvailability();
}

function showHistoryWorkspace() {
  const workspace = $("review-workspace");
  if (!workspace) return;
  workspace.hidden = false;
  workspace.open = true;
  $("history-list")?.scrollIntoView({ block: "nearest" });
}

function sortHistorySessions(sessions) {
  return (sessions || []).slice().sort((left, right) => (
    Number(right.last_activity_at_ms || 0) - Number(left.last_activity_at_ms || 0)
  ));
}

async function fetchSessionHistory(operation = currentSessionOperation()) {
  const body = await api(sessionHistoryPath(), { signal: operation.signal });
  return sortHistorySessions(body.sessions || []);
}

function cacheHistorySessions(sessions) {
  _historySessions = sessions;
  return _historySessions;
}

async function loadSessionHistory(operation = currentSessionOperation()) {
  try {
    const sessions = await fetchSessionHistory(operation);
    if (!isCurrentSessionOperation(operation)) return;
    cacheHistorySessions(sessions);
    if (!sessions.length) {
      $("history-list").innerHTML = `<div class="empty">还没有历史会议。</div>`;
      return;
    }
    $("history-list").innerHTML = sessions.slice(0, 8).map((s) => {
      const info = sessionSourceInfo(s.event_source || { provider: s.provider, provider_mode: s.provider_mode, is_mock: s.is_mock }, s);
      const warning = info.warning ? `<div class="history-warning">${escapeHtml(info.warning)}</div>` : "";
      return `<button class="btn history-item" data-session-id="${escapeHtml(s.session_id)}" style="width:100%;height:auto;justify-content:flex-start;align-items:flex-start;margin-bottom:8px;white-space:normal;flex-direction:column">
        <span><span class="${escapeHtml(info.className)}">${escapeHtml(info.label)}</span> · ${escapeHtml(s.session_id)}</span>
        <span style="color:var(--fg-secondary);font-size:12px">${s.final_count || 0} 条文字 · ${s.suggestion_card_count || 0} 条建议${s.has_minutes ? " · 已复盘" : ""}${s.has_audio ? " · 已保存录音" : ""}</span>
        ${warning}
      </button>`;
    }).join("");
    document.querySelectorAll(".history-item").forEach((el) => {
      el.addEventListener("click", () => openHistorySession(el.dataset.sessionId));
    });
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return;
    $("history-list").innerHTML = `<div class="empty">历史记录读取失败：${escapeHtml(err.message)}</div>`;
  }
}

function openHistoryModal() {
  const modal = $("history-modal");
  if (!modal) return;
  modal.hidden = false;
  const searchInput = $("history-search");
  if (searchInput) searchInput.value = "";
}

function closeHistoryModal() {
  const modal = $("history-modal");
  if (!modal) return;
  modal.hidden = true;
  const searchInput = $("history-search");
  if (searchInput) searchInput.value = "";
  const listEl = $("history-modal-list");
  if (listEl) listEl.innerHTML = "";
}

function formatHistoryTime(ms) {
  if (!ms) return "未知时间";
  const d = new Date(Number(ms));
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function loadSessionHistoryForModal() {
  const listEl = $("history-modal-list");
  if (!listEl) return;
  listEl.innerHTML = `<div class="empty">正在加载...</div>`;
  try {
    cacheHistorySessions(await fetchSessionHistory());
    const sessions = _historySessions;
    renderHistoryModalList("");
  } catch (err) {
    listEl.innerHTML = `<div class="empty">历史记录读取失败：${escapeHtml(err.message)}</div>`;
  }
}

function renderHistoryModalList(searchTerm) {
  const listEl = $("history-modal-list");
  if (!listEl) return;
  const term = String(searchTerm || "").trim().toLowerCase();
  const sessions = term
    ? _historySessions.filter((s) => {
        const sid = String(s.session_id || "").toLowerCase();
        const info = sessionSourceInfo(s.event_source || { provider: s.provider, provider_mode: s.provider_mode, is_mock: s.is_mock }, s);
        const label = String(info.label || "").toLowerCase();
        return sid.includes(term) || label.includes(term);
      })
    : _historySessions;
  if (!sessions.length) {
    listEl.innerHTML = `<div class="empty">${term ? "没有匹配的会议记录" : "还没有历史会议"}</div>`;
    return;
  }
  listEl.innerHTML = sessions.map((s) => {
    const info = sessionSourceInfo(s.event_source || { provider: s.provider, provider_mode: s.provider_mode, is_mock: s.is_mock }, s);
    const time = formatHistoryTime(s.last_activity_at_ms);
    const minutesPreview = s.has_minutes ? "" : "";
    return `<div class="history-modal-item" data-session-id="${escapeHtml(s.session_id)}">
      <div class="item-meta">
        <span class="${escapeHtml(info.className)}">${escapeHtml(info.label)}</span>
        <span>${escapeHtml(time)}</span>
        <span>${s.final_count || 0} 条文字</span>
        <span>${s.suggestion_card_count || 0} 条建议</span>
        <span>${s.has_minutes ? "已复盘" : "无纪要"}</span>
        ${s.has_audio ? '<span>已保存录音</span>' : ""}
      </div>
      ${minutesPreview}
      <div class="item-actions">
        <button class="btn btn-primary" data-action="open" data-session-id="${escapeHtml(s.session_id)}">打开</button>
        <button class="btn btn-danger" data-action="delete" data-session-id="${escapeHtml(s.session_id)}">删除</button>
      </div>
    </div>`;
  }).join("");
  listEl.querySelectorAll("button[data-action]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const sid = btn.dataset.sessionId;
      const action = btn.dataset.action;
      if (action === "open") {
        closeHistoryModal();
        await openHistorySession(sid);
      } else if (action === "delete") {
        if (!confirm("确定删除此会议记录？删除后不可恢复。")) return;
        try {
          await api(`/live/asr/sessions/${sid}`, { method: "DELETE" });
          _historySessions = _historySessions.filter((s) => s.session_id !== sid);
          renderHistoryModalList($("history-search")?.value || "");
          await loadSessionHistory();
          if (currentSession === sid) {
            resetSessionView("本次会议已删除。");
          }
          toast("会议记录已删除");
        } catch (err) {
          toast("删除失败: " + err.message);
        }
      }
    });
  });
}

const btnCloseHistory = $("btn-close-history");
if (btnCloseHistory) {
  btnCloseHistory.addEventListener("click", closeHistoryModal);
}

const historySearch = $("history-search");
if (historySearch) {
  historySearch.addEventListener("input", (e) => renderHistoryModalList(e.target.value));
}

function isInterruptedRecoveredSession(body = {}) {
  const source = body.event_source || {};
  const inputSource = String(source.input_source || source.audio_source || "");
  const isRealMicrophone = inputSource === "browser_live_mic" || inputSource === "real_mic";
  const endedNormally = (body.events || []).some((event) => (
    event.event_type === "end_of_stream"
    || (
      event.event_type === "evaluation_summary"
      && Number(event.payload?.end_of_stream_event_count || 0) > 0
    )
  ));
  return isRealMicrophone && !endedNormally;
}

async function restoreLatestRealSession() {
  if (currentSession) return false;
  const operation = beginSessionOperation();
  try {
    const history = await api(sessionHistoryPath(), { signal: operation.signal });
    if (!isCurrentSessionOperation(operation) || currentSession) return false;
    const latest = (history.sessions || [])
      .filter((session) => session.recoverable && !session.is_mock)
      .sort((left, right) => (
        Number(right.last_activity_at_ms || 0) - Number(left.last_activity_at_ms || 0)
      ))[0];
    if (!latest?.session_id) return false;
    const events = await api(`/live/asr/sessions/${latest.session_id}/events`, { signal: operation.signal });
    if (!isCurrentSessionOperation(operation) || currentSession) return false;
    prepareNewSession("", { sessionOperation: operation });
    currentSession = latest.session_id;
    const hasTranscript = sessionHasTranscript(events);
    const hasAudio = Boolean(events.audio?.saved);
    const interrupted = isInterruptedRecoveredSession(events);
    const message = interrupted
      ? hasAudio
        ? "已恢复最近会议。录音连接已中断，已保留截至断开时的文字和录音。"
        : "已恢复最近会议。录音连接已中断，已保留截至断开时的文字；本场历史会话未保存录音。"
      : hasTranscript
        ? "已恢复最近会议。"
      : hasAudio
        ? "已恢复最近会议，录音已保存，但实时识别未产生可用文字。"
        : "已恢复最近会议。未找到可用文字或录音。";
    applySessionEvents(latest.session_id, events, message, { runAutoSuggestions: false });
    setMeetingPhase(hasTranscript ? "ready" : "idle");
    return true;
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return false;
    console.warn("[workbench] 最近会议恢复失败:", err);
    return false;
  }
}

async function openHistorySession(sid) {
  if (!sid) return;
  const operation = beginSessionOperation();
  try {
    const ev = await api(`/live/asr/sessions/${sid}/events`, { signal: operation.signal });
    if (!isCurrentSessionOperation(operation)) return;
    prepareNewSession("", { sessionOperation: operation });
    currentSession = sid;
    const hasTranscript = sessionHasTranscript(ev);
    applySessionEvents(
      sid,
      ev,
      hasTranscript ? "已打开历史会议。" : sessionDegradationText(ev),
      { runAutoSuggestions: false },
    );
    setMeetingPhase(hasTranscript ? "ready" : "idle");
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return;
    toast("打开历史失败: " + err.message);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderDemoSessionFromCreated(sid, created) {
  currentSession = sid;
  currentEvents = created.live_events || [];
  currentSessionSource = created.event_source || { provider: MOCK_PAYLOAD.provider, is_mock: true, provider_mode: "mock" };
  renderSourceBadge(currentSessionSource, created);
  renderTranscriptAndCandidates(currentEvents);
  $("session-meta").textContent = `演示 · ${sid} · ${currentEvents.length} 条记录`;
  $("s-asr").textContent = "演示";
  $("sys-status").innerHTML = `<div class="empty">演示会议已加载，正在补全持久化状态。</div>`;
  setMeetingPhase("ready");
}

$("btn-load").addEventListener("click", async () => {
  try {
    prepareNewSession();
    console.log("[workbench] 试用示例会议...");
    const sid = "workbench_" + Date.now().toString(36);
    const created = await api("/live/asr/mock/sessions", { method: "POST", body: JSON.stringify({ ...MOCK_PAYLOAD, session_id: sid }) });
    console.log("[workbench] session 创建:", sid, "events:", (created.live_events||[]).length);
    renderDemoSessionFromCreated(sid, created);
    try {
      const ev = await api(`/live/asr/sessions/${sid}/events`);
      applySessionEvents(sid, ev, "演示会议已加载。可以生成会议建议或会后复盘。");
    } catch (snapshotErr) {
      console.warn("[workbench] 演示会议持久化状态读取失败:", snapshotErr);
      $("sys-status").innerHTML = `<div class="empty">演示会议已加载，但持久化状态暂时读取失败：${escapeHtml(snapshotErr.message)}</div>`;
    }
    await loadSessionHistory();
    toast("示例会议已加载");
  } catch (err) {
    console.error("[workbench] 加载失败:", err);
    toast("加载失败: " + err.message);
  }
});

$("btn-upload-label").addEventListener("click", () => $("btn-upload").click());

// 上传音频文件 → FunASR batch 转写 → session
$("btn-upload").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 500 * 1024 * 1024) {
    toast("文件超过 500MB 限制，请缩短录音或分段导入");
    e.target.value = "";
    return;
  }
  const operation = beginSessionOperation();
  const previousSession = currentSession;
  try {
    console.log("[workbench] 上传音频文件:", file.name, file.size, "bytes");
    toast("正在识别录音 " + file.name + " ...");
    setMeetingPhase("processing");
    $("sys-status").innerHTML = `<div class="empty">正在识别录音，可能需要 10-30 秒。</div>`;
    const formData = new FormData();
    formData.append("file", file);
    const r = await fetch(apiUrl("/live/asr/transcribe-file/sessions"), { method: "POST", body: formData, signal: operation.signal });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail || r.statusText);
    if (!isCurrentSessionOperation(operation)) return;
    console.log("[workbench] 文件转写完成:", body.session_id, "transcript:", body.transcript?.slice(0, 80));
    currentSession = body.session_id;
    prepareNewSession("", { sessionOperation: operation });
    currentSession = body.session_id;
    const ev = await api(`/live/asr/sessions/${currentSession}/events`, { signal: operation.signal });
    if (!isCurrentSessionOperation(operation)) return;
    applySessionEvents(currentSession, ev, "录音识别完成。可以生成会议建议或会后复盘。");
    $("session-meta").textContent = `录音 ${file.name} · ${committedTranscriptCount()} 段文字`;
    setMeetingPhase("ready");
    await loadSessionHistory();
    toast("录音识别完成");
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return;
    console.error("[workbench] 文件转写失败:", err);
    currentSession = previousSession;
    setMeetingPhase(currentSession ? "ready" : "idle");
    toast("录音识别失败: " + err.message);
    $("sys-status").innerHTML = `<div class="empty">录音识别失败: ${escapeHtml(err.message)}</div>`;
  }
  e.target.value = "";
});

function compactTranscriptText(text) {
  return String(text || "").replace(/\s+/g, "");
}

function createCanonicalTranscriptState() {
  return {
    schemaVersion: "canonical-transcript.v1",
    segments: [],
    activeTail: null,
    updatedAtMs: 0,
    sequenceCounter: 0,
  };
}

function canonicalTranscriptEventRank(eventType = "") {
  return {
    partial: 0,
    transcript_partial: 0,
    final: 1,
    transcript_final: 1,
    revision: 2,
    transcript_revision: 2,
  }[eventType] ?? -1;
}

function canonicalTranscriptEventTarget(event = {}, payload = {}) {
  if (event.event_type === "transcript_revision" || event.event_type === "revision") {
    const target = revisionTargetSegmentId(event, payload);
    if (target) return { segmentId: target, projectionKey: `segment:${target}`, revisionSupplement: false };
    const identity = String(event.id || payload.id || payload.segment_id || event.segment_id || Date.now());
    const segmentId = `revision-supplement:${identity}`;
    return { segmentId, projectionKey: segmentId, revisionSupplement: true };
  }
  const segmentId = String(payload.segment_id || event.segment_id || event.id || "").trim();
  return {
    segmentId,
    projectionKey: segmentId ? `segment:${segmentId}` : "",
    revisionSupplement: false,
  };
}

function canonicalTranscriptEvidenceIds(payload = {}) {
  const ids = [];
  (payload.evidence_ids || []).forEach((id) => {
    const value = String(id || "").trim();
    if (value && !ids.includes(value)) ids.push(value);
  });
  (payload.evidence_spans || []).forEach((span) => {
    const value = String(span?.id || "").trim();
    if (value && !ids.includes(value)) ids.push(value);
  });
  return ids;
}

function canonicalTranscriptSegmentFromEvent(event = {}, existing = null) {
  const payload = event.payload || {};
  const target = canonicalTranscriptEventTarget(event, payload);
  const rank = canonicalTranscriptEventRank(event.event_type);
  const displayText = transcriptEventText(event, payload);
  if (!target.segmentId || rank < 0 || !displayText) return null;
  const updatedAtMs = Number(event.at_ms ?? payload.end_ms ?? event.end_ms ?? Date.now()) || Date.now();
  const originalText = rank === 2
    ? String(existing?.originalText || existing?.displayText || payload.original_text || event.original_text || "")
    : displayText;
  return {
    segmentId: target.segmentId,
    projectionKey: target.projectionKey,
    sourceSegmentId: String(payload.segment_id || event.segment_id || event.id || target.segmentId),
    sequence: existing?.sequence || ++canonicalTranscriptState.sequenceCounter,
    startMs: Number(payload.start_ms ?? event.start_ms ?? 0) || 0,
    endMs: Number(payload.end_ms ?? event.end_ms ?? updatedAtMs) || updatedAtMs,
    updatedAtMs,
    rawText: String(payload.text || event.text || "").trim(),
    normalizedText: String(payload.normalized_text || event.normalized_text || displayText).trim(),
    correctedText: rank === 2 ? displayText : null,
    displayText,
    originalText,
    status: rank === 2 ? "corrected" : rank === 1 ? "final" : "partial",
    rank,
    evidenceIds: canonicalTranscriptEvidenceIds(payload),
    revisionSupplement: target.revisionSupplement,
    projectionReconciled: Boolean(payload.projection_reconciled || event.projection_reconciled),
    sourceSnapshotText: String(payload.source_snapshot_text || event.source_snapshot_text || "").trim(),
  };
}

function canonicalCommonPrefixLength(left = "", right = "") {
  const limit = Math.min(left.length, right.length);
  let length = 0;
  while (length < limit && left[length] === right[length]) length += 1;
  return length;
}

function truncateCanonicalSegmentsToChars(segments = [], charCount = 0) {
  let remaining = Math.max(0, Number(charCount) || 0);
  const truncated = [];
  for (const segment of segments) {
    if (remaining <= 0) break;
    if (segment.displayText.length <= remaining) {
      truncated.push(segment);
      remaining -= segment.displayText.length;
      continue;
    }
    const displayText = segment.displayText.slice(0, remaining);
    if (displayText) truncated.push({ ...segment, displayText, normalizedText: displayText });
    remaining = 0;
  }
  return truncated;
}

function reconcileCanonicalCommittedSegments() {
  const reconciled = [];
  canonicalTranscriptState.segments.forEach((segment) => {
    if (!segment.projectionReconciled || !segment.sourceSnapshotText) {
      reconciled.push(segment);
      return;
    }
    const committedText = reconciled.map((item) => item.displayText).join("");
    let displayText = "";
    if (segment.sourceSnapshotText.startsWith(committedText)) {
      displayText = segment.sourceSnapshotText.slice(committedText.length);
    } else {
      const commonPrefix = canonicalCommonPrefixLength(committedText, segment.sourceSnapshotText);
      reconciled.splice(0, reconciled.length, ...truncateCanonicalSegmentsToChars(reconciled, commonPrefix));
      displayText = segment.sourceSnapshotText.slice(commonPrefix);
    }
    if (displayText) reconciled.push({ ...segment, displayText, normalizedText: displayText });
  });
  canonicalTranscriptState.segments = reconciled;
}

function reconcileCanonicalActiveTail() {
  const tail = canonicalTranscriptState.activeTail;
  if (!tail?.sourceSnapshotText) return;
  const committedText = canonicalTranscriptState.segments.map((segment) => segment.displayText).join("");
  let displayText = "";
  if (tail.sourceSnapshotText.startsWith(committedText)) {
    displayText = tail.sourceSnapshotText.slice(committedText.length);
  } else if (tail.projectionReconciled) {
    const commonPrefix = canonicalCommonPrefixLength(committedText, tail.sourceSnapshotText);
    canonicalTranscriptState.segments = truncateCanonicalSegmentsToChars(
      canonicalTranscriptState.segments,
      commonPrefix,
    );
    displayText = tail.sourceSnapshotText.slice(commonPrefix);
  } else {
    return;
  }
  canonicalTranscriptState.activeTail = displayText
    ? { ...tail, displayText, normalizedText: displayText }
    : null;
}

function applyCanonicalTranscriptEvent(event = {}) {
  const payload = event.payload || {};
  const target = canonicalTranscriptEventTarget(event, payload);
  const existingIndex = canonicalTranscriptState.segments.findIndex((segment) => segment.projectionKey === target.projectionKey);
  const existing = existingIndex >= 0 ? canonicalTranscriptState.segments[existingIndex] : null;
  const segment = canonicalTranscriptSegmentFromEvent(event, existing);
  if (!segment) return false;
  if (existing && segment.rank < existing.rank) return false;
  if (
    existing
    && segment.rank === existing.rank
    && compactTranscriptText(segment.displayText) === compactTranscriptText(existing.displayText)
    && segment.status === existing.status
  ) {
    return false;
  }
  canonicalTranscriptState.updatedAtMs = Math.max(canonicalTranscriptState.updatedAtMs, segment.updatedAtMs);

  if (segment.rank === 0) {
    if (existing?.rank >= 1) return false;
    if (canonicalTranscriptState.activeTail && segment.updatedAtMs < canonicalTranscriptState.activeTail.updatedAtMs) return false;
    const unchanged = canonicalTranscriptState.activeTail
      && canonicalTranscriptState.activeTail.projectionKey === segment.projectionKey
      && compactTranscriptText(canonicalTranscriptState.activeTail.displayText) === compactTranscriptText(segment.displayText);
    canonicalTranscriptState.activeTail = segment;
    reconcileCanonicalActiveTail();
    return !unchanged;
  }

  if (existingIndex >= 0) canonicalTranscriptState.segments.splice(existingIndex, 1, segment);
  else canonicalTranscriptState.segments.push(segment);
  canonicalTranscriptState.segments.sort((left, right) => left.sequence - right.sequence);
  reconcileCanonicalCommittedSegments();
  if (
    canonicalTranscriptState.activeTail
    && (
      canonicalTranscriptState.activeTail.segmentId === segment.segmentId
      || canonicalTranscriptState.activeTail.projectionKey === segment.projectionKey
      || canonicalTranscriptState.activeTail.updatedAtMs <= segment.updatedAtMs
    )
  ) {
    canonicalTranscriptState.activeTail = null;
  }
  return true;
}

function replaceCanonicalTranscriptSnapshot(snapshot = null, fallbackEvents = []) {
  const previousFullText = canonicalTranscriptFullText();
  canonicalTranscriptState = createCanonicalTranscriptState();
  if (snapshot && Array.isArray(snapshot.segments)) {
    canonicalTranscriptState.schemaVersion = snapshot.schema_version || "canonical-transcript.v1";
    canonicalTranscriptState.segments = snapshot.segments.map((segment, index) => ({
      segmentId: String(segment.segment_id || `segment_${index + 1}`),
      projectionKey: String(segment.projection_key || `segment:${segment.segment_id || `segment_${index + 1}`}`),
      sourceSegmentId: String(segment.source_segment_id || segment.segment_id || ""),
      sequence: Number(segment.sequence || index + 1),
      startMs: Number(segment.start_ms || 0),
      endMs: Number(segment.end_ms || 0),
      updatedAtMs: Number(segment.updated_at_ms || segment.end_ms || 0),
      rawText: String(segment.raw_text || ""),
      normalizedText: String(segment.normalized_text || segment.display_text || ""),
      correctedText: segment.corrected_text ? String(segment.corrected_text) : null,
      displayText: String(segment.display_text || ""),
      originalText: String(segment.original_text || segment.display_text || ""),
      status: String(segment.status || "final"),
      rank: segment.status === "corrected" ? 2 : 1,
      evidenceIds: Array.isArray(segment.evidence_ids) ? [...segment.evidence_ids] : [],
      revisionSupplement: Boolean(segment.revision_supplement),
      projectionReconciled: Boolean(segment.projection_reconciled),
      sourceSnapshotText: String(segment.source_snapshot_text || ""),
    })).filter((segment) => segment.displayText);
    const tail = snapshot.active_tail;
    if (tail?.display_text) {
      canonicalTranscriptState.activeTail = {
        segmentId: String(tail.segment_id || "active_tail"),
        projectionKey: String(tail.projection_key || `segment:${tail.segment_id || "active_tail"}`),
        sourceSegmentId: String(tail.source_segment_id || tail.segment_id || ""),
        sequence: Number(tail.sequence || canonicalTranscriptState.segments.length + 1),
        startMs: Number(tail.start_ms || 0),
        endMs: Number(tail.end_ms || 0),
        updatedAtMs: Number(tail.updated_at_ms || tail.end_ms || 0),
        rawText: String(tail.raw_text || ""),
        normalizedText: String(tail.normalized_text || tail.display_text || ""),
        correctedText: null,
        displayText: String(tail.display_text),
        originalText: String(tail.original_text || tail.display_text || ""),
        status: "partial",
        rank: 0,
        evidenceIds: Array.isArray(tail.evidence_ids) ? [...tail.evidence_ids] : [],
        revisionSupplement: false,
        projectionReconciled: Boolean(tail.projection_reconciled),
        sourceSnapshotText: String(tail.source_snapshot_text || ""),
      };
    }
    canonicalTranscriptState.sequenceCounter = Math.max(
      0,
      ...canonicalTranscriptState.segments.map((segment) => segment.sequence),
      canonicalTranscriptState.activeTail?.sequence || 0,
    );
    canonicalTranscriptState.updatedAtMs = Number(snapshot.updated_at_ms || 0);
  } else {
    (fallbackEvents || []).forEach((event) => applyCanonicalTranscriptEvent(event));
  }
  renderCanonicalTranscriptView({ contentChanged: canonicalTranscriptFullText() !== previousFullText });
  return canonicalTranscriptState;
}

function canonicalTranscriptFullText() {
  return [
    ...canonicalTranscriptState.segments.map((segment) => segment.displayText),
    canonicalTranscriptState.activeTail?.displayText || "",
  ].join("");
}

function ensureCanonicalTranscriptContainers() {
  const stream = $("transcript-stream");
  let documentNode = $("transcript-document");
  let tailNode = $("transcript-active-tail");
  if (!documentNode || !tailNode) {
    stream.innerHTML = "";
    documentNode = null;
    tailNode = null;
  }
  if (!documentNode) {
    documentNode = document.createElement("div");
    documentNode.id = "transcript-document";
    documentNode.className = "transcript-document";
    stream.appendChild(documentNode);
  }
  if (!tailNode) {
    tailNode = document.createElement("div");
    tailNode.id = "transcript-active-tail";
    tailNode.className = "utterance live-partial transcript-active-tail";
    tailNode.hidden = true;
    stream.appendChild(tailNode);
  }
  return { stream, documentNode, tailNode };
}

function renderCanonicalTranscriptEmptyState(message = TRANSCRIPT_EMPTY_MESSAGE) {
  const { documentNode, tailNode } = ensureCanonicalTranscriptContainers();
  documentNode.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
  tailNode.hidden = true;
  tailNode.innerHTML = "";
  tailNode.removeAttribute("data-live-segment-id");
}

function groupCanonicalTranscriptSegments(segments = []) {
  const paragraphs = [];
  segments.forEach((segment) => {
    const current = paragraphs[paragraphs.length - 1];
    const gapMs = current ? Math.max(0, segment.startMs - current.endMs) : Infinity;
    const combinedChars = current
      ? current.segments.reduce((sum, item) => sum + item.displayText.length, 0) + segment.displayText.length
      : segment.displayText.length;
    const evidenceBoundary = Boolean(
      segment.status === "corrected"
      || segment.evidenceIds.length
      || current?.segments.some((item) => item.status === "corrected" || item.evidenceIds.length)
    );
    if (!current || gapMs > 3000 || combinedChars > 180 || evidenceBoundary) {
      paragraphs.push({ startMs: segment.startMs, endMs: segment.endMs, segments: [segment] });
      return;
    }
    current.segments.push(segment);
    current.endMs = Math.max(current.endMs, segment.endMs);
  });
  return paragraphs;
}

function renderCommittedTranscriptDocument({ syncFollow = true } = {}) {
  const followSnapshot = syncFollow ? captureTranscriptFollowState() : null;
  const { documentNode } = ensureCanonicalTranscriptContainers();
  documentNode.innerHTML = "";
  const paragraphs = groupCanonicalTranscriptSegments(canonicalTranscriptState.segments);
  if (!paragraphs.length) {
    if (!canonicalTranscriptState.activeTail) {
      documentNode.innerHTML = `<div class="empty">${escapeHtml(TRANSCRIPT_EMPTY_MESSAGE)}</div>`;
    }
    syncMeetingOverview({ transcriptCount: 0 });
    if (syncFollow) syncTranscriptAfterRender(followSnapshot);
    return false;
  }
  paragraphs.forEach((paragraph) => {
    const row = document.createElement("div");
    row.className = "utterance transcript-paragraph";
    row.setAttribute("role", "article");
    row.dataset.transcriptSegmentId = paragraph.segments[0].projectionKey;
    const time = document.createElement("div");
    time.className = "paragraph-time ts";
    time.textContent = fmtMs(paragraph.startMs);
    const body = document.createElement("div");
    body.className = "paragraph-body text";
    paragraph.segments.forEach((segment, index) => {
      const wrapper = document.createElement("span");
      wrapper.className = "transcript-segment";
      wrapper.dataset.segmentId = segment.revisionSupplement
        ? (segment.sourceSegmentId || segment.segmentId)
        : (segment.segmentId || segment.sourceSegmentId);
      wrapper.dataset.sourceSegmentId = segment.sourceSegmentId || segment.segmentId;
      wrapper.dataset.transcriptSegmentId = segment.projectionKey;
      wrapper.dataset.originalAsrText = segment.originalText || segment.displayText;
      wrapper.dataset.status = segment.status;
      if (segment.evidenceIds.length) wrapper.dataset.evidenceIds = segment.evidenceIds.join(",");
      if (segment.status === "corrected") wrapper.dataset.revisionOf = segment.segmentId;
      if (segment.revisionSupplement) wrapper.dataset.revisionSupplement = "true";
      const text = document.createElement("span");
      text.className = "transcript-text";
      const previousText = paragraph.segments[index - 1]?.displayText || "";
      const separator = previousText && /[A-Za-z0-9]$/.test(previousText) && /^[A-Za-z0-9]/.test(segment.displayText) ? " " : "";
      text.textContent = separator + segment.displayText;
      wrapper.appendChild(text);
      if (segment.status === "corrected") {
        const badge = document.createElement("span");
        badge.className = "correction-badge";
        badge.textContent = segment.revisionSupplement ? "修正补充" : "AI 已校正";
        wrapper.appendChild(badge);
      }
      body.appendChild(wrapper);
      if (
        segment.status === "corrected"
        && segment.originalText
        && compactTranscriptText(segment.originalText) !== compactTranscriptText(segment.displayText)
      ) {
        const details = document.createElement("details");
        details.className = "original-asr-text";
        details.dataset.segmentId = segment.segmentId;
        details.innerHTML = `<summary>查看原始识别</summary><div>${escapeHtml(segment.originalText)}</div>`;
        body.appendChild(details);
      }
    });
    row.append(time, body);
    documentNode.appendChild(row);
  });
  syncMeetingOverview({ transcriptCount: canonicalTranscriptState.segments.length });
  if (syncFollow) syncTranscriptAfterRender(followSnapshot);
  return true;
}

function upsertCanonicalActiveTail({ syncFollow = true } = {}) {
  const followSnapshot = syncFollow ? captureTranscriptFollowState() : null;
  const { documentNode, tailNode } = ensureCanonicalTranscriptContainers();
  const tail = canonicalTranscriptState.activeTail;
  if (!tail?.displayText) {
    tailNode.hidden = true;
    tailNode.innerHTML = "";
    tailNode.removeAttribute("data-live-segment-id");
    if (!canonicalTranscriptState.segments.length && !documentNode.children.length) {
      documentNode.innerHTML = `<div class="empty">${escapeHtml(TRANSCRIPT_EMPTY_MESSAGE)}</div>`;
    }
    if (syncFollow) syncTranscriptAfterRender(followSnapshot);
    return false;
  }
  documentNode.querySelector(".empty")?.remove();
  tailNode.hidden = false;
  tailNode.dataset.liveSegmentId = tail.segmentId;
  tailNode.dataset.segmentId = tail.segmentId;
  tailNode.innerHTML = `<div class="ts">${fmtMs(tail.startMs)}</div><div class="text"><span class="tail-label">正在识别</span><span class="tail-text">${escapeHtml(tail.displayText)}</span></div>`;
  if (syncFollow) syncTranscriptAfterRender(followSnapshot);
  return true;
}

function renderCanonicalTranscriptView({ contentChanged = true } = {}) {
  const followSnapshot = captureTranscriptFollowState();
  renderCommittedTranscriptDocument({ syncFollow: false });
  upsertCanonicalActiveTail({ syncFollow: false });
  syncTranscriptAfterRender(followSnapshot, { contentChanged });
}

function partialDraftKey(event = {}, payload = {}) {
  return String(payload.segment_id || event.segment_id || event.id || "").trim();
}

function revisionTargetSegmentId(event = {}, payload = {}) {
  return String(
    payload.supersedes_segment_id
    || payload.revision_of
    || event.supersedes_segment_id
    || event.revision_of
    || ""
  ).trim();
}

function revisionSupplementSegmentId(event = {}, payload = {}) {
  const identity = String(
    payload.id
    || event.id
    || payload.segment_id
    || event.segment_id
    || `${payload.start_ms || event.start_ms || 0}:${compactTranscriptText(transcriptEventText(event, payload))}`
  ).trim();
  return `revision-supplement:${identity}`;
}

function transcriptProjectionKey(event = {}, payload = {}) {
  if (event.event_type === "transcript_revision" && !revisionTargetSegmentId(event, payload)) {
    return revisionSupplementSegmentId(event, payload);
  }
  const segmentId = event.event_type === "transcript_revision"
    ? revisionTargetSegmentId(event, payload)
    : partialDraftKey(event, payload);
  if (!segmentId) return "";
  return `segment:${segmentId}`;
}

function isPartialEventType(eventType = "") {
  return eventType === "partial" || eventType === "transcript_partial";
}

function isTranscriptEventType(eventType = "") {
  return isPartialEventType(eventType)
    || eventType === "final"
    || eventType === "transcript_final"
    || eventType === "transcript_revision";
}

function transcriptTargetSegmentId(event = {}, payload = {}) {
  return transcriptProjectionKey(event, payload);
}

function transcriptEventText(event = {}, payload = {}) {
  return String(payload.normalized_text || payload.text || event.normalized_text || event.text || "").trim();
}

function transcriptEventRank(eventType = "") {
  return {
    partial: 0,
    transcript_partial: 0,
    final: 1,
    transcript_final: 2,
    transcript_revision: 3,
  }[eventType] ?? -1;
}

function projectTranscriptEvents(events = []) {
  const projected = new Map();
  (events || []).forEach((event = {}, index) => {
    if (!isTranscriptEventType(event.event_type)) return;
    const payload = event.payload || {};
    const projectionKey = transcriptProjectionKey(event, payload);
    const segmentId = transcriptTargetSegmentId(event, payload);
    const text = transcriptEventText(event, payload);
    if (!projectionKey || !segmentId || !text) return;
    const rank = transcriptEventRank(event.event_type);
    const previous = projected.get(projectionKey);
    if (previous && rank < previous.rank) return;
    const previousCommittedText = previous && previous.rank >= 1 ? previous.text : "";
    const originalText = event.event_type === "transcript_revision"
      ? String(previous?.originalText || previousCommittedText || payload.original_text || event.original_text || "")
      : String(previous?.originalText || (rank >= 1 ? previousCommittedText || text : ""));
    projected.set(projectionKey, {
      event,
      payload,
      projectionKey,
      segmentId,
      text,
      rank,
      originalText,
      order: previous?.order ?? index,
    });
  });
  return projected;
}

function replaceTranscriptRenderState(projection = new Map()) {
  transcriptRenderStateBySegment.clear();
  projection.forEach((entry, projectionKey) => {
    transcriptRenderStateBySegment.set(projectionKey, {
      rank: entry.rank,
      compactText: compactTranscriptText(entry.text),
      eventType: entry.event?.event_type || "",
    });
  });
}

function shouldApplyTranscriptEvent(event = {}, payload = {}, text = "") {
  const projectionKey = transcriptProjectionKey(event, payload);
  if (!projectionKey || !text) return false;
  const rank = transcriptEventRank(event.event_type);
  const previous = transcriptRenderStateBySegment.get(projectionKey);
  if (!previous) return true;
  if (rank < previous.rank) return false;
  if (previous.rank >= 1 && rank > previous.rank && previous.compactText === compactTranscriptText(text)) {
    registerTranscriptEvent(event, payload, text);
    return false;
  }
  if (rank === previous.rank && previous.compactText === compactTranscriptText(text)) return false;
  return true;
}

function registerTranscriptEvent(event = {}, payload = {}, text = "") {
  const projectionKey = transcriptProjectionKey(event, payload);
  if (!projectionKey) return;
  transcriptRenderStateBySegment.set(projectionKey, {
    rank: transcriptEventRank(event.event_type),
    compactText: compactTranscriptText(text),
    eventType: event.event_type || "",
  });
}

function latestUnresolvedPartials(events = []) {
  const latest = new Map();
  projectTranscriptEvents(events).forEach((entry) => {
    if (!isPartialEventType(entry.event?.event_type)) return;
    latest.set(partialDraftKey(entry.event, entry.payload), entry.event);
  });
  return latest;
}

function committedTranscriptSelector(segmentId) {
  return `[data-transcript-segment-id="${CSS.escape(String(segmentId || ""))}"]`;
}

function buildCommittedTranscriptRow(event = {}, payload = {}, text = "", options = {}) {
  const targetSegmentId = String(options.targetSegmentId || transcriptTargetSegmentId(event, payload)).trim();
  const isRevision = event.event_type === "transcript_revision";
  const isRevisionSupplement = isRevision && !revisionTargetSegmentId(event, payload);
  const originalText = String(options.originalText || payload.original_text || event.original_text || "").trim();
  const row = document.createElement("div");
  row.className = isRevision ? `utterance transcript-revision${isRevisionSupplement ? " revision-supplement" : ""}` : "utterance";
  row.setAttribute("role", "article");
  row.setAttribute("aria-label", isRevisionSupplement ? "修正补充" : (isRevision ? "AI 已校正的会议文字" : "已确认的会议文字"));
  row.dataset.transcriptSegmentId = targetSegmentId;
  row.dataset.segmentId = targetSegmentId;
  if (isRevisionSupplement) {
    row.dataset.revisionSupplement = "true";
  } else if (isRevision) {
    row.dataset.revisionOf = payload.revision_of || event.revision_of || targetSegmentId;
    row.dataset.supersedesSegmentId = payload.supersedes_segment_id || payload.revision_of || targetSegmentId;
  }
  attachTranscriptEvidence(row, payload, event);
  const correctionLabel = isRevisionSupplement ? "修正补充" : "AI 已校正";
  const correctionBadge = isRevision ? `<span class="correction-badge">${correctionLabel}</span>` : "";
  const originalDetails = isRevision && !isRevisionSupplement && originalText && compactTranscriptText(originalText) !== compactTranscriptText(text)
    ? `<details class="original-asr-text"><summary>查看原始识别</summary><div>${escapeHtml(originalText)}</div></details>`
    : "";
  row.innerHTML = `<div class="ts">${fmtMs(payload.start_ms || event.start_ms || 0)}</div><div class="text"><span class="speaker">发言：</span><span class="transcript-text">${escapeHtml(text)}</span>${correctionBadge}${originalDetails}</div>`;
  return row;
}

function upsertCommittedTranscript(event = {}, payload = {}, text = "", options = {}) {
  if (!shouldApplyTranscriptEvent(event, payload, text)) return false;
  const targetSegmentId = String(options.targetSegmentId || transcriptTargetSegmentId(event, payload)).trim();
  if (!targetSegmentId) return false;
  claimRecordingDraftView();
  const stream = $("transcript-stream");
  const existing = stream.querySelector(committedTranscriptSelector(targetSegmentId));
  const originalText = String(
    options.originalText
    || existing?.dataset.originalAsrText
    || existing?.querySelector(".transcript-text")?.textContent
    || ""
  ).trim();
  const row = buildCommittedTranscriptRow(event, payload, text, { targetSegmentId, originalText });
  row.dataset.originalAsrText = originalText || text;
  if (existing) existing.replaceWith(row);
  else stream.appendChild(row);
  registerTranscriptEvent(event, payload, text);
  return true;
}

function shouldDisplayPartial(event = {}, payload = {}, text = "") {
  const key = partialDraftKey(event, payload);
  if (!key) return false;
  const compact = compactTranscriptText(text);
  if (compact.length < PARTIAL_DRAFT_MIN_CHARS) return false;
  const confidence = Number(payload.confidence ?? event.confidence ?? PARTIAL_DRAFT_MIN_CONFIDENCE);
  if (Number.isFinite(confidence) && confidence < PARTIAL_DRAFT_MIN_CONFIDENCE) return false;
  return true;
}

function livePartialMarkup(event = {}, payload = {}, text = "") {
  return `<div class="ts">${fmtMs(payload.start_ms || event.start_ms || 0)}</div><div class="text" aria-live="off"><span class="speaker">正在听：</span>${escapeHtml(text)}</div>`;
}

function livePartialSelector(segmentId) {
  return `[data-live-segment-id="${CSS.escape(String(segmentId || ""))}"]`;
}

function upsertLivePartial(event = {}, payload = {}, text = "") {
  if (!shouldDisplayPartial(event, payload, text)) return false;
  if (!shouldApplyTranscriptEvent(event, payload, text)) return false;
  claimRecordingDraftView();
  clearStreamEmptyState();
  const segmentId = partialDraftKey(event, payload);
  const compactText = compactTranscriptText(text);
  const previous = latestPartialTextBySegment.get(segmentId) || {};
  if (previous.displayText === compactText) return false;
  latestPartialTextBySegment.set(segmentId, { ...previous, displayText: compactText });
  const stream = $("transcript-stream");
  let row = stream.querySelector(livePartialSelector(segmentId));
  if (!row) {
    row = document.createElement("div");
    row.className = "utterance live-partial";
    row.dataset.liveSegmentId = segmentId;
    stream.appendChild(row);
  }
  row.innerHTML = livePartialMarkup(event, payload, text);
  registerTranscriptEvent(event, payload, text);
  return true;
}

function removeLivePartialForSegment(segmentId = "") {
  const key = String(segmentId || "").trim();
  if (!key) return;
  document.querySelectorAll(livePartialSelector(key)).forEach((div) => div.remove());
  latestPartialTextBySegment.delete(key);
}

function appendLiveEvent(e) {
  const stream = $("transcript-stream");
  const p = e.payload || {};
  const renderResult = { visibleTextChanged: false, eventType: e.event_type || "" };
  if (e.event_type === "provider_error") {
    const code = e.error_code || p.error_code || "real_asr_sidecar_unavailable";
    const message = e.message || p.message || "实时识别不可用，请检查本地语音识别服务。";
    if (restorePreservedSessionAfterRecordingFailure(`实时识别不可用：${message}`, _recSid)) return renderResult;
    if (canonicalTranscriptFullText().trim()) {
      renderCanonicalTranscriptView({ contentChanged: false });
    } else {
      renderCanonicalTranscriptEmptyState(`实时识别不可用：${message}`);
    }
    $("sys-status").innerHTML = `<div class="empty">实时识别不可用：${escapeHtml(message)}</div>`;
    $("s-asr").textContent = "不可用";
    $("source-badge").className = "source-badge degraded";
    $("source-badge").textContent = "降级";
    $("source-badge").title = "实时识别不可用，本次不能作为真实验收。";
    $("session-meta").textContent = "实时识别不可用";
    setMeetingPhase("idle");
  } else if (e.event_type === "transcript_revision") {
    const text = transcriptEventText(e, p);
    if (!text) return renderResult;
    claimRecordingDraftView();
    const rendered = applyCanonicalTranscriptEvent(e);
    if (rendered) {
      renderCanonicalTranscriptView();
      renderResult.visibleTextChanged = true;
      announceCommittedTranscript(text, true);
    }
  } else if (e.event_type === "partial" || e.event_type === "transcript_partial") {
    const text = transcriptEventText(e, p);
    if (!text) return renderResult;
    claimRecordingDraftView();
    const rendered = applyCanonicalTranscriptEvent(e);
    if (rendered) {
      upsertCanonicalActiveTail();
      renderResult.visibleTextChanged = true;
    }
  } else if (e.event_type === "final" || e.event_type === "transcript_final") {
    const text = transcriptEventText(e, p);
    if (!text) {
      if (canonicalTranscriptState.activeTail) {
        $("sys-status").innerHTML = `<div class="empty">最终识别暂时为空，已保留最后一条临时文字。</div>`;
      }
      return renderResult;
    }
    claimRecordingDraftView();
    const rendered = applyCanonicalTranscriptEvent(e);
    if (rendered) {
      renderCanonicalTranscriptView();
      renderResult.visibleTextChanged = true;
      announceCommittedTranscript(text, false);
    }
  } else if (e.event_type === "suggestion_candidate_event" || e.event_type === "partial_hint_event") {
    appendCandidateReminderEvent(e);
    syncCandidateFocusCounts();
    renderCandidateReminders();
    announceRealtimeReminder(e);
  }
  syncMeetingOverview();
  return renderResult;
}

function attachTranscriptEvidence(div, payload = {}, event = {}) {
  const segmentId = payload.segment_id || event.segment_id || payload.id || event.id || "";
  if (segmentId) div.dataset.segmentId = segmentId;
  const evidenceIds = (payload.evidence_spans || [])
    .map((span) => span.id)
    .filter(Boolean);
  if (evidenceIds.length) div.dataset.evidenceIds = evidenceIds.join(" ");
}

function markSessionAudioCleanupPending(body = {}) {
  currentSessionCleanupPending = true;
  currentSessionSource = {
    ...(currentSessionSource || {}),
    delete_cleanup_pending: true,
  };
  $("session-meta").textContent = `会议记录已删除 · 录音清理待重试 · ${currentSession || ""}`;
  $("source-badge").className = "source-badge degraded";
  $("source-badge").textContent = "录音清理待重试";
  $("source-badge").title = "会议文字和分析结果仍保留在当前页面；录音文件删除失败，可再次点击删除按钮重试。";
  $("btn-delete").textContent = "重试清理录音";
  const errorCount = Array.isArray(body.errors) ? body.errors.length : 0;
  $("sys-status").innerHTML = `<div class="empty">会议文字和分析结果已保留在当前页面，但录音文件仍在等待本地清理${errorCount ? `（${errorCount} 个清理错误）` : ""}。请点击“重试清理录音”。</div>`;
  syncActionAvailability(currentMeetingPhase);
  syncMeetingOverview();
  toast("会议记录已删除，但录音文件仍在等待本地清理");
  return true;
}

function deleteConfirmationText() {
  const sourceInfo = sessionSourceInfo(currentSessionSource || {});
  const textCount = currentEvents.filter((e) => e.event_type === "transcript_final" || e.event_type === "final" || e.event_type === "transcript_revision").length;
  const minutesState = currentMinutes ? "已生成" : "未生成";
  if (currentSessionCleanupPending) {
    return [
      "录音文件上次清理未完成，确定重试吗？",
      `会议来源：${sourceInfo.label}`,
      "当前页面的文字和分析结果仅作为只读快照保留。",
    ].join("\n");
  }
  return [
    "确定删除本次会议吗？",
    `会议来源：${sourceInfo.label}${sourceInfo.warning ? `（${sourceInfo.warning}）` : ""}`,
    `文字条数：${textCount}`,
    `AI 建议：${currentSuggestionCards.length}`,
    `方案分析：${currentApproachCards.length}`,
    `会议纪要：${minutesState}`,
    "删除范围：本地会议记录、文字、实时建议、AI 建议、方案分析和会议纪要。",
    "当前页面不会删除你电脑上另存的原始音频文件。",
  ].join("\n");
}

$("btn-delete").addEventListener("click", async () => {
  if (!currentSession) return toast("还没有会议可删除");
  const sid = currentSession;
  if (!confirm(deleteConfirmationText())) return;
  const operation = beginSessionOperation();
  try {
    const outcome = await api(`/live/asr/sessions/${sid}`, { method: "DELETE", signal: operation.signal });
    if (!isCurrentSessionOperation(operation)) return;
    if (outcome?.audio_cleanup_pending === true && outcome?.session_record_deleted === true) {
      markSessionAudioCleanupPending(outcome);
      await loadSessionHistory(operation);
      return;
    }
    resetSessionView("本次会议已删除。", { sessionOperation: operation });
    await loadSessionHistory(operation);
    toast("本次会议已删除");
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return;
    toast("删除失败: " + err.message);
  }
});

function downloadSessionArtifact(url, fallbackName) {
  if (!currentSession) {
    toast("还没有会议可导出");
    return;
  }
  const link = document.createElement("a");
  link.href = apiUrl(url);
  link.download = fallbackName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

$("btn-export-transcript").addEventListener("click", () => {
  if (!currentSessionHasTranscript()) return toast("还没有可导出的文字稿");
  downloadSessionArtifact(`/live/asr/sessions/${currentSession}/transcript.txt`, `${currentSession}.transcript.txt`);
});

$("btn-export-minutes").addEventListener("click", () => {
  if (!currentMinutes) return toast("请先生成会议纪要");
  downloadSessionArtifact(`/live/asr/sessions/${currentSession}/minutes.md`, `${currentSession}.minutes.md`);
});

$("btn-export-audio").addEventListener("click", () => {
  if (!currentSessionHasAudio()) return toast("本次会议没有可导出的录音");
  const format = currentAudioAsset.format || "wav";
  downloadSessionArtifact(`/live/asr/sessions/${currentSession}/audio.wav`, `${currentSession}.audio.${format}`);
});

function setRecState(state) {
  if (state === "recording") setMeetingPhase("recording");
  else if (state === "live") setMeetingPhase("ready");
  else setMeetingPhase("idle");
}

let _micCtx = null, _micWs = null, _recSid = null;
let _manualStop = false, _reconnecting = false, _reconnectAttempts = 0;
let _stopRequestedAfterReconnect = false;
let _asrReadyStopTimer = null;
const MAX_RECONNECT_ATTEMPTS = 3, RECONNECT_INTERVAL_MS = 2000;
const MAX_UNSENT_MIC_FRAMES = 100;
let _micStream = null, _micSource = null, _micNode = null, _micMonitor = null;
let _micInputSampleRate = 16000, _micPendingSamples = new Float32Array(0), _micSentChunks = 0;
let _micUnsentFrames = [];
let _micDroppedFrameCount = 0;
let _micAsrReady = false;
let _micPeak = 0, _micRms = 0, _micLevelFrames = 0, _micLastLevelStatusAt = 0;
let _micHealthSampleCount = 0, _micHealthSquareSum = 0, _micHealthActiveCount = 0, _micHealthPeak = 0;
let _lastBrowserMicHealthReport = null;
let _realtimeUiMetrics = null;

function resampleTo16k(input, inputSampleRate) {
  const targetRate = 16000;
  if (!input || !input.length) return new Float32Array(0);
  if (inputSampleRate === targetRate) return new Float32Array(input);
  const ratio = inputSampleRate / targetRate;
  const outputLength = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i++) {
    const pos = i * ratio;
    const left = Math.floor(pos);
    const right = Math.min(left + 1, input.length - 1);
    const frac = pos - left;
    output[i] = input[left] + (input[right] - input[left]) * frac;
  }
  return output;
}

function concatFloat32(a, b) {
  if (!a.length) return b;
  const out = new Float32Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

function queueOrSendMicFrame(frame) {
  if (_micWs && _micWs.readyState === WebSocket.OPEN && _micAsrReady) {
    flushQueuedMicFrames();
    _micWs.send(frame.buffer);
    _micSentChunks++;
    return true;
  }
  if (_micUnsentFrames.length >= MAX_UNSENT_MIC_FRAMES) {
    _micDroppedFrameCount++;
    document.body.dataset.audioQueueOverflow = "true";
    $("sys-status").innerHTML = `<div class="empty">录音连接中断时间过长，部分音频未能保留；正在继续重连。</div>`;
    return false;
  }
  _micUnsentFrames.push(new Float32Array(frame));
  return false;
}

function flushQueuedMicFrames() {
  if (!_micWs || _micWs.readyState !== WebSocket.OPEN || !_micAsrReady) return 0;
  let flushed = 0;
  while (_micUnsentFrames.length) {
    const frame = _micUnsentFrames.shift();
    _micWs.send(frame.buffer);
    _micSentChunks++;
    flushed++;
  }
  return flushed;
}

function sendPcm16kFrame(input) {
  const pcm16 = resampleTo16k(input, _micInputSampleRate);
  _micPendingSamples = concatFloat32(_micPendingSamples, pcm16);
  const chunkSamples = 4800;
  while (_micPendingSamples.length >= chunkSamples) {
    const frame = _micPendingSamples.slice(0, chunkSamples);
    _micPendingSamples = _micPendingSamples.slice(chunkSamples);
    queueOrSendMicFrame(frame);
  }
}

function flushPendingPcm() {
  if (_micPendingSamples.length) {
    const frame = new Float32Array(_micPendingSamples);
    queueOrSendMicFrame(frame);
  }
  _micPendingSamples = new Float32Array(0);
}

function updateMicLevel(input) {
  let peak = 0;
  let sum = 0;
  let activeFrameCount = 0;
  for (let i = 0; i < input.length; i++) {
    const value = Math.abs(input[i]);
    if (value > peak) peak = value;
    sum += value * value;
    _micHealthSampleCount++;
    _micHealthSquareSum += value * value;
    if (value >= MIC_ACTIVE_SAMPLE_THRESHOLD) {
      _micHealthActiveCount++;
      activeFrameCount++;
    }
  }
  if (peak > _micHealthPeak) _micHealthPeak = peak;
  _micPeak = peak;
  _micRms = Math.sqrt(sum / Math.max(input.length, 1));
  setMicLevelMeter(Math.max(_micRms * 3.2, peak * 0.35));
  _micLevelFrames++;
  const activeFrameRatio = activeFrameCount / Math.max(input.length, 1);
  recordRealtimeAudioActivityMetric(activeFrameRatio, _micRms, peak);
}

function resetMicHealthStats() {
  _micHealthSampleCount = 0;
  _micHealthSquareSum = 0;
  _micHealthActiveCount = 0;
  _micHealthPeak = 0;
  _lastBrowserMicHealthReport = null;
}

function classifyBrowserMicHealth(report) {
  if (!report.sample_count) return "blocked_no_audio_samples";
  if (
    report.rms < MIC_MIN_RMS
    || report.peak < MIC_MIN_PEAK
    || report.active_sample_ratio < MIC_MIN_ACTIVE_SAMPLE_RATIO
  ) {
    return "blocked_audio_too_quiet";
  }
  return "audio_capture_health_passed";
}

function browserMicHealthSnapshot() {
  const sampleCount = _micHealthSampleCount;
  const rms = Math.sqrt(_micHealthSquareSum / Math.max(sampleCount, 1));
  const activeSampleRatio = sampleCount ? _micHealthActiveCount / sampleCount : 0;
  const report = {
    report_type: "workbench_browser_mic_health",
    session_id: _recSid || currentSession || null,
    sample_count: sampleCount,
    chunk_count: _micSentChunks,
    rms,
    peak: _micHealthPeak,
    active_sample_ratio: activeSampleRatio,
    raw_audio_uploaded: false,
    remote_asr_called: false,
    llm_called: false,
  };
  report.health_status = classifyBrowserMicHealth(report);
  return report;
}

function publishBrowserMicHealthReport() {
  if (!_micHealthSampleCount && _lastBrowserMicHealthReport) return _lastBrowserMicHealthReport;
  _lastBrowserMicHealthReport = browserMicHealthSnapshot();
  window.__meetingCopilotLastBrowserMicHealth = _lastBrowserMicHealthReport;
  document.body.dataset.browserMicHealth = JSON.stringify(_lastBrowserMicHealthReport);
  console.info("[workbench] workbench_browser_mic_health " + JSON.stringify(_lastBrowserMicHealthReport));
  return _lastBrowserMicHealthReport;
}

window.__meetingCopilotBrowserMicHealth = () => _lastBrowserMicHealthReport || browserMicHealthSnapshot();

function resetRealtimeUiMetrics() {
  _realtimeUiMetrics = {
    report_type: "workbench_realtime_ui_metrics",
    session_id: _recSid || currentSession || null,
    recording_started_at_epoch_ms: Date.now(),
    first_audio_active_at_epoch_ms: null,
    first_audio_active_offset_ms: null,
    first_text_visible_latency_ms: null,
    first_partial_visible_latency_ms: null,
    first_final_visible_latency_ms: null,
    first_text_after_audio_active_latency_ms: null,
    first_partial_after_audio_active_latency_ms: null,
    first_final_after_audio_active_latency_ms: null,
    partial_visible_count: 0,
    final_visible_count: 0,
    latest_partial_text_sample: "",
    latest_final_text_sample: "",
  };
  document.body.dataset.realtimeUiMetrics = JSON.stringify(_realtimeUiMetrics);
}

function recordRealtimeAudioActivityMetric(activeFrameRatio = 0, rms = 0, peak = 0) {
  if (!_realtimeUiMetrics) resetRealtimeUiMetrics();
  if (_realtimeUiMetrics.first_audio_active_at_epoch_ms !== null) return _realtimeUiMetrics;
  if (
    rms < MIC_MIN_RMS
    || peak < MIC_MIN_PEAK
    || activeFrameRatio < MIC_MIN_ACTIVE_SAMPLE_RATIO
  ) return _realtimeUiMetrics;
  const now = Date.now();
  _realtimeUiMetrics.first_audio_active_at_epoch_ms = now;
  _realtimeUiMetrics.first_audio_active_offset_ms = now - _realtimeUiMetrics.recording_started_at_epoch_ms;
  document.body.dataset.realtimeUiMetrics = JSON.stringify(_realtimeUiMetrics);
  return _realtimeUiMetrics;
}

function realtimeUiEventText(event = {}) {
  const payload = event.payload || {};
  return payload.normalized_text || payload.text || event.normalized_text || event.text || "";
}

function recordRealtimeUiEventMetric(event = {}, renderResult = {}) {
  if (!_realtimeUiMetrics) resetRealtimeUiMetrics();
  const text = realtimeUiEventText(event).trim();
  if (!text) return _realtimeUiMetrics;
  const eventType = event.event_type || "";
  if (isTranscriptEventType(eventType) && renderResult.visibleTextChanged !== true) return _realtimeUiMetrics;
  const elapsedMs = Date.now() - _realtimeUiMetrics.recording_started_at_epoch_ms;
  const streamText = $("transcript-stream")?.innerText || "";
  const visible = streamText.includes(text) || streamText.includes(text.slice(0, Math.min(text.length, 24)));
  if (!visible) return _realtimeUiMetrics;
  if (_realtimeUiMetrics.first_text_visible_latency_ms === null) {
    _realtimeUiMetrics.first_text_visible_latency_ms = elapsedMs;
    if (_realtimeUiMetrics.first_audio_active_at_epoch_ms !== null) {
      _realtimeUiMetrics.first_text_after_audio_active_latency_ms = Date.now() - _realtimeUiMetrics.first_audio_active_at_epoch_ms;
    }
  }
  if (eventType === "partial" || eventType === "transcript_partial") {
    const payload = event.payload || {};
    const segmentId = partialDraftKey(event, payload);
    const compactText = compactTranscriptText(text);
    const previous = latestPartialTextBySegment.get(segmentId) || {};
    if (previous.metricText === compactText) return _realtimeUiMetrics;
    latestPartialTextBySegment.set(segmentId, { ...previous, metricText: compactText });
    _realtimeUiMetrics.partial_visible_count++;
    _realtimeUiMetrics.latest_partial_text_sample = text.slice(0, 80);
    if (_realtimeUiMetrics.first_partial_visible_latency_ms === null) {
      _realtimeUiMetrics.first_partial_visible_latency_ms = elapsedMs;
      if (_realtimeUiMetrics.first_audio_active_at_epoch_ms !== null) {
        _realtimeUiMetrics.first_partial_after_audio_active_latency_ms = Date.now() - _realtimeUiMetrics.first_audio_active_at_epoch_ms;
      }
    }
  }
  if (eventType === "final" || eventType === "transcript_final") {
    _realtimeUiMetrics.final_visible_count++;
    _realtimeUiMetrics.latest_final_text_sample = text.slice(0, 80);
    if (_realtimeUiMetrics.first_final_visible_latency_ms === null) {
      _realtimeUiMetrics.first_final_visible_latency_ms = elapsedMs;
      if (_realtimeUiMetrics.first_audio_active_at_epoch_ms !== null) {
        _realtimeUiMetrics.first_final_after_audio_active_latency_ms = Date.now() - _realtimeUiMetrics.first_audio_active_at_epoch_ms;
      }
    }
  }
  _realtimeUiMetrics.session_id = _recSid || currentSession || _realtimeUiMetrics.session_id;
  document.body.dataset.realtimeUiMetrics = JSON.stringify(_realtimeUiMetrics);
  return _realtimeUiMetrics;
}

function realtimeUiMetricsSnapshot() {
  return _realtimeUiMetrics || {
    report_type: "workbench_realtime_ui_metrics",
    session_id: _recSid || currentSession || null,
    recording_started_at_epoch_ms: null,
    first_audio_active_at_epoch_ms: null,
    first_audio_active_offset_ms: null,
    first_text_visible_latency_ms: null,
    first_partial_visible_latency_ms: null,
    first_final_visible_latency_ms: null,
    first_text_after_audio_active_latency_ms: null,
    first_partial_after_audio_active_latency_ms: null,
    first_final_after_audio_active_latency_ms: null,
    partial_visible_count: 0,
    final_visible_count: 0,
    latest_partial_text_sample: "",
    latest_final_text_sample: "",
  };
}

window.__meetingCopilotRealtimeUiMetrics = () => realtimeUiMetricsSnapshot();

function updateMicLevelStatus(force = false) {
  const now = Date.now();
  if (!force && now - _micLastLevelStatusAt < 900) return;
  _micLastLevelStatusAt = now;
  const peak = _micPeak;
  const rms = _micRms;
  if (_micLevelFrames < 3) {
    setMicInputStatus("检测输入");
    $("sys-status").innerHTML = `<div class="empty">麦克风已连接，正在检测输入声音。</div>`;
    return;
  }
  if (peak < 0.003 && rms < 0.001) {
    setMicInputStatus("未检测到声音");
    $("sys-status").innerHTML = `<div class="empty">没有检测到麦克风声音。请检查浏览器权限、macOS 输入设备或输入音量；外放声音不一定会进入麦克风输入。</div>`;
    return;
  }
  setMicInputStatus("输入正常");
  $("sys-status").innerHTML = `<div class="empty">检测到麦克风声音，正在实时识别。</div>`;
}

function stopAudioCapture() {
  try { _micNode && _micNode.disconnect(); } catch {}
  try { _micMonitor && _micMonitor.disconnect(); } catch {}
  try { _micSource && _micSource.disconnect(); } catch {}
  try { _micStream && _micStream.getTracks().forEach((track) => track.stop()); } catch {}
  try { _micCtx && _micCtx.close && _micCtx.close(); } catch {}
  _micNode = null;
  _micMonitor = null;
  _micSource = null;
  _micStream = null;
  _micCtx = null;
  _micPeak = 0;
  _micRms = 0;
  _micLevelFrames = 0;
  _micLastLevelStatusAt = 0;
  setMicLevelMeter(0);
}

async function checkAudioDevice() {
  const status = await api("/audio/check");
  if (status) currentReadiness = status;
  return status.available !== false;
}

const MIC_PERMISSION_TIMEOUT_MS = 15_000;

function requestMicrophoneStream(constraints, timeoutMs = MIC_PERMISSION_TIMEOUT_MS) {
  const mediaDevices = window.navigator?.mediaDevices;
  if (!mediaDevices || typeof mediaDevices.getUserMedia !== "function") {
    const error = new Error("当前浏览器不支持麦克风访问");
    error.name = "MicUnavailableError";
    return Promise.reject(error);
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    let timer = null;
    const finish = (handler, value) => {
      if (settled) return;
      settled = true;
      if (timer !== null) window.clearTimeout(timer);
      handler(value);
    };
    timer = window.setTimeout(() => {
      const error = new Error("麦克风权限请求超时");
      error.name = "MicPermissionTimeoutError";
      finish(reject, error);
    }, timeoutMs);
    try {
      Promise.resolve(mediaDevices.getUserMedia(constraints)).then(
        (stream) => {
          if (settled) {
            try { stream.getTracks().forEach((track) => track.stop()); } catch {}
            return;
          }
          finish(resolve, stream);
        },
        (error) => finish(reject, error),
      );
    } catch (error) {
      finish(reject, error);
    }
  });
}

function waitForRealtimeCorrection(delayMs = 100) {
  return new Promise((resolve) => window.setTimeout(resolve, delayMs));
}

async function drainRealtimeCorrectionsOnStop(sessionId) {
  const sid = String(sessionId || "");
  if (!sid) return { drained: false, reason: "missing_session" };
  clearRealtimeCorrectionRetryTimer();
  realtimeCorrectionPending = false;
  realtimeCorrectionPendingForce = false;
  const deadlineAt = Date.now() + REALTIME_CORRECTION_DRAIN_TIMEOUT_MS;
  let partialCorrection = false;

  for (let batchIndex = 0; batchIndex < MAX_REALTIME_CORRECTION_DRAIN_BATCHES; batchIndex++) {
    while (realtimeCorrectionInFlight) {
      if (Date.now() >= deadlineAt) {
        return { drained: false, partialCorrection, reason: "drain_timeout", batchCount: batchIndex };
      }
      await waitForRealtimeCorrection();
    }
    const remainingMs = deadlineAt - Date.now();
    if (remainingMs <= 0) {
      return { drained: false, partialCorrection, reason: "drain_timeout", batchCount: batchIndex };
    }
    const result = await runRealtimeCorrectionsOnce({
      force: true,
      sessionId: sid,
      requestTimeoutMs: Math.min(10_000, remainingMs),
    });
    if (!result.ok) {
      if (result.pending || result.gate?.reason === "in_flight") {
        await waitForRealtimeCorrection(300);
        continue;
      }
      return {
        drained: false,
        partialCorrection,
        reason: Date.now() >= deadlineAt ? "drain_timeout" : (result.error || "correction_failed"),
        batchCount: batchIndex,
      };
    }
    partialCorrection = partialCorrection || result.status?.status === "partially_completed";
    if (result.gate?.reason === "no_unrevised_final") {
      return { drained: true, partialCorrection, reason: "no_unrevised_final", batchCount: batchIndex };
    }
    if (result.gate?.reason === "in_flight") {
      await waitForRealtimeCorrection(300);
      continue;
    }
    if (!result.called) {
      return { drained: true, partialCorrection, reason: result.gate?.reason || "nothing_called", batchCount: batchIndex };
    }
  }
  return { drained: false, partialCorrection, reason: "batch_limit_reached", batchCount: MAX_REALTIME_CORRECTION_DRAIN_BATCHES };
}

async function refreshRecordedSession(sid) {
  if (!sid) return;
  try {
    const ev = await api(`/live/asr/sessions/${sid}/events`);
    const hasTranscript = sessionHasTranscript(ev);
    if (!hasTranscript && restorePreservedSessionAfterRecordingFailure(sessionDegradationText(ev), sid)) {
      await loadSessionHistory();
      toast("未识别到有效语音，已保留上一场会议");
      return;
    }
    const readyMessage = "会议文字和录音已保存，AI 校正与建议会在后台继续更新。";
    applySessionEvents(sid, ev, hasTranscript ? readyMessage : sessionDegradationText(ev), { preserveExistingTranscript: true });
    setMeetingPhase(hasTranscript ? "ready" : "idle");
    if (hasTranscript) {
      preservedSessionBeforeRecording = null;
      restoredRecordingFailureSessionId = null;
    }
    await loadSessionHistory();
    toast(
      hasTranscript
        ? "会议文字已生成"
        : "未识别到有效语音",
    );
  } catch (err) {
    const message = operationErrorMessage(err, "会议整理失败");
    if (restorePreservedSessionAfterRecordingFailure(`会议已停止，但整理失败：${message}`, sid)) {
      await loadSessionHistory();
      toast("会议整理失败，已保留当前会议");
      return;
    }
    $("sys-status").innerHTML = `<div class="empty">${escapeHtml(`会议已停止，但整理失败：${message}。可以点击“刷新文字”重试。`)}</div>`;
    setMeetingPhase(currentSessionHasTranscript() ? "ready" : "idle");
    toast("会议整理失败，已保留当前文字");
  }
}

async function refreshLiveText(sid) {
  if (!sid) return;
  if (_micWs) {
    $("sys-status").innerHTML = `<div class="empty">实时文字正在自动更新。结束会议后会整理成完整文字。</div>`;
    toast("实时文字正在自动更新");
    return;
  }
  const operation = currentSessionOperation();
  try {
    const ev = await api(`/live/asr/sessions/${sid}/events`, { signal: operation.signal });
    if (!isCurrentSessionOperation(operation) || currentSession !== sid) return;
    applySessionEvents(sid, ev, "实时文字已刷新。", { preserveExistingTranscript: true });
    setMeetingPhase("ready");
    await loadSessionHistory(operation);
    if (!isCurrentSessionOperation(operation) || currentSession !== sid) return;
    toast("实时文字已刷新");
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation) || currentSession !== sid) return;
    $("sys-status").innerHTML = `<div class="empty">还没有可刷新的完整文字。录音中会自动显示实时文字，结束后会保存。</div>`;
    toast("还没有可刷新的完整文字");
  }
}

function clearAsrReadyStopTimer() {
  if (_asrReadyStopTimer !== null) {
    window.clearTimeout(_asrReadyStopTimer);
    _asrReadyStopTimer = null;
  }
}

function scheduleAsrReadyStopTimeout(sessionId) {
  clearAsrReadyStopTimer();
  _asrReadyStopTimer = window.setTimeout(() => {
    _asrReadyStopTimer = null;
    if (!_stopRequestedAfterReconnect || _recSid !== sessionId || _micAsrReady) return;
    _stopRequestedAfterReconnect = false;
    _manualStop = true;
    _reconnecting = false;
    stopAudioCapture();
    publishBrowserMicHealthReport();
    setMeetingPhase("processing");
    $("sys-status").innerHTML = "<div class=\"empty\">识别服务未在限定时间内就绪，已停止本次连接。请重试；当前文字和可恢复状态会保留。</div>";
    toast("实时识别未就绪，已停止本次连接");
    if (_micWs && _micWs.readyState === WebSocket.OPEN) {
      try {
        _micWs.close(1000, "asr_ready_timeout_client");
      } catch {
        _micWs = null;
        _recSid = null;
        void refreshRecordedSession(sessionId);
      }
      return;
    }
    _micWs = null;
    _recSid = null;
    void refreshRecordedSession(sessionId);
  }, STOP_WAIT_FOR_ASR_READY_MS);
}

function finishMicAfterAsrReady() {
  if (!_stopRequestedAfterReconnect || !_micWs || _micWs.readyState !== WebSocket.OPEN || !_micAsrReady) return false;
  clearAsrReadyStopTimer();
  _stopRequestedAfterReconnect = false;
  _manualStop = true;
  flushQueuedMicFrames();
  _micWs.send("END");
  $("sys-status").innerHTML = `<div class="empty">连接已恢复，正在整理最终文字。</div>`;
  return true;
}

function connectMicWs(sid) {
  _micWs = new WebSocket(apiWsUrl(`/live/asr/stream/ws/${sid}?audio_source=browser_live_mic`));
  _micWs.onmessage = (m) => {
    let ev; try { ev = JSON.parse(m.data); } catch { return; }
    console.log("[workbench] WS ASR 事件:", ev.event_type, ev.text?.slice(0,40));
    if (ev.event_type === "asr_starting") {
      _micAsrReady = false;
      $("sys-status").innerHTML = `<div class="empty">正在准备实时识别，请稍候。</div>`;
      setMicInputStatus("正在准备识别");
      return;
    }
    if (ev.event_type === "asr_ready") {
      _micAsrReady = ev.ready === true;
    if (_micAsrReady) {
        setMicInputStatus("已连接");
        $("s-asr").textContent = "已就绪";
        $("sys-status").innerHTML = `<div class="empty">实时识别已就绪，正在接收会议声音。</div>`;
        flushQueuedMicFrames();
        finishMicAfterAsrReady();
      }
      return;
    }
    if (ev.event_type === "provider_error" && ev.error_code === "asr_ready_timeout") {
      _micAsrReady = false;
      clearAsrReadyStopTimer();
      _manualStop = true;
      _stopRequestedAfterReconnect = false;
      stopAudioCapture();
    }
    currentEvents.push(ev);
    const renderResult = appendLiveEvent(ev);
    recordRealtimeUiEventMetric(ev, renderResult);
    if (ev.event_type === "provider_error") return;
    const transcriptCount = committedTranscriptCount();
    $("session-meta").textContent = transcriptCount
      ? `录音中 · ${transcriptCount} 段已确认`
      : "录音中 · 正在听，等待第一段文字";
  };
  _micWs.onopen = () => {
    _micAsrReady = false;
    console.log("[workbench] WS 已连接, 等待实时识别就绪...", "sampleRate:", _micInputSampleRate);
    _reconnectAttempts = 0;
    _reconnecting = false;
    if (_stopRequestedAfterReconnect) {
      $("sys-status").innerHTML = `<div class="empty">连接已恢复，正在等待识别就绪后保存剩余录音。</div>`;
      return;
    }
    $("sys-status").innerHTML = `<div class="empty">录音连接已建立，正在准备实时识别。</div>`;
  };
  _micWs.onerror = () => {
    console.log("[workbench] WS 错误, manualStop:", _manualStop);
    if (!_manualStop) {
      $("sys-status").innerHTML = `<div class="empty">录音连接出现错误，正在尝试恢复...</div>`;
    }
  };
  _micWs.onclose = async () => {
    const closedSid = _recSid;
    clearAsrReadyStopTimer();
    _micAsrReady = false;
    console.log("[workbench] WS 已关闭, sentChunks:", _micSentChunks, "manualStop:", _manualStop);
    _micWs = null;
    _reconnecting = false;

    // 用户主动关闭，正常结束流程
    if (_manualStop) {
      publishBrowserMicHealthReport();
      _recSid = null;
      _reconnectAttempts = 0;
      _stopRequestedAfterReconnect = false;
      stopAudioCapture();
      if (closedSid && closedSid === restoredRecordingFailureSessionId) {
        restoredRecordingFailureSessionId = null;
        await loadSessionHistory();
        return;
      }
      setMeetingPhase("processing");
      await refreshRecordedSession(closedSid);
      return;
    }

    // 非用户主动关闭，尝试重连
    _reconnectAttempts++;
    if (_reconnectAttempts <= MAX_RECONNECT_ATTEMPTS) {
      console.log(`[workbench] 录音连接中断，尝试重连第 ${_reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS} 次...`);
      $("sys-status").innerHTML = `<div class="empty">录音连接中断，正在重连...（第${_reconnectAttempts}次/共${MAX_RECONNECT_ATTEMPTS}次）</div>`;
      _reconnecting = true;
      setTimeout(() => {
        _reconnecting = false;
        if (_manualStop || !_recSid || _recSid !== closedSid) return;
        connectMicWs(closedSid);
      }, RECONNECT_INTERVAL_MS);
      return;
    }

    // 重连全部失败，保存已录制内容
    console.log("[workbench] 重连全部失败，保存已录制内容");
    publishBrowserMicHealthReport();
    _recSid = null;
    _reconnectAttempts = 0;
    _stopRequestedAfterReconnect = false;
    stopAudioCapture();
    $("sys-status").innerHTML = `<div class="empty">重连失败，已保存已录制内容</div>`;
    toast("重连失败，已保存已录制内容");
    if (closedSid && closedSid === restoredRecordingFailureSessionId) {
      restoredRecordingFailureSessionId = null;
      await loadSessionHistory();
      return;
    }
    setMeetingPhase("processing");
    await refreshRecordedSession(closedSid);
  };
}

async function stopMeetingRecording() {
  if (!_micWs && !_reconnecting) {
    toast("当前没有正在录音的会议");
    return false;
  }
  console.log("[workbench] 停止录音，发送 END...");
  const liveText = $("transcript-stream").innerText.trim();
  const sid = _recSid;
  const openSocket = _micWs && _micWs.readyState === WebSocket.OPEN;
  const waitingForSocket = !openSocket && Boolean(_micWs || _reconnecting);
  flushPendingPcm();
  if (openSocket && !_micAsrReady) {
    _stopRequestedAfterReconnect = true;
    _manualStop = false;
    publishBrowserMicHealthReport();
    stopAudioCapture();
    setMeetingPhase("processing");
    $("sys-status").innerHTML = `<div class="empty">正在等待实时识别就绪；就绪后会补发已缓存音频，再结束会议。</div>`;
    scheduleAsrReadyStopTimeout(sid);
    toast("正在等待识别就绪并保存录音");
    return true;
  }
  if (waitingForSocket) {
    _stopRequestedAfterReconnect = true;
    _manualStop = false;
    publishBrowserMicHealthReport();
    stopAudioCapture();
    setMeetingPhase("processing");
    $("sys-status").innerHTML = `<div class="empty">正在恢复录音连接；连接后会先补发已缓存音频，再结束会议。</div>`;
    scheduleAsrReadyStopTimeout(sid);
    toast("正在恢复连接并保存剩余录音");
    return true;
  }
  _manualStop = true;
  _reconnecting = false;
  if (openSocket) {
    _micWs.send("END");
  }
  publishBrowserMicHealthReport();
  stopAudioCapture();
  setMeetingPhase("processing");
  if (!liveText) {
    renderCanonicalTranscriptEmptyState("正在整理最终文字，请稍等。");
  }
  $("sys-status").innerHTML = `<div class="empty">正在整理最终文字，当前实时文字会保留在这里。</div>`;
  toast("会议结束，正在整理文字");
  if (!openSocket) {
    _micWs = null;
    _recSid = null;
    _reconnectAttempts = 0;
    if (sid && sid === restoredRecordingFailureSessionId) {
      restoredRecordingFailureSessionId = null;
      await loadSessionHistory();
    } else if (sid) {
      await refreshRecordedSession(sid);
    }
  }
  return true;
}

$("btn-record").addEventListener("click", async () => {
  const operation = beginSessionOperation();
  try {
    // 会前设备检查
    console.log("[workbench] 检查音频设备...");
    const deviceAvailable = await checkAudioDevice();
    if (!deviceAvailable) {
      toast("音频设备不可用，请检查麦克风连接");
      $("sys-status").innerHTML = `<div class="empty">音频设备不可用，请检查麦克风连接后重试。</div>`;
      return;
    }

    console.log("[workbench] 请求麦克风权限...");
    setMicInputStatus("请求权限");
    $("sys-status").innerHTML = `<div class="empty">正在请求麦克风权限，请在浏览器提示中允许访问；如果长时间没有提示，会自动报告超时。</div>`;
    if (currentReadiness && !currentReadiness.realtime_asr_available) {
      $("sys-status").innerHTML = `<div class="empty">实时识别不可用，请先导入录音或检查本地语音识别服务。</div>`;
      toast("实时识别不可用，请先导入录音");
      updateRecordButtonReadiness("idle");
      return;
    }
    const AudioContextImpl = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextImpl) throw new Error("当前浏览器不支持 AudioContext");
    const audioConstraints = {
      sampleRate: 16000,
      channelCount: 1,
      ...(selectedMicrophoneDeviceId ? { deviceId: { exact: selectedMicrophoneDeviceId } } : {}),
    };
    const stream = await requestMicrophoneStream({ audio: audioConstraints });
    if (!isCurrentSessionOperation(operation)) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }
    await refreshMicrophoneDevices();
    const activeDeviceId = stream.getAudioTracks?.()[0]?.getSettings?.().deviceId;
    if (activeDeviceId) {
      selectedMicrophoneDeviceId = activeDeviceId;
      const deviceSelect = $("mic-device-select");
      if (deviceSelect && [...deviceSelect.options].some((option) => option.value === activeDeviceId)) {
        deviceSelect.value = activeDeviceId;
      }
    }
    console.log("[workbench] 麦克风已授权, 创建 WS...");
    _micStream = stream;
    _micCtx = new AudioContextImpl({ sampleRate: 16000 });
    await _micCtx.resume();
    if (!isCurrentSessionOperation(operation)) {
      stopAudioCapture();
      return;
    }
    _micInputSampleRate = _micCtx.sampleRate || 16000;
    _micSentChunks = 0;
    _micPendingSamples = new Float32Array(0);
    _micUnsentFrames = [];
    _micDroppedFrameCount = 0;
    _micAsrReady = false;
    clearAsrReadyStopTimer();
    _stopRequestedAfterReconnect = false;
    delete document.body.dataset.audioQueueOverflow;
    _micPeak = 0;
    _micRms = 0;
    _micLevelFrames = 0;
    _micLastLevelStatusAt = 0;
    resetMicHealthStats();
    _recSid = "rec_" + Date.now().toString(36);
    resetRealtimeUiMetrics();
    preserveSessionBeforeRecording();
    if (!startRecordingDraftSession(_recSid, { sessionOperation: operation })) return;
    setMeetingPhase("recording");
    _micSource = _micCtx.createMediaStreamSource(stream);
    _micMonitor = _micCtx.createGain();
    _micMonitor.gain.value = 0;
    _micNode = _micCtx.createScriptProcessor(4096, 1, 1);
    _micNode.onaudioprocess = (e) => {
      if (_recordingPaused) return;
      const d = e.inputBuffer.getChannelData(0);
      updateMicLevel(d);
      updateMicLevelStatus();
      sendPcm16kFrame(new Float32Array(d));
    };
    _micSource.connect(_micNode);
    _micNode.connect(_micMonitor);
    _micMonitor.connect(_micCtx.destination);
    _manualStop = false;
    _reconnectAttempts = 0;
    connectMicWs(_recSid);
  } catch (err) {
    if (err?.name === "AbortError" || !isCurrentSessionOperation(operation)) return;
    const failedSessionId = _recSid;
    publishBrowserMicHealthReport();
    stopAudioCapture();
    _recSid = null;
    const permissionMessage = err?.name === "MicPermissionTimeoutError"
      ? "麦克风权限请求超时，请检查浏览器权限后重试。"
      : err?.name === "NotAllowedError"
        ? "麦克风权限被拒绝，请在浏览器和 macOS 设置中允许访问。"
        : `麦克风或录音失败: ${err.message}`;
    $("sys-status").innerHTML = `<div class="empty">${escapeHtml(permissionMessage)}</div>`;
    setMicInputStatus(err?.name === "MicPermissionTimeoutError" ? "权限超时" : "未使用");
    toast(permissionMessage);
    if (!restorePreservedSessionAfterRecordingFailure(permissionMessage, failedSessionId)) {
      resetSessionView(permissionMessage, { sessionOperation: operation });
    }
  }
});

$("btn-live").addEventListener("click", async () => {
  if (!currentSession) return toast("请先开始会议或导入录音");
  await refreshLiveText(currentSession);
});

$("btn-stop").addEventListener("click", () => {
  void stopMeetingRecording();
});

function togglePauseRecording() {
  if (currentMeetingPhase !== "recording") return;
  const btnPause = $("btn-pause");
  if (!_recordingPaused) {
    _recordingPaused = true;
    recordingPausedAtMs = Date.now();
    setMicInputStatus("已暂停");
    if (btnPause) btnPause.textContent = "继续";
    updateRecordingDuration();
    toast("录音已暂停");
  } else {
    if (recordingPausedAtMs !== null) {
      recordingStartedAtMs += (Date.now() - recordingPausedAtMs);
      recordingPausedAtMs = null;
    }
    _recordingPaused = false;
    setMicInputStatus("已连接");
    if (btnPause) btnPause.textContent = "暂停";
    updateRecordingDuration();
    toast("录音已继续");
  }
}

$("btn-pause").addEventListener("click", togglePauseRecording);

$("btn-organize").addEventListener("click", async () => {
  if (!currentSession) return toast("请先开始会议或导入录音");
  if (!currentSessionHasTranscript()) return toast("还没有会议文字可整理");
  await organizeCurrentSession();
});

async function generateSuggestionCards({ showToast = true, maxCandidates = null } = {}) {
  if (!currentSession) return toast("请先开始会议或导入录音");
  const sid = currentSession;
  try {
    if (autoSuggestionInFlight) await waitForAutoSuggestionIdle();
    if (currentSession !== sid) return { ok: false, stale: true };
    $("btn-cards").disabled = true;
    const body = await api(`${derivationBasePath(sid)}/llm-execution-runs`, { method: "POST", body: JSON.stringify(enabledLlmRequestBody({ maxCandidates })) });
    if (currentSession !== sid) return { ok: false, stale: true };
    if (body.derivation_blocked) {
      const message = derivationBlockedMessage(body);
      renderRealCards([]);
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      await loadSessionHistory();
      if (showToast) toast(message);
      return { ok: false, blocked: true, message, count: 0 };
    }
    const cardReport = renderRealCards(body.runs || []);
    await loadSessionHistory();
    if (body.degraded || cardReport.visibleCount === 0) {
      const message = body.message || "本轮没有生成可展示的正式 AI 建议，文字稿已保留。";
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      if (showToast) toast(message);
      return { ok: false, degraded: Boolean(body.degraded), message, count: 0 };
    }
    if (showToast) toast(`AI 建议已更新，当前 ${cardReport.visibleCount} 条`);
    return { ok: true, count: cardReport.visibleCount, generatedRunCount: body.run_count || 0 };
  } catch (err) {
    if (err.status === 422) {
      $("s-llm").textContent = "暂不可用";
      renderSuggestionFailureState("AI 建议暂不可用，请检查设置后重试。");
      if (showToast) toast("AI 分析暂不可用，请检查设置");
    } else {
      renderSuggestionFailureState("AI 建议生成失败，请稍后重试。");
      if (showToast) toast("生成失败: " + err.message);
    }
    return { ok: false, error: err.message || "生成会议建议失败" };
  } finally {
    syncActionAvailability(currentMeetingPhase);
  }
}

$("btn-cards").addEventListener("click", async () => {
  await generateSuggestionCards();
});

async function generateApproachCards({ showToast = true } = {}) {
  if (!currentSession) return toast("请先开始会议或导入录音");
  const sid = currentSession;
  try {
    $("btn-approach").disabled = true;
    const body = await api(`${derivationBasePath(sid)}/approach-cards`, { method: "POST", body: JSON.stringify(enabledLlmRequestBody()) });
    if (currentSession !== sid) return { ok: false, stale: true };
    if (body.derivation_blocked) {
      const message = derivationBlockedMessage(body);
      renderApproachCards([]);
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      await loadSessionHistory();
      if (showToast) toast(message);
      return { ok: false, blocked: true, message, count: 0 };
    }
    renderApproachCards(body.approach_cards || []);
    await loadSessionHistory();
    if (body.degraded || !(body.count > 0)) {
      const message = body.message || "方案分析暂未生成，文字稿已保留。";
      $("sys-status").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      if (showToast) toast(message);
      return { ok: false, degraded: Boolean(body.degraded), message, count: 0 };
    }
    if (showToast) toast(`生成 ${body.count} 条方案分析`);
    return { ok: true, count: body.count || 0 };
  } catch (err) {
    if (err.status === 422) {
      if (showToast) toast("AI 分析暂不可用，请检查设置");
    } else if (showToast) {
      toast("生成失败: " + err.message);
    }
    return { ok: false, error: err.message || "生成方案分析失败" };
  } finally {
    syncActionAvailability(currentMeetingPhase);
  }
}

$("btn-approach").addEventListener("click", async () => {
  await generateApproachCards();
});

async function generateMinutes({ showToast = true } = {}) {
  if (!currentSession) return toast("请先开始会议或导入录音");
  const sid = currentSession;
  const previousMinutes = currentMinutes;
  try {
    $("btn-minutes").disabled = true;
    $("minutes-panel").innerHTML = `<div class="empty">正在生成会后复盘...</div>`;
    const body = await api(`${derivationBasePath(sid)}/minutes`, { method: "POST", body: JSON.stringify(enabledLlmRequestBody()) });
    if (currentSession !== sid) return { ok: false, stale: true };
    if (body.derivation_blocked) {
      const message = derivationBlockedMessage(body);
      if (previousMinutes) renderMinutes(previousMinutes);
      else $("minutes-panel").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      await loadSessionHistory();
      if (showToast) toast(message);
      return { ok: false, blocked: true, message, count: 0 };
    }
    if (body.degraded || !String(body.minutes_md || "").trim()) {
      const message = body.message || "会后复盘未生成，文字稿和录音已保留，请稍后重试。";
      if (previousMinutes) renderMinutes(previousMinutes);
      else $("minutes-panel").innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
      await loadSessionHistory();
      if (showToast) toast(message);
      return { ok: false, degraded: Boolean(body.degraded), message, count: 0 };
    }
    renderMinutes(body.minutes_md || "");
    await loadSessionHistory();
    if (showToast) toast("会后复盘已生成");
    return { ok: true, count: body.minutes_md ? 1 : 0 };
  } catch (err) {
    if (err.status === 422) {
      if (previousMinutes) renderMinutes(previousMinutes);
      else $("minutes-panel").innerHTML = `<div class="empty">AI 分析暂不可用，请检查设置。</div>`;
      if (showToast) toast("AI 分析暂不可用，请检查设置");
    } else {
      if (previousMinutes) renderMinutes(previousMinutes);
      else $("minutes-panel").innerHTML = `<div class="empty">会后复盘生成失败：${escapeHtml(err.message)}</div>`;
      if (showToast) toast("会后复盘生成失败");
    }
    return { ok: false, error: err.message || "生成会后复盘失败" };
  } finally {
    syncActionAvailability(currentMeetingPhase);
  }
}

async function organizeCurrentSession() {
  const sid = currentSession;
  if (!sid) return toast("请先开始会议或导入录音");
  if (!currentSessionHasTranscript()) return toast("还没有会议文字可整理");
  const buttons = ["btn-organize", "btn-cards", "btn-approach", "btn-minutes"];
  buttons.forEach((id) => { $(id).disabled = true; });
  $("sys-status").innerHTML = `<div class="empty">正在整理会议：生成正式建议、方案分析和会后复盘。</div>`;
  const results = [];
  try {
    results.push(["正式建议", await generateSuggestionCards({ showToast: false, maxCandidates: ORGANIZE_FAST_SUGGESTION_BUDGET })]);
    if (currentSession !== sid) return;
    results.push(["方案分析", await generateApproachCards({ showToast: false })]);
    if (currentSession !== sid) return;
    results.push(["会后复盘", await generateMinutes({ showToast: false })]);
    if (currentSession !== sid) return;
    const failed = results.filter(([, result]) => !result?.ok);
    if (failed.length) {
      const blocked = failed.find(([, result]) => result?.blocked);
      if (blocked) {
        const message = blocked[1].message || ASR_SEMANTIC_QUALITY_MESSAGE;
        $("sys-status").innerHTML = `<div class="empty">会议整理完成，但识别质量不足：${escapeHtml(message)} 文字稿和录音已保留，可换清晰会议音频后重试。</div>`;
        toast("识别质量不足，先不生成正式建议");
        return;
      }
      const names = failed.map(([name]) => name).join("、");
      $("sys-status").innerHTML = `<div class="empty">会议整理完成，但 ${escapeHtml(names)} 暂未生成。AI 分析暂不可用，文字稿已保留，可稍后重试。</div>`;
      toast(`整理完成，部分失败：${names}`);
    } else {
      $("sys-status").innerHTML = `<div class="empty">会议整理完成：正式建议、方案分析和会后复盘已更新。</div>`;
      toast("会议整理完成");
    }
  } finally {
    if (currentSession === sid) buttons.forEach((id) => { $(id).disabled = false; });
    syncActionAvailability(currentMeetingPhase);
  }
}

$("btn-minutes").addEventListener("click", async () => {
  await generateMinutes();
});

const btnHistory = $("btn-history");
if (btnHistory) {
  btnHistory.addEventListener("click", async () => {
    openHistoryModal();
    await loadSessionHistoryForModal();
  });
}

const btnAutoSuggestionToggle = $("btn-auto-suggestion-toggle");
if (btnAutoSuggestionToggle) {
  btnAutoSuggestionToggle.addEventListener("click", toggleAutoSuggestion);
}

async function bootstrapWorkbench() {
  initDemoTools();
  bindCandidateFocusFilters();
  bindMeetingOverviewJumps();
  bindTranscriptScrollFollow();
  await initDesktopRuntimeProbe();
  setMeetingPhase("idle");
  await loadAudioCheck();
  await loadSessionHistory();
  await restoreLatestRealSession();
  await writePackagedBackendApiProbe();
  await runPackagedSameChainProbe();
}

window.meetingCopilotClient = Object.freeze({
  apiUrl,
  notify: toast,
});

// ===== 键盘快捷键 =====
document.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

  if (e.code === "Space" && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
    e.preventDefault();
    if (_micWs || _reconnecting) {
      $("btn-stop").click();
    } else if (!$("btn-record").disabled) {
      $("btn-record").click();
    }
  }

  if ((e.ctrlKey || e.metaKey) && e.code === "Enter") {
    e.preventDefault();
    const btn = $("btn-minutes");
    if (btn && !btn.disabled) btn.click();
  }

  if (e.code === "Escape") {
    const hm = $("history-modal");
    if (hm && !hm.hidden) { hm.hidden = true; return; }
    const sm = $("settings-modal");
    if (sm && !sm.hidden) { sm.hidden = true; return; }
  }

  if ((e.ctrlKey || e.metaKey) && e.code === "KeyH") {
    e.preventDefault();
    $("btn-history").click();
  }
});

bootstrapWorkbench();

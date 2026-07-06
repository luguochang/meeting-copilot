let currentSessionId = "";
let currentFixtureId = "";
let currentEventMode = "replay";
let currentSnapshot = null;
let currentEvaluationSummary = null;
let liveEventSource = null;
let liveStreamEvents = [];
let liveStreamSourceMeta = null;
let snapshotLookup = null;
let liveSessionPending = false;
let sessionLoadToken = 0;
let desktopMicAdapterContractState = null;
let desktopMicAdapterNoopInvocationState = null;
let desktopTauriNoopRunResultCollectorState = null;
let desktopTauriNoopRunResultValidationState = null;
let macLocalShadowMvpState = null;
let mainlineTrialFeedbackExportClosureState = null;

const liveEventTypes = [
  "transcript_partial",
  "transcript_final",
  "transcript_revision",
  "state_event",
  "scheduler_event",
  "suggestion_candidate_event",
  "llm_request_draft_event",
  "llm_schema_result",
  "suggestion_invalidated",
  "suggestion_silenced",
  "suggestion_card",
  "provider_error",
  "evaluation_summary",
];

const desktopMicAdapterNoopCommands = [
  ["mic_adapter.prepare", "mic_adapter_prepare"],
  ["mic_adapter.status", "mic_adapter_status"],
  ["mic_adapter.start", "mic_adapter_start"],
  ["mic_adapter.pause", "mic_adapter_pause"],
  ["mic_adapter.resume", "mic_adapter_resume"],
  ["mic_adapter.stop", "mic_adapter_stop"],
  ["mic_adapter.delete_audio_chunks", "mic_adapter_delete_audio_chunks"],
];

const desktopTauriNoopRunCommands = [
  ["runtime.get_status", "runtime_get_status"],
  ["session.prepare", "session_prepare"],
  ["asr_worker.health", "asr_worker_health"],
  ...desktopMicAdapterNoopCommands,
];

const macLocalShadowMvpDemoId = "mac_local_shadow_mvp";
const macLocalShadowMvpClosureStatus = "closed_to_no_llm_request_draft_and_readiness_blockers";
const realisticMeetingSimulationPackId = "realistic_meeting_simulation_pack";
const mainlineAsrBlockedTrialId = "mainline_asr_blocked_trial";
const mainlineAsrEventArtifactTrialId = "mainline_asr_event_artifact_trial";
const mainlineAsrBlockedTrialNextAction = "continue_pc_product_flow_keep_real_mic_blocked";
const mainlineAsrEventArtifactPath = "artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json";
const mainlineAsrEventArtifactProvider = "local_artifact_asr";

const fixtureSelect = document.getElementById("fixture-select");
const loadFixtureButton = document.getElementById("load-fixture-button");
const macLocalShadowMvpButton = document.getElementById("mac-local-shadow-mvp-button");
const realisticMeetingSimulationButton = document.getElementById("realistic-meeting-simulation-button");
const longRealisticMeetingSimulationButton = document.getElementById("long-realistic-meeting-simulation-button");
const mainlineAsrBlockedTrialButton = document.getElementById("mainline-asr-blocked-trial-button");
const mainlineAsrEventArtifactTrialButton = document.getElementById("mainline-asr-event-artifact-trial-button");
const mainlineFeedbackExportClosureButton = document.getElementById("mainline-feedback-export-closure-button");
const exportReportButton = document.getElementById("export-report-button");
const deleteSessionButton = document.getElementById("delete-session-button");
const replayModeButton = document.getElementById("event-mode-replay");
const liveMockModeButton = document.getElementById("event-mode-live-mock");
const liveAsrModeButton = document.getElementById("event-mode-live-asr");
const shadowReportFeedbackForm = document.getElementById("shadow-report-feedback-form");
const shadowReportFeedbackCandidateReport = document.getElementById("shadow-report-feedback-candidate-report");
const shadowReportFeedbackEntries = document.getElementById("shadow-report-feedback-entries");
const shadowReportFeedbackResult = document.getElementById("shadow-report-feedback-result");
const localShadowPreviewReleaseRefreshButton = document.getElementById("local-shadow-preview-release-refresh");
const toast = document.getElementById("toast");

document.addEventListener("DOMContentLoaded", () => {
  loadLocalShadowPreviewReleaseReadiness();
  loadDesktopShellReadiness();
  loadDesktopRuntimeBoundary();
  loadDesktopNativeBridgeContract();
  loadDesktopNativeRuntime();
  loadDesktopAsrHandoffDryRunReadiness();
  loadDesktopMicAdapterContractReadiness();
  loadDesktopRealMicShadowTestReadiness();
  loadDesktopMicAdapterNoopInvocation();
  loadDesktopTauriNoopRunResultCollector();
  loadFixtures();
  loadFixtureButton.addEventListener("click", loadSelectedFixture);
  macLocalShadowMvpButton.addEventListener("click", loadMacLocalShadowMvpDemo);
  realisticMeetingSimulationButton.addEventListener("click", loadRealisticMeetingSimulationPack);
  longRealisticMeetingSimulationButton.addEventListener("click", loadLongRealisticMeetingSimulationPack);
  mainlineAsrBlockedTrialButton.addEventListener("click", loadMainlineAsrBlockedTrial);
  mainlineAsrEventArtifactTrialButton.addEventListener("click", loadMainlineAsrEventArtifactTrial);
  mainlineFeedbackExportClosureButton.addEventListener("click", loadMainlineTrialFeedbackExportClosure);
  localShadowPreviewReleaseRefreshButton?.addEventListener("click", loadLocalShadowPreviewReleaseReadiness);
  exportReportButton.addEventListener("click", loadReport);
  deleteSessionButton.addEventListener("click", deleteCurrentSession);
  replayModeButton.addEventListener("click", () => setEventMode("replay"));
  liveMockModeButton.addEventListener("click", () => setEventMode("live_mock"));
  liveAsrModeButton.addEventListener("click", () => setEventMode("live_asr"));
  bindShadowReportFeedbackForm();
});

async function loadFixtures() {
  try {
    const data = await requestJson("/demo/fixtures");
    fixtureSelect.innerHTML = "";
    data.fixtures.forEach((fixture) => {
      const option = document.createElement("option");
      option.value = fixture.id;
      option.textContent = `${fixture.title} · ${fixture.scenario_type}`;
      fixtureSelect.append(option);
    });
  } catch (error) {
    showToast(error.message);
  }
}

async function loadSelectedFixture() {
  const fixtureId = fixtureSelect.value;
  if (!fixtureId) {
    return;
  }
  closeLiveEventStream();
  const loadToken = ++sessionLoadToken;
  liveSessionPending = false;
  currentEventMode = "replay";
  syncEventModeButtons();
  try {
    const sessionId = `workbench_${fixtureId}_${Date.now()}`;
    const data = await requestJson(`/demo/fixtures/${fixtureId}/sessions`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: sessionId}),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "replay") {
      return;
    }
    currentSessionId = data.snapshot.session_id;
    currentFixtureId = fixtureId;
    currentSnapshot = data.snapshot;
    currentEvaluationSummary = data.evaluation_summary || null;
    snapshotLookup = null;
    renderSnapshot(currentSnapshot);
    renderCardLifecycleReadinessEmpty();
    await loadEventStream();
    await loadReport();
    showToast(`已加载 ${data.metadata.fixture_id}`);
  } catch (error) {
    showToast(error.message);
  }
}

async function loadReport() {
  if (!currentSessionId) {
    return;
  }
  if (liveSessionPending) {
    return;
  }
  if (currentEventMode === "live_asr") {
    return loadLiveAsrDraft();
  }
  const sessionId = currentSessionId;
  const mode = currentEventMode;
  try {
    const response = await fetch(`/sessions/${sessionId}/report.md`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const text = await response.text();
    if (currentSessionId !== sessionId || currentEventMode !== mode) {
      return;
    }
    document.getElementById("report-panel").textContent = text;
  } catch (error) {
    showToast(error.message);
  }
}

async function loadLiveAsrDraft() {
  if (!currentSessionId) {
    return;
  }
  if (liveSessionPending) {
    return;
  }
  const sessionId = currentSessionId;
  const mode = currentEventMode;
  try {
    const response = await fetch(`/live/asr/sessions/${sessionId}/draft.md`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const text = await response.text();
    if (currentSessionId !== sessionId || currentEventMode !== mode) {
      return;
    }
    document.getElementById("report-panel").textContent = text;
  } catch (error) {
    showToast(error.message);
  }
}

async function deleteCurrentSession() {
  if (!currentSessionId) {
    return;
  }
  try {
    closeLiveEventStream();
    const response = await fetch(`/sessions/${currentSessionId}`, {method: "DELETE"});
    if (!response.ok) {
      throw new Error(await response.text());
    }
    currentSessionId = "";
    currentFixtureId = "";
    currentEventMode = "replay";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    showToast("会话已删除");
  } catch (error) {
    showToast(error.message);
  }
}

function renderSnapshot(snapshot) {
  document.getElementById("summary-title").textContent = snapshot.session_id;
  document.getElementById("meeting-summary").textContent = snapshot.summary || "无摘要";
  renderQuality(snapshot.quality);
  renderEvaluationSummary(currentEvaluationSummary);
  renderStateBoard(snapshot.states, snapshot.transcript.evidence_spans);
  renderSuggestionCards(snapshot.suggestion_cards, snapshot.transcript.evidence_spans);
  renderEvidence(snapshot.transcript.evidence_spans);
  renderTranscript(snapshot.transcript);
}

function renderEmpty() {
  document.getElementById("summary-title").textContent = "等待加载会议样本";
  document.getElementById("meeting-summary").textContent = "请选择一个本地 demo fixture。";
  document.getElementById("quality-panel").innerHTML = "";
  document.getElementById("evaluation-panel").innerHTML = "";
  document.getElementById("state-board").innerHTML = "";
  document.getElementById("suggestion-list").innerHTML = "";
  document.getElementById("evidence-panel").innerHTML = "";
  document.getElementById("transcript-panel").innerHTML = "";
  document.getElementById("event-stream-panel").innerHTML = "";
  document.getElementById("report-panel").textContent = "";
  renderLocalShadowPreviewReleaseReadinessEmpty();
  renderMacLocalShadowMvpEmpty();
  renderMainlineTrialFeedbackExportClosureEmpty();
  renderCardLifecycleReadinessEmpty();
  renderDesktopShellReadinessEmpty();
  renderDesktopRuntimeBoundaryEmpty();
  renderDesktopNativeBridgeContractEmpty();
  renderDesktopNativeRuntimeEmpty();
  renderDesktopAsrHandoffDryRunReadinessEmpty();
  renderDesktopMicAdapterContractReadinessEmpty();
  renderDesktopRealMicShadowTestReadinessEmpty();
  loadLocalShadowPreviewReleaseReadiness();
  loadDesktopShellReadiness();
  loadDesktopRuntimeBoundary();
  loadDesktopNativeBridgeContract();
  loadDesktopNativeRuntime();
  loadDesktopAsrHandoffDryRunReadiness();
  loadDesktopMicAdapterContractReadiness();
  loadDesktopRealMicShadowTestReadiness();
  loadDesktopMicAdapterNoopInvocation();
  syncEventModeButtons();
}

async function setEventMode(mode) {
  if (mode === currentEventMode) {
    return;
  }
  currentEventMode = mode;
  syncEventModeButtons();
  if (mode === "live_mock") {
    await loadLiveMockSession();
    return;
  }
  if (mode === "live_asr") {
    await loadLiveAsrSession();
    return;
  }
  await loadSelectedFixture();
}

async function loadLiveMockSession() {
  const fixtureId = currentFixtureId || fixtureSelect.value;
  if (!fixtureId) {
    return;
  }
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    const sessionId = `live_${fixtureId}_${Date.now()}`;
    const data = await requestJson(`/live/mock/fixtures/${fixtureId}/sessions`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: sessionId}),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_mock") {
      return;
    }
    currentSessionId = data.snapshot.session_id;
    currentFixtureId = fixtureId;
    snapshotLookup = createSnapshotLookup(data.snapshot);
    currentSnapshot = createLiveIncrementalSnapshot(data.snapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty("等待 Live ASR 事件流结束后生成 readiness summary。");
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast(`已加载 Live Mock ${fixtureId}`);
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function loadLiveAsrSession() {
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    currentSessionId = "";
    currentFixtureId = "";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    const sessionId = `live_asr_local_${Date.now()}`;
    const data = await requestJson("/live/asr/mock/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        session_id: sessionId,
        provider: "local_mock_asr",
        streaming_events: localAsrStreamingEvents(),
      }),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_asr") {
      return;
    }
    currentSessionId = data.session_id;
    currentFixtureId = "";
    currentSnapshot = emptyLiveSnapshot(currentSessionId, "Local ASR event source skeleton");
    snapshotLookup = createSnapshotLookup(currentSnapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty();
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast("已加载 Live ASR 本地事件源");
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function loadMacLocalShadowMvpDemo() {
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    currentEventMode = "live_asr";
    syncEventModeButtons();
    currentSessionId = "";
    currentFixtureId = "";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    const sessionId = `mac_shadow_mvp_${Date.now()}`;
    const data = await requestJson("/desktop/mac-local-shadow-mvp-demo/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: sessionId}),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_asr") {
      return;
    }
    macLocalShadowMvpState = data;
    currentSessionId = data.session_id;
    currentFixtureId = "";
    currentSnapshot = emptyLiveSnapshot(
      currentSessionId,
      "Mac Local Shadow MVP synthetic stream",
    );
    snapshotLookup = createSnapshotLookup(currentSnapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    renderMacLocalShadowMvp(data);
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty();
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast("已加载 Mac Local Shadow MVP");
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function loadRealisticMeetingSimulationPack() {
  await loadRealisticMeetingSimulationPackProfile({
    profileId: "standard",
    sessionPrefix: "realistic_sim",
    summaryLabel: "Realistic synthetic Chinese technical meeting",
    toastText: "已加载真实感模拟会议",
  });
}

async function loadLongRealisticMeetingSimulationPack() {
  await loadRealisticMeetingSimulationPackProfile({
    profileId: "long_shadow",
    sessionPrefix: "long_realistic_sim",
    summaryLabel: "Long realistic synthetic Chinese technical meeting",
    toastText: "已加载长会模拟",
  });
}

async function loadMainlineAsrBlockedTrial() {
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    currentEventMode = "live_asr";
    syncEventModeButtons();
    currentSessionId = "";
    currentFixtureId = "";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    const sessionId = `mainline_asr_blocked_trial_${Date.now()}`;
    const data = await requestJson("/desktop/mainline-asr-blocked-trial/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: sessionId}),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_asr") {
      return;
    }
    macLocalShadowMvpState = data;
    currentSessionId = data.session_id;
    currentFixtureId = "";
    currentSnapshot = emptyLiveSnapshot(
      currentSessionId,
      "Mainline ASR blocked product trial",
    );
    snapshotLookup = createSnapshotLookup(currentSnapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    renderMainlineAsrBlockedTrial(data);
    renderMainlineTrialFeedbackExportClosureEmpty("等待主线试运行事件流结束后生成闭环预览。");
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty();
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast("已加载主线试运行");
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function loadMainlineAsrEventArtifactTrial() {
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    currentEventMode = "live_asr";
    syncEventModeButtons();
    currentSessionId = "";
    currentFixtureId = "";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    const sessionId = `mainline_asr_event_artifact_trial_${Date.now()}`;
    const data = await requestJson("/desktop/mainline-asr-event-artifact-trial/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        session_id: sessionId,
        provider: mainlineAsrEventArtifactProvider,
        events_path: mainlineAsrEventArtifactPath,
      }),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_asr") {
      return;
    }
    macLocalShadowMvpState = data;
    currentSessionId = data.session_id;
    currentFixtureId = "";
    currentSnapshot = emptyLiveSnapshot(
      currentSessionId,
      "Mainline ASR event artifact product trial",
    );
    snapshotLookup = createSnapshotLookup(currentSnapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    renderMainlineAsrBlockedTrial(data);
    renderMainlineTrialFeedbackExportClosureEmpty("等待工件主线事件流结束后生成闭环预览。");
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty();
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast("已加载工件主线试运行");
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function loadRealisticMeetingSimulationPackProfile({
  profileId,
  sessionPrefix,
  summaryLabel,
  toastText,
}) {
  try {
    closeLiveEventStream();
    const loadToken = ++sessionLoadToken;
    liveSessionPending = true;
    currentEventMode = "live_asr";
    syncEventModeButtons();
    currentSessionId = "";
    currentFixtureId = "";
    currentSnapshot = null;
    currentEvaluationSummary = null;
    snapshotLookup = null;
    renderEmpty();
    const sessionId = `${sessionPrefix}_${Date.now()}`;
    const requestBody = profileId === "long_shadow"
      ? {session_id: sessionId, profile: "long_shadow"}
      : {session_id: sessionId};
    const data = await requestJson("/desktop/realistic-meeting-simulation-pack/sessions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(requestBody),
    });
    if (loadToken !== sessionLoadToken || currentEventMode !== "live_asr") {
      return;
    }
    macLocalShadowMvpState = data;
    currentSessionId = data.session_id;
    currentFixtureId = "";
    currentSnapshot = emptyLiveSnapshot(
      currentSessionId,
      summaryLabel,
    );
    snapshotLookup = createSnapshotLookup(currentSnapshot);
    currentEvaluationSummary = null;
    renderSnapshot(currentSnapshot);
    renderRealisticMeetingSimulationPack(data);
    document.getElementById("report-panel").textContent = "";
    renderCardLifecycleReadinessEmpty();
    liveSessionPending = false;
    await connectLiveEventStream(data.event_source || {});
    showToast(toastText);
  } catch (error) {
    showToast(error.message);
  } finally {
    liveSessionPending = false;
  }
}

async function connectLiveEventStream(sourceMeta = {}) {
  if (!currentSessionId) {
    return;
  }
  closeLiveEventStream();
  liveStreamEvents = [];
  liveStreamSourceMeta = {
    source: sourceMeta.source || "live_mock_stream",
    trace_kind: sourceMeta.trace_kind || "live_event",
  };
  renderEventStream(liveStreamEvents, liveStreamSourceMeta);
  if (!window.EventSource) {
    const events = await loadEventStream();
    applyTerminalLiveEventSideEffects(events || []);
    return;
  }
  window.__meetingCopilotLiveStreamClosed = false;
  const source = new EventSource(liveEventStreamSseUrl());
  liveEventSource = source;
  const handleLiveEventMessage = (message) => {
    if (liveEventSource !== source || !isLiveEventMode()) {
      return;
    }
    let event;
    try {
      event = JSON.parse(message.data);
    } catch {
      showToast("Live 事件解析失败");
      return;
    }
    liveStreamEvents.push(event);
    applyLiveEventToSnapshot(event);
    renderEventStream(liveStreamEvents, liveStreamSourceMeta || {});
    syncEvaluationFromEvents(liveStreamEvents);
    if (event.event_type === "evaluation_summary") {
      closeLiveEventStream();
      applyTerminalLiveEventSideEffects([event]);
    }
  };
  liveEventTypes.forEach((eventType) => {
    liveEventSource.addEventListener(eventType, handleLiveEventMessage);
  });
  liveEventSource.onerror = () => {
    if (liveEventSource !== source) {
      return;
    }
    const hasEvaluationSummary = liveStreamEvents.some((event) => event.event_type === "evaluation_summary");
    closeLiveEventStream();
    if (!hasEvaluationSummary) {
      loadEventStream().then((events) => applyTerminalLiveEventSideEffects(events || []));
    }
  };
}

function applyTerminalLiveEventSideEffects(events = []) {
  const hasEvaluationSummary = events.some((event) => event.event_type === "evaluation_summary");
  if (!hasEvaluationSummary) {
    return;
  }
  if (currentEventMode === "live_mock") {
    loadReport();
  }
  if (currentEventMode === "live_asr") {
    loadLiveAsrDraft();
    const readinessEvents = events.length > 1 ? events : liveStreamEvents;
    loadLiveAsrCardLifecycleReadinessSummary(readinessEvents);
  }
}

function closeLiveEventStream() {
  if (!liveEventSource) {
    return;
  }
  liveEventSource.close();
  liveEventSource = null;
  window.__meetingCopilotLiveStreamClosed = true;
}

function createLiveIncrementalSnapshot(snapshot) {
  return {
    ...emptyLiveSnapshot(snapshot.session_id, "Live Mock 增量事件流进行中"),
    quality: {
      ...snapshot.quality,
      state_event_count: 0,
      suggestion_card_count: 0,
    },
  };
}

function emptyLiveSnapshot(sessionId, summary) {
  return {
    session_id: sessionId,
    summary,
    transcript: {
      text: "",
      normalized_text: "",
      segments: [],
      evidence_spans: [],
    },
    states: {
      decision_candidates: [],
      action_items: [],
      risks: [],
      open_questions: [],
    },
    suggestion_cards: [],
    state_events: [],
    quality: {
      provider: "local_mock_asr",
      latency_ms: 0,
      rtf: 0,
      state_event_count: 0,
      suggestion_card_count: 0,
      llm_total_tokens: 0,
    },
  };
}

function createSnapshotLookup(snapshot) {
  const states = snapshot.states || {};
  const stateItemsByType = {};
  Object.entries(stateTypeToCollection()).forEach(([targetType, collectionName]) => {
    stateItemsByType[targetType] = indexById(states[collectionName] || []);
  });
  return {
    segments: indexById(snapshot.transcript?.segments || []),
    evidenceSpans: indexById(snapshot.transcript?.evidence_spans || []),
    stateItemsByType,
    cards: indexById(snapshot.suggestion_cards || []),
  };
}

function applyLiveEventToSnapshot(event) {
  if (!currentSnapshot || !snapshotLookup) {
    return;
  }
  if (event.event_type === "transcript_partial") {
    applyTranscriptPartial(event.payload || {});
    return;
  }
  if (event.event_type === "transcript_final" || event.event_type === "transcript_revision") {
    applyTranscriptFinal(event.payload || {});
    return;
  }
  if (event.event_type === "state_event") {
    applyStateEvent(event.payload || {});
    return;
  }
  if (
    event.event_type === "suggestion_card"
    || event.event_type === "suggestion_silenced"
    || event.event_type === "suggestion_invalidated"
  ) {
    applySuggestionEvent(event.payload || {});
  }
}

function applyTranscriptPartial(payload) {
  const segmentId = payload.segment_id;
  if (!segmentId) {
    return;
  }
  const partialSegment = {
    id: segmentId,
    start_ms: payload.start_ms ?? 0,
    end_ms: payload.end_ms ?? 0,
    text: payload.text || "",
    confidence: payload.confidence,
    is_partial: true,
  };
  upsertById(currentSnapshot.transcript.segments, partialSegment);
  renderTranscript(currentSnapshot.transcript);
}

function applyTranscriptFinal(payload) {
  const segmentId = payload.segment_id;
  if (!segmentId) {
    return;
  }
  const segment = snapshotLookup.segments[segmentId] || {
    id: segmentId,
    start_ms: payload.start_ms ?? 0,
    end_ms: payload.end_ms ?? 0,
    text: payload.text || "",
    confidence: payload.confidence,
  };
  upsertById(currentSnapshot.transcript.segments, {...segment, is_partial: false});
  const evidenceSpans = payload.evidence_spans || Object.values(snapshotLookup.evidenceSpans)
    .filter((evidence) => evidence.segment_id === segmentId);
  (payload.superseded_evidence_spans || [])
    .forEach((evidence) => upsertById(currentSnapshot.transcript.evidence_spans, evidence));
  evidenceSpans
    .forEach((evidence) => upsertById(currentSnapshot.transcript.evidence_spans, evidence));
  currentSnapshot.transcript.text = currentSnapshot.transcript.segments
    .map((item) => item.text || "")
    .join("");
  currentSnapshot.transcript.normalized_text = currentSnapshot.transcript.text;
  renderEvidence(currentSnapshot.transcript.evidence_spans);
  renderTranscript(currentSnapshot.transcript);
  renderStateBoard(currentSnapshot.states, currentSnapshot.transcript.evidence_spans);
  renderSuggestionCards(currentSnapshot.suggestion_cards, currentSnapshot.transcript.evidence_spans);
}

function applyStateEvent(payload) {
  const targetType = payload.target_type;
  const targetId = payload.target_id;
  const collectionName = stateTypeToCollection()[targetType];
  const item = payload.state_item || snapshotLookup.stateItemsByType[targetType]?.[targetId];
  if (!collectionName || !item) {
    return;
  }
  upsertById(currentSnapshot.states[collectionName], item);
  upsertById(currentSnapshot.state_events, {
    id: payload.event_id,
    target_type: targetType,
    target_id: targetId,
    event_type: payload.state_event_type || "created",
    evidence_span_ids: payload.evidence_span_ids || [],
  });
  currentSnapshot.quality.state_event_count = currentSnapshot.state_events.length;
  renderQuality(currentSnapshot.quality);
  renderStateBoard(currentSnapshot.states, currentSnapshot.transcript.evidence_spans);
}

function applySuggestionEvent(payload) {
  const cardId = payload.card_id;
  const incomingCard = payload.card || snapshotLookup.cards[cardId];
  const currentCard = snapshotLookup.cards[cardId];
  const card = mergeSuggestionCardLifecycle(currentCard, incomingCard);
  if (!card) {
    return;
  }
  snapshotLookup.cards[cardId] = card;
  upsertById(currentSnapshot.suggestion_cards, card);
  currentSnapshot.quality.suggestion_card_count = currentSnapshot.suggestion_cards.length;
  renderQuality(currentSnapshot.quality);
  renderSuggestionCards(currentSnapshot.suggestion_cards, currentSnapshot.transcript.evidence_spans);
}

function mergeSuggestionCardLifecycle(currentCard, incomingCard) {
  if (!incomingCard) {
    return null;
  }
  if (!currentCard?.invalidated_by_event_id) {
    return incomingCard;
  }
  if (incomingCard.invalidated_by_event_id) {
    return incomingCard;
  }
  return {
    ...incomingCard,
    show_or_silence_decision: "silence",
    invalidation_reason: currentCard.invalidation_reason,
    invalidated_by_event_id: currentCard.invalidated_by_event_id,
    stale_evidence_span_ids: currentCard.stale_evidence_span_ids || incomingCard.stale_evidence_span_ids || [],
    replacement_evidence_span_ids: currentCard.replacement_evidence_span_ids || incomingCard.replacement_evidence_span_ids || [],
  };
}

function stateTypeToCollection() {
  return {
    DecisionCandidate: "decision_candidates",
    ActionItem: "action_items",
    Risk: "risks",
    OpenQuestion: "open_questions",
  };
}

function upsertById(items, item) {
  const index = items.findIndex((existing) => existing.id === item.id);
  if (index === -1) {
    items.push(item);
    return;
  }
  items[index] = item;
}

async function loadEventStream() {
  if (!currentSessionId) {
    return [];
  }
  const sessionId = currentSessionId;
  const mode = currentEventMode;
  try {
    const data = await requestJson(eventStreamUrl(sessionId, mode));
    if (currentSessionId !== sessionId || currentEventMode !== mode) {
      return [];
    }
    const defaultSource = mode === "live_asr"
      ? "live_asr_stream"
      : (mode === "live_mock" ? "live_mock_stream" : "replay_snapshot");
    renderEventStream(data.events || [], {
      source: data.source || defaultSource,
      trace_kind: data.trace_kind || (mode === "live_mock" || mode === "live_asr" ? "live_event" : "replay_derived"),
    });
    syncEvaluationFromEvents(data.events || []);
    return data.events || [];
  } catch (error) {
    showToast(error.message);
    return [];
  }
}

function eventStreamUrl(sessionId = currentSessionId, mode = currentEventMode) {
  if (mode === "live_asr") {
    return `/live/asr/sessions/${sessionId}/events`;
  }
  if (mode === "live_mock") {
    return `/live/sessions/${sessionId}/events`;
  }
  return `/sessions/${sessionId}/events`;
}

function liveEventStreamSseUrl() {
  if (currentEventMode === "live_asr") {
    return `/live/asr/sessions/${currentSessionId}/events.sse`;
  }
  return `/live/sessions/${currentSessionId}/events.sse`;
}

function isLiveEventMode() {
  return currentEventMode === "live_mock" || currentEventMode === "live_asr";
}

function syncEventModeButtons() {
  replayModeButton.setAttribute("aria-pressed", String(currentEventMode === "replay"));
  liveMockModeButton.setAttribute("aria-pressed", String(currentEventMode === "live_mock"));
  liveAsrModeButton.setAttribute("aria-pressed", String(currentEventMode === "live_asr"));
  const labels = {
    replay: "Replay Event Stream",
    live_mock: "Live Mock Event Stream",
    live_asr: "Live ASR Event Stream",
  };
  document.getElementById("event-stream-eyebrow").textContent = labels[currentEventMode] || labels.replay;
}

function renderEventStream(events, sourceMeta = {}) {
  const panel = document.getElementById("event-stream-panel");
  if (!events.length) {
    panel.innerHTML = `<div class="empty">暂无事件</div>`;
    return;
  }
  const source = sourceMeta.source || events[0]?.source || "unknown";
  const traceKind = sourceMeta.trace_kind || events[0]?.trace_kind || "unknown";
  const sourceItem = document.createElement("article");
  sourceItem.className = `event-item stream-source ${escapeHtml(source)}`;
  sourceItem.innerHTML = `
    <div>
      <strong>${escapeHtml(source)}</strong>
      <span>${escapeHtml(traceKind)}</span>
    </div>
    <p>${streamSourceDescription(source)}</p>
  `;
  panel.replaceChildren(
    sourceItem,
    ...events.map((event) => {
      const item = document.createElement("article");
      item.className = `event-item ${event.event_type}`;
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(event.sequence)} · ${escapeHtml(event.event_type)}</strong>
          <span>${escapeHtml(event.at_ms)}ms</span>
        </div>
        <p>${escapeHtml(eventSummary(event))}</p>
      `;
      return item;
    }),
  );
}

function streamSourceDescription(source) {
  if (source === "live_asr_stream") {
    return "local ASR streaming contract skeleton";
  }
  if (source === "live_mock_stream") {
    return "mock live envelope";
  }
  return "replay snapshot timeline";
}

function eventSummary(event) {
  const payload = event.payload || {};
  if (event.event_type === "transcript_final") {
    return `${payload.segment_id}: ${payload.text || ""}`;
  }
  if (event.event_type === "transcript_partial") {
    return `${payload.segment_id}: ${payload.text || ""}`;
  }
  if (event.event_type === "transcript_revision") {
    return `${payload.segment_id} revises ${payload.revision_of || ""}: ${payload.text || ""}`;
  }
  if (event.event_type === "state_event") {
    return `${payload.target_type}:${payload.target_id} ${payload.event_type || payload.state_event_type || ""}`;
  }
  if (event.event_type === "llm_scheduled") {
    return `${payload.card_id} · ${payload.trigger_source} · ${payload.gap_rule_id} · ${payload.model}`;
  }
  if (event.event_type === "scheduler_event") {
    return [
      payload.scheduler_event_type,
      payload.decision_reason,
      payload.llm_call_status,
      `cooldown ${payload.cooldown_remaining_ms ?? 0}ms`,
      payload.card_id,
      payload.trigger_source,
      payload.gap_rule_id,
      payload.model,
    ].filter(Boolean).join(" · ");
  }
  if (event.event_type === "suggestion_candidate_event") {
    const degradationReasons = (payload.degradation_reasons || []).join(", ") || "none";
    return [
      payload.candidate_id,
      payload.gap_rule_id,
      `${payload.confidence_level || "unknown"}/${payload.confidence ?? "n/a"}`,
      `degraded ${degradationReasons}`,
      payload.llm_call_status,
      payload.card_status,
    ].filter(Boolean).join(" · ");
  }
  if (event.event_type === "llm_request_draft_event") {
    const degradationReasons = (payload.candidate_degradation_reasons || []).join(", ") || "none";
    return [
      payload.request_id,
      payload.target_candidate_id,
      payload.request_status,
      payload.gap_rule_id,
      payload.input_summary,
      `source ${(payload.source_event_ids || []).join(", ") || "none"}`,
      `evidence ${(payload.evidence_span_ids || []).join(", ") || "none"}`,
      `segments ${(payload.segment_batch || []).join(", ") || "none"}`,
      payload.llm_call_status,
      payload.schema_status,
      payload.card_status,
      `${payload.candidate_confidence_level || "unknown"}/${payload.candidate_confidence ?? "n/a"}`,
      `degraded ${degradationReasons}`,
    ].filter(Boolean).join(" · ");
  }
  if (event.event_type === "llm_schema_result") {
    const usage = payload.usage || {};
    const totalTokens = usage.total_tokens ?? 0;
    return `${payload.card_id} · ${payload.schema_result} · ${payload.show_or_silence_decision} · ${totalTokens} tokens`;
  }
  if (event.event_type === "suggestion_silenced") {
    return `${payload.card_id} · ${payload.schema_result} · ${payload.show_or_silence_decision}`;
  }
  if (event.event_type === "suggestion_invalidated") {
    return [
      payload.card_id,
      payload.reason,
      `by ${payload.invalidated_by_event_id}`,
      `stale ${(payload.stale_evidence_span_ids || []).join(", ")}`,
      `replacement ${(payload.replacement_evidence_span_ids || []).join(", ")}`,
    ].filter(Boolean).join(" · ");
  }
  if (event.event_type === "suggestion_card") {
    return `${payload.card_id} · ${payload.gap_rule_id} · ${payload.schema_result}`;
  }
  if (event.event_type === "provider_error") {
    return `${payload.provider || ""} · ${payload.message || ""}`;
  }
  if (event.event_type === "evaluation_summary") {
    if (payload.source === "live_asr_stream") {
      return `${payload.provider} · final ${payload.final_event_count || 0} · revision ${payload.revision_event_count || 0}`;
    }
    return payload.passes_minimum_gate ? "fixture gate passed" : `fixture gate failed: ${(payload.failures || []).join(", ")}`;
  }
  return JSON.stringify(payload);
}

function localAsrStreamingEvents() {
  return [
    {
      event_type: "partial",
      segment_id: "asr_seg_001",
      text: "先灰度",
      start_ms: 0,
      end_ms: 1200,
      received_at_ms: 1300,
      confidence: 0.72,
    },
    {
      event_type: "final",
      segment_id: "asr_seg_001",
      text: "先灰度 10%。",
      start_ms: 0,
      end_ms: 3200,
      received_at_ms: 3500,
      confidence: 0.91,
    },
    {
      event_type: "revision",
      segment_id: "asr_seg_001_rev1",
      revision_of: "asr_seg_001",
      text: "先灰度 5%，不是 10%。",
      start_ms: 0,
      end_ms: 3400,
      received_at_ms: 5200,
      confidence: 0.94,
    },
    {
      event_type: "final",
      segment_id: "asr_seg_002",
      text: "谁负责回滚？",
      start_ms: 3400,
      end_ms: 6100,
      received_at_ms: 7000,
      confidence: 0.9,
    },
    {
      event_type: "final",
      segment_id: "asr_seg_003",
      text: "如果错误率超过 0.1% 就回滚。",
      start_ms: 6100,
      end_ms: 8200,
      received_at_ms: 8800,
      confidence: 0.9,
    },
    {
      event_type: "final",
      segment_id: "asr_seg_004",
      text: "张三下周三补充兼容性测试用例。",
      start_ms: 8200,
      end_ms: 10400,
      received_at_ms: 11200,
      confidence: 0.9,
    },
    {
      event_type: "end_of_stream",
      segment_id: "asr_eos",
      text: "",
      start_ms: 10400,
      end_ms: 10400,
      received_at_ms: 11400,
    },
  ];
}

function syncEvaluationFromEvents(events) {
  const event = [...events].reverse().find((item) => item.event_type === "evaluation_summary");
  if (!event) {
    return;
  }
  currentEvaluationSummary = event.payload || null;
  renderEvaluationSummary(currentEvaluationSummary);
}

function renderEvaluationSummary(summary) {
  const panel = document.getElementById("evaluation-panel");
  if (!summary) {
    panel.innerHTML = "";
    return;
  }
  const stateCounts = summary.state_counts || {};
  const statusClass = summary.passes_minimum_gate ? "ok" : "danger";
  const gateText = summary.passes_minimum_gate ? "Gate passed" : "Gate failed";
  const failures = summary.failures && summary.failures.length
    ? summary.failures.join(", ")
    : "none";
  const metrics = [
    ["Gate", gateText, statusClass],
    ["Meeting", summary.is_engineering_meeting ? "engineering" : "non-engineering", ""],
    ["Effective Cards", summary.effective_card_count, ""],
    ["Gap Rules", `${summary.gap_rule_count}/${summary.expected_gap_rule_count}`, ""],
    ["False Positive", summary.false_positive_count, ""],
    ["Too Late", summary.too_late_count, ""],
    ["Kept", summary.kept_count, ""],
    ["Schema Blocked", summary.schema_blocked_count || 0, ""],
    ["Silenced", summary.silenced_card_count || 0, ""],
    ["States", stateCountText(stateCounts), ""],
  ];
  const items = metrics.map(([label, value, className]) => {
    const item = document.createElement("div");
    item.className = `metric compact ${className || ""}`.trim();
    item.innerHTML = `<strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span>`;
    return item;
  });
  const footer = document.createElement("p");
  footer.className = summary.failures && summary.failures.length ? "evaluation-failures danger-text" : "evaluation-failures";
  footer.textContent = `failures: ${failures}`;
  panel.replaceChildren(...items, footer);
}

function stateCountText(stateCounts) {
  return [
    stateCounts.decision_candidates || 0,
    stateCounts.action_items || 0,
    stateCounts.risks || 0,
    stateCounts.open_questions || 0,
  ].join("/");
}

function renderQuality(quality) {
  const metrics = [
    ["Provider", quality.provider],
    ["Latency", `${quality.latency_ms} ms`],
    ["RTF", Number(quality.rtf).toFixed(2)],
    ["State Events", quality.state_event_count],
    ["Cards", quality.suggestion_card_count],
    ["LLM Tokens", quality.llm_total_tokens],
  ];
  document.getElementById("quality-panel").replaceChildren(
    ...metrics.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "metric";
      item.innerHTML = `<strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span>`;
      return item;
    }),
  );
}

function renderStateBoard(states, evidenceSpans) {
  const evidenceById = indexById(evidenceSpans);
  const segmentByEvidence = segmentByEvidenceId(evidenceSpans);
  const lanes = [
    ["decision_candidates", "候选决策", (item) => item.statement || item.description || item.id],
    ["action_items", "行动项", (item) => item.description || item.title || item.id],
    ["risks", "风险", (item) => item.description || item.title || item.id],
    ["open_questions", "未闭环问题", (item) => item.question || item.description || item.id],
  ];
  const board = document.getElementById("state-board");
  board.replaceChildren(
    ...lanes.map(([key, title, textFor]) => {
      const lane = document.createElement("article");
      lane.className = "state-lane";
      const items = states[key] || [];
      lane.innerHTML = `<header><h3>${title}</h3></header>`;
      const list = document.createElement("ul");
      if (items.length === 0) {
        list.innerHTML = `<li class="empty">暂无</li>`;
      } else {
        items.forEach((item) => {
          const li = document.createElement("li");
          li.className = "state-item";
          li.innerHTML = `<p>${escapeHtml(textFor(item))}</p>`;
          li.append(renderEvidenceChips(item.evidence_span_ids, evidenceById, segmentByEvidence));
          list.append(li);
        });
      }
      lane.append(list);
      return lane;
    }),
  );
}

function renderSuggestionCards(cards, evidenceSpans) {
  const evidenceById = indexById(evidenceSpans);
  const segmentByEvidence = segmentByEvidenceId(evidenceSpans);
  const list = document.getElementById("suggestion-list");
  if (!cards.length) {
    list.innerHTML = `<div class="empty">当前无正式建议卡片</div>`;
    return;
  }
  list.replaceChildren(
    ...cards.map((card) => {
      const item = document.createElement("article");
      const staleEvidenceIds = staleEvidenceIdsForCard(card, evidenceById);
      const missingEvidenceIds = missingEvidenceIdsForCard(card, evidenceById);
      const actionable = isActionableCard(card, evidenceById);
      item.className = actionable ? "suggestion-card" : "suggestion-card muted";
      const title = card.title || card.suggested_question || card.id;
      const question = card.suggested_question || card.trigger_reason || "";
      const trace = [
        ["", `rule: ${card.gap_rule_id}`],
        ["", `latency: ${card.latency_ms}ms`],
        [schemaResultClass(card.schema_result), `schema: ${card.schema_result}`],
        ["", `decision: ${card.show_or_silence_decision}`],
        ["", `model: ${card.model}`],
        [card.invalidation_reason ? "warning" : "", card.invalidation_reason ? `invalidated: ${card.invalidation_reason}` : ""],
        [staleEvidenceIds.length ? "warning" : "", staleEvidenceIds.length ? `evidence: stale ${staleEvidenceIds.join(", ")}` : ""],
        [missingEvidenceIds.length ? "danger" : "", missingEvidenceIds.length ? `evidence: missing ${missingEvidenceIds.join(", ")}` : ""],
      ];
      item.innerHTML = `
        <div>
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(question)}</p>
          <div class="meta-row">
            <span class="chip">${escapeHtml(card.type)}</span>
            <span class="chip">${escapeHtml(card.status)}</span>
            ${trace.filter(([, value]) => value).map(([className, value]) => `<span class="chip ${escapeHtml(className)}">${escapeHtml(value)}</span>`).join("")}
          </div>
        </div>
      `;
      item.querySelector("div").append(renderEvidenceChips(card.evidence_span_ids, evidenceById, segmentByEvidence));
      if (actionable) {
        item.append(renderCardActions(card));
      }
      return item;
    }),
  );
}

function isActionableCard(card, evidenceById = {}) {
  return (
    card.show_or_silence_decision === "show"
    && staleEvidenceIdsForCard(card, evidenceById).length === 0
    && missingEvidenceIdsForCard(card, evidenceById).length === 0
  );
}

function staleEvidenceIdsForCard(card, evidenceById) {
  return (card.evidence_span_ids || []).filter((evidenceId) => {
    const evidence = evidenceById[evidenceId];
    return evidenceLifecycleClass(evidence?.status) === "stale";
  });
}

function missingEvidenceIdsForCard(card, evidenceById) {
  return (card.evidence_span_ids || []).filter((evidenceId) => !evidenceById[evidenceId]);
}

function schemaResultClass(schemaResult) {
  if (schemaResult === "valid") {
    return "";
  }
  if (schemaResult === "timeout") {
    return "warning";
  }
  return "danger";
}

function renderCardActions(card) {
  const actions = document.createElement("div");
  actions.className = "card-actions";
  [
    ["kept", "保留"],
    ["dismissed", "忽略"],
    ["marked_wrong", "标错"],
    ["too_late", "太晚"],
    ["too_intrusive", "打扰"],
  ].forEach(([status, label]) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", () => updateCardStatus(card.id, status));
    actions.append(button);
  });
  return actions;
}

async function updateCardStatus(cardId, status) {
  if (!currentSessionId) {
    return;
  }
  try {
    const updatedSnapshot = await requestJson(`/sessions/${currentSessionId}/cards/${cardId}/status`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({status}),
    });
    if (currentEventMode === "live_mock") {
      applyLiveCardStatus(updatedSnapshot, cardId);
      await loadReport();
      showToast(`卡片已更新为 ${status}`);
      return;
    }
    currentSnapshot = updatedSnapshot;
    renderSnapshot(currentSnapshot);
    await loadEventStream();
    await loadReport();
    showToast(`卡片已更新为 ${status}`);
  } catch (error) {
    showToast(error.message);
  }
}

function applyLiveCardStatus(updatedSnapshot, cardId) {
  const updatedCard = (updatedSnapshot.suggestion_cards || []).find((card) => card.id === cardId);
  if (!updatedCard || !currentSnapshot || !snapshotLookup) {
    return;
  }
  snapshotLookup.cards[cardId] = updatedCard;
  const currentCard = currentSnapshot.suggestion_cards.find((card) => card.id === cardId);
  if (currentCard) {
    Object.assign(currentCard, updatedCard);
  }
  renderSuggestionCards(currentSnapshot.suggestion_cards, currentSnapshot.transcript.evidence_spans);
}

function renderEvidence(evidenceSpans) {
  const panel = document.getElementById("evidence-panel");
  if (!evidenceSpans.length) {
    panel.innerHTML = `<div class="empty">暂无证据</div>`;
    return;
  }
  panel.replaceChildren(
    ...evidenceSpans.map((evidence) => {
      const item = document.createElement("article");
      item.className = `evidence-item ${evidenceLifecycleClass(evidence.status)}`.trim();
      item.id = `evidence-${evidence.id}`;
      const lineageChips = [
        evidence.revision_of ? `<span class="chip">revision_of ${escapeHtml(evidence.revision_of)}</span>` : "",
        evidence.replaced_by ? `<span class="chip">replaced_by ${escapeHtml(evidence.replaced_by)}</span>` : "",
      ].join("");
      item.innerHTML = `
        <p><strong>${escapeHtml(evidence.id)}</strong> · ${escapeHtml(evidence.segment_id)}</p>
        <p>${escapeHtml(evidence.quote)}</p>
        <div class="meta-row">
          <span class="chip">${evidence.start_ms}-${evidence.end_ms}ms</span>
          <span class="chip ${escapeHtml(evidenceLifecycleClass(evidence.status))}">${escapeHtml(evidence.status || "active")}</span>
          ${lineageChips}
        </div>
      `;
      return item;
    }),
  );
}

function renderTranscript(transcript) {
  const panel = document.getElementById("transcript-panel");
  const segments = transcript.segments || [];
  if (!segments.length) {
    panel.innerHTML = `<div class="empty">${escapeHtml(transcript.normalized_text || transcript.text || "暂无转写")}</div>`;
    return;
  }
  panel.replaceChildren(
    ...segments.map((segment) => {
      const item = document.createElement("article");
      item.className = "segment-item";
      item.id = `transcript-segment-${segment.id}`;
      item.innerHTML = `
        <p><strong>${escapeHtml(segment.id)}</strong> · ${segment.start_ms}-${segment.end_ms}ms</p>
        <p>${escapeHtml(segment.text)}</p>
      `;
      return item;
    }),
  );
}

function renderEvidenceChips(evidenceIds, evidenceById, segmentByEvidence) {
  const row = document.createElement("div");
  row.className = "meta-row";
  (evidenceIds || []).forEach((evidenceId) => {
    const evidence = evidenceById[evidenceId];
    const segmentId = segmentByEvidence[evidenceId] || "";
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = evidenceId;
    chip.dataset.evidenceId = evidenceId;
    if (segmentId) {
      chip.dataset.segmentId = segmentId;
    }
    chip.addEventListener("click", () => focusEvidence(evidenceId, segmentId));
    if (!evidence) {
      chip.classList.add("danger");
    } else {
      const lifecycleClass = evidenceLifecycleClass(evidence.status);
      if (lifecycleClass) {
        chip.classList.add(lifecycleClass);
      }
    }
    row.append(chip);
  });
  return row;
}

function focusEvidence(evidenceId, segmentId) {
  document.querySelectorAll(".evidence-item.active").forEach((item) => {
    item.classList.remove("active");
  });
  const target = document.getElementById(`evidence-${evidenceId}`);
  if (target) {
    target.classList.add("active");
    target.scrollIntoView({block: "nearest", behavior: "smooth"});
  }
  focusTranscriptSegment(segmentId);
}

function focusTranscriptSegment(segmentId) {
  document.querySelectorAll(".segment-item.active").forEach((item) => {
    item.classList.remove("active");
  });
  if (!segmentId) {
    return;
  }
  const target = document.getElementById(`transcript-segment-${segmentId}`);
  if (target) {
    target.classList.add("active");
    target.scrollIntoView({block: "nearest", behavior: "smooth"});
  }
}

function segmentByEvidenceId(evidenceSpans) {
  return Object.fromEntries(
    (evidenceSpans || []).map((evidence) => [evidence.id, evidence.segment_id]),
  );
}

function evidenceLifecycleClass(status) {
  const normalized = String(status || "active");
  if (normalized === "stale" || normalized === "superseded") {
    return "stale";
  }
  return "";
}

function renderMacLocalShadowMvpEmpty(message = "等待本地 shadow MVP 演示会话。") {
  const panel = document.getElementById("mac-local-shadow-mvp-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderMacLocalShadowMvp(summary) {
  const panel = document.getElementById("mac-local-shadow-mvp-panel");
  if (!panel) {
    return;
  }
  const demoId = summary.demo_id || macLocalShadowMvpDemoId;
  const closureStatus = summary.closure_status || macLocalShadowMvpClosureStatus;
  const counts = summary.live_event_counts || {};
  const blockers = summary.readiness_blockers || [];
  panel.innerHTML = `
    <div class="mac-local-shadow-mvp-grid">
      ${macLocalShadowMvpMetric("Demo", demoId)}
      ${macLocalShadowMvpMetric("Status", summary.demo_status)}
      ${macLocalShadowMvpMetric("Closure", closureStatus)}
      ${macLocalShadowMvpMetric("Finals", counts.transcript_final || 0)}
      ${macLocalShadowMvpMetric("Revisions", counts.transcript_revision || 0)}
      ${macLocalShadowMvpMetric("States", counts.state_event || 0)}
      ${macLocalShadowMvpMetric("Drafts", counts.llm_request_draft_event || 0)}
      ${macLocalShadowMvpMetric("LLM", summary.llm_execution_status)}
      ${macLocalShadowMvpMetric("Real Mic", summary.real_mic_shadow_readiness_status)}
    </div>
    <div class="mac-local-shadow-mvp-flow">
      ${(summary.product_chain || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
    <div class="mac-local-shadow-mvp-blockers">
      ${blockers.map((blocker) => `<span>${escapeHtml(blocker)}</span>`).join("")}
    </div>
  `;
}

function renderRealisticMeetingSimulationPack(summary) {
  const panel = document.getElementById("mac-local-shadow-mvp-panel");
  if (!panel) {
    return;
  }
  const counts = summary.live_event_counts || {};
  const meetingShape = summary.meeting_shape || {};
  const blockers = summary.readiness_blockers || [];
  const features = summary.realism_features || [];
  const terms = summary.technical_terms || [];
  panel.innerHTML = `
    <div class="mac-local-shadow-mvp-grid">
      ${macLocalShadowMvpMetric("Simulation", summary.simulation_id || realisticMeetingSimulationPackId)}
      ${macLocalShadowMvpMetric("Scenario", summary.scenario_id)}
      ${macLocalShadowMvpMetric("Speakers", meetingShape.speaker_count || 0)}
      ${macLocalShadowMvpMetric("Turns", meetingShape.speaker_turn_count || 0)}
      ${macLocalShadowMvpMetric("Revisions", counts.transcript_revision || 0)}
      ${macLocalShadowMvpMetric("States", counts.state_event || 0)}
      ${macLocalShadowMvpMetric("Drafts", counts.llm_request_draft_event || 0)}
      ${macLocalShadowMvpMetric("LLM", summary.llm_execution_status)}
      ${macLocalShadowMvpMetric("Real Mic", summary.real_mic_shadow_readiness_status)}
    </div>
    <div class="mac-local-shadow-mvp-flow">
      ${(summary.product_chain || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
    <div class="mac-local-shadow-mvp-flow">
      ${features.map((feature) => `<span>${escapeHtml(feature)}</span>`).join("")}
      ${terms.map((term) => `<span>${escapeHtml(term)}</span>`).join("")}
    </div>
    <div class="mac-local-shadow-mvp-blockers">
      ${blockers.map((blocker) => `<span>${escapeHtml(blocker)}</span>`).join("")}
    </div>
  `;
}

function renderMainlineAsrBlockedTrial(summary) {
  const panel = document.getElementById("mac-local-shadow-mvp-panel");
  if (!panel) {
    return;
  }
  const counts = summary.live_event_counts || {};
  const replay = summary.product_replay_summary || {};
  const blockers = summary.readiness_blockers || [];
  const candidates = summary.blocked_asr_candidates || [];
  panel.innerHTML = `
    <div class="mac-local-shadow-mvp-grid">
      ${macLocalShadowMvpMetric("Trial", summary.trial_id || mainlineAsrBlockedTrialId)}
      ${macLocalShadowMvpMetric("Decision", summary.mainline_decision_id || "DEC-201")}
      ${macLocalShadowMvpMetric("ASR Exit", summary.asr_quality_exit_status)}
      ${macLocalShadowMvpMetric("Gate", summary.asr_quality_decision_status)}
      ${macLocalShadowMvpMetric("Artifact", summary.source_event_artifact_status || "not_applicable")}
      ${macLocalShadowMvpMetric("Finals", counts.transcript_final || 0)}
      ${macLocalShadowMvpMetric("States", counts.state_event || 0)}
      ${macLocalShadowMvpMetric("Drafts", counts.llm_request_draft_event || 0)}
      ${macLocalShadowMvpMetric("FunASR previews", `${replay.funasr_engineering_preview_created_count || 0}/${replay.funasr_engineering_scenario_count || 0}`)}
      ${macLocalShadowMvpMetric("Next", summary.recommended_next_action || mainlineAsrBlockedTrialNextAction)}
    </div>
    <div class="mac-local-shadow-mvp-flow">
      ${(summary.product_chain || []).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
    <div class="mac-local-shadow-mvp-flow">
      ${candidates.map((candidate) => `<span>${escapeHtml(candidate.candidate_id)} · ${escapeHtml(candidate.rtf_range)} · ${escapeHtml(candidate.quality_tradeoff)}</span>`).join("")}
      <span>${escapeHtml(replay.failed_funasr_scenario_id || "incident-review-001")}</span>
      <span>${escapeHtml(summary.events_path || "")}</span>
      <span>${escapeHtml(summary.selected_product_route || "")}</span>
    </div>
    <div class="mac-local-shadow-mvp-blockers">
      ${blockers.map((blocker) => `<span>${escapeHtml(blocker)}</span>`).join("")}
    </div>
  `;
}

function isMainlineTrialSession(summary) {
  return Boolean(
    summary &&
      [mainlineAsrBlockedTrialId, mainlineAsrEventArtifactTrialId].includes(
        summary.trial_id,
      ),
  );
}

async function loadMainlineTrialFeedbackExportClosure() {
  if (
    !currentSessionId ||
    !macLocalShadowMvpState ||
    !isMainlineTrialSession(macLocalShadowMvpState)
  ) {
    showToast("请先运行主线试运行");
    return;
  }
  renderMainlineTrialFeedbackExportClosureEmpty("正在生成主线反馈与导出预览...");
  try {
    const data = await requestJson("/desktop/mainline-trial-feedback-export-closures", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({session_id: currentSessionId}),
    });
    renderMainlineTrialFeedbackExportClosure(data);
    document.getElementById("report-panel").textContent = data.markdown_export_preview || "";
    showToast("主线闭环预览已生成");
  } catch (error) {
    renderMainlineTrialFeedbackExportClosureEmpty("主线闭环预览生成失败。");
    showToast(error.message);
  }
}

function renderMainlineTrialFeedbackExportClosureEmpty(message = "等待主线试运行后的反馈与导出预览。") {
  mainlineTrialFeedbackExportClosureState = null;
  const panel = document.getElementById("mainline-closure-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderMainlineTrialFeedbackExportClosure(closure) {
  mainlineTrialFeedbackExportClosureState = closure;
  const panel = document.getElementById("mainline-closure-panel");
  if (!panel) {
    return;
  }
  const counts = closure.timeline_counts || {};
  const decision = closure.final_decision || {};
  const feedback = closure.feedback_analysis || {};
  const selectedCandidateIds = closure.selected_candidate_ids || [];
  panel.innerHTML = `
    <div class="mainline-closure-grid">
      ${macLocalShadowMvpMetric("Closure", closure.closure_id || "mainline_trial_feedback_export_closure")}
      ${macLocalShadowMvpMetric("Status", closure.closure_status)}
      ${macLocalShadowMvpMetric("Export", closure.export_readiness_status)}
      ${macLocalShadowMvpMetric("Evidence", closure.go_evidence_status)}
      ${macLocalShadowMvpMetric("Decision", decision.decision)}
      ${macLocalShadowMvpMetric("Feedback", closure.feedback_ingestion_status)}
      ${macLocalShadowMvpMetric("Entries", closure.feedback_entry_count ?? 0)}
      ${macLocalShadowMvpMetric("Cards", counts.candidate_cards ?? 0)}
    </div>
    <div class="mainline-closure-flow">
      ${selectedCandidateIds.map((candidateId) => `<span>${escapeHtml(candidateId)}</span>`).join("")}
      <span>${escapeHtml(closure.source_trial_id || mainlineAsrBlockedTrialId)}</span>
      <span>${escapeHtml(closure.source_event_artifact_status || "not_applicable")}</span>
      <span>${escapeHtml(closure.final_decision_readiness_status || "")}</span>
      <span>positive=${escapeHtml(feedback.useful_or_would_have_asked_count ?? 0)}</span>
      <span>negative=${escapeHtml(feedback.negative_feedback_count ?? 0)}</span>
    </div>
    <div class="mainline-closure-flow warning">
      <span>${escapeHtml(closure.not_go_reason || "synthetic preview is not Go evidence")}</span>
      <span>safe_to_call_remote_asr_now=${escapeHtml(closure.safe_to_call_remote_asr_now)}</span>
      <span>safe_to_call_llm_now=${escapeHtml(closure.safe_to_call_llm_now)}</span>
      <span>safe_to_access_microphone_now=${escapeHtml(closure.safe_to_access_microphone_now)}</span>
    </div>
  `;
}

function macLocalShadowMvpMetric(label, value) {
  return `
    <article class="mac-local-shadow-mvp-metric">
      <strong>${escapeHtml(value ?? "")}</strong>
      <span>${escapeHtml(label)}</span>
    </article>
  `;
}

async function loadLiveAsrCardLifecycleReadinessSummary(events = liveStreamEvents) {
  if (!currentSessionId || currentEventMode !== "live_asr") {
    return;
  }
  const probe = buildCardLifecycleReadinessCandidateResponse(events);
  if (!probe) {
    renderCardLifecycleReadinessEmpty("没有可评估的 Live ASR request draft。");
    return;
  }
  const sessionId = currentSessionId;
  const mode = currentEventMode;
  renderCardLifecycleReadinessEmpty("正在生成 response-only readiness summary...");
  try {
    const summary = await requestJson(
      `/live/asr/sessions/${sessionId}/llm-card-lifecycle-readiness-summaries`,
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          mode: "summary_only",
          request_id: probe.requestId,
          candidate_response: probe.candidateResponse,
        }),
      },
    );
    if (currentSessionId !== sessionId || currentEventMode !== mode) {
      return;
    }
    renderCardLifecycleReadinessSummary(summary);
  } catch (error) {
    if (currentSessionId === sessionId && currentEventMode === mode) {
      renderCardLifecycleReadinessEmpty("readiness summary 生成失败。");
    }
    showToast(error.message);
  }
}

function buildCardLifecycleReadinessCandidateResponse(events) {
  const drafts = events
    .filter((event) => event.event_type === "llm_request_draft_event")
    .map((event) => event.payload || {});
  const activeEvidenceIds = new Set();
  events.forEach((event) => {
    const payload = event.payload || {};
    [
      ...(payload.evidence_spans || []),
      ...(payload.superseded_evidence_spans || []),
    ].forEach((evidence) => {
      const evidenceId = String(evidence.id || "");
      if (!evidenceId) {
        return;
      }
      if (String(evidence.status || "active") === "active") {
        activeEvidenceIds.add(evidenceId);
      } else {
        activeEvidenceIds.delete(evidenceId);
      }
    });
  });
  const segmentFinalTimes = Object.fromEntries(
    events
      .filter((event) => event.event_type === "transcript_final" || event.event_type === "transcript_revision")
      .map((event) => [String(event.payload?.segment_id || ""), Number(event.at_ms || 0)]),
  );
  const stateEventTimes = Object.fromEntries(
    events
      .filter((event) => event.event_type === "state_event")
      .map((event) => [String(event.payload?.event_id || ""), Number(event.at_ms || 0)]),
  );
  const eligibleDraft = drafts.find((draft) => {
    const evidenceIds = draft.evidence_span_ids || [];
    const degradationReasons = draft.candidate_degradation_reasons || [];
    return (
      evidenceIds.length > 0
      && evidenceIds.every((evidenceId) => activeEvidenceIds.has(String(evidenceId)))
      && degradationReasons.length === 0
    );
  });
  const draft = eligibleDraft;
  if (!draft?.request_id) {
    return null;
  }
  const segmentBatch = (draft.segment_batch || []).map(String);
  const sourceEventIds = (draft.source_event_ids || []).map(String);
  const finalSegmentAtMs = Math.max(
    0,
    ...segmentBatch.map((segmentId) => segmentFinalTimes[segmentId] || 0),
  );
  const stateEventAtMs = Math.max(
    finalSegmentAtMs,
    ...sourceEventIds.map((eventId) => stateEventTimes[eventId] || 0),
  );
  const cardCreatedAtMs = stateEventAtMs + 200;
  const targetType = String(draft.target_type || "StateCandidate");
  const targetId = String(draft.target_id || "unknown");
  const requestId = String(draft.request_id);
  return {
    requestId,
    candidateResponse: {
      id: `card_readiness_probe_${safeToken(requestId)}`,
      type: cardTypeForTargetType(targetType),
      evidence_span_ids: (draft.evidence_span_ids || []).map(String),
      state_refs: [`${targetType}:${targetId}`],
      state_event_ids: sourceEventIds,
      gap_rule_id: String(draft.gap_rule_id || "state.candidate.review"),
      trigger_reason: "local card lifecycle readiness probe",
      trigger_source: "live_asr_readiness_ui_probe",
      final_segment_at_ms: finalSegmentAtMs,
      state_event_at_ms: stateEventAtMs,
      card_created_at_ms: cardCreatedAtMs,
      latency_ms: cardCreatedAtMs - finalSegmentAtMs,
      prompt_version: "suggestion-card-execution-preview.v1",
      model: "not_called",
      usage: {total_tokens: 0},
      schema_result: "valid",
      show_or_silence_decision: "show",
      segment_batch: segmentBatch,
      status: "new",
      title: "Readiness probe",
      suggested_question: String(draft.suggested_prompt || "确认该候选是否可以生成建议卡。"),
    },
  };
}

function cardTypeForTargetType(targetType) {
  void targetType;
  return "owner_gap";
}

function safeToken(value) {
  return String(value || "unknown").replace(/[^A-Za-z0-9_]+/g, "_");
}

function renderCardLifecycleReadinessEmpty(message = "Live ASR 结束后显示候选成卡链路状态。") {
  const panel = document.getElementById("card-lifecycle-readiness-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderCardLifecycleReadinessSummary(summary) {
  const panel = document.getElementById("card-lifecycle-readiness-panel");
  if (!panel) {
    return;
  }
  const phases = summary.card_lifecycle_summary_phases || [];
  const metrics = [
    ["Status", summary.card_lifecycle_overall_readiness_status],
    ["Phases", `${summary.card_lifecycle_summary_phase_count || phases.length} phases`],
    ["Source", summary.source_preflight_status],
    ["LLM", summary.llm_call_status],
    ["Config", summary.config_source_status],
    ["Credentials", summary.credentials_status],
    ["Events", summary.event_append_status],
    ["Audit", summary.audit_event_append_status],
    ["Idempotency", summary.idempotency_store_write_status],
    ["Commit", summary.repository_transaction_commit_status],
  ];
  const safeFlagEntries = [
    "card_lifecycle_safe_to_execute_llm",
    "card_lifecycle_safe_to_create_card",
    "card_lifecycle_safe_to_append_events",
    "card_lifecycle_safe_to_mutate_events",
    "card_lifecycle_safe_to_begin_transaction",
    "card_lifecycle_safe_to_commit_transaction",
    "card_lifecycle_safe_to_write_idempotency_store",
    "card_lifecycle_safe_to_persist_append_result_audit_event",
  ].map((key) => [key, summary[key]]);
  const blockReasons = summary.card_lifecycle_block_reasons || [];
  const nextDecisions = summary.card_lifecycle_next_required_decisions || [];
  const header = document.createElement("div");
  header.className = "lifecycle-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Response Only</p>
      <h3>${escapeHtml(summary.card_lifecycle_overall_readiness_status || "unknown")}</h3>
      <p>${escapeHtml(summary.source_preflight_kind || "")} · ${escapeHtml(summary.source_readiness_status || "")}</p>
    </div>
    <div class="lifecycle-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "lifecycle-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const reasons = document.createElement("div");
  reasons.className = "lifecycle-chip-group";
  reasons.innerHTML = [
    ...blockReasons.map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...nextDecisions.map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const phaseList = document.createElement("div");
  phaseList.className = "lifecycle-phase-list";
  phaseList.replaceChildren(
    ...phases.map((phase) => {
      const item = document.createElement("article");
      item.className = "lifecycle-phase";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(phase.phase_id)}</strong>
          <span>${escapeHtml(phase.phase_status)} · ${escapeHtml(phase.phase_mode)}</span>
        </div>
        <p>${escapeHtml(phase.source_status_field)}=${escapeHtml(phase.source_status_value)} · safe_to_write=${escapeHtml(phase.safe_to_write)} · items=${escapeHtml(phase.item_count)}</p>
      `;
      return item;
    }),
  );
  panel.replaceChildren(header, flags, reasons, phaseList);
}

async function loadLocalShadowPreviewReleaseReadiness() {
  try {
    const readiness = await requestJson("/desktop/local-shadow-preview-release-readiness");
    renderLocalShadowPreviewReleaseReadiness(readiness);
  } catch (error) {
    renderLocalShadowPreviewReleaseReadinessEmpty("本地预览发布状态读取失败。");
    showToast(error.message);
  }
}

function renderLocalShadowPreviewReleaseReadinessEmpty(message = "正在读取本地预览发布状态。") {
  const panel = document.getElementById("local-shadow-preview-release-content");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderLocalShadowPreviewReleaseReadiness(payload) {
  const panel = document.getElementById("local-shadow-preview-release-content");
  if (!panel) {
    return;
  }
  const blockers = payload.release_blockers || [];
  const flags = payload.safety_flags || {};
  const metrics = [
    ["Preview", payload.demo_preview_ready ? "Ready" : "Blocked", ""],
    ["Shadow Pilot", payload.shadow_pilot_ready ? "Ready" : "Blocked", "blocked"],
    ["Production MVP", payload.production_mvp_ready ? "Ready" : "Blocked", "blocked"],
    ["ASR", payload.asr_quality_exit_status, ""],
    ["Real Mic", payload.real_mic_readiness_status, "blocked"],
    ["LLM", payload.llm_execution_status, "blocked"],
    ["Cards", payload.formal_card_status, "blocked"],
    ["Report", payload.formal_report_status, "blocked"],
  ];
  panel.innerHTML = `
    ${metrics.map(([label, value, stateClass]) => `
      <article class="release-summary-item ${stateClass}">
        <span class="label">${escapeHtml(label)}</span>
        <strong>${escapeHtml(value ?? "unknown")}</strong>
      </article>
    `).join("")}
    <div class="release-summary-wide">
      <span class="label">Allowed Claim</span>
      <p>${escapeHtml(payload.allowed_claim || "")}</p>
    </div>
    <div class="release-summary-wide">
      <span class="label">Blockers</span>
      <ul>${blockers.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
    </div>
    <div class="release-summary-wide">
      <span class="label">Safety</span>
      <div class="release-summary-flags">
        ${Object.entries(flags).map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`).join("")}
      </div>
    </div>
  `;
}

async function loadDesktopShellReadiness() {
  try {
    const readiness = await requestJson("/desktop/shell-readiness");
    renderDesktopShellReadiness(readiness);
  } catch (error) {
    renderDesktopShellReadinessEmpty("桌面准备度读取失败。");
    showToast(error.message);
  }
}

function renderDesktopShellReadinessEmpty(message = "正在读取桌面准备度边界。") {
  const panel = document.getElementById("desktop-readiness-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopShellReadiness(readiness) {
  const panel = document.getElementById("desktop-readiness-panel");
  if (!panel) {
    return;
  }
  const phases = readiness.desktop_readiness_phases || [];
  const metrics = [
    ["Status", readiness.desktop_readiness_status],
    ["Phases", `${readiness.desktop_readiness_phase_count || phases.length} phases`],
    ["Shell", readiness.desktop_shell_status],
    ["Target", readiness.target_platform_status],
    ["Audio", readiness.audio_capture_status],
    ["Mic", readiness.microphone_permission_status],
    ["System", readiness.system_audio_permission_status],
    ["ASR", readiness.asr_worker_status],
    ["LLM", readiness.llm_provider_status],
    ["Data", readiness.local_data_dir_status],
  ];
  const safeFlagEntries = [
    "desktop_safe_to_capture_audio",
    "desktop_safe_to_request_permissions",
    "desktop_safe_to_start_asr_worker",
    "desktop_safe_to_call_remote_asr",
    "desktop_safe_to_call_llm",
    "desktop_safe_to_write_audio_chunks",
  ].map((key) => [key, readiness[key]]);
  const blockers = readiness.desktop_readiness_blockers || [];
  const nextDecisions = readiness.desktop_readiness_next_decisions || [];
  const header = document.createElement("div");
  header.className = "desktop-readiness-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Preflight Only</p>
      <h3>${escapeHtml(readiness.desktop_readiness_status || "unknown")}</h3>
      <p>${escapeHtml(readiness.desktop_readiness_mode || "")} · ${escapeHtml(readiness.target_platform_status || "")}</p>
    </div>
    <div class="desktop-readiness-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-readiness-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const reasons = document.createElement("div");
  reasons.className = "desktop-readiness-chip-group";
  reasons.innerHTML = [
    ...blockers.map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...nextDecisions.map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const phaseList = document.createElement("div");
  phaseList.className = "desktop-phase-list";
  phaseList.replaceChildren(
    ...phases.map((phase) => {
      const item = document.createElement("article");
      item.className = "desktop-phase";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(phase.phase_id)}</strong>
          <span>${escapeHtml(phase.phase_status)} · ${escapeHtml(phase.phase_mode)}</span>
        </div>
        <p>${escapeHtml(phase.source_status_field)}=${escapeHtml(phase.source_status_value)} · safe_to_proceed=${escapeHtml(phase.safe_to_proceed)} · items=${escapeHtml(phase.item_count)}</p>
      `;
      return item;
    }),
  );
  panel.replaceChildren(header, flags, reasons, phaseList);
}

async function loadDesktopRuntimeBoundary() {
  try {
    const boundary = await requestJson("/desktop/runtime-boundary");
    renderDesktopRuntimeBoundary(boundary);
  } catch (error) {
    renderDesktopRuntimeBoundaryEmpty("桌面运行时边界读取失败。");
    showToast(error.message);
  }
}

function renderDesktopRuntimeBoundaryEmpty(message = "正在读取桌面运行时决策边界。") {
  const panel = document.getElementById("desktop-runtime-boundary-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopRuntimeBoundary(boundary) {
  const panel = document.getElementById("desktop-runtime-boundary-panel");
  if (!panel) {
    return;
  }
  const phases = boundary.desktop_runtime_phases || [];
  const metrics = [
    ["Status", boundary.desktop_runtime_boundary_status],
    ["Phases", `${boundary.desktop_runtime_phase_count || phases.length} phases`],
    ["Runtime", boundary.recommended_desktop_runtime],
    ["Decision", boundary.desktop_runtime_decision_status],
    ["Process", boundary.desktop_process_model_status],
    ["UI", boundary.ui_reuse_status],
    ["Core", boundary.core_isolation_status],
    ["Bridge", boundary.native_bridge_status],
    ["ASR", boundary.asr_worker_process_model],
    ["macOS", boundary.macos_target_status],
    ["Windows", boundary.windows_target_status],
    ["Packaging", boundary.packaging_pipeline_status],
  ];
  const safeFlagEntries = [
    "desktop_runtime_safe_to_create_shell",
    "desktop_runtime_safe_to_start_native_bridge",
    "desktop_runtime_safe_to_spawn_worker",
    "desktop_runtime_safe_to_package_installer",
    "desktop_runtime_safe_to_request_permissions",
    "desktop_runtime_safe_to_capture_audio",
    "desktop_runtime_safe_to_call_remote_asr",
    "desktop_runtime_safe_to_call_llm",
  ].map((key) => [key, boundary[key]]);
  const blockers = boundary.desktop_runtime_blockers || [];
  const nextDecisions = boundary.desktop_runtime_next_decisions || [];
  const header = document.createElement("div");
  header.className = "desktop-runtime-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Decision Preflight</p>
      <h3>${escapeHtml(boundary.desktop_runtime_boundary_status || "unknown")}</h3>
      <p>${escapeHtml(boundary.desktop_runtime_mode || "")} · ${escapeHtml(boundary.recommended_desktop_runtime || "")}</p>
    </div>
    <div class="desktop-runtime-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-runtime-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const reasons = document.createElement("div");
  reasons.className = "desktop-runtime-chip-group";
  reasons.innerHTML = [
    ...blockers.map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...nextDecisions.map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const phaseList = document.createElement("div");
  phaseList.className = "desktop-runtime-phase-list";
  phaseList.replaceChildren(
    ...phases.map((phase) => {
      const item = document.createElement("article");
      item.className = "desktop-runtime-phase";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(phase.phase_id)}</strong>
          <span>${escapeHtml(phase.phase_status)} · ${escapeHtml(phase.phase_mode)}</span>
        </div>
        <p>${escapeHtml(phase.source_status_field)}=${escapeHtml(phase.source_status_value)} · safe_to_proceed=${escapeHtml(phase.safe_to_proceed)} · items=${escapeHtml(phase.item_count)}</p>
      `;
      return item;
    }),
  );
  panel.replaceChildren(header, flags, reasons, phaseList);
}

async function loadDesktopNativeBridgeContract() {
  try {
    const contract = await requestJson("/desktop/native-bridge-contract");
    renderDesktopNativeBridgeContract(contract);
  } catch (error) {
    renderDesktopNativeBridgeContractEmpty("桌面原生桥契约读取失败。");
    showToast(error.message);
  }
}

function renderDesktopNativeBridgeContractEmpty(message = "正在读取桌面原生桥契约。") {
  const panel = document.getElementById("desktop-native-bridge-contract-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopNativeBridgeContract(contract) {
  const panel = document.getElementById("desktop-native-bridge-contract-panel");
  if (!panel) {
    return;
  }
  const commands = contract.desktop_bridge_commands || [];
  const phases = contract.desktop_bridge_phases || [];
  const resourcePolicy = contract.desktop_bridge_resource_policy || {};
  const errorContract = contract.desktop_bridge_error_contract || {};
  const metrics = [
    ["Status", contract.desktop_bridge_contract_status],
    ["Commands", `${contract.desktop_bridge_command_count || commands.length} commands`],
    ["Phases", `${contract.desktop_bridge_phase_count || phases.length} phases`],
    ["Bridge", contract.native_bridge_status],
    ["Runtime", contract.desktop_shell_runtime_status],
    ["Transport", contract.bridge_transport_status],
    ["Lifecycle", contract.bridge_process_lifecycle_status],
    ["Resources", contract.bridge_resource_policy_status],
    ["Errors", contract.bridge_error_contract_status],
    ["Audit", contract.bridge_audit_contract_status],
  ];
  const safeFlagEntries = [
    "desktop_bridge_safe_to_create_native_bridge",
    "desktop_bridge_safe_to_bind_ipc",
    "desktop_bridge_safe_to_invoke_commands",
    "desktop_bridge_safe_to_request_permissions",
    "desktop_bridge_safe_to_enumerate_devices",
    "desktop_bridge_safe_to_capture_audio",
    "desktop_bridge_safe_to_spawn_worker",
    "desktop_bridge_safe_to_write_local_files",
    "desktop_bridge_safe_to_call_remote_asr",
    "desktop_bridge_safe_to_call_llm",
  ].map((key) => [key, contract[key]]);
  const blockers = contract.desktop_bridge_blockers || [];
  const nextDecisions = contract.desktop_bridge_next_decisions || [];
  const header = document.createElement("div");
  header.className = "desktop-bridge-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Contract Preflight</p>
      <h3>${escapeHtml(contract.desktop_bridge_contract_status || "unknown")}</h3>
      <p>${escapeHtml(contract.desktop_bridge_contract_mode || "")} · ${escapeHtml(contract.bridge_command_contract_status || "")}</p>
    </div>
    <div class="desktop-bridge-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-bridge-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const reasons = document.createElement("div");
  reasons.className = "desktop-bridge-chip-group";
  reasons.innerHTML = [
    ...blockers.map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...nextDecisions.map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const phaseList = document.createElement("div");
  phaseList.className = "desktop-bridge-phase-list";
  phaseList.replaceChildren(
    ...phases.map((phase) => {
      const item = document.createElement("article");
      item.className = "desktop-bridge-phase";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(phase.phase_id)}</strong>
          <span>${escapeHtml(phase.phase_status)} · ${escapeHtml(phase.phase_mode)}</span>
        </div>
        <p>${escapeHtml(phase.source_status_field)}=${escapeHtml(phase.source_status_value)} · safe_to_proceed=${escapeHtml(phase.safe_to_proceed)} · items=${escapeHtml(phase.item_count)}</p>
      `;
      return item;
    }),
  );
  const commandList = document.createElement("div");
  commandList.className = "desktop-bridge-command-list";
  commandList.replaceChildren(
    ...commands.map((command) => {
      const item = document.createElement("article");
      item.className = "desktop-bridge-command";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(command.command_id)}</strong>
          <span>${escapeHtml(command.command_group)} · ${escapeHtml(command.effect_class)}</span>
        </div>
        <p>${escapeHtml(command.command_status)} · ${escapeHtml(command.implementation_status)} · ${escapeHtml(command.transport_status)} · safe_to_execute_now=${escapeHtml(command.safe_to_execute_now)}</p>
        <p>reads=${escapeHtml((command.read_set || []).join(", ") || "none")} · writes=${escapeHtml((command.write_set || []).join(", ") || "none")}</p>
      `;
      return item;
    }),
  );
  const policy = document.createElement("div");
  policy.className = "desktop-bridge-chip-group";
  policy.innerHTML = [
    `secret_redaction_policy=${errorContract.secret_redaction_policy}`,
    `retry_policy=${errorContract.retry_policy}`,
    `worker_spawn_status=${resourcePolicy.worker_spawn_status}`,
    `audio_buffer_retention=${resourcePolicy.audio_buffer_retention}`,
    `payload_size_limit_kb=${resourcePolicy.payload_size_limit_kb}`,
  ].map((entry) => `<span class="chip">${escapeHtml(entry)}</span>`).join("");
  panel.replaceChildren(header, flags, reasons, phaseList, commandList, policy);
}

function getTauriInvoke() {
  const tauri = window.__TAURI__;
  if (!tauri) {
    return null;
  }
  if (tauri.core && typeof tauri.core.invoke === "function") {
    return tauri.core.invoke.bind(tauri.core);
  }
  if (tauri.tauri && typeof tauri.tauri.invoke === "function") {
    return tauri.tauri.invoke.bind(tauri.tauri);
  }
  return null;
}

async function loadDesktopNativeRuntime() {
  const invoke = getTauriInvoke();
  if (!invoke) {
    renderDesktopNativeRuntime({
      runtime_environment: "browser_fallback",
      native_runtime_status: "not_available",
      bridge_transport_status: "not_connected",
      command_results: [],
      message: "Tauri IPC is not available in this browser context.",
      safe_to_capture_audio: false,
      safe_to_spawn_process: false,
      safe_to_call_remote_provider: false,
      safe_to_write_local_files: false,
    });
    return;
  }

  const commandNames = [
    ["runtime.get_status", "runtime_get_status"],
    ["session.prepare", "session_prepare"],
    ["asr_worker.health", "asr_worker_health"],
  ];
  const commandResults = [];
  for (const [commandId, commandName] of commandNames) {
    try {
      const result = await invoke(commandName);
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "returned",
        result,
      });
    } catch (error) {
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "failed",
        error_message: error && error.message ? error.message : String(error),
      });
    }
  }
  renderDesktopNativeRuntime({
    runtime_environment: "tauri",
    native_runtime_status: "available",
    bridge_transport_status: "tauri_ipc_invoked",
    command_results: commandResults,
    message: "Tauri no-op IPC commands were invoked.",
    safe_to_capture_audio: false,
    safe_to_spawn_process: false,
    safe_to_call_remote_provider: false,
    safe_to_write_local_files: false,
  });
}

function renderDesktopNativeRuntimeEmpty(message = "正在检测 Tauri 原生运行时。") {
  const panel = document.getElementById("desktop-native-runtime-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopNativeRuntime(runtime) {
  const panel = document.getElementById("desktop-native-runtime-panel");
  if (!panel) {
    return;
  }
  const commandResults = runtime.command_results || [];
  const metrics = [
    ["Runtime", runtime.runtime_environment],
    ["Native", runtime.native_runtime_status],
    ["Transport", runtime.bridge_transport_status],
    ["Commands", `${commandResults.length} invoked`],
  ];
  const safeFlagEntries = [
    "safe_to_capture_audio",
    "safe_to_spawn_process",
    "safe_to_call_remote_provider",
    "safe_to_write_local_files",
  ].map((key) => [key, runtime[key]]);
  const header = document.createElement("div");
  header.className = "desktop-native-runtime-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Runtime Probe</p>
      <h3>${escapeHtml(runtime.native_runtime_status || "unknown")}</h3>
      <p>${escapeHtml(runtime.message || "")}</p>
    </div>
    <div class="desktop-native-runtime-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-native-runtime-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const commandList = document.createElement("div");
  commandList.className = "desktop-native-runtime-command-list";
  if (commandResults.length === 0) {
    commandList.innerHTML = `<div class="empty">当前不是 Tauri 运行环境，未调用 native IPC。</div>`;
  } else {
    commandList.replaceChildren(
      ...commandResults.map((command) => {
        const item = document.createElement("article");
        item.className = "desktop-native-runtime-command";
        const result = command.result || {};
        item.innerHTML = `
          <div>
            <strong>${escapeHtml(command.command_id)}</strong>
            <span>${escapeHtml(command.invoke_status)}</span>
          </div>
          <p>${escapeHtml(result.command_status || command.error_message || "no result")}</p>
          <p>real_action=${escapeHtml(result.safe_to_execute_real_action)} · audio=${escapeHtml(result.captures_audio)} · remote=${escapeHtml(result.calls_remote_provider)} · writes=${escapeHtml(result.writes_local_files)}</p>
        `;
        return item;
      }),
    );
  }
  panel.replaceChildren(header, flags, commandList);
}

async function loadDesktopAsrHandoffDryRunReadiness() {
  try {
    const readiness = await requestJson("/desktop/asr-worker-handoff-dry-run-readiness");
    renderDesktopAsrHandoffDryRunReadiness(readiness);
  } catch (error) {
    renderDesktopAsrHandoffDryRunReadinessEmpty("ASR worker dry-run 状态读取失败。");
    showToast(error.message);
  }
}

function renderDesktopAsrHandoffDryRunReadinessEmpty(message = "正在读取 ASR worker dry-run 状态。") {
  const panel = document.getElementById("desktop-asr-handoff-dry-run-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopAsrHandoffDryRunReadiness(readiness) {
  const panel = document.getElementById("desktop-asr-handoff-dry-run-panel");
  if (!panel) {
    return;
  }
  const phases = readiness.desktop_asr_handoff_phases || [];
  const metrics = [
    ["Status", readiness.desktop_asr_worker_handoff_dry_run_status],
    ["Phases", `${readiness.desktop_asr_handoff_phase_count || phases.length} phases`],
    ["Default", readiness.pcweb_096_default_dry_run_status],
    ["Synthetic", readiness.synthetic_local_test_status],
    ["Worker", readiness.worker_execution_status],
    ["Event file", readiness.event_file_read_status],
    ["Web session", readiness.web_handoff_mutation_status],
    ["Next", readiness.next_pcweb_id],
  ];
  const safeFlagEntries = [
    "desktop_asr_handoff_safe_to_start_worker",
    "desktop_asr_handoff_safe_to_capture_audio",
    "desktop_asr_handoff_safe_to_read_real_audio",
    "desktop_asr_handoff_safe_to_read_configs_local",
    "desktop_asr_handoff_safe_to_call_remote_asr",
    "desktop_asr_handoff_safe_to_call_llm",
    "desktop_asr_handoff_safe_to_download_models",
    "desktop_asr_handoff_safe_to_run_tauri_or_cargo",
    "desktop_asr_handoff_safe_to_mutate_web_session_now",
  ].map((key) => [key, readiness[key]]);
  const blockers = readiness.desktop_asr_handoff_blockers || [];
  const nextDecisions = readiness.desktop_asr_handoff_next_decisions || [];
  const header = document.createElement("div");
  header.className = "desktop-asr-handoff-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">Readiness Only</p>
      <h3>${escapeHtml(readiness.desktop_asr_worker_handoff_dry_run_status || "unknown")}</h3>
      <p>${escapeHtml(readiness.desktop_asr_worker_handoff_dry_run_mode || "")} · ${escapeHtml(readiness.handoff_api_endpoint || "")}</p>
    </div>
    <div class="desktop-asr-handoff-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const roots = document.createElement("div");
  roots.className = "desktop-asr-handoff-chip-group";
  roots.innerHTML = [
    `event_root=${readiness.approved_event_file_root}`,
    `temp_data_root=${readiness.approved_temp_web_data_dir_root}`,
    `pcweb=${readiness.pcweb_id}`,
  ].map((entry) => `<span class="chip">${escapeHtml(entry)}</span>`).join("");
  const flags = document.createElement("div");
  flags.className = "desktop-asr-handoff-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const reasons = document.createElement("div");
  reasons.className = "desktop-asr-handoff-chip-group";
  reasons.innerHTML = [
    ...blockers.map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...nextDecisions.map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const phaseList = document.createElement("div");
  phaseList.className = "desktop-asr-handoff-phase-list";
  phaseList.replaceChildren(
    ...phases.map((phase) => {
      const item = document.createElement("article");
      item.className = "desktop-asr-handoff-phase";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(phase.phase_id)}</strong>
          <span>${escapeHtml(phase.phase_status)} · ${escapeHtml(phase.phase_mode)}</span>
        </div>
        <p>${escapeHtml(phase.source_status_field)}=${escapeHtml(phase.source_status_value)} · safe_to_proceed=${escapeHtml(phase.safe_to_proceed)} · items=${escapeHtml(phase.item_count)}</p>
      `;
      return item;
    }),
  );
  panel.replaceChildren(header, roots, flags, reasons, phaseList);
}

async function loadDesktopMicAdapterContractReadiness() {
  try {
    const readiness = await requestJson("/desktop/mic-adapter-contract-readiness");
    renderDesktopMicAdapterContractReadiness(readiness);
  } catch (error) {
    renderDesktopMicAdapterContractReadinessEmpty("麦克风 adapter 合同状态读取失败。");
    showToast(error.message);
  }
}

function renderDesktopMicAdapterContractReadinessEmpty(message = "正在读取麦克风 adapter 合同。") {
  const panel = document.getElementById("desktop-mic-adapter-contract-panel");
  if (!panel) {
    return;
  }
  desktopMicAdapterContractState = null;
  desktopMicAdapterNoopInvocationState = null;
  desktopTauriNoopRunResultCollectorState = null;
  desktopTauriNoopRunResultValidationState = null;
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopMicAdapterContractReadiness(readiness) {
  desktopMicAdapterContractState = readiness;
  renderDesktopMicAdapterPanel();
}

async function loadDesktopRealMicShadowTestReadiness() {
  try {
    const readiness = await requestJson("/desktop/real-mic-shadow-test-readiness");
    renderDesktopRealMicShadowTestReadiness(readiness);
  } catch (error) {
    renderDesktopRealMicShadowTestReadinessEmpty("真实会议验收状态读取失败。");
    showToast(error.message);
  }
}

function renderDesktopRealMicShadowTestReadinessEmpty(message = "正在读取真实麦克风 shadow test 准备状态。") {
  const panel = document.getElementById("desktop-real-mic-shadow-readiness-panel");
  if (!panel) {
    return;
  }
  panel.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
}

function renderDesktopRealMicShadowTestReadiness(readiness) {
  const panel = document.getElementById("desktop-real-mic-shadow-readiness-panel");
  if (!panel) {
    return;
  }
  const summary = readiness.readiness_summary || {};
  const protocol = readiness.pilot_protocol || {};
  const blockers = readiness.blockers || [];
  const nextActions = readiness.allowed_next_actions || [];
  const exportItems = protocol.required_export || [];
  const metrics = [
    ["Status", readiness.readiness_status],
    ["Can start", readiness.user_can_start_real_mic_shadow_test_now],
    ["ASR", readiness.asr_quality_exit_status],
    ["Go evidence", readiness.asr_quality_counts_as_go_evidence],
    ["Worker", readiness.worker_mic_source_approval_status],
    ["Tauri", readiness.tauri_noop_evidence_status],
    ["Mic", readiness.mic_adapter_implementation_status],
    ["ASR worker", readiness.asr_worker_implementation_status],
    ["Export", readiness.export_feedback_status],
    ["Mode", readiness.readiness_mode],
  ];
  const safeFlagEntries = [
    "safe_to_access_microphone_from_gate_now",
    "safe_to_request_audio_permission_from_gate_now",
    "safe_to_read_real_user_audio_from_gate_now",
    "safe_to_write_audio_chunk_from_gate_now",
    "safe_to_spawn_worker_from_gate_now",
    "safe_to_run_tauri_or_cargo_from_gate_now",
    "safe_to_read_configs_local_from_gate_now",
    "safe_to_call_remote_asr_from_gate_now",
    "safe_to_call_llm_from_gate_now",
    "safe_to_download_models_from_gate_now",
    "safe_to_download_public_audio_from_gate_now",
  ].map((key) => [key, readiness[key]]);
  const header = document.createElement("div");
  header.className = "desktop-real-mic-shadow-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">PCWEB-115</p>
      <h3>${escapeHtml(readiness.readiness_status || "unknown")}</h3>
      <p>${escapeHtml(readiness.next_required_decision || "")} · user_start=${escapeHtml(protocol.user_start_required)}</p>
    </div>
    <div class="desktop-real-mic-shadow-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const summaryChips = document.createElement("div");
  summaryChips.className = "desktop-real-mic-shadow-chip-group";
  summaryChips.innerHTML = Object.entries(summary)
    .map(([key, value]) => `<span class="chip">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const flags = document.createElement("div");
  flags.className = "desktop-real-mic-shadow-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const blockerList = document.createElement("div");
  blockerList.className = "desktop-real-mic-shadow-blocker-list";
  blockerList.replaceChildren(
    ...blockers.map((reason) => {
      const item = document.createElement("article");
      item.className = "desktop-real-mic-shadow-blocker";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(reason)}</strong>
          <span>blocked</span>
        </div>
      `;
      return item;
    }),
  );
  const actions = document.createElement("div");
  actions.className = "desktop-real-mic-shadow-chip-group";
  actions.innerHTML = nextActions
    .map((action) => `<span class="chip">${escapeHtml(action)}</span>`)
    .join("");
  const exports = document.createElement("div");
  exports.className = "desktop-real-mic-shadow-chip-group";
  exports.innerHTML = [
    `duration=${protocol.meeting_duration_minutes}`,
    `meeting=${protocol.meeting_type}`,
    `raw_audio_upload_default=${protocol.raw_audio_upload_default}`,
    `remote_asr_default=${protocol.remote_asr_default}`,
    ...exportItems.map((item) => `export=${item}`),
  ].map((entry) => `<span class="chip">${escapeHtml(entry)}</span>`).join("");
  panel.replaceChildren(header, summaryChips, flags, blockerList, actions, exports);
}

async function loadDesktopMicAdapterNoopInvocation() {
  const invoke = getTauriInvoke();
  if (!invoke) {
    renderDesktopMicAdapterNoopInvocation({
      invocation_environment: "browser_fallback",
      invocation_status: "mic_adapter_browser_fallback",
      transport_status: "not_available",
      command_results: desktopMicAdapterNoopCommands.map(([commandId, commandName]) => ({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "not_invoked",
        result: {
          command_id: commandId,
          command_status: "mic_adapter_browser_fallback",
          safe_to_invoke_noop: false,
          safe_to_execute_real_action: false,
          captures_audio: false,
          spawns_process: false,
          calls_remote_provider: false,
          writes_local_files: false,
        },
      })),
      safe_to_request_audio_permission_now: false,
      safe_to_capture_audio_now: false,
      safe_to_write_audio_chunk_now: false,
      safe_to_delete_audio_chunks_now: false,
      safe_to_call_remote_asr_now: false,
      safe_to_call_llm_now: false,
      safe_to_run_tauri_or_cargo_now: false,
    });
    return;
  }

  const commandResults = [];
  for (const [commandId, commandName] of desktopMicAdapterNoopCommands) {
    try {
      const result = await invoke(commandName);
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "returned",
        result,
      });
    } catch (error) {
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "failed",
        error_message: error && error.message ? error.message : String(error),
      });
    }
  }
  renderDesktopMicAdapterNoopInvocation({
    invocation_environment: "tauri",
    invocation_status: "mic_adapter_noop_ipc_invoked",
    transport_status: "tauri_ipc_invoked",
    command_results: commandResults,
    safe_to_request_audio_permission_now: false,
    safe_to_capture_audio_now: false,
    safe_to_write_audio_chunk_now: false,
    safe_to_delete_audio_chunks_now: false,
    safe_to_call_remote_asr_now: false,
    safe_to_call_llm_now: false,
    safe_to_run_tauri_or_cargo_now: false,
  });
}

function renderDesktopMicAdapterNoopInvocation(invocation) {
  desktopMicAdapterNoopInvocationState = invocation;
  renderDesktopMicAdapterPanel();
}

function desktopNoopFallbackCommandResult(commandId, commandName) {
  return {
    command_id: commandId,
    command_name: commandName,
    invoke_status: "not_invoked",
    result: {
      command_id: commandId,
      command_status: "collector_browser_fallback",
      implementation_status: "noop_only",
      transport_status: "not_available",
      side_effect_status: "none",
      safe_to_invoke_noop: false,
      safe_to_execute_real_action: false,
      captures_audio: false,
      spawns_process: false,
      calls_remote_provider: false,
      writes_local_files: false,
    },
  };
}

function buildDesktopTauriNoopRunResult({
  runEnvironment,
  explicitTauriRunApprovalRecorded,
  webAppUrlStatus,
  ipcTransportStatus,
  commandResults,
}) {
  return {
    run_result_version: "desktop_tauri_noop_run_result.v1",
    run_id: runEnvironment === "tauri_webview"
      ? `tauri-noop-webview-${new Date().toISOString().replace(/[:.]/g, "-")}`
      : "browser-fallback-not-a-real-tauri-run",
    run_environment: runEnvironment,
    explicit_tauri_run_approval_recorded: explicitTauriRunApprovalRecorded,
    web_app_url_status: webAppUrlStatus,
    ipc_transport_status: ipcTransportStatus,
    command_results: commandResults,
  };
}

async function loadDesktopTauriNoopRunResultCollector() {
  const invoke = getTauriInvoke();
  if (!invoke) {
    const runResult = buildDesktopTauriNoopRunResult({
      runEnvironment: "browser_fallback",
      explicitTauriRunApprovalRecorded: false,
      webAppUrlStatus: "local_workbench_loaded",
      ipcTransportStatus: "not_available",
      commandResults: desktopTauriNoopRunCommands.map(([commandId, commandName]) =>
        desktopNoopFallbackCommandResult(commandId, commandName),
      ),
    });
    renderDesktopTauriNoopRunResultCollector({
      pcweb_id: "PCWEB-116",
      collector_status: "collector_browser_fallback",
      collector_mode: "webview_result_collector_no_file_write",
      result_schema_version: runResult.run_result_version,
      real_tauri_noop_result_ready: false,
      run_result: runResult,
      safe_to_request_audio_permission_now: false,
      safe_to_capture_audio_now: false,
      safe_to_start_asr_worker_now: false,
      safe_to_read_audio_chunk_now: false,
      safe_to_write_audio_chunk_now: false,
      safe_to_read_worker_event_file_now: false,
      safe_to_write_worker_event_file_now: false,
      safe_to_call_remote_asr_now: false,
      safe_to_call_llm_now: false,
      safe_to_run_tauri_or_cargo_now: false,
    });
    renderDesktopTauriNoopRunResultValidation({
      pcweb_id: "PCWEB-117",
      pcweb_117_validation_status: "not_submitted",
      validation_status: "validation_browser_fallback",
      validation_mode: "browser_fallback_no_submit",
      result_validation_status: "not_submitted",
      tauri_noop_run_result_status: "not_validated",
      real_tauri_noop_run_evidence_status: "not_available",
      validated_command_count: 0,
      returned_command_count: 0,
      safe_to_request_audio_permission_now: false,
      safe_to_capture_audio_now: false,
      safe_to_start_asr_worker_now: false,
      safe_to_read_audio_chunk_now: false,
      safe_to_write_audio_chunk_now: false,
      safe_to_read_worker_event_file_now: false,
      safe_to_write_worker_event_file_now: false,
      safe_to_call_remote_asr_now: false,
      safe_to_call_llm_now: false,
      safe_to_run_tauri_or_cargo_now: false,
    });
    return;
  }

  const commandResults = [];
  for (const [commandId, commandName] of desktopTauriNoopRunCommands) {
    try {
      const result = await invoke(commandName);
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "returned",
        result,
      });
    } catch (error) {
      commandResults.push({
        command_id: commandId,
        command_name: commandName,
        invoke_status: "failed",
        error_message: error && error.message ? error.message : String(error),
      });
    }
  }
  const allReturned = commandResults.every((command) => command.invoke_status === "returned");
  const runResult = buildDesktopTauriNoopRunResult({
    runEnvironment: "tauri_webview",
    explicitTauriRunApprovalRecorded: true,
    webAppUrlStatus: "local_dev_url_loaded",
    ipcTransportStatus: "tauri_ipc_available",
    commandResults,
  });
  window.__meetingCopilotTauriNoopRunResult = runResult;
  renderDesktopTauriNoopRunResultCollector({
    pcweb_id: "PCWEB-116",
    collector_status: allReturned
      ? "collector_tauri_noop_result_collected"
      : "collector_tauri_noop_result_failed",
    collector_mode: "webview_result_collector_no_file_write",
    result_schema_version: runResult.run_result_version,
    real_tauri_noop_result_ready: allReturned,
    run_result: runResult,
    safe_to_request_audio_permission_now: false,
    safe_to_capture_audio_now: false,
    safe_to_start_asr_worker_now: false,
    safe_to_read_audio_chunk_now: false,
    safe_to_write_audio_chunk_now: false,
    safe_to_read_worker_event_file_now: false,
    safe_to_write_worker_event_file_now: false,
    safe_to_call_remote_asr_now: false,
    safe_to_call_llm_now: false,
    safe_to_run_tauri_or_cargo_now: false,
  });
  if (allReturned) {
    await validateDesktopTauriNoopRunResult(runResult);
  } else {
    renderDesktopTauriNoopRunResultValidation({
      pcweb_id: "PCWEB-117",
      pcweb_117_validation_status: "not_submitted_failed_collector",
      validation_status: "collector_failed_not_submitted",
      validation_mode: "webview_result_validation_no_file_write",
      result_validation_status: "not_submitted",
      tauri_noop_run_result_status: "not_validated",
      real_tauri_noop_run_evidence_status: "blocked",
      validated_command_count: 0,
      returned_command_count: commandResults.filter((command) => command.invoke_status === "returned").length,
      safe_to_request_audio_permission_now: false,
      safe_to_capture_audio_now: false,
      safe_to_start_asr_worker_now: false,
      safe_to_read_audio_chunk_now: false,
      safe_to_write_audio_chunk_now: false,
      safe_to_read_worker_event_file_now: false,
      safe_to_write_worker_event_file_now: false,
      safe_to_call_remote_asr_now: false,
      safe_to_call_llm_now: false,
      safe_to_run_tauri_or_cargo_now: false,
    });
  }
}

function renderDesktopTauriNoopRunResultCollector(collector) {
  desktopTauriNoopRunResultCollectorState = collector;
  renderDesktopMicAdapterPanel();
}

async function validateDesktopTauriNoopRunResult(runResult) {
  try {
    const validation = await requestJson("/desktop/tauri-noop-run-results/validations", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ run_result: runResult }),
    });
    renderDesktopTauriNoopRunResultValidation({
      pcweb_id: "PCWEB-117",
      pcweb_117_validation_status: "validated_by_pcweb_113",
      validation_status: "validated_noop_ipc_observed",
      validation_mode: "webview_result_validation_no_file_write",
      ...validation,
    });
  } catch (error) {
    renderDesktopTauriNoopRunResultValidation({
      pcweb_id: "PCWEB-117",
      pcweb_117_validation_status: "blocked_by_pcweb_113_validation",
      validation_status: "blocked_by_result_validation",
      validation_mode: "webview_result_validation_no_file_write",
      result_validation_status: "failed",
      tauri_noop_run_result_status: "blocked_by_result_validation",
      real_tauri_noop_run_evidence_status: "blocked",
      validated_command_count: 0,
      returned_command_count: 0,
      validation_error_message: error.message,
      safe_to_request_audio_permission_now: false,
      safe_to_capture_audio_now: false,
      safe_to_start_asr_worker_now: false,
      safe_to_read_audio_chunk_now: false,
      safe_to_write_audio_chunk_now: false,
      safe_to_read_worker_event_file_now: false,
      safe_to_write_worker_event_file_now: false,
      safe_to_call_remote_asr_now: false,
      safe_to_call_llm_now: false,
      safe_to_run_tauri_or_cargo_now: false,
    });
  }
}

function renderDesktopTauriNoopRunResultValidation(validation) {
  desktopTauriNoopRunResultValidationState = validation;
  renderDesktopMicAdapterPanel();
}

function renderDesktopMicAdapterPanel() {
  const panel = document.getElementById("desktop-mic-adapter-contract-panel");
  if (!panel) {
    return;
  }
  const nodes = [];
  if (desktopMicAdapterContractState) {
    nodes.push(...buildDesktopMicAdapterContractNodes(desktopMicAdapterContractState));
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "empty";
    placeholder.textContent = "正在读取麦克风 adapter 合同。";
    nodes.push(placeholder);
  }
  if (desktopMicAdapterNoopInvocationState) {
    nodes.push(...buildDesktopMicAdapterNoopInvocationNodes(desktopMicAdapterNoopInvocationState));
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "empty";
    placeholder.textContent = "正在检测麦克风 adapter no-op invocation。";
    nodes.push(placeholder);
  }
  if (desktopTauriNoopRunResultCollectorState) {
    nodes.push(...buildDesktopTauriNoopRunResultCollectorNodes(desktopTauriNoopRunResultCollectorState));
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "empty";
    placeholder.textContent = "正在生成 Tauri no-op run result collector。";
    nodes.push(placeholder);
  }
  if (desktopTauriNoopRunResultValidationState) {
    nodes.push(...buildDesktopTauriNoopRunResultValidationNodes(desktopTauriNoopRunResultValidationState));
  } else {
    const placeholder = document.createElement("div");
    placeholder.className = "empty";
    placeholder.textContent = "正在等待 Tauri no-op run result validation。";
    nodes.push(placeholder);
  }
  panel.replaceChildren(...nodes);
}

function buildDesktopMicAdapterContractNodes(readiness) {
  const commands = readiness.mic_adapter_command_catalog || [];
  const metrics = [
    ["Status", readiness.mic_adapter_ui_status],
    ["Contract", readiness.mic_adapter_contract_status],
    ["Commands", `${readiness.mic_adapter_command_count || commands.length} commands`],
    ["Permission", readiness.permission_request_status],
    ["Capture", readiness.audio_capture_status],
    ["Chunks", readiness.audio_chunk_write_status],
    ["Delete", readiness.audio_chunk_delete_status],
    ["Source", readiness.source_pcweb_id],
  ];
  const safeFlagEntries = [
    "safe_to_bind_mic_adapter_now",
    "safe_to_accept_mic_command_now",
    "safe_to_execute_mic_command_now",
    "safe_to_select_input_device_now",
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_recording_now",
    "safe_to_pause_recording_now",
    "safe_to_resume_recording_now",
    "safe_to_stop_recording_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_read_user_audio_now",
    "safe_to_read_configs_local_now",
    "safe_to_read_secret_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_download_models_now",
    "safe_to_mutate_web_session_now",
    "safe_to_run_tauri_or_cargo_now",
  ].map((key) => [key, readiness[key]]);
  const header = document.createElement("div");
  header.className = "desktop-mic-adapter-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">No-op Contract</p>
      <h3>${escapeHtml(readiness.mic_adapter_contract_status || "unknown")}</h3>
      <p>${escapeHtml(readiness.readiness_mode || "")} · ${escapeHtml(readiness.contract_version || "")}</p>
    </div>
    <div class="desktop-mic-adapter-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const roots = document.createElement("div");
  roots.className = "desktop-mic-adapter-chip-group";
  roots.innerHTML = [
    `runtime_root=${readiness.approved_runtime_audio_root}`,
    `chunk_root=${readiness.approved_audio_chunk_root}`,
    `start=${readiness.user_start_boundary}`,
    `delete=${readiness.delete_semantics}`,
  ].map((entry) => `<span class="chip">${escapeHtml(entry)}</span>`).join("");
  const flags = document.createElement("div");
  flags.className = "desktop-mic-adapter-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const blockers = document.createElement("div");
  blockers.className = "desktop-mic-adapter-chip-group";
  blockers.innerHTML = [
    ...(readiness.mic_adapter_readiness_blockers || []).map((reason) => `<span class="chip warning">${escapeHtml(reason)}</span>`),
    ...(readiness.mic_adapter_readiness_next_decisions || []).map((decision) => `<span class="chip">${escapeHtml(decision)}</span>`),
  ].join("");
  const commandList = document.createElement("div");
  commandList.className = "desktop-mic-adapter-command-list";
  commandList.replaceChildren(
    ...commands.map((command) => {
      const item = document.createElement("article");
      item.className = "desktop-mic-adapter-command";
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(command.command_id)}</strong>
          <span>${escapeHtml(command.requested_state_after)}</span>
        </div>
        <p>states=${escapeHtml((command.allowed_current_states || []).join(","))} · safe_to_execute_now=${escapeHtml(command.safe_to_execute_now)}</p>
      `;
      return item;
    }),
  );
  return [header, roots, flags, blockers, commandList];
}

function buildDesktopMicAdapterNoopInvocationNodes(invocation) {
  const commandResults = invocation.command_results || [];
  const metrics = [
    ["Environment", invocation.invocation_environment],
    ["Status", invocation.invocation_status],
    ["Transport", invocation.transport_status],
    ["Commands", `${commandResults.length} commands`],
  ];
  const safeFlagEntries = [
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_delete_audio_chunks_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
  ].map((key) => [key, invocation[key]]);
  const header = document.createElement("div");
  header.className = "desktop-mic-adapter-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">No-op Invocation</p>
      <h3>${escapeHtml(invocation.invocation_status || "unknown")}</h3>
      <p>${escapeHtml(invocation.invocation_environment || "")} · ${escapeHtml(invocation.transport_status || "")}</p>
    </div>
    <div class="desktop-mic-adapter-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-mic-adapter-chip-group";
  flags.innerHTML = safeFlagEntries
    .map(([key, value]) => `<span class="chip warning">${escapeHtml(key)}=${escapeHtml(value)}</span>`)
    .join("");
  const commandList = document.createElement("div");
  commandList.className = "desktop-mic-adapter-invoke-list";
  commandList.replaceChildren(
    ...commandResults.map((command) => {
      const item = document.createElement("article");
      item.className = "desktop-mic-adapter-invoke-command";
      const result = command.result || {};
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(command.command_id)}</strong>
          <span>${escapeHtml(command.command_name)} · ${escapeHtml(command.invoke_status)}</span>
        </div>
        <p>${escapeHtml(result.command_status || command.error_message || "no result")}</p>
        <p>noop=${escapeHtml(result.safe_to_invoke_noop)} · real_action=${escapeHtml(result.safe_to_execute_real_action)} · audio=${escapeHtml(result.captures_audio)} · remote=${escapeHtml(result.calls_remote_provider)} · writes=${escapeHtml(result.writes_local_files)}</p>
      `;
      return item;
    }),
  );
  return [header, flags, commandList];
}

function buildDesktopTauriNoopRunResultCollectorNodes(collector) {
  const runResult = collector.run_result || {};
  const commandResults = runResult.command_results || [];
  const metrics = [
    ["Status", collector.collector_status],
    ["Schema", collector.result_schema_version],
    ["Run env", runResult.run_environment],
    ["IPC", runResult.ipc_transport_status],
    ["Commands", `${commandResults.length} commands`],
  ];
  const safeFlagEntries = [
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
  ].map((key) => [key, collector[key]]);
  const header = document.createElement("div");
  header.className = "desktop-mic-adapter-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">No-op Result Collector</p>
      <h3>${escapeHtml(collector.collector_status || "unknown")}</h3>
      <p>${escapeHtml(collector.collector_mode || "")} · real_tauri_noop_result_ready=${escapeHtml(collector.real_tauri_noop_result_ready)}</p>
    </div>
    <div class="desktop-mic-adapter-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-mic-adapter-chip-group";
  flags.innerHTML = [
    `run_id=${runResult.run_id}`,
    `approval_recorded=${runResult.explicit_tauri_run_approval_recorded}`,
    `web_app=${runResult.web_app_url_status}`,
    ...safeFlagEntries.map(([key, value]) => `${key}=${value}`),
  ]
    .map((entry) => `<span class="chip warning">${escapeHtml(entry)}</span>`)
    .join("");
  const commandList = document.createElement("div");
  commandList.className = "desktop-tauri-noop-result-list";
  commandList.replaceChildren(
    ...commandResults.map((command) => {
      const item = document.createElement("article");
      item.className = "desktop-tauri-noop-result-command";
      const result = command.result || {};
      item.innerHTML = `
        <div>
          <strong>${escapeHtml(command.command_id)}</strong>
          <span>${escapeHtml(command.command_name)} · ${escapeHtml(command.invoke_status)}</span>
        </div>
        <p>${escapeHtml(result.command_status || command.error_message || "no result")} · ${escapeHtml(result.transport_status || runResult.ipc_transport_status || "unknown")}</p>
        <p>noop=${escapeHtml(result.safe_to_invoke_noop)} · real_action=${escapeHtml(result.safe_to_execute_real_action)} · audio=${escapeHtml(result.captures_audio)} · worker=${escapeHtml(result.spawns_process)} · remote=${escapeHtml(result.calls_remote_provider)} · writes=${escapeHtml(result.writes_local_files)}</p>
      `;
      return item;
    }),
  );
  return [header, flags, commandList];
}

function buildDesktopTauriNoopRunResultValidationNodes(validation) {
  const metrics = [
    ["Status", validation.pcweb_117_validation_status || validation.validation_status],
    ["Result", validation.result_validation_status],
    ["Evidence", validation.real_tauri_noop_run_evidence_status],
    ["Commands", `${validation.validated_command_count || 0} validated`],
    ["Returned", `${validation.returned_command_count || 0} returned`],
  ];
  const safeFlagEntries = [
    "safe_to_request_audio_permission_now",
    "safe_to_capture_audio_now",
    "safe_to_start_asr_worker_now",
    "safe_to_read_audio_chunk_now",
    "safe_to_write_audio_chunk_now",
    "safe_to_read_worker_event_file_now",
    "safe_to_write_worker_event_file_now",
    "safe_to_call_remote_asr_now",
    "safe_to_call_llm_now",
    "safe_to_run_tauri_or_cargo_now",
  ].map((key) => [key, validation[key]]);
  const header = document.createElement("div");
  header.className = "desktop-tauri-noop-validation-summary";
  header.innerHTML = `
    <div>
      <p class="eyebrow">No-op Result Validation</p>
      <h3>${escapeHtml(validation.validation_status || "unknown")}</h3>
      <p>pcweb_117_validation_status=${escapeHtml(validation.pcweb_117_validation_status || "unknown")} · ${escapeHtml(validation.validation_mode || "")}</p>
    </div>
    <div class="desktop-mic-adapter-metrics">
      ${metrics.map(([label, value]) => `
        <div class="metric compact">
          <strong>${escapeHtml(value ?? "unknown")}</strong>
          <span>${escapeHtml(label)}</span>
        </div>
      `).join("")}
    </div>
  `;
  const flags = document.createElement("div");
  flags.className = "desktop-mic-adapter-chip-group";
  flags.innerHTML = [
    `tauri_noop_run_result_status=${validation.tauri_noop_run_result_status}`,
    `next_required_decision=${validation.next_required_decision || "not_available"}`,
    ...safeFlagEntries.map(([key, value]) => `${key}=${value}`),
  ]
    .map((entry) => `<span class="chip warning">${escapeHtml(entry)}</span>`)
    .join("");
  return [header, flags];
}

function bindShadowReportFeedbackForm() {
  if (!shadowReportFeedbackForm) {
    return;
  }
  shadowReportFeedbackForm.addEventListener("submit", submitShadowReportFeedback);
}

async function submitShadowReportFeedback(event) {
  event.preventDefault();
  try {
    const candidateReportText = shadowReportFeedbackCandidateReport.value.trim();
    const feedbackText = shadowReportFeedbackEntries.value.trim();
    if (!candidateReportText || !feedbackText) {
      throw new Error("Report JSON and Feedback JSON are required");
    }
    const candidateReport = JSON.parse(candidateReportText);
    const feedbackEntries = JSON.parse(feedbackText);
    const report = await requestJson("/shadow-reports/feedback-ingestions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        candidate_report: candidateReport,
        feedback_entries: feedbackEntries,
      }),
    });
    renderShadowReportFeedbackResult(report);
    showToast("反馈已更新");
  } catch (error) {
    renderShadowReportFeedbackResult({
      feedback_ingestion_status: "client_error",
      validation_errors: [error.message],
    });
    showToast(error.message);
  }
}

function renderShadowReportFeedbackResult(report) {
  if (!shadowReportFeedbackResult) {
    return;
  }
  const readiness = report.readiness_report || {};
  const updatedReport = report.updated_candidate_report || {};
  const feedback = updatedReport.feedback_summary || {};
  const decision = updatedReport.final_decision || {};
  const errors = report.validation_errors || [];
  const labels = feedback.labels || {};
  shadowReportFeedbackResult.innerHTML = `
    <div class="shadow-report-feedback-summary">
      <span class="chip">${escapeHtml(report.feedback_ingestion_status || "not_run")}</span>
      <span class="chip">${escapeHtml(decision.decision || "no_decision")}</span>
      <span class="chip">${escapeHtml(readiness.export_readiness_status || "no_export_status")}</span>
    </div>
    <div class="shadow-report-feedback-metrics">
      <div><strong>${escapeHtml(feedback.useful_or_would_have_asked_count ?? 0)}</strong><span>positive</span></div>
      <div><strong>${escapeHtml(feedback.negative_feedback_count ?? 0)}</strong><span>negative</span></div>
      <div><strong>${escapeHtml(labels.dismissed ?? 0)}</strong><span>dismissed</span></div>
    </div>
    ${errors.length ? `<pre>${escapeHtml(errors.join("\\n"))}</pre>` : ""}
  `;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function indexById(items) {
  return Object.fromEntries((items || []).map((item) => [item.id, item]));
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("visible");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => {
    toast.classList.remove("visible");
  }, 3600);
}

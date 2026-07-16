import { copyFile, mkdir, readFile, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import {
  buildMainlineCompletionReport,
  buildRealtimeAiSuggestionReport,
  buildRealtimeExperienceReport,
  liveReminderDriftStatusFails,
  mainlineCompletionStatusFails,
  realtimeAiSuggestionStatusFails,
  realtimeExperienceStatusFails,
} from "./workbench_browser_live_mic_gate.mjs";
import {
  buildRealtimeTranscriptCompactionReport,
  realtimeTranscriptCompactionStatusFails,
} from "./workbench_browser_live_mic_compaction.mjs";
import {
  CANONICAL_TRANSCRIPT_SELECTOR,
  hasCanonicalTranscript,
  isMeetingStopped,
  isMinutesReady,
  isOrganizeTerminal,
} from "./workbench_ui_contract.mjs";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const artifactRoot = path.resolve(process.env.MEETING_COPILOT_ARTIFACT_ROOT
  || path.join(repoRoot, "artifacts", "tmp", "browser_live_mic", `run-${Date.now()}`));
const dataDir = path.resolve(process.env.MEETING_COPILOT_DATA_DIR || path.join(artifactRoot, "runtime_data"));
const useExistingServer = parseBooleanEnv(process.env.MEETING_COPILOT_E2E_USE_EXISTING_SERVER, false);
const backendServerMode = useExistingServer ? "existing_external" : "managed_isolated";
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || (useExistingServer ? "8765" : "8769"));
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9225");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const recordSeconds = Number(process.env.MEETING_COPILOT_BROWSER_MIC_SECONDS || "8");
const chromeHeadless = parseBooleanEnv(process.env.MEETING_COPILOT_BROWSER_MIC_HEADLESS, true);
const chromeFakeUiForMediaStream = parseBooleanEnv(process.env.MEETING_COPILOT_BROWSER_MIC_FAKE_UI, true);
const chromeFakeAudioFile = process.env.MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE
  ? path.resolve(process.env.MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE)
  : "";
const chromeDiagnosticsEnabled = parseBooleanEnv(
  process.env.MEETING_COPILOT_BROWSER_MIC_CHROME_DIAGNOSTICS,
  false,
);
const chromeNoSandbox = parseBooleanEnv(
  process.env.MEETING_COPILOT_BROWSER_MIC_CHROME_NO_SANDBOX,
  false,
);
const inputMode = chromeFakeAudioFile ? "fake_audio_file_browser_mic" : "real_browser_mic";
const uiCoverage = chromeHeadless ? "headless_chrome" : "visible_chrome";
const chromeUserDataDir = path.join(artifactRoot, "chrome-user-data");
let chromeAudioInputFile = chromeFakeAudioFile;
const requestedDerivationMode = process.env.MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE || "production_enabled";
const derivationMode = requestedDerivationMode === "no_cost_deterministic"
  ? "no_cost_deterministic"
  : "production_enabled";
const noCostDerivationSelfTest = derivationMode === "no_cost_deterministic";
const deleteSessionAfterRun = parseBooleanEnv(process.env.MEETING_COPILOT_BROWSER_MIC_DELETE_SESSION, true);

await mkdir(artifactRoot, { recursive: true });
const fakeAudioValidation = chromeFakeAudioFile
  ? await validateFakeAudioInput(chromeFakeAudioFile)
  : { configured: false, path: "", ok: true, reason: "not_configured" };
await writeBrowserEnvironment();
const processes = [];
const cdpSockets = [];
let serverStdout = "";
let serverStderr = "";
let chromeStdout = "";
let chromeStderr = "";
let page;
const recordingPhaseUiSamples = [];

try {
  if (chromeFakeAudioFile && !fakeAudioValidation.ok) {
    throw new Error(`invalid_fake_audio_file:${fakeAudioValidation.reason}`);
  }
  if (!useExistingServer) {
    const server = spawn(
      "uvicorn",
      ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)],
      {
        cwd: backendDir,
        env: backendEnvForBrowserMicRun(),
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    processes.push(server);
    server.stdout.on("data", (chunk) => { serverStdout += chunk.toString(); });
    server.stderr.on("data", (chunk) => { serverStderr += chunk.toString(); });
  }
  await waitForHttp(`http://127.0.0.1:${port}/health`);

  if (chromeFakeAudioFile) {
    await mkdir(chromeUserDataDir, { recursive: true });
    chromeAudioInputFile = path.join(chromeUserDataDir, "fake-audio-input.wav");
    await copyFile(chromeFakeAudioFile, chromeAudioInputFile);
    await writeBrowserEnvironment();
  }
  const chromeArgs = [
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    `--remote-debugging-port=${chromePort}`,
    `--user-data-dir=${chromeUserDataDir}`,
    "about:blank",
  ];
  if (chromeHeadless) chromeArgs.unshift("--headless=new");
  if (chromeFakeUiForMediaStream) chromeArgs.splice(chromeHeadless ? 3 : 2, 0, "--use-fake-ui-for-media-stream");
  if (chromeFakeAudioFile) {
    chromeArgs.splice(chromeHeadless ? 4 : 3, 0, "--use-fake-device-for-media-stream");
    chromeArgs.splice(chromeHeadless ? 5 : 4, 0, `--use-file-for-fake-audio-capture=${chromeAudioInputFile}`);
  }
  if (chromeDiagnosticsEnabled) {
    chromeArgs.push("--enable-logging=stderr", "--v=1");
  }
  if (chromeNoSandbox) chromeArgs.push("--no-sandbox");
  const chrome = spawn(
    chromePath,
    chromeArgs,
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  processes.push(chrome);
  chrome.stdout.on("data", (chunk) => { chromeStdout += chunk.toString(); });
  chrome.stderr.on("data", (chunk) => { chromeStderr += chunk.toString(); });
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`);

  const workbenchUrl = `http://127.0.0.1:${port}/workbench${noCostDerivationSelfTest ? "?noCostDerivationSelfTest=1" : ""}`;
  page = await createCdpPage(chromePort, workbenchUrl);
  await waitForCdpExpression(page, `document.getElementById('btn-record') !== null`);
  await evaluate(page, () => document.getElementById("btn-record").click());
  await waitForCdpExpression(page, `document.getElementById('btn-stop').hidden === false`, 15000);
  await collectRecordingPhaseUiSamples(recordSeconds);
  recordingPhaseUiSamples.push(await readRecordingPhaseUiSample("immediately_before_stop"));
  await evaluate(page, () => document.getElementById("btn-stop").click());
  await waitForCdpExpression(page, `window.__meetingCopilotBrowserMicHealth && window.__meetingCopilotBrowserMicHealth().session_id`, 20000);
  await waitForBrowserState(
    page,
    () => ({
      recordButtonHidden: document.getElementById("btn-record")?.hidden ?? true,
      stopButtonHidden: document.getElementById("btn-stop")?.hidden ?? false,
      cockpitState: document.getElementById("c-cockpit-stage")?.dataset.state || "",
    }),
    isMeetingStopped,
    30000,
    "meeting stopped state",
  );
  await delay(800);

  const canOrganize = await evaluate(page, () => {
    const button = document.getElementById("btn-organize");
    return Boolean(button && !button.hidden && !button.disabled);
  });
  if (canOrganize) {
    await evaluate(page, () => document.getElementById("btn-organize").click());
    const organizeState = await waitForBrowserState(
      page,
      () => ({
        organizeButtonDisabled: document.getElementById("btn-organize")?.disabled ?? true,
        statusText: document.getElementById("sys-status")?.innerText || "",
      }),
      isOrganizeTerminal,
      45000,
      "organize terminal state",
    );
    const organizeWaitStatus = { status: "matched", state: organizeState };
    await writeFile(path.join(artifactRoot, "organize_wait_status.json"), JSON.stringify({
      organize_wait_status: organizeWaitStatus,
    }, null, 2));
  }

  const evidence = await writePartialEvidence();
  const health = evidence.health;
  const sessionId = health.session_id;
  const asrProbe = evidence.asrProbe;
  const uiReport = evidence.uiReport || {};
  const audioExportProbe = await writeAudioExportProbe(sessionId, evidence.sessionEvents);
  const liveReminderDriftReport = buildLiveReminderDriftReport(
    evidence.sessionEvents,
    recordingPhaseUiSamples,
  );
  const realtimeExperienceReport = buildRealtimeExperienceReport({
    uiReport,
    healthStatus: health.health_status,
    recordingPhaseUiSamples,
    textLatencySloMs: process.env.MEETING_COPILOT_TEXT_LATENCY_SLO_MS || "15000",
    finalLatencySloMs: process.env.MEETING_COPILOT_FINAL_LATENCY_SLO_MS || "60000",
  });
  const realtimeAiSuggestionReport = buildRealtimeAiSuggestionReport({
    derivationMode,
    recordingPhaseUiSamples,
    expectedSessionId: sessionId,
    recordingStartedAtEpochMs: uiReport?.realtime_ui_metrics?.recording_started_at_epoch_ms,
  });
  const realtimeTranscriptCompactionReport = buildRealtimeTranscriptCompactionReport({
    derivationMode,
    correctionEnabled: asrProbe.realtime_transcript_correction_enabled,
    correctionStatus: asrProbe.realtime_transcript_correction,
    recordingPhaseUiSamples,
    recordingStartedAtEpochMs: uiReport?.realtime_ui_metrics?.recording_started_at_epoch_ms,
  });
  await writeFile(
    path.join(artifactRoot, "realtime_transcript_compaction_report.json"),
    JSON.stringify(realtimeTranscriptCompactionReport, null, 2),
  );
  const mainlineCompletionReport = buildMainlineCompletionReport({
    derivationMode,
    asrProbe,
    audioExportProbe,
    uiReport,
  });

  if (sessionId && deleteSessionAfterRun) {
    const deleteResponse = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}`, { method: "DELETE" });
    const deletedBody = await deleteResponse.json().catch(() => ({}));
    await writeFile(path.join(artifactRoot, "delete_response.json"), JSON.stringify(deletedBody, null, 2));
    const check = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/events`);
    asrProbe.delete_verified = check.status === 404;
  } else {
    await writeFile(path.join(artifactRoot, "delete_response.json"), JSON.stringify({
      delete_session_after_run: deleteSessionAfterRun,
      skipped: true,
      reason: sessionId ? "MEETING_COPILOT_BROWSER_MIC_DELETE_SESSION=false" : "session_id_missing",
    }, null, 2));
  }
  await writeFile(path.join(artifactRoot, "asr_probe.json"), JSON.stringify(asrProbe, null, 2));

  const summary = {
    artifact_root: artifactRoot,
    session_id: sessionId,
    backend_server_mode: backendServerMode,
    input_mode: inputMode,
    ui_coverage: uiCoverage,
    chrome_headless: chromeHeadless,
    chrome_fake_ui_for_media_stream: chromeFakeUiForMediaStream,
    chrome_fake_audio_file: chromeFakeAudioFile || null,
    health_status: health.health_status,
    chunk_count: health.chunk_count,
    asr_final_count: asrProbe.events.filter((event) => event.event_type === "transcript_final" || event.event_type === "final").length,
    derivation_mode: derivationMode,
    derivations_generated: Boolean(asrProbe.derivations_generated),
    counts_as_production_llm_evidence: Boolean(asrProbe.counts_as_production_llm_evidence),
    suggestion_card_count: asrProbe.suggestion_card_count || 0,
    approach_card_count: asrProbe.approach_card_count || 0,
    minutes_char_count: asrProbe.minutes_char_count || 0,
    audio_export_http_status: audioExportProbe.audio_export_http_status,
    audio_file_size_bytes: audioExportProbe.audio_file_size_bytes,
    audio_sha256_matches_session: Boolean(audioExportProbe.audio_sha256_matches_session),
    workbench_same_session_visible: Boolean(uiReport.workbench_same_session_visible),
    frontend_utterance_count: uiReport.frontend_utterance_count || 0,
    frontend_card_count: uiReport.frontend_card_count || 0,
    frontend_minutes_visible: Boolean(uiReport.frontend_minutes_visible),
    meeting_cockpit_stage: uiReport.meeting_cockpit_stage || { label: "", state: "" },
    meeting_cockpit_counts: uiReport.meeting_cockpit_counts || {},
    first_text_after_audio_active_latency_ms: uiReport.first_text_after_audio_active_latency_ms ?? null,
    first_final_after_audio_active_latency_ms: uiReport.first_final_after_audio_active_latency_ms ?? null,
    partial_visible_count: uiReport.partial_visible_count || 0,
    final_visible_count: uiReport.final_visible_count || 0,
    realtime_experience_status: realtimeExperienceReport.status,
    realtime_experience_report: realtimeExperienceReport,
    realtime_ai_suggestion_status: realtimeAiSuggestionReport.status,
    realtime_ai_suggestion_report: realtimeAiSuggestionReport,
    max_recording_ai_suggestions: realtimeAiSuggestionReport.max_recording_ai_suggestions,
    first_ai_suggestion_visible_latency_ms: realtimeAiSuggestionReport.first_ai_suggestion_visible_latency_ms,
    first_correction_visible_latency_ms: realtimeTranscriptCompactionReport.first_correction_visible_latency_ms,
    realtime_transcript_compaction_status: realtimeTranscriptCompactionReport.status,
    realtime_transcript_compaction_report: realtimeTranscriptCompactionReport,
    mainline_completion_status: mainlineCompletionReport.status,
    mainline_completion_report: mainlineCompletionReport,
    browser_console_error_count: uiReport.browser_console_error_count || 0,
    network_error_count: uiReport.network_error_count || 0,
    fake_audio_validation: fakeAudioValidation,
    fake_audio_input_copy: chromeAudioInputFile || null,
    chrome_diagnostics_enabled: chromeDiagnosticsEnabled,
    chrome_no_sandbox: chromeNoSandbox,
    chrome_stderr_tail: tailText(chromeStderr, 4000),
    recording_phase_ui_samples: recordingPhaseUiSamples.length,
    live_reminder_drift_status: liveReminderDriftReport.status,
    live_reminder_drift_report: liveReminderDriftReport,
    delete_session_after_run: deleteSessionAfterRun,
    delete_verified: Boolean(asrProbe.delete_verified),
  };
  await writeFile(path.join(artifactRoot, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
  if (liveReminderDriftStatusFails(liveReminderDriftReport.status)) {
    console.error(JSON.stringify({
      error: "live reminder drift detected during recording",
      live_reminder_drift_report: liveReminderDriftReport,
    }, null, 2));
    process.exitCode = 1;
  }
  if (realtimeExperienceStatusFails(realtimeExperienceReport.status)) {
    console.error(JSON.stringify({
      error: "realtime text experience failed",
      realtime_experience_report: realtimeExperienceReport,
    }, null, 2));
    process.exitCode = 1;
  }
  if (realtimeAiSuggestionStatusFails(realtimeAiSuggestionReport.status)) {
    console.error(JSON.stringify({
      error: "formal AI suggestion was not visible during recording",
      realtime_ai_suggestion_report: realtimeAiSuggestionReport,
    }, null, 2));
    process.exitCode = 1;
  }
  if (realtimeTranscriptCompactionStatusFails(realtimeTranscriptCompactionReport.status)) {
    console.error(JSON.stringify({
      error: "realtime transcript compaction or correction visibility failed",
      realtime_transcript_compaction_report: realtimeTranscriptCompactionReport,
    }, null, 2));
    process.exitCode = 1;
  }
  if (mainlineCompletionStatusFails(mainlineCompletionReport.status)) {
    console.error(JSON.stringify({
      error: "full meeting mainline did not close",
      mainline_completion_report: mainlineCompletionReport,
    }, null, 2));
    process.exitCode = 1;
  }
} catch (err) {
  let partialEvidence = {};
  const expectedEvidenceFiles = [
    "browser_mic_health_report.json",
    "asr_probe.json",
    "ui_verification.json",
    "session_events.json",
    "audio_export_probe.json",
    "page_state_after_failure.json",
  ];
  try {
    partialEvidence = await writePartialEvidence({ error: err.message });
  } catch (evidenceErr) {
    partialEvidence = { evidence_error: evidenceErr.message };
  }
  const report = {
    artifact_root: artifactRoot,
    error: err.message,
    backend_server_mode: backendServerMode,
    expected_evidence_files: expectedEvidenceFiles,
    partial_evidence: partialEvidence,
    server_stdout_tail: tailText(serverStdout, 2000),
    server_stderr_tail: tailText(serverStderr, 2000),
    fake_audio_validation: fakeAudioValidation,
    fake_audio_input_copy: chromeAudioInputFile || null,
    chrome_no_sandbox: chromeNoSandbox,
    chrome_stdout_tail: tailText(chromeStdout, 2000),
    chrome_stderr_tail: tailText(chromeStderr, 4000),
  };
  await writeFile(path.join(artifactRoot, "browser_live_mic_verify_error.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
  process.exitCode = 1;
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
}

function delay(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function tailText(text, maxChars) { return text.length > maxChars ? text.slice(text.length - maxChars) : text; }
function parseBooleanEnv(value, fallback) {
  if (value === undefined || value === null || value === "") return fallback;
  return !["0", "false", "no", "off"].includes(String(value).trim().toLowerCase());
}

function backendEnvForBrowserMicRun() {
  const env = {
    ...process.env,
    MEETING_COPILOT_DATA_DIR: dataDir,
    PYTHONPATH: ".:../../core",
  };
  if (!noCostDerivationSelfTest) return env;
  return {
    ...env,
    LLM_GATEWAY_BASE_URL: "",
    LLM_GATEWAY_API_KEY: "",
    LLM_GATEWAY_MODEL: "",
    LLM_GATEWAY_PROVIDER_LABEL: "not_configured_no_cost_browser_mic_selftest",
  };
}

async function writeBrowserEnvironment() {
  await writeFile(path.join(artifactRoot, "browser_environment.json"), JSON.stringify({
    report_type: "browser_live_mic_environment",
    input_mode: inputMode,
    ui_coverage: uiCoverage,
    chrome_headless: chromeHeadless,
    chrome_fake_ui_for_media_stream: chromeFakeUiForMediaStream,
    chrome_fake_audio_file: chromeFakeAudioFile || null,
    chrome_audio_input_file: chromeAudioInputFile || null,
    fake_audio_input_copy: chromeAudioInputFile || null,
    fake_audio_validation: fakeAudioValidation,
    chrome_diagnostics_enabled: chromeDiagnosticsEnabled,
    chrome_no_sandbox: chromeNoSandbox,
    record_seconds: recordSeconds,
    chrome_path: chromePath,
    chrome_remote_debugging_port: chromePort,
    backend_port: port,
    backend_server_mode: backendServerMode,
    managed_backend_started: !useExistingServer,
    workbench_url: `http://127.0.0.1:${port}/workbench${noCostDerivationSelfTest ? "?noCostDerivationSelfTest=1" : ""}`,
    derivation_mode: derivationMode,
    no_cost_derivation_selftest: noCostDerivationSelfTest,
    production_derivation_requested: derivationMode === "production_enabled",
    production_llm_evidence_source: "asr_probe.json",
    delete_session_after_run: deleteSessionAfterRun,
  }, null, 2));
}

async function validateFakeAudioInput(filePath) {
  const base = { configured: true, path: filePath, ok: false };
  try {
    const fileStat = await stat(filePath);
    if (!fileStat.isFile()) return { ...base, reason: "not_a_file" };
    if (fileStat.size < 44) return { ...base, reason: "invalid_empty_audio_file", bytes: fileStat.size };
    const buffer = await readFile(filePath);
    if (buffer.toString("ascii", 0, 4) !== "RIFF" || buffer.toString("ascii", 8, 12) !== "WAVE") {
      return { ...base, reason: "invalid_wave_header", bytes: buffer.length };
    }
    let offset = 12;
    let fmt = null;
    let dataOffset = -1;
    let dataSize = 0;
    while (offset + 8 <= buffer.length) {
      const chunkId = buffer.toString("ascii", offset, offset + 4);
      const chunkSize = buffer.readUInt32LE(offset + 4);
      const chunkDataOffset = offset + 8;
      if (chunkDataOffset + chunkSize > buffer.length) {
        return { ...base, reason: "truncated_wave_chunk", bytes: buffer.length };
      }
      if (chunkId === "fmt " && chunkSize >= 16) {
        fmt = {
          audio_format: buffer.readUInt16LE(chunkDataOffset),
          channels: buffer.readUInt16LE(chunkDataOffset + 2),
          sample_rate: buffer.readUInt32LE(chunkDataOffset + 4),
          bits_per_sample: buffer.readUInt16LE(chunkDataOffset + 14),
        };
      } else if (chunkId === "data" && dataOffset < 0) {
        dataOffset = chunkDataOffset;
        dataSize = chunkSize;
      }
      offset = chunkDataOffset + chunkSize + (chunkSize % 2);
    }
    if (!fmt || dataOffset < 0 || dataSize <= 0) {
      return { ...base, reason: "missing_pcm_chunks", bytes: buffer.length };
    }
    if (fmt.audio_format !== 1 || fmt.bits_per_sample !== 16 || fmt.channels < 1) {
      return { ...base, reason: "unsupported_pcm_format", bytes: buffer.length, ...fmt };
    }
    const end = Math.min(buffer.length, dataOffset + dataSize);
    let sumSquares = 0;
    let peak = 0;
    let sampleCount = 0;
    for (let index = dataOffset; index + 1 < end; index += 2) {
      const sample = buffer.readInt16LE(index) / 32768;
      sumSquares += sample * sample;
      peak = Math.max(peak, Math.abs(sample));
      sampleCount += 1;
    }
    const rms = Math.sqrt(sumSquares / Math.max(sampleCount, 1));
    if (sampleCount === 0 || rms === 0 || peak === 0) {
      return { ...base, reason: "silent_audio_file", bytes: buffer.length, ...fmt, rms, peak };
    }
    return {
      ...base,
      ok: true,
      reason: "valid_non_silent_pcm_wav",
      bytes: buffer.length,
      ...fmt,
      rms,
      peak,
      sample_count: sampleCount,
    };
  } catch (error) {
    return { ...base, reason: "audio_file_read_failed", error: error.message };
  }
}

async function writePartialEvidence(context = {}) {
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "recording_phase_ui_samples.json"), JSON.stringify({
    report_type: "recording_phase_ui_samples",
    samples: recordingPhaseUiSamples,
  }, null, 2));
  const health = await readBrowserMicHealth();
  await writeFile(path.join(artifactRoot, "browser_mic_health_report.json"), JSON.stringify(health, null, 2));

  const pageState = await readPageState(context);
  await writeFile(path.join(artifactRoot, "page_state_after_failure.json"), JSON.stringify(pageState, null, 2));

  const sessionId = health.session_id || pageState.session_id || "";
  const { asrProbe, sessionEvents } = await readAsrProbe(sessionId);
  await writeFile(path.join(artifactRoot, "asr_probe.json"), JSON.stringify(asrProbe, null, 2));
  if (sessionEvents) {
    await writeFile(path.join(artifactRoot, "session_events.json"), JSON.stringify(sessionEvents, null, 2));
  }

  const uiReport = await readUiReport();
  uiReport.canonical_transcript_selector = CANONICAL_TRANSCRIPT_SELECTOR;
  uiReport.canonical_transcript_visible = hasCanonicalTranscript({
    canonicalCount: uiReport.frontend_utterance_count,
  });
  uiReport.frontend_minutes_visible = isMinutesReady({
    minutesCountText: uiReport.minutes_count_text,
    panelText: uiReport.minutes_text,
  });
  await writeFile(path.join(artifactRoot, "ui_verification.json"), JSON.stringify(uiReport, null, 2));
  await captureScreenshot("workbench-browser-live-mic.png");
  return { health, asrProbe, sessionEvents, uiReport, pageState };
}

async function collectRecordingPhaseUiSamples(seconds) {
  const totalMs = Math.max(0, Number(seconds || 0) * 1000);
  const sampleEveryMs = Math.max(1200, Math.min(5000, Math.floor(totalMs / 4) || 1200));
  const deadline = Date.now() + totalMs;
  recordingPhaseUiSamples.push(await readRecordingPhaseUiSampleWithBackend("recording_started"));
  while (Date.now() + sampleEveryMs < deadline) {
    await delay(sampleEveryMs);
    recordingPhaseUiSamples.push(await readRecordingPhaseUiSampleWithBackend("recording_in_progress"));
  }
  const remainingMs = Math.max(0, deadline - Date.now());
  if (remainingMs) await delay(remainingMs);
  recordingPhaseUiSamples.push(await readRecordingPhaseUiSampleWithBackend("before_stop"));
  await writeFile(path.join(artifactRoot, "recording_phase_ui_samples.json"), JSON.stringify({
    report_type: "recording_phase_ui_samples",
    samples: recordingPhaseUiSamples,
  }, null, 2));
}

async function readRecordingPhaseUiSampleWithBackend(label) {
  const sample = await readRecordingPhaseUiSample(label);
  const backendProbe = await readRecordingBackendReminderProbe(sample.session_id || "");
  return { ...sample, ...backendProbe };
}

async function readRecordingPhaseUiSample(label) {
  if (!page) return { label, sample_error: "page_missing" };
  try {
    return await evaluate(page, (sampleLabel) => {
      const browserMicHealth = window.__meetingCopilotBrowserMicHealth
        ? window.__meetingCopilotBrowserMicHealth()
        : {};
      function readMeetingCockpitCounts() {
        const text = (id) => document.getElementById(id)?.innerText || "";
        return {
          transcript: text("c-transcript"),
          realtime_reminders: text("c-gap"),
          ai_suggestions: text("c-cards"),
          approach: text("c-approach"),
          audio: text("c-audio"),
          minutes: text("c-minutes"),
          decisions: text("c-decision"),
          actions: text("c-action"),
          risks: text("c-risk"),
          questions: text("c-question"),
        };
      }
      function readMeetingCockpitStage() {
        const stage = document.getElementById("c-cockpit-stage");
        return {
          label: stage?.innerText || "",
          state: stage?.dataset?.state || "",
        };
      }
      const transcript = document.getElementById("transcript-stream");
      const partialDrafts = Array.from(document.querySelectorAll("[data-live-segment-id]"));
      const livePartial = partialDrafts[0] || null;
      const activeRowsBySegment = new Map();
      partialDrafts.forEach((row) => {
        const segmentId = row.dataset.liveSegmentId || "";
        activeRowsBySegment.set(segmentId, (activeRowsBySegment.get(segmentId) || 0) + 1);
      });
      const maxRowsForSingleActiveSegment = Math.max(0, ...activeRowsBySegment.values());
      const committedTranscriptRows = Array.from(document.querySelectorAll(".transcript-segment[data-transcript-segment-id]"));
      const canonicalTranscriptRows = Array.from(document.querySelectorAll(".transcript-segment[data-transcript-segment-id]"));
      const correctedTranscriptRows = Array.from(new Set([
        ...canonicalTranscriptRows.filter((row) => row.dataset.status === "corrected"),
        ...committedTranscriptRows.filter((row) => row.classList.contains("transcript-revision")),
      ]));
      const correctedTranscriptSegmentIds = correctedTranscriptRows
        .map((row) => row.dataset.segmentId || row.dataset.transcriptSegmentId || "")
        .map((value) => String(value).trim())
        .filter(Boolean);
      const correctedTranscriptSourceSegmentIds = correctedTranscriptRows
        .map((row) => row.dataset.sourceSegmentId || "")
        .map((value) => String(value).trim())
        .filter(Boolean);
      const renderedSuggestionCards = Array.from(document.querySelectorAll("#suggestions-panel [data-card-kind='suggestion']"));
      const visibleSuggestionCards = renderedSuggestionCards.filter((card) => {
        const style = window.getComputedStyle(card);
        return style.display !== "none"
          && style.visibility !== "hidden"
          && Number(style.opacity || "1") > 0
          && card.getClientRects().length > 0;
      });
      const visibleEvidenceBackedSuggestionCards = visibleSuggestionCards.filter((card) => (
        Array.from(card.querySelectorAll(".evidence-link")).some((link) => (
          Boolean(link.dataset.evidenceId || link.dataset.segmentId)
        ))
      ));
      return {
        label: sampleLabel,
        at_ms: Date.now(),
        session_id: browserMicHealth.session_id || "",
        cockpit_counts: readMeetingCockpitCounts(),
        cockpit_stage: readMeetingCockpitStage(),
        transcript_text: transcript?.innerText || "",
        utterance_count: transcript?.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length || 0,
        partial_draft_count: partialDrafts.length,
        partial_draft_texts: partialDrafts.map((item) => item.innerText || ""),
        live_partial_exists: Boolean(livePartial),
        live_partial_text: livePartial?.innerText || "",
        active_live_partial_count: partialDrafts.length,
        committed_transcript_row_count: committedTranscriptRows.length,
        corrected_transcript_row_count: correctedTranscriptRows.length,
        corrected_transcript_segment_ids: [...new Set(correctedTranscriptSegmentIds)],
        corrected_transcript_source_segment_ids: [...new Set(correctedTranscriptSourceSegmentIds)],
        max_rows_for_single_active_segment: maxRowsForSingleActiveSegment,
        rendered_suggestion_card_count: renderedSuggestionCards.length,
        visible_suggestion_card_count: visibleSuggestionCards.length,
        visible_evidence_backed_suggestion_card_count: visibleEvidenceBackedSuggestionCards.length,
      };
    }, label);
  } catch (err) {
    return { label, sample_error: err.message };
  }
}

async function readRecordingBackendReminderProbe(sessionId) {
  const fallback = {
    backend_probe_status: sessionId ? "not_started" : "session_id_missing",
    backend_event_count: 0,
    backend_suggestion_candidate_count: 0,
    backend_partial_hint_count: 0,
    backend_live_reminder_count: null,
  };
  if (!sessionId) return fallback;
  try {
    const response = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/events`);
    if (response.status === 404) {
      return { ...fallback, backend_probe_status: "session_not_yet_persisted" };
    }
    if (!response.ok) {
      return { ...fallback, backend_probe_status: "http_error", backend_http_status: response.status };
    }
    const body = await response.json();
    const events = Array.isArray(body.events) ? body.events : [];
    const suggestionCount = events.filter((event) => event.event_type === "suggestion_candidate_event").length;
    const partialHintCount = events.filter((event) => event.event_type === "partial_hint_event").length;
    return {
      backend_probe_status: "ok",
      backend_event_count: events.length,
      backend_suggestion_candidate_count: suggestionCount,
      backend_partial_hint_count: partialHintCount,
      backend_live_reminder_count: suggestionCount + partialHintCount,
    };
  } catch (err) {
    return {
      ...fallback,
      backend_probe_status: "probe_error",
      backend_probe_error: err.message,
    };
  }
}

function buildLiveReminderDriftReport(sessionEvents, samples = []) {
  const events = Array.isArray(sessionEvents?.events) ? sessionEvents.events : [];
  const finalSuggestionCount = events.filter((event) => event.event_type === "suggestion_candidate_event").length;
  const finalPartialHintCount = events.filter((event) => event.event_type === "partial_hint_event").length;
  const sampleSummaries = (samples || []).map((sample) => {
    const frontendCount = numericText(sample?.cockpit_counts?.realtime_reminders);
    const backendCount = numericText(sample?.backend_live_reminder_count);
    return {
      label: sample?.label || "",
      at_ms: sample?.at_ms || null,
      frontend_realtime_reminders: frontendCount,
      backend_live_reminders: backendCount,
      backend_probe_status: sample?.backend_probe_status || "",
    };
  });
  const backendCounts = sampleSummaries
    .map((sample) => sample.backend_live_reminders)
    .filter((value) => Number.isFinite(value));
  const frontendCounts = sampleSummaries
    .map((sample) => sample.frontend_realtime_reminders)
    .filter((value) => Number.isFinite(value));
  const maxBackend = backendCounts.length ? Math.max(...backendCounts) : 0;
  const maxFrontend = frontendCounts.length ? Math.max(...frontendCounts) : 0;
  const toleratedDelta = 2;
  let status = "not_evaluated_no_recording_backend_candidates";
  if (!sampleSummaries.length) {
    status = "not_evaluated_no_recording_samples";
  } else if (!backendCounts.length) {
    status = "not_evaluated_missing_recording_backend_probe";
  } else if (maxBackend <= 0) {
    status = "not_evaluated_no_recording_backend_candidates";
  } else if (maxFrontend + toleratedDelta >= maxBackend) {
    status = "passed";
  } else {
    status = "failed_backend_candidates_not_visible";
  }
  return {
    status,
    sample_count: sampleSummaries.length,
    recording_backend_probe_sample_count: backendCounts.length,
    max_recording_backend_live_reminders: maxBackend,
    max_recording_frontend_realtime_reminders: maxFrontend,
    tolerated_delta: toleratedDelta,
    max_recording_backend_frontend_delta: Math.max(maxBackend - maxFrontend, 0),
    final_session_candidate_event_count: finalSuggestionCount,
    final_session_partial_hint_event_count: finalPartialHintCount,
    final_session_live_reminder_count: finalSuggestionCount + finalPartialHintCount,
    sample_summaries: sampleSummaries,
  };
}

function numericText(value) {
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(String(value).replace(/[^\d.-]/g, ""));
  return Number.isFinite(numeric) ? numeric : null;
}

async function writeAudioExportProbe(sessionId, sessionEvents) {
  const sessionAudio = sessionEvents?.audio || {};
  const fallback = {
    session_id: sessionId || "",
    audio_export_http_status: null,
    audio_export_content_type: "",
    audio_file_size_bytes: 0,
    audio_file_magic: "",
    audio_sha256: "",
    audio_sha256_matches_session: false,
    expected_session_audio_sha256: sessionAudio.sha256 || "",
    session_audio_saved: Boolean(sessionAudio.saved),
    session_audio_duration_ms: Number(sessionAudio.duration_ms || 0),
    session_audio_file_size_bytes: Number(sessionAudio.file_size_bytes || 0),
    exported_audio_artifact: "",
  };
  if (!sessionId) {
    await writeFile(path.join(artifactRoot, "audio_export_probe.json"), JSON.stringify({
      ...fallback,
      audio_export_error: "session_id_missing",
    }, null, 2));
    return fallback;
  }
  try {
    const response = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/audio.wav`);
    const bytes = response.ok ? Buffer.from(await response.arrayBuffer()) : Buffer.alloc(0);
    const exportedAudioPath = path.join(artifactRoot, "exported-audio.wav");
    if (bytes.length) {
      await writeFile(exportedAudioPath, bytes);
    }
    const audioSha256 = bytes.length ? createHash("sha256").update(bytes).digest("hex") : "";
    const probe = {
      ...fallback,
      audio_export_http_status: response.status,
      audio_export_content_type: response.headers.get("content-type") || "",
      audio_file_size_bytes: bytes.length,
      audio_file_magic: bytes.subarray(0, 4).toString("ascii"),
      audio_sha256: audioSha256,
      audio_sha256_matches_session: Boolean(sessionAudio.sha256 && audioSha256 === sessionAudio.sha256),
      exported_audio_artifact: bytes.length ? exportedAudioPath : "",
    };
    await writeFile(path.join(artifactRoot, "audio_export_probe.json"), JSON.stringify(probe, null, 2));
    return probe;
  } catch (err) {
    const probe = {
      ...fallback,
      audio_export_error: err.message,
    };
    await writeFile(path.join(artifactRoot, "audio_export_probe.json"), JSON.stringify(probe, null, 2));
    return probe;
  }
}

async function readBrowserMicHealth() {
  const fallback = {
    report_type: "workbench_browser_mic_health",
    session_id: "",
    input_mode: inputMode,
    health_status: "blocked_no_audio_samples",
    sample_count: 0,
    chunk_count: 0,
    rms: 0,
    peak: 0,
    active_sample_ratio: 0,
    raw_audio_uploaded: false,
    remote_asr_called: false,
    llm_called: false,
    configs_local_read: false,
    user_audio_committed_to_repo: false,
  };
  if (!page) return fallback;
  try {
    const health = await evaluate(page, () => {
      if (window.__meetingCopilotBrowserMicHealth) return window.__meetingCopilotBrowserMicHealth();
      const raw = document.body.dataset.browserMicHealth;
      return raw ? JSON.parse(raw) : null;
    });
    return health ? { ...fallback, ...health } : fallback;
  } catch {
    return fallback;
  }
}

async function readPageState(context = {}) {
  const fallback = {
    artifact_root: artifactRoot,
    session_id: "",
    error: context.error || "",
    server_stdout_tail: tailText(serverStdout, 2000),
    server_stderr_tail: tailText(serverStderr, 2000),
  };
  if (!page) return fallback;
  try {
    const state = await evaluate(page, () => ({
      session_id: window.__meetingCopilotBrowserMicHealth?.().session_id || "",
      title: document.title,
      status_text: document.getElementById("sys-status")?.innerText || "",
      toast_text: document.getElementById("toast")?.innerText || "",
      session_meta: document.getElementById("session-meta")?.innerText || "",
      transcript_text: document.getElementById("transcript-stream")?.innerText || "",
      candidate_text: document.getElementById("candidate-panel")?.innerText || "",
      suggestions_text: document.getElementById("suggestions-panel")?.innerText || "",
      approach_text: document.getElementById("approach-panel")?.innerText || "",
      minutes_text: document.getElementById("minutes-panel")?.innerText || "",
      minutes_count_text: document.getElementById("c-minutes")?.innerText || "",
      utterance_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length,
      suggestion_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length,
      approach_card_count: document.querySelectorAll("[data-card-kind='approach']").length,
    }));
    return { ...fallback, ...state };
  } catch (err) {
    return { ...fallback, page_state_error: err.message };
  }
}

async function readAsrProbe(sessionId) {
  const fallback = {
    session_id: sessionId,
    provider: "not_started",
    provider_mode: "unknown",
    is_mock: false,
    asr_fallback_used: true,
    degradation_reasons: [sessionId ? "session_snapshot_missing" : "session_id_missing"],
    events: [],
    derivation_mode: derivationMode,
    derivations_generated: false,
    llm_called: false,
    llm_provider: process.env.LLM_GATEWAY_IS_MOCK === "true" ? "local_mock_openai" : (process.env.LLM_GATEWAY_BASE_URL ? "real_gateway" : "not_configured"),
    gateway_base_url_kind: gatewayBaseUrlKind(),
    counts_as_production_llm_evidence: false,
    suggestion_card_count: 0,
    approach_card_count: 0,
    minutes_char_count: 0,
    all_cards_have_evidence: false,
    delete_verified: false,
  };
  if (!sessionId) return { asrProbe: fallback, sessionEvents: null };
  try {
    const response = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/events`);
    if (!response.ok) {
      return {
        asrProbe: {
          ...fallback,
          degradation_reasons: [`session_snapshot_http_${response.status}`],
        },
        sessionEvents: null,
      };
    }
    const body = await response.json();
    const suggestionCards = body.suggestion_cards || [];
    const approachCards = body.approach_cards || [];
    const minutesMarkdown = body.minutes?.minutes_md || "";
    const llmProvider = inferLlmProviderFromSessionBody(body);
    const hasLlmUsage = hasLlmUsageEvidence(body);
    const gatewayKind = gatewayBaseUrlKindFromSessionBody(body);
    const llmUsageSummary = collectLlmUsageSummary(body);
    const derivationsGenerated = Boolean(suggestionCards.length || approachCards.length || minutesMarkdown);
    const productionLlmEvidence = countsAsProductionLlmEvidence(llmProvider, hasLlmUsage, gatewayKind);
    return {
      asrProbe: {
        session_id: sessionId,
        provider: body.provider || "not_started",
        provider_mode: body.provider_mode || "unknown",
        is_mock: Boolean(body.is_mock),
        asr_fallback_used: Boolean(body.asr_fallback_used),
        degradation_reasons: body.degradation_reasons || [],
        asr_semantic_quality: body.event_source?.asr_semantic_quality || {},
        acceptance_eligible: Boolean(body.event_source?.acceptance_eligible),
        acceptance_blockers: body.event_source?.acceptance_blockers || [],
        events: body.events || [],
        derivation_mode: derivationMode,
        derivations_generated: derivationsGenerated,
        llm_called: Boolean(productionLlmEvidence),
        llm_provider: llmProvider,
        gateway_base_url_kind: gatewayKind,
        counts_as_production_llm_evidence: productionLlmEvidence,
        llm_call_count: llmUsageSummary.llm_call_count,
        llm_usage_total_tokens: llmUsageSummary.llm_usage_total_tokens,
        suggestion_card_count: suggestionCards.length,
        approach_card_count: approachCards.length,
        minutes_char_count: minutesMarkdown.length,
        all_cards_have_evidence: suggestionCards.length > 0
          && suggestionCards.every((card) => (card.evidence_span_ids || []).length > 0),
        realtime_transcript_correction_enabled: inferRealtimeCorrectionEnabled(body),
        realtime_transcript_correction: body.realtime_transcript_correction || {},
        delete_verified: false,
      },
      sessionEvents: body,
    };
  } catch (err) {
    return {
      asrProbe: {
        ...fallback,
        degradation_reasons: [`session_snapshot_error:${err.message}`],
      },
      sessionEvents: null,
    };
  }
}

function inferLlmProviderFromSessionBody(body) {
  if (hasDeterministicDemoDerivation(body)) return "deterministic_demo";
  if (body.llm_evidence?.is_mock === true) return "local_mock_openai";
  if (process.env.LLM_GATEWAY_IS_MOCK === "true") return "local_mock_openai";
  if (["local", "remote"].includes(gatewayBaseUrlKindFromSessionBody(body)) && hasLlmUsageEvidence(body)) {
    return "real_gateway";
  }
  if (gatewayBaseUrlKindFromSessionBody(body) === "local") return "local_openai_compatible_gateway";
  if (hasLlmUsageEvidence(body)) return "real_gateway";
  return process.env.LLM_GATEWAY_BASE_URL ? "real_gateway" : "not_configured";
}

function hasDeterministicDemoDerivation(body) {
  const providers = [
    body.minutes?.llm_provider?.provider,
    ...(body.suggestion_cards || []).map((card) => card.llm_trace?.provider),
    ...(body.approach_cards || []).map((card) => card.llm_trace?.provider),
  ];
  return providers.includes("deterministic_demo");
}

function hasLlmUsageEvidence(body) {
  return Boolean(
    body.llm_evidence?.llm_usage_total_tokens
    || body.llm_evidence?.llm_called
    || body.minutes?.llm_usage?.total_tokens
    || (body.suggestion_cards || []).some((card) => card.llm_trace?.usage?.total_tokens || card.llm_usage?.total_tokens)
    || (body.approach_cards || []).some((card) => card.llm_trace?.usage?.total_tokens || card.llm_usage?.total_tokens)
  );
}

function collectLlmUsageSummary(body) {
  const sessionEvidence = body.llm_evidence || {};
  if (Number(sessionEvidence.llm_call_count || 0) > 0 && Number(sessionEvidence.llm_usage_total_tokens || 0) > 0) {
    return {
      llm_call_count: Number(sessionEvidence.llm_call_count),
      llm_usage_total_tokens: Number(sessionEvidence.llm_usage_total_tokens),
    };
  }
  const suggestionUsages = (body.suggestion_cards || [])
    .map((card) => usageFromValue(card.llm_trace?.usage || card.llm_usage))
    .filter((usage) => usage.total_tokens > 0);
  const approachUsages = (body.approach_cards || [])
    .map((card) => usageFromValue(card.llm_trace?.usage || card.llm_usage))
    .filter((usage) => usage.total_tokens > 0);
  const minutesUsage = usageFromValue(body.minutes?.llm_usage);
  const usages = [...suggestionUsages];
  if (approachUsages.length) usages.push(approachUsages[0]);
  if (minutesUsage.total_tokens > 0) usages.push(minutesUsage);
  return {
    llm_call_count: usages.length,
    llm_usage_total_tokens: usages.reduce((sum, usage) => sum + usage.total_tokens, 0),
  };
}

function usageFromValue(value) {
  if (!value || typeof value !== "object") return { total_tokens: 0 };
  return { total_tokens: Number(value.total_tokens || 0) || 0 };
}

function gatewayBaseUrlKind() {
  if (noCostDerivationSelfTest) return "not_configured";
  const baseUrl = process.env.LLM_GATEWAY_BASE_URL || "";
  if (!baseUrl) return "not_configured";
  return gatewayUrlKind(baseUrl);
}

function gatewayBaseUrlKindFromSessionBody(body) {
  const sessionKind = String(body.llm_evidence?.gateway_base_url_kind || "");
  if (["local", "remote"].includes(sessionKind)) return sessionKind;
  const envKind = gatewayBaseUrlKind();
  if (envKind !== "not_configured") return envKind;
  const providerValues = [
    ...(body.suggestion_cards || []).map((card) => card.llm_trace?.provider),
    ...(body.approach_cards || []).map((card) => card.llm_trace?.provider),
    body.minutes?.llm_provider?.provider,
  ].filter(Boolean);
  const urlKinds = providerValues
    .map((provider) => gatewayUrlKind(String(provider)))
    .filter((kind) => kind !== "not_configured");
  if (urlKinds.includes("remote")) return "remote";
  if (urlKinds.includes("local")) return "local";
  return "not_configured";
}

function gatewayUrlKind(value) {
  if (!/^https?:\/\//i.test(value)) return "not_configured";
  return /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(?::|\/|$)/i.test(value) ? "local" : "remote";
}

function inferRealtimeCorrectionEnabled(body) {
  const snapshotValue = body?.settings_snapshot?.asr?.l2_correction_enabled;
  if (typeof snapshotValue === "boolean") return snapshotValue;
  const correction = body?.realtime_transcript_correction || {};
  if (correction.status === "correction_disabled_by_setting") return false;
  if (correction.status || (correction.processed_segment_ids || []).length > 0) return true;
  return null;
}

function countsAsProductionLlmEvidence(llmProvider, hasLlmUsage, gatewayKind) {
  return derivationMode === "production_enabled"
    && llmProvider === "real_gateway"
    && hasLlmUsage
    && gatewayKind === "remote"
    && process.env.LLM_GATEWAY_IS_MOCK !== "true";
}

async function readUiReport() {
  const realtimeFallback = {
    report_type: "workbench_realtime_ui_metrics",
    session_id: "",
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
  const fallback = {
    ui_coverage: uiCoverage,
    input_mode: inputMode,
    chrome_headless: chromeHeadless,
    chrome_fake_ui_for_media_stream: chromeFakeUiForMediaStream,
    workbench_same_session_visible: false,
    frontend_utterance_count: 0,
    frontend_card_count: 0,
    frontend_partial_hint_count: 0,
    frontend_minutes_visible: false,
    minutes_text: "",
    minutes_count_text: "",
    browser_console_error_count: 0,
    network_error_count: 0,
    session_meta: "",
    candidate_panel_text: "",
    realtime_ui_metrics: realtimeFallback,
    first_text_visible_latency_ms: null,
    first_partial_visible_latency_ms: null,
    first_final_visible_latency_ms: null,
    first_audio_active_offset_ms: null,
    first_text_after_audio_active_latency_ms: null,
    first_partial_after_audio_active_latency_ms: null,
    first_final_after_audio_active_latency_ms: null,
    partial_visible_count: 0,
    final_visible_count: 0,
  };
  if (!page) return fallback;
  try {
    const domReport = await evaluate(page, () => {
      function readMeetingCockpitCounts() {
        const text = (id) => document.getElementById(id)?.innerText || "";
        return {
          transcript: text("c-transcript"),
          realtime_reminders: text("c-gap"),
          ai_suggestions: text("c-cards"),
          approach: text("c-approach"),
          audio: text("c-audio"),
          minutes: text("c-minutes"),
          decisions: text("c-decision"),
          actions: text("c-action"),
          risks: text("c-risk"),
          questions: text("c-question"),
        };
      }
      function readMeetingCockpitStage() {
        const stage = document.getElementById("c-cockpit-stage");
        return {
          label: stage?.innerText || "",
          state: stage?.dataset?.state || "",
        };
      }
      const realtimeMetrics = window.__meetingCopilotRealtimeUiMetrics
        ? window.__meetingCopilotRealtimeUiMetrics()
        : {};
      return {
        workbench_same_session_visible: Boolean(document.getElementById("session-meta")?.innerText && document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length >= 1),
        frontend_utterance_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id], #transcript-active-tail:not([hidden])").length,
        frontend_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length + document.querySelectorAll("[data-card-kind='approach']").length,
        frontend_partial_hint_count: document.querySelectorAll("[data-card-kind='partial-hint']").length,
        minutes_text: document.getElementById("minutes-panel")?.innerText || "",
        frontend_minutes_visible: document.getElementById("c-minutes")?.innerText.trim() === "已生成"
          && (document.getElementById("minutes-panel")?.innerText || "").trim().length > 20,
        minutes_count_text: document.getElementById("c-minutes")?.innerText || "",
        browser_console_error_count: 0,
        network_error_count: 0,
        session_meta: document.getElementById("session-meta")?.innerText || "",
        candidate_panel_text: document.getElementById("candidate-panel")?.innerText || "",
        meeting_cockpit_counts: readMeetingCockpitCounts(),
        meeting_cockpit_stage: readMeetingCockpitStage(),
        realtime_ui_metrics: realtimeMetrics,
        first_text_visible_latency_ms: realtimeMetrics.first_text_visible_latency_ms ?? null,
        first_partial_visible_latency_ms: realtimeMetrics.first_partial_visible_latency_ms ?? null,
        first_final_visible_latency_ms: realtimeMetrics.first_final_visible_latency_ms ?? null,
        first_audio_active_offset_ms: realtimeMetrics.first_audio_active_offset_ms ?? null,
        first_text_after_audio_active_latency_ms: realtimeMetrics.first_text_after_audio_active_latency_ms ?? null,
        first_partial_after_audio_active_latency_ms: realtimeMetrics.first_partial_after_audio_active_latency_ms ?? null,
        first_final_after_audio_active_latency_ms: realtimeMetrics.first_final_after_audio_active_latency_ms ?? null,
        partial_visible_count: realtimeMetrics.partial_visible_count || 0,
        final_visible_count: realtimeMetrics.final_visible_count || 0,
      };
    });
    return {
      ...fallback,
      ...domReport,
      realtime_ui_metrics: { ...realtimeFallback, ...(domReport.realtime_ui_metrics || {}) },
    };
  } catch (err) {
    return { ...fallback, ui_error: err.message };
  }
}

async function captureScreenshot(filename) {
  if (!page) return;
  try {
    const screenshot = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
    await writeFile(path.join(artifactRoot, filename), Buffer.from(screenshot.data, "base64"));
  } catch {}
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

async function waitForBrowserState(page, reader, predicate, timeoutMs, label) {
  const deadline = Date.now() + timeoutMs;
  let latestState = null;
  while (Date.now() < deadline) {
    latestState = await evaluate(page, reader);
    if (predicate(latestState)) return latestState;
    await delay(120);
  }
  throw new Error(`timed out waiting for ${label}: ${JSON.stringify(latestState)}`);
}

async function waitForAnyCdpExpression(page, expressions, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const expression of expressions) {
      const check = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (check.result?.value) return expression;
    }
    await delay(180);
  }
  throw new Error(`timed out waiting for any of: ${expressions.join(" OR ")}`);
}

async function waitForAnyCdpExpressionOrTimeout(page, expressions, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    for (const expression of expressions) {
      const check = await page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (check.result?.value) {
        return {
          status: "matched",
          matched_expression: expression,
        };
      }
    }
    await delay(180);
  }
  return {
    status: "timed_out",
    waited_ms: timeoutMs,
    expressions,
  };
}

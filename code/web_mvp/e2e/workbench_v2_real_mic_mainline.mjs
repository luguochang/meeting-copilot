import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

if (process.argv[2] === "--evaluate-report-contract") {
  const report = await evaluateArtifactReportContract(process.argv[3]);
  await new Promise((resolve, reject) => {
    process.stdout.write(`${JSON.stringify(report)}\n`, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
  process.exit(0);
}

const baseUrl = String(process.env.MEETING_COPILOT_BASE_URL || "http://127.0.0.1:8767").replace(/\/+$/, "");
const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const sourceAudio = path.resolve(
  process.env.MEETING_COPILOT_REAL_MIC_SOURCE_AUDIO
    || path.join(repoRoot, "artifacts", "tmp", "audio_fixtures", "two-turn-release-incident-16k.wav"),
);
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "browser_live_mic", `v2-real-mic-${Date.now()}`),
);
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9251");
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-v2-real-mic-"));
const diagnostics = { runtime_exceptions: [], console_errors: [], network_failures: [], http_5xx: [] };
const recordingSamples = [];
const sockets = [];
let chrome;
let player;

try {
  await mkdir(artifactRoot, { recursive: true });
  await waitForHttp(`${baseUrl}/health`, 30_000);
  await readFile(sourceAudio);

  chrome = spawn(chromePath, [
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    `--remote-debugging-port=${chromePort}`,
    `--user-data-dir=${chromeUserDataDir}`,
    "about:blank",
  ], { stdio: "ignore" });
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`, 30_000);

  const page = await createCdpPage(chromePort, `${baseUrl}/workbench`);
  await setViewport(page, { width: 1440, height: 900 });
  await waitFor(page, `document.querySelectorAll('.start-meeting-button').length === 1`, 20_000);
  await capture(page, "01-before-start.png");
  await evaluate(page, () => document.querySelector(".start-meeting-button")?.click());
  await waitFor(page, `document.querySelector('.end-meeting-button') !== null`, 30_000);
  await waitFor(page, `new URL(location.href).searchParams.has('meeting_id')`, 10_000);
  const meetingId = await evaluate(page, () => new URL(location.href).searchParams.get("meeting_id"));
  if (!meetingId) throw new Error("V2 start did not bind a meeting_id");

  player = spawn("afplay", [sourceAudio], { stdio: "ignore" });
  const playbackStartedAtMs = Date.now();
  let firstTextAtMs = null;
  let firstFinalAtMs = null;
  let firstSuggestionAtMs = null;
  let firstCorrectionAtMs = null;
  let playerFinished = false;
  player.once("exit", () => { playerFinished = true; });

  const recordingDeadline = Date.now() + 50_000;
  while (Date.now() < recordingDeadline) {
    const sample = await evaluate(page, () => ({
      at_ms: Date.now(),
      recording_status: document.querySelector('.meeting-statuses')?.textContent?.trim() || "",
      segment_count: document.querySelectorAll('.transcript-segment').length,
      partial_count: document.querySelectorAll('.active-partial').length,
      partial_text: document.querySelector('.active-partial p')?.textContent?.trim() || "",
      suggestion_count: document.querySelectorAll('.suggestion-card').length,
      suggestion_text: document.querySelector('.suggestion-card blockquote')?.textContent?.trim() || "",
      corrected_count: document.querySelectorAll('.correction-mark').length,
    }));
    recordingSamples.push(sample);
    if (firstTextAtMs === null && (sample.partial_count > 0 || sample.segment_count > 0)) firstTextAtMs = sample.at_ms;
    if (firstFinalAtMs === null && sample.segment_count > 0) firstFinalAtMs = sample.at_ms;
    if (firstSuggestionAtMs === null && sample.suggestion_count > 0) firstSuggestionAtMs = sample.at_ms;
    if (firstCorrectionAtMs === null && sample.corrected_count > 0) firstCorrectionAtMs = sample.at_ms;
    if (
      playerFinished
      && sample.segment_count > 0
      && sample.suggestion_count > 0
      && sample.corrected_count > 0
      && Date.now() - playbackStartedAtMs > 30_000
    ) break;
    await delay(1_000);
  }
  await capture(page, "02-live-transcript-and-suggestion.png");

  await evaluate(page, () => document.querySelector(".end-meeting-button")?.click());
  await waitFor(page, `document.querySelectorAll('[role="tab"]').length === 4`, 45_000);
  await waitForReviewArtifacts(meetingId, 120_000);
  await waitFor(page, `document.body.innerText.includes('会议复盘')`, 20_000);
  await capture(page, "03-review.png");

  const transcript = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/transcript?after_transcript_seq=0&limit=500`);
  const expectedTranscriptCount = Array.isArray(transcript.segments) ? transcript.segments.length : 0;
  await clickTab(page, "会议文字");
  await waitFor(
    page,
    `document.querySelectorAll('.review-transcript .transcript-segment').length === ${expectedTranscriptCount}`,
    15_000,
  );
  await evaluate(page, () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const transcriptUiState = await evaluate(page, () => {
    const container = document.querySelector('.review-transcript .transcript-scroll');
    if (!container) return { container: null, rows: [] };
    const containerRect = container.getBoundingClientRect();
    const tolerancePx = 1;
    return {
      container: {
        top: containerRect.top,
        bottom: containerRect.bottom,
        client_height: container.clientHeight,
        scroll_height: container.scrollHeight,
        scroll_top: container.scrollTop,
      },
      rows: [...document.querySelectorAll('.review-transcript .transcript-segment')].map((row) => {
        const rowRect = row.getBoundingClientRect();
        return {
          segment_id: row.getAttribute('data-segment-id') || "",
          text: row.querySelector('p')?.textContent?.trim() || "",
          corrected: Boolean(row.querySelector('.correction-mark')),
          visible: Boolean(
            row.getClientRects().length
            && rowRect.height > 0
            && rowRect.top >= containerRect.top - tolerancePx
            && rowRect.bottom <= containerRect.bottom + tolerancePx
          ),
          top: rowRect.top,
          bottom: rowRect.bottom,
        };
      }),
    };
  });
  const transcriptUi = transcriptUiState.rows;
  await capture(page, "04-complete-transcript.png");
  await clickTab(page, "录音");
  await waitFor(page, `document.querySelector('audio')?.getAttribute('src')?.includes('/audio/content') === true`, 20_000);
  await capture(page, "05-recording.png");

  const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
  const events = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/events?after_seq=0`);
  const traces = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/traces`);
  const audio = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/audio`);
  const legacyEvidence = await fetchJson(`${baseUrl}/live/asr/sessions/${encodeURIComponent(meetingId)}/events`);
  const audioResponse = await fetch(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/audio/content`);
  const audioBytes = new Uint8Array(await audioResponse.arrayBuffer());

  await page.send("Page.navigate", { url: `${baseUrl}/workbench` });
  await waitFor(page, `document.querySelectorAll('.history-row-open').length > 0`, 20_000);
  const history = await fetchJson(`${baseUrl}/v2/meetings`);
  const newestHistoryMeetingId = history.meetings?.[0]?.id
    || history.meetings?.[0]?.meeting_id
    || history.meetings?.[0]?.meetingId
    || null;
  await evaluate(page, (expectedMeetingId) => {
    document.querySelector(`.history-row-open[data-meeting-id="${CSS.escape(expectedMeetingId)}"]`)?.click();
  }, meetingId);
  await waitFor(page, `new URL(location.href).searchParams.get('meeting_id') === ${JSON.stringify(meetingId)}`, 20_000);
  const historyUiReopened = await evaluate(
    page,
    (expectedMeetingId) => new URL(location.href).searchParams.get("meeting_id") === expectedMeetingId,
    meetingId,
  );
  await capture(page, "06-history-reopened.png");

  const observabilityContract = evaluateJobAndRevisionContract({
    snapshot,
    segments: Array.isArray(transcript.segments) ? transcript.segments : [],
    events,
    traces,
  });
  const jobsContractPresent = observabilityContract.jobs_contract_present;
  const reviewJobs = snapshot.review_jobs || {};
  const segments = observabilityContract.segments;
  const suggestions = Array.isArray(snapshot.suggestions) ? snapshot.suggestions : [];
  const revisedEvents = observabilityContract.revised_events;
  const revisedSegmentIds = observabilityContract.revised_segment_ids;
  const canonicalById = new Map(segments.map((segment) => [
    segment.segment_id,
    String(segment.normalized_text || segment.text || "").trim(),
  ]));
  const traceRevisionCount = observabilityContract.trace_revision_count;
  const llmEvidence = legacyEvidence.llm_evidence || {};
  const report = {
    schema_version: "workbench-v2-real-mic-mainline.v1",
    verdict: "pending",
    meeting_id: meetingId,
    input_mode: "real_browser_microphone_with_acoustic_speaker_source",
    ui_coverage: "visible_chrome",
    fake_media_device_used: false,
    media_permission_auto_accepted: true,
    source_audio: sourceAudio,
    playback_started_at_ms: playbackStartedAtMs,
    first_text_latency_ms: firstTextAtMs === null ? null : firstTextAtMs - playbackStartedAtMs,
    first_final_latency_ms: firstFinalAtMs === null ? null : firstFinalAtMs - playbackStartedAtMs,
    first_suggestion_latency_ms: firstSuggestionAtMs === null ? null : firstSuggestionAtMs - playbackStartedAtMs,
    first_correction_latency_ms: firstCorrectionAtMs === null ? null : firstCorrectionAtMs - playbackStartedAtMs,
    live_sample_count: recordingSamples.length,
    live_partial_observed: recordingSamples.some((sample) => sample.partial_count > 0),
    live_final_observed: recordingSamples.some((sample) => sample.segment_count > 0),
    live_suggestion_observed: recordingSamples.some((sample) => sample.suggestion_count > 0),
    live_correction_observed: recordingSamples.some((sample) => sample.corrected_count > 0),
    jobs_contract_present: jobsContractPresent,
    transcript_segment_count: segments.length,
    transcript_revision_count: observabilityContract.transcript_revision_count,
    transcript_revision_event_count: revisedEvents.length,
    trace_revision_count: traceRevisionCount,
    transcript_ui: {
      row_count: transcriptUi.length,
      scroll_container: transcriptUiState.container,
      all_rows_visible: transcriptUi.every((row) => row.visible),
      canonical_text_match: transcriptUi.every((row) => canonicalById.get(row.segment_id) === row.text),
      corrected_row_count: transcriptUi.filter((row) => row.corrected).length,
      corrected_ids_match: transcriptUi
        .filter((row) => row.corrected)
        .every((row) => revisedSegmentIds.has(row.segment_id))
        && [...revisedSegmentIds].every((segmentId) => transcriptUi.some((row) => row.segment_id === segmentId && row.corrected)),
    },
    committed_suggestion_count: suggestions.filter((item) => item.status === "committed").length,
    correction_jobs: observabilityContract.correction_jobs.map(jobSummary),
    suggestion_jobs: observabilityContract.suggestion_jobs.map(jobSummary),
    review_jobs: Object.fromEntries(Object.entries(reviewJobs).map(([kind, job]) => [kind, jobSummary(job)])),
    minutes_ready: Boolean(snapshot.minutes?.markdown),
    approach_card_count: Array.isArray(snapshot.approach_cards) ? snapshot.approach_cards.length : 0,
    formal_derivation_status: snapshot.diagnostics?.formal_derivation_status
      || snapshot.formal_derivation_status
      || null,
    audio: {
      assembled: audio.assembled === true,
      duration_ms: audio.duration_ms,
      chunk_count: audio.chunk_count,
      tracks: audio.tracks,
      content_http_status: audioResponse.status,
      content_bytes: audioBytes.byteLength,
    },
    provider: {
      asr_provider: legacyEvidence.provider,
      asr_provider_mode: legacyEvidence.provider_mode,
      asr_is_mock: legacyEvidence.is_mock,
      llm_provider: llmEvidence.provider,
      llm_model: llmEvidence.model,
      llm_is_mock: llmEvidence.is_mock,
      llm_called: llmEvidence.llm_called,
      llm_call_count: llmEvidence.llm_call_count,
      llm_usage_total_tokens: llmEvidence.llm_usage_total_tokens,
      gateway_base_url_kind: llmEvidence.gateway_base_url_kind,
    },
    history_reopened: historyUiReopened && newestHistoryMeetingId === meetingId,
    event_count: Array.isArray(events.events) ? events.events.length : 0,
    trace_count: Array.isArray(traces.traces) ? traces.traces.length : 0,
    diagnostics,
  };
  const blockers = [...observabilityContract.blockers];
  if (!report.live_partial_observed) blockers.push("live_partial_missing");
  if (!report.live_final_observed || report.transcript_segment_count < 1) blockers.push("live_final_missing");
  if (!report.live_suggestion_observed || report.committed_suggestion_count < 1) blockers.push("realtime_suggestion_missing");
  if (!report.live_correction_observed) blockers.push("realtime_correction_not_visible");
  if (report.transcript_revision_count < 1 && !blockers.includes("correction_projection_missing")) blockers.push("correction_projection_missing");
  if (
    report.transcript_ui.row_count !== report.transcript_segment_count
    || !report.transcript_ui.all_rows_visible
    || !report.transcript_ui.canonical_text_match
    || !report.transcript_ui.corrected_ids_match
  ) blockers.push("transcript_ui_projection_mismatch");
  if (!report.minutes_ready || report.approach_card_count < 1) {
    blockers.push(
      report.formal_derivation_status === "suppressed_by_asr_semantic_quality"
        ? "post_meeting_review_paused_by_asr_quality"
        : "post_meeting_review_missing",
    );
  }
  if (!report.audio.assembled || report.audio.content_http_status !== 200 || report.audio.content_bytes <= 44) blockers.push("recording_missing");
  if (report.provider.asr_provider_mode !== "real" || report.provider.asr_is_mock !== false) blockers.push("real_local_asr_missing");
  if (!report.provider.llm_called || report.provider.llm_is_mock !== false || report.provider.gateway_base_url_kind !== "remote") blockers.push("real_relay_missing");
  if (!report.history_reopened) blockers.push("history_reopen_missing");
  if (diagnostics.runtime_exceptions.length || diagnostics.console_errors.length || diagnostics.http_5xx.length) blockers.push("browser_runtime_errors");
  report.blockers = blockers;
  report.verdict = blockers.length ? "no_go" : "go";

  await writeFile(path.join(artifactRoot, "recording-samples.json"), JSON.stringify(recordingSamples, null, 2));
  await writeFile(path.join(artifactRoot, "snapshot.json"), JSON.stringify(snapshot, null, 2));
  await writeFile(path.join(artifactRoot, "events.json"), JSON.stringify(events, null, 2));
  await writeFile(path.join(artifactRoot, "traces.json"), JSON.stringify(traces, null, 2));
  await writeFile(path.join(artifactRoot, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ artifact_root: artifactRoot, ...report }, null, 2));
  if (report.verdict !== "go") throw new Error(`V2 real microphone mainline failed: ${blockers.join(",")}`);
} finally {
  if (player && player.exitCode === null) player.kill("SIGTERM");
  for (const socket of sockets) socket.close();
  if (chrome && chrome.exitCode === null) {
    chrome.kill("SIGTERM");
    await delay(1_000);
  }
  await rm(chromeUserDataDir, {
    recursive: true,
    force: true,
    maxRetries: 10,
    retryDelay: 200,
  }).catch((error) => {
    console.warn(`Unable to remove temporary Chrome profile: ${error.code || error.message}`);
  });
}

function evaluateJobAndRevisionContract({ snapshot, segments, events, traces }) {
  const jobsArrayPresent = Array.isArray(snapshot?.jobs);
  const jobs = jobsArrayPresent ? snapshot.jobs : [];
  const jobsContractValid = jobsArrayPresent && jobs.every(jobStatusSummaryContractValid);
  const correctionJobs = jobs.filter((job) => job.kind === "correction");
  const suggestionJobs = jobs.filter((job) => job.kind === "suggestion");
  const safeSegments = Array.isArray(segments) ? segments : [];
  const revisedEvents = Array.isArray(events?.events)
    ? events.events.filter((event) => event.type === "transcript.segment.revised")
    : [];
  const revisedSegmentIds = new Set(
    safeSegments
      .filter((segment) => Number(segment.revision || 1) > 1)
      .map((segment) => String(segment.segment_id || "").trim())
      .filter(Boolean),
  );
  const revisedEventSegmentIds = new Set(
    revisedEvents.map(revisionEventSegmentId).filter(Boolean),
  );
  const traceRevisionCount = Array.isArray(traces?.traces)
    ? traces.traces.reduce((total, trace) => total + traceRevisionValue(trace), 0)
    : 0;
  const projectionIdsMatch = revisedEventSegmentIds.size === revisedSegmentIds.size
    && [...revisedEventSegmentIds].every((segmentId) => revisedSegmentIds.has(segmentId));
  const blockers = [];
  if (!jobsArrayPresent) blockers.push("jobs_contract_missing");
  else if (!jobsContractValid) blockers.push("jobs_contract_invalid");
  if (!correctionLaneComplete(correctionJobs)) {
    blockers.push("correction_jobs_incomplete");
  }
  if (!suggestionJobs.length || suggestionJobs.some((job) => !jobTerminalSucceeded(job))) {
    blockers.push("suggestion_jobs_incomplete");
  }
  if (revisedEvents.length !== revisedSegmentIds.size || !projectionIdsMatch) {
    blockers.push("correction_projection_missing");
  }
  if (traceRevisionCount > 0 && (!revisedEvents.length || !revisedSegmentIds.size)) {
    blockers.push("correction_projection_mismatch");
  }
  return {
    blockers,
    jobs_contract_present: jobsContractValid,
    correction_jobs: correctionJobs,
    suggestion_jobs: suggestionJobs,
    segments: safeSegments,
    revised_events: revisedEvents,
    revised_segment_ids: revisedSegmentIds,
    transcript_revision_count: revisedSegmentIds.size,
    trace_revision_count: traceRevisionCount,
  };
}

function jobStatusSummaryContractValid(job) {
  if (!job || typeof job !== "object" || Array.isArray(job)) return false;
  const expectedFields = [
    "attempts",
    "completed_at_ms",
    "created_at_ms",
    "error_class",
    "id",
    "kind",
    "max_attempts",
    "status",
    "updated_at_ms",
  ];
  const actualFields = Object.keys(job).sort();
  if (actualFields.length !== expectedFields.length) return false;
  if (!expectedFields.every((field, index) => actualFields[index] === field)) return false;
  return typeof job.id === "string"
    && typeof job.kind === "string"
    && typeof job.status === "string"
    && Number.isInteger(job.attempts)
    && Number.isInteger(job.max_attempts)
    && (job.error_class === null || typeof job.error_class === "string")
    && Number.isFinite(job.created_at_ms)
    && Number.isFinite(job.updated_at_ms)
    && (job.completed_at_ms === null || Number.isFinite(job.completed_at_ms));
}

function traceRevisionValue(trace) {
  const rawValue = Number(
    trace?.stages?.validated?.attributes?.reconciled_revision_count
    ?? trace?.stages?.validated?.attributes?.provider_revision_count
    ?? trace?.stages?.validated?.attributes?.revision_count
    ?? 0,
  );
  return Number.isFinite(rawValue) && rawValue > 0 ? rawValue : 0;
}

function revisionEventSegmentId(event) {
  return String(event?.aggregate_id || event?.payload?.segment_id || "").trim();
}

function jobSummary(job) {
  if (!job || typeof job !== "object") return null;
  return {
    id: job.id,
    kind: job.kind,
    status: job.status,
    attempts: job.attempts,
    max_attempts: job.max_attempts,
    error_class: job.error_class,
    created_at_ms: job.created_at_ms,
    updated_at_ms: job.updated_at_ms,
    completed_at_ms: job.completed_at_ms,
  };
}

function jobTerminalSucceeded(job) {
  return job?.status === "succeeded";
}

function correctionJobTerminalAccepted(job) {
  return jobTerminalSucceeded(job)
    || (job?.status === "cancelled" && job?.error_class === "evidence_superseded");
}

function correctionLaneComplete(jobs) {
  return jobs.length > 0
    && jobs.some(jobTerminalSucceeded)
    && jobs.every(correctionJobTerminalAccepted);
}

async function evaluateArtifactReportContract(artifactDirectory) {
  if (!artifactDirectory) throw new Error("--evaluate-report-contract requires an artifact directory");
  const root = path.resolve(artifactDirectory);
  const [snapshot, events, traces] = await Promise.all([
    readArtifactJson(root, "snapshot.json"),
    readArtifactJson(root, "events.json"),
    readArtifactJson(root, "traces.json"),
  ]);
  const contract = evaluateJobAndRevisionContract({
    snapshot,
    segments: Array.isArray(snapshot.segments) ? snapshot.segments : [],
    events,
    traces,
  });
  return {
    schema_version: "workbench-v2-real-mic-report-contract.v1",
    verdict: contract.blockers.length ? "no_go" : "go",
    blockers: contract.blockers,
    jobs_contract_present: contract.jobs_contract_present,
    correction_jobs: contract.correction_jobs.map(jobSummary),
    suggestion_jobs: contract.suggestion_jobs.map(jobSummary),
    transcript_revision_count: contract.transcript_revision_count,
    transcript_revision_event_count: contract.revised_events.length,
    trace_revision_count: contract.trace_revision_count,
  };
}

async function readArtifactJson(root, fileName) {
  return JSON.parse(await readFile(path.join(root, fileName), "utf-8"));
}

async function waitForReviewArtifacts(meetingId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    const reviewJobs = Object.values(snapshot.review_jobs || {});
    if (!Array.isArray(snapshot.jobs)) throw new Error("snapshot jobs contract is missing");
    if (!snapshot.jobs.every(jobStatusSummaryContractValid)) throw new Error("snapshot jobs contract is invalid");
    const correctionJobs = snapshot.jobs.filter((job) => job.kind === "correction");
    const suggestionJobs = snapshot.jobs.filter((job) => job.kind === "suggestion");
    const qualityPaused = snapshot.diagnostics?.formal_derivation_status === "suppressed_by_asr_semantic_quality"
      || snapshot.formal_derivation_status === "suppressed_by_asr_semantic_quality";
    if (!correctionJobs.length) throw new Error("transcript correction job is missing");
    if (!suggestionJobs.length) throw new Error("suggestion job is missing");
    if (correctionJobs.some((job) => job.status === "failed" || (
      job.status === "cancelled" && job.error_class !== "evidence_superseded"
    ))) {
      throw new Error("transcript correction reached a terminal failure");
    }
    if (suggestionJobs.some((job) => ["failed", "cancelled"].includes(job.status))) {
      throw new Error("suggestion generation reached a terminal failure");
    }
    if (correctionJobs.some((job) => ![
      "pending",
      "running",
      "retry_wait",
      "succeeded",
      "cancelled",
    ].includes(job.status)) || suggestionJobs.some((job) => ![
      "pending",
      "running",
      "retry_wait",
      "succeeded",
    ].includes(job.status))) {
      throw new Error("AI job has an unsupported status");
    }
    if (
      snapshot.runtime?.phase === "ended"
      && snapshot.minutes?.markdown
      && Array.isArray(snapshot.approach_cards)
      && snapshot.approach_cards.length > 0
      && reviewJobs.length === 3
      && reviewJobs.every((job) => job.status === "succeeded")
      && correctionLaneComplete(correctionJobs)
      && suggestionJobs.every(jobTerminalSucceeded)
    ) return;
    if (
      snapshot.runtime?.phase === "ended"
      && qualityPaused
      && reviewJobs.length === 3
      && reviewJobs.every((job) => ["succeeded", "failed"].includes(job.status))
      && correctionLaneComplete(correctionJobs)
      && suggestionJobs.every(jobTerminalSucceeded)
    ) return;
    await delay(500);
  }
  throw new Error("post-meeting review did not reach a terminal state");
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
    const failedUrl = urls.get(params.requestId) || "";
    if (failedUrl.endsWith("/favicon.ico") || (failedUrl.includes("/audio/content") && params.errorText === "net::ERR_ABORTED")) return;
    diagnostics.network_failures.push({ url: failedUrl, error: params.errorText });
  });
  page.on("Network.responseReceived", (params) => {
    if (Number(params.response?.status || 0) >= 500) diagnostics.http_5xx.push({ url: params.response?.url, status: params.response?.status });
  });
  page.on("Page.javascriptDialogOpening", () => {
    void page.send("Page.handleJavaScriptDialog", { accept: true });
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

async function waitFor(page, expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await page.send("Runtime.evaluate", { expression: `Boolean(${expression})`, returnByValue: true });
    if (result.result?.value) return;
    await delay(100);
  }
  throw new Error(`timed out waiting for ${expression}`);
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

async function setViewport(page, { width, height }) {
  await page.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
}

async function capture(page, fileName) {
  const result = await page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, fromSurface: true });
  await writeFile(path.join(artifactRoot, fileName), Buffer.from(result.data, "base64"));
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
      // Retry until the bounded deadline.
    }
    await delay(150);
  }
  throw new Error(`timed out waiting for ${url}`);
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

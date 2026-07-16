import { spawn, execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";


const execFileAsync = promisify(execFile);
const semanticMinUniqueAudioSeconds = 300;
const semanticMaxRepetitionRatio = 0.1;

if (process.argv[2] === "--evaluate-input-metadata-contract") {
  const payload = JSON.parse(await readFile(process.argv[3], "utf8"));
  await writeJsonToStdout(evaluateSemanticAcceptance(payload));
  process.exit(0);
}

if (process.argv[2] === "--probe-input-audio") {
  const audioPath = path.resolve(process.argv[3]);
  const audioBytes = await readFile(audioPath);
  await writeJsonToStdout({
    source_audio_duration_seconds: await probeAudioDurationSeconds(audioPath, audioBytes),
    source_audio_sha256: createHash("sha256").update(audioBytes).digest("hex"),
  });
  process.exit(0);
}

const baseUrl = String(process.env.MEETING_COPILOT_BASE_URL || "http://127.0.0.1:8767").replace(/\/+$/, "");
const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const sourceAudio = path.resolve(
  process.env.MEETING_COPILOT_REAL_MIC_SOURCE_AUDIO
    || path.join(repoRoot, "artifacts", "tmp", "audio_fixtures", "two-turn-release-incident-16k.wav"),
);
const targetDurationSeconds = positiveNumber(process.env.MEETING_COPILOT_SOAK_DURATION_SECONDS, 180);
const sampleIntervalMs = positiveNumber(process.env.MEETING_COPILOT_SOAK_SAMPLE_INTERVAL_MS, 5_000);
const maxPostProcessingSeconds = positiveNumber(
  process.env.MEETING_COPILOT_MAX_POST_PROCESSING_SECONDS,
  45,
);
const requireOneHour = process.env.MEETING_COPILOT_REQUIRE_ONE_HOUR === "1";
const requireFullReview = requireOneHour || process.env.MEETING_COPILOT_REQUIRE_FULL_REVIEW === "1";
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "long_meeting", `v2-long-mic-${Date.now()}`),
);
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9252");
const cdpOperationTimeoutMs = 10_000;
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-v2-long-mic-"));
const diagnostics = {
  runtime_exceptions: [],
  console_errors: [],
  network_failures: [],
  allowlisted_network_cancellations: [],
  http_5xx: [],
};
const samples = [];
const playlistEntries = [];
const sockets = [];
let chrome;
let player;
let playbackStopped = false;
let sourceAudioDurationSeconds = null;
let sourceAudioSha256 = "";

try {
  await mkdir(artifactRoot, { recursive: true });
  await waitForHttp(`${baseUrl}/health`, 30_000);
  const sourceAudioBytes = await readFile(sourceAudio);
  sourceAudioSha256 = createHash("sha256").update(sourceAudioBytes).digest("hex");
  sourceAudioDurationSeconds = await probeAudioDurationSeconds(sourceAudio, sourceAudioBytes);
  const backendPid = await resolveBackendPid(baseUrl);

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
  if (!meetingId) throw new Error("long soak did not bind a meeting_id");

  const playbackStartedAtMs = Date.now();
  phase("recording_started");
  const playbackDeadlineMs = playbackStartedAtMs + targetDurationSeconds * 1_000;
  const playbackTask = playAudioUntil(playbackDeadlineMs);
  let firstTextAtMs = null;
  let firstFinalAtMs = null;
  let firstSuggestionAtMs = null;
  let firstCorrectionAtMs = null;

  while (Date.now() < playbackDeadlineMs) {
    const sampleStartedAt = performance.now();
    const [ui, snapshotProbe, rssKb] = await Promise.all([
      evaluate(page, () => ({
        at_ms: Date.now(),
        segment_count: document.querySelectorAll(".transcript-segment").length,
        partial_count: document.querySelectorAll(".active-partial").length,
        suggestion_count: document.querySelectorAll(".suggestion-card").length,
        corrected_count: document.querySelectorAll(".correction-mark").length,
        body_scroll_height: document.body.scrollHeight,
      })),
      timedFetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`),
      sampleRssKb(backendPid),
    ]);
    const sample = {
      ...ui,
      snapshot_latency_ms: snapshotProbe.elapsed_ms,
      snapshot_last_seq: snapshotProbe.payload.last_seq,
      snapshot_segment_count: Array.isArray(snapshotProbe.payload.segments)
        ? snapshotProbe.payload.segments.length
        : 0,
      backend_rss_kb: rssKb,
    };
    samples.push(sample);
    if (firstTextAtMs === null && (sample.partial_count > 0 || sample.segment_count > 0)) firstTextAtMs = sample.at_ms;
    if (firstFinalAtMs === null && sample.segment_count > 0) firstFinalAtMs = sample.at_ms;
    if (firstSuggestionAtMs === null && sample.suggestion_count > 0) firstSuggestionAtMs = sample.at_ms;
    if (firstCorrectionAtMs === null && sample.corrected_count > 0) firstCorrectionAtMs = sample.at_ms;
    const spentMs = performance.now() - sampleStartedAt;
    await delay(Math.max(100, sampleIntervalMs - spentMs));
  }
  playbackStopped = true;
  if (player && player.exitCode === null) player.kill("SIGTERM");
  await waitForPlaybackStop(playbackTask);
  const recordingStoppedAtMs = Date.now();
  phase("end_requested");
  const endScheduled = await evaluate(page, () => {
    const button = document.querySelector(".end-meeting-button");
    if (!button) return false;
    setTimeout(() => button.click(), 0);
    return true;
  });
  if (!endScheduled) throw new Error("end_meeting_button_missing");
  phase("end_command_sent");
  await waitForMeetingEnded(meetingId, 60_000);
  phase("server_ended");
  await waitForLongReview(meetingId, 300_000);
  const reviewReadyAtMs = Date.now();
  phase("review_ready");
  await waitFor(page, `document.querySelectorAll('[role="tab"]').length === 4`, 30_000);
  await capture(page, "03-review.png");

  const transcript = await fetchCompleteTranscript(meetingId);
  const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
  const events = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/events?after_seq=0`);
  const audio = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/audio`);
  const legacyEvidence = await fetchJson(`${baseUrl}/live/asr/sessions/${encodeURIComponent(meetingId)}/events`);

  await clickTab(page, "会议文字");
  await waitFor(
    page,
    `document.querySelectorAll('.review-transcript .transcript-segment').length === ${transcript.segments.length}`,
    30_000,
  );
  const transcriptUi = await evaluate(page, async () => {
    const container = document.querySelector(".review-transcript .transcript-scroll");
    const rows = [...document.querySelectorAll(".review-transcript .transcript-segment")];
    if (!container || !rows.length) return { row_count: rows.length, last_row_visible_after_scroll: false };
    const previousScrollBehavior = container.style.scrollBehavior;
    container.style.scrollBehavior = "auto";
    for (let attempt = 0; attempt < 3; attempt += 1) {
      container.scrollTop = container.scrollHeight;
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }
    const containerRect = container.getBoundingClientRect();
    const lastRect = rows.at(-1).getBoundingClientRect();
    const result = {
      row_count: rows.length,
      client_height: container.clientHeight,
      scroll_height: container.scrollHeight,
      last_row_visible_after_scroll: lastRect.top >= containerRect.top - 1 && lastRect.bottom <= containerRect.bottom + 1,
    };
    container.style.scrollBehavior = previousScrollBehavior;
    return result;
  });
  await capture(page, "04-complete-transcript-tail.png");

  await page.send("Page.navigate", { url: `${baseUrl}/workbench` });
  await waitFor(page, `document.querySelectorAll('.history-row-open').length > 0`, 20_000);
  await evaluate(page, (expectedMeetingId) => {
    document.querySelector(`.history-row-open[data-meeting-id="${CSS.escape(expectedMeetingId)}"]`)?.click();
  }, meetingId);
  await waitFor(page, `new URL(location.href).searchParams.get('meeting_id') === ${JSON.stringify(meetingId)}`, 20_000);
  const historyReopened = await evaluate(
    page,
    (expectedMeetingId) => new URL(location.href).searchParams.get("meeting_id") === expectedMeetingId,
    meetingId,
  );
  await capture(page, "05-history-reopened.png");

  const rssValues = samples.map((sample) => sample.backend_rss_kb).filter(Number.isFinite);
  const snapshotLatencies = samples.map((sample) => sample.snapshot_latency_ms).filter(Number.isFinite);
  const earlyLatencies = snapshotLatencies.slice(0, Math.max(1, Math.floor(snapshotLatencies.length / 4)));
  const lateLatencies = snapshotLatencies.slice(Math.max(0, Math.floor(snapshotLatencies.length * 0.75)));
  const llmEvidence = legacyEvidence.llm_evidence || {};
  const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : [];
  const correctionJobs = jobs.filter((job) => job.kind === "correction");
  const suggestionJobs = jobs.filter((job) => job.kind === "suggestion");
  const reviewJobs = Object.values(snapshot.review_jobs || {});
  const committedSuggestionCount = Array.isArray(snapshot.suggestions)
    ? snapshot.suggestions.filter((item) => item.status === "committed").length
    : 0;
  const minutesReady = Boolean(snapshot.minutes?.markdown);
  const approachCardCount = Array.isArray(snapshot.approach_cards) ? snapshot.approach_cards.length : 0;
  const formalDerivationSuppressed = legacyEvidence.formal_derivation_status === "suppressed_by_asr_semantic_quality"
    || (legacyEvidence.degradation_reasons || []).includes("asr_semantic_quality_blocked");
  const correctionLaneComplete = correctionJobs.length > 0
    && correctionJobs.some((job) => job.status === "succeeded")
    && correctionJobs.every((job) => job.status === "succeeded"
      || (job.status === "cancelled" && job.error_class === "evidence_superseded"));
  const suggestionLaneComplete = suggestionJobs.length > 0
    && suggestionJobs.some((job) => job.status === "succeeded")
    && suggestionJobs.every((job) => job.status === "succeeded"
      || (job.status === "cancelled" && job.error_class === "evidence_superseded"));
  const actualRecordingSeconds = Number(audio.duration_ms || 0) / 1_000;
  const recordingWallClockSeconds = (recordingStoppedAtMs - playbackStartedAtMs) / 1_000;
  const postProcessingSeconds = (reviewReadyAtMs - recordingStoppedAtMs) / 1_000;
  const playlistManifest = buildPlaylistManifest({
    entries: playlistEntries,
    sourceAudioDurationSeconds,
    sourceAudioSha256,
  });
  const playbackCount = playlistManifest.playback_count;
  const repetitionRatio = calculateRepetitionRatio(
    playlistManifest.entries,
    sourceAudioDurationSeconds,
  );
  const inputMode = playbackCount === 1 && repetitionRatio === 0
    ? "real_browser_microphone_with_unique_source_audio"
    : "real_browser_microphone_with_repeated_acoustic_fixture";
  const semanticAcceptance = evaluateSemanticAcceptance({
    target_duration_seconds: targetDurationSeconds,
    source_audio_duration_seconds: sourceAudioDurationSeconds,
    source_audio_sha256: sourceAudioSha256,
    playback_count: playbackCount,
    playlist_manifest: playlistManifest,
    repetition_ratio: repetitionRatio,
  });
  const countsAsPhase2OneHourGate = targetDurationSeconds >= 3_600
    && recordingWallClockSeconds >= 3_590
    && actualRecordingSeconds >= 3_590;
  const countsAsPhase2SemanticGate = countsAsPhase2OneHourGate
    && semanticAcceptance.semantic_acceptance_eligible
    && !formalDerivationSuppressed
    && minutesReady
    && approachCardCount > 0;
  const report = {
    schema_version: "workbench-v2-long-mic-soak.v1",
    verdict: "pending",
    meeting_id: meetingId,
    target_duration_seconds: targetDurationSeconds,
    recording_wall_clock_seconds: recordingWallClockSeconds,
    post_processing_seconds: postProcessingSeconds,
    total_wall_clock_seconds: (reviewReadyAtMs - playbackStartedAtMs) / 1_000,
    max_post_processing_seconds: maxPostProcessingSeconds,
    actual_recording_seconds: actualRecordingSeconds,
    source_audio_duration_seconds: sourceAudioDurationSeconds,
    source_audio_sha256: sourceAudioSha256,
    playback_count: playbackCount,
    playlist_manifest: playlistManifest,
    repetition_ratio: repetitionRatio,
    semantic_acceptance_eligible: semanticAcceptance.semantic_acceptance_eligible,
    semantic_acceptance_blockers: semanticAcceptance.semantic_acceptance_blockers,
    semantic_acceptance_policy: {
      scope: "input_provenance_and_repetition",
      required_unique_audio_seconds: semanticAcceptance.required_unique_audio_seconds,
      max_repetition_ratio: semanticAcceptance.max_repetition_ratio,
    },
    counts_as_phase2_one_hour_gate: countsAsPhase2OneHourGate,
    counts_as_phase2_semantic_gate: countsAsPhase2SemanticGate,
    input_mode: inputMode,
    fake_media_device_used: false,
    first_text_latency_ms: firstTextAtMs === null ? null : firstTextAtMs - playbackStartedAtMs,
    first_final_latency_ms: firstFinalAtMs === null ? null : firstFinalAtMs - playbackStartedAtMs,
    first_suggestion_latency_ms: firstSuggestionAtMs === null ? null : firstSuggestionAtMs - playbackStartedAtMs,
    first_correction_latency_ms: firstCorrectionAtMs === null ? null : firstCorrectionAtMs - playbackStartedAtMs,
    sample_count: samples.length,
    transcript_segment_count: transcript.segments.length,
    event_count: Array.isArray(events.events) ? events.events.length : 0,
    full_review_required: requireFullReview,
    formal_review: {
      jobs_contract_present: Array.isArray(snapshot.jobs),
      correction_job_count: correctionJobs.length,
      correction_lane_complete: correctionLaneComplete,
      suggestion_job_count: suggestionJobs.length,
      suggestion_jobs_succeeded: suggestionJobs.length > 0 && suggestionJobs.every((job) => job.status === "succeeded"),
      suggestion_lane_complete: suggestionLaneComplete,
      committed_suggestion_count: committedSuggestionCount,
      review_jobs: reviewJobs.map((job) => ({ kind: job.kind, status: job.status, error_class: job.error_class })),
      minutes_ready: minutesReady,
      approach_card_count: approachCardCount,
      formal_derivation_suppressed: formalDerivationSuppressed,
    },
    transcript_ui: transcriptUi,
    audio: {
      assembled: audio.assembled === true,
      duration_ms: audio.duration_ms,
      chunk_count: audio.chunk_count,
      file_size_bytes: audio.file_size_bytes,
      tracks: audio.tracks,
    },
    provider: {
      asr_provider: legacyEvidence.provider,
      asr_provider_mode: legacyEvidence.provider_mode,
      asr_is_mock: legacyEvidence.is_mock,
      llm_provider: llmEvidence.provider,
      llm_model: llmEvidence.model,
      llm_is_mock: llmEvidence.is_mock,
      llm_call_count: llmEvidence.llm_call_count,
      llm_usage_total_tokens: llmEvidence.llm_usage_total_tokens,
      gateway_base_url_kind: llmEvidence.gateway_base_url_kind,
    },
    memory: {
      backend_pid_observed: backendPid !== null,
      rss_start_mb: rssValues.length ? rssValues[0] / 1_024 : null,
      rss_peak_mb: rssValues.length ? Math.max(...rssValues) / 1_024 : null,
      rss_end_mb: rssValues.length ? rssValues.at(-1) / 1_024 : null,
      rss_growth_mb: rssValues.length ? (rssValues.at(-1) - rssValues[0]) / 1_024 : null,
    },
    snapshot_latency: {
      sample_count: snapshotLatencies.length,
      early_median_ms: median(earlyLatencies),
      late_median_ms: median(lateLatencies),
      max_ms: snapshotLatencies.length ? Math.max(...snapshotLatencies) : null,
    },
    history_reopened: historyReopened,
    diagnostics,
  };
  const blockers = [];
  if (requireOneHour && !report.counts_as_phase2_one_hour_gate) blockers.push("one_hour_duration_not_reached");
  if (requireOneHour && !report.semantic_acceptance_eligible) blockers.push("semantic_acceptance_input_ineligible");
  if (actualRecordingSeconds < targetDurationSeconds - 10) blockers.push("recording_duration_short");
  if (postProcessingSeconds > maxPostProcessingSeconds) blockers.push("post_processing_duration_exceeded");
  if (!report.audio.assembled || Number(report.audio.chunk_count || 0) < Math.floor(targetDurationSeconds / 5) - 2) blockers.push("recording_chunks_missing");
  if (report.transcript_segment_count < Math.max(1, Math.floor(targetDurationSeconds / 60))) blockers.push("transcript_growth_missing");
  if (report.transcript_ui.row_count !== report.transcript_segment_count || !report.transcript_ui.last_row_visible_after_scroll) blockers.push("transcript_tail_not_reachable");
  if (!report.history_reopened) blockers.push("history_reopen_missing");
  if (report.provider.asr_provider_mode !== "real" || report.provider.asr_is_mock !== false) blockers.push("real_local_asr_missing");
  if (report.memory.rss_growth_mb !== null && report.memory.rss_growth_mb > 256) blockers.push("memory_growth_exceeded");
  if (
    report.snapshot_latency.late_median_ms !== null
    && report.snapshot_latency.early_median_ms !== null
    && report.snapshot_latency.late_median_ms > Math.max(750, report.snapshot_latency.early_median_ms * 3)
  ) blockers.push("snapshot_latency_growth_exceeded");
  if (
    diagnostics.runtime_exceptions.length
    || diagnostics.console_errors.length
    || diagnostics.network_failures.length
    || diagnostics.http_5xx.length
  ) blockers.push("browser_runtime_errors");
  if (requireFullReview) {
    if (!Array.isArray(snapshot.jobs)) blockers.push("jobs_contract_missing");
    if (!correctionLaneComplete) blockers.push("correction_jobs_incomplete");
    if (!suggestionLaneComplete) blockers.push("suggestion_jobs_incomplete");
    if (!reviewJobs.length || reviewJobs.length !== 3 || reviewJobs.some((job) => job.status !== "succeeded")) blockers.push("review_jobs_incomplete");
    if (committedSuggestionCount < 1) blockers.push("committed_suggestion_missing");
    if (!minutesReady || approachCardCount < 1) blockers.push("post_meeting_review_missing");
    if (formalDerivationSuppressed) blockers.push("formal_derivation_suppressed");
  }
  report.blockers = blockers;
  report.verdict = blockers.length ? "no_go" : "go";

  await writeFile(path.join(artifactRoot, "samples.json"), JSON.stringify(samples, null, 2));
  await writeFile(path.join(artifactRoot, "report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ artifact_root: artifactRoot, ...report }, null, 2));
  if (report.verdict !== "go") throw new Error(`long microphone soak failed: ${blockers.join(",")}`);
} finally {
  playbackStopped = true;
  if (player && player.exitCode === null) player.kill("SIGTERM");
  for (const socket of sockets) socket.close();
  if (chrome && chrome.exitCode === null) {
    chrome.kill("SIGTERM");
    await delay(1_000);
  }
  await rm(chromeUserDataDir, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }).catch(() => undefined);
}


async function playAudioUntil(deadlineMs) {
  while (!playbackStopped && Date.now() < deadlineMs) {
    const remainingSeconds = Math.max(1, Math.ceil((deadlineMs - Date.now()) / 1_000));
    const entry = {
      playback_index: playlistEntries.length + 1,
      source_audio_sha256: sourceAudioSha256,
      requested_max_duration_seconds: remainingSeconds,
      started_at_ms: Date.now(),
      ended_at_ms: null,
      played_seconds: null,
      exit_code: null,
      signal: null,
    };
    playlistEntries.push(entry);
    player = spawn("afplay", ["-t", String(remainingSeconds), sourceAudio], { stdio: "ignore" });
    try {
      await waitForChildExit(player);
    } finally {
      entry.ended_at_ms = Date.now();
      entry.played_seconds = roundSeconds((entry.ended_at_ms - entry.started_at_ms) / 1_000);
      entry.exit_code = player.exitCode;
      entry.signal = player.signalCode;
    }
  }
}

async function waitForChildExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error) => {
      if (settled) return;
      settled = true;
      child.off("exit", onExit);
      child.off("error", onError);
      error ? reject(error) : resolve();
    };
    const onExit = () => finish();
    const onError = (error) => finish(error);
    child.once("exit", onExit);
    child.once("error", onError);
    if (child.exitCode !== null || child.signalCode !== null) finish();
  });
}

async function waitForPlaybackStop(task) {
  let timer;
  try {
    await Promise.race([
      task,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error("audio_playback_stop_timeout")),
          5_000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function waitForLongReview(meetingId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    const reviewJobs = Object.values(snapshot.review_jobs || {});
    if (
      snapshot.runtime?.phase === "ended"
      && snapshot.audio?.status === "saved"
      && reviewJobs.length === 3
      && reviewJobs.every((job) => ["succeeded", "failed", "cancelled"].includes(job.status))
    ) return;
    await delay(1_000);
  }
  throw new Error("long meeting post-processing did not finish");
}

async function waitForMeetingEnded(meetingId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const snapshot = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/snapshot`);
    if (snapshot.runtime?.phase === "ended") return;
    await delay(500);
  }
  throw new Error("meeting did not reach ended state");
}

async function fetchCompleteTranscript(meetingId) {
  const segments = [];
  let after = 0;
  for (;;) {
    const page = await fetchJson(`${baseUrl}/v2/meetings/${encodeURIComponent(meetingId)}/transcript?after_transcript_seq=${after}&limit=500`);
    const current = Array.isArray(page.segments) ? page.segments : [];
    segments.push(...current);
    if (!page.has_more || !current.length) break;
    after = Number(page.next_after_transcript_seq || current.at(-1)?.transcript_seq || after);
  }
  return { segments };
}

async function timedFetchJson(url) {
  const startedAt = performance.now();
  const payload = await fetchJson(url);
  return { payload, elapsed_ms: performance.now() - startedAt };
}

async function resolveBackendPid(url) {
  const configured = Number(process.env.MEETING_COPILOT_BACKEND_PID || 0);
  if (configured > 0) return configured;
  const port = Number(new URL(url).port);
  if (!port) return null;
  try {
    const { stdout } = await execFileAsync(
      "lsof",
      ["-t", `-iTCP:${port}`, "-sTCP:LISTEN"],
      { timeout: 5_000 },
    );
    const pid = Number(String(stdout).trim().split(/\s+/)[0]);
    return pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

async function sampleRssKb(pid) {
  if (!pid) return null;
  try {
    const { stdout } = await execFileAsync(
      "ps",
      ["-o", "rss=", "-p", String(pid)],
      { timeout: 5_000 },
    );
    const value = Number(String(stdout).trim());
    return Number.isFinite(value) ? value : null;
  } catch {
    return null;
  }
}

async function probeAudioDurationSeconds(filePath, audioBytes) {
  const wavDuration = parseWavDurationSeconds(audioBytes);
  if (wavDuration !== null) return roundSeconds(wavDuration);
  try {
    const { stdout } = await execFileAsync("afinfo", [filePath], { timeout: 10_000 });
    const match = String(stdout).match(/estimated duration:\s*([0-9]+(?:\.[0-9]+)?)\s*sec/i);
    const duration = match ? Number(match[1]) : NaN;
    return Number.isFinite(duration) && duration > 0 ? roundSeconds(duration) : null;
  } catch {
    return null;
  }
}

function parseWavDurationSeconds(audioBytes) {
  if (
    audioBytes.length < 12
    || audioBytes.toString("ascii", 0, 4) !== "RIFF"
    || audioBytes.toString("ascii", 8, 12) !== "WAVE"
  ) return null;

  let byteRate = null;
  let dataBytes = 0;
  for (let offset = 12; offset + 8 <= audioBytes.length;) {
    const chunkId = audioBytes.toString("ascii", offset, offset + 4);
    const chunkSize = audioBytes.readUInt32LE(offset + 4);
    const payloadOffset = offset + 8;
    const payloadEnd = payloadOffset + chunkSize;
    if (payloadEnd > audioBytes.length) return null;
    if (chunkId === "fmt " && chunkSize >= 12) byteRate = audioBytes.readUInt32LE(payloadOffset + 8);
    if (chunkId === "data") dataBytes += chunkSize;
    offset = payloadEnd + (chunkSize % 2);
  }
  return Number.isFinite(byteRate) && byteRate > 0 && dataBytes > 0
    ? dataBytes / byteRate
    : null;
}

function buildPlaylistManifest({ entries, sourceAudioDurationSeconds, sourceAudioSha256 }) {
  const totalPlayedSeconds = entries.reduce(
    (total, entry) => total + (Number.isFinite(entry.played_seconds) ? entry.played_seconds : 0),
    0,
  );
  return {
    schema_version: "workbench-v2-soak-playlist.v1",
    strategy: "repeat_single_source_until_deadline",
    source_count: sourceAudioSha256 ? 1 : 0,
    sources: sourceAudioSha256 ? [{
      source_audio_sha256: sourceAudioSha256,
      source_audio_duration_seconds: sourceAudioDurationSeconds,
    }] : [],
    playback_count: entries.length,
    total_played_seconds: roundSeconds(totalPlayedSeconds),
    entries: entries.map((entry) => ({ ...entry })),
  };
}

function calculateRepetitionRatio(entries, uniqueSourceDurationSeconds) {
  const playedSeconds = Array.isArray(entries)
    ? entries.map((entry) => entry?.played_seconds).filter((value) => Number.isFinite(value) && value > 0)
    : [];
  const totalPlayedSeconds = playedSeconds.reduce((total, value) => total + value, 0);
  if (
    !Number.isFinite(totalPlayedSeconds)
    || totalPlayedSeconds <= 0
    || !Number.isFinite(uniqueSourceDurationSeconds)
    || uniqueSourceDurationSeconds <= 0
  ) return null;
  const uniquePlayedSeconds = Math.min(Math.max(...playedSeconds), uniqueSourceDurationSeconds);
  return roundSeconds(Math.max(0, totalPlayedSeconds - uniquePlayedSeconds) / totalPlayedSeconds);
}

function evaluateSemanticAcceptance(payload) {
  const targetDurationSeconds = Number.isFinite(payload?.target_duration_seconds)
    && payload.target_duration_seconds > 0
    ? payload.target_duration_seconds
    : semanticMinUniqueAudioSeconds;
  const requiredUniqueAudioSeconds = Math.min(targetDurationSeconds, semanticMinUniqueAudioSeconds);
  const sourceDurationSeconds = Number.isFinite(payload?.source_audio_duration_seconds)
    && payload.source_audio_duration_seconds > 0
    ? payload.source_audio_duration_seconds
    : null;
  const sourceAudioSha256 = typeof payload?.source_audio_sha256 === "string"
    ? payload.source_audio_sha256
    : "";
  const playbackCount = Number.isInteger(payload?.playback_count) && payload.playback_count > 0
    ? payload.playback_count
    : 0;
  const playlistManifest = payload?.playlist_manifest;
  const playlistEntries = Array.isArray(playlistManifest?.entries) ? playlistManifest.entries : [];
  const playlistSources = Array.isArray(playlistManifest?.sources) ? playlistManifest.sources : [];
  const manifestTotalPlayedSeconds = playlistManifest?.total_played_seconds;
  const entryTotalPlayedSeconds = playlistEntries.reduce(
    (total, entry) => total + (Number.isFinite(entry?.played_seconds) ? entry.played_seconds : 0),
    0,
  );
  const playlistManifestValid = playlistManifest?.schema_version === "workbench-v2-soak-playlist.v1"
    && playlistManifest?.strategy === "repeat_single_source_until_deadline"
    && playlistManifest?.source_count === 1
    && playlistSources.length === 1
    && playlistSources[0]?.source_audio_sha256 === sourceAudioSha256
    && playlistSources[0]?.source_audio_duration_seconds === sourceDurationSeconds
    && playlistManifest?.playback_count === playbackCount
    && playlistEntries.length === playbackCount
    && Number.isFinite(manifestTotalPlayedSeconds)
    && manifestTotalPlayedSeconds > 0
    && Math.abs(entryTotalPlayedSeconds - manifestTotalPlayedSeconds)
      <= Math.max(0.1, manifestTotalPlayedSeconds * 0.01)
    && playlistEntries.every((entry, index) => (
      entry?.playback_index === index + 1
      && entry?.source_audio_sha256 === sourceAudioSha256
      && Number.isFinite(entry?.played_seconds)
      && entry.played_seconds > 0
    ));
  const repetitionRatio = Number.isFinite(payload?.repetition_ratio)
    && payload.repetition_ratio >= 0
    && payload.repetition_ratio <= 1
    ? payload.repetition_ratio
    : null;
  const calculatedRepetitionRatio = calculateRepetitionRatio(playlistEntries, sourceDurationSeconds);

  const blockers = [];
  if (sourceDurationSeconds === null) blockers.push("source_audio_duration_unknown");
  if (!/^[a-f0-9]{64}$/i.test(sourceAudioSha256)) blockers.push("source_audio_sha256_missing");
  if (!playlistManifestValid) blockers.push("playback_manifest_invalid");
  if (repetitionRatio === null) blockers.push("repetition_ratio_unknown");
  if (
    repetitionRatio !== null
    && calculatedRepetitionRatio !== null
    && Math.abs(repetitionRatio - calculatedRepetitionRatio) > 0.001
  ) blockers.push("repetition_ratio_mismatch");
  if (sourceDurationSeconds !== null && sourceDurationSeconds < requiredUniqueAudioSeconds) {
    blockers.push("source_audio_too_short");
  }
  if (repetitionRatio !== null && repetitionRatio > semanticMaxRepetitionRatio) {
    blockers.push("repetition_ratio_exceeded");
  }
  return {
    semantic_acceptance_eligible: blockers.length === 0,
    semantic_acceptance_blockers: blockers,
    required_unique_audio_seconds: roundSeconds(requiredUniqueAudioSeconds),
    max_repetition_ratio: semanticMaxRepetitionRatio,
  };
}

function roundSeconds(value) {
  return Number(Number(value).toFixed(6));
}

async function writeJsonToStdout(payload) {
  await new Promise((resolve, reject) => {
    process.stdout.write(`${JSON.stringify(payload)}\n`, (error) => {
      if (error) reject(error);
      else resolve();
    });
  });
}

function median(values) {
  if (!values.length) return null;
  const ordered = [...values].sort((a, b) => a - b);
  const middle = Math.floor(ordered.length / 2);
  return ordered.length % 2 ? ordered[middle] : (ordered[middle - 1] + ordered[middle]) / 2;
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
    const entry = {
      request_id: params.requestId,
      resource_type: params.type || null,
      url,
      error: params.errorText,
      canceled: params.canceled === true,
    };
    const expectedCancellation = params.errorText === "net::ERR_ABORTED"
      && (
        params.type === "EventSource"
        || url.includes("/v2/meetings/") && url.includes("/events")
        || url.includes("/audio/content")
      );
    if (expectedCancellation) {
      diagnostics.allowlisted_network_cancellations.push(entry);
      return;
    }
    diagnostics.network_failures.push(entry);
  });
  page.on("Network.responseReceived", (params) => {
    if (Number(params.response?.status || 0) >= 500) diagnostics.http_5xx.push({ url: params.response?.url, status: params.response?.status });
  });
  page.on("Page.javascriptDialogOpening", (params) => {
    const expectedEndConfirmation = params.type === "confirm"
      && String(params.message || "").includes("结束会议");
    if (!expectedEndConfirmation) diagnostics.runtime_exceptions.push(`unexpected_dialog:${params.type || "unknown"}`);
    void page.send("Page.handleJavaScriptDialog", { accept: expectedEndConfirmation }).catch((error) => {
      diagnostics.runtime_exceptions.push(`dialog_handle_failed:${error.message}`);
    });
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
  const result = await withOperationTimeout(
    page.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }),
    cdpOperationTimeoutMs,
    "Runtime.evaluate",
  );
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text || "browser evaluation failed");
  return result.result.value;
}

async function waitFor(page, expression, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await withOperationTimeout(
      page.send("Runtime.evaluate", { expression: `Boolean(${expression})`, returnByValue: true }),
      cdpOperationTimeoutMs,
      "Runtime.waitFor",
    );
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
  const result = await withOperationTimeout(
    page.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false, fromSurface: true }),
    cdpOperationTimeoutMs,
    "Page.captureScreenshot",
  );
  await writeFile(path.join(artifactRoot, fileName), Buffer.from(result.data, "base64"));
}

async function fetchJson(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
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

function positiveNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : fallback;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function withOperationTimeout(task, timeoutMs, operation) {
  let timer;
  try {
    return await Promise.race([
      task,
      new Promise((_, reject) => {
        timer = setTimeout(
          () => reject(new Error(`cdp_operation_timeout:${operation}`)),
          timeoutMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function phase(name) {
  process.stdout.write(JSON.stringify({ phase: name, at_ms: Date.now() }) + "\n");
}

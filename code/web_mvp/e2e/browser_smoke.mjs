import { mkdir, mkdtemp, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-web-e2e-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "meeting-copilot-chrome-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8767");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9223");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const processes = [];
const cdpSockets = [];

try {
  await ensureMainlineArtifactTrialFixture();

  const server = spawn(
    "uvicorn",
    ["meeting_copilot_web_mvp.app:app", "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        MEETING_COPILOT_DATA_DIR: dataDir,
        PYTHONPATH: ".:../../core",
      },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  processes.push(server);
  collectLogs(server, "uvicorn");
  await waitForHttp(`http://127.0.0.1:${port}/health`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/shell-readiness`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/runtime-boundary`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/native-bridge-contract`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/asr-worker-handoff-dry-run-readiness`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/mic-adapter-contract-readiness`);
  await waitForHttp(`http://127.0.0.1:${port}/desktop/local-shadow-preview-release-readiness`);

  const chrome = spawn(
    chromePath,
    [
      "--headless=new",
      "--disable-gpu",
      "--disable-background-networking",
      "--no-first-run",
      "--no-default-browser-check",
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${chromeUserDataDir}`,
      "about:blank",
    ],
    { stdio: ["ignore", "pipe", "pipe"] },
  );
  processes.push(chrome);
  collectLogs(chrome, "Google Chrome");
  await waitForHttp(`http://127.0.0.1:${chromePort}/json/version`);

  const page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/`);
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-readiness-panel')?.textContent?.includes('blocked_before_desktop_shell')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-runtime-boundary-panel')?.textContent?.includes('blocked_before_runtime_creation')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-native-bridge-contract-panel')?.textContent?.includes('specified_not_bound')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-native-runtime-panel')?.textContent?.includes('not_available')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-asr-handoff-dry-run-panel')?.textContent?.includes('preview_only_ready')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('desktop-mic-adapter-contract-panel')?.textContent?.includes('specified_not_executable')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('local-shadow-preview-release-title')?.textContent?.includes('Local Shadow Preview')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('local-shadow-preview-release-content')?.textContent?.includes('not_exited')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('local-shadow-preview-release-content')?.textContent?.includes('blocked_not_ready_for_user_real_mic_shadow_test')",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('#fixture-select option').length >= 1");
  const passiveDataDirEntries = await readdir(dataDir);
  assert(
    passiveDataDirEntries.length === 0,
    `expected passive workbench load not to write local data, got ${passiveDataDirEntries.join(", ")}`,
  );
  await selectFixture(page, "api-review");
  await waitForCdpExpression(page, "document.querySelectorAll('.suggestion-card').length >= 2");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_scheduled').length >= 1");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('Meeting workbench_api-review_')");

  const apiReview = await evaluate(page, () => {
    const chip = document.querySelector(
      '.suggestion-card .chip[data-evidence-id="ev_002"][data-segment-id="seg_002"]',
    );
    if (!chip) {
      throw new Error("missing json_smoke_api_review evidence click-back chip ev_002 -> seg_002");
    }
    chip.click();
    return {
      sessionTitle: document.getElementById("summary-title")?.textContent || "",
      activeEvidenceId: document.querySelector(".evidence-item.active")?.id || "",
      activeSegmentId: document.querySelector(".segment-item.active")?.id || "",
      eventKinds: Array.from(document.querySelectorAll(".event-item")).map((item) =>
        item.className,
      ),
      reportText: document.getElementById("report-panel")?.textContent || "",
      desktopReadinessText: document.getElementById("desktop-readiness-panel")?.textContent || "",
      desktopReadinessPhaseCount: document.querySelectorAll(".desktop-phase").length,
      desktopRuntimeText: document.getElementById("desktop-runtime-boundary-panel")?.textContent || "",
      desktopRuntimePhaseCount: document.querySelectorAll(".desktop-runtime-phase").length,
      desktopBridgeText: document.getElementById("desktop-native-bridge-contract-panel")?.textContent || "",
      desktopBridgeCommandCount: document.querySelectorAll(".desktop-bridge-command").length,
      desktopNativeRuntimeText: document.getElementById("desktop-native-runtime-panel")?.textContent || "",
      desktopAsrHandoffText: document.getElementById("desktop-asr-handoff-dry-run-panel")?.textContent || "",
      desktopAsrHandoffPhaseCount: document.querySelectorAll(".desktop-asr-handoff-phase").length,
      desktopMicAdapterText: document.getElementById("desktop-mic-adapter-contract-panel")?.textContent || "",
      desktopMicAdapterCommandCount: document.querySelectorAll(".desktop-mic-adapter-command").length,
      desktopMicAdapterInvokeCommandCount: document.querySelectorAll(".desktop-mic-adapter-invoke-command").length,
      desktopTauriNoopResultCommandCount: document.querySelectorAll(".desktop-tauri-noop-result-command").length,
      localShadowPreviewReleaseText: document.getElementById("local-shadow-preview-release-panel")?.textContent || "",
      localShadowPreviewReleaseMetrics: Array.from(
        document.querySelectorAll("#local-shadow-preview-release-content .release-summary-item"),
      ).map((item) => ({
        label: item.querySelector(".label")?.textContent || "",
        value: item.querySelector("strong")?.textContent || "",
      })),
    };
  });
  assert(
    apiReview.activeEvidenceId === "evidence-ev_002",
    `expected evidence-ev_002, got ${apiReview.activeEvidenceId}`,
  );
  assert(
    apiReview.activeSegmentId === "transcript-segment-seg_002",
    `expected transcript-segment-seg_002, got ${apiReview.activeSegmentId}`,
  );
  assert(
    apiReview.eventKinds.some((className) => className.includes("llm_scheduled")),
    "expected replay timeline to include llm_scheduled",
  );
  assert(apiReview.reportText.includes("Meeting workbench_api-review_"), "expected report panel");
  assert(apiReview.desktopReadinessText.includes("blocked_before_desktop_shell"), "expected desktop readiness blocked status");
  assert(apiReview.desktopReadinessText.includes("8 phases"), "expected desktop readiness phase count");
  assert(apiReview.desktopReadinessText.includes("not_connected"), "expected desktop readiness no audio capture status");
  assert(apiReview.desktopReadinessText.includes("not_requested"), "expected desktop readiness permissions not requested");
  assert(apiReview.desktopReadinessText.includes("not_started"), "expected desktop readiness shell/worker not started");
  assert(apiReview.desktopReadinessText.includes("desktop_safe_to_capture_audio=false"), "expected desktop readiness safe-to-capture flag false");
  assert(apiReview.desktopReadinessPhaseCount === 8, `expected 8 desktop readiness phases, got ${apiReview.desktopReadinessPhaseCount}`);
  assert(apiReview.desktopRuntimeText.includes("blocked_before_runtime_creation"), "expected desktop runtime blocked status");
  assert(apiReview.desktopRuntimeText.includes("tauri_first_electron_fallback"), "expected desktop runtime recommendation");
  assert(apiReview.desktopRuntimeText.includes("8 phases"), "expected desktop runtime phase count");
  assert(apiReview.desktopRuntimeText.includes("sidecar_worker_planned"), "expected desktop runtime worker model");
  assert(apiReview.desktopRuntimeText.includes("desktop_runtime_safe_to_create_shell=false"), "expected runtime safe-to-create-shell flag false");
  assert(apiReview.desktopRuntimePhaseCount === 8, `expected 8 desktop runtime phases, got ${apiReview.desktopRuntimePhaseCount}`);
  assert(apiReview.desktopBridgeText.includes("specified_not_bound"), "expected native bridge contract status");
  assert(apiReview.desktopBridgeText.includes("runtime.get_status"), "expected runtime status bridge command");
  assert(apiReview.desktopBridgeText.includes("audio.capture_start"), "expected audio capture bridge command");
  assert(apiReview.desktopBridgeText.includes("asr_worker.start"), "expected ASR worker bridge command");
  assert(apiReview.desktopBridgeText.includes("desktop_bridge_safe_to_create_native_bridge=false"), "expected bridge safe-to-create flag false");
  assert(apiReview.desktopBridgeCommandCount === 8, `expected 8 desktop bridge commands, got ${apiReview.desktopBridgeCommandCount}`);
  assert(apiReview.desktopNativeRuntimeText.includes("browser_fallback"), "expected native runtime browser fallback");
  assert(apiReview.desktopNativeRuntimeText.includes("not_available"), "expected native runtime not available outside Tauri");
  assert(apiReview.desktopNativeRuntimeText.includes("safe_to_capture_audio=false"), "expected native runtime no audio capture flag");
  assert(apiReview.desktopAsrHandoffText.includes("preview_only_ready"), "expected ASR handoff dry-run preview ready status");
  assert(apiReview.desktopAsrHandoffText.includes("preview_ready_no_web_mutation"), "expected PCWEB-096 default dry-run status");
  assert(apiReview.desktopAsrHandoffText.includes("explicit_mode_only"), "expected synthetic local test explicit-only status");
  assert(apiReview.desktopAsrHandoffText.includes("desktop_asr_handoff_safe_to_start_worker=false"), "expected ASR handoff no worker flag");
  assert(apiReview.desktopAsrHandoffText.includes("desktop_asr_handoff_safe_to_capture_audio=false"), "expected ASR handoff no audio flag");
  assert(apiReview.desktopAsrHandoffText.includes("desktop_asr_handoff_safe_to_call_remote_asr=false"), "expected ASR handoff no remote ASR flag");
  assert(apiReview.desktopAsrHandoffPhaseCount === 7, `expected 7 desktop ASR handoff phases, got ${apiReview.desktopAsrHandoffPhaseCount}`);
  assert(apiReview.desktopMicAdapterText.includes("ready_noop_contract_visible"), "expected mic adapter readiness UI status");
  assert(apiReview.desktopMicAdapterText.includes("specified_not_executable"), "expected mic adapter contract status");
  assert(apiReview.desktopMicAdapterText.includes("mic_adapter.start"), "expected mic adapter start command");
  assert(apiReview.desktopMicAdapterText.includes("safe_to_request_audio_permission_now=false"), "expected mic adapter no permission flag");
  assert(apiReview.desktopMicAdapterText.includes("safe_to_capture_audio_now=false"), "expected mic adapter no capture flag");
  assert(apiReview.desktopMicAdapterText.includes("safe_to_run_tauri_or_cargo_now=false"), "expected mic adapter no Cargo/Tauri flag");
  assert(apiReview.desktopMicAdapterText.includes("mic_adapter_browser_fallback"), "expected mic adapter browser fallback invocation status");
  assert(apiReview.desktopMicAdapterText.includes("not_invoked"), "expected mic adapter no-op commands not invoked outside Tauri");
  assert(apiReview.desktopMicAdapterText.includes("collector_browser_fallback"), "expected Tauri no-op result collector browser fallback");
  assert(apiReview.desktopMicAdapterText.includes("validation_browser_fallback"), "expected Tauri no-op result validation browser fallback");
  assert(apiReview.desktopMicAdapterText.includes("desktop_tauri_noop_run_result.v1"), "expected no-op result schema version");
  assert(apiReview.desktopMicAdapterText.includes("real_tauri_noop_result_ready=false"), "expected no real Tauri result outside Tauri");
  assert(apiReview.desktopMicAdapterText.includes("pcweb_117_validation_status=not_submitted"), "expected no validation submit outside Tauri");
  assert(apiReview.desktopMicAdapterText.includes("safe_to_call_remote_asr_now=false"), "expected mic adapter invocation no remote ASR flag");
  assert(apiReview.desktopMicAdapterText.includes("safe_to_call_llm_now=false"), "expected mic adapter invocation no LLM flag");
  for (const flag of [
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
  ]) {
    assert(apiReview.desktopMicAdapterText.includes(`${flag}=false`), `expected mic adapter ${flag}=false`);
  }
  assert(apiReview.desktopMicAdapterCommandCount === 7, `expected 7 desktop mic adapter commands, got ${apiReview.desktopMicAdapterCommandCount}`);
  assert(apiReview.desktopMicAdapterInvokeCommandCount === 7, `expected 7 desktop mic adapter invocation rows, got ${apiReview.desktopMicAdapterInvokeCommandCount}`);
  assert(apiReview.desktopTauriNoopResultCommandCount === 10, `expected 10 desktop Tauri no-op result rows, got ${apiReview.desktopTauriNoopResultCommandCount}`);
  assert(apiReview.localShadowPreviewReleaseText.includes("Local Shadow Preview"), "expected local shadow preview release panel title");
  assert(apiReview.localShadowPreviewReleaseText.includes("Preview"), "expected preview release metric");
  assert(apiReview.localShadowPreviewReleaseText.includes("Ready"), "expected preview release ready status");
  assert(apiReview.localShadowPreviewReleaseText.includes("Shadow Pilot"), "expected shadow pilot release metric");
  assert(apiReview.localShadowPreviewReleaseText.includes("Production MVP"), "expected production MVP release metric");
  assert(apiReview.localShadowPreviewReleaseText.includes("not_exited"), "expected ASR quality exit not_exited");
  assert(apiReview.localShadowPreviewReleaseText.includes("blocked_not_ready_for_user_real_mic_shadow_test"), "expected real mic release blocker");
  assert(apiReview.localShadowPreviewReleaseText.includes("disabled_not_called"), "expected disabled LLM status");
  assert(apiReview.localShadowPreviewReleaseText.includes("not_created_in_current_mainline_preview"), "expected formal card not-created status");
  assert(apiReview.localShadowPreviewReleaseText.includes("preview_only_not_real_meeting_go_evidence"), "expected formal report not-Go status");
  assert(apiReview.localShadowPreviewReleaseText.includes("safe_to_capture_microphone_now=false"), "expected no microphone capture safety flag");
  assert(apiReview.localShadowPreviewReleaseText.includes("safe_to_capture_system_audio_now=false"), "expected no system audio capture safety flag");
  assert(apiReview.localShadowPreviewReleaseText.includes("safe_to_call_remote_asr_now=false"), "expected no remote ASR safety flag");
  assert(apiReview.localShadowPreviewReleaseText.includes("safe_to_call_llm_now=false"), "expected no LLM safety flag");
  assert(apiReview.localShadowPreviewReleaseText.includes("safe_to_read_configs_local_now=false"), "expected no configs/local safety flag");
  const releaseMetricByLabel = Object.fromEntries(
    apiReview.localShadowPreviewReleaseMetrics.map((item) => [item.label, item.value]),
  );
  assert(releaseMetricByLabel.Preview === "Ready", `expected Preview Ready, got ${releaseMetricByLabel.Preview}`);
  assert(releaseMetricByLabel["Shadow Pilot"] === "Blocked", `expected Shadow Pilot Blocked, got ${releaseMetricByLabel["Shadow Pilot"]}`);
  assert(releaseMetricByLabel["Production MVP"] === "Blocked", `expected Production MVP Blocked, got ${releaseMetricByLabel["Production MVP"]}`);
  assert(releaseMetricByLabel.Report === "preview_only_not_real_meeting_go_evidence", `expected Report not-Go, got ${releaseMetricByLabel.Report}`);

  await evaluate(page, () => {
    const NativeEventSource = window.EventSource;
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    window.__meetingCopilotLiveDomAtEventSourceOpen = null;
    window.__meetingCopilotReportBeforeLiveEvents = [];
    window.EventSource = class extends NativeEventSource {
      constructor(url, options) {
        window.__meetingCopilotEventSourceUrls.push(String(url));
        window.__meetingCopilotLiveDomAtEventSourceOpen = {
          suggestionCards: document.querySelectorAll(".suggestion-card").length,
          stateItems: document.querySelectorAll(".state-item").length,
          transcriptSegments: document.querySelectorAll(".segment-item").length,
          evidenceItems: document.querySelectorAll(".evidence-item").length,
          eventText: document.getElementById("event-stream-panel")?.textContent || "",
          reportText: document.getElementById("report-panel")?.textContent || "",
        };
        super(url, options);
      }
      addEventListener(type, listener, options) {
        const wrappedListener = (event) => {
        window.__meetingCopilotReportBeforeLiveEvents.push({
          type,
          reportText: document.getElementById("report-panel")?.textContent || "",
        });
          if (type === "suggestion_card") {
            const eventEnvelope = JSON.parse(event.data);
            eventEnvelope.payload.card = {
              ...eventEnvelope.payload.card,
              title: `Payload-only ${eventEnvelope.payload.card.title}`,
            };
            Object.defineProperty(event, "data", {
              configurable: true,
              value: JSON.stringify(eventEnvelope),
            });
          }
          if (type === "transcript_final") {
            const eventEnvelope = JSON.parse(event.data);
            eventEnvelope.payload.evidence_spans = (eventEnvelope.payload.evidence_spans || []).map((evidence) => ({
              ...evidence,
              quote: `Payload-only ${evidence.quote}`,
            }));
            Object.defineProperty(event, "data", {
              configurable: true,
              value: JSON.stringify(eventEnvelope),
            });
          }
          if (type === "transcript_revision") {
            const eventEnvelope = JSON.parse(event.data);
            eventEnvelope.payload.evidence_spans = (eventEnvelope.payload.evidence_spans || []).map((evidence) => ({
              ...evidence,
              quote: `Payload-only ${evidence.quote}`,
            }));
            eventEnvelope.payload.superseded_evidence_spans = (eventEnvelope.payload.superseded_evidence_spans || []).map((evidence) => ({
              ...evidence,
              quote: `Payload-only ${evidence.quote}`,
            }));
            Object.defineProperty(event, "data", {
              configurable: true,
              value: JSON.stringify(eventEnvelope),
            });
          }
          if (type === "suggestion_invalidated") {
            const eventEnvelope = JSON.parse(event.data);
            eventEnvelope.payload.card = {
              ...eventEnvelope.payload.card,
              invalidation_reason: "payload-only-stale-evidence",
            };
            Object.defineProperty(event, "data", {
              configurable: true,
              value: JSON.stringify(eventEnvelope),
            });
          }
          listener.call(this, event);
        };
        return super.addEventListener(type, wrappedListener, options);
      }
    };
  });
  const liveMock = await evaluate(page, () => {
    const button = document.getElementById("event-mode-live-mock");
    if (!button) {
      throw new Error("missing Live Mock event mode button");
    }
    button.click();
    return true;
  });
  assert(liveMock === true, "expected live mock button click to run");
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/sessions/') && url.endsWith('/events.sse'))",
  );
  const liveDomAtOpen = await evaluate(page, () => window.__meetingCopilotLiveDomAtEventSourceOpen);
  assert(liveDomAtOpen.suggestionCards === 0, `expected no preloaded live cards, got ${liveDomAtOpen.suggestionCards}`);
  assert(liveDomAtOpen.stateItems === 0, `expected no preloaded live state items, got ${liveDomAtOpen.stateItems}`);
  assert(liveDomAtOpen.transcriptSegments === 0, `expected no preloaded live transcript segments, got ${liveDomAtOpen.transcriptSegments}`);
  assert(liveDomAtOpen.evidenceItems === 0, `expected no preloaded live evidence items, got ${liveDomAtOpen.evidenceItems}`);
  assert(!liveDomAtOpen.reportText.includes("Meeting workbench_api-review_"), "expected no previous replay report at live stream open");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_partial').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.scheduler_event').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.segment-item').length >= 3");
  await waitForCdpExpression(page, "document.querySelectorAll('.state-item').length >= 5");
  await waitForCdpExpression(page, "document.querySelectorAll('.suggestion-card').length >= 2");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.suggestion_card').length >= 2");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_revision').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.suggestion_invalidated').length >= 1");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  const liveReportBeforeEvents = await evaluate(page, () => window.__meetingCopilotReportBeforeLiveEvents || []);
  const beforeEvaluationSummary = liveReportBeforeEvents.find((item) => item.type === "evaluation_summary");
  assert(Boolean(beforeEvaluationSummary), "expected evaluation_summary report timing sample");
  assert(
    !beforeEvaluationSummary.reportText.includes("Meeting live_api-review_"),
    "expected no full live report before evaluation_summary handler",
  );
  const liveReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-mock")?.getAttribute("aria-pressed") || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      cardText: document.getElementById("suggestion-list")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      evidenceText: document.getElementById("evidence-panel")?.textContent || "",
      sessionTitle: document.getElementById("summary-title")?.textContent || "",
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(liveReview.livePressed === "true", "expected Live Mock mode to be selected");
  assert(liveReview.eventText.includes("transcript_partial"), "expected live partial event");
  assert(liveReview.eventText.includes("scheduler_event"), "expected live scheduler event");
  assert(liveReview.eventText.includes("live_mock_stream"), "expected live source marker");
  assert(liveReview.stateText.includes("上线后应该看哪些监控指标"), "expected live state item");
  assert(liveReview.cardText.includes("Payload-only 补齐接口变更监控指标"), "expected live suggestion card from event payload");
  assert(liveReview.transcriptText.includes("trace_id"), "expected live transcript final text");
  assert(liveReview.evidenceText.includes("Payload-only"), "expected live evidence from transcript event payload");
  assert(liveReview.evidenceText.includes("v2"), "expected live revision evidence from mock SSE revision payload");
  assert(liveReview.sessionTitle.includes("live_api-review_"), `expected live session title, got ${liveReview.sessionTitle}`);
  assert(liveReview.reportText.includes("Meeting live_api-review_"), "expected live mock report panel");
  const liveRevisionReview = await evaluate(page, () => {
    const compatibilityCard = Array.from(document.querySelectorAll(".suggestion-card"))
      .find((item) => item.textContent?.includes("确认兼容性测试覆盖旧版调用方"));
    return {
      revisionEventText: document.getElementById("event-stream-panel")?.textContent || "",
      invalidatedEventText: Array.from(document.querySelectorAll(".event-item.suggestion_invalidated"))
        .map((item) => item.textContent || "")
        .join("\n"),
      oldEvidenceClass: document.getElementById("evidence-ev_002")?.className || "",
      oldEvidenceText: document.getElementById("evidence-ev_002")?.textContent || "",
      newEvidenceText: document.getElementById("evidence-ev_002_rev1")?.textContent || "",
      newEvidenceReplacedBy: document.getElementById("evidence-ev_002_rev1")?.textContent?.includes("replaced_by") || false,
      staleCardClass: compatibilityCard?.className || "",
      staleCardButtonCount: compatibilityCard?.querySelectorAll(".card-actions button").length ?? -1,
      staleCardText: compatibilityCard?.textContent || "",
      staleCardInvalidationReason: compatibilityCard?.textContent?.includes("payload-only-stale-evidence") || false,
    };
  });
  assert(liveRevisionReview.revisionEventText.includes("transcript_revision"), "expected mock SSE transcript_revision event in live timeline");
  assert(liveRevisionReview.invalidatedEventText.includes("card_002"), "expected invalidated event to cite impacted card");
  assert(liveRevisionReview.invalidatedEventText.includes("stale_evidence"), "expected invalidated event to explain stale evidence");
  assert(
    liveRevisionReview.oldEvidenceClass.includes("stale"),
    `expected superseded evidence to use stale class, got ${liveRevisionReview.oldEvidenceClass}`,
  );
  assert(liveRevisionReview.oldEvidenceText.includes("superseded"), "expected old evidence to show superseded status");
  assert(liveRevisionReview.oldEvidenceText.includes("ev_002_rev1"), "expected old evidence to show replacement evidence id");
  assert(liveRevisionReview.newEvidenceText.includes("v2"), "expected new revision evidence to render v2 quote");
  assert(liveRevisionReview.newEvidenceText.includes("revision_of ev_002"), "expected new evidence to point back with revision_of");
  assert(liveRevisionReview.newEvidenceReplacedBy === false, "expected new active evidence not to carry replaced_by");
  assert(
    liveRevisionReview.staleCardClass.includes("muted"),
    `expected card using superseded evidence to be muted, got ${liveRevisionReview.staleCardClass}`,
  );
  assert(liveRevisionReview.staleCardButtonCount === 0, "expected card using superseded evidence to have no feedback buttons");
  assert(liveRevisionReview.staleCardText.includes("evidence: stale"), "expected stale evidence reason on muted card");
  assert(
    liveRevisionReview.staleCardInvalidationReason === true,
    "expected invalidated card UI to use suggestion_invalidated payload card",
  );
  await evaluate(page, () => {
    const firstLiveCardButton = document.querySelector(".suggestion-card .card-actions button");
    if (!firstLiveCardButton) {
      throw new Error("missing live suggestion card feedback button");
    }
    firstLiveCardButton.click();
  });
  await waitForCdpExpression(
    page,
    "Array.from(document.querySelectorAll('.suggestion-card .chip')).some((chip) => chip.textContent?.includes('kept'))",
  );
  const liveFeedbackReview = await evaluate(page, () => {
    return {
      cardCount: document.querySelectorAll(".suggestion-card").length,
      stateCount: document.querySelectorAll(".state-item").length,
      transcriptCount: document.querySelectorAll(".segment-item").length,
      revisionSegmentPresent: Boolean(document.getElementById("transcript-segment-seg_002_rev1")),
      replayJsonEvents: document.querySelectorAll(".event-item.llm_scheduled").length,
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(liveFeedbackReview.cardCount === 2, `expected live feedback to keep 2 visible cards, got ${liveFeedbackReview.cardCount}`);
  assert(liveFeedbackReview.stateCount === 5, `expected live feedback to keep 5 visible state items, got ${liveFeedbackReview.stateCount}`);
  assert(liveFeedbackReview.transcriptCount === 4, `expected live feedback to keep 3 fixture segments plus revision, got ${liveFeedbackReview.transcriptCount}`);
  assert(liveFeedbackReview.revisionSegmentPresent === true, "expected live feedback to keep revision segment visible");
  assert(liveFeedbackReview.replayJsonEvents === 0, "expected live feedback not to replace timeline with replay JSON events");
  assert(liveFeedbackReview.reportText.includes("Meeting live_api-review_"), "expected report to remain available after summary");

  await evaluate(page, () => {
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    window.__meetingCopilotFetchUrls = [];
    window.__meetingCopilotReadinessBodies = [];
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.fetch = (...args) => {
      const url = String(args[0]);
      window.__meetingCopilotFetchUrls.push(url);
      if (url.includes("/llm-card-lifecycle-readiness-summaries")) {
        try {
          window.__meetingCopilotReadinessBodies.push(JSON.parse(args[1]?.body || "{}"));
        } catch {
          window.__meetingCopilotReadinessBodies.push({parse_error: true});
        }
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_partial').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.scheduler_event').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_revision').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.evidence-item').length >= 2");
  await waitForCdpExpression(page, "document.querySelectorAll('.state-item').length >= 1");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('card-lifecycle-readiness-panel')?.textContent?.includes('blocked_until_enabled')",
  );
  const liveAsrReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-asr")?.getAttribute("aria-pressed") || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      evidenceText: document.getElementById("evidence-panel")?.textContent || "",
      cardCount: document.querySelectorAll(".suggestion-card").length,
      stateCount: document.querySelectorAll(".state-item").length,
      suggestionCandidateEventCount: document.querySelectorAll(".event-item.suggestion_candidate_event").length,
      llmRequestDraftEventCount: document.querySelectorAll(".event-item.llm_request_draft_event").length,
      llmSchemaEventCount: document.querySelectorAll(".event-item.llm_schema_result").length,
      suggestionEventCount: document.querySelectorAll(".event-item.suggestion_card").length,
      silencedEventCount: document.querySelectorAll(".event-item.suggestion_silenced").length,
      reportText: document.getElementById("report-panel")?.textContent || "",
      lifecycleText: document.getElementById("card-lifecycle-readiness-panel")?.textContent || "",
      lifecyclePhaseCount: document.querySelectorAll(".lifecycle-phase").length,
      reportFetchCount: (window.__meetingCopilotFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
      draftFetchCount: (window.__meetingCopilotFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      readinessSummaryFetchCount: (window.__meetingCopilotFetchUrls || [])
        .filter((url) => url.includes("/llm-card-lifecycle-readiness-summaries")).length,
      readinessBodies: window.__meetingCopilotReadinessBodies || [],
    };
  });
  assert(liveAsrReview.livePressed === "true", "expected Live ASR mode to be selected");
  assert(liveAsrReview.eventText.includes("live_asr_stream"), "expected live ASR source marker");
  assert(liveAsrReview.eventText.includes("local ASR streaming contract skeleton"), "expected live ASR boundary description");
  assert(liveAsrReview.eventText.includes("local_mock_asr"), "expected live ASR provider summary");
  assert(liveAsrReview.eventText.includes("scheduler_event"), "expected live ASR scheduler placeholder");
  assert(liveAsrReview.eventText.includes("not-called"), "expected live ASR no-LLM scheduler marker");
  assert(liveAsrReview.eventText.includes("llm_candidate_queued"), "expected live ASR queued scheduler decision");
  assert(liveAsrReview.eventText.includes("llm_candidate_skipped"), "expected live ASR skipped scheduler decision");
  assert(liveAsrReview.eventText.includes("cooldown"), "expected live ASR cooldown scheduler reason");
  assert(liveAsrReview.eventText.includes("not_called"), "expected live ASR no-call scheduler status");
  assert(liveAsrReview.eventText.includes("suggestion_candidate_event"), "expected live ASR suggestion candidate audit event");
  assert(liveAsrReview.eventText.includes("llm_request_draft_event"), "expected live ASR LLM request draft audit event");
  assert(liveAsrReview.eventText.includes("draft_only"), "expected live ASR request draft status");
  assert(liveAsrReview.eventText.includes("not_generated"), "expected live ASR request draft schema status");
  assert(liveAsrReview.eventText.includes("asr_suggestion_candidate_asr_action_event_asr_seg_004"), "expected live ASR request draft candidate linkage");
  assert(liveAsrReview.eventText.includes("asr_action_event_asr_seg_004"), "expected live ASR request draft source event linkage");
  assert(liveAsrReview.eventText.includes("asr_ev_asr_seg_004"), "expected live ASR request draft evidence linkage");
  assert(liveAsrReview.eventText.includes("asr_seg_004"), "expected live ASR request draft segment linkage");
  assert(liveAsrReview.eventText.includes("high/0.9"), "expected live ASR candidate quality summary");
  assert(liveAsrReview.eventText.includes("degraded none"), "expected live ASR candidate degradation summary");
  assert(liveAsrReview.eventText.includes("risk.rollback.validation"), "expected live ASR risk candidate gap rule");
  assert(liveAsrReview.eventText.includes("action.owner.deadline.confirmation"), "expected live ASR action candidate gap rule");
  assert(liveAsrReview.eventText.includes("not_created"), "expected live ASR candidate not-created card status");
  assert(liveAsrReview.stateText.includes("先灰度 5%"), "expected live ASR state candidate from revision");
  assert(liveAsrReview.stateText.includes("谁负责回滚？"), "expected live ASR open question state candidate");
  assert(liveAsrReview.stateText.includes("如果错误率超过 0.1%"), "expected live ASR risk state candidate");
  assert(liveAsrReview.stateText.includes("张三下周三补充兼容性测试用例"), "expected live ASR action item state candidate");
  assert(liveAsrReview.transcriptText.includes("先灰度 5%"), "expected live ASR revision transcript text");
  assert(liveAsrReview.transcriptText.includes("谁负责回滚？"), "expected live ASR question transcript text");
  assert(liveAsrReview.transcriptText.includes("如果错误率超过 0.1%"), "expected live ASR risk transcript text");
  assert(liveAsrReview.transcriptText.includes("张三下周三补充兼容性测试用例"), "expected live ASR action transcript text");
  assert(liveAsrReview.evidenceText.includes("先灰度 5%"), "expected live ASR revision evidence");
  assert(liveAsrReview.evidenceText.includes("谁负责回滚？"), "expected live ASR question evidence");
  assert(liveAsrReview.evidenceText.includes("如果错误率超过 0.1%"), "expected live ASR risk evidence");
  assert(liveAsrReview.evidenceText.includes("张三下周三补充兼容性测试用例"), "expected live ASR action evidence");
  assert(liveAsrReview.evidenceText.includes("superseded"), "expected live ASR superseded evidence lifecycle");
  assert(liveAsrReview.cardCount === 0, `expected live ASR skeleton not to create suggestion cards, got ${liveAsrReview.cardCount}`);
  assert(liveAsrReview.stateCount >= 5, `expected live ASR skeleton to create decision, question, risk, and action state candidates, got ${liveAsrReview.stateCount}`);
  assert(liveAsrReview.suggestionCandidateEventCount >= 5, `expected live ASR suggestion candidate audit events, got ${liveAsrReview.suggestionCandidateEventCount}`);
  assert(liveAsrReview.llmRequestDraftEventCount >= 5, `expected live ASR LLM request draft audit events, got ${liveAsrReview.llmRequestDraftEventCount}`);
  assert(liveAsrReview.llmSchemaEventCount === 0, `expected no live ASR llm schema events, got ${liveAsrReview.llmSchemaEventCount}`);
  assert(liveAsrReview.suggestionEventCount === 0, `expected no live ASR suggestion events, got ${liveAsrReview.suggestionEventCount}`);
  assert(liveAsrReview.silencedEventCount === 0, `expected no live ASR silenced events, got ${liveAsrReview.silencedEventCount}`);
  assert(
    liveAsrReview.reportText.includes("Draft only; not a formal gated meeting report."),
    "expected live ASR draft warning in report panel",
  );
  assert(
    liveAsrReview.reportText.includes("## State Candidates"),
    "expected live ASR draft state candidates section in report panel",
  );
  assert(
    liveAsrReview.reportText.includes("## Suggestion Candidates"),
    "expected live ASR draft suggestion candidates section in report panel",
  );
  assert(
    liveAsrReview.reportText.includes("## LLM Request Drafts"),
    "expected live ASR draft LLM request draft section in report panel",
  );
  assert(
    liveAsrReview.reportText.includes("llm_suggestion_card_draft"),
    "expected live ASR draft request type",
  );
  assert(
    liveAsrReview.reportText.includes("not_generated"),
    "expected live ASR draft request schema status",
  );
  assert(
    liveAsrReview.reportText.includes("asr_suggestion_candidate_asr_action_event_asr_seg_004"),
    "expected live ASR draft request candidate linkage",
  );
  assert(
    liveAsrReview.reportText.includes("ActionItem asr_action_asr_seg_004 from asr_seg_004 using asr_ev_asr_seg_004"),
    "expected live ASR draft input summary linkage",
  );
  assert(
    liveAsrReview.reportText.includes("confidence high/0.9"),
    "expected live ASR draft candidate confidence metadata",
  );
  assert(
    liveAsrReview.reportText.includes("asr-candidate-policy.v1"),
    "expected live ASR draft candidate policy version",
  );
  assert(
    liveAsrReview.reportText.includes("action.owner.deadline.confirmation"),
    "expected live ASR draft action suggestion candidate",
  );
  assert(
    liveAsrReview.lifecycleText.includes("blocked_until_enabled"),
    "expected Live ASR card lifecycle readiness summary status",
  );
  assert(
    liveAsrReview.lifecycleText.includes("12 phases"),
    "expected Live ASR card lifecycle readiness phase count",
  );
  assert(
    liveAsrReview.lifecycleText.includes("not_called"),
    "expected Live ASR lifecycle readiness to keep LLM disabled",
  );
  assert(
    liveAsrReview.lifecycleText.includes("not_appended"),
    "expected Live ASR lifecycle readiness to keep event append disabled",
  );
  assert(
    liveAsrReview.lifecycleText.includes("not_written"),
    "expected Live ASR lifecycle readiness to keep idempotency writes disabled",
  );
  assert(
    liveAsrReview.lifecyclePhaseCount === 12,
    `expected 12 lifecycle phases, got ${liveAsrReview.lifecyclePhaseCount}`,
  );
  assert(
    liveAsrReview.readinessSummaryFetchCount === 1,
    `expected one lifecycle readiness summary POST, got ${liveAsrReview.readinessSummaryFetchCount}`,
  );
  assert(liveAsrReview.readinessBodies.length === 1, `expected one readiness POST body, got ${liveAsrReview.readinessBodies.length}`);
  assert(liveAsrReview.readinessBodies[0].mode === "summary_only", "expected readiness POST mode=summary_only");
  assert(liveAsrReview.readinessBodies[0].candidate_response?.type === "owner_gap", "expected readiness probe type owner_gap");
  assert(liveAsrReview.readinessBodies[0].candidate_response?.model === "not_called", "expected readiness probe model not_called");
  assert(liveAsrReview.readinessBodies[0].candidate_response?.usage?.total_tokens === 0, "expected readiness probe zero token usage");
  assert(liveAsrReview.reportFetchCount === 0, `expected live ASR not to request report.md, got ${liveAsrReview.reportFetchCount}`);
  assert(liveAsrReview.draftFetchCount === 1, `expected live ASR to request one draft.md, got ${liveAsrReview.draftFetchCount}`);
  await evaluate(page, () => {
    const button = document.getElementById("export-report-button");
    if (!button) {
      throw new Error("missing report refresh button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotFetchUrls || []).filter((url) => url.includes('/draft.md')).length === 2",
  );
  const liveAsrRefreshReview = await evaluate(page, () => {
    return {
      reportText: document.getElementById("report-panel")?.textContent || "",
      reportFetchCount: (window.__meetingCopilotFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
      draftFetchCount: (window.__meetingCopilotFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
    };
  });
  assert(
    liveAsrRefreshReview.reportText.includes("Draft only; not a formal gated meeting report."),
    "expected live ASR manual report refresh to keep draft warning",
  );
  assert(liveAsrRefreshReview.reportFetchCount === 0, `expected live ASR manual report refresh not to request report.md, got ${liveAsrRefreshReview.reportFetchCount}`);
  assert(liveAsrRefreshReview.draftFetchCount === 2, `expected live ASR manual report refresh to request draft.md twice total, got ${liveAsrRefreshReview.draftFetchCount}`);

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    window.__meetingCopilotShadowMvpFetchUrls = [];
    window.__meetingCopilotShadowMvpDomAtEventSourceOpen = null;
    const NativeEventSource = window.EventSource;
    window.EventSource = class extends NativeEventSource {
      constructor(url, options) {
        window.__meetingCopilotEventSourceUrls.push(String(url));
        window.__meetingCopilotShadowMvpDomAtEventSourceOpen = {
          suggestionCards: document.querySelectorAll(".suggestion-card").length,
          stateItems: document.querySelectorAll(".state-item").length,
          transcriptSegments: document.querySelectorAll(".segment-item").length,
          evidenceItems: document.querySelectorAll(".evidence-item").length,
          reportText: document.getElementById("report-panel")?.textContent || "",
          mvpPanelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
        };
        super(url, options);
      }
    };
    window.fetch = (...args) => {
      window.__meetingCopilotShadowMvpFetchUrls.push(String(args[0]));
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("mac-local-shadow-mvp-button");
    if (!button) {
      throw new Error("missing Shadow MVP button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/mac-local-shadow-mvp-demo/sessions')",
  );
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  const shadowMvpDomAtOpen = await evaluate(page, () => window.__meetingCopilotShadowMvpDomAtEventSourceOpen);
  assert(shadowMvpDomAtOpen.suggestionCards === 0, `expected Shadow MVP not to preload cards, got ${shadowMvpDomAtOpen.suggestionCards}`);
  assert(shadowMvpDomAtOpen.stateItems === 0, `expected Shadow MVP not to preload state items, got ${shadowMvpDomAtOpen.stateItems}`);
  assert(shadowMvpDomAtOpen.transcriptSegments === 0, `expected Shadow MVP not to preload transcript segments, got ${shadowMvpDomAtOpen.transcriptSegments}`);
  assert(shadowMvpDomAtOpen.evidenceItems === 0, `expected Shadow MVP not to preload evidence items, got ${shadowMvpDomAtOpen.evidenceItems}`);
  assert(
    shadowMvpDomAtOpen.mvpPanelText.includes("mac_local_shadow_mvp"),
    "expected Shadow MVP panel to identify the demo before SSE events arrive",
  );
  assert(
    !shadowMvpDomAtOpen.reportText.includes("Draft only; not a formal gated meeting report."),
    "expected Shadow MVP report to stay empty before terminal live events",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_partial').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 4");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_revision').length >= 1");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 5");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('mac-local-shadow-mvp-panel')?.textContent?.includes('asr_quality_gate_not_exited')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('card-lifecycle-readiness-panel')?.textContent?.includes('blocked_until_enabled')",
  );
  const shadowMvpReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-asr")?.getAttribute("aria-pressed") || "",
      panelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      evidenceText: document.getElementById("evidence-panel")?.textContent || "",
      cardCount: document.querySelectorAll(".suggestion-card").length,
      draftFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
    };
  });
  assert(shadowMvpReview.livePressed === "true", "expected Shadow MVP to reuse Live ASR mode");
  assert(shadowMvpReview.panelText.includes("mac_local_shadow_mvp"), "expected Shadow MVP panel demo id");
  assert(
    shadowMvpReview.panelText.includes("closed_to_no_llm_request_draft_and_readiness_blockers"),
    "expected Shadow MVP closure status",
  );
  assert(shadowMvpReview.panelText.includes("Finals"), "expected Shadow MVP final event metric label");
  assert(shadowMvpReview.panelText.includes("4"), "expected Shadow MVP final event metric value");
  assert(shadowMvpReview.panelText.includes("Drafts"), "expected Shadow MVP draft event metric label");
  assert(shadowMvpReview.panelText.includes("5"), "expected Shadow MVP draft event metric value");
  assert(shadowMvpReview.panelText.includes("not_called"), "expected Shadow MVP no-LLM status");
  assert(
    shadowMvpReview.panelText.includes("blocked_not_ready_for_user_real_mic_shadow_test"),
    "expected Shadow MVP real mic readiness blocker",
  );
  assert(shadowMvpReview.eventText.includes("live_asr_stream"), "expected Shadow MVP live ASR source");
  assert(shadowMvpReview.eventText.includes("llm_request_draft_event"), "expected Shadow MVP request draft events");
  assert(shadowMvpReview.eventText.includes("not_called"), "expected Shadow MVP events to keep LLM not called");
  assert(shadowMvpReview.stateText.includes("谁负责回滚？"), "expected Shadow MVP open question state");
  assert(shadowMvpReview.stateText.includes("张三下周三补充兼容性测试用例"), "expected Shadow MVP action state");
  assert(shadowMvpReview.transcriptText.includes("先灰度 5%"), "expected Shadow MVP revision transcript");
  assert(shadowMvpReview.evidenceText.includes("superseded"), "expected Shadow MVP evidence lifecycle");
  assert(shadowMvpReview.cardCount === 0, `expected Shadow MVP not to create suggestion cards, got ${shadowMvpReview.cardCount}`);
  assert(shadowMvpReview.draftFetchCount === 1, `expected Shadow MVP to fetch one draft.md, got ${shadowMvpReview.draftFetchCount}`);
  assert(shadowMvpReview.reportFetchCount === 0, `expected Shadow MVP not to fetch report.md, got ${shadowMvpReview.reportFetchCount}`);

  await evaluate(page, () => {
    window.__meetingCopilotShadowMvpFetchUrls = [];
    window.__meetingCopilotShadowMvpDomAtEventSourceOpen = null;
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    const button = document.getElementById("realistic-meeting-simulation-button");
    if (!button) {
      throw new Error("missing realistic meeting simulation button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/realistic-meeting-simulation-pack/sessions')",
  );
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  await waitForCdpExpression(page, "window.__meetingCopilotShadowMvpDomAtEventSourceOpen !== null");
  const realisticDomAtOpen = await evaluate(page, () => window.__meetingCopilotShadowMvpDomAtEventSourceOpen);
  assert(realisticDomAtOpen.suggestionCards === 0, `expected realistic simulation not to preload cards, got ${realisticDomAtOpen.suggestionCards}`);
  assert(realisticDomAtOpen.stateItems === 0, `expected realistic simulation not to preload state items, got ${realisticDomAtOpen.stateItems}`);
  assert(realisticDomAtOpen.transcriptSegments === 0, `expected realistic simulation not to preload transcript segments, got ${realisticDomAtOpen.transcriptSegments}`);
  assert(realisticDomAtOpen.evidenceItems === 0, `expected realistic simulation not to preload evidence items, got ${realisticDomAtOpen.evidenceItems}`);
  assert(
    realisticDomAtOpen.mvpPanelText.includes("realistic_meeting_simulation_pack"),
    "expected realistic simulation panel to identify the simulation before SSE events arrive",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_partial').length >= 3");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 6");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_revision').length >= 2");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 2");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('mac-local-shadow-mvp-panel')?.textContent?.includes('asr_quality_gate_not_exited')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  const realisticReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-asr")?.getAttribute("aria-pressed") || "",
      panelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      evidenceText: document.getElementById("evidence-panel")?.textContent || "",
      cardCount: document.querySelectorAll(".suggestion-card").length,
      draftFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
    };
  });
  assert(realisticReview.livePressed === "true", "expected realistic simulation to reuse Live ASR mode");
  assert(realisticReview.panelText.includes("realistic_meeting_simulation_pack"), "expected realistic simulation panel id");
  assert(realisticReview.panelText.includes("pcweb_126_release_incident_review"), "expected realistic simulation scenario id");
  assert(realisticReview.panelText.includes("multi_speaker_turns"), "expected realistic simulation feature list");
  assert(realisticReview.panelText.includes("payment-gateway"), "expected realistic simulation technical term");
  assert(realisticReview.eventText.includes("llm_request_draft_event"), "expected realistic simulation request draft events");
  assert(realisticReview.eventText.includes("not_called"), "expected realistic simulation events to keep LLM not called");
  assert(realisticReview.stateText.includes("Kafka lag"), "expected realistic simulation risk state");
  assert(realisticReview.stateText.includes("谁确认回滚 owner"), "expected realistic simulation open question state");
  assert(realisticReview.transcriptText.includes("payment-gateway"), "expected realistic simulation transcript technical term");
  assert(realisticReview.transcriptText.includes("P99 延迟超过 800ms"), "expected realistic simulation transcript latency threshold");
  assert(realisticReview.evidenceText.includes("superseded"), "expected realistic simulation revision evidence lifecycle");
  assert(realisticReview.cardCount === 0, `expected realistic simulation not to create suggestion cards, got ${realisticReview.cardCount}`);
  assert(realisticReview.draftFetchCount === 1, `expected realistic simulation to fetch one draft.md, got ${realisticReview.draftFetchCount}`);
  assert(realisticReview.reportFetchCount === 0, `expected realistic simulation not to fetch report.md, got ${realisticReview.reportFetchCount}`);

  await evaluate(page, () => {
    window.__meetingCopilotShadowMvpFetchUrls = [];
    window.__meetingCopilotShadowMvpDomAtEventSourceOpen = null;
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    const button = document.getElementById("long-realistic-meeting-simulation-button");
    if (!button) {
      throw new Error("missing long realistic meeting simulation button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/realistic-meeting-simulation-pack/sessions')",
  );
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  await waitForCdpExpression(page, "window.__meetingCopilotShadowMvpDomAtEventSourceOpen !== null");
  const longRealisticDomAtOpen = await evaluate(page, () => window.__meetingCopilotShadowMvpDomAtEventSourceOpen);
  assert(longRealisticDomAtOpen.suggestionCards === 0, `expected long realistic simulation not to preload cards, got ${longRealisticDomAtOpen.suggestionCards}`);
  assert(longRealisticDomAtOpen.stateItems === 0, `expected long realistic simulation not to preload state items, got ${longRealisticDomAtOpen.stateItems}`);
  assert(longRealisticDomAtOpen.transcriptSegments === 0, `expected long realistic simulation not to preload transcript segments, got ${longRealisticDomAtOpen.transcriptSegments}`);
  assert(longRealisticDomAtOpen.evidenceItems === 0, `expected long realistic simulation not to preload evidence items, got ${longRealisticDomAtOpen.evidenceItems}`);
  assert(
    longRealisticDomAtOpen.mvpPanelText.includes("pcweb_127_long_architecture_release_review"),
    "expected long realistic simulation panel to identify the long scenario before SSE events arrive",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_partial').length >= 5");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 13");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_revision').length >= 3");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 12");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  const longRealisticReview = await evaluate(page, () => {
    return {
      panelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      evidenceText: document.getElementById("evidence-panel")?.textContent || "",
      cardCount: document.querySelectorAll(".suggestion-card").length,
      draftFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
    };
  });
  assert(longRealisticReview.panelText.includes("pcweb_127_long_architecture_release_review"), "expected long realistic simulation scenario id");
  assert(longRealisticReview.panelText.includes("long_meeting_timeline"), "expected long realistic simulation long timeline feature");
  assert(longRealisticReview.panelText.includes("idempotency-key"), "expected long realistic simulation idempotency term");
  assert(longRealisticReview.panelText.includes("Redis cluster"), "expected long realistic simulation Redis term");
  assert(longRealisticReview.panelText.includes("SLO"), "expected long realistic simulation SLO term");
  assert(longRealisticReview.transcriptText.includes("idempotency-key"), "expected long realistic transcript idempotency term");
  assert(longRealisticReview.transcriptText.includes("SLO 99.9%"), "expected long realistic transcript SLO");
  assert(longRealisticReview.stateText.includes("谁确认降级开关 owner"), "expected long realistic open question state");
  assert(longRealisticReview.stateText.includes("Redis cluster"), "expected long realistic risk state");
  assert(longRealisticReview.evidenceText.includes("superseded"), "expected long realistic revision evidence lifecycle");
  assert(longRealisticReview.eventText.includes("not_called"), "expected long realistic simulation events to keep LLM not called");
  assert(longRealisticReview.cardCount === 0, `expected long realistic simulation not to create suggestion cards, got ${longRealisticReview.cardCount}`);
  assert(longRealisticReview.draftFetchCount === 1, `expected long realistic simulation to fetch one draft.md, got ${longRealisticReview.draftFetchCount}`);
  assert(longRealisticReview.reportFetchCount === 0, `expected long realistic simulation not to fetch report.md, got ${longRealisticReview.reportFetchCount}`);

  await evaluate(page, () => {
    window.__meetingCopilotShadowMvpFetchUrls = [];
    window.__meetingCopilotShadowMvpDomAtEventSourceOpen = null;
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    const button = document.getElementById("mainline-asr-blocked-trial-button");
    if (!button) {
      throw new Error("missing mainline ASR blocked trial button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/mainline-asr-blocked-trial/sessions')",
  );
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  await waitForCdpExpression(page, "window.__meetingCopilotShadowMvpDomAtEventSourceOpen !== null");
  const mainlineDomAtOpen = await evaluate(page, () => window.__meetingCopilotShadowMvpDomAtEventSourceOpen);
  assert(mainlineDomAtOpen.suggestionCards === 0, `expected mainline trial not to preload cards, got ${mainlineDomAtOpen.suggestionCards}`);
  assert(mainlineDomAtOpen.stateItems === 0, `expected mainline trial not to preload state items, got ${mainlineDomAtOpen.stateItems}`);
  assert(
    mainlineDomAtOpen.mvpPanelText.includes("mainline_asr_blocked_trial"),
    "expected mainline trial panel to identify the trial before SSE events arrive",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 13");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 12");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('mac-local-shadow-mvp-panel')?.textContent?.includes('blocked_by_funasr_smoke_assembly_input_guard')",
  );
  const mainlineReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-asr")?.getAttribute("aria-pressed") || "",
      panelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      draftFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotShadowMvpFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
    };
  });
  assert(mainlineReview.livePressed === "true", "expected mainline trial to reuse Live ASR mode");
  assert(mainlineReview.panelText.includes("mainline_asr_blocked_trial"), "expected mainline trial id");
  assert(mainlineReview.panelText.includes("DEC-201"), "expected mainline trial decision id");
  assert(mainlineReview.panelText.includes("not_exited"), "expected mainline trial ASR exit status");
  assert(
    mainlineReview.panelText.includes("continue_pc_product_flow_keep_real_mic_blocked"),
    "expected mainline trial next action",
  );
  assert(mainlineReview.panelText.includes("chunk10_hotword"), "expected mainline trial chunk10 candidate");
  assert(mainlineReview.panelText.includes("chunk20_hotword"), "expected mainline trial chunk20 candidate");
  assert(mainlineReview.panelText.includes("incident-review-001"), "expected mainline trial failed FunASR scenario");
  assert(mainlineReview.eventText.includes("not_called"), "expected mainline trial events to keep LLM not called");
  assert(mainlineReview.stateText.includes("谁确认降级开关 owner"), "expected mainline trial state flow");
  assert(mainlineReview.transcriptText.includes("recommendation-service"), "expected mainline trial transcript technical term");
  assert(mainlineReview.draftFetchCount === 1, `expected mainline trial to fetch one draft.md, got ${mainlineReview.draftFetchCount}`);
  assert(mainlineReview.reportFetchCount === 0, `expected mainline trial not to fetch report.md, got ${mainlineReview.reportFetchCount}`);

  await evaluate(page, () => {
    const button = document.getElementById("mainline-feedback-export-closure-button");
    if (!button) {
      throw new Error("missing mainline feedback export closure button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/mainline-trial-feedback-export-closures')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('mainline-closure-panel')?.textContent?.includes('mainline_trial_feedback_export_closure')",
  );
  const mainlineClosureReview = await evaluate(page, () => {
    return {
      closureText: document.getElementById("mainline-closure-panel")?.textContent || "",
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(
    mainlineClosureReview.closureText.includes("draft_export_preview_only"),
    "expected mainline closure to show draft export preview only",
  );
  assert(
    mainlineClosureReview.closureText.includes("not_go_evidence_replay_or_feedback_missing"),
    "expected mainline closure to show replay feedback not-Go evidence",
  );
  assert(
    mainlineClosureReview.closureText.includes("inconclusive_requires_more_shadow_tests"),
    "expected mainline closure to show inconclusive final decision",
  );
  assert(
    mainlineClosureReview.closureText.includes("positive=2"),
    "expected mainline closure to show deterministic positive feedback count",
  );
  assert(
    mainlineClosureReview.closureText.includes("negative=0"),
    "expected mainline closure to show zero negative feedback",
  );
  assert(
    mainlineClosureReview.closureText.includes("asr_suggestion_candidate_asr_state_event_long_shadow_seg_001"),
    "expected mainline closure to show selected candidate id",
  );
  assert(
    mainlineClosureReview.reportText.includes("Draft only; not real mic validation."),
    "expected mainline closure markdown preview to be rendered in report panel",
  );
  assert(
    mainlineClosureReview.reportText.includes("确认决策是否包含 owner"),
    "expected mainline closure markdown to include suggestion candidate text",
  );

  await evaluate(page, () => {
    window.__meetingCopilotShadowMvpFetchUrls = [];
    window.__meetingCopilotShadowMvpDomAtEventSourceOpen = null;
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    const button = document.getElementById("mainline-asr-event-artifact-trial-button");
    if (!button) {
      throw new Error("missing mainline ASR event artifact trial button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/mainline-asr-event-artifact-trial/sessions')",
  );
  await waitForCdpExpression(
    page,
    "window.__meetingCopilotEventSourceUrls?.some((url) => url.includes('/live/asr/sessions/') && url.endsWith('/events.sse'))",
  );
  await waitForCdpExpression(page, "window.__meetingCopilotShadowMvpDomAtEventSourceOpen !== null");
  const artifactMainlineDomAtOpen = await evaluate(page, () => window.__meetingCopilotShadowMvpDomAtEventSourceOpen);
  assert(
    artifactMainlineDomAtOpen.mvpPanelText.includes("mainline_asr_event_artifact_trial"),
    "expected artifact mainline panel to identify the artifact trial before SSE events arrive",
  );
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.transcript_final').length >= 4");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.llm_request_draft_event').length >= 3");
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('mac-local-shadow-mvp-panel')?.textContent?.includes('DEC-214')",
  );
  const artifactMainlineReview = await evaluate(page, () => {
    return {
      panelText: document.getElementById("mac-local-shadow-mvp-panel")?.textContent || "",
      stateText: document.getElementById("state-board")?.textContent || "",
      transcriptText: document.getElementById("transcript-panel")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
    };
  });
  assert(artifactMainlineReview.panelText.includes("local_asr_event_file_handoff_created"), "expected artifact mainline source status");
  assert(artifactMainlineReview.panelText.includes("m15_runner_artifact_mainline.events.json"), "expected artifact mainline path");
  assert(artifactMainlineReview.stateText.includes("谁确认降级开关 owner"), "expected artifact mainline open question state");
  assert(artifactMainlineReview.stateText.includes("Redis cluster"), "expected artifact mainline risk state");
  assert(artifactMainlineReview.transcriptText.includes("idempotency-key"), "expected artifact mainline transcript technical term");
  assert(artifactMainlineReview.eventText.includes("not_called"), "expected artifact mainline events to keep LLM not called");

  await evaluate(page, () => {
    const button = document.getElementById("mainline-feedback-export-closure-button");
    if (!button) {
      throw new Error("missing mainline feedback export closure button");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "(window.__meetingCopilotShadowMvpFetchUrls || []).includes('/desktop/mainline-trial-feedback-export-closures')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('mainline-closure-panel')?.textContent?.includes('mainline_asr_event_artifact_trial')",
  );
  const artifactClosureReview = await evaluate(page, () => {
    return {
      closureText: document.getElementById("mainline-closure-panel")?.textContent || "",
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(
    artifactClosureReview.closureText.includes("local_asr_event_file_handoff_created"),
    "expected artifact closure to show source artifact handoff status",
  );
  assert(
    artifactClosureReview.closureText.includes("inconclusive_requires_more_shadow_tests"),
    "expected artifact closure to show inconclusive final decision",
  );
  assert(
    artifactClosureReview.reportText.includes("Draft only; not real mic validation."),
    "expected artifact closure markdown preview to be rendered in report panel",
  );
  assert(
    artifactClosureReview.reportText.includes("确认未闭环问题是否需要现场追问"),
    "expected artifact closure markdown to include artifact suggestion candidate text",
  );

  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "document.querySelectorAll('.event-item.suggestion_silenced').length === 3");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('## Silenced Suggestion Records')");
  const schemaReview = await evaluate(page, () => {
    return {
      sessionTitle: document.getElementById("summary-title")?.textContent || "",
      gateText: document.getElementById("evaluation-panel")?.textContent || "",
      mutedCards: document.querySelectorAll(".suggestion-card.muted").length,
      mutedActionButtons: document.querySelectorAll(".suggestion-card.muted .card-actions button").length,
      silencedEvents: document.querySelectorAll(".event-item.suggestion_silenced").length,
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(
    schemaReview.sessionTitle.includes("workbench_schema-degradation-review_"),
    `expected schema degradation session, got ${schemaReview.sessionTitle}`,
  );
  assert(schemaReview.gateText.includes("Gate passed"), "expected schema degradation gate pass");
  assert(schemaReview.gateText.includes("Schema Blocked"), "expected Schema Blocked metric");
  assert(schemaReview.mutedCards === 3, `expected 3 muted cards, got ${schemaReview.mutedCards}`);
  assert(
    schemaReview.mutedActionButtons === 0,
    `expected no muted card action buttons, got ${schemaReview.mutedActionButtons}`,
  );
  assert(
    schemaReview.silencedEvents === 3,
    `expected 3 suggestion_silenced events, got ${schemaReview.silencedEvents}`,
  );
  assert(
    schemaReview.reportText.includes("## Silenced Suggestion Records"),
    "expected silenced suggestion records in report",
  );

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__meetingCopilotPendingModeFetchUrls = [];
    window.__releaseLiveAsrCreate = null;
    window.__delayLiveAsrCreate = true;
    window.fetch = (...args) => {
      const url = String(args[0]);
      window.__meetingCopilotPendingModeFetchUrls.push(url);
      if (url === "/live/asr/mock/sessions" && window.__delayLiveAsrCreate) {
        window.__delayLiveAsrCreate = false;
        return new Promise((resolve, reject) => {
          window.__releaseLiveAsrCreate = () =>
            window.__meetingCopilotNativeFetch(...args).then(resolve, reject);
        });
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button for pending-session test");
    }
    button.click();
  });
  await waitForCdpExpression(page, "typeof window.__releaseLiveAsrCreate === 'function'");
  await evaluate(page, () => {
    const button = document.getElementById("export-report-button");
    if (!button) {
      throw new Error("missing report refresh button for pending-session test");
    }
    button.click();
  });
  await delay(300);
  const pendingModeReview = await evaluate(page, () => {
    return {
      draftFetchCount: (window.__meetingCopilotPendingModeFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotPendingModeFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
    };
  });
  assert(pendingModeReview.draftFetchCount === 0, `expected pending Live ASR session not to request old-session draft.md, got ${pendingModeReview.draftFetchCount}`);
  assert(pendingModeReview.reportFetchCount === 0, `expected pending Live ASR session not to request report.md, got ${pendingModeReview.reportFetchCount}`);
  await evaluate(page, () => window.__releaseLiveAsrCreate());
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );

  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('## Silenced Suggestion Records')");
  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__releaseStaleLiveAsrCreate = null;
    window.__delayStaleLiveAsrCreate = true;
    window.fetch = (...args) => {
      const url = String(args[0]);
      if (url === "/live/asr/mock/sessions" && window.__delayStaleLiveAsrCreate) {
        window.__delayStaleLiveAsrCreate = false;
        return new Promise((resolve, reject) => {
          window.__releaseStaleLiveAsrCreate = () =>
            window.__meetingCopilotNativeFetch(...args).then(resolve, reject);
        });
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button for stale-create test");
    }
    button.click();
  });
  await waitForCdpExpression(page, "typeof window.__releaseStaleLiveAsrCreate === 'function'");
  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('## Silenced Suggestion Records')");
  await evaluate(page, () => window.__releaseStaleLiveAsrCreate());
  await delay(500);
  const staleCreateReview = await evaluate(page, () => {
    return {
      livePressed: document.getElementById("event-mode-live-asr")?.getAttribute("aria-pressed") || "",
      sessionTitle: document.getElementById("summary-title")?.textContent || "",
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(staleCreateReview.livePressed === "false", "expected stale Live ASR create response not to reselect Live ASR mode");
  assert(staleCreateReview.sessionTitle.includes("workbench_schema-degradation-review_"), `expected stale Live ASR create not to overwrite current replay session, got ${staleCreateReview.sessionTitle}`);
  assert(staleCreateReview.reportText.includes("## Silenced Suggestion Records"), "expected stale Live ASR create not to overwrite current replay report");

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__releaseStaleEventStream = null;
    window.__delayNextEventStream = true;
    window.fetch = (...args) => {
      const url = String(args[0]);
      if (url.includes("/events") && !url.includes(".sse") && window.__delayNextEventStream) {
        window.__delayNextEventStream = false;
        return new Promise((resolve, reject) => {
          window.__releaseStaleEventStream = () =>
            window.__meetingCopilotNativeFetch(...args).then(resolve, reject);
        });
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
  });
  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "typeof window.__releaseStaleEventStream === 'function'");
  await selectFixture(page, "api-review");
  await waitForCdpExpression(page, "document.getElementById('summary-title')?.textContent?.includes('workbench_api-review_')");
  await evaluate(page, () => window.__releaseStaleEventStream());
  await delay(500);
  const staleEventsReview = await evaluate(page, () => {
    return {
      sessionTitle: document.getElementById("summary-title")?.textContent || "",
      eventText: document.getElementById("event-stream-panel")?.textContent || "",
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(staleEventsReview.sessionTitle.includes("workbench_api-review_"), `expected stale events response not to overwrite current api-review session, got ${staleEventsReview.sessionTitle}`);
  assert(staleEventsReview.eventText.includes("workbench_api-review_") || staleEventsReview.reportText.includes("Meeting workbench_api-review_"), "expected current api-review UI to remain after stale events response");

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.fetch = window.__meetingCopilotNativeFetch;
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button before stale-draft test");
    }
    button.click();
  });
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__meetingCopilotStaleDraftFetchUrls = [];
    window.__releaseDelayedDraft = null;
    window.__delayNextDraft = true;
    window.fetch = (...args) => {
      const url = String(args[0]);
      window.__meetingCopilotStaleDraftFetchUrls.push(url);
      if (url.includes("/draft.md") && window.__delayNextDraft) {
        window.__delayNextDraft = false;
        return new Promise((resolve, reject) => {
          window.__releaseDelayedDraft = () =>
            window.__meetingCopilotNativeFetch(...args).then(resolve, reject);
        });
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("export-report-button");
    if (!button) {
      throw new Error("missing report refresh button for stale-draft test");
    }
    button.click();
  });
  await waitForCdpExpression(page, "typeof window.__releaseDelayedDraft === 'function'");
  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('## Silenced Suggestion Records')");
  await evaluate(page, () => window.__releaseDelayedDraft());
  await delay(300);
  const staleDraftReview = await evaluate(page, () => {
    return {
      reportText: document.getElementById("report-panel")?.textContent || "",
    };
  });
  assert(staleDraftReview.reportText.includes("## Silenced Suggestion Records"), "expected delayed old Live ASR draft not to overwrite the current replay report");
  assert(!staleDraftReview.reportText.includes("Draft only; not a formal gated meeting report."), "expected delayed old Live ASR draft warning not to overwrite current replay report");

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.fetch = window.__meetingCopilotNativeFetch;
    window.__meetingCopilotDegradedFetchUrls = [];
    const originalLocalAsrStreamingEvents = window.localAsrStreamingEvents;
    window.localAsrStreamingEvents = () => [
      {
        event_type: "final",
        segment_id: "asr_degraded_seg_001",
        text: "灰度",
        start_ms: 0,
        end_ms: 500,
        received_at_ms: 900,
        confidence: 0.3,
      },
      {
        event_type: "end_of_stream",
        segment_id: "asr_degraded_eos",
        text: "",
        start_ms: 500,
        end_ms: 500,
        received_at_ms: 1000,
      },
    ];
    window.fetch = (...args) => {
      window.__meetingCopilotDegradedFetchUrls.push(String(args[0]));
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button for degraded readiness test");
    }
    button.click();
    window.localAsrStreamingEvents = originalLocalAsrStreamingEvents;
  });
  await waitForCdpExpression(page, "window.__meetingCopilotLiveStreamClosed === true");
  await waitForCdpExpression(
    page,
    "document.getElementById('card-lifecycle-readiness-panel')?.textContent?.includes('没有可评估')",
  );
  const degradedReadinessReview = await evaluate(page, () => {
    return {
      lifecycleText: document.getElementById("card-lifecycle-readiness-panel")?.textContent || "",
      readinessSummaryFetchCount: (window.__meetingCopilotDegradedFetchUrls || [])
        .filter((url) => url.includes("/llm-card-lifecycle-readiness-summaries")).length,
    };
  });
  assert(
    degradedReadinessReview.lifecycleText.includes("没有可评估"),
    "expected degraded-only Live ASR stream to show local no-summary state",
  );
  assert(
    degradedReadinessReview.readinessSummaryFetchCount === 0,
    `expected degraded-only Live ASR stream not to POST readiness summary, got ${degradedReadinessReview.readinessSummaryFetchCount}`,
  );

  await selectFixture(page, "schema-degradation-review");
  await waitForCdpExpression(page, "document.getElementById('report-panel')?.textContent?.includes('## Silenced Suggestion Records')");

  await evaluate(page, () => {
    if (!window.__meetingCopilotNativeFetch) {
      window.__meetingCopilotNativeFetch = window.fetch.bind(window);
    }
    window.__meetingCopilotFallbackFetchUrls = [];
    window.__meetingCopilotFallbackReadinessBodies = [];
    window.__meetingCopilotEventSourceUrls = [];
    window.__meetingCopilotLiveStreamClosed = false;
    window.EventSource = undefined;
    window.fetch = (...args) => {
      const url = String(args[0]);
      window.__meetingCopilotFallbackFetchUrls.push(url);
      if (url.includes("/llm-card-lifecycle-readiness-summaries")) {
        try {
          window.__meetingCopilotFallbackReadinessBodies.push(JSON.parse(args[1]?.body || "{}"));
        } catch {
          window.__meetingCopilotFallbackReadinessBodies.push({parse_error: true});
        }
      }
      return window.__meetingCopilotNativeFetch(...args);
    };
    const button = document.getElementById("event-mode-live-asr");
    if (!button) {
      throw new Error("missing Live ASR event mode button for fallback test");
    }
    button.click();
  });
  await waitForCdpExpression(
    page,
    "document.getElementById('report-panel')?.textContent?.includes('Draft only; not a formal gated meeting report.')",
  );
  await waitForCdpExpression(
    page,
    "document.getElementById('card-lifecycle-readiness-panel')?.textContent?.includes('blocked_until_enabled')",
  );
  const fallbackReview = await evaluate(page, () => {
    return {
      draftFetchCount: (window.__meetingCopilotFallbackFetchUrls || [])
        .filter((url) => url.includes("/draft.md")).length,
      reportFetchCount: (window.__meetingCopilotFallbackFetchUrls || [])
        .filter((url) => url.includes("/report.md")).length,
      readinessSummaryFetchCount: (window.__meetingCopilotFallbackFetchUrls || [])
        .filter((url) => url.includes("/llm-card-lifecycle-readiness-summaries")).length,
      lifecycleText: document.getElementById("card-lifecycle-readiness-panel")?.textContent || "",
      readinessBodies: window.__meetingCopilotFallbackReadinessBodies || [],
      eventSourceUrlCount: (window.__meetingCopilotEventSourceUrls || []).length,
    };
  });
  assert(fallbackReview.draftFetchCount === 1, `expected EventSource fallback to request draft.md once, got ${fallbackReview.draftFetchCount}`);
  assert(fallbackReview.reportFetchCount === 0, `expected EventSource fallback not to request report.md, got ${fallbackReview.reportFetchCount}`);
  assert(fallbackReview.lifecycleText.includes("blocked_until_enabled"), "expected EventSource fallback to render lifecycle readiness summary");
  assert(fallbackReview.readinessSummaryFetchCount === 1, `expected EventSource fallback to request readiness summary once, got ${fallbackReview.readinessSummaryFetchCount}`);
  assert(fallbackReview.readinessBodies[0]?.candidate_response?.type === "owner_gap", "expected EventSource fallback readiness probe type owner_gap");
  assert(fallbackReview.eventSourceUrlCount === 0, `expected EventSource fallback not to open EventSource URLs, got ${fallbackReview.eventSourceUrlCount}`);

  console.log(
    JSON.stringify(
      {
        status: "ok",
        checked: [
          "desktop shell readiness panel",
          "desktop runtime boundary panel",
          "desktop native bridge contract panel",
          "desktop native runtime browser fallback",
          "desktop ASR handoff dry-run readiness panel",
          "desktop mic adapter contract readiness panel",
          "desktop mic adapter no-op invocation browser fallback",
          "desktop Tauri no-op result collector browser fallback",
          "local shadow preview release readiness",
          "json_smoke_api_review evidence click-back",
          "replay event stream",
          "live mock EventSource stream",
          "live mock incremental UI",
          "live mock SSE revision lifecycle",
          "live mock suggestion invalidation lifecycle",
          "live mock feedback without full snapshot rehydrate",
          "live ASR local EventSource skeleton",
          "live ASR readiness POST body and degraded-draft no-summary boundary",
          "Mac Local Shadow MVP synthetic demo closure",
          "realistic meeting simulation pack",
          "long realistic meeting simulation pack",
          "mainline ASR blocked trial",
          "mainline trial feedback export closure",
          "mainline ASR event artifact trial",
          "mainline ASR event artifact feedback export closure",
          "live ASR no-EventSource readiness fallback",
          "schema degradation muted cards",
          "schema degradation report",
        ],
        dataDir,
      },
      null,
      2,
    ),
  );
} finally {
  await cleanup();
}

async function ensureMainlineArtifactTrialFixture() {
  const eventsPath = path.join(
    repoRoot,
    "artifacts",
    "tmp",
    "asr_events",
    "m15_runner_artifact_mainline.events.json",
  );
  await mkdir(path.dirname(eventsPath), { recursive: true });
  await writeFile(
    eventsPath,
    JSON.stringify(
      [
        {
          event_type: "final",
          segment_id: "artifact_mainline_001",
          text: "我们先确认 payment-gateway 的 rollback owner。",
          start_ms: 0,
          end_ms: 3000,
          received_at_ms: 3500,
          confidence: 0.92,
        },
        {
          event_type: "final",
          segment_id: "artifact_mainline_002",
          text: "谁确认降级开关 owner？feature flag 半夜触发时值班群怎么通知？",
          start_ms: 4000,
          end_ms: 6500,
          received_at_ms: 7000,
          confidence: 0.91,
        },
        {
          event_type: "final",
          segment_id: "artifact_mainline_003",
          text: "如果 Redis cluster 缓存穿透打到 MySQL，P99 超过 900ms 就触发 rollback。",
          start_ms: 8000,
          end_ms: 12500,
          received_at_ms: 13000,
          confidence: 0.91,
        },
        {
          event_type: "final",
          segment_id: "artifact_mainline_004",
          text: "李四明天补充 idempotency-key 重试和 callback 失败的兼容测试。",
          start_ms: 14000,
          end_ms: 17500,
          received_at_ms: 18000,
          confidence: 0.91,
        },
        {
          event_type: "end_of_stream",
          segment_id: "artifact_mainline_eos",
          text: "",
          start_ms: 17500,
          end_ms: 17500,
          received_at_ms: 18100,
        },
      ],
      null,
      2,
    ),
    "utf8",
  );
}

function collectLogs(child, label) {
  child.stdout?.on("data", (chunk) => {
    if (process.env.MEETING_COPILOT_E2E_VERBOSE) {
      process.stdout.write(`[${label}] ${chunk}`);
    }
  });
  child.stderr?.on("data", (chunk) => {
    if (process.env.MEETING_COPILOT_E2E_VERBOSE) {
      process.stderr.write(`[${label}] ${chunk}`);
    }
  });
}

async function waitForHttp(url, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
    } catch {
      // Keep polling until the local process is ready.
    }
    await delay(120);
  }
  throw new Error(`timed out waiting for ${url}`);
}

async function createCdpPage(debugPort, url) {
  const response = await fetch(`http://127.0.0.1:${debugPort}/json/new?${encodeURIComponent(url)}`, {
    method: "PUT",
  });
  if (!response.ok) {
    throw new Error(`failed to create Chrome DevTools page: ${response.status}`);
  }
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
      if (message.error) {
        reject(new Error(message.error.message));
      } else {
        resolve(message.result || {});
      }
    }
  });
  const page = {
    socket,
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
      });
    },
  };
  await page.send("Runtime.enable");
  await page.send("Page.enable");
  await page.send("Page.navigate", { url });
  await waitForCdpExpression(page, "document.readyState === 'complete'");
  return page;
}

async function evaluate(page, fn, arg = undefined) {
  const serializedArg = JSON.stringify(arg);
  const expression = `(${fn.toString()})(${serializedArg})`;
  const result = await page.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "browser evaluation failed");
  }
  return result.result.value;
}

async function waitForCdpExpression(page, expression, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await evaluate(page, () => true);
    if (result) {
      const check = await page.send("Runtime.evaluate", {
        expression,
        awaitPromise: true,
        returnByValue: true,
      });
      if (check.result?.value) {
        return;
      }
    }
    await delay(120);
  }
  throw new Error(`timed out waiting for browser expression: ${expression}`);
}

async function selectFixture(page, fixtureId) {
  await evaluate(page, (id) => {
    const select = document.getElementById("fixture-select");
    const button = document.getElementById("load-fixture-button");
    if (!select || !button) {
      throw new Error("fixture controls are missing");
    }
    select.value = id;
    button.click();
  }, fixtureId);
  await waitForCdpExpression(
    page,
    `document.getElementById("summary-title")?.textContent?.includes("workbench_${fixtureId}_")`,
  );
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function cleanup() {
  for (const socket of cdpSockets.splice(0).reverse()) {
    try {
      socket.close();
    } catch {
      // Best-effort CDP cleanup before process termination.
    }
  }
  for (const child of processes.reverse()) {
    if (!child.killed) {
      child.kill("SIGTERM");
    }
  }
  await Promise.all(
    processes.map(async (child) => {
      const exited = await onceExit(child);
      if (!exited && child.exitCode === null && child.signalCode === null) {
        child.kill("SIGKILL");
        await onceExit(child);
      }
    }),
  );
  await rm(dataDir, { recursive: true, force: true });
  await rm(chromeUserDataDir, { recursive: true, force: true });
}

function onceExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve(true);
  }
  return new Promise((resolve) => {
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off("exit", onExit);
      resolve(false);
    }, 1500);
    child.once("exit", onExit);
  });
}

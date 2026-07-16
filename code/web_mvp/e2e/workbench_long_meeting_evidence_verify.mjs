// Workbench long-meeting evidence selftest: fixture-only, no remote ASR/LLM.
// It verifies the UI can carry a longer Chinese technical meeting context with
// transcript finals, a revision, suggestion cards, approach cards, minutes,
// exports, evidence clickback, history reopen, and delete.
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const repoRoot = path.resolve(import.meta.dirname, "..", "..", "..");
const backendDir = path.join(repoRoot, "code", "web_mvp", "backend");
const dataDir = await mkdtemp(path.join(tmpdir(), "mc-long-meeting-data-"));
const chromeUserDataDir = await mkdtemp(path.join(tmpdir(), "mc-long-meeting-chrome-"));
const port = Number(process.env.MEETING_COPILOT_E2E_PORT || "8774");
const chromePort = Number(process.env.MEETING_COPILOT_E2E_CHROME_PORT || "9234");
const chromePath = process.env.CHROME_BIN || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const artifactRoot = path.resolve(
  process.env.MEETING_COPILOT_ARTIFACT_ROOT
    || path.join(repoRoot, "artifacts", "tmp", "ui_screenshots", "workbench-long-meeting-evidence"),
);

const sessionId = "long_meeting_evidence_20260710";
const syntheticDurationMinutes = 20;
const originalClickSegmentId = "long_seg_03";
const originalClickEvidenceId = `asr_ev_${originalClickSegmentId}`;
const revisionSourceSegmentId = "long_seg_06";
const revisionSegmentId = `${revisionSourceSegmentId}_rev1`;
const revisionEvidenceId = `asr_ev_${revisionSegmentId}`;
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

  page = await createCdpPage(chromePort, `http://127.0.0.1:${port}/workbench?demo=1`);
  await waitForCdpExpression(page, `document.getElementById('btn-history') !== null`, 15000);
  await captureStep(page, "initial_page", "Workbench 已加载，准备打开长会议历史会话");

  await evaluate(page, () => document.getElementById("btn-history").click());
  await waitForCdpExpression(page, `document.querySelector('.history-modal-item[data-session-id="${sessionId}"]') !== null`, 15000);
  await evaluate(page, (sid) => {
    const item = document.querySelector(`.history-modal-item[data-session-id="${sid}"]`);
    const button = item?.querySelector('button[data-action="open"]');
    if (!button) throw new Error(`history item open action missing: ${sid}`);
    button.click();
  }, sessionId);

  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes(${JSON.stringify(sessionId)})`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll('.utterance').length >= 12`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll('.transcript-segment[data-status="corrected"]').length >= 1`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='suggestion']").length >= 4`, 15000);
  await waitForCdpExpression(page, `document.querySelectorAll("[data-card-kind='approach']").length >= 2`, 15000);
  await evaluate(page, () => {
    if (document.getElementById("review-workspace")) document.getElementById("review-workspace").open = true;
  });
  await waitForCdpExpression(page, `document.getElementById('minutes-panel').textContent.includes('会议纪要')`, 15000);
  await captureStep(page, "long_session_loaded", "长会议会话已加载，多条转写、建议、方案和纪要可见");

  const loadedState = await evaluate(page, (expectedRevisionSegmentId) => ({
    synthetic_duration_minutes: 20,
    counts_as_20_60_min_production_soak: false,
    utterance_count: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
    revision_utterance_count: document.querySelectorAll('.transcript-segment[data-status="corrected"]').length,
    suggestion_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approach_card_count: document.querySelectorAll("[data-card-kind='approach']").length,
    evidence_link_count: document.querySelectorAll(".evidence-link").length,
    minutes_visible: document.getElementById("minutes-panel")?.textContent.includes("会议纪要") || false,
    revision_utterance_visible: Array.from(document.querySelectorAll('.transcript-segment[data-status="corrected"]'))
      .some((el) => el.dataset.segmentId === "long_seg_06"),
    revision_source_segment_visible: Array.from(document.querySelectorAll('.transcript-segment[data-status="corrected"]'))
      .some((el) => el.dataset.sourceSegmentId === expectedRevisionSegmentId),
    revision_relationship_visible: Array.from(document.querySelectorAll('.transcript-segment[data-status="corrected"]'))
      .some((el) => el.dataset.revisionOf === "long_seg_06"),
    transcript_text: document.getElementById("transcript-stream")?.innerText || "",
    suggestions_text: document.getElementById("suggestions-panel")?.innerText || "",
    approach_text: document.getElementById("approach-panel")?.innerText || "",
    minutes_text: document.getElementById("minutes-panel")?.innerText || "",
  }), revisionSegmentId);
  assert(loadedState.utterance_count >= 12, `expected canonical transcript segments: ${JSON.stringify(loadedState)}`);
  assert(loadedState.revision_utterance_count >= 1, `revision row missing: ${JSON.stringify(loadedState)}`);
  assert(loadedState.suggestion_card_count >= 4, `suggestion cards missing: ${JSON.stringify(loadedState)}`);
  assert(loadedState.approach_card_count >= 2, `approach cards missing: ${JSON.stringify(loadedState)}`);
  assert(loadedState.evidence_link_count >= 4, `evidence links missing: ${JSON.stringify(loadedState)}`);
  assert(loadedState.minutes_visible, `minutes not visible: ${JSON.stringify(loadedState)}`);
  assert(loadedState.revision_utterance_visible, `revision utterance not visible: ${JSON.stringify(loadedState)}`);
  assert(loadedState.revision_source_segment_visible, `revision source segment not exposed: ${JSON.stringify(loadedState)}`);
  assert(loadedState.revision_relationship_visible, `revision relationship not visible: ${JSON.stringify(loadedState)}`);

  await evaluate(page, (evidenceId) => {
    const button = Array.from(document.querySelectorAll(".evidence-link"))
      .find((el) => el.dataset.evidenceId === evidenceId);
    if (!button) throw new Error(`original evidence link missing: ${evidenceId}`);
    button.click();
  }, originalClickEvidenceId);
  await waitForCdpExpression(
    page,
    `Array.from(document.querySelectorAll(".transcript-segment.evidence-focus")).some((el) => el.dataset.segmentId === ${JSON.stringify(originalClickSegmentId)})`,
    5000,
  );
  const originalClickback = await focusedEvidenceState(page, originalClickSegmentId);
  assert(originalClickback.focused_segment_id === originalClickSegmentId, `wrong original focus: ${JSON.stringify(originalClickback)}`);
  assert(originalClickback.focused_evidence_ids.includes(originalClickEvidenceId), `wrong original evidence focus: ${JSON.stringify(originalClickback)}`);
  await captureStep(page, "original_evidence_clickback", "点击普通建议证据后，原始转写行高亮");

  await evaluate(page, (evidenceId) => {
    const button = Array.from(document.querySelectorAll(".evidence-link"))
      .find((el) => el.dataset.evidenceId === evidenceId);
    if (!button) throw new Error(`revision evidence link missing: ${evidenceId}`);
    button.click();
  }, revisionEvidenceId);
  await waitForCdpExpression(
    page,
    `Array.from(document.querySelectorAll(".transcript-segment.evidence-focus")).some((el) => el.dataset.segmentId === ${JSON.stringify(revisionSourceSegmentId)} && el.dataset.sourceSegmentId === ${JSON.stringify(revisionSegmentId)} && el.dataset.status === "corrected")`,
    5000,
  );
  const revisionClickback = await focusedEvidenceState(page, revisionSegmentId);
  assert(revisionClickback.focused_segment_id === revisionSourceSegmentId, `wrong revision target focus: ${JSON.stringify(revisionClickback)}`);
  assert(revisionClickback.focused_source_segment_id === revisionSegmentId, `wrong revision source focus: ${JSON.stringify(revisionClickback)}`);
  assert(revisionClickback.focused_evidence_ids.includes(revisionEvidenceId), `wrong revision evidence focus: ${JSON.stringify(revisionClickback)}`);
  assert(revisionClickback.original_details_open, `original ASR disclosure not opened: ${JSON.stringify(revisionClickback)}`);
  assert(
    revisionClickback.focused_text.includes("灰度") && revisionClickback.focused_text.includes("AI 已校正"),
    `corrected revision text not visible: ${JSON.stringify(revisionClickback)}`,
  );
  await captureStep(page, "revision_evidence_clickback", "点击修正建议证据后，修正文稿行高亮");

  const renderedPanels = await evaluate(page, () => ({
    suggestion_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approach_card_count: document.querySelectorAll("[data-card-kind='approach']").length,
    minutes_visible: document.getElementById("minutes-panel")?.innerText.includes("会议纪要") || false,
    approach_text: document.getElementById("approach-panel")?.innerText || "",
    minutes_text: document.getElementById("minutes-panel")?.innerText || "",
  }));
  assert(renderedPanels.approach_card_count >= 2, `approach cards not visible: ${JSON.stringify(renderedPanels)}`);
  assert(renderedPanels.minutes_visible, `minutes not visible before export: ${JSON.stringify(renderedPanels)}`);
  await captureStep(page, "minutes_and_approach_visible", "方案分析和会议纪要在同一长会议会话中可见");

  const downloads = await evaluate(page, () => {
    window.__longMeetingDownloads = [];
    if (!window.__originalLongMeetingAnchorClick) {
      window.__originalLongMeetingAnchorClick = HTMLAnchorElement.prototype.click;
    }
    HTMLAnchorElement.prototype.click = function patchedAnchorClick() {
      if (this.download) {
        window.__longMeetingDownloads.push({ href: this.href, download: this.download });
        return;
      }
      return window.__originalLongMeetingAnchorClick.call(this);
    };
    document.getElementById("btn-export-transcript").click();
    document.getElementById("btn-export-minutes").click();
    const result = window.__longMeetingDownloads.slice();
    HTMLAnchorElement.prototype.click = window.__originalLongMeetingAnchorClick;
    return result;
  });
  assert(downloads.length === 2, `expected transcript and minutes downloads: ${JSON.stringify(downloads)}`);
  assert(downloads[0].download.endsWith(".transcript.txt"), `bad transcript download: ${JSON.stringify(downloads)}`);
  assert(downloads[1].download.endsWith(".minutes.md"), `bad minutes download: ${JSON.stringify(downloads)}`);
  await captureStep(page, "exports_verified", "长会议文字稿和纪要导出目标已验证");

  await evaluate(page, () => {
    window.confirm = () => true;
    document.getElementById("btn-delete").click();
  });
  await waitForCdpExpression(page, `document.getElementById('session-meta').innerText.includes('准备开始')`, 15000);
  const deleteProbe = await fetch(`http://127.0.0.1:${port}/live/asr/sessions/${sessionId}/events`);
  const finalState = await evaluate(page, () => ({
    currentSession,
    utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
    suggestion_card_count: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approach_card_count: document.querySelectorAll("[data-card-kind='approach']").length,
    minutes_text: document.getElementById("minutes-panel")?.innerText || "",
    session_meta: document.getElementById("session-meta")?.innerText || "",
  }));
  assert(deleteProbe.status === 404, `expected deleted session API 404, got ${deleteProbe.status}`);
  assert(finalState.currentSession === null, `expected currentSession reset: ${JSON.stringify(finalState)}`);
  assert(finalState.utterances === 0, `expected no utterances after delete: ${JSON.stringify(finalState)}`);
  await captureStep(page, "delete_reset", "删除长会议后 UI 重置且后端会话记录已删除");

  const report = {
    status: "go_long_meeting_ui_evidence",
    session_id: sessionId,
    synthetic_duration_minutes: 20,
    counts_as_20_60_min_production_soak: false,
    counts_as_real_mic_go_evidence: false,
    remote_llm_called: false,
    remote_asr_called: false,
    loaded_state: loadedState,
    original_clickback: originalClickback,
    revision_clickback: revisionClickback,
    rendered_panels: renderedPanels,
    downloads,
    delete_probe_status: deleteProbe.status,
    final_state: finalState,
    checklist,
    screenshots,
    screenshot_count: screenshots.length,
    limitations: [
      "fixture_only_no_audio_runtime",
      "not_real_microphone_evidence",
      "not_20_60_min_wall_clock_soak",
      "not_paid_or_production_llm_evidence",
    ],
  };
  await writeFile(path.join(artifactRoot, "long_meeting_ui_report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} catch (err) {
  failed = true;
  if (page) {
    try { await captureStep(page, "failure_state", "失败现场截图"); } catch {}
  }
  const state = page ? await safeReadPageState(page).catch((stateErr) => ({ state_error: stateErr.message })) : {};
  const report = {
    status: "blocked_long_meeting_ui_evidence",
    session_id: sessionId,
    error: err.message,
    page_state: state,
    checklist,
    screenshots,
    server_stdout_tail: tailText(serverStdout, 4000),
    server_stderr_tail: tailText(serverStderr, 4000),
    remote_llm_called: false,
    remote_asr_called: false,
  };
  await mkdir(artifactRoot, { recursive: true });
  await writeFile(path.join(artifactRoot, "long_meeting_ui_error.json"), JSON.stringify(report, null, 2));
  console.error(JSON.stringify(report, null, 2));
} finally {
  for (const s of cdpSockets) { try { s.close(); } catch {} }
  for (const p of processes) { try { p.kill("SIGTERM"); } catch {} }
  await removeTempDirWithRetry(dataDir);
  await removeTempDirWithRetry(chromeUserDataDir);
  process.exit(failed ? 1 : 0);
}

function fixtureRecord() {
  const finals = [
    [0, "今天主要评审推荐服务灰度发布，先确认范围、指标和回滚条件。"],
    [90000, "推荐服务第一阶段只放量百分之五，观察 P99 延迟和错误率。"],
    [180000, "张三负责在发布前补齐 checkout-service 的压测报告和回归用例。"],
    [300000, "如果 P99 超过九百毫秒或者错误率超过零点一，就立即暂停灰度。"],
    [420000, "李四需要确认 Redis 缓存穿透保护和幂等逻辑已经上线。"],
    [540000, "Kafka lag 如果持续超过三分钟，需要先回滚消费策略再继续发布。"],
    [660000, "这里先恢度到百分之十，但是回滚 owner 还没有完全确认。"],
    [780000, "王五补充移动端兼容性用例，今天下班前给出结果。"],
    [900000, "支付链路只读开关需要保持开启，避免推荐服务影响下单路径。"],
    [1020000, "发布观察窗口暂定三十分钟，SLO 面板需要展示实时错误预算。"],
    [1110000, "如果夜间值班没有确认 owner，就不要自动扩大灰度比例。"],
    [1190000, "结论是先小流量验证，明天根据指标再决定是否扩大到百分之二十。"],
  ];
  const events = [];
  const evidenceBySegment = new Map();
  let sequence = 1;

  for (const [index, [startMs, text]] of finals.entries()) {
    const segmentId = `long_seg_${String(index + 1).padStart(2, "0")}`;
    const evidence = {
      id: `asr_ev_${segmentId}`,
      segment_id: segmentId,
      start_ms: startMs,
      end_ms: startMs + 5200,
      quote: text,
      status: "active",
    };
    evidenceBySegment.set(segmentId, evidence);
    events.push({
      id: `transcript_final:${segmentId}`,
      event_type: "transcript_final",
      at_ms: startMs + 5600,
      source: "live_asr_stream",
      trace_kind: "live_event",
      sequence: sequence++,
      payload: {
        segment_id: segmentId,
        start_ms: startMs,
        end_ms: startMs + 5200,
        text,
        normalized_text: text,
        confidence: 0.86,
        is_final: true,
        evidence_spans: [evidence],
      },
    });
    if ([2, 3, 4, 6, 8, 11].includes(index + 1)) {
      const state = stateForSegment(segmentId, text, evidence.id);
      events.push(state.stateEvent(sequence++));
      events.push(state.candidateEvent(sequence++));
      events.push(state.requestDraftEvent(sequence++));
    }
  }

  const originalEvidence = evidenceBySegment.get(revisionSourceSegmentId);
  const revisionEvidence = {
    id: revisionEvidenceId,
    segment_id: revisionSegmentId,
    revision_of: originalEvidence.id,
    start_ms: originalEvidence.start_ms,
    end_ms: originalEvidence.end_ms + 800,
    quote: "这里先灰度到百分之十，但是回滚 owner 还没有完全确认。",
    status: "active",
  };
  const supersededEvidence = {
    ...originalEvidence,
    status: "superseded",
    replaced_by: revisionEvidenceId,
  };
  events.push({
    id: `transcript_revision:${revisionSegmentId}`,
    event_type: "transcript_revision",
    at_ms: 672000,
    source: "live_asr_stream",
    trace_kind: "live_event",
    sequence: sequence++,
    payload: {
      segment_id: revisionSegmentId,
      start_ms: revisionEvidence.start_ms,
      end_ms: revisionEvidence.end_ms,
      text: revisionEvidence.quote,
      normalized_text: revisionEvidence.quote,
      confidence: 0.91,
      is_final: true,
      evidence_spans: [revisionEvidence],
      revision_of: revisionSourceSegmentId,
      supersedes_segment_id: revisionSourceSegmentId,
      superseded_evidence_spans: [supersededEvidence],
    },
  });
  events.push({
    id: "evaluation:long_meeting_summary",
    event_type: "evaluation_summary",
    at_ms: syntheticDurationMinutes * 60 * 1000,
    source: "live_asr_stream",
    trace_kind: "live_event",
    sequence: sequence++,
    payload: {
      source: "live_asr_stream",
      provider: "fixture_long_meeting",
      is_mock: false,
      passes_minimum_gate: true,
      partial_event_count: 0,
      final_event_count: finals.length,
      revision_event_count: 1,
      suggestion_card_count: 4,
      approach_card_count: 2,
      duration_minutes: syntheticDurationMinutes,
      counts_as_20_60_min_production_soak: false,
      remote_llm_called: false,
      remote_asr_called: false,
    },
  });

  return {
    session_id: sessionId,
    provider: "fixture_long_meeting",
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
      matched_entities: ["P99", "Redis", "Kafka", "SLO", "checkout-service", "灰度", "回滚"],
      matched_entity_groups: ["latency", "cache", "messaging", "release"],
      missing_entity_groups: [],
      technical_entity_hit_count: 7,
      technical_group_hit_count: 4,
      gibberish_score: 0,
      reason: "fixture_long_meeting_ui_evidence",
    },
    auto_suggestion: { paused: true, updated_at_ms: syntheticDurationMinutes * 60 * 1000 },
    events,
    suggestion_cards: suggestionCards(evidenceBySegment, revisionEvidence),
    approach_cards: approachCards(evidenceBySegment),
    minutes: {
      minutes_md: [
        "# 会议纪要",
        "",
        "## 决策",
        "- 推荐服务先按 5% 小流量灰度，观察 P99、错误率和 Kafka lag。",
        "- 如果 P99 超过 900ms 或错误率超过 0.1%，立即暂停灰度并回滚。",
        "",
        "## 待办",
        "- 张三补齐 checkout-service 压测报告和回归用例。",
        "- 王五补充移动端兼容性用例。",
        "",
        "## 风险",
        "- 回滚 owner 和夜间值班确认仍未闭环。",
        "- Redis 缓存穿透保护、幂等逻辑和 SLO 面板需要发布前复核。",
      ].join("\n"),
    },
  };
}

function stateForSegment(segmentId, text, evidenceId) {
  const eventId = `asr_state_event_${segmentId}`;
  const targetType = text.includes("负责") || text.includes("补充") ? "ActionItem" : "Risk";
  const targetId = `asr_${targetType.toLowerCase()}_${segmentId}`;
  return {
    stateEvent(sequence) {
      return {
        id: `state:${eventId}`,
        event_type: "state_event",
        at_ms: 0,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence,
        payload: {
          event_id: eventId,
          target_type: targetType,
          target_id: targetId,
          state_event_type: "created",
          evidence_span_ids: [evidenceId],
          state_item: {
            id: targetId,
            description: text,
            status: "open",
            evidence_span_ids: [evidenceId],
            source: "live_asr_stream",
            state_origin: "fixture_long_meeting",
          },
        },
      };
    },
    candidateEvent(sequence) {
      return {
        id: `suggestion_candidate:${eventId}`,
        event_type: "suggestion_candidate_event",
        at_ms: 0,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence,
        payload: {
          candidate_id: `asr_suggestion_candidate_${eventId}`,
          target_type: targetType,
          target_id: targetId,
          gap_rule_id: targetType === "ActionItem" ? "action.owner.deadline" : "risk.rollback.validation",
          suggested_prompt: targetType === "ActionItem" ? "确认 owner、截止时间和验收标准。" : "确认阈值、回滚动作和监控 owner。",
          source_event_ids: [eventId],
          evidence_span_ids: [evidenceId],
          segment_batch: [segmentId],
          llm_call_status: "not_called",
          card_status: "not_created",
          confidence: 0.78,
          confidence_level: "medium",
          degradation_reasons: [],
          source: "live_asr_stream",
        },
      };
    },
    requestDraftEvent(sequence) {
      return {
        id: `llm_request_draft:${eventId}`,
        event_type: "llm_request_draft_event",
        at_ms: 0,
        source: "live_asr_stream",
        trace_kind: "live_event",
        sequence,
        payload: {
          request_id: `asr_llm_request_draft_${eventId}`,
          request_type: "llm_suggestion_card_draft",
          target_candidate_id: `asr_suggestion_candidate_${eventId}`,
          target_type: targetType,
          target_id: targetId,
          gap_rule_id: targetType === "ActionItem" ? "action.owner.deadline" : "risk.rollback.validation",
          prompt_version: "not-called",
          model: "not-called",
          llm_call_status: "not_called",
          card_status: "not_created",
          schema_status: "not_generated",
          suggested_prompt: targetType === "ActionItem" ? "确认 owner、截止时间和验收标准。" : "确认阈值、回滚动作和监控 owner。",
          input_summary: `${targetType} ${targetId} from ${segmentId} using ${evidenceId}`,
          source_event_ids: [eventId],
          evidence_span_ids: [evidenceId],
          segment_batch: [segmentId],
          candidate_confidence: 0.78,
          candidate_confidence_level: "medium",
          candidate_degradation_reasons: [],
          request_origin: "fixture_long_meeting",
          source: "live_asr_stream",
        },
      };
    },
  };
}

function suggestionCards(evidenceBySegment, revisionEvidence) {
  const cardSpecs = [
    ["card-risk-threshold", "建议把 P99 900ms、错误率 0.1% 和 Kafka lag 三分钟写成发布门禁。", "阈值和回滚条件需要现场确认", ["long_seg_03", "long_seg_04"]],
    ["card-owner-action", "建议明确张三、李四、王五各自交付物和截止时间。", "多个待办已经出现，但验收标准还需固化", ["long_seg_03", "long_seg_05", "long_seg_08"]],
    ["card-revision-rollback-owner", "修正后仍显示回滚 owner 未闭环，建议在扩大灰度前指定值班负责人。", "END 后修正补齐了灰度语义，但 owner 风险仍存在", [revisionSegmentId]],
    ["card-slo-dashboard", "建议发布前确认 SLO 面板、错误预算和支付只读开关。", "观测面和隔离开关是本次发布的保护条件", ["long_seg_09", "long_seg_10"]],
  ];
  return cardSpecs.map(([cardId, suggestionText, triggerReason, segmentIds], index) => {
    const spans = segmentIds.map((segmentId) => segmentId === revisionSegmentId ? revisionEvidence : evidenceBySegment.get(segmentId)).filter(Boolean);
    return {
      card_id: cardId,
      card_type: "meeting.suggestion",
      suggestion_text: suggestionText,
      confidence: 0.82 + index * 0.02,
      trigger_reason: triggerReason,
      evidence_span_ids: spans.map((span) => span.id),
      evidence_spans: spans,
      source_event_ids: spans.map((span) => `transcript:${span.segment_id}`),
    };
  });
}

function approachCards(evidenceBySegment) {
  return [
    {
      card_id: "approach-small-gray",
      card_type: "approach.consideration",
      suggestion_text: "小流量灰度可以降低发布风险，但必须绑定明确回滚门禁和观察窗口。",
      confidence: 0.88,
      trigger_reason: "灰度比例和观察窗口已讨论",
      evidence_span_ids: [evidenceBySegment.get("long_seg_02").id, evidenceBySegment.get("long_seg_10").id],
      evidence_spans: [evidenceBySegment.get("long_seg_02"), evidenceBySegment.get("long_seg_10")],
      evidence_quote: "先放量 5%，观察 P99 延迟、错误率和 SLO 面板。",
    },
    {
      card_id: "approach-owner-risk",
      card_type: "approach.risk",
      suggestion_text: "如果夜间值班 owner 未确认，就不要自动扩大灰度比例。",
      confidence: 0.9,
      trigger_reason: "责任人缺口影响自动扩量",
      evidence_span_ids: [evidenceBySegment.get("long_seg_11").id],
      evidence_spans: [evidenceBySegment.get("long_seg_11")],
      evidence_quote: "如果夜间值班没有确认 owner，就不要自动扩大灰度比例。",
    },
  ];
}

async function writeFixtureSession(rootDir) {
  const recordDir = path.join(rootDir, "live_asr_sessions");
  await mkdir(recordDir, { recursive: true });
  const record = fixtureRecord();
  await writeFile(path.join(recordDir, `${sessionId}.json`), JSON.stringify(record, null, 2));
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
  if (result.exceptionDetails) {
    const detail = result.exceptionDetails.exception?.description
      || result.exceptionDetails.exception?.value
      || result.exceptionDetails.text
      || "browser eval failed";
    throw new Error(`browser eval failed: ${detail}`);
  }
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
    const focusedItems = Array.from(document.querySelectorAll(".transcript-segment.evidence-focus"));
    const focused = focusedItems.find((item) => item.dataset.segmentId === segmentId || item.dataset.sourceSegmentId === segmentId) || focusedItems[0] || null;
    return {
      focused_segment_id: focused?.dataset.segmentId || "",
      focused_source_segment_id: focused?.dataset.sourceSegmentId || "",
      focused_evidence_ids: (focused?.dataset.evidenceIds || "").split(/[，,\s]+/).filter(Boolean),
      focused_text: focused?.innerText || "",
      original_details_open: Boolean(focused?.parentElement?.querySelector("details.original-asr-text")?.open),
      is_revision: focused?.dataset.status === "corrected" || Boolean(focused?.dataset.revisionOf),
    };
  }, preferredSegmentId);
}

async function safeReadPageState(cdpPage) {
  return evaluate(cdpPage, () => ({
    title: document.title,
    sessionMeta: document.getElementById("session-meta")?.innerText,
    sysStatus: document.getElementById("sys-status")?.innerText,
    sourceBadge: document.getElementById("source-badge")?.innerText,
    transcript: document.getElementById("transcript-stream")?.innerText,
    suggestions: document.getElementById("suggestions-panel")?.innerText,
    approach: document.getElementById("approach-panel")?.innerText,
    minutes: document.getElementById("minutes-panel")?.innerText,
    utterances: document.querySelectorAll(".transcript-segment[data-transcript-segment-id]").length,
    revisionUtterances: document.querySelectorAll('.transcript-segment[data-status="corrected"]').length,
    suggestionCards: document.querySelectorAll("[data-card-kind='suggestion']").length,
    approachCards: document.querySelectorAll("[data-card-kind='approach']").length,
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

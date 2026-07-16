"""Phase 3 workbench frontend tests."""
import json
from pathlib import Path
import re
from fastapi.testclient import TestClient
from meeting_copilot_web_mvp.app import create_app


def test_workbench_route_serves_html():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    assert "会议助手" in r.text
    assert "实时文字" in r.text
    assert "/static/workbench.js?v=" in r.text
    assert "loadWorkbenchScript" in r.text
    assert "window.location.protocol === \"tauri:\"" in r.text
    assert 'script.src = isTauri ? "workbench.js?v=20260714-mainline-status2" : "/static/workbench.js?v=20260714-mainline-status2"' in r.text
    assert "inlineDesktopFrontendProbe" in r.text
    assert "runtime_write_frontend_probe" in r.text


def test_workbench_uses_product_mainline_language_instead_of_debug_terms():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    html = r.text
    for expected in ["会议助手", "开始会议", "结束会议", "导入录音", "实时文字", "实时提醒", "AI 建议", "会议纪要", "历史记录"]:
        assert expected in html
    for debug_label in ["真实录音", "实时订阅", "缺口卡", "方案考量卡", "events.sse", "sherpa sidecar"]:
        assert debug_label not in html


def test_workbench_recording_phase_exposes_only_one_end_meeting_control():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy").text
    js = client.get("/static/workbench.js").text

    assert ".btn[hidden]{display:none!important}" in html
    recording_branch = js[js.index('if (phase === "recording")'):]
    recording_branch = recording_branch[: recording_branch.index('} else if (phase === "processing")')]
    assert 'recordButton.textContent = "结束会议"' not in recording_branch
    assert 'stopButton.textContent = "结束会议"' in recording_branch


def test_root_route_serves_current_workbench_not_legacy_dashboard():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "会议助手" in r.text
    assert "/workbench-assets/" in r.text
    assert "/static/workbench.js" not in r.text
    assert "/static/app.js" not in r.text
    assert "Local Shadow Preview" not in r.text


def test_workbench_js_served():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    assert "live/asr/mock/sessions" in r.text
    assert "approach-cards" in r.text
    assert "minutes" in r.text
    assert "loadSessionHistory" in r.text


def test_workbench_supports_explicit_no_cost_derivation_selftest_without_changing_production_default():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function isNoCostDerivationSelfTest" in js
    assert "noCostDerivationSelfTest" in js
    assert 'mode: "deterministic_demo"' in js

    request_body = js[js.index("function enabledLlmRequestBody"):]
    request_body = request_body[: request_body.index("function derivationBasePath")]
    assert "isNoCostDerivationSelfTest()" in request_body
    assert 'return { mode: "enabled" }' in request_body

    derivation_base = js[js.index("function derivationBasePath"):]
    derivation_base = derivation_base[: derivation_base.index("function syncAutoSuggestionControl")]
    assert "isNoCostDerivationSelfTest()" in derivation_base
    assert '"/live/asr/demo/sessions"' in derivation_base
    assert '"/live/asr/sessions"' in derivation_base


def test_workbench_runs_startup_audio_check_and_explains_provider_readiness():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "async function loadAudioCheck" in js
    assert 'api("/audio/check")' in js
    assert 'api("/providers/health")' in js
    assert "providerHealth?.llm?.configured" in js
    assert "providerHealth.llm.provider" not in js
    assert "providerHealth.llm.model" not in js
    assert "credential_configured" not in js
    assert "api_key" not in js
    assert "麦克风" in js
    assert "实时识别" in js
    assert "AI 分析" in js
    assert "AI 分析暂不可用" in js
    assert "loadAudioCheck();" in js
    assert "realtime_asr_available" in js
    assert "file_asr_available" in js
    assert "实时识别不可用" in js
    assert "updateRecordButtonReadiness" in js
    assert "currentReadiness.realtime_asr_available" in js
    assert "实时识别不可用" in js
    assert "async function bootstrapWorkbench" in js
    assert "await initDesktopRuntimeProbe();" in js
    assert 'initDesktopRuntimeProbe();\nsetMeetingPhase("idle");\nloadAudioCheck();' not in js
    assert "await loadAudioCheck();" in js
    assert "bootstrapWorkbench();" in js


def test_workbench_history_labels_demo_degraded_and_live_mic_sessions():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function sessionSourceInfo" in js
    assert "演示会议" in js
    assert "真实麦克风" in js
    assert "降级" in js
    assert "未识别到有效语音" in js
    assert "history-source" in js
    assert "history-warning" in js
    source_info = js[js.index("function sessionSourceInfo"):]
    source_info = source_info[: source_info.index("function sessionSourceLabel")]
    assert source_info.index("fallbackUsed") < source_info.index("if (isMock)")
    assert "非真实识别" in source_info


def test_workbench_ws_close_waits_for_snapshot_before_success_state():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    close_handler = js[js.index("_micWs.onclose = async () =>"):]
    close_handler = close_handler[: close_handler.index("};", close_handler.index("await refreshRecordedSession")) + 2]
    assert 'setMeetingPhase("processing")' in close_handler
    assert 'setRecState("live")' not in close_handler


def test_workbench_refresh_button_avoids_replay_sse_client():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    assert "async function refreshLiveText" in r.text
    assert "new EventSource" not in r.text
    assert "subscribeLive" not in r.text
    assert "addEventListener(eventType" not in r.text


def test_workbench_refresh_live_text_uses_stable_snapshot_not_missing_sse_endpoint():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "async function refreshLiveText" in js
    assert "subscribeLive(currentSession)" not in js
    assert "EventSource(`/live/asr/sessions/${sid}/events.sse`)" not in js
    assert "实时文字正在自动更新" in js
    assert "`/live/asr/sessions/${sid}/events`" in js


def test_workbench_has_stable_copilot_panels_outside_transcript_stream():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="transcript-stream"' in html.text
    assert 'id="suggestions-panel"' in html.text
    assert 'id="approach-panel"' in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert 'const stream = $("transcript-stream")' in text
    assert 'const panel = $("suggestions-panel")' in text
    assert 'const panel = $("approach-panel")' in text
    suggestion_renderer = text[text.index("function renderSuggestionCards"):]
    suggestion_renderer = suggestion_renderer[: suggestion_renderer.index("function renderApproachCardList")]
    assert '$("stream")' not in suggestion_renderer
    approach_renderer = text[text.index("function renderApproachCardList"):]
    approach_renderer = approach_renderer[: approach_renderer.index("function renderMinutes")]
    assert '$("stream")' not in approach_renderer


def test_workbench_prioritizes_real_meeting_actions_and_downshifts_demo():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    html = r.text
    assert 'id="primary-actions"' in html
    assert 'id="session-actions"' in html
    assert 'id="secondary-actions"' in html
    assert 'id="demo-actions"' in html
    primary = html[html.index('<div class="actions" id="primary-actions"'):]
    primary = primary[: primary.index("</div>")]
    for element_id in ["btn-record", "btn-stop", "btn-pause", "btn-upload-label", "btn-history"]:
        assert f'id="{element_id}"' in primary
    for element_id in ["btn-organize", "btn-delete", "btn-load", "btn-cards", "btn-approach", "btn-minutes", "btn-live"]:
        assert f'id="{element_id}"' not in primary
    assert primary.count("<button") + primary.count("<label") == 5
    session_tools = html[html.index('id="review-workspace"'):]
    session_tools = session_tools[: session_tools.index("</section>")]
    assert 'hidden' in session_tools.split(">", 1)[0]
    assert 'id="btn-organize"' in session_tools
    assert 'id="btn-delete"' in session_tools
    assert html.index('id="demo-actions"') < html.index('id="btn-load"')


def test_workbench_html_has_unique_ids_valid_modal_placement_and_ordered_assets():
    client = TestClient(create_app())
    response = client.get("/workbench-legacy")
    assert response.status_code == 200
    html = response.text

    ids = re.findall(r'\bid="([^"]+)"', html)
    duplicates = sorted({element_id for element_id in ids if ids.count(element_id) > 1})
    assert duplicates == []
    assert html.index('id="history-modal"') < html.index("</body>")
    assert html.index('id="settings-modal"') < html.index("</body>")
    assert "workbench-enhancements.js" not in html
    assert 'settingsScript.src = isTauri ? "settings-panel.js" : "/static/settings-panel.js"' in html
    assert html.index("script.onload = function") < html.index("document.body.appendChild(script)")


def test_workbench_main_script_owns_audio_preflight_stop_and_pause_state():
    client = TestClient(create_app())
    response = client.get("/static/workbench.js")
    assert response.status_code == 200
    js = response.text

    assert "async function checkAudioDevice" in js
    assert "async function stopMeetingRecording" in js
    stop_handler = js[js.index('$("btn-stop").addEventListener("click"'):]
    stop_handler = stop_handler[: stop_handler.index("function togglePauseRecording")]
    assert "stopMeetingRecording()" in stop_handler
    assert '$("btn-record").click()' not in stop_handler

    audio_handler = js[js.index("_micNode.onaudioprocess ="):]
    audio_handler = audio_handler[: audio_handler.index("};", 1) + 2]
    assert "if (_recordingPaused) return" in audio_handler
    pause_handler = js[js.index("function togglePauseRecording"):]
    pause_handler = pause_handler[: pause_handler.index('$("btn-pause")')]
    assert "_micNode.disconnect()" not in pause_handler
    assert "_micSource.connect(_micNode)" not in pause_handler


def test_workbench_reconnect_queues_pcm_frames_and_flushes_them_in_order():
    client = TestClient(create_app())
    response = client.get("/static/workbench.js")
    assert response.status_code == 200
    js = response.text

    assert "MAX_UNSENT_MIC_FRAMES" in js
    assert "let _micUnsentFrames = []" in js
    assert "function queueOrSendMicFrame" in js
    assert "function flushQueuedMicFrames" in js
    queue_function = js[js.index("function queueOrSendMicFrame"):]
    queue_function = queue_function[: queue_function.index("function flushQueuedMicFrames")]
    assert "_micUnsentFrames.push" in queue_function
    assert "_micUnsentFrames.length >= MAX_UNSENT_MIC_FRAMES" in queue_function
    assert "部分音频未能保留" in queue_function

    frame_sender = js[js.index("function sendPcm16kFrame"):]
    frame_sender = frame_sender[: frame_sender.index("function flushPendingPcm")]
    assert "queueOrSendMicFrame(frame)" in frame_sender
    assert "_micWs.send(frame.buffer)" not in frame_sender

    socket_open = js[js.index("_micWs.onopen ="):]
    socket_open = socket_open[: socket_open.index("_micWs.onerror =")]
    assert "flushQueuedMicFrames()" not in socket_open
    ready_handler = js[js.index('if (ev.event_type === "asr_ready")'):]
    ready_handler = ready_handler[: ready_handler.index("currentEvents.push(ev)")]
    assert "flushQueuedMicFrames()" in ready_handler


def test_workbench_stop_during_reconnect_flushes_audio_before_end():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let _stopRequestedAfterReconnect = false" in js
    stop_function = js[js.index("async function stopMeetingRecording"):]
    stop_function = stop_function[: stop_function.index('$("btn-record").addEventListener("click"')]
    assert "_stopRequestedAfterReconnect = true" in stop_function
    assert "flushPendingPcm()" in stop_function
    assert "const waitingForSocket = !openSocket && Boolean(_micWs || _reconnecting)" in stop_function
    reconnect_stop_branch = stop_function[stop_function.index("if (waitingForSocket)"):]
    assert "stopAudioCapture()" in reconnect_stop_branch
    assert "return true" in reconnect_stop_branch

    socket_open = js[js.index("_micWs.onopen ="):]
    socket_open = socket_open[: socket_open.index("_micWs.onerror =")]
    assert "if (_stopRequestedAfterReconnect)" in socket_open
    assert '_micWs.send("END")' not in socket_open
    finish = js[js.index("function finishMicAfterAsrReady") :]
    finish = finish[: finish.index("function connectMicWs")]
    assert finish.index("flushQueuedMicFrames()") < finish.index('_micWs.send("END")')


def test_workbench_settings_panel_persists_only_non_secret_preferences():
    client = TestClient(create_app())
    html_response = client.get("/workbench-legacy")
    script_response = client.get("/static/settings-panel.js")
    assert html_response.status_code == 200
    assert script_response.status_code == 200
    html = html_response.text
    js = script_response.text

    assert 'id="setting-llm-api-key"' not in html
    assert 'id="setting-llm-base-url"' not in html
    assert "apiKey" not in js
    assert "baseUrl" not in js
    assert "localStorage" not in js
    assert 'fetchJson("/settings")' in js
    assert 'method: "PATCH"' in js
    assert "function initializeSettingsPanel" in js
    assert 'document.readyState === "loading"' in js
    assert 'settingsBtn.id = "btn-settings"' in js


def test_workbench_productized_layout_has_session_tools_and_mobile_guardrails():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    html = r.text
    assert "@media (max-width:900px)" in html
    assert ".session-tools[hidden]" in html
    assert ".topbar" in html
    assert "grid-template-areas" in html
    assert "会议操作" in html
    assert "实时提醒" in html
    assert 'id="review-workspace"' in html
    assert "会议纪要" in html
    assert "数据与隐私" in html


def test_workbench_uses_focused_two_column_meeting_layout():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    text = html.text

    assert 'grid-template-areas:"topbar topbar" "center right" "status status"' in text
    assert '<aside class="left"' not in text
    assert 'id="meeting-status-strip"' in text
    assert 'id="realtime-guidance-panel"' in text
    assert text.index('id="candidate-panel"') < text.index('id="suggestions-panel"')
    assert "实时提醒" in text
    assert "AI 建议" in text
    assert "实时建议" not in text


def test_workbench_mobile_layout_keeps_transcript_before_ai_rail():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    html = r.text
    mobile_css = html[html.index("@media (max-width:900px)"):]
    mobile_css = mobile_css[: mobile_css.index("</style>")]

    assert 'grid-template-areas:"topbar" "center" "right" "status"' in mobile_css
    assert 'grid-template-areas:"topbar" "right" "center" "status"' not in mobile_css


def test_workbench_top_status_strip_explains_meeting_mainline_status():
    client = TestClient(create_app())
    r = client.get("/workbench-legacy")
    assert r.status_code == 200
    html = r.text
    strip = html[html.index('id="meeting-status-strip"'):]
    strip = strip[: strip.index("</div>")]

    assert 'aria-label="本场会议状态与快捷导航"' in strip
    assert 'id="c-cockpit-stage"' in strip
    assert "待开始" in strip
    for element_id in ["c-transcript", "c-gap", "c-cards", "c-approach", "c-audio", "c-minutes"]:
        assert f'id="{element_id}"' in strip
    for label in ["文字记录", "实时提醒", "AI 建议", "方案分析", "录音保存", "会后复盘"]:
        assert label in strip

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function syncMeetingOverview" in text
    assert "function visibleTranscriptCount" in text
    assert '$("c-transcript")' in text
    assert '$("c-cards")' in text
    assert '$("c-audio")' in text
    assert '$("c-minutes")' in text
    assert "currentSessionHasAudio()" in text
    assert "currentMinutes ? \"已生成\" : \"未生成\"" in text


def test_workbench_meeting_cockpit_stage_tracks_mainline_phase():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function meetingCockpitStage" in js
    stage = js[js.index("function meetingCockpitStage"):]
    stage = stage[: stage.index("function syncMeetingOverview")]
    for label in ["待开始", "录音中", "整理中", "已复盘", "已记录"]:
        assert label in stage
    assert 'currentMeetingPhase === "recording"' in stage
    assert 'currentMeetingPhase === "processing"' in stage
    assert "currentMinutes" in stage
    assert "currentSessionHasTranscript()" in stage

    overview = js[js.index("function syncMeetingOverview"):]
    overview = overview[: overview.index("function enabledLlmRequestBody")]
    assert '$("c-cockpit-stage").textContent = stage.label' in overview
    assert '$("c-cockpit-stage").dataset.state = stage.state' in overview

    phase = js[js.index("function setMeetingPhase"):]
    phase = phase[: phase.index("function syncActionAvailability")]
    assert "syncMeetingOverview();" in phase


def test_workbench_overview_reminder_count_uses_unprocessed_deduped_projection_and_keeps_zero():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function currentReminderCount" in js
    assert "function projectedUnprocessedCandidateReminders" in js
    reminder_counter = js[js.index("function currentReminderCount"):]
    reminder_counter = reminder_counter[: reminder_counter.index("function numericCountOverride")]
    assert "projectedUnprocessedCandidateReminders().length" in reminder_counter
    assert "currentEvents" not in reminder_counter
    assert "visibleCandidateReminderCount()" not in reminder_counter
    assert "||" not in reminder_counter
    overview = js[js.index("function syncMeetingOverview"):]
    overview = overview[: overview.index("function enabledLlmRequestBody")]
    assert "currentReminderCount()" in overview
    assert 'Number($("s-candidates")?.textContent || 0)' not in overview


def test_workbench_overview_defaults_do_not_treat_null_as_zero():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    overview = js[js.index("function syncMeetingOverview"):]
    overview = overview[: overview.index("function enabledLlmRequestBody")]

    assert "function numericCountOverride" in js
    assert "numericCountOverride(transcriptCount)" in overview
    assert "numericCountOverride(reminderCount)" in overview
    assert "Number.isFinite(Number(transcriptCount))" not in overview
    assert "Number.isFinite(Number(reminderCount))" not in overview
    assert "visibleTranscriptCount()" in overview
    assert "currentReminderCount()" in overview


def test_workbench_js_shows_session_only_tools_after_session_exists():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function syncSessionToolVisibility" in js
    assert 'const hasSession = Boolean(currentSession)' in js
    assert '$("session-actions").hidden = !hasSession' in js
    assert '$("btn-stop").hidden = phase !== "recording" && phase !== "processing"' in js
    assert '$("btn-record").hidden = phase === "recording" || phase === "processing"' in js


def test_workbench_user_facing_error_states_are_clear():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    for expected in [
        "还没有开始会议",
        "没有检测到麦克风声音",
        "实时识别不可用",
        "AI 分析暂不可用",
        "本次会议已删除",
        "识别语义质量不足",
    ]:
        assert expected in text


def test_workbench_maps_asr_semantic_quality_blocker_to_user_message():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "ASR_SEMANTIC_QUALITY_MESSAGE" in text
    assert 'reasons.includes("asr_semantic_quality_blocked")' in text
    assert 'blockers.includes("asr_semantic_quality_blocked")' in text
    assert "声音可用，但没有听清关键业务内容" in text


def test_workbench_handles_quality_blocked_demo_derivation_without_success_copy():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text

    assert "function derivationBlockedMessage" in text
    assert "body.derivation_blocked" in text
    assert "识别语义质量不足" in text
    assert "先不生成正式建议" in text
    assert "会议整理完成，但识别质量不足" in text

    suggestions = text[text.index("async function generateSuggestionCards"):]
    suggestions = suggestions[: suggestions.index("async function generateApproachCards")]
    assert "body.derivation_blocked" in suggestions
    assert "renderRealCards([])" in suggestions
    assert "return { ok: false" in suggestions

    approach = text[text.index("async function generateApproachCards"):]
    approach = approach[: approach.index("async function generateMinutes")]
    assert "body.derivation_blocked" in approach
    assert "renderApproachCards([])" in approach
    assert "return { ok: false" in approach

    minutes = text[text.index("async function generateMinutes"):]
    minutes = minutes[: minutes.index("async function organizeCurrentSession")]
    assert "body.derivation_blocked" in minutes
    assert "derivationBlockedMessage(body)" in minutes


def test_workbench_does_not_hide_structured_api_errors_or_degraded_minutes():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function apiErrorMessage" in js
    assert "JSON.stringify(detail)" in js
    assert "body.degraded || !String(body.minutes_md" in js
    assert "会后复盘未生成" in js


def test_workbench_builds_absolute_websocket_url_in_normal_browser_mode():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "window.location.protocol === \"https:\" ? \"wss:\" : \"ws:\"" in js
    assert "window.location.host" in js
    assert "return path;" not in js[js.index("function apiWsUrl"):js.index("function sessionSourceInfo")]


def test_workbench_has_recording_export_buttons_and_download_handlers():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="btn-export-transcript"' in html.text
    assert 'id="btn-export-minutes"' in html.text
    assert 'id="btn-export-audio"' in html.text
    assert "导出文字稿" in html.text
    assert "导出纪要" in html.text
    assert "导出录音" in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert '$("btn-export-transcript").disabled = recording || cleanupPending || !hasTranscript' in text
    assert '$("btn-export-minutes").disabled = recording || cleanupPending || !currentMinutes' in text
    assert '$("btn-export-audio").disabled = recording || cleanupPending || !currentSessionHasAudio()' in text
    assert "link.href = apiUrl(url)" in text
    assert 'downloadSessionArtifact(`/live/asr/sessions/${currentSession}/transcript.txt`' in text
    assert 'downloadSessionArtifact(`/live/asr/sessions/${currentSession}/minutes.md`' in text
    assert 'downloadSessionArtifact(`/live/asr/sessions/${currentSession}/audio.wav`' in text


def test_workbench_has_source_badge_and_candidate_panel():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="source-badge"' in html.text
    assert 'id="candidate-panel"' in html.text
    assert "实时提醒" in html.text
    assert "实时建议" not in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function renderSourceBadge" in text
    assert "function renderCandidateReminders" in text
    assert 'const panel = $("candidate-panel")' in text
    assert "partial_hint_event" in text
    assert "实时提醒" in text


def test_workbench_candidate_reminders_do_not_render_inside_transcript_stream():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    renderer = js[js.index("function renderTranscriptAndCandidates"):]
    renderer = renderer[: renderer.index("function applySessionEvents")]
    candidate_scan = renderer[renderer.index("events.forEach((e) => {"): renderer.index("replaceCandidateReminderEvents")]
    assert "renderCandidateReminders" in renderer
    assert "candidate-panel" in js
    assert 'e.event_type === "suggestion_candidate_event"' in candidate_scan
    assert 'e.event_type === "partial_hint_event"' in candidate_scan
    assert "transcript-document" not in candidate_scan
    assert "transcript-active-tail" not in candidate_scan


def test_workbench_candidate_reminders_hide_engineering_evidence_ids():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function candidateReminderMetaLabel" in js
    renderer = js[js.index("function renderCandidateReminders"):]
    renderer = renderer[: renderer.index("function renderTranscriptAndCandidates")]
    assert "candidateReminderMetaLabel(e)" in renderer
    assert "evidence_span_ids || p.source_event_ids" not in renderer
    assert "source_event_ids || p.evidence_span_ids" not in renderer


def test_workbench_candidate_reminders_are_deduped_and_capped_for_long_meetings():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "const MAX_CANDIDATE_REMINDERS_VISIBLE" in js
    assert "function candidateReminderKey" in js
    assert "function visibleCandidateReminders" in js
    renderer = js[js.index("function renderCandidateReminders"):]
    renderer = renderer[: renderer.index("function renderTranscriptAndCandidates")]
    assert "const filteredCandidates = filteredCandidateReminders(candidates);" in renderer
    assert "const visibleCandidates = visibleCandidateReminders(filteredCandidates);" in renderer
    assert "visibleCandidates.forEach" in renderer
    assert "candidates.forEach" not in renderer
    assert "条较早或重复提醒已收起" in renderer


def test_workbench_candidate_reminders_use_three_item_priority_queue_and_latest_dedupe_content():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "const MAX_CANDIDATE_REMINDERS_VISIBLE = 3;" in js
    assert "const CANDIDATE_REMINDER_PRIORITY" in js
    priority = js[js.index("const CANDIDATE_REMINDER_PRIORITY") :]
    priority = priority[: priority.index("};")]
    assert priority.index("DecisionCandidate") < priority.index("ActionItem")
    assert priority.index("ActionItem") < priority.index("Risk")
    assert priority.index("Risk") < priority.index("OpenQuestion")

    key_helper = js[js.index("function candidateReminderSemanticKey") :]
    key_helper = key_helper[: key_helper.index("function visibleCandidateReminders")]
    assert "payload.dedupe_key || event.dedupe_key" in key_helper
    assert "function dedupedCandidateReminderProjection" in key_helper
    assert "function projectedUnprocessedCandidateReminders" in key_helper
    assert "candidateReminderProjectionCache?.version" in key_helper
    assert "dedupedCandidateReminderProjection(candidateReminderEvents)" in key_helper
    assert key_helper.index("dedupedCandidateReminderProjection(candidateReminderEvents)") < key_helper.index("candidateReminderProjectionCache =")

    queue = js[js.index("function visibleCandidateReminders") :]
    queue = queue[: queue.index("function renderCandidateReminders")]
    assert "const reminderByKey = new Map()" in queue
    assert "reminderByKey.set(key" in queue
    assert "existing.firstIndex" in queue
    assert ".sort(" in queue
    assert ".slice(0, MAX_CANDIDATE_REMINDERS_VISIBLE)" in queue


def test_workbench_meeting_focus_rows_are_actionable_candidate_filters():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    guidance = html.text[html.text.index('id="realtime-guidance-panel"'):]
    guidance = guidance[: guidance.index("</section>")]

    for target_type, label in [
        ("DecisionCandidate", "决定了什么"),
        ("ActionItem", "待办事项"),
        ("Risk", "风险提醒"),
        ("OpenQuestion", "待确认问题"),
    ]:
        assert '<button type="button" class="focus-filter"' in guidance
        assert f'data-focus-type="{target_type}"' in guidance
        assert f'aria-label="只看{label}"' in guidance
        assert label in guidance

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    for expected in [
        "let currentCandidateFocusType",
        "function candidateReminderFocusType",
        "function filteredCandidateReminders",
        "function setCandidateFocusFilter",
        "function clearCandidateFocusFilter",
        "function bindCandidateFocusFilters",
    ]:
        assert expected in text


def test_workbench_ai_rail_disables_empty_focus_filters_and_names_business_role():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    guidance = html.text[html.text.index('id="realtime-guidance-panel"'):]
    guidance = guidance[: guidance.index("</section>")]

    assert 'aria-label="筛选实时提醒"' in guidance
    assert "实时提醒" in guidance
    assert "AI 建议" in guidance
    for target_type in ["DecisionCandidate", "ActionItem", "Risk", "OpenQuestion"]:
        target_index = guidance.index(f'data-focus-type="{target_type}"')
        row_start = guidance.rfind("<button", 0, target_index)
        row = guidance[row_start:]
        row = row[: row.index("</button>")]
        assert "disabled" in row

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function focusTypeCountFromDom" in text
    assert "button.disabled = !active && count <= 0;" in text
    assert "暂无这类提醒" in text
    assert "syncCandidateFocusButtons(counts);" in text


def test_workbench_meeting_overview_rows_are_actionable_navigation():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    strip = html.text[html.text.index('id="meeting-status-strip"'):]
    strip = strip[: strip.index("</div>")]

    expected_targets = {
        "transcript": "查看实时文字",
        "reminders": "查看实时提醒",
        "suggestions": "查看 AI 建议",
        "approach": "查看方案分析",
        "audio": "查看录音保存",
        "minutes": "查看会后复盘",
    }
    for target, aria_label in expected_targets.items():
        assert f'data-overview-target="{target}"' in strip
        target_index = strip.index(f'data-overview-target="{target}"')
        row_start = strip.rfind("<button", 0, target_index)
        row = strip[row_start:]
        row = row[: row.index("</button>")]
        assert "overview-jump" in row
        assert f'aria-label="{aria_label}"' in row
        assert "disabled" not in row

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    for expected in [
        "function overviewTargetState",
        "function syncOverviewJumpButtons",
        "function jumpToOverviewTarget",
        "function bindMeetingOverviewJumps",
        '.overview-jump[data-overview-target]',
        "targetElement.scrollIntoView",
        "toast(state.emptyMessage)",
        "syncOverviewJumpButtons();",
        "bindMeetingOverviewJumps();",
    ]:
        assert expected in text


def test_workbench_overview_navigation_preserves_native_focusable_controls():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    text = r.text

    assert "function isNativeFocusableOverviewTarget" in text
    assert 'focusSelector: currentSessionHasAudio() ? "#btn-export-audio" : "#sys-status"' in text

    helper = text[text.index("function focusOverviewTargetElement"):]
    helper = helper[: helper.index("function jumpToOverviewTarget")]
    assert "const needsTemporaryTabIndex = !isNativeFocusableOverviewTarget(targetElement) && !targetElement.hasAttribute(\"tabindex\");" in helper
    assert 'if (needsTemporaryTabIndex) targetElement.setAttribute("tabindex", "-1");' in helper
    assert 'if (!targetElement.hasAttribute("tabindex")) targetElement.setAttribute("tabindex", "-1");' not in helper


def test_workbench_candidate_filter_shows_clear_state_without_changing_counts():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    renderer = js[js.index("function renderCandidateReminders"):]
    renderer = renderer[: renderer.index("function renderTranscriptAndCandidates")]
    assert "const filteredCandidates = filteredCandidateReminders(candidates);" in renderer
    assert "visibleCandidateReminders(filteredCandidates)" in renderer
    assert "正在查看" in renderer
    assert "显示全部" in renderer
    assert "clearCandidateFocusFilter()" in renderer
    assert "syncMeetingOverview({ reminderCount: candidates.length })" in renderer
    assert "syncMeetingOverview({ reminderCount: filteredCandidates.length })" not in renderer


def test_workbench_realtime_transcript_labels_stable_append_and_live_tail():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="transcript-mode-label"' in html.text
    assert "待开始" in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function setTranscriptModeLabel" in text
    assert 'setTranscriptModeLabel("已记录 + 正在听")' in text
    assert 'setTranscriptModeLabel("整理中")' in text
    assert 'setTranscriptModeLabel("已记录")' in text
    assert "正在听：" in text
    assert "function upsertLivePartial" in text
    assert "appendPartialDraftUtterance" not in text
    assert "已记录：" not in text
    assert "临时稿：" not in text


def test_workbench_replaces_provisional_reminder_when_final_candidate_arrives():
    js = TestClient(create_app()).get("/static/workbench.js").text
    key_helper = js[js.index("function candidateReminderSemanticKey") :]
    key_helper = key_helper[: key_helper.index("function dedupedCandidateReminderProjection")]

    assert "function candidateReminderSemanticKey" in key_helper
    assert "payload.segment_batch" in key_helper
    assert "candidateReminderSemanticKey(event)" in key_helper


def test_workbench_disabled_buttons_have_clear_visual_state():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    css = html.text[html.text.index("<style>"):]
    css = css[: css.index("</style>")]

    assert ".btn:disabled" in css
    assert "cursor:not-allowed" in css
    assert "opacity:.46" in css
    assert ".btn-primary:disabled" in css


def test_workbench_live_candidate_events_refresh_focus_counts_with_snapshot_path():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function candidateFocusCounts" in js
    assert "function syncCandidateFocusCounts" in js
    count_syncer = js[js.index("function syncCandidateFocusCounts"):]
    count_syncer = count_syncer[: count_syncer.index("function candidateReminderKey")]
    assert "projectedUnprocessedCandidateReminders(events)" in count_syncer
    assert "unprocessedCandidateReminders(" not in count_syncer
    snapshot_renderer = js[js.index("function renderTranscriptAndCandidates"):]
    snapshot_renderer = snapshot_renderer[: snapshot_renderer.index("function applySessionEvents")]
    assert "replaceCandidateReminderEvents(candidateEvents);" in snapshot_renderer
    assert "const activeCandidateEvents = projectedUnprocessedCandidateReminders();" in snapshot_renderer
    assert "syncCandidateFocusCounts();" in snapshot_renderer

    live_append = js[js.index("function appendLiveEvent"):]
    live_append = live_append[: live_append.index("function attachTranscriptEvidence")]
    candidate_branch = live_append[live_append.index('e.event_type === "suggestion_candidate_event"'):]
    assert "appendCandidateReminderEvent(e);" in candidate_branch
    assert "syncCandidateFocusCounts();" in candidate_branch
    assert "currentEvents.filter" not in candidate_branch
    assert '$("c-decision").textContent' not in candidate_branch
    assert '$("c-risk").textContent' not in candidate_branch


def test_workbench_has_auto_suggestion_status_and_pause_control():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="auto-suggestion-status"' in html.text
    assert 'id="btn-auto-suggestion-toggle"' in html.text
    assert "AI 建议" in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function renderAutoSuggestionStatus" in text
    assert "function toggleAutoSuggestion" in text
    for label in ["AI 建议运行中", "已暂停", "冷却中", "低质量已抑制", "AI 分析暂不可用"]:
        assert label in text


def test_workbench_auto_suggestion_uses_session_orchestrator_api_not_manual_button_click():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "async function loadAutoSuggestionStatus" in js
    assert "async function runAutoSuggestionsOnce" in js
    assert "`/live/asr/sessions/${sid}/auto-suggestions/status`" in js
    assert "`/live/asr/sessions/${sid}/auto-suggestions/run-once`" in js
    assert "`/live/asr/sessions/${sid}/auto-suggestions/status`" in js
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("async function generateSuggestionCards")]
    assert '$("btn-cards").click()' not in auto_runner
    assert "llm-execution-runs" not in auto_runner


def test_workbench_auto_suggestion_queues_one_pending_trigger_while_request_is_in_flight():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "let autoSuggestionPending = false" in js
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("async function toggleAutoSuggestion")]
    assert "if (autoSuggestionInFlight)" in auto_runner
    assert "autoSuggestionPending = true" in auto_runner
    assert "queueMicrotask" in auto_runner
    assert 'reason: "pending_trigger"' in auto_runner
    assert "} else {\n      autoSuggestionPending = false;" in auto_runner


def test_workbench_auto_suggestion_updates_left_ai_suggestion_count_state():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("async function toggleAutoSuggestion")]
    assert "mergeSuggestionCards(currentSuggestionCards, body.suggestion_cards || [])" in auto_runner
    assert "currentSuggestionCards = cards" in auto_runner
    assert "highlightCardIds: newCardIds" in auto_runner
    assert auto_runner.index("currentSuggestionCards = cards") < auto_runner.index("renderSuggestionCards(currentSuggestionCards")
    assert "renderSuggestionCards(body.suggestion_cards || [])" not in auto_runner


def test_workbench_live_status_uses_committed_transcript_rows_and_syncs_asr_ready():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert "function committedTranscriptCount" in js
    assert "currentEvents.length} 条实时文字" not in js
    assert '$("s-asr").textContent = "已就绪"' in js
    assert '$("s-asr").textContent = sourceLabel' in js


def test_workbench_realtime_correction_accumulates_revision_evidence():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert "realtimeCorrectionRevisionIds" in js
    assert "本批无需修改" in js
    assert "body.realtime_transcript_correction || null" in js


def test_workbench_shows_ai_analysis_in_progress_while_realtime_request_is_pending():
    js = TestClient(create_app()).get("/static/workbench.js").text
    auto_runner = js[js.index("async function runAutoSuggestionsOnce") :]
    auto_runner = auto_runner[: auto_runner.index("function applyRealtimeTranscriptRevisions")]

    assert "正在分析这段已确认文字" in auto_runner
    assert 'status: "running"' in auto_runner


def test_workbench_surfaces_actual_ai_and_post_meeting_errors_without_false_quality_downgrade():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert "function operationErrorMessage" in js
    assert "apiErrorMessage(error?.body || {}" in js
    assert "provider_error" in js

    auto_runner = js[js.index("async function runAutoSuggestionsOnce") :]
    auto_runner = auto_runner[: auto_runner.index("function applyRealtimeTranscriptRevisions")]
    assert 'operationErrorMessage(err, "AI 建议请求失败")' in auto_runner
    assert "renderSuggestionFailureState(`AI 建议请求失败：${message}`)" in auto_runner

    correction_runner = js[js.index("async function runRealtimeCorrectionsOnce") :]
    correction_runner = correction_runner[: correction_runner.index("async function toggleAutoSuggestion")]
    assert 'operationErrorMessage(err, "实时文字校正失败")' in correction_runner
    assert "实时文字校正失败" in correction_runner

    refresh_runner = js[js.index("async function refreshRecordedSession") :]
    refresh_runner = refresh_runner[: refresh_runner.index("async function refreshLiveText")]
    assert "会议已停止，但整理失败" in refresh_runner
    assert "没有识别到有效语音：${escapeHtml(err.message" not in refresh_runner


def test_workbench_empty_ai_panel_explains_when_realtime_suggestions_start():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert 'const SUGGESTIONS_EMPTY_MESSAGE = "会议开始并确认发言后，AI 建议会自动出现。";' in js

    html = TestClient(create_app()).get("/workbench-legacy").text
    assert "会议开始并确认发言后，AI 建议会自动出现。" in html
    assert "正在根据已确认发言分析。" not in html


def test_workbench_auto_suggestion_clears_stale_formal_results_after_acceptance_block():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("function applyRealtimeTranscriptRevisions")]

    assert 'body.reason === "acceptance_blocked"' in auto_runner
    assert 'currentSuggestionCards = []' in auto_runner
    assert 'currentApproachCards = []' in auto_runner
    assert 'currentMinutes = null' in auto_runner
    assert 'renderSuggestionCards([])' in auto_runner
    assert 'renderApproachCardList([])' in auto_runner
    assert 'renderMinutes("")' in auto_runner


def test_workbench_does_not_orchestrate_paid_ai_after_final_or_snapshot():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    apply_session = js[js.index("function applySessionEvents"):]
    apply_session = apply_session[: apply_session.index("function renderRealCards")]
    assert "loadAutoSuggestionStatus(sid)" in apply_session
    assert 'runAutoSuggestionsOnce({ reason: "session_snapshot" })' not in apply_session
    append_live = js[js.index("function appendLiveEvent"):]
    append_live = append_live[: append_live.index("function attachTranscriptEvidence")]
    assert "runRealtimeAiAfterFinal()" not in append_live
    assert 'runAutoSuggestionsOnce({ reason: "live_final" })' not in append_live
    assert "runRealtimeCorrectionsOnce" not in append_live


def test_workbench_partial_hint_does_not_call_formal_llm_before_a_persisted_final():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    append_live = js[js.index("function appendLiveEvent"):]
    append_live = append_live[: append_live.index("function attachTranscriptEvidence")]
    assert "shouldRunRealtimeAutoSuggestionFromHint" not in js
    assert 'runAutoSuggestionsOnce({ reason: "partial_hint" })' not in append_live
    assert "runRealtimeAiAfterFinal()" not in append_live


def test_workbench_delete_confirmation_includes_source_counts_and_scope():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function deleteConfirmationText" in js
    assert "会议来源" in js
    assert "文字条数" in js
    assert "AI 建议" in js
    assert "方案分析" in js
    assert "会议纪要" in js
    assert "当前页面不会删除你电脑上另存的原始音频文件" in js
    assert "confirm(deleteConfirmationText())" in js


def test_workbench_real_mic_source_badge_stays_pending_until_server_snapshot():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "pending_verification" in js
    assert "待确认" in js
    assert "服务端识别确认前" in js
    assert 'provider_mode: "pending"' in js
    start_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "startRecordingDraftSession(_recSid" in start_handler
    draft_starter = js[js.index("function startRecordingDraftSession"):]
    draft_starter = draft_starter[: draft_starter.index("function claimRecordingDraftView")]
    assert "pendingMicSource()" in draft_starter
    assert 'provider_mode: "real"' not in start_handler


def test_workbench_footer_separates_candidate_formal_and_approach_counts():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    assert html.status_code == 200
    assert 'id="s-candidates"' in html.text
    assert 'id="s-cards"' in html.text
    assert 'id="s-approach-cards"' in html.text
    assert "实时提醒" in html.text
    assert "AI 建议" in html.text

    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    transcript_renderer = text[text.index("function renderTranscriptAndCandidates"):]
    transcript_renderer = transcript_renderer[: transcript_renderer.index("function applySessionEvents")]
    assert "syncCandidateFocusCounts();" in transcript_renderer
    assert '$("s-cards").textContent = gapCount' not in transcript_renderer
    count_syncer = text[text.index("function syncCandidateFocusCounts"):]
    count_syncer = count_syncer[: count_syncer.index("function candidateReminderKey")]
    assert '$("s-candidates").textContent = String(candidates.length)' in count_syncer
    suggestion_renderer = text[text.index("function renderSuggestionCards"):]
    suggestion_renderer = suggestion_renderer[: suggestion_renderer.index("function renderApproachCardList")]
    assert '$("s-cards").textContent = String(uniqueCards.length)' in suggestion_renderer
    assert "currentApproachCards.length" not in suggestion_renderer
    assert "c-gap" not in suggestion_renderer
    approach_renderer = text[text.index("function renderApproachCardList"):]
    approach_renderer = approach_renderer[: approach_renderer.index("function renderMinutes")]
    assert '$("s-approach-cards").textContent = String(cards.length)' in approach_renderer
    assert '$("s-cards").textContent' not in approach_renderer


def test_workbench_formal_suggestions_render_evidence_quotes_and_clickback():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    assert "function focusEvidenceSpan" in text
    assert "card.evidence_spans" in text
    assert "data-segment-id" in text
    assert "evidence-link" in text
    assert "evidence-focus" in text


def test_workbench_revision_replaces_target_row_and_exposes_original_text():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    html = client.get("/workbench-legacy")
    assert js.status_code == 200
    assert html.status_code == 200
    text = js.text

    assert "function upsertCommittedTranscript" in text
    assert "function buildCommittedTranscriptRow" in text
    assert "data-transcript-segment-id" in text
    assert "existing.replaceWith(row)" in text
    assert "AI 已校正" in text
    assert "查看原始识别" in text
    assert "original-asr-text" in text
    assert "dataset.revisionOf" in text
    assert "dataset.supersedesSegmentId" in text
    assert ".correction-badge" in html.text
    assert ".original-asr-text" in html.text


def test_workbench_canonical_revision_keeps_original_and_revision_clickback_targets():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text

    assert "wrapper.dataset.sourceSegmentId" in text
    assert "target.dataset.sourceSegmentId" in text
    assert "originalDetails.open = true" in text


def test_workbench_snapshot_projection_keeps_latest_authoritative_event_per_segment():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text

    assert "function applyCanonicalTranscriptEvent" in text
    reducer = text[text.index("function applyCanonicalTranscriptEvent"):]
    reducer = reducer[: reducer.index("function replaceCanonicalTranscriptSnapshot")]
    assert "segment.rank < existing.rank" in reducer
    assert "canonicalTranscriptState.segments.splice" in reducer
    assert 'status: rank === 2 ? "corrected"' in text


def test_workbench_delayed_final_cannot_override_revision_state():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text

    helper = text[text.index("function shouldApplyTranscriptEvent"):]
    helper = helper[: helper.index("function registerTranscriptEvent")]
    assert "if (rank < previous.rank) return false" in helper
    assert "removeRevisionRowsForSegment" not in text


def test_workbench_formal_suggestions_show_trigger_reason_and_confidence():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    text = js.text
    suggestion_renderer = text[text.index("function buildSuggestionCardElement"):]
    suggestion_renderer = suggestion_renderer[: suggestion_renderer.index("function renderSuggestionCards")]
    assert "card.trigger_reason" in suggestion_renderer
    assert "card.confidence" in suggestion_renderer
    assert "触发原因" in suggestion_renderer
    assert "置信度" in suggestion_renderer


def test_workbench_formal_suggestions_put_new_unique_cards_first_and_mark_only_new_arrivals():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    html = client.get("/workbench-legacy")
    assert js.status_code == 200
    assert html.status_code == 200
    text = js.text

    assert "function uniqueSuggestionCardsNewestFirst" in text
    assert "function mergeSuggestionCards" in text
    merge = text[text.index("function mergeSuggestionCards") :]
    merge = merge[: merge.index("function renderSuggestionCards")]
    assert "existingSemanticKeys" in merge
    assert "newCardIds" in merge
    assert "incomingSemanticKeys" in merge
    assert "suggestionSemanticKey" in merge

    real_cards = text[text.index("function renderRealCards") :]
    real_cards = real_cards[: real_cards.index("function renderApproachCards")]
    assert "mergeSuggestionCards" in real_cards
    assert "highlightCardIds: newCardIds" in real_cards

    renderer = text[text.index("function buildSuggestionCardElement") :]
    renderer = renderer[: renderer.index("function formatConfidence")]
    assert "highlightCardIds = new Set()" in renderer
    assert 'div.classList.add("suggestion-new")' in renderer
    assert 'div.setAttribute("aria-label", "本次新到的 AI 建议")' in renderer
    assert ".suggestion.suggestion-new" in html.text


def test_workbench_replaces_auto_suggestion_when_formal_card_targets_same_candidate():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    merge = js[js.index("function mergeSuggestionCards"):]
    merge = merge[: merge.index("function renderSuggestionCards")]

    assert "function suggestionCandidateKey" in js
    assert "incomingCandidateKeys" in merge
    assert "existingCards.filter" in merge
    assert "suggestionCandidateKey(card, index)" in merge


def test_workbench_review_workspace_is_hidden_during_recording_and_visible_afterward():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    visibility = js[js.index("function syncSessionToolVisibility") :]
    visibility = visibility[: visibility.index("function updateRecordButtonReadiness")]
    assert 'phase === "recording" || phase === "processing"' in visibility
    assert "reviewWorkspace.hidden = !hasSession || meetingInProgress" in visibility
    assert "reviewWorkspace.open = hasSession && !meetingInProgress" in visibility

    jump = js[js.index("function jumpToOverviewTarget") :]
    jump = jump[: jump.index("function bindMeetingOverviewJumps")]
    assert '["approach", "audio", "minutes"].includes(target)' in jump
    assert "reviewWorkspace.open = true" in jump


def test_workbench_topbar_shows_recording_duration_and_plain_language_microphone_status():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    js = client.get("/static/workbench.js")
    assert html.status_code == 200
    assert js.status_code == 200

    assert 'id="recording-duration"' in html.text
    assert 'id="mic-input-status"' in html.text
    assert "录音时长" in html.text
    assert "麦克风" in html.text

    text = js.text
    assert "let recordingStartedAtMs = null" in text
    assert "let recordingStatusTimer = null" in text
    assert "function formatRecordingDuration" in text
    assert "function startRecordingStatusTimer" in text
    assert "function stopRecordingStatusTimer" in text
    assert "setInterval(updateRecordingDuration, 1000)" in text
    assert "clearInterval(recordingStatusTimer)" in text
    assert 'setMicInputStatus("已连接")' in text
    assert 'setMicInputStatus("输入正常")' in text
    assert 'setMicInputStatus("未检测到声音")' in text
    prepare = text[text.index("function prepareNewSession"):]
    prepare = prepare[: prepare.index("function preserveSessionBeforeRecording")]
    assert "stopRecordingStatusTimer({ reset: true })" in prepare


def test_workbench_removes_executed_candidates_from_realtime_reminders():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let executedSuggestionCandidateIds = new Set()" in js
    assert "function unprocessedCandidateReminders" in js
    helper = js[js.index("function unprocessedCandidateReminders"):]
    helper = helper[: helper.index("function candidateFocusCounts")]
    assert "currentAutoSuggestionStatus?.processed_candidate_ids" in helper
    assert "executedSuggestionCandidateIds" in helper
    assert "payload.candidate_id" in helper
    reminder_count = js[js.index("function currentReminderCount"):]
    reminder_count = reminder_count[: reminder_count.index("function numericCountOverride")]
    assert "projectedUnprocessedCandidateReminders" in reminder_count
    projection = js[js.index("function projectedUnprocessedCandidateReminders"):]
    projection = projection[: projection.index("function visibleCandidateReminders")]
    assert "unprocessedCandidateReminders(dedupedCandidateReminderProjection(candidateReminderEvents))" in projection
    assert "candidateReminderProjectionCache" in projection
    renderer = js[js.index("function renderCandidateReminders"):]
    renderer = renderer[: renderer.index("function renderTranscriptAndCandidates")]
    assert "projectedUnprocessedCandidateReminders" in renderer
    real_cards = js[js.index("function renderRealCards"):]
    real_cards = real_cards[: real_cards.index("function renderApproachCards")]
    assert "run.target_candidate_id" in real_cards
    assert "refreshCandidateReminderProjection()" in real_cards


def test_workbench_ai_suggestion_failures_are_visible_in_ai_suggestion_panel():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function renderSuggestionFailureState" in js
    failure_renderer = js[js.index("function renderSuggestionFailureState"):]
    failure_renderer = failure_renderer[: failure_renderer.index("function renderSuggestionCards")]
    assert '$("suggestions-panel")' in failure_renderer
    assert "suggestion-error" in failure_renderer
    status_renderer = js[js.index("function renderAutoSuggestionStatus"):]
    status_renderer = status_renderer[: status_renderer.index("async function loadAutoSuggestionStatus")]
    assert 'state === "blocked"' in status_renderer
    assert "renderSuggestionFailureState" in status_renderer
    status_loader = js[js.index("async function loadAutoSuggestionStatus"):]
    status_loader = status_loader[: status_loader.index("async function runAutoSuggestionsOnce")]
    assert 'status: "blocked"' in status_loader
    assert "renderAutoSuggestionStatus" in status_loader
    readiness = js[js.index("async function loadAudioCheck"):]
    readiness = readiness[: readiness.index("function clearStreamEmptyState")]
    assert "if (!llmReady)" in readiness
    assert "renderSuggestionFailureState" in readiness
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("function applyRealtimeTranscriptRevisions")]
    assert "renderSuggestionFailureState" in auto_runner
    manual_runner = js[js.index("async function generateSuggestionCards"):]
    manual_runner = manual_runner[: manual_runner.index('$("btn-cards").addEventListener')]
    assert "renderSuggestionFailureState" in manual_runner


def test_workbench_surfaces_realtime_correction_state_and_model_outcomes():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy").text
    js = client.get("/static/workbench.js").text

    assert 'id="realtime-correction-status"' in html
    assert "function renderRealtimeCorrectionStatus" in js
    for label in [
        "AI 校正中",
        "AI 已校正",
        "无需修改",
        "校正未应用",
        "校正失败",
        "AI 校正已关闭",
    ]:
        assert label in js

    correction_runner = js[js.index("async function runRealtimeCorrectionsOnce"):]
    correction_runner = correction_runner[: correction_runner.index("async function toggleAutoSuggestion")]
    assert "renderRealtimeCorrectionStatus" in correction_runner
    assert "renderRealtimeCorrectionStatus(body)" in correction_runner

    snapshot_renderer = js[js.index("function applySessionEvents"):]
    snapshot_renderer = snapshot_renderer[: snapshot_renderer.index("function normalizedSuggestionSemanticValue")]
    assert "body.realtime_transcript_correction" in snapshot_renderer
    assert "renderRealtimeCorrectionStatus" in snapshot_renderer


def test_workbench_opening_history_does_not_repeat_paid_suggestion_analysis():
    js = TestClient(create_app()).get("/static/workbench.js").text
    history_loader = js[js.index("async function openHistorySession"):]
    history_loader = history_loader[: history_loader.index("function escapeHtml")]

    assert "runAutoSuggestions: false" in history_loader


def test_workbench_readiness_panel_uses_plain_language_without_provider_diagnostics():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    readiness = js[js.index("async function loadAudioCheck") :]
    readiness = readiness[: readiness.index("function clearStreamEmptyState")]
    for label in ["麦克风可用", "实时识别可用", "AI 分析可用"]:
        assert label in readiness
    assert "providers.join" not in readiness
    assert "providerHealth.llm.provider" not in readiness
    assert "providerHealth.llm.model" not in readiness
    assert "body.mic_error" not in readiness


def test_workbench_organize_meeting_uses_real_orchestrator_not_button_clicks():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "async function organizeCurrentSession" in js
    organize_handler = js[js.index('$("btn-organize").addEventListener("click"'):]
    organize_handler = organize_handler[: organize_handler.index('$("btn-cards").addEventListener')]
    assert "await organizeCurrentSession()" in organize_handler
    assert '$("btn-cards").click()' not in organize_handler
    assert '$("btn-approach").click()' not in organize_handler
    assert '$("btn-minutes").click()' not in organize_handler


def test_workbench_organize_meeting_uses_fast_suggestion_candidate_budget():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function enabledLlmRequestBody" in js
    assert "max_candidates" in js
    assert "ORGANIZE_FAST_SUGGESTION_BUDGET" in js
    assert "await generateSuggestionCards({ showToast: false, maxCandidates: ORGANIZE_FAST_SUGGESTION_BUDGET })" in js


def test_workbench_manual_organize_waits_for_live_auto_suggestion_before_execution():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function waitForAutoSuggestionIdle" in js
    assert "let autoSuggestionIdlePromise = Promise.resolve()" in js
    manual_runner = js[js.index("async function generateSuggestionCards"):]
    manual_runner = manual_runner[: manual_runner.index('$("btn-cards").addEventListener')]
    assert "await waitForAutoSuggestionIdle()" in manual_runner


def test_workbench_stop_recording_keeps_realtime_text_during_final_processing():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "正在整理最终文字，当前实时文字会保留在这里" in js
    assert "const liveText = $(\"transcript-stream\").innerText.trim()" in js
    assert "if (!liveText)" in js


def test_workbench_mock_payload_includes_end_of_stream_for_finite_sse_replay():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    assert 'event_type: "end_of_stream"' in r.text


def test_workbench_real_mic_capture_resumes_sends_pcm_and_releases_tracks():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "_micStream" in js
    assert "_micSource" in js
    assert "_micNode" in js
    assert "_micMonitor" in js
    assert "await _micCtx.resume()" in js
    assert "resampleTo16k" in js
    assert "new Float32Array" in js
    assert "_micSentChunks++" in js
    assert "track.stop()" in js
    assert "_micNode.disconnect()" in js
    assert "setMeetingPhase(\"recording\")" in js
    assert "setMeetingPhase(\"processing\")" in js


def test_workbench_real_mic_ws_close_refreshes_persisted_session():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "async function refreshRecordedSession" in js
    assert "`/live/asr/sessions/${sid}/events`" in js
    assert "await refreshRecordedSession(sid)" in js
    assert "没有识别到有效语音" in js


def test_workbench_renders_raw_realtime_asr_partial_and_final_events():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert 'e.event_type === "partial"' in js
    assert 'e.event_type === "final"' in js
    assert 'e.event_type === "transcript_partial"' in js
    assert 'e.event_type === "transcript_final"' in js
    assert "live-partial" in js
    assert "normalized_text || p.text || e.normalized_text || e.text" in js
    assert "empty final" not in js.lower()


def test_workbench_partial_updates_one_global_canonical_active_tail():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "function applyCanonicalTranscriptEvent" in js
    assert "function upsertCanonicalActiveTail" in js
    assert "data-live-segment-id" in js
    assert "正在识别" in js
    assert "partial-draft-index" not in js
    assert "appendPartialDraftUtterance" not in js

    partial_branch = js[js.index('} else if (e.event_type === "partial" || e.event_type === "transcript_partial")'):]
    partial_branch = partial_branch[: partial_branch.index('} else if (e.event_type === "final" || e.event_type === "transcript_final")')]
    assert "applyCanonicalTranscriptEvent(e)" in partial_branch
    assert "upsertCanonicalActiveTail()" in partial_branch

    final_branch = js[js.index('} else if (e.event_type === "final" || e.event_type === "transcript_final")'):]
    final_branch = final_branch[: final_branch.index('} else if (e.event_type === "suggestion_candidate_event" || e.event_type === "partial_hint_event")')]
    assert "applyCanonicalTranscriptEvent(e)" in final_branch
    assert "renderCanonicalTranscriptView()" in final_branch


def test_workbench_uses_canonical_transcript_document_with_one_active_tail():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy").text
    js = client.get("/static/workbench.js").text

    assert 'id="transcript-document"' in html
    assert 'id="transcript-active-tail"' in html
    for helper in [
        "function createCanonicalTranscriptState",
        "function applyCanonicalTranscriptEvent",
        "function replaceCanonicalTranscriptSnapshot",
        "function renderCommittedTranscriptDocument",
        "function upsertCanonicalActiveTail",
    ]:
        assert helper in js

    renderer = js[js.index("function renderCommittedTranscriptDocument"):]
    renderer = renderer[: renderer.index("function upsertCanonicalActiveTail")]
    assert "transcript-paragraph" in renderer
    assert "transcript-segment" in renderer
    assert 'speaker">发言：' not in renderer


def test_workbench_snapshot_prefers_backend_canonical_transcript():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    apply_session = js[js.index("function applySessionEvents"):]
    apply_session = apply_session[: apply_session.index("function normalizedSuggestionSemanticValue")]
    assert "body.canonical_transcript" in apply_session
    assert "canonicalSnapshot: body.canonical_transcript || null" in apply_session


def test_workbench_live_partial_updates_only_canonical_active_tail():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    partial_branch = js[js.index('} else if (e.event_type === "partial" || e.event_type === "transcript_partial")'):]
    partial_branch = partial_branch[: partial_branch.index('} else if (e.event_type === "final" || e.event_type === "transcript_final")')]
    assert "applyCanonicalTranscriptEvent(e)" in partial_branch
    assert "upsertCanonicalActiveTail" in partial_branch


def test_workbench_upsert_live_partial_reuses_existing_segment_row():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    helper = js[js.index("function upsertLivePartial"):]
    helper = helper[: helper.index("function removeLivePartialForSegment")]
    assert "stream.querySelector(livePartialSelector(segmentId))" in helper
    assert "document.createElement(\"div\")" in helper
    assert "row.dataset.liveSegmentId = segmentId" in helper
    assert "stream.appendChild(row)" in helper
    assert "row.innerHTML = livePartialMarkup(event, payload, text)" in helper


def test_workbench_partial_visibility_is_not_gated_by_semantic_markers():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    helper = js[js.index("function shouldDisplayPartial"):]
    helper = helper[: helper.index("function livePartialMarkup")]
    assert "PARTIAL_DRAFT_MARKERS.some" not in helper
    assert "return true;" in helper


def test_workbench_final_removes_active_partial_for_segment():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    remover = js[js.index("function removeLivePartialForSegment"):]
    remover = remover[: remover.index("function appendLiveEvent")]
    assert "document.querySelectorAll" in remover
    assert "forEach((div) => div.remove())" in remover


def test_workbench_snapshot_keeps_only_one_latest_canonical_active_tail():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    assert "canonicalTranscriptState.activeTail" in js
    reducer = js[js.index("function applyCanonicalTranscriptEvent"):]
    reducer = reducer[: reducer.index("function replaceCanonicalTranscriptSnapshot")]
    assert "segment.updatedAtMs < canonicalTranscriptState.activeTail.updatedAtMs" in reducer
    assert "canonicalTranscriptState.activeTail = segment" in reducer


def test_workbench_partial_dedupe_map_is_shared_by_dom_and_metrics():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let latestPartialTextBySegment = new Map()" in js
    upsert = js[js.index("function upsertLivePartial"):]
    upsert = upsert[: upsert.index("function removeLivePartialForSegment")]
    assert "latestPartialTextBySegment.get(segmentId)" in upsert
    assert "displayText" in upsert
    metrics = js[js.index("function recordRealtimeUiEventMetric"):]
    metrics = metrics[: metrics.index("function realtimeUiMetricsSnapshot")]
    assert "latestPartialTextBySegment.get(segmentId)" in metrics
    assert "metricText" in metrics


def test_workbench_delayed_final_does_not_remove_newer_revision_row():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "removeRevisionRowsForSegment" not in js
    assert "transcript_revision: 3" in js
    assert "transcript_final: 2" in js
    assert "final: 1" in js


def test_workbench_empty_final_finds_canonical_active_tail():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    final_branch = js[js.index('} else if (e.event_type === "final" || e.event_type === "transcript_final")'):]
    final_branch = final_branch[: final_branch.index('} else if (e.event_type === "suggestion_candidate_event" || e.event_type === "partial_hint_event")')]
    assert "canonicalTranscriptState.activeTail" in final_branch


def test_workbench_snapshot_projection_keeps_revision_over_long_final():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text

    helper = js[js.index("function projectTranscriptEvents"):]
    helper = helper[: helper.index("function replaceTranscriptRenderState")]
    assert "transcriptTargetSegmentId(event, payload)" in helper
    assert "rank < previous.rank" in helper
    assert "previousCommittedText = previous && previous.rank >= 1" in helper
    assert "previous?.originalText || previousCommittedText" in helper


def test_workbench_empty_final_preserves_last_live_partial():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    final_branch = js[js.index('} else if (e.event_type === "final" || e.event_type === "transcript_final")'):]
    final_branch = final_branch[: final_branch.index('} else if (e.event_type === "suggestion_candidate_event" || e.event_type === "partial_hint_event")')]
    assert 'if (!text)' in final_branch
    assert "return renderResult" in final_branch
    assert final_branch.index('if (!text)') < final_branch.index('applyCanonicalTranscriptEvent(e)')
    assert "保留最后一条临时文字" in js


def test_workbench_saved_transcript_uses_normalized_text_fallback_and_empty_state():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "p.normalized_text || p.text || e.normalized_text || e.text || \"\"" in js
    assert "本次没有识别到有效语音" in js


def test_workbench_snapshot_render_supports_raw_final_events():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert 'e.event_type === "transcript_final" || e.event_type === "final"' in js
    assert "p.normalized_text || p.text || e.normalized_text || e.text || \"\"" in js


def test_workbench_recording_start_keeps_non_empty_waiting_state():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "正在听，会实时显示文字" in js
    assert 'prepareNewSession("正在听，会实时显示文字' in js
    assert "clearStreamEmptyState()" in js


def test_workbench_recording_draft_does_not_claim_view_until_text_arrives():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "let recordingDraftHasClaimedView" in js
    assert "function startRecordingDraftSession" in js
    assert "function claimRecordingDraftView" in js
    start_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "preserveSessionBeforeRecording()" in start_handler
    assert "startRecordingDraftSession(_recSid" in start_handler
    assert 'prepareNewSession("正在听，会实时显示文字' not in start_handler
    append_live = js[js.index("function appendLiveEvent"):]
    append_live = append_live[: append_live.index("function attachTranscriptEvidence")]
    assert "renderResult.visibleTextChanged = true" in append_live
    for marker in [
        'e.event_type === "transcript_revision"',
        'e.event_type === "partial" || e.event_type === "transcript_partial"',
        'e.event_type === "final" || e.event_type === "transcript_final"',
    ]:
        branch = append_live[append_live.index(marker):]
        branch = branch[: branch.index("} else if", 1) if "} else if" in branch[1:] else len(branch)]
        assert branch.index("if (!text)") < branch.index("claimRecordingDraftView()")

    committed_upsert = js[js.index("function upsertCommittedTranscript"):]
    committed_upsert = committed_upsert[: committed_upsert.index("function shouldDisplayPartial")]
    assert committed_upsert.index("claimRecordingDraftView()") < committed_upsert.index('const stream = $("transcript-stream")')

    partial_upsert = js[js.index("function upsertLivePartial"):]
    partial_upsert = partial_upsert[: partial_upsert.index("function removeLivePartialForSegment")]
    assert partial_upsert.index("claimRecordingDraftView()") < partial_upsert.index("clearStreamEmptyState()")


def test_workbench_new_recording_clears_previous_visible_session_immediately():
    js = TestClient(create_app()).get("/static/workbench.js").text
    draft_starter = js[js.index("function startRecordingDraftSession") :]
    draft_starter = draft_starter[: draft_starter.index("function claimRecordingDraftView")]

    assert 'prepareNewSession("正在听，会实时显示文字' in draft_starter
    assert "会先保留上一场会议文字" not in draft_starter


def test_workbench_snapshot_renders_revisions_at_the_original_segment_position():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    reducer = js[js.index("function applyCanonicalTranscriptEvent"):]
    reducer = reducer[: reducer.index("function replaceCanonicalTranscriptSnapshot")]
    assert "existingIndex >= 0" in reducer
    assert "canonicalTranscriptState.segments.splice(existingIndex, 1, segment)" in reducer
    assert "canonicalTranscriptState.segments.sort" in reducer


def test_workbench_unresolved_revision_renders_as_stable_supplement():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function revisionTargetSegmentId" in js
    assert "function revisionSupplementSegmentId" in js
    assert 'return `revision-supplement:${identity}`' in js
    assert 'const isRevisionSupplement = isRevision && !revisionTargetSegmentId(event, payload)' in js
    assert 'isRevisionSupplement ? "修正补充" : "AI 已校正"' in js
    assert 'row.dataset.revisionSupplement = "true"' in js


def test_workbench_projection_namespaces_segments_and_revision_supplements():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function transcriptProjectionKey" in js
    projection_key = js[js.index("function transcriptProjectionKey"):]
    projection_key = projection_key[: projection_key.index("function transcriptEventText")]
    assert 'return `segment:${segmentId}`' in projection_key
    assert "revisionSupplementSegmentId(event, payload)" in projection_key
    projection = js[js.index("function projectTranscriptEvents"):]
    projection = projection[: projection.index("function replaceTranscriptRenderState")]
    assert "const projectionKey = transcriptProjectionKey(event, payload)" in projection
    assert "projected.get(projectionKey)" in projection
    assert "projected.set(projectionKey" in projection


def test_workbench_session_operation_rejects_history_after_any_session_change():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let sessionOperationGeneration = 0" in js
    assert "let sessionOperationController = new AbortController()" in js
    assert "function beginSessionOperation" in js
    assert "function isCurrentSessionOperation" in js
    opener = js[js.index("async function openHistorySession"):]
    opener = opener[: opener.index("function escapeHtml")]
    assert "const operation = beginSessionOperation()" in opener
    assert "{ signal: operation.signal }" in opener
    assert "!isCurrentSessionOperation(operation)" in opener
    assert 'err?.name === "AbortError"' in opener

    record_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    record_handler = record_handler[: record_handler.index('$("btn-live").addEventListener')]
    assert "const operation = beginSessionOperation()" in record_handler
    assert "!isCurrentSessionOperation(operation)" in record_handler

    upload_handler = js[js.index('$("btn-upload").addEventListener("change"'):]
    upload_handler = upload_handler[: upload_handler.index("function compactTranscriptText")]
    assert "const operation = beginSessionOperation()" in upload_handler
    assert "signal: operation.signal" in upload_handler
    assert "!isCurrentSessionOperation(operation)" in upload_handler

    for helper_name in ["resetSessionView", "prepareNewSession"]:
        helper = js[js.index(f"function {helper_name}"):]
        helper = helper[: helper.index("\n}") + 2]
        assert "beginSessionOperation()" in helper

    delete_handler = js[js.index('$("btn-delete").addEventListener("click"'):]
    delete_handler = delete_handler[: delete_handler.index("function downloadSessionArtifact")]
    assert "const operation = beginSessionOperation()" in delete_handler
    assert "signal: operation.signal" in delete_handler


def test_workbench_refresh_live_text_rejects_stale_session_operation():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    refresh = js[js.index("async function refreshLiveText"):]
    refresh = refresh[: refresh.index('$("btn-record").addEventListener')]
    assert "const operation = currentSessionOperation()" in refresh
    assert "{ signal: operation.signal }" in refresh
    assert "!isCurrentSessionOperation(operation) || currentSession !== sid" in refresh
    assert "loadSessionHistory(operation)" in refresh


def test_workbench_committed_dom_key_uses_projection_namespace_without_secondary_escape():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    target = js[js.index("function transcriptTargetSegmentId"):]
    target = target[: target.index("function transcriptEventText")]
    assert "return transcriptProjectionKey(event, payload)" in target
    assert 'startsWith("revision-supplement:")' not in target


def test_workbench_real_mic_level_status_explains_silent_input():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "_micPeak" in js
    assert "_micRms" in js
    assert "function updateMicLevelStatus" in js
    assert "没有检测到麦克风声音" in js
    assert "检测到麦克风声音" in js
    assert "外放声音不一定会进入麦克风输入" in js
    assert "updateMicLevelStatus()" in js


def test_workbench_exposes_selectable_microphone_and_visible_input_level():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy")
    js = client.get("/static/workbench.js")
    assert html.status_code == 200
    assert js.status_code == 200

    assert 'id="mic-device-select"' in html.text
    assert 'id="mic-level-meter"' in html.text
    assert 'aria-label="选择麦克风输入设备"' in html.text
    assert 'aria-label="麦克风输入电平"' in html.text

    text = js.text
    assert "let selectedMicrophoneDeviceId = \"\"" in text
    assert "async function refreshMicrophoneDevices" in text
    assert "navigator.mediaDevices.enumerateDevices" in text
    assert "selectedMicrophoneDeviceId" in text
    assert "deviceId: { exact: selectedMicrophoneDeviceId }" in text
    assert "mic-level-meter" in text
    assert "refreshMicrophoneDevices()" in text
    bootstrap = text[text.index("async function bootstrapWorkbench"):]
    assert "await refreshMicrophoneDevices();" not in bootstrap


def test_workbench_opens_review_tools_when_recording_finishes():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js")
    assert js.status_code == 200
    visibility = js.text[js.text.index("function syncSessionToolVisibility"):]
    visibility = visibility[: visibility.index("function updateRecordButtonReadiness")]
    assert "reviewWorkspace.open = hasSession && !meetingInProgress" in visibility


def test_workbench_tracks_browser_mic_health_for_gate_a_evidence():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "workbench_browser_mic_health" in js
    assert "function resetMicHealthStats" in js
    assert "function browserMicHealthSnapshot" in js
    assert "function classifyBrowserMicHealth" in js
    assert "function publishBrowserMicHealthReport" in js
    assert "MIC_ACTIVE_SAMPLE_THRESHOLD" in js
    assert "active_sample_ratio" in js
    assert "health_status" in js
    assert "blocked_audio_too_quiet" in js
    assert "audio_capture_health_passed" in js
    assert "raw_audio_uploaded: false" in js
    assert "remote_asr_called: false" in js
    assert "llm_called: false" in js
    assert "window.__meetingCopilotBrowserMicHealth" in js
    assert "document.body.dataset.browserMicHealth" in js
    assert "JSON.stringify(_lastBrowserMicHealthReport)" in js
    assert "publishBrowserMicHealthReport()" in js
    start_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "resetMicHealthStats()" in start_handler
    connector = js[js.index("function connectMicWs"):]
    connector = connector[: connector.index('$("btn-record").addEventListener("click"')]
    assert "?audio_source=browser_live_mic" in connector


def test_workbench_tracks_realtime_ui_latency_metrics_for_browser_mic():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function resetRealtimeUiMetrics" in js
    assert "function recordRealtimeUiEventMetric" in js
    assert "function realtimeUiMetricsSnapshot" in js
    assert "window.__meetingCopilotRealtimeUiMetrics" in js
    assert "first_text_visible_latency_ms" in js
    assert "first_partial_visible_latency_ms" in js
    assert "first_final_visible_latency_ms" in js
    assert "first_audio_active_offset_ms" in js
    assert "first_text_after_audio_active_latency_ms" in js
    assert "first_partial_after_audio_active_latency_ms" in js
    assert "first_final_after_audio_active_latency_ms" in js
    assert "partial_visible_count" in js
    assert "final_visible_count" in js
    assert "function recordRealtimeAudioActivityMetric" in js
    assert "activeFrameRatio" in js
    assert "activeFrameRatio < MIC_MIN_ACTIVE_SAMPLE_RATIO" in js

    start_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "resetRealtimeUiMetrics()" in start_handler

    mic_level = js[js.index("function updateMicLevel"):]
    mic_level = mic_level[: mic_level.index("function resetMicHealthStats")]
    assert "recordRealtimeAudioActivityMetric" in mic_level

    ws_handler = js[js.index("_micWs.onmessage ="):]
    ws_handler = ws_handler[: ws_handler.index("_micWs.onclose =")]
    assert "const renderResult = appendLiveEvent(ev)" in ws_handler
    assert "recordRealtimeUiEventMetric(ev, renderResult)" in ws_handler
    assert ws_handler.index("appendLiveEvent(ev)") < ws_handler.index("recordRealtimeUiEventMetric(ev, renderResult)")


def test_workbench_applies_combined_and_fallback_realtime_corrections_in_place():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let realtimeCorrectionInFlight = false" in js
    assert "let realtimeCorrectionPending = false" in js
    assert "let realtimeCorrectionPendingForce = false" in js
    assert "function applyRealtimeTranscriptRevisions" in js
    assert "async function runRealtimeCorrectionsOnce" in js
    assert "/realtime-corrections/run-once" in js
    auto_runner = js[js.index("async function runAutoSuggestionsOnce"):]
    auto_runner = auto_runner[: auto_runner.index("async function toggleAutoSuggestion")]
    assert "applyRealtimeTranscriptRevisions(body.transcript_revisions || [])" in auto_runner
    fallback_runner = js[js.index("async function runRealtimeCorrectionsOnce"):]
    fallback_runner = fallback_runner[: fallback_runner.index("async function", 10)]
    assert "force" in fallback_runner
    assert "applyRealtimeTranscriptRevisions(body.transcript_revisions || [])" in fallback_runner


def test_workbench_leaves_final_and_stop_ai_execution_to_backend_jobs():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    append_live = js[js.index("function appendLiveEvent"):]
    append_live = append_live[: append_live.index("function attachTranscriptEvidence")]
    partial_branch = append_live[append_live.index('e.event_type === "partial"'):]
    partial_branch = partial_branch[: partial_branch.index('} else if (e.event_type === "final"')]
    final_branch = append_live[append_live.index('e.event_type === "final"'):]
    final_branch = final_branch[: final_branch.index('} else if (e.event_type === "suggestion_candidate_event"')]
    assert "runRealtimeAiAfterFinal" not in partial_branch
    assert "runRealtimeCorrectionsOnce" not in partial_branch
    assert "runRealtimeAiAfterFinal" not in final_branch
    assert "runRealtimeCorrectionsOnce" not in final_branch

    assert "let realtimeCorrectionRetryTimer = null" in js
    assert "function scheduleRealtimeCorrectionRetry" in js
    assert "retry_after_ms" in js
    assert "async function drainRealtimeCorrectionsOnStop" in js

    refresh = js[js.index("async function refreshRecordedSession"):]
    refresh = refresh[: refresh.index("async function refreshLiveText")]
    assert "drainRealtimeCorrectionsOnStop" not in refresh
    assert "AI 校正与建议会在后台继续更新" in refresh


def test_workbench_stop_drain_is_bounded_and_waits_for_all_remaining_batches():
    js = TestClient(create_app()).get("/static/workbench.js").text
    drain = js[js.index("async function drainRealtimeCorrectionsOnStop"):]
    drain = drain[: drain.index("async function refreshRecordedSession")]

    assert "MAX_REALTIME_CORRECTION_DRAIN_BATCHES" in drain
    assert "force: true" in drain
    assert 'gate?.reason === "no_unrevised_final"' in drain
    assert "realtimeCorrectionInFlight" in drain
    assert "REALTIME_CORRECTION_DRAIN_TIMEOUT_MS" in drain
    assert "deadlineAt" in drain
    assert "requestTimeoutMs" in drain

    refresh = js[js.index("async function refreshRecordedSession"):]
    refresh = refresh[: refresh.index("async function refreshLiveText")]
    assert "const correctionDrain" not in refresh
    assert "drainRealtimeCorrectionsOnStop" not in refresh
    assert "AI 校正与建议会在后台继续更新" in refresh


def test_workbench_surfaces_partial_correction_without_hiding_original_text():
    js = TestClient(create_app()).get("/static/workbench.js").text

    drain = js[js.index("async function drainRealtimeCorrectionsOnStop"):]
    drain = drain[: drain.index("async function refreshRecordedSession")]
    assert "partialCorrection" in drain
    assert 'status === "partially_completed"' in drain

    refresh = js[js.index("async function refreshRecordedSession"):]
    refresh = refresh[: refresh.index("async function refreshLiveText")]
    assert "correctionDrain.partialCorrection" not in refresh
    assert "body.realtime_transcript_correction" in js
    assert "原始识别" in js


def test_workbench_realtime_correction_state_isolated_by_session_generation():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert "let realtimeCorrectionOwnerSessionId = null" in js
    assert "let realtimeCorrectionGeneration = 0" in js
    assert "function resetRealtimeCorrectionState" in js
    runner = js[js.index("async function runRealtimeCorrectionsOnce"):]
    runner = runner[: runner.index("async function toggleAutoSuggestion")]
    assert "const generation = realtimeCorrectionGeneration" in runner
    assert "realtimeCorrectionOwnerSessionId = sid" in runner
    assert "generation !== realtimeCorrectionGeneration" in runner

    prepare = js[js.index("function prepareNewSession"):]
    prepare = prepare[: prepare.index("function preserveSessionBeforeRecording")]
    assert "resetRealtimeCorrectionState()" in prepare
    draft = js[js.index("function startRecordingDraftSession"):]
    draft = draft[: draft.index("function claimRecordingDraftView")]
    assert "resetRealtimeCorrectionState()" in draft


def test_workbench_has_no_browser_owned_ai_pipeline_after_each_final():
    js = TestClient(create_app()).get("/static/workbench.js").text

    assert "realtimeAiPipelineInFlight" not in js
    assert "realtimeAiPipelinePending" not in js
    assert "runRealtimeAiAfterFinal" not in js
    assert 'runAutoSuggestionsOnce({ reason: "live_final" })' not in js


def test_browser_live_mic_verify_exports_realtime_ui_latency_metrics():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "window.__meetingCopilotRealtimeUiMetrics" in script
    assert "realtime_ui_metrics" in script
    assert "first_text_visible_latency_ms" in script
    assert "first_partial_visible_latency_ms" in script
    assert "first_final_visible_latency_ms" in script
    assert "first_audio_active_offset_ms" in script
    assert "first_text_after_audio_active_latency_ms" in script
    assert "first_partial_after_audio_active_latency_ms" in script
    assert "first_final_after_audio_active_latency_ms" in script
    assert "partial_visible_count" in script
    assert "final_visible_count" in script


def test_browser_live_mic_verify_exports_partial_hint_visibility():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "frontend_partial_hint_count" in script
    assert "candidate_panel_text" in script
    assert "[data-card-kind='partial-hint']" in script or '[data-card-kind="partial-hint"]' in script


def test_workbench_browser_live_mic_ws_marks_browser_source_not_generic_replay():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    connector = js[js.index("function connectMicWs"):]
    connector = connector[: connector.index('$("btn-record").addEventListener("click"')]
    assert 'new WebSocket(apiWsUrl(`/live/asr/stream/ws/${sid}?audio_source=browser_live_mic`))' in connector
    assert 'audio_source=real_mic_recorded_wav' not in connector
    assert 'audio_source=simulated_realtime_wav' not in connector


def test_workbench_mic_audio_waits_for_server_asr_ready_before_flush():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    connector = js[js.index("function connectMicWs"):]
    connector = connector[: connector.index('$("btn-record").addEventListener("click"')]
    assert "let _micAsrReady = false" in js
    assert "event_type === \"asr_ready\"" in connector
    assert "event_type === \"asr_starting\"" in connector
    assert "readyState === WebSocket.OPEN && _micAsrReady" in js
    assert "flushQueuedMicFrames();" in connector
    onopen = connector[connector.index("_micWs.onopen") : connector.index("_micWs.onerror")]
    assert "flushQueuedMicFrames();" not in onopen


def test_workbench_asr_ready_timeout_stops_retrying_and_keeps_failure_visible():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    handler = js[js.index("_micWs.onmessage =") :]
    handler = handler[: handler.index("_micWs.onopen =")]
    assert 'ev.error_code === "asr_ready_timeout"' in handler
    assert "_manualStop = true" in handler
    assert "stopAudioCapture()" in handler


def test_workbench_open_history_keeps_current_view_until_fetch_succeeds():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    handler = js[js.index("async function openHistorySession") :]
    handler = handler[: handler.index("function escapeHtml")]
    assert handler.index("const ev = await api") < handler.index("prepareNewSession")
    assert "打开历史失败" in handler


def test_workbench_stop_wait_for_asr_ready_has_bounded_recovery_path():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    assert "STOP_WAIT_FOR_ASR_READY_MS" in js
    assert "function scheduleAsrReadyStopTimeout" in js
    assert "识别服务未在限定时间内就绪" in js
    stop_function = js[js.index("async function stopMeetingRecording") :]
    stop_function = stop_function[: stop_function.index('$("btn-record").addEventListener')]
    assert "scheduleAsrReadyStopTimeout(sid)" in stop_function
    assert "_micWs.close" in js


def test_workbench_microphone_permission_request_is_bounded_and_visible():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text
    assert "MIC_PERMISSION_TIMEOUT_MS" in js
    assert "function requestMicrophoneStream" in js
    assert "麦克风权限请求超时" in js
    start_handler = js[js.index('$("btn-record").addEventListener("click"') :]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "正在请求麦克风权限" in start_handler
    assert "requestMicrophoneStream" in start_handler


def test_browser_live_mic_verify_script_writes_runner_inputs():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "browser_mic_health_report.json" in script
    assert "asr_probe.json" in script
    assert "ui_verification.json" in script
    assert "workbench-browser-live-mic.png" in script
    assert "--use-fake-ui-for-media-stream" in script
    assert "btn-record" in script
    assert "btn-stop" in script
    assert "audio_source=real_mic_recorded_wav" not in script
    assert "audio_source=simulated_realtime_wav" not in script


def test_browser_live_mic_verify_script_writes_partial_evidence_on_failure():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    catch_handler = script[script.index("} catch (err) {"):]
    assert "writePartialEvidence" in script
    assert "await writePartialEvidence" in catch_handler
    assert "browser_mic_health_report.json" in catch_handler
    assert "asr_probe.json" in catch_handler
    assert "ui_verification.json" in catch_handler
    assert "session_events.json" in catch_handler
    assert "page_state_after_failure.json" in catch_handler


def test_browser_live_mic_verify_script_treats_missing_cards_as_no_go_evidence_not_script_failure():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "organize_wait_status.json" in script
    assert "waitForBrowserState" in script
    assert "isOrganizeTerminal" in script
    assert "organize_wait_status" in script
    organize_block = script[script.index("if (canOrganize) {"):]
    organize_block = organize_block[: organize_block.index("const evidence = await writePartialEvidence")]
    assert "waitForAnyCdpExpression(" not in organize_block
    assert "waitForBrowserState" in organize_block
    assert "isOrganizeTerminal" in organize_block


def test_browser_live_mic_verify_script_infers_real_gateway_from_session_llm_usage():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "function inferLlmProviderFromSessionBody" in script
    assert "body.minutes?.llm_usage" in script
    assert "real_gateway" in script
    assert "inferLlmProviderFromSessionBody(body)" in script


def test_browser_live_mic_verify_script_records_chrome_capture_mode():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "MEETING_COPILOT_BROWSER_MIC_HEADLESS" in script
    assert "MEETING_COPILOT_BROWSER_MIC_FAKE_UI" in script
    assert "browser_environment.json" in script
    assert "chrome_headless" in script
    assert "chrome_fake_ui_for_media_stream" in script
    assert "visible_chrome" in script
    assert "headless_chrome" in script
    assert "chromeArgs" in script
    assert "if (chromeHeadless)" in script


def test_browser_live_mic_verify_environment_does_not_overstate_production_llm_evidence():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "production_derivation_requested" in script
    assert 'counts_as_production_llm_evidence: derivationMode === "production_enabled"' not in script


def test_browser_live_mic_verify_script_supports_fake_audio_file_diagnostic_mode():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "MEETING_COPILOT_BROWSER_MIC_AUDIO_FILE" in script
    assert "chrome_fake_audio_file" in script
    assert "--use-fake-device-for-media-stream" in script
    assert "--use-file-for-fake-audio-capture=" in script
    assert "input_mode" in script
    assert "fake_audio_file_browser_mic" in script


def test_browser_live_mic_verify_script_exports_asr_semantic_quality_for_acceptance_bundle():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")
    assert "asr_semantic_quality" in script


def test_browser_live_mic_verify_script_separates_no_cost_selftest_from_production_llm_evidence():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "MEETING_COPILOT_BROWSER_MIC_DERIVATION_MODE" in script
    assert "no_cost_deterministic" in script
    assert "production_enabled" in script
    assert "noCostDerivationSelfTest=1" in script
    assert "counts_as_production_llm_evidence" in script
    assert "derivations_generated" in script
    assert "deterministic_demo" in script


def test_browser_live_mic_verify_no_cost_mode_strips_remote_llm_gateway_env():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "function backendEnvForBrowserMicRun" in script
    assert "noCostDerivationSelfTest" in script
    assert 'LLM_GATEWAY_BASE_URL: ""' in script
    assert 'LLM_GATEWAY_API_KEY: ""' in script
    assert 'LLM_GATEWAY_MODEL: ""' in script
    assert 'LLM_GATEWAY_PROVIDER_LABEL: "not_configured_no_cost_browser_mic_selftest"' in script


def test_browser_live_mic_verify_infers_remote_gateway_from_session_traces_not_only_node_env():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "function gatewayBaseUrlKindFromSessionBody" in script
    assert "body.llm_evidence?.gateway_base_url_kind" in script
    assert "llm_trace?.provider" in script
    assert "gatewayBaseUrlKindFromSessionBody(body)" in script
    assert "countsAsProductionLlmEvidence(llmProvider, hasLlmUsage, gatewayKind)" in script


def test_browser_live_mic_verify_exports_llm_usage_summary_for_production_acceptance():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "function collectLlmUsageSummary" in script
    assert "body.llm_evidence?.llm_usage_total_tokens" in script
    assert "llm_call_count" in script
    assert "llm_usage_total_tokens" in script
    assert "collectLlmUsageSummary(body)" in script
    assert "body.event_source?.asr_semantic_quality" in script
    assert "acceptance_eligible" in script
    assert "acceptance_blockers" in script


def test_browser_live_mic_verify_can_keep_session_and_probe_audio_export_for_long_recording():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "MEETING_COPILOT_BROWSER_MIC_DELETE_SESSION" in script
    assert "deleteSessionAfterRun" in script
    assert "audio_export_probe.json" in script
    assert "/audio.wav" in script
    assert "audio_file_size_bytes" in script
    assert "audio_sha256_matches_session" in script


def test_browser_live_mic_verify_can_reuse_existing_server_without_owning_its_process():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "MEETING_COPILOT_E2E_USE_EXISTING_SERVER" in script
    assert "useExistingServer" in script
    assert '"existing_external"' in script
    assert '"managed_isolated"' in script
    assert 'useExistingServer ? "8765" : "8769"' in script
    assert "backend_server_mode" in script
    managed_branch = script[script.index("if (!useExistingServer)"):]
    managed_branch = managed_branch[: managed_branch.index("await waitForHttp")]
    assert 'spawn(\n      "uvicorn"' in managed_branch
    assert "processes.push(server)" in managed_branch
    assert "await waitForHttp" in script


def test_browser_live_mic_verify_records_recording_phase_append_first_ui_samples():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "recording_phase_ui_samples.json" in script
    assert "function readRecordingPhaseUiSample" in script
    assert "partial_draft_count" in script
    assert "live_partial_text" in script
    assert "transcript_text" in script
    assert "recording_phase_ui_samples" in script
    assert "active_live_partial_count" in script
    assert "committed_transcript_row_count" in script
    assert "corrected_transcript_row_count" in script
    assert "max_rows_for_single_active_segment" in script


def test_browser_live_mic_verify_gates_realtime_transcript_compaction_and_correction_visibility():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert 'from "./workbench_browser_live_mic_compaction.mjs"' in script
    compaction_module = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_compaction.mjs"
    ).read_text(encoding="utf-8")
    assert "export function buildRealtimeTranscriptCompactionReport" in compaction_module
    assert "realtime_transcript_compaction_status" in script
    assert "realtime_transcript_compaction_report" in script
    assert "first_correction_visible_latency_ms" in script
    assert "failed_duplicate_active_segment_rows" in compaction_module
    assert "failed_realtime_correction_not_visible" in compaction_module
    assert "correction_disabled_by_setting" in compaction_module
    assert "no_revision_needed" in compaction_module
    assert "production_enabled" in script


def test_browser_live_mic_verify_records_meeting_cockpit_counts():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "function readMeetingCockpitCounts" in script
    assert "cockpit_counts: readMeetingCockpitCounts()" in script
    assert "meeting_cockpit_counts" in script
    assert "function readMeetingCockpitStage" in script
    assert "cockpit_stage: readMeetingCockpitStage()" in script
    assert "meeting_cockpit_stage" in script
    assert "c-cockpit-stage" in script
    assert "c-transcript" in script
    assert "c-gap" in script
    assert "c-cards" in script
    assert "c-audio" in script
    assert "c-minutes" in script


def test_browser_live_mic_verify_writes_machine_readable_summary_file():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "const summary =" in script
    assert 'path.join(artifactRoot, "summary.json")' in script
    assert "JSON.stringify(summary, null, 2)" in script
    assert "console.log(JSON.stringify(summary, null, 2))" in script


def test_browser_live_mic_summary_promotes_ui_acceptance_fields():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    summary_block = script[script.index("const summary =") :]
    summary_block = summary_block[: summary_block.index("await writeFile(path.join(artifactRoot, \"summary.json\")")]
    for expected in [
        "workbench_same_session_visible",
        "frontend_utterance_count",
        "frontend_card_count",
        "frontend_minutes_visible",
        "meeting_cockpit_stage",
        "meeting_cockpit_counts",
        "first_text_after_audio_active_latency_ms",
        "first_final_after_audio_active_latency_ms",
        "partial_visible_count",
        "final_visible_count",
        "browser_console_error_count",
        "network_error_count",
    ]:
        assert expected in summary_block


def test_browser_live_mic_summary_classifies_realtime_experience_separately_from_final_latency():
    e2e_dir = (
        Path(__file__).resolve().parents[2]
        / "e2e"
    )
    script = (e2e_dir / "workbench_browser_live_mic_verify.mjs").read_text(encoding="utf-8")
    gate = (e2e_dir / "workbench_browser_live_mic_gate.mjs").read_text(encoding="utf-8")

    summary_block = script[script.index("const summary =") :]
    summary_block = summary_block[: summary_block.index("await writeFile(path.join(artifactRoot, \"summary.json\")")]
    assert "buildRealtimeExperienceReport" in script
    assert "recordingPhaseUiSamples" in script
    assert "healthStatus: health.health_status" in script
    assert "realtimeExperienceStatusFails" in script
    assert "realtime_experience_status" in summary_block
    assert "realtime_experience_report" in summary_block
    assert "mainline_completion_status" in summary_block
    assert "mainline_completion_report" in summary_block
    assert "text_latency_slo_ms" in gate
    assert "final_latency_slo_ms" in gate
    assert "passed_realtime_partial_final_slow" in gate
    assert "failed_realtime_text_not_visible_during_recording" in gate
    assert "failed_audio_capture_health" in gate
    assert "failed_invalid_slo_configuration" in gate
    assert "buildMainlineCompletionReport" in gate
    assert "failed_mainline_completion" in gate
    assert "production_llm_evidence_missing" in gate


def test_browser_live_mic_summary_gates_formal_ai_suggestions_during_recording():
    e2e_dir = (
        Path(__file__).resolve().parents[2]
        / "e2e"
    )
    script = (e2e_dir / "workbench_browser_live_mic_verify.mjs").read_text(encoding="utf-8")
    gate = (e2e_dir / "workbench_browser_live_mic_gate.mjs").read_text(encoding="utf-8")

    summary_block = script[script.index("const summary =") :]
    summary_block = summary_block[: summary_block.index("await writeFile(path.join(artifactRoot, \"summary.json\")")]
    assert "buildRealtimeAiSuggestionReport" in script
    assert "realtimeAiSuggestionStatusFails" in script
    assert "recordingPhaseUiSamples" in script
    assert "realtime_ai_suggestion_status" in summary_block
    assert "realtime_ai_suggestion_report" in summary_block
    assert "max_recording_ai_suggestions" in summary_block
    assert "first_ai_suggestion_visible_latency_ms" in summary_block
    assert "failed_realtime_ai_suggestion_not_visible_during_recording" in gate
    assert "passed_realtime_ai_suggestion_visible" in gate
    failure_block = script[script.index("realtimeAiSuggestionStatusFails"):]
    assert "process.exitCode = 1" in failure_block


def test_browser_live_mic_verify_detects_live_reminder_drift_during_recording():
    script = (
        Path(__file__).resolve().parents[2]
        / "e2e"
        / "workbench_browser_live_mic_verify.mjs"
    ).read_text(encoding="utf-8")

    assert "function readRecordingBackendReminderProbe" in script
    assert "backend_live_reminder_count" in script
    assert "function buildLiveReminderDriftReport" in script
    assert "live_reminder_drift_report" in script
    assert "live_reminder_drift_status" in script
    assert "failed_backend_candidates_not_visible" in script
    assert "liveReminderDriftStatusFails" in script
    assert "process.exitCode = 1" in script


def test_workbench_empty_snapshot_preserves_existing_realtime_text():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "preserveExistingTranscript" in js
    assert "尚未收到完整整理结果，已保留实时文字" in js
    assert "const existingLivePartial = canonicalTranscriptState.activeTail" in js
    assert "已保留临时实时文字" in js
    assert "preserveExistingTranscript: true" in js


def test_workbench_provider_error_from_real_mic_is_shown_as_unavailable_not_success():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert 'e.event_type === "provider_error"' in js
    assert "real_asr_sidecar_unavailable" in js
    assert "实时识别不可用" in js
    assert 'setMeetingPhase("idle")' in js
    provider_error = js[js.index('if (e.event_type === "provider_error")'):]
    provider_error = provider_error[: provider_error.index('} else if (e.event_type === "transcript_revision"')]
    assert "stream.innerHTML" not in provider_error
    assert "canonicalTranscriptFullText()" in provider_error
    assert "renderCanonicalTranscriptEmptyState" in provider_error


def test_workbench_real_mic_failure_restores_previous_readable_session():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "let preservedSessionBeforeRecording" in js
    assert "function preserveSessionBeforeRecording" in js
    assert "function restorePreservedSessionAfterRecordingFailure" in js
    start_handler = js[js.index('$("btn-record").addEventListener("click"'):]
    start_handler = start_handler[: start_handler.index('$("btn-live").addEventListener("click"')]
    assert "preserveSessionBeforeRecording()" in start_handler
    provider_error_branch = js[js.index('if (e.event_type === "provider_error")'):]
    provider_error_branch = provider_error_branch[: provider_error_branch.index('} else if (e.event_type === "transcript_revision"')]
    assert "restorePreservedSessionAfterRecordingFailure" in provider_error_branch
    refresh_recorded = js[js.index("async function refreshRecordedSession"):]
    refresh_recorded = refresh_recorded[: refresh_recorded.index("async function refreshLiveText")]
    assert "restorePreservedSessionAfterRecordingFailure" in refresh_recorded
    assert "已保留上一场会议" in js


def test_workbench_degraded_empty_asr_session_is_not_shown_as_success():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function sessionHasTranscript" in js
    assert "function sessionDegradationText" in js
    assert "body.degradation_reasons" in js
    assert "未识别到有效语音" in js
    assert "setMeetingPhase(hasTranscript ? \"ready\" : \"idle\")" in js
    assert "AI 校正与建议会在后台继续更新" in js
    assert "会议文字已生成" in js
    assert ': "未识别到有效语音"' in js
    assert "function currentSessionHasTranscript" in js
    assert "const hasTranscript = Boolean(currentSession) && currentSessionHasTranscript()" in js
    assert "$(\"btn-cards\").disabled = recording || cleanupPending || !hasTranscript" in js
    history_loader = js[js.index("async function openHistorySession"):]
    history_loader = history_loader[: history_loader.index("function escapeHtml")]
    assert "applySessionEvents(" in history_loader
    assert "hasTranscript ? \"已打开历史会议。\" : sessionDegradationText(ev)" in history_loader
    assert "runAutoSuggestions: false" in history_loader


def test_workbench_upload_uses_full_session_snapshot_renderer():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    upload_handler = js[js.index('$("btn-upload").addEventListener("change"'):]
    upload_handler = upload_handler[: upload_handler.index("function appendLiveEvent")]
    assert "applySessionEvents(currentSession, ev, \"录音识别完成。可以生成会议建议或会后复盘。\")" in upload_handler
    assert "renderTranscriptAndCandidates(currentEvents);" not in upload_handler


def test_workbench_slow_generation_ignores_stale_session_responses():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "if (currentSession !== sid) return;" in js


def test_workbench_upload_does_not_clear_existing_session_before_success():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    upload_handler = js[js.index('$("btn-upload").addEventListener("change"'):]
    upload_handler = upload_handler[: upload_handler.index("function appendLiveEvent")]
    assert upload_handler.index('prepareNewSession("", { sessionOperation: operation });') > upload_handler.index("currentSession = body.session_id")
    assert "setMeetingPhase(\"processing\")" in upload_handler


def test_workbench_delete_resets_all_visible_session_state_and_stops_capture():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function resetSessionView" in js
    assert "currentEvents = []" in js
    for element_id in ["c-decision", "c-action", "c-risk", "c-question", "c-gap", "c-approach", "s-candidates", "s-cards", "s-approach-cards"]:
        assert f'$(\"{element_id}\").textContent = \"0\"' in js
    assert '$(\"s-asr\").textContent = "—"' in js
    assert '$(\"s-llm\").textContent = "—"' in js
    assert "stopAudioCapture()" in js
    assert "_micWs.close()" in js


def test_workbench_partial_delete_keeps_snapshot_and_exposes_audio_cleanup_retry():
    client = TestClient(create_app())
    response = client.get("/static/workbench.js")
    assert response.status_code == 200
    js = response.text

    assert "let currentSessionCleanupPending = false" in js
    assert "function markSessionAudioCleanupPending" in js

    helper = js[js.index("function markSessionAudioCleanupPending"):]
    helper = helper[: helper.index("\n}") + 2]
    assert "currentSessionCleanupPending = true" in helper
    assert "currentEvents" not in helper
    assert "currentSuggestionCards" not in helper
    assert "currentApproachCards" not in helper
    assert "currentMinutes" not in helper
    assert '$("btn-delete").textContent = "重试清理录音"' in helper
    assert "会议记录已删除，但录音文件仍在等待本地清理" in helper

    availability = js[js.index("function syncActionAvailability"):]
    availability = availability[: availability.index("function syncSessionToolVisibility")]
    assert "currentSessionCleanupPending" in availability
    assert '$("btn-delete").disabled = recording || !currentSession' in availability
    for button_id in [
        "btn-cards",
        "btn-approach",
        "btn-organize",
        "btn-live",
        "btn-minutes",
        "btn-export-transcript",
        "btn-export-minutes",
        "btn-export-audio",
    ]:
        assert f'$("{button_id}").disabled' in availability

    handler = js[js.index('$("btn-delete").addEventListener("click"'):]
    handler = handler[: handler.index("function downloadSessionArtifact")]
    assert "const outcome = await api" in handler
    assert "outcome?.audio_cleanup_pending" in handler
    assert "outcome?.session_record_deleted" in handler
    assert "markSessionAudioCleanupPending(outcome)" in handler
    assert handler.index("markSessionAudioCleanupPending(outcome)") < handler.index("resetSessionView")
    assert "return;" in handler[handler.index("markSessionAudioCleanupPending(outcome)"):handler.index("resetSessionView")]


def test_workbench_demo_load_renders_created_events_before_snapshot_fetch():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "renderDemoSessionFromCreated(sid, created)" in js
    assert "演示会议已加载，正在补全持久化状态" in js
    assert "演示会议已加载，但持久化状态暂时读取失败" in js


def test_workbench_history_requests_demo_sessions_only_after_demo_opt_in():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function sessionHistoryPath" in js
    assert "include_demo=true" in js
    history_fetcher = js[js.index("async function fetchSessionHistory"):]
    history_fetcher = history_fetcher[: history_fetcher.index("function cacheHistorySessions")]
    assert "const body = await api(sessionHistoryPath(), { signal: operation.signal })" in history_fetcher
    history_loader = js[js.index("async function loadSessionHistory"):]
    history_loader = history_loader[: history_loader.index("async function openHistorySession")]
    assert "fetchSessionHistory(operation)" in history_loader
    assert 'api("/live/asr/sessions")' not in history_loader


def test_workbench_recording_phase_locks_conflicting_actions():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function syncActionAvailability(phase)" in js
    for element_id in ["btn-upload", "btn-load", "btn-delete", "btn-cards", "btn-approach", "btn-live"]:
        assert f'$(\"{element_id}\")' in js
    assert "const recording = phase === \"recording\" || phase === \"processing\";" in js
    assert "recording || !currentSession" in js


def test_workbench_labels_demo_and_real_session_sources():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "function sessionSourceLabel" in js
    assert "演示" in js
    assert "麦克风" in js
    assert "导入录音" in js
    assert "provider_mode" in js


def test_workbench_labels_simulated_realtime_wav_without_claiming_real_mic():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "simulated_realtime_wav" in js
    assert "模拟实时" in js
    simulated_branch = js[js.index('source.input_source === "simulated_realtime_wav"'):]
    simulated_branch = simulated_branch[: simulated_branch.index('source.input_source === "real_mic_recorded_wav"')]
    assert "真实麦克风" not in simulated_branch
    assert "counts_as_real_mic_go_evidence" in js


def test_workbench_labels_real_mic_recorded_wav_as_recorded_not_browser_live():
    client = TestClient(create_app())
    r = client.get("/static/workbench.js")
    assert r.status_code == 200
    js = r.text
    assert "real_mic_recorded_wav" in js
    assert "真实麦克风录音" in js
    recorded_branch = js[js.index('source.input_source === "real_mic_recorded_wav"'):]
    recorded_branch = recorded_branch[: recorded_branch.index("const missingRealMicTranscript")]
    assert "browser_live_mic_go_evidence" in recorded_branch
    assert "counts_as_real_mic_go_evidence" in recorded_branch
    assert "浏览器实时麦克风" not in recorded_branch


def test_workbench_full_flow_with_fake_llm(monkeypatch):
    """The API flow the workbench drives: mock session -> events -> LLM cards -> approach cards."""
    from meeting_copilot_web_mvp import llm_service

    class FakeClient:
        def post_json(self, url, headers, body, timeout):
            sys_msg = body["messages"][0]["content"] if body.get("messages") else ""
            if "方案考量" in sys_msg:
                return {"choices": [{"message": {"content": json.dumps([{"card_type": "approach.alternative", "suggestion_text": "加 50% 档", "confidence": 0.85, "trigger_reason": "灰度档位", "evidence_quote": "先灰度 5%"}])}}], "usage": {"total_tokens": 90}}
            return {"choices": [{"message": {"content": '{"suggestion_text":"建议确认 owner","confidence":0.8,"trigger_reason":"owner 缺失"}'}}], "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}

    fake = FakeClient()
    monkeypatch.setattr(llm_service, "HttpxLlmClient", lambda: fake)
    monkeypatch.setenv("LLM_GATEWAY_BASE_URL", "https://gw.example")
    monkeypatch.setenv("LLM_GATEWAY_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_GATEWAY_MODEL", "m1")

    client = TestClient(create_app())
    create = client.post("/live/asr/mock/sessions", json={
        "session_id": "wb_flow", "provider": "local_mock_asr",
        "streaming_events": [{"event_type": "final", "segment_id": "s1", "text": "先灰度 5%。", "start_ms": 0, "end_ms": 3200, "received_at_ms": 3500, "confidence": 0.9}]
    })
    assert create.status_code == 201

    ev = client.get("/live/asr/sessions/wb_flow/events")
    assert ev.status_code == 200 and ev.json()["events"]

    cards = client.post("/live/asr/demo/sessions/wb_flow/llm-execution-runs", json={"mode": "enabled"})
    assert cards.status_code == 200
    assert any(r.get("card_status") == "new" for r in cards.json()["runs"])

    ap = client.post("/live/asr/demo/sessions/wb_flow/approach-cards", json={"mode": "enabled"})
    assert ap.status_code == 200
    assert ap.json()["count"] >= 1


def test_workbench_recording_failure_preserves_auto_suggestion_and_reminder_state():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    preserve = js[js.index("function preserveSessionBeforeRecording"):]
    preserve = preserve[: preserve.index("function restorePreservedSessionAfterRecordingFailure")]
    restore = js[js.index("function restorePreservedSessionAfterRecordingFailure"):]
    restore = restore[: restore.index("function startRecordingDraftSession")]

    assert "autoSuggestionStatus" in preserve
    assert "executedSuggestionCandidateIds" in preserve
    assert "candidateFocusType" in preserve
    assert "currentAutoSuggestionStatus = cloneAutoSuggestionStatus(preserved.autoSuggestionStatus)" in restore
    assert "executedSuggestionCandidateIds = new Set(preserved.executedSuggestionCandidateIds)" in restore
    assert "currentCandidateFocusType = preserved.candidateFocusType" in restore
    assert "replaceCandidateReminderEvents(currentEvents)" in restore


def test_workbench_formal_suggestions_use_semantic_identity_and_fold_older_cards():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "const MAX_FORMAL_SUGGESTIONS_VISIBLE" in js
    assert "function suggestionSemanticKey" in js
    assert "suggestion_text" in js[js.index("function suggestionSemanticKey"): js.index("function uniqueSuggestionCardsNewestFirst")]
    assert "suggestionEvidenceTargetKey" in js
    assert "suggestion-fold" in js
    assert "查看其余" in js


def test_workbench_reminder_projection_is_cached_and_partial_events_do_not_rescan_current_events():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "let candidateReminderProjectionCache" in js
    assert "function invalidateCandidateReminderProjection" in js
    assert "function replaceCandidateReminderEvents" in js
    assert "function appendCandidateReminderEvent" in js
    reminder_count = js[js.index("function currentReminderCount"):]
    reminder_count = reminder_count[: reminder_count.index("function numericCountOverride")]
    assert "projectedUnprocessedCandidateReminders()" in reminder_count
    assert "currentEvents" not in reminder_count
    append_live = js[js.index("function appendLiveEvent"):]
    append_live = append_live[: append_live.index("function attachTranscriptEvidence")]
    partial_branch = append_live[append_live.index('e.event_type === "partial"'):]
    partial_branch = partial_branch[: partial_branch.index('e.event_type === "final"')]
    assert "currentEvents.filter" not in partial_branch


def test_workbench_accessibility_announces_committed_results_without_partial_chatter():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy").text
    js = client.get("/static/workbench.js").text

    assert 'id="transcript-live-region"' in html
    assert 'id="reminder-live-region"' in html
    assert 'id="suggestions-panel"' in html and 'aria-label="AI 建议和状态"' in html
    assert 'id="toast" role="status" aria-live="polite" aria-atomic="true"' in html
    assert "function announceCommittedTranscript" in js
    assert "function announceRealtimeReminder" in js
    partial_markup = js[js.index("function livePartialMarkup"):]
    partial_markup = partial_markup[: partial_markup.index("function livePartialSelector")]
    assert 'aria-live="off"' in partial_markup


def test_workbench_transcript_scroll_follow_preserves_reader_position_and_offers_resume_control():
    client = TestClient(create_app())
    html = client.get("/workbench-legacy").text
    js = client.get("/static/workbench.js").text

    assert 'id="btn-new-transcript-content"' in html
    assert "有新内容" in html
    assert "const TRANSCRIPT_NEAR_BOTTOM_PX = 96" in js
    for helper in [
        "function getTranscriptScrollTarget",
        "function isTranscriptNearBottom",
        "function captureTranscriptFollowState",
        "function syncTranscriptAfterRender",
        "function resumeTranscriptFollowing",
        "function bindTranscriptScrollFollow",
    ]:
        assert helper in js
    assert 'window.matchMedia("(max-width: 900px)").matches' in js
    assert "document.scrollingElement" in js
    assert 'window.matchMedia("(prefers-reduced-motion: reduce)").matches' in js
    assert '$("btn-new-transcript-content").hidden = false' in js
    assert '$("btn-new-transcript-content").hidden = true' in js


def test_workbench_transcript_renderers_share_scroll_follow_boundary():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function renderCanonicalTranscriptView" in js
    transaction = js[js.index("function renderCanonicalTranscriptView"):]
    transaction = transaction[: transaction.index("function partialDraftKey")]
    assert transaction.count("captureTranscriptFollowState()") == 1
    assert "renderCommittedTranscriptDocument({ syncFollow: false })" in transaction
    assert "upsertCanonicalActiveTail({ syncFollow: false })" in transaction
    assert transaction.count("syncTranscriptAfterRender(") == 1

    apply_session = js[js.index("function applySessionEvents"):]
    apply_session = apply_session[: apply_session.index("function normalizedSuggestionSemanticValue")]
    assert "replaceCanonicalTranscriptSnapshot(body.canonical_transcript" not in apply_session
    assert "canonicalSnapshot: body.canonical_transcript" in apply_session


def test_workbench_restores_latest_recoverable_real_session_on_startup():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "async function restoreLatestRealSession" in js
    restore = js[js.index("async function restoreLatestRealSession"):]
    restore = restore[: restore.index("async function openHistorySession")]
    assert "sessionHistoryPath()" in restore
    assert "session.recoverable" in restore
    assert "session.is_mock" in restore
    assert "last_activity_at_ms" in restore
    assert "beginSessionOperation()" in restore
    assert "isCurrentSessionOperation(operation)" in restore
    assert "body.canonical_transcript" in js[js.index("function applySessionEvents"):]
    assert "已恢复最近会议" in restore

    bootstrap = js[js.index("async function bootstrapWorkbench"):]
    assert "await loadSessionHistory();" in bootstrap
    assert "await restoreLatestRealSession();" in bootstrap
    assert bootstrap.index("await loadSessionHistory();") < bootstrap.index("await restoreLatestRealSession();")


def test_workbench_restored_real_microphone_session_reports_interrupted_connection_honestly():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function isInterruptedRecoveredSession" in js
    helper = js[js.index("function isInterruptedRecoveredSession"):]
    helper = helper[: helper.index("async function restoreLatestRealSession")]
    assert "browser_live_mic" in helper
    assert "real_mic" in helper
    assert "end_of_stream" in helper
    assert "evaluation_summary" in helper
    assert "end_of_stream_event_count" in helper

    restore = js[js.index("async function restoreLatestRealSession"):]
    restore = restore[: restore.index("async function openHistorySession")]
    assert "shouldShowDemoTools()" not in restore.split("\n", 2)[1]
    assert "isInterruptedRecoveredSession(events)" in restore
    assert "events.audio?.saved" in restore
    assert "录音连接已中断" in restore
    assert "已保留截至断开时的文字和录音" in restore
    assert "本场历史会话未保存录音" in restore
    assert "const hasAudio = Boolean(events.audio?.saved)" in restore
    assert "录音已保存，但实时识别未产生可用文字" in restore
    assert "未找到可用文字或录音" in restore


def test_workbench_recovery_counts_canonical_active_tail_as_transcript():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    helper = js[js.index("function sessionHasTranscript"):]
    helper = helper[: helper.index("function currentSessionHasTranscript")]
    assert "body.canonical_transcript?.full_text" in helper


def test_workbench_reconciles_committed_final_source_snapshots_in_live_reducer():
    client = TestClient(create_app())
    js = client.get("/static/workbench.js").text

    assert "function reconcileCanonicalCommittedSegments" in js
    segment_builder = js[js.index("function canonicalTranscriptSegmentFromEvent"):]
    segment_builder = segment_builder[: segment_builder.index("function applyCanonicalTranscriptEvent")]
    assert "sourceSnapshotText" in segment_builder
    reducer = js[js.index("function applyCanonicalTranscriptEvent"):]
    reducer = reducer[: reducer.index("function replaceCanonicalTranscriptSnapshot")]
    assert "reconcileCanonicalCommittedSegments()" in reducer

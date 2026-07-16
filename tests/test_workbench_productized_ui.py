from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = (
    REPO_ROOT
    / "code"
    / "web_mvp"
    / "backend"
    / "meeting_copilot_web_mvp"
    / "frontend_static"
)


def test_demo_tools_are_hidden_from_default_product_workbench():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert 'class="demo-disclosure" hidden' in html
    assert "shouldShowDemoTools()" in js
    assert 'params.get("demo") === "1"' in js
    assert "meetingCopilotDemo" in js


def test_workbench_copy_avoids_internal_acceptance_terms_in_default_ui():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    default_surface = html + "\n" + js

    assert "当前会话还未满足正式分析条件" not in default_surface
    assert "请检查 AI 分析配置或会话验收状态" not in default_surface
    assert "自动建议运行中。有新文字后会自动分析，不需要手动点击。" not in html


def test_realtime_unavailable_points_to_import_recording_path():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "导入录音继续" in js
    assert "当前不能实时识别，请先导入录音。" in js


def test_workbench_live_regions_are_scoped_to_stable_user_facing_updates():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    assert 'id="transcript-stream" aria-live=' not in html
    assert 'id="transcript-live-region" class="sr-only" role="status" aria-live="polite"' in html
    assert 'id="reminder-live-region" class="sr-only" role="status" aria-live="polite"' in html
    assert 'id="candidate-panel" role="region" aria-label="实时提醒"' in html
    assert 'id="suggestions-panel" role="region" aria-live="polite" aria-atomic="false" aria-label="AI 建议和状态"' in html


def test_workbench_transcript_is_a_continuous_document_not_an_event_log():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    assert 'id="transcript-document"' in html
    assert 'id="transcript-active-tail"' in html
    assert "transcript-paragraph" in html
    assert "transcript-active-tail" in html


def test_post_meeting_workspace_stays_in_main_column_without_a_folded_details_wrapper():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    transcript_index = html.index('<div id="transcript-stream">')
    review_index = html.index('id="review-workspace"')
    main_end = html.index("</main>")
    status_strip = html[html.index('id="meeting-status-strip"'):html.index("</header>")]

    assert transcript_index < review_index < main_end
    assert '<section class="post-meeting-workspace" id="review-workspace" hidden' in html
    assert '<details class="review-workspace"' not in html
    assert 'id="btn-organize"' in html
    assert 'id="btn-minutes"' in html
    assert 'id="btn-export-audio"' in html
    assert 'id="c-audio"' in status_strip
    assert 'id="c-minutes"' in status_strip


def test_post_meeting_actions_use_compact_responsive_grids():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    assert ".post-meeting-workspace .session-tools{grid-template-columns:repeat(3,minmax(0,1fr))}" in html
    assert ".post-meeting-workspace .secondary-tools{grid-template-columns:repeat(2,minmax(0,1fr))" in html
    assert ".post-meeting-workspace .session-tools,.post-meeting-workspace .secondary-tools{grid-template-columns:repeat(2,minmax(0,1fr))}" in html


def test_auto_suggestion_status_and_pause_control_live_inside_ai_suggestion_section():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    realtime_title = html.index('<div class="panel-title">实时提醒</div>')
    candidate_panel = html.index('id="candidate-panel"')
    ai_title = html.index('<div class="panel-title">AI 建议</div>')
    auto_status = html.index('id="auto-suggestion-status"')
    auto_toggle = html.index('id="btn-auto-suggestion-toggle"')
    suggestions_panel = html.index('id="suggestions-panel"')

    assert realtime_title < candidate_panel < ai_title < auto_status < auto_toggle < suggestions_panel
    assert "暂停 AI 建议" in html


def test_import_recording_uses_visible_native_button_and_keeps_file_input_compatibility():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    e2e = (
        REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_all_buttons_smoke.mjs"
    ).read_text(encoding="utf-8")

    assert '<button type="button" class="btn" id="btn-upload-label">导入录音</button>' in html
    assert '<input type="file" id="btn-upload"' in html
    assert '<label class="btn"' not in html
    assert '$("btn-upload-label").addEventListener("click", () => $("btn-upload").click())' in js
    assert 'uploadLabel.disabled = recording' in js
    assert 'document.getElementById("btn-upload-label").focus()' in e2e
    assert 'page.send("Input.dispatchKeyEvent"' in e2e
    assert 'code: "Space"' in e2e
    assert 'visible_button_triggered_file_input' in e2e
    assert e2e.index('document.getElementById("btn-upload-label").focus()') < e2e.index('page.send("Input.dispatchKeyEvent"')
    assert e2e.index('page.send("Input.dispatchKeyEvent"') < e2e.index("DOM.setFileInputFiles")


def test_history_button_opens_the_single_history_modal_without_e2e_bypass():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")
    e2e = (
        REPO_ROOT / "code" / "web_mvp" / "e2e" / "workbench_all_buttons_smoke.mjs"
    ).read_text(encoding="utf-8")

    assert "function openHistoryModal" in js
    assert "function loadSessionHistoryForModal" in js
    history_handler = js[js.index('$("btn-history").addEventListener("click"'):]
    history_handler = history_handler[: history_handler.index('$("btn-auto-suggestion-toggle")')]
    assert "openHistoryModal()" in history_handler
    assert "await loadSessionHistoryForModal()" in history_handler
    assert 'document.querySelector("#review-workspace > summary").click()' not in e2e
    assert 'document.getElementById("history-modal").hidden === false' in e2e


def test_history_surfaces_share_one_sorted_client_cache():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "let _historySessions = []" in js
    assert "function sortHistorySessions" in js
    assert "async function fetchSessionHistory" in js
    compact_loader = js[js.index("async function loadSessionHistory"):]
    compact_loader = compact_loader[: compact_loader.index("function openHistoryModal")]
    modal_loader = js[js.index("async function loadSessionHistoryForModal"):]
    modal_loader = modal_loader[: modal_loader.index("function renderHistoryModalList")]
    assert "fetchSessionHistory" in compact_loader
    assert "fetchSessionHistory" in modal_loader
    assert "cacheHistorySessions" in compact_loader
    assert "_historySessions" in modal_loader

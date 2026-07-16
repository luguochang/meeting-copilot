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


def test_workbench_exposes_desktop_runtime_status_slot():
    html = (STATIC_DIR / "workbench.html").read_text(encoding="utf-8")

    assert 'id="s-desktop"' in html
    assert "桌面壳" in html


def test_workbench_probes_tauri_runtime_when_available():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "initDesktopRuntimeProbe" in js
    assert "__TAURI__" in js
    assert "runtime_get_status" in js
    assert "桌面壳已连接" in js
    assert "浏览器模式" in js


def test_workbench_desktop_runtime_resolves_api_base_from_tauri_runtime_status():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "desktop_api_base_url" in js
    assert "apiBaseUrl" in js
    assert "function apiUrl" in js
    assert "function apiWsUrl" in js
    assert "new WebSocket(apiWsUrl" in js


def test_workbench_writes_packaged_frontend_probe_when_tauri_runtime_is_available():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "writeDesktopFrontendProbe" in js
    assert "runtime_write_frontend_probe" in js
    assert "document.readyState" in js
    assert "history-list" in js
    assert "session-meta" in js


def test_workbench_writes_packaged_backend_api_probe_after_bootstrap():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "writePackagedBackendApiProbe" in js
    assert "packaged_api_probe" in js
    assert 'api("/health")' in js
    assert 'api("/live/asr/sessions")' in js
    assert "await writePackagedBackendApiProbe();" in js


def test_workbench_writes_opt_in_packaged_same_chain_probe():
    js = (STATIC_DIR / "workbench.js").read_text(encoding="utf-8")

    assert "runPackagedSameChainProbe" in js
    assert "packaged_same_chain_probe" in js
    assert "packaged_same_chain_probe_enabled" in js
    assert "deterministic_demo" in js
    assert "same_session_id_observed" in js
    assert "transcript_visible" in js
    assert "suggestion_card_count" in js
    assert "approach_card_count" in js
    assert "minutes_visible" in js
    assert "history_visible" in js
    assert "delete_verified" in js
    assert "paid_provider_called" in js
    assert "runtime_write_frontend_probe" in js

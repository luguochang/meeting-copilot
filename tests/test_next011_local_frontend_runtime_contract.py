from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "code" / "web_mvp" / "frontend_v2"
SRC_ROOT = FRONTEND_ROOT / "src"


def _read(relative_path: str) -> str:
    return (FRONTEND_ROOT / relative_path).read_text(encoding="utf-8")


def test_shared_frontend_uses_one_local_only_base_for_http_sse_and_browser_asr():
    helper = _read("src/api/localApiBase.ts")
    app = _read("src/app/App.tsx")
    event_transport = _read("src/api/eventTransport.ts")
    microphone = _read("src/features/live-meeting/useBrowserMicrophone.ts")

    assert "export function resolveLocalApiBase" in helper
    assert "export function resolveLocalApiUrl" in helper
    assert "export function resolveLocalWebSocketUrl" in helper
    assert 'from "../api/localApiBase"' in app
    assert 'from "./localApiBase"' in event_transport
    assert 'from "../../api/localApiBase"' in microphone
    assert "resolveLocalApiBase" in app
    assert "new HttpMeetingApi(apiBase)" in app
    assert "new SseEventTransport(apiBase)" in app
    assert "asrBaseUrl={apiBase}" in app
    assert "resolveLocalApiUrl" in event_transport
    assert "resolveLocalWebSocketUrl" in microphone


def test_vite_dev_proxy_covers_formal_local_api_and_asr_routes():
    vite_config = _read("vite.config.ts")
    assert 'from "./src/api/localApiBase"' in vite_config
    assert "resolveLocalApiBase" in vite_config
    for route in (
        '"/v2"',
        '"/providers"',
        '"/settings"',
        '"/health"',
        '"/metrics"',
        '"/desktop"',
        '"/live/asr/stream/ws"',
    ):
        assert route in vite_config
    assert "ws: true" in vite_config
    assert "VITE_DEV_API_TARGET is required" in vite_config
    assert '|| "http://127.0.0.1:8767"' not in vite_config


def test_local_frontend_contract_does_not_add_cloud_sync_or_remote_audio_fallback():
    sources = "\n".join(
        (path.read_text(encoding="utf-8") for path in (
            SRC_ROOT / "api" / "localApiBase.ts",
            SRC_ROOT / "app" / "App.tsx",
            SRC_ROOT / "api" / "eventTransport.ts",
            SRC_ROOT / "features" / "live-meeting" / "useBrowserMicrophone.ts",
        ))
    )
    assert "cloud" not in sources.lower()
    assert "api.example" not in sources.lower()

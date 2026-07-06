# PCWEB-109 Mic Adapter No-op UI Invocation Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 主线节点：`mic adapter`、`desktop runtime`  
> 边界：本计划不授权访问麦克风、不请求音频权限、不枚举设备、不采集或写入 audio chunk、不删除真实 audio chunk、不启动 ASR worker、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

## 1. 目标

PCWEB-105 定义了麦克风 adapter 合同，PCWEB-106 把合同展示到 Web 工作台，PCWEB-107 把 7 个 mic adapter command 静态绑定到 Tauri no-op IPC。PCWEB-109 要把这条链路推进到 UI invocation 层：工作台必须能展示 7 个 mic adapter no-op command 的调用状态。

在普通浏览器中，UI 必须明确显示 `mic_adapter_browser_fallback` 和 `not_invoked`，证明没有 Tauri IPC、没有麦克风权限请求、没有音频采集和没有远程调用。

在未来 Tauri WebView 中，UI 才允许通过 `window.__TAURI__.core.invoke` 或 `window.__TAURI__.tauri.invoke` 调用以下 7 个 no-op command：

- `mic_adapter_prepare`
- `mic_adapter_status`
- `mic_adapter_start`
- `mic_adapter_pause`
- `mic_adapter_resume`
- `mic_adapter_stop`
- `mic_adapter_delete_audio_chunks`

这些调用仍只能返回 no-op/safe flags，不代表真实录音可用。

## 2. 范围

修改：

- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/app.js`
- `code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/styles.css`
- `code/web_mvp/backend/tests/test_app.py`
- `code/web_mvp/e2e/browser_smoke.mjs`
- `docs/current-mainline-index.md`
- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `README.md`
- `code/desktop_tauri/README.md`

不修改：

- Rust/Tauri command binding。
- mic adapter command 的真实执行逻辑。
- ASR worker。
- ASR provider/model runtime。
- LLM provider config 或 `configs/local/`。
- 任何真实音频、用户录音或 `.m4a`。

## 3. 验收

测试必须证明：

- 静态资源包含 `loadDesktopMicAdapterNoopInvocation` 和 `renderDesktopMicAdapterNoopInvocation`。
- 静态资源包含 7 个 no-op Tauri command name。
- 普通浏览器中 mic adapter panel 展示 `mic_adapter_browser_fallback`。
- 普通浏览器中 7 个 invocation row 均为 `not_invoked`。
- UI 继续展示 `safe_to_request_audio_permission_now=false`、`safe_to_capture_audio_now=false`、`safe_to_call_remote_asr_now=false`、`safe_to_call_llm_now=false` 和 `safe_to_run_tauri_or_cargo_now=false`。
- Browser smoke 中 `.desktop-mic-adapter-invoke-command` 数量为 7。

## 4. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：`1 failed, 2 warnings`。失败原因是静态 JS 尚未包含 `loadDesktopMicAdapterNoopInvocation`。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：`1 passed, 2 warnings`。

总门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile pc-web
```

结果：

- root-pytest：`286 passed, 2 warnings`
- core-pytest：`34 passed, 1 warning`
- web-backend-pytest：`316 passed, 2 warnings`
- browser smoke：`status=ok`，包含 `desktop mic adapter no-op invocation browser fallback`
- quality gate：`profile=pc-web passed`

## 5. 实现说明

`app.js` 新增 `desktopMicAdapterNoopCommands`，并在 `DOMContentLoaded` 和 `renderEmpty()` 后加载 no-op invocation 状态。

普通浏览器 fallback：

- 不调用 Tauri invoke。
- 不请求 `navigator.mediaDevices`。
- 不请求权限。
- 不采集音频。
- 不写 chunk。
- 不调用远程 ASR/LLM。
- 显示 7 个 `not_invoked` row。

Tauri context：

- 只调用 PCWEB-107 已绑定的 no-op command。
- 展示每个 command 的返回状态。
- 所有真实执行相关 safety flags 继续为 false。

## 6. 后续

PCWEB-109 只证明 UI 到 mic adapter no-op IPC 的 invocation surface。它不代表真实麦克风可用，也不代表可以进入真实会议。

后续仍按 6 个里程碑推进：

- M2 真实 Tauri no-op run 需要单独审批运行 Tauri/Cargo。
- M5 短时本地模拟输入只能使用合成输入、mock events 或 approved synthetic event file。
- 真实麦克风 shadow test 必须等 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete、导出和反馈链路全部具备后，由用户显式启动。

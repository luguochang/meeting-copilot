# P0-P2 Mainline Closure Checklist

> 日期：2026-07-17 更新
> 状态：active / current audit recorded below
> 目的：把“完成所有 P0-P2”收束到可执行、可验证的产品主链路，避免继续用 mock、no-op、边界证明或计划文档代替生产功能。

## 0. 当前判断

项目不能只做成“音频转文字工具”。完成标准必须证明它是一个会议中的实时辅助产品：

```text
音频输入 -> 实时 ASR -> 实时转写 -> 自动建议卡 -> 纪要/行动项 -> 历史记录 -> 删除/复盘
```

当前已经有部分 Web MVP、Tauri 壳、ASR worker 生命周期和非生产 handoff 证据，但还不能宣称生产级 PC 端产品完成。

## 1. P0 主链路必须完成

- [x] P0-1 公开测试音频模拟实时会议链路完成：
  - [x] 输入是一段可公开使用的中文技术会议/多人讨论测试音频，或项目内生成的非私密测试音频。
  - [x] 音频按时间片模拟实时输入，而不是一次性生成最终文本。
  - [x] 后端持续产出 ASR partial/final events。
  - [x] 前端实时显示转写。
  - [x] 同一 session 内自动生成建议卡。
  - [x] 会议结束后生成纪要、行动项、关键风险。
  - [x] 会话进入历史记录。
  - [x] 历史详情可重新打开。
  - [x] 删除会话会清理对应转写、建议、纪要和运行产物。

- [x] P0-2 真实麦克风链路完成：
  - [x] Mac 端能显式由用户点击开始。
  - [x] 能触发或说明麦克风权限状态。
  - [x] 能采集真实麦克风或外放收音音频。
  - [x] 实时会议原始录音默认本地保存，并与同一 session 关联。
  - [x] 录音资产记录包含相对路径、格式、采样率、声道数、时长、文件大小、sha256 和来源类型。
  - [x] 历史详情可回放或导出该次会议录音，方便会后复盘和 ASR 纠错。
  - [x] 音频进入同一套实时 ASR 链路。
  - [x] ASR 输出进入同一套建议卡/纪要/历史链路。
  - [x] 删除会话会同步删除关联录音文件；删除失败必须可见，不允许静默成功。

  边界：真实多人自然会议人工验收仍未完成；已有 P0 证据来自 visible Chrome + getUserMedia + 本机外放中文技术会议语料。

- [x] P0-3 前端产品化完成：
  - [x] 首页/Workbench 只保留用户能理解的核心入口：开始会议、导入录音、历史记录。
  - [x] 所有按钮都接真实接口或明确 disabled，不允许静默 mock。
  - [x] 实时转写、建议卡、纪要、历史记录在同一页面或清晰导航中完成。
  - [x] 去掉复杂内部名词，例如 lane、shadow、candidate、fixture 等面向开发者的词。
  - [x] 页面需要 Playwright 或等价自动化逐按钮触发证据。

- [x] P0-4 自动建议链路完成：
  - [x] 建议不是固定 mock 文案。
  - [x] 建议基于最近上下文生成。
  - [x] 建议要覆盖程序员会议价值点：架构风险、接口边界、测试缺口、上线风险、遗漏问题。
  - [x] 调用频率有节流，避免高成本和高延迟。
  - [x] LLM 失败时页面仍能显示转写和降级提示。

## 2. P1 交付可用性必须完成

- [x] P1-1 录音导入/会后转写完成：
  - [x] 用户可以导入本地音频文件。
  - [x] 文件进入本地 ASR 或当前配置的 ASR provider。
  - [x] 导入的原始录音会保存为本地 audio asset，供后续导出复盘。
  - [x] 生成完整文字稿。
  - [x] 生成纪要、行动项、风险点。
  - [x] 进入历史记录。

- [x] P1-2 Provider 和成本策略完成：
  - [x] 默认只使用本地 ASR，不引入额外付费 ASR。
  - [x] 远程 ASR 仅作为可选 provider，不默认启用。
  - [x] LLM 默认走 OpenAI-compatible gateway。
  - [x] API key/base URL/model 只能来自安全配置，不写入代码和文档；桌面端使用 Keychain/Credential Manager。

- [x] P1-3 数据保留和删除完成：
  - [x] 隐私/保留/删除策略文档存在。
  - [x] 删除 API 会清理 session 数据。
  - [x] 删除 API 会清理实时会议关联录音资产。
  - [x] 原始录音默认只保存在本地，不上传到远程 ASR/LLM。
  - [x] 测试覆盖删除边界，禁止误删项目外目录。
  - [x] 证据报告脱敏。

- [x] P1-4 长会议策略完成：
  - [x] 至少有 synthetic long-meeting gate。
  - [x] 报告明确 synthetic 不等于真实 20-60 分钟生产 soak。
  - [x] ASR/LLM 上下文窗口有截断和摘要策略。

## 3. P2 桌面交付必须完成

- [ ] P2-1 Mac Tauri fresh run 完成：
  - [ ] 受控 toolchain 能运行 `cargo tauri dev`。
  - [ ] 桌面窗口打开当前 Workbench。
  - [ ] 桌面端能访问后端健康检查。
  - [ ] 桌面端能跑同一套主链路。
  - [ ] 证据落在 `artifacts/tmp/desktop_tauri_current_run/`。

- [ ] P2-2 Mac 打包完成：
  - [ ] `cargo tauri build` 可复现。
  - [ ] 产出 `.app` 或 `.dmg`。
  - [ ] 本机安装 smoke test。
  - [ ] 签名/公证若未完成，必须记录明确 blocker，不得宣称发布完成。

- [ ] P2-3 Windows 保持计划边界：
  - [ ] 明确 Windows 当前是 plan-only，除非有 Windows 机器实测。
  - [ ] 记录 Windows 音频采集、权限、打包差异。
  - [ ] 不得把 Mac/Tauri 共用代码等同于 Windows 完成。

## 3.1 2026-07-17 当前候选覆盖结论

本节覆盖早期历史记录中的旧 no-op 结论，必须与 [P0-P2 完成报告](p0-p2-completion-report-2026-07-17.md) 一起阅读：

- P0-1、P0-3、P0-4 的实现和 no-cost 主链已经通过当前 packaged backend/React 代码与测试；实时建议采用 durable job、SSE draft/delta 和 commit barrier，不是固定 mock 文案。
- P0-2 的 browser microphone 和 native helper 分别有真实证据，但当前候选还没有同一次 packaged React UI 点击证据。必须等 Mac 解锁后由用户显式开始会议并确认权限，不能把 `mic_adapter_prepare` 当成开始录音。
- P1-2 已实现为本地 ASR + OpenAI-compatible LLM + 系统凭据库；本轮自测使用本地 fake provider，未调用收费中转站。
- P1-4 的一小时 browser 纵向门禁已有既有 Go 证据，但不升级为自然多人会议质量或公开发布证据。
- P2-1 当前为 `partial`: bundled runtime、native helper、随机端口 ACL 和实际 packaged WebView IPC 已通过；`cargo tauri dev` 仍因受控工具链没有 cargo-tauri 不可执行，用户点击后的同场 UI 主链尚未完成。
- P2-2 当前为 `partial/no-go for release`: `.app` 可复现构建并通过资源/runtime smoke，Developer ID、notary、staple、Gatekeeper 和 clean Mac 安装仍未完成。
- P2-3 继续 plan-only，未做 Windows 真机执行。

### 3.2 2026-07-17 V2 导入录音闭环补齐

早期的 P1-1 勾选只代表旧版 `/live/asr/transcribe-file/sessions` 可用，不能证明 V2 React 页面可用。本轮已把它补成 V2 真实入口：

- [x] V2 首页提供“导入录音”入口，不再只有开始麦克风会议。
- [x] 新增 `POST /v2/meetings/import-audio`，multipart 文件直接进入本地 FunASR batch。
- [x] 原始上传文件和标准化 `audio.wav` 都落在会议目录，删除栅栏覆盖两者。
- [x] 导入结果写入 V2 canonical transcript、recording session、audio chunk 和 legacy ASR projection。
- [x] 导入会议自动结束并唤醒 durable correction/suggestion/minutes/approach/index jobs。
- [x] 前端导入成功后打开 V2 review；历史、音频播放和完整文字通过真实 API 可读。
- [x] 后端集成验证 `1 passed`，包含 durable post-job 完成；V2 focused `56 passed`，前端 `54 passed`。

边界仍明确：该入口是文件导入/会后转写，不是实时 partial 字幕；FunASR batch 当前以 whole-file segment 兼容，未来需要真实 timestamped segments 时再扩展批量 transcript transaction。详见 [V2 导入录音契约](v2-import-audio-contract-2026-07-17.md)。

## 4. 当前执行顺序

1. 先修通“公开测试音频模拟实时会议”主链路。
2. 再清理前端 mock 和不可用按钮，做逐按钮自测。
3. 再把 Mac Tauri 壳接到同一套 Workbench 主链路。
4. 再做真实麦克风人工/半自动验收。
5. 再做打包、安装、发布证据。

## 5. 不计入完成的内容

- 只写计划，不跑链路。
- 只证明 no-op IPC。
- 只证明本地事件文件可以被后端读取。
- 只跑旧 evidence bundle。
- 只做 ASR，不生成实时建议卡。
- 只生成建议卡，不保存历史和纪要。
- 只支持 Web，不证明 Mac 桌面 fresh run。
- 只做 Mac，不得宣称 Windows 完成。

## 6. 当前执行环境注意事项

当前工作树里的旧文件存在普通 `O_RDONLY` 打开超时现象；`stat`、新建文件读写、以及 `O_NONBLOCK` 打开旧文件可用。后续脚本如果遇到读取卡住，应优先使用短路径 `/tmp/meeting-copilot-short` 和非阻塞读取，直到工作树 I/O 行为恢复正常。

## 7. 2026-07-09 多 Agent 审查结论

### 前端审查

- Workbench 已有核心入口：开始会议、结束会议、导入录音、历史记录、整理会议、刷新文字、删除、生成建议、方案分析、生成纪要、导出文字稿、导出纪要。
- 主要缺口不是接口缺失，而是产品化不足：演示/mock 默认可见、内部验收/Provider 术语暴露、AI/ASR 不可用时按钮体验像调试台。
- 已执行的修复：
  - [x] 默认隐藏演示入口，只允许 `?demo=1` 或 `localStorage.meetingCopilotDemo=1` 开启。
  - [x] 将实时 ASR 不可用的主按钮文案改为引导导入录音。
  - [x] 将自动建议和整理失败文案从内部验收语言改成用户可理解语言。
  - [x] 增加 `tests/test_workbench_productized_ui.py` 静态合同测试。
  - [x] 增加桌面壳状态位和 Tauri runtime probe：浏览器显示“浏览器模式”，Tauri WebView 中尝试调用 `runtime_get_status` 并显示“桌面壳已连接/未连接”。
  - [x] 将 `workbench_smoke.mjs` 改为显式 `?demo=1` opt-in 后再跑示例，避免默认产品页面暴露 mock/demo。

### 后端审查

- `/live/asr/*` 已经具备 Web MVP 主链路接口：实时流、文件导入、事件查询、自动建议、方案卡、纪要、历史、删除。
- 主要缺口：
  - `/sessions` 和 `/live/asr/sessions` 两套会话模型尚未收口。
  - production 与 demo/mock 路径并存，真实 production endpoint E2E 证据仍偏薄。
  - 文件上传缺大小/类型/时长/并发保护。
  - JSON repo 缺轻量并发控制。
  - 自动建议更多依赖前端触发 `run-once`，不是完整后端 runner。

### 桌面审查

- Tauri 壳、配置、IPC command、ASR worker lifecycle、audio chunk manifest runtime 已存在。
- Web Workbench 真实浏览器麦克风链路存在，但 Rust `mic_adapter` 仍只是 manifest/runtime state，不采集 PCM。
- W4 缺一致的最新 Tauri WebView 打开证据和 desktop runtime probe。
- W5 已有 synthetic event file handoff，但不能算真实/准真实音频验收。
- 推荐优先路线：先把 Tauri WebView 里的浏览器麦克风作为准真实桌面输入验收 lane；Rust 原生 mic_adapter 作为后续更重的生产路线。

## 8. 本轮已验证

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q /tmp/meeting-copilot-short/tests/test_workbench_productized_ui.py
3 passed, 1 warning
```

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_workbench_desktop_runtime_probe.py \
  /tmp/meeting-copilot-short/tests/test_workbench_productized_ui.py
5 passed, 1 warning
```

当前机器真实环境模拟实时主链路：

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core \
python3 /tmp/meeting-copilot-short/tools/mainline_evidence_bundle_runner.py \
  --lane simulated-realtime \
  --audio /tmp/meeting-copilot-short/code/asr_runtime/outputs/simulated-release-review.16k.wav \
  --artifact-root /tmp/meeting-copilot-short/artifacts/tmp/acceptance/current-simulated-realtime-20260709 \
  --data-dir /tmp/meeting-copilot-short/artifacts/tmp/current-simulated-realtime-data \
  --run-id current-simulated-realtime-20260709
```

结果：

```text
verdict=go
audio_source=simulated_realtime_wav
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
asr_semantic_quality_status=passed
llm_provider=real_gateway
llm_model=gpt-5.5
llm_called=true
llm_call_count=5
llm_usage_total_tokens=2083
transcript_char_count=68
final_segment_count=1
suggestion_card_count=3
approach_card_count=3
minutes_char_count=294
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
delete_verified=true
counts_as_real_mic_go_evidence=false
artifact_root=artifacts/tmp/acceptance/current-simulated-realtime-20260709
```

边界：这证明“测试音频按实时 WebSocket 输入 -> 真实本地 ASR -> 真实 LLM -> 建议/方案/纪要/历史/删除”主链路成立，但不证明真实麦克风。

Workbench 浏览器 UI smoke：

```text
URL=http://127.0.0.1:8765/workbench
title=会议助手
demoHidden=true
primaryTexts=["开始会议","结束会议","导入录音","历史记录"]
recordButton=开始会议 enabled
upload=enabled
history=enabled
sessionTools=disabled before session
autoSuggestionText=会议开始后，我会根据文字自动提醒风险、待办和待确认问题。
bodyHasInternalAcceptanceCopy=false
sysStatus=麦克风可用；实时识别可用；AI 分析已配置 openai_compatible_gateway gpt-5.5
browser_console_error_count=0
```

Workbench desktop probe smoke after backend restart:

```text
scriptSrc=http://127.0.0.1:8765/static/workbench.js?v=20260709-desktop-probe
desktopText=浏览器模式
demoHidden=true
internalCopyVisible=false
footerText includes 桌面壳 浏览器模式
browser_console_error_count=0
```

Tauri W4 current-machine dev evidence:

```text
artifact=artifacts/tmp/desktop_tauri_current_run/w4-tauri-dev-desktop-probe-20260709/evidence.json
cargo_tauri_version=tauri-cli 2.11.4
backend_health_raw={"status":"ok","service":"meeting-copilot-web-mvp"}
workbench_html_served=true
tauri_dev_started=true
tauri_binary_running=true
desktop_probe_static_present=true
desktop_runtime_probe_command=runtime_get_status
production_desktop_status=dev_webview_process_running_not_packaged
window_inspection_status=blocked_by_macos_accessibility_permission
window_inspection_error=osascript is not allowed assistive access (-1728)
tauri_dev_stopped_after_capture=true
```

边界：这证明当前机器可用 `cargo tauri dev --no-watch` 启动桌面二进制并服务当前 Workbench，但还没有截图/DOM 级桌面 WebView 证据、没有 `.app/.dmg` 包、Rust mic adapter 仍不是 PCM 采集。

Focused regression:

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_workbench_desktop_runtime_probe.py \
  /tmp/meeting-copilot-short/tests/test_workbench_productized_ui.py \
  /tmp/meeting-copilot-short/tests/test_mainline_evidence_bundle_runner.py \
  -k 'simulated_realtime or workbench_productized or desktop_runtime_probe'
7 passed, 12 deselected, 2 warnings

PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_desktop_audio_chunk_runtime.py \
  /tmp/meeting-copilot-short/tests/test_desktop_asr_worker_lifecycle_runtime.py \
  /tmp/meeting-copilot-short/tests/test_desktop_tauri_scaffold.py
16 passed, 1 warning

CARGO_HOME=/tmp/meeting-copilot-short/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/tmp/meeting-copilot-short/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/tmp/meeting-copilot-short/artifacts/tmp/desktop_tauri_target \
/tmp/meeting-copilot-short/artifacts/tmp/rust_toolchain/cargo/bin/cargo check \
  --manifest-path /tmp/meeting-copilot-short/code/desktop_tauri/src-tauri/Cargo.toml
Finished `dev` profile
```

Workbench demo opt-in / button smoke:

```text
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  /tmp/meeting-copilot-short/tests/test_workbench_e2e_demo_opt_in.py
1 passed, 1 warning

MEETING_COPILOT_E2E_PORT=8771 \
MEETING_COPILOT_E2E_CHROME_PORT=9231 \
MEETING_COPILOT_E2E_FAKE_LLM_PORT=18771 \
PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core \
node /tmp/meeting-copilot-short/code/web_mvp/e2e/workbench_smoke.mjs
workbench smoke OK: 4 utterances, 9 suggestions, history/minutes/delete verified
```

边界：该 smoke 使用 `?demo=1` 和 fake LLM，证明页面按钮与业务路径连通，不等于真实 ASR/真实 LLM 生产证据；真实 ASR/真实 LLM 证据见 simulated realtime bundle。

边界：这次 UI smoke 只验证默认页面状态和产品化文案；还没有逐个点击导入录音/历史/删除等按钮，也没有在真实麦克风权限弹窗中验收。

## 9. 2026-07-09 本轮新增真实麦克风与 Mac 打包状态

详见：

```text
docs/p0-p2-progress-update-2026-07-09-real-mic-and-mac-packaging.md
```

### 9.1 真实麦克风主链路

新增 GO 证据：

```text
artifacts/tmp/acceptance/browser-live-mic-tts-20260709-mainline-001/manifest.json
```

关键结论：

```text
verdict=go
audio_source=browser_live_mic
input_audio_path_kind=browser_get_user_media
counts_as_real_mic_go_evidence=true
browser_live_mic_go_evidence=true
ui_coverage=visible_chrome
asr_provider=sherpa_onnx_realtime
asr_provider_mode=real
asr_fallback_used=false
asr_semantic_quality_status=passed
transcript_char_count=105
final_segment_count=1
llm_provider=real_gateway
llm_called=true
suggestion_card_count=3
approach_card_count=3
minutes_char_count=614
all_cards_have_evidence=true
workbench_same_session_visible=true
frontend_utterance_count=1
frontend_card_count=6
frontend_minutes_visible=true
browser_console_error_count=0
network_error_count=0
delete_verified=true
degradation_reasons=[]
```

边界：

- 该证据来自 visible Chrome + getUserMedia + 本机 `say` 外放中文技术会议语料后由真实麦克风收音。
- 证明真实麦克风主链路可运行，但仍不等于多人真实会议、自然语速、复杂噪声环境下的最终生产验收。

### 9.2 Mac 打包

新增结果：

```text
cargo tauri build
release binary built
Meeting Copilot.app bundled
DMG bundle failed at bundle_dmg.sh
```

已观察到 `.app` 启动进程：

```text
Meeting Copilot.app/Contents/MacOS/meeting-copilot-desktop
```

仍未完成：

- `.dmg` 未产出。
- packaged `.app` 还没有 DOM/截图级 Workbench 验收。
- packaged `.app` 还没有同一套实时会议主链路证据。
- 签名、公证和安装 smoke 未完成。

2026-07-09 后续追加：

- `cargo tauri build --bundles app` 已成功复现，`.app` 可构建。
- packaged `.app` 已可启动进程。
- 已修复 packaged Workbench API base 边界：Tauri `runtime_get_status` 返回 `desktop_api_base_url=http://127.0.0.1:8765`，前端通过 `apiUrl(...)` / `apiWsUrl(...)` 解析后端 HTTP/WebSocket。
- `.dmg` blocker 更具体定位为 `bundle_dmg.sh` 卡在 Finder AppleScript：

```text
Running AppleScript to make Finder stuff pretty: /usr/bin/osascript ... dmg.DLq0dd
```

- 因此当前 P2 状态是：`.app` 可构建可启动；`.dmg`、签名/公证、packaged DOM/mainline evidence 仍未完成。

2026-07-09 packaged window / regression 追加：

- Packaged `.app` CoreGraphics window probe 已落证据：

```text
artifacts/tmp/desktop_tauri_current_run/packaged-app-window-20260709/evidence.json
```

- 结果：进程、WebKit 子进程、CoreGraphics 窗口均存在，窗口 owner 为 `Meeting Copilot`，bounds 为 `1280x849`。
- 仍未完成：截图与 DOM 级证据。`screencapture -l <window_id>` 和全屏 `screencapture` 均被 macOS 阻止。
- Backend mainline regression 已重新跑通：

```text
214 passed, 2 warnings
```

- Release acceptance 已在修正 browser live mic gate 和 Workbench UI verifier runtime path 后刷新为 GO：

```text
artifact_root=artifacts/tmp/release_acceptance/release-current-20260709-browser-mic-gate-and-ui-path-fixed
verdict=go
blockers=[]
file_lane=go
simulated_realtime=go
browser_live_mic=go
real_mic_recorded_realtime=no_go optional auxiliary lane
```

- P2 packaged DOM/截图证据、`.dmg`、签名、公证、Windows 实机仍未完成，因此不能宣称完整 P0-P2 目标已完成。

2026-07-09 DMG skip-Finder 打包追加：

- Tauri 默认 DMG 阶段的 blocker 仍是 Finder AppleScript 美化步骤。
- 已新增项目级工具 `tools/package_macos_dmg_skip_finder.py`，用 Tauri 生成的 `bundle_dmg.sh --skip-jenkins` 绕过 Finder AppleScript，产出本地开发验收 DMG：

```text
tests/test_package_macos_dmg_skip_finder.py=3 passed, 1 warning
artifact=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/evidence.json
dmg=artifacts/tmp/desktop_dmg_skip_finder_tool_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=9610afbd51c7c98e5887e9c354642b590381270011aacb3a4652e55af3794d94
dmg_mount_smoke_exit_code=0
mounted_app_codesign_verify_exit_code=0
```

- 该 DMG 仍不是公开发布安装包：`spctl` 对 app 和 DMG 均拒绝，因为没有 Developer ID 签名和公证。
- 当前 P2 状态更新为：`.app` 可构建、可启动、可本地 ad-hoc 重签；本地开发 DMG 可产出并通过挂载 smoke；packaged DOM/截图主链路、Developer ID 签名、公证、Windows 实机仍未完成。

2026-07-09 packaged app 重签后窗口复查：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-app-window-signed-dmg-20260709/evidence.json
process_observed=true
coregraphics_window_observed=true
app_codesign_verify_exit_code=0
spctl_app_exit_code=3
screenshot_status=blocked_by_macos_screen_capture_permission_or_window_capture_policy
counts_as_packaged_window_evidence=true
counts_as_packaged_dom_evidence=false
```

- 这证明 packaged `.app` 作为本地 ad-hoc 签名应用能启动并产生窗口。
- 这仍不证明 packaged DOM 或 packaged app 内实时会议主链路；需要 macOS 屏幕录制/辅助功能权限，或另建 Tauri WebView 内部探针。

2026-07-09 packaged WebView runtime probe 追加：

- 已采用 Tauri 内部探针绕过 macOS 截图/辅助功能权限限制，证明 packaged WebView 内 Workbench 页面和前端运行时可用。
- 关键修复：
  - `desktop_frontend_probe_runtime::repo_root()` 从 `CARGO_MANIFEST_DIR` 向上查找仓库根，避免写到 `code/artifacts/tmp/...`。
  - 探针不再只写单一 `latest.json`，而是保留：
    - `artifacts/tmp/desktop_frontend_probe_runtime/latest-page-load.json`
    - `artifacts/tmp/desktop_frontend_probe_runtime/latest-inline-dom.json`
    - `artifacts/tmp/desktop_frontend_probe_runtime/latest-workbench-runtime.json`
  - Workbench HTML 在 Tauri packaged 环境优先加载相对 `workbench.js`，Web 服务环境仍加载 `/static/workbench.js`。
  - Workbench bootstrap 改为先等待 `runtime_get_status` 设置 `apiBaseUrl`，再请求 `/audio/check` 和历史记录。

新增正式证据：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-webview-runtime-probe-20260709-backend-api/evidence.json
status=go_packaged_webview_runtime_probe
counts_as_packaged_runtime_probe_evidence=true
counts_as_packaged_dom_evidence=true
counts_as_packaged_backend_api_evidence=true
counts_as_packaged_screenshot_evidence=false
counts_as_packaged_mainline_evidence=false
packaged_workbench_loaded=true
packaged_inline_dom_selectors_present=true
packaged_workbench_runtime_connected=true
packaged_backend_api_connected=true
desktop_api_base_url=http://127.0.0.1:8765
desktop_status_text=桌面壳已连接
```

验证命令结果：

```text
Backend CORS for packaged Tauri origin:
OPTIONS /health with Origin tauri://localhost -> access-control-allow-origin: tauri://localhost

Packaged backend API probe:
latest-backend-api.health_ok=true
latest-backend-api.sessions_loaded=true
latest-backend-api.errors=[]

PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
  tests/test_packaged_frontend_probe_evidence.py \
  tests/test_workbench_desktop_runtime_probe.py \
  tests/test_desktop_tauri_scaffold.py

16 passed, 1 warning

PYTHONPATH=/tmp/meeting-copilot-short/code/web_mvp/backend:/tmp/meeting-copilot-short/code/core pytest -q \
code/web_mvp/backend/tests/test_workbench.py \
  tests/test_workbench_desktop_runtime_probe.py \
  tests/test_desktop_tauri_scaffold.py

75 passed, 2 warnings

focused no-cost regression:
187 passed, 2 warnings

cargo test desktop_frontend_probe_runtime
3 passed

cargo check
Finished `dev` profile successfully

cargo tauri build --bundles app
Finished 1 bundle at artifacts/tmp/desktop_tauri_target/release/bundle/macos/Meeting Copilot.app
```

边界：

- packaged DOM/runtime evidence 已完成，截图证据仍被 macOS 屏幕捕获权限阻止。
- packaged same-chain no-cost meeting flow 后续已完成，见下方 2026-07-09 packaged same-chain 更新。
- Developer ID 签名、公证、Gatekeeper、Windows 实机仍未完成。

2026-07-09 当前开发 DMG 刷新：

```text
artifact=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/evidence.json
status=go_development_dmg_not_public_release
dmg=artifacts/tmp/desktop_dmg_skip_finder_current_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg
dmg_sha256=f5e047cdf7f1cdd13d9777bccc0ac39e648b549c40b1eede539bf74929dac435
dmg_create_exit_code=0
dmg_mount_smoke_exit_code=0
mounted_app_codesign_verify_exit_code=0
spctl_app_exit_code=3
spctl_dmg_exit_code=3
counts_as_public_release_package=false
remaining_blockers=[
  developer_id_codesign_not_done,
  notarization_not_done,
  gatekeeper_rejects_unsigned_or_adhoc_artifacts,
  packaged_screenshot_evidence_still_missing
]
```

2026-07-09 packaged same-chain no-cost 更新：

```text
artifact=artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json
status=go_packaged_webview_runtime_probe
blockers=[]
counts_as_packaged_same_chain_no_cost_evidence=true
counts_as_packaged_mainline_evidence=true
packaged_same_chain_flow_complete=true
packaged_same_chain_suggestion_card_count=3
packaged_same_chain_approach_card_count=1
same_chain_blockers=[]
remaining_blockers=[
  developer_id_signing_not_done,
  notarization_not_done,
  gatekeeper_acceptance_not_done,
  windows_real_machine_not_verified
]
```

该更新证明 packaged `.app` WebView 内可以跑通：

```text
mock ASR session
-> events snapshot
-> Workbench transcript render
-> deterministic no-cost suggestion cards
-> deterministic no-cost approach cards
-> deterministic no-cost minutes
-> history visible
-> delete session
-> history removed
```

边界：

- 该 self-probe 只在 `MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE=1` 时运行。
- 该模式不调用远端 ASR/LLM，不读 `.env`，不消耗中转站费用。
- 该模式不是生产真实 LLM 证据，也不是生产真实麦克风证据。

## 5.1 2026-07-09 Release External Preflight 更新

当前 P2 public release blocker 已通过机器可读 evidence 固定：

```text
artifact=artifacts/tmp/release_external_preflight_20260709/evidence.json
status=blocked_external_release_requirements
counts_as_packaged_same_chain_no_cost_evidence=true
counts_as_public_release_package=false
macos_public_release.ready=false
notarization.tooling_ready=true
notarization.notarization_completed=false
developer_id_application_count=0
developer_id_installer_count=0
notarytool_available=true
notary_profile_provided=false
spctl_app_exit_code=3
spctl_dmg_exit_code=3
windows_real_machine_verified=false
remaining_blockers=[
  developer_id_signing_not_done,
  notarization_not_done,
  gatekeeper_acceptance_not_done,
  windows_real_machine_not_verified
]
visual_evidence.packaged_screenshot_requirement_status=waived_by_internal_dom_runtime_probe
```

已完成：

- [x] release external preflight 工具和测试已落地。
- [x] packaged same-chain no-cost evidence 被继续接受。
- [x] development DMG 未被误算为 public release package。
- [x] 工具未读取 secret、未读取 keychain password、未提交 notarization、未调用远程服务。
- [x] packaged screenshot 缺失已重分类为 visual QA gap，并由 internal DOM/runtime/same-chain probe 豁免 public release blocker。
- [x] external preflight 已消费 Mac public release runner evidence；notary profile 可用不会误清除 `notarization_not_done`。

仍未完成：

- [ ] Apple Developer ID Application/Installer 签名身份。
- [ ] notarytool profile、notarization、staple。
- [ ] Gatekeeper acceptance。
- [ ] Windows 真机 verification。

## 5.2 2026-07-09 Mac Public Release Runner 更新

当前 Mac public release 已从人工步骤收敛为可执行 runner：

```text
tool=tools/macos_public_release_runner.py
test=tests/test_macos_public_release_runner.py
artifact=artifacts/tmp/macos_public_release_20260709/evidence.json
status=blocked_public_release_execution_requirements
remaining_blockers=[
  developer_id_signing_not_done
]
developer_id_application_count=0
notarytool_available=true
notary_profile_provided=true
counts_as_public_release_package=false
executed_mutating_command_count=0
privacy_safety.notarization_submitted=false
privacy_safety.remote_service_called=false
```

已完成：

- [x] 有 Developer ID 和 notary profile 时的完整命令链已由 TDD 覆盖。
- [x] 无 Developer ID 时不会执行 `ditto`、`codesign`、`bundle_dmg.sh`、`notarytool submit`、`stapler` 或 `spctl`。
- [x] 当前机器真实运行已生成 blocked evidence。

仍未完成：

- [ ] 本机可用 Developer ID Application identity。
- [ ] 实际 signed DMG。
- [ ] 实际 notarization / staple。
- [ ] 实际 Gatekeeper acceptance。

## 5.3 2026-07-09 Windows Real-Machine Validator 更新

当前 Windows 真机验证已经有只读 intake/validator：

```text
tool=tools/windows_real_machine_verification.py
test=tests/test_windows_real_machine_verification.py
artifact=artifacts/tmp/windows_real_machine_verification_20260709/evidence.json
status=blocked_windows_real_machine_verification
windows_real_machine_verified=false
remaining_blockers=[
  windows_real_machine_not_observed,
  windows_host_os_not_observed,
  windows_tauri_webview_not_verified,
  windows_backend_health_not_verified,
  windows_workbench_not_verified,
  windows_provider_health_not_verified,
  windows_microphone_permission_path_not_verified,
  windows_realtime_mainline_not_verified,
  windows_file_import_export_not_verified,
  windows_installer_or_portable_launch_not_verified,
  windows_delete_not_verified,
  windows_secret_redaction_not_verified
]
```

已完成：

- [x] Windows GO evidence contract 已定义。
- [x] external preflight 只接受 validator GO evidence，不接受裸布尔值。
- [x] 当前 Mac 环境已生成 blocked evidence，明确未跑 Windows。

仍未完成：

- [ ] Windows 真机 Tauri/WebView。
- [ ] Windows backend health / Workbench / provider health。
- [ ] Windows 麦克风权限路径。
- [ ] Windows 实时 ASR 到建议、纪要、历史、删除主链路。
- [ ] Windows 录音导入导出。
- [ ] Windows installer/portable launch smoke。
- [ ] Windows delete 和 secret redaction evidence。

## 10. 2026-07-09 录音保存闭环追加

本轮把“会议录音必须保存下来方便后续复盘”从文档缺口补成代码能力：

- [x] 实时麦克风 `browser_live_mic` WebSocket 音频同步写成本地 WAV。
- [x] live ASR session 记录关联 `audio` 元数据：相对路径、格式、采样率、声道数、时长、文件大小、sha256、来源类型、保存策略。
- [x] 导入录音文件保存原始上传音频为本地 audio asset。
- [x] `/live/asr/sessions/{session_id}/events` 返回 `audio` 元数据。
- [x] `/live/asr/sessions` 历史摘要返回 `has_audio`。
- [x] `/live/asr/sessions/{session_id}/audio.wav` 可导出录音。
- [x] Workbench 提供“导出录音”按钮，历史列表显示“已保存录音”。
- [x] 删除 live ASR session 会先删除关联录音文件，再删除 session record。
- [x] 旧 `/sessions/{session_id}` 兼容删除路径也会清理关联录音。

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py::test_asr_stream_browser_live_mic_source_is_acceptance_lane \
  code/web_mvp/backend/tests/test_file_convert.py::test_file_converted_session_saves_uploaded_recording_for_review \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_has_recording_export_buttons_and_download_handlers \
  code/web_mvp/backend/tests/test_app.py::test_delete_asr_live_session_reports_exact_delete_scope_without_overclaiming

4 passed, 2 warnings
```

仍需后续验证：

- [ ] 20-60 分钟长会议录音文件写入 soak。
- [ ] 真实多人自然会议的回放复盘人工验收。
- [ ] Windows 真机录音保存、导出和删除。

## 10.1 2026-07-09 真实浏览器麦克风 5 分钟长录音结果

本轮按用户授权，在当前外部环境有声音的情况下执行可见 Chrome + 真实浏览器麦克风长录音。为避免额外中转站费用，本轮建议/纪要派生使用 no-cost deterministic 自测路径，不调用远程 LLM，也不上传原始音频。

命令策略：

```text
script=code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
input_mode=real_browser_mic
ui_coverage=visible_chrome
record_seconds=300
derivation_mode=no_cost_deterministic
delete_session_after_run=false
artifact=artifacts/tmp/browser_live_mic/long-real-mic-20260709-213403/
```

录音与导出证据：

```text
session_id=rec_mrdjspjy
health_status=audio_capture_health_passed
sample_count=4796416
chunk_count=1000
rms=0.07906509767883087
active_sample_ratio=0.5536681972539497
audio_export_http_status=200
audio_file_magic=RIFF
audio_file_size_bytes=9592876
duration_seconds=299.776
sample_rate_hz=16000
channels=1
audio_sha256_matches_session=true
```

结论：

- [x] 真实浏览器麦克风入口能连续录制约 5 分钟。
- [x] 实时录音能保存为本地 WAV，并与同一 live ASR session 关联。
- [x] `/live/asr/sessions/{session_id}/audio.wav` 导出成功，导出文件 sha256 与 session 元数据一致。
- [x] Workbench 同一 session 可见，前端无 console/network 错误。
- [x] 本轮未调用远程 LLM，中转站费用为 0。
- [ ] 本轮不算 20-60 分钟生产 soak，只能算 5 分钟 longer-than-smoke 录音 soak。
- [ ] 本轮不算真实技术会议建议验收：外部环境声被 ASR 转写为非技术会议内容，语义质量 gate 返回 `blocked`。
- [ ] no-cost deterministic 卡片只证明同一条 UI/派生链路可运行，不代表生产建议质量；当 ASR semantic quality blocked 时，建议卡不得被计入生产 Go 证据。

后续需要：

- 使用清晰中文技术会议音频或用户真实会议配合，验证 `ASR final/revision -> EvidenceSpan -> 真实建议卡 -> 纪要 -> 导出/删除`。
- 拉长到 20-60 分钟时增加磁盘增长、内存/RSS、卡片频率、WebSocket 稳定性和停止后元数据写入检查。
- 优化 no-cost/demo 模式的 UI 标识：ASR 语义 blocked 时，演示卡片必须明显标记为演示，不得让用户误以为是真实建议。

## 10.2 2026-07-09 中文会议识别优化第一阶段

本阶段先修“识别质量不足时的产品可信度”和“中文技术会议词表召回”，暂不默认引入任何额外收费 ASR。

已完成：

- [x] 真实/准真实输入 session 若 `asr_semantic_quality_blocked`，no-cost deterministic 派生不再持久化正式建议、方案分析或纪要。
- [x] 后端返回 `demo_no_cost_quality_blocked`、`derivation_blocked=true`、`degraded=true` 和 blockers，方便前端和证据报告区分。
- [x] Workbench 遇到 blocked 派生时显示“识别语义质量不足，先不生成正式建议”，不再显示“生成成功/复盘已生成”。
- [x] 中文技术会议语义词表扩展：数据库、Redis、Kafka、缓存、连接池、P95、SLA、QPS、限流、幂等、排期、值班等。
- [x] 保留 mock demo 样例能力，不影响 packaged no-cost demo。

验证：

```text
test_packaged_no_cost_demo_derivation.py=3 passed
test_workbench.py::test_workbench_handles_quality_blocked_demo_derivation_without_success_copy=1 passed
test_asr_semantic_quality.py=5 passed
node --check workbench.js=passed
```

仍需后续：

- [ ] VAD/静音切句/长句强切优化，提升实时 final 频率。
- [ ] 本地中文实时 ASR provider bake-off：sherpa baseline vs FunASR/Paraformer/SenseVoice 类本地方案。
- [ ] 清晰中文技术会议音频验证建议卡证据绑定，不再使用环境噪声作为产品 Go 证据。

## 10.3 2026-07-09 中文长会议停止后切段优化

状态更新：2026-07-10 起被 10.18 修订。当前实现不再把 END 后修正文案替换为 `corrected_full_*` final；为保护实时 evidence/clickback，原始 `transcript_final` 保留，会后修正作为 `transcript_revision` 追加。

本阶段处理上一轮 5 分钟长录音暴露出的“停止后只剩 1 条超长 final”问题。它不增加费用，也不更换 ASR 模型；目标是让会后复盘、证据片段和建议卡输入有可用粒度。

已完成：

- [x] `corrected_full` 长中文文本按句末标点切成多个 `transcript_final`。
- [x] 单句过长时按逗号/顿号和最大长度继续切分。
- [x] 多段 segment id 稳定为 `corrected_full_001`、`corrected_full_002` 等。
- [x] 时间戳按文本长度比例分配，保证单调递增。
- [x] 短文本仍保留单条 `corrected_full`。

验证：

```text
test_asr_stream.py::test_asr_stream_splits_long_corrected_chinese_meeting_final_for_review_granularity=1 passed
test_asr_stream.py=12 passed
```

仍需后续：

- [ ] 会议中实时 endpoint/VAD 优化，让 final 不只在停止后出现。
- [ ] 对真实 browser mic 长录音重跑，确认历史 final_count 从 1 变成多段，并检查建议卡证据粒度。

## 10.4 2026-07-09 会中实时 VAD endpoint 优化

本阶段把“稳定 partial + 连续静音”提升为实时 final，目标是让会议中也能持续落可用 `transcript_final`，而不是只等停止后统一整理。

已完成：

- [x] WebSocket handler 计算浏览器上传 float32 PCM 的 RMS。
- [x] 稳定非空 partial 后连续静音达到 900ms 时，生成 `vad_endpoint_001` 这类实时 final。
- [x] VAD final 会立即发送给前端，并持久化到同一 live ASR session。
- [x] 空 partial、短文本和重复文本不会触发。
- [x] 不调用远程 ASR/LLM，不增加费用。

验证：

```text
test_asr_stream.py::test_asr_stream_promotes_stable_partial_to_realtime_final_after_silence=1 passed
test_asr_stream.py=13 passed
```

仍需后续：

- [ ] 用真实 browser mic + 清晰中文技术会议音频重跑，确认会中 final_count 增加。
- [ ] 结合自动建议链路验证 VAD final 是否能在会议中触发建议卡，而不只在会后触发。
- [ ] 继续做本地中文实时 ASR provider bake-off。

## 10.5 2026-07-09 真实浏览器麦克风中文技术会议 TTS 验证与 normalizer 优化

本阶段针对“目前识别效果是不是挺差”做主链路验证和小步修复：用真实浏览器麦克风收本机外放的清晰中文技术会议内容，验证本地 ASR、会话持久化、录音导出、页面整理链路，并把暴露出的中文技术误识写入 Web normalizer。

第一轮证据：

```text
artifact=artifacts/tmp/browser_live_mic/tech-vad-nocost-20260709-223621/
session_id=rec_mrdm0ttx
input_mode=real_browser_mic
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
health_status=audio_capture_health_passed
chunk_count=200
final_count=5
semantic_quality.status=passed
technical_entity_hit_count=29
technical_group_hit_count=6
acceptance_eligible=true
audio_export_http_status=200
audio_sha256_matches_session=true
derivation_mode=no_cost_deterministic
llm_called=false
counts_as_production_llm_evidence=false
```

第二轮证据：

```text
artifact=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/
session_id=rec_mrdm9vc5
input_mode=real_browser_mic
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
health_status=audio_capture_health_passed
chunk_count=200
final_count=3
semantic_quality.status=passed
technical_entity_hit_count=30
technical_group_hit_count=6
acceptance_eligible=true
audio_export_http_status=200
audio_sha256_matches_session=true
derivation_mode=no_cost_deterministic
llm_called=false
counts_as_production_llm_evidence=false
```

已完成：

- [x] 真实浏览器麦克风 + 本机中文 TTS 外放跑通 60 秒主链路。
- [x] 录音保存和 `/audio.wav` 导出继续通过，sha256 与 session 元数据一致。
- [x] 语义质量 gate 对清晰中文技术会议通过，覆盖 release/reliability/data/ownership/deadline/action 六类实体。
- [x] 页面能展示实时文字并生成 no-cost 建议、方案分析和纪要。
- [x] 针对真实输出补充 normalizer 测试和修复：`P95/P99/SLO/owner/Redis/Kafka/checkout-service/李四补监控看板`。
- [x] Web normalizer 增加中文字符间空格折叠，减少 `监 控`、`观 察` 这类复盘文本断词。

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_transcript_normalizer.py
8 passed, 1 warning

真实输出复验：
source_contains_unk=true
normalized_contains_unk=false
normalized_contains_cjk_split_spaces=false
contains_checkout_service=true
contains_redis=true
contains_kafka=true
contains_slo=true
contains_owner=true
```

仍未完成：

- [ ] 这两轮 `partial_count=0`、`vad_finals=0`，不能证明服务器 VAD partial 提升路径在真实 sherpa 下触发。
- [ ] no-cost deterministic 只证明 UI/派生链路，不证明生产 LLM 建议质量。
- [ ] 真实多人自然会议、系统音频输入源、长会性能和 provider bake-off 仍未完成。
- [ ] 需要比较 FunASR/SenseVoice/Paraformer 类本地中文方案，决定是否替换 sherpa 作为中文默认实时 provider。

## 10.6 2026-07-09 FunASR 本地 provider bake-off 探针

本阶段没有改默认 provider，只用 10.5 第二轮导出的真实 browser mic WAV 跑一次本地 FunASR file-replayed streaming 探针，判断它是否适合作为下一轮中文实时 ASR bake-off 候选。

证据：

```text
source_audio=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/exported-audio.wav
provider=funasr
mode=file_replayed_streaming_events
model_id=paraformer-zh-streaming
model_resolution=local_model_dir
model_download_status=not_performed
safe_to_download_models=false
hotword_status=enabled
hotword_count=18
audio_duration_seconds=59.904
latency_ms=41925
rtf=0.69987
partial_event_count=69
final_event_count=16
semantic_quality.status=passed
technical_entity_hit_count=26
technical_group_hit_count=6
artifact=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-provider.json
events=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-events.json
```

结论：

- [x] 本地 FunASR streaming 能离线运行，未执行模型下载，未调用远程 ASR。
- [x] FunASR 生成了更多 partial/final，说明它有实时分段潜力。
- [ ] 当前不能替换 sherpa 默认：同一段音频上文本误识更多，例如 `tap er`、`t九五`、`t一九`、`LO板`、`安尔`。
- [ ] 当前 CPU RTF 约 0.70，性能风险明显高于 sherpa baseline。

下一步：

- [x] 建立正式 provider result report 脚本：同一真实音频、同一指标、统一输出 provider 风险和默认 provider 决策。
- [ ] 指标至少包括：术语保真、`partial_count/final_count`、首字延迟、final 延迟、RTF、内存、CPU、是否远程下载/调用、是否可稳定长会运行。
- [ ] 在不增加额外收费的前提下继续评估本地 Paraformer/SenseVoice/FunASR 参数组合。

## 10.7 2026-07-09 Provider result report 脚本化汇总

本阶段把 DEC-273/DEC-274 的人工结论沉淀为可重复脚本，避免后续 provider 选择继续靠口头判断。

新增：

```text
code/asr_bakeoff/asr_bakeoff/provider_result_report.py
code/asr_bakeoff/tests/test_provider_result_report.py
```

能力：

- [x] 读取 live ASR `session_events.json`。
- [x] 读取普通 provider JSON，例如 FunASR streaming provider output。
- [x] 统一输出 provider、input kind、final/partial 数、语义质量、文本质量、latency/RTF、成本/隐私 flags。
- [x] 区分 `raw_contains_unk` 和 `normalized_contains_unk`，避免把已被 normalizer 修复的旧 raw artifact 误判成当前可见文本风险。
- [x] 输出默认 provider 决策：当前证据不足时 `replacement_allowed=false`，`recommended_action=keep_current_default`。
- [x] 输出实时性指标：`first_partial_received_at_ms`、`first_final_received_at_ms`、`final_latency_ms`、`final_interval_ms`。
- [x] 输出实时性风险：`first_final_after_10s`、`final_interval_above_15s`、`final_latency_above_5s`。
- [x] 输出资源指标：`max_rss_mb`、`peak_memory_footprint_mb`、`wall_seconds`、`cpu_time_ratio`。
- [x] 输出资源风险：`max_rss_above_2gb`、`cpu_time_ratio_above_2`。

真实报告：

```text
artifact=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/provider-result-report.json
candidate_count=2
semantic_pass_count=2
remote_asr_call_count=0
remote_llm_call_count=0
model_download_count=0
recommended_action=keep_current_default
replacement_allowed=false
blockers=[
  candidate_not_proven_on_natural_meeting,
  funasr_has_quality_or_latency_risk
]
sherpa_onnx_realtime.risk_flags=[no_partial_events]
funasr.risk_flags=[rtf_above_realtime_margin]
```

实时性指标：

```text
sherpa_onnx_realtime:
  first_final_received_at_ms=27000
  final_interval_ms.max_ms=27000
  final_latency_ms.max_ms=0
  risk_flags=[
    no_partial_events,
    first_final_after_10s,
    final_interval_above_15s
  ]

funasr:
  first_partial_received_at_ms=6850
  first_final_received_at_ms=7566
  final_interval_ms.p95_ms=1978
  final_latency_ms.p95_ms=4566
  max_rss_mb=2974.234375
  peak_memory_footprint_mb=2987.737396
  risk_flags=[
    rtf_above_realtime_margin,
    max_rss_above_2gb
  ]
```

验证：

```text
PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests/test_provider_result_report.py
4 passed, 1 warning

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests
21 passed, 1 warning
```

仍未完成：

- [ ] 该脚本还没有覆盖完整 UI 端到端 latency 和长会稳定性。
- [ ] sherpa resource 仍未按同口径测量。
- [ ] 当前只汇总 sherpa 与 FunASR 两个结果，尚未纳入 SenseVoice/其他 Paraformer 配置。
- [ ] 当前样本是本机 TTS 外放，不等于真实多人自然会议。

## 10.8 2026-07-09 Provider 实时性指标补齐

本阶段把 provider report 从“语义/数量/RTF”推进到“实时会议可用性”指标。

新增指标：

```text
realtime_metrics.first_partial_received_at_ms
realtime_metrics.first_final_received_at_ms
realtime_metrics.first_final_latency_ms
realtime_metrics.final_latency_ms.count/p50_ms/p95_ms/max_ms
realtime_metrics.final_interval_ms.count/p50_ms/p95_ms/max_ms
realtime_metrics.timing_source
```

真实报告结论：

- [x] sherpa 语义可过，但实时性弱：
  - `first_final_received_at_ms=27000`
  - `final_interval_ms.max_ms=27000`
  - `risk_flags=[no_partial_events, first_final_after_10s, final_interval_above_15s]`
- [x] FunASR 实时切分强，但 CPU 风险仍在：
  - `first_partial_received_at_ms=6850`
  - `first_final_received_at_ms=7566`
  - `final_interval_ms.p95_ms=1978`
  - `final_latency_ms.p95_ms=4566`
  - `risk_flags=[rtf_above_realtime_margin]`
- [x] provider JSON 自动关联 sibling events 文件：
  - `funasr-streaming-provider.json` -> `funasr-streaming-events.json`

验证：

```text
PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q \
  code/asr_bakeoff/tests/test_provider_result_report.py::test_provider_result_report_records_realtime_metrics_from_live_and_sibling_event_files
1 passed, 1 warning

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests/test_provider_result_report.py
4 passed, 1 warning
```

仍未完成：

- [ ] 首字延迟目前只在已有 events 中可见时统计；完整浏览器 UI 首字 latency 仍未覆盖。
- [ ] CPU/RSS 峰值、长会稳定性、自然多人会议样本仍未覆盖。

## 10.9 2026-07-09 Provider 资源指标补齐

本阶段把 provider report 从实时性推进到本机笔记本资源可用性。目标是避免某个 provider 虽然出字更密，但长期会议把本机内存/CPU 压垮。

新增能力：

- [x] 自动读取 sibling resource JSON：
  - `funasr-streaming-provider.json` -> `funasr-streaming-resource.json`
- [x] 统一记录：
  - `wall_seconds`
  - `user_cpu_seconds`
  - `system_cpu_seconds`
  - `cpu_time_ratio`
  - `max_rss_mb`
  - `peak_memory_footprint_mb`
- [x] summary 增加 `resource_measured_count`。
- [x] risk flags 增加：
  - `max_rss_above_2gb`
  - `cpu_time_ratio_above_2`

FunASR 资源探针：

```text
resource_artifact=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-resource.json
provider_output=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-provider.resource-run.json
events_output=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-events.resource-run.json
time_log=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/funasr-streaming-resource-time.log
command_exit_status=0
model_download_status=not_performed
provider_latency_ms=40566
provider_rtf=0.677183
wall_seconds=41.02
user_cpu_seconds=20.23
system_cpu_seconds=26.57
cpu_time_ratio=1.140907
max_rss_mb=2974.234375
peak_memory_footprint_mb=2987.737396
```

结论：

- [x] FunASR streaming 继续证明“分段能力更强”。
- [x] 但 FunASR 约 3GB RSS，对笔记本长会议是明确风险。
- [x] 当前不允许把 FunASR 设为默认 provider。
- [x] sherpa 已补同口径资源探针，约 186MB RSS、RTF≈0.016，默认本地 provider 的资源风险低。
- [ ] 需要 20-60 分钟长会资源曲线，不只是一段 60 秒样本。

验证：

```text
PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q \
  code/asr_bakeoff/tests/test_provider_result_report.py::test_provider_result_report_records_resource_metrics_from_sibling_resource_file
1 passed, 1 warning

PYTHONPATH=code/asr_bakeoff:code/web_mvp/backend:code/core pytest -q code/asr_bakeoff/tests/test_provider_result_report.py
5 passed, 1 warning
```

## 10.10 2026-07-09 中文识别现状判断和下一步优化收束

用户反馈“目前识别效果是不是挺差”。本节把当前证据归档，避免后续继续陷入无边界测评。

当前判断：

- [x] 当前不是“完全不可用”：60 秒中文技术会议语料能识别到发布、灰度、回滚、错误率、P95/P99、延迟、监控、数据库、缓存、负责人、回归测试等关键语义。
- [x] 当前也不能宣称生产级可用：真实会议体验会明显受“出字慢、分段粗、术语错、`<unk>`、无说话人区分、标点断句弱”影响。
- [x] 用户感知差的首要原因不是录音没保存，而是实时展示链路没有稳定给用户持续 partial 字幕。
- [x] sherpa 默认 provider 资源占用很低，适合笔记本默认本地 ASR，但中文技术术语准确率和实时分段体验不足。
- [x] FunASR 分段更密，partial/final 更多，但 60 秒样本约 3GB RSS，不适合作为默认 provider。

sherpa 同口径资源探针：

```text
source_audio=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/exported-audio.wav
provider_output=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/sherpa-streaming-provider.resource-run.json
events_output=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/sherpa-streaming-events.resource-run.json
resource_artifact=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/sherpa-streaming-resource.json
time_log=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/sherpa-streaming-resource-time.log
command_exit_status=0
provider_latency_ms=953
provider_rtf=0.015909
wall_seconds=1.07
user_cpu_seconds=1.52
system_cpu_seconds=0.05
max_rss_mb=185.96875
peak_memory_footprint_mb=154.110085
```

同一段音频的核心对比：

```text
sherpa:
  resource: light, max_rss_mb≈186, rtf≈0.016
  realtime UX risk: browser live run final_count=3, partial_count=0, first_final≈27s
  quality risk: <unk>、灰度/限流/幂等/Redis/Kafka/SLO/owner 等术语错漏

funasr:
  realtime UX: final_count=16, partial_count=69, first_partial≈6.85s
  resource risk: max_rss_mb≈2974, rtf≈0.68
  conclusion: optional bake-off provider only, not default
```

下一步只做三类优化，不再扩散：

- [ ] P0-CN-1 打通 sherpa partial 到浏览器实时字幕：离线回放已有 partial，真实浏览器链路必须显示持续 partial，首字可见目标小于 3 秒。
- [ ] P0-CN-2 做中文技术术语后处理：热词表、上下文纠错、`<unk>` 修复、P95/P99/SLO/owner/Redis/Kafka/checkout-service 等术语恢复。
- [ ] P0-CN-3 做低成本双轨策略：默认 sherpa 本地实时；FunASR 只作为用户显式开启的高资源实验 provider；远程 ASR 继续不默认启用。

验收口径：

- [ ] 真实浏览器麦克风或本机外放会议音频中，前端能连续看到 partial/final 文本。
- [ ] 同一 session 自动生成建议卡、纪要、行动项，并保存录音和历史。
- [ ] 报告必须同时包含识别质量、首字延迟、partial/final 数、录音保存、建议生成和资源指标。

## 10.11 2026-07-09 中文优化第一轮实现：partial 证据和术语纠错

本轮按 DEC-278 收束执行，只处理两件直接改善用户感知的问题：实时 partial 可见性、中文技术术语纠错。

实现内容：

- [x] WebSocket raw `partial/final/revision` event 统一补 `normalized_text`，前端优先展示 `normalized_text`。
- [x] 非空 partial 会进入同一 live session 事件流，空 partial 不持久化，避免页面和报告被空字幕污染。
- [x] finalize 阶段从 sherpa sidecar 队列 drain 出来的 partial 也会持久化，修复“WebSocket 收到过 partial，但 session 报告 partial_count=0”的问题。
- [x] `event_source` 增加 `partial_count`，报告可以直接看本次 session 是否真的有实时 partial。
- [x] sherpa/FunASR sidecar 保留 worker 原始 `segment_id`，只加 session 前缀，不再把所有事件覆盖成同一个 `stream_seg_<session>`。
- [x] 中文技术术语词典补真实 sherpa 近似错词：
  - `恢度` -> `灰度`
  - `款存` -> `缓存`
  - `往五` -> `王五`
  - `现流` -> `限流`
  - `密等` -> `幂等`
  - `确任` -> `确认`
  - `九五/九九` -> `P95/P99`
  - `<unk>看板` -> `SLO看板`
  - 真实上下文中的 Redis/Kafka/owner/checkout-service 恢复。

新增或更新测试：

- [x] `test_asr_stream_persists_non_empty_partial_before_final_with_normalized_text`
- [x] `test_asr_stream_persists_partial_events_drained_during_finalize`
- [x] `test_sherpa_sidecar_preserves_worker_segment_ids`
- [x] `test_normalize_recovers_current_sherpa_resource_probe_near_misses`
- [x] Workbench 静态合同测试更新为 raw event `e.normalized_text` fallback。

真实/准真实证据：

```text
artifact=artifacts/tmp/asr_cn_optimization/partial-persistence-sherpa-20260709-rerun
source_audio=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/exported-audio.wav
provider=sherpa_onnx_realtime
input_source=simulated_realtime_wav
ws_event_count=258
ws_non_empty_partial_count=55
ws_final_count=3
persisted_partial_count=3
persisted_final_count=5
event_source.partial_count=3
event_source.final_count=5
semantic_quality.status=passed
remote_llm_call_count=0
```

```text
artifact=artifacts/tmp/asr_cn_optimization/partial-persistence-sherpa-paced-20260709
source_audio=artifacts/tmp/browser_live_mic/tech-normalizer-nocost-20260709-224324/exported-audio.wav
provider=sherpa_onnx_realtime
input_source=simulated_realtime_wav
sent_chunks_before_end=90
ws_non_empty_partial_count=28
mid_persisted_partial_count=2
mid_persisted_final_count=1
final_persisted_partial_count=2
final_persisted_final_count=3
event_source.partial_count=2
event_source.final_count=3
semantic_quality.status=passed
```

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_sherpa_sidecar.py \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  code/web_mvp/backend/tests/test_workbench.py

135 passed, 2 warnings
```

边界：

- [ ] 这轮证明 partial 可以进入 session 和前端展示链路，但仍未完成真实浏览器 UI 截图级首字 latency 统计。
- [ ] 真实多人自然会议、噪声环境、长会议 20-60 分钟 soak 仍未完成。
- [ ] 术语纠错仍是保守词典，不能替代更完整的 ASR 模型评估或说话人分离。

## 10.12 2026-07-10 当前中文识别效果判断

本节回应“是否还有优化空间、当前识别效果是不是挺差”。结论必须分层，避免过度乐观或过度否定：

- [x] 不是完全不可用：在干净、主题明确的中文技术会议音频里，本地 sherpa 能抓住发布评审、灰度、回滚、错误率、P95/P99、延迟、监控、缓存、负责人、测试等主语义，并能驱动建议卡、纪要和历史链路。
- [x] 也不是生产级：真实外放、环境声、长会议、多人自然对话下会出现明显问题，包括长时间不出字、分段粗、`<unk>`、同音错词、标点断句弱、术语错漏、无说话人区分。
- [x] 用户感知“差”的关键原因不只是 ASR 模型本身，还包括实时 partial 可见性、首字延迟、断句策略和前端反馈不足。即使最终文本可用，会议中长时间看不到文字也会让产品价值下降。
- [x] 默认本地 ASR 仍选择 sherpa：资源占用低，适合笔记本默认免费运行；但必须通过热词、术语纠错、VAD/分段、UI 首字 latency 观测来补齐体验。
- [x] FunASR/Paraformer 类中文模型适合作为高质量候选或可选高资源 provider，不适合当前默认启用，因为已有资源探针显示 60 秒样本可能接近 3GB RSS。

当前证据对比：

```text
clean/controlled technical audio:
  result: can recover core meeting semantics
  examples: 灰度、回滚、P99、缓存、负责人、回归测试
  product chain: transcript -> cards -> minutes -> history can run

real speaker / ambient / long audio:
  result: unstable and not production-grade
  observed issues: irrelevant speech captured, <unk>, rough segmentation, missing partial visibility
  product risk: live suggestions may be late or based on noisy context
```

下一轮优化必须收束到可感知体验，不继续泛化横评：

- [x] CN-P0-1 前端增加首字、首个 partial、首个 final 可见 latency 指标，并在浏览器级自测产物中落盘。
- [ ] CN-P0-2 继续修实时 partial 展示：确保真实麦克风或模拟实时音频中，用户能连续看到临时文字，不等结束才出最终文字。
- [ ] CN-P0-3 强化本地低成本中文技术术语 normalizer，但只做保守纠错，不凭空补实体。
- [ ] CN-P0-4 增加音频预处理/VAD 分段策略，降低长段一次性 final 和环境声污染。
- [ ] CN-P0-5 对建议卡增加 ASR 质量感知：低置信或语义质量差时先提示“需要追问/确认”，而不是直接给确定性建议。
- [ ] CN-P1-1 保留 FunASR/Paraformer 为显式可选实验 provider，只有用户接受更高 CPU/RAM 或未来远程 ASR 费用时才启用。

当前发布判断：

```text
MVP demo / controlled pilot: conditionally usable
真实自然中文会议生产发布: not ready
默认路线: no-cost local sherpa + normalizer + UI latency/partial 优化
不默认增加收费 ASR
```

## 10.13 2026-07-10 UI 首字延迟指标和真实麦克风收外放证据

本轮把“页面是否真的及时出字”从主观体验变成浏览器级证据：

- [x] Workbench 暴露 `window.__meetingCopilotRealtimeUiMetrics()`。
- [x] 指标包含：
  - `first_text_visible_latency_ms`
  - `first_partial_visible_latency_ms`
  - `first_final_visible_latency_ms`
  - `partial_visible_count`
  - `final_visible_count`
  - `latest_partial_text_sample`
  - `latest_final_text_sample`
- [x] 浏览器自测脚本把指标写入 `ui_verification.json`。
- [x] `no_cost_deterministic` 浏览器自测启动后端时清空 `LLM_GATEWAY_BASE_URL`、`LLM_GATEWAY_API_KEY` 和 `LLM_GATEWAY_MODEL`，避免实时 ASR 修正或建议链路误调用中转站。

真实麦克风收本机外放自测：

```text
artifact=artifacts/tmp/browser_live_mic/cn-ui-latency-speaker-real-mic-nocost-isolated-20260710-004334
input_mode=real_browser_mic
ui_coverage=visible_chrome
audio_source=Mac speaker playback -> real browser microphone
provider=sherpa_onnx_realtime
provider_mode=real
asr_fallback_used=false
health_status=audio_capture_health_passed
chunk_count=117
event_source.partial_count=2
event_source.final_count=2
acceptance_eligible=true
asr_semantic_quality.status=passed
frontend_utterance_count=2
frontend_card_count=3
frontend_minutes_visible=true
derivation_mode=no_cost_deterministic
llm_called=false
llm_provider=deterministic_demo
gateway_base_url_kind=not_configured
counts_as_production_llm_evidence=false
audio_export_http_status=200
audio_sha256_matches_session=true
```

UI latency 结果：

```text
first_text_visible_latency_ms=10266
first_partial_visible_latency_ms=10266
first_final_visible_latency_ms=28954
partial_visible_count=33
final_visible_count=3
```

结论：

- [x] 当前页面已能在真实浏览器麦克风路径中显示 partial/final，并把首字/partial/final 延迟落盘。
- [x] 录音保存和导出链路成立：导出 WAV 与 session audio sha256 一致。
- [x] no-cost 自测不再调用远程 LLM/中转站。
- [ ] 首字约 10.3 秒、首个 final 约 29 秒，明显慢于实时会议助手目标；不能视为生产级体验。
- [ ] 识别文本仍有明显错词：如“技术会议/接口/灰度/回滚/缓存穿透/连接池”等部分语义被误识，说明 normalizer 和音频/VAD 仍需优化。
- [ ] Chrome fake-audio-file 路径本轮录到静音，不能作为 UI latency 成功证据；真实麦克风收外放路径才是本轮有效证据。

下一步收束：

- [ ] 优先降低首字/partial 可见延迟，目标先从约 10 秒压到 3 秒内。
- [ ] 调整 VAD/endpoint 策略，减少首个 final 接近 30 秒才出现的问题。
- [ ] 扩充保守术语纠错，覆盖本轮真实错词，但禁止凭空补不存在实体。

## 10.14 2026-07-10 Speech-active latency 修正和真实错词补丁

10.13 的首字可见指标从录音开始计时，适合表达用户点击后等待时间，但会被“点击开始后几秒才真正说话/播放测试音频”污染。本轮新增从首次有效麦克风声音开始计时的 speech-active latency，并修正了首次有效声音被环境噪声误触发的问题。

实现内容：

- [x] Workbench `realtime_ui_metrics` 增加：
  - `first_audio_active_at_epoch_ms`
  - `first_audio_active_offset_ms`
  - `first_text_after_audio_active_latency_ms`
  - `first_partial_after_audio_active_latency_ms`
  - `first_final_after_audio_active_latency_ms`
- [x] 首次有效声音不再由单点采样峰值触发，必须满足帧级：
  - `rms >= MIC_MIN_RMS`
  - `peak >= MIC_MIN_PEAK`
  - `activeFrameRatio >= MIC_MIN_ACTIVE_SAMPLE_RATIO`
- [x] 浏览器自测脚本导出上述字段到 `ui_verification.json`。
- [x] 对本轮真实麦克风错词增加保守词典修复，包括：
  - `是技术建立去些第合性一度外分之五` -> `我们开始技术会议，checkout-service 接口先灰度百分之五`
  - `零点一旧回<unk> 缓存穿透` -> `零点一就回滚。Redis 缓存穿透`
  - `需要网母今天处理科消费堆积` -> `需要王五今天处理。Kafka 消费堆积`
  - `数据控连坚持打满` -> `数据库连接池打满`
  - `限流合密等` -> `限流和幂等`
  - `消费堆积导致<unk> P95延迟升高` -> `消费堆积导致 P95延迟升高`

真实麦克风收本机外放自测：

```text
artifact=artifacts/tmp/browser_live_mic/cn-speech-active-latency-fixed-speaker-real-mic-nocost-20260710-010650
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
provider=sherpa_onnx_realtime
provider_mode=real
asr_fallback_used=false
acceptance_eligible=true
asr_semantic_quality.status=passed
frontend_utterance_count=3
frontend_card_count=3
frontend_minutes_visible=true
event_source.partial_count=3
event_source.final_count=3
audio_sha256_matches_session=true
llm_called=false
gateway_base_url_kind=not_configured
```

Latency 结果：

```text
first_audio_active_offset_ms=4114
first_text_visible_latency_ms=5151
first_partial_visible_latency_ms=5151
first_final_visible_latency_ms=9763
first_text_after_audio_active_latency_ms=1039
first_partial_after_audio_active_latency_ms=1039
first_final_after_audio_active_latency_ms=5649
partial_visible_count=37
final_visible_count=4
```

结论：

- [x] 页面“从有效声音到首字/partial 可见”已能做到约 1 秒，本轮证明“文字又没有了”的主因不在前端渲染。
- [x] 从点击开始到首字约 5.1 秒，其中约 4.1 秒是测试音频播放前/输入起点等待；新 speech-active 指标能区分这两类延迟。
- [ ] 首个 final 从有效声音开始仍约 5.6 秒，实时建议如果依赖 final 仍会偏慢。
- [ ] 文本可读性仍不够稳定，虽然保守词典已修本轮错词，但这不能替代更系统的中文 ASR/热词/说话人验证。
- [ ] 后续应优先让实时建议可基于稳定 partial 触发轻量确认类提示，而不是完全等 final。

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py

96 passed, 2 warnings

python3 -m json.tool configs/asr_terms.json
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
git diff --check -- <touched files>
```

## 10.15 2026-07-10 Partial 驱动的 no-cost 实时提醒

10.14 证明首字/partial 能较快出现，但首个 final 仍可能需要约 5-6 秒。如果实时建议完全依赖 final，会议中的辅助价值会被延迟拖慢。本轮新增 partial 驱动的本地轻量提醒，不调用远程 LLM，不生成正式建议卡，只在候选提醒区提示“需要确认/追问”。

实现内容：

- [x] `asr_live_events` 对工程风险/行动项/未闭环问题类 partial 生成 `partial_hint_event`。
- [x] `partial_hint_event` 的边界：
  - `hint_policy_version=partial-hint-policy.v1`
  - `hint_origin=local_deterministic_partial`
  - `llm_call_status=not_called`
  - `card_status=not_created`
  - `degradation_reasons=[partial_not_final, needs_confirmation]`
- [x] WebSocket 在 raw partial 后通过同一连接发送 `partial_hint_event`，避免等会议结束/刷新快照才出现提醒。
- [x] session 快照中也保留同一类事件，保证可追溯。
- [x] Workbench 在“实时建议/会议中提醒”区域展示 `partial_hint_event`，标题为“实时提醒”，并标注来源为临时文字。
- [x] 浏览器自测 `ui_verification.json` 增加：
  - `frontend_partial_hint_count`
  - `candidate_panel_text`

真实麦克风收本机外放自测：

```text
artifact=artifacts/tmp/browser_live_mic/cn-partial-hint-speaker-real-mic-nocost-20260710-013127
input_mode=real_browser_mic
ui_coverage=visible_chrome
health_status=audio_capture_health_passed
provider=sherpa_onnx_realtime
provider_mode=real
asr_fallback_used=false
acceptance_eligible=true
asr_semantic_quality.status=passed
frontend_utterance_count=2
frontend_card_count=3
frontend_partial_hint_count=1
frontend_minutes_visible=true
event_source.partial_count=3
event_source.final_count=2
partial_hint_count_in_session=1
llm_called=false
gateway_base_url_kind=not_configured
counts_as_production_llm_evidence=false
audio_sha256_matches_session=true
screenshot=artifacts/tmp/browser_live_mic/cn-partial-hint-speaker-real-mic-nocost-20260710-013127/workbench-browser-live-mic.png
```

页面候选区证据：

```text
实时提醒
实时识别到风险条件，建议确认触发阈值、回滚动作、监控口径和负责人。
临时文字 transcript_partial:rec_mrdsa1b8_sherpa_sidecar_002
```

Latency 结果：

```text
first_audio_active_offset_ms=2841
first_text_after_audio_active_latency_ms=4116
first_partial_after_audio_active_latency_ms=4116
first_final_after_audio_active_latency_ms=5640
partial_visible_count=33
final_visible_count=4
```

结论：

- [x] 实时提醒不再完全依赖 final；partial 已能触发本地、可追溯、no-cost 的确认类提醒。
- [x] 本轮没有调用中转站或远程 ASR。
- [x] partial hint 只作为“需要确认”的轻量提醒，不作为正式建议卡或最终结论。
- [ ] 本轮真实麦克风收外放的 speech-active 首字延迟波动到约 4.1 秒；需要更多样本/更稳定输入来判断是否稳定低于 3 秒。
- [ ] partial hint 的触发规则仍是启发式，后续需要补误触发/漏触发样本。

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py

139 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
git diff --check -- <touched files>
python3 -m json.tool artifacts/tmp/browser_live_mic/cn-partial-hint-speaker-real-mic-nocost-20260710-013127/ui_verification.json
python3 -m json.tool artifacts/tmp/browser_live_mic/cn-partial-hint-speaker-real-mic-nocost-20260710-013127/asr_probe.json
python3 -m json.tool artifacts/tmp/browser_live_mic/cn-partial-hint-speaker-real-mic-nocost-20260710-013127/session_events.json
```

## 10.16 2026-07-10 Partial hint 小样本误触发/漏触发评测

10.15 证明 partial hint 链路可用。本轮补一组小样本评测，防止“技术词 + 确认/风险”导致乱提醒，也防止风险/行动项/未闭环问题漏提醒。

评测样本：

```text
casual_chat:
  text=我们晚上吃什么，要不要点奶茶
  expected_hint_type=null

benign_engineering:
  text=接口负责人已经确认没有风险，监控看板也正常
  expected_hint_type=null

action_missing_details:
  text=请李四今天确认缓存窗口和回滚负责人
  expected_hint_type=action_confirmation

open_question:
  text=P99 延迟这个风险有没有 owner
  expected_hint_type=question_followup

risk_threshold:
  text=如果 P99 延迟超过九百毫秒就要回滚
  expected_hint_type=risk_confirmation
```

实现调整：

- [x] `question_followup` 优先级高于 `risk_confirmation`。例如“P99 延迟这个风险有没有 owner”应提示追问 owner，而不是泛化为风险确认。
- [x] 非工程闲聊不触发。
- [x] 已明确“没有风险/已确认”的工程上下文不触发。
- [x] 行动项、未闭环问题、风险阈值能触发对应类型。

评测报告：

```text
artifact=artifacts/tmp/partial_hint_eval/partial-hint-balance-20260710-013959/summary.json
case_count=5
passed_count=5
failed_count=0
remote_llm_called=false
remote_asr_called=false
```

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py

140 passed, 2 warnings

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
git diff --check -- <touched files>
python3 -m json.tool artifacts/tmp/partial_hint_eval/partial-hint-balance-20260710-013959/summary.json
```

边界：

- [ ] 5 个样本只算准入评测，不代表真实会议生产级 precision/recall。
- [ ] 仍需用更多中文技术会议 partial 样本覆盖噪声、错词、多人打断、非技术讨论穿插。

## 10.17 2026-07-10 中文实时提醒去重、错词准入与 normalizer 防注入

本轮针对“当前识别效果是不是挺差”的实际风险做收敛：承认本地 sherpa 对中文技术会议仍会有明显错词，但产品不能用 normalizer 把未明确听到的服务名、人名、中间件硬补出来伪装准确。实时助手的可用价值应来自“及时提醒需要确认”，不是把错词改写成看似完整的会议事实。

实现调整：

- [x] `partial_hint_event` 增加 `dedupe_key`，同一类连续 partial 只出一次提醒。
- [x] `build_asr_live_events()` 对 progressive partial 做 session 内语义去重，避免 `P99 延迟超过...` 随文字增长连续刷三张提醒。
- [x] WebSocket 实时推送同样按 `dedupe_key` 去重，避免页面实时候选区刷屏。
- [x] partial hint 评测集扩展到 14 条，覆盖：
  - 非工程闲聊；
  - 非技术打断；
  - 已闭环 action/risk；
  - 行动项、owner 问题、风险阈值；
  - 真实麦克风错词片段；
  - progressive partial duplicate。
- [x] 新增 `tools/partial_hint_eval_runner.py`，本地 no-cost 生成 `summary.json`，不调用远程 ASR/LLM。
- [x] `transcript_normalizer` 增加 protected entity guard：默认不得从模糊片段补出未在原文出现的 `checkout-service`、`Kafka`、`Redis`、`SLO`、`王五`、`李四`。
- [x] `configs/asr_terms.json` 将部分安全错词拆小，例如只修 `零点一旧回<unk> -> 零点一就回滚`，不顺手补 `Redis`。
- [x] final/revision 的候选抽取改用 normalized analysis text；原始 evidence quote 仍保留 raw ASR 文本，兼顾提醒召回和复盘可信度。

关键边界：

- [x] 真实会议文字稿仍应保留 raw text + normalized text 两层语义。
- [x] normalizer 可以修稳定术语，例如 `P 九九 -> P99`、`先恢度 -> 先灰度`、`限流合密等 -> 限流和幂等`。
- [x] normalizer 不应把 `<unk>` 或错词强行补成具体服务名、人名、中间件。
- [x] partial hint 仍只是“实时提醒/需要确认”，不等于正式建议卡，不等于最终会议事实。
- [ ] 这轮没有提升底层 ASR 模型的逐字准确率，只降低错词对实时提醒和复盘可信度的伤害。
- [ ] 仍需后续用更长真实会议/多人说话/环境噪声验证。

评测报告：

```text
artifact=artifacts/tmp/partial_hint_eval/partial-hint-dedupe-normalizer-guard-20260710-091138/summary.json
status=passed
case_count=14
passed_count=14
failed_count=0
precision=1.0
recall=1.0
progressive_duplicate_hint_count=1
remote_llm_called=false
remote_asr_called=false
```

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  tests/test_partial_hint_eval_runner.py

148 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core python3 tools/partial_hint_eval_runner.py \
  --output-dir artifacts/tmp/partial_hint_eval/partial-hint-dedupe-normalizer-guard-20260710-091138

python3 -m py_compile \
  tools/partial_hint_eval_runner.py \
  code/web_mvp/backend/meeting_copilot_web_mvp/asr_live_events.py \
  code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py \
  code/web_mvp/backend/meeting_copilot_web_mvp/transcript_normalizer.py

node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js
node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs
git diff --check -- <touched files>
```

当前结论：

- [x] 当前识别效果不能说生产级；中文技术词、英文缩写、多人/噪声场景仍有明显风险。
- [x] 但实时助手主价值不应等同于“完美文字稿”：本轮已把错词 partial 转成轻量确认提醒，并防止重复/误补实体。
- [x] 下一步主线应做更真实的长会议自测和 evidence clickback 稳定性，避免继续只扩局部样本。

## 10.18 2026-07-10 END 后修正改为 revision，保护实时证据链

10.17 后继续处理“实时建议 + 会后复盘”的可信度问题。旧逻辑在 `END` 后会把累积 final 拼接、修正、重切为 `corrected_full_*` 并替换 session events；这样实时阶段已经产生的 evidence id 可能在会后记录里消失，导致建议卡/执行预览无法点回原始会议片段。

实现调整：

- [x] `asr_stream` 保留实时阶段的原始 `transcript_final` 和原始 evidence span。
- [x] `END` 后 L2/L3 修正文案不再替换 `accumulated_finals`。
- [x] 若修正文案与原始 final 不同，生成 `transcript_revision`，字段包含：
  - `revision_of`
  - `supersedes_segment_id`
  - `superseded_evidence_spans`
  - 新 revision evidence span。
- [x] 长修正文案仍会按句/逗号切分，但切分结果作为多个 revision，而不是多个替换 final。
- [x] `/live/asr/sessions/{id}/llm-execution-previews` 能继续读到原始 evidence span，证明会后复盘/建议预览仍可追溯到实时原话。

关键边界：

- [x] 会后修正可以改善文字可读性，但不能抹掉会议中实时捕获的原始证据。
- [x] 原始 evidence quote 保留 raw ASR 文本；revision 表达“后来修正怎么看”。
- [x] 这轮没有解决完整的 revision 后建议卡失效/替换策略，只保证原始 evidence 不丢失。
- [x] 前端截图级验证已补：建议卡原始 evidence-link 点击后能高亮原始 transcript 行；revision evidence-link 点击后能高亮 `.utterance.transcript-revision` 修正文稿行，并展示 `revision_of/supersedes_segment_id` 关系。

验证：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_asr_stream.py::test_asr_stream_end_correction_preserves_original_final_evidence_and_adds_revision

1 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q code/web_mvp/backend/tests/test_asr_stream.py

18 passed, 2 warnings

PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_live_events.py \
  code/web_mvp/backend/tests/test_asr_stream.py \
  code/web_mvp/backend/tests/test_workbench.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py \
  tests/test_partial_hint_eval_runner.py

149 passed, 2 warnings
```

当前结论：

- [x] 会后修正不再破坏实时 evidence/clickback 的基本可追溯性。
- [x] 产品主线更接近“实时会议 + 实时建议 + 录音/文字复盘”而不是单纯停止后重写全文。
- [x] 模拟 revision 会话已完成浏览器截图验证 evidence-link UI 行为。
- [ ] 仍需真实/长会议浏览器验证同一交互在更长上下文、更多建议卡下是否稳定。

## 10.19 2026-07-10 Workbench 渲染 transcript_revision 并验证原始/修正 evidence 点击回看

10.18 已把 END 后修正从“替换 final”改为“追加 revision”，但前端复审发现 Workbench 只渲染 `transcript_final/final`，没有把 `transcript_revision` 作为可点击回看的转写行展示。这样建议卡里即使存在 revision evidence link，也只能证明链接文字可见，不能证明用户能定位“AI 修正了哪一句”。

实现调整：

- [x] `renderTranscriptAndCandidates()` 支持 `transcript_revision`，渲染为 `.utterance.transcript-revision`。
- [x] `appendLiveEvent()` 支持实时收到 `transcript_revision` 后追加修正行，不替换原始 `transcript_final`。
- [x] revision 行携带：
  - `data-segment-id`
  - `data-evidence-ids`
  - `data-revision-of`
  - `data-supersedes-segment-id`
- [x] 复用已有 `focusEvidenceSpan()`，原始 evidence 和 revision evidence 点击后都能高亮对应 `.utterance`。
- [x] 页面用“修正：”和“修正原话：<segment_id>”区分修正文稿，避免把 ASR 原文和 END 后修正文案混成同一条事实。
- [x] `sessionHasTranscript()` 和删除确认的文字条数统计纳入 `transcript_revision`，避免 UI 可见行和会话状态判断不一致。

验证证据：

```text
artifact_root=artifacts/tmp/ui_screenshots/workbench-revision-evidence-clickback-20260710-094808
report=artifacts/tmp/ui_screenshots/workbench-revision-evidence-clickback-20260710-094808/revision_evidence_clickback_report.json
status=go_revision_evidence_clickback
utterance_count=2
revision_utterance_visible=true
original_evidence_link_visible=true
revision_evidence_link_visible=true
original_clickback.focused_segment_id=raw_release_revision_clickback
revision_clickback.focused_segment_id=raw_release_revision_clickback_rev1
screenshot_count=5
remote_llm_called=false
remote_asr_called=false
```

截图清单：

- `01-initial_page.png`
- `02-revision_loaded.png`
- `03-original_evidence_clickback.png`
- `04-revision_evidence_clickback.png`
- `05-revision_relationship_visible.png`

验证命令：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  code/web_mvp/backend/tests/test_workbench.py::test_workbench_renders_transcript_revision_rows_for_evidence_clickback \
  tests/test_workbench_revision_evidence_clickback.py

3 passed, 2 warnings

node --check code/web_mvp/e2e/workbench_revision_evidence_clickback.mjs
node --check code/web_mvp/backend/meeting_copilot_web_mvp/frontend_static/workbench.js

MEETING_COPILOT_ARTIFACT_ROOT=artifacts/tmp/ui_screenshots/workbench-revision-evidence-clickback-20260710-094808 \
  node code/web_mvp/e2e/workbench_revision_evidence_clickback.mjs

status=go_revision_evidence_clickback
screenshot_count=5
remote_llm_called=false
remote_asr_called=false
```

当前结论：

- [x] 原始 ASR 证据和 END 后修正证据现在都可以在 Workbench 中可视化、点击定位和截图留痕。
- [x] 这一步保护了“实时建议可追溯”的产品主线，避免 END 后修正变成黑箱。
- [ ] 这不是生产级 ASR 质量结论；中文长会议识别质量、多人噪声、真实麦克风连续录音仍需继续按主链路验证。

## 10.20 2026-07-10 长会议 Workbench 主链路承载自测（fixture-only）

为避免继续只在短样本上验证 evidence clickback，本轮新增一个无成本、fixture-only 的长会议 UI 自测。它不启动真实麦克风、不读取用户真实录音、不调用远程 ASR/LLM；目标是证明 Workbench 能承载更长中文技术会议上下文中的多条转写、多张建议卡、revision、方案、纪要、导出和删除。

实现调整：

- [x] 新增 `code/web_mvp/e2e/workbench_long_meeting_evidence_verify.mjs`。
- [x] 脚本直接写入临时 `MEETING_COPILOT_DATA_DIR/live_asr_sessions/{session_id}.json`，复用生产 `/live/asr/sessions/{id}/events` 和 Workbench 历史加载链路。
- [x] 固定空网关配置：
  - `LLM_GATEWAY_BASE_URL=""`
  - `LLM_GATEWAY_API_KEY=""`
  - `LLM_GATEWAY_MODEL=""`
- [x] fixture 覆盖：
  - 20 分钟语义跨度（非 wall-clock soak）
  - 12 条 `transcript_final`
  - 1 条 `transcript_revision`
  - 4 张 suggestion card
  - 2 张 approach card
  - 1 份会议纪要
  - 8 个 evidence link
- [x] 浏览器步骤覆盖：
  - 历史记录打开长会议
  - 普通 evidence clickback
  - revision evidence clickback
  - 方案/纪要可见
  - 导出文字稿/纪要下载目标
  - 删除后 UI 重置且后端会话 404

验证证据：

```text
artifact_root=artifacts/tmp/ui_screenshots/workbench-long-meeting-evidence-20260710-095836
report=artifacts/tmp/ui_screenshots/workbench-long-meeting-evidence-20260710-095836/long_meeting_ui_report.json
status=go_long_meeting_ui_evidence
synthetic_duration_minutes=20
counts_as_20_60_min_production_soak=false
counts_as_real_mic_go_evidence=false
remote_llm_called=false
remote_asr_called=false
utterance_count=13
revision_utterance_count=1
suggestion_card_count=4
approach_card_count=2
evidence_link_count=8
minutes_visible=true
original_clickback.focused_segment_id=long_seg_03
revision_clickback.focused_segment_id=long_seg_06_rev1
delete_probe_status=404
screenshot_count=7
```

截图清单：

- `01-initial_page.png`
- `02-long_session_loaded.png`
- `03-original_evidence_clickback.png`
- `04-revision_evidence_clickback.png`
- `05-minutes_and_approach_visible.png`
- `06-exports_verified.png`
- `07-delete_reset.png`

验证命令：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_workbench_long_meeting_evidence.py

2 passed, 1 warning

node --check code/web_mvp/e2e/workbench_long_meeting_evidence_verify.mjs

MEETING_COPILOT_ARTIFACT_ROOT=artifacts/tmp/ui_screenshots/workbench-long-meeting-evidence-20260710-095836 \
  node code/web_mvp/e2e/workbench_long_meeting_evidence_verify.mjs

status=go_long_meeting_ui_evidence
screenshot_count=7
```

当前结论：

- [x] Workbench 不再只证明短句 evidence clickback；它可以在同一长会议会话中展示多转写、多建议、revision、方案、纪要、导出和删除。
- [x] 这一步证明的是 UI/会话层主链路承载能力，不证明 ASR 质量、真实麦克风、真实长时间内存稳定性或生产 LLM 质量。
- [ ] 仍需后续真实/公开音频或真实麦克风的长时间 wall-clock soak，覆盖录音持续写入、真实 ASR 延迟、内存增长和建议频率控制。

## 10.21 2026-07-10 真实 ASR 产物主链路质量报告：pipeline 可闭合，但中文识别未达生产级

10.20 证明了 Workbench 能承载长会议 UI，但它仍是 fixture-only。为继续靠近真实识别，本轮复用已有 `release-review-001` 的 FunASR/Sherpa ASR event 和 transcript report，不重新跑模型、不读取用户私有录音、不调用远程 ASR/LLM，验证两个问题：

1. ASR event 能否闭合到 `EvidenceSpan -> state -> suggestion candidate -> llm_request_draft` 主链路。
2. ASR 文本质量是否足以支撑生产级中文技术会议建议。

新增工具：

- [x] `tools/asr_mainline_quality_report.py`
- [x] `tests/test_asr_mainline_quality_report.py`

回放证据：

```text
funasr_replay=artifacts/tmp/asr_reports/release-review-001.funasr.live-pipeline-replay-20260710.json
funasr_replay_status=asr_events_replayed_to_live_pipeline
funasr_input_events=partial:19 final:4 revision:0 error:0 end_of_stream:1
funasr_live_events=transcript_partial:19 transcript_final:4 state_event:2 scheduler_event:2 suggestion_candidate_event:2 llm_request_draft_event:2
funasr_evidence_span_count=4
funasr_first_partial_latency_ms=3733
funasr_first_final_latency_ms=5358
funasr_llm_statuses=["not_called"]

sherpa_replay=artifacts/tmp/asr_reports/release-review-001.sherpa.live-pipeline-replay-20260710.json
sherpa_replay_status=asr_events_replayed_to_live_pipeline
sherpa_input_events=partial:17 final:1 revision:0 error:0 end_of_stream:1
sherpa_live_events=transcript_partial:17 transcript_final:1 partial_hint_event:2 state_event:2 scheduler_event:2 suggestion_candidate_event:2 llm_request_draft_event:2
sherpa_evidence_span_count=1
sherpa_first_partial_latency_ms=273
sherpa_first_final_latency_ms=386
sherpa_llm_statuses=["not_called"]
```

质量报告：

```text
report=artifacts/tmp/asr_reports/release-review-001.asr-mainline-quality-20260710.json
schema_version=asr_mainline_quality_report.v1
sample_id=S02-release-review
provider_count=2
pipeline_closed_count=2
quality_pass_count=0
remote_asr_call_count=0
llm_call_count=0
decision_status=no_go_quality_not_production
blocker=pipeline_closed_but_asr_quality_insufficient
best_quality_provider=funasr_streaming
```

Provider 结论：

- [x] FunASR：主链路闭合，4 个 final、4 个 evidence span、2 个 suggestion candidate；术语召回 `0.5`，缺失 `payment-gateway / 800ms / 0.1% / Grafana`，CER `0.941176`，RTF `0.984067`。
- [x] Sherpa：主链路闭合，延迟更低；但只有 1 个 final、`<unk>` 多，术语召回 `0.375`，缺失 `payment-gateway / 800ms / 错误率 / 0.1% / Grafana`，CER `0.926471`。
- [x] 两者都未调用远程 ASR/LLM。
- [x] 两者都不能作为当前生产级中文 ASR 质量 Go 证据。

验证命令：

```text
python3 tools/asr_live_pipeline_replay.py \
  --events-path artifacts/tmp/asr_events/release-review-001.funasr.events.json \
  --provider funasr_streaming \
  --session-id release-review-001-funasr-replay \
  > artifacts/tmp/asr_reports/release-review-001.funasr.live-pipeline-replay-20260710.json

python3 tools/asr_live_pipeline_replay.py \
  --events-path artifacts/tmp/asr_events/release-review-001.sherpa.events.json \
  --provider sherpa_onnx_streaming \
  --session-id release-review-001-sherpa-replay \
  > artifacts/tmp/asr_reports/release-review-001.sherpa.live-pipeline-replay-20260710.json

python3 tools/asr_mainline_quality_report.py \
  --sample-id S02-release-review \
  --reference data/asr_eval/references/S02-release-review.txt \
  --annotation data/asr_eval/annotations/S02-release-review.annotation.json \
  --provider funasr_streaming \
  --transcript-report artifacts/tmp/asr_reports/release-review-001.funasr.transcript-report.json \
  --pipeline-replay-report artifacts/tmp/asr_reports/release-review-001.funasr.live-pipeline-replay-20260710.json \
  --provider sherpa_onnx_streaming \
  --transcript-report artifacts/tmp/asr_reports/release-review-001.sherpa.transcript-report.json \
  --pipeline-replay-report artifacts/tmp/asr_reports/release-review-001.sherpa.live-pipeline-replay-20260710.json \
  --output artifacts/tmp/asr_reports/release-review-001.asr-mainline-quality-20260710.json

PYTHONPATH=code/web_mvp/backend:code/core pytest -q tests/test_asr_mainline_quality_report.py
```

当前结论：

- [x] 产品主链路可从真实 ASR event 产物进入 no-LLM candidate timeline。
- [x] 但当前本地 ASR 对中文技术会议的准确性仍不足，尤其是英文服务名、数值阈值、Grafana/owner 等关键复盘信息。
- [x] 不能因为 `pipeline_closed_count=2` 就宣称 ASR 可生产；当前质量决策是 `no_go_quality_not_production`。
- [ ] 下一步应优先做 provider/热词/分段策略优化，或扩大到更多公开/合成音频样本；真实麦克风 shadow test 应继续保留人工复核边界。

## 10.22 2026-07-10 ASR 批量主链路质量门禁：显式 matrix、8/8 pipeline 闭合，但质量仍 No-Go

10.21 仍是单样本报告，并且复核发现 `S01/S02/S03` reference 与 `api/release/incident` synthetic ASR 产物不能按名字硬配。为避免评测污染，本轮新增显式 matrix 批量门禁：只评估 reference、annotation、events、transcript-report 全部显式声明且互相对齐的合成技术会议样本。

新增/更新：

- [x] 新增 `tools/asr_mainline_quality_batch_report.py`。
- [x] 新增 `tests/test_asr_mainline_quality_batch_report.py`。
- [x] 新增 `data/asr_eval/manifests/asr-mainline-quality-synthetic.json`。
- [x] 新增 `data/asr_eval/references/generated/*.txt` 和 `data/asr_eval/annotations/generated/*.annotation.json`，来源为 `data/asr_eval/synthetic_meetings/scripts/*.json`。
- [x] 修复 `incident-review-001` live pipeline 对运行时事故信号的识别：
  - `autoker/auder + 消费堆积/lag/告警` 保守归一为 `order-worker`。
  - `消费堆积/告警延迟/临时扩容/根因` 且具备工程上下文时进入 Risk state。
  - raw ASR evidence 不被覆盖，修正只进入 `normalized_text`/state description。

最新证据：

```text
report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-batch-20260710-after-runtime-risk.json
decision_status=no_go_quality_not_production
blockers=["some_samples_have_no_quality_passing_provider"]
recommended_next_gate=improve_asr_terms_segmentation_or_provider_before_real_gate

sample_count=4
evaluated_sample_count=4
missing_input_sample_count=0
sample_provider_count=8
pipeline_closed_sample_provider_count=8
quality_pass_sample_provider_count=3
samples_with_quality_pass_count=3
best_provider_by_average_term_recall=funasr_streaming

remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
```

Provider 结论：

- [x] FunASR：`api-review-001`、`release-review-001`、`architecture-review-001` 达到当前最低质量门槛；`incident-review-001` 未达标，缺失 `timeout` 和 `监控阈值`。
- [x] Sherpa：低延迟，但 4 个技术样本均未达标，主要问题是 `<unk>` 多、英文服务名/中间件/字段名召回差。
- [x] `incident-review-001` 修复后 pipeline 从不闭合变为闭合，但质量仍不过线；这说明建议链路问题已修复，ASR 字面质量仍是 blocker。
- [ ] 仍不能宣称生产级中文会议识别可发布；下一步必须提升本地 ASR 术语召回和分段策略。

验证命令：

```text
PYTHONPATH=code/web_mvp/backend:code/core pytest -q \
  tests/test_asr_mainline_quality_batch_report.py \
  code/web_mvp/backend/tests/test_transcript_normalizer.py::test_normalize_recovers_order_worker_only_in_backlog_context \
  code/web_mvp/backend/tests/test_live_events.py::test_build_asr_live_events_extracts_runtime_incident_risk_from_backlog_final

python3 tools/asr_mainline_quality_batch_report.py \
  --matrix data/asr_eval/manifests/asr-mainline-quality-synthetic.json \
  --output artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-batch-20260710-after-runtime-risk.json \
  --replay-run-id 20260710-runtime-risk
```

详细报告：

```text
docs/asr-mainline-quality-batch-report-2026-07-10.md
```

## 10.23 2026-07-10 ASR repaired local synthetic gate：4/4 质量候选通过，下一步转真实/公开音频 soak

10.22 的 No-Go 继续复核后确认不是单纯 provider 质量失败：`release-review-001` 和 `incident-review-001` 的旧合成音频/ASR artifact 只覆盖 reference 前半段，导致尾部 `staging`、`timeout`、`监控阈值` 被错误计入 provider 漏识别。后续处理不裁剪 reference，而是重生成本地合成音频并重跑同一主链路。

新增/更新：

- [x] `tools/asr_mainline_quality_batch_report.py` 新增 `reference_artifact_coverage`，自动标记 reference/artifact 覆盖不一致。
- [x] `tools/synthetic_audio_local_tts_smoke.py` 支持显式 `voice` 和 `rate_wpm`，本轮使用 `Tingting / 130 WPM`。
- [x] 重新生成 `release-review-001` 与 `incident-review-001` 本地合成音频，放在 `artifacts/tmp/synthetic_audio/tingting_r130/`。
- [x] 重跑本地 FunASR chunk20 hotword events/provider/transcript report。
- [x] 增加 bounded normalizer/quality alias：
  - `check outservice -> checkout-service`，仅发布/灰度/指标上下文。
  - `error r ate -> error_rate`，仅指标/P99/灰度上下文。
  - 不把 `ing` 硬补为 `staging`。
  - 不把 `timeet` 硬补为 `timeout`。
- [x] 新增 `data/asr_eval/manifests/asr-mainline-quality-tingting-r130.json`。
- [x] 新增 `data/asr_eval/manifests/asr-mainline-quality-synthetic-repaired-local.json`。

关键证据：

```text
old release duration=11.297438s
new release duration=21.792313s

old incident duration=11.362375s
new incident duration=21.060875s

coverage-audit-old:
  report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-variants-20260710-coverage-audit-v2.json
  samples_with_suspected_reference_artifact_mismatch=[release-review-001, incident-review-001]

repaired-targeted:
  report=artifacts/tmp/asr_reports/asr-mainline-quality-tingting-r130-20260710-after-normalizer.json
  quality_pass_sample_provider_count=2/2
  suspected_reference_artifact_mismatch_sample_count=0
```

Repaired 4 场景批量门禁：

```text
matrix=data/asr_eval/manifests/asr-mainline-quality-synthetic-repaired-local.json
report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-repaired-local-20260710-final-verify.json

decision_status=candidate_for_next_real_audio_gate
blockers=[]
recommended_next_gate=real_or_public_audio_wall_clock_soak

sample_count=4
sample_provider_count=4
pipeline_closed_sample_provider_count=4
quality_pass_sample_provider_count=4
samples_with_quality_pass_count=4
samples_without_quality_pass=[]
suspected_reference_artifact_mismatch_sample_count=0

remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
```

当前结论：

- [x] Repaired local synthetic ASR 主链路候选通过：4/4 场景都能从本地 ASR event 进入实时建议候选，且达到当前最低质量门。
- [x] 仍不代表真实多人会议生产可用，因为本轮音频是本地 TTS 合成音频。
- [x] 远程 ASR 默认仍关闭；本轮没有调用远程 ASR/LLM，也没有上传原始音频。
- [x] FunASR 是当前质量优先本地候选；Sherpa 仍只作为低延迟预览/降级候选。
- [ ] 下一步固定为真实/公开音频 wall-clock soak 与真实麦克风人工验收，不再继续扩开放式 provider 横评。

注意：

- 一次本地 batch probe 曾触发 FunASR VAD 模型缓存检查/下载提示；这不是远程 ASR，也不是付费调用，但生产/离线包必须把 ASR/VAD/Punc 模型依赖纳入可审计缓存或安装包管理，禁止运行时静默联网。

## 10.24 2026-07-10 真实/公开音频 wall-clock gate：当前 blocked，缺真实长时证据

新增/更新：

- [x] 新增 `tools/real_public_audio_wall_clock_gate.py`。
- [x] 新增 `tests/test_real_public_audio_wall_clock_gate.py`。
- [x] 生成当前 gate evidence：`artifacts/tmp/asr_reports/real-public-audio-wall-clock-gate-20260710-final-verify.json`。
- [x] 生成公开音频白名单报告：`artifacts/tmp/asr_reports/public-audio-source-whitelist-20260710-final-verify.json`。

当前 gate 输入：

```text
quality_report=artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-repaired-local-20260710-final-verify.json
synthetic_soak_report=artifacts/tmp/soak/p1-4-long-meeting-soak-20260708/soak_report.json
real_mic_manifest=artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh2/manifest.json
wall_clock_soak_report=null
```

当前 gate 结论：

```text
gate_status=blocked_real_or_public_wall_clock_soak_missing
blockers=[real_or_public_wall_clock_soak_missing]
recommended_next_action=run_real_microphone_or_public_audio_wall_clock_soak

repaired_synthetic_quality.ready=true
synthetic_soak.ready=true
synthetic_soak.counts_as_real_or_public_wall_clock_soak=false
real_mic_short.ready=true
real_mic_short.counts_as_wall_clock_soak=false
wall_clock_soak.ready=false
```

判断：

- [x] 不再把 synthetic 20 分钟 soak 伪装成真实/公开音频 soak。
- [x] 不再把短真实 browser mic evidence 伪装成 20 分钟级真实会议 soak。
- [x] 远程 ASR 仍为默认关闭。
- [x] 公开音频当前只完成 source whitelist，不授权自动下载。
- [x] 公开音频 bounded sample extraction approval 已恢复为 live 工具：`tools/public_audio_sample_extraction_plan.py`。
- [x] 当前 no-download approval evidence 已生成：`artifacts/tmp/asr_reports/public-audio-sample-extraction-approval-20260710-final-verify.json`。
- [x] 当前公开样本方向固定为中文/普通话：AliMeeting SLR119 第一，AISHELL-4 SLR111 第二，AISHELL-1 SLR33 只做 baseline。
- [x] planned sample 不允许 placeholder archive member path；即使 schema 通过也不会生成下载、抽取或转码命令。
- [ ] 仍缺具体 planned sample manifest：archive member path、clip start/end、expected duration、license citation、cleanup_required、expected sha256。
- [ ] 下一步仍需要真实/公开音频 wall-clock evidence，至少 20 分钟级，且要证明 real ASR、pipeline closed、quality gate passed、raw audio not uploaded。

公开音频官方复核摘要：

- [x] AliMeeting / OpenSLR SLR119：CC BY-SA 4.0，真实多方会议；Eval 包约 3.42G，不默认下载。
- [x] AISHELL-4 / OpenSLR SLR111：CC BY-SA 4.0，会议场景；test 包约 5.2G，不默认下载。
- [x] AISHELL-1 / OpenSLR SLR33：Apache 2.0，普通话朗读；data 包约 15G，只能作普通话 smoke，不证明会议声学。

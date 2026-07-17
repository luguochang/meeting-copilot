# P0-P2 完成报告

日期：2026-07-17

代码候选：`3bcc852` (`codex/phase0-clean-baseline`)，包含 V2 导入录音闭环和启动失败回滚，工作树 clean。

## 总结结论

当前结果不是“全部完成”，也不是“项目没有主线”。准确结论是：

```text
P0 Web 产品主链：Go
P1 本地录音/Provider/删除/复盘能力：实现 Go，部分当前候选证据沿用既有纵向证据
P2 Mac packaged runtime：实现 Go
P2 packaged WebView 安全 IPC：Go
P2 用户点击后的 native mic + UI 完整同场会议：No-Go，等待解锁 Mac
公开发布：No-Go
```

本轮已经解决此前最关键的工程断点：Rust backend/native 能力虽然存在，但 packaged 页面来自随机 localhost remote origin，Tauri ACL 拒绝 React 自定义 command。现在 exact-port runtime capability 和 application permissions 已实现，并由真实打包 React WebView 调用验证。

## P0 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P0-1 公开或生成音频模拟实时会议 | Go | packaged local AI mainline 有 FunASR final、流式建议、修正、录音、minutes/approach/index；证据 `artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json` |
| P0-2 真实麦克风 | Partial / No-Go for exact packaged UI | browser getUserMedia 和 packaged native helper 分别已有 Go；同一新 packaged React 页面点击开始到结束复盘尚未一次性验证 |
| P0-3 V2 产品化 UI | Go for implementation, UI E2E pending | React V2、完整文字、建议、复盘、录音 tab、历史返回和加载态已实现；前端 `49 passed`，但 macOS 锁屏阻止了本轮真实打包窗口点击 |
| P0-4 自动建议 | Go for local mainline | `draft.started -> delta -> committed` 已真实落盘；本轮 provider 为本地 fake OpenAI-compatible，不计远端中转站证据 |

## P1 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P1-1 导入/录音/会后转写 | Go | 录音 chunk journal、WAV export、完整文字、minutes/approach/index、历史复盘链路已有测试和 packaged local smoke |
| P1-2 Provider 与成本策略 | Go for implementation | 本地 ASR 默认路径、远程 ASR 非默认、OpenAI-compatible LLM、Keychain/Credential Manager、HTTPS-only remote URL 已实现；真实用户中转站本轮没有调用，避免费用 |
| P1-3 删除与保留策略 | Go | 删除边界、tombstone、录音清理和敏感证据脱敏已有测试；当前不读取 `configs/local` 或用户私有录音 |
| P1-4 长会议 | Implemented with prior Go evidence, current-candidate rerun not required for this code slice | 既有一小时 browser vertical 证据通过物理和语义门禁；仍不能把它升级为公开发布或自然多人会议质量证据 |

## P2 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P2-1 Mac Tauri fresh run | Partial / No-Go | 新 `.app` 能启动 bundled backend、FunASR 和 native helper；真实 WebView IPC 通过；`cargo tauri dev` 仍因工具链没有 cargo-tauri 不可执行；真实用户点击后的完整 UI 主链等待解锁 |
| P2-2 Mac packaged app | Partial / No-Go for release | `.app` 可复现构建，runtime resource 完整，binary SHA 可追溯；未完成 Developer ID、notary、staple、Gatekeeper 和 clean Mac 安装验收 |
| P2-3 Windows | No-Go / plan-only | 共享业务代码和平台 adapter 边界已设计，未在 Windows 真机执行 Tauri、WASAPI、安装和删除 smoke |
| P2-4 桌面安全配置 | Go for local implementation | 随机 loopback token、bootstrap cookie、exact-port ACL、Keychain/Credential Manager、backend 环境清理已实现；packaged CSP 仍待收口 |

## 本轮真实证据

- [packaged IPC](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/packaged_tauri_ipc_smoke/phase3-packaged-tauri-ipc-20260717-r1/evidence.json)
- [packaged local AI mainline](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json)
- [packaged app package](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/tauri_runtime_package/phase3-ipc-tauri-20260717-r1/evidence.json)
- [provider/IPC/AI 设计状态](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/docs/phase3-provider-tauri-ipc-and-packaged-ai-status-2026-07-17.md)

验证汇总：frontend `55 passed`；provider/LLM/correction/streaming backend `81 passed`；Rust `28 passed`，Mac Keychain integration `1 passed`；Tauri/smoke `23 passed`；typecheck/lint/build/Ruff/Python compile/diff check 全部通过。

## 剩余主线

下一步只有一个优先主流程：

```text
解锁 Mac
-> 启动新 packaged app
-> UI 保存本地 fake provider 或用户自行配置 relay
-> 点击开始会议并确认麦克风权限
-> native mic PCM
-> 页面实时文字
-> 页面实时建议和可见修正
-> 结束并整理
-> 录音/文字/minutes/approach/history
-> 返回会议列表
```

这一步完成后，才能把 P2-1 的 UI 主链标为 Go。随后才处理 system audio、自然多人中文质量、CSP、签名公证、clean Mac 和供应链，不再回到泛化 ASR bake-off。

2026-07-17 的自动化尝试因 macOS locked 被系统拒绝，测试进程已清理；没有绕过锁屏，也没有把该次尝试记录为成功。

## 2026-07-17 后续主线修复：V2 导入录音

此前文档把“录音导入完成”写得过于宽泛：旧 `/live/asr/transcribe-file/sessions` 虽然能做 batch 转写，却只写 legacy session，V2 React 首页没有导入口，V2 历史/复盘也无法读取该结果。这是一个实际功能缺口，而不是测评边界。

本轮已补齐：

- 新增 `POST /v2/meetings/import-audio`，使用本地 FunASR batch，不新增 ASR 费用。
- 原始上传文件和标准化 16 kHz mono WAV 均保存在 `audio_assets/<meeting_id>/`，并由删除栅栏覆盖。
- V2 canonical transcript、录音 `audio_chunks/recording_sessions`、legacy ASR projection、结束会议和 durable correction/suggestion/minutes/approach/index jobs 同一条业务链路关联。
- V2 首页新增“导入录音”，导入成功后自动打开该会议复盘；API 使用 multipart，不手动覆盖浏览器 boundary。
- 阻塞性的 FunASR 文件转写放入 `asyncio.to_thread`，不占用 FastAPI event loop。

详细契约：[V2 录音导入闭环](v2-import-audio-contract-2026-07-17.md)。

本轮测试结果：后端 V2 app/persistence/recording `56 passed`（含新增导入和 durable post-job 集成）；前端完整 `54 passed`，API/workbench focused `23 passed`；typecheck/build/Ruff 通过。

本轮仍不改变总体发布结论：导入闭环已实现，但真实 packaged UI 麦克风点击链路仍待 Mac 解锁，Windows、签名、公证和中文自然多人质量仍不是本轮假装完成的内容。

## 2026-07-17 后续主线修复：麦克风启动失败回滚

当前 clean worktree 在独立端口 `8770` 的页面自测发现：V2 首页点击开始会议后，如果麦克风权限请求超时，后端已经创建的 meeting 会残留为“进行中 / 0 段文字”。这不是边界测试，而是开始会议主流程的数据一致性缺陷。

已修复为：

- 新会议只有在 meeting 创建成功后才标记为可回滚。
- 采集启动失败时调用 V2 delete 完成 tombstone/资产清理，再返回会议列表。
- meeting 创建本身失败时不调用无意义的 delete。
- 回滚失败时同时展示原始采集错误和清理错误。
- 已存在会议重新开始录音失败时不自动删除既有会议资产。

验证结果：frontend focused `18 passed`、frontend 全量 `55 passed`、typecheck、ESLint、production build、`git diff --check` 通过。浏览器真实失败路径复测中，权限超时后 URL 返回 `/workbench`、历史行数为 0、`GET /v2/meetings` 为空、console warn/error 为 0。

该修复关闭“启动失败留下幽灵会议”的缺陷；它不代表真实麦克风成功，也不改变 packaged UI 仍等待 Mac 解锁的 No-Go 结论。

## 2026-07-17 当前提交 packaged 运行时收口

为避免继续依赖旧 `.app` 证据，本轮在隔离工具链中完成了当前提交的重新构建：

- Rust `1.97.1`、`cargo-tauri 2.11.4` 安装在 `artifacts/tmp/controlled_rust_toolchain`。
- 当前 runtime bundle：`artifacts/tmp/macos_bundled_runtime/phase0-2-current-runtime-bundle-20260717-r2/evidence.json`。
- 当前 packaged app：`artifacts/tmp/tauri_runtime_package/phase0-2-current-tauri-20260717-r1/evidence.json`。
- 当前 packaged IPC：`artifacts/tmp/packaged_tauri_ipc_smoke/phase0-2-current-packaged-ipc-20260717-r1/evidence.json`。
- 当前 packaged supervisor：`artifacts/tmp/packaged_runtime_supervisor_smoke/phase0-2-current-packaged-runtime-20260717-r1/evidence.json`。
- 当前 packaged local AI：`artifacts/tmp/packaged_ai_mainline_smoke/phase0-2-current-packaged-ai-20260717-r1/evidence.json`。

当前隔离 Rust/Tauri 工具链下 Rust 测试为 `28 passed, 1 ignored`，说明当前提交的 Tauri native/runtime 代码在新建工具链中仍可编译和通过行为测试。

当前 packaged local AI 主链实际通过：

```text
本地中文 TTS WAV
-> bundled FunASR final
-> V2 transcript projection
-> suggestion draft/delta/commit
-> correction request
-> recording chunks + WAV export
-> end meeting
-> minutes/approach/index
-> app/backend/port cleanup
```

该证据将 P2 的“当前提交可打包、运行时可启动、后端 AI 主链可闭合”从旧制品重新绑定到 `3bcc852`；它仍不能替代真实 packaged UI 麦克风同场证据。

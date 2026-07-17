# Phase 3 Provider、Tauri IPC 与 Packaged AI 状态报告

日期：2026-07-17

结论：桌面工程主链从“Rust handler 已实现但 packaged 页面不可调用”推进到“真实 packaged React WebView IPC 已通过”；同一新包的本地 ASR、流式建议、修正、录音和会后任务也已通过。当前可以作为 Mac Internal Alpha 候选继续收口，但不是生产发布包。

## 已实现

| 模块 | 当前实现 | 验证状态 |
|---|---|---|
| 中文实时 ASR | bundled local FunASR online Paraformer，进程级常驻 worker | packaged final 已通过；准确率仍需自然多人会议验收 |
| 实时文字 | final 持久化后通过 SSE/snapshot 投影，完整 transcript 保留 | browser 长会和 packaged synthetic 均已通过 |
| 实时建议 | durable suggestion lane；流式 draft/delta；commit barrier 后正式卡 | packaged local OpenAI-compatible smoke 已通过 |
| AI 修正 | final 同事务入队，独立 correction lane，不阻塞录音/文字 | packaged correction 请求与 job 完成已通过 |
| 录音 | 5 秒 chunk journal、seal、后台 WAV export、播放 URL | 新包 3 chunks/14.842 秒 WAV 已通过；真实 native helper 30 秒证据已存在 |
| 会后复盘 | end 后自动 index/minutes/approach，历史可重开 | 三类 job 全部 succeeded |
| Provider 配置 | Keychain/Credential Manager + runtime-only backend sync | 单元/集成与 packaged command status 通过；不回显 secret |
| Tauri IPC | command manifest + 精确随机 loopback port runtime capability | actual packaged React WebView 8 项检查全通过 |
| UI 状态 | 配置/连接区分、首次加载保护、返回历史/popstate | frontend 49 tests + typecheck/lint/build 通过 |

补充验证：Rust normal suite `28 passed, 1 ignored`，显式 Mac Keychain round-trip integration `1 passed`；provider/LLM/correction/streaming backend focused `81 passed`；Tauri/smoke 合同 `23 passed`；Ruff、Python compile 与 `git diff --check` 通过。

## 新候选制品

- runtime：`artifacts/tmp/macos_bundled_runtime/phase3-ipc-runtime-20260717-r1/MeetingCopilotRuntime.bundle`
- app：`artifacts/tmp/tauri_runtime_package/phase3-ipc-tauri-20260717-r1/Meeting Copilot.app`
- app binary SHA-256：`18368babca86b6656ab56e9089fcb5ca933377a45415bade22bbeaf634af1d3d`
- packaged IPC evidence：`artifacts/tmp/packaged_tauri_ipc_smoke/phase3-packaged-tauri-ipc-20260717-r1/evidence.json`
- packaged AI mainline evidence：`artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json`

## 当前 No-Go

1. 还没有同一次证据覆盖：用户在真实打包页面点击开始录音、系统授权、native mic、实时文字、真实中转站流式建议/修正、结束会议、复盘和返回历史。
2. 当前公开中文技术样本 final 有明显错字和中英混杂；不能把“非空 final”写成“生产准确率达标”。下一质量工作只允许热词/ITN/标点和 system-audio 输入改善，不再无限扩充评测集。
3. system audio 尚未接正式主链。远端会议仅靠扬声器回采会损失准确率。
4. packaged CSP、Developer ID、hardened runtime、notary、staple、Gatekeeper、separate clean Mac 尚未闭环。
5. FunASR 模型和 bundled binary 的不可变 revision、许可证和再分发授权未闭环。

2026-07-17 已实际启动新 `.app` 和本地 fake provider 准备执行 UI 全链路，但自动化接口返回 Mac locked。测试 app、backend 和 provider 随即全部终止且无残留；没有尝试绕过锁屏，也没有把该次尝试记录为 UI 通过。

## 下一执行顺序

1. 用真实 packaged 页面完成一次用户授权的 native mic UI 全链路；需要用户在系统权限弹窗出现时确认。
2. 在同一链路中由桌面设置写入用户中转站并执行 probe，再验证真实流式建议、可见修正与 usage；不得从文件或环境读取历史密钥。
3. 接入 Mac system audio 独立 track，保留 mic/system 两轨和明确来源；不先扩 Windows/移动端。
4. 对自然中文技术会议做固定小样本热词/ITN/标点优化，并报告原始文字、修正文字和延迟，不用 LLM 掩盖 ASR 原始质量。
5. 完成 CSP、签名公证、clean Mac 和供应链后，才进入受控 Pilot。

# P0-P2 完成报告

日期：2026-07-17

源码基线：`8eafc92383f8` (`codex/phase0-clean-baseline`)；其上的 Phase 0-2 收口改动尚未提交，当前工作树不是 release provenance。

## 总结结论

当前结果不是“全部完成”，也不是“项目没有主线”。准确结论是：

```text
P0 Web 产品主链：Go
P1 本地录音/Provider/删除/复盘能力：实现 Go，部分当前候选证据沿用既有纵向证据
P2 Mac packaged runtime：实现 Go
P2 packaged WebView 安全 IPC：Go
P2 用户点击后的 native mic + UI：真实麦克风/文字/录音/回放已验证；同场真实 relay 仍 No-Go
公开发布：No-Go
```

本轮已经解决此前最关键的工程断点：Rust backend/native 能力虽然存在，但 packaged 页面来自随机 localhost remote origin，Tauri ACL 拒绝 React 自定义 command。现在 exact-port runtime capability 和 application permissions 已实现，并由真实打包 React WebView 调用验证。

## P0 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P0-1 公开或生成音频模拟实时会议 | Go | packaged local AI mainline 有 FunASR final、流式建议、修正、录音、minutes/approach/index；证据 `artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json` |
| P0-2 真实麦克风 | Partial / packaged execution Go | r11 真实 packaged React + native microphone 连续 270.6 秒、16 段文字和 55 个录音 chunk；r12 已验证结束、完整文字和 271 秒录音回放；最新源代码与真实 relay 尚未同场 |
| P0-3 V2 产品化 UI | Go for implementation | React V2、完整文字、建议、复盘、录音、历史、导出和失败态已实现；当前前端全量 `80 passed` |
| P0-4 自动建议 | Go for local mainline / remote same-session pending | `draft.started -> delta -> committed` 已真实落盘；Provider 未连接时 job 现在等待且不消耗重试，终态失败可见；真实用户 relay 尚未与最新 native mic 同场验证 |

## P1 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P1-1 导入/录音/会后转写 | Go | 录音 chunk journal、WAV export、完整文字、minutes/approach/index、历史复盘链路已有测试和 packaged local smoke |
| P1-2 Provider 与成本策略 | Go for implementation | 本地 ASR 默认路径、远程 ASR 非默认、OpenAI-compatible LLM、Keychain/Credential Manager、HTTPS-only remote URL 已实现；会前页可显式同步 AI；普通 metadata 保存不再读取既有 Keychain secret 或连接 backend |
| P1-3 删除与保留策略 | Go | 删除边界、tombstone、录音清理和敏感证据脱敏已有测试；当前不读取 `configs/local` 或用户私有录音 |
| P1-4 长会议 | Implemented with prior Go evidence, current-candidate rerun not required for this code slice | 既有一小时 browser vertical 证据通过物理和语义门禁；仍不能把它升级为公开发布或自然多人会议质量证据 |

## P2 逐项审计

| 项目 | 结论 | 当前证据/边界 |
|---|---|---|
| P2-1 Mac Tauri fresh run | Partial / internal mainline | `.app` 能启动 bundled backend、FunASR 和 native helper，真实 WebView IPC 与麦克风执行已通过；最新工作树仍需固定身份包的一次真实 relay 同场验收 |
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
固定并启动同一 packaged app identity
-> 用户显式连接已保存 relay（只读取一次系统凭据）
-> 获得一次真实付费调用确认
-> 点击开始会议并确认麦克风权限
-> native mic PCM
-> 页面实时文字
-> 页面实时建议和可见修正
-> 结束并整理
-> 录音/文字/minutes/approach/history
-> 返回会议列表
```

这一步完成后，才能把 P2-1 的同场真实 UI 主链标为 Go。随后才处理 system audio、自然多人中文质量、CSP、签名公证、clean Mac 和供应链，不再回到泛化 ASR bake-off。

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

## 2026-07-17 主线修复：native 实时事件回流与结束态一致性

本轮不再扩展 provider bake-off，而是针对真实 packaged UI 主链暴露的三个阻断直接修复：

- native helper 权限请求从同步 semaphore 改为异步 continuation，避免权限回调阻塞导致的假性 readiness timeout。
- Tauri supervisor 回传 helper stderr 的具体错误；helper stdout 仅经过白名单解析后进入有界 `mic_adapter_collect_events` 队列，React 每 300ms 合并 partial/final 和 ASR 状态，因此桌面端可以展示“正在识别”而不是只等最终段落。
- 前端只有在服务端确认 `meeting.ended` 后才进入复盘；本地录音先结束但后端仍 `live/ending` 时不会误显示复盘页。

验证：frontend `57 passed`，Rust native/runtime `29 passed, 1 ignored`，typecheck、lint、production build、cargo fmt 通过。

当前候选制品：

- runtime：[phase0-2-mainline-runtime](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-runtime-20260717-r1/evidence.json)
- app：[phase0-2-mainline-tauri](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/tauri_runtime_package/phase0-2-mainline-tauri-20260717-r1/evidence.json)
- app binary SHA-256：`a31a20eb6451d9993d1dc5f788c3902f3f28b58c268246e5cd777bdb5ad4ee91`

诚实边界：新 packaged app 的自动 IPC probe 因 Mac 锁屏未完成，证据为 [no-go packaged IPC](/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/packaged_tauri_ipc_smoke/phase0-2-native-events-packaged-ipc-20260717-r1/evidence.json)，不是代码成功证据，也不是麦克风失败证据。解锁后只需继续一次 UI 同场主流程：页面配置本地 fake OpenAI-compatible provider，点击开始会议，允许系统麦克风，确认实时文字/建议/修正，结束并检查录音、完整文字、纪要、方案和历史。

当前阶段结论仍为：`Mac internal alpha candidate / packaged UI same-session Go pending unlock / public release No-Go`。权限稳定归属、签名、公证、clean Mac 和模型再分发授权不能因为本轮代码测试通过而提前关闭。

## 2026-07-17 主线修复：异常退出后禁止孤儿 helper 继续采音

旧候选真实麦克风测试暴露：主 app 被终止后，native helper 可能成为 `PPID=1` 的孤儿进程；backend 已退出、WebSocket 已断开，但 helper 仍未自动停止。该结果不计入最终 UI Go。

现已修复：helper 每秒监控父进程存活，父进程消失或父子关系改变时停止采音并退出；WebSocket receive failure 也会主动 stop/exit。Swift 编译通过，新的权威候选为：

- runtime：`artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-r2-runtime-20260717/evidence.json`
- app：`artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r2-tauri-20260717/evidence.json`
- native helper SHA-256：`fc70ae580c7d70e46bf7493861472110935e96816d471cd08afdebe95107e423`

所有旧 app/backend/helper/外放测试进程已清理。最终同场 UI 验证必须在解锁后从 r2 候选重新开始。

r2 候选的 packaged 绑定回归已通过：

- supervisor：`artifacts/tmp/packaged_runtime_supervisor_smoke/phase0-2-mainline-r2-supervisor-20260717/evidence.json`，bootstrap/health/providers/workbench/ASR runtime 均通过，FunASR ready，公开中文音频产生非空 partial 和会中 final，app/backend/随机端口全部回收。
- local AI mainline：`artifacts/tmp/packaged_ai_mainline_smoke/phase0-2-mainline-r2-ai-20260717/evidence.json`，V2 segments=2、committed suggestions=2、correction=1、recording chunks=6、WAV assembled、minutes/approach/index succeeded；本地 fake OpenAI-compatible provider 请求=5。
- remote ASR、remote LLM、paid service、用户私有音频读取均为 0。

以上证明 r2 packaged backend/FunASR/AI/录音/复盘未因 helper 隐私修复回归；仍不替代真实 React 页面点击后的 native mic 同场证据。

## 2026-07-17 r5：启动凭据与会后决策链闭环

r5 不再在 macOS 启动阶段自动读取 Keychain。`provider_config_status` 只读元数据，已保存但未同步的配置显示“AI 待连接”；用户主动“连接并测试”时才执行异步 `provider_config_sync`。未设置任何 auto-sync 环境变量启动 ad-hoc r5 后，15 秒内没有 `SecurityAgent`，桌面窗口和 bundled backend/FunASR 均正常。项目不会提取、读取或保存 Mac 登录密码。

会后数据链同时修复：模型返回的结构化纪要不再在 Markdown 投影后丢失，V2 持久化保留 `decisions/action_items/risks/open_questions`；“决策与待办”页展示这些正式会后结果，并为旧 Markdown-only 会议提供兼容解析。既有会议无需重跑模型即可看到决策、行动项、负责人/截止、风险与未闭环问题。

权威内部候选：

- runtime：`artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-r5-runtime-20260717/evidence.json`
- app：`artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r5-tauri-20260717/evidence.json`
- UI：`artifacts/tmp/packaged_ui_mainline/phase0-2-mainline-r5-ui-20260717/evidence.json`

验证为 frontend `59 passed`、backend minutes/V2 `35 passed`、Rust `30 passed, 1 ignored`，并通过 lint/build/fmt/diff check。录音内容端点支持 `206 Range`，探针为 PCM 16-bit mono 16 kHz WAV。

总体发布判断仍不改变：业务主链和 packaged review reopen 为内部 Go；packaged native mic 真实同场仍因稳定 Apple 签名/TCC 身份未关闭；公开发布仍为 No-Go。

## 2026-07-17 r6：主流程缺口与内部打包真实性收口

r6 不是新一轮评测，而是修复 r5 暴露的四个产品缺陷：

- 异常或空的模型 JSON 不再崩溃 durable minutes job，也不会伪装成成功空纪要。
- 结构化纪要存在时，用户明确保留的会中建议仍可见；决策页真实区分生成中、失败和语义质量暂停。
- minutes 与实时待确认问题去重；结构化空数组为权威结果；旧 Markdown 兼容 `已确认重点`，不误读 fenced code 和嵌套列表。
- Provider 启动策略统一为显式连接，旧 auto-sync 环境变量无法重新开启 Keychain 访问；同步、保存和清除串行，保存/清除不阻塞 Tauri UI。

打包器同时修复了一个此前被“能启动”掩盖的问题：`--no-sign` 的旧 `.app` 没有封装资源，`codesign --verify --deep --strict` 会失败。现在 packager 自动执行整包本地 ad-hoc 签名和 deep/strict 验证，失败则不生成 Go evidence。该签名不需要 Apple 账号或 Mac 登录密码，也不等于 Developer ID 发布签名。

权威 r6 证据：

- runtime：`artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-r6-runtime-20260717/evidence.json`
- app：`artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r6-tauri-20260717/evidence.json`
- signed packaged AI mainline：`artifacts/tmp/packaged_ai_mainline_smoke/phase0-2-mainline-r6-signed-ai-20260717/evidence.json`
- 汇总：`artifacts/tmp/packaged_ui_mainline/phase0-2-mainline-r6-mainline-20260717/evidence.json`

验证结果：frontend `65 passed`；backend minutes/G5/V2 `41 passed`；Rust `32 passed, 1 ignored`；packager `7 passed`；lint、production build、Ruff、Python compile、cargo fmt 和 diff check 通过。签名后的 app 运行 packaged local AI 主链成功，包含本地 FunASR、实时建议、修正、30 秒录音/WAV、结构化纪要、方案、索引与进程清理；远端 ASR/LLM、付费服务和用户私有音频读取均为 0。

P0/P1 业务实现保持内部 Go，P2 Mac 内部包的 runtime、资源签名和业务主链为 Go。当前 Mac 锁屏导致 r6 最新窗口截图和同场 native mic UI 没有执行，不能据此勾选 packaged microphone；Developer ID、notary/staple、Gatekeeper、separate clean Mac、Windows 真机和模型/依赖再分发授权仍是公开发布 blocker。因此本报告仍不能宣称“Phase 0-2 公开发布全部完成”。

## 2026-07-17 DEC-412/413 当前完成审计

本节基于共享工作区当前内容做增量审计。它不把其他 Agent 的未提交修改写成新的 clean commit，也不覆盖前文 r6 的候选 provenance。

| 能力 | 当前真实结论 | 证据与边界 |
|---|---|---|
| DEC-412 V2 Markdown/JSON 导出 | Implemented / focused Go | `GET /v2/meetings/{id}/export?format=markdown|json` 已实现；两种格式复用同一 allowlisted payload 和 V2 canonical transcript，focused backend `3 passed, 2 warnings` |
| DEC-412 无 secret | Implemented by allowlist, release audit unchanged | 当前 payload 只选取 portable meeting artifacts，不序列化 provider raw config、API key、Authorization、base URL、prompt、LLM request/response 或本地音频路径；现有测试覆盖 `api_key` negative assertion，但这不替代公开发布 secret/provenance 总门禁 |
| DEC-413 五类实时事实 | Storage/projection implemented / focused Go | V2 已持久化并投影 topic/decision/action/risk/open-question；snapshot、事实事件、React parser/reducer、会议事实区域和 evidence 回跳已有 focused 覆盖 |
| DEC-413 candidate/confirmed/dismissed 状态写回 | Implemented / focused cross-stack Go | 前端已改用后端 canonical `PATCH /v2/meetings/{id}/entities/{entityId}`；client、Workbench 和 backend entity API 均覆盖，旧 `/facts/{type}/{id}` 调用已移除；packaged same-session UI 仍待验 |
| DEC-414 会前预检、设备与会议级热词 | Implemented / focused Go | 存储、本地 ASR、AI 状态、录音告知、浏览器设备选择、受管本地 hotwords 和删除清理已接入；自定义热词使用独立本地 FunASR sidecar，不新增远程 ASR 费用 |
| DEC-414 Provider 成本与隐私可见性 | Implemented / focused Go | AI 设置按需展示 provider/model、本地 ASR 免费边界、远程 ASR 默认关闭、原始音频不上传及 token/费用；定价缺失不误报 0 元，连接测试明确提示可能计费 |

当前可以声明：

- V2 用户可以从复盘页选择 Markdown 或 JSON 下载；Markdown 与 JSON 不从 legacy event、partial 或 DOM 各自重建文字，完整文字来自同一 V2 canonical transcript。
- 导出不触发新的 ASR/LLM 调用，不上传会议数据，也不新增费用。
- 五类事实已进入 V2 durable snapshot 和 React 同一投影；`candidate/confirmed/dismissed` 由后端持久化并追加事实事件，前端不复制提取规则。

当前不能声明：

- 不能把 endpoint focused 通过升级为 packaged native mic 同场 UI 已验收；候选事实确认/忽略和 evidence 回跳仍需在下一包进行一次真实页面操作。
- 不能把会后结构化 minutes 的 `decisions/action_items/risks/open_questions` 写成实时事实 confirm/dismiss 闭环；两者是不同生命周期。
- 不能把 DEC-412 导出通过升级为 packaged native microphone 同场 UI Go，也不能关闭 Developer ID、notary/staple、Gatekeeper、clean Mac、Windows 真机或模型/依赖再分发授权。

因此当前总体判断应读为：P0/P1 录音、文字、建议、复盘、V2 导出、五类事实、状态写回和会前预检代码主链保持内部可用；P2 packaged native mic same-session 继续保持 No-Go。system audio/mixed 的 Phase 0 可行性 spike 已完成，正式产品接线属于 Phase 3；公开发布外部门禁没有因本次实现被标记为完成。

## 2026-07-17 r7：会议事实、会前预检与录音归属收口

r7 继续补产品主流程，不新增 provider bake-off：

- 实时状态扩展为 current topic、open question、decision、action item、risk 五类 canonical 会议事实；决策、待办和风险支持 candidate/confirmed/dismissed，逐条绑定 transcript segment、时间范围和原文 quote。
- React “会议事实”区域只消费后端 snapshot/event，支持依据回跳、确认和忽略；状态由 canonical `PATCH /v2/meetings/{meetingId}/entities/{entityId}` 持久化。
- 会前预检显示本地存储、本地中文实时 ASR、AI 配置、输入设备、会议技术词和录音告知；技术词只保存在本地并实际传给 FunASR。Provider 设置按需展示 token、估算费用和原始音频默认不上传的边界。
- 后端 V2 成为录音 journal、chunk、recording session、WAV、历史回放和删除的唯一 owner；历史 Tauri audio chunk runtime 和命令已删除，native helper 只把 PCM 送入认证后的后端链路。

本轮发现并修复了一个真实 UI 契约错误：预检页面原来读取不存在的 `available_bytes`，真实后端返回 `writable_capacity_bytes` 和 `estimated_meeting_bytes`，因此页面显示“容量未知”。现在页面直接显示“本地可写 10.0 GB · 本场预计 110 MB”，测试桩也使用真实字段。

权威 r7 内部证据：

- runtime：`artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-r7-runtime-20260717/evidence.json`
- app：`artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r7-tauri-20260717/evidence.json`
- supervisor：`artifacts/tmp/packaged_runtime_supervisor_smoke/phase0-2-mainline-r7-supervisor-20260717/evidence.json`
- packaged AI mainline：`artifacts/tmp/packaged_ai_mainline_smoke/phase0-2-mainline-r7-ai-20260717/evidence.json`
- packaged WebView IPC：`artifacts/tmp/packaged_tauri_ipc_smoke/phase0-2-mainline-r7-ipc-20260717/evidence.json`
- browser preflight/facts：`artifacts/tmp/browser_visual/phase0-2-mainline-r7-preflight-20260717/evidence.json`

结果：r7 `.app` 资源完整、整包 ad-hoc 签名和 deep/strict 验证通过，binary SHA-256=`02b90dfb8060472534ac0ca55668c3b3be96a2eec857b6b20674cfa624e530c0`；packaged local AI 主链得到 2 段 canonical transcript、2 条 committed suggestions、1 次 correction、6 个录音 chunk/30 秒 WAV，minutes/approach/index 全部成功。浏览器真实页面在桌面和 375x812 视口无横向溢出，facts evidence 回跳、decision confirm、risk dismiss 和刷新重放通过，console error/warning 为 0。以上测试调用远端 ASR/LLM、付费服务、Keychain 和 Mac 登录密码均为 0。

仍未关闭的 Phase 0-2 产品主线只有：r9 packaged React 页面中由用户显式开始的 native microphone 同场会议。当前 Mac 在 Computer Use 启动时再次进入锁屏，因此没有把独立通过的 native helper、packaged AI 和浏览器 facts 拼接成假的 UI E2E。system audio/mixed 正式接线按 DEC-416 保持在 Phase 3；Developer ID、notary/staple、Gatekeeper、clean Mac、Windows 真机和再分发授权继续属于公开发布外部门禁。

## 2026-07-17 r9：打包可复现与完整进程树回收

r8 首次重打包证明前端容量修复已经进入 bundle，但也暴露两个工程缺陷：打包脚本只接收 `cargo-tauri` 路径时仍从旧 toolchain 目录寻找 `cargo metadata`；另一次 r8 UI 启动终止后 backend leader 已退出，Rust stop 函数提前返回，包内 FunASR resident worker 成为 `PPID=1` 的孤儿进程。

r9 的修复：

- packager 从显式 `cargo-tauri` 路径推导同级受控 `cargo-home/rustup-home`，不再依赖调用者隐藏的 shell 环境；聚焦测试 `8 passed`。
- Unix backend stop 始终对完整 process group 发送 TERM，并以 process group 是否仍存在作为退出条件；leader 已退出也不会漏掉 descendant，超时后对整组 KILL。真实测试覆盖“shell leader 先退出、sleep descendant 留存”的竞态。
- packaged runtime、AI 和 DMG install smoke 都记录 FunASR PID；worker 未自然退出或只能强制清理时一律 No-Go，不再只检查 app/backend/端口。

当前权威内部候选：

- runtime：`artifacts/tmp/macos_bundled_runtime/phase0-2-mainline-r8-runtime-20260717/evidence.json`
- app：`artifacts/tmp/tauri_runtime_package/phase0-2-mainline-r9-tauri-20260717/evidence.json`
- supervisor：`artifacts/tmp/packaged_runtime_supervisor_smoke/phase0-2-mainline-r9-supervisor-worker-reap-20260717/evidence.json`
- AI/录音/复盘：`artifacts/tmp/packaged_ai_mainline_smoke/phase0-2-mainline-r9-ai-worker-reap-20260717/evidence.json`
- WebView IPC：`artifacts/tmp/packaged_tauri_ipc_smoke/phase0-2-mainline-r9-ipc-20260717/evidence.json`
- DMG/临时安装：`artifacts/tmp/macos_dmg_install_smoke/phase0-2-mainline-r9-dmg-install-worker-reap-20260717/evidence.json`

r9 app binary SHA-256=`fbe11074dd3fd24a1c4ba4ac27d9200adfc72783307f88c273f795b99a0c30f6`。supervisor 和 AI 主链均证明 FunASR worker 自然退出，`forced_cleanup=false`；AI 主链继续得到本地 FunASR final、2 段 canonical transcript、2 条 committed suggestions、1 次 correction、6 chunks/30 秒 WAV 和成功的 minutes/approach/index，远端服务与付费调用为 0。开发 DMG SHA-256=`abd55ccf4d37090738e6f8ec3bda71fe01c103a420cefefd622d632ac0ec94ec`，挂载、`Applications` 链接、临时复制、三处 deep/strict 签名校验、本地 Workbench/FunASR 启动、卸载和临时安装清理全部通过；未使用 sudo、Keychain、密码、麦克风、屏幕采集或远端服务。

packaged native microphone 同场 UI 仍未验收：Computer Use 对 r8 窗口返回锁屏/AX 不可读，测试进程已清理，没有绕过用户录音同意。该项仍是 Phase 0-2 唯一产品门禁。

## 2026-07-17 r10-r12：真实 packaged 麦克风、直接结束与权限身份结论

r9 在解锁后的真实页面启动 native microphone 时，helper 已成功获得权限、建立 WebSocket 并启动 AVAudioEngine，随后因 async `main()` 从主队列调用 `dispatchMain()` 被 libdispatch 以 `SIGTRAP` 终止。r10 将同步进程入口作为主事件循环 owner，异步 Task 只负责权限和采集；Swift 编译和生命周期回归通过。r10 真实页面随后稳定显示系统麦克风已连接、FunASR partial/final 和连续会议文字，证明原崩溃已关闭。

r11 继续增加 WebSocket ping、24 小时资源超时和停止原因日志，并修复中断后页面隐藏结束按钮的问题。真实麦克风会议跨过旧 90.6 秒故障点，连续运行到 270.6 秒；后端持久化 55 个 microphone chunks、16 段 canonical transcript，WAV 状态为 ready。连接最终仍以 `NSPOSIXErrorDomain Code=57` 中断，没有产生 crash report，因此不能把长会议连接稳定性写成完全关闭。

r12 增加同一 meeting 的有界 native 自动重连；连接稳定超过 30 秒后重置连续失败计数，单次中断最多重试三次，用户仍可随时结束。`window.confirm` 从结束主流程移除，页面始终只有一个直接可用的“结束并整理”；中断时同时提供“重新开始录音”。focused frontend 为 `26 passed`，TypeScript 和 production build 通过。

真实 r12 页面打开 r11 持久化会议后，单击“结束并整理”进入复盘；完整文字 16 段可见；录音页显示 271 秒、55 分片、microphone 音轨，点击播放后时间轴从 0 推进到 2 秒。截图和机器证据位于 `artifacts/tmp/packaged_native_mic_ui/phase0-2-mainline-r11-r12-20260717/evidence.json`。

r12 新版本路径再次开始麦克风时，helper 停在 `request_permission` 并在 30 秒后 readiness timeout；前端按 DEC-405 删除新建会议并回到列表。r11/r12 helper 的 SHA-256 和 CDHash 相同，变化的是 ad-hoc app 路径/签名身份。这证明反复授权的正确解法是固定安装路径和稳定 Developer ID/Team ID 签名，不是提取、读取、保存或自动填写 Mac 登录密码。项目继续禁止 `sudo`、密码自动化和隐蔽授权。

当前内部结论：packaged native microphone 执行、实时文字、录音持久化、直接结束、完整文字和实际回放已经得到真实证据；长连接已增加自动恢复但根因仍需后续根据安全 `error_class` 定位。真实 `codexai.club` + `gpt-5.5` 同场 correction/suggestion/minutes 仍需一次显式计费确认和稳定凭据身份，不能把 r9 本地 fake provider 与 r11/r12 麦克风证据拼成同场 Go。因此 Phase 0-2 目标保持未完成，公开发布继续 No-Go。

## 2026-07-18 Provider 401 诊断与收口

本轮确认 401 的直接原因不是网络不可达，而是“协议与认证来源不能从本机 Codex 配置推断”。本机 Codex 的非敏感配置为 `https://codexai.club`、`gpt-5.6-sol`、`wire_api=responses`；Meeting Copilot 则从独立系统凭据库读取 API Key，并按自身显式 `api_style` 组装请求。Responses 探测曾连续返回 `HTTP 401 INVALID_API_KEY`，切换并同步 `chat_completions` 后同一固定客户端的真实 probe 返回 200。

当前结论：Meeting Copilot 对 `codexai.club + gpt-5.6-sol` 固定使用 Chat Completions，不根据模型名自动猜测协议，也不在 401 后自动双投。Provider 错误文案已区分 Responses 认证路由不兼容的可能性、当前应用凭据来源和模型权限检查项。API Key 仍只存在系统凭据库，本文不保存、回显或比较密钥内容。本轮不重复发起远程调用；用户在对话中暴露过的测试 Key 应按已暴露凭据处理并轮换。

## 2026-07-17 主流程收口：Provider 等待、修正失败与钥匙串触发边界

本轮多 Agent 只读审计发现三个会直接破坏真实 AI 主流程的问题，并已经按 TDD 修复：

1. backend 重启后 Provider runtime 未同步时，早期 correction/suggestion job 原来会在约 1 秒、2 秒退避后耗尽三次机会。现在 `ProviderRuntimeNotConfiguredDeferred` 通过 durable `defer_job` 回到 `retry_wait`，撤销 claim 增加的 attempt，并在 10 秒后继续检查；Provider 显式同步后可继续原 job，不丢早期会议段落。
2. realtime correction 原来会把网络、HTTP 或解析失败吞成“原文 + degraded”，上层可能把真实 Provider 故障保存为 rejection/success。现在 realtime durable 调用启用 strict failure；第一次安全重试，第二次达到既有 failure budget 后终态失败，API 仍只返回 allowlist 502，不回显 URL、key 或底层响应。
3. correction/suggestion 终态失败时，`runtime.ai` 现在返回 `state=error` 和安全错误类别；suggestion 残留 draft 在同一事务转为 `rejected` 并追加可重放事件，不再永久显示“生成中”。

会前页面在桌面端新增显式“连接 AI”：只调用 `provider_config_sync` 并重新读取 `/providers/health`，不调用付费 probe；AI 未连接时仍可按 DEC-414 进入本地录音/转写。设置页普通“保存”在 API Key 留空且 metadata 已存在时，只原子更新 metadata，不读取既有 Keychain secret、不注入 backend；读取凭据仍只发生在明确的连接动作。

有限回归结果：

```text
frontend full: 13 files / 80 passed
backend affected mainline: 99 passed, 1 dependency deprecation warning
desktop Rust: 34 passed, 1 ignored Keychain integration smoke
frontend lint/typecheck/production build: passed
backend Ruff: passed
Rust fmt and git diff --check: passed
```

本轮没有调用远程 ASR、没有读取或输出 API Key、没有输入或保存 Mac 登录密码，也没有发起新的真实付费 relay 请求。r11 固定原始 app 路径仍是当前已授权麦克风的真实证据主体；最新未提交源码不能借用该旧制品身份冒充完成。最终仍需一个固定签名/路径候选，在同一场会议里完成 native mic、FunASR、真实 `codexai.club/gpt-5.5` 修正/建议、结束整理和回放，之后才能关闭 Phase 0-2。

最新源码现已冻结到固定内部候选 `artifacts/tmp/local-alpha/tauri_runtime_package/current/Meeting Copilot.app`，binary SHA-256=`1c39fa83a7a48d80e88d0efe827aac2327f65eff67cc7ad1a5bebc2578f8b993`。relocatable runtime 的 backend、FunASR ready、native helper probe、工作台和 Provider health 均通过；普通 metadata 保存没有启动 SecurityAgent。用户显式点击会前“连接 AI”后，系统按预期进入一次 Keychain 授权等待；自动化没有输入密码，也没有继续真实 relay。证据：`artifacts/tmp/local-alpha/fixed-client-mainline-20260717/evidence.json`。因此当前剩余动作已经收窄为：用户本人完成这一次系统授权，随后取得一次付费确认并运行同场主链。

## 2026-07-18 Phase 0-2 最终内部主链审计

此前剩余的“固定 packaged 客户端、真实麦克风、本地 FunASR、真实远端 AI、结束整理和录音回放必须同场完成”门禁已经关闭。当前权威候选为：

- `artifacts/tmp/local-alpha/tauri_runtime_package/phase0-2-current-20260718-r2/Meeting Copilot.app`
- app binary SHA-256=`f396522c6f9f0d69614d0f9816c0e9e0ec72e6db66b2a73ef13b4042cc9d670a`
- `codesign --verify --deep --strict` 通过，签名类别仍为 internal ad-hoc
- 脱敏证据：`artifacts/tmp/local-alpha/packaged_ui_mainline/phase0-2-current-20260718-r2/evidence.json`

### 需求、证据与结论

| Phase 0-2 必备能力 | 同场真实证据 | 内部结论 |
|---|---|---|
| packaged 桌面客户端与自带 runtime | Tauri app 启动 bundled backend 和 resident FunASR；固定 binary hash；deep/strict 验证通过 | Go |
| 真实麦克风采集 | native macOS helper 采集本机扬声器外放的中文 TTS；页面显示麦克风已连接 | Go |
| 免费本地中文实时 ASR | local FunASR/Paraformer 产生 3 个 finalized segment；remote ASR disabled | Go |
| 会议文字持续追加 | 3 个 final 均进入 canonical transcript；页面保留完整文字而非只显示当前句 | Go |
| 大模型文字修正 | 3 次真实 correction 调用，1 个 revision；原始音频未发给 LLM | Go，changed revision 可见性仍有约 4 秒会后延迟 |
| 实时流式 AI 建议 | 2 个 draft started、4 个 delta、2 个 committed；首条建议在首个 final 后约 19.7 秒 | Go，满足当前 10-30 秒目标窗 |
| 录音可靠持久化 | 29 个 committed chunks、WAV export ready、144.089 秒、4,610,900 bytes | Go |
| 结束会议与会后派生 | 唯一“结束并整理”成功；minutes/approach/index jobs succeeded | Go |
| 纪要、决策、待办、风险、问题 | 复盘页对应内容可见并持久化 | Go |
| 历史、完整文字和录音回放 | 重开同一会议；修复 WebKit metadata 问题后显示 02:24，播放推进并可暂停 | Go |
| 隐私与成本边界 | raw audio 不上传；remote ASR off；7 次远端 LLM、3547 tokens 有 ledger | Go |
| Provider 兼容 | `gpt-5.6-sol + chat_completions` 真实 probe 200；Responses 401 被显式隔离 | Go for current relay contract |
| 进程生命周期 | 关闭 r2 app 后约 2 秒，backend、resident FunASR 和随机端口自然退出，未强制清理 | Go |

### 401 的最终判断

历史日志在 `2026-07-18T02:09Z-02:31Z` 记录了 Responses 路径的 `HTTP 401 INVALID_API_KEY`。配置切换为 `chat_completions` 后，`02:32:28Z`、`03:56:32Z` 和 `04:17:38Z` 的真实 probe 均为 200，约 1.8 秒完成；其中首个 200 明确来自固定客户端完成 metadata 保存与 runtime sync 之后，后两次共享 packaged app 日志没有保留足以反推单一进程身份的信息。认证头在两种实现中均为 Bearer；差异是 `/v1/responses` 与 `/v1/chat/completions`。因此本轮没有证据支持“Key 已失效”或“客户端漏传 Authorization”，但也不能把现象升级为“Responses 协议本身导致 401”；准确结论是当前 relay contract 的 Chat Completions 路径已验证可用。当前 metadata 为 `gpt-5.6-sol + chat_completions`。

审计时发现两个 packaged app 和 `8767-8770` 历史开发后端仍同时存在。两个 packaged backend 在应用重启后都显示 `not_configured`，因为 DEC-419/421 的显式凭据同步策略不会在启动时自动读取 Keychain；这不是 401。多个实例也不会共享进程内 Provider runtime override。后续真实使用和验收只运行固定安装路径的一套 app，避免在错误端口判断状态。

### 真实性能与边界

从 durable event timestamp 计算：首个 final 为 34.508 秒，首条建议 draft 为 53.807 秒、commit 为 54.224 秒，录音 export ready 为 145.228 秒，会议 ended 为 146.112 秒，changed revision 为 150.176 秒，approach/minutes ready 分别为 195.563/199.517 秒。

这组数据支持“边识别边产生实时建议”，但不支持“每次文字修正都在会议结束前可见”。当前 correction request 的 `max_completion_tokens=4096` 对最多 2000 字的 bounded batch 偏大；将其改为动态小上限是后续性能优化，不是 Phase 0-2 功能阻断。本轮不再增加 Provider/ASR 横评，也不为该参数重复完整麦克风会议。

### 完成与发布边界

Phase 0-2 的**内部产品主链**现在可以标记为 Go：用户能够在 Mac packaged 客户端中开始会议、看到连续文字与流式建议、结束整理，并在会后复盘完整文字、结构化结果和本地录音。

公开发布继续 No-Go，未完成项仅保留为发布工程门禁：Developer ID Application、hardened runtime、notary/staple、Gatekeeper 和 clean Mac 安装；Windows 真实机器的采集、安装和生命周期验证；FunASR 模型及 FFmpeg/依赖的不可变清单与再分发批准。以上不得与已经完成的产品主链混为同一个“功能没跑通”。

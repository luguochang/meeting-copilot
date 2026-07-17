# Current Mainline Index

## 2026-07-17 V2 纵向主链路状态（当前唯一状态）

> 当前等级：`L0 功能原型`；功能里程碑 `M2 Recoverable Local Runtime` 已达成，桌面交付里程碑尚未达成
>
> 下一里程碑：`M3 Mac Internal Alpha`，当前不是可公开发布的安装包
>
> 当前实施基线：`docs/production-maturity-architecture-and-execution-plan-2026-07-14.md`
>
> 文档真相与历史归档索引：`docs/archive/readiness-index.md`
>
> 当前实施分支：`codex/phase0-clean-baseline`；本轮 provider/IPC/UX 改动完成验证后再生成新的 clean commit provenance
>
> 最新 clean provenance：`artifacts/tmp/release_provenance/phase0-clean-commit-20260717-r6/manifest.json`
>
> 最新 clean packaged runtime 证据：`artifacts/tmp/packaged_runtime_supervisor_smoke/phase3-native-mic-packaged-smoke-20260716-r2/evidence.json`

> 最新 relocatable runtime：`artifacts/tmp/macos_bundled_runtime/phase3-ipc-runtime-20260717-r1/evidence.json`

> 最新 Tauri resource app：`artifacts/tmp/tauri_runtime_package/phase3-ipc-tauri-20260717-r1/evidence.json`；binary SHA-256=`18368babca86b6656ab56e9089fcb5ca933377a45415bade22bbeaf634af1d3d`

> 最新 packaged WebView IPC 证据：`artifacts/tmp/packaged_tauri_ipc_smoke/phase3-packaged-tauri-ipc-20260717-r1/evidence.json`；真实 React 页面已调用 runtime/provider/mic prepare，未启动录音或绕过授权

> 最新 packaged AI 主链证据：`artifacts/tmp/packaged_ai_mainline_smoke/phase3-packaged-ai-mainline-20260717-r2/evidence.json`；本地 fake OpenAI-compatible provider，不计远端 relay 或 UI 证据

> 最新真实 native helper 证据：`artifacts/tmp/packaged_native_mic_smoke/phase3-real-native-mic-speaker-20260717-r3/evidence.json`；用户点击 UI 后 native mic + relay 同场仍待验
>
> 历史 packaged 业务证据：`artifacts/tmp/packaged_mainline/packaged-r9-final-real-mic-20260716/report.json`；它不绑定当前 clean 候选代码

当前事实：

- DEC-404 已关闭 packaged localhost WebView 的 Tauri ACL 阻塞：构建时为 19 个应用 command 生成 permission manifest，运行时只给 `main` 窗口和本次 `http://127.0.0.1:<random-port>/*` 授权，拒绝 `localhost`、隐式端口和远端 origin。新 `.app` 的真实 React WebView 已成功调用 `runtime_get_status`、`provider_config_status`、`mic_adapter_prepare` 并通过 Rust command 写出 IPC evidence；app/backend/端口全部回收。
- 桌面 AI 配置已采用 Mac Keychain / Windows Credential Manager；磁盘只保存 base URL、model 和 provider label。packaged backend 运行时配置不继承 `LLM_GATEWAY_*`，远端只允许 HTTPS，保存/清除带回滚，API Key 不回显。UI 保存后只显示“AI 已配置”，仅 probe 成功后显示“AI 已连接”。
- 同一新包的 no-cost AI 主链已产生 FunASR final、流式建议 draft/delta/commit、transcript correction、14.842 秒录音导出、会议结束以及 minutes/approach/index。受控样本原始文字仍有中英混杂和错字，因此该证据只证明工程链路，不证明中文 ASR 生产质量。
- V2 已增加首次 snapshot 中性加载态、复盘返回会议列表/popstate 同步，并修复 `_dedupe_strings` 只返回第一个原因的问题。当前尚未把“用户在真实打包页面点击开始录音 -> 真实 mic -> relay -> UI 建议/修正 -> 结束复盘”压成同一次自动化证据，不能把独立通过的 IPC prepare、native helper 和 backend AI smoke 拼接成 UI E2E。
- 新 `.app` 的 UI 点击运行已经准备并启动，但 macOS 自动化接口返回 Mac locked；测试 app/backend/local provider 已全部清理。下一次只在用户解锁后继续该单一门禁，不重复 runtime、provider 或 ASR 基础横评。
- `/workbench` 已切换为 React/TypeScript V2 权威入口；`/workbench-v2` 是别名，`/workbench-legacy` 只保留旧页面。
- 正式 correction/suggestion 由后台 durable executor 在 final 同一 SQLite 事务入队；浏览器不触发付费 AI。建议通过 SSE 流式展示草稿并使用 commit barrier，只有显式 `VITE_EVENT_TRANSPORT=poll` 才回退轮询。
- Phase 1A 远程幂等提示已接入：suggestion durable job 与 correction preview 的稳定 key 先做 SHA-256 脱敏，再作为 `Idempotency-Key` 发送；stream -> non-stream fallback、429/5xx retry 和 backend lease 恢复沿用同一值。原始 meeting/job ID 不进入 header；本地提交仍由 SQLite CAS 保证，中转站不支持该 header 时仍只能提供 at-least-once provider 执行。
- r9 packaged 候选前端已用真实 browser `getUserMedia` 麦克风和扬声器受控中文技术会议声源完成同场闭环：首个文字 `7055ms`、首个 final `14071ms`、首张实时建议 `17078ms`、首次可见 AI 校正 `30117ms`；3 段 final、2 段 AI 修正、1 张正式建议、29.761 秒录音、3 张方案卡、会议纪要和历史重开均成功。完整文字 DOM 与 canonical API 逐段一致，3/3 行均位于 `.transcript-scroll` 可视区域。
- DEC-382 fresh mainline follow-up：首次 20 秒重跑暴露 `/end` 对规范化 `evaluation_summary(end_of_stream_event_count=1)` 漏判，以及浏览器在 final 发送阶段断开会把已完成 finalize 误标为 `stream_interrupted`；两项均已修复并补 TDD。修复后 20 秒和 90 秒真实 browser microphone + 本地 FunASR + 本地 fake OpenAI-compatible gateway 通过，90 秒 artifact=`artifacts/tmp/mainline-rerun-20260716-120637/real-mic-90s/report.json`，录音 `90.488s`、会后 `1.522s`、首字 `15.005s`、首 final `20.006s`、首建议 `45.009s`、首校正 `50.008s`、9 段文字、1 条建议、1 张方案卡、纪要非空、内存增长 `7.09MB`；correction/suggestion/review jobs 全部达到严格接受状态，`strict-contract.json` 为 `go`。这是无付费协议链路证据，不替代远端 gpt-5.5 或一小时门禁。
- DEC-382 一小时 follow-up：物理稳定性门槛通过，`recording_wall_clock_seconds=3600.104`、最终音频 `3627.792s`、`post_processing_seconds=6.794`、726 chunks、256 段文字、RSS 增长 `226.44MB`、晚期 snapshot median `80.76ms`；修复后的 1440x900 尾部验证通过。完整产品门禁仍为 No-Go：重复短音频触发 `asr_semantic_quality_blocked`，formal status=`suppressed_by_asr_semantic_quality`，minutes/approach failed，故不宣称 Phase 2 完成。证据：`artifacts/tmp/mainline-rerun-20260716-120637/real-mic-one-hour/phase2-gate-assessment.json`。长会 runner 现已对一小时自动启用完整 review gate。
- DEC-383 已把质量阻断会后状态改为“识别质量不足，已暂停”，文字和录音仍可见；`real-mic-one-hour/06-quality-pause-review.png` 已验证页面不再显示普通生成错误，也不暴露内部 blocker。
- DEC-384 已为长测增加音频 hash/时长/playlist/重复率/语义资格门禁，并消除 correction job 累计输出和 source snapshot 的主要重复持久化；semantic blocked suggestion 会等待同段 correction，strict runner 接受 succeeded replacement + `evidence_superseded`。免费 46 秒完整链路为 `go`；82 秒远端运行已生成 correction、suggestion、minutes、3 张方案卡和录音，但不折算为一小时 Phase 2。
- DEC-385 已将 FunASR 从“每场会议重新启动 sidecar”改为进程级常驻 worker：JSONL 显式 session start/audio/end/abort/shutdown，单并发 fail-closed，结束会议 reset cache 而不卸载模型，应用退出回收 worker。真实 14.842 秒中文音频连续三场只启动 1 个模型进程，process cold ready `3.110s`，三场首字 `2.155/2.154/2.162s`，无串稿；该证据关闭冷启动架构缺陷，不关闭 `20 warm + 5 cold` 分布式门禁。
- DEC-386 已收口活动会议 WebSocket 的服务退出：`workbench_server` 固定 `--timeout-graceful-shutdown 8`，停止命令等待窗口为 `12s`；自包含 runtime launcher/probe 也已同步同一 Uvicorn 退出预算。真实活动 WebSocket + PCM 进程验证中，CLI `stop` 返回 `status=stopped`/exit code `0`，耗时约 `8.805s`；录音 session 保留为 `ready`、会议标记 `interrupted`，FunASR resident worker 无残留。已有 r9 安装包未因本决策重打包，clean Mac/签名发布验收仍未完成。
- DEC-389 已修复 V2 final/partial 同 segment 的短暂双显、同 revision 同 timestamp 的旧 snapshot 回退，以及 resident FunASR 活动 session 直接 shutdown 的协议冲突。V2 前端 `39 passed`，typecheck/lint/build 通过；resident focused `5 passed`。修复从下一次服务重启后的主链路生效，不倒灌当前正在运行的一小时会话。
- DEC-391 已在独立 `post-fix-data` 上重启新代码并完成 60 秒 browser V2 回归：`post-fix-short-run/report.json` 为 `go`，4 段文字、1 条正式建议、录音、minutes/approach/index、历史重开全部通过，浏览器/网络/5xx 为 0。干净样本没有产生可见 revision，因此本次不宣称新的 visible correction evidence；correction lane succeeded 与可见语义变化继续分开验收。
- DEC-393 已把生产化剩余工程问题固定为五项 backlog：实时音频热路径同步 I/O、legacy live projection 累计重建、SSE/snapshot 无界查询、streaming checkpoint retry、`to_thread` 取消后的副作用 fencing。后续按这一顺序做 TDD 和短主链路回归，不再重启泛化 provider/边界评测循环。
- DEC-394 已完成第一项 backlog 的最小实现：每个实时 meeting 使用 `max_workers=1` 的 session-scoped executor，音频写入、ASR、live projection、final commit 和录音 seal 保持串行但离开 async event loop；recording export 的 `wake()` 改为 `call_soon_threadsafe()`；legacy events 在 V2 export 成功后补齐 WAV metadata。slow ASR/slow final callback heartbeat TDD 与实时/录音/V2 focused 回归共 `57 passed`。其余四项 backlog 和 Phase 0 发布门禁仍未关闭。
- DEC-395 已用修复后代码完成 60 秒中文主链路回归：`artifacts/tmp/phase2-pure-chinese-fixture-20260716/post-hotpath-soak-60s/report.json` 为 `go`，首字 `5004ms`、首 final/首实时建议 `20005ms`、4 段文字、1 条正式建议、录音导出、minutes/approach/index、历史重开均成功，RSS 增长 `2.671875MB`，runtime/console/network/5xx 均为 0。纯净输入没有 visible correction，未将 correction job succeeded 冒充 revision evidence；本地 fake gateway 也不计生产 relay evidence。
- DEC-397 已关闭 DEC-393 的 bounded-state backlog：V2 events 默认 200/最大 1000 分页并返回 `has_more/next_after_seq`，SSE 和 polling 都按 cursor 有界 drain；legacy live projection 固定最近 512 finals、32 partials、128 revisions，完整事实由 V2 normalized tables 提供；streaming suggestion 的草稿和 commit 都受 durable job lease 原子 fencing，provider retry 从已有 `draft_seq` 继续。
- DEC-398 修复了长会 runner 未把 `network_failures` 纳入 blocker 的缺陷。首次修复后 60 秒运行的功能数据保留，但其空 URL `ERR_ABORTED` 不再算 strict clean evidence；修正后 `post-pagination-fencing-diagnostic-25s/report.json` 在真实 browser mic + 本地 FunASR + 本地 fake gateway 下为 `go`，runtime/console/network/5xx 均为 0，文字、实时建议、录音、会后产物和历史重开通过。local gateway 与无 visible correction 继续保持非生产证据边界。
- DEC-400 已把 clean 候选的 packaged runtime 启动合同收口：单一 `runtime-bundle-manifest.json` 统一 backend/FunASR Python、venv、site-packages、launcher 和 model 路径；Tauri 每次启动生成 256-bit loopback token，通过 HttpOnly/SameSite=Strict bootstrap cookie 保护 REST、SSE 和 WebSocket，并用 HMAC instance proof 防止把其他 loopback HTTP 进程误判为自己的 backend；FunASR 必须收到 worker `ready` 才允许 desktop runtime 绿色启动，子进程不继承 LLM/API 密钥或 local token。当前代码合同已通过，但 clean checkout 没有受控 FunASR Python 3.11 runtime 和 online model，所以 packaged 真实 ASR final 仍为 No-Go。
- DEC-401 已关闭上述 clean packaged runtime 执行门禁。首次真实运行发现 relocation probe 仍硬编码 `30s`，而 backend resident prewarm 和 Rust supervisor 预算为 `45/60s`；将 probe 统一为 `60s` 后，2.106 GB runtime 在仓库外启动，backend `44.731s` 就绪、FunASR `5.232s` ready，外部 symlink 为 0。当前 Tauri `.app` 已包含 backend Python 3.13、FunASR Python 3.11、依赖和 online Paraformer 模型；packaged smoke 通过 bootstrap/HMAC/resident-ready，将 14.8425 秒受控中文技术会议 WAV 送入 app backend WebSocket，得到 1 个非空 `funasr_realtime` final，并验证 app/backend/端口全部回收。该证据关闭 packaged runtime 工程门禁，不关闭 native mic、ASR 中文质量、Keychain、供应链、签名或 clean Mac。
- DEC-402 已把 native microphone 连接进 Tauri 主链：Swift AVAudioEngine helper、Rust supervisor、authenticated backend WebSocket、pause/resume/stop lifecycle 和 V2 native/browser adaptive selection 均已实现并通过 TDD；最新 relocatable runtime、Tauri resource app 和 synthetic packaged ASR smoke 均通过。真实麦克风 UI 尚未通过，因为本轮 Computer Use 遇到 macOS 锁屏，未能触发权限和真实音频；因此当前状态是 `M3 implementation ready / real mic evidence pending`，不是 Mac Internal Alpha 功能出口完成。
- DEC-403 已完成包内 native helper 的真实麦克风执行门禁。首次 30 秒运行暴露 CoreAudio 回调包被 backend 固定按 300ms 计时，产生约 3x timeline 漂移；Swift helper 改为固定 4,800-sample 帧并在 END 前排空尾帧后，受控中文音频经扬声器进入真实麦克风，得到 2 个技术内容 final、state/scheduler/suggestion candidate/LLM request draft、30.382 秒/7 chunks/972,278B WAV，app/backend/端口全部清理。该证据只关闭 real packaged helper execution，不关闭 Tauri IPC/UI、中文语义质量或 packaged relay 正式建议同场。
- 本次 ASR 为 packaged 本地 `funasr_realtime`、`provider_mode=real`、`is_mock=false`；LLM 为远程 OpenAI-compatible `gpt-5.5`、`is_mock=false`，5 次调用、3598 tokens；未启用远程 ASR。runtime exception、console error、network failure 和 HTTP 5xx 均为 0。
- Phase 1C 的 20 个中文技术会议核心触发点已使用生产同源提示词和真实 `gpt-5.5` 完成两阶段价值门禁：先生成并记录 evidence/TTFT/usage，再逐条人工语义审查。结果为 provider success `20/20`、evidence correct `20/20`、可直接追问且总调用耗时 <=20s `20/20`、重复 `0`、无 evidence `0`、unsupported claim `0`；总用量 4665 tokens。证据：`artifacts/tmp/product_value_gate/phase1c-gpt55-20260716-r1/report.json`。
- 录音采用默认 5 秒 chunk journal、独立 capture lease 和后台 WAV export job。每个 chunk 在触碰文件系统前先做 lease fence；fsync 后、SQLite commit 前崩溃产生的磁盘块会先由 journal 校验并补齐 SQLite，再恢复过期 capture；对账写入同时校验扫描时的 `capture_generation + expired lease`，续租或 resume 后的陈旧扫描不能越权补块。相同 epoch 的重复捕获使用递增 `capture_generation`，迟到 seal 只能同 journal 幂等返回，不能把 `ready/exporting/failed` 降级。WebSocket `END` 只封存 journal，不同步扫描整场录音。独立子进程真实 `SIGKILL` 证据仍为 `RPO=4000ms`、`RTO=15ms`，两个 durable AI job 同时恢复成功：`artifacts/tmp/v2_recovery_process/2026-07-15-recording-lease/report.json`。
- v1 -> v2 历史迁移固定 `enqueue_jobs=False`，不会为历史文字创建 correction/suggestion job 或产生隐形中转站费用。
- AI 修正会生成新的 evidence hash；已通过 meaning-preserved 校验的已提交建议可以重映射。semantic quality 被阻断时，草稿 suggestion 先等待同段 correction，旧 evidence 被 revision 取消后由 replacement job 基于新 evidence 生成，避免草稿闪现后消失；原始质量可用时两个 lane 继续并行。401/422、stale evidence 等不可重试错误一次进入终态。
- current topic/open question 除生命周期状态外，SQLite 还持久化 `version/first_seen_seq/last_updated_seq`；超出页面最近三项的问题仍保留事实行，稍后 reopen 不会重置版本、首次出现位置或 evidence。结构化日志与 stdlib/uvicorn 最终 formatter 都执行脱敏，只保留稳定 `meeting_id_hash`；access query value 整段丢弃，异常 traceback 不写入，真实会议 ID、transcript/prompt/secret/error detail 和本地敏感路径不会进入新日志。
- 首页已移除 mock 示例入口；`/v2/meetings`、`/live/asr/sessions` 和 `/sessions` 三类删除入口统一先持久化 tombstone，再取消并等待活跃采集任务，并在同一文件锁下清理剩余录音目录；所有迟到 create/final/chunk/seal 都被拒绝，不能在删除后重建 legacy/V2 meeting 或 WAV。会议文字时间戳可定位录音，纪要使用安全 Markdown 渲染；录音后台状态通过 SSE 从“整理中”自动刷新为可播放。前端已接收权威 `suggestion.superseded`/`suggestion.evidence.remapped`，旧 committed 卡不会遮蔽服务端终态。最新无付费回归 `artifacts/tmp/ui_screenshots/workbench-v2-recording-export-20260715/report.json` 在桌面与 375px 均为 `go`，console/runtime/HTTP 5xx 为 0。
- 当前 r9 本机未签名 Tauri 候选包内含 backend Python 3.12、FunASR Python 3.11、应用代码和 online Paraformer 模型，逻辑体积 2,292,365,451 bytes。app 可在随机 loopback 端口自启动 backend，三个 HTTP 入口为 200，SIGTERM 后 parent watchdog 能回收 app、backend 与端口。supervisor 证据：`artifacts/tmp/packaged_runtime_supervisor_smoke/packaged-runtime-supervisor-smoke-20260716-r9/evidence.json`；完整业务证据为上方 r9 report。边界：当前 packaged GO 仍使用 WebView/browser `getUserMedia`，Tauri native mic/system audio PCM 尚未接正式 WebSocket；凭据仍由进程环境注入，不是 Keychain 产品配置。
- 当前 clean 候选回归基线：backend 全量 `910 passed, 1 warning`；根级/runtime/package 合同 `67 passed`；Tauri/Rust `15 passed`；Ruff、Python compile、Rust fmt 和 `git diff --check` 通过。frontend V2 仍沿用已通过的 `40 passed` + lint/typecheck/production build 基线。
- Phase 0 发布来源门禁已重新绑定代码候选 `257c80ffad54a3dbaf6834ab8340a28c69cc183f`。`phase0-clean-commit-20260716-r4/manifest.json` 的 `dirty_tracked_count=0`、`untracked_source_count=0`、`tracked_sensitive_count=0`，artifact path/hash 一致，但历史 DMG evidence 明确不允许发布，四个模型与 FFmpeg 的 revision/hash/redistribution 仍未闭环，所以 `verdict=no_go`。该门禁未读取 `configs/local`、未读取密钥、未调用网络。
- DEC-387 已新增只读本地供应链快照 `artifacts/tmp/release_provenance/local-supply-chain-snapshot-20260716.json`，TDD 为 `4 passed, 1 warning`。四个实际 ModelScope 缓存模型均存在，并记录了完整逐文件 `path/size_bytes/sha256` 与目录 manifest：SeACo `95204e09...`, online Paraformer `c405c0a2...`, VAD `4c4ffbf9...`, punctuation `5367251e...`；四个 `.mv` 的 revision 均为 `master`，因此 `immutable_revision=null`。本机 FFmpeg 已记录二进制 SHA-256 `00d01197...`、version/buildconf/license 输出，但 policy 仍为 `redistribution_status=unresolved`。该快照没有下载模型、没有调用网络、没有读取密钥，也没有把本地缓存事实升级为发布授权；Phase 0 继续 `no_go`。
- Phase 0 的 Mac 高风险 spike 已完成本机正向验证：ScreenCaptureKit 同次采集得到约 60.7 秒麦克风和约 60.5 秒 system audio 非空 WAV；r7 可移动 runtime 在仓库外 clean env 启动三个 HTTP 入口并让 FunASR 34.752 秒 cold ready；r9 `.app` 已携带 runtime resource，并通过 supervisor smoke 和 browser-mic packaged 同场主链路。证据分别为 `code/desktop_tauri/spikes/macos_capture/.build/phase0-both-60s-20260716/evidence.json`、`artifacts/tmp/macos_bundled_runtime/phase0-local-relocatable-full-20260716-r7/evidence.json`、r9 supervisor smoke 和 r9 mainline report。separate clean Mac、native capture 到主链路连接、许可证与正式签名公证仍未完成。
- DEC-390 已完成 DEC-384 后版本的纯中文、低重复一小时 browser vertical 主链路：`artifacts/tmp/phase2-pure-chinese-fixture-20260716/one-hour-run/report.json` 为 `go`，录音 `3600.491s`、最终音频 `3628.066s`、224 段文字、726 chunks、6 条正式建议、correction/suggestion/review 全部成功、minutes/approach/index 成功、历史重开通过，runtime/console/network/HTTP 5xx 均为 0。该证据关闭 M2 Browser Vertical 的一小时功能门禁，但 `post-run-audit.json` 明确记录数据根复用、旧 input_mode 标签、FunASR 进程树未纳入 RSS SLO 和本地 gateway 成本 rates 未配置；因此仍是内部功能证据，不是公开发布证据。当前已关闭 `M1 Browser Vertical Alpha` 与 `M2 Recoverable Local Runtime` 功能出口；`20 warm + 5 cold` 分布保留为性能统计与 Pilot 证据，不重开功能里程碑。clean release/provenance、native capture/Keychain、separate clean Mac 和 Mac/Windows 分发仍未完成。

后续只按 `Phase 3 packaged runtime -> native mic -> system audio -> Keychain/签名/clean Mac -> Phase 4` 执行。Phase 1A/1B/1C 和 Phase 2 的功能出口不再重复测评；以下 2026-07-13 及更早内容全部保留为历史证据，不再作为当前计划、当前完成率或发布结论。

> 日期：2026-07-13
> 状态：历史短时 no-cost 证据已证明过一次真实麦克风录音、ASR、实时文字、确定性建议、方案、纪要和录音导出；production 文件链已证明本地离线 FunASR 后可由真实 `gpt-5.5` 生成带证据建议、方案和纪要；2026-07-13 新鲜受控多段音频已经在会议未结束时产生 5 个 final，并在本机 fake gateway lane 中通过录音期正式建议 gate，但这不计入 production LLM evidence。真实自然麦克风、真实远程 gateway、中文技术质量、20 分钟真实 gate、自然多人会议与发布验收仍为 No-Go。当前主线不把历史成功、本机 fake gateway 或会后有卡片等同于生产可用。
> 目的：给后续开发一个短入口，避免在多个计划文档之间丢失主线。
> 当前生产验收入口：`docs/pc-workbench-production-acceptance-report-2026-07-09.md`。
> 当前功能闭环入口：`docs/pc-workbench-full-chain-selftest-report-2026-07-09.md`。
> 当前 ASR 质量门禁入口：`docs/asr-mainline-quality-batch-report-2026-07-10.md`。
> 当前真实麦克风主链路入口：`docs/real-mic-workbench-mainline-report-2026-07-10.md`。
> 当前实施计划入口：`docs/full-chain-completion-implementation-plan-2026-07-09.md`。
> 当前恢复实施计划入口：`docs/superpowers/plans/2026-07-12-v2-recovery-implementation-plan.md`。
> 本轮主线交付报告：`docs/mainline-evidence-fix-report-2026-07-13.md`。
> `docs/full-chain-completion-implementation-plan-2026-07-09.md` 保留为历史 Web lane 基线，不作为当前唯一计划入口。
> 边界：本索引不授权读取真实用户录音或任意未授权 `.m4a`，不授权读取 `configs/local/` 或提交任何 secret，不新增默认付费 ASR。真实麦克风测试只允许在用户已授权的本地 Workbench/ignored artifact 路径内进行，并必须写入 Go/No-Go evidence。

2026-07-13 主线状态修复（DEC-369）：前端错误展示已收口。AI 建议、实时校正和会后整理不再把 HTTP 409/5xx、网关失败、网络失败或整理超时统一显示成“语音质量不足”；只有后端明确返回 `asr_semantic_quality_blocked` 才进入质量降级文案。会后整理失败会保留当前文字并提示刷新重试，空会话 AI 区域改为明确等待状态。TDD 新增行为测试先红后绿；全按钮 smoke 为 `go_workbench_all_buttons_smoke`，受控多段音频主链路为 `passed_production_mainline`。本机 Chrome fake-media 测试若不带 `--no-sandbox` 会得到 `RMS=0/peak=0` 并被 `blocked_audio_too_quiet` 正确拦截；带该参数后得到 5 个 final、录音期建议/修正可见、方案 3 条、纪要 695 字符、录音 SHA 匹配。该参数仅是本机验收工具条件，不是产品运行时依赖；in-app browser 当前仍需用户授权麦克风后才能进行自然麦克风验证。

2026-07-13 主线优先收口：用户反馈明确要求停止无止境 provider/边界评测，优先修复“开始会议 -> 实时文字 -> 结束会议 -> 会后处理”主流程和首屏信息架构。本轮确认原会后工具位于右侧关闭 `details`，导致结束后整理、纪要、历史和导出入口被隐藏；现已移动到 transcript 主区之后的普通 `post-meeting-workspace`，结束后直接展示，会议操作为桌面三列、生成/导出为两列、移动端两列。决策详见 `decision-log.md` 的 DEC-366。

本轮 UI 主线证据：`code/web_mvp/backend/artifacts/tmp/ui_screenshots/workbench-all-buttons-layout-final-20260713/workbench-all-buttons-after.png` 及同目录 25 张步骤截图。`go_workbench_all_buttons_smoke` 通过；覆盖导入录音、历史打开/恢复、实时文字刷新、风险筛选、AI 建议、证据回跳、方案分析、纪要、三类导出、AI 建议暂停/恢复、一键整理、删除和移动端布局；runtime、console、network loading 和 HTTP 5xx 均为 0。Workbench/backend focused `173 passed`，productized UI `11 passed`。

本轮真实音频结论保持诚实：独立 fresh 受控单段中文 WAV + 本地 FunASR + 真实远程 `gpt-5.5` 产生 partial/final、录音保存、会后建议/方案/纪要和导出，`llm_evidence` 为 remote non-mock、4 次调用、2599 tokens；但单段音频在录音期间只有 1 个 final，录音期实时建议门禁为 `failed_realtime_ai_suggestion_not_visible_during_recording`，不能把会后卡片当成实时建议通过。双段复核在当前 Chrome fake-media 权限请求 pending，未产生新主线证据；该失败记录为浏览器采集时序阻塞，不改写为 ASR 成功。真实自然麦克风仍保持 No-Go。

本轮真实麦克风可用性修复：新增设备选择、可见输入电平和静音提示；设备枚举改为首次 `getUserMedia` 授权成功后执行，避免启动阶段阻塞权限请求。该功能不增加远程费用或 ASR 调用，详见 `decision-log.md` 的 DEC-367。

本轮验收契约收口：`workbench_smoke.mjs`、`workbench_browser_live_mic_verify.mjs` 和 `workbench_ui_contract.mjs` 已改用 canonical transcript、结构化停止状态、历史项 session 属性和纪要生成状态，不再依赖旧文案、泛 `.utterance` 或隐藏 `<pre>`。`workbench_browser_live_mic_gate.test.mjs` 为 `23 passed`；这只证明验收器契约正确，不把尚未重新执行的 full live-mic runner 伪装为 Go，详见 `decision-log.md` 的 DEC-368。

2026-07-13 workspace refresh：本轮主线回归为 backend `662 passed, 2 warnings`、仓库主目录 `tests/` `353 passed, 2 warnings`、ASR runtime `89 passed, 1 warning`、core `34 passed, 1 warning`、ASR bakeoff `24 passed, 1 warning`；Workbench backend focused `161 passed, 2 warnings`；Node syntax、Python compile 和 `git diff --check` 通过；8767 `/health` 正常，SQLite runtime data dir 已按绝对路径加载。ASR bakeoff 的 `<unk>` 契约已同步为：raw/normalized 都保留未知标记，风险报告必须继续标记 `contains_unk`，不得从缺失文本推断实体。

2026-07-13 DEC-361/362/363：本轮修复了两个会把主线误报为未完成的验收与状态问题。DEC-361：`GET /live/asr/sessions/{id}/events` 现在返回 `llm_evidence`，只包含 provider/model、remote/local 分类、非 mock 标记、调用次数和 token 总量；摘要来自运行时配置与 session usage ledger，不返回网关地址或密钥。浏览器 verifier 优先读取该摘要，解决后端从项目 `.env` 加载配置而 Node 进程环境为空时的 `production_llm_evidence_missing` 假失败。DEC-362：实时校正跨批次出现“部分片段已安全接受、部分片段被安全拒绝”时，状态统一为 `partially_completed`，审计批次也使用该状态；已接受 revision 不被后续拒绝覆盖。DEC-363：Workbench 停止会议时明确显示“部分文字已校正，未通过安全校验的片段保留原始识别”，不会把部分结果伪装成全部校正成功。

本轮 TDD focused：7 个目标测试先红后绿，Node syntax 通过。新鲜主链路复核已经确认 `llm_evidence.gateway_base_url_kind=remote`、usage ledger、正式建议、方案、纪要和录音导出同时成立；中文 ASR 质量、自然多人麦克风、真实 wall-clock 长会、Mac/Windows 发布验收仍保持 No-Go。

新鲜主链路证据：`code/web_mvp/backend/artifacts/tmp/browser_live_mic/current-mainline-evidence-fix2-20260713/summary.json`。使用本地 FunASR + 受控非静音中文 WAV + 真实远程 OpenAI-compatible `gpt-5.5`：`mainline_completion_status=passed_production_mainline`、`health_status=audio_capture_health_passed`、`asr_final_count=4`、录音期间 `partial_visible_count=9`、`first_text_after_audio_active_latency_ms=5932`、`first_final_after_audio_active_latency_ms=12305`、录音期正式建议通过且首卡 `20148ms`、建议 `1`、方案 `3`、纪要 `836` 字符、录音 HTTP `200` 且 SHA 一致、Workbench 同 session、console/network error 为 `0`。`session_events.json` 的 `llm_evidence` 为 `configured=true`、`provider=openai_compatible_gateway`、`model=gpt-5.5`、`is_mock=false`、`gateway_base_url_kind=remote`、`llm_called=true`、`llm_call_count=4`、`llm_usage_total_tokens=2871`。这是受控浏览器音频的生产 LLM 主线 Go，不等于自然多人真实麦克风、中文质量或发布 Go；本次 L2 校正设置为关闭，校正验收状态为 `correction_disabled_by_setting`。

2026-07-13 DEC-364/365：发现并修复录音期自动建议与停止后正式整理建议在同一候选上出现两张近似卡的问题。前端 `mergeSuggestionCards` 现在按 `target_type + target_id + gap_rule_id` 合并同一候选，正式卡到达时替换临时自动卡；不同候选仍独立保留。TDD focused、Workbench `161 passed`、Node syntax 及一个独立 VM 行为探针通过。旧截图仍保留为修复前证据，后续 UI fresh screenshot 应重新确认同一候选只显示一张卡。

2026-07-13 DEC-353/354：本轮主线只处理实时会议的两个真实阻断。DEC-353 将服务端录音 writer 与 ASR readiness 解耦：ready 前持续接收 PCM，先写 WAV，再用上限 64 的有界队列等待 ASR；`END` 或 readiness timeout 会保存录音并标记 `asr_not_ready_at_stop` / `asr_ready_timeout`，不生成伪造 transcript final。DEC-354 修复 FunASR burst 输入在 END 时被固定 2 秒 writer drain 和 5 秒 process wait 提前杀死的问题；graceful 排空窗口按已接收音频量估算，最小 5 秒、最大 120 秒，abort 路径仍快速关闭。

TDD/真实证据：ready 前 END、readiness timeout 两个录音测试均验证 WAV 时长、文件大小、SHA、会话记录和无 final；ASR stream 回归通过。真实 `http://127.0.0.1:8767` 按 150ms 节奏发送仓库内中文受控 WAV：`asr_ready=true`、`partial_count=60`、`session_final_count=1`、`audio_duration_ms=17929`、`audio_sha_matches=true`、`degradation_reasons=[]`。瞬时 burst 发送若在模型 ready 前结束，会诚实返回 `asr_not_ready_at_stop` 并保留录音，这是预期边界，不计为 ASR Go。

这次推进了实时 ASR/录音主链，不代表生产发布已完成：自然多人麦克风、中文技术语义质量、真实远程 LLM、录音期正式 AI 延迟、真实 wall-clock 长会以及 Mac/Windows 打包验收仍保持 No-Go。

当前有效决策顺序：

- DEC-347：工作区核验已刷新；旧远程 key 不复用，真实远程 gateway 仍需新 key 和明确授权。
- DEC-346：deterministic correction fixture 的真实 backend 到 canonical UI 链路已通过，但不计生产 LLM 证据。
- DEC-345：自然 FunASR 运行时 correction 仍 fail-closed；其“没有安全 revision”结论仍有效，但“独立 deterministic fixture 尚未完成”已被 DEC-346 supersede。

2026-07-13 DEC-350：Workbench 历史弹窗和会后复盘区现在共用 `_historySessions` 缓存、统一排序和当前 operation 防陈旧覆盖；删除/打开/恢复后的历史状态不再由两套独立请求分别决定。focused Workbench/backend `173 passed, 2 warnings`、UI/productized `24 passed, 1 warning`，浏览器全按钮 smoke `go`、25 张截图、runtime/console/network/HTTP 5xx 为 0。该修复改善会话记录主流程，不改变真实远程 gateway、自然中文麦克风和发布验收结论。

2026-07-13 DEC-351：主线已修复一个会让产品失去价值的质量门禁漏洞。真实 session `rec_mrh7w0eb` 原先因技术关键词命中过多而被错误判定为 `passed`，并继续显示 9 张正式建议卡；现在 v3 策略增加中英混杂/音译碎片检测，历史 session 按 canonical transcript 重算，低质量结果通过 `asr_semantic_quality_blocked` 阻止自动建议和其他正式推断。Workbench 保留 `53` 段完整文字，但将正式建议、方案、纪要和实时提醒收口为 `0`，页面显示“识别语义质量不足，正式建议暂停”。

本轮证据：ASR quality `11 passed`；ASR stream/file 质量回归 `5 passed`；低质量实时建议集成 `1 passed` 且 LLM calls=`0`；正式推断 API 投影回归通过；WorkBench/backend focused `256 passed, 2 warnings`；8767 health=`ok`、页面 console errors=`0`。该结果修复的是“坏文字不能继续推断”的主线行为，不代表中文 ASR 准确率或生产发布门禁已通过。

2026-07-13 DEC-345 result：realtime correction verifier 已改为 fail-closed。录音期 sample 现在记录 canonical corrected target/source ID；只有 backend revised_segment_ids 与页面 data-status="corrected" 的目标 ID 相交时才报告修正可见。关闭设置优先、明确 no_revision_needed 才能免失败；completed 无 revision、UI 残留修正、partially_completed、mapping_rejected、provider failure 和 degraded 均不会被放行。回归：backend 642 passed、root 351 passed、ASR runtime 89 passed、focused verifier 5 passed、Node syntax 和 diff check 均通过。

DEC-345 受控 retry：code/web_mvp/backend/artifacts/tmp/browser_live_mic/v2-correction-verifier-retry2-20260713/summary.json 在 16 秒 Chrome --no-sandbox + 本地 fake gateway 下得到 3 个 final、8 个 partial，首字 4364ms、首 final 12304ms、录音期正式建议通过且首卡 16110ms，实时提醒漂移通过、录音 SHA 匹配、console/network error 为 0。L2 设置开启并真实尝试，但 safe correction 拒绝 1 个漂移 segment，报告为 failed_realtime_correction_not_visible / classification_reason=correction_rejected，没有伪造修正文案。8 秒版本只得到 1 个 final，正式卡在停止后出现，已保留为采样窗口不足的失败证据。两次均不是 production LLM evidence；真实远程 gateway、自然多人麦克风、中文技术质量、真实长会和发布验收仍 No-Go。

2026-07-13 DEC-346 result：新增 deterministic correction E2E，初始 session 只有 1 个 final、0 个 revision；runner 调用真实 backend realtime-corrections/run-once，经本机 fake OpenAI-compatible gateway 得到 1 个安全接受的 revision，SQLite events/status/revised_segment_ids 均闭环。Workbench 浏览器进一步通过 canonical target=det_corr_seg_1、source=det_corr_seg_1:rtc-v1、修正文案、原始 ASR disclosure、原始 evidence clickback 和 2 张截图。最终证据：code/web_mvp/backend/artifacts/tmp/ui_screenshots/workbench-deterministic-correction-20260713-retry5/deterministic_correction_report.json。该结果 counts_as_production_llm_evidence=false、remote_asr_called=false，证明的是 correction 业务链路实现，不是生产远程模型验收。中间四次 verifier/fixture 失败均保留在独立 artifact 目录并记录根因。

2026-07-13 DEC-343 result：本轮修复并验证了两个真实阻塞。第一，revision evidence clickback fixture 原本写入 SQLite 但没有出现在普通历史列表；根因是 `simulated_realtime_wav` 默认被历史接口隔离，而 E2E 没有显式 demo opt-in。脚本现在使用 `?demo=1&verify=revision-evidence-clickback` 和当前 `history-modal-item` 打开动作，默认真实历史过滤保持不变。第二，canonical transcript 页面使用“修正文案主视图 + 查看原始识别”，原有 clickback 断言仍期待旧式两条 `.utterance.transcript-revision`；前端现在同时保留 `data-segment-id`（修正目标）和 `data-source-segment-id`（原始修正来源），原始 evidence 点击会定位 canonical 行并展开原始 ASR。浏览器 evidence gate 已通过：`artifacts/tmp/ui_screenshots/workbench-revision-evidence-clickback-20260713-final/revision_evidence_clickback_report.json`，5 张截图、原始/修正 evidence 均可点击、revision 关系可见、原始 ASR 可展开，远程 ASR/LLM 均为 false。

本轮新鲜双段受控音频复测：`artifacts/tmp/browser_live_mic/v2-local-gateway-two-turn-correction-enabled-current-20260713-retry/summary.json`。Chrome `--no-sandbox` 下真实本地 FunASR 产生 `5` 个 final、`17` 个 partial 可见，首字约 `4639ms`、首 final 约 `12306ms`；录音期间正式 AI 建议通过，首卡约 `15131ms`；录音 SHA 匹配、Workbench 同 session、方案/纪要/建议均可见、console/network error 为 0、实时提醒漂移 gate 通过。该运行只使用本机 fake OpenAI-compatible gateway，`counts_as_production_llm_evidence=false`，不能替代真实远程 LLM 验收。

L2 修正结果保持诚实失败：设置已显式开启，后端确实尝试修正，但本次 FunASR 文本为 `tracout out service`、`p九b` 等漂移形式，fake fixture 未形成安全可接受 revision，`combined_rejected_segment_ids=1`，报告为 `failed_realtime_correction_not_visible`。这证明新的 verifier 不再把“修正关闭/无需修正”误报为失败，同时也没有放宽事实保护规则。另一次没有 `--no-sandbox` 的相同运行得到 `rms=0/peak=0`、`blocked_audio_too_quiet`，证据保留在 `artifacts/tmp/browser_live_mic/v2-local-gateway-two-turn-correction-enabled-current-20260713/`，不能用来判断下游业务链路。

当前结论仍为：本地受控主链路 Go for controlled evidence；真实远程 LLM、自然多人真实麦克风、生产 Chrome sandbox 输入、中文技术语义质量、真实 wall-clock 长会和 Mac/Windows 发布验收仍 No-Go。

2026-07-13 DEC-344 result：all-buttons smoke 对 canonical namespace collision 做了 fresh 回归，发现无目标 `revision-supplement:*` 段的 `data-segment-id` 被双 ID 修复误改为 namespace key；已按边界修复为“有目标修正：目标 ID + 来源 ID；无目标补充：来源事件 ID + 独立 projection key”。fresh `go_workbench_all_buttons_smoke` 重新通过，25 张截图，revision supplement、reload recovery、滚动跟随、移动端无横向溢出、所有按钮和浏览器 diagnostics 均通过。最终回归为 backend `642 passed, 2 warnings`、root `347 passed, 2 warnings`、ASR runtime `89 passed, 1 warning`。

2026-07-13 DEC-342 result：录音期正式建议的受控验证已闭环。单段连续音频只产生 1 个结束 final，因此录音期正式卡为 0；这不是调度器可用性的充分反证。新增 `artifacts/tmp/audio_fixtures/two-turn-release-incident-16k.wav`，由两段受控中文语音和静音间隔组成；在 `artifacts/tmp/browser_live_mic/v2-local-gateway-two-turn-20260713` 中，真实本地 FunASR 产生 `5` 个 final，实时文字可见，录音期正式建议 `passed_realtime_ai_suggestion_visible`，首卡延迟 `15028ms`，录音导出 SHA 匹配，浏览器 console/network error 为 0。该 lane 使用本机 fake OpenAI-compatible gateway，`counts_as_production_llm_evidence=false`、`remote_asr_called=false`，不能替代真实远程 provider 验收。详细证据：`docs/v2-ready-gate-followup-2026-07-13.md`、`docs/decision-log.md` 的 DEC-342。

本轮新鲜回归：backend `641 passed, 2 warnings`；root `342 passed, 2 warnings`；ASR runtime `89 passed, 1 warning`；all-buttons `go_workbench_all_buttons_smoke`、25 screenshots、browser runtime/console/network/HTTP 5xx 均为 0。当前服务 `http://127.0.0.1:8767` health=ok，Workbench asset=`workbench.js?v=20260713-v2-ready-gate2`。

2026-07-13 DEC-341 follow-up：FunASR/Workbench readiness gate 已完成。后端在真实 FunASR `wait_ready()` 成功前不读取音频，发送 `asr_starting -> asr_ready`；前端 `_micAsrReady=false` 时只缓存有界 PCM，ready 后按序发送，`asr_ready_timeout` 停止自动重连。8767 真实 handshake 为 `provider=funasr_realtime`、`is_mock=false`、`ready_latency_ms=5278.7`、随后收到 `partial`，不调用远程 ASR/LLM。新鲜浏览器复测分别使用 fake audio 与可见 Chrome 系统麦克风，均为 `rms=0/peak=0`、`blocked_audio_too_quiet`；录音 HTTP 200/SHA 匹配、console/network error=0，但无非空 final，因此明确保持 No-Go。详细证据：`docs/v2-ready-gate-followup-2026-07-13.md`。下一步不再重复 fake-audio-file 诊断，进入 final candidate -> 正式建议新 session 验证和完整回归；真实麦克风验收需要有效中文声源。

2026-07-13 V2 FunASR/真实麦克风 follow-up：本轮已把 FunASR streaming worker 改为显式 `ready` 协议，并固定使用本机 Paraformer online 模型目录，避免会议期间 ModelScope 下载。直接 worker 证据为 ready 延迟约 `3.55s`、`11` 条非空 partial、`1` 条 final；按真实 300ms 节奏送入 8767 WebSocket 后，provider=`funasr_realtime`、`is_mock=false`、录音导出 HTTP 200。Chrome `getUserMedia` 受控扬声器测试 `rec_mrih83en` 通过音频健康门禁，`241` 个采集块、`1` 个非空 final、音频 SHA 匹配，页面出现实时技术文字和 3 类本地提醒。另发现并修复同 segment stable partial -> final 时 `emitted_state_event_ids` 过早去重，导致正式 AI 候选一直 deferred；现在 final 生成 `_final` queued candidate，本机 fake OpenAI-compatible gateway 已验证 1 次 HTTP 调用、1 张卡、170 tokens。证据报告：`docs/v2-funasr-real-mic-evidence-2026-07-13.md`，JSON：`code/web_mvp/backend/artifacts/tmp/browser_live_mic/v2-recovery-controlled-speaker-20260713/real_mic_evidence.json`。结论仍不是发布 Go：自然多人会议、中文技术术语/断句质量、约 3.5 秒冷启动、用户真实 gateway 录音期正式建议和真实 20 分钟 soak 仍未验收。

2026-07-13 DEC-336 result: 长会议 Workbench 验证器最后一个失败已定位并修复。删除会议后页面保留“本次会议已删除”空态，该空态复用了 `.utterance` 类，旧验证器因此把空态误报为一条正文。新增回归先得到 `5 passed` 中 1 个预期失败，再将长会议 verifier 和 all-buttons verifier 统一到 `transcript-segment[data-transcript-segment-id]` 加可见 `#transcript-active-tail` 的 canonical 统计口径。新证据：`artifacts/tmp/ui_screenshots/workbench-long-meeting-evidence/long_meeting_ui_report.json` 为 `go_long_meeting_ui_evidence`，12 条 canonical 段落、1 条修正、4 张建议卡、2 张方案卡、纪要、普通/修正证据回跳、文字稿/纪要导出、删除后 `utterances=0`；`artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_all_buttons_report.json` 为 `go_workbench_all_buttons_smoke`，25 张截图，`runtime_exceptions=[]`、`error_console=[]`、`network_loading_failed=[]`、`http_5xx=[]`，删除后 canonical `utterances=0`。规范回归为根目录 `tests=341 passed, 2 warnings`、backend `633 passed, 2 warnings`。这只证明本地 fixture/UI 主链路和报告口径已闭环，不证明真实麦克风、录音期 production 正式 AI、真实 wall-clock 20 分钟 soak、录音 SHA 门禁或 Mac/Windows 发布验收；这些继续保持 No-Go。

2026-07-13 V2 follow-up: 低风险剩余证据已核对但没有冒充生产验收。`artifacts/tmp/soak/v2-recovery-synthetic-20260713/soak_report.json` 为 deterministic synthetic 20-minute `go`：`chunk_count=600`、`asr_rtf=0.1`、RSS 增长 `12 MB`、`remote_asr_called=false`、`llm_called=false`，明确 `not a wall-clock soak`。现有 `artifacts/tmp/asr_reports/public_chinese_asr_baseline_20260710.json` 已复核：`weighted_cer=0.047794`、`max_rtf=1.169649`、`release_gate_status=needs_asr_optimization_before_release`，因此不重复下载公开音频。现有真实麦克风音频导出证据 `artifacts/tmp/browser_live_mic/realtime-focus-speaker-to-real-mic-production-20260711-final/audio_export_probe.json` 经独立 rehash：HTTP `200`、`3194924` bytes、计算 SHA 与 session SHA 匹配；这证明已有录音导出完整性，不改变真实麦克风录音期正式 AI和真实 wall-clock soak 的 No-Go。

2026-07-13 DEC-338 result: 最新受控真实麦克风 no-cost 复测严格判为 No-Go，并留下完整 evidence。`artifacts/tmp/browser_live_mic/v2-recovery-real-mic-nocost-20260713`：30 秒录音 `health_status=audio_capture_health_passed`、`RMS=0.0170`、`peak=0.5828`、`active_sample_ratio=0.1939`，音频保存/导出 HTTP `200`/SHA 匹配，但 `funasr_realtime` `final_event_count=0`、页面文字为 0；`artifacts/tmp/browser_live_mic/v2-recovery-real-mic-60s-nocost-20260713`：60 秒录音 `health_status=blocked_audio_too_quiet`、`RMS=0.00364`、`active_sample_ratio=0.0549`，同样无 ASR final，但音频保存/导出 SHA 匹配。两次均 `provider_mode=real`、`is_mock=false`、`asr_fallback_used=false`、浏览器 console/network error 为 0、删除验证通过。失败根因分别落在“有效声音下本地 realtime provider 没有 final”和“有效音量门禁未通过”，不是前端重复渲染或空态统计；当前不继续盲目重复录音，下一步需要可控中文声源/ASR endpoint 或明确 provider 路线后再测。

2026-07-13 DEC-339 result: 真实麦克风 verifier 的录音期样本、失败页状态、最终 UI report 和 same-session 判断已统一使用 canonical transcript rows，不再把“未识别到有效语音”空态计为 `frontend_utterance_count=1`。新增 focused test 红绿通过，历史 DEC-338 失败报告保留不改；这只修正证据口径，不改变真实麦克风 No-Go、非空 ASR final 和录音期正式 AI 门禁。

2026-07-12 DEC-334 result: Canonical Transcript 的审查阻断项已修复。FunASR 跨已提交边界的小范围重识别在 tail 转 final 后继续以 `source_snapshot_text` 为权威，backend projector 和 frontend reducer 都会按公共前缀截断旧 committed segments 后补齐正文；`provider_error` 不再覆盖文字稿 DOM；正常结束从 `evaluation_summary.end_of_stream_event_count` 恢复；只有 active tail 的 canonical 会话也算有文字，且 `audio.saved=false` 不会再宣称录音已保存。RED focused=`6 failed`，GREEN focused=`6 passed`；核心回归 `315 passed`，扩展回归 `256 passed`，all-buttons E2E=`go`。浏览器 probe 证明重识别 final 全文精确、active tail=0，provider error 后 canonical 文本、segment 数和固定容器均保持。当前 `8767` 已加载修复后的代码和历史数据。仍未执行本轮新的真实麦克风声学录制，production 中文 ASR、20 分钟 soak、录音期正式 AI 建议继续 No-Go。

2026-07-12 DEC-333 result: Workbench 用户可见文字统一为 `committed canonical segments + optional active_tail`，原始 ASR 事件仅用于审计，不再直接追加成可见日志。revision 原位替换，`projection_key` 与 ASR `segment_id` 分离；一次渲染事务更新正文和尾句；96px 内自动跟随，向上阅读时显示 `↓ 有新内容`；启动恢复最新 `recoverable && !is_mock` 会话，且不重复触发付费建议。历史真实会话 `rec_mrh7w0eb` 当前显示 53 个已确认段落、1 个 active tail、无重复“发言”标签，并明确标记历史录音未保存。

2026-07-12 DEC-332 result: 用户真实会议截图确认 FunASR 累计 partial 被服务端多个 `vad_endpoint_*` 当作独立 final 发送，导致页面按不同 segment 重复展示“前文 + 新增内容”；最终落库又被累计替换规则收缩，造成会中与历史不一致。修复后仅 `funasr_realtime` 走累计增量切分：每个 VAD endpoint 只发送新增后缀，当前 partial 与对应 final 共用 segment ID，其他 ASR Provider 协议保持不变。同步修复 `.btn` 覆盖 `hidden` 导致两个“结束会议”的问题，录音中只显示独立 stop 控件。TDD 红灯准确复现两项缺陷，绿灯后 ASR/Workbench/UI 回归 `183 passed`。仍需在重启后的真实页面用连续两段中文语音复核，不把本次代码回归直接声明为 production real-mic Go。

2026-07-11 DEC-331 result: production 真实麦克风验收新增独立 `realtime_ai_suggestion_status/report`。`production_enabled` 只有在录音阶段 UI sample 的 `ai_suggestions > 0` 时才通过；no-cost 模式明确豁免。三次既有 production 真实麦克风 artifact 离线重算分别有 18/19/25 个录音样本，但最大正式卡数均为 0，全部得到 `failed_realtime_ai_suggestion_not_visible_during_recording`。同时，中文语义策略升级为 `general_chinese_technical_meeting.v2`，补齐 SDK/toolkit/工具封装/客户端/权限/bug 等通用技术讨论，并统一 OpenQuestion 的状态与候选关键词表。真实 production 文件链已通过：66 秒中文技术 WAV -> 本地离线 FunASR -> 真实 gpt-5.5，得到 1 条建议、3 条方案、909 字纪要、证据完整，捕获三次派生合计 15188 tokens。Chrome fake-audio-file 在当前环境 RMS/peak 均为 0，正式关闭为环境 No-Go。当前 8765 已受控重启并加载最新源码与 production provider；但“真实麦克风录音期间正式 AI 卡可见”仍未通过，不得宣称生产完成。

2026-07-11 DEC-330 result: 正式自动建议链路完成成本与竞态收敛。后端 `run_once` 每个 API 请求最多执行 1 个远端候选，剩余候选保留到后续触发；Workbench partial hint 只显示本地实时提醒，不再在缺少 final 时空调用 LLM；END/finalize final 先持久化 session 再发送给浏览器；请求在途时合并为一个 `autoSuggestionPending`，结束后补跑，避免并发和触发丢失。静态 cache key 更新为 `20260711-auto-suggestion-reliability1`。TDD 新增单候选、partial-only、finalize persist-before-send、pending trigger 和 cache key 覆盖；旧 live 测试按事件类型消费合法 candidate frame。最终回归：Python `172 passed`、Node gate `12 passed`、全按钮浏览器 E2E `go`、23 张截图、syntax/diff check 通过。边界：正式卡仍要求可接受 final；当前不擅自重启带用户 production LLM 配置的 8765 Python 进程，当前 `/static/workbench.js` 已是新代码但 `/workbench` 仍由启动时缓存 HTML 引用旧 key，需保留配置后受控重启。

2026-07-11 DEC-329 result: 多 Agent 审查后，真实麦克风 verifier 已从单一 realtime latency 判断升级为 fail-closed 双门禁。`workbench_browser_live_mic_gate.mjs` 直接检查 audio health、recording-phase text、合法 SLO、reminder backend probe，以及建议/方案/纪要/证据/同 session UI/已复盘/录音 SHA；production 还要求 remote non-mock LLM usage。Node 行为测试完成 RED/GREEN，当前 `12 passed`。历史 45 秒与 10 分钟真实成功 artifact 严格重算仍为 `passed_no_cost_mainline`；10 分钟 realtime 为 `passed_realtime_partial_final_slow`。新鲜真实麦克风 artifact=`realtime-experience-gate-v2-real-mic-success-20260711` 因现场声音覆盖技术 TTS，ASR 主要识别到加密货币/美股讨论，得到 `asr_semantic_quality_blocked`；虽然首字 `7219ms`、录音保存/SHA 通过，但 `mainline_completion_status=failed_mainline_completion`、卡片/方案/纪要为 0，verifier 正确 exit 1。Chrome fake-audio-file 仍近静音，也同时被 audio/realtime/reminder/mainline gate 拒绝。当前不放宽质量门禁，不把 No-Go 包装为 Go。

2026-07-11 DEC-328 result: 真实麦克风 verifier 已新增 `realtime_experience_status/report`，将“有效声音后首字是否在 15 秒内可见”设为硬门禁，将 final 60 秒观察线作为独立 warning，避免把 partial/revision 已实时追加但 final 较慢误报成整个主链路失败。45 秒真实麦克风 no-cost 复测 artifact=`artifacts/tmp/browser_live_mic/realtime-experience-gate-real-mic-20260711`：`health_status=audio_capture_health_passed`、`first_text_after_audio_active_latency_ms=8247`、`first_final_after_audio_active_latency_ms=45059`、`partial_visible_count=2`、`final_visible_count=1`、`realtime_experience_status=passed_realtime_full`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=252`、录音导出 200 且 SHA 一致、console/network error 为 0。fake-audio-file 诊断因音量过低得到 `failed_realtime_text_not_visible`；DEC-329 已进一步把 audio health 显式纳入 fail-closed。边界：本轮不是生产 LLM evidence，不替代 10/20 分钟稳定性或自然多人会议验收；DEC-327 的 10 分钟 `first_final=288627ms` 仍是 ASR final latency 阻塞项。

2026-07-11 DEC-327 result: 10 分钟真实浏览器麦克风 no-cost soak 已完成。artifact=`artifacts/tmp/browser_live_mic/real-mic-normalizer-v5-10min-nocost-20260711-135007`，`input_mode=real_browser_mic`、`health_status=audio_capture_health_passed`、`chunk_count=2000`、`derivation_mode=no_cost_deterministic`、`counts_as_production_llm_evidence=false`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=402`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`frontend_utterance_count=37`、`frontend_card_count=4`、`frontend_minutes_visible=true`、`meeting_cockpit_stage=已复盘/reviewed`、`meeting_cockpit_counts.transcript=37`、`meeting_cockpit_counts.realtime_reminders=46`、`meeting_cockpit_counts.audio=已保存`、`meeting_cockpit_counts.minutes=已生成`、`live_reminder_drift_status=passed`、console/network error 为 0。录音中 before_stop 样本显示 `partial_draft_count=41`、`live_partial_exists=true`、`cockpit_counts.transcript=43`、`cockpit_counts.realtime_reminders=11`，说明长会录音中仍持续追加文字和提醒。TDD 修复 ASR normalizer v6：覆盖 `picture er / featurelag / row|raw|rall|ll back checklist / dicloster / havect / haveg如超 / orourer / honor|那or / ror 超过 / low看板` 等 10 分钟残留，normalizer suite `23 passed`，artifact spot-check 残留 near-miss 全为 false，目标词 `SLO看板/feature flag/rollback checklist/Redis cluster/Kafka lag/order-worker/owner` 为 true。边界：本轮不是生产 LLM evidence，不是 20 分钟 gate；`first_final_after_audio_active_latency_ms=288627` 仍是 ASR final latency 阻塞。

2026-07-11 DEC-326 result: 左侧 `本场会议` overview jump 修复后，重新跑本机外放中文技术会议脚本 + Chrome `getUserMedia` 真实麦克风 no-cost 3 分钟主链路。artifact=`artifacts/tmp/browser_live_mic/real-mic-post-overview-jump-3min-nocost-20260711-123349`，`input_mode=real_browser_mic`、`health_status=audio_capture_health_passed`、`chunk_count=600`、`derivation_mode=no_cost_deterministic`、`counts_as_production_llm_evidence=false`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=354`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`frontend_utterance_count=12`、`frontend_card_count=4`、`frontend_minutes_visible=true`、`meeting_cockpit_stage=已复盘/reviewed`、`meeting_cockpit_counts.audio=已保存`、`meeting_cockpit_counts.minutes=已生成`、`live_reminder_drift_status=passed`、console/network error 为 0。录音中 before_stop 样本已有 `partial_draft_count=41`、`live_partial_exists=true`，说明实时文字会中持续追加；但 `first_final_after_audio_active_latency_ms=180336`，final 仍接近 stop，不能声明生产级 ASR latency。TDD 修复 ASR normalizer v5：新增 `test_normalize_recovers_post_overview_jump_real_mic_release_review_variants`，覆盖 `flow看板 / ure fly / feature fly / roll backlist / ll back checklist / ononor`，红绿后 normalizer suite `22 passed`。边界：本轮不是 20 分钟长会 Go，也不是生产 LLM evidence。

2026-07-11 DEC-325 result: 用户继续指出左侧 `本场会议` 看起来没有实际作用，多 Agent 复核确认数据真实但上半区仍是静态状态数字。实现：`文字记录/实时提醒/AI 建议/方案分析/录音保存/会后复盘` 六行从 `div.state-row` 升级为 `button.overview-jump`，分别带 `data-overview-target=transcript/reminders/suggestions/approach/audio/minutes`；点击会跳转并聚焦到 `#transcript-stream`、`#candidate-panel`、`#suggestions-panel`、`#approach-panel`、`#btn-export-audio` 或 `#minutes-panel`，并按当前 session 内容显示 toast/title 空态反馈。TDD：`test_workbench_meeting_overview_rows_are_actionable_navigation` 先红后绿；all-buttons 脚本新增 `overview_jump_coverage`、`overview_jump_focus_state` 和 6 个截图步骤。最新浏览器证据：`artifacts/tmp/ui_screenshots/workbench-all-buttons-overview-jump-focus-20260711-122831`，`status=go_workbench_all_buttons_smoke`，`overview_jump_coverage` 六项均为 `clicked_navigation`，`overview_jump_focus_state` 六项均为 `active_element_matches=true / target_in_viewport=true / toast_after_click_matches=true`，`screenshot_count=23`。边界：这是 P0 导航和可理解性修复，不新增生产 LLM 证据；P1 仍是“下一步行动栏”、复制追问/忽略/标误报和具体证据跳转。

2026-07-11 DEC-324 result: 会后 snapshot 不应把已可见的 revision 文稿塌成一条长 final。真实麦克风 no-cost artifact `real-mic-cockpit-ux-current-nocost-20260711-113618` 暴露前端最终只显示 `frontend_utterance_count=1`，但后端已有多条 `transcript_revision`；根因是 `resolvedRevisionKeysFromEvents()` 错把普通 final segment id 当作已解决 revision key。修复后只有 `transcript_revision` 的 supersedes/revision_of 会隐藏更早 revision；artifact `artifacts/tmp/browser_live_mic/real-mic-revision-visible-nocost-20260711-114824` 显示 `frontend_utterance_count=9`、`meeting_cockpit_counts.transcript=9`、录音 SHA 一致、console/network error 为 0。边界：这提升会后可读性，不解决 ASR final latency；最新 no-cost run 仍显示 final 基本在 stop 后才出现。

2026-07-11 DEC-323 result: 当前 in-app browser 一度仍显示旧左侧栏 `会议状态/会议重点`，根因不是 DEC-322 代码未生效，而是 8765 旧 uvicorn 进程继续服务旧 HTML。复核命令 `curl http://127.0.0.1:8765/workbench | rg "workbench.js\\?v=|会议状态|本场会议|会议驾驶舱|transcript-mode-label"` 先返回 `workbench.js?v=20260711-focus-filter2` 和旧文案；用 `python3 tools/workbench_server.py stop/start --port 8765` 重启受控服务后，served HTML 变为 `workbench.js?v=20260711-cockpit-ux1`，包含 `本场会议`、`会议驾驶舱` 和 `transcript-mode-label`。in-app browser 刷新后截图留痕：`artifacts/tmp/ui_screenshots/current-workbench-cockpit-reload-20260711/01-current-workbench-after-server-restart.png`。后续 UI 验收必须同时核对源码和 8765 实际 served static 版本，避免把旧进程误判为产品缺陷。

2026-07-11 DEC-322 result: 左侧栏继续作为 Meeting Cockpit，但空态和交互已改得更像真实产品而不是装饰数字列。实现：左侧可见分区从 `会议状态/会议重点` 收敛为 `本场会议/重点筛选`，`aside.left` 增加 `aria-label="会议驾驶舱：查看本场会议主流程状态并筛选实时提醒"`；四类重点筛选在 0 条时禁用，有提醒后启用并过滤右侧实时提醒；`workbench_all_buttons_smoke.mjs` 对零计数类型记录 `disabled_zero_count_filter`；实时文字标题新增 `transcript-mode-label`，录音中显示 `已记录 + 正在听`，稳定 partial 文案为 `已记录`，当前可变尾巴为 `正在听`；`PARTIAL_DRAFT_MIN_CHARS` 从 24 降到 12，让实时上下文更早追加。TDD：`test_workbench_left_cockpit_disables_empty_focus_filters_and_names_business_role`、`test_workbench_realtime_transcript_labels_stable_append_and_live_tail` 先红后绿；回归 `test_workbench.py=102 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、`node --check workbench.js/workbench_all_buttons_smoke.mjs` 通过；浏览器 all-buttons artifact=`artifacts/tmp/ui_screenshots/workbench-all-buttons-cockpit-ux-disabled-20260711-112328`，状态 `go_workbench_all_buttons_smoke`，下载校验覆盖 transcript/minutes/audio。边界：这不新增生产 LLM 证据；`本场会议` 六行是否升级为 click-to-evidence 仍是生产前 P1 决策。

2026-07-11 DEC-321 result: 真实麦克风 verifier 已新增录音期实时提醒漂移 gate，避免未来只看会后最终数字掩盖“录音中不显示提醒”。实现：`workbench_browser_live_mic_verify.mjs` 的每个 `recording_phase_ui_samples` sample 现在同时记录后端 `/live/asr/sessions/{sid}/events` 的 `backend_suggestion_candidate_count`、`backend_partial_hint_count`、`backend_live_reminder_count`；`summary.json` 新增 `live_reminder_drift_status` 和 `live_reminder_drift_report`。判定：录音期最大前端 `c-gap` 若落后后端候选超过 2 条，则输出 `failed_backend_candidates_not_visible` 并 `process.exitCode=1`。TDD：`test_browser_live_mic_verify_detects_live_reminder_drift_during_recording` 先红后绿；回归：`test_workbench.py=99 passed`、`test_asr_stream.py=23 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、`node --check workbench_browser_live_mic_verify.mjs` 通过。真实麦克风 no-cost recheck artifact=`artifacts/tmp/browser_live_mic/real-mic-live-reminder-drift-gate-nocost-20260711-110137`：`live_reminder_drift_status=passed`、`max_recording_backend_live_reminders=3`、`max_recording_frontend_realtime_reminders=3`、`final_session_live_reminder_count=7`、`audio_sha256_matches_session=true`、console/network error 为 0。边界：这是本地 no-cost verifier gate，不是生产 LLM 质量证明。

2026-07-11 DEC-320 result: 已修复录音中实时提醒不持续追加的核心断点。10 分钟 no-cost artifact `artifacts/tmp/browser_live_mic/real-mic-summary-ui-fields-10min-nocost-20260711-102156` 暴露：录音中 transcript/partial 持续增长，但 `realtime_reminders` 大部分时间停在 3；会后 snapshot 才显示 47。根因：`asr_stream.handle_stream()` 持久化 session 时会用 `build_asr_live_events()` 生成 `suggestion_candidate_event`，但 WebSocket 只推 raw ASR `partial/final` 和少量 `partial_hint_event`，前端录音态不会拉 snapshot。实现：`_upsert_live_session()` 返回 live events，后端用 `sent_live_candidate_event_ids` 去重并通过同一 WS 只补发新增 `suggestion_candidate_event`，不推内部 `state_event/scheduler_event/llm_request_draft_event`。TDD：`test_asr_stream_sends_stable_partial_candidate_over_same_websocket` 先红后绿；新增 final 路径回归 `test_asr_stream_sends_live_final_candidate_over_same_websocket`。回归：`test_asr_stream.py=23 passed`、`test_workbench.py=98 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、三个 `node --check` 通过、`workbench_all_buttons_smoke` 为 `go`。短真实麦克风 no-cost recheck：`real-mic-live-candidates-ws-nocost-20260711-104608` 录音中 `realtime_reminders 0 -> 4 -> 5 -> 6`，最终 13；`real-mic-live-candidates-ws-short-sentences-nocost-20260711-104824` 录音中 `0 -> 1 -> 2 -> 3 -> 4`，最终 15；两次均 `audio_sha256_matches_session=true`、console/network error 为 0。边界：两次仍是 no-cost deterministic，不是生产 LLM；当前本机 real-time provider 仍主要在 stop 时产出 final，因此 final/revision 候选会中可见由后端单测锁定，后续需要更长真实会或 provider profile 验证。

2026-07-11 DEC-319 result: 真实麦克风 `summary.json` 已提升为主验收入口，顶层包含 UI 阶段、计数、实时延迟和浏览器/网络错误字段。实现：`workbench_browser_live_mic_verify.mjs` 从 `ui_verification` 把 `workbench_same_session_visible`、`frontend_utterance_count`、`frontend_card_count`、`frontend_minutes_visible`、`meeting_cockpit_stage`、`meeting_cockpit_counts`、`first_text_after_audio_active_latency_ms`、`first_final_after_audio_active_latency_ms`、`partial_visible_count`、`final_visible_count`、`browser_console_error_count`、`network_error_count` 写入 summary 顶层。TDD：`test_browser_live_mic_summary_promotes_ui_acceptance_fields` 先红后绿；回归 `test_workbench.py=98 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、三个 `node --check` 通过。短 real-mic no-cost recheck artifact=`artifacts/tmp/browser_live_mic/real-mic-summary-ui-fields-nocost-20260711-101557`：`input_mode=real_browser_mic`、`health_status=audio_capture_health_passed`、`chunk_count=200`、`asr_final_count=1`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=252`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`workbench_same_session_visible=true`、`frontend_card_count=4`、`frontend_minutes_visible=true`、`meeting_cockpit_stage=已复盘/reviewed`、`first_text_after_audio_active_latency_ms=8473`、`first_final_after_audio_active_latency_ms=59310`、`browser_console_error_count=0`、`network_error_count=0`。边界：仍是 no-cost deterministic，不是生产 LLM；但 summary 已能一眼回答实时文字延迟、建议/方案/纪要、录音保存和 cockpit 状态。

2026-07-11 DEC-318 result: 真实麦克风 E2E evidence 已补 `cockpit_stage` 和 `summary.json`。实现：`workbench_browser_live_mic_verify.mjs` 的 `recording_phase_ui_samples.json` 每个 sample 增加 `cockpit_stage`，`ui_verification.json` 增加 `meeting_cockpit_stage`，并把 stdout 摘要同步写入 `summary.json`，避免终端截断或外层 shell 错误时丢失主结论。TDD：`test_browser_live_mic_verify_records_meeting_cockpit_counts`、`test_browser_live_mic_verify_writes_machine_readable_summary_file` 先红后绿；回归 `test_workbench.py=96 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、三个 `node --check` 通过。短 real-mic no-cost recheck artifact=`artifacts/tmp/browser_live_mic/real-mic-cockpit-stage-summary-nocost-20260711-100829`：`summary.json exists=true`、`input_mode=real_browser_mic`、`health_status=audio_capture_health_passed`、`chunk_count=200`、`asr_final_count=1`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=252`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`；录音 samples 记录 `cockpit_stage=录音中/recording`，最终 `meeting_cockpit_stage=已复盘/reviewed`。边界：这是 no-cost deterministic，不是 production LLM evidence；仍需更长真实会议和生产 LLM 复测。

2026-07-11 DEC-317 result: 左侧会议驾驶舱已补阶段标签，避免空态只显示一列 `0/未保存/未生成`。实现：`会议状态` 标题旁新增 `c-cockpit-stage`，由 `meetingCockpitStage()` 自动同步为 `待开始 / 录音中 / 整理中 / 已记录 / 已复盘`；`syncMeetingOverview()` 更新标签文本和 `data-state`，`setMeetingPhase()` 在阶段变化时同步 cockpit。浏览器 all-buttons smoke 的 `safeReadPageState()` 增加 `cockpitStage`，最新报告 `artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_visual_acceptance_report.json` 记录到阶段序列：initial/delete 为 `待开始`，导入/历史/建议/方案为 `已记录`，纪要/导出/整理后为 `已复盘`。TDD：`test_workbench_meeting_cockpit_stage_tracks_mainline_phase` 先红后绿；回归 `test_workbench.py=96 passed`、`tests/test_workbench_all_buttons_smoke.py=7 passed`、`node --check` 通过、`node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` 为 `go_workbench_all_buttons_smoke`。边界：这改善 cockpit 可理解性，不新增生产 LLM 证据，也不替代真实麦克风长会复测。

2026-07-11 DEC-316 result: 用户再次指出当前页面左侧一列“好像没有实际作用”。复核结论：左侧栏不是 mock；上半区 `会议状态` 从实时文字、候选提醒、建议卡、方案卡、录音资产和纪要状态同步，下半区 `会议重点` 四个按钮用 `data-focus-type=DecisionCandidate/ActionItem/Risk/OpenQuestion` 筛选右侧实时提醒。但当前空态全是 `0/未保存/未生成`，四个 0 条筛选仍可点击，产品表达上确实容易被理解成装饰列。因此计划补充：左侧驾驶舱生产前必须覆盖空态/录音中/会后三种状态；空态要有明确反馈，录音中计数必须随实时文字和提醒增长，会后必须反映同一 session 的建议、方案、录音和纪要；暂不禁用 0 条筛选，因为现有 E2E 依赖点击空态筛选验证，如果后续改为 0 条不可点，必须同步改 E2E 和可访问性测试。10 分钟 post-fix evidence `artifacts/tmp/browser_live_mic/real-mic-cockpit-fix-10min-nocost-20260711-092525` 已证明长会中 final cockpit counts 为 `transcript=1/realtime_reminders=32/ai_suggestions=3/approach=1/audio=已保存/minutes=已生成/decisions=7/actions=3/risks=22/questions=0`，但这仍是 no-cost deterministic，不是生产 LLM 证据。

2026-07-11 ASR normalizer v4 result: 真实麦克风 speaker-to-mic 近场中文技术会议仍会错听 mixed terms。新增 bounded contextual rules 后，artifact `artifacts/tmp/browser_live_mic/real-mic-normalizer-v4-nocost-20260711-093934` 仍保持主链路通过：`provider=funasr_realtime`、`is_mock=false`、`health_status=audio_capture_health_passed`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=342`、`audio_sha256_matches_session=true`、console/network error 为 0；术语检查中 `payment-gateway/error_rate/P99/feature flag/rollback checklist/owner/SLO看板/Kafka lag` 为 true，`honor/caf collect/closter` 为 false。残留：`Redis cluster`、早期 `feature slag/llback checklist` 仍不稳定，10 分钟 run 的 first final latency 达 `228944ms`，实时 UX 主要依赖 partial/draft。下一步不应无限追单词，要转向热词/profile/post-ASR correction 策略和生产 LLM 复测。

2026-07-11 DEC-315 result: 20 分钟真实浏览器麦克风 no-cost 长测录音中暴露会议驾驶舱上半区默认计数 bug：页面实际已有 `31` 个可见文字片段、`3` 个候选提醒事件，右侧 `candidate-panel` 也有提醒，但左侧 `会议状态` 仍显示 `文字记录 0 / 实时提醒 0`。CDP 探针确认 `visibleTranscriptCount()=31`、`currentReminderCount()=3`，手动调用 `syncMeetingOverview()` 仍不更新。根因是 `Number.isFinite(Number(null))` 把默认 `null` 参数误判为显式 `0`。修复：新增 `numericCountOverride()`，`null/undefined/空字符串` 回退到实时 DOM/session state，显式 `0` 仍作为 reset/empty override。TDD：`test_workbench_overview_defaults_do_not_treat_null_as_zero` 先红后绿；focused 回归 `2 passed`，`node --check workbench.js` 通过。边界：正在运行的 20 分钟 evidence 使用旧 JS，会记录该 bug；修复后需要新浏览器主链路复测证明左侧计数不再卡 0。

2026-07-11 left-cockpit clarification: 当前左侧栏不是 mock，也不是装饰计数列；它的业务职责固定为 Workbench 的会议驾驶舱。`会议状态` 是主链路进度灯，负责展示实时文字、实时提醒、AI 建议、方案分析、录音保存、会后复盘是否真的发生；`会议重点` 是会议中 triage 入口，四行按钮按决定、待办、风险、待确认问题筛选右侧实时提醒。当前已实现状态投影、分类计数、筛选、窄屏优先展示；仍需通过本轮 20 分钟真实麦克风 evidence 验证长会录音中这些数字是否持续更新，并在生产验收前决定 `会议状态` 六行是否需要升级为 click-to-evidence 快捷入口。若升级，必须先补键盘顺序和可访问性测试。

2026-07-11 DEC-313 result: Workbench 左侧栏的业务定位已经固定为“会议驾驶舱”，不是装饰计数列。上半区 `会议状态` 投影实时文字、实时提醒、AI 建议、方案分析、录音保存、会后复盘；下半区 `会议重点` 作为决定/待办/风险/待确认问题的候选提醒筛选入口。本轮在 in-app browser 截图中发现窄屏布局会把 `left` 排到 `center/right` 之后，导致驾驶舱可发现性差，用户会感知为左侧栏无实际作用。修复：`@media (max-width:900px)` 的 `grid-template-areas` 改为 `topbar -> left -> center -> right -> status`，让会议驾驶舱在窄屏位于顶部操作区之后、实时文字和详情面板之前。TDD：新增 `test_workbench_mobile_layout_keeps_meeting_cockpit_before_detail_panels`，先红灯证明旧顺序为 `topbar/center/right/left/status`，再改 CSS；回归 `test_workbench.py` 为 `93 passed`，`tests/test_workbench_all_buttons_smoke.py` 为 `7 passed`，`node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` 为 `go_workbench_all_buttons_smoke`。浏览器证据：`artifacts/tmp/ui_screenshots/left-column-responsive-20260711/browser-report.json` 显示 viewport `696x761`、computed `gridTemplateAreas="topbar" "left" "center" "right" "status"`，截图 `workbench-responsive-cockpit.png`。边界：这是 UI 可发现性修复，不改变 ASR/LLM/录音/session trace，也不替代 20 分钟真实长会 gate；后续如果“会议状态”也变成可点击导航，需要补键盘顺序和可访问性测试。

2026-07-11 DEC-312 result: Workbench 左侧“会议重点”已从只读计数推进为实时候选提醒筛选入口。多 Agent 审查一致认为：DEC-311 的“会议状态”适合作为主链路状态投影，但“会议重点”如果仍只能看数字，会继续被用户感知为无实际作用。实现：`决定了什么 / 待办事项 / 风险提醒 / 待确认问题` 四行改为可点击 button，分别携带 `data-focus-type=DecisionCandidate/ActionItem/Risk/OpenQuestion` 和 `aria-pressed`；点击后右侧 `candidate-panel` 只显示对应类型提醒，并显示“正在查看 / 显示全部”。筛选只改变用户可见视图，不删除事件、不改变 session trace、不改变 `c-gap/s-candidates` 全场提醒总数；候选提醒先筛选再去重/封顶，避免长会中某类提醒被先截断后误判为空。修复同时把快照路径和直播增量路径统一到 `syncCandidateFocusCounts()`，解决真实会议中右侧已有候选提醒但左侧重点计数没有随实时事件更新的风险；`syncMeetingOverview()` 也新增 `currentReminderCount()/visibleCandidateReminderCount()`，避免 `实时提醒` 从旧 DOM 数字回读成 0。TDD：新增 `test_workbench_meeting_focus_rows_are_actionable_candidate_filters`、`test_workbench_candidate_filter_shows_clear_state_without_changing_counts`、`test_workbench_live_candidate_events_refresh_focus_counts_with_snapshot_path`、`test_workbench_overview_reminder_count_derives_from_current_events_not_stale_dom`，先红后绿，focused suite `4 passed`。浏览器验证：in-app browser `?demo=1` 显示四类候选，点击 Risk 后只剩 Risk，清除后四类恢复，artifact=`artifacts/tmp/ui_screenshots/workbench-left-focus-filter-20260711/browser-report.json`；headless E2E `node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` 为 `go_workbench_all_buttons_smoke`，新增 `focus_filter_coverage`，导入录音样例覆盖 Decision/Risk/OpenQuestion 有候选筛选，ActionItem 无候选时验证空态筛选，截图 `05-candidate_filter_risk.png` 和 `06-candidate_filter_all.png`。边界：这仍不是 20 分钟真实长会 gate，也不是生产 LLM evidence；下一步仍是长会议主链路和真实麦克风验收。

2026-07-11 DEC-311 result: Workbench 左侧栏已从“裸信号计数器”收敛为主链路状态投影。根因：旧左侧栏虽然来自真实 `state_event/suggestion_candidate_event/partial_hint_event/approach_cards`，但只显示“决定/待办/风险/问题/正在提醒/方案分析”的数字，无法回答用户最关心的实时会议主流程是否跑通。实现：左侧新增 `会议状态`，显示 `文字记录(c-transcript)`、`实时提醒(c-gap)`、`AI 建议(c-cards)`、`方案分析(c-approach)`、`录音保存(c-audio)`、`会后复盘(c-minutes)`；保留 `会议重点` 的决定、待办、风险、待确认问题作为候选分类。新增 `syncMeetingOverview()` 统一从当前 DOM/session state 同步左侧状态；自动建议 run-once 现在先 merge 到 `currentSuggestionCards` 再渲染，避免右侧建议卡已出现但左侧 AI 建议仍为旧值。TDD：`test_workbench_left_column_explains_meeting_mainline_status` 红灯证明旧 HTML 没有会议状态；`test_workbench_auto_suggestion_updates_left_ai_suggestion_count_state` 红灯证明自动建议不更新 `currentSuggestionCards`；修复后相关 focused tests `4 passed`，`node --check workbench.js` 通过。浏览器验证：重启 8765 后 `/workbench` 左侧显示“会议状态 / 文字记录 0 / 实时提醒 0 / AI 建议 0 / 方案分析 0 / 录音保存 未保存 / 会后复盘 未生成 / 会议重点”；截图 `artifacts/tmp/ui_screenshots/workbench-left-status-20260711/workbench-left-status-mobile.png`。边界：这不是 20 分钟真实长会 gate，也不是生产 LLM evidence；后续若要让左侧变成行动面板，需要补点击筛选/证据跳转。

2026-07-11 DEC-310 result: mixed-topic no-cost 会议纪要已记录为独立产品边界。DEC-308 只解决普通会议被发布模板污染；10 分钟 mixed real-mic no-cost soak `artifacts/tmp/browser_live_mic/real-mic-mixed-10min-nocost-20260710-210533` 又暴露多主题纪要可能丢失产品反馈或发布风险。实现后 `deterministic_demo` 仍保持 no-cost/not_called/local only，但 key points 扩到 6，mixed transcript 纪要必须同时保留用户访谈反馈、首页引导、持续看到上下文、录音/文字稿/会议纪要，以及灰度比例、P99/错误率、回滚 owner、观察窗口、兼容性测试。复测 `artifacts/tmp/browser_live_mic/real-mic-mixed-minutes-both-nocost-20260710-212729` 显示 `suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=514`、`audio_sha256_matches_session=true`，且 minutes 同时包含产品主题和发布主题。边界：仍是 no-cost deterministic，不算 production LLM quality。

2026-07-11 DEC-309 result: 长会议候选提醒已做用户可见去重/折叠。10 分钟 real-mic mixed no-cost soak 证明主链路不崩、录音保存和 SHA OK、录音中 `partial_draft_count≈0 -> 87`，但候选提醒面板重复刷同一类提醒。实现：`visibleCandidateReminders()` 按 `event_type + gap/hint/target + reminder text` 去重，可见提醒最多 8 条，较早/重复提醒折叠为 `N 条较早或重复提醒已收起`。TDD：`test_workbench_candidate_reminders_are_deduped_and_capped_for_long_meetings`；复测 `artifacts/tmp/browser_live_mic/real-mic-mixed-context-dedupe-nocost-20260710-212125` 显示 `20 条较早或重复提醒已收起`，最终 mixed fix run 显示 `11 条较早或重复提醒已收起`。边界：事件仍完整保存在 session 中，折叠只影响用户可见面板。

2026-07-10 DEC-308 result: 真实麦克风普通中文会议回归暴露并修复 no-cost deterministic 派生错域问题。首轮普通会议 artifact=`artifacts/tmp/browser_live_mic/real-mic-ordinary-append-nocost-20260710-205103` 证明 real browser mic、FunASR real provider、录音保存、实时追加闭环：`partial_draft_count` 录音中从 0 增到 9，`audio_export_http_status=200`、`audio_sha256_matches_session=true`，但会后纪要仍输出灰度/回滚/P99 发布模板。实现：`deterministic_demo` 保持 no-cost/not_called/不读密钥/不调用中转站，但建议、方案和纪要改为读取当前 transcript 做上下文派生；发布语境保留发布模板，普通会议生成用户访谈反馈、首页引导、实时上下文、录音/文字稿/会议纪要和行动项。TDD：新增 `test_demo_no_cost_derivation_uses_transcript_context_for_ordinary_meeting`，红灯证明旧模板错域，修复后 no-cost suite `4 passed`。复测 artifact=`artifacts/tmp/browser_live_mic/real-mic-ordinary-context-nocost-20260710-205801`：`input_mode=real_browser_mic`、`provider=funasr_realtime`、`acceptance_eligible=true`、`recording_phase_ui_samples=15`、`partial_draft_count 0 -> 9`、`suggestion_card_count=1`、`approach_card_count=1`、`minutes_char_count=431`、`audio_sha256_matches_session=true`；最终方案/纪要包含“用户访谈反馈 / 首页 / 持续看到 / 录音、文字稿和会议纪要”，不再包含“灰度 / 回滚 / P99 / 错误率”。边界：仍是 no-cost deterministic，不计入生产 LLM evidence；ASR 把“今天”识别成“这天”等质量问题仍存在，20 分钟真实长会 gate 未完成。

2026-07-10 DEC-307 result: Workbench 实时文字追加规则继续收敛：`临时稿` 可见追加不再依赖发布/灰度/回滚/P99 等技术语义关键词。根因是 DEC-305 虽然把页面从“只替换 live tail”修到 append-first projection，但 `shouldCommitPartialDraft()` 仍把可见转写绑定到 `PARTIAL_DRAFT_MARKERS`，会导致普通会议、非工程会议或没有命中关键词的片段仍看起来只在替换。新规则：稳定 partial 是否追加只看 segment、长度和置信度；语义关键词只服务候选提醒/建议卡，不控制主文字区是否可见。后端仍保存 append-only session/events/audio trace，前端 `escapeHtml` 只负责安全渲染，不是追加策略。TDD：新增 `test_workbench_partial_draft_visibility_is_not_gated_by_semantic_markers`，红灯证明旧实现被关键词卡住，修复后 focused test `1 passed`；相关 partial/clean projection 回归 `6 passed, 79 deselected`；`node --check workbench.js` 通过。边界：仍需真实长会验证 `临时稿` 不会过多、滚动不跳、final/revision 去重手感正常；这不改变 ASR 质量和 20 分钟真实长会 gate 仍未完成的事实。

2026-07-10 DEC-306 result: Workbench 用户可见文字/建议面板已从“事件日志味”收敛为 clean user projection。真实麦克风 E2E 暴露的问题是主文字区显示 `修正原话：rec_...`，实时建议面板显示 `asr_ev_...` 和 `transcript_partial:...`；这些 metadata 现在只保留在事件和 DOM dataset/evidence 中，不作为默认用户文案。实现：snapshot 渲染在同 segment final 覆盖时隐藏 revision；live final 会移除同 segment revision 行；revision 行不再展示 `修正原话`；candidate reminder meta 改为 `来自会议原话` / `来自实时文字`。TDD：新增 `test_workbench_renders_transcript_revision_rows_without_engineering_metadata`、`test_workbench_snapshot_renderer_hides_revisions_covered_by_final_segment`、`test_workbench_live_final_removes_revision_rows_for_resolved_segment`、`test_workbench_candidate_reminders_hide_engineering_evidence_ids`；`test_workbench.py` 为 `84 passed`，聚合主线回归 `102 passed, 184 deselected`。真实麦克风 no-cost 证据：`artifacts/tmp/browser_live_mic/real-mic-clean-ui-nocost-20260710-203134`，visible Chrome + real browser mic，`provider=funasr_realtime`、`provider_mode=real`、`is_mock=false`、`acceptance_eligible=true`、`audio_sha256_matches_session=true`、录音中 `partial_draft_count` 从 1 增到 8，最终 `frontend_utterance_count=1`、`frontend_card_count=4`、`frontend_minutes_visible=true`、console/network error 为 0；最终 transcript/candidate 文案不再包含 `修正原话`、`修正：`、`rec_`、`asr_ev_`、`transcript_partial:`。边界：本轮仍是 no-cost deterministic derivation，不增加生产 LLM evidence；`first_text_after_audio_active_latency_ms≈7220`、`first_final_after_audio_active_latency_ms≈55301`，ASR 对中文技术词/外放近场仍有明显错词，20 分钟真实长会 gate 未完成。

2026-07-10 DEC-305 result: 用户再次指出“页面一直替换、不追加”后，前端展示边界已收敛为 append-first user projection：稳定 partial 增量作为多个 `临时稿` 片段追加，当前不稳定尾巴只在 `实时` 行内更新；final/revision 到来后按 segment 移除所有对应临时片段，再展示正式发言/修正，避免重复。这不是转义问题，`escapeHtml` 只做安全显示，业务核心是事件投影、片段追加、尾巴更新和 final 去重。实现同时恢复文件恢复后丢失的三个主线契约：`partial_hint_event` 仅在生产 LLM 已配置且非 no-cost self-test 时触发自动建议；`整理会议` 继续使用 `ORGANIZE_FAST_SUGGESTION_BUDGET=1` 避免长会候选拖死方案/纪要；普通历史列表继续默认隐藏 demo/mock，只有 demo opt-in 才请求 `include_demo=true`。验证：新增 `test_workbench_partial_drafts_append_chunks_and_keep_only_live_tail_mutable` 与 `test_workbench_final_removes_all_provisional_partial_chunks_for_segment`；`test_workbench.py` 为 `80 passed, 2 warnings`；`node --check workbench.js` 通过。边界：本轮未跑新的真实麦克风长会，也未新增生产 LLM evidence；后续仍需浏览器实测截图/长会 gate 验证滚动和 final 替换手感。

2026-07-10 DEC-304 result: 公开音频路线已收窄为少量、可复现、无下载扩张的中文 ASR 回归辅助，不能再作为当前主线。用户指出“页面一直替换、不追加”后，根因确认不是转义问题，而是 FunASR stream worker 只向前端发短 rolling partial，导致 Workbench 缺少可稳定追加的会议上下文。实现：`funasr_stream_worker.py` 现在对非累积 streaming partial 发出 merged partial 文本；Workbench 支持 stable partial 作为 `临时稿` 可见追加，并在 final/revision 到来后通过 live 路径和 session snapshot 路径移除已解决的临时稿；后端保留 stable partial snapshot，避免后续短尾巴覆盖可用上下文。TDD/验证：新增 `test_stream_worker_emits_accumulated_partial_text_for_live_transcript`、`test_workbench_snapshot_renderer_hides_resolved_partial_drafts` 等回归；ASR runtime `21 passed`；partial 主线回归 `30 passed, 126 deselected`；聚合主线回归 `95 passed, 185 deselected`；`node --check` 和 `git diff --check` 通过。真实麦克风 no-cost 证据：`artifacts/tmp/browser_live_mic/real-mic-merged-partial-snapshot-filter-nocost-20260710-193221`，`input_mode=real_browser_mic`、`provider=funasr_realtime`、`acceptance_eligible=true`、`audio_sha256_matches_session=true`、`transcript_partial` 已是长累积文本，final 前出现 `partial_hint_event` 和 `local_deterministic_asr_stable_partial_skeleton` 候选；刷新后 transcript 不再重复显示已被 final 解决的 `临时稿`。边界：本轮是 no-cost deterministic derivation，不新增生产 LLM 证据；`first_final_after_audio_active_latency_ms` 仍约 48s，ASR 对自然外放/中文技术词仍有错词，不能声明生产级长会完成。

2026-07-10 DEC-303 result: Workbench 真实麦克风暴露的中文技术会议近音错词已用 bounded normalizer 修复并补入回归，入口仍为 `docs/real-mic-workbench-mainline-report-2026-07-10.md`。真实麦克风 no-cost 复测 artifact=`artifacts/tmp/browser_live_mic/real-mic-normalizer-v3-nocost-20260710-185357` 显示主链路仍可跑通：`input_mode=real_browser_mic`、`ui_coverage=visible_chrome`、`provider=funasr_realtime`、`provider_mode=real`、`acceptance_eligible=true`、`health_status=audio_capture_health_passed`、`chunk_count=160`、`asr_final_count=1`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=252`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`，但该 run 也暴露 `t九/t一九`、`四low看板`、`ure flap`。实现：仅在发布/灰度/错误率/回滚/延迟/毫秒上下文中修 `发布庭审 -> 发布评审`，仅在指标上下文中修 `t九九/t九/t一九 -> P99`，修 `斯隆看板/四low看板 -> SLO看板`，修 `ure flap -> feature flag`；不改通用词典排序，不扩大为全局替换，不新增远端 ASR/公开音频下载。验证：`test_normalize_recovers_third_real_browser_mic_release_review_variants` 和 `test_normalize_recovers_fourth_real_browser_mic_release_review_variants` 红绿闭环；`test_transcript_normalizer.py` 为 `19 passed`；主线相关回归为 `79 passed, 148 deselected`。边界：这只提升已观测中文技术会议术语稳定性，不能声明中文 ASR 生产级完成，也不能替代 20 分钟真实长会议 gate；后续不应继续无限追词，需转向热词/轻量纠错/ASR latency 的系统性优化。

2026-07-10 DEC-300 result: Workbench 真实浏览器麦克风 5 分钟 fast-organize 复测通过，入口仍为 `docs/real-mic-workbench-mainline-report-2026-07-10.md`。新证据 artifact=`artifacts/tmp/browser_live_mic/real-mic-workbench-5min-fast-organize-history-filter-20260710-175608`，visible Chrome + real browser mic + 本机 `say -v Tingting` 中文技术会议外放，`provider=funasr_realtime`、`provider_mode=real`、`is_mock=false`、`acceptance_eligible=true`、`health_status=audio_capture_health_passed`、`events_count=116`、`drafts=24`、`suggestion_card_count=1`、`approach_card_count=3`、`minutes_char_count=817`、`llm_call_count=3`、`llm_usage_total_tokens=16332`、`audio.duration_ms=299776`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`organize_wait_status=matched`。这证明 DEC-298 的 organize 快路径在 24 个候选的 5 分钟真实麦克风会话中没有再被建议候选拖住。新增风险：`first_text_after_audio_active_latency_ms=7178` 仍偏高，`first_final_after_audio_active_latency_ms=175636`，实时体验主要依赖 partial/revision；ASR 仍有 `Redis/feature flag/checklist/owner/P99` 等技术词错听。当前仍不能声明 20 分钟长会议生产 gate 完成。

2026-07-10 DEC-301 result: Workbench 浏览器层主按钮 E2E 覆盖已补齐，入口仍为 `docs/real-mic-workbench-mainline-report-2026-07-10.md`。脚本 `code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` 新增 `button_coverage` 表和 `btn-export-audio` 下载目标验证，artifact=`artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke`，`status=go_workbench_all_buttons_smoke`，`screenshot_count=15`，`fake_llm_request_count=10`，下载目标覆盖 `transcript.txt`、`minutes.md`、`audio.wav`。覆盖表显示 `btn-upload/history/cards/approach/minutes/live/export-transcript/export-minutes/export-audio/auto-suggestion-toggle/organize/delete` 在导入录音浏览器 E2E 中被点击验证；`btn-record/btn-stop` 由真实浏览器麦克风 E2E `workbench_browser_live_mic_verify.mjs` 覆盖。验证：`python3 -m pytest tests/test_workbench_all_buttons_smoke.py -q` 为 `6 passed, 1 warning`；`node --check code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` exit 0；`node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` exit 0。边界：这不是 Tauri 安装包点击验收，也不是 20 分钟长会 gate。

2026-07-10 DEC-302 result: Workbench 会议中自动建议触发竞态已修，入口仍为 `docs/real-mic-workbench-mainline-report-2026-07-10.md`。根因是 `asr_stream.handle_stream()` 旧顺序先把 realtime `final` 发给浏览器，再 `_upsert_live_session()`；前端收到 final 会立即调用 `/auto-suggestions/run-once`，可能早于同 session 记录和候选持久化。已改为 chunk partial/final 和 VAD endpoint final 先更新并持久化 live session，再发送给浏览器；新增红绿测试 `test_asr_stream_persists_live_final_before_sending_to_browser`。验证：`test_asr_stream.py` 为 `19 passed`，主线相关回归为 `60 passed`，`workbench_all_buttons_smoke` exit 0。短真实麦克风 no-cost 复测 artifact=`artifacts/tmp/browser_live_mic/real-mic-order-fix-nocost-20260710-183032`，`input_mode=real_browser_mic`、`ui_coverage=visible_chrome`、`provider=funasr_realtime`、`provider_mode=real`、`is_mock=false`、`acceptance_eligible=true`、`health_status=audio_capture_health_passed`、`sample_count=479232`、`chunk_count=100`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=252`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`first_text_after_audio_active_latency_ms=6158`、`first_final_after_audio_active_latency_ms=30258`。边界：本复测为 `derivation_mode=no_cost_deterministic`，`counts_as_production_llm_evidence=false`，不能替代生产 LLM 或 20 分钟长会 gate。

2026-07-10 DEC-299 result: Workbench 历史列表已完成 mock/demo 默认隔离。后端 `GET /live/asr/sessions` 默认过滤 `mock_asr_session`、`local_asr_event_file`、`input_source=mock/local_event_file/simulated_realtime_wav`、`provider=local_mock_asr/fake` 或 acceptance blocker 含 `mock_or_demo_session/local_event_file_not_real_input` 的记录；只有显式 `?include_demo=true` 才返回这些测试/演示记录。前端 `loadSessionHistory()` 改为调用 `sessionHistoryPath()`，普通 Workbench 走默认真实历史，`?demo=1` 或 localStorage demo opt-in 才请求 `include_demo=true`。验证：新增红绿测试 `test_asr_live_sessions_list_endpoint_hides_mock_sessions_by_default`、`test_workbench_history_requests_demo_sessions_only_after_demo_opt_in`；相关回归 `40 passed`；`node code/web_mvp/e2e/workbench_smoke.mjs` 通过。影响：正常用户打开 Workbench 时不会再把 mock/demo 历史混进主产品视图，降低“真实会议是否跑通”的判断污染。

2026-07-10 DEC-298 result: Workbench 真实浏览器麦克风 2 分钟主链路从“录音/ASR/单建议卡可用但整理会议超时”推进到“实时文字 + 正式建议 + 方案分析 + 会议纪要 + 录音保存/导出”闭环。根因确认：`整理会议` 串行执行 `llm-execution-runs -> approach-cards -> minutes`，真实会议会产生多条 `llm_request_draft_event`；此前 `/llm-execution-runs` 对候选全量/多量顺序调远端 LLM，5 分钟真实麦克风会话中 33 个候选导致 45s 内后续方案/纪要没有轮到。实现：后端新增 `llm-execution-candidate-selection.v1`，支持 `max_candidates`、默认上限和选择摘要；前端 `整理会议` 使用 `ORGANIZE_FAST_SUGGESTION_BUDGET=1`，只先生成一条最高价值正式建议后立即进入方案和纪要，单独“生成会议建议”仍可走普通预算。验证：先红后绿新增 `test_asr_live_llm_execution_runs_enabled_caps_long_meeting_candidates`、`test_asr_live_llm_execution_runs_enabled_honors_request_candidate_budget`、`test_workbench_organize_meeting_uses_fast_suggestion_candidate_budget`；相关回归 `40 passed`。真实链路 artifact=`artifacts/tmp/browser_live_mic/real-mic-workbench-2min-fast-organize-20260710-173727`，visible Chrome + real browser mic + 本机外放中文技术会议脚本，`provider=funasr_realtime`、`provider_mode=real`、`acceptance_eligible=true`、`health_status=audio_capture_health_passed`、`first_text_after_audio_active_latency_ms=7197`、`first_final_after_audio_active_latency_ms=32304`、`suggestion_card_count=1`、`approach_card_count=3`、`minutes_char_count=668`、`llm_call_count=3`、`llm_usage_total_tokens=15364`、`audio_export_http_status=200`、`audio_sha256_matches_session=true`、`organize_wait_status=matched`。边界：这不是 20 分钟长会 Go；ASR 中文技术词仍有错字/英文词错听，需继续优化；全按钮 E2E 和生产安装包验收仍未完成。

2026-07-10 DEC-297 result: 中文 ASR 优化已从“继续下载/继续评测”收敛为产品双 ASR 路线并落到代码。实时会议入口现在优先选择 `funasr_realtime`，sherpa 作为 fallback；`FunasrSidecarRecognizer -> funasr_stream_worker.py` 显式使用 `balanced_chinese_meeting` profile，传入 `chunk_size=0,30,15`，worker 支持可测试的 `--chunk-size`/look-back 参数；该配置在 MagicHub 中文会议样例上把 streaming RTF 从原 baseline `1.169649` 改善到 `0.389919`，代价是 worker 推理窗口约 1.8s。本轮产品式 sidecar 探针又发现旧 worker final 只输出最后片段，已新增 partial 合并逻辑，公开 OSR 小样本探针 `artifacts/tmp/asr_reports/realtime_funasr_sidecar_public_probe_20260710_after_merge.json` 显示 `provider=funasr_realtime`、`asr_profile=balanced_chinese_meeting`、`chunk_size=0,30,15`、`duration_seconds=19.967625`、`rtf=0.552726`、`partial_count=11`、`final_text_chars=68`，不调用远端 ASR/LLM。会后录音/导入入口 `/live/asr/transcribe-file/sessions` 现在调用 `transcribe_funasr.py --offline-batch`，优先使用本地缓存 SeACo Paraformer + VAD + 标点模型，保存 `post_meeting_asr_profile` 到 session/event_source；公开中文小样本离线结果 `weighted_cer=0.014706`、`avg_rtf=0.048278`、`release_gate_status=baseline_passed_for_current_public_samples`。验证：`code/asr_runtime/tests/test_transcribe_funasr.py` 20 passed，`test_funasr_sidecar.py` 4 passed，`test_file_convert.py` 7 passed，`test_asr_stream.py` 18 passed。边界：仍不下载更多公开音频，不读取用户私有录音，不读取 `configs/local`，不调用远端 ASR，不把公开样本误识别硬编码进 normalizer；真实麦克风长会产品手感仍需后续验收。

2026-07-10 DEC-296 result: 公开中文小样本 ASR baseline 已完成，入口为 `docs/public-chinese-asr-baseline-report-2026-07-10.md`，工具为 `tools/public_chinese_asr_baseline_report.py`，证据为 `artifacts/tmp/asr_reports/public_chinese_asr_baseline_20260710.json`。本轮实际只使用 OSR Mandarin 4 段带参考文本小样本和 MagicHub 1 段中文会议样例，未下载 AliMeeting/AISHELL-4 GB 级大包，`remote_asr_call_count=0`、`llm_call_count=0`、`raw_audio_uploaded=false`。结果显示 `weighted_cer=0.047794`、`avg_cer=0.049955`、`max_rtf=1.169649`、`release_gate_status=needs_asr_optimization_before_release`；单进程复用模型对照 `transcribe_only_rtf=1.084665`，会议样例仍 `rtf=1.511296`。结论：公开音频不是产品功能，也不是继续下载的理由；当前停止扩大数据集，下一步固定转向本地流式 ASR runtime、断句/标点可读性、Workbench 实时 ASR -> 建议卡主链路修复，禁止把 OSR 句子误识别硬编码进产品 normalizer。

2026-07-10 DEC-295 result: 用户确认公开样本方向必须以中文为主后，已把公开音频 bounded sample extraction approval 从 archive 漂移恢复为 live 工具。工具入口 `tools/public_audio_sample_extraction_plan.py`，测试入口 `tests/test_public_audio_sample_extraction_plan.py`，示例入口 `data/asr_eval/public_sample_plan.example.json`。本轮 no-download 审批报告 `artifacts/tmp/asr_reports/public-audio-sample-extraction-approval-20260710-final-verify.json` 显示：`plan_status=blocked_no_planned_samples`、`source_id=alimeeting_openslr_slr119`、`source_language=zh-CN Mandarin`、`dataset_role=primary_mandarin_meeting_acoustics`、`meeting_acoustics_evidence=true`、`counts_toward_public_meeting_wall_clock_candidate=true`、`planned_sample_count=0`、`safe_to_download_now=false`、`safe_to_extract_now=false`、`remote_asr_call_count=0`、`llm_call_count=0`、`raw_audio_uploaded=false`。当前排序固定为：AliMeeting SLR119 第一中文会议声学候选，AISHELL-4 SLR111 第二中文会议声学补充，AISHELL-1 SLR33 只能做普通话 runtime smoke baseline，不能计入会议声学或 wall-clock meeting evidence。planned sample 一旦提供，必须具体到安全相对 archive member path、clip window、license citation、cleanup_required 和 64 位 lowercase sha256，且不得包含 placeholder 文本；校验通过也只代表 `schema_validated_no_download`，不会生成下载/抽取/转码命令。

2026-07-10 DEC-294 result: 新增真实/公开音频 wall-clock gate，避免把 repaired synthetic quality Go、20 分钟 synthetic soak Go、短真实 browser mic Go 拼成不存在的真实长会 Go。工具入口 `tools/real_public_audio_wall_clock_gate.py`，最新证据 `artifacts/tmp/asr_reports/real-public-audio-wall-clock-gate-20260710-final-verify.json`：`gate_status=blocked_real_or_public_wall_clock_soak_missing`、`blockers=[real_or_public_wall_clock_soak_missing]`。该报告确认：`repaired_synthetic_quality.ready=true`，`synthetic_soak.ready=true` 但 `counts_as_real_or_public_wall_clock_soak=false`，`real_mic_short.ready=true` 但 `duration_status=missing_or_too_short`，`wall_clock_soak.ready=false`。公开音频白名单报告 `artifacts/tmp/asr_reports/public-audio-source-whitelist-20260710-final-verify.json` 显示 `source_validation_status=passed`、`source_count=3`、`safe_to_download_now=false`、`next_action=create_bounded_sample_extraction_plan`。官方 OpenSLR 复核仍支持当前边界：AliMeeting SLR119 是 CC BY-SA 4.0 的真实多方会议大包，Eval 3.42G；AISHELL-4 SLR111 是 CC BY-SA 4.0 的会议场景大包，test 5.2G；AISHELL-1 SLR33 是 Apache 2.0 普通话朗读语料，data 15G。下一步不能自动下载大包；要么用户配合 20 分钟级真实麦克风 wall-clock soak，要么先形成公开音频 bounded sample extraction approval。

2026-07-10 DEC-292/DEC-293 result: ASR 质量门禁从 No-Go 推进到 repaired local synthetic candidate。入口仍为 `docs/asr-mainline-quality-batch-report-2026-07-10.md`。新增 batch coverage audit 后，旧 variants 证据 `artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-variants-20260710-coverage-audit-v2.json` 明确 `release-review-001` 和 `incident-review-001` 是 ASR artifact/reference 覆盖不一致：coverage 约 `0.698925 / 0.634146`，缺失尾部为 `staging...` 与 `timeout/监控阈值...`。本轮没有裁剪 reference，而是在本地用 macOS `say -v Tingting -r 130` 重新生成 release/incident 完整合成音频，重跑本地 FunASR chunk20 hotword，补充 bounded normalizer/quality aliases（`check outservice -> checkout-service`、`error r ate -> error_rate`，不硬补 `staging/timeout`）。repaired matrix `data/asr_eval/manifests/asr-mainline-quality-synthetic-repaired-local.json` 的最新证据 `artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-repaired-local-20260710-final-verify.json` 显示：`decision_status=candidate_for_next_real_audio_gate`、`sample_count=4`、`pipeline_closed_sample_provider_count=4`、`quality_pass_sample_provider_count=4`、`samples_with_quality_pass_count=4`、`suspected_reference_artifact_mismatch_sample_count=0`、`remote_asr_call_count=0`、`llm_call_count=0`、`raw_audio_uploaded=false`。当前可声明：repaired local synthetic 中文技术会议 ASR -> 实时建议候选主链路在 4 场景质量门禁中候选通过；当前不可声明：真实多人会议生产 ASR 已通过。下一步固定为真实/公开音频 wall-clock soak 与真实麦克风验收，不再继续扩开放式评测。

2026-07-10 DEC-290/DEC-291 result: ASR 主链路质量门禁已从单样本推进到显式 matrix 批量报告，入口为 `docs/asr-mainline-quality-batch-report-2026-07-10.md`。新增 `tools/asr_mainline_quality_batch_report.py` 和 `data/asr_eval/manifests/asr-mainline-quality-synthetic.json`，禁止按文件名把不匹配的 reference/annotation 与 ASR 产物硬配。证据 `artifacts/tmp/asr_reports/asr-mainline-quality-synthetic-batch-20260710-after-runtime-risk.json` 显示：`sample_count=4`、`sample_provider_count=8`、`pipeline_closed_sample_provider_count=8`、`quality_pass_sample_provider_count=3`、`samples_with_quality_pass_count=3`、`remote_asr_call_count=0`、`llm_call_count=0`、`decision_status=no_go_quality_not_production`。本轮修复了 `incident-review-001` live pipeline 未识别运行时事故信号的问题，`autoker/auder + 消费堆积/lag/告警` 只在上下文成立时保守归一为 `order-worker`，并让 `消费堆积/告警延迟/临时扩容/根因` 进入 Risk state；raw evidence 仍保留 ASR 原文。该结论已被 DEC-292/DEC-293 refine：原 No-Go 主因包含 release/incident 合成音频 artifact 覆盖不足，不能再作为最终本地 ASR 质量判断。

2026-07-09 DEC-269 result: PC Workbench browser-live-mic 主链路完成生产 LLM 验收，报告入口为 `docs/pc-workbench-production-acceptance-report-2026-07-09.md`。真实证据链为 visible Chrome Workbench -> real browser microphone/getUserMedia -> 本机 `say -v Tingting` 外放会议提示 -> `sherpa_onnx_realtime` 本地实时 ASR -> ASR semantic quality gate -> production `/live/asr/sessions/{id}/...` derivations -> 远端非 mock OpenAI-compatible LLM gateway -> 建议卡/方案卡/纪要 -> 同 session UI 可见 -> delete -> standard evidence bundle。raw artifact=`artifacts/tmp/browser_live_mic/real-speaker-mic-production-usage-20260709-171209`，latest strict bundle=`artifacts/tmp/acceptance/real-speaker-mic-production-usage-20260709-171209-refresh2`，release-level summary=`artifacts/tmp/release_acceptance/release-current-20260709-production-browser-llm-evidence-fixed/summary.json`。latest browser manifest=`verdict=go`、`input_audio_path_kind=browser_get_user_media`、`asr_provider=sherpa_onnx_realtime`、`asr_provider_mode=real`、`asr_fallback_used=false`、`asr_semantic_quality_status=passed`、`derivation_mode=production_enabled`、`llm_provider=real_gateway`、`gateway_base_url_kind=remote`、`llm_called=true`、`llm_call_count=5`、`llm_usage_total_tokens=24116`、`counts_as_production_llm_evidence=true`、`suggestion_card_count=3`、`approach_card_count=3`、`minutes_char_count=490`、`all_cards_have_evidence=true`、`delete_verified=true`、`browser_console_error_count=0`、`network_error_count=0`。release summary=`verdict=go`、`blockers=[]`、`llm_call_count=10`、`llm_usage_total_tokens=26199`，其中 browser lane 指向 refresh2。审查发现旧 release summary 仍引用 usage=0 的旧 browser bundle；已修复 mainline/release evidence gate：browser-live-mic 若声称 `production_enabled`，必须同时满足 remote gateway、`counts_as_production_llm_evidence=true`、`llm_call_count>0`、`llm_usage_total_tokens>0`，否则 No-Go；no-cost 自测仍可 Go 但不能冒充生产 LLM。边界：这代表 Mac 本地 PC Workbench 主业务链路生产 LLM 验收 Go，不代表公开发布包完成；仍未完成 Developer ID signing、notarization、Gatekeeper acceptance、Windows 真机验收、真实长会议成本/性能 soak、人工真实会议产品手感验收。

2026-07-09 DEC-268 result: PC Workbench 主功能链路完成 no-cost selftest 闭环，报告入口为 `docs/pc-workbench-full-chain-selftest-report-2026-07-09.md`。本轮根因确认：真实麦克风此前后续建议/方案/纪要为 0，是因为生产派生端点正确拒绝 `LLM_GATEWAY_IS_MOCK=true`，不是 ASR 或页面渲染缺失。实现上新增显式 `?noCostDerivationSelfTest=1`，仅该模式把 Workbench 派生切到 `/live/asr/demo/sessions/{id}/...` + `mode=deterministic_demo`，默认产品路径仍是 production `mode=enabled`；E2E evidence 新增 `derivation_mode`、`derivations_generated`、`counts_as_production_llm_evidence` 等字段，避免 no-cost 被误报为真实远端 LLM。新鲜证据：`node code/web_mvp/e2e/workbench_all_buttons_smoke.mjs` Go，覆盖导入录音、建议、方案、纪要、历史、evidence clickback、导出、删除；真实浏览器麦克风 + 本机外放 Go，artifact=`artifacts/tmp/browser_live_mic/real-speaker-mic-nocost-20260709-164605`，`input_mode=real_browser_mic`、`health_status=audio_capture_health_passed`、`asr_final_count=1`、`suggestion_card_count=3`、`approach_card_count=1`、`minutes_char_count=247`、`delete_verified=true`、`counts_as_production_llm_evidence=false`。in-app browser 已打开并验证 `http://127.0.0.1:8765/workbench` 页面可用，8765 launcher running/health ok。回归：`92 passed, 2 warnings`，Node syntax/py_compile/git diff check passed，`workbench_smoke` 和 `workbench_all_buttons_smoke` 均 exit 0。仍未完成：生产真实远端 LLM 质量验收、Chrome fake-audio-file mic diagnostic（本轮为 zero samples No-Go）、公开发布签名/公证/Gatekeeper、Windows 真机。

2026-07-09 DEC-267 result: Workbench page function completion audit 已落地，入口为 `docs/workbench-function-completion-audit-2026-07-09.md`。结论：不能宣称“页面所有功能已完成并自测闭环”，也不能用外部发布/签名/公证预检替代页面功能完成证据。本轮新鲜验证：`node code/web_mvp/e2e/workbench_smoke.mjs` 通过，覆盖 demo opt-in、文字、建议候选、历史、正式建议、方案分析、纪要、刷新文字、自动建议暂停/恢复、整理会议、导出链接 API base、删除复位；focused export regression 先红后绿，修复 `downloadSessionArtifact()` 在 Tauri API base 场景下未走 `apiUrl(url)` 的问题；相关 Workbench/API 回归 `84 passed, 2 warnings`。仍未闭环：导入录音真实 file picker 点击、真实下载保存、evidence clickback 点击、file/simulated lane 的全按钮级 UI 闭环、用户真实麦克风最终验收、Tauri packaged 用户点击流、Windows 真机、Mac Developer ID/notarization/Gatekeeper。当前内置浏览器 `127.0.0.1:8765` 无监听而 8000 有后端健康响应，说明运行入口/端口管理也未产品化闭环。

2026-07-09 DEC-266 result: Windows real-machine verification 已从裸布尔 blocker 收敛为只读 intake/validator。新增 `tools/windows_real_machine_verification.py` 和 `tests/test_windows_real_machine_verification.py`；validator 只读取 `artifacts/tmp/**` 下 caller-provided observation JSON，不在 Mac 上执行 Windows 命令、不访问 WASAPI、不读取 secret 或用户原始音频。当前无 Windows 输入文件时生成 `artifacts/tmp/windows_real_machine_verification_20260709/evidence.json`，状态 `blocked_windows_real_machine_verification`，`windows_real_machine_verified=false`，缺口包括 Windows 真机、Windows host OS、Tauri WebView、backend health、Workbench、Provider health、麦克风权限路径、实时 ASR 到建议/纪要/历史/删除主链路、导入导出、installer/portable launch、delete 和 secret redaction。External preflight 现在只接受 `status=go_windows_real_machine_verified` 且 `windows_real_machine_verified=true` 的 validator evidence；手写裸 `windows_real_machine_verified=true` 不再能清除 blocker。

2026-07-09 DEC-265 result: release external preflight 已接入 Mac public release runner evidence，修正“notary profile 存在不等于 notarization 完成”的语义。`tools/macos_release_external_preflight.py` 新增 `--macos-release-evidence`，默认读取 `artifacts/tmp/macos_public_release_20260709/evidence.json`；只有该 runner 返回 `status=go_public_release_signed_notarized_gatekeeper` 且 `counts_as_public_release_package=true` 时，才清除 Mac 侧 Developer ID/notarization/Gatekeeper blocker。当前 `artifacts/tmp/release_external_preflight_20260709/evidence.json` 显示 `macos_public_release.ready=false`、`notarization.tooling_ready=true`、`notarization.notarization_completed=false`、`remaining_blockers=[developer_id_signing_not_done, notarization_not_done, gatekeeper_acceptance_not_done, windows_real_machine_not_verified]`。这避免把 notarytool/profile 可用误判为已公证。

2026-07-09 DEC-264 result: Mac public release signing/notarization runner 已落地到可执行边界。新增 `tools/macos_public_release_runner.py` 和 `tests/test_macos_public_release_runner.py`，TDD 验证无 Developer ID 时不会执行任何 mutating 命令；有 Developer ID identity 和 notary profile 时命令链为 `ditto -> codesign app -> verify app -> create DMG -> codesign DMG -> notarytool submit --wait -> stapler staple -> spctl app -> spctl DMG`。真实运行写入 `artifacts/tmp/macos_public_release_20260709/evidence.json`，结果 `status=blocked_public_release_execution_requirements`，`remaining_blockers=[developer_id_signing_not_done]`，`notarytool_available=true`，`notary_profile_provided=true`，`executed_mutating_command_count=0`，`notarization_submitted=false`，`remote_service_called=false`。这说明 Mac public release 路线已从“人工步骤”收敛为可执行 runner，但当前机器缺 Developer ID Application 身份，仍不能完成签名/公证/Gatekeeper。

2026-07-09 DEC-263 result: packaged screenshot evidence 已从 public release external blocker 重分类为 visual QA evidence。原因是 DEC-261 的 packaged DOM/runtime/same-chain probe 已证明 Workbench 在 packaged WebView 内可运行完整 no-cost 链路，比 macOS 屏幕捕获截图更适合作为自动化 release preflight 证据。`artifacts/tmp/release_external_preflight_20260709/evidence.json` 现在记录 `visual_evidence.packaged_screenshot_requirement_status=waived_by_internal_dom_runtime_probe`，截图缺失不再混入 public release blocker；如果后续需要视觉验收，可通过人工截图或授权屏幕录制另补，不阻断 Developer ID/notarization/Gatekeeper/Windows 外部发布判断。

2026-07-09 DEC-262 result: P2 Mac/Windows public release external preflight 已落地为机器可读证据。新增 `tools/macos_release_external_preflight.py` 和 `tests/test_macos_release_external_preflight.py`，真实运行写入 `artifacts/tmp/release_external_preflight_20260709/evidence.json`。结果为 `status=blocked_external_release_requirements`，`counts_as_packaged_same_chain_no_cost_evidence=true`，`counts_as_public_release_package=false`；当前机器 `Developer ID Application` 和 `Developer ID Installer` 身份均为 0，`notarytool_available=true` 但未提供 notary profile，`spctl_app_exit_code=3`、`spctl_dmg_exit_code=3`，Windows real-machine evidence 缺失。剩余 blocker 固定为 `developer_id_signing_not_done`、`notarization_not_done`、`gatekeeper_acceptance_not_done`、`windows_real_machine_not_verified`。该预检不读 secret、不读 keychain password、不提交 notarization、不调用远程服务；它证明当前无法宣称公开发布完成，但不回退 DEC-261 的 packaged same-chain 主链路证据。

2026-07-09 DEC-261 result: P2 Mac packaged `.app` 已完成 no-cost same-chain self-probe。新增后端 demo-only `deterministic_demo` derivation mode，仅允许 `/live/asr/demo/sessions/{id}/...` 使用，生成建议卡、方案卡和纪要时 `llm_call_status=not_called`、`cost_status=no_cost`，不读取 `LLM_GATEWAY_*`、不调用远端 provider、不计入生产真实 LLM 证据。Tauri `runtime_get_status` 新增 `packaged_same_chain_probe_enabled`，仅当 `MEETING_COPILOT_PACKAGED_SAME_CHAIN_PROBE=1` 时 Workbench 在 packaged WebView 内执行 self-probe。新证据：`artifacts/tmp/desktop_tauri_current_run/packaged-same-chain-no-cost-20260709/evidence.json`，状态 `go_packaged_webview_runtime_probe`，`blockers=[]`，`counts_as_packaged_same_chain_no_cost_evidence=true`，`counts_as_packaged_mainline_evidence=true`，`packaged_same_chain_flow_complete=true`，`suggestion_card_count=3`，`approach_card_count=1`，`same_chain_blockers=[]`；`remaining_blockers` 已收窄为 Developer ID signing、notarization、Gatekeeper acceptance、Windows real-machine verification。边界：该证据不是生产真实 LLM、不是生产真实麦克风、不是公开分发签名公证包，也不解决 macOS 截图权限 blocker。

2026-07-09 DEC-260 result: P2 Mac packaged `.app` 已从“仅进程/窗口可见”推进到 packaged WebView DOM/runtime/backend API probe Go。当前入口文档为 `docs/p0-p2-full-completion-execution-plan-2026-07-09.md`、`docs/p0-p2-mainline-closure-checklist-2026-07-09.md`、`docs/p0-p2-progress-update-2026-07-09-real-mic-and-mac-packaging.md`。新增/更新证据：`artifacts/tmp/desktop_tauri_current_run/packaged-webview-runtime-probe-20260709-backend-api/evidence.json`，状态 `go_packaged_webview_runtime_probe`，`counts_as_packaged_dom_evidence=true`、`counts_as_packaged_backend_api_evidence=true`、`latest-backend-api.health_ok=true`、`sessions_loaded=true`；刷新开发 DMG `artifacts/tmp/desktop_dmg_skip_finder_current_20260709/Meeting Copilot_0.1.0_aarch64.skip-finder.signed-local.dmg`，sha256=`f5e047cdf7f1cdd13d9777bccc0ac39e648b549c40b1eede539bf74929dac435`，仍为 `go_development_dmg_not_public_release`。验证：focused no-cost regression `187 passed, 2 warnings`，`cargo test desktop_frontend_probe_runtime=3 passed`，`cargo check` passed，`cargo tauri build --bundles app` passed，local ad-hoc `codesign --verify --deep --strict` passed。该 DEC-260 时点 P2 仍未完整完成：`packaged_same_chain_realtime_meeting_flow_not_verified`、Developer ID signing、notarization、Gatekeeper acceptance、Windows real-machine verification 仍是 blocker；Rust native PCM mic capture 继续作为 P2+/P3，不阻塞 Mac-first v1 WebView route。

2026-07-08 DEC-259 result: `docs/meeting-copilot-completion-target-and-selftest-plan-2026-07-08.md` 中 P0/P1/P2/P3 工作项已按本文档边界完成并自测。新增最终报告 `docs/completion-target-implementation-and-selftest-report-2026-07-08.md`；目标文档顶部状态更新为 `implemented to documented boundaries`；final no-cost release summary 写入 `artifacts/tmp/release_acceptance/final-completion-target-20260708-nocost-summary/summary.json`，verdict=go、blockers=[]，复用既有 P0 lane manifests 以避免重复付费 LLM/ASR lane。新鲜验证：P1/P2 tool regression `40 passed, 2 warnings`；core backend/workbench `176 passed, 2 warnings`；ASR bakeoff `19 passed, 1 warning`；P1 export/provider/delete focused `65 passed, 2 warnings`；Python/Node syntax passed；Workbench smoke passed；`git diff --check` clean；`/health` ok；Workbench JS version present。当前状态仍保持 `Production MVP: Conditional No-Go`，因为真实 native 桌面麦克风/ASR worker/audio chunk lifecycle/签名/notarization/安装包 smoke 不在本目标完成范围，必须作为下一独立生产里程碑。

2026-07-08 DEC-258 result: P3-1 移动端远期规划已完成。新增 `docs/mobile-app-future-plan.md`，将 iOS/Android 定位为后续 companion app，而不是当前 PC/Mac 实时会议主链路；记录 Apple Developer Program、App Privacy、App Review、Google Play Console、个人开发者测试要求、User Data/Data safety 和中国 Android 市场差异。移动端不是当前 P0/P1/P2 blocker，未创建 iOS/Android 工程、未注册账号、未提交市场、未实现移动端录音。当前下一步指针改为最终完整回归和 release acceptance。

2026-07-08 DEC-257 result: P2-2 Windows 兼容计划已完成。新增 `docs/desktop-windows-compatibility-plan.md`，明确 Mac-first / Windows-second，业务 UI/backend/core 共享，平台差异封装在 desktop adapter；记录 Windows 麦克风权限、WASAPI loopback、安装包、签名、SmartScreen、防火墙/杀软和 smoke checklist。该状态不代表 Windows 实现完成；未跑 Windows Tauri WebView、未实现 WASAPI、未打包 installer、未处理签名。

2026-07-08 DEC-256 result: P2-1 Mac 桌面当前完成边界定义为 `Mac dev shell + no-op IPC evidence`。新增 `docs/desktop-mac-mvp-plan.md`；`code/desktop_tauri` 已具备 Tauri v2 scaffold、Workbench WebView 配置、macOS 麦克风/系统音频权限文案和 10 个 IPC command；历史 PCWEB-118 `cargo check` 与 PCWEB-119 real Tauri no-op WebView IPC evidence 可追溯。本轮清理源码树误生成的 `code/desktop_tauri/src-tauri/target`，修复 PCWEB-120 policy/import 漂移并补齐 PCWEB-112/113/114/120 policy JSON。验证：`tests/test_desktop_tauri_scaffold.py + tests/test_desktop_worker_mic_source_from_tauri_evidence.py = 13 passed, 1 warning`；`py_compile tools/desktop_worker_mic_source_from_tauri_evidence.py` 通过；PCWEB-120 CLI 从历史 Tauri no-op evidence 生成 `ready_for_manual_review_not_executable / worker_mic_source_approval_status=not_approved`，所有真实音频/worker/remote flags false。边界：不能宣称真实麦克风采集、系统音频、ASR worker spawn、audio chunk lifecycle、签名、notarization 或 `.app/.dmg` 交付完成。

2026-07-08 DEC-255 result: P1-4 长会议 synthetic soak gate 已完成。新增 `tools/long_meeting_soak_runner.py` 和 `tests/test_long_meeting_soak_runner.py`，默认构造 20 分钟 deterministic simulated realtime meeting plan：`chunk_seconds=2 / chunk_count=600 / expected_audio_seconds=1200`，通过 metrics 注入记录 `asr_rtf`、`llm_call_count`、`llm_usage_total_tokens`、`memory_rss`、`card_count`、`suppression_count` 和 privacy/cost flags；超过 `max_cards_per_10_minutes` 时输出 `suggestion_frequency_cap_exceeded` No-Go，缺失非法 metrics 输出 blocked，report 写入 `artifacts/tmp/soak/<run-id>/soak_report.json` 并做 secret redaction。验证：`tests/test_long_meeting_soak_runner.py = 5 passed, 1 warning`；`py_compile` 通过；`p1-4-long-meeting-soak-20260708` verdict=go；`p1-4-frequency-cap-20260708` verdict=no_go / suppression_count=12 / blocker=`suggestion_frequency_cap_exceeded`。边界：这是 deterministic soak decision gate，不等于真实 backend 进程连续运行 20-60 分钟的生产压测。当前下一步指针改为 `P2-1 Mac 桌面客户端计划/启动 smoke`，随后是 `P2-2 Windows 兼容计划` 和 `P3-1 移动端远期规划`。`Production MVP` 仍为 `Conditional No-Go`，因为 P2/P3 和最终 release verification 尚未完成。

2026-07-08 DEC-254 result: P1-3 数据保留和隐私策略已完成到 focused verification。新增 `docs/privacy-retention-and-delete-policy.md`，明确 `MEETING_COPILOT_DATA_DIR/live_asr_sessions/<session_id>.json` 保存 session/transcript/cards/approach/minutes/provider metadata，ignored `artifacts/tmp/**` 保存 evidence 和自测产物；实时浏览器麦克风不持久化原始 audio chunks，导入录音删除 session 不宣称删除用户电脑另存原始文件。`DELETE /live/asr/sessions/{session_id}` 返回结构化 `delete_scope`：session/transcript/suggestion/approach/minutes 随 session 删除，`audio/exports/evidence_bundle=not_tracked_by_live_session_repo`。Workbench 删除确认展示来源、文字数、AI 建议、方案分析、纪要状态和删除范围，并提示不会删除另存原始音频。Evidence sanitizer 已覆盖 mainline 和 release JSON writer。验证：`tests/test_mainline_evidence_bundle_runner.py + tests/test_release_acceptance_runner.py + delete_scope focused test = 23 passed, 2 warnings`；release secret writer focused test `1 passed, 2 warnings`。

2026-07-08 DEC-253 result: P1-2 Provider config 和成本策略已完成到 focused verification。新增 `GET /providers/health`，只暴露非密钥 LLM/ASR readiness；Workbench 启动读取 `/providers/health`，只显示 provider/model/configured 和本地 ASR 状态；`docs/provider-config-and-cost-policy.md` 记录默认成本为 `LLM gateway only when AI analysis is enabled`，远程 ASR 默认关闭 `remote_asr_default_enabled=false / raw_audio_uploaded_by_default=false`。Mainline evidence 和 release acceptance summary 汇总 `llm_call_count` 与 `llm_usage_total_tokens`，release 会正确聚合 lane 顶层 `llm_called=true`。验证：provider health/workbench/evidence/release focused tests `4 passed, 2 warnings`；`node --check workbench.js` 通过。

2026-07-08 DEC-252 result: P1-1 录音导入和导出产品化已完成到 focused verification。Workbench 支持 `.wav/.m4a/.mp3` 文件选择并通过 `/live/asr/transcribe-file/sessions` 生成 ASR live session，导入成功后使用完整 session snapshot 渲染，不在上传成功前清空上一场会议。新增 `GET /live/asr/sessions/{session_id}/transcript.txt`，按 `[mm:ss] text` 导出 final transcript；`GET /live/asr/sessions/{session_id}/minutes.md` 增加 attachment header；Workbench 新增 `导出文字稿` 和 `导出纪要`。删除确认展示音频/派生产物范围。验证：`test_file_convert.py + test_workbench.py = 63 passed, 2 warnings`；`node --check workbench.js` 通过。

2026-07-08 DEC-251 result: C10/P0-5 Release acceptance runner 已完成到 P0 release gate。新增 `tools/release_acceptance_runner.py`、`tests/test_release_acceptance_runner.py` 和 `docs/release-acceptance-checklist.md`；runner 复用 `mainline_evidence_bundle_runner.py`，汇总 backend mainline pytest、Workbench smoke、`git diff --check`、`/health`、Workbench JS 版本、file lane、simulated realtime、real mic recorded realtime 和 browser live mic Go bundle。release 层明确要求 browser live mic `browser_live_mic_go_evidence=true`，否则输出 `blocked_browser_live_mic_not_proven`，不能用 `real_mic_recorded_wav` 替代。真实 CLI 运行 `p0-5-release-acceptance-20260708` 已完成，后续修正了汇总层 `privacy_cost_flags.llm_called` 聚合并生成 `artifacts/tmp/release_acceptance/p0-5-release-acceptance-20260708-corrected-summary/summary.json`：`verdict=go / blockers=[] / file_lane=go / simulated_realtime=go / real_mic_recorded_realtime=go / browser_live_mic=go / llm_called=true / remote_asr_called=false / configs_local_read=false / user_audio_committed_to_repo=false`。验证：`tests/test_release_acceptance_runner.py + tests/test_mainline_evidence_bundle_runner.py + P0-3 workbench/asr/file tests = 95 passed, 2 warnings`；`code/asr_bakeoff/tests = 19 passed, 1 warning`；`py_compile` 通过。当前下一步指针改为 `P1-1 录音导入和导出产品化`，随后是 `P1-2 Provider config`、`P1-3 privacy/retention/delete`、`P1-4 long meeting soak`、`P2/P3 desktop/mobile planning`。`Production MVP` 仍为 `Conditional No-Go`，因为 P1/P2/P3 尚未完成。

2026-07-08 DEC-250 result: C3/P0-3 ASR semantic quality gate 已完成到 focused verification。新增 deterministic `asr_semantic_quality` gate 和 10 条中文技术会议短句评测集，覆盖接口/API、灰度、回滚、错误率、owner、deadline、SLO/P99、监控告警等关键实体；无意义转写或流畅但非技术内容会输出 `asr_semantic_quality_blocked`，并进入 session `event_source.asr_semantic_quality`、`degradation_reasons` 和 acceptance blockers。realtime/file lane、Workbench 用户提示、自动建议/正式派生阻断和 `tools/mainline_evidence_bundle_runner.py` manifest 均已接入，manifest 新增 `asr_semantic_quality_status`、`asr_semantic_quality_blocked` 和 `asr_semantic_quality`。新增 `code/asr_bakeoff/asr_bakeoff/semantic_quality_report.py`，正式报告 `artifacts/tmp/asr_eval/semantic_quality/p0-3-semantic-quality-report-20260708.json` 显示 `sample_count=10 / expected_status_match_count=10 / false_pass_count=0 / false_block_count=0 / keyword_recall_average=1.0 / remote_asr_default_enabled=false / cost_status=no_paid_remote_service`。验证：`test_asr_semantic_quality.py + test_asr_stream.py + test_file_convert.py + test_workbench.py + tests/test_mainline_evidence_bundle_runner.py = 89 passed, 2 warnings`；`code/asr_bakeoff/tests = 19 passed, 1 warning`。当前下一步指针改为 `P0-5 Release acceptance runner`，随后进入 P1/P2/P3。`Production MVP` 仍为 `Conditional No-Go`，因为 C10 release acceptance 和 P1/P2/P3 尚未完成。

2026-07-08 DEC-249 result: C2/P0-2 实时自动建议 orchestrator 已完成到 focused verification，并经过只读复审 hardening。新增 `auto_suggestion_orchestrator.py` 和生产 API `GET/PATCH/POST /live/asr/sessions/{session_id}/auto-suggestions/...`；自动建议只处理 `llm_candidate_queued` candidate，并在正式 LLM 调用前检查 acceptance blockers、paused、duplicate、confidence/degradation、cooldown 和 max-calls-per-hour。Workbench 新增自动建议状态卡与暂停/恢复按钮，`applySessionEvents(...)` 的 session snapshot 和 `appendLiveEvent(...)` 的 live final 会调用 `runAutoSuggestionsOnce(...)`，不是通过 `btn-cards.click()` 包装旧手动按钮。复审发现旧实现只在 `END` 后 snapshot 可用，已修为 WebSocket chunk final 阶段增量 upsert ASR live session，使 `END` 前正式卡生成和暂停/恢复可用；`max_calls_per_hour` 改为 1 小时滑动窗口；正式建议卡展示触发原因和置信度。验证：`test_auto_suggestions.py = 9 passed, 2 warnings`；`test_auto_suggestions.py + test_workbench.py + test_real_asr_to_cards.py + test_asr_stream.py = 75 passed, 2 warnings`；`node --check workbench.js` 和 `node --check workbench_smoke.mjs` 通过。当前下一步指针改为 `P0-3 ASR semantic quality gate`，随后是 `P0-5 Release acceptance runner`。`Production MVP` 仍为 `Conditional No-Go`，因为 C3/C10/P1/P2/P3 尚未完成。

2026-07-08 DEC-248 result: C1/P0-1 Browser live mic evidence lane 已完成到 Go。先新增 browser live mic runner 契约：health pass 但无 ASR final 必须 No-Go；完整 same-session browser evidence 才 Go；短转写低于 30 字不能 Go；`workbench_browser_live_mic_verify.mjs` 在页面等待失败时也必须写出 `browser_mic_health_report.json`、`asr_probe.json`、`ui_verification.json`、`session_events.json`、截图和失败页状态，避免“跑了但没证据”。真实自测使用 macOS `say -v Tingting` 播放可复现中文技术会议短句，通过浏览器 `getUserMedia` 进入 Workbench：证据目录 `artifacts/tmp/browser_live_mic/p0-browser-live-mic-tech-audio-20260708-231952/`，bundle 目录 `artifacts/tmp/acceptance/p0-browser-live-mic-tech-audio-20260708-231952/`。正式 bundle manifest 为 `verdict=go / audio_source=browser_live_mic / browser_live_mic_go_evidence=true / counts_as_real_mic_go_evidence=true / asr_provider=sherpa_onnx_realtime / asr_provider_mode=real / asr_fallback_used=false / llm_provider=real_gateway / transcript_char_count=58 / final_segment_count=1 / suggestion_card_count=3 / approach_card_count=2 / minutes_char_count=273 / workbench_same_session_visible=true / frontend_card_count=5 / delete_verified=true / degradation_reasons=[]`。验证命令：focused P0-1 tests 通过，`node --check code/web_mvp/e2e/workbench_browser_live_mic_verify.mjs` 通过，`tools/mainline_evidence_bundle_runner.py --lane browser-live-mic ...` exit 0。当前下一步指针改为 `P0-2 实时自动建议 orchestrator`，随后是 `P0-3 ASR semantic quality gate`、`P0-5 Release acceptance runner`。`Production MVP` 仍不是最终 Go，因为 C2/C3/C10/P1/P2/P3 仍未完成。

2026-07-08 DEC-247 result: C4/P0-4 Workbench 产品化 UI 已完成到可验证状态。首屏主操作现在固定为 `开始会议 / 结束会议 / 导入录音 / 历史记录`；`整理会议 / 刷新文字 / 删除本次会议 / 生成会议建议 / 分析方案利弊 / 生成会议纪要` 只在有 session 后出现；页面分区收敛为 `实时文字 / 实时建议 / AI 建议 / 方案分析 / 会议纪要 / 历史记录`。验证：`test_workbench.py = 47 passed, 2 warnings`；`test_workbench.py + test_app.py -k 'workbench or delete_asr_live_session or privacy' = 48 passed, 87 deselected, 2 warnings`；`node code/web_mvp/e2e/workbench_smoke.mjs` 通过并生成 desktop/mobile 截图到 `artifacts/tmp/ui_screenshots/workbench-p0-4-smoke/`；`git diff --check` clean。当前下一步指针改为 `P0-1 Browser live mic evidence lane`，随后是 `P0-2 实时自动建议 orchestrator`、`P0-3 ASR semantic quality gate`、`P0-5 Release acceptance runner`。`Production MVP` 仍为 `Conditional No-Go`。

2026-07-08 completion target: 下一阶段剩余工作总清单已收束到 `docs/meeting-copilot-completion-target-and-selftest-plan-2026-07-08.md`。该文档明确 10 个生产级完成条件、P0/P1/P2/P3 工作拆分、固定自测命令、release candidate gates 和 stop rules。当前下一步推荐目标为 `Workbench 产品化 UI 重构和全按钮自测`，然后再跑 `Browser live mic evidence lane`。在 browser live mic 未通过前，项目状态仍为 `Production MVP: Conditional No-Go`。

2026-07-08 DEC-245 result: real mic recorded realtime 主链路自测通过。新增 `tools/mainline_evidence_bundle_runner.py --lane real-mic-recorded-realtime --health-report ...`，用于把已授权真实麦克风 WAV 按实时 chunk 送入 `/live/asr/stream/ws/{session_id}?audio_source=real_mic_recorded_wav`，再跑同一套建议卡、方案卡、纪要、Workbench 同 session、历史/删除和 evidence bundle。最终通过命令为 `python3 tools/mainline_evidence_bundle_runner.py --lane real-mic-recorded-realtime --audio artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.wav --health-report artifacts/tmp/audio_health/p0-real-mic-afplay-20260708-214203.health.json --run-id p0-real-mic-recorded-realtime-afplay-20260708-01`。证据目录为 `artifacts/tmp/acceptance/p0-real-mic-recorded-realtime-afplay-20260708-01/`，manifest 为 `verdict=go / audio_source=real_mic_recorded_wav / counts_as_real_mic_go_evidence=true / browser_live_mic_go_evidence=false / asr_provider=sherpa_onnx_realtime / asr_fallback_used=false / llm_provider=real_gateway / transcript_char_count=86 / suggestion_card_count=3 / approach_card_count=3 / minutes_char_count=404 / workbench_same_session_visible=true / delete_verified=true / degradation_reasons=[]`。报告入口：`docs/p0-real-mic-recorded-realtime-selftest-report-2026-07-08.md`。当前状态更新为 `Demo/mock: Go for demo only / File lane: Go / Simulated realtime wav: Go / Real mic recorded realtime: Go / Browser live mic: Not yet proven / Production MVP: Conditional No-Go`。

2026-07-08 DEC-243 pivot: 用户当前不方便配合真实麦克风收音，因此真实麦克风 Gate A/B/C 暂停自动推进。新增当前执行入口 `docs/p0-no-mic-simulated-realtime-mainline-plan-2026-07-08.md`：使用仓库内合成/公开授权 WAV 作为 `simulated_realtime_wav`，按实时 chunk 发送到 `/live/asr/stream/ws/{session_id}`，再跑同一套 ASR live session、建议卡、方案卡、纪要、Workbench 同 session、历史/删除和 evidence bundle。该 lane 只能证明 no-mic 实时协议和产品业务链路，必须标注 `counts_as_real_mic_go_evidence=false`，不能写成真实麦克风 Go。当前状态更新为 `Demo/mock: Go for demo only / File lane: Go / Simulated realtime wav: In progress / Real mic: No-Go deferred / Production MVP: No-Go`。

2026-07-08 DEC-244 result: no-mic simulated realtime 主链路自测通过。命令为 `python3 tools/mainline_evidence_bundle_runner.py --lane simulated-realtime --audio code/asr_runtime/outputs/simulated-release-review.16k.wav --run-id p0-simulated-realtime-20260708-01`。证据目录为 `artifacts/tmp/acceptance/p0-simulated-realtime-20260708-01/`，manifest 为 `verdict=go / audio_source=simulated_realtime_wav / counts_as_real_mic_go_evidence=false / asr_provider=sherpa_onnx_realtime / asr_fallback_used=false / llm_provider=real_gateway / suggestion_card_count=3 / approach_card_count=3 / minutes_char_count=407 / workbench_same_session_visible=true / delete_verified=true / degradation_reasons=[]`。报告入口：`docs/p0-no-mic-simulated-realtime-selftest-report-2026-07-08.md`。当前状态更新为 `Demo/mock: Go for demo only / File lane: Go / Simulated realtime wav: Go / Real mic: No-Go deferred / Production MVP: No-Go`。

2026-07-08 real mic Gate A rerun: 本轮在当前机器用 `tools/audio_capture_healthcheck.py --record-seconds 20 --audio-device-index 0` 采集 `MacBook Air麦克风`，结果写入 `artifacts/tmp/audio_health/gate-a-real-mic-20260708-160858.health.json`，并生成标准 bundle `artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-160858/manifest.json`。结论为 `verdict=no_go / health_status=blocked_audio_too_quiet / rms=0.0 / peak=0.0 / active_sample_ratio=0.0 / llm_called=false / asr_provider=not_started`。Stop rule 生效：不跑 Gate B/C，不调用 ASR/LLM。当前状态仍为 `Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-241 implementation: Workbench 开麦不立即清空上一场文字已完成并记录到 `docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md` 第 12 节。新增 `recordingDraftHasClaimedView/startRecordingDraftSession/claimRecordingDraftView`，有上一场可读会议时，开始真实麦克风只进入“录音草稿”并保留上一场主视图；只有收到第一条非空 partial/final/transcript_final 才切到新会议文字。provider_error、空 final、空 snapshot 继续恢复上一场会议。验证 `test_workbench.py = 41 passed, 2 warnings`。该修复改善用户看到“文字没了”的前端路径，但不代表 real mic Gate B 已 Go；真实麦克风仍需 Gate A/B/C 证据。

2026-07-08 DEC-240 implementation: 后端 `recognizer metadata fail-closed` 已完成并记录到 `docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md` 第 11 节。新增 TDD 测试 `test_asr_stream_recognizer_without_metadata_fails_closed_by_default`，红灯表现为服务端未返回 provider_error 而进入正常识别路径；实现后 recognizer 若缺 `provider/provider_mode/is_mock/fallback_used` 任一 metadata，会输出 `provider_mode=unknown/is_mock=true/fallback_used=true/degradation_reasons=recognizer_metadata_missing`，默认生产 WebSocket 返回 provider_error 且不持久化 session。相关回归 `test_asr_stream.py + test_real_asr_to_cards.py = 8 passed, 2 warnings`。下一步转向 Workbench 开麦不丢字和真实麦克风 Gate A。状态仍为 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-239 audit: 本轮按用户要求重新对 Workbench 前端和后端/API 做多 Agent 溯源审计，结论已追加到 `docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md` 第 9-10 节。运行态发现旧 8765 进程曾脱离 screen，已 kill 并用 `screen=38984.meeting-copilot-8765 / pid=38991` 重新拉起；`/health` ok，`/audio/check` 显示 mic/file/realtime ASR/LLM configured，`/workbench` 返回 `workbench.js?v=20260708-p0-boundary`。`node code/web_mvp/e2e/workbench_smoke.mjs` 通过，但只算 demo/mock UI 回归。前端根因收敛为：开麦会立即创建 `rec_*` 并清空当前主视图，若真实 ASR 没有非空 final，用户就会看到“文字没了”；后端新增风险收敛为：`recognizer` 缺少显式 metadata 时可能默认被当作 real ASR。下一轮执行顺序固定为：先 TDD 修 `recognizer metadata fail-closed`，再 TDD 修 Workbench 开麦不丢字，然后再跑真实麦克风 Gate A/B/C。当前状态仍为 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-238 implementation: 本轮三路多 Agent 全站审计已落到 `docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md`，结论收敛为：项目不是空壳，但真实麦克风完整产品链路仍 No-Go；file lane 是唯一可信 Go 子链路；公开音频 lane 未执行到 Go；Workbench 和后端仍存在 demo/file/real mic/degraded 混淆风险。后端新增生产 LLM provider 边界：`LLM_GATEWAY_IS_MOCK=true` 的 provider 不能在生产 `/live/asr/sessions/{id}/llm-execution-runs|approach-cards|minutes|minutes.json` 生成正式派生产物，统一返回 409；demo 路由 `/live/asr/demo/sessions/*` 继续允许 mock LLM 用于 UI 回归。Focused TDD 先红后绿；相关回归 `4 passed, 2 warnings`。状态仍为 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-237 implementation: 正式建议卡补齐 EvidenceSpan quote/timestamp/回跳。后端 execution preview/run/card 现在包含 `evidence_spans` 和 `evidence_context`，LLM suggestion card prompt 使用原话证据而不只传 evidence id；Workbench 正式建议卡展示证据时间窗和原话 quote，点击 evidence 会滚动并高亮对应 transcript utterance。验证：focused app/workbench evidence tests 通过，相关核心回归 `169 passed, 2 warnings`，`node code/web_mvp/e2e/workbench_smoke.mjs` 通过，`git diff --check` 无输出。`formal_card_evidence` 改为结构性部分 Go，但 `Real mic` 仍 No-Go，`Production MVP` 仍 No-Go。

2026-07-08 DEC-236 implementation: 生产 LLM 派生端点已移除 demo/test 绕过口。`CreateLlmExecutionRunsRequest` 不再包含 `allow_non_acceptance_execution`；生产端点 `/live/asr/sessions/{id}/llm-execution-runs|approach-cards|minutes|minutes.json` 收到该字段会 422，并一律走正式验收 gate。新增 `/live/asr/demo/sessions/{id}/...` 专用 demo 派生端点，响应标记 `execution_boundary=demo_non_acceptance_execution`；Workbench demo session 已切到 demo 路径，真实/导入 session 仍走生产路径。Workbench JS cache-busting 版本更新为 `20260708-p0-boundary`。验证：focused `2 passed, 2 warnings`，相关核心回归 `161 passed, 2 warnings`，`node code/web_mvp/e2e/workbench_smoke.mjs` 通过，`git diff --check` 无输出。状态仍是 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-235 reset: 用户再次要求全站溯源审查后，结论收束为：项目不是空壳，但也不是生产级；Workbench、file lane、demo、LLM cards、approach、minutes、history、delete 都有局部实现，真实麦克风主链路仍 No-Go。当前继续保留 demo/mock 只用于 UI 回归和示例，不能作为真实验收；后续只执行 5 类 P0 工作：隔离生产端点的 `allow_non_acceptance_execution`、补正式建议 evidence quote/时间戳/回跳、补 delete/evidence/audio retention 逐项状态、继续压低 Workbench 主流程复杂度、按 Gate A/B/C 串行跑真实麦克风。唯一活计划的新增执行段为 `docs/current-status-and-p0-execution-plan-2026-07-08.md` 第 15 节。当前状态不变：`Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-234 implementation: Workbench 真实麦克风失败不再清空上一场已打开会议。新增 `preserveSessionBeforeRecording()` / `restorePreservedSessionAfterRecordingFailure()`，当用户已打开一场有文字会议后启动真实麦克风，但实时识别失败、provider_error 或最终 ASR 为空时，页面恢复上一场会议文字，并把失败原因留在当前状态区；失败 session 仍进入历史并标记降级。验证：focused `1 passed, 2 warnings`，Workbench 全量 `39 passed, 2 warnings`，核心回归 `107 passed, 2 warnings`，`node code/web_mvp/e2e/workbench_smoke.mjs` 通过。真实页面验证：示例 `workbench_mrboukhq` 有 4 条文字，启动麦克风后页面提示无麦克风声音，结束生成降级 `rec_mrboum34/final_count=0/acceptance_eligible=false`，页面保留 `workbench_mrboukhq` 的 4 条文字。Real Mic 仍 No-Go，原因仍是有效音频输入缺失。

2026-07-08 DEC-233 implementation: Workbench P0 产品语义修复已完成并验证。麦克风开始阶段现在使用 `pending_mic`/`待确认` 来源，不会在服务端非空 ASR final 前标记为真实可验收；footer 拆分 `候选提醒`、`正式建议`、`方案分析`，候选提醒不再计入正式建议；`整理会议` 改为独立 orchestration，统一顺序生成正式建议、方案分析和会后复盘，不再通过三个按钮 `.click()` 并发拼装。验证：focused `3 passed, 2 warnings`，Workbench 全量 `38 passed, 2 warnings`，核心回归 `145 passed, 2 warnings`，`node code/web_mvp/e2e/workbench_smoke.mjs` 通过。Real Mic Gate A 本轮真实采集仍 No-Go：`artifacts/tmp/audio_health/gate-a-real-mic-20260708-140009.health.json` 显示 `blocked_audio_too_quiet / rms=0.0 / peak=0.0 / active_sample_ratio=0.0`；evidence bundle `artifacts/tmp/acceptance/p0-real-mic-gate-a-20260708-140009/manifest.json` 为 `verdict=no_go / llm_called=false / final_segment_count=0`。按 Stop Rule，未继续 Gate B/C。

2026-07-08 DEC-232 reset: 本轮四路 Agent 再审查后，P0 主线只承认 7 个 release-decisive 状态：`backend_acceptance_enforcement`、`workbench_real_demo_separation`、`formal_card_evidence`、`delete_evidence`、`real_mic_gate_a`、`real_mic_gate_b`、`real_mic_gate_c`。任何任务若不能改变这些状态之一，只能记为 supporting work，不能写成 P0 主线完成。下一步先修 Workbench 三个 P0 产品语义缺口：麦克风开始阶段显示待确认而非已验收真实、候选/正式建议/方案计数拆分、`整理会议` 改为独立 orchestration。当前状态仍是 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。

2026-07-08 DEC-229 reset: 四路 Agent 再审查后，当前唯一活文档仍是 `docs/current-status-and-p0-execution-plan-2026-07-08.md`，但其执行段已更新为 P0 恢复 checklist：文档收束、后端非空 final/provenance/delete 语义、Workbench 主流程产品化、real mic Gate A/B/C、公开音频独立 lane。旧的 2026-07-03/07-04/07-07/早期 07-08 mainline、P0、workbench、readiness、preflight、wrapper 文档均只能作为 historical context 或 evidence archive，不再作为当前执行入口。当前状态仍是 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。下一步禁止继续把 demo smoke、file lane Go、synthetic/replay、readiness wrapper 包装成真实会议主链路完成。

2026-07-08 DEC-230 implementation: 后端 P0 验收边界补齐非空 ASR final gate 和删除范围精确化。ASR live `event_source` 现在会返回 `final_count`、`non_empty_final_count`、`non_empty_transcript`、`transcript_chars`；enabled LLM gate 会用 `asr_final_missing` / `asr_transcript_empty` 阻断空 ASR session，file lane 空文本也不能生成正式建议、方案或复盘。`DELETE /live/asr/sessions/{id}` 不再返回模糊 `cascade`，改为结构化 `delete_scope`，明确 audio/export/evidence 当前为 `not_tracked_by_live_session_repo`。验证：focused `2 passed, 2 warnings`；核心回归 `138 passed, 2 warnings`。

2026-07-08 DEC-231 implementation: Workbench P0 主流程收敛。顶栏现在只保留 `开始/结束会议`、`导入录音`、`整理会议`、`历史记录`、`删除本次会议`；`试用示例` 下沉到演示区；新增 `source-badge` 和独立 `候选提醒` 面板；`suggestion_candidate_event` 不再渲染到实时文字流；删除确认会展示 session 来源、文字数、正式建议数、方案数、复盘状态和删除范围。验证：focused Workbench `4 passed, 2 warnings`；核心回归 `142 passed, 2 warnings`；`node code/web_mvp/e2e/workbench_smoke.mjs` 通过。该 smoke 仍只算 demo，不算 real mic Go。

2026-07-08 DEC-225 reset: 当前 P0 执行入口更新为 `docs/current-status-and-p0-execution-plan-2026-07-08.md`。该文档吸收前端、后端、文档三路 Agent 审查和主线程运行态复核，明确 8765 旧进程曾缓存旧 Workbench HTML，重启后 `/workbench` 已返回 `#transcript-stream/#suggestions-panel/#approach-panel` 新结构。最新状态仍是 `Demo/mock: Go for demo only / File lane: Go / Real mic: No-Go / Production MVP: No-Go`。后续只允许按 Gate A/B/C 推进真实麦克风主链路，第一步是 source/provenance contract，防止 mock/demo/local-event/degraded 污染真实 Go/No-Go。

2026-07-08 DEC-226 implementation: source/provenance contract 第一层已落地。ASR live `event_source` 现在会返回 `input_source`、`acceptance_eligible` 和 `acceptance_blockers`；mock/demo、local-event-file、fake fallback、degraded empty final 默认 `acceptance_eligible=false`。验证：`test_app.py + test_asr_stream.py = 87 passed, 2 warnings`；Workbench/主线相关回归 `37 passed, 2 warnings`。下一步固定为真实会议模式 sidecar 不可用 hard fail，不允许静默 fake fallback 成功。

2026-07-08 DEC-227 implementation: Stage 1 production boundary hardening 已完成两项。`create_app()` 默认真实会议模式不允许 fake ASR fallback；sidecar 不可用时 WebSocket 返回 `provider_error/real_asr_sidecar_unavailable` 并且不持久化 session，只有显式 `allow_fake_asr_fallback=True` 的 demo/test app 可复用 fake recognizer。enabled LLM derivation 也增加门禁：mock/demo、local-event、fake fallback、degraded session 默认不能调用 `llm-execution-runs`、`approach-cards`、`minutes`、`minutes.json`，除非请求显式 `allow_non_acceptance_execution=true` 用于 demo/test。验证：`test_app.py + test_asr_stream.py = 90 passed, 2 warnings`；相关套件 `61 passed, 2 warnings`。下一步转向前端 P0：修复开始麦克风清空旧文字、空 snapshot 覆盖 live partial、主按钮受 realtime ASR readiness 和 source badge 约束。

2026-07-08 DEC-228 implementation: Workbench P0 止血完成第一批。后端 `provider_error` 现在在页面展示为“实时识别不可用”，不会继续显示录音成功；空 snapshot 不再覆盖最后一条 `live-partial`，会保留“临时实时文字”；`/audio/check` 的 `realtime_asr_available=false` 会禁用/改写 `开始会议` 主按钮；演示 session 触发建议/方案/复盘时显式传 `allow_non_acceptance_execution=true`，降级/非验收 session 不默认绕过后端门禁。验证：`test_workbench.py = 31 passed, 2 warnings`；核心回归 `136 passed, 2 warnings`；`node code/web_mvp/e2e/workbench_smoke.mjs` 通过。该 smoke 仍只算 demo，不算 real mic Go。下一步仍是 Workbench 产品化剩余项和 real mic Gate A/B/C。

2026-07-08 DEC-219/DEC-220 reset: 当前项目状态正式收敛为 `file lane Go / real mic No-Go / Production MVP No-Go`。此前 2026-07-04/07-05 的 synthetic、replay、Local Shadow Preview、readiness/preflight/approval wrapper 都只保留为历史上下文，不再作为下一步主入口。后续只允许推进直接改变 P0 主线状态的工作：Workbench 真实/演示隔离、真实麦克风有效采集、非 fake ASR final、同一 session 的 LLM 建议/方案/复盘、历史恢复、删除和 evidence bundle。

2026-07-08 DEC-223 reset: 当前执行入口更新为 `docs/p0-real-product-mainline-plan-2026-07-08.md`。该文档吸收本轮产品/前端/后端多 Agent 审查，修正旧计划中过期结论，并把下一步收敛为 Workbench 产品化止血、真实麦克风 Gate A/B/C、后端 live ASR schema 边界和 evidence bundle。旧 `docs/p0-mainline-recovery-execution-plan-2026-07-08.md` 与 `docs/p0-product-mainline-recovery-checklist-2026-07-08.md` 保留为历史执行记录，不再作为唯一入口。

当前可声明状态：

```text
Local Web Demo: 可用，但只算 demo。
文件导入 P0 子链路: Go。
真实麦克风实时 Copilot: No-Go。
Production MVP: No-Go。
```

最新可信 file lane evidence：

```text
artifacts/tmp/acceptance/p0-file-lane-20260708-after-p0fix/
verdict=go
audio_source=uploaded_wav
asr_provider=local_funasr_batch
asr_provider_mode=real
asr_fallback_used=false
llm_provider=real_gateway
ui_coverage=headless_chrome
delete_verified=true
degradation_reasons=[]
```

真实麦克风 No-Go evidence：

```text
artifacts/tmp/real_mic_shadow_tests/p0_real_mic_20260708/full_chain_summary.json
mean_volume=-91.0 dB
max_volume=-91.0 dB
asr.text=""
event_counts.final=0
candidate_count=0
```

下一步固定为：

```text
Workbench getUserMedia / 用户授权真实音频
  -> 有效音量门槛
  -> 非 fake ASR final >= 1
  -> 同 session 生成建议/方案/复盘
  -> Workbench 可见同 session
  -> 删除验证
  -> real mic lane evidence bundle
```

禁止把以下内容继续包装成主线进展：

- 新增 readiness / preflight / approval wrapper。
- demo smoke。
- synthetic/replay preview。
- file lane Go 的重复外推。
- real mic 没有 ASR final 前的 LLM 质量扩展评测。
- P0 未通前的 Tauri、Mac/Windows 安装包、iOS/Android、系统音频采集扩展。

2026-07-05 DEC-217 reset: 当前项目状态正式收敛为 `Local Shadow Preview / Engineering Demo ready`，不是 `Shadow Pilot ready`，也不是 `Production MVP ready`。完整复盘入口为 `docs/project-release-readiness-reset-2026-07-05.md`。下一阶段不得继续把 readiness/preflight/approval/preview/wrapper-only 工作算作主线进展，除非它直接改变 `quality_exit_status`、`real_mic_shadow_readiness_status`、`user_can_start_real_mic_shadow_test_now`、`normalized technical entity recall`、`formal card/report evidence status` 或真实会议反馈指标。

2026-07-05 DEC-218 implementation: Local Shadow Preview release path 已落到 Web 工作台、FastAPI API、browser smoke 和 mainline runner 顶层报告。当前允许声明的范围仍只有 `local synthetic/replay/artifact Copilot preview`；UI/runner 必须同时显示 `ASR quality not_exited`、`real mic blocked`、`LLM disabled_not_called`、`formal card/report not-Go` 和 microphone/remote/LLM safety flags=false。该实现只把当前真实状态可见化，不授权麦克风、系统音频、远程 ASR、LLM 或生产发布。

## 1. 当前主线

产品是中文技术会议实时 Copilot，不是普通转写工具。主线固定为：

```text
ASR final/revision
  -> EvidenceSpan
  -> meeting state / engineering gap candidate
  -> suggestion candidate/card
  -> feedback/export readiness
  -> desktop runtime
  -> 用户真实麦克风 shadow test
```

PC/桌面端优先。Local Web MVP 只是验证 core/API/UI/event 的本地切片；Mac/Tauri desktop shell、ASR worker handoff 和用户显式授权麦克风 adapter 才是当前产品形态主线。

当前总控计划入口是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`。若旧阶段报告、`docs/superpowers/plans/**` 或 2026-07-02 文档与该总控计划冲突，以 2026-07-03 P0/P1 文档为准。

2026-07-03 计划确认入口是 `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`：我负责公开授权音频来源审查、合成音频模拟、本地 ASR 自测和指标报告；用户最终负责真实麦克风会议 shadow test。

2026-07-03 最新用户确认：完整计划必须继续按“网上公开音频来源复核 + 合成音频/模拟转写自测 + 用户最终真实麦克风会议验证”执行。当前结论是计划已写下，不再新建散点计划；后续只更新本索引、总控计划、RTM 和 decision-log。公开音频只能推进到官方来源复核和 no-download sample manifest，除非后续出现可复核 clip manifest 或用户明确批准 GB 级公开包下载。

2026-07-03 本轮执行确认：`转写类验证` 的责任边界保持为我先完成官方公开来源复核、no-download manifest、合成中文技术会议、mock streaming events 和 approved synthetic event replay；`最终真实麦克风会议验证` 仍由用户在 readiness gate 满足后手动执行。当前不得把“再找公开视频/音频”扩展成抓取 Bilibili、YouTube、播客、公开课、技术大会录播或版权链不清材料；也不得为了补公开音频证据而默认下载 AliMeeting/AISHELL-4/AISHELL-1 的 GB 级包。

2026-07-04 用户最新确认：完整计划已经写下，且转写类验证仍由我先通过网上官方公开音频来源复核、本地合成/Mock 模拟和 ASR event replay 完成；用户最终再做真实麦克风会议验证。本轮只允许把已有 readiness 状态可见化和继续执行 bounded 模拟/ASR quality 出口，不允许访问麦克风、读取用户录音或 `.m4a`、下载 GB 级公开音频包、调用远程 ASR/LLM 或读取 `configs/local/`。

2026-07-04 DEC-175/176/177 执行锁：`DRV-044 FunASR synthetic smoke result evidence schema/gate` 已实现并完成 provenance/hash 加固。它只验证未来本地 FunASR synthetic smoke result 的 evidence schema、硬阈值、artifact provenance 和 gate 结果，不运行 ASR、不下载模型、不访问麦克风、不调用远程 ASR/LLM。单场景 smoke 只算候选；5 场景 batch confirmation 必须带 `batch_artifact_provenance_status=validated`，才能让 DRV-032 严格退出 ASR quality blocker；该 evidence 仍不是真实麦克风 Go evidence。开源基座决策同步锁定为：不 fork 会议助手项目做二开，采用自建薄主线并复用 Tauri、FastAPI、FunASR/sherpa 和 OpenAI-compatible LLM 协议；500+ star 项目如有可用能力，只能作为 provider/adapter，不替代 EvidenceSpan -> gap/card -> feedback 主线。

2026-07-04 DEC-178 执行确认：新增 `docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md` 作为短入口，明确回答“完整计划已写下，转写由我先通过网上官方公开音频和本地模拟完成，用户最终再做真实麦克风会议验证”。本轮联网复核仍只保留 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 作为默认白名单；MagicData-RAMC 等 NC/ND 或非会议来源只观察不进入自动评测。当前不下载公开音频大包，不抓公开视频，不读取真实录音，不访问麦克风，不调用远程 ASR/LLM。

2026-07-04 DEC-179/DRV-045 执行确认：新增 `tools/funasr_synthetic_smoke_execution_packet.py` 和 `tests/test_funasr_synthetic_smoke_execution_packet.py`，把 DRV-043 readiness evidence 到 DRV-044 batch provenance/hash gate 之间的手动执行交接固定下来。合法 readiness 会生成 5 场景 FunASR synthetic smoke command previews、expected output paths 和 `expected_drv044_batch_artifact_provenance` template；默认仍 `safe_to_execute_now=false`、`execution_approval_status=not_approved_manual_run_only`，不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。Focused TDD gate 为 `6 passed, 1 warning`。

2026-07-04 DEC-180/DRV-046 执行确认：新增 `tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 和 `tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py`，把 DRV-045 manual packet 产出的 5 个 smoke report JSON 装配成 DRV-044 batch evidence。工具只读取 approved `artifacts/tmp/asr_reports/**.json` artifact bytes 计算 sha256、合并 scenario_results、生成 `batch_synthetic_confirmation`，然后调用 DRV-044 gate；缺 artifact、unsafe path 或 DRV-044 quality blocker 都会 blocked。它不运行 ASR、不读取音频、不写 artifacts、不下载模型、不调用远程 provider。Focused TDD gate 为 `6 passed, 1 warning`。

2026-07-04 DEC-186 收口确认：三路只读审查和本地门禁复跑一致确认，完整计划已经写下，转写类验证由我先通过官方公开音频 no-download 路线、本地中文技术会议合成/Mock 和 approved ASR event replay 完成，真实麦克风会议由用户最终验证。公开音频当前合理 blocked，模拟 shadow pipeline batch 已证明产品链路 preview 可闭合但不算 ASR quality Go evidence。下一张主线票不得继续 readiness/report-only wrapper；默认应选择 `ASR quality exit` 的实际出口，或进入 `Mac Local Shadow MVP: Tauri/WebView synthetic event demo closure`，用 approved synthetic/mock streaming events 演示 Mac 端 Copilot 工作流，同时明确展示真实麦克风仍被 ASR quality 阻断。

2026-07-04 DEC-187/PCWEB-125 执行确认：`Mac Local Shadow MVP synthetic demo closure` 已实现。Web backend 新增 `POST /desktop/mac-local-shadow-mvp-demo/sessions`，使用内置 synthetic streaming events 创建 Live ASR session，并闭合到 transcript partial/final/revision、EvidenceSpan、state_event、scheduler_event、suggestion_candidate_event、no-LLM request draft 和真实麦克风 readiness blocker；Web 工作台新增 `Shadow MVP` 按钮和 `mac-local-shadow-mvp-panel`，点击后进入现有 Live ASR SSE 渲染路径。Focused TDD gate 为 `3 passed, 2 warnings`，`test_app.py` 全量为 `285 passed, 2 warnings`，`test_live_events.py` 为 `40 passed, 1 warning`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `Mac Local Shadow MVP synthetic demo closure`。该切片不访问麦克风、不读取真实音频、不运行 ASR、不调用远程 ASR/LLM、不下载模型或公开音频，且不算 ASR quality Go evidence。

2026-07-04 DEC-188 执行收口确认：本轮针对“完整计划是否写下、转写是否由我先找公开音频和模拟、用户是否最后真实麦克风验证”再次启动两路只读 Agent 审查。审查结论一致：计划完整，主线无本质矛盾，真实麦克风边界清楚，当前 blocker 是 ASR quality exit 而不是计划缺失。PCWEB-125 已完成，不能再把 Mac synthetic demo closure 当作下一张未完成开发票；public audio bounded manifest 仍只是条件分支，不是当前默认主线，也不能替代 ASR quality exit。下一步只允许进入 ASR quality exit 的实际出口，或在用户显式接受风险时走一次 degraded pilot；不得继续新增总控计划、同类 readiness/report-only wrapper、开放式 provider 横评、版权链不清音频搜索或默认下载 GB 级公开音频包。

2026-07-04 DEC-189/PCWEB-126 执行确认：`Realistic Meeting Simulation Pack` 已实现，用更接近真实中文技术会议的 synthetic turn stream 推进产品体验验证。Web backend 新增 `POST /desktop/realistic-meeting-simulation-pack/sessions`，生成 4 speaker / 8 turn / 47.2s 的 release+incident review synthetic stream，覆盖 partial corrections、2 次 revision、pause/overlap markers、payment-gateway、P99、0.1%、Kafka lag、rollback、feature flag 等技术词，并复用 Live ASR SSE 渲染到 transcript、EvidenceSpan、state、scheduler、suggestion candidate 和 no-LLM request draft。Web 工作台新增 `真实模拟` 按钮和 `renderRealisticMeetingSimulationPack`，点击后展示 scenario、speaker/turn/revision/state/draft、realism features、technical terms 和 readiness blockers。TDD 红灯为新 endpoint 404，focused 绿灯为 `3 passed, 2 warnings`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `realistic meeting simulation pack`。该切片不访问麦克风、不读取真实音频、不下载公开音频/模型、不运行 ASR、不调用远程 ASR/LLM、不写 audio chunk，且不算 ASR quality Go evidence 或真实麦克风 Go evidence。

2026-07-04 DEC-190/PCWEB-127 执行确认：`Long Realistic Meeting Simulation Profile` 已实现。`POST /desktop/realistic-meeting-simulation-pack/sessions` 现在支持 `profile=long_shadow`，生成 5 speaker / 16 turn / 615s 的 architecture+release+incident follow-up synthetic timeline，覆盖 5 partial、13 final、3 revision、pause/overlap、recommendation-service、payment-gateway、idempotency-key、Redis cluster、MySQL、P99、SLO、Kafka lag、rollback、feature flag 等技术词。Web 工作台新增 `长会模拟` 按钮，复用同一 Live ASR SSE 和 `renderRealisticMeetingSimulationPack`，可在 UI 里看到 `pcweb_127_long_architecture_release_review`、long timeline features、technical terms、draft report preview 和 readiness blockers。TDD 红灯为 `profile` 被请求模型拒绝并返回 422；focused 绿灯为 `3 passed, 2 warnings`；浏览器 E2E `node code/web_mvp/e2e/browser_smoke.mjs` 返回 `status=ok`，checked list 包含 `long realistic meeting simulation pack`。该 profile 仍是 synthetic-only，不访问麦克风、不读真实音频、不下载公开音频/模型、不运行 ASR、不调用远程 ASR/LLM、不写 audio chunk，不算 ASR quality Go evidence 或真实麦克风 Go evidence。

2026-07-04 DEC-191/DRV-043-045 执行确认：本机已有可用 FunASR/ModelScope 本地缓存和 synthetic audio，`tools/funasr_synthetic_smoke_readiness.py` 对 `api-review-001` 返回 `readiness_status=cache_preflight_passed_offline_execution_not_proven`、`required_cached_models_status=present`、`model_download_status=not_started`、`validation_errors=[]`。`tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 已把 ASR quality 状态从 `requires_funasr_model_dir_or_drv019_approval` 推进到 `funasr_cache_preflight_ready_requires_execution_approval`；`tools/funasr_synthetic_smoke_execution_packet.py --funasr-readiness-path artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 返回 `packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`，覆盖 5 个 synthetic 场景。no-execution packet 已固化到 ignored artifact `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-packet.json`，当前不再需要模型下载审批作为默认下一步；真正的下一步是是否批准一次本地 FunASR synthetic smoke 执行。该批准会读取 `artifacts/tmp/synthetic_audio/**` 合成音频并写 ignored `artifacts/tmp/asr_events/**` / `artifacts/tmp/asr_reports/**`，但仍不访问麦克风、不读真实用户音频、不读 `.m4a`、不读 `configs/local`、不调用远程 ASR/LLM、不下载模型。未批准前 `safe_to_run_asr_now=false`，真实麦克风 shadow test 仍 blocked。

2026-07-04 DEC-192/DRV-045 后处理闭环加固：`funasr_synthetic_smoke_execution_packet` 已补齐 `postprocess_command_previews`，每个 scenario 都有 transcript report 命令和 synthetic ASR smoke report 命令，并显式绑定 `data/asr_eval/synthetic_meetings/scripts/*.json` golden script、`data/asr_eval/glossaries/technical-terms.zh.json` 术语表、events/provider/transcript/smoke report 路径和 smoke report stdout redirect。`code/asr_runtime/scripts/transcript_report.py` 在 provider JSON 含 `audio_duration_seconds` 时不再要求人工传 `--duration-seconds`，减少手工执行断点。该变更不运行 ASR、不读取音频、不写 smoke 结果，只让一次获批本地 FunASR smoke 后可以从 provider/events artifact 直接进入 transcript report -> smoke report -> DRV-046/044/032。

2026-07-04 DEC-193/DRV-048 执行前入口加固：新增 `tools/funasr_synthetic_smoke_approved_runner.py` 和 `tests/test_funasr_synthetic_smoke_approved_runner.py`。该 runner 默认只 dry-run，读取 DRV-045 packet 后输出 `runner_status=dry_run_ready_requires_execute_flag_and_approval`、`planned_provider_command_count=5`、`planned_postprocess_command_count=10`、`executed_command_count=0`，并保持 `safe_to_run_asr_now=false`。只有同时提供 `--execute`、合法 `funasr_synthetic_smoke_execution_approval.v1` approval record 和通过校验的本地模型目录时，才会执行 5 个 provider command 和 10 个后处理 command；本轮只用 fake runner 测试 execute 分支，没有运行真实 FunASR。dry-run artifact 已写入 `artifacts/tmp/asr_reports/funasr.synthetic-smoke.approved-runner.dry-run.json`。下一步仍是显式批准一次本地 FunASR synthetic smoke；未批准前真实麦克风 shadow test 继续 blocked。

2026-07-04 DEC-194/DRV-049 approval record 模板：新增 `tools/funasr_synthetic_smoke_execution_approval_record.py` 和 `tests/test_funasr_synthetic_smoke_execution_approval_record.py`，生成 `funasr_synthetic_smoke_execution_approval.v1` 模板。默认模板 artifact 为 `artifacts/tmp/asr_reports/funasr.synthetic-smoke.execution-approval-record.template.json`，其 `approval_confirmed_by_user=false`，runner 在 `--execute` 下会返回 `blocked_missing_or_invalid_execution_approval`，不会执行任何命令。只有模板被显式确认为 `approval_confirmed_by_user=true`，且 token/scope/packet path/场景数/拒绝真实音频和远程调用等字段全部匹配，runner 才允许进入执行分支。该阶段仍不运行 ASR、不读取合成音频、不写 ASR artifacts。

2026-07-04 DEC-195 approval wrapper 互操作修复：核验发现 `funasr_synthetic_smoke_execution_approval_record.py` 输出的是 report wrapper，而 `funasr_synthetic_smoke_approved_runner.py --approval-record-path` 之前会把整个 wrapper 当作 approval record 校验，导致模板路径模式报出一组无关字段错误。已修复 runner：当 approval JSON 包含 `approval_record_template` object 时自动解包再校验。回归确认未确认模板现在只返回 `validation_errors=['approval_confirmed_by_user must be true']` 且 `executed_command_count=0`；相关测试为 `44 passed, 1 warning`。该修复不运行 ASR、不读取音频、不写 ASR artifacts。

2026-07-04 DEC-196 真实麦克风全链路升级确认：用户确认“适当的时候要调麦克风走真实全链路流程”。该方向进入主线，但不代表当前已经授权访问麦克风。执行顺序固定为：先完成本地 FunASR synthetic smoke -> DRV-046/044/032 ASR quality exit 或显式 degraded pilot -> PCWEB-115 readiness gate -> 真实麦克风 approval/start -> 真实会议 shadow test -> feedback/export。未满足 ASR quality exit 或合法降级试点前，不访问麦克风、不枚举设备、不请求权限、不采集或写真实 audio chunk。

2026-07-04 DEC-197/DRV-048 执行前安全加固：多 Agent 只读审查确认主线 blocker 仍是 ASR quality exit，但发现 approved runner 在获批执行前应二次校验 packet 内部命令和路径。已加固 `tools/funasr_synthetic_smoke_approved_runner.py`：provider/postprocess argv 必须匹配 approved DRV-045 command contract，synthetic audio/events/report/script roots 必须精确匹配，forbidden roots、`.m4a`、系统语音备忘录临时路径、`outputs/**`、仓库外路径或缺 `argv` 均在执行前 blocked，`executed_command_count=0`。TDD 红灯为 `4 failed, 6 passed, 1 warning`；focused 绿灯为 `10 passed, 1 warning`。该阶段不运行 ASR、不读音频、不写 ASR artifacts、不访问麦克风、不下载模型、不调用远程 ASR/LLM。

2026-07-04 DEC-198/DRV-050 格式桥接修复：多 Agent 审查发现原 DRV-045 `smoke_report_argv` 调用旧 `synthetic_asr_smoke_report.py` 会产出 `synthetic_asr_smoke_report.v1` 扁平摘要，无法被 DRV-046 assembler 消费。已新增 `tools/funasr_synthetic_smoke_single_result_builder.py`，并把 DRV-045 packet 的 smoke postprocess 改为输出 DRV-044 可验证的 `funasr_synthetic_smoke_result.v1` / `single_synthetic_smoke` / `scenario_results=[...]`。新增跨工具测试确认：用 DRV-045 packet 的 `smoke_report_argv` 生成 5 个单场景 artifact 后，DRV-046 可 assemble 成 `drv044_batch_evidence_validated`，DRV-044 返回 `funasr_synthetic_smoke_quality_batch_confirmed`。已重新生成 ignored no-execution packet 和 dry-run artifact；当前仍未批准真实本地 FunASR smoke，因此 ASR quality 未退出，真实麦克风仍 blocked。

2026-07-03 DEC-138 复核结论：两个只读审查 Agent 均确认计划已经完整，公开音频阶段当前正确停在 no-download blocked，真实麦克风会议最终由用户验证；旧 decision-log 中 PCWEB-097/098 这类历史 next pointer 已由 PCWEB-107、DRV-033、DEC-136 和 DEC-138 supersede。当前下一步只以第 4 节的 6 个收敛里程碑为准。

2026-07-03 PCWEB-109 执行结论：M4 已从“合同可见 / Rust no-op 静态绑定”推进到 Web UI invocation surface。普通浏览器中 `desktop-mic-adapter-contract-panel` 会显示 7 个 mic adapter no-op invocation row，状态为 `mic_adapter_browser_fallback` / `not_invoked`；未来 Tauri WebView 中才通过 `window.__TAURI__` 调用 PCWEB-107 已绑定的 no-op command。该阶段仍不请求麦克风权限、不枚举设备、不采集或写入 audio chunk、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-110 执行结论：M5 已从“待做短时本地模拟输入”推进到可审计 timeline report。`tools/asr_live_pipeline_replay.py` 现在会把 approved synthetic/mock event file replay 成 `short_local_simulated_input_status`、`asr_metrics`、`evidence_span_timeline`、`state_timeline` 和 `candidate_card_timeline`；工程样本可闭合到 candidate timeline，非工程 control 保持 0 state / 0 candidate。该阶段仍不读取 `.m4a`、不读取真实用户录音、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不下载公开音频、不运行 Cargo/Tauri。

2026-07-03 PCWEB-111 执行结论：下一轮最小闭环第一步 `ASR event manifest / provenance` 已完成。`tools/asr_live_pipeline_replay.py` 支持可选 `--event-manifest-path`，manifest 必须位于 `artifacts/tmp/asr_events`，绑定 replay events path，声明 `input_source_kind`，并保持 no-LLM/no-remote/no-mic/no-user-audio/no-public-download flags 全 false。该阶段只增加 event provenance 审计，不读取音频、不下载公开音频、不运行 ASR、不调用远程 ASR/LLM。

2026-07-03 PCWEB-111 复审加固结论：manifest provenance id fields 现在会阻断绝对路径、相对 forbidden-root 文本、反斜杠路径文本和音频路径文本，包括 `/Users/...`、`configs/local/...`、`data/asr_eval/local_samples/...`、`data\asr_eval\local_samples\...` 和 `.m4a`；blocked manifest 不读取 events，也不会把这些文本写入 report。`tests/test_asr_live_pipeline_replay.py` 当前为 `13 passed, 1 warning`。

2026-07-03 DRV-034 执行结论：公开音频 post-extraction evidence schema 已完成。`tools/public_audio_post_extraction_evidence_schema.py` 只接收未来人工批准抽样后的 evidence JSON，验证 planned sample、source attribution、archive member、clip window、expected/observed sha256、observed duration、sample rate、channel count、license citation 和 cleanup status；仍不下载、不解压、不转码、不读取音频、不运行 ASR、不调用远程 ASR/LLM。focused gate 为 `10 passed, 1 warning`。

2026-07-03 DRV-034 复审同步结论：DRV-034 后当时的明确下一步是 `replay -> DRV-033 shadow report draft adapter`，现已由 DRV-035 完成。也就是把 PCWEB-110/111 replay timeline 映射成真实 shadow-test report 草稿；模拟输入可填 transcript、ASR metrics、EvidenceSpan/state/candidate timeline 和 event provenance，但 `audio_chunk_write_status` 必须保持 `not_written`，仍不访问麦克风、不读取真实音频、不生成真实反馈。

2026-07-03 DRV-035 执行结论：`replay -> DRV-033 shadow report draft adapter` 已完成。`tools/replay_shadow_report_draft_adapter.py` 会把 replay report 映射成 `real_mic_shadow_test_report.v1` candidate report draft，并调用 DRV-033 schema 校验；成功草稿包含 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、0 feedback、`inconclusive_requires_more_shadow_tests` 和 `audio_chunk_write_status=not_written`。复审后已要求输入 replay 的远程 ASR、真实音频、`configs/local`、麦克风和 LLM safety flags 全部明确为 false，且 `validation_errors` 为空。非 candidate replay 会 blocked，不伪造产品价值。focused gate 为 `6 passed, 1 warning`。

2026-07-03 DRV-036 执行结论：`shadow report ingestion/export/feedback` 已完成。`tools/shadow_report_ingestion_export_feedback.py` 可直接 ingest DRV-033 candidate report，或读取 `artifacts/tmp/real_mic_shadow_reports` 下的 report JSON，或读取 `artifacts/tmp/asr_reports` 下的 DRV-035 adapter report 并抽取 candidate report；输出 schema validation、timeline counts、feedback analysis、feedback collection status、Go/Pivot/Stop readiness、JSON export preview 和 Markdown export preview。Replay draft 只会得到 `draft_export_preview_only` / `feedback_required_before_decision`，不能作为 Go 证据；真实反馈 report 才能输出 `ready_for_shadow_test_export`。focused gate 为 `6 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写导出文件、不调用远程 ASR/LLM。

2026-07-03 DRV-037 执行结论：`shadow report export file writer` 已完成。`tools/shadow_report_export_file_writer.py` 会调用 DRV-036，把 JSON/Markdown export preview 写入 ignored `artifacts/tmp/shadow_report_exports`；输出文件名来自安全 `session_id`，写入前阻断 unsafe output root、unsafe session id 和既有文件内容冲突；内容一致重复执行返回 `idempotent_existing_files_match`。Replay draft 可写预览但标记 `not_go_evidence_replay_or_feedback_missing`，真实反馈 report 才会标记 `go_evidence_supported_by_real_feedback_report`。focused gate 为 `7 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不写仓库可提交导出文件、不调用远程 ASR/LLM。

2026-07-03 DRV-038 执行结论：`shadow report feedback ingestion API/UI` 已完成。`tools/shadow_report_feedback_ingestion.py` 可接收 DRV-033 candidate report 或 DRV-035 adapter report path，并把 `useful/would_have_asked/wrong/too_late/too_intrusive/dismissed` 写回 candidate card timeline 和 feedback summary；真实 audio-written report 在 positive>=2 且 negative<=1 时更新为 `go` 并通过 DRV-036 readiness，replay draft 即使有正反馈也保持 `inconclusive_requires_more_shadow_tests` / `not_go_evidence_replay_or_feedback_missing`。Web backend 新增 `POST /shadow-reports/feedback-ingestions`，工作台新增 `shadow-report-feedback-panel`。focused gate 为 `11 passed, 2 warnings`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 DRV-039 执行结论：`shadow-test pilot bundle runner` 已完成。`tools/shadow_test_pilot_bundle_runner.py` 会先调用 DRV-038 写回 feedback entries，再调用 DRV-037 把更新后的报告写入 ignored `artifacts/tmp/shadow_report_exports` JSON/Markdown bundle；unsafe output root 会在读取 candidate report 前 blocked，bad feedback 不会写导出文件。真实 audio-written report 且 feedback 达标时输出 `pilot_bundle_written` / `go_evidence_supported_by_real_feedback_report`，replay draft 只输出 `pilot_bundle_preview_written_not_go_evidence`。focused gate 为 `5 passed, 1 warning`，仍不访问麦克风、不读取真实音频、不写 audio chunk、不读取 `configs/local`、不调用远程 ASR/LLM、不下载公开音频或模型、不运行 Cargo/Tauri。

2026-07-03 PCWEB-112 执行结论：`Desktop Worker/Mic Connector Contract` 已完成。`tools/desktop_worker_mic_connector_contract.py` 会复用 PCWEB-105 mic adapter contract 和 PCWEB-099 worker command protocol，把 `mic_adapter.start` 的显式用户 start preview 与 `worker.prepare(source_kind=mic)` 的 future approval blocker 放进同一 session 的组合门禁。合法 request 输出 `ready_for_worker_mic_connector_contract_review`，但 worker 侧仍保留 `source_kind requires later approval: mic`，下一步 decision 为 `approve_worker_mic_source_after_real_tauri_noop_run`。focused gate 为 `7 passed, 1 warning`，仍不访问麦克风、不请求权限、不读写 audio chunk、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-113 执行结论：`Desktop Tauri No-op Run Result Intake` 已完成。`tools/desktop_tauri_noop_run_result_intake.py` 只接收未来显式批准真实 Tauri WebView no-op run 后的 caller-provided JSON，验证 PCWEB-107 的 10 个 no-op IPC command 全部 returned，且每个返回仍为 `noop_bound/noop_only/tauri_ipc_bound`、`side_effect_status=none`、`captures_audio=false`、`spawns_process=false`、`calls_remote_provider=false`、`writes_local_files=false`。合法 result 只输出 `validated_noop_ipc_observed` / `ready_for_worker_mic_source_approval_review`，下一步为 `review_worker_mic_source_approval_packet`；该阶段仍不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 event file、不调用远程 ASR/LLM。

2026-07-03 PCWEB-114 执行结论：`Desktop Worker Mic Source Approval Packet` 已完成。`tools/desktop_worker_mic_source_approval.py` 复用 PCWEB-112 connector request 和 PCWEB-113 Tauri no-op result intake，只有二者均通过且 `tauri_run_result.run_id == connector.session_id` 时，才输出 `ready_for_manual_review_not_executable`，并在读取前阻断 forbidden `policy_path`。合法 report 仍保持 `worker_mic_source_approval_status=not_approved`、`approved_to_execute_now=false`、`safe_to_accept_worker_mic_source_now=false`，下一步为 `manual_approve_worker_prepare_source_kind_mic_or_keep_blocked`；focused gate 为 `8 passed, 1 warning`，仍不批准真实 worker/mic source、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

2026-07-03 PCWEB-115 执行结论：`Real Mic Shadow Test Readiness Gate` 已完成。`tools/real_mic_shadow_test_readiness_gate.py` 组合 DRV-032 ASR quality decision、PCWEB-114/120 worker mic source approval packet、真实 Tauri no-op evidence、真实 mic adapter evidence、ASR worker mic source evidence 和 DRV-033/036/037/038/039 export/feedback readiness，默认输出 `blocked_not_ready_for_user_real_mic_shadow_test`。PCWEB-119 已补齐真实 Tauri no-op evidence，PCWEB-120 已补齐同 session manual review packet，PCWEB-121 已提供 mic adapter implementation-boundary evidence，PCWEB-122 已提供 ASR worker real mic source boundary evidence，PCWEB-123 已提供单次 worker mic source approval evidence 机制；默认无 approval record 时仍 not approved，提供合法 PCWEB-123 evidence 后默认仍被 ASR quality 阻断。PCWEB-115 现在可消费 DRV-032 的 `degraded_pilot_accepted_with_quality_risk`，但仅作为一次用户显式 shadow-test 风险接受，`counts_as_asr_quality_go_evidence=false`。PCWEB-115 CLI 已补齐 evidence input：可从 inline JSON 或 approved `artifacts/tmp/**` JSON 证据文件加载所有前置 evidence，且在读取前阻断 forbidden roots、`.m4a`、仓库外路径和非 JSON。该工具只做静态 preflight，不访问麦克风、不读取真实音频、不运行 Cargo/Tauri、不启动 worker、不调用远程 ASR/LLM、不下载模型或公开音频。

2026-07-03 PCWEB-116 执行结论：`Desktop Tauri No-op Run Result Collector` 已完成。Web 工作台的 `desktop-mic-adapter-contract-panel` 现在额外展示 10-command collector，普通浏览器输出 `collector_browser_fallback`、`desktop_tauri_noop_run_result.v1`、10 个 `not_invoked` row 和 `real_tauri_noop_result_ready=false`；未来 Tauri WebView 中会通过 `window.__TAURI__` 调用 PCWEB-107 的 10 个 no-op IPC，并把 result 放到 `window.__meetingCopilotTauriNoopRunResult`，形状对齐 PCWEB-113。Focused static asset gate 为 `1 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 worker event file、不写本地文件、不调用远程 ASR/LLM。

2026-07-03 PCWEB-117 执行结论：`Desktop Tauri No-op Run Result Validation API/UI` 已完成。Web backend 新增 `POST /desktop/tauri-noop-run-results/validations`，复用 PCWEB-113 `desktop_tauri_noop_run_result_intake` validator；工作台普通浏览器显示 `validation_browser_fallback` / `pcweb_117_validation_status=not_submitted`，未来 Tauri WebView 中 PCWEB-116 collector 的 10 个 command 全部 returned 后才自动提交 validation。Focused gate 为 `4 passed, 2 warnings`，browser smoke 为 `status=ok`。该阶段不运行 Cargo/Tauri、不访问麦克风、不请求权限、不启动 worker、不读写 audio chunk 或 worker event file、不写本地 result 文件、不调用远程 ASR/LLM。

2026-07-03 PCWEB-118 执行结论：`Desktop First Controlled Cargo Check` 已完成。第一次受控 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml` 暴露两类真实编译阻塞：Tauri `generate_context!()` 默认需要 `src-tauri/icons/icon.png`，以及 `#[tauri::command] pub fn` 会触发 Tauri helper macro duplicate reexport。当前修复为新增最小 PNG icon、将 10 个 no-op command 改为 private `fn`、保留 `src-tauri/Cargo.lock`，并把 target 放到 ignored `artifacts/tmp/desktop_tauri_target`。Focused Cargo/Tauri policy/scaffold gate 为 `40 passed, 1 warning`，真实 `cargo check` 为 exit 0。PCWEB-118 只证明 Rust crate 可编译，不代表 `cargo tauri dev`、Tauri WebView、PCWEB-116 collector result、PCWEB-117 validation result、麦克风、worker 或 ASR quality 已完成。

2026-07-03 音频来源与模拟分工复审结论：两个只读审查 Agent 和官方网页复核一致确认，完整计划已经写下，公开音频、合成/Mock 转写和用户最终真实麦克风验证已经分开；AliMeeting/OpenSLR SLR119 与 AISHELL-4/OpenSLR SLR111 只作为 no-download 会议声学来源，AISHELL-1/OpenSLR SLR33 只做普通话 sanity，FunASR 仍需本地模型目录或 DRV-019 审批。下一步不再泛搜音频，不下载公开包，不把公开音频或 replay draft 当作产品价值 Go 证据。

2026-07-03 最新执行复核：用户再次确认“转写由我先通过网上公开音频和模拟完成，最终真实麦克风会议由用户验证”。本轮已复核 OpenSLR 官方来源并重跑 no-download 工具链：`tools/public_audio_source_whitelist.py` 通过，3 个白名单来源保持 `safe_to_download_now=false`；`tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio` 返回 `blocked_no_planned_samples`；`tools/public_audio_planned_sample_manifest_decision.py` 返回 `blocked_no_verified_public_sample_manifest`，blocked reasons 为 `no_verified_archive_member_path`、`no_expected_clip_sha256_after_extract`、`no_user_approval_for_gb_archive_download`。公开音频阶段因此继续停在官方来源复核和 manifest 模拟，不下载、不抽取、不喂 ASR。

2026-07-03 二次收敛确认：完整计划已写下且经只读 Agent 复审确认，不再新建散点计划。转写验证由我先通过官方授权公开来源审查、no-download manifest、合成音频、mock events 和 approved synthetic event replay 完成；真实麦克风会议由用户在前置链路满足后显式启动。当前 no-download 公开音频测试为 `25 passed, 1 warning`，短时模拟/ASR event 链路测试为 `24 passed, 1 warning`；ASR 质量 gate 默认仍是 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。PCWEB-119 已完成 `Real Tauri no-op run`，PCWEB-120 已把真实 Tauri evidence 接到同 session worker mic source manual review packet，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；下一步默认转向 ASR quality decision 退出动作，不继续泛搜公开视频、播客或版权链不清音频。

2026-07-03 最新用户确认后的执行锁：完整计划已经写下，本轮只做了官方来源/本地工具/只读 Agent 复核，没有访问麦克风、没有读取真实用户音频、没有下载公开音频大包、没有调用远程 ASR/LLM。公开音频阶段当前正确停在 `blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，因为缺真实 archive member path、clip sha256 和 GB 级公开包下载审批；ASR 质量阶段默认正确停在 `requires_funasr_model_dir_or_drv019_approval`，因为 sherpa 中文技术实体召回不达标且 FunASR 本地模型目录缺失。本轮 DRV-032 已补齐 machine-readable quality exit contract：本地 FunASR、DRV-019、可选远端 ASR 对照、显式降级试点四条出口；显式降级试点只能结束评估循环并进入一次 shadow-test timing/feedback 验证，不能当作 ASR quality Go evidence。PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；无 approval record 时仍 not approved，有合法 evidence 时仍不执行。下一轮不得继续新增泛化 plan/schema/readiness 来替代进展；优先顺序固定为：ASR quality 出口选择，最后才由用户执行真实麦克风会议 shadow test。

## 2. 当前已证明的结论

- Web Live ASR pipeline 已能从 `final/revision` 生成 EvidenceSpan、state、scheduler、suggestion candidate 和 LLM request draft。
- 本地 ASR event file handoff API 已完成边界加固，只允许 `artifacts/tmp/asr_events`。
- desktop-side ASR worker handoff preflight 已完成 descriptor 合同，但仍不启动 worker、不访问麦克风。
- PCWEB-096 已新增 desktop ASR worker handoff local dry-run bridge：默认只生成 Web handoff preview，显式 synthetic local test 可用临时 data dir 调 Web handoff API；仍不启动 worker、不访问麦克风、不读取真实音频、不调用远程 ASR/LLM。
- PCWEB-097 已把 PCWEB-096 dry-run readiness/status 接入 Web/Tauri no-op UI/API：`GET /desktop/asr-worker-handoff-dry-run-readiness` 和 `desktop-asr-handoff-dry-run-panel` 只展示 readiness、allowed roots、blockers 和 false safety flags，仍不读 event file、不写 session、不启动 worker、不访问音频。
- DRV-025/026 tri-lane + batch gate 已完成；DRV-027 后当前 5 场景结论推进为 `overall_decision=blocked_by_asr_quality`。
- 5 个场景 perfect/mock lane 均已 ready；4 个工程场景 real ASR 因 sherpa 技术实体 recall 不达标而阻断。
- DRV-028 已完成一轮 bounded normalizer：`incident-review-001` normalized recall 从 0.0 到 0.25，`architecture-review-001` 从 0.0 到 0.2；剩余缺失实体没有足够 ASR 文本线索，不能继续用规则猜。
- 非工程 control 当前保持 0 candidate，是必须保护的安全边界。
- sherpa 速度可用，但中文技术实体质量不足；FunASR/Paraformer 仍需本地模型目录或明确模型下载审批。
- 计划确认和多 Agent 只读审查结论一致：当前不是缺计划，而是缺可用中文技术实体 ASR 质量和可运行桌面端闭环。
- PCWEB-098 已实现 desktop ASR worker process contract，定义 future worker lifecycle、command catalog、resource limits、event output contract、approved roots 和 no-spawn/no-audio/no-remote safety flags。
- PCWEB-099 已实现 desktop ASR worker command protocol，定义 `worker.prepare/start/stop/health/collect_events/cleanup` 的 request/response envelope、lifecycle transition preview、blocked response、approved roots 和 safety flags；readiness endpoint 下一步指针已从 PCWEB-098 改为 PCWEB-099；仍不启动 worker、不访问麦克风、不读写音频、不调用 Web mutation、不运行 Cargo/Tauri。
- PCWEB-100 已实现 desktop ASR worker synthetic lifecycle harness，按 `prepare -> start -> collect_events -> stop -> cleanup` 跑通 synthetic command sequence，并在 `collect_events` 阶段复用 PCWEB-096 临时 Web handoff；仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri、不 mutate production Web session。
- PCWEB-101 已实现 desktop ASR worker implementation approval packet，定义未来 worker implementation 的 entrypoint、command runner、provider/source、event/runtime roots、资源预算和 approval tokens；合法 packet 也只会返回人工 review preview，不批准实现或执行。
- PCWEB-102 已实现 desktop ASR worker no-execution skeleton，新增 `code/asr_runtime/scripts/asr_worker_sidecar.py`、`tools/desktop_asr_worker_no_execution_skeleton.py` 和 `code/desktop_tauri/asr-worker-no-execution-skeleton.policy.json`；它只定义 module boundary 和 preview report，仍不启动 worker、不访问麦克风、不读写 event file、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-103 已实现 desktop ASR worker command runner binding preview，新增 `tools/desktop_asr_worker_command_runner_binding.py` 和 `code/desktop_tauri/asr-worker-command-runner-binding.policy.json`；它只验证 sidecar module path、future native command runner path、command catalog、provider/source 和 root 边界，合法 request 也保持 command dispatch、Tauri IPC、subprocess、health probe、event collection 和 worker execution 全部 not executed。
- PCWEB-104 已实现 desktop ASR worker command runner implementation skeleton / no-dispatch boundary，新增 `code/desktop_tauri/src-tauri/src/asr_worker_command_runner.rs`、`tools/desktop_asr_worker_command_runner_implementation_skeleton.py` 和 `code/desktop_tauri/asr-worker-command-runner-implementation-skeleton.policy.json`；Rust skeleton 未在 `lib.rs` 中绑定，合法 request 也只返回 blocked command preview，仍不 accept/dispatch worker command、不 invoke Tauri IPC、不 spawn subprocess、不读写 event file、不访问麦克风、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri；PCWEB-104 不代表真实 command runner 或 worker execution 已获批。
- PCWEB-105 已实现 desktop microphone adapter contract，新增 `code/desktop_tauri/mic-adapter-contract.policy.json` 和 `tools/desktop_mic_adapter_contract.py`；它只定义 `prepare/status/start/pause/resume/stop/delete_audio_chunks` 合同、显式用户 start 边界、ignored runtime audio root、delete 语义和 all-false safety flags；仍不访问麦克风、不请求权限、不写 audio chunk、不读取真实用户音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-106 已实现 desktop microphone adapter contract readiness UI/API，新增 `GET /desktop/mic-adapter-contract-readiness` 和 `desktop-mic-adapter-contract-panel`；端点复用 PCWEB-105 静态 contract report，显示 `mic_adapter_contract_status=specified_not_executable`、7 个 mic adapter command、approved runtime/audio chunk roots、delete semantics 和 all-false safety flags；ASR handoff readiness endpoint 现指向 `next_pcweb_id=PCWEB-106`，仍不访问麦克风、不请求权限、不枚举设备、不写或删除 audio chunk、不读取真实用户音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-107 已实现 desktop mic adapter no-op Tauri IPC binding：`code/desktop_tauri/src-tauri/src/lib.rs` 现在静态绑定 10 个 no-op command，其中 7 个是 `mic_adapter.*`；`tools/desktop_tauri_noop_shell_run_smoke.py` 会校验 exact command set、bridge id set、`generate_handler!` set、function-to-command mapping 和 no-side-effect fields；focused TDD gate 为 `20 passed, 1 warning`。PCWEB-107 仍不运行 Cargo/Tauri、不请求权限、不访问麦克风、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM。
- PCWEB-108 已实现 worker output -> Web Live ASR session closure gate：`tools/desktop_asr_worker_handoff_local_dry_run.py` 的 `synthetic_local_test` 不再只证明 Web handoff API 接收成功，而是必须在临时 Web session response 中汇总 transcript final、EvidenceSpan、state event、scheduler event、suggestion candidate 和 LLM request draft；技术会议输入返回 `closed_to_evidence_state_gap`，非工程输入即使有 transcript/EvidenceSpan 也会因没有 state/gap candidate 返回 `blocked_by_live_session_closure`。该阶段仍不启动真实 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。
- PCWEB-109 已实现 mic adapter no-op UI invocation：Web 工作台在同一个 `desktop-mic-adapter-contract-panel` 中展示 PCWEB-105 合同 readiness 和 PCWEB-107 七个 no-op command 的 invocation status；普通浏览器固定显示 `mic_adapter_browser_fallback`、7 个 `not_invoked` row 和 no-permission/no-audio/no-remote/no-LLM/no-Cargo safety flags。该阶段仍不访问麦克风、不请求权限、不枚举设备、不写或删除 audio chunk、不启动 worker、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-112 已实现 worker/mic connector contract：同一 session 下的 `mic_adapter.start` preview 能被 PCWEB-105 校验为 `validated_not_executed`，`worker.prepare(source_kind=mic)` 能被 PCWEB-099 明确阻断为 `source_kind requires later approval: mic`，组合 report 输出下一步审批决策；仍不访问麦克风、不请求权限、不读写 audio chunk、不启动 worker、不读写 event file、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- PCWEB-113 已实现 Tauri no-op run result intake：未来显式批准的真实 Tauri WebView no-op run 结果可以作为 caller-provided JSON 进入机器校验；缺失、失败、额外 command、side-effect flag 漂移或 raw path/secret/stdout 字段都会 blocked；合法结果只进入 worker mic source approval review，不自动批准真实 worker/mic source。
- PCWEB-114 已实现 worker mic source approval packet：PCWEB-112 connector 和 PCWEB-113 Tauri no-op result 必须同 session 才能形成人工 review packet；合法 packet 仍是 `not_approved` / `not_executable`，不移除 `source_kind=mic` 的真实执行边界。
- PCWEB-118 已实现 first controlled Tauri Rust `cargo check`：Tauri scaffold 当前可编译，`Cargo.lock` 已生成并应保留，target 位于 ignored `artifacts/tmp/desktop_tauri_target`；这只消除了 Rust crate compile blocker，仍未证明 Tauri WebView/no-op IPC runtime。
- PCWEB-110 已实现 short local simulated input timeline report：`tools/asr_live_pipeline_replay.py` 会在 replay report 中输出 approved synthetic event source、timeline window、ASR metrics、EvidenceSpan timeline、state timeline 和 candidate/card timeline；工程 mock sample 返回 `closed_to_candidate_timeline`，非工程 control 返回 `no_engineering_candidate_detected` 且 `candidate_card_timeline=[]`。该阶段仍不读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音，不访问麦克风、不调用远程 ASR/LLM。
- PCWEB-111 已实现 ASR event provenance manifest：replay report 现在可从 `asr_event_provenance.v1` manifest 输出 `event_manifest_status`、repo-relative `event_manifest_path`、`input_source_kind` 和 sanitized `event_provenance`，用于区分 `synthetic_audio`、`mock_streaming` 和未来 `public_audio_sample`。Manifest path、events path 绑定、schema、side-effect flags 和 provenance id path-text 都会在读取 events 前校验；仍不读取音频、不下载公开包、不调用 ASR/LLM。
- DRV-034 已实现 public audio post-extraction evidence schema：只验证未来人工批准抽样后的 evidence JSON，不读取音频、不下载/抽取/转码、不调用 ASR/LLM。它把 DRV-031 的 planned sample manifest 与未来 ASR event provenance 之间的证据交接补齐。
- DRV-033 已实现 real mic shadow test report schema：新增 `tools/real_mic_shadow_test_report_schema.py` 和 `tests/test_real_mic_shadow_test_report_schema.py`，固定 transcript、ASR metrics、EvidenceSpan/state/candidate-card timeline、feedback labels、Go/Pivot/Stop、privacy/cost flags、audio retention/delete status 和 known limitations；复审后已加固 nested timeline 字段、transcript/evidence/card 交叉引用、feedback aggregate、`go` 决策最低反馈、audio retention enum 和全 forbidden-root 预读阻断；默认只输出 schema contract，valid candidate report 只做 `schema_validated_no_audio_access` 校验；仍不访问麦克风、不读取真实录音、不写或删除 audio chunk、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
- DRV-030 已完成 public/audio simulation guard hardening：`synthetic_asr_smoke_report` 现在会在读取前阻止 allowed root 内 symlink 指向 `configs/local` 等 forbidden roots；`public_audio_sample_extraction_plan` 会按 repo root 打开已校验的 planned samples 文件；`copilot_product_value_batch_gate` 的默认 mock lane 已改为 `.mock.events.json`，不再和 real sherpa lane 共用同一事件文件。
- DRV-032 已实现 ASR quality decision gate，并扩展为 ASR quality exit contract：`tools/asr_quality_decision_gate.py` 只组合 product value batch、FunASR readiness、DRV-019 approval packet 和 DRV-031 public audio decision，不运行 ASR、不下载模型、不下载公开音频、不访问麦克风、不调用远程 ASR/LLM；当前默认输出 `decision_status=requires_funasr_model_dir_or_drv019_approval`、`quality_exit_status=not_exited`、`recommended_quality_exit_path_id=local_funasr_model_dir_if_available_else_explicit_degraded_pilot_decision`，确认 perfect/mock 5/5 ready、real sherpa blocked 4/5、非工程 candidate=0、FunASR runtime cache/local model dir missing。合法 `asr_quality_degraded_pilot_acceptance.v1` 可输出 `degraded_pilot_accepted_with_quality_risk`，只解锁一次 shadow-test 风险试点前置判断，不算 ASR quality Go evidence。
- DRV-032 现在消费 DRV-042 simulated shadow pipeline batch status：若 batch 未通过，先返回 `fix_simulated_shadow_pipeline_first`，避免把产品链路问题误判成 ASR provider 问题；当前 DRV-042 batch passed 后，ASR quality 默认仍正确停在 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`。
- DRV-043 已补齐 FunASR 本地模型预检 evidence 输入：`tools/funasr_synthetic_smoke_readiness.py --model-cache-root` 只检查必需模型文件且不回显绝对路径；`tools/asr_quality_decision_gate.py --funasr-readiness-path artifacts/tmp/**.json` 可消费该 evidence，并在 ready 时输出 `funasr_cache_preflight_ready_requires_execution_approval`，仍不运行 ASR、不下载模型、不访问麦克风。
- DRV-044 已补齐 FunASR synthetic smoke result evidence gate：`tools/funasr_synthetic_smoke_result_evidence.py` 只验证 approved `artifacts/tmp/asr_reports/**` 下的 caller-provided result JSON；单场景通过输出 `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`，batch 4 工程 + 1 非工程 control 通过才输出 `funasr_synthetic_smoke_quality_batch_confirmed`。`tools/asr_quality_decision_gate.py --funasr-smoke-result-path` 可消费该 evidence；默认仍不运行 ASR、不访问麦克风、不下载模型。
- DRV-045 已补齐 FunASR synthetic smoke execution packet：`tools/funasr_synthetic_smoke_execution_packet.py` 消费 DRV-043 readiness evidence，生成 5 场景 manual command preview、expected outputs 和 DRV-044 batch provenance template；默认仍不执行命令、不读取音频、不写产物、不下载模型。它让未来本地模型就绪后的执行路径可重复、可审计，但不算 ASR quality evidence。
- DRV-046 已补齐 FunASR synthetic smoke batch evidence assembler：`tools/funasr_synthetic_smoke_batch_evidence_assembler.py` 消费 DRV-045 packet 和 5 个 approved smoke report JSON，计算 sha256 后组装 DRV-044 batch evidence 并调用 DRV-044 gate；默认没有 artifacts 时 blocked，不运行 ASR、不读音频、不写产物、不下载模型。
- DRV-047 已补齐 ASR quality 对 DRV-046 assembly report 的 intake：`tools/asr_quality_decision_gate.py --funasr-smoke-assembly-path` 可直接消费 DRV-046 `drv044_batch_evidence_validated` report，校验嵌套 DRV-044 gate report 后复用 strict quality exit；默认仍不运行 DRV-046、不读取 artifacts、不运行 ASR、不读音频、不下载模型。
- DRV-032 CLI acceptance input 已补齐：降级试点记录可通过 inline JSON 或 approved `artifacts/tmp/**` JSON path 提供；默认没有 acceptance record，仍输出 `not_requested` / `not_exited`。合法降级 path 只允许 PCWEB-115 继续检查其它真实会议前置条件，不能写成 ASR quality Go；CLI 会在读取前阻断 forbidden roots、`.m4a`、仓库外路径和非 JSON。

## 3. 当前不做什么

- 不继续泛化 ASR/provider 横评。
- 不抓取 Bilibili、YouTube、播客、公开课、直播回放或版权链不清音频。
- 不下载 AliMeeting/AISHELL-4/AISHELL-1 的 GB 级公开包，除非具体 sample manifest 和人工审批就绪。
- 不把 MagicHub Web Meeting 等 observed-but-not-whitelisted 候选绕过白名单用于自动下载、抽样、转码或产品价值 gate。
- 不读取真实用户录音或 `data/asr_eval/local_samples/`。
- 不访问麦克风。
- 不读取 `configs/local/` 或真实 API key。
- 不调用远程 ASR/LLM。
- 不自动下载 FunASR/ModelScope 模型。
- 不把 PCWEB-118 的 `cargo check` 通过解读成 Tauri 窗口已经运行、IPC 已观测、麦克风可访问或 worker 可启动。

## 4. 6 个收敛里程碑状态与退出条件

已完成项不得重复包装成下一步。后续只把直接推进 ASR quality、真实 Tauri evidence、worker/mic source、真实 mic adapter 或用户 pilot 的工作算作主线进展。

1. ASR quality decision 一次性决策：当前默认结论为 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`；只有提供已验证 FunASR 本地模型目录、明确批准 DRV-019、选择可选远程 ASR 对照，或提交合法 `asr_quality_degraded_pilot_acceptance.v1` 显式降级试点记录，才算从评估循环退出。降级试点只允许一次用户手动 shadow-test timing/feedback 验证，`counts_as_asr_quality_go_evidence=false`。不能继续扩 provider 横评，不能从 `<unk>` 或缺失文本猜实体。
2. Real Tauri no-op run：PCWEB-118 已证明 Tauri Rust crate 可编译；PCWEB-119 已在真实 Tauri WebView 中验证 10 个 no-op IPC returned，并把 validation passed evidence 写入 ignored `artifacts/tmp/desktop_tauri_noop_run_results`。这只消除了真实 Tauri no-op result 缺口，仍不访问麦克风、不启动 worker、不读取 secret、不调用远程 provider。
3. Worker output -> Web Live ASR session 闭环：PCWEB-108 已证明 approved synthetic event output 可经 `/live/asr/local-event-files/sessions` 写入临时 Web Live ASR session，并形成 transcript/EvidenceSpan/state/gap/candidate/request draft closure；仍不启动真实 worker、不读真实音频。后续若继续 M3，只能把同一 closure summary 接到 UI/报告，不扩大到真实 worker 或麦克风。
4. Mic adapter no-op UI invocation：PCWEB-109 已在 Web UI 中展示 `prepare/status/start/pause/resume/stop/delete_audio_chunks` 7 个 no-op invocation row；普通浏览器为 `mic_adapter_browser_fallback` / `not_invoked`，Tauri WebView 才允许调用 PCWEB-107 no-op IPC；`start` 仍只能 no-op/validated-not-executed，直到真实采集审批。
5. Short local simulated input：PCWEB-110 已完成 M5 timeline report；只能用合成生成音频、mock events 或 `artifacts/tmp/asr_events` 下 approved synthetic event file 跑 EvidenceSpan -> gap/state -> candidate/card，保持非工程 control 0 candidate，并输出 ASR metrics、EvidenceSpan timeline、state timeline、candidate/card timeline；不得读取 `.m4a`、本地私有短音频、`data/asr_eval/local_samples` 或任意用户录音。
6. Real mic shadow test report schema：DRV-033 已固定真实验收报告结构，包括 transcript、ASR metrics、EvidenceSpan/state/candidate/card timeline、feedback labels、privacy/cost flags 和 Go/Pivot/Stop；之后仍必须等 desktop runtime/worker/mic adapter/export 具备，再由用户最终执行 20-30 分钟真实中文技术会议。

DRV-036 已完成 `shadow report ingestion/export/feedback`，DRV-037 已完成 `shadow report export file writer`，DRV-038 已完成 `feedback ingestion API/UI`，DRV-039 已完成 `shadow-test pilot bundle runner`，DRV-041 已完成 `simulated shadow pipeline smoke runner`，DRV-042 已完成 `simulated shadow pipeline batch smoke`，PCWEB-112 已完成 `worker/mic connector contract`，PCWEB-113 已完成 `Tauri no-op run result intake`，PCWEB-114 已完成 `worker mic source approval packet`，PCWEB-115 已完成 `real mic shadow-test readiness gate`，PCWEB-116 已完成 `Tauri no-op run result collector`，PCWEB-117 已完成 `Tauri no-op run result validation API/UI`，PCWEB-119 已完成 `real Tauri no-op WebView IPC evidence`，PCWEB-120 已完成 `worker mic source from real Tauri evidence bridge`，PCWEB-121 已完成 `minimal mic adapter implementation boundary`，PCWEB-122 已完成 `ASR worker real mic source boundary`，PCWEB-123 已完成 `single-shadow worker mic source approval evidence`，PCWEB-124 已完成 `real mic shadow-test readiness API/UI visibility`。下一张主线票不得继续评测循环或继续做同类 readiness/report-only 包装器，默认应在 ASR quality decision 的退出动作中选择；不得再把公开音频 post-extraction evidence schema、shadow report draft adapter、report ingestion/export/feedback preview、export file writer、feedback ingestion API/UI、pilot bundle runner、simulated shadow pipeline smoke/batch、worker/mic connector wrapper、Tauri result-intake wrapper、Tauri result-validation wrapper、worker mic source approval wrapper、真实 Tauri no-op run wrapper、Tauri evidence bridge、mic adapter boundary、ASR worker mic source boundary、single-shadow approval evidence、真实麦克风 readiness gate 或 readiness UI 当作待做项。

PCWEB-118 已完成 first controlled Tauri Rust `cargo check` 并消除了当前 scaffold 的编译阻塞。PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence。PCWEB-120 已完成从真实 Tauri evidence 到同 session worker mic source manual review packet 的桥接。PCWEB-121 已完成最小 mic adapter implementation boundary，并让 readiness gate 可在提供该 evidence 时移除 mic adapter blocker。PCWEB-122 已完成 ASR worker real mic source boundary，并让 readiness gate 可在提供该 evidence 时移除 `asr_worker_real_mic_source_not_available` blocker。PCWEB-123 已完成单次 worker mic source approval evidence，并让 readiness gate 可在提供合法 evidence 时移除 `worker_mic_source_not_approved` blocker。PCWEB-124 已把 PCWEB-115 默认 readiness 暴露到 Web 工作台，便于直接看到仍被 ASR quality 和缺省 evidence 阻断。PCWEB-125 已把 synthetic Live ASR 产品链路接成可点击 Mac local shadow MVP 演示入口，并已通过 browser E2E 点击验证。DRV-032 已补齐 ASR quality exit contract，PCWEB-115 已能消费显式降级试点风险接受。下一张主线票现在应选择 ASR quality 出口，而不是继续新增 compile/readiness/boundary/approval 文档。真实麦克风会议仍必须等 ASR quality exit 或合法降级试点接受后，由用户显式启动。

2026-07-03 PCWEB-120 执行结论：`Desktop Worker Mic Source From Tauri Evidence` 已完成。`tools/desktop_worker_mic_source_from_tauri_evidence.py` 读取 PCWEB-119 capture evidence，校验 `captured_validated_tauri_noop_run`、validation passed、10 个 command returned 和 all-false safety flags，派生同 session PCWEB-112 connector request，再调用 PCWEB-114 生成 manual review packet。真实 evidence CLI run 输出 `ready_for_manual_review_not_executable`、`worker_mic_source_approval_status=not_approved`、`safe_to_capture_audio_now=false`、`safe_to_start_worker_now=false`；focused gate 为 `5 passed, 1 warning`。该阶段仍不批准 worker mic source、不访问麦克风、不请求权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不调用远程 ASR/LLM。

2026-07-03 PCWEB-121 执行结论：`Desktop Mic Adapter Implementation Boundary` 已完成。`code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs` 定义 inert mic adapter runtime boundary evidence，`tools/desktop_mic_adapter_implementation_boundary.py` 校验 policy/Rust boundary 并输出 readiness gate 兼容 evidence；focused TDD gate 为 `6 passed, 1 warning`。该 evidence 能让 `real_mic_shadow_test_readiness_gate._mic_adapter_ready()` 返回 true，并从 readiness blockers 中移除 `mic_adapter_real_implementation_not_available`；但仍不请求权限、不访问麦克风、不采集或写入 audio chunk、不启动 worker、不调用远程 ASR/LLM。

2026-07-03 PCWEB-122 执行结论：`ASR Worker Real Mic Source Boundary` 已完成。`code/desktop_tauri/src-tauri/src/asr_worker_mic_source_runtime.rs` 定义 inert ASR worker mic source boundary evidence，`tools/desktop_asr_worker_real_mic_source_boundary.py` 校验 policy/Rust boundary 并输出 readiness gate 兼容 evidence；focused TDD gate 为 `6 passed, 1 warning`。该 evidence 能让 `real_mic_shadow_test_readiness_gate._asr_worker_ready()` 返回 true，并从 readiness blockers 中移除 `asr_worker_real_mic_source_not_available`；但仍不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 event/audio 文件、不访问麦克风、不调用远程 ASR/LLM。

2026-07-03 PCWEB-123 执行结论：`Worker Mic Source Single Shadow Approval` 已完成。`tools/desktop_worker_mic_source_single_shadow_approval.py` 只接收 caller-provided PCWEB-114 manual review packet report 和显式 approval record，校验 approval token、session id、scope 和 all-false safety flags；focused TDD gate 为 `6 passed, 1 warning`，集成 gate 为 `36 passed, 1 warning`。合法 evidence 能让 `real_mic_shadow_test_readiness_gate._worker_mic_source_ready()` 返回 true，并从 readiness blockers 中移除 `worker_mic_source_not_approved`；默认无 approval record 时仍 blocked/not approved，且任何路径仍不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 event/audio 文件、不访问麦克风、不调用远程 ASR/LLM。

2026-07-04 PCWEB-124 执行结论：`Real Mic Shadow-Test Readiness API/UI` 已完成。Web backend 新增 `GET /desktop/real-mic-shadow-test-readiness`，只调用 PCWEB-115 默认静态报告；Web 工作台新增 `desktop-real-mic-shadow-readiness-panel`，展示 readiness status、blockers、ASR quality exit、Tauri/worker/mic/export 前置状态、pilot protocol 和全部 safety flags。TDD 红灯为 `5 failed, 2 warnings`，实现后 focused gate 为 `5 passed, 2 warnings`。该面板只是把已有 go/no-go 可见化，不是新 gate，不访问麦克风、不读取真实音频、不读取证据文件或 `configs/local`、不创建真实存储、不启动 worker、不运行 Cargo/Tauri、不调用远程 ASR/LLM、不下载模型或公开音频。默认仍显示 `blocked_not_ready_for_user_real_mic_shadow_test`，下一步仍应按 DRV-032 ASR quality exit 或 bounded 公共/合成模拟路径推进。

2026-07-04 DRV-041 执行结论：`Simulated Shadow Pipeline Smoke Runner` 已完成。`tools/simulated_shadow_pipeline_smoke.py` 只编排已有 `asr_live_pipeline_replay`、`replay_shadow_report_draft_adapter` 和 `shadow_report_ingestion_export_feedback`，把 approved ASR event JSON 纯内存串到 `simulated_shadow_pipeline_preview_created`。工程 mock events 产出 `draft_export_preview_only`，非工程 control 返回 `blocked_by_no_candidate_timeline` 且不伪造 shadow report；可选 public audio provenance 只作为 `public_audio_sample` 来源审计，仍 `public_audio_download_status=not_downloaded`。TDD 红灯为 `5 failed, 1 warning`，原因是工具不存在；实现后 focused gate 为 `5 passed, 1 warning`。该工具不访问麦克风、不读取真实音频或 `.m4a`、不下载公开音频或模型、不调用远程 ASR/LLM、不写导出文件、不运行 Cargo/Tauri；它只证明模拟转写事件能闭合到产品价值 preview，不代表 ASR quality 达标或真实会议 ready。

2026-07-04 DRV-042 执行结论：`Simulated Shadow Pipeline Batch Smoke` 已完成。`tools/simulated_shadow_pipeline_smoke.py --batch-default-mock-events` 会运行 5 个默认 mock 场景，要求 `api/architecture/incident/release` 4 个工程场景全部 `simulated_shadow_pipeline_preview_created`，且 `non-engineering-control-001` 必须 `blocked_by_no_candidate_timeline` / `candidate_cards=0`。TDD 红灯为 `4 failed, 5 passed, 1 warning`，原因是 batch builder 和 CLI flag 不存在；实现后 focused gate 为 `9 passed, 1 warning`。真实本地 artifacts batch 输出 `scenario_count=5`、`engineering_preview_created_count=4`、`negative_control_blocked_count=1`、`negative_control_fake_candidate_count=0`，并保持 `not_go_evidence_batch_replay_or_feedback_missing`。该 batch 仍不访问麦克风、不读取真实音频、不下载模型/公开音频、不调用远程 ASR/LLM、不写导出文件。

每个下一步仍按 SDD/TDD 执行：先落需求/决策 ID 和失败用例或 smoke，再实现最小变更，最后把红灯/绿灯命令、结果、隐私边界和成本边界写回 RTM/decision-log/对应计划文档。

## 4.1 2026-07-04 DEC-205 / PCWEB-129 主线反馈与导出预览闭环

`PCWEB-129 Mainline Trial Feedback And Export Closure` 已完成。当前 PC 工作台已经支持：

```text
主线试运行
  -> suggestion candidates
  -> deterministic local feedback
  -> Markdown/JSON export preview
  -> explicit replay/synthetic not-Go evidence
```

新增入口：

- `POST /desktop/mainline-trial-feedback-export-closures`
- 工作台按钮 `闭环预览`
- 工作台面板 `主线闭环`

验证结果：

```text
Focused backend API red: 404 before endpoint existed
Focused backend API green: 1 passed, 2 warnings
Focused static UI red: missing closure button
Focused static UI green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline trial feedback export closure"
```

主线状态：

- 产品闭环已经从“试运行可见”推进到“反馈/导出预览可见”。
- 该闭环仍为 synthetic/replay draft preview，不是真实麦克风 Go evidence。
- 当前继续显示：
  - `export_readiness_status=draft_export_preview_only`
  - `go_evidence_status=not_go_evidence_replay_or_feedback_missing`
  - `final_decision=inconclusive_requires_more_shadow_tests`

后续不得再把同类 `mainline trial -> feedback/export preview` 包装成下一张主线票。下一步应在以下两类中选择：

- 继续推进真实麦克风前置缺口：ASR quality exit / explicit degraded pilot / real mic readiness evidence。
- 在现有 PC 工作台上推进真实桌面 runtime/mic adapter 的受控主流程，但仍必须先满足审批和 safety gate。

## 4.2 转写模拟与真实麦克风分工锁

后续不再把“转写验证”理解成马上开麦克风或读取真实录音。执行顺序固定为：

```text
官方公开音频来源复核
  -> no-download planned sample manifest
  -> 自建中文技术会议合成音频 / mock streaming events
  -> 本地 ASR events / tri-lane product gate
  -> desktop worker / IPC / mic adapter contract
  -> 用户最终真实麦克风 shadow test
```

当前可执行动作：

- 继续使用 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33 作为自动白名单来源；AliMeeting/AISHELL-4 用于会议声学，AISHELL-1 只做普通话 sanity check。
- MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只做候选观察，不进入自动下载、抽样、转码或产品价值 gate。
- MISP-Meeting 只做 non-commercial observed candidate；PyCon China、QCon/InfoQ 只做 future authorized-only domain candidate。无书面授权、人工标注计划和许可审查前，不抓取、不抽音频、不转写、不进入产品价值 gate。
- WenetSpeech 明确排除，因为其音频依赖 YouTube/podcast 等平台来源。
- 没有具体 `planned_samples` 时，公开音频阶段必须保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples`，不能绕去 Bilibili、YouTube、播客、公开课、技术大会录播或其他版权链不清音频。
- 真实麦克风会议只能在 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete 和导出反馈链路具备后，由用户显式启动。

本轮短入口：`docs/audio-source-simulation-and-real-mic-execution-lock-2026-07-04.md`。该文档是给后续执行看的压缩版结论，不替代本文档和总控计划；若口径冲突，以 P0/P1 文档和 decision-log 最新 accepted 决策为准。

## 5. 验证命令

Focused:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_contract.py \
  tests/test_asr_quality_decision_gate.py \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  tests/test_copilot_product_value_tri_lane_gate.py \
  tests/test_copilot_product_value_batch_gate.py \
  -q -p no:cacheprovider
```

Batch gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py \
  --scripts-root data/asr_eval/synthetic_meetings/scripts \
  --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.mock.events.json' \
  --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' \
  --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' \
  --real-provider sherpa_onnx_streaming
```

ASR quality decision:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
```

Mic adapter contract:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_contract.py
```

Short local simulated input timeline:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

ASR event provenance manifest:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_live_pipeline_replay.py \
  -q -p no:cacheprovider
```

Desktop first controlled cargo check:

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

Public audio post-extraction evidence schema:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_post_extraction_evidence_schema.py \
  -q -p no:cacheprovider
```

Shadow report ingestion/export/feedback readiness:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_ingestion_export_feedback.py \
  -q -p no:cacheprovider
```

Shadow report export file writer:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_export_file_writer.py \
  -q -p no:cacheprovider
```

Shadow report feedback ingestion API/UI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_report_feedback_ingestion.py \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_updates_report_readiness \
  code/web_mvp/backend/tests/test_app.py::test_shadow_report_feedback_ingestion_api_blocks_forbidden_report_path \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Shadow-test pilot bundle runner:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_shadow_test_pilot_bundle_runner.py \
  -q -p no:cacheprovider
```

Worker/mic connector contract:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_connector_contract.py \
  -q -p no:cacheprovider
```

Tauri no-op run result intake:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_noop_run_result_intake.py \
  -q -p no:cacheprovider
```

Worker mic source approval packet:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_approval.py \
  -q -p no:cacheprovider
```

Real mic shadow-test readiness gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  -q -p no:cacheprovider

PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_readiness_gate.py
```

Tauri no-op run result collector browser smoke:

```bash
cd code/web_mvp
node e2e/browser_smoke.mjs
```

Tauri no-op run result validation API/UI:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_accepts_collector_result_without_running_tauri \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_rejects_browser_fallback_or_side_effects \
  code/web_mvp/backend/tests/test_app.py::test_desktop_tauri_noop_run_result_validation_with_data_dir_does_not_create_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

Real mic shadow-test schema:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/real_mic_shadow_test_report_schema.py
```

Full local gate:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 5.1 2026-07-03 直接检查点：计划、公开音频、模拟转写、真实麦克风

用户最新确认的问题是：完整计划是否已经写下；转写类验证是否由我先通过网上官方公开音频和模拟完成；最终真实麦克风会议是否由用户验证。

本轮结论：

- 完整计划已经写下，不缺新的计划文档。权威入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、本文档、`docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`、RTM 和 `docs/decision-log.md`。
- 两个只读审查 Agent 结论一致：当前缺的是执行态证据，不是计划文字。公开/合成音频由我先做模拟和自测；真实麦克风会议最终由用户在前置链路满足后显式启动。
- 官方网页复核确认 AliMeeting/OpenSLR SLR119、AISHELL-4/OpenSLR SLR111、AISHELL-1/OpenSLR SLR33 仍是当前可追溯公开来源池；FunASR 仍是中文质量主候选。
- OpenSLR 官方页显示 AliMeeting 是 Mandarin multi-channel meeting speech corpus，License 为 `CC BY-SA 4.0`，`Eval_Ali.tar.gz` 为 3.42G；本项目只保留 no-download manifest 路径，不默认下载。
- OpenSLR 官方页显示 AISHELL-4 是 Mandarin multi-channel meeting speech corpus，License 为 `CC BY-SA 4.0`，`test.tar.gz` 为 5.2G；本项目只保留 no-download manifest 路径，不默认下载。
- OpenSLR 官方页显示 AISHELL-1 License 为 `Apache License v.2.0`，`data_aishell.tgz` 为 15G；它只能做普通话 ASR sanity，不证明会议声学或产品价值。
- FunASR 官方 README 当前仍说明其支持 streaming 等 ASR 能力；本项目只在已有本地模型目录或 DRV-019 明确审批后验证，不静默下载模型。

当前状态：

- 公开音频：`blocked_no_planned_samples` / `blocked_no_verified_public_sample_manifest`，原因是缺真实 `archive_member_path`、clip sha256 和 GB 级公开包下载审批。
- 合成/模拟：已有合成中文技术会议脚本、mock streaming events、approved synthetic event replay、synthetic ASR smoke report 和 product value gate；这些可继续由我自测，但不能替代真实会议。
- ASR 质量：默认 `requires_funasr_model_dir_or_drv019_approval` / `quality_exit_status=not_exited`，因为 sherpa 中文技术实体召回不达标，FunASR 本地模型目录/cache 缺失；可选出口只有本地 FunASR、DRV-019、显式远端 ASR 对照或合法显式降级试点。
- 真实麦克风：`blocked_not_ready_for_user_real_mic_shadow_test`，因为 ASR quality 仍未全绿；PCWEB-121/122/123 已提供 mic adapter、ASR worker mic source 和单次 worker mic source approval evidence 机制，但不代表真实采集或真实 worker 已可执行。
- PCWEB-115 CLI：已能加载 inline JSON 或 approved `artifacts/tmp/**` JSON evidence，并基于 DRV-032/PCWEB-119/121/122/123/DRV-033/036/037/038/039 evidence 计算 readiness；默认 CLI 仍 blocked，不读取麦克风、真实音频、`configs/local` 或任何 forbidden root。

下一步默认执行顺序：

1. 不再新增泛化计划或同类 readiness/report-only wrapper。
2. 若要继续 ASR 质量：必须提供已验证 FunASR 本地模型目录、明确批准 DRV-019、显式选择远端 ASR 对照，或提交合法 `asr_quality_degraded_pilot_acceptance.v1` 降级试点记录。
3. 若要继续公开音频：只做 AliMeeting/AISHELL-4 的 3-5 个 official clip manifest；没有真实 archive member path 和 checksum 就保持 blocked。
4. PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成同 session worker mic source manual review packet bridge，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence 机制；不得再把这些当作未完成主线。
5. 下一步只在 DRV-032 quality exit 出口之间推进；显式降级试点只验证 timing/feedback，不算 ASR quality Go evidence；仍不访问麦克风、不启动真实 worker、不写真实 audio chunk。
6. 前置 gate 全绿后，才由用户执行 20-30 分钟中文技术会议真实麦克风 shadow test。

## 5.2 2026-07-04 DEC-199 本地 FunASR synthetic smoke 主链路执行结果

用户要求停止深入边界测试，先跑主流程。本轮已执行本地合成中文技术会议 FunASR 主链路：

```text
confirmed approval record
  -> approved runner --execute
  -> 5 provider commands
  -> 10 postprocess commands
  -> DRV-046 batch assembler
  -> DRV-032 ASR quality decision gate
```

执行证据：

```text
runner_status=executed_local_funasr_synthetic_smoke_commands
executed_command_count=15
validation_errors=[]
```

```text
assembly_status=drv044_batch_evidence_blocked
artifact_read_status=read
artifact_count=5
counts_as_asr_quality_go_evidence=false
```

```text
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
safe_to_capture_microphone_now=false
```

结论：

- 主链路结构已跑通：本地合成音频 -> FunASR streaming artifacts -> transcript report -> single-result smoke report -> DRV-046 batch assembly -> DRV-032 quality gate。
- 质量门未过：工程场景 normalized technical entity recall 约为 `0.50 / 0.20 / 0.25 / 0.00`，低于 `>=0.80`；RTF 约为 `0.92-1.01`，高于 `<=0.60`。
- 失败点不是执行器或格式桥接，而是当前本地 FunASR route 对中文技术会议中的英文服务名、指标名、错误码和混合中英词保存不稳定。
- non-engineering control 没有生成工程 state/candidate，负控链路表现正常。
- 真实麦克风仍不能启动；不能把当前结果包装成 ASR quality Go evidence。

下一步只做收敛动作：

1. 基于已有 provider/events artifacts 修 deterministic hotword/normalizer 和技术词归一化，重跑 postprocess/DRV-046/DRV-032。
2. 若 recall 仍不足，再进入 FunASR 参数/热词或显式 degraded pilot 取舍。
3. RTF 后续要通过长驻 ASR worker 或 batch mode 剥离模型加载重新测，不能放宽阈值。

## 5.3 2026-07-04 DEC-200 技术词 normalizer 与 FunASR batch runtime 收敛结果

本轮已按 DEC-199 后续计划完成实现和自测：

- 增加 deterministic 技术词 near-miss normalizer，不从 `<unk>` 或缺失文本中补实体。
- 重新执行 10 条 postprocess command，没有重跑原 provider/FunASR 命令。
- 重新执行 DRV-046 和 DRV-032。
- 新增 `transcribe_streaming_batch()`，验证单进程复用 FunASR model 的 runtime 表现。

主链路最终结果：

```text
postprocess_executed=10
assembly_status=drv044_batch_evidence_blocked
counts_as_asr_quality_go_evidence=false
decision_status=blocked_by_funasr_smoke_assembly_input_guard
quality_exit_status=not_exited
safe_to_capture_microphone_now=false
```

recall 改善：

| Scenario | Before | After |
| --- | ---: | ---: |
| `api-review-001` | `0.50` | `1.00` |
| `architecture-review-001` | `0.20` | `0.80` |
| `incident-review-001` | `0.25` | `0.50` |
| `release-review-001` | `0.00` | `0.75` |

RTF 对照：

| Runtime | RTF | 判断 |
| --- | ---: | --- |
| original per-file runner | `0.92-1.01` | 不达标 |
| batch `chunk_size=[0,10,5]` | `0.679` | 仍不达标 |
| batch `chunk_size=[0,20,10]` | `0.358` | 速度达标但文本质量下降 |

当前判断：

- normalizer 已把可恢复的 near-miss 拉起来，但不能解决 ASR 原文缺失实体。
- `chunk20` 可显著降低 RTF，但技术词 recall 更差，不能直接替换主链路。
- ASR quality exit 仍未退出，真实麦克风仍 blocked。
- 下一步如果继续主线，只能做受控 FunASR 参数/热词机制，或者用户显式接受 degraded pilot 风险。

## 5.4 2026-07-04 DEC-201 真实主链路自测与热词参数结论

用户要求不要继续深入边界测试，先跑真实主流程。本轮把 FunASR hotword runtime 的实际 artifacts 接入完整主链路：

```text
FunASR batch hotword provider/events
  -> transcript report
  -> single-result smoke evidence
  -> DRV-046 batch assembly
  -> DRV-032 ASR quality gate
  -> product-side replay/shadow pipeline
```

候选结果：

| Candidate | RTF | Engineering normalized recall | Gate |
| --- | ---: | ---: | --- |
| `chunk10_hotword` | `0.668-0.694` | `1.00 / 0.80 / 0.25 / 0.50` | blocked |
| `chunk20_hotword` | `0.355-0.363` | `0.50 / 0.60 / 0.25 / 0.50` | blocked |

产品 replay 结果：

- FunASR hotword events 可驱动 `api`、`architecture`、`release` 三个工程场景生成 preview/candidate。
- `incident-review-001` 两组都失败为 `blocked_by_no_candidate_timeline`，根因是 ASR 文本丢失足够多事故上下文。
- mock control 仍为 `simulated_shadow_pipeline_batch_passed`，`engineering_preview_created_count=4`，`negative_control_blocked_count=1`，说明产品 replay/card pipeline 本身不是当前主 blocker。

当前判断：

- 热词 manifest 支持保留为 provider 扩展口，但本轮不能解锁 ASR quality exit。
- `chunk20_hotword` 速度达标但质量不达标；`chunk10_hotword` 质量也未达标且速度不达标。
- 真实麦克风仍 blocked；下一步默认推进 PC 端产品主流程和 ASR-blocked 可见状态。若要提前开麦，只能走显式 degraded pilot 风险接受。

## 5.5 2026-07-04 DEC-202 / PCWEB-128 主线 ASR-blocked 试运行入口

本轮按 DEC-201 的下一步建议推进 PC 端产品主流程，而不是继续 ASR 边界评测。

新增能力：

- `POST /desktop/mainline-asr-blocked-trial/sessions`
- Web 工作台按钮：`主线试运行`
- Live ASR SSE 复用现有长会 synthetic timeline。
- Shadow MVP panel 展示 DEC-201 质量阻断摘要。

面板必须可见：

```text
mainline_asr_blocked_trial
DEC-201
not_exited
blocked_by_funasr_smoke_assembly_input_guard
chunk10_hotword
chunk20_hotword
incident-review-001
continue_pc_product_flow_keep_real_mic_blocked
```

验证：

```text
focused pytest: 2 passed, 2 warnings
browser smoke: status=ok, checked includes "mainline ASR blocked trial"
```

当前判断：

- PC 端已有一个主线入口展示“产品链路可运行，但真实麦克风因 ASR quality blocked 不能启动”。
- 这不是 ASR quality Go evidence，不是真实麦克风 Go evidence，也没有调用远程 ASR/LLM。
- 下一步继续产品可用闭环，而不是重启 provider 横评。

## 5.6 2026-07-04 DEC-203 PC 产品主流程 smoke 自测

用户要求不要继续深入边界测试，先把主流程跑通。本轮复用本地 Web MVP 服务和 PCWEB-128 主线入口，执行产品链路 smoke：

```text
POST /desktop/mainline-asr-blocked-trial/sessions
  -> Live ASR session manual_true_mainline_selftest_20260704
  -> /live/asr/sessions/{session_id}/draft
  -> /live/asr/sessions/{session_id}/llm-request-drafts
  -> browser smoke verifies workbench UI
```

自测结果：

```text
health: ok
focused pytest: 1 passed, 2 warnings
browser smoke: status=ok, checked includes "mainline ASR blocked trial"
```

主流程产物摘要：

- `transcript_final=13`
- `transcript_revision=3`
- `state_event=17`
- `scheduler_event=17`
- `suggestion_candidate_event=17`
- `llm_request_draft_event=17`
- draft review 中 `transcript_segments=16`、`evidence_spans=19`、`state_candidates=17`、`suggestion_candidates=17`
- LLM 仍为 `not_called`
- 真实麦克风 readiness 仍为 `blocked_not_ready_for_user_real_mic_shadow_test`

结论：

PC 产品主流程 smoke 已跑通，当前主 blocker 不是产品链路未连通，而是真实 ASR quality 与真实麦克风 readiness 未通过。当时的下一步 `PCWEB-129 Mainline Trial Feedback And Export Closure` 现已由 DEC-205 完成；后续不再围绕同类 preview 包装，继续回到 ASR quality exit、真实麦克风 readiness 或受控桌面 runtime 主线。

证据文档：

- `docs/mainline-product-smoke-selftest-2026-07-04.md`

## 5.7 2026-07-04 DEC-204 / PCWEB-130 UI/UX Pro Max 工作台重设计

用户要求安装 `ui-ux-pro-max-skill` 并改善当前展示页。已执行：

```text
npx --yes ui-ux-pro-max-cli init --ai codex
```

安装结果：

- 项目级：`.codex/skills/ui-ux-pro-max`
- 全局：`~/.codex/skills/ui-ux-pro-max`

设计取舍：

- 采用 skill 生成的暗色开发者工具、微交互、状态反馈、响应式与 reduced-motion 建议。
- 不采用其误判出的 landing-page pattern，因为当前目标是 PC 工作台，不是营销页。
- 页面改为深色专业工作台：品牌锁定区、分组 toolbar、绿色主线 CTA、阻塞状态 chip、深色 panel/tile、移动端单列状态条。

验证：

```text
TDD red: static asset test failed on missing app-shell
Focused green: 1 passed, 2 warnings
Browser smoke: status=ok, checked includes "mainline ASR blocked trial"
Visual:
  artifacts/tmp/ui_screenshots/workbench-home-dark-v4.png
  artifacts/tmp/ui_screenshots/workbench-mobile-dark-v4.png
Mobile layout: scrollWidth == clientWidth
```

证据文档：

- `docs/pcweb-130-ui-ux-pro-max-workbench-redesign.md`

## 5.8 2026-07-04 DEC-209 / M2 Mac 系统音频采集适配层

M2 已从“固定写未实现 blocker”推进为可执行的本地 adapter + mainline gap 接入。新增：

```text
tools/mac_system_audio_capture_adapter.py
tests/test_mac_system_audio_capture_adapter.py
docs/mac-system-audio-capture-m2-plan-2026-07-04.md
```

核心决策：

- 默认 no-capture preflight，不请求权限、不录音、不枚举或读取真实用户音频。
- 显式录制才通过 `ffmpeg avfoundation` 从指定 audio input index 采 16k mono s16 WAV。
- 要做 Mac 系统音频数字采集，当前推荐先走虚拟系统音频输入设备；ScreenCaptureKit 作为未来 native path，不在本 M2 实现内。
- 采集输出必须先进 M1 `audio_capture_healthcheck`，不能绕过健康门。
- 即使 M2 health pass，也只移除 `mac_system_audio_capture` blocker；不移除 `production_asr_quality` 和 `real_meeting_go_evidence` blocker。
- 不新增付费 ASR 项目，不调用远程 ASR/LLM，不读取 `configs/local`。

主链路 runner 已新增 `system_audio_capture` report 字段。默认仍显示：

```text
capture_adapter_status=preflight_only_not_capturing
mac_system_audio_capture=blocked_requires_m2_system_audio_capture
```

当注入或未来真实显式 M2 capture 的 `audio_health.health_status=audio_capture_health_passed` 时：

```text
mac_system_audio_capture=implemented_and_verified
real_meeting_go_evidence=blocked_requires_explicit_user_approval
```

M2.1 已把显式系统音频 capture 接进 mainline CLI：

```text
--system-audio-record-seconds
--system-audio-device-index
--system-audio-output-path
```

只有 `--system-audio-record-seconds > 0` 才调用 M2 adapter。默认主链路仍不录制、不请求权限。

已完成验证：

```text
M2 adapter: 8 passed, 1 warning
mainline integration: 9 passed, 2 warnings
adjacent regression: 28 passed, 2 warnings
mainline CLI + browser smoke: exit 0, browser_smoke_status=passed
sensitive scan: no matches
```

后续仍需用户明确授权后的真实 Mac virtual-system-audio health capture。

## 5.9 2026-07-04 DEC-210 / 本地 Artifact Retention And Delete Boundary

本地主链路自测产物现在有 retention/delete 边界。新增：

```text
tools/local_artifact_retention.py
tests/test_local_artifact_retention.py
docs/local-artifact-retention-delete-2026-07-04.md
```

主链路 runner 已新增：

```text
artifact_retention
```

默认策略：

```text
retention_status=local_artifacts_retained
retention_policy=local_artifacts_retained_until_explicit_delete
```

显式删除只允许 approved ignored artifact roots，不读取 artifact 内容，只记录 path/existence/size/action，并在删除前阻断 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/` 和 repo 外路径。

已完成验证：

```text
local artifact retention: 4 passed, 1 warning
mainline integration: 13 passed, 2 warnings
adjacent regression: 32 passed, 2 warnings
syntax: exit 0
```

## 5.10 2026-07-04 DEC-211 / 主链路 Copilot Report Preview

主链路 runner 现在不再只输出 `draft_review_created`，而是把结构化 Live ASR draft 转成一等 `copilot_report_preview` 字段。新增能力：

```text
transcript
  -> evidence_span
  -> meeting_state
  -> suggestion_candidate
  -> llm_request_draft
  -> feedback_export_preview
```

报告字段：

```text
draft_review.formal_report_status=formal_report_preview_created
copilot_report_preview.preview_status=copilot_report_preview_created
copilot_report_preview.is_formal_go_evidence=false
copilot_report_preview.value_chain=[transcript,evidence_span,meeting_state,suggestion_candidate,llm_request_draft,feedback_export_preview]
```

Markdown 报告新增 `Copilot Report Preview` 章节，展示 top state items、top suggestion candidates、closure decision 和 quality blockers。该 preview 不调用 LLM、不调用远程 ASR、不读取真实音频，不是真实会议 Go evidence。

验证：

```text
RED: formal_report_status was not_created
GREEN: tests/test_mainline_usable_e2e_runner.py::test_runner_executes_mainline_and_writes_traceable_reports passed
```

## 5.11 2026-07-04 DEC-212 / ASR Quality Decision Evidence Into Mainline

主链路 runner 现在可以读取 approved ASR quality decision artifact：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id asr_quality_evidence_mainline_20260704 \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json
```

新增报告字段：

```text
asr_quality.source_status
asr_quality.source_path
asr_quality.decision_status
asr_quality.quality_exit_status
asr_quality.blocked_reasons
```

边界：

- 只读 `artifacts/tmp/asr_reports/*.json`。
- 在读取前阻断 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/`、`outputs/`、repo 外路径、`.m4a` 和非 JSON。
- 不运行 ASR、不读取音频、不下载模型、不调用远程 ASR/LLM。

真实已有 artifact 的主链路结论：

```text
asr_quality.source_status=provided_asr_quality_decision_report
asr_quality.decision_status=blocked_by_funasr_smoke_assembly_input_guard
asr_quality.quality_exit_status=not_exited
production_asr_quality=blocked_by_asr_quality
gap_summary.implemented_and_verified=6
```

这说明 ASR quality blocker 已经从“没接入主链路”推进到“主链路可消费真实本地质量证据，且证据显示当前未达生产门槛”。后续不要继续新增同类 readiness/report-only wrapper；默认优先级为：

```text
1. 修复或优化本地 FunASR synthetic smoke 质量，争取 DRV-046/044/032 通过
2. 让 approved ASR event artifact 直接进入 Web Live ASR handoff，降低 hardcoded mock 占比
3. 用户明确授权后再跑真实 Mac system audio / mic health capture
```

最终验证：

```text
mainline runner: 13 passed, 2 warnings
adjacent regression: 36 passed, 2 warnings
syntax check: exit 0
final browser mainline smoke with ASR quality artifact: exit 0, browser_smoke_status=passed
sensitive scan: no matches
```

## 5.12 2026-07-04 DEC-213 / Approved ASR Event Artifact Handoff Into Mainline Runner

主链路 runner 现在可以把 approved ASR event artifact 交给现有 Web handoff API：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_handoff_verified_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/api-review-001.mock.events.json \
  --asr-events-provider local_mock_asr_artifact
```

新增报告字段：

```text
asr_event_handoff.handoff_status
asr_event_handoff.events_path
asr_event_handoff.event_source
asr_event_handoff.input_event_counts
asr_event_handoff.live_event_counts
```

总控 smoke 结论：

```text
asr_event_handoff.handoff_status=local_asr_event_file_handoff_created
asr_event_handoff.event_source.is_mock=false
asr_event_handoff.live_event_counts.transcript_final=3
asr_event_handoff.live_event_counts.state_event=1
asr_event_handoff.live_event_counts.suggestion_candidate_event=1
asr_event_handoff.live_event_counts.llm_request_draft_event=1
asr_event_artifact_handoff=implemented_and_verified
gap_summary.implemented_and_verified=7
```

这把“ASR artifact 能进入 Web Live ASR handoff”从历史工具/endpoint 能力提升到了当前总控 runner 的可验证分支。它仍不代表 ASR quality 通过，也不是真实会议 Go evidence。

验证：

```text
RED: run_mainline_usable_e2e_selftest did not accept asr_events_path
GREEN: 1 passed, 2 warnings
mainline runner: 13 passed, 2 warnings
final browser smoke with ASR quality + ASR event artifact: exit 0, browser_smoke_status=passed
```

## 5.13 2026-07-04 DEC-214 / ASR Event Artifact As Mainline Trial And Feedback Closure Source

主链路 runner 现在不再只把 approved ASR event artifact 当作辅助 handoff。只要传入 `--asr-events-path`，主 session 就会创建为 artifact-backed trial，并且 draft review、Copilot report preview、feedback/export closure 都围绕同一个 artifact-backed session 执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/mainline_usable_e2e_runner.py \
  --session-id final_goal_artifact_mainline_closure_green_20260704 \
  --run-browser-smoke \
  --asr-quality-decision-path artifacts/tmp/asr_reports/funasr.synthetic-smoke.asr-quality-decision-chunk20_hotword.json \
  --asr-events-path artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json \
  --asr-events-provider local_artifact_asr
```

新增/关键报告字段：

```text
mainline_trial.trial_id
mainline_trial.ingest_mode
mainline_trial.events_path
mainline_trial.source_event_artifact_status
closure.source_trial_id
closure.source_event_artifact_status
asr_event_artifact_closure
```

总控 smoke 结论：

```text
overall_status=mainline_product_chain_exercised_with_expected_blockers
mainline_trial.trial_id=mainline_asr_event_artifact_trial
mainline_trial.trial_status=mainline_artifact_trial_session_created
live_asr.event_counts.transcript_final=4
live_asr.event_counts.suggestion_candidate_event=3
closure.source_trial_id=mainline_asr_event_artifact_trial
closure.source_event_artifact_status=local_asr_event_file_handoff_created
closure.closure_status=mainline_trial_feedback_export_preview_created
browser_smoke_status=passed
gap_summary.implemented_and_verified=8
```

边界结论：

```text
api-review-001.mock.events.json -> only 1 suggestion candidate -> closure reports blocked_by_candidate_report
m15_runner_artifact_mainline.events.json -> 3 suggestion candidates -> closure preview created
```

这说明主链路现在可以区分两件事：

- 工件可读、可 handoff；
- 工件内容是否足以支撑 meeting-copilot feedback/export closure。

验证：

```text
RED: artifact-backed closure endpoint/test missing, then source tool loader used mutable REPO_ROOT
GREEN: code/web_mvp/backend/tests/test_app.py::test_desktop_mainline_event_artifact_trial_closes_feedback_export_preview passed
RED: runner still used blocked trial as main source
GREEN: tests/test_mainline_usable_e2e_runner.py::test_runner_uses_asr_event_artifact_as_mainline_trial_source passed
focused regression: 15 passed, 2 warnings
final artifact-backed browser smoke: exit 0
```

## 5.14 2026-07-04 DEC-215 / Artifact-Backed Mainline Is Visible In PC Workbench

PC workbench 现在新增 `工件主线` 按钮。该入口调用：

```text
POST /desktop/mainline-asr-event-artifact-trial/sessions
events_path=artifacts/tmp/asr_events/m15_runner_artifact_mainline.events.json
provider=local_artifact_asr
```

用户可见链路：

```text
工件主线
  -> mainline_asr_event_artifact_trial
  -> Live ASR SSE
  -> transcript / EvidenceSpan / state / suggestion candidate / LLM draft
  -> 闭环预览
  -> source_event_artifact_status=local_asr_event_file_handoff_created
```

Browser smoke 已覆盖：

```text
node code/web_mvp/e2e/browser_smoke.mjs
Result: exit 0
Checked:
  - mainline ASR event artifact trial
  - mainline ASR event artifact feedback export closure
```

前端边界：

- 不读取音频。
- 不请求麦克风权限。
- 不调用远程 ASR/LLM。
- 不读取 `configs/local`。
- artifact preview 仍标记为 not-Go evidence。

剩余主线方向更新：

```text
1. ASR quality exit: FunASR synthetic smoke 仍未通过 DRV-046/044/032。
2. Real capture: Mac system audio / mic 仍需要用户明确授权和设备路由。
3. LLM execution: 当前仍是 request draft，不默认调用远程模型。
4. UI refinement: 继续改善状态密度、候选不足原因和 ASR quality blocker 的展示，但主入口已可用。
```

## 6. 关键文档

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/plan-confirmation-audio-simulation-and-real-mic-boundary-2026-07-03.md`
- `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`
- `docs/plan-lock-public-audio-simulation-and-real-mic-validation-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/copilot-product-value-batch-result-2026-07-03.md`
- `docs/asr-quality-decision-gate-2026-07-03.md`
- `docs/asr-mainchain-fullflow-selftest-2026-07-04.md`
- `docs/mainline-product-smoke-selftest-2026-07-04.md`
- `docs/pcweb-130-ui-ux-pro-max-workbench-redesign.md`
- `docs/mac-system-audio-capture-m2-plan-2026-07-04.md`
- `docs/local-artifact-retention-delete-2026-07-04.md`
- `docs/mainline-goal-progress-and-next-plan-2026-07-04.md`
- `docs/pcweb-105-desktop-mic-adapter-contract-plan.md`
- `docs/pcweb-106-desktop-mic-adapter-contract-readiness-ui-plan.md`
- `docs/pcweb-107-desktop-mic-adapter-noop-tauri-ipc-binding-plan.md`
- `docs/pcweb-110-short-local-simulated-input-timeline-report-plan.md`
- `docs/pcweb-111-asr-event-provenance-manifest-plan.md`
- `docs/pcweb-112-desktop-worker-mic-connector-contract-plan.md`
- `docs/pcweb-113-desktop-tauri-noop-run-result-intake-plan.md`
- `docs/pcweb-114-desktop-worker-mic-source-approval-plan.md`
- `docs/pcweb-115-real-mic-shadow-test-readiness-gate-plan.md`
- `docs/pcweb-116-desktop-tauri-noop-run-result-collector-plan.md`
- `docs/drv-033-real-mic-shadow-test-report-schema-plan.md`
- `docs/drv-034-public-audio-post-extraction-evidence-schema-plan.md`
- `docs/drv-035-replay-shadow-report-draft-adapter-plan.md`
- `docs/drv-036-shadow-report-ingestion-export-feedback-plan.md`
- `docs/drv-037-shadow-report-export-file-writer-plan.md`
- `docs/drv-038-shadow-report-feedback-ingestion-api-ui-plan.md`
- `docs/drv-039-shadow-test-pilot-bundle-runner-plan.md`
- `docs/drv-045-funasr-synthetic-smoke-execution-packet-plan.md`
- `docs/drv-046-funasr-synthetic-smoke-batch-evidence-assembler-plan.md`
- `docs/drv-047-asr-quality-drv046-assembly-intake-plan.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

## 4.2 2026-07-04 DEC-216 / ASR quality follow-up 结论

本轮 ASR quality follow-up 已完成一轮收敛，但没有通过质量出口。

关键变化：

- 单场景 FunASR smoke report 现在输出 expected/matched/missing entity 明细。
- DRV-044 gate 会在这些明细存在时校验 recall 数值和 matched/missing 集合一致性。
- normalizer/glossary 只修当前 transcript 中可观察的 near-miss：`paymentway`、`字段 quest`、`ure store`、`REDi coasterBQP`、`auder` backlog 上下文、`trcoutservice service`。
- 没有反填 `timeout`、`监控阈值`、`staging`。

最新 `chunk20_hotword` 结果：

```text
api-review-001:          normalized_recall=1.0, missing=[]
architecture-review-001: normalized_recall=1.0, missing=[]
incident-review-001:     normalized_recall=0.5, missing=[timeout, 监控阈值]
release-review-001:      normalized_recall=0.75, missing=[staging]

DRV-046 assembly_status=drv044_batch_evidence_blocked
DRV-032 quality_exit_status=not_exited
```

主线回归仍通过 artifact-backed runner + browser smoke，但真实麦克风继续 blocked。下一步不再堆 normalizer，只做受控 ASR 输入/参数实验，或进入远程 ASR 成本/隐私审批、显式 degraded pilot 决策。

## 5.15 2026-07-08 DEC-242 / Workbench 真实页面麦克风 Gate A 仍 No-Go

本轮新增浏览器侧麦克风健康证据，并跑真实 Workbench 页面主路径：

```text
Workbench -> getUserMedia -> WebSocket /live/asr/stream/ws/rec_mrbtjiwq
  -> sherpa_onnx_realtime -> stop -> session snapshot -> UI degraded
```

证据文件：

```text
artifacts/tmp/audio_health/workbench-browser-mic-health-20260708-163131.json
```

浏览器侧 Gate A 指标：

```text
sample_count=163840
chunk_count=35
rms=0
peak=0
active_sample_ratio=0
health_status=blocked_audio_too_quiet
raw_audio_uploaded=false
remote_asr_called=false
llm_called=false
```

后端同 session：

```text
provider=sherpa_onnx_realtime
provider_mode=real
is_mock=false
asr_fallback_used=false
degradation_reasons=[asr_final_empty]
final_count=0
acceptance_eligible=false
acceptance_blockers=[degraded_asr_session, asr_final_missing, asr_transcript_empty]
```

新增/更新文档：

- `docs/p0-fullstack-audit-and-mainline-execution-report-2026-07-08.md`
- `docs/current-status-and-p0-execution-plan-2026-07-08.md`
- `docs/decision-log.md`

当前判断：

- Workbench 真实页面路径已触发，不是按钮没接。
- `real_mic_gate_a=No-Go`，因为浏览器输入样本全 0。
- 不继续 Gate B/C，不调用 LLM。
- 下一步必须先解决 macOS/浏览器输入路由，或改为系统音频/虚拟声卡输入方案。

## 7. Canonical Transcript Mainline - 2026-07-12

Current active Workbench:

```text
URL=http://127.0.0.1:8767/workbench
LLM configured=true
LLM provider=openai_compatible_gateway
LLM model=gpt-5.5
file ASR=local_funasr_batch
realtime ASR=funasr_realtime,sherpa_onnx_realtime
remote ASR enabled=false
```

Mainline behavior now:

```text
ASR audit events
  -> backend canonical projector
  -> committed segments + optional active tail
  -> one frontend render transaction
  -> continuous meeting transcript
```

Refresh and reconnect behavior:

- session summaries expose `created_at_ms`, `last_activity_at_ms`, `has_transcript`, `has_audio`, and `recoverable`;
- latest real recoverable session is restored automatically;
- mock/demo sessions are never selected as the latest real meeting;
- restore does not trigger a new paid AI run;
- interrupted real microphone sessions retain text and report the disconnected state honestly.

Fresh acceptance:

```text
311 Python tests passed
all-buttons browser status=go_workbench_all_buttons_smoke
screenshots=25
scroll-follow=passed
reload recovery=passed
revision in-place replacement=passed
canonical namespace collision=passed
browser runtime exceptions=0
browser console errors=0
HTTP 5xx=0
```

Primary evidence:

- `artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/workbench_all_buttons_report.json`
- `artifacts/tmp/ui_screenshots/workbench-all-buttons-smoke/05-reload_recovery.png`
- `artifacts/tmp/ui_screenshots/canonical-transcript-live-8767.png`
- `docs/superpowers/specs/2026-07-12-canonical-transcript-design.md`
- `docs/superpowers/plans/2026-07-12-canonical-transcript-implementation-plan.md`

Remaining release blockers are ASR semantic accuracy, a fresh controlled real-microphone run, long-meeting soak, and recording-time formal AI suggestion latency. They are not transcript-document UI blockers.

## 2026-07-13 主线持久化与派生物契约收口

本轮停止继续扩大 ASR 横评，直接修复会阻断“会后复盘/历史恢复”的主线缺陷：

- `POST /live/asr/*/minutes.json` 现在把结构化纪要写入当前会话的 `record.minutes.minutes_json`，同时保留 `minutes_json_llm_usage` 和 `minutes_json_degraded`；同一份 record 可被 `/events`、历史列表和重启后的新应用读取。
- `has_minutes` 和 SQLite history metadata 同时识别 Markdown 纪要与结构化 JSON 纪要，避免“接口成功但历史仍显示无纪要”。
- ASR 语义质量投影只对正式输入源（真实麦克风、浏览器麦克风、上传音频和受控实时音频）隐藏正式派生物；mock/demo 非验收链路允许回读自身生成的方案卡。真实坏转写仍保持 `suppressed_by_asr_semantic_quality`，没有放宽 fail-closed。
- 旧 `live_asr_sessions/*.json` 迁移到 SQLite 时，若缺失时间戳，使用 JSON 文件修改时间补齐 `created_at_epoch_ms` 与 `last_activity_at_epoch_ms`，历史排序不再退化为 0/未知时间。

TDD 与验证证据：

```text
RED: minutes JSON persistence + approach history round-trip 两项回归先失败
GREEN: backend 全量 652 passed, 2 warnings
focused mainline: minutes/approach/G3-G5 18 passed
Workbench static behavior: 159 passed, 2 warnings
browser fixture mainline: go_long_meeting_ui_evidence
  12 canonical transcript segments
  1 corrected segment
  4 suggestion cards
  2 approach cards
  minutes visible
  transcript/minutes export targets passed
  delete reset + backend 404 passed
  7 screenshots
```

本轮浏览器证据目录：

`artifacts/tmp/ui_screenshots/workbench-long-meeting-mainline-current-20260713/`

这次收口证明的是本地受控主线和历史持久化，不等同于生产 Go。真实远程 gateway、自然多人中文麦克风、中文技术语义质量、真实 wall-clock 长会、录音期正式 AI 建议延迟以及 Mac/Windows 发布验收仍是未完成项。前端“ASR 未 ready 时结束会议的有界超时/可恢复状态”已在 DEC-352 收口；剩余的是服务端 readiness 失败前的录音可靠落盘，不再用静态测试通过代替实现。

补充主线修复：`openHistorySession()` 现在先请求目标会话，只有请求成功且 operation 仍有效后才替换当前页面；历史请求失败时保留当前会议内容，避免页面进入“当前 session 仍在但正文已清空”的半状态。旧 `workbench_session_verify.mjs` 已同步到当前 `history-modal-list` / `.history-modal-item` / `button[data-action="open"]` 契约。该修复通过新增 Workbench 回归、Node syntax check 和 `git diff --check`。

## 2026-07-13 ASR 未 ready 停止路径收口

Workbench 停止会议时若 WebSocket 已连上但 ASR 尚未 ready，现在会启动 `STOP_WAIT_FOR_ASR_READY_MS=35s` 的前端 deadline。收到 ready 会清理 timer 并正常补发缓存、发送 `END`；服务端 readiness error 或 WS close 也会清理 timer；若服务端异常无响应，前端会主动关闭连接、退出等待状态并给出可重试的明确提示，不再永久锁在“正在整理”。

验证：新增 Workbench 回归先红后绿，`node --check workbench.js` 通过，后端全量 `651 passed, 2 warnings`。需要明确的剩余边界：这个修复只保证 UI 有界退出，不宣称 readiness 失败前的浏览器队列音频已经落盘。服务端目前在 ASR ready 之后才消费实时音频并创建/更新主录音 writer；“未 ready 也可靠保存录音”仍需下一项服务端异步录音接收与持久化设计。

## 2026-07-13 主线恢复后 fresh evidence

本轮目标只覆盖用户主流程，不继续 provider 横评：开始新会议 -> 浏览器模拟麦克风 -> 本地 FunASR 实时 partial/final -> L2 -> 录音期 AI 建议 -> 停止 -> 录音/文字稿/方案/纪要/历史导出。

已完成的主线修复：

- 质量策略：普通可读中文只 warning；历史会话在读取和列表展示时迁移旧语义降级状态。
- ASR 状态：真实 sidecar 报 ready 后恢复粘滞的 ASR degradation controller。
- L2：确认 final 达到 80 字或 15 秒即可调用，避免首个短 final 静默等待到停止。
- 前端并发：修正和建议进入同一串行队列，后续 final 不再因 `in_flight` 直接丢失建议。
- 前端显示：开始新会议立即清空上一场会议的可见文字、卡片和提醒；同一片段的临时提醒由最终提醒替换。
- 可见状态：远端建议请求等待期间显示“正在分析这段已确认文字”，错误和安全拒绝继续显式保留原始文字。

fresh browser evidence：

```text
artifact=artifacts/tmp/browser_live_mic/mainline-fix-final2-20260713
session=rec_mrj7xx2e
provider=funasr_realtime
provider_mode=real
is_mock=false
first_text_after_audio_active_latency_ms=5652
first_final_after_audio_active_latency_ms=12315
first_ai_suggestion_visible_latency_ms=20143
first_correction_visible_latency_ms=20143
frontend_utterance_count=5
frontend_card_count=3
audio_sha256_matches_session=true
minutes_visible=true
browser_console_error_count=0
network_error_count=0
realtime_experience_status=passed_realtime_full
realtime_ai_suggestion_status=passed_realtime_ai_suggestion_visible
realtime_transcript_compaction_status=passed_partial_correction_visible
mainline_completion_status=passed_production_mainline
```

发布判断：主线在受控中文技术音频上已闭环，但还不是“自然多人会议生产发布”。剩余明确风险只有：自然麦克风中文 ASR 质量、约 20 秒远端模型可见延迟、部分片段安全拒绝、长会议成本/稳定性和 Mac/Windows 安装包验收。后续应以这些发布风险为单独目标，不再回到已经通过的页面和 provider 边界循环。

## 2026-07-16 热路径工程化收口

本轮继续推进主线，没有新增 provider 横评或长会评测。实时 WebSocket 现在为每场会议使用一个 `max_workers=1` 的 session-scoped executor：音频分块写入、FunASR/sherpa `recognize_chunk`、最终识别、live session SQLite upsert、最终提交回调和 abort 按顺序离开 async event loop 执行。事件循环仍负责收包、发送 partial/final/candidate 和 heartbeat，因而不改变 final 确认边界和前端追加语义。

录音导出 executor 的 `wake()` 已改为 `call_soon_threadsafe()`，解决 session worker 线程触发 asyncio Event 的线程安全问题；旧 `/live/asr/sessions/{id}/events` 在 V2 export 已成功但 legacy projection 尚未刷新时，会读取 V2 durable export metadata，避免已完成的 WAV 在历史页短暂显示未组装。

TDD 证据：

```text
focused realtime/recording/V2 integration = 57 passed, 2 warnings
slow ASR + slow final callback heartbeat test = passed
V2 streaming suggestion and recording export = passed
```

代码位置：

- `code/web_mvp/backend/meeting_copilot_web_mvp/asr_stream.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/recording_export.py`
- `code/web_mvp/backend/meeting_copilot_web_mvp/app.py`
- `code/web_mvp/backend/tests/test_asr_stream.py`

本轮仍不能称为公开发布：Phase 0 provenance 仍有 dirty/untracked、模型 immutable revision/再分发授权、FFmpeg bundle 及签名公证 blocker；native Tauri capture、Keychain、Windows 真机和 provider exactly-once 也未关闭。下一步只做本地零成本 gateway 的短主链路回归和 Phase 0 gate 更新。

## 2026-07-16 Phase 0 clean source baseline

Phase 0 的源码可追溯性缺口已经收口到独立分支：`codex/phase0-clean-baseline` 的候选源码提交为 `144c9bea037ca27c579b6146057f548eb31360fb`。原 `main` 大工作树没有被回滚或提交；用户/私密录音、运行录音、有效音频制品、模型、运行产物、`configs/local` 和密钥均未进入候选提交。基线原有的 4 个 `.wav` 路径只是 0-1B、无 WAV magic 的评测占位文件。

clean checkout 的验证结果：后端 `901 passed, 1 warning`，前端 `40 passed` 且 typecheck/lint/build 通过，Rust `10 passed`，CycloneDX 1.5 SBOM 包含 888 个 components；根入口、provenance、SBOM 和桌面 runtime 合同为 `65 passed`，额外 lane-lock 回归为 `3 passed`。根验证入口已消除未声明的 `requests` 依赖，统一使用锁文件中的 `httpx`。

权威 provenance：

`/Users/chase/Documents/面试/meeting-copilot-phase0-clean/artifacts/tmp/release_provenance/phase0-clean-commit-20260716-r2/manifest.json`

结果为 `dirty_tracked_count=0`、`untracked_source_count=0`、`tracked_sensitive_count=0`，DMG path/hash 一致，但总 verdict 仍为 `no_go`。剩余 blocker 只包括旧 evidence 非 release Go、4 个 FunASR 模型的不可变 revision/制品清单/再分发状态，以及 FFmpeg bundle 的 revision/hash/再分发状态。

因此当前真实里程碑是：Phase 1A-1C 与 Phase 2 Browser Vertical/M2 功能出口已完成，Phase 0 clean source baseline 已完成；可公开发布的 Mac 客户端仍未完成。下一主线转入 Phase 3 native mic/system audio、bundle 内 runtime、Keychain 和 clean Mac E2E，不再重复已通过的 Browser Vertical 测评。

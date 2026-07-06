# Desktop Runtime Validation and Audio Simulation Plan

> 日期：2026-07-02  
> 状态：Accepted for next-stage planning  
> 范围：Meeting Copilot 从 PC Web/readiness 阶段切换到 Mac-first desktop runtime validation 阶段。  
> 目的：把主线拉回“笔记本开会时实时听见、理解、提醒”，并明确真实麦克风会议验证前的公开音频/模拟音频验证路径。  
> 边界：本文档不授权读取真实用户音频、不授权调用远程 ASR、不授权读取 `configs/local/`、不授权自动下载大体量数据集、不授权自动运行 Cargo/Tauri/package manager；这些动作必须在对应阶段单独受控执行。

## 1. 一句话结论

完整计划已经落地到本文件，并在 2026-07-03 追加主计划：

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`

后续不再把新的 no-op readiness/policy 阶段当作主线进展。下一阶段进入：

```text
公开/合成音频模拟
  -> 桌面壳真实运行
  -> 麦克风采集
  -> 本地 ASR worker
  -> final/revision transcript
  -> 会议状态和工程缺口候选
  -> 受控 LLM 建议卡片
  -> 用户真实麦克风会议验证
```

产品初心保持不变：Meeting Copilot 不是转写工具，核心价值是中文技术会议中实时发现工程讨论缺口，并用 EvidenceSpan 给出低频、可追溯、可反馈的建议。

## 2. 为什么要纠偏

前一阶段做出的安全边界有价值：

- 不误读真实 API key。
- 不误读或上传真实音频。
- 不自动调用远程 LLM/ASR。
- 不自动安装 Rust、Tauri 或 package manager 依赖。
- 不自动生成大量构建产物。
- 保证 PC Web MVP、core gate、EvidenceSpan、非工程会议 gate、LLM draft/card lifecycle contract 可追溯。

但 PCWEB-083 到 PCWEB-091 的后半段已经过度偏向：

```text
证明不会运行
证明不会采集
证明不会调用
证明不会读取
证明不会写入
```

这些不是最终产品价值。下一阶段必须让每个增量推进真实链路：

```text
audio chunk
  -> ASR final/revision
  -> EvidenceSpan
  -> meeting state
  -> engineering gap candidate
  -> suggestion card
  -> user feedback
```

## 3. 当前真实状态

已经完成：

- PC Local Web MVP 可以运行和测试。
- core 层已有 EvidenceSpan、meeting state、suggestion card、non-engineering gate、realtime timing gate 等关键约束。
- ASR runtime/bake-off 工具有本地实验能力。
- Tauri v2 source scaffold 已存在，含三个 no-op command。
- 文档、需求矩阵、决策日志和阶段报告已有基础。

尚未完成：

- 没有真实运行 Tauri 桌面窗口。
- 没有 `cargo check` 结果。
- 没有前端真实调用 Tauri IPC。
- 没有麦克风采集。
- 没有系统音频采集。
- 没有 ASR worker sidecar。
- 没有真实音频流进入 `partial/final/revision`。
- 没有真实 LLM enabled executor。
- 没有真实会议中验证建议卡片是否有用。

## 4. 音频验证策略

用户最终会做真实麦克风会议验证。在此之前，验证分三层完成，避免一上来就依赖真实会议。

### 4.1 第一层：公开授权中文语音/会议音频

用途：

- 验证中文 ASR 基础识别。
- 验证多人说话、重叠、远场、噪声和切句。
- 验证 `partial/final/revision/error/end_of_stream` 事件管线。
- 验证 RTF、CPU、内存、segment 切分和长音频稳定性。

不用于：

- 判断产品建议卡片是否真的有会议价值。
- 替代真实技术会议验证。
- 直接证明技术实体识别达标。

允许优先评估的数据源：

| Source | URL | License | Size / Traits | Use |
| --- | --- | --- | --- | --- |
| AISHELL-4 / OpenSLR SLR111 | https://www.openslr.org/111/ | CC BY-SA 4.0 | 120 小时，211 场普通话会议，4-8 人，8-channel microphone array；test 包约 5.2G | 多人会议、远场、重叠说话、speaker turn、切句和 diarization 风险 |
| AliMeeting / OpenSLR SLR119 | https://www.openslr.org/119/ | CC BY-SA 4.0 | 118.75 小时真实会议；Eval 包约 3.42G，含 far-field 和 near-field | 真实会议 ASR、近场/远场对照、多人会议转写 |
| AISHELL-1 / OpenSLR SLR33 | https://www.openslr.org/33/ | Apache License v2.0 | 15G 普通话朗读数据，400 人，16kHz | 中文 ASR smoke、模型安装和基础转写管线；不代表会议场景 |

下载原则：

- 不默认下载完整数据集。
- 先只记录来源白名单和下载计划。
- 第一次下载必须单独创建受控脚本，支持 resume、checksum、目标目录限制和清理。
- 数据只能放到 ignored 目录，例如 `data/asr_eval/public_raw/` 或 `artifacts/tmp/public_audio/`。
- 不提交原始音频，不提交下载包，不提交解压后的音频。
- 报告只记录样本 ID、来源、授权、时长、指标，不记录本机绝对路径。

公开网页/视频音频使用规则：

- 不抓取不明授权的 B 站、YouTube、播客、会议录播作为测试集。
- 只有明确允许下载和二次处理的公开授权内容才能进入 public audio whitelist。
- 没有明确授权时，只能作为人工观察竞品/场景，不进入自动评测。

### 4.2 第二层：自建中文技术会议脚本 + 合成/模拟音频

公开数据集通常不是软件工程评审会。为了验证产品差异化，需要自建可复现中文技术会议脚本。

脚本类型：

- API review：字段、兼容性、错误码、灰度。
- Release review：版本、回滚、监控、阈值、上线窗口。
- Incident review：告警、根因、止血、复盘、owner。
- Architecture review：容量、依赖、缓存、扩展性、风险。
- Mixed Chinese/English：service、repo、endpoint、schema、token、latency、SLO。
- Non-engineering control：生活、行政、泛业务讨论，工程卡片必须为 0。

音频生成方式：

- 优先使用本机可控 TTS 或离线 TTS 生成多说话人模拟音频。
- 使用音频混合脚本加入轻微重叠、停顿、环境噪声、远场衰减。
- 所有文本脚本、reference、annotation、expected gaps 都提交到仓库。
- 生成的音频放 ignored 目录，不提交大音频文件；如需要可提交很短的占位 smoke 音频。

这一层验证：

- ASR 是否能识别技术词。
- normalizer/hotwords 是否能修正服务名、字段名、指标、owner 和 deadline。
- `final/revision` 是否能稳定产生 EvidenceSpan。
- deterministic gap candidate 是否能找出 owner/deadline/rollback/test/metric 缺口。
- 非工程会议是否保持 0 工程卡。

### 4.3 第三层：用户真实麦克风会议验证

这是最终产品价值验证，不在我自动执行范围内。

进入条件：

- Mac 桌面壳能真实运行。
- 麦克风采集能手动 start/pause/resume/stop。
- ASR worker 能把真实音频转为 `partial/final/revision`。
- Web/Tauri UI 能展示 transcript、EvidenceSpan、状态候选和建议候选。
- LLM 建议卡片已受控接入，且可关闭。
- 本地数据保存、删除和导出路径明确。

验证方式：

- First pilot：用户选择 1 场 20-30 分钟中文技术会议做 shadow test。
- 第一轮卡片可以只对 host 可见，不打扰所有参会者。
- 每张卡片必须标记：useful、wrong、too_late、too_intrusive、dismissed。
- 会后导出 transcript、state timeline、card timeline、ASR metrics 和用户反馈。
- Go evidence：至少 2 个真实中文技术会议场景达标后，才视为产品路线继续投入的强证据。

## 4.4 阶段预算和止损边界

这部分用于防止验证无限扩大。后续每个阶段都必须回答：是否推进真实主链路、是否仍在免费/本地边界内、是否达到进入下一阶段的最低证据。

| 阶段 | 默认成本边界 | 默认上限 | 继续条件 | 停止/回退条件 |
| --- | --- | --- | --- | --- |
| 公开音频来源研究 | 免费，只浏览官方/原始来源 | 只进入授权清晰 whitelist；不抓 B 站、YouTube、播客或会议录播 | 找到中文、会议/普通话、可下载、许可清楚来源 | 授权不清、需要登录且条款不明、限制二次处理 |
| 公开音频下载/抽样 | 免费数据集；不调用云 ASR | 首轮 3-5 段，每段 30-120 秒；下载包优先 Eval/Test，小于单阶段计划上限 | 能生成 16k wav、ASR event 和基础指标 | 下载体量失控、checksum/来源不明、无法清理或输出落到 forbidden root |
| 合成音频 | 本机/offline TTS；不调用远程 TTS | 首轮 1-5 个脚本，每段 15-120 秒；输出只在 `artifacts/tmp/synthetic_audio` | 能稳定生成 wav 并喂给 ASR | 只能靠远程付费 TTS，或生成音频无法复现 |
| 本地 ASR | 本地模型；不调用远程 ASR | 首轮只测 sherpa/FunASR 已有环境和已缓存模型；不静默下载大模型 | normalized technical entity recall 接近或超过 0.8，RTF < 1 | 技术实体长期低于 0.8 且无 normalizer/hotword 修正路径 |
| LLM pilot | 只用用户配置的 OpenAI-compatible 中转站 | 首轮只对 synthetic/public event 做小样本 enabled smoke；严格限频、限 token、限重试 | card latency <= 30s，卡片有 EvidenceSpan，非工程 0 卡 | 费用不可控、无证据强建议、卡片主要变成总结或刷屏 |
| 真实麦克风会议 | 用户最终执行；默认本地 ASR | first pilot 1 场 20-30 分钟；Go evidence 至少 2 场 | useful/would-have-asked >= 40%，wrong/too_late/too_intrusive <= 20-25% | 两周仍跑不通真实音频到卡片，或卡片没有及时性/可信证据 |

## 5. 后续里程碑

### M1: Desktop Build Evidence

目标：

- 第一次跨过受控桌面执行边界。

动作：

- 运行 `cargo check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml`。
- 设置 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`。
- 允许生成并提交 `code/desktop_tauri/src-tauri/Cargo.lock`。
- 不采集音频、不读密钥、不调用远程、不打包、不签名。

成功标准：

- `cargo check` 成功，或失败原因明确可修。
- 构建输出仅在允许目录。
- 结果写入阶段报告。

当前执行记录：

- 2026-07-02 已运行 `python3 tools/desktop_rust_toolchain_readiness.py --probe-local-toolchain`。
- 结果：`rustc_status=missing`、`cargo_status=missing`、`rustup_status=missing`、`macos_command_line_tools_status=available`。
- 结论：本机当前不能执行 M1 `cargo check`；不自动安装 Rust，不修改 shell profile，不生成 `Cargo.lock` 或 target。
- 下一步：若继续 M1，先走 Rust toolchain 安装/确认路径，然后重新运行受控 probe 和 `cargo check`。

### M2: Real Tauri No-op Run

目标：

- Mac 桌面窗口真实打开并加载本地 Web MVP。

动作：

- 启动 Web MVP backend。
- 启动 Tauri shell。
- 加载 `http://127.0.0.1:8765/`。
- 记录窗口是否打开、页面是否加载、退出是否干净。

成功标准：

- 真实窗口打开。
- 页面加载成功。
- 不接音频、不调用远程、不打包。

### M3: Frontend-to-Tauri IPC

目标：

- Web UI 真正调用 Tauri native bridge，而不是只看 FastAPI readiness。

动作：

- 新增 Native Runtime 面板。
- 在 Tauri 环境调用：
  - `runtime_get_status`
  - `session_prepare`
  - `asr_worker_health`
- 在浏览器环境显示 fallback。

成功标准：

- Tauri 窗口内能看到 native IPC 返回。
- 浏览器模式下不报错，显示非 native fallback。
- 错误用统一 envelope 展示。

当前执行记录：

- 2026-07-02 已新增 Web 工作台 `desktop-native-runtime-panel`。
- 普通浏览器上下文显示 `browser_fallback/not_available`，并保留 `safe_to_capture_audio=false`、`safe_to_spawn_process=false`、`safe_to_call_remote_provider=false`、`safe_to_write_local_files=false`。
- Tauri 上下文将通过 `window.__TAURI__.core.invoke` 或兼容 `window.__TAURI__.tauri.invoke` 调用 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op command。
- 已通过静态 Web tests；真实 Tauri WebView 内 IPC 验证仍等待 M1/M2。

### M4: Public Audio ASR Simulation

目标：

- 用公开授权中文音频验证 ASR 和事件管线。

动作：

- 建立 public audio source whitelist。
- 先选择 AliMeeting Eval 或 AISHELL-4 test 的小样本抽取方案。
- 增加下载/抽取计划，不默认下载完整数据集。
- 将样本转为 16k wav。
- 跑 FunASR/sherpa。
- 生成 `partial/final/revision` event JSON。

成功标准：

- 至少 3 段公开音频可进入 ASR pipeline。
- 每段有时长、RTF、final latency、segment count、CER 或参考文本对齐结果。
- 结果明确标注：公开会议音频只证明 ASR/管线，不证明产品卡片价值。

当前执行记录：

- 2026-07-02 已新增 `data/asr_eval/public_sources.json` 和 `tools/public_audio_source_whitelist.py`。
- 白名单包含 AISHELL-4 / OpenSLR SLR111、AliMeeting / OpenSLR SLR119、AISHELL-1 / OpenSLR SLR33。
- 当前报告模式为 `public_audio_source_whitelist_only`，`download_status=not_started`。
- 当前不下载公开音频，不读取真实用户音频，不读取 `configs/local`，不调用远程 ASR/LLM。
- 2026-07-03 已新增 `tools/public_audio_sample_extraction_plan.py` 和 `data/asr_eval/public_sample_plan.example.json`。
- 新增 bounded sample extraction plan 仍是 `plan_only`：默认 `safe_to_download_now=false`、`safe_to_extract_now=false`，只允许 OpenSLR 白名单 source id 和 `artifacts/tmp/public_audio` / `data/asr_eval/public_raw` 目标根。
- 下一步若要进入真实公开音频，必须先人工确认具体 source split、artifact URL、下载体积、checksum 和清理策略；当前仍未下载公开音频或运行 ASR。

### M5: Synthetic Technical Meeting Simulation

目标：

- 用自建中文技术会议脚本验证产品差异化。

动作：

- 创建 5-10 个中文技术会议脚本。
- 每个脚本有 reference transcript、technical_entities、expected_state_events、expected_gap_candidates、expected_suggestion_cards。
- 每个脚本有 transcript-only / summary-only baseline expectations，避免把产品做回普通转写或会后总结。
- 生成多说话人 TTS 或模拟音频。
- 跑本地 ASR。
- 对比 raw transcript、normalized transcript、expected state/gap/card annotations 和 baseline。

成功标准：

- 至少覆盖 API、release、incident、architecture、non-engineering control。
- 每张 expected suggestion card 必须声明缺口类型、建议追问、EvidenceSpan 要求、触发时间窗和应展示/沉默/降级标记。
- 技术实体 normalized recall >= 80% 才能进入 first real-mic pilot；>= 90% precision/recall 才是 MVP/product 目标。
- Copilot 必须在至少一个场景里比 transcript-only 和 summary-only baseline 更早在追问窗口内发现工程缺口。
- 60 分钟等效建议卡频率必须保持 3-8 张。
- 非工程 control 输出 0 工程建议卡候选。

当前执行记录：

- 2026-07-03 已新增 5 个文本级合成会议脚本：
  - `data/asr_eval/synthetic_meetings/scripts/api-review.json`
  - `data/asr_eval/synthetic_meetings/scripts/release-review.json`
  - `data/asr_eval/synthetic_meetings/scripts/incident-review.json`
  - `data/asr_eval/synthetic_meetings/scripts/architecture-review.json`
  - `data/asr_eval/synthetic_meetings/scripts/non-engineering-control.json`
- 每个工程脚本已包含 `technical_entities`、`expected_state_events`、`expected_gap_candidates`、`expected_suggestion_cards` 和 transcript-only / summary-only baseline expectations。
- 非工程 control 的 `technical_entities`、`expected_gap_candidates`、`expected_suggestion_cards` 均为空，工程卡片计数为 0。
- 已新增 `tools/synthetic_meeting_script_report.py`，当前报告 `coverage_status=passed`。
- 已新增 `tools/synthetic_audio_generation_plan.py`，当前只生成本地/offline TTS 计划，默认 `safe_to_generate_audio_now=false`，不调用远程 TTS/ASR/LLM。
- 已新增 `tools/asr_event_generation_plan.py`，当前只生成本地 ASR event plan，默认 `safe_to_run_asr_now=false`，要求后续 event gate 指标包含 RTF、latency、raw/normalized CER、技术实体 precision/recall、CPU 和内存。
- 2026-07-03 已新增 `tools/synthetic_audio_local_tts_smoke.py`，并使用 macOS 本机 `say` + `afconvert` 生成 `api-review-001` 合成 wav，输出在 ignored `artifacts/tmp/synthetic_audio/`。
- 2026-07-03 已用本地 sherpa-onnx streaming 模型消费该 wav，输出 `artifacts/tmp/asr_events/api-review-001.sherpa.events.json` 和 provider/transcript/smoke reports。
- 当前 smoke 结果：duration 约 16.83s，latency 516ms，RTF 0.030667，partial 25，final 1，end_of_stream 1；但 raw 技术实体 recall 为 0.0，normalized recall 仅 0.25，只恢复 `P99`，未达到 first-pilot threshold。
- 结论：本地事件链路和性能基线可跑；中文技术词质量仍不达标，下一步必须优先验证 FunASR、hotword/normalizer 和技术词修正，不可把 sherpa smoke 视为产品 ASR 达标。

### M6: Mac Mic Capture Adapter

目标：

- 让应用真的听到本机麦克风。

动作：

- 手动请求麦克风权限。
- 枚举/选择输入设备。
- start/pause/resume/stop。
- 显示 input level、chunk count、session duration。
- 写入 ignored local runtime 目录。
- 支持一键删除。

成功标准：

- 用户点击后才开始录音。
- UI 可见录音状态和音量。
- chunk 可清理。
- 不默认上传、不默认调用远程 ASR。

### M7: ASR Worker Sidecar Integration

目标：

- 麦克风音频进入本地 ASR worker，产生 live transcript。

动作：

- mic chunk -> ASR worker。
- worker 输出 `partial/final/revision/error/end_of_stream`。
- Web/Tauri UI 消费同一事件 envelope。
- 记录 latency、RTF、CPU、内存、错误。

成功标准：

- 真实说话可产生 final/revision。
- final P95 目标 <= 3.5-5s；不达标则记录失败。
- 技术实体和切句质量有指标。

### M8: State and Gap Candidate Without LLM

目标：

- 先验证不靠 LLM 的会议状态和工程缺口候选。

动作：

- 从 final/revision 抽取状态候选。
- 用 deterministic gap rules 判断 owner/deadline/rollback/test/metric 等缺口。
- 生成 candidate card，不生成正式 LLM card。

成功标准：

- 工程会议能产生合理候选。
- 非工程会议 0 工程候选。
- 每个候选都有 EvidenceSpan。

### M9: Controlled LLM Suggestion Cards

目标：

- 在 ASR 和候选稳定后，才启用中转站生成建议卡片。

动作：

- 使用 OpenAI-compatible provider。
- 默认只使用用户配置的 LLM 中转站。
- 不增加默认远程 ASR 费用。
- 只把必要 EvidenceSpan 和状态候选发给 LLM。
- 严格限制频率、token、cooldown 和重试。

成功标准：

- 卡片在相关 final/revision 后 10-30 秒内出现。
- 每张卡都能回溯证据。
- 无证据不出强建议。
- 用户可反馈 useful/wrong/too_late/too_intrusive。

### M10: User Real Mic Meeting Validation

目标：

- 用真实中文技术会议决定继续、降级或停止。

动作：

- 用户主导选择真实会议并确认同意边界。
- 第一轮 shadow mode，卡片只对 host 可见。
- 记录 ASR、状态、卡片、延迟、反馈。

继续条件：

- 每 60 分钟 3-8 张有效卡。
- useful / would-have-asked >= 40%。
- wrong / too_intrusive / too_late <= 20-25%。
- P95 card latency <= 30s。
- 非工程会议工程卡 = 0。
- 至少 2 张卡改变或本应改变会议行为。
- 关键技术实体 recall >= 80% 可进入 first pilot；Go evidence 目标是 precision/recall >= 90% 或有明确到 90% 的工程路径。

停止或降级条件：

- 两周后仍不能跑通真实音频到卡片。
- ASR 无法保留关键技术实体。
- 卡片主要变成会后总结。
- 用户更想要普通 transcript/summary。
- 建议卡片没有及时性或可信证据。

## 6. 不再继续做的事情

- 不再新增只输出 `ready_for_explicit_*_approval` 的 PCWEB 阶段作为主线。
- 不再把 `safe_to_execute=false` 当作产品进展。
- 不再扩展 provider/secret/card lifecycle dry-run，除非它直接阻塞 enabled runtime。
- 不再用 fixture/synthetic UI success 证明产品价值。
- 不再扩大到 Windows、iOS、Android、应用商店、远程 ASR 默认接入、协作空间或知识库检索。
- 不再把文档 fan-out 作为每个微增量的主要工作量；保留必要的 PRD、Decision Log、RTM、阶段报告和专项计划。

## 7. 质量门禁

每个阶段必须有可重复证据：

- 命令。
- 输入来源。
- 输出路径。
- 指标。
- 失败原因。
- 隐私/成本边界。
- 是否推进真实主链路。

建议新增阶段性 gate：

```text
desktop-runtime
  cargo check
  tauri no-op run
  tauri ipc smoke

public-audio-asr
  source whitelist
  sample extraction
  ASR event generation
  latency/entity metrics

synthetic-tech-meeting
  scripted transcript
  generated audio
  expected gaps
  non-engineering zero-card gate

mic-live-asr
  local mic chunks
  ASR final/revision
  live UI
  cleanup

pilot-meeting
  user feedback
  card usefulness
  false-positive rate
  latency
```

## 8. 文档同步要求

本文档是下一阶段主线计划。相关文档必须同步：

- `docs/decision-log.md`：记录从 readiness mode 切换到 runtime validation mode。
- `docs/asr-evaluation-dataset.md`：补充公开音频、合成音频、真实麦克风会议三层数据策略。
- `docs/project-stage-status-and-next-work-2026-07-02.md`：后续更新时应把 PCWEB-092 no-op 路线降级为非主线，主线改为 desktop runtime + audio simulation。
- `docs/requirements-traceability-matrix.md`：后续实现 M1-M10 时增加对应需求/验收 ID。

## 9. 下一步执行顺序

推荐立即执行：

1. 记录本决策到 `docs/decision-log.md`。
2. 更新 `docs/asr-evaluation-dataset.md` 的数据分层。
3. 创建 public audio source whitelist 脚本计划。
4. 执行 M1 Desktop Build Evidence。
5. 执行 M2 Real Tauri No-op Run。
6. 执行 M3 Frontend-to-Tauri IPC。
7. 并行准备 M4/M5 的公开音频和合成技术会议模拟。

在 M1-M5 完成前，不进入真实麦克风会议验证。真实麦克风会议验证由用户最终确认和执行。

## 10. 公开视频和模拟转写执行计划

2026-07-02 已补充专项执行计划：

- `docs/superpowers/plans/2026-07-02-public-audio-and-synthetic-meeting-asr-validation.md`

2026-07-03 已补充主计划：

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`

该计划把转写自测拆成四个可验阶段：

1. 公开授权音频 source whitelist 和 bounded sample extraction plan。
2. 自建中文技术会议脚本文本层，覆盖 API、release、incident、architecture、non-engineering control。
3. 本地合成音频生成计划，默认只允许本机 TTS 或离线 TTS，生成音频写入 ignored artifact root。
4. 本地 ASR event generation gate，把公开/合成音频转成 `partial/final/revision/error/end_of_stream` 事件和 latency/entity 指标。

执行边界：

- 我负责通过公开视频/公开数据集和合成音频做前置自测。
- 用户最终再做真实麦克风会议验证。
- 前置自测不读取 `data/asr_eval/local_samples/`，不读取真实录音，不读取 `configs/local/`，不调用远程 ASR，不调用 LLM。
- OpenSLR AISHELL-4、AliMeeting、AISHELL-1 是当前默认公开来源白名单；MagicHub ASR-CCMeetingSC 因需要登录且授权限制更重，只作为人工评估候选，不进入默认自动下载。

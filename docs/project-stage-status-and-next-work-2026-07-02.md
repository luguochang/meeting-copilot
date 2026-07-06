# Meeting Copilot 阶段状态与后续工作报告

> 日期：2026-07-02  
> 范围：当前仓库 `/Users/chase/Documents/面试/meeting-copilot` 的产品、文档、代码、验证状态和下一阶段计划。  
> 生成目的：回答“项目目前进行到什么情况了，后续还在做什么”，并作为后续开发的阶段快照。  
> 边界：本报告不读取 `configs/local/`、不读取真实用户音频、不调用远程 ASR/LLM、不安装 Rust、不运行 `cargo`、Tauri 或 package manager。

## 1. 一句话结论

项目没有停在“无限评测”里，已经从早期的想法验证和 ASR bake-off，推进到：

```text
产品定位已确定
核心智能层已成型
PC Local Web MVP 已可本地验证
Live ASR synthetic -> 状态候选 -> LLM draft -> card lifecycle dry-run 链路已形成
Tauri 桌面壳静态 scaffold 已创建
桌面 Rust/Tauri 运行前安全边界已推进到 PCWEB-091
```

当前最准确的状态是：**PCWEB-091 已完成到 Tauri no-op shell local run smoke readiness boundary；但多 Agent 复盘后，PCWEB-092 no-op/readiness 路线不再作为主线。下一步应进入 desktop runtime validation：受控 `cargo check`、真实 Tauri 窗口、真实 Tauri IPC、公开/合成音频 ASR 模拟、再到麦克风采集。**

新的主线计划见 `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`。

2026-07-03 执行更新：

- 已按 `docs/superpowers/plans/2026-07-02-public-audio-and-synthetic-meeting-asr-validation.md` 实现 M4/M5 的 no-execute 自测骨架。
- 新增公开视频 bounded sample extraction plan 工具，但仍不下载公开音频。
- 新增 5 个中文合成技术会议脚本和脚本 coverage report，覆盖 API、release、incident、architecture、non-engineering control。
- 新增本地合成音频 generation plan 和 ASR event generation plan，默认仍不调用远程 ASR/LLM。
- 已完成一次本地 synthetic audio smoke：用 macOS `say` + `afconvert` 为 `api-review-001` 生成 16kHz mono wav，输出在 ignored `artifacts/tmp/synthetic_audio/`。
- 已完成一次 sherpa-onnx 本地 ASR smoke：duration 约 16.83s，latency 516ms，RTF 0.030667，partial 25，final 1，end_of_stream 1。
- sherpa smoke 的质量结论是未达标：raw 技术实体 recall 0.0，normalized recall 0.25，只恢复 `P99`，`payment-gateway`、`request_id`、`40012` 未恢复。
- 已新增完整主计划 `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`，明确我负责公开授权音频/合成音频前置自测，用户最终做真实麦克风会议验证。
- 已在 RTM 中补齐 DRV-009 到 DRV-013：Mac 麦克风采集、本地 ASR worker、无 LLM 状态/缺口候选、受控 LLM 建议卡、用户真实麦克风会议验收。
- 已新增 `tools/funasr_synthetic_smoke_readiness.py` 和测试，要求 FunASR runtime cache 缺失时 blocked，legacy/hub cache 不能误判为可执行；cache preflight 通过也不代表离线执行已被证明。
- 已尝试一次显式本地 FunASR synthetic smoke，但 ModelScope 因 runtime cache 缺失开始下载约 840MB online 模型；我已中断执行并清理本轮误触发的 runtime cache，不把它视为成功自测。
- 2026-07-03 后续执行更新：`transcribe_funasr.py --streaming` 已建立 FunASR 离线执行 guard，必须显式传入 `--local-model-dir`，缺失或目录不完整时返回 blocked 且不构造 `AutoModel`；readiness command preview 也已加入 `--local-model-dir` 占位符。
- 当前主线下一步是：在本地模型目录/cache 缺失时形成模型下载审批决策，或先推进 synthetic audio smoke batch、hotword/normalizer 和公开授权会议音频 bounded sample review；不是继续扩展 no-op readiness，也不是直接真实麦克风会议。

## 2. 当前阶段判断

当前项目处于：

```text
PC Web MVP 已形成可测骨架
Mac-first desktop shell 正在进入可运行前夜
真实音频采集、真实 ASR worker、真实 LLM enabled executor 尚未开启
```

换句话说，项目现在已经不是“只做文档”，也不是“只有 ASR 评测”。已有可运行的本地 Web/API、可测的 core、可消费的 synthetic Live ASR event、可展示的状态和建议链路，以及 Tauri 桌面壳源码 scaffold。

但项目也还没有到“可用客户端”的阶段。真实麦克风/系统音频、真实 ASR worker、真实 LLM 调用、安装包、签名、公证、跨平台发布都还没有实现或默认启用。

## 3. 已经完成什么

### 3.1 产品和需求层

已经固定的核心判断：

- 产品不是录音转文字工具，也不是普通会后总结工具。
- 产品价值在中文技术会议中实时/准实时维护会议状态，并提醒工程讨论缺口。
- 核心缺口包括 owner、deadline、rollback、test/verification、metric/monitoring、risk、open question。
- 所有正式状态、建议和纪要必须 EvidenceSpan-backed。
- 非工程会议不得输出工程建议卡片。
- ASR 默认本地/开源，远程 ASR 只作为显式可选或质量对照。
- 默认远程成本只允许来自 OpenAI-compatible LLM 中转站。
- PC 客户端采用共享 core/UI + 分平台 adapter + 分平台打包，不做 Windows/Mac 两套业务代码。

关键文档已存在：

- `docs/product-requirements.md`
- `docs/implementation-roadmap.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/privacy-and-data-flow.md`
- `docs/platform-packaging-and-store-compliance.md`
- `docs/project-structure.md`
- `docs/project-progress-report-2026-07-02.md`
- `docs/project-current-status-2026-07-02.md`

### 3.2 ASR 和评测层

已完成：

- 中文技术会议 ASR 评测集目录、manifest、reference、annotation、glossary。
- `mock` provider 和 `command` provider。
- 中文 CER、技术实体召回、延迟等指标。
- FunASR、sherpa-onnx 的本地文件链路和 streaming contract 验证。
- `partial/final/revision/error/end_of_stream` 风格的 streaming transcript contract。
- scheduler 规则已证明 `partial` 不触发 LLM，只有 `final/revision` 或状态变化可进入调度。
- 公开视频 source whitelist/report。
- 公开视频 bounded sample extraction plan/report。
- 合成中文技术会议脚本 gate，包含 expected state/gap/card annotations 和 transcript-only / summary-only baseline expectations。
- 本地合成音频 generation plan/report。
- public/synthetic ASR event generation plan/report。

当前结论：

- FunASR/Paraformer 是中文质量主候选。
- sherpa-onnx 是轻量端侧备选和性能基线。
- 远程 ASR 不进入默认 MVP，只在本地 ASR 不达标时作为对照或显式高质量模式。
- ASR bake-off 后续只作为 targeted gate，不再作为主线无限扩展。

尚未证明：

- 公开音频真实样本下载、抽取、转码和本地 ASR 结果。
- 合成会议脚本已完成一次真实本地 TTS 音频生成和 sherpa 本地 ASR smoke，但质量未达 first-pilot threshold；FunASR 和技术词修正仍未证明。
- FunASR runtime cache/本地模型目录就绪状态尚未证明；legacy/hub cache 存在不足以避免模型下载，执行器已强制 `--local-model-dir` guard。
- 真实 Mac 麦克风/系统音频进入 ASR worker 的端到端延迟。
- 真实多人中文技术会议下的技术词准确率。
- 长会议下的 CPU、内存、模型缓存、清理和稳定性。

### 3.3 Core 层

已完成：

- 平台无关 core。
- `TranscriptSegment`、`EvidenceSpan`、`SuggestionCard`、`StateEvent` 等合同。
- meeting snapshot 聚合。
- 工程语境 gate。
- 证据链 gate。
- 非工程会议工程卡片为 0 的 gate。
- schema failure、timeout、invalid response 的降级/静默路径。
- 建议卡片 `kept/dismissed/marked_wrong/too_late/too_intrusive` 等基础状态。

意义：

- 这部分是产品不退化成“转写 + 总结”的核心防线。
- Web MVP、Tauri、Windows adapter、未来移动端都应复用这一层。

### 3.4 PC Local Web MVP

已完成：

- FastAPI 本地 backend。
- 静态 Web 工作台。
- fixture session 创建、读取、删除。
- replay event stream。
- Live Mock SSE。
- Live ASR synthetic SSE。
- EventSource 增量 UI。
- Evidence click-back。
- 本地 JSON session persistence。
- no-LLM scheduler decision log。
- no-LLM suggestion candidate queue。
- no-LLM LLM request draft queue。
- OpenAI-compatible request body preview。
- request body redaction guard。
- provider config boundary、validation、masked status、loader preflight 等不读密钥边界。
- card creation policy dry-run。
- card lifecycle preview、append、retry/replay、serializer、mutation、commit、idempotency、audit persistence、readiness summary 等 dry-run/preflight 链路。

当前能证明：

- Web/API/UI 可以消费受控 live envelope。
- transcript、EvidenceSpan、状态候选、scheduler trace、候选建议、request draft 和 readiness panels 可以被展示。
- 默认质量门禁不读取 `configs/local/`，不调用远程 ASR/LLM。

当前不能证明：

- 真实桌面音频采集。
- 真实 ASR worker 输入输出。
- 真实 LLM enabled executor。
- 正式建议卡片在真实会议中的保留率、打扰率和有效性。

### 3.5 桌面端路径

已完成的桌面增量：

```text
PCWEB-079  Desktop shell readiness boundary
PCWEB-080  Desktop runtime boundary
PCWEB-081  Native bridge command contract
PCWEB-082  Tauri v2 static scaffold
PCWEB-083  Desktop build readiness policy
PCWEB-084  Cargo check artifact policy
PCWEB-085  Rust toolchain readiness policy
PCWEB-086  Rust toolchain installation decision policy
PCWEB-087  Rust toolchain install approval packet
PCWEB-088  Rust post-install probe approval packet
PCWEB-089  Rust post-install probe result intake
PCWEB-090  First cargo check execution boundary
```

桌面端当前事实：

- `code/desktop_tauri/src-tauri/` 已存在静态 Tauri scaffold。
- 已有 no-op bridge commands：`runtime_get_status`、`session_prepare`、`asr_worker_health`。
- `PCWEB-090` 已把 PCWEB-084 的 cargo check artifact policy 和 PCWEB-089 的 bounded toolchain result 合并成手动执行包边界。
- 当前没有运行 Tauri。
- 当前没有运行 `cargo check`。
- 当前没有生成 `Cargo.lock`、target、installer、bundle、签名或公证产物。
- 当前没有麦克风/系统音频采集、权限请求、ASR worker、provider config、密钥或远程调用。

## 4. 最新验证状态

PCWEB-090 的最新验证结果：

```text
PCWEB-090 RED: 9 failed, 1 warning
PCWEB-090 focused GREEN: 11 passed, 1 warning
PCWEB-090 + PCWEB-089 + PCWEB-084 + quality gate tests: 37 passed, 1 warning
docs gate: 1 passed, 2 warnings
pc-web quality gate: root 83 passed, core 34 passed, web backend 300 passed, browser smoke status ok
all-local --no-browser: ASR runtime 65 passed, ASR bake-off 18 passed, root 83 passed, core 34 passed, web backend 300 passed
```

PCWEB-090 评审后已补强：

- 不再透传 PCWEB-089 delegated validation error 中的不可信 unknown field 名，避免本机路径或 bearer-like 字符串泄漏到报告。
- path guard tests 已覆盖 `configs/local`、`data/local_runtime`、`outputs`、`artifacts/tmp`、`data/asr_eval/samples` 五类 forbidden roots。
- path guard tests 已覆盖 `policy_path`、`artifact_policy_path`、`probe_result_intake_policy_path`、`probe_result_path` 四个输入。

当前质量状态可以这样理解：

```text
PCWEB-090 已通过 focused 和相邻回归
PC Web MVP gate 当前为 green
all-local no-browser gate 当前为 green
桌面端仍处于 no-command/no-audio/no-secret/no-remote 边界内
```

## 5. 当前还没有做什么

这些内容不要误认为已经完成：

- 没有真实运行 Tauri shell。
- 没有运行 `cargo check`、`cargo build`、`cargo tauri dev`、`cargo tauri build`。
- 没有安装 Rust。
- 没有生成 `Cargo.lock` 或 Cargo target。
- 没有请求麦克风或系统音频权限。
- 没有真实麦克风/系统音频采集。
- 没有启动 ASR worker。
- 没有默认接入远程 ASR。
- 没有真实 LLM enabled executor。
- 没有读取 `configs/local/`。
- 没有读取真实用户音频。
- 没有生成安装包、签名、公证、App Store 或移动端产物。

## 6. 目前最大的风险

### 6.1 产品风险

- 真实中文技术会议 ASR 准确率和实时性仍未通过桌面链路证明。
- 如果 audio -> ASR final/revision -> state -> suggestion 的端到端延迟过高，产品会退化为会后工具。
- 如果建议卡片过多、误报多、时机晚，用户会觉得打扰。
- 如果只继续做 fixture/synthetic UI，不接真实音频链路，产品价值无法被证明。

### 6.2 工程风险

- Tauri/Rust 还没有真正运行过。
- macOS 系统音频采集涉及权限、系统版本、会议软件和音频路由差异。
- 本地 ASR 模型体积、缓存、首次下载、CPU/内存和清理策略仍需产品级约束。
- LLM enabled executor 需要严格控制频率、schema、重试、成本和密钥边界。

### 6.3 开发节奏风险

已经有较多 dry-run、preflight、policy 边界。它们对安全、密钥、费用和本地文件保护是必要的，但继续横向扩展会让项目看起来一直在准备、没有推进真实客户端能力。

后续增量应满足一个硬标准：**让真实链路向前一步。**

## 7. 后续还在做什么

### 7.1 已完成：PCWEB-091

PCWEB-091 已完成为：

```text
PCWEB-091 Tauri no-op shell local run smoke
```

目标：

- 验证 Tauri no-op shell 的本地运行边界。
- 验证 shell 是否能加载现有 Web MVP 地址 `http://127.0.0.1:8765/`。
- 验证 no-op IPC command catalog 是否仍只包含 `runtime_get_status`、`session_prepare`、`asr_worker_health`。
- 继续不接音频、不启动 ASR worker、不读取 provider config、不读取密钥、不调用远程 provider。

PCWEB-091 当前只是 `readiness_report_only`，不直接运行 `cargo` 或 Tauri 命令。若后续明确批准真实运行，再另起增量执行真实 smoke。

### 7.2 修订后的立即下一步：desktop runtime validation

目标：

- 跨过第一个受控执行边界，证明 Mac 桌面壳可以真实构建和运行。
- 让 Web UI 在 Tauri shell 内真实调用 native IPC，而不是只展示 FastAPI readiness。
- 在真实麦克风会议前，用公开授权中文会议音频和自建中文技术会议合成音频验证 ASR、切句、技术实体、事件链路和 gap candidate。

立即顺序：

1. M1 Desktop Build Evidence：受控运行 `cargo check`，允许 `Cargo.lock`，target 固定到 `artifacts/tmp/desktop_tauri_target`。
2. M2 Real Tauri No-op Run：真实打开 Mac Tauri 窗口并加载本地 Web MVP。
3. M3 Frontend-to-Tauri IPC：前端调用 `runtime_get_status`、`session_prepare`、`asr_worker_health`。
4. M4 Public Audio ASR Simulation：基于 AISHELL-4、AliMeeting 或 AISHELL-1 的公开授权小样本验证 ASR 管线。
5. M5 Synthetic Technical Meeting Simulation：用自建中文技术会议脚本和合成音频验证产品差异化。

PCWEB-092 native bridge no-op integration 可被吸收进 M3，不再单独扩成新的 readiness 阶段。

2026-07-02 执行更新：

- M1 已尝试受控 toolchain probe：`rustc`、`cargo`、`rustup` 均缺失，`xcode-select -p` 可用且路径已脱敏为 presence-only；因此当前不能运行 `cargo check`，也未安装 Rust、未生成 `Cargo.lock` 或 target。
- M3 已新增 Web 工作台 Native Runtime 面板 `desktop-native-runtime-panel`。普通浏览器显示 `browser_fallback/not_available`；Tauri 环境会调用 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个 no-op IPC command。真实 Tauri WebView 内验证仍等待 M1/M2。
- M4 已新增 `data/asr_eval/public_sources.json` 与 `tools/public_audio_source_whitelist.py`。当前只生成公开音频白名单报告，`download_status=not_started`，不下载公开音频、不读取真实用户音频、不读取 `configs/local`。

### 7.3 PCWEB-093+：Mac audio capture adapter contract

目标：

- 设计并实现 macOS 音频采集 adapter 边界。
- 先从麦克风权限、设备枚举、手动开始/暂停/停止、chunk contract 做起。
- 系统音频采集作为单独 spike，不和麦克风混在一个风险包里。

边界：

- 不隐蔽录音。
- 不自动开始录音。
- 真实音频只进入本地 runtime/output 受控目录，不进入仓库。
- 必须有删除和清理策略。

### 7.4 PCWEB-094+：ASR worker sidecar integration

目标：

- 让本地 audio chunk 输入 ASR worker。
- 输出 `partial/final/revision/error/end_of_stream`。
- 将 `final/revision` 转成 Web MVP 可消费的 Live ASR event。
- 用真实链路计量延迟、准确率、CPU、内存。

### 7.5 之后：LLM enabled executor

真实 LLM enabled executor 不应该先于真实 audio -> ASR -> state/candidate 链路开启。

建议前置条件：

- 真实 ASR event 能稳定产生状态候选。
- request draft、schema outline、card creation policy、card lifecycle dry-run 均保持 green。
- provider config loader 有明确授权。
- secret reference 不回显、不 fingerprint、不推断 key presence。
- 每次 LLM 请求记录 trigger reason、segment batch、prompt version、model、usage、schema result 和 show/silence decision。
- 有 cooldown、预算和失败降级。

## 8. 暂缓事项

为了避免项目再次发散，以下暂不作为近期主线：

- 不继续扩大远程 ASR provider 横评。
- 不默认接入阿里、讯飞、腾讯、百度等付费 ASR。
- 不先做 Windows 音频采集。
- 不先做 iOS/Android 上架实现。
- 不直接启用真实 LLM 付费调用。
- 不 fork 大型会议转写项目作为主仓库，除非它明确降低桌面音频/ASR worker 成本且不破坏 core-first 架构。
- 不继续新增大量 fixture-only UI 能力，除非它阻塞真实桌面链路。

## 9. 当前推荐决策

继续推进项目，但收窄路线：

```text
不再扩泛评测
不再横向堆 dry-run
PCWEB-091 桌面 no-op shell readiness 已完成
下一步推进 no-op IPC
再推进 Mac audio capture
再推进 ASR worker
最后开启真实 LLM enabled executor
```

项目仍值得继续做，因为目前已经具备普通转写工具没有的核心骨架：

- EvidenceSpan 证据链。
- 工程语境 gate。
- 会议状态模型。
- no-LLM scheduler。
- suggestion candidate 和 request draft。
- card lifecycle 边界。
- provider/secret/cost 安全边界。
- PC Web MVP 可测工作台。
- Tauri 桌面壳 scaffold。

但下一阶段必须尽快靠近真实桌面链路。否则继续做文档、评测或 dry-run 会降低项目有效进展。

## 10. 当前开发入口

当前最推荐的下一步开发入口：

```text
PCWEB-092 desktop native bridge no-op integration
```

PCWEB-091 已交付：

- `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`
- `tools/desktop_tauri_noop_shell_run_smoke.py`
- `tests/test_desktop_tauri_noop_shell_run_smoke.py`
- `docs/pcweb-091-tauri-noop-shell-local-run-smoke-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-091-tauri-noop-shell-local-run-smoke.md`

PCWEB-091 已验收：

- RED tests 先失败，原因是 policy/tool 不存在。
- focused tests green。
- 桌面相邻边界回归 green。
- docs gate green。
- `pc-web` gate green。
- `all-local --no-browser` gate green。
- 评审后补强 package-manager artifact blockers、function-to-command mapping 和 no-side-effect drift checks。

仍然保持：

- 不运行 Cargo/Tauri/package manager，除非用户另起显式授权。
- 不读取 `configs/local/`。
- 不读取真实音频。
- 不调用远程 provider。
- 不生成非预期 artifacts。

## 11. 可追溯文件

本报告对应的主要文件：

- `README.md`
- `code/desktop_tauri/README.md`
- `docs/project-progress-report-2026-07-02.md`
- `docs/project-current-status-2026-07-02.md`
- `docs/implementation-roadmap.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/privacy-and-data-flow.md`
- `docs/project-structure.md`
- `docs/pc-local-web-mvp-acceptance.md`
- `docs/pcweb-090-desktop-first-cargo-check-execution-boundary-plan.md`
- `docs/superpowers/plans/2026-07-02-pcweb-090-desktop-first-cargo-check-execution-boundary.md`
- `tests/test_desktop_first_cargo_check_execution_boundary.py`
- `tools/desktop_first_cargo_check_execution_boundary.py`
- `code/desktop_tauri/first-cargo-check-execution.policy.json`

## 12. 下一次汇报应回答的问题

下一次阶段汇报不应再重复泛泛说明“为什么做这个项目”，而应回答：

- PCWEB-091 是否已经完成？
- Tauri no-op shell 是否已经进入真实运行或仍停留在 readiness/report boundary？
- 是否已经获得运行 Cargo/Tauri 的明确授权？
- 是否已经生成 `Cargo.lock` 或 target？如果有，是否符合 PCWEB-084/090 artifact policy？
- no-op IPC 是否已被真实 shell 调用？
- 下一步是否可以进入 Mac audio capture adapter？
- 真实链路是否向 `audio chunk -> ASR final/revision -> state/candidate -> card` 前进了一步？

## 13. PCWEB-091 执行更新

`PCWEB-091 Tauri no-op shell local run smoke` 已完成为 no-command readiness boundary：新增 `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json` 和 `tools/desktop_tauri_noop_shell_run_smoke.py`。该阶段仍是 `readiness_report_only`，只静态验证 PCWEB-082 Tauri scaffold、`devUrl=http://127.0.0.1:8765/`、`frontendDist`、minimal capability、exact no-op command catalog、generated artifact blockers 和 PCWEB-090 no-command boundary。

当前 PCWEB-091 不是 Tauri 真实运行。valid scaffold 最多返回 `ready_for_explicit_tauri_run_approval`，并保持 `safe_to_run_tauri_dev_now=false`、`safe_to_run_cargo_check_now=false` 和 `safe_to_capture_audio_now=false`。评审后已补强 package-manager artifact blockers、function-to-command mapping 和 no-side-effect drift checks。多 Agent 复盘后，`PCWEB-092` 不再作为单独 no-op readiness 主线；其有效部分已吸收到 M3 Frontend-to-Tauri IPC。

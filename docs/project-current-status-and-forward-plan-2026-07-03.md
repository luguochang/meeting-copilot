# Project Current Status and Forward Plan

> 日期：2026-07-03  
> 状态：Accepted as current execution report  
> 范围：中文技术会议实时 Copilot 的当前进展、转写验证计划、公开音频模拟边界、下一阶段开发计划。  
> 边界：本文档不授权下载公开音频大包、不授权读取真实用户音频、不授权访问麦克风、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权自动下载 FunASR/ModelScope 模型。

## 1. 结论先行

完整计划已经写下，并且主线没有忘：

```text
合成中文技术会议
  -> 本地 ASR partial/final/revision
  -> 技术实体和切句质量
  -> EvidenceSpan
  -> meeting state
  -> engineering gap candidate
  -> suggestion card candidate
  -> desktop runtime
  -> 用户最终真实麦克风会议验证
```

公开授权会议音频不是产品价值证明，而是补真实会议声学、多说话人、远场、重叠和切句风险。真实麦克风会议最终由用户验证。

2026-07-03 计划确认：完整计划已经写下；我负责先完成公开授权音频来源审查、合成音频模拟、本地 ASR 自测和指标报告，用户最终执行真实麦克风会议 shadow test。下一阶段不继续泛化评测，默认收敛到 ASR 质量决策、公开音频 no-download sample manifest 和桌面 worker/process contract。

当前不应继续无限评测。后续评测只服务于三个问题：

- 中文技术实体能不能被 ASR 保留下来。
- final/revision 能不能及时产生 EvidenceSpan 和 gap candidate。
- 桌面端能不能真实采集音频并把事件送进同一条 Copilot 链路。

2026-07-03 最新主线执行计划已落档到 `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md`。Copilot product value tri-lane gate 和 5 场景 batch gate 已完成：perfect/mock lane 已证明产品智能链路基础可用，当前下一步不再重复该 gate，而是转向 ASR 质量受控路径、公开音频 no-download sample manifest 和桌面端 no-execution/no-op 实现前置。

## 2. 已完成内容

### 2.1 产品与架构

- 产品定位已固定为中文技术会议实时 Copilot，不是普通转写工具，也不是会后总结工具。
- 核心价值边界已固定为：会议中及时发现 owner、deadline、rollback、test、metric、monitoring、risk、open question 等工程讨论缺口。
- 核心智能层自研，底层复用开源 ASR/音频/LLM 协议能力，客户端 Mac-first。
- 默认本地 ASR；远程 ASR 只作为显式可选对照，不进入默认 MVP。
- LLM 中转站是唯一默认远程模型成本方向，但转写验证阶段默认不调用。

### 2.2 合成中文技术会议验证

- 已建立 5 个合成脚本：
  - `api-review-001`
  - `release-review-001`
  - `incident-review-001`
  - `architecture-review-001`
  - `non-engineering-control-001`
- 已用 macOS 本机 `say` + `afconvert` 生成 5 个 16kHz mono wav，输出在 ignored `artifacts/tmp/synthetic_audio/`。
- 已用本地 sherpa 跑完 baseline，输出 events/provider/transcript/smoke reports 到 ignored `artifacts/tmp/`。
- sherpa 速度稳定，RTF 约 0.029-0.035。
- sherpa 技术实体质量未达标，4 个工程脚本 normalized entity recall 仅 0-0.5。
- 已新增 synthetic product value gate，把 ASR smoke 结果和脚本 expected cards/gaps 合并判断：4 个工程脚本均为 `needs_asr_quality_work`，非工程 control 为 `negative_control_passed`。
- 已新增 ASR Live Pipeline Replay gate，把 `artifacts/tmp/asr_events/*.events.json` 接入 Web Live ASR pipeline，验证 final -> EvidenceSpan -> state/scheduler/suggestion candidate/request draft 的无 LLM 链路。
- replay gate 暴露并修复了非工程误触发：普通会议里的 “是否方便” 和 “名单整理明天发群” 不再触发工程 OpenQuestion/ActionItem candidate；真实 `non-engineering-control-001` sherpa events 现在为 0 state / 0 scheduler / 0 candidate。
- 已新增本地 ASR event file handoff API：`POST /live/asr/local-event-files/sessions` 只允许从 `artifacts/tmp/asr_events` 读取 ASR event JSON 并创建 Live ASR session；创建后可用现有 `/live/asr/sessions/{id}/events` 和 `/events.sse` 消费。这是未来桌面 ASR worker handoff 的本地 API 前置入口，当前仍不读真实音频、不访问麦克风、不跑模型、不调用远程 ASR/LLM。
- 已加固本地 ASR event file handoff API：坏 JSON/非 list/非 object/缺失文件进入 `blocked_by_invalid_events_file`，未知事件/缺 segment/空 final 或 revision/非法时间戳或 confidence/revision 缺 `revision_of` 进入 `blocked_by_event_contract`，重复 `session_id` 进入 `blocked_by_duplicate_session` 且不覆盖已有事件；repo 内 allowed 绝对路径可读，repo 外绝对路径 redacted；data_dir 模式可跨 app 实例读回。
- 已新增 desktop-side ASR worker handoff preflight：`code/desktop_tauri/asr-worker-handoff-preflight.policy.json` 和 `tools/desktop_asr_worker_handoff_preflight.py` 只校验 future worker descriptor、event file path 和 chunk lifecycle，并生成 Web handoff request preview；当前只允许 `preflight_only|synthetic`，拒绝 `mic|file`，仍不启动 worker、不访问麦克风、不读写 event file、不写 runtime audio、不调用 Web API/远程 ASR/LLM。
- 已新增 PCWEB-096 desktop ASR worker handoff local dry-run bridge：`preview_only` 只生成 Web handoff preview，`synthetic_local_test` 只允许 synthetic event file + 临时 Web data dir 调用 `/live/asr/local-event-files/sessions`，验证 worker descriptor 到 Web Live ASR session 的本地合同；仍不启动 worker、不访问麦克风、不读真实音频、不调用远程 ASR/LLM、不下载模型。
- 已新增 Copilot product value tri-lane gate：`tools/copilot_product_value_tri_lane_gate.py` 对同一 synthetic scenario 跑 `perfect_transcript`、`mock_asr`、`real_asr` 三路，统一复用 Web Live ASR builder 输出 EvidenceSpan/state/scheduler/suggestion candidate/request draft，并把失败归因为 `blocked_by_product_logic`、`blocked_by_stream_contract` 或 `blocked_by_asr_quality`。当前 `api-review-001` smoke 显示 perfect/mock lane 为 `product_logic_ready`，real ASR lane 因 sherpa normalized technical entity recall 0.5 返回 `blocked_by_asr_quality`。
- 已新增 Copilot product value batch gate：`tools/copilot_product_value_batch_gate.py` 把 tri-lane gate 扩到 5 个 synthetic scripts。DRV-027 后，当前 batch smoke 结果已推进为 `overall_decision=blocked_by_asr_quality`：5 个场景 perfect/mock lane 均 ready，4 个工程场景 real ASR 仍因 sherpa 技术实体 recall 不达标阻断，`non-engineering-control` 三路通过且 candidate=0。
- 已新增 DRV-027 architecture review product logic coverage：Live ASR 本地抽取现在能从 `QPS/缓存穿透/mysql` 识别架构风险，并从 `压测 owner 还没安排` 识别未闭环问题；`architecture-review-001` perfect/mock lane 已转 ready，`incident-review-001` mock lane 也转 ready。
- 结论：产品逻辑 perfect/mock gate 已过线；sherpa 只能作为性能基线，不作为中文技术会议质量主线。

### 2.3 FunASR guard

- FunASR streaming 执行器已增加 offline guard：
  - `--streaming` 必须显式传入 `--local-model-dir`。
  - 目录必须是绝对路径，且包含 `model.pt` 和 `config.yaml`。
  - 缺失或目录不完整时返回 `status=blocked`。
  - blocked 路径不构造可能自动下载模型的 `AutoModel(model=<alias>)`。
- 当前真实 FunASR smoke 仍 blocked，因为本地 runtime model dir/cache 缺失。
- DRV-019 已形成模型下载审批包：
  - `code/asr_runtime/funasr-model-download-approval.policy.json`
  - `tools/funasr_model_download_approval_packet.py`
  - `tests/test_funasr_model_download_approval_packet.py`
- 该审批包只生成 `manual_user_run_only` 静态报告，记录 ModelScope/iic model id、约 840MB 磁盘风险、手动下载说明、清理策略和 post-download 验证顺序；当前仍不下载模型、不执行命令、不运行 FunASR smoke。

### 2.4 公开音频计划

已复核的公开来源：

| 优先级 | 来源 | 授权 | 用途 | 当前边界 |
| --- | --- | --- | --- | --- |
| 1 | AliMeeting / OpenSLR SLR119 | CC BY-SA 4.0 | 会议实时转写模拟主候选，含 near-field / far-field 对照 | `Eval_Ali.tar.gz` 约 3.42G，不自动下载 |
| 2 | AISHELL-4 / OpenSLR SLR111 | CC BY-SA 4.0 | 复杂会议声学、多人、远场、重叠说话补充 | `test.tar.gz` 约 5.2G，不自动下载 |
| 3 | AISHELL-1 / OpenSLR SLR33 | Apache License v2.0 | 普通话 ASR/runtime sanity check | 非会议，不证明产品价值 |

2026-07-03 二次公开音频检索结论：MagicHub Web Meeting 小体量候选、MagicData-RAMC 和 Common Voice zh-CN 只记录为 observed-but-not-whitelisted；MagicHub/MagicData 受 CC BY-NC-ND 4.0 约束，Common Voice 虽为 CC0 但不是会议且包体大。WenetSpeech 因依赖 YouTube/podcast 平台音频版权链而明确排除。默认白名单仍只包含 AliMeeting、AISHELL-4 和 AISHELL-1。

明确不采用 Bilibili、YouTube、播客、公开视频、公开课、技术大会录播等版权链不清来源。

`public_audio_sample_extraction_plan` 已升级为 planned sample schema 校验：

- 支持函数入参 `planned_samples`。
- 支持 CLI `--planned-samples-file`。
- 必填：`sample_id`、`source_id`、`source_url`、`source_license`、`archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_duration_seconds`、`expected_sha256_after_extract`、`license_citation`、`cleanup_required`。
- 绝对路径、`..` 路径、超预算、非 sha256、缺 license、`cleanup_required=false` 都会 blocked。
- 校验通过只表示 `schema_validated_no_download`，仍不下载、不解压、不转码。
- 没有具体 planned samples 时返回 `blocked_no_planned_samples`，不再显示为可下载评审 ready。
- `--source-path` 和 `--planned-samples-file` 现在会在读文件前拒绝 forbidden roots、仓库外绝对路径和 symlink 逃逸。

## 3. 当前最大风险

### 3.1 ASR 质量风险

当前最大风险不是速度，而是中文技术实体保留：

- `payment-gateway`
- `request_id`
- `40012`
- `feature-store`
- `recommendation-service`
- `redis cluster`
- `order-worker`
- `error_rate`
- `staging`

这些实体如果丢失，EvidenceSpan 就没有可靠证据，后续 LLM 只能猜，产品会退化成普通转写或幻觉建议。

2026-07-03 的 product value gate 给出的阶段判断：

| 脚本 | product value decision | 结论 |
| --- | --- | --- |
| `api-review-001` | `needs_asr_quality_work` | 事件链路完整，但实体召回 0.5，低于 0.8 |
| `architecture-review-001` | `needs_asr_quality_work` | 事件链路完整，但实体召回 0.0 |
| `incident-review-001` | `needs_asr_quality_work` | 事件链路完整，但实体召回 0.0 |
| `release-review-001` | `needs_asr_quality_work` | 事件链路完整，但实体召回 0.25 |
| `non-engineering-control-001` | `negative_control_passed` | 非工程 control 仍为 0 工程卡负控 |

因此，当前不能进入真实麦克风 pilot；desktop runtime 可以做 no-op/IPC/worker contract，但不能宣称强实时建议链路质量已经达标。

### 3.2 模型获取风险

FunASR 很可能是中文质量主候选，但当前本地模型目录缺失。不能静默下载 840MB 级模型，也不能把 legacy/hub cache 误当成可离线执行证据。当前风险已经收束为 DRV-019 “Need model approval” 决策点：只有用户明确批准模型下载，或提供已就绪的本地模型目录，才允许进入 post-download verification order。

### 3.3 桌面 runtime 风险

Tauri scaffold 已有，但本机 Rust/Cargo 缺失，真实桌面窗口和 native IPC 还没有跑通。麦克风 adapter 不能在 desktop runtime 之前启动。

## 4. 未来计划

### Phase 1: 收口 ASR 输入层

目标：让文件回放式实时事件链稳定输出 `partial/final/revision/error/end_of_stream`，并能形成 EvidenceSpan。但 Phase 1 不能再单独作为下一主线，必须和 Phase 1A 的产品价值 gate 并行。

### Phase 1A: Copilot product value tri-lane gate

目标：在 ASR 质量完全达标前，先证明 Copilot brain 本身有价值。

动作（产品价值 gate）：

1. 同一 synthetic scenario 同时跑 perfect transcript、mock ASR 和 real ASR lane。
2. 每个 lane 输出 expected gap、detected gap、candidate/card、EvidenceSpan、latency window 和非工程误报。
3. 如果 perfect transcript lane 失败，停止继续 ASR/provider 横评，先修产品逻辑。
4. 如果 perfect transcript lane 通过但 real ASR lane 失败，才继续 ASR/hotword/normalizer/FunASR。

退出条件：

- perfect transcript lane 至少通过 2 个工程脚本和 non-engineering control。
- 每个 candidate/card 都能追溯 EvidenceSpan。
- 非工程 control 0 candidate。
- 当前 5 场景 batch summary 已完成；DRV-027 后 perfect/mock lane 已稳定，下一步应进入 ASR 质量受控路径：FunASR 本地模型目录或 DRV-019 模型审批、bounded normalizer/hotword，或明确记录 ASR 阻塞/降级路径。

动作（ASR 输入层）：

1. 保留 sherpa 作为性能 baseline，不继续在 sherpa 上扩大质量调参。
2. 如果本地 FunASR 模型目录就绪，运行 FunASR synthetic smoke。
3. 如果模型目录不就绪，使用 DRV-019 模型下载审批包说明体积、缓存目录、清理策略和收益；审批包本身不下载模型。
4. 继续完善 deterministic normalizer，但只恢复 ASR 文本中已有线索，不从 `<unk>` 猜实体。

退出条件：

- 至少一个技术会议脚本 normalized entity recall 接近 0.8。
- final/revision 能稳定生成 EvidenceSpan。
- non-engineering control 不产生工程 state/candidate/card。

### Phase 2: 公开音频计划只做小样本治理

目标：为后续合法公开会议声学验证建立可审计计划，不下载大包。

动作：

1. 先运行 whitelist gate。
2. 优先 AliMeeting Eval，其次 AISHELL-4 test。
3. 用 `--planned-samples-file` 校验 3-5 个样本计划。
4. 如果必须下载 GB 级整包才能抽样，记录 blocked，不继续找版权不清替代来源，也不把 observed-but-not-whitelisted 候选当成白名单来源绕过。

退出条件：

- 得到 schema-valid 的 no-download sample plan，或明确 blocked 原因。

### Phase 3: 桌面 runtime

目标：把当前 Web/Core/ASR 事件链推进到 Mac 本地桌面壳。

动作：

1. PCWEB-098/099/100/101/102/103/104 已完成：worker process contract、command protocol、synthetic lifecycle harness、implementation approval packet、no-execution sidecar skeleton、command runner binding preview 和 inert command runner implementation skeleton/no-dispatch 均已落地；这些阶段仍不启动真实 worker、不访问麦克风、不读写 event file、不读真实音频、不调用远程 ASR/LLM、不运行 Cargo/Tauri。
2. 下一步只能在明确审批边界下选择真实 Tauri no-op run、公开音频 planned samples、ASR quality decision/FunASR 本地模型目录或 DRV-019 审批、或 mic adapter contract，并按 SDD/TDD 另起计划；不再把 PCWEB-104 当未来任务。
3. 后续在审批边界明确后，受控确认 Rust/Cargo 工具链。
4. 受控运行 `cargo check`，target 输出到 `artifacts/tmp/desktop_tauri_target`。
5. 运行 Tauri no-op window，并验证 Web UI 在 Tauri WebView 中调用 native IPC。

退出条件：

- Tauri 窗口能启动。
- `runtime_get_status`、`session_prepare`、`asr_worker_health` no-op command 在 WebView 内可调用。
- 本地 ASR event file handoff API 和 PCWEB-096 dry-run 可作为 worker 输出到 Web Live ASR 的临时桥；不采集音频，不读取密钥，不调用远程 provider。

### Phase 4: Mac microphone adapter

目标：用户手动启动真实本机音频采集，但先不进入真实会议。

动作：

1. 设计 input device、start/pause/resume/stop、level meter、duration、chunk count。
2. chunk 写入 ignored 本地 runtime 目录。
3. 提供一键删除。
4. 先用本地短音频和模拟输入验证 adapter contract。

退出条件：

- 用户点击后才采集。
- stop/delete 可用。
- 不默认上传，不默认远程 ASR。

### Phase 5: Controlled LLM suggestion cards

目标：在 ASR final/revision、EvidenceSpan 和 gap candidate 稳定后，低频生成建议卡片。

动作：

1. 只发送 EvidenceSpan 和结构化状态摘要。
2. 继续使用 OpenAI-compatible 中转站。
3. 限制 token、频率、cooldown、schema 和重试。
4. 每张卡支持 useful/wrong/too_late/too_intrusive/dismissed 反馈。

退出条件：

- 非工程 control 0 工程卡。
- 卡片能追溯到 EvidenceSpan。
- 每 60 分钟 3-8 张有效卡，而不是刷屏。

### Phase 6: 用户真实麦克风会议验证

目标：由用户最终验证真实产品价值。

进入条件：

- 桌面 app 可运行。
- 麦克风 adapter 可控。
- 本地 ASR worker 可输出统一事件。
- UI 可展示 transcript、EvidenceSpan、state/gap 和建议候选。

验收：

- first pilot：1 场 20-30 分钟中文技术会议 shadow test。
- Go evidence：至少 2 个真实中文技术会议场景。
- 导出 transcript、ASR metrics、state timeline、card timeline 和用户反馈。

## 5. 停止条件

如果出现以下任一情况，应停止继续扩评测并进入产品取舍：

- 本地 ASR 长期无法保留关键中文技术实体。
- FunASR 质量验证被模型下载/缓存长期卡住，且用户不批准模型下载。
- 公开音频只能通过 GB 级整包下载才能抽样。
- 真实会议中建议卡 useful / would_have_asked 低于 40%。
- wrong / too_late / too_intrusive 高于 20-25%。

可选取舍：

- 批准一次 FunASR 模型下载并建立缓存/清理策略。
- 引入远程 ASR 作为可选高质量模式，而非默认模式。
- 降级为会后结构化纪要，不做强实时 Copilot。

## 6. 本轮验证命令

已通过：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_sample_extraction_plan.py -q -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_synthetic_product_value_gate.py -q -p no:cacheprovider
```

结果：

```text
10 passed, 1 warning
7 passed, 1 warning
```

后续完整本地门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 7. 关联文档

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/asr-zero-cost-and-private-audio-boundary.md`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`

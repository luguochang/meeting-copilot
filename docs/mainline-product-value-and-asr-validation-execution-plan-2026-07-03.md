# Mainline Product Value and ASR Validation Execution Plan

> 日期：2026-07-03  
> 状态：Accepted as current mainline execution plan  
> 范围：中文技术会议实时 Copilot 的产品价值验证、公开授权音频模拟、本地 ASR 事件链路、桌面 runtime 和用户最终真实麦克风会议验收。  
> 硬边界：本文档不授权访问麦克风、不授权读取真实用户音频、不授权读取 `configs/local/` 或 `data/asr_eval/local_samples/`，不授权默认下载公开音频大包，不授权自动下载 FunASR/ModelScope 模型，不授权默认调用远程 ASR/LLM。

## 1. 最终结论

完整计划已经写下，并且下一阶段不能再围着 ASR 评测空转。主线固定为：

```text
产品价值闸门
  perfect transcript / mock ASR / real ASR 三路对照
  -> EvidenceSpan
  -> engineering state/gap candidate
  -> suggestion candidate/card
  -> feedback rubric

输入能力闸门
  合成中文技术会议音频
  + 公开授权中文会议音频小样本计划
  -> 本地 ASR partial/final/revision/error/end_of_stream
  -> Web Live ASR handoff
  -> desktop ASR worker handoff
  -> 用户最终真实麦克风会议验证
```

产品不是普通音频转文字，也不是会后总结工具。只要下一轮报告只讨论 CER、RTF、provider readiness 或模型下载，就算偏离主线。每轮必须先回答：

1. 会议中是否发现了工程讨论缺口。
2. 缺口是否在 10-30 秒窗口内形成 EvidenceSpan 和 candidate。
3. candidate/card 是否会被用户标记为 useful 或 would_have_asked，而不是 wrong、too_late 或 too_intrusive。

### 1.1 平台优先级：PC/桌面端优先

首发形态固定为 PC/桌面端，当前开发环境和 first pilot 都以 macOS 为主。Local Web MVP 只是为了先验证 core/API/UI、Live ASR event、EvidenceSpan、state/gap 和 suggestion candidate 的垂直切片；它不是最终产品形态，也不是纯 Web SaaS 主线。

桌面端主线是：本地 Web 工作台 -> Tauri/Mac desktop shell -> native IPC no-op -> ASR worker handoff -> 用户显式授权的麦克风 adapter。移动端、纯 Web 版本和应用市场规划只作为后续产品路线预留，不进入当前 MVP 主线。真实麦克风采集必须等 desktop runtime、IPC、worker handoff、用户 start/pause/resume/stop 和删除语义都具备后再进入。

## 2. 当前事实

### 2.1 已经证明的部分

- Web Live ASR 事件管线已经能从 `final/revision` 生成 EvidenceSpan、state、scheduler、suggestion candidate 和 LLM request draft。
- 本地 ASR event file handoff API 已存在并已加固：只允许读取 `artifacts/tmp/asr_events`，拒绝 forbidden roots、坏 JSON、坏 event contract 和重复 session。
- desktop-side ASR worker handoff preflight 已存在：只校验 descriptor、provider、event_file_path、source_kind 和 chunk lifecycle，不启动 worker，不访问麦克风。
- 5 个合成中文技术会议脚本已经建立：API review、release review、incident review、architecture review、non-engineering control。
- sherpa-onnx 速度可作为性能 baseline，但中文技术实体质量不达标。
- FunASR/Paraformer 是中文质量主候选，但当前缺本地 runtime model dir；没有用户批准前不能自动下载约 840MB 模型。
- 公开音频官方来源已复核，但默认包都是 GB 级，不能自动下载。

### 2.2 尚未证明的部分

- 真实桌面 Tauri 窗口和 native IPC 尚未在本机跑通。
- 真实麦克风 adapter 尚未实现。
- 本地 ASR worker 尚未接入桌面 runtime。
- 真实中文技术会议中建议卡是否足够及时、有用、少打扰，尚未验证。
- 公开音频还没有具体到 3-5 个 clip 的可复现 sample manifest；当前只能作为 no-download plan。

## 3. 网上公开音频方案

公开音频只用于验证会议声学、多人、远场、重叠说话、切句和 ASR event contract。它不能证明工程建议卡片价值。

### 3.1 已复核官方来源

| 优先级 | 来源 | 官方页 | 授权 | 当前可用性 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 | AliMeeting / OpenSLR SLR119 | https://www.openslr.org/119/ | CC BY-SA 4.0 | Eval 包约 3.42G，真实多人会议，含 near/far-field | 最适合公开会议声学验证，但默认不下载 |
| 2 | AISHELL-4 / OpenSLR SLR111 | https://www.openslr.org/111/ | CC BY-SA 4.0 | test 包约 5.2G，真实会议、远场、多通道、重叠说话 | 第二公开会议声学候选，默认不下载 |
| 3 | AISHELL-1 / OpenSLR SLR33 | https://www.openslr.org/33/ | Apache License v2.0 | 主数据包约 15G，普通话朗读 | 只做普通话 ASR sanity，不证明会议价值 |

补充观察：MagicHub / Mandarin Chinese Conversational Speech Corpus - Web Meeting 约 5.2 小时，场景更接近线上会议，但页面标注 CC BY-NC-ND 4.0。当前只记录为 `observed_but_not_whitelisted_sources`，不进入自动下载、切分、转码或产品价值 gate；如果未来要使用，必须先单独做授权和用途复核。

明确不进入自动评测：

- Bilibili、YouTube、播客、直播回放、公开课、技术大会录播。
- 未明确授权下载、切分、转码、衍生处理的公开视频。
- 第三方重打包但无法确认原始授权链的数据。
- 需要登录、禁止商用、禁止改编或授权不清的数据。
- observed-but-not-whitelisted 候选，例如 MagicHub Web Meeting，在未完成单独授权复核前不得绕过白名单。

### 3.2 当前执行状态

公开音频阶段现在不是“准备下载”，而是 `blocked_no_planned_samples`：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_source_whitelist.py

PYTHONDONTWRITEBYTECODE=1 python3 tools/public_audio_sample_extraction_plan.py \
  --source-id alimeeting_openslr_slr119 \
  --target-root artifacts/tmp/public_audio \
  --source-split eval \
  --sample-budget-count 3 \
  --sample-budget-minutes 9 \
  --max-clip-seconds 180
```

期望：

- whitelist 返回 `source_validation_status=passed`。
- sample plan 在没有具体 `planned_samples` 时返回 `plan_status=blocked_no_planned_samples`。
- `safe_to_download_now=false`、`download_command=null`、`extract_command=null`、`transcode_command=null`。

### 3.3 进入公开音频执行的条件

只有满足以下条件之一，才允许继续公开音频小样本执行：

1. 能从官方来源复核到 archive 内真实 member path，形成 3-5 个 clip 的 sample manifest。
2. 用户明确批准一次 GB 级公开数据包下载，并接受 ignored 存储、checksum、清理策略和人工复核。

sample manifest 必须包含：

- `sample_id`
- `source_id`
- `source_url`
- `source_license`
- `archive_name`
- `archive_member_path`
- `clip_start_seconds`
- `clip_end_seconds`
- `expected_duration_seconds`
- `expected_sha256_after_extract`
- `license_citation`
- `cleanup_required=true`

没有这些字段，就不下载、不抽取、不转码、不喂给 ASR。

因此，“网上寻找音频”的执行边界已经收口：继续找公开来源只用于补候选清单，不再扩大自动评测；真正能进入模拟转写的，必须先成为白名单来源并形成 3-5 个可复核 clip 的 sample manifest。

## 4. 模拟转写方案

模拟转写分三层，每层回答的问题不同。

### 4.1 Perfect Transcript Lane

目的：

- 在 ASR 完全正确的情况下，验证 Copilot brain 是否能发现工程缺口。
- 如果 perfect transcript 都不能产生有价值 candidate/card，问题在产品逻辑，不在 ASR。

输入：

- `data/asr_eval/synthetic_meetings/scripts/*.json` 中的标准文字稿和 expected gaps/cards。

输出：

- EvidenceSpan timeline。
- state/gap candidate timeline。
- suggestion candidate/card preview。
- feedback rubric report。

通过条件：

- 每个工程脚本至少命中核心 expected gap。
- 非工程 control 仍为 0 工程 candidate。
- 每个 candidate 都能追溯 EvidenceSpan。
- 10-30 秒窗口内能出现 would-have-asked 类型候选。

### 4.2 Mock ASR Lane

目的：

- 验证 event contract、SSE/JSON streaming、状态增量应用和卡片生命周期。
- 不代表真实 ASR 质量。

输入：

- mock streaming event fixtures。

输出：

- Web Live ASR session。
- JSON/SSE events。
- no-LLM request draft。
- lifecycle readiness summary。

通过条件：

- partial 不触发正式 EvidenceSpan 或 LLM。
- final/revision 能触发 EvidenceSpan 和 state/gap。
- revision 能处理 stale/superseded evidence。
- 非工程 control 0 candidate。

### 4.3 Real ASR Lane

目的：

- 验证本地 ASR 是否能保留中文技术实体、切句和实时性。

输入：

- 合成中文技术会议音频。
- 未来可复核的公开授权会议小样本。
- 最终用户真实麦克风会议。

输出：

- ASR events：`partial/final/revision/error/end_of_stream`。
- ASR metrics：RTF、first partial latency、final latency、raw/normalized technical entity recall。
- Live ASR replay/product value report。

通过条件：

- first pilot 前，至少一个工程脚本 normalized technical entity recall 接近 0.8，且有明确可修正路径。
- final/revision 能稳定生成 EvidenceSpan。
- 非工程 control 0 state/candidate/card。

## 5. 真实麦克风会议验证

真实麦克风会议由用户最终执行。当前阶段我不会访问麦克风，也不会读取用户真实录音。

### 5.1 进入条件

- desktop app 或 desktop shell 可运行。
- native IPC no-op 路径已经验证。
- 麦克风 adapter 支持用户显式 start/pause/resume/stop。
- ASR worker 能输出统一事件文件或事件流。
- Web/desktop UI 能展示 transcript、EvidenceSpan、state/gap 和 candidate/card。
- stop/delete 可用，音频 chunk 只写 ignored runtime root。

### 5.2 First Pilot Protocol

第一场真实会议只做 shadow test：

- 时长：20-30 分钟。
- 场景：中文技术会议，最好包含 API、上线、事故、架构、监控或 owner/deadline 类讨论。
- 用户点击 start 后才采集。
- 默认不上传原始音频。
- 默认不调用远程 ASR。
- 如果启用 LLM，只发送 EvidenceSpan 和结构化状态摘要，不发送原始音频。

导出：

- transcript。
- ASR metrics。
- state timeline。
- candidate/card timeline。
- feedback summary。

### 5.3 人工反馈标签

每张 candidate/card 必须能被标注：

- `useful`
- `would_have_asked`
- `wrong`
- `too_late`
- `too_intrusive`
- `dismissed`

first pilot 建议门槛：

- 20-30 分钟内至少 2 张 genuinely useful 或 would_have_asked 的卡。
- wrong 或 too_intrusive 不超过 1 张。
- 每张卡都有紧凑 EvidenceSpan。

Go evidence 门槛：

- 至少 2 个真实中文技术会议场景。
- useful / would_have_asked >= 40%。
- wrong / too_late / too_intrusive <= 20-25%。

### 5.4 SDD/TDD 与可追溯决策记录

后续每个主线动作必须先落到需求和验收，再实现和自测。最小追踪单元包括：

- `docs/requirements-traceability-matrix.md`：新增或更新需求 ID、验收标准、测试/脚本、当前状态。
- `docs/decision-log.md`：记录产品、架构、ASR/LLM provider、成本、隐私、阶段 Go/No-Go 的重要决策。
- TDD 红/绿证据：涉及代码或行为变化时，先写失败测试，再实现，再跑 focused tests 和必要的质量门。
- 计划文档回填：任何偏离主线的 ASR/provider/readiness 工作，都必须说明它如何直接改善 `EvidenceSpan -> state/gap -> candidate/card -> feedback`，否则不进入主线。

这条规则的目的不是增加文档工作量，而是防止项目重新退化成“又跑了一个 ASR 指标”的循环。每次阶段报告必须同时回答产品价值、事件链路、输入质量和非工程负控四个问题。

## 6. 下一阶段执行顺序

### P0: 公开音频工具边界加固

状态：本轮已完成。

内容：

- `--source-path` 和 `--planned-samples-file` 必须在读文件前拒绝 forbidden roots、仓库外绝对路径和 symlink 逃逸。
- 零 planned samples 不再返回 `ready_for_manual_download_review`，而是 `blocked_no_planned_samples`。
- planned sample attribution 必须绑定 selected source：`source_id/source_url/source_license`。

验证：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  -q -p no:cacheprovider
```

### P1: Product Logic Repair After Tri-Lane/Batch Gate

下一步优先级：最高。

状态：DRV-025 tri-lane gate 和 DRV-026 batch gate 已实现。当前 P1 的剩余动作不是再建 gate，而是根据 batch gate 暴露的问题修产品逻辑和 event lane。

更新：DRV-027 已完成 `architecture-review-001` 架构评审产品逻辑覆盖，并使 `incident-review-001` mock lane 转 ready。当前 5 场景 batch gate 已从 `blocked_by_product_logic` 推进为 `blocked_by_asr_quality`；P1 产品逻辑修复阶段已满足本轮退出条件。

目标：

- 补 `architecture-review-001` perfect transcript lane，让架构评审至少生成 1 个 evidence-backed candidate。
- 拆 `incident-review-001` mock/real event lane 的失败原因，区分事件文本质量、streaming contract 和事故规则覆盖。
- 复跑 5 场景 batch gate，确保非工程 control 仍为 0 candidate。
- gate 已经证明失败来源时，不继续用 ASR/provider 横评遮住产品逻辑缺口。

计划产物：

- `tools/copilot_product_value_tri_lane_gate.py`：已实现。
- `tests/test_copilot_product_value_tri_lane_gate.py`：已实现。
- `docs/copilot-product-value-gate.md`：已实现。

核心输出字段：

- `scenario_id`
- `lane=perfect_transcript|mock_asr|real_asr`
- `expected_gap_count`
- `detected_gap_count`
- `candidate_count`
- `evidence_span_count`
- `candidate_latency_window_status`
- `non_engineering_candidate_count`
- `feedback_rubric_required=true`
- `decision=product_logic_ready|blocked_by_product_logic|blocked_by_stream_contract|blocked_by_asr_quality`

退出条件：

- perfect transcript lane 通过 4 个工程脚本和 non-engineering control，或至少 `architecture-review-001` 从 0 candidate 变为 evidence-backed candidate 并记录剩余缺口。
- `incident-review-001` mock lane 的失败原因被结构化归因；若是规则缺口则补最小规则，若是输入/ASR 质量则记录为 stream/ASR 阻塞。
- 如果 perfect lane 失败，停止继续 ASR/provider 评测，先修产品逻辑。
- 如果 perfect lane 通过但 real ASR lane 失败，才继续 ASR/hotword/normalizer/FunASR。

当前 smoke：

- `api-review-001` perfect transcript lane：`product_logic_ready`，candidate_count=1，EvidenceSpan=3，latency window within expected window。
- `api-review-001` mock lane：`product_logic_ready`。
- `api-review-001` real ASR lane：`blocked_by_asr_quality`，sherpa normalized technical entity recall=0.5。
- 当前结论：该场景下产品 skeleton 并非完全失效，瓶颈主要是 real ASR 中文技术实体质量。

当前 batch summary：

- `tools/copilot_product_value_batch_gate.py` 已把 tri-lane gate 扩到 5 个 synthetic scripts。
- DRV-027 后 5 场景结果：`blocked_by_asr_quality=4`、`product_logic_ready=1`。
- `non-engineering-control-001` 三路通过且 candidate=0。
- 当前 batch-level `overall_decision=blocked_by_asr_quality`。
- 下一步：进入 FunASR/normalizer/hotword 的受控 ASR 质量路径；没有本地模型目录或明确审批前，不下载 FunASR/ModelScope 模型。

### P2: Desktop No-Op Runtime / IPC

下一步优先级：高。

目标：

- 把 Web 工作台放进 Mac desktop shell。
- 只验证 no-op IPC 和 runtime status，不接麦克风、不启动 worker。

边界：

- 不安装 Rust。
- 不修改 shell profile。
- 不运行 Cargo/Tauri，除非工具链和用户批准边界明确。
- 任何 target/build artifact 必须进入 `artifacts/tmp/desktop_tauri_target`。

计划产物：

- `PCWEB-097` 或后续编号的 desktop no-op IPC run packet / readiness intake；`PCWEB-096` 已用于 desktop ASR worker handoff local dry-run bridge。
- focused tests 覆盖 no-mic/no-worker/no-secret/no-remote flags。

退出条件：

- Tauri no-op window 可运行，或明确 blocked 原因。
- `runtime_get_status`、`session_prepare`、`asr_worker_health` 能在 WebView 中调用。

### P3: ASR Worker Handoff Integration

下一步优先级：高。

目标：

- 让 desktop side 的 worker descriptor preflight 能被 Web/desktop UI 或 no-op IPC 路径展示。
- 仍不启动 worker，不访问麦克风。

计划产物：

- descriptor preview UI 或 API bridge。
- worker event file handoff smoke：descriptor -> Web handoff request preview -> `/live/asr/local-event-files/sessions`。
- PCWEB-096 已实现 local dry-run bridge：`preview_only` 不读 event file、不调用 Web API；`synthetic_local_test` 只在临时 data dir 中验证 synthetic event file 到 Web Live ASR session 的合同。

退出条件：

- synthetic/preflight descriptor 能形成 Web handoff request preview。
- 成功和失败路径都不读真实音频、不写 runtime audio、不调用远程 ASR/LLM。
- synthetic local dry-run 能返回 Web handoff response summary，且默认 preview mode 不产生 Web mutation。

### P4: Controlled LLM Suggestion Card Path

下一步优先级：中，高于继续扩 ASR 横评。

目标：

- 在 no-LLM candidate 稳定后，再低频调用 OpenAI-compatible 中转站。
- 不做“会后总结优先”，先做实时建议卡。

边界：

- 只发送 EvidenceSpan 和结构化 state/gap 摘要。
- 控制 token、频率、cooldown、schema、重试。
- 每张卡可反馈 useful/wrong/too_late/too_intrusive/dismissed。

退出条件：

- 非工程 control 仍为 0 工程卡。
- 每张卡能追溯 EvidenceSpan。
- 每 60 分钟 3-8 张有效卡，而不是刷屏。

### P5: Mac Microphone Adapter

下一步优先级：等 P2/P3 完成后。

目标：

- 用户显式点击后，才开始采集真实麦克风。
- 先验证 adapter contract，不直接进入真实会议。

功能：

- input device。
- start/pause/resume/stop。
- level meter。
- duration。
- chunk count。
- local ignored runtime root。
- delete local chunks。

退出条件：

- 用户可控 start/stop/delete。
- 默认不上传，不远程 ASR。
- chunk 不进入仓库。

## 7. 停止和转向条件

停止继续扩评测的条件：

- perfect transcript lane 不能产生有价值 gap/candidate。
- 非工程 control 出现工程 candidate。
- real ASR 长期无法保留关键中文技术实体，且 FunASR 本地模型或用户批准都不可用。
- 公开音频只能通过 GB 级整包下载才能抽样，且用户不批准。
- 真实会议中 useful / would_have_asked < 40%。
- wrong / too_late / too_intrusive > 20-25%。

可选转向：

- 批准一次 FunASR 模型下载并建立缓存和清理策略。
- 引入远程 ASR 作为可选高质量模式，而非默认模式。
- 降级为会后结构化纪要，不承诺强实时 Copilot。

## 8. 本轮已验证命令

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_public_audio_source_whitelist.py \
  tests/test_public_audio_sample_extraction_plan.py \
  -q -p no:cacheprovider
```

结果：

```text
19 passed, 1 warning
```

还需要在收尾前跑：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 9. 当前最终回答

完整计划已经写下。接下来不是继续无穷 ASR 评测，而是：

1. 用 tri-lane product gate 先证明 Copilot brain 有价值。
2. 公开音频只做合法授权小样本治理，不下载大包。
3. 桌面 no-op runtime 和 ASR worker handoff 往真实 PC 客户端推进。
4. 用户最终再做真实麦克风会议 shadow test。

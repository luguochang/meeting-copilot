# Plan Lock: Public Audio Simulation and Real Mic Validation

> 日期：2026-07-03  
> 状态：Accepted as current plan lock  
> 目的：回答“完整计划是否已经写下、转写如何通过网上公开音频和模拟自测、最终真实麦克风会议如何验证”。  
> 边界：本文档不授权下载公开大包、不授权读取用户真实音频、不授权访问麦克风、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权自动下载 FunASR/ModelScope 模型。

## 1. 结论

完整计划已经写下，且当前执行主线固定为：

```text
公开授权中文会议音频来源审查
  + 自建中文技术会议脚本/合成音频
  -> 本地 ASR partial/final/revision
  -> 技术实体、切句、延迟和 EvidenceSpan 质量
  -> meeting state / engineering gap candidate
  -> suggestion card candidate / controlled LLM later
  -> desktop runtime / ASR worker handoff
  -> 用户最终真实麦克风会议验证
```

这条线的重点不是“把音频转成文字”，而是证明会议中能不能及时发现工程讨论缺口，并把每个建议追溯到 EvidenceSpan。ASR 是输入层，实时 Copilot 的价值在 `EvidenceSpan -> state/gap -> suggestion candidate/card -> feedback`。

## 2. 网上公开音频怎么用

我已经联网复核官方来源，当前只保留可说明授权和使用边界的公开数据。真正下载前仍需再次人工复核官方页面的授权、artifact 名称、包大小、存储目录和清理策略。

这里的“公网授权音频”“公开授权音频”和 `public licensed meeting audio` 指同一类输入：公网可访问、原始授权链可复核、允许下载/切分/转码/衍生处理用于本地评测的公开语料。它不包含版权不清的视频、播客、直播回放、公开课，也不包含需要登录、禁止衍生处理或无法复核原始授权的数据。

| 优先级 | 来源 | 官方页 | 授权 | 用途 | 当前动作 |
| --- | --- | --- | --- | --- | --- |
| 1 | AliMeeting / OpenSLR SLR119 | https://www.openslr.org/119/ | CC BY-SA 4.0 | 真实多人中文会议、near/far-field、重叠说话、speaker turn；最接近会议转写模拟 | 只做 bounded sample plan，`Eval_Ali.tar.gz` 约 3.42G，不默认下载 |
| 2 | AISHELL-4 / OpenSLR SLR111 | https://www.openslr.org/111/ | CC BY-SA 4.0 | 中文多人会议声学、远场、多说话人和重叠风险补充 | 只做 bounded sample plan，`test.tar.gz` 约 5.2G，不默认下载 |
| 3 | AISHELL-1 / OpenSLR SLR33 | https://www.openslr.org/33/ | Apache License v2.0 | 普通话 ASR/runtime sanity check | 非会议，不证明产品价值 |

THCHS-30 / OpenSLR SLR18 已作为低优先级公开来源被观察过，但当前不进入自动执行白名单，因为它不是会议场景，且默认包也偏大。实际可执行白名单以 `data/asr_eval/public_sources.json` 和 `tools/public_audio_source_whitelist.py` 为准；目前工具只允许 AliMeeting、AISHELL-4 和 AISHELL-1。

2026-07-03 追加检索到 MagicHub / Mandarin Chinese Conversational Speech Corpus - Web Meeting（页面标注 CC BY-NC-ND 4.0，约 5.2 小时 Web meeting 语料）。它更小且更接近线上会议，但当前不进入自动执行白名单：`NC/ND` 对商业化验证、切分/转码/衍生处理边界更敏感，且需要更严格人工复核。它已记录为 `observed_but_not_whitelisted_sources`，只能作为候选观察，不默认下载、不抽样、不进入产品价值 gate。

明确不采用 Bilibili、YouTube、播客、直播回放、公开视频、公开课和大会录播作为自动评测输入。原因不是技术上做不到，而是授权链、下载/衍生处理、复现实验和合规边界不稳。

## 3. 模拟转写怎么做

转写模拟分三层：

1. `mock_streaming`  
   只验证 event contract、SSE/JSON 流、state/gap/card 前置链路，不代表真实 ASR。

2. `synthetic technical meetings`  
   使用自建中文技术会议脚本和本机合成音频验证技术实体、工程缺口、非工程 0 卡和实时触发窗口。当前已经有 `api-review`、`release-review`、`incident-review`、`architecture-review`、`non-engineering-control`。

3. `public licensed meeting audio`  
   用 AliMeeting/AISHELL-4 小样本覆盖真实会议声学、多说话人、远场、重叠和切句风险。公开音频只证明 ASR/声学/事件链路，不证明工程建议卡片价值。

当前 sherpa-onnx 已证明速度可用，但中文技术实体质量不足，只能作为性能基线。FunASR/Paraformer 是中文质量主候选，但本地 runtime model dir 当前缺失，已被 DRV-019 收束为手动模型下载审批点；没有明确批准前不自动下载模型。

## 4. 公开音频执行边界

公开音频不是“找到链接就下载”。下一步只允许：

1. 运行 `tools/public_audio_source_whitelist.py`，确认来源仍在白名单。
2. 运行 `tools/public_audio_sample_extraction_plan.py` 生成或校验 sample plan。
3. sample plan 必须具体到 `archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_sha256_after_extract`、`license_citation` 和 `cleanup_required`。
4. 校验通过也只表示 `schema_validated_no_download`，不生成下载、解压、转码命令；没有具体 planned samples 时必须返回 `blocked_no_planned_samples`，不再显示为可下载评审 ready。
5. 如果官方包必须整体 GB 级下载才能抽样，则记录 blocked，不继续找版权不清替代来源，也不把 MagicHub 这类 observed-but-not-whitelisted 候选绕过白名单执行。

当前状态是 `no-download 可审查方案`，不是可下载执行方案。DRV-031 已把 public audio sample manifest 条件审查实现为 `tools/public_audio_planned_sample_manifest_decision.py`；默认结论为 `blocked_no_verified_public_sample_manifest`。现在还没有真实 public audio sample manifest：`planned_samples` 为空，没有具体到 3-5 个 clip 的官方 archive member path、clip 起止、真实 sha256 和 attribution，也没有用户批准 GB 级公开包下载。因此当前不能下载、不能抽取、不能把公开音频喂给 ASR。

只有出现以下情况之一，公开音频阶段才继续：

- 能在不下载 GB 级大包的前提下确认官方 archive 内真实 member path，并形成 3-5 个 sample manifest。
- 用户明确批准一次 GB 级公开数据包下载，并接受 ignored 存储、checksum 和清理策略。

否则该阶段结论应写成：`blocked: no bounded public sample manifest without large archive download`，然后继续推进合成中文技术会议、ASR provider 质量、desktop runtime 和最终真实麦克风验收。

允许的本地 artifact 根：

- `artifacts/tmp/public_audio`
- `data/asr_eval/public_raw`

禁止的根：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`

## 5. 最终真实麦克风验证怎么做

真实麦克风会议由用户最终验证；我不会在当前阶段擅自访问麦克风或读取真实会议录音。

进入真实麦克风验证前必须满足：

- desktop app 或 desktop shell 能运行。
- 麦克风 adapter 支持用户显式 start/pause/resume/stop。
- 本地 ASR worker 输出统一 `partial/final/revision/error/end_of_stream`。
- Web/desktop UI 能展示 transcript、EvidenceSpan、state/gap 和建议候选。
- 非工程 control 仍为 0 工程候选。
- ASR 技术实体质量至少有可解释的达标路径。

first pilot：

- 1 场 20-30 分钟中文技术会议 shadow test。
- 导出 transcript、ASR metrics、state timeline、candidate/card timeline 和用户反馈。

Go evidence：

- 至少 2 个真实中文技术会议场景。
- useful / would-have-asked 达到 40% 以上。
- wrong / too-late / too-intrusive 控制在 20-25% 以下。

## 6. 下一步不是继续无限评测

当前已经得到的关键结论：

- ASR event contract 和 Web Live ASR pipeline 已经能连起来。
- `final/revision -> EvidenceSpan -> state/scheduler -> suggestion candidate -> LLM request draft` 的无 LLM 链路已跑通。
- 非工程 control 已修正为 0 工程候选。
- sherpa 速度够，但技术词质量不够。
- FunASR 质量验证被本地模型目录缺失卡住，不能静默下载。
- Web local ASR event file handoff API 已完成边界加固。
- desktop-side ASR worker handoff preflight 已完成 descriptor 级合同；它仍不启动 worker、不访问麦克风、不读写事件文件。
- Copilot product value tri-lane gate 和 5 场景 batch gate 已完成。DRV-027 后，`architecture-review-001` perfect/mock lane 和 `incident-review-001` mock lane 已转 ready，当前 batch-level 结论推进为 `blocked_by_asr_quality`。
- 桌面真实 runtime、真实 ASR worker、麦克风 adapter 仍未完成。

下一轮开发应转向主线交付，不再扩散评测：

1. 进入 ASR 质量受控路径。perfect/mock lane 已过线，当前不能再把 real ASR 质量问题误判成产品逻辑问题。
2. FunASR 只能在用户提供本地模型目录或明确批准 DRV-019 模型下载审批包后运行；没有批准前不下载模型。
3. 保留公开音频 no-download sample plan。只有抽样计划可人工复核时，才进入一次小样本公开音频执行；不能确认具体 sample manifest 时要 blocked，不转向版权不清来源。
4. 继续推进 desktop no-op runtime / IPC，但不越过审批边界运行 Cargo/Tauri、安装工具链或访问麦克风。
5. normalizer/hotword 只能做 bounded 修正：恢复 ASR 文本中已有线索，不从 `<unk>` 或缺失文本猜实体。

### 6.1 立即开发顺序

下一步按以下顺序进入开发，不再新增宽泛 ASR 横评：

| 顺序 | 动作 | 验收 |
| --- | --- | --- |
| 1 | ASR 质量受控路径 | 至少一个工程脚本 real ASR normalized recall 接近 0.8，或明确进入 DRV-019 Need model approval；非工程 control 仍为 0 candidate |
| 2 | bounded normalizer/hotword | 只恢复 ASR 文本中已有线索，不从 `<unk>` 或缺失文本猜实体；raw recall 和 normalized recall 必须分别记录 |
| 3 | 重新跑 5 场景 batch gate | perfect/mock lane 保持 ready；非工程负控仍 0；batch 结论若仍为 `blocked_by_asr_quality`，必须给出模型目录/审批/降级路径 |
| 4 | 公开音频 sample manifest 条件审查 | DRV-031 已完成默认 no-download decision；AliMeeting/AISHELL-4 只有在能确认 3-5 个官方 archive member path、clip start/end、sha256 和 license citation 时进入人工复核；否则保持 `blocked_no_verified_public_sample_manifest` / `blocked_no_planned_samples` |
| 5 | 受控推进 desktop no-op runtime 验证 | 只有在 Rust/Cargo/Tauri 条件已被显式确认时才运行；`cargo check` 必须使用 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`；Tauri no-op shell 只验证 WebView 和 `runtime_get_status`、`session_prepare`、`asr_worker_health` 三个无副作用 IPC |

公开音频和 FunASR 只保留窄评测入口：FunASR 只有在用户批准模型下载或提供完整本地模型目录后跑一次 synthetic smoke；公开音频只有在形成真实 sample manifest 或用户批准 GB 级公开包下载后跑一次小样本。

DRV-041 已把本地模拟转写链路收束为一条可复跑 smoke：approved ASR event JSON 可以纯内存串过 replay、shadow report draft 和 export preview；工程 mock events 产出 `draft_export_preview_only`，非工程 control 保持 blocked/no fake card。这一步只证明模拟事件能进入产品价值 preview，不下载公开音频、不读取真实音频、不访问麦克风、不调用远程 ASR/LLM。

DRV-042 已把 DRV-041 扩展成 5 场景批量 smoke：4 个工程 mock 场景必须 preview，1 个非工程 control 必须 blocked/no candidate。该 batch 是本地模拟质量门，不是 ASR quality Go evidence，也不是真实麦克风会议 evidence。

DRV-032 已消费 DRV-042 batch status：如果 5 场景模拟 batch 失败，下一步必须先修模拟输入或 gap/card 逻辑；当前 batch 通过后，ASR quality gate 仍停在 FunASR 本地模型目录 / DRV-019 审批 / 显式降级试点这类出口。后续不继续扩展 mock smoke，除非它直接支撑 ASR quality exit 或真实 shadow-test 前置。

### 6.2 下一轮报告必须回答的三个问题

后续进展报告不能只列 transcript、CER、RTF 或 provider readiness。每轮主线报告必须回答：

1. 技术实体 normalized recall 是否接近 first-pilot 门槛，或为什么仍然 blocked。
2. final/revision 是否能在 10-30 秒窗口内形成 EvidenceSpan 和工程 gap candidate。
3. 非工程 control 是否仍为 0 工程 state/candidate/card。

只有这三个问题继续变好，才算靠近“实时会议 Copilot”；否则就是普通转写工具风险。

## 7. 关联文件

- `docs/project-current-status-and-forward-plan-2026-07-03.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/drv-041-simulated-shadow-pipeline-smoke-plan.md`
- `docs/drv-042-simulated-shadow-pipeline-batch-smoke-plan.md`
- `docs/current-plan-and-validation-report-2026-07-04.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `data/asr_eval/public_sources.json`
- `tools/public_audio_source_whitelist.py`
- `tools/public_audio_sample_extraction_plan.py`
- `tools/asr_live_pipeline_replay.py`
- `tools/simulated_shadow_pipeline_smoke.py`

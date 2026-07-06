# ASR Public Audio and Simulation Next Run Plan

> 日期：2026-07-03  
> 状态：Accepted as next-run plan  
> 范围：中文技术会议实时 Copilot 的公开音频寻找、合成音频模拟、ASR 转写自测和用户最终真实麦克风验证边界。  
> 硬边界：本文档不授权下载公开大包、不授权读取真实用户音频、不授权读取任何 `.m4a`、不授权读取 `configs/local/`、`data/local_runtime/` 或 `outputs/`、不授权调用远程 ASR/LLM、不授权模型自动下载。

## 1. 当前结论

完整计划已经写下，主线是：

```text
合法公开中文会议音频来源审查
  + 自建中文技术会议脚本和本地合成音频
  -> 本地 ASR partial/final/revision
  -> EvidenceSpan
  -> meeting state
  -> engineering gap candidate
  -> suggestion card candidate
  -> 用户最终真实麦克风会议验证
```

我负责在真实麦克风会议之前完成公开来源审查、合成音频模拟、本地 ASR 自测和指标报告。用户最终再用真实麦克风会议验证产品价值。

这不是继续无限评测。下一轮只回答三个问题：

1. 本地 ASR 是否能保留中文技术实体。
2. 文件回放式实时事件是否能及时产生 EvidenceSpan 和 gap candidate。
3. 进入真实麦克风会议前，还缺的是模型质量、桌面 runtime，还是产品范围取舍。

## 2. 联网寻找公开音频的结论

2026-07-03 已复核官方来源：

| 来源 | 官方页 | 授权 | 当前判断 |
| --- | --- | --- | --- |
| AliMeeting / OpenSLR SLR119 | https://www.openslr.org/119/ | CC BY-SA 4.0 | 真实多人会议近场/远场对照主候选；`Eval_Ali.tar.gz` 约 3.42G，不默认下载 |
| AISHELL-4 / OpenSLR SLR111 | https://www.openslr.org/111/ | CC BY-SA 4.0 | 真实普通话多人会议声学和重叠说话补充；`test.tar.gz` 约 5.2G，不默认下载 |
| AISHELL-1 / OpenSLR SLR33 | https://www.openslr.org/33/ | Apache License v2.0 | 普通话 ASR sanity check；不是会议，不证明产品价值 |
| THCHS-30 / OpenSLR SLR18 | https://www.openslr.org/18/ | Apache License v2.0 | 观察/低优先级备选；不是会议，不进入当前自动白名单 |
| MagicHub Web Meeting | https://magichub.com/datasets/mandarin-chinese-conversational-speech-corpus-web-meeting/ | CC BY-NC-ND 4.0 | 小体量 web meeting 观察候选；NC/ND，需登录/人工复核，不进入自动白名单 |
| MagicData-RAMC / OpenSLR SLR123 | https://www.openslr.org/123/ | CC BY-NC-ND 4.0 | 自发对话观察候选；非会议主集，NC/ND，不进入自动白名单 |
| MISP-Meeting | https://challenge.xfyun.cn/misp_dataset | Non-commercial research license | 真实多人会议、多通道、噪声/房间差异强候选；非商业许可，不进入商业 MVP 自动白名单 |
| Mozilla Common Voice zh-CN | https://commonvoice.mozilla.org/ | CC0-1.0 | 普通话短句 baseline 观察候选；非会议且包体大，不进入自动白名单 |
| PyCon China | https://cn.pycon.org/2024/ | Authorization required | 真实中文技术会议域候选；公开可看不等于允许抽音频/转写/商用评测，需书面授权和人工标注 |
| QCon / InfoQ | https://time.geekbang.org/course/intro/101031501 | Authorization required | 真实中文架构/工程实践域候选；平台课程和讲者企业限制风险高，需书面授权和人工标注 |
| WenetSpeech / OpenSLR SLR121 | https://www.openslr.org/121/ | CC BY 4.0 metadata with platform-audio provenance caveat | 明确排除；依赖 YouTube/podcast 平台音频版权链 |

明确不采用：

- Bilibili、YouTube、播客、直播回放、公开视频和大会录播作为自动评测输入。
- 需要登录、限制衍生、限制商用、授权链不清的第三方重打包数据。
- PyCon China、QCon、InfoQ、ArchSummit 等真实技术大会内容在无书面授权和人工标注计划前，只能作为 future authorized-only 域内验证候选，不能抓取或自动评测。
- GitHub 代码仓库中的论文、样例图或训练脚本替代原始授权音频。
- 依赖 YouTube、podcast 或其他平台音频 URL 的语料，即使元数据或标注存在开放许可。

现实判断：

- 公开授权会议音频已经找到，但官方包是 GB 级。
- 当前自动计划工具白名单只包含 AliMeeting、AISHELL-4 和 AISHELL-1；THCHS-30 若未来要进入工具链，必须另做白名单和测试决策。
- MISP-Meeting、PyCon China 和 QCon/InfoQ 能提高“真实会议/真实技术词”贴合度，但它们当前只记录为 observed/authorization-required，不改变自动白名单或默认 no-download 策略。
- 下一步不是继续找更多网站，而是生成具体的 bounded extraction plan。AliMeeting Eval 优先，AISHELL-4 test 作为第二选择。
- 如果无法在不下载大包的情况下锁定 3-5 个小样本，则公开音频阶段保持 plan-only，不影响合成音频和桌面 runtime 主线继续推进。

## 3. 下一轮执行顺序

### Step 1: FunASR offline guard

目的：

- 避免再次触发 FunASR/ModelScope 自动下载约 840MB online 模型。

当前状态：

- `artifacts/tmp/asr_reports/api-review-001.funasr.readiness.json` 显示 `blocked`。
- 原因是 runtime cache 缺失；legacy/hub cache 存在不能证明可离线执行。
- 2026-07-03 已在 `code/asr_runtime/scripts/transcribe_funasr.py` 实现 streaming offline guard：`--streaming` 执行必须显式传入 `--local-model-dir`，且目录必须包含 `model.pt` 和 `config.yaml`；缺失时返回 `status=blocked`，不会构造 `AutoModel`。
- `tools/funasr_synthetic_smoke_readiness.py` 的 command preview 已同步为 `--local-model-dir <modelscope_runtime_models_iic/...>` 占位符，不再暗示可以裸跑 alias。
- 2026-07-03 已新增 DRV-019 FunASR 模型下载审批包：`code/asr_runtime/funasr-model-download-approval.policy.json` 和 `tools/funasr_model_download_approval_packet.py`。它只生成 `manual_user_run_only` 静态报告，记录 ModelScope/iic `speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online`、约 840MB 模型风险、approval tokens、清理说明和 post-download 验证顺序；仍不执行下载。

已完成动作：

1. 为 `code/asr_runtime/scripts/transcribe_funasr.py` 增加离线执行 guard 的 TDD 覆盖。
2. 支持显式 local model dir offline-only model resolution。
3. runtime cache/local model dir 缺失时只输出 blocked report，不构造会自动下载的 provider。
4. 报告继续保持 `safe_to_download_models=false`、`safe_to_call_remote_asr=false`、`safe_to_call_llm=false`。
5. 为模型缺失状态增加 DRV-019 审批包，确保 “Need model approval” 是可审计决策点，而不是隐式下载动作。

剩余动作：

1. 真实 FunASR synthetic smoke 仍需一个已验证的本地模型目录，或用户明确批准并手动完成 DRV-019 模型下载。
2. 在模型目录就绪前，不运行真实 FunASR provider，只保留 guard、readiness 和审批包自测。

退出条件：

- offline guard 已通过 focused tests；还必须有本地模型目录/cache 才允许跑 FunASR synthetic smoke。
- 如果本地模型目录/cache 继续缺失，FunASR 质量验证只能进入 DRV-019 “Need model approval” 决策点；不能绕过审批触发 ModelScope 下载。

### Step 2: Synthetic audio smoke batch

目的：

- 用自建中文技术会议脚本证明产品链路不是普通转写。

输入：

- `data/asr_eval/synthetic_meetings/scripts/api-review.json`
- `data/asr_eval/synthetic_meetings/scripts/release-review.json`
- `data/asr_eval/synthetic_meetings/scripts/incident-review.json`
- `data/asr_eval/synthetic_meetings/scripts/architecture-review.json`
- `data/asr_eval/synthetic_meetings/scripts/non-engineering-control.json`

输出：

- 合成音频：`artifacts/tmp/synthetic_audio/`
- ASR events：`artifacts/tmp/asr_events/`
- ASR reports：`artifacts/tmp/asr_reports/`

指标：

- RTF。
- first partial latency。
- final latency。
- raw/normalized technical entity precision/recall。
- EvidenceSpan 可生成率。
- expected gap/card 命中窗口。
- non-engineering control 工程卡为 0。

退出条件：

- 2026-07-03 已完成第一轮 synthetic audio smoke batch；sherpa RTF 约 0.029-0.035，速度达标；确定性 normalizer 增量后，4 个工程脚本 normalized technical entity recall 仅 0-0.5，未接近 first-pilot 门槛。
- 至少 `api-review` 和一个第二场景的 normalized technical entity recall 接近 first-pilot 门槛后，才允许进入 first real-mic pilot。
- sherpa 已确认只能保留为性能基线，不再在 sherpa 上继续打磨产品质量。
- 如果 FunASR 不可用，先推进 normalizer/hotword 和桌面 runtime，不继续无限扩 provider 横评。

### Step 3: Public audio bounded sample plan

目的：

- 用合法公开会议音频覆盖真实会议声学、多说话人、远场、重叠和切句风险。

前置 gate：

1. 先运行 `tools/public_audio_source_whitelist.py`，确认来源仍在白名单，且 `safe_to_download_now=false`。
2. 再运行 `tools/public_audio_sample_extraction_plan.py`，生成或校验 planned sample schema。
3. whitelist 和 sample plan 都通过，也只表示“可以人工复核下载方案”，不表示可以自动下载。

计划必须包含：

- `source_id`
- `source_url`
- `source_license`
- `source_split`
- `sample_id`
- `archive_name`
- `archive_member_path`
- `clip_start_seconds`
- `clip_end_seconds`
- `expected_duration_seconds`
- `expected_sha256_after_extract`
- `license_citation`
- `target_root`
- `max_download_bytes`
- `max_clip_seconds`
- `sample_budget_count`
- `sample_budget_minutes`
- `cleanup_required`

当前实现状态：

- `tools/public_audio_sample_extraction_plan.py` 已支持 `planned_samples` schema 校验。
- 校验通过时输出 `planned_samples_status=schema_validated_no_download`、`planned_sample_count` 和 `planned_total_duration_seconds`。
- 没有具体 planned samples 时输出 `plan_status=blocked_no_planned_samples`，下一步是 `create_concrete_public_audio_sample_manifest`。
- `--source-path` 和 `--planned-samples-file` 在读文件前拒绝 forbidden roots、仓库外绝对路径和 symlink 逃逸。
- 绝对路径、`..` 路径、超出 `max_clip_seconds`、超出样本数量、超出总分钟预算、非 64 位小写 sha256、缺失 license citation 或 `cleanup_required=false` 都会 blocked。
- 即使 planned samples 校验通过，`safe_to_download_now=false`、`safe_to_extract_now=false`，且 `download_command`、`extract_command`、`transcode_command` 仍为 `null`。

硬限制：

- 默认不下载。
- 默认不抽取。
- 默认不调用 ASR。
- 默认不生成 `download_command`、`extract_command` 或 `transcode_command`。
- 任何真实下载前必须再次人工确认官方 artifact、字节上限、checksum、ignored 存储目录和清理策略。
- 只有计划具体到 3-5 个 clip 且人工复核通过，才允许进入一次公开音频小样本执行。

退出条件：

- 如果公开音频抽样需要下载 GB 级大包且无法限定字节上限，则记录 blocked。
- blocked 后不继续搜索版权不清来源；继续走 synthetic + desktop runtime 主线。

### Step 4: Desktop runtime validation

目的：

- 从文件/fixture 验证推进到真实 PC 客户端运行。

顺序：

1. 受控确认 Rust/Cargo 工具链。
2. `cargo check` 使用 `CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target`。
3. 运行 Tauri no-op window。
4. 验证 Web UI 在 Tauri WebView 内调用 no-op native IPC。
5. 进入最小真实 mic adapter implementation boundary / readiness；本步骤仍不采集，真实采集只能在 readiness gate 返回 ready 后由用户显式 start。

说明：

- 这一步不依赖公开音频必须成功。
- 只要 synthetic event -> state/gap/card 链路可运行，就可以推进桌面壳验证。

### Step 5: User real mic meeting validation

目的：

- 验证产品真实价值，而不是继续证明评测工具。

执行者：

- 用户最终执行真实会议验证。

进入条件：

- 桌面 app 可运行。
- 麦克风采集支持 start/pause/resume/stop。
- 本地 ASR worker 可输出 `partial/final/revision/error/end_of_stream`。
- UI 可展示 transcript、EvidenceSpan、state/gap 和建议候选。

隐私和数据边界：

- 只有用户显式点击 start 后才采集麦克风。
- 原始 chunk 只能写入 ignored 本地 runtime 目录，不能写入仓库可提交路径。
- 必须提供 stop 和本地删除路径。
- 默认不上传原始音频，不读取 `configs/local/`，不调用远程 ASR。
- 如果后续启用 LLM，只能发送 EvidenceSpan 和结构化状态摘要，不发送整段原始音频。

验收：

- first pilot：1 场 20-30 分钟中文技术会议 shadow test。
- Go evidence：至少 2 个真实中文技术会议场景。
- 导出 transcript、ASR metrics、state timeline、card timeline 和用户反馈。

## 4. 本轮不做什么

本轮明确不做：

- 不抓 B 站、YouTube、播客或公开课音频。
- 不默认下载 AISHELL-4/AliMeeting/AISHELL-1 的 GB 级数据包；THCHS-30 当前不在自动白名单，也不默认下载。
- 不读取真实用户音频或 `data/asr_eval/local_samples/`。
- 不访问麦克风。
- 不读取 `configs/local/`。
- 不调用远程 ASR。
- 不调用 LLM 中转站。
- 不自动下载 FunASR 模型。
- 不把公开音频或合成音频当成最终产品价值证明。

## 5. 决策点

下一轮结束后只允许进入以下三种结论之一：

| 结论 | 条件 | 下一步 |
| --- | --- | --- |
| Go to desktop | 合成音频链路可运行，技术实体质量有可修正路径 | 推进 Tauri runtime 和麦克风 adapter |
| Need model approval | FunASR 本地质量验证被 runtime cache/model download 卡住 | 形成模型下载审批包，说明体积、缓存、清理和收益 |
| Pivot ASR strategy | 本地模型长期无法保留中文技术实体 | 评估可选远程 ASR 对照或缩小 MVP |

不允许的结论：

- “继续增加更多 readiness 文档再说”。
- “再找一些版权不清音频试试”。
- “先把它做成普通转写工具”。

## 6. 关联文件

- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-zero-cost-and-private-audio-boundary.md`
- `docs/asr-evaluation-dataset.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `data/asr_eval/public_sources.json`
- `data/asr_eval/public_sample_plan.example.json`
- `tools/public_audio_sample_extraction_plan.py`
- `tools/funasr_synthetic_smoke_readiness.py`
- `tools/synthetic_audio_local_tts_smoke.py`
- `tools/synthetic_asr_smoke_report.py`

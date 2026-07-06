# 中文技术会议 ASR 评测集规范

> 日期：2026-06-18  
> 目的：建立可复现的中文技术会议 ASR provider 横评数据集。

## 1. 数据集分层

### smoke

用途：

- 验证 manifest、reference、annotation、provider、report 管线。
- 可以使用占位音频和 mock transcript。

不用于：

- 判断真实 ASR 质量。
- provider 排名。
- 证明实时会议 Copilot 产品价值。

### dev

用途：

- 5-10 段脱敏中文技术会议音频。
- 每段 1-5 分钟。
- 用于调试 provider、热词、归一化和指标。
- 当前阶段优先使用公开授权会议音频小样本 + 自建中文技术会议脚本生成音频，真实用户麦克风会议放到最终验证。

### benchmark

用途：

- 20+ 段真实脱敏会议音频。
- 覆盖 API、上线、事故、架构、中英混合、多说话人、弱音质、长音频。
- 用于 Go/No-Go 和 provider 选型。

## 2. 样本字段

Manifest v1 当前字段：

- id。
- audio_path。
- reference_path。
- annotation_path。
- language。
- scenario。
- duration_seconds。

Manifest v2 计划字段：

- speaker_count。
- audio_quality。
- noise_level。
- meeting_type。
- domain。
- hotwords_path。
- expected_provider_capabilities。
- tags。

## 3. 标注内容

每条 annotation 至少包含：

- technical_entities。
- decisions。
- action_items。
- risks。
- gaps。

建议扩展：

- transcript_segments。
- speaker_turns。
- evidence_spans。
- hotword_entities。

## 4. 必备样本类型

MVP bake-off 至少覆盖：

- API review：字段名、接口名、兼容性。
- Release review：灰度、回滚、阈值。
- Incident review：告警、根因、指标。
- Architecture review：组件、依赖、容量。
- Mixed Chinese/English：服务名、repo、字段名密集。
- Noisy meeting：远场、键盘声、混响。
- Multi-speaker：多人插话。

## 5. 真实评测门槛

在真实 provider 选型前，至少需要：

- 10 段以上人工 reference。
- 每段都有 technical_entities 标注。
- 至少 3 类会议场景。
- 至少 1 段弱音质。
- 至少 1 段中英混合密集。
- 至少 1 段 15 分钟以上长音频。

## 6. 公开音频和模拟音频策略

在用户最终进行真实麦克风会议验证前，ASR 和实时事件链路先用三层数据验证。

### 6.1 公开授权中文音频

允许优先评估的公开来源：

| Source | URL | License | 用途边界 |
| --- | --- | --- | --- |
| AISHELL-4 / OpenSLR SLR111 | https://www.openslr.org/111/ | CC BY-SA 4.0 | 真实普通话多人会议、远场、重叠、切句、speaker turn；不代表软件工程会议语义 |
| AliMeeting / OpenSLR SLR119 | https://www.openslr.org/119/ | CC BY-SA 4.0 | 真实多人会议，近场/远场对照，适合验证 meeting ASR 和 diarization 风险 |
| AISHELL-1 / OpenSLR SLR33 | https://www.openslr.org/33/ | Apache License v2.0 | 普通话 ASR smoke 和模型安装验证；不代表会议场景 |

使用规则：

- 不默认下载完整数据集。
- 第一次下载必须走单独的 source whitelist 和 sample extraction 计划。
- 原始数据只能放到 ignored 目录，例如 `data/asr_eval/public_raw/` 或 `artifacts/tmp/public_audio/`。
- 不提交原始音频、压缩包、解压音频或大体量生成音频。
- 报告只保存样本 ID、来源、授权、时长、指标和相对输出，不保存本机绝对路径。
- 不抓取 B 站、YouTube、播客、会议录播等不明授权音频进入自动评测。

默认自动白名单之外的候选：

| Source | 当前判断 | 使用边界 |
| --- | --- | --- |
| MagicHub ASR-CCMeetingSC Chinese Conversational Meeting (Web) Speech Corpus | 体量较小，2026-07-02 网页核查显示约 202MB 且需要登录下载；授权/使用限制比 OpenSLR 默认候选更重 | 不进入默认自动白名单；如后续要用，只能人工复核 license、下载条款、用途限制和输出物保存方式 |

公开视频/网页音频规则：

- 可以联网寻找公开授权数据集和 demo 页面。
- 不能抓取不明授权的视频、播客、直播回放或会议录播作为自动评测输入。
- 不能把需要登录、限制二次处理或限制商用/衍生的资源默认写入自动下载白名单。
- 任何新增来源必须先通过 `public_audio_source_whitelist` 类似的 no-download report，再进入 bounded extraction plan。

### 6.2 自建中文技术会议脚本与合成音频

公开会议数据通常不覆盖软件工程评审语义，因此必须补充可控脚本：

- API review。
- Release review。
- Incident review。
- Architecture review。
- Mixed Chinese/English。
- Non-engineering control。

每个脚本必须包含：

- reference transcript。
- technical_entities。
- expected_state_events。
- expected_gap_candidates。
- expected_suggestion_cards。
- transcript-only / summary-only baseline expectations。
- expected zero-card control 标注。

`expected_suggestion_cards` 在合成技术会议层是必填，不是可选扩展。每张 expected card 至少包含：

- gap_type。
- suggested_question。
- related turn 或 EvidenceSpan 线索。
- trigger_window_seconds，最大值不得超过 30 秒。
- should_show / should_silence / should_degrade 标记。
- 预期用户反馈标签，例如 useful、would_have_asked、too_late、too_intrusive 或 wrong。

指标口径：

- `>=80%` 技术实体 recall 只是进入 first real-mic pilot 的最低门槛。
- `>=90%` 技术实体 precision/recall 才是 MVP/product 目标。
- 同一合成脚本必须能与 transcript-only 和 summary-only baseline 对照，证明 Copilot 能在仍可追问的窗口内提前发现工程缺口。

音频可由本机 TTS 或离线 TTS 生成，必要时加入停顿、轻微重叠、噪声和远场衰减。生成音频放 ignored 目录，脚本、reference 和 annotation 可提交。

### 6.3 用户真实麦克风会议

真实麦克风会议是最终产品价值验证，不用于前置自动评测。

进入条件：

- Mac 桌面壳真实运行。
- 麦克风采集可手动 start/pause/resume/stop。
- 本地 ASR worker 能输出 `partial/final/revision`。
- UI 能展示 transcript、EvidenceSpan、状态候选和建议候选。
- 本地数据删除和导出路径明确。

真实会议验证必须记录：

- ASR final latency。
- 技术实体 precision/recall。
- suggestion card latency。
- useful/wrong/too_late/too_intrusive 反馈。
- 非工程会议工程卡是否为 0。

真实会议验证分两级：

- First pilot：1 场 20-30 分钟中文技术会议 shadow test，用来发现桌面采集、ASR、卡片时机和打扰问题。
- Go evidence：至少 2 个真实中文技术会议场景，每场都导出 transcript、state timeline、card timeline、ASR metrics 和用户反馈；只有 Go evidence 达标后，才能说实时 Copilot 路线继续投入是合理的。

## 7. 当前 smoke 数据

当前仓库已提供：

- `manifests/smoke.json`：单样本 smoke。
- `manifests/smoke-multiscenario.json`：多场景 smoke。
- `references/`：人工参考文本。
- `annotations/`：技术实体和结构化标注。
- `configs/asr_providers/mock-transcripts.json`：mock provider 输入。

这些只能验证评测管线，不代表真实 ASR 效果。

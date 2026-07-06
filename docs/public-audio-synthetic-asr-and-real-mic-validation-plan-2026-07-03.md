# Public Audio, Synthetic ASR, and Real Mic Validation Master Plan

> 日期：2026-07-03  
> 状态：Accepted as current validation master plan  
> 范围：中文技术会议实时 Copilot 的转写、模拟、公开音频验证与最终真实麦克风验收。  
> 边界：本文档不授权读取真实用户音频，不授权读取任何 `.m4a`，不授权读取 `configs/local/`，不授权读取 `data/asr_eval/local_samples/`，不授权读取 `data/local_runtime/` 或 `outputs/`，不授权默认调用远程 ASR/LLM，不授权抓取版权不清的视频、播客或会议录播。

## 1. 一句话结论

完整计划已经写下并收束为这条主线：

```text
公开授权中文会议音频
  + 自建中文技术会议脚本/合成音频
  -> 本地 ASR partial/final/revision
  -> 技术实体与切句质量
  -> EvidenceSpan
  -> meeting state
  -> engineering gap candidate
  -> suggestion card candidate
  -> 用户最终真实麦克风会议验证
```

我负责前置的公开来源研究、合成音频模拟、本地 ASR 自测和指标报告。用户最终再用真实麦克风会议做产品价值验收。真实麦克风会议不能被公开数据集、TTS 合成音频或 fixture 成功替代。

2026-07-04 最新确认：转写验证仍由我先用网上官方公开音频来源和本地模拟完成；最终真实麦克风会议由用户验证。本计划继续只允许官方来源复核、no-download manifest、合成音频、mock streaming events 和 approved replay；没有 bounded clip manifest 和下载审批前不下载公开音频大包。PCWEB-124 已把 PCWEB-115 readiness 显示到 Web 工作台，默认仍 blocked，不代表真实麦克风会议可以开始。

## 2. 当前已经得到的结论

### 2.1 产品结论

- 主线没有变：产品不是转写工具，也不是会后总结工具。
- 有价值的 MVP 必须在会议中及时发现工程讨论缺口，例如 owner、deadline、rollback、test、metric、monitoring、risk 和 open question。
- ASR 只是输入层；只有 `ASR -> EvidenceSpan -> state/gap -> suggestion card -> feedback` 链路能跑通，才算接近产品价值。
- 公开音频主要证明声学、实时性、切句和 ASR 事件合同；不能证明技术会议建议卡片真的有价值。
- 合成中文技术会议主要证明技术实体、工程缺口、非工程 0 卡和实时追问窗口；不能替代真实麦克风会议。

### 2.2 技术结论

- sherpa-onnx 在本机小样本上速度足够快，可作为性能基线。
- sherpa-onnx 当前中文技术词质量不够，不能作为默认质量候选。
- FunASR/Paraformer 仍是中文质量主候选；后续应优先用已有本地缓存模型验证，不静默下载新模型。
- 2026-07-03 复测发现：ModelScope legacy/hub cache 存在并不等于 FunASR runtime cache 可用；如果 runtime cache 缺失，FunASR 会尝试下载约 840MB online 模型。当前已把 readiness gate 改为 runtime cache 缺失即 blocked；即使 cache preflight 通过，也只能说明文件预检通过，不能证明离线执行绝不下载。
- 远程 ASR 不进入默认 MVP。除非后续本地 ASR 长期达不到最低质量，远程 ASR 只能作为显式可选质量对照。
- LLM 中转站是唯一默认远程模型成本方向，但转写验证阶段默认不调用 LLM。

### 2.3 已完成的本地 smoke 证据

2026-07-03 已完成一次本地合成音频和 sherpa ASR smoke：

| 项 | 结果 |
| --- | --- |
| 合成输入 | `api-review-001` 中文技术会议脚本 |
| TTS | macOS 本机 `say` + `afconvert` |
| 输出音频 | `artifacts/tmp/synthetic_audio/api-review-001.wav`，16kHz mono PCM，约 16.83 秒 |
| ASR provider | sherpa-onnx streaming zipformer small CTC zh int8 |
| ASR 状态 | `status=ok` |
| latency | 516 ms |
| RTF | 0.030667 |
| event count | partial 25，final 1，revision 0，error 0，end_of_stream 1 |
| `<unk>` count | 7 |
| raw technical entity recall | 0.0 |
| normalized technical entity recall | 0.25，只恢复 `P99` |
| first-pilot threshold | 未通过，低于 0.8 |
| product target | 未通过，低于 0.9 precision/recall |

结论：

- 本地文件回放式实时 ASR 事件链路能跑，速度不是当前最大问题。
- 中文技术词识别质量是当前最大风险，尤其是 `payment-gateway`、`request_id`、`40012` 这类中英混合实体。
- 后续必须继续验证 FunASR、hotword/normalizer、技术词纠错和 card 触发质量；不能把这次 smoke 解释成“转写已可用”。

## 3. 公开音频来源策略

### 3.1 已联网复核并推荐的来源

| 优先级 | 来源 | 授权 | 适合点 | 不适合点 | 使用策略 |
| --- | --- | --- | --- | --- | --- |
| 1 | [AliMeeting / OpenSLR SLR119](https://www.openslr.org/119/) | CC BY-SA 4.0 | 真实多人会议，同时有 near-field/far-field，对比价值高 | 不是软件工程会议，Eval/Test 仍是 GB 级 | 作为会议实时转写模拟主集；优先 Eval 小样本计划 |
| 2 | [AISHELL-4 / OpenSLR SLR111](https://www.openslr.org/111/) | CC BY-SA 4.0 | 普通话真实多人会议、远场、多通道、重叠说话、speaker turn | 不是软件工程会议，test 仍是 GB 级 | 作为复杂会议声学和多人重叠风险补充；先做 bounded sample extraction plan |
| 3 | [AISHELL-1 / OpenSLR SLR33](https://www.openslr.org/33/) | Apache License v2.0 | 普通话干净朗读，适合模型/管线 sanity check | 非会议、非多人、非技术场景 | 只做中文 ASR smoke 和回归，不做产品价值判断 |
| 4 | [THCHS-30 / OpenSLR SLR18](https://www.openslr.org/18/) | Apache License v2.0 | 小型中文朗读/噪声回归可观察 | 非会议、偏 toy corpus | 观察/低优先级备选，不进入当前自动白名单 |

2026-07-03 二次联网复核结果：

- OpenSLR SLR111 官方页仍标记 AISHELL-4 为 CC BY-SA 4.0；`test.tar.gz` 约 5.2G，训练包更大，不适合默认下载。
- OpenSLR SLR119 官方页仍标记 AliMeeting 为 CC BY-SA 4.0；`Eval_Ali.tar.gz` 约 3.42G，`Test_Ali.tar.gz` 约 8.90G，第一次只允许形成小样本抽取计划。
- OpenSLR SLR33 官方页仍标记 AISHELL-1 为 Apache License v2.0；`data_aishell.tgz` 约 15G，只能作为普通话 ASR sanity check，不代表会议场景。
- OpenSLR SLR18 官方页仍标记 THCHS-30 为 Apache License v2.0；作为观察/低优先级备选，不进入当前自动白名单。
- AISHELL-4 GitHub 仓库只适合作为论文、代码和样例说明参考；未发现可直接替代 OpenSLR 大包的小体量公开会议 wav 样本，因此不能把 GitHub 代码仓库当作默认音频来源。
- MagicHub / Mandarin Chinese Conversational Speech Corpus - Web Meeting 已作为小体量线上会议候选观察；页面标注 CC BY-NC-ND 4.0，约 5.2 小时。由于 `NC/ND` 对商业化验证、切分/转码/衍生处理更敏感，且来源使用方式需要人工复核，它只记录到 `observed_but_not_whitelisted_sources`，不进入自动下载/抽样白名单。
- MagicData-RAMC / OpenSLR SLR123 已作为自发对话候选观察；许可证同样是 CC BY-NC-ND 4.0，且不是会议主集，只能作为未来人工观察，不进入自动下载/抽样白名单。
- Mozilla Common Voice zh-CN 已作为 CC0 普通话短句 baseline 候选观察；它不是会议语音且包体大，只能用于未来可选 ASR sanity/口音覆盖思路，不证明多人会议或技术会议产品价值。
- WenetSpeech / OpenSLR SLR121 明确排除；即使页面有 CC BY 元数据口径，其音频来源依赖 YouTube/podcast 等平台 URL，和本项目不抓平台音频/播客的边界冲突。

### 3.2 明确不进入自动评测的来源

- Bilibili、YouTube、播客、电视节目、直播切片、公开课和大会回放。
- 版权链不清或只允许在线观看、不允许下载和二次处理的会议公开视频。
- Hugging Face 上第三方重打包但不是原始授权方发布的数据。
- 需要登录、限制商用、限制改编或禁止衍生的音频，不进入默认自动下载白名单；MagicHub Web Meeting、MagicData-RAMC 这类候选也按此规则处理。
- 依赖 YouTube、podcast 或其他平台音频 URL 的语料，不进入默认自动评测，即使其转写、URL 列表或元数据本身带有开放许可。

这些来源可以用于人工观察竞品或场景，但不能进入本项目自动评测数据集。

### 3.3 下载和抽样边界

- 不默认下载完整公开数据集。
- 每个公开来源先经过 `public_audio_source_whitelist` 和 `public_audio_sample_extraction_plan`。
- 第一次真实下载必须记录 source id、官方 URL、split、archive name、archive member path、clip start/end、大小上限、时长上限、checksum、目标根和清理策略。
- 原始音频只能放在 ignored 目录：`data/asr_eval/public_raw/` 或 `artifacts/tmp/public_audio/`。
- 不提交原始音频、解压音频、压缩包或大体量生成音频。

当前执行判断：

- 公开音频来源已经找到并复核，但 OpenSLR 默认包都是 GB 级；我不会在未形成具体抽样清单前下载。
- 当前自动计划工具白名单只包含 AliMeeting、AISHELL-4 和 AISHELL-1；THCHS-30 只是观察/低优先级备选，若未来要进入工具链，必须另做白名单、工具常量和测试决策。
- 公开音频的下一步不是“继续找更多网站”，而是把 AliMeeting Eval 或 AISHELL-4 test 的某个 split 细化到 3-5 个可复现 clip。
- 2026-07-03 已把 `public_audio_sample_extraction_plan` 升级为 planned sample schema 校验：样本计划必须包含 `sample_id`、`source_id`、`source_url`、`source_license`、`archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_duration_seconds`、`expected_sha256_after_extract`、`license_citation` 和 `cleanup_required`；校验通过也只表示 `schema_validated_no_download`，不会生成下载、解压或转码命令。没有具体 planned samples 时返回 `blocked_no_planned_samples`，不再显示为可下载评审 ready。
- 如果找不到小体量、授权清楚、可复现的公开会议 clip，公开音频只做 no-download plan；真实 ASR 语义价值继续靠合成中文技术会议和用户最终麦克风会议证明。

### 3.4 公开音频硬 Gate

公开音频阶段必须按以下 gate 执行，不再扩大来源池：

| Gate | 验收门槛 |
| --- | --- |
| Source whitelist gate | 自动白名单只允许 `alimeeting_openslr_slr119`、`aishell4_openslr_slr111`、`aishell1_openslr_slr33`；所有 source 必须保持 `default_download_enabled=false`、`raw_audio_committed_to_repo=false`、`product_value_validation_allowed=false`。 |
| Public sample manifest gate | 没有 3-5 个 concrete clips 时必须是 `blocked_no_planned_samples`；每个 clip 必须包含 `source_id`、`source_url`、`source_license`、`archive_name`、`archive_member_path`、`clip_start_seconds`、`clip_end_seconds`、`expected_duration_seconds`、`expected_sha256_after_extract`、`license_citation` 和 `cleanup_required=true`；`download_command`、`extract_command`、`transcode_command` 必须为 `null`。 |
| Public audio execution gate | AliMeeting Eval 优先，AISHELL-4 test 次之；每段建议 30-120 秒，工具硬上限可保留 180 秒作为异常上限；总时长建议不超过 9 分钟；任何 GB 级下载必须另有人工批准、checksum、ignored 目录和清理策略。 |
| Supplement gate | AISHELL-1 只能标记为 Mandarin ASR/runtime sanity；报告里不得把它计入 meeting acoustics、speaker overlap、speaker turn 或 product value evidence。 |
| Exclusion gate | MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 只能 observed；WenetSpeech、Bilibili、YouTube、播客、公开视频、公开课、技术大会录播和第三方不明重包数据必须 excluded。 |

`data/asr_eval/public_sample_plan.example.json` 当前使用 AISHELL-4 是 schema example，不代表执行优先级高于 AliMeeting。`expected_sha256_after_extract` 是人工复核 manifest 字段；如果未来批准真实抽取，还必须在 post-extraction run report 里记录 observed clip sha256，再进入 ASR。

## 4. 合成中文技术会议策略

公开数据不覆盖软件工程评审语义，所以必须保留自建脚本和合成音频层。

当前已落地 5 个脚本：

| 脚本 | 目的 |
| --- | --- |
| `api-review-001` | API 兼容性、错误码、字段、P99、owner 缺口 |
| `release-review-001` | 灰度、回滚、监控、上线窗口 |
| `incident-review-001` | 告警、根因、止血、复盘和责任人 |
| `architecture-review-001` | 依赖、容量、缓存、扩展性和风险 |
| `non-engineering-control-001` | 非工程会议必须 0 工程卡 |

每个工程脚本必须继续保留：

- `technical_entities`
- `expected_state_events`
- `expected_gap_candidates`
- `expected_suggestion_cards`
- transcript-only baseline
- summary-only baseline
- 10-30 秒触发窗口
- useful / would_have_asked / too_late / too_intrusive / wrong 等反馈标签

合成音频不是为了证明真实语音体验，而是为了构建可重复、可控、可回归的技术会议输入层。后续要逐步加入：

- 多说话人音色。
- 轻微抢话和重叠。
- 停顿、口头禅、修正表达。
- 键盘声、空调声、会议室混响。
- 中英混合技术词高密度片段。

## 5. ASR Provider 验证顺序

### 5.1 默认顺序

1. `mock_streaming`
   - 只验证 event contract、state/gap/card pipeline。
   - 不代表真实 ASR。

2. `sherpa_onnx_streaming`
   - 用作本地低成本性能基线。
   - 当前已证明速度可用，但技术词质量不足。

3. `funasr_streaming`
   - 中文质量主候选。
   - 下一步只使用已有本地环境或已有缓存模型；若模型缺失，记录 blocked，不静默下载。

4. remote ASR
   - 非默认。
   - 仅当本地 ASR 达不到最低阈值且用户明确选择高质量模式时，作为对照或可选能力。

### 5.2 指标门槛

ASR 层至少记录：

- duration seconds
- RTF
- first partial latency
- final latency P95
- segment count
- raw CER
- normalized CER
- raw/normalized technical entity precision
- raw/normalized technical entity recall
- CPU peak percent
- memory peak MB
- error/end_of_stream 事件完整性

进入 first real-mic pilot 的最低门槛：

- 至少 2 个工程场景 normalized technical entity recall 达到或非常接近 >= 0.8，其中至少一个应包含中英混合技术实体。
- technical entity precision 必须同步记录；产品目标仍是 precision/recall >= 0.9，或有清晰可实现的 hotword/normalizer/LLM 修正路径。
- final/revision 可稳定生成 EvidenceSpan。
- 非工程 control 不产生工程卡。
- 公开/合成链路不会读取真实用户音频、`configs/local` 或调用远程 ASR/LLM。

产品目标：

- 技术实体 precision/recall >= 0.9，或有清晰可实现的 hotword/normalizer/LLM 修正路径。
- card latency <= 30 秒。
- 每 60 分钟 3-8 张有效卡。
- useful / would_have_asked >= 40%。
- wrong / too_late / too_intrusive <= 20-25%。

## 6. 后续执行计划

### Phase A: 完成合成音频 ASR 候选验证

状态：进行中。

已完成：

- 5 个合成中文技术会议脚本。
- 本地 TTS smoke。
- sherpa 本地 ASR smoke。
- synthetic ASR smoke report。
- FunASR synthetic smoke readiness report。
- FunASR streaming offline guard：`transcribe_funasr.py --streaming` 现在必须传入显式 `--local-model-dir`；缺失或目录不完整时返回 blocked，不构造 `AutoModel`。
- DRV-019 FunASR 模型下载审批包：`code/asr_runtime/funasr-model-download-approval.policy.json` 和 `tools/funasr_model_download_approval_packet.py` 已把 ModelScope/iic 模型、约 840MB 下载风险、手动执行边界、清理策略和 post-download verification order 固定为静态报告；当前仍不下载模型、不运行 FunASR smoke。
- 5 脚本 synthetic audio batch smoke：macOS `say` + `afconvert` 已生成 5 个 16kHz mono wav；sherpa-onnx batch baseline 已完成，RTF 约 0.029-0.035。确定性 normalizer 增量后，`api-review-001` normalized technical entity recall 从 0.25 提升到 0.5，但 4 个工程脚本整体仍只有 0-0.5，未达 first-pilot 门槛。详见 `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`。

下一步：

1. FunASR runtime cache 当前缺失，不能继续执行真实 FunASR smoke；后续必须提供已验证的本地模型目录，或明确批准并手动完成 DRV-019 模型下载。
2. 继续保持 `safe_to_download_models=false`，不再裸跑 `AutoModel(model="paraformer-zh-streaming")`。
3. 若 runtime cache/本地模型目录后续就绪，把 FunASR/sherpa 输出统一成 `synthetic_asr_smoke_report`。
4. 根据 batch smoke 缺失实体补 hotword/normalizer 规则，但不能用硬编码答案造假。
5. 让 `api-review` 至少在 normalized technical entity recall 上接近 first-pilot 门槛。

### Phase B: 公开授权会议音频小样本验证

状态：已完成来源研究和 no-download plan，未下载。

下一步：

1. 优先选择 AliMeeting Eval，其次 AISHELL-4 test，不再把 B 站/YouTube/播客/公开视频作为自动评测候选；MagicHub Web Meeting、MagicData-RAMC 和 Common Voice zh-CN 仅保留人工观察，不进入自动下载或抽样；WenetSpeech 明确排除。
2. 先运行 `public_audio_source_whitelist`，再生成具体 sample extraction plan，锁定 archive、archive member path、clip start/end、目标目录、duration cap、byte cap、checksum 和清理策略。
3. planned samples 必须先过 schema 校验；校验通过只表示可人工复核，`safe_to_download_now` 和 `safe_to_extract_now` 仍为 false。
4. 只抽 3-5 段 30-120 秒公开会议音频；如果官方包体量导致抽样本身不可控，则记录 blocked，不下载，不通过非白名单候选绕路。
5. 文件直喂本地 ASR，记录 RTF、latency、CER/实体指标。
6. 不用公开音频判断工程建议卡片价值。

### Phase C: 桌面 runtime 主链路

状态：历史状态已 superseded。PCWEB-118 已完成受控 `cargo check`，PCWEB-119 已完成真实 Tauri no-op WebView IPC evidence，PCWEB-120 已完成从真实 Tauri evidence 到同 session worker mic source manual review packet 的桥接，PCWEB-121 已完成最小 mic adapter implementation boundary，PCWEB-122 已完成 ASR worker real mic source boundary，PCWEB-123 已完成单次 worker mic source approval evidence，PCWEB-124 已把 readiness blocker 展示到 Web 工作台；当前 blocker 仍是 ASR quality 默认未退出，除非提供本地 FunASR/DRV-019/远端对照/显式降级试点证据。

下一步：

1. 不重复做已完成的 Rust/Cargo readiness、`cargo check`、Tauri no-op window 或 no-op IPC validation。
2. 不重复做已完成的最小 mic adapter implementation boundary。
3. 继续保持 ASR quality exit 路径：FunASR 本地模型目录、DRV-019 审批、可选远程 ASR 对照或明确降级取舍。
4. 在不访问麦克风、不请求权限、不启动真实 worker、不写真实 audio chunk 的边界内，推进 ASR worker real mic source 前置实现。
5. 真实麦克风会议仍只能在 readiness gate 满足后由用户最终显式启动。

### Phase D: 本地麦克风与 ASR worker

状态：未开始。

进入条件：

- desktop shell 可运行。
- native IPC/no-op 已验证。
- mic adapter 支持用户显式 start/pause/resume/stop/delete。
- 本地 ASR worker 输出统一 `partial/final/revision/error/end_of_stream`。
- chunk 只写 ignored runtime root。
- 默认不上传原始音频，默认不调用远程 ASR。
- 真实麦克风会议由用户最终显式启动。

- Phase A/B 至少证明 ASR 候选和 event contract 可用。
- Tauri no-op window 和 IPC 已跑通。

目标：

- 用户点击后才 start/pause/resume/stop。
- chunk 写入 ignored local runtime 目录。
- 本地 ASR worker 输出 `partial/final/revision/error/end_of_stream`。
- UI 展示 transcript、EvidenceSpan、状态候选和 gap candidate。

### Phase E: Controlled LLM suggestion cards

状态：禁用，未调用。

进入条件：

- ASR final/revision 和 gap candidate 稳定。
- 非工程 control 0 卡。
- card 频率、cooldown、schema、EvidenceSpan 裁剪和成本预算已设置。

目标：

- 使用 OpenAI-compatible 中转站。
- 只发送必要 EvidenceSpan 和结构化状态。
- 低频生成建议卡，不刷屏。
- 每张卡可反馈 useful/wrong/too_late/too_intrusive。

### Phase F: 用户真实麦克风会议验证

状态：由用户最终执行。

进入条件：

- 本地桌面 app 可运行。
- 麦克风采集和本地 ASR worker 可用。
- 合成/公开音频链路的质量风险已收敛到可试用范围。
- 用户明确选择一场真实会议做 shadow test。

验收输出：

- transcript。
- ASR metrics。
- state timeline。
- card timeline。
- 用户反馈。
- Go / Pivot / Stop 决策。

## 7. 停止继续扩大评估的规则

为了避免“永远在评测”，后续评估只允许服务于以下问题：

1. ASR 是否能保留中文技术实体。
2. final/revision 是否能及时产生 EvidenceSpan。
3. state/gap/card 是否比转写和会后总结更早发现会议缺口。
4. 桌面端是否能真实采集音频并运行。
5. 用户真实会议中卡片是否有用。

不再作为主线继续做：

- 只证明 `safe_to_execute=false` 的新 readiness 阶段。
- 无真实链路推进的 provider 横评。
- 和 Mac-first PC MVP 无关的 iOS/Android/应用市场工作。
- 版权不清公开视频抓取。
- 默认远程 ASR 接入。

## 8. 下一轮只读/本地/零费用运行手册

下一轮执行只允许推进三件事，每件事都必须在报告里明确“不会做什么”：

### 8.1 FunASR offline guard

目的：

- 证明 FunASR synthetic smoke 不会因为缺 runtime cache 而静默下载模型。
- 2026-07-03 已实现执行器层 guard：缺 `--local-model-dir` 或目录不完整时，streaming 模式直接 blocked。

允许：

- 只检查 committed synthetic audio、FunASR venv、runtime cache 状态和输出目录策略。
- runtime cache 缺失时直接 `blocked`。
- 本地模型目录就绪时，只把目录路径传给 FunASR 内部，不在报告中回显本机绝对路径。

禁止：

- 不调用 `AutoModel(model="paraformer-zh-streaming")` 这类可能自动下载的路径。
- 不下载模型，不复制 840MB 级缓存，不调用远程 ASR/LLM，不读取真实用户音频，不读取 `configs/local/`。

### 8.2 Synthetic audio smoke

目的：

- 用自建中文技术会议脚本生成本机合成音频，验证 ASR event contract、技术实体、EvidenceSpan、state/gap/card 前置链路。

允许：

- 只读取 `data/asr_eval/synthetic_meetings/scripts/` 里的脚本。
- 只写 ignored `artifacts/tmp/synthetic_audio/`、`artifacts/tmp/asr_events/` 和 `artifacts/tmp/asr_reports/`。
- 先用 sherpa 做性能基线；FunASR 只有 offline guard 成立后才能跑。

禁止：

- 不访问麦克风，不读取 `data/asr_eval/local_samples/`，不读取 `data/local_runtime/`，不调用远程 TTS/ASR/LLM，不把生成音频提交到仓库。

### 8.3 Public audio sample plan

目的：

- 把已经找到的合法公开音频来源细化成可审查、可复现、可清理的小样本计划。

允许：

- 只读取官方网页元信息和已提交的 whitelist/plan JSON。
- 只输出计划；计划必须包含 source id、license、archive、clip 起止时间、duration cap、byte cap、checksum、target root 和 cleanup policy。

禁止：

- 不下载完整数据集，不抽取音频，不抓版权不清视频，不提交原始音频，不调用远程 ASR/LLM。

这三个动作完成后，如果仍无法证明中文技术实体质量接近 first-pilot 门槛，下一步应转向产品级取舍：本地模型下载审批、可选远程 ASR 对照，或缩小 MVP 范围；不再无限增加评测层。

## 9. 文档和测试追踪

本计划对应的主要文档：

- `docs/desktop-runtime-validation-and-audio-simulation-plan-2026-07-02.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/asr-zero-cost-and-private-audio-boundary.md`
- `docs/asr-synthetic-batch-smoke-result-2026-07-03.md`
- `docs/asr-evaluation-dataset.md`
- `docs/requirements-traceability-matrix.md`
- `docs/decision-log.md`
- `docs/project-stage-status-and-next-work-2026-07-02.md`

已实现或正在使用的工具和测试：

- `tools/public_audio_sample_extraction_plan.py`
- `tools/synthetic_meeting_script_report.py`
- `tools/synthetic_audio_generation_plan.py`
- `tools/synthetic_audio_local_tts_smoke.py`
- `tools/asr_event_generation_plan.py`
- `tools/synthetic_asr_smoke_report.py`
- `tests/test_public_audio_sample_extraction_plan.py`
- `tests/test_synthetic_meeting_scripts.py`
- `tests/test_synthetic_audio_generation_plan.py`
- `tests/test_synthetic_audio_local_tts_smoke.py`
- `tests/test_asr_event_generation_from_public_or_synthetic_audio.py`
- `tests/test_synthetic_asr_smoke_report.py`

当前临时 smoke 输出都在 `artifacts/tmp/`，不提交仓库。

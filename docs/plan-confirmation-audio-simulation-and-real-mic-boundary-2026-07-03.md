# Plan Confirmation: Audio Simulation Ownership and Real Mic Boundary

> 日期：2026-07-03  
> 状态：Accepted as current plan confirmation  
> 目的：回答“完整计划是否已经写下来、转写验证是否由我先通过网上公开音频和模拟完成、真实麦克风会议是否最终由用户验证”。  
> 边界：本文档不授权下载公开音频大包、不授权抽取或转码公开音频、不授权访问麦克风、不授权读取真实用户录音、不授权读取 `configs/local/`、不授权读取 `data/asr_eval/local_samples/`、不授权下载 FunASR/ModelScope 模型、不授权调用远程 ASR/LLM。

## 1. 结论

完整计划已经写下，且已经有总控入口：

- `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`
- `docs/current-mainline-index.md`
- `docs/public-audio-synthetic-asr-and-real-mic-validation-plan-2026-07-03.md`
- `docs/asr-public-audio-and-simulation-next-run-plan-2026-07-03.md`
- `docs/project-current-status-and-forward-plan-2026-07-03.md`

当前需要修正的不是“缺计划”，而是避免后续执行被 ASR 质量评测、公开音频来源检索和 readiness 文档带成循环。后续每轮必须证明自己在推进以下主线：

```text
中文技术会议实时 Copilot
  -> 本地/开源 ASR 产生 partial/final/revision/error/end_of_stream
  -> EvidenceSpan
  -> meeting state / engineering gap candidate
  -> suggestion candidate/card
  -> desktop runtime / ASR worker handoff
  -> 用户真实麦克风 shadow test
```

## 2. 责任边界

### 我负责的前置验证

- 联网寻找并复核授权清楚的公开中文会议音频来源。
- 建立公开音频白名单和 bounded sample manifest，不抓版权不清来源。
- 用自建中文技术会议脚本和本地合成音频做可重复模拟。
- 用本地 ASR provider 输出统一事件，并跑产品价值 tri-lane gate。
- 把 ASR event file/worker handoff 接到 Web Live ASR pipeline。
- 输出 transcript、ASR metrics、EvidenceSpan/state/candidate/card timeline 和阶段结论。

### 用户最终负责的真实验证

- 真实麦克风会议 shadow test 由用户最终执行。
- 用户必须在桌面端显式点击 start 后才允许采集。
- 真实会议验证前必须完成 start/pause/resume/stop/delete、ignored runtime audio root、默认不上传原始音频、默认不远程 ASR、导出和反馈标注。
- 真实会议后形成 Go / Pivot / Stop 决策。

## 3. 多 Agent 审查结论

2026-07-03 已启动子 Agent 做只读对抗审查。结论一致：

- 计划已经落档，不是散点想法。
- 主线没有偏离，仍是中文技术会议实时 Copilot，不是普通音频转文字。
- 当前真正缺口是“可用的中文技术实体 ASR 质量”和“可运行的桌面端闭环”。
- 公开音频计划当前只能执行到白名单审查和 no-download sample plan，还不是 download-ready。
- 下一步应停止泛化 provider 横评、版权不清音频搜索和更多 report-only readiness 文档。

## 4. 公开音频策略

当前自动计划工具白名单只包含：

| 来源 | 用途 | 当前状态 |
| --- | --- | --- |
| AliMeeting / OpenSLR SLR119 | 真实多人会议、near/far-field、切句和事件链路 | 最高优先级，no-download sample manifest only |
| AISHELL-4 / OpenSLR SLR111 | 多人、远场、多通道、重叠说话补充 | 第二优先级，no-download sample manifest only |
| AISHELL-1 / OpenSLR SLR33 | 普通话 ASR/runtime sanity check | 非会议，不证明产品价值 |

THCHS-30 只作为观察/低优先级备选，不进入当前自动白名单。MagicHub Web Meeting、MagicData-RAMC / OpenSLR SLR123 和 Mozilla Common Voice zh-CN 只作为 observed-but-not-whitelisted，不进入自动下载、抽样、转码或产品价值 gate。WenetSpeech / OpenSLR SLR121 明确排除，因为其音频依赖 YouTube/podcast 等平台来源，和本项目不抓取平台音频、播客或版权链不清录音的边界冲突。

下一步公开音频只允许做一件事：

```text
AliMeeting Eval 优先，AISHELL-4 test 其次
  -> 形成 3-5 个 30-120 秒 clip 的 planned sample manifest
  -> 人工复核 official URL / archive / member path / clip range / sha256 / license citation / cleanup
  -> 仍然 safe_to_download_now=false
```

如果必须下载 GB 级整包且无法限定字节上限，就记录 blocked，不再继续扩源，也不转向 Bilibili、YouTube、播客、公开课、直播回放或版权链不清来源。

## 5. 模拟转写策略

模拟转写分三层：

- Perfect transcript lane：证明产品智能层能发现工程缺口。
- Mock ASR lane：证明 streaming event contract、revision、SSE/UI 和 scheduler 可用。
- Real ASR lane：用本地 ASR 输出验证中文技术实体、latency、RTF、EvidenceSpan 和 gap candidate。

当前结论：

- perfect/mock lane 已经 5/5 ready。
- 非工程 control 当前保持 0 candidate。
- real sherpa ASR lane 仍是 `blocked_by_asr_quality`。
- sherpa 只保留为性能基线。
- FunASR/Paraformer 是中文质量主候选，但需要本地模型目录或明确模型下载审批。

## 6. 下一步锁定

当前下一步不再是继续写评估报告，而是收敛到可停止工作。2026-07-03 状态同步：PCWEB-098、PCWEB-099、PCWEB-100、PCWEB-101、PCWEB-102、PCWEB-103 和 PCWEB-104 已经完成；后续不直接进入真实麦克风，必须另起明确计划和 TDD。`PCWEB-104` 的 readiness 指针只是当前 no-dispatch boundary 状态标记，不代表 PCWEB-104 仍是未来任务。

1. **Public audio sample manifest**  
   只做 AliMeeting Eval 或 AISHELL-4 test 的 3-5 个 planned samples no-download manifest。做不到就 blocked。

2. **ASR quality decision**  
   FunASR 本地模型目录就绪则跑一次 synthetic smoke；否则停在 DRV-019 Need model approval，不裸跑 ModelScope 自动下载。

3. **Tauri no-op run 或 mic adapter contract**  
   只有在工具链、target 目录、IPC no-op 和用户 start/pause/resume/stop/delete 语义都明确后推进；真实麦克风仍由用户最终启动和验证。

4. **硬停止线**  
   不再新增只证明 `safe_to_execute=false` 的横向 readiness 文档；下一张执行票必须直接服务 ASR 质量取舍、公开音频 no-download manifest、桌面可运行 no-op IPC 或麦克风 adapter 合同。

## 7. 不再做什么

- 不继续泛化 ASR/provider 横评。
- 不继续搜版权不清音频。
- 不把公开音频当产品价值证明。
- 不把更多 readiness/report-only 文档当主线进展。
- 不从 `<unk>` 或缺失 ASR 文本猜技术实体。
- 不默认启用远程 ASR。
- 不访问麦克风或真实录音。

## 8. 每轮报告必须回答

后续每轮主线报告必须回答：

1. 技术实体 normalized recall 是否改善，或为什么仍 blocked。
2. final/revision 是否能在 10-30 秒窗口形成 EvidenceSpan 和工程 gap candidate。
3. 非工程 control 是否仍为 0 工程 state/candidate/card。
4. 本轮是否推进了 desktop runtime、ASR worker handoff、mic adapter 或真实 pilot 前置条件。
5. 本轮是否引入任何额外费用、远程调用、模型下载、真实音频读取或麦克风权限。

如果无法回答前 4 个问题，本轮不算推进主线。

## 9. 本轮用户确认的执行边界

用户明确确认：转写相关验证由我先通过网上公开授权音频来源审查和模拟音频自测完成；最终真实麦克风会议由用户再验证。

2026-07-03 最新补充确认：这不是要求我现在访问麦克风或读取真实会议录音，而是要求我先完成可审计的公网音频来源复核、合成音频/模拟转写自测和本地 ASR 事件链路验证；真实麦克风会议作为最后 shadow test，由用户在产品前置条件满足后显式启动。

因此后续默认执行顺序为：

```text
官方公开音频来源复核 / no-download manifest
  + 自建中文技术会议脚本 / 合成音频
  -> 本地 ASR event 自测
  -> EvidenceSpan / gap / card pipeline
  -> desktop runtime / worker protocol / mic adapter
  -> 用户真实麦克风会议 shadow test
```

当前仍不授权下载公开大包、读取真实录音、访问麦克风、自动下载模型或调用远程 ASR/LLM。

## 10. 最新执行复核

2026-07-03 用户再次确认：转写类验证需要我先通过网上公开音频来源和模拟完成，最终真实麦克风会议由用户验证。本确认页的执行口径保持不变，并补充本轮证据：

- 已复核 OpenSLR 官方页面：AliMeeting / SLR119 是 CC BY-SA 4.0 的中文多通道会议语料；AISHELL-4 / SLR111 是 CC BY-SA 4.0 的中文会议场景语料；AISHELL-1 / SLR33 是 Apache License v2.0 的普通话语音语料，不是会议。
- 已重跑 no-download 工具链：source whitelist passed；AliMeeting sample extraction plan 返回 `blocked_no_planned_samples`；planned sample manifest decision 返回 `blocked_no_verified_public_sample_manifest`。
- 已验证相关测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_public_audio_source_whitelist.py tests/test_public_audio_sample_extraction_plan.py tests/test_public_audio_planned_sample_manifest_decision.py -q -p no:cacheprovider`，结果为 `25 passed, 1 warning`。

所以当前不是缺计划，也不是需要我马上找任意公网音频下载。正确动作是继续用官方白名单来源做 no-download manifest 约束，用自建中文技术会议脚本/合成音频/Mock events 做可重复转写模拟；真实麦克风 shadow test 保持为最后由用户显式启动的验收动作。

## 11. 2026-07-03 二次确认与收敛结论

用户再次追问“完整计划是否写下来、转写是否需要我先网上找音频和模拟、最终真实麦克风会议由用户验证”。本轮结论固定如下：

- 完整计划已经写下，主入口仍是 `docs/complete-forward-plan-audio-simulation-and-real-mic-2026-07-03.md`、`docs/current-mainline-index.md` 和本文档。
- 两个只读审查 Agent 的结论一致：当前没有明显计划缺口，缺的是执行态的中文技术实体 ASR 质量、真实 mic adapter、ASR worker real mic source 和真实 shadow-test 前置清单；PCWEB-119 已补齐真实 Tauri no-op run，PCWEB-120 已补齐同 session worker mic source manual review packet，但 worker mic source 仍未批准。
- 网络音频来源只能使用可复核的官方授权来源。当前自动白名单仍是 AliMeeting / OpenSLR SLR119、AISHELL-4 / OpenSLR SLR111 和 AISHELL-1 / OpenSLR SLR33；公开视频、Bilibili、YouTube、播客、直播回放、公开课和授权链不清录音不得进入自动评测。
- 公开音频阶段已按计划阻断在 no-download manifest：`tools/public_audio_source_whitelist.py` 通过，但 `tools/public_audio_sample_extraction_plan.py --source-id alimeeting_openslr_slr119 --target-root artifacts/tmp/public_audio` 返回 `blocked_no_planned_samples`，`tools/public_audio_planned_sample_manifest_decision.py` 返回 `blocked_no_verified_public_sample_manifest`。
- 模拟转写链路继续使用自建中文技术会议脚本、合成音频、mock streaming events 和 approved synthetic event file；本轮相关测试 `tests/test_asr_live_pipeline_replay.py tests/test_asr_event_generation_from_public_or_synthetic_audio.py tests/test_synthetic_audio_generation_plan.py tests/test_synthetic_audio_batch_smoke.py` 为 `24 passed, 1 warning`。
- ASR 质量 gate 仍返回 `requires_funasr_model_dir_or_drv019_approval`，原因是 sherpa real lane 中文技术实体召回不足，FunASR 本地模型目录缺失且 DRV-019 模型下载未获显式审批。
- 真实麦克风会议仍由用户最终执行，且只能在 desktop runtime、worker handoff、mic adapter start/pause/resume/stop/delete、导出和反馈链路满足后进入。
- 下一步默认不再做公开音频泛搜或 report-only 评估，应转向最小真实 mic adapter implementation boundary、真实麦克风 shadow-test 前置清单，或 ASR quality decision 的退出动作。

# Public Chinese ASR Baseline Report

日期：2026-07-10

状态：已完成小样本中文 ASR 基线；不再继续下载更多公开音频；已把有效优化收敛为产品双 ASR 路线。

## 一句话结论

下载公开音频不是为了下载，也不是产品功能。它的作用是提供一组可复现、可对比、可追溯的中文 ASR 标尺，避免只凭主观感受判断“中文识别差”。本轮已经足够暴露问题，也已经给出收敛方向：实时会议继续走流式 ASR 并采用中文会议 balanced profile，会后录音/导入走离线高质量 ASR + 标点模型。

## 为什么需要公开音频

公开音频只用于研发评测，不进入产品数据链路，原因有三点：

1. 真实麦克风测试受环境、说话人、外放音量影响很大，同一次测试很难复现。
2. 公开样本带标准文本时可以计算 CER，能量化“优化前后到底有没有变好”。
3. 在不读取用户隐私音频、不调用远端 ASR、不额外付费的前提下，可以先定位中文 ASR 的硬问题。

因此这一步的边界是：用小样本建立中文基线，跑出指标和错误画像，然后停止下载，进入修复和复测。

## 本轮实际使用的样本

本轮没有下载 AliMeeting 或 AISHELL-4 的 GB 级大包。实际只使用了小样本：

- Open Speech Repository Mandarin Chinese：4 段普通话 8k WAV，有公开测试句文本，可计算 CER。
- MagicHub 中文 web meeting 样例：1 段约 64 秒中文会议样例，只做定性会议声学观察，不作为发布 Go 证据。

安全边界：

- `remote_asr_call_count=0`
- `llm_call_count=0`
- `raw_audio_uploaded=false`
- 不读取用户真实录音
- 不读取 `configs/local`
- 不提交原始音频到 git

## 基线结果

报告产物：

- JSON 报告：`artifacts/tmp/asr_reports/public_chinese_asr_baseline_20260710.json`
- 报告工具：`tools/public_chinese_asr_baseline_report.py`
- 测试：`tests/test_public_chinese_asr_baseline_report.py`

摘要：

```text
item_count=5
referenced_item_count=4
qualitative_only_item_count=1
weighted_cer=0.047794
avg_cer=0.049955
max_rtf=1.169649
avg_rtf=0.999683
release_gate_status=needs_asr_optimization_before_release
```

带答案普通话样本的典型错误：

```text
邮局 -> 尤局
邮箱 -> 油箱
她用画笔 -> 他用画笔
蓝图 -> 狼图
校园依山环湖 -> 校园衣裳惶虎
散心 -> 上心
山间的小道 -> 三间的小道
春天来了 -> 窜天来了
白皑皑 -> 白皑矮
```

这些错误说明：当前本地流式 ASR 对中文常见词和短语仍有明显误识别。不能把这些公开样本句子硬编码进产品纠错规则，否则会变成“背测试集”，污染真实会议。

## 性能对照

单文件流式转写结果：

```text
OSR 4 段：RTF 约 0.91 - 1.02
MagicHub 会议样例：RTF 1.169649
```

单进程复用模型对照：

```text
model_load_latency_ms=5791
total_audio_duration_seconds=153.594
total_transcribe_latency_ms=166598
transcribe_only_rtf=1.084665

OSR_cn_000_0072_8k.16k: RTF 0.746382
OSR_cn_000_0073_8k.16k: RTF 0.759974
OSR_cn_000_0074_8k.16k: RTF 0.762046
OSR_cn_000_0075_8k.16k: RTF 0.845938
magichub_web_meeting_sample.16k: RTF 1.511296
```

判断：

- 复用模型能改善短音频性能，但会议样例仍明显超过实时阈值。
- 性能问题不是靠继续下载音频解决，而是要优化本地流式 runtime、chunk/final 策略、模型选择或 provider 路由。
- 如果本地方案无法把中文会议样例稳定压到 RTF < 1，生产级实时会议体验需要保留远端 ASR provider 口子，但默认不启用额外付费项。

## 本轮优化结果

本轮没有继续下载公开音频，而是用同一批样本验证两条产品路径：

1. 实时会议路径：Workbench 后端优先选择 `funasr_realtime`，sherpa 作为 fallback；FunASR sidecar 支持显式 chunk 参数，产品入口默认传入 `balanced_chinese_meeting` profile，对应 `chunk_size=0,30,15`。
2. 会后复盘路径：上传录音/导入文件改接 `transcribe_funasr.py --offline-batch`，优先使用本地缓存 SeACo Paraformer + VAD + 标点模型，保存 `post_meeting_asr_profile` 元数据，不调用远端 ASR，不新增付费项。

离线高质量路径的新结果：

```text
provider=offline SeACo Paraformer + VAD + punctuation
batch_mode=single_process_reused_funasr_offline_model
item_count=5
weighted_cer=0.014706
avg_cer=0.014910
max_rtf=0.063102
avg_rtf=0.048278
release_gate_status=baseline_passed_for_current_public_samples
remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
```

实时路径 chunk 对照：

```text
MagicHub meeting sample, original baseline: rtf=1.169649
FunASR streaming chunk 0,20,10: rtf=0.466800
FunASR streaming chunk 0,30,15: rtf=0.389919
```

产品式 sidecar 探针：

```text
artifact=artifacts/tmp/asr_reports/realtime_funasr_sidecar_public_probe_20260710_after_merge.json
provider=funasr_realtime
asr_profile=balanced_chinese_meeting
chunk_size=0,30,15
frontend_chunk_ms=300
duration_seconds=19.967625
rtf=0.552726
partial_count=11
final_count=1
final_text_chars=68
remote_asr_call_count=0
llm_call_count=0
raw_audio_uploaded=false
```

这次探针暴露了一个真实产品问题：旧 worker 在结束时只把最后一个 partial 当作 final，导致 final 丢掉大部分前文。本轮已修复为“实时 partial 仍原样发出，结束 final 使用本地合并后的 partial hypotheses”，避免会议结束后只保存最后一小段。

解释：

- `0,30,15` 明显改善 MagicHub 中文会议样例的 RTF，但 partial 更新会更粗，约 1.8 秒一个 worker 推理窗口。
- 这适合作为当前中文会议 balanced 默认值，因为产品核心是“实时可用 + 建议卡稳定触发”，不是逐字字幕竞速。
- 前端仍按 300ms 发送浏览器麦克风 PCM，worker 内部聚合后送 FunASR，避免重写前端 VAD/UI 计时。
- 实时 sidecar 的 final 现在会合并非累积 partial，避免会话结束时只留下尾段。
- 会后稿不再依赖实时流式假设，而是用离线高质量模型重新生成更准确的转写和标点。

## 决策

本轮决策如下：

1. 公开音频下载到此为止，除非后续有明确修复需要复测，不再扩展数据集。
2. 不把 OSR 普通话句子的错误直接硬编码进产品 normalizer。
3. 产品 normalizer 只继续保守修正中文技术会议中的上下文术语，例如 `P99`、`灰度`、`回滚`、`checkout-service`、`error_rate`。
4. 产品采用双 ASR 路线：实时会议用流式 ASR 驱动实时文字和建议卡；会后复盘/录音导入用离线高质量 ASR 生成完整文字稿。
5. ASR 优化主线转向真实产品链路：本地流式性能、中文会议准确率、实时文字可读性、LLM 建议卡时延。
6. 发布判断不能用公开音频替代真实麦克风最终验收；公开音频只是修复前后的可复现基线。

## 下一步

下一步不再做开放式测评，而是进入收敛修复：

1. 用真实麦克风/外放会议声继续跑 Workbench 全链路，观察 `balanced_chinese_meeting` 的实时文字延迟和建议卡触发。
2. 针对实时文字展示增加轻量标点/断句修复，提升会议中可读性。
3. 把会后高质量 ASR 结果用于录音复盘、导出 transcript/minutes 和后续全文检索。
4. 保留远端 ASR provider adapter 设计，但默认关闭，不新增付费成本。
5. 复跑当前 5 个中文样本和真实麦克风主链路，只有同一套样本指标改善才算优化有效。

## 当前回答你的质疑

不是为了下载而下载。这一步的价值已经产出：它证明了中文本地 ASR 当前的真实瓶颈是“普通话准确率和会议实时性需要分路径优化”，不是样本不够，也不是缺一个更大的数据集。当前结论已经落到产品代码：实时会议优先使用 FunASR 中文会议 balanced profile，会后复盘使用离线高质量 ASR；后续应该继续围绕真实麦克风主链路和页面体验验证，不再扩散数据集。

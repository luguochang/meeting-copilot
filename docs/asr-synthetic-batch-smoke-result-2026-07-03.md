# ASR Synthetic Batch Smoke Result

> 日期：2026-07-03  
> 状态：Completed local synthetic batch smoke  
> 范围：5 个中文合成会议脚本的本机 TTS 音频生成、sherpa-onnx 本地 ASR baseline、transcript report 和 synthetic ASR smoke report。  
> 边界：未读取真实用户音频，未读取 `configs/local/`，未调用远程 ASR/LLM/TTS，未下载公开音频或 FunASR 模型。

## 1. 一句话结论

本轮 batch smoke 证明：

```text
5 个 synthetic scripts
  -> macOS say + afconvert 生成 16kHz mono wav
  -> sherpa-onnx 本地 streaming 文件回放
  -> transcript report
  -> synthetic ASR smoke report
```

链路和速度稳定，但中文技术实体识别仍明显不达标。sherpa-onnx 可以继续作为本地性能基线，不能作为中文技术会议质量主候选。

2026-07-03 已追加 synthetic product value gate：4 个工程脚本均因 `normalized technical entity recall below first-pilot threshold` 被判定为 `needs_asr_quality_work`，非工程 control 判定为 `negative_control_passed`。这说明当前问题不是事件链路，而是 ASR 是否能保留足够工程实体来支撑 EvidenceSpan 和建议卡。

## 2. 执行内容

### 2.1 合成音频 batch

工具：

- `tools/synthetic_audio_batch_smoke.py`
- 复用 `tools/synthetic_audio_local_tts_smoke.py`

输入：

- `api-review-001`
- `architecture-review-001`
- `incident-review-001`
- `non-engineering-control-001`
- `release-review-001`

输出：

- `artifacts/tmp/synthetic_audio/*.aiff`
- `artifacts/tmp/synthetic_audio/*.wav`

结果：

- batch status: `generated`
- 5 个 wav 均为 RIFF/WAVE、16 bit、mono、16000 Hz。
- artifacts 均在 ignored `artifacts/tmp/` 下，不提交仓库。

### 2.2 sherpa-onnx batch baseline

模型：

- `code/asr_runtime/models/sherpa-onnx/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01`

输出：

- events: `artifacts/tmp/asr_events/*.sherpa.events.json`
- provider: `artifacts/tmp/asr_reports/*.sherpa.provider.json`
- transcript report: `artifacts/tmp/asr_reports/*.sherpa.transcript-report.json`
- smoke report: `artifacts/tmp/asr_reports/*.sherpa.smoke-report.json`

## 3. 指标结果

2026-07-03 后续 normalizer 增量：

- 新增 spoken error code 规则：`错误码...四万零一十二` -> `错误码...40012`。
- 新增/修正 committed technical glossary：`request id` -> `request_id`、`error rate` -> `error_rate`、`payment gateway` -> `payment-gateway` 等。
- 修复短 alias 把 `payment gateway` 误替换成 `payment-gatewayway` 的问题。
- 该增量只恢复文本中已有的数字或别名线索，不把 `<unk>` 猜成服务名。

| script | duration s | latency ms | RTF | final | EOS | normalized entity recall | first-pilot |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `api-review-001` | 17.202 | 568 | 0.033019 | 1 | 1 | 0.5 | false |
| `architecture-review-001` | 14.842 | 428 | 0.028837 | 1 | 1 | 0.0 | false |
| `incident-review-001` | 11.362 | 398 | 0.035029 | 1 | 1 | 0.0 | false |
| `non-engineering-control-001` | 13.491 | 417 | 0.030909 | 1 | 1 | 1.0 | true |
| `release-review-001` | 11.297 | 391 | 0.034611 | 1 | 1 | 0.25 | false |

说明：

- `non-engineering-control-001` 的 entity recall 为 1.0，是因为该脚本没有技术实体；这只能证明 control 不误报技术实体指标，不能证明 ASR 质量好。
- 5 个样本都有 final 和 end_of_stream，事件链路完整。
- RTF 全部远低于 1，说明 sherpa 的本地速度不是当前瓶颈。

## 4. 缺失实体

| script | matched | missing |
| --- | --- | --- |
| `api-review-001` | `40012`, `P99` | `payment-gateway`, `request_id` |
| `architecture-review-001` | none | `QPS`, `feature-store`, `mysql`, `recommendation-service`, `redis cluster` |
| `incident-review-001` | none | `lag`, `order-worker`, `timeout`, `监控阈值` |
| `release-review-001` | `P99` | `checkout-service`, `error_rate`, `staging` |

## 5. 典型原始转写风险

sherpa 输出中出现大量 `<unk>`，尤其集中在中英混合服务名、字段名、错误码、指标名：

- `payment-gateway` 被 `<unk> <unk>`。
- `request_id` 被漏掉。
- `40012` 原本被转成“四万零一十二”，当前 normalizer 已可在错误码上下文恢复为 `40012`。
- `feature-store`、`recommendation-service`、`redis cluster`、`checkout-service` 等均未保留。
- `error_rate`、`staging`、`order-worker`、`lag`、`timeout` 等工程词未恢复。

这会直接影响 EvidenceSpan、state/gap/card 的可信度。LLM 可以做二次修正，但如果 ASR 根本没有保留技术实体线索，LLM 修正容易变成猜测。

## 6. 产品判断

当前不能进入真实麦克风会议验证，原因不是速度，而是质量：

- first-pilot 最低门槛要求关键技术实体 normalized recall >= 0.8。
- 本轮 4 个工程脚本中，normalizer 增量后最高为 0.5，两个为 0。
- 产品目标是技术实体 precision/recall >= 0.9，当前差距明显。

### 6.1 Synthetic product value gate

工具：

- `tools/synthetic_product_value_gate.py`

该 gate 不运行模型、不读取真实音频、不调用远程 ASR/LLM，只读取 allowed synthetic smoke report 和 committed script JSON。它把 ASR 事件完整性、normalized technical entity recall、expected gap candidates、expected suggestion cards 和 non-engineering control 合并成产品阶段决策。

| script | decision | desktop runtime ready | real mic ready | gate failure |
| --- | --- | --- | --- | --- |
| `api-review-001` | `needs_asr_quality_work` | false | false | normalized recall 0.5 < 0.8 |
| `architecture-review-001` | `needs_asr_quality_work` | false | false | normalized recall 0.0 < 0.8 |
| `incident-review-001` | `needs_asr_quality_work` | false | false | normalized recall 0.0 < 0.8 |
| `release-review-001` | `needs_asr_quality_work` | false | false | normalized recall 0.25 < 0.8 |
| `non-engineering-control-001` | `negative_control_passed` | false | false | none |

结论：

- 5 个样本的 final / end_of_stream 事件链路完整。
- 4 个工程样本都不允许进入真实麦克风 pilot。
- 非工程 control 继续作为负控样本保留：当前 gate 确认脚本期望为 0 工程卡且不推进 desktop / real mic；实际 downstream card count 会在后续 state/gap/card artifact 接入后纳入 gate。
- 下一步应优先提升本地 ASR 质量、准备 FunASR 模型审批，或并行推进 desktop no-op runtime；不能把 sherpa 结果解释为 Copilot 产品价值已达标。

### 6.2 Live ASR pipeline replay gate

工具：

- `tools/asr_live_pipeline_replay.py`

该 gate 不运行模型、不读取真实音频、不调用远程 ASR/LLM，只读取 allowed `artifacts/tmp/asr_events/*.events.json`，并复用 Web Live ASR pipeline 检查 `transcript_final/revision -> EvidenceSpan -> state_event -> scheduler_event -> suggestion_candidate_event -> llm_request_draft_event` 是否能成立。

2026-07-03 复测结果：

| script | final | evidence | state | scheduler | candidate | request draft | card | llm |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `api-review-001` | 1 | 1 | 1 | 1 | 1 | 1 | 0 | not_called |
| `release-review-001` | 1 | 1 | 1 | 1 | 1 | 1 | 0 | not_called |
| `non-engineering-control-001` | 1 | 1 | 0 | 0 | 0 | 0 | 0 | not_called |

结论：

- ASR event JSON 已能实际接入 Web Live ASR pipeline，而不只是停留在 ASR smoke report。
- replay gate 暴露并修复了非工程误触发：原先 “大家是否方便” 会被识别为 OpenQuestion，“名单整理明天发群” 会被识别为 ActionItem；现在 OpenQuestion/ActionItem 本地抽取都要求工程上下文。
- 该结果证明无 LLM 的 EvidenceSpan/state/scheduler/candidate 链路可以从 ASR events 工作；仍不证明 ASR 技术实体质量达标，也不允许进入真实麦克风 pilot。

可确认的进展：

- 合成音频 batch 可复现。
- 本地 ASR event contract 可复现。
- 本地 ASR event 可以 replay 到 Live ASR pipeline，并形成可审计的 EvidenceSpan/state/scheduler/candidate/report。
- sherpa 性能基线稳定。
- normalizer 可恢复明确数字/别名线索，例如 `40012`、`P99`、`request_id`、`error_rate` 的可辨认 alias。
- 非工程 control 没有引入工程实体。

未解决风险：

- 中文技术词和中英混合实体识别。
- provider endpoint final 语义；当前每个合成样本仍只有 1 个 final。
- FunASR 本地质量验证仍被本地模型目录/cache 缺失阻塞。

## 7. 下一步

下一步不继续扩大 provider 横评，按以下顺序推进：

1. 保持 FunASR offline guard，不允许裸跑 alias。
2. 如果要验证 FunASR 质量，需要本地模型目录/cache 或明确模型下载审批。
3. 在不下载模型的前提下，先推进 hotword/normalizer/技术实体纠错，但不能硬编码答案造假。
4. 公开音频仍只做 bounded sample plan，不下载 GB 级大包。
5. 桌面 runtime 可以并行推进，因为当前 synthetic event 链路和 UI/core 链路已有基础，但真实麦克风验证必须等 ASR 质量风险收敛。

## 8. 验证命令

Focused tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/asr_runtime/tests/test_transcribe_funasr.py \
  tests/test_funasr_synthetic_smoke_readiness.py \
  -q -p no:cacheprovider
```

ASR runtime tests:

```bash
cd code/asr_runtime
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q -p no:cacheprovider
```

Synthetic batch smoke tests:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_synthetic_audio_batch_smoke.py \
  tests/test_synthetic_audio_local_tts_smoke.py \
  tests/test_synthetic_asr_smoke_report.py \
  tests/test_synthetic_product_value_gate.py \
  -q -p no:cacheprovider
```

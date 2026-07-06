# ASR Runtime

本目录用于本地 ASR 可行性自测，和 `asr_bakeoff` 评测工具分开，避免模型依赖污染评测脚手架。

原则：

- 独立 Python 环境。
- 模型文件放 `models/`，不提交。
- 转写输出放 `outputs/`，不提交。
- 真实录音放 `data/asr_eval/local_samples/`，不提交。
- 优先本地 ASR，默认不使用付费云 ASR。

## 当前目标

1. 检查真实音频元数据。
2. 建立本地 ASR runtime。
3. 优先尝试 sherpa-onnx 或 FunASR。
4. 输出 transcript JSON。
5. 调 LLM 中转站生成会议分析。

## 当前能力

- `inspect_audio.py`：检查 WAV 元数据。
- `transcribe_sherpa_onnx.py`：sherpa-onnx streaming event 适配和 provider JSON 输出。
- `transcribe_funasr.py`：FunASR 文件转写和文件回放式 streaming event 适配。
- `transcript_report.py`：Provider JSON 转 TranscriptReport/EvidenceSpan。
- `transcript_normalizer.py`：中文技术术语和数字归一化。
- `meeting_analysis.py`：通过 OpenAI-compatible LLM 中转站生成结构化会议状态和建议卡片。
- `incremental_scheduler.py`：限制会中 LLM 调用频率，避免每个 ASR partial 都触发 LLM。
- `streaming_contract.py`：定义 streaming `partial/final/revision/error/end_of_stream` 到 provider transcript 的契约。
- `realtime_simulation.py`：用可控 streaming events 验证 EvidenceSpan 输入和 LLM 调度次数。

## Realtime contract smoke

```bash
python3 scripts/realtime_simulation.py \
  --events-json ../../configs/asr_providers/mock-streaming-events.release-review.json \
  --provider mock-stream \
  --min-final-interval-ms 30000 \
  --min-state-change-interval-ms 10000 \
  --max-calls-per-hour 80 \
  --state-change-segment-id seg_owner_gap
```

该命令只证明 streaming 契约和调度器可控，不代表真实 ASR provider 质量达标。

## sherpa-onnx event smoke

```bash
.venv-sherpa/bin/python scripts/transcribe_sherpa_onnx.py \
  outputs/simulated-release-review.16k.wav \
  --model-dir models/sherpa-onnx/sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01 \
  --num-threads 2 \
  --chunk-ms 500 \
  --events-output outputs/simulated-release-review.sherpa.streaming-events.json \
  > outputs/simulated-release-review.sherpa.streaming-provider.json
```

当前 smoke 证明 sherpa 可以用文件回放方式产出 streaming events 并接入 scheduler；但这还不等于桌面实时音频采集，且短样本仍只有 1 条 final segment，不能视为多 chunk final 达标。

## FunASR streaming event smoke

```bash
.venv-funasr/bin/python scripts/transcribe_funasr.py \
  outputs/simulated-release-review.16k.wav \
  --streaming \
  --model paraformer-zh-streaming \
  --local-model-dir <absolute-local-funasr-model-dir> \
  --device cpu \
  --chunk-size 0,10,5 \
  --encoder-chunk-look-back 4 \
  --decoder-chunk-look-back 1 \
  --final-window-ms 3000 \
  --events-output outputs/simulated-release-review.funasr.streaming-events.json \
  > outputs/simulated-release-review.funasr.streaming-provider.json
```

该命令按 FunASR streaming 参数把本地文件切成 chunk 回放，产出统一 `StreamingTranscriptEvent`，并不代表已经完成 macOS 麦克风/系统音频实时采集。`--local-model-dir` 是必填的 offline guard；缺失或目录不完整时脚本返回 `status=blocked`，不会构造可能自动下载模型的 `AutoModel`。

当前本机 warm run 关键结果：

```json
{
  "model_id": "paraformer-zh-streaming",
  "finalization_strategy": "fixed_window_from_partial_hypotheses",
  "provider_endpoint_finals": false,
  "partial_event_count": 30,
  "final_event_count": 6,
  "end_of_stream_event_count": 1,
  "segment_count": 6,
  "audio_duration_seconds": 17.929,
  "latency_ms": 18864,
  "rtf": 1.05215
}
```

调度器验证：

```json
{
  "llm_trigger_count": 1,
  "decision_reasons": {
    "partial_ignored": 30,
    "final_segment": 1,
    "cooldown": 5,
    "control_event_ignored": 1
  }
}
```

结论：

- FunASR streaming 文件回放能产出多条窗口化 final segment，比当前 sherpa 短样本只有 1 条 final 更能锻炼 EvidenceSpan 和 scheduler 链路；但这些 final 仍是适配层窗口切分，不是 provider endpoint final。
- scheduler 正确忽略所有 partial，没有按 partial 调 LLM。
- warm run RTF 约 1.05，已接近实时边界但仍偏重；首次运行还会下载约 840MB online 模型。
- 技术词仍有明显错识别，例如 `payment-gateway` 和 `P99`，因此不能跳过 normalizer/stabilizer，也不能宣称中文技术会议质量达标。

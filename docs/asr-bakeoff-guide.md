# 中文技术会议 ASR Bake-off 使用说明

> 日期：2026-06-18  
> 目标：先用评测验证中文实时识别质量，再决定是否进入实时 Copilot 实现。

## 1. 为什么先做 bake-off

中文技术会议的难点不是“能不能转文字”，而是：

- 技术词、服务名、接口名、字段名是否能识别。
- 中英混合是否稳定。
- 实时延迟是否能跟上会议节奏。
- ASR 错误是否会污染实时建议。
- 热词能否显著改善内部术语。

如果 ASR bake-off 不达标，产品应先做会后结构化纪要，不应贸然做实时 Copilot。

成本边界：

- MVP 默认不引入远程 ASR 付费链路。
- 本地/开源 ASR 是默认候选。
- 远程 ASR 只用于质量对照、可选高质量模式或企业自选 provider。
- 任何远程 ASR 费用都必须显式展示，不能隐藏在默认链路中。

## 2. 当前工作台能力

代码位置：

```text
meeting-copilot/code/asr_bakeoff
```

当前支持：

- `manifest.json` 样本清单校验。
- reference 文本加载。
- annotation 技术实体加载。
- 中文 CER。
- 技术实体 precision/recall/F1。
- 延迟 P50/P95/max。
- mock provider 端到端评测。
- command provider：调用任意外部 ASR 命令并读取 JSON 输出。
- 样本级失败隔离：provider 单样本失败不会中断整轮评测。
- 不可评测语义：缺 reference/annotation 的样本不会被算成满分。

## 3. 运行测试

```bash
cd <repo>/code/asr_bakeoff
python3 -m pytest tests -q
```

当前验证输出：

```text
18 passed, 1 warning
```

## 4. 运行 mock bake-off

```bash
cd <repo>/code/asr_bakeoff
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke.json \
  --provider mock \
  --mock-transcripts ../../configs/asr_providers/mock-transcripts.json \
  --output ../../results/asr_bakeoff/smoke-mock.json
```

期望输出：

```json
{
  "sample_count": 1,
  "failed_sample_count": 0,
  "scored_cer_sample_count": 1,
  "scored_entity_sample_count": 1,
  "avg_cer": 0.0,
  "avg_entity_f1": 1.0,
  "latency": {
    "count": 1,
    "p50_ms": 0,
    "p95_ms": 0,
    "max_ms": 0
  }
}
```

结果文件：

```text
meeting-copilot/results/asr_bakeoff/smoke-mock.json
```

## 5. 运行多场景 smoke bake-off

当前已提供 4 个中文技术会议 smoke 样本：

- `S01-api-review`：API 评审。
- `S02-release-review`：上线 / 灰度评审。
- `S03-incident-review`：事故复盘。
- `S04-mixed-terms`：中英混排技术实体。

运行：

```bash
cd <repo>/code/asr_bakeoff
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke-multiscenario.json \
  --provider mock \
  --mock-transcripts ../../configs/asr_providers/mock-transcripts.json \
  --output ../../results/asr_bakeoff/smoke-multiscenario-mock.json
```

当前验证输出：

```json
{
  "sample_count": 4,
  "failed_sample_count": 0,
  "scored_cer_sample_count": 4,
  "scored_entity_sample_count": 4,
  "avg_cer": 0.0,
  "avg_entity_f1": 1.0,
  "latency": {
    "count": 4,
    "p50_ms": 0,
    "p95_ms": 0,
    "max_ms": 0
  }
}
```

注意：这是 mock provider 的管线验证结果，不代表真实 ASR provider 效果。

## 6. command provider

`command` provider 用于快速接入任意外部 ASR 程序。外部命令必须向 stdout 输出一个 JSON object。

命令参数支持两个占位符：

- `{sample_id}`
- `{audio_path}`

示例：

```bash
python3 -m asr_bakeoff.cli \
  --manifest ../../data/asr_eval/manifests/smoke.json \
  --provider command \
  --provider-name local-funasr \
  --command "python ../asr_runtime/scripts/transcribe_funasr.py {audio_path} --model paraformer-zh --device cpu --no-punc" \
  --output ../../results/asr_bakeoff/funasr-smoke.json
```

外部命令输出格式：

```json
{
  "text": "接口新增 trace_id 字段，需要兼容调用方。",
  "latency_ms": 1234,
  "segments": [
    {"start_ms": 0, "end_ms": 3000, "text": "接口新增 trace_id 字段，需要兼容调用方。"}
  ],
  "entities": ["trace_id", "兼容", "调用方"],
  "raw": {
    "provider_request_id": "optional"
  }
}
```

字段说明：

- `text` 必填。
- `latency_ms` 可选；不返回时由 runner 使用端到端 wall-clock 近似值。
- `entities` 可选；后续真实评测更建议使用统一实体抽取器，避免不同 provider 不公平。
- `raw` 可选；不得包含 API key 或敏感凭据。

## 7. 数据格式

### 7.1 Manifest

位置：

```text
meeting-copilot/data/asr_eval/manifests/smoke.json
```

格式：

```json
{
  "version": 1,
  "samples": [
    {
      "id": "S01-api-review",
      "audio_path": "../samples/S01-api-review.wav",
      "reference_path": "../references/S01-api-review.txt",
      "annotation_path": "../annotations/S01-api-review.annotation.json",
      "language": "zh-CN",
      "scenario": "api_review",
      "duration_seconds": 24.0
    }
  ]
}
```

### 7.2 Reference

人工参考转写，一行或多行均可，脚本会忽略首尾换行。

### 7.3 Annotation

当前最小标注：

```json
{
  "technical_entities": [
    {
      "type": "field",
      "text": "trace_id",
      "normalized": "trace_id",
      "start_ms": 3200,
      "end_ms": 4200
    }
  ],
  "decisions": [],
  "action_items": [],
  "risks": [],
  "gaps": []
}
```

## 8. 报告语义

summary 字段：

- `sample_count`：样本总数。
- `failed_sample_count`：provider 调用失败的样本数。
- `scored_cer_sample_count`：实际纳入 CER 平均的样本数。
- `scored_entity_sample_count`：实际纳入实体 F1 平均的样本数。
- `avg_cer`：无可评测样本时为 `null`，不会写成 `0.0`。
- `avg_entity_f1`：无可评测样本时为 `null`，不会写成 `1.0`。

sample 字段：

- `status`：`success` 或 `failed`。
- `error`：失败原因。
- `evaluation_status.cer`：`scored` 或 `not_evaluated`。
- `evaluation_status.entity_accuracy`：`scored` 或 `not_evaluated`。

## 9. Provider 接入计划

### Phase 1：离线/伪实时 provider

- FunASR local file provider。
- sherpa-onnx local file provider。
- whisper.cpp file provider baseline。

目的：

- 快速比较中文 CER 和技术实体准确率。
- 不先处理 WebSocket 实时协议复杂度。
- 先验证 raw ASR、normalized transcript 和下游 Copilot gate 的关系。

当前补充结论：

- 远程 ASR 不是 Phase 1 必接项，不阻塞 Mac MVP。
- 如果接入远程 ASR，只作为质量上界和成本/隐私评估样本，报告必须标记为 `remote_paid`。
- bake-off 报告必须同时记录 raw ASR 指标和 normalized 指标，不能只报告修正后的结果。

### Phase 2：本地 streaming provider

- FunASR streaming。
- sherpa-onnx streaming。
- SenseVoice streaming 或 second-pass 对照。

目的：

- 验证 partial/final/revision 延迟。
- 验证真实 final segment 是否能在 10-30 秒建议窗口内触发 Copilot。
- 验证热词和术语表是否能提升技术实体 recall。
- provider 必须返回 partial/final/revision 延迟统计。
- final segment 必须能转为统一 TranscriptSegment/EvidenceSpan。
- 报告必须区分 `file_segment`、`chunked_endpoint_file`、`streaming_final_segment`，避免把伪实时和真实 streaming 混为一谈。
- mock streaming provider 的结果必须标记为 `contract_test`，只用于契约和调度器回归。

### Phase 3：可选远程对照 provider

- 阿里/讯飞/腾讯/百度等远程 ASR。
- 企业自有 ASR 服务。

要求：

- 默认不开启。
- 配置必须显式提示额外费用和上传音频范围。
- 不作为默认产品依赖。
- 可优先通过 `command` provider 接入，避免一开始绑定 SDK。

### Phase 1.5：本地实时 provider

- FunASR streaming / Paraformer 中文流式模型。
- sherpa-onnx streaming 中文或中英模型。

目的：

- 验证不增加远程 ASR 费用时，是否能实时展示中文会议文字。
- 验证本地 CPU/GPU、模型体积、延迟和中文技术词准确率。
- 只有这一层不达标时，才考虑远程 ASR 作为对照或可选模式。

### Phase 2：远程实时 provider

- 阿里 Paraformer WebSocket。
- 讯飞实时转写。
- 腾讯云实时语音识别。
- 百度实时语音识别。
- OpenAI Realtime transcription。

目的：

- 作为质量对照，比较实时延迟、稳定性、热词、标点、时间戳。
- 不作为默认生产依赖。
- 不作为默认收费项。

### Phase 3：真实会议压测

- 10 段中文技术会议样本。
- 每段 5-60 分钟。
- 覆盖 API、上线、事故、架构、中英混合、多人插话、弱音质。

## 10. Provider 接口

当前 provider 最小接口：

```python
class AsrProvider:
    name: str

    def transcribe(self, sample_id: str, audio_path: Path) -> TranscriptResult:
        raise NotImplementedError
```

结果：

```python
@dataclass(frozen=True)
class TranscriptResult:
    text: str
    latency_ms: int = 0
    entities: list[str] = field(default_factory=list)
    raw: dict | None = None
```

真实 provider 后续应返回：

- full text。
- segment list。
- partial latency。
- final latency。
- word/segment timestamp。
- provider raw response。
- provider confidence。

Streaming provider 应提供等价事件接口：

```python
@dataclass(frozen=True)
class StreamingAsrEvent:
    provider: str
    session_id: str
    event_type: str  # partial | final | revision | error | end_of_stream
    segment_id: str
    text: str
    start_ms: int
    end_ms: int
    received_at_ms: int
    is_final: bool
    latency_ms: int
    confidence: float | None = None
    revision_of: str | None = None
    raw: dict | None = None


class StreamingAsrProvider:
    name: str

    def stream(self, audio_chunks: Iterable[bytes]) -> Iterator[StreamingAsrEvent]:
        raise NotImplementedError
```

事件处理规则：

- `partial` 只能用于实时预览或低风险候选信号，不能生成正式 EvidenceSpan。
- `final` 转为 `TranscriptSegment`，再转为 EvidenceSpan。
- `revision` 必须引用被修正 segment，并触发下游状态更新或降级。
- `error` 和 `end_of_stream` 必须进入质量状态和运行日志。
- 未知事件类型必须失败，不允许静默吞掉。

## 11. Go / No-Go 门槛

| 指标 | MVP 门槛 |
|---|---:|
| 中文 CER | <= 12% |
| raw 核心技术实体 recall | 必须单独记录 |
| raw 核心技术实体 precision | 必须单独记录 |
| normalized 核心技术实体 recall | >= 90% |
| normalized 核心技术实体 precision | >= 90% |
| 热词后核心术语准确率 | >= 90% |
| 中英混合实体准确率 | >= 85% |
| partial 延迟 P50 | <= 1.5s |
| partial 延迟 P95 | <= 2.5s |
| final 延迟 P95 | <= 3.5s |
| 句子时间戳误差 P95 | <= 1.5s |

No-Go：

- 任何 provider 都达不到中文技术词指标。
- LLM 修正经常编造。
- ASR 错误导致缺口雷达误报严重。
- 无法稳定给出时间戳证据链。

## 12. 下一步

当前 ASR bake-off 不是下一阶段主线，主线以 `docs/mainline-product-value-and-asr-validation-execution-plan-2026-07-03.md` 为准。

1. 先用 synthetic scripts 跑 perfect transcript / mock ASR / real ASR 三路产品价值 gate。
2. 公开音频只使用 OpenSLR 白名单来源，并且没有 3-5 个具体 clip manifest 时保持 `blocked_no_planned_samples`。
3. 用户真实会议音频只能由用户在最终 shadow test 阶段显式提供或现场采集，必须写入 ignored 本地 runtime root，不进入仓库，不作为默认 bake-off 前置项。
4. 本地 provider 对比只在产品价值 gate 或 ASR 质量阻塞明确后进行；远程 provider 只作为可选质量上界对照，不作为默认 MVP 阻塞项。
5. 所有 provider 对比表必须同时列出 raw 与 normalized 指标，并解释它们对 EvidenceSpan/gap/card 的影响。

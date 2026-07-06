# Copilot Product Value Batch Result

> 日期：2026-07-03  
> 状态：Implemented as DRV-026  
> 范围：5 个 synthetic meeting scripts 的 perfect transcript / mock ASR / real ASR 三路产品价值矩阵。  
> 边界：本结果不访问麦克风，不读取真实用户音频，不读取 `configs/local/`，不调用远程 ASR/LLM，不下载模型，不写 runtime audio。

## 1. 本轮结论

`tools/copilot_product_value_batch_gate.py` 已把 DRV-025 单场景 tri-lane gate 扩成 5 场景 batch summary。DRV-027 修复 `architecture-review-001` 架构评审产品逻辑覆盖后，本结果已更新。DRV-028 增加 bounded ASR normalizer 后，real ASR recall 有小幅提升，但仍被 ASR 质量阻断。

当前真实仓库 sherpa artifacts 的批量结论：

```json
{
  "overall_decision": "blocked_by_asr_quality",
  "scenario_count": 5,
  "engineering_scenario_count": 4,
  "negative_control_count": 1,
  "decision_counts": {
    "blocked_by_asr_quality": 4,
    "product_logic_ready": 1
  },
  "perfect_lane_ready_count": 5,
  "mock_lane_ready_count": 5,
  "real_asr_blocked_count": 4,
  "non_engineering_candidate_count": 0
}
```

这意味着：产品逻辑门已经明显向前推进。5 个场景的 perfect/mock lane 均已 ready，非工程负控仍为 0 candidate；当前主阻塞转为真实 ASR 质量，尤其是 sherpa 对中文技术实体的 normalized recall。

## 2. 五场景矩阵

| Scenario | Perfect Transcript | Mock ASR | Real ASR | Overall | 当前解读 |
| --- | --- | --- | --- | --- | --- |
| `api-review-001` | `product_logic_ready` | `product_logic_ready` | `blocked_by_asr_quality` | `blocked_by_asr_quality` | 产品 skeleton 能抓到候选；sherpa 技术实体 recall 0.5，ASR 质量阻断 |
| `architecture-review-001` | `product_logic_ready` | `product_logic_ready` | `blocked_by_asr_quality` | `blocked_by_asr_quality` | DRV-027 后可从缓存穿透/压测 owner 未安排识别 2 个候选；DRV-028 只能从“峰值按两万估”恢复 `QPS`，real ASR recall 0.2，仍被 ASR 质量阻断 |
| `incident-review-001` | `product_logic_ready` | `product_logic_ready` | `blocked_by_asr_quality` | `blocked_by_asr_quality` | DRV-027 后 mock lane 已转 ready；DRV-028 只能从“消费堆积...最高到了八万”恢复 `lag`，real ASR recall 0.25，仍被 ASR 质量阻断 |
| `release-review-001` | `product_logic_ready` | `product_logic_ready` | `blocked_by_asr_quality` | `blocked_by_asr_quality` | 产品 skeleton 可抓；sherpa recall 0.25，ASR 质量阻断 |
| `non-engineering-control-001` | `product_logic_ready` | `product_logic_ready` | `product_logic_ready` | `product_logic_ready` | 非工程负控保持 0 candidate，是当前重要安全信号 |

## 3. 当前优先级

下一轮不应该再停留在产品逻辑 gate 本身，而应进入受控 ASR 质量工作：

1. **保留 sherpa 作为性能基线**
   - 当前 batch 中 4 个工程场景 real ASR 均因 normalized technical entity recall 不达标而阻断。
   - 不继续在 sherpa 上证明中文质量主线，只保留 RTF/事件合同 baseline。

2. **进入 FunASR/normalizer/hotword 受控路径**
   - 如果用户提供本地 FunASR model dir 或明确批准 DRV-019 模型下载审批包，才运行 FunASR synthetic smoke。
   - 在不下载模型的前提下，只允许做 deterministic normalizer/hotword 规则；规则只能恢复 ASR 文本中已有线索，不能从 `<unk>` 或缺失文本猜实体。
   - DRV-028 已经完成一轮 bounded normalizer：`incident-review-001` 从 0.0 提升到 0.25，`architecture-review-001` 从 0.0 提升到 0.2；剩余缺失实体已经没有足够文本线索，继续用规则硬补会变成猜测。

3. **保持负控门**
   - 非工程 control 必须继续保持 0 candidate。
   - 任一后续 ASR/normalizer/hotword 改动导致负控 candidate > 0，都必须先回滚或收紧规则。

## 4. 命令

Focused：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_copilot_product_value_batch_gate.py \
  -q -p no:cacheprovider
```

当前结果：

```text
51 passed, 1 warning
```

真实仓库 batch smoke：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/copilot_product_value_batch_gate.py \
  --scripts-root data/asr_eval/synthetic_meetings/scripts \
  --mock-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' \
  --real-events-pattern 'artifacts/tmp/asr_events/{script_id}.sherpa.events.json' \
  --real-smoke-report-pattern 'artifacts/tmp/asr_reports/{script_id}.sherpa.smoke-report.json' \
  --real-provider sherpa_onnx_streaming
```

完整本地门禁：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/run_quality_gate.py --profile all-local --no-browser
```

## 5. 与产品初心的关系

这个 batch gate 把当前开发从“继续评测 ASR”拉回了产品价值：

- 如果 perfect transcript 失败，说明产品逻辑不行。
- 如果 mock ASR 失败，说明 streaming/event 或场景输入不行。
- 如果 real ASR 失败，才说明需要继续 ASR 质量工作。

当前五场景结果显示 perfect/mock 产品链路已经过线，但 real ASR 中文技术实体质量仍是主阻塞。因此下一步应进入 FunASR 本地模型审批/已就绪模型验证、bounded normalizer/hotword 或可选远程 ASR 对照，而不是继续扩产品逻辑 gate。

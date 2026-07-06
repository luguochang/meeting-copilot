# DRV-047 ASR Quality DRV-046 Assembly Intake Plan

> 日期：2026-07-04  
> 状态：Implemented  
> 目标：让 DRV-032 ASR quality decision gate 直接消费 DRV-046 FunASR synthetic smoke batch evidence assembler report，避免后续人工抽取嵌套 DRV-044 gate report。

## 1. 背景

DRV-043、DRV-045、DRV-046 已把本地 FunASR strict quality exit 的路径固定为：

```text
DRV-043 readiness
  -> DRV-045 execution packet
  -> manual FunASR synthetic smoke artifacts
  -> DRV-046 batch evidence assembler
  -> DRV-044 gate report
  -> DRV-032 ASR quality decision
```

DRV-046 的输出里已经包含 `drv044_gate_report`。在 DRV-047 之前，DRV-032 只能直接接收 DRV-044 gate report path，后续执行者仍需要手动从 DRV-046 report 里复制嵌套报告。这会制造不必要的手工步骤和证据漂移风险。

## 2. 决策

- `tools/asr_quality_decision_gate.py` 新增 `funasr_smoke_assembly_report` 内部参数。
- CLI 新增 `--funasr-smoke-assembly-path` 和 `--funasr-smoke-assembly-json`。
- DRV-032 只验证 caller-provided DRV-046 assembly report，不主动运行 DRV-046，不读取 artifact 列表，不计算 sha256。
- DRV-046 report 必须满足：
  - `decision_id=DRV-046`
  - `assembly_mode=funasr_synthetic_smoke_batch_evidence_assembler`
  - `assembly_version=funasr_synthetic_smoke_batch_evidence_assembler.v1`
  - `assembly_status=drv044_batch_evidence_validated`
  - `artifact_read_status=read`
  - `artifact_count=5`
  - `counts_as_asr_quality_go_evidence=true`
  - `counts_as_real_mic_go_evidence=false`
  - safety flags 全部为 false
  - `drv044_gate_report` 必须是合法 DRV-044 gate report，且 batch confirmed、provenance validated
- 通过后，DRV-032 report 输出 `funasr_smoke_result_source=drv046_batch_assembly`，并继续复用既有 strict quality exit 逻辑。
- 如果直接 DRV-044 report 和 DRV-046 assembly report 同时提供，DRV-032 阻断为 `blocked_by_funasr_smoke_assembly_input_guard`。

## 3. 不做什么

- 不运行 ASR。
- 不读取音频、`.m4a` 或真实用户录音。
- 不访问麦克风或请求权限。
- 不下载 FunASR/ModelScope 模型。
- 不下载或抽取公开音频。
- 不调用远程 ASR/LLM。
- 不读取 `configs/local/`、`data/asr_eval/local_samples/`、`data/local_runtime/` 或 `outputs/`。
- 不把 synthetic evidence 写成真实麦克风 Go evidence。

## 4. TDD 记录

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
```

结果：`4 failed, 17 passed, 1 warning`。

失败原因：

- `build_asr_quality_decision_gate_report()` 尚不支持 `funasr_smoke_assembly_report`。
- CLI 尚不支持 `--funasr-smoke-assembly-path`。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_asr_quality_decision_gate.py \
  -q -p no:cacheprovider
```

结果：`21 passed, 1 warning`。

## 5. 当前结论

DRV-047 只消除了 DRV-046 -> DRV-032 之间的手工证据交接缺口。当前仍缺真实本地 FunASR smoke artifacts，因此默认 ASR quality gate 仍应保持：

```text
decision_status=requires_funasr_model_dir_or_drv019_approval
quality_exit_status=not_exited
```

真实麦克风会议仍由用户最终验证，且必须等 readiness gate 解释所有 blocker 后才能启动。

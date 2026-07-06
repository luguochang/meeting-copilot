# DRV-046 FunASR Synthetic Smoke Batch Evidence Assembler Plan

> 日期：2026-07-04  
> 状态：Implemented  
> 目的：把 DRV-045 manual execution packet 产出的 5 个 smoke report JSON 装配成 DRV-044 batch evidence，并由 DRV-044 gate 重新验证 provenance/hash 和质量阈值。  
> 边界：本文档和工具不运行 ASR、不读取音频、不下载模型、不访问麦克风、不读取真实用户录音或 `.m4a`、不读取 `configs/local/`、不调用远程 ASR/LLM、不写 artifacts。

## 1. 为什么需要 DRV-046

DRV-045 已经固定未来手动执行的命令、输出路径和 DRV-044 provenance template。但执行完成后仍需要一个机器步骤：

```text
5 个 manual FunASR smoke report JSON
  -> 读取 approved artifact bytes
  -> 计算 sha256
  -> 合并 scenario_results
  -> 组装 batch_synthetic_confirmation
  -> 调用 DRV-044 gate
```

如果没有这一步，后续仍可能手工拼错 scenario、sha256、artifact path 或 provenance shape。DRV-046 把这段交接变成可复跑工具。

## 2. 输入

工具：`tools/funasr_synthetic_smoke_batch_evidence_assembler.py`

允许输入：

- inline `execution_packet` object。
- `--execution-packet-json`。
- `--execution-packet-path`，且路径必须位于 approved `artifacts/tmp/**.json`。

execution packet 必须满足：

- `decision_id=DRV-045`
- `packet_mode=funasr_synthetic_smoke_execution_packet`
- `packet_version=funasr_synthetic_smoke_execution_packet.v1`
- `packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run`
- 5 个默认 scenario。
- provenance artifact path 全部位于 `artifacts/tmp/asr_reports/**.json`。
- packet safety flags 全 false。

## 3. 输出

无 packet 时：

```text
assembly_status=blocked_missing_drv045_execution_packet
```

packet 合法但 smoke artifacts 缺失时：

```text
assembly_status=blocked_missing_manual_smoke_artifacts
```

artifacts 存在且 DRV-044 通过时：

```text
assembly_status=drv044_batch_evidence_validated
artifact_read_status=read
artifact_count=5
counts_as_asr_quality_go_evidence=true
counts_as_real_mic_go_evidence=false
```

如果 artifacts 存在但质量、安全字段或 provenance 不满足 DRV-044：

```text
assembly_status=drv044_batch_evidence_blocked
counts_as_asr_quality_go_evidence=false
```

## 4. 不做什么

- 不调用 `transcribe_funasr.py`。
- 不读取 `.wav` 或任何音频 bytes。
- 不读取用户 `.m4a` 或真实录音。
- 不下载 FunASR/ModelScope 模型。
- 不下载公开音频。
- 不调用远程 ASR/LLM。
- 不访问麦克风或枚举设备。
- 不写 `artifacts/tmp/asr_events` 或 `artifacts/tmp/asr_reports`。
- 不绕过 DRV-044；所有 batch evidence 必须再次经 DRV-044 gate。

## 5. 验证

TDD red：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py \
  -q -p no:cacheprovider

Result: 6 failed, 1 warning
Reason: tools/funasr_synthetic_smoke_batch_evidence_assembler.py did not exist
```

TDD green：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_batch_evidence_assembler.py \
  -q -p no:cacheprovider

Result: 6 passed, 1 warning
```

## 6. 后续

本地 FunASR ASR quality exit 的严格路径现在是：

1. DRV-043：生成 readiness evidence。
2. DRV-045：生成 manual execution packet。
3. 人工执行 packet 中的 5 个命令，产出 smoke report JSON。
4. DRV-046：读取 5 个 smoke report JSON，计算 sha256，组装 batch evidence，并调用 DRV-044。
5. DRV-032：消费 DRV-044 gate report，判断是否退出 ASR quality blocker。

这仍不是真实麦克风 Go evidence；真实会议必须等 PCWEB-115 readiness 通过后由用户最终执行。

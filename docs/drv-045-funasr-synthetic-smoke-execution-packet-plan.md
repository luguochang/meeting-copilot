# DRV-045 FunASR Synthetic Smoke Execution Packet Plan

> 日期：2026-07-04  
> 状态：Implemented  
> 目的：把 DRV-043 FunASR readiness evidence 和 DRV-044 FunASR smoke result evidence 之间的执行交接固定为机器可验收 packet。  
> 边界：本文档和工具不运行 ASR、不读取音频、不下载模型、不访问麦克风、不读取真实用户录音或 `.m4a`、不读取 `configs/local/`、不调用远程 ASR/LLM、不写 artifacts。

## 1. 为什么需要 DRV-045

当前 ASR quality exit 已有两端：

- DRV-043：证明本地 FunASR runtime cache/model files 是否存在，但不运行 ASR。
- DRV-044：证明 caller-provided FunASR synthetic smoke result 是否满足质量阈值和 provenance/hash gate，但不运行 ASR。

缺口在中间：模型目录一旦就绪，必须明确跑哪 5 个 synthetic scenarios、命令怎么组成、输出产物放哪里、哪些产物计算 sha256 后进入 DRV-044 batch provenance。否则后续即使有本地模型，也可能再次陷入“该跑什么、怎么算通过”的循环。

DRV-045 的定位是执行包，不是执行器。它只生成 manual-user-run packet，所有 safety flags 保持 false。

## 2. 输入

工具：`tools/funasr_synthetic_smoke_execution_packet.py`

允许输入：

- inline `funasr_readiness_report` object。
- `--funasr-readiness-json`。
- `--funasr-readiness-path`，且路径必须位于 approved `artifacts/tmp/**.json`。
- 可选 `--scenario-id`，但必须覆盖 4 个工程场景和 1 个非工程负控。

readiness 必须满足：

- `report_mode=funasr_synthetic_smoke_readiness`
- `report_version=funasr_synthetic_smoke_readiness.v1`
- `readiness_status=cache_preflight_passed_offline_execution_not_proven`
- `required_cached_models_status=present`
- `offline_guard_status=required_before_execution`
- `model_download_status=not_started`
- `execution_mode=preflight_only_no_execution_authorization`
- readiness safety flags 全 false。

## 3. 输出

默认无 readiness 时：

```text
packet_status=blocked_missing_funasr_readiness
execution_approval_status=not_approved
safe_to_execute_now=false
```

readiness 合法时：

```text
packet_status=ready_for_manual_batch_funasr_synthetic_smoke_run
execution_approval_status=not_approved_manual_run_only
scenario_count=5
engineering_scenario_count=4
negative_control_count=1
```

工具会生成：

- 5 个 command preview。
- 每个 scenario 的 approved synthetic audio path、events output path、provider output path、transcript report path、smoke report path。
- `expected_drv044_batch_artifact_provenance` template，source_kind 固定为 `local_funasr_synthetic_smoke_artifacts`。
- 每个 artifact 的 `sha256_source=compute_after_manual_run`，要求未来手动执行后再计算真实 sha256。

默认 5 场景：

```text
api-review-001
architecture-review-001
incident-review-001
release-review-001
non-engineering-control-001
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
- 不把 packet 写成 ASR quality Go evidence。

## 5. 验证

TDD red：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  -q -p no:cacheprovider

Result: 6 failed, 1 warning
Reason: tools/funasr_synthetic_smoke_execution_packet.py did not exist
```

TDD green：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_funasr_synthetic_smoke_execution_packet.py \
  -q -p no:cacheprovider

Result: 6 passed, 1 warning
```

## 6. 后续

DRV-045 让下一步更具体：

1. 用户提供已验证 FunASR 本地模型目录，或明确批准 DRV-019 后，先生成 DRV-043 readiness evidence。
2. 用 DRV-045 生成 5 场景 manual execution packet。
3. 手动执行 packet 中命令，产出 events/provider/transcript/smoke reports。
4. 对每个 smoke report artifact 计算 sha256，填入 DRV-044 batch provenance。
5. DRV-044 batch 通过且 provenance/hash validated 后，DRV-032 才能严格退出 ASR quality blocker。

这仍不是真实麦克风 Go evidence；真实会议必须等 PCWEB-115 readiness 通过后由用户最终执行。

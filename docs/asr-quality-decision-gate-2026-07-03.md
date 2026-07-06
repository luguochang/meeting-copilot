# ASR Quality Decision Gate

> 日期：2026-07-03  
> 决策 ID：DRV-032  
> 状态：Accepted / implemented  
> 目的：把当前 ASR 质量结论从口头判断收束为机器可测 gate，避免继续陷入泛评测循环。  
> 边界：本 gate 不运行 ASR、不下载 FunASR/ModelScope 模型、不下载公开音频、不访问麦克风、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不运行 Cargo/Tauri。

## 1. 结论

新增：

- `tools/asr_quality_decision_gate.py`
- `tests/test_asr_quality_decision_gate.py`

当前默认输出：

```json
{
  "decision_id": "DRV-032",
  "decision_status": "requires_funasr_model_dir_or_drv019_approval",
  "product_value_batch_overall_decision": "blocked_by_asr_quality",
  "perfect_lane_ready_count": 5,
  "mock_lane_ready_count": 5,
  "real_asr_blocked_count": 4,
  "non_engineering_candidate_count": 0,
  "funasr_readiness_status": "blocked",
  "funasr_required_cached_models_status": "missing",
  "funasr_approval_packet_status": "generated_for_manual_review",
  "public_audio_decision_status": "blocked_no_verified_public_sample_manifest",
  "quality_exit_status": "not_exited",
  "recommended_quality_exit_path_id": "local_funasr_model_dir_if_available_else_explicit_degraded_pilot_decision",
  "can_unblock_real_mic_shadow_test_quality_gate": false,
  "counts_as_asr_quality_go_evidence": false
}
```

解释：

- 产品逻辑不是当前最大阻塞：perfect/mock lane 已经 5/5 ready。
- 非工程 control 仍为 0 candidate，负控边界继续成立。
- real sherpa ASR 仍被中文技术实体召回阻塞，不能进入真实麦克风 pilot。
- FunASR/Paraformer 是中文质量主候选，但当前缺本地模型目录或明确 DRV-019 模型下载审批。
- 公开音频阶段仍是 no-download blocked，不参与当前 ASR 质量解锁。
- 默认 ASR quality exit 仍未退出：`quality_exit_status=not_exited`，不能开始真实麦克风 shadow test。
- 若用户显式接受一次降级试点，gate 可输出 `degraded_pilot_accepted_with_quality_risk`，只用于验证桌面时序和反馈闭环，不算 ASR 质量 Go 证据。

## 2. 输入来源

DRV-032 只组合已有 gate 的输出：

| 输入 | 工具 | 作用 |
| --- | --- | --- |
| Product value batch | `tools/copilot_product_value_batch_gate.py` | 判断 perfect/mock/real 三车道和非工程负控 |
| FunASR readiness | `tools/funasr_synthetic_smoke_readiness.py` | 判断本地 FunASR runtime cache / local model dir 是否可进入后续审批 |
| FunASR smoke result evidence | `tools/funasr_synthetic_smoke_result_evidence.py` | 判断未来本地 FunASR synthetic smoke result 是否满足单场景候选或 batch confirmation |
| FunASR approval packet | `tools/funasr_model_download_approval_packet.py` | 判断 DRV-019 是否已生成 manual-user-run-only 审批包 |
| Public audio decision | `tools/public_audio_planned_sample_manifest_decision.py` | 判断公开音频是否仍 blocked/no-download |

## 3. 决策状态

| 状态 | 含义 | 下一步 |
| --- | --- | --- |
| `fix_product_logic_first` | perfect/mock lane 未 ready，不能把失败归因给 ASR provider | 修 EvidenceSpan / gap / candidate 逻辑 |
| `fix_stream_contract_first` | streaming event contract 未 ready | 修 ASR event contract |
| `requires_funasr_model_dir_or_drv019_approval` | real sherpa ASR blocked，FunASR 本地模型未就绪 | 提供已验证 FunASR local model dir，或明确批准 DRV-019 手动模型下载审批包 |
| `funasr_cache_preflight_ready_requires_execution_approval` | FunASR cache preflight 就绪，但还没有执行授权 | 批准一次 synthetic smoke，然后复跑 batch gate |
| `funasr_smoke_candidate_requires_batch_confirmation` | 单场景 FunASR synthetic smoke result 达标，但还没有 4 工程 + 1 负控 batch confirmation | 跑 batch confirmation；真实麦克风仍 blocked |
| `degraded_pilot_accepted_with_quality_risk` | 用户显式接受单次降级 shadow test 风险 | 只允许 PCWEB-115 继续检查其它前置条件；不算 ASR quality Go evidence |
| `asr_quality_current_gate_not_blocking` | 当前 ASR 质量 gate 不阻塞 | 推进 desktop runtime 或 controlled LLM cards |

## 3.1 Quality Exit Options

DRV-032 现在稳定输出 `quality_exit_options`，用于停止“无限评估循环”：

| Path | 默认 | 额外 provider 费用 | 说明 |
| --- | --- | --- | --- |
| `verified_local_funasr_model_dir` | 启用 | 无 | 用户已有本地 FunASR 模型目录时，进入一次 synthetic smoke 审批，再复跑 batch gate |
| `drv019_manual_model_download` | 禁用 | 无 | 只在用户明确批准 DRV-019 后手动下载/校验 ModelScope 模型 |
| `optional_remote_asr_comparison` | 禁用 | 有 | 只作为显式选择的质量对照，不作为 MVP 默认成本项 |
| `explicit_degraded_pilot_acceptance` | 禁用 | 无 | 用户明确接受降级风险后，只允许一次真实会议 shadow-test 前置判断，不证明 ASR 达标 |

主线 stop conditions：

- `do_not_expand_provider_bakeoff_without_funasr_model_or_explicit_remote_asr_decision`
- `do_not_add_more_report_only_readiness_wrappers`
- `do_not_start_real_mic_shadow_test_without_quality_exit_or_explicit_degraded_acceptance`

## 3.2 CLI Degraded Pilot Acceptance Input

DRV-032 的降级试点 acceptance 现在既可以由 Python caller 传入，也可以通过 CLI 传入：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py \
  --degraded-pilot-acceptance-json '{"acceptance_record_version":"asr_quality_degraded_pilot_acceptance.v1",...}'
```

或：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py \
  --degraded-pilot-acceptance-path artifacts/tmp/asr-quality-decision/degraded-acceptance.json
```

Path input 只允许 approved `artifacts/tmp/**` 下的 `.json` 文件。工具会在读取前阻断：

- `configs/local`
- `data/asr_eval/local_samples`
- `data/asr_eval/samples`
- `data/local_runtime`
- `outputs`
- 仓库外路径
- `.m4a`
- 非 JSON 文件

合法 acceptance 的结果仍然是 exit `1`，因为它不是严格 ASR 质量通过。报告会显示 `decision_status=degraded_pilot_accepted_with_quality_risk`、`quality_exit_status=degraded_pilot_accepted_with_quality_risk`、`can_unblock_real_mic_shadow_test_quality_gate=true` 和 `counts_as_asr_quality_go_evidence=false`。默认没有 acceptance record 时，报告保持 `acceptance_input_status=not_requested`、`degraded_pilot_acceptance_status=not_requested`、`quality_exit_status=not_exited`。

## 3.3 CLI FunASR Readiness Evidence Input

2026-07-04 DRV-043 更新：ASR quality gate 也可以从 approved artifact JSON 读取 FunASR readiness evidence：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py \
  --funasr-readiness-path artifacts/tmp/asr-quality-decision/funasr-readiness.json
```

该 path 只允许 `artifacts/tmp/**` 下的 JSON，并在读取前阻断 `configs/local`、真实/样本音频、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。若 evidence 表示 `cache_preflight_passed_offline_execution_not_proven` 且 `required_cached_models_status=present`，报告输出 `decision_status=funasr_cache_preflight_ready_requires_execution_approval`；这仍不是 ASR quality Go evidence，只表示可以另开审批跑一次 FunASR synthetic smoke。

## 3.4 CLI FunASR Smoke Result Evidence Input

2026-07-04 DRV-044 更新：ASR quality gate 也可以从 approved artifact JSON 读取 FunASR synthetic smoke result evidence：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py \
  --funasr-smoke-result-path artifacts/tmp/asr_reports/funasr-smoke-result.json
```

该 path 只允许 `artifacts/tmp/**` 下的 JSON，并在读取前阻断 `configs/local`、真实/样本音频、`data/local_runtime`、`outputs`、仓库外路径、`.m4a` 和非 JSON。输入 JSON 仍必须先通过 `tools/funasr_synthetic_smoke_result_evidence.py` 的 schema/quality gate。

决策语义：

- `funasr_synthetic_smoke_quality_candidate_requires_batch_confirmation`：只表示单场景达标，DRV-032 输出 `funasr_smoke_candidate_requires_batch_confirmation` / `quality_exit_status=not_exited`。
- `funasr_synthetic_smoke_quality_batch_confirmed` 且 `counts_as_asr_quality_go_evidence=true`：DRV-032 输出 `asr_quality_current_gate_not_blocking` / `strict_quality_gate_not_blocking`。
- 任意 blocked result：DRV-032 输出 `fix_funasr_smoke_result_evidence_first`。

该 evidence 不是真实麦克风 Go evidence；真实会议仍必须经过 PCWEB-115 readiness gate 和用户显式 start。

## 4. 默认命令

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/asr_quality_decision_gate.py
```

当前结果：

- exit code：`1`
- `decision_status=requires_funasr_model_dir_or_drv019_approval`
- `blocked_reasons`：
  - `real_sherpa_asr_blocked_by_chinese_technical_entity_recall`
  - `funasr_local_model_dir_or_cache_not_ready`
  - `drv019_model_download_requires_explicit_user_approval`
- `next_allowed_actions`：
  - `provide_verified_local_funasr_model_dir`
  - `approve_drv019_manual_model_download_packet`
  - `accept_degraded_pilot_with_explicit_quality_risk`
  - `continue_desktop_noop_or_mic_adapter_contract_without_claiming_asr_quality_solved`

## 5. Safety Flags

默认保持全部 false：

- `safe_to_run_funasr_smoke_now=false`
- `safe_to_download_models_now=false`
- `safe_to_download_public_audio_now=false`
- `safe_to_extract_public_audio_now=false`
- `safe_to_call_public_audio_asr_now=false`
- `safe_to_capture_microphone_now=false`
- `safe_to_read_user_audio_now=false`
- `safe_to_read_configs_local_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_call_llm_now=false`
- `safe_to_run_cargo_tauri_now=false`

## 6. TDD Evidence

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
5 failed, 1 warning
```

失败原因：`tools/asr_quality_decision_gate.py` 不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
5 passed, 1 warning
```

本轮 ASR quality exit contract 扩展红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
4 failed, 12 passed, 1 warning
```

失败原因：DRV-032 尚未输出 `quality_exit_status` / `quality_exit_options` / degraded pilot acceptance 字段，PCWEB-115 尚不接受 `degraded_pilot_accepted_with_quality_risk`。

本轮扩展绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py tests/test_real_mic_shadow_test_readiness_gate.py -q -p no:cacheprovider
```

结果：

```text
16 passed, 1 warning
```

本轮 CLI acceptance input 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
2 failed, 7 passed, 1 warning
```

失败原因：CLI 尚不识别 `--degraded-pilot-acceptance-json` 和 `--degraded-pilot-acceptance-path`，也不能从 approved artifact JSON path 加载 acceptance evidence。

本轮 CLI acceptance input 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
9 passed, 1 warning
```

DRV-044 FunASR smoke result evidence 红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
9 failed, 12 passed, 1 warning
```

失败原因：`tools/funasr_synthetic_smoke_result_evidence.py` 不存在；DRV-032 尚未接受 `funasr_smoke_result_report`。

DRV-044 绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_result_evidence.py tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
```

结果：

```text
23 passed, 1 warning
```

## 7. 后续边界

DRV-032 完成后，下一步不再继续泛化 ASR/provider 横评，也不继续找版权不清公开音频。允许的下一步只有：

- 提供已验证 FunASR 本地模型目录后，申请一次 synthetic smoke 并复跑 batch gate。
- 明确批准 DRV-019 manual-user-run-only 模型下载审批包后，再进入 post-download verification。
- 显式接受 `asr_quality_degraded_pilot_acceptance.v1` 降级试点风险后，只允许 PCWEB-115 继续检查其它真实会议前置条件；该路径 `counts_as_asr_quality_go_evidence=false`。
- 在 ASR 质量仍 blocked 时，继续推进 desktop no-op runtime / IPC / mic adapter contract，但不能宣称真实实时建议链路质量已达标。

# DRV-043 FunASR Local Readiness Evidence Input Plan

> 日期：2026-07-04  
> 状态：Accepted / implemented  
> 范围：把“已有本地 FunASR 模型目录或 runtime cache 已预检”变成 ASR quality gate 可消费的 evidence input。  
> 边界：本计划不授权下载模型、不运行 FunASR、不运行 ASR、不访问麦克风、不读取真实音频或 `.m4a`、不读取 `configs/local/`、不调用远程 ASR/LLM、不写 runtime ASR artifacts。

## 1. 背景

DRV-032 已把 ASR quality exit 收束为四条路径：已验证本地 FunASR 模型目录、DRV-019 手动模型下载审批、显式远端 ASR 对照、显式降级试点风险接受。

DRV-041/042 已证明 mock/approved event 产品链路可过 5 场景 batch。现在继续推进主线时，不能再扩展 mock smoke，也不能静默下载模型。最小可行下一步是补齐“已有本地模型预检证据如何进入 ASR quality gate”的接口。

## 2. 决策

- `tools/funasr_synthetic_smoke_readiness.py` CLI 新增 `--model-cache-root`。
- 该参数只检查必需模型文件是否存在，不读取模型内容，不运行模型。
- 输出不回显本机绝对路径，只输出 `model_cache_root_input_status`：
  - `explicit_root_validated_no_path_echo`
  - `default_runtime_cache_checked_no_path_echo`
  - `blocked_forbidden_root`
- `model_cache_root` 若位于 `configs/local`、`data/asr_eval/local_samples`、`data/asr_eval/samples`、`data/local_runtime`、`outputs` 或音频路径，会在读取模型组件前阻断。
- `tools/asr_quality_decision_gate.py` CLI 新增 `--funasr-readiness-path`。
- `--funasr-readiness-path` 只允许读取 approved `artifacts/tmp/**` 下的 JSON；读取前阻断 forbidden roots、仓库外路径、`.m4a` 和非 JSON。
- 当 readiness JSON 表示 `cache_preflight_passed_offline_execution_not_proven` 且 `required_cached_models_status=present`，ASR quality gate 输出 `funasr_cache_preflight_ready_requires_execution_approval`，仍不运行 smoke。

## 3. 状态变化

实现前：

```text
ASR quality gate 只能用内部 default FunASR readiness，默认 runtime cache missing。
如果用户已有本地模型目录，缺一个可审计入口把预检 evidence 输入 DRV-032。
```

实现后：

```text
FunASR local model/cache preflight evidence
  -> write JSON under artifacts/tmp/**
  -> tools/asr_quality_decision_gate.py --funasr-readiness-path <json>
  -> decision_status=funasr_cache_preflight_ready_requires_execution_approval
  -> next_allowed_actions=approve_single_funasr_synthetic_smoke_run
```

这仍不是 ASR quality Go evidence。它只说明下一步可以在明确审批下跑一次 FunASR synthetic smoke。

## 4. TDD 记录

Red：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_readiness.py -q -p no:cacheprovider
Result: 2 failed, 6 passed, 1 warning
```

失败原因：

- `main()` 不接受 `audio_exists` / `venv_python_exists` 注入。
- forbidden `model_cache_root` 会被检查模型组件，未在读取前 blocked。

Red：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
Result: 2 failed, 10 passed, 1 warning
```

失败原因：

- CLI 不识别 `--funasr-readiness-path`。
- forbidden readiness path 未在读取前 blocked。

Green：

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_funasr_synthetic_smoke_readiness.py -q -p no:cacheprovider
Result: 8 passed, 1 warning
```

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests/test_asr_quality_decision_gate.py -q -p no:cacheprovider
Result: 12 passed, 1 warning
```

## 5. 安全边界

DRV-043 不改变以下事实：

- `safe_to_run_funasr_smoke_now=false`
- `safe_to_download_models_now=false`
- `safe_to_capture_microphone_now=false`
- `safe_to_read_user_audio_now=false`
- `safe_to_read_configs_local_now=false`
- `safe_to_call_remote_asr_now=false`
- `safe_to_call_llm_now=false`

它只把本地模型预检证据接入 quality gate，不执行模型。

## 6. 下一步

如果后续出现本地 FunASR 模型目录或 DRV-019 手动下载完成后的目录，先生成 readiness JSON，再通过 `--funasr-readiness-path` 喂给 DRV-032。只有 DRV-032 返回 `funasr_cache_preflight_ready_requires_execution_approval` 后，才可以另开受控审批，运行一次 synthetic smoke。

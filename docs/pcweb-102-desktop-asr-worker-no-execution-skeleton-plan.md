# PCWEB-102 Desktop ASR Worker No-Execution Skeleton Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把 PCWEB-101 的人工 approval packet 推进到可导入的 worker sidecar module boundary，但仍不实现或执行真实 worker。  
> 边界：不启动进程、不访问麦克风、不读取真实音频、不读取 `configs/local`、不读写 event file、不写 runtime audio、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

## 1. 目标

PCWEB-102 的目标是建立真实 worker 代码落点，但只做到 no-execution skeleton：

```text
PCWEB-101 approval packet
  -> code/asr_runtime/scripts/asr_worker_sidecar.py
  -> tools/desktop_asr_worker_no_execution_skeleton.py
  -> Web readiness next pointer PCWEB-102
```

这不是 worker implementation approval，也不是 worker execution approval。它只定义未来 worker 的身份、命令入口、lifecycle state、event writer、provider adapter、health/status 和 cleanup plan 的预览合同。

## 2. 新增文件

- `code/asr_runtime/scripts/asr_worker_sidecar.py`
  - 纯 Python no-execution sidecar skeleton。
  - 输出 `build_no_execution_worker_skeleton_report()`。
  - 所有执行、音频、模型、event IO、Web mutation、Tauri/Cargo safety flags 均为 false。

- `tools/desktop_asr_worker_no_execution_skeleton.py`
  - 静态 policy/report CLI。
  - 校验 provider/source、approved roots、forbidden roots、repo 外路径和 symlink 逃逸。
  - blocked config 时 CLI 返回非 0。

- `code/desktop_tauri/asr-worker-no-execution-skeleton.policy.json`
  - 固定 `PCWEB-102`、`required_previous_contracts=["PCWEB-101"]`、`skeleton_mode=module_boundary_only`、`execution_mode=no_execution`。

- `tests/test_desktop_asr_worker_no_execution_skeleton.py`
  - TDD 覆盖 policy、source scan、默认 report、sherpa/synthetic preview、mic/remote/FunASR blocker、path blocker、custom policy blocker 和 CLI blocker。

## 3. Web Readiness

`GET /desktop/asr-worker-handoff-dry-run-readiness` 的 next pointer 已从 `PCWEB-101` 推进到 `PCWEB-102`，next decision 改为：

```text
define_desktop_asr_worker_no_execution_skeleton
```

该 endpoint 仍然只读 readiness，不启动 worker、不读 event file、不写 session、不访问麦克风。

## 4. TDD Evidence

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_no_execution_skeleton.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：`10 failed, 2 warnings`。失败原因是 policy/tool/sidecar 不存在，Web readiness 仍返回 `PCWEB-101`。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_asr_worker_no_execution_skeleton.py \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  -q -p no:cacheprovider
```

结果：`10 passed, 2 warnings`。

## 5. 后续边界

PCWEB-102 后仍不能直接进入真实麦克风或真实 worker execution。下一步只能在新决策/TDD 下选择：

- desktop command runner binding approval。
- Tauri no-op run approval/smoke。
- mic adapter contract。
- FunASR 本地模型目录/DRV-019 后的一次 synthetic smoke。
- public audio no-download sample manifest 条件审查。

# PCWEB-123 Worker Mic Source Single Shadow Approval Plan

> 日期：2026-07-03  
> 状态：Implemented with TDD  
> 主线节点：worker mic source approval evidence  
> 边界：本计划不授权访问麦克风、不请求音频权限、不启动 worker、不执行 `worker.prepare(source_kind=mic)`、不读写 audio chunk、不读写 worker event file、不读取真实用户音频、不读取 `configs/local/`、不调用远程 ASR/LLM、不下载模型或公开音频、不运行 Tauri dev/build。

## 1. 目的

PCWEB-114/120 已能生成 `ready_for_manual_review_not_executable` 的 worker mic source review packet，但 `worker_mic_source_approval_status` 仍是 `not_approved`。PCWEB-123 只补一层单次 shadow-test approval evidence：

- 输入：PCWEB-114 manual review packet report。
- 输入：显式 approval record，包含 session id、scope 和固定 approval token。
- 输出：PCWEB-115 readiness gate 可识别的 worker mic source approval evidence。

该 evidence 只把 `worker_mic_source_approval_status` 设为 `manually_approved_for_single_shadow_test`，并继续保持 `approved_to_execute_now=false` 和全部执行/音频/远程安全 flags 为 false。

## 2. 实现内容

新增文件：

- `tests/test_desktop_worker_mic_source_single_shadow_approval.py`
- `tools/desktop_worker_mic_source_single_shadow_approval.py`
- `code/desktop_tauri/worker-mic-source-single-shadow-approval.policy.json`

验收要点：

- 没有 manual review packet 或没有 approval record 时必须 blocked。
- approval token、scope 或 session id 不匹配时必须 blocked。
- manual review packet 未处于 `ready_for_manual_review_not_executable` 时必须 blocked。
- 合法 approval evidence 能让 `real_mic_shadow_test_readiness_gate._worker_mic_source_ready()` 返回 true。
- 合法 approval evidence 只能移除 `worker_mic_source_not_approved` blocker，不得让真实麦克风 shadow test 直接 ready。

## 3. TDD 验证

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_single_shadow_approval.py \
  -q -p no:cacheprovider
```

结果：

```text
6 failed, 1 warning
```

失败原因：PCWEB-123 policy 和 tool 不存在。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_single_shadow_approval.py \
  -q -p no:cacheprovider
```

结果：

```text
6 passed, 1 warning
```

Focused integration：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_worker_mic_source_single_shadow_approval.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  tests/test_desktop_worker_mic_source_approval.py \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py \
  tests/test_desktop_asr_worker_real_mic_source_boundary.py \
  -q -p no:cacheprovider
```

结果：

```text
36 passed, 1 warning
```

默认 CLI 无输入时：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_worker_mic_source_single_shadow_approval.py
```

结果：

```text
exit 1
approval_evidence_status=blocked_missing_manual_review_packet
worker_mic_source_approval_status=not_approved
```

## 4. Readiness 影响

PCWEB-123 合法 evidence 能移除：

```text
worker_mic_source_not_approved
```

仍然保留的真实会议阻塞项：

- `asr_quality_decision_requires_funasr_model_dir_or_drv019_approval`

如果没有同时提供 PCWEB-121 mic adapter evidence 和 PCWEB-122 ASR worker evidence，也会继续保留对应 blocker。PCWEB-123 不代表真实 worker 已启动，不代表真实麦克风已访问，也不代表 ASR 质量已达标。

## 5. 后续主线

PCWEB-123 后不得继续新增同类 worker approval wrapper。下一步只允许在以下方向推进：

1. ASR quality exit：提供 FunASR 本地模型目录、批准 DRV-019 模型下载、选择可选远程 ASR 对照，或明确接受降级 pilot。
2. 用户最终真实麦克风 shadow test：只能在 PCWEB-115 readiness gate 全部满足后，由用户在 UI 中显式启动。

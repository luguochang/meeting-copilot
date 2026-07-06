# PCWEB-106 Desktop Mic Adapter Contract Readiness UI/API Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把 PCWEB-105 麦克风 adapter 合同暴露到 Web/Tauri no-op 工作台，使真实麦克风 shadow test 的前置状态可见、可测、可追溯。  
> 边界：本阶段不访问麦克风、不请求权限、不枚举设备、不写 audio chunk、不删除真实音频、不读取真实用户音频、不读取 `configs/local`、不调用远程 ASR/LLM、不下载模型、不运行 Cargo/Tauri。

## 背景

PCWEB-105 已经定义了 `mic_adapter.prepare/status/start/pause/resume/stop/delete_audio_chunks` 合同、显式用户 start 边界、ignored runtime audio root、audio chunk root 和删除语义，但 Web 工作台仍只能在 ASR handoff readiness 里看到旧的 `mic_adapter_contract_status=not_defined` 口径。

PCWEB-106 的目标不是进入真实录音，而是让桌面主线能在 UI/API 中看到同一份 PCWEB-105 合同快照，避免后续误以为麦克风 adapter 还没有定义，也避免误把合同定义当成可以采集音频。

## 实现

新增后端只读端点：

```text
GET /desktop/mic-adapter-contract-readiness
```

该端点复用 `tools/desktop_mic_adapter_contract.py` 的静态 report，返回：

- `pcweb_id=PCWEB-106`
- `source_pcweb_id=PCWEB-105`
- `readiness_mode=readiness_only_no_mic_permission`
- `mic_adapter_ui_status=ready_noop_contract_visible`
- `mic_adapter_contract_status=specified_not_executable`
- `contract_version=desktop_mic_adapter_contract.v1`
- `adapter_execution_status=not_bound_not_executed`
- `permission_request_status=not_requested`
- `audio_capture_status=not_started`
- `audio_chunk_write_status=not_written`
- `audio_chunk_delete_status=not_executed`
- `approved_runtime_audio_root=artifacts/tmp/desktop_mic_adapter_runtime`
- `approved_audio_chunk_root=artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`
- `delete_semantics=delete_audio_chunks_before_session_discard`
- 7 个 mic adapter command catalog
- all-false safety flags

同步更新：

- `GET /desktop/asr-worker-handoff-dry-run-readiness` 的 `next_pcweb_id` 从 `PCWEB-105` 推进到 `PCWEB-106`。
- `mic_adapter_contract_status` 从 `not_defined` 更新为 `specified_not_executable`。
- blocker 从 `mic_adapter_contract_not_approved` 更新为 `mic_adapter_not_bound_to_desktop_runtime`。
- next decision 从 `define_desktop_mic_adapter_contract` 更新为 `surface_mic_adapter_contract_readiness_ui`。

新增 Web 工作台面板：

```text
desktop-mic-adapter-contract-panel
```

面板展示：

- 合同状态和版本。
- permission/capture/chunk/delete 状态。
- runtime audio root 和 audio chunk root。
- user start boundary 和 delete semantics。
- command catalog。
- blockers、next decisions 和 false safety flags。

## 安全边界

PCWEB-106 仍然不授权真实麦克风会议：

- 不绑定 native mic adapter。
- 不接受或执行真实 mic command。
- 不请求 macOS 麦克风权限。
- 不枚举 input device。
- 不访问麦克风。
- 不写 audio chunk。
- 不读 audio chunk。
- 不删除真实 audio chunk。
- 不读取真实用户音频或 `data/asr_eval/local_samples`。
- 不读取 `configs/local` 或 secret。
- 不调用远程 ASR/LLM。
- 不下载模型。
- 不 mutate Web session。
- 不运行 Cargo/Tauri。

## TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_reports_contract_without_audio_access \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
6 failed, 2 warnings
```

失败原因：

- ASR handoff readiness 仍返回 `next_pcweb_id=PCWEB-105`。
- `/desktop/mic-adapter-contract-readiness` 尚不存在。
- `desktop-mic-adapter-contract-panel` 尚不存在。
- `loadDesktopMicAdapterContractReadiness` 尚不存在。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_desktop_asr_worker_handoff_dry_run_readiness_reports_noop_boundary \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_reports_contract_without_audio_access \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_does_not_probe_audio_or_read_secrets \
  code/web_mvp/backend/tests/test_app.py::test_desktop_mic_adapter_contract_readiness_with_data_dir_does_not_create_local_storage \
  code/web_mvp/backend/tests/test_app.py::test_workbench_index_serves_state_first_ui_shell \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

结果：

```text
6 passed, 2 warnings
```

## Review Follow-up

只读审查发现：API 已返回 PCWEB-105 `FALSE_SAFETY_FLAGS` 的完整集合，但初版面板只渲染了其中一部分，和“all-false safety flags 可见”的验收口径不完全一致。

已补充：

- `desktop-mic-adapter-contract-panel` 现在展示 PCWEB-105 的完整 21 个 safety flags。
- `test_workbench_static_assets_are_served` 逐项检查完整 flag 名称存在于前端源码。
- `browser_smoke.mjs` 逐项检查浏览器中渲染出的完整 `flag=false`。

补充 TDD：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  code/web_mvp/backend/tests/test_app.py::test_workbench_static_assets_are_served \
  -q -p no:cacheprovider
```

红灯：

```text
1 failed, 2 warnings
```

失败原因：`safe_to_accept_mic_command_now` 等完整 PCWEB-105 safety flags 尚未出现在 `app.js`。

绿灯：

```text
1 passed, 2 warnings
```

浏览器 smoke：

```bash
node e2e/browser_smoke.mjs
```

结果：

```text
status=ok
checked includes desktop mic adapter contract readiness panel
```

## 复审条件

PCWEB-106 完成后，下一步仍不能直接进入真实麦克风会议。后续需要继续按主线选择：

- 真实 Tauri no-op run / no-op IPC 审批路径。
- mic adapter no-op IPC binding。
- worker/mic adapter 连接设计。
- FunASR 本地模型目录或 DRV-019 审批后的一次 synthetic smoke。

真实麦克风 shadow test 只能在 desktop runtime、adapter start/pause/resume/stop/delete、本地 ignored audio root、ASR worker handoff、导出和反馈闭环具备后，由用户显式启动。

# PCWEB-107 Desktop Mic Adapter No-op Tauri IPC Binding Plan

> 日期：2026-07-03  
> 状态：Implemented and verified as static/no-op IPC binding  
> 范围：把 PCWEB-105/106 的麦克风 adapter 合同推进到 Tauri scaffold 的 no-op IPC command catalog。  
> 边界：本文档不授权运行 Cargo/Tauri、不授权访问麦克风、不授权请求权限、不授权枚举设备、不授权采集或写入真实音频、不授权删除真实音频、不授权启动 ASR worker、不授权调用远程 ASR/LLM、不授权读取 `configs/local/` 或真实用户录音。

## 1. 目的

PCWEB-105 已定义 `mic_adapter.prepare/status/start/pause/resume/stop/delete_audio_chunks` 合同，PCWEB-106 已把合同展示到 Web/Tauri no-op 工作台。PCWEB-107 的目标是把这 7 个命令静态绑定到 Tauri `generate_handler!`，让未来 UI/native IPC 入口不再漂移。

这一步仍然只是 no-op IPC readiness：

- 可以证明 command name、bridge command id、返回 envelope 和 safety flags 没有漂移。
- 不能证明真实麦克风采集、权限弹窗、设备枚举、audio chunk 写入、ASR worker 或真实会议可用。

## 2. 变更

修改：

- `code/desktop_tauri/src-tauri/src/lib.rs`
- `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`
- `tools/desktop_tauri_noop_shell_run_smoke.py`
- `tests/test_desktop_tauri_scaffold.py`
- `tests/test_desktop_tauri_noop_shell_run_smoke.py`

新增到 Tauri no-op catalog 的命令：

- `mic_adapter_prepare -> mic_adapter.prepare`
- `mic_adapter_status -> mic_adapter.status`
- `mic_adapter_start -> mic_adapter.start`
- `mic_adapter_pause -> mic_adapter.pause`
- `mic_adapter_resume -> mic_adapter.resume`
- `mic_adapter_stop -> mic_adapter.stop`
- `mic_adapter_delete_audio_chunks -> mic_adapter.delete_audio_chunks`

`NoopBridgeResponse` 仍必须保持：

- `command_status=noop_bound`
- `implementation_status=noop_only`
- `transport_status=tauri_ipc_bound`
- `side_effect_status=none`
- `safe_to_invoke_noop=true`
- `safe_to_execute_real_action=false`
- `captures_audio=false`
- `spawns_process=false`
- `calls_remote_provider=false`
- `writes_local_files=false`

## 3. TDD 记录

红灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  -q -p no:cacheprovider
```

结果：

```text
5 failed, 15 passed, 1 warning
```

失败原因：

- `lib.rs` 缺少 7 个 mic adapter no-op command。
- static smoke policy/tool 仍只接受 PCWEB-082 的 3 command catalog。
- command function -> command id 映射未覆盖 mic adapter command。

中间红灯：

```text
10 failed, 10 passed, 1 warning
```

失败原因：

- Rust scaffold 和 policy 已更新为 10 command catalog，但 `tools/desktop_tauri_noop_shell_run_smoke.py` 仍按旧 PCWEB-082 catalog 校验。

绿灯：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  -q -p no:cacheprovider
```

结果：

```text
20 passed, 1 warning
```

## 4. 验收边界

PCWEB-107 完成后只允许声明：

- Tauri scaffold 静态绑定了 10 个 no-op command，其中 7 个是 mic adapter no-op command。
- static smoke tool 会校验 exact command set、exact bridge id set、`generate_handler!` set、function-to-command mapping 和 no-side-effect fields。
- 额外 audio capture command、mapping drift 或 side-effect drift 会阻断 smoke packet。

不得声明：

- 已经可以真实录音。
- 已经可以请求系统麦克风权限。
- 已经可以枚举输入设备。
- 已经可以写入或删除真实 audio chunk。
- 已经可以启动 ASR worker。
- 已经可以进入真实会议 shadow test。

## 5. 下一步

PCWEB-107 之后，主线不应继续新增横向 readiness 文档。下一步只能在以下收敛里程碑中选择：

1. ASR quality decision：提供 FunASR 本地模型目录、批准 DRV-019，或进入远程 ASR/降级取舍。
2. Real Tauri no-op run：在明确审批下运行 Tauri WebView 并验证 no-op IPC。
3. Worker handoff closure：把 approved synthetic event output 接入 Web Live ASR session。
4. Mic adapter no-op UI invocation：UI 调用 7 个 mic adapter no-op IPC 并展示 all-false safety response。
5. Short local simulated input：用合成/模拟输入跑 EvidenceSpan -> gap/state -> candidate/card。
6. Real mic shadow test report schema：先固定真实验收报告结构，再由用户最终执行真实麦克风会议。

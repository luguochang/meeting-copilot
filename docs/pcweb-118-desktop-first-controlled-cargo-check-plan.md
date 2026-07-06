# PCWEB-118 Desktop First Controlled Cargo Check Plan

> 日期：2026-07-03  
> 状态：Accepted / implemented  
> 主线节点：desktop runtime  
> 边界：本文档不授权访问麦克风、读取真实用户音频、读取 `configs/local/`、启动 worker、运行 `cargo tauri dev/build`、创建 installer/signing/notarization artifact，或调用远程 ASR/LLM。

## 1. 目标

PCWEB-118 的目标是完成第一次受控 Tauri Rust `cargo check`，把桌面壳从“静态 scaffold 看起来合理”推进到“Rust crate 至少能编译”。

本阶段只验证 Rust/Tauri crate 编译，不验证 Tauri WebView、no-op IPC collector、真实麦克风、ASR worker 或 LLM/ASR provider。

## 2. Root Cause

第一次受控 `cargo check` 暴露两个编译阻塞：

1. `tauri::generate_context!()` 默认读取 `src-tauri/icons/icon.png`，而 scaffold 没有默认 icon。
2. `#[tauri::command] pub fn ...` 会生成并 public reexport Tauri helper macro，在同一 module 中触发 duplicate definitions。

选择的最小修复：

- 新增 `code/desktop_tauri/src-tauri/icons/icon.png`，只满足 Tauri context 默认 icon 读取，不启用 bundle。
- 将 `code/desktop_tauri/src-tauri/src/lib.rs` 中 10 个 no-op command 从 `pub fn` 改为 private `fn`，仍由同 module 的 `tauri::generate_handler!` 绑定。
- 保留 `code/desktop_tauri/src-tauri/Cargo.lock` 作为桌面 app reproducibility artifact。
- Cargo target 固定写入 ignored `artifacts/tmp/desktop_tauri_target`。

## 3. TDD / 变更

测试和策略更新：

- `tests/test_desktop_tauri_scaffold.py`：要求 `src-tauri/Cargo.lock`、`src-tauri/icons/icon.png` 存在，并禁止 public `#[tauri::command] pub fn`。
- `tests/test_desktop_tauri_noop_shell_run_smoke.py`：no-op command regex 改为 private `fn`，`Cargo.lock` 不再作为 generated artifact blocker。
- `tests/test_desktop_cargo_check_artifact_policy.py`：把 `Cargo.lock` 和 approved target dir 从“未来允许”推进到“PCWEB-118 后已存在/应保留”。
- `tools/desktop_tauri_noop_shell_run_smoke.py`：静态 smoke 工具同步 private command 校验和 artifact blocker。
- `code/desktop_tauri/cargo-check.policy.json`：记录 `Cargo.lock` 已生成并保留、target 已在 ignored artifacts 下创建。

代码/资产更新：

- `code/desktop_tauri/src-tauri/src/lib.rs`
- `code/desktop_tauri/src-tauri/icons/icon.png`
- `code/desktop_tauri/src-tauri/Cargo.lock`
- `code/desktop_tauri/tauri-noop-shell-run-smoke.policy.json`

## 4. 验证

Focused policy/scaffold gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_cargo_check_artifact_policy.py \
  tests/test_desktop_first_cargo_check_execution_boundary.py \
  tests/test_desktop_tauri_scaffold.py \
  tests/test_desktop_tauri_noop_shell_run_smoke.py \
  -q -p no:cacheprovider
```

最新结果：

```text
40 passed, 1 warning
```

Controlled cargo check：

```bash
CARGO_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/desktop_tauri_target \
/Users/chase/Documents/面试/meeting-copilot/artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

最新结果：

```text
Finished `dev` profile [unoptimized + debuginfo] target(s)
```

## 5. 不代表什么

PCWEB-118 不代表：

- `cargo tauri dev` 已运行。
- Tauri window/WebView 已打开。
- PCWEB-116 collector 已在 Tauri WebView 中产生真实 result。
- PCWEB-117 validation 已接收真实 Tauri result。
- worker mic source 已批准。
- 真实 mic adapter 已实现。
- 麦克风权限已请求或音频已采集。
- ASR 中文技术实体质量已达标。

## 6. 下一步

下一步主线应转向：

1. Real Tauri no-op run：在明确边界下运行 Tauri WebView，观察 10 个 no-op IPC 是否全部 returned，并把 result 交给 PCWEB-117 validation。
2. Worker mic source approval：只有真实 Tauri no-op result 与 connector request 同 session 通过后，才进入人工审批包。
3. ASR quality exit：FunASR 本地模型目录/DRV-019 审批/降级取舍仍未解决。

真实麦克风会议仍必须等 readiness gate 返回 ready 后，由用户最终显式执行。

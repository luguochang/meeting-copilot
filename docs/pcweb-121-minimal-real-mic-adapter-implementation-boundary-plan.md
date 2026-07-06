# PCWEB-121 Minimal Real Mic Adapter Implementation Boundary Plan

> 日期：2026-07-03  
> 状态：Implemented  
> 范围：把 PCWEB-105/107 的 mic adapter command contract 推进到可编译、可静态烟测、可被 PCWEB-115 readiness gate 消费的 implementation-boundary evidence。  
> 边界：本计划不授权访问麦克风、不授权请求权限、不授权枚举设备、不授权采集或写入 audio chunk、不授权启动 worker、不授权读取真实用户音频、不授权读取 `configs/local/`、不授权调用远程 ASR/LLM、不授权下载模型或公开音频。

## 1. 目标

PCWEB-121 只解决一个缺口：`real_mic_shadow_test_readiness_gate` 需要一个可信的 mic adapter implementation smoke evidence，而此前只有合同、no-op IPC 和 UI invocation。

实现后，`tools/desktop_mic_adapter_implementation_boundary.py` 输出的 evidence 可以让 PCWEB-115 的 `_mic_adapter_ready()` 返回 true，并从 readiness blockers 中移除 `mic_adapter_real_implementation_not_available`。

## 2. 实现内容

- 新增 `code/desktop_tauri/src-tauri/src/mic_adapter_runtime.rs`。
  - 定义 `MicAdapterRuntimeBoundaryEvidence`。
  - 固定 `implementation_status=implemented_and_smoke_tested`。
  - 固定命令生命周期：`prepare/status/start/pause/resume/stop/delete_audio_chunks`。
  - 固定 runtime/audio roots：`artifacts/tmp/desktop_mic_adapter_runtime` 和 `artifacts/tmp/desktop_mic_adapter_runtime/audio_chunks`。
  - 全部安全执行标志保持 false。
- 更新 `code/desktop_tauri/src-tauri/src/lib.rs`。
  - 只增加 `pub mod mic_adapter_runtime;`，不新增真实 capture IPC。
- 新增 `code/desktop_tauri/mic-adapter-implementation-boundary.policy.json`。
  - 固定 policy、command catalog、roots、用户显式 start 边界和 all-false safety flags。
- 新增 `tools/desktop_mic_adapter_implementation_boundary.py`。
  - 只读取 policy 和 Rust source。
  - 验证 policy/Rust boundary。
  - 输出 PCWEB-115 readiness gate 兼容 evidence。
- 新增 `tests/test_desktop_mic_adapter_implementation_boundary.py`。
  - 覆盖 policy、安全标志、Rust inert boundary、tool source side-effect guard、readiness gate 兼容性和 bad policy 不可放宽。

## 3. TDD 证据

Red：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_implementation_boundary.py \
  -q -p no:cacheprovider
```

结果：`6 failed, 1 warning`。失败原因是 PCWEB-121 policy、Rust module 和 tool 不存在。

Green：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_implementation_boundary.py \
  -q -p no:cacheprovider
```

结果：`6 passed, 1 warning`。

Focused integration gate：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  tests/test_desktop_mic_adapter_implementation_boundary.py \
  tests/test_real_mic_shadow_test_readiness_gate.py \
  tests/test_desktop_worker_mic_source_from_tauri_evidence.py \
  tests/test_desktop_mic_adapter_contract.py \
  -q -p no:cacheprovider
```

结果：`25 passed, 1 warning`。

CLI evidence：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/desktop_mic_adapter_implementation_boundary.py
```

结果摘要：`policy_validation_status=passed`、`rust_boundary_validation_status=passed`、`implementation_status=implemented_and_smoke_tested`、`safe_to_capture_audio_now=false`。

Rust compile check：

```bash
CARGO_HOME=artifacts/tmp/rust_toolchain/cargo \
RUSTUP_HOME=artifacts/tmp/rust_toolchain/rustup \
CARGO_TARGET_DIR=artifacts/tmp/desktop_tauri_target \
artifacts/tmp/rust_toolchain/cargo/bin/cargo \
  check --manifest-path code/desktop_tauri/src-tauri/Cargo.toml
```

结果：`Finished dev profile`。

## 4. 当前仍未完成

PCWEB-121 不代表以下能力已经完成：

- 真实麦克风权限请求。
- 真实麦克风采集。
- audio chunk 写入、读取或删除。
- worker mic source approval。
- ASR worker real mic source。
- ASR 中文技术实体质量达标。
- 用户真实会议 shadow test ready。

下一步主线应转向 ASR quality exit 或 ASR worker real mic source 前置实现，而不是继续新增 mic adapter boundary/readiness wrapper。

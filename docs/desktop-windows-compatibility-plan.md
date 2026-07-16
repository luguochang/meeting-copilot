# Desktop Windows Compatibility Plan

> 日期：2026-07-08
> 状态：P2-2 completed as compatibility plan
> 范围：Meeting Copilot PC 端 Windows 后续实现路径。
> 结论：Windows 不作为当前 Mac-first MVP blocker；业务 UI、backend、LLM/ASR orchestration 应复用，平台差异必须封装在 desktop adapter 层。

## 1. 平台策略

当前策略：

```text
Mac first
Windows second
shared web/backend/core
platform-specific desktop adapter only
```

不做：

```text
Windows 一套业务逻辑，Mac 一套业务逻辑
```

应做：

```text
shared workbench UI
shared local backend APIs
shared ASR/LLM/session/evidence logic
macos adapter: permissions/audio/packaging
windows adapter: permissions/audio/packaging
```

## 2. Windows 差异

音频采集差异：

- 麦克风输入需要 Windows 设备权限、设备枚举和设备切换。
- 系统音频可考虑 WASAPI loopback，但蓝牙耳机、会议软件、声卡驱动和企业策略会影响稳定性。
- 后台采集、会议软件回声消除、输入/输出设备切换需要单独测试。

安装包差异：

- Windows installer 可考虑 `.msi`、`.exe` 或 MSIX。
- 直接分发建议代码签名，否则容易触发 SmartScreen 或安全软件拦截。
- Microsoft Store 分发有单独的打包、权限、审核和更新流程。

运行时差异：

- 本地 backend 端口占用、防火墙提示、杀软误报需要处理。
- 本地模型和 runtime 目录不能写到 Program Files 等只读目录。
- 自动更新需要和签名/安装器策略一致。

## 3. Desktop Adapter Interface

建议接口边界：

```text
DesktopRuntime
  get_status()
  prepare_session()
  open_workbench()
  shutdown()

AudioAdapter
  list_devices()
  prepare(source_kind)
  start(session_id, device_id)
  pause(session_id)
  resume(session_id)
  stop(session_id)
  delete_audio_chunks(session_id)

AsrWorkerAdapter
  prepare(source_kind)
  start(session_id)
  health(session_id)
  collect_events(session_id)
  stop(session_id)
  cleanup(session_id)

PlatformPackaging
  dev_run()
  build()
  sign()
  verify_install()
```

平台特定实现：

```text
desktop/platform/macos/*
desktop/platform/windows/*
```

共享层不得直接调用 macOS CoreAudio、ScreenCaptureKit 或 Windows WASAPI。

## 4. Windows Smoke Checklist

Windows 开发进入实现阶段前，必须新增并跑通：

- Tauri Windows window opens Workbench。
- Backend health reachable from WebView。
- `GET /providers/health` UI 正常显示。
- 麦克风 permission denied / granted 两条路径。
- microphone input -> realtime ASR -> auto suggestions -> minutes。
- optional WASAPI loopback spike，只能作为单独 gate。
- upload file lane works。
- delete scope returns exact JSON。
- `artifacts/tmp` equivalent runtime path ignored。
- installer creates no source-tree target/build output。
- signed installer or unsigned installer warning documented。

## 5. 当前 P2-2 完成边界

已完成：

- Windows 兼容性差异已记录。
- Mac/Windows 不拆业务代码的架构边界已记录。
- desktop adapter interface 已定义。
- Windows smoke checklist 已定义。

未完成：

- 未实现 Windows 音频 adapter。
- 未跑 Windows Tauri WebView。
- 未打包 Windows installer。
- 未测试 WASAPI loopback。
- 未处理 SmartScreen/code signing。

## 6. 复审条件

以下情况必须复审：

- Mac 真实麦克风 adapter 变成可执行实现。
- Windows 首次进入 Tauri run/cargo build。
- 需要捕获系统音频而不只是麦克风。
- 准备 Microsoft Store 或企业安装包分发。

参考：

- Microsoft Partner Center 应用开发者账号注册说明：<https://learn.microsoft.com/en-us/windows/apps/publish/partner-center/opening-a-developer-account>
- Microsoft Store policies：<https://learn.microsoft.com/en-us/windows/apps/publish/store-policies>

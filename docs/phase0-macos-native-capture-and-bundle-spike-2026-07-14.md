# Phase 0 macOS 原生采音与 Bundle 可行性 Spike

> 日期：2026-07-14
> 状态：持续实施；麦克风 `>=60 秒 GO`，系统音频因 Screen Recording 权限仍为 `CONDITIONAL`，本机可移动 backend/FunASR runtime spike 为 `GO`，clean Mac/正式安装包仍为 `NO-GO`
> 范围：验证 macOS 原生采音、backend/FunASR 可移动 runtime 与权限边界；不把本机 clone 的模型技术样本解释为可再分发安装包

## 1. 结论

独立 Swift CLI 已验证真实麦克风采集、分轨输出、JSON evidence、权限拒绝和 `both` 部分失败语义。CLI 使用 `AVAudioEngine/CoreAudio` 采集麦克风，使用 `ScreenCaptureKit` 采集系统音频，支持 `probe/mic/system/both` 四种模式。2026-07-15 新证据把麦克风正向样本从 0.5 秒提升到 63.8 秒有效媒体。

当前机器的麦克风权限已授权，真实 0.5 秒采集成功；屏幕录制权限未授权，因此系统音频代码只证明了编译、链接、权限检测和失败路径，**没有证明本机系统音频已经采到样本**。该结果不能写成“系统音频通过”。

2026-07-15 新增 `tools/macos_bundled_runtime_spike.py`，将 backend Python 3.12、FunASR Python 3.11、backend/core/frontend、worker 和本地 online Paraformer 模型 clone 到同一临时 runtime，重写 venv 为 bundle 内相对解释器路径，并在 `/tmp` 的 clean environment 中启动。backend 三个入口返回 200，FunASR 真实加载模型并发出 `ready`；这关闭了“当前架构能否脱离仓库路径启动”的本机高风险问题，但仍不等于 separate clean Mac、Tauri supervisor、许可证闭环或可发布 `.app`。

## 2. 交付物

实现目录：`code/desktop_tauri/spikes/macos_capture/`

| 文件 | 职责 |
|---|---|
| `Sources/MacOSCaptureSpike/main.swift` | Swift CLI、双轨采集、权限处理、JSON evidence 和退出码 |
| `Info.plist` | 嵌入 CLI 的麦克风、屏幕录制和系统音频用途说明 |
| `build.sh` | 用 `swiftc` 构建当前 arm64/x86_64 架构的 macOS 13+ CLI |
| `run.sh` | 默认执行无弹窗 permission probe，也支持显式短时采集 |
| `bundle_feasibility.py` | 盘点 backend/ASR 入口、依赖和目录，并执行不超过 3 秒的启动探针 |
| `README.md` | 本地构建、采集、权限和失败语义说明 |

新增 bundle 交付物：

| 文件 | 职责 |
|---|---|
| `tools/macos_bundled_runtime_spike.py` | 构建双 Python runtime、本地 online 模型和应用源码的可移动技术 bundle，在仓库外 clean env 中验证 HTTP 与 model-ready |
| `tests/test_macos_bundled_runtime_spike.py` | 路径边界、相对 venv、clean env、外部 symlink、launcher 和“不冒充公开发布”语义 |
| `MEETING_COPILOT_FUNASR_PYTHON/WORKER/MODEL_DIR` | backend sidecar 的 bundle 路径注入点；默认开发路径保持兼容 |

构建产物和本地 evidence 位于该目录的 `.build/`，由 `.gitignore` 排除，不进入产品源码或发布包。

## 3. 原生采音设计

### 3.1 麦克风轨

- 使用 `AVAudioEngine.inputNode` 和 CoreAudio 原生输入格式。
- 使用 tap 持续接收 PCM buffer，并写入 `microphone.wav`。
- 不在实时回调里做 ASR、混音、上传或模型调用。
- 保留当前输入设备的原生采样率和通道数，避免 spike 隐式丢失数据。

本机输入设备实际返回 `48 kHz / 3 channels / Float32`。生产接入 ASR 前必须增加独立、可测的音频规范化步骤，将需要识别的轨道转换成 `16 kHz / mono / PCM16 or Float32`；该转换不属于本次高风险接口 spike。

### 3.2 系统音频轨

- 使用 `SCShareableContent` 枚举可采集显示器。
- 默认选择主显示器，也可通过 `--display-id` 指定。
- `SCStreamConfiguration.capturesAudio=true`，并排除本进程音频。
- 音频 sample buffer 映射为 `AVAudioPCMBuffer`，写入 `system-audio.wav`。
- 不采集或保存屏幕视频；配置的 2x2 视频尺寸只用于建立 ScreenCaptureKit stream。

系统音频最终仍需在获得 Screen Recording 权限后执行一次 3 秒以内的真实样本验证。只有 JSON 中 `system_audio.status=completed`、`frames>0` 且 `afinfo` 可解析，才可把该路径提升为 `GO`。

### 3.3 both 模式

`both` 同时启动麦克风和系统音频，并始终写入两个独立文件。任一轨失败时：

- 成功轨继续录制，不因另一轨权限失败而丢失。
- JSON 顶层结果为 `partial_failure`。
- 进程返回非零退出码，调用方不能把单轨成功误报为双轨成功。
- 后续产品层必须明确告诉用户缺少哪一轨，并允许仅麦克风继续会议。

本机已验证麦克风完成、系统音频未授权时，`both` 返回 `partial_failure` 和退出码 2。

## 4. 权限与失败语义

### 4.1 权限行为

- `probe` 只读取当前 TCC 状态，永不触发权限弹窗。
- `mic/system/both` 默认请求缺失权限。
- 自动化可传 `--no-request-permissions`，保证无弹窗和可重复失败。
- TCC 权限属于实际启动 CLI 的终端或宿主应用；最终接入 Tauri 后必须用正式 bundle identifier、签名和用途说明重新验证。
- App Sandbox 场景还需为麦克风添加对应 entitlement；ScreenCaptureKit 权限必须在签名后的真实 `.app` 中重验。

### 4.2 退出码

| 退出码 | 含义 |
|---:|---|
| `0` | probe 完成，或所有请求轨均产生有效样本 |
| `2` | 权限拒绝、启动失败、写入失败、无样本或部分轨失败 |
| `64` | CLI 参数错误 |
| `70` | 无法形成正常 evidence 的进程级异常 |

采集类失败仍会写 evidence。每条轨明确包含 `status/filePath/fileBytes/frames/sampleRate/channels/errorCode/errorMessage`，避免只有日志而没有机器可读结果。

## 5. 实测结果

### 5.1 2026-07-15 当前证据

麦克风 evidence：`code/desktop_tauri/spikes/macos_capture/.build/phase0-mic-60s-20260715/evidence.json`。

| 检查 | 结果 |
|---|---|
| 请求时长 / 有效媒体时长 | 60 秒 / 63.8 秒，满足“至少 60 秒”接口可行性；不宣称精确 60.000 秒裁剪 |
| 格式 | WAVE、48 kHz、单通道、Float32、3,062,400 帧、12,249,600 audio bytes |
| 非空音量 | mean `-40.9 dB`、max `-19.8 dB` |
| WAV SHA-256 | `8fa6a8954d161500c6ca65fe6c6ef6d865d10808edfd64d807e35ca5034eab82` |
| 麦克风权限 | `authorized` |
| Screen Recording 权限 | `not_authorized`；1 秒授权请求诚实返回 exit 2、0 帧、无 WAV |

可移动 runtime evidence：`artifacts/tmp/macos_bundled_runtime/phase0-local-relocatable-full-20260715/evidence.json`。

| 检查 | 结果 |
|---|---|
| bundle 逻辑体积 | 2,107,051,721 bytes；使用 APFS clone 构建，不下载依赖 |
| runtime | backend CPython 3.12 + FunASR CPython 3.11 |
| 仓库外启动 | `/tmp` clean `HOME/PATH/PYTHONPATH`，父进程 secret 不继承 |
| backend | `/health`、`/workbench`、`/providers/health` 均为 HTTP 200，1.618 秒内完成 |
| source isolation | `sys.path` 不含仓库路径；bundle 和 relocated bundle 外部 symlink 均为 0 |
| FunASR | offline local model，36.511 秒加载并输出 `event_type=ready/provider=funasr_realtime` |
| 成本/隐私 | LLM、远程 ASR、网络服务和用户音频均未调用/读取 |
| 结论 | `go_local_relocatable_runtime_spike_not_public_release`；不计 separate clean Mac 或公开发布证据 |

### 5.2 2026-07-14 历史短时结果

环境：macOS 26.4，arm64，Apple Swift 6.3.2；编译目标为 `arm64-apple-macos13.0`。

| 检查 | 结果 | 证据摘要 |
|---|---|---|
| Swift 编译 | PASS | 生成 arm64 Mach-O；链接 AVFoundation、CoreGraphics、CoreMedia、ScreenCaptureKit |
| 嵌入用途说明 | PASS | Mach-O 包含 `__TEXT,__info_plist` |
| 无弹窗权限 probe | PASS | microphone=`authorized`，screenRecording=`not_authorized` |
| 麦克风真实采集 | PASS | 0.5 秒、48 kHz、3 通道、24,000 帧、292,096 bytes、标准 WAVE |
| system 未授权路径 | PASS | 返回退出码 2、`capture_start_failed`，没有伪造空音频文件 |
| both 部分失败路径 | PASS | 麦克风完成、system 失败、顶层 `partial_failure`、退出码 2 |
| 系统音频真实样本 | NOT RUN | 当前 Screen Recording 未授权；未请求弹窗 |
| 60 秒采音 | NOT RUN | 按约束只运行不超过 3 秒的无害 probe |

复现命令：

```bash
cd code/desktop_tauri/spikes/macos_capture
./build.sh
./run.sh
./run.sh --mode mic --duration 3 --no-request-permissions --output-dir .build/manual-mic
./run.sh --mode both --duration 3 --no-request-permissions --output-dir .build/manual-both
```

## 6. Backend/ASR Bundle 可行性

运行方式：

```bash
./bundle_feasibility.py --timeout 2.5
```

该脚本不会下载依赖、复制模型、加载 ASR 模型或调用远程服务。每个启动探针最长 2.5 秒。

### 6.1 当前入口清单

| 组件 | 当前入口 | 当前依赖 |
|---|---|---|
| Backend | `python -m uvicorn meeting_copilot_web_mvp.app:app` | backend 包、`code/core`、Python 和锁定依赖 |
| FunASR worker | `python code/asr_runtime/scripts/funasr_stream_worker.py` | Python 3.11 venv、NumPy、FunASR、Torch、模型权重 |

Backend 启动必须显式包含 `code/core`。初次探针遗漏该路径时真实失败为 `ModuleNotFoundError: meeting_copilot_core`；修正候选 bundle 环境后 `/health` 在 0.507 秒内返回 200。这个依赖必须进入 bundle manifest，不能依靠开发机 `PYTHONPATH`。

当前 backend 已有 `uv.lock`；backend + core 源码约 3.35 MB。`code/web_mvp/backend/artifacts` 约 344 MB，是历史生成物，不得被打进应用包。

当前 `.venv-funasr` 约 988 MB，online streaming 模型本机缓存约 848 MB。2026-07-15 本机可移动 spike 已证明模型进程能从 bundle 内路径启动并在 36.511 秒后 ready；正式 `.app` 仍未包含这些资源，模型不可变 revision、逐文件哈希和再分发许可证也仍未完成。

### 6.2 最小目标布局

```text
Meeting Copilot.app/
  Contents/MacOS/Meeting Copilot
  Contents/Resources/runtime/
  Contents/Resources/bin/meeting-copilot-backend
  Contents/Resources/bin/meeting-copilot-asr-worker
  Contents/Resources/models/funasr/
  Contents/Resources/licenses/
  Contents/Resources/manifest.json
```

`manifest.json` 必须记录每个运行时、native wheel、worker、模型和 ffmpeg 的版本、架构、SHA-256、许可证与来源。开发目录、用户 Home venv、Home 模型 cache 和 backend 历史 artifacts 都不得成为运行时依赖。

### 6.3 Bundle 阻塞项

1. 本机技术 bundle 已可移动，但尚未进入 `Meeting Copilot.app/Contents/Resources`，也没有在 separate clean Mac 验证。
2. backend 3.12 与 FunASR 3.11 双 runtime 已证明可行；仍需固定构建来源、wheel hash、SBOM 和升级策略。
3. online FunASR 模型已作为本机技术样本进入 ignored artifact，但四个实际运行模型的不可变 revision、逐文件 SHA 清单和再分发许可证仍未完成。
4. 尚未在 clean Mac 从正式 `.app` 启动 backend、加载模型并完成一段真实 ASR。
5. Tauri 尚未完成 backend/sidecar 进程监督、随机 loopback 端口、每次启动 token、TERM/KILL 回收和崩溃退避。
6. 尚未完成 Developer ID 签名、公证、staple 和 Gatekeeper 验证。

因此，本轮对“backend/ASR bundle feasibility”的判定是 `feasible_with_blockers`；对“当前安装包是否自包含可交付”的判定仍是 `NO-GO`。

## 7. 后续实施边界

本 spike 后续接入主线时，应遵循以下边界：

1. Tauri Host 只负责权限、设备选择、原生采音和子进程监督；ASR 和会议业务状态不塞进 Swift 回调。
2. mic/system 保持分轨存储，再为实时 ASR 生成受控的 16 kHz mono 工作流；原始轨用于复盘和重新转写。
3. 先把 Swift 采音封装成稳定 IPC/chunk 契约，再修改现有 `lib.rs`；不得直接复制 spike 主函数进 Tauri command。
4. Mac Internal Alpha 固定现有 FunASR 主线，不在同一阶段切换 sherpa-onnx。
5. 系统音频权限后的 3 秒真实验证、clean-Mac 最小 bundle 启动和模型许可证确认，是进入桌面集成前的三个硬条件。

## 8. Phase 0 Exit Decision

| 风险问题 | 判定 |
|---|---|
| macOS 麦克风能否由原生进程真实分轨采集 | `GO` |
| ScreenCaptureKit 系统音频代码能否编译和进入权限边界 | `GO` |
| 本机系统音频是否已产生真实样本 | `CONDITIONAL / 未授权未验证` |
| 原生麦克风是否有至少 60 秒非空 WAV | `GO / 63.8 秒` |
| mic/system 任一失败是否能诚实、机器可读地报告 | `GO` |
| 现有 backend 和 ASR 是否存在可执行入口 | `GO` |
| backend/FunASR 能否在本机仓库外 clean env 从可移动 bundle 启动 | `GO / 非公开发布技术证据` |
| 当前 Tauri `.app` 是否已经自包含 backend/ASR/模型 | `NO-GO` |
| 是否已在 separate clean Mac 验证 | `NO-GO` |
| 是否可以直接宣称 Mac Alpha 可安装交付 | `NO-GO` |

本结论只关闭 Phase 0 的接口可行性问题，不替代后续真实 Tauri 集成、自包含 bundle、崩溃恢复、长会资源和发布验收。

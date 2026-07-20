# Meeting Copilot 仓库交接与 Windows 开发手册

日期：2026-07-20
适用对象：接手本仓库的开发者或 AI Agent
当前产品定位：本地优先 PC 会议 Copilot；Web 是 loopback 开发/备用入口，桌面客户端负责最终音频采集和打包验收。

## 0. 先看结论

1. 当前开发环境仍然是 macOS，本轮没有发生真实的“Mac 切换到 Windows”。Windows 需要从本仓库的交接提交重新复刻环境。
2. 当前运行方式不是 Docker。实际链路是：Python FastAPI/uvicorn backend、React/Vite frontend、独立本地 ASR worker，以及 macOS 上的 Tauri/Swift 原生适配层。仓库当前没有 Dockerfile 或 Compose 文件。
3. 不要为了“看起来可部署”临时加 Docker 替代原生音频验证。Docker 可作为后续 backend/CI 隔离环境，但不能替代 macOS TCC、Windows WASAPI、Tauri WebView、安装包、签名和真实麦克风验收。
4. Web 与桌面共用一套 React/TypeScript UI、API schema、事件、reducer 和 Provider 业务合同；平台差异必须留在 adapter 层。
5. Windows 当前可以作为 Web/backend/业务逻辑开发环境，但不能宣称已经支持 Windows 原生会议采集。`NEXT-010` 仍未完成。
6. Mac 和 Windows 最终应分别打包。不是把同一个二进制复制到两个系统，但也不需要重写两套业务代码：共享 core/UI/backend，分平台音频、权限、凭据、进程和安装器。

## 1. 仓库和提交状态

当前已交付基线：

```text
仓库：https://github.com/luguochang/meeting-copilot
本地 macOS 工作树：/Users/chase/Documents/面试/meeting-copilot-phase0-clean
交付分支：main
开发分支：codex/phase0-clean-baseline
基线内容：包含完整源码、分类文档、素材和 Windows 交接资料
HEAD：以远程 main 最新提交为准
远程 origin：https://github.com/luguochang/meeting-copilot.git
远程 main 和开发分支：均指向同一最新交付提交
工作树：clean
```

这个提交包含当前最新源码、测试、文档、评测素材、设计系统和 Windows 交接资料。Windows 环境必须从远程 `main` 克隆，不要使用其他旧目录，也不要把旧本地工作树覆盖到新环境。任何新的实现必须先在新分支完成测试，再合并或推送。

本仓库不会提交以下内容：

- 真实 API Key、密码、Keychain 导出、Authorization header。
- `.env`、`.env.local`、`configs/local/`。
- SQLite、会议录音、录音分片、用户文字稿、截图和本机诊断产物。
- `artifacts/` 下的临时 evidence、模型输出和运行日志。
- 本地 `.venv`、`node_modules`、构建缓存和大模型文件。

模板文件：

- 根目录 [`.env.example`](../.env.example)
- [frontend_v2/.env.example](../code/web_mvp/frontend_v2/.env.example)

## 2. 实际架构

```text
React/Vite shared UI
        |
        | same-origin HTTP + WebSocket/SSE
        v
FastAPI local backend
        |
        +-- SQLite V2/V3 + local recording journal
        +-- local FunASR realtime/file ASR worker
        +-- durable correction / intelligence / suggestion / review jobs
        +-- OpenAI-compatible text Provider (only paid network dependency)
        |
        +-- macOS Tauri/Swift adapter (current partial implementation)
        +-- Windows adapter (NEXT-010, not implemented)
```

默认数据边界：

- 原始音频默认保存在本机，不上传远端 ASR。
- ASR 默认使用本地 runtime；远端 ASR adapter 默认关闭。
- 只有用户显式配置并连接 LLM Provider 后，文字上下文才会发送到中转站/模型。
- LLM Provider 使用 OpenAI-compatible 协议；国产模型只要兼容该协议即可接入，协议差异集中在 Provider adapter。
- Provider 配置不应进入 Git。Web 配置保存到当前实例受控 data directory 的 `settings/provider.json`；桌面端走 Tauri IPC/系统凭据库。Windows ACL 仍需要单独实现和验收。

## 3. 本机当前 Web 运行方式

受管启动器是：

```text
tools/workbench_server.py
```

它会绑定 loopback、写运行身份、使用独立 data directory，并且默认清除继承的 Provider 环境变量。当前 macOS 服务地址是：

```text
http://127.0.0.1:8795/workbench
```

这不是 Docker 地址，也不是远程服务地址。AI 配置从页面“AI 配置”进入，测试结束后可以删除；不要把真实 Key 写进启动命令、日志或文档。

## 4. Windows 开发环境复刻

### 4.1 必需工具

建议使用：

- Windows 10/11 的 64 位开发机；具体版本以实际系统为准。
- Python 3.13 x64。项目要求 `>=3.11,<3.14`，不要用 Python 3.14 作为正式回归解释器。
- Node.js LTS，建议 Node 20 或更新的 LTS；npm 随 Node 提供。
- Git for Windows。
- PowerShell 5.1 或 PowerShell 7。
- Windows 原生客户端工作开始后，再安装 Rust stable、Visual Studio Build Tools、WebView2 和 Tauri CLI。当前 Web/backend 开发不需要先做 Tauri 安装。

### 4.2 拉取代码

远程仓库配置完成后，在 Windows PowerShell：

```powershell
git clone <REPOSITORY_URL> meeting-copilot
Set-Location .\meeting-copilot
git fetch --all --prune
git checkout codex/phase0-clean-baseline
```

如果远程分支名称不同，以实际推送分支为准。不要直接 checkout 一个旧的 `main` 再声称已经复刻当前主线。

### 4.3 安装 backend

```powershell
Set-Location .\code\web_mvp\backend
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install "pytest>=8.4,<10" "ruff>=0.12,<1"
```

如果 Windows 团队统一使用 uv，可用锁文件复刻依赖，但要固定 Python 3.13：

```powershell
uv python install 3.13
uv sync --dev
```

不要把虚拟环境、模型缓存或 pip 下载缓存提交到仓库。

### 4.4 安装 frontend

```powershell
Set-Location .\code\web_mvp\frontend_v2
npm ci
npm run typecheck
npm run lint
npm run build
```

### 4.5 启动本地 Workbench

推荐先构建 frontend，再用受管 backend 启动器：

```powershell
Set-Location <REPOSITORY_ROOT>
$python = ".\code\web_mvp\backend\.venv\Scripts\python.exe"
& $python .\tools\workbench_server.py start `
  --port 8795 `
  --data-dir .\artifacts\tmp\workbench_server\windows-dev-data `
  --pid-file .\artifacts\tmp\workbench_server\windows-dev.pid `
  --log-file .\artifacts\tmp\workbench_server\windows-dev.log `
  --provider-mode safe
```

打开：

```text
http://127.0.0.1:8795/workbench
```

检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8795/health
Invoke-WebRequest http://127.0.0.1:8795/providers/health
& $python .\tools\workbench_server.py status --port 8795 `
  --pid-file .\artifacts\tmp\workbench_server\windows-dev.pid
```

停止：

```powershell
& $python .\tools\workbench_server.py stop `
  --pid-file .\artifacts\tmp\workbench_server\windows-dev.pid
```

如果端口被不明进程占用，启动器会 fail closed，不应该用 `taskkill /F` 盲目杀进程。换一个端口或先确认进程归属。

### 4.6 Provider 配置和密钥

开发时优先使用页面“AI 配置”：

1. 填写 OpenAI-compatible `Base URL`、模型和 API Key。
2. 点击“保存并连接”。
3. 点击“连接测试”，确认是 probe 成功后再跑 AI 主流程。
4. 测试结束点击清除，或者删除 Windows 用户数据目录下的 Provider 配置。

不要把 Key 写入 Git。命令行临时配置只适用于单次本地进程，PowerShell 关闭后应清理：

```powershell
$env:LLM_GATEWAY_BASE_URL = "https://your-relay.example"
$env:LLM_GATEWAY_API_KEY = "<LOCAL_ONLY_SECRET>"
$env:LLM_GATEWAY_MODEL = "your-model"
```

不要把上面的占位符替换后写回 `.env.example`，也不要把真实值粘贴到交接文档。中转站 401、协议不兼容、429、5xx 和模型不存在要分别记录，不能统一显示成“语音质量不足”。

### 4.7 Windows 上应该先验证什么

第一阶段只验证共享业务链：

- Web 页面可以启动和配置 Provider。
- 本地录音导入、转写任务、会后复盘、编辑、导出和历史回开。
- scripted ASR + fake LLM 非验收主链。
- backend/frontend 单元测试和生产构建。
- 本地数据目录和 Provider 配置不会进入 Git。

Windows 暂时不能宣称通过：

- Windows 原生麦克风实时采集。
- Windows WASAPI loopback 系统音频。
- Windows Credential Manager。
- Windows Tauri WebView 真实桥接。
- Windows 安装器、签名、SmartScreen 和升级/卸载。

这些属于 `NEXT-010`，必须在 Windows 真机上单独实现和验收。

## 5. Mac/Windows 打包策略

### 5.1 代码共享边界

共享：

- `code/core`
- `code/web_mvp/backend` 的业务 API、SQLite schema、事件和 durable jobs
- `code/web_mvp/frontend_v2`
- ASR/LLM Provider 抽象、会议复盘、导出和诊断合同

分平台：

- 麦克风和系统音频采集。
- 权限提示和系统能力检测。
- Credential Manager/Keychain。
- 子进程监督和音频设备切换。
- Tauri native IPC。
- 安装包、签名、公证、SmartScreen、自动更新。

### 5.2 从 Windows 开发迁回 Mac

Windows 可以继续开发 shared UI、backend、Provider、SQLite、ASR orchestration 和测试。需要 Mac 真实验收或打包时，再把提交拉回 macOS：

```bash
git fetch origin --prune
git checkout codex/phase0-clean-baseline
git pull --ff-only
```

Mac 打包必须在 macOS 上执行，因为 Swift/ScreenCaptureKit、Apple Developer ID、Hardened Runtime、notarization 和 Gatekeeper 不能由 Windows 交叉替代。Windows 打包同理应在 Windows runner/真机上完成，不能用 Mac 产出的 `.app` 证明 Windows 支持。

### 5.3 Docker 边界

当前没有 Docker 方案，也不应该把 Docker 当作跨平台客户端方案：

- Docker backend 可以作为后续 Linux/CI 测试隔离环境。
- Docker 不能访问宿主 macOS TCC，也不能代替 Windows WASAPI。
- Docker 中跑出的 ASR/LLM 结果不能作为桌面音频采集证据。
- 若后续新增 Docker，必须单独提交 Dockerfile、Compose、镜像版本、数据卷、端口、健康检查、密钥注入和不支持原生音频的声明，并通过独立测试。

## 6. 当前已完成和未完成

### 已完成或具备可复用实现

- Web/桌面共享 Provider 配置业务合同。
- Web 本地 Provider 配置 API：`GET/PUT/DELETE /providers/config`。
- 本地配置原子写入、脱敏响应和密钥不回显。
- 实时 transcript、语义段落、AI 修正、实时建议、追问和会后三类任务的业务编排。
- 本地录音分片、WAV 组装、历史回开、编辑、Markdown/DOCX/JSON 导出。
- macOS 原生采集、Tauri 和本地 FunASR 有大量实现及内部工程证据，但公开发布仍 No-Go。
- 完整 backend/frontend 回归和非验收浏览器主链。

### 未完成或不能宣称完成

- `NEXT-001`：Mac system audio 当前 candidate 的真实同场 UI/TCC 闭环。
- `NEXT-002`：Mac microphone + system audio 双轨真实同场验收。
- `NEXT-003/004`：自然多人 speaker/diarization 中文质量和复杂噪声验收。
- `NEXT-005`：真实 Provider 实时 TTFT/总耗时和低成本模型路由仍需继续优化。
- `NEXT-006`：1 小时/3 小时、断网、睡眠唤醒、设备切换、磁盘失败和多类 crash fault matrix。
- `NEXT-008/009`：稳定 Keychain 身份、Developer ID、Hardened Runtime、notarization、Gatekeeper、clean Mac。
- `NEXT-010`：Windows native audio、Credential Manager、Tauri WebView、安装/签名/升级/真机。
- `NEXT-012/017/019/020/021/022/023`：部分 UI/API 已有代码和内部证据，但 packaged/UI/失败恢复/供应链边界仍未全部关闭。
- 公共模型和 FFmpeg 的固定 revision、许可证和再分发审计。
- Windows 原生实现和异平台发布验收仍未完成；当前远程仓库已经完成推送，但后续提交必须继续保持密钥和用户数据隔离。

权威跟踪文档：

- [full-roadmap-execution-checklist-2026-07-18.md](full-roadmap-execution-checklist-2026-07-18.md)
- [post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md](post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md)
- [runtime-operating-constraints.md](runtime-operating-constraints.md)
- [decision-log.md](decision-log.md)

## 7. 接手后的第一轮执行顺序

1. 从远程 `main` 克隆并确认 `git status`、分支、提交 hash 和远程地址，禁止使用旧工作树冒充最新代码。
2. 在 Windows 按本手册建立 Python 3.13 和 Node LTS 环境。
3. 运行 backend/frontend focused tests、typecheck、lint、build，再运行完整回归。
4. 启动 loopback Workbench，使用页面 Provider 配置；先用 fake gateway 验证共享业务链，不把它写成真实验收。
5. 完成 Windows `NEXT-010` 的 SDD：WASAPI microphone、WASAPI loopback、设备切换、权限拒绝、进程监督、Credential Manager、安装器和签名。
6. 按 TDD 先为 Windows adapter 写红灯合同，再实现 adapter；不得把 Windows 逻辑复制进 shared core/UI。
7. Windows 业务链稳定后，将同一提交同步回 Mac，重新跑 Mac packaged、TCC、签名和真实 Provider 门禁。

## 8. 可复制给下一位 AI 的接手指令

````text
你正在接手 Meeting Copilot。不要从零重新评估项目，也不要先新增评测/边界文档。先从远程仓库获取最新代码：

```powershell
git clone https://github.com/luguochang/meeting-copilot.git
Set-Location .\meeting-copilot
git checkout main
git log -1 --oneline
git status --short --branch
git remote -v
```

确认 `git status` 为 clean，并以远程 `main` 的最新提交为唯一交付基线。然后阅读：

1. docs/handoff/windows-development-2026-07-20.md
2. docs/full-roadmap-execution-checklist-2026-07-18.md
3. docs/post-phase0-2-product-gap-and-roadmap-discussion-2026-07-18.md
4. docs/runtime-operating-constraints.md
5. docs/decision-log.md

当前产品是 local-first：Web/backend 用于 Windows 开发和共享业务验证；当前不是 Docker 运行，也不能用 Docker 替代桌面音频验收。
先在 Windows 建立 Python 3.13、Node LTS、npm 环境，运行 backend/frontend 全量回归和 build，再启动 loopback Workbench。
Provider 只能通过页面配置或本次进程临时环境变量注入；绝不把真实 API Key 写入 Git、日志、截图、evidence 或文档。
Windows 原生 WASAPI、Credential Manager、Tauri WebView、安装包、签名和升级仍未实现，按 NEXT-010 建立 SDD/TDD，不要声称 Windows 客户端已完成。
共享业务代码/UI 不要复制成两套；Mac 最终打包必须回 macOS 完成 Swift/ScreenCaptureKit、Developer ID、notarization 和 Gatekeeper 验收。
先完成环境复刻和当前代码回归，再进入 `NEXT-010`；不要重复旧的 ASR 泛化评测循环，也不要把 fake audio、fake LLM、旧 artifact 或 Web 页面通过写成 packaged client/Windows/公开发布完成。
当前未完成项以 `full-roadmap-execution-checklist-2026-07-18.md` 的 `NEXT-001` 至 `NEXT-023` 表格为准，任何 `Partial`、`Blocked`、`Not started` 都不能改写为 Go。
每次重要决策、测试结果和未完成项都要更新 docs/current-mainline-index.md 与 docs/decision-log.md。
````

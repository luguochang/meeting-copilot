# 言迹 Talktrace

本地优先的中文技术会议工作台。言迹 Talktrace 将麦克风与系统音频采集、实时转写、证据化建议、会后复盘和个人笔记整合在一个桌面应用中。核心设计不是让单个模型同时承担低延迟和高准确率，而是采用“在线预览 + 离线精修 + 可选 LLM”的分层链路。

[官网](https://talktrace.codexai.club/) · [GitHub](https://github.com/luguochang/meeting-copilot) · [CSDN 博客](https://blog.csdn.net/luguochang) · [AI 赞助商](https://codexai.club/)

![言迹 Talktrace 界面预览](docs/assets/talktrace-tour.gif)

## 技术架构

Talktrace 采用桌面壳、Web 工作台、本地服务、领域核心与模型运行时分层。Tauri 负责原生音频和进程生命周期，React 负责交互，FastAPI 统一编排 ASR、持久化和 AI 任务，SQLite 保存规范化会议事实。

```mermaid
flowchart LR
    subgraph Client["Windows 客户端"]
        Audio["WASAPI 音频采集<br/>麦克风 / 系统音频"]
        Tauri["Tauri 2 + Rust<br/>进程与安全配置"]
        UI["React + TypeScript<br/>会议工作台"]
    end

    subgraph Local["本机服务"]
        API["FastAPI<br/>HTTP / WebSocket / 任务编排"]
        ASR["FunASR 本地运行时<br/>在线识别 + 离线精修"]
        Core["领域核心<br/>转写 / 证据 / 状态 / 建议闸门"]
        Store["SQLite + 受控音频目录"]
    end

    LLM["OpenAI-compatible LLM<br/>用户显式配置后启用"]

    Audio -->|"16 kHz 单声道 PCM"| API
    Tauri --> UI
    Tauri --> API
    UI <-->|"回环地址 HTTP / WebSocket"| API
    API <--> ASR
    API <--> Core
    API <--> Store
    API -. "仅发送所需会议文本" .-> LLM
```

### 语音转文字链路

```mermaid
flowchart LR
    A["麦克风 / 系统音频"] --> B["16 kHz PCM<br/>WebSocket"]
    B --> C["本地在线阶段<br/>Paraformer + ONNX Runtime"]
    C --> D["低延迟 Partial<br/>只用于即时反馈"]
    B --> E["服务端 VAD<br/>静音或最长时限分段"]
    E --> F["本地离线阶段<br/>SeACo Paraformer + FSMN VAD + CT-Transformer 标点"]
    L["导入录音"] --> M["本地音频规范化与分段"]
    M --> F
    F --> G["权威 Final"]
    D --> H["Canonical Transcript<br/>统一事件投影"]
    G --> H
    H --> I["可选远程阶段<br/>LLM 文本校正 + 安全校验"]
    I --> J["Revision 事件"]
    J --> H
    H --> K["证据片段 / 会议状态 / 建议 / 纪要"]
```

这条链路实际包含三个职责不同的层次：

1. **本地在线 ASR**：音频通过 WebSocket 持续送入 FunASR 在线 Paraformer ONNX 模型，快速产生 `partial`，保证会中界面有连续反馈。在线输出只用于实时预览，不作为最终权威文本。
2. **本地离线精修**：服务端 VAD 根据自然静音或最长段时限切出完整 PCM 片段，交给常驻的离线 FunASR worker。离线 SeACo Paraformer 配合 FSMN VAD、CT-Transformer 标点和热词生成 `final`；模型只加载一次并复用，避免每段重复启动。
3. **可选远程 LLM**：只在用户配置 OpenAI-compatible 服务后启用，用于文本校正和会议语义分析。它接收必要的会议文本，不接收原始录音；不可用时，本地转写与会议记录仍然工作。

### 为什么能兼顾实时性和准确率

- **延迟与准确率解耦**：在线模型负责“尽快显示”，离线模型负责“最终落盘”，避免用更重的离线推理阻塞实时反馈。
- **服务端端点检测**：VAD 在自然停顿处收束句子，同时用最大段长限制持续发言，保证精修任务有明确边界。
- **常驻模型进程**：在线 worker 与离线 refiner 都采用进程常驻、会话复用模式，减少模型冷启动和重复加载开销。
- **热词与文本规范化**：本地 ASR 支持会议热词，入库前执行幂等术语规范化，降低技术名词和中英文混排的误差。
- **明确的文本权威级别**：规范转写按 `partial < final < revision` 投影。同一语音段只允许更高等级事件覆盖展示结果，同时保留事件历史用于追踪。
- **安全的 LLM 校正**：校正任务累计到 80 字或等待 15 秒后触发，单批最多 2,000 字；长度比例必须在 `0.65-1.40` 内，文本相似度不得低于 `0.65`，不满足条件的改写会被拒绝。
- **失败可降级**：离线精修不可用时保留在线结果并标记降级；LLM 超时、限流或返回非法结构时不破坏规范转写。任务通过租约、有限重试和审计状态避免重复提交。

### 证据化会议理解

远程 LLM 不直接修改会议事实。FastAPI 先从规范转写中构造带时间范围和段落 ID 的证据，再通过 OpenAI-compatible Chat Completions 或 Responses API 生成结构化结果。传输默认优先使用 SSE 流式响应，不支持流式的服务可显式降级到非流式模式。

生成结果需要通过领域闸门后才能进入工作台：

- 建议必须引用当前有效的原文证据，证据被转写修订替代后会变为 `superseded`，不能继续支撑强建议。
- 会议状态围绕负责人、截止时间、测试验证、指标监控和回滚条件建模，用于识别技术讨论中的未闭环项。
- ASR 语义质量不足或链路处于降级状态时，系统阻止生成高置信度建议，保留转写并明确暴露质量状态。
- 增量 AI 任务保存输入转写版本和证据哈希；提交前再次检查证据版本，避免过期结果覆盖新内容。

## 技术栈

| 层次 | 技术 | 用途 |
| --- | --- | --- |
| 桌面端 | Tauri 2、Rust、Windows WASAPI | 客户端生命周期、麦克风与系统回放音频、双轨采集、系统安全存储 |
| Web 前端 | React 18、TypeScript 5.9、Vite 8、Lucide | 会中工作台、历史、复盘、笔记、设置与离线能力管理 |
| 本地后端 | Python 3.11-3.13、FastAPI、Pydantic、Uvicorn | 本地 API、WebSocket、ASR 编排、任务和静态资源托管 |
| 数据与任务 | SQLite、受控文件目录、持久化 Job/Outbox | 会议事实、音频、事件、租约、重试与异常恢复 |
| 本地 ASR | FunASR 1.3.10、FunASR ONNX、ONNX Runtime、Paraformer | 在线低延迟识别与本地离线精修 |
| 语音后处理 | SeACo Paraformer、FSMN VAD、CT-Transformer Punctuation | 端点检测、完整段重识别、标点与热词增强 |
| 远程 AI | OpenAI-compatible Chat Completions / Responses、HTTPX、SSE | 可选文本校正、实时建议、纪要与复盘生成 |
| 工程质量 | Pytest、Vitest、Testing Library、Ruff、ESLint、Cargo Check | 单元、契约、集成、前端和桌面端验证 |
| 发布 | Tauri/NSIS、`.mcpkg`、CycloneDX SBOM | Windows 安装包、独立模型能力包与供应链清单 |

## 可靠性与数据边界

- 桌面后端只监听随机的 `127.0.0.1` 回环端口，由 Tauri 负责启动、健康检查和退出回收。
- 本地 API 使用启动时生成的令牌完成引导，再通过 `HttpOnly`、`SameSite=Strict` Cookie 访问工作台。
- Provider 密钥由桌面端写入操作系统安全存储，前端只读取脱敏配置状态。
- 规范化会议数据保存在 SQLite；录音与派生文件位于受控数据根目录，导入、导出和删除均执行路径边界校验。
- 后台任务使用持久化状态、租约、截止时间和重试策略，应用异常退出后可恢复未完成任务。
- 诊断输出会过滤密钥、URL 凭据和本机绝对路径；官网与客户端运行数据完全隔离。
- ASR 模型不写入 Git，也不绑定在基础安装包中；`.mcpkg` 能力包通过清单、平台信息和哈希进行校验。

## 主要功能

- **会议工作台**：麦克风与系统音频输入、连续转写、当前议题和会议状态集中展示。
- **AI 建议**：围绕负责人、截止时间、验证、监控和回滚条件生成带原文依据的追问建议。
- **会后复盘**：在同一场会议中查看纪要、方案与风险、待确认项、完整文字和录音。
- **录音导入**：导入已有音频并跟踪转写和分析进度。
- **会议与笔记管理**：搜索、筛选、重命名、删除历史会议，并整理个人笔记。
- **离线能力管理**：导入本地 ASR 能力包并查看模型与运行状态。

| 实时会议 | 会后复盘 |
| --- | --- |
| ![实时会议工作台](docs/assets/talktrace-workbench-live.png) | ![会后复盘](docs/assets/talktrace-workbench-review.png) |

![会议历史](docs/assets/talktrace-workbench-history.png)

## 下载 Windows 版

[GitHub Release v0.1.0](https://github.com/luguochang/meeting-copilot/releases/tag/v0.1.0) 提供 Windows x64 基础客户端：

| 文件 | 用途 | SHA-256 |
| --- | --- | --- |
| [Windows x64 安装程序](https://github.com/luguochang/meeting-copilot/releases/download/v0.1.0/Talktrace-0.1.0-windows-x64-base-unsigned.exe) | 当前用户安装与卸载 | `7d077e35f1e67da059a1291865d858f95eaa25c7257387b298b80187cc405463` |

基础客户端包含桌面应用和本地服务，不包含 ASR 模型。实时转写和录音文件转写需要与平台匹配的 `.mcpkg` 能力包。当前安装程序尚未进行代码签名，详细步骤和校验方式见 [安装指南](docs/installation.md)。

## 从源码运行

环境要求：Windows 10/11、Python 3.11-3.13、Node.js 22，以及 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/luguochang/meeting-copilot.git
cd meeting-copilot\code\web_mvp\frontend_v2
npm ci
npm run build

cd ..\backend
uv sync --frozen --group dev
uv run python ..\..\..\tools\workbench_server.py start
```

打开 `http://127.0.0.1:8765/workbench`。停止服务：

```powershell
uv run python ..\..\..\tools\workbench_server.py stop
```

模型能力包、桌面构建和配置方式见 [安装指南](docs/installation.md) 与 [开发指南](docs/development.md)。

## 代码结构

```text
meeting-copilot/
├─ code/
│  ├─ core/                 # 会议状态、证据契约与建议闸门
│  ├─ web_mvp/
│  │  ├─ backend/           # FastAPI、SQLite、ASR 与 AI 任务编排
│  │  └─ frontend_v2/       # React + TypeScript 工作台
│  ├─ desktop_tauri/        # Tauri 桌面壳、WASAPI 音频与进程管理
│  └─ asr_runtime/          # FunASR worker、模型清单与转写工具
├─ configs/                 # 配置模板与术语表
├─ data/                    # 脱敏演示数据和评测词表
├─ docs/                    # 用户与技术文档
├─ tests/                   # 跨模块契约和工具测试
├─ tools/                   # 启动、诊断、打包和发布工具
└─ website/                 # Vite + React 产品官网
```

## 验证

```powershell
# 前端
cd code\web_mvp\frontend_v2
npm run lint
npm run typecheck
npm test
npm run build

# 后端
cd ..\backend
uv run --frozen ruff check meeting_copilot_web_mvp
uv run --frozen pytest -q

# 桌面端
cargo check --locked --manifest-path ..\..\desktop_tauri\src-tauri\Cargo.toml

# 官网
cd ..\..\..\website
npm run check
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [安装指南](docs/installation.md) | 安装、能力包、源码运行和 AI 服务配置 |
| [使用指南](docs/user-guide.md) | 会议、导入、复盘、笔记和离线能力 |
| [架构说明](docs/architecture.md) | 组件职责、数据流和安全边界 |
| [实时智能质量提升方案](docs/realtime-intelligence-quality-and-agent-decision-plan.md) | 分片诊断、语义窗口、任务拆分、质量评测与 Pi Go / No-Go |
| [实时对话 Agent 产品与技术方案](docs/pi-agent-product-and-technical-design.md) | 双音轨实时教练、Pi 边界、腾讯会议采集、介入策略与实施路线 |
| [开发指南](docs/development.md) | 开发环境、命令、测试和贡献约定 |
| [隐私说明](docs/privacy.md) | 本地数据、远程调用和删除边界 |
| [故障排查](docs/troubleshooting.md) | 启动、音频、转写与 AI 配置问题 |

## 许可

第一方代码使用仓库中的 [Talktrace Source License](LICENSE)。第三方依赖、模型、字体和二进制文件受各自许可约束，详见 [NOTICE](NOTICE) 与 [SBOM](sbom.cdx.json)。

Copyright (c) 2026 [luguochang](https://github.com/luguochang).

# 言迹 Talktrace · 本地会议助手

把交流中的声音保存为可回看的文字，在讨论中辅助提出追问、比较方案和组织回应，再把核对后的文稿用于下一次会议。主题、角色和资料可选，不要求先输入大段背景。

当前交付为 **Windows 本机单用户网页版**：后端同时提供 API 和构建好的页面，用浏览器访问，暂不制作客户端安装包。已有功能入口与持久化流程；真实长会议的识别质量、建议价值和延迟仍需人工验收。

![会议工作台（隔离演示资料）](docs/assets/web-delivery-20261011/overview.jpg)

> 本轮截图说明操作入口，不代表真实录音或远端模型质量验收通过。

## 文档入口

| 文档 | 内容 |
| --- | --- |
| [Windows 网页版部署](docs/web-deployment.md) | 安装、模型、配置、浏览器启动、启停和故障排查 |
| [带截图的用户操作手册](docs/realtime-participation-user-manual-20261008.md) | 会前准备、会中参与、会后核对、资料库和导出 |
| [机器配置与精修说明](docs/local-refinement-resource-guide-20261011.md) | 内存实测、校对模式、资源保护和回退 |
| [架构与 Pi 流程](docs/web-architecture.md) | 组件、上下文、工具、触发和保存边界 |
| [功能与交付状态](docs/web-delivery-status-20261011.md) | 已有功能、限制和待验收清单 |
| [本轮交付记录](docs/web-delivery-changelog-20261011.md) | 修改、验证和发布范围 |

## 已有功能

| 阶段 | 能力 |
| --- | --- |
| 会前 | AI 配置、资源和识别检查；选择本地精修＋大模型校对或仅大模型校对；可选主题、角色、项目和资料 |
| 会中 | 本地实时转写、独立草稿与保存正文、后台校对；Pi 自动建议及「我可以问什么 / 还有什么方案 / 帮我组织回应」；范围问答、依据和历史 |
| 会后 | 纪要、决策待办、分析和索引；失败重试、人工终稿、版本、导出和录音回看 |
| 复用 | 笔记、项目与文件夹资料库；DOCX/文本 PDF/TXT/MD 转换后确认；确认资料、项目事项、Wiki、术语偏好用于后续会议 |

![会议文字与 Pi 教练（合成历史建议）](docs/assets/web-delivery-20261011/transcript-coach.jpg)

## Windows 快速启动

先安装 [Git for Windows](https://git-scm.com/downloads/win)、[Node.js](https://nodejs.org/en/download)（**22.19.0+**）和 [uv](https://docs.astral.sh/uv/getting-started/installation/)，重新打开 PowerShell。在新目录克隆，不复制其他电脑的环境、会议数据库或密钥。

```powershell
git clone --branch feat/pi-realtime-coach-agent-loop --single-branch https://github.com/luguochang/meeting-copilot.git
cd meeting-copilot
uv python install 3.13 3.11
uv sync --project code/web_mvp/backend --python 3.13 --frozen
uv venv --python 3.11 code/asr_runtime/.venv-funasr
uv pip install --python code/asr_runtime/.venv-funasr/Scripts/python.exe -r code/asr_runtime/requirements-funasr.lock
npm --prefix code/agent_runtime/pi_coach_bridge ci
npm --prefix code/web_mvp/frontend_v2 ci
npm --prefix code/web_mvp/frontend_v2 run build
uv run --project code/web_mvp/backend python tools/web_quality.py models
uv run --project code/web_mvp/backend python tools/web_quality.py doctor
uv run --project code/web_mvp/backend python tools/web_quality.py start
Start-Process 'http://127.0.0.1:8765/workbench'
```

每条命令成功后再继续。`doctor` 检查依赖、模型哈希和测试 Provider 的 Pi smoke，不采音、不调用远端模型、不加载 ASR 做推理；通过不等于整场质量通过。服务启动后在后台运行，首次需等待在线识别就绪。

浏览器入口：[http://127.0.0.1:8765/workbench](http://127.0.0.1:8765/workbench)。前端由后端托管，正常使用无需 Vite、Rust/Tauri 或两个常驻终端。

查看状态与停止：

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py status
uv run --project code/web_mvp/backend python tools/web_quality.py stop
```

停止前在页面结束录音并确认保存。默认数据目录 `data/local_runtime/web-quality/`，模型目录 `data/local_runtime/web-models/`；自定义目录或端口后，所有启停命令沿用同一参数，见[部署说明](docs/web-deployment.md)。

### AI 配置

在页头 **AI 设置**填写自己的 Base URL、Key、模型和协议并保存；也可复制根目录 `.env.example` 为 `.env` 后填写密钥，已有文件不要覆盖。

公开示例地址 `https://codexai.club`，通用/实时/校对模型 `gpt-6-sol`，协议 `responses`。**仓库不提供可用密钥**；更换服务以其实际支持的模型和协议为准。页面保存的配置在同一数据目录内优先恢复，用户修改后持续使用新的选择；修改 `.env` 不一定覆盖已保存配置。

远程 AI 会接收分析所需文字和已授权资料片段；录音默认留在本机。设置已保存不代表连接、校对、教练和纪要均已验收。

### 机器与声音要求

| 项目 | 当前参考与边界 |
| --- | --- |
| 系统 / CPU | Windows 11 x64，多核 CPU；参考 i5-12400F（6 核 / 12 线程） |
| 内存 | 完整 CPU 双模型流程以 **32 GB** 为验证起点，尚无认证最低配置；其他应用占用多时仍可能加载失败 |
| 离线冷加载余量 | 提交余量至少 **8 GiB**、物理可用至少 **6 GiB**，另计输入缓冲；这是启动预算，不是机器总内存要求 |
| 磁盘 | SSD，安装与缓存建议预留 15–20 GB，录音另计；推理模型约 3.1 GB |
| GPU | 当前精修走 CPU，不要求独显；不会自动启用 GPU |
| 声音 | 浏览器麦克风和明确授权；网页版系统音频直采、复杂噪声和可靠多人归属尚未交付 |

「仅大模型校对」省去离线精修，仍需本地在线 ASR。资源不足保留识别原文，等待或回退不能称作已校对，详见[配置指南](docs/local-refinement-resource-guide-20261011.md)。

## 开发与验证

```text
code/web_mvp/frontend_v2/            React + TypeScript 工作台
code/web_mvp/backend/                FastAPI、SQLite、后台任务
code/agent_runtime/pi_coach_bridge/  Pi Agent 与 Provider 适配
code/asr_runtime/                    语音 worker、模型清单与依赖锁
code/core/                           共享协议与基础能力
tools/web_quality.py                 网页版模型、检查与启停
docs/                                部署、操作、架构与交付记录
```

```powershell
npm --prefix code/agent_runtime/pi_coach_bridge test
npm --prefix code/web_mvp/frontend_v2 test
npm --prefix code/web_mvp/frontend_v2 run lint
npm --prefix code/web_mvp/frontend_v2 run build
uv run --project code/web_mvp/backend pytest code/web_mvp/backend/tests
uv run --project code/web_mvp/backend pytest tests/test_web_quality.py tests/test_workbench_server_tool.py tests/test_workbench_server_runtime_identity.py
```

独立前端开发可运行 `npm --prefix code/web_mvp/frontend_v2 run dev`，示例代理目标为后端 `127.0.0.1:8765`。改源码后重新构建页面并按受管流程加载后端修改。

密钥、Provider 运行设置、录音、数据库、模型权重和私人测试截图不进入 Git。旧交接文档用于历史查阅；当前安装和验收以上方文档为准。

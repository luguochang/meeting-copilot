# 言迹 Talktrace · 让你更有准备地参与讨论

**本地实时转写 + Pi 参会教练 + 可复用的会议资料。**

开会时听到了问题，却一时想不到该问什么？言迹把正在讨论的内容与本次授权资料放在一起，帮助你找到值得追问的条件、可比较的方案，以及适合当场说出口的回应。会议结束后，再把核对过的文稿、决策和资料留给下一次讨论。

> **这是 `feat/pi-realtime-coach-agent-loop` 分支的说明。** Pi 教练、资料复用和新版网页流程在本分支继续迭代，尚未合并到 [main](https://github.com/luguochang/meeting-copilot/tree/main)。当前使用 **Windows 本机单用户网页版**；浏览器即可操作，暂不提供这一版的客户端安装包。

[能帮你做什么](#features) · [一场会议怎么用](#workflow) · [Pi 如何工作](#pi-agent) · [Windows 快速启动](#windows-快速启动) · [配置与机器要求](#configuration) · [常见问题](#faq)

![言迹会议工作台：会议列表与主要入口](docs/assets/web-delivery-20261011/overview.jpg)

*本页截图使用隔离的公开演示资料，展示真实界面与入口，不代表真实收音或建议质量验收通过。*

<a id="features"></a>
## 能帮你做什么

### 在会上，不只记下内容，还能参与讨论

左侧保留会议正文，右侧给你三个直接可用的参与入口：

| 你此刻的需要 | 使用入口 | 帮助方向 |
| --- | --- | --- |
| 不知道接下来该问什么 | **我可以问什么** | 寻找待确认条件、遗漏的约束和验收边界 |
| 大家很快同意了一个方案 | **还有什么方案** | 比较替代路径、成本与取舍，补充专业视角 |
| 有想法，但不知道怎么表达 | **帮我组织回应** | 把当前讨论与想法整理成适合当场说的话 |
| 有明确的问题或想核对资料 | **自己提问 / 询问 AI** | 针对所选文字、近期讨论、整场会议或本场资料提问 |

例如，在需求评审中讨论「通知失败就自动重试」时，有价值的追问可能是：

> 如果权益已经发放、通知却失败，重试会不会再次发放权益？我们要不要把这两件事的幂等和补偿边界分开？

这是用途示意，不是承诺模型每次都生成同一句话。产品关注的是让建议影响当前取舍、风险或验收标准；建议仍需要使用者结合现场判断。

![会议文字与实时教练：三个主动入口、历史建议及其依据](docs/assets/web-delivery-20261011/transcript-coach.jpg)

*图中为已过期的合成历史建议。当前建议有时效，历史可展开回看；模型也可以选择保持安静。*

### 文字先保存，校对在后台继续

- **本地实时转写**：通过浏览器麦克风接收声音，实时显示草稿，闭合后的识别正文追加保存。草稿变化不会替代已保存的历史正文。
- **两种校对方式**：会前可选「本地精修＋大模型校对」或「仅大模型校对」。后者省去离线精修进程，仍保留本地在线语音识别。
- **状态如实显示**：区分识别原文、处理中、校对未改字、已校对和失败保留原文。大模型校对独立处理有效正式段，不必一直等待本地精修成功。
- **重要内容可回看**：保留来源与版本，查看正文、历史建议、录音或导出结果；关键数字、人名和结论仍应人工核对。

### 让资料进入讨论，而不是只作为附件存着

需求文档、接口说明、方案和参考资料，可以作为本次会议的可选背景：

```text
上传 DOCX / 文本 PDF / TXT / MD
          ↓
后台转换为可阅读、可修改的文本
          ↓
核对正文与版本，明确允许用于本场背景
          ↓
教练和问答按本场范围使用相关片段
```

不要求每次手写大段背景，也不会把上传成功等同于内容已核对。扫描 PDF、图片和复杂表格暂不保证 OCR 与版式还原。

### 把本次讨论变成下次可用的准备

会后可以查看纪要、决策与待办、分析建议，编辑并保存**人工终稿**，导出 Markdown、Word 或 JSON。后台整理失败可以重试，原文继续保留。

长期资料按**项目 → 文件夹 → 文档版本**管理。核对后的终稿入库先生成待确认稿；确认有效版后，下次会议选择相同项目和资料范围即可复用。项目事项与 Wiki、术语与偏好有各自的确认流程，不会因为保存一份文稿就自动改写项目状态。

![资料库：项目、文件夹、确认版本与可修订正文](docs/assets/web-delivery-20261011/library.jpg)

左侧的**笔记**用于保存自己的记录或有用回答，**资料库**用于组织经确认、可供后续会议选择的背景内容；它们与会议原文各有用途。

<a id="workflow"></a>
## 一场会议怎么用

| 阶段 | 你的操作 | 在页面上确认什么 |
| --- | --- | --- |
| **会前** | 设置 AI；可选主题、角色和资料；选择校对模式 | 在线识别、可用内存、离线精修准备状态、AI 是否配置。主题和资料都可跳过 |
| **开始** | 选择麦克风、检查声音，告知参会者并确认录音 | 明确点击开始后才进入正式采集；打开准备页不会自动录音 |
| **会中** | 阅读左侧已保存正文，查看自动建议或点击三个参与入口 | 看当前话题、建议依据与时效；有疑问时选定范围提问 |
| **会后** | 结束录音，查看整理结果；必要时重试、修正文稿 | 原文保留、人工终稿已保存；等待 AI 不等于已完成校对 |
| **下次** | 将有用终稿确认入库，选择项目和相关资料范围 | 已确认版本与来源正确；项目事项与 Wiki 按需单独确认 |

![会后复盘：人工编辑保存的公开示例稿](docs/assets/web-delivery-20261011/meeting-review.jpg)

*图中终稿由人工编写并通过页面保存，AI 分析仍待处理；它演示编辑与复用入口，不是模型生成效果。*

<a id="pi-agent"></a>
## Pi 在这里做了什么

Pi 不是另一个语音模型，而是右侧教练的**智能体运行层**。它仍需要调用你配置的大模型；它的作用是把会话、上下文、受限工具与结构化结果连接成可控制的流程。

```text
本地语音识别 → 保存正文 → 可选精修 / 独立大模型校对
                              ↓
                最近可靠原话 + 当前讨论 + 授权资料
                              ↓
                自动机会判断 / 使用者主动请求
                              ↓
            Pi 会话 → 按需读取有范围与版本的证据
                              ↓
         提交建议或保持安静 → 核对依据与时效 → 页面展示
```

与把整段转写直接塞给模型相比，当前实现增加了：

- **任务意图**：追问、方案比较、组织回应、问答和会后任务各有明确入口。
- **有范围的上下文**：近期可靠原话、讨论工作记忆和授权资料共同参与；旧记忆不能直接当作当前需求。
- **受限工具与依据**：按任务读取内容，有次数和时间预算；建议附带可核对的来源，不能无限调用工具。
- **结果发布约束**：核对来源版本、当前讨论和有效期，避免把过期建议一直当作当前建议。

自动教练受新正文、当前机会、去重、运行任务、时间预算与增量价值共同约束，**不是每几秒必须弹一条**。模型可以保持安静，用户也可主动请求。

Pi SDK 本身不保证建议质量；当前没有自动联网搜索、无限上下文或模型权重自训练。长会议和多资料场景仍可能遗漏信息，需要继续做真实质量与时延验收。

## Windows 快速启动

### 1. 准备环境

安装 [Git for Windows](https://git-scm.com/downloads/win)、[Node.js](https://nodejs.org/en/download) **22.19.0 或更高**，以及 [uv](https://docs.astral.sh/uv/getting-started/installation/)，然后重新打开 PowerShell。当前验证环境为 Windows 11 x64。

所有后续命令从仓库根目录执行。推荐新建目录干净克隆，不复制其他电脑的虚拟环境、密钥、数据库或录音。

### 2. 拉取本分支，安装依赖并构建页面

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
```

每条命令成功后再继续，保留仓库锁文件。两套 Python 环境分别用于后端和语音依赖，无需手动激活。

已有 main 仓库时，先处理未保存修改，再切换分支：

```powershell
git remote set-branches --add origin feat/pi-realtime-coach-agent-loop
git fetch origin
git switch feat/pi-realtime-coach-agent-loop
```

第一条命令把 Pi 分支加入 origin 的拉取范围，也适用于之前仅克隆 main 的 `--single-branch` 仓库。切换后重新按本分支安装和构建，不沿用 main 的运行进程或旧页面。

### 3. 下载模型并检查安装

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py models
uv run --project code/web_mvp/backend python tools/web_quality.py doctor
```

模型默认放在 `data/local_runtime/web-models/`，下载后核对文件哈希。标准安装目前检查在线、离线、VAD、标点和 CAM++ 五类配套模型；选择仅大模型校对也不会让标准安装跳过模型检查。

`doctor` 检查依赖、模型哈希、前端构建和测试 Provider 的 Pi smoke，不采音、不加载语音模型做推理、不请求真实远端 AI。`ready=true` 表示安装检查通过，不等于模型已加载或会议效果已验收。

### 4. 启动并打开浏览器

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py start
Start-Process 'http://127.0.0.1:8765/workbench'
```

浏览器地址：**[http://127.0.0.1:8765/workbench](http://127.0.0.1:8765/workbench)**。

服务在后台运行，后端同时提供 API 和前端页面，不需要单独运行 Vite、安装 Rust/Tauri 或保留两个终端。首次启动需等待本地在线识别准备完成，再到页面的 **AI 设置**填写自己的模型配置。

### 5. 查看状态与停止

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py status
uv run --project code/web_mvp/backend python tools/web_quality.py stop
```

停止前在页面结束录音并确认保存。关闭浏览器不会自动停止后端；`status` 的服务运行状态也不等于在线 ASR 和离线精修都已就绪。

默认会议数据、录音和设置保存在 `data/local_runtime/web-quality/`。自定义目录/端口时，三个命令都使用同一组参数，例如：

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py start --data-dir 'D:/Talktrace/meetings' --port 8770
uv run --project code/web_mvp/backend python tools/web_quality.py status --data-dir 'D:/Talktrace/meetings' --port 8770
uv run --project code/web_mvp/backend python tools/web_quality.py stop --data-dir 'D:/Talktrace/meetings' --port 8770
```

此时浏览器访问 `http://127.0.0.1:8770/workbench`。不同实例使用不同数据目录和端口，不同时写同一会议库，也不混用底层启动工具的默认参数。

<a id="configuration"></a>
## AI 配置、机器要求与声音来源

### 配置自己的模型

在页头 **AI 设置**填写并保存：

| 字段 | 用途 |
| --- | --- |
| Base URL / API Key | 自己的 OpenAI-compatible 服务与密钥 |
| 通用模型 | 问答、会后整理等用途 |
| 实时模型 / 转写校正模型 | 可分别指定；留空时继承通用模型 |
| 高级设置 → 接口协议 | 按服务实际支持选择 Responses 或 Chat Completions |

保存后可点击**测试连接**，它会访问配置的模型，可能产生费用；通过最小连接测试不等于所有功能都已验收。费用和等待时间由服务商、模型、上下文与调用量决定，本项目不附带免费额度。

也可以在本机使用 `.env`：

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

填写自己的 Key，不覆盖已有文件。公开示例使用 `https://codexai.club`、`gpt-6-sol` 与 `responses`；这些是可修改的配置示例，不是内置免费服务。仓库不包含可用密钥。

**同一数据目录中，页面保存的配置优先恢复。** 用户在页面修改后持续使用新的选择；修改 `.env` 不一定覆盖已经保存的设置。建议通过页面核对当前通用/实时/校对模型。

### 机器需要什么配置

| 项目 | 当前参考与限制 |
| --- | --- |
| 系统 / CPU | Windows 11 x64，多核 CPU；参考 i5-12400F（6 核 / 12 线程），更低规格尚无整场认证 |
| 完整双模型内存 | 以 **32 GB RAM** 为部署验证起点，**不是认证最低配置**；其他程序占用多时仍可能失败 |
| 离线冷加载余量 | 可提交余量至少 **8 GiB**、物理可用至少 **6 GiB**，另计输入缓冲；不是“8 GB 内存机器就能跑” |
| 存储 | SSD；安装与缓存建议预留 **15–20 GB**，录音另计；推理模型约 3.1 GB |
| GPU | 当前精修走 CPU，不要求独显，有显卡也不会自动启用 GPU |
| 网络 | 首次依赖/模型下载和远程 AI 需要联网；本地识别不把声音送给远端文字模型 |

本机 32 GB 环境也发生过离线精修资源拒绝，因此会前要看**实际余量和 worker 就绪状态**。选择「仅大模型校对」可省离线精修进程，但在线 ASR、模型文件和环境仍需要；未配置 AI 时不能把回退称作已校对。

### 本地与远程的边界

- **本机保存**：录音、会议文字、文稿版本、笔记、资料和设置；模型权重、数据库和密钥不进入 Git。
- **启用远程 AI 后发送**：分析所需的会议文字和已授权资料片段。原始录音默认留在本机，不等于所有处理都完全离线。
- **声音来源**：本分支网页版通过浏览器麦克风采音。麦克风可收到外放，但效果受回声、噪声和设备影响；不能据此宣称支持 Windows 系统音频直采或桌面双轨。
- **运行范围**：服务默认仅绑定 `127.0.0.1`，面向本机单用户，尚未交付公网多人托管。

<a id="faq"></a>
## 常见问题与当前边界

| 问题 | 处理方式或说明 |
| --- | --- |
| 页面能打开，但没有文字 | 核对已正式开始采集、麦克风权限/设备和在线识别 ready；能打开网页不代表收到声音 |
| 一直等待本地精修或提示内存不足 | 查看会前资源和错误，释放资源后明确重新准备，或下场选择仅大模型校对；不要把门槛调低来绕过保护 |
| AI 设置好了，右侧仍没有建议 | 核对实际连接、功能开关与请求状态；自动教练可以保持安静，也可点击三个参与入口。缺配置、失败或超时需分别处理 |
| 校对未改字是不是完全正确 | 不是；它与失败保留原文不同，但不能保证语义和事实准确，重要内容仍需核对 |
| 上传文件后就能一直记住吗 | 先检查转换与版本、确认授权范围；跨会复用需确认入库并选择项目/资料，不是无限记忆或自动训练 |
| 能处理扫描 PDF、多人同时讲话吗 | 简单文本资料为主；复杂 OCR、可靠多人归属、重叠声音和背景音乐不属于当前保证 |
| 已经是最终验收版吗 | 当前是持续打磨的源码网页版；长会议保存完整性、真实转写/校对质量、建议增量价值和延迟仍需验收 |

## 开发与进一步阅读

```text
code/web_mvp/frontend_v2/            React + TypeScript 工作台
code/web_mvp/backend/                FastAPI、SQLite、后台任务
code/agent_runtime/pi_coach_bridge/  Pi Agent 与模型适配
code/asr_runtime/                    语音 worker、模型清单与依赖锁
code/core/                           共享协议与基础能力
tools/web_quality.py                 网页版模型、安装检查与启停
docs/                                操作、部署、架构和阶段记录
```

常用验证命令：

```powershell
npm --prefix code/agent_runtime/pi_coach_bridge test
npm --prefix code/web_mvp/frontend_v2 test
npm --prefix code/web_mvp/frontend_v2 run lint
npm --prefix code/web_mvp/frontend_v2 run build
uv run --project code/web_mvp/backend pytest code/web_mvp/backend/tests
uv run --project code/web_mvp/backend pytest tests/test_web_quality.py tests/test_workbench_server_tool.py tests/test_workbench_server_runtime_identity.py
```

独立前端开发用 `npm --prefix code/web_mvp/frontend_v2 run dev`，示例代理指向后端 `127.0.0.1:8765`。改源码后重新构建页面，并通过同一受管入口加载后端修改。

本页已经包含常规体验所需的功能、流程、安装和配置；下面的文档用于查阅更详细的操作与技术细节：

| 进一步阅读 | 内容 |
| --- | --- |
| [截图操作手册](docs/realtime-participation-user-manual-20261008.md) | 具体按钮、文稿编辑、资料授权、项目复用与导出 |
| [Windows 部署说明](docs/web-deployment.md) | 模型来源、完整部署参数、代理与排错 |
| [精修资源指南](docs/local-refinement-resource-guide-20261011.md) | 内存实测、保护门槛、失败与回退 |
| [架构与 Pi 流程](docs/web-architecture.md) | 模块、上下文、工具与发布合同 |
| [交付状态与验收](docs/web-delivery-status-20261011.md) | 当前功能、已知限制和开放验收项 |
| [阶段交付记录](docs/web-delivery-changelog-20261011.md) | 已执行的修改与验证范围 |

第一方代码使用 [Talktrace Source License](LICENSE)，第三方依赖与模型受各自许可约束，见 [NOTICE](NOTICE)。

# Windows 网页版安装、启动与使用

更新：2026-10-11。适用分支：`feat/pi-realtime-coach-agent-loop`。本次交付是源码运行的本地网页版：浏览器打开工作台，后端同时提供 API 和构建好的页面，不需要客户端安装包、Rust/Tauri 或单独常驻的 Vite 前端服务。

服务默认只绑定本机 `127.0.0.1`，当前面向单机用户。不是部署一个公网地址供多人共用；公网、多用户和远程设备采音尚未交付。

按顺序完成下列 Windows PowerShell 流程。所有命令从**仓库根目录**执行，也就是能看到 `tools/`、`code/` 和 `.env.example` 的目录。新电脑请独立克隆，不复制开发者的 `artifacts/`、虚拟环境、`.env`、会议数据库或录音。

详细操作和实际截图见[用户操作手册](realtime-participation-user-manual-20261008.md)，实测资源见[机器配置指南](local-refinement-resource-guide-20261011.md)，最新会议校对模式见[实施记录](correction-mode-implementation-20261011.md)。

## 1. 准备运行环境与机器资源

安装 [Git for Windows](https://git-scm.com/downloads/win)、[Node.js](https://nodejs.org/en/download) 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)，安装后重新打开 PowerShell。Pi bridge 要求 Node **22.19.0 或更高**；本机本轮为 Node 24.18.0。Python 由 uv 安装，后端与语音依赖分别使用独立环境。

| 项目 | 当前要求或建议 | 验证边界 |
| --- | --- | --- |
| 系统 | 本轮使用 Windows 11 x64 | Windows 10、其他架构及全新 Windows 安装仍需独立验收 |
| CPU | 64 位、多核；当前参考为 i5-12400F，6 核 / 12 线程 | 尚无较低规格的整场性能认证 |
| 内存 | 完整 CPU 双模型流程以 32 GB 为部署验证起点 | 不是最低配置保证；本机 32 GB 也曾因其他应用占用启动精修失败 |
| 冷加载余量 | 提交余量至少 8 GiB、物理可用至少 6 GiB，另计输入缓冲 | 暂定保守准入预算，不是“8 GB RAM 机器可运行” |
| 磁盘 | SSD 优先；安装与缓存建议预留 15–20 GB，录音另计 | 模型推理文件约 3.1 GB，环境、缓存和录音另占空间 |
| GPU | 当前本地精修走 CPU，不要求独立显卡 | 有显卡不会自动切到 GPU；GPU 推理尚未验收 |
| 网络 | 首次安装、模型下载与 L2/Pi 远端文字推理需要联网 | 声音识别在本机；云端模型速度与错误另行评估 |

“仅大模型校对”可以省去本场离线精修进程，但仍需本地在线 ASR、Python 环境和模型，不是免模型或云端语音识别版本。标准安装入口目前仍检查五类模型全部齐全。

## 2. 干净克隆与安装依赖

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

每一步成功后再继续。后端 `uv.lock`、语音 `requirements-funasr.lock` 和两份 `package-lock.json` 都来自仓库；安装失败时按具体错误修复，不删除锁文件或跳过语音依赖。本机现有依赖为后端 Python 3.13.14、ASR Python 3.11.15、FunASR 1.3.10、Torch 2.12.1；其他 wheel/平台组合未因此被认证。

这些命令无需激活虚拟环境。`code/web_mvp/frontend_v2/dist/` 之后由后端托管。正常使用无需运行 `npm run dev`，也无需复制前端 `.env.example`；后者用于独立 Vite 开发实例，API 目标和事件传输可能与标准部署不同。

## 3. 下载模型与安装检查

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py models
uv run --project code/web_mvp/backend python tools/web_quality.py doctor
```

`models` 下载缺失模型，或复制已经通过校验的公开缓存。每个推理文件必须匹配仓库记录的 SHA-256，哈希不符不能绕过。默认模型目录为 `data/local_runtime/web-models/`，模型来自以下上游，版本身份以文件哈希为准：

| 目录 | 上游来源 | 本项目用途 |
| --- | --- | --- |
| `online` | [流式 Paraformer](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online) | 本地实时语音转文字 |
| `offline` | [SeACo-Paraformer](https://www.modelscope.cn/models/iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch) | 本地段落精修与文件识别 |
| `vad` | [FSMN VAD](https://www.modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch) | 音频分段，定位 `v2.0.4` |
| `punc` | [CT-Transformer 标点](https://www.modelscope.cn/models/iic/punc_ct-transformer_cn-en-common-vocab471067-large) | 本地恢复标点 |
| `camplus` | [CAM++](https://www.modelscope.cn/models/iic/speech_campplus_sv_zh-cn_16k-common) | 现有语音配套资源，定位 `v2.0.2`；多人归属不是本轮验收主线 |

`doctor` 检查前端构建、五类模型哈希、后端/FFmpeg、FunASR/Torch 依赖、Node 版本和 Pi bridge smoke。它不请求远端 AI、不采集麦克风、不播放音频，也不加载语音模型做推理。`ready=true` 只表示安装检查通过，不表示精修进程已经就绪、识别准确或整场时延达标。Pi smoke 使用测试 Provider；真实模型的协议/工具能力在页面配置后单独检查。

## 4. 配置 AI：页面保存的选择会持续使用

新用户可以先启动页面，再到“AI 设置”填写自己的地址、Key、模型和协议。本轮已使用的示例为服务地址 `https://codexai.club`、模型 `gpt-6-sol`、协议 `Responses`。仓库不提供可用 Key。页面“连接测试”会访问用户配置的模型；`doctor` 不做这一步。

也可以在仓库根目录使用本地 `.env`。复制示例时保留已有文件：

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

复制后用编辑器填写自己的 Key；仓库示例的 Key 留空，无法直接请求模型：

```dotenv
LLM_GATEWAY_BASE_URL=https://codexai.club
LLM_GATEWAY_API_KEY=
LLM_GATEWAY_MODEL=gpt-6-sol
LLM_GATEWAY_REALTIME_MODEL=gpt-6-sol
LLM_GATEWAY_CORRECTION_MODEL=gpt-6-sol
LLM_GATEWAY_API_STYLE=responses
LLM_GATEWAY_TIMEOUT_SECONDS=60
LLM_GATEWAY_PROXY_URL=
```

实时/校对模型留空时按当前配置的继承规则使用通用模型，需要分别选择时在 AI 设置中保存。其他中转站按实际支持选择 `responses` 或 `chat_completions`；能打开工作台不表示模型已连接。

当前数据目录内已有的页面保存配置会被优先恢复，用户在页面修改模型后会持续使用该选择。修改 `.env` 不一定覆盖已经保存的配置；更改现有实例请使用 AI 设置并核对当前模型。`.env` 与保存的 Provider 配置只在本机，不提交 Git 或复制给其他用户。

`tools/web_quality.py` 用命令参数固定模型/数据目录，`.env` 的 `MEETING_COPILOT_DATA_DIR` 不覆盖其 `--data-dir`，避免启停时指向不同会议库。

## 5. 启动、浏览器入口、状态与停止

默认使用端口 **8765**、数据目录 **data/local_runtime/web-quality**。以下三个命令在同一克隆的仓库根目录执行：

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py start
uv run --project code/web_mvp/backend python tools/web_quality.py status
uv run --project code/web_mvp/backend python tools/web_quality.py stop
```

正常启动返回 `status=started`，已有同一受管实例则返回 `already_running`。命令结束后服务继续在后台运行，无需同时打开两个终端。浏览器打开 [http://127.0.0.1:8765/workbench](http://127.0.0.1:8765/workbench)。停止前先在页面结束正在录制的会议、确认音频已经保存，再运行 `stop`。

启动成功后，也可以在 PowerShell 打开默认浏览器：

```powershell
Start-Process 'http://127.0.0.1:8765/workbench'
```

`status` 的 `running` 和 `runtime_identity_verified=true` 表示后端/页面来源匹配，仍需核对在线 ASR 与离线精修的独立状态。服务启动准备在线 ASR/Pi 本地 bridge；离线精修延后到本场选择本地模式并准备。启动成功不表示已完成精修或远端文字校对。

### 自定义实例：一组参数贯穿启停

下面是新独立实例的示例，使用 8770 和独立目录；目录可按自己的磁盘修改：

```powershell
uv run --project code/web_mvp/backend python tools/web_quality.py models --model-root "D:/Talktrace/models"
uv run --project code/web_mvp/backend python tools/web_quality.py doctor --model-root "D:/Talktrace/models" --data-dir "D:/Talktrace/meetings" --port 8770
uv run --project code/web_mvp/backend python tools/web_quality.py start --model-root "D:/Talktrace/models" --data-dir "D:/Talktrace/meetings" --port 8770
uv run --project code/web_mvp/backend python tools/web_quality.py status --model-root "D:/Talktrace/models" --data-dir "D:/Talktrace/meetings" --port 8770
uv run --project code/web_mvp/backend python tools/web_quality.py stop --model-root "D:/Talktrace/models" --data-dir "D:/Talktrace/meetings" --port 8770
```

对应入口为 [http://127.0.0.1:8770/workbench](http://127.0.0.1:8770/workbench)。模型目录可以共享，不同实例必须使用不同数据目录和端口，不能同时写同一数据库。下次启动、状态和停止都使用相同参数；`server.pid`、身份记录和 `server.log` 位于指定数据目录。

管理已有实例时沿用其原启动登记参数。默认 `web_quality stop` 只针对默认数据目录中的受管记录，不管理其他数据目录中的实例。不要混用不同实例的参数，也不要猜进程编号。

标准部署统一使用 `tools/web_quality.py`。底层 `tools/workbench_server.py` 的默认数据/PID目录不同，默认 `provider-mode=safe`；不要混用两套工具的默认启停，也不要用裸 `uvicorn` 替代标准入口而遗漏模型/Pi 配置。

## 6. 会前、会中、会后怎么用

1. 打开工作台，到 AI 设置核对地址、模型、协议和连接状态。校对/规范化/主动建议开关以实际设置为准；标准启动不替用户重置已保存的关闭开关。
2. 点击“开始会议”。主题、角色和上传资料可选；本轮提供“本地精修 + 大模型校对”和“仅大模型校对”两种会议级选择。
3. 查看总物理内存、可提交余量与物理可用内存。`available` 表示允许尝试准备，`worker_ready` 才是真正就绪。资源不足时明确显示仅大模型回退；大模型关闭或未配置时不冒充已校对。
4. 本地精修需要准备入口与实际 ready；失败锁存后释放资源再明确准备。仅大模型模式本场不申请、等待或调用离线精修。进入收音后模式锁定，换模式需结束本场再开新会。
5. 选择麦克风、检查权限、确认录音提示并开始。左侧草稿会变化，闭合正文追加保存，未精修正文保留真实来源。右侧 Pi 根据近期有效正文与本次授权资料判断是否建议，可能保持安静，不是每段必须出卡。
6. 结束后查看“会议文字”、录音、校对状态与会后整理。失败保留原文，不等同人工确认。需要时编辑最终文字、保存本地版本或导出；资料项目/目录、来源跳转和历史建议按[完整手册](realtime-participation-user-manual-20261008.md)操作。

网页主要通过麦克风收音；外放能否被收进去取决于设备与环境，戴耳机时不能保证转写对方声音。本轮没有新增浏览器自动抓取任意应用系统音频或可靠双音轨采集，客户端原生接口不算纯网页已具备。多人归属暂不作为实现/验收要求。

## 7. 数据位置与更新源码

| 内容 | 默认位置 |
| --- | --- |
| 推理模型 | `data/local_runtime/web-models/` |
| 会议、音频、资料、设置 | `data/local_runtime/web-quality/` 下各业务目录及 SQLite |
| 受管进程登记与日志 | 同一数据目录的 `server.pid`、`server.pid.runtime-identity.json`、`server.log` |
| 本地环境配置 | 仓库根 `.env` |
| 前端与 Pi 依赖 | 前端 `dist/`、各自 `node_modules/`，可重新安装/构建 |

关闭浏览器不会自动停止后端，服务停止不应删除数据。新机器使用自己的数据和 Key；迁移已有会议应停服后按完整迁移/校验流程处理，不共享正在写的 SQLite。

更新同一工作区时，先结束会议并停止该受管服务，确保没有未提交的本地代码冲突，再执行：

```powershell
git pull --ff-only
uv sync --project code/web_mvp/backend --python 3.13 --frozen
uv pip install --python code/asr_runtime/.venv-funasr/Scripts/python.exe -r code/asr_runtime/requirements-funasr.lock
npm --prefix code/agent_runtime/pi_coach_bridge ci
npm --prefix code/web_mvp/frontend_v2 ci
npm --prefix code/web_mvp/frontend_v2 run build
uv run --project code/web_mvp/backend python tools/web_quality.py doctor
uv run --project code/web_mvp/backend python tools/web_quality.py start
```

自定义实例给最后两个命令补回相同 `--model-root / --data-dir / --port`。版本更新了模型清单时先运行 `models`。不要在服务运行时更新源码和前端构建，否则来源指纹检查会报告旧进程与当前文件不一致。

## 8. 常见问题与定位

| 现象 | 核对/处理 |
| --- | --- |
| `uv` / `node` / `git` 找不到 | 安装后重开 PowerShell，用 `--version` 检查 PATH；无需启用虚拟环境脚本 |
| 依赖安装失败或 doctor 失败 | 核对失败包/锁定版本、Python 与 wheel；按具体错误修复，不删除精修层绕过检查 |
| 模型 `missing` / `sha256_mismatch` | 用同一 `--model-root` 运行 `models`；下载网络与哈希问题分别排查，不跳过校验 |
| `blocked_port_in_use` | 端口上已有未通过归属检查的服务；换空闲端口与独立数据目录，或用原登记命令停止，不杀未知进程 |
| `blocked_start_failed` | 查看返回的 `log_file` 末尾；核对构建、依赖、在线启动与资源，不把失败当作已启动 |
| 页面打不开 / 仍旧版 | 核对 `status` 的端口、入口、身份，完成构建并受控重启后刷新 |
| 精修未就绪但文件检查通过 | 看 `/providers/asr/runtime` 的 `offline_refinement.worker.process_ready`；容量不足保留未精修正文并明确选择实际模式 |
| 32 GB 机器仍容量不足 | 区分总 RAM、物理可用与提交余量；释放其他应用再检查，不降低 guard、不自动改分页配置 |
| 校对等待、失败或待核对 | 核对本场正式段、L2 开关/配置/任务；`no_change` 不是准确率保证，切模式不能补算历史校对 |
| AI 配置与 `.env` 不同 | 查看页面已保存的 Provider 配置，重启会恢复该选择，启动不重置关闭开关 |
| 麦克风拒绝或无声 | 检查浏览器对本机地址的设备权限与所选设备；有草稿不等于完整录音已保存 |
| `blocked_foreign_process` / `blocked_stop_timeout` | 归属未确认或关停未完成；保留记录排查，不删 PID 文件接管 |

默认实例读日志示例；自定义实例改成其数据目录：

```powershell
Get-Content -LiteralPath "data/local_runtime/web-quality/server.log" -Tail 80
```

日志用于本机诊断，对外仅分享错误代码和必要非敏感字段，不上传 `.env`、Key、会议或录音。

确实需要后端代理时，本地 `.env` 设置 `LLM_GATEWAY_PROXY_URL=http://127.0.0.1:<代理端口>` 并重启对应实例。后端未配时直连，本地 `127.0.0.1/localhost/::1` Provider 绕过代理。Pi 使用 Node 网络栈；Node 24 或 Node 22.21+ 可用 `NODE_USE_ENV_PROXY=1` 配合 `HTTP_PROXY/HTTPS_PROXY`，同时设 `NO_PROXY=127.0.0.1,localhost,::1`，避免代理本机 API。无需代理时保持留空。

## 9. 本轮验证范围与交付边界

本轮仅交付网页版源码与文档。入口脚本、语音 worker、模型 manifest、热词和依赖锁文件均受版本控制；公开运行流程不依赖开发机便利脚本、私人会议库或仅在开发机存在的证据文件。

2026-10-11 本轮入口检查：两脚本 `--help` 通过；`tests/test_web_quality.py`、`tests/test_workbench_server_tool.py`、`tests/test_workbench_server_runtime_identity.py` 共 **44 passed / 1.04 秒**。覆盖模型哈希失败、配置继承、受管身份、端口拒绝及不停止无归属进程等合同；这组检查没有新开语音模型、麦克风、付费模型或操作现役服务。本轮没有重做全新 Windows 安装或真实模型下载，不把静态入口和既有依赖检查称作新机全流程通过。

现有运行实例的会前模式、资源界面与被动检查已通过，在线 ASR 已就绪。一次明确执行的本地准备触发资源保护，离线精修未就绪，也没有完成精修推理；不能将会前界面通过称作完整双层精修通过。真实收音准确率、完整本地精修、教练持续价值、整场性能和用户最终验收仍开放。32 GB 参考值、一次短 PCM 峰值及历史失败不替代这些验收。

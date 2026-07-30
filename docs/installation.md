# 安装指南

## 运行环境

- Windows 10/11 x64
- Python 3.11-3.13
- Node.js 22
- [uv](https://docs.astral.sh/uv/)
- Rust stable，仅在开发 Tauri 桌面端时需要

仓库不包含模型文件、会议数据或密钥。完整离线识别能力需要另行导入与当前平台匹配的 `.mcpkg` 能力包。

## 从源码运行

先构建工作台前端：

```powershell
cd code\web_mvp\frontend_v2
npm ci
npm run build
```

再安装并启动本地服务：

```powershell
cd ..\backend
uv sync --frozen --group dev
uv run python ..\..\..\tools\workbench_server.py start
```

浏览器访问 `http://127.0.0.1:8765/workbench`。本地服务只监听回环地址，不应直接暴露到公网。

查看状态或停止服务：

```powershell
uv run python ..\..\..\tools\workbench_server.py status
uv run python ..\..\..\tools\workbench_server.py stop
```

默认开发数据写入 `artifacts/tmp/web_mvp_data/`。也可以通过 `MEETING_COPILOT_DATA_DIR` 指定独立目录。

## 前后端开发模式

终端一：

```powershell
cd code\web_mvp\backend
$env:MEETING_COPILOT_DATA_DIR = "../../../data/local_runtime/web_mvp"
uv run uvicorn meeting_copilot_web_mvp.app:app --host 127.0.0.1 --port 8765 --reload
```

终端二：

```powershell
cd code\web_mvp\frontend_v2
$env:VITE_DEV_API_TARGET = "http://127.0.0.1:8765"
npm run dev
```

打开 Vite 输出的本地地址，通常为 `http://127.0.0.1:5174/workbench`。

## 配置 AI 分析服务

推荐在应用的“设置”中填写服务地址、API Key 和模型，并先执行连接检测。该配置仅用于 OpenAI-compatible 分析能力，本地转写不依赖它。

服务端开发环境也可以使用环境变量：

```powershell
$env:LLM_GATEWAY_BASE_URL = "https://your-provider.example"
$env:LLM_GATEWAY_API_KEY = "your-key"
$env:LLM_GATEWAY_MODEL = "your-model"
$env:LLM_GATEWAY_API_STYLE = "chat_completions"
```

不要提交 `.env`，也不要把密钥放在 `VITE_*` 变量中。所有 `VITE_*` 值都会进入公开前端产物。

## 离线能力包

1. 打开左侧“本地能力”。
2. 选择“导入离线包”。
3. 选择原始 `.mcpkg` 文件，不要预先解压或修改内容。
4. 等待平台、空间、清单和哈希校验完成。
5. 按界面提示激活或重启应用。

能力包体积较大，应单独分发，不应提交到 Git 或放入官网构建产物。

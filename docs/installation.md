# 安装指南

## Windows 安装程序

在 [GitHub Release v0.1.0](https://github.com/luguochang/meeting-copilot/releases/tag/v0.1.0) 下载 `Meeting-Copilot-0.1.0-windows-x64-base-unsigned.exe`。

安装程序 SHA-256：

```text
421e2bbcfb64a490ea1cadf35c96e642da001028aacc244be26d1e0b7e4a9088
```

在 PowerShell 中校验下载文件：

```powershell
Get-FileHash .\Meeting-Copilot-0.1.0-windows-x64-base-unsigned.exe -Algorithm SHA256
```

当前安装程序尚未进行 Authenticode 代码签名，Windows SmartScreen 可能显示提示。请先确认下载来源和哈希，不要使用第三方重新打包的文件。

安装后从开始菜单启动 Meeting Copilot。卸载时打开 Windows“设置 > 应用 > 已安装的应用”，找到 Meeting Copilot 并选择“卸载”。卸载应用不会代替用户的数据备份流程；需要保留的会议内容应先在应用中导出。

该安装包按当前用户安装，不要求管理员权限，也不会注册 Windows 服务、开机启动项或防火墙规则。首次启动时若系统缺少 Microsoft Edge WebView2 Runtime，安装器可能联网下载该系统组件。麦克风权限只在用户开始会议并选择音频输入时由 Windows 请求；导入录音和能力包时只读取用户主动选择的文件。

客户端会启动一个无控制台窗口的本地后台进程。它只监听 `127.0.0.1` 上的随机端口，使用本次启动生成的本地令牌，并在桌面客户端退出时结束。任务管理器中看到随应用启动的打包 `python.exe` 属于本地服务，不是常驻系统服务。

基础客户端包含桌面应用和本地服务，但不包含 ASR 模型。没有离线能力包时，应用仍可启动并使用会议管理、笔记、设置和能力包管理；实时本地转写及录音文件转写不可用。

## 安装目录与数据目录

程序文件保存在安装时选择的目录。会议数据库、录音、日志、设置和能力包属于可变用户数据，不写入安装目录，以免升级或卸载时误删，也避免普通用户目录需要管理员写权限。

新安装默认使用：

```text
%LOCALAPPDATA%\com.meetingcopilot.desktop
```

从旧版本升级时，如果 `%APPDATA%\com.meetingcopilot.desktop` 已存在且新目录尚不存在，客户端会继续使用旧目录，避免历史会议突然不可见。WebView2 自身的浏览器缓存仍由 Windows 保存在 `%LOCALAPPDATA%\com.meetingcopilot.desktop\EBWebView`，不包含能力包。

需要把数据库、录音、日志和能力包整体放到 D/E 盘时，在启动客户端前设置绝对路径。例如：

```powershell
setx MEETING_COPILOT_STORAGE_DIR "D:\MeetingCopilotData"
```

完全退出客户端后重新启动，设置才会生效。不要把新目录放在安装目录内部，也不要在客户端运行时手动移动其中的文件。若要恢复默认路径，可执行 `reg delete HKCU\Environment /v MEETING_COPILOT_STORAGE_DIR /f` 后重新登录 Windows，或在“系统属性 > 环境变量”中删除同名用户变量。

## 源码运行环境

- Windows 10/11 x64
- Python 3.11-3.13
- Node.js 22
- [uv](https://docs.astral.sh/uv/)
- Rust stable，仅在开发 Tauri 桌面端时需要

仓库不包含模型文件、会议数据或密钥。完整离线识别能力需要另行导入来源合法、与当前平台匹配的 `.mcpkg` 能力包。

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

推荐在应用的“AI 设置”中填写服务地址、API Key 和模型，并先执行连接检测。新配置会预填 AI 赞助商地址 `https://codexai.club`，用户可以直接删除或替换；预填本身不会发送请求。该配置仅用于 OpenAI-compatible 分析能力，本地转写不依赖它。

服务端开发环境也可以使用环境变量：

```powershell
$env:LLM_GATEWAY_BASE_URL = "https://your-provider.example"
$env:LLM_GATEWAY_API_KEY = "your-key"
$env:LLM_GATEWAY_MODEL = "your-model"
$env:LLM_GATEWAY_API_STYLE = "chat_completions"
```

不要提交 `.env`，也不要把密钥放在 `VITE_*` 变量中。所有 `VITE_*` 值都会进入公开前端产物。

## 离线能力包

当前完整能力包约 3.05 GiB，解包和激活需要约 9 GB 可用磁盘空间。其模型和二进制组件的公开再分发条件仍在核验，因此本仓库和 GitHub Release 暂不提供下载。取得来源合法的兼容能力包后：

1. 打开左侧“离线能力”。
2. 选择“导入离线包”。
3. 选择原始 `.mcpkg` 文件，不要预先解压或修改内容。
4. 等待平台、空间、清单和哈希校验完成。
5. 按界面提示激活或重启应用。

能力包体积较大，应在许可确认后单独分发，不应提交到 Git 或放入官网构建产物。不要从来源不明的地址下载模型包，也不要绕过客户端的清单和哈希校验。

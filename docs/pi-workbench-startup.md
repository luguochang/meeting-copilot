# Pi 工作台启动手册

本文记录 `feat/pi-realtime-coach-agent-loop` 分支的本地启动方式。命令默认使用独立 Pi worktree，不会修改或复用当前 `main` 工作区的源码。

## 运行前确认

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent
git branch --show-current
git status --short --branch
```

预期当前分支为：

```text
feat/pi-realtime-coach-agent-loop
```

首次运行或前端依赖更新后，先构建工作台：

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent/code/web_mvp/frontend_v2
npm ci
npm run build
```

## 启动后端

在终端一执行。`--provider-mode inherit` 允许当前运行环境使用已配置的 Provider；它不会把密钥写入代码或前端产物。`--data-dir` 使用 Pi worktree 自己的数据目录，避免污染 `main` 的本地会议数据。

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent/code/web_mvp/backend

uv run python ../../../tools/workbench_server.py start \
  --port 8981 \
  --pid-file /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.pid \
  --log-file /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.log \
  --data-dir artifacts/tmp/web_mvp_data \
  --provider-mode inherit \
  --realtime-refiner-policy prewarm \
  --realtime-coach-cutoff-ms 8000
```

这个命令会启动 FastAPI、Pi Node sidecar 的预热入口和本地 ASR 精修运行时。后端启动成功的标志是输出 `"status": "started"`，并且 `health_ok` 为 `true`。

## 启动前端开发服务

如果需要 React/Vite 热更新，在终端二执行：

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent/code/web_mvp/frontend_v2

VITE_DEV_API_TARGET=http://127.0.0.1:8981 \
npm run dev -- --host 127.0.0.1 --port 5174
```

推荐日常验收使用后端托管的工作台：

- [http://127.0.0.1:8981/workbench](http://127.0.0.1:8981/workbench)

前端热更新入口为：

- [http://127.0.0.1:5174/workbench-assets/](http://127.0.0.1:5174/workbench-assets/)

Vite 使用 `/workbench-assets/` 作为 base path，因此不要把 `5174/workbench` 当作开发入口。录音 WebSocket 和 API 会通过 `VITE_DEV_API_TARGET` 代理到 `8981`。

## 配置 Provider

启动后进入工作台的“AI 设置”，填写 OpenAI-compatible Provider 的地址、模型和 API Key，并执行连接检测。配置状态可以用下面的命令查看；输出只包含脱敏状态，不会打印密钥：

```bash
curl -fsS http://127.0.0.1:8981/providers/status
curl -fsS http://127.0.0.1:8981/providers/config
```

必须看到 `configured: true` 后，右侧 Pi 实时教练才会调用远程 Provider。未配置 Provider 时，本地录音和 ASR 仍可测试，但实时建议、纠错和其他 LLM 功能不可用。

## 状态、日志与停止

查看后端状态：

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent/code/web_mvp/backend

uv run python ../../../tools/workbench_server.py status \
  --port 8981 \
  --pid-file /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.pid
```

查看后端日志：

```bash
tail -f /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.log
```

停止后端：

```bash
cd /Users/chase/Documents/面试/meeting-copilot-pi-agent/code/web_mvp/backend

uv run python ../../../tools/workbench_server.py stop \
  --pid-file /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.pid
```

前端 Vite 服务在其终端按 `Ctrl-C` 停止。

## 快速健康检查

```bash
curl -fsS http://127.0.0.1:8981/health
curl -fsS -o /dev/null -w 'workbench=%{http_code}\n' http://127.0.0.1:8981/workbench

lsof -nP -iTCP:8981 -sTCP:LISTEN
lsof -nP -iTCP:5174 -sTCP:LISTEN
```

期望结果：后端 `/health` 返回 `status: ok`，工作台返回 HTTP `200`，并且 `8981`、`5174` 分别由当前 Pi worktree 的进程监听。

## 常见问题

### 8981 已被占用

先检查是否是本 Pi worktree 的受管进程：

```bash
uv run python ../../../tools/workbench_server.py status \
  --port 8981 \
  --pid-file /Users/chase/Documents/面试/meeting-copilot-pi-agent/artifacts/tmp/pi-workbench-server.pid
```

如果显示 `blocked_port_in_use`，不要直接杀掉未知进程。可以停止已确认属于 Pi worktree 的旧服务，或临时改用一个新端口，并同步修改 `VITE_DEV_API_TARGET`。

### 页面能打开但右侧没有 Pi 建议

先检查 `/providers/status` 的 `configured`、`realtime_ready` 和 `probe_status`。Provider 未配置、连接检测失败、实时熔断或 Provider 超时都会使 Pi 保持可解释静默；这不代表麦克风或 ASR 没有运行。

### 左侧文字没有出现

先检查浏览器麦克风权限和页面是否在 `127.0.0.1:8981` 上打开，再查看浏览器控制台以及后端日志。不要把 `main` 工作区的旧服务与 Pi worktree 的服务混用。

## 安全约束

- 不要把 API Key 写进脚本、`.env`、Git 文档或 `VITE_*` 变量。
- 不要提交 `artifacts/`、录音、会议数据库、模型文件或诊断原始产物。
- 仅使用回环地址 `127.0.0.1`，不要把本地工作台暴露到公网。
- 关闭服务前确认 PID 和 runtime identity 属于 Pi worktree，避免误停 `main` 的 macOS 开发服务。

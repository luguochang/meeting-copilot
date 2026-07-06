# Mac 优先 MVP 需求与技术方案

> 日期：2026-06-18  
> 结论：首发平台优先 macOS；默认本地/开源 ASR；远程 ASR 默认关闭；基座优先评估 Meetily fork/复用；Copilot 智能层自研。

## 1. 关键判断

### 1.1 首发平台

首发平台选择：macOS。

原因：

- 当前用户本机是 Mac，能最快验证真实会议场景。
- macOS 上可以优先打通本机麦克风、系统音频、模型运行、实时 UI 和本地存储。
- Windows 系统音频采集、安装包、权限、驱动和音频设备差异较大，放到第二阶段更稳。

MVP 平台范围：

- macOS Apple Silicon 优先。
- Intel Mac 作为兼容性观察项。
- Windows 作为第二阶段，不进入第一轮实现。
- Linux 暂不进入产品 MVP。

### 1.2 基座选择

推荐判断：优先评估 Meetily 作为基座候选，但不直接继承其 AI 能力结论。

可复用方向：

- 桌面会议助手形态。
- 本地录音。
- 系统音频捕获。
- 实时转写 UI。
- 本地会议记录。
- 会后总结流程。
- AI provider 配置思路。

需要自研或替换方向：

- 中文技术会议 ASR 选型。
- 技术词、接口名、字段名、中英混排处理。
- partial/final/revision 转写稳定器。
- 会议状态机。
- 缺口雷达。
- 建议卡片。
- LLM 结构化抽取与证据链。
- ASR/LLM 评测体系。

基座策略：

```text
先调研/跑通 Meetily
  -> 如果架构清晰、许可证适合、Mac 采集可用，则 fork 做 MVP
  -> 如果架构不适合深改，则只借鉴其实现，另起轻量桌面壳
```

不要做：

- 不从零做 ASR 模型。
- 不把 Meetily 的默认转写能力直接视为中文技术会议达标。
- 不默认启用远程 ASR。

### 1.3 ASR 路线

默认路线：

```text
sherpa-onnx streaming 本地 ASR 做 Mac 端侧首测
  -> FunASR streaming / Paraformer 做中文质量优先验证
  -> 若 sherpa 性能可行但技术词不足，则引入热词、归一化和 second-pass 修正
  -> SenseVoice 作为中英混合/多语种实验候选
```

远程 ASR：

- 仅作为 bake-off 对照。
- 仅在用户显式配置时启用。
- 不进入默认成本结构。

当前本机判断：

- sherpa-onnx 已证明 Mac 本地性能可行：352.6 秒音频约 4.1 秒完成，RTF 约 0.0115。
- 合成中文技术会议短样本显示技术词仍有质量风险：英文服务名、灰度、P99 等识别不稳。
- 因此 MVP 不能只凭 sherpa-onnx 宣称中文技术会议 ASR 达标。
- 下一轮应优先验证 FunASR streaming 和热词/术语表，而不是先扩展桌面端大功能。

### 1.4 LLM 路线

默认路线：

- OpenAI-compatible 中转站。
- 支持自定义 endpoint、model、api key。
- 结构化 JSON 输出。
- 记录 prompt version、model、延迟、失败原因。

LLM 不直接处理原始音频，默认只处理稳定 transcript 和会议状态。

### 1.5 依赖和环境管理

目标：

- 不让桌面主进程被 ASR/LLM/模型依赖拖垮。
- 不让 Python 依赖污染客户端环境。
- 不让模型占用不可控内存。
- 可复现安装、可观测资源、可清理缓存。

原则：

- 桌面端、ASR worker、LLM gateway 分进程。
- 模型文件单独管理，有版本、大小、hash。
- Python ASR 环境独立，不塞进桌面主进程。
- 依赖锁定，避免隐式升级。
- 所有长驻进程有内存上限、超时、健康检查。
- 会议结束释放 ASR session 和音频 buffer。

推荐结构：

```text
desktop app
  -> audio capture service
  -> local asr worker process
  -> transcript stabilizer
  -> llm gateway worker
  -> local storage
```

## 2. MVP 可实现功能清单

### 2.1 会前功能

必须实现：

- 创建本地会议会话。
- 选择麦克风输入。
- 采集系统音频。
- 检测音频电平。
- 检测静音、设备不可用、权限缺失。
- 选择项目术语表。
- 显示本场 ASR provider：默认本地。
- 显示本场 LLM provider：用户配置的中转站。
- 显示是否保存音频。
- 显示录音告知文案。

第一版可以不做：

- 自动读取日历。
- 自动加入会议。
- 自动识别会议软件。
- 自动同步参会人列表。

### 2.2 会中功能

必须实现：

- 开始/暂停/恢复/停止录音。
- 实时展示 transcript。
- 区分 partial 和 final。
- 记录 ASR revision。
- 展示本地 ASR 状态：运行中、延迟高、失败、重启中。
- 术语高亮：字段名、服务名、接口、指标、错误码。
- 低频建议卡片。
- 建议卡片保留、忽略、复制追问、标记错误。
- 会议状态面板：议题、候选决策、行动项、风险、未闭环问题。
- 音频质量提示。
- 工程语境门禁：非工程会议不显示工程缺口卡片。
- 状态事件日志：显示关键状态 diff。

第一版可以降级：

- 不做完整多人 speaker diarization。
- 只区分本机麦克风和系统音频来源。
- owner 不明确时显示待确认。
- 卡片先以侧边栏/队列展示，不做强弹窗。
- transcript 作为可展开证据面板，不作为主界面中心。

### 2.3 会后功能

必须实现：

- 保存本地音频。
- 保存 raw transcript。
- 保存 normalized transcript。
- 保存建议卡片和用户操作。
- 生成结构化会议纪要。
- 导出 Markdown。
- 导出 JSON。
- 删除会议数据。

第一版可以不做：

- 自动创建 Jira/Linear/GitHub issue。
- 自动生成完整 ADR/RFC。
- 多会议趋势分析。
- 企业权限系统。
- 通用会议问答。
- 全量知识库检索。
- 自动周报。
- 跨会议长期记忆。
- 完整协作空间。

### 2.4 设置功能

必须实现：

- ASR provider 设置。
- 本地 ASR 模型路径。
- LLM endpoint/model/api key 设置。
- 是否保存音频。
- 数据目录位置。
- 缓存清理。
- 日志级别。

推荐实现：

- 模型下载不要自动偷偷执行，用户明确确认。
- 显示模型大小和磁盘占用。
- 显示当前 worker 内存和运行状态。

## 3. 基座功能清单

如果使用 Meetily 作为基座，优先验证以下能力。

### 3.1 可直接复用或参考

- macOS 桌面应用启动。
- 会议会话创建。
- 本地音频录制。
- 系统音频捕获。
- 实时转写展示区域。
- 会议历史列表。
- 会后摘要页面。
- 本地数据保存。
- AI provider 配置入口。

### 3.2 需要替换或重写

- ASR provider 层。
- 中文技术词识别和热词。
- 转写稳定器。
- 实时建议卡片。
- 会议状态机。
- 证据链存储。
- LLM prompt 和 JSON schema。
- 质量评测和回归测试。

### 3.3 必须评估的风险

- 许可证是否允许商业二次开发。
- macOS 系统音频捕获是否稳定。
- 是否依赖虚拟声卡或第三方驱动。
- 代码结构是否适合深度改造。
- ASR 进程是否与 UI 强耦合。
- 模型和依赖是否容易打包。
- 内存是否可控。

## 4. 技术架构

### 4.1 运行时架构

```text
macOS Desktop App
  Audio Capture
    - mic track
    - system audio track
    - mixed track
  Local ASR Worker
    - FunASR streaming
    - sherpa-onnx fallback
  Transcript Stabilizer
    - partial
    - final
    - revision
  Technical Language Layer
    - term dictionary
    - normalization
    - entity highlight
  Meeting State Engine
    - topics
    - decision candidates
    - action items
    - risks
    - open questions
    - state events
  LLM Gateway
    - OpenAI-compatible
    - structured JSON
    - timeout/retry
  UI
    - state-first meeting panel
    - suggestion cards
    - evidence transcript panel
    - meeting state
    - quality status
  Local Storage
    - audio
    - transcript
    - states
    - summaries
    - logs
```

### 4.2 进程边界

主进程：

- UI。
- 会话状态。
- 用户操作。

音频进程或模块：

- 采集音频。
- 写入本地 buffer。
- 推送 chunk 给 ASR worker。

ASR worker：

- 加载本地模型。
- 处理流式音频。
- 输出 partial/final/revision。
- 监控内存和延迟。

LLM worker：

- 接收稳定 transcript。
- 调用中转站。
- 校验 JSON。
- 返回结构化状态和建议。

### 4.3 数据流

```text
audio chunk
  -> asr partial
  -> live transcript preview

asr final
  -> transcript segment
  -> term normalization
  -> meeting state update
  -> LLM extraction
  -> suggestion candidate
  -> evidence check
  -> suggestion card

meeting stopped
  -> post-meeting LLM summary
  -> markdown/json export
```

## 5. 依赖管理规范

### 5.1 桌面端依赖

要求：

- 使用锁文件。
- 禁止无版本范围的核心依赖。
- 禁止把大型模型文件放进源码目录。
- 构建产物和缓存放入 ignored 目录。

如果基座是 Electron / Node：

- 使用 `package-lock.json`、`pnpm-lock.yaml` 或等价锁文件。
- Node 版本固定。
- 构建缓存不提交。

如果基座是 Tauri：

- Rust toolchain 固定。
- npm/pnpm 依赖锁定。
- sidecar worker 独立打包。

### 5.2 Python / ASR 依赖

要求：

- ASR worker 独立虚拟环境。
- 使用 `requirements.lock`、`uv.lock` 或 conda env lock。
- 模型下载目录独立，例如 `~/Library/Application Support/MeetingCopilot/models`。
- 禁止在运行时隐式升级模型或依赖。
- 每个模型记录 name、version、size、hash。

### 5.3 资源控制

必须实现：

- ASR worker 启动/停止。
- ASR worker 健康检查。
- 单场会议结束释放模型 session。
- 音频 chunk buffer 上限。
- LLM 请求超时。
- 日志滚动。
- 临时文件清理。

建议指标：

- ASR worker 内存。
- ASR chunk queue 长度。
- partial latency。
- final latency。
- LLM latency。
- provider error count。

## 6. 硬件要求初判

MVP 优先目标：

- Apple Silicon Mac。
- 16GB 内存体验更稳。
- CPU-only 应作为目标，但实时性必须实测。

需要通过 bake-off 确认：

- FunASR streaming CPU-only 是否能低延迟运行。
- 模型加载后内存占用。
- 长会议 30-60 分钟是否内存稳定。
- 系统音频采集和 ASR 同时运行是否影响会议软件。

不提前承诺：

- 所有 Intel Mac 都流畅。
- 8GB 内存 Mac 长会议稳定。
- 不同会议软件都能无差别采集系统音频。

## 7. 验收标准

### 7.1 本地实时转写验收

- 能在 Mac 上启动会议。
- 能采集 mic 和系统音频。
- 能实时显示中文文字。
- final 延迟 P95 <= 3.5s。
- ASR worker 长时间运行不崩溃。
- 停止会议后释放资源。

### 7.2 Copilot 验收

- 能识别候选决策、行动项、风险、未闭环问题。
- 能生成低频建议卡片。
- 每张建议卡片有证据片段。
- 能输出 Markdown/JSON 纪要。
- 低置信度时能沉默或标记待确认。

### 7.3 成本验收

- 默认 ASR 不产生远程 API 费用。
- 远程 ASR 默认关闭。
- LLM 中转站费用可解释。
- 任何付费 provider 都需要用户显式配置。

## 8. 下一步执行顺序

推荐顺序：

1. 拉取并本地运行 Meetily，验证 Mac 录音、系统音频、实时转写和依赖结构。
2. 做基座评估表：许可证、架构、可改造性、音频能力、内存、打包。
3. 用现有 ASR bake-off 的 `command` provider 接 FunASR。
4. 准备真实脱敏中文技术会议短音频。
5. 先跑 file/伪实时 ASR，再跑 streaming ASR。
6. 本地实时文字达标后，再接 LLM 中转站做会议状态和建议卡片。
7. 若 Meetily 不适合深改，再决定自建 Mac 桌面壳。

## 9. 当前未决但我给出的默认答案

| 问题 | 默认判断 |
|---|---|
| 首发平台 | macOS |
| 是否双平台首发 | 否，Windows 第二阶段 |
| 是否 fork Meetily | 先评估，适合则 fork |
| 是否自己写 ASR | 否 |
| 默认 ASR | 本地 FunASR streaming |
| ASR 备选 | sherpa-onnx |
| 是否默认远程 ASR | 否 |
| 是否允许远程 ASR | 允许，但必须显式配置 |
| LLM | OpenAI-compatible 中转站 |
| 是否保存音频 | 默认本地保存，可关闭 |
| 是否完整 speaker diarization | MVP 不做 |
| owner 推断 | 不明确则待确认 |
| 依赖管理 | 分进程、锁版本、模型独立目录 |

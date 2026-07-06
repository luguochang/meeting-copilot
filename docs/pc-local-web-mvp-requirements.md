# PC Local Web MVP 需求文档

> 日期：2026-06-30  
> 阶段：PC-1，本地 Web MVP 垂直切片  
> 结论：先在本机验证 Copilot 核心价值链路，再进入 Mac 桌面壳和 Windows 适配。

## 1. 定位

PC Local Web MVP 是一个运行在本机的 PC 原型，用来验证：

```text
ASR streaming/replay events
  -> stable transcript
  -> EvidenceSpan
  -> meeting state
  -> low-frequency suggestion cards
  -> evidence-backed report
```

它不是云端 SaaS，不是最终安装包，也不是普通录音转文字工具。

## 2. 范围

必须实现：

- 本地启动 Web UI/API。
- 用 mock/replayed streaming events 或现有 ASR provider JSON 创建会议会话。
- 展示 raw transcript、normalized transcript、segments、EvidenceSpan。
- 展示会议状态：DecisionCandidate、ActionItem、Risk、OpenQuestion。
- 展示实时建议卡片，且每张卡片必须引用 EvidenceSpan。
- 展示状态事件和 LLM 调度/用量摘要。
- 导出 JSON/Markdown 报告。
- 删除本地会话数据。

第一版不做：

- 不做真实 Mac/Windows 安装包。
- 不做移动端。
- 不做多用户账号和云同步。
- 不做完整 speaker diarization。
- 不做日历读取、自动入会、自动 Jira/Linear/GitHub issue。
- 不默认启用远程 ASR。

## 3. 功能需求

### PCWEB-001 本地运行

系统必须能在开发者本机启动，不依赖公网服务才能打开 UI。默认远程调用只允许 LLM 中转站，且必须通过 scheduler 控制频率。

验收：

- 本地 API 有健康检查。
- 不需要上传原始音频到产品后端。
- API key 只允许读取 `configs/local/`，不得写入公开文档或输出。

### PCWEB-002 会话快照

系统必须把 transcript report、LLM analysis、state events 和 evaluation 聚合成一个可展示的会议快照。

验收：

- 快照包含 transcript、states、suggestion_cards、state_events、quality。
- transcript 必须包含 raw text 和 normalized text。
- quality 必须包含 ASR provider、latency、RTF、状态数量、建议卡片数量。

### PCWEB-003 EvidenceSpan 强约束

正式状态、建议卡片和报告条目必须引用 EvidenceSpan。

验收：

- 有证据的卡片进入 `suggestion_cards`。
- 缺证据的卡片被拒绝或降级为草稿，不能作为正式建议。
- UI/API 返回时保留 evidence_span_ids，便于点击回到原文片段。

### PCWEB-004 工程语境门禁

非工程会议不得输出工程建议卡片。

验收：

- `meeting_context.is_engineering_meeting=false` 时，`suggestion_cards` 必须为空。
- 非工程会议仍可保留 transcript 和普通 summary。

### PCWEB-005 会中建议卡片

建议卡片必须低频、可操作、可追溯。

验收：

- 卡片状态至少支持 `new`、`kept`、`dismissed`、`marked_wrong`。
- 每张卡片必须有 `type`、`suggested_question` 或等价操作文本。
- 不允许 partial transcript 直接触发强建议。

### PCWEB-006 会后报告

系统必须能生成会后报告，报告不能脱离证据。

验收：

- 支持 JSON 导出。
- 支持 Markdown 导出。
- 正式行动项、风险、未闭环问题必须包含 evidence_span_ids。

### PCWEB-007 本地数据和删除

会议数据默认保存在本地，且用户能删除。

验收：

- 删除会话会删除快照和导出物。
- 第一版可使用 JSON 文件或内存存储；进入桌面壳前必须切换到明确的数据目录策略。

### PCWEB-008 平台无关 core

PC-1 的 core 不得依赖 Tauri、Electron、macOS 或 Windows API。

验收：

- core 可被 Web MVP、后续 Mac desktop shell、Windows adapter 复用。
- platform adapter 只能出现在后续桌面阶段。

## 4. 成功标准

PC-1 成功必须同时满足：

- 至少 2 个中文技术会议样本跑通。
- 工程会议样本能产生 DecisionCandidate、ActionItem、Risk、OpenQuestion。
- 至少 1 张建议卡片在 EvidenceSpan 上可追溯。
- 非工程会议样本工程建议卡片为 0。
- 会后 JSON/Markdown 报告可导出。
- 测试命令可复现。

如果只实现实时文字和会后总结，PC-1 判定失败。

